from fastapi import FastAPI

from app.logging_config import configure_logging
from app.middleware import RequestContextMiddleware
from app.routers import estimations

configure_logging()

app = FastAPI(
    title="Estimador CAG",
    description=(
        "API para generar estimaciones de proyectos de software a partir de "
        "transcripciones de reuniones, utilizando ejemplos previos como contexto (CAG)."
    ),
)

app.add_middleware(RequestContextMiddleware)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(estimations.router, prefix="/api/v1")
