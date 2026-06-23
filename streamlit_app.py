"""Interfaz Streamlit para el Estimador CAG — modo conversación con memoria.

Consume la API del proyecto por HTTP (módulo `api_client`) en lugar de importar
la lógica del backend en el mismo proceso. Arranca la API por separado:

    uv run uvicorn app.main:app --reload      # API  (por defecto :8000)
    uv run streamlit run streamlit_app.py     # UI

La UI apunta a la API mediante la variable de entorno `ESTIMADOR_API_URL`
(por defecto `http://127.0.0.1:8000`).

Esta página demuestra el flujo de **sesión** (`POST /sessions` + `POST
/sessions/{id}/estimate`): al cargar crea una sesión, permite enviar una
transcripción con documentación adjunta opcional, y muestra por separado el
**historial** del diálogo (la memoria conversacional) y la **memoria del
proyecto** (`project_metadata`, la memoria estructurada que el backend infiere
tras cada turno). El formulario estructurado de `POST /estimate` ya no se expone
aquí, pero el endpoint y `api_client.request_estimation` siguen disponibles para
uso programático.
"""

import time

import httpx
import streamlit as st

import api_client
from api_client import API_BASE, EstimationError

# Límites del contrato del endpoint de sesión (`app/services/documents.py`), que
# replicamos en la UI para avisar antes de llegar a la API (mismo criterio que el
# resto de validaciones cliente del proyecto).
_MAX_ATTACHMENTS = 8
_ALLOWED_EXTS = ["pdf", "docx"]

# Etiquetas legibles de los campos de `ProjectMetadata` (las claves del dict son
# los nombres de campo del modelo); si aparece un campo nuevo, cae al propio nombre.
_METADATA_LABELS = {
    "project_name": "Nombre del proyecto",
    "assumed_team_size": "Tamaño de equipo asumido",
    "mentioned_technologies": "Tecnologías mencionadas",
    "agreed_scope": "Alcance acordado",
}

st.set_page_config(page_title="Estimador CAG — Sesión", page_icon="🧮")


def _start_new_session() -> None:
    """Crea una sesión nueva y resetea el estado conversacional de la página."""
    st.session_state.session_id = api_client.create_session()
    st.session_state.turns = []
    st.session_state.project_metadata = {}


def _ensure_session() -> None:
    """Garantiza una sesión al cargar la página (una sola vez por carga)."""
    if "session_id" not in st.session_state:
        _start_new_session()


# La sesión debe existir antes de renderizar el input. Si la API no está disponible
# al cargar, paramos con un mensaje claro en lugar de operar con estado a medias.
try:
    _ensure_session()
except httpx.RequestError:
    st.error(
        f"⚠️ No se pudo conectar con la API en `{API_BASE}`.\n\n"
        "Arranca el backend con `uv run uvicorn app.main:app --reload` "
        "o ajusta la variable `ESTIMADOR_API_URL`."
    )
    st.stop()
except EstimationError as exc:
    st.error(f"⚠️ No se pudo crear la sesión: {exc.detail}")
    st.stop()


def _render_metadata(metadata: dict) -> None:
    """Panel de la memoria estructurada del proyecto (`project_metadata`)."""
    has_data = any(v not in (None, "", []) for v in (metadata or {}).values())
    if not has_data:
        st.caption(
            "Aún vacía. El backend la irá rellenando tras cada turno extrayendo "
            "los hechos del proyecto de la conversación."
        )
        return
    for key, value in metadata.items():
        if value in (None, "", []):
            continue
        label = _METADATA_LABELS.get(key, key)
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        st.markdown(f"**{label}:** {value}")


st.title("🧮 Estimador CAG — Conversación")
st.caption(
    "Envía la transcripción de la reunión (con documentación adjunta opcional) y "
    "el modelo estima en el contexto de la sesión. El historial y la memoria del "
    "proyecto se mantienen entre turnos."
)

# --- Historial del diálogo (la memoria conversacional) ---
st.subheader("🧵 Historial de conversación")
if not st.session_state.turns:
    st.caption("Aún no hay turnos. Envía la primera transcripción más abajo.")
for turn in st.session_state.turns:
    with st.chat_message("user"):
        st.markdown(turn["transcript"])
        if turn["attachments"]:
            st.caption("📎 " + ", ".join(turn["attachments"]))
    with st.chat_message("assistant"):
        st.markdown(turn["estimation"])
        st.caption(
            f"{turn['provider']} · `{turn['model']}` · "
            f"{turn['used_tokens']} tokens · {turn['elapsed']:.2f} s"
        )

# --- Entrada de un nuevo turno ---
with st.form("session_turn_form", clear_on_submit=True):
    transcript = st.text_area(
        "Transcripción de la reunión",
        height=180,
        placeholder="Pega o escribe la transcripción / resumen de la reunión…",
    )
    uploaded = st.file_uploader(
        "Documentación adjunta (opcional)",
        type=_ALLOWED_EXTS,
        accept_multiple_files=True,
        help=f"PDF o Word .docx · máximo {_MAX_ATTACHMENTS} archivos.",
    )
    submitted = st.form_submit_button("Generar estimación", type="primary")

if submitted:
    transcript = (transcript or "").strip()
    uploaded = uploaded or []

    if not transcript:
        st.warning("Escribe la transcripción de la reunión antes de enviar.")
    elif len(uploaded) > _MAX_ATTACHMENTS:
        st.warning(
            f"Como máximo {_MAX_ATTACHMENTS} adjuntos (tienes {len(uploaded)})."
        )
    else:
        attachments = [(f.name, f.getvalue(), f.type) for f in uploaded]
        try:
            start = time.perf_counter()
            with st.spinner("Generando estimación…"):
                resp = api_client.request_session_estimate(
                    st.session_state.session_id, transcript, attachments or None
                )
            elapsed = time.perf_counter() - start
        except httpx.RequestError:
            st.error(
                f"⚠️ No se pudo conectar con la API en `{API_BASE}`. "
                "¿Está arrancado el backend?"
            )
        except EstimationError as exc:
            if exc.code == 404:
                # La sesión se perdió (almacén volátil: reinicio del backend).
                # Recreamos una limpia para que el alumno pueda seguir sin atascarse.
                _start_new_session()
                st.warning(
                    "Tu sesión ya no existe en el servidor (probablemente se "
                    "reinició). He empezado una conversación nueva: vuelve a enviar."
                )
                st.rerun()
            else:
                st.error(f"⚠️ {exc.detail}")
        else:
            st.session_state.turns.append(
                {
                    "transcript": transcript,
                    "attachments": [a[0] for a in attachments],
                    "estimation": resp.get("text", ""),
                    "model": resp.get("model", "—"),
                    "provider": resp.get("provider", "—"),
                    "used_tokens": resp.get("used_tokens", 0),
                    "elapsed": elapsed,
                }
            )
            st.session_state.project_metadata = resp.get("project_metadata") or {}
            st.rerun()

# --- Barra lateral: memoria del proyecto + control de sesión ---
with st.sidebar:
    st.subheader("🧠 Memoria del proyecto")
    st.caption(
        "Memoria **estructurada** (`project_metadata`): los hechos que el backend "
        "infiere y conserva entre turnos, distinta del historial del diálogo."
    )
    _render_metadata(st.session_state.project_metadata)

    st.divider()
    if st.button("🆕 Nueva conversación", type="primary", width="stretch"):
        try:
            _start_new_session()
        except httpx.RequestError:
            st.error(f"No se pudo contactar la API en `{API_BASE}`.")
        except EstimationError as exc:
            st.error(f"No se pudo crear la sesión: {exc.detail}")
        else:
            st.rerun()

    st.caption(f"Sesión actual: `{st.session_state.get('session_id', '—')}`")
    st.caption(f"Turnos en el historial: {len(st.session_state.turns)}")
