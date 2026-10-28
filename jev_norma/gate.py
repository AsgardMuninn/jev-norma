"""jev_norma.gate — cliente del gate System One (Jev) + construcción de preguntas.

El núcleo del producto son DECISIONES TIPADAS de Jev (choice + score + confidence calibrada),
no generar texto. Cada cláusula se embebe en la INSTRUCCIÓN de sus preguntas
(self-contained) — regla de state engineering verificada empíricamente (WU1-WU4):
compartir el inventario solo en `state` degrada a todo-`keep` con confianza alta
("confidently wrong"); embebido en la instrucción da precisión alta.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
TIMEOUT_S = 20

# Buckets normativos (Ley 1581/2012 Colombia, protección de datos).
BUCKETS = ("obligacion", "riesgo", "sin_accion")
SEVERITY_LEVELS = (
    "sin obligación normativa concreta",
    "informativa o de impacto menor, sin consecuencia sancionatoria directa",
    "exigible o sancionable, con impacto material para el responsable o el titular",
)


def build_questions(clauses: dict) -> dict:
    """clauses: {cid: texto_cláusula} -> preguntas choice + score self-contained."""
    qs = {}
    for cid in clauses:
        clause = clauses[cid]
        qs[cid] = {
            "type": "choice",
            "instructions": (
                "Triaje normativo (Ley 1581/2012 Colombia, protección de datos). Evalúa esta "
                f"cláusula de una política de tratamiento de datos personales. Cláusula [{cid}]: "
                f"\"{clause}\". Clasifica en: obligacion (impone una obligación legal concreta "
                "que el responsable debe cumplir u ofrecer al titular: obtener autorización previa "
                "expresa e informada, conservar prueba de la autorización, informar finalidades, "
                "deberes de información/notificación, ejercicio de derechos ARCO), riesgo "
                "(riesgo de incumplimiento, cláusula abusiva o recolección invasiva: modificación "
                "unilateral sin aviso, amplitud excesiva en la recolección de datos del "
                "dispositivo/contactos, decisiones automatizadas con efectos jurídicos), o "
                "sin_accion (cláusula inocua o boilerplate que no exige acción normativa: "
                "alcance/destinatarios, vigencia, enumeración de normas, excepción legal al "
                "ejercicio de derechos)."
            ),
            "criteria": {
                "obligacion": "impone una obligación legal concreta o un deber de información/ofrecimiento al titular",
                "riesgo": "riesgo de incumplimiento, cláusula abusiva o recolección invasiva sin garantía suficiente",
                "sin_accion": "inocua, boilerplate, de alcance/vigencia/normativa, no exige acción normativa",
            },
        }
        qs[f"severity__{cid}"] = {
            "type": "score",
            "instructions": (
                "Evalúa la severidad normativa de esta cláusula de una política de tratamiento "
                f"de datos personales bajo Ley 1581/2012 de Colombia. Cláusula [{cid}]: \"{clause}\". "
                "Valora la gravedad/consecuencia de la obligación o riesgo descrito, no la calidad "
                "de redacción ni el nivel de confianza de la clasificación."
            ),
            "criteria": list(SEVERITY_LEVELS),
        }
    return qs


def split_chunks(d: dict, n: int):
    """Divide un dict en chunks de hasta n ítems (para respetar contexto ~32k y timeout)."""
    items = list(d.items())
    for i in range(0, len(items), n):
        yield dict(items[i:i + n])


def load_key() -> str:
    """Lee TYPESAFE_API_KEY de ~/.hermes/.env o del entorno. Nunca imprime el valor."""
    p = Path.home() / ".hermes" / ".env"
    try:
        for line in p.read_text().splitlines():
            if line.strip().startswith("TYPESAFE_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    except FileNotFoundError:
        pass
    return os.environ.get("TYPESAFE_API_KEY", "")


def call(key: str, questions: dict) -> dict:
    """Una request a Jev (System One). Devuelve {latencia_ms, in_tokens, out_tokens, answers}."""
    body = json.dumps({
        "model": MODEL,
        "state": {"input": "Política de tratamiento de datos personales en español (Ley 1581 de 2012).",
                  "output": "Bucket normativo y severidad ordinal por cláusula."},
        "questions": questions,
    }).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        resp = json.loads(r.read().decode())
    ms = int((time.monotonic() - t0) * 1000)
    usage = resp.get("usage", {})
    out = {}
    for eid, a in resp.get("answers", {}).items():
        if eid.startswith("severity__"):
            cid = eid.removeprefix("severity__")
            out.setdefault(cid, {}).update({
                "severidad": a.get("score"),
                "severidad_confianza": round(a.get("confidence", 0), 3),
                "severidad_prob": a.get("probabilities"),
            })
        else:
            out.setdefault(eid, {}).update({
                "bucket": a.get("choice"),
                "confianza": round(a.get("confidence", 0), 3),
                "prob": a.get("probabilities"),
            })
    return {"latencia_ms": ms, "in_tokens": usage.get("input_tokens", 0),
            "out_tokens": usage.get("output_tokens", 0), "answers": out}


def sanitize_clause(c: str, n: int = 80) -> str:
    return (c[:n] + "…") if len(c) > n else c