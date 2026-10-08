/* ==========================================================================
   bitacora.js — Zona D: todo lo que pasa en la red (docs/UX.md §8).

   Cada evento del servidor ({n, tiempo, tipo, mensaje, datos}) se muestra con
   un icono, una etiqueta en texto (no sólo color), «#n · hh:mm:ss» y la frase
   completa del servidor. Lo más reciente va arriba. Los nuevos entran con un
   resaltado breve. Se filtran por grupo con los chips de arriba.
   ========================================================================== */

import { NOMBRE_ESTADO_POS, estado } from './estado.js';
import { $, atributo, fmt, h, icono, sincronizarLista, texto } from './util.js';

/* Tipo de evento → [grupo, icono, etiqueta]. */
const TIPOS = {
  simulacion: ['red', 'refresh', 'Red creada'],
  transaccion: ['tx', 'swap', 'Transacción'],
  transaccion_rechazada: ['rech', 'x', 'Transacción rechazada'],
  mineria: ['bloque', 'pick', 'Minería'],
  bloque_minado: ['bloque', 'block', 'Bloque agregado'],
  bloque_rechazado: ['rech', 'x', 'Bloque rechazado'],
  cadena_aceptada: ['bloque', 'link', 'Cadena aceptada'],
  cadena_rechazada: ['rech', 'x', 'Cadena rechazada'],
  empate: ['bloque', 'trophy', 'Empate'],
  recompensa_pendiente: ['rec', 'clock', 'Recompensa pendiente'],
  recompensa_acreditada: ['rec', 'coins', 'Recompensa acreditada'],
  pos_estado: ['pos', 'vote', 'Ronda PoS'],
  sorteo: ['pos', 'dice', 'Sorteo'],
  voto: ['pos', 'vote', 'Voto'],
  voto_rechazado: ['rech', 'x', 'Voto rechazado'],
  castigo: ['rech', 'scale', 'Castigo'],
  sin_validadores: ['rech', 'warn', 'Sin validadores'],
  ataque: ['ataque', 'warn', 'Ataque / prueba'],
  nodo: ['red', 'server', 'Nodo'],
  entrada_rechazada: ['rech', 'x', 'Entrada rechazada'],
  error: ['rech', 'warn', 'Error'],
};

/* Chips de filtro: [clave, texto]. «Rechazos y ataques» junta los grupos rech y ataque. */
const FILTROS = [
  ['todo', 'Todo'], ['bloque', 'Bloques'], ['tx', 'Transacciones'], ['rec', 'Recompensas'],
  ['pos', 'Votación'], ['rech', 'Rechazos y ataques'], ['red', 'Red'],
];

const MAX_FILAS = 500;

let filtro = 'todo';
/* Eventos de la última prueba del paso 5 (se resaltan «· de la prueba»): los
   que tienen desde < n ≤ hasta. Mientras la prueba corre, hasta = null. */
let marca = null;

const deLaPrueba = (n) => marca !== null && n > marca.desde && (marca.hasta === null || n <= marca.hasta);

/** En PoS, un bloque aceptado llega como «Ronda PoS … → ACEPTADO»: también es un bloque agregado. */
const esBloquePos = (evento) => evento.tipo === 'pos_estado' && evento.datos && evento.datos.estado === 'ACEPTADO'
  && / → ACEPTADO/.test(String(evento.mensaje));

/** [grupo, icono, etiqueta] de un evento. */
function tipoDe(evento) {
  if (esBloquePos(evento)) return ['bloque', 'block', 'Bloque agregado'];
  return TIPOS[evento.tipo] || ['red', 'info', String(evento.tipo || 'Evento')];
}

/** Grupos de filtro en los que aparece el evento (un bloque PoS aceptado: Bloques y Votación). */
function gruposFiltro(evento) {
  if (esBloquePos(evento)) return ['bloque', 'pos'];
  const grupo = tipoDe(evento)[0];
  return [grupo === 'ataque' ? 'rech' : grupo];
}
const pasaFiltro = (evento) => filtro === 'todo' || gruposFiltro(evento).includes(filtro);

/* Los mensajes de la ronda PoS traen el estado en código («VOTACION → ACEPTADO»):
   en pantalla se ve con su nombre («Votación → Aceptado»), como en el resto de la interfaz. */
const CODIGOS_POS = new RegExp(String.raw`\b(${Object.keys(NOMBRE_ESTADO_POS).join('|')})\b`, 'g');
const TIPOS_CON_ESTADO = new Set(['pos_estado', 'sorteo', 'sin_validadores']);

function textoEvento(evento) {
  const mensaje = typeof evento.mensaje === 'string' ? evento.mensaje : '';
  return TIPOS_CON_ESTADO.has(evento.tipo) ? mensaje.replace(CODIGOS_POS, (c) => NOMBRE_ESTADO_POS[c]) : mensaje;
}

/** Fila de un evento, construida con createElement (el mensaje va como texto). */
function filaEvento(evento, nuevo) {
  const [grupo, nombreIcono, etiqueta] = tipoDe(evento);
  const hora = Number.isFinite(evento.tiempo)
    ? new Date(evento.tiempo).toLocaleTimeString('es-MX', { hour12: false }) : '';
  const fila = h('li', { class: `evt g-${grupo}${nuevo ? ' nuevo' : ''}`, 'data-n': evento.n },
    h('span', { class: 'eic' }, icono(nombreIcono)),
    h('div', { class: 'cont' },
      h('div', { class: 'ecab' },
        h('span', { class: 'etipo' }, etiqueta),
        h('span', { class: 'emeta' }, `#${evento.n}${hora ? ` · ${hora}` : ''}`)),
      h('p', {}, textoEvento(evento))));
  if (deLaPrueba(evento.n)) marcar(fila, true);
  if (nuevo) setTimeout(() => fila.classList.remove('nuevo'), 1900);
  return fila;
}

/** Pone (o quita) el resaltado «· de la prueba» de una fila. */
function marcar(fila, si) {
  if (fila.classList.contains('marcado') === si) return;
  fila.classList.toggle('marcado', si);
  if (si) {
    const cab = fila.querySelector('.ecab .etipo');
    if (cab) cab.after(h('span', { class: 'marca-prueba' }, ' · de la prueba'));
  } else {
    const etiqueta = fila.querySelector('.marca-prueba');
    if (etiqueta) etiqueta.remove();
  }
}

function repintarMarcas() {
  for (const fila of $('#eventos').children) marcar(fila, deLaPrueba(Number(fila.dataset.n)));
}

/** Vuelve a dibujar toda la lista (al cambiar de filtro o de simulación). */
function redibujar() {
  const contenedor = $('#eventos');
  const visibles = estado.eventos.filter(pasaFiltro).slice(-MAX_FILAS).reverse();
  contenedor.replaceChildren(...visibles.map((e) => filaEvento(e, false)));
  $('#eventos-vacio').hidden = visibles.length > 0;
}

/** Actualiza los números de los chips y el total. */
function actualizarConteos(S) {
  const cuenta = { todo: estado.eventos.length };
  for (const e of estado.eventos) for (const g of gruposFiltro(e)) cuenta[g] = (cuenta[g] || 0) + 1;
  sincronizarLista($('#bit-filtros'), FILTROS, ([clave]) => clave,
    ([clave, etiqueta]) => h('button', { type: 'button', class: 'fchip', 'data-filtro': clave },
      etiqueta, ' ', h('span', { class: 'n' })),
    (boton, [clave]) => {
      atributo(boton, 'aria-pressed', String(filtro === clave));
      texto(boton.querySelector('.n'), fmt(cuenta[clave] || 0));
    });
  const total = S && Number.isFinite(S.ultimo_evento) ? S.ultimo_evento : estado.eventos.length;
  texto($('#bit-n'), `${fmt(total)} ${total === 1 ? 'evento' : 'eventos'}`);
}

/** Se llama con cada instantánea: agrega arriba sólo los eventos nuevos. */
export function actualizarBitacora(S, nuevos, { reinicio }) {
  if (reinicio) {
    marca = null;
    redibujar();
  } else if (nuevos.length) {
    const contenedor = $('#eventos');
    const visibles = nuevos.filter(pasaFiltro);
    for (const e of visibles) contenedor.prepend(filaEvento(e, true));
    while (contenedor.children.length > MAX_FILAS) contenedor.lastElementChild.remove();
    if (visibles.length) $('#eventos-vacio').hidden = true;
  }
  actualizarConteos(S);
}

/**
 * Empieza a resaltar los eventos de una prueba del paso 5: los que tengan un
 * número mayor que `n` (el último evento al empezar la prueba), hasta que se
 * llame a terminarMarca().
 */
export function marcarEventosDesde(n) {
  marca = { desde: n, hasta: null };
  repintarMarcas();
}

/**
 * La prueba terminó: se siguen resaltando sólo sus eventos (hasta el último
 * recibido). Lo que pase después (acciones a mano, otra ronda…) ya no se marca.
 */
export function terminarMarca() {
  if (marca === null || marca.hasta !== null) return;
  marca.hasta = estado.ultimoEvento;
  repintarMarcas();
}

/** Conecta los chips de filtro (una vez). */
export function iniciarBitacora() {
  $('#bit-filtros').addEventListener('click', (e) => {
    const boton = e.target.closest('[data-filtro]');
    if (!boton) return;
    filtro = boton.dataset.filtro;
    redibujar();
    actualizarConteos(estado.S);
  });
}
