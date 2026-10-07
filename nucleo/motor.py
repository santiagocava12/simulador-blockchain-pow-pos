"""Motor: un hilo en segundo plano que avanza solo la minería y las rondas PoS automáticas.

El navegador no mina: sólo pide "minar" y luego sondea /api/estado. Este hilo
es quien llama a ``Simulador.paso()`` una y otra vez, con una pausa entre
pasos (``intervalo_s``) para que la carrera de mineros o la ronda PoS se vean
en pantalla. Pide el simulador en cada vuelta, así que sigue funcionando
aunque el usuario reinicie la simulación.

El hilo nunca muere por una excepción: la atrapa, la anota en la bitácora si
puede y sigue. Es "daemon": se cierra solo cuando termina el programa.
"""

import threading
from typing import Callable

from nucleo.errores import ErrorSimulacion

ESPERA_REPOSO_S = 0.05      # sin trabajo: revisa de nuevo pronto (la acción del usuario se ve enseguida)
ESPERA_TRAS_ERROR_S = 0.5   # tras una falla: una pausa para no repetirla en ráfaga
ESPERA_MINIMA_S = 0.01


class Motor:
    """Hilo único (por motor) que da los pasos automáticos del simulador actual."""

    def __init__(self, obtener_simulador: Callable[[], object]):
        self._obtener_simulador = obtener_simulador
        self._candado = threading.Lock()
        self._hilo: threading.Thread | None = None
        self._parar = threading.Event()

    @property
    def activo(self) -> bool:
        """True si el hilo está corriendo."""
        with self._candado:
            return self._hilo is not None and self._hilo.is_alive()

    def asegurar_iniciado(self) -> None:
        """Arranca el hilo si no está corriendo. Llamarla muchas veces deja un solo hilo."""
        with self._candado:
            if self._hilo is not None and self._hilo.is_alive():
                return
            self._parar = threading.Event()   # señal propia de este hilo
            self._hilo = threading.Thread(target=self._bucle, args=(self._parar,), name="motor-simulador",
                                          daemon=True)
            self._hilo.start()

    def detener(self, espera_s: float = 2.0) -> None:
        """Pide al hilo que termine y lo espera un momento."""
        with self._candado:
            hilo, parar = self._hilo, self._parar
            self._hilo = None
            parar.set()
        if hilo is not None and hilo is not threading.current_thread():
            hilo.join(timeout=espera_s)

    # ------------------------------------------------------------ hilo

    def _bucle(self, parar: threading.Event) -> None:
        """Da pasos mientras haya trabajo; si no, espera un poco y vuelve a revisar."""
        while not parar.is_set():
            try:
                espera = self._un_paso()
            except Exception:   # ni siquiera algo rarísimo detiene el motor
                espera = ESPERA_TRAS_ERROR_S
            parar.wait(espera)

    def _un_paso(self) -> float:
        """Un paso si el simulador lo necesita. Devuelve cuántos segundos esperar antes del siguiente."""
        simulador = None
        try:
            simulador = self._obtener_simulador()
            if not simulador.requiere_pasos():
                return ESPERA_REPOSO_S
            simulador.paso()
            return max(ESPERA_MINIMA_S, float(simulador.intervalo_s()))
        except ErrorSimulacion:
            # El simulador ya la anotó (y si fue interna, detuvo lo automático).
            return ESPERA_TRAS_ERROR_S
        except Exception as error:
            self._anotar_error(simulador, error)
            return ESPERA_TRAS_ERROR_S

    @staticmethod
    def _anotar_error(simulador, error: Exception) -> None:
        """Deja constancia de la falla en la bitácora del simulador, si se puede."""
        try:
            if simulador is not None:
                simulador.registrar_error(f"El motor automático tuvo un error ({type(error).__name__}) "
                                          f"y sigue funcionando")
        except Exception:
            pass
