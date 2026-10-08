/* ==========================================================================
   navegacion.js — Ir a un paso (pestaña) desde cualquier módulo.

   pasos.js registra aquí la función real. Así un botón de otro módulo (por
   ejemplo «Ver en el paso 3» de una prueba) puede cambiar de pestaña sin que
   los módulos se importen entre sí en círculo.
   ========================================================================== */

let manejador = null;

/** pasos.js llama a esto una vez al iniciar. */
export function registrarNavegacion(fn) {
  manejador = fn;
}

/** Abre el paso `n` (1 a 5). opciones.foco = true pone el foco en la pestaña. */
export function irAPaso(n, opciones = {}) {
  if (manejador) manejador(n, opciones);
}

/** Lleva la vista (y el foco) a la bitácora. */
export function irABitacora() {
  const bitacora = document.getElementById('bitacora');
  if (!bitacora) return;
  bitacora.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  bitacora.focus({ preventScroll: true });
}
