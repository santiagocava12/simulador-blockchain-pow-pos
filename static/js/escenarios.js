/* ==========================================================================
   escenarios.js — Las pruebas y ataques del paso 5.

   Cada escenario es un objeto con su texto («Qué hace», «Debería pasar») y
   una función run(p, parametros) que hace las llamadas REALES a la API en
   orden y devuelve el veredicto:
     { tipo: 'ok' | 'warn' | 'info', titulo, texto, acciones }
   - ✓ ok:   el servidor se comportó como se esperaba (¡un rechazo esperado es
             un éxito!);
   - △ warn: «Resultado distinto al esperado», con la respuesta tal cual;
   - info:   falta una precondición: no se llamó a la API y se explica qué
             hacer (con un botón).
   `p` es el ayudante de prueba (clase Prueba en paso5_pruebas.js): guarda cada
   llamada para el «Ver detalle técnico», muestra el avance y sabe esperar a
   que la instantánea cumpla una condición.
   ========================================================================== */

import { post } from './api.js';
import {
  NOMBRE_ESTADO_POS, estado, nodoPorId, nodosDe, nombreEstadoPos, posActiva, powActivo, refrescar,
} from './estado.js';
import { irAPaso } from './navegacion.js';
import { cuerpoAvanzar, enJuego } from './pos_comun.js';
import { sha256Texto } from './sha256.js';
import { TEXTO_TRAMPA, TRAMPAS_DE_BLOQUE } from './trampas.js';
import { confirmarEnLinea, conBoton, resultado } from './ui.js';
import { fmt, h, icono, lista, listaY, num, objeto, plural, umbral } from './util.js';

/* ---------------------------------------------------------------- ayudas */

/** Un nodo conectado para el papel de «X» (N01 si se puede y tiene al menos `minimo` disponible). */
function elegirX(S, minimo = 0) {
  const conectados = nodosDe(S).filter((n) => n.conectado);
  const n01 = conectados.find((n) => n.id === 'N01');
  if (n01 && num(n01.disponible) >= minimo) return n01.id;
  const conSaldo = conectados.slice().sort((a, b) => num(b.disponible) - num(a.disponible));
  return (conSaldo[0] || nodosDe(S)[0] || { id: 'N01' }).id;
}

/** Otro nodo distinto de los dados (de preferencia N02, N03…). */
function otroNodo(S, ...excepto) {
  const n = nodosDe(S).find((x) => !excepto.includes(x.id));
  return n ? n.id : 'N02';
}

/** Describe la respuesta del servidor en una frase. */
function describir(r) {
  if (r.redCaida) return `No hubo respuesta del servidor (${r.error}).`;
  return r.ok ? `El servidor respondió: «${r.mensaje}».` : `Respuesta del servidor: «${r.error}» (código ${r.codigo || '—'}, HTTP ${r.http}).`;
}

/** Veredicto estándar: ✓ si `bien`, △ si no (con la respuesta tal cual). */
function veredicto(bien, tituloOk, r, textoOk) {
  return {
    tipo: bien ? 'ok' : 'warn',
    titulo: bien ? tituloOk : 'Resultado distinto al esperado',
    texto: bien && textoOk ? textoOk : describir(r),
  };
}

/** △ con una explicación propia (no viene de una respuesta del servidor). */
function distinto(texto) {
  return { tipo: 'warn', titulo: 'Resultado distinto al esperado', texto };
}

/** Resultado «falta un paso antes de esta prueba» (no se llama a la API). */
function falta(texto, acciones = []) {
  return { tipo: 'info', titulo: 'Falta un paso antes de esta prueba', texto, acciones, precondicion: true };
}

const accionPaso = (n, texto = `Ir al paso ${n}`) => ({ texto, icono: 'arrow', fn: () => irAPaso(n, { foco: true }) });

/** Abre el paso 1 con «Opciones avanzadas» desplegadas (regla de castigo, máximo de rondas…). */
const accionAvanzadas = (texto = 'Ir a Configurar › Opciones avanzadas') => ({
  texto,
  icono: 'sliders',
  fn: () => {
    irAPaso(1);
    const avanzadas = document.getElementById('avanzadas');
    if (!avanzadas) return;
    avanzadas.open = true;
    avanzadas.scrollIntoView({ block: 'center', behavior: 'smooth' });
    avanzadas.querySelector('summary').focus({ preventScroll: true });
  },
});

/**
 * «Restaurar la red normal»: W1 y W3 dejan la red con una dificultad (y un
 * límite de rondas) raros; esto crea otra con la dificultad 3 y 3,000 rondas,
 * tras confirmarlo, y sin marcar sus eventos como «de la prueba».
 */
function accionRestaurar() {
  return {
    texto: 'Restaurar la red normal (dificultad 3, 3,000 rondas)',
    icono: 'refresh',
    fn: async (zona, boton) => {
      const conf = zona.closest('.esc') && zona.closest('.esc').querySelector('.esc-conf');
      if (conf) {
        const si = await confirmarEnLinea(conf, 'Crea una red nueva con dificultad 3 y un máximo de 3,000 rondas (se borra la red de la prueba).',
          'Sí, restaurar', boton);
        if (!si) return;
      }
      const r = await conBoton(boton, 'Creando…', () => post('/api/simulacion', { ...objeto(estado.S.config), dificultad: 3, max_rondas: 3000 }));
      if (!r) return;
      await refrescar();
      resultado(zona, r.ok
        ? { tipo: 'ok', titulo: 'Red normal restaurada: dificultad 3 y un máximo de 3,000 rondas.', texto: 'Arriba, «Ahora» te dice el siguiente paso.' }
        : { tipo: 'error', titulo: 'No se pudo restaurar la red.', texto: describir(r) });
      // La red nueva borró el botón pulsado: el foco vuelve al «Probar» de la tarjeta, no al <body>.
      const probar = zona.closest('.esc') && zona.closest('.esc').querySelector('[data-probar]');
      if (probar && (document.activeElement === document.body || !document.activeElement)) probar.focus({ preventScroll: true });
    },
  };
}

/** El motivo más común de una lista de resultados de difusión (sin contar «desconectado»). */
function motivoComun(resultados) {
  const cuenta = new Map();
  for (const r of resultados) {
    if (r.acepto || r.motivo === 'desconectado') continue;
    cuenta.set(r.motivo, (cuenta.get(r.motivo) || 0) + 1);
  }
  let mejor = null;
  for (const [motivo, n] of cuenta) if (!mejor || n > mejor[1]) mejor = [motivo, n];
  return mejor ? mejor[0] : null;
}

/** «Hay una ronda en curso. [Cancelarla y probar]» */
function rondaEnCurso(p) {
  const pos = objeto(p.S.pos);
  return {
    tipo: 'info',
    titulo: `Hay una ronda en curso (${nombreEstadoPos(pos.estado)}).`,
    texto: 'Esta prueba necesita una ronda nueva.',
    precondicion: true,
    acciones: [{
      texto: 'Cancelarla y probar',
      icono: 'play',
      fn: async (zona, boton) => {
        await conBoton(boton, 'Cancelando…', async () => {
          await p.post('/api/pos/cancelar', {});
          await p.refrescar();
        });
        p.reintentar();
      },
    }],
  };
}

/** Si la ronda avanza sola, se apaga (la prueba la avanza paso a paso). */
async function apagarAutomatico(p) {
  if (objeto(p.S.pos).automatico && posActiva(p.S)) {
    await p.post('/api/pos/automatico', { activo: false });
    await p.refrescar();
  }
}

/** Abre una ronda PoS (crea 3 transacciones al azar si no hay en espera). Devuelve el registro o null. */
async function abrirRonda(p) {
  if (!lista(p.S.pendientes).length) {
    p.avance('Creando 3 transacciones al azar para la ronda…');
    await p.post('/api/transacciones/aleatorias', { cantidad: 3 });
  }
  p.avance('Iniciando una ronda nueva…');
  const r = await p.post('/api/pos/ronda', {});
  await p.refrescar();
  return r;
}

/**
 * Avanza la ronda mientras su estado sea el esperado, en orden, con una pausa
 * para que se vea en pantalla. Manda estado, bloque e intento esperados.
 */
async function avanzarPor(p, estados, pausa = 700) {
  for (const esperado of estados) {
    const pos = objeto(p.S.pos);
    if (pos.estado !== esperado) break;
    p.avance(`Avanzando la ronda: ${NOMBRE_ESTADO_POS[esperado]} →…`);
    const r = await p.post('/api/pos/avanzar', cuerpoAvanzar(pos));
    await p.refrescar();
    if (!r.ok) return r;
    await p.pausa(pausa);
  }
  return null;
}

/** Avanza hasta llegar a `objetivo` (máx. 8 transiciones). */
async function avanzarHasta(p, objetivo, pausa = 500) {
  for (let i = 0; i < 8 && posActiva(p.S) && objeto(p.S.pos).estado !== objetivo; i++) {
    const r = await avanzarPor(p, [objeto(p.S.pos).estado], pausa);
    if (r && !r.ok) return r;
  }
  return null;
}

/**
 * Repite en el navegador el sorteo público de la guía:
 *   r = SHA-256("hash_anterior|numero|intento") mod A; gana el primero con r < acumulado.
 * Devuelve el id del proponente. sha256Texto funciona también si la página se
 * abrió por IP sin https (ahí el navegador no trae crypto.subtle).
 */
async function sorteoLocal(hashAnterior, numero, intento, apuestas) {
  const resumen = await sha256Texto(`${hashAnterior}|${numero}|${intento}`);
  let r = 0n;
  for (const byte of resumen) r = (r << 8n) | BigInt(byte);
  const ids = Object.keys(apuestas).sort();
  const A = ids.reduce((s, id) => s + BigInt(apuestas[id]), 0n);
  if (A === 0n) return null;
  r %= A;
  let acumulado = 0n;
  for (const id of ids) {
    acumulado += BigInt(apuestas[id]);
    if (r < acumulado) return id;
  }
  return null;
}

/* ---------------------------------------------------------------- 7.1 entradas inválidas (tabla) */

const espera400 = (registros) => registros.every((r) => r.http === 400 && r.esJson && r.json.ok === false);

const FILAS_ENTRADAS = [
  { cod: 'E1', caso: 'Red con 25 nodos', envio: 'num_nodos: 25',
    run: (p) => p.post('/api/simulacion', { ...objeto(p.S.config), num_nodos: 25 }), esperado: espera400 },
  { cod: 'E8', caso: 'Dificultad 9', envio: 'dificultad: 9',
    run: (p) => p.post('/api/simulacion', { ...objeto(p.S.config), dificultad: 9 }), esperado: espera400 },
  { cod: 'E2', caso: 'Monto negativo', envio: 'monto: -5',
    run: (p) => p.post('/api/transacciones', { emisor: elegirX(p.S), receptor: otroNodo(p.S, elegirX(p.S)), monto: -5 }), esperado: espera400 },
  { cod: 'E3', caso: 'Monto cero', envio: 'monto: 0',
    run: (p) => p.post('/api/transacciones', { emisor: elegirX(p.S), receptor: otroNodo(p.S, elegirX(p.S)), monto: 0 }), esperado: espera400 },
  { cod: 'E4', caso: 'Monto con letras', envio: 'monto: "diez"',
    run: (p) => p.post('/api/transacciones', { emisor: elegirX(p.S), receptor: otroNodo(p.S, elegirX(p.S)), monto: 'diez' }), esperado: espera400 },
  { cod: 'E5', caso: 'Monto vacío', envio: 'monto: ""',
    run: (p) => p.post('/api/transacciones', { emisor: elegirX(p.S), receptor: otroNodo(p.S, elegirX(p.S)), monto: '' }), esperado: espera400 },
  { cod: 'E6', caso: 'Enviarse a sí mismo', envio: 'emisor = receptor',
    run: (p) => p.post('/api/transacciones', { emisor: elegirX(p.S), receptor: elegirX(p.S), monto: 5 }), esperado: espera400 },
  { cod: 'E7', caso: 'Nodo que no existe', envio: 'emisor: "N99" y GET /api/nodos/N99',
    run: async (p) => [
      await p.post('/api/transacciones', { emisor: 'N99', receptor: otroNodo(p.S, 'N99'), monto: 5 }),
      await p.get('/api/nodos/N99'),
    ],
    esperado: (registros) => registros.every((r) => r.http === 404 && r.esJson && r.json.ok === false) },
];

/* ---------------------------------------------------------------- 7.6 A4 datos mal formados (tabla) */

const esJsonDeError = (http) => (registros) => registros.every((r) => r.http === http && r.esJson && r.json && r.json.ok === false);

const FILAS_MALFORMADAS = [
  { cod: 'A4', caso: 'JSON roto', envio: 'POST /api/transacciones  {"monto": }',
    run: (p) => p.crudo('POST', '/api/transacciones', '{"monto": }'), esperado: esJsonDeError(400) },
  { cod: 'A4', caso: 'Lista en vez de objeto', envio: 'POST /api/transacciones  [1, 2]',
    run: (p) => p.crudo('POST', '/api/transacciones', '[1, 2]'), esperado: esJsonDeError(400) },
  { cod: 'A4', caso: 'Monto como lista', envio: 'POST /api/transacciones  {"monto": [5]}',
    run: (p) => p.post('/api/transacciones', { emisor: elegirX(p.S), receptor: otroNodo(p.S, elegirX(p.S)), monto: [5] }), esperado: esJsonDeError(400) },
  { cod: 'A4', caso: 'Ruta que no existe', envio: 'GET /api/no-existe',
    run: (p) => p.get('/api/no-existe'), esperado: esJsonDeError(404) },
  { cod: 'A4', caso: 'Método equivocado', envio: 'GET /api/pow/minar',
    run: (p) => p.get('/api/pow/minar'), esperado: esJsonDeError(405) },
  { cod: 'A4', caso: 'Cuerpo de 3 MB', envio: 'POST /api/transacciones  (3 MB)',
    run: (p) => p.crudo('POST', '/api/transacciones', `{"relleno": "${'a'.repeat(3 * 1024 * 1024)}"}`, '(3 MB de texto)'),
    esperado: esJsonDeError(413) },
];

/* ---------------------------------------------------------------- 7.2 transacciones tramposas */

async function escT1(p) {
  const X = elegirX(p.S);
  const Y = otroNodo(p.S, X);
  p.avance(`${X} firma un envío a ${Y} y se altera su firma…`);
  const r = await p.post('/api/ataques/transaccion', { tipo: 'firma_alterada', emisor: X, receptor: Y, monto: 5 });
  const rechazo = objeto(lista(r.datos.rechazadas)[0]);
  const bien = r.ok && rechazo.codigo === 'firma_invalida' && !lista(r.datos.aceptadas).length;
  return veredicto(bien, 'Correcto: la red rechazó la transacción.', r,
    `Respuesta del servidor: «${rechazo.error}» (código firma_invalida). No entró a la lista de espera.`);
}

async function escT2(p) {
  const X = elegirX(p.S);
  const Y = otroNodo(p.S, X);
  const F = otroNodo(p.S, X, Y);
  p.avance(`${F} firma con su clave un envío que dice venir de ${X}…`);
  const r = await p.post('/api/ataques/transaccion', { tipo: 'otra_clave', emisor: X, receptor: Y, monto: 5, firmante: F });
  const rechazo = objeto(lista(r.datos.rechazadas)[0]);
  const bien = r.ok && rechazo.codigo === 'firma_invalida' && !lista(r.datos.aceptadas).length;
  return veredicto(bien, 'Correcto: una firma de otro nodo no sirve.', r,
    `La firma es de ${F}, pero el envío dice ser de ${X}. Respuesta: «${rechazo.error}» (código firma_invalida).`);
}

async function escT3(p) {
  const X = elegirX(p.S);
  const Y = otroNodo(p.S, X);
  const monto = num(nodoPorId(p.S, X).disponible) + 1;
  p.avance(`${X} intenta enviar ${fmt(monto)} (una moneda más de lo que tiene)…`);
  const r = await p.post('/api/transacciones', { emisor: X, receptor: Y, monto });
  return veredicto(!r.ok && r.codigo === 'saldo_insuficiente', 'Correcto: la red no deja gastar de más.', r,
    `Respuesta del servidor: «${r.error}» (código saldo_insuficiente, HTTP ${r.http}).`);
}

async function escT4(p) {
  const X = elegirX(p.S, 1);
  const disponible = num(nodoPorId(p.S, X).disponible);
  if (disponible < 1) return falta('Ningún nodo conectado tiene saldo disponible. Crea una red nueva.', [accionPaso(1)]);
  const Y = otroNodo(p.S, X);
  const monto = Math.floor(disponible / 2) + 1;
  p.avance(`${X} (con ${fmt(disponible)} disponibles) envía dos veces ${fmt(monto)}…`);
  const r = await p.post('/api/ataques/transaccion', { tipo: 'doble_gasto', emisor: X, receptor: Y, monto });
  const aceptadas = lista(r.datos.aceptadas);
  const rechazadas = lista(r.datos.rechazadas);
  const bien = r.ok && aceptadas.length === 1 && rechazadas.length === 1;
  return veredicto(bien, 'Correcto: el mismo dinero no se gasta dos veces.', r,
    `La primera (${fmt(monto)}) entra y queda en espera; la segunda ya no alcanza: «${objeto(rechazadas[0]).error}».`);
}

async function escT5(p) {
  if (num(p.S.altura_red) < 1) {
    return falta('Todavía no hay bloques en la cadena. Mina (o vota) uno primero.', [accionPaso(3)]);
  }
  const X = elegirX(p.S);
  const Y = otroNodo(p.S, X);
  p.avance('Se reenvía una transacción que ya está en la cadena…');
  const r = await p.post('/api/ataques/transaccion', { tipo: 'repetida', emisor: X, receptor: Y, monto: 5 });
  const rechazo = objeto(lista(r.datos.rechazadas)[0]);
  const bien = r.ok && rechazo.codigo === 'doble_gasto' && !lista(r.datos.aceptadas).length;
  return veredicto(bien, 'Correcto: una transacción ya registrada no se acepta otra vez.', r,
    `Respuesta del servidor: «${rechazo.error}» (código doble_gasto).`);
}

/* ---------------------------------------------------------------- 7.3 cadena manipulada */

const TIPOS_ALTERACION = [
  ['monto', 'el monto de una transacción'],
  ['monto_rehash', 'el monto y recalcular su hash'],
  ['hash', 'el hash'],
  ['hash_anterior', 'el hash anterior'],
  ['recompensa', 'la recompensa ×10'],
];

/** Precondición común: la cadena del nodo X necesita `minimo` bloques. */
function faltanBloques(p, X, minimo) {
  const altura = num(nodoPorId(p.S, X).altura);
  if (altura >= minimo) return null;
  const acciones = p.S.modo === 'pow'
    ? [{
      texto: `Minar ${minimo - altura} ${plural(minimo - altura, 'bloque', 'bloques')}`,
      icono: 'pick',
      fn: async (zona, boton) => {
        await conBoton(boton, 'Minando…', () => p.post('/api/pow/minar', { bloques: minimo - altura, auto_tx: true }));
        irAPaso(3, { foco: true });
      },
    }]
    : [accionPaso(3, 'Ir a votar bloques (paso 3)')];
  return falta(`Se necesitan al menos ${minimo} ${plural(minimo, 'bloque', 'bloques')} en la cadena (hay ${altura}).`, acciones);
}

async function escC5(p, { tipo = 'monto' } = {}) {
  const X = elegirX(p.S);
  const sinBloques = faltanBloques(p, X, 2);
  if (sinBloques) return sinBloques;
  const altura = num(nodoPorId(p.S, X).altura);
  const m = Math.ceil(altura / 2);
  const que = (TIPOS_ALTERACION.find(([k]) => k === tipo) || ['', tipo])[1];
  p.avance(`${X} cambia ${que} en el bloque ${m} de una copia de su cadena y la difunde…`);
  const r = await p.post('/api/ataques/alterar-bloque', { nodo: X, numero: m, tipo });
  const resultados = lista(r.datos.resultados);
  const revisaron = resultados.filter((x) => x.motivo !== 'desconectado').length;
  const desconectados = resultados.length - revisaron;
  const motivo = motivoComun(resultados);
  const S = await p.refrescar();
  const bien = r.ok && resultados.length > 0 && num(r.datos.rechazos) === resultados.length;
  // Si se cambió un dato sin recalcular el hash, lo primero que revisa cada nodo es el hash: ése es el motivo.
  const porElHash = ['monto', 'recompensa'].includes(tipo)
    ? ` Se cambió ${tipo === 'recompensa' ? 'la recompensa (×10)' : que} sin recalcular el hash del bloque, así que la alteración se detecta por el hash: ya no coincide con el contenido.` : '';
  return {
    ...veredicto(bien, `Correcto: los ${revisaron} nodos rechazaron la cadena de ${X}.`, r,
      `Motivo: «${motivo}».${porElHash} La copia de ${X} no cambió y ${S && S.sincronizados ? 'todos siguen sincronizados ✓' : 'la red sigue igual'}.${desconectados ? ` (${desconectados} desconectados no la recibieron.)` : ''}`),
    acciones: [accionPaso(4, 'Ver la cadena en el paso 4')],
  };
}

async function escC3(p) {
  const X = elegirX(p.S);
  const sinBloques = faltanBloques(p, X, 1);
  if (sinBloques) return sinBloques;
  p.avance(`${X} difunde su cadena sin el último bloque…`);
  const r = await p.post('/api/ataques/cadena-corta', { nodo: X, quitar: 1 });
  const resultados = lista(r.datos.resultados);
  const bien = r.ok && resultados.length > 0 && num(r.datos.rechazos) === resultados.length;
  return veredicto(bien, 'Correcto: nadie adopta una cadena más corta.', r,
    `Cada nodo respondió: «${motivoComun(resultados)}». Sólo se adopta una cadena válida y más larga.`);
}

async function escC4(p) {
  const Y = otroNodo(p.S, elegirX(p.S));
  p.avance(`Se manda a ${Y} una «cadena» con un bloque roto…`);
  const r1 = await p.post(`/api/nodos/${Y}/recibir`, { cadena: [{ numero: 'uno' }] });
  p.avance(`Se manda a ${Y} un texto en vez de una lista…`);
  const r2 = await p.post(`/api/nodos/${Y}/recibir`, { cadena: 'hola' });
  const rechazada = (r) => (r.ok && r.datos.acepto === false) || (r.http === 400 && r.esJson);
  const bien = rechazada(r1) && rechazada(r2);
  const motivo = (r) => (r.ok ? r.datos.motivo : r.error);
  return veredicto(bien, 'Correcto: los datos rotos se rechazan sin romper nada.', r1.ok ? r2 : r1,
    `Lista con un bloque roto: «${motivo(r1)}» (HTTP ${r1.http}). Texto en vez de lista: «${motivo(r2)}» (HTTP ${r2.http}). Ningún error 500 y ninguna copia cambió.`);
}

/* ---------------------------------------------------------------- 7.4 minería (PoW) */

async function escT6(p) {
  if (powActivo(p.S)) return falta('Hay una minería en curso; espera a que termine o pulsa «Detener» en el paso 3.', [accionPaso(3)]);
  const pendientes = lista(p.S.pendientes).length;
  if (pendientes) {
    return falta(`Hay ${pendientes} ${plural(pendientes, 'transacción', 'transacciones')} en espera; la prueba necesita la lista vacía.`, [{
      texto: 'Minar las que hay',
      icono: 'pick',
      fn: async (zona, boton) => {
        await conBoton(boton, 'Minando…', () => p.post('/api/pow/minar', {}));
        irAPaso(3, { foco: true });
      },
    }]);
  }
  p.avance('Pidiendo minar con la lista de espera vacía…');
  const r = await p.post('/api/pow/minar', {});
  return veredicto(r.http === 409 && r.codigo === 'sin_pendientes', 'Correcto: no se mina un bloque vacío.', r,
    `Respuesta del servidor (HTTP 409): «${r.error}».`);
}

async function escW2(p) {
  if (powActivo(p.S)) {
    p.nota('Se detuvo la minería que estaba en curso.');
    await p.post('/api/pow/cancelar', {});
  }
  p.avance('Paso 1 de 3: empieza a minar 10 bloques seguidos…');
  const r1 = await p.post('/api/pow/minar', { bloques: 10, auto_tx: true });
  p.avance('Paso 2 de 3: enseguida pide minar otra vez…');
  const r2 = await p.post('/api/pow/minar', {});
  await p.refrescar();
  await p.pausa(900);
  p.avance('Paso 3 de 3: detiene la minería…');
  await p.post('/api/pow/cancelar', {});
  return veredicto(r1.ok && r2.http === 409, 'Correcto: no se puede minar dos veces a la vez.', r2,
    `La segunda petición recibió 409: «${r2.error}». Al final se detuvo la minería.`);
}

async function escW1(p) {
  p.avance('Paso 1 de 4: creando una red nueva con dificultad 1…');
  const r0 = await p.post('/api/simulacion', { ...objeto(p.S.config), modo: 'pow', dificultad: 1 });
  if (!r0.ok) return veredicto(false, '', r0);
  p.avance('Paso 2 de 4: creando 3 transacciones al azar…');
  await p.post('/api/transacciones/aleatorias', { cantidad: 3 });
  p.avance('Paso 3 de 4: minando 1 bloque…');
  const r = await p.post('/api/pow/minar', {});
  if (!r.ok) return veredicto(false, '', r);
  p.avance('Paso 4 de 4: esperando al ganador…');
  await p.esperar((S) => !powActivo(S) && num(S.altura_red) >= 1, 20000);
  const pow = objeto(p.S.pow);
  const ganador = objeto(pow.ultimo_ganador);
  const empatados = lista(ganador.empate);
  const hashes = new Map(lista(pow.ultimo_empate).map((x) => [x.minero, x.hash]));
  const bien = empatados.length > 1;
  return {
    tipo: bien ? 'ok' : 'warn',
    titulo: bien ? `Correcto: empataron ${empatados.length} mineros y ganó el hash menor.` : 'No hubo empate esta vez',
    texto: bien
      ? `En la ronda ${ganador.ronda}: ${listaY(empatados.map((id) => (hashes.has(id) ? `${id} (${String(hashes.get(id)).slice(0, 8)}…)` : id)))}. Gana el hash menor → ${ganador.minero}.`
      : `${describir(r)} Vuelve a probar.`,
    acciones: [accionPaso(3, 'Ver la carrera'), accionRestaurar()],
  };
}

async function escW3(p) {
  p.avance('Paso 1 de 4: creando una red nueva con dificultad 6 y límite de 20 rondas…');
  const r0 = await p.post('/api/simulacion', { ...objeto(p.S.config), modo: 'pow', dificultad: 6, max_rondas: 20 });
  if (!r0.ok) return veredicto(false, '', r0);
  p.avance('Paso 2 de 4: creando 3 transacciones al azar…');
  await p.post('/api/transacciones/aleatorias', { cantidad: 3 });
  p.avance('Paso 3 de 4: minando…');
  const r = await p.post('/api/pow/minar', {});
  if (!r.ok) return veredicto(false, '', r);
  p.avance('Paso 4 de 4: esperando a que se acaben las 20 rondas…');
  await p.esperar((S) => objeto(S.pow).estado === 'agotada' || !powActivo(S), 40000);
  const pow = objeto(p.S.pow);
  const bien = pow.estado === 'agotada';
  return {
    tipo: bien ? 'ok' : 'warn',
    titulo: bien ? 'Correcto: la minería se detiene sola al llegar al límite.' : 'Resultado distinto al esperado',
    texto: bien
      ? `«${pow.mensaje}». No se agregó ningún bloque y la red sigue sana. La red quedó con dificultad 6 y un máximo de 20 rondas: restáurala para seguir minando.`
      : `Estado de la minería: ${pow.estado}. ${pow.mensaje || ''}`,
    acciones: [accionRestaurar()],
  };
}

async function escW4(p) {
  const conPendiente = (S) => nodosDe(S).filter((n) => n.conectado && num(n.total_recompensas_pendientes) > 0)
    .sort((a, b) => Math.min(...lista(a.recompensas_pendientes).map((r) => num(r.faltan)))
      - Math.min(...lista(b.recompensas_pendientes).map((r) => num(r.faltan))));
  if (!conPendiente(p.S).length) {
    if (powActivo(p.S)) return falta('Hay una minería en curso; espera a que termine para probar.', [accionPaso(3)]);
    p.avance('Nadie tiene una recompensa pendiente: se mina 1 bloque primero…');
    const r0 = await p.post('/api/pow/minar', { bloques: 1, auto_tx: true });
    if (!r0.ok) return veredicto(false, '', r0);
    await p.esperar((S) => !powActivo(S) && conPendiente(S).length > 0, 25000);
    if (!conPendiente(p.S).length) return falta('No se pudo minar un bloque para tener una recompensa pendiente.');
  }
  const Z = conPendiente(p.S)[0];
  const Y = otroNodo(p.S, Z.id);
  const pendiente = num(Z.total_recompensas_pendientes);
  const monto = num(Z.disponible) + pendiente;
  const k = Math.max(1, Math.min(...lista(Z.recompensas_pendientes).map((r) => num(r.faltan))));
  p.avance(`${Z.id} intenta gastar ${fmt(monto)} (su saldo más la recompensa que aún no madura)…`);
  const r = await p.post('/api/transacciones', { emisor: Z.id, receptor: Y, monto });
  return {
    ...veredicto(!r.ok && r.codigo === 'saldo_insuficiente', 'Correcto: la recompensa todavía no se puede gastar.', r,
      `${Z.id} intentó enviar ${fmt(monto)} (sus ${fmt(num(Z.disponible))} disponibles más ${fmt(pendiente)} de recompensa). Respuesta: «${r.error}».`),
    acciones: [{
      texto: `Minar ${k} ${plural(k, 'bloque', 'bloques')} y ver cómo madura`,
      icono: 'pick',
      fn: async (zona, boton) => {
        await conBoton(boton, 'Minando…', () => p.post('/api/pow/minar', { bloques: k, auto_tx: true }));
        irAPaso(3, { foco: true });
      },
    }],
  };
}

async function escW5(p, { trampa = 'recompensa_falsa', forma = 'bloque' } = {}) {
  if (powActivo(p.S)) {
    p.nota('Se detuvo la minería que estaba en curso.');
    await p.post('/api/pow/cancelar', {});
    await p.refrescar();
  }
  const textoTrampa = (TEXTO_TRAMPA[trampa] || trampa).toLowerCase();
  if (forma === 'bloque') {
    const X = elegirX(p.S);
    p.avance(`${X} arma un bloque con la trampa «${textoTrampa}», lo mina y lo difunde…`);
    const r = await p.post('/api/ataques/bloque-tramposo', { nodo: X, trampa });
    const resultados = lista(r.datos.resultados);
    const rechazos = num(r.datos.rechazos);
    const bloque = objeto(r.datos.bloque);
    const bien = r.ok && resultados.length > 0 && rechazos === resultados.length;
    return {
      ...veredicto(bien, `Correcto: el bloque tramposo de ${X} se rechazó.`, r,
        `${X} minó el bloque ${num(bloque.numero)} con la trampa. Motivo del rechazo: «${r.datos.motivo}». ${rechazos} de ${resultados.length} nodos lo revisaron y lo rechazaron (incluido el propio ${X}). Ninguna cadena cambió.`),
      acciones: [accionPaso(3, 'Ver en el paso 3')],
    };
  }
  // Forma «carrera»: la mitad de los mineros se vuelve tramposa y se mina hasta que gane uno de ellos.
  const conectados = nodosDe(p.S).filter((n) => n.conectado).map((n) => n.id);
  const malos = conectados.slice(-Math.max(1, Math.floor(conectados.length / 2)));
  const inicio = estado.ultimoEvento;
  let rechazo = null;
  try {
    p.avance(`Paso 1 de 3: ${listaY(malos)} se vuelven tramposos…`);
    for (const id of malos) await p.post(`/api/nodos/${id}/deshonesto`, { deshonesto: true, trampa });
    p.avance('Paso 2 de 3: minando hasta que un tramposo gane la carrera…');
    const r = await p.post('/api/pow/minar', { bloques: 10, auto_tx: true });
    if (!r.ok) return veredicto(false, '', r);
    const buscar = () => estado.eventos.find((e) => e.n > inicio && e.tipo === 'bloque_rechazado');
    await p.esperar((S) => !!buscar() || !powActivo(S), 45000);
    rechazo = buscar();
    if (powActivo(p.S)) await p.post('/api/pow/cancelar', {});
  } finally {
    p.avance('Paso 3 de 3: vuelven a ser honestos…');
    for (const id of malos) await p.post(`/api/nodos/${id}/deshonesto`, { deshonesto: false });
  }
  const datos = objeto(rechazo && rechazo.datos);
  return {
    tipo: rechazo ? 'ok' : 'warn',
    titulo: rechazo ? `Correcto: el bloque tramposo de ${datos.nodo} se rechazó.` : 'Ningún tramposo ganó la carrera esta vez',
    texto: rechazo
      ? `El bloque ${datos.bloque} de ${datos.nodo} traía: «${datos.motivo}». La red lo rechazó y nadie lo agregó; la prueba detuvo la minería en ese momento, así que ningún bloque tramposo entró a la cadena. Al final, ${listaY(malos)} volvieron a ser honestos.`
      : 'Vuelve a probar, o elige «Un nodo arma y difunde un bloque tramposo».',
    acciones: [accionPaso(3, 'Ver la carrera')],
  };
}

/* ---------------------------------------------------------------- 7.5 votación (PoS) */

async function escT7(p) {
  if (posActiva(p.S)) return rondaEnCurso(p);
  const pendientes = lista(p.S.pendientes).length;
  if (pendientes) {
    return falta(`Hay ${pendientes} ${plural(pendientes, 'transacción', 'transacciones')} en espera; la prueba necesita la lista vacía. Vota primero un bloque.`,
      [accionPaso(3)]);
  }
  p.avance('Pidiendo una ronda con la lista de espera vacía…');
  const r = await p.post('/api/pos/ronda', {});
  return veredicto(r.http === 409 && r.codigo === 'sin_pendientes', 'Correcto: no se propone un bloque vacío.', r,
    `Respuesta del servidor (HTTP 409): «${r.error}».`);
}

async function escS2(p) {
  if (posActiva(p.S) && objeto(p.S.pos).estado !== 'APUESTAS') return rondaEnCurso(p);
  if (!posActiva(p.S)) {
    const r0 = await abrirRonda(p);
    if (!r0.ok) return veredicto(false, '', r0);
  }
  await apagarAutomatico(p);
  const vs = enJuego(objeto(p.S.pos));
  if (!vs.length) return falta('La ronda no tiene validadores.');
  const V = vs[0].id;
  const nodo = nodoPorId(p.S, V) || {};
  const limite = num(nodo.disponible) + num(nodo.apuesta_bloqueada);
  const antes = JSON.stringify(vs.map((v) => [v.id, v.apuesta]));
  p.avance(`${V} intenta apostar ${fmt(limite + 50)} (tiene ${fmt(limite)})…`);
  const r1 = await p.post('/api/pos/apuestas', { apuestas: { [V]: limite + 50 } });
  p.avance(`${V} intenta apostar 0…`);
  const r2 = await p.post('/api/pos/apuestas', { apuestas: { [V]: 0 } });
  const S = await p.refrescar();
  const despues = JSON.stringify(enJuego(objeto(S.pos)).map((v) => [v.id, v.apuesta]));
  const bien = !r1.ok && !r2.ok && antes === despues;
  return {
    ...veredicto(bien, 'Correcto: las apuestas imposibles se rechazan y no cambia ninguna.', r1.ok ? r1 : r2,
      `Apostar ${fmt(limite + 50)}: «${r1.error}». Apostar 0: «${r2.error}». Las apuestas siguen igual.`),
    acciones: [accionPaso(3, 'Ver en el paso 3')],
  };
}

async function escS6(p, { trampa = 'firma_alterada' } = {}) {
  const permitido = ['APUESTAS', 'SORTEO', 'RECHAZADO'];
  if (posActiva(p.S) && !permitido.includes(objeto(p.S.pos).estado)) return rondaEnCurso(p);
  if (!posActiva(p.S)) {
    const r0 = await abrirRonda(p);
    if (!r0.ok) return veredicto(false, '', r0);
  }
  await apagarAutomatico(p);
  if (objeto(p.S.pos).estado !== 'SORTEO') {
    const r = await avanzarPor(p, [objeto(p.S.pos).estado], 600);
    if (r && !r.ok) return veredicto(false, '', r);
  }
  const P = objeto(p.S.pos).proponente;
  if (objeto(p.S.pos).estado !== 'SORTEO' || !P) return distinto(`La ronda quedó en ${nombreEstadoPos(objeto(p.S.pos).estado)}; no se llegó al sorteo.`);
  const textoTrampa = (TEXTO_TRAMPA[trampa] || trampa).toLowerCase();
  try {
    p.avance(`${P} ganó el sorteo y se vuelve tramposo: «${textoTrampa}»…`);
    await p.post(`/api/nodos/${P}/deshonesto`, { deshonesto: true, trampa });
    await p.refrescar();
    await p.pausa(600);
    const r = await avanzarPor(p, ['SORTEO', 'CANDIDATO', 'VOTACION'], 800);
    if (r && !r.ok) return veredicto(false, '', r);
  } finally {
    await p.post(`/api/nodos/${P}/deshonesto`, { deshonesto: false });
  }
  const S = await p.refrescar();
  const pos = objeto(S.pos);
  const historial = lista(pos.historial);
  const ultimo = objeto(historial[historial.length - 1]);
  const bien = ultimo.proponente === P && ultimo.resultado === 'RECHAZADO';
  const A = num(ultimo.A);
  return {
    tipo: bien ? 'ok' : 'warn',
    titulo: bien ? 'Correcto: la red rechazó el bloque tramposo y castigó al proponente.' : 'Resultado distinto al esperado',
    texto: bien
      ? `${P} propuso un bloque con una trampa («${textoTrampa}»); los validadores lo revisaron y votaron en contra: ${fmt(num(ultimo.V_favor))} de ${fmt(A)} a favor (se necesitaban ${fmt(umbral(A))}). Motivo: «${ultimo.motivo}». ${P} pierde ${fmt(num(ultimo.castigo))} monedas (regla ${objeto(S.config).regla_castigo}, se queman) y queda fuera; ${pos.estado === 'RECHAZADO' ? `sigue un nuevo sorteo sin ${P}.` : 'no quedan validadores.'} Al final, ${P} volvió a ser honesto.`
      : `La ronda quedó en ${nombreEstadoPos(pos.estado)}. ${pos.mensaje || ''}`,
    acciones: [accionPaso(3, 'Ver en el paso 3')],
  };
}

async function escS3(p, { caso = 'exacto' } = {}) {
  if (posActiva(p.S) && objeto(p.S.pos).estado !== 'APUESTAS') return rondaEnCurso(p);
  if (!posActiva(p.S)) {
    const r0 = await abrirRonda(p);
    if (!r0.ok) return veredicto(false, '', r0);
  }
  await apagarAutomatico(p);
  const pos = objeto(p.S.pos);
  const vs = enJuego(pos);
  const n = vs.length;
  if (n < 2) return falta('Se necesitan al menos 2 validadores en la ronda.');
  const limite = (id) => {
    const nodo = nodoPorId(p.S, id) || {};
    return num(nodo.disponible) + num(nodo.apuesta_bloqueada);
  };
  const debajo = caso === 'debajo';
  const armar = (b, contra) => {
    const H = b * (n - 1);
    const apuestaContra = H / 2 + (debajo ? 1 : 0);
    const apuestas = {};
    for (const v of vs) apuestas[v.id] = v.id === contra ? apuestaContra : b;
    const cabe = vs.every((v) => limite(v.id) >= apuestas[v.id]);
    return { H, apuestaContra, apuestas, cabe };
  };
  // Se busca quién votará en contra: alguien que NO gane el sorteo (si el
  // proponente vota en contra de su propio bloque, el bloque no lleva su firma).
  let plan = null;
  for (const b of [10, 2]) {
    for (const v of vs.slice().reverse()) {
      const intento = armar(b, v.id);
      if (!intento.cabe) continue;
      const ganador = await sorteoLocal(pos.hash_anterior, pos.numero, num(pos.intento), intento.apuestas);
      if (ganador === null || ganador !== v.id) {
        plan = { b, contra: v.id, ...intento };
        break;
      }
    }
    if (plan) break;
  }
  if (!plan) return falta('Algunos validadores no tienen saldo suficiente para esta prueba. Crea una red nueva.', [accionPaso(1)]);
  const { b, contra, H, apuestaContra, apuestas } = plan;
  const A = H + apuestaContra;
  try {
    p.avance(`Paso 1 de 3: ${n - 1} validadores apuestan ${b} y ${contra} apuesta ${apuestaContra}…`);
    const r1 = await p.post('/api/pos/apuestas', { apuestas });
    if (!r1.ok) return veredicto(false, '', r1);
    p.avance(`Paso 2 de 3: ${contra} votará al revés (en contra)…`);
    await p.post(`/api/nodos/${contra}/deshonesto`, { deshonesto: true, trampa: 'voto_invertido' });
    await p.refrescar();
    await p.pausa(700);
    p.avance('Paso 3 de 3: sorteo, propuesta, votos y conteo…');
    const r = await avanzarPor(p, ['APUESTAS', 'SORTEO', 'CANDIDATO', 'VOTACION'], 700);
    if (r && !r.ok) return veredicto(false, '', r);
  } finally {
    await p.post(`/api/nodos/${contra}/deshonesto`, { deshonesto: false });
  }
  const S = await p.refrescar();
  const historial = lista(objeto(S.pos).historial);
  const ultimo = objeto(historial[historial.length - 1]);
  const bien = debajo ? ultimo.resultado === 'RECHAZADO' : ultimo.resultado === 'ACEPTADO';
  const cuenta = `3 × ${fmt(H)} = ${fmt(3 * H)} ${debajo ? '<' : '='} 2 × ${fmt(A)} = ${fmt(2 * A)}`;
  return {
    tipo: bien ? 'ok' : 'warn',
    titulo: bien ? (debajo ? 'Correcto: apenas debajo de 2/3 se rechaza.' : 'Correcto: exactamente 2/3 se acepta.') : 'Resultado distinto al esperado',
    texto: `A favor ${fmt(num(ultimo.V_favor))} de ${fmt(num(ultimo.A))}: ${n - 1} validadores apostaron ${b} cada uno y votaron a favor (${fmt(H)}); ${contra} apostó ${fmt(apuestaContra)} y votó en contra. ${cuenta} → ${debajo ? 'no alcanza: se rechaza y se aplica la regla de rechazo (castigo al proponente y nuevo sorteo).' : 'exactamente 2/3: se acepta (la regla es «al menos 2/3»).'} Resultado del servidor: ${nombreEstadoPos(ultimo.resultado || objeto(S.pos).estado)}. Al final, ${contra} volvió a votar normal.`,
    acciones: [accionPaso(3, 'Ver en el paso 3')],
  };
}

async function escS4(p) {
  const pos0 = objeto(p.S.pos);
  const enRonda = (S) => new Set(lista(objeto(S.pos).validadores).map((v) => v.id));
  let x = null;
  let desconectado = null;
  if (posActiva(p.S) && pos0.estado === 'VOTACION') {
    const dentro = enRonda(p.S);
    const fuera = nodosDe(p.S).find((n) => !dentro.has(n.id));
    if (fuera) x = fuera.id;
  }
  if (!x && posActiva(p.S)) return rondaEnCurso(p);
  try {
    if (!x) {
      const nodos = nodosDe(p.S);
      const ultimo = nodos[nodos.length - 1];
      // Con «todos validan», el último nodo se desconecta antes de la ronda para quedar fuera.
      if (objeto(p.S.config).seleccion_validadores !== 'aleatorio' && ultimo.conectado) {
        p.avance(`Paso 1 de 4: ${ultimo.id} se desconecta para no ser validador…`);
        await p.post(`/api/nodos/${ultimo.id}/conexion`, { conectado: false });
        desconectado = ultimo.id;
      }
      const r0 = await abrirRonda(p);
      if (!r0.ok) return veredicto(false, '', r0);
      await apagarAutomatico(p);
      const dentro = enRonda(p.S);
      const fuera = nodosDe(p.S).filter((n) => !dentro.has(n.id));
      x = (fuera.find((n) => n.id === ultimo.id) || fuera[0] || {}).id;
      if (!x) return falta('Todos los nodos son validadores en esta ronda; no hay a quién probar.');
      p.avance('Paso 2 de 4: la ronda avanza hasta la votación…');
      const r = await avanzarPor(p, ['APUESTAS', 'SORTEO', 'CANDIDATO'], 500);
      if (r && !r.ok) return veredicto(false, '', r);
      if (objeto(p.S.pos).estado !== 'VOTACION') return distinto(`La ronda quedó en ${nombreEstadoPos(objeto(p.S.pos).estado)}; no se llegó a la votación.`);
    }
    p.avance(`Paso 3 de 4: ${x}, que no es validador, intenta votar…`);
    const r = await p.post('/api/pos/votar', { nodo: x, voto: true });
    return {
      ...veredicto(r.http === 409 && r.codigo === 'voto_invalido', 'Correcto: el voto de un nodo que no es validador no cuenta.', r,
        `Respuesta del servidor (HTTP 409): «${r.error}».${desconectado ? ` (${desconectado} se desconectó antes de la ronda para no ser validador y al final se reconectó.)` : ''} La ronda quedó en la votación: puedes contar los votos en el paso 3.`),
      acciones: [accionPaso(3, 'Ver en el paso 3')],
    };
  } finally {
    if (desconectado) {
      p.avance(`Paso 4 de 4: ${desconectado} se reconecta…`);
      await p.post(`/api/nodos/${desconectado}/conexion`, { conectado: true });
    }
  }
}

async function escS5(p) {
  if (!posActiva(p.S)) {
    const r0 = await abrirRonda(p);
    if (!r0.ok) return veredicto(false, '', r0);
  }
  await apagarAutomatico(p);
  p.avance('La ronda avanza hasta la votación…');
  const r0 = await avanzarHasta(p, 'VOTACION');
  if (r0 && !r0.ok) return veredicto(false, '', r0);
  const pos = objeto(p.S.pos);
  if (pos.estado !== 'VOTACION') return falta(`No se pudo llegar a la votación (la ronda quedó en ${nombreEstadoPos(pos.estado)}).`, [accionPaso(3)]);
  const primero = objeto(lista(pos.votos)[0]);
  if (!primero.validador) return falta('Nadie ha votado todavía (¿validadores desconectados?).', [accionPaso(3)]);
  p.avance(`${primero.validador}, que ya votó, intenta votar otra vez…`);
  const r = await p.post('/api/pos/votar', { nodo: primero.validador, voto: true });
  return {
    ...veredicto(r.http === 409 && r.codigo === 'voto_duplicado', 'Correcto: nadie puede votar dos veces.', r,
      `Respuesta del servidor (HTTP 409): «${r.error}». La ronda quedó en la votación: puedes contar los votos en el paso 3.`),
    acciones: [accionPaso(3, 'Ver en el paso 3')],
  };
}

/** S7 deja a todos sin saldo sólo con la regla A (con la B, el castigado conserva parte de su saldo). */
function previoS7(S) {
  if (objeto(S.config).regla_castigo === 'A') return null;
  return falta(`Esta prueba necesita la regla de castigo A (pierde toda su apuesta). Esta red usa la regla B: el castigado pierde sólo el ${num(objeto(S.config).alfa_porcentaje)} % del valor de las transacciones del bloque (sin pasar de su apuesta), conserva saldo y la red nunca se queda sin validadores. Crea una red con la regla A en Configurar › Opciones avanzadas.`,
    [accionAvanzadas()]);
}

async function escS7(p) {
  const sinReglaA = previoS7(p.S);
  if (sinReglaA) return sinReglaA;
  if (posActiva(p.S)) {
    p.nota('Se canceló la ronda que estaba en curso.');
    await p.post('/api/pos/cancelar', {});
    await p.refrescar();
  }
  const r0 = await abrirRonda(p);
  if (!r0.ok) return veredicto(false, '', r0);
  p.avance('Cada validador apuesta todo su saldo…');
  await p.post('/api/pos/apostar-todo', {});
  const S0 = await p.refrescar();
  const vs = enJuego(objeto(S0.pos)).map((v) => v.id);
  try {
    p.avance(`${vs.length} validadores se vuelven tramposos (firma alterada)…`);
    for (const id of vs) await p.post(`/api/nodos/${id}/deshonesto`, { deshonesto: true, trampa: 'firma_alterada' });
    await p.post('/api/pos/automatico', { activo: true });
    await p.esperar((S) => {
      const pos = objeto(S.pos);
      p.avance(`La ronda avanza sola: intento ${num(pos.intento) + 1}, ${nombreEstadoPos(pos.estado)}; ${lista(pos.excluidos).length} de ${vs.length} castigados…`);
      return pos.estado === 'SIN_VALIDADORES' || !posActiva(S);
    }, 120000);
  } finally {
    for (const id of vs) await p.post(`/api/nodos/${id}/deshonesto`, { deshonesto: false });
  }
  const S = await p.refrescar();
  const pos = objeto(S.pos);
  const castigos = lista(pos.castigos_ronda);
  const quemado = castigos.reduce((s, c) => s + num(c.monto), 0);
  const conSaldo = nodosDe(S).filter((n) => n.conectado && num(n.disponible) > 0).map((n) => n.id);
  const bien = pos.estado === 'SIN_VALIDADORES' && conSaldo.length === 0;
  let texto;
  if (bien) {
    texto = `Cada uno apostó todo su saldo y propuso un bloque con una firma alterada; cada propuesta se rechazó y su autor perdió ${fmt(quemado)} monedas en total (${castigos.map((c) => `${c.nodo} −${fmt(num(c.monto))}`).join(', ')}; se queman). La red quedó sin dinero para validar.`;
  } else if (pos.estado === 'SIN_VALIDADORES') {
    texto = `La ronda terminó sin bloque, pero ${listaY(conSaldo)} todavía ${plural(conSaldo.length, 'tiene', 'tienen')} saldo disponible.`;
  } else {
    texto = `La ronda quedó en ${nombreEstadoPos(pos.estado)}. ${pos.mensaje || ''}`;
  }
  return {
    tipo: bien ? 'ok' : 'warn',
    titulo: bien ? `Correcto: los ${vs.length} validadores fueron castigados y la ronda terminó sin bloque.` : 'Resultado distinto al esperado',
    texto,
    acciones: [
      { texto: 'Ahora: iniciar ronda sin validadores (S1)', icono: 'flask', fn: () => p.correrOtra('S1') },
      accionPaso(1, 'Crear red nueva'),
    ],
  };
}

async function escS1(p) {
  if (posActiva(p.S)) return rondaEnCurso(p);
  const conSaldo = nodosDe(p.S).filter((n) => n.conectado && num(n.disponible) > 0);
  if (conSaldo.length && objeto(p.S.config).regla_castigo !== 'A') {
    return falta(`Todavía hay ${conSaldo.length} ${plural(conSaldo.length, 'nodo', 'nodos')} con saldo. Con la regla de castigo B los castigados conservan parte de su saldo, así que la red no se queda sin validadores: esta prueba necesita una red con la regla A.`,
      [accionAvanzadas()]);
  }
  if (conSaldo.length) {
    return falta(`Todavía hay ${conSaldo.length} ${plural(conSaldo.length, 'nodo', 'nodos')} con saldo para validar. Primero ejecuta «Castigar a todos hasta dejarlos sin saldo».`,
      [{ texto: 'Ejecutar esa prueba', icono: 'flask', fn: () => p.correrOtra('S7') }]);
  }
  p.avance('Pidiendo una ronda sin validadores con saldo…');
  const r = await p.post('/api/pos/ronda', { auto_tx: true });
  return veredicto(r.http === 409 && r.codigo === 'sin_validadores', 'Correcto: sin validadores con saldo no se forma la ronda.', r,
    `Respuesta del servidor (HTTP 409): «${r.error}».`);
}

/* ---------------------------------------------------------------- 7.6 red y robustez */

/** Acción «Reconectar X» que escribe el resultado en la misma zona. */
function accionReconectar(p, x) {
  return {
    texto: `Reconectar ${x}`,
    icono: 'power',
    fn: async (zona, boton) => {
      const r = await conBoton(boton, 'Reconectando…', () => p.post(`/api/nodos/${x}/conexion`, { conectado: true }));
      if (!r) return;
      const S = await p.refrescar();
      const n = nodoPorId(S, x) || {};
      p.pintar(zona, {
        tipo: r.ok && n.sincronizado ? 'ok' : 'warn',
        titulo: r.ok && n.sincronizado ? `Correcto: ${x} se puso al día.` : 'Resultado distinto al esperado',
        texto: `${r.ok ? r.mensaje : describir(r)}${n.sincronizado ? '. Vuelve a estar sincronizado ✓.' : ''}`,
      });
    },
  };
}

async function escR1(p) {
  const nodos = nodosDe(p.S);
  const x = nodos[nodos.length - 1].id;
  if (!nodoPorId(p.S, x).conectado) {
    return { tipo: 'info', titulo: `${x} ya está desconectado.`, texto: 'Reconéctalo para ver cómo se pone al día.', precondicion: true, acciones: [accionReconectar(p, x)] };
  }
  if (powActivo(p.S)) {
    p.nota('Se detuvo la minería que estaba en curso.');
    await p.post('/api/pow/cancelar', {});
  }
  if (posActiva(p.S)) {
    p.nota('Se canceló la ronda que estaba en curso.');
    await p.post('/api/pos/cancelar', {});
  }
  await p.refrescar();
  const alturaAntes = num(p.S.altura_red);
  p.avance(`Paso 1 de 3: ${x} se desconecta…`);
  await p.post(`/api/nodos/${x}/conexion`, { conectado: false });
  if (p.S.modo === 'pow') {
    p.avance('Paso 2 de 3: la red mina 1 bloque sin él…');
    const r = await p.post('/api/pow/minar', { bloques: 1, auto_tx: true });
    if (!r.ok) return veredicto(false, '', r);
  } else {
    p.avance('Paso 2 de 3: la red vota 1 bloque sin él (la ronda avanza sola)…');
    const r = await p.post('/api/pos/ronda', { auto_tx: true });
    if (!r.ok) return veredicto(false, '', r);
    await p.post('/api/pos/automatico', { activo: true });
  }
  p.avance('Paso 3 de 3: esperando el bloque nuevo…');
  await p.esperar((S) => num(S.altura_red) > alturaAntes && !powActivo(S) && !posActiva(S), 40000);
  const n = nodoPorId(p.S, x) || {};
  const atrasado = !n.sincronizado;
  return {
    tipo: atrasado ? 'ok' : 'warn',
    titulo: atrasado ? `Correcto: ${x} se quedó en el bloque ${num(n.altura)} (✗ atrasado).` : 'Resultado distinto al esperado',
    texto: `${x} sigue en el bloque ${num(n.altura)}; la red va en el ${num(p.S.altura_red)}. Mira su chip arriba. Ahora reconéctalo: pedirá la cadena a los demás y adoptará la válida más larga.`,
    acciones: [accionReconectar(p, x)],
  };
}

async function escA1(p) {
  if (p.S.modo === 'pow') {
    if (!powActivo(p.S)) {
      p.avance('Paso 1 de 2: empieza a minar 10 bloques…');
      const r = await p.post('/api/pow/minar', { bloques: 10, auto_tx: true });
      if (!r.ok) return veredicto(false, '', r);
    }
    await p.refrescar();
    await p.pausa(1500);
  } else {
    if (!posActiva(p.S)) {
      const r = await abrirRonda(p);
      if (!r.ok) return veredicto(false, '', r);
    }
    await apagarAutomatico(p);
    // Se avanza un paso sólo si eso deja la ronda a medias (desde la votación se terminaría la ronda).
    if (['APUESTAS', 'SORTEO', 'CANDIDATO'].includes(objeto(p.S.pos).estado)) await avanzarPor(p, [objeto(p.S.pos).estado], 1000);
  }
  p.avance('Paso 2 de 2: crea una red nueva a mitad de la operación…');
  const r = await p.post('/api/simulacion', { ...objeto(p.S.config) });
  const S = await p.refrescar();
  const bien = r.ok && num(S.altura_red) === 0 && !powActivo(S) && !posActiva(S) && S.invariantes_ok !== false;
  return {
    ...veredicto(bien, 'Correcto: la red se reinició limpia a mitad de la operación.', r,
      `Red nueva en el bloque 0, sin ${S.modo === 'pow' ? 'minería' : 'ronda'} en curso y con el dinero cuadrando (${fmt(num(objeto(S.circulacion).total))}).`),
  };
}

async function escA2(p) {
  if (p.S.modo === 'pow') {
    if (!powActivo(p.S)) {
      p.avance('Empieza a minar 10 bloques…');
      const r = await p.post('/api/pow/minar', { bloques: 10, auto_tx: true });
      if (!r.ok) return veredicto(false, '', r);
    }
  } else {
    if (!posActiva(p.S)) {
      const r = await abrirRonda(p);
      if (!r.ok) return veredicto(false, '', r);
    }
    await apagarAutomatico(p);
    p.avance('La ronda avanza hasta la votación…');
    await avanzarHasta(p, 'VOTACION', 300);
  }
  const S = await p.refrescar();
  p.recordarParaRecarga({
    id: S.id_simulacion, modo: S.modo,
    estado: S.modo === 'pow' ? objeto(S.pow).estado : objeto(S.pos).estado,
    numero: S.modo === 'pow' ? objeto(S.pow).numero : objeto(S.pos).numero,
  });
  return {
    tipo: 'info',
    titulo: 'Listo. Ahora pulsa F5 (recargar la página).',
    texto: `Al recargar verás ${S.modo === 'pow' ? 'la misma minería en curso' : `la misma ronda en el paso de ${(NOMBRE_ESTADO_POS[objeto(S.pos).estado] || '').toLowerCase()}`}: el estado vive en el servidor y la página lo vuelve a pedir. Esta tarjeta te dirá si coincide.`,
  };
}

async function escA3() {
  const instrucciones = 'Inicia una ronda (PoS) y pulsa el mismo botón de la ronda en las dos pestañas, por ejemplo «Contar los votos →». Una avanza; la otra muestra «La ronda ya avanzó en otra pestaña (estado actual: …). Se actualizó la vista.» (409 estado_cambio). En PoW, pide «Minar 1 bloque» en ambas: la segunda recibe 409.';
  let ventana = null;
  try {
    ventana = window.open(window.location.href, '_blank');
  } catch {
    ventana = null;
  }
  if (ventana) return { tipo: 'info', titulo: 'Se abrió otra pestaña con esta misma página.', texto: instrucciones };
  // El navegador la bloqueó (ventanas emergentes): se da la dirección para abrirla a mano.
  const direccion = h('input', { class: 'copiar-in', value: window.location.href, readonly: true, 'aria-label': 'Dirección de esta página' });
  return {
    tipo: 'info',
    titulo: 'El navegador no abrió la pestaña: copia esta dirección y ábrela en otra pestaña.',
    texto: instrucciones,
    extra: [direccion],
    acciones: [{
      texto: 'Copiar la dirección',
      icono: 'list',
      fn: async (zona, boton) => {
        let copiada = false;
        try {
          await navigator.clipboard.writeText(direccion.value);
          copiada = true;
        } catch {
          copiada = false;   // sin portapapeles (página abierta por IP): se selecciona para Ctrl+C
        }
        direccion.focus();
        direccion.select();
        boton.replaceChildren(icono(copiada ? 'check' : 'info', 'ic-s'), copiada ? 'Copiada' : 'Seleccionada: pulsa Ctrl+C');
      },
    }],
  };
}

/* ---------------------------------------------------------------- catálogo */

export const GRUPOS = {
  entradas: 'Entradas inválidas', tx: 'Transacciones tramposas', cadena: 'Cadena manipulada',
  mineria: 'Minería (PoW)', votacion: 'Votación (PoS)', red: 'Red y robustez',
};

const OPCIONES_TRAMPA = TRAMPAS_DE_BLOQUE.map((t) => [t, TEXTO_TRAMPA[t]]);

/**
 * Los escenarios. Campos: id, cods (códigos de la guía), grupo, modos ('pow' |
 * 'pos' | 'ambos'), video (modos en los que va en «★ Para el video»),
 * confirma (texto si la prueba borra algo), params (selectores) y run o tabla.
 */
export const ESCENARIOS = [
  { id: 'E', cods: ['E1–E8'], grupo: 'entradas', modos: 'ambos', tabla: FILAS_ENTRADAS, boton: 'Probar las 8',
    titulo: 'Ocho valores equivocados',
    hace: 'Envía al servidor valores equivocados, sin pasar por la validación del navegador.',
    espera: 'Cada uno se rechaza con un mensaje claro (400 o 404) y la red no cambia.' },
  { id: 'T1', cods: ['T1'], grupo: 'tx', modos: 'ambos', run: escT1, ver: 2,
    titulo: 'Enviar una transacción con la firma alterada',
    hace: 'N01 firma un envío a N02 y se cambia un carácter de la firma antes de mandarlo.',
    espera: 'Se rechaza: «Firma inválida». No entra a la lista de espera.' },
  { id: 'T2', cods: ['T2'], grupo: 'tx', modos: 'ambos', run: escT2, ver: 2,
    titulo: 'Firmar con la clave de otro nodo',
    hace: 'Se arma un envío de N01 a N02, pero lo firma N03 con su propia clave.',
    espera: 'Se rechaza: la firma no es de N01 (firma_invalida).' },
  { id: 'T3', cods: ['T3'], grupo: 'tx', modos: 'ambos', run: escT3, ver: 2,
    titulo: 'Gastar más de lo que se tiene',
    hace: 'N01 intenta enviar una moneda más que su saldo disponible.',
    espera: 'Se rechaza por saldo insuficiente (400), diciendo cuánto tiene.' },
  { id: 'T4', cods: ['T4'], grupo: 'tx', modos: 'ambos', video: ['pow'], run: escT4, ver: 2,
    titulo: 'Gastar dos veces el mismo dinero',
    hace: 'N01 envía dos veces poco más de la mitad de su saldo.',
    espera: 'La primera entra; la segunda se rechaza porque ya no alcanza.' },
  { id: 'T5', cods: ['T5'], grupo: 'tx', modos: 'ambos', run: escT5, ver: 2,
    titulo: 'Reenviar una transacción ya registrada',
    hace: 'Se vuelve a enviar una transacción que ya está dentro de un bloque.',
    espera: 'Se rechaza como doble gasto: «ya está registrada en el bloque k».' },
  { id: 'C5', cods: ['C1', 'C2', 'C5'], grupo: 'cadena', modos: 'ambos', video: ['pow'], run: escC5,
    params: [{ clave: 'tipo', etiqueta: 'Qué cambiar', opciones: TIPOS_ALTERACION }],
    titulo: 'Alterar un bloque del medio y difundirlo',
    hace: 'N01 cambia el bloque del medio en una copia de su cadena y la envía a todos.',
    espera: 'Todos los demás la rechazan con el motivo y nadie cambia su copia.' },
  { id: 'C3', cods: ['C3'], grupo: 'cadena', modos: 'ambos', run: escC3, ver: 4,
    titulo: 'Difundir una cadena más corta',
    hace: 'N01 envía su cadena sin el último bloque.',
    espera: 'Nadie la adopta: «no es más larga que la propia».' },
  { id: 'C4', cods: ['C4'], grupo: 'cadena', modos: 'ambos', run: escC4, ver: 4,
    titulo: 'Enviar una cadena mal formada',
    hace: 'Se manda a N02 una «cadena» con datos rotos y otra que ni siquiera es una lista.',
    espera: 'N02 la rechaza con un motivo claro; nunca un error 500.' },
  { id: 'T6', cods: ['T6'], grupo: 'mineria', modos: 'pow', run: escT6, ver: 3,
    titulo: 'Minar sin transacciones en espera',
    hace: 'Pide minar cuando la lista de espera está vacía.',
    espera: '409: «No hay transacciones pendientes para minar».' },
  { id: 'W2', cods: ['W2'], grupo: 'mineria', modos: 'pow', run: escW2, ver: 3,
    titulo: 'Pedir minar mientras ya se mina',
    hace: 'Empieza a minar 10 bloques y enseguida pide minar otra vez; al final detiene.',
    espera: '409: «Ya se está minando el bloque n».' },
  { id: 'W1', cods: ['W1'], grupo: 'mineria', modos: 'pow', run: escW1,
    confirma: 'Esta prueba crea una red nueva con dificultad 1 (se borran la cadena, las transacciones y la bitácora actuales).',
    titulo: 'Provocar un empate entre mineros',
    hace: 'Con dificultad 1, varios mineros encuentran un hash en la misma ronda.',
    espera: 'Se anuncia el empate y gana el hash menor.' },
  { id: 'W3', cods: ['W3'], grupo: 'mineria', modos: 'pow', run: escW3, ver: 3,
    confirma: 'Esta prueba crea una red nueva con dificultad 6 y un límite de 20 rondas (se borra la actual).',
    titulo: 'Dificultad que no se resuelve',
    hace: 'Mina con dificultad 6 y un límite de 20 rondas.',
    espera: 'Se detiene sola: «Se alcanzó el límite de 20 rondas…».' },
  { id: 'W4', cods: ['W4'], grupo: 'mineria', modos: 'pow', video: ['pow'], run: escW4,
    titulo: 'Gastar una recompensa antes de 6 confirmaciones',
    hace: 'Un minero con recompensa pendiente intenta gastarla ya (si no hay, primero se mina 1 bloque).',
    espera: 'Se rechaza (400): dice cuánto está pendiente y en cuántos bloques madura.' },
  { id: 'W5', cods: ['W5', 'T4'], grupo: 'mineria', modos: 'pow', run: escW5,
    params: [
      { clave: 'trampa', etiqueta: 'Trampa', opciones: OPCIONES_TRAMPA, inicial: 'recompensa_falsa' },
      { clave: 'forma', etiqueta: 'Cómo', opciones: [['bloque', 'Un nodo arma y difunde un bloque tramposo'], ['carrera', 'Mineros tramposos en la carrera']] },
    ],
    titulo: 'Minero tramposo: bloque con recompensa falsa (u otra trampa)',
    hace: 'N01 mina un bloque con la trampa elegida y lo difunde (o, en la carrera, la mitad de los mineros hace trampa).',
    espera: 'Todos lo rechazan con el motivo («recompensa falsa: 500 distinta a la establecida 50») y sólo entran bloques honestos.' },
  { id: 'T7', cods: ['T7'], grupo: 'votacion', modos: 'pos', run: escT7, ver: 3,
    titulo: 'Iniciar ronda sin transacciones en espera',
    hace: 'Pide una ronda cuando la lista de espera está vacía.',
    espera: '409: «No hay transacciones pendientes para proponer».' },
  { id: 'S2', cods: ['S2'], grupo: 'votacion', modos: 'pos', run: escS2,
    titulo: 'Apostar más de lo que se tiene (y cero)',
    hace: 'Un validador intenta apostar su saldo + 50 y luego 0.',
    espera: 'Ambas se rechazan y ninguna apuesta cambia.' },
  { id: 'S6', cods: ['S6'], grupo: 'votacion', modos: 'pos', video: ['pos'], run: escS6,
    params: [{ clave: 'trampa', etiqueta: 'Trampa', opciones: OPCIONES_TRAMPA, inicial: 'firma_alterada' }],
    titulo: 'Proponente tramposo: rechazo y castigo',
    hace: 'Quien gana el sorteo se vuelve tramposo y propone un bloque con la trampa elegida.',
    espera: 'Los validadores votan en contra, lo castigan (con la regla A pierde toda su apuesta; con la B, una parte) y hay nuevo sorteo sin él.' },
  { id: 'S3', cods: ['S3'], grupo: 'votacion', modos: 'pos', video: ['pos'], run: escS3,
    params: [{ clave: 'caso', etiqueta: 'Caso', opciones: [['exacto', 'Exactamente 2/3'], ['debajo', 'Apenas debajo de 2/3']] }],
    titulo: 'Votación exactamente en 2/3',
    hace: 'Arma las apuestas para que los votos a favor sean justo 2/3 del total (o una moneda menos) y un validador vota al revés.',
    espera: 'Exactamente 2/3 se acepta; apenas debajo se rechaza.' },
  { id: 'S4', cods: ['S4'], grupo: 'votacion', modos: 'pos', run: escS4,
    titulo: 'Votar sin ser validador',
    hace: 'Un nodo que no está en la ronda intenta votar (si todos validan, el último se desconecta antes de la ronda).',
    espera: '409: «… no es validador en esta ronda; su voto no cuenta».' },
  { id: 'S5', cods: ['S5'], grupo: 'votacion', modos: 'pos', video: ['pos'], run: escS5,
    titulo: 'Votar dos veces',
    hace: 'Un validador que ya votó intenta votar otra vez.',
    espera: '409: «… ya votó en esta ronda; no puede votar dos veces».' },
  { id: 'S7', cods: ['S7'], grupo: 'votacion', modos: 'pos', run: escS7, previo: previoS7, ver: 3,
    confirma: 'Esta prueba deja a todos los validadores sin dinero (tarda unos 30 s); después tendrás que crear una red nueva.',
    titulo: 'Castigar a todos hasta dejarlos sin saldo',
    hace: 'Todos apuestan todo y todos proponen bloques tramposos; la ronda avanza sola (necesita la regla de castigo A).',
    espera: 'Todos son castigados y la ronda termina sin bloque.' },
  { id: 'S1', cods: ['S1'], grupo: 'votacion', modos: 'pos', run: escS1, ver: 3,
    titulo: 'Iniciar ronda sin validadores con saldo',
    hace: 'Pide una ronda cuando nadie tiene saldo (después de la prueba anterior).',
    espera: '409: «Ningún validador con saldo: no se puede formar la ronda».' },
  { id: 'R1', cods: ['R1'], grupo: 'red', modos: 'ambos', video: ['pow', 'pos'], run: escR1,
    titulo: 'Desconectar un nodo y ver cómo se pone al día',
    hace: 'El último nodo se desconecta, la red agrega un bloque y luego lo reconectas.',
    espera: 'Primero se queda atrás (✗); al reconectar adopta la cadena más larga (✓).' },
  { id: 'A1', cods: ['A1'], grupo: 'red', modos: 'ambos', run: escA1,
    confirma: 'Esta prueba reinicia la red a mitad de la operación (se borra la actual).',
    titulo: 'Reiniciar a mitad de la operación',
    hace: 'Empieza a minar o a votar y crea una red nueva en medio.',
    espera: 'Red nueva en el bloque 0, sin nada a medias y con el dinero cuadrando.' },
  { id: 'A2', cods: ['A2'], grupo: 'red', modos: 'ambos', run: escA2, boton: 'Preparar',
    titulo: 'Recargar la página a mitad de una ronda',
    hace: 'Deja algo en curso y te pide recargar.',
    espera: 'Al recargar se ve el mismo paso y la misma ronda: el estado vive en el servidor.' },
  { id: 'A3', cods: ['A3'], grupo: 'red', modos: 'ambos', run: escA3, boton: 'Abrir otra pestaña',
    titulo: 'Dos pestañas a la vez',
    hace: 'Abre esta página en otra pestaña para pulsar el mismo botón en ambas.',
    espera: 'Sólo una acción gana; la otra recibe un aviso amable (409).' },
  { id: 'A4', cods: ['A4'], grupo: 'red', modos: 'ambos', tabla: FILAS_MALFORMADAS, boton: 'Probar los 6',
    titulo: 'Enviar datos mal formados',
    hace: 'Manda peticiones rotas directamente a la API.',
    espera: 'Siempre una respuesta JSON con el error (400, 404, 405, 413); nunca 500 ni una página HTML.' },
];

/** ¿El escenario aplica en este modo? */
export const aplica = (escenario, modo) => escenario.modos === 'ambos' || escenario.modos === modo;

/** Cuántas pruebas hay en un modo (cada fila de una tabla cuenta). */
export function contarEn(modo, enOtro = false) {
  return ESCENARIOS.filter((e) => (enOtro ? !aplica(e, modo) : aplica(e, modo)))
    .reduce((s, e) => s + (e.tabla ? e.tabla.length : 1), 0);
}
