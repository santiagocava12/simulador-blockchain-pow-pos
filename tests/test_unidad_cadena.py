"""Pruebas unitarias de libro.py, validacion.py y red.py.

Las cadenas se construyen a mano con los ayudantes de este archivo: génesis con
10 nodos, bloques PoW minados por fuerza bruta (dificultad 1) y bloques PoS con
validadores y votos firmados.
"""

import copy
import itertools
import json

import pytest

from nucleo.bloque import (
    crear_genesis,
    crear_transaccion,
    hash_bloque,
    hash_sin_votos,
    nuevo_bloque,
)
from nucleo.config import Config
from nucleo.cripto import firmar, generar_claves
from nucleo.errores import Conflicto, EntradaInvalida, ErrorSimulacion, NoEncontrado
from nucleo.libro import CadenaInvalida, Libro
from nucleo.red import Nodo, Red
from nucleo.reglas import cumple_dificultad, mensaje_voto, sortear
from nucleo.reloj import INICIO_SIMULADO_MS, Reloj
from nucleo.trampas import transacciones_tramposas
from nucleo.validacion import (
    evaluar_cadena_recibida,
    validar_bloque,
    validar_cadena,
    validar_candidato,
    validar_transaccion_nueva,
)

SEMILLA = "prueba-cadena"
IDS = [f"N{i:02d}" for i in range(1, 11)]
CLAVES = {i: generar_claves(SEMILLA, i) for i in IDS}
DIRECTORIO = {i: CLAVES[i][1] for i in IDS}
CLAVE_N99 = generar_claves(SEMILLA, "N99")[0]   # nodo que no está en el directorio
SALDO_INICIAL = 100
RECOMPENSA = 50

# Marcas de tiempo únicas para las transacciones (todas antes del bloque 1).
_TIEMPOS = itertools.count(INICIO_SIMULADO_MS + 1)


# ---------------------------------------------------------------- ayudantes

def genesis_de(modo: str = "pow", **cambios) -> dict:
    config = Config(modo=modo, dificultad=1, saldo_inicial=SALDO_INICIAL, recompensa=RECOMPENSA,
                    max_tx_por_bloque=5, **cambios)
    return crear_genesis(config, DIRECTORIO, INICIO_SIMULADO_MS)


def tx(emisor: str, receptor: str, monto, timestamp=None, clave=None) -> dict:
    """Transacción firmada por el emisor (o por `clave`, para simular otra firma)."""
    if timestamp is None:
        timestamp = next(_TIEMPOS)
    if clave is None:
        clave = CLAVES[emisor][0] if emisor in CLAVES else CLAVE_N99
    return crear_transaccion(emisor, receptor, monto, timestamp, clave)


def minar(bloque: dict, dificultad: int = 1) -> dict:
    """Fuerza bruta: prueba nonces hasta que el hash cumple la dificultad."""
    for nonce in itertools.count():
        bloque["nonce"] = nonce
        h = hash_bloque(bloque)
        if cumple_dificultad(h, dificultad):
            bloque["hash"] = h
            return bloque
    raise AssertionError("inalcanzable")


def bloque_pow(cadena: list, txs: list, proponente: str = "N01", recompensa: int = RECOMPENSA) -> dict:
    anterior = cadena[-1]
    bloque = nuevo_bloque(anterior["numero"] + 1, anterior["timestamp"] + 1000, txs, anterior["hash"],
                          proponente, recompensa, "pow")
    return minar(bloque)


def extender_pow(cadena: list, txs: list, **opciones) -> list:
    return cadena + [bloque_pow(cadena, txs, **opciones)]


def cadena_pow(bloques: int, genesis: dict | None = None) -> list:
    """Cadena PoW válida: cada bloque lleva una transacción N05 → N06 de 1."""
    cadena = [genesis or genesis_de()]
    for _ in range(bloques):
        cadena = extender_pow(cadena, [tx("N05", "N06", 1)])
    return cadena


def firmar_votos(bloque: dict, a_favor: set[str]) -> dict:
    """Agrega los votos firmados de todos los validadores y recalcula el hash."""
    candidato = hash_sin_votos(bloque)
    bloque["votos"] = [
        {"validador": v["id"], "peso": v["apuesta"], "voto": v["id"] in a_favor,
         "firma": firmar(CLAVES[v["id"]][0], mensaje_voto(candidato, v["id"], v["id"] in a_favor))}
        for v in bloque["validadores"]
    ]
    bloque["hash"] = hash_bloque(bloque)
    return bloque


def candidato_pos(cadena: list, txs: list, apuestas: dict[str, int], intento: int = 0,
                  castigos: list | None = None, proponente: str | None = None,
                  recompensa: int = RECOMPENSA) -> dict:
    """Bloque PoS sin votos; el proponente es el que sale en el sorteo (salvo que se indique)."""
    anterior = cadena[-1]
    numero = anterior["numero"] + 1
    validadores = [{"id": i, "apuesta": a} for i, a in sorted(apuestas.items())]
    if proponente is None:
        proponente = sortear(validadores, anterior["hash"], numero, intento)
    return nuevo_bloque(numero, anterior["timestamp"] + 1000, txs, anterior["hash"], proponente, recompensa,
                        "pos", validadores=validadores, intento=intento, castigos=castigos)


def bloque_pos(cadena: list, txs: list, apuestas: dict[str, int], a_favor: set[str] | None = None,
               **opciones) -> dict:
    bloque = candidato_pos(cadena, txs, apuestas, **opciones)
    return firmar_votos(bloque, set(apuestas) if a_favor is None else a_favor)


def castigo(nodo: str, apuesta: int, monto: int, regla: str = "A", valor: int = 10, numero: int = 1) -> dict:
    return {"nodo": nodo, "apuesta": apuesta, "monto": monto, "regla": regla, "intento": 0,
            "numero": numero, "valor_transacciones": valor, "motivo": "candidato con firma alterada"}


def error_del_bloque(cadena: list, bloque: dict) -> str | None:
    """Valida `bloque` como sucesor de la cadena (que se supone válida)."""
    valida, _, libro = validar_cadena(cadena, cadena[0])
    assert valida
    return validar_bloque(bloque, cadena[-1], libro, cadena[0])


def invariante_ok(libro: Libro, genesis: dict) -> bool:
    esperado = sum(genesis["saldos_iniciales"].values()) + RECOMPENSA * libro.altura - libro.quemado
    return libro.total_en_circulacion() == esperado


def lista_anidada(niveles: int = 10_000) -> list:
    valor: list = []
    for _ in range(niveles):
        valor = [valor]
    return valor


def nueva_red(modo: str = "pow") -> Red:
    genesis = genesis_de(modo)
    return Red(genesis, [(i, DIRECTORIO[i]) for i in IDS])


# ---------------------------------------------------------------- cadena PoW válida

def test_cadena_pow_valida_y_saldos():
    genesis = genesis_de()
    cadena = extender_pow([genesis], [tx("N02", "N03", 30), tx("N03", "N04", 130)])  # N03 usa lo recién recibido
    cadena = extender_pow(cadena, [tx("N04", "N05", 1)], proponente="N02")
    valida, mensaje, libro = validar_cadena(cadena, genesis)
    assert (valida, mensaje) == (True, "cadena válida (3 bloques)")
    assert libro.altura == 2
    assert [libro.saldo(i) for i in ("N02", "N03", "N04", "N05")] == [70, 0, 229, 101]
    assert libro.saldo("N01") == 100 and libro.total_pendiente("N01") == 50   # recompensa aún pendiente
    assert len(libro.ids_tx) == 3 and set(libro.ids_tx.values()) == {1, 2}
    assert invariante_ok(libro, genesis)


def test_solo_genesis_es_cadena_valida():
    genesis = genesis_de()
    valida, mensaje, libro = validar_cadena([genesis], genesis)
    assert valida and mensaje == "cadena válida (1 bloque)"
    assert libro.altura == 0 and libro.saldo("N10") == SALDO_INICIAL


def test_libro_clonar_es_independiente():
    cadena = cadena_pow(2)
    _, _, libro = validar_cadena(cadena, cadena[0])
    clon = libro.clonar()
    clon.saldos["N05"] = 0
    clon.recompensas_pendientes[0]["monto"] = 999
    clon.ids_tx.clear()
    clon.quemado = 7
    assert libro.saldo("N05") == 98 and libro.recompensas_pendientes[0]["monto"] == 50
    assert len(libro.ids_tx) == 2 and libro.quemado == 0


# ---------------------------------------------------------------- recompensas PoW

def test_recompensa_pendiente_y_madurez_exacta_en_h_mas_6():
    genesis = genesis_de()
    libro = Libro(genesis)
    cadena = [genesis]
    for h in range(1, 8):
        bloque = bloque_pow(cadena, [tx("N05", "N06", 1)], proponente="N01" if h == 1 else "N02")
        assert validar_bloque(bloque, cadena[-1], libro.clonar(), genesis) is None
        acreditadas = libro.aplicar_bloque(bloque)
        cadena.append(bloque)
        if h < 7:   # bloque 1 + 6 confirmaciones = altura 7
            assert acreditadas == []
            assert libro.saldo("N01") == 100
            assert libro.pendientes_de("N01") == [{"bloque": 1, "monto": 50, "faltan": 7 - h}]
            assert libro.total_pendiente("N01") == 50
        assert invariante_ok(libro, genesis)
    # En h + 6 (altura 7) se acredita, no antes.
    assert acreditadas == [{"bloque": 1, "beneficiario": "N01", "monto": 50}]
    assert libro.saldo("N01") == 150 and libro.pendientes_de("N01") == []
    assert libro.acreditadas == [{"bloque": 1, "beneficiario": "N01", "monto": 50}]
    assert [p["faltan"] for p in libro.pendientes_de("N02")] == [1, 2, 3, 4, 5, 6]
    assert libro.total_pendiente("N02") == 300
    assert libro.total_en_circulacion() == 10 * SALDO_INICIAL + 7 * RECOMPENSA


def test_gastar_recompensa_no_madura_invalida_el_bloque():
    genesis = genesis_de()
    cadena = extender_pow([genesis], [tx("N05", "N06", 1)], proponente="N01")      # N01 gana 50 (pendiente)
    gasto = tx("N01", "N02", 120)                                                     # necesita la recompensa
    error = error_del_bloque(cadena, bloque_pow(cadena, [gasto], proponente="N02"))
    assert error.startswith("Bloque 2: transacción 1 (N01 → N02, 120): doble gasto / saldo insuficiente")
    assert "N01 tiene 100 e intenta enviar 120" in error
    assert "50 en recompensas aún no maduras" in error

    for _ in range(5):                                                                # bloques 2..6
        cadena = extender_pow(cadena, [tx("N05", "N06", 1)], proponente="N02")
    # Bloque 7 = h + 6: la recompensa madura al CERRAR este bloque; dentro de él aún no se puede gastar.
    error = error_del_bloque(cadena, bloque_pow(cadena, [gasto], proponente="N02"))
    assert error is not None and "N01 tiene 100 e intenta enviar 120" in error
    cadena = extender_pow(cadena, [tx("N05", "N06", 1)], proponente="N02")
    assert error_del_bloque(cadena, bloque_pow(cadena, [gasto], proponente="N02")) is None   # bloque 8: ya madura


# ---------------------------------------------------------------- transacciones inválidas en un bloque

def test_doble_gasto_dentro_del_mismo_bloque():
    cadena = [genesis_de()]
    bloque = bloque_pow(cadena, [tx("N03", "N04", 60), tx("N03", "N05", 60)])
    error = error_del_bloque(cadena, bloque)
    assert error.startswith("Bloque 1: transacción 2 (N03 → N05, 60)")
    assert "doble gasto / saldo insuficiente en el bloque: N03 tiene 40 e intenta enviar 60" in error


@pytest.mark.parametrize("trampa", ["gasto_excesivo", "doble_gasto"])
@pytest.mark.parametrize("trampa_primero", [False, True])
def test_bloque_tramposo_con_pendiente_que_le_paga_se_rechaza(trampa, trampa_primero):
    """Aunque el bloque incluya una pendiente que paga al tramposo, su trampa no alcanza."""
    genesis = genesis_de()
    pendientes = [tx("N03", "N05", 60)]                          # N05 recibe 60 dentro del mismo bloque
    tramposas = transacciones_tramposas(trampa, "N05", "N06", Libro(genesis).saldo("N05"),
                                        CLAVES["N05"][0], Reloj(), pendientes)
    txs = tramposas + pendientes if trampa_primero else pendientes + tramposas
    bloque = minar(nuevo_bloque(1, INICIO_SIMULADO_MS + 100_000, txs, genesis["hash"], "N05", RECOMPENSA, "pow"))
    error = validar_bloque(bloque, genesis, Libro(genesis), genesis)
    assert error is not None and "doble gasto / saldo insuficiente en el bloque: N05 tiene" in error


def test_transaccion_repetida_en_otro_bloque_y_en_el_mismo():
    repetida = tx("N02", "N03", 10)
    cadena = extender_pow([genesis_de()], [repetida])
    error = error_del_bloque(cadena, bloque_pow(cadena, [repetida]))
    assert "transacción repetida: ya está en el bloque 1" in error and error.startswith("Bloque 2:")
    error = error_del_bloque([cadena[0]], bloque_pow([cadena[0]], [repetida, repetida]))
    assert error.startswith("Bloque 1: transacción 2") and "transacción repetida: ya está en el bloque 1" in error


def test_firma_alterada_en_bloque():
    cadena = [genesis_de()]
    alterada = tx("N02", "N03", 10)
    alterada["firma"] = ("1" if alterada["firma"][0] == "0" else "0") + alterada["firma"][1:]
    error = error_del_bloque(cadena, bloque_pow(cadena, [alterada]))
    assert "firma inválida" in error and error.startswith("Bloque 1: transacción 1 (N02 → N03, 10)")


def test_firma_de_otra_clave_y_monto_alterado_tras_firmar():
    cadena = [genesis_de()]
    ajena = tx("N02", "N03", 10, clave=CLAVES["N03"][0])          # N03 firma en nombre de N02
    assert "firma inválida" in error_del_bloque(cadena, bloque_pow(cadena, [ajena]))
    original = tx("N02", "N03", 10)
    bloque = nuevo_bloque(1, cadena[0]["timestamp"] + 1000, [original], cadena[0]["hash"], "N01", 50, "pow")
    bloque["transacciones"][0]["monto"] = 99                      # se cambia lo firmado y se vuelve a minar
    assert "firma inválida" in error_del_bloque(cadena, minar(bloque))


@pytest.mark.parametrize("emisor, receptor, fragmento", [
    ("N99", "N01", "el emisor N99 no existe"),
    ("N01", "N42", "el receptor N42 no existe"),
    ("N04", "N04", "el emisor y el receptor son el mismo nodo"),
])
def test_transaccion_con_nodos_invalidos_en_bloque(emisor, receptor, fragmento):
    cadena = [genesis_de()]
    error = error_del_bloque(cadena, bloque_pow(cadena, [tx(emisor, receptor, 5)]))
    assert error.startswith("Bloque 1: transacción 1") and fragmento in error


def test_transaccion_con_timestamp_posterior_al_bloque():
    cadena = [genesis_de()]
    futura = tx("N02", "N03", 5, timestamp=INICIO_SIMULADO_MS + 5000)   # el bloque 1 lleva INICIO + 1000
    assert "timestamp es posterior al del bloque" in error_del_bloque(cadena, bloque_pow(cadena, [futura]))


# ---------------------------------------------------------------- recompensa falsa

def test_recompensa_falsa():
    cadena = [genesis_de()]
    error = error_del_bloque(cadena, bloque_pow(cadena, [tx("N02", "N03", 5)], recompensa=500))
    assert error == "Bloque 1: recompensa falsa: 500 distinta a la establecida 50"


def test_recompensa_para_otro_nodo_o_proponente_inexistente():
    cadena = [genesis_de()]
    bloque = bloque_pow(cadena, [tx("N02", "N03", 5)])
    bloque["recompensa"]["beneficiario"] = "N09"
    error = error_del_bloque(cadena, minar(bloque))
    assert error == "Bloque 1: la recompensa es para N09 pero el proponente es N01"
    error = error_del_bloque(cadena, bloque_pow(cadena, [tx("N02", "N03", 5)], proponente="X"))
    assert error == "Bloque 1: el proponente X no existe en el directorio"


# ---------------------------------------------------------------- hash y enlaces

def test_hash_alterado():
    cadena = cadena_pow(2)
    cadena[2]["hash"] = "0" + "f" * 63
    valida, mensaje, libro = validar_cadena(cadena, cadena[0])
    assert (valida, libro) == (False, None)
    assert mensaje == "Bloque 2: el hash no coincide con el recalculado (bloque alterado)"


def test_hash_anterior_alterado():
    cadena = cadena_pow(2)
    cadena[2]["hash_anterior"] = "a" * 64
    valida, mensaje, _ = validar_cadena(cadena, cadena[0])
    assert not valida and mensaje == "Bloque 2: hash_anterior no coincide con el hash del bloque 1"


def test_numero_no_consecutivo_modo_y_timestamp():
    cadena = cadena_pow(1)
    genesis = cadena[0]
    salto = nuevo_bloque(3, cadena[-1]["timestamp"] + 1000, [tx("N02", "N03", 1)], cadena[-1]["hash"],
                         "N01", 50, "pow")
    assert "el número no es consecutivo (se esperaba 2)" in error_del_bloque(cadena, minar(salto))
    viejo = nuevo_bloque(2, cadena[-1]["timestamp"], [tx("N02", "N03", 1)], cadena[-1]["hash"], "N01", 50, "pow")
    assert "el timestamp no es posterior al del bloque 1" in error_del_bloque(cadena, minar(viejo))
    otro_modo = nuevo_bloque(2, cadena[-1]["timestamp"] + 1000, [tx("N02", "N03", 1)], cadena[-1]["hash"],
                             "N01", 50, "pos")
    assert "el modo pos no coincide con el de la red (pow)" in error_del_bloque(cadena, otro_modo)
    assert genesis["modo"] == "pow"


def test_cantidad_de_transacciones_y_dificultad():
    cadena = [genesis_de()]
    assert error_del_bloque(cadena, bloque_pow(cadena, [])) == "Bloque 1: sin transacciones de usuario"
    # max_tx_por_bloque = 5: con 5 es válido.
    cinco = [tx("N02", "N03", 1) for _ in range(5)]
    assert error_del_bloque(cadena, bloque_pow(cadena, cinco)) is None
    # 6 o 7 transacciones válidas pasan el filtro inicial (+2), pero el máximo se cumple al final.
    for cantidad in (6, 7):
        validas = [tx("N02", "N03", 1) for _ in range(cantidad)]
        assert error_del_bloque(cadena, bloque_pow(cadena, validas)) == (
            f"Bloque 1: tiene {cantidad} transacciones (máximo 5 por bloque)")
    # Un bloque tramposo de 7 transacciones muestra primero su causa real (el doble gasto).
    tramposo = [tx("N02", "N03", 1) for _ in range(5)] + [tx("N04", "N05", 60), tx("N04", "N06", 60)]
    error = error_del_bloque(cadena, bloque_pow(cadena, tramposo))
    assert error.startswith("Bloque 1: transacción 7") and "N04 tiene 40 e intenta enviar 60" in error
    ocho = [tx("N02", "N03", 1) for _ in range(8)]
    assert error_del_bloque(cadena, bloque_pow(cadena, ocho)) == "Bloque 1: tiene 8 transacciones (máximo 5 por bloque)"
    sin_minar = nuevo_bloque(1, cadena[0]["timestamp"] + 1000, [tx("N02", "N03", 1)], cadena[0]["hash"],
                             "N01", 50, "pow")
    for nonce in itertools.count():            # un nonce cuyo hash NO empieza con 0
        sin_minar["nonce"] = nonce
        sin_minar["hash"] = hash_bloque(sin_minar)
        if not sin_minar["hash"].startswith("0"):
            break
    assert error_del_bloque(cadena, sin_minar) == "Bloque 1: el hash no cumple la dificultad (1 cero)"


def test_bloque_pow_con_castigos_o_votos():
    cadena = [genesis_de()]
    bloque = nuevo_bloque(1, cadena[0]["timestamp"] + 1000, [tx("N02", "N03", 1)], cadena[0]["hash"],
                          "N01", 50, "pow", castigos=[castigo("N04", 10, 10)])
    error = error_del_bloque(cadena, minar(bloque))
    assert error == "Bloque 1: un bloque PoW no lleva votos, validadores ni castigos"


# ---------------------------------------------------------------- bloque intermedio alterado

def test_bloque_intermedio_alterado_sin_recalcular_hash():
    cadena = cadena_pow(4)
    alterada = copy.deepcopy(cadena)
    alterada[2]["transacciones"][0]["monto"] += 1000
    valida, mensaje, _ = validar_cadena(alterada, alterada[0])
    assert not valida and mensaje == "Bloque 2: el hash no coincide con el recalculado (bloque alterado)"
    assert validar_cadena(cadena, cadena[0])[0]          # la original sigue intacta


def test_bloque_intermedio_alterado_recalculando_hash():
    cadena = cadena_pow(4)
    # Sólo recalcula el hash: falla la dificultad o la firma, pero siempre en el bloque 2.
    alterada = copy.deepcopy(cadena)
    alterada[2]["transacciones"][0]["monto"] += 1000
    alterada[2]["hash"] = hash_bloque(alterada[2])
    valida, mensaje, _ = validar_cadena(alterada, alterada[0])
    assert not valida and mensaje.startswith("Bloque 2:")
    # Además lo vuelve a minar: la firma del emisor delata el cambio.
    minar(alterada[2])
    valida, mensaje, _ = validar_cadena(alterada, alterada[0])
    assert not valida and mensaje.startswith("Bloque 2: transacción 1") and "firma inválida" in mensaje
    # Un cambio que no toca lo firmado (timestamp) y se vuelve a minar: se rompe el enlace con el bloque 3.
    enlace = copy.deepcopy(cadena)
    enlace[2]["timestamp"] += 1
    minar(enlace[2])
    valida, mensaje, _ = validar_cadena(enlace, enlace[0])
    assert not valida and mensaje == "Bloque 3: hash_anterior no coincide con el hash del bloque 2"


# ---------------------------------------------------------------- génesis y largo

def test_genesis_distinto():
    genesis = genesis_de()
    otra_red = cadena_pow(2, genesis_de(semilla="otra"))
    valida, mensaje, _ = validar_cadena(otra_red, genesis)
    assert not valida and mensaje.startswith("génesis distinto")
    alterado = copy.deepcopy(cadena_pow(1, genesis))
    alterado[0]["saldos_iniciales"]["N01"] = 10**6           # regalarse dinero en el génesis
    assert validar_cadena(alterado, genesis)[1].startswith("génesis distinto")
    assert validar_cadena([genesis_de("pos")], genesis)[1].startswith("génesis distinto")


@pytest.mark.parametrize("campo, valor", [
    ("numero", 0.0), ("numero", False), ("nonce", False), ("nonce", 0.0), ("intento", False),
    ("timestamp", float(INICIO_SIMULADO_MS)),
])
def test_genesis_con_tipos_cambiados_es_distinto(campo, valor):
    """En Python 0 == 0.0 == False: un génesis así parecería igual, pero su hash ya no coincide."""
    genesis = genesis_de()
    cadena = cadena_pow(2, genesis)
    falsa = copy.deepcopy(cadena)
    falsa[0][campo] = valor
    assert falsa[0] == genesis                     # la igualdad de dict de Python no lo distingue
    valida, mensaje, _ = validar_cadena(falsa, genesis)
    assert not valida and mensaje.startswith("génesis distinto")
    nodo = Nodo("N05", 4, DIRECTORIO["N05"], genesis)
    assert nodo.recibir_cadena(falsa, genesis)[0] is False and nodo.cadena == [genesis]


def test_genesis_con_saldos_o_parametros_de_otro_tipo_es_distinto():
    genesis = genesis_de()
    cadena = cadena_pow(1, genesis)
    for cambiar in (lambda g: g["saldos_iniciales"].update(N01=100.0),
                    lambda g: g["parametros"].update(confirmaciones=6.0),
                    lambda g: g["parametros"].update(dificultad=True)):      # True == 1
        falsa = copy.deepcopy(cadena)
        cambiar(falsa[0])
        assert falsa[0] == genesis
        assert validar_cadena(falsa, genesis)[1].startswith("génesis distinto")


def test_nodo_guarda_el_genesis_de_confianza():
    genesis = genesis_de()
    cadena = cadena_pow(1, genesis)
    reordenada = copy.deepcopy(cadena)
    reordenada[0] = dict(reversed(list(reordenada[0].items())))   # mismo génesis, otro orden de claves
    nodo = Nodo("N02", 1, DIRECTORIO["N02"], genesis)
    assert nodo.recibir_cadena(reordenada, genesis) == (True, "aceptada")
    assert list(nodo.cadena[0]) == list(genesis) and nodo.cadena[0] is not genesis


def test_cadena_mas_corta_igual_y_mas_larga():
    propia = cadena_pow(3)
    genesis = propia[0]
    assert evaluar_cadena_recibida(propia[:2], propia, genesis) == (
        False, "no es más larga que la propia (1 ≤ 3)", None)
    assert evaluar_cadena_recibida(copy.deepcopy(propia), propia, genesis) == (
        False, "no es más larga que la propia (3 ≤ 3)", None)
    mas_larga = extender_pow(propia, [tx("N07", "N08", 1)])
    acepta, motivo, libro = evaluar_cadena_recibida(mas_larga, propia, genesis)
    assert (acepta, motivo) == (True, "aceptada") and libro.altura == 4


def test_primero_validez_despues_largo():
    propia = cadena_pow(3)
    corta_e_invalida = copy.deepcopy(propia[:2])
    corta_e_invalida[1]["hash"] = "f" * 64
    acepta, motivo, _ = evaluar_cadena_recibida(corta_e_invalida, propia, propia[0])
    assert not acepta and motivo == "Bloque 1: el hash no coincide con el recalculado (bloque alterado)"


# ---------------------------------------------------------------- timestamp en el futuro

def test_bloque_fechado_en_el_futuro_se_rechaza_con_timestamp_max():
    genesis = genesis_de()
    ahora = INICIO_SIMULADO_MS + 10_000
    futuro = minar(nuevo_bloque(1, 10**15, [tx("N02", "N03", 5)], genesis["hash"], "N09", RECOMPENSA, "pow"))
    cadena = [genesis, futuro]
    # Sin límite (uso del contrato original) la cadena es válida...
    assert validar_cadena(cadena, genesis)[0]
    # ...pero con la hora del nodo se rechaza: si no, ningún bloque honesto podría sucederla.
    esperado = f"Bloque 1: el timestamp está en el futuro ({10**15} > {ahora})"
    assert validar_cadena(cadena, genesis, timestamp_max=ahora) == (False, esperado, None)
    assert evaluar_cadena_recibida(cadena, [genesis], genesis, timestamp_max=ahora) == (False, esperado, None)
    nodo = Nodo("N01", 0, DIRECTORIO["N01"], genesis)
    assert nodo.recibir_cadena(cadena, genesis, timestamp_max=ahora) == (False, esperado)
    assert nodo.cadena == [genesis] and nodo.libro.altura == 0
    assert validar_bloque(futuro, genesis, Libro(genesis), genesis, timestamp_max=ahora) == esperado


def test_bloque_justo_en_timestamp_max_se_acepta():
    genesis = genesis_de()
    cadena = cadena_pow(2, genesis)
    ultimo = cadena[-1]["timestamp"]
    assert validar_cadena(cadena, genesis, timestamp_max=ultimo)[0]
    assert validar_cadena(cadena, genesis, timestamp_max=ultimo - 1)[1].startswith(
        "Bloque 2: el timestamp está en el futuro")
    nodo = Nodo("N01", 0, DIRECTORIO["N01"], genesis)
    assert nodo.recibir_cadena(cadena, genesis, timestamp_max=ultimo) == (True, "aceptada")


# ---------------------------------------------------------------- cadenas malformadas

def _malformadas() -> list:
    genesis = genesis_de()
    bloque = bloque_pow([genesis], [tx("N02", "N03", 1)])
    return [
        None, [], [1, 2], "texto", 5, 3.5, True, {"numero": 0}, lista_anidada(),
        [genesis, {}], [genesis, None], [genesis, [1]], [genesis, "bloque"], [genesis, 7],
        [genesis, {"numero": 1}], [{}], [None], [lista_anidada()],
        [genesis, {**bloque, "numero": "1"}], [genesis, {**bloque, "numero": True}],
        [genesis, {**bloque, "timestamp": float("nan")}], [genesis, {**bloque, "nonce": 1.0}],
        [genesis, {**bloque, "transacciones": lista_anidada()}],
        [genesis, {**bloque, "recompensa": {"a": lista_anidada()}}],
        [genesis, {**bloque, "votos": [lista_anidada()]}],
        [genesis, {**bloque, "extra": 1}],
        [genesis, {k: v for k, v in bloque.items() if k != "firma"}],
        [genesis, {**bloque, 5: "clave numérica"}],
        [genesis, bloque, bloque],
    ]


@pytest.mark.parametrize("cadena", _malformadas())
def test_cadena_malformada_se_rechaza_sin_excepcion(cadena):
    genesis = genesis_de()
    valida, mensaje, libro = validar_cadena(cadena, genesis)
    assert valida is False and isinstance(mensaje, str) and mensaje and libro is None
    acepta, motivo, libro = evaluar_cadena_recibida(cadena, [genesis], genesis)
    assert acepta is False and isinstance(motivo, str) and libro is None
    nodo = Nodo("N01", 0, DIRECTORIO["N01"], genesis)
    assert nodo.recibir_cadena(cadena, genesis)[0] is False
    assert nodo.cadena == [genesis] and nodo.altura == 0


def test_mensajes_de_cadena_malformada():
    genesis = genesis_de()
    assert validar_cadena(None, genesis)[1] == "la cadena debe ser una lista no vacía de bloques"
    assert validar_cadena([], genesis)[1] == "la cadena debe ser una lista no vacía de bloques"
    assert validar_cadena([1, 2], genesis)[1].startswith("génesis distinto")
    assert validar_cadena([genesis, {}], genesis)[1].startswith("Bloque 1: estructura inválida")
    assert validar_cadena([genesis] * 100_001, genesis)[1] == "la cadena es demasiado larga (máximo 100000 bloques)"


@pytest.mark.parametrize("anterior", [None, {}, [], "x", {"numero": "0", "hash": 5}])
def test_validar_bloque_nunca_lanza(anterior):
    genesis = genesis_de()
    bloque = bloque_pow([genesis], [tx("N02", "N03", 1)])
    mensaje = validar_bloque(bloque, anterior, Libro(genesis), genesis)
    assert isinstance(mensaje, str) and mensaje.startswith("Bloque 1:")
    assert isinstance(validar_bloque(None, anterior, Libro(genesis), genesis), str)
    assert isinstance(validar_bloque(bloque, genesis, None, genesis), str)


def test_aplicar_bloque_solo_lanza_cadena_invalida():
    genesis = genesis_de()
    for raro in (None, {}, {"numero": 1}, [], {"numero": 1, "castigos": None}, {"numero": lista_anidada()}):
        with pytest.raises(CadenaInvalida):
            Libro(genesis).aplicar_bloque(raro)
    bloque = bloque_pow([genesis], [tx("N02", "N03", 1)])
    with pytest.raises(CadenaInvalida, match="una firma por transacción"):
        Libro(genesis).aplicar_bloque({**bloque, "firma": []})
    libro = Libro(genesis)
    libro.aplicar_bloque(bloque)
    with pytest.raises(CadenaInvalida, match="no sigue al último bloque aplicado"):
        libro.aplicar_bloque(bloque)


def test_numero_gigante_no_rompe_los_mensajes():
    """Python no convierte a texto un int de más de 4300 cifras: el mensaje no debe intentarlo."""
    genesis = genesis_de()
    bloque = {**bloque_pow([genesis], [tx("N02", "N03", 1)]), "numero": 10**5000}
    mensaje = validar_bloque(bloque, genesis, Libro(genesis), genesis)
    assert mensaje == "Bloque 1: estructura inválida: 'numero' debe ser un entero entre 0 y 1000000000"
    assert validar_bloque(bloque, {**genesis, "numero": 10**5000}, Libro(genesis), genesis) == (
        "Bloque ?: estructura inválida: 'numero' debe ser un entero entre 0 y 1000000000")
    with pytest.raises(CadenaInvalida, match=r"^Bloque \?: estructura inválida$"):
        Libro(genesis).aplicar_bloque(bloque)
    assert validar_cadena([genesis, bloque], genesis)[1].startswith("Bloque 1: estructura inválida")


# ---------------------------------------------------------------- PoS

APUESTAS = {"N01": 10, "N02": 20, "N03": 30}


def test_cadena_pos_valida_recompensa_inmediata():
    genesis = genesis_de("pos")
    bloque = bloque_pos([genesis], [tx("N05", "N06", 40)], APUESTAS)
    proponente = bloque["proponente"]
    assert proponente == sortear(bloque["validadores"], genesis["hash"], 1, 0)
    valida, mensaje, libro = validar_cadena([genesis, bloque], genesis)
    assert valida, mensaje
    assert libro.saldo(proponente) == SALDO_INICIAL + RECOMPENSA       # confirmaciones = 0
    assert libro.pendientes_de(proponente) == [] and libro.recompensas_pendientes == []
    assert libro.saldo("N05") == 60 and libro.saldo("N06") == 140
    assert invariante_ok(libro, genesis)


def test_pos_proponente_que_no_coincide_con_el_sorteo():
    genesis = genesis_de("pos")
    sorteado = sortear([{"id": i, "apuesta": a} for i, a in sorted(APUESTAS.items())], genesis["hash"], 1, 0)
    impostor = next(i for i in APUESTAS if i != sorteado)
    bloque = bloque_pos([genesis], [tx("N05", "N06", 1)], APUESTAS, proponente=impostor)
    error = error_del_bloque([genesis], bloque)
    assert error == (f"Bloque 1: el proponente {impostor} no coincide con el sorteo "
                     f"(salió {sorteado} en el intento 0)")


def test_pos_votos_insuficientes_y_umbral_exacto():
    genesis = genesis_de("pos")
    txs = [tx("N05", "N06", 1)]
    # A = 60 y 2/3 de A = 40. N01 + N03 = 40 (exacto): se acepta. Sólo N03 = 30: se rechaza.
    assert error_del_bloque([genesis], bloque_pos([genesis], txs, APUESTAS, a_favor={"N01", "N03"})) is None
    error = error_del_bloque([genesis], bloque_pos([genesis], txs, APUESTAS, a_favor={"N03"}))
    assert error.startswith("Bloque 1: votos insuficientes: 30 a favor de 60")


def test_pos_el_proponente_debe_firmar_su_bloque():
    """Guía §4 paso 4: el proponente construye el bloque "y lo firma" (con su voto a favor)."""
    genesis = genesis_de("pos")
    apuestas = {"N01": 10, "N02": 10, "N03": 10, "N04": 10}      # cualquiera sin el proponente suma 30/40
    candidato = candidato_pos([genesis], [tx("N05", "N06", 1)], apuestas)
    proponente = candidato["proponente"]
    otros = set(apuestas) - {proponente}
    en_contra = firmar_votos(copy.deepcopy(candidato), otros)          # alcanza 2/3 sin él
    esperado = f"Bloque 1: el proponente {proponente} no firmó su bloque (falta su voto a favor)"
    assert error_del_bloque([genesis], en_contra) == esperado
    sin_su_voto = copy.deepcopy(en_contra)                             # ni siquiera votó
    sin_su_voto["votos"] = [v for v in sin_su_voto["votos"] if v["validador"] != proponente]
    sin_su_voto["hash"] = hash_bloque(sin_su_voto)
    assert error_del_bloque([genesis], sin_su_voto) == esperado
    assert error_del_bloque([genesis], firmar_votos(copy.deepcopy(candidato), set(apuestas))) is None


def test_pos_voto_con_firma_alterada_o_sin_votos():
    genesis = genesis_de("pos")
    bloque = bloque_pos([genesis], [tx("N05", "N06", 1)], APUESTAS)
    bloque["votos"][0]["firma"] = "0" * 128
    bloque["hash"] = hash_bloque(bloque)
    assert error_del_bloque([genesis], bloque) == "Bloque 1: firma inválida en el voto de N01"
    sin_votos = candidato_pos([genesis], [tx("N05", "N06", 1)], APUESTAS)
    assert error_del_bloque([genesis], sin_votos).startswith("Bloque 1: votos insuficientes: 0 a favor")


def test_pos_validadores_mal_formados():
    genesis = genesis_de("pos")
    sin_validadores = nuevo_bloque(1, genesis["timestamp"] + 1000, [tx("N05", "N06", 1)], genesis["hash"],
                                   "N01", 50, "pos")
    assert error_del_bloque([genesis], sin_validadores) == "Bloque 1: el bloque PoS no tiene validadores"
    desordenados = candidato_pos([genesis], [tx("N05", "N06", 1)], APUESTAS)
    desordenados["validadores"].reverse()
    desordenados["hash"] = hash_bloque(desordenados)
    assert "no están ordenados por id" in error_del_bloque([genesis], desordenados)
    con_nonce = candidato_pos([genesis], [tx("N05", "N06", 1)], APUESTAS)
    con_nonce["nonce"] = 3
    con_nonce["hash"] = hash_bloque(con_nonce)
    assert error_del_bloque([genesis], con_nonce) == "Bloque 1: un bloque PoS debe tener nonce 0"


def test_pos_castigo_valido_quema_el_dinero():
    genesis = genesis_de("pos")
    bloque = bloque_pos([genesis], [tx("N05", "N06", 1)], APUESTAS, castigos=[castigo("N04", 40, 40)])
    valida, mensaje, libro = validar_cadena([genesis, bloque], genesis)
    assert valida, mensaje
    assert libro.saldo("N04") == 60 and libro.quemado == 40
    assert invariante_ok(libro, genesis)
    assert libro.total_en_circulacion() == 10 * SALDO_INICIAL + RECOMPENSA - 40


@pytest.mark.parametrize("regla_red, el_castigo, fragmento", [
    ("A", castigo("N04", 40, 20), "el castigo de N04 es 20 pero según la regla A debe ser 40"),
    ("A", castigo("N04", 40, 41), "el castigo de N04 (41) supera su apuesta (40)"),
    ("A", castigo("N04", 40, 15, regla="B", valor=30), "usa la regla B pero la red usa la regla A"),
    ("B", castigo("N04", 40, 16, regla="B", valor=30), "el castigo de N04 es 16 pero según la regla B debe ser 15"),
    ("A", castigo("N04", 150, 150), "el castigo de N04 (150) supera su saldo (100)"),
    ("A", castigo("N77", 10, 10), "castigo a un nodo inexistente (N77)"),
    ("A", castigo("N04", 10, 10, numero=5), "castigo de un bloque futuro (5)"),
])
def test_pos_castigo_invalido(regla_red, el_castigo, fragmento):
    genesis = genesis_de("pos", regla_castigo=regla_red, alfa_porcentaje=50)
    bloque = bloque_pos([genesis], [tx("N05", "N06", 1)], APUESTAS, castigos=[el_castigo])
    error = error_del_bloque([genesis], bloque)
    assert error is not None and error.startswith("Bloque 1:") and fragmento in error


def test_pos_castigo_regla_b_correcto():
    genesis = genesis_de("pos", regla_castigo="B", alfa_porcentaje=50)
    bloque = bloque_pos([genesis], [tx("N05", "N06", 1)], APUESTAS,
                        castigos=[castigo("N04", 40, 15, regla="B", valor=30)])   # min(40, ⌈0.5·30⌉) = 15
    valida, mensaje, libro = validar_cadena([genesis, bloque], genesis)
    assert valida, mensaje
    assert libro.quemado == 15 and invariante_ok(libro, genesis)


def test_pos_apuesta_mayor_al_saldo():
    genesis = genesis_de("pos")
    bloque = bloque_pos([genesis], [tx("N05", "N06", 1)], {"N01": 150, "N02": 20})
    assert "la apuesta de N01 (150) supera su saldo (100)" in error_del_bloque([genesis], bloque)


def test_pos_validador_gasta_su_apuesta_bloqueada():
    genesis = genesis_de("pos")
    bloque = bloque_pos([genesis], [tx("N01", "N05", 50)], {"N01": 80, "N02": 20})
    error = error_del_bloque([genesis], bloque)
    assert error == ("Bloque 1: N01 gastó dinero de su apuesta bloqueada "
                     "(apostó 80 y tras las transacciones le quedan 50)")


def test_validar_candidato():
    genesis = genesis_de("pos")
    cadena = [genesis]
    libro = Libro(genesis)
    candidato = candidato_pos(cadena, [tx("N05", "N06", 1)], APUESTAS)
    assert validar_candidato(cadena, libro, candidato, genesis) == (True, "candidato válido")
    assert libro.altura == 0 and libro.saldo("N05") == SALDO_INICIAL           # el libro no se tocó
    con_votos = firmar_votos(copy.deepcopy(candidato), set(APUESTAS))
    assert validar_candidato(cadena, libro, con_votos, genesis) == (
        False, "Bloque 1: el candidato todavía no debe llevar votos")
    tramposo = candidato_pos(cadena, [tx("N05", "N06", 1)], APUESTAS, recompensa=500)
    acepta, motivo = validar_candidato(cadena, libro, tramposo, genesis)
    assert not acepta and "recompensa falsa" in motivo
    assert validar_candidato(cadena, libro, None, genesis)[0] is False
    assert validar_candidato([], libro, candidato, genesis)[0] is False


def test_invariante_en_cadena_pos_larga():
    genesis = genesis_de("pos")
    cadena = [genesis]
    for i in range(6):
        castigos = [castigo("N09", 10, 10, numero=i + 1)] if i % 2 else []
        cadena.append(bloque_pos(cadena, [tx("N07", "N08", 3)], APUESTAS, castigos=castigos))
    valida, mensaje, libro = validar_cadena(cadena, genesis)
    assert valida, mensaje
    assert libro.quemado == 30 and libro.altura == 6 and invariante_ok(libro, genesis)


def test_invariante_en_cadena_pow_larga():
    cadena = cadena_pow(9)
    valida, _, libro = validar_cadena(cadena, cadena[0])
    assert valida and invariante_ok(libro, cadena[0])
    assert libro.total_en_circulacion() == 10 * SALDO_INICIAL + 9 * RECOMPENSA


# ---------------------------------------------------------------- validar_transaccion_nueva

def validar_nueva(t, libro=None, pendientes=(), comprometido=None, timestamp_max=INICIO_SIMULADO_MS + 10**9):
    genesis = genesis_de()
    validar_transaccion_nueva(t, libro or Libro(genesis), DIRECTORIO, list(pendientes), comprometido or {},
                              timestamp_max)


def test_transaccion_nueva_valida():
    validar_nueva(tx("N01", "N02", 100))          # puede enviar todo su saldo


@pytest.mark.parametrize("hacer, tipo, codigo, fragmento", [
    (lambda: "no es objeto", EntradaInvalida, "entrada_invalida", "debe ser un objeto"),
    (lambda: {k: v for k, v in tx("N01", "N02", 5).items() if k != "firma"}, EntradaInvalida,
     "entrada_invalida", "le falta el campo 'firma'"),
    (lambda: {**tx("N01", "N02", 5), "extra": 1}, EntradaInvalida, "entrada_invalida", "campo no permitido"),
    (lambda: {**tx("N01", "N02", 5), "id": "a" * 64}, EntradaInvalida, "entrada_invalida",
     "El id de la transacción no coincide"),
    (lambda: tx("N99", "N02", 5), NoEncontrado, "nodo_inexistente", "El emisor: el nodo 'N99' no existe"),
    (lambda: tx("N01", "N42", 5), NoEncontrado, "nodo_inexistente", "El receptor: el nodo 'N42' no existe"),
    (lambda: tx("N01", "N01", 5), EntradaInvalida, "entrada_invalida", "deben ser distintos"),
    (lambda: tx("N01", "N02", -5), EntradaInvalida, "entrada_invalida", "El monto no puede ser negativo"),
    (lambda: tx("N01", "N02", 0), EntradaInvalida, "entrada_invalida", "El monto debe ser mayor que cero"),
    (lambda: tx("N01", "N02", "abc"), EntradaInvalida, "entrada_invalida", "debe ser un número entero"),
    (lambda: tx("N01", "N02", None), EntradaInvalida, "entrada_invalida", "El monto es obligatorio"),
    (lambda: tx("N01", "N02", "5"), EntradaInvalida, "entrada_invalida", "sin comillas ni decimales"),
    (lambda: tx("N01", "N02", 5.0), EntradaInvalida, "entrada_invalida", "sin comillas ni decimales"),
    (lambda: tx("N01", "N02", True), EntradaInvalida, "entrada_invalida", "debe ser un número entero"),
    (lambda: tx("N01", "N02", 10**13), EntradaInvalida, "entrada_invalida", "demasiado grande"),
    (lambda: tx("N01", "N02", 5, timestamp=INICIO_SIMULADO_MS + 10**10), EntradaInvalida, "entrada_invalida",
     "El timestamp está en el futuro"),
    (lambda: tx("N01", "N02", 5, clave=CLAVES["N02"][0]), EntradaInvalida, "firma_invalida",
     "Firma inválida: la transacción fue alterada o no la firmó el emisor (N01)"),
    (lambda: {**tx("N01", "N02", 5), "firma": "zz"}, EntradaInvalida, "firma_invalida", "Firma inválida"),
    (lambda: {**tx("N01", "N02", 5), "firma": None}, EntradaInvalida, "firma_invalida", "Firma inválida"),
    (lambda: tx("N01", "N02", 101), EntradaInvalida, "saldo_insuficiente",
     "Saldo insuficiente: N01 tiene 100 disponibles e intenta enviar 101"),
    # Python ≤ 3.13 no puede serializar 10 000 niveles ("estructura inválida"); 3.14 sí, y entonces
    # el id ya no coincide con los datos. En ambos casos: 400 entrada_invalida, nunca otra excepción.
    (lambda: {**tx("N01", "N02", 5), "emisor": lista_anidada()}, EntradaInvalida, "entrada_invalida",
     ("estructura inválida", "id de la transacción no coincide")),
])
def test_transaccion_nueva_errores(hacer, tipo, codigo, fragmento):
    with pytest.raises(tipo) as info:
        validar_nueva(hacer())
    fragmentos = fragmento if isinstance(fragmento, tuple) else (fragmento,)
    assert info.value.codigo == codigo and any(f in info.value.mensaje for f in fragmentos)


def test_transaccion_nueva_firma_alterada():
    t = tx("N01", "N02", 5)
    t["firma"] = ("1" if t["firma"][0] == "0" else "0") + t["firma"][1:]
    with pytest.raises(EntradaInvalida) as info:
        validar_nueva(t)
    assert info.value.codigo == "firma_invalida"


def test_transaccion_nueva_repetida_en_cadena_y_en_pendientes():
    registrada = tx("N02", "N03", 10)
    cadena = extender_pow([genesis_de()], [registrada])
    _, _, libro = validar_cadena(cadena, cadena[0])
    with pytest.raises(Conflicto) as info:
        validar_nueva(dict(registrada), libro=libro)
    assert info.value.codigo == "doble_gasto"
    assert info.value.mensaje == "Transacción repetida: ya está registrada en el bloque 1 (doble gasto)"
    pendiente = tx("N04", "N05", 10)
    with pytest.raises(Conflicto) as info:
        validar_nueva(dict(pendiente), pendientes=[pendiente])
    assert info.value.codigo == "doble_gasto" and "lista de pendientes" in info.value.mensaje


def test_saldo_insuficiente_con_comprometido_y_recompensas_no_maduras():
    cadena = extender_pow([genesis_de()], [tx("N05", "N06", 1)], proponente="N01")
    _, _, libro = validar_cadena(cadena, cadena[0])
    with pytest.raises(EntradaInvalida) as info:
        validar_nueva(tx("N01", "N02", 120), libro=libro)
    assert info.value.codigo == "saldo_insuficiente"
    assert info.value.mensaje == ("Saldo insuficiente: N01 tiene 100 disponibles e intenta enviar 120 "
                                  "(50 en recompensas aún no maduras; la primera madura en 6 bloques)")
    with pytest.raises(EntradaInvalida) as info:
        validar_nueva(tx("N02", "N03", 50), comprometido={"N02": 70})
    assert info.value.mensaje.startswith("Saldo insuficiente: N02 tiene 30 disponibles e intenta enviar 50")
    assert "70 ya están comprometidos" in info.value.mensaje
    validar_nueva(tx("N02", "N03", 30), comprometido={"N02": 70})     # justo lo disponible


def test_validar_transaccion_nueva_solo_lanza_errores_de_simulacion():
    for raro in (None, 5, [], {"id": 1}, {**tx("N01", "N02", 5), "emisor": ["N01"]},
                 {**tx("N01", "N02", 5), "timestamp": float("nan")}, {**tx("N01", "N02", 5), 3: "x"}):
        with pytest.raises(ErrorSimulacion):
            validar_nueva(raro)


# ---------------------------------------------------------------- Nodo

def test_nodo_inicial_y_resumen():
    genesis = genesis_de()
    nodo = Nodo("N03", 2, DIRECTORIO["N03"], genesis)
    assert nodo.altura == 0 and nodo.ultimo_hash == genesis["hash"]
    assert nodo.cadena[0] == genesis and nodo.cadena[0] is not genesis
    assert nodo.resumen() == {"id": "N03", "altura": 0, "ultimo_hash": genesis["hash"], "conectado": True,
                              "deshonesto": False, "trampa": None}


def test_nodo_recibir_cadena_guarda_copia_profunda():
    cadena = cadena_pow(2)
    genesis = cadena[0]
    nodo = Nodo("N01", 0, DIRECTORIO["N01"], genesis)
    assert nodo.recibir_cadena(cadena, genesis) == (True, "aceptada")
    assert nodo.altura == 2 and nodo.libro.altura == 2
    cadena[1]["transacciones"][0]["monto"] = 999          # quien la envió la modifica después
    cadena[2]["hash"] = "x"
    cadena.append({"basura": True})
    assert nodo.altura == 2 and nodo.cadena[1]["transacciones"][0]["monto"] == 1
    assert nodo.cadena[1] is not cadena[1]
    assert validar_cadena(nodo.cadena, genesis)[0]
    assert nodo.recibir_cadena(nodo.cadena, genesis) == (False, "no es más larga que la propia (2 ≤ 2)")


# ---------------------------------------------------------------- Red

def test_red_ids_nodo_y_nodo_inexistente():
    red = nueva_red()
    assert red.ids() == IDS
    assert red.nodo("N04").indice == 3 and red.nodo("N04").clave_publica == DIRECTORIO["N04"]
    for raro in ("N99", "n01", 5, None, ["N01"], {"a": 1}):
        with pytest.raises(NoEncontrado) as info:
            red.nodo(raro)
        assert info.value.codigo == "nodo_inexistente"
    assert red.sincronizados() and len(red.conectados()) == 10


def test_textos_con_sustitutos_sueltos_dan_mensajes_codificables():
    """Un "\\ud800" suelto (válido en JSON) nunca debe llegar crudo a un mensaje: no se puede escribir en UTF-8."""
    sustituto = json.loads('"\\ud800"')
    red = nueva_red()
    with pytest.raises(NoEncontrado) as info:
        red.nodo(sustituto)
    assert "\\ud800" in info.value.mensaje and info.value.mensaje.encode("utf-8")

    rara = tx(sustituto, "N02", 5)                         # firmada y con su id correcto
    with pytest.raises(NoEncontrado) as info:
        red.agregar_transaccion(rara, INICIO_SIMULADO_MS + 10**9)
    assert info.value.mensaje.startswith("El emisor: el nodo '\\ud800' no existe")
    assert info.value.mensaje.encode("utf-8") and red.pendientes == []

    genesis = red.genesis
    bloque = minar(nuevo_bloque(1, INICIO_SIMULADO_MS + 100_000, [rara], genesis["hash"], "N01", RECOMPENSA, "pow"))
    valida, mensaje, _ = validar_cadena([genesis, bloque], genesis)
    assert not valida and mensaje.startswith("Bloque 1: estructura inválida") and mensaje.encode("utf-8")
    acepto, motivo = red.nodo("N05").recibir_cadena([genesis, bloque], genesis)
    assert not acepto and motivo.encode("utf-8")


def test_red_difundir_acepta_rechaza_y_desconectado():
    red = nueva_red()
    cadena = cadena_pow(2, red.genesis)
    red.nodo("N05").conectado = False
    assert red.nodo("N01").recibir_cadena(cadena, red.genesis)[0]
    resultados = red.difundir(cadena, "N01")
    assert [r["nodo"] for r in resultados] == IDS[1:]
    por_nodo = {r["nodo"]: r for r in resultados}
    assert por_nodo["N05"] == {"nodo": "N05", "acepto": False, "motivo": "desconectado"}
    assert all(r["acepto"] and r["motivo"] == "aceptada" for i, r in por_nodo.items() if i != "N05")
    assert red.nodo("N02").cadena[1] is not red.nodo("N03").cadena[1]      # cada nodo con su copia
    assert not red.sincronizados()
    # La misma cadena otra vez: no es más larga.
    otra_vez = red.difundir(cadena, "N01")
    assert all(not r["acepto"] for r in otra_vez)
    assert {r["motivo"] for r in otra_vez} == {"desconectado", "no es más larga que la propia (2 ≤ 2)"}
    # Una cadena alterada más larga: todos la rechazan y nadie cambia.
    alterada = extender_pow(cadena, [tx("N07", "N08", 1)])
    alterada[1]["transacciones"][0]["monto"] = 50
    rechazos = red.difundir(alterada, "N02")
    conectados = [r for r in rechazos if r["motivo"] != "desconectado"]
    assert conectados and all(not r["acepto"] and r["motivo"].startswith("Bloque 1:") for r in conectados)
    assert all(red.nodo(i).altura == 2 for i in IDS if i != "N05")


def test_red_sincronizar_tras_reconexion():
    red = nueva_red()
    cadena = cadena_pow(3, red.genesis)
    red.nodo("N05").conectado = False
    red.nodo("N01").recibir_cadena(cadena, red.genesis)
    red.difundir(cadena, "N01")
    assert red.nodo("N05").altura == 0 and not red.sincronizados()
    red.nodo("N05").conectado = True
    assert red.sincronizar("N05") == {"nodo": "N05", "altura_antes": 0, "altura_despues": 3, "adopto_de": "N01"}
    assert red.sincronizados() and red.nodo("N05").cadena == cadena
    assert red.sincronizar("N05") == {"nodo": "N05", "altura_antes": 3, "altura_despues": 3, "adopto_de": None}
    with pytest.raises(NoEncontrado):
        red.sincronizar("N77")


def test_red_sincronizar_ignora_nodos_desconectados():
    red = nueva_red()
    cadena = cadena_pow(1, red.genesis)
    red.nodo("N02").recibir_cadena(cadena, red.genesis)
    red.nodo("N02").conectado = False                     # el único con la cadena larga está desconectado
    assert red.sincronizar("N03")["adopto_de"] is None


def test_red_nodo_referencia():
    red = nueva_red()
    assert red.nodo_referencia().id == "N01"
    cadena = cadena_pow(1, red.genesis)
    red.nodo("N02").conectado = False
    red.difundir(cadena, "N01")                            # N01 (origen) y N02 (desconectado) se quedan atrás
    assert red.nodo_referencia().id == "N03"              # mayor altura; empate ⇒ menor id
    assert red.libro_referencia() is red.nodo("N03").libro
    for i in IDS[2:]:
        red.nodo(i).conectado = False
    assert red.nodo_referencia().id == "N01"              # sólo N01 sigue conectado
    red.nodo("N01").conectado = False
    assert red.nodo_referencia().id == "N03"              # nadie conectado: el de mayor altura


def test_red_comprometido_y_disponible():
    red = nueva_red()
    red.agregar_transaccion(tx("N01", "N02", 30), INICIO_SIMULADO_MS + 10**9)
    red.castigos_pendientes.append(castigo("N01", 20, 20))
    apuestas = {"N01": 40, "N03": 10}
    comprometido = red.comprometido(apuestas)
    assert comprometido["N01"] == 90 and comprometido["N03"] == 10 and comprometido["N02"] == 0
    assert set(comprometido) == set(IDS)
    assert red.disponible("N01", apuestas) == 10
    assert red.disponible("N01") == 50
    assert red.disponible("N02", apuestas) == 100          # lo que recibe en pendientes aún no cuenta
    assert red.disponible("N01", {"N01": 500}) == 0        # nunca negativo
    with pytest.raises(NoEncontrado):
        red.disponible("N99")
    with pytest.raises(EntradaInvalida) as info:
        red.agregar_transaccion(tx("N01", "N03", 20), INICIO_SIMULADO_MS + 10**9, apuestas)
    assert info.value.codigo == "saldo_insuficiente" and "N01 tiene 10 disponibles" in info.value.mensaje
    assert len(red.pendientes) == 1


def test_red_agregar_transaccion_y_errores_del_contrato():
    red = nueva_red()
    maximo = INICIO_SIMULADO_MS + 10**9
    t = tx("N02", "N03", 10)
    guardada = red.agregar_transaccion(t, maximo)
    assert guardada == t and red.pendientes == [t]
    guardada["monto"] = 999                                # la copia devuelta no es la de pendientes
    t["monto"] = 999
    assert red.pendientes[0]["monto"] == 10
    casos = [
        (dict(red.pendientes[0]), "doble_gasto"),
        (tx("N02", "N03", 10, clave=CLAVES["N09"][0]), "firma_invalida"),
        (tx("N02", "N03", 91), "saldo_insuficiente"),     # 100 - 10 comprometidos
        (tx("N99", "N03", 1), "nodo_inexistente"),
        (tx("N02", "N02", 1), "entrada_invalida"),
        (tx("N02", "N03", 1, timestamp=maximo + 1), "entrada_invalida"),
        ("no es una transacción", "entrada_invalida"),
    ]
    for mala, codigo in casos:
        with pytest.raises(ErrorSimulacion) as info:
            red.agregar_transaccion(mala, maximo)
        assert info.value.codigo == codigo
    assert len(red.pendientes) == 1                         # ningún rechazo cambia las pendientes


def test_red_quitar_y_revalidar_pendientes():
    red = nueva_red()
    maximo = INICIO_SIMULADO_MS + 10**9
    incluida = red.agregar_transaccion(tx("N02", "N03", 60), maximo)
    sin_fondos = red.agregar_transaccion(tx("N04", "N05", 10), maximo)
    sigue = red.agregar_transaccion(tx("N06", "N07", 5), maximo)
    # Se acepta un bloque con la primera y con un gasto de N04 que lo deja sin fondos para la segunda.
    cadena = extender_pow([red.genesis], [incluida, tx("N04", "N08", 95)])
    red.nodo("N01").recibir_cadena(cadena, red.genesis)
    red.difundir(cadena, "N01")
    descartadas = red.revalidar_pendientes()
    assert [d["id"] for d in descartadas] == [incluida["id"], sin_fondos["id"]]
    assert "ya está registrada en el bloque 1" in descartadas[0]["motivo"]
    assert descartadas[1]["motivo"].startswith("Saldo insuficiente: N04 tiene 5 disponibles")
    assert red.pendientes == [sigue]
    assert red.revalidar_pendientes() == []
    # Con apuestas bloqueadas, una pendiente que ya no alcanza también se descarta.
    otra = red.agregar_transaccion(tx("N07", "N08", 80), maximo)
    assert [d["id"] for d in red.revalidar_pendientes({"N07": 30})] == [otra["id"]]
    red.quitar_pendientes({sigue["id"]})
    assert red.pendientes == []


def test_red_requiere_nodos():
    with pytest.raises(ValueError):
        Red(genesis_de(), [])
