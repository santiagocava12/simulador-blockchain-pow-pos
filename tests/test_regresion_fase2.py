"""Pruebas de regresión de la revisión de la fase 2.

Cada sección corresponde a un hallazgo de los revisores:

1. La bitácora registra "cadena_rechazada" en los ataques de cadena y
   "bloque_rechazado" cuando la votación PoS rechaza un bloque.
2. estado() y cada respuesta llevan `id_simulacion` (cambia al reiniciar).
3. avanzar_pos acepta el bloque y el intento esperados (pestaña atrasada ⇒ 409).
4/9. Rendimiento: un nodo que recibe su propia cadena más bloques nuevos sólo
   valida los nuevos; un ataque o una cadena corta no revalidan todo.
5. Errores del protocolo HTTP y OPTIONS responden JSON.
6. PoS tras una partición: sólo validan los nodos con la cadena de la red.
7. PoS: un castigo de un bloque más alto no entra en un bloque más bajo.
8. Reorganización: lo de los bloques abandonados vuelve a pendientes.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest

import nucleo.red as modulo_red
from conftest import (
    RECOMPENSA,
    SEMILLA,
    assert_invariantes,
    clave_privada,
    completar_ronda_pos,
    correr_mineria,
    crear_cliente,
    dejar_pasar_tiempo,
    esperar_error,
    estado_pos,
    eventos,
    extender_cadena,
    firmar_tx,
    ids_de,
    minar_bloques,
    nodo,
    normalizar,
    sim_pos,
    sim_pow,
    timestamp_siguiente,
)
from nucleo.bloque import hash_bloque, hash_sin_votos, nuevo_bloque
from nucleo.errores import Conflicto, EntradaInvalida
from nucleo.reglas import mensaje_voto, sortear
from nucleo.validacion import validar_cadena


# ---------------------------------------------------------------- ayudas

def _avanzar_hasta(sim, objetivo: str, max_pasos: int = 20) -> None:
    for _ in range(max_pasos):
        if estado_pos(sim) == objetivo:
            return
        sim.avanzar_pos()
    raise AssertionError(f"La ronda no llegó a {objetivo}")


def _bloque_pos_externo(anterior: dict, emisor: str, receptor: str, monto: int, ids: list[str]) -> dict:
    """Bloque PoS válido armado fuera del simulador: todos validan con apuesta 1 y votan a favor."""
    validadores = [{"id": i, "apuesta": 1} for i in ids]
    tx = firmar_tx(SEMILLA, emisor, receptor, monto, anterior["timestamp"] + 1)
    numero = anterior["numero"] + 1
    proponente = sortear(validadores, anterior["hash"], numero, 0)
    bloque = nuevo_bloque(numero, anterior["timestamp"] + 1, [tx], anterior["hash"], proponente, RECOMPENSA, "pos",
                          validadores=validadores)
    candidato = hash_sin_votos(bloque)
    bloque["votos"] = [{"validador": v["id"], "peso": 1, "voto": True,
                        "firma": clave_privada(SEMILLA, v["id"]).sign(mensaje_voto(candidato, v["id"], True)).hex()}
                       for v in validadores]
    bloque["hash"] = hash_bloque(bloque)
    return bloque


class _Contador:
    """Envuelve una función de nucleo.red y cuenta sus llamadas."""

    def __init__(self, monkeypatch, nombre: str):
        self.llamadas = 0
        original = getattr(modulo_red, nombre)

        def envoltura(*args, **kwargs):
            self.llamadas += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(modulo_red, nombre, envoltura)


# ---------------------------------------------------------------- 1. bitácora

@pytest.mark.parametrize("tipo", ["monto", "monto_rehash", "hash", "hash_anterior", "recompensa"])
def test_alterar_bloque_anota_cadena_rechazada(tipo):
    sim = sim_pow()
    minar_bloques(sim, 3)
    inicio = sim.bitacora.ultimo
    resultado = sim.ataque_alterar_bloque("N02", 1, tipo)
    rechazadas = eventos(sim, "cadena_rechazada", desde=inicio)
    assert len(rechazadas) == 1 and eventos(sim, "ataque", desde=inicio)
    datos = rechazadas[0]["datos"]
    assert datos["nodo"] == "N02" and datos["rechazos"] == resultado["rechazos"] == 9
    assert datos["motivo"] == resultado["resultados"][0]["motivo"] and datos["motivo"].startswith("Bloque")
    assert_invariantes(sim)


def test_cadena_corta_anota_cadena_rechazada():
    sim = sim_pow()
    minar_bloques(sim, 3)
    sim.fijar_conexion("N05", False)
    inicio = sim.bitacora.ultimo
    sim.ataque_cadena_corta("N02", 1)
    rechazadas = eventos(sim, "cadena_rechazada", desde=inicio)
    assert len(rechazadas) == 1
    assert rechazadas[0]["datos"]["rechazos"] == 8            # los desconectados no la revisaron
    assert "no es mas larga" in normalizar(rechazadas[0]["datos"]["motivo"])
    assert "sin su ultimo bloque" in normalizar(rechazadas[0]["mensaje"])


def test_pos_bloque_rechazado_en_votacion_anota_bloque_rechazado():
    sim = sim_pos()
    sim.crear_transaccion("N02", "N03", 5)
    sim.iniciar_ronda_pos()
    _avanzar_hasta(sim, "SORTEO")
    tramposo = sim.ronda_pos.proponente
    sim.marcar_deshonesto(tramposo, True, "recompensa_falsa")
    inicio = sim.bitacora.ultimo
    _avanzar_hasta(sim, "RECHAZADO")
    nuevos = eventos(sim, desde=inicio)
    tipos = [e["tipo"] for e in nuevos]
    assert "bloque_rechazado" in tipos and tipos.index("bloque_rechazado") < tipos.index("castigo")
    rechazo = nuevos[tipos.index("bloque_rechazado")]
    assert rechazo["datos"]["nodo"] == tramposo and rechazo["datos"]["bloque"] == 1
    assert "recompensa falsa" in normalizar(rechazo["datos"]["motivo"])


# ---------------------------------------------------------------- 2. id de la simulación

def test_estado_trae_id_de_simulacion_que_cambia_al_reiniciar():
    a, b = sim_pow(), sim_pow()
    id_a = a.estado()["id_simulacion"]
    assert isinstance(id_a, str) and len(id_a) == 32
    assert id_a != b.estado()["id_simulacion"]               # misma configuración, otra simulación
    a.crear_transaccion("N01", "N02", 5)
    assert a.estado()["id_simulacion"] == id_a                # no cambia durante la simulación


def test_respuestas_http_traen_id_de_simulacion():
    cliente = crear_cliente()
    primero = cliente.get("/api/estado").get_json()
    id_viejo = primero["id_simulacion"]
    assert primero["datos"]["id_simulacion"] == id_viejo
    respuesta = cliente.post("/api/transacciones", json={"emisor": "N01", "receptor": "N02", "monto": 5}).get_json()
    assert respuesta["ok"] and respuesta["id_simulacion"] == id_viejo
    reinicio = cliente.post("/api/simulacion", json={"modo": "pow", "dificultad": 1}).get_json()
    assert reinicio["id_simulacion"] != id_viejo
    despues = cliente.get(f"/api/estado?desde={primero['datos']['ultimo_evento']}").get_json()
    assert despues["id_simulacion"] == reinicio["id_simulacion"] != id_viejo


# ---------------------------------------------------------------- 3. pestaña atrasada

def _ronda_en_votacion_del_bloque_2():
    """La pestaña B ve la VOTACION del bloque 1; la A decide ese bloque y lleva el 2 a VOTACION."""
    sim = sim_pos()
    sim.generar_transacciones(10)
    sim.iniciar_ronda_pos(rondas=3, auto_tx=True)
    _avanzar_hasta(sim, "VOTACION")
    vista_b = {"estado_esperado": "VOTACION", "numero_esperado": 1, "intento_esperado": 0}
    sim.avanzar_pos(estado_esperado="VOTACION")              # bloque 1 aceptado, sigue el 2
    assert sim.ronda_pos.numero == 2
    _avanzar_hasta(sim, "VOTACION")
    return sim, vista_b


def test_pestana_atrasada_no_decide_otra_ronda():
    sim, vista_b = _ronda_en_votacion_del_bloque_2()
    version = sim.version
    error = esperar_error(sim.avanzar_pos, **vista_b)
    assert isinstance(error, Conflicto) and error.codigo == "estado_cambio"
    assert error.detalles == {"estado_actual": "VOTACION", "numero_actual": 2, "intento_actual": 0}
    assert sim.ronda_pos.estado == "VOTACION" and sim.ronda_pos.numero == 2 and sim.version == version
    # Con lo que sí está a la vista, avanza.
    sim.avanzar_pos(estado_esperado="VOTACION", numero_esperado=2, intento_esperado=0)
    assert sim.ronda_pos.numero == 3 or sim.ronda_pos.estado in ("ACEPTADO", "RECHAZADO")


def test_intento_esperado_distinto_da_conflicto():
    sim = sim_pos()
    sim.crear_transaccion("N02", "N03", 5)
    sim.iniciar_ronda_pos()
    _avanzar_hasta(sim, "SORTEO")
    sim.marcar_deshonesto(sim.ronda_pos.proponente, True, "recompensa_falsa")
    _avanzar_hasta(sim, "RECHAZADO")
    sim.avanzar_pos(estado_esperado="RECHAZADO", numero_esperado=1, intento_esperado=0)   # nuevo sorteo
    assert sim.ronda_pos.intento == 1
    error = esperar_error(sim.avanzar_pos, estado_esperado="SORTEO", intento_esperado=0)
    assert error.codigo == "estado_cambio"


@pytest.mark.parametrize("campo, valor", [("numero_esperado", "abc"), ("numero_esperado", 0),
                                          ("numero_esperado", 1.5), ("intento_esperado", -1),
                                          ("intento_esperado", []), ("numero_esperado", True)])
def test_valores_esperados_invalidos(campo, valor):
    sim = sim_pos()
    sim.crear_transaccion("N02", "N03", 5)
    sim.iniciar_ronda_pos()
    esperar_error(sim.avanzar_pos, tipos=EntradaInvalida, **{campo: valor})
    assert sim.ronda_pos.estado == "APUESTAS"


def test_pestana_atrasada_por_http_recibe_409():
    cliente = crear_cliente(modo="pos")
    cliente.post("/api/transacciones/aleatorias", json={"cantidad": 10})
    cliente.post("/api/pos/ronda", json={"rondas": 2, "auto_tx": True})
    cliente.post("/api/pos/avanzar", json={"estado_esperado": "APUESTAS", "numero_esperado": 1,
                                           "intento_esperado": 0})
    resp = cliente.post("/api/pos/avanzar", json={"estado_esperado": "SORTEO", "numero_esperado": 2})
    cuerpo = resp.get_json()
    assert resp.status_code == 409 and cuerpo["ok"] is False and cuerpo["codigo"] == "estado_cambio"
    assert cuerpo["detalles"]["numero_actual"] == 1


# ---------------------------------------------------------------- 4/9. rendimiento

def test_bloque_minado_solo_valida_el_bloque_nuevo(monkeypatch):
    sim = sim_pow(num_nodos=20)
    minar_bloques(sim, 25)
    bloques = _Contador(monkeypatch, "validar_bloque")
    completas = _Contador(monkeypatch, "evaluar_cadena_recibida")
    minar_bloques(sim, 1)
    estado = sim.estado()
    assert estado["altura_red"] == 26 and estado["sincronizados"]
    assert completas.llamadas == 0                            # nadie revalida desde el génesis
    assert bloques.llamadas == 20                             # cada nodo valida el bloque nuevo, una vez
    assert_invariantes(sim)


def test_ataques_no_revalidan_toda_la_cadena_y_dan_el_mismo_motivo(monkeypatch):
    sim = sim_pow()
    minar_bloques(sim, 12)
    copia = sim.cadena_nodo("N03")
    copia[4]["transacciones"][0]["monto"] += 1000             # lo mismo que hace el ataque "monto"
    esperado = validar_cadena(copia, copia[0])[1]           # el motivo de validar todo desde el génesis
    bloques = _Contador(monkeypatch, "validar_bloque")
    completas = _Contador(monkeypatch, "evaluar_cadena_recibida")
    resultado = sim.ataque_alterar_bloque("N03", 4, "monto")
    assert {r["motivo"] for r in resultado["resultados"]} == {esperado}
    resultado = sim.ataque_cadena_corta("N03", 2)
    assert {r["motivo"] for r in resultado["resultados"]} == {"no es más larga que la propia (10 ≤ 12)"}
    assert bloques.llamadas == 0 and completas.llamadas == 0
    assert all(n["altura"] == 12 for n in sim.estado()["nodos"])
    assert_invariantes(sim)


def test_cadena_mas_larga_con_un_bloque_intermedio_cambiado_se_rechaza():
    """El atajo recalcula el hash: un bloque reemplazado por una copia alterada (mismo campo hash) no pasa."""
    sim = sim_pow()
    minar_bloques(sim, 4)
    receptor = sim.red.nodo("N04")
    propia = receptor.cadena
    alterado = json.loads(json.dumps(propia[2]))
    alterado["transacciones"][0]["monto"] += 1
    dejar_pasar_tiempo(sim)
    tx = firmar_tx(SEMILLA, "N09", "N10", 1, timestamp_siguiente(propia))
    nuevo = extender_cadena(propia, [tx])[-1]
    mas_larga = list(propia) + [nuevo]                        # sus mismos bloques más uno nuevo...
    mas_larga[2] = alterado                                   # ...salvo el bloque 2, cambiado
    acepto, motivo = receptor.recibir_cadena(mas_larga, sim.red.genesis, sim.reloj.actual())
    assert not acepto and motivo == "Bloque 2: el hash no coincide con el recalculado (bloque alterado)"
    assert receptor.cadena is propia


def test_nodo_que_extiende_su_cadena_no_comparte_bloques_con_quien_la_envia_desde_fuera():
    sim = sim_pow()
    minar_bloques(sim, 2)
    receptor = sim.red.nodo("N04")
    dejar_pasar_tiempo(sim)
    externa = sim.cadena_nodo("N01")
    tx = firmar_tx(SEMILLA, "N09", "N10", 1, timestamp_siguiente(externa))
    externa = extender_cadena(externa, [tx])
    enviada = list(receptor.cadena) + [externa[-1]]            # mismos bloques propios + uno nuevo de fuera
    assert receptor.recibir_cadena(enviada, sim.red.genesis, sim.reloj.actual()) == (True, "aceptada")
    externa[-1]["nonce"] = 999                                 # quien la envió la modifica después
    assert receptor.cadena[-1] is not externa[-1] and receptor.cadena[-1]["nonce"] != 999


# ---------------------------------------------------------------- 5. HTTP

def test_options_responde_405_json():
    cliente = crear_cliente()
    for ruta in ("/api/estado", "/api/pos/avanzar", "/api/nodos/N01"):
        resp = cliente.open(ruta, method="OPTIONS")
        cuerpo = resp.get_json(silent=True)
        assert resp.status_code == 405 and isinstance(cuerpo, dict), resp.data[:200]
        assert cuerpo["ok"] is False and cuerpo["codigo"] == "metodo_no_permitido"


@pytest.fixture
def servidor_real():
    """Servidor de desarrollo real (con ManejadorHTTP) en un puerto libre."""
    from werkzeug.serving import make_server

    from app import ManejadorHTTP, create_app

    servidor = make_server("127.0.0.1", 0, create_app(motor_activo=False), threaded=True,
                           request_handler=ManejadorHTTP)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    yield servidor.server_port
    servidor.shutdown()
    servidor.server_close()


def _peticion_cruda(puerto: int, datos: bytes) -> tuple[int, str, bytes]:
    with socket.create_connection(("127.0.0.1", puerto), timeout=5) as conexion:
        conexion.sendall(datos)
        respuesta = b""
        while True:
            parte = conexion.recv(65536)
            if not parte:
                break
            respuesta += parte
    cabecera, _, cuerpo = respuesta.partition(b"\r\n\r\n")
    lineas = cabecera.decode("latin-1").split("\r\n")
    tipo = next((l.split(":", 1)[1].strip() for l in lineas if l.lower().startswith("content-type")), "")
    return int(lineas[0].split()[1]), tipo, cuerpo


@pytest.mark.parametrize("cruda, http", [
    (b"GARBAGE\r\n\r\n", 400),
    (b"GET /api/estado HTTP/9.9\r\nHost: x\r\n\r\n", 505),
    (b"GET /api/estado?desde=" + b"1" * 70_000 + b" HTTP/1.1\r\nHost: x\r\n\r\n", 414),
    (b"GET /api/estado HTTP/1.1\r\nHost: x\r\n" + b"".join(b"X%d: 1\r\n" % i for i in range(120)) + b"\r\n", 431),
], ids=["linea_basura", "http_9_9", "url_de_70_kb", "120_cabeceras"])
def test_errores_del_protocolo_http_responden_json(servidor_real, cruda, http):
    estado, tipo, cuerpo = _peticion_cruda(servidor_real, cruda)
    assert estado == http and tipo.startswith("application/json")
    datos = json.loads(cuerpo)
    assert datos["ok"] is False and datos["error"] and datos["codigo"] and datos["detalles"]["http"] == http


# ---------------------------------------------------------------- 6. bifurcación tras una partición (PoS)

def test_pos_bifurcacion_de_igual_altura_se_resuelve_sin_castigar_honestos():
    sim = sim_pos()
    ids = ids_de(sim)
    sim.fijar_conexion("N01", False)
    sim.crear_transaccion("N02", "N03", 5)
    sim.iniciar_ronda_pos()
    assert completar_ronda_pos(sim) == "ACEPTADO"            # rama A, sin N01
    for i in ids[1:]:
        sim.fijar_conexion(i, False)
    sim.fijar_conexion("N01", True)
    sim.crear_transaccion("N01", "N02", 1)
    sim.iniciar_ronda_pos()
    assert completar_ronda_pos(sim) == "ACEPTADO"            # rama B, sólo N01
    for i in ids[1:]:
        sim.fijar_conexion(i, True)
    estado = sim.estado()
    assert not estado["sincronizados"] and all(n["altura"] == 1 for n in estado["nodos"])

    sim.crear_transaccion("N03", "N04", 1)
    sim.iniciar_ronda_pos()
    referencia = sim.red.nodo_referencia()
    assert set(sim.ronda_pos.apuestas) == {n.id for n in sim.red.conectados()
                                           if n.ultimo_hash == referencia.ultimo_hash}
    assert completar_ronda_pos(sim) == "ACEPTADO"
    assert sim.ronda_pos.castigos_ronda == [] and sim.red.castigos_pendientes == []
    estado = sim.estado()
    assert estado["sincronizados"] and estado["altura_red"] == 2
    assert all(n["altura"] == 2 for n in estado["nodos"])
    assert_invariantes(sim)


def test_pos_tras_ponerse_al_dia_validan_todos():
    sim = sim_pos()
    sim.fijar_conexion("N01", False)
    sim.crear_transaccion("N02", "N03", 5)
    sim.iniciar_ronda_pos()
    completar_ronda_pos(sim)
    sim.fijar_conexion("N01", True)                          # se pone al día: no hay bifurcación
    assert sim.estado()["sincronizados"]
    sim.crear_transaccion("N04", "N05", 5)
    sim.iniciar_ronda_pos()
    assert len(sim.ronda_pos.apuestas) == 10                  # todos tienen la cadena de la red


# ---------------------------------------------------------------- 7. castigo de un bloque más alto

def test_pos_castigo_de_un_bloque_mas_alto_espera_su_altura():
    sim = sim_pos()
    ids = ids_de(sim)
    sim.fijar_conexion("N01", False)                          # N01 se queda en el génesis
    sim.crear_transaccion("N02", "N03", 5)
    sim.iniciar_ronda_pos()
    assert completar_ronda_pos(sim) == "ACEPTADO"            # bloque 1 sin N01
    sim.crear_transaccion("N02", "N03", 5)
    sim.iniciar_ronda_pos()
    _avanzar_hasta(sim, "SORTEO")
    tramposo = sim.ronda_pos.proponente
    sim.marcar_deshonesto(tramposo, True, "recompensa_falsa")
    _avanzar_hasta(sim, "RECHAZADO")                          # castigo con numero = 2
    sim.cancelar_ronda_pos()
    assert [(c["nodo"], c["numero"]) for c in sim.red.castigos_pendientes] == [(tramposo, 2)]
    for i in ids[1:]:
        sim.fijar_conexion(i, False)
    sim.fijar_conexion("N01", True)                           # la red es sólo N01, en la altura 0
    sim.crear_transaccion("N01", "N02", 1)
    sim.iniciar_ronda_pos()
    assert completar_ronda_pos(sim) == "ACEPTADO"            # el castigo del bloque 2 no entra al 1
    assert sim.ronda_pos.castigos_ronda == []
    assert sim.cadena_nodo("N01")[1]["castigos"] == []
    assert [(c["nodo"], c["numero"]) for c in sim.red.castigos_pendientes] == [(tramposo, 2)]
    assert_invariantes(sim)

    for i in ids[1:]:                                         # la partición sana
        sim.fijar_conexion(i, True)
    sim.marcar_deshonesto(tramposo, False)
    saldo = nodo(sim, tramposo)["saldo_cadena"]
    monto = sim.red.castigos_pendientes[0]["monto"]
    sim.crear_transaccion("N03", "N04", 1)
    sim.iniciar_ronda_pos()
    assert completar_ronda_pos(sim) == "ACEPTADO"
    ultimo = sim.cadena_nodo("N05")[-1]
    assert ultimo["numero"] == 2 and [(c["nodo"], c["numero"]) for c in ultimo["castigos"]] == [(tramposo, 2)]
    assert nodo(sim, "N01")["castigo_pendiente"] == 0 and sim.red.castigos_pendientes == []
    assert ultimo["proponente"] == "N01"                      # sólo N01 tenía la cadena de la red
    recibido = sum(t["monto"] for t in ultimo["transacciones"] if t["receptor"] == tramposo)
    enviado = sum(t["monto"] for t in ultimo["transacciones"] if t["emisor"] == tramposo)
    assert nodo(sim, tramposo)["saldo_cadena"] == saldo - monto + recibido - enviado
    assert sim.estado()["sincronizados"]
    assert_invariantes(sim)


# ---------------------------------------------------------------- 8. reorganización

def test_reorganizacion_pow_devuelve_transacciones_a_pendientes():
    sim = sim_pow()
    tx = sim.crear_transaccion("N02", "N03", 40)
    sim.iniciar_mineria()
    correr_mineria(sim)
    assert nodo(sim, "N02")["saldo_cadena"] == 60
    genesis = sim.cadena_nodo("N01")[0]
    otra = extender_cadena([genesis], [firmar_tx(SEMILLA, "N05", "N06", 1, genesis["timestamp"] + 1)])
    otra = extender_cadena(otra, [firmar_tx(SEMILLA, "N05", "N06", 2, timestamp_siguiente(otra))])
    dejar_pasar_tiempo(sim)
    inicio = sim.bitacora.ultimo
    assert sim.recibir_cadena_externa("N01", otra)["acepto"]
    estado = sim.estado()
    assert estado["altura_red"] == 2 and estado["sincronizados"]
    assert [p["id"] for p in estado["pendientes"]] == [tx["id"]]          # volvió a pendientes
    n02 = nodo(sim, "N02")
    assert n02["saldo_cadena"] == 100 and n02["disponible"] == 60
    reorganizaciones = [e for e in eventos(sim, "cadena_aceptada", desde=inicio) if "reorganizacion" in
                        normalizar(e["mensaje"])]
    assert len(reorganizaciones) == 1 and reorganizaciones[0]["datos"]["transacciones"] == 1
    assert_invariantes(sim)
    sim.iniciar_mineria()                                    # y se puede minar otra vez
    correr_mineria(sim)
    assert nodo(sim, "N02")["saldo_cadena"] == 60 and sim.estado()["pendientes"] == []
    assert_invariantes(sim)


def test_reorganizacion_quita_de_pendientes_lo_que_la_nueva_cadena_ya_confirma():
    sim = sim_pow()
    genesis = sim.cadena_nodo("N01")[0]
    tx = sim.crear_transaccion("N05", "N06", 3)
    dejar_pasar_tiempo(sim)
    otra = extender_cadena([genesis], [tx])                   # otra cadena ya trae esa transacción
    inicio = sim.bitacora.ultimo
    assert sim.recibir_cadena_externa("N01", otra)["acepto"]
    assert sim.estado()["pendientes"] == []
    assert eventos(sim, "transaccion_rechazada", desde=inicio) == []      # no es un rechazo: se confirmó
    assert any("confirmadas" in normalizar(e["mensaje"]) for e in eventos(sim, "transaccion", desde=inicio))
    assert_invariantes(sim)


def test_reorganizacion_pos_devuelve_castigos_a_pendientes():
    sim = sim_pos()
    ids = ids_de(sim)
    sim.crear_transaccion("N02", "N03", 5)
    sim.iniciar_ronda_pos()
    _avanzar_hasta(sim, "SORTEO")
    tramposo = sim.ronda_pos.proponente
    sim.marcar_deshonesto(tramposo, True, "recompensa_falsa")
    assert completar_ronda_pos(sim) == "ACEPTADO"
    sim.marcar_deshonesto(tramposo, False)
    castigo = sim.cadena_nodo("N01")[1]["castigos"][0]
    assert castigo["nodo"] == tramposo
    saldo_castigado = nodo(sim, tramposo)["saldo_cadena"]

    genesis = sim.cadena_nodo("N01")[0]
    emisor = "N10" if tramposo != "N10" else "N09"
    x1 = _bloque_pos_externo(genesis, emisor, tramposo, 1, ids)   # otra rama, sin el castigo
    x2 = _bloque_pos_externo(x1, emisor, tramposo, 2, ids)
    dejar_pasar_tiempo(sim)
    assert sim.recibir_cadena_externa("N01", [genesis, x1, x2])["acepto"]
    assert sim.red.castigos_pendientes == [castigo]          # el castigo volvió a pendientes
    n = nodo(sim, tramposo)
    assert n["castigo_pendiente"] == castigo["monto"] and n["saldo_cadena"] > saldo_castigado
    assert_invariantes(sim)

    sim.crear_transaccion(emisor, "N08", 1)
    sim.iniciar_ronda_pos()
    assert completar_ronda_pos(sim) == "ACEPTADO"
    assert sim.cadena_nodo("N01")[-1]["castigos"] == [castigo]           # se registró otra vez
    assert sim.red.castigos_pendientes == [] and nodo(sim, tramposo)["castigo_pendiente"] == 0
    assert_invariantes(sim)


# ---------------------------------------------------------------- revisión del orquestador
# El atajo de Nodo.recibir_cadena compara hashes RECALCULADOS (no identidad de
# objetos) y las invariantes detectan un bloque modificado en memoria.

def test_copia_identica_desde_fuera_mas_un_bloque_valida_solo_lo_nuevo(monkeypatch):
    sim = sim_pow()
    minar_bloques(sim, 5)
    receptor = sim.red.nodo("N04")
    propia = receptor.cadena
    dejar_pasar_tiempo(sim)
    externa = json.loads(json.dumps(sim.cadena_nodo("N01")))    # objetos nuevos, mismo contenido
    tx = firmar_tx(SEMILLA, "N09", "N10", 1, timestamp_siguiente(externa))
    externa = extender_cadena(externa, [tx])
    bloques = _Contador(monkeypatch, "validar_bloque")
    completas = _Contador(monkeypatch, "evaluar_cadena_recibida")
    assert receptor.recibir_cadena(externa, sim.red.genesis, sim.reloj.actual()) == (True, "aceptada")
    assert bloques.llamadas == 1 and completas.llamadas == 0     # sólo el bloque nuevo pasa por todas las reglas
    assert all(receptor.cadena[i] is propia[i] for i in range(len(propia)))   # conserva sus bloques validados


def test_bloque_modificado_en_memoria_lo_detectan_las_invariantes():
    sim = sim_pow()
    minar_bloques(sim, 3)
    assert sim.verificar_invariantes() == []
    sim.red.nodo("N01").cadena[2]["transacciones"][0]["monto"] += 7   # nadie debería hacer esto
    problemas = sim.verificar_invariantes()
    assert any("cambió después de validarse" in p for p in problemas)


def test_bloque_propio_modificado_en_memoria_no_cuenta_como_conocido():
    sim = sim_pow()
    minar_bloques(sim, 3)
    receptor = sim.red.nodo("N05")
    receptor.cadena[2]["nonce"] += 1                             # corrupción en memoria (bloque compartido)
    dejar_pasar_tiempo(sim)
    cadena = list(receptor.cadena)
    tx = firmar_tx(SEMILLA, "N09", "N10", 1, timestamp_siguiente(cadena))
    mas_larga = cadena + [extender_cadena(json.loads(json.dumps(cadena)), [tx])[-1]]
    acepto, motivo = receptor.recibir_cadena(mas_larga, sim.red.genesis, sim.reloj.actual())
    assert not acepto and "Bloque 2" in motivo
