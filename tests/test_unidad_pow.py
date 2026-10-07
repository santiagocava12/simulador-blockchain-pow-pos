"""Pruebas unitarias de nucleo/pow.py: minería por rondas, nonces disjuntos y desempate.

Se ejecutan sin conftest (``pytest --noconftest``): sólo usan el núcleo.
"""

import copy
import itertools
import json

import pytest

from nucleo.bloque import crear_genesis, crear_transaccion, hash_bloque, nuevo_bloque
from nucleo.config import Config
from nucleo.cripto import generar_claves
from nucleo.errores import Conflicto
from nucleo.libro import Libro
from nucleo.pow import (
    ACTIVOS,
    ESTADOS,
    FINALES,
    Minero,
    SesionPow,
    bloque_ganador,
    cancelar_sesion,
    elegir_ganador,
    ordenar_hallazgos,
    preparar_hash,
    reanudar_tras_rechazo,
    registrar_ganador,
    revisar_limite,
    ronda_pow,
    terminar_sesion,
)
from nucleo.reglas import cumple_dificultad
from nucleo.reloj import INICIO_SIMULADO_MS
from nucleo.validacion import validar_bloque

SEMILLA = "prueba-pow"
IDS = [f"N{i:02d}" for i in range(1, 11)]
N = len(IDS)
CLAVES = {i: generar_claves(SEMILLA, i) for i in IDS}
DIRECTORIO = {i: CLAVES[i][1] for i in IDS}
RECOMPENSA = 50
GENESIS = crear_genesis(
    Config(modo="pow", dificultad=1, saldo_inicial=100, recompensa=RECOMPENSA, max_tx_por_bloque=5),
    DIRECTORIO, INICIO_SIMULADO_MS,
)
TX = crear_transaccion("N01", "N02", 5, INICIO_SIMULADO_MS + 1000, CLAVES["N01"][0])

CLAVES_A_DICT = {"estado", "numero", "ronda", "max_rondas", "k", "dificultad", "esperado", "intentos_totales",
                 "mineros", "ultimo_empate", "bloques_restantes", "auto_tx", "mensaje"}
CLAVES_MINERO = {"id", "indice", "nonce", "intentos", "ultimo_hash", "encontro", "conectado"}


# ---------------------------------------------------------------- ayudantes

def candidatos(recompensas: dict[str, int] | None = None, ids=IDS) -> dict[str, dict]:
    """Un bloque propio por minero: proponente y recompensa = él mismo."""
    recompensas = recompensas or {}
    return {i: nuevo_bloque(1, INICIO_SIMULADO_MS + 2000, [TX], GENESIS["hash"], i,
                            recompensas.get(i, RECOMPENSA), "pow") for i in ids}


def sesion_nueva(k: int = 5, dificultad: int = 1, max_rondas: int = 100, ids=IDS, **cambios) -> SesionPow:
    return SesionPow(1, candidatos(ids=ids), ids, k, dificultad, max_rondas, **cambios)


def foto(sesion: SesionPow) -> dict[str, tuple[int, int]]:
    """(nonce, intentos) de cada minero."""
    return {m.id: (m.nonce, m.intentos) for m in sesion.mineros}


def nonces_probados(antes: tuple[int, int], minero: Minero, n: int) -> list[int]:
    """Nonces que el minero probó en la última ronda, deducidos de su avance."""
    nonce_antes, intentos_antes = antes
    return [nonce_antes + n * j for j in range(minero.intentos - intentos_antes)]


def ronda_con_empate(sesion: SesionPow, max_rondas: int = 50) -> list[dict]:
    """Avanza rondas (reanudando a quien encuentra) hasta una con dos o más hallazgos."""
    for _ in range(max_rondas):
        hallazgos = ronda_pow(sesion, set(IDS))
        if len(hallazgos) > 1:
            return hallazgos
        for h in hallazgos:
            reanudar_tras_rechazo(sesion, h["minero"])
    raise AssertionError("No hubo empate en las rondas probadas")


# ---------------------------------------------------------------- creación

def test_constantes_de_estado():
    assert ESTADOS == ("minando", "ganador", "cancelada", "agotada", "terminada")
    assert set(ACTIVOS) | set(FINALES) == set(ESTADOS) and not set(ACTIVOS) & set(FINALES)


def test_nonces_iniciales_i_y_estado_inicial():
    sesion = sesion_nueva(k=7, dificultad=4, max_rondas=30, bloques_restantes=3, auto_tx=True)
    assert [(m.id, m.indice, m.nonce, m.intentos, m.encontro) for m in sesion.mineros] == [
        (i, n, n, 0, False) for n, i in enumerate(IDS)]
    assert (sesion.estado, sesion.ronda, sesion.N, sesion.k, sesion.dificultad) == ("minando", 0, N, 7, 4)
    assert sesion.bloques_restantes == 3 and sesion.auto_tx is True and sesion.activa
    assert sesion.ultimo_empate is None and sesion.ganador is None


def test_la_sesion_copia_los_candidatos():
    propios = candidatos()
    sesion = SesionPow(1, propios, IDS, 5, 1, 10)
    propios["N01"]["recompensa"]["monto"] = 999
    assert sesion.candidatos["N01"]["recompensa"]["monto"] == RECOMPENSA


@pytest.mark.parametrize("cambios", [
    {"ids": []},
    {"ids": ["N01", "N01"]},
    {"ids": ["N01", 2]},
    {"k": 0},
    {"k": True},
    {"dificultad": 0},
    {"dificultad": 65},
    {"max_rondas": 0},
    {"numero": 0},
    {"bloques_restantes": 0},
    {"candidatos": ["no", "es", "dict"]},
])
def test_constructor_rechaza_parametros_imposibles(cambios):
    argumentos = {"numero": 1, "candidatos": candidatos(), "ids": IDS, "k": 5, "dificultad": 1,
                  "max_rondas": 10, "bloques_restantes": 1, **cambios}
    with pytest.raises(ValueError):
        SesionPow(**argumentos)


# ---------------------------------------------------------------- hash rápido

@pytest.mark.parametrize("bloque", [
    candidatos()["N03"],
    nuevo_bloque(7, 123, [TX, TX], "ab" * 32, "N09", 500, "pow", castigos=[{"x": 1}]),
    {"numero": 1, "proponente": "N01", "nonce": 0, "hash": ""},
    {"zeta": "ñ   \"comillas\" \\", "a": {"b": [1, 2.5, None, True, {"z": 1, "a": 2}]}, "nonce": 9},
    {"solo": "sin nonce"},
    {},
])
def test_hash_rapido_igual_a_hash_bloque(bloque):
    calcular = preparar_hash(bloque)
    for nonce in itertools.chain(range(300), (10**9, 2**62 + 7)):
        assert calcular(nonce) == hash_bloque({**bloque, "nonce": nonce})


# ---------------------------------------------------------------- rondas

def test_ronda_con_k_intentos_y_sin_hallazgo():
    sesion = sesion_nueva(k=7, dificultad=6)
    assert ronda_pow(sesion, set(IDS)) == []
    assert sesion.ronda == 1
    for m in sesion.mineros:
        assert m.intentos == 7
        assert m.nonce == m.indice + 7 * N
        ultimo = m.indice + 6 * N   # el último nonce probado
        assert m.ultimo_hash == hash_bloque({**sesion.candidatos[m.id], "nonce": ultimo})
        assert not m.encontro
    assert sesion.intentos_totales() == 7 * N
    assert "Ronda 1 de 100" in sesion.mensaje


def test_mineros_desconectados_no_prueban():
    sesion = sesion_nueva(k=4, dificultad=6)
    conectados = set(IDS) - {"N03", "N07"}
    for _ in range(3):
        ronda_pow(sesion, conectados)
    for m in sesion.mineros:
        if m.id in ("N03", "N07"):
            assert (m.intentos, m.nonce, m.ultimo_hash, m.conectado) == (0, m.indice, "", False)
        else:
            assert m.intentos == 12 and m.nonce == m.indice + 12 * N and m.conectado
    mineros = {m["id"]: m for m in sesion.a_dict()["mineros"]}
    assert mineros["N03"]["conectado"] is False and mineros["N04"]["conectado"] is True


def test_ningun_minero_conectado():
    sesion = sesion_nueva(dificultad=6)
    assert ronda_pow(sesion, []) == []
    assert sesion.ronda == 1 and sesion.intentos_totales() == 0
    assert "ningún minero conectado" in sesion.mensaje


def test_minero_sin_candidato_no_prueba():
    ids = IDS[:3]
    sesion = SesionPow(1, {i: c for i, c in candidatos(ids=ids).items() if i != "N02"}, ids, 5, 6, 10)
    ronda_pow(sesion, set(ids))
    assert [m.intentos for m in sesion.mineros] == [5, 0, 5]


def test_nonces_disjuntos_en_muchas_rondas():
    """Con hallazgos, rechazos y desconexiones, ningún nonce se prueba dos veces."""
    sesion = sesion_nueva(k=7, dificultad=2, max_rondas=1000)
    probados: dict[str, list[int]] = {i: [] for i in IDS}
    hubo_hallazgos = False
    for ronda in range(60):
        conectados = set(IDS) - {IDS[ronda % N]}   # cada ronda falta un minero distinto
        antes = foto(sesion)
        hallazgos = ronda_pow(sesion, conectados)
        for m in sesion.mineros:
            nuevos = nonces_probados(antes[m.id], m, N)
            if m.id not in conectados:
                assert nuevos == [] and (m.nonce, m.intentos) == antes[m.id]
                continue
            assert 1 <= len(nuevos) <= 7
            assert m.ultimo_hash == hash_bloque({**sesion.candidatos[m.id], "nonce": nuevos[-1]})
            if m.encontro:
                assert m.nonce == nuevos[-1] and cumple_dificultad(m.ultimo_hash, 2)
            else:
                assert len(nuevos) == 7 and m.nonce == nuevos[-1] + N
            probados[m.id].extend(nuevos)
        hubo_hallazgos = hubo_hallazgos or bool(hallazgos)
        for h in hallazgos:   # la red los "rechaza": siguen desde nonce + N
            reanudar_tras_rechazo(sesion, h["minero"], "rechazo de prueba")
    assert hubo_hallazgos
    todos = list(itertools.chain.from_iterable(probados.values()))
    assert len(todos) == len(set(todos)), "Algún nonce se probó dos veces"
    for indice, id_minero in enumerate(IDS):
        assert probados[id_minero], f"{id_minero} nunca minó"
        assert all(nonce % N == indice for nonce in probados[id_minero])
        assert len(set(probados[id_minero])) == len(probados[id_minero])
    for a, b in itertools.combinations(IDS, 2):
        assert not set(probados[a]) & set(probados[b])


def test_cada_minero_se_detiene_en_su_primer_hallazgo_y_la_ronda_no_se_corta():
    sesion = sesion_nueva(k=5, dificultad=1)
    hallazgos = ronda_pow(sesion, set(IDS))
    assert hallazgos, "Con dificultad 1 y 50 intentos debería haber hallazgos"
    assert all(m.intentos >= 1 for m in sesion.mineros), "Todos los mineros juegan su turno"
    encontraron = []
    for m in sesion.mineros:
        probados = [m.indice + N * j for j in range(m.intentos)]
        hashes = [hash_bloque({**sesion.candidatos[m.id], "nonce": n}) for n in probados]
        # Ningún nonce anterior al último era válido: se detiene en el PRIMERO.
        assert not any(cumple_dificultad(h, 1) for h in hashes[:-1])
        if m.encontro:
            assert cumple_dificultad(hashes[-1], 1) and m.nonce == probados[-1] and m.ultimo_hash == hashes[-1]
            encontraron.append({"minero": m.id, "indice": m.indice, "nonce": m.nonce, "hash": hashes[-1]})
        else:
            assert m.intentos == 5 and not cumple_dificultad(hashes[-1], 1)
    assert hallazgos == encontraron
    # Los que encontraron esperan el resultado: en la siguiente ronda no prueban más.
    antes = foto(sesion)
    ronda_pow(sesion, set(IDS))
    for h in hallazgos:
        assert foto(sesion)[h["minero"]] == antes[h["minero"]]


# ---------------------------------------------------------------- ganador y empate

def test_ordenar_y_elegir_ganador_con_hallazgos_fabricados():
    h1 = {"minero": "N04", "indice": 3, "nonce": 13, "hash": "0f" + "1" * 62}
    h2 = {"minero": "N02", "indice": 1, "nonce": 31, "hash": "0a" + "f" * 62}
    h3 = {"minero": "N09", "indice": 8, "nonce": 8, "hash": "0e" + "0" * 62}
    assert [h["minero"] for h in ordenar_hallazgos([h1, h2, h3])] == ["N02", "N09", "N04"]
    ganador, empatados = elegir_ganador([h1, h2, h3])
    assert ganador == h2 and [h["minero"] for h in empatados] == ["N02", "N09", "N04"]
    assert elegir_ganador([h1]) == (h1, [])
    mismo = "0" + "7" * 63   # empate exacto de hash: gana el menor índice
    ganador, _ = elegir_ganador([{"minero": "N05", "indice": 4, "nonce": 4, "hash": mismo},
                                 {"minero": "N03", "indice": 2, "nonce": 12, "hash": mismo}])
    assert ganador["minero"] == "N03"
    with pytest.raises(ValueError):
        elegir_ganador([])
    ordenados = ordenar_hallazgos([h1])
    ordenados[0]["hash"] = "x"
    assert h1["hash"].startswith("0f"), "ordenar_hallazgos no debe modificar los originales"


def test_empate_real_gana_el_hash_menor_y_su_bloque_es_valido():
    sesion = sesion_nueva(k=5, dificultad=1)
    hallazgos = ronda_con_empate(sesion)
    ganador, empatados = elegir_ganador(hallazgos)
    assert ganador == min(hallazgos, key=lambda h: (h["hash"], h["indice"]))
    assert len(empatados) == len(hallazgos) > 1
    assert sesion.ultimo_empate == ordenar_hallazgos(hallazgos)
    assert "gana el hash menor" in sesion.mensaje

    original = copy.deepcopy(sesion.candidatos[ganador["minero"]])
    bloque = bloque_ganador(sesion, ganador)
    assert sesion.candidatos[ganador["minero"]] == original, "El candidato guardado no cambia"
    assert (bloque["nonce"], bloque["hash"], bloque["proponente"]) == (
        ganador["nonce"], ganador["hash"], ganador["minero"])
    assert bloque["hash"] == hash_bloque(bloque) and cumple_dificultad(bloque["hash"], 1)
    assert bloque["recompensa"] == {"beneficiario": ganador["minero"], "monto": RECOMPENSA}
    assert validar_bloque(bloque, GENESIS, Libro(GENESIS), GENESIS) is None


def test_cierre_prueba_hallazgos_en_orden_y_el_primero_valido_gana():
    """Si el mejor hash es de un tramposo, la red lo rechaza y gana el siguiente válido."""
    honestos = {"N02", "N05"}
    recompensas = {i: (RECOMPENSA if i in honestos else RECOMPENSA * 10) for i in IDS}
    sesion = SesionPow(1, candidatos(recompensas), IDS, 5, 1, 500)
    ganador = None
    for _ in range(500):
        hallazgos = ronda_pow(sesion, set(IDS))
        for h in ordenar_hallazgos(hallazgos):
            bloque = bloque_ganador(sesion, h)
            motivo = validar_bloque(bloque, GENESIS, Libro(GENESIS), GENESIS)
            if motivo is None:
                ganador = h
                break
            assert "recompensa falsa" in motivo
            reanudar_tras_rechazo(sesion, h["minero"], motivo)
        if ganador:
            break
    assert ganador and ganador["minero"] in honestos
    assert registrar_ganador(sesion, ganador) == "terminada"
    assert sesion.ganador == ganador
    assert all(r["minero"] not in honestos and "recompensa" in r["motivo"] for r in sesion.historial)


def test_reanudar_tras_rechazo_sigue_desde_nonce_mas_n():
    sesion = sesion_nueva(k=5, dificultad=1)
    hallazgos = ronda_pow(sesion, set(IDS))
    h = hallazgos[0]
    minero = sesion.minero(h["minero"])
    reanudar_tras_rechazo(sesion, h["minero"], "recompensa falsa")
    assert (minero.nonce, minero.encontro, sesion.estado) == (h["nonce"] + N, False, "minando")
    assert sesion.historial[-1] == {"ronda": 1, "minero": h["minero"], "nonce": h["nonce"], "hash": h["hash"],
                                    "motivo": "recompensa falsa"}
    # En la siguiente ronda empieza justo en nonce + N.
    intentos_antes = minero.intentos
    ronda_pow(sesion, {h["minero"]})
    assert minero.intentos > intentos_antes
    primero = h["nonce"] + N
    probados = [primero + N * j for j in range(minero.intentos - intentos_antes)]
    assert minero.ultimo_hash == hash_bloque({**sesion.candidatos[minero.id], "nonce": probados[-1]})
    # Un minero que no había encontrado no se salta su nonce pendiente.
    otro = next(m for m in sesion.mineros if not m.encontro and m.id != h["minero"])
    nonce = otro.nonce
    reanudar_tras_rechazo(sesion, otro.id)
    assert otro.nonce == nonce
    with pytest.raises(ValueError):
        reanudar_tras_rechazo(sesion, "N99")


def test_historial_de_rechazos_acotado():
    sesion = sesion_nueva(k=5, dificultad=1, max_rondas=10_000)
    for _ in range(400):
        for h in ronda_pow(sesion, set(IDS)):
            reanudar_tras_rechazo(sesion, h["minero"], "rechazado")
    assert 0 < len(sesion.historial) <= 50


# ---------------------------------------------------------------- estados

def test_ganador_con_bloques_restantes_y_terminada_sin_ellos():
    for restantes, esperado in ((3, "ganador"), (1, "terminada")):
        sesion = sesion_nueva(bloques_restantes=restantes)
        hallazgo = ronda_con_empate(sesion)[0]
        assert registrar_ganador(sesion, hallazgo) == esperado == sesion.estado
        assert hallazgo["minero"] in sesion.mensaje and "detienen" in sesion.mensaje
        assert sesion.activa is (esperado == "ganador")
        with pytest.raises(Conflicto):
            ronda_pow(sesion, set(IDS))
        with pytest.raises(Conflicto):
            registrar_ganador(sesion, hallazgo)


def test_terminar_tras_ganador():
    sesion = sesion_nueva(bloques_restantes=2)
    registrar_ganador(sesion, ronda_con_empate(sesion)[0])
    terminar_sesion(sesion, "No quedan transacciones pendientes")
    assert (sesion.estado, sesion.mensaje) == ("terminada", "No quedan transacciones pendientes")
    with pytest.raises(Conflicto):
        terminar_sesion(sesion, "otra vez")


def test_limite_de_rondas_agota_la_sesion():
    sesion = sesion_nueva(k=2, dificultad=6, max_rondas=3)
    for ronda in range(1, 4):
        assert ronda_pow(sesion, set(IDS)) == []
        assert revisar_limite(sesion) is (ronda == 3)
    assert sesion.estado == "agotada" and not sesion.activa
    assert "límite de 3 rondas" in sesion.mensaje
    assert revisar_limite(sesion) is False
    with pytest.raises(Conflicto):
        ronda_pow(sesion, set(IDS))


def test_cancelar_sesion():
    sesion = sesion_nueva(dificultad=6)
    ronda_pow(sesion, set(IDS))
    cancelar_sesion(sesion)
    assert sesion.estado == "cancelada" and "cancelada" in sesion.mensaje
    with pytest.raises(Conflicto) as info:
        cancelar_sesion(sesion)
    assert info.value.codigo == "sin_mineria"
    with pytest.raises(Conflicto):
        ronda_pow(sesion, set(IDS))
    reanudar_tras_rechazo(sesion, "N01")   # no revive una sesión terminada
    assert sesion.estado == "cancelada"


def test_cancelar_en_estado_ganador():
    sesion = sesion_nueva(bloques_restantes=5)
    registrar_ganador(sesion, ronda_con_empate(sesion)[0])
    cancelar_sesion(sesion)
    assert sesion.estado == "cancelada"


# ---------------------------------------------------------------- a_dict

def test_a_dict_serializable_y_con_las_claves_del_contrato():
    sesion = sesion_nueva(k=5, dificultad=1, bloques_restantes=2, auto_tx=True)
    ronda_con_empate(sesion)
    sesion.fijar_conectados(set(IDS) - {"N10"})
    datos = sesion.a_dict()
    assert set(datos) == CLAVES_A_DICT
    assert json.loads(json.dumps(datos)) == datos
    assert all(set(m) == CLAVES_MINERO for m in datos["mineros"])
    assert [m["id"] for m in datos["mineros"]] == IDS
    assert datos["esperado"] == 16 and datos["dificultad"] == 1 and datos["k"] == 5
    assert datos["intentos_totales"] == sum(m["intentos"] for m in datos["mineros"])
    assert datos["ultimo_empate"] and datos["bloques_restantes"] == 2 and datos["auto_tx"] is True
    assert datos["mineros"][-1]["conectado"] is False
    datos["mineros"][0]["nonce"] = -1
    datos["ultimo_empate"][0]["hash"] = "alterado"
    assert sesion.mineros[0].nonce != -1 and sesion.ultimo_empate[0]["hash"] != "alterado"


def test_a_dict_en_todos_los_estados():
    sesion = sesion_nueva(dificultad=6, max_rondas=1)
    assert sesion.a_dict()["estado"] == "minando"
    ronda_pow(sesion, set(IDS))
    revisar_limite(sesion)
    datos = sesion.a_dict()
    assert datos["estado"] == "agotada" and json.dumps(datos)
    assert datos["esperado"] == 16 ** 6
