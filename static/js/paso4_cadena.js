/* ==========================================================================
   paso4_cadena.js — Paso 4 «Ver la cadena y la red» (docs/UX.md §4 y §6.6).

   - Tabla de nodos (8 columnas): estado, bloque, si está sincronizado, último
     hash, saldo disponible, recompensas por madurar (PoW) o apuesta
     (PoS) y los botones «Ver cadena» y «Desconectar»/«Reconectar».
   - Explorador: GET /api/nodos/<id>/cadena trae la copia de la cadena de ese
     nodo; se dibuja como una tira de bloques unidos por eslabones y, al
     elegir uno, su ficha completa (con el JSON cerrado).
   ========================================================================== */

import { get, post, rutaNodo } from './api.js';
import { estado, nodoPorId, nodosDe, posActiva, powActivo, refrescar } from './estado.js';
import { irAPaso } from './navegacion.js';
import { alMostrarPaso, pasoVisible } from './pasos.js';
import { enJuego } from './pos_comun.js';
import { TEXTO_TRAMPA } from './trampas.js';
import { conBoton, limpiar, mostrarRespuesta, ocupado, resultado } from './ui.js';
import {
  $, atributo, clases, fmt, h, hashCorto, hashLargo, icono, lista, mostrar, num, objeto, plural, siCambia, sincronizarLista, texto,
} from './util.js';

let nodoElegido = 'N01';
let bloqueElegido = null;          // número del bloque de la ficha
let seguirUltimo = true;           // la ficha sigue al último bloque mientras no se elija otro
let cadena = [];                   // copia de la cadena del nodo elegido
let cargada = null;                // qué cadena se tiene: "idSimulacion|nodo|último hash"
let cargando = false;

/* ---------------------------------------------------------------- tabla de nodos */

function insignias(S, n) {
  const pos = objeto(S.pos);
  const marcas = [];
  marcas.push(n.conectado ? h('span', { class: 'badge ok' }, 'Conectado') : h('span', { class: 'badge' }, icono('power', 'ic-s'), 'Desconectado'));
  if (powActivo(S) && n.conectado) marcas.push(h('span', { class: 'badge pow' }, icono('pick', 'ic-s'), 'Minando'));
  if (posActiva(S) && enJuego(pos).some((v) => v.id === n.id)) {
    const propone = pos.proponente === n.id && ['SORTEO', 'CANDIDATO', 'VOTACION'].includes(pos.estado);
    marcas.push(propone ? h('span', { class: 'badge pos' }, icono('star', 'ic-s'), 'Proponente')
      : h('span', { class: 'badge pos' }, icono('shield', 'ic-s'), 'Validador'));
  }
  if (n.deshonesto) marcas.push(h('span', { class: 'badge err' }, `Tramposo: ${(TEXTO_TRAMPA[n.trampa] || String(n.trampa)).toLowerCase()}`));
  return h('span', { class: 'badges' }, marcas);
}

function actualizarTabla(S) {
  const pow = S.modo === 'pow';
  texto($('#th-extra'), pow ? 'Por madurar' : 'Apuesta');
  sincronizarLista($('#nodos-filas'), nodosDe(S), (n) => n.id,
    (n) => h('tr', {},
      h('td', {}, h('b', {}, n.id)), h('td', {}), h('td', { class: 'num' }), h('td', {}), h('td', {}),
      h('td', { class: 'num' }), h('td', { class: 'num' }),
      h('td', {}, h('span', { class: 'acciones', style: 'flex-wrap:nowrap;gap:6px' },
        h('button', { type: 'button', class: 'btn chico', 'data-ver': n.id }, icono('eye', 'ic-s'), 'Ver cadena'),
        h('button', { type: 'button', class: 'btn chico', 'data-conexion': n.id })))),
    (fila, n) => {
      const c = fila.children;
      clases(fila, nodoElegido === n.id ? 'sel' : '');
      siCambia(c[1], JSON.stringify([n.conectado, n.deshonesto, n.trampa, powActivo(S), objeto(S.pos).estado, objeto(S.pos).proponente,
        enJuego(objeto(S.pos)).some((v) => v.id === n.id)]), () => insignias(S, n));
      texto(c[2], String(num(n.altura)));
      siCambia(c[4], String(n.ultimo_hash), () => hashCorto(n.ultimo_hash, 10));
      // «Sincronizado» va antes del hash: es lo que pide la rúbrica y se ve aun en un teléfono.
      siCambia(c[3], String(!!n.sincronizado), () => (n.sincronizado
        ? h('span', { class: 'voto-si' }, icono('check', 'ic-s'), 'Sí')
        : h('span', { class: 'voto-atras' }, icono('x', 'ic-s'), 'Atrasado')));
      texto(c[5], fmt(num(n.disponible)));
      if (pow) {
        const pendiente = num(n.total_recompensas_pendientes);
        const faltan = lista(n.recompensas_pendientes).map((r) => num(r.faltan));
        siCambia(c[6], `${pendiente}|${faltan.join(',')}`, () => (pendiente
          ? [fmt(pendiente), ' ', h('span', { class: 'pista', style: 'margin:0' }, `(faltan ${Math.min(...faltan)})`)] : '—'));
      } else {
        siCambia(c[6], `a|${num(n.apuesta_bloqueada)}`, () => (num(n.apuesta_bloqueada) ? fmt(num(n.apuesta_bloqueada)) : '—'));
      }
      const boton = c[7].querySelector('[data-conexion]');
      if (!ocupado(boton)) {
        siCambia(boton, String(!!n.conectado), () => [icono('power', 'ic-s'), n.conectado ? 'Desconectar' : 'Reconectar']);
        atributo(boton, 'aria-label', `${n.conectado ? 'Desconectar' : 'Reconectar'} ${n.id}`);
      }
      atributo(c[7].querySelector('[data-ver]'), 'aria-label', `Ver la cadena de ${n.id}`);
    });
}

/* ---------------------------------------------------------------- explorador de la cadena */

function fechaUTC(ms) {
  if (!Number.isFinite(ms)) return '—';
  try {
    return `${new Date(ms).toLocaleString('es-MX', { timeZone: 'UTC', dateStyle: 'medium', timeStyle: 'medium' })} UTC`;
  } catch {
    return new Date(ms).toISOString();
  }
}

/** Pide la cadena del nodo elegido si cambió (otro nodo, bloque nuevo u otra red). */
async function cargarSiHaceFalta() {
  const S = estado.S;
  if (!S || pasoVisible() !== 4 || cargando) return;
  if (!nodoPorId(S, nodoElegido)) {
    nodoElegido = nodosDe(S)[0] ? nodosDe(S)[0].id : 'N01';
    bloqueElegido = null;
    seguirUltimo = true;
  }
  const n = nodoPorId(S, nodoElegido);
  if (!n) return;
  const llave = `${S.id_simulacion}|${n.id}|${n.ultimo_hash}`;
  if (cargada === llave) return;
  cargando = true;
  if (!cadena.length) siCambia($('#tira-bloques'), 'cargando', () => h('p', { class: 'cargando' }, 'Cargando la cadena…'));
  const r = await get(rutaNodo(n.id, '/cadena'));
  cargando = false;
  // Mientras viajaba la petición se pudo elegir otro nodo o crear otra red (aquí o en otra
  // pestaña): esa respuesta ya no sirve, se descarta y se pide la que corresponde.
  if (r.idSimulacion && estado.S && r.idSimulacion !== estado.S.id_simulacion) {
    refrescar();   // el servidor ya tiene otra red: el sondeo la trae y vuelve a pedir la cadena
    return;
  }
  if (n.id !== nodoElegido || !estado.S || estado.S.id_simulacion !== S.id_simulacion) {
    cargarSiHaceFalta();
    return;
  }
  if (r.ok && Array.isArray(r.datos.cadena)) {
    limpiar($('#res-cadena'));
    cadena = r.datos.cadena;
    cargada = llave;
    dibujarCadena(estado.S);
  } else {
    resultado($('#res-cadena'), { tipo: 'error', titulo: 'No se pudo leer la cadena.', texto: r.error });
  }
}

/** Tira de bloques + ficha del bloque elegido. */
function dibujarCadena(S) {
  if (!S) return;
  const n = nodoPorId(S, nodoElegido);
  const altura = cadena.length - 1;
  if (bloqueElegido !== null && bloqueElegido > altura) seguirUltimo = true;   // la cadena se acortó
  if (seguirUltimo || bloqueElegido === null) bloqueElegido = altura;
  texto($('#cadena-rango'), altura >= 0
    ? `bloques 0 a ${altura}${n && !n.sincronizado ? ` · se quedó atrás (la red va en el ${num(S.altura_red)})` : ''}` : '');
  const pow = S.modo === 'pow';
  const tira = $('#tira-bloques');
  const firma = `${nodoElegido}|${cadena.length}|${cadena.length ? cadena[cadena.length - 1].hash : ''}`;
  const reconstruida = siCambia(tira, firma, () => cadena.map((b, i) => [
    i ? h('span', { class: 'eslabon', 'aria-hidden': 'true' }, icono('link', 'ic-s')) : null,
    h('button', { type: 'button', class: 'blq', 'data-bloque': i },
      h('span', { class: 'bn' }, `#${i}${i === 0 ? ' Génesis' : ''}`),
      hashCorto(b.hash, 10),
      h('span', { class: 'bl' }, `↑ ${i ? `${String(b.hash_anterior).slice(0, 8)}…` : 'sin anterior'}`),
      h('span', { class: 'bl' }, i
        ? `${pow ? 'Minó' : 'Propuso'} ${b.proponente} · ${lista(b.transacciones).length} tx`
        : 'Saldos iniciales')),
  ]));
  for (const boton of tira.querySelectorAll('[data-bloque]')) {
    const elegido = Number(boton.dataset.bloque) === bloqueElegido;
    boton.classList.toggle('sel', elegido);
    atributo(boton, 'aria-pressed', String(elegido));
  }
  if (reconstruida && bloqueElegido === altura) tira.scrollLeft = tira.scrollWidth;   // se ve el último bloque
  dibujarFicha(S);
}

function dibujarFicha(S) {
  const i = bloqueElegido;
  const b = cadena[i];
  const pow = S.modo === 'pow';
  const conf = num(S.confirmaciones, pow ? 6 : 0);
  const ficha = $('#ficha');
  // Lo único que cambia con bloques nuevos es «pendiente: faltan k» (PoW): sólo eso entra en la firma,
  // así un bloque nuevo no reconstruye la ficha de otro ni cierra su «Ver JSON completo».
  const faltanDeEste = pow && b && i > 0 ? Math.max(0, num(b.numero) + conf - (cadena.length - 1)) : null;
  const deEste = `${nodoElegido}|${i}|${b ? b.hash : ''}`;
  const jsonAbierto = ficha._deEste === deEste && !!(ficha.querySelector('details') || {}).open;
  const reconstruida = siCambia(ficha, `${deEste}|${faltanDeEste}`, () => {
    if (!b) return null;
    const filas = [
      ['Número', `${b.numero}${i === 0 ? ' (génesis)' : ''}`],
      ['Hora', fechaUTC(b.timestamp)],
      ['Hash', hashLargo(b.hash)],
    ];
    if (i === 0) {
      filas.push(['Hash anterior', [h('span', { class: 'hash' }, `${String(b.hash_anterior).slice(0, 16)}…`), ' (no hay anterior)']]);
      const saldos = objeto(b.saldos_iniciales);
      const ids = Object.keys(saldos);
      filas.push(['Saldos iniciales', `${ids.length} nodos × ${fmt(num(saldos[ids[0]]))} monedas`]);
      filas.push(['Directorio', 'Claves públicas de todos los nodos (ver JSON)']);
    } else {
      const coincide = cadena[i - 1] && cadena[i - 1].hash === b.hash_anterior;
      filas.push(['Hash anterior', [hashLargo(b.hash_anterior), h('br'), coincide
        ? h('span', { class: 'voto-si' }, icono('check', 'ic-s'), `coincide con el hash del bloque ${i - 1}`)
        : h('span', { class: 'voto-no' }, icono('x', 'ic-s'), `no coincide con el bloque ${i - 1}`)]]);
      if (pow) filas.push(['Nonce', fmt(num(b.nonce))]);
      filas.push([pow ? 'Minero' : 'Proponente', String(b.proponente)]);
      const recompensa = objeto(b.recompensa);
      let estadoRecompensa = 'pagada al aceptarse';
      if (pow) {
        const faltan = num(b.numero) + conf - (cadena.length - 1);
        estadoRecompensa = faltan > 0 ? `pendiente: faltan ${faltan} ${plural(faltan, 'bloque', 'bloques')}` : 'acreditada (ya se puede gastar)';
      }
      filas.push(['Recompensa', `${fmt(num(recompensa.monto))} para ${recompensa.beneficiario} — ${estadoRecompensa}`]);
      const txs = lista(b.transacciones);
      const firmas = lista(b.firma);
      filas.push(['Transacciones', txs.length ? h('table', { class: 'mini' }, h('tbody', {}, txs.map((t, k) => h('tr', {},
        h('td', {}, h('b', {}, String(t.emisor)), ` → ${t.receptor}`),
        h('td', { class: 'num' }, fmt(num(t.monto))),
        h('td', { class: 'r' }, h('span', { class: 'hash', title: String(firmas[k] || '') }, `${String(firmas[k] || '').slice(0, 10)}…`), ' ',
          h('span', { class: 'voto-si' }, icono('check', 'ic-s'), 'firma válida')))))) : 'ninguna']);
      if (!pow) {
        const votos = lista(b.votos);
        const V = votos.filter((v) => v.voto === true).reduce((s, v) => s + num(v.peso), 0);
        const A = lista(b.validadores).reduce((s, v) => s + num(v.apuesta), 0);
        filas.push(['Votos', `${fmt(V)} de ${fmt(A)} a favor (${votos.length} votos firmados, intento ${num(b.intento) + 1})`]);
        filas.push(['Validadores', lista(b.validadores).map((v) => `${v.id} (${fmt(num(v.apuesta))})`).join(', ') || '—']);
        const castigos = lista(b.castigos);
        if (castigos.length) filas.push(['Castigos registrados', castigos.map((c) => `${c.nodo} −${fmt(num(c.monto))} (regla ${c.regla})`).join(', ')]);
      }
    }
    return [
      h('h3', {}, icono('block'), `Bloque ${b.numero} en la copia de ${nodoElegido}`),
      h('dl', {}, filas.map(([clave, valor]) => [h('dt', {}, clave), h('dd', {}, valor)])),
      h('details', {}, h('summary', {}, 'Ver JSON completo'), h('pre', {}, JSON.stringify(b, null, 2))),
    ];
  });
  if (reconstruida && jsonAbierto) ficha.querySelector('details').open = true;
  ficha._deEste = deEste;
}

/** Elige el nodo cuya cadena se muestra. */
function elegirNodo(id) {
  if (id === nodoElegido) return;
  nodoElegido = id;
  bloqueElegido = null;
  seguirUltimo = true;
  cadena = [];
  cargada = null;
  $('#sel-cadena').value = id;
  if (estado.S) actualizarTabla(estado.S);
  cargarSiHaceFalta();
}

/* ---------------------------------------------------------------- todo el paso */

export function actualizarPaso4(S, nuevos, { reinicio } = {}) {
  if (reinicio) {
    cadena = [];
    cargada = null;
    bloqueElegido = null;
    seguirUltimo = true;
  }
  actualizarTabla(S);
  sincronizarLista($('#sel-cadena'), nodosDe(S), (n) => n.id, (n) => h('option', { value: n.id }, n.id), () => {});
  if (!nodoPorId(S, nodoElegido) && nodosDe(S).length) {
    nodoElegido = nodosDe(S)[0].id;
    bloqueElegido = null;
    seguirUltimo = true;
  }
  if ($('#sel-cadena').value !== nodoElegido) $('#sel-cadena').value = nodoElegido;
  if (pasoVisible() === 4) {
    if (cadena.length) {
      const n = nodoPorId(S, nodoElegido);
      texto($('#cadena-rango'), `bloques 0 a ${cadena.length - 1}${n && !n.sincronizado ? ` · se quedó atrás (la red va en el ${num(S.altura_red)})` : ''}`);
    }
    cargarSiHaceFalta();
  }
}

/** Conecta los botones (una vez). */
export function iniciarPaso4() {
  alMostrarPaso(4, cargarSiHaceFalta);
  $('#sel-cadena').addEventListener('change', (e) => elegirNodo(e.target.value));
  $('#tira-bloques').addEventListener('click', (e) => {
    const boton = e.target.closest('[data-bloque]');
    if (!boton) return;
    bloqueElegido = Number(boton.dataset.bloque);
    seguirUltimo = bloqueElegido === cadena.length - 1;   // elegir el último vuelve a seguir los nuevos
    if (estado.S) dibujarCadena(estado.S);
  });
  $('#nodos-filas').addEventListener('click', async (e) => {
    const ver = e.target.closest('[data-ver]');
    if (ver) {
      elegirNodo(ver.dataset.ver);
      $('#card-cadena').scrollIntoView({ block: 'start', behavior: 'smooth' });
      return;
    }
    const boton = e.target.closest('[data-conexion]');
    if (!boton || !estado.S) return;
    const n = nodoPorId(estado.S, boton.dataset.conexion);
    if (!n) return;
    const r = await conBoton(boton, n.conectado ? 'Desconectando…' : 'Reconectando…',
      () => post(rutaNodo(n.id, '/conexion'), { conectado: !n.conectado }));
    if (!r) return;
    mostrarRespuesta($('#res-nodos'), r);
    await refrescar();
  });
  // Un chip de la tira de nodos (zona B) pide ver la cadena de ese nodo.
  document.addEventListener('ver-cadena', (e) => {
    elegirNodo(e.detail.nodo);
    irAPaso(4);
    $('#card-cadena').scrollIntoView({ block: 'start', behavior: 'smooth' });
  });
  mostrar($('#ficha'), true);
}
