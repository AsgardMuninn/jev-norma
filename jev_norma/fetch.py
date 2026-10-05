"""jev_norma.fetch — fetch de documentos legales con resistencia a WAF/bot-protection.

Razón de ser (hallazgo WU4, 2026-10-01): el crawl directo con urllib pelado a
políticas de tratamiento colombianas (bancos, SIC, superintendencias) responde
403/000 por WAF (Akamai, etc.). Este módulo trae el fetch a nivel de producto:

  * valida esquema http(s)
  * usa User-Agent de navegador real (no urllib default, que los WAF cortan)
  * rota entre varios navegadores ante 403/5xx (cada UA distinto token)
  * reintenta errores transitorios (timeout, reset) con backoff corto
  * declina de inmediato y con mensaje claro en 404 (no es WAF, es que no existe)

API: fetch_html(url, timeout=15) -> str  (el HTML completo en UTF-8)
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

TIMEOUT_S = 15
MAX_ATTEMPTS = 3  # intentos por UA (cubre ~1-2 fallos transitorios)

# Rotación de navegadores: distintos tokens evitan fingerprinting de egress/UA.
BUILTIN_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
USER_AGENTS = (
    BUILTIN_UA,
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:115.0) Gecko/20100101 Firefox/115.0",
)

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.5",
    "Accept-Encoding": "identity",  # no pedir gzip; el normalizador espera HTML
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def fetch_html(url: str, timeout: int = TIMEOUT_S) -> str:
    """Descarga {url} y devuelve el HTML como str. Lanza excepción útil si falla."""
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"URL inválida (solo http/https): {url!r}")

    last_err: Exception | None = None
    # 404/410 -> declinar al instante (no es WAF, es ausencia de recurso)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        ua = USER_AGENTS[(attempt - 1) % len(USER_AGENTS)]
        req = urllib.request.Request(url, headers={**HEADERS, "User-Agent": ua})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise RuntimeError(f"recurso no existe ({e.code}): {url}") from e
            # 403/429/5xx -> reintentar con otro UA (backoff leve)
            last_err = e
            time.sleep(0.4 * attempt)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            time.sleep(0.4 * attempt)

    raise ConnectionError(f"no se pudo descargar {url} tras {MAX_ATTEMPTS} intentos: {last_err}")


__all__ = ["fetch_html", "BUILTIN_UA", "TIMEOUT_S", "MAX_ATTEMPTS"]