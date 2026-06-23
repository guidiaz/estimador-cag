"""Tests de la ventana deslizante de `ConversationHistory` (`app.services.sessions`).

El foco es el recorte por turnos (un turno = un par `user`+`assistant`) y el
contrato de `to_messages_list`. Invariantes protegidos: (1) el system prompt
siempre encabeza la salida pero NO se almacena (se regenera por llamada); (2) el
recorte razona en pares, así que jamás separa un `assistant` de su `user` ni deja
la ventana empezando por un `assistant` huérfano; (3) un `user` sin respuesta es
el turno en curso y no se recorta; (4) `pending_user` no muta el historial. Son
tests puramente locales: no tocan el modelo.
"""

import pytest

from app.config import settings
from app.services.sessions import MAX_TURNS, ConversationHistory

SYS = "SYS"


def _window(history: ConversationHistory) -> list[dict]:
    """Mensajes de la ventana (la salida sin el system regenerado de cabeza)."""
    return history.to_messages_list(SYS)[1:]


def _window_contents(history: ConversationHistory) -> list[str]:
    return [m["content"] for m in _window(history)]


def _fill_turns(history: ConversationHistory, n: int) -> None:
    for i in range(n):
        history.add("user", f"u{i}")
        history.add("assistant", f"a{i}")


# --- construcción / configuración ---------------------------------------------


def test_max_turns_invalido_lanza_value_error():
    with pytest.raises(ValueError):
        ConversationHistory(max_turns=0)


def test_max_turns_por_defecto_es_6():
    # El valor por defecto especificado es 6 (ajustable con SESSION_MAX_TURNS, que
    # en el entorno de tests no se sobreescribe).
    assert settings.session_max_turns == 6
    assert MAX_TURNS == 6
    # ConversationHistory toma ese default sin que haya que pasarlo.
    assert ConversationHistory().max_turns == 6


# --- to_messages_list: el system es invariante pero no se almacena -------------


def test_to_messages_list_antepone_siempre_el_system():
    h = ConversationHistory(max_turns=2)
    _fill_turns(h, 3)

    msgs = h.to_messages_list(SYS)
    assert msgs[0] == {"role": "system", "content": SYS}
    # El system no cuenta como turno ni ocupa hueco de la ventana.
    assert h.turn_count() == 2
    assert len(h) == 4  # 2 turnos * (user + assistant)


def test_historial_vacio_solo_devuelve_el_system():
    h = ConversationHistory()
    assert h.to_messages_list(SYS) == [{"role": "system", "content": SYS}]


def test_el_system_se_regenera_en_cada_llamada_no_se_almacena():
    h = ConversationHistory()
    h.add("user", "u0")
    h.add("assistant", "a0")

    # Dos llamadas con system distinto reflejan cada una el valor que se les pasa:
    # no hay system «pegado» de una llamada anterior.
    assert h.to_messages_list("SYS-1")[0]["content"] == "SYS-1"
    assert h.to_messages_list("SYS-2")[0]["content"] == "SYS-2"


def test_add_con_rol_system_lanza_value_error():
    h = ConversationHistory()
    with pytest.raises(ValueError):
        h.add("system", "SYS")


# --- pending_user: completa la lista sin mutar el historial -------------------


def test_pending_user_se_anade_al_final_sin_mutar_el_historial():
    h = ConversationHistory()
    h.add("user", "u0")
    h.add("assistant", "a0")

    msgs = h.to_messages_list(SYS, pending_user="u1")
    assert msgs == [
        {"role": "system", "content": SYS},
        {"role": "user", "content": "u0"},
        {"role": "assistant", "content": "a0"},
        {"role": "user", "content": "u1"},
    ]
    # El turno pendiente no se persiste: el historial sigue con un solo turno.
    assert h.turn_count() == 1
    assert len(h) == 2


# --- recorte por turnos (pares) -----------------------------------------------


def test_recorta_conservando_los_n_turnos_mas_recientes():
    h = ConversationHistory(max_turns=3)
    _fill_turns(h, 5)

    # Se quedan los turnos 2, 3 y 4; se descartan los pares 0 y 1.
    assert _window_contents(h) == ["u2", "a2", "u3", "a3", "u4", "a4"]
    assert h.turn_count() == 3


def test_bajo_el_limite_no_recorta():
    h = ConversationHistory(max_turns=5)
    _fill_turns(h, 3)

    assert h.turn_count() == 3
    assert len(h) == 6


def test_exactamente_en_el_limite_no_recorta():
    h = ConversationHistory(max_turns=3)
    _fill_turns(h, 3)

    assert _window_contents(h) == ["u0", "a0", "u1", "a1", "u2", "a2"]


def test_el_recorte_nunca_separa_assistant_de_su_user():
    h = ConversationHistory(max_turns=2)
    _fill_turns(h, 4)

    win = _window(h)
    # La ventana empieza por `user` y cada par queda completo y emparejado.
    assert win[0]["role"] == "user"
    for u, a in zip(win[::2], win[1::2]):
        assert u["role"] == "user"
        assert a["role"] == "assistant"
        assert u["content"][1:] == a["content"][1:]  # u3<->a3, u2<->a2


# --- turno en curso (user sin respuesta) --------------------------------------


def test_user_sin_respuesta_es_el_turno_en_curso_y_no_se_recorta():
    h = ConversationHistory(max_turns=3)
    _fill_turns(h, 3)
    h.add("user", "u3")  # turno 4 abierto, aún sin assistant

    # Abrir el 4º turno expulsa al más antiguo (turno 0) y deja u3 intacto al final.
    assert h.turn_count() == 3
    assert _window_contents(h)[-1] == "u3"
    assert "u0" not in _window_contents(h)


def test_dos_users_seguidos_cuentan_como_dos_turnos():
    h = ConversationHistory(max_turns=1)
    h.add("user", "u0")
    h.add("user", "u1")

    # Cada `user` abre turno; con max_turns=1 solo sobrevive el último.
    assert h.turn_count() == 1
    assert _window_contents(h) == ["u1"]


def test_assistant_inicial_suelto_no_abre_turno_y_se_descarta_al_recortar():
    h = ConversationHistory(max_turns=1)
    h.add("assistant", "a-suelto")  # no abre turno: no hay `user` que lo preceda
    h.add("user", "u0")
    h.add("assistant", "a0")
    h.add("user", "u1")
    h.add("assistant", "a1")

    # El segundo turno expulsa al primero, y al cortar en el `user` superviviente
    # el assistant suelto de cabeza desaparece con lo anterior.
    assert h.turn_count() == 1
    assert "a-suelto" not in _window_contents(h)
    assert _window_contents(h) == ["u1", "a1"]
