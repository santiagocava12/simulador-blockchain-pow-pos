/* ==========================================================================
   trampas.js — Nombres en pantalla de las trampas de un nodo tramposo
   (paso 5). Las claves son las que entiende el servidor.
   ========================================================================== */

export const TEXTO_TRAMPA = {
  recompensa_falsa: 'Se da una recompensa falsa (10 veces más)',
  firma_alterada: 'Mete una transacción con la firma alterada',
  gasto_excesivo: 'Gasta más de lo que tiene',
  doble_gasto: 'Gasta dos veces el mismo dinero',
  voto_invertido: 'Vota al revés',
};

/** Trampas que cambian el bloque propuesto (sirven en PoW y PoS). */
export const TRAMPAS_DE_BLOQUE = ['recompensa_falsa', 'firma_alterada', 'gasto_excesivo', 'doble_gasto'];

/** Trampas disponibles en un modo: «Vota al revés» sólo tiene sentido en PoS. */
export const trampasDelModo = (modo) => (modo === 'pos' ? [...TRAMPAS_DE_BLOQUE, 'voto_invertido'] : TRAMPAS_DE_BLOQUE);
