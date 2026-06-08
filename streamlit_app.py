"""Interfaz de chat Streamlit para el Estimador CAG.

Reutiliza la lógica de llamada al LLM del proyecto (`app.services.llm_service`):
el usuario pega o escribe la transcripción de una reunión y el asistente
responde con la estimación de software generada.

Ejecutar con:
    uv run streamlit run streamlit_app.py
"""

import streamlit as st

from app.config import settings
from app.services.llm_service import stream_estimation

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
            estimation = st.write_stream(stream_estimation(transcription, usage))
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
                st.caption(
                    f"Proveedor: `{usage['provider']}` · Modelo: `{usage['model']}` · "
                    f"Tokens: {usage['used_tokens']}"
                )
            st.session_state.messages.append(
                {"role": "assistant", "content": estimation}
            )

with st.sidebar:
    st.subheader("Configuración")
    st.write(f"**Proveedor:** `{settings.llm_provider}`")
    st.write(f"**Modelo:** `{settings.resolved_model}`")
    if st.button("Limpiar conversación"):
        st.session_state.messages = []
        st.rerun()
