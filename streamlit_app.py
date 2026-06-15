"""Interfaz Streamlit para el Estimador CAG.

Consume la API del proyecto por HTTP (módulo `api_client`) en lugar de importar
la lógica del backend en el mismo proceso. Arranca la API por separado:

    uv run uvicorn app.main:app --reload      # API  (por defecto :8000)
    uv run streamlit run streamlit_app.py     # UI

La UI apunta a la API mediante la variable de entorno `ESTIMADOR_API_URL`
(por defecto `http://127.0.0.1:8000`).

El formulario reproduce el contrato `EstimationRequest` del servicio: su envío
hace `POST /api/v1/estimate` y muestra la estimación (texto libre) que devuelve.
"""

import time

import httpx
import streamlit as st

from api_client import API_BASE, EstimationError, get_context, request_estimation

# Etiquetas en español ↔ valores del contrato (los enums de `app/schemas.py`).
PROJECT_TYPES = {
    "mobile_app": "Aplicación móvil",
    "web_saas": "Plataforma web SaaS",
    "internal_tool": "Herramienta interna",
    "data_pipeline": "Pipeline de datos",
}
DETAIL_LEVELS = {
    "summary": "Resumen",
    "medium": "Medio",
    "detailed": "Detallado",
}
OUTPUT_FORMATS = {
    "phases_table": "Tabla de fases",
    "line_items": "Lista de partidas",
    "narrative": "Narrativo",
}

# Límites del contrato (`EstimationRequest.description`), validados también aquí.
_DESC_MIN = 20
_DESC_MAX = 2000

st.set_page_config(page_title="Estimador CAG", page_icon="🧮")

st.title("🧮 Estimador CAG")
st.caption(
    "Describe el proyecto y elige tipo, nivel de detalle y formato. La estimación "
    "se genera a partir de ejemplos previos (CAG)."
)

with st.form("estimation_form"):
    description = st.text_area(
        "Descripción del proyecto",
        height=200,
        max_chars=_DESC_MAX,
        placeholder=(
            "Resume la reunión o describe el proyecto: objetivos, alcance, "
            "integraciones, plazos…"
        ),
        help=f"Entre {_DESC_MIN} y {_DESC_MAX} caracteres.",
    )

    col_type, col_detail, col_format = st.columns(3)
    project_type = col_type.selectbox(
        "Tipo de proyecto",
        options=list(PROJECT_TYPES),
        format_func=PROJECT_TYPES.get,
    )
    detail_level = col_detail.selectbox(
        "Nivel de detalle",
        options=list(DETAIL_LEVELS),
        format_func=DETAIL_LEVELS.get,
    )
    output_format = col_format.selectbox(
        "Formato de salida",
        options=list(OUTPUT_FORMATS),
        format_func=OUTPUT_FORMATS.get,
    )

    submitted = st.form_submit_button("Generar estimación", type="primary")

if submitted:
    description = description.strip()
    if len(description) < _DESC_MIN:
        st.warning(
            f"La descripción debe tener al menos {_DESC_MIN} caracteres "
            f"(actual: {len(description)})."
        )
    else:
        try:
            start = time.perf_counter()
            with st.spinner("Generando estimación…"):
                result = request_estimation(
                    description, project_type, detail_level, output_format
                )
            elapsed = time.perf_counter() - start
        except httpx.RequestError:
            # No se pudo conectar con la API (backend apagado, URL incorrecta...).
            st.error(
                f"⚠️ No se pudo conectar con la API en `{API_BASE}`.\n\n"
                "Arranca el backend con `uv run uvicorn app.main:app --reload` "
                "o ajusta la variable `ESTIMADOR_API_URL`."
            )
        except EstimationError as exc:
            # Error reportado por la API (400/422 validación, 502 proveedor).
            st.error(f"⚠️ {exc.detail}")
        except Exception as exc:  # noqa: BLE001 - cualquier otro fallo inesperado
            st.error(f"⚠️ Error al generar la estimación: {exc}")
        else:
            st.session_state.last_result = {
                "text": result.get("text", ""),
                "prompt_version": result.get("prompt_version", "—"),
                "elapsed": elapsed,
            }

# Última estimación generada (persiste entre re-renders del formulario).
last_result = st.session_state.get("last_result")
if last_result:
    st.subheader("Estimación")
    st.markdown(last_result["text"])
    st.caption(
        f"Versión del prompt: `{last_result['prompt_version']}` · "
        f"{last_result['elapsed']:.2f} s"
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

    # --- Última llamada ---
    st.subheader("📊 Última llamada")
    if last_result:
        st.write(f"**Versión del prompt:** `{last_result['prompt_version']}`")
        st.metric("Tiempo de respuesta", f"{last_result['elapsed']:.2f} s")
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
    if st.button("Limpiar resultado"):
        st.session_state.pop("last_result", None)
        st.rerun()
