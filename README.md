# Estimador CAG

API REST que genera estimaciones de proyectos de software a partir de transcripciones de reuniones con clientes. Utiliza **CAG** (*Context-Augmented Generation*): ejemplos de estimaciones previas se inyectan en el prompt del sistema para guiar formato, nivel de detalle y criterios de estimación.

## Características

- Endpoint único `POST /api/v1/estimate` que recibe una transcripción y devuelve una estimación estructurada en Markdown.
- Soporte para **OpenAI** y **Anthropic** como proveedores de LLM.
- Contexto fijo con ejemplos reales (inventario, app móvil, portal B2B, microservicios, etc.) en `app/context/examples.py`.
- Documentación interactiva con Swagger UI en `/docs`.

## Requisitos

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recomendado) o pip
- API key de OpenAI y/o Anthropic según el proveedor elegido

## Instalación

```bash
git clone <url-del-repositorio>
cd estimador-cag

# Crear entorno e instalar dependencias
uv sync
```

Si no usas `uv`:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

## Configuración

Copia el archivo de ejemplo y completa las variables:

```bash
cp .env.example .env
```

| Variable | Descripción |
|----------|-------------|
| `OPENAI_API_KEY` | API key de OpenAI (requerida si `LLM_PROVIDER=openai`) |
| `ANTHROPIC_API_KEY` | API key de Anthropic (requerida si `LLM_PROVIDER=anthropic`) |
| `LLM_PROVIDER` | `openai` o `anthropic` (por defecto: `openai`) |
| `LLM_MODEL` | Modelo explícito; si está vacío se usa el predeterminado del proveedor |
| `APP_ENV` | Entorno de ejecución (opcional) |
| `LOG_LEVEL` | Nivel de logging (opcional) |

**Modelos por defecto** (cuando `LLM_MODEL` está vacío):

| Proveedor | Modelo |
|-----------|--------|
| `openai` | `gpt-o4-mini` |
| `anthropic` | `claude-haiku-4-5` |

## Ejecución

```bash
uv run uvicorn app.main:app --reload
```

La API queda disponible en `http://127.0.0.1:8000`. Si el puerto 8000 está bloqueado, prueba con otro:

```bash
uv run uvicorn app.main:app --reload --port 8080
```

## Uso de la API

### Health check

```bash
curl http://127.0.0.1:8000/health
```

Respuesta:

```json
{"status": "ok"}
```

### Generar estimación

```bash
curl -X POST http://127.0.0.1:8000/api/v1/estimate \
  -H "Content-Type: application/json" \
  -d "{\"transcription\": \"El cliente necesita un dashboard interno para visualizar KPIs de ventas en tiempo real, con login SSO y exportación a PDF.\"}"
```

**Request** (`EstimateRequest`):

```json
{
  "transcription": "Texto de la reunión con el cliente..."
}
```

**Response** (`EstimateResponse`):

```json
{
  "estimation": "## Estimación: ...\n\n### Desglose de tareas:\n...",
  "model": "gpt-o4-mini",
  "provider": "openai",
  "usedTokens": 4521,
  "timestamp": 1717171200
}
```

También puedes probar el endpoint desde la documentación interactiva en [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## Cómo funciona (CAG)

```
┌─────────────────┐      ┌──────────────────────────────────┐
│  Transcripción  │────> │  LLM (OpenAI / Anthropic)        │
│  (user message) │      │                                  │
└─────────────────┘      │  system: instrucciones +         │
                         │          ejemplos previos (CAG)  │
                         └──────────────────────────────────┘
                                        │
                                        v
                              Estimación en Markdown
```

1. El **system prompt** incluye instrucciones de rol y los ejemplos de `app/context/examples.py` (resumen de reunión + estimación generada).
2. La **transcripción** del cliente se envía como mensaje de usuario.
3. El modelo devuelve una estimación con desglose de tareas, horas, equipo recomendado y duración.

Para ajustar el comportamiento del estimador, edita o amplía los ejemplos en `app/context/examples.py`.

## Estructura del proyecto

```
estimador-cag/
├── app/
│   ├── main.py              # Aplicación FastAPI
│   ├── config.py            # Settings desde .env
│   ├── context/
│   │   └── examples.py      # Ejemplos CAG
│   ├── routers/
│   │   └── estimations.py   # POST /estimate
│   ├── schemas/
│   │   └── estimation.py    # Modelos Pydantic
│   └── services/
│       └── llm_service.py   # Integración OpenAI / Anthropic
├── .env.example
├── pyproject.toml
└── README.md
```

## Códigos de error

| Código | Causa |
|--------|--------|
| `400` | Proveedor LLM no válido u otro error de validación |
| `502` | Fallo al llamar al proveedor (API key, red, modelo, etc.) |

## Stack

- [FastAPI](https://fastapi.tiangolo.com/)
- [Uvicorn](https://www.uvicorn.org/)
- [OpenAI Python SDK](https://github.com/openai/openai-python)
- [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python)
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)

## Licencia

Consulta el repositorio para información de licencia.
