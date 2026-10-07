"""Parámetros de la simulación y su validación estricta."""

from dataclasses import asdict, dataclass, fields

from nucleo.entradas import leer_entero, leer_opcion, leer_texto
from nucleo.errores import EntradaInvalida, ErrorSimulacion

CONFIRMACIONES_POW = 6


@dataclass(frozen=True)
class Config:
    """Configuración inmutable de una simulación (valores por omisión de la guía)."""

    modo: str = "pow"                  # "pow" | "pos"
    num_nodos: int = 10                # 10..20
    semilla: str = "anahuac"           # texto 1..64
    saldo_inicial: int = 100           # 1..1_000_000
    recompensa: int = 50               # 1..1_000_000
    max_tx_por_bloque: int = 10        # 1..50
    # PoW
    dificultad: int = 3                # 1..6  (sugerido 3..5)
    intentos_por_ronda: int = 50       # k: 1..5000
    max_rondas: int = 3000             # 1..1_000_000 (límite para no minar eternamente)
    intervalo_ms: int = 250            # 50..2000 (ritmo del motor automático)
    # PoS
    seleccion_validadores: str = "todos"   # "todos" | "aleatorio"
    num_validadores: int = 5               # 1..20 y <= num_nodos (se usa con "aleatorio")
    regla_castigo: str = "A"               # "A" | "B"
    alfa_porcentaje: int = 50              # 1..100  (α = alfa_porcentaje / 100)
    reloj: str = "simulado"                # "simulado" | "real"

    def a_dict(self) -> dict:
        """Copia en dict (para JSON y para la interfaz)."""
        return asdict(self)


# Campo -> (nombre para los mensajes, mínimo, máximo)
_ENTEROS = {
    "num_nodos": ("El número de nodos", 10, 20),
    "saldo_inicial": ("El saldo inicial", 1, 1_000_000),
    "recompensa": ("La recompensa", 1, 1_000_000),
    "max_tx_por_bloque": ("El máximo de transacciones por bloque", 1, 50),
    "dificultad": ("La dificultad", 1, 6),
    "intentos_por_ronda": ("El número de intentos por ronda", 1, 5000),
    "max_rondas": ("El máximo de rondas", 1, 1_000_000),
    "intervalo_ms": ("El intervalo en milisegundos", 50, 2000),
    "num_validadores": ("El número de validadores", 1, 20),
    "alfa_porcentaje": ("El porcentaje alfa", 1, 100),
}

# Campo -> (nombre para los mensajes, opciones válidas)
_OPCIONES = {
    "modo": ("El modo", ("pow", "pos")),
    "seleccion_validadores": ("La selección de validadores", ("todos", "aleatorio")),
    "regla_castigo": ("La regla de castigo", ("A", "B")),
    "reloj": ("El reloj", ("simulado", "real")),
}

# Campo -> (nombre para los mensajes, largo máximo)
_TEXTOS = {
    "semilla": ("La semilla", 64),
}


def _leer_campo(campo: str, valor):
    """Lee un campo con el lector que le toca; el error indica qué campo falló."""
    try:
        if campo in _ENTEROS:
            nombre, minimo, maximo = _ENTEROS[campo]
            return leer_entero(valor, nombre, minimo, maximo)
        if campo in _OPCIONES:
            nombre, opciones = _OPCIONES[campo]
            return leer_opcion(valor, nombre, opciones)
        nombre, max_largo = _TEXTOS[campo]
        return leer_texto(valor, nombre, max_largo)
    except ErrorSimulacion as error:
        error.detalles["campo"] = campo
        raise


def validar_config(datos=None) -> Config:
    """Convierte datos no confiables en un Config válido o lanza EntradaInvalida.

    None o {} dan los valores por omisión; las claves desconocidas se ignoran.
    """
    if datos is None:
        datos = {}
    elif isinstance(datos, Config):  # se revalida por si se construyó a mano
        datos = datos.a_dict()
    if not isinstance(datos, dict):
        raise EntradaInvalida("La configuración debe ser un objeto JSON con los parámetros de la simulación")

    valores = {}
    for campo in fields(Config):
        if campo.name in datos:
            valores[campo.name] = _leer_campo(campo.name, datos[campo.name])
    config = Config(**valores)

    if config.num_validadores > config.num_nodos:
        raise EntradaInvalida(
            f"El número de validadores ({config.num_validadores}) no puede ser mayor que "
            f"el número de nodos ({config.num_nodos})",
            detalles={"campo": "num_validadores"},
        )
    return config
