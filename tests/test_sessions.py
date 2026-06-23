"""Tests del recorte por turnos de `ConversationHistory` (`app.services.sessions`).

El foco es la ventana deslizante: un *turno* es un intercambio que abre cada
mensaje `user`, y al superar `max_turns` se descartan los turnos más antiguos.
Los invariantes que se protegen aquí son los frágiles: (1) el system prompt nunca
caduca y siempre encabeza el hilo; (2) el recorte razona en turnos, así que jamás
separa un `assistant` de su `user`; (3) un `user` aún sin respuesta cuenta como el
turno en curso y no se recorta. Son tests puramente locales: no tocan el modelo.
"""

import pytest

from app.services.sessions import ConversationHistory


def _roles(history: ConversationHistory) -> list[str]:
    return [m["role"] for m in history.messages()]


def _contents(history: ConversationHistory) -> list[str]:
    return [m["content"] for m in history.messages()]


# --- construcción / validación ------------------------------------------------


def test_max_turns_invalido_lanza_value_error():
    with pytest.raises(ValueError):
        ConversationHistory(max_turns=0)


# --- el system prompt se preserva ---------------------------------------------


def test_system_prompt_encabeza_y_queda_fuera_de_la_ventana():
    h = ConversationHistory(max_turns=2)
    h.set_system("SYS")
    for i in range(5):
        h.add("user", f"u{i}")
        h.add("assistant", f"a{i}")

    msgs = h.messages()
    assert msgs[0] == {"role": "system", "content": "SYS"}
    # El system no cuenta como turno ni ocupa hueco de la ventana.
    assert h.turn_count() == 2
    assert len(h) == 4  # 2 turnos * (user + assistant)


def test_set_system_reemplaza_sin_duplicar_y_no_afecta_a_la_ventana():
    h = ConversationHistory(max_turns=3)
    h.set_system("SYS-1")
    h.add("user", "u0")
    h.set_system("SYS-2")

    msgs = h.messages()
    assert msgs[0] == {"role": "system", "content": "SYS-2"}
    assert _roles(h).count("system") == 1
    assert len(h) == 1


def test_add_con_rol_system_equivale_a_set_system():
    h = ConversationHistory(max_turns=2)
    h.add("system", "SYS")
    h.add("user", "u0")

    assert h.messages()[0] == {"role": "system", "content": "SYS"}
    assert h.turn_count() == 1  # el system no abre turno


def test_sin_system_prompt_solo_devuelve_la_ventana():
    h = ConversationHistory(max_turns=2)
    h.add("user", "u0")
    h.add("assistant", "a0")

    assert _roles(h) == ["user", "assistant"]


# --- recorte por turnos -------------------------------------------------------


def test_recorta_conservando_los_n_turnos_mas_recientes():
    h = ConversationHistory(max_turns=3)
    h.set_system("SYS")
    for i in range(5):
        h.add("user", f"u{i}")
        h.add("assistant", f"a{i}")

    # Se quedan los turnos 2, 3 y 4; se descartan 0 y 1.
    assert _contents(h) == ["SYS", "u2", "a2", "u3", "a3", "u4", "a4"]
    assert h.turn_count() == 3


def test_bajo_el_limite_no_recorta():
    h = ConversationHistory(max_turns=5)
    for i in range(3):
        h.add("user", f"u{i}")
        h.add("assistant", f"a{i}")

    assert h.turn_count() == 3
    assert len(h) == 6


def test_exactamente_en_el_limite_no_recorta():
    h = ConversationHistory(max_turns=3)
    for i in range(3):
        h.add("user", f"u{i}")
        h.add("assistant", f"a{i}")

    assert _contents(h) == ["u0", "a0", "u1", "a1", "u2", "a2"]


def test_el_recorte_nunca_separa_assistant_de_su_user():
    h = ConversationHistory(max_turns=2)
    for i in range(4):
        h.add("user", f"u{i}")
        h.add("assistant", f"a{i}")

    # Cada turno conservado mantiene su par completo; ningún `assistant` queda
    # huérfano a la cabeza de la ventana.
    msgs = h.messages()
    assert msgs[0]["role"] == "user"
    pares = list(zip(msgs[::2], msgs[1::2]))
    for u, a in pares:
        assert u["role"] == "user"
        assert a["role"] == "assistant"
        assert u["content"][1:] == a["content"][1:]  # u3<->a3, u2<->a2


# --- turno en curso (user sin respuesta) --------------------------------------


def test_user_sin_respuesta_es_el_turno_en_curso_y_no_se_recorta():
    h = ConversationHistory(max_turns=3)
    for i in range(3):
        h.add("user", f"u{i}")
        h.add("assistant", f"a{i}")
    h.add("user", "u3")  # turno 4 abierto, aún sin assistant

    # Abrir el 4º turno expulsa al más antiguo (turno 0) y deja u3 intacto al final.
    assert h.turn_count() == 3
    assert _contents(h)[-1] == "u3"
    assert "u0" not in _contents(h)


def test_dos_users_seguidos_cuentan_como_dos_turnos():
    h = ConversationHistory(max_turns=1)
    h.add("user", "u0")
    h.add("user", "u1")

    # Cada `user` abre turno; con max_turns=1 solo sobrevive el último.
    assert h.turn_count() == 1
    assert _contents(h) == ["u1"]


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
    assert "a-suelto" not in _contents(h)
    assert _contents(h) == ["u1", "a1"]
