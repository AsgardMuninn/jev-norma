"""jev_norma.ocr — OCR de PDFs legales escaneados (WU11).

El producto jev-norma triaga políticas/contratos que llegan como *imagen*
(firmados/escaneados). Aquí se resuelve esa entrada: detectar si un PDF carece de
capa de texto y, en ese caso, transcribirlo página a página con un VLM *barato y
rápido* vía OpenRouter (GLM-5.3-Flash, visión), y volver a alimentar el pipeline
0-IA + gate Jev normal (sin duplicar normalizer/gate).

Por qué GLM-5.3-Flash por OpenRouter y no un OCR local (tesseract/easyocr):
  * es la "visión de respaldo" ya sancionada en el stack de Juan (auxiliary.vision);
  * no arrastra torch ni binarios nativos al paquete (mantiene el core 0-deps);
  * calidad robusta en texto legal español (marca/signos), a centavos por página.

Este módulo es la ÚNICA pieza que depende de la red + de una key distinta
(OPENROUTER_API_KEY); `normalize_document(path, ocr=True)` decide cuándo usarla.
Ningún otro módulo toca esto.
"""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
VISION_MODEL = "z-ai/glm-5.3-flash"  # verificado en la lista de modelos de OpenRouter
EXTRACTANT_KEY = "OPENROUTER_API_KEY"
# Un PDF "digital" (con capa de texto) suele tener >= ~40 chars por página; un
# escaneado sin OCR devuelve "" o ruido mínimo. Umbral conservador de detección.
SCANNED_MIN_CHARS = 40
VISION_PROMPT = (
    "Transcribe este documento legal escaneado, página por página, EXACTAMENTE "
    "(verbatim), en español. Conserva puntuación, tildes y mayúsculas. Respeta los "
    "saltos de párrafo evidentes. No añadas comentarios, títulos ni resumen: solo el texto."
)


def extract_openrouter_key() -> str:
    """Lee OPENROUTER_API_KEY de ~/.hermes/.env o del entorno. Nunca imprime el valor."""
    v = __import__("os").environ.get(EXTRACTANT_KEY, "")
    if v:
        return v
    p = Path.home() / ".hermes" / ".env"
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{EXTRACTANT_KEY}="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    except (OSError, UnicodeDecodeError):
        pass
    return ""


def detect_scanned(path: Path, min_chars: int = SCANNED_MIN_CHARS) -> bool:
    """¿Es este PDF una imagen (escaneado sin capa de texto)?

    True si el PDF existe, tiene páginas y su capa de texto extraíble total es
    menor a `min_chars`. Un .html / .txt / inexistente nunca es escaneado.
    """
    if path.suffix.lower() != ".pdf" or not path.exists():
        return False
    try:
        from pypdf import PdfReader
    except ImportError:
        return False
    try:
        reader = PdfReader(str(path))
    except Exception:
        return False
    total = sum(len((pg.extract_text() or "")) for pg in reader.pages)
    return len(reader.pages) > 0 and total < min_chars


def render_page_png(path: Path, page_no: int, dpi: int = 150) -> bytes:
    """Renderiza una página del PDF a PNG (bytes). Requiere pymupdf (extra `[ocr]`)."""
    import fitz  # pymupdf

    doc = fitz.open(str(path))
    try:
        page = doc.load_page(page_no)
        pix = page.get_pixmap(dpi=dpi)
        return pix.tobytes("png")
    finally:
        doc.close()


def vision_transcribe(image_png: bytes, key: str, model: str = VISION_MODEL) -> dict:
    """Una página (PNG) -> texto transcrito vía OpenRouter visión.

    Retorna {"text", "prompt_tokens", "completion_tokens", "latencia_ms"}.
    Lanza RuntimeError si la key es vacía o la API responde error.
    """
    import json
    import urllib.error
    import urllib.request

    if not key:
        raise RuntimeError(f"error: {EXTRACTANT_KEY} no encontrada en ~/.hermes/.env")
    b64 = base64.b64encode(image_png).decode("ascii")
    body = json.dumps({
        "model": model,
        "max_tokens": 2048,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
    }).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_ENDPOINT, data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"error OpenRouter visión HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"error OpenRouter visión: {e.reason}")
    lat = int((time.monotonic() - t0) * 1000)
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        text = ""
    usage = data.get("usage") or {}
    return {
        "text": text,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "latencia_ms": lat,
    }


def ocr_pdf(path: Path, page_limit: int | None = None, key: str | None = None,
            vision_fn=None) -> dict:
    """PDF escaneado -> texto transcrito (página a página) + stats.

    `vision_fn` inyectable para tests sin red: vi(png_bytes) -> {text, prompt_tokens,
    completion_tokens, latencia_ms}. Por defecto usa la red real (OpenRouter visión).
    `page_limit` acota el número de páginas a transcribir (costo).
    """
    import fitz  # pymupdf (extra [ocr])

    t0 = time.monotonic()
    doc = fitz.open(str(path))
    n = doc.page_count
    doc.close()
    if page_limit is not None:
        n = min(n, page_limit)
    key = key if key is not None else extract_openrouter_key()
    vision = vision_fn or (lambda png: vision_transcribe(png, key))

    chunks: list[str] = []
    prompt_tok = completion_tok = 0
    lat = 0
    for i in range(n):
        png = render_page_png(path, i)
        r = vision(png)
        chunks.append(r.get("text", "") or "")
        prompt_tok += int(r.get("prompt_tokens") or 0)
        completion_tok += int(r.get("completion_tokens") or 0)
        lat += int(r.get("latencia_ms") or 0)
    return {
        "text": "\n\n".join(chunks).strip(),
        "prompt_tokens": prompt_tok,
        "completion_tokens": completion_tok,
        "stats": {
            "n_pages": n,
            "latencia_ms": int((time.monotonic() - t0) * 1000),
            "prompt_tokens": prompt_tok,
            "completion_tokens": completion_tok,
        },
    }


__all__ = [
    "OPENROUTER_ENDPOINT", "VISION_MODEL", "EXTRACTANT_KEY",
    "extract_openrouter_key", "detect_scanned", "render_page_png",
    "vision_transcribe", "ocr_pdf",
]
