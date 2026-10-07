"""Nodos y red: cada nodo guarda su propia copia de la cadena y la valida.

La red reparte cadenas (difundir), ayuda a un nodo a ponerse al día
(sincronizar) y guarda las transacciones y castigos pendientes, que son
compartidos por todos como en una red real.
"""

import copy

from nucleo.bloque import hash_bloque, validar_estructura_bloque
from nucleo.entradas import texto_visible
from nucleo.errores import NoEncontrado
from nucleo.libro import Libro
from nucleo.validacion import (MAX_BLOQUES_CADENA, evaluar_cadena_recibida, revisar_sin_saldos, validar_bloque,
                               validar_cadena, validar_transaccion_nueva)

TIMESTAMP_SIN_LIMITE = 10**15   # al revalidar, las pendientes ya pasaron la revisión del tiempo


class Nodo:
    """Un participante de la red con su copia de la cadena y su libro de saldos."""

    def __init__(self, id: str, indice: int, clave_publica: str, genesis: dict):
        self.id = id
        self.indice = indice
        self.clave_publica = clave_publica
        self.cadena: list[dict] = [copy.deepcopy(genesis)]
        self.libro = Libro(genesis)
        self.conectado = True
        self.deshonesto = False
        self.trampa: str | None = None

    @property
    def altura(self) -> int:
        """Número del último bloque de su cadena."""
        return self.cadena[-1]["numero"]

    @property
    def ultimo_hash(self) -> str:
        """Hash del último bloque de su cadena."""
        return self.cadena[-1]["hash"]

    def recibir_cadena(self, cadena, genesis: dict, timestamp_max: int | None = None,
                       compartir: bool = False) -> tuple[bool, str]:
        """Valida una cadena recibida y la adopta si es válida y más larga. Nunca lanza.

        `timestamp_max` (opcional) es la hora del nodo: rechaza bloques fechados en el futuro.
        `compartir`: la cadena es la de otro nodo de la red, cuyos bloques nunca se
        modifican; se guardan esos mismos bloques en vez de copiarlos.

        Se valida la cadena COMPLETA: a cada bloque recibido se le recalcula el
        hash. Mientras ese hash recalculado sea igual al del bloque que el nodo ya
        tiene en la misma posición (y que ya validó con todas las reglas al
        adoptarlo), el contenido es idéntico y sus firmas, saldos y consenso ya
        están comprobados. A partir del primer bloque distinto se aplican todas
        las reglas, partiendo del libro de saldos del nodo. El resultado es el
        mismo que validar desde el génesis, pero sin repetir las firmas y saldos
        que el nodo ya revisó.
        """
        try:
            propia = self.cadena
            conocidos = self._bloques_conocidos(cadena, genesis)
            if conocidos and conocidos == len(cadena):
                # Es la propia o un pedazo inicial de ella: válida, pero no más larga (regla d).
                return False, f"no es más larga que la propia ({len(cadena) - 1} ≤ {len(propia) - 1})"
            if conocidos == len(propia) and len(cadena) <= MAX_BLOQUES_CADENA:
                # Extiende la propia: se validan sólo los bloques nuevos.
                libro = self.libro.clonar()
                for i in range(conocidos, len(cadena)):
                    error = validar_bloque(cadena[i], cadena[i - 1], libro, genesis, timestamp_max=timestamp_max)
                    if error:
                        return False, error
                nuevos = cadena[conocidos:]
                self.cadena = propia + (list(nuevos) if compartir else copy.deepcopy(nuevos))
                self.libro = libro
                return True, "aceptada"
            if 0 < conocidos < len(cadena) and len(cadena) <= MAX_BLOQUES_CADENA:
                # Se separa de la propia en el bloque `conocidos` (los anteriores ya son válidos).
                # Si ese bloque falla en su forma, enlace, hash o consenso, ése es justo el primer
                # error que daría validar toda la cadena: no hace falta revisar el resto.
                error = revisar_sin_saldos(cadena[conocidos], cadena[conocidos - 1], genesis, timestamp_max)
                if error:
                    return False, error
            acepta, motivo, libro = evaluar_cadena_recibida(cadena, propia, genesis, timestamp_max)
            if not acepta:
                return False, motivo
            self.cadena, self.libro = self._para_guardar(cadena, genesis, compartir), libro
            return True, motivo
        except Exception:
            return False, "la cadena tiene una estructura inválida"

    def _bloques_conocidos(self, cadena, genesis: dict) -> int:
        """Cuántos bloques del principio de `cadena` son idénticos a los que ya tiene este nodo.

        A cada bloque recibido se le RECALCULA el hash y se compara con el del
        bloque propio en esa posición (ya validado). Mismo hash recalculado ⇒
        mismo contenido. Un bloque alterado no cuenta aunque conserve su campo
        "hash". El génesis se compara completo.
        """
        propia = self.cadena
        if type(cadena) is not list or not cadena or propia[0]["hash"] != genesis["hash"]:
            return 0
        if cadena[0] is not propia[0] and not validar_cadena(cadena[:1], genesis)[0]:
            return 0
        conocidos = 1
        tope = min(len(cadena), len(propia))
        while conocidos < tope and self._mismo_bloque(cadena[conocidos], propia[conocidos]):
            conocidos += 1
        return conocidos

    @staticmethod
    def _mismo_bloque(recibido, propio: dict) -> bool:
        """True si `recibido` tiene exactamente el contenido del bloque propio (ya validado)."""
        if recibido is not propio:
            # Un objeto ajeno: primero su forma (tipos exactos), luego el hash.
            if type(recibido) is not dict or recibido.get("hash") != propio["hash"]:
                return False
            if validar_estructura_bloque(recibido) is not None:
                return False
        # Siempre se recalcula: también detecta un bloque propio modificado en memoria.
        return hash_bloque(recibido) == propio["hash"]

    def _para_guardar(self, cadena: list, genesis: dict, compartir: bool) -> list[dict]:
        """La cadena aceptada lista para guardarse.

        El génesis es siempre el de confianza. Un bloque con el mismo hash que el
        propio en esa posición es el mismo bloque (ambos hashes se recalcularon
        al validar): se conserva el propio. Los demás se copian, salvo que la
        cadena venga de otro nodo de la red (`compartir`): nada se comparte con
        quien la envió desde fuera.
        """
        propia = self.cadena
        nueva = [copy.deepcopy(genesis)]
        for i in range(1, len(cadena)):
            bloque = cadena[i]
            if i < len(propia) and propia[i]["hash"] == bloque["hash"]:
                nueva.append(propia[i])
            else:
                nueva.append(bloque if compartir else copy.deepcopy(bloque))
        return nueva

    def resumen(self) -> dict:
        """Datos básicos del nodo para la interfaz."""
        return {
            "id": self.id,
            "altura": self.altura,
            "ultimo_hash": self.ultimo_hash,
            "conectado": self.conectado,
            "deshonesto": self.deshonesto,
            "trampa": self.trampa,
        }


class Red:
    """Conjunto de nodos más las transacciones y castigos pendientes."""

    def __init__(self, genesis: dict, ids_y_claves: list[tuple[str, str]]):
        if not ids_y_claves:
            raise ValueError("la red necesita al menos un nodo")
        self.genesis = copy.deepcopy(genesis)
        ordenados = sorted(ids_y_claves, key=lambda par: par[0])
        self.nodos: dict[str, Nodo] = {
            id: Nodo(id, indice, clave, self.genesis) for indice, (id, clave) in enumerate(ordenados)
        }
        self.pendientes: list[dict] = []           # transacciones firmadas en espera
        self.castigos_pendientes: list[dict] = []  # castigos PoS que el siguiente bloque registra

    # ------------------------------------------------------------ nodos

    def ids(self) -> list[str]:
        """Ids de los nodos en orden (N01, N02, ...)."""
        return list(self.nodos)

    def nodo(self, id) -> Nodo:
        """El nodo con ese id; NoEncontrado si no existe."""
        if isinstance(id, str) and id in self.nodos:
            return self.nodos[id]
        ids = self.ids()
        texto = texto_visible(id, 30) if isinstance(id, str) else type(id).__name__
        raise NoEncontrado(f"El nodo '{texto}' no existe (nodos válidos: {ids[0]}–{ids[-1]})",
                           codigo="nodo_inexistente")

    def conectados(self) -> list[Nodo]:
        """Nodos conectados, en orden de id."""
        return [n for n in self.nodos.values() if n.conectado]

    def nodo_referencia(self) -> Nodo:
        """Nodo conectado con la cadena más alta (empate: menor id).

        Si no hay ninguno conectado, el de mayor altura de todos.
        """
        candidatos = self.conectados() or list(self.nodos.values())
        # max() devuelve el primero entre empatados y los nodos van en orden de id.
        return max(candidatos, key=lambda n: n.altura)

    def libro_referencia(self) -> Libro:
        """Libro de saldos del nodo de referencia."""
        return self.nodo_referencia().libro

    # ------------------------------------------------------------ cadenas

    def difundir(self, cadena, origen_id: str, timestamp_max: int | None = None) -> list[dict]:
        """Envía la cadena a todos los nodos (menos el origen); cada uno decide si la adopta.

        `timestamp_max` (opcional) es la hora de los nodos: rechazan bloques fechados después.
        """
        origen = self.nodos.get(origen_id) if isinstance(origen_id, str) else None
        # La cadena del propio origen ya es de la red (sus bloques no cambian): no hace falta copiarla.
        compartir = origen is not None and cadena is origen.cadena
        resultados = []
        for nodo in self.nodos.values():
            if nodo.id == origen_id:
                continue
            if not nodo.conectado:
                resultados.append({"nodo": nodo.id, "acepto": False, "motivo": "desconectado"})
                continue
            acepto, motivo = nodo.recibir_cadena(cadena, self.genesis, timestamp_max, compartir=compartir)
            resultados.append({"nodo": nodo.id, "acepto": acepto, "motivo": motivo})
        return resultados

    def sincronizar(self, id: str) -> dict:
        """El nodo pide la cadena a los nodos conectados y adopta la válida más larga."""
        nodo = self.nodo(id)
        altura_antes = nodo.altura
        adopto_de = None
        otros = [n for n in self.conectados() if n.id != nodo.id]
        otros.sort(key=lambda n: (-n.altura, n.id))   # la más larga primero
        for otro in otros:
            # Una cadena que no es más larga se rechazaría de todos modos (regla d): se omite.
            if len(otro.cadena) <= len(nodo.cadena):
                continue
            acepto, _ = nodo.recibir_cadena(otro.cadena, self.genesis, compartir=True)
            if acepto:
                adopto_de = otro.id
        return {"nodo": nodo.id, "altura_antes": altura_antes, "altura_despues": nodo.altura,
                "adopto_de": adopto_de}

    def sincronizados(self) -> bool:
        """True si todos los nodos (también los desconectados) tienen el mismo último hash."""
        return len({n.ultimo_hash for n in self.nodos.values()}) == 1

    # ------------------------------------------------------------ dinero comprometido

    def comprometido(self, apuestas: dict[str, int] | None = None) -> dict[str, int]:
        """Por nodo: lo que envía en pendientes + castigos pendientes + apuesta bloqueada."""
        total = {id: 0 for id in self.nodos}
        for tx in self.pendientes:
            if tx["emisor"] in total:
                total[tx["emisor"]] += tx["monto"]
        for castigo in self.castigos_pendientes:
            if castigo["nodo"] in total:
                total[castigo["nodo"]] += castigo["monto"]
        for id, apuesta in (apuestas or {}).items():
            if id in total and type(apuesta) is int and apuesta > 0:
                total[id] += apuesta
        return total

    def disponible(self, id: str, apuestas: dict[str, int] | None = None) -> int:
        """Saldo en cadena menos lo comprometido; nunca negativo."""
        nodo = self.nodo(id)
        saldo = self.libro_referencia().saldo(nodo.id)
        return max(0, saldo - self.comprometido(apuestas)[nodo.id])

    # ------------------------------------------------------------ pendientes

    def agregar_transaccion(self, tx, timestamp_max: int, apuestas: dict[str, int] | None = None) -> dict:
        """Valida una transacción firmada y la agrega al final de pendientes (devuelve una copia)."""
        validar_transaccion_nueva(tx, self.libro_referencia(), self.genesis["directorio"],
                                  self.pendientes, self.comprometido(apuestas), timestamp_max)
        guardada = dict(tx)   # sólo campos simples (texto y enteros) ya revisados
        self.pendientes.append(guardada)
        return dict(guardada)

    def quitar_pendientes(self, ids) -> None:
        """Quita de pendientes las transacciones con esos ids (ya incluidas en un bloque)."""
        quitar = set(ids)
        self.pendientes = [tx for tx in self.pendientes if tx["id"] not in quitar]

    def revalidar_pendientes(self, apuestas: dict[str, int] | None = None) -> list[dict]:
        """Vuelve a revisar las pendientes en orden y descarta las que ya no son válidas.

        Devuelve las descartadas (copias con un campo extra "motivo").
        """
        libro = self.libro_referencia().clonar()
        directorio = self.genesis["directorio"]
        # Lo comprometido que no son transacciones: castigos y apuestas.
        comprometido = {id: 0 for id in self.nodos}
        for castigo in self.castigos_pendientes:
            if castigo["nodo"] in comprometido:
                comprometido[castigo["nodo"]] += castigo["monto"]
        for id, apuesta in (apuestas or {}).items():
            if id in comprometido and type(apuesta) is int and apuesta > 0:
                comprometido[id] += apuesta

        vigentes: list[dict] = []
        descartadas: list[dict] = []
        for tx in self.pendientes:
            try:
                validar_transaccion_nueva(tx, libro, directorio, vigentes, comprometido, TIMESTAMP_SIN_LIMITE)
            except Exception as error:
                descartadas.append({**tx, "motivo": getattr(error, "mensaje", str(error))})
                continue
            vigentes.append(tx)
            comprometido[tx["emisor"]] += tx["monto"]
        self.pendientes = vigentes
        return descartadas
