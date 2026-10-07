"""Servidor web del simulador (Flask): la página y la API JSON (docs/DISENO.md §18).

Las rutas son delgadas: leen el cuerpo, sacan los campos y llaman al
``Simulador``, que valida cada valor y aplica la acción de forma atómica.
Este módulo sólo garantiza que toda respuesta de ``/api/`` sea JSON con la
forma del contrato (nunca una página HTML ni un traceback), incluso con
datos mal formados, rutas inexistentes, métodos incorrectos o fallas.

Para correrlo se recomienda ``python app.py``: así también las peticiones mal
formadas a nivel del protocolo HTTP (que el servidor rechaza antes de llegar a
Flask) reciben una respuesta JSON. ``flask --app app run`` también funciona,
pero en esos casos raros el servidor de desarrollo responde con su página HTML.
"""

import json
import math
import threading

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException, InternalServerError, RequestEntityTooLarge
from werkzeug.serving import WSGIRequestHandler

from nucleo.config import validar_config
from nucleo.entradas import MAX_ENTERO, exigir_objeto, leer_entero, leer_id_nodo, texto_visible
from nucleo.errores import EntradaInvalida, ErrorSimulacion
from nucleo.motor import Motor
from nucleo.simulador import Simulador

MAX_CUERPO = 2 * 1024 * 1024    # 2 MB; un cuerpo más grande se rechaza con 413
MAX_ANIDAMIENTO = 32             # niveles de listas/objetos; una cadena válida usa 5
MENSAJE_ERROR_INTERNO = "Error interno: la operación no se aplicó"

# Errores HTTP que no vienen del simulador (los produce Flask): código -> (codigo, mensaje).
ERRORES_HTTP = {
    400: ("peticion_invalida", "La petición no es válida"),
    404: ("ruta_inexistente", "No existe la ruta {metodo} {ruta}"),
    405: ("metodo_no_permitido", "El método {metodo} no está permitido en {ruta}"),
    413: ("cuerpo_demasiado_grande", "El cuerpo de la petición es demasiado grande (máximo 2 MB)"),
    500: ("error_interno", MENSAJE_ERROR_INTERNO),
}


# ---------------------------------------------------------------------------
# Simulador actual
# ---------------------------------------------------------------------------

class Gestor:
    """Guarda el simulador en uso; reiniciar lo reemplaza por uno nuevo bajo un candado propio."""

    def __init__(self, config_inicial=None):
        self._candado = threading.Lock()
        self._simulador = Simulador(config_inicial)

    def actual(self) -> Simulador:
        """El simulador en uso (cada petición y el motor lo piden aquí)."""
        with self._candado:
            return self._simulador

    def reiniciar(self, datos_config) -> Simulador:
        """Crea un simulador nuevo. Si la configuración es inválida lanza
        EntradaInvalida y el simulador anterior sigue intacto."""
        nuevo = Simulador(validar_config(datos_config))
        with self._candado:
            self._simulador = nuevo
        return nuevo


# ---------------------------------------------------------------------------
# Lectura de la petición
# ---------------------------------------------------------------------------

def _rechazar_constante(nombre):
    """json.loads llama aquí con NaN, Infinity o -Infinity, que no son JSON válido."""
    raise ValueError(f"{nombre} no es un valor JSON válido")


def _leer_decimal(texto):
    """Número con punto o exponente; uno que se desborda (1e400 = infinito) no se acepta."""
    valor = float(texto)
    if not math.isfinite(valor):
        raise ValueError("número fuera de rango")
    return valor


def _json_invalido(motivo: str, **extra) -> EntradaInvalida:
    """Error 400 para un cuerpo que no se puede leer como JSON."""
    return EntradaInvalida("El cuerpo debe ser JSON válido", "json_invalido", {"motivo": motivo, **extra})


def _demasiado_anidado(valor, limite: int) -> bool:
    """True si hay listas u objetos a más de `limite` niveles (se recorre sin recursión)."""
    pila = [(valor, 1)]
    while pila:
        actual, nivel = pila.pop()
        if nivel > limite:
            return True
        hijos = actual.values() if isinstance(actual, dict) else actual
        pila.extend((hijo, nivel + 1) for hijo in hijos if isinstance(hijo, (dict, list)))
    return False


def leer_cuerpo() -> dict:
    """Cuerpo de la petición como objeto JSON (vacío = {}).

    No se usa request.get_json(): aquí se controla cada falla para responder
    siempre 400 con un mensaje claro. Más de 2 MB ⇒ 413 (lo lanza get_data).
    Un JSON con demasiados niveles se rechaza aquí, antes de llegar al núcleo.
    """
    crudo = request.get_data(cache=False)
    if request.content_length is None and len(crudo) >= MAX_CUERPO:
        raise RequestEntityTooLarge()    # envío por partes (chunked): Werkzeug lo corta en 2 MB sin avisar
    if not crudo.strip():
        return {}
    try:
        datos = json.loads(crudo.decode("utf-8-sig"),
                           parse_constant=_rechazar_constante, parse_float=_leer_decimal)
    except UnicodeDecodeError:
        raise _json_invalido("el texto no está codificado en UTF-8") from None
    except RecursionError:
        raise _json_invalido("tiene demasiados niveles de anidamiento") from None
    except json.JSONDecodeError as error:
        raise _json_invalido("error de sintaxis", linea=error.lineno, columna=error.colno) from None
    except (ValueError, TypeError):
        raise _json_invalido("contiene NaN, Infinity o un número fuera de rango") from None
    datos = exigir_objeto(datos)
    if _demasiado_anidado(datos, MAX_ANIDAMIENTO):
        raise EntradaInvalida(f"El cuerpo tiene demasiados niveles de anidamiento (máximo {MAX_ANIDAMIENTO})",
                              "json_invalido")
    return datos


def opcionales(cuerpo: dict, *campos: str) -> dict:
    """Sólo los campos opcionales que vienen en el cuerpo (los ausentes toman el valor por omisión)."""
    return {campo: cuerpo[campo] for campo in campos if campo in cuerpo}


def leer_desde() -> int:
    """Parámetro ?desde=n: último evento que ya vio el navegador (ausente o vacío = 0)."""
    valor = request.args.get("desde", "")
    if valor.strip() == "":
        return 0
    return leer_entero(valor, "El parámetro desde", 0, MAX_ENTERO)


def es_ruta_api() -> bool:
    """True si la petición es para la API JSON."""
    return request.path == "/api" or request.path.startswith("/api/")


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------

def exito(sim, datos, mensaje_defecto: str, version: int | None = None):
    """{"ok": true, "mensaje", "datos", "version", "id_simulacion"}; usa el mensaje del simulador si lo trae.

    `id_simulacion` cambia al reiniciar (version y los eventos vuelven a empezar):
    la interfaz que lo ve cambiar olvida lo que mostraba y pide todo desde cero.
    """
    mensaje = datos.get("mensaje") if isinstance(datos, dict) else None
    if not isinstance(mensaje, str) or not mensaje:
        mensaje = mensaje_defecto
    return jsonify({"ok": True, "mensaje": mensaje, "datos": datos,
                    "version": sim.version if version is None else version,
                    "id_simulacion": sim.id_simulacion})


def error_json(http: int, mensaje: str, codigo: str, detalles: dict | None = None):
    """{"ok": false, "error", "codigo", "detalles"} con el código HTTP dado."""
    return jsonify({"ok": False, "error": mensaje, "codigo": codigo, "detalles": detalles or {}}), http


# ---------------------------------------------------------------------------
# Aplicación
# ---------------------------------------------------------------------------

def create_app(config_inicial=None, motor_activo: bool = True) -> Flask:
    """Crea la aplicación con su propio simulador y su motor (el hilo de las rondas automáticas)."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_CUERPO
    app.config["PROVIDE_AUTOMATIC_OPTIONS"] = False   # OPTIONS no es parte de la API: 405 JSON
    app.json.sort_keys = False              # el JSON sale en el orden en que se arma
    app.url_map.merge_slashes = False       # "/api//estado" da 404 JSON, no una redirección

    gestor = Gestor(config_inicial)
    motor = Motor(gestor.actual)
    app.extensions["gestor"] = gestor
    app.extensions["motor"] = motor

    if motor_activo:
        @app.before_request
        def arrancar_motor():
            """El hilo se arranca en la primera petición (no al importar: así no se duplica)."""
            try:
                motor.asegurar_iniciado()
            except Exception as error:   # sin motor la página sigue funcionando
                app.logger.error("No se pudo arrancar el motor automático", exc_info=error)

    @app.after_request
    def sin_cache(respuesta):
        """El navegador no debe guardar respuestas de la API: el estado cambia todo el tiempo."""
        if es_ruta_api():
            respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    _registrar_manejadores(app, gestor)
    _registrar_rutas(app, gestor)
    return app


def _registrar_manejadores(app: Flask, gestor: Gestor) -> None:
    """Convierte cualquier error bajo /api/ en JSON y anota los rechazos en la bitácora."""

    def anotar_rechazo(mensaje: str) -> None:
        """Evento "entrada_rechazada" en la bitácora del simulador actual (nunca falla)."""
        try:
            ruta = texto_visible(f"{request.method} {request.path}", 120)
            gestor.actual().registrar_rechazo(ruta, mensaje)
        except Exception as error:
            app.logger.error("No se pudo anotar el rechazo en la bitácora", exc_info=error)

    @app.errorhandler(ErrorSimulacion)
    def error_de_simulacion(error: ErrorSimulacion):
        """Errores esperados del núcleo: 400, 404, 409 (o 500 si el núcleo revirtió una falla)."""
        if error.http < 500:
            anotar_rechazo(error.mensaje)
        return jsonify(error.a_dict()), error.http

    @app.errorhandler(HTTPException)
    def error_http(error: HTTPException):
        """Ruta inexistente, método no permitido, cuerpo de más de 2 MB, etc."""
        if not es_ruta_api() or error.code is None or error.code < 400:
            return error    # la página y las redirecciones usan la respuesta normal de Flask
        if error.code >= 500:
            codigo, plantilla = ERRORES_HTTP[500]
        else:
            codigo, plantilla = ERRORES_HTTP.get(error.code, ("peticion_invalida", "La petición no se pudo atender"))
        mensaje = plantilla.format(metodo=texto_visible(request.method, 20), ruta=texto_visible(request.path, 80))
        detalles = {"http": error.code}
        if error.code == 413:
            detalles["maximo_bytes"] = MAX_CUERPO
        permitidos = sorted(getattr(error, "valid_methods", None) or [])
        if permitidos:
            detalles["permitidos"] = permitidos
        if error.code < 500:
            anotar_rechazo(mensaje)
        respuesta, http = error_json(error.code, mensaje, codigo, detalles)
        if permitidos:
            respuesta.headers["Allow"] = ", ".join(permitidos)
        return respuesta, http

    @app.errorhandler(Exception)
    def error_inesperado(error: Exception):
        """Cualquier falla no prevista: 500 JSON sin traceback (el detalle va sólo al registro del servidor)."""
        app.logger.error("Error no previsto en %s %s", request.method, request.path, exc_info=error)
        try:
            ruta = texto_visible(f"{request.method} {request.path}", 120)
            gestor.actual().registrar_error(f"Falla no prevista al atender {ruta} ({type(error).__name__})")
        except Exception:
            pass    # anotar el evento es opcional; la respuesta JSON no depende de ello
        if not es_ruta_api():
            return InternalServerError()
        return error_json(500, MENSAJE_ERROR_INTERNO, "error_interno")


def _registrar_rutas(app: Flask, gestor: Gestor) -> None:
    """Rutas de §18. Cada una toma el simulador actual y le pasa los campos tal como llegan."""

    # ---- página y lectura ----

    @app.get("/")
    def pagina():
        return render_template("index.html")

    @app.get("/api/estado")
    def estado():
        desde = leer_desde()
        sim = gestor.actual()
        datos = sim.estado(desde)
        return exito(sim, datos, "Estado de la simulación", datos["version"])

    @app.get("/api/bitacora")
    def bitacora():
        desde = leer_desde()
        sim = gestor.actual()
        datos = sim.estado(desde)    # estado() lee la bitácora bajo el candado del simulador
        eventos = {"eventos": datos["eventos"], "ultimo_evento": datos["ultimo_evento"]}
        return exito(sim, eventos, "Eventos de la bitácora", datos["version"])

    @app.get("/api/nodos/<id_nodo>")
    def detalle_nodo(id_nodo):
        sim = gestor.actual()
        return exito(sim, sim.detalle_nodo(id_nodo), "Detalle del nodo")

    @app.get("/api/nodos/<id_nodo>/cadena")
    def cadena_nodo(id_nodo):
        sim = gestor.actual()
        cadena = sim.cadena_nodo(id_nodo)    # NoEncontrado (404) si el nodo no existe
        nodo = leer_id_nodo(id_nodo, "El nodo", sim.red.ids())   # "n1" -> "N01" para mostrar
        return exito(sim, {"nodo": nodo, "cadena": cadena}, f"Cadena del nodo {nodo} (altura {len(cadena) - 1})")

    # ---- simulación ----

    @app.post("/api/simulacion")
    def nueva_simulacion():
        cuerpo = leer_cuerpo()
        sim = gestor.reiniciar(cuerpo)
        config = sim.config
        modo = "PoW" if config.modo == "pow" else "PoS"
        mensaje = f"Nueva simulación {modo} con {config.num_nodos} nodos (semilla «{config.semilla}»)"
        return exito(sim, sim.estado(), mensaje)

    # ---- transacciones ----

    @app.post("/api/transacciones")
    def crear_transaccion():
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        tx = sim.crear_transaccion(cuerpo.get("emisor"), cuerpo.get("receptor"), cuerpo.get("monto"))
        return exito(sim, tx, "Transacción firmada y agregada a las pendientes")

    @app.post("/api/transacciones/firmada")
    def enviar_transaccion_firmada():
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        tx = sim.enviar_transaccion_firmada(cuerpo)   # el núcleo revisa campos, id y firma
        return exito(sim, tx, "Transacción firmada recibida y agregada a las pendientes")

    @app.post("/api/transacciones/aleatorias")
    def generar_transacciones():
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        return exito(sim, sim.generar_transacciones(cuerpo.get("cantidad")), "Transacciones generadas")

    # ---- Proof of Work ----

    @app.post("/api/pow/minar")
    def minar():
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        resultado = sim.iniciar_mineria(**opcionales(cuerpo, "bloques", "auto_tx"))
        return exito(sim, resultado, "Minería iniciada")

    @app.post("/api/pow/cancelar")
    def cancelar_mineria():
        leer_cuerpo()
        sim = gestor.actual()
        return exito(sim, sim.cancelar_mineria(), "Minería cancelada")

    # ---- Proof of Stake ----

    @app.post("/api/pos/ronda")
    def iniciar_ronda():
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        resultado = sim.iniciar_ronda_pos(**opcionales(cuerpo, "rondas", "auto_tx"))
        return exito(sim, resultado, "Ronda PoS iniciada: es momento de fijar las apuestas")

    @app.post("/api/pos/apuestas")
    def fijar_apuestas():
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        return exito(sim, sim.fijar_apuestas(cuerpo.get("apuestas")), "Apuestas actualizadas")

    @app.post("/api/pos/apostar-todo")
    def apostar_todo():
        leer_cuerpo()
        sim = gestor.actual()
        return exito(sim, sim.apostar_todo(), "Cada validador apostó todo su saldo disponible")

    @app.post("/api/pos/avanzar")
    def avanzar_pos():
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        resultado = sim.avanzar_pos(**opcionales(cuerpo, "estado_esperado", "numero_esperado", "intento_esperado"))
        return exito(sim, resultado, "La ronda avanzó")

    @app.post("/api/pos/automatico")
    def pos_automatico():
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        return exito(sim, sim.pos_automatico(cuerpo.get("activo")), "Modo automático actualizado")

    @app.post("/api/pos/votar")
    def votar():
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        return exito(sim, sim.votar_manual(cuerpo.get("nodo"), cuerpo.get("voto")), "Voto registrado")

    @app.post("/api/pos/cancelar")
    def cancelar_ronda():
        leer_cuerpo()
        sim = gestor.actual()
        return exito(sim, sim.cancelar_ronda_pos(), "Ronda PoS cancelada")

    # ---- nodos ----

    @app.post("/api/nodos/<id_nodo>/deshonesto")
    def marcar_deshonesto(id_nodo):
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        resultado = sim.marcar_deshonesto(id_nodo, cuerpo.get("deshonesto"), **opcionales(cuerpo, "trampa"))
        return exito(sim, resultado, "Comportamiento del nodo actualizado")

    @app.post("/api/nodos/<id_nodo>/conexion")
    def fijar_conexion(id_nodo):
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        return exito(sim, sim.fijar_conexion(id_nodo, cuerpo.get("conectado")), "Conexión del nodo actualizada")

    @app.post("/api/nodos/<id_nodo>/recibir")
    def recibir_cadena(id_nodo):
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        resultado = sim.recibir_cadena_externa(id_nodo, cuerpo.get("cadena"))
        return exito(sim, resultado, "El nodo revisó la cadena recibida")

    # ---- ataques (laboratorio de pruebas) ----

    @app.post("/api/ataques/alterar-bloque")
    def ataque_alterar_bloque():
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        resultado = sim.ataque_alterar_bloque(cuerpo.get("nodo"), cuerpo.get("numero"), cuerpo.get("tipo"))
        return exito(sim, resultado, "Ataque ejecutado: bloque alterado y difundido")

    @app.post("/api/ataques/cadena-corta")
    def ataque_cadena_corta():
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        resultado = sim.ataque_cadena_corta(cuerpo.get("nodo"), **opcionales(cuerpo, "quitar"))
        return exito(sim, resultado, "Ataque ejecutado: cadena corta difundida")

    @app.post("/api/ataques/transaccion")
    def ataque_transaccion():
        cuerpo = leer_cuerpo()
        sim = gestor.actual()
        resultado = sim.ataque_transaccion(cuerpo.get("tipo"), cuerpo.get("emisor"), cuerpo.get("receptor"),
                                           cuerpo.get("monto"), **opcionales(cuerpo, "firmante"))
        return exito(sim, resultado, "Ataque ejecutado con transacciones tramposas")


# ---------------------------------------------------------------------------
# Servidor
# ---------------------------------------------------------------------------

ERRORES_PROTOCOLO = {
    400: ("peticion_invalida", "La petición HTTP está mal formada"),
    414: ("url_demasiado_larga", "La dirección (URL) de la petición es demasiado larga"),
    431: ("cabeceras_demasiado_grandes", "La petición trae demasiadas cabeceras o son demasiado grandes"),
    505: ("version_http_no_soportada", "Versión de HTTP no soportada (use HTTP/1.0 o HTTP/1.1)"),
}


class ManejadorHTTP(WSGIRequestHandler):
    """Manejador del servidor de desarrollo que contesta en JSON los errores del protocolo HTTP.

    Una petición mal formada a ese nivel (línea inválida, versión HTTP/9.9, URL o
    cabeceras gigantes) la rechaza el servidor antes de llegar a Flask; el
    manejador normal respondería con una página HTML.
    """

    def send_error(self, code, message=None, explain=None):
        """Igual que el manejador normal, pero el cuerpo es el JSON de error del contrato."""
        codigo, mensaje = ERRORES_PROTOCOLO.get(code, ("peticion_invalida", "La petición no se pudo atender"))
        cuerpo = json.dumps({"ok": False, "error": mensaje, "codigo": codigo, "detalles": {"http": code}},
                            ensure_ascii=False).encode("utf-8")
        self.log_error("code %d, message %s", code, message)
        self.send_response(code, message)
        self.send_header("Connection", "close")
        con_cuerpo = code >= 200 and code not in (204, 205, 304)
        if con_cuerpo:
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        if self.command != "HEAD" and con_cuerpo:
            self.wfile.write(cuerpo)


def servir(host: str = "127.0.0.1", puerto: int = 5000) -> None:
    """Arranca el servidor de desarrollo (varias peticiones a la vez) con el manejador JSON."""
    app.run(host=host, port=puerto, debug=False, threaded=True, request_handler=ManejadorHTTP)


app = create_app()

if __name__ == "__main__":
    servir()
