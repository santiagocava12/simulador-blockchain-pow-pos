"""Regresiones de la revisión final del orquestador."""

from conftest import sim_pow


def test_mensaje_de_mineria_cuenta_solo_los_mineros_conectados():
    sim = sim_pow()
    sim.generar_transacciones(2)
    sim.fijar_conexion("N10", False)
    respuesta = sim.iniciar_mineria()
    assert "9 mineros conectados" in respuesta["mensaje"]


def test_mensaje_de_mineria_con_todos_conectados():
    sim = sim_pow()
    sim.generar_transacciones(2)
    respuesta = sim.iniciar_mineria()
    assert "10 mineros," in respuesta["mensaje"]
