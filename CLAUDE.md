# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

REST API that turns a client-meeting transcription into a structured software project estimate (Markdown). It uses **CAG (Context-Augmented Generation)**: a fixed set of prior estimation examples is injected into the system prompt — there is no retrieval/vector store at request time. Both OpenAI and Anthropic are supported as interchangeable LLM backends.

## Commands

```bash
uv sync                                          # install deps (creates .venv)
cp .env.example .env                             # then fill in API key(s)
uv run uvicorn app.main:app --reload             # 1) API on :8000 (alternate: --port 8080)
uv run streamlit run streamlit_app.py            # 2) Streamlit UI on :8501
```

The **UI and API are two separate processes** — the Streamlit app (`streamlit_app.py`) is an HTTP client of the API, not an in-process import. Run both. The UI finds the API via `ESTIMADOR_API_URL` (default `http://127.0.0.1:8000`); set it if the API runs elsewhere or on a non-default port.

Docs/playground at `/docs` (Swagger UI). Health check at `/health`. There is **no test suite, linter, or formatter configured** — `pyproject.toml` declares only runtime deps.

## Architecture

Two processes: the **Streamlit UI** (`streamlit_app.py`) talks to the **FastAPI backend** purely over HTTP via the thin client in `api_client.py` — no `app.*` imports cross into the UI.

```
streamlit_app.py ──HTTP──> api_client.py ──> FastAPI (app/)
  UI: st.form + sidebar      POST /api/v1/estimate         (blocking JSON: structured request)
                             POST /api/v1/estimate/stream  (NDJSON token stream, legacy transcription)
                             GET  /api/v1/context          (provider/model/system_prompt/examples)

app/main.py            FastAPI app, /health, mounts router under /api/v1
  ├─ logging_config.py   configure_logging(): structlog + stdlib bridge (called at import)
  ├─ middleware.py       RequestContextMiddleware: pure-ASGI request_id + access log
  └─ routers/estimations.py   5 endpoints (see below); maps ValueError→400, else→502
       └─ services/llm_service.py   prompt build + LiteLLM Router (retry→fallback)
            ├─ generate_estimation → _complete   blocking → EstimationResult (POST /estimate)
            ├─ generate_from_messages → _complete_messages   blocking, full thread (POST /sessions/{id}/estimate)
            ├─ extract_project_metadata → _complete_messages   2nd call/turn: interaction → ProjectMetadata JSON
            ├─ stream_estimation   → _stream      generator yielding deltas  (POST /estimate/stream)
            ├─ services/sessions.py   in-memory session store (history + ProjectMetadata.merged_with), volatile
            ├─ services/documents.py  plain-text extraction of attachments (pypdf / python-docx)
            ├─ prompts/loader.py     render_estimation_prompt (/estimate) + render_session_system_prompt (session)
            ├─ prompts/estimation/v1/  system.j2 / user.j2 / examples.j2 (few-shot)
            ├─ prompts/session/v1/   system.j2 (role + <project_metadata> block + CAG examples)
            ├─ services/cache.py     Redis cache wrapping both (estimate:v2 / estimate-stream:v2)
            ├─ context/examples.py   ESTIMATION_EXAMPLES (the "CAG") — feeds stream + /context only
            └─ config.py             primary_model / fallback_model + cache settings
  └─ schemas.py          EstimationRequest/EstimationResponse (structured /estimate) + legacy EstimateRequest (stream)
```

The five endpoints in `routers/estimations.py`: `POST /estimate` (blocking JSON; **structured** `EstimationRequest` → `EstimationResponse` `{text, prompt_version}`), `POST /estimate/stream` (NDJSON: `{"type":"delta"|"done"|"error", ...}` — once streaming starts the status is 200, so mid-stream errors are `error` records in the body, not HTTP codes; still on the **legacy** free-text `EstimateRequest`), `POST /sessions` (mints a volatile session, returns `{session_id}` UUID v4), `POST /sessions/{id}/estimate` (**multipart** `transcript` + optional `attachments` → conversational CAG estimate over the session thread; → `SessionEstimationResponse`), and `GET /context` (static CAG data for the sidebar). The endpoints intentionally use **different request contracts**: `/estimate` is the structured form contract; `/estimate/stream` keeps the transcription contract; `/sessions/{id}/estimate` is the multipart session contract. FastAPI rejects a malformed `EstimationRequest` (e.g. `description` < 20 chars, bad enum) with **422** before the router's try/except, so the `ValueError→400` path is for business errors raised inside the service.

- **Session memory + attachments (`POST /sessions/{id}/estimate`).** Sessions live in `services/sessions.py` — an **in-memory `dict` (volatile by design, no DB/Redis in this phase)** of `Session` (a `ConversationHistory` sliding window that always keeps the system prompt + drops oldest *turns*, where a turn = one `user`+`assistant` exchange; plus a `ProjectMetadata` Pydantic model). Unknown `session_id` → **404** (consistent with the documented volatility: IDs break across restarts). Attachments (PDF via `pypdf`, Word `.docx` via `python-docx`) are extracted to **plain text in the backend** — deliberately **not** the provider Files API — so the path is provider-agnostic and ready for future RAG chunking; extraction failures (unsupported/corrupt/oversized, capped at 8 files × 10 MB) → **400**. The handler is `async def`, so every blocking call (file extraction, generation, *and* metadata extraction) is wrapped in `run_in_threadpool`. History is mutated (`add` user+assistant) **only after** generation succeeds, so a 502 leaves no orphan turn. This path is **not cached** (the thread grows per turn).
- **Session system prompt + structured metadata.** Unlike the streaming path (which uses `build_system_prompt`), the session path renders its system prompt from **its own Jinja template** `app/prompts/session/v1/system.j2` via `loader.render_session_system_prompt(metadata)`: role + a `<project_metadata>` block (the session's known facts; **empty on turn 1**) + the CAG `ESTIMATION_EXAMPLES`. It's re-rendered each turn with the latest metadata. After each response a **second LLM call** (`llm_service.extract_project_metadata`) extracts facts from the interaction as JSON and merges them into `session.metadata` (`ProjectMetadata.merged_with`: new scalars win, lists union, nulls don't clobber). **Single source of truth = the `ProjectMetadata` schema**: the extraction prompt is built from `model_json_schema()`, the block render iterates `model_fields`, parse-back is `model_validate`, merge iterates `model_fields` — so adding a field needs **no code/template/prompt edits**. Extraction **degrades gracefully** (any provider/JSON/validation failure → logged, metadata unchanged, estimate still 200) and is **synchronous** (one extra round-trip of latency; `BackgroundTask` is the documented next step). The session loader (`render_session_system_prompt`) imports `ESTIMATION_EXAMPLES` directly (not `_format_examples` from `llm_service`) to avoid a `loader`↔`llm_service` cycle.

Key design points to know before editing:

- **Provider routing is a LiteLLM `Router` in `services/llm_service.py`** (`_get_router`, used by `_complete` and `_stream`). Anthropic (`PRIMARY_MODEL`) is the default group `"estimador"`; OpenAI (`FALLBACK_MODEL`) is `"estimador-fallback"`. `num_retries=1` + `fallbacks=[{"estimador": ["estimador-fallback"]}]` means: try Anthropic, retry once on transient/connection errors, then fall back to OpenAI. API keys follow the model's provider via `_api_key_for`, so swapping `primary_model`/`fallback_model` never crosses credentials. The served `provider`/`model` is read back from the response (not from config). Any failure after retries+fallback surfaces as **502** at the router.
- **Observability is structlog (`app/logging_config.py` + `app/middleware.py`).** `configure_logging()` runs at `app/main.py` import: it routes both structlog and stdlib logs (litellm, `cache.py`) through one `ProcessorFormatter` pipeline — console renderer on a TTY, JSON otherwise (`LOG_JSON=true` forces JSON; `LOG_LEVEL` sets level; litellm/httpx are pinned to WARNING). `RequestContextMiddleware` is **pure ASGI, not `BaseHTTPMiddleware`**, on purpose: it binds a `request_id` contextvar that reaches the service-layer logs (BaseHTTPMiddleware's task-copy would drop it) and it never buffers the NDJSON stream. It clears contextvars at the start of each request, echoes/accepts `x-request-id`, and emits `http.request` with status + `duration_ms`. **Token/usage metrics are logged from the service layer, never the middleware** — for streaming the status is already sent before the body is consumed, so `estimate.stream.completed` is emitted from `stream_estimation` only after the generator exhausts cleanly (same not-in-`finally` discipline as the cache write; an aborted stream logs nothing). Blocking path logs `estimate.completed`. A failed stream logs `estimate.stream.error` (with `code` 400/502) from the router's `ndjson()` — the body still carries its `error` record at HTTP 200. The **prompt loader** (`prompts/loader.py`) also emits `prompt.rendered` on every `/estimate` render (`prompt_version`, `system_hash`/`user_hash` = sha256 of each rendered string, `reference_count`) — a content-free fingerprint for prod debugging; `system_hash` equals the cache's `sp_hash` (same digest, computed locally to avoid a `loader`↔`llm_service` import cycle). Log metadata only (model/provider/tokens/`cached`/`transcription_chars`/hashes), never transcription or estimation content.
- **Streaming fallback has a manual contingency** in `_stream`: it pulls the first chunk inside a `try`; if a connection error fires *before the first delta*, it re-opens forcing `"estimador-fallback"`. This guards against LiteLLM's flaky stream-path fallback. A mid-stream failure (after deltas sent) is not recovered — documented limitation.
- **Model config is `config.py`: `primary_model` / `fallback_model`** (litellm `"<provider>/<model>"` strings). The legacy `llm_provider`/`llm_model`/`resolved_model` no longer drive routing.
- **The prompt is the product — but two paths build it differently.** `/estimate` renders its prompt from **Jinja2 templates** via `app/prompts/loader.py::render_estimation_prompt(request, version=PROMPT_VERSION)`, which returns `(system, user)`: `system.j2` holds the role + instructions + conditional blocks keyed on `output_format`/`detail_level` + an `{% include "examples.j2" %}` of **invented few-shot** examples; `user.j2` wraps the `description`. The loader's `Environment` uses `StrictUndefined`/`trim_blocks`/`lstrip_blocks` and a `FileSystemLoader` rooted at `estimation/<version>/` (anchored on `__file__`), so changing `PROMPT_VERSION` (and adding a template dir) swaps prompts without touching call sites. **`/estimate` therefore no longer uses the CAG `ESTIMATION_EXAMPLES`** — those now feed only `stream_estimation` (via `build_system_prompt()`) and `GET /context`. To change `/estimate` style/format/granularity, edit the templates under `app/prompts/estimation/v1/`; for the streaming/legacy path edit `SYSTEM_PROMPT_TEMPLATE` / `ESTIMATION_EXAMPLES`. `PROMPT_VERSION` (in `llm_service.py`, currently `"v1"`) is returned as `prompt_version` and is part of the `/estimate` cache key.
- **Response field naming.** Legacy `EstimateResponse` is camelCase-over-snake via a Pydantic alias + `populate_by_name=True` (`used_tokens` ↔ `usedTokens`; FastAPI serializes `by_alias`). The newer `EstimationResponse` deliberately keeps `prompt_version` in **snake_case on the wire** (no alias) because the agreed contract spec was written that way — so the two endpoints differ here. For genuinely new fields prefer the camelCase alias convention unless a contract dictates otherwise.
- **Caching is internal to the service, not the router.** `generate_estimation` and `stream_estimation` wrap their LLM calls with `app/services/cache.py` (Redis), keyed per-endpoint (`estimate:v2:` = `{primary_model, sp_hash, prompt_version, description, project_type, detail_level, output_format}`; `estimate-stream:v2:` = `{primary_model, sp_hash, transcription, max_tokens}`) by a sha256 of that payload (keyed on the router's primary model, not the served provider, so a fallback-served response still hits the cache for identical requests). The `sp_hash` means changing `ESTIMATION_EXAMPLES` auto-invalidates. Two rules when editing the stream path: (1) write to cache **after** the delta loop exhausts cleanly, **never in `finally`** — otherwise a truncated/aborted stream gets cached; (2) `cache.py` must **degrade gracefully** (any Redis error → treat as miss/no-op, never raise) with a short connect timeout + circuit breaker so a down Redis can't slow or break requests.

## Config / environment

Settings come from `.env` via `pydantic-settings` (`extra="ignore"`). Relevant vars: `ANTHROPIC_API_KEY` (primary), `OPENAI_API_KEY` (fallback), `PRIMARY_MODEL` (default `anthropic/claude-haiku-4-5`), `FALLBACK_MODEL` (default `openai/gpt-4o-mini`), and the cache vars `REDIS_URL` (default `redis://localhost:6379/0`), `CACHE_ENABLED` (default `true`), `CACHE_TTL_SECONDS` (default `86400`), and the observability vars `LOG_LEVEL` (default `INFO`) and `LOG_JSON` (default `false` — auto JSON without a TTY). Keys are passed into the Router at first use, so a missing/invalid key fails at request time as a 502, not at startup. The legacy `LLM_PROVIDER`/`LLM_MODEL` are no longer used for routing.

## Notes

- All prose, prompts, error messages, and examples are in **Spanish** — match that when extending user-facing strings.
- `streamlit_app.py` + `api_client.py` are the UI: a Streamlit **form** (`st.form` → structured `EstimationRequest` → `POST /estimate`, rendering the returned free text) that talks to the API over HTTP (see Architecture). Run it as a second process. `api_client.py` exposes both `request_estimation` (blocking, structured) and `request_estimation_stream` (legacy NDJSON); the streaming client is no longer wired into the UI but is kept functional.
