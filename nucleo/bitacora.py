"""Bitácora de eventos visibles en la interfaz (bloque minado, castigo, recompensa...)."""

import time
from collections import deque

TIPOS = ("simulacion", "transaccion", "transaccion_rechazada", "mineria", "bloque_minado", "bloque_rechazado",
         "cadena_aceptada", "cadena_rechazada", "empate", "recompensa_pendiente", "recompensa_acreditada",
         "pos_estado", "sorteo", "voto", "voto_rechazado", "castigo", "sin_validadores", "ataque", "nodo",
         "entrada_rechazada", "error")


class Bitacora:
    """Guarda los últimos `maximo` eventos, numerados en orden desde 1."""

    def __init__(self, maximo: int = 1000):
        if type(maximo) is not int or maximo < 1:
            raise ValueError("maximo debe ser un entero positivo")
        self._eventos: deque[dict] = deque(maxlen=maximo)
        self._ultimo = 0

    def registrar(self, tipo: str, mensaje: str, **datos) -> dict:
        """Agrega un evento y lo devuelve. `datos` debe tener sólo tipos simples (JSON)."""
        if tipo not in TIPOS:
            raise ValueError(f"tipo de evento desconocido: {tipo!r}")
        self._ultimo += 1
        evento = {
            "n": self._ultimo,
            "tiempo": time.time_ns() // 1_000_000,   # hora real, sólo para mostrar
            "tipo": tipo,
            "mensaje": mensaje,
            "datos": datos,
        }
        self._eventos.append(evento)
        return evento

    def desde(self, n: int, limite: int = 300) -> list[dict]:
        """Eventos con número mayor que `n` (como mucho los últimos `limite`)."""
        if type(n) is not int:
            n = 0
        if type(limite) is not int or limite <= 0:
            return []
        nuevos = [dict(e) for e in self._eventos if e["n"] > n]
        return nuevos[-limite:]

    @property
    def ultimo(self) -> int:
        """Número del último evento registrado (0 si no hay)."""
        return self._ultimo
