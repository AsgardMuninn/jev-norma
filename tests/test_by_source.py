"""Tests de la desagregación POR FUENTE (WU22a): estabilidad cross-documento sin API.

El gold multi-política necesita medir cada documento por separado: una accuracy
global de 0.85 puede esconder una política 0.95 y otra 0.70. Estas pruebas fijan
el contrato ANTES de implementar `evaluate_by_source` (TDD).
"""

import json
from pathlib import Path

from jev_norma.metrics import evaluate_by_source, recommended_point

FILAS = [
    # fuente A: 2 aciertos de 2, confianzas altas
    {"id": "a1", "source": "doc-a", "gold_bucket": "obligacion", "pred_bucket": "obligacion",
     "confidence": 0.95, "gold_severity": 2, "pred_severity": 2, "severity_confidence": 0.8,
     "gate_humano": False},
    {"id": "a2", "source": "doc-a", "gold_bucket": "sin_accion", "pred_bucket": "sin_accion",
     "confidence": 0.9, "gold_severity": 0, "pred_severity": 0, "severity_confidence": 0.9,
     "gate_humano": False},
    # fuente B: 1 acierto de 2, la falla con confianza baja (fail-open correcto)
    {"id": "b1", "source": "doc-b", "gold_bucket": "riesgo", "pred_bucket": "riesgo",
     "confidence": 0.55, "gold_severity": 2, "pred_severity": 2, "severity_confidence": 0.5,
     "gate_humano": True},
    {"id": "b2", "source": "doc-b", "gold_bucket": "obligacion", "pred_bucket": "sin_accion",
     "confidence": 0.3, "gold_severity": 2, "pred_severity": 0, "severity_confidence": 0.4,
     "gate_humano": True},
]


def test_evaluate_by_source_desagrega_n_y_accuracy():
    out = evaluate_by_source(FILAS)
    assert set(out) == {"doc-a", "doc-b"}
    assert out["doc-a"]["n"] == 2
    assert out["doc-b"]["n"] == 2
    assert abs(out["doc-a"]["evaluacion"]["accuracy_bucket"] - 1.0) < 1e-9
    assert abs(out["doc-b"]["evaluacion"]["accuracy_bucket"] - 0.5) < 1e-9
    # la matriz de cada fuente solo cuenta sus propias filas
    assert out["doc-b"]["evaluacion"]["matriz_confusion"]["riesgo"]["riesgo"] == 1
    assert out["doc-a"]["evaluacion"]["matriz_confusion"]["riesgo"]["riesgo"] == 0


def test_evaluate_by_source_curva_independiente_por_fuente():
    out = evaluate_by_source(FILAS)
    curva_a = out["doc-a"]["curva"]
    curva_b = out["doc-b"]["curva"]
    # doc-a tiene todo con conf >= 0.9 -> a umbral 0.9 auto=2, 0 errores
    alto_a = [p for p in curva_a if p["umbral"] == 0.9][0]
    assert alto_a["auto"] == 2 and alto_a["errores_auto"] == 0
    # doc-b nunca llega a 0.9 -> auto=0 (no se inventa cobertura)
    alto_b = [p for p in curva_b if p["umbral"] == 0.9][0]
    assert alto_b["auto"] == 0 and alto_b["cobertura"] == 0.0
    assert out["doc-a"]["punto_recomendado"] == recommended_point(curva_a)


def test_evaluate_by_source_sin_source_usa_placeholder():
    filas = [{"gold_bucket": "obligacion", "pred_bucket": "obligacion", "confidence": 0.9,
              "gold_severity": 1, "pred_severity": 1, "severity_confidence": 0.8,
              "gate_humano": False}]
    out = evaluate_by_source(filas)
    assert list(out) == ["?"]
    assert out["?"]["n"] == 1


def test_evaluate_by_source_sin_filas_no_inventa_metricas():
    assert evaluate_by_source([]) == {}


def _load_tool():
    import importlib.util
    import sys

    path = Path(__file__).resolve().parent.parent / "tools" / "eval_gold.py"
    spec = importlib.util.spec_from_file_location("eval_gold", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_gold"] = mod
    spec.loader.exec_module(mod)
    return mod


GOLD = [
    {"id": "a1", "source": "doc-a", "text": "El responsable conservará prueba de la autorización.",
     "bucket": "obligacion", "severity": 2, "rationale": "Deber de conservar autorizaciones."},
    {"id": "b1", "source": "doc-b", "text": "Los datos serán usados para videovigilancia.",
     "bucket": "riesgo", "severity": 1, "rationale": "Recolección por cámaras."},
]


def test_eval_gold_emite_por_fuente_y_adjunta_source_a_las_filas(tmp_path, monkeypatch):
    tool = _load_tool()
    gold = tmp_path / "gold.jsonl"
    gold.write_text("\n".join(json.dumps(g) for g in GOLD), encoding="utf-8")

    def _fake_gate(sample, model=None):
        answers = {cid: {"decision": "obligacion", "confidence": 0.91, "score": 2,
                         "score_confidence": 0.88}
                   for cid in sample}
        return {"latencia_ms": 10, "in_tokens": 100, "out_tokens": 0,
                "answers": answers, "modelo": "jev-test"}

    monkeypatch.setattr(tool, "gate", _fake_gate)
    out = tmp_path / "ev.json"
    rc = tool.main([str(gold), "--out", str(out), "--by-source"])
    assert rc == 0

    ev = json.loads(out.read_text(encoding="utf-8"))
    assert "por_fuente" in ev
    assert set(ev["por_fuente"]) == {"doc-a", "doc-b"}
    assert ev["evaluacion"]["n"] == 2  # sigue habiendo evaluación global
    assert {f.get("source") for f in ev["filas"]} == {"doc-a", "doc-b"}
    assert ev["por_fuente"]["doc-a"]["evaluacion"]["n"] == 1
