/* ==========================================================================
   paso2_transacciones.js — Paso 2 «Crear transacciones» (docs/UX.md §4).

   - Enviar monedas: De / Para / Monto → POST /api/transacciones. El servidor
     firma con la clave del emisor, verifica la firma y el saldo, y la deja
     en espera.
   - Generar N al azar → POST /api/transacciones/aleatorias.
   - Tabla «En espera» con la firma de cada una.
   ========================================================================== */

import { post } from './api.js';
import { estado, nodoPorId, nodosDe, refrescar } from './estado.js';
import { botonAyuda } from './glosario.js';
import { conBoton, limpiar, mostrarRespuesta, resultado } from './ui.js';
import { $, fmt, h, icono, lista, num, objeto, siCambia, sincronizarLista, texto, mostrar } from './util.js';

/** Lee el monto como el servidor (leer_monto); devuelve { valor } o { error }. */
function leerMonto(crudo) {
  const t = String(crudo ?? '');
  if (t.trim() === '') return { error: 'El monto es obligatorio' };
  const entero = /^\s*([+-]?)(\d+)\s*$/.exec(t);
  if (entero) {
    const n = Number(`${entero[1]}${entero[2]}`);
    if (n < 0) return { error: `El monto no puede ser negativo (recibido: ${n})` };
    if (n === 0) return { error: 'El monto debe ser mayor que cero' };
    if (n > 1e12) return { error: 'El monto es demasiado grande (máximo 1000000000000)' };
    return { valor: n };
  }
  if (/^\s*[+-]?(\d+\.\d*|\.\d+)\s*$/.test(t)) return { error: 'El monto debe ser un número entero, sin decimales' };
  return { error: `El monto debe ser un número entero (recibido: '${t.slice(0, 38)}')` };
}

const CAMPOS = { emisor: '#tx-emisor', receptor: '#tx-receptor', monto: '#tx-monto' };

function limpiarErrores() {
  for (const [campo, selector] of Object.entries(CAMPOS)) {
    texto($(`#e-${campo}`), '');
    $(selector).removeAttribute('aria-invalid');
  }
}

/** Marca un campo con su error y le pone el foco (§9). */
function marcarCampo(campo, mensaje) {
  const el = $(CAMPOS[campo]);
  texto($(`#e-${campo}`), mensaje);
  el.setAttribute('aria-invalid', 'true');
  el.focus();
}

/** ¿A qué campo se refiere un rechazo del servidor? */
function campoDelError(r) {
  const m = r.error || '';
  if (r.codigo === 'saldo_insuficiente' || /monto/i.test(m)) return 'monto';
  if (/^(El )?emisor y el receptor/i.test(m)) return 'receptor';
  if (/^(El )?emisor/i.test(m)) return 'emisor';
  if (/^(El )?receptor/i.test(m)) return 'receptor';
  return null;
}

/** Opciones de «De» y «Para» (se actualizan sin perder lo elegido). */
function actualizarSelectores(S) {
  const nodos = nodosDe(S);
  const emisor = $('#tx-emisor');
  const receptor = $('#tx-receptor');
  const eraVacio = emisor.options.length === 0;
  sincronizarLista(emisor, nodos, (n) => n.id, (n) => h('option', { value: n.id }),
    (opcion, n) => texto(opcion, `${n.id} · ${fmt(num(n.disponible))} disponibles`));
  sincronizarLista(receptor, nodos, (n) => n.id, (n) => h('option', { value: n.id }), (opcion, n) => texto(opcion, n.id));
  if (eraVacio && nodos.length > 1) {
    emisor.value = nodos[0].id;
    receptor.value = nodos[1].id;
  }
  if (!emisor.value && nodos.length) emisor.value = nodos[0].id;
  if (!receptor.value && nodos.length > 1) receptor.value = nodos[1].id;
  actualizarPista(S);
}

/** «N01 puede enviar hasta 125.» */
function actualizarPista(S) {
  const n = nodoPorId(S, $('#tx-emisor').value);
  if (!n) {
    texto($('#tx-pista'), '');
    return;
  }
  let pista = `${n.id} puede enviar hasta ${fmt(num(n.disponible))}.`;
  const pendiente = num(n.total_recompensas_pendientes);
  if (S.modo === 'pow' && pendiente > 0) pista += ` (Tiene ${fmt(pendiente)} en recompensas que aún no maduran.)`;
  texto($('#tx-pista'), pista);
}

/** Tabla «En espera» y castigos por registrar. */
function actualizarPendientes(S) {
  const pendientes = lista(S.pendientes);
  const maxTx = num(objeto(S.config).max_tx_por_bloque);
  texto($('#pend-n'), String(pendientes.length));
  texto($('#pend-nota'), pendientes.length
    ? `Al ${S.modo === 'pow' ? 'minar' : 'proponer'}, el bloque toma hasta ${maxTx} de estas, en este orden.` : '');
  mostrar($('#pend-tabla'), pendientes.length > 0);
  mostrar($('#pend-vacio'), pendientes.length === 0);
  let i = 0;
  sincronizarLista($('#pend-filas'), pendientes, (tx) => tx.id,
    (tx) => {
      const firma = typeof tx.firma === 'string' ? tx.firma : '';
      const id = typeof tx.id === 'string' ? tx.id : '';
      return h('tr', {},
        h('td', { class: 'pos-n' }),
        h('td', {}, h('b', {}, String(tx.emisor)), ` → ${tx.receptor}`),
        h('td', { class: 'num' }, fmt(num(tx.monto))),
        h('td', {}, h('span', { class: 'hash', title: firma }, `${firma.slice(0, 8)}…`), ' ',
          h('span', { class: 'voto-si' }, icono('check', 'ic-s'), h('span', { class: 'sr-only' }, 'firma verificada'))),
        h('td', {}, h('span', { class: 'hash', title: id }, `${id.slice(0, 8)}…`)));
    },
    (fila) => {
      i += 1;
      texto(fila.firstElementChild, String(i));
    });

  const castigos = lista(S.castigos_pendientes);
  const caja = $('#castigos-pend');
  mostrar(caja, S.modo === 'pos' && castigos.length > 0);
  siCambia(caja, JSON.stringify(castigos.map((c) => [c.nodo, c.monto])), () => [
    icono('scale'), ' ', h('b', {}, 'Castigos por registrar en el siguiente bloque: '),
    castigos.map((c) => `${c.nodo} −${fmt(num(c.monto))}`).join(', '), ' ',
    botonAyuda('castigo', '¿Qué es el castigo?'),
  ]);
}

/** Se llama con cada instantánea. */
export function actualizarPaso2(S) {
  actualizarSelectores(S);
  actualizarPendientes(S);
}

/** Conecta el formulario (una vez). */
export function iniciarPaso2() {
  $('#tx-emisor').addEventListener('change', () => estado.S && actualizarPista(estado.S));

  $('#form-tx').addEventListener('submit', async (e) => {
    e.preventDefault();
    const zona = $('#res-tx');
    limpiarErrores();
    const emisor = $('#tx-emisor').value;
    const receptor = $('#tx-receptor').value;
    if (emisor === receptor) {
      marcarCampo('receptor', `El emisor y el receptor deben ser distintos (ambos son ${emisor})`);
      resultado(zona, { tipo: 'error', titulo: 'Revisa el campo marcado en rojo.', texto: 'Elige otro nodo en «Para».' });
      return;
    }
    const monto = leerMonto($('#tx-monto').value);
    if (monto.error) {
      marcarCampo('monto', monto.error);
      resultado(zona, { tipo: 'error', titulo: 'Revisa el campo marcado en rojo.', texto: monto.error });
      return;
    }
    const boton = $('#b-enviar-tx');
    const r = await conBoton(boton, 'Enviando…', () => post('/api/transacciones', { emisor, receptor, monto: monto.valor }));
    if (!r) return;
    if (r.ok) {
      resultado(zona, { tipo: 'ok', titulo: `${emisor} envió ${fmt(monto.valor)} a ${receptor}.`, texto: 'Firma verificada; queda en espera.' });
      $('#tx-monto').value = '';
    } else {
      const campo = campoDelError(r);
      if (campo) marcarCampo(campo, r.error);
      mostrarRespuesta(zona, r);
    }
    await refrescar();
  });

  $('#b-aleatorias').addEventListener('click', async (e) => {
    const zona = $('#res-aleatorias');
    const cantidad = Number($('#tx-cuantas').value) || 3;
    const r = await conBoton(e.currentTarget, 'Generando…', () => post('/api/transacciones/aleatorias', { cantidad }));
    if (!r) return;
    if (r.ok) resultado(zona, { tipo: 'ok', titulo: r.mensaje || 'Listo.', texto: 'Cada una va firmada por su emisor y quedó en espera.' });
    else mostrarRespuesta(zona, r);
    await refrescar();
  });

  $('#tx-monto').addEventListener('input', () => {
    if ($('#tx-monto').hasAttribute('aria-invalid')) {
      $('#tx-monto').removeAttribute('aria-invalid');
      texto($('#e-monto'), '');
      limpiar($('#res-tx'));
    }
  });
}
