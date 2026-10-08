/* ==========================================================================
   sha256.js — SHA-256 escrito en JavaScript, sin librerías.

   El navegador ya trae SHA-256 (crypto.subtle), pero SÓLO en un "contexto
   seguro": https, localhost o 127.0.0.1. Si el profesor abre la app desde
   otra computadora por la IP de la red (http://192.168.x.x:5000) no existe, y
   la prueba S3 necesita repetir el sorteo público de la guía:
     r = SHA-256("hash_anterior|numero|intento") mod A.
   Este archivo es el respaldo para ese caso (estándar FIPS 180-4).
   ========================================================================== */

/* Las 64 constantes: los primeros 32 bits de la parte fraccionaria de las raíces cúbicas de los 64 primeros primos. */
const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

/* Valores iniciales: los primeros 32 bits de la parte fraccionaria de las raíces cuadradas de los 8 primeros primos. */
const INICIO = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];

/** Rotación a la derecha de un entero de 32 bits. */
const rotar = (x, n) => (x >>> n) | (x << (32 - n));

/** SHA-256 de unos bytes (Uint8Array). Devuelve los 32 bytes del resumen. */
export function sha256(bytes) {
  // Relleno: un bit 1, ceros y la longitud en bits (64 bits), hasta un múltiplo de 64 bytes.
  const largo = bytes.length;
  const mensaje = new Uint8Array(Math.ceil((largo + 9) / 64) * 64);
  mensaje.set(bytes);
  mensaje[largo] = 0x80;
  const vista = new DataView(mensaje.buffer);
  const bits = largo * 8;
  vista.setUint32(mensaje.length - 8, Math.floor(bits / 2 ** 32));
  vista.setUint32(mensaje.length - 4, bits >>> 0);

  const estado = Uint32Array.from(INICIO);
  const w = new Uint32Array(64);
  for (let bloque = 0; bloque < mensaje.length; bloque += 64) {
    for (let t = 0; t < 16; t++) w[t] = vista.getUint32(bloque + t * 4);
    for (let t = 16; t < 64; t++) {
      const s0 = rotar(w[t - 15], 7) ^ rotar(w[t - 15], 18) ^ (w[t - 15] >>> 3);
      const s1 = rotar(w[t - 2], 17) ^ rotar(w[t - 2], 19) ^ (w[t - 2] >>> 10);
      w[t] = (w[t - 16] + s0 + w[t - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, k] = estado;
    for (let t = 0; t < 64; t++) {
      const t1 = (k + (rotar(e, 6) ^ rotar(e, 11) ^ rotar(e, 25)) + ((e & f) ^ (~e & g)) + K[t] + w[t]) >>> 0;
      const t2 = ((rotar(a, 2) ^ rotar(a, 13) ^ rotar(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
      k = g;
      g = f;
      f = e;
      e = (d + t1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (t1 + t2) >>> 0;
    }
    [a, b, c, d, e, f, g, k].forEach((valor, i) => { estado[i] = (estado[i] + valor) >>> 0; });
  }
  const resumen = new Uint8Array(32);
  const salida = new DataView(resumen.buffer);
  estado.forEach((valor, i) => salida.setUint32(i * 4, valor));
  return resumen;
}

/**
 * SHA-256 de un texto (UTF-8). Usa crypto.subtle si existe y, si no (página
 * abierta por IP sin https), el cálculo de arriba. Devuelve un Uint8Array.
 */
export async function sha256Texto(texto) {
  const bytes = new TextEncoder().encode(texto);
  if (window.crypto && window.crypto.subtle) {
    try {
      return new Uint8Array(await window.crypto.subtle.digest('SHA-256', bytes));
    } catch {
      /* se sigue con el cálculo propio */
    }
  }
  return sha256(bytes);
}
