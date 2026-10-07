"""Proof of Stake: una ronda como máquina de estados.

    APUESTAS → SORTEO → CANDIDATO → VOTACION → ACEPTADO
                  ↑                         ↘ RECHAZADO → SORTEO (nuevo intento sin el castigado)
                  └─────────────────────────────────────┘     ↘ SIN_VALIDADORES (nadie queda)

ACEPTADO, SIN_VALIDADORES y CANCELADA son finales. Cada función revisa que la
ronda esté en el estado correcto (si no, ``Conflicto``) y valida todo ANTES de
cambiar algo. Las firmas de los votos, la validación del candidato, la difusión
y los castigos pendientes de la red los coordina ``simulador.py``.
"""

import copy

from nucleo.bloque import hash_bloque, hash_sin_votos, resumen_bloque
from nucleo.cripto import verificar
from nucleo.entradas import MAX_ENTERO, leer_bool, leer_entero, leer_id_nodo, texto_visible
from nucleo.errores import Conflicto, EntradaInvalida
from nucleo.reglas import alcanza_umbral, calcular_castigo, mensaje_voto, probabilidades, sortear

ESTADOS = ("APUESTAS", "SORTEO", "CANDIDATO", "VOTACION", "ACEPTADO", "RECHAZADO", "SIN_VALIDADORES",
           "CANCELADA")
FINALES = ("ACEPTADO", "SIN_VALIDADORES", "CANCELADA")
MAX_MOTIVO = 200   # largo máximo del motivo de un castigo (así cabe en el bloque)


class RondaPos:
    """Ronda PoS para el bloque `numero`. Las apuestas quedan bloqueadas mientras dure."""

    def __init__(self, numero: int, hash_anterior: str, apuestas: dict[str, int], rondas_restantes: int = 1,
                 auto_tx: bool = False, automatico: bool = False):
        for nombre, valor in (("numero", numero), ("rondas_restantes", rondas_restantes)):
            if type(valor) is not int or valor < 1:
                raise ValueError(f"{nombre} debe ser un entero mayor o igual a 1")
        if not isinstance(apuestas, dict) or not apuestas:
            raise Conflicto("Ningún validador con saldo: no se puede formar la ronda", "sin_validadores")
        for id_nodo, apuesta in apuestas.items():
            if not isinstance(id_nodo, str) or type(apuesta) is not int or apuesta < 1:
                raise ValueError("cada apuesta inicial debe ser id (texto) -> entero mayor que cero")

        self.numero = numero
        self.hash_anterior = hash_anterior
        self.estado = "APUESTAS"
        self.intento = 0
        self.apuestas: dict[str, int] = {i: apuestas[i] for i in sorted(apuestas)}   # validadores en juego
        self.excluidos: list[dict] = []        # castigados: {"id","apuesta","castigo","motivo","intento"}
        self.proponente: str | None = None
        self.candidato: dict | None = None     # bloque propuesto, aún sin votos
        self.hash_candidato: str | None = None
        self.votos: list[dict] = []            # {"validador","peso","voto","firma"} del intento actual
        self.castigos_ronda: list[dict] = []
        self.resultado: dict | None = None
        self.historial: list[dict] = []        # un resumen por intento terminado
        self.rondas_restantes = rondas_restantes
        self.auto_tx = bool(auto_tx)
        self.automatico = bool(automatico)
        self.mensaje = (f"Ronda del bloque {numero}: {len(self.apuestas)} validadores. "
                        f"Ajuste las apuestas y avance para sortear al proponente")

    # ------------------------------------------------------------ consultas

    @property
    def validadores(self) -> list[dict]:
        """[{"id", "apuesta"}] de los validadores que siguen en juego, ordenados por id."""
        return [{"id": i, "apuesta": a} for i, a in self.apuestas.items()]

    @property
    def es_final(self) -> bool:
        return self.estado in FINALES

    def total_apostado(self) -> int:
        """A = suma de las apuestas de los validadores en juego."""
        return sum(self.apuestas.values())

    def bloqueadas(self) -> dict[str, int]:
        """Apuestas que siguen bloqueadas: las de los validadores en juego, {} si la ronda terminó.

        Las de los castigados ya no cuentan: su castigo pendiente las sustituye.
        """
        return {} if self.es_final else dict(self.apuestas)

    def a_dict(self) -> dict:
        """Resumen JSON de la ronda para la interfaz."""
        if self.estado == "RECHAZADO" and self.historial:
            ultimo = self.historial[-1]   # cuenta del intento rechazado (el proponente ya salió)
            v_favor, v_contra, total = ultimo["V_favor"], ultimo["V_contra"], ultimo["A"]
        else:
            v_favor, v_contra = _sumar_votos(self.votos)
            total = self.total_apostado()
        return {
            "estado": self.estado,
            "numero": self.numero,
            "intento": self.intento,
            "validadores": self._validadores_para_mostrar(),
            "excluidos": copy.deepcopy(self.excluidos),
            "A": total,
            "V_favor": v_favor,
            "V_contra": v_contra,
            "umbral_alcanzado": alcanza_umbral(v_favor, total),
            "proponente": self.proponente,
            "candidato": resumen_bloque(self.candidato) if self.candidato else None,
            "hash_candidato": self.hash_candidato,
            "votos": copy.deepcopy(self.votos),
            "castigos_ronda": copy.deepcopy(self.castigos_ronda),
            "historial": copy.deepcopy(self.historial),
            "automatico": self.automatico,
            "rondas_restantes": self.rondas_restantes,
            "auto_tx": self.auto_tx,
            "resultado": copy.deepcopy(self.resultado),
            "mensaje": self.mensaje,
        }

    def _validadores_para_mostrar(self) -> list[dict]:
        """En juego y excluidos, por id, con su probabilidad y su voto del intento actual."""
        prob = probabilidades(self.validadores)
        votos = {v["validador"]: v["voto"] for v in self.votos}
        filas = [{"id": i, "apuesta": a, "probabilidad": prob[i], "voto": votos.get(i), "excluido": False}
                 for i, a in self.apuestas.items()]
        filas += [{"id": e["id"], "apuesta": e["apuesta"], "probabilidad": 0.0, "voto": votos.get(e["id"]),
                   "excluido": True} for e in self.excluidos]
        return sorted(filas, key=lambda f: f["id"])


# ---------------------------------------------------------------- ayudas internas

def _exigir_estado(ronda: RondaPos, permitidos: tuple[str, ...], accion: str) -> None:
    """Conflicto si la ronda no está en uno de los estados permitidos."""
    if ronda.estado not in permitidos:
        raise Conflicto(f"No se puede {accion}: la ronda está en {ronda.estado} "
                        f"(se necesita {' o '.join(permitidos)})", "estado_invalido")


def _sumar_votos(votos: list[dict]) -> tuple[int, int]:
    """(peso a favor, peso en contra)."""
    favor = sum(v["peso"] for v in votos if v["voto"] is True)
    contra = sum(v["peso"] for v in votos if v["voto"] is not True)
    return favor, contra


def _leer_apuesta(valor, id_nodo: str) -> int:
    """Entero de 1 a MAX_ENTERO; si es cero o negativo lo dice con claridad."""
    nombre = f"La apuesta de {id_nodo}"
    try:
        return leer_entero(valor, nombre, 1, MAX_ENTERO)
    except EntradaInvalida as error:
        try:   # ¿era un entero cero o negativo? Entonces se explica así.
            n = leer_entero(valor, nombre, -MAX_ENTERO, 0)
        except EntradaInvalida:
            raise error from None
        raise EntradaInvalida(f"La apuesta debe ser mayor que cero ({id_nodo}: recibido {n})",
                              detalles={"nodo": id_nodo}) from None


def _cerrar_intento(ronda: RondaPos, resultado: str, motivo: str, castigo: int | None) -> None:
    """Guarda en el historial el resumen del intento que termina."""
    v_favor, v_contra = _sumar_votos(ronda.votos)
    ronda.historial.append({
        "intento": ronda.intento,
        "proponente": ronda.proponente,
        "hash_candidato": ronda.hash_candidato,
        "V_favor": v_favor,
        "V_contra": v_contra,
        "A": ronda.total_apostado(),
        "resultado": resultado,
        "motivo": motivo,
        "castigo": castigo,
    })


def _mensaje_votacion(ronda: RondaPos) -> str:
    """Cómo va la votación del intento actual."""
    v_favor, v_contra = _sumar_votos(ronda.votos)
    total = ronda.total_apostado()
    estado = "alcanza" if alcanza_umbral(v_favor, total) else "no alcanza"
    return (f"Votación del intento {ronda.intento}: {v_favor} a favor y {v_contra} en contra de A = {total}; "
            f"{estado} los 2/3 (3V ≥ 2A)")


# ---------------------------------------------------------------- transiciones

def fijar_apuestas(ronda: RondaPos, nuevas, disponibles: dict[str, int]) -> None:
    """Cambia apuestas (sólo en APUESTAS). Todo o nada: si una falla, no cambia ninguna.

    `nuevas`: {id: monto}; los ids aceptan "N3", "n03", "3" o 3. `disponibles`:
    saldo disponible de CADA nodo de la red sin contar su apuesta en esta ronda
    (sus claves son los ids válidos).
    """
    _exigir_estado(ronda, ("APUESTAS",), "cambiar las apuestas")
    if not isinstance(nuevas, dict):
        raise EntradaInvalida("Las apuestas deben ser un objeto {nodo: monto}, por ejemplo {\"N03\": 25}")
    if not nuevas:
        raise EntradaInvalida("Indique al menos una apuesta {nodo: monto}")
    ids_red = sorted(set(disponibles) | set(ronda.apuestas))

    leidas: dict[str, int] = {}
    for clave, valor in nuevas.items():
        id_nodo = leer_id_nodo(clave, "La apuesta", ids_red)   # NoEncontrado si el nodo no existe
        if id_nodo in leidas:
            raise EntradaInvalida(f"La apuesta de {id_nodo} aparece dos veces")
        if id_nodo not in ronda.apuestas:
            raise Conflicto(f"{id_nodo} no es validador en esta ronda", "no_validador")
        monto = _leer_apuesta(valor, id_nodo)
        disponible = max(0, disponibles.get(id_nodo, 0))
        if monto > disponible:
            raise EntradaInvalida(f"La apuesta de {id_nodo} ({monto}) supera su saldo disponible ({disponible})",
                                  "saldo_insuficiente", {"nodo": id_nodo})
        leidas[id_nodo] = monto

    ronda.apuestas.update(leidas)   # todo se validó: ahora sí se aplica
    cambios = ", ".join(f"{i} = {m}" for i, m in leidas.items())
    ronda.mensaje = f"Apuestas actualizadas ({cambios}); A = {ronda.total_apostado()}"


def ejecutar_sorteo(ronda: RondaPos) -> str:
    """APUESTAS → SORTEO, o RECHAZADO → SORTEO (nuevo intento sin los castigados).

    Usa el sorteo público de la guía; devuelve el id del proponente.
    """
    _exigir_estado(ronda, ("APUESTAS", "RECHAZADO"), "hacer el sorteo")
    if not ronda.apuestas:
        raise Conflicto("No quedan validadores para un nuevo sorteo", "sin_validadores")
    nuevo_intento = ronda.estado == "RECHAZADO"
    intento = ronda.intento + 1 if nuevo_intento else ronda.intento
    proponente = sortear(ronda.validadores, ronda.hash_anterior, ronda.numero, intento)

    if nuevo_intento:   # se olvida el candidato rechazado y sus votos
        ronda.candidato = None
        ronda.hash_candidato = None
        ronda.votos = []
    ronda.intento = intento
    ronda.proponente = proponente
    ronda.estado = "SORTEO"
    apuesta, total = ronda.apuestas[ronda.proponente], ronda.total_apostado()
    ronda.mensaje = (f"Sorteo del intento {ronda.intento}: salió {ronda.proponente} "
                     f"(apuesta {apuesta} de A = {total}, probabilidad {100 * apuesta / total:.1f} %)")
    return ronda.proponente


def fijar_candidato(ronda: RondaPos, bloque: dict) -> None:
    """SORTEO → CANDIDATO: el proponente sorteado presenta su bloque (todavía sin votos)."""
    _exigir_estado(ronda, ("SORTEO",), "presentar un candidato")
    esperado = {"proponente": ronda.proponente, "numero": ronda.numero, "intento": ronda.intento,
                "hash_anterior": ronda.hash_anterior, "validadores": ronda.validadores, "votos": []}
    for campo, valor in esperado.items():
        if not isinstance(bloque, dict) or bloque.get(campo) != valor:
            raise ValueError(f"el candidato no corresponde a la ronda (campo '{campo}')")
    hash_candidato = hash_sin_votos(bloque)   # lo que firmarán los votos
    ronda.candidato = copy.deepcopy(bloque)
    ronda.hash_candidato = hash_candidato
    ronda.estado = "CANDIDATO"
    ronda.mensaje = (f"{ronda.proponente} propuso el bloque {ronda.numero} con "
                     f"{len(bloque.get('transacciones') or [])} transacciones; los validadores lo revisarán")


def abrir_votacion(ronda: RondaPos) -> None:
    """CANDIDATO → VOTACION: desde ahora cada validador puede votar una vez."""
    _exigir_estado(ronda, ("CANDIDATO",), "abrir la votación")
    ronda.votos = []
    ronda.estado = "VOTACION"
    ronda.mensaje = _mensaje_votacion(ronda)


def registrar_voto(ronda: RondaPos, validador_id, voto, firma, directorio: dict) -> dict:
    """Registra un voto firmado (sólo en VOTACION). Peso = apuesta del validador. Devuelve el voto.

    Errores: nodo inexistente (NoEncontrado), voto que no es verdadero/falso o firma
    inválida (EntradaInvalida), no validador ("voto_invalido") o voto repetido
    ("voto_duplicado") como Conflicto.
    """
    _exigir_estado(ronda, ("VOTACION",), "votar")
    id_nodo = leer_id_nodo(validador_id, "El votante", sorted(directorio))
    valor = leer_bool(voto, "El voto")
    if id_nodo not in ronda.apuestas:
        if any(e["id"] == id_nodo for e in ronda.excluidos):
            mensaje = f"{id_nodo} fue castigado y excluido de esta ronda; su voto no cuenta"
        else:
            mensaje = f"{id_nodo} no es validador en esta ronda; su voto no cuenta"
        raise Conflicto(mensaje, "voto_invalido")
    if any(v["validador"] == id_nodo for v in ronda.votos):
        raise Conflicto(f"{id_nodo} ya votó en esta ronda; no puede votar dos veces", "voto_duplicado")
    if not verificar(directorio[id_nodo], mensaje_voto(ronda.hash_candidato, id_nodo, valor), firma):
        raise EntradaInvalida(f"Firma inválida: el voto no está firmado por {id_nodo} o fue alterado",
                              "firma_invalida")
    registro = {"validador": id_nodo, "peso": ronda.apuestas[id_nodo], "voto": valor, "firma": firma}
    ronda.votos.append(registro)
    ronda.mensaje = _mensaje_votacion(ronda)
    return dict(registro)


def contar_votos(ronda: RondaPos) -> tuple[int, int, bool]:
    """(V a favor, A total apostado, ¿3V ≥ 2A?)."""
    v_favor, _ = _sumar_votos(ronda.votos)
    total = ronda.total_apostado()
    return v_favor, total, alcanza_umbral(v_favor, total)


def bloque_con_votos(ronda: RondaPos) -> dict:
    """El candidato con los votos registrados y su hash final (lo que se difunde)."""
    _exigir_estado(ronda, ("VOTACION",), "armar el bloque final")
    bloque = copy.deepcopy(ronda.candidato)
    bloque["votos"] = copy.deepcopy(ronda.votos)
    bloque["hash"] = hash_bloque(bloque)
    return bloque


def aceptar_bloque(ronda: RondaPos, bloque: dict) -> None:
    """VOTACION → ACEPTADO: los votos alcanzaron 2/3 y la red agregó el bloque."""
    _exigir_estado(ronda, ("VOTACION",), "aceptar el bloque")
    v_favor, total, alcanza = contar_votos(ronda)
    if not alcanza:
        raise Conflicto(f"No se puede aceptar: {v_favor} a favor de {total} no alcanza los 2/3", "sin_umbral")
    hash_final = bloque.get("hash") if isinstance(bloque, dict) else None
    _cerrar_intento(ronda, "ACEPTADO", "bloque aceptado", None)
    ronda.estado = "ACEPTADO"
    ronda.resultado = {"estado": "ACEPTADO", "numero": ronda.numero, "hash": hash_final,
                       "proponente": ronda.proponente, "intento": ronda.intento,
                       "V_favor": v_favor, "A": total}
    ronda.mensaje = (f"Bloque {ronda.numero} aceptado: {v_favor} de {total} a favor (≥ 2/3). "
                     f"Proponente {ronda.proponente}; las apuestas se liberan")


def castigar_proponente(ronda: RondaPos, regla: str, alfa_porcentaje: int, motivo: str) -> dict:
    """VOTACION → RECHAZADO: el proponente pierde c y queda fuera de la ronda. Devuelve el castigo.

    c = calcular_castigo(regla, apuesta, valor de las transacciones del candidato, α).
    Los demás validadores siguen en juego para un nuevo intento.
    """
    _exigir_estado(ronda, ("VOTACION",), "castigar al proponente")
    proponente = ronda.proponente
    apuesta = ronda.apuestas[proponente]
    transacciones = (ronda.candidato or {}).get("transacciones") or []
    valor = sum(tx["monto"] for tx in transacciones)
    monto = calcular_castigo(regla, apuesta, valor, alfa_porcentaje)   # ValueError si la regla no existe
    motivo = texto_visible(str(motivo or "bloque rechazado"), MAX_MOTIVO)[:MAX_MOTIVO]

    castigo = {"nodo": proponente, "apuesta": apuesta, "monto": monto, "regla": regla,
               "intento": ronda.intento, "numero": ronda.numero, "valor_transacciones": valor,
               "motivo": motivo}
    _cerrar_intento(ronda, "RECHAZADO", motivo, monto)
    del ronda.apuestas[proponente]   # deja de ser validador: su castigo sustituye a la apuesta
    ronda.excluidos.append({"id": proponente, "apuesta": apuesta, "castigo": monto, "motivo": motivo,
                            "intento": ronda.intento})
    ronda.castigos_ronda.append(dict(castigo))
    ronda.estado = "RECHAZADO"
    ronda.mensaje = (f"Bloque rechazado ({motivo}). {proponente} pierde {monto} (regla {regla}) y queda "
                     f"fuera; quedan {len(ronda.apuestas)} validadores")
    return castigo


def cerrar_sin_validadores(ronda: RondaPos) -> None:
    """RECHAZADO → SIN_VALIDADORES: nadie queda para otro intento; la ronda termina sin bloque."""
    _exigir_estado(ronda, ("RECHAZADO",), "cerrar la ronda")
    if ronda.apuestas:
        raise Conflicto("Todavía quedan validadores para otro intento", "quedan_validadores")
    ronda.estado = "SIN_VALIDADORES"
    ronda.resultado = {"estado": "SIN_VALIDADORES", "numero": ronda.numero, "intentos": ronda.intento + 1}
    ronda.mensaje = ("No quedan validadores: la ronda termina sin bloque. "
                     "Las transacciones siguen pendientes")


def avanzar_tras_rechazo(ronda: RondaPos) -> str:
    """RECHAZADO → SORTEO si quedan validadores, si no → SIN_VALIDADORES. Devuelve el nuevo estado."""
    _exigir_estado(ronda, ("RECHAZADO",), "seguir tras el rechazo")
    if ronda.apuestas:
        ejecutar_sorteo(ronda)
    else:
        cerrar_sin_validadores(ronda)
    return ronda.estado


def cancelar_ronda(ronda: RondaPos, mensaje: str | None = None) -> None:
    """Cualquier estado no final → CANCELADA. Las apuestas se liberan; los castigos hechos se quedan."""
    if ronda.es_final:
        raise Conflicto(f"La ronda ya terminó (estado: {ronda.estado})", "sin_ronda")
    ronda.estado = "CANCELADA"
    ronda.resultado = {"estado": "CANCELADA", "numero": ronda.numero, "intento": ronda.intento}
    ronda.mensaje = mensaje or "Ronda cancelada; las apuestas se liberaron"
