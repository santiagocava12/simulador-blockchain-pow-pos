"""Libro de saldos: se obtiene "reproduciendo" la cadena bloque por bloque.

Los saldos nunca se guardan aparte de la cadena: se calculan aplicando, en
orden, las transacciones, castigos y recompensas de cada bloque a partir de
los saldos iniciales del génesis. Si un bloque rompe una regla, ``aplicar_bloque``
lanza ``CadenaInvalida`` con un mensaje legible ("Bloque 3: ...").

Recompensas: en PoW quedan pendientes hasta que la cadena llega a la altura
h + 6 (6 confirmaciones); en PoS se acreditan al aceptarse el bloque
(confirmaciones = 0).
"""

from nucleo.bloque import MAX_NUMERO, id_transaccion, serializar
from nucleo.cripto import verificar
from nucleo.reglas import calcular_castigo


class CadenaInvalida(Exception):
    """Un bloque rompe una regla del consenso. El mensaje explica cuál."""


class Libro:
    """Saldos, recompensas pendientes, dinero quemado y transacciones ya registradas."""

    def __init__(self, genesis: dict):
        self.saldos: dict[str, int] = dict(genesis["saldos_iniciales"])
        self.recompensas_pendientes: list[dict] = []   # [{"bloque", "beneficiario", "monto"}]
        self.acreditadas: list[dict] = []              # historial de recompensas ya maduras
        self.quemado: int = 0                          # castigos PoS (el dinero desaparece)
        self.ids_tx: dict[str, int] = {}               # id de transacción -> número de bloque
        self.altura: int = 0
        self.directorio: dict[str, str] = dict(genesis["directorio"])
        self.parametros: dict = dict(genesis["parametros"])

    # ------------------------------------------------------------ consultas

    @property
    def confirmaciones(self) -> int:
        """Bloques que debe esperar una recompensa (6 en PoW, 0 en PoS)."""
        return self.parametros["confirmaciones"]

    def clonar(self) -> "Libro":
        """Copia independiente: modificar el clon nunca cambia el original."""
        copia = Libro.__new__(Libro)
        copia.saldos = dict(self.saldos)
        copia.recompensas_pendientes = [dict(r) for r in self.recompensas_pendientes]
        copia.acreditadas = [dict(r) for r in self.acreditadas]
        copia.quemado = self.quemado
        copia.ids_tx = dict(self.ids_tx)
        copia.altura = self.altura
        copia.directorio = dict(self.directorio)
        copia.parametros = dict(self.parametros)
        return copia

    def saldo(self, id: str) -> int:
        """Saldo disponible en cadena (sin contar recompensas que aún no maduran)."""
        if not isinstance(id, str):
            return 0
        return self.saldos.get(id, 0)

    def pendientes_de(self, id: str) -> list[dict]:
        """Recompensas no maduras del nodo: [{"bloque", "monto", "faltan"}]."""
        return [
            {"bloque": r["bloque"], "monto": r["monto"],
             "faltan": r["bloque"] + self.confirmaciones - self.altura}
            for r in self.recompensas_pendientes
            if r["beneficiario"] == id
        ]

    def total_pendiente(self, id: str) -> int:
        """Suma de las recompensas no maduras del nodo."""
        return sum(r["monto"] for r in self.recompensas_pendientes if r["beneficiario"] == id)

    def total_en_circulacion(self) -> int:
        """Todo el dinero que existe: saldos + recompensas pendientes."""
        return sum(self.saldos.values()) + sum(r["monto"] for r in self.recompensas_pendientes)

    # ------------------------------------------------------------ cambios

    def madurar(self, altura: int) -> list[dict]:
        """Acredita las recompensas con bloque + confirmaciones <= altura y las devuelve."""
        maduras = [r for r in self.recompensas_pendientes
                   if r["bloque"] + self.confirmaciones <= altura]
        if not maduras:
            return []
        self.recompensas_pendientes = [r for r in self.recompensas_pendientes
                                       if r["bloque"] + self.confirmaciones > altura]
        for r in maduras:
            self.saldos[r["beneficiario"]] = self.saldos.get(r["beneficiario"], 0) + r["monto"]
            self.acreditadas.append(dict(r))
        return [dict(r) for r in maduras]

    def aplicar_bloque(self, bloque: dict) -> list[dict]:
        """Aplica un bloque (cuya estructura ya se revisó) y devuelve las recompensas acreditadas.

        Lanza CadenaInvalida si rompe una regla. Si falla, el libro puede quedar a
        medias: quien llama debe trabajar sobre un clon.
        """
        numero = bloque.get("numero") if isinstance(bloque, dict) else None
        # Nunca se imprime un valor raro (ni un int de miles de cifras, que no se puede convertir a texto).
        numero = numero if type(numero) is int and 0 <= numero <= MAX_NUMERO else "?"
        try:
            return self._aplicar(bloque)
        except CadenaInvalida:
            raise
        except Exception:
            raise CadenaInvalida(f"Bloque {numero}: estructura inválida") from None

    def _aplicar(self, bloque: dict) -> list[dict]:
        n = bloque["numero"]
        if n != self.altura + 1:
            raise CadenaInvalida(f"Bloque {n}: no sigue al último bloque aplicado ({self.altura})")
        es_pos = self.parametros["modo"] == "pos"

        # 1) Recompensas que ya debían estar maduras antes de este bloque.
        acreditadas = self.madurar(n - 1)

        # 2) Castigos PoS: el dinero castigado se quema.
        if bloque["castigos"] and not es_pos:
            raise CadenaInvalida(f"Bloque {n}: un bloque PoW no puede llevar castigos")
        for castigo in bloque["castigos"]:
            self._aplicar_castigo(n, castigo)

        # 3) PoS: cada validador debe poder cubrir su apuesta.
        if es_pos:
            for v in bloque["validadores"]:
                if v["id"] not in self.directorio:
                    raise CadenaInvalida(f"Bloque {n}: el validador {v['id']} no existe en el directorio")
                if v["apuesta"] > self.saldo(v["id"]):
                    raise CadenaInvalida(f"Bloque {n}: la apuesta de {v['id']} ({v['apuesta']}) "
                                         f"supera su saldo ({self.saldo(v['id'])})")

        # 4) Transacciones en orden (una gastada antes en el mismo bloque ya cuenta).
        if len(bloque["firma"]) != len(bloque["transacciones"]):
            raise CadenaInvalida(f"Bloque {n}: debe haber una firma por transacción")
        for i, (tx, firma) in enumerate(zip(bloque["transacciones"], bloque["firma"])):
            self._aplicar_transaccion(n, i, tx, firma, bloque["timestamp"])

        # 5) PoS: la apuesta estaba bloqueada; no se pudo gastar.
        if es_pos:
            for v in bloque["validadores"]:
                if self.saldo(v["id"]) < v["apuesta"]:
                    raise CadenaInvalida(
                        f"Bloque {n}: {v['id']} gastó dinero de su apuesta bloqueada "
                        f"(apostó {v['apuesta']} y tras las transacciones le quedan {self.saldo(v['id'])})")

        # 6) Recompensa del proponente.
        self._registrar_recompensa(n, bloque)

        # 7) Cierra el bloque y acredita lo que madura a esta altura.
        self.altura = n
        return acreditadas + self.madurar(n)

    def _aplicar_castigo(self, n: int, castigo: dict) -> None:
        nodo = castigo["nodo"]
        monto = castigo["monto"]
        if nodo not in self.directorio:
            raise CadenaInvalida(f"Bloque {n}: castigo a un nodo inexistente ({nodo})")
        if castigo["numero"] > n:
            raise CadenaInvalida(f"Bloque {n}: castigo de un bloque futuro ({castigo['numero']})")
        if monto > castigo["apuesta"]:
            raise CadenaInvalida(f"Bloque {n}: el castigo de {nodo} ({monto}) supera su apuesta "
                                 f"({castigo['apuesta']})")
        regla = self.parametros["regla_castigo"]
        if castigo["regla"] != regla:
            raise CadenaInvalida(f"Bloque {n}: el castigo de {nodo} usa la regla {castigo['regla']} "
                                 f"pero la red usa la regla {regla}")
        esperado = calcular_castigo(regla, castigo["apuesta"], castigo["valor_transacciones"],
                                    self.parametros["alfa_porcentaje"])
        if monto != esperado:
            raise CadenaInvalida(f"Bloque {n}: el castigo de {nodo} es {monto} pero según la regla "
                                 f"{regla} debe ser {esperado}")
        if self.saldo(nodo) < monto:
            raise CadenaInvalida(f"Bloque {n}: el castigo de {nodo} ({monto}) supera su saldo "
                                 f"({self.saldo(nodo)})")
        self.saldos[nodo] -= monto
        self.quemado += monto

    def _aplicar_transaccion(self, n: int, i: int, tx: dict, firma: str, timestamp_bloque: int) -> None:
        emisor, receptor, monto = tx["emisor"], tx["receptor"], tx["monto"]
        que = f"Bloque {n}: transacción {i + 1} ({emisor} → {receptor}, {monto})"
        if emisor not in self.directorio:
            raise CadenaInvalida(f"{que}: el emisor {emisor} no existe")
        if receptor not in self.directorio:
            raise CadenaInvalida(f"{que}: el receptor {receptor} no existe")
        if emisor == receptor:
            raise CadenaInvalida(f"{que}: el emisor y el receptor son el mismo nodo")
        if tx["timestamp"] > timestamp_bloque:
            raise CadenaInvalida(f"{que}: su timestamp es posterior al del bloque")
        if not verificar(self.directorio[emisor], serializar(tx), firma):
            raise CadenaInvalida(f"{que}: firma inválida (la transacción fue alterada o no la firmó {emisor})")
        id_tx = id_transaccion(tx)
        if id_tx in self.ids_tx:
            raise CadenaInvalida(f"{que}: transacción repetida: ya está en el bloque {self.ids_tx[id_tx]}")
        saldo = self.saldo(emisor)
        if saldo < monto:
            mensaje = (f"{que}: doble gasto / saldo insuficiente en el bloque: "
                       f"{emisor} tiene {saldo} e intenta enviar {monto}")
            pendiente = self.total_pendiente(emisor)
            if pendiente:
                mensaje += f" ({pendiente} en recompensas aún no maduras, que no se pueden gastar)"
            raise CadenaInvalida(mensaje)
        self.saldos[emisor] = saldo - monto
        self.saldos[receptor] = self.saldo(receptor) + monto
        self.ids_tx[id_tx] = n

    def _registrar_recompensa(self, n: int, bloque: dict) -> None:
        recompensa = bloque["recompensa"]
        beneficiario, monto = recompensa["beneficiario"], recompensa["monto"]
        if beneficiario != bloque["proponente"]:
            raise CadenaInvalida(f"Bloque {n}: la recompensa es para {beneficiario} pero el proponente "
                                 f"es {bloque['proponente']}")
        if beneficiario not in self.directorio:
            raise CadenaInvalida(f"Bloque {n}: el proponente {beneficiario} no existe en el directorio")
        establecida = self.parametros["recompensa"]
        if monto != establecida:
            raise CadenaInvalida(f"Bloque {n}: recompensa falsa: {monto} distinta a la establecida {establecida}")
        self.recompensas_pendientes.append({"bloque": n, "beneficiario": beneficiario, "monto": monto})

