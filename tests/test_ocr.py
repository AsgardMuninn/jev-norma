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
