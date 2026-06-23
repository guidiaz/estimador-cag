"""Estado conversacional en memoria para estimaciones multi-turno.

Este módulo introduce *sesiones*: el hilo de conversación con un cliente más los
datos del proyecto que se van acumulando turno a turno. A diferencia del resto
del servicio —donde cada `POST /estimate` es sin estado—, aquí guardamos memoria
entre peticiones.

**Por qué volatilidad (sin BBDD, sin Redis) en esta fase.** El almacén es un
diccionario en el proceso de Python (`SessionStore`). Es deliberado y temporal:

- Estamos validando el *producto* (¿sirve mantener contexto entre turnos?), no su
  durabilidad. Un dict acota la complejidad a cero infraestructura: ni esquema,
  ni migraciones, ni serialización, ni TTLs distribuidos.
- Las sesiones son cortas y de un solo usuario (la reunión que se está estimando);
  perder el estado al reiniciar el proceso es un coste aceptable mientras iteramos
  el diseño de `ProjectMetadata` y la ventana de contexto.
- Las consecuencias asumidas y conocidas son: (1) no sobrevive a un reinicio ni a
  un deploy; (2) no se comparte entre workers/procesos —con varios workers el
  enrutado a sesión no es estable—; (3) crece sin límite si no se purga. Cuando el
  flujo se estabilice, este almacén es el único punto a sustituir por Redis (o una
  BBDD) detrás de la misma interfaz, sin tocar a quien consume `Session`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import settings

# Roles válidos en el hilo, alineados con el contrato de mensajes de LiteLLM/OpenAI
# (`{"role": ..., "content": ...}`), que es lo que consume la capa LLM.
Role = str  # "system" | "user" | "assistant"

# Tamaño por defecto de la ventana deslizante, en turnos (un turno = un par
# user+assistant). Vale 6 salvo que se ajuste por configuración (`SESSION_MAX_TURNS`).
MAX_TURNS = settings.session_max_turns


class Message(BaseModel):
    """Un mensaje del hilo, en el formato que espera la capa LLM."""

    role: Role
    content: str


class ProjectMetadata(BaseModel):
    """Datos del proyecto que se van fijando a lo largo de la conversación.

    Es la memoria *estructurada* de la sesión (frente al hilo de texto libre de
    `ConversationHistory`): lo que se ha acordado y conviene mantener estable entre
    turnos para no re-preguntarlo ni perderlo. Todos los campos son opcionales y
    arrancan vacíos: se rellenan a medida que la conversación los revela.
    """

    project_name: str | None = Field(
        default=None,
        max_length=120,
        description="Nombre del proyecto, si se ha mencionado",
    )
    assumed_team_size: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Tamaño de equipo asumido para la estimación",
    )
    mentioned_technologies: list[str] = Field(
        default_factory=list,
        description="Tecnologías/stack mencionados (se van acumulando)",
    )
    agreed_scope: str | None = Field(
        default=None,
        max_length=4000,
        description="Alcance acordado hasta ahora, en texto libre",
    )

    def merged_with(self, updates: "ProjectMetadata") -> "ProjectMetadata":
        """Funde hechos nuevos sobre los actuales, sin perder lo ya sabido.

        Recorre los campos del modelo (no nombres codificados, así añadir un campo
        nuevo no obliga a tocar este método): los escalares se sobrescriben solo si
        el valor nuevo es no nulo/no vacío, y las listas se **unen** sin duplicar
        ni reordenar. Devuelve una instancia nueva (no muta `self`)."""
        data = self.model_dump()
        for name in type(self).model_fields:
            new_value = getattr(updates, name)
            if isinstance(new_value, list):
                existing = data.get(name) or []
                data[name] = list(dict.fromkeys([*existing, *new_value]))
            elif new_value not in (None, ""):
                data[name] = new_value
        return ProjectMetadata(**data)


class ConversationHistory:
    """Ventana deslizante de turnos `user`/`assistant`.

    Solo almacena los turnos del diálogo. Un *turno* es un par: cada mensaje
    `user` lo abre y el `assistant` que le sigue lo cierra. La ventana se acota a
    `max_turns` (por defecto `MAX_TURNS`); al superarse se descartan los **pares
    más antiguos**. El recorte razona en turnos —no en mensajes sueltos— para no
    separar nunca una respuesta de su pregunta y para dejar el último turno en
    curso (un `user` aún sin respuesta) intacto.

    **El system prompt no se almacena aquí.** Es invariante (siempre presente en la
    salida) pero se *regenera* en cada `to_messages_list`: así refleja siempre el
    `project_metadata` más reciente y nunca queda obsoleto. Por eso esta clase no
    importa el renderizador (vive en `prompts/loader.py`, que sí depende de este
    módulo): el llamante le pasa el system prompt ya renderizado.

    No es un Pydantic model porque su valor está en el comportamiento (añadir y
    recortar), no en la validación de un payload de entrada/salida.
    """

    def __init__(self, max_turns: int = MAX_TURNS) -> None:
        if max_turns < 1:
            raise ValueError("max_turns debe ser >= 1")
        self.max_turns = max_turns
        self._messages: list[Message] = []

    def add(self, role: Role, content: str) -> None:
        """Añade un mensaje `user` o `assistant` y recorta la ventana si procede.

        El rol `system` no se acepta: el system prompt no se almacena, se regenera
        en `to_messages_list`."""
        if role == "system":
            raise ValueError(
                "El system prompt no se almacena en el historial; se regenera en "
                "to_messages_list a partir del project_metadata."
            )
        self._messages.append(Message(role=role, content=content))
        self._trim()

    def _trim(self) -> None:
        """Mantiene solo los `max_turns` turnos (pares) más recientes.

        Cada mensaje `user` marca el inicio de un turno. Si hay más turnos que
        `max_turns`, cortamos justo en el `user` que abre el turno más antiguo
        que debe permanecer, descartando todo lo anterior. Así nunca se separa un
        `assistant` de su `user`, un `user` sin respuesta cuenta como el turno en
        curso y la ventana jamás empieza por un `assistant` huérfano.
        """
        user_positions = [i for i, m in enumerate(self._messages) if m.role == "user"]
        if len(user_positions) <= self.max_turns:
            return
        start = user_positions[-self.max_turns]
        self._messages = self._messages[start:]

    def to_messages_list(
        self, system_prompt: str, pending_user: str | None = None
    ) -> list[dict[str, str]]:
        """Devuelve el array `messages` listo para la API del LLM.

        Antepone `system_prompt` (invariante, siempre presente) a la ventana de
        turnos. `system_prompt` lo aporta ya renderizado el llamante, que lo
        regenera en cada llamada a partir del `project_metadata` actual de la
        sesión; como no se almacena, nunca queda obsoleto. Si se pasa
        `pending_user`, se añade como turno `user` final (el que está a punto de
        responderse) **sin** mutar el historial: así la lista queda completa para
        enviar sin dejar un turno huérfano si la generación falla."""
        thread: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        thread.extend(m.model_dump() for m in self._messages)
        if pending_user is not None:
            thread.append({"role": "user", "content": pending_user})
        return thread

    def __len__(self) -> int:
        """Número de mensajes en la ventana (los turnos `user`/`assistant`)."""
        return len(self._messages)

    def turn_count(self) -> int:
        """Turnos en la ventana (= número de mensajes `user`)."""
        return sum(1 for m in self._messages if m.role == "user")


class Session:
    """Estado de una conversación de estimación: hilo + metadatos del proyecto.

    Agrupa las dos memorias de una sesión —el `ConversationHistory` (texto del
    diálogo) y la `ProjectMetadata` (datos estructurados acordados)— bajo un
    `session_id`. Es el objeto que `SessionStore` indexa en memoria.
    """

    def __init__(self, session_id: str, max_turns: int = MAX_TURNS) -> None:
        self.session_id = session_id
        self.history = ConversationHistory(max_turns=max_turns)
        self.metadata = ProjectMetadata()


class SessionStore:
    """Almacén volátil de sesiones, indexado por `session_id`, en este proceso.

    Es un simple `dict[str, Session]`. Volatilidad asumida a propósito en esta
    fase: ver la nota de cabecera del módulo. La interfaz (`get_or_create`,
    `get`, `drop`) es el punto único a respaldar con Redis/BBDD más adelante sin
    cambiar a sus consumidores.
    """

    def __init__(self, max_turns: int = MAX_TURNS) -> None:
        self._max_turns = max_turns
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            session = Session(session_id, max_turns=self._max_turns)
            self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def drop(self, session_id: str) -> None:
        """Elimina una sesión si existe (cierre explícito o limpieza)."""
        self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._sessions)


# Singleton del proceso. Mientras el almacén sea en memoria, esta instancia ES el
# estado de sesiones del servicio; sustituirla es el camino a un backend durable.
sessions = SessionStore()
