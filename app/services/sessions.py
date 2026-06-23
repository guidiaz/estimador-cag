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

# Roles válidos en el hilo, alineados con el contrato de mensajes de LiteLLM/OpenAI
# (`{"role": ..., "content": ...}`), que es lo que consume la capa LLM.
Role = str  # "system" | "user" | "assistant"


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


class ConversationHistory:
    """Hilo de mensajes con ventana deslizante que preserva el system prompt.

    El system prompt (rol `system`) es el contrato con el modelo y nunca debe
    caducar: se guarda aparte y siempre encabeza el hilo. El resto de mensajes
    (`user`/`assistant`) forman una ventana acotada a `max_turns`; al superarla se
    descartan los turnos más antiguos. Aquí un *turno* es un intercambio: cada
    mensaje `user` abre uno y el `assistant` que le sigue lo cierra. El recorte
    razona en turnos —no en mensajes sueltos— para no separar nunca una respuesta
    de su pregunta y para dejar el último turno en curso (un `user` aún sin
    respuesta) intacto.

    No es un Pydantic model porque su valor está en el comportamiento (añadir y
    recortar), no en la validación de un payload de entrada/salida.
    """

    def __init__(self, max_turns: int = 10) -> None:
        if max_turns < 1:
            raise ValueError("max_turns debe ser >= 1")
        self.max_turns = max_turns
        self._system: Message | None = None
        self._messages: list[Message] = []

    def set_system(self, content: str) -> None:
        """Fija (o reemplaza) el system prompt. Queda fuera de la ventana."""
        self._system = Message(role="system", content=content)

    def add(self, role: Role, content: str) -> None:
        """Añade un mensaje. Un `system` reemplaza el prompt; el resto entra a la
        ventana y dispara el recorte de los más antiguos si se supera `max_turns`.
        """
        if role == "system":
            self.set_system(content)
            return
        self._messages.append(Message(role=role, content=content))
        self._trim()

    def _trim(self) -> None:
        """Mantiene solo los `max_turns` turnos más recientes de la ventana.

        Cada mensaje `user` marca el inicio de un turno. Si hay más turnos que
        `max_turns`, cortamos justo en el `user` que abre el turno más antiguo
        que debe permanecer, descartando todo lo anterior. Así nunca se separa un
        `assistant` de su `user` y un `user` sin respuesta todavía cuenta como el
        turno en curso. El system prompt no vive aquí, así que no se ve afectado.
        """
        user_positions = [i for i, m in enumerate(self._messages) if m.role == "user"]
        if len(user_positions) <= self.max_turns:
            return
        start = user_positions[-self.max_turns]
        self._messages = self._messages[start:]

    def messages(self) -> list[dict[str, str]]:
        """Devuelve el hilo listo para la capa LLM: system (si existe) + ventana."""
        thread: list[Message] = []
        if self._system is not None:
            thread.append(self._system)
        thread.extend(self._messages)
        return [m.model_dump() for m in thread]

    def __len__(self) -> int:
        """Número de mensajes en la ventana (sin contar el system prompt)."""
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

    def __init__(self, session_id: str, max_turns: int = 10) -> None:
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

    def __init__(self, max_turns: int = 10) -> None:
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
