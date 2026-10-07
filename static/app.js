/*
 * Simulador de blockchain (PoW / PoS) — interfaz web.
 *
 * Cómo funciona esta página:
 *   1. El estado vive en el servidor. La página lo pide con
 *      GET /api/estado?desde=<último evento visto> cada 300 ms (si hay minería
 *      o ronda activa) o cada 1000 ms (en reposo) y lo dibuja.
 *   2. Cada botón envía una petición a la API. El resultado («mensaje» o
 *      «error») aparece en la zona de avisos. Nunca se usa alert() ni confirm().
 *   3. Seguridad: los datos del servidor se insertan sólo con textContent o
 *      createElement, nunca con innerHTML.
 *   4. Tolerancia: cualquier campo puede faltar o venir en null. Cada panel se
 *      dibuja por separado; un error en uno no rompe los demás.
 */
(function () {
  "use strict";

  // =========================================================================
  // 1. Constantes
  // =========================================================================

  const SONDEO_ACTIVO_MS = 300;
  const SONDEO_REPOSO_MS = 1000;
  const TIEMPO_LIMITE_MS = 10000;
  const MAX_EVENTOS = 500;
  const MAX_MONTO = 10 ** 12;
  const MAX_PENDIENTES_VISIBLES = 100;
  const BLOQUES_POR_PAGINA = 50;
  const MAX_AVISOS = 5;

  const ESTADOS_FINALES_POS = ["ACEPTADO", "SIN_VALIDADORES", "CANCELADA"];
  const ORDEN_ESTADOS_POS = {
    APUESTAS: 0, SORTEO: 1, CANDIDATO: 2, VOTACION: 3, ACEPTADO: 4, RECHAZADO: 4, SIN_VALIDADORES: 5,
  };
  const SIGUIENTE_ESTADO_POS = {
    APUESTAS: "SORTEO",
    SORTEO: "CANDIDATO",
    CANDIDATO: "VOTACION",
    VOTACION: "ACEPTADO o RECHAZADO",
    RECHAZADO: "SORTEO",
  };
  const CLASE_ESTADO_POS = {
    INACTIVA: "neutro", APUESTAS: "pos", SORTEO: "pos", CANDIDATO: "pos", VOTACION: "pos",
    ACEPTADO: "ok", RECHAZADO: "mal", SIN_VALIDADORES: "alerta", CANCELADA: "gris",
  };
  const EXPLICACION_POS = {
    INACTIVA: "No hay ronda en curso. Cree transacciones y pulse «Iniciar ronda».",
    APUESTAS: "Los validadores bloquean su apuesta. Puede editarlas abajo; luego pulse «Avanzar» para sortear al proponente.",
    SORTEO: "Se sorteó al proponente con probabilidad proporcional a su apuesta (sha256 de hash_anterior|número|intento). «Avanzar»: el proponente arma el bloque candidato.",
    CANDIDATO: "El proponente armó el bloque candidato. «Avanzar»: cada validador lo valida con su propia copia de la cadena y firma su voto.",
    VOTACION: "Votos emitidos. «Avanzar» los cuenta: el bloque se acepta si 3V ≥ 2A (dos tercios de lo apostado). También puede votar a mano.",
    ACEPTADO: "El bloque alcanzó 2/3 de los votos y se agregó a la cadena. Las apuestas quedaron liberadas.",
    RECHAZADO: "El bloque no alcanzó 2/3 o era inválido: el proponente fue castigado. «Avanzar» hace un nuevo sorteo sin él.",
    SIN_VALIDADORES: "No quedan validadores con saldo: la ronda terminó sin bloque.",
    CANCELADA: "La ronda se canceló y las apuestas se liberaron (los castigos ya aplicados se mantienen).",
  };
  const ESTADOS_POW = {
    inactivo: ["Sin minería", "neutro"],
    minando: ["Minando…", "pow"],
    ganador: ["Bloque encontrado", "ok"],
    cancelada: ["Cancelada", "gris"],
    agotada: ["Límite de rondas alcanzado", "mal"],
    terminada: ["Terminada", "ok"],
  };

  const TRAMPAS = {
    recompensa_falsa: "Recompensa falsa",
    firma_alterada: "Firma alterada",
    gasto_excesivo: "Gasto excesivo",
    doble_gasto: "Doble gasto",
    voto_invertido: "Voto invertido",
  };
  const AYUDA_TRAMPAS = {
    recompensa_falsa: "Se asigna una recompensa 10 veces mayor a la establecida",
    firma_alterada: "Incluye una transacción con la firma alterada",
    gasto_excesivo: "Incluye una transacción por más de su saldo",
    doble_gasto: "Incluye dos transacciones que juntas superan su saldo",
    voto_invertido: "Como validador PoS, vota al revés de lo que concluye su validación",
  };
  const TRAMPAS_PROPONENTE = ["recompensa_falsa", "firma_alterada", "gasto_excesivo", "doble_gasto"];

  const AYUDA_ATAQUE_TX = {
    firma_alterada: "Se firma una transacción válida y luego se cambia su firma: la verificación Ed25519 debe fallar.",
    otra_clave: "La transacción dice venir del emisor, pero la firma otra clave: la firma no corresponde al emisor.",
    doble_gasto: "Se envían dos transacciones por el monto indicado; la segunda se rechaza si el saldo no alcanza para ambas.",
    repetida: "Se reenvía una transacción ya registrada (o pendiente): debe rechazarse como doble gasto.",
  };

  // Tipos de la bitácora: etiqueta visible, color (categoría) y grupo del filtro.
  const TIPOS_EVENTO = {
    simulacion: { etiqueta: "Simulación", categoria: "info", grupo: "General" },
    nodo: { etiqueta: "Nodo", categoria: "neutro", grupo: "General" },
    entrada_rechazada: { etiqueta: "Entrada rechazada", categoria: "mal", grupo: "General" },
    error: { etiqueta: "Error", categoria: "mal", grupo: "General" },
    transaccion: { etiqueta: "Transacción", categoria: "ok", grupo: "Transacciones" },
    transaccion_rechazada: { etiqueta: "Tx rechazada", categoria: "mal", grupo: "Transacciones" },
    mineria: { etiqueta: "Minería", categoria: "pow", grupo: "Proof of Work" },
    bloque_minado: { etiqueta: "Bloque minado", categoria: "ok", grupo: "Proof of Work" },
    empate: { etiqueta: "Empate", categoria: "alerta", grupo: "Proof of Work" },
    recompensa_pendiente: { etiqueta: "Recompensa pendiente", categoria: "alerta", grupo: "Proof of Work" },
    recompensa_acreditada: { etiqueta: "Recompensa acreditada", categoria: "ok", grupo: "Proof of Work" },
    bloque_rechazado: { etiqueta: "Bloque rechazado", categoria: "mal", grupo: "Bloques y cadenas" },
    cadena_aceptada: { etiqueta: "Cadena aceptada", categoria: "ok", grupo: "Bloques y cadenas" },
    cadena_rechazada: { etiqueta: "Cadena rechazada", categoria: "mal", grupo: "Bloques y cadenas" },
    pos_estado: { etiqueta: "Estado PoS", categoria: "pos", grupo: "Proof of Stake" },
    sorteo: { etiqueta: "Sorteo", categoria: "pos", grupo: "Proof of Stake" },
    voto: { etiqueta: "Voto", categoria: "ok", grupo: "Proof of Stake" },
    voto_rechazado: { etiqueta: "Voto rechazado", categoria: "mal", grupo: "Proof of Stake" },
    castigo: { etiqueta: "Castigo", categoria: "mal", grupo: "Proof of Stake" },
    sin_validadores: { etiqueta: "Sin validadores", categoria: "alerta", grupo: "Proof of Stake" },
    ataque: { etiqueta: "Ataque", categoria: "ataque", grupo: "Ataques" },
  };

  // Campos del formulario «Nueva simulación» (rangos de DISENO.md §4).
  const CAMPOS_SIMULACION = [
    { nombre: "num_nodos", etiqueta: "el número de nodos", tipo: "entero", minimo: 10, maximo: 20, modo: "comun" },
    { nombre: "semilla", etiqueta: "la semilla", tipo: "texto", maximo: 64, modo: "comun" },
    { nombre: "saldo_inicial", etiqueta: "el saldo inicial", tipo: "entero", minimo: 1, maximo: 1000000, modo: "comun" },
    { nombre: "recompensa", etiqueta: "la recompensa", tipo: "entero", minimo: 1, maximo: 1000000, modo: "comun" },
    { nombre: "max_tx_por_bloque", etiqueta: "el máximo de transacciones por bloque", tipo: "entero", minimo: 1, maximo: 50, modo: "comun" },
    { nombre: "intervalo_ms", etiqueta: "el intervalo del motor", tipo: "entero", minimo: 50, maximo: 2000, modo: "comun" },
    { nombre: "reloj", tipo: "opcion", modo: "comun" },
    { nombre: "dificultad", etiqueta: "la dificultad", tipo: "entero", minimo: 1, maximo: 6, modo: "pow" },
    { nombre: "intentos_por_ronda", etiqueta: "el número de intentos por ronda", tipo: "entero", minimo: 1, maximo: 5000, modo: "pow" },
    { nombre: "max_rondas", etiqueta: "el máximo de rondas", tipo: "entero", minimo: 1, maximo: 1000000, modo: "pow" },
    { nombre: "seleccion_validadores", tipo: "opcion", modo: "pos" },
    { nombre: "num_validadores", etiqueta: "el número de validadores", tipo: "entero", minimo: 1, maximo: 20, modo: "pos" },
    { nombre: "regla_castigo", tipo: "opcion", modo: "pos" },
    { nombre: "alfa_porcentaje", etiqueta: "el porcentaje α", tipo: "entero", minimo: 1, maximo: 100, modo: "pos" },
  ];

  // Ejemplos para «Enviar una petición cruda» (datos mal formados a propósito).
  const EJEMPLOS_CRUDOS = [
    { titulo: "Monto con texto", metodo: "POST", ruta: "/api/transacciones", cuerpo: '{"emisor": "N01", "receptor": "N02", "monto": "abc"}' },
    { titulo: "Monto negativo", metodo: "POST", ruta: "/api/transacciones", cuerpo: '{"emisor": "N01", "receptor": "N02", "monto": -5}' },
    { titulo: "Monto con decimales", metodo: "POST", ruta: "/api/transacciones", cuerpo: '{"emisor": "N01", "receptor": "N02", "monto": 10.5}' },
    { titulo: "Sin monto", metodo: "POST", ruta: "/api/transacciones", cuerpo: '{"emisor": "N01", "receptor": "N02"}' },
    { titulo: "Emisor igual al receptor", metodo: "POST", ruta: "/api/transacciones", cuerpo: '{"emisor": "N01", "receptor": "N01", "monto": 5}' },
    { titulo: "Nodo inexistente (N99)", metodo: "POST", ruta: "/api/transacciones", cuerpo: '{"emisor": "N99", "receptor": "N02", "monto": 5}' },
    { titulo: "JSON roto", metodo: "POST", ruta: "/api/transacciones", cuerpo: '{"emisor": "N01", "monto": ' },
    { titulo: "Cuerpo que no es objeto", metodo: "POST", ruta: "/api/transacciones", cuerpo: "[1, 2, 3]" },
    { titulo: "Transacción con firma falsa", metodo: "POST", ruta: "/api/transacciones/firmada", cuerpo: `{"emisor": "N01", "receptor": "N02", "monto": 5, "timestamp": 1767225601000, "firma": "${"ab".repeat(64)}"}` },
    { titulo: "N fuera de rango (25)", metodo: "POST", ruta: "/api/simulacion", cuerpo: '{"num_nodos": 25}' },
    { titulo: "Dificultad inválida", metodo: "POST", ruta: "/api/simulacion", cuerpo: '{"modo": "pow", "dificultad": "x"}' },
    { titulo: "Consultar nodo N99", metodo: "GET", ruta: "/api/nodos/N99", cuerpo: "" },
    { titulo: "Cadena malformada", metodo: "POST", ruta: "/api/nodos/N01/recibir", cuerpo: '{"cadena": [{"numero": "x", "hash": 5}]}' },
    { titulo: "Cadena que no es lista", metodo: "POST", ruta: "/api/nodos/N01/recibir", cuerpo: '{"cadena": "hola"}' },
    { titulo: "Minar (409 si ya se mina)", metodo: "POST", ruta: "/api/pow/minar", cuerpo: '{"bloques": 1}' },
    { titulo: "Avanzar con estado viejo", metodo: "POST", ruta: "/api/pos/avanzar", cuerpo: '{"estado_esperado": "APUESTAS"}' },
    { titulo: "Apuesta negativa", metodo: "POST", ruta: "/api/pos/apuestas", cuerpo: '{"apuestas": {"N01": -10}}' },
    { titulo: "Método no permitido", metodo: "DELETE", ruta: "/api/estado", cuerpo: "" },
    { titulo: "Ruta inexistente", metodo: "GET", ruta: "/api/no-existe", cuerpo: "" },
  ];

  // =========================================================================
  // 2. Estado del cliente (sólo lo necesario para dibujar)
  // =========================================================================

  const cliente = {
    inst: null,                  // última instantánea del servidor
    ultimoEvento: 0,             // número del último evento de bitácora recibido
    eventos: [],                 // bitácora acumulada (máximo MAX_EVENTOS)
    firmaSimulacion: null,       // identifica la simulación (para detectar reinicios)
    version: null,
    generacion: 0,               // cambia al reiniciar: descarta respuestas viejas
    formularioSucio: false,      // el usuario editó «Nueva simulación»
    formularioPrellenado: null,  // firma de la simulación con que se llenó el formulario
    validarEnNavegador: true,
    borradorApuestas: new Map(), // id -> texto escrito en la apuesta (aún sin guardar)
    estadoPosMostrado: null,     // estado PoS dibujado (se envía como estado_esperado)
  };

  const explorador = {
    id: null,             // nodo elegido
    idCargado: null,      // nodo cuya cadena se muestra
    idSolicitado: null,   // último nodo pedido al servidor
    hashSolicitado: null, // último hash del nodo cuando se pidió su cadena
    cadena: null,
    cargando: false,
    error: null,
    abiertos: new Set(),  // bloques desplegados (se conservan al refrescar)
    limite: BLOQUES_POR_PAGINA,
    actualizado: null,
  };

  const ocupados = new WeakSet();     // botones con una petición en curso
  const filasNodos = new Map();       // id -> fila de la tabla de nodos
  const filasValidadores = new Map(); // id -> fila de la tabla de validadores

  // =========================================================================
  // 3. Utilidades de valores y formato
  // =========================================================================

  function esObjeto(valor) {
    return valor !== null && typeof valor === "object" && !Array.isArray(valor);
  }

  /** Devuelve el valor si es un objeto; si no, un objeto vacío. */
  function objeto(valor) {
    return esObjeto(valor) ? valor : {};
  }

  /** Devuelve el valor si es una lista; si no, una lista vacía. */
  function lista(valor) {
    return Array.isArray(valor) ? valor : [];
  }

  /** Número finito o null. */
  function numero(valor) {
    return typeof valor === "number" && Number.isFinite(valor) ? valor : null;
  }

  /** Texto legible para cualquier valor (null/undefined/"" ⇒ defecto). */
  function texto(valor, defecto = "—") {
    if (valor === null || valor === undefined || valor === "") return defecto;
    if (typeof valor === "string") return valor;
    if (typeof valor === "number" || typeof valor === "boolean") return String(valor);
    try {
      return JSON.stringify(valor);
    } catch (_error) {
      return defecto;
    }
  }

  function mayuscula(cadena) {
    return cadena ? cadena.charAt(0).toUpperCase() + cadena.slice(1) : cadena;
  }

  function recortar(cadena, largo) {
    const s = String(cadena);
    return s.length > largo ? `${s.slice(0, largo)}…` : s;
  }

  function limitar(valor, minimo, maximo) {
    return Math.min(maximo, Math.max(minimo, valor));
  }

  /** Porcentaje 0..100 de parte/total (0 si no se puede calcular). */
  function porcentaje(parte, total) {
    const p = numero(parte);
    const t = numero(total);
    if (p === null || t === null || t <= 0) return 0;
    return limitar((p / t) * 100, 0, 100);
  }

  function sumar(elementos, obtener) {
    return elementos.reduce((suma, elemento) => suma + (numero(obtener(elemento)) ?? 0), 0);
  }

  const formatoEntero = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 0 });

  function fmtNum(valor) {
    const n = numero(valor);
    return n === null ? "—" : formatoEntero.format(n);
  }

  function fmtProb(valor) {
    const n = numero(valor);
    return n === null ? "—" : `${(n * 100).toFixed(1)} %`;
  }

  /** Marca de tiempo en ms ⇒ «2026-01-01 00:00:05 UTC». */
  function fmtFecha(ms) {
    const n = numero(ms);
    if (n === null) return "—";
    const fecha = new Date(n);
    if (Number.isNaN(fecha.getTime())) return "—";
    return fecha.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, " UTC");
  }

  function fmtHora(ms) {
    const n = numero(ms);
    const fecha = new Date(n === null ? NaN : n);
    if (Number.isNaN(fecha.getTime())) return "--:--:--";
    return fecha.toLocaleTimeString("es-MX", { hour12: false });
  }

  function textoFaltan(faltan) {
    const n = numero(faltan);
    if (n === null) return "";
    if (n <= 0) return "madura ya";
    return n === 1 ? "falta 1" : `faltan ${n}`;
  }

  function hashCorto(hash, inicio = 10, fin = 4) {
    if (typeof hash !== "string" || hash === "") return "—";
    if (hash.length <= inicio + fin + 1) return hash;
    return `${hash.slice(0, inicio)}…${fin > 0 ? hash.slice(-fin) : ""}`;
  }

  // =========================================================================
  // 4. Utilidades del DOM (sin innerHTML)
  // =========================================================================

  function $(selector, raiz = document) {
    return raiz.querySelector(selector);
  }

  function $$(selector, raiz = document) {
    return Array.from(raiz.querySelectorAll(selector));
  }

  function agregarHijos(elemento, hijos) {
    for (const hijo of hijos.flat(Infinity)) {
      if (hijo === null || hijo === undefined || hijo === false) continue;
      elemento.append(hijo instanceof Node ? hijo : String(hijo));
    }
  }

  /**
   * Crea un elemento. props admite: clase, texto, titulo, datos (data-*) y
   * cualquier atributo. Los hijos pueden ser nodos o textos.
   */
  function crear(etiqueta, props = {}, ...hijos) {
    const elemento = document.createElement(etiqueta);
    for (const [clave, valor] of Object.entries(props || {})) {
      if (valor === undefined || valor === null || valor === false) continue;
      if (clave === "clase") elemento.className = valor;
      else if (clave === "texto") elemento.textContent = String(valor);
      else if (clave === "titulo") elemento.title = String(valor);
      else if (clave === "datos") {
        for (const [k, v] of Object.entries(valor)) elemento.dataset[k] = String(v);
      } else elemento.setAttribute(clave, valor === true ? "" : String(valor));
    }
    agregarHijos(elemento, hijos);
    return elemento;
  }

  /** Cambia el texto sólo si es distinto (evita trabajo innecesario). */
  function fijarTexto(elemento, valor) {
    if (!elemento) return;
    const nuevo = String(valor);
    if (elemento.textContent !== nuevo) elemento.textContent = nuevo;
  }

  /** Reemplaza todos los hijos de un elemento. */
  function fijarHijos(elemento, ...hijos) {
    if (!elemento) return;
    const nuevos = [];
    for (const hijo of hijos.flat(Infinity)) {
      if (hijo === null || hijo === undefined || hijo === false) continue;
      nuevos.push(hijo instanceof Node ? hijo : document.createTextNode(String(hijo)));
    }
    elemento.replaceChildren(...nuevos);
  }

  function insignia(contenido, clase = "neutro", titulo) {
    return crear("span", { clase: `insignia ${clase}`, texto: contenido, titulo });
  }

  function ponerInsignia(elemento, contenido, clase) {
    if (!elemento) return;
    const nuevaClase = `insignia ${clase}`;
    if (elemento.className !== nuevaClase) elemento.className = nuevaClase;
    fijarTexto(elemento, contenido);
  }

  /** Hash recortado en monoespaciada; el título lleva el hash completo. */
  function elementoHash(hash, opciones = {}) {
    if (typeof hash !== "string" || hash === "") return crear("span", { clase: "vacio-dato", texto: "—" });
    const corto = hashCorto(hash, opciones.inicio, opciones.fin);
    const span = crear("span", { clase: "hash", titulo: `${hash}\n(clic para copiar)`, datos: { completo: hash } });
    if (opciones.ceros) {
      const ceros = /^0*/.exec(corto)[0];
      if (ceros) span.append(crear("span", { clase: "ceros", texto: ceros }));
      span.append(corto.slice(ceros.length));
    } else {
      span.textContent = corto;
    }
    return span;
  }

  /** Hash completo (para el explorador). */
  function elementoHashCompleto(hash) {
    if (typeof hash !== "string" || hash === "") return crear("span", { clase: "vacio-dato", texto: "—" });
    return crear("code", { clase: "hash-completo hash", titulo: "Clic para copiar", datos: { completo: hash }, texto: hash });
  }

  function barraProbabilidad(probabilidad, vistaPrevia = false) {
    const p = numero(probabilidad);
    const relleno = crear("div", { clase: "prob-relleno" });
    relleno.style.width = `${p === null ? 0 : limitar(p * 100, 0, 100)}%`;
    return crear("div", { clase: `prob${vistaPrevia ? " vista-previa" : ""}` },
      crear("div", { clase: "prob-barra" }, relleno),
      crear("span", { clase: "prob-texto", texto: fmtProb(p) }),
      vistaPrevia ? crear("span", { clase: "prob-nota", texto: "vista previa" }) : null);
  }

  function elementoVoto(voto) {
    if (voto === true) return crear("span", { clase: "voto si", texto: "Sí ✓" });
    if (voto === false) return crear("span", { clase: "voto no", texto: "No ✗" });
    return crear("span", { clase: "vacio-dato", texto: "—" });
  }

  function celda(contenido, clase) {
    const td = crear("td", { clase });
    agregarHijos(td, [contenido]);
    return td;
  }

  function filaVacia(columnas, mensaje) {
    return crear("tr", {}, crear("td", { colspan: columnas, clase: "vacio", texto: mensaje }));
  }

  function agregarDato(dl, termino, valor) {
    const dd = crear("dd");
    agregarHijos(dd, [valor]);
    dl.append(crear("div", {}, crear("dt", { texto: termino }), dd));
  }

  /** JSON desplegable; el texto se arma sólo al abrirlo. */
  function detallesJson(valor, resumen = "Ver JSON") {
    const detalles = crear("details", { clase: "json" }, crear("summary", { texto: resumen }));
    let construido = false;
    detalles.addEventListener("toggle", () => {
      if (!detalles.open || construido) return;
      construido = true;
      let contenido;
      try {
        contenido = JSON.stringify(valor, null, 2);
      } catch (_error) {
        contenido = "(no se pudo mostrar)";
      }
      detalles.append(crear("pre", { clase: "codigo", texto: contenido }));
    });
    return detalles;
  }

  /**
   * Mantiene filas de una tabla identificadas por clave, sin recrearlas en cada
   * sondeo (así no se pierde el foco de un campo ni un menú abierto).
   */
  function sincronizarFilas(cuerpo, mapa, claves, crearFila) {
    const vigentes = new Set(claves);
    for (const [clave, registro] of mapa) {
      if (!vigentes.has(clave)) {
        registro.fila.remove();
        mapa.delete(clave);
      }
    }
    claves.forEach((clave, indice) => {
      let registro = mapa.get(clave);
      if (!registro) {
        registro = crearFila(clave);
        mapa.set(clave, registro);
      }
      if (cuerpo.children[indice] !== registro.fila) cuerpo.insertBefore(registro.fila, cuerpo.children[indice] || null);
    });
    return claves.map((clave) => mapa.get(clave));
  }

  // =========================================================================
  // 5. Avisos (toasts) y conexión
  // =========================================================================

  const ICONOS_AVISO = { exito: "✓", error: "✗", info: "i" };

  function aviso(tipo, mensaje, detalle) {
    const zona = $("#avisos");
    if (!zona) return;
    const caja = crear("div", { clase: `aviso aviso-${tipo}` });
    const cerrar = crear("button", { type: "button", clase: "aviso-cerrar", "aria-label": "Cerrar aviso", texto: "×" });
    cerrar.addEventListener("click", () => caja.remove());
    caja.append(
      crear("span", { clase: "aviso-icono", "aria-hidden": "true", texto: ICONOS_AVISO[tipo] || "i" }),
      crear("div", { clase: "aviso-cuerpo" },
        crear("p", { clase: "aviso-texto", texto: texto(mensaje, "") }),
        detalle ? crear("p", { clase: "aviso-detalle", texto: detalle }) : null),
      cerrar);
    zona.prepend(caja);
    while (zona.children.length > MAX_AVISOS) zona.lastElementChild.remove();
    window.setTimeout(() => caja.remove(), tipo === "error" ? 9000 : 5000);
  }

  function marcarConexion(conectado, mensaje) {
    const banda = $("#aviso-conexion");
    const indicador = $("#ind-conexion");
    if (banda) {
      banda.hidden = conectado;
      if (!conectado) fijarTexto(banda, mensaje || "Sin conexión con el servidor; reintentando…");
    }
    if (indicador) {
      indicador.dataset.estado = conectado ? "conectado" : "desconectado";
      fijarTexto(indicador, conectado ? "En línea" : "Sin conexión");
    }
  }

  // =========================================================================
  // 6. Comunicación con la API
  // =========================================================================

  /**
   * Hace una petición y nunca lanza. Devuelve
   * {red, status, statusText, tipoContenido, json, texto}; red=false si no hubo respuesta.
   * Si `cuerpo` es texto se envía tal cual (petición cruda); si no, como JSON.
   */
  async function pedir(metodo, ruta, cuerpo, tiempoLimite = TIEMPO_LIMITE_MS) {
    const controlador = new AbortController();
    const temporizador = window.setTimeout(() => controlador.abort(), tiempoLimite);
    try {
      const opciones = { method: metodo, headers: { Accept: "application/json" }, signal: controlador.signal, cache: "no-store" };
      if (cuerpo !== undefined) {
        opciones.headers["Content-Type"] = "application/json";
        opciones.body = typeof cuerpo === "string" ? cuerpo : JSON.stringify(cuerpo);
      }
      const respuesta = await fetch(ruta, opciones);
      const contenido = await respuesta.text();
      let json = null;
      try {
        json = contenido ? JSON.parse(contenido) : null;
      } catch (_error) {
        json = null;
      }
      return {
        red: true,
        status: respuesta.status,
        statusText: respuesta.statusText,
        tipoContenido: respuesta.headers.get("Content-Type") || "",
        json,
        texto: contenido,
      };
    } catch (error) {
      return { red: false, status: 0, statusText: "", tipoContenido: "", json: null, texto: "", error };
    } finally {
      window.clearTimeout(temporizador);
    }
  }

  function mensajeError(respuesta) {
    if (!respuesta.red) return "No se pudo contactar al servidor (¿sigue en ejecución?).";
    const json = respuesta.json;
    if (esObjeto(json) && typeof json.error === "string" && json.error) return json.error;
    return `El servidor respondió HTTP ${respuesta.status} sin un mensaje legible.`;
  }

  function detalleError(respuesta) {
    if (!respuesta.red) return "";
    const json = objeto(respuesta.json);
    return json.codigo ? `Código: ${texto(json.codigo)} · HTTP ${respuesta.status}` : `HTTP ${respuesta.status}`;
  }

  function ponerOcupado(boton, ocupado) {
    if (!boton) return;
    if (ocupado) {
      ocupados.add(boton);
      boton.disabled = true;
      boton.classList.add("ocupado");
      boton.setAttribute("aria-busy", "true");
    } else {
      ocupados.delete(boton);
      boton.disabled = false;
      boton.classList.remove("ocupado");
      boton.removeAttribute("aria-busy");
    }
  }

  /**
   * Envía una acción a la API con el botón desactivado mientras tanto y
   * muestra el «mensaje» o el «error» de la respuesta.
   * opciones: alExito(datos, json), alError(respuesta), silencioso.
   */
  async function ejecutarAccion(boton, metodo, ruta, cuerpo, opciones = {}) {
    if (boton && ocupados.has(boton)) return null;
    ponerOcupado(boton, true);
    try {
      const respuesta = await pedir(metodo, ruta, cuerpo);
      const json = respuesta.json;
      if (respuesta.red && esObjeto(json) && json.ok === true) {
        if (!opciones.silencioso) aviso("exito", texto(json.mensaje, "Listo."));
        if (opciones.alExito) seguro(opciones.alExito, json.datos ?? {}, json);
        return json;
      }
      aviso("error", mensajeError(respuesta), detalleError(respuesta));
      if (opciones.alError) seguro(opciones.alError, respuesta);
      return null;
    } finally {
      ponerOcupado(boton, false);
      pedirSondeo();
    }
  }

  /** Ejecuta una función atrapando errores para que un panel no rompa a otro. */
  function seguro(funcion, ...argumentos) {
    try {
      return funcion(...argumentos);
    } catch (error) {
      console.error(`Error en ${funcion.name || "una función"}:`, error);
      return undefined;
    }
  }

  // =========================================================================
  // 7. Sondeo del estado
  // =========================================================================

  let temporizadorSondeo = null;
  let sondeando = false;
  let repetirSondeo = false;

  function programarSondeo(ms) {
    window.clearTimeout(temporizadorSondeo);
    temporizadorSondeo = window.setTimeout(sondear, ms);
  }

  /** Pide un sondeo inmediato (tras una acción del usuario). */
  function pedirSondeo() {
    if (sondeando) repetirSondeo = true;
    else programarSondeo(0);
  }

  async function sondear() {
    if (sondeando) {
      repetirSondeo = true;
      return;
    }
    sondeando = true;
    repetirSondeo = false;
    window.clearTimeout(temporizadorSondeo);
    const generacion = cliente.generacion;
    let espera = SONDEO_REPOSO_MS;
    try {
      const respuesta = await pedir("GET", `/api/estado?desde=${cliente.ultimoEvento}`, undefined, 8000);
      if (generacion !== cliente.generacion) {
        repetirSondeo = true; // la simulación se reinició mientras tanto
      } else if (!respuesta.red) {
        marcarConexion(false, "Sin conexión con el servidor; reintentando…");
      } else if (!esObjeto(respuesta.json)) {
        marcarConexion(false, `El servidor respondió algo inesperado (HTTP ${respuesta.status}); reintentando…`);
      } else if (respuesta.json.ok === false) {
        marcarConexion(false, `El servidor no pudo dar el estado: ${texto(respuesta.json.error, "error desconocido")}; reintentando…`);
      } else {
        marcarConexion(true);
        const inst = extraerInstantanea(respuesta.json);
        if (inst) {
          aplicarInstantanea(inst);
          espera = hayActividad(inst) ? SONDEO_ACTIVO_MS : SONDEO_REPOSO_MS;
        }
      }
    } catch (error) {
      console.error("Error al mostrar el estado:", error);
    } finally {
      sondeando = false;
      if (document.hidden) espera = Math.max(espera, SONDEO_REPOSO_MS);
      programarSondeo(repetirSondeo ? 0 : espera);
    }
  }

  /** Acepta {ok, datos: instantánea}, {datos: {estado: instantánea}} o la instantánea sola. */
  function extraerInstantanea(json) {
    if (!esObjeto(json)) return null;
    const datos = json.datos;
    if (esObjeto(datos)) {
      if (!Array.isArray(datos.nodos) && esObjeto(datos.estado)) return datos.estado;
      return datos;
    }
    if (Array.isArray(json.nodos) || typeof json.modo === "string") return json;
    return null;
  }

  function hayActividad(inst) {
    if (objeto(inst.pow).estado === "minando") return true;
    const estadoPos = objeto(inst.pos).estado;
    return typeof estadoPos === "string" && estadoPos !== "INACTIVA" && !ESTADOS_FINALES_POS.includes(estadoPos);
  }

  function firmaDeSimulacion(inst) {
    return `${texto(inst.modo, "")}|${texto(inst.config ?? null, "")}`;
  }

  /** Olvida lo acumulado (bitácora, cadena) porque empezó otra simulación. */
  function reiniciarCliente() {
    cliente.generacion += 1;
    cliente.ultimoEvento = 0;
    cliente.eventos = [];
    cliente.firmaSimulacion = null;
    cliente.version = null;
    cliente.formularioPrellenado = null;
    cliente.borradorApuestas.clear();
    explorador.cadena = null;
    explorador.idCargado = null;
    explorador.idSolicitado = null;
    explorador.hashSolicitado = null;
    explorador.error = null;
    explorador.abiertos.clear();
    explorador.limite = BLOQUES_POR_PAGINA;
    seguro(renderBitacoraCompleta);
    seguro(renderCadena);
  }

  function aplicarInstantanea(inst) {
    const firma = firmaDeSimulacion(inst);
    const ultimo = numero(inst.ultimo_evento);
    const version = numero(inst.version);
    const otraSimulacion = cliente.firmaSimulacion !== null && (
      firma !== cliente.firmaSimulacion
      || (ultimo !== null && ultimo < cliente.ultimoEvento)
      || (version !== null && cliente.version !== null && version < cliente.version));

    if (otraSimulacion) {
      // Otra pestaña (o un reinicio del servidor) creó una simulación nueva:
      // se descarta lo acumulado y se vuelve a pedir la bitácora desde 0.
      reiniciarCliente();
      cliente.firmaSimulacion = firma;
      cliente.version = version;
      cliente.inst = inst;
      aviso("info", "La simulación se reinició (desde otra pestaña o por el servidor); la vista se actualizó.");
      renderTodo(inst);
      pedirSondeo();
      return;
    }

    cliente.firmaSimulacion = firma;
    cliente.version = version;
    cliente.inst = inst;
    seguro(agregarEventos, lista(inst.eventos), ultimo);
    renderTodo(inst);
  }

  function renderTodo(inst) {
    seguro(renderModo, inst);
    seguro(renderEncabezado, inst);
    seguro(actualizarSelectsNodos, inst);
    seguro(prellenarFormulario, inst, false);
    seguro(renderNodos, inst);
    seguro(renderTransacciones, inst);
    seguro(renderPow, inst);
    seguro(renderPos, inst);
    seguro(revisarExplorador, inst);
    seguro(renderLaboratorio);
  }

  // =========================================================================
  // 8. Encabezado y modo
  // =========================================================================

  function modoDe(inst) {
    const modo = inst.modo ?? objeto(inst.config).modo;
    return modo === "pow" || modo === "pos" ? modo : "";
  }

  function renderModo(inst) {
    const modo = modoDe(inst);
    if (document.body.dataset.modo !== modo) document.body.dataset.modo = modo;
  }

  function renderEncabezado(inst) {
    const config = objeto(inst.config);
    const modo = modoDe(inst);
    const indModo = $("#ind-modo");
    fijarTexto(indModo, modo === "pow" ? "PoW · prueba de trabajo" : modo === "pos" ? "PoS · prueba de participación" : "—");
    if (indModo) indModo.className = modo ? `valor-modo modo-${modo}` : "";

    fijarTexto($("#ind-semilla"), texto(config.semilla));
    const nodos = lista(inst.nodos);
    fijarTexto($("#ind-nodos"), nodos.length ? String(nodos.length) : fmtNum(config.num_nodos));

    const altura = $("#ind-altura");
    if (altura) {
      fijarHijos(altura, crear("span", { texto: fmtNum(inst.altura_red) }), " ",
        typeof inst.hash_red === "string" ? elementoHash(inst.hash_red, { inicio: 8, fin: 0, ceros: modo === "pow" }) : null);
    }

    const sincronia = $("#ind-sincronia");
    if (inst.sincronizados === true) ponerInsignia(sincronia, "Todos sincronizados ✓", "ok");
    else if (inst.sincronizados === false) ponerInsignia(sincronia, "Desincronizados ✗", "mal");
    else ponerInsignia(sincronia, "—", "neutro");

    const invariantes = $("#ind-invariantes");
    if (inst.invariantes_ok === true) ponerInsignia(invariantes, "OK ✓", "ok");
    else if (inst.invariantes_ok === false) ponerInsignia(invariantes, "Con fallas ✗", "mal");
    else ponerInsignia(invariantes, "—", "neutro");
    if (invariantes) {
      const problemas = lista(inst.invariantes).map((p) => texto(p, ""));
      invariantes.title = problemas.length ? problemas.join("\n") : "Cadenas válidas, saldos no negativos y conservación del dinero";
    }

    const circulacion = objeto(inst.circulacion);
    const quemado = numero(circulacion.quemado);
    fijarTexto($("#ind-circulacion"),
      `${fmtNum(circulacion.total)}${quemado ? ` (quemado: ${fmtNum(quemado)})` : ""}`);
  }

  // =========================================================================
  // 9. Listas de nodos en los <select>
  // =========================================================================

  function idsDeNodos(inst) {
    return lista(inst && inst.nodos)
      .filter((nodo) => esObjeto(nodo) && typeof nodo.id === "string")
      .map((nodo) => nodo.id);
  }

  function nodoPorId(id) {
    return lista(cliente.inst && cliente.inst.nodos).find((nodo) => esObjeto(nodo) && nodo.id === id) || null;
  }

  /** Rellena cada <select data-nodos> sólo cuando cambia la lista de nodos. */
  function actualizarSelectsNodos(inst) {
    const ids = idsDeNodos(inst);
    const firma = ids.join(",");
    for (const select of $$("select[data-nodos]")) {
      if (select.dataset.firmaNodos === firma) continue;
      const anterior = select.value;
      const opciones = [];
      if (select.dataset.nodos === "opcional") {
        opciones.push(crear("option", { value: "", texto: select.dataset.textoVacio || "(ninguno)" }));
      }
      for (const id of ids) opciones.push(crear("option", { value: id, texto: id }));
      select.replaceChildren(...opciones);
      if (anterior && ids.includes(anterior)) select.value = anterior;
      else if (select.dataset.defecto !== undefined && ids[Number(select.dataset.defecto)]) {
        select.value = ids[Number(select.dataset.defecto)];
      }
      select.dataset.firmaNodos = firma;
    }
  }

  // =========================================================================
  // 10. Validación en el navegador (el servidor siempre vuelve a validar)
  // =========================================================================

  /** Clasifica un texto: vacio | entero | enorme | decimal | invalido. */
  function analizarEntero(crudo) {
    const t = String(crudo ?? "").trim();
    if (t === "") return { tipo: "vacio" };
    if (/^[+-]?\d{1,15}$/.test(t)) return { tipo: "entero", valor: Number(t) };
    if (/^[+-]?\d+$/.test(t)) return { tipo: "enorme", texto: t };
    if (/^[+-]?(\d+[.,]\d*|[.,]\d+)$/.test(t)) return { tipo: "decimal" };
    return { tipo: "invalido", texto: t };
  }

  /** Entero en [minimo, maximo] ⇒ {valor} o {error}. */
  function validarEntero(crudo, etiqueta, minimo, maximo) {
    const analisis = analizarEntero(crudo);
    const sujeto = mayuscula(etiqueta);
    if (analisis.tipo === "vacio") return { error: `Falta ${etiqueta}` };
    if (analisis.tipo === "decimal") return { error: `${sujeto} debe ser un número entero, sin decimales` };
    if (analisis.tipo === "invalido") return { error: `${sujeto} debe ser un número entero (recibido: «${recortar(analisis.texto, 40)}»)` };
    if (analisis.tipo === "enorme") return { error: `${sujeto} debe estar entre ${minimo} y ${maximo} (recibido: ${recortar(analisis.texto, 40)})` };
    if (analisis.valor < minimo || analisis.valor > maximo) {
      return { error: `${sujeto} debe estar entre ${minimo} y ${maximo} (recibido: ${analisis.valor})` };
    }
    return { valor: analisis.valor };
  }

  /** Monto: entero de 1 a 10^12, con los mensajes del contrato. */
  function validarMonto(crudo) {
    const analisis = analizarEntero(crudo);
    if (analisis.tipo === "vacio") return { error: "El monto es obligatorio" };
    if (analisis.tipo === "decimal") return { error: "El monto debe ser un número entero, sin decimales" };
    if (analisis.tipo === "invalido") return { error: `El monto debe ser un número entero (recibido: «${recortar(analisis.texto, 40)}»)` };
    if (analisis.tipo === "enorme") {
      return analisis.texto.startsWith("-")
        ? { error: `El monto no puede ser negativo (recibido: ${recortar(analisis.texto, 40)})` }
        : { error: `El monto es demasiado grande (máximo ${MAX_MONTO})` };
    }
    if (analisis.valor < 0) return { error: `El monto no puede ser negativo (recibido: ${analisis.valor})` };
    if (analisis.valor === 0) return { error: "El monto debe ser mayor que cero" };
    if (analisis.valor > MAX_MONTO) return { error: `El monto es demasiado grande (máximo ${MAX_MONTO})` };
    return { valor: analisis.valor };
  }

  function validarTexto(crudo, etiqueta, maximo) {
    const t = String(crudo ?? "").trim();
    if (t === "") return { error: `Falta ${etiqueta}` };
    if (t.length > maximo) return { error: `${mayuscula(etiqueta)} debe tener como máximo ${maximo} caracteres (tiene ${t.length})` };
    if (/[\u0000-\u001f\u007f]/.test(t)) return { error: `${mayuscula(etiqueta)} no puede tener caracteres de control` };
    return { valor: t };
  }

  function marcarError(control, mensaje) {
    if (!control) return;
    control.setAttribute("aria-invalid", "true");
    const contenedor = control.closest(".campo") || control.parentElement;
    if (!contenedor) return;
    let nota = contenedor.querySelector(".error-campo");
    if (!nota) {
      nota = crear("small", { clase: "error-campo" });
      contenedor.append(nota);
    }
    nota.textContent = mensaje;
  }

  function limpiarError(control) {
    if (!control) return;
    control.removeAttribute("aria-invalid");
    const contenedor = control.closest(".campo") || control.parentElement;
    const nota = contenedor && contenedor.querySelector(".error-campo");
    if (nota) nota.remove();
  }

  function limpiarErrores(formulario) {
    if (!formulario) return;
    for (const control of $$("[aria-invalid]", formulario)) limpiarError(control);
  }

  function botonEnvio(formulario) {
    return formulario ? formulario.querySelector('button[type="submit"]') : null;
  }

  /** Valida un entero si la validación del navegador está activa; si no, devuelve el texto crudo. */
  function leerEnteroCampo(control, etiqueta, minimo, maximo) {
    limpiarError(control);
    const crudo = control ? control.value : "";
    if (!cliente.validarEnNavegador) return { valor: crudo };
    const resultado = validarEntero(crudo, etiqueta, minimo, maximo);
    if (resultado.error) marcarError(control, resultado.error);
    return resultado;
  }

  // =========================================================================
  // 11. Panel «Nueva simulación»
  // =========================================================================

  function actualizarFormularioSegunModo() {
    const formulario = $("#form-simulacion");
    if (!formulario) return;
    const campos = formulario.elements;
    formulario.dataset.modoForm = campos.modo.value === "pos" ? "pos" : "pow";
    campos.num_validadores.disabled = campos.seleccion_validadores.value !== "aleatorio";
    campos.alfa_porcentaje.disabled = campos.regla_castigo.value !== "B";
  }

  function prellenarFormulario(inst, forzar) {
    if (!forzar && (cliente.formularioSucio || cliente.formularioPrellenado === cliente.firmaSimulacion)) return;
    const formulario = $("#form-simulacion");
    if (!formulario || !inst) return;
    const config = objeto(inst.config);
    const modo = modoDe(inst);
    if (modo) formulario.elements.modo.value = modo;
    for (const campo of CAMPOS_SIMULACION) {
      const control = formulario.elements[campo.nombre];
      const valor = config[campo.nombre];
      if (control && valor !== undefined && valor !== null && typeof valor !== "object") control.value = String(valor);
    }
    limpiarErrores(formulario);
    actualizarFormularioSegunModo();
    cliente.formularioPrellenado = cliente.firmaSimulacion;
    cliente.formularioSucio = false;
  }

  /** Lee el formulario ⇒ {cuerpo, errores}. Sólo envía los campos del modo elegido. */
  function leerFormularioSimulacion(formulario) {
    const modo = formulario.elements.modo.value;
    const cuerpo = { modo };
    const errores = [];
    limpiarErrores(formulario);
    for (const campo of CAMPOS_SIMULACION) {
      if (campo.modo !== "comun" && campo.modo !== modo) continue;
      const control = formulario.elements[campo.nombre];
      if (!control || control.disabled) continue;
      const crudo = control.value;
      if (!cliente.validarEnNavegador || campo.tipo === "opcion") {
        cuerpo[campo.nombre] = crudo;
        continue;
      }
      const resultado = campo.tipo === "texto"
        ? validarTexto(crudo, campo.etiqueta, campo.maximo)
        : validarEntero(crudo, campo.etiqueta, campo.minimo, campo.maximo);
      if (resultado.error) {
        marcarError(control, resultado.error);
        errores.push(resultado.error);
      } else {
        cuerpo[campo.nombre] = resultado.valor;
      }
    }
    if (cliente.validarEnNavegador && typeof cuerpo.num_validadores === "number" && typeof cuerpo.num_nodos === "number"
        && cuerpo.num_validadores > cuerpo.num_nodos) {
      const error = `El número de validadores (${cuerpo.num_validadores}) no puede ser mayor que el número de nodos (${cuerpo.num_nodos})`;
      marcarError(formulario.elements.num_validadores, error);
      errores.push(error);
    }
    return { cuerpo, errores };
  }

  async function crearSimulacion(evento) {
    evento.preventDefault();
    const formulario = evento.currentTarget;
    const { cuerpo, errores } = leerFormularioSimulacion(formulario);
    if (errores.length) {
      aviso("error", errores[0], errores.length > 1 ? `Hay ${errores.length} campos por corregir.` : "");
      return;
    }
    await ejecutarAccion(botonEnvio(formulario), "POST", "/api/simulacion", cuerpo, {
      alExito: (datos) => {
        reiniciarCliente();
        cliente.formularioSucio = false;
        const inst = extraerInstantanea({ ok: true, datos });
        if (inst && (Array.isArray(inst.nodos) || inst.modo)) aplicarInstantanea(inst);
      },
    });
  }

  // =========================================================================
  // 12. Tabla de nodos
  // =========================================================================

  function crearFilaNodo(id) {
    const celdas = {};
    const fila = crear("tr", { datos: { nodo: id } });
    const columnas = [
      ["id", "id-nodo"], ["estado", ""], ["altura", "num"], ["hash", ""], ["sinc", "centro"],
      ["disponible", "num"], ["recompensas", ""],
      ["nonce", "num solo-pow mono"], ["intentos", "num solo-pow"], ["hashMinero", "solo-pow"],
      ["apuesta", "num solo-pos"], ["probabilidad", "solo-pos"], ["voto", "centro solo-pos"],
      ["acciones", "acciones"],
    ];
    for (const [clave, clase] of columnas) {
      celdas[clave] = crear("td", { clase });
      fila.append(celdas[clave]);
    }
    celdas.id.textContent = id;

    const verCadena = crear("button", { type: "button", clase: "boton chico", texto: "Cadena", titulo: `Ver la cadena de ${id}` });
    const trampa = crear("select", { clase: "chico", "aria-label": `Trampa de ${id}` });
    const deshonesto = crear("button", { type: "button", clase: "boton chico" });
    const conexion = crear("button", { type: "button", clase: "boton chico" });
    verCadena.addEventListener("click", () => abrirExplorador(id));
    deshonesto.addEventListener("click", () => alternarDeshonesto(id, deshonesto, trampa));
    trampa.addEventListener("change", () => cambiarTrampa(id, trampa));
    conexion.addEventListener("click", () => alternarConexion(id, conexion));
    celdas.acciones.append(crear("div", { clase: "acciones-nodo" }, trampa, deshonesto, verCadena, conexion));
    return { fila, celdas, controles: { trampa, deshonesto, conexion } };
  }

  function insigniasNodo(nodo, inst) {
    const resultado = [];
    const pow = objeto(inst.pow);
    const pos = objeto(inst.pos);
    if (nodo.conectado === false) resultado.push(insignia("Desconectado", "gris"));
    if (nodo.deshonesto === true) {
      resultado.push(insignia(`Deshonesto${nodo.trampa ? `: ${TRAMPAS[nodo.trampa] || texto(nodo.trampa)}` : ""}`, "mal"));
    }
    const minero = esObjeto(nodo.minero) ? nodo.minero : null;
    if (minero && minero.encontro === true) resultado.push(insignia("Encontró hash", "ok"));
    else if (minero && pow.estado === "minando" && nodo.conectado !== false) resultado.push(insignia("Minando", "pow"));
    const validador = esObjeto(nodo.validador) ? nodo.validador : null;
    if (validador && validador.excluido === true) resultado.push(insignia("Castigado", "mal"));
    else if (validador) resultado.push(insignia("Validador", "pos"));
    if (pos.proponente === nodo.id && typeof pos.estado === "string" && pos.estado !== "INACTIVA") {
      resultado.push(insignia("Proponente", "acento"));
    }
    if (!resultado.length) resultado.push(insignia("Activo", "neutro"));
    return resultado;
  }

  function vistaDisponible(nodo) {
    const contenedor = crear("div", { clase: "disponible" }, crear("strong", { texto: fmtNum(nodo.disponible) }));
    const saldo = numero(nodo.saldo_cadena);
    if (saldo !== null && saldo !== numero(nodo.disponible)) {
      contenedor.append(crear("small", { clase: "tenue", texto: `de ${fmtNum(saldo)} en cadena` }));
    }
    contenedor.title = [
      `Saldo en la cadena: ${fmtNum(nodo.saldo_cadena)}`,
      `Por enviar (pendientes): ${fmtNum(nodo.pendiente_salida)}`,
      `Apuesta bloqueada: ${fmtNum(nodo.apuesta_bloqueada)}`,
      `Castigo pendiente: ${fmtNum(nodo.castigo_pendiente)}`,
      `Disponible: ${fmtNum(nodo.disponible)}`,
    ].join("\n");
    return contenedor;
  }

  function vistaRecompensas(nodo) {
    const pendientes = lista(nodo.recompensas_pendientes).filter(esObjeto);
    const total = numero(nodo.total_recompensas_pendientes) ?? sumar(pendientes, (p) => p.monto);
    if (!pendientes.length && !total) return crear("span", { clase: "vacio-dato", texto: "—" });
    const contenedor = crear("div", { clase: "recompensas" }, crear("strong", { texto: `${fmtNum(total)} sin madurar` }));
    const elementos = crear("ul", { clase: "lista-compacta" });
    for (const p of pendientes.slice(0, 3)) {
      elementos.append(crear("li", {
        texto: `#${texto(p.bloque)}: ${fmtNum(p.monto)} · ${textoFaltan(p.faltan)}`,
        titulo: `Recompensa del bloque ${texto(p.bloque)}: madura con 6 confirmaciones`,
      }));
    }
    if (pendientes.length > 3) elementos.append(crear("li", { clase: "tenue", texto: `y ${pendientes.length - 3} más` }));
    contenedor.append(elementos);
    return contenedor;
  }

  function actualizarOpcionesTrampa(select, modo, trampaActual) {
    const claves = modo === "pos" ? Object.keys(TRAMPAS) : TRAMPAS_PROPONENTE.slice();
    if (trampaActual && !claves.includes(trampaActual)) claves.push(trampaActual);
    const firma = claves.join(",");
    if (select.dataset.firma === firma) return;
    const anterior = select.value;
    select.replaceChildren(...claves.map((clave) => crear("option", {
      value: clave,
      texto: TRAMPAS[clave] || clave,
      titulo: AYUDA_TRAMPAS[clave],
    })));
    if (claves.includes(anterior)) select.value = anterior;
    select.dataset.firma = firma;
  }

  function actualizarFilaNodo(registro, nodo, inst) {
    const { fila, celdas, controles } = registro;
    const modo = modoDe(inst);
    fila.classList.toggle("desconectado", nodo.conectado === false);
    fila.classList.toggle("deshonesto", nodo.deshonesto === true);

    fijarHijos(celdas.estado, crear("div", { clase: "insignias" }, insigniasNodo(nodo, inst)));
    fijarTexto(celdas.altura, fmtNum(nodo.altura));
    fijarHijos(celdas.hash, elementoHash(nodo.ultimo_hash, { ceros: modo === "pow" }));
    if (nodo.sincronizado === true) fijarHijos(celdas.sinc, crear("span", { clase: "marca-ok", texto: "✓", titulo: "Sincronizado" }));
    else if (nodo.sincronizado === false) fijarHijos(celdas.sinc, crear("span", { clase: "marca-mal", texto: "✗", titulo: "Desincronizado" }));
    else fijarTexto(celdas.sinc, "—");
    fijarHijos(celdas.disponible, vistaDisponible(nodo));
    fijarHijos(celdas.recompensas, vistaRecompensas(nodo));

    const minero = esObjeto(nodo.minero) ? nodo.minero : null;
    fijarTexto(celdas.nonce, minero ? fmtNum(minero.nonce) : "—");
    fijarTexto(celdas.intentos, minero ? fmtNum(minero.intentos) : "—");
    fijarHijos(celdas.hashMinero, minero
      ? crear("span", { clase: minero.encontro === true ? "hash-encontrado" : "" },
        elementoHash(minero.ultimo_hash, { ceros: true }), minero.encontro === true ? " ✓" : null)
      : crear("span", { clase: "vacio-dato", texto: "—" }));

    const validador = esObjeto(nodo.validador) ? nodo.validador : null;
    fijarTexto(celdas.apuesta, validador ? fmtNum(validador.apuesta) : "—");
    fijarHijos(celdas.probabilidad, validador && !validador.excluido
      ? barraProbabilidad(validador.probabilidad)
      : crear("span", { clase: "vacio-dato", texto: "—" }));
    fijarHijos(celdas.voto, elementoVoto(validador ? validador.voto : null));

    // Acciones: se actualizan sin recrear los controles.
    actualizarOpcionesTrampa(controles.trampa, modo, nodo.trampa);
    if (nodo.deshonesto === true && nodo.trampa && document.activeElement !== controles.trampa) {
      controles.trampa.value = nodo.trampa;
    }
    fijarTexto(controles.deshonesto, nodo.deshonesto === true ? "Volver honesto" : "Hacer deshonesto");
    controles.deshonesto.classList.toggle("peligro", nodo.deshonesto !== true);
    fijarTexto(controles.conexion, nodo.conectado === false ? "Reconectar" : "Desconectar");
  }

  function renderNodos(inst) {
    const cuerpo = $("#cuerpo-nodos");
    if (!cuerpo) return;
    const nodos = lista(inst.nodos).filter((nodo) => esObjeto(nodo) && typeof nodo.id === "string");
    const registros = sincronizarFilas(cuerpo, filasNodos, nodos.map((nodo) => nodo.id), crearFilaNodo);
    registros.forEach((registro, i) => seguro(actualizarFilaNodo, registro, nodos[i], inst));
    const vacio = $("#nodos-vacio");
    if (vacio) {
      vacio.hidden = nodos.length > 0;
      fijarTexto(vacio, cliente.inst ? "El servidor no envió nodos." : "Esperando datos del servidor…");
    }
    const conectados = nodos.filter((n) => n.conectado !== false).length;
    const deshonestos = nodos.filter((n) => n.deshonesto === true).length;
    fijarTexto($("#nodos-resumen"),
      nodos.length ? `${nodos.length} nodos · ${conectados} conectados · ${deshonestos} deshonestos` : "");
  }

  async function alternarDeshonesto(id, boton, select) {
    const nodo = nodoPorId(id);
    const serDeshonesto = !(nodo && nodo.deshonesto === true);
    const cuerpo = serDeshonesto ? { deshonesto: true, trampa: select.value } : { deshonesto: false };
    await ejecutarAccion(boton, "POST", `/api/nodos/${encodeURIComponent(id)}/deshonesto`, cuerpo);
  }

  /** Si el nodo ya es deshonesto, cambiar el menú cambia su trampa. */
  async function cambiarTrampa(id, select) {
    const nodo = nodoPorId(id);
    if (!nodo || nodo.deshonesto !== true || nodo.trampa === select.value) return;
    await ejecutarAccion(select, "POST", `/api/nodos/${encodeURIComponent(id)}/deshonesto`, { deshonesto: true, trampa: select.value });
  }

  async function alternarConexion(id, boton) {
    const nodo = nodoPorId(id);
    const conectar = Boolean(nodo && nodo.conectado === false);
    await ejecutarAccion(boton, "POST", `/api/nodos/${encodeURIComponent(id)}/conexion`, { conectado: conectar });
  }

  // =========================================================================
  // 13. Transacciones
  // =========================================================================

  function renderTransacciones(inst) {
    const pendientes = lista(inst.pendientes).filter(esObjeto);
    fijarTexto($("#pendientes-cuenta"), String(pendientes.length));

    const cuerpo = $("#cuerpo-pendientes");
    const firma = pendientes.map((tx) => texto(tx.id ?? tx.firma, "")).join(",");
    if (cuerpo && cuerpo.dataset.firma !== firma) {
      const filas = pendientes.slice(0, MAX_PENDIENTES_VISIBLES).map((tx) => crear("tr", {},
        celda(elementoHash(tx.id, { inicio: 8, fin: 0 })),
        celda(`${texto(tx.emisor)} → ${texto(tx.receptor)}`),
        celda(fmtNum(tx.monto), "num"),
        celda(fmtFecha(tx.timestamp), "tenue"),
        celda(elementoHash(tx.firma, { inicio: 8, fin: 0 }))));
      if (pendientes.length > MAX_PENDIENTES_VISIBLES) {
        filas.push(filaVacia(5, `y ${pendientes.length - MAX_PENDIENTES_VISIBLES} más…`));
      }
      cuerpo.replaceChildren(...filas);
      cuerpo.dataset.firma = firma;
    }
    const vacio = $("#pendientes-vacio");
    if (vacio) vacio.hidden = pendientes.length > 0;

    seguro(mostrarDisponibleEmisor);
    seguro(renderCastigosPendientes, inst);
  }

  function mostrarDisponibleEmisor() {
    const formulario = $("#form-transaccion");
    const ayuda = $("#tx-disponible");
    if (!formulario || !ayuda) return;
    const nodo = nodoPorId(formulario.elements.emisor.value);
    if (!nodo) {
      fijarTexto(ayuda, "");
      return;
    }
    const partes = [`Disponible de ${nodo.id}: ${fmtNum(nodo.disponible)} monedas`];
    const pendiente = numero(nodo.total_recompensas_pendientes);
    if (pendiente) partes.push(`${fmtNum(pendiente)} en recompensas sin madurar (no se pueden gastar)`);
    fijarTexto(ayuda, partes.join(" · "));
  }

  function renderCastigosPendientes(inst) {
    const contenedor = $("#lista-castigos-pendientes");
    if (!contenedor) return;
    const castigos = lista(inst.castigos_pendientes).filter(esObjeto);
    const firma = texto(castigos, "");
    if (contenedor.dataset.firma === firma) return;
    contenedor.dataset.firma = firma;
    if (!castigos.length) {
      fijarHijos(contenedor, crear("li", { clase: "vacio", texto: "Ninguno." }));
      return;
    }
    fijarHijos(contenedor, castigos.map((c) => crear("li", {}, textoCastigo(c))));
  }

  function textoCastigo(c) {
    const monto = c.monto ?? c.castigo;
    const partes = [`${texto(c.nodo ?? c.id)}: −${fmtNum(monto)} quemadas`];
    if (c.regla) partes.push(`regla ${texto(c.regla)}`);
    if (c.apuesta !== undefined) partes.push(`apuesta ${fmtNum(c.apuesta)}`);
    if (c.intento !== undefined) partes.push(`intento ${texto(c.intento)}`);
    if (c.valor_transacciones !== undefined) partes.push(`valor de las tx ${fmtNum(c.valor_transacciones)}`);
    const linea = partes.join(" · ");
    return c.motivo ? `${linea} — ${texto(c.motivo)}` : linea;
  }

  async function enviarTransaccion(evento) {
    evento.preventDefault();
    const formulario = evento.currentTarget;
    const campos = formulario.elements;
    limpiarErrores(formulario);
    const emisor = campos.emisor.value;
    const receptor = campos.receptor.value;
    let monto = campos.monto.value;
    if (cliente.validarEnNavegador) {
      const errores = [];
      if (!emisor) errores.push("Elija el nodo emisor");
      if (!receptor) errores.push("Elija el nodo receptor");
      if (emisor && emisor === receptor) {
        errores.push("El emisor y el receptor deben ser nodos distintos");
        marcarError(campos.receptor, "Debe ser distinto del emisor");
      }
      const resultado = validarMonto(monto);
      if (resultado.error) {
        errores.push(resultado.error);
        marcarError(campos.monto, resultado.error);
      } else {
        monto = resultado.valor;
      }
      if (errores.length) {
        aviso("error", errores[0]);
        return;
      }
    }
    await ejecutarAccion(botonEnvio(formulario), "POST", "/api/transacciones", { emisor, receptor, monto });
  }

  async function generarAleatorias(evento) {
    evento.preventDefault();
    const formulario = evento.currentTarget;
    const resultado = leerEnteroCampo(formulario.elements.cantidad, "la cantidad", 1, 50);
    if (resultado.error) {
      aviso("error", resultado.error);
      return;
    }
    await ejecutarAccion(botonEnvio(formulario), "POST", "/api/transacciones/aleatorias", { cantidad: resultado.valor });
  }

  // =========================================================================
  // 14. Proof of Work
  // =========================================================================

  function renderPow(inst) {
    const pow = objeto(inst.pow);
    const config = objeto(inst.config);
    const estado = typeof pow.estado === "string" ? pow.estado : "inactivo";
    const [etiqueta, clase] = ESTADOS_POW[estado] || [estado, "neutro"];
    ponerInsignia($("#pow-estado"), etiqueta, clase);

    const activa = estado !== "inactivo";
    fijarTexto($("#pow-numero"), activa && numero(pow.numero) !== null ? `#${pow.numero}` : "—");
    const ronda = numero(pow.ronda);
    const maxRondas = numero(pow.max_rondas) ?? numero(config.max_rondas);
    fijarTexto($("#pow-ronda"), `${activa ? fmtNum(ronda) : "—"} / ${fmtNum(maxRondas)}`);
    const barra = $("#pow-barra-rondas");
    if (barra) {
      barra.style.width = `${activa ? porcentaje(ronda, maxRondas) : 0}%`;
      barra.classList.toggle("agotada", estado === "agotada");
    }

    const dificultad = numero(pow.dificultad) ?? numero(config.dificultad);
    const d = dificultad === null ? null : limitar(Math.trunc(dificultad), 0, 64);
    fijarTexto($("#pow-dificultad"), d === null ? "—" : `${d} (el hash empieza con ${"0".repeat(d)})`);
    fijarTexto($("#pow-esperados"), d === null ? "—" : `≈ 16^${d} = ${fmtNum(16 ** d)}`);
    fijarTexto($("#pow-k"), fmtNum(numero(pow.k) ?? numero(config.intentos_por_ronda)));

    const mineros = lista(pow.mineros).filter(esObjeto);
    fijarTexto($("#pow-intentos"), mineros.length ? fmtNum(sumar(mineros, (m) => m.intentos)) : "—");
    const txs = Array.isArray(pow.txs) ? pow.txs.length : numero(pow.num_transacciones);
    fijarTexto($("#pow-txs"), activa && txs !== null ? fmtNum(txs) : "—");
    fijarTexto($("#pow-restantes"), activa ? fmtNum(pow.bloques_restantes) : "—");

    fijarHijos($("#pow-ganador"), seguro(vistaGanadorPow, pow) || null);
    fijarHijos($("#pow-empates"), seguro(vistaEmpatesPow, pow) || null);
  }

  function ultimoEventoDeTipo(tipo) {
    for (let i = cliente.eventos.length - 1; i >= 0; i -= 1) {
      if (cliente.eventos[i].tipo === tipo) return cliente.eventos[i];
    }
    return null;
  }

  /** Normaliza un hallazgo con nombres de campo posibles. */
  function normalizarHallazgo(datos) {
    const base = esObjeto(datos.ganador) ? { ...datos, ...datos.ganador } : datos;
    const candidatos = [base.minero, base.ganador, base.nodo, base.proponente, base.id];
    const minero = candidatos.find((valor) => typeof valor === "string" && valor !== "");
    return {
      minero: minero || null,
      numero: base.numero ?? base.bloque ?? null,
      nonce: base.nonce ?? null,
      hash: typeof base.hash === "string" ? base.hash : null,
      ronda: base.ronda ?? null,
    };
  }

  /** Ganador del último bloque: de la sesión, de su historial o de la bitácora. */
  function ultimoGanadorPow(pow) {
    const directo = pow.ganador ?? pow.ultimo_ganador;
    if (typeof directo === "string" && directo) return { ...normalizarHallazgo({ minero: directo }), mensaje: null };
    if (esObjeto(directo)) return { ...normalizarHallazgo(directo), mensaje: null };
    const historial = lista(pow.historial);
    for (let i = historial.length - 1; i >= 0; i -= 1) {
      const entrada = historial[i];
      if (esObjeto(entrada) && (entrada.ganador || entrada.minero)) return { ...normalizarHallazgo(entrada), mensaje: null };
    }
    const evento = ultimoEventoDeTipo("bloque_minado");
    if (evento) return { ...normalizarHallazgo(objeto(evento.datos)), mensaje: texto(evento.mensaje, "") };
    return null;
  }

  function vistaGanadorPow(pow) {
    const ganador = ultimoGanadorPow(pow);
    if (!ganador) return crear("p", { clase: "vacio", texto: "Todavía no se ha minado ningún bloque en esta simulación." });
    const caja = crear("div", { clase: "ganador" });
    if (ganador.minero) {
      const bloque = ganador.numero !== null ? `el bloque #${texto(ganador.numero)}` : "un bloque";
      const ronda = ganador.ronda !== null ? ` en la ronda ${texto(ganador.ronda)}` : "";
      caja.append(crear("p", {}, crear("strong", { texto: ganador.minero }), ` encontró ${bloque}${ronda}.`));
    }
    if (ganador.nonce !== null || ganador.hash) {
      const datos = crear("dl", { clase: "datos-rejilla compacta" });
      agregarDato(datos, "Nonce", texto(ganador.nonce));
      agregarDato(datos, "Hash", elementoHash(ganador.hash, { ceros: true }));
      caja.append(datos);
    }
    if (ganador.mensaje) caja.append(crear("p", { clase: "tenue", texto: ganador.mensaje }));
    return caja;
  }

  function vistaEmpatesPow(pow) {
    let hallazgos = lista(pow.ultimo_empate).filter(esObjeto);
    let mensaje = null;
    if (!hallazgos.length) {
      const evento = ultimoEventoDeTipo("empate");
      if (evento) {
        const datos = objeto(evento.datos);
        hallazgos = lista(datos.hallazgos ?? datos.empatados ?? datos.mineros).filter(esObjeto);
        mensaje = texto(evento.mensaje, "");
      }
    }
    if (!hallazgos.length) {
      return crear("p", {
        clase: mensaje ? "" : "vacio",
        texto: mensaje || "Sin empates por ahora. Ocurren cuando dos o más mineros encuentran un hash válido en la misma ronda (más probable con dificultad baja).",
      });
    }
    const normalizados = hallazgos.map((h) => ({ ...normalizarHallazgo(h), indice: numero(h.indice) ?? 0 }));
    const ganador = normalizados.reduce((mejor, h) => {
      if (!mejor) return h;
      const hashH = h.hash || "";
      const hashMejor = mejor.hash || "";
      if (hashH < hashMejor || (hashH === hashMejor && h.indice < mejor.indice)) return h;
      return mejor;
    }, null);
    const cuerpo = crear("tbody", {}, normalizados.map((h) => crear("tr", { clase: h === ganador ? "fila-ganadora" : "" },
      celda(texto(h.minero)),
      celda(texto(h.nonce), "num mono"),
      celda(elementoHash(h.hash, { ceros: true })),
      celda(h === ganador ? insignia("Gana: hash menor", "ok") : insignia("Pierde", "gris")))));
    return crear("div", {},
      mensaje ? crear("p", { texto: mensaje }) : crear("p", { texto: `${normalizados.length} mineros encontraron un hash válido en la misma ronda.` }),
      crear("div", { clase: "tabla-desplazable" }, crear("table", { clase: "tabla" },
        crear("thead", {}, crear("tr", {}, crear("th", { texto: "Minero" }), crear("th", { clase: "num", texto: "Nonce" }),
          crear("th", { texto: "Hash" }), crear("th", { texto: "Resultado" }))),
        cuerpo)),
      crear("p", { clase: "ayuda", texto: "Desempate: gana el hash menor; si fueran idénticos, el minero de menor índice." }));
  }

  async function minar(boton, bloques, autoTx) {
    await ejecutarAccion(boton, "POST", "/api/pow/minar", { bloques, auto_tx: autoTx });
  }

  // =========================================================================
  // 15. Proof of Stake
  // =========================================================================

  function renderPos(inst) {
    const pos = objeto(inst.pos);
    const estado = typeof pos.estado === "string" ? pos.estado : "INACTIVA";
    cliente.estadoPosMostrado = estado;
    seguro(renderRondaPos, pos, estado);
    seguro(renderDiagramaPos, estado);
    seguro(renderValidadores, inst, pos, estado);
    seguro(renderVotacion, pos, estado);
    seguro(renderHistorialPos, pos);
    seguro(renderCastigosRonda, pos);
  }

  function renderRondaPos(pos, estado) {
    ponerInsignia($("#pos-estado"), estado === "INACTIVA" ? "Sin ronda" : estado, CLASE_ESTADO_POS[estado] || "neutro");
    fijarTexto($("#pos-explicacion"), EXPLICACION_POS[estado] || `Estado actual: ${estado}`);
    const activa = estado !== "INACTIVA";
    fijarTexto($("#pos-numero"), activa && pos.numero !== undefined && pos.numero !== null ? `#${texto(pos.numero)}` : "—");
    fijarTexto($("#pos-intento"), activa ? texto(pos.intento) : "—");
    fijarTexto($("#pos-restantes"), activa ? texto(pos.rondas_restantes) : "—");
    fijarHijos($("#pos-proponente"), activa && pos.proponente
      ? crear("strong", { clase: "proponente", texto: texto(pos.proponente) })
      : "—");

    const candidato = objeto(pos.candidato);
    if (activa && Object.keys(candidato).length) {
      const txs = lista(candidato.transacciones).length;
      const recompensa = objeto(candidato.recompensa);
      fijarTexto($("#pos-candidato"), `${txs} transacciones · recompensa ${fmtNum(recompensa.monto)}`);
    } else {
      fijarTexto($("#pos-candidato"), "—");
    }
    fijarHijos($("#pos-hash-candidato"), activa ? elementoHash(pos.hash_candidato) : "—");
    fijarHijos($("#pos-hash-anterior"), activa ? elementoHash(pos.hash_anterior) : "—");
    fijarHijos($("#pos-resultado"), activa ? vistaResultadoPos(pos.resultado, estado) : null);

    const siguiente = SIGUIENTE_ESTADO_POS[estado];
    fijarTexto($("#btn-pos-avanzar"), siguiente ? `Avanzar → ${siguiente}` : "Avanzar");
    const automatico = pos.automatico === true;
    const botonAuto = $("#btn-pos-automatico");
    if (botonAuto) {
      fijarTexto(botonAuto, automatico ? "Automático: activado" : "Automático: desactivado");
      botonAuto.setAttribute("aria-pressed", String(automatico));
      botonAuto.classList.toggle("activo", automatico);
    }
  }

  function vistaResultadoPos(resultado, estado) {
    if (resultado === null || resultado === undefined) return null;
    const clase = estado === "ACEPTADO" ? "ok" : estado === "RECHAZADO" || estado === "SIN_VALIDADORES" ? "mal" : "info";
    if (typeof resultado !== "object") return crear("div", { clase: `nota-resultado ${clase}`, texto: texto(resultado) });
    const datos = objeto(resultado);
    const titulo = texto(datos.mensaje ?? datos.motivo ?? datos.estado, "Resultado de la ronda");
    return crear("div", { clase: `nota-resultado ${clase}` }, crear("p", { texto: titulo }), detallesJson(resultado, "Detalles"));
  }

  function renderDiagramaPos(estado) {
    const ordenActual = ORDEN_ESTADOS_POS[estado];
    for (const elemento of $$("#panel-pos-detalle [data-estado]")) {
      const propio = elemento.dataset.estado;
      const esActual = propio === estado;
      const orden = ORDEN_ESTADOS_POS[propio];
      const pasado = !esActual && ordenActual !== undefined && orden !== undefined && orden < ordenActual && propio !== "ACEPTADO";
      elemento.classList.toggle("actual", esActual);
      elemento.classList.toggle("pasado", pasado);
      if (esActual) elemento.setAttribute("aria-current", "step");
      else elemento.removeAttribute("aria-current");
    }
  }

  function crearFilaValidador(clave) {
    const id = clave.replace(/^x:/, "");
    const celdas = {
      id: crear("td", { clase: "id-nodo", texto: id }),
      disponible: crear("td", { clase: "num" }),
      apuesta: crear("td", { clase: "num" }),
      probabilidad: crear("td"),
      voto: crear("td", { clase: "centro" }),
      papel: crear("td"),
    };
    const entrada = crear("input", { type: "text", inputmode: "numeric", clase: "entrada-apuesta", "aria-label": `Apuesta de ${id}` });
    const textoApuesta = crear("span");
    entrada.addEventListener("input", () => {
      cliente.borradorApuestas.set(id, entrada.value);
      limpiarError(entrada);
      if (cliente.inst) seguro(renderValidadores, cliente.inst, objeto(cliente.inst.pos), cliente.estadoPosMostrado);
    });
    entrada.addEventListener("keydown", (evento) => {
      if (evento.key === "Enter") guardarApuestas($("#btn-guardar-apuestas"));
    });
    celdas.apuesta.append(entrada, textoApuesta);
    const fila = crear("tr", {}, Object.values(celdas));
    return { fila, celdas, entrada, textoApuesta };
  }

  /** Probabilidades con las apuestas escritas (aún sin guardar). */
  function calcularVistaPrevia(activos) {
    if (!cliente.borradorApuestas.size) return null;
    const valores = [];
    for (const validador of activos) {
      let apuesta = numero(validador.apuesta) ?? 0;
      const borrador = cliente.borradorApuestas.get(validador.id);
      if (borrador !== undefined) {
        const analisis = analizarEntero(borrador);
        if (analisis.tipo !== "entero" || analisis.valor <= 0) return null;
        apuesta = analisis.valor;
      }
      valores.push([validador.id, apuesta]);
    }
    const total = valores.reduce((suma, [, apuesta]) => suma + apuesta, 0);
    if (total <= 0) return null;
    return new Map(valores.map(([id, apuesta]) => [id, apuesta / total]));
  }

  function renderValidadores(inst, pos, estado) {
    const cuerpo = $("#cuerpo-validadores");
    if (!cuerpo) return;
    const editable = estado === "APUESTAS";
    if (!editable && cliente.borradorApuestas.size) cliente.borradorApuestas.clear();
    const activos = lista(pos.validadores).filter((v) => esObjeto(v) && typeof v.id === "string");
    const excluidos = lista(pos.excluidos).filter((v) => esObjeto(v) && typeof v.id === "string");
    const claves = activos.map((v) => v.id).concat(excluidos.map((v) => `x:${v.id}`));
    const registros = sincronizarFilas(cuerpo, filasValidadores, claves, crearFilaValidador);

    const votos = new Map(lista(pos.votos).filter(esObjeto).map((v) => [v.validador, v]));
    const probServidor = objeto(pos.probabilidades);
    const totalApostado = numero(pos.A) ?? sumar(activos, (v) => v.apuesta);
    const vistaPrevia = editable ? calcularVistaPrevia(activos) : null;

    registros.forEach((registro, i) => {
      const esExcluido = i >= activos.length;
      const validador = esExcluido ? excluidos[i - activos.length] : activos[i];
      const nodo = objeto(nodoPorId(validador.id));
      registro.fila.classList.toggle("excluido", esExcluido);
      fijarTexto(registro.celdas.disponible, fmtNum(nodo.disponible));

      const conEntrada = editable && !esExcluido;
      registro.entrada.hidden = !conEntrada;
      registro.textoApuesta.hidden = conEntrada;
      const borrador = cliente.borradorApuestas.get(validador.id);
      if (conEntrada && borrador === undefined && document.activeElement !== registro.entrada) {
        registro.entrada.value = numero(validador.apuesta) === null ? "" : String(validador.apuesta);
      }
      registro.entrada.classList.toggle("editado", borrador !== undefined);
      fijarTexto(registro.textoApuesta, fmtNum(validador.apuesta));

      if (esExcluido) {
        fijarHijos(registro.celdas.probabilidad, crear("span", { clase: "vacio-dato", texto: "—" }));
        fijarHijos(registro.celdas.voto, elementoVoto(null));
        const castigo = validador.castigo ?? validador.monto;
        fijarHijos(registro.celdas.papel, insignia(`Castigado: −${fmtNum(castigo)}`, "mal", texto(validador.motivo, "")));
        return;
      }
      const previa = vistaPrevia ? vistaPrevia.get(validador.id) : undefined;
      const probabilidad = previa !== undefined ? previa
        : numero(probServidor[validador.id]) ?? (totalApostado > 0 ? (numero(validador.apuesta) ?? 0) / totalApostado : null);
      fijarHijos(registro.celdas.probabilidad, barraProbabilidad(probabilidad, previa !== undefined));
      const voto = votos.get(validador.id);
      fijarHijos(registro.celdas.voto, elementoVoto(voto ? voto.voto : null));
      fijarHijos(registro.celdas.papel, pos.proponente === validador.id ? insignia("Proponente", "acento") : insignia("Validador", "pos"));
    });

    const vacio = $("#validadores-vacio");
    if (vacio) {
      vacio.hidden = claves.length > 0;
      fijarTexto(vacio, estado === "INACTIVA" ? "No hay ronda activa. Pulse «Iniciar ronda»." : "Esta ronda no tiene validadores.");
    }
    for (const id of ["#btn-guardar-apuestas", "#btn-descartar-apuestas"]) {
      const boton = $(id);
      if (boton) boton.hidden = !editable;
    }
  }

  async function guardarApuestas(boton) {
    if (!cliente.borradorApuestas.size) {
      aviso("info", "No hay cambios: escriba una apuesta nueva y vuelva a pulsar «Guardar apuestas».");
      return;
    }
    const apuestas = {};
    const errores = [];
    for (const [id, valor] of cliente.borradorApuestas) {
      const registro = filasValidadores.get(id);
      if (!cliente.validarEnNavegador) {
        apuestas[id] = valor;
        continue;
      }
      const analisis = analizarEntero(valor);
      const nodo = objeto(nodoPorId(id));
      // Lo que puede apostar: disponible + lo que ya tiene bloqueado en esta ronda.
      const maximo = (numero(nodo.disponible) ?? 0) + (numero(nodo.apuesta_bloqueada) ?? 0);
      let error = null;
      if (analisis.tipo === "vacio") error = `Falta la apuesta de ${id}`;
      else if (analisis.tipo === "decimal") error = `La apuesta de ${id} debe ser un número entero, sin decimales`;
      else if (analisis.tipo === "invalido") error = `La apuesta de ${id} debe ser un número entero (recibido: «${recortar(analisis.texto, 40)}»)`;
      else if (analisis.tipo === "enorme") error = `La apuesta de ${id} es demasiado grande`;
      else if (analisis.valor <= 0) error = "La apuesta debe ser mayor que cero";
      else if (cliente.inst && analisis.valor > maximo) error = `La apuesta de ${id} (${analisis.valor}) supera su saldo disponible (${maximo})`;
      if (error) {
        errores.push(error);
        if (registro) marcarError(registro.entrada, error);
      } else {
        apuestas[id] = analisis.valor;
      }
    }
    if (errores.length) {
      aviso("error", errores[0], errores.length > 1 ? `Hay ${errores.length} apuestas por corregir.` : "");
      return;
    }
    await ejecutarAccion(boton, "POST", "/api/pos/apuestas", { apuestas }, {
      alExito: () => cliente.borradorApuestas.clear(),
    });
  }

  function descartarApuestas() {
    cliente.borradorApuestas.clear();
    for (const registro of filasValidadores.values()) limpiarError(registro.entrada);
    if (document.activeElement && document.activeElement.classList.contains("entrada-apuesta")) document.activeElement.blur();
    if (cliente.inst) seguro(renderPos, cliente.inst);
  }

  function renderVotacion(pos, estado) {
    const votos = lista(pos.votos).filter(esObjeto);
    const activos = lista(pos.validadores).filter(esObjeto);
    const favor = numero(pos.V_favor) ?? sumar(votos.filter((v) => v.voto === true), (v) => v.peso);
    const contra = numero(pos.V_contra) ?? sumar(votos.filter((v) => v.voto === false), (v) => v.peso);
    const total = numero(pos.A) ?? sumar(activos, (v) => v.apuesta);
    const alcanza = typeof pos.umbral_alcanzado === "boolean" ? pos.umbral_alcanzado : total > 0 && 3 * favor >= 2 * total;
    const activa = estado !== "INACTIVA";

    const anchoFavor = activa ? porcentaje(favor, total) : 0;
    const barraFavor = $("#umbral-favor");
    const barraContra = $("#umbral-contra");
    if (barraFavor) {
      barraFavor.style.width = `${anchoFavor}%`;
      barraFavor.classList.toggle("alcanza", alcanza);
    }
    if (barraContra) barraContra.style.width = `${activa ? Math.min(100 - anchoFavor, porcentaje(contra, total)) : 0}%`;

    if (!activa || total <= 0) {
      fijarTexto($("#umbral-texto"), "Sin ronda o sin apuestas: no hay votación.");
      fijarTexto($("#umbral-regla"), "Se acepta si 3V ≥ 2A");
    } else {
      const sinVotar = Math.max(0, total - favor - contra);
      fijarTexto($("#umbral-texto"),
        `A favor V = ${fmtNum(favor)} · en contra ${fmtNum(contra)} · sin votar ${fmtNum(sinVotar)} · total apostado A = ${fmtNum(total)}`);
      fijarTexto($("#umbral-regla"),
        `3V ${3 * favor >= 2 * total ? "≥" : "<"} 2A → 3 × ${favor} = ${3 * favor} ${3 * favor >= 2 * total ? "≥" : "<"} 2 × ${total} = ${2 * total} ${alcanza ? "✓ alcanza 2/3" : "✗ no alcanza 2/3"}`);
    }
    const regla = $("#umbral-regla");
    if (regla) {
      regla.classList.toggle("ok", activa && total > 0 && alcanza);
      regla.classList.toggle("mal", activa && total > 0 && !alcanza && votos.length > 0);
    }

    const cuerpo = $("#cuerpo-votos");
    const firma = texto(votos, "");
    if (cuerpo && cuerpo.dataset.firma !== firma) {
      cuerpo.replaceChildren(...votos.map((v) => crear("tr", {},
        celda(texto(v.validador), "id-nodo"),
        celda(fmtNum(v.peso), "num"),
        celda(elementoVoto(v.voto), "centro"),
        celda(elementoHash(v.firma, { inicio: 8, fin: 4 })))));
      cuerpo.dataset.firma = firma;
    }
    const vacio = $("#votos-vacio");
    if (vacio) vacio.hidden = votos.length > 0;
  }

  function renderHistorialPos(pos) {
    const cuerpo = $("#cuerpo-historial-pos");
    if (!cuerpo) return;
    const historial = lista(pos.historial).filter(esObjeto);
    const firma = texto(historial, "");
    if (cuerpo.dataset.firma !== firma) {
      cuerpo.replaceChildren(...historial.map((h) => {
        const resultado = h.resultado ?? h.estado ?? (h.aceptado === true ? "ACEPTADO" : h.aceptado === false ? "RECHAZADO" : null);
        const textoResultado = esObjeto(resultado) ? texto(resultado.estado ?? resultado.mensaje ?? resultado) : texto(resultado);
        const favor = h.V_favor ?? h.v_favor ?? h.votos_favor;
        const total = h.A ?? h.total ?? h.total_apostado;
        const castigo = esObjeto(h.castigo) ? h.castigo.monto : h.castigo;
        const detalle = [h.motivo ?? h.mensaje, castigo !== undefined && castigo !== null ? `castigo ${fmtNum(castigo)}` : null]
          .filter((parte) => parte !== undefined && parte !== null && parte !== "")
          .map((parte) => texto(parte))
          .join(" · ");
        const claseResultado = /ACEPT/i.test(textoResultado) ? "ok" : /RECHAZ|SIN_VALID/i.test(textoResultado) ? "mal" : "neutro";
        return crear("tr", {},
          celda(texto(h.intento), "num"),
          celda(texto(h.proponente), "id-nodo"),
          celda(insignia(textoResultado, claseResultado)),
          celda(favor !== undefined || total !== undefined ? `${fmtNum(favor)} / ${fmtNum(total)}` : "—", "mono"),
          celda(detalle || "—", "detalle"));
      }));
      cuerpo.dataset.firma = firma;
    }
    const vacio = $("#historial-pos-vacio");
    if (vacio) vacio.hidden = historial.length > 0;
  }

  function renderCastigosRonda(pos) {
    const contenedor = $("#lista-castigos-ronda");
    if (!contenedor) return;
    const desdeRonda = lista(pos.castigos_ronda).filter(esObjeto);
    const castigos = desdeRonda.length ? desdeRonda : lista(pos.excluidos).filter(esObjeto);
    const firma = texto(castigos, "");
    if (contenedor.dataset.firma !== firma) {
      fijarHijos(contenedor, castigos.map((c) => crear("li", {}, textoCastigo(c))));
      contenedor.dataset.firma = firma;
    }
    const vacio = $("#castigos-ronda-vacio");
    if (vacio) vacio.hidden = castigos.length > 0;
  }

  async function iniciarRondaPos(boton) {
    const control = $("#pos-rondas");
    const resultado = leerEnteroCampo(control, "el número de rondas", 1, 1000000);
    if (resultado.error) {
      aviso("error", resultado.error);
      return;
    }
    const autoTx = Boolean($("#pos-auto-tx") && $("#pos-auto-tx").checked);
    await ejecutarAccion(boton, "POST", "/api/pos/ronda", { rondas: resultado.valor, auto_tx: autoTx });
  }

  /** Envía el estado que se ve en pantalla: si otra pestaña ya avanzó, el servidor responde 409. */
  async function avanzarPos(boton) {
    const estado = cliente.estadoPosMostrado;
    const cuerpo = estado && estado !== "INACTIVA" ? { estado_esperado: estado } : {};
    await ejecutarAccion(boton, "POST", "/api/pos/avanzar", cuerpo);
  }

  async function alternarAutomatico(boton) {
    const activo = objeto(cliente.inst && cliente.inst.pos).automatico === true;
    await ejecutarAccion(boton, "POST", "/api/pos/automatico", { activo: !activo });
  }

  async function votarManual(evento) {
    evento.preventDefault();
    const formulario = evento.currentTarget;
    const nodo = formulario.elements.nodo.value;
    const voto = formulario.elements.voto.value === "si";
    await ejecutarAccion(botonEnvio(formulario), "POST", "/api/pos/votar", { nodo, voto });
  }

  // =========================================================================
  // 16. Explorador de la cadena
  // =========================================================================

  function extraerCadena(datos) {
    if (Array.isArray(datos)) return datos;
    if (esObjeto(datos) && Array.isArray(datos.cadena)) return datos.cadena;
    if (esObjeto(datos) && Array.isArray(datos.bloques)) return datos.bloques;
    return null;
  }

  function abrirExplorador(id) {
    explorador.id = id;
    const select = $("#cadena-nodo");
    if (select) select.value = id;
    cargarCadena(false);
    const panel = $("#panel-cadena");
    if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /** Pide la cadena del nodo elegido (GET /api/nodos/<id>/cadena). */
  async function cargarCadena(silencioso) {
    const id = explorador.id;
    if (!id || explorador.cargando) return;
    explorador.cargando = true;
    explorador.idSolicitado = id;
    const nodo = nodoPorId(id);
    explorador.hashSolicitado = nodo && typeof nodo.ultimo_hash === "string" ? nodo.ultimo_hash : null;
    const generacion = cliente.generacion;
    const boton = $("#btn-cargar-cadena");
    ponerOcupado(boton, true);
    renderResumenCadena();
    try {
      const respuesta = await pedir("GET", `/api/nodos/${encodeURIComponent(id)}/cadena`);
      if (generacion !== cliente.generacion) return;
      if (respuesta.red && esObjeto(respuesta.json) && respuesta.json.ok === true) {
        const cadena = extraerCadena(respuesta.json.datos);
        if (cadena) {
          if (explorador.idCargado !== id) {
            explorador.abiertos.clear();
            explorador.limite = BLOQUES_POR_PAGINA;
          }
          explorador.cadena = cadena;
          explorador.idCargado = id;
          explorador.error = null;
          explorador.actualizado = Date.now();
        } else {
          explorador.error = "La respuesta del servidor no contiene una cadena.";
        }
      } else {
        explorador.error = mensajeError(respuesta);
        if (!silencioso) aviso("error", explorador.error, detalleError(respuesta));
      }
      seguro(renderCadena);
    } finally {
      explorador.cargando = false;
      ponerOcupado(boton, false);
      seguro(renderResumenCadena);
      if (explorador.id && explorador.id !== id) cargarCadena(true); // se eligió otro nodo mientras tanto
    }
  }

  /** En cada sondeo: recarga la cadena si el nodo elegido tiene un bloque nuevo. */
  function revisarExplorador(inst) {
    const ids = idsDeNodos(inst);
    if (!ids.length) return;
    if (!explorador.id || !ids.includes(explorador.id)) {
      explorador.id = ids[0];
      const select = $("#cadena-nodo");
      if (select) select.value = explorador.id;
    }
    if (explorador.cargando) return;
    const nodo = nodoPorId(explorador.id);
    const hashActual = nodo && typeof nodo.ultimo_hash === "string" ? nodo.ultimo_hash : null;
    const automatico = !$("#cadena-auto") || $("#cadena-auto").checked;
    if (explorador.idSolicitado !== explorador.id || (automatico && hashActual !== explorador.hashSolicitado)) {
      cargarCadena(true);
    }
  }

  function renderResumenCadena() {
    const resumen = $("#cadena-resumen");
    if (!resumen) return;
    if (explorador.cargando && !explorador.cadena) {
      fijarTexto(resumen, `Cargando la cadena de ${texto(explorador.id)}…`);
      return;
    }
    if (!explorador.cadena) {
      fijarTexto(resumen, explorador.error ? `No se pudo cargar: ${explorador.error}` : "Elija un nodo para ver su copia de la cadena.");
      return;
    }
    const n = explorador.cadena.length;
    const hora = explorador.actualizado ? fmtHora(explorador.actualizado) : "";
    fijarTexto(resumen, `Cadena de ${texto(explorador.idCargado)}: ${fmtNum(n)} bloques (altura ${fmtNum(n - 1)})` +
      `${hora ? ` · actualizada a las ${hora}` : ""}${explorador.cargando ? " · actualizando…" : ""}` +
      `${explorador.error ? ` · último intento falló: ${explorador.error}` : ""}`);
  }

  function renderCadena() {
    const contenedor = $("#lista-bloques");
    if (!contenedor) return;
    renderResumenCadena();
    if (!explorador.cadena) {
      contenedor.replaceChildren();
      return;
    }
    const recienteArriba = !$("#cadena-reciente") || $("#cadena-reciente").checked;
    const indices = explorador.cadena.map((_, i) => i);
    if (recienteArriba) indices.reverse();
    const visibles = indices.slice(0, explorador.limite);
    const fragmento = document.createDocumentFragment();
    for (const indice of visibles) fragmento.append(crearBloque(explorador.cadena[indice], indice));
    const resto = indices.length - visibles.length;
    if (resto > 0) {
      const mas = crear("button", { type: "button", clase: "boton fantasma", texto: `Mostrar ${Math.min(resto, BLOQUES_POR_PAGINA)} bloques más (quedan ${resto})` });
      mas.addEventListener("click", () => {
        explorador.limite += BLOQUES_POR_PAGINA;
        renderCadena();
      });
      fragmento.append(mas);
    }
    contenedor.replaceChildren(fragmento);
  }

  function crearBloque(valor, indice) {
    const bloque = objeto(valor);
    const esGenesis = bloque.numero === 0 || indice === 0;
    const clave = `${texto(bloque.numero, String(indice))}|${texto(bloque.hash, "")}`;
    const modo = texto(bloque.modo, modoDe(objeto(cliente.inst)));
    const detalles = crear("details", { clase: `bloque${esGenesis ? " genesis" : ""}` });
    const transacciones = lista(bloque.transacciones).length;
    detalles.append(crear("summary", { clase: "bloque-resumen" },
      crear("span", { clase: "bloque-numero", texto: esGenesis ? "Génesis #0" : `Bloque #${texto(bloque.numero, String(indice))}` }),
      elementoHash(bloque.hash, { ceros: modo === "pow" }),
      crear("span", { clase: "bloque-meta", texto: esGenesis ? "directorio y saldos iniciales" : `${transacciones} tx · ${texto(bloque.proponente)}` })));
    let construido = false;
    const construir = () => {
      if (construido) return;
      construido = true;
      detalles.append(seguro(detalleBloque, bloque, esGenesis) || crear("p", { clase: "vacio", texto: "No se pudo mostrar este bloque." }));
    };
    detalles.addEventListener("toggle", () => {
      if (detalles.open) {
        explorador.abiertos.add(clave);
        construir();
      } else {
        explorador.abiertos.delete(clave);
      }
    });
    if (explorador.abiertos.has(clave)) {
      construir();
      detalles.open = true;
    }
    return detalles;
  }

  function tablaSimple(encabezados, filas) {
    return crear("div", { clase: "tabla-desplazable" }, crear("table", { clase: "tabla" },
      crear("thead", {}, crear("tr", {}, encabezados.map(([titulo, clase]) => crear("th", { clase, texto: titulo })))),
      crear("tbody", {}, filas)));
  }

  function detalleBloque(bloque, esGenesis) {
    const cuerpo = crear("div", { clase: "bloque-cuerpo" });
    const campos = crear("dl", { clase: "campos-bloque" });
    agregarDato(campos, "numero", texto(bloque.numero));
    agregarDato(campos, "timestamp", `${texto(bloque.timestamp)} ms · ${fmtFecha(bloque.timestamp)}`);
    agregarDato(campos, "hash", elementoHashCompleto(bloque.hash));
    agregarDato(campos, "hash_anterior", elementoHashCompleto(bloque.hash_anterior));
    agregarDato(campos, "nonce", texto(bloque.nonce));
    agregarDato(campos, "proponente", texto(bloque.proponente));
    const recompensa = esObjeto(bloque.recompensa)
      ? `${fmtNum(bloque.recompensa.monto)} para ${texto(bloque.recompensa.beneficiario)}`
      : "— (sin recompensa)";
    agregarDato(campos, "recompensa", recompensa);
    agregarDato(campos, "modo", texto(bloque.modo));
    agregarDato(campos, "intento", texto(bloque.intento));
    const validadores = lista(bloque.validadores).filter(esObjeto);
    agregarDato(campos, "validadores", validadores.length
      ? validadores.map((v) => `${texto(v.id)} (${fmtNum(v.apuesta)})`).join(", ")
      : "ninguno");
    cuerpo.append(campos);

    const transacciones = lista(bloque.transacciones);
    const firmas = lista(bloque.firma);
    cuerpo.append(crear("h4", { texto: `transacciones y firma (${transacciones.length})` }));
    if (transacciones.length) {
      cuerpo.append(tablaSimple(
        [["#", "num"], ["Emisor"], ["Receptor"], ["Monto", "num"], ["Marca de tiempo"], ["Firma del emisor"]],
        transacciones.map((tx, i) => {
          const t = objeto(tx);
          return crear("tr", {},
            celda(String(i + 1), "num"),
            celda(texto(t.emisor), "id-nodo"),
            celda(texto(t.receptor), "id-nodo"),
            celda(fmtNum(t.monto), "num"),
            celda(fmtFecha(t.timestamp), "tenue"),
            celda(elementoHash(firmas[i], { inicio: 10, fin: 6 })));
        })));
    } else {
      cuerpo.append(crear("p", { clase: "vacio", texto: "Sin transacciones." }));
    }

    const votos = lista(bloque.votos).filter(esObjeto);
    cuerpo.append(crear("h4", { texto: `votos (${votos.length})` }));
    if (votos.length) {
      cuerpo.append(tablaSimple([["Validador"], ["Peso", "num"], ["Voto", "centro"], ["Firma"]],
        votos.map((v) => crear("tr", {},
          celda(texto(v.validador), "id-nodo"),
          celda(fmtNum(v.peso), "num"),
          celda(elementoVoto(v.voto), "centro"),
          celda(elementoHash(v.firma, { inicio: 10, fin: 6 }))))));
    } else {
      cuerpo.append(crear("p", { clase: "vacio", texto: "Sin votos." }));
    }

    const castigos = lista(bloque.castigos).filter(esObjeto);
    cuerpo.append(crear("h4", { texto: `castigos (${castigos.length})` }));
    cuerpo.append(castigos.length
      ? crear("ul", { clase: "lista-simple" }, castigos.map((c) => crear("li", { texto: textoCastigo(c) })))
      : crear("p", { clase: "vacio", texto: "Sin castigos." }));

    if (esGenesis) {
      const directorio = objeto(bloque.directorio);
      const saldos = objeto(bloque.saldos_iniciales);
      cuerpo.append(crear("h4", { texto: "directorio (claves públicas) y saldos_iniciales" }));
      cuerpo.append(tablaSimple([["Nodo"], ["Clave pública Ed25519"], ["Saldo inicial", "num"]],
        Object.keys(directorio).sort().map((id) => crear("tr", {},
          celda(id, "id-nodo"),
          celda(elementoHash(directorio[id], { inicio: 16, fin: 6 })),
          celda(fmtNum(saldos[id]), "num")))));
      const parametros = objeto(bloque.parametros);
      const datosParametros = crear("dl", { clase: "campos-bloque" });
      for (const clave of Object.keys(parametros)) agregarDato(datosParametros, clave, texto(parametros[clave]));
      cuerpo.append(crear("h4", { texto: "parametros" }), datosParametros);
    }
    cuerpo.append(detallesJson(bloque, "Ver JSON del bloque"));
    return cuerpo;
  }

  // =========================================================================
  // 17. Bitácora
  // =========================================================================

  function construirFiltroBitacora() {
    const select = $("#bitacora-filtro");
    if (!select) return;
    const grupos = new Map();
    for (const [tipo, info] of Object.entries(TIPOS_EVENTO)) {
      if (!grupos.has(info.grupo)) grupos.set(info.grupo, crear("optgroup", { label: info.grupo }));
      grupos.get(info.grupo).append(crear("option", { value: tipo, texto: info.etiqueta }));
    }
    select.append(...grupos.values());
  }

  function eventoVisible(evento) {
    const filtro = $("#bitacora-filtro") ? $("#bitacora-filtro").value : "";
    const busqueda = $("#bitacora-busqueda") ? $("#bitacora-busqueda").value.trim().toLowerCase() : "";
    if (filtro && evento.tipo !== filtro) return false;
    if (busqueda && !texto(evento.mensaje, "").toLowerCase().includes(busqueda)) return false;
    return true;
  }

  function crearElementoEvento(evento) {
    const info = TIPOS_EVENTO[evento.tipo] || { etiqueta: texto(evento.tipo, "evento"), categoria: "neutro" };
    const elemento = crear("li", { clase: `evento cat-${info.categoria}` },
      crear("span", { clase: "evento-hora", texto: fmtHora(evento.tiempo) }),
      crear("span", { clase: "evento-n", texto: `#${texto(evento.n)}` }),
      crear("span", { clase: "evento-tipo", texto: info.etiqueta }),
      crear("span", { clase: "evento-mensaje", texto: texto(evento.mensaje, "") }));
    if (esObjeto(evento.datos) && Object.keys(evento.datos).length) {
      elemento.append(detallesJson(evento.datos, "datos"));
    }
    return elemento;
  }

  function agregarEventos(nuevos, ultimoServidor) {
    let maximo = cliente.ultimoEvento;
    const vistos = new Set();
    const agregados = [];
    for (const evento of nuevos) {
      if (!esObjeto(evento)) continue;
      const n = numero(evento.n);
      if (n === null || n <= cliente.ultimoEvento || vistos.has(n)) continue;
      vistos.add(n);
      agregados.push(evento);
      maximo = Math.max(maximo, n);
    }
    agregados.sort((a, b) => a.n - b.n);
    cliente.eventos.push(...agregados);
    if (cliente.eventos.length > MAX_EVENTOS) cliente.eventos.splice(0, cliente.eventos.length - MAX_EVENTOS);
    if (ultimoServidor !== null && ultimoServidor > maximo) maximo = ultimoServidor;
    cliente.ultimoEvento = maximo;
    if (agregados.length) mostrarEventosNuevos(agregados);
  }

  function mostrarEventosNuevos(nuevos) {
    const lista_ = $("#lista-eventos");
    if (!lista_) return;
    for (const evento of nuevos) {
      if (eventoVisible(evento)) lista_.prepend(crearElementoEvento(evento));
    }
    while (lista_.children.length > MAX_EVENTOS) lista_.lastElementChild.remove();
    actualizarConteoBitacora();
  }

  function renderBitacoraCompleta() {
    const lista_ = $("#lista-eventos");
    if (!lista_) return;
    const fragmento = document.createDocumentFragment();
    for (let i = cliente.eventos.length - 1; i >= 0; i -= 1) {
      if (eventoVisible(cliente.eventos[i])) fragmento.append(crearElementoEvento(cliente.eventos[i]));
    }
    lista_.replaceChildren(fragmento);
    actualizarConteoBitacora();
  }

  function actualizarConteoBitacora() {
    const lista_ = $("#lista-eventos");
    const visibles = lista_ ? lista_.children.length : 0;
    const total = cliente.eventos.length;
    fijarTexto($("#bitacora-cuenta"), visibles === total ? `${total}` : `${visibles} de ${total}`);
    const vacia = $("#bitacora-vacia");
    if (vacia) {
      vacia.hidden = visibles > 0;
      fijarTexto(vacia, total ? "Ningún evento coincide con el filtro." : "Aún no hay eventos.");
    }
  }

  // =========================================================================
  // 18. Laboratorio de pruebas
  // =========================================================================

  /** Ayudas del laboratorio que dependen del nodo o del ataque elegidos. */
  function renderLaboratorio() {
    const formulario = $("#form-alterar");
    const ayuda = $("#ayuda-alterar");
    if (formulario && ayuda) {
      const nodo = nodoPorId(formulario.elements.nodo.value);
      const altura = nodo ? numero(nodo.altura) : null;
      fijarTexto(ayuda, altura === null ? "" : altura < 1
        ? `${nodo.id} sólo tiene el génesis: mine o proponga bloques primero (también puede alterar el bloque 0).`
        : `Bloques de ${nodo.id}: 0 a ${altura}. Pruebe un bloque intermedio.`);
    }
    const formularioTx = $("#form-ataque-tx");
    if (formularioTx) {
      const tipo = formularioTx.elements.tipo.value;
      const campoFirmante = $("#campo-firmante");
      if (campoFirmante) campoFirmante.hidden = tipo !== "otra_clave";
      fijarTexto($("#ayuda-ataque-tx"), AYUDA_ATAQUE_TX[tipo] || "");
    }
  }

  function mostrarResultado(selector, ...contenido) {
    const caja = $(selector);
    if (!caja) return;
    fijarHijos(caja, ...contenido);
    caja.hidden = false;
  }

  function vistaError(respuesta) {
    return crear("div", { clase: "nota-resultado mal" },
      crear("p", { texto: mensajeError(respuesta) }),
      crear("p", { clase: "tenue", texto: detalleError(respuesta) }));
  }

  /** Resultado de un ataque de difusión: qué nodo aceptó o rechazó y por qué. */
  function vistaDifusion(datos, mensaje) {
    const d = objeto(datos);
    const resultados = lista(d.resultados).filter(esObjeto);
    const partes = [crear("p", { clase: "resultado-mensaje", texto: texto(mensaje ?? d.mensaje, "") })];
    if (resultados.length) {
      partes.push(tablaSimple([["Nodo"], ["Resultado"], ["Motivo"]], resultados.map((r) => crear("tr", {},
        celda(texto(r.nodo), "id-nodo"),
        celda(r.acepto === true ? insignia("Aceptó ✗", "mal") : insignia("Rechazó ✓", "ok")),
        celda(texto(r.motivo, ""), "detalle")))));
      const rechazos = numero(d.rechazos) ?? resultados.filter((r) => r.acepto !== true).length;
      partes.push(crear("p", { clase: "tenue", texto: `Rechazaron ${rechazos} de ${resultados.length} nodos.` }));
    }
    partes.push(detallesJson(datos, "Respuesta completa (JSON)"));
    return partes;
  }

  function vistaAtaqueTransaccion(datos, mensaje) {
    const d = objeto(datos);
    const aceptadas = lista(d.aceptadas);
    const rechazadas = lista(d.rechazadas);
    const partes = [crear("p", { clase: "resultado-mensaje", texto: texto(mensaje ?? d.mensaje, "") })];
    if (aceptadas.length) {
      partes.push(crear("p", { clase: "subtitulo-resultado", texto: `Aceptadas (${aceptadas.length})` }));
      partes.push(crear("ul", { clase: "lista-simple" }, aceptadas.map((tx) => {
        const t = objeto(tx);
        return crear("li", { texto: `${texto(t.emisor)} → ${texto(t.receptor)}: ${fmtNum(t.monto)} (id ${hashCorto(t.id, 8, 0)})` });
      })));
    }
    if (rechazadas.length) {
      partes.push(crear("p", { clase: "subtitulo-resultado", texto: `Rechazadas (${rechazadas.length})` }));
      partes.push(crear("ul", { clase: "lista-simple" }, rechazadas.map((r) => {
        const x = objeto(r);
        return crear("li", { clase: "rechazo" }, crear("span", { texto: texto(x.error, texto(r)) }),
          x.codigo ? crear("code", { clase: "codigo-error", texto: texto(x.codigo) }) : null);
      })));
    }
    partes.push(detallesJson(datos, "Respuesta completa (JSON)"));
    return partes;
  }

  async function alterarBloque(evento) {
    evento.preventDefault();
    const formulario = evento.currentTarget;
    const campos = formulario.elements;
    const numeroBloque = leerEnteroCampo(campos.numero, "el número de bloque", 0, 1000000000);
    if (numeroBloque.error) {
      aviso("error", numeroBloque.error);
      return;
    }
    await ejecutarAccion(botonEnvio(formulario), "POST", "/api/ataques/alterar-bloque",
      { nodo: campos.nodo.value, numero: numeroBloque.valor, tipo: campos.tipo.value }, {
        alExito: (datos, json) => mostrarResultado("#resultado-alterar", vistaDifusion(datos, json.mensaje)),
        alError: (respuesta) => mostrarResultado("#resultado-alterar", vistaError(respuesta)),
      });
  }

  async function difundirCadenaCorta(evento) {
    evento.preventDefault();
    const formulario = evento.currentTarget;
    const campos = formulario.elements;
    const quitar = leerEnteroCampo(campos.quitar, "el número de bloques a quitar", 1, 1000000000);
    if (quitar.error) {
      aviso("error", quitar.error);
      return;
    }
    await ejecutarAccion(botonEnvio(formulario), "POST", "/api/ataques/cadena-corta",
      { nodo: campos.nodo.value, quitar: quitar.valor }, {
        alExito: (datos, json) => mostrarResultado("#resultado-cadena-corta", vistaDifusion(datos, json.mensaje)),
        alError: (respuesta) => mostrarResultado("#resultado-cadena-corta", vistaError(respuesta)),
      });
  }

  async function enviarAtaqueTransaccion(evento) {
    evento.preventDefault();
    const formulario = evento.currentTarget;
    const campos = formulario.elements;
    limpiarErrores(formulario);
    let monto = campos.monto.value;
    if (cliente.validarEnNavegador) {
      const resultado = validarMonto(monto);
      if (resultado.error) {
        marcarError(campos.monto, resultado.error);
        aviso("error", resultado.error);
        return;
      }
      monto = resultado.valor;
    }
    const cuerpo = { tipo: campos.tipo.value, emisor: campos.emisor.value, receptor: campos.receptor.value, monto };
    if (campos.tipo.value === "otra_clave" && campos.firmante.value) cuerpo.firmante = campos.firmante.value;
    await ejecutarAccion(botonEnvio(formulario), "POST", "/api/ataques/transaccion", cuerpo, {
      alExito: (datos, json) => mostrarResultado("#resultado-ataque-tx", vistaAtaqueTransaccion(datos, json.mensaje)),
      alError: (respuesta) => mostrarResultado("#resultado-ataque-tx", vistaError(respuesta)),
    });
  }

  /** Registra en la caja de resultados lo que respondió el servidor a un voto. */
  async function enviarVotoDePrueba(boton, nodo, descripcion, lineas) {
    let linea = null;
    await ejecutarAccion(boton, "POST", "/api/pos/votar", { nodo, voto: true }, {
      alExito: (_datos, json) => {
        linea = crear("li", {}, insignia("Aceptado", "alerta"), ` ${descripcion}: ${texto(json.mensaje, "")}`);
      },
      alError: (respuesta) => {
        linea = crear("li", {}, insignia(`Rechazado (HTTP ${respuesta.status})`, "ok"), ` ${descripcion}: ${mensajeError(respuesta)}`);
      },
    });
    if (linea) lineas.push(linea);
  }

  async function probarVotoNoValidador(boton) {
    const pos = objeto(cliente.inst && cliente.inst.pos);
    const enJuego = new Set(lista(pos.validadores).filter(esObjeto).map((v) => v.id));
    const elegido = idsDeNodos(cliente.inst).find((id) => !enJuego.has(id));
    if (!elegido) {
      aviso("info", "Todos los nodos son validadores en esta ronda. Use la selección «Muestra aleatoria» de validadores para probar este caso.");
      return;
    }
    const lineas = [];
    await enviarVotoDePrueba(boton, elegido, `voto de ${elegido} (no es validador)`, lineas);
    mostrarResultado("#resultado-votos", crear("ul", { clase: "lista-simple" }, lineas));
  }

  async function probarVotoDoble(boton) {
    const pos = objeto(cliente.inst && cliente.inst.pos);
    const votos = lista(pos.votos).filter(esObjeto);
    const validadores = lista(pos.validadores).filter(esObjeto);
    const id = (votos[0] && votos[0].validador) || (validadores[0] && validadores[0].id);
    if (!id) {
      aviso("info", "No hay validadores en juego: inicie una ronda y avance hasta VOTACION.");
      return;
    }
    const yaVoto = votos.some((v) => v.validador === id);
    const lineas = [];
    if (!yaVoto) await enviarVotoDePrueba(boton, id, `primer voto de ${id}`, lineas);
    await enviarVotoDePrueba(boton, id, `segundo voto de ${id}`, lineas);
    mostrarResultado("#resultado-votos", crear("ul", { clase: "lista-simple" }, lineas));
  }

  function usarEjemploCrudo(evento) {
    const indice = Number(evento.currentTarget.value);
    const ejemplo = EJEMPLOS_CRUDOS[indice];
    const formulario = $("#form-cruda");
    if (!ejemplo || !formulario || evento.currentTarget.value === "") return;
    formulario.elements.metodo.value = ejemplo.metodo;
    formulario.elements.ruta.value = ejemplo.ruta;
    formulario.elements.cuerpo.value = ejemplo.cuerpo;
  }

  function claseHttp(status) {
    if (status >= 200 && status < 300) return "ok";
    if (status >= 500) return "mal";
    return "alerta";
  }

  /** Envía método + ruta + cuerpo tal cual y muestra el código HTTP y la respuesta. */
  async function enviarPeticionCruda(evento) {
    evento.preventDefault();
    const formulario = evento.currentTarget;
    const metodo = formulario.elements.metodo.value;
    const ruta = formulario.elements.ruta.value.trim();
    const cuerpo = formulario.elements.cuerpo.value;
    limpiarErrores(formulario);
    if (!/^\/(?![/\\])\S*$/.test(ruta)) {
      const error = "La ruta debe empezar con «/» y ser de este mismo servidor (por ejemplo /api/estado).";
      marcarError(formulario.elements.ruta, error);
      aviso("error", error);
      return;
    }
    const sinCuerpo = metodo === "GET" || metodo === "HEAD";
    const boton = botonEnvio(formulario);
    if (ocupados.has(boton)) return;
    ponerOcupado(boton, true);
    const inicio = performance.now();
    try {
      const respuesta = await pedir(metodo, ruta, sinCuerpo ? undefined : cuerpo);
      const milisegundos = Math.round(performance.now() - inicio);
      if (!respuesta.red) {
        mostrarResultado("#resultado-crudo", crear("div", { clase: "nota-resultado mal", texto: "No hubo respuesta del servidor (sin conexión o tiempo agotado)." }));
        aviso("error", "No se pudo contactar al servidor (¿sigue en ejecución?).");
        return;
      }
      let contenido = respuesta.json !== null ? JSON.stringify(respuesta.json, null, 2) : respuesta.texto;
      if (contenido.length > 20000) contenido = `${contenido.slice(0, 20000)}\n… (respuesta recortada)`;
      mostrarResultado("#resultado-crudo",
        crear("p", { clase: "resultado-http" },
          insignia(`HTTP ${respuesta.status}${respuesta.statusText ? ` ${respuesta.statusText}` : ""}`, claseHttp(respuesta.status)),
          crear("span", { clase: "tenue", texto: ` ${milisegundos} ms · ${respuesta.tipoContenido || "sin Content-Type"}` })),
        sinCuerpo && cuerpo.trim() ? crear("p", { clase: "tenue", texto: `Las peticiones ${metodo} no llevan cuerpo: el texto escrito no se envió.` }) : null,
        crear("pre", { clase: "codigo", texto: contenido || "(respuesta vacía)" }));
      const json = respuesta.json;
      if (esObjeto(json) && json.ok === true) aviso("exito", texto(json.mensaje, `HTTP ${respuesta.status}`));
      else if (esObjeto(json) && json.ok === false) aviso("error", mensajeError(respuesta), detalleError(respuesta));
    } finally {
      ponerOcupado(boton, false);
      pedirSondeo();
    }
  }

  function cambiarValidacionNavegador(evento) {
    cliente.validarEnNavegador = evento.currentTarget.checked;
    document.body.classList.toggle("sin-validacion", !cliente.validarEnNavegador);
    for (const formulario of $$("form")) limpiarErrores(formulario);
    aviso("info", cliente.validarEnNavegador
      ? "Validación del navegador activada."
      : "Validación del navegador desactivada: los datos se envían tal cual y responde el servidor.");
  }

  // =========================================================================
  // 19. Tema claro / oscuro y copiar hashes
  // =========================================================================

  const TEMAS = ["auto", "claro", "oscuro"];
  const NOMBRES_TEMA = { auto: "automático", claro: "claro", oscuro: "oscuro" };

  function aplicarTema(tema) {
    const elegido = TEMAS.includes(tema) ? tema : "auto";
    if (elegido === "auto") delete document.documentElement.dataset.tema;
    else document.documentElement.dataset.tema = elegido;
    fijarTexto($("#btn-tema"), `Tema: ${NOMBRES_TEMA[elegido]}`);
    try {
      window.localStorage.setItem("simulador.tema", elegido);
    } catch (_error) {
      // Sin almacenamiento local: el tema sólo dura esta visita.
    }
    return elegido;
  }

  function temaGuardado() {
    try {
      return window.localStorage.getItem("simulador.tema") || "auto";
    } catch (_error) {
      return "auto";
    }
  }

  function copiarHash(evento) {
    const objetivo = evento.target instanceof Element ? evento.target.closest(".hash[data-completo]") : null;
    if (!objetivo) return;
    const valor = objetivo.dataset.completo;
    if (!navigator.clipboard || !valor) return;
    navigator.clipboard.writeText(valor)
      .then(() => aviso("info", "Hash copiado al portapapeles.", hashCorto(valor, 16, 8)))
      .catch(() => undefined);
  }

  // =========================================================================
  // 20. Enlace de eventos e inicio
  // =========================================================================

  function al(selector, tipo, manejador) {
    const elemento = $(selector);
    if (elemento) elemento.addEventListener(tipo, manejador);
  }

  function enlazarEventos() {
    // Nueva simulación
    al("#form-simulacion", "submit", crearSimulacion);
    al("#form-simulacion", "input", () => { cliente.formularioSucio = true; });
    al("#form-simulacion", "change", () => {
      cliente.formularioSucio = true;
      actualizarFormularioSegunModo();
    });
    al("#btn-config-actual", "click", () => {
      if (cliente.inst) prellenarFormulario(cliente.inst, true);
    });

    // Transacciones
    al("#form-transaccion", "submit", enviarTransaccion);
    al("#form-transaccion", "change", () => seguro(mostrarDisponibleEmisor));
    al("#form-aleatorias", "submit", generarAleatorias);

    // PoW
    al("#btn-minar-1", "click", (e) => minar(e.currentTarget, 1, false));
    al("#btn-minar-10", "click", (e) => minar(e.currentTarget, 10, true));
    al("#btn-cancelar-mineria", "click", (e) => ejecutarAccion(e.currentTarget, "POST", "/api/pow/cancelar", {}));

    // PoS
    al("#btn-pos-iniciar", "click", (e) => iniciarRondaPos(e.currentTarget));
    al("#btn-pos-avanzar", "click", (e) => avanzarPos(e.currentTarget));
    al("#btn-pos-automatico", "click", (e) => alternarAutomatico(e.currentTarget));
    al("#btn-pos-apostar-todo", "click", (e) => ejecutarAccion(e.currentTarget, "POST", "/api/pos/apostar-todo", {}));
    al("#btn-pos-cancelar", "click", (e) => ejecutarAccion(e.currentTarget, "POST", "/api/pos/cancelar", {}));
    al("#btn-guardar-apuestas", "click", (e) => guardarApuestas(e.currentTarget));
    al("#btn-descartar-apuestas", "click", descartarApuestas);
    al("#form-votar", "submit", votarManual);

    // Explorador
    al("#cadena-nodo", "change", (e) => {
      explorador.id = e.currentTarget.value;
      cargarCadena(false);
    });
    al("#btn-cargar-cadena", "click", () => cargarCadena(false));
    al("#cadena-reciente", "change", () => seguro(renderCadena));

    // Bitácora
    al("#bitacora-filtro", "change", () => seguro(renderBitacoraCompleta));
    al("#bitacora-busqueda", "input", () => seguro(renderBitacoraCompleta));

    // Laboratorio
    al("#validacion-navegador", "change", cambiarValidacionNavegador);
    al("#form-alterar", "submit", alterarBloque);
    al("#form-alterar", "change", () => seguro(renderLaboratorio));
    al("#form-cadena-corta", "submit", difundirCadenaCorta);
    al("#form-ataque-tx", "submit", enviarAtaqueTransaccion);
    al("#form-ataque-tx", "change", () => seguro(renderLaboratorio));
    al("#btn-voto-no-validador", "click", (e) => probarVotoNoValidador(e.currentTarget));
    al("#btn-voto-doble", "click", (e) => probarVotoDoble(e.currentTarget));
    al("#crudo-ejemplo", "change", usarEjemploCrudo);
    al("#form-cruda", "submit", enviarPeticionCruda);

    // Generales
    al("#btn-tema", "click", () => {
      const actual = document.documentElement.dataset.tema || "auto";
      aplicarTema(TEMAS[(TEMAS.indexOf(actual) + 1) % TEMAS.length]);
    });
    document.addEventListener("click", copiarHash);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) pedirSondeo();
    });
  }

  function construirEjemplosCrudos() {
    const select = $("#crudo-ejemplo");
    if (!select) return;
    EJEMPLOS_CRUDOS.forEach((ejemplo, indice) => {
      select.append(crear("option", { value: String(indice), texto: `${ejemplo.metodo} · ${ejemplo.titulo}` }));
    });
  }

  function iniciar() {
    aplicarTema(temaGuardado());
    construirFiltroBitacora();
    construirEjemplosCrudos();
    enlazarEventos();
    actualizarFormularioSegunModo();
    renderLaboratorio();
    actualizarConteoBitacora();
    sondear();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar);
  else iniciar();
})();
