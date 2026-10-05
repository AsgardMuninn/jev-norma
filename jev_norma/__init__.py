"""jev_norma — paquete raíz: triaje normativo LatAm con Jev (System One) como gate.

Pipeline (0-IA para normalizar, Jev para decidir):
  documento (HTML/PDF) -> cláusulas (heurística sin palabras) -> gate Jev
  (choice [obligacion|riesgo|sin_accion] + confidence calibrada) ->
  reporte JSON con cola de revisión humana (fail-open: conf < umbral -> humano).
"""

__version__ = "0.1.0"
__all__ = [
    "normalize_document",
    "segment_plain_text",
    "normalize_clauses_general",
    "build_questions",
    "split_chunks",
    "call",
    "load_key",
    "sanitize_clause",
    "build_report",
    "GATE_UMBRAL",
    "CHUNK_SIZE",
    "fetch_html",
    "BUILTIN_UA",
]

from .normalizer import normalize_clauses_general, segment_plain_text
from .gate import build_questions, call, load_key, sanitize_clause, split_chunks
from .fetch import fetch_html, BUILTIN_UA
from .pipeline import (
    CHUNK_SIZE,
    GATE_UMBRAL,
    build_report,
    gate,
    normalize_document,
)

# Re-export para comodidad (el pipeline normaliza+decide en un solo objeto).
__all__ += ["gate"]