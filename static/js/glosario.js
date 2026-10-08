/* ==========================================================================
   glosario.js — Botones «?» junto a los términos y cajón «Glosario»
   (textos exactos de docs/UX.md §10).
   Un botón con data-g="clave" abre una burbuja con la definición.
   ========================================================================== */

import { $, $$, h, icono } from './util.js';

export const GLOSARIO = {
  nodo: ['Nodo', 'Una computadora de la red. Guarda su propia copia de la cadena y revisa todo lo que recibe. Aquí se llaman N01, N02…'],
  cadena: ['Cadena de bloques', 'Lista de bloques en orden. Cada bloque guarda el hash del anterior, así que cambiar uno rompe todos los que siguen.'],
  bloque: ['Bloque', 'Paquete de transacciones con su número, hora, el hash del bloque anterior y su propio hash.'],
  genesis: ['Génesis', 'El bloque 0. Trae los saldos iniciales y las claves públicas de todos los nodos.'],
  hash: ['Hash', 'Huella digital de 64 caracteres calculada con el contenido del bloque. Si cambia una sola coma, el hash cambia por completo.'],
  nonce: ['Nonce', 'Número que el minero cambia en cada intento para obtener un hash distinto.'],
  dificultad: ['Dificultad', 'Cuántos ceros debe tener el hash al inicio. Con 3, debe empezar con «000». Cada cero más pide unas 16 veces más intentos.'],
  minero: ['Minero', 'Nodo que busca el nonce. El primero que encuentra un hash válido agrega el bloque.'],
  recompensa: ['Recompensa', 'Monedas nuevas que gana quien agrega el bloque.'],
  confirmaciones: ['Confirmaciones', 'Bloques que se agregaron después de uno. En PoW, la recompensa se puede gastar cuando su bloque tiene 6 confirmaciones; antes está pendiente.'],
  pendiente: ['Recompensa pendiente', 'Recompensa ganada que todavía no tiene 6 confirmaciones: se ve, pero no se puede gastar.'],
  firma: ['Firma digital', 'Sello que sólo puede producir el dueño de la clave secreta. Prueba que el emisor autorizó la transacción y que nadie la cambió.'],
  espera: ['Transacción en espera', 'Firmada y revisada por la red, pero todavía no está dentro de un bloque.'],
  disponible: ['Saldo disponible', 'Lo que un nodo puede gastar ahora: su saldo en la cadena menos lo que ya envió y está en espera, su apuesta y castigos por registrar.'],
  doble: ['Doble gasto', 'Intentar gastar las mismas monedas dos veces. La red lo detecta y rechaza la segunda.'],
  validador: ['Validador', 'Nodo que apuesta monedas para participar en PoS: puede proponer el bloque y vota.'],
  apuesta: ['Apuesta', 'Monedas que el validador deja bloqueadas durante la ronda. Si hace trampa, las pierde.'],
  sorteo: ['Sorteo ponderado', 'Rifa en la que cada validador tiene tantos boletos como monedas apostó. Es pública: cualquiera puede repetirla.'],
  proponente: ['Proponente', 'Validador que ganó el sorteo y arma el bloque.'],
  umbral: ['Umbral de 2/3', 'El bloque se acepta si los votos a favor, pesados por la apuesta, suman al menos dos tercios del total apostado.'],
  castigo: ['Castigo', 'El proponente tramposo pierde su apuesta (regla A) o una parte (regla B). Esas monedas se queman.'],
  quemado: ['Quemado', 'Monedas destruidas por castigos. Ya no existen para nadie.'],
  sincronizado: ['Sincronizado', 'El nodo tiene el mismo último bloque que el resto de la red.'],
  difundir: ['Difundir', 'Enviar mi cadena a los demás nodos. Cada uno la revisa antes de aceptarla y sólo adopta una válida y más larga.'],
  tramposo: ['Nodo tramposo', 'Nodo que propone bloques con trampas o vota al revés. Su propia copia sigue siendo válida; los demás lo rechazan.'],
  circulacion: ['Dinero en circulación', 'Saldos más recompensas pendientes. Debe ser igual a los saldos iniciales más las recompensas, menos lo quemado: si cuadra, nadie creó ni perdió dinero por error.'],
};

/** Botón «?» para insertar desde JavaScript junto a un término. */
export function botonAyuda(clave, etiqueta) {
  const termino = (GLOSARIO[clave] || [clave])[0];
  return h('button', { type: 'button', class: 'ayuda-btn', 'data-g': clave, 'aria-label': etiqueta || `¿Qué es ${termino.toLowerCase()}?` }, '?');
}

let botonAbierto = null;   // el «?» cuya burbuja está abierta
let volverA = null;        // a dónde regresa el foco al cerrar el cajón

/* ---------------------------------------------------------------- burbuja */

function abrirBurbuja(boton) {
  const clave = boton.dataset.g;
  const [titulo, definicion] = GLOSARIO[clave] || [clave, ''];
  const pop = $('#pop');
  if (botonAbierto === boton && !pop.hidden) {
    cerrarBurbuja(true);
    return;
  }
  pop.replaceChildren(
    h('h4', { id: 'pop-t' }, titulo),
    h('p', {}, definicion),
    h('div', { class: 'acciones' },
      h('button', { type: 'button', class: 'btn chico fantasma', 'data-glos': clave }, icono('book', 'ic-s'), 'Ver glosario'),
      h('button', { type: 'button', class: 'btn chico', 'data-cerrar-pop': '' }, 'Cerrar')));
  pop.hidden = false;
  // Se coloca bajo el botón, sin salirse de la pantalla.
  const caja = boton.getBoundingClientRect();
  const ancho = pop.offsetWidth;
  const anchoVentana = document.documentElement.clientWidth;
  const izquierda = Math.max(12, Math.min(caja.left + caja.width / 2 - ancho / 2, anchoVentana - ancho - 12));
  pop.style.left = `${izquierda + window.scrollX}px`;
  pop.style.top = `${caja.bottom + window.scrollY + 8}px`;
  if (botonAbierto) botonAbierto.setAttribute('aria-expanded', 'false');
  botonAbierto = boton;
  boton.setAttribute('aria-expanded', 'true');
  pop.focus();
}

function cerrarBurbuja(devolverFoco) {
  const pop = $('#pop');
  if (pop.hidden) return;
  pop.hidden = true;
  if (botonAbierto) {
    botonAbierto.setAttribute('aria-expanded', 'false');
    if (devolverFoco && document.contains(botonAbierto)) botonAbierto.focus();
  }
  botonAbierto = null;
}

/* ---------------------------------------------------------------- cajón */

function abrirCajon(clave) {
  volverA = botonAbierto || document.activeElement;
  cerrarBurbuja(false);
  $('#fondo').hidden = false;
  $('#glosario').hidden = false;
  for (const dt of $$('#glos-lista dt')) dt.classList.toggle('marcado', dt.id === `g-${clave}`);
  const marcado = clave ? document.getElementById(`g-${clave}`) : null;
  if (marcado) marcado.scrollIntoView({ block: 'center' });
  $('#glos-cerrar').focus();
}

function cerrarCajon() {
  if ($('#glosario').hidden) return;
  $('#glosario').hidden = true;
  $('#fondo').hidden = true;
  if (volverA && document.contains(volverA)) volverA.focus();
}

/** Arma el cajón y conecta los clics (una vez, desde main.js). */
export function iniciarGlosario() {
  const terminos = Object.entries(GLOSARIO).sort((a, b) => a[1][0].localeCompare(b[1][0], 'es'));
  $('#glos-lista').replaceChildren(...terminos.flatMap(([clave, [titulo, definicion]]) => [
    h('dt', { id: `g-${clave}` }, titulo),
    h('dd', {}, definicion),
  ]));

  document.addEventListener('click', (e) => {
    const objetivo = e.target.closest('[data-g],[data-glos],[data-cerrar-pop]');
    if (!e.target.closest('#pop') && !(objetivo && objetivo.dataset.g)) cerrarBurbuja(false);
    if (!objetivo) return;
    if (objetivo.dataset.g) abrirBurbuja(objetivo);
    else if (objetivo.dataset.glos) abrirCajon(objetivo.dataset.glos);
    else cerrarBurbuja(true);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (!$('#glosario').hidden) cerrarCajon();
      else cerrarBurbuja(true);
    }
    // El cajón es modal: el foco no sale de él (sólo hay dos elementos con foco).
    if (e.key === 'Tab' && !$('#glosario').hidden) {
      const enfocables = [$('#glos-cerrar'), $('#glosario .cajon-cuerpo')];
      const i = enfocables.indexOf(document.activeElement);
      e.preventDefault();
      const siguiente = e.shiftKey ? (i <= 0 ? enfocables.length - 1 : i - 1) : (i + 1) % enfocables.length;
      enfocables[siguiente].focus();
    }
  });
  $('#btn-glosario').addEventListener('click', () => abrirCajon(null));
  $('#glos-cerrar').addEventListener('click', cerrarCajon);
  $('#fondo').addEventListener('click', cerrarCajon);
}
