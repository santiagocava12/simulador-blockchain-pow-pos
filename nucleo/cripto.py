"""Firmas digitales Ed25519: claves reproducibles, firmar y verificar.

Ed25519 es determinista (mismo mensaje y clave => misma firma) y rápido.
Las claves se derivan de la semilla, así la misma semilla da los mismos nodos.

Limitación conocida (de la simulación, no de Ed25519): la semilla es pública
(viaja en el génesis), así que quien la conozca puede derivar todas las claves
privadas. Las carteras viven en el servidor y las firmas sirven para detectar
transacciones, bloques y votos alterados, no para proteger a los nodos de un
cliente externo. En una red real cada nodo genera su clave al azar y no la comparte.
"""

import hashlib
import re
from functools import lru_cache

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

_HEX = re.compile(r"[0-9a-f]+")


def es_hex(valor, largo: int | None = None) -> bool:
    """True si `valor` es texto hexadecimal en minúsculas (y del largo dado, si se pide)."""
    if not isinstance(valor, str) or not valor:
        return False
    if largo is not None and len(valor) != largo:
        return False
    return _HEX.fullmatch(valor) is not None


def generar_claves(semilla: str, id_nodo: str) -> tuple[Ed25519PrivateKey, str]:
    """Par de claves del nodo derivado de la semilla: (clave privada, clave pública hex)."""
    secreto = hashlib.sha256(f"{semilla}|{id_nodo}|clave".encode()).digest()  # 32 bytes
    privada = Ed25519PrivateKey.from_private_bytes(secreto)
    publica_hex = privada.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return privada, publica_hex


def firmar(clave_privada: Ed25519PrivateKey, datos: bytes) -> str:
    """Firma `datos` y devuelve la firma en hex (128 caracteres)."""
    return clave_privada.sign(datos).hex()


def verificar(clave_publica_hex, datos, firma_hex) -> bool:
    """True si la firma es válida. Nunca lanza: cualquier dato raro da False."""
    # Sólo se memoriza con los tipos exactos; así lru_cache nunca recibe algo raro.
    if type(clave_publica_hex) is not str or type(datos) is not bytes or type(firma_hex) is not str:
        return False
    try:
        return _verificar_memorizado(clave_publica_hex, datos, firma_hex)
    except Exception:
        return False


@lru_cache(maxsize=200_000)
def _verificar_memorizado(clave_publica_hex: str, datos: bytes, firma_hex: str) -> bool:
    """Verificación real; es una función pura, por eso se puede memorizar."""
    if not es_hex(clave_publica_hex, 64) or not es_hex(firma_hex, 128):
        return False
    try:
        clave = Ed25519PublicKey.from_public_bytes(bytes.fromhex(clave_publica_hex))
        clave.verify(bytes.fromhex(firma_hex), datos)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
