"""Pruebas de la ruta del bloque tramposo de un minero (PoW).

``POST /api/ataques/bloque-tramposo {nodo, trampa}`` llama a
``Simulador.ataque_bloque_tramposo``. El nodo arma sobre la punta de SU cadena
un bloque con la trampa (pendientes válidas, o una transacción de relleno que
no entra a pendientes, más las transacciones tramposas al final y la
recompensa), lo mina y lo envía a todos los nodos conectados, él incluido.
Nadie lo agrega: las cadenas, los saldos y las pendientes no cambian; sólo
avanzan el reloj, la bitácora y la versión. Hace determinista el escenario W5.
"""

from __future__ import annotations

import copy
import itertools
import re
from typing import Any

import pytest

import nucleo.red as modulo_red
import nucleo.simulador as modulo_simulador
from conftest import (
    CONFIG_POS,
    RECOMPENSA,
    SEMILLA,
    assert_invariantes,
    clave_publica_hex,
    correr_mineria,
    crear_cliente,
    esperar_error,
    estado_pow,
    eventos,
    firma_valida,
    hash_guia,
    ids_de,
    minar_bloques,
    nodo,
    normalizar,
    serializar_guia,
    sim_pos,
    sim_pow,
)
from nucleo.errores import Conflicto, EntradaInvalida, NoEncontrado
from nucleo.trampas import TRAMPAS_PROPONENTE
from nucleo.validacion import validar_cadena

RUTA = "/api/ataques/bloque-tramposo"

# Lo que debe decir el motivo del rechazo (normalizado: minúsculas y sin acentos).
MOTIVO_ESPERADO = {
    "recompensa_falsa": f"recompensa falsa: {RECOMPENSA * 10} distinta a la establecida {RECOMPENSA}",
    "firma_alterada": "firma invalida",
    "gasto_excesivo": "doble gasto / saldo insuficiente",
    "doble_gasto": "doble gasto / saldo insuficiente",
}
# Cuántas transacciones tramposas agrega cada trampa al final del bloque.
NUM_TRAMPOSAS = {"recompensa_falsa": 0, "firma_alterada": 1, "gasto_excesivo": 1, "doble_gasto": 2}


# ---------------------------------------------------------------- ayudas

def _foto(sim) -> dict[str, Any]:
    """Todo lo que el ataque NO debe cambiar: cadenas, libros, pendientes y sesiones."""
    datos = sim.estado()
    return {
        "cadenas": {id_nodo: sim.cadena_nodo(id_nodo) for id_nodo in ids_de(sim)},
        "nodos": datos["nodos"],
        "pendientes": datos["pendientes"],
        "castigos": datos["castigos_pendientes"],
        "hash_red": datos["hash_red"],
        "altura": datos["altura_red"],
        "circulacion": datos["circulacion"],
        "pow": datos["pow"],
        "pos": datos["pos"],
    }


class _Espia:
    """Guarda cada cadena que recibe un nodo, para revisar el bloque tramposo por fuera."""

    def __init__(self, monkeypatch):
        self.recibidas: list[tuple[str, list[dict]]] = []
        original = modulo_red.Nodo.recibir_cadena

        def espia(nodo_receptor, cadena, *args, **kwargs):
            self.recibidas.append((nodo_receptor.id, cadena))
            return original(nodo_receptor, cadena, *args, **kwargs)

        monkeypatch.setattr(modulo_red.Nodo, "recibir_cadena", espia)

    def bloque(self) -> tuple[list[dict], dict]:
        """(cadena base, bloque tramposo): todos recibieron la misma cadena."""
        assert self.recibidas, "Ningún nodo recibió la cadena"
        cadenas = [cadena for _, cadena in self.recibidas]
        assert all(cadena == cadenas[0] for cadena in cadenas)
        return cadenas[0][:-1], cadenas[0][-1]


def _minar_guia(bloque: dict, dificultad: int) -> dict:
    """Mina el bloque con el hash EXACTO de la guía (nonce 0, 1, 2, …)."""
    prefijo = "0" * dificultad
    nonce = next(n for n in itertools.count() if hash_guia({**bloque, "nonce": n}).startswith(prefijo))
    minado = {**bloque, "nonce": nonce}
    minado["hash"] = hash_guia(minado)
    return minado


def _sin_trampa(bloque: dict, trampa: str, dificultad: int) -> dict:
    """El mismo bloque sin las transacciones tramposas y con la recompensa correcta, vuelto a minar."""
    honesto = copy.deepcopy(bloque)
    n = NUM_TRAMPOSAS[trampa]
    if n:
        honesto["transacciones"] = honesto["transacciones"][:-n]
        honesto["firma"] = honesto["firma"][:-n]
    honesto["recompensa"]["monto"] = RECOMPENSA
    return _minar_guia(honesto, dificultad)


def _indice_tx_del_motivo(motivo: str) -> int | None:
    """Número de transacción (1, 2, …) que señala el motivo del rechazo, o None."""
    encontrado = re.search(r"transaccion (\d+)", normalizar(motivo))
    return int(encontrado.group(1)) if encontrado else None


def _sin_cambios(sim, funcion, *args, tipos) -> Any:
    """Exige el error y que la simulación quede EXACTAMENTE igual (también reloj y bitácora)."""
    antes = sim.estado()
    reloj = sim.reloj.actual()
    error = esperar_error(funcion, *args, tipos=tipos)
    assert sim.estado() == antes
    assert sim.reloj.actual() == reloj
    return error


# ---------------------------------------------------------------- núcleo: cada trampa

@pytest.mark.parametrize("con_pendientes", [False, True], ids=["sin_pendientes", "con_pendientes"])
@pytest.mark.parametrize("trampa", TRAMPAS_PROPONENTE)
def test_cada_trampa_la_rechazan_todos_los_nodos(trampa, con_pendientes, monkeypatch):
    sim = sim_pow()
    minar_bloques(sim, 2)
    if con_pendientes:
        sim.crear_transaccion("N03", "N02", 5)   # le paga al atacante
        sim.crear_transaccion("N02", "N04", 7)
    antes = _foto(sim)
    version, inicio = sim.version, sim.bitacora.ultimo
    espia = _Espia(monkeypatch)

    resultado = sim.ataque_bloque_tramposo("N02", trampa)

    # Lo revisaron todos los conectados (también N02, que lo armó) y ninguno lo agregó.
    assert [r["nodo"] for r in resultado["resultados"]] == ids_de(sim)
    assert resultado["rechazos"] == 10
    assert not any(r["acepto"] for r in resultado["resultados"])
    for r in resultado["resultados"]:
        assert MOTIVO_ESPERADO[trampa] in normalizar(r["motivo"]), r
    assert resultado["motivo"] == resultado["resultados"][0]["motivo"]
    assert resultado["nodo"] == "N02" and resultado["trampa"] == trampa

    # Nada cambió salvo reloj, bitácora y versión.
    assert _foto(sim) == antes
    assert sim.version == version + 1
    assert_invariantes(sim)

    # Bitácora: "ataque" y "bloque_rechazado" con el motivo.
    nuevos = eventos(sim, desde=inicio)
    assert [e["tipo"] for e in nuevos] == ["ataque", "bloque_rechazado"]
    ataque, rechazo = nuevos
    assert ataque["mensaje"] == resultado["mensaje"]
    assert ataque["datos"]["nodo"] == rechazo["datos"]["nodo"] == "N02"
    assert ataque["datos"]["bloque"] == rechazo["datos"]["bloque"] == 3
    assert ataque["datos"]["trampa"] == rechazo["datos"]["trampa"] == trampa
    assert rechazo["datos"]["motivo"] == resultado["motivo"]
    assert rechazo["datos"]["rechazos"] == 10
    assert normalizar(resultado["motivo"]) in normalizar(rechazo["mensaje"])
    assert "ninguna cadena cambio" in normalizar(resultado["mensaje"])

    # El bloque: sobre la punta de N02, minado como cualquier otro, con la trampa al final.
    base, bloque = espia.bloque()
    assert base == antes["cadenas"]["N02"]
    assert bloque["numero"] == 3 and bloque["hash_anterior"] == antes["hash_red"]
    assert bloque["proponente"] == "N02" and bloque["modo"] == "pow"
    monto = RECOMPENSA * 10 if trampa == "recompensa_falsa" else RECOMPENSA
    assert bloque["recompensa"] == {"beneficiario": "N02", "monto": monto}
    assert bloque["hash"] == hash_guia(bloque) and bloque["hash"].startswith("0")
    assert resultado["intentos"] == bloque["nonce"] + 1
    assert not any(hash_guia({**bloque, "nonce": n}).startswith("0") for n in range(bloque["nonce"]))
    assert all(tx["timestamp"] <= bloque["timestamp"] for tx in bloque["transacciones"])
    assert bloque["timestamp"] <= sim.reloj.actual()
    resumen = resultado["bloque"]
    assert (resumen["numero"], resumen["hash"], resumen["nonce"]) == (3, bloque["hash"], bloque["nonce"])
    assert resumen["num_transacciones"] == len(bloque["transacciones"])
    assert resumen["recompensa"] == bloque["recompensa"]

    honestas = len(bloque["transacciones"]) - NUM_TRAMPOSAS[trampa]
    if con_pendientes:
        campos = ("emisor", "receptor", "monto", "timestamp")
        esperadas = [{k: tx[k] for k in campos} for tx in antes["pendientes"]]
        assert bloque["transacciones"][:honestas] == esperadas
    else:
        # Transacción de relleno: de N02, de 1 moneda, bien firmada y fuera de pendientes.
        assert honestas == 1
        relleno = bloque["transacciones"][0]
        assert (relleno["emisor"], relleno["monto"]) == ("N02", 1) and relleno["receptor"] != "N02"
        assert firma_valida(clave_publica_hex(SEMILLA, "N02"), serializar_guia(relleno), bloque["firma"][0])
        assert sim.estado()["pendientes"] == []

    # El rechazo se debe SÓLO a la trampa: sin ella, el mismo bloque sería válido.
    if NUM_TRAMPOSAS[trampa]:
        assert _indice_tx_del_motivo(resultado["motivo"]) > honestas   # falla una transacción tramposa
    valida, motivo, _ = validar_cadena(base + [_sin_trampa(bloque, trampa, 1)], base[0], sim.reloj.actual())
    assert valida, motivo


@pytest.mark.parametrize("trampa", TRAMPAS_PROPONENTE)
def test_atacante_sin_saldo_recibe_el_relleno_de_otro_nodo(trampa, monkeypatch):
    sim = sim_pow()
    sim.crear_transaccion("N02", "N03", 100)   # N02 se queda sin saldo
    sim.iniciar_mineria()
    correr_mineria(sim)
    assert nodo(sim, "N02")["saldo_cadena"] == 0
    antes = _foto(sim)
    espia = _Espia(monkeypatch)

    resultado = sim.ataque_bloque_tramposo("N02", trampa)

    assert resultado["rechazos"] == 10
    assert all(MOTIVO_ESPERADO[trampa] in normalizar(r["motivo"]) for r in resultado["resultados"])
    _, bloque = espia.bloque()
    relleno = bloque["transacciones"][0]
    assert relleno["receptor"] == "N02" and relleno["emisor"] != "N02" and relleno["monto"] == 1
    assert firma_valida(clave_publica_hex(SEMILLA, relleno["emisor"]), serializar_guia(relleno), bloque["firma"][0])
    assert _foto(sim) == antes
    assert_invariantes(sim)


def test_respeta_el_maximo_de_transacciones_y_el_rechazo_dice_la_causa_real(monkeypatch):
    sim = sim_pow(max_tx_por_bloque=2)
    for emisor, receptor in (("N01", "N02"), ("N03", "N04"), ("N05", "N06"), ("N07", "N08")):
        sim.crear_transaccion(emisor, receptor, 3)
    pendientes = sim.estado()["pendientes"]
    espia = _Espia(monkeypatch)

    resultado = sim.ataque_bloque_tramposo("N09", "doble_gasto")

    _, bloque = espia.bloque()
    assert len(bloque["transacciones"]) == 4   # 2 pendientes (el máximo) + 2 tramposas
    assert [tx["emisor"] for tx in bloque["transacciones"][:2]] == ["N01", "N03"]
    assert resultado["rechazos"] == 10
    assert all("doble gasto" in normalizar(r["motivo"]) for r in resultado["resultados"])
    assert all("maximo" not in normalizar(r["motivo"]) for r in resultado["resultados"])
    assert sim.estado()["pendientes"] == pendientes


def test_los_desconectados_no_lo_reciben_y_el_desconectado_no_puede_atacar():
    sim = sim_pow()
    sim.fijar_conexion("N05", False)
    sim.fijar_conexion("N07", False)
    resultado = sim.ataque_bloque_tramposo("N02", "recompensa_falsa")
    recibieron = [r["nodo"] for r in resultado["resultados"]]
    assert recibieron == [i for i in ids_de(sim) if i not in ("N05", "N07")]
    assert resultado["rechazos"] == 8
    assert "2 nodos desconectados no lo recibieron" in resultado["mensaje"]
    assert_invariantes(sim)

    error = _sin_cambios(sim, sim.ataque_bloque_tramposo, "N05", "recompensa_falsa", tipos=Conflicto)
    assert error.codigo == "nodo_desconectado"


def test_la_red_sigue_funcionando_tras_varios_ataques():
    sim = sim_pow()
    minar_bloques(sim, 1)
    hashes = set()
    for i, trampa in enumerate(TRAMPAS_PROPONENTE * 2):
        resultado = sim.ataque_bloque_tramposo(f"N0{i % 9 + 1}", trampa)
        assert resultado["rechazos"] == 10
        hashes.add(resultado["bloque"]["hash"])
    assert len(hashes) == 2 * len(TRAMPAS_PROPONENTE)   # cada intento es un bloque distinto
    assert sim.estado()["altura_red"] == 1
    minar_bloques(sim, 2)                               # y después se mina con normalidad
    assert sim.estado()["altura_red"] == 3 and sim.estado()["sincronizados"]
    assert_invariantes(sim)


def test_acepta_la_trampa_sin_distinguir_mayusculas_y_el_nodo_abreviado():
    sim = sim_pow()
    resultado = sim.ataque_bloque_tramposo("n3", "  Recompensa_Falsa ")
    assert resultado["nodo"] == "N03" and resultado["trampa"] == "recompensa_falsa"
    assert resultado["rechazos"] == 10


# ---------------------------------------------------------------- núcleo: errores sin cambios

def test_en_pos_es_un_conflicto():
    sim = sim_pos()
    error = _sin_cambios(sim, sim.ataque_bloque_tramposo, "N01", "firma_alterada", tipos=Conflicto)
    assert error.codigo == "modo_incorrecto"
    assert error.mensaje == "En Proof of Stake usa el escenario del proponente tramposo"


@pytest.mark.parametrize("trampa", ["voto_invertido", "VOTO_INVERTIDO", "inventado", None, "", "  ", 5, [], {},
                                    True])
def test_trampa_invalida(trampa):
    sim = sim_pow()
    error = _sin_cambios(sim, sim.ataque_bloque_tramposo, "N01", trampa, tipos=EntradaInvalida)
    assert all(nombre in error.mensaje for nombre in TRAMPAS_PROPONENTE), error.mensaje


@pytest.mark.parametrize("valor, tipo", [("N99", NoEncontrado), ("N11", NoEncontrado), (None, EntradaInvalida),
                                         ("", EntradaInvalida), ([], EntradaInvalida)])
def test_nodo_invalido(valor, tipo):
    sim = sim_pow()
    _sin_cambios(sim, sim.ataque_bloque_tramposo, valor, "recompensa_falsa", tipos=tipo)


def test_durante_la_mineria_es_un_conflicto():
    sim = sim_pow()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_mineria(bloques=2, auto_tx=True)
    error = _sin_cambios(sim, sim.ataque_bloque_tramposo, "N03", "recompensa_falsa", tipos=Conflicto)
    assert error.codigo == "mineria_en_curso"

    # También mientras se muestra al ganador antes del siguiente bloque (la sesión sigue activa).
    for _ in range(5000):
        if estado_pow(sim) != "minando":
            break
        sim.paso()
    assert estado_pow(sim) == "ganador"
    error = _sin_cambios(sim, sim.ataque_bloque_tramposo, "N03", "recompensa_falsa", tipos=Conflicto)
    assert error.codigo == "mineria_en_curso"

    correr_mineria(sim)
    assert sim.ataque_bloque_tramposo("N03", "recompensa_falsa")["rechazos"] == 10


def test_tras_cancelar_la_mineria_si_se_puede():
    sim = sim_pow()
    sim.crear_transaccion("N01", "N02", 5)
    sim.iniciar_mineria()
    sim.cancelar_mineria()
    resultado = sim.ataque_bloque_tramposo("N03", "firma_alterada")
    assert resultado["rechazos"] == 10
    assert len(sim.estado()["pendientes"]) == 1
    assert_invariantes(sim)


def test_dificultad_alta_no_cambia_nada(monkeypatch):
    monkeypatch.setattr(modulo_simulador, "MAX_INTENTOS_TRAMPOSO", 3)
    sim = sim_pow(dificultad=6)
    error = _sin_cambios(sim, sim.ataque_bloque_tramposo, "N01", "recompensa_falsa", tipos=Conflicto)
    assert error.codigo == "dificultad_alta"
    assert error.detalles == {"intentos": 3, "dificultad": 6}
    assert "menor dificultad" in normalizar(error.mensaje)


def test_si_falla_la_mineria_el_bloque_no_queda_a_medias(monkeypatch):
    monkeypatch.setattr(modulo_simulador, "MAX_INTENTOS_TRAMPOSO", 3)
    sim = sim_pow(dificultad=6)
    bloque = sim._armar_bloque_tramposo(sim.red.nodo("N01"), "doble_gasto")
    copia = copy.deepcopy(bloque)
    with pytest.raises(Conflicto):
        sim._minar_bloque_tramposo("N01", bloque)
    assert bloque == copia


# ---------------------------------------------------------------- HTTP

def _exito(resp) -> dict:
    cuerpo = resp.get_json(silent=True)
    assert resp.status_code == 200 and isinstance(cuerpo, dict) and cuerpo.get("ok") is True, resp.data[:300]
    assert isinstance(cuerpo["mensaje"], str) and isinstance(cuerpo["version"], int) and cuerpo["id_simulacion"]
    return cuerpo


def _error(resp, http: int) -> dict:
    cuerpo = resp.get_json(silent=True)
    assert resp.status_code == http, (resp.status_code, resp.data[:300])
    assert isinstance(cuerpo, dict) and cuerpo.get("ok") is False
    assert isinstance(cuerpo.get("error"), str) and cuerpo["error"].strip()
    assert isinstance(cuerpo.get("codigo"), str) and cuerpo["codigo"]
    return cuerpo


def _estado(cliente) -> dict:
    return _exito(cliente.get("/api/estado"))["datos"]


def _assert_red_intacta(cliente) -> None:
    datos = _estado(cliente)
    assert datos["invariantes_ok"] is True and datos["problemas_invariantes"] == []
    assert datos["altura_red"] == 0 and datos["sincronizados"] is True
    assert datos["pendientes"] == []


@pytest.mark.parametrize("trampa", TRAMPAS_PROPONENTE)
def test_http_cada_trampa_rechazada(trampa):
    cliente = crear_cliente()
    version = _estado(cliente)["version"]
    cuerpo = _exito(cliente.post(RUTA, json={"nodo": "N04", "trampa": trampa}))
    datos = cuerpo["datos"]
    assert cuerpo["mensaje"] == datos["mensaje"] and cuerpo["version"] == version + 1
    assert datos["rechazos"] == 10 and len(datos["resultados"]) == 10
    assert all(not r["acepto"] and MOTIVO_ESPERADO[trampa] in normalizar(r["motivo"]) for r in datos["resultados"])
    assert datos["bloque"]["numero"] == 1 and datos["bloque"]["proponente"] == "N04"
    assert datos["intentos"] == datos["bloque"]["nonce"] + 1
    _assert_red_intacta(cliente)
    tipos = [e["tipo"] for e in _estado(cliente)["eventos"]]
    assert tipos[-2:] == ["ataque", "bloque_rechazado"]


def test_http_en_pos_409():
    cliente = crear_cliente(**CONFIG_POS)
    cuerpo = _error(cliente.post(RUTA, json={"nodo": "N01", "trampa": "firma_alterada"}), 409)
    assert cuerpo["codigo"] == "modo_incorrecto"
    assert cuerpo["error"] == "En Proof of Stake usa el escenario del proponente tramposo"
    _assert_red_intacta(cliente)


@pytest.mark.parametrize("cuerpo", [{"nodo": "N01", "trampa": "voto_invertido"},
                                    {"nodo": "N01", "trampa": "inventado"},
                                    {"nodo": "N01"},
                                    {"nodo": "N01", "trampa": 5},
                                    {"trampa": "recompensa_falsa"},
                                    {"nodo": [], "trampa": "recompensa_falsa"}])
def test_http_datos_invalidos_400(cuerpo):
    cliente = crear_cliente()
    _error(cliente.post(RUTA, json=cuerpo), 400)
    _assert_red_intacta(cliente)


@pytest.mark.parametrize("crudo, tipo", [(b"{malo", "application/json"), (b"[]", "application/json"),
                                         (b"null", "application/json"), (b"hola", "text/plain"),
                                         (b"nodo=N01&trampa=doble_gasto", "application/x-www-form-urlencoded"),
                                         (b"", "application/json")])
def test_http_cuerpo_mal_formado_400(crudo, tipo):
    cliente = crear_cliente()
    _error(cliente.post(RUTA, data=crudo, content_type=tipo), 400)
    _assert_red_intacta(cliente)


def test_http_nodo_inexistente_404():
    cliente = crear_cliente()
    _error(cliente.post(RUTA, json={"nodo": "N99", "trampa": "recompensa_falsa"}), 404)
    _assert_red_intacta(cliente)


def test_http_durante_la_mineria_409():
    cliente = crear_cliente()
    _exito(cliente.post("/api/transacciones", json={"emisor": "N01", "receptor": "N02", "monto": 5}))
    _exito(cliente.post("/api/pow/minar", json={}))
    cuerpo = _error(cliente.post(RUTA, json={"nodo": "N03", "trampa": "gasto_excesivo"}), 409)
    assert cuerpo["codigo"] == "mineria_en_curso"
    _exito(cliente.post("/api/pow/cancelar", json={}))
    datos = _exito(cliente.post(RUTA, json={"nodo": "N03", "trampa": "gasto_excesivo"}))["datos"]
    assert datos["rechazos"] == 10


def test_http_nodo_desconectado_409():
    cliente = crear_cliente()
    _exito(cliente.post("/api/nodos/N03/conexion", json={"conectado": False}))
    cuerpo = _error(cliente.post(RUTA, json={"nodo": "N03", "trampa": "doble_gasto"}), 409)
    assert cuerpo["codigo"] == "nodo_desconectado"


def test_http_dificultad_alta_409(monkeypatch):
    monkeypatch.setattr(modulo_simulador, "MAX_INTENTOS_TRAMPOSO", 3)
    cliente = crear_cliente(dificultad=6)
    antes = _estado(cliente)
    cuerpo = _error(cliente.post(RUTA, json={"nodo": "N01", "trampa": "recompensa_falsa"}), 409)
    assert cuerpo["codigo"] == "dificultad_alta"
    assert cuerpo["detalles"] == {"intentos": 3, "dificultad": 6}
    despues = _estado(cliente)
    # La API sólo anota el rechazo en la bitácora ("entrada_rechazada"); lo demás queda igual.
    assert despues["eventos"][-1]["tipo"] == "entrada_rechazada"
    assert cuerpo["error"] in despues["eventos"][-1]["mensaje"]
    sin_bitacora = ("eventos", "ultimo_evento")
    assert {k: v for k, v in despues.items() if k not in sin_bitacora} == {
        k: v for k, v in antes.items() if k not in sin_bitacora}
