"""Pruebas de integración de la fachada (simulador.py + pow.py + pos.py + app.py).

Cubren las precisiones del contrato que fijó la integración: el estado
"ganador" dura un paso, los últimos bloques minados, el ritmo del motor, las
rondas PoS automáticas encadenadas, la cadena recibida por HTTP (se difunde,
reinicia la minería, cancela la ronda PoS y rechaza bloques del futuro), el
registro de peticiones rechazadas, la reversión ante una falla imprevista y
la rapidez de estado().
"""

from __future__ import annotations

import statistics
import time

import pytest

from conftest import (
    SEMILLA,
    assert_invariantes,
    completar_ronda_pos,
    correr_mineria,
    crear_cliente,
    dejar_pasar_tiempo,
    esperar_error,
    eventos,
    extender_cadena,
    firmar_tx,
    ids_de,
    minar_bloques,
    normalizar,
    sim_pos,
    sim_pow,
    timestamp_siguiente,
)
from nucleo.errores import Conflicto, ErrorInterno
from nucleo.red import Red

CLAVES_BLOQUE_MINADO = {"numero", "minero", "nonce", "hash", "ronda", "empate", "rechazados"}


def _pasos_hasta(sim, estado_pow: str, max_pasos: int = 2000) -> None:
    """Da pasos hasta que la sesión PoW llegue a `estado_pow`."""
    for _ in range(max_pasos):
        if sim.estado()["pow"]["estado"] == estado_pow:
            return
        sim.paso()
    raise AssertionError(f"La sesión PoW no llegó a {estado_pow}")


# ---------------------------------------------------------------- PoW

def test_ganador_dura_un_paso_y_luego_sigue_el_siguiente_bloque():
    sim = sim_pow()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_mineria(bloques=2, auto_tx=True)
    _pasos_hasta(sim, "ganador")

    datos = sim.estado()
    assert datos["altura_red"] == 1 and datos["pow"]["numero"] == 1
    assert sim.requiere_pasos(), "En 'ganador' el motor debe dar un paso más"
    ganador = datos["pow"]["ultimo_ganador"]
    assert ganador["numero"] == 1 and ganador["minero"] in ids_de(sim)
    assert [b["numero"] for b in datos["pow"]["bloques_minados"]] == [1]
    assert CLAVES_BLOQUE_MINADO <= set(datos["pow"]["bloques_minados"][0])
    error = esperar_error(sim.iniciar_mineria, tipos=Conflicto)   # 409 mientras se muestra al ganador
    assert error.codigo == "mineria_en_curso"

    sim.paso()   # un solo paso: empieza el bloque 2
    siguiente = sim.estado()["pow"]
    assert siguiente["estado"] == "minando" and siguiente["numero"] == 2
    assert siguiente["ultimo_ganador"]["numero"] == 1

    correr_mineria(sim)
    final = sim.estado()
    assert final["pow"]["estado"] == "terminada" and not sim.requiere_pasos()
    assert final["altura_red"] == 2
    assert [b["numero"] for b in final["pow"]["bloques_minados"]] == [1, 2]
    # Una sesión terminada se conserva para mostrarla, pero no impide minar de nuevo.
    sim.crear_transaccion("N03", "N04", 5)
    assert sim.iniciar_mineria()["estado"] == "minando"
    assert_invariantes(sim)


def test_bloques_minados_guarda_solo_los_ultimos_20():
    sim = sim_pow()
    sim.crear_transaccion("N01", "N02", 1)
    sim.iniciar_mineria(bloques=22, auto_tx=True)
    correr_mineria(sim)
    minados = sim.estado()["pow"]["bloques_minados"]
    assert [b["numero"] for b in minados] == list(range(3, 23))
    assert_invariantes(sim)


def test_intervalo_del_motor_en_pow_y_en_pos():
    assert sim_pow(intervalo_ms=50).intervalo_s() == pytest.approx(0.05)
    sim = sim_pos(intervalo_ms=50)
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    assert sim.intervalo_s() == pytest.approx(0.05)           # ronda manual
    sim.pos_automatico(True)
    assert sim.intervalo_s() == pytest.approx(0.8)            # al menos 800 ms para que se vea
    lento = sim_pos(intervalo_ms=1200)
    lento.crear_transaccion("N01", "N02", 5)
    lento.iniciar_ronda_pos()
    lento.pos_automatico(True)
    assert lento.intervalo_s() == pytest.approx(1.2)


# ---------------------------------------------------------------- PoS

def test_rondas_pos_encadenadas_heredan_el_modo_automatico():
    sim = sim_pos()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos(rondas=3, auto_tx=True)
    sim.pos_automatico(True)
    for _ in range(100):
        if sim.estado()["altura_red"] == 1:
            break
        sim.paso()
    ronda = sim.estado()["pos"]
    assert ronda["numero"] == 2 and ronda["estado"] == "APUESTAS"
    assert ronda["automatico"] is True and ronda["rondas_restantes"] == 2
    assert sim.estado()["pos"]["ultimo_bloque"]["numero"] == 1
    correr_mineria(sim)
    final = sim.estado()
    assert final["altura_red"] == 3 and final["pos"]["estado"] == "ACEPTADO"
    assert final["pos"]["rondas_restantes"] == 1 and not sim.requiere_pasos()
    assert_invariantes(sim)


# ---------------------------------------------------------------- cadena recibida por HTTP

def _extendida(sim, monto: int = 1) -> list[dict]:
    cadena = sim.cadena_nodo("N01")
    return extender_cadena(cadena, [firmar_tx(SEMILLA, "N10", "N09", monto, timestamp_siguiente(cadena))])


def test_cadena_recibida_con_bloque_del_futuro_se_rechaza():
    sim = sim_pow()
    minar_bloques(sim, 1)
    extendida = _extendida(sim)   # su bloque va 10 s después del último: el reloj no ha llegado ahí
    resultado = sim.recibir_cadena_externa("N02", extendida)
    assert resultado["acepto"] is False
    assert "futuro" in normalizar(resultado["motivo"])
    assert all(n["altura"] == 1 for n in sim.estado()["nodos"])
    dejar_pasar_tiempo(sim)
    assert sim.recibir_cadena_externa("N02", extendida)["acepto"] is True
    assert_invariantes(sim)


def test_cadena_recibida_se_difunde_y_reinicia_la_mineria():
    sim = sim_pow()
    minar_bloques(sim, 2)
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_mineria()
    assert sim.estado()["pow"]["numero"] == 3
    dejar_pasar_tiempo(sim)
    extendida = _extendida(sim)   # alguien de fuera ya tiene el bloque 3
    desde = sim.bitacora.ultimo

    resultado = sim.recibir_cadena_externa("N04", extendida)
    assert resultado["acepto"] is True
    assert all(r["acepto"] for r in resultado["resultados"]), resultado["resultados"]
    datos = sim.estado()
    assert datos["sincronizados"] is True and datos["altura_red"] == 3
    assert datos["pow"]["estado"] == "minando" and datos["pow"]["numero"] == 4, "La minería no se reinició"
    assert any("reinicio" in normalizar(e["mensaje"]) for e in eventos(sim, "mineria", desde))
    correr_mineria(sim)
    assert sim.estado()["altura_red"] == 4
    assert_invariantes(sim)


def test_cadena_recibida_cancela_la_ronda_pos_en_curso():
    otra = sim_pos()
    otra.crear_transaccion("N03", "N04", 7)
    otra.iniciar_ronda_pos()
    assert completar_ronda_pos(otra) == "ACEPTADO"
    cadena_otra = otra.cadena_nodo("N01")   # mismo génesis (misma configuración)

    sim = sim_pos()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    sim.avanzar_pos()   # SORTEO
    assert sim.estado()["nodos"][0]["apuesta_bloqueada"] > 0
    dejar_pasar_tiempo(sim)
    desde = sim.bitacora.ultimo

    resultado = sim.recibir_cadena_externa("N05", cadena_otra)
    assert resultado["acepto"] is True
    datos = sim.estado()
    assert datos["pos"]["estado"] == "CANCELADA"
    assert datos["altura_red"] == 1 and datos["sincronizados"] is True
    assert all(n["apuesta_bloqueada"] == 0 for n in datos["nodos"])
    assert any("cancelada" in normalizar(e["mensaje"]) for e in eventos(sim, "pos_estado", desde))
    # La transacción pendiente sigue siendo válida y se puede proponer sobre la nueva punta.
    assert len(datos["pendientes"]) == 1
    sim.iniciar_ronda_pos()
    assert completar_ronda_pos(sim) == "ACEPTADO"
    assert sim.estado()["altura_red"] == 2
    assert_invariantes(sim)


# ---------------------------------------------------------------- bitácora y atomicidad

def test_registrar_rechazo_anota_y_nunca_lanza():
    sim = sim_pow()
    sim.registrar_rechazo("POST /api/pow/minar", "El número de bloques debe estar entre 1 y 50")
    ultimo = eventos(sim, "entrada_rechazada")[-1]
    assert "POST /api/pow/minar" in ultimo["mensaje"]
    for ruta, mensaje in ((None, None), (object(), 5), ("\x00\ud800" * 500, "\x1b[31m" * 500)):
        sim.registrar_rechazo(ruta, mensaje)
    assert len(eventos(sim, "entrada_rechazada")) == 4
    assert_invariantes(sim)


def test_falla_imprevista_en_un_paso_revierte_y_detiene_la_mineria(monkeypatch):
    sim = sim_pow()
    sim.crear_transaccion("N01", "N02", 5)
    pendientes = sim.estado()["pendientes"]
    sim.iniciar_mineria()

    def falla(self, ids):
        raise RuntimeError("falla simulada")

    monkeypatch.setattr(Red, "quitar_pendientes", falla)   # justo después de que la red agrega el bloque
    with pytest.raises(ErrorInterno):
        for _ in range(1000):
            sim.paso()
    datos = sim.estado()
    assert datos["altura_red"] == 0 and all(n["altura"] == 0 for n in datos["nodos"]), "El bloque quedó a medias"
    assert datos["pendientes"] == pendientes
    assert datos["pow"]["estado"] == "cancelada" and not sim.requiere_pasos()
    assert any("falla simulada" in e["mensaje"] for e in eventos(sim, "error"))
    assert datos["invariantes_ok"] is True

    monkeypatch.undo()
    sim.iniciar_mineria()
    correr_mineria(sim)
    assert sim.estado()["altura_red"] == 1
    assert_invariantes(sim)


# ---------------------------------------------------------------- estado()

def test_estado_no_cambia_nada_y_recalcula_invariantes_solo_si_cambia_la_version(monkeypatch):
    sim = sim_pow()
    minar_bloques(sim, 2)
    llamadas = []
    original = sim._problemas_invariantes
    monkeypatch.setattr(sim, "_problemas_invariantes", lambda: llamadas.append(1) or original())
    reloj, version = sim.reloj.actual(), sim.version
    sim.estado()
    sim.estado()
    assert len(llamadas) <= 1, "Sin cambios, las invariantes no deben recalcularse"
    assert sim.reloj.actual() == reloj and sim.version == version
    antes = len(llamadas)
    sim.crear_transaccion("N01", "N02", 1)
    assert sim.estado()["invariantes_ok"] is True
    sim.estado()
    assert len(llamadas) == antes + 1


def test_estado_es_rapido_con_20_nodos():
    sim = sim_pow(num_nodos=20)
    sim.crear_transaccion("N01", "N02", 1)
    sim.iniciar_mineria(bloques=30, auto_tx=True)
    correr_mineria(sim)
    assert sim.estado()["altura_red"] == 30
    tiempos = []
    for _ in range(20):
        inicio = time.perf_counter()
        sim.estado()
        tiempos.append(time.perf_counter() - inicio)
    assert statistics.median(tiempos) < 0.02, f"estado() tarda {statistics.median(tiempos) * 1000:.1f} ms"


# ---------------------------------------------------------------- API

def test_api_rechazos_quedan_en_la_bitacora_y_estado_trae_lo_de_pow():
    cliente = crear_cliente()
    assert cliente.post("/api/pow/minar", json={"bloques": "muchos"}).status_code == 400
    assert cliente.get("/api/no-existe").status_code == 404
    assert cliente.post("/api/transacciones", json={"emisor": "N01", "receptor": "N01", "monto": 5}).status_code == 400
    datos = cliente.get("/api/estado").get_json()["datos"]
    tipos = [e["tipo"] for e in datos["eventos"]]
    assert tipos.count("entrada_rechazada") == 2           # /pow/minar y la ruta inexistente
    assert tipos.count("transaccion_rechazada") == 1       # el simulador ya la anotó: no se repite
    assert datos["pow"] == {"estado": "inactivo", "ultimo_ganador": None, "bloques_minados": []}
    assert datos["pos"]["estado"] == "INACTIVA"
