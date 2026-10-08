/* ==========================================================================
   paso3_pow.js — Paso 3 en PoW «Minar un bloque» (docs/UX.md §4 y §6.1–6.2).

   El navegador NO mina: pide POST /api/pow/minar y el motor del servidor da
   las rondas. Aquí se dibuja lo que trae cada sondeo:
   - la carrera de mineros (nonce, intentos, último hash y sus ceros);
   - el aviso del ganador (o del empate, del bloque rechazado, de la minería
     agotada);
   - las recompensas por madurar con sus 6 puntitos de confirmación.
   ========================================================================== */

import { post } from './api.js';
import { estado, nodosDe, powActivo, refrescar } from './estado.js';
import { recompensasPendientes } from './ahora.js';
import { conBoton, limpiar, mostrarRespuesta, ocupado } from './ui.js';
import {
  $, cerosIniciales, cerosMeta, clases, fmt, h, hashCorto, icono, lista, listaY, mostrar, num, objeto, plural, propiedad, siCambia,
  sincronizarLista, texto,
} from './util.js';

/* ---------------------------------------------------------------- avisos */

/** Aviso grande (verde, rojo, ámbar o azul) con título y texto. */
function aviso(tipo, nombreIcono, titulo, ...contenido) {
  return h('div', { class: `aviso ${tipo}` }, icono(nombreIcono),
    h('div', {}, h('p', { class: 't' }, titulo), h('p', {}, ...contenido)));
}

/** El último bloque rechazado (de la carrera o del ataque «bloque tramposo») que sigue siendo actual. */
function ultimoRechazo(S) {
  const pow = objeto(S.pow);
  const ganador = objeto(pow.ultimo_ganador);
  const numeroActual = powActivo(S) ? num(pow.numero) : num(ganador.numero, num(S.altura_red));
  for (let i = estado.eventos.length - 1; i >= 0; i--) {
    const e = estado.eventos[i];
    if (e.tipo !== 'bloque_rechazado') continue;
    const datos = objeto(e.datos);
    if (num(datos.bloque, -1) >= numeroActual) return { nodo: datos.nodo, bloque: datos.bloque, motivo: datos.motivo, n: e.n };
    return null;
  }
  return null;
}

function actualizarAvisos(S) {
  const pow = objeto(S.pow);
  const config = objeto(S.config);
  const d = num(config.dificultad);
  const conf = num(S.confirmaciones, 6);
  const ganador = pow.ultimo_ganador && typeof pow.ultimo_ganador === 'object' ? pow.ultimo_ganador : null;
  const rechazo = ultimoRechazo(S);
  const activo = powActivo(S);
  const empatesEnBitacora = estado.eventos.filter((e) => e.tipo === 'empate').length;
  const firma = JSON.stringify([pow.estado === 'agotada' ? pow.mensaje : null, rechazo, ganador, activo,
    lista(pow.ultimo_empate).map((x) => [x.minero, x.hash]), pow.numero, empatesEnBitacora]);

  siCambia($('#pow-avisos'), firma, () => {
    const avisos = [];
    if (pow.estado === 'agotada') {
      avisos.push(aviso('warn', 'warn', 'No se encontró un hash válido', String(pow.mensaje || 'Se alcanzó el límite de rondas.')));
    }
    if (rechazo) {
      avisos.push(aviso('err', 'x', `El bloque de ${rechazo.nodo} se rechazó`,
        `${rechazo.nodo} encontró un hash válido para el bloque ${rechazo.bloque}, pero su bloque traía: «${rechazo.motivo}». `,
        `La red lo revisó, lo rechazó y nadie lo agregó.${activo ? ' La carrera sigue.' : ''}`));
    }
    if (ganador) {
      const restantes = Math.max(0, num(ganador.aceptaron) - 1);
      const faltaron = num(ganador.total) - num(ganador.aceptaron);
      avisos.push(aviso('ok', 'trophy', `${ganador.minero} ganó el bloque ${ganador.numero}`,
        `Encontró el nonce `, h('b', {}, fmt(num(ganador.nonce))), ` en la ronda ${fmt(num(ganador.ronda))}: hash `,
        hashCorto(ganador.hash, 14), ' (empieza con ', cerosMeta(d), ' ✓). ',
        `Los ${restantes} nodos restantes lo revisaron y lo agregaron${faltaron > 0 ? ` (${faltaron} ${plural(faltaron, 'desconectado no lo recibió', 'desconectados no lo recibieron')})` : ''}. `,
        `Recompensa: ${fmt(num(config.recompensa))} monedas, pendientes hasta el bloque ${num(ganador.numero) + conf}.`));
      const empatados = lista(ganador.empate);
      if (empatados.length > 1) {
        // Los hashes del empate: de la sesión, si es la del mismo bloque; si no, de la bitácora.
        const hashes = num(pow.numero) === num(ganador.numero)
          ? new Map(lista(pow.ultimo_empate).map((x) => [x.minero, x.hash])) : new Map();
        const evento = estado.eventos.slice().reverse()
          .find((e) => e.tipo === 'empate' && num(objeto(e.datos).bloque) === num(ganador.numero));
        let detalle;
        if (empatados.every((id) => hashes.has(id))) {
          const partes = empatados.map((id) => [id, ' (', hashCorto(hashes.get(id), 8), ')']);
          detalle = partes.flatMap((p, i) => (i === 0 ? p : [i === partes.length - 1 ? ' y ' : ', ', ...p]));
          detalle.push('. ');
        } else if (evento) {
          detalle = [`${evento.mensaje}. `];
        } else {
          detalle = [`${listaY(empatados)}. `];
        }
        avisos.push(aviso('info', 'trophy', `Empate en la misma ronda (${ganador.ronda})`,
          ...detalle, 'Gana el hash menor → ', h('b', {}, ganador.minero), '.'));
      }
    }
    return avisos;
  });
}

/* ---------------------------------------------------------------- carrera de mineros */

function actualizarCarrera(S) {
  const pow = objeto(S.pow);
  const d = num(objeto(S.config).dificultad);
  const activo = powActivo(S);
  const mineros = new Map(lista(pow.mineros).map((m) => [m.id, m]));
  const ganador = objeto(pow.ultimo_ganador);
  const nodos = nodosDe(S);
  const N = nodos.length;
  const conectados = nodos.filter((n) => n.conectado).length;

  let nota = `Cada minero prueba números distintos: N01 prueba 0, ${N}, ${2 * N}…; N02 prueba 1, ${N + 1}, ${2 * N + 1}…; así nadie repite trabajo.`;
  if (!mineros.size) nota += ` Pulsa «Minar 1 bloque»: los ${conectados} mineros empezarán a probar nonces.`;
  texto($('#pow-nonces'), nota);

  sincronizarLista($('#carrera-filas'), nodos, (n) => n.id,
    () => h('tr', {}, h('td', {}), h('td', { class: 'num' }), h('td', { class: 'num' }), h('td', {}), h('td', {}), h('td', {})),
    (fila, n) => {
      const m = mineros.get(n.id);
      const celdas = fila.children;
      const conectado = n.conectado && (!m || m.conectado !== false);
      const probo = m && num(m.intentos) > 0;
      const gano = !activo && m && ganador.minero === n.id && num(ganador.numero) === num(pow.numero);
      let clase = '';
      let estadoMinero;
      if (!conectado) {
        clase = 'apagado';
        estadoMinero = ['off', 'power', 'No mina (desconectado)'];
      } else if (gano) {
        clase = 'ganador';
        estadoMinero = ['gana', 'trophy', 'Ganó'];
      } else if (m && m.encontro) {
        clase = 'encontro';
        estadoMinero = ['enc', 'check', '¡Encontró!'];
      } else if (activo) {
        estadoMinero = ['', 'clock', 'probando…'];
      } else {
        estadoMinero = ['off', null, '—'];
      }
      clases(fila, clase);
      siCambia(celdas[0], `${n.id}|${n.deshonesto}`, () => [h('b', {}, n.id), n.deshonesto ? [' ', h('span', { class: 'badge err' }, 'Tramposo')] : null]);
      texto(celdas[1], probo ? fmt(num(m.nonce)) : '—');
      texto(celdas[2], probo ? fmt(num(m.intentos)) : '—');
      const hash = probo ? m.ultimo_hash : '';
      siCambia(celdas[3], hash, () => (hash ? hashCorto(hash, 12) : '—'));
      const z = hash ? Math.min(cerosIniciales(hash), d) : 0;
      siCambia(celdas[4], hash ? `${z}/${d}` : '', () => (hash
        ? h('span', { class: 'ceros', role: 'img', 'aria-label': `${z} de ${d} ceros` },
          Array.from({ length: d }, (_, i) => h('i', { class: i < z ? 'on' : '' })))
        : '—'));
      siCambia(celdas[5], estadoMinero.join('|'), () => h('span', { class: `estado-m ${estadoMinero[0]}` },
        estadoMinero[1] ? icono(estadoMinero[1], 'ic-s') : null, estadoMinero[2]));
    });
}

/* ---------------------------------------------------------------- recompensas por madurar */

/** Las 3 últimas recompensas acreditadas (salen de la bitácora). */
function ultimasAcreditadas() {
  const filas = [];
  for (let i = estado.eventos.length - 1; i >= 0 && filas.length < 3; i--) {
    const e = estado.eventos[i];
    if (e.tipo !== 'recompensa_acreditada') continue;
    const datos = objeto(e.datos);
    filas.push({ n: e.n, id: datos.nodo, bloque: datos.bloque, monto: datos.monto });
  }
  return filas;
}

function puntitos(llenos, total, etiqueta) {
  return h('span', { class: 'dots', role: 'img', 'aria-label': etiqueta },
    Array.from({ length: total }, (_, i) => h('i', { class: i < llenos ? 'on' : '' })));
}

function actualizarRecompensas(S) {
  const conf = num(S.confirmaciones, 6);
  texto($('#pow-recs-nota'), `Una recompensa se puede gastar cuando su bloque tiene ${conf} bloques encima (${conf} confirmaciones). Mientras tanto no cuenta en el saldo disponible.`);
  const pendientes = recompensasPendientes(S);
  const acreditadas = ultimasAcreditadas();
  const items = [
    ...pendientes.map((r) => ({ tipo: 'p', clave: `p|${r.id}|${r.bloque}`, ...r })),
    ...acreditadas.map((r) => ({ tipo: 'a', clave: `a|${r.n}`, ...r })),
  ];
  mostrar($('#pow-recs-vacio'), items.length === 0);
  sincronizarLista($('#pow-recs'), items, (r) => r.clave,
    (r) => h('li', { class: r.tipo === 'a' ? 'rec lista' : 'rec' }),
    (li, r) => {
      if (r.tipo === 'a') {
        siCambia(li, r.clave, () => [
          h('span', { class: 'q' }, icono('check', 'ic-s'), ' ', h('b', {}, String(r.id)),
            ` · ${fmt(num(r.monto))} monedas · bloque ${num(r.bloque)}`),
          puntitos(conf, conf, `${conf} de ${conf} confirmaciones`),
          h('span', { class: 'f' }, 'Acreditada: ya se puede gastar'),
        ]);
        return;
      }
      const llenos = Math.max(0, Math.min(conf, conf - r.faltan));
      const cuando = r.faltan <= 1 ? 'Madura en el siguiente bloque' : `Faltan ${r.faltan} bloques`;
      siCambia(li, `${r.faltan}|${r.monto}`, () => [
        h('span', { class: 'q' }, h('b', {}, r.id), ` · ${fmt(r.monto)} monedas · bloque ${r.bloque}`),
        puntitos(llenos, conf, `${llenos} de ${conf} confirmaciones`),
        h('span', { class: 'f' }, `${cuando} · ${llenos} de ${conf} confirmaciones`),
      ]);
    });

  // Botón «Minar f bloques más» (f = lo que le falta a la primera en madurar).
  const zonaBoton = $('#pow-madurar');
  const boton = zonaBoton.querySelector('button');
  if (ocupado(boton)) return;
  const primera = pendientes[0];
  const f = primera ? Math.max(1, primera.faltan) : 0;
  siCambia(zonaBoton, primera && !powActivo(S) ? `${f}|${primera.id}` : '', () => (primera && !powActivo(S)
    ? h('button', { type: 'button', class: 'btn', 'data-bloques': f }, icono('pick'),
      `Minar ${f} ${plural(f, 'bloque', 'bloques')} más para ver madurar la de ${primera.id}`)
    : null));
}

/* ---------------------------------------------------------------- todo el paso */

export function actualizarPow(S) {
  if (S.modo !== 'pow') return;
  const config = objeto(S.config);
  const pow = objeto(S.pow);
  const d = num(config.dificultad);
  const activo = powActivo(S);
  const p = lista(S.pendientes).length;
  const maxTx = num(config.max_tx_por_bloque);
  const conf = num(S.confirmaciones, 6);

  siCambia($('#quees-pow'), `${d}|${config.recompensa}|${conf}`, () => [
    'Todos los nodos compiten: cada uno prueba números (nonce) hasta que el hash del bloque empiece con ', cerosMeta(d),
    `. El primero que lo logra agrega el bloque y gana ${fmt(num(config.recompensa))} monedas, que podrá gastar cuando haya ${conf} bloques más encima.`,
  ]);

  const detener = $('#b-detener');
  if (!ocupado(detener)) propiedad(detener, 'disabled', !activo);

  let pista;
  if (activo) pista = 'Mientras se mina puedes detener la carrera; pedir otra minería será rechazado (409).';
  else if (p) pista = `El bloque llevará ${Math.min(p, maxTx)} de las ${p} transacciones en espera. «Minar 10 seguidos»: si faltan transacciones, se crean al azar.`;
  else pista = 'No hay transacciones en espera: el servidor rechazará «Minar 1 bloque». «Minar 10 seguidos» las crea al azar.';
  texto($('#pow-pista'), pista);

  // Tras «Detener» (o si se agotó) se sigue viendo el bloque y la ronda en que se quedó.
  const detenida = !activo && (pow.estado === 'cancelada' || pow.estado === 'agotada') && num(pow.numero) === num(S.altura_red) + 1;
  const numero = activo || detenida ? num(pow.numero) : num(S.altura_red) + 1;   // el bloque que se mina (o el siguiente)
  const ronda = activo || detenida ? num(pow.ronda) : 0;
  const maxRondas = num(pow.max_rondas, num(config.max_rondas));
  const restantes = num(pow.bloques_restantes);
  const comoQuedo = detenida ? (pow.estado === 'cancelada' ? 'se detuvo en la ronda ' : 'se agotó en la ronda ') : 'ronda ';
  siCambia($('#pow-meta'), JSON.stringify([numero, ronda, comoQuedo, maxRondas, config.intentos_por_ronda, d, activo, restantes, pow.intentos_totales]), () => [
    'Bloque ', h('b', {}, String(numero)),
    ` · ${comoQuedo}`, h('b', {}, fmt(ronda)), ` de ${fmt(maxRondas)}`,
    ' · cada minero prueba ', h('b', {}, fmt(num(config.intentos_por_ronda))), ' nonces por ronda',
    ' · meta: el hash debe empezar con ', cerosMeta(d),
    activo && restantes > 1 ? [' · faltan ', h('b', {}, String(restantes)), ' bloques'] : null,
    activo && Number.isFinite(pow.intentos_totales) ? ` · ${fmt(pow.intentos_totales)} hashes calculados` : null,
  ]);

  actualizarAvisos(S);
  actualizarCarrera(S);
  actualizarRecompensas(S);
}

/** Conecta los botones (una vez). */
export function iniciarPow() {
  const zona = $('#res-pow');
  async function minar(boton, cuerpo) {
    const r = await conBoton(boton, 'Pidiendo…', () => post('/api/pow/minar', cuerpo));
    if (!r) return;
    if (r.ok) limpiar(zona);
    else mostrarRespuesta(zona, r);
    await refrescar();
  }
  $('#b-minar1').addEventListener('click', (e) => minar(e.currentTarget, {}));
  $('#b-minar10').addEventListener('click', (e) => minar(e.currentTarget, { bloques: 10, auto_tx: true }));
  $('#b-detener').addEventListener('click', async (e) => {
    const r = await conBoton(e.currentTarget, 'Deteniendo…', () => post('/api/pow/cancelar', {}));
    if (!r) return;
    mostrarRespuesta(zona, r);
    await refrescar();
  });
  $('#pow-madurar').addEventListener('click', async (e) => {
    const boton = e.target.closest('[data-bloques]');
    if (!boton) return;
    const r = await conBoton(boton, 'Minando…', () => post('/api/pow/minar', { bloques: Number(boton.dataset.bloques), auto_tx: true }));
    if (!r) return;
    if (r.ok) limpiar($('#res-madurar'));
    else mostrarRespuesta($('#res-madurar'), r);
    await refrescar();
  });
}
