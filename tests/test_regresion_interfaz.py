"""Pruebas de regresión de datos y mensajes que usa la interfaz.

1. Los mensajes de la ronda PoS (bitácora, mensaje de la ronda y 409
   estado_cambio) cuentan el intento desde 1, igual que la barra «Ahora», el
   paso 3 y el historial. El campo "intento" de los datos y la semilla del
   sorteo siguen contando desde 0, como pide la guía.
"""

from __future__ import annotations

from conftest import avanzar_hasta, completar_ronda_pos, esperar_error, eventos, sim_pos
from nucleo.errores import Conflicto


def test_mensajes_de_la_ronda_cuentan_el_intento_desde_1():
    sim = sim_pos()
    sim.crear_transaccion("N02", "N03", 5)
    sim.iniciar_ronda_pos()
    avanzar_hasta(sim, "SORTEO")

    # Primer intento: «intento 1» en el texto; el dato y la semilla usan 0.
    sorteo = eventos(sim, "sorteo")[-1]
    assert f"Sorteo del bloque 1, intento 1: salió {sim.ronda_pos.proponente}" in sorteo["mensaje"]
    assert "sha256(hash_anterior|1|0)" in sorteo["mensaje"]
    assert sorteo["datos"]["intento"] == 0
    assert sim.ronda_pos.mensaje.startswith("Sorteo del intento 1:")

    tramposo = sim.ronda_pos.proponente
    sim.marcar_deshonesto(tramposo, True, "recompensa_falsa")
    avanzar_hasta(sim, "VOTACION")
    assert sim.ronda_pos.mensaje.startswith("Votación del intento 1:")
    avanzar_hasta(sim, "RECHAZADO")
    rechazo = eventos(sim, "bloque_rechazado")[-1]
    assert f"que propuso {tramposo} (intento 1) fue rechazado" in rechazo["mensaje"]

    # Nuevo sorteo sin el castigado: es el intento 2 (índice 1).
    sim.avanzar_pos(estado_esperado="RECHAZADO")
    assert sim.ronda_pos.intento == 1
    segundo = eventos(sim, "sorteo")[-1]
    assert "Sorteo del bloque 1, intento 2:" in segundo["mensaje"]
    assert "sha256(hash_anterior|1|1)" in segundo["mensaje"]

    # Una pestaña que todavía ve el intento 1 recibe el 409 con el número que ve la interfaz.
    error = esperar_error(sim.avanzar_pos, estado_esperado="SORTEO", intento_esperado=0)
    assert isinstance(error, Conflicto) and error.codigo == "estado_cambio"
    assert "intento 2" in error.mensaje and error.detalles["intento_actual"] == 1

    assert completar_ronda_pos(sim) == "ACEPTADO"
    aceptado = [e for e in eventos(sim, "pos_estado") if e["datos"].get("estado") == "ACEPTADO"][-1]
    assert "en el intento 2." in aceptado["mensaje"]
