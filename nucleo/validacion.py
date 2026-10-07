"""Validación de bloques, cadenas y transacciones nuevas.

Reglas de aceptación de una cadena recibida (sección 2.4 de la guía):
  (a) cada hash coincide con el recalculado y cada hash_anterior con el hash del
      bloque previo,
  (b) todas las firmas de transacciones son válidas y ningún emisor gasta más de
      lo que tiene (incluido el doble gasto dentro del mismo bloque),
  (c) se cumple la regla del consenso (dificultad en PoW; sorteo y votos
      suficientes en PoS),
  (d) la cadena recibida es MÁS LARGA que la propia.
Primero se revisa la validez (a, b, c) y después el largo (d).

``validar_bloque``, ``validar_cadena``, ``evaluar_cadena_recibida`` y
``validar_candidato`` nunca lanzan excepciones: cualquier dato raro se convierte
en un mensaje de rechazo.
"""

import reprlib

from nucleo.bloque import (MAX_NUMERO, hash_bloque, hash_sin_votos, id_transaccion, serializar,
                           validar_estructura_bloque)
from nucleo.cripto import es_hex, verificar
from nucleo.entradas import leer_entero, leer_monto, texto_visible
from nucleo.errores import Conflicto, EntradaInvalida, ErrorSimulacion, NoEncontrado
from nucleo.libro import CadenaInvalida, Libro
from nucleo.reglas import cumple_dificultad, sortear, verificar_votos_bloque

MAX_BLOQUES_CADENA = 100_000
MAX_TIMESTAMP = 10**15
CAMPOS_TX_FIRMADA = ("id", "emisor", "receptor", "monto", "timestamp", "firma")

_REPR = reprlib.Repr()
_REPR.maxlevel = 2
_REPR.maxstring = 30
_REPR.maxother = 30


def _corto(valor) -> str:
    """Valor recibido, en pocas letras y sin riesgo (para mensajes)."""
    if isinstance(valor, str):
        return texto_visible(valor, 30)
    try:
        return _REPR.repr(valor)[:40]
    except Exception:
        return f"<{type(valor).__name__}>"


def _plural(n: int, singular: str, plural: str) -> str:
    """'1 bloque', '3 bloques'."""
    return f"{n} {singular if n == 1 else plural}"


# ---------------------------------------------------------------- bloques

def _es_numero_de_bloque(valor) -> bool:
    """Entero de 0 a MAX_NUMERO: se puede imprimir sin riesgo (un int de miles de cifras no)."""
    return type(valor) is int and 0 <= valor <= MAX_NUMERO


def _numero_para_mensaje(bloque, anterior) -> object:
    """Número que se muestra en "Bloque n: ...", aunque el bloque venga roto."""
    if isinstance(bloque, dict) and _es_numero_de_bloque(bloque.get("numero")):
        return bloque["numero"]
    if isinstance(anterior, dict) and _es_numero_de_bloque(anterior.get("numero")):
        return anterior["numero"] + 1
    return "?"


def _revisar_validadores(validadores: list[dict], directorio: dict) -> str | None:
    """PoS: lista no vacía, sin repetidos, ordenada por id y con nodos del directorio."""
    if not validadores:
        return "el bloque PoS no tiene validadores"
    ids = [v["id"] for v in validadores]
    if len(set(ids)) != len(ids):
        return "un validador aparece dos veces"
    if ids != sorted(ids):
        return "los validadores no están ordenados por id"
    for id_validador in ids:
        if id_validador not in directorio:
            return f"el validador {id_validador} no está en el directorio del génesis"
    return None


def _revisar_bloque(bloque, anterior: dict, libro: Libro, genesis: dict, exigir_votos: bool,
                    timestamp_max: int | None) -> str | None:
    n = _numero_para_mensaje(bloque, anterior)
    parametros = genesis["parametros"]
    directorio = genesis["directorio"]

    # 1. Forma: campos exactos, tipos y tamaños.
    error = validar_estructura_bloque(bloque)
    if error:
        return f"Bloque {n}: {error}"
    # 2. Número consecutivo.
    if bloque["numero"] != anterior["numero"] + 1:
        return f"Bloque {n}: el número no es consecutivo (se esperaba {anterior['numero'] + 1})"
    # 3. Enlace con el bloque anterior.
    if bloque["hash_anterior"] != anterior["hash"]:
        return f"Bloque {n}: hash_anterior no coincide con el hash del bloque {n - 1}"
    # 4. El hash guardado debe ser el de su contenido.
    if bloque["hash"] != hash_bloque(bloque):
        return f"Bloque {n}: el hash no coincide con el recalculado (bloque alterado)"
    # 5. Modo de la red y tiempo creciente.
    if bloque["modo"] != parametros["modo"]:
        return f"Bloque {n}: el modo {bloque['modo']} no coincide con el de la red ({parametros['modo']})"
    if bloque["timestamp"] <= anterior["timestamp"]:
        return f"Bloque {n}: el timestamp no es posterior al del bloque {n - 1}"
    # Un bloque fechado en el futuro bloquearía la cadena: nadie podría sucederlo.
    if timestamp_max is not None and bloque["timestamp"] > timestamp_max:
        return f"Bloque {n}: el timestamp está en el futuro ({bloque['timestamp']} > {timestamp_max})"
    # 6. Cantidad de transacciones (+2 deja ver la causa real de un bloque tramposo).
    total_tx = len(bloque["transacciones"])
    maximo = parametros["max_tx_por_bloque"]
    mensaje_maximo = f"Bloque {n}: tiene {total_tx} transacciones (máximo {maximo} por bloque)"
    if total_tx == 0:
        return f"Bloque {n}: sin transacciones de usuario"
    if total_tx > maximo + 2:
        return mensaje_maximo
    # 7. Consenso.
    error = (_revisar_consenso_pow(bloque, parametros) if parametros["modo"] == "pow"
             else _revisar_consenso_pos(bloque, directorio, exigir_votos))
    if error:
        return f"Bloque {n}: {error}"
    # 8. Saldos, firmas, doble gasto, castigos y recompensa.
    try:
        libro.aplicar_bloque(bloque)
    except CadenaInvalida as error:
        return str(error)
    # 9. Si todo lo demás está bien, el máximo del génesis se cumple sin margen.
    if total_tx > maximo:
        return mensaje_maximo
    return None


def _revisar_consenso_pow(bloque: dict, parametros: dict) -> str | None:
    dificultad = parametros["dificultad"]
    if not cumple_dificultad(bloque["hash"], dificultad):
        return f"el hash no cumple la dificultad ({_plural(dificultad, 'cero', 'ceros')})"
    if bloque["votos"] or bloque["validadores"] or bloque["castigos"]:
        return "un bloque PoW no lleva votos, validadores ni castigos"
    if bloque["intento"] != 0:
        return "un bloque PoW debe tener intento 0"
    return None


def _revisar_consenso_pos(bloque: dict, directorio: dict, exigir_votos: bool) -> str | None:
    if bloque["nonce"] != 0:
        return "un bloque PoS debe tener nonce 0"
    error = _revisar_validadores(bloque["validadores"], directorio)
    if error:
        return error
    sorteado = sortear(bloque["validadores"], bloque["hash_anterior"], bloque["numero"], bloque["intento"])
    if bloque["proponente"] != sorteado:
        return (f"el proponente {bloque['proponente']} no coincide con el sorteo "
                f"(salió {sorteado} en el intento {bloque['intento']})")
    if exigir_votos:
        return verificar_votos_bloque(bloque, directorio)
    if bloque["votos"]:
        return "el candidato todavía no debe llevar votos"
    if bloque["hash"] != hash_sin_votos(bloque):
        return "el hash del candidato no coincide con el recalculado"
    return None


def validar_bloque(bloque, anterior, libro: Libro, genesis: dict, exigir_votos: bool = True,
                   timestamp_max: int | None = None) -> str | None:
    """Valida `bloque` como sucesor de `anterior`; si es válido lo aplica a `libro`.

    Devuelve None si es válido o el mensaje del primer fallo. Si es inválido,
    `libro` puede quedar a medias (usar un clon). `timestamp_max` (opcional) es la
    hora del nodo que valida: un bloque posterior se rechaza. Nunca lanza.
    """
    try:
        return _revisar_bloque(bloque, anterior, libro, genesis, exigir_votos, timestamp_max)
    except Exception:
        return f"Bloque {_numero_para_mensaje(bloque, anterior)}: estructura inválida"


# ---------------------------------------------------------------- cadenas

def _es_mismo_genesis(recibido, genesis: dict) -> bool:
    """El génesis recibido debe ser idéntico al de la red: mismos campos, tipos y hash.

    No basta `recibido == genesis`: en Python 0 == 0.0 == False, así que un génesis
    con "numero": 0.0 parecería igual aunque su hash recalculado ya no coincida.
    """
    return (validar_estructura_bloque(recibido, es_genesis=True) is None
            and recibido == genesis
            and hash_bloque(recibido) == genesis["hash"])


def _revisar_cadena(cadena, genesis: dict, timestamp_max: int | None) -> tuple[bool, str, Libro | None]:
    if type(cadena) is not list or not cadena:
        return False, "la cadena debe ser una lista no vacía de bloques", None
    if len(cadena) > MAX_BLOQUES_CADENA:
        return False, f"la cadena es demasiado larga (máximo {MAX_BLOQUES_CADENA} bloques)", None
    if not _es_mismo_genesis(cadena[0], genesis):
        return False, "génesis distinto: la cadena no empieza con el bloque génesis de esta red", None
    libro = Libro(genesis)
    for i in range(1, len(cadena)):
        error = validar_bloque(cadena[i], cadena[i - 1], libro, genesis, timestamp_max=timestamp_max)
        if error:
            return False, error, None
    return True, f"cadena válida ({_plural(len(cadena), 'bloque', 'bloques')})", libro


def validar_cadena(cadena, genesis: dict, timestamp_max: int | None = None) -> tuple[bool, str, Libro | None]:
    """Valida una cadena completa desde el génesis. Devuelve (ok, mensaje, libro). Nunca lanza.

    Con `timestamp_max` también rechaza bloques fechados después de esa hora.
    """
    try:
        return _revisar_cadena(cadena, genesis, timestamp_max)
    except Exception:
        return False, "la cadena tiene una estructura inválida", None


def evaluar_cadena_recibida(recibida, propia: list, genesis: dict,
                            timestamp_max: int | None = None) -> tuple[bool, str, Libro | None]:
    """Regla 2.4: primero la validez y después el largo. Devuelve (acepta, motivo, libro)."""
    try:
        valida, motivo, libro = validar_cadena(recibida, genesis, timestamp_max)
        if not valida:
            return False, motivo, None
        if len(recibida) <= len(propia):
            return (False, f"no es más larga que la propia ({len(recibida) - 1} ≤ {len(propia) - 1})",
                    None)
        return True, "aceptada", libro
    except Exception:
        return False, "la cadena tiene una estructura inválida", None


def validar_candidato(cadena: list, libro: Libro, candidato, genesis: dict) -> tuple[bool, str]:
    """Lo que revisa un validador PoS antes de votar: el candidato aún sin votos."""
    try:
        error = validar_bloque(candidato, cadena[-1], libro.clonar(), genesis, exigir_votos=False)
    except Exception:
        error = "el candidato tiene una estructura inválida"
    return (error is None), (error or "candidato válido")


# ---------------------------------------------------------------- transacciones nuevas

def _leer_nodo_tx(tx: dict, campo: str, nombre: str, directorio: dict) -> str:
    """Emisor o receptor: debe ser exactamente un id del directorio (es lo que se firmó)."""
    valor = tx[campo]
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        raise EntradaInvalida(f"{nombre} es obligatorio")
    if not isinstance(valor, str):
        raise EntradaInvalida(f"{nombre} debe ser un identificador de nodo como N03 "
                              f"(recibido: {_corto(valor)})")
    if valor not in directorio:
        ids = sorted(directorio)
        validos = f"{ids[0]}–{ids[-1]}" if ids else "no hay nodos"
        raise NoEncontrado(f"{nombre}: el nodo '{_corto(valor)}' no existe (nodos válidos: {validos})",
                           codigo="nodo_inexistente")
    return valor


def _describir_no_maduras(libro: Libro, id: str) -> str:
    """'(80 en recompensas aún no maduras; la primera madura en 3 bloques)' o ''."""
    pendientes = libro.pendientes_de(id)
    if not pendientes:
        return ""
    total = sum(p["monto"] for p in pendientes)
    faltan = max(1, min(p["faltan"] for p in pendientes))
    return (f" ({total} en recompensas aún no maduras; la primera madura en "
            f"{_plural(faltan, 'bloque', 'bloques')})")


def _revisar_transaccion(tx, libro: Libro, directorio: dict, pendientes: list,
                         comprometido: dict, timestamp_max: int) -> None:
    # Estructura: un objeto con exactamente id, firma y los 4 campos firmados.
    if not isinstance(tx, dict):
        raise EntradaInvalida("La transacción debe ser un objeto con id, emisor, receptor, monto, "
                              "timestamp y firma")
    for campo in CAMPOS_TX_FIRMADA:
        if campo not in tx:
            raise EntradaInvalida(f"A la transacción le falta el campo '{campo}'")
    for clave in tx:
        if clave not in CAMPOS_TX_FIRMADA:
            raise EntradaInvalida(f"La transacción tiene un campo no permitido: {_corto(clave)}")
    if not es_hex(tx["id"], 64) or tx["id"] != id_transaccion(tx):
        raise EntradaInvalida("El id de la transacción no coincide con sus datos (¿fue alterada?): "
                              "debe ser el SHA-256 de emisor, receptor, monto y timestamp")

    emisor = _leer_nodo_tx(tx, "emisor", "El emisor", directorio)
    receptor = _leer_nodo_tx(tx, "receptor", "El receptor", directorio)
    if emisor == receptor:
        raise EntradaInvalida(f"El emisor y el receptor deben ser distintos (ambos son {emisor})")

    monto = leer_monto(tx["monto"])
    if type(tx["monto"]) is not int:   # se firmó otro valor (texto o decimal): no se acepta en un bloque
        raise EntradaInvalida("El monto de una transacción firmada debe ser un número entero, "
                              "sin comillas ni decimales")
    timestamp = leer_entero(tx["timestamp"], "El timestamp", 0, MAX_TIMESTAMP)
    if type(tx["timestamp"]) is not int:
        raise EntradaInvalida("El timestamp de una transacción firmada debe ser un número entero, "
                              "sin comillas ni decimales")
    if timestamp > timestamp_max:
        raise EntradaInvalida(f"El timestamp está en el futuro ({timestamp} > {timestamp_max})")

    if not verificar(directorio[emisor], serializar(tx), tx["firma"]):
        raise EntradaInvalida(f"Firma inválida: la transacción fue alterada o no la firmó el emisor ({emisor})",
                              codigo="firma_invalida")

    if tx["id"] in libro.ids_tx:
        raise Conflicto(f"Transacción repetida: ya está registrada en el bloque {libro.ids_tx[tx['id']]} "
                        f"(doble gasto)", codigo="doble_gasto")
    if any(isinstance(p, dict) and p.get("id") == tx["id"] for p in pendientes):
        raise Conflicto("Transacción repetida: ya está en la lista de pendientes (doble gasto)",
                        codigo="doble_gasto")

    saldo = libro.saldo(emisor)
    ya_comprometido = comprometido.get(emisor, 0)
    disponible = max(0, saldo - ya_comprometido)
    if monto > disponible:
        mensaje = f"Saldo insuficiente: {emisor} tiene {disponible} disponibles e intenta enviar {monto}"
        if ya_comprometido:
            mensaje += (f" (saldo en cadena {saldo}, de los que {ya_comprometido} ya están comprometidos "
                        f"en transacciones pendientes, apuestas o castigos)")
        mensaje += _describir_no_maduras(libro, emisor)
        raise EntradaInvalida(mensaje, codigo="saldo_insuficiente")


def validar_transaccion_nueva(tx, libro: Libro, directorio: dict, pendientes: list,
                              comprometido: dict[str, int], timestamp_max: int) -> None:
    """Revisa una transacción firmada antes de agregarla a pendientes.

    Lanza EntradaInvalida, NoEncontrado ("nodo_inexistente") o Conflicto
    ("doble_gasto") con un mensaje claro; no devuelve nada si es válida.
    """
    try:
        _revisar_transaccion(tx, libro, directorio, pendientes, comprometido, timestamp_max)
    except ErrorSimulacion:
        raise
    except Exception:
        raise EntradaInvalida("La transacción tiene una estructura inválida") from None
