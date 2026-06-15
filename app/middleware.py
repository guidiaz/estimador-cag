"""Middleware ASGI pura para observabilidad de peticiones.

Se implementa a nivel ASGI (no `BaseHTTPMiddleware`) a propósito:

- `BaseHTTPMiddleware` ejecuta la app en una subtarea que copia el contexto, por
  lo que los `contextvars` enlazados aquí no siempre llegan a los logs de las
  capas internas. A nivel ASGI no hay frontera de tarea: el `request_id`
  enlazado se ve en todo el procesamiento de la petición.
- También tiene un historial de bufferizar `StreamingResponse`; a nivel ASGI el
  stream NDJSON fluye intacto.

Genera/propaga un `request_id` y emite una línea de acceso (`http.request`) con
método, ruta, status y latencia. Las métricas de tokens NO se loguean aquí: en
streaming el cuerpo se consume después de que el status ya se ha enviado, así que
ese evento se emite desde la capa de servicio al agotar el generador.
"""

import time
import uuid

import structlog

logger = structlog.get_logger("app.access")

_REQUEST_ID_HEADER = b"x-request-id"


class RequestContextMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(_REQUEST_ID_HEADER)
        request_id = incoming.decode() if incoming else uuid.uuid4().hex

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        method = scope.get("method", "")
        path = scope.get("path", "")
        start = time.monotonic()
        status_code = {"value": 500}

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                status_code["value"] = message["status"]
                # Devuelve el request_id al cliente para correlación.
                message.setdefault("headers", [])
                message["headers"].append(
                    (_REQUEST_ID_HEADER, request_id.encode())
                )
            await send(message)

        logger.info("http.request.started", method=method, path=path)
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000, 2)
            logger.exception(
                "http.request.failed",
                method=method,
                path=path,
                duration_ms=duration_ms,
            )
            raise

        duration_ms = round((time.monotonic() - start) * 1000, 2)
        logger.info(
            "http.request",
            method=method,
            path=path,
            status=status_code["value"],
            duration_ms=duration_ms,
        )
