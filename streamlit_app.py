"""Interfaz de chat Streamlit para el Estimador CAG.

Reutiliza la lógica de llamada al LLM del proyecto (`app.services.llm_service`):
el usuario pega o escribe la transcripción de una reunión y el asistente
responde con la estimación de software generada.

Ejecutar con:
    uv run streamlit run streamlit_app.py
"""

import time

import streamlit as st

from app.config import settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.services.llm_service import build_system_prompt, stream_estimation

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

    # Respuesta del asistente: se escribe token a token (streaming).
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
                stream_estimation(transcription, usage, max_tokens=max_tokens)
            )
            elapsed = time.perf_counter() - start
        except ValueError as exc:
            # Proveedor LLM no soportado u otro error de validación.
            error = f"⚠️ {exc}"
            st.error(error)
            st.session_state.messages.append({"role": "assistant", "content": error})
        except Exception as exc:  # noqa: BLE001 - mostrar cualquier fallo del proveedor
            error = (
                f"⚠️ Error al generar la estimación: {exc}\n\n"
                "Revisa que la API key y el proveedor estén configurados en `.env`."
            )
            st.error(error)
            st.session_state.messages.append({"role": "assistant", "content": error})
        else:
            if usage:
                # Métricas de la última llamada (visibles en el panel lateral).
                st.session_state.last_metrics = {**usage, "elapsed": elapsed}
            st.session_state.messages.append(
                {"role": "assistant", "content": estimation}
            )

with st.sidebar:
    st.subheader("⚙️ Configuración")
    st.write(f"**Proveedor:** `{settings.llm_provider}`")
    st.write(f"**Modelo:** `{settings.resolved_model}`")
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

    # --- Contexto CAG inyectado en el system prompt ---
    st.subheader("🧠 Contexto CAG")

    with st.expander("System prompt activo (solo lectura)"):
        st.text_area(
            "System prompt",
            value=build_system_prompt(),
            height=300,
            disabled=True,
            label_visibility="collapsed",
        )

    with st.expander(f"Estimaciones de ejemplo ({len(ESTIMATION_EXAMPLES)})"):
        for index, example in enumerate(ESTIMATION_EXAMPLES, start=1):
            st.markdown(f"**Ejemplo {index} — resumen de la reunión:**")
            st.caption(example["meeting_summary"])
            st.markdown("**Estimación generada:**")
            st.code(example["estimation"].strip(), language="markdown")

    st.divider()
    if st.button("Limpiar conversación"):
        st.session_state.messages = []
        st.session_state.pop("last_metrics", None)
        st.rerun()
