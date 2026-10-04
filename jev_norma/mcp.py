"""jev_norma.mcp — servidor MCP (stdio, stdlib puro) que expone el pipeline como tool `triage`.

Protocolo MCP 2024-11-05, subset stdio, JSON-RPC 2.0. Sin dependencias fuera de stdlib
(coherente con la filosofía 0-deps del paquete). Un agente (Hermes/Claude) lo consume
sin UI: tools/call triage(source|text, type, sample, api).

Uso:
  jev-norma-mcp --selftest   -> tests del protocolo (sin API, sin stdio real)
  jev-norma-mcp              -> sirve MCP por stdio (lo lanza un cliente MCP)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .gate import sanitize_clause
from .normalizer import normalize_clauses_general, segment_plain_text
from .pipeline import build_report, detect_input, gate, normalize_document, sample_indices

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "jev-norma"
SERVER_VERSION = "0.1.0"

TOOL_SCHEMA = {
    "name": "triage",
    "description": (
        "Triaje normativo LatAm (gate System One). Toma un documento legal (HTML o PDF "
        "de una política de tratamiento de datos / términos en español, Ley 1581 Colombia) "
        "o texto/HTML inline, normaliza cláusulas (heurística 0-IA sin palabras), y clasifica "
        "cada cláusula con Jev en choice [obligacion|riesgo|sin_accion] + confidence calibrada. "
        "Devuelve un reporte JSON con cola de revisión humana: toda cláusula con confianza < 0.8 "
        "marca gate_humano=true (fail-open: nunca matar por Jev solo)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Ruta a un archivo .html/.pdf. Alternativo a 'text'."},
            "text": {"type": "string", "description": "Texto o HTML inline (si no se pasa 'source')."},
            "type": {"type": "string", "enum": ["html", "pdf", "text"],
                     "description": "Tipo del contenido. 'pdf' solo tiene sentido con source. Por defecto se infiere."},
            "sample": {"type": "number", "description": "Cláusulas a enviar al gate Jev (0 = todas). Default 12.", "default": 12},
            "api": {"type": "boolean", "description": "Si false, solo normaliza (sin llamar a la API de Jev). Default true.", "default": True},
        },
        "required": [],
    },
}


def _infer_type(source_path, explicit):
    if explicit in ("html", "pdf", "text"):
        return explicit
    if source_path is not None:
        return detect_input(Path(source_path))
    return "text"


def _normalize(source, text, kind):
    """Devuelve (clauses, headers, stats). kind ya resuelto."""
    if source is not None:
        path = Path(source)
        if not path.exists():
            raise ValueError(f"no existe la ruta: {source}")
        r = normalize_document(path)
        return r.get("clauses", []), r.get("headers", []), r.get("stats", {})
    if not text or not text.strip():
        raise ValueError("se requiere 'source' o 'text'")
    if kind == "html":
        r = normalize_clauses_general(text)
        return r.get("clauses", []), r.get("headers", []), r.get("stats", {})
    r = segment_plain_text(text)
    return r.get("clauses", []), [], r.get("stats", {})


def tool_triage(args):
    """Ejecuta la tool 'triage'. Devuelve dict con content MCP (no lanza en error de negocio)."""
    api = args.get("api", True)
    source = args.get("source")
    text = args.get("text")
    sample = int(args.get("sample", 12))
    kind = _infer_type(source, args.get("type"))
    clauses, headers, stats = _normalize(source, text, kind)

    if not api:
        return {
            "gate": "SKIPPED (api=false)", "kind": kind, "stats": stats,
            "n_clauses": len(clauses), "headers": headers[:10],
            "primeras_clausulas": [sanitize_clause(c) for c in clauses[:5]],
        }

    n = min(sample, len(clauses)) if sample > 0 else len(clauses)
    indices = sample_indices(clauses, n)
    sample_map = {f"{'d' if kind == 'pdf' else 'g'}{i:02d}": clauses[i] for i in indices}
    from .gate import load_key
    if not load_key():
        return {"gate": "ERROR",
                "error": "TYPESAFE_API_KEY no encontrada en ~/.hermes/.env. Pasa api=false para solo-normalización."}
    r = gate(sample_map)
    report = build_report(sample_map, r["answers"], r["in_tokens"], r["latencia_ms"])
    report["NOTA"] = "cláusulas normalizadas por heurística 0-IA (no curadas); confianza<0.8 -> revisión humana (fail-open)"
    return report


def handle_message(msg):
    """Procesa un mensaje JSON-RPC 2.0. Devuelve el dict a responder (o None si notification)."""
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"protocolVersion": PROTOCOL_VERSION,
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}}
    if method == "notifications/initialized":
        return None
    if method in ("ping", "tools/list"):
        result = {} if method == "ping" else {"tools": [TOOL_SCHEMA]}
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    if method == "tools/call":
        params = msg.get("params", {}) or {}
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        if name != "triage":
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": json.dumps({"error": f"tool desconocida: {name}"})}],
                               "isError": True}}
        try:
            body = tool_triage(args)
            is_error = body.get("gate") == "ERROR"
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False, indent=2)}],
                               "isError": bool(is_error)}}
        except Exception as e:  # noqa: BLE001 — error de negocio propagado como resultado MCP
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": json.dumps({"gate": "ERROR", "error": str(e)}, ensure_ascii=False)}],
                               "isError": True}}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"método no soportado: {method}"}}


def serve_stdio():
    """Lee mensajes JSON por línea de stdin y responde por stdout (transporte MCP stdio)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_message(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


def selftest() -> bool:
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("PASS" if cond else "FAIL"), name)
        ok = ok and cond

    r = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    check("initialize devuelve id 1", r["id"] == 1 and r["result"]["protocolVersion"] == PROTOCOL_VERSION)
    check("initialize anuncia tools", "tools" in r["result"]["capabilities"])
    check("initialize serverInfo jev-norma", r["result"]["serverInfo"]["name"] == "jev-norma")
    check("initialized no responde", handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None)
    check("ping responde", handle_message({"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"] == {})
    r = handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = r["result"]["tools"]
    check("tools/list tiene triage", len(tools) == 1 and tools[0]["name"] == "triage")
    check("schema triage inputSchema object", tools[0]["inputSchema"]["type"] == "object")

    text = ("<p>PRINCIPIOS</p>"
            "<p>La sociedad tratará los datos conforme a la Ley 1581.</p>"
            "<p>Se solicitará la autorización previa, expresa e informada del titular. "
            "Se conservará la prueba de dichas autorizaciones.</p>")
    r = handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "triage", "arguments": {"text": text, "type": "html", "api": False}}})
    content = json.loads(r["result"]["content"][0]["text"])
    check("call triage no-error", r["result"].get("isError") is False)
    check("call triage n_clauses>=1", content.get("n_clauses", 0) >= 1)
    check("call triage gate SKIPPED", content.get("gate") == "SKIPPED (api=false)")

    r = handle_message({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                        "params": {"name": "triage", "arguments": {}}})
    check("call sin args -> isError", r["result"]["isError"] is True)
    r = handle_message({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                        "params": {"name": "nope", "arguments": {}}})
    check("tool desconocida -> isError", r["result"]["isError"] is True)
    r = handle_message({"jsonrpc": "2.0", "id": 7, "method": "bogus"})
    check("método desconocido -> -32601", r["error"]["code"] == -32601)
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="jev-norma MCP server (stdio, stdlib puro)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return 0 if selftest() else 1
    serve_stdio()
    return 0


if __name__ == "__main__":
    sys.exit(main())