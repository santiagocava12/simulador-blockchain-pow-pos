/* ==========================================================================
   main.js — Punto de entrada de la interfaz.

   Arma la página (cada módulo conecta sus botones), se suscribe a las
   instantáneas del servidor y arranca el sondeo de /api/estado.

   Mapa de módulos (docs/UX.md §2):
     A  Encabezado ........ paso1_config.js (conmutador PoW/PoS), glosario.js, tema (aquí)
     B  Ahora ............. ahora.js (+ pos_comun.js)
     C  Pasos ............. pasos.js y un módulo por paso:
                            paso1_config.js, paso2_transacciones.js, paso3_pow.js,
                            paso3_pos.js, paso4_cadena.js, paso5_pruebas.js (+ escenarios.js)
     D  Bitácora .......... bitacora.js
     Comunes .............. api.js (HTTP), estado.js (sondeo), ui.js (resultados,
                            confirmaciones), util.js (crear elementos sin innerHTML)
   ========================================================================== */

import { actualizarAhora, iniciarAhora } from './ahora.js';
import { actualizarBitacora, iniciarBitacora } from './bitacora.js';
import { alActualizar, alCambiarConexion, estado, iniciarSondeo } from './estado.js';
import { iniciarGlosario } from './glosario.js';
import { actualizarPaso1, iniciarPaso1 } from './paso1_config.js';
import { actualizarPaso2, iniciarPaso2 } from './paso2_transacciones.js';
import { actualizarPos, iniciarPos } from './paso3_pos.js';
import { actualizarPow, iniciarPow } from './paso3_pow.js';
import { actualizarPaso4, iniciarPaso4 } from './paso4_cadena.js';
import { actualizarPaso5, iniciarPaso5 } from './paso5_pruebas.js';
import { actualizarPasos, iniciarPasos } from './pasos.js';
import { limpiarResultadosViejos } from './ui.js';
import { $, guardar, icono, leerGuardado, mostrar, texto } from './util.js';

/* ---------------------------------------------------------------- tema claro / oscuro / automático */

const TEMAS = ['auto', 'light', 'dark'];
const NOMBRE_TEMA = { auto: 'automático', light: 'claro', dark: 'oscuro' };
let tema = 'auto';

function aplicarTema(nuevo) {
  tema = TEMAS.includes(nuevo) ? nuevo : 'auto';
  if (tema === 'auto') delete document.documentElement.dataset.theme;   // sigue al sistema operativo
  else document.documentElement.dataset.theme = tema;
  texto($('#tema-txt'), `Tema: ${NOMBRE_TEMA[tema]}`);
}

function iniciarTema() {
  aplicarTema(leerGuardado('localStorage', 'simulador-tema'));
  $('#btn-tema').addEventListener('click', () => {
    aplicarTema(TEMAS[(TEMAS.indexOf(tema) + 1) % TEMAS.length]);
    guardar('localStorage', 'simulador-tema', tema);
  });
}

/* ---------------------------------------------------------------- franja de conexión */

let ocultarFranja = null;

function cambioDeConexion(conectado, recuperada) {
  const franja = $('#banner-conexion');
  clearTimeout(ocultarFranja);
  if (!conectado) {
    franja.className = 'banner';
    franja.replaceChildren(icono('warn'), 'Sin conexión con el servidor; reintentando…');
    franja.hidden = false;
  } else if (recuperada) {
    franja.className = 'banner ok';
    franja.replaceChildren(icono('check'), 'Conexión recuperada');
    franja.hidden = false;
    ocultarFranja = setTimeout(() => { franja.hidden = true; }, 3000);
  }
  // La barra «Ahora» también lo dice (regla 0).
  if (estado.S) actualizarAhora(estado.S);
  else if (!conectado) {
    texto($('#ahora-frase'), 'Sin conexión con el servidor; reintentando…');
    $('#ahora').dataset.tono = 'warn';
  }
}

/* ---------------------------------------------------------------- inicio */

function iniciar() {
  iniciarTema();
  iniciarGlosario();
  iniciarBitacora();
  iniciarAhora();
  iniciarPaso1();
  iniciarPaso2();
  iniciarPow();
  iniciarPos();
  iniciarPaso4();
  iniciarPaso5();
  iniciarPasos();   // al final: abre el paso guardado y avisa a los módulos

  // Cada instantánea nueva se reparte a cada zona. Un error en una no frena a las demás.
  alActualizar((S, nuevos, info) => {
    if (info.reinicio) limpiarResultadosViejos(S.id_simulacion);   // resultados de una red anterior
    mostrar($('#p3-pow'), S.modo === 'pow');
    mostrar($('#p3-pos'), S.modo === 'pos');
  });
  alActualizar((S) => actualizarAhora(S));
  alActualizar((S) => actualizarPasos(S));
  alActualizar((S) => actualizarPaso1(S));
  alActualizar((S) => actualizarPaso2(S));
  alActualizar((S) => actualizarPow(S));
  alActualizar((S) => actualizarPos(S));
  alActualizar((S, nuevos, info) => actualizarPaso4(S, nuevos, info));
  alActualizar((S) => actualizarPaso5(S));
  alActualizar((S, nuevos, info) => actualizarBitacora(S, nuevos, info));
  alCambiarConexion(cambioDeConexion);

  iniciarSondeo();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
else iniciar();

/* Sólo para depurar desde la consola del navegador: window.simulador.estado */
window.simulador = { estado };
