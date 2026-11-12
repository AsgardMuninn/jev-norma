#!/usr/bin/env python3
"""Evalúa el gate de jev-norma contra un gold set etiquetado (API real) y guarda evidencia.

Uso:
    python tools/eval_gold.py wu20/gold.jsonl --out wu20/evidence-wu20.json

El gold es JSONL con: id, text, bucket (obligacion|riesgo|sin_accion), severity (0|1|2),
source y rationale. El script corre el MISMO pipeline de producción (gate + build_report)
y calcula accuracy, matriz de confusión, MAE de severidad, ECE y cobertura del gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jev_norma.metrics import (  # noqa: E402
    DEFAULT_SWEEP,
    coverage_curve,
    evaluate,
    evaluate_by_source,
    recommended_point,
    rows_from_report,
)
from jev_norma.pipeline import CHUNK_SIZE, GATE_UMBRAL, build_report, gate  # noqa: E402


def load_gold(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    required = {"id", "text", "bucket", "severity"}
    for r in rows:
        missing = required - set(r)
        if missing:
            sys.exit(f"error: fila {r.get('id')} sin campos {sorted(missing)}")
    return rows


def _print_curve(curva: list[dict]) -> None:
    print("\ncurva cobertura↔accuracy (auto-pase por confianza de `choice`):")
    print("  umbral  auto  cobertura  accuracy_auto  errores_auto")
    for p in curva:
        cob = "n/a" if p["cobertura"] is None else f"{p['cobertura']:.2f}"
        acc = "n/a" if p["accuracy_auto"] is None else f"{p['accuracy_auto']:.2f}"
        print(f"  {p['umbral']:<6.2f}  {p['auto']:>3}  {cob:>9}  {acc:>13}  {p['errores_auto']:>12}")
    rec = recommended_point(curva)
    if rec:
        print(f"  punto recomendado (0 errores, máx cobertura): umbral {rec['umbral']:.2f} → "
              f"auto {rec['auto']}/{rec['n']} ({rec['cobertura']:.2f})")
    else:
        print("  punto recomendado: ninguno con 0 errores (no se recomienda automatizar todavía)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Evalúa jev-norma contra un gold set (API real de Jev)")
    ap.add_argument("gold", nargs="?", default=None, help="gold.jsonl etiquetado (modo API real)")
    ap.add_argument("--out", default=None, help="ruta de la evidencia JSON")
    ap.add_argument("--model", default=None, help="modelo a pinear (default: JEV_MODEL o jev-latest)")
    ap.add_argument("--from-evidence", default=None,
                    help="recalcula métricas+curva desde una evidencia guardada, SIN llamar a la API")
    ap.add_argument("--sweep", action="store_true", help="imprime la curva cobertura↔accuracy")
    ap.add_argument("--by-source", action="store_true",
                    help="desagrega métricas y curva POR FUENTE (estabilidad cross-documento)")
    args = ap.parse_args(argv)

    if args.from_evidence:
        prev = json.loads(Path(args.from_evidence).read_text(encoding="utf-8"))
        rows = prev["filas"]
        ev = evaluate(rows, threshold=GATE_UMBRAL)
        evidence = {
            "fecha": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "origen": "evidence-replay",
            "replay_de": str(args.from_evidence),
            "modelo": prev.get("modelo"),
            "gold": prev.get("gold"),
            "ejecucion": prev.get("ejecucion"),
            "evaluacion": ev,
            "curva": coverage_curve(rows),
            "punto_recomendado": recommended_point(coverage_curve(rows)),
            "filas": rows,
        }
        if args.by_source:
            evidence["por_fuente"] = evaluate_by_source(rows, threshold=GATE_UMBRAL)
    else:
        if not args.gold:
            ap.error("se requiere `gold.jsonl` o `--from-evidence`")
        gold = load_gold(Path(args.gold))
        sample = {g["id"]: g["text"] for g in gold}
        gold_by_id = {g["id"]: {"bucket": g["bucket"], "severity": g["severity"]} for g in gold}
        source_by_id = {g["id"]: g.get("source", "?") for g in gold}

        prod = gate(sample, model=args.model)
        report = build_report(sample, prod["answers"], prod["in_tokens"], prod["latencia_ms"],
                              modelo=prod["modelo"])
        rows = rows_from_report(report["tabla"], gold_by_id)
        for r in rows:
            r["source"] = source_by_id.get(r["id"], "?")
        ev = evaluate(rows, threshold=GATE_UMBRAL)
        evidence = {
            "fecha": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "origen": "api",
            "gold": {"ruta": str(args.gold), "n": len(gold),
                     "fuente": sorted({g.get("source", "?") for g in gold}),
                     "nota": "etiquetado por un solo anotador con rúbrica documentada; no es ground truth legal"},
            "modelo": prod["modelo"],
            "ejecucion": {"chunk_size": CHUNK_SIZE, "in_tokens": prod["in_tokens"],
                          "out_tokens": prod["out_tokens"], "latencia_ms": prod["latencia_ms"],
                          "costo_usd_estimado": round(prod["in_tokens"] * 42e-9, 6)},
            "evaluacion": ev,
            "curva": coverage_curve(rows),
            "punto_recomendado": recommended_point(coverage_curve(rows)),
            "filas": rows,
            "reporte_produccion": report,
        }
        if args.by_source:
            evidence["por_fuente"] = evaluate_by_source(rows, threshold=GATE_UMBRAL)

    out = json.dumps(evidence, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")

    ej = evidence.get("ejecucion") or {}
    modelo = evidence.get("modelo")
    if isinstance(modelo, list):
        modelo = ",".join(str(m) for m in modelo)
    print(f"n={ev['n']}  modelo={modelo}  tokens={ej.get('in_tokens')}  "
          f"latencia={ej.get('latencia_ms')}ms  costo=${ej.get('costo_usd_estimado')}")
    print(f"accuracy bucket = {ev['accuracy_bucket']:.3f}")
    for label, m in ev["por_clase"].items():
        prec = "n/a" if m["precision"] is None else f"{m['precision']:.2f}"
        rec = "n/a" if m["recall"] is None else f"{m['recall']:.2f}"
        print(f"  {label:<11} support={m['support']:<2} precision={prec} recall={rec}")
    sev = ev["severidad_accuracy_exacta"]
    mae = ev["severidad_mae"]
    dentro = ev["severidad_dentro_de_1"]
    if sev is not None:
        print(f"severidad: exacta={sev:.3f}  MAE={mae:.2f}  dentro_de_1={dentro:.3f}")
    ece_b = ev["ece_bucket"]
    ece_s = ev["ece_severidad"]
    fmt = lambda v: "n/a" if v is None else f"{v:.3f}"  # noqa: E731
    print(f"ECE bucket={fmt(ece_b)}  ECE severidad={fmt(ece_s)}")
    g = ev["gate"]
    auto = "n/a" if g["accuracy_auto"] is None else f"{g['accuracy_auto']:.2f}"
    cob = "n/a" if g["fraccion_revision"] is None else f"{1 - g['fraccion_revision']:.2f}"
    print(f"gate@{g['umbral']}: auto={g['auto']}/{g['n']} (cobertura {cob})  "
          f"accuracy_auto={auto}  errores_auto={g['errors_auto']}")
    if args.sweep:
        _print_curve(evidence["curva"])
    if args.by_source:
        print("\npor fuente (estabilidad cross-documento):")
        for src, d in evidence["por_fuente"].items():
            e = d["evaluacion"]
            rec = d["punto_recomendado"]
            rec_txt = "ninguno 0-errores" if rec is None else f"umbral {rec['umbral']:.2f}"
            print(f"  {src[:34]:<34} n={d['n']:<3} acc={e['accuracy_bucket']:.3f} "
                  f"ECE={e['ece_bucket']:.3f} gate@{e['gate']['umbral']}: auto={e['gate']['auto']} "
                  f"err={e['gate']['errors_auto']} | recomendado={rec_txt}")
    if args.out:
        print(f"(evidencia en {args.out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
