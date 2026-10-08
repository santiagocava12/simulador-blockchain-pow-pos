/* ==========================================================================
   util.js — Ayudas pequeñas que usan todos los módulos.

   Regla de seguridad de toda la interfaz: lo que viene del servidor NUNCA se
   mete con innerHTML. Los elementos se crean con document.createElement (la
   función h) y el texto se pone con textContent; así un mensaje con "<b>" se
   ve tal cual y no se interpreta como HTML.
   ========================================================================== */

/** Busca un elemento (o varios) con un selector CSS. */
export const $ = (selector, raiz = document) => raiz.querySelector(selector);
export const $$ = (selector, raiz = document) => Array.from(raiz.querySelectorAll(selector));

/* ---------------------------------------------------------------- números y textos */

const FORMATO = new Intl.NumberFormat('es-MX');

/** 1250 → "1,250" (miles con coma, como en México). Si no es número: "—". */
export function fmt(n) {
  return Number.isFinite(n) ? FORMATO.format(n) : '—';
}

/** Elige singular o plural: plural(3, 'bloque', 'bloques') → 'bloques'. */
export function plural(n, uno, varios) {
  return n === 1 ? uno : varios;
}

/** 0.253 → "25.3 %". */
export function pct(x) {
  return Number.isFinite(x) ? `${(Math.round(x * 1000) / 10).toLocaleString('es-MX')} %` : '—';
}

/** Lista en español: ['N01','N02','N03'] → "N01, N02 y N03". */
export function listaY(textos) {
  const t = textos.filter(Boolean);
  if (t.length <= 1) return t.join('');
  return `${t.slice(0, -1).join(', ')} y ${t[t.length - 1]}`;
}

/** Pausa: await esperar(500). */
export const esperar = (ms) => new Promise((resolver) => setTimeout(resolver, ms));

/** Votos mínimos para el umbral de 2/3: u = ⌈2A/3⌉. */
export const umbral = (A) => Math.ceil((2 * A) / 3);

/* La API puede omitir campos: estas funciones devuelven un valor seguro. */
export const num = (v, defecto = 0) => (Number.isFinite(v) ? v : defecto);
export const lista = (v) => (Array.isArray(v) ? v : []);
export const objeto = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});

/** "000" para una dificultad 3. */
export const ceros = (d) => '0'.repeat(Math.max(0, num(d)));

/* ---------------------------------------------------------------- crear elementos */

/**
 * Crea un elemento HTML de forma segura.
 *   h('button', { class: 'btn', type: 'button', onclick: fn }, icono('play'), 'Probar')
 * Los textos se agregan como nodos de texto (nunca como HTML).
 */
export function h(etiqueta, props = {}, ...hijos) {
  const el = document.createElement(etiqueta);
  for (const [clave, valor] of Object.entries(props || {})) {
    if (valor === null || valor === undefined || valor === false) continue;
    if (clave === 'class') el.className = valor;
    else if (clave === 'text') el.textContent = valor;
    else if (clave === 'dataset') Object.assign(el.dataset, valor);
    else if (clave.startsWith('on') && typeof valor === 'function') el.addEventListener(clave.slice(2), valor);
    else el.setAttribute(clave, valor === true ? '' : String(valor));
  }
  agregarHijos(el, hijos);
  return el;
}

/** Agrega hijos: nodos tal cual, textos como texto; ignora null/false; aplana listas. */
export function agregarHijos(el, hijos) {
  for (const hijo of hijos.flat(Infinity)) {
    if (hijo === null || hijo === undefined || hijo === false) continue;
    el.append(hijo instanceof Node ? hijo : String(hijo));
  }
  return el;
}

const SVG = 'http://www.w3.org/2000/svg';

/** Icono del sprite de index.html: icono('check', 'ic-s'). Decorativo (aria-hidden). */
export function icono(nombre, clase = '') {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('class', `ic ${clase}`.trim());
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  const uso = document.createElementNS(SVG, 'use');
  uso.setAttribute('href', `#i-${nombre}`);
  svg.append(uso);
  return svg;
}

/** Cuántos ceros tiene un hash al principio ("000a3f…" → 3). */
export function cerosIniciales(hash) {
  if (typeof hash !== 'string') return 0;
  let z = 0;
  while (z < hash.length && hash[z] === '0') z++;
  return z;
}

/** Hash recortado con los ceros iniciales en verde; el valor completo va en title. */
export function hashCorto(hash, largo = 12) {
  if (typeof hash !== 'string' || !hash) return h('span', { class: 'hash' }, '—');
  const z = Math.min(cerosIniciales(hash), largo);
  const resto = hash.slice(z, largo) + (hash.length > largo ? '…' : '');
  return h('span', { class: 'hash', title: hash }, h('span', { class: 'z' }, hash.slice(0, z)), resto);
}

/** Hash completo (64 caracteres) con los ceros iniciales resaltados. */
export function hashLargo(hash) {
  if (typeof hash !== 'string' || !hash) return h('span', { class: 'hash' }, '—');
  const z = cerosIniciales(hash);
  return h('span', { class: 'hash' }, h('span', { class: 'z' }, hash.slice(0, z)), hash.slice(z));
}

/** Los ceros de una meta: «000» en verde, en letra monoespaciada. */
export function cerosMeta(dificultad) {
  return h('span', { class: 'hash' }, h('span', { class: 'z' }, ceros(dificultad)));
}

/* ---------------------------------------------------------------- actualizar en su lugar
   Cada sondeo trae una instantánea nueva. Para no reconstruir la página (y no
   perder el foco, lo escrito en los campos ni los paneles abiertos), sólo se
   cambia lo que de verdad cambió. */

/** Pone el texto sólo si es distinto (no toca el DOM si es igual). */
export function texto(el, valor) {
  const t = valor === null || valor === undefined ? '' : String(valor);
  if (el && el.textContent !== t) el.textContent = t;
}

/** Pone o quita un atributo sólo si cambia. valor null/false = quitarlo. */
export function atributo(el, nombre, valor) {
  if (!el) return;
  if (valor === null || valor === undefined || valor === false) {
    if (el.hasAttribute(nombre)) el.removeAttribute(nombre);
    return;
  }
  const v = valor === true ? '' : String(valor);
  if (el.getAttribute(nombre) !== v) el.setAttribute(nombre, v);
}

/** Pone la lista de clases sólo si es distinta (className = "a b c"). */
export function clases(el, valor) {
  if (el && el.className !== valor) el.className = valor;
}

/** Pone una propiedad (disabled, checked…) sólo si cambia. */
export function propiedad(el, nombre, valor) {
  if (el && el[nombre] !== valor) el[nombre] = valor;
}

/** Muestra u oculta un elemento (atributo hidden) sólo si cambia. */
export function mostrar(el, visible) {
  if (el && el.hidden === !!visible) el.hidden = !visible;
}

/**
 * Reconstruye el contenido de `el` SÓLO si cambió su "firma" (un texto que
 * resume los datos que se dibujan, normalmente JSON.stringify de ellos).
 * Si la firma es igual, no se toca nada.
 */
export function siCambia(el, firma, construir) {
  if (!el || el._firma === firma) return false;
  el._firma = firma;
  el.replaceChildren(...[construir()].flat(Infinity).filter((n) => n !== null && n !== undefined && n !== false));
  return true;
}

/**
 * Lista "con llave": cada elemento de `items` tiene su fila (por ejemplo, un
 * nodo por id). Las filas existentes se ACTUALIZAN (actualizar), las nuevas se
 * CREAN (crear) y las que sobran se quitan. Sólo se mueven las que cambiaron
 * de lugar, así un botón con el foco no lo pierde.
 */
export function sincronizarLista(contenedor, items, clave, crear, actualizar) {
  const previos = new Map();
  for (const hijo of Array.from(contenedor.children)) {
    if (hijo._clave !== undefined) previos.set(hijo._clave, hijo);
    else hijo.remove();
  }
  let anterior = null;
  for (const item of items) {
    const k = clave(item);
    let el = previos.get(k);
    if (el) previos.delete(k);
    else {
      el = crear(item);
      el._clave = k;
    }
    actualizar(el, item);
    const enSuLugar = anterior ? anterior.nextSibling : contenedor.firstChild;
    if (el !== enSuLugar) contenedor.insertBefore(el, enSuLugar);
    anterior = el;
  }
  for (const sobra of previos.values()) sobra.remove();
}

/* ---------------------------------------------------------------- almacenamiento local
   Sólo para comodidades (tema, paso abierto). Si el navegador lo bloquea, no pasa nada. */

export function leerGuardado(almacen, clave) {
  try {
    return window[almacen].getItem(clave);
  } catch {
    return null;
  }
}

export function guardar(almacen, clave, valor) {
  try {
    if (valor === null) window[almacen].removeItem(clave);
    else window[almacen].setItem(clave, valor);
  } catch {
    /* sin almacenamiento: se ignora */
  }
}
