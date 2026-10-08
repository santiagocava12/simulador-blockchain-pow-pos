/* ==========================================================================
   api.js — Habla con el servidor Flask (rutas de docs/DISENO.md §18).

   Toda llamada devuelve SIEMPRE un objeto "registro" (nunca lanza):
     { metodo, ruta, cuerpo, http, ok, mensaje, datos, error, codigo,
       detalles, idSimulacion, esJson, texto, redCaida }
   - ok = true sólo si el servidor respondió 2xx con {"ok": true}.
   - Si el servidor rechaza algo (400/404/409…), error y codigo traen su
     mensaje tal cual: la interfaz lo muestra junto al botón.
   - redCaida = true si no hubo respuesta (servidor apagado o sin red).
   El paso 5 guarda estos registros para el «Ver detalle técnico».
   ========================================================================== */

const LARGO_MAX_TEXTO = 20000;   // respuestas más largas se recortan al guardarlas

/**
 * Hace una petición HTTP.
 * opciones.tiempo:   ms de espera antes de abandonar (por omisión 15 s).
 * opciones.crudo:    true = `cuerpo` es un texto que se envía tal cual (JSON roto, 3 MB…).
 * opciones.mostrar:  cómo describir el cuerpo en el detalle técnico (p. ej. "(3 MB de texto)").
 */
export async function llamar(metodo, ruta, cuerpo, opciones = {}) {
  const { tiempo = 15000, crudo = false, mostrar } = opciones;
  const registro = {
    metodo, ruta, cuerpo: mostrar !== undefined ? mostrar : cuerpo,
    http: 0, ok: false, mensaje: '', datos: {}, error: '', codigo: '', detalles: {},
    idSimulacion: null, esJson: false, json: null, texto: '', redCaida: false,
  };
  const control = new AbortController();
  const alarma = setTimeout(() => control.abort(), tiempo);
  try {
    const init = { method: metodo, signal: control.signal, cache: 'no-store', headers: {} };
    if (cuerpo !== undefined && metodo !== 'GET') {
      init.body = crudo ? String(cuerpo) : JSON.stringify(cuerpo);
      init.headers['Content-Type'] = 'application/json';
    }
    const respuesta = await fetch(ruta, init);
    registro.http = respuesta.status;
    const textoRespuesta = await respuesta.text();
    registro.texto = textoRespuesta.length > LARGO_MAX_TEXTO
      ? `${textoRespuesta.slice(0, LARGO_MAX_TEXTO)}…` : textoRespuesta;
    try {
      registro.json = JSON.parse(textoRespuesta);
      registro.esJson = true;
    } catch {
      registro.json = null;   // no era JSON (no debería pasar en /api/)
    }
    const j = registro.json && typeof registro.json === 'object' && !Array.isArray(registro.json) ? registro.json : {};
    registro.ok = respuesta.ok && j.ok === true;
    registro.mensaje = typeof j.mensaje === 'string' ? j.mensaje : '';
    registro.datos = j.datos && typeof j.datos === 'object' ? j.datos : {};
    registro.codigo = typeof j.codigo === 'string' ? j.codigo : '';
    registro.detalles = j.detalles && typeof j.detalles === 'object' ? j.detalles : {};
    registro.idSimulacion = typeof j.id_simulacion === 'string' ? j.id_simulacion : null;
    if (typeof j.error === 'string') registro.error = j.error;
    else if (!registro.ok) registro.error = `Respuesta inesperada del servidor (HTTP ${respuesta.status})`;
  } catch (e) {
    registro.redCaida = true;
    registro.codigo = 'sin_conexion';
    registro.error = e && e.name === 'AbortError'
      ? 'El servidor no respondió a tiempo'
      : 'No hay conexión con el servidor';
  } finally {
    clearTimeout(alarma);
  }
  return registro;
}

/** GET a una ruta de la API. */
export const get = (ruta, opciones) => llamar('GET', ruta, undefined, opciones);

/** POST con un cuerpo JSON (por omisión {}). */
export const post = (ruta, cuerpo = {}, opciones) => llamar('POST', ruta, cuerpo, opciones);

/** Parte de una ruta con un id de nodo, escapado por si acaso. */
export const rutaNodo = (id, resto = '') => `/api/nodos/${encodeURIComponent(id)}${resto}`;
