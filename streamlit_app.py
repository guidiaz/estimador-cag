"""Interfaz de chat Streamlit para el Estimador CAG.

Consume la API del proyecto por HTTP (módulo `api_client`) en lugar de importar
la lógica del backend en el mismo proceso. Arranca la API por separado:

    uv run uvicorn app.main:app --reload      # API  (por defecto :8000)
    uv run streamlit run streamlit_app.py     # UI

La UI apunta a la API mediante la variable de entorno `ESTIMADOR_API_URL`
(por defecto `http://127.0.0.1:8000`).
"""

import time

import httpx
import streamlit as st

from api_client import API_BASE, EstimationError, get_context, request_estimation_stream

st.set_page_config(page_title="Estimador CAG", page_icon="🧮")

st.title("🧮 Estimador CAG")
st.caption(
    "Pega o escribe la transcripción de una reunión con el cliente y obtén "
    "una estimación de software basada en ejemplos previos (CAG)."
)

# Historial de la conversación en el estado de sesión.
if "messages" not in st.session_state:
    st.session_state.messages = []

# Pinta el historial existente en cada re-render.
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Entrada de chat: la transcripción de la reunión.
transcription = st.chat_input("Escribe o pega aquí la transcripción de la reunión...")

if transcription:
    # Mensaje del usuario.
    st.session_state.messages.append({"role": "user", "content": transcription})
    with st.chat_message("user"):
        st.markdown(transcription)

    # Respuesta del asistente: se escribe token a token (streaming vía HTTP).
    with st.chat_message("assistant"):
        usage: dict = {}
        try:
            # st.write_stream consume el generador y va pintando cada delta;
            # devuelve el texto completo al terminar.
            # El slider del panel lateral fija el límite de tokens de salida;
            # su valor persiste en session_state entre re-renders.
            max_tokens = st.session_state.get("max_tokens", 4096)
            start = time.perf_counter()
            estimation = st.write_stream(
                request_estimation_stream(transcription, usage, max_tokens=max_tokens)
            )
            elapsed = time.perf_counter() - start
        except httpx.RequestError:
            # No se pudo conectar con la API (backend apagado, URL incorrecta...).
            error = (
                f"⚠️ No se pudo conectar con la API en `{API_BASE}`.\n\n"
                "Arranca el backend con `uv run uvicorn app.main:app --reload` "
                "o ajusta la variable `ESTIMADOR_API_URL`."
            )
            st.error(error)
            st.session_state.messages.append({"role": "assistant", "content": error})
        except EstimationError as exc:
            # Error reportado por la API durante la generación (400 / 502).
            error = f"⚠️ {exc.detail}"
            st.error(error)
            st.session_state.messages.append({"role": "assistant", "content": error})
        except Exception as exc:  # noqa: BLE001 - cualquier otro fallo inesperado
            error = f"⚠️ Error al generar la estimación: {exc}"
            st.error(error)
            st.session_state.messages.append({"role": "assistant", "content": error})
        else:
            if usage:
                # Métricas de la última llamada (visibles en el panel lateral).
                st.session_state.last_metrics = {**usage, "elapsed": elapsed}
            st.session_state.messages.append(
                {"role": "assistant", "content": estimation}
            )


@st.cache_data(show_spinner=False)
def _load_context() -> dict:
    """Contexto estático del backend (cacheado: no cambia entre llamadas)."""
    return get_context()


with st.sidebar:
    st.subheader("⚙️ Configuración")

    try:
        ctx = _load_context()
    except Exception:  # noqa: BLE001 - API no disponible al cargar el panel
        ctx = None

    if ctx:
        st.write(f"**Proveedor:** `{ctx['provider']}`")
        st.write(f"**Modelo:** `{ctx['model']}`")
    else:
        st.warning(f"API no disponible en `{API_BASE}`.")

    st.slider(
        "Tokens de salida (máx.)",
        min_value=512,
        max_value=8192,
        value=4096,
        step=512,
        key="max_tokens",
        help="Límite de tokens que el modelo puede generar en la respuesta.",
    )

    # --- Métricas de la última llamada ---
    st.subheader("📊 Última llamada")
    metrics = st.session_state.get("last_metrics")
    if metrics:
        st.write(f"**Modelo:** `{metrics['model']}`")
        col_in, col_out = st.columns(2)
        col_in.metric("Tokens entrada", metrics["input_tokens"])
        col_out.metric("Tokens salida", metrics["output_tokens"])
        st.metric("Tiempo de respuesta", f"{metrics['elapsed']:.2f} s")
    else:
        st.caption("Aún no hay llamadas en esta sesión.")

    # --- Contexto CAG inyectado en el system prompt (vía GET /context) ---
    if ctx:
        st.subheader("🧠 Contexto CAG")

        with st.expander("System prompt activo (solo lectura)"):
            st.text_area(
                "System prompt",
                value=ctx["system_prompt"],
                height=300,
                disabled=True,
                label_visibility="collapsed",
            )

        with st.expander(f"Estimaciones de ejemplo ({len(ctx['examples'])})"):
            for index, example in enumerate(ctx["examples"], start=1):
                st.markdown(f"**Ejemplo {index} — resumen de la reunión:**")
                st.caption(example["meeting_summary"])
                st.markdown("**Estimación generada:**")
                st.code(example["estimation"].strip(), language="markdown")

    st.divider()
    if st.button("Limpiar conversación"):
        st.session_state.messages = []
        st.session_state.pop("last_metrics", None)
        st.rerun()
