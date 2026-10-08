/* ==========================================================================
   paso5_pruebas.js — Paso 5 «Pruebas y ataques»: la pantalla y el que
   ejecuta los escenarios (los escenarios están en escenarios.js).

   - Filtros: Todas · ★ Para el video · un chip por grupo.
   - Una tarjeta por escenario: qué hace, qué debería pasar, parámetros,
     «▶ Probar» y el resultado (✓ / △ y «Ver detalle técnico»).
   - Tarjeta «Nodos tramposos» y «Para expertos: enviar una petición a mano».
   ========================================================================== */

import { llamar, post, rutaNodo } from './api.js';
import { marcarEventosDesde, terminarMarca } from './bitacora.js';
import { ESCENARIOS, GRUPOS, aplica, contarEn } from './escenarios.js';
import {
  NOMBRE_ESTADO_POW, esperarCondicion, estado, nodosDe, nombreEstadoPos, refrescar,
} from './estado.js';
import { irAPaso } from './navegacion.js';
import { TEXTO_TRAMPA, trampasDelModo } from './trampas.js';
import { confirmarEnLinea, conBoton, mostrarRespuesta, ocupado, resultado } from './ui.js';
import {
  $, $$, esperar, guardar, h, icono, leerGuardado, mostrar, num, objeto, sincronizarLista, texto,
} from './util.js';

/** Número de pruebas del modo (para el resumen de la pestaña 5). */
export const contarPruebas = (modo) => contarEn(modo);

let modoDibujado = null;
let filtro = 'todas';

/*
 * Una prueba a la vez: cada escenario supone que controla la red él solo.
 * Mientras una corre, los botones «Probar» de las demás (y los de las filas
 * de las tablas) quedan desactivados; el de la que corre dice «Probando…».
 */
let pruebaEnCurso = null;

function bloquearPruebas(id) {
  pruebaEnCurso = id;
  for (const boton of $$('#lista-esc [data-probar], #lista-esc [data-fila]')) {
    if (boton.getAttribute('aria-busy') !== 'true') boton.disabled = id !== null;
  }
}

/** Corre fn() con el candado puesto; si ya hay otra prueba en curso, no hace nada. */
async function conCandado(id, fn) {
  if (pruebaEnCurso !== null) return;
  bloquearPruebas(id);
  try {
    await fn();
  } finally {
    bloquearPruebas(null);
  }
}

/* ---------------------------------------------------------------- ayudante de cada prueba */

/** Escribe el avance de una prueba («Paso 2 de 4: minando…»). */
function progreso(zona, mensaje) {
  zona.replaceChildren(h('p', { class: 'progreso' }, icono('clock'), mensaje));
  delete zona.dataset.idsim;   // si la prueba reinicia la red, el avance no se borra
}

/** Pinta el veredicto de una prueba con sus acciones y el detalle técnico. */
function pintarResultado(zona, res, llamadas = [], escenario = null, notas = []) {
  const acciones = [...(res.acciones || [])];
  if (escenario && escenario.ver && !res.precondicion && !acciones.some((a) => /^Ver /.test(a.texto))) {
    acciones.push({ texto: `Ver en el paso ${escenario.ver}`, icono: 'arrow', fn: () => irAPaso(escenario.ver, { foco: true }) });
  }
  resultado(zona, {
    tipo: res.tipo || 'info',
    titulo: res.titulo || '',
    texto: res.texto || null,
    extra: [...(res.extra || []), notas.length ? h('p', { class: 'nota', style: 'margin:4px 0 0' }, notas.join(' ')) : null],
    acciones,
    llamadas,
  });
}

/**
 * Lo que recibe cada escenario: hace las llamadas (y las anota para el
 * detalle técnico), muestra el avance y sabe esperar a la instantánea.
 */
class Prueba {
  constructor(zona, escenario) {
    this.zona = zona;
    this.escenario = escenario;
    this.llamadas = [];
    this.notas = [];
  }

  /** Última instantánea del servidor. */
  get S() {
    return estado.S;
  }

  avance(mensaje) {
    progreso(this.zona, mensaje);
  }

  nota(mensaje) {
    this.notas.push(mensaje);
  }

  async post(ruta, cuerpo = {}, opciones) {
    const r = await post(ruta, cuerpo, opciones);
    this.llamadas.push(r);
    if (ruta === '/api/simulacion' && r.ok) {
      await refrescar();            // la página ve la red nueva…
      marcarEventosDesde(0);        // …y resalta en la bitácora todo lo que pase en ella
    }
    return r;
  }

  async get(ruta) {
    const r = await llamar('GET', ruta);
    this.llamadas.push(r);
    return r;
  }

  /** Petición con un cuerpo de texto tal cual (JSON roto, 3 MB…). */
  async crudo(metodo, ruta, cuerpoTexto, comoMostrarlo) {
    const r = await llamar(metodo, ruta, cuerpoTexto, { crudo: true, mostrar: comoMostrarlo, tiempo: 30000 });
    this.llamadas.push(r);
    return r;
  }

  async refrescar() {
    await refrescar();
    return estado.S;
  }

  esperar(condicion, ms) {
    return esperarCondicion(condicion, ms);
  }

  pausa(ms) {
    return esperar(ms);
  }

  /** Vuelve a correr esta misma prueba (tras «Cancelarla y probar»). */
  reintentar() {
    correr(this.escenario.id, true);
  }

  /** Corre otra prueba (por ejemplo S1 después de S7). */
  correrOtra(id) {
    correrPorId(id);
  }

  pintar(zona, res) {
    pintarResultado(zona, res);
  }

  recordarParaRecarga(datos) {
    guardar('sessionStorage', 'simulador-prueba-a2', JSON.stringify(datos));
  }
}

/* ---------------------------------------------------------------- correr pruebas */

function leerParametros(tarjeta) {
  const valores = {};
  for (const sel of $$('[data-param]', tarjeta)) valores[sel.dataset.param] = sel.value;
  return valores;
}

/** Corre un escenario por su id. `sinConfirmar` = ya se confirmó (o no hace falta). */
async function correr(id, sinConfirmar = false) {
  const escenario = ESCENARIOS.find((e) => e.id === id);
  const tarjeta = document.getElementById(`esc-${id}`);
  if (!escenario || !tarjeta || !estado.S || pruebaEnCurso !== null) return;
  const boton = tarjeta.querySelector('[data-probar]');
  if (ocupado(boton)) return;
  const zona = tarjeta.querySelector('.esc-res');
  // Lo que falta antes de probar se dice ANTES de pedir confirmación (p. ej. S7 necesita la regla A).
  const previo = escenario.previo ? escenario.previo(estado.S) : null;
  if (previo) {
    pintarResultado(zona, previo, [], escenario);
    return;
  }
  if (escenario.confirma && !sinConfirmar) {
    const si = await confirmarEnLinea(tarjeta.querySelector('.esc-conf'), escenario.confirma, 'Sí, probar', boton);
    if (!si) return;
  }
  if (escenario.tabla) {
    await correrTabla(escenario, null, boton);
    return;
  }
  await conCandado(id, () => conBoton(boton, 'Probando…', async () => {
    const p = new Prueba(zona, escenario);
    p.avance('Probando… mira la barra «Ahora», la tira de nodos y la bitácora.');
    await refrescar();   // la prueba decide con el estado de AHORA, no con el de hace un segundo
    marcarEventosDesde(estado.ultimoEvento);
    let res;
    try {
      res = await escenario.run(p, leerParametros(tarjeta));
    } catch (error) {
      console.error(error);
      res = { tipo: 'warn', titulo: 'La prueba no pudo terminar', texto: String((error && error.message) || error) };
    }
    await refrescar();
    terminarMarca();
    pintarResultado(zona, res, res.precondicion ? [] : p.llamadas, escenario, p.notas);
  }));
}

/** Corre las filas de una tabla (E1–E8 o A4); `solo` = una fila o null para todas. */
async function correrTabla(escenario, solo, boton) {
  if (pruebaEnCurso !== null) return;
  const tarjeta = document.getElementById(`esc-${escenario.id}`);
  const zona = tarjeta.querySelector('.esc-res');
  const indices = solo === null ? escenario.tabla.map((_, i) => i) : [solo];
  const p = new Prueba(zona, escenario);
  await conCandado(escenario.id, () => conBoton(boton, 'Probando…', async () => {
    await refrescar();
    marcarEventosDesde(estado.ultimoEvento);
    const antes = { id: estado.S.id_simulacion, hash: estado.S.hash_red, altura: estado.S.altura_red };
    let bien = 0;
    for (const i of indices) {
      const fila = escenario.tabla[i];
      const celda = document.getElementById(`r-${escenario.id}-${i}`);
      celda.replaceChildren(h('span', { class: 'estado-m' }, icono('clock', 'ic-s'), 'probando…'));
      let registros;
      try {
        registros = [].concat(await fila.run(p));
      } catch (error) {
        console.error(error);
        registros = [];
      }
      const ok = registros.length > 0 && fila.esperado(registros);
      if (ok) bien += 1;
      const r = registros[0] || {};
      const mensaje = r.redCaida ? `sin respuesta: ${r.error}` : (r.error || r.mensaje || (r.esJson ? '' : 'la respuesta no es JSON'));
      celda.replaceChildren(
        h('span', { class: `badge ${ok ? 'ok' : 'warn'}` }, icono(ok ? 'check' : 'warn', 'ic-s'),
          `HTTP ${registros.map((x) => x.http || '—').join(' / ')}`),
        ' ', `«${mensaje}»`);
      celda.dataset.idsim = estado.idSimulacion || '';   // si se crea otra red, vuelve a «—»
      if (indices.length > 1) await esperar(150);
    }
    await refrescar();
    terminarMarca();
    const S = estado.S;
    const igual = S.id_simulacion === antes.id && S.hash_red === antes.hash;
    const n = indices.length;
    const todoBien = bien === n && igual;
    pintarResultado(zona, {
      tipo: todoBien ? 'ok' : 'warn',
      titulo: bien === n
        ? (n > 1 ? `Correcto: los ${n} casos se rechazaron con un mensaje claro.` : 'Correcto: se rechazó con un mensaje claro.')
        : `${bien} de ${n} casos se comportaron como se esperaba`,
      texto: igual ? 'La red no cambió: misma simulación, mismo bloque y mismo hash que antes.'
        : 'Ojo: la red cambió durante la prueba (¿había minería o una ronda en curso?).',
    }, p.llamadas);
  }));
}

/** Abre el paso 5, muestra la tarjeta y corre la prueba. */
function correrPorId(id) {
  irAPaso(5);
  filtro = 'todas';
  aplicarFiltro();
  const tarjeta = document.getElementById(`esc-${id}`);
  if (!tarjeta) return;
  tarjeta.scrollIntoView({ block: 'center', behavior: 'smooth' });
  correr(id);
}

/* ---------------------------------------------------------------- dibujar las tarjetas */

function tarjetaEscenario(escenario, modo) {
  const video = !!(escenario.video && escenario.video.includes(modo));
  const parametros = (escenario.params || []).map((par) => h('div', { class: 'campo' },
    h('label', { for: `p-${escenario.id}-${par.clave}` }, par.etiqueta),
    h('select', { id: `p-${escenario.id}-${par.clave}`, 'data-param': par.clave },
      par.opciones.map(([valor, etiqueta]) => h('option', { value: valor, selected: valor === par.inicial }, etiqueta)))));
  // En pantallas estrechas cada fila se apila (caso, envío y respuesta uno bajo otro): .apilada en estilos.css.
  const tabla = escenario.tabla ? h('div', { class: 'tabla-wrap' }, h('table', { class: 'mini apilada' },
    h('thead', {}, h('tr', {}, h('th', {}, 'Cód.'), h('th', {}, 'Caso'), h('th', {}, 'Lo que se envía'),
      h('th', {}, 'Respuesta del servidor'), h('th', {}, h('span', { class: 'sr-only' }, 'Probar')))),
    h('tbody', {}, escenario.tabla.map((fila, i) => h('tr', {},
      h('td', {}, h('span', { class: 'cod' }, fila.cod)),
      h('td', {}, fila.caso),
      h('td', {}, h('span', { class: 'hash' }, fila.envio)),
      h('td', { class: 'r', id: `r-${escenario.id}-${i}`, 'data-vacio': '—' }, h('span', { class: 'estado-m off' }, '—')),
      h('td', {}, h('button', { type: 'button', class: 'btn chico', 'data-fila': `${escenario.id}:${i}`, 'aria-label': `Probar: ${fila.caso}` },
        icono('play', 'ic-s')))))))) : null;
  return h('article', { class: `esc${escenario.tabla ? ' ancho' : ''}`, id: `esc-${escenario.id}`, 'data-grupo-esc': escenario.grupo, 'data-video': video ? '1' : '0' },
    h('div', { class: 'esc-cab' },
      h('h4', {}, video ? [icono('star', 'ic-s'), h('span', { class: 'sr-only' }, 'Recomendada para el video: ')] : null, escenario.titulo),
      h('span', { class: 'cods' }, escenario.cods.map((c) => h('span', { class: 'cod' }, c)))),
    h('p', {}, h('b', {}, 'Qué hace:'), ' ', escenario.hace),
    h('p', {}, h('b', {}, 'Debería pasar:'), ' ', escenario.espera),
    tabla,
    h('div', { class: 'esc-acc' }, parametros,
      h('button', { type: 'button', class: 'btn', 'data-probar': escenario.id, 'aria-label': `${escenario.boton || 'Probar'}: ${escenario.titulo}` },
        icono('play', 'ic-s'), escenario.boton || 'Probar')),
    h('div', { class: 'esc-conf' }),
    h('div', { class: 'esc-res', id: `res-${escenario.id}`, role: 'status' }));
}

/** Construye los filtros y las tarjetas del modo (sólo cuando cambia el modo). */
function dibujarEscenarios(modo) {
  modoDibujado = modo;
  const lista = ESCENARIOS.filter((e) => aplica(e, modo));
  const grupos = Object.keys(GRUPOS).filter((g) => lista.some((e) => e.grupo === g));
  if (filtro !== 'todas' && filtro !== 'video' && !grupos.includes(filtro)) filtro = 'todas';
  $('#filtros-esc').replaceChildren(...[['todas', 'Todas'], ['video', '★ Para el video'], ...grupos.map((g) => [g, GRUPOS[g]])]
    .map(([clave, etiqueta]) => h('button', { type: 'button', class: 'fchip', 'data-fesc': clave, 'aria-pressed': String(filtro === clave) }, etiqueta)));
  $('#lista-esc').replaceChildren(...grupos.map((g) => h('section', { class: 'grupo-esc', 'data-grupo-sec': g },
    h('h3', { class: 'grupo-t' }, GRUPOS[g]),
    h('div', { class: 'esc-grid' }, lista.filter((e) => e.grupo === g).map((e) => tarjetaEscenario(e, modo))))));
  if (pruebaEnCurso !== null) bloquearPruebas(pruebaEnCurso);   // una prueba que cambió de modo sigue corriendo
  const otros = contarEn(modo, true);
  texto($('#esc-otro-modo'), `Hay ${otros} pruebas más en modo ${modo === 'pow' ? 'PoS' : 'PoW'} (cámbialo arriba, en el encabezado).`);
  aplicarFiltro();
}

function aplicarFiltro() {
  for (const chip of $$('#filtros-esc .fchip')) chip.setAttribute('aria-pressed', String(chip.dataset.fesc === filtro));
  for (const tarjeta of $$('#lista-esc .esc')) {
    tarjeta.hidden = !(filtro === 'todas' || (filtro === 'video' ? tarjeta.dataset.video === '1' : tarjeta.dataset.grupoEsc === filtro));
  }
  for (const seccion of $$('#lista-esc .grupo-esc')) seccion.hidden = !seccion.querySelector('.esc:not([hidden])');
}

/* ---------------------------------------------------------------- nodos tramposos */

let modoTrampas = null;

function actualizarTramposos(S) {
  const selNodo = $('#tr-nodo');
  const vacio = selNodo.options.length === 0;
  sincronizarLista(selNodo, nodosDe(S), (n) => n.id, (n) => h('option', { value: n.id }, n.id), () => {});
  if (vacio && nodosDe(S).length) selNodo.value = nodosDe(S)[nodosDe(S).length - 1].id;
  if (modoTrampas !== S.modo) {
    modoTrampas = S.modo;
    $('#tr-trampa').replaceChildren(...trampasDelModo(S.modo).map((t) => h('option', { value: t }, TEXTO_TRAMPA[t])));
  }
  const tramposos = nodosDe(S).filter((n) => n.deshonesto);
  mostrar($('#tramp-vacio'), tramposos.length === 0);
  sincronizarLista($('#tramp-lista'), tramposos, (n) => n.id,
    (n) => h('li', {}, h('b', {}, n.id), h('span', { class: 'que' }),
      h('button', { type: 'button', class: 'btn chico', 'data-volver-honesto': n.id, 'aria-label': `Volver honesto a ${n.id}` }, 'Volver honesto')),
    (li, n) => texto(li.querySelector('.que'), ` ${(TEXTO_TRAMPA[n.trampa] || String(n.trampa)).toLowerCase()}`));
}

/* ---------------------------------------------------------------- A2: ¿se ve lo mismo tras recargar? */

let revisadaA2 = false;

function revisarRecarga(S) {
  if (revisadaA2) return;
  revisadaA2 = true;
  const guardado = leerGuardado('sessionStorage', 'simulador-prueba-a2');
  if (!guardado) return;
  guardar('sessionStorage', 'simulador-prueba-a2', null);
  let antes;
  try {
    antes = JSON.parse(guardado);
  } catch {
    return;
  }
  const zona = document.getElementById('res-A2');
  if (!zona) return;
  const ahora = S.modo === 'pow'
    ? { estado: objeto(S.pow).estado, numero: objeto(S.pow).numero }
    : { estado: objeto(S.pos).estado, numero: objeto(S.pos).numero };
  const nombre = (codigo) => (S.modo === 'pow' ? NOMBRE_ESTADO_POW[codigo] || String(codigo) : nombreEstadoPos(codigo));
  const misma = antes.id === S.id_simulacion && antes.modo === S.modo;
  const bien = misma && (S.modo === 'pos'
    ? ahora.estado === antes.estado && ahora.numero === antes.numero
    : num(ahora.numero) >= num(antes.numero));
  pintarResultado(zona, {
    tipo: bien ? 'ok' : 'warn',
    titulo: bien ? 'Correcto: tras recargar se ve lo mismo.' : 'Resultado distinto al esperado',
    texto: `Antes de recargar: ${nombre(antes.estado)} (bloque ${antes.numero}). Después: ${nombre(ahora.estado)} (bloque ${ahora.numero}). `
      + (misma ? 'Es la misma simulación: el estado vive en el servidor y la página lo reconstruyó.' : 'La simulación cambió (¿se creó otra red?).'),
  });
  irAPaso(5);
  zona.scrollIntoView({ block: 'center' });
}

/* ---------------------------------------------------------------- todo el paso */

export function actualizarPaso5(S) {
  if (modoDibujado !== S.modo) dibujarEscenarios(S.modo);
  actualizarTramposos(S);
  revisarRecarga(S);
}

/** Conecta los clics (una vez). */
export function iniciarPaso5() {
  $('#filtros-esc').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-fesc]');
    if (!chip) return;
    filtro = chip.dataset.fesc;
    aplicarFiltro();
  });
  $('#lista-esc').addEventListener('click', (e) => {
    const probar = e.target.closest('[data-probar]');
    if (probar) {
      correr(probar.dataset.probar);
      return;
    }
    const fila = e.target.closest('[data-fila]');
    if (fila) {
      const [id, i] = fila.dataset.fila.split(':');
      const escenario = ESCENARIOS.find((x) => x.id === id);
      if (escenario) correrTabla(escenario, Number(i), fila);
    }
  });

  // Nodos tramposos.
  $('#b-tramposo').addEventListener('click', async (e) => {
    const id = $('#tr-nodo').value;
    const trampa = $('#tr-trampa').value;
    const r = await conBoton(e.currentTarget, 'Enviando…', () => post(rutaNodo(id, '/deshonesto'), { deshonesto: true, trampa }));
    if (!r) return;
    const cuando = estado.S && estado.S.modo === 'pow' ? 'gane la carrera' : trampa === 'voto_invertido' ? 'vote' : 'gane el sorteo';
    mostrarRespuesta($('#res-tramposos'), r, {
      tituloOk: `${id} es tramposo: ${(TEXTO_TRAMPA[trampa] || trampa).toLowerCase()}.`,
      textoOk: `Hará esta trampa cuando ${cuando}. Mira su chip arriba (!).`,
    });
    await refrescar();
  });
  $('#tramp-lista').addEventListener('click', async (e) => {
    const boton = e.target.closest('[data-volver-honesto]');
    if (!boton) return;
    const id = boton.dataset.volverHonesto;
    const r = await conBoton(boton, 'Enviando…', () => post(rutaNodo(id, '/deshonesto'), { deshonesto: false }));
    if (!r) return;
    mostrarRespuesta($('#res-tramposos'), r, { tituloOk: `${id} vuelve a ser honesto.`, textoOk: '' });
    await refrescar();
  });

  // Para expertos: cualquier petición a mano.
  $('#form-cruda').addEventListener('submit', async (e) => {
    e.preventDefault();
    const zona = $('#res-cruda');
    const metodo = $('#cr-metodo').value;
    const escrita = $('#cr-ruta').value.trim();
    // Sólo rutas de ESTE servidor: «//otro.com/x» (o con «\» o un tabulador) saldría a otro dominio.
    let destino = null;
    try {
      destino = escrita.startsWith('/') ? new URL(escrita, window.location.origin) : null;
    } catch {
      destino = null;
    }
    if (!destino || destino.origin !== window.location.origin) {
      resultado(zona, { tipo: 'error', titulo: 'La ruta debe empezar con «/» y ser de este servidor.', texto: 'Por ejemplo: /api/estado o /api/transacciones' });
      return;
    }
    const ruta = `${destino.pathname}${destino.search}`;
    const cuerpo = metodo === 'GET' ? undefined : $('#cr-cuerpo').value;
    const r = await conBoton($('#b-cruda'), 'Enviando…', () => llamar(metodo, ruta, cuerpo, { crudo: true }));
    if (!r) return;
    let tipo = 'info';
    let titulo = `HTTP ${r.http}`;
    if (r.redCaida) {
      tipo = 'error';
      titulo = `Sin respuesta: ${r.error}`;
    } else if (!r.esJson) {
      tipo = 'warn';
      titulo = `HTTP ${r.http} — la respuesta no es JSON`;
    } else if (r.http >= 500) {
      tipo = 'warn';
      titulo = `HTTP ${r.http} — error del servidor`;
    } else if (r.http >= 400) {
      tipo = 'ok';
      titulo = `HTTP ${r.http} — rechazada con un JSON claro (correcto)`;
    }
    const contenido = r.esJson ? JSON.stringify(r.json, null, 2) : r.texto;
    resultado(zona, { tipo, titulo, extra: [h('pre', {}, contenido.length > 8000 ? `${contenido.slice(0, 8000)}\n…` : contenido)] });
    await refrescar();
  });
}
