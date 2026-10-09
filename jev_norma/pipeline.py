"""jev_norma.pipeline — orquesta el pipeline completo: documento -> cláusulas -> gate Jev -> reporte.

Reúne normalizer (0-IA) + gate (Jev System One) en una sola API:
  * normalize_document(path) -> {clauses, headers, stats}  (HTML o PDF)
  * gate(sample) -> {latencia_ms, in_tokens, answers}       (Jev real, chunked)
  * build_report(sample, answers, ...) -> reporte fail-open con cola de revisión humana
"""

from __future__ import annotations

import sys
from pathlib import Path

from .gate import build_questions, call, load_key, sanitize_clause, split_chunks
from .normalizer import MAX_CLAUSE_CHARS, normalize_clauses_general, segment_plain_text

CHUNK_SIZE = 6
GATE_UMBRAL = 0.8  # confianza < umbral -> cola de revisión humana (fail-open)


def detect_input(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".html", ".htm"):
        return "html"
    if ext == ".pdf":
        return "pdf"
    return "desconocido"


def normalize_document(path: Path, ocr: bool = False, ocr_workers: int = 4) -> dict:
    """Documento (HTML o PDF) -> {'clauses', 'headers', 'stats'} con normalizador 0-IA.

    `ocr=True` habilita la ruta OCR (WU11) para PDFs escaneados/imagen: si el PDF
    no tiene capa de texto, se transcribe página a página con un VLM vía
    OpenRouter (GLM-5.3-Flash) y se devuelve a segmentar por la heurística normal.
    `ocr_workers` (WU12) solapa páginas del escaneado en paralelo (I/O-bound).
    Sin `ocr`, un PDF escaneado falla de forma clara y sugerente (no engaña).
    """
    if detect_input(path) == "pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            sys.exit("error: pypdf no instalado. `pip install jev-norma[pdf]` para leer PDFs.")
        try:
            reader = PdfReader(str(path))
        except Exception as e:
            sys.exit(f"error: no se pudo abrir el PDF ({path}): {e}")
        text = "\n\n".join((pg.extract_text() or "") for pg in reader.pages)
        if not text.strip():
            if not ocr:
                sys.exit("error: PDF sin capa de texto (¿escaneado/imagen?). "
                         "Reintenta con `--ocr` o `pip install jev-norma[ocr]`.")
            from .ocr import ocr_pdf
            try:
                o = ocr_pdf(path, max_workers=ocr_workers)
            except ImportError:
                sys.exit("error: OCR requiere pymupdf. `pip install jev-norma[ocr]`.")
            if not o["text"].strip():
                sys.exit("error: el OCR no devolvió texto para este escaneado.")
            r = segment_plain_text(o["text"])
            r.setdefault("headers", [])
            r["stats"]["ocr"] = True
            r["stats"]["ocr_n_pages"] = o["stats"]["n_pages"]
            r["stats"]["ocr_workers"] = o["stats"].get("workers", 1)
            r["stats"]["ocr_latencia_ms"] = o["stats"]["latencia_ms"]
            return r
        r = segment_plain_text(text)
        r.setdefault("headers", [])
        return r
    raw = path.read_text(encoding="utf-8")
    return normalize_clauses_general(raw)


def gate(sample: dict) -> dict:
    """Decisión tipada real contra la API de Jev (chunked). Requiere TYPESAFE_API_KEY."""
    key = load_key()
    if not key:
        sys.exit("error: TYPESAFE_API_KEY no encontrada en ~/.hermes/.env")
    qs = build_questions(sample)
    r = {"latencia_ms": 0, "in_tokens": 0, "out_tokens": 0, "answers": {}}
    for chunk in split_chunks(qs, CHUNK_SIZE):
        rk = call(key, chunk)
        r["latencia_ms"] += rk["latencia_ms"]
        r["in_tokens"] += rk["in_tokens"]
        r["out_tokens"] += rk["out_tokens"]
        r["answers"].update(rk["answers"])
    return r


def build_report(sample: dict, answers: dict, in_tokens: int, lat_ms: int) -> dict:
    """Reporte de decisión tipada con cola de revisión humana (fail-open)."""
    por_bucket: dict = {}
    tabla = []
    for cid, txt in sample.items():
        a = answers.get(cid, {})
        dec = a.get("bucket") or "NO_BUCKET"
        por_bucket[dec] = por_bucket.get(dec, 0) + 1
        conf = a.get("confianza", 0)
        tabla.append({"clausula": cid, "texto": sanitize_clause(txt), "decidido": dec,
                      "confianza": conf, "gate_humano": conf < GATE_UMBRAL})
    return {
        "gate": "choice [obligacion|riesgo|sin_accion] + confidence",
        "umbral_gate_humano": GATE_UMBRAL,
        "clausulas_al_gate": len(sample),
        "por_bucket": por_bucket,
        "in_tokens": in_tokens,
        "latencia_ms": lat_ms,
        "costo_usd_estimado": round(in_tokens * 42e-9, 6),
        "tabla": tabla,
        "clausulas_a_revision": [t["clausula"] for t in tabla if t["gate_humano"]],
    }


def sample_indices(clauses: list, n: int) -> list:
    """Índices de un subconjunto REPRESENTATIVO distribuido (no curado) de las cláusulas."""
    if n <= 0:
        return list(range(len(clauses)))
    n = min(n, len(clauses))
    step = max(1, len(clauses) // n)
    return list(range(0, len(clauses), step))[:n]


__all__ = [
    "CHUNK_SIZE", "GATE_UMBRAL", "MAX_CLAUSE_CHARS",
    "detect_input", "normalize_document", "gate", "build_report", "sample_indices",
]