"""Pruebas de aceptación de la sección 5 de la guía, a nivel de Simulador.

Cada caso de la tabla de docs/DISENO.md §20 tiene al menos una prueba cuyo
nombre empieza con su código (test_e1_..., test_w1_..., test_s3_...). Se
escribieron antes del código: sólo usan el comportamiento observable del
contrato (excepciones y su código, estado(), cadena_nodo(), detalle_nodo(),
la bitácora y las funciones puras de reglas/pow).
"""

from __future__ import annotations

import copy
import math

import pytest

from conftest import (
    CONFIG_POW,
    CONFIRMACIONES_POW,
    HASH_CERO,
    INTENTOS_POR_RONDA,
    N_NODOS,
    RECOMPENSA,
    SALDO_INICIAL,
    SEMILLA,
    alterar_hex,
    assert_disponible_coherente,
    assert_invariantes,
    avanzar_hasta,
    clave_publica_hex,
    completar_ronda_pos,
    correr_mineria,
    en_paralelo,
    esperar_error,
    estado_pos,
    estado_pow,
    eventos,
    extender_cadena,
    firma_valida,
    firmar_tx,
    hash_guia,
    ids_de,
    minar_bloques,
    nodo,
    nodo_de,
    normalizar,
    saldos_por_cadena,
    serializar_guia,
    sim_pos,
    sim_pow,
    sortear_guia,
    texto_evento,
    timestamp_siguiente,
)
from nucleo import reglas
from nucleo.config import Config, validar_config
from nucleo.errores import Conflicto, EntradaInvalida, NoEncontrado
from nucleo.pow import SesionPow, elegir_ganador, ronda_pow
from nucleo.simulador import Simulador

IDS_10 = [f"N{i:02d}" for i in range(1, N_NODOS + 1)]


# ---------------------------------------------------------------------------
# Ayudas locales
# ---------------------------------------------------------------------------

def _rechaza_cadena(sim: Simulador, id_nodo: str, cadena) -> str:
    """El nodo debe rechazar la cadena (acepto False o EntradaInvalida) sin cambiar la suya."""
    antes = sim.cadena_nodo(id_nodo)
    try:
        resultado = sim.recibir_cadena_externa(id_nodo, cadena)
    except EntradaInvalida as error:
        motivo = error.mensaje
    else:
        assert resultado["acepto"] is False, f"Se aceptó una cadena inválida: {resultado}"
        motivo = resultado["motivo"]
    assert isinstance(motivo, str) and motivo.strip(), "El rechazo no explica el motivo"
    assert sim.cadena_nodo(id_nodo) == antes, "La copia del nodo cambió pese al rechazo"
    assert_invariantes(sim)
    return normalizar(motivo)


def _proponente_esperado(sim: Simulador, apuestas: dict[str, int], intento: int = 0) -> str:
    """Quién sale sorteado para el siguiente bloque con estas apuestas (fórmula de la guía)."""
    cadena = sim.cadena_nodo("N01")
    validadores = [{"id": i, "apuesta": apuestas[i]} for i in sorted(apuestas)]
    return sortear_guia(validadores, cadena[-1]["hash"], len(cadena), intento)


def _hallazgos_de_referencia(bloque: dict, ids: list[str], k: int, dificultad: int, recompensa: int):
    """Repite la minería por rondas del contrato (§14) a partir del bloque ganador.

    Cada minero i prueba los nonces i, i+N, i+2N, ... (k por ronda) sobre su
    propio candidato (proponente y recompensa = él mismo) y se detiene en su
    primer hallazgo. Devuelve (ronda, hallazgos) de la primera ronda con hallazgos.
    """
    n = len(ids)
    for ronda in range(200):
        hallazgos = []
        for indice, id_minero in enumerate(ids):
            candidato = {
                **bloque,
                "proponente": id_minero,
                "recompensa": {"beneficiario": id_minero, "monto": recompensa},
            }
            for j in range(ronda * k, (ronda + 1) * k):
                nonce = indice + n * j
                calculado = hash_guia({**candidato, "nonce": nonce})
                if calculado.startswith("0" * dificultad):
                    hallazgos.append({"minero": id_minero, "indice": indice, "nonce": nonce, "hash": calculado})
                    break
        if hallazgos:
            return ronda + 1, hallazgos
    return None, []


def _todos_tramposos_en_pow(trampa: str) -> list[str]:
    """Todos los mineros hacen la misma trampa: cada bloque ganador se rechaza.

    Devuelve los textos de los eventos bloque_rechazado.
    """
    sim = sim_pow(max_rondas=25)
    for id_nodo in ids_de(sim):
        sim.marcar_deshonesto(id_nodo, True, trampa)
    sim.crear_transaccion("N01", "N02", 5)
    inicio = sim.bitacora.ultimo
    sim.iniciar_mineria()
    correr_mineria(sim)
    assert estado_pow(sim) == "agotada"
    estado = sim.estado()
    assert estado["altura_red"] == 0
    assert all(n["altura"] == 0 for n in estado["nodos"]), "Algún nodo agregó un bloque tramposo"
    assert len(estado["pendientes"]) == 1, "La transacción honesta debe seguir pendiente"
    rechazos = [texto_evento(e) for e in eventos(sim, "bloque_rechazado", desde=inicio)]
    assert rechazos, "Debió registrarse al menos un bloque_rechazado"
    assert_invariantes(sim)
    return rechazos


def _cadena_minada(bloques: int = 3) -> Simulador:
    """Simulador PoW con `bloques` bloques minados y todos los nodos sincronizados."""
    sim = sim_pow()
    minar_bloques(sim, bloques)
    assert sim.estado()["sincronizados"]
    return sim


def _ataque_alterar_rechazado(tipo: str, numero: int = 2) -> list[str]:
    """El nodo N01 difunde una copia alterada; nadie la acepta y nada cambia."""
    sim = _cadena_minada(3)
    antes = {i: sim.cadena_nodo(i) for i in ids_de(sim)}
    resultado = sim.ataque_alterar_bloque("N01", numero, tipo)
    assert resultado["rechazos"] == N_NODOS - 1
    assert len(resultado["resultados"]) == N_NODOS - 1
    assert all(r["acepto"] is False for r in resultado["resultados"])
    assert {i: sim.cadena_nodo(i) for i in ids_de(sim)} == antes, "Alguna copia de la cadena cambió"
    assert sim.estado()["sincronizados"]
    assert_invariantes(sim)
    return [normalizar(r["motivo"]) for r in resultado["resultados"]]


def _ronda_con_proponente_deshonesto(sim: Simulador, trampa: str = "recompensa_falsa") -> tuple[str, dict]:
    """Prepara una ronda (apuestas de 10) cuyo primer sorteado es tramposo y la lleva a RECHAZADO.

    Devuelve (id del tramposo, castigo registrado).
    """
    ids = ids_de(sim)
    apuestas = {i: 10 for i in ids}
    tramposo = _proponente_esperado(sim, apuestas)
    sim.marcar_deshonesto(tramposo, True, trampa)
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    sim.fijar_apuestas(apuestas)
    inicio = sim.bitacora.ultimo
    avanzar_hasta(sim, "SORTEO")
    assert sim.estado()["pos"]["proponente"] == tramposo
    avanzar_hasta(sim, "VOTACION")
    assert sim.estado()["pos"]["V_favor"] == 0, "Ningún validador debe aprobar un bloque tramposo"
    sim.avanzar_pos()
    estado = sim.estado()
    assert estado["pos"]["estado"] == "RECHAZADO"
    castigos = [c for c in estado["castigos_pendientes"] if c["nodo"] == tramposo]
    assert len(castigos) == 1, f"Debe haber un castigo pendiente para {tramposo}"
    info = nodo_de(estado, tramposo)
    assert info["castigo_pendiente"] == castigos[0]["monto"]
    assert info["validador"]["excluido"] is True
    assert eventos(sim, "castigo", desde=inicio), "Falta el evento castigo"
    assert_disponible_coherente(estado)
    assert_invariantes(sim)
    return tramposo, castigos[0]


# ---------------------------------------------------------------------------
# E — entradas inválidas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("valor", [9, 21, "abc", "", 10.5, True, [], -10, "9", 0])
def test_e1_n_fuera_de_rango(valor):
    esperar_error(Simulador, {**CONFIG_POW, "num_nodos": valor}, tipos=EntradaInvalida)
    esperar_error(validar_config, {"num_nodos": valor}, tipos=EntradaInvalida)


def test_e1_mensaje_indica_el_rango_y_el_valor():
    error = esperar_error(validar_config, {"num_nodos": 25}, tipos=EntradaInvalida)
    mensaje = normalizar(error.mensaje)
    assert "10" in mensaje and "20" in mensaje and "25" in mensaje


@pytest.mark.parametrize("valor, esperado", [(10, 10), (20, 20), ("15", 15), (12.0, 12)])
def test_e1_n_en_los_limites_es_valido(valor, esperado):
    sim = sim_pow(num_nodos=valor)
    estado = sim.estado()
    assert [n["id"] for n in estado["nodos"]] == [f"N{i:02d}" for i in range(1, esperado + 1)]
    assert estado["config"]["num_nodos"] == esperado
    assert_invariantes(sim)


def test_e1_config_por_omision_y_no_objeto():
    assert validar_config(None) == Config()
    assert validar_config({}) == Config()
    assert validar_config({"clave_desconocida": 1, "num_nodos": 12}).num_nodos == 12
    for datos in ([], "texto", 5, [("num_nodos", 10)]):
        esperar_error(validar_config, datos, tipos=EntradaInvalida)
    esperar_error(validar_config, {"num_nodos": 10, "num_validadores": 15}, tipos=EntradaInvalida)


@pytest.mark.parametrize("monto", [-5, "-5", -1, -(10**12)])
def test_e2_monto_negativo(monto):
    sim = sim_pow()
    error = esperar_error(sim.crear_transaccion, "N01", "N02", monto, tipos=EntradaInvalida)
    assert "negativo" in normalizar(error.mensaje)
    assert sim.estado()["pendientes"] == []
    assert_invariantes(sim)


@pytest.mark.parametrize("monto", [0, "0", 0.0, "+0"])
def test_e3_monto_cero(monto):
    sim = sim_pow()
    error = esperar_error(sim.crear_transaccion, "N01", "N02", monto, tipos=EntradaInvalida)
    assert "cero" in normalizar(error.mensaje)
    assert sim.estado()["pendientes"] == []


@pytest.mark.parametrize(
    "monto",
    ["abc", "12abc", "1e3", "5 5", [5], {"monto": 5}, True, False, 3.5, "3.5",
     float("nan"), float("inf"), -float("inf")],
)
def test_e4_monto_no_numerico(monto):
    sim = sim_pow()
    esperar_error(sim.crear_transaccion, "N01", "N02", monto, tipos=EntradaInvalida)
    assert sim.estado()["pendientes"] == []
    assert_invariantes(sim)


@pytest.mark.parametrize("monto", [None, "", "   "])
def test_e5_monto_vacio(monto):
    sim = sim_pow()
    error = esperar_error(sim.crear_transaccion, "N01", "N02", monto, tipos=EntradaInvalida)
    if monto is None or monto == "":
        assert "obligatorio" in normalizar(error.mensaje)
    assert sim.estado()["pendientes"] == []


def test_e5_ids_y_montos_se_normalizan():
    sim = sim_pow()
    sim.crear_transaccion("n1", 2, "5")
    pendiente = sim.estado()["pendientes"][0]
    assert (pendiente["emisor"], pendiente["receptor"], pendiente["monto"]) == ("N01", "N02", 5)
    assert isinstance(pendiente["monto"], int)
    assert_invariantes(sim)


@pytest.mark.parametrize("emisor, receptor", [("N01", "N01"), ("N1", "n01"), ("3", 3), ("N03", "3")])
def test_e6_emisor_igual_a_receptor(emisor, receptor):
    sim = sim_pow()
    esperar_error(sim.crear_transaccion, emisor, receptor, 5, tipos=(EntradaInvalida, Conflicto))
    assert sim.estado()["pendientes"] == []
    assert_invariantes(sim)


@pytest.mark.parametrize(
    "emisor, receptor", [("N99", "N01"), ("N01", "N99"), ("N11", "N01"), ("N00", "N02"), (99, "N02")]
)
def test_e7_nodos_inexistentes_en_transaccion(emisor, receptor):
    sim = sim_pow()
    error = esperar_error(sim.crear_transaccion, emisor, receptor, 5, tipos=NoEncontrado)
    assert error.codigo == "nodo_inexistente"
    assert sim.estado()["pendientes"] == []


def test_e7_nodo_inexistente_en_consultas_y_acciones():
    sim = sim_pow()
    cadena = sim.cadena_nodo("N01")
    for accion in (
        lambda: sim.detalle_nodo("N99"),
        lambda: sim.cadena_nodo("N99"),
        lambda: sim.fijar_conexion("N99", False),
        lambda: sim.marcar_deshonesto("N99", True, "recompensa_falsa"),
        lambda: sim.recibir_cadena_externa("N99", cadena),
        lambda: sim.ataque_alterar_bloque("N99", 0, "hash"),
    ):
        error = esperar_error(accion, tipos=NoEncontrado)
        assert error.codigo == "nodo_inexistente"
    esperar_error(sim.crear_transaccion, "abc", "N01", 5, tipos=(NoEncontrado, EntradaInvalida))
    esperar_error(sim.crear_transaccion, None, "N01", 5, tipos=(NoEncontrado, EntradaInvalida))
    assert isinstance(sim.detalle_nodo("n1"), dict)
    assert_invariantes(sim)


@pytest.mark.parametrize("valor", [0, 7, "x", "", 2.5, True, -1, [1]])
def test_e8_dificultad_fuera_de_rango(valor):
    esperar_error(Simulador, {**CONFIG_POW, "dificultad": valor}, tipos=EntradaInvalida)
    esperar_error(validar_config, {"dificultad": valor}, tipos=EntradaInvalida)


@pytest.mark.parametrize("valor", [1, 6, "3"])
def test_e8_dificultad_valida(valor):
    sim = sim_pow(dificultad=valor)
    assert sim.estado()["config"]["dificultad"] == int(valor)


# ---------------------------------------------------------------------------
# Formato de la cadena (claves, génesis, hashes, firmas)
# ---------------------------------------------------------------------------

def test_genesis_publica_directorio_derivado_de_la_semilla():
    sim = sim_pow()
    genesis = sim.cadena_nodo("N01")[0]
    assert genesis["numero"] == 0 and genesis["hash_anterior"] == HASH_CERO
    assert genesis["proponente"] == "GENESIS" and genesis["recompensa"] is None
    assert genesis["transacciones"] == [] and genesis["firma"] == []
    assert genesis["hash"] == hash_guia(genesis)
    assert sorted(genesis["directorio"]) == IDS_10
    for id_nodo in IDS_10:
        assert genesis["directorio"][id_nodo] == clave_publica_hex(SEMILLA, id_nodo)
    assert genesis["saldos_iniciales"] == {i: SALDO_INICIAL for i in IDS_10}
    assert genesis["parametros"]["confirmaciones"] == CONFIRMACIONES_POW
    assert sim_pos().cadena_nodo("N01")[0]["parametros"]["confirmaciones"] == 0


def test_cadena_minada_cumple_el_formato_de_la_guia():
    sim = _cadena_minada(3)
    cadena = sim.cadena_nodo("N01")
    directorio = cadena[0]["directorio"]
    for anterior, bloque in zip(cadena, cadena[1:]):
        assert bloque["numero"] == anterior["numero"] + 1
        assert bloque["hash_anterior"] == anterior["hash"]
        assert bloque["hash"] == hash_guia(bloque)
        assert bloque["hash"].startswith("0")
        assert bloque["timestamp"] > anterior["timestamp"]
        assert bloque["modo"] == "pow" and bloque["votos"] == [] and bloque["castigos"] == []
        assert bloque["recompensa"] == {"beneficiario": bloque["proponente"], "monto": RECOMPENSA}
        assert len(bloque["firma"]) == len(bloque["transacciones"]) >= 1
        for tx, firma in zip(bloque["transacciones"], bloque["firma"]):
            assert set(tx) == {"emisor", "receptor", "monto", "timestamp"}
            assert firma_valida(directorio[tx["emisor"]], serializar_guia(tx), firma)


def test_cadena_nodo_devuelve_una_copia():
    sim = _cadena_minada(1)
    copia = sim.cadena_nodo("N01")
    copia[0]["saldos_iniciales"]["N01"] = 10**9
    copia[1]["transacciones"][0]["monto"] = 999
    copia.append({"basura": True})
    assert sim.cadena_nodo("N01") != copia
    assert len(sim.cadena_nodo("N01")) == 2
    assert_invariantes(sim)


# ---------------------------------------------------------------------------
# T — transacciones
# ---------------------------------------------------------------------------

def test_transaccion_firmada_valida_se_acepta():
    sim = sim_pow()
    genesis = sim.cadena_nodo("N01")[0]
    tx = firmar_tx(SEMILLA, "N01", "N02", 7, genesis["timestamp"])
    sim.enviar_transaccion_firmada(tx)
    pendientes = sim.estado()["pendientes"]
    assert [p["id"] for p in pendientes] == [tx["id"]]
    sin_id = firmar_tx(SEMILLA, "N03", "N04", 8, genesis["timestamp"])
    del sin_id["id"]
    sim.enviar_transaccion_firmada(sin_id)
    assert len(sim.estado()["pendientes"]) == 2
    assert_invariantes(sim)


def test_t1_firma_alterada():
    sim = sim_pow()
    genesis = sim.cadena_nodo("N01")[0]
    tx = firmar_tx(SEMILLA, "N01", "N02", 7, genesis["timestamp"])
    tx["firma"] = alterar_hex(tx["firma"])
    error = esperar_error(sim.enviar_transaccion_firmada, tx, tipos=EntradaInvalida)
    assert error.codigo == "firma_invalida"
    assert "firma" in normalizar(error.mensaje)
    assert sim.estado()["pendientes"] == []
    assert_invariantes(sim)


def test_t1_monto_alterado_despues_de_firmar():
    sim = sim_pow()
    genesis = sim.cadena_nodo("N01")[0]
    tx = firmar_tx(SEMILLA, "N01", "N02", 7, genesis["timestamp"])
    del tx["id"]
    tx["monto"] = 70
    error = esperar_error(sim.enviar_transaccion_firmada, tx, tipos=EntradaInvalida)
    assert error.codigo == "firma_invalida"
    assert sim.estado()["pendientes"] == []


def test_t1_ataque_firma_alterada():
    sim = sim_pow()
    resultado = sim.ataque_transaccion("firma_alterada", "N01", "N02", 10)
    assert resultado["aceptadas"] == []
    assert [r["codigo"] for r in resultado["rechazadas"]] == ["firma_invalida"]
    assert sim.estado()["pendientes"] == []
    assert_invariantes(sim)


def test_t1_bloque_con_firma_alterada_se_rechaza():
    sim = _cadena_minada(2)
    cadena = sim.cadena_nodo("N01")
    tx = firmar_tx(SEMILLA, "N10", "N09", 1, timestamp_siguiente(cadena))
    tx["firma"] = alterar_hex(tx["firma"])
    motivo = _rechaza_cadena(sim, "N02", extender_cadena(cadena, [tx]))
    assert "firma" in motivo


def test_t1_mineros_con_firma_alterada_son_rechazados():
    rechazos = _todos_tramposos_en_pow("firma_alterada")
    assert any("firma" in r for r in rechazos)


def test_t2_transaccion_firmada_por_otra_clave():
    sim = sim_pow()
    genesis = sim.cadena_nodo("N01")[0]
    tx = firmar_tx(SEMILLA, "N01", "N02", 9, genesis["timestamp"], firmante="N03")
    error = esperar_error(sim.enviar_transaccion_firmada, tx, tipos=EntradaInvalida)
    assert error.codigo == "firma_invalida"
    otra_semilla = firmar_tx("otra-semilla", "N01", "N02", 9, genesis["timestamp"])
    error = esperar_error(sim.enviar_transaccion_firmada, otra_semilla, tipos=EntradaInvalida)
    assert error.codigo == "firma_invalida"
    assert sim.estado()["pendientes"] == []


def test_t2_ataque_otra_clave():
    sim = sim_pow()
    for firmante in ("N03", None):
        resultado = sim.ataque_transaccion("otra_clave", "N01", "N02", 9, firmante=firmante)
        assert resultado["aceptadas"] == []
        assert [r["codigo"] for r in resultado["rechazadas"]] == ["firma_invalida"]
    assert sim.estado()["pendientes"] == []
    assert_invariantes(sim)


def test_t3_saldo_insuficiente():
    sim = sim_pow()
    error = esperar_error(sim.crear_transaccion, "N01", "N02", SALDO_INICIAL + 1, tipos=EntradaInvalida)
    assert error.codigo == "saldo_insuficiente"
    assert "saldo" in normalizar(error.mensaje)
    assert sim.estado()["pendientes"] == []


def test_t3_las_pendientes_comprometen_el_saldo():
    sim = sim_pow()
    sim.crear_transaccion("N01", "N02", 60)
    error = esperar_error(sim.crear_transaccion, "N01", "N03", 50, tipos=EntradaInvalida)
    assert error.codigo == "saldo_insuficiente"
    sim.crear_transaccion("N01", "N03", 40)
    info = nodo(sim, "N01")
    assert info["pendiente_salida"] == 100 and info["disponible"] == 0
    assert_disponible_coherente(sim.estado())
    genesis = sim.cadena_nodo("N01")[0]
    firmada = firmar_tx(SEMILLA, "N01", "N04", 1, genesis["timestamp"])
    error = esperar_error(sim.enviar_transaccion_firmada, firmada, tipos=EntradaInvalida)
    assert error.codigo == "saldo_insuficiente"
    assert_invariantes(sim)


def test_t3_mineros_con_gasto_excesivo_son_rechazados():
    rechazos = _todos_tramposos_en_pow("gasto_excesivo")
    assert any("saldo" in r for r in rechazos)


def test_t4_doble_gasto_en_pendientes():
    sim = sim_pow()
    resultado = sim.ataque_transaccion("doble_gasto", "N01", "N02", 60)
    assert len(resultado["aceptadas"]) == 1
    assert len(resultado["rechazadas"]) == 1
    assert resultado["rechazadas"][0]["codigo"] in ("saldo_insuficiente", "doble_gasto")
    sim.iniciar_mineria()
    correr_mineria(sim)
    bloque = sim.cadena_nodo("N01")[1]
    assert [(t["emisor"], t["monto"]) for t in bloque["transacciones"]] == [("N01", 60)]
    assert nodo(sim, "N01")["saldo_cadena"] == SALDO_INICIAL - 60
    assert_invariantes(sim)


def test_t4_bloque_con_doble_gasto_se_rechaza():
    sim = _cadena_minada(1)
    cadena = sim.cadena_nodo("N01")
    ts = timestamp_siguiente(cadena)
    primera = firmar_tx(SEMILLA, "N10", "N09", 60, ts)
    segunda = firmar_tx(SEMILLA, "N10", "N08", 60, ts + 1)
    motivo = _rechaza_cadena(sim, "N02", extender_cadena(cadena, [primera, segunda]))
    assert "saldo" in motivo or "doble" in motivo


def test_t4_mineros_con_doble_gasto_son_rechazados():
    rechazos = _todos_tramposos_en_pow("doble_gasto")
    assert any("saldo" in r or "doble" in r for r in rechazos)


def test_t5_transaccion_ya_registrada_se_rechaza():
    sim = _cadena_minada(1)
    bloque = sim.cadena_nodo("N01")[1]
    repetida = {**bloque["transacciones"][0], "firma": bloque["firma"][0]}
    error = esperar_error(sim.enviar_transaccion_firmada, repetida, tipos=Conflicto)
    assert error.codigo == "doble_gasto"
    assert "bloque" in normalizar(error.mensaje)
    assert sim.estado()["pendientes"] == []
    assert_invariantes(sim)


def test_t5_transaccion_pendiente_repetida_se_rechaza():
    sim = sim_pow()
    genesis = sim.cadena_nodo("N01")[0]
    tx = firmar_tx(SEMILLA, "N01", "N02", 3, genesis["timestamp"])
    sim.enviar_transaccion_firmada(tx)
    error = esperar_error(sim.enviar_transaccion_firmada, dict(tx), tipos=Conflicto)
    assert error.codigo == "doble_gasto"
    assert len(sim.estado()["pendientes"]) == 1


def test_t5_ataque_transaccion_repetida():
    sim = _cadena_minada(1)
    resultado = sim.ataque_transaccion("repetida", "N01", "N02", 1)
    assert resultado["aceptadas"] == []
    assert [r["codigo"] for r in resultado["rechazadas"]] == ["doble_gasto"]
    assert_invariantes(sim)


def test_t5_bloque_que_repite_una_transaccion_se_rechaza():
    sim = _cadena_minada(2)
    cadena = sim.cadena_nodo("N01")
    bloque_1 = cadena[1]
    repetida = {**bloque_1["transacciones"][0], "firma": bloque_1["firma"][0]}
    motivo = _rechaza_cadena(sim, "N02", extender_cadena(cadena, [repetida]))
    assert "repetida" in motivo or "doble" in motivo


def test_t6_minar_sin_pendientes():
    sim = sim_pow()
    error = esperar_error(sim.iniciar_mineria, tipos=Conflicto)
    assert error.codigo == "sin_pendientes"
    assert estado_pow(sim) == "inactivo"
    assert not sim.requiere_pasos()
    assert_invariantes(sim)


def test_t7_proponer_sin_pendientes():
    sim = sim_pos()
    error = esperar_error(sim.iniciar_ronda_pos, tipos=Conflicto)
    assert error.codigo == "sin_pendientes"
    assert estado_pos(sim) == "INACTIVA"
    assert_invariantes(sim)


def test_generar_transacciones_aleatorias():
    sim = sim_pow()
    sim.generar_transacciones(3)
    assert len(sim.estado()["pendientes"]) == 3
    for cantidad in (0, 51, "x", None, 2.5, True):
        esperar_error(sim.generar_transacciones, cantidad, tipos=EntradaInvalida)
    assert len(sim.estado()["pendientes"]) == 3
    assert_invariantes(sim)


# ---------------------------------------------------------------------------
# C — cadenas recibidas
# ---------------------------------------------------------------------------

def test_c1_bloque_con_hash_alterado():
    motivos = _ataque_alterar_rechazado("hash")
    assert all("hash" in m for m in motivos)


def test_c1_cadena_mas_larga_con_hash_alterado():
    sim = _cadena_minada(3)
    cadena = sim.cadena_nodo("N01")
    extendida = extender_cadena(cadena, [firmar_tx(SEMILLA, "N10", "N09", 1, timestamp_siguiente(cadena))])
    extendida[2]["hash"] = "f" * 64
    motivo = _rechaza_cadena(sim, "N02", extendida)
    assert "hash" in motivo and "bloque 2" in motivo


def test_c2_bloque_con_hash_anterior_alterado():
    motivos = _ataque_alterar_rechazado("hash_anterior")
    assert all("hash" in m for m in motivos)


def test_c2_cadena_mas_larga_con_hash_anterior_alterado():
    sim = _cadena_minada(3)
    cadena = sim.cadena_nodo("N01")
    extendida = extender_cadena(cadena, [firmar_tx(SEMILLA, "N10", "N09", 1, timestamp_siguiente(cadena))])
    extendida[2]["hash_anterior"] = "a" * 64
    motivo = _rechaza_cadena(sim, "N02", extendida)
    assert "hash_anterior" in motivo and "bloque 2" in motivo


def test_c3_cadena_recibida_mas_corta():
    sim = _cadena_minada(3)
    resultado = sim.ataque_cadena_corta("N01", 1)
    assert resultado["rechazos"] == N_NODOS - 1
    assert all(r["acepto"] is False and "larga" in normalizar(r["motivo"]) for r in resultado["resultados"])
    cadena = sim.cadena_nodo("N01")
    assert "larga" in _rechaza_cadena(sim, "N02", cadena[:-1])
    assert "larga" in _rechaza_cadena(sim, "N02", cadena)
    assert all(n["altura"] == 3 for n in sim.estado()["nodos"])


@pytest.mark.parametrize("quitar", [0, 4, -1, "x", 1.5, [1]])
def test_c3_cadena_corta_con_parametro_invalido(quitar):
    sim = _cadena_minada(3)
    esperar_error(sim.ataque_cadena_corta, "N01", quitar, tipos=EntradaInvalida)
    assert_invariantes(sim)


def test_c4_cadena_recibida_invalida():
    sim = _cadena_minada(2)
    cadena = sim.cadena_nodo("N01")
    genesis = cadena[0]
    profunda: list = []
    for _ in range(900):
        profunda = [profunda]
    bloque_vacio = {campo: None for campo in cadena[1]}
    malformadas = {
        "none": None,
        "texto": "texto",
        "numero": 5,
        "objeto": {},
        "lista_vacia": [],
        "lista_con_none": [None],
        "lista_con_objeto_vacio": [{}],
        "lista_anidada": [[]],
        "anidamiento_profundo": profunda,
        "bloque_incompleto": [genesis, {"numero": 1}],
        "bloque_con_nulos": [genesis, bloque_vacio, bloque_vacio, bloque_vacio],
        "bloque_tipos_raros": [genesis, {**cadena[1], "numero": True, "transacciones": profunda}] + cadena[2:] + [{}],
        "genesis_repetido": [genesis, genesis, genesis, genesis],
        "genesis_con_clave_extra": [{**genesis, "extra": 1}] + cadena[1:],
        "mucha_basura": [genesis] + [{}] * 5000,
        "bloque_con_clave_extra": cadena[:2] + [{**cadena[2], "extra": 1}, {}],
    }
    for nombre, mala in malformadas.items():
        try:
            _rechaza_cadena(sim, "N02", mala)
        except AssertionError as error:
            raise AssertionError(f"Caso {nombre}: {error}") from error
    assert sim.estado()["sincronizados"]


def test_c4_cadena_con_genesis_distinto():
    sim = _cadena_minada(2)
    cadena = sim.cadena_nodo("N01")
    alterada = copy.deepcopy(cadena)
    alterada[0]["saldos_iniciales"]["N01"] = 10**6
    alterada[0]["hash"] = hash_guia(alterada[0])
    alterada = extender_cadena(alterada, [firmar_tx(SEMILLA, "N10", "N09", 1, timestamp_siguiente(alterada))])
    assert "genesis" in _rechaza_cadena(sim, "N02", alterada)
    otra_red = sim_pow(semilla="otra-red").cadena_nodo("N01")
    for monto in (1, 2, 3):
        siguiente = [firmar_tx("otra-red", "N10", "N09", monto, timestamp_siguiente(otra_red))]
        otra_red = extender_cadena(otra_red, siguiente)
    assert "genesis" in _rechaza_cadena(sim, "N02", otra_red)


def test_c4_cadena_valida_mas_larga_se_acepta_y_queda_aislada():
    sim = _cadena_minada(2)
    cadena = sim.cadena_nodo("N01")
    extendida = extender_cadena(cadena, [firmar_tx(SEMILLA, "N10", "N09", 1, timestamp_siguiente(cadena))])
    resultado = sim.recibir_cadena_externa("N03", extendida)
    assert resultado["acepto"] is True, resultado
    assert nodo(sim, "N03")["altura"] == 3
    extendida[1]["transacciones"][0]["monto"] = 999
    extendida.append({})
    assert len(sim.cadena_nodo("N03")) == 4
    assert sim.cadena_nodo("N03")[1] == cadena[1], "El nodo guardó una referencia a la lista recibida"
    assert sim.estado()["sincronizados"] is False
    assert_invariantes(sim)


@pytest.mark.parametrize("tipo", ["monto", "monto_rehash"])
def test_c5_bloque_intermedio_manipulado_por_ataque(tipo):
    motivos = _ataque_alterar_rechazado(tipo, numero=2)
    assert all("bloque 2" in m for m in motivos)


@pytest.mark.parametrize("rehash", [False, True])
def test_c5_bloque_intermedio_manipulado_en_cadena_mas_larga(rehash):
    sim = _cadena_minada(3)
    cadena = sim.cadena_nodo("N01")
    extendida = extender_cadena(cadena, [firmar_tx(SEMILLA, "N10", "N09", 1, timestamp_siguiente(cadena))])
    manipulada = copy.deepcopy(extendida)
    manipulada[2]["transacciones"][0]["monto"] += 1000
    if rehash:
        manipulada[2]["hash"] = hash_guia(manipulada[2])
    motivo = _rechaza_cadena(sim, "N02", manipulada)
    assert "bloque 2" in motivo
    assert nodo(sim, "N02")["altura"] == 3
    # Control: la misma cadena sin manipular sí se acepta.
    assert sim.recibir_cadena_externa("N03", extendida)["acepto"] is True
    assert_invariantes(sim)


def test_ataque_alterar_bloque_parametros_invalidos():
    sim = _cadena_minada(2)
    for numero in (3, -1, "x", None, 1.5):
        esperar_error(sim.ataque_alterar_bloque, "N01", numero, "hash", tipos=EntradaInvalida)
    for tipo in ("inventado", "", None, 5):
        esperar_error(sim.ataque_alterar_bloque, "N01", 1, tipo, tipos=EntradaInvalida)
    resultado = sim.ataque_alterar_bloque("N01", 0, "hash")
    assert resultado["rechazos"] == N_NODOS - 1
    assert_invariantes(sim)


# ---------------------------------------------------------------------------
# W — minería PoW
# ---------------------------------------------------------------------------

def test_w1_elegir_ganador_con_hallazgos_fabricados():
    h1 = {"minero": "N04", "indice": 3, "nonce": 13, "hash": "0f" + "1" * 62}
    h2 = {"minero": "N02", "indice": 1, "nonce": 31, "hash": "0a" + "f" * 62}
    h3 = {"minero": "N09", "indice": 8, "nonce": 8, "hash": "0e" + "0" * 62}
    ganador, empatados = elegir_ganador([h1, h2, h3])
    assert ganador["minero"] == "N02"
    assert sorted(h["minero"] for h in empatados) == ["N02", "N04", "N09"]
    ganador, empatados = elegir_ganador([h1])
    assert ganador["minero"] == "N04" and empatados == []
    mismo = "0" + "7" * 63
    ganador, _ = elegir_ganador([
        {"minero": "N05", "indice": 4, "nonce": 4, "hash": mismo},
        {"minero": "N03", "indice": 2, "nonce": 12, "hash": mismo},
    ])
    assert ganador["minero"] == "N03"


def test_w1_dos_ganadores_misma_ronda():
    con_empate = 0
    for i in range(15):
        sim = sim_pow(semilla=f"empate-{i}")
        sim.crear_transaccion("N01", "N02", 5)
        inicio = sim.bitacora.ultimo
        sim.iniciar_mineria()
        correr_mineria(sim)
        bloque = sim.cadena_nodo("N01")[1]
        ronda, hallazgos = _hallazgos_de_referencia(bloque, IDS_10, INTENTOS_POR_RONDA, 1, RECOMPENSA)
        assert hallazgos, "La referencia no reproduce la minería del contrato"
        esperado = min(hallazgos, key=lambda h: (h["hash"], h["indice"]))
        assert (bloque["proponente"], bloque["nonce"], bloque["hash"]) == (
            esperado["minero"], esperado["nonce"], esperado["hash"],
        ), f"Semilla empate-{i}: no ganó el hash menor de la ronda {ronda}"
        empates = eventos(sim, "empate", desde=inicio)
        if len(hallazgos) > 1:
            assert empates, f"Semilla empate-{i}: {len(hallazgos)} hallazgos y ningún evento empate"
            con_empate += 1
        else:
            assert not empates
        assert_invariantes(sim)
        if con_empate >= 2:
            break
    assert con_empate >= 1, "Ninguna semilla produjo un empate; amplíe la búsqueda"


def test_w2_minar_mientras_ya_se_mina():
    sim = sim_pow(dificultad=6)
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_mineria()
    error = esperar_error(sim.iniciar_mineria, tipos=Conflicto)
    assert "minando" in normalizar(error.mensaje)
    esperar_error(sim.iniciar_mineria, 5, True, tipos=Conflicto)
    assert estado_pow(sim) == "minando"
    assert sim.requiere_pasos()
    sim.cancelar_mineria()
    assert_invariantes(sim)


def test_w3_dificultad_imposible_agota_el_limite_de_rondas():
    sim = sim_pow(dificultad=6, intentos_por_ronda=2, max_rondas=5)
    sim.crear_transaccion("N01", "N02", 5)
    inicio = sim.bitacora.ultimo
    sim.iniciar_mineria()
    resultados = correr_mineria(sim, max_pasos=50)
    assert 5 <= len(resultados) <= 6
    assert estado_pow(sim) == "agotada"
    assert not sim.requiere_pasos()
    textos = [texto_evento(e) for e in eventos(sim, desde=inicio)] + [normalizar(r) for r in resultados]
    assert any("limite" in t for t in textos), "Falta el aviso de límite de rondas"
    estado = sim.estado()
    assert estado["altura_red"] == 0 and len(estado["pendientes"]) == 1
    assert_invariantes(sim)
    sim.iniciar_mineria()
    assert estado_pow(sim) == "minando"


def test_w3_cancelar_mineria_que_no_termina():
    sim = sim_pow(dificultad=6, max_rondas=1_000_000)
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_mineria()
    for _ in range(3):
        sim.paso()
    assert estado_pow(sim) == "minando"
    sim.cancelar_mineria()
    assert estado_pow(sim) == "cancelada"
    assert not sim.requiere_pasos()
    estado = sim.estado()
    assert estado["altura_red"] == 0 and len(estado["pendientes"]) == 1
    esperar_error(sim.cancelar_mineria, tipos=Conflicto)
    assert_invariantes(sim)


def test_w3_cancelar_sin_mineria():
    sim = sim_pow()
    esperar_error(sim.cancelar_mineria, tipos=Conflicto)


def test_w4_recompensa_pendiente_hasta_6_confirmaciones():
    sim = sim_pow()
    minar_bloques(sim, 1)
    minero = sim.cadena_nodo("N01")[1]["proponente"]
    inicio = sim.bitacora.ultimo

    info = nodo(sim, minero)
    pendientes_b1 = [r for r in info["recompensas_pendientes"] if r["bloque"] == 1]
    assert [(r["monto"], r["faltan"]) for r in pendientes_b1] == [(RECOMPENSA, CONFIRMACIONES_POW)]
    assert info["total_recompensas_pendientes"] == RECOMPENSA
    saldos, _ = saldos_por_cadena(sim.cadena_nodo("N01"))
    assert info["saldo_cadena"] == saldos[minero], "La recompensa no madura no debe estar en el saldo"
    assert isinstance(sim.detalle_nodo(minero), dict)

    otro = "N01" if minero != "N01" else "N02"
    error = esperar_error(sim.crear_transaccion, minero, otro, info["disponible"] + 1, tipos=EntradaInvalida)
    assert error.codigo == "saldo_insuficiente"
    assert "madur" in normalizar(error.mensaje), "El error debe mencionar la recompensa no madura"

    minar_bloques(sim, CONFIRMACIONES_POW - 1)  # altura 6: falta 1
    info = nodo(sim, minero)
    assert [r["faltan"] for r in info["recompensas_pendientes"] if r["bloque"] == 1] == [1]
    assert not eventos(sim, "recompensa_acreditada", desde=inicio), "Ninguna recompensa madura antes de h+6"

    minar_bloques(sim, 1)  # altura 7 = 1 + 6: madura
    info = nodo(sim, minero)
    assert all(r["bloque"] != 1 for r in info["recompensas_pendientes"])
    saldos, _ = saldos_por_cadena(sim.cadena_nodo("N01"))
    assert info["saldo_cadena"] == saldos[minero]
    assert eventos(sim, "recompensa_acreditada", desde=inicio)
    assert_invariantes(sim)


def test_w4_eventos_de_recompensa_pendiente():
    sim = sim_pow()
    inicio = sim.bitacora.ultimo
    minar_bloques(sim, 1)
    assert eventos(sim, "bloque_minado", desde=inicio)
    assert eventos(sim, "recompensa_pendiente", desde=inicio)


def test_w5_mineros_con_recompensa_falsa_son_rechazados():
    rechazos = _todos_tramposos_en_pow("recompensa_falsa")
    assert any("recompensa" in r for r in rechazos)


def test_w5_minero_honesto_entre_tramposos_gana_con_recompensa_correcta():
    sim = sim_pow()
    for id_nodo in IDS_10[:-1]:
        sim.marcar_deshonesto(id_nodo, True, "recompensa_falsa")
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_mineria()
    correr_mineria(sim)
    cadena = sim.cadena_nodo("N01")
    assert len(cadena) == 2
    assert cadena[1]["proponente"] == "N10"
    assert cadena[1]["recompensa"] == {"beneficiario": "N10", "monto": RECOMPENSA}
    assert sim.estado()["sincronizados"]
    assert_invariantes(sim)


def test_w5_bloque_con_recompensa_falsa_se_rechaza():
    sim = _cadena_minada(1)
    cadena = sim.cadena_nodo("N01")
    falsa = extender_cadena(
        cadena, [firmar_tx(SEMILLA, "N10", "N09", 1, timestamp_siguiente(cadena))], recompensa=RECOMPENSA * 10,
    )
    assert "recompensa" in _rechaza_cadena(sim, "N02", falsa)


def test_w5_ataque_recompensa_alterada():
    _ataque_alterar_rechazado("recompensa", numero=1)


def test_nonces_disjuntos_en_el_simulador():
    sim = sim_pow(dificultad=6)
    sim.fijar_conexion("N03", False)
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_mineria()
    rondas = 3
    for _ in range(rondas):
        sim.paso()
    estado = sim.estado()
    for n in estado["nodos"]:
        minero = n["minero"]
        if n["id"] == "N03":
            sin_minar = minero is None or (minero["intentos"] == 0 and minero["nonce"] == n["indice"])
            assert sin_minar, "Un nodo desconectado no mina"
            continue
        assert minero["intentos"] == rondas * INTENTOS_POR_RONDA
        assert minero["nonce"] == n["indice"] + rondas * INTENTOS_POR_RONDA * N_NODOS
        assert minero["nonce"] % N_NODOS == n["indice"]
    sim.cancelar_mineria()


def test_nonces_disjuntos_en_sesion_pow():
    candidatos = {i: {"numero": 1, "proponente": i, "nonce": 0, "hash": ""} for i in IDS_10}
    sesion = SesionPow(1, candidatos, IDS_10, 7, 6, 10)
    assert [(m.id, m.indice, m.nonce) for m in sesion.mineros] == [(i, n, n) for n, i in enumerate(IDS_10)]
    conectados = set(IDS_10) - {"N05"}
    for _ in range(3):
        assert ronda_pow(sesion, conectados) == []
    assert sesion.ronda == 3
    for minero in sesion.mineros:
        if minero.id == "N05":
            assert minero.intentos == 0 and minero.nonce == minero.indice
        else:
            assert minero.intentos == 21
            assert minero.nonce == minero.indice + 21 * N_NODOS


def test_minar_10_bloques_seguidos_con_auto_tx():
    sim = sim_pow()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_mineria(bloques=10, auto_tx=True)
    assert sim.requiere_pasos()
    correr_mineria(sim, max_pasos=5000)
    estado = sim.estado()
    assert estado["altura_red"] == 10 and estado["sincronizados"]
    assert all(n["altura"] == 10 for n in estado["nodos"])
    cadena = sim.cadena_nodo("N01")
    for anterior, bloque in zip(cadena, cadena[1:]):
        assert bloque["hash_anterior"] == anterior["hash"]
        assert bloque["hash"] == hash_guia(bloque) and bloque["hash"].startswith("0")
        assert 1 <= len(bloque["transacciones"]) <= CONFIG_POW["max_tx_por_bloque"]
    assert estado["circulacion"]["total"] == N_NODOS * SALDO_INICIAL + 10 * RECOMPENSA
    assert_invariantes(sim)


def test_minar_varios_bloques_sin_auto_tx_termina_al_vaciar_pendientes():
    sim = sim_pow()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_mineria(bloques=3)
    correr_mineria(sim)
    assert sim.estado()["altura_red"] == 1
    assert not sim.requiere_pasos()
    assert_invariantes(sim)


@pytest.mark.parametrize("bloques", [0, 51, "x", -1, 2.5, True, [1]])
def test_iniciar_mineria_con_bloques_invalidos(bloques):
    sim = sim_pow()
    sim.crear_transaccion("N01", "N02", 5)
    esperar_error(sim.iniciar_mineria, bloques, tipos=EntradaInvalida)
    assert estado_pow(sim) == "inactivo"


def test_nodo_desconectado_no_mina_y_se_sincroniza_al_reconectar():
    sim = sim_pow()
    sim.fijar_conexion("N05", False)
    minar_bloques(sim, 2)
    estado = sim.estado()
    assert nodo_de(estado, "N05")["altura"] == 0 and nodo_de(estado, "N05")["conectado"] is False
    assert estado["sincronizados"] is False
    assert all(b["proponente"] != "N05" for b in sim.cadena_nodo("N01")[1:])
    sim.fijar_conexion("N05", True)
    estado = sim.estado()
    assert nodo_de(estado, "N05")["altura"] == 2 and estado["sincronizados"]
    assert_invariantes(sim)


def test_modo_equivocado_da_conflicto():
    pow_ = sim_pow()
    pow_.crear_transaccion("N01", "N02", 5)
    for accion in (pow_.iniciar_ronda_pos, pow_.apostar_todo, pow_.avanzar_pos, pow_.cancelar_ronda_pos):
        esperar_error(accion, tipos=Conflicto)
    pos = sim_pos()
    pos.crear_transaccion("N01", "N02", 5)
    for accion in (pos.iniciar_mineria, pos.cancelar_mineria):
        esperar_error(accion, tipos=Conflicto)
    assert_invariantes(pow_)
    assert_invariantes(pos)


def test_marcar_deshonesto_valida_la_trampa():
    sim = sim_pow()
    esperar_error(sim.marcar_deshonesto, "N01", True, tipos=EntradaInvalida)
    esperar_error(sim.marcar_deshonesto, "N01", True, "inventada", tipos=EntradaInvalida)
    esperar_error(sim.marcar_deshonesto, "N01", "quizas", "recompensa_falsa", tipos=EntradaInvalida)
    sim.marcar_deshonesto("n1", True, "Recompensa_Falsa")
    info = nodo(sim, "N01")
    assert info["deshonesto"] is True and info["trampa"] == "recompensa_falsa"
    sim.marcar_deshonesto("N01", False)
    info = nodo(sim, "N01")
    assert info["deshonesto"] is False and info["trampa"] is None


# ---------------------------------------------------------------------------
# S — Proof of Stake
# ---------------------------------------------------------------------------

def test_pos_ronda_honesta_completa_y_verificable():
    sim = sim_pos()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    estado = sim.estado()
    assert estado["pos"]["estado"] == "APUESTAS"
    validadores = [n for n in estado["nodos"] if n["validador"]]
    assert len(validadores) == N_NODOS
    for n in validadores:
        assert 1 <= n["validador"]["apuesta"] <= n["saldo_cadena"]
        assert n["apuesta_bloqueada"] == n["validador"]["apuesta"]
    assert math.isclose(sum(n["validador"]["probabilidad"] for n in validadores), 1.0, abs_tol=1e-6)
    assert_disponible_coherente(estado)

    assert completar_ronda_pos(sim) == "ACEPTADO"
    cadena = sim.cadena_nodo("N01")
    bloque = cadena[1]
    assert bloque["modo"] == "pos" and bloque["nonce"] == 0
    assert bloque["hash"] == hash_guia(bloque)
    ids_validadores = [v["id"] for v in bloque["validadores"]]
    assert ids_validadores == sorted(ids_validadores)
    assert bloque["proponente"] == sortear_guia(bloque["validadores"], bloque["hash_anterior"], 1, bloque["intento"])
    hash_candidato = hash_guia({**bloque, "votos": []})
    directorio = cadena[0]["directorio"]
    apuestas = {v["id"]: v["apuesta"] for v in bloque["validadores"]}
    favor = 0
    for voto in bloque["votos"]:
        texto_voto = "si" if voto["voto"] else "no"
        mensaje = f"voto|{hash_candidato}|{voto['validador']}|{texto_voto}".encode()
        assert firma_valida(directorio[voto["validador"]], mensaje, voto["firma"])
        assert voto["peso"] == apuestas[voto["validador"]]
        favor += voto["peso"] if voto["voto"] else 0
    assert 3 * favor >= 2 * sum(apuestas.values())

    estado = sim.estado()
    assert estado["sincronizados"] and estado["altura_red"] == 1 and estado["pendientes"] == []
    saldos, no_maduras = saldos_por_cadena(cadena)
    assert no_maduras == {}, "En PoS la recompensa se paga al aceptarse"
    for n in estado["nodos"]:
        assert n["saldo_cadena"] == saldos[n["id"]]
        assert n["apuesta_bloqueada"] == 0, "Las apuestas se liberan al aceptar el bloque"
    assert estado["circulacion"]["total"] == N_NODOS * SALDO_INICIAL + RECOMPENSA
    assert_invariantes(sim)


def test_pos_requiere_pasos_solo_en_automatico():
    sim = sim_pos()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    assert not sim.requiere_pasos()
    sim.pos_automatico(True)
    assert sim.requiere_pasos()
    sim.paso()
    assert estado_pos(sim) == "SORTEO"
    sim.pos_automatico(False)
    assert not sim.requiere_pasos()
    sim.pos_automatico(True)
    correr_mineria(sim, max_pasos=100)
    assert estado_pos(sim) == "ACEPTADO"
    assert_invariantes(sim)


def test_pos_varias_rondas_seguidas_con_auto_tx():
    sim = sim_pos()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos(rondas=3, auto_tx=True)
    sim.pos_automatico(True)
    correr_mineria(sim, max_pasos=500)
    if estado_pos(sim) not in ("ACEPTADO", "SIN_VALIDADORES"):
        completar_ronda_pos(sim, max_pasos=200)
    estado = sim.estado()
    assert estado["altura_red"] == 3 and estado["sincronizados"]
    assert_invariantes(sim)


def test_cancelar_ronda_pos_libera_apuestas():
    sim = sim_pos()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    sim.fijar_apuestas({"N03": 40})
    assert nodo(sim, "N03")["disponible"] == SALDO_INICIAL - 40
    sim.cancelar_ronda_pos()
    assert estado_pos(sim) == "CANCELADA"
    info = nodo(sim, "N03")
    assert info["disponible"] == SALDO_INICIAL and info["apuesta_bloqueada"] == 0
    assert len(sim.estado()["pendientes"]) == 1
    esperar_error(sim.cancelar_ronda_pos, tipos=Conflicto)
    sim.iniciar_ronda_pos()
    assert estado_pos(sim) == "APUESTAS"
    assert_invariantes(sim)


def test_s1_ningun_validador_con_saldo():
    sim = sim_pos(saldo_inicial=1)
    sim.crear_transaccion("N01", "N02", 1)
    for id_nodo in IDS_10[1:]:
        sim.fijar_conexion(id_nodo, False)
    error = esperar_error(sim.iniciar_ronda_pos, tipos=Conflicto)
    assert error.codigo == "sin_validadores"
    assert "validador" in normalizar(error.mensaje)
    assert estado_pos(sim) == "INACTIVA"
    assert_invariantes(sim)


@pytest.mark.parametrize("apuesta", [SALDO_INICIAL + 50, 0, -5, "abc", "", None, 2.5, True, [10]])
def test_s2_apuesta_invalida(apuesta):
    sim = sim_pos()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    antes = nodo(sim, "N02")["validador"]["apuesta"]
    error = esperar_error(sim.fijar_apuestas, {"N02": apuesta}, tipos=(EntradaInvalida, Conflicto))
    mensaje = normalizar(error.mensaje)
    if apuesta == SALDO_INICIAL + 50:
        assert "saldo" in mensaje
    if apuesta in (0, -5) and not isinstance(apuesta, bool):
        assert "cero" in mensaje or "negativ" in mensaje
    assert nodo(sim, "N02")["validador"]["apuesta"] == antes
    assert estado_pos(sim) == "APUESTAS"
    assert_invariantes(sim)


def test_s2_apuestas_todo_o_nada_y_normalizacion():
    sim = sim_pos()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    antes = nodo(sim, "N03")["validador"]["apuesta"]
    error = esperar_error(sim.fijar_apuestas, {"N03": 10, "N04": 1000}, tipos=(EntradaInvalida, Conflicto))
    assert "saldo" in normalizar(error.mensaje)
    assert nodo(sim, "N03")["validador"]["apuesta"] == antes, "Una apuesta inválida no debe cambiar ninguna"
    esperar_error(sim.fijar_apuestas, {"N01": SALDO_INICIAL}, tipos=(EntradaInvalida, Conflicto))
    for apuestas in ([1, 2], "texto", None, 5):
        esperar_error(sim.fijar_apuestas, apuestas, tipos=EntradaInvalida)
    esperar_error(sim.fijar_apuestas, {"N99": 5}, tipos=(NoEncontrado, EntradaInvalida, Conflicto))
    sim.fijar_apuestas({"n3": "10", 4: 20})
    estado = sim.estado()
    assert nodo_de(estado, "N03")["validador"]["apuesta"] == 10
    assert nodo_de(estado, "N04")["validador"]["apuesta"] == 20
    assert_disponible_coherente(estado)
    sim.avanzar_pos()
    esperar_error(sim.fijar_apuestas, {"N03": 5}, tipos=Conflicto)
    assert_invariantes(sim)


def test_s2_apuesta_de_quien_no_es_validador():
    sim = sim_pos(seleccion_validadores="aleatorio", num_validadores=3)
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    estado = sim.estado()
    no_validadores = [n["id"] for n in estado["nodos"] if n["validador"] is None]
    assert len(no_validadores) == N_NODOS - 3
    error = esperar_error(sim.fijar_apuestas, {no_validadores[0]: 5}, tipos=(Conflicto, EntradaInvalida))
    assert "validador" in normalizar(error.mensaje)


def test_s3_umbral_de_dos_tercios_regla_pura():
    assert reglas.alcanza_umbral(18, 27) is True
    assert reglas.alcanza_umbral(18, 28) is False
    assert reglas.alcanza_umbral(2, 3) is True
    assert reglas.alcanza_umbral(1, 2) is False
    assert reglas.alcanza_umbral(0, 0) is False
    assert reglas.alcanza_umbral(0, 5) is False


@pytest.mark.parametrize("apuesta_invertido, esperado", [(9, "ACEPTADO"), (10, "RECHAZADO")])
def test_s3_votacion_exactamente_en_dos_tercios(apuesta_invertido, esperado):
    sim = sim_pos()
    ids = ids_de(sim)
    invertido = None
    for candidato in reversed(ids):
        sorteados = set()
        for monto in (9, 10):
            apuestas = {i: 2 for i in ids}
            apuestas[candidato] = monto
            sorteados.add(_proponente_esperado(sim, apuestas))
        if candidato not in sorteados:
            invertido = candidato
            break
    assert invertido is not None
    apuestas = {i: 2 for i in ids}
    apuestas[invertido] = apuesta_invertido
    sim.marcar_deshonesto(invertido, True, "voto_invertido")
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    sim.fijar_apuestas(apuestas)
    avanzar_hasta(sim, "VOTACION")
    pos = sim.estado()["pos"]
    assert pos["proponente"] != invertido
    assert pos["A"] == 18 + apuesta_invertido
    assert pos["V_favor"] == 18
    sim.avanzar_pos()
    assert estado_pos(sim) == esperado
    if esperado == "ACEPTADO":
        bloque = sim.cadena_nodo("N01")[1]
        votos = {v["validador"]: v["voto"] for v in bloque["votos"]}
        assert len(votos) == N_NODOS and votos[invertido] is False
        assert sum(1 for v in votos.values() if v) == N_NODOS - 1
        assert sim.estado()["sincronizados"]
    assert_invariantes(sim)


def test_s4_voto_de_un_nodo_que_no_es_validador():
    sim = sim_pos(seleccion_validadores="aleatorio", num_validadores=3)
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    avanzar_hasta(sim, "VOTACION")
    estado = sim.estado()
    no_validador = next(n["id"] for n in estado["nodos"] if n["validador"] is None)
    favor, contra = estado["pos"]["V_favor"], estado["pos"]["V_contra"]
    error = esperar_error(sim.votar_manual, no_validador, True, tipos=Conflicto)
    assert error.codigo == "voto_invalido"
    pos = sim.estado()["pos"]
    assert (pos["V_favor"], pos["V_contra"]) == (favor, contra)
    assert completar_ronda_pos(sim) == "ACEPTADO"
    bloque = sim.cadena_nodo("N01")[1]
    assert no_validador not in {v["validador"] for v in bloque["votos"]}
    assert len(bloque["validadores"]) == 3
    assert_invariantes(sim)


def test_s5_voto_doble():
    sim = sim_pos()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    esperar_error(sim.votar_manual, "N03", True, tipos=Conflicto)  # aún no es la votación
    avanzar_hasta(sim, "VOTACION")
    pos = sim.estado()["pos"]
    favor, contra = pos["V_favor"], pos["V_contra"]
    error = esperar_error(sim.votar_manual, "N03", False, tipos=Conflicto)
    assert error.codigo == "voto_duplicado"
    error = esperar_error(sim.votar_manual, "n3", True, tipos=Conflicto)
    assert error.codigo == "voto_duplicado"
    esperar_error(sim.votar_manual, "N03", "quizas", tipos=EntradaInvalida)
    esperar_error(sim.votar_manual, "N99", True, tipos=NoEncontrado)
    pos = sim.estado()["pos"]
    assert (pos["V_favor"], pos["V_contra"]) == (favor, contra)
    assert completar_ronda_pos(sim) == "ACEPTADO"
    bloque = sim.cadena_nodo("N01")[1]
    nombres = [v["validador"] for v in bloque["votos"]]
    assert len(nombres) == len(set(nombres)), "Un validador votó dos veces"
    assert_invariantes(sim)


def test_s6_calculo_del_castigo_reglas_a_y_b():
    assert reglas.calcular_castigo("A", 10, 5, 50) == 10
    assert reglas.calcular_castigo("B", 10, 5, 50) == 3
    assert reglas.calcular_castigo("B", 10, 0, 50) == 1
    assert reglas.calcular_castigo("B", 2, 100, 50) == 2
    assert reglas.calcular_castigo("B", 100, 7, 100) == 7


def test_s6_proponente_deshonesto_castigado_y_nuevo_sorteo():
    sim = sim_pos()
    tramposo, castigo = _ronda_con_proponente_deshonesto(sim)
    assert (castigo["monto"], castigo["apuesta"], castigo["regla"]) == (10, 10, "A")
    assert (castigo["numero"], castigo["intento"]) == (1, 0)

    sim.avanzar_pos()
    pos = sim.estado()["pos"]
    assert pos["estado"] == "SORTEO" and pos["intento"] == 1
    restantes = {i: 10 for i in IDS_10 if i != tramposo}
    assert pos["proponente"] == _proponente_esperado(sim, restantes, intento=1) != tramposo

    assert completar_ronda_pos(sim) == "ACEPTADO"
    cadena = sim.cadena_nodo("N01")
    bloque = cadena[1]
    assert bloque["proponente"] != tramposo and bloque["intento"] == 1
    assert [c["nodo"] for c in bloque["castigos"]] == [tramposo]
    assert tramposo not in {v["id"] for v in bloque["validadores"]}

    estado = sim.estado()
    assert estado["castigos_pendientes"] == []
    assert estado["circulacion"]["quemado"] == 10
    assert estado["circulacion"]["total"] == N_NODOS * SALDO_INICIAL + RECOMPENSA - 10
    saldos, _ = saldos_por_cadena(cadena)
    for n in estado["nodos"]:
        assert n["saldo_cadena"] == saldos[n["id"]]
    assert nodo_de(estado, tramposo)["castigo_pendiente"] == 0
    assert_disponible_coherente(estado)
    assert_invariantes(sim)


def test_s6_castigo_con_regla_b():
    sim = sim_pos(regla_castigo="B", alfa_porcentaje=50)
    _, castigo = _ronda_con_proponente_deshonesto(sim)
    assert castigo["regla"] == "B" and castigo["apuesta"] == 10
    valor = castigo["valor_transacciones"]
    assert castigo["monto"] == min(10, max(1, -(-50 * valor // 100)))
    assert completar_ronda_pos(sim) == "ACEPTADO"
    assert sim.estado()["circulacion"]["quemado"] == castigo["monto"]
    assert_invariantes(sim)


def test_s7_todos_castigados_hasta_quedar_sin_saldo():
    sim = sim_pos()
    for id_nodo in IDS_10:
        sim.marcar_deshonesto(id_nodo, True, "firma_alterada")
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    sim.apostar_todo()
    estado = sim.estado()
    for n in estado["nodos"]:
        assert n["validador"]["apuesta"] == (SALDO_INICIAL - 5 if n["id"] == "N01" else SALDO_INICIAL)
        assert n["disponible"] == 0

    assert completar_ronda_pos(sim, max_pasos=200) == "SIN_VALIDADORES"
    estado = sim.estado()
    assert estado["altura_red"] == 0
    assert all(n["disponible"] == 0 for n in estado["nodos"])
    assert sorted(c["nodo"] for c in estado["castigos_pendientes"]) == IDS_10
    assert sum(c["monto"] for c in estado["castigos_pendientes"]) == N_NODOS * SALDO_INICIAL - 5
    assert len(estado["pendientes"]) == 1
    assert_disponible_coherente(estado)

    error = esperar_error(sim.iniciar_ronda_pos, tipos=Conflicto)
    assert error.codigo == "sin_validadores"
    error = esperar_error(sim.crear_transaccion, "N03", "N04", 1, tipos=EntradaInvalida)
    assert error.codigo == "saldo_insuficiente"
    assert_invariantes(sim)


def test_sorteo_reproducible_y_formula_de_la_guia():
    validadores = [{"id": "N01", "apuesta": 10}, {"id": "N02", "apuesta": 30}, {"id": "N03", "apuesta": 60}]
    hash_anterior = "ab" * 32
    for intento in range(50):
        esperado = sortear_guia(validadores, hash_anterior, 7, intento)
        assert reglas.sortear(validadores, hash_anterior, 7, intento) == esperado
        assert reglas.sortear(copy.deepcopy(validadores), hash_anterior, 7, intento) == esperado
    probabilidades = reglas.probabilidades(validadores)
    assert probabilidades == pytest.approx({"N01": 0.1, "N02": 0.3, "N03": 0.6})
    for invalidos in ([], [{"id": "N01", "apuesta": 0}]):
        with pytest.raises(ValueError):
            reglas.sortear(invalidos, hash_anterior, 1, 0)


def test_sorteo_ponderado_por_apuesta():
    validadores = [{"id": "N01", "apuesta": 10}, {"id": "N02", "apuesta": 30}, {"id": "N03", "apuesta": 60}]
    conteo = {"N01": 0, "N02": 0, "N03": 0}
    total = 2000
    for intento in range(total):
        conteo[reglas.sortear(validadores, "cd" * 32, 3, intento)] += 1
    for id_nodo, esperado in (("N01", 0.1), ("N02", 0.3), ("N03", 0.6)):
        assert abs(conteo[id_nodo] / total - esperado) < 0.05


# ---------------------------------------------------------------------------
# A — situaciones de uso (reinicio, recarga, concurrencia, datos basura)
# ---------------------------------------------------------------------------

def test_a1_reiniciar_crea_una_simulacion_limpia():
    sim = sim_pow(dificultad=6)
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_mineria()
    sim.paso()
    nueva = sim_pow(dificultad=6)
    estado = nueva.estado()
    assert estado["pow"]["estado"] == "inactivo"
    assert estado["altura_red"] == 0 and estado["pendientes"] == []
    assert nueva.cadena_nodo("N01")[0] == sim.cadena_nodo("N01")[0], "Misma semilla ⇒ mismo génesis"
    assert estado_pow(sim) == "minando", "La simulación anterior no se ve afectada"
    assert_invariantes(nueva)


def test_a2_lecturas_no_cambian_el_estado_a_mitad_de_ronda():
    sim = sim_pos()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    avanzar_hasta(sim, "CANDIDATO")
    reloj = sim.reloj.actual()
    version = sim.version
    primera = sim.estado()
    sim.detalle_nodo("N01")
    sim.cadena_nodo("N02")
    sim.verificar_invariantes()
    segunda = sim.estado()
    assert primera == segunda
    assert primera["pos"]["estado"] == "CANDIDATO" and primera["version"] == version
    assert sim.reloj.actual() == reloj and sim.version == version
    assert sim.estado(desde_evento=primera["ultimo_evento"])["eventos"] == []
    assert completar_ronda_pos(sim) == "ACEPTADO"
    assert sim.version > version


def test_a2_version_aumenta_con_cada_cambio():
    sim = sim_pow()
    version = sim.estado()["version"]
    sim.crear_transaccion("N01", "N02", 5)
    assert sim.estado()["version"] > version


def test_a3_dos_pedidos_de_minar_a_la_vez():
    for _ in range(5):
        sim = sim_pow(dificultad=6)
        sim.crear_transaccion("N01", "N02", 5)
        resultados = en_paralelo([sim.iniciar_mineria, sim.iniciar_mineria])
        clases = sorted(clase for clase, _ in resultados)
        assert clases == ["error", "ok"], resultados
        error = next(valor for clase, valor in resultados if clase == "error")
        assert isinstance(error, Conflicto)
        assert estado_pow(sim) == "minando"
        sim.cancelar_mineria()
        assert_invariantes(sim)


def test_a3_transacciones_concurrentes_no_sobregastan():
    for _ in range(3):
        sim = sim_pow()
        receptores = IDS_10[1:9]
        resultados = en_paralelo([lambda r=r: sim.crear_transaccion("N01", r, 30) for r in receptores])
        assert not [v for c, v in resultados if c == "inesperado"], resultados
        exitosas = [v for c, v in resultados if c == "ok"]
        fallidas = [v for c, v in resultados if c == "error"]
        assert len(exitosas) == 3 and len(fallidas) == 5
        assert all(e.codigo == "saldo_insuficiente" for e in fallidas)
        estado = sim.estado()
        assert sum(p["monto"] for p in estado["pendientes"] if p["emisor"] == "N01") == 90
        assert nodo_de(estado, "N01")["disponible"] == 10
        assert_invariantes(sim)


def test_a3_dos_pestanas_avanzan_la_misma_ronda():
    for _ in range(5):
        sim = sim_pos()
        sim.crear_transaccion("N01", "N02", 5)
        sim.iniciar_ronda_pos()
        resultados = en_paralelo([lambda: sim.avanzar_pos("APUESTAS"), lambda: sim.avanzar_pos("APUESTAS")])
        clases = sorted(clase for clase, _ in resultados)
        assert clases == ["error", "ok"], resultados
        error = next(valor for clase, valor in resultados if clase == "error")
        assert isinstance(error, Conflicto) and error.codigo == "estado_cambio"
        assert estado_pos(sim) == "SORTEO"
        assert_invariantes(sim)


def test_a3_estado_obsoleto_da_conflicto():
    sim = sim_pos()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_ronda_pos()
    sim.avanzar_pos("APUESTAS")
    error = esperar_error(sim.avanzar_pos, "APUESTAS", tipos=Conflicto)
    assert error.codigo == "estado_cambio"
    assert estado_pos(sim) == "SORTEO"


def test_a3_lecturas_concurrentes_durante_la_mineria():
    sim = sim_pow()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_mineria(bloques=3, auto_tx=True)

    def leer() -> int:
        for _ in range(30):
            estado = sim.estado()
            assert estado["altura_red"] >= 0
            sim.cadena_nodo("N01")
        return 0

    resultados = en_paralelo([lambda: correr_mineria(sim), leer, leer])
    assert [c for c, _ in resultados] == ["ok", "ok", "ok"], resultados
    assert sim.estado()["altura_red"] == 3
    assert_invariantes(sim)


BASURA = [None, "", "   ", "abc", "N99", -1, 0, 1.5, float("nan"), float("inf"), True, [], {}, [1, 2],
          {"a": 1}, "x" * 10_000, 10**30]


@pytest.mark.parametrize("modo", ["pow", "pos"])
def test_a4_simulador_resiste_argumentos_basura(modo):
    sim = sim_pow() if modo == "pow" else sim_pos()
    genesis = sim.cadena_nodo("N01")[0]
    tx = firmar_tx(SEMILLA, "N01", "N02", 3, genesis["timestamp"])
    if modo == "pos":
        sim.crear_transaccion("N03", "N04", 2)
        sim.iniciar_ronda_pos()
    acciones = {
        "crear_transaccion.emisor": lambda b: sim.crear_transaccion(b, "N02", 5),
        "crear_transaccion.receptor": lambda b: sim.crear_transaccion("N01", b, 5),
        "crear_transaccion.monto": lambda b: sim.crear_transaccion("N01", "N02", b),
        "enviar_transaccion_firmada": lambda b: sim.enviar_transaccion_firmada(b),
        "enviar_transaccion_firmada.campo": lambda b: [
            sim.enviar_transaccion_firmada({**tx, campo: b})
            for campo in ("emisor", "monto", "timestamp", "firma", "id")
        ],
        "generar_transacciones": lambda b: sim.generar_transacciones(b),
        "iniciar_mineria.bloques": lambda b: sim.iniciar_mineria(b),
        "iniciar_mineria.auto_tx": lambda b: sim.iniciar_mineria(1, b),
        "iniciar_ronda_pos.rondas": lambda b: sim.iniciar_ronda_pos(b),
        "fijar_apuestas": lambda b: sim.fijar_apuestas(b),
        "fijar_apuestas.monto": lambda b: sim.fijar_apuestas({"N01": b}),
        "fijar_apuestas.id": lambda b: sim.fijar_apuestas({b: 5} if isinstance(b, (str, int, float)) else b),
        "avanzar_pos": lambda b: sim.avanzar_pos(b if b is not None else "NINGUNO"),
        "pos_automatico": lambda b: sim.pos_automatico(b),
        "votar_manual.nodo": lambda b: sim.votar_manual(b, True),
        "votar_manual.voto": lambda b: sim.votar_manual("N01", b),
        "marcar_deshonesto.id": lambda b: sim.marcar_deshonesto(b, False),
        "marcar_deshonesto.deshonesto": lambda b: sim.marcar_deshonesto("N01", b, "recompensa_falsa"),
        "marcar_deshonesto.trampa": lambda b: sim.marcar_deshonesto("N01", True, b),
        "fijar_conexion.id": lambda b: sim.fijar_conexion(b, True),
        "fijar_conexion.conectado": lambda b: sim.fijar_conexion("N01", b),
        "recibir_cadena_externa": lambda b: sim.recibir_cadena_externa("N01", b),
        "recibir_cadena_externa.bloque": lambda b: sim.recibir_cadena_externa("N01", [genesis, b]),
        "ataque_alterar_bloque.nodo": lambda b: sim.ataque_alterar_bloque(b, 0, "hash"),
        "ataque_alterar_bloque.numero": lambda b: sim.ataque_alterar_bloque("N01", b, "hash"),
        "ataque_alterar_bloque.tipo": lambda b: sim.ataque_alterar_bloque("N01", 0, b),
        "ataque_cadena_corta": lambda b: sim.ataque_cadena_corta("N01", b),
        "ataque_transaccion.tipo": lambda b: sim.ataque_transaccion(b, "N01", "N02", 5),
        "ataque_transaccion.monto": lambda b: sim.ataque_transaccion("firma_alterada", "N01", "N02", b),
        "ataque_transaccion.firmante": lambda b: sim.ataque_transaccion("otra_clave", "N01", "N02", 5, b),
        "detalle_nodo": lambda b: sim.detalle_nodo(b),
        "cadena_nodo": lambda b: sim.cadena_nodo(b),
        "estado.desde_evento": lambda b: sim.estado(b),
    }
    inesperados = []
    for nombre, accion in acciones.items():
        for basura in BASURA:
            try:
                accion(basura)
            except (EntradaInvalida, NoEncontrado, Conflicto):
                pass
            except Exception as error:  # noqa: BLE001 - se reporta abajo
                inesperados.append(f"{nombre}({basura!r:.30}): {type(error).__name__}: {error}")
    assert inesperados == [], "\n".join(inesperados[:30])
    assert_invariantes(sim)


# ---------------------------------------------------------------------------
# Reproducibilidad y conservación del dinero
# ---------------------------------------------------------------------------

def test_misma_semilla_mismos_hashes_pow():
    def recorrido(con_lecturas: bool) -> list[dict]:
        sim = sim_pow()
        if con_lecturas:
            sim.estado()
            sim.detalle_nodo("N01")
        sim.crear_transaccion("N01", "N02", 5)
        sim.generar_transacciones(3)
        if con_lecturas:
            sim.estado()
            sim.verificar_invariantes()
        sim.iniciar_mineria(bloques=3, auto_tx=True)
        correr_mineria(sim)
        return sim.cadena_nodo("N01")

    primera = recorrido(False)
    assert len(primera) == 4
    assert recorrido(True) == primera, "Las lecturas no deben cambiar el resultado"
    otra = sim_pow(semilla="otra-semilla").cadena_nodo("N01")[0]
    assert otra["hash"] != primera[0]["hash"]


def test_misma_semilla_mismos_hashes_pos():
    def recorrido() -> list[dict]:
        sim = sim_pos()
        sim.crear_transaccion("N01", "N02", 5)
        sim.iniciar_ronda_pos()
        completar_ronda_pos(sim)
        return sim.cadena_nodo("N01")

    assert recorrido() == recorrido()


def test_conservacion_del_dinero_pow():
    sim = sim_pow()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_mineria(bloques=8, auto_tx=True)
    correr_mineria(sim)
    estado = sim.estado()
    altura = estado["altura_red"]
    assert altura == 8
    circulacion = estado["circulacion"]
    assert circulacion["quemado"] == 0
    assert circulacion["total"] == N_NODOS * SALDO_INICIAL + RECOMPENSA * altura
    suma = sum(n["saldo_cadena"] + n["total_recompensas_pendientes"] for n in estado["nodos"])
    assert suma == circulacion["total"]
    saldos, no_maduras = saldos_por_cadena(sim.cadena_nodo("N01"))
    for n in estado["nodos"]:
        assert n["saldo_cadena"] == saldos[n["id"]]
        assert n["total_recompensas_pendientes"] == no_maduras.get(n["id"], 0)
    assert estado["invariantes_ok"] is True
    assert_invariantes(sim)


def test_conservacion_del_dinero_pos_con_castigo():
    sim = sim_pos()
    _, castigo = _ronda_con_proponente_deshonesto(sim)
    assert completar_ronda_pos(sim) == "ACEPTADO"
    estado = sim.estado()
    total = N_NODOS * SALDO_INICIAL + RECOMPENSA - castigo["monto"]
    assert estado["circulacion"]["total"] == total
    assert estado["circulacion"]["quemado"] == castigo["monto"]
    assert sum(n["saldo_cadena"] + n["total_recompensas_pendientes"] for n in estado["nodos"]) == total
    assert_invariantes(sim)


def test_intervalo_del_motor():
    assert sim_pow(intervalo_ms=250).intervalo_s() == pytest.approx(0.25)
