"""Pruebas de la API HTTP (app.py) con el cliente de pruebas de Flask, sin hilo motor.

Cubren los casos de la guía que se ven por HTTP (E1–E8, T1, T5–T7, W2, C4,
S2, S4, S5, A1–A3) y el fuzz A4: ninguna petición mal formada produce un 500,
una página HTML o un traceback; la respuesta siempre es JSON con
{"ok": false, "error": "..."} y el estado de la simulación queda íntegro.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

import pytest

from conftest import (
    CONFIG_POS,
    CONFIG_POW,
    SEMILLA,
    alterar_hex,
    crear_cliente,
    dejar_pasar_tiempo,
    extender_cadena,
    firmar_tx,
    normalizar,
    timestamp_siguiente,
)

ESTADOS_ERROR = (400, 404, 405, 409, 413)
DOS_MB = 2 * 1024 * 1024


# ---------------------------------------------------------------------------
# Ayudas
# ---------------------------------------------------------------------------

def respuesta_json(resp) -> dict:
    """Cuerpo JSON de la respuesta; falla si es un 5xx o no es JSON."""
    assert resp.status_code < 500, f"HTTP {resp.status_code}: {resp.data[:300]!r}"
    datos = resp.get_json(silent=True)
    assert isinstance(datos, dict), (
        f"La respuesta no es JSON ({resp.status_code}, {resp.content_type}): {resp.data[:200]!r}"
    )
    return datos


def exito(resp) -> Any:
    """Exige una respuesta de éxito del contrato y devuelve su campo "datos"."""
    cuerpo = respuesta_json(resp)
    assert resp.status_code == 200 and cuerpo.get("ok") is True, cuerpo
    assert isinstance(cuerpo.get("mensaje"), str)
    assert isinstance(cuerpo.get("version"), int)
    return cuerpo["datos"]


def problema_de_error(resp, estados: tuple[int, ...] = ESTADOS_ERROR) -> str | None:
    """Describe por qué `resp` no es un error JSON bien formado (None si lo es)."""
    if resp.status_code >= 500:
        return f"HTTP {resp.status_code}: {resp.data[:200]!r}"
    cuerpo = resp.get_json(silent=True)
    if not isinstance(cuerpo, dict):
        return f"respuesta no JSON (HTTP {resp.status_code}, {resp.content_type}): {resp.data[:120]!r}"
    if cuerpo.get("ok") is not False:
        return f"se esperaba ok=false y llegó ok={cuerpo.get('ok')!r} (HTTP {resp.status_code})"
    if not (isinstance(cuerpo.get("error"), str) and cuerpo["error"].strip()):
        return f"falta el mensaje de error: {cuerpo}"
    if not (isinstance(cuerpo.get("codigo"), str) and cuerpo["codigo"]):
        return f"falta el código de error: {cuerpo}"
    if resp.status_code not in estados:
        return f"HTTP {resp.status_code} inesperado (se esperaba {estados}): {cuerpo.get('error')}"
    return None


def problema_de_recibir(resp) -> str | None:
    """/recibir con una cadena mala: 200 con acepto=false, o un error 400/404 JSON."""
    if resp.status_code == 200:
        cuerpo = resp.get_json(silent=True)
        if not (isinstance(cuerpo, dict) and cuerpo.get("ok") is True):
            return f"200 sin ok=true: {resp.data[:200]!r}"
        if cuerpo["datos"].get("acepto") is not False:
            return f"se aceptó una cadena inválida: {cuerpo['datos']}"
        return None
    return problema_de_error(resp, (400, 404))


def assert_error(resp, estados: tuple[int, ...] = ESTADOS_ERROR) -> dict:
    """Exige un error JSON del contrato y devuelve el cuerpo."""
    problema = problema_de_error(resp, estados)
    assert problema is None, problema
    return resp.get_json()


def estado(cliente) -> dict:
    """Instantánea de GET /api/estado."""
    return exito(cliente.get("/api/estado"))


def cadena_de(cliente, id_nodo: str = "N01") -> list[dict]:
    """Cadena completa de un nodo (acepta datos como lista o como {"cadena": [...]})."""
    datos = exito(cliente.get(f"/api/nodos/{id_nodo}/cadena"))
    return datos if isinstance(datos, list) else datos["cadena"]


def nodo_de(datos_estado: dict, id_nodo: str) -> dict:
    """Entrada de un nodo en la instantánea."""
    return next(n for n in datos_estado["nodos"] if n["id"] == id_nodo)


def crear_tx(cliente, emisor: str = "N01", receptor: str = "N02", monto: Any = 5):
    """POST /api/transacciones."""
    return cliente.post("/api/transacciones", json={"emisor": emisor, "receptor": receptor, "monto": monto})


def avanzar(cliente, veces: int) -> None:
    """Avanza la ronda PoS `veces` transiciones por HTTP."""
    for _ in range(veces):
        exito(cliente.post("/api/pos/avanzar", json={}))


def assert_estado_integro(cliente, modo: str) -> None:
    """Tras peticiones rechazadas, la simulación sigue como recién creada e íntegra."""
    datos = estado(cliente)
    assert datos["invariantes_ok"] is True
    assert datos["modo"] == modo
    assert datos["altura_red"] == 0 and datos["sincronizados"] is True
    assert datos["pendientes"] == [] and datos["castigos_pendientes"] == []
    assert all(n["conectado"] and not n["deshonesto"] for n in datos["nodos"])
    assert datos["config"]["semilla"] == SEMILLA and len(datos["nodos"]) == 10
    assert datos["pow"]["estado"] == "inactivo" and datos["pos"]["estado"] == "INACTIVA"


def profunda(niveles: int) -> str:
    """JSON de una lista anidada `niveles` veces."""
    return "[" * niveles + "]" * niveles


def cliente_de(modo: str):
    """Cliente PoW o PoS."""
    return crear_cliente() if modo == "pow" else crear_cliente(**CONFIG_POS)


# ---------------------------------------------------------------------------
# Forma de las respuestas y página
# ---------------------------------------------------------------------------

def test_a2_pagina_principal_responde(cliente):
    resp = cliente.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.content_type


def test_estado_tiene_la_forma_del_contrato(cliente):
    datos = estado(cliente)
    for clave in ("version", "modo", "config", "altura_red", "sincronizados", "hash_red", "nodos", "pendientes",
                  "castigos_pendientes", "pow", "pos", "eventos", "ultimo_evento", "invariantes_ok", "circulacion"):
        assert clave in datos, f"Falta {clave} en el estado"
    assert len(datos["nodos"]) == 10
    for clave in ("id", "indice", "altura", "ultimo_hash", "sincronizado", "conectado", "deshonesto", "trampa",
                  "saldo_cadena", "pendiente_salida", "apuesta_bloqueada", "castigo_pendiente", "disponible",
                  "recompensas_pendientes", "total_recompensas_pendientes", "minero", "validador"):
        assert clave in datos["nodos"][0], f"Falta {clave} en el nodo"
    assert datos["circulacion"]["total"] == 1000 and datos["circulacion"]["quemado"] == 0
    assert exito(cliente.get("/api/nodos/N01"))
    assert exito(cliente.get("/api/nodos/n1"))
    assert len(cadena_de(cliente)) == 1
    assert isinstance(exito(cliente.get("/api/bitacora?desde=0")), (dict, list))


def test_respuesta_exitosa_y_version(cliente):
    version = estado(cliente)["version"]
    cuerpo = respuesta_json(crear_tx(cliente))
    assert cuerpo["ok"] is True and cuerpo["version"] > version
    assert len(estado(cliente)["pendientes"]) == 1


# ---------------------------------------------------------------------------
# E — entradas inválidas por HTTP
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("valor", [9, 21, "abc", "", 10.5])
def test_e1_n_fuera_de_rango_http(cliente, valor):
    assert_error(cliente.post("/api/simulacion", json={**CONFIG_POW, "num_nodos": valor}), (400,))
    assert len(estado(cliente)["nodos"]) == 10


def test_e1_n_valido_reinicia_http(cliente):
    exito(cliente.post("/api/simulacion", json={**CONFIG_POW, "num_nodos": 12}))
    datos = estado(cliente)
    assert len(datos["nodos"]) == 12 and datos["config"]["num_nodos"] == 12


@pytest.mark.parametrize("monto", [-5, "-5"])
def test_e2_monto_negativo_http(cliente, monto):
    cuerpo = assert_error(crear_tx(cliente, monto=monto), (400,))
    assert "negativo" in normalizar(cuerpo["error"])
    assert estado(cliente)["pendientes"] == []


def test_e3_monto_cero_http(cliente):
    cuerpo = assert_error(crear_tx(cliente, monto=0), (400,))
    assert "cero" in normalizar(cuerpo["error"])


@pytest.mark.parametrize("monto", ["abc", [5], {"a": 1}, True, 3.5, "1e3", "12abc"])
def test_e4_monto_no_numerico_http(cliente, monto):
    assert_error(crear_tx(cliente, monto=monto), (400,))
    assert estado(cliente)["pendientes"] == []


def test_e5_monto_vacio_o_ausente_http(cliente):
    for cuerpo in ({"emisor": "N01", "receptor": "N02", "monto": ""},
                   {"emisor": "N01", "receptor": "N02", "monto": None},
                   {"emisor": "N01", "receptor": "N02"}):
        assert_error(cliente.post("/api/transacciones", json=cuerpo), (400,))
    assert estado(cliente)["pendientes"] == []


def test_e6_emisor_igual_a_receptor_http(cliente):
    assert_error(crear_tx(cliente, "N01", "N01"), (400, 409))
    assert_error(crear_tx(cliente, "n1", "N01"), (400, 409))
    assert estado(cliente)["pendientes"] == []


def test_e7_nodos_inexistentes_http(cliente):
    for resp in (
        crear_tx(cliente, "N99", "N01"),
        crear_tx(cliente, "N01", "N99"),
        cliente.get("/api/nodos/N99"),
        cliente.get("/api/nodos/N99/cadena"),
        cliente.post("/api/nodos/N99/conexion", json={"conectado": False}),
        cliente.post("/api/nodos/N99/deshonesto", json={"deshonesto": True, "trampa": "recompensa_falsa"}),
    ):
        cuerpo = assert_error(resp, (404,))
        assert cuerpo["codigo"] == "nodo_inexistente"
    assert_error(cliente.post("/api/nodos/N99/recibir", json={"cadena": []}), (400, 404))
    assert_estado_integro(cliente, "pow")


@pytest.mark.parametrize("valor", [0, 7, "x", "", 2.5])
def test_e8_dificultad_fuera_de_rango_http(cliente, valor):
    assert_error(cliente.post("/api/simulacion", json={**CONFIG_POW, "dificultad": valor}), (400,))
    assert estado(cliente)["config"]["dificultad"] == CONFIG_POW["dificultad"]


# ---------------------------------------------------------------------------
# T, W, S y C por HTTP
# ---------------------------------------------------------------------------

def test_t1_t5_transaccion_firmada_http(cliente):
    genesis = cadena_de(cliente)[0]
    tx = firmar_tx(SEMILLA, "N01", "N02", 7, genesis["timestamp"])
    alterada = {**tx, "firma": alterar_hex(tx["firma"])}
    cuerpo = assert_error(cliente.post("/api/transacciones/firmada", json=alterada), (400,))
    assert cuerpo["codigo"] == "firma_invalida"
    otra_clave = firmar_tx(SEMILLA, "N01", "N02", 8, genesis["timestamp"], firmante="N03")
    cuerpo = assert_error(cliente.post("/api/transacciones/firmada", json=otra_clave), (400,))
    assert cuerpo["codigo"] == "firma_invalida"
    exito(cliente.post("/api/transacciones/firmada", json=tx))
    cuerpo = assert_error(cliente.post("/api/transacciones/firmada", json=tx), (409,))
    assert cuerpo["codigo"] == "doble_gasto"
    assert [p["id"] for p in estado(cliente)["pendientes"]] == [tx["id"]]


def test_t1_ataque_firma_alterada_http(cliente):
    datos = exito(cliente.post("/api/ataques/transaccion",
                               json={"tipo": "firma_alterada", "emisor": "N01", "receptor": "N02", "monto": 10}))
    assert datos["aceptadas"] == []
    assert [r["codigo"] for r in datos["rechazadas"]] == ["firma_invalida"]
    assert_estado_integro(cliente, "pow")


def test_t3_saldo_insuficiente_http(cliente):
    cuerpo = assert_error(crear_tx(cliente, monto=101), (400,))
    assert cuerpo["codigo"] == "saldo_insuficiente"


def test_t6_minar_sin_pendientes_http(cliente):
    cuerpo = assert_error(cliente.post("/api/pow/minar", json={}), (409,))
    assert cuerpo["codigo"] == "sin_pendientes"
    assert_estado_integro(cliente, "pow")


def test_t7_proponer_sin_pendientes_http(cliente_pos):
    cuerpo = assert_error(cliente_pos.post("/api/pos/ronda", json={}), (409,))
    assert cuerpo["codigo"] == "sin_pendientes"
    assert_estado_integro(cliente_pos, "pos")


def test_w2_minar_mientras_se_mina_http(cliente):
    exito(crear_tx(cliente))
    exito(cliente.post("/api/pow/minar", json={"bloques": 1}))
    assert_error(cliente.post("/api/pow/minar", json={"bloques": 1}), (409,))
    assert_error(cliente.post("/api/pow/minar", json={"bloques": 3, "auto_tx": True}), (409,))
    assert estado(cliente)["pow"]["estado"] == "minando"
    exito(cliente.post("/api/pow/cancelar"))
    assert estado(cliente)["pow"]["estado"] == "cancelada"
    assert_error(cliente.post("/api/pow/cancelar"), (409,))
    assert estado(cliente)["invariantes_ok"] is True


def test_s2_apuesta_invalida_http(cliente_pos):
    exito(crear_tx(cliente_pos))
    exito(cliente_pos.post("/api/pos/ronda", json={}))
    cuerpo = assert_error(cliente_pos.post("/api/pos/apuestas", json={"apuestas": {"N02": 500}}), (400, 409))
    assert "saldo" in normalizar(cuerpo["error"])
    for monto in (0, -5, "abc", None, 2.5):
        assert_error(cliente_pos.post("/api/pos/apuestas", json={"apuestas": {"N02": monto}}), (400, 409))
    exito(cliente_pos.post("/api/pos/apuestas", json={"apuestas": {"n2": "7"}}))
    assert nodo_de(estado(cliente_pos), "N02")["validador"]["apuesta"] == 7


def test_s4_s5_votos_invalidos_http(cliente_pos):
    exito(cliente_pos.post("/api/simulacion", json={**CONFIG_POW, **CONFIG_POS, "seleccion_validadores": "aleatorio",
                                                    "num_validadores": 3}))
    exito(crear_tx(cliente_pos))
    exito(cliente_pos.post("/api/pos/ronda", json={}))
    assert_error(cliente_pos.post("/api/pos/votar", json={"nodo": "N01", "voto": True}), (409,))
    avanzar(cliente_pos, 3)
    datos = estado(cliente_pos)
    assert datos["pos"]["estado"] == "VOTACION"
    no_validador = next(n["id"] for n in datos["nodos"] if n["validador"] is None)
    validador = next(n["id"] for n in datos["nodos"] if n["validador"] is not None)
    cuerpo = assert_error(cliente_pos.post("/api/pos/votar", json={"nodo": no_validador, "voto": True}), (409,))
    assert cuerpo["codigo"] == "voto_invalido"
    cuerpo = assert_error(cliente_pos.post("/api/pos/votar", json={"nodo": validador, "voto": False}), (409,))
    assert cuerpo["codigo"] == "voto_duplicado"
    avanzar(cliente_pos, 1)
    datos = estado(cliente_pos)
    assert datos["pos"]["estado"] == "ACEPTADO" and datos["altura_red"] == 1 and datos["invariantes_ok"]


def test_c1_ataque_alterar_bloque_http(cliente):
    datos = exito(cliente.post("/api/ataques/alterar-bloque", json={"nodo": "N01", "numero": 0, "tipo": "hash"}))
    assert datos["rechazos"] == 9
    assert_estado_integro(cliente, "pow")


CADENAS_MALAS_JSON = {
    "none": "null",
    "texto": '"texto"',
    "numero": "5",
    "objeto": "{}",
    "lista_vacia": "[]",
    "lista_con_null": "[null]",
    "lista_con_objeto_vacio": "[{}]",
    "lista_anidada": "[[]]",
    "lista_de_numeros": "[1, 2, 3]",
    "anidamiento_profundo": profunda(1500),
    "bloque_incompleto": '[{"numero": 0}]',
    "basura_larga": "[" + ",".join(["{}"] * 3000) + "]",
}


@pytest.mark.parametrize("nombre", sorted(CADENAS_MALAS_JSON))
def test_c4_recibir_cadena_malformada_http(cliente, nombre):
    crudo = '{"cadena": ' + CADENAS_MALAS_JSON[nombre] + "}"
    resp = cliente.post("/api/nodos/N02/recibir", data=crudo, content_type="application/json")
    problema = problema_de_recibir(resp)
    assert problema is None, f"{nombre}: {problema}"
    assert_estado_integro(cliente, "pow")


def test_c4_recibir_cadena_con_bloques_malos_http(cliente):
    genesis = cadena_de(cliente)[0]
    # Pasa un minuto en el reloj de la red: el bloque fabricado aquí no queda en el futuro (§21.2).
    dejar_pasar_tiempo(cliente.application.extensions["gestor"].actual())
    extendida = extender_cadena([genesis], [firmar_tx(SEMILLA, "N10", "N09", 1, timestamp_siguiente([genesis]))])
    malas = [
        [genesis, {**extendida[1], "hash": "f" * 64}],
        [genesis, {**extendida[1], "recompensa": {"beneficiario": "N05", "monto": 500}}],
        [genesis, {**extendida[1], "numero": True}],
        [genesis, {**extendida[1], "transacciones": "x"}],
        [{**genesis, "saldos_iniciales": {}}, extendida[1]],
        [genesis, extendida[1], extendida[1]],
        [genesis, {k: v for k, v in extendida[1].items() if k != "firma"}],
    ]
    for indice, mala in enumerate(malas):
        problema = problema_de_recibir(cliente.post("/api/nodos/N02/recibir", json={"cadena": mala}))
        assert problema is None, f"caso {indice}: {problema}"
    assert_estado_integro(cliente, "pow")
    # Control positivo: la cadena válida y más larga sí se acepta.
    datos = exito(cliente.post("/api/nodos/N02/recibir", json={"cadena": extendida}))
    assert datos["acepto"] is True
    final = estado(cliente)
    # N02 la adopta y la difunde: los demás también la validan y la adoptan.
    assert nodo_de(final, "N02")["altura"] == 1 and final["altura_red"] == 1 and final["sincronizados"] is True
    assert final["invariantes_ok"] is True


# ---------------------------------------------------------------------------
# A1–A3 por HTTP
# ---------------------------------------------------------------------------

def test_a1_reiniciar_a_mitad_de_mineria_http(cliente):
    exito(crear_tx(cliente))
    exito(cliente.post("/api/pow/minar", json={"bloques": 5, "auto_tx": True}))
    assert estado(cliente)["pow"]["estado"] == "minando"
    exito(cliente.post("/api/simulacion", json={**CONFIG_POW, "num_nodos": 12, "semilla": "nueva"}))
    datos = estado(cliente)
    assert datos["pow"]["estado"] == "inactivo"
    assert datos["altura_red"] == 0 and datos["pendientes"] == []
    assert len(datos["nodos"]) == 12 and datos["config"]["semilla"] == "nueva"
    assert datos["invariantes_ok"] is True
    assert assert_error(cliente.post("/api/pow/minar", json={}), (409,))["codigo"] == "sin_pendientes"


def test_a1_reiniciar_con_config_invalida_conserva_la_simulacion(cliente):
    exito(crear_tx(cliente))
    for config in ({"num_nodos": 99}, {"modo": "otro"}, {"dificultad": "x"}, {"semilla": ""}):
        assert_error(cliente.post("/api/simulacion", json=config), (400,))
    datos = estado(cliente)
    assert len(datos["pendientes"]) == 1 and datos["config"]["semilla"] == SEMILLA


def test_a1_reiniciar_con_cuerpo_vacio_usa_valores_por_omision(cliente):
    exito(cliente.post("/api/simulacion", data=b"", content_type="application/json"))
    datos = estado(cliente)
    assert datos["config"]["num_nodos"] == 10 and datos["config"]["semilla"] == "anahuac"


def test_a2_recargar_a_mitad_de_ronda_pos(cliente_pos):
    exito(crear_tx(cliente_pos))
    exito(cliente_pos.post("/api/pos/ronda", json={}))
    avanzar(cliente_pos, 2)
    primera = estado(cliente_pos)
    assert cliente_pos.get("/").status_code == 200
    segunda = estado(cliente_pos)
    assert primera == segunda
    assert primera["pos"]["estado"] == "CANDIDATO"
    desde = exito(cliente_pos.get(f"/api/estado?desde={primera['ultimo_evento']}"))
    assert desde["eventos"] == []
    avanzar(cliente_pos, 2)
    final = estado(cliente_pos)
    assert final["pos"]["estado"] == "ACEPTADO" and final["altura_red"] == 1 and final["invariantes_ok"]


def test_a3_dos_pestanas_avanzan_la_ronda_http(cliente_pos):
    exito(crear_tx(cliente_pos))
    exito(cliente_pos.post("/api/pos/ronda", json={}))
    exito(cliente_pos.post("/api/pos/avanzar", json={"estado_esperado": "APUESTAS"}))
    cuerpo = assert_error(cliente_pos.post("/api/pos/avanzar", json={"estado_esperado": "APUESTAS"}), (409,))
    assert cuerpo["codigo"] == "estado_cambio"
    assert estado(cliente_pos)["pos"]["estado"] == "SORTEO"


# ---------------------------------------------------------------------------
# A4 — fuzz de datos mal formados
# ---------------------------------------------------------------------------

RUTAS_POST = (
    "/api/simulacion",
    "/api/transacciones",
    "/api/transacciones/firmada",
    "/api/transacciones/aleatorias",
    "/api/pow/minar",
    "/api/pow/cancelar",
    "/api/pos/ronda",
    "/api/pos/apuestas",
    "/api/pos/apostar-todo",
    "/api/pos/avanzar",
    "/api/pos/automatico",
    "/api/pos/votar",
    "/api/pos/cancelar",
    "/api/nodos/N01/deshonesto",
    "/api/nodos/N01/conexion",
    "/api/nodos/N01/recibir",
    "/api/ataques/alterar-bloque",
    "/api/ataques/cadena-corta",
    "/api/ataques/transaccion",
)

JSON = "application/json"
CUERPOS_MAL_FORMADOS: list[tuple[str, bytes, str]] = [
    ("json_invalido", b"{malo", JSON),
    ("comillas_simples", b"{'monto': 5}", JSON),
    ("truncado", b'{"emisor": "N01", "monto": ', JSON),
    ("doble_coma", b'{"a": 1,, "b": 2}', JSON),
    ("lista_vacia", b"[]", JSON),
    ("lista", b'[{"monto": 5}]', JSON),
    ("null", b"null", JSON),
    ("texto_json", b'"texto"', JSON),
    ("numero_json", b"123", JSON),
    ("booleano_json", b"true", JSON),
    ("nan_crudo", b'{"monto": NaN}', JSON),
    ("infinito_crudo", b'{"monto": Infinity, "cantidad": 1}', JSON),
    ("menos_infinito", b'{"cantidad": -Infinity}', JSON),
    ("anidamiento_lista", b"[" * 50_000, JSON),
    ("anidamiento_objeto", b'{"a":' * 50_000, JSON),
    ("cadena_1mb_cruda", b"a" * 1_000_000, JSON),
    ("cadena_1mb_json", json.dumps("a" * 1_000_000).encode(), JSON),
    ("utf8_invalido", b"\xff\xfe\xfa\x00", JSON),
    ("bytes_nulos", b"\x00\x00\x00", JSON),
    ("formulario", b"monto=5&emisor=N01", "application/x-www-form-urlencoded"),
    ("texto_plano", b"hola", "text/plain"),
]

MALOS_ENTERO = [True, [], {}, "abc", "1.5", 1.5, -1, 0, 10**13]
MALOS_ENTERO_OBLIGATORIO = MALOS_ENTERO + [None, ""]
MALOS_BOOL = [[], {}, "quizas", 2, -1, 1.5]
MALOS_BOOL_OBLIGATORIO = MALOS_BOOL + [None, ""]
MALOS_ID = [None, "", "N99", "abc", [], {}, 99, True, "x" * 1000]
MALOS_MONTO = [None, "", "abc", -5, 0, 1.5, "1.5", [], {}, True, 10**13, "1e3"]
MALOS_OPCION = [None, "", "inventado", 5, [], {}, True]

CAMPOS_CONFIG: dict[str, list] = {
    "modo": ["", "abc", 5, [], {}, True],
    "num_nodos": MALOS_ENTERO + ["", 9, 21],
    "semilla": ["", "x" * 65, 5, [], {}, "a\x00b"],
    "saldo_inicial": MALOS_ENTERO + [""],
    "recompensa": MALOS_ENTERO + [""],
    "max_tx_por_bloque": MALOS_ENTERO + ["", 51],
    "dificultad": MALOS_ENTERO + ["", 7],
    "intentos_por_ronda": MALOS_ENTERO + ["", 5001],
    "max_rondas": MALOS_ENTERO + [""],
    "intervalo_ms": MALOS_ENTERO + ["", 49, 2001],
    "seleccion_validadores": ["", "algunos", 1, [], {}],
    "num_validadores": MALOS_ENTERO + ["", 21],
    "regla_castigo": ["", "C", 1, [], {}],
    "alfa_porcentaje": MALOS_ENTERO + ["", 101],
    "reloj": ["", "lunar", 1, [], {}],
}


def especificacion(ruta: str, genesis: dict) -> tuple[dict, dict[str, list], tuple[str, ...]]:
    """(cuerpo válido de base, {campo: valores inválidos}, campos obligatorios) de una ruta POST."""
    tx = firmar_tx(SEMILLA, "N01", "N02", 5, genesis["timestamp"])
    malas_cadenas = [None, "", "texto", 5, {}, [], [None], [{}], [[]], ["x"], [1, 2], [genesis], [genesis, {}]]
    especificaciones: dict[str, tuple[dict, dict[str, list], tuple[str, ...]]] = {
        "/api/simulacion": ({}, CAMPOS_CONFIG, ()),
        "/api/transacciones": (
            {"emisor": "N01", "receptor": "N02", "monto": 5},
            {"emisor": MALOS_ID, "receptor": MALOS_ID + ["N01"], "monto": MALOS_MONTO},
            ("emisor", "receptor", "monto"),
        ),
        "/api/transacciones/firmada": (
            tx,
            {
                "emisor": MALOS_ID + ["N03"],
                "receptor": MALOS_ID + ["N03"],
                "monto": MALOS_MONTO + [6],
                "timestamp": [None, "", "abc", -1, 1.5, [], True, 10**16, genesis["timestamp"] + 1],
                "firma": [None, "", 5, [], "zz", "0" * 128, "A" * 128, tx["firma"][:-2], alterar_hex(tx["firma"])],
                "id": ["abc", 5, [], "0" * 64],
            },
            ("emisor", "receptor", "monto", "timestamp", "firma"),
        ),
        "/api/transacciones/aleatorias": ({"cantidad": 3}, {"cantidad": MALOS_ENTERO_OBLIGATORIO + [51]}, ("cantidad",)),
        "/api/pow/minar": ({"bloques": 1, "auto_tx": False}, {"bloques": MALOS_ENTERO + [51], "auto_tx": MALOS_BOOL}, ()),
        "/api/pow/cancelar": ({}, {}, ()),
        "/api/pos/ronda": ({"rondas": 1, "auto_tx": False}, {"rondas": MALOS_ENTERO, "auto_tx": MALOS_BOOL}, ()),
        "/api/pos/apuestas": (
            {"apuestas": {"N01": 5}},
            {"apuestas": [None, "", [], "abc", 5, {"N01": "abc"}, {"N01": -1}, {"N01": 0}, {"N99": 5},
                          {"N01": []}, {"N01": 10**13}, {"N01": None}, {"N01": 1.5}]},
            ("apuestas",),
        ),
        "/api/pos/apostar-todo": ({}, {}, ()),
        "/api/pos/avanzar": ({"estado_esperado": "APUESTAS"}, {"estado_esperado": [[], {}, 5, "NO_EXISTE", True]}, ()),
        "/api/pos/automatico": ({"activo": True}, {"activo": MALOS_BOOL_OBLIGATORIO}, ("activo",)),
        "/api/pos/votar": (
            {"nodo": "N01", "voto": True},
            {"nodo": MALOS_ID, "voto": MALOS_BOOL_OBLIGATORIO},
            ("nodo", "voto"),
        ),
        "/api/pos/cancelar": ({}, {}, ()),
        "/api/nodos/N01/deshonesto": (
            {"deshonesto": True, "trampa": "recompensa_falsa"},
            {"deshonesto": MALOS_BOOL_OBLIGATORIO, "trampa": MALOS_OPCION},
            ("deshonesto", "trampa"),
        ),
        "/api/nodos/N01/conexion": ({"conectado": False}, {"conectado": MALOS_BOOL_OBLIGATORIO}, ("conectado",)),
        "/api/nodos/N01/recibir": ({"cadena": [genesis]}, {"cadena": malas_cadenas}, ("cadena",)),
        "/api/ataques/alterar-bloque": (
            {"nodo": "N01", "numero": 0, "tipo": "hash"},
            {"nodo": MALOS_ID, "numero": [None, "", True, [], {}, "abc", "1.5", 1.5, -1, 5, 10**13],
             "tipo": MALOS_OPCION},
            ("nodo", "numero", "tipo"),
        ),
        "/api/ataques/cadena-corta": ({"nodo": "N01", "quitar": 1}, {"nodo": MALOS_ID, "quitar": MALOS_ENTERO}, ("nodo",)),
        "/api/ataques/transaccion": (
            {"tipo": "otra_clave", "emisor": "N01", "receptor": "N02", "monto": 5, "firmante": "N03"},
            {"tipo": MALOS_OPCION, "emisor": MALOS_ID, "receptor": MALOS_ID, "monto": MALOS_MONTO,
             "firmante": ["N99", "abc", [], {}, 99, True, "x" * 1000]},
            ("tipo", "emisor", "receptor", "monto"),
        ),
    }
    return especificaciones[ruta]


def _revisar(ruta: str, resp, revisar_recibir: bool = False) -> str | None:
    """Problema de una respuesta del fuzz (None si es un rechazo bien formado)."""
    if revisar_recibir and ruta.endswith("/recibir"):
        return problema_de_recibir(resp)
    return problema_de_error(resp)


@pytest.mark.parametrize("modo", ["pow", "pos"])
@pytest.mark.parametrize("ruta", RUTAS_POST)
def test_a4_cuerpos_mal_formados(ruta, modo):
    cliente = cliente_de(modo)
    problemas = []
    for nombre, crudo, tipo in CUERPOS_MAL_FORMADOS:
        resp = cliente.post(ruta, data=crudo, content_type=tipo)
        problema = problema_de_error(resp, (400,))
        if problema:
            problemas.append(f"{nombre}: {problema}")
    if ruta != "/api/simulacion":  # cuerpo vacío en /api/simulacion = reiniciar con valores por omisión
        resp = cliente.post(ruta, data=b"", content_type=JSON)
        problema = _revisar(ruta, resp, revisar_recibir=True)
        if problema:
            problemas.append(f"cuerpo vacío: {problema}")
    assert problemas == [], "\n".join(problemas)
    assert_estado_integro(cliente, modo)


@pytest.mark.parametrize("modo", ["pow", "pos"])
@pytest.mark.parametrize("ruta", RUTAS_POST)
def test_a4_tipos_incorrectos_y_campos_ausentes(ruta, modo):
    cliente = cliente_de(modo)
    base, campos, obligatorios = especificacion(ruta, cadena_de(cliente)[0])
    problemas = []
    for campo, valores in campos.items():
        for valor in valores:
            resp = cliente.post(ruta, json={**base, campo: valor})
            problema = _revisar(ruta, resp, revisar_recibir=True)
            if problema:
                problemas.append(f"{campo}={valor!r:.40}: {problema}")
    for campo in obligatorios:
        sin_campo = {k: v for k, v in base.items() if k != campo}
        problema = _revisar(ruta, cliente.post(ruta, json=sin_campo), revisar_recibir=True)
        if problema:
            problemas.append(f"sin {campo}: {problema}")
    if campos:
        primer_campo = next(iter(campos))
        con_texto_enorme = {**base, primer_campo: "a" * 1_000_000}
        problema = _revisar(ruta, cliente.post(ruta, json=con_texto_enorme), revisar_recibir=True)
        if problema:
            problemas.append(f"{primer_campo} de 1 MB: {problema}")
    assert problemas == [], "\n".join(problemas)
    assert_estado_integro(cliente, modo)


@pytest.mark.parametrize("ruta", ["/api/transacciones", "/api/simulacion", "/api/nodos/N01/recibir"])
def test_a4_cuerpo_mayor_a_2_mb(cliente, ruta):
    crudo = b'{"cadena": "' + b"x" * (DOS_MB + 1024) + b'"}'
    assert_error(cliente.post(ruta, data=crudo, content_type=JSON), (413,))
    assert_estado_integro(cliente, "pow")


@pytest.mark.parametrize(
    "metodo, ruta",
    [("GET", "/api/no-existe"), ("POST", "/api/pow/minarr"), ("GET", "/api/nodos/N01/otra"),
     ("POST", "/api/ataques/inventado"), ("GET", "/api/"), ("DELETE", "/api/x/y/z")],
)
def test_a4_rutas_inexistentes(cliente, metodo, ruta):
    assert_error(cliente.open(ruta, method=metodo), (404,))


@pytest.mark.parametrize(
    "metodo, ruta",
    [("GET", "/api/transacciones"), ("GET", "/api/pow/minar"), ("PUT", "/api/simulacion"),
     ("DELETE", "/api/estado"), ("PATCH", "/api/nodos/N01"), ("POST", "/api/estado"),
     ("POST", "/api/bitacora"), ("DELETE", "/api/nodos/N01/cadena"), ("GET", "/api/pos/avanzar")],
)
def test_a4_metodos_incorrectos(cliente, metodo, ruta):
    assert_error(cliente.open(ruta, method=metodo, json={}), (405,))
    assert_estado_integro(cliente, "pow")


@pytest.mark.parametrize("id_nodo", ["N99", "abc", "0", "N1000", "%20", "N01%00", "-1", "N%C3%B1", "x" * 300])
def test_a4_ids_de_nodo_invalidos_en_la_ruta(cliente, id_nodo):
    peticiones: list[Callable[[], Any]] = [
        lambda: cliente.get(f"/api/nodos/{id_nodo}"),
        lambda: cliente.get(f"/api/nodos/{id_nodo}/cadena"),
        lambda: cliente.post(f"/api/nodos/{id_nodo}/deshonesto", json={"deshonesto": False}),
        lambda: cliente.post(f"/api/nodos/{id_nodo}/conexion", json={"conectado": True}),
        lambda: cliente.post(f"/api/nodos/{id_nodo}/recibir", json={"cadena": []}),
    ]
    for peticion in peticiones:
        assert_error(peticion(), (400, 404))
    assert_estado_integro(cliente, "pow")


@pytest.mark.parametrize(
    "ruta",
    ["/api/estado?desde=abc", "/api/estado?desde=-5", "/api/estado?desde=99999999999999999999",
     "/api/estado?desde=1.5", "/api/estado?desde=", "/api/estado?desde=%00", "/api/bitacora?desde=abc",
     "/api/bitacora?desde=-1", "/api/bitacora?desde=" + "9" * 400],
)
def test_a4_parametros_de_consulta_invalidos(cliente, ruta):
    resp = cliente.get(ruta)
    cuerpo = respuesta_json(resp)
    if cuerpo.get("ok") is False:
        assert_error(resp, (400,))
    else:
        assert resp.status_code == 200


def test_a4_nan_e_infinito_nunca_entran_al_estado(cliente):
    for crudo in (b'{"emisor": "N01", "receptor": "N02", "monto": NaN}',
                  b'{"emisor": "N01", "receptor": "N02", "monto": Infinity}',
                  b'{"emisor": "N01", "receptor": "N02", "monto": 1e400}'):
        assert_error(cliente.post("/api/transacciones", data=crudo, content_type=JSON), (400,))
    assert_estado_integro(cliente, "pow")


# ---------------------------------------------------------------------------
# Motor (hilo) — una sola prueba con motor activo
# ---------------------------------------------------------------------------

def _esperar_estado(cliente, condicion: Callable[[dict], bool], segundos: float = 20.0) -> dict:
    """Sondea /api/estado (como el navegador) hasta que se cumpla la condición."""
    limite = time.monotonic() + segundos
    while True:
        datos = estado(cliente)
        if condicion(datos):
            return datos
        assert time.monotonic() < limite, f"El motor no avanzó a tiempo: pow={datos['pow']} pos={datos['pos']}"
        time.sleep(0.05)


def test_motor_avanza_mineria_y_ronda_pos_por_si_solo():
    from app import create_app

    aplicacion = create_app(config_inicial={**CONFIG_POW}, motor_activo=True)
    cliente = aplicacion.test_client()
    exito(crear_tx(cliente))
    exito(cliente.post("/api/pow/minar", json={}))
    datos = _esperar_estado(cliente, lambda e: e["altura_red"] == 1 and e["pow"]["estado"] != "minando")
    assert datos["sincronizados"] and datos["invariantes_ok"]

    exito(cliente.post("/api/simulacion", json={**CONFIG_POW, **CONFIG_POS}))
    exito(crear_tx(cliente))
    exito(cliente.post("/api/pos/ronda", json={}))
    exito(cliente.post("/api/pos/automatico", json={"activo": True}))
    datos = _esperar_estado(cliente, lambda e: e["pos"]["estado"] == "ACEPTADO")
    assert datos["altura_red"] == 1 and datos["invariantes_ok"]
