# Estimador CAG

API REST que genera estimaciones de proyectos de software a partir de la descripción de un proyecto o reunión con el cliente. Utiliza **CAG** (*Context-Augmented Generation*): ejemplos de estimaciones previas se inyectan en el prompt del sistema para guiar formato, nivel de detalle y criterios de estimación.

## Características

- `POST /api/v1/estimate` (bloqueante): recibe una **petición estructurada** (descripción + tipo de proyecto, nivel de detalle y formato de salida) y devuelve la estimación como texto libre más la versión del prompt que la generó.
- `POST /api/v1/estimate/stream` (streaming NDJSON, token a token): opera sobre una transcripción libre.
- **Sesiones con memoria conversacional**: `POST /api/v1/sessions` crea una sesión y `POST /api/v1/sessions/{session_id}/estimate` genera estimaciones **multi-turno** sobre ella, con **documentación adjunta opcional** (PDF / Word `.docx`) de la que se extrae texto plano en el backend.
- Soporte para **Anthropic** (por defecto) y **OpenAI** (fallback) mediante un router de LiteLLM.
- Contexto fijo con ejemplos reales (inventario, app móvil, portal B2B, microservicios, etc.) en `app/context/examples.py`.
- Cache opcional en Redis y logging estructurado (structlog) con correlación de peticiones.
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
| `LOG_LEVEL` | Nivel de logging: `DEBUG`/`INFO`/`WARNING`/`ERROR` (por defecto `INFO`) |
| `LOG_JSON` | Fuerza salida de logs en JSON aunque haya terminal (por defecto `false`: consola con TTY, JSON sin él) |
| `REDIS_URL` | URL de Redis para el cache (por defecto `redis://localhost:6379/0`) |
| `CACHE_ENABLED` | Activa/desactiva el cache de estimaciones (por defecto `true`) |
| `CACHE_TTL_SECONDS` | Expiración de las entradas de cache en segundos (por defecto `86400`) |
| `SESSION_MAX_TURNS` | Tamaño de la ventana deslizante de memoria de sesión, en turnos (un turno = par user+assistant; por defecto `6`) |

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

`streamlit_app.py` es un cliente HTTP de la API: un **formulario** (descripción + tipo de proyecto, nivel de detalle y formato) que envía una petición estructurada a `POST /api/v1/estimate` y muestra la estimación. Es un **proceso aparte**; arranca primero la API y luego, en otra terminal:

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
  -d "{\"description\": \"El cliente necesita un dashboard interno para visualizar KPIs de ventas en tiempo real, con login SSO y exportación a PDF.\", \"project_type\": \"internal_tool\", \"detail_level\": \"detailed\", \"output_format\": \"phases_table\"}"
```

**Request** (`EstimationRequest`):

```json
{
  "description": "Descripción del proyecto o resumen de la reunión (20–2000 caracteres)",
  "project_type": "mobile_app | web_saas | internal_tool | data_pipeline",
  "detail_level": "summary | medium | detailed",
  "output_format": "phases_table | line_items | narrative"
}
```

**Response** (`EstimationResponse`):

```json
{
  "text": "## Estimación: ...\n\n### Desglose de tareas:\n...",
  "prompt_version": "structured-v1"
}
```

Una petición con campos inválidos (descripción corta, enum desconocido) se rechaza con **422** antes de llegar al modelo. También puedes probar el endpoint desde la documentación interactiva en [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

> El endpoint de streaming `POST /api/v1/estimate/stream` mantiene el contrato heredado basado en `transcription` (devuelve NDJSON token a token).

### Sesiones con memoria y documentación adjunta

Para estimar de forma **conversacional** (varios turnos sobre el mismo contexto) y aportar documentación complementaria, primero se crea una sesión:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/sessions
# {"session_id": "8938f4e4-975f-4f99-9c6b-58e839dc2a46"}
```

El cliente guarda ese `session_id` (UUID v4) y lo reenvía en cada petición posterior para reutilizar la memoria entre páginas. Luego se solicita la estimación con `multipart/form-data`:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/sessions/<session_id>/estimate \
  -F "transcript=El cliente necesita un panel interno de KPIs con login SSO." \
  -F "attachments=@requisitos.pdf" \
  -F "attachments=@alcance.docx"
```

- `transcript` (texto, requerido): la transcripción de la reunión.
- `attachments` (lista opcional de ficheros): documentación complementaria en **PDF** o **Word `.docx`**.

**Response** (`SessionEstimationResponse`):

```json
{
  "text": "## Estimación: ...",
  "model": "anthropic/claude-haiku-4-5",
  "provider": "anthropic",
  "used_tokens": 1234,
  "attachments": [
    {"filename": "requisitos.pdf", "extracted_chars": 4096},
    {"filename": "alcance.docx", "extracted_chars": 1820}
  ]
}
```

La estimación se genera viendo el **hilo completo** de la sesión (la memoria multi-turno) y el nuevo turno se persiste en ella. Errores: **404** si la sesión no existe (el almacén de sesiones es **volátil** —vive en memoria del proceso—, así que tras un reinicio los `session_id` previos dejan de ser válidos; es un compromiso asumido en esta fase), **400** si un adjunto no es soportado/está corrupto o se exceden los límites (máx. 8 adjuntos, 10 MB por archivo), **502** si falla el proveedor.

#### Por qué la documentación se procesa en backend (texto plano)

De los adjuntos **extraemos el texto plano nosotros** (`pypdf` para PDF, `python-docx` para Word), **sin** subir los ficheros a la *Files API* de OpenAI/Anthropic. Los motivos:

- **Independencia del proveedor.** El texto extraído se inyecta como un mensaje más del prompt, así que funciona igual sea cual sea el modelo configurado y el fallback Anthropic↔OpenAI no depende de que ambos soporten el mismo formato binario ni la misma API de archivos.
- **Control para el RAG futuro.** Tener el texto plano en el backend es el punto de partida natural para el *chunking* y la indexación de un RAG más adelante: troceamos, embebemos e indexamos sobre texto que ya controlamos, sin reextraer ni depender de un almacén de archivos remoto.
- **Coste y trazabilidad.** Evita reenviar el binario completo en cada turno y permite medir/registrar cuánto texto aportó cada adjunto (solo metadatos: nombre y nº de caracteres, **nunca** el contenido).

Solo se admiten formatos con capa de texto. Un PDF escaneado (solo imagen) extrae texto vacío y se ignora ese adjunto; el `.doc` binario antiguo no se soporta (sí el `.docx` moderno).

#### Memoria estructurada del proyecto (`<project_metadata>`)

Cada sesión mantiene, además del hilo de la conversación, una **memoria estructurada** de hechos del proyecto (`ProjectMetadata`: nombre, tamaño de equipo asumido, tecnologías mencionadas, alcance acordado). En cada turno, el system prompt incluye un bloque `<project_metadata>` con los hechos ya conocidos (vacío en la primera interacción), de modo que el modelo es coherente entre turnos y no vuelve a preguntar lo que ya está establecido.

El hilo del diálogo se acota con una **ventana deslizante** de `SESSION_MAX_TURNS` turnos (por defecto **6**; un turno es un par user+assistant): al superarse, se descartan los pares más antiguos para acotar el coste en tokens. El system prompt **no** entra en la ventana: es invariante y se **regenera** en cada llamada a partir del `project_metadata` actual, así que nunca queda obsoleto aunque la conversación crezca.

Esa memoria se actualiza **después de cada respuesta** mediante una **segunda llamada al LLM**: un prompt específico que recibe la interacción (transcripción + estimación generada) y devuelve un JSON con los campos de `ProjectMetadata`, que se funde sobre los hechos previos (los escalares nuevos ganan, las listas se unen, lo desconocido no se pisa).

**Por qué delegar la extracción en el LLM y no en expresiones regulares:**

- **Más fiable y sencillo.** Detectar «el equipo será de 4 personas» o «usaremos React y Postgres» en lenguaje natural libre con regex es frágil y costoso de mantener; el LLM lo hace de forma más robusta y con mucho menos código.
- **Más flexible ante la evolución del modelo de datos.** El prompt de extracción se genera a partir del **propio esquema** de `ProjectMetadata` (`model_json_schema()`), y el bloque del prompt, el parseo y la fusión recorren sus campos de forma genérica. Si más adelante `ProjectMetadata` gana o cambia campos, **no hay que tocar código ni el prompt**: el nuevo campo se extrae, se renderiza y se funde automáticamente.
- **El coste está justificado.** Es una llamada adicional al LLM por turno, pero a cambio se obtiene una memoria estructurada fiable y mantenible. La extracción es **síncrona** (añade una ronda de latencia) y **degrada con elegancia**: si falla (error del proveedor, respuesta no-JSON, validación), se registra y la metadata simplemente no avanza ese turno, sin afectar a la estimación ya entregada. Si la latencia se volviera un problema, el siguiente paso natural es moverla a un `BackgroundTask` de FastAPI.

## Cómo funciona (CAG)

```
┌─────────────────────────┐      ┌──────────────────────────────────┐
│  Descripción + directivas│────> │  LLM (Anthropic / OpenAI)        │
│  (tipo/detalle/formato)  │      │                                  │
│  (user message)          │      │  system: instrucciones +         │
└─────────────────────────┘      │          ejemplos previos (CAG)  │
                                  └──────────────────────────────────┘
                                        │
                                        v
                              Estimación en texto libre (Markdown)
```

1. El **system prompt** incluye instrucciones de rol y los ejemplos de `app/context/examples.py` (resumen de reunión + estimación generada).
2. La **descripción** del proyecto se envía como mensaje de usuario, precedida de directivas en español traducidas de los enums (`project_type` / `detail_level` / `output_format`).
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

Las llamadas a `POST /api/v1/estimate` y `POST /api/v1/estimate/stream` se cachean en Redis y se sirven al instante y sin coste de tokens cuando se repite una petición idéntica con el mismo modelo y contexto CAG. La clave de `/estimate` incluye la descripción y los campos del contrato (tipo, detalle, formato) más la versión del prompt; la de streaming, la transcripción y `max_tokens` (en streaming, la respuesta cacheada se reproduce troceada y el registro `done` incluye `cached: true`).

- La clave incluye un hash del *system prompt*, así que **editar los ejemplos de `app/context/examples.py` invalida el cache automáticamente**.
- **Degradación elegante:** si Redis no está disponible, las peticiones siguen funcionando sin cache (no fallan). Tras un fallo de conexión, el cache se omite durante 30 s para no penalizar la latencia.
- **Nota de comportamiento:** con el cache activo, peticiones idénticas devuelven siempre la misma respuesta (antes podían variar entre llamadas por la temperatura del modelo).
- Desactívalo con `CACHE_ENABLED=false`.

## Estructura del proyecto

```
estimador-cag/
├── app/
│   ├── main.py              # Aplicación FastAPI (+ middleware de observabilidad)
│   ├── config.py            # Settings desde .env
│   ├── logging_config.py    # Configuración de structlog
│   ├── middleware.py        # request_id + access log (ASGI)
│   ├── schemas.py           # Contrato Pydantic (EstimationRequest/Response + sesiones + legacy)
│   ├── context/
│   │   └── examples.py      # Ejemplos CAG
│   ├── routers/
│   │   └── estimations.py   # POST /estimate, /estimate/stream, /sessions, /sessions/{id}/estimate, GET /context
│   └── services/
│       ├── llm_service.py   # Router LiteLLM (Anthropic/OpenAI) + prompts
│       ├── sessions.py      # Memoria de sesión en memoria (hilo + metadatos), volátil
│       ├── documents.py     # Extracción de texto plano de adjuntos (pypdf / python-docx)
│       └── cache.py         # Cache Redis con degradación elegante
├── streamlit_app.py         # UI (formulario) — proceso aparte
├── api_client.py            # Cliente HTTP fino de la API
├── .env.example
├── pyproject.toml
└── README.md
```

## Códigos de error

| Código | Causa |
|--------|--------|
| `422` | La petición no cumple el contrato (descripción fuera de rango, enum desconocido, campo faltante) |
| `400` | Error de negocio de validación dentro del servicio (incluye adjunto no soportado/corrupto o exceso de límites) |
| `404` | La sesión indicada en `/sessions/{id}/estimate` no existe (el almacén de sesiones es volátil) |
| `502` | Fallo al llamar al proveedor (API key, red, modelo, etc.) |

## Stack

- [FastAPI](https://fastapi.tiangolo.com/)
- [Uvicorn](https://www.uvicorn.org/)
- [LiteLLM](https://docs.litellm.ai/) (router Anthropic / OpenAI)
- [Streamlit](https://streamlit.io/) (UI)
- [Redis](https://redis.io/) (cache opcional)
- [pypdf](https://pypdf.readthedocs.io/) + [python-docx](https://python-docx.readthedocs.io/) (extracción de texto de adjuntos)
- [structlog](https://www.structlog.org/) (logging estructurado)
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)

## Licencia

Consulta el repositorio para información de licencia.
