/* ==========================================================================
   paso1_config.js — Paso 1 «Configurar la red» y el conmutador PoW/PoS del
   encabezado.

   - «Red actual»: lo que dice config en la instantánea.
   - «Crear una red nueva»: 4 campos a la vista y el resto en «Opciones
     avanzadas» (cerrado). Se valida en el navegador con los MISMOS mensajes
     que el servidor y, tras una confirmación en línea, POST /api/simulacion.
   ========================================================================== */

import { post } from './api.js';
import { estado, refrescar } from './estado.js';
import { confirmarEnLinea, conBoton, limpiar, mostrarRespuesta, resultado } from './ui.js';
import { $, $$, atributo, cerosMeta, fmt, h, icono, num, objeto, siCambia, texto } from './util.js';

/* Campo → [nombre en los mensajes, mínimo, máximo] (igual que nucleo/config.py). */
const ENTEROS = {
  num_nodos: ['El número de nodos', 10, 20],
  saldo_inicial: ['El saldo inicial', 1, 1000000],
  recompensa: ['La recompensa', 1, 1000000],
  dificultad: ['La dificultad', 1, 6],
  max_tx_por_bloque: ['El máximo de transacciones por bloque', 1, 50],
  intentos_por_ronda: ['El número de intentos por ronda', 1, 5000],
  max_rondas: ['El máximo de rondas', 1, 1000000],
  intervalo_ms: ['El intervalo en milisegundos', 50, 2000],
  num_validadores: ['El número de validadores', 1, 20],
  alfa_porcentaje: ['El porcentaje alfa', 1, 100],
};
const OPCIONES = ['reloj', 'seleccion_validadores', 'regla_castigo'];

/*
 * Opciones avanzadas: su valor normal (el de nucleo/config.py), su nombre en
 * pantalla y el modo en que se usan. Si una red tiene alguna distinta (por
 * ejemplo, la prueba W3 deja «máximo de rondas = 20»), se dice en «Red actual»
 * y junto a «Opciones avanzadas», aunque esté cerrado.
 */
const AVANZADAS = {
  semilla: ['anahuac', 'semilla', null],
  max_tx_por_bloque: [10, 'transacciones por bloque', null],
  intentos_por_ronda: [50, 'intentos por ronda', 'pow'],
  max_rondas: [3000, 'máximo de rondas', 'pow'],
  intervalo_ms: [250, 'ritmo de la animación (ms)', null],
  reloj: ['simulado', 'reloj', null],
  seleccion_validadores: ['todos', 'quiénes validan', 'pos'],
  num_validadores: [5, 'cuántos validan', 'pos'],
  regla_castigo: ['A', 'regla de castigo', 'pos'],
  alfa_porcentaje: [50, 'α', 'pos'],
};

/** Opciones avanzadas de `config` que no tienen su valor normal: [{ etiqueta, valor, normal }]. */
function avanzadasCambiadas(config) {
  const cambiadas = [];
  for (const [campo, [normal, etiqueta, modo]] of Object.entries(AVANZADAS)) {
    if (modo && modo !== config.modo) continue;
    if (campo === 'num_validadores' && config.seleccion_validadores !== 'aleatorio') continue;
    if (campo === 'alfa_porcentaje' && config.regla_castigo !== 'B') continue;
    const valor = config[campo];
    if (valor === undefined || valor === null || valor === '' || String(valor) === String(normal)) continue;
    cambiadas.push({ etiqueta, valor: typeof valor === 'number' ? fmt(valor) : String(valor), normal: typeof normal === 'number' ? fmt(normal) : normal });
  }
  return cambiadas;
}

const describirCambiadas = (cambiadas) => cambiadas.map((c) => `${c.etiqueta} ${c.valor} (lo normal: ${c.normal})`).join('; ');

/**
 * Lee un entero como lo hace el servidor (nucleo/entradas.py): acepta "25",
 * rechaza vacío, decimales y letras. Devuelve { valor } o { error }.
 */
export function leerEntero(crudo, nombre, minimo, maximo) {
  const t = String(crudo === null || crudo === undefined ? '' : crudo);
  if (t.trim() === '') return { error: `${nombre} es obligatorio` };
  const entero = /^\s*([+-]?)(\d+)\s*$/.exec(t);
  if (entero) {
    const n = Number(`${entero[1]}${entero[2]}`);
    if (!(n >= minimo && n <= maximo)) return { error: `${nombre} debe estar entre ${minimo} y ${maximo} (recibido: ${n})` };
    return { valor: n };
  }
  if (/^\s*[+-]?(\d+\.\d*|\.\d+)\s*$/.test(t)) return { error: `${nombre} debe ser un número entero, sin decimales` };
  return { error: `${nombre} debe ser un número entero (recibido: '${t.slice(0, 38)}')` };
}

const formulario = () => $('#form-red');
const modoElegido = () => (formulario().querySelector('input[name=modo]:checked') || {}).value || 'pow';

/** Valida todo el formulario. Devuelve { config, errores }. */
function leerFormulario() {
  const f = formulario();
  const config = { modo: modoElegido() };
  const errores = {};
  for (const [campo, [nombre, minimo, maximo]] of Object.entries(ENTEROS)) {
    const r = leerEntero(f.elements[campo].value, nombre, minimo, maximo);
    if (r.error) errores[campo] = r.error;
    else config[campo] = r.valor;
  }
  const semilla = f.elements.semilla.value.trim();
  if (!semilla) errores.semilla = 'La semilla es obligatorio';   // mismo texto que el servidor
  else if (semilla.length > 64) errores.semilla = 'La semilla es demasiado largo (máximo 64 caracteres)';
  else config.semilla = semilla;
  for (const campo of OPCIONES) config[campo] = f.elements[campo].value;
  if (!errores.num_validadores && !errores.num_nodos && config.num_validadores > config.num_nodos) {
    errores.num_validadores = `El número de validadores (${config.num_validadores}) no puede ser mayor que el número de nodos (${config.num_nodos})`;
  }
  return { config, errores };
}

/** Quita las marcas de error de los campos. */
function limpiarErrores() {
  const f = formulario();
  for (const el of $$('[aria-invalid]', f)) el.removeAttribute('aria-invalid');
  for (const el of $$('.err', f)) el.textContent = '';
}

/** Marca los campos con error (borde rojo, texto debajo) y lleva el foco al primero. */
function marcarErrores(errores) {
  const f = formulario();
  let primero = null;
  for (const [campo, mensaje] of Object.entries(errores)) {
    const el = f.elements[campo];
    const error = document.getElementById(`e-${campo}`);
    if (error) error.textContent = mensaje;
    if (el) {
      el.setAttribute('aria-invalid', 'true');
      const detalles = el.closest('details');
      if (detalles) detalles.open = true;
      if (!primero && !el.closest('[hidden]')) primero = el;
    }
  }
  if (primero) primero.focus();
}

/** Junto a «Opciones avanzadas»: cuántas opciones del formulario no tienen su valor normal. */
function notaAvanzadas() {
  const f = formulario();
  const config = { modo: modoElegido() };
  for (const campo of Object.keys(AVANZADAS)) {
    const valor = f.elements[campo].value.trim();
    config[campo] = /^\d+$/.test(valor) ? Number(valor) : valor;
  }
  const cambiadas = avanzadasCambiadas(config);
  texto($('#avanzadas-nota'), cambiadas.length
    ? ` · ${cambiadas.length} ${cambiadas.length === 1 ? 'distinta' : 'distintas'} de lo normal: ${describirCambiadas(cambiadas)}` : '');
}

/** Muestra u oculta los campos de PoW/PoS y actualiza las ayudas vivas. */
function ajustarFormulario() {
  const f = formulario();
  const modo = modoElegido();
  for (const el of $$('.solo-pow', f)) el.hidden = modo !== 'pow';
  for (const el of $$('.solo-pos', f)) el.hidden = modo !== 'pos';
  texto($('#a-recompensa'), modo === 'pow' ? 'Se puede gastar tras 6 bloques más.' : 'Se paga al aceptarse el bloque.');
  const r = leerEntero(f.elements.dificultad.value, 'La dificultad', 1, 6);
  const previa = $('#a-dificultad');
  if (r.error) {
    previa.replaceChildren('Entre 1 y 6.');
  } else {
    const d = r.valor;
    previa.replaceChildren('El hash debe empezar con ', cerosMeta(d),
      ` · en promedio ≈ ${fmt(16 ** d)} intentos (16${'⁰¹²³⁴⁵⁶'[d]})`,
      d > 4 ? h('b', {}, ' · Puede tardar mucho.') : '');
  }
  notaAvanzadas();
}

/** Pone en el formulario la configuración actual (al cargar o al cambiar de red). */
function llenarFormulario(config) {
  const f = formulario();
  for (const radio of $$('input[name=modo]', f)) radio.checked = radio.value === config.modo;
  for (const campo of [...Object.keys(ENTEROS), 'semilla', ...OPCIONES]) {
    if (f.elements[campo] && config[campo] !== undefined && config[campo] !== null) f.elements[campo].value = String(config[campo]);
  }
  limpiarErrores();
  ajustarFormulario();
}

/** Tabla «Red actual». */
function actualizarResumen(config) {
  siCambia($('#resumen-red'), JSON.stringify(config), () => {
    const pow = config.modo === 'pow';
    const filas = [
      ['Tipo', pow ? 'Prueba de trabajo (PoW)' : 'Prueba de participación (PoS)'],
      ['Nodos', String(num(config.num_nodos))],
      ['Dinero inicial por nodo', fmt(num(config.saldo_inicial))],
      ['Recompensa por bloque', `${fmt(num(config.recompensa))} ${pow ? '(se gasta tras 6 bloques)' : '(se paga al aceptarse)'}`],
      pow
        ? ['Dificultad', [`${num(config.dificultad)} (el hash empieza con `, cerosMeta(config.dificultad), ')']]
        : ['Castigo', config.regla_castigo === 'B'
          ? `Regla B: pierde ${num(config.alfa_porcentaje)} % del valor del bloque (sin pasar de su apuesta)`
          : 'Regla A: pierde toda su apuesta'],
      ['Transacciones por bloque', String(num(config.max_tx_por_bloque))],
      ['Semilla', h('span', { class: 'mono' }, String(config.semilla ?? ''))],
    ];
    const cambiadas = avanzadasCambiadas(config).filter((c) => !['semilla', 'transacciones por bloque'].includes(c.etiqueta));
    if (cambiadas.length) {
      filas.push(['Opciones avanzadas distintas de lo normal',
        h('span', { class: 'aviso-txt' }, icono('warn', 'ic-s'), ' ', describirCambiadas(cambiadas))]);
    }
    return filas.map(([clave, valor]) => h('tr', {}, h('th', { scope: 'row' }, clave), h('td', {}, valor)));
  });
}

let idLlenado = null;

/** Se llama con cada instantánea. */
export function actualizarPaso1(S) {
  const config = objeto(S.config);
  actualizarResumen(config);
  // El formulario sólo se rellena cuando cambia la simulación (no pisa lo que se escribe).
  if (idLlenado !== S.id_simulacion) {
    idLlenado = S.id_simulacion;
    llenarFormulario(config);
  }
  for (const boton of $$('.modo button')) atributo(boton, 'aria-pressed', String(boton.dataset.modo === S.modo));
}

/** Crea la red nueva con `config` (tras confirmar). */
async function crearRed(config, boton, zona) {
  const r = await conBoton(boton, 'Creando…', () => post('/api/simulacion', config));
  if (!r) return;
  if (r.ok) {
    const datos = objeto(r.datos);
    const c = objeto(datos.config);
    resultado(zona, {
      tipo: 'ok',
      titulo: 'Red nueva creada.',
      texto: `${num(c.num_nodos, config.num_nodos)} nodos con ${fmt(num(c.saldo_inicial, config.saldo_inicial))} monedas cada uno. Arriba, «Ahora» te sugiere el siguiente paso.`,
      idSimulacion: r.idSimulacion,
    });
  } else {
    if (r.detalles && r.detalles.campo) marcarErrores({ [r.detalles.campo]: r.error });
    mostrarRespuesta(zona, r);
  }
  await refrescar();
}

/** Conecta el formulario y el conmutador del encabezado (una vez). */
export function iniciarPaso1() {
  const f = formulario();
  f.addEventListener('change', (e) => {
    if (e.target.name === 'modo') ajustarFormulario();
  });
  f.elements.dificultad.addEventListener('input', ajustarFormulario);
  $('#avanzadas').addEventListener('input', notaAvanzadas);
  $('#avanzadas').addEventListener('change', notaAvanzadas);
  f.addEventListener('submit', async (e) => {
    e.preventDefault();
    const zona = $('#res-red');
    limpiarErrores();
    const { config, errores } = leerFormulario();
    if (Object.keys(errores).length) {
      marcarErrores(errores);
      resultado(zona, { tipo: 'error', titulo: 'Revisa los campos marcados en rojo.', texto: Object.values(errores)[0] });
      return;
    }
    limpiar(zona);
    const boton = $('#b-crear-red');
    const si = await confirmarEnLinea($('#conf-red'),
      'Esto borra la cadena, las transacciones y la bitácora actuales. ¿Crear la red nueva?', 'Sí, crear', boton);
    if (si) await crearRed(config, boton, zona);
  });

  // Conmutador PoW / PoS: cambiarlo crea una red nueva con la misma configuración.
  for (const boton of $$('.modo button')) {
    boton.addEventListener('click', async () => {
      const S = estado.S;
      if (!S || boton.dataset.modo === S.modo) return;
      const nombre = boton.dataset.modo === 'pow' ? 'prueba de trabajo (PoW)' : 'prueba de participación (PoS)';
      const si = await confirmarEnLinea($('#conf-modo'),
        `Cambiar a ${nombre} crea una red nueva: se borran la cadena, las transacciones y la bitácora actuales.`,
        'Cambiar y crear red', boton);
      if (!si) return;
      const zona = $('#res-modo');
      const r = await conBoton(boton, 'Creando…', () => post('/api/simulacion', { ...objeto(estado.S.config), modo: boton.dataset.modo }));
      if (!r) return;
      mostrarRespuesta(zona, r, { tituloOk: `Red nueva en ${nombre}.`, textoOk: 'Mira la barra «Ahora»: te dice el siguiente paso.' });
      if (r.ok) setTimeout(() => limpiar(zona), 6000);
      await refrescar();
      boton.focus();
    });
  }
}
