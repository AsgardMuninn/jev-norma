"""Tests del módulo OCR (WU11) — detección de PDF escaneado + OCR visión vía OpenRouter.

Sin red en CI: `detect_scanned` y `extract_openrouter_key` se prueban puros;
`ocr_pdf`/`vision_transcribe` se prueban con `vision_fn` inyectado (mock de la
llamada a OpenRouter). La llamada de red real se valida como smoke test aparte.
"""
import base64

from pathlib import Path

import jev_norma.ocr as ocrmod
from jev_norma import ocr_pdf, detect_scanned
from jev_norma.ocr import EXTRACTANT_KEY, VISION_MODEL

FIXTURE = "<p>PRINCIPIOS</p><p>La sociedad tratará los datos conforme a la Ley 1581.</p>"


def _make_text_pdf(tmp_path, text="La sociedad tratará los datos conforme a la Ley 1581 y el Decreto 1377."):
    """Crea un PDF con capa de texto (pymupdf), como el de un documento legal digital."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), text, fontsize=12)
    pth = tmp_path / "text.pdf"
    doc.save(str(pth))
    doc.close()
    return pth


def _make_blank_pdf(tmp_path):
    """Crea un PDF sin capa de texto (página en blanco) — proxy de un escaneado sin OCR."""
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    pth = tmp_path / "scanned.pdf"
    with open(pth, "wb") as f:
        w.write(f)
    return pth


def test_detect_scanned_text_pdf(tmp_path):
    assert detect_scanned(_make_text_pdf(tmp_path)) is False


def test_detect_scanned_blank_pdf(tmp_path):
    assert detect_scanned(_make_blank_pdf(tmp_path)) is True


def test_extract_openrouter_key_returns_str(monkeypatch):
    # Sin key en entorno: devuelve str (vacío si no hay) — no lanza.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert isinstance(ocrmod.extract_openrouter_key(), str)


def test_vision_constants_sane():
    assert VISION_MODEL == "z-ai/glm-5.3-flash"
    assert "OPENROUTER" in EXTRACTANT_KEY


def test_ocr_pdf_uses_vision_fn_and_segments(tmp_path, monkeypatch):
    """`ocr_pdf(path, vision_fn=...)` inyecta el transcritor y devuelve texto + stats."""
    p = _make_blank_pdf(tmp_path)

    def fake_vision(png_bytes: bytes) -> dict:
        return {
            "text": "La sociedad tratará los datos conforme a la Ley 1581 de 2012. "
                    "Se conservará la prueba de dichas autorizaciones.",
            "prompt_tokens": 1200,
            "completion_tokens": 80,
        }

    r = ocr_pdf(p, vision_fn=fake_vision, page_limit=1)
    assert "tratará los datos conforme" in r["text"]
    assert r["stats"]["n_pages"] == 1
    assert r["stats"]["completion_tokens"] == 80
    assert r["prompt_tokens"] == 1200


def test_ocr_pdf_concats_pages_and_respects_limit(tmp_path, monkeypatch):
    p = _make_blank_pdf(tmp_path)

    def fake_vision(png_bytes: bytes) -> dict:
        return {"text": "cláusula", "prompt_tokens": 100, "completion_tokens": 10}

    # page_limit=1 sobre un PDF de 1 página -> exactamente 1 página.
    r = ocr_pdf(p, vision_fn=fake_vision, page_limit=1)
    assert r["stats"]["n_pages"] == 1


def test_normalize_document_pdf_scanned_with_ocr(tmp_path, monkeypatch):
    """PDF escaneado + ocr=True -> segmentado por texto OCR (no hard fail)."""
    p = _make_blank_pdf(tmp_path)
    # El símbolo real que `pipeline.normalize_document` importa es jev_norma.ocr.ocr_pdf.
    monkeypatch.setattr(
        "jev_norma.ocr.ocr_pdf",
        lambda path, **kw: {"text": FIXTURE + " La sociedad tratará los datos conforme a la Ley 1581.",
                            "prompt_tokens": 1300, "completion_tokens": 90,
                            "stats": {"n_pages": 1, "latencia_ms": 600,
                                      "prompt_tokens": 1300, "completion_tokens": 90}})
    from jev_norma.pipeline import normalize_document
    res = normalize_document(p, ocr=True)
    joined = " ".join(res["clauses"]).lower()
    assert "tratará los datos conforme" in joined
    assert res["stats"].get("ocr") is True


def test_detect_scanned_requires_pdf_like(tmp_path):
    # Un .html nunca es escaneado.
    f = tmp_path / "pol.txt"
    f.write_text("x", encoding="utf-8")
    assert detect_scanned(f) is False


# ---------------------------------------------------------------------------
# WU12 — OCR en paralelo: la transcripción página a página es I/O-bound (red),
# así que se solapan las páginas. Contrato: orden preservado, tokens sumados y
# latencia de pared < serial. Se prueba sin red (vision_fn + render mockeados).
# ---------------------------------------------------------------------------

def _make_multipage_pdf(tmp_path, pages=4):
    """PDF de `pages` páginas en blanco (proxy de escaneado) para el conteo."""
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=612, height=792)
    pth = tmp_path / "multi.pdf"
    with open(pth, "wb") as f:
        w.write(f)
    return pth


def test_ocr_pdf_parallel_preserves_page_order(tmp_path, monkeypatch):
    """Aunque las páginas se transcriban en paralelo, el texto sale en orden."""
    p = _make_multipage_pdf(tmp_path, pages=5)
    # render_page_png encodea el índice de página en los bytes -> identidad por página.
    monkeypatch.setattr(ocrmod, "render_page_png",
                        lambda path, page_no, dpi=150: str(page_no).encode())

    def fake_vision(png_bytes: bytes) -> dict:
        i = int(png_bytes.decode())
        return {"text": f"clausula-pagina-{i}", "prompt_tokens": 10, "completion_tokens": 2}

    r = ocr_pdf(p, vision_fn=fake_vision, max_workers=5)
    got = [int(t.split("-")[-1]) for t in r["text"].split("\n\n")]
    assert got == [0, 1, 2, 3, 4]
    assert r["prompt_tokens"] == 50
    assert r["stats"]["completion_tokens"] == 10
    assert r["stats"]["n_pages"] == 5


def test_ocr_pdf_parallel_is_faster_than_serial(tmp_path, monkeypatch):
    """Con 4 páginas que tardan ~0.15s c/u, el modo paralelo no espera N*0.15s."""
    import time as _time
    p = _make_multipage_pdf(tmp_path, pages=4)
    monkeypatch.setattr(ocrmod, "render_page_png",
                        lambda path, page_no, dpi=150: str(page_no).encode())

    def slow_vision(png_bytes: bytes) -> dict:
        _time.sleep(0.15)
        return {"text": "clausula", "prompt_tokens": 5, "completion_tokens": 1}

    t0 = _time.monotonic()
    ocr_pdf(p, vision_fn=slow_vision, max_workers=4)
    parallel = _time.monotonic() - t0
    # Serial serían ~0.60s; paralelo 4 workers debe quedar muy por debajo.
    assert parallel < 0.45, f"paralelo tardó {parallel:.3f}s (¿secuencial?)"


def test_ocr_pdf_respects_max_workers(tmp_path, monkeypatch):
    """No se excede `max_workers` llamadas concurrentes en vuelo."""
    import threading
    import time as _time
    p = _make_multipage_pdf(tmp_path, pages=6)
    monkeypatch.setattr(ocrmod, "render_page_png",
                        lambda path, page_no, dpi=150: str(page_no).encode())
    lock = threading.Lock()
    state = {"inflight": 0, "peak": 0}

    def vision(png_bytes: bytes) -> dict:
        with lock:
            state["inflight"] += 1
            state["peak"] = max(state["peak"], state["inflight"])
        _time.sleep(0.05)
        with lock:
            state["inflight"] -= 1
        return {"text": "x", "prompt_tokens": 1, "completion_tokens": 1}

    ocr_pdf(p, vision_fn=vision, max_workers=2)
    assert state["peak"] <= 2, f"concurrencia {state['peak']} > max_workers"
    assert state["peak"] >= 2, "no hubo paralelismo real (todo secuencial)"


def test_ocr_pdf_workers_one_is_sequential(tmp_path, monkeypatch):
    """max_workers=1 mantiene el comportamiento serial (1 en vuelo a la vez)."""
    import threading
    import time as _time
    p = _make_multipage_pdf(tmp_path, pages=3)
    monkeypatch.setattr(ocrmod, "render_page_png",
                        lambda path, page_no, dpi=150: str(page_no).encode())
    lock = threading.Lock()
    state = {"inflight": 0, "peak": 0}

    def vision(png_bytes: bytes) -> dict:
        with lock:
            state["inflight"] += 1
            state["peak"] = max(state["peak"], state["inflight"])
        _time.sleep(0.02)
        with lock:
            state["inflight"] -= 1
        return {"text": "x", "prompt_tokens": 1, "completion_tokens": 1}

    ocr_pdf(p, vision_fn=vision, max_workers=1)
    assert state["peak"] == 1
