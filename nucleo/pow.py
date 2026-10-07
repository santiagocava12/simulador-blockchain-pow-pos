"""Proof of Work: minería por rondas ("ticks").

En cada ronda TODOS los mineros conectados prueban hasta k nonces sobre su
propio bloque candidato (proponente y recompensa = él mismo). Los nonces no se
repiten entre mineros: el minero i prueba i, i+N, i+2N, ... Cada minero se
detiene en su primer hash válido, pero la ronda no se corta por el hallazgo de
otro: así dos mineros pueden ganar en la misma ronda (empate) y gana el hash
menor.

Este módulo sólo lleva el proceso. Las claves, la red, la validación del bloque
ganador y la bitácora las coordina ``simulador.py``.
"""

import copy
import hashlib
import json
from dataclasses import asdict, dataclass

from nucleo.bloque import hash_bloque
from nucleo.errores import Conflicto
from nucleo.reglas import cumple_dificultad

ESTADOS = ("minando", "ganador", "cancelada", "agotada", "terminada")
ACTIVOS = ("minando", "ganador")                  # el motor debe seguir dando pasos
FINALES = ("cancelada", "agotada", "terminada")   # se conservan sólo para mostrarlas
MAX_HISTORIAL = 50                                # rechazos recordados por sesión


@dataclass
class Minero:
    """Estado de un minero dentro de la sesión (uno por nodo)."""

    id: str
    indice: int
    nonce: int                 # siguiente nonce a probar (o el hallado, si encontro)
    intentos: int = 0
    ultimo_hash: str = ""
    encontro: bool = False
    conectado: bool = True     # se actualiza en cada ronda (sólo para mostrar)


class SesionPow:
    """Minería de un bloque (altura `numero`) por rondas de k intentos por minero."""

    def __init__(self, numero: int, candidatos: dict[str, dict], ids: list[str], k: int,
                 dificultad: int, max_rondas: int, bloques_restantes: int = 1, auto_tx: bool = False,
                 plantilla: dict | None = None, txs: list[dict] | None = None):
        ids = list(ids)
        if not ids or len(set(ids)) != len(ids) or not all(isinstance(i, str) for i in ids):
            raise ValueError("los mineros deben ser una lista no vacía de ids distintos")
        for nombre, valor, minimo in (("numero", numero, 1), ("k", k, 1), ("dificultad", dificultad, 1),
                                      ("max_rondas", max_rondas, 1),
                                      ("bloques_restantes", bloques_restantes, 1)):
            if type(valor) is not int or valor < minimo:
                raise ValueError(f"{nombre} debe ser un entero mayor o igual a {minimo}")
        if dificultad > 64:
            raise ValueError("la dificultad no puede pasar de 64 ceros")
        if not isinstance(candidatos, dict):
            raise ValueError("candidatos debe ser un dict id -> bloque")

        self.numero = numero
        self.plantilla = copy.deepcopy(plantilla)        # bloque sin proponente/recompensa/nonce/hash
        self.txs = copy.deepcopy(txs) if txs else []     # transacciones honestas incluidas
        self.candidatos = copy.deepcopy(candidatos)      # id -> bloque propio de cada minero
        self.N = len(ids)
        # Minero i empieza en el nonce i y avanza de N en N: nadie repite el nonce de otro.
        self.mineros = [Minero(id=id_minero, indice=i, nonce=i) for i, id_minero in enumerate(ids)]
        self.k = k
        self.dificultad = dificultad
        self.ronda = 0
        self.max_rondas = max_rondas
        self.estado = "minando"
        self.bloques_restantes = bloques_restantes      # contando el bloque que se mina ahora
        self.auto_tx = bool(auto_tx)
        self.ultimo_empate: list[dict] | None = None
        self.ganador: dict | None = None                # hallazgo aceptado por la red
        self.historial: list[dict] = []                 # hallazgos rechazados (los últimos)
        self.mensaje = (f"Minando el bloque {numero}: {self.N} mineros, {k} nonces por ronda cada uno, "
                        f"dificultad {dificultad} (se esperan unos {16 ** dificultad} intentos)")

    @property
    def activa(self) -> bool:
        """True mientras el motor debe seguir dando pasos (minando o mostrando al ganador)."""
        return self.estado in ACTIVOS

    def minero(self, id_minero: str) -> Minero:
        """El minero con ese id (ValueError si no participa en la sesión)."""
        for m in self.mineros:
            if m.id == id_minero:
                return m
        raise ValueError(f"{id_minero!r} no es minero de esta sesión")

    def fijar_conectados(self, conectados) -> None:
        """Marca qué mineros están conectados (para mostrarlo en la interfaz)."""
        conectados = set(conectados)
        for m in self.mineros:
            m.conectado = m.id in conectados

    def intentos_totales(self) -> int:
        """Hashes calculados por todos los mineros en la sesión."""
        return sum(m.intentos for m in self.mineros)

    def a_dict(self) -> dict:
        """Resumen JSON de la sesión para la interfaz."""
        return {
            "estado": self.estado,
            "numero": self.numero,
            "ronda": self.ronda,
            "max_rondas": self.max_rondas,
            "k": self.k,
            "dificultad": self.dificultad,
            "esperado": 16 ** self.dificultad,
            "intentos_totales": self.intentos_totales(),
            "mineros": [asdict(m) for m in self.mineros],
            "ultimo_empate": copy.deepcopy(self.ultimo_empate),
            "bloques_restantes": self.bloques_restantes,
            "auto_tx": self.auto_tx,
            "mensaje": self.mensaje,
        }


# ---------------------------------------------------------------- hash rápido

def preparar_hash(bloque: dict):
    """Función nonce -> hash_bloque({**bloque, "nonce": nonce}), pero mucho más rápida.

    El JSON canónico del bloque (claves ordenadas) sólo cambia en el nonce. Se
    arma una vez el texto que va antes y después del nonce, y en cada intento
    sólo se agrega el número. El resultado es idéntico al de hash_bloque.
    """
    datos = {clave: valor for clave, valor in bloque.items() if clave not in ("hash", "nonce")}
    if not all(type(clave) is str for clave in datos):
        return lambda nonce: hash_bloque({**bloque, "nonce": nonce})   # caso raro: camino normal

    antes, despues = [], []
    for clave in sorted(datos):   # mismo orden que json.dumps(sort_keys=True)
        texto = json.dumps(clave) + ": " + json.dumps(datos[clave], sort_keys=True)
        (antes if clave < "nonce" else despues).append(texto)
    prefijo = ("{" + "".join(t + ", " for t in antes) + '"nonce": ').encode()
    sufijo = ("".join(", " + t for t in despues) + "}").encode()
    base = hashlib.sha256(prefijo)

    def calcular(nonce: int) -> str:
        h = base.copy()
        h.update(str(nonce).encode())
        h.update(sufijo)
        return h.hexdigest()

    return calcular


# ---------------------------------------------------------------- rondas

def ronda_pow(sesion: SesionPow, conectados) -> list[dict]:
    """Una ronda: cada minero conectado prueba hasta k nonces. Devuelve los hallazgos.

    Un minero se detiene en su primer hash válido; los demás terminan su turno
    (la ronda no se corta por el hallazgo de otro). Hallazgo:
    {"minero", "indice", "nonce", "hash"}.
    """
    if sesion.estado != "minando":
        raise Conflicto(f"No se está minando (estado de la sesión: {sesion.estado})", "sin_mineria")
    sesion.fijar_conectados(conectados)
    hallazgos = []
    for minero in sesion.mineros:
        candidato = sesion.candidatos.get(minero.id)
        # No mina: desconectado, sin bloque propio o esperando el resultado de su hallazgo.
        if not minero.conectado or candidato is None or minero.encontro:
            continue
        calcular = preparar_hash(candidato)
        for _ in range(sesion.k):
            h = calcular(minero.nonce)
            minero.intentos += 1
            minero.ultimo_hash = h
            if cumple_dificultad(h, sesion.dificultad):
                # El atajo es sólo una optimización: el hallazgo se confirma con hash_bloque.
                h = hash_bloque({**candidato, "nonce": minero.nonce})
                minero.ultimo_hash = h
                if cumple_dificultad(h, sesion.dificultad):
                    minero.encontro = True
                    hallazgos.append({"minero": minero.id, "indice": minero.indice,
                                      "nonce": minero.nonce, "hash": h})
                    break
            minero.nonce += sesion.N

    sesion.ronda += 1
    if len(hallazgos) > 1:
        sesion.ultimo_empate = ordenar_hallazgos(hallazgos)   # copias, del mejor al peor
    sesion.mensaje = _mensaje_ronda(sesion, hallazgos)
    return hallazgos


def _mensaje_ronda(sesion: SesionPow, hallazgos: list[dict]) -> str:
    """Texto breve de lo que pasó en la última ronda."""
    activos = sum(1 for m in sesion.mineros if m.conectado)
    inicio = f"Ronda {sesion.ronda} de {sesion.max_rondas}"
    if activos == 0:
        return f"{inicio}: ningún minero conectado, nadie pudo minar"
    if not hallazgos:
        return (f"{inicio}: {activos} mineros probaron hasta {sesion.k} nonces cada uno; "
                f"ningún hash empieza con {_ceros(sesion.dificultad)}")
    nombres = ", ".join(h["minero"] for h in ordenar_hallazgos(hallazgos))
    if len(hallazgos) == 1:
        return f"{inicio}: {nombres} encontró un hash válido"
    return f"{inicio}: {len(hallazgos)} mineros encontraron hash válido ({nombres}); gana el hash menor"


def _ceros(n: int) -> str:
    """'1 cero', '3 ceros'."""
    return f"{n} cero" if n == 1 else f"{n} ceros"


def ordenar_hallazgos(hallazgos: list[dict]) -> list[dict]:
    """Hallazgos del mejor al peor: hash menor primero; si el hash empata, menor índice."""
    return sorted((dict(h) for h in hallazgos), key=lambda h: (h["hash"], h["indice"]))


def elegir_ganador(hallazgos: list[dict]) -> tuple[dict, list[dict]]:
    """(ganador, empatados): gana el hash menor; empatados = todos si hubo más de uno, si no []."""
    if not hallazgos:
        raise ValueError("no hay hallazgos para elegir ganador")
    ordenados = ordenar_hallazgos(hallazgos)
    return ordenados[0], (ordenados if len(ordenados) > 1 else [])


def bloque_ganador(sesion: SesionPow, hallazgo: dict) -> dict:
    """Copia del candidato del minero con el nonce hallado y su hash final."""
    bloque = copy.deepcopy(sesion.candidatos[hallazgo["minero"]])
    bloque["nonce"] = hallazgo["nonce"]
    bloque["hash"] = hash_bloque(bloque)
    return bloque


def reanudar_tras_rechazo(sesion: SesionPow, minero_id: str, motivo: str | None = None) -> None:
    """La red rechazó el bloque de este minero: sigue minando desde nonce + N."""
    minero = sesion.minero(minero_id)
    if minero.encontro:   # si no había encontrado, su nonce actual aún no se ha probado
        sesion.historial.append({"ronda": sesion.ronda, "minero": minero.id, "nonce": minero.nonce,
                                 "hash": minero.ultimo_hash, "motivo": motivo or "bloque rechazado"})
        del sesion.historial[:-MAX_HISTORIAL]
        minero.nonce += sesion.N
        minero.encontro = False
    if sesion.estado not in FINALES:
        sesion.estado = "minando"
        sesion.mensaje = f"La red rechazó el bloque de {minero.id}; la minería continúa"


# ---------------------------------------------------------------- fin de la sesión

def registrar_ganador(sesion: SesionPow, hallazgo: dict) -> str:
    """La red aceptó el bloque: todos se detienen. Devuelve el nuevo estado.

    "ganador" si quedan bloques por minar (se muestra un paso y luego sigue el
    siguiente bloque) o "terminada" si era el último.
    """
    if sesion.estado != "minando":
        raise Conflicto(f"No se está minando (estado de la sesión: {sesion.estado})", "sin_mineria")
    sesion.ganador = dict(hallazgo)
    sesion.estado = "ganador" if sesion.bloques_restantes > 1 else "terminada"
    sesion.mensaje = (f"{hallazgo['minero']} ganó el bloque {sesion.numero} con el nonce {hallazgo['nonce']} "
                      f"(hash {hallazgo['hash'][:12]}…); todos los mineros se detienen")
    return sesion.estado


def revisar_limite(sesion: SesionPow) -> bool:
    """Si se llegó al máximo de rondas sin bloque, la sesión queda "agotada" (devuelve True)."""
    if sesion.estado == "minando" and sesion.ronda >= sesion.max_rondas:
        sesion.estado = "agotada"
        sesion.mensaje = (f"Se alcanzó el límite de {sesion.max_rondas} rondas sin encontrar un hash "
                          f"válido; baje la dificultad o aumente el límite")
        return True
    return False


def terminar_sesion(sesion: SesionPow, mensaje: str) -> None:
    """Termina la sesión sin más bloques (por ejemplo, ya no hay transacciones pendientes)."""
    if sesion.estado in FINALES:
        raise Conflicto(f"La minería ya terminó (estado: {sesion.estado})", "sin_mineria")
    sesion.estado = "terminada"
    sesion.mensaje = mensaje


def cancelar_sesion(sesion: SesionPow) -> None:
    """El usuario detiene la minería (sólo si está activa)."""
    if sesion.estado not in ACTIVOS:
        raise Conflicto(f"No hay minería en curso (estado: {sesion.estado})", "sin_mineria")
    sesion.estado = "cancelada"
    sesion.mensaje = f"Minería del bloque {sesion.numero} cancelada en la ronda {sesion.ronda}"
