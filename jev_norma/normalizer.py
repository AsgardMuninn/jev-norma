"""jev_norma.normalizer — normalización de cláusulas 0-IA (sin palabras, stdlib puro).

Convierte un documento legal (HTML crudo de cualquier stack, o texto plano/PDF extraído)
en una lista de cláusulas gatieables por Jev. TODO el filtrado es heurístico y
determinista: no llama a ningún modelo.

Estrategias (verificadas en el proyecto jev-norma-triage, WU3/WU4):
  * HTML: extracción POSICIONAL de la región de contenido (<main> > <article> > <body>
    tras quitar chrome nav/header/footer/aside), <h1..h6> y líneas cortas en mayúsculas
    como cabeceras de sección (boundaries no-cláusula), merge de <p> con sus <li> hijos,
    descarte de boilerplate (NIT/dirección/www/©/email/logo).
  * Texto plano (PDF con capa de texto vía pypdf en el pipeline): segment_plain_text
    re-empaqueta oraciones en cláusulas de hasta MAX_CLAUSE_CHARS (independiente de cómo
    el extractor repartió las líneas).
"""

from __future__ import annotations

import html as _html
import re
import time

HEADER_PAT = re.compile(
    r"^(?:princi|autori|finalidad|clientes|datos sensibles|datos de ninyos|atencion|consultas|reclamos|"
    r"supresion|revocatoria|canales|transferencia|relacionamiento|cookies|politicas|vigencia|seguridad|"
    r"propiedad|definiciones|tratamiento)",
    re.IGNORECASE,
)
BOILERPLATE_PAT = re.compile(
    r"^(nit:|carrera |www\.|@|\d{1,2}\s|\u200d|línea de atención|chat de atención|facebook:|x: @|instagram:)",
    re.IGNORECASE,
)

MAX_CLAUSE_CHARS = 1600  # presupuesto por cláusula (~440 tokens) antes de split por oración
CONTACT_MAX_CHARS = 220

NAV_TAGS = re.compile(
    r"<(nav|header|footer|aside|form|svg|figure|noscript|script|style)[^>]*>.*?</\1>",
    re.S | re.I,
)
BLOCK_RE = re.compile(r"<(h[1-6]|p|li)[^>]*>(.*?)</\1>", re.S | re.I)

# Firma de contacto/dirección en CUALQUIER posición (no solo prefijo): la línea se
# descarta si es corta y contiene NIT / email / www / © / dirección.
CONTACT_ANYWHERE = re.compile(
    r"(?:\bNIT\s*[:.]\s*\d|[\w.+-]+@[\w.-]+\.(?:co|com|org|net)|\bwww\.|©|"
    r"\bCarrera\s+\d+|Cll\s+\d+|Av\.?\s+\d+|Cra\.?\s+\d+|Bogot[aá]\s*D\.?C\.?)",
    re.I,
)

_SENT_SPLIT = re.compile(r"(?<=[.;])\s+(?=[A-ZÁÉÍÓÚÑ0-9¿¡\"'«])")


def _strip_html(txt: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = _html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def looks_like_header(txt: str) -> bool:
    """Cabecera de sección = línea corta (<= 70) y mayoritariamente mayúsculas, o patrón estándar."""
    if not txt or len(txt) > 70:
        return False
    upper = sum(c.isupper() for c in txt if c.isalpha())
    alpha = sum(c.isalpha() for c in txt)
    if alpha and upper / alpha >= 0.6:
        return True
    return bool(HEADER_PAT.match(txt))


def extract_content(raw_html: str) -> str:
    """Extrae la región de CONTENIDO del documento (posición), no una clase particular.

    Preferencia: <main> > <article> > <body> (tras quitar chrome nav/header/footer/aside).
    """
    raw = re.sub(r"<!--.*?-->", " ", raw_html, flags=re.S)
    raw = NAV_TAGS.sub(" ", raw)
    m = re.search(r"<main[^>]*>(.*?)</main>", raw, re.S | re.I)
    if not m:
        m = re.search(r"<article[^>]*>(.*?)</article>", raw, re.S | re.I)
    if not m:
        m = re.search(r"<body[^>]*>(.*?)</body>", raw, re.S | re.I)
    return m.group(1) if m else raw


def _is_boilerplate(txt: str) -> bool:
    if BOILERPLATE_PAT.match(txt):
        return True
    return len(txt) <= CONTACT_MAX_CHARS and bool(CONTACT_ANYWHERE.search(txt))


def _split_long(c: str, max_chars: int = MAX_CLAUSE_CHARS):
    buf = ""
    for s in re.split(r"(?<=[.;:])\s+", c):
        if buf and len(buf) + len(s) > max_chars:
            yield buf
            buf = s
        else:
            buf = " ".join(x for x in (buf, s) if x)
    if buf:
        yield buf


def normalize_clauses_general(raw_html: str) -> dict:
    """HTML crudo (cualquier stack) -> cláusulas gatieables (sin IA).

    IDs de cláusula no se generan aquí (el gate los asigna). Retorna
    {clauses, headers, stats{latencia_ms, n_blocks, n_clauses_raw, n_clauses_final}}.
    """
    t0 = time.monotonic()
    content = extract_content(raw_html)
    blocks: list[tuple[str, str]] = []
    for m in BLOCK_RE.finditer(content):
        tag, inner = m.group(1), m.group(2)
        txt = _strip_html(inner)
        if not txt or _is_boilerplate(txt):
            continue
        blocks.append(("h" if tag.startswith("h") else tag, txt))

    headers: list[str] = []
    body: list[tuple[str, str]] = []
    for typ, txt in blocks:
        if typ == "h" or (typ == "p" and looks_like_header(txt)):
            headers.append(txt)
        else:
            body.append((typ, txt))

    clauses: list[str] = []
    i = 0
    while i < len(body):
        tag, txt = body[i]
        if tag == "li":
            clauses.append(txt)  # li huérfano = cláusula propia
            i += 1
            continue
        clause = txt
        k = i + 1
        while k < len(body) and body[k][0] == "li":
            clause += " " + body[k][1]
            k += 1
        clauses.append(clause)
        i = k

    final = [c for cc in clauses for c in _split_long(cc)]
    return {
        "clauses": [c for c in final if c],
        "headers": headers,
        "stats": {
            "latencia_ms": int((time.monotonic() - t0) * 1000),
            "n_blocks": len(blocks),
            "n_clauses_raw": len(clauses),
            "n_clauses_final": len([c for c in final if c]),
        },
    }


def segment_plain_text(text: str, max_chars: int = MAX_CLAUSE_CHARS) -> dict:
    """Texto plano (PDF extraído) -> cláusulas gatieables (sin IA).

    pypdf/texto legal extraído casi nunca conserva párrafos separados; se normaliza el flujo
    a espacios, se divide en oraciones sobre párrafos/saltos/puntuación (. ; :) y se
    re-empaquetan oraciones cortas en cláusulas de hasta `max_chars`. Cabeceras de sección
    (línea corta en mayúsculas) son boundaries no-cláusula. Descarta boilerplate corto.
    """
    t0 = time.monotonic()
    raw_chunks = re.split(r"\n\s*\n", text)
    clauses: list[str] = []
    for ch in raw_chunks:
        joined = re.sub(r"\s+", " ", ch).strip()
        if not joined:
            continue
        parts = _SENT_SPLIT.split(joined)
        buf = ""
        for s in parts:
            raw_s = s.strip()
            if not raw_s:
                continue
            if BOILERPLATE_PAT.match(raw_s) or (len(raw_s) <= CONTACT_MAX_CHARS and CONTACT_ANYWHERE.search(raw_s)):
                continue
            if looks_like_header(raw_s):
                if buf:
                    clauses.append(buf)
                    buf = ""
                continue
            if not buf:
                buf = raw_s
            elif len(buf) + len(raw_s) + 1 <= max_chars:
                buf = buf + " " + raw_s
            else:
                clauses.append(buf)
                buf = raw_s
        if buf:
            clauses.append(buf)

    return {
        "clauses": [c for c in clauses if c],
        "stats": {
            "latencia_ms": int((time.monotonic() - t0) * 1000),
            "n_parrafos": len(raw_chunks),
            "n_clauses_final": len([c for c in clauses if c]),
        },
    }