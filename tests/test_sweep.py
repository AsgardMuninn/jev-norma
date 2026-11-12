"""Tests de la curva cobertura↔accuracy (`--sweep`) — puros, sin API.

La curva responde la pregunta de producto que dejó WU20: el comprador elige el
punto de operación (cuánto auto-pasa vs cuánto manda a humano), no un umbral fijo.
"""

import importlib.util
import json
import sys
from pathlib import Path

from jev_norma.metrics import DEFAULT_SWEEP, coverage_curve, recommended_point

FILAS = [
    {"id": "a", "gold_bucket": "obligacion", "pred_bucket": "obligacion", "confidence": 0.99},
    {"id": "b", "gold_bucket": "riesgo", "pred_bucket": "riesgo", "confidence": 0.90},
    {"id": "c", "gold_bucket": "sin_accion", "pred_bucket": "obligacion", "confidence": 0.91},
    {"id": "d", "gold_bucket": "sin_accion", "pred_bucket": "sin_accion", "confidence": 0.60},
    {"id": "e", "gold_bucket": "obligacion", "pred_bucket": "sin_accion", "confidence": 0.40},
]


def test_curve_es_monotona_en_cobertura_y_arranca_sin_filtro():
    curva = coverage_curve(FILAS, thresholds=(0.0, 0.6, 0.9, 0.95))
    assert [p["umbral"] for p in curva] == [0.0, 0.6, 0.9, 0.95]
    coberturas = [p["cobertura"] for p in curva]
    assert coberturas == sorted(coberturas, reverse=True)
    assert curva[0]["auto"] == len(FILAS) and curva[0]["cobertura"] == 1.0
    # umbral 0.0 = todo auto, accuracy = accuracy global (3/5)
    assert abs(curva[0]["accuracy_auto"] - 3 / 5) < 1e-9
    # umbral 0.95 solo deja pasar a "a" (0.99) que acierta
    assert curva[-1]["auto"] == 1 and curva[-1]["errores_auto"] == 0


def test_curve_expone_los_ids_de_los_errores_auto():
    punto = next(p for p in coverage_curve(FILAS, thresholds=(0.9,)) if p["umbral"] == 0.9)
    # a, b, c pasan (c se equivoca)
    assert punto["auto"] == 3
    assert punto["errores_auto"] == 1
    assert punto["ids_errores"] == ["c"]


def test_curve_sin_filas_no_inventa_metricas():
    punto = coverage_curve([], thresholds=(0.8,))[0]
    assert punto == {"umbral": 0.8, "n": 0, "auto": 0, "cobertura": None,
                     "accuracy_auto": None, "errores_auto": 0, "ids_errores": []}


def test_recommended_point_prefiere_cero_errores_y_maxima_cobertura():
    curva = coverage_curve(FILAS, thresholds=DEFAULT_SWEEP)
    rec = recommended_point(curva)
    assert rec is not None
    assert rec["errores_auto"] == 0
    # el umbral más bajo sin errores es 0.95 en estos datos
    rec_p = recommended_point(curva)
    assert rec_p is not None
    assert rec_p["umbral"] == 0.95 and rec_p["auto"] == 1
    assert recommended_point([]) is None


def _load_tool():
    path = Path(__file__).resolve().parent.parent / "tools" / "eval_gold.py"
    spec = importlib.util.spec_from_file_location("eval_gold_tool", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_sweep_offline_recalcula_sin_llamar_a_la_api(tmp_path, monkeypatch):
    tool = _load_tool()

    def _boom(*a, **k):  # cualquier llamada real a Jev es un fallo del test
        raise AssertionError("el modo --from-evidence no debe llamar a la API")

    monkeypatch.setattr(tool, "gate", _boom)

    ev = {"modelo": "jev-1.14.0",
          "gold": {"n": len(FILAS), "ruta": "wu20/gold.jsonl"},
          "ejecucion": {"in_tokens": 1000, "latencia_ms": 200},
          "filas": FILAS}
    src = tmp_path / "evidence.json"
    src.write_text(json.dumps(ev), encoding="utf-8")
    out = tmp_path / "sweep.json"

    rc = tool.main(["--from-evidence", str(src), "--out", str(out), "--sweep"])
    assert rc == 0

    got = json.loads(out.read_text(encoding="utf-8"))
    assert got["modelo"] == "jev-1.14.0"
    assert got["origen"] == "evidence-replay"
    assert got["evaluacion"]["n"] == len(FILAS)
    assert abs(got["evaluacion"]["accuracy_bucket"] - 3 / 5) < 1e-9
    assert len(got["curva"]) == len(DEFAULT_SWEEP)
    assert got["punto_recomendado"]["errores_auto"] == 0
