"""jev_norma.metrics — métricas de accuracy y calibración sobre un set etiquetado.

Módulo PURO: recibe filas ya decididas (gold + predicción) y devuelve agregados.
No llama a Jev, no lee red ni disco. Es la pieza que permite afirmar (o refutar)
que el gate es preciso y que su confianza está calibrada, en vez de suponerlo.

Fila esperada (dict):
    gold_bucket, pred_bucket        -> "obligacion" | "riesgo" | "sin_accion"
    gold_severity, pred_severity    -> 0 | 1 | 2
    confidence                      -> confianza de la decisión `choice`
    severity_confidence             -> confianza del eje `score`
    gate_humano                     -> bool (True = enviada a revisión humana)
"""

from __future__ import annotations

LABELS = ("obligacion", "riesgo", "sin_accion")
SEVERITIES = (0, 1, 2)
SEVERITY_NAMES = ("sin_obligacion", "informativa_menor", "exigible_sancionable")
DEFAULT_SWEEP = (0.0, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0)


def _div(a: float, b: float):
    return None if b == 0 else a / b


def accuracy(rows: list[dict], gold: str = "gold_bucket", pred: str = "pred_bucket"):
    """Aciertos / total. None si no hay filas (no se inventa un 0/0)."""
    if not rows:
        return None
    return sum(1 for r in rows if r[gold] == r[pred]) / len(rows)


def confusion(rows: list[dict], gold: str = "gold_bucket", pred: str = "pred_bucket") -> dict:
    """Matriz {gold: {pred: conteo}} con todas las celdas inicializadas en 0."""
    m = {g: {p: 0 for p in LABELS} for g in LABELS}
    for r in rows:
        g, p = r[gold], r[pred]
        if g in m and p in m[g]:
            m[g][p] += 1
    return m


def per_class(rows: list[dict], gold: str = "gold_bucket", pred: str = "pred_bucket") -> dict:
    """Precisión/recall/F1 por bucket. None donde el denominador es 0 (sin maquillaje)."""
    out = {}
    for label in LABELS:
        tp = sum(1 for r in rows if r[gold] == label and r[pred] == label)
        fp = sum(1 for r in rows if r[gold] != label and r[pred] == label)
        fn = sum(1 for r in rows if r[gold] == label and r[pred] != label)
        precision = _div(tp, tp + fp)
        recall = _div(tp, tp + fn)
        f1 = None if (precision is None or recall is None or precision + recall == 0) else (
            2 * precision * recall / (precision + recall))
        out[label] = {"tp": tp, "fp": fp, "fn": fn, "precision": precision,
                      "recall": recall, "f1": f1, "support": tp + fn}
    return out


def severity_mae(rows: list[dict]):
    """Error absoluto medio del nivel de severidad (0..2). None sin filas."""
    if not rows:
        return None
    return sum(abs(int(r["pred_severity"]) - int(r["gold_severity"])) for r in rows) / len(rows)


def severity_within_one(rows: list[dict]):
    """Fracción de niveles de severidad con error <= 1 (tolerancia de un nivel)."""
    if not rows:
        return None
    return sum(1 for r in rows if abs(int(r["pred_severity"]) - int(r["gold_severity"])) <= 1) / len(rows)


def reliability(rows: list[dict], conf: str = "confidence", gold: str = "gold_bucket",
                pred: str = "pred_bucket", n_bins: int = 5) -> list[dict]:
    """Tabla de fiabilidad por bins iguales de confianza (diagnóstico de calibración)."""
    bins = [{"lo": i / n_bins, "hi": (i + 1) / n_bins, "n": 0, "hits": 0,
             "sum_conf": 0.0} for i in range(n_bins)]
    for r in rows:
        c = float(r[conf])
        idx = min(int(c * n_bins), n_bins - 1)
        idx = max(idx, 0)
        b = bins[idx]
        b["n"] += 1
        b["hits"] += 1 if r[gold] == r[pred] else 0
        b["sum_conf"] += c
    for b in bins:
        b["avg_confidence"] = b["sum_conf"] / b["n"] if b["n"] else None
        b["accuracy"] = b["hits"] / b["n"] if b["n"] else None
        del b["sum_conf"]
        del b["hits"]
    return bins


def ece(rows: list[dict], conf: str = "confidence", gold: str = "gold_bucket",
        pred: str = "pred_bucket", n_bins: int = 5):
    """Expected Calibration Error: Σ (n_bin/N) · |accuracy_bin − confianza_media_bin|."""
    if not rows:
        return None
    total = 0.0
    for b in reliability(rows, conf=conf, gold=gold, pred=pred, n_bins=n_bins):
        if not b["n"]:
            continue
        total += (b["n"] / len(rows)) * abs(b["accuracy"] - b["avg_confidence"])
    return total


def gate_summary(rows: list[dict], threshold: float = 0.8) -> dict:
    """Cobertura del gate: cuánto se auto-resuelve y con qué accuracy, cuánto va a humano."""
    n = len(rows)
    if not n:
        return {"n": 0, "umbral": threshold, "revisadas": 0, "fraccion_revision": None,
                "auto": 0, "accuracy_auto": None, "errors_auto": 0}
    auto = [r for r in rows if not r.get("gate_humano", False)]
    revisadas = n - len(auto)
    errors_auto = sum(1 for r in auto if r["gold_bucket"] != r["pred_bucket"])
    return {
        "n": n,
        "umbral": threshold,
        "revisadas": revisadas,
        "fraccion_revision": revisadas / n,
        "auto": len(auto),
        "accuracy_auto": _div(len(auto) - errors_auto, len(auto)),
        "errors_auto": errors_auto,
    }


def coverage_curve(rows: list[dict], thresholds=None, conf: str = "confidence") -> list[dict]:
    """Curva cobertura↔accuracy del auto-pase, barriendo umbrales de confianza.

    A diferencia de `gate_summary`, NO depende de `gate_humano`: recalcula el
    auto-pase sólo con la confianza del bucket (`choice`). Así el comprador ve el
    precio de cada punto de operación (cuántas cláusulas se auto-resuelven y con
    cuántos errores) en vez de heredar un umbral dogmático. PURA: no toca red.
    """
    ths = tuple(thresholds) if thresholds is not None else DEFAULT_SWEEP
    n = len(rows)
    curve = []
    for t in ths:
        th = float(t)
        auto = [r for r in rows if float(r.get(conf, 0)) >= th]
        errores = [r for r in auto if r["gold_bucket"] != r["pred_bucket"]]
        curve.append({
            "umbral": round(th, 2),
            "n": n,
            "auto": len(auto),
            "cobertura": _div(len(auto), n),
            "accuracy_auto": _div(len(auto) - len(errores), len(auto)),
            "errores_auto": len(errores),
            "ids_errores": [r.get("id") for r in errores],
        })
    return curve


def recommended_point(curve: list[dict]):
    """Punto de operación con MÁXIMA cobertura entre los que no auto-resuelven errores.

    None si la curva está vacía o si ningún umbral llega a cero errores (no se
    inventa recomendación cuando no hay evidencia de que exista un punto seguro).
    """
    seguros = [p for p in curve if p["errores_auto"] == 0 and p["auto"] > 0]
    if not seguros:
        return None
    return max(seguros, key=lambda p: (p["cobertura"], -p["umbral"]))


def evaluate_by_source(rows: list[dict], threshold: float = 0.8, n_bins: int = 5) -> dict:
    """Desagrega accuracy/calibración y curva cobertura↔accuracy POR FUENTE (documento).

    Una accuracy global puede esconder una política con 0.95 y otra con 0.60; la
    estabilidad cross-documento es justo lo que hay que medir antes de fijar un
    umbral por defecto. PURA: agrupa por `row["source"]` (ausente -> "?"), no
    toca red ni disco. Devuelve {} sin filas (no inventa fuentes vacías).
    """
    grupos: dict[str, list[dict]] = {}
    for r in rows:
        grupos.setdefault(r.get("source") or "?", []).append(r)
    out: dict[str, dict] = {}
    for src in sorted(grupos):
        rs = grupos[src]
        curva = coverage_curve(rs)
        out[src] = {
            "n": len(rs),
            "evaluacion": evaluate(rs, threshold=threshold, n_bins=n_bins),
            "curva": curva,
            "punto_recomendado": recommended_point(curva),
        }
    return out


def evaluate(rows: list[dict], threshold: float = 0.8, n_bins: int = 5) -> dict:
    """Reporte completo de accuracy/calibración/cobertura sobre filas gold+predicción."""
    sev_rows = [r for r in rows if r.get("pred_severity") is not None and r.get("gold_severity") is not None]
    return {
        "n": len(rows),
        "accuracy_bucket": accuracy(rows),
        "matriz_confusion": confusion(rows),
        "por_clase": per_class(rows),
        "severidad_accuracy_exacta": accuracy(sev_rows, gold="gold_severity", pred="pred_severity"),
        "severidad_mae": severity_mae(sev_rows),
        "severidad_dentro_de_1": severity_within_one(sev_rows),
        "ece_bucket": ece(rows, conf="confidence", n_bins=n_bins),
        "ece_severidad": ece(sev_rows, conf="severity_confidence", gold="gold_severity",
                             pred="pred_severity", n_bins=n_bins),
        "fiabilidad_bucket": reliability(rows, conf="confidence", n_bins=n_bins),
        "fiabilidad_severidad": reliability(sev_rows, conf="severity_confidence",
                                            gold="gold_severity", pred="pred_severity", n_bins=n_bins),
        "gate": gate_summary(rows, threshold=threshold),
    }


def rows_from_report(tabla: list[dict], gold_by_id: dict) -> list[dict]:
    """Une el reporte de producción (`tabla`) con el gold y normaliza el eje severidad.

    `gold_by_id`: {clausula: {"bucket": ..., "severity": 0|1|2}}.
    `pred_severity` sale del nivel ganador de `probabilities` que ya calculó
    build_report (`nivel_severidad`), no del float crudo del score.
    """
    rows = []
    for t in tabla:
        cid = t["clausula"]
        g = gold_by_id[cid]
        nivel = t.get("nivel_severidad")
        rows.append({
            "id": cid,
            "gold_bucket": g["bucket"],
            "pred_bucket": t["decidido"],
            "gold_severity": g["severity"],
            "pred_severity": SEVERITY_NAMES.index(nivel) if nivel in SEVERITY_NAMES else None,
            "confidence": t.get("confianza", 0),
            "severity_confidence": t.get("severidad_confianza", 0),
            "gate_humano": bool(t.get("gate_humano", False)),
        })
    return rows


__all__ = ["LABELS", "SEVERITIES", "SEVERITY_NAMES", "DEFAULT_SWEEP", "accuracy", "confusion", "per_class",
           "severity_mae", "severity_within_one", "reliability", "ece", "gate_summary", "coverage_curve",
           "recommended_point", "evaluate", "evaluate_by_source", "rows_from_report"]
