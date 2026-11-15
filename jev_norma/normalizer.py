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

# Separador de oraciones que NO consume el espacio: cada fragmento conserva su sangría, y
# esa sangría (con el sentinel de línea) es la que dice si ocupa su línea completa
# (WU22b-fix3).
_SENT_SPLIT_LEAD = re.compile(r"(?<=[.;])(?=\s+[A-ZÁÉÍÓÚÑ0-9¿¡\"'«])")

# WU18: cabeceras de página numéricas de extracciones PDF ("Página 3 de 11",
# "Página 4 de 11") se cuelan como cláusulas (vistas en WU17, Logistral d00/d24).
# Sin anclas: se eliminan también cuando el extractor las incrusta a mitad de párrafo.
PAGE_HEADER_PAT = re.compile(r"\bp[aá]gina\s+\d+\s*(?:de|sl\.?|/)\s*\d*\b", re.IGNORECASE)

# WU22b-fix: el extractor PDF deja la NUMERACIÓN sola como "cláusula" ("1.", "2.4.", "7.2.").
# WU22a midió 8 de 42 en la política Logistral: se pagaban tokens al gate por nada y ensuciaban
# el reporte. No es contenido legal: es un boundary de sección (cierra la cláusula en curso).
NUMERACION_PAT = re.compile(r"^\(?\d{1,3}(?:[.)]\d{1,3})*[.)]?\)?$")
NUMERACION_MAX_CHARS = 15

# WU22b-fix2: cuando la numeración va PEGADA al final del párrafo ("...siguientes
# finalidades: 7.1. Autorización..."), el separador de oraciones no la aísla y el token queda
# como sufijo de la cláusula: viaja al gate como ruido y aparece en el reporte. Se recorta
# SÓLO si es un marcador de sección (1-2 dígitos por grupo, punto final) y no una referencia
# legal ni una fecha.
SUFIJO_NUMERACION_PAT = re.compile(r"\s+(\d{1,2}(?:\.\d{1,2}){0,2})\.$")
REFERENCIA_LEGAL_PAT = re.compile(
    r"\b(?:art[íi]culo|art\.|numeral|literal|[íi]tem|punto|secci[óo]n|cap[íi]tulo|anexo|ley|"
    r"decreto|resoluci[óo]n|circular|ordinal|par[áa]grafo|cl[áa]usula|inciso|libro|t[íi]tulo|"
    r"p[áa]gina)\s*$",
    re.IGNORECASE,
)


def strip_trailing_numbering(txt: str) -> str:
    """Quita un marcador de sección pegado al final de la cláusula ("... finalidades: 7.1.").

    No toca referencias legales ("artículo 6.", "numeral 4.2.") ni fechas ("20 de agosto de
    2026."): el marcador es 1-2 dígitos por grupo y 1-3 grupos, con punto final.
    """
    s = txt.rstrip()
    m = SUFIJO_NUMERACION_PAT.search(s)
    if not m:
        return txt
    head = s[: m.start()].rstrip()
    if not head or REFERENCIA_LEGAL_PAT.search(head):
        return txt
    return head


def is_numbering_fragment(txt: str) -> bool:
    """True si el texto es SOLO un número de enumeración/binario ("1.", "2.4.", "3)", "(7.2)").

    Exige que no haya ninguna letra: "Artículo 5." o "Ley 1581 de 2012" son cláusulas reales.
    """
    s = txt.strip()
    if not s or len(s) > NUMERACION_MAX_CHARS or any(c.isalpha() for c in s):
        return False
    return bool(NUMERACION_PAT.match(s))


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


def _mayusculas_predominantes(txt: str) -> bool:
    alpha = sum(c.isalpha() for c in txt)
    upper = sum(c.isupper() for c in txt if c.isalpha())
    return bool(alpha) and upper / alpha >= 0.6


_CIERRE_ORACION = (".", ":", ";", "!", "?")

def _cierra_oracion(txt: str) -> bool:
    """¿El fragmento termina una oración? (ignora comillas/paréntesis de cierre)."""
    t = txt.rstrip().rstrip("\"'\u201d\u00bb)]").rstrip()
    return t.endswith(_CIERRE_ORACION)


def _cuerpo_gatieable(cuerpo: str, ocupa_linea: bool) -> str:
    """Texto plano: decide si el fragmento es cabecera (boundary) o contenido.

    `cuerpo` conserva los saltos de línea del extractor. `ocupa_linea` dice si el fragmento
    ocupa su línea completa (nada antes ni después en la misma línea).

    Regla (WU22b-fix3): lo único que se puede borrar es lo que ya borraba
    `looks_like_header` sobre el texto UNIDO. Si el fragmento trae cuerpo (saltos de línea
    internos, p.ej. "INTRODUCCIÓN \\nLOGISTRAL S.A.S., identificada con NIT. 900.161.726-3"),
    se recortan SÓLO las líneas de cabecera iniciales y el cuerpo se queda: antes se perdía
    entero y la cláusula quedaba sin sujeto ("900.161.726-3, empresa colombiana ...").
    Si no hay cuerpo recuperable (el resto es otra cabecera, numeración o un marcador de
    lista "a.") se mantiene el comportamiento previo: boundary.
    """
    plano = re.sub(r"\s+", " ", cuerpo).strip()
    if is_numbering_fragment(plano):
        return ""  # numeración sola: nunca es contenido legal
    if not looks_like_header(plano):
        return plano
    if "\n" not in cuerpo:
        # Sin cuerpo pegado: cabecera real si ocupa su línea; si no, es cola de oración.
        # Una prosa que sólo matchea HEADER_PAT (minúsculas + cierra oración) se queda.
        return "" if ocupa_linea else plano
    resto = cuerpo
    while "\n" in resto:
        cabeza, resto = resto.split("\n", 1)
        cabeza, resto = cabeza.strip(), resto.strip()
        if not cabeza:
            continue
        if not (_mayusculas_predominantes(cabeza) and looks_like_header(cabeza)):
            # La cabecera inicial no está completa (el extractor partió el título): no se
            # puede separar sin arriesgar contenido -> se conserva el fragmento entero.
            return plano
    if not resto or (looks_like_header(resto) and _mayusculas_predominantes(resto)):
        return ""
    if len(resto.split()) < 3:  # "a." / "3.1." / restos sin prosa: nada que recuperar
        return ""
    return re.sub(r"\s+", " ", resto).strip()


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

    El texto se re-empaqueta en cláusulas de hasta `max_chars` sobre párrafos/saltos y
    puntuación (. ; :). Desde WU22b-fix3 se conserva la POSICIÓN de los saltos de línea:
    sin esa información la cola de una oración partida por el extractor era indistinguible
    de una cabecera de sección y se descartaba contenido legal (ver `_es_cabecera`).
    Descarta boilerplate corto; la numeración suelta sigue siendo boundary.
    """
    t0 = time.monotonic()
    raw_chunks = re.split(r"\n\s*\n", text)
    clauses: list[str] = []
    for ch in raw_chunks:
        # WU18: quitar cabeceras de página numéricas ("Página 3 de 11"), tanto como
        # línea propia como incrustadas a mitad de párrafo por el extractor PDF.
        ch = PAGE_HEADER_PAT.sub(" ", ch)
        # Sentinel de línea: los espacios se colapsan, los saltos de línea NO.
        ch = re.sub(r"[^\S\n]*\n[^\S\n]*", "\n", ch)
        joined = re.sub(r"[^\S\n]+", " ", ch).strip()
        if not joined:
            continue
        parts = _SENT_SPLIT_LEAD.split(joined)
        buf = ""
        previa_numeracion = False
        for i, part in enumerate(parts):
            cuerpo = part.strip()
            if not cuerpo:
                continue
            lead = part[: len(part) - len(part.lstrip())]
            sig_lead = ""
            if i + 1 < len(parts):
                nxt = parts[i + 1]
                sig_lead = nxt[: len(nxt) - len(nxt.lstrip())]
            sin_salto = "\n" not in cuerpo
            abre_linea = i == 0 or "\n" in lead or previa_numeracion
            cierra_linea = i + 1 == len(parts) or "\n" in sig_lead
            ocupa_linea = sin_salto and abre_linea and cierra_linea
            plano = re.sub(r"\s+", " ", cuerpo).strip()
            previa_numeracion = is_numbering_fragment(plano)
            if BOILERPLATE_PAT.match(plano) or (
                len(plano) <= CONTACT_MAX_CHARS and CONTACT_ANYWHERE.search(plano)
            ):
                continue
            contenido = _cuerpo_gatieable(cuerpo, ocupa_linea)
            if not contenido:
                if buf:
                    clauses.append(strip_trailing_numbering(buf))
                    buf = ""
                continue
            if not buf:
                buf = contenido
            elif len(buf) + len(contenido) + 1 <= max_chars:
                buf = buf + " " + contenido
            else:
                clauses.append(strip_trailing_numbering(buf))
                buf = contenido
        if buf:
            clauses.append(strip_trailing_numbering(buf))

    return {
        "clauses": [c for c in clauses if c],
        "stats": {
            "latencia_ms": int((time.monotonic() - t0) * 1000),
            "n_parrafos": len(raw_chunks),
            "n_clauses_final": len([c for c in clauses if c]),
        },
    }