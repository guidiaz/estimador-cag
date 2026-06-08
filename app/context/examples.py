ESTIMATION_EXAMPLES = [
    {
        "meeting_summary": "El cliente necesita una plataforma web de gestión de inventario.",
        "estimation": """
            ## Estimación: Plataforma de Gestión de Inventario

            ### Desglose de tareas:
            1. Diseño UI/UX: 40 horas
            2. Backend API (CRUD inventario): 60 horas
            3. Autenticación y roles: 20 horas
            4. Dashboard con métricas: 30 horas
            5. Testing y QA: 25 horas

            **Total estimado: 175 horas**
            **Equipo recomendado: 2 desarrolladores full-stack + 1 diseñador UX (part-time)**
            **Duración estimada: 6-8 semanas**
        """,
    },
    {
        "meeting_summary": (
            "Startup de logística quiere una app móvil (iOS y Android) para que "
            "repartidores registren entregas, capturen firma del destinatario y "
            "sincronicen estado en tiempo real con el panel web existente."
        ),
        "estimation": """
            ## Estimación: App Móvil de Entregas para Repartidores

            ### Desglose de tareas:
            1. Diseño UI/UX móvil (flujos offline/online): 35 horas
            2. App React Native (listado rutas, detalle entrega, firma): 80 horas
            3. Modo offline y cola de sincronización: 45 horas
            4. Integración API panel web (REST + WebSocket): 30 horas
            5. Notificaciones push (FCM/APNs): 15 horas
            6. Publicación en App Store y Google Play: 20 horas
            7. Testing en dispositivos reales y QA: 35 horas

            **Total estimado: 260 horas**
            **Equipo recomendado: 1 desarrollador mobile senior + 1 backend (part-time) + 1 QA**
            **Duración estimada: 10-12 semanas**
        """,
    },
    {
        "meeting_summary": (
            "Empresa mediana de servicios profesionales necesita un portal de clientes "
            "donde puedan consultar facturas, descargar contratos y abrir tickets de "
            "soporte, integrado con su ERP (SAP) y con inicio de sesión SSO corporativo."
        ),
        "estimation": """
            ## Estimación: Portal de Clientes B2B con Integración ERP

            ### Desglose de tareas:
            1. Diseño UI/UX portal (accesibilidad WCAG 2.1 AA): 50 horas
            2. Frontend web (Next.js, área autenticada): 70 horas
            3. SSO SAML/OIDC con IdP del cliente: 35 horas
            4. Conector lectura facturas y contratos desde SAP: 55 horas
            5. Módulo tickets de soporte (CRUD + adjuntos): 40 horas
            6. Infraestructura y despliegue (Azure, CI/CD): 25 horas
            7. Seguridad, auditoría y pruebas de integración: 45 horas

            **Total estimado: 320 horas**
            **Equipo recomendado: 2 desarrolladores full-stack + 1 integrador SAP + 1 diseñador UX**
            **Duración estimada: 12-14 semanas**
        """,
    },
    {
        "meeting_summary": (
            "El cliente tiene un monolito PHP legacy y quiere extraer el módulo de "
            "pagos a un microservicio en Node.js, con API documentada, tests automatizados "
            "y despliegue en contenedores sin interrumpir operaciones actuales."
        ),
        "estimation": """
            ## Estimación: Extracción del Módulo de Pagos a Microservicio

            ### Desglose de tareas:
            1. Análisis del monolito y diseño de límites del dominio: 30 horas
            2. API de pagos (Node.js, OpenAPI, validaciones): 65 horas
            3. Adaptador/strangler para convivir con el monolito: 40 horas
            4. Migración de datos y scripts de rollback: 25 horas
            5. Contenedorización (Docker) y pipeline CI/CD: 20 horas
            6. Tests unitarios, integración y contrato (Pact): 50 horas
            7. Observabilidad (logs, métricas, alertas): 15 horas
            8. Despliegue gradual (blue-green) y documentación: 25 horas

            **Total estimado: 270 horas**
            **Equipo recomendado: 2 desarrolladores backend senior + 1 DevOps (part-time)**
            **Duración estimada: 9-11 semanas**
        """,
    },
    {
        "meeting_summary": (
            "Marca de retail quiere lanzar una tienda online B2C con catálogo de "
            "500 SKU, carrito, checkout con Stripe y Mercado Pago, cupones de descuento "
            "y panel admin para gestionar pedidos y stock."
        ),
        "estimation": """
            ## Estimación: Tienda Online B2C con Pasarelas de Pago

            ### Desglose de tareas:
            1. Diseño UI/UX (catálogo, ficha producto, checkout): 55 horas
            2. Frontend tienda (Next.js, SEO, responsive): 75 horas
            3. Backend e-commerce (productos, carrito, pedidos): 70 horas
            4. Integración Stripe y Mercado Pago (webhooks): 35 horas
            5. Panel admin (pedidos, stock, cupones): 45 horas
            6. Emails transaccionales y notificaciones: 15 horas
            7. Testing E2E (flujo compra) y QA: 40 horas

            **Total estimado: 335 horas**
            **Equipo recomendado: 2 desarrolladores full-stack + 1 diseñador UX + 1 QA**
            **Duración estimada: 11-13 semanas**
        """,
    },
    {
        "meeting_summary": (
            "Equipo de producto necesita un MVP que permita a usuarios subir documentos "
            "PDF y hacer preguntas en lenguaje natural; las respuestas deben citar "
            "fragmentos del documento usando un LLM (OpenAI) y embeddings en vector store."
        ),
        "estimation": """
            ## Estimación: MVP de Consulta Documental con RAG (LLM)

            ### Desglose de tareas:
            1. Diseño UX (upload, chat, citas de fuente): 25 horas
            2. Frontend MVP (React, upload y chat): 40 horas
            3. Pipeline ingestión PDF (chunking, embeddings): 35 horas
            4. Vector store y búsqueda semántica (Pinecone/pgvector): 25 horas
            5. Orquestación RAG con OpenAI (prompts, citas): 45 horas
            6. API backend, auth básica y límites de uso: 30 horas
            7. Evaluación calidad respuestas y ajuste prompts: 20 horas
            8. Despliegue cloud y monitoreo de costes API: 15 horas

            **Total estimado: 235 horas**
            **Equipo recomendado: 1 desarrollador full-stack + 1 ingeniero ML/IA**
            **Duración estimada: 7-9 semanas**
        """,
    },
    {
        "meeting_summary": (
            "Área de datos de una aseguradora necesita un pipeline ETL nocturno que "
            "extraiga pólizas y siniestros de tres bases Oracle legacy, los transforme "
            "a un modelo unificado y los cargue en un data warehouse Snowflake para BI."
        ),
        "estimation": """
            ## Estimación: Pipeline ETL Oracle → Snowflake para BI

            ### Desglose de tareas:
            1. Análisis fuentes Oracle y modelo destino unificado: 40 horas
            2. Extracción incremental desde 3 bases (Python/SQL): 50 horas
            3. Transformaciones y reglas de negocio (dbt o SQL): 55 horas
            4. Carga y particionado en Snowflake: 30 horas
            5. Orquestación jobs nocturnos (Airflow): 25 horas
            6. Validación calidad datos y alertas de fallo: 35 horas
            7. Documentación lineage y handoff a equipo BI: 15 horas

            **Total estimado: 250 horas**
            **Equipo recomendado: 1 ingeniero de datos senior + 1 analista de negocio (part-time)**
            **Duración estimada: 8-10 semanas**
        """,
    },
]
