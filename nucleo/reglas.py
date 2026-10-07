"""Reglas puras del consenso: dificultad PoW, sorteo PoS, umbral de 2/3, castigo y votos.

No guardan estado: reciben datos y devuelven un resultado. Por eso cualquier
nodo puede repetir la cuenta y llegar a lo mismo.
"""

import hashlib

from nucleo.bloque import hash_sin_votos
from nucleo.cripto import verificar


def cumple_dificultad(hash_hex: str, dificultad: int) -> bool:
    """PoW: el hash debe empezar con `dificultad` ceros hexadecimales."""
    return isinstance(hash_hex, str) and hash_hex.startswith("0" * dificultad)


def sortear(validadores: list[dict], hash_anterior: str, numero: int, intento: int) -> str:
    """Sorteo ponderado de la guía: devuelve el id del proponente.

    validadores = [{"id", "apuesta"}] en orden fijo (por id). La semilla es pública,
    así que cualquiera puede comprobar el resultado. P(i) = a_i / A.
    """
    semilla = f"{hash_anterior}|{numero}|{intento}".encode()
    A = sum(v["apuesta"] for v in validadores)
    if not validadores or A <= 0:
        raise ValueError("no se puede sortear sin validadores con apuesta")
    r = int(hashlib.sha256(semilla).hexdigest(), 16) % A
    acum = 0
    for v in validadores:
        acum += v["apuesta"]
        if r < acum:
            return v["id"]
    raise ValueError("apuestas inválidas para el sorteo")


def probabilidades(validadores: list[dict]) -> dict[str, float]:
    """Probabilidad de cada validador de ser sorteado: a_i / A."""
    A = sum(v["apuesta"] for v in validadores)
    if A <= 0:
        return {v["id"]: 0.0 for v in validadores}
    return {v["id"]: v["apuesta"] / A for v in validadores}


def alcanza_umbral(votos_favor: int, total: int) -> bool:
    """Se acepta si los votos a favor suman al menos 2/3 de A: 3V >= 2A (sin decimales)."""
    return total > 0 and 3 * votos_favor >= 2 * total


def mensaje_voto(hash_candidato: str, validador: str, voto: bool) -> bytes:
    """Bytes que firma un validador al votar."""
    return f"voto|{hash_candidato}|{validador}|{'si' if voto else 'no'}".encode()


def calcular_castigo(regla: str, apuesta: int, valor_transacciones: int, alfa_porcentaje: int) -> int:
    """Castigo al proponente rechazado.

    A: pierde toda su apuesta. B: c = min(apuesta, techo(alfa * valor)), al menos 1,
    con aritmética entera (-(-x // 100) es el techo de x / 100).
    """
    if regla == "A":
        return apuesta
    if regla == "B":
        return min(apuesta, max(1, -(-alfa_porcentaje * valor_transacciones // 100)))
    raise ValueError(f"regla de castigo desconocida: {regla!r}")


def verificar_votos_bloque(bloque, directorio) -> str | None:
    """Revisa los votos de un bloque PoS (regla de consenso c). Devuelve un mensaje o None.

    Votos firmados, sin repetir, con peso = apuesta y al menos 2/3 de A a favor;
    además, el proponente debe haber firmado su bloque con un voto a favor.
    Nunca lanza: si algo no tiene la forma esperada lo informa como error.
    """
    try:
        return _revisar_votos(bloque, directorio)
    except Exception:
        return "los votos o los validadores del bloque tienen una estructura inválida"


def _revisar_votos(bloque: dict, directorio: dict) -> str | None:
    validadores = bloque["validadores"]
    if not isinstance(validadores, list) or not validadores:
        return "el bloque no tiene validadores"
    if not isinstance(directorio, dict):
        return "no hay directorio de claves para verificar los votos"

    apuestas: dict[str, int] = {}
    for v in validadores:
        if v["id"] in apuestas:
            return f"el validador {v['id']} aparece dos veces"
        if type(v["apuesta"]) is not int or v["apuesta"] < 1:
            return f"la apuesta del validador {v['id']} debe ser un entero mayor que cero"
        apuestas[v["id"]] = v["apuesta"]
    ids = list(apuestas)
    if ids != sorted(ids):
        return "los validadores no están ordenados por id"
    for id_validador in ids:
        if id_validador not in directorio:
            return f"el validador {id_validador} no está en el directorio del génesis"

    hash_candidato = hash_sin_votos(bloque)
    ya_votaron: set[str] = set()
    votos_favor = 0
    for voto in bloque["votos"]:
        quien = voto["validador"]
        if quien not in apuestas:
            return f"{quien} votó sin ser validador de este bloque; su voto no cuenta"
        if quien in ya_votaron:
            return f"{quien} vota dos veces"
        ya_votaron.add(quien)
        if type(voto["voto"]) is not bool:
            return f"el voto de {quien} debe ser verdadero o falso"
        if type(voto["peso"]) is not int or voto["peso"] != apuestas[quien]:
            return f"el voto de {quien} pesa {voto['peso']} pero su apuesta es {apuestas[quien]}"
        mensaje = mensaje_voto(hash_candidato, quien, voto["voto"])
        if not verificar(directorio[quien], mensaje, voto["firma"]):
            return f"firma inválida en el voto de {quien}"
        if voto["voto"] is True:
            votos_favor += voto["peso"]

    total = sum(apuestas.values())
    if not alcanza_umbral(votos_favor, total):
        return (f"votos insuficientes: {votos_favor} a favor de {total} "
                f"(se necesitan al menos 2/3: 3V ≥ 2A)")

    # Guía §4 paso 4: el proponente construye el bloque "y lo firma". Su voto a favor,
    # firmado sobre hash_sin_votos(bloque) y ya verificado arriba, es esa firma.
    proponente = bloque["proponente"]
    if not any(v["validador"] == proponente and v["voto"] is True for v in bloque["votos"]):
        return f"el proponente {proponente} no firmó su bloque (falta su voto a favor)"
    return None
