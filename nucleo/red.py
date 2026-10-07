"""Nodos y red: cada nodo guarda su propia copia de la cadena y la valida.

La red reparte cadenas (difundir), ayuda a un nodo a ponerse al día
(sincronizar) y guarda las transacciones y castigos pendientes, que son
compartidos por todos como en una red real.
"""

import copy

from nucleo.entradas import texto_visible
from nucleo.errores import NoEncontrado
from nucleo.libro import Libro
from nucleo.validacion import evaluar_cadena_recibida, validar_transaccion_nueva

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

    def recibir_cadena(self, cadena, genesis: dict, timestamp_max: int | None = None) -> tuple[bool, str]:
        """Valida una cadena recibida y la adopta si es válida y más larga. Nunca lanza.

        `timestamp_max` (opcional) es la hora del nodo: rechaza bloques fechados en el futuro.
        """
        try:
            acepta, motivo, libro = evaluar_cadena_recibida(cadena, self.cadena, genesis, timestamp_max)
            if not acepta:
                return False, motivo
            nueva = copy.deepcopy(cadena)   # copia propia: nada se comparte con quien la envió
            nueva[0] = copy.deepcopy(genesis)   # el génesis guardado es siempre el de confianza
            self.cadena, self.libro = nueva, libro
            return True, motivo
        except Exception:
            return False, "la cadena tiene una estructura inválida"

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

    def difundir(self, cadena, origen_id: str) -> list[dict]:
        """Envía la cadena a todos los nodos (menos el origen); cada uno decide si la adopta."""
        resultados = []
        for nodo in self.nodos.values():
            if nodo.id == origen_id:
                continue
            if not nodo.conectado:
                resultados.append({"nodo": nodo.id, "acepto": False, "motivo": "desconectado"})
                continue
            acepto, motivo = nodo.recibir_cadena(cadena, self.genesis)
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
            acepto, _ = nodo.recibir_cadena(otro.cadena, self.genesis)
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
