"""Pruebas unitarias de nucleo/pos.py: la ronda PoS como máquina de estados.

Se ejecutan sin conftest (``pytest --noconftest``): sólo usan el núcleo. Los
votos se firman aquí con las claves de cada nodo, como lo hará el simulador.
"""

import copy
import json

import pytest

from nucleo.bloque import (CAMPOS_CASTIGO, crear_genesis, crear_transaccion, hash_bloque, hash_sin_votos,
                           nuevo_bloque, validar_estructura_bloque)
from nucleo.config import Config
from nucleo.cripto import firmar, generar_claves
from nucleo.errores import Conflicto, EntradaInvalida, NoEncontrado
from nucleo.libro import Libro
from nucleo.pos import (
    ESTADOS,
    FINALES,
    RondaPos,
    abrir_votacion,
    aceptar_bloque,
    avanzar_tras_rechazo,
    bloque_con_votos,
    cancelar_ronda,
    castigar_proponente,
    cerrar_sin_validadores,
    contar_votos,
    ejecutar_sorteo,
    fijar_apuestas,
    fijar_candidato,
    registrar_voto,
)
from nucleo.reglas import calcular_castigo, mensaje_voto, sortear, verificar_votos_bloque
from nucleo.reloj import INICIO_SIMULADO_MS
from nucleo.validacion import validar_bloque, validar_candidato

SEMILLA = "prueba-pos"
IDS = [f"N{i:02d}" for i in range(1, 11)]
CLAVES = {i: generar_claves(SEMILLA, i) for i in IDS}
DIRECTORIO = {i: CLAVES[i][1] for i in IDS}
SALDO = 100
RECOMPENSA = 50


def genesis_de(regla: str = "A", alfa: int = 50) -> dict:
    config = Config(modo="pos", saldo_inicial=SALDO, recompensa=RECOMPENSA, max_tx_por_bloque=5,
                    regla_castigo=regla, alfa_porcentaje=alfa)
    return crear_genesis(config, DIRECTORIO, INICIO_SIMULADO_MS)


GENESIS = genesis_de()
TX = crear_transaccion("N01", "N02", 5, INICIO_SIMULADO_MS + 1000, CLAVES["N01"][0])
TX_GRANDE = crear_transaccion("N03", "N04", 7, INICIO_SIMULADO_MS + 1001, CLAVES["N03"][0])

CLAVES_A_DICT = {"estado", "numero", "intento", "validadores", "excluidos", "A", "V_favor", "V_contra",
                 "umbral_alcanzado", "proponente", "candidato", "hash_candidato", "votos", "castigos_ronda",
                 "historial", "automatico", "rondas_restantes", "auto_tx", "resultado", "mensaje"}
CLAVES_VALIDADOR = {"id", "apuesta", "probabilidad", "voto", "excluido"}


# ---------------------------------------------------------------- ayudantes

def ronda_nueva(apuestas: dict | None = None, genesis: dict = GENESIS, **cambios) -> RondaPos:
    return RondaPos(1, genesis["hash"], apuestas or {i: 10 for i in IDS}, **cambios)


def disponibles(valor: int = SALDO) -> dict[str, int]:
    return {i: valor for i in IDS}


def candidato_de(ronda: RondaPos, txs=None, castigos=None) -> dict:
    """Bloque que arma el proponente sorteado (sin votos)."""
    return nuevo_bloque(ronda.numero, INICIO_SIMULADO_MS + 2000, txs or [TX], ronda.hash_anterior,
                        ronda.proponente, RECOMPENSA, "pos", validadores=ronda.validadores,
                        intento=ronda.intento, castigos=castigos)


def firma_voto(ronda: RondaPos, id_nodo: str, voto: bool, firmante: str | None = None) -> str:
    return firmar(CLAVES[firmante or id_nodo][0], mensaje_voto(ronda.hash_candidato, id_nodo, voto))


def votar(ronda: RondaPos, id_nodo: str, voto: bool) -> dict:
    return registrar_voto(ronda, id_nodo, voto, firma_voto(ronda, id_nodo, voto), DIRECTORIO)


def hasta_votacion(ronda: RondaPos, txs=None, castigos=None) -> RondaPos:
    """Sorteo, candidato y apertura de la votación."""
    ejecutar_sorteo(ronda)
    fijar_candidato(ronda, candidato_de(ronda, txs, castigos))
    abrir_votacion(ronda)
    return ronda


def ronda_en(estado: str) -> RondaPos:
    """Una ronda (10 validadores de 10) llevada hasta `estado`."""
    ronda = ronda_nueva()
    if estado == "APUESTAS":
        return ronda
    if estado == "CANCELADA":
        cancelar_ronda(ronda)
        return ronda
    ejecutar_sorteo(ronda)
    if estado == "SORTEO":
        return ronda
    fijar_candidato(ronda, candidato_de(ronda))
    if estado == "CANDIDATO":
        return ronda
    abrir_votacion(ronda)
    if estado == "VOTACION":
        return ronda
    if estado == "ACEPTADO":
        for i in IDS:
            votar(ronda, i, True)
        aceptar_bloque(ronda, bloque_con_votos(ronda))
        return ronda
    castigar_proponente(ronda, "A", 50, "candidato inválido")
    if estado == "RECHAZADO":
        return ronda
    raise AssertionError(f"estado sin preparar: {estado}")


def ronda_sin_validadores() -> RondaPos:
    """Un solo validador, castigado: no queda nadie para otro intento."""
    ronda = hasta_votacion(ronda_nueva({"N04": 10}))
    castigar_proponente(ronda, "A", 50, "bloque tramposo")
    assert avanzar_tras_rechazo(ronda) == "SIN_VALIDADORES"
    return ronda


def foto(ronda: RondaPos) -> dict:
    return copy.deepcopy(ronda.__dict__)


# ---------------------------------------------------------------- creación

def test_constantes():
    assert ESTADOS == ("APUESTAS", "SORTEO", "CANDIDATO", "VOTACION", "ACEPTADO", "RECHAZADO",
                       "SIN_VALIDADORES", "CANCELADA")
    assert FINALES == ("ACEPTADO", "SIN_VALIDADORES", "CANCELADA")


def test_estado_inicial_validadores_ordenados_y_apuestas_bloqueadas():
    ronda = RondaPos(3, "cd" * 32, {"N07": 4, "N02": 9, "N10": 1}, rondas_restantes=2, auto_tx=True)
    assert (ronda.estado, ronda.numero, ronda.intento, ronda.proponente) == ("APUESTAS", 3, 0, None)
    assert ronda.validadores == [{"id": "N02", "apuesta": 9}, {"id": "N07", "apuesta": 4},
                                 {"id": "N10", "apuesta": 1}]
    assert ronda.bloqueadas() == {"N02": 9, "N07": 4, "N10": 1} and ronda.total_apostado() == 14
    assert ronda.rondas_restantes == 2 and ronda.auto_tx is True and ronda.automatico is False
    assert not ronda.es_final


def test_constructor_sin_validadores_o_datos_imposibles():
    with pytest.raises(Conflicto) as info:
        RondaPos(1, GENESIS["hash"], {})
    assert info.value.codigo == "sin_validadores"
    for apuestas in ({"N01": 0}, {"N01": -3}, {"N01": "5"}, {"N01": True}, {3: 5}):
        with pytest.raises(ValueError):
            RondaPos(1, GENESIS["hash"], apuestas)
    for numero in (0, -1, "1", True):
        with pytest.raises(ValueError):
            RondaPos(numero, GENESIS["hash"], {"N01": 5})
    with pytest.raises(ValueError):
        RondaPos(1, GENESIS["hash"], {"N01": 5}, rondas_restantes=0)


# ---------------------------------------------------------------- recorrido completo

def test_recorrido_completo_hasta_aceptado_con_bloque_valido():
    ronda = ronda_nueva(rondas_restantes=3, automatico=True)
    proponente = ejecutar_sorteo(ronda)
    assert ronda.estado == "SORTEO" and proponente == ronda.proponente
    candidato = candidato_de(ronda)
    fijar_candidato(ronda, candidato)
    assert ronda.estado == "CANDIDATO" and ronda.hash_candidato == hash_sin_votos(candidato)
    assert validar_candidato([GENESIS], Libro(GENESIS), ronda.candidato, GENESIS) == (True, "candidato válido")
    abrir_votacion(ronda)
    assert ronda.estado == "VOTACION" and ronda.votos == []
    for i in IDS:
        registro = votar(ronda, i, True)
        assert registro["peso"] == 10 and registro["voto"] is True
    assert contar_votos(ronda) == (100, 100, True)

    bloque = bloque_con_votos(ronda)
    assert ronda.candidato["votos"] == [], "El candidato guardado sigue sin votos"
    assert bloque["hash"] == hash_bloque(bloque) and len(bloque["votos"]) == len(IDS)
    assert verificar_votos_bloque(bloque, DIRECTORIO) is None
    assert validar_bloque(bloque, GENESIS, Libro(GENESIS), GENESIS) is None

    aceptar_bloque(ronda, bloque)
    assert ronda.estado == "ACEPTADO" and ronda.es_final
    assert ronda.bloqueadas() == {}, "Al aceptar, las apuestas se liberan"
    assert ronda.resultado == {"estado": "ACEPTADO", "numero": 1, "hash": bloque["hash"],
                               "proponente": proponente, "intento": 0, "V_favor": 100, "A": 100}
    assert [h["resultado"] for h in ronda.historial] == ["ACEPTADO"]
    assert "aceptado" in ronda.mensaje
    datos = ronda.a_dict()
    assert datos["automatico"] is True and datos["rondas_restantes"] == 3
    assert datos["candidato"]["hash"] == candidato["hash"] and datos["umbral_alcanzado"] is True


# ---------------------------------------------------------------- transiciones inválidas

def _acciones() -> dict:
    """Cada transición con argumentos válidos (para probarla en estados donde no procede)."""
    return {
        "fijar_apuestas": lambda r: fijar_apuestas(r, {"N01": 5}, disponibles()),
        "ejecutar_sorteo": ejecutar_sorteo,
        "fijar_candidato": lambda r: fijar_candidato(r, candidato_de(r)),
        "abrir_votacion": abrir_votacion,
        "registrar_voto": lambda r: registrar_voto(r, "N01", True, "0" * 128, DIRECTORIO),
        "bloque_con_votos": bloque_con_votos,
        "aceptar_bloque": lambda r: aceptar_bloque(r, {"hash": "x"}),
        "castigar_proponente": lambda r: castigar_proponente(r, "A", 50, "x"),
        "avanzar_tras_rechazo": avanzar_tras_rechazo,
        "cerrar_sin_validadores": cerrar_sin_validadores,
        "cancelar_ronda": cancelar_ronda,
    }


PERMITIDAS = {
    "APUESTAS": {"fijar_apuestas", "ejecutar_sorteo", "cancelar_ronda"},
    "SORTEO": {"fijar_candidato", "cancelar_ronda"},
    "CANDIDATO": {"abrir_votacion", "cancelar_ronda"},
    "VOTACION": {"registrar_voto", "bloque_con_votos", "aceptar_bloque", "castigar_proponente",
                 "cancelar_ronda"},
    "RECHAZADO": {"ejecutar_sorteo", "avanzar_tras_rechazo", "cerrar_sin_validadores", "cancelar_ronda"},
    "ACEPTADO": set(),
    "CANCELADA": set(),
}


@pytest.mark.parametrize("estado", list(PERMITIDAS))
def test_transiciones_invalidas_dan_conflicto_sin_cambiar_nada(estado):
    for nombre, accion in _acciones().items():
        if nombre in PERMITIDAS[estado]:
            continue
        ronda = ronda_en(estado)
        antes = foto(ronda)
        with pytest.raises(Conflicto) as info:
            accion(ronda)
        assert info.value.mensaje.strip(), nombre
        assert foto(ronda) == antes, f"{nombre} cambió la ronda en {estado}"


def test_transiciones_en_sin_validadores_dan_conflicto():
    for nombre, accion in _acciones().items():
        ronda = ronda_sin_validadores()
        antes = foto(ronda)
        with pytest.raises(Conflicto):
            accion(ronda)
        assert foto(ronda) == antes, nombre


def test_conflicto_de_estado_explica_el_estado():
    ronda = ronda_en("APUESTAS")
    with pytest.raises(Conflicto) as info:
        registrar_voto(ronda, "N01", True, "0" * 128, DIRECTORIO)
    assert info.value.codigo == "estado_invalido"
    assert "APUESTAS" in info.value.mensaje and "VOTACION" in info.value.mensaje


def test_fijar_candidato_que_no_corresponde():
    ronda = ronda_en("SORTEO")
    otro = next(i for i in IDS if i != ronda.proponente)
    antes = foto(ronda)
    for cambio in ({"proponente": otro}, {"numero": 2}, {"intento": 3}, {"votos": [{"x": 1}]},
                   {"validadores": ronda.validadores[:-1]}, {"hash_anterior": "ef" * 32}):
        with pytest.raises(ValueError):
            fijar_candidato(ronda, {**candidato_de(ronda), **cambio})
    with pytest.raises(ValueError):
        fijar_candidato(ronda, "no es un bloque")
    assert foto(ronda) == antes


# ---------------------------------------------------------------- apuestas

@pytest.mark.parametrize("apuesta", [0, -5, "0", "-5", -5.0])
def test_apuesta_cero_o_negativa(apuesta):
    ronda = ronda_nueva()
    with pytest.raises(EntradaInvalida) as info:
        fijar_apuestas(ronda, {"N02": apuesta}, disponibles())
    assert "mayor que cero" in info.value.mensaje
    assert ronda.apuestas["N02"] == 10


def test_apuesta_mayor_al_disponible():
    ronda = ronda_nueva()
    with pytest.raises(EntradaInvalida) as info:
        fijar_apuestas(ronda, {"N03": 150}, disponibles())
    assert info.value.mensaje == "La apuesta de N03 (150) supera su saldo disponible (100)"
    assert info.value.codigo == "saldo_insuficiente"
    fijar_apuestas(ronda, {"N03": 100}, disponibles())   # justo todo el disponible sí se puede
    assert ronda.apuestas["N03"] == 100
    with pytest.raises(EntradaInvalida):
        fijar_apuestas(ronda, {"N03": 1}, {**disponibles(), "N03": 0})


@pytest.mark.parametrize("apuesta", ["abc", "", None, 2.5, "2.5", True, [10], {"a": 1}, float("nan"),
                                     10**13, "9" * 50])
def test_apuesta_que_no_es_un_entero_valido(apuesta):
    ronda = ronda_nueva()
    with pytest.raises(EntradaInvalida) as info:
        fijar_apuestas(ronda, {"N02": apuesta}, disponibles())
    assert info.value.mensaje.strip() and len(info.value.mensaje) < 200
    assert ronda.apuestas["N02"] == 10


def test_apuesta_de_quien_no_es_validador_o_no_existe():
    ronda = ronda_nueva({"N01": 5, "N02": 5, "N03": 5})
    with pytest.raises(Conflicto) as info:
        fijar_apuestas(ronda, {"N07": 5}, disponibles())
    assert info.value.mensaje == "N07 no es validador en esta ronda"
    assert info.value.codigo == "no_validador"
    for clave in ("N99", 99, "nada", "", None, 2.5):
        with pytest.raises((NoEncontrado, EntradaInvalida)):
            fijar_apuestas(ronda, {clave: 5}, disponibles())
    with pytest.raises(NoEncontrado) as info:
        fijar_apuestas(ronda, {"N99": 5}, disponibles())
    assert info.value.codigo == "nodo_inexistente"
    assert ronda.apuestas == {"N01": 5, "N02": 5, "N03": 5}


@pytest.mark.parametrize("nuevas", [[1, 2], "texto", None, 5, {}])
def test_apuestas_que_no_son_un_objeto(nuevas):
    ronda = ronda_nueva()
    with pytest.raises(EntradaInvalida):
        fijar_apuestas(ronda, nuevas, disponibles())


def test_apuestas_todo_o_nada():
    ronda = ronda_nueva()
    antes = dict(ronda.apuestas)
    for nuevas in ({"N03": 40, "N04": 1000}, {"N03": 40, "N04": 0}, {"N03": 40, "N04": "x"},
                   {"N03": 40, "N99": 5}, {"N03": 40, "N3": 41}):
        with pytest.raises((EntradaInvalida, NoEncontrado, Conflicto)):
            fijar_apuestas(ronda, nuevas, disponibles())
        assert ronda.apuestas == antes, f"{nuevas} cambió alguna apuesta"


def test_apuestas_con_ids_normalizados():
    ronda = ronda_nueva()
    fijar_apuestas(ronda, {"n3": "40", 4: 20, "5": 30.0, " N06 ": 1}, disponibles())
    assert {i: ronda.apuestas[i] for i in ("N03", "N04", "N05", "N06")} == {
        "N03": 40, "N04": 20, "N05": 30, "N06": 1}
    assert ronda.total_apostado() == 6 * 10 + 40 + 20 + 30 + 1
    assert ronda.estado == "APUESTAS"
    with pytest.raises(EntradaInvalida) as info:
        fijar_apuestas(ronda, {"N3": 1, "n03": 2}, disponibles())
    assert "dos veces" in info.value.mensaje


# ---------------------------------------------------------------- sorteo

def test_sorteo_reproducible_e_igual_a_reglas_sortear():
    apuestas = {"N01": 10, "N02": 30, "N05": 60, "N09": 7}
    for numero in range(1, 30):
        hash_anterior = f"{numero:064x}"
        a = RondaPos(numero, hash_anterior, apuestas)
        b = RondaPos(numero, hash_anterior, dict(reversed(list(apuestas.items()))))
        esperado = sortear([{"id": i, "apuesta": apuestas[i]} for i in sorted(apuestas)], hash_anterior,
                           numero, 0)
        assert ejecutar_sorteo(a) == ejecutar_sorteo(b) == esperado == a.proponente
        assert a.estado == "SORTEO" and esperado in a.mensaje


def test_sorteo_usa_las_apuestas_fijadas():
    resultados = set()
    for monto in range(1, 60):
        ronda = ronda_nueva({"N01": 30, "N02": 30})
        fijar_apuestas(ronda, {"N02": monto}, disponibles())
        resultados.add(ejecutar_sorteo(ronda))
        assert ronda.proponente == sortear(ronda.validadores, GENESIS["hash"], 1, 0)
    assert resultados == {"N01", "N02"}


# ---------------------------------------------------------------- votos

def test_votos_peso_igual_a_la_apuesta_y_conteo():
    ronda = ronda_nueva({"N01": 3, "N02": 5, "N03": 7})
    hasta_votacion(ronda)
    votar(ronda, "N01", True)
    votar(ronda, "N02", False)
    assert [v["peso"] for v in ronda.votos] == [3, 5]
    assert contar_votos(ronda) == (3, 15, False)
    datos = ronda.a_dict()
    assert (datos["V_favor"], datos["V_contra"], datos["A"]) == (3, 5, 15)
    assert {v["id"]: v["voto"] for v in datos["validadores"]} == {"N01": True, "N02": False, "N03": None}


def test_voto_de_quien_no_es_validador():
    ronda = hasta_votacion(ronda_nueva({"N01": 5, "N02": 5, "N03": 5}))
    votar(ronda, "N01", True)
    with pytest.raises(Conflicto) as info:
        votar(ronda, "N08", True)
    assert info.value.codigo == "voto_invalido" and "N08 no es validador" in info.value.mensaje
    with pytest.raises(NoEncontrado):
        registrar_voto(ronda, "N99", True, "0" * 128, DIRECTORIO)
    assert contar_votos(ronda) == (5, 15, False) and len(ronda.votos) == 1


def test_voto_duplicado():
    ronda = hasta_votacion(ronda_nueva())
    votar(ronda, "N03", True)
    for id_nodo, voto in (("N03", True), ("N03", False), ("n3", True), (3, False)):
        with pytest.raises(Conflicto) as info:
            registrar_voto(ronda, id_nodo, voto, firma_voto(ronda, "N03", voto), DIRECTORIO)
        assert info.value.codigo == "voto_duplicado"
        assert info.value.mensaje == "N03 ya votó en esta ronda; no puede votar dos veces"
    assert len(ronda.votos) == 1 and contar_votos(ronda)[0] == 10


def test_voto_con_firma_invalida():
    ronda = hasta_votacion(ronda_nueva())
    buena = firma_voto(ronda, "N03", True)
    alterada = ("1" if buena[0] == "0" else "0") + buena[1:]
    for firma in (firma_voto(ronda, "N03", True, firmante="N04"),   # firmado con otra clave
                  alterada,                                         # firma alterada
                  firma_voto(ronda, "N03", False),                  # firma del voto contrario
                  "zz" * 64, "", None, 12345, ["a"]):
        with pytest.raises(EntradaInvalida) as info:
            registrar_voto(ronda, "N03", True, firma, DIRECTORIO)
        assert info.value.codigo == "firma_invalida"
    assert ronda.votos == []
    registrar_voto(ronda, "N03", True, buena, DIRECTORIO)
    assert len(ronda.votos) == 1


def test_voto_que_no_es_verdadero_o_falso():
    ronda = hasta_votacion(ronda_nueva())
    for voto in ("quizas", None, 2, [True], 1.5):
        with pytest.raises(EntradaInvalida):
            registrar_voto(ronda, "N03", voto, firma_voto(ronda, "N03", True), DIRECTORIO)
    registrar_voto(ronda, "N03", "si", firma_voto(ronda, "N03", True), DIRECTORIO)
    assert ronda.votos[0]["voto"] is True


def test_voto_de_un_castigado():
    ronda = hasta_votacion(ronda_nueva({"N01": 5, "N02": 5, "N03": 5}))
    castigado = ronda.proponente
    castigar_proponente(ronda, "A", 50, "bloque tramposo")
    avanzar_tras_rechazo(ronda)
    fijar_candidato(ronda, candidato_de(ronda))
    abrir_votacion(ronda)
    with pytest.raises(Conflicto) as info:
        votar(ronda, castigado, True)
    assert info.value.codigo == "voto_invalido" and "castigado" in info.value.mensaje


# ---------------------------------------------------------------- umbral de 2/3

def _ronda_con_umbral(apuesta_en_contra: int) -> tuple[RondaPos, str]:
    """Tres validadores: dos de 9 a favor (incluido el proponente) y uno en contra."""
    for en_contra in ("N03", "N02", "N01"):
        apuestas = {"N01": 9, "N02": 9, "N03": 9, en_contra: apuesta_en_contra}
        ronda = ronda_nueva(apuestas)
        if ejecutar_sorteo(ronda) != en_contra:
            fijar_candidato(ronda, candidato_de(ronda))
            abrir_votacion(ronda)
            for i in sorted(apuestas):
                votar(ronda, i, i != en_contra)
            return ronda, en_contra
    raise AssertionError("no se encontró un proponente que vote a favor")


def test_umbral_exacto_27_18_se_acepta():
    ronda, _ = _ronda_con_umbral(9)
    assert contar_votos(ronda) == (18, 27, True)
    assert ronda.a_dict()["umbral_alcanzado"] is True
    bloque = bloque_con_votos(ronda)
    assert validar_bloque(bloque, GENESIS, Libro(GENESIS), GENESIS) is None, "La red también acepta 3V = 2A"
    aceptar_bloque(ronda, bloque)
    assert ronda.estado == "ACEPTADO"


def test_umbral_28_18_se_rechaza():
    ronda, _ = _ronda_con_umbral(10)
    assert contar_votos(ronda) == (18, 28, False)
    assert ronda.a_dict()["umbral_alcanzado"] is False
    bloque = bloque_con_votos(ronda)
    assert "votos insuficientes" in validar_bloque(bloque, GENESIS, Libro(GENESIS), GENESIS)
    with pytest.raises(Conflicto) as info:
        aceptar_bloque(ronda, bloque)
    assert info.value.codigo == "sin_umbral" and ronda.estado == "VOTACION"
    castigar_proponente(ronda, "A", 50, "votos insuficientes")
    assert ronda.estado == "RECHAZADO"
    datos = ronda.a_dict()
    assert (datos["V_favor"], datos["V_contra"], datos["A"]) == (18, 10, 28), "Se muestra la cuenta del intento"


# ---------------------------------------------------------------- castigos

def test_castigo_regla_a_pierde_toda_la_apuesta():
    ronda = hasta_votacion(ronda_nueva())
    proponente = ronda.proponente
    for i in IDS:
        votar(ronda, i, False)
    castigo = castigar_proponente(ronda, "A", 50, "firma alterada en la transacción 1")
    assert castigo == {"nodo": proponente, "apuesta": 10, "monto": 10, "regla": "A", "intento": 0,
                       "numero": 1, "valor_transacciones": 5, "motivo": "firma alterada en la transacción 1"}
    assert set(castigo) == set(CAMPOS_CASTIGO)
    assert ronda.estado == "RECHAZADO"
    assert proponente not in ronda.apuestas and proponente not in ronda.bloqueadas()
    assert ronda.excluidos == [{"id": proponente, "apuesta": 10, "castigo": 10,
                                "motivo": "firma alterada en la transacción 1", "intento": 0}]
    assert ronda.castigos_ronda == [castigo]
    assert ronda.historial[-1]["resultado"] == "RECHAZADO" and ronda.historial[-1]["castigo"] == 10
    fila = next(v for v in ronda.a_dict()["validadores"] if v["id"] == proponente)
    assert (fila["excluido"], fila["probabilidad"], fila["voto"]) == (True, 0.0, False)
    assert proponente in ronda.mensaje and "regla A" in ronda.mensaje


@pytest.mark.parametrize("apuesta, alfa, txs, esperado", [
    (10, 50, [TX], 3),                 # techo(0.5 · 5) = 3
    (10, 100, [TX], 5),                # techo(1 · 5) = 5
    (10, 50, [TX, TX_GRANDE], 6),      # techo(0.5 · 12) = 6
    (2, 100, [TX, TX_GRANDE], 2),      # nunca más que la apuesta
    (10, 1, [TX], 1),                  # techo(0.05) = 1 (mínimo 1)
])
def test_castigo_regla_b(apuesta, alfa, txs, esperado):
    ronda = hasta_votacion(ronda_nueva({i: apuesta for i in IDS}), txs=txs)
    castigo = castigar_proponente(ronda, "B", alfa, "recompensa falsa")
    valor = sum(t["monto"] for t in txs)
    assert castigo["monto"] == esperado == calcular_castigo("B", apuesta, valor, alfa)
    assert (castigo["regla"], castigo["valor_transacciones"], castigo["apuesta"]) == ("B", valor, apuesta)
    assert ronda.excluidos[0]["castigo"] == esperado


def test_castigo_cabe_en_un_bloque_y_motivo_seguro():
    ronda = hasta_votacion(ronda_nueva())
    motivo = "\x00control\ud800" + "x" * 400
    castigo = castigar_proponente(ronda, "A", 50, motivo)
    assert len(castigo["motivo"]) <= 200 and "\x00" not in castigo["motivo"] and "\ud800" not in castigo["motivo"]
    json.dumps(castigo, ensure_ascii=False).encode("utf-8")
    bloque = nuevo_bloque(2, 1, [TX], "ab" * 32, "N01", RECOMPENSA, "pos", castigos=[castigo])
    assert validar_estructura_bloque(bloque) is None


def test_castigo_con_regla_desconocida_no_cambia_nada():
    ronda = hasta_votacion(ronda_nueva())
    antes = foto(ronda)
    with pytest.raises(ValueError):
        castigar_proponente(ronda, "C", 50, "x")
    assert foto(ronda) == antes


# ---------------------------------------------------------------- nuevo intento

def test_nuevo_intento_sin_el_castigado_y_con_a_recalculado():
    genesis = genesis_de("A")
    apuestas = {i: 5 + n for n, i in enumerate(IDS)}
    ronda = hasta_votacion(ronda_nueva(apuestas, genesis))
    castigado = ronda.proponente
    for i in IDS:
        votar(ronda, i, False)
    castigo = castigar_proponente(ronda, "A", 50, "bloque tramposo")

    assert avanzar_tras_rechazo(ronda) == "SORTEO"
    restantes = [{"id": i, "apuesta": apuestas[i]} for i in IDS if i != castigado]
    assert ronda.intento == 1 and ronda.validadores == restantes
    assert ronda.total_apostado() == sum(apuestas.values()) - apuestas[castigado]
    assert ronda.proponente == sortear(restantes, genesis["hash"], 1, 1) != castigado
    assert (ronda.candidato, ronda.hash_candidato, ronda.votos) == (None, None, [])
    datos = ronda.a_dict()
    assert datos["A"] == ronda.total_apostado() and datos["V_favor"] == 0
    assert sum(v["probabilidad"] for v in datos["validadores"]) == pytest.approx(1.0)
    assert [v["id"] for v in datos["validadores"] if v["excluido"]] == [castigado]

    # El bloque del segundo intento lleva el castigo y la red lo acepta.
    fijar_candidato(ronda, candidato_de(ronda, castigos=[castigo]))
    abrir_votacion(ronda)
    for v in ronda.validadores:
        votar(ronda, v["id"], True)
    bloque = bloque_con_votos(ronda)
    libro = Libro(genesis)
    assert validar_bloque(bloque, genesis, libro, genesis) is None
    assert libro.quemado == apuestas[castigado]
    aceptar_bloque(ronda, bloque)
    assert [h["resultado"] for h in ronda.historial] == ["RECHAZADO", "ACEPTADO"]
    assert ronda.resultado["intento"] == 1


def test_ejecutar_sorteo_desde_rechazado_equivale_a_avanzar():
    ronda = ronda_en("RECHAZADO")
    excluido = ronda.excluidos[0]["id"]
    proponente = ejecutar_sorteo(ronda)
    assert ronda.estado == "SORTEO" and ronda.intento == 1 and proponente != excluido
    with pytest.raises(Conflicto) as info:
        cerrar_sin_validadores(ronda_en("RECHAZADO"))
    assert info.value.codigo == "quedan_validadores"


# ---------------------------------------------------------------- sin validadores

def test_sin_validadores_tras_castigar_al_unico():
    ronda = hasta_votacion(ronda_nueva({"N04": 10}))
    castigar_proponente(ronda, "A", 50, "bloque tramposo")
    antes = foto(ronda)
    with pytest.raises(Conflicto) as info:
        ejecutar_sorteo(ronda)
    assert info.value.codigo == "sin_validadores" and foto(ronda) == antes
    assert avanzar_tras_rechazo(ronda) == "SIN_VALIDADORES"
    assert ronda.es_final and ronda.bloqueadas() == {}
    assert ronda.resultado == {"estado": "SIN_VALIDADORES", "numero": 1, "intentos": 1}
    assert "No quedan validadores" in ronda.mensaje
    datos = ronda.a_dict()
    assert datos["A"] == 0 and datos["validadores"][0]["excluido"] is True
    json.dumps(datos)


def test_todos_castigados_uno_por_uno_hasta_sin_validadores():
    ronda = ronda_nueva({"N02": 4, "N05": 6, "N08": 8})
    castigados = []
    while True:
        if ronda.estado == "APUESTAS":
            ejecutar_sorteo(ronda)
        fijar_candidato(ronda, candidato_de(ronda))
        abrir_votacion(ronda)
        castigados.append(castigar_proponente(ronda, "A", 50, "tramposo")["nodo"])
        if avanzar_tras_rechazo(ronda) == "SIN_VALIDADORES":
            break
    assert sorted(castigados) == ["N02", "N05", "N08"] and ronda.intento == 2
    assert [c["monto"] for c in ronda.castigos_ronda] == [{"N02": 4, "N05": 6, "N08": 8}[i] for i in castigados]
    assert [h["intento"] for h in ronda.historial] == [0, 1, 2]


# ---------------------------------------------------------------- cancelar

@pytest.mark.parametrize("estado", ["APUESTAS", "SORTEO", "CANDIDATO", "VOTACION", "RECHAZADO"])
def test_cancelar_libera_apuestas_y_conserva_castigos(estado):
    ronda = ronda_en(estado)
    castigos = copy.deepcopy(ronda.castigos_ronda)
    cancelar_ronda(ronda)
    assert ronda.estado == "CANCELADA" and ronda.bloqueadas() == {}
    assert ronda.castigos_ronda == castigos and ronda.resultado["estado"] == "CANCELADA"
    with pytest.raises(Conflicto):
        cancelar_ronda(ronda)


def test_cancelar_con_mensaje_propio():
    ronda = ronda_nueva()
    cancelar_ronda(ronda, "La cadena cambió: la ronda se cancela")
    assert ronda.mensaje == "La cadena cambió: la ronda se cancela"


# ---------------------------------------------------------------- a_dict

@pytest.mark.parametrize("estado", ["APUESTAS", "SORTEO", "CANDIDATO", "VOTACION", "ACEPTADO", "RECHAZADO",
                                    "CANCELADA", "SIN_VALIDADORES"])
def test_a_dict_serializable_con_las_claves_del_contrato(estado):
    ronda = ronda_sin_validadores() if estado == "SIN_VALIDADORES" else ronda_en(estado)
    datos = ronda.a_dict()
    assert set(datos) == CLAVES_A_DICT
    assert json.loads(json.dumps(datos)) == datos
    assert datos["estado"] == estado and datos["mensaje"].strip()
    assert all(set(v) == CLAVES_VALIDADOR for v in datos["validadores"])
    assert [v["id"] for v in datos["validadores"]] == sorted(v["id"] for v in datos["validadores"])
    if estado in ("CANDIDATO", "VOTACION", "ACEPTADO", "RECHAZADO"):
        assert datos["candidato"]["numero"] == 1 and datos["hash_candidato"]
    if estado == "APUESTAS":
        assert datos["candidato"] is None and datos["proponente"] is None


def test_a_dict_es_una_copia():
    ronda = ronda_en("ACEPTADO")
    datos = ronda.a_dict()
    datos["votos"][0]["peso"] = 999
    datos["historial"][0]["A"] = -1
    datos["resultado"]["hash"] = "alterado"
    assert ronda.votos[0]["peso"] == 10 and ronda.historial[0]["A"] == 100
    assert ronda.resultado["hash"] != "alterado"
