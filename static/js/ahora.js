/* ==========================================================================
   ahora.js — Zona B «Ahora»: qué pasa, qué sigue y cómo están los nodos.

   1) La frase de estado y el botón del siguiente paso salen de la
      instantánea con estas reglas, evaluadas EN ORDEN: gana la
      primera que se cumple.
   2) La tira de nodos (un chip por nodo, siempre visible).
   3) Los chips de resumen: bloque, en espera, sincronización y dinero.
   ========================================================================== */

import { post, rutaNodo } from './api.js';
import { estado, nodosDe, powActivo, posActiva, refrescar } from './estado.js';
import { irABitacora, irAPaso } from './navegacion.js';
import { botonRonda, descripcionPos, ejecutarBotonRonda, enJuego } from './pos_comun.js';
import { caducar, conBoton, conVigencia, fijarVigencia, limpiar, mostrarRespuesta, ocupado } from './ui.js';
import { $, atributo, clases, fmt, h, icono, listaY, lista, num, objeto, plural, siCambia, sincronizarLista, texto } from './util.js';

const TONOS = {
  ok: ['check', 'Todo en orden'],
  run: ['clock', 'En curso'],
  warn: ['warn', 'Atención'],
  err: ['x', 'Problema'],
};

/* ---------------------------------------------------------------- reglas de la barra «Ahora» */

/**
 * Calcula { clave, tono, frase, cta, sec } con la instantánea S.
 * cta = { texto, icono, accion } es el botón primario; sec, uno secundario.
 */
export function calcularAhora(S, conectado) {
  // Regla 0: el sondeo falla.
  if (!conectado) {
    return { clave: 'r0', tono: 'warn', frase: 'Sin conexión con el servidor; reintentando…', cta: null };
  }
  const config = objeto(S.config);
  const nodos = nodosDe(S);
  const N = num(config.num_nodos, nodos.length);
  const altura = num(S.altura_red);
  const p = lista(S.pendientes).length;
  const pow = objeto(S.pow);
  const pos = objeto(S.pos);

  // Regla 1: algo no cuadra (invariantes).
  if (S.invariantes_ok === false) {
    return { clave: 'r1', tono: 'err', frase: 'Algo no cuadra en los saldos o en las cadenas. Revisa la bitácora.',
      cta: { texto: 'Ver bitácora', icono: 'list', accion: 'bitacora' } };
  }
  // Regla 2: se está minando.
  if (powActivo(S)) {
    const c = nodos.filter((n) => n.conectado).length;
    let frase = `Minando el bloque ${num(pow.numero)}: ronda ${fmt(num(pow.ronda))} de ${fmt(num(pow.max_rondas))}. ${c} ${plural(c, 'minero compite', 'mineros compiten')}.`;
    if (num(pow.bloques_restantes) > 1) frase += ` Faltan ${num(pow.bloques_restantes)} bloques.`;
    return { clave: 'r2', tono: 'run', frase,
      cta: { texto: 'Ver la carrera', icono: 'pick', accion: 'paso3' },
      sec: { texto: 'Detener', icono: 'stop', accion: 'detener' } };
  }
  // Regla 3: la minería se agotó sin encontrar un hash.
  if (S.modo === 'pow' && pow.estado === 'agotada') {
    return { clave: 'r3', tono: 'warn',
      frase: `No se encontró un hash válido en ${fmt(num(pow.max_rondas))} rondas. Baja la dificultad o sube el límite.`,
      cta: { texto: 'Configurar la red', icono: 'sliders', accion: 'paso1' } };
  }
  // Regla 4: ronda PoS en curso.
  if (posActiva(S)) {
    const b = botonRonda(S);
    return { clave: `r4:${pos.estado}:${pos.intento}`, tono: 'run',
      frase: `Ronda del bloque ${num(pos.numero)}, intento ${num(pos.intento) + 1}: ${descripcionPos(S)}`,
      cta: { texto: b.texto, icono: b.icono, accion: 'ronda-pos' } };
  }
  // Regla 5: la ronda terminó sin validadores.
  if (S.modo === 'pos' && pos.estado === 'SIN_VALIDADORES') {
    return { clave: 'r5', tono: 'err', frase: 'La ronda terminó sin bloque: no quedan validadores con saldo.',
      cta: { texto: 'Crear red nueva', icono: 'sliders', accion: 'paso1' } };
  }
  // Reglas 6 y 6b: nodos atrasados.
  if (S.sincronizados === false) {
    const atrasadosOff = nodos.filter((n) => !n.conectado && !n.sincronizado);
    const k = nodos.filter((n) => n.sincronizado).length;
    if (atrasadosOff.length) {
      const X = atrasadosOff[0];
      const frase = atrasadosOff.length === 1
        ? `${k} de ${N} nodos están en el bloque ${altura}; ${X.id} está desconectado y se quedó en el bloque ${num(X.altura)}.`
        : `${k} de ${N} nodos están en el bloque ${altura}; ${listaY(atrasadosOff.map((n) => n.id))} están desconectados y se quedaron atrás.`;
      return { clave: `r6:${X.id}`, tono: 'warn', frase,
        cta: { texto: `Reconectar ${X.id}`, icono: 'power', accion: `reconectar:${X.id}` } };
    }
    return { clave: 'r6b', tono: 'warn', frase: 'Los nodos se están poniendo al día…',
      cta: { texto: 'Ver nodos', icono: 'server', accion: 'paso4' } };
  }
  // Prefijo PoS cuando el último bloque se acaba de aceptar.
  let previo = '';
  if (S.modo === 'pos' && pos.estado === 'ACEPTADO' && num(pos.numero) === altura) {
    previo = `Bloque ${altura} aceptado; ${pos.proponente} recibió ${fmt(num(config.recompensa))} monedas. `;
  }
  // Regla 7: red recién creada.
  if (altura === 0 && p === 0) {
    return { clave: 'r7', tono: 'ok',
      frase: `Red lista: ${N} nodos con ${fmt(num(config.saldo_inicial))} monedas cada uno. Aún no hay transacciones.`,
      cta: { texto: 'Crear transacciones', icono: 'swap', accion: 'paso2' } };
  }
  // Un nodo desconectado puede estar al día: se dice para que no parezca que todos están conectados.
  const apagados = nodos.filter((n) => !n.conectado).map((n) => n.id);
  const nota = apagados.length ? ` (${listaY(apagados)} ${plural(apagados.length, 'desconectado', 'desconectados')})` : '';
  const sincronia = altura === 0
    ? `Los ${N} nodos están sincronizados${nota} (sólo tienen el bloque génesis).`
    : `Los ${N} nodos están sincronizados en el bloque ${altura}${nota}.`;
  // Regla 8: hay transacciones en espera.
  if (p > 0) {
    const verbo = S.modo === 'pow' ? plural(p, 'minada', 'minadas') : plural(p, 'validada', 'validadas');
    const espera = p === 1 ? `Hay 1 transacción esperando a ser ${verbo}.` : `Hay ${p} transacciones esperando a ser ${verbo}.`;
    const cta = S.modo === 'pow'
      ? { texto: 'Minar 1 bloque', icono: 'pick', accion: 'minar1' }
      : { texto: 'Iniciar ronda', icono: 'play', accion: 'ronda-pos' };
    return { clave: 'r8', tono: 'ok', frase: `${previo}${sincronia} ${espera}`, cta };
  }
  // Regla 9: PoW sin pendientes pero con una recompensa por madurar.
  if (S.modo === 'pow') {
    const recs = recompensasPendientes(S);
    if (recs.length) {
      const r = recs[0];
      const f = Math.max(1, r.faltan);
      const cuando = f === 1 ? 'madura en el siguiente bloque' : `madura en ${f} bloques`;
      return { clave: 'r9', tono: 'ok', frase: `${sincronia} La recompensa de ${r.id} (bloque ${r.bloque}) ${cuando}.`,
        cta: { texto: f === 1 ? 'Minar 1 bloque más' : `Minar ${f} bloques más`, icono: 'pick', accion: `madurar:${f}` } };
    }
  }
  // Regla 10: todo quieto.
  return { clave: 'r10', tono: 'ok', frase: `${previo}${sincronia} No hay transacciones en espera.`,
    cta: { texto: 'Crear una transacción', icono: 'swap', accion: 'paso2' } };
}

/** Recompensas PoW pendientes de todos los nodos, de la que madura antes a la última. */
export function recompensasPendientes(S) {
  const filas = [];
  for (const n of nodosDe(S)) {
    for (const r of lista(n.recompensas_pendientes)) {
      filas.push({ id: n.id, bloque: num(r.bloque), monto: num(r.monto), faltan: num(r.faltan) });
    }
  }
  return filas.sort((a, b) => a.faltan - b.faltan || a.bloque - b.bloque || a.id.localeCompare(b.id));
}

/* ---------------------------------------------------------------- acciones de los botones */

let limpiarTras = null;

/**
 * Ejecuta la acción de un botón de «Ahora». Su resultado se queda bajo la
 * frase mientras siga la misma regla (por ejemplo, el 409 «Ya se está minando»
 * hasta que termina esa minería); el de éxito, además, se borra a los 5 s.
 */
async function ejecutar(accion, boton) {
  const zona = $('#res-ahora');
  const S = estado.S;
  if (!S) return;
  clearTimeout(limpiarTras);   // un «éxito» anterior no debe borrar lo que muestre esta acción
  const [nombre, arg] = accion.split(':');
  let r = null;
  switch (nombre) {
    case 'paso1': case 'paso2': case 'paso3': case 'paso4': case 'paso5':
      irAPaso(Number(nombre.slice(4)), { foco: true });
      return;
    case 'bitacora':
      irABitacora();
      return;
    case 'detener':
      r = await conBoton(boton, 'Deteniendo…', () => post('/api/pow/cancelar', {}));
      break;
    case 'minar1':
      r = await conBoton(boton, 'Minando…', () => post('/api/pow/minar', {}));
      if (r && r.ok) irAPaso(3);
      break;
    case 'madurar':
      r = await conBoton(boton, 'Minando…', () => post('/api/pow/minar', { bloques: Number(arg), auto_tx: true }));
      if (r && r.ok) irAPaso(3);
      break;
    case 'reconectar':
      r = await conBoton(boton, 'Reconectando…', () => post(rutaNodo(arg, '/conexion'), { conectado: true }));
      break;
    case 'ronda-pos':
      // El mismo botón que el paso 3: inicia la ronda o la avanza un estado.
      await ejecutarBotonRonda(S, zona, boton, { alIniciar: () => irAPaso(3) });
      fijarVigencia(zona);
      return;
    default:
      return;
  }
  if (!r) return;
  mostrarRespuesta(zona, r);
  if (r.ok) limpiarTras = setTimeout(() => limpiar(zona), 5000);   // el éxito se borra solo
  await refrescar();
  fijarVigencia(zona);
}

/* ---------------------------------------------------------------- dibujar */

let ultimaClave = null;

function botonCta(datos, clase, llave) {
  return h('button', { type: 'button', class: `btn ${clase}`, 'data-accion': datos.accion, 'data-llave': llave },
    icono(datos.icono), datos.texto);
}

/** Actualiza la frase, el botón, la tira y los chips con la instantánea. */
export function actualizarAhora(S) {
  const a = calcularAhora(S, estado.conectado);
  const seccion = $('#ahora');
  atributo(seccion, 'data-tono', a.tono);
  const tono = $('#ahora-tono');
  clases(tono, `tono ${a.tono}`);
  siCambia(tono, a.tono, () => [icono(TONOS[a.tono][0], 'ic-s'), h('span', {}, TONOS[a.tono][1])]);
  texto($('#ahora-frase'), a.frase);
  // Para lectores de pantalla: se anuncia la frase sólo cuando cambia de regla,
  // no en cada ronda de minería (si no, hablaría sin parar).
  if (a.clave !== ultimaClave) {
    ultimaClave = a.clave;
    texto($('#ahora-vivo'), `${TONOS[a.tono][1]}. ${a.frase}`);
  }
  caducar($('#res-ahora'));   // el mensaje de una acción se va cuando cambia la regla (la situación)

  // Botón del siguiente paso. Si está «en curso», no se toca hasta que termine.
  const cta = $('#ahora-cta');
  if (!Array.from(cta.querySelectorAll('button')).some(ocupado)) {
    const firma = JSON.stringify([a.cta, a.sec]);
    const foco = cta.contains(document.activeElement) ? document.activeElement.dataset.llave : null;
    if (siCambia(cta, firma, () => (a.cta ? [
      h('span', { class: 'ahora-sig' }, 'Siguiente paso:'),
      botonCta(a.cta, 'pri', 'cta'),
      a.sec ? botonCta(a.sec, 'peligro', 'sec') : null,
    ] : [])) && foco) {
      const mismo = cta.querySelector(`[data-llave="${foco}"]`) || cta.querySelector('button');
      if (mismo) mismo.focus();   // el foco sigue en el botón aunque cambie su texto
    }
  }

  actualizarTira(S);
  actualizarChips(S);
}

/** Chip de un nodo en la tira: id, bloque e iconos de estado. */
function actualizarTira(S) {
  const pos = objeto(S.pos);
  const minando = powActivo(S);
  const rondaViva = posActiva(S);
  const validadores = new Set(enJuego(pos).map((v) => v.id));
  const proponeAhora = ['SORTEO', 'CANDIDATO', 'VOTACION'].includes(pos.estado) ? pos.proponente : null;

  sincronizarLista($('#tira'), nodosDe(S), (n) => n.id,
    (n) => h('li', {}, h('button', { type: 'button', class: 'nchip', 'data-nodo': n.id })),
    (li, n) => {
      const boton = li.firstElementChild;
      const sinc = !!n.sincronizado;
      const estados = [];
      const nombresClase = ['nchip'];
      const iconoSinc = sinc ? 'check' : 'x';
      let extra = null;
      if (!n.conectado) {
        nombresClase.push('off');
        estados.push('desconectado');
      }
      if (!sinc) nombresClase.push('atras');
      estados.push(sinc ? 'sincronizado' : 'atrasado');
      if (minando && n.conectado) {
        nombresClase.push('mina');
        estados.push('minando');
        extra = 'pick';
      }
      if (rondaViva && proponeAhora === n.id) {
        nombresClase.push('prop');
        estados.push('proponente');
        extra = 'star';
      } else if (rondaViva && validadores.has(n.id)) {
        nombresClase.push('val');
        estados.push('validador');
        extra = 'shield';
      }
      if (n.deshonesto) {
        nombresClase.push('trampa');
        estados.push('tramposo');
      }
      clases(boton, nombresClase.join(' '));
      atributo(boton, 'aria-label', `${n.id}: bloque ${num(n.altura)}, ${estados.join(', ')}`);
      // Desconectado: ⏻ y además ✓ o ✗, para saber si se quedó atrás.
      siCambia(boton, JSON.stringify([n.id, n.altura, iconoSinc, !!n.conectado, extra, !!n.deshonesto]), () => [
        h('span', { class: 'nid' }, n.id, n.deshonesto ? h('b', { class: 'excl', 'aria-hidden': 'true' }, '!') : null),
        h('span', { class: 'nmeta' }, `#${num(n.altura)} `,
          n.conectado ? null : icono('power', 'ic-s off-i'),
          icono(iconoSinc, 'ic-s ok-i'),
          extra ? icono(extra, `ic-s ${extra === 'pick' ? 'pow-i' : 'pos-i'}`) : null),
      ]);
    });

  siCambia($('#ley-modo'), S.modo, () => (S.modo === 'pow'
    ? [icono('pick', 'ic-s'), 'minando']
    : [icono('shield', 'ic-s'), 'validador · ', icono('star', 'ic-s'), 'proponente']));
}

/** Chips de resumen: bloque, en espera, sincronización y dinero en circulación. */
function actualizarChips(S) {
  const h0 = num(S.altura_red);
  const p = lista(S.pendientes).length;
  const atrasados = nodosDe(S).filter((n) => !n.sincronizado).length;
  const cuadra = S.invariantes_ok !== false;
  const total = num(objeto(S.circulacion).total);

  texto($('#chip-bloque span'), `Bloque ${h0}`);
  texto($('#chip-espera span'), `${p} en espera`);
  const chipSync = $('#chip-sync');
  clases(chipSync, `chip ${atrasados ? 'warn' : 'ok'}`);
  siCambia(chipSync, String(atrasados), () => [
    icono(atrasados ? 'x' : 'check', 'ic-s'),
    h('span', {}, atrasados ? `${atrasados} ${plural(atrasados, 'atrasado', 'atrasados')}` : 'Sincronizados'),
  ]);
  const chipDinero = $('#chip-dinero');
  clases(chipDinero, `chip ${cuadra ? 'ok' : 'err'}`);
  siCambia(chipDinero, `${cuadra}|${total}`, () => [
    icono(cuadra ? 'check' : 'x', 'ic-s'),
    h('span', {}, `Dinero ${fmt(total)} ${cuadra ? 'cuadra' : 'no cuadra'}`),
  ]);
}

/** Conecta los clics de la zona B (una vez). */
export function iniciarAhora() {
  conVigencia($('#res-ahora'), () => ultimaClave);
  $('#ahora-cta').addEventListener('click', (e) => {
    const boton = e.target.closest('[data-accion]');
    if (boton) ejecutar(boton.dataset.accion, boton);
  });
  // Tocar un nodo de la tira abre el paso 4 con ese nodo elegido.
  $('#tira').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-nodo]');
    if (!chip) return;
    document.dispatchEvent(new CustomEvent('ver-cadena', { detail: { nodo: chip.dataset.nodo } }));
  });
}
