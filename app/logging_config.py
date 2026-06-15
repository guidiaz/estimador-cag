"""Configuración de observabilidad básica con structlog.

Unifica el logging de la aplicación, de las librerías que usan `logging` de la
stdlib (p. ej. `app/services/cache.py`) y de litellm en una sola tubería de
procesadores. Renderiza en consola legible cuando hay un TTY adjunto y en JSON
en caso contrario (contenedores, ficheros, agregadores de logs).

Llamar a `configure_logging()` una vez al arrancar la app.
"""

import logging
import sys

import structlog

from app.config import settings

# Procesadores compartidos por structlog y por los logs de la stdlib, de modo que
# ambos orígenes salgan con el mismo formato y los mismos campos de contexto.
_SHARED_PROCESSORS: list = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.StackInfoRenderer(),
]


def _renderer() -> object:
    """Consola legible si hay TTY; JSON en cualquier otro caso."""
    if settings.log_json or not sys.stderr.isatty():
        return structlog.processors.JSONRenderer()
    return structlog.dev.ConsoleRenderer()


def configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            # Prepara el evento para que lo renderice el ProcessorFormatter de la
            # stdlib (al que también enviamos los logs de librerías).
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _renderer(),
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # litellm es muy ruidoso por stdlib; lo subimos a WARNING para que la
    # observabilidad no se ahogue en logs de la librería.
    logging.getLogger("litellm").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
