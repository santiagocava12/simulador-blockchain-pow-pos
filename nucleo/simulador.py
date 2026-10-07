"""Fachada del simulador: la ÚNICA puerta de entrada al núcleo.

La interfaz web (``app.py``), el motor automático y las pruebas sólo llaman a
métodos de ``Simulador``. Cada método público:

* toma el candado (``threading.RLock``): dos peticiones nunca se mezclan;
* lee los valores crudos con ``nucleo.entradas`` ("N3", 3 y "n03" son el nodo
  N03; "abc" como monto es un error claro);
* es atómico: antes de cambiar algo guarda una instantánea del estado y, si algo
  falla, la restaura. La cadena y los saldos nunca quedan a medias;
* sólo lanza ``ErrorSimulacion`` (400 entrada inválida, 404 no encontrado, 409
  conflicto). Una falla imprevista se convierte en ``ErrorInterno`` (500), se
  anota en la bitácora y la operación no se aplica.

Las claves privadas de las carteras viven en ``self._claves``, fuera de la
instantánea: firman transacciones, votos y las trampas de los deshonestos.
"""

import copy
import functools
import hashlib
import random
import threading
import uuid
from collections import Counter

from nucleo.bitacora import Bitacora
from nucleo.bloque import crear_genesis, hash_bloque, id_transaccion, nuevo_bloque, resumen_bloque
from nucleo.bloque import crear_transaccion as firmar_transaccion
from nucleo.config import validar_config
from nucleo.cripto import firmar, generar_claves
from nucleo.entradas import (MAX_ENTERO, exigir_objeto, leer_bool, leer_entero, leer_id_nodo, leer_monto,
                             leer_opcion, texto_visible)
from nucleo.errores import Conflicto, EntradaInvalida, ErrorInterno, ErrorSimulacion
from nucleo.pos import ESTADOS as ESTADOS_POS
from nucleo.pos import (RondaPos, abrir_votacion, aceptar_bloque, avanzar_tras_rechazo, bloque_con_votos,
                        cancelar_ronda, castigar_proponente, contar_votos, ejecutar_sorteo, fijar_candidato,
                        registrar_voto)
from nucleo.pos import fijar_apuestas as fijar_apuestas_ronda
from nucleo.pow import (SesionPow, bloque_ganador, cancelar_sesion, ordenar_hallazgos, reanudar_tras_rechazo,
                        registrar_ganador, revisar_limite, ronda_pow, terminar_sesion)
from nucleo.red import TIMESTAMP_SIN_LIMITE, Red
from nucleo.reglas import mensaje_voto, probabilidades
from nucleo.reloj import Reloj
from nucleo.trampas import TRAMPAS, TRAMPAS_PROPONENTE, aplicar_trampa_recompensa, transacciones_tramposas
from nucleo.validacion import validar_bloque, validar_cadena, validar_candidato, validar_transaccion_nueva

MAX_BLOQUES_MINADOS = 20          # resultados PoW recientes que muestra la interfaz
MAX_CADENAS_MEMORIZADAS = 64      # cadenas ya validadas que recuerda verificar_invariantes
MAX_BLOQUES_DETALLE = 200         # bloques resumidos en detalle_nodo
MAX_CASTIGOS_BLOQUE = 500         # tope de castigos que caben en un bloque (bloque.py)
MAX_LOTE = 50                     # transacciones aleatorias, bloques o rondas por pedido
PASO_MINIMO_POS_MS = 800          # una transición PoS automática dura al menos esto (para que se vea)

TIPOS_ALTERACION = ("monto", "monto_rehash", "hash", "hash_anterior", "recompensa")
TIPOS_ATAQUE_TX = ("firma_alterada", "otra_clave", "doble_gasto", "repetida")
MENSAJE_ERROR_INTERNO = "Error interno: la operación no se aplicó"

DESCRIPCION_TRAMPAS = {
    "recompensa_falsa": "se asigna una recompensa 10 veces mayor a la establecida",
    "firma_alterada": "mete en su bloque una transacción con la firma alterada",
    "gasto_excesivo": "gasta en su bloque más dinero del que tiene",
    "doble_gasto": "gasta dos veces el mismo saldo dentro de su bloque",
    "voto_invertido": "vota al revés de lo que indica su validación (sólo en PoS)",
}

DESCRIPCION_ALTERACION = {
    "monto": "cambió el monto de la primera transacción (+1000) sin recalcular el hash",
    "monto_rehash": "cambió el monto de la primera transacción (+1000) y recalculó el hash del bloque",
    "hash": "reemplazó el hash del bloque por otro",
    "hash_anterior": "reemplazó el hash_anterior del bloque",
    "recompensa": "multiplicó por 10 la recompensa del bloque",
}

# Atributos que cambian durante la simulación: son los que guarda la instantánea.
CAMPOS_MUTABLES = ("red", "sesion_pow", "ronda_pos", "bitacora", "reloj", "version", "_contadores",
                   "_bloques_minados", "_ultimo_ganador", "_ultimo_bloque_pos", "_cadena_red")


# ---------------------------------------------------------------- ayudas de módulo

def _corto(hash_hex) -> str:
    """Los primeros 12 caracteres de un hash, para mensajes."""
    return f"{hash_hex[:12]}…" if isinstance(hash_hex, str) else "?"


def _plural(n: int, singular: str, plural: str) -> str:
    """'1 bloque', '3 bloques'."""
    return f"{n} {singular if n == 1 else plural}"


def _minuscula(texto: str) -> str:
    """El texto con la primera letra en minúscula (para seguir una frase tras dos puntos)."""
    return texto[:1].lower() + texto[1:]


def _alterar_hex(texto: str) -> str:
    """Cambia el primer carácter hexadecimal por otro distinto."""
    return ("1" if texto[:1] == "0" else "0") + texto[1:]


def _resumir_rechazos(resultados: list[dict]) -> str:
    """'9 × motivo' agrupando los rechazos de una difusión (los 3 motivos más comunes)."""
    motivos = Counter(r["motivo"] for r in resultados if not r["acepto"])
    return "; ".join(f"{n} × {motivo}" for motivo, n in motivos.most_common(3))


def _mismo_libro(a, b) -> bool:
    """True si dos libros tienen los mismos saldos, recompensas, castigos y transacciones."""
    return (a.saldos == b.saldos and a.quemado == b.quemado and a.altura == b.altura
            and a.recompensas_pendientes == b.recompensas_pendientes and a.ids_tx == b.ids_tx)


# ---------------------------------------------------------------- decoradores

def _operacion(evento_rechazo: str | None = None):
    """Envuelve un método que CAMBIA el estado: candado, instantánea y restauración.

    * Error esperado (ErrorSimulacion): se restaura la instantánea y se relanza.
      Si se indica `evento_rechazo`, se anota en la bitácora (p. ej. una
      transacción rechazada).
    * Cualquier otra excepción: se restaura, se anota un evento "error" y se
      lanza ErrorInterno. El usuario nunca ve un traceback.
    * Éxito: la versión sube en 1 (la interfaz sabe que algo cambió).
    """
    def decorador(metodo):
        @functools.wraps(metodo)
        def envoltura(self, *args, **kwargs):
            with self._candado:
                try:
                    instantanea = self._tomar_instantanea()
                except Exception as error:
                    raise self._error_interno(metodo.__name__.strip("_"), error) from None
                try:
                    resultado = metodo(self, *args, **kwargs)
                except ErrorSimulacion as error:
                    self._restaurar(instantanea)
                    if evento_rechazo:
                        self._anotar_rechazo(evento_rechazo, error)
                    raise
                except Exception as error:
                    self._restaurar(instantanea)
                    raise self._error_interno(metodo.__name__.strip("_"), error) from None
                self.version += 1
                return resultado
        return envoltura
    return decorador


def _lectura(metodo):
    """Envuelve un método que sólo LEE: candado y conversión de fallas imprevistas."""
    @functools.wraps(metodo)
    def envoltura(self, *args, **kwargs):
        with self._candado:
            try:
                return metodo(self, *args, **kwargs)
            except ErrorSimulacion:
                raise
            except Exception as error:
                raise self._error_interno(metodo.__name__.strip("_"), error) from None
    return envoltura


# ---------------------------------------------------------------- simulador

class Simulador:
    """Una simulación completa: red de nodos, transacciones, PoW o PoS, ataques y bitácora."""

    def __init__(self, config=None):
        self.config = validar_config(config)     # EntradaInvalida si algún parámetro está mal
        self._candado = threading.RLock()
        try:
            self._crear()
        except ErrorSimulacion:
            raise
        except Exception as error:
            raise ErrorInterno("Error interno: no se pudo crear la simulación",
                               detalles={"tipo": type(error).__name__}) from None

    def _crear(self) -> None:
        """Claves de cada nodo, génesis, red y bitácora."""
        config = self.config
        self.reloj = Reloj(config.reloj)
        ids = [f"N{i:02d}" for i in range(1, config.num_nodos + 1)]
        self._claves = {}
        directorio = {}
        for id_nodo in ids:
            self._claves[id_nodo], directorio[id_nodo] = generar_claves(config.semilla, id_nodo)
        self.genesis = crear_genesis(config, directorio, self.reloj.actual())
        self.red = Red(self.genesis, list(directorio.items()))
        self.bitacora = Bitacora()
        self.version = 0
        # Identifica esta simulación: al reiniciar, version y la numeración de eventos vuelven a
        # empezar, y así una pestaña abierta sabe que debe olvidar lo que mostraba.
        self.id_simulacion = uuid.uuid4().hex
        self.sesion_pow: SesionPow | None = None
        self.ronda_pos: RondaPos | None = None
        self._contadores = {"aleatorias": 0, "rondas_pos": 0}   # semillas de los generadores de azar
        self._bloques_minados: list[dict] = []
        self._ultimo_ganador: dict | None = None
        self._ultimo_bloque_pos: dict | None = None
        self._cadena_red: list[dict] = self.red.nodo_referencia().cadena   # la última que se vio (reorganizaciones)
        # Cachés: no forman parte del estado (se pueden recalcular en cualquier momento).
        self._cadenas_validas: dict[tuple, object] = {}
        self._cache_invariantes: tuple[int, list[str]] | None = None
        self._rechazo_anotado: tuple[int, str] | None = None

        if config.modo == "pow":
            ceros = _plural(config.dificultad, "cero", "ceros")
            detalle = (f"dificultad {config.dificultad} (el hash debe empezar con {ceros}), "
                       f"{config.intentos_por_ronda} nonces por ronda por minero, recompensa {config.recompensa} "
                       f"que madura tras 6 confirmaciones")
        else:
            detalle = (f"validadores: {config.seleccion_validadores}, castigo regla {config.regla_castigo}"
                       f"{f' (α = {config.alfa_porcentaje} %)' if config.regla_castigo == 'B' else ''}, "
                       f"recompensa {config.recompensa} al aceptarse el bloque")
        modo = "Proof of Work (PoW)" if config.modo == "pow" else "Proof of Stake (PoS)"
        self._evento("simulacion",
                     f"Nueva simulación {modo}: {len(ids)} nodos con {config.saldo_inicial} monedas cada uno, "
                     f"semilla «{config.semilla}», {detalle}. El génesis publica las claves públicas de todos.",
                     modo=config.modo, nodos=len(ids))

    # ============================================================ atomicidad

    def _tomar_instantanea(self) -> dict:
        """Copia profunda del estado mutable, para deshacer una operación que falle.

        Las cadenas de los nodos (la lista y sus bloques), los libros de saldos,
        las transacciones pendientes, los candidatos PoW y los eventos ya
        registrados NUNCA se modifican en su lugar (siempre se reemplazan por
        objetos nuevos). Por eso se comparten en vez de copiarse: la instantánea
        tarda milisegundos aunque haya 20 nodos con miles de bloques cada uno.
        """
        compartidos: dict[int, object] = {}

        def compartir(objeto) -> None:
            compartidos[id(objeto)] = objeto

        compartir(self.red.genesis)
        compartir(self._cadena_red)
        for nodo in self.red.nodos.values():
            compartir(nodo.libro)
            compartir(nodo.cadena)   # al adoptar otra cadena el nodo guarda una lista nueva
        for tx in self.red.pendientes:
            compartir(tx)
        if self.sesion_pow is not None:
            for bloque in self.sesion_pow.candidatos.values():
                compartir(bloque)
        for evento in self.bitacora._eventos:   # la bitácora sólo agrega eventos, nunca los cambia
            compartir(evento)
        estado = {campo: getattr(self, campo) for campo in CAMPOS_MUTABLES}
        return copy.deepcopy(estado, compartidos)

    def _restaurar(self, instantanea: dict) -> None:
        """Vuelve al estado guardado en la instantánea."""
        for campo, valor in instantanea.items():
            setattr(self, campo, valor)

    def _error_interno(self, operacion: str, error: Exception) -> ErrorInterno:
        """Anota la falla imprevista en la bitácora y devuelve el ErrorInterno a lanzar."""
        tipo = type(error).__name__
        try:
            detalle = texto_visible(str(error), 160)
            self._evento("error", f"Error interno en «{operacion}» ({tipo}: {detalle}); la operación "
                                  f"se revirtió y la simulación sigue íntegra", operacion=operacion, clase=tipo)
        except Exception:
            pass
        return ErrorInterno(MENSAJE_ERROR_INTERNO, detalles={"operacion": operacion, "tipo": tipo})

    def _anotar_rechazo(self, tipo: str, error: ErrorSimulacion) -> None:
        """Evento de una acción rechazada (transacción o voto). Nunca falla."""
        try:
            prefijo = "Transacción rechazada" if tipo == "transaccion_rechazada" else "Voto rechazado"
            self._evento(tipo, f"{prefijo}: {error.mensaje}", codigo=error.codigo)
            self._rechazo_anotado = (self.bitacora.ultimo, error.mensaje)
        except Exception:
            pass

    # ============================================================ ayudas generales

    def _evento(self, tipo: str, mensaje: str, **datos) -> None:
        """Agrega un evento a la bitácora."""
        self.bitacora.registrar(tipo, mensaje, **datos)

    def _leer_nodo(self, valor, nombre: str) -> str:
        """Id de nodo normalizado ("n3" -> "N03"); NoEncontrado si no existe."""
        return leer_id_nodo(valor, nombre, self.red.ids())

    def _exigir_modo(self, modo: str, accion: str) -> None:
        """Conflicto si la simulación no está en el modo que necesita la acción."""
        if self.config.modo != modo:
            actual = "Proof of Work (PoW)" if self.config.modo == "pow" else "Proof of Stake (PoS)"
            raise Conflicto(f"No se puede {accion}: esta simulación usa {actual}. "
                            f"Inicie una simulación nueva en modo {'PoW' if modo == 'pow' else 'PoS'}", "modo_incorrecto")

    def _hora_maxima(self) -> int:
        """La hora de los nodos (§21.2): un bloque recibido fechado después se rechaza ("en el futuro")."""
        return self.reloj.actual()

    def _referencia(self):
        """Nodo conectado con la cadena más alta: su punta es "la cadena de la red"."""
        return self.red.nodo_referencia()

    def _apuestas(self) -> dict[str, int]:
        """Apuestas bloqueadas por la ronda PoS en curso ({} si no hay)."""
        return self.ronda_pos.bloqueadas() if self.ronda_pos is not None else {}

    def _disponibles(self, con_apuestas: bool = True) -> dict[str, int]:
        """Por nodo: saldo en cadena menos lo comprometido (pendientes, castigos y, si se pide, apuestas)."""
        libro = self.red.libro_referencia()
        comprometido = self.red.comprometido(self._apuestas() if con_apuestas else None)
        return {id_nodo: max(0, libro.saldo(id_nodo) - comprometido[id_nodo]) for id_nodo in self.red.ids()}

    def _transacciones_para_bloque(self, apuestas: dict[str, int]) -> list[dict]:
        """Hasta max_tx_por_bloque pendientes, en orden, que siguen siendo válidas.

        Se revisan contra el libro de la red, contando como comprometidos los
        castigos (en PoS se aplican antes que las transacciones), las apuestas
        (deben seguir cubiertas) y las transacciones ya elegidas.
        """
        libro = self.red.libro_referencia()
        directorio = self.red.genesis["directorio"]
        comprometido = {id_nodo: 0 for id_nodo in self.red.ids()}
        for castigo in self.red.castigos_pendientes:
            comprometido[castigo["nodo"]] += castigo["monto"]
        for id_nodo, apuesta in apuestas.items():
            comprometido[id_nodo] += apuesta
        elegidas: list[dict] = []
        for tx in self.red.pendientes:
            if len(elegidas) >= self.config.max_tx_por_bloque:
                break
            try:
                validar_transaccion_nueva(tx, libro, directorio, elegidas, comprometido, TIMESTAMP_SIN_LIMITE)
            except ErrorSimulacion:
                continue
            elegidas.append(dict(tx))
            comprometido[tx["emisor"]] += tx["monto"]
        return elegidas

    def _revalidar_pendientes(self) -> None:
        """Descarta las pendientes que ya no son válidas (y lo anota en la bitácora)."""
        for tx in self.red.revalidar_pendientes(self._apuestas()):
            self._evento("transaccion_rechazada",
                         f"Se descartó de pendientes la transacción {tx['emisor']} → {tx['receptor']} por "
                         f"{tx['monto']}: {tx['motivo']}", nodo=tx["emisor"], id=tx["id"])

    def _revalidar_castigos(self) -> None:
        """Quita los castigos ya registrados en la cadena y los que el nodo ya no puede pagar."""
        if not self.red.castigos_pendientes:
            return
        libro = self.red.libro_referencia()
        registrados = [c for bloque in self._referencia().cadena[1:] for c in bloque["castigos"]]
        vigentes: list[dict] = []
        comprometido: Counter = Counter()
        for castigo in self.red.castigos_pendientes:
            if castigo in registrados:
                registrados.remove(castigo)   # ya está en un bloque: no se cobra dos veces
                continue
            if libro.saldo(castigo["nodo"]) - comprometido[castigo["nodo"]] < castigo["monto"]:
                self._evento("castigo", f"Se descartó el castigo de {castigo['nodo']} ({castigo['monto']}): con la "
                                        f"cadena actual ya no tiene saldo para pagarlo", nodo=castigo["nodo"])
                continue
            comprometido[castigo["nodo"]] += castigo["monto"]
            vigentes.append(castigo)
        self.red.castigos_pendientes = vigentes

    def _tras_cambio_de_red(self) -> None:
        """Después de que un nodo adoptó una cadena o cambió su conexión.

        Si la punta de la red cambió, la minería se reinicia sobre la nueva punta
        y la ronda PoS en curso se cancela. Las pendientes y castigos se revisan.
        """
        punta = self._referencia().ultimo_hash
        ronda = self.ronda_pos
        if ronda is not None and not ronda.es_final and ronda.hash_anterior != punta:
            cancelar_ronda(ronda, "La cadena de la red cambió mientras se votaba: la ronda se canceló y las "
                                  "apuestas se liberaron")
            self._evento("pos_estado", f"Ronda del bloque {ronda.numero} cancelada: la cadena de la red cambió "
                                       f"(nueva punta {_corto(punta)}). Las apuestas se liberaron",
                         bloque=ronda.numero)
        self._seguir_cadena_de_la_red()
        self._revalidar_castigos()
        self._revalidar_pendientes()
        sesion = self.sesion_pow
        if sesion is not None and sesion.estado == "minando" and sesion.plantilla["hash_anterior"] != punta:
            self._reiniciar_mineria(f"la cadena de la red cambió (nueva punta {_corto(punta)})")

    def _seguir_cadena_de_la_red(self) -> None:
        """La red adoptó otra cadena: las pendientes y los castigos se ajustan a ella.

        * Las pendientes que la nueva cadena ya incluye salen de pendientes (quedaron confirmadas).
        * Reorganización: si la cadena de la red dejó fuera bloques que tenía (cambió
          de rama tras una partición, por ejemplo), las transacciones y los castigos de
          esos bloques que no están en la nueva cadena vuelven al principio de las
          pendientes. Después se revalidan: lo que ya no es válido se descarta con su
          motivo.
        """
        antes, ahora = self._cadena_red, self._referencia().cadena
        self._cadena_red = ahora
        if len(antes) == len(ahora) and antes[-1]["hash"] == ahora[-1]["hash"]:
            return   # misma cadena
        libro = self.red.libro_referencia()

        comun = 0   # bloques iguales al principio (los hashes encadenados garantizan el resto)
        while comun < min(len(antes), len(ahora)) and antes[comun]["hash"] == ahora[comun]["hash"]:
            comun += 1
        abandonados = antes[comun:]
        if abandonados:
            pendientes = {tx["id"] for tx in self.red.pendientes}
            registrados = [c for bloque in ahora[1:] for c in bloque["castigos"]]
            txs, castigos = [], []
            for bloque in abandonados:
                for tx, firma in zip(bloque["transacciones"], bloque["firma"]):
                    id_tx = id_transaccion(tx)
                    if id_tx not in libro.ids_tx and id_tx not in pendientes:
                        txs.append({"id": id_tx, **tx, "firma": firma})
                        pendientes.add(id_tx)
                for castigo in bloque["castigos"]:
                    if castigo in registrados:
                        registrados.remove(castigo)   # la nueva cadena también lo registró
                    else:
                        castigos.append(dict(castigo))
            self.red.pendientes = txs + self.red.pendientes
            self.red.castigos_pendientes = castigos + self.red.castigos_pendientes
            devueltos = _plural(len(txs), "transacción volvió", "transacciones volvieron") + " a pendientes"
            if castigos:
                devueltos += f" y {_plural(len(castigos), 'castigo volvió', 'castigos volvieron')} a los pendientes"
            if len(abandonados) == 1:
                que = f"el bloque {abandonados[0]['numero']} que tenía ya no está"
            else:
                que = f"los bloques {abandonados[0]['numero']}–{abandonados[-1]['numero']} que tenía ya no están"
            self._evento("cadena_aceptada", f"Reorganización: la cadena de la red cambió y {que} en ella. De esos "
                                            f"bloques, {devueltos} (se revisan contra la nueva cadena)",
                         abandonados=len(abandonados), transacciones=len(txs), castigos=len(castigos))

        confirmadas = [tx for tx in self.red.pendientes if tx["id"] in libro.ids_tx]
        if confirmadas:
            self.red.quitar_pendientes({tx["id"] for tx in confirmadas})
            cuantas = _plural(len(confirmadas), "transacción pendiente ya está", "transacciones pendientes ya están")
            self._evento("transaccion", f"{cuantas} en la cadena que adoptó la red: quedaron confirmadas y salen "
                                        f"de pendientes", confirmadas=len(confirmadas))

    # ============================================================ lectura

    @_lectura
    def estado(self, desde_evento=0) -> dict:
        """Instantánea completa para la interfaz (§18). No avanza el reloj ni consume azar."""
        desde = 0 if desde_evento is None else leer_entero(desde_evento, "El parámetro desde", 0, MAX_ENTERO)
        ref = self._referencia()
        libro = ref.libro
        hash_red = ref.ultimo_hash
        salida: Counter = Counter()
        for tx in self.red.pendientes:
            salida[tx["emisor"]] += tx["monto"]
        castigos: Counter = Counter()
        for castigo in self.red.castigos_pendientes:
            castigos[castigo["nodo"]] += castigo["monto"]
        apuestas = self._apuestas()
        pos = self._estado_pos()
        validadores = {v["id"]: v for v in pos.get("validadores", [])}
        mineros = {m.id: m for m in self.sesion_pow.mineros} if self.sesion_pow is not None else {}

        nodos = []
        for nodo in self.red.nodos.values():
            saldo = libro.saldo(nodo.id)
            apuesta = apuestas.get(nodo.id, 0)
            minero = mineros.get(nodo.id)
            validador = validadores.get(nodo.id)
            nodos.append({
                "id": nodo.id,
                "indice": nodo.indice,
                "altura": nodo.altura,
                "ultimo_hash": nodo.ultimo_hash,
                "sincronizado": nodo.ultimo_hash == hash_red,
                "conectado": nodo.conectado,
                "deshonesto": nodo.deshonesto,
                "trampa": nodo.trampa,
                "saldo_cadena": saldo,
                "pendiente_salida": salida[nodo.id],
                "apuesta_bloqueada": apuesta,
                "castigo_pendiente": castigos[nodo.id],
                "disponible": max(0, saldo - salida[nodo.id] - apuesta - castigos[nodo.id]),
                "recompensas_pendientes": libro.pendientes_de(nodo.id),
                "total_recompensas_pendientes": libro.total_pendiente(nodo.id),
                "minero": None if minero is None else {
                    "nonce": minero.nonce, "intentos": minero.intentos,
                    "ultimo_hash": minero.ultimo_hash, "encontro": minero.encontro},
                "validador": None if validador is None else {
                    "apuesta": validador["apuesta"], "probabilidad": validador["probabilidad"],
                    "voto": validador["voto"], "excluido": validador["excluido"]},
            })

        problemas = self._invariantes_en_cache()
        return {
            "id_simulacion": self.id_simulacion,
            "version": self.version,
            "modo": self.config.modo,
            "config": self.config.a_dict(),
            "confirmaciones": libro.confirmaciones,
            "altura_red": ref.altura,
            "sincronizados": self.red.sincronizados(),
            "hash_red": hash_red,
            "nodos": nodos,
            "pendientes": [dict(tx) for tx in self.red.pendientes],
            "castigos_pendientes": [dict(c) for c in self.red.castigos_pendientes],
            "pow": self._estado_pow(),
            "pos": pos,
            "eventos": self.bitacora.desde(desde),
            "ultimo_evento": self.bitacora.ultimo,
            "invariantes_ok": not problemas,
            "problemas_invariantes": list(problemas[:10]),
            "circulacion": {"total": libro.total_en_circulacion(), "quemado": libro.quemado,
                            "castigos_por_quemar": sum(castigos.values())},
        }

    def _estado_pow(self) -> dict:
        """Sesión PoW para la interfaz, con el último ganador y los bloques minados recientes."""
        sesion = self.sesion_pow
        datos = sesion.a_dict() if sesion is not None else {"estado": "inactivo"}
        if sesion is not None:
            datos["num_transacciones"] = len(sesion.txs)   # transacciones honestas del bloque que se mina
        datos["ultimo_ganador"] = copy.deepcopy(self._ultimo_ganador)
        datos["bloques_minados"] = copy.deepcopy(self._bloques_minados)
        return datos

    def _estado_pos(self) -> dict:
        """Ronda PoS para la interfaz, con el boleto del sorteo y el último bloque aceptado."""
        ronda = self.ronda_pos
        datos = ronda.a_dict() if ronda is not None else {"estado": "INACTIVA"}
        if ronda is not None:
            datos["hash_anterior"] = ronda.hash_anterior   # parte pública de la semilla del sorteo
        datos["boleto"] = None
        if ronda is not None and ronda.estado in ("SORTEO", "CANDIDATO", "VOTACION") and ronda.apuestas:
            # El número sorteado r de la guía: sha256(hash_anterior|numero|intento) mod A.
            semilla = f"{ronda.hash_anterior}|{ronda.numero}|{ronda.intento}".encode()
            datos["boleto"] = int(hashlib.sha256(semilla).hexdigest(), 16) % ronda.total_apostado()
        datos["ultimo_bloque"] = copy.deepcopy(self._ultimo_bloque_pos)
        return datos

    @_lectura
    def detalle_nodo(self, id) -> dict:
        """Lo que sabe un nodo: su cadena (resumida), los saldos según su copia y sus recompensas."""
        nodo = self.red.nodo(self._leer_nodo(id, "El nodo"))
        libro = nodo.libro
        bloques = nodo.cadena[-MAX_BLOQUES_DETALLE:]
        return {
            **nodo.resumen(),
            "indice": nodo.indice,
            "clave_publica": nodo.clave_publica,
            "sincronizado": nodo.ultimo_hash == self._referencia().ultimo_hash,
            "descripcion_trampa": DESCRIPCION_TRAMPAS.get(nodo.trampa) if nodo.deshonesto else None,
            "saldo_cadena": libro.saldo(nodo.id),
            "recompensas_pendientes": libro.pendientes_de(nodo.id),
            "total_recompensas_pendientes": libro.total_pendiente(nodo.id),
            "disponible": self._disponibles()[nodo.id],
            "saldos": dict(libro.saldos),
            "quemado": libro.quemado,
            "total_en_circulacion": libro.total_en_circulacion(),
            "bloques": [resumen_bloque(b) for b in bloques],
            "bloques_omitidos": len(nodo.cadena) - len(bloques),
        }

    @_lectura
    def cadena_nodo(self, id) -> list[dict]:
        """Copia profunda de la cadena completa del nodo (modificarla no cambia nada)."""
        nodo = self.red.nodo(self._leer_nodo(id, "El nodo"))
        return copy.deepcopy(nodo.cadena)

    @_lectura
    def verificar_invariantes(self) -> list[str]:
        """Revisa que nada esté roto. Devuelve [] o la lista de problemas encontrados.

        Cada cadena de cada nodo es válida y su libro coincide con ella; ningún
        saldo es negativo; el dinero se conserva (total = saldos iniciales +
        R · altura − quemado); las pendientes son válidas en orden y sin ids
        repetidos; los castigos pendientes y las apuestas caben en los saldos.
        """
        return list(self._problemas_invariantes())

    def _invariantes_en_cache(self) -> list[str]:
        """Problemas de invariantes, recalculados sólo si la versión cambió (estado() debe ser rápido)."""
        if self._cache_invariantes is None or self._cache_invariantes[0] != self.version:
            self._cache_invariantes = (self.version, self._problemas_invariantes())
        return self._cache_invariantes[1]

    def _problemas_invariantes(self) -> list[str]:
        problemas: list[str] = []
        genesis = self.red.genesis
        dinero_inicial = sum(genesis["saldos_iniciales"].values())
        recompensa = genesis["parametros"]["recompensa"]
        try:
            problemas.extend(self._problemas_integridad())
        except Exception as error:
            problemas.append(f"No se pudo revisar la integridad de los bloques ({type(error).__name__})")
        for nodo in self.red.nodos.values():
            try:
                valida, motivo, libro = self._validar_cadena_memorizada(nodo.cadena)
                if not valida:
                    problemas.append(f"La cadena de {nodo.id} no es válida: {motivo}")
                    continue
                if not _mismo_libro(nodo.libro, libro):
                    problemas.append(f"El libro de saldos de {nodo.id} no coincide con su cadena")
                negativos = sorted(i for i, saldo in libro.saldos.items() if saldo < 0)
                if negativos:
                    problemas.append(f"Saldos negativos en la cadena de {nodo.id}: {', '.join(negativos)}")
                esperado = dinero_inicial + recompensa * libro.altura - libro.quemado
                total = libro.total_en_circulacion()
                if total != esperado:
                    problemas.append(f"No se conserva el dinero en {nodo.id}: hay {total} y debería haber "
                                     f"{esperado} (iniciales + R·altura − quemado)")
            except Exception as error:
                problemas.append(f"No se pudo revisar la cadena de {nodo.id} ({type(error).__name__})")
        try:
            problemas.extend(self._problemas_pendientes())
        except Exception as error:
            problemas.append(f"No se pudieron revisar las pendientes ({type(error).__name__})")
        return problemas

    def _problemas_integridad(self) -> list[str]:
        """Cada bloque guardado conserva el contenido que dio su hash.

        Los nodos comparten bloques (nunca se modifican en su lugar). Recalcular
        el hash de cada bloque distinto detecta cualquier modificación en memoria,
        algo que la memoria de cadenas validadas por hash no vería.
        """
        problemas: list[str] = []
        revisados: set[int] = set()
        for nodo in self.red.nodos.values():
            for bloque in nodo.cadena:
                if id(bloque) in revisados:
                    continue
                revisados.add(id(bloque))
                if hash_bloque(bloque) != bloque["hash"]:
                    problemas.append(f"El bloque {bloque['numero']} de {nodo.id} cambió después de validarse "
                                     f"(su hash ya no coincide)")
        return problemas

    def _validar_cadena_memorizada(self, cadena: list[dict]):
        """validar_cadena, pero recordando las cadenas ya validadas (por la lista de sus hashes).

        Los bloques guardados nunca cambian y su hash se revisó al validarlos, así
        que la misma lista de hashes es la misma cadena. Si sólo se agregaron
        bloques al final, se validan nada más los nuevos a partir del libro ya
        calculado.
        """
        hashes = tuple(bloque["hash"] for bloque in cadena)
        memoria = self._cadenas_validas
        if hashes in memoria:
            return True, "cadena válida", memoria[hashes]
        libro = None
        for nuevos in range(1, min(len(cadena), 8)):
            previo = memoria.get(hashes[:-nuevos])
            if previo is not None:
                libro = previo.clonar()
                for i in range(len(cadena) - nuevos, len(cadena)):
                    error = validar_bloque(cadena[i], cadena[i - 1], libro, self.red.genesis)
                    if error:
                        return False, error, None
                break
        if libro is None:
            valida, motivo, libro = validar_cadena(cadena, self.red.genesis)
            if not valida:
                return False, motivo, None
        memoria[hashes] = libro
        while len(memoria) > MAX_CADENAS_MEMORIZADAS:
            del memoria[next(iter(memoria))]   # olvida la más antigua
        return True, "cadena válida", libro

    def _problemas_pendientes(self) -> list[str]:
        """Pendientes válidas en orden, sin repetir, y nada comprometido por encima del saldo."""
        problemas: list[str] = []
        libro = self.red.libro_referencia()
        directorio = self.red.genesis["directorio"]
        comprometido = {id_nodo: 0 for id_nodo in self.red.ids()}
        for castigo in self.red.castigos_pendientes:
            comprometido[castigo["nodo"]] += castigo["monto"]
        for id_nodo in comprometido:
            if comprometido[id_nodo] > libro.saldo(id_nodo):
                problemas.append(f"Los castigos pendientes de {id_nodo} ({comprometido[id_nodo]}) superan "
                                 f"su saldo ({libro.saldo(id_nodo)})")
        for id_nodo, apuesta in self._apuestas().items():
            if type(apuesta) is not int or apuesta < 1 or id_nodo not in comprometido:
                problemas.append(f"Apuesta inválida de {id_nodo}: {apuesta!r}")
                continue
            comprometido[id_nodo] += apuesta
        vistas: list[dict] = []
        for tx in self.red.pendientes:
            try:
                validar_transaccion_nueva(tx, libro, directorio, vistas, comprometido, TIMESTAMP_SIN_LIMITE)
            except ErrorSimulacion as error:
                problemas.append(f"La transacción pendiente {_corto(tx.get('id'))} ya no es válida: "
                                 f"{error.mensaje}")
                continue
            vistas.append(tx)
            comprometido[tx["emisor"]] += tx["monto"]
        for id_nodo, total in comprometido.items():
            if total > libro.saldo(id_nodo):
                problemas.append(f"{id_nodo} tiene comprometido {total}, más que su saldo "
                                 f"({libro.saldo(id_nodo)}): su disponible sería negativo")
        return problemas

    # ============================================================ bitácora desde fuera

    def registrar_rechazo(self, ruta: str, mensaje: str) -> None:
        """La app anota una petición rechazada ("entrada_rechazada"). Nunca lanza.

        Si el simulador ya anotó ese mismo rechazo (p. ej. "transacción
        rechazada"), no se repite.
        """
        try:
            with self._candado:
                if self._rechazo_anotado == (self.bitacora.ultimo, mensaje):
                    return
                ruta = texto_visible(str(ruta), 120)
                texto = texto_visible(str(mensaje), 300)
                self._evento("entrada_rechazada", f"Petición rechazada ({ruta}): {texto}", ruta=ruta)
        except Exception:
            pass

    def registrar_error(self, mensaje: str) -> None:
        """El motor anota una falla que no vino del simulador ("error"). Nunca lanza."""
        try:
            with self._candado:
                self._evento("error", texto_visible(str(mensaje), 300))
        except Exception:
            pass

    # ============================================================ transacciones

    @_operacion("transaccion_rechazada")
    def crear_transaccion(self, emisor, receptor, monto) -> dict:
        """El emisor firma con su cartera una transacción nueva; se valida y queda pendiente."""
        ids = self.red.ids()
        emisor = leer_id_nodo(emisor, "El emisor", ids)
        receptor = leer_id_nodo(receptor, "El receptor", ids)
        if emisor == receptor:
            raise EntradaInvalida(f"El emisor y el receptor deben ser distintos (ambos son {emisor})")
        monto = leer_monto(monto)
        tx = firmar_transaccion(emisor, receptor, monto, self.reloj.ahora(), self._claves[emisor])
        guardada = self.red.agregar_transaccion(tx, self.reloj.actual(), self._apuestas())
        self._evento("transaccion", f"{emisor} firmó una transacción a {receptor} por {monto}; queda pendiente "
                                    f"(id {_corto(tx['id'])})", nodo=emisor, receptor=receptor, monto=monto,
                     id=tx["id"])
        return guardada

    @_operacion("transaccion_rechazada")
    def enviar_transaccion_firmada(self, datos) -> dict:
        """Recibe una transacción ya firmada {emisor, receptor, monto, timestamp, firma, id?}."""
        tx = dict(exigir_objeto(datos))
        if "id" not in tx:
            try:
                tx["id"] = id_transaccion(tx)   # el id se deduce de los 4 campos firmados
            except Exception:
                pass   # faltan campos o tienen valores raros: la validación lo explicará
        guardada = self.red.agregar_transaccion(tx, self.reloj.actual(), self._apuestas())
        self._evento("transaccion", f"Llegó una transacción firmada de {guardada['emisor']} a {guardada['receptor']} "
                                    f"por {guardada['monto']}: la firma es válida y queda pendiente "
                                    f"(id {_corto(guardada['id'])})", nodo=guardada["emisor"],
                     receptor=guardada["receptor"], monto=guardada["monto"], id=guardada["id"])
        return guardada

    @_operacion()
    def generar_transacciones(self, cantidad) -> dict:
        """Crea de 1 a 50 transacciones válidas al azar (reproducibles con la semilla)."""
        cantidad = leer_entero(cantidad, "La cantidad de transacciones", 1, MAX_LOTE)
        creadas = self._generar_aleatorias(cantidad, "aleatorias")
        if not creadas:
            raise Conflicto("Ningún nodo tiene saldo disponible: no se pueden generar transacciones", "sin_saldo")
        mensaje = f"Se generaron {_plural(len(creadas), 'transacción aleatoria', 'transacciones aleatorias')}"
        if len(creadas) < cantidad:
            mensaje += f" de {cantidad} pedidas (ya no hay saldo disponible para más)"
        return {"mensaje": mensaje, "transacciones": creadas}

    def _generar_aleatorias(self, cantidad: int | None, contexto: str) -> list[dict]:
        """Transacciones válidas al azar; `cantidad` None = de 1 a 3. Devuelve las creadas."""
        self._contadores["aleatorias"] += 1
        azar = random.Random(f"{self.config.semilla}|{contexto}|{self._contadores['aleatorias']}")
        if cantidad is None:
            cantidad = azar.randint(1, 3)
        ids = self.red.ids()
        creadas = []
        for _ in range(cantidad):
            disponibles = self._disponibles()
            emisores = [i for i in ids if disponibles[i] >= 1]
            if not emisores:
                break
            emisor = azar.choice(emisores)
            receptor = azar.choice([i for i in ids if i != emisor])
            monto = azar.randint(1, max(1, disponibles[emisor] // 4))
            tx = firmar_transaccion(emisor, receptor, monto, self.reloj.ahora(), self._claves[emisor])
            creadas.append(self.red.agregar_transaccion(tx, self.reloj.actual(), self._apuestas()))
            self._evento("transaccion", f"{emisor} firmó una transacción a {receptor} por {monto} (generada al azar); "
                                        f"queda pendiente", nodo=emisor, receptor=receptor, monto=monto, id=tx["id"])
        return creadas

    def _asegurar_pendientes(self, auto_tx: bool, numero: int, que: str) -> None:
        """Revisa las pendientes; con auto_tx crea 1–3 si no hay. Conflicto si sigue sin haber."""
        self._revalidar_pendientes()
        if not self.red.pendientes and auto_tx:
            self._generar_aleatorias(None, f"auto_tx|{numero}")
        if not self.red.pendientes:
            extra = " (y ningún nodo tiene saldo para generarlas)" if auto_tx else ""
            raise Conflicto(f"No hay transacciones pendientes para {que}{extra}", "sin_pendientes")

    # ============================================================ Proof of Work

    @_operacion()
    def iniciar_mineria(self, bloques=1, auto_tx=False) -> dict:
        """Empieza a minar `bloques` bloques seguidos (1–50); el motor da los pasos."""
        self._exigir_modo("pow", "minar")
        bloques = leer_entero(bloques, "El número de bloques", 1, MAX_LOTE)
        auto_tx = leer_bool(auto_tx, "auto_tx (generar transacciones automáticamente)")
        sesion = self.sesion_pow
        if sesion is not None and sesion.activa:
            raise Conflicto(f"Ya se está minando el bloque {sesion.numero} (ronda {sesion.ronda}); espere a que "
                            f"termine o cancele la minería", "mineria_en_curso")
        self.sesion_pow = self._preparar_sesion_pow(bloques, auto_tx)
        nueva = self.sesion_pow
        return {**nueva.a_dict(), "mensaje": nueva.mensaje}

    def _preparar_sesion_pow(self, bloques: int, auto_tx: bool) -> SesionPow:
        """Sesión para minar el siguiente bloque sobre la punta de la red (Conflicto si no se puede)."""
        conectados = [n.id for n in self.red.conectados()]
        if not conectados:
            raise Conflicto("Ningún nodo está conectado: nadie puede minar", "sin_mineros")
        ref = self._referencia()
        anterior = ref.cadena[-1]
        numero = anterior["numero"] + 1
        self._asegurar_pendientes(auto_tx, numero, "minar")
        txs = self._transacciones_para_bloque({})
        if not txs:
            raise Conflicto("No hay transacciones pendientes válidas para minar", "sin_pendientes")

        # Las trampas se firman ANTES de pedir el timestamp del bloque (una
        # transacción no puede ser posterior a su bloque) y van al final del bloque.
        tramposas = {nodo.id: self._transacciones_tramposas(nodo, txs) for nodo in self.red.nodos.values()}
        timestamp = max(self.reloj.ahora(), anterior["timestamp"] + 1)
        candidatos = {nodo.id: self._candidato_pow(nodo, numero, timestamp, anterior["hash"], txs, tramposas[nodo.id])
                      for nodo in self.red.nodos.values()}
        # Lo común a todos los candidatos (sin proponente, recompensa, nonce ni hash).
        honesto = nuevo_bloque(numero, timestamp, txs, anterior["hash"], "", 0, "pow")
        plantilla = {k: v for k, v in honesto.items() if k not in ("proponente", "recompensa", "nonce", "hash")}
        sesion = SesionPow(numero, candidatos, self.red.ids(), self.config.intentos_por_ronda,
                           self.config.dificultad, self.config.max_rondas, bloques_restantes=bloques,
                           auto_tx=auto_tx, plantilla=plantilla, txs=txs)
        sesion.fijar_conectados(conectados)
        tramposos = [f"{n.id} ({n.trampa})" for n in self.red.nodos.values()
                     if n.deshonesto and n.trampa in TRAMPAS_PROPONENTE]
        extra = f" Mineros deshonestos: {', '.join(tramposos)}." if tramposos else ""
        restantes = f" (quedan {bloques} por minar)" if bloques > 1 else ""
        ceros = _plural(self.config.dificultad, "cero", "ceros")
        self._evento("mineria", f"Empieza la minería del bloque {numero}{restantes}: {len(conectados)} mineros "
                                f"conectados compiten, cada uno prueba {self.config.intentos_por_ronda} nonces por "
                                f"ronda; el hash debe empezar con {ceros} (se esperan unos "
                                f"{16 ** self.config.dificultad} intentos). Lleva "
                                f"{_plural(len(txs), 'transacción', 'transacciones')}.{extra}",
                     bloque=numero, mineros=len(conectados), transacciones=len(txs))
        return sesion

    def _transacciones_tramposas(self, nodo, txs: list[dict]) -> list[dict]:
        """Las transacciones que un nodo deshonesto mete en su bloque ([] si es honesto)."""
        if not nodo.deshonesto or nodo.trampa not in TRAMPAS_PROPONENTE:
            return []
        ids = self.red.ids()
        otro = ids[(ids.index(nodo.id) + 1) % len(ids)]
        saldo = self.red.libro_referencia().saldo(nodo.id)
        return transacciones_tramposas(nodo.trampa, nodo.id, otro, saldo, self._claves[nodo.id], self.reloj, txs)

    def _candidato_pow(self, nodo, numero: int, timestamp: int, hash_anterior: str, txs: list[dict],
                       tramposas: list[dict]) -> dict:
        """Bloque propio de un minero: él es el proponente y cobra la recompensa (falsa si hace trampa)."""
        trampa = nodo.trampa if nodo.deshonesto else None
        recompensa = aplicar_trampa_recompensa(self.config.recompensa, trampa)
        return nuevo_bloque(numero, timestamp, txs + tramposas, hash_anterior, nodo.id, recompensa, "pow")

    def _rehacer_candidato(self, id_nodo: str) -> bool:
        """Si se está minando, el candidato del nodo se rehace con su comportamiento nuevo.

        Conserva el avance del minero (nonce e intentos). Devuelve True si lo rehízo.
        """
        sesion = self.sesion_pow
        if sesion is None or sesion.estado != "minando":
            return False
        nodo = self.red.nodo(id_nodo)
        plantilla = sesion.plantilla
        tramposas = self._transacciones_tramposas(nodo, sesion.txs)
        timestamp = plantilla["timestamp"]
        if tramposas:   # las trampas recién firmadas son posteriores: el bloque también
            timestamp = max(self.reloj.ahora(), plantilla["timestamp"])
        sesion.candidatos[id_nodo] = self._candidato_pow(nodo, sesion.numero, timestamp,
                                                         plantilla["hash_anterior"], sesion.txs, tramposas)
        return True

    def _reiniciar_mineria(self, motivo: str) -> None:
        """La punta de la red cambió: se vuelve a empezar el bloque sobre la nueva punta."""
        sesion = self.sesion_pow
        try:
            self.sesion_pow = self._preparar_sesion_pow(sesion.bloques_restantes, sesion.auto_tx)
        except Conflicto as error:
            terminar_sesion(sesion, f"La minería se detuvo: {motivo} y {_minuscula(error.mensaje)}")
            self._evento("mineria", sesion.mensaje, bloque=sesion.numero)
            return
        self._evento("mineria", f"La minería se reinició sobre el bloque {self.sesion_pow.numero}: {motivo}",
                     bloque=self.sesion_pow.numero)

    @_operacion()
    def cancelar_mineria(self) -> dict:
        """Detiene la minería en curso; las transacciones siguen pendientes."""
        self._exigir_modo("pow", "cancelar la minería")
        sesion = self.sesion_pow
        if sesion is None or not sesion.activa:
            raise Conflicto("No hay minería en curso que cancelar", "sin_mineria")
        cancelar_sesion(sesion)
        self._evento("mineria", f"{sesion.mensaje}; las transacciones siguen pendientes", bloque=sesion.numero)
        return {**sesion.a_dict(), "mensaje": sesion.mensaje}

    def _paso_pow(self) -> dict:
        """Un paso de la minería: una ronda de k intentos por minero, o el siguiente bloque tras un ganador."""
        sesion = self.sesion_pow
        if sesion.estado == "ganador":
            return self._siguiente_bloque_pow()
        if sesion.plantilla["hash_anterior"] != self._referencia().ultimo_hash:
            self._reiniciar_mineria("la cadena de la red cambió")
            return {"avanzo": True, "modo": "pow", "estado": self.sesion_pow.estado,
                    "mensaje": self.sesion_pow.mensaje}
        hallazgos = ronda_pow(sesion, {n.id for n in self.red.conectados()})
        if hallazgos:
            self._cerrar_ronda_pow(hallazgos)
        if revisar_limite(sesion):
            self._evento("mineria", f"{sesion.mensaje}. El bloque {sesion.numero} no se minó y las transacciones "
                                    f"siguen pendientes", bloque=sesion.numero)
        return {"avanzo": True, "modo": "pow", "estado": sesion.estado, "ronda": sesion.ronda,
                "hallazgos": len(hallazgos), "mensaje": sesion.mensaje}

    def _cerrar_ronda_pow(self, hallazgos: list[dict]) -> None:
        """Prueba los hallazgos del mejor al peor hash; el primero que la red acepta gana."""
        sesion = self.sesion_pow
        ordenados = ordenar_hallazgos(hallazgos)
        if len(ordenados) > 1:
            lista = ", ".join(f"{h['minero']} ({_corto(h['hash'])})" for h in ordenados)
            self._evento("empate", f"Empate en la ronda {sesion.ronda}: {len(ordenados)} mineros encontraron un hash "
                                   f"válido para el bloque {sesion.numero}: {lista}. Gana el hash menor "
                                   f"({ordenados[0]['minero']}) si su bloque es válido",
                         bloque=sesion.numero, ronda=sesion.ronda, mineros=[h["minero"] for h in ordenados])
        ref = self._referencia()
        libro_antes = ref.libro
        base = ref.cadena
        for hallazgo in ordenados:
            bloque = bloque_ganador(sesion, hallazgo)
            minero = self.red.nodo(hallazgo["minero"])
            acepto, motivo = minero.recibir_cadena(base + [bloque], self.red.genesis, self._hora_maxima())
            if not acepto:
                self._evento("bloque_rechazado",
                             f"{minero.id} encontró el nonce {hallazgo['nonce']} (hash {_corto(hallazgo['hash'])}), "
                             f"pero su bloque {sesion.numero} es inválido y nadie lo agrega: {motivo}. "
                             f"La carrera sigue", nodo=minero.id, bloque=sesion.numero, motivo=motivo)
                reanudar_tras_rechazo(sesion, minero.id, motivo)
                continue
            resultados = self.red.difundir(minero.cadena, minero.id, self._hora_maxima())
            aceptaron = 1 + sum(1 for r in resultados if r["acepto"])
            registrar_ganador(sesion, hallazgo)
            empate = [h["minero"] for h in ordenados] if len(ordenados) > 1 else []
            desconectados = sum(1 for r in resultados if r["motivo"] == "desconectado")
            nota = ""
            if desconectados:
                quien = _plural(desconectados, "nodo desconectado no lo recibió", "nodos desconectados no lo recibieron")
                nota = f" ({quien})"
            self._evento("bloque_minado",
                         f"{minero.id} minó el bloque {sesion.numero} con el nonce {hallazgo['nonce']} en la ronda "
                         f"{sesion.ronda}: hash {_corto(hallazgo['hash'])}. Lo revisaron y agregaron "
                         f"{aceptaron} de {len(self.red.nodos)} nodos{nota}",
                         nodo=minero.id, bloque=sesion.numero, nonce=hallazgo["nonce"], hash=hallazgo["hash"],
                         ronda=sesion.ronda, aceptaron=aceptaron)
            registro = {"numero": sesion.numero, "minero": minero.id, "nonce": hallazgo["nonce"],
                        "hash": hallazgo["hash"], "ronda": sesion.ronda, "empate": empate,
                        "rechazados": [{"minero": h["minero"], "motivo": h["motivo"]}
                                       for h in sesion.historial[-10:]],
                        "aceptaron": aceptaron, "total": len(self.red.nodos)}
            self._ultimo_ganador = registro
            self._bloques_minados = (self._bloques_minados + [registro])[-MAX_BLOQUES_MINADOS:]
            self._despues_de_bloque(bloque, libro_antes)
            return

    def _siguiente_bloque_pow(self) -> dict:
        """Tras mostrar al ganador un paso, empieza el siguiente bloque (o termina)."""
        sesion = self.sesion_pow
        try:
            self.sesion_pow = self._preparar_sesion_pow(sesion.bloques_restantes - 1, sesion.auto_tx)
        except Conflicto as error:
            terminar_sesion(sesion, f"Bloque {sesion.numero} listo. La minería termina: {_minuscula(error.mensaje)}")
            self._evento("mineria", sesion.mensaje, bloque=sesion.numero)
            return {"avanzo": True, "modo": "pow", "estado": sesion.estado, "mensaje": sesion.mensaje}
        return {"avanzo": True, "modo": "pow", "estado": self.sesion_pow.estado, "mensaje": self.sesion_pow.mensaje}

    def _despues_de_bloque(self, bloque: dict, libro_antes) -> None:
        """Un bloque entró a la cadena: limpia pendientes y castigos y anota las recompensas."""
        incluidas = {id_transaccion(tx) for tx in bloque["transacciones"]}
        self.red.quitar_pendientes(incluidas)
        self._cadena_red = self._referencia().cadena   # la cadena de la red sólo creció
        self._revalidar_castigos()
        self._revalidar_pendientes()
        self._eventos_recompensas(libro_antes, self.red.libro_referencia())

    def _eventos_recompensas(self, antes, despues) -> None:
        """Compara dos libros y anota las recompensas nuevas pendientes y las acreditadas."""
        confirmaciones = despues.confirmaciones
        previas = {(r["bloque"], r["beneficiario"]) for r in antes.recompensas_pendientes}
        for r in despues.recompensas_pendientes:
            if (r["bloque"], r["beneficiario"]) not in previas:
                self._evento("recompensa_pendiente",
                             f"Recompensa de {r['monto']} para {r['beneficiario']} por el bloque {r['bloque']}: queda "
                             f"pendiente hasta que la cadena llegue al bloque {r['bloque'] + confirmaciones} "
                             f"({confirmaciones} confirmaciones); mientras tanto no se puede gastar",
                             nodo=r["beneficiario"], bloque=r["bloque"], monto=r["monto"])
        ya_acreditadas = {(r["bloque"], r["beneficiario"]) for r in antes.acreditadas}
        for r in despues.acreditadas:
            if (r["bloque"], r["beneficiario"]) in ya_acreditadas:
                continue
            if confirmaciones:
                mensaje = (f"Maduró la recompensa de {r['beneficiario']} por el bloque {r['bloque']} (+{r['monto']}): "
                           f"la cadena llegó a la altura {despues.altura} y ya la puede gastar")
            else:
                mensaje = (f"{r['beneficiario']} recibe la recompensa de {r['monto']} por el bloque {r['bloque']} "
                           f"(en PoS se paga en cuanto el bloque se acepta)")
            self._evento("recompensa_acreditada", mensaje, nodo=r["beneficiario"], bloque=r["bloque"],
                         monto=r["monto"])

    # ============================================================ Proof of Stake

    @_operacion()
    def iniciar_ronda_pos(self, rondas=1, auto_tx=False) -> dict:
        """Abre una ronda PoS (estado APUESTAS) con apuestas sugeridas para cada validador."""
        self._exigir_modo("pos", "iniciar una ronda PoS")
        rondas = leer_entero(rondas, "El número de rondas", 1, MAX_LOTE)
        auto_tx = leer_bool(auto_tx, "auto_tx (generar transacciones automáticamente)")
        ronda = self.ronda_pos
        if ronda is not None and not ronda.es_final:
            raise Conflicto(f"Ya hay una ronda PoS en curso para el bloque {ronda.numero} (estado {ronda.estado}); "
                            f"termínela o cancélela primero", "ronda_en_curso")
        self.ronda_pos = self._crear_ronda_pos(rondas, auto_tx, automatico=False)
        return {**self.ronda_pos.a_dict(), "mensaje": self.ronda_pos.mensaje}

    def _crear_ronda_pos(self, rondas: int, auto_tx: bool, automatico: bool) -> RondaPos:
        """Ronda nueva sobre la punta de la red: elige validadores y sugiere apuestas."""
        ref = self._referencia()
        numero = ref.altura + 1
        self._asegurar_pendientes(auto_tx, numero, "proponer")
        disponibles = self._disponibles(con_apuestas=False)
        # Sólo validan los conectados que tienen la cadena de la red. Uno que quedó en otra rama (tras
        # una partición) revisaría el candidato con otra cadena y votaría en contra de un bloque válido.
        en_la_red = [n.id for n in self.red.conectados() if n.ultimo_hash == ref.ultimo_hash]
        otra_rama = [n.id for n in self.red.conectados() if n.ultimo_hash != ref.ultimo_hash]
        en_otra_rama = ""
        if otra_rama:
            uno = len(otra_rama) == 1
            en_otra_rama = (f"{', '.join(otra_rama)} {'está' if uno else 'están'} en otra rama de la cadena y no "
                            f"{'puede' if uno else 'pueden'} validar")
        elegibles = [i for i in en_la_red if disponibles[i] > 0]
        if not elegibles:
            extra = f"; {en_otra_rama}" if en_otra_rama else ""
            raise Conflicto("Ningún validador con saldo: no se puede formar la ronda (los nodos conectados no "
                            f"tienen saldo disponible para apostar{extra})", "sin_validadores")
        self._contadores["rondas_pos"] += 1
        contador = self._contadores["rondas_pos"]
        semilla = self.config.semilla
        if self.config.seleccion_validadores == "aleatorio":
            azar = random.Random(f"{semilla}|validadores|{numero}|{contador}")
            elegidos = sorted(azar.sample(elegibles, min(self.config.num_validadores, len(elegibles))))
        else:
            elegidos = elegibles
        azar = random.Random(f"{semilla}|apuestas|{numero}|{contador}")
        apuestas = {}
        for id_nodo in elegidos:
            d = disponibles[id_nodo]
            apuestas[id_nodo] = azar.randint(max(1, d // 10), max(1, d // 2))
        ronda = RondaPos(numero, ref.ultimo_hash, apuestas, rondas_restantes=rondas, auto_tx=auto_tx,
                         automatico=automatico)
        lista = ", ".join(f"{i} ({a})" for i, a in ronda.apuestas.items())
        restantes = f" Quedan {rondas} rondas por hacer." if rondas > 1 else ""
        if en_otra_rama:
            adoptara = "adoptará" if len(otra_rama) == 1 else "adoptarán"
            restantes += f" {en_otra_rama}: {adoptara} la cadena de la red cuando se acepte el bloque."
        self._evento("pos_estado", f"Nueva ronda PoS para el bloque {numero}: estado APUESTAS. Validadores con su "
                                   f"apuesta sugerida: {lista}; A = {ronda.total_apostado()}.{restantes}",
                     bloque=numero, estado="APUESTAS", validadores=list(ronda.apuestas))
        return ronda

    def _ronda_en_curso(self, accion: str) -> RondaPos:
        """La ronda PoS no terminada; Conflicto si no hay."""
        ronda = self.ronda_pos
        if ronda is None or ronda.es_final:
            detalle = f" (la última terminó en {ronda.estado})" if ronda is not None else ""
            raise Conflicto(f"No hay una ronda PoS en curso{detalle}: inicie una ronda para {accion}", "sin_ronda")
        return ronda

    @_operacion()
    def fijar_apuestas(self, apuestas) -> dict:
        """Cambia apuestas {nodo: monto} durante APUESTAS. Todo o nada."""
        self._exigir_modo("pos", "fijar apuestas")
        ronda = self._ronda_en_curso("fijar apuestas")
        fijar_apuestas_ronda(ronda, apuestas, self._disponibles(con_apuestas=False))
        self._evento("pos_estado", f"{ronda.mensaje} (bloque {ronda.numero})", bloque=ronda.numero,
                     estado=ronda.estado)
        return {**ronda.a_dict(), "mensaje": ronda.mensaje}

    @_operacion()
    def apostar_todo(self) -> dict:
        """Cada validador apuesta todo su saldo disponible."""
        self._exigir_modo("pos", "apostar")
        ronda = self._ronda_en_curso("apostar")
        disponibles = self._disponibles(con_apuestas=False)
        todo = {id_nodo: disponibles[id_nodo] for id_nodo in ronda.apuestas}
        fijar_apuestas_ronda(ronda, todo, disponibles)
        mensaje = f"Cada validador apostó todo su saldo disponible; A = {ronda.total_apostado()}"
        ronda.mensaje = mensaje
        self._evento("pos_estado", f"{mensaje} (bloque {ronda.numero})", bloque=ronda.numero, estado=ronda.estado)
        return {**ronda.a_dict(), "mensaje": mensaje}

    @_operacion()
    def avanzar_pos(self, estado_esperado=None, numero_esperado=None, intento_esperado=None) -> dict:
        """Una transición de la ronda.

        Lo "esperado" es lo que muestra la pestaña que pide avanzar (estado, bloque e
        intento, todos opcionales). Si la ronda ya no es esa (otra pestaña la avanzó)
        ⇒ Conflicto: así dos pestañas no avanzan dos veces ni una pestaña atrasada
        decide una ronda que no ha visto.
        """
        self._exigir_modo("pos", "avanzar una ronda PoS")
        esperado = None
        if estado_esperado is not None:
            esperado = leer_opcion(estado_esperado, "El estado esperado", ESTADOS_POS)
        numero = None if numero_esperado is None else leer_entero(numero_esperado, "El bloque esperado", 1,
                                                                    MAX_ENTERO)
        intento = None if intento_esperado is None else leer_entero(intento_esperado, "El intento esperado", 0,
                                                                      MAX_ENTERO)
        ronda = self.ronda_pos
        if ronda is not None:
            detalles = {"estado_actual": ronda.estado, "numero_actual": ronda.numero, "intento_actual": ronda.intento}
            if esperado is not None and esperado != ronda.estado:
                raise Conflicto(f"La ronda ya avanzó (estado actual: {ronda.estado}); actualice la vista",
                                "estado_cambio", detalles)
            if (numero is not None and numero != ronda.numero) or (intento is not None and intento != ronda.intento):
                raise Conflicto(f"La ronda ya avanzó: ahora es la del bloque {ronda.numero}, intento {ronda.intento} "
                                f"(estado {ronda.estado}); actualice la vista", "estado_cambio", detalles)
        self._ronda_en_curso("avanzarla")
        return self._avanzar_ronda()

    @_operacion()
    def pos_automatico(self, activo) -> dict:
        """Activa o desactiva que el motor avance la ronda solo."""
        self._exigir_modo("pos", "usar el modo automático")
        activo = leer_bool(activo, "activo")
        ronda = self._ronda_en_curso("avanzarla automáticamente")
        ronda.automatico = activo
        if activo:
            segundos = max(self.config.intervalo_ms, PASO_MINIMO_POS_MS) / 1000
            mensaje = f"Modo automático activado: la ronda avanza sola cada {segundos:g} s"
        else:
            mensaje = "Modo automático desactivado: la ronda avanza con el botón"
        self._evento("pos_estado", f"{mensaje} (bloque {ronda.numero}, estado {ronda.estado})", bloque=ronda.numero,
                     estado=ronda.estado)
        return {**ronda.a_dict(), "mensaje": mensaje}

    @_operacion("voto_rechazado")
    def votar_manual(self, nodo, voto) -> dict:
        """Registra a mano el voto firmado de un nodo (para probar votos inválidos o repetidos)."""
        self._exigir_modo("pos", "votar")
        ronda = self._ronda_en_curso("votar")
        if ronda.estado != "VOTACION":
            raise Conflicto(f"Sólo se puede votar durante la VOTACION (la ronda está en {ronda.estado})",
                            "estado_invalido")
        id_nodo = self._leer_nodo(nodo, "El votante")
        valor = leer_bool(voto, "El voto")
        firma = firmar(self._claves[id_nodo], mensaje_voto(ronda.hash_candidato, id_nodo, valor))
        registro = registrar_voto(ronda, id_nodo, valor, firma, self.red.genesis["directorio"])
        favor, total, alcanza = contar_votos(ronda)
        self._evento("voto", f"{id_nodo} votó {'a favor' if valor else 'en contra'} a mano (peso {registro['peso']}); "
                             f"van {favor} a favor de A = {total}", nodo=id_nodo, bloque=ronda.numero, voto=valor)
        return {**ronda.a_dict(), "mensaje": f"Voto de {id_nodo} registrado"}

    @_operacion()
    def cancelar_ronda_pos(self) -> dict:
        """Cancela la ronda: las apuestas se liberan; los castigos ya hechos se quedan."""
        self._exigir_modo("pos", "cancelar una ronda PoS")
        ronda = self._ronda_en_curso("cancelarla")
        cancelar_ronda(ronda)
        self._evento("pos_estado", f"Ronda del bloque {ronda.numero} cancelada: las apuestas se liberaron"
                                   f"{' (los castigos ya aplicados se quedan)' if ronda.castigos_ronda else ''}",
                     bloque=ronda.numero, estado="CANCELADA")
        return {**ronda.a_dict(), "mensaje": ronda.mensaje}

    def _avanzar_ronda(self) -> dict:
        """Ejecuta la transición que toca según el estado de la ronda."""
        ronda = self.ronda_pos
        antes = ronda.estado
        if ronda.hash_anterior != self._referencia().ultimo_hash:
            self._tras_cambio_de_red()   # la cadena cambió: la ronda se cancela
            mensaje = ronda.mensaje
        elif antes == "APUESTAS":
            mensaje = self._pos_sorteo()
        elif antes == "SORTEO":
            mensaje = self._pos_candidato()
        elif antes == "CANDIDATO":
            mensaje = self._pos_votacion()
        elif antes == "VOTACION":
            mensaje = self._pos_decidir()
        else:   # RECHAZADO
            mensaje = self._pos_tras_rechazo()
        actual = self.ronda_pos
        return {**actual.a_dict(), "anterior": antes, "mensaje": mensaje}

    def _pos_sorteo(self) -> str:
        """APUESTAS|RECHAZADO → SORTEO: el sorteo público de la guía elige al proponente."""
        ronda = self.ronda_pos
        antes = ronda.estado
        proponente = ejecutar_sorteo(ronda)
        apuesta, total = ronda.apuestas[proponente], ronda.total_apostado()
        probabilidad = probabilidades(ronda.validadores)[proponente]
        self._evento("sorteo", f"Sorteo del bloque {ronda.numero}, intento {ronda.intento}: salió {proponente} "
                               f"(apuesta {apuesta} de A = {total}, probabilidad {probabilidad:.1%}). La semilla es "
                               f"pública: sha256(hash_anterior|{ronda.numero}|{ronda.intento})",
                     nodo=proponente, bloque=ronda.numero, intento=ronda.intento, probabilidad=probabilidad)
        self._evento("pos_estado", f"Bloque {ronda.numero}: {antes} → SORTEO", bloque=ronda.numero, estado="SORTEO")
        return ronda.mensaje

    def _pos_candidato(self) -> str:
        """SORTEO → CANDIDATO: el proponente arma su bloque (con trampas si es deshonesto)."""
        ronda = self.ronda_pos
        txs = self._transacciones_para_bloque(ronda.apuestas)
        if not txs:
            cancelar_ronda(ronda, "No quedan transacciones pendientes válidas: la ronda se canceló")
            self._evento("pos_estado", f"Ronda del bloque {ronda.numero} cancelada: no quedan transacciones "
                                       f"pendientes válidas para proponer", bloque=ronda.numero, estado="CANCELADA")
            return ronda.mensaje
        proponente = self.red.nodo(ronda.proponente)
        anterior = self._referencia().cadena[-1]
        tramposas = self._transacciones_tramposas(proponente, txs)   # antes del timestamp del bloque
        timestamp = max(self.reloj.ahora(), anterior["timestamp"] + 1)
        trampa = proponente.trampa if proponente.deshonesto else None
        recompensa = aplicar_trampa_recompensa(self.config.recompensa, trampa)
        # Un castigo de la ronda de un bloque más alto (de otra rama, tras una partición) haría inválido
        # este bloque ("castigo de un bloque futuro"): sigue pendiente hasta que la cadena llegue ahí.
        registrables = [c for c in self.red.castigos_pendientes if c["numero"] <= ronda.numero]
        castigos = [dict(c) for c in registrables[:MAX_CASTIGOS_BLOQUE]]
        candidato = nuevo_bloque(ronda.numero, timestamp, txs + tramposas, ronda.hash_anterior, proponente.id,
                                 recompensa, "pos", validadores=ronda.validadores, intento=ronda.intento,
                                 castigos=castigos)
        fijar_candidato(ronda, candidato)
        detalle = (f"; es deshonesto y {DESCRIPCION_TRAMPAS[trampa]}" if trampa in TRAMPAS_PROPONENTE else "")
        registrados = f" y registra {_plural(len(castigos), 'castigo', 'castigos')} pendientes" if castigos else ""
        self._evento("pos_estado", f"Bloque {ronda.numero}: SORTEO → CANDIDATO. {proponente.id} propone un bloque con "
                                   f"{_plural(len(candidato['transacciones']), 'transacción', 'transacciones')}"
                                   f"{registrados} (hash {_corto(ronda.hash_candidato)}){detalle}",
                     nodo=proponente.id, bloque=ronda.numero, estado="CANDIDATO")
        return ronda.mensaje

    def _pos_votacion(self) -> str:
        """CANDIDATO → VOTACION: cada validador revisa el candidato con SU copia y vota firmado."""
        ronda = self.ronda_pos
        abrir_votacion(ronda)
        directorio = self.red.genesis["directorio"]
        for validador in ronda.validadores:
            nodo = self.red.nodo(validador["id"])
            if not nodo.conectado:
                self._evento("voto", f"{nodo.id} está desconectado: no vota (su apuesta {validador['apuesta']} "
                                     f"sigue contando en A)", nodo=nodo.id, bloque=ronda.numero)
                continue
            valido, motivo = validar_candidato(nodo.cadena, nodo.libro, ronda.candidato, self.red.genesis)
            invertido = nodo.deshonesto and nodo.trampa == "voto_invertido"
            voto = (not valido) if invertido else valido
            firma = firmar(self._claves[nodo.id], mensaje_voto(ronda.hash_candidato, nodo.id, voto))
            registrar_voto(ronda, nodo.id, voto, firma, directorio)
            texto = f"{nodo.id} vota {'a favor' if voto else 'en contra'} (peso {validador['apuesta']})"
            if not valido:
                texto += f": {motivo}"
            if invertido:
                texto += " — es deshonesto e invierte su voto"
            self._evento("voto", texto, nodo=nodo.id, bloque=ronda.numero, voto=voto)
        favor, total, alcanza = contar_votos(ronda)
        self._evento("pos_estado", f"Bloque {ronda.numero}: CANDIDATO → VOTACION. {favor} a favor de A = {total}: "
                                   f"{'alcanza' if alcanza else 'no alcanza'} los 2/3 (3V ≥ 2A)",
                     bloque=ronda.numero, estado="VOTACION", V_favor=favor, A=total)
        return ronda.mensaje

    def _pos_decidir(self) -> str:
        """VOTACION → ACEPTADO (si 3V ≥ 2A y el bloque final es válido) o RECHAZADO (con castigo)."""
        ronda = self.ronda_pos
        favor, total, alcanza = contar_votos(ronda)
        ref = self._referencia()
        if alcanza:
            bloque = bloque_con_votos(ronda)
            bloque["votos"].sort(key=lambda v: v["validador"])
            bloque["hash"] = hash_bloque(bloque)
            proponente = self.red.nodo(ronda.proponente)
            if proponente.conectado:
                acepto, motivo = proponente.recibir_cadena(ref.cadena + [bloque], self.red.genesis,
                                                           self._hora_maxima())
            else:
                acepto, motivo = False, f"el proponente {proponente.id} está desconectado y su bloque no llegó a la red"
            if acepto:
                return self._pos_aceptar(bloque, ref.libro)
            motivo = f"el bloque final con los votos es inválido ({motivo})"
        else:
            valido, error = validar_candidato(ref.cadena, ref.libro, ronda.candidato, self.red.genesis)
            if valido:
                motivo = f"votos insuficientes: {favor} a favor de A = {total} (se necesitan 2/3: 3V ≥ 2A)"
            else:
                motivo = f"bloque inválido ({error}); votos a favor {favor} de A = {total}"
        castigo = castigar_proponente(ronda, self.config.regla_castigo, self.config.alfa_porcentaje, motivo)
        self.red.castigos_pendientes.append(castigo)
        if castigo["regla"] == "A":
            regla = "regla A: pierde toda su apuesta"
        else:
            regla = (f"regla B: min(apuesta {castigo['apuesta']}, ⌈{self.config.alfa_porcentaje} % de "
                     f"{castigo['valor_transacciones']}⌉)")
        self._evento("pos_estado", f"Bloque {ronda.numero}: VOTACION → RECHAZADO. {motivo}", bloque=ronda.numero,
                     estado="RECHAZADO", nodo=castigo["nodo"])
        self._evento("bloque_rechazado", f"El bloque {ronda.numero} que propuso {castigo['nodo']} (intento "
                                         f"{castigo['intento']}) fue rechazado y nadie lo agrega: {motivo}",
                     nodo=castigo["nodo"], bloque=ronda.numero, motivo=motivo)
        self._evento("castigo", f"{castigo['nodo']} es castigado con {castigo['monto']} ({regla}) y queda fuera de la "
                                f"ronda. El castigo se quema y se registrará en el siguiente bloque aceptado",
                     nodo=castigo["nodo"], bloque=ronda.numero, monto=castigo["monto"], regla=castigo["regla"])
        return ronda.mensaje

    def _pos_aceptar(self, bloque: dict, libro_antes) -> str:
        """El bloque entró: se difunde, se limpian pendientes y castigos y se liberan las apuestas."""
        ronda = self.ronda_pos
        proponente = self.red.nodo(ronda.proponente)
        resultados = self.red.difundir(proponente.cadena, proponente.id, self._hora_maxima())
        aceptaron = 1 + sum(1 for r in resultados if r["acepto"])
        aceptar_bloque(ronda, bloque)
        favor, total = ronda.resultado["V_favor"], ronda.resultado["A"]
        self._ultimo_bloque_pos = {"numero": ronda.numero, "proponente": proponente.id, "hash": bloque["hash"],
                                   "intento": ronda.intento, "V_favor": favor, "A": total,
                                   "aceptaron": aceptaron, "total": len(self.red.nodos)}
        self._evento("pos_estado", f"Bloque {ronda.numero}: VOTACION → ACEPTADO. {favor} de A = {total} a favor "
                                   f"(≥ 2/3); propuesto por {proponente.id} en el intento {ronda.intento}. Lo agregaron "
                                   f"{aceptaron} de {len(self.red.nodos)} nodos; las apuestas se liberan",
                     nodo=proponente.id, bloque=ronda.numero, estado="ACEPTADO", hash=bloque["hash"])
        for castigo in bloque["castigos"]:
            self._evento("castigo", f"El castigo de {castigo['nodo']} ({castigo['monto']}) quedó registrado en el "
                                    f"bloque {ronda.numero}: ese dinero se quemó", nodo=castigo["nodo"],
                         bloque=ronda.numero, monto=castigo["monto"])
        self._despues_de_bloque(bloque, libro_antes)
        mensaje = ronda.mensaje
        if ronda.rondas_restantes > 1:
            try:
                self.ronda_pos = self._crear_ronda_pos(ronda.rondas_restantes - 1, ronda.auto_tx, ronda.automatico)
                mensaje += f". Sigue la ronda del bloque {self.ronda_pos.numero}"
            except Conflicto as error:
                ronda.mensaje = f"{ronda.mensaje}. No se puede preparar la siguiente ronda: {_minuscula(error.mensaje)}"
                mensaje = ronda.mensaje
                self._evento("pos_estado", mensaje, bloque=ronda.numero, estado="ACEPTADO")
        return mensaje

    def _pos_tras_rechazo(self) -> str:
        """RECHAZADO → SORTEO (nuevo intento sin el castigado) o → SIN_VALIDADORES."""
        ronda = self.ronda_pos
        if ronda.apuestas:
            return self._pos_sorteo()
        avanzar_tras_rechazo(ronda)
        castigados = ", ".join(f"{e['id']} ({e['castigo']})" for e in ronda.excluidos)
        self._evento("sin_validadores", f"No quedan validadores para el bloque {ronda.numero}: la ronda termina sin "
                                        f"bloque. Castigados: {castigados}. Las transacciones siguen pendientes",
                     bloque=ronda.numero, estado="SIN_VALIDADORES")
        return ronda.mensaje

    # ============================================================ motor

    @_lectura
    def requiere_pasos(self) -> bool:
        """True si el motor debe dar pasos: se mina, o hay una ronda PoS automática sin terminar."""
        return self._requiere_pasos()

    def _requiere_pasos(self) -> bool:
        sesion, ronda = self.sesion_pow, self.ronda_pos
        if sesion is not None and sesion.activa:
            return True
        return ronda is not None and not ronda.es_final and ronda.automatico

    @_lectura
    def intervalo_s(self) -> float:
        """Segundos entre pasos del motor (una transición PoS dura al menos 0.8 s para que se vea)."""
        ronda = self.ronda_pos
        if self.config.modo == "pos" and ronda is not None and not ronda.es_final and ronda.automatico:
            return max(self.config.intervalo_ms, PASO_MINIMO_POS_MS) / 1000
        return self.config.intervalo_ms / 1000

    def paso(self) -> dict:
        """Un tick del motor: una ronda de minería PoW o una transición PoS automática."""
        with self._candado:
            if not self._requiere_pasos():
                return {"avanzo": False, "mensaje": "No hay minería ni ronda automática en curso"}
            try:
                return self._dar_paso()
            except ErrorInterno:
                self._detener_automatico()
                raise

    @_operacion()
    def _dar_paso(self) -> dict:
        if self.sesion_pow is not None and self.sesion_pow.activa:
            return self._paso_pow()
        return self._avanzar_ronda()

    def _detener_automatico(self) -> None:
        """Tras una falla imprevista en un paso, se detiene lo automático (si no, fallaría en cada tick)."""
        try:
            sesion, ronda = self.sesion_pow, self.ronda_pos
            if sesion is not None and sesion.activa:
                cancelar_sesion(sesion)
                sesion.mensaje = "La minería se detuvo por un error interno; puede iniciarla de nuevo"
            if ronda is not None and ronda.automatico:
                ronda.automatico = False
            self.version += 1
        except Exception:
            pass

    # ============================================================ nodos

    @_operacion()
    def marcar_deshonesto(self, id, deshonesto, trampa=None) -> dict:
        """Hace a un nodo deshonesto (con una trampa) u honesto otra vez."""
        id_nodo = self._leer_nodo(id, "El nodo")
        deshonesto = leer_bool(deshonesto, "deshonesto")
        if deshonesto:
            if trampa is None or (isinstance(trampa, str) and not trampa.strip()):
                raise EntradaInvalida(f"Indique la trampa del nodo deshonesto: uno de {', '.join(TRAMPAS)}")
            trampa = leer_opcion(trampa, "La trampa", TRAMPAS)
        else:
            trampa = None
        nodo = self.red.nodo(id_nodo)
        nodo.deshonesto, nodo.trampa = deshonesto, trampa
        rehecho = self._rehacer_candidato(id_nodo)
        if deshonesto:
            mensaje = f"{id_nodo} ahora es deshonesto: {DESCRIPCION_TRAMPAS[trampa]}"
            if trampa == "voto_invertido" and self.config.modo == "pow":
                mensaje += " (en PoW esta trampa no tiene efecto)"
        else:
            mensaje = f"{id_nodo} vuelve a ser honesto"
        if rehecho:
            mensaje += f"; su bloque candidato para el bloque {self.sesion_pow.numero} se rehízo"
        self._evento("nodo", mensaje, nodo=id_nodo, deshonesto=deshonesto, trampa=trampa)
        return {**nodo.resumen(), "mensaje": mensaje}

    @_operacion()
    def fijar_conexion(self, id, conectado) -> dict:
        """Desconecta un nodo (no mina, no vota, no recibe bloques) o lo reconecta y lo pone al día."""
        id_nodo = self._leer_nodo(id, "El nodo")
        conectado = leer_bool(conectado, "conectado")
        nodo = self.red.nodo(id_nodo)
        if nodo.conectado == conectado:
            mensaje = f"{id_nodo} ya estaba {'conectado' if conectado else 'desconectado'}"
            return {**nodo.resumen(), "mensaje": mensaje}
        nodo.conectado = conectado
        if not conectado:
            mensaje = (f"{id_nodo} se desconectó: no mina, no vota y no recibe bloques hasta reconectarse "
                       f"(se quedó en el bloque {nodo.altura})")
            self._evento("nodo", mensaje, nodo=id_nodo, conectado=False)
        else:
            sincronia = self.red.sincronizar(id_nodo)
            if sincronia["adopto_de"]:
                mensaje = (f"{id_nodo} se reconectó y adoptó la cadena válida más larga (de {sincronia['adopto_de']}): "
                           f"bloque {sincronia['altura_antes']} → {sincronia['altura_despues']}")
            else:
                mensaje = f"{id_nodo} se reconectó; su cadena ya estaba al día (bloque {nodo.altura})"
            otros = [n.altura for n in self.red.conectados() if n.id != id_nodo]
            if otros and nodo.altura > max(otros):   # traía una cadena más larga: la comparte
                resultados = self.red.difundir(nodo.cadena, id_nodo, self._hora_maxima())
                aceptaron = sum(1 for r in resultados if r["acepto"])
                mensaje += f"; además compartió su cadena más larga y {aceptaron} nodos la adoptaron"
            self._evento("nodo", mensaje, nodo=id_nodo, conectado=True, altura=nodo.altura)
        if self.sesion_pow is not None and self.sesion_pow.activa:
            self.sesion_pow.fijar_conectados(n.id for n in self.red.conectados())
        self._tras_cambio_de_red()
        return {**nodo.resumen(), "mensaje": mensaje}

    @_operacion()
    def recibir_cadena_externa(self, id, cadena) -> dict:
        """Un nodo recibe una cadena por HTTP: la valida (reglas a–d de la guía) y la adopta si procede.

        Los bloques no pueden estar fechados después de la hora de la red
        (reloj.actual()). Si el nodo la adopta y está conectado, la difunde al
        resto, que la valida cada uno con su propia copia.
        """
        id_nodo = self._leer_nodo(id, "El nodo")
        nodo = self.red.nodo(id_nodo)
        altura_antes = nodo.altura
        acepto, motivo = nodo.recibir_cadena(cadena, self.red.genesis, self._hora_maxima())
        resultados: list[dict] = []
        if acepto:
            mensaje = (f"{id_nodo} recibió una cadena válida y más larga y la adoptó (bloque {altura_antes} → "
                       f"{nodo.altura})")
            if nodo.conectado:
                resultados = self.red.difundir(nodo.cadena, id_nodo, self._hora_maxima())
                aceptaron = sum(1 for r in resultados if r["acepto"])
                mensaje += f"; la difundió y {aceptaron} de {len(resultados)} nodos también la adoptaron"
            else:
                mensaje += "; está desconectado: la compartirá al reconectarse"
            self._evento("cadena_aceptada", mensaje, nodo=id_nodo, altura=nodo.altura)
            self._tras_cambio_de_red()
        else:
            mensaje = f"{id_nodo} rechazó la cadena recibida: {motivo}"
            self._evento("cadena_rechazada", mensaje, nodo=id_nodo, motivo=motivo)
        return {"acepto": acepto, "motivo": motivo, "nodo": id_nodo, "altura": nodo.altura,
                "resultados": resultados, "mensaje": mensaje}

    # ============================================================ ataques

    @_operacion()
    def ataque_alterar_bloque(self, nodo, numero, tipo) -> dict:
        """El nodo altera un bloque de una COPIA de su cadena y la difunde: nadie la acepta."""
        id_nodo = self._leer_nodo(nodo, "El nodo atacante")
        origen = self.red.nodo(id_nodo)
        numero = leer_entero(numero, "El número de bloque", 0, origen.altura)
        tipo = leer_opcion(tipo, "El tipo de alteración", TIPOS_ALTERACION)
        self._exigir_conectado(origen, "difundir la cadena alterada")
        copia = list(origen.cadena)   # los demás bloques son los mismos: no se modifican
        bloque = copia[numero] = copy.deepcopy(copia[numero])
        if tipo in ("monto", "monto_rehash"):
            if not bloque["transacciones"]:
                raise EntradaInvalida(f"El bloque {numero} no tiene transacciones: elija otro bloque u otro tipo")
            bloque["transacciones"][0]["monto"] += 1000
            if tipo == "monto_rehash":
                bloque["hash"] = hash_bloque(bloque)
        elif tipo == "hash":
            bloque["hash"] = _alterar_hex(bloque["hash"])
        elif tipo == "hash_anterior":
            bloque["hash_anterior"] = _alterar_hex(bloque["hash_anterior"])
        else:   # recompensa
            if bloque["recompensa"] is None:
                raise EntradaInvalida("El génesis no tiene recompensa: elija otro bloque u otro tipo")
            bloque["recompensa"]["monto"] *= 10
        resultados = self.red.difundir(copia, id_nodo, self._hora_maxima())
        rechazos = sum(1 for r in resultados if not r["acepto"])
        mensaje = (f"{id_nodo} {DESCRIPCION_ALTERACION[tipo]} en el bloque {numero} de una copia de su cadena y la "
                   f"difundió: {rechazos} de {len(resultados)} nodos la rechazaron ({_resumir_rechazos(resultados)}). "
                   f"Su propia cadena no cambió")
        self._evento("ataque", mensaje, nodo=id_nodo, bloque=numero, alteracion=tipo, rechazos=rechazos)
        self._anotar_cadena_rechazada(id_nodo, f"la copia con el bloque {numero} alterado", resultados)
        if rechazos < len(resultados):
            self._tras_cambio_de_red()
        return {"mensaje": mensaje, "resultados": resultados, "rechazos": rechazos}

    @_operacion()
    def ataque_cadena_corta(self, nodo, quitar=1) -> dict:
        """El nodo difunde su cadena sin los últimos `quitar` bloques: es más corta y nadie la acepta."""
        id_nodo = self._leer_nodo(nodo, "El nodo atacante")
        origen = self.red.nodo(id_nodo)
        if origen.altura == 0:
            raise Conflicto(f"La cadena de {id_nodo} sólo tiene el génesis: no hay bloques que quitar",
                            "sin_bloques")
        quitar = leer_entero(quitar, "El número de bloques a quitar", 1, origen.altura)
        self._exigir_conectado(origen, "difundir la cadena corta")
        corta = origen.cadena[:-quitar]
        resultados = self.red.difundir(corta, id_nodo, self._hora_maxima())
        rechazos = sum(1 for r in resultados if not r["acepto"])
        sin = "sin su último bloque" if quitar == 1 else f"sin los últimos {quitar} bloques"
        mensaje = (f"{id_nodo} difundió su cadena {sin} (altura {len(corta) - 1}): {rechazos} de {len(resultados)} "
                   f"nodos la rechazaron ({_resumir_rechazos(resultados)})")
        self._evento("ataque", mensaje, nodo=id_nodo, quitar=quitar, rechazos=rechazos)
        self._anotar_cadena_rechazada(id_nodo, f"la cadena {sin}", resultados)
        if rechazos < len(resultados):
            self._tras_cambio_de_red()
        return {"mensaje": mensaje, "resultados": resultados, "rechazos": rechazos}

    def _anotar_cadena_rechazada(self, origen: str, que: str, resultados: list[dict]) -> None:
        """Evento "cadena_rechazada" con los motivos de los nodos que revisaron la cadena y la rechazaron."""
        rechazos = [r for r in resultados if not r["acepto"] and r["motivo"] != "desconectado"]
        if not rechazos:
            return
        motivo = Counter(r["motivo"] for r in rechazos).most_common(1)[0][0]
        quienes = _plural(len(rechazos), "nodo la revisó y la rechazó", "nodos la revisaron y la rechazaron")
        self._evento("cadena_rechazada", f"Los nodos rechazaron {que} que difundió {origen}: {quienes} "
                                         f"({_resumir_rechazos(rechazos)})",
                     nodo=origen, motivo=motivo, rechazos=len(rechazos))

    def _exigir_conectado(self, nodo, accion: str) -> None:
        if not nodo.conectado:
            raise Conflicto(f"{nodo.id} está desconectado: no puede {accion}", "nodo_desconectado")

    @_operacion()
    def ataque_transaccion(self, tipo, emisor, receptor, monto, firmante=None) -> dict:
        """Envía transacciones tramposas a la red; cada rechazo se informa (no es un error de la petición)."""
        tipo = leer_opcion(tipo, "El tipo de ataque", TIPOS_ATAQUE_TX)
        ids = self.red.ids()
        emisor = leer_id_nodo(emisor, "El emisor", ids)
        receptor = leer_id_nodo(receptor, "El receptor", ids)
        if emisor == receptor:
            raise EntradaInvalida(f"El emisor y el receptor deben ser distintos (ambos son {emisor})")
        monto = leer_monto(monto)
        if firmante is not None:
            firmante = leer_id_nodo(firmante, "El firmante", ids)
        if tipo == "otra_clave":
            firmante = firmante or receptor
            if firmante == emisor:
                raise EntradaInvalida("Para firmar con otra clave, el firmante debe ser distinto del emisor")

        if tipo == "firma_alterada":
            tx = firmar_transaccion(emisor, receptor, monto, self.reloj.ahora(), self._claves[emisor])
            tx["firma"] = _alterar_hex(tx["firma"])
            envios = [tx]
            explicacion = f"{emisor} firmó una transacción a {receptor} por {monto} y después se alteró su firma"
        elif tipo == "otra_clave":
            envios = [firmar_transaccion(emisor, receptor, monto, self.reloj.ahora(), self._claves[firmante])]
            explicacion = (f"{firmante} intentó gastar el dinero de {emisor}: firmó con SU clave una transacción de "
                           f"{emisor} a {receptor} por {monto}")
        elif tipo == "doble_gasto":
            envios = [firmar_transaccion(emisor, receptor, monto, self.reloj.ahora(), self._claves[emisor])
                      for _ in range(2)]
            explicacion = f"{emisor} intentó gastar dos veces: envió dos transacciones de {monto} a {receptor}"
        else:   # repetida
            registrada = self._transaccion_ya_vista(emisor)
            if registrada is None:   # nada que repetir aún: se manda una nueva dos veces
                tx = firmar_transaccion(emisor, receptor, monto, self.reloj.ahora(), self._claves[emisor])
                envios = [tx, dict(tx)]
                explicacion = f"{emisor} envió dos veces la misma transacción a {receptor} por {monto}"
            else:
                envios = [registrada]
                explicacion = (f"Se reenvió una transacción ya conocida ({registrada['emisor']} → "
                               f"{registrada['receptor']} por {registrada['monto']})")

        aceptadas, rechazadas = [], []
        for tx in envios:
            try:
                aceptadas.append(self.red.agregar_transaccion(tx, self.reloj.actual(), self._apuestas()))
                self._evento("transaccion", f"Ataque: se aceptó la transacción {tx['emisor']} → {tx['receptor']} por "
                                            f"{tx['monto']} (era válida)", nodo=tx["emisor"], id=tx["id"])
            except ErrorSimulacion as error:
                rechazadas.append({"error": error.mensaje, "codigo": error.codigo, "transaccion": dict(tx)})
                self._evento("transaccion_rechazada", f"Ataque: transacción rechazada: {error.mensaje}",
                             nodo=tx["emisor"], codigo=error.codigo)
        mensaje = (f"{explicacion}. Resultado: {_plural(len(aceptadas), 'aceptada', 'aceptadas')} y "
                   f"{_plural(len(rechazadas), 'rechazada', 'rechazadas')}")
        if rechazadas:
            mensaje += f" ({rechazadas[0]['error']})"
        self._evento("ataque", mensaje, ataque=tipo, nodo=emisor, aceptadas=len(aceptadas), rechazadas=len(rechazadas))
        return {"mensaje": mensaje, "tipo": tipo, "aceptadas": aceptadas, "rechazadas": rechazadas}

    def _transaccion_ya_vista(self, emisor: str) -> dict | None:
        """Una transacción firmada que la red ya conoce: de la cadena (de preferencia del emisor) o pendiente."""
        ultima = None
        for bloque in reversed(self._referencia().cadena[1:]):
            for tx, firma in zip(reversed(bloque["transacciones"]), reversed(bloque["firma"])):
                firmada = {"id": id_transaccion(tx), **tx, "firma": firma}
                if tx["emisor"] == emisor:
                    return firmada
                ultima = ultima or firmada
        if ultima is not None:
            return ultima
        for tx in self.red.pendientes:
            return dict(tx)
        return None
