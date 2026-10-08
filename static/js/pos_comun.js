/* ==========================================================================
   pos_comun.js — Lo que comparten la barra «Ahora» y el paso 3 en PoS:
   la descripción del estado de la ronda (§3.1) y el botón que nombra la
   siguiente transición (§3.2). Así el texto es idéntico en los dos lugares.
   ========================================================================== */

import { post } from './api.js';
import { NOMBRE_ESTADO_POS, refrescar } from './estado.js';
import { irAPaso } from './navegacion.js';
import { conBoton, limpiar, mostrarRespuesta, resultado } from './ui.js';
import { fmt, lista, num, objeto, umbral } from './util.js';

/** Nombre corto de cada estado de la ronda (pestaña 3, línea de pasos). Vive en estado.js. */
export { NOMBRE_ESTADO_POS };

/** Validadores que siguen en juego (sin los castigados), ordenados por id. */
export function enJuego(pos) {
  return lista(objeto(pos).validadores).filter((v) => v && !v.excluido)
    .sort((a, b) => String(a.id).localeCompare(String(b.id)));
}

/** El último castigo de la ronda (el del proponente rechazado), o null. */
export function ultimoCastigo(pos) {
  const castigos = lista(objeto(pos).castigos_ronda);
  return castigos.length ? objeto(castigos[castigos.length - 1]) : null;
}

/** Texto de la regla 4 de «Ahora» según el estado de la ronda (§3.1). */
export function descripcionPos(S) {
  const pos = objeto(S.pos);
  const P = pos.proponente || '?';
  switch (pos.estado) {
    case 'APUESTAS': return 'los validadores están apostando.';
    case 'SORTEO': return `el sorteo eligió a ${P}.`;
    case 'CANDIDATO': return `${P} propuso un bloque; falta que voten.`;
    case 'VOTACION': {
      const v = lista(pos.votos).length;
      const A = num(pos.A);
      return `votaron ${v} ${v === 1 ? 'validador' : 'validadores'}: ${fmt(num(pos.V_favor))} a favor de ${fmt(A)} (se necesitan ${fmt(umbral(A))}).`;
    }
    case 'RECHAZADO': {
      const c = ultimoCastigo(pos);
      return `el bloque de ${P} fue rechazado y perdió ${fmt(num(c && c.monto))} monedas.`;
    }
    default: return '';
  }
}

/**
 * El botón de la ronda (§3.2): mismo texto en «Ahora» y en el paso 3.
 * Devuelve { texto, icono, pista, accion } con accion 'ronda' | 'avanzar' | 'paso1'.
 */
export function botonRonda(S) {
  const pos = objeto(S.pos);
  const P = pos.proponente || '?';
  switch (pos.estado) {
    case 'APUESTAS':
      return { texto: 'Hacer el sorteo →', icono: 'dice', accion: 'avanzar',
        pista: 'Cada validador tiene tantos boletos como monedas apostó. Puedes cambiar las apuestas antes del sorteo.' };
    case 'SORTEO':
      return { texto: `Pedir el bloque a ${P} →`, icono: 'block', accion: 'avanzar',
        pista: `${P} armará el bloque con las transacciones en espera.` };
    case 'CANDIDATO':
      return { texto: 'Pedir los votos →', icono: 'vote', accion: 'avanzar',
        pista: 'Cada validador revisa el bloque en su propia copia y vota; su voto pesa lo que apostó.' };
    case 'VOTACION': {
      const A = num(pos.A);
      return { texto: 'Contar los votos →', icono: 'check', accion: 'avanzar',
        pista: `Se acepta si los votos a favor suman al menos ${fmt(umbral(A))} de ${fmt(A)} (2/3).` };
    }
    case 'RECHAZADO':
      return { texto: `Nuevo sorteo sin ${P} →`, icono: 'loop', accion: 'avanzar',
        pista: 'El castigado queda fuera; se sortea otra vez entre los demás.' };
    case 'SIN_VALIDADORES':
      return { texto: 'Crear red nueva', icono: 'sliders', accion: 'paso1', pista: 'No queda nadie con saldo para validar.' };
    default:   // INACTIVA, ACEPTADO, CANCELADA
      // Como en PoW, el botón no se bloquea: si no hay transacciones se avisa antes (principio 9).
      return { texto: 'Iniciar ronda', icono: 'play', accion: 'ronda',
        pista: lista(S.pendientes).length
          ? 'Se forma la ronda con los nodos que tienen saldo.'
          : 'No hay transacciones en espera: el servidor rechazará «Iniciar ronda». Crea algunas en el paso 2.' };
  }
}

/*
 * Antes del sorteo se guardan las apuestas escritas y no guardadas del paso 3
 * (si no, el sorteo usaría las anteriores sin avisar). paso3_pos.js registra
 * aquí la función que las guarda; devuelve el registro de la API o null si no
 * había nada escrito.
 */
let guardarApuestasEscritas = null;

export function registrarAntesDelSorteo(fn) {
  guardarApuestasEscritas = fn;
}

/**
 * Cuerpo de «avanzar»: dice qué estado, bloque e intento está viendo esta
 * pestaña. Si otra pestaña ya avanzó, el servidor responde 409 estado_cambio.
 */
export function cuerpoAvanzar(pos) {
  const cuerpo = { estado_esperado: pos.estado };
  if (Number.isInteger(pos.numero) && pos.numero >= 1) cuerpo.numero_esperado = pos.numero;
  if (Number.isInteger(pos.intento) && pos.intento >= 0) cuerpo.intento_esperado = pos.intento;
  return cuerpo;
}

/**
 * Ejecuta el botón de la ronda. `zona` recibe el resultado; `boton` queda
 * «en curso» mientras viaja la petición. Devuelve el registro de la API (o null).
 */
export async function ejecutarBotonRonda(S, zona, boton, opciones = {}) {
  const b = botonRonda(S);
  if (b.accion === 'paso1') {
    irAPaso(1, { foco: true });
    return null;
  }
  const pos = objeto(S.pos);
  let apuestas = null;
  if (b.accion === 'avanzar' && pos.estado === 'APUESTAS' && guardarApuestasEscritas) {
    apuestas = await conBoton(boton, 'Guardando apuestas…', guardarApuestasEscritas);
    if (apuestas && !apuestas.ok) {
      mostrarRespuesta(zona, apuestas,
        { tituloError: 'No se hizo el sorteo: hay una apuesta escrita que el servidor no acepta. Corrígela en el paso 3.' });
      await refrescar();
      return apuestas;
    }
  }
  const r = b.accion === 'ronda'
    ? await conBoton(boton, 'Iniciando…', () => post('/api/pos/ronda', {}))
    : await conBoton(boton, 'Avanzando…', () => post('/api/pos/avanzar', cuerpoAvanzar(pos)));
  if (!r) return null;
  if (r.ok) {
    if (apuestas) resultado(zona, { tipo: 'ok', titulo: 'Se guardaron las apuestas que escribiste y se hizo el sorteo con ellas.' });
    else limpiar(zona);
    if (b.accion === 'ronda' && opciones.alIniciar) opciones.alIniciar();
  } else {
    mostrarRespuesta(zona, r);
  }
  await refrescar();
  return r;
}
