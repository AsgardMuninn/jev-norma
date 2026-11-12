"""Paquete ciego de anotación: separa el TEXTO de la etiqueta para medir acuerdo.

El problema que resuelve: un gold etiquetado por una sola persona no prueba que la
rúbrica sea reproducible. Para medir κ de Cohen hace falta que un SEGUNDO anotador
etiquete las mismas cláusulas sin ver la etiqueta original ni las predicciones del
modelo. Este módulo construye ese paquete, con lista blanca de campos, y la guía
que el anotador recibe.

Puro: lee un JSONL y devuelve datos. No llama a la API, no imprime nada.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

# Lo ÚNICO que ve el anotador. Cualquier otro campo se descarta.
BLIND_FIELDS = ("id", "source", "text")

# Campos que jamás pueden viajar en el paquete: etiqueta humana o decisión del modelo.
FORBIDDEN_FIELDS = frozenset({
    "bucket", "severity", "rationale", "decided", "decidido", "decision",
    "pred_bucket", "pred_severity", "prediction", "prediccion",
    "confidence", "confianza", "severidad_confianza", "nivel_severidad",
    "gate_humano", "probabilities",
})


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def blind_rows(rows: list[dict]) -> list[dict]:
    """Devuelve sólo {id, source, text}. Falla si falta texto o si un id se repite."""
    out = []
    seen = set()
    for r in rows:
        rid = r.get("id")
        if rid is None or str(rid) == "":
            raise ValueError("fila sin id")
        if rid in seen:
            raise ValueError("id duplicado: %r" % (rid,))
        text = r.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("fila %r sin texto" % (rid,))
        seen.add(rid)
        out.append({"id": rid, "source": r.get("source") or "?", "text": text})
    if not out:
        raise ValueError("gold vacío: no hay nada que anotar")
    return out


def build_packet(path: Path) -> tuple[list[dict], dict]:
    """Construye el paquete ciego y su manifiesto (n, fuentes, sha256, fecha, origen)."""
    raw = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
           if line.strip()]
    packet = blind_rows(raw)
    meta = {
        "origen": str(path),
        "n": len(packet),
        "fuentes": dict(sorted(Counter(r["source"] for r in packet).items())),
        "sha256": sha256_file(path),
        "fecha": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rubrica": "v1 (WU20, sin cambios)",
    }
    return packet, meta


def packet_csv(packet: list[dict]) -> str:
    """Versión CSV del paquete (para hojas de cálculo), con columnas de respuesta VACÍAS.

    Se usa `csv` del stdlib y quoting mínimo; el texto puede traer comas y saltos de
    línea. `bucket` y `severity` van vacíos: son para que el anotador los llene.
    """
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "source", "text", "bucket", "severity", "nota"])
    for r in packet:
        w.writerow([r["id"], r["source"], r["text"], "", "", ""])
    return buf.getvalue()


def guide_markdown(meta: dict) -> str:
    """Guía autocontenida para el segundo anotador. No revela etiquetas ni predicciones."""
    fuentes = "\n".join("- `%s`: %d cláusulas" % (src, n) for src, n in meta["fuentes"].items())
    return """# Guía de anotación ciega, jev-norma

Paquete generado: {fecha}
Origen: `{origen}` (sha256 `{sha}`)
Cláusulas: {n}
Fuentes:
{fuentes}

## Qué tienes que hacer

Etiquetar las {n} cláusulas del paquete. Hay dos formatos con el mismo contenido:
`packet_blind.csv` (recomendado: se abre en Excel/Sheets) y `packet_blind.jsonl`.
En el CSV llena las columnas `bucket` y `severity` (y `nota` si quieres); en el JSONL
añade esos mismos campos a cada línea. Devuelve el archivo completo, sin borrar filas.

Reglas:

1. Trabaja sólo con el texto. **No abrir el gold** (`gold_multi.jsonl`, `gold.jsonl`,
   `gold_logistral.jsonl`) ni ningún reporte de evaluación: si ves la etiqueta original
   o la predicción del modelo, el acuerdo deja de medir la rúbrica.
2. Decide siempre una de las tres etiquetas. Si dudas, elige la que más se acerque y
   anótalo en `nota`. No dejes celdas vacías.
3. No consultes a la persona que etiquetó el gold. La independencia es el punto.
4. No hace falta conocimiento legal profundo: aplica la rúbrica tal como está. Si la
   rúbrica no alcanza para decidir, esa es información valiosa, anótala.

## Rúbrica v1 (la misma del gold, sin cambios)

- `obligacion`: la cláusula impone al responsable o encargado un deber concreto y
  exigible (obtener autorización, atender derechos, conservar prueba, aplicar medidas
  de seguridad, avisar incidentes).
- `riesgo`: la cláusula describe una práctica que puede lesionar al titular o exponer a
  sanción si no se controla (transferencia a terceros, recolección amplia, videovigilancia,
  retención sin plazo, deslinde de responsabilidad, uso secundario de datos).
- `sin_accion`: información, definición, vigencia, marco normativo, contacto o
  descripción del documento, sin deber ni riesgo operativo.

Fronteras conocidas que la rúbrica v1 todavía no resuelve (decide con tu criterio y
anota la duda, no intentes adivinar la respuesta "oficial"): listas de finalidades,
avisos de videovigilancia y deslindes de responsabilidad sobre terceros.

## Severidad

- `0`: sin obligación ni riesgo (informativa).
- `1`: deber genérico, informativo con carga menor.
- `2`: exigible o sancionable (autorización, derechos del titular, seguridad, transferencias).

## Formato de respuesta

Una línea JSON por cláusula, en el mismo orden:

```json
{{"id": "n000", "source": "nequi-politica-tratamiento-datos", "bucket": "sin_accion", "severity": 0, "nota": "opcional"}}
```

## Cómo se evalúa

`tools/kappa_report.py` une tu etiquetado con el gold y calcula κ de Cohen global y por
documento, más la matriz de acuerdo. El criterio pre-registrado es κ >= 0.6 para
considerar la rúbrica reproducible; por debajo, el umbral de producción no se mueve.
Si tu archivo no cubre las {n} cláusulas, el reporte lo dice y no se aprueba nada.
""" .format(fecha=meta["fecha"], origen=meta["origen"], sha=meta["sha256"], n=meta["n"],
            fuentes=fuentes)


__all__ = ["BLIND_FIELDS", "FORBIDDEN_FIELDS", "sha256_file", "blind_rows", "build_packet",
           "packet_csv", "guide_markdown"]
