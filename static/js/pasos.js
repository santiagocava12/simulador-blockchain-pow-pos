/* ==========================================================================
   pasos.js — Zona C: las cinco pestañas numeradas (role="tablist").

   Debajo de cada nombre va un resumen vivo (docs/UX.md §3.3) y una marca ✓
   cuando ese paso ya se hizo. Todas se pueden abrir en cualquier orden.
   Teclado: ← → cambian de pestaña; Inicio / Fin van a la primera / última.
   El paso abierto se recuerda en la sesión (al recargar se ve el mismo).
   ========================================================================== */

import { NOMBRE_ESTADO_POS, nodosDe, powActivo, posActiva } from './estado.js';
import { registrarNavegacion } from './navegacion.js';
import { contarPruebas } from './paso5_pruebas.js';
import { $, guardar, h, icono, leerGuardado, lista, num, objeto, plural, siCambia, texto } from './util.js';

let pasoActual = 1;
const alMostrar = {};   // paso -> funciones que corren cuando se abre ese paso

/** Registra una función que corre cada vez que se abre el paso `n`. */
export function alMostrarPaso(n, fn) {
  (alMostrar[n] = alMostrar[n] || []).push(fn);
}

/** Paso visible ahora (1 a 5). */
export const pasoVisible = () => pasoActual;

/**
 * En pantallas estrechas la tira de pestañas se desplaza en horizontal: si la
 * pestaña elegida no se ve entera, la tira se corre hasta su inicio (que es
 * donde se ancla el scroll-snap), sin mover la página en vertical.
 */
function verPestana(pestana) {
  const tira = $('#pasos');
  if (tira.scrollWidth <= tira.clientWidth) return;
  const caja = pestana.getBoundingClientRect();
  const marco = tira.getBoundingClientRect();
  if (caja.left < marco.left || caja.right > marco.right) tira.scrollLeft += caja.left - marco.left;
}

/** Abre el paso n. opciones.foco = true mueve el foco a su pestaña. */
function seleccionar(n, opciones = {}) {
  if (!(n >= 1 && n <= 5)) return;
  pasoActual = n;
  for (let k = 1; k <= 5; k++) {
    const pestana = $(`#tab-${k}`);
    const elegida = k === n;
    pestana.setAttribute('aria-selected', String(elegida));
    pestana.tabIndex = elegida ? 0 : -1;
    $(`#panel-${k}`).hidden = !elegida;
  }
  guardar('sessionStorage', 'simulador-paso', String(n));
  verPestana($(`#tab-${n}`));
  if (opciones.foco) {
    const pestana = $(`#tab-${n}`);
    pestana.focus({ preventScroll: true });
    const caja = pestana.getBoundingClientRect();
    if (caja.top < 0 || caja.bottom > window.innerHeight) pestana.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }
  for (const fn of alMostrar[n] || []) fn();
}

/** Resúmenes y marcas de las pestañas con la instantánea. */
export function actualizarPasos(S) {
  const altura = num(S.altura_red);
  const p = lista(S.pendientes).length;
  const pow = objeto(S.pow);
  const pos = objeto(S.pos);
  const enCurso = powActivo(S) || posActiva(S);
  const atrasados = nodosDe(S).filter((n) => !n.sincronizado).length;
  const tramposos = nodosDe(S).filter((n) => n.deshonesto).length;

  let resumen3;
  if (S.modo === 'pow') resumen3 = powActivo(S) ? `Minando… ronda ${num(pow.ronda)}` : `Bloque ${altura}`;
  else resumen3 = posActiva(S) ? NOMBRE_ESTADO_POS[pos.estado] : `Bloque ${altura}`;

  // [resumen, hecho, en curso, alerta]
  const info = {
    1: [`${S.modo === 'pow' ? 'PoW' : 'PoS'} · ${nodosDe(S).length} nodos`, true, false, false],
    2: [p ? `${p} en espera` : 'Sin pendientes', altura > 0 || p > 0, false, false],
    3: [resumen3, altura > 0, enCurso, false],
    4: [atrasados ? `${atrasados} ${plural(atrasados, 'atrasado', 'atrasados')}` : 'Todos ✓', false, false, atrasados > 0],
    5: [tramposos ? `${tramposos} ${plural(tramposos, 'tramposo', 'tramposos')}` : `${contarPruebas(S.modo)} pruebas`, false, false, tramposos > 0],
  };
  texto($('#t-3'), S.modo === 'pow' ? 'Minar' : 'Votar');
  for (let i = 1; i <= 5; i++) {
    const [resumen, hecho, curso, alerta] = info[i];
    const pestana = $(`#tab-${i}`);
    const elegida = pasoActual === i;
    pestana.classList.toggle('hecho', hecho && !elegida);
    pestana.classList.toggle('curso', curso && !elegida);
    const s = $(`#s-${i}`);
    texto(s, curso && i === 3 ? `● ${resumen}` : resumen);
    s.classList.toggle('warn', alerta);
    siCambia($(`#num-${i}`), `${hecho && !elegida}`, () => (hecho && !elegida
      ? [icono('check', 'ic-s'), h('span', { class: 'sr-only' }, `${i} (hecho)`)]
      : [String(i)]));
  }
}

/** Conecta clics y teclado de las pestañas y abre el paso inicial. */
export function iniciarPasos() {
  registrarNavegacion(seleccionar);
  const pestanas = $('#pasos');
  pestanas.addEventListener('click', (e) => {
    const pestana = e.target.closest('[role="tab"]');
    if (pestana) seleccionar(Number(pestana.dataset.paso));
  });
  pestanas.addEventListener('keydown', (e) => {
    const mov = { ArrowRight: 1, ArrowLeft: -1, Home: 'inicio', End: 'fin' }[e.key];
    if (mov === undefined) return;
    e.preventDefault();
    const n = mov === 'inicio' ? 1 : mov === 'fin' ? 5 : ((pasoActual - 1 + mov + 5) % 5) + 1;
    seleccionar(n, { foco: true });
  });
  // Enlace directo: «/?paso=3» abre ese paso (útil para el video y las capturas);
  // si no viene, se usa el último paso abierto en esta pestaña.
  const pedido = Number(new URLSearchParams(location.search).get('paso'));
  const guardado = Number(leerGuardado('sessionStorage', 'simulador-paso'));
  const inicial = [pedido, guardado].find((n) => Number.isInteger(n) && n >= 1 && n <= 5);
  seleccionar(inicial || 1);
}
