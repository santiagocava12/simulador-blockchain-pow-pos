/* ==========================================================================
   ui.js — Piezas de interfaz que se repiten:
     · línea de resultado bajo cada acción (ok / error / aviso / info),
     · respuesta del servidor con su remedio («Crear 3 al azar»…),
     · confirmación en línea para lo que borra (nunca confirm()),
     · botón «en curso» mientras su petición viaja,
     · detalle técnico de las llamadas (paso 5).
   ========================================================================== */

import { post } from './api.js';
import { estado, nombreEstadoPos, refrescar } from './estado.js';
import { irAPaso } from './navegacion.js';
import { $$, h, icono } from './util.js';

const ICONOS = { ok: 'check', error: 'x', warn: 'warn', info: 'info' };

/**
 * Escribe una línea de resultado en `zona` (un elemento con role="status").
 *   tipo:     'ok' | 'error' | 'warn' | 'info'
 *   titulo:   frase en negrita
 *   texto:    texto (o nodos) debajo
 *   extra:    nodos adicionales (listas, tablas…)
 *   acciones: [{ texto, icono, fn(zona, boton) }] botones pequeños
 *   llamadas: registros de api.js para «Ver detalle técnico»
 * La zona se marca con la simulación actual: si después se crea otra red,
 * los resultados viejos se borran solos (limpiarResultadosViejos).
 */
export function resultado(zona, opciones = {}) {
  if (!zona) return;
  const { tipo = 'ok', titulo = '', texto = null, extra = [], acciones = [], llamadas = [], idSimulacion } = opciones;
  const cuerpo = h('div', {}, h('b', { class: 't' }, titulo));
  if (texto !== null && texto !== undefined && texto !== '') cuerpo.append(h('p', {}, texto));
  for (const nodo of extra) if (nodo) cuerpo.append(nodo);
  if (acciones.length) {
    cuerpo.append(h('div', { class: 'acciones' }, acciones.map((a) => h('button', {
      type: 'button',
      class: 'btn chico',
      onclick: (ev) => a.fn(zona, ev.currentTarget),
    }, icono(a.icono || 'arrow', 'ic-s'), a.texto))));
  }
  if (llamadas.length) cuerpo.append(detalleTecnico(llamadas));
  // Si el foco estaba dentro (un botón de acción del resultado anterior), no se pierde en <body>:
  // pasa al primer botón del resultado nuevo o, si no hay, a la zona misma.
  const teniaFoco = zona.contains(document.activeElement);
  zona.replaceChildren(h('div', { class: `res ${tipo}` }, icono(ICONOS[tipo] || 'info'), cuerpo));
  zona.dataset.idsim = idSimulacion || estado.idSimulacion || '';
  if (teniaFoco) {
    const destino = zona.querySelector('button');
    if (destino) destino.focus({ preventScroll: true });
    else {
      zona.tabIndex = -1;
      zona.focus({ preventScroll: true });
    }
  }
  delete zona.dataset.vigencia;   // un resultado nuevo todavía no tiene vigencia (ver fijarVigencia)
}

/**
 * Borra la línea de resultado. Si la zona tiene data-vacio (por ejemplo, una
 * celda de las tablas del paso 5), vuelve a mostrar ese texto («—»).
 */
export function limpiar(zona) {
  if (!zona) return;
  if (zona.dataset.vacio) zona.replaceChildren(h('span', { class: 'estado-m off' }, zona.dataset.vacio));
  else zona.replaceChildren();
  delete zona.dataset.idsim;
  delete zona.dataset.vigencia;
}

/*
 * Vigencia de los mensajes. Algunas zonas (la de «Ahora», las del paso 3 PoS)
 * dicen de qué depende lo que muestran con una «llave» del estado: la regla de
 * «Ahora», o el bloque/intento/estado de la ronda. El mensaje de una acción se
 * queda mientras la llave sea la misma que había al terminar la acción y se
 * borra solo en cuanto cambia (por ejemplo, el 409 «Ya se está minando…»
 * desaparece cuando esa minería termina).
 */

/** La zona usará llave() (una función sin argumentos) para saber si su mensaje sigue valiendo. */
export function conVigencia(zona, llave) {
  if (zona) zona._llave = llave;
}

/** Fija la llave del mensaje que muestra la zona. Se llama DESPUÉS de refrescar. */
export function fijarVigencia(zona) {
  if (zona && zona._llave && zona.childElementCount) zona.dataset.vigencia = String(zona._llave());
}

/** Borra el mensaje de la zona si el estado ya cambió desde que se escribió (se llama con cada instantánea). */
export function caducar(zona) {
  if (zona && zona._llave && zona.dataset.vigencia !== undefined && zona.dataset.vigencia !== String(zona._llave())) {
    limpiar(zona);
  }
}

/** Tras crear otra red se borran los resultados que hablaban de la anterior. */
export function limpiarResultadosViejos(idActual) {
  for (const zona of $$('[data-idsim]')) {
    if (zona.dataset.idsim && zona.dataset.idsim !== idActual) limpiar(zona);
  }
}

/** Bloque «Ver detalle técnico (n llamadas)»: método, ruta, cuerpo, HTTP y JSON. */
export function detalleTecnico(llamadas) {
  const partes = llamadas.map((l) => {
    let cuerpo = '';
    if (l.cuerpo !== undefined) {
      cuerpo = typeof l.cuerpo === 'string' ? l.cuerpo : JSON.stringify(l.cuerpo);
      if (cuerpo.length > 400) cuerpo = `${cuerpo.slice(0, 400)}… (${cuerpo.length} caracteres)`;
      cuerpo = `\n  cuerpo: ${cuerpo}`;
    }
    let respuesta;
    if (l.redCaida) respuesta = `sin respuesta (${l.error})`;
    else if (l.esJson) respuesta = JSON.stringify(l.json, null, 2);
    else respuesta = `(no es JSON) ${l.texto.slice(0, 300)}`;
    if (respuesta.length > 6000) respuesta = `${respuesta.slice(0, 6000)}\n…`;
    return `${l.metodo} ${l.ruta}${cuerpo}\n  → HTTP ${l.http || '—'}\n${respuesta}`;
  });
  const n = llamadas.length;
  return h('details', {},
    h('summary', {}, `Ver detalle técnico (${n} ${n === 1 ? 'llamada' : 'llamadas'})`),
    h('pre', {}, partes.join('\n\n')));
}

/**
 * Muestra lo que respondió el servidor a una acción:
 * verde con su mensaje, o rojo con el error tal cual y el remedio si existe.
 * opciones: tituloOk / textoOk (éxito) y tituloError (rechazo).
 */
export function mostrarRespuesta(zona, r, opciones = {}) {
  const { tituloOk, textoOk, tituloError } = opciones;
  if (r.ok) {
    resultado(zona, {
      tipo: 'ok',
      titulo: tituloOk || r.mensaje || 'Listo.',
      texto: tituloOk ? (textoOk !== undefined ? textoOk : r.mensaje) : null,
      idSimulacion: r.idSimulacion,
    });
    return;
  }
  if (r.codigo === 'estado_cambio') {
    // Otra pestaña (u otro clic) avanzó la ronda antes: no es un error grave.
    const actual = nombreEstadoPos(r.detalles && r.detalles.estado_actual);
    resultado(zona, {
      tipo: 'info',
      titulo: `La ronda ya avanzó en otra pestaña (estado actual: ${actual}). Se actualizó la vista.`,
      texto: `Respuesta del servidor: «${r.error}».`,
    });
    refrescar().then(() => fijarVigencia(zona));
    return;
  }
  if (r.redCaida) {
    resultado(zona, { tipo: 'error', titulo: 'No se pudo: no hay conexión con el servidor.', texto: `${r.error}. Se reintentará solo.` });
    return;
  }
  const acciones = [];
  let titulo = tituloError || 'No se pudo: el servidor lo rechazó.';
  if (r.codigo === 'sin_pendientes') {
    acciones.push({
      texto: 'Crear 3 al azar',
      icono: 'dice',
      fn: async (z, boton) => {
        const r2 = await conBoton(boton, 'Creando…', () => post('/api/transacciones/aleatorias', { cantidad: 3 }));
        if (r2) mostrarRespuesta(z, r2);
        await refrescar();
        fijarVigencia(z);
      },
    });
  }
  if (r.codigo === 'saldo_insuficiente' && /recompensa/i.test(r.error)) {
    acciones.push({ texto: 'Ver recompensas por madurar', icono: 'clock', fn: () => irAPaso(3, { foco: true }) });
  }
  if (r.codigo === 'mineria_en_curso') {
    const m = /bloque (\d+)/.exec(r.error);
    titulo = m ? `Ya se está minando el bloque ${m[1]}. Espera o pulsa «Detener».`
      : 'Ya se está minando un bloque. Espera o pulsa «Detener».';
    acciones.push({
      texto: 'Detener',
      icono: 'stop',
      fn: async (z, boton) => {
        const r2 = await conBoton(boton, 'Deteniendo…', () => post('/api/pow/cancelar', {}));
        if (r2) mostrarRespuesta(z, r2);
        await refrescar();
        fijarVigencia(z);
      },
    });
  }
  const textoError = [`«${r.error}» `, h('span', { class: 'cod' }, `${r.codigo || 'error'} · HTTP ${r.http}`)];
  const extra = [];
  if (r.codigo === 'nodo_inexistente' && !/válidos/.test(r.error) && estado.S) {
    const ids = (estado.S.nodos || []).map((n) => n.id);
    if (ids.length) extra.push(h('p', {}, `Nodos válidos: ${ids[0]}–${ids[ids.length - 1]}`));
  }
  resultado(zona, { tipo: 'error', titulo, texto: textoError, extra, acciones });
}

/**
 * Confirmación en línea (para lo que borra): un panel pequeño con el efecto en
 * una frase y [Sí, …] [Cancelar]. Esc cancela. Devuelve una promesa con true/false.
 * Al cerrarse (con cualquier respuesta) el foco vuelve a `volverA`, el botón
 * que la abrió, para que no se pierda en la página.
 */
export function confirmarEnLinea(contenedor, texto, textoSi = 'Sí, continuar', volverA = null, textoNo = 'Cancelar') {
  return new Promise((resolver) => {
    if (contenedor._cerrar) contenedor._cerrar(false);   // sólo una confirmación a la vez
    const flotante = contenedor.classList.contains('confirmar');
    const si = h('button', { type: 'button', class: 'btn pri chico' }, textoSi);
    const no = h('button', { type: 'button', class: 'btn chico' }, textoNo);
    const caja = h('div', { class: flotante ? '' : 'confirmar', role: 'group', 'aria-label': 'Confirmar' },
      h('p', {}, icono('warn'), h('span', {}, texto)),
      h('div', { class: 'acciones' }, si, no));
    const alTeclear = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        cerrar(false);
      }
    };
    function cerrar(respuesta) {
      document.removeEventListener('keydown', alTeclear, true);
      contenedor._cerrar = null;
      contenedor.replaceChildren();
      if (flotante) contenedor.hidden = true;
      if (volverA && document.contains(volverA)) volverA.focus();
      resolver(respuesta);
    }
    contenedor._cerrar = cerrar;
    contenedor.replaceChildren(caja);
    contenedor.hidden = false;
    si.addEventListener('click', () => cerrar(true));
    no.addEventListener('click', () => cerrar(false));
    document.addEventListener('keydown', alTeclear, true);
    si.focus();
  });
}

/**
 * Ejecuta fn() con el botón «en curso»: desactivado, con texto de progreso
 * («Minando…») y aria-busy. Si ya estaba en curso, no hace nada (devuelve null).
 */
export async function conBoton(boton, textoProgreso, fn) {
  if (!boton) return fn();
  if (boton.getAttribute('aria-busy') === 'true') return null;
  const contenido = Array.from(boton.childNodes);
  const teniaFoco = document.activeElement === boton;
  boton.disabled = true;
  boton.setAttribute('aria-busy', 'true');
  boton.replaceChildren(icono('clock'), textoProgreso);
  try {
    return await fn();
  } finally {
    boton.replaceChildren(...contenido);
    boton.disabled = false;
    boton.removeAttribute('aria-busy');
    if (teniaFoco && (document.activeElement === document.body || document.activeElement === null)) boton.focus();
  }
}

/** ¿El botón está esperando su petición? (los módulos no lo tocan mientras tanto) */
export const ocupado = (boton) => !!boton && boton.getAttribute('aria-busy') === 'true';
