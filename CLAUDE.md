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
  UI: chat + sidebar         POST /api/v1/estimate/stream  (NDJSON token stream)
                             GET  /api/v1/context          (provider/model/system_prompt/examples)

app/main.py            FastAPI app, /health, mounts router under /api/v1
  └─ routers/estimations.py   3 endpoints (see below); maps ValueError→400, else→502
       └─ services/llm_service.py   build_system_prompt + LiteLLM Router (retry→fallback)
            ├─ generate_estimation → _complete   blocking → EstimationResult (POST /estimate)
            ├─ stream_estimation   → _stream      generator yielding deltas  (POST /estimate/stream)
            ├─ services/cache.py     Redis cache wrapping both (estimate:v2 / estimate-stream:v2)
            ├─ context/examples.py   ESTIMATION_EXAMPLES injected into the prompt (the "CAG")
            └─ config.py             primary_model / fallback_model + cache settings
  └─ schemas/estimation.py   EstimateRequest (+max_tokens) / EstimateResponse (Pydantic)
```

The three endpoints in `routers/estimations.py`: `POST /estimate` (blocking JSON), `POST /estimate/stream` (NDJSON: `{"type":"delta"|"done"|"error", ...}` — once streaming starts the status is 200, so mid-stream errors are `error` records in the body, not HTTP codes), and `GET /context` (static CAG data for the sidebar).

Key design points to know before editing:

- **Provider routing is a LiteLLM `Router` in `services/llm_service.py`** (`_get_router`, used by `_complete` and `_stream`). Anthropic (`PRIMARY_MODEL`) is the default group `"estimador"`; OpenAI (`FALLBACK_MODEL`) is `"estimador-fallback"`. `num_retries=1` + `fallbacks=[{"estimador": ["estimador-fallback"]}]` means: try Anthropic, retry once on transient/connection errors, then fall back to OpenAI. API keys follow the model's provider via `_api_key_for`, so swapping `primary_model`/`fallback_model` never crosses credentials. The served `provider`/`model` is read back from the response (not from config). Any failure after retries+fallback surfaces as **502** at the router.
- **Streaming fallback has a manual contingency** in `_stream`: it pulls the first chunk inside a `try`; if a connection error fires *before the first delta*, it re-opens forcing `"estimador-fallback"`. This guards against LiteLLM's flaky stream-path fallback. A mid-stream failure (after deltas sent) is not recovered — documented limitation.
- **Model config is `config.py`: `primary_model` / `fallback_model`** (litellm `"<provider>/<model>"` strings). The legacy `llm_provider`/`llm_model`/`resolved_model` no longer drive routing.
- **The prompt is the product.** `build_system_prompt()` formats every entry of `ESTIMATION_EXAMPLES` into the system message; the transcription is the only user message. To change estimate style/format/granularity, edit the examples in `app/context/examples.py` or `SYSTEM_PROMPT_TEMPLATE` — not the call sites.
- **API responses are camelCase over snake_case internals.** `EstimateResponse` uses a Pydantic alias (`used_tokens` ↔ `usedTokens`) with `populate_by_name=True`. Keep that convention for new response fields.
- **Caching is internal to the service, not the router.** `generate_estimation` and `stream_estimation` wrap their LLM calls with `app/services/cache.py` (Redis), keyed per-endpoint (`estimate:v2:` / `estimate-stream:v2:`) by a sha256 of `{primary_model, sp_hash, transcription[, max_tokens]}` (keyed on the router's primary model, not the served provider, so a fallback-served response still hits the cache for identical requests). The `sp_hash` means changing `ESTIMATION_EXAMPLES` auto-invalidates. Two rules when editing the stream path: (1) write to cache **after** the delta loop exhausts cleanly, **never in `finally`** — otherwise a truncated/aborted stream gets cached; (2) `cache.py` must **degrade gracefully** (any Redis error → treat as miss/no-op, never raise) with a short connect timeout + circuit breaker so a down Redis can't slow or break requests.

## Config / environment

Settings come from `.env` via `pydantic-settings` (`extra="ignore"`). Relevant vars: `ANTHROPIC_API_KEY` (primary), `OPENAI_API_KEY` (fallback), `PRIMARY_MODEL` (default `anthropic/claude-haiku-4-5`), `FALLBACK_MODEL` (default `openai/gpt-4o-mini`), and the cache vars `REDIS_URL` (default `redis://localhost:6379/0`), `CACHE_ENABLED` (default `true`), `CACHE_TTL_SECONDS` (default `86400`). Keys are passed into the Router at first use, so a missing/invalid key fails at request time as a 502, not at startup. The legacy `LLM_PROVIDER`/`LLM_MODEL` are no longer used for routing.

## Notes

- All prose, prompts, error messages, and examples are in **Spanish** — match that when extending user-facing strings.
- `streamlit_app.py` + `api_client.py` are the UI: a Streamlit chat client that talks to the API over HTTP (see Architecture). Run it as a second process.
