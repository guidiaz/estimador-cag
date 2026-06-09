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
       └─ services/llm_service.py   build_system_prompt + provider dispatch
            ├─ generate_estimation   blocking → EstimationResult   (POST /estimate)
            └─ stream_estimation     generator yielding text deltas (POST /estimate/stream)
            ├─ context/examples.py   ESTIMATION_EXAMPLES injected into the prompt (the "CAG")
            └─ config.py             settings + resolved_model
  └─ schemas/estimation.py   EstimateRequest (+max_tokens) / EstimateResponse (Pydantic)
```

The three endpoints in `routers/estimations.py`: `POST /estimate` (blocking JSON), `POST /estimate/stream` (NDJSON: `{"type":"delta"|"done"|"error", ...}` — once streaming starts the status is 200, so mid-stream errors are `error` records in the body, not HTTP codes), and `GET /context` (static CAG data for the sidebar).

Key design points to know before editing:

- **Provider dispatch lives in `services/llm_service.py`** (`generate_estimation` → `_call_openai` / `_call_anthropic`). It branches on `settings.llm_provider`. Adding a provider means a new `_call_*` plus a branch here; an unknown provider raises `ValueError`, which the router converts to **400**. Any other failure (bad API key, network, model name) surfaces as **502**.
- **Model selection is in `config.py::Settings.resolved_model`**: if `LLM_MODEL` is set it wins; otherwise the default depends on provider (`anthropic` → `claude-haiku-4-5`, else → `gpt-o4-mini`). Don't hardcode model names elsewhere.
- **The prompt is the product.** `build_system_prompt()` formats every entry of `ESTIMATION_EXAMPLES` into the system message; the transcription is the only user message. To change estimate style/format/granularity, edit the examples in `app/context/examples.py` or `SYSTEM_PROMPT_TEMPLATE` — not the call sites.
- **API responses are camelCase over snake_case internals.** `EstimateResponse` uses a Pydantic alias (`used_tokens` ↔ `usedTokens`) with `populate_by_name=True`. Keep that convention for new response fields.

## Config / environment

Settings come from `.env` via `pydantic-settings` (`extra="ignore"`). Relevant vars: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `LLM_PROVIDER` (`openai` | `anthropic`, default `openai`), `LLM_MODEL` (blank = provider default). The app reads keys eagerly inside the `_call_*` functions, so a missing key fails at request time as a 502, not at startup.

## Notes

- All prose, prompts, error messages, and examples are in **Spanish** — match that when extending user-facing strings.
- `streamlit_app.py` (untracked) is an empty placeholder, presumably a planned UI; ignore unless asked.
