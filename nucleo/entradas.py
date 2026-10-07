"""Lectura estricta de valores no confiables (vienen de JSON, formularios o pruebas).

Cada función devuelve el valor ya limpio o lanza ``EntradaInvalida``
(``NoEncontrado`` en ``leer_id_nodo``) con un mensaje claro en español.
Nunca lanza otra excepción, ni siquiera con valores absurdos (listas anidadas,
números de miles de cifras, NaN, texto con caracteres de control...).
"""

import math
import re
import reprlib
import unicodedata

from nucleo.errores import EntradaInvalida, NoEncontrado

MAX_ENTERO = 10**12

# Entero escrito como texto: espacios opcionales, signo opcional y sólo dígitos 0-9.
# El contrato acepta hasta 15 cifras; aquí se lee cualquier largo para que un
# número enorme reciba el mensaje "fuera de rango" en vez de "no es un número".
_PATRON_DIGITOS = re.compile(r"\s*([+-]?)(\d+)\s*", re.ASCII)
_PATRON_DECIMAL = re.compile(r"\s*[+-]?(\d+\.\d*|\.\d+)\s*", re.ASCII)
_PATRON_NODO = re.compile(r"\s*[Nn]?0*(\d{1,6})\s*", re.ASCII)

_TOPE_MOSTRAR = 10**40  # números más grandes se describen, no se imprimen

# repr acotado: nunca recorre estructuras profundas ni cadenas enormes.
_REPR = reprlib.Repr()
_REPR.maxlevel = 3
_REPR.maxstring = 40
_REPR.maxother = 40
_REPR.maxlong = 40


def _mostrar(valor) -> str:
    """Representación corta (máx. 40 caracteres) y segura de cualquier valor."""
    if isinstance(valor, int) and not isinstance(valor, bool) and abs(valor) >= _TOPE_MOSTRAR:
        return _mostrar_entero(valor)
    try:
        texto = _REPR.repr(valor)
    except Exception:
        texto = f"<{type(valor).__name__}>"
    return texto[:40]


def _mostrar_entero(n: int) -> str:
    """Un entero para un mensaje; los gigantescos se describen en palabras."""
    if abs(n) < _TOPE_MOSTRAR:
        return str(n)
    return "un número negativo de más de 40 cifras" if n < 0 else "un número de más de 40 cifras"


def texto_visible(texto: str, largo: int = 40) -> str:
    """Texto recibido, recortado y listo para repetirse en un mensaje.

    Los caracteres de control (Cc) y las mitades sueltas de pares sustitutos (Cs,
    como "\\ud800") se escriben escapados: así el mensaje siempre se puede guardar
    como UTF-8 (JSON, bitácora, consola) sin provocar un error.
    """
    recorte = texto[:largo]
    return "".join(ascii(c)[1:-1] if unicodedata.category(c) in ("Cc", "Cs") else c for c in recorte)


def _vacio(valor) -> bool:
    """None o texto en blanco: el usuario no escribió nada."""
    return valor is None or (isinstance(valor, str) and valor.strip() == "")


def _a_entero(valor, nombre: str) -> int:
    """Convierte a int sin revisar el rango (sólo errores de tipo y formato)."""
    if _vacio(valor):
        raise EntradaInvalida(f"{nombre} es obligatorio")
    if isinstance(valor, bool):  # en Python True es 1, pero no lo aceptamos como número
        raise EntradaInvalida(f"{nombre} debe ser un número entero (recibido: {_mostrar(valor)})")
    if isinstance(valor, int):
        return int(valor)
    if isinstance(valor, float):
        if not math.isfinite(valor):
            raise EntradaInvalida(f"{nombre} debe ser un número entero (recibido: {_mostrar(valor)})")
        if not valor.is_integer():
            raise EntradaInvalida(f"{nombre} debe ser un número entero, sin decimales")
        return int(valor)
    if isinstance(valor, str):
        coincide = _PATRON_DIGITOS.fullmatch(valor)
        if coincide:
            signo, digitos = coincide.groups()
            digitos = digitos.lstrip("0") or "0"
            if len(digitos) > 40:  # no se convierte: sólo importa que es enorme
                digitos = "1" + "0" * 40
            n = int(digitos)
            return -n if signo == "-" else n
        if _PATRON_DECIMAL.fullmatch(valor):
            raise EntradaInvalida(f"{nombre} debe ser un número entero, sin decimales")
    raise EntradaInvalida(f"{nombre} debe ser un número entero (recibido: {_mostrar(valor)})")


def exigir_objeto(datos) -> dict:
    """El cuerpo de una petición debe ser un objeto JSON (dict)."""
    if isinstance(datos, dict):
        return datos
    raise EntradaInvalida("El cuerpo de la petición debe ser un objeto JSON")


def leer_entero(valor, nombre: str, minimo: int, maximo: int) -> int:
    """Entero entre minimo y maximo. Acepta 25, 25.0 y "25"; rechaza True, 2.5, "abc"."""
    n = _a_entero(valor, nombre)
    if not minimo <= n <= maximo:
        raise EntradaInvalida(
            f"{nombre} debe estar entre {minimo} y {maximo} (recibido: {_mostrar_entero(n)})"
        )
    return n


def leer_monto(valor, nombre: str = "El monto") -> int:
    """Monto de una transacción: entero de 1 a MAX_ENTERO con mensajes específicos."""
    n = _a_entero(valor, nombre)
    if n < 0:
        raise EntradaInvalida(f"{nombre} no puede ser negativo (recibido: {_mostrar_entero(n)})")
    if n == 0:
        raise EntradaInvalida(f"{nombre} debe ser mayor que cero")
    if n > MAX_ENTERO:
        raise EntradaInvalida(f"{nombre} es demasiado grande (máximo {MAX_ENTERO})")
    return n


def leer_texto(valor, nombre: str, max_largo: int = 64, obligatorio: bool = True) -> str:
    """Texto sin espacios en los extremos, de largo acotado y sin caracteres de control."""
    if valor is None:
        if obligatorio:
            raise EntradaInvalida(f"{nombre} es obligatorio")
        return ""
    if not isinstance(valor, str):
        raise EntradaInvalida(f"{nombre} debe ser texto")
    texto = valor.strip()
    if not texto:
        if obligatorio:
            raise EntradaInvalida(f"{nombre} es obligatorio")
        return ""
    if len(texto) > max_largo:
        raise EntradaInvalida(f"{nombre} es demasiado largo (máximo {max_largo} caracteres)")
    # Cc = caracteres de control; Cs = mitades sueltas de pares sustitutos (no se pueden codificar).
    if any(unicodedata.category(c) in ("Cc", "Cs") for c in texto):
        raise EntradaInvalida(f"{nombre} contiene caracteres no permitidos (de control)")
    return texto


def leer_opcion(valor, nombre: str, opciones: tuple[str, ...]) -> str:
    """Una de las opciones dadas, sin distinguir mayúsculas; devuelve la forma canónica."""
    if isinstance(valor, str):
        buscado = valor.strip().lower()
        for opcion in opciones:
            if opcion.lower() == buscado:
                return opcion
    raise EntradaInvalida(f"{nombre} debe ser uno de: {', '.join(opciones)} (recibido: {_mostrar(valor)})")


def leer_bool(valor, nombre: str) -> bool:
    """Verdadero o falso: True/False, 1/0 o los textos true, false, si, sí, no."""
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, int) and valor in (0, 1):
        return valor == 1
    if isinstance(valor, str):
        texto = valor.strip().lower()
        if texto in ("true", "si", "sí"):
            return True
        if texto in ("false", "no"):
            return False
    raise EntradaInvalida(f"{nombre} debe ser verdadero o falso")


def leer_id_nodo(valor, nombre: str, ids_validos) -> str:
    """Identificador de nodo: "N3", "n03", "3" o 3 se normalizan a "N03" si existe."""
    if _vacio(valor):
        raise EntradaInvalida(f"{nombre} es obligatorio")
    if isinstance(valor, bool) or not isinstance(valor, (int, str)):
        raise EntradaInvalida(
            f"{nombre} debe ser un identificador de nodo como N03 (recibido: {_mostrar(valor)})"
        )
    ids = list(ids_validos)
    if isinstance(valor, int):
        numero = valor
        texto = _mostrar_entero(valor)
    else:
        coincide = _PATRON_NODO.fullmatch(valor)
        numero = int(coincide.group(1)) if coincide else None
        texto = texto_visible(valor.strip())
    if numero is not None and 0 <= numero < 1000:
        candidato = f"N{numero:02d}"
        if candidato in ids:
            return candidato
    validos = f"N01–N{len(ids):02d}" if ids else "no hay nodos"
    raise NoEncontrado(
        f"{nombre}: el nodo '{texto}' no existe (nodos válidos: {validos})",
        codigo="nodo_inexistente",
    )
