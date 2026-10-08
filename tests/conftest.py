"""Utilidades compartidas por las pruebas de aceptación.

Las pruebas se escribieron ANTES del código, a partir del contrato de
diseño y de la sección 5 de la guía. Por eso aquí se
reimplementan, de forma independiente y siguiendo la guía al pie de la letra,
el hash de bloque, la serialización y firma de transacciones y el sorteo PoS:
así las pruebas comprueban el formato del contrato y no sólo que el código
coincida consigo mismo.

Las fábricas y ayudas se importan desde las pruebas con
`from conftest import ...` (pytest agrega `tests/` al sys.path).
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import unicodedata
from typing import Any, Callable

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from nucleo.errores import Conflicto, EntradaInvalida, ErrorSimulacion, NoEncontrado
from nucleo.simulador import Simulador

# ---------------------------------------------------------------------------
# Configuraciones base (rápidas y reproducibles)
# ---------------------------------------------------------------------------

SEMILLA = "pruebas-anahuac"
N_NODOS = 10
SALDO_INICIAL = 100
RECOMPENSA = 50
INTENTOS_POR_RONDA = 5
CONFIRMACIONES_POW = 6

CONFIG_POW: dict[str, Any] = {
    "modo": "pow",
    "num_nodos": N_NODOS,
    "semilla": SEMILLA,
    "saldo_inicial": SALDO_INICIAL,
    "recompensa": RECOMPENSA,
    "max_tx_por_bloque": 10,
    "dificultad": 1,
    "intentos_por_ronda": INTENTOS_POR_RONDA,
    "max_rondas": 500,
    "intervalo_ms": 50,
}

CONFIG_POS: dict[str, Any] = {
    "modo": "pos",
    "num_nodos": N_NODOS,
    "semilla": SEMILLA,
    "saldo_inicial": SALDO_INICIAL,
    "recompensa": RECOMPENSA,
    "max_tx_por_bloque": 10,
    "seleccion_validadores": "todos",
    "regla_castigo": "A",
    "alfa_porcentaje": 50,
    "intervalo_ms": 50,
}

ESTADOS_FINALES_POS = ("ACEPTADO", "SIN_VALIDADORES", "CANCELADA")
ERRORES_ESPERADOS = (EntradaInvalida, NoEncontrado, Conflicto)

CAMPOS_TX = ("emisor", "receptor", "monto", "timestamp")
CAMPOS_BLOQUE = (
    "numero", "timestamp", "transacciones", "firma", "hash_anterior", "nonce",
    "proponente", "recompensa", "votos", "validadores", "intento", "castigos", "modo", "hash",
)
HASH_CERO = "0" * 64


# ---------------------------------------------------------------------------
# Fábricas de simuladores
# ---------------------------------------------------------------------------

def sim_pow(**cambios: Any) -> Simulador:
    """Simulador PoW de pruebas: N=10, dificultad 1, k=5, semilla fija."""
    return Simulador({**CONFIG_POW, **cambios})


def sim_pos(**cambios: Any) -> Simulador:
    """Simulador PoS de pruebas: N=10, validadores "todos", regla A, semilla fija."""
    return Simulador({**CONFIG_POS, **cambios})


# ---------------------------------------------------------------------------
# Avance de la simulación
# ---------------------------------------------------------------------------

def correr_mineria(sim: Simulador, max_pasos: int = 5000) -> list[dict]:
    """Llama sim.paso() mientras sim.requiere_pasos(); devuelve los resultados.

    Falla si se llega al tope sin que la sesión termine. También sirve para una
    ronda PoS en modo automático (requiere_pasos sólo es True con automatico).
    """
    resultados: list[dict] = []
    while sim.requiere_pasos():
        assert len(resultados) < max_pasos, f"La simulación no terminó en {max_pasos} pasos"
        resultados.append(sim.paso())
    return resultados


def estado_pow(sim: Simulador) -> str:
    """Estado de la sesión PoW ("inactivo" si no hay sesión)."""
    return sim.estado()["pow"]["estado"]


def estado_pos(sim: Simulador) -> str:
    """Estado de la ronda PoS ("INACTIVA" si no hay ronda)."""
    return sim.estado()["pos"]["estado"]


def completar_ronda_pos(sim: Simulador, max_pasos: int = 100) -> str:
    """Llama avanzar_pos hasta un estado final; devuelve ese estado."""
    for _ in range(max_pasos):
        actual = estado_pos(sim)
        if actual in ESTADOS_FINALES_POS:
            return actual
        sim.avanzar_pos()
    raise AssertionError(f"La ronda PoS no llegó a un estado final en {max_pasos} pasos")


def avanzar_hasta(sim: Simulador, objetivo: str, max_pasos: int = 50) -> None:
    """Avanza la ronda PoS (manualmente) hasta el estado `objetivo`."""
    for _ in range(max_pasos):
        actual = estado_pos(sim)
        if actual == objetivo:
            return
        assert actual not in ESTADOS_FINALES_POS, (
            f"La ronda terminó en {actual} antes de llegar a {objetivo}"
        )
        sim.avanzar_pos()
    raise AssertionError(f"No se llegó al estado {objetivo} en {max_pasos} pasos")


def dejar_pasar_tiempo(sim: Simulador, ms: int = 60_000) -> None:
    """Avanza el reloj simulado `ms` milisegundos (como si pasara el tiempo).

    Los nodos rechazan bloques fechados después de su hora
    (timestamp_max = reloj.actual()). Las pruebas que fabrican bloques fuera del
    simulador con timestamps posteriores al último bloque lo usan antes de
    enviarlos.
    """
    objetivo = sim.reloj.actual() + ms
    while sim.reloj.actual() < objetivo:
        sim.reloj.ahora()


def minar_bloques(sim: Simulador, cantidad: int, monto: int = 1) -> None:
    """Mina `cantidad` bloques PoW uno por uno, cada uno con una transacción nueva.

    Sólo usa N01..N08 como emisores, para que N09 y N10 queden libres en las
    pruebas que fabrican bloques a mano.
    """
    ids = ids_de(sim)
    for i in range(cantidad):
        altura = sim.estado()["altura_red"]
        sim.crear_transaccion(ids[i % 8], ids[(i + 1) % 8], monto)
        sim.iniciar_mineria()
        correr_mineria(sim)
        assert sim.estado()["altura_red"] == altura + 1, "El bloque no se agregó a la red"


# ---------------------------------------------------------------------------
# Comprobaciones
# ---------------------------------------------------------------------------

def assert_invariantes(sim: Simulador) -> None:
    """La cadena, los saldos y las pendientes siguen íntegros."""
    problemas = sim.verificar_invariantes()
    assert problemas == [], f"Invariantes rotas: {problemas}"


def esperar_error(
    funcion: Callable[..., Any],
    *args: Any,
    tipos: type | tuple[type, ...] = ERRORES_ESPERADOS,
    **kwargs: Any,
) -> ErrorSimulacion:
    """Ejecuta `funcion` y exige un error esperado; lo devuelve.

    Un ErrorInterno (excepción no prevista convertida) o cualquier otra
    excepción NO cuenta como error esperado y hace fallar la prueba.
    """
    with pytest.raises(tipos) as info:
        funcion(*args, **kwargs)
    error = info.value
    assert isinstance(error.mensaje, str) and error.mensaje.strip(), "El error no trae mensaje"
    return error


def normalizar(texto: Any) -> str:
    """Texto en minúsculas y sin acentos, para buscar palabras clave."""
    descompuesto = unicodedata.normalize("NFD", str(texto))
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn").lower()


def ids_de(sim: Simulador) -> list[str]:
    """Ids de los nodos en orden (N01, N02, ...)."""
    return [n["id"] for n in sim.estado()["nodos"]]


def nodo_de(estado: dict, id_nodo: str) -> dict:
    """Entrada de un nodo dentro de una instantánea estado()."""
    for nodo in estado["nodos"]:
        if nodo["id"] == id_nodo:
            return nodo
    raise AssertionError(f"El nodo {id_nodo} no aparece en el estado")


def nodo(sim: Simulador, id_nodo: str) -> dict:
    """Entrada actual de un nodo en sim.estado()."""
    return nodo_de(sim.estado(), id_nodo)


def eventos(sim: Simulador, tipo: str | None = None, desde: int = 0) -> list[dict]:
    """Eventos de la bitácora posteriores a `desde`, opcionalmente de un tipo."""
    todos = sim.bitacora.desde(desde, limite=100_000)
    return [e for e in todos if tipo is None or e["tipo"] == tipo]


def texto_evento(evento: dict) -> str:
    """Mensaje y datos de un evento, normalizados, para buscar palabras clave."""
    datos = json.dumps(evento.get("datos", {}), ensure_ascii=False, default=str)
    return normalizar(f"{evento.get('mensaje', '')} {datos}")


def saldos_por_cadena(cadena: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    """Repite la cadena de forma independiente: (saldos disponibles, recompensas no maduras).

    Las recompensas maduran cuando bloque + confirmaciones <= altura.
    """
    genesis = cadena[0]
    saldos = dict(genesis["saldos_iniciales"])
    confirmaciones = genesis["parametros"]["confirmaciones"]
    altura = len(cadena) - 1
    no_maduras: dict[str, int] = {}
    for bloque in cadena[1:]:
        for castigo in bloque["castigos"]:
            saldos[castigo["nodo"]] -= castigo["monto"]
        for tx in bloque["transacciones"]:
            saldos[tx["emisor"]] -= tx["monto"]
            saldos[tx["receptor"]] += tx["monto"]
        beneficiario = bloque["recompensa"]["beneficiario"]
        monto = bloque["recompensa"]["monto"]
        if bloque["numero"] + confirmaciones <= altura:
            saldos[beneficiario] += monto
        else:
            no_maduras[beneficiario] = no_maduras.get(beneficiario, 0) + monto
    return saldos, no_maduras


def assert_disponible_coherente(estado: dict) -> None:
    """disponible = saldo en cadena − pendiente − apuesta − castigo pendiente (mínimo 0)."""
    for n in estado["nodos"]:
        esperado = max(
            0,
            n["saldo_cadena"] - n["pendiente_salida"] - n["apuesta_bloqueada"] - n["castigo_pendiente"],
        )
        assert n["disponible"] == esperado, f"Disponible incoherente en {n['id']}: {n}"
        assert n["disponible"] >= 0 and n["saldo_cadena"] >= 0


# ---------------------------------------------------------------------------
# Criptografía y bloques, reimplementados según la guía (independientes del núcleo)
# ---------------------------------------------------------------------------

def clave_privada(semilla: str, id_nodo: str) -> Ed25519PrivateKey:
    """Clave Ed25519 derivada como dice el contrato: sha256(f"{semilla}|{id}|clave")."""
    secreto = hashlib.sha256(f"{semilla}|{id_nodo}|clave".encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(secreto)


def clave_publica_hex(semilla: str, id_nodo: str) -> str:
    """Clave pública cruda (64 caracteres hex) del nodo."""
    publica = clave_privada(semilla, id_nodo).public_key()
    return publica.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def serializar_guia(tx: dict) -> bytes:
    """Serialización EXACTA de la guía (sólo los 4 campos firmados)."""
    datos = {k: tx[k] for k in CAMPOS_TX}
    return json.dumps(datos, sort_keys=True, separators=(",", ":")).encode()


def firmar_tx(
    semilla: str,
    emisor: str,
    receptor: str,
    monto: Any,
    timestamp: int,
    firmante: str | None = None,
) -> dict:
    """Transacción firmada con la forma de la API: {id, emisor, receptor, monto, timestamp, firma}."""
    carga = {"emisor": emisor, "receptor": receptor, "monto": monto, "timestamp": timestamp}
    datos = serializar_guia(carga)
    firma = clave_privada(semilla, firmante or emisor).sign(datos).hex()
    return {"id": hashlib.sha256(datos).hexdigest(), **carga, "firma": firma}


def firma_valida(clave_publica: str, datos: bytes, firma_hex: str) -> bool:
    """Verifica una firma Ed25519 sin lanzar excepciones."""
    try:
        publica = Ed25519PublicKey.from_public_bytes(bytes.fromhex(clave_publica))
        publica.verify(bytes.fromhex(firma_hex), datos)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def alterar_hex(texto: str) -> str:
    """Cambia el primer carácter hex por otro distinto (como la trampa del contrato)."""
    return ("1" if texto[0] == "0" else "0") + texto[1:]


def hash_guia(bloque: dict) -> str:
    """Hash EXACTO de la guía: sha256 del JSON ordenado sin el campo "hash"."""
    datos = {k: v for k, v in bloque.items() if k != "hash"}
    return hashlib.sha256(json.dumps(datos, sort_keys=True).encode()).hexdigest()


def sortear_guia(validadores: list[dict], hash_anterior: str, numero: int, intento: int) -> str:
    """Sorteo PoS EXACTO de la guía (validadores ya ordenados por id)."""
    semilla = f"{hash_anterior}|{numero}|{intento}".encode()
    total = sum(v["apuesta"] for v in validadores)
    r = int(hashlib.sha256(semilla).hexdigest(), 16) % total
    acumulado = 0
    for v in validadores:
        acumulado += v["apuesta"]
        if r < acumulado:
            return v["id"]
    raise AssertionError("El sorteo no eligió a nadie")


def timestamp_siguiente(cadena: list[dict], extra: int = 1000) -> int:
    """Un timestamp posterior al último bloque de la cadena."""
    return cadena[-1]["timestamp"] + extra


def extender_cadena(
    cadena: list[dict],
    txs_firmadas: list[dict],
    proponente: str = "N05",
    recompensa: int | None = None,
) -> list[dict]:
    """Copia de `cadena` con un bloque PoW nuevo, armado y minado aquí, al final.

    Si las transacciones y la recompensa son válidas, el bloque es válido; las
    pruebas lo alteran para fabricar bloques tramposos con un hash correcto.
    """
    genesis = cadena[0]
    parametros = genesis["parametros"]
    anterior = cadena[-1]
    if recompensa is None:
        recompensa = parametros["recompensa"]
    bloque: dict[str, Any] = {
        "numero": anterior["numero"] + 1,
        "timestamp": anterior["timestamp"] + 10_000,
        "transacciones": [{k: tx[k] for k in CAMPOS_TX} for tx in txs_firmadas],
        "firma": [tx["firma"] for tx in txs_firmadas],
        "hash_anterior": anterior["hash"],
        "nonce": 0,
        "proponente": proponente,
        "recompensa": {"beneficiario": proponente, "monto": recompensa},
        "votos": [],
        "validadores": [],
        "intento": 0,
        "castigos": [],
        "modo": "pow",
    }
    prefijo = "0" * parametros["dificultad"]
    for nonce in range(2_000_000):
        bloque["nonce"] = nonce
        calculado = hash_guia(bloque)
        if calculado.startswith(prefijo):
            bloque["hash"] = calculado
            return copy.deepcopy(cadena) + [bloque]
    raise AssertionError("No se encontró un nonce válido al fabricar el bloque")


# ---------------------------------------------------------------------------
# Concurrencia
# ---------------------------------------------------------------------------

def en_paralelo(funciones: list[Callable[[], Any]], espera_s: float = 30.0) -> list[tuple[str, Any]]:
    """Ejecuta las funciones a la vez (con una barrera) y devuelve (clase, valor) de cada una.

    clase: "ok" (valor devuelto), "error" (ErrorSimulacion esperado) o
    "inesperado" (cualquier otra excepción, incluida ErrorInterno).
    """
    barrera = threading.Barrier(len(funciones))
    resultados: list[tuple[str, Any]] = [("inesperado", "el hilo no terminó")] * len(funciones)

    def correr(indice: int, funcion: Callable[[], Any]) -> None:
        try:
            barrera.wait(timeout=espera_s)
            resultados[indice] = ("ok", funcion())
        except ERRORES_ESPERADOS as error:
            resultados[indice] = ("error", error)
        except Exception as error:  # noqa: BLE001 - se reporta en la prueba
            resultados[indice] = ("inesperado", error)

    hilos = [threading.Thread(target=correr, args=(i, f), daemon=True) for i, f in enumerate(funciones)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=espera_s)
    return resultados


# ---------------------------------------------------------------------------
# Cliente HTTP (Flask) sin motor
# ---------------------------------------------------------------------------

def crear_cliente(**cambios: Any):
    """Cliente de pruebas de Flask con un simulador PoW rápido (sin hilo motor)."""
    from app import create_app  # import perezoso: las pruebas del núcleo no dependen de Flask

    aplicacion = create_app(config_inicial={**CONFIG_POW, **cambios}, motor_activo=False)
    return aplicacion.test_client()


@pytest.fixture
def cliente():
    """Cliente HTTP con simulación PoW."""
    return crear_cliente()


@pytest.fixture
def cliente_pos():
    """Cliente HTTP con simulación PoS."""
    return crear_cliente(**CONFIG_POS)
