"""Tests de métricas puras (sin API): accuracy, matriz de confusión, calibración y gate."""

from jev_norma.metrics import (
    accuracy,
    confusion,
    ece,
    evaluate,
    gate_summary,
    per_class,
    reliability,
    severity_mae,
    severity_within_one,
)

ROWS = [
    {"gold_bucket": "obligacion", "pred_bucket": "obligacion", "confidence": 0.9,
     "gold_severity": 2, "pred_severity": 2, "severity_confidence": 0.9, "gate_humano": False},
    {"gold_bucket": "riesgo", "pred_bucket": "sin_accion", "confidence": 0.4,
     "gold_severity": 2, "pred_severity": 1, "severity_confidence": 0.5, "gate_humano": True},
    {"gold_bucket": "sin_accion", "pred_bucket": "sin_accion", "confidence": 0.7,
     "gold_severity": 0, "pred_severity": 0, "severity_confidence": 0.7, "gate_humano": True},
]


def test_accuracy_and_confusion():
    assert abs(accuracy(ROWS) - 2 / 3) < 1e-9
    conf = confusion(ROWS)
    assert conf["obligacion"]["obligacion"] == 1
    assert conf["riesgo"]["sin_accion"] == 1
    assert conf["sin_accion"]["sin_accion"] == 1
    assert conf["obligacion"]["riesgo"] == 0


def test_per_class_precision_recall_support():
    pc = per_class(ROWS)
    assert pc["obligacion"] == {"tp": 1, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0, "support": 1}
    # riesgo nunca predicho: precision indefinida (None), recall 0
    assert pc["riesgo"]["precision"] is None
    assert pc["riesgo"]["recall"] == 0.0
    assert pc["riesgo"]["f1"] is None
    assert pc["sin_accion"]["precision"] == 0.5
    assert pc["sin_accion"]["recall"] == 1.0
    assert abs(pc["sin_accion"]["f1"] - 2 / 3) < 1e-9


def test_severity_metrics():
    assert abs(severity_mae(ROWS) - 1 / 3) < 1e-9
    assert severity_within_one(ROWS) == 1.0


def test_reliability_and_ece():
    bins = reliability(ROWS)
    assert [b["n"] for b in bins] == [0, 0, 1, 1, 1]
    assert abs(bins[2]["avg_confidence"] - 0.4) < 1e-9
    assert bins[2]["accuracy"] == 0.0
    assert abs(bins[4]["avg_confidence"] - 0.9) < 1e-9
    assert bins[4]["accuracy"] == 1.0
    assert abs(ece(ROWS) - (0.1 + 0.4 + 0.3) / 3) < 1e-9


def test_gate_summary_coverage_and_auto_accuracy():
    g = gate_summary(ROWS, threshold=0.8)
    assert g["n"] == 3
    assert g["revisadas"] == 2
    assert abs(g["fraccion_revision"] - 2 / 3) < 1e-9
    assert g["auto"] == 1
    assert g["accuracy_auto"] == 1.0
    assert g["umbral"] == 0.8


def test_evaluate_shape_and_empty_input():
    ev = evaluate(ROWS)
    assert ev["n"] == 3
    assert set(ev["por_clase"]) == {"obligacion", "riesgo", "sin_accion"}
    assert ev["accuracy_bucket"] is not None
    assert ev["ece_bucket"] is not None
    assert ev["gate"]["auto"] == 1
    empty = evaluate([])
    assert empty["n"] == 0
    assert empty["accuracy_bucket"] is None
    assert empty["ece_bucket"] is None
    assert empty["severidad_mae"] is None


def test_rows_from_report_joins_gold_and_maps_severity_level():
    from jev_norma.metrics import rows_from_report

    gold = {
        "n1": {"bucket": "obligacion", "severity": 2},
        "n2": {"bucket": "riesgo", "severity": 1},
    }
    tabla = [
        {"clausula": "n1", "decidido": "obligacion", "confianza": 0.95, "severidad": 1.9,
         "severidad_confianza": 0.9, "nivel_severidad": "exigible_sancionable", "gate_humano": False},
        {"clausula": "n2", "decidido": "sin_accion", "confianza": 0.5, "severidad": 1.4,
         "severidad_confianza": 0.6, "nivel_severidad": "informativa_menor", "gate_humano": True},
    ]
    rows = rows_from_report(tabla, gold)
    assert rows[0] == {"id": "n1", "gold_bucket": "obligacion", "pred_bucket": "obligacion",
                       "gold_severity": 2, "pred_severity": 2, "confidence": 0.95,
                       "severity_confidence": 0.9, "gate_humano": False}
    assert rows[1]["pred_severity"] == 1
    assert rows[1]["gate_humano"] is True
