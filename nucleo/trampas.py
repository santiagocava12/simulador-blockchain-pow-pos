"""Comportamiento de los nodos deshonestos.

Un tramposo sólo engaña en lo que PROPONE (transacciones o recompensa de su
bloque) o en cómo VOTA. Su propia copia de la cadena siempre se valida, así que
nunca se corrompe; los demás nodos detectan la trampa y rechazan el bloque.
"""

import copy

from nucleo.bloque import crear_transaccion

TRAMPAS_PROPONENTE = ("recompensa_falsa", "firma_alterada", "gasto_excesivo", "doble_gasto")
TRAMPAS_VOTANTE = ("voto_invertido",)
TRAMPAS = TRAMPAS_PROPONENTE + TRAMPAS_VOTANTE


def _alterar_firma(firma: str) -> str:
    """Cambia el primer carácter hex por otro distinto: la firma deja de ser válida."""
    nuevo = "1" if firma[:1] == "0" else "0"
    return nuevo + firma[1:]


def _saldo_maximo(nodo_id: str, saldo_nodo: int, pendientes: list[dict]) -> int:
    """Lo más que el tramposo podría tener dentro de su bloque.

    Si el bloque también incluye pendientes que le PAGAN, su saldo sube mientras
    se aplica el bloque. Se suman todas para que la trampa nunca alcance a ser
    válida, sin importar en qué lugar del bloque quede.
    """
    entrantes = sum(p["monto"] for p in pendientes
                    if isinstance(p, dict) and p.get("receptor") == nodo_id
                    and type(p.get("monto")) is int and p["monto"] > 0)
    return max(0, saldo_nodo) + entrantes


def transacciones_tramposas(trampa: str, nodo_id: str, otro_id: str, saldo_nodo: int,
                            clave_privada_nodo, reloj, pendientes: list[dict]) -> list[dict]:
    """Transacciones firmadas que el tramposo mete en su bloque según su trampa.

    `saldo_nodo` es su saldo en la cadena. Usan `reloj.ahora()`: hay que llamarla
    ANTES de tomar el timestamp del bloque (una transacción no puede ser posterior
    a su bloque, y entonces el rechazo no diría la causa real).
    """
    if trampa == "firma_alterada":
        if pendientes:
            tx = copy.deepcopy(pendientes[0])
        else:
            tx = crear_transaccion(nodo_id, otro_id, 1, reloj.ahora(), clave_privada_nodo)
        tx["firma"] = _alterar_firma(tx["firma"])
        return [tx]

    if trampa == "gasto_excesivo":
        monto = _saldo_maximo(nodo_id, saldo_nodo, pendientes) + 1    # uno más de lo que puede tener
        return [crear_transaccion(nodo_id, otro_id, monto, reloj.ahora(), clave_privada_nodo)]

    if trampa == "doble_gasto":
        # Cada una por separado cabe en lo que puede llegar a tener; juntas lo superan.
        monto = max(1, _saldo_maximo(nodo_id, saldo_nodo, pendientes) // 2 + 1)
        return [crear_transaccion(nodo_id, otro_id, monto, reloj.ahora(), clave_privada_nodo)
                for _ in range(2)]

    return []   # "recompensa_falsa" y "voto_invertido" no agregan transacciones


def aplicar_trampa_recompensa(recompensa_monto: int, trampa: str | None) -> int:
    """La recompensa que se asigna el proponente: diez veces más si hace trampa."""
    if trampa == "recompensa_falsa":
        return recompensa_monto * 10
    return recompensa_monto
