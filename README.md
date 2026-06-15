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
- Redis (opcional, para cachear estimaciones). Ej.: `docker run -p 6379:6379 redis`. Si no hay Redis, la app funciona igual sin cache.

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
| `ANTHROPIC_API_KEY` | API key de Anthropic (proveedor por defecto del router) |
| `OPENAI_API_KEY` | API key de OpenAI (proveedor de fallback del router) |
| `PRIMARY_MODEL` | Modelo por defecto, formato litellm (por defecto `anthropic/claude-haiku-4-5`) |
| `FALLBACK_MODEL` | Modelo de fallback, formato litellm (por defecto `openai/gpt-4o-mini`) |
| `APP_ENV` | Entorno de ejecución (opcional) |
| `LOG_LEVEL` | Nivel de logging (opcional) |
| `REDIS_URL` | URL de Redis para el cache (por defecto `redis://localhost:6379/0`) |
| `CACHE_ENABLED` | Activa/desactiva el cache de estimaciones (por defecto `true`) |
| `CACHE_TTL_SECONDS` | Expiración de las entradas de cache en segundos (por defecto `86400`) |

> Los antiguos `LLM_PROVIDER` / `LLM_MODEL` ya no gobiernan la elección de proveedor: ahora lo hace el **router LiteLLM** (ver más abajo).

## Ejecución

### API

```bash
uv run uvicorn app.main:app --reload
```

La API queda disponible en `http://127.0.0.1:8000`. Si el puerto 8000 está bloqueado, prueba con otro:

```bash
uv run uvicorn app.main:app --reload --port 8080
```

### Interfaz Streamlit (opcional)

`streamlit_app.py` es un cliente HTTP de la API: una interfaz de chat con respuesta en streaming. Es un **proceso aparte**; arranca primero la API y luego, en otra terminal:

```bash
uv run streamlit run streamlit_app.py
```

La UI queda en `http://localhost:8501` y localiza la API mediante la variable `ESTIMADOR_API_URL` (por defecto `http://127.0.0.1:8000`). Si la API corre en otro host o puerto:

```bash
ESTIMADOR_API_URL=http://127.0.0.1:8080 uv run streamlit run streamlit_app.py
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
  "model": "gpt-4o-mini",
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

## Proveedores y fallback (LiteLLM Router)

Las llamadas a `/api/v1/estimate` y `/api/v1/estimate/stream` pasan por un **router de [LiteLLM](https://docs.litellm.ai/docs/routing)**:

- **Proveedor por defecto:** Anthropic (`PRIMARY_MODEL`).
- **Fallback:** OpenAI (`FALLBACK_MODEL`), que se usa **solo después de que falle un reintento de conexión** al proveedor por defecto. Es decir: se intenta Anthropic → ante un error transitorio (incluida la conexión) se reintenta una vez → si sigue fallando, se sirve con OpenAI.
- El campo `provider`/`model` de la respuesta (y el registro `done` del stream) refleja el proveedor que **realmente** atendió la petición.

**Limitaciones:**
- En streaming, el fallback solo está garantizado si el fallo ocurre **antes** del primer token; un fallo a mitad de la generación no se recupera.
- Un acierto de cache puede reportar el proveedor de fallback aunque el primario ya esté disponible de nuevo.

## Cache (Redis)

Las llamadas a `POST /api/v1/estimate` y `POST /api/v1/estimate/stream` se cachean en Redis: una transcripción repetida con el mismo proveedor, modelo y contexto CAG se sirve al instante y sin coste de tokens (en el endpoint de streaming, la respuesta cacheada se reproduce troceada y el registro `done` incluye `cached: true`).

- La clave incluye un hash del *system prompt*, así que **editar los ejemplos de `app/context/examples.py` invalida el cache automáticamente**.
- **Degradación elegante:** si Redis no está disponible, las peticiones siguen funcionando sin cache (no fallan). Tras un fallo de conexión, el cache se omite durante 30 s para no penalizar la latencia.
- **Nota de comportamiento:** con el cache activo, transcripciones idénticas devuelven siempre la misma respuesta (antes podían variar entre llamadas por la temperatura del modelo).
- Desactívalo con `CACHE_ENABLED=false`.

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
