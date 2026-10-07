"""Pruebas unitarias del núcleo base: errores, entradas, config, cripto, reloj,
bloque, reglas, bitácora y trampas."""

import hashlib
import json

import pytest

from nucleo.bitacora import TIPOS, Bitacora
from nucleo.bloque import (
    CAMPOS_BLOQUE,
    CAMPOS_GENESIS,
    HASH_CERO,
    crear_genesis,
    crear_transaccion,
    hash_bloque,
    hash_sin_votos,
    id_transaccion,
    nuevo_bloque,
    payload,
    resumen_bloque,
    serializar,
    validar_estructura_bloque,
    validar_estructura_tx,
)
from nucleo.config import CONFIRMACIONES_POW, Config, validar_config
from nucleo.cripto import es_hex, firmar, generar_claves, verificar
from nucleo.entradas import (
    MAX_ENTERO,
    exigir_objeto,
    leer_bool,
    leer_entero,
    leer_id_nodo,
    leer_monto,
    leer_opcion,
    leer_texto,
    texto_visible,
)
from nucleo.errores import Conflicto, EntradaInvalida, ErrorInterno, ErrorSimulacion, NoEncontrado
from nucleo.reglas import (
    alcanza_umbral,
    calcular_castigo,
    cumple_dificultad,
    mensaje_voto,
    probabilidades,
    sortear,
    verificar_votos_bloque,
)
from nucleo.reloj import INICIO_SIMULADO_MS, Reloj
from nucleo.trampas import TRAMPAS, aplicar_trampa_recompensa, transacciones_tramposas

IDS = [f"N{i:02d}" for i in range(1, 11)]


# ---------------------------------------------------------------- utilidades

def lista_anidada(niveles: int = 10_000) -> list:
    valor: list = []
    for _ in range(niveles):
        valor = [valor]
    return valor


def serializar_guia(tx):  # copia literal de la guía (sección 2.2)
    datos = {k: tx[k] for k in ("emisor", "receptor", "monto", "timestamp")}
    return json.dumps(datos, sort_keys=True, separators=(",", ":")).encode()


def hash_bloque_guia(b):  # copia literal de la guía (sección 2.3)
    datos = {k: v for k, v in b.items() if k != "hash"}
    return hashlib.sha256(json.dumps(datos, sort_keys=True).encode()).hexdigest()


@pytest.fixture(scope="module")
def claves():
    """Claves de N01..N10 con la semilla de prueba: {id: (privada, publica_hex)}."""
    return {i: generar_claves("prueba", i) for i in IDS}


@pytest.fixture(scope="module")
def directorio(claves):
    return {i: claves[i][1] for i in IDS}


def bloque_pos(claves, apuestas: dict[str, int], a_favor: set[str]) -> dict:
    """Bloque PoS con una transacción y votos firmados de todos los validadores."""
    tx = crear_transaccion("N01", "N02", 5, INICIO_SIMULADO_MS + 1000, claves["N01"][0])
    validadores = [{"id": i, "apuesta": a} for i, a in sorted(apuestas.items())]
    bloque = nuevo_bloque(1, INICIO_SIMULADO_MS + 2000, [tx], "a" * 64, validadores[0]["id"], 50, "pos",
                          validadores=validadores)
    candidato = hash_sin_votos(bloque)
    bloque["votos"] = [
        {"validador": v["id"], "peso": v["apuesta"], "voto": v["id"] in a_favor,
         "firma": firmar(claves[v["id"]][0], mensaje_voto(candidato, v["id"], v["id"] in a_favor))}
        for v in validadores
    ]
    bloque["hash"] = hash_bloque(bloque)
    return bloque


# ---------------------------------------------------------------- errores

def test_errores_codigos_http_y_a_dict():
    assert (EntradaInvalida.http, NoEncontrado.http, Conflicto.http, ErrorInterno.http) == (400, 404, 409, 500)
    error = NoEncontrado("No existe", detalles={"id": "N99"})
    assert isinstance(error, ErrorSimulacion)
    assert error.a_dict() == {"ok": False, "error": "No existe", "codigo": "no_encontrado",
                              "detalles": {"id": "N99"}}
    assert Conflicto("x", codigo="voto_duplicado").codigo == "voto_duplicado"
    assert EntradaInvalida("x").a_dict()["detalles"] == {}
    assert str(EntradaInvalida("mensaje claro")) == "mensaje claro"


# ---------------------------------------------------------------- entradas

@pytest.mark.parametrize("valor, esperado", [(3, 3), (3.0, 3), ("3", 3), (" 3 ", 3), ("-4", -4), ("+7", 7)])
def test_leer_entero_acepta(valor, esperado):
    assert leer_entero(valor, "El valor", -10, 10) == esperado


@pytest.mark.parametrize("valor, fragmento", [
    (True, "debe ser un número entero (recibido: True)"),
    (False, "debe ser un número entero (recibido: False)"),
    (3.5, "sin decimales"),
    ("3.5", "sin decimales"),
    ("abc", "debe ser un número entero (recibido: 'abc')"),
    (None, "es obligatorio"),
    ("", "es obligatorio"),
    ("   ", "es obligatorio"),
    ([], "debe ser un número entero (recibido: [])"),
    ({}, "debe ser un número entero (recibido: {})"),
    (float("nan"), "debe ser un número entero"),
    (float("inf"), "debe ser un número entero"),
    (float("-inf"), "debe ser un número entero"),
    (10**20, "debe estar entre 0 y 10 (recibido: 100000000000000000000)"),
    ("-4", "debe estar entre 0 y 10 (recibido: -4)"),
    ("1e3", "debe ser un número entero"),
    ("3,5", "debe ser un número entero"),
])
def test_leer_entero_rechaza(valor, fragmento):
    with pytest.raises(EntradaInvalida) as info:
        leer_entero(valor, "El valor", 0, 10)
    assert info.value.mensaje.startswith("El valor ")
    assert fragmento in info.value.mensaje


@pytest.mark.parametrize("valor", [
    10**5000, "9" * 5000, lista_anidada(), {"a": lista_anidada()}, object(), b"3", 1e300, "\ud800", "3\x00",
], ids=lambda valor: type(valor).__name__)
def test_leer_entero_valores_absurdos_solo_lanza_entrada_invalida(valor):
    with pytest.raises(EntradaInvalida) as info:
        leer_entero(valor, "El valor", 0, 10)
    assert len(info.value.mensaje) < 200


@pytest.mark.parametrize("valor, mensaje", [
    (-5, "El monto no puede ser negativo (recibido: -5)"),
    ("-5", "El monto no puede ser negativo (recibido: -5)"),
    (0, "El monto debe ser mayor que cero"),
    ("0", "El monto debe ser mayor que cero"),
    ("", "El monto es obligatorio"),
    (None, "El monto es obligatorio"),
    ("abc", "El monto debe ser un número entero (recibido: 'abc')"),
    (2.5, "El monto debe ser un número entero, sin decimales"),
    ("2.5", "El monto debe ser un número entero, sin decimales"),
    (True, "El monto debe ser un número entero (recibido: True)"),
    (MAX_ENTERO + 1, "El monto es demasiado grande (máximo 1000000000000)"),
    ("99999999999999999999", "El monto es demasiado grande (máximo 1000000000000)"),
])
def test_leer_monto_mensajes(valor, mensaje):
    with pytest.raises(EntradaInvalida) as info:
        leer_monto(valor)
    assert info.value.mensaje == mensaje


def test_leer_monto_acepta():
    assert leer_monto("25") == 25
    assert leer_monto(25.0) == 25
    assert leer_monto(MAX_ENTERO) == MAX_ENTERO


def test_leer_texto_opcion_bool_y_objeto():
    assert leer_texto("  hola ", "La semilla") == "hola"
    assert leer_texto(None, "El motivo", obligatorio=False) == ""
    for valor, fragmento in [(None, "es obligatorio"), ("  ", "es obligatorio"), (5, "debe ser texto"),
                             ("x" * 65, "demasiado largo"), ("a\x00b", "caracteres no permitidos"),
                             ("a\ud800", "caracteres no permitidos")]:
        with pytest.raises(EntradaInvalida, match=fragmento):
            leer_texto(valor, "La semilla")

    assert leer_opcion("POS", "El modo", ("pow", "pos")) == "pos"
    assert leer_opcion(" b ", "La regla", ("A", "B")) == "B"
    with pytest.raises(EntradaInvalida, match=r"debe ser uno de: A, B \(recibido: 'C'\)"):
        leer_opcion("C", "La regla", ("A", "B"))
    with pytest.raises(EntradaInvalida):
        leer_opcion(lista_anidada(), "La regla", ("A", "B"))

    for valor, esperado in [(True, True), (False, False), (1, True), (0, False), ("true", True),
                            ("Sí", True), ("si", True), ("NO", False), ("false", False)]:
        assert leer_bool(valor, "Conectado") is esperado
    for valor in (2, "quizá", None, [], 1.0):
        with pytest.raises(EntradaInvalida, match="debe ser verdadero o falso"):
            leer_bool(valor, "Conectado")

    assert exigir_objeto({"a": 1}) == {"a": 1}
    for valor in ([], "texto", 5, None):
        with pytest.raises(EntradaInvalida, match="objeto JSON"):
            exigir_objeto(valor)


@pytest.mark.parametrize("valor", ["N3", "n03", "3", 3, " N03 ", "N0003"])
def test_leer_id_nodo_normaliza(valor):
    assert leer_id_nodo(valor, "El emisor", IDS) == "N03"


def test_leer_id_nodo_inexistente_y_vacio():
    with pytest.raises(NoEncontrado) as info:
        leer_id_nodo("N99", "El emisor", IDS)
    assert info.value.codigo == "nodo_inexistente"
    assert info.value.mensaje == "El emisor: el nodo 'N99' no existe (nodos válidos: N01–N10)"
    for valor in (11, 0, -3, "N0", "abc", "N1000000", 10**5000):
        with pytest.raises(NoEncontrado):
            leer_id_nodo(valor, "El emisor", IDS)
    for valor in ("", None, "  ", True, 3.0, [], {}, lista_anidada()):
        with pytest.raises(EntradaInvalida):
            leer_id_nodo(valor, "El emisor", IDS)


def test_leer_id_nodo_repite_el_texto_de_forma_segura():
    """Un sustituto suelto ("\\ud800", válido en JSON) o un carácter de control no llegan crudos al mensaje."""
    for valor, visible in ((json.loads('"\\ud800"'), "\\ud800"), ("N9\x1b[31m", "N9\\x1b[31m")):
        with pytest.raises(NoEncontrado) as info:
            leer_id_nodo(valor, "El emisor", IDS)
        assert info.value.mensaje == f"El emisor: el nodo '{visible}' no existe (nodos válidos: N01–N10)"
        assert info.value.mensaje.encode("utf-8")


def test_texto_visible():
    assert texto_visible("Ñandú N03") == "Ñandú N03"              # los acentos se quedan
    assert texto_visible("a\nb\x00") == "a\\nb\\x00"
    assert texto_visible(json.loads('"x\\udfffy"')) == "x\\udfffy"
    assert texto_visible("z" * 100) == "z" * 40 and texto_visible("z" * 100, 5) == "zzzzz"


# ---------------------------------------------------------------- config

def test_validar_config_por_omision():
    assert validar_config(None) == Config()
    assert validar_config({}) == Config()
    assert validar_config({"desconocida": 1}) == Config()
    assert Config().a_dict()["num_nodos"] == 10
    assert CONFIRMACIONES_POW == 6


def test_validar_config_convierte_textos():
    config = validar_config({"modo": "POS", "num_nodos": "12", "regla_castigo": "b", "semilla": " x ",
                             "seleccion_validadores": "aleatorio", "num_validadores": 12.0})
    assert (config.modo, config.num_nodos, config.regla_castigo, config.semilla) == ("pos", 12, "B", "x")
    assert config.num_validadores == 12


@pytest.mark.parametrize("datos, mensaje", [
    ({"num_nodos": 9}, "El número de nodos debe estar entre 10 y 20 (recibido: 9)"),
    ({"num_nodos": 21}, "El número de nodos debe estar entre 10 y 20 (recibido: 21)"),
    ({"num_nodos": "abc"}, "El número de nodos debe ser un número entero (recibido: 'abc')"),
    ({"num_nodos": ""}, "El número de nodos es obligatorio"),
    ({"num_nodos": 10.5}, "El número de nodos debe ser un número entero, sin decimales"),
    ({"dificultad": 0}, "La dificultad debe estar entre 1 y 6 (recibido: 0)"),
    ({"dificultad": 7}, "La dificultad debe estar entre 1 y 6 (recibido: 7)"),
    ({"regla_castigo": "C"}, "La regla de castigo debe ser uno de: A, B (recibido: 'C')"),
    ({"alfa_porcentaje": 0}, "El porcentaje alfa debe estar entre 1 y 100 (recibido: 0)"),
    ({"alfa_porcentaje": 101}, "El porcentaje alfa debe estar entre 1 y 100 (recibido: 101)"),
    ({"num_nodos": 10, "num_validadores": 15},
     "El número de validadores (15) no puede ser mayor que el número de nodos (10)"),
])
def test_validar_config_rechaza(datos, mensaje):
    with pytest.raises(EntradaInvalida) as info:
        validar_config(datos)
    assert info.value.mensaje == mensaje
    assert info.value.detalles["campo"] in datos


@pytest.mark.parametrize("datos", [[], "pow", 5, True, lista_anidada()])
def test_validar_config_datos_no_dict(datos):
    with pytest.raises(EntradaInvalida, match="objeto JSON"):
        validar_config(datos)


def test_validar_config_revalida_un_config_hecho_a_mano():
    with pytest.raises(EntradaInvalida, match="número de nodos"):
        validar_config(Config(num_nodos=99))


# ---------------------------------------------------------------- cripto

def test_claves_reproducibles():
    privada, publica = generar_claves("anahuac", "N01")
    assert es_hex(publica, 64)
    assert generar_claves("anahuac", "N01")[1] == publica
    assert generar_claves("otra", "N01")[1] != publica
    assert generar_claves("anahuac", "N02")[1] != publica
    assert firmar(privada, b"hola") == firmar(generar_claves("anahuac", "N01")[0], b"hola")


def test_firmar_y_verificar(claves):
    privada, publica = claves["N01"]
    firma = firmar(privada, b"mensaje")
    assert es_hex(firma, 128)
    assert verificar(publica, b"mensaje", firma) is True
    assert verificar(claves["N02"][1], b"mensaje", firma) is False      # otra clave
    assert verificar(publica, b"mensaje alterado", firma) is False      # datos alterados
    alterada = ("1" if firma[0] == "0" else "0") + firma[1:]
    assert verificar(publica, b"mensaje", alterada) is False            # firma alterada
    assert verificar(publica, b"mensaje", firma.upper()) is False       # hex en mayúsculas
    assert verificar(publica, b"mensaje", "zz" * 64) is False           # hex inválido
    assert verificar(publica, b"mensaje", firma[:-2]) is False          # largo incorrecto
    assert verificar(publica[:-2], b"mensaje", firma) is False
    assert verificar("00" * 32, b"mensaje", firma) is False


@pytest.mark.parametrize("publica, datos, firma", [
    (None, b"x", "0" * 128), (123, b"x", "0" * 128), (b"\x00" * 32, b"x", "0" * 128),
    ("0" * 64, "x", "0" * 128), ("0" * 64, [1, 2], "0" * 128), ("0" * 64, bytearray(b"x"), "0" * 128),
    ("0" * 64, b"x", None), ("0" * 64, b"x", ["no", "hashable"]), (lista_anidada(), b"x", {}),
    ("0" * 64, b"x", 10**5000),
], ids=lambda valor: type(valor).__name__)
def test_verificar_tipos_raros_da_false(publica, datos, firma):
    assert verificar(publica, datos, firma) is False


def test_es_hex():
    assert es_hex("0a" * 32, 64)
    assert not es_hex("0A" * 32, 64)
    assert not es_hex("0a" * 32, 63)
    assert not es_hex("", None)
    assert not es_hex(None)
    assert not es_hex(12)
    assert es_hex("abc")


# ---------------------------------------------------------------- reloj

def test_reloj_simulado():
    reloj = Reloj()
    assert reloj.actual() == INICIO_SIMULADO_MS
    assert reloj.actual() == INICIO_SIMULADO_MS          # leer no avanza
    marcas = [reloj.ahora() for _ in range(5)]
    assert marcas == [INICIO_SIMULADO_MS + 1000 * k for k in range(1, 6)]
    assert reloj.actual() == marcas[-1]
    assert reloj.actual() == marcas[-1]


def test_reloj_real_estrictamente_creciente():
    reloj = Reloj("real")
    marcas = [reloj.ahora() for _ in range(2000)]
    assert all(b > a for a, b in zip(marcas, marcas[1:]))
    assert reloj.actual() == marcas[-1]


# ---------------------------------------------------------------- bloque

def test_serializar_y_hash_coinciden_con_la_guia(claves):
    tx = crear_transaccion("N01", "N02", 25, INICIO_SIMULADO_MS, claves["N01"][0])
    assert serializar(tx) == serializar_guia(tx)
    assert serializar(tx) == b'{"emisor":"N01","monto":25,"receptor":"N02","timestamp":1767225600000}'
    assert id_transaccion(tx) == hashlib.sha256(serializar_guia(tx)).hexdigest() == tx["id"]
    assert set(tx) == {"id", "emisor", "receptor", "monto", "timestamp", "firma"}
    assert payload(tx) == {"emisor": "N01", "receptor": "N02", "monto": 25, "timestamp": INICIO_SIMULADO_MS}
    assert verificar(claves["N01"][1], serializar(tx), tx["firma"])

    bloque = nuevo_bloque(1, INICIO_SIMULADO_MS + 1000, [tx], HASH_CERO, "N03", 50, "pow", nonce=7)
    assert bloque["hash"] == hash_bloque_guia(bloque) == hash_bloque(bloque)
    assert hash_bloque({**bloque, "hash": "otro"}) == bloque["hash"]   # "hash" no entra en su cálculo
    assert tuple(bloque) == CAMPOS_BLOQUE
    assert bloque["transacciones"] == [payload(tx)] and bloque["firma"] == [tx["firma"]]
    assert bloque["recompensa"] == {"beneficiario": "N03", "monto": 50}
    assert hash_sin_votos(bloque) == hash_bloque({**bloque, "votos": []})


def test_nuevo_bloque_copia_las_listas():
    validadores = [{"id": "N01", "apuesta": 5}]
    bloque = nuevo_bloque(1, 1, [], HASH_CERO, "N01", 50, "pos", validadores=validadores)
    validadores[0]["apuesta"] = 999
    assert bloque["validadores"] == [{"id": "N01", "apuesta": 5}]


def test_genesis(directorio):
    genesis = crear_genesis(Config(), directorio, INICIO_SIMULADO_MS)
    assert set(genesis) == set(CAMPOS_GENESIS)
    assert genesis["numero"] == 0 and genesis["hash_anterior"] == HASH_CERO
    assert genesis["proponente"] == "GENESIS" and genesis["recompensa"] is None
    assert list(genesis["directorio"]) == IDS
    assert genesis["saldos_iniciales"] == {i: 100 for i in IDS}
    assert genesis["parametros"]["confirmaciones"] == 6
    assert genesis["hash"] == hash_bloque_guia(genesis)
    assert validar_estructura_bloque(genesis, es_genesis=True) is None
    assert validar_estructura_bloque(genesis) is not None             # claves de más
    genesis_pos = crear_genesis(Config(modo="pos"), directorio, INICIO_SIMULADO_MS)
    assert genesis_pos["parametros"]["confirmaciones"] == 0 and genesis_pos["modo"] == "pos"
    assert resumen_bloque(genesis)["num_transacciones"] == 0


@pytest.fixture()
def bloque_valido(claves):
    tx = crear_transaccion("N01", "N02", 5, INICIO_SIMULADO_MS, claves["N01"][0])
    castigo = {"nodo": "N04", "apuesta": 10, "monto": 10, "regla": "A", "intento": 0, "numero": 1,
               "valor_transacciones": 5, "motivo": "firma alterada"}
    return nuevo_bloque(1, INICIO_SIMULADO_MS + 1000, [tx], HASH_CERO, "N03", 50, "pos",
                        validadores=[{"id": "N03", "apuesta": 10}], castigos=[castigo])


def test_estructura_bloque_valido(bloque_valido):
    assert validar_estructura_bloque(bloque_valido) is None
    resumen = resumen_bloque(bloque_valido)
    assert resumen["num_transacciones"] == 1 and resumen["num_votos"] == 0
    assert resumen["castigos"][0]["nodo"] == "N04"


@pytest.mark.parametrize("cambio", [
    lambda b: b.update(extra=1),                                  # clave de más
    lambda b: b.pop("nonce"),                                     # clave de menos
    lambda b: b.update({1: "clave no textual"}),
    lambda b: b.update(numero=True),                              # bool en lugar de int
    lambda b: b.update(numero="1"),
    lambda b: b.update(numero=-1),
    lambda b: b.update(nonce=2**63 + 1),
    lambda b: b.update(nonce=10**5000),
    lambda b: b.update(timestamp=1.5),
    lambda b: b.update(transacciones="no es lista"),
    lambda b: b.update(transacciones=[{"emisor": "N01"}]),
    lambda b: b["transacciones"][0].update(monto=True),
    lambda b: b["transacciones"][0].update(monto=0),
    lambda b: b["transacciones"][0].update(extra=1),
    lambda b: b.update(firma=[]),                                 # una firma por transacción
    lambda b: b.update(firma=["xyz"]),
    lambda b: b.update(hash_anterior="0" * 63),
    lambda b: b.update(hash="Z" * 64),
    lambda b: b.update(proponente=""),
    lambda b: b.update(recompensa=None),                          # None sólo en génesis
    lambda b: b.update(recompensa={"beneficiario": "N03", "monto": True}),
    lambda b: b.update(votos=[{"validador": "N03", "peso": 1, "voto": 1, "firma": "0" * 128}]),
    lambda b: b.update(validadores=[{"id": "N03", "apuesta": 0}]),
    lambda b: b.update(validadores=[{"id": "N%02d" % i, "apuesta": 1} for i in range(21)]),
    lambda b: b.update(intento=1001),
    lambda b: b["castigos"][0].update(regla="C"),
    lambda b: b["castigos"][0].update(motivo="x" * 201),
    lambda b: b.update(modo="pox"),
    lambda b: b.update(modo=["pow"]),
])
def test_estructura_bloque_invalido(bloque_valido, cambio):
    cambio(bloque_valido)
    mensaje = validar_estructura_bloque(bloque_valido)
    assert isinstance(mensaje, str) and mensaje.startswith("estructura inválida")


@pytest.mark.parametrize("campo", list(CAMPOS_BLOQUE))
def test_estructura_bloque_con_lista_anidada_profunda(bloque_valido, campo):
    bloque_valido[campo] = lista_anidada()
    assert isinstance(validar_estructura_bloque(bloque_valido), str)


@pytest.mark.parametrize("valor", [None, 5, "texto", [], lista_anidada(), {"a": lista_anidada()}])
def test_estructura_valores_que_no_son_bloque(valor):
    assert isinstance(validar_estructura_bloque(valor), str)
    assert isinstance(validar_estructura_bloque(valor, es_genesis=True), str)
    assert isinstance(validar_estructura_tx(valor), str)


def test_estructura_tx():
    tx = {"emisor": "N01", "receptor": "N02", "monto": 5, "timestamp": 0}
    assert validar_estructura_tx(tx) is None
    for cambio in ({"monto": True}, {"monto": 10**12 + 1}, {"monto": 5.0}, {"emisor": ""},
                   {"receptor": "x" * 17}, {"timestamp": -1}, {"timestamp": lista_anidada()}):
        assert isinstance(validar_estructura_tx({**tx, **cambio}), str)
    assert isinstance(validar_estructura_tx({**tx, "firma": "ab"}), str)   # sólo los 4 campos
    assert isinstance(validar_estructura_tx({"emisor": "N01"}), str)
    sustituto = json.loads('"N0\\ud8001"')                 # texto que no se puede escribir en UTF-8
    assert validar_estructura_tx({**tx, "emisor": sustituto}) == (
        "la transacción: 'emisor' debe ser texto de 1 a 16 caracteres")
    assert validar_estructura_tx({**tx, "emisor": "Ñ1"}) is None     # los acentos sí son texto válido


# ---------------------------------------------------------------- reglas

def test_cumple_dificultad():
    assert cumple_dificultad("000abc", 3)
    assert not cumple_dificultad("00abc", 3)


def test_sortear_reproducible_y_ponderado():
    validadores = [{"id": "N01", "apuesta": 10}, {"id": "N02", "apuesta": 30}, {"id": "N03", "apuesta": 60}]
    assert sortear(validadores, "a" * 64, 5, 0) == sortear(validadores, "a" * 64, 5, 0)
    conteo = {"N01": 0, "N02": 0, "N03": 0}
    total = 6000
    for i in range(total):
        hash_anterior = hashlib.sha256(str(i).encode()).hexdigest()
        conteo[sortear(validadores, hash_anterior, 1, 0)] += 1
    esperadas = probabilidades(validadores)
    assert esperadas == {"N01": 0.1, "N02": 0.3, "N03": 0.6}
    for id_validador, veces in conteo.items():
        assert abs(veces / total - esperadas[id_validador]) < 0.03


def test_sortear_es_el_algoritmo_de_la_guia():
    validadores = [{"id": "N01", "apuesta": 3}, {"id": "N02", "apuesta": 4}]
    r = int(hashlib.sha256(b"abc|2|1").hexdigest(), 16) % 7
    assert sortear(validadores, "abc", 2, 1) == ("N01" if r < 3 else "N02")
    with pytest.raises(ValueError):
        sortear([], "abc", 1, 0)
    with pytest.raises(ValueError):
        sortear([{"id": "N01", "apuesta": 0}], "abc", 1, 0)


def test_alcanza_umbral_exacto():
    assert alcanza_umbral(18, 27) is True
    assert alcanza_umbral(18, 28) is False
    assert alcanza_umbral(0, 0) is False
    assert alcanza_umbral(5, 0) is False
    assert alcanza_umbral(2, 3) is True


def test_calcular_castigo():
    assert calcular_castigo("A", 40, 5, 50) == 40
    assert calcular_castigo("B", 40, 7, 50) == 4          # techo de 3.5
    assert calcular_castigo("B", 40, 0, 50) == 1          # mínimo 1
    assert calcular_castigo("B", 40, 1, 1) == 1           # techo de 0.01
    assert calcular_castigo("B", 40, 1000, 50) == 40      # nunca más que la apuesta
    assert calcular_castigo("B", 1, 10**12, 100) == 1
    assert calcular_castigo("B", 10, 3, 100) == 3
    for apuesta in range(1, 30):
        for valor in range(0, 60, 7):
            assert 1 <= calcular_castigo("B", apuesta, valor, 33) <= apuesta


def test_mensaje_voto():
    assert mensaje_voto("ab", "N01", True) == b"voto|ab|N01|si"
    assert mensaje_voto("ab", "N01", False) == b"voto|ab|N01|no"


def test_verificar_votos_bloque_valido(claves, directorio):
    bloque = bloque_pos(claves, {"N01": 10, "N02": 20, "N03": 30}, {"N01", "N02", "N03"})
    assert verificar_votos_bloque(bloque, directorio) is None


def test_verificar_votos_umbral_exacto(claves, directorio):
    justo = bloque_pos(claves, {"N01": 9, "N02": 9, "N03": 9}, {"N01", "N02"})      # V=18, A=27
    assert verificar_votos_bloque(justo, directorio) is None
    debajo = bloque_pos(claves, {"N01": 9, "N02": 9, "N03": 10}, {"N01", "N02"})    # V=18, A=28
    mensaje = verificar_votos_bloque(debajo, directorio)
    assert mensaje is not None and "votos insuficientes" in mensaje and "18" in mensaje and "28" in mensaje


def test_verificar_votos_bloque_trampas(claves, directorio):
    base = bloque_pos(claves, {"N01": 10, "N02": 20, "N03": 30}, {"N01", "N02", "N03"})

    duplicado = json.loads(json.dumps(base))
    duplicado["votos"].append(dict(duplicado["votos"][0]))
    assert "dos veces" in verificar_votos_bloque(duplicado, directorio)

    intruso = json.loads(json.dumps(base))
    firma = firmar(claves["N04"][0], mensaje_voto(hash_sin_votos(intruso), "N04", True))
    intruso["votos"].append({"validador": "N04", "peso": 5, "voto": True, "firma": firma})
    assert "sin ser validador" in verificar_votos_bloque(intruso, directorio)

    peso = json.loads(json.dumps(base))
    peso["votos"][0]["peso"] = 99
    assert "pesa 99" in verificar_votos_bloque(peso, directorio)

    firma_mala = json.loads(json.dumps(base))
    original = firma_mala["votos"][1]["firma"]
    firma_mala["votos"][1]["firma"] = ("1" if original[0] == "0" else "0") + original[1:]
    assert "firma inválida" in verificar_votos_bloque(firma_mala, directorio)

    otra_clave = json.loads(json.dumps(base))
    otra_clave["votos"][1]["firma"] = firmar(claves["N09"][0],
                                             mensaje_voto(hash_sin_votos(base), "N02", True))
    assert "firma inválida" in verificar_votos_bloque(otra_clave, directorio)

    invertido = json.loads(json.dumps(base))   # cambiar el sentido del voto rompe su firma
    invertido["votos"][0]["voto"] = False
    assert "firma inválida" in verificar_votos_bloque(invertido, directorio)

    desordenado = json.loads(json.dumps(base))
    desordenado["validadores"].reverse()
    assert "ordenados" in verificar_votos_bloque(desordenado, directorio)

    repetido = json.loads(json.dumps(base))
    repetido["validadores"].append({"id": "N03", "apuesta": 30})
    assert "dos veces" in verificar_votos_bloque(repetido, directorio)

    sin_validadores = json.loads(json.dumps(base))
    sin_validadores["validadores"] = []
    assert "no tiene validadores" in verificar_votos_bloque(sin_validadores, directorio)

    fuera_directorio = {k: v for k, v in directorio.items() if k != "N03"}
    assert "directorio" in verificar_votos_bloque(base, fuera_directorio)

    alterado = json.loads(json.dumps(base))     # cambiar el bloque invalida todos los votos
    alterado["transacciones"][0]["monto"] = 500
    assert "firma inválida" in verificar_votos_bloque(alterado, directorio)


def test_verificar_votos_el_proponente_firma_su_bloque(claves, directorio):
    # El proponente (N01) vota en contra; los demás reúnen 2/3 (V=18, A=27). Sin su firma no vale.
    sin_firma = bloque_pos(claves, {"N01": 9, "N02": 9, "N03": 9}, {"N02", "N03"})
    assert sin_firma["proponente"] == "N01"
    assert verificar_votos_bloque(sin_firma, directorio) == (
        "el proponente N01 no firmó su bloque (falta su voto a favor)")
    sin_voto = json.loads(json.dumps(sin_firma))
    sin_voto["votos"] = [v for v in sin_voto["votos"] if v["validador"] != "N01"]
    assert verificar_votos_bloque(sin_voto, directorio) == (
        "el proponente N01 no firmó su bloque (falta su voto a favor)")
    # Si además no alcanzan los votos, el mensaje es el del umbral.
    debajo = bloque_pos(claves, {"N01": 9, "N02": 9, "N03": 10}, {"N02"})
    assert "votos insuficientes" in verificar_votos_bloque(debajo, directorio)


@pytest.mark.parametrize("bloque, directorio_raro", [
    (None, {}), ([], {}), ({}, {}), ({"validadores": lista_anidada(), "votos": []}, {}),
    ({"validadores": [{"id": [1], "apuesta": 1}], "votos": []}, {}),
    ({"validadores": [{"id": "N01", "apuesta": 1}], "votos": [lista_anidada()]}, {"N01": "0" * 64}),
    ({"validadores": [{"id": "N01", "apuesta": 1}], "votos": []}, None),
])
def test_verificar_votos_nunca_lanza(bloque, directorio_raro):
    assert isinstance(verificar_votos_bloque(bloque, directorio_raro), str)


# ---------------------------------------------------------------- bitácora

def test_bitacora():
    bitacora = Bitacora(maximo=5)
    assert bitacora.ultimo == 0 and bitacora.desde(0) == []
    for k in range(8):
        evento = bitacora.registrar("transaccion", f"evento {k}", monto=k)
        assert evento["n"] == k + 1 and evento["tipo"] == "transaccion" and evento["datos"] == {"monto": k}
        assert isinstance(evento["tiempo"], int)
    assert bitacora.ultimo == 8
    assert [e["n"] for e in bitacora.desde(0)] == [4, 5, 6, 7, 8]       # sólo los últimos `maximo`
    assert [e["n"] for e in bitacora.desde(6)] == [7, 8]
    assert bitacora.desde(8) == []
    assert [e["n"] for e in bitacora.desde(0, limite=2)] == [7, 8]
    assert bitacora.desde(0, limite=0) == []
    with pytest.raises(ValueError):
        bitacora.registrar("tipo_inventado", "x")
    assert "error" in TIPOS and len(TIPOS) == 21


# ---------------------------------------------------------------- trampas

def test_trampa_firma_alterada(claves):
    privada = claves["N05"][0]
    pendiente = crear_transaccion("N01", "N02", 7, INICIO_SIMULADO_MS, claves["N01"][0])
    [tx] = transacciones_tramposas("firma_alterada", "N05", "N06", 50, privada, Reloj(), [pendiente])
    assert payload(tx) == payload(pendiente) and tx["firma"] != pendiente["firma"]
    assert es_hex(tx["firma"], 128)
    assert not verificar(claves["N01"][1], serializar(tx), tx["firma"])
    assert verificar(claves["N01"][1], serializar(pendiente), pendiente["firma"])   # la original intacta

    [nueva] = transacciones_tramposas("firma_alterada", "N05", "N06", 50, privada, Reloj(), [])
    assert (nueva["emisor"], nueva["receptor"], nueva["monto"]) == ("N05", "N06", 1)
    assert not verificar(claves["N05"][1], serializar(nueva), nueva["firma"])


@pytest.mark.parametrize("saldo", [0, 1, 37, 100])
def test_trampa_gasto_excesivo(claves, saldo):
    privada, publica = claves["N05"]
    [tx] = transacciones_tramposas("gasto_excesivo", "N05", "N06", saldo, privada, Reloj(), [])
    assert tx["monto"] == saldo + 1 and tx["monto"] > saldo
    assert verificar(publica, serializar(tx), tx["firma"])


@pytest.mark.parametrize("saldo", [0, 1, 2, 3, 10, 101])
def test_trampa_doble_gasto(claves, saldo):
    privada, publica = claves["N05"]
    txs = transacciones_tramposas("doble_gasto", "N05", "N06", saldo, privada, Reloj(), [])
    assert len(txs) == 2 and txs[0]["id"] != txs[1]["id"]
    assert all(verificar(publica, serializar(t), t["firma"]) for t in txs)
    assert sum(t["monto"] for t in txs) > saldo
    if saldo >= 2:
        assert all(t["monto"] <= saldo for t in txs)
    if saldo == 0:
        assert [t["monto"] for t in txs] == [1, 1]


def test_trampas_cuentan_las_pendientes_que_le_pagan(claves):
    """Si su bloque incluye pendientes que le pagan, la trampa debe superar también ese dinero."""
    privada = claves["N05"][0]
    entrantes = [crear_transaccion("N03", "N05", 60, INICIO_SIMULADO_MS, claves["N03"][0]),
                 crear_transaccion("N04", "N05", 15, INICIO_SIMULADO_MS, claves["N04"][0]),
                 crear_transaccion("N05", "N06", 30, INICIO_SIMULADO_MS, privada),     # sale: no suma
                 {"receptor": "N05", "monto": "mucho"}]                                # rara: se ignora
    [tx] = transacciones_tramposas("gasto_excesivo", "N05", "N06", 100, privada, Reloj(), entrantes)
    assert tx["monto"] == 100 + 60 + 15 + 1
    txs = transacciones_tramposas("doble_gasto", "N05", "N06", 100, privada, Reloj(), entrantes)
    assert [t["monto"] for t in txs] == [88, 88] and sum(t["monto"] for t in txs) > 175


def test_trampas_sin_transacciones_y_recompensa(claves):
    for trampa in ("recompensa_falsa", "voto_invertido"):
        assert transacciones_tramposas(trampa, "N05", "N06", 50, claves["N05"][0], Reloj(), []) == []
    assert aplicar_trampa_recompensa(50, "recompensa_falsa") == 500
    for trampa in (None, "firma_alterada", "voto_invertido"):
        assert aplicar_trampa_recompensa(50, trampa) == 50
    assert set(TRAMPAS) == {"recompensa_falsa", "firma_alterada", "gasto_excesivo", "doble_gasto",
                            "voto_invertido"}
