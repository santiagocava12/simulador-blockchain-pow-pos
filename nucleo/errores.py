"""Excepciones del simulador. Cada una sabe qué código HTTP le corresponde.

Las rutas de Flask convierten cualquier ``ErrorSimulacion`` en una respuesta
JSON ``{"ok": false, "error": ..., "codigo": ..., "detalles": {...}}``.
"""


class ErrorSimulacion(Exception):
    """Error esperado: entrada inválida, recurso inexistente, conflicto, etc."""

    http = 400
    codigo_defecto = "entrada_invalida"

    def __init__(self, mensaje: str, codigo: str | None = None, detalles: dict | None = None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.codigo = codigo or self.codigo_defecto
        self.detalles = dict(detalles) if detalles else {}

    def a_dict(self) -> dict:
        """Forma JSON del error para la API."""
        return {"ok": False, "error": self.mensaje, "codigo": self.codigo, "detalles": self.detalles or {}}


class EntradaInvalida(ErrorSimulacion):
    """El usuario envió un valor que no se puede aceptar (HTTP 400)."""

    http = 400
    codigo_defecto = "entrada_invalida"


class NoEncontrado(ErrorSimulacion):
    """Se pidió un nodo u objeto que no existe (HTTP 404)."""

    http = 404
    codigo_defecto = "no_encontrado"


class Conflicto(ErrorSimulacion):
    """La acción no procede en el estado actual (HTTP 409)."""

    http = 409
    codigo_defecto = "conflicto"


class ErrorInterno(ErrorSimulacion):
    """Falla inesperada; la operación se revirtió (HTTP 500)."""

    http = 500
    codigo_defecto = "error_interno"
