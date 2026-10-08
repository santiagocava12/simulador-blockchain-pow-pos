/* ==========================================================================
   estado.js — La instantánea del servidor y el sondeo.

   El estado vive en el servidor. El navegador sólo pregunta, una y otra vez,
   GET /api/estado?desde=<último evento visto>:
     - cada 300 ms si se está minando o hay una ronda PoS en curso;
     - cada 1000 ms en reposo;
     - si el servidor no responde (espera máxima 5 s), muestra la franja
       «Sin conexión…» y reintenta cada 2 s, sin romperse.
   La respuesta trae sólo los eventos nuevos de la bitácora (desde=n).
   Si cambia id_simulacion (alguien creó una red nueva, aquí o en otra
   pestaña), se olvida la bitácora y se vuelve a pedir todo con desde=0.
   Los módulos de la pantalla se suscriben con alActualizar(fn).
   ========================================================================== */

import { get } from './api.js';
import { lista, num } from './util.js';

const INTERVALO_ACTIVO = 300;
const INTERVALO_REPOSO = 1000;
const INTERVALO_ESPERA = 100;       // primer sondeo de una espera de las pruebas (pronto)
const INTERVALO_SIN_CONEXION = 2000;
const ESPERA_MAXIMA = 5000;
const MAX_EVENTOS = 500;            // eventos que se guardan en la página

const POS_ACTIVOS = ['APUESTAS', 'SORTEO', 'CANDIDATO', 'VOTACION', 'RECHAZADO'];
const POW_ACTIVOS = ['minando', 'ganador'];

/** Nombre en pantalla de cada estado de la ronda PoS (el servidor usa los códigos en mayúsculas). */
export const NOMBRE_ESTADO_POS = {
  INACTIVA: 'Sin ronda', APUESTAS: 'Apuestas', SORTEO: 'Sorteo', CANDIDATO: 'Bloque propuesto',
  VOTACION: 'Votación', ACEPTADO: 'Aceptado', RECHAZADO: 'Rechazado',
  SIN_VALIDADORES: 'Sin validadores', CANCELADA: 'Cancelada',
};

/** Nombre en pantalla de cada estado de la minería PoW. */
export const NOMBRE_ESTADO_POW = {
  inactivo: 'sin minería', minando: 'minando', ganador: 'entre dos bloques', terminada: 'terminada',
  agotada: 'agotada', cancelada: 'detenida',
};

/** «VOTACION» → «Votación» (si no se conoce, el código tal cual). */
export const nombreEstadoPos = (codigo) => NOMBRE_ESTADO_POS[codigo] || String(codigo ?? '?');

/** Todo lo que la página sabe del servidor. Lo leen los demás módulos. */
export const estado = {
  S: null,                 // última instantánea (datos de /api/estado)
  eventos: [],             // bitácora acumulada (los 500 más recientes)
  ultimoEvento: 0,         // número del último evento recibido
  idSimulacion: null,      // identifica la simulación; cambia al reiniciar
  conectado: true,         // false mientras el sondeo falla
};

/* ---- ayudas que usan varios módulos para leer la instantánea ---- */

/** ¿Hay minería en curso? (el estado "ganador" es el instante entre dos bloques de un lote) */
export const powActivo = (S) => !!S && S.modo === 'pow' && POW_ACTIVOS.includes(S.pow && S.pow.estado);

/** ¿Hay una ronda PoS sin terminar? */
export const posActiva = (S) => !!S && S.modo === 'pos' && POS_ACTIVOS.includes(S.pos && S.pos.estado);

/** Hay algo moviéndose: se sondea más seguido. */
export const hayActividad = (S) => powActivo(S) || posActiva(S);

/** Nodos de la instantánea (siempre una lista). */
export const nodosDe = (S) => lista(S && S.nodos);

/** Un nodo por id (o undefined). */
export const nodoPorId = (S, id) => nodosDe(S).find((n) => n && n.id === id);

/* ---------------------------------------------------------------- suscripciones */

const oyentes = [];          // fn(S, eventosNuevos, { reinicio })
const oyentesConexion = [];  // fn(conectado, recuperada)

/** Llama a fn con cada instantánea nueva. */
export function alActualizar(fn) {
  oyentes.push(fn);
}

/** Llama a fn cuando se pierde o se recupera la conexión. */
export function alCambiarConexion(fn) {
  oyentesConexion.push(fn);
}

/* ---------------------------------------------------------------- sondeo */

let temporizador = null;
let enCurso = false;         // hay una petición de estado en vuelo
let repetir = false;         // alguien pidió otra apenas termine la actual
let iniciados = 0;
let completados = 0;
const alTerminar = [];       // promesas de refrescar() esperando un sondeo
const esperas = new Set();   // condiciones de esperarCondicion()

/** Pide /api/estado y reparte la instantánea. Devuelve true si funcionó. */
async function sondear() {
  const desde = estado.ultimoEvento;
  let r = await get(`/api/estado?desde=${desde}`, { tiempo: ESPERA_MAXIMA });
  if (!r.ok || !r.datos || typeof r.datos !== 'object') return false;
  let S = r.datos;
  const id = typeof S.id_simulacion === 'string' ? S.id_simulacion : r.idSimulacion;

  // ¿Simulación distinta (o primera vez)? Se empieza de cero la bitácora.
  const primeraVez = estado.idSimulacion === null;
  const otraSimulacion = !primeraVez && id !== estado.idSimulacion;
  const reinicio = primeraVez || otraSimulacion;
  if (reinicio) {
    estado.eventos = [];
    estado.ultimoEvento = 0;
    if (desde !== 0) {   // la respuesta traía eventos "desde n" de la otra simulación: se pide todo
      r = await get('/api/estado?desde=0', { tiempo: ESPERA_MAXIMA });
      if (!r.ok || !r.datos || typeof r.datos !== 'object') return false;
      S = r.datos;
    }
  }
  estado.idSimulacion = typeof S.id_simulacion === 'string' ? S.id_simulacion : id;

  // Eventos nuevos (sin repetir los que ya se tenían).
  const nuevos = lista(S.eventos)
    .filter((e) => e && Number.isFinite(e.n) && e.n > estado.ultimoEvento)
    .sort((a, b) => a.n - b.n);
  if (nuevos.length) {
    estado.eventos.push(...nuevos);
    if (estado.eventos.length > MAX_EVENTOS) estado.eventos.splice(0, estado.eventos.length - MAX_EVENTOS);
    estado.ultimoEvento = nuevos[nuevos.length - 1].n;
  }
  estado.ultimoEvento = Math.max(estado.ultimoEvento, num(S.ultimo_evento, 0));
  estado.S = S;

  // Avisar a la pantalla. Un error en un módulo no detiene a los demás ni al sondeo.
  for (const fn of oyentes) {
    try {
      fn(S, nuevos, { reinicio });
    } catch (error) {
      console.error('Error al actualizar la pantalla:', error);
    }
  }
  // ¿Alguna espera de las pruebas ya se cumplió?
  for (const espera of Array.from(esperas)) {
    let cumple = false;
    try {
      cumple = !!espera.condicion(S);
    } catch {
      cumple = false;
    }
    if (cumple) {
      esperas.delete(espera);
      clearTimeout(espera.limite);
      espera.resolver(true);
    }
  }
  return true;
}

function avisarConexion(conectado) {
  const recuperada = conectado && !estado.conectado;
  const perdida = !conectado && estado.conectado;
  estado.conectado = conectado;
  if (!recuperada && !perdida) return;
  for (const fn of oyentesConexion) {
    try {
      fn(conectado, recuperada);
    } catch (error) {
      console.error(error);
    }
  }
}

function programar(ms) {
  clearTimeout(temporizador);
  temporizador = setTimeout(ciclo, ms);
}

/** Un sondeo y la programación del siguiente. Nunca hay dos peticiones a la vez. */
async function ciclo() {
  if (enCurso) {
    repetir = true;
    return;
  }
  clearTimeout(temporizador);
  enCurso = true;
  iniciados += 1;
  const este = iniciados;
  let exito = false;
  try {
    exito = await sondear();
  } catch (error) {
    console.error('Fallo inesperado al sondear:', error);
  }
  enCurso = false;
  completados = este;
  avisarConexion(exito);
  // Resolver a quienes esperaban este sondeo (refrescar).
  for (let i = alTerminar.length - 1; i >= 0; i--) {
    if (alTerminar[i].objetivo <= completados) alTerminar.splice(i, 1)[0].resolver(exito);
  }
  if (repetir) {
    repetir = false;
    ciclo();
    return;
  }
  let pausa = INTERVALO_REPOSO;
  if (!exito) pausa = INTERVALO_SIN_CONEXION;
  else if (esperas.size || hayActividad(estado.S)) pausa = INTERVALO_ACTIVO;
  programar(pausa);
}

/** Arranca el sondeo (una sola vez, desde main.js). */
export function iniciarSondeo() {
  ciclo();
}

/**
 * Pide el estado YA (después de una acción) y espera a tenerlo.
 * Devuelve true si el servidor respondió.
 */
export function refrescar() {
  return new Promise((resolver) => {
    alTerminar.push({ objetivo: iniciados + 1, resolver });
    if (enCurso) repetir = true;
    else ciclo();
  });
}

/**
 * Espera (máx. `ms`) a que la instantánea cumpla `condicion(S)`.
 * Mientras tanto se sondea cada 300 ms. Devuelve true si se cumplió.
 * La usan las pruebas del paso 5 («esperar a que termine la minería»).
 */
export async function esperarCondicion(condicion, ms = 20000) {
  const hasta = Date.now() + ms;
  // Primero una instantánea NUEVA: la que se tenía puede ser de antes de la
  // última acción (por ejemplo, de antes de pedir «minar»).
  await refrescar();
  try {
    if (estado.S && condicion(estado.S)) return true;
  } catch {
    /* la condición falló con este estado: se sigue esperando */
  }
  return new Promise((resolver) => {
    const espera = { condicion, resolver, limite: null };
    espera.limite = setTimeout(() => {
      esperas.delete(espera);
      resolver(false);
    }, Math.max(0, hasta - Date.now()));
    esperas.add(espera);
    if (!enCurso) programar(INTERVALO_ESPERA);
  });
}
