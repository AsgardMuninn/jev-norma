#!/usr/bin/env python3
"""Emite el paquete CIEGO de anotación (sin etiqueta ni predicción) + su guía.

Uso:
    python tools/annotate_packet.py wu22/gold_multi.jsonl \\
        --out wu22b/packet_blind.jsonl --guide wu22b/GUIA-ANOTACION.md

El paquete sólo lleva `id`, `source` y `text`: ni `bucket`, ni `severity`, ni
`rationale`, ni nada del reporte del modelo. Así un segundo anotador puede
etiquetar sin contaminarse y `tools/kappa_report.py` mide acuerdo real.

No imprime etiquetas, no llama a la API.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jev_norma.packet import BLIND_FIELDS, build_packet, guide_markdown, packet_csv  # noqa: E402


def write_packet(packet: list[dict], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in packet),
                   encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Paquete ciego de anotación (sin etiquetas)")
    ap.add_argument("gold", help="gold JSONL con id/source/text (las etiquetas se descartan)")
    ap.add_argument("--out", default="wu22b/packet_blind.jsonl", help="paquete ciego de salida")
    ap.add_argument("--csv", default="wu22b/packet_blind.csv",
                    help="paquete CSV para anotar en hoja de cálculo")
    ap.add_argument("--guide", default="wu22b/GUIA-ANOTACION.md", help="guía para el anotador")
    args = ap.parse_args(argv)

    gold = Path(args.gold)
    if not gold.is_file():
        sys.exit("error: no existe %s" % gold)
    packet, meta = build_packet(gold)

    # Cinturón y tirantes: el paquete no puede llevar ningún campo prohibido.
    for r in packet:
        if set(r) - set(BLIND_FIELDS):
            sys.exit("error: el paquete filtró campos: %s" % sorted(set(r) - set(BLIND_FIELDS)))

    out = write_packet(packet, Path(args.out))
    csv_out = Path(args.csv)
    csv_out.parent.mkdir(parents=True, exist_ok=True)
    csv_out.write_text(packet_csv(packet), encoding="utf-8")
    guide = Path(args.guide)
    guide.parent.mkdir(parents=True, exist_ok=True)
    guide.write_text(guide_markdown(meta), encoding="utf-8")

    print("paquete ciego: %d cláusulas -> %s (+ CSV %s)" % (meta["n"], out, csv_out))
    print("fuentes: " + ", ".join("%s=%d" % (k, v) for k, v in meta["fuentes"].items()))
    print("sha256 del gold: " + meta["sha256"])
    print("guía: %s" % guide)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
