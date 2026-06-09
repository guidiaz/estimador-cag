"""Envoltura fina de Redis para cachear estimaciones.

Diseñada para **degradar con elegancia**: si Redis no está disponible (caído,
URL incorrecta, timeout) las operaciones se comportan como un miss / no-op y la
petición sigue su curso normal. El cache nunca debe tumbar un endpoint.

Se desactiva por completo con `CACHE_ENABLED=false`.
"""

import hashlib
import json
import logging
import time

import redis

from app.config import settings

logger = logging.getLogger(__name__)

# Timeouts cortos: con Redis caído no queremos que cada petición pague el
# timeout TCP por defecto (segundos) antes de caer al modo sin-cache.
_CONNECT_TIMEOUT = 0.3

# Circuit breaker: tras un fallo de Redis, saltamos el cache durante este tiempo
# para no pagar el timeout de conexión en cada petición mientras siga caído.
_COOLDOWN_SECONDS = 30.0

_client: redis.Redis | None = None
_unavailable_logged = False
_unavailable_until = 0.0


def _get_client() -> redis.Redis | None:
    """Cliente Redis perezoso.

    Devuelve None si el cache está desactivado o si el circuit breaker está
    abierto (Redis marcado como no disponible recientemente).
    """
    global _client
    if not settings.cache_enabled:
        return None
    if time.monotonic() < _unavailable_until:
        return None
    if _client is None:
        _client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=_CONNECT_TIMEOUT,
            socket_timeout=_CONNECT_TIMEOUT,
            decode_responses=True,
        )
    return _client


def _on_error(exc: Exception) -> None:
    """Abre el circuit breaker y loguea la indisponibilidad (una vez por ventana)."""
    global _unavailable_logged, _unavailable_until
    _unavailable_until = time.monotonic() + _COOLDOWN_SECONDS
    if not _unavailable_logged:
        logger.warning(
            "Cache Redis no disponible, se continúa sin cache durante %ss: %s",
            int(_COOLDOWN_SECONDS),
            exc,
        )
        _unavailable_logged = True


def build_key(namespace: str, payload: dict) -> str:
    """Clave determinista: `<namespace>:<sha256 del payload canónico>`."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"


def get_json(key: str) -> dict | None:
    """Devuelve el valor cacheado (dict) o None en miss / cache no disponible."""
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
    except (redis.RedisError, OSError) as exc:
        _on_error(exc)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def set_json(key: str, value: dict, ttl: int) -> None:
    """Guarda `value` (dict) con expiración `ttl` segundos. No-op si falla Redis."""
    client = _get_client()
    if client is None:
        return
    try:
        client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
    except (redis.RedisError, OSError) as exc:
        _on_error(exc)
