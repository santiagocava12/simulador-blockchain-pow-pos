/* ==========================================================================
   paso3_pos.js — Paso 3 en PoS «Votar un bloque» (docs/UX.md §4, §6.3–6.5).

   La ronda es una máquina de estados que vive en el servidor:
     APUESTAS → SORTEO → CANDIDATO → VOTACION → ACEPTADO | RECHAZADO
   Un solo botón dice la siguiente transición («Hacer el sorteo →»…) y llama
   POST /api/pos/avanzar con el estado, bloque e intento que se ven aquí.
   Se dibujan: la línea de pasos, la rifa del sorteo (boletos = apuesta), la
   barra de votos con la marca de 2/3, la tabla de validadores y el historial.
   ========================================================================== */

import { post } from './api.js';
import { estado, nodoPorId, posActiva, refrescar } from './estado.js';
import { botonRonda, ejecutarBotonRonda, enJuego, registrarAntesDelSorteo } from './pos_comun.js';
import {
  caducar, confirmarEnLinea, conBoton, conVigencia, fijarVigencia, limpiar, mostrarRespuesta, ocupado, resultado,
} from './ui.js';
import {
  $, $$, atributo, clases, fmt, h, icono, lista, mostrar, num, objeto, pct, propiedad, siCambia, sincronizarLista, texto,
  umbral,
} from './util.js';

/**
 * «Llave» del paso de la ronda: los mensajes del paso 3 (un rechazo, «Apuestas
 * guardadas»…) se borran solos cuando cambia (otro estado, intento o bloque).
 */
const llaveRonda = (S) => {
  const pos = objeto(S && S.pos);
  return `${S && S.id_simulacion}|${pos.numero}|${pos.intento}|${pos.estado}`;
};

/** Índice del estado en la línea de pasos (null = sin ronda). */
const INDICE = { APUESTAS: 0, SORTEO: 1, CANDIDATO: 2, VOTACION: 3, ACEPTADO: 4, RECHAZADO: 4, SIN_VALIDADORES: 4 };
const CON_PROPONENTE = ['SORTEO', 'CANDIDATO', 'VOTACION', 'ACEPTADO'];

/** Último intento terminado de la ronda (del historial), o null. */
function ultimoIntento(pos) {
  const historial = lista(pos.historial);
  return historial.length ? objeto(historial[historial.length - 1]) : null;
}

/* ---------------------------------------------------------------- cabecera, línea y botones */

function actualizarCabecera(S) {
  const pos = objeto(S.pos);
  const config = objeto(S.config);
  const hayRonda = Number.isFinite(pos.numero);
  siCambia($('#pos-meta'), JSON.stringify([pos.numero, pos.intento, enJuego(pos).length, pos.estado, pos.automatico, S.altura_red]), () => (hayRonda
    ? ['Bloque ', h('b', {}, String(pos.numero)), ' · intento ', h('b', {}, String(num(pos.intento) + 1)),
      ` · ${enJuego(pos).length} validadores en juego`,
      pos.estado === 'CANCELADA' ? [' · ', h('b', {}, 'ronda cancelada')] : null,
      pos.automatico && posActiva(S) ? ' · avanza sola' : null]
    : ['Sin ronda todavía · siguiente bloque: ', h('b', {}, String(num(S.altura_red) + 1))]));

  // Línea de pasos ① Apuestas — ② Sorteo — ③ Bloque propuesto — ④ Votación — ⑤ Resultado.
  const indice = INDICE[pos.estado] ?? null;
  const final = pos.estado === 'ACEPTADO' ? ['bien', 'Aceptado ✓', 'check']
    : pos.estado === 'RECHAZADO' ? ['mal', 'Rechazado ✗', 'x']
      : pos.estado === 'SIN_VALIDADORES' ? ['mal', 'Sin validadores', 'x'] : ['', 'Resultado', null];
  for (const li of $$('#pos-linea li')) {
    const i = Number(li.dataset.i);
    let clase = '';
    let contenido = String(i + 1);
    if (indice !== null && i < indice) {
      clase = 'hecho';
      contenido = 'check';
    }
    if (indice === i) clase = i === 4 && final[0] ? final[0] : 'actual';
    if (i === 4 && indice === 4 && final[2]) contenido = final[2];
    clases(li, clase);
    if (indice === i) li.setAttribute('aria-current', 'step');
    else li.removeAttribute('aria-current');
    siCambia(li.querySelector('.c'), contenido, () => (/^\d$/.test(contenido) ? contenido : icono(contenido, 'ic-s')));
    if (i === 4) texto(li.querySelector('.lt'), final[1]);
  }
  const vuelta = $('#pos-vuelta');
  mostrar(vuelta, pos.estado === 'RECHAZADO');
  siCambia(vuelta, pos.estado === 'RECHAZADO' ? `${pos.proponente}|${pos.intento}` : '', () => (pos.estado === 'RECHAZADO'
    ? [icono('loop'), `Siguiente: nuevo sorteo sin ${pos.proponente} (intento ${num(pos.intento) + 2}).`] : null));

  // Botón de la transición, interruptor «Avanzar solo» y «Cancelar ronda».
  const b = botonRonda(S);
  const boton = $('#b-pos');
  if (!ocupado(boton)) siCambia(boton, b.texto, () => [icono(b.icono), h('span', {}, b.texto)]);
  texto($('#pos-pista'), b.pista);
  const auto = $('#b-auto');
  if (!auto.dataset.enviando) propiedad(auto, 'checked', !!pos.automatico && posActiva(S));   // no se pisa mientras se envía
  propiedad(auto, 'disabled', !posActiva(S) || !!auto.dataset.enviando);
  const cancelar = $('#b-cancelar-ronda');
  if (!ocupado(cancelar)) propiedad(cancelar, 'disabled', !posActiva(S));
  texto($('#pos-castigo'), config.regla_castigo === 'B'
    ? `Castigo: regla B (pierde ${num(config.alfa_porcentaje)} % del valor de las transacciones del bloque, sin pasar de su apuesta) — se cambia en Configurar › Opciones avanzadas.`
    : 'Castigo: regla A (pierde toda su apuesta) — se cambia en Configurar › Opciones avanzadas.');
}

/* ---------------------------------------------------------------- aviso del resultado */

function actualizarAviso(S) {
  const pos = objeto(S.pos);
  const R = num(objeto(S.config).recompensa);
  const ultimo = ultimoIntento(pos);
  const firma = JSON.stringify([pos.estado, pos.numero, pos.resultado, ultimo, pos.mensaje]);
  siCambia($('#pos-aviso'), firma, () => {
    const caja = (tipo, nombreIcono, titulo, ...partes) => h('div', { class: `aviso ${tipo}` }, icono(nombreIcono),
      h('div', {}, h('p', { class: 't' }, titulo), h('p', {}, ...partes)));
    if (pos.estado === 'ACEPTADO') {
      const r = objeto(pos.resultado);
      const A = num(r.A, num(pos.A));
      const V = num(r.V_favor, num(pos.V_favor));
      const exacto = 3 * V === 2 * A;
      return caja('ok', 'check', `Bloque ${num(r.numero, num(pos.numero))} aceptado`,
        `${fmt(V)} de ${fmt(A)} a favor (se necesitaban ${fmt(umbral(A))})${exacto ? ' — exactamente 2/3' : ''}. `,
        `${r.proponente || pos.proponente} recibe ${fmt(R)} monedas al momento.`);
    }
    if ((pos.estado === 'RECHAZADO' || pos.estado === 'SIN_VALIDADORES') && ultimo && ultimo.resultado === 'RECHAZADO') {
      const P = ultimo.proponente;
      const A = num(ultimo.A);
      const avisos = [caja('err', 'x', `El bloque de ${P} fue rechazado`,
        `${fmt(num(ultimo.V_favor))} de ${fmt(A)} a favor (se necesitaban ${fmt(umbral(A))}). `,
        `Motivo que vieron los validadores: «${ultimo.motivo}». `,
        `${P} pierde ${fmt(num(ultimo.castigo))} monedas (se queman). `,
        pos.estado === 'RECHAZADO' ? `Sigue un nuevo sorteo sin ${P}.` : 'No queda nadie para otro sorteo.')];
      if (pos.estado === 'SIN_VALIDADORES') {
        avisos.push(caja('err', 'warn', 'La ronda terminó sin bloque', 'No quedan validadores con saldo. Crea una red nueva para seguir.'));
      }
      return avisos;
    }
    if (pos.estado === 'CANCELADA') return caja('info', 'info', 'Ronda cancelada', String(pos.mensaje || 'Las apuestas se liberaron.'));
    return null;
  });
}

/* ---------------------------------------------------------------- sorteo (rifa proporcional, §6.3) */

function actualizarSorteo(S) {
  const pos = objeto(S.pos);
  const vs = enJuego(pos);
  const excluidos = lista(pos.excluidos);
  const firma = JSON.stringify([pos.estado, pos.numero, pos.intento, pos.proponente, pos.boleto,
    vs.map((v) => [v.id, v.apuesta]), excluidos.map((x) => [x.id, x.castigo])]);
  siCambia($('#pos-sorteo'), firma, () => {
    if (!Number.isFinite(pos.numero)) {
      return h('p', { class: 'vacio' }, 'Aún no hay ronda. Pulsa «Iniciar ronda» para ver cómo se reparten los boletos.');
    }
    const fuera = excluidos.length
      ? h('p', { class: 'nota', style: 'margin:8px 0 0' }, icono('scale', 'ic-s'), ' Fuera de la ronda: ',
        excluidos.map((x) => `${x.id} (castigado −${fmt(num(x.castigo))})`).join(', '))
      : null;
    const A = vs.reduce((s, v) => s + num(v.apuesta), 0);
    if (!vs.length || A <= 0) return [h('p', { class: 'vacio' }, 'No quedan validadores en juego.'), fuera];

    const sorteado = CON_PROPONENTE.includes(pos.estado) && vs.some((v) => v.id === pos.proponente);
    let acumulado = 0;
    let desde = 0;
    let hasta = 0;
    const segmentos = vs.map((v) => {
      const ancho = (num(v.apuesta) / A) * 100;
      const gana = sorteado && v.id === pos.proponente;
      if (gana) {
        desde = acumulado;
        hasta = acumulado + num(v.apuesta) - 1;
      }
      acumulado += num(v.apuesta);
      const seg = h('div', { class: `seg${gana ? ' gana' : ''}`, title: `${v.id}: ${fmt(num(v.apuesta))} boletos (${pct(num(v.apuesta) / A)})` },
        ancho >= 6 ? [String(v.id), h('small', {}, fmt(num(v.apuesta)))] : null);
      seg.style.width = `${ancho}%`;
      return seg;
    });
    const tieneBoleto = sorteado && Number.isFinite(pos.boleto);
    let flecha = null;
    if (sorteado) {
      const posicion = tieneBoleto ? ((pos.boleto + 0.5) / A) * 100 : ((desde + hasta + 1) / 2 / A) * 100;
      flecha = h('div', { class: 'flecha' }, tieneBoleto ? `boleto ${pos.boleto}` : `ganó ${pos.proponente}`,
        h('span', { 'aria-hidden': 'true' }, '▼'));
      flecha.style.left = `${posicion}%`;
    }
    const barra = h('div', { class: 'barra', role: 'img', 'aria-label': `Boletos por validador: ${vs.map((v) => `${v.id} ${v.apuesta}`).join(', ')}${sorteado ? `; ganó ${pos.proponente}` : ''}` }, segmentos);
    let explicacion;
    if (tieneBoleto) {
      explicacion = ['Boleto ', h('b', {}, String(pos.boleto)), ` de ${fmt(A)} → `, h('b', {}, String(pos.proponente)),
        ` (boletos ${desde}–${hasta}). El sorteo es público: cualquiera puede repetirlo con el hash del bloque anterior.`];
    } else if (sorteado) {
      explicacion = ['Ganó el sorteo ', h('b', {}, String(pos.proponente)), ` (boletos ${desde}–${hasta} de ${fmt(A)}). El sorteo es público: cualquiera puede repetirlo con el hash del bloque anterior.`];
    } else if (pos.estado === 'RECHAZADO') {
      explicacion = [`Nuevo sorteo pendiente: así quedan los ${fmt(A)} boletos sin ${pos.proponente}.`];
    } else if (pos.estado === 'CANCELADA') {
      explicacion = [`La ronda se canceló; así estaban repartidos los ${fmt(A)} boletos.`];
    } else {
      explicacion = [`Aún no se sortea: así se reparten los ${fmt(A)} boletos (uno por moneda apostada).`];
    }
    return [h('div', { class: 'rifa' }, flecha, barra), h('p', { class: 'nota', style: 'margin:0' }, ...explicacion), fuera];
  });
}

/* ---------------------------------------------------------------- votación (barra con 2/3, §6.4) */

function actualizarVotos(S) {
  const pos = objeto(S.pos);
  let V = num(pos.V_favor);
  let C = num(pos.V_contra);
  let A = num(pos.A);
  if (pos.estado === 'SIN_VALIDADORES') {
    const ultimo = ultimoIntento(pos);
    if (ultimo) {
      V = num(ultimo.V_favor);
      C = num(ultimo.V_contra);
      A = num(ultimo.A);
    }
  }
  const mostrarBarra = ['SORTEO', 'CANDIDATO', 'VOTACION', 'ACEPTADO', 'RECHAZADO', 'SIN_VALIDADORES'].includes(pos.estado) && A > 0;
  siCambia($('#pos-votos'), JSON.stringify([mostrarBarra, V, C, A]), () => {
    if (!mostrarBarra) return h('p', { class: 'vacio' }, 'Aún no hay votos. Primero se hace el sorteo y el proponente arma el bloque.');
    const u = umbral(A);
    const marca = h('div', { class: 'umbral' }, h('span', {}, `2/3 = ${fmt(u)}`));
    marca.style.left = `${200 / 3}%`;
    const favor = h('div', { class: 'fav' });
    favor.style.width = `${(V / A) * 100}%`;
    const contra = h('div', { class: 'con' });
    contra.style.width = `${(C / A) * 100}%`;
    const barra = h('div', { class: 'votos' }, marca,
      h('div', { class: 'pista-v', role: 'img', 'aria-label': `${V} a favor y ${C} en contra de ${A}; se necesitan ${u}` }, favor, contra));
    const muestra = (estilo) => {
      const i = h('i');
      i.style.cssText = estilo;
      return i;
    };
    const cuenta = h('p', { class: 'cuenta' },
      h('span', {}, muestra('background:var(--ok)'), 'A favor ', h('b', {}, fmt(V))),
      h('span', {}, muestra('background:repeating-linear-gradient(45deg,var(--err) 0 3px,var(--err-soft) 3px 5px)'), 'En contra ', h('b', {}, fmt(C))),
      h('span', {}, muestra('background:var(--surface-3);border:1px solid var(--border)'), 'Sin votar ', h('b', {}, fmt(Math.max(0, A - V - C)))),
      h('span', {}, 'Total apostado ', h('b', {}, fmt(A))));
    let regla;
    if (V + C > 0) {
      const alcanza = 3 * V >= 2 * A;
      const exacto = 3 * V === 2 * A;
      const signo = exacto ? '=' : alcanza ? '≥' : '<';
      const fin = exacto ? 'exactamente 2/3: se acepta (la regla es «al menos 2/3»)' : alcanza ? 'se acepta ✓' : 'no alcanza ✗';
      regla = h('p', { class: `regla ${alcanza ? 'si' : 'no'}` },
        `3 × ${fmt(V)} = ${fmt(3 * V)} ${signo} 2 × ${fmt(A)} = ${fmt(2 * A)} → ${fin}`);
    } else {
      regla = h('p', { class: 'nota', style: 'margin:8px 0 0' }, 'Todavía nadie vota. La marca punteada es lo mínimo para aceptar.');
    }
    return [barra, cuenta, regla];
  });
}

/* ---------------------------------------------------------------- validadores (tabla con apuestas) */

let rondaDeLasApuestas = null;   // si cambia la ronda, se olvida lo escrito en las apuestas

function actualizarValidadores(S) {
  const pos = objeto(S.pos);
  const filas = lista(pos.validadores).slice().sort((a, b) => String(a.id).localeCompare(String(b.id)));
  const enApuestas = pos.estado === 'APUESTAS';
  const llaveRonda = `${pos.numero}|${S.id_simulacion}`;
  if (rondaDeLasApuestas !== llaveRonda) {
    rondaDeLasApuestas = llaveRonda;
    for (const entrada of $$('#val-filas [data-apuesta]')) delete entrada.dataset.editado;
  }
  mostrar($('#val-tabla'), filas.length > 0);
  mostrar($('#val-vacio'), filas.length === 0);
  mostrar($('#val-guardar'), enApuestas && filas.length > 0);
  const excluidos = new Map(lista(pos.excluidos).map((x) => [x.id, x]));

  sincronizarLista($('#val-filas'), filas, (v) => v.id,
    () => h('tr', {}, h('td', {}), h('td', { class: 'num' }), h('td', { class: 'prob' }), h('td', {})),
    (fila, v) => {
      const celdas = fila.children;
      const nodo = nodoPorId(S, v.id) || {};
      const excluido = !!v.excluido;
      clases(fila, excluido ? 'excluido' : '');
      const propone = !excluido && v.id === pos.proponente && CON_PROPONENTE.includes(pos.estado);
      let marcaTrampa = null;
      if (nodo.deshonesto) marcaTrampa = nodo.trampa === 'voto_invertido' ? 'Vota al revés' : 'Tramposo';
      siCambia(celdas[0], JSON.stringify([v.id, propone, marcaTrampa, nodo.conectado]), () => [
        h('b', {}, String(v.id)),
        propone ? [' ', h('span', { class: 'badge pos' }, icono('star', 'ic-s'), 'Propone')] : null,
        marcaTrampa ? [' ', h('span', { class: 'badge err' }, marcaTrampa)] : null,
        nodo.conectado === false ? [' ', h('span', { class: 'badge' }, icono('power', 'ic-s'), 'Desconectado')] : null,
      ]);
      // Apuesta: campo editable sólo en APUESTAS (y sólo para los que siguen en juego).
      const editable = enApuestas && !excluido;
      const maximo = num(nodo.disponible) + num(nodo.apuesta_bloqueada);
      siCambia(celdas[1], editable ? `in|${v.id}` : `tx|${v.apuesta}`, () => (editable
        ? h('input', { class: 'apuesta-in', 'data-apuesta': v.id, inputmode: 'numeric', autocomplete: 'off', value: String(num(v.apuesta)) })
        : fmt(num(v.apuesta))));
      if (editable) {
        const entrada = celdas[1].firstElementChild;
        atributo(entrada, 'aria-label', `Apuesta de ${v.id} (máximo ${maximo})`);
        // No se pisa lo que el usuario está escribiendo.
        if (document.activeElement !== entrada && !entrada.dataset.editado && entrada.value !== String(num(v.apuesta))) {
          entrada.value = String(num(v.apuesta));
        }
      }
      if (excluido) {
        const x = excluidos.get(v.id) || {};
        siCambia(celdas[2], `x|${x.castigo}`, () => h('span', { class: 'badge err' }, icono('scale', 'ic-s'), `Castigado −${fmt(num(x.castigo))}`));
      } else {
        const p = num(v.probabilidad);
        siCambia(celdas[2], `p|${p}`, () => {
          const barra = h('i');
          barra.style.width = `${p * 100}%`;
          return [h('span', { class: 'probbar', 'aria-hidden': 'true' }, barra), pct(p)];
        });
      }
      const voto = v.voto === true ? 'si' : v.voto === false ? 'no' : '';
      siCambia(celdas[3], voto, () => (voto === 'si' ? h('span', { class: 'voto-si' }, icono('check', 'ic-s'), 'Sí')
        : voto === 'no' ? h('span', { class: 'voto-no' }, icono('x', 'ic-s'), 'No')
          : h('span', { class: 'estado-m off' }, '—')));
    });
}

/* ---------------------------------------------------------------- historial de la ronda */

function actualizarHistorial(S) {
  const pos = objeto(S.pos);
  const historial = lista(pos.historial);
  siCambia($('#pos-hist'), JSON.stringify([pos.numero, historial]), () => (historial.length
    ? h('ul', { class: 'hist' }, historial.map((x) => h('li', {},
      h('b', {}, `Intento ${num(x.intento) + 1}`),
      h('span', {}, `${x.proponente} propuso`),
      h('span', {}, `${fmt(num(x.V_favor))} de ${fmt(num(x.A))} a favor`),
      x.resultado === 'ACEPTADO'
        ? h('span', { class: 'badge ok' }, icono('check', 'ic-s'), 'Aceptado')
        : [h('span', { class: 'badge err' }, icono('x', 'ic-s'), 'Rechazado'),
          h('span', { class: 'badge err' }, icono('scale', 'ic-s'), `${x.proponente} −${fmt(num(x.castigo))}`)])))
    : h('p', { class: 'vacio' }, 'Aquí aparece cada intento de la ronda: quién propuso, cuántos votos tuvo y si hubo castigo.')));
}

/* ---------------------------------------------------------------- todo el paso */

export function actualizarPos(S) {
  if (S.modo !== 'pos') return;
  caducar($('#res-pos'));
  caducar($('#res-apuestas'));
  actualizarCabecera(S);
  actualizarAviso(S);
  actualizarSorteo(S);
  actualizarVotos(S);
  actualizarValidadores(S);
  actualizarHistorial(S);
}

/* ---------------------------------------------------------------- apuestas escritas */

/** Lo escrito en las casillas: { N01: 12, … } (lo que no es un entero va tal cual, para que el servidor lo rechace). */
function leerApuestas() {
  const apuestas = {};
  for (const entrada of $$('#val-filas [data-apuesta]')) {
    const valor = entrada.value.trim();
    apuestas[entrada.dataset.apuesta] = /^\d{1,15}$/.test(valor) ? Number(valor) : valor;
    entrada.removeAttribute('aria-invalid');
  }
  return apuestas;
}

/** POST /api/pos/apuestas con lo escrito. Si se rechaza, marca la casilla y muestra el error junto a la tabla. */
async function enviarApuestas() {
  const apuestas = leerApuestas();
  if (!Object.keys(apuestas).length) return null;
  const r = await post('/api/pos/apuestas', { apuestas });
  if (r.ok) {
    for (const entrada of $$('#val-filas [data-apuesta]')) delete entrada.dataset.editado;
    limpiar($('#res-apuestas'));
  } else {
    const m = /(N\d{2})/.exec(r.error);
    const entrada = m ? $(`#val-filas [data-apuesta="${m[1]}"]`) : null;
    if (entrada) {
      entrada.setAttribute('aria-invalid', 'true');
      entrada.focus();
    }
    mostrarRespuesta($('#res-apuestas'), r);
  }
  return r;
}

/* ---------------------------------------------------------------- botones */

/** Conecta los botones (una vez). */
export function iniciarPos() {
  const zona = $('#res-pos');
  conVigencia(zona, () => llaveRonda(estado.S));
  conVigencia($('#res-apuestas'), () => llaveRonda(estado.S));
  // «Hacer el sorteo →» (aquí o en «Ahora») guarda antes lo que se escribió en las apuestas.
  registrarAntesDelSorteo(() => ($$('#val-filas [data-apuesta][data-editado]').length ? enviarApuestas() : null));

  $('#b-pos').addEventListener('click', async (e) => {
    if (!estado.S) return;
    await ejecutarBotonRonda(estado.S, zona, e.currentTarget);
    fijarVigencia(zona);
    fijarVigencia($('#res-apuestas'));   // un rechazo de las apuestas escritas se muestra ahí
  });

  $('#b-auto').addEventListener('change', async (e) => {
    const interruptor = e.currentTarget;
    const activo = interruptor.checked;
    interruptor.dataset.enviando = '1';
    interruptor.disabled = true;
    const r = await post('/api/pos/automatico', { activo });
    delete interruptor.dataset.enviando;
    interruptor.disabled = false;
    if (r.ok) limpiar(zona);
    else {
      interruptor.checked = !activo;
      mostrarRespuesta(zona, r);
    }
    await refrescar();
    fijarVigencia(zona);
    interruptor.focus();
  });

  $('#b-cancelar-ronda').addEventListener('click', async (e) => {
    const boton = e.currentTarget;
    const si = await confirmarEnLinea($('#conf-ronda'), 'Se liberan las apuestas; los castigos ya hechos se quedan. ¿Cancelar la ronda?',
      'Sí, cancelar la ronda', boton, 'No, seguir');
    if (!si) return;
    const r = await conBoton(boton, 'Cancelando…', () => post('/api/pos/cancelar', {}));
    if (!r) return;
    mostrarRespuesta(zona, r);
    await refrescar();
    fijarVigencia(zona);
    // Sin ronda, «Cancelar ronda» queda desactivado y el foco se perdería: pasa al botón de la ronda.
    if (boton.disabled && (document.activeElement === boton || document.activeElement === document.body)) $('#b-pos').focus();
  });

  // Lo que se escribe en una apuesta queda marcado para que el sondeo no lo borre.
  $('#val-filas').addEventListener('input', (e) => {
    const entrada = e.target.closest('[data-apuesta]');
    if (!entrada) return;
    entrada.dataset.editado = '1';
    entrada.removeAttribute('aria-invalid');
  });

  $('#b-guardar-apuestas').addEventListener('click', async (e) => {
    const zonaApuestas = $('#res-apuestas');
    const r = await conBoton(e.currentTarget, 'Guardando…', enviarApuestas);
    if (!r) return;
    if (r.ok) resultado(zonaApuestas, { tipo: 'ok', titulo: 'Apuestas guardadas.', texto: 'La barra del sorteo ya muestra los boletos nuevos.' });
    await refrescar();
    fijarVigencia(zonaApuestas);   // se borra al pasar al sorteo, al cancelar o en otra ronda
  });
}
