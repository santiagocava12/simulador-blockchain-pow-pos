"""Transacciones y bloques: creación, hash canónico y revisión de estructura.

``serializar`` y ``hash_bloque`` son idénticas al código de la guía, para que
cualquier nodo (o el profesor) obtenga los mismos bytes y los mismos hashes.
"""

import copy
import hashlib
import json
import re

from nucleo.config import CONFIRMACIONES_POW
from nucleo.cripto import es_hex, firmar

CAMPOS_TX = ("emisor", "receptor", "monto", "timestamp")
CAMPOS_BLOQUE = ("numero", "timestamp", "transacciones", "firma", "hash_anterior", "nonce",
                 "proponente", "recompensa", "votos", "validadores", "intento", "castigos", "modo", "hash")
CAMPOS_GENESIS = CAMPOS_BLOQUE + ("directorio", "saldos_iniciales", "parametros")
HASH_CERO = "0" * 64
PROPONENTE_GENESIS = "GENESIS"
MAX_MONTO = 10**12

# Límites de la revisión de estructura (evitan valores absurdos o imposibles de serializar).
MAX_TIMESTAMP = 10**15
MAX_NUMERO = 10**9
MAX_NONCE = 2**63
LIMITE_ENTERO = 10**15        # tope para los enteros que el contrato sólo pide ">= 1" o ">= 0"
MAX_TX_BLOQUE = 60
MAX_VOTOS = 20
MAX_VALIDADORES = 20
MAX_CASTIGOS = 500
MAX_NODOS_GENESIS = 20
CAMPOS_RECOMPENSA = ("beneficiario", "monto")
CAMPOS_VOTO = ("validador", "peso", "voto", "firma")
CAMPOS_VALIDADOR = ("id", "apuesta")
CAMPOS_CASTIGO = ("nodo", "apuesta", "monto", "regla", "intento", "numero", "valor_transacciones", "motivo")
_SUSTITUTO_SUELTO = re.compile("[\ud800-\udfff]")   # mitades de pares sustitutos (no son texto válido)


# ---------------------------------------------------------------- transacciones

def serializar(tx) -> bytes:
    """Bytes canónicos que firma el emisor (orden fijo de llaves = misma firma en todos lados)."""
    datos = {k: tx[k] for k in ("emisor", "receptor", "monto", "timestamp")}
    return json.dumps(datos, sort_keys=True, separators=(",", ":")).encode()


def id_transaccion(tx) -> str:
    """Identificador de la transacción: SHA-256 de su serialización."""
    return hashlib.sha256(serializar(tx)).hexdigest()


def crear_transaccion(emisor: str, receptor: str, monto: int, timestamp: int, clave_privada) -> dict:
    """Transacción firmada: {"id", "emisor", "receptor", "monto", "timestamp", "firma"}."""
    datos = {"emisor": emisor, "receptor": receptor, "monto": monto, "timestamp": timestamp}
    mensaje = serializar(datos)
    return {
        "id": hashlib.sha256(mensaje).hexdigest(),
        **datos,
        "firma": firmar(clave_privada, mensaje),
    }


def payload(tx) -> dict:
    """Sólo los 4 campos firmados de una transacción."""
    return {k: tx[k] for k in CAMPOS_TX}


# ---------------------------------------------------------------- bloques

def hash_bloque(b) -> str:
    """SHA-256 de todos los campos del bloque menos "hash" (forma canónica de la guía)."""
    datos = {k: v for k, v in b.items() if k != "hash"}
    return hashlib.sha256(json.dumps(datos, sort_keys=True).encode()).hexdigest()


def hash_sin_votos(b) -> str:
    """Hash del bloque con la lista de votos vacía: es lo que firman los votos PoS."""
    return hash_bloque({**b, "votos": []})


def crear_genesis(config, directorio: dict[str, str], timestamp: int) -> dict:
    """Bloque 0: publica el directorio de claves, los saldos iniciales y los parámetros."""
    ids = sorted(directorio)
    genesis = {
        "numero": 0,
        "timestamp": timestamp,
        "transacciones": [],
        "firma": [],
        "hash_anterior": HASH_CERO,
        "nonce": 0,
        "proponente": PROPONENTE_GENESIS,
        "recompensa": None,
        "votos": [],
        "validadores": [],
        "intento": 0,
        "castigos": [],
        "modo": config.modo,
        "directorio": {i: directorio[i] for i in ids},
        "saldos_iniciales": {i: config.saldo_inicial for i in ids},
        "parametros": {
            "modo": config.modo,
            "dificultad": config.dificultad,
            "recompensa": config.recompensa,
            "confirmaciones": CONFIRMACIONES_POW if config.modo == "pow" else 0,
            "max_tx_por_bloque": config.max_tx_por_bloque,
            "regla_castigo": config.regla_castigo,
            "alfa_porcentaje": config.alfa_porcentaje,
            "semilla": config.semilla,
        },
    }
    genesis["hash"] = hash_bloque(genesis)
    return genesis


def nuevo_bloque(numero: int, timestamp: int, txs_firmadas: list[dict], hash_anterior: str,
                 proponente: str, recompensa_monto: int, modo: str, nonce: int = 0,
                 validadores: list[dict] | None = None, intento: int = 0,
                 castigos: list[dict] | None = None, votos: list[dict] | None = None) -> dict:
    """Arma un bloque con su hash. Copia las listas para no compartirlas con quien llama."""
    bloque = {
        "numero": numero,
        "timestamp": timestamp,
        "transacciones": [payload(t) for t in txs_firmadas],
        "firma": [t["firma"] for t in txs_firmadas],
        "hash_anterior": hash_anterior,
        "nonce": nonce,
        "proponente": proponente,
        "recompensa": {"beneficiario": proponente, "monto": recompensa_monto},
        "votos": copy.deepcopy(votos) if votos else [],
        "validadores": copy.deepcopy(validadores) if validadores else [],
        "intento": intento,
        "castigos": copy.deepcopy(castigos) if castigos else [],
        "modo": modo,
    }
    bloque["hash"] = hash_bloque(bloque)
    return bloque


# ---------------------------------------------------------------- revisión de estructura
# Sólo se comparan tipos exactos (type(x) is int, etc.) y nunca se recorre un valor
# de tipo inesperado: así ni una lista anidada 10 000 niveles puede provocar un error.

def _es_entero(valor, minimo: int, maximo: int) -> bool:
    """Entero (bool NO cuenta) dentro del rango."""
    return type(valor) is int and minimo <= valor <= maximo


def _es_texto(valor, minimo: int, maximo: int) -> bool:
    """Texto con largo dentro del rango y sin mitades sueltas de pares sustitutos.

    Un "\\ud800" suelto no se puede escribir en UTF-8: si se aceptara, cualquier
    mensaje que repita ese texto fallaría al guardarse o mostrarse.
    """
    return (type(valor) is str and minimo <= len(valor) <= maximo
            and _SUSTITUTO_SUELTO.search(valor) is None)


def _nombre_clave(clave) -> str:
    """Nombre de una clave para un mensaje, sin imprimir valores raros."""
    if type(clave) is str:
        return repr(clave[:30])
    return f"(clave de tipo {type(clave).__name__})"


def _revisar_claves(objeto, esperadas: tuple[str, ...], que: str) -> str | None:
    """El objeto debe ser un dict con EXACTAMENTE las claves esperadas."""
    if type(objeto) is not dict:
        return f"{que} debe ser un objeto"
    for campo in esperadas:
        if campo not in objeto:
            return f"{que}: falta el campo '{campo}'"
    if len(objeto) != len(esperadas):
        for clave in objeto:
            if not (type(clave) is str and clave in esperadas):
                return f"{que}: sobra el campo {_nombre_clave(clave)}"
    return None


def _revisar_tx(tx, que: str) -> str | None:
    error = _revisar_claves(tx, CAMPOS_TX, que)
    if error:
        return error
    for campo in ("emisor", "receptor"):
        if not _es_texto(tx[campo], 1, 16):
            return f"{que}: '{campo}' debe ser texto de 1 a 16 caracteres"
    if not _es_entero(tx["monto"], 1, MAX_MONTO):
        return f"{que}: 'monto' debe ser un entero entre 1 y {MAX_MONTO}"
    if not _es_entero(tx["timestamp"], 0, MAX_TIMESTAMP):
        return f"{que}: 'timestamp' debe ser un entero entre 0 y {MAX_TIMESTAMP}"
    return None


def validar_estructura_tx(tx) -> str | None:
    """Mensaje de error si la transacción (4 campos firmados) está mal formada; None si está bien."""
    try:
        return _revisar_tx(tx, "la transacción")
    except Exception:
        return "la transacción tiene una estructura inválida"


def _revisar_lista(valor, campo: str, maximo: int) -> str | None:
    if type(valor) is not list:
        return f"'{campo}' debe ser una lista"
    if len(valor) > maximo:
        return f"'{campo}' tiene demasiados elementos (máximo {maximo})"
    return None


def _revisar_votos(votos) -> str | None:
    error = _revisar_lista(votos, "votos", MAX_VOTOS)
    if error:
        return error
    for i, voto in enumerate(votos):
        que = f"voto {i}"
        error = _revisar_claves(voto, CAMPOS_VOTO, que)
        if error:
            return error
        if not _es_texto(voto["validador"], 1, 16):
            return f"{que}: 'validador' debe ser texto de 1 a 16 caracteres"
        if not _es_entero(voto["peso"], 1, LIMITE_ENTERO):
            return f"{que}: 'peso' debe ser un entero mayor o igual a 1"
        if type(voto["voto"]) is not bool:
            return f"{que}: 'voto' debe ser verdadero o falso"
        if not es_hex(voto["firma"], 128):
            return f"{que}: 'firma' debe ser hexadecimal de 128 caracteres"
    return None


def _revisar_validadores(validadores) -> str | None:
    error = _revisar_lista(validadores, "validadores", MAX_VALIDADORES)
    if error:
        return error
    for i, validador in enumerate(validadores):
        que = f"validador {i}"
        error = _revisar_claves(validador, CAMPOS_VALIDADOR, que)
        if error:
            return error
        if not _es_texto(validador["id"], 1, 16):
            return f"{que}: 'id' debe ser texto de 1 a 16 caracteres"
        if not _es_entero(validador["apuesta"], 1, LIMITE_ENTERO):
            return f"{que}: 'apuesta' debe ser un entero mayor o igual a 1"
    return None


def _revisar_castigos(castigos) -> str | None:
    error = _revisar_lista(castigos, "castigos", MAX_CASTIGOS)
    if error:
        return error
    for i, castigo in enumerate(castigos):
        que = f"castigo {i}"
        error = _revisar_claves(castigo, CAMPOS_CASTIGO, que)
        if error:
            return error
        if not _es_texto(castigo["nodo"], 1, 16):
            return f"{que}: 'nodo' debe ser texto de 1 a 16 caracteres"
        for campo, minimo in (("apuesta", 1), ("monto", 1), ("intento", 0), ("numero", 1),
                              ("valor_transacciones", 0)):
            if not _es_entero(castigo[campo], minimo, LIMITE_ENTERO):
                return f"{que}: '{campo}' debe ser un entero mayor o igual a {minimo}"
        if type(castigo["regla"]) is not str or castigo["regla"] not in ("A", "B"):
            return f"{que}: 'regla' debe ser \"A\" o \"B\""
        if not _es_texto(castigo["motivo"], 0, 200):
            return f"{que}: 'motivo' debe ser texto de hasta 200 caracteres"
    return None


def _revisar_extras_genesis(b: dict) -> str | None:
    directorio = b["directorio"]
    if type(directorio) is not dict or not 1 <= len(directorio) <= MAX_NODOS_GENESIS:
        return f"'directorio' debe ser un objeto con 1 a {MAX_NODOS_GENESIS} nodos"
    for clave, publica in directorio.items():
        if not _es_texto(clave, 1, 16) or not es_hex(publica, 64):
            return "'directorio' debe asociar cada id con una clave pública hex de 64 caracteres"
    saldos = b["saldos_iniciales"]
    if type(saldos) is not dict or len(saldos) > MAX_NODOS_GENESIS:
        return "'saldos_iniciales' debe ser un objeto {id: saldo}"
    for clave, saldo in saldos.items():
        if not _es_texto(clave, 1, 16) or not _es_entero(saldo, 0, LIMITE_ENTERO):
            return "'saldos_iniciales' debe asociar cada id con un saldo entero no negativo"
    parametros = b["parametros"]
    if type(parametros) is not dict or len(parametros) > 20:
        return "'parametros' debe ser un objeto"
    for clave, valor in parametros.items():
        escalar_valido = (
            valor is None
            or type(valor) is bool
            or _es_entero(valor, -LIMITE_ENTERO, LIMITE_ENTERO)
            or _es_texto(valor, 0, 200)
        )
        if not _es_texto(clave, 1, 40) or not escalar_valido:
            return "'parametros' sólo admite valores simples (texto, entero, verdadero/falso o nulo)"
    return None


def _revisar_bloque(b, es_genesis: bool) -> str | None:
    error = _revisar_claves(b, CAMPOS_GENESIS if es_genesis else CAMPOS_BLOQUE, "el bloque")
    if error:
        return error
    if not _es_entero(b["numero"], 0, MAX_NUMERO):
        return f"'numero' debe ser un entero entre 0 y {MAX_NUMERO}"
    if not _es_entero(b["timestamp"], 0, MAX_TIMESTAMP):
        return f"'timestamp' debe ser un entero entre 0 y {MAX_TIMESTAMP}"

    error = _revisar_lista(b["transacciones"], "transacciones", MAX_TX_BLOQUE)
    if error:
        return error
    for i, tx in enumerate(b["transacciones"]):
        error = _revisar_tx(tx, f"transacción {i}")
        if error:
            return error

    firmas = b["firma"]
    if type(firmas) is not list or len(firmas) != len(b["transacciones"]):
        return "'firma' debe ser una lista con una firma por transacción"
    for i, firma in enumerate(firmas):
        if not es_hex(firma, 128):
            return f"firma {i}: debe ser hexadecimal de 128 caracteres"

    if not es_hex(b["hash_anterior"], 64):
        return "'hash_anterior' debe ser hexadecimal de 64 caracteres"
    if not _es_entero(b["nonce"], 0, MAX_NONCE):
        return f"'nonce' debe ser un entero entre 0 y {MAX_NONCE}"
    if not _es_texto(b["proponente"], 1, 16):
        return "'proponente' debe ser texto de 1 a 16 caracteres"

    recompensa = b["recompensa"]
    if es_genesis:
        if recompensa is not None:
            return "'recompensa' del génesis debe ser nula"
    else:
        error = _revisar_claves(recompensa, CAMPOS_RECOMPENSA, "la recompensa")
        if error:
            return error
        if not _es_texto(recompensa["beneficiario"], 1, 16):
            return "la recompensa: 'beneficiario' debe ser texto de 1 a 16 caracteres"
        if not _es_entero(recompensa["monto"], 0, LIMITE_ENTERO):
            return f"la recompensa: 'monto' debe ser un entero entre 0 y {LIMITE_ENTERO}"

    for revisar, campo in ((_revisar_votos, "votos"), (_revisar_validadores, "validadores")):
        error = revisar(b[campo])
        if error:
            return error
    if not _es_entero(b["intento"], 0, 1000):
        return "'intento' debe ser un entero entre 0 y 1000"
    error = _revisar_castigos(b["castigos"])
    if error:
        return error
    if type(b["modo"]) is not str or b["modo"] not in ("pow", "pos"):
        return "'modo' debe ser \"pow\" o \"pos\""
    if not es_hex(b["hash"], 64):
        return "'hash' debe ser hexadecimal de 64 caracteres"
    if es_genesis:
        return _revisar_extras_genesis(b)
    return None


def validar_estructura_bloque(b, es_genesis: bool = False) -> str | None:
    """Mensaje de error si el bloque está mal formado (claves, tipos, tamaños); None si está bien.

    Nunca lanza: cualquier sorpresa se informa como estructura inválida.
    """
    try:
        error = _revisar_bloque(b, es_genesis)
    except Exception:
        return "estructura inválida: el bloque no se pudo revisar"
    return f"estructura inválida: {error}" if error else None


def resumen_bloque(b) -> dict:
    """Datos principales de un bloque para la interfaz."""
    transacciones = b.get("transacciones")
    votos = b.get("votos")
    return {
        "numero": b.get("numero"),
        "hash": b.get("hash"),
        "hash_anterior": b.get("hash_anterior"),
        "proponente": b.get("proponente"),
        "nonce": b.get("nonce"),
        "num_transacciones": len(transacciones) if isinstance(transacciones, list) else 0,
        "timestamp": b.get("timestamp"),
        "recompensa": copy.deepcopy(b.get("recompensa")),
        "num_votos": len(votos) if isinstance(votos, list) else 0,
        "castigos": copy.deepcopy(b.get("castigos") or []),
    }
