"""Reloj de la simulación en milisegundos.

Por omisión es simulado: empieza el 2026-01-01T00:00:00Z y avanza 1000 ms cada
vez que se pide la hora para una transacción o un bloque. Así la misma semilla
y las mismas acciones producen los mismos hashes. El modo "real" usa la hora
del sistema, pero nunca repite ni retrocede.
"""

import time

INICIO_SIMULADO_MS = 1767225600000   # 2026-01-01T00:00:00Z


class Reloj:
    """Entrega marcas de tiempo enteras (ms) estrictamente crecientes."""

    def __init__(self, modo: str = "simulado", inicio_ms: int = INICIO_SIMULADO_MS, paso_ms: int = 1000):
        if modo not in ("simulado", "real"):
            raise ValueError(f"modo de reloj desconocido: {modo!r}")
        if type(paso_ms) is not int or paso_ms < 1:
            raise ValueError("paso_ms debe ser un entero positivo")
        self.modo = modo
        self.paso_ms = paso_ms
        self._ultimo = inicio_ms if modo == "simulado" else self._ms_reales()

    @staticmethod
    def _ms_reales() -> int:
        return time.time_ns() // 1_000_000

    def ahora(self) -> int:
        """Nueva marca de tiempo, siempre mayor que la anterior (avanza el reloj)."""
        if self.modo == "simulado":
            self._ultimo += self.paso_ms
        else:
            self._ultimo = max(self._ms_reales(), self._ultimo + 1)
        return self._ultimo

    def actual(self) -> int:
        """Último valor emitido (o el inicio), sin avanzar el reloj."""
        return self._ultimo
