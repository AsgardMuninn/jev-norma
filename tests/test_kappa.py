"""Tests del acuerdo inter-anotador (Cohen κ) y del paquete ciego (WU22b).

Todo puro: 0 llamadas a la API, 0 red. El κ real sólo puede calcularse cuando
un segundo anotador devuelva el paquete etiquetado; aquí se fija el contrato con
casos conocidos (acuerdo perfecto, azar, desacuerdo sistemático) y se verifica
que el paquete NO filtre etiqueta alguna.
"""

import importlib.util
from pathlib import Path

import pytest

from jev_norma.metrics import (
    agreement_matrix,
    cohen_kappa,
    kappa_report,
)
from jev_norma.packet import (
    BLIND_FIELDS,
    FORBIDDEN_FIELDS,
    blind_rows,
    build_packet,
    guide_markdown,
)

ROOT = Path(__file__).resolve().parent.parent


def _row(rid, bucket, source="doc-a", severity=1):
    return {"id": rid, "source": source, "text": "cláusula %s" % rid,
            "bucket": bucket, "severity": severity}


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- Cohen κ

def test_kappa_perfect_agreement_is_one():
    a = [_row("1", "obligacion"), _row("2", "riesgo"), _row("3", "sin_accion")]
    assert cohen_kappa(a, list(a)) == pytest.approx(1.0)


def test_kappa_chance_agreement_is_zero():
    a = [_row("1", "obligacion"), _row("2", "obligacion"),
         _row("3", "riesgo"), _row("4", "riesgo")]
    b = [_row("1", "obligacion"), _row("2", "riesgo"),
         _row("3", "obligacion"), _row("4", "riesgo")]
    # po=0.5 y pe=0.5 -> κ=0 exactamente
    assert cohen_kappa(a, b) == pytest.approx(0.0)


def test_kappa_systematic_disagreement_is_negative():
    a = [_row("1", "obligacion"), _row("2", "obligacion"),
         _row("3", "riesgo"), _row("4", "riesgo")]
    b = [_row("1", "riesgo"), _row("2", "riesgo"),
         _row("3", "obligacion"), _row("4", "obligacion")]
    assert cohen_kappa(a, b) == pytest.approx(-1.0)


def test_kappa_undefined_cases_return_none():
    assert cohen_kappa([], []) is None
    assert cohen_kappa([_row("1", "riesgo")], [_row("2", "riesgo")]) is None  # sin ids comunes
    # pe == 1: ambos anotadores usan SIEMPRE la misma etiqueta -> κ indefinido
    assert cohen_kappa([_row("1", "riesgo")], [_row("1", "riesgo")]) is None


def test_kappa_joins_by_id_not_by_position():
    a = [_row("1", "obligacion"), _row("2", "riesgo")]
    b = [_row("2", "riesgo"), _row("1", "obligacion")]
    assert cohen_kappa(a, b) == pytest.approx(1.0)


def test_kappa_rejects_duplicate_ids_and_missing_labels():
    dup = [_row("1", "riesgo"), _row("1", "riesgo")]
    with pytest.raises(ValueError):
        cohen_kappa(dup, [_row("1", "riesgo")])
    with pytest.raises(ValueError):
        cohen_kappa([{"id": "1", "text": "x"}], [_row("1", "riesgo")])


def test_agreement_matrix_counts_off_diagonal_cells():
    a = [_row("1", "obligacion"), _row("2", "riesgo"), _row("3", "riesgo")]
    b = [_row("1", "obligacion"), _row("2", "obligacion"), _row("3", "riesgo")]
    m = agreement_matrix(a, b)
    assert m["n"] == 3
    assert m["matriz"]["obligacion"]["obligacion"] == 1
    assert m["matriz"]["riesgo"]["obligacion"] == 1
    assert m["matriz"]["riesgo"]["riesgo"] == 1
    assert m["matriz"]["sin_accion"]["sin_accion"] == 0
    assert m["acuerdo_observado"] == pytest.approx(2 / 3)


def test_kappa_report_per_source_and_verdict():
    a = [_row("1", "obligacion", "doc-a"), _row("2", "riesgo", "doc-a"),
         _row("3", "sin_accion", "doc-b"), _row("4", "obligacion", "doc-b")]
    b = [_row("1", "obligacion", "doc-a"), _row("2", "riesgo", "doc-a"),
         _row("3", "obligacion", "doc-b"), _row("4", "obligacion", "doc-b")]
    rep = kappa_report(a, b)
    assert rep["n_comun"] == 4
    assert rep["kappa"] == pytest.approx(5 / 9)
    assert set(rep["por_fuente"]) == {"doc-a", "doc-b"}
    assert rep["por_fuente"]["doc-a"]["kappa"] == pytest.approx(1.0)
    assert rep["por_fuente"]["doc-b"]["kappa"] == pytest.approx(0.0)
    assert rep["veredicto"] == "no_fiable"
    assert rep["umbral"] == 0.6


def test_kappa_report_reports_missing_ids_and_severity_agreement():
    a = [_row("1", "obligacion", severity=2), _row("2", "riesgo", severity=1)]
    b = [_row("1", "obligacion", severity=2), _row("3", "riesgo", severity=1)]
    rep = kappa_report(a, b)
    assert rep["n_comun"] == 1
    assert rep["solo_a"] == ["2"]
    assert rep["solo_b"] == ["3"]
    assert rep["severidad"]["n"] == 1
    assert rep["severidad"]["exacta"] == 1.0
    assert rep["severidad"]["mae"] == 0.0


def test_kappa_report_undefined_is_not_a_fake_number():
    a = [_row("1", "riesgo")]
    rep = kappa_report(a, [_row("1", "riesgo")])
    assert rep["kappa"] is None
    assert rep["veredicto"] == "indefinido"


# ------------------------------------------------------------ paquete ciego

def test_blind_rows_strip_every_label_and_prediction_field():
    rows = [{"id": "n1", "source": "doc-a", "text": "hola",
             "bucket": "riesgo", "severity": 2, "rationale": "x",
             "pred_bucket": "obligacion", "confidence": 0.9}]
    out = blind_rows(rows)
    assert out == [{"id": "n1", "source": "doc-a", "text": "hola"}]
    assert set(out[0]) == set(BLIND_FIELDS)
    assert not (FORBIDDEN_FIELDS & set(out[0]))


def test_blind_rows_requires_text_and_unique_ids():
    with pytest.raises(ValueError):
        blind_rows([{"id": "n1", "source": "doc-a"}])
    with pytest.raises(ValueError):
        blind_rows([_row("n1", "riesgo"), _row("n1", "riesgo")])


def test_build_packet_meta_has_counts_sources_and_hash(tmp_path):
    p = tmp_path / "gold.jsonl"
    import json

    p.write_text("\n".join(json.dumps(_row(i, "riesgo", "doc-a" if i < "5" else "doc-b"))
                           for i in ["1", "2", "5", "6"]) + "\n", encoding="utf-8")
    packet, meta = build_packet(p)
    assert meta["n"] == 4
    assert meta["fuentes"] == {"doc-a": 2, "doc-b": 2}
    assert len(meta["sha256"]) == 64
    assert all(set(r) == set(BLIND_FIELDS) for r in packet)


def test_guide_is_self_contained_and_mentions_the_answer_schema():
    meta = {"n": 52, "fuentes": {"doc-a": 26, "doc-b": 26}, "sha256": "a" * 64,
            "fecha": "2026-11-12T11:00:00-05:00", "origen": "wu22/gold_multi.jsonl"}
    g = guide_markdown(meta)
    for token in ("obligacion", "riesgo", "sin_accion", "severity", "0", "1", "2",
                  "52", "doc-a", "doc-b", "a" * 64, "rúbrica v1", "0.6", "abrir el gold"):
        assert token in g


# -------------------------------------------------------------------- CLIs

def test_annotate_packet_cli_writes_blind_packet_and_guide(tmp_path):
    import json

    gold = tmp_path / "gold.jsonl"
    gold.write_text("\n".join(json.dumps(_row(i, "riesgo")) for i in ["1", "2"]) + "\n",
                    encoding="utf-8")
    out = tmp_path / "packet.jsonl"
    csv_out = tmp_path / "packet.csv"
    guide = tmp_path / "GUIA.md"
    mod = _load_tool("annotate_packet")
    assert mod.main([str(gold), "--out", str(out), "--csv", str(csv_out),
                     "--guide", str(guide)]) == 0
    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [r["id"] for r in rows] == ["1", "2"]
    assert all(set(r) == set(BLIND_FIELDS) for r in rows)
    assert "rúbrica v1" in guide.read_text(encoding="utf-8")

    import csv

    with csv_out.open(encoding="utf-8") as f:
        csv_rows = list(csv.DictReader(f))
    assert list(csv_rows[0]) == ["id", "source", "text", "bucket", "severity", "nota"]
    assert csv_rows[0]["text"] == "cláusula 1"
    assert csv_rows[0]["bucket"] == "" and csv_rows[0]["severity"] == ""


def test_kappa_report_cli_reads_csv_answers(tmp_path):
    import json

    a = tmp_path / "gold.jsonl"
    a.write_text("\n".join(json.dumps(r) for r in
                           [_row("1", "obligacion", severity=2),
                            _row("2", "riesgo", severity=1)]) + "\n", encoding="utf-8")
    b = tmp_path / "anotador2.csv"
    b.write_text("id,source,text,bucket,severity,nota\n"
                 "1,doc-a,cláusula 1,obligacion,2,\n"
                 "2,doc-a,cláusula 2,riesgo,1,duda menor\n", encoding="utf-8")
    ev = tmp_path / "ev.json"
    mod = _load_tool("kappa_report")
    assert mod.main([str(a), str(b), "--out", str(ev)]) == 0
    data = json.loads(ev.read_text(encoding="utf-8"))
    assert data["kappa"] == pytest.approx(1.0)
    assert data["severidad"]["exacta"] == 1.0


def test_kappa_report_cli_writes_evidence_json(tmp_path):
    import json

    a = tmp_path / "gold.jsonl"
    b = tmp_path / "anotador2.jsonl"
    a.write_text("\n".join(json.dumps(r) for r in
                           [_row("1", "obligacion"), _row("2", "riesgo")]) + "\n", encoding="utf-8")
    b.write_text("\n".join(json.dumps(r) for r in
                           [_row("1", "obligacion"), _row("2", "riesgo")]) + "\n", encoding="utf-8")
    ev = tmp_path / "ev.json"
    mod = _load_tool("kappa_report")
    assert mod.main([str(a), str(b), "--out", str(ev)]) == 0
    data = json.loads(ev.read_text(encoding="utf-8"))
    assert data["kappa"] == pytest.approx(1.0)
    assert data["n_comun"] == 2
