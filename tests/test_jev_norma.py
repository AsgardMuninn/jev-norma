"""Tests del paquete jev-norma (TDD, sin API)."""

import json
from pathlib import Path

from jev_norma.gate import build_questions, load_key, sanitize_clause, split_chunks
from jev_norma.mcp import TOOL_SCHEMA, handle_message
from jev_norma.normalizer import (
    MAX_CLAUSE_CHARS,
    normalize_clauses_general,
    segment_plain_text,
)
from jev_norma.pipeline import (
    GATE_UMBRAL,
    build_report,
    detect_input,
    normalize_document,
    sample_indices,
)

FIXTURE_WP = """<html><head><title>Politica de tratamiento</title></head><body>
<nav><p>Inicio Productos Contacto Noticias</p></nav>
<div id="wrap">
<article class="post-content">
<h2>POLITICA DE TRATAMIENTO DE DATOS PERSONALES</h2>
<p>La sociedad XYZ S.A.S. tratará los datos de conformidad con la Ley 1581 de 2012 y el Decreto 1377.</p>
<p>Se solicitará la autorización previa, expresa e informada del titular.</p>
<ul><li>Conservará la prueba de dichas autorizaciones.</li><li>Informará las finalidades del tratamiento.</li></ul>
<h2>AUTORIZACIONES Y FINALIDADES</h2>
<p>La autorización también podrá obtenerse a partir de conductas inequívocas del titular.</p>
<p>Dirección: Carrera 15 # 10-20, Bogotá. Correo: contacto@xyz.co. NIT: 900.123.456-8.</p>
</article>
</div>
<footer><p>© 2026 XYZ S.A.S. Todos los derechos reservados</p></footer>
</body></html>"""


def test_detect_input():
    assert detect_input(Path("p.html")) == "html"
    assert detect_input(Path("p.HTML")) == "html"
    assert detect_input(Path("p.pdf")) == "pdf"
    assert detect_input(Path("p.txt")) == "desconocido"


def test_normalize_general_headers_no_clause():
    r = normalize_clauses_general(FIXTURE_WP)
    joined = " ".join(r["clauses"]).lower()
    assert "política de tratamiento de datos personales" not in joined
    assert "autorizaciones y finalidades" not in joined
    assert len(r["headers"]) == 2


def test_normalize_general_p_li_merged():
    r = normalize_clauses_general(FIXTURE_WP)
    fused = [c for c in r["clauses"] if "autorización previa" in c and "Conservará" in c and "Informará" in c]
    assert len(fused) == 1


def test_normalize_general_boilerplate_dropped():
    r = normalize_clauses_general(FIXTURE_WP)
    joined = " ".join(r["clauses"]).lower()
    for bad in ["inicio productos", "© 2026", "carrera 15", "nit:", "contacto@xyz"]:
        assert bad not in joined


def test_normalize_general_counts():
    r = normalize_clauses_general(FIXTURE_WP)
    assert r["stats"]["n_clauses_final"] == 3
    assert r["stats"]["latencia_ms"] < 50


def test_segment_plain_text_pdf():
    tx = ("NIT: 901.633.276-0\n\n"
          "La sociedad tratará los datos conforme a la Ley 1581 y el Decreto 1377.\n\n"
          "Se solicitará la autorización previa, expresa e informada del titular. "
          "Se conservará la prueba de dichas autorizaciones.\n\n"
          "Carrera 48 # 18A-14, Medellín")
    r = segment_plain_text(tx)
    joined = " ".join(r["clauses"]).lower()
    assert "tratará los datos conforme" in joined
    assert "autorización previa" in joined and "conservará la prueba" in joined
    assert "nit:" not in joined
    assert "carrera 48" not in joined
    assert r["stats"]["n_clauses_final"] == 2


def test_build_questions_self_contained():
    qs = build_questions({"a": "El responsable conservará la prueba de la autorización."})
    instr = qs["a"]["instructions"]
    assert "Cláusula [a]" in instr
    assert "El responsable conservará la prueba" in instr  # cláusula embebida (self-contained)
    assert qs["a"]["type"] == "choice"
    assert set(qs["a"]["criteria"]) == {"obligacion", "riesgo", "sin_accion"}


def test_split_chunks():
    chunks = list(split_chunks({"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}, 2))
    assert [len(c) for c in chunks] == [2, 2, 1]


def test_sanitize_clause():
    assert sanitize_clause("x" * 100).endswith("…")
    assert sanitize_clause("corto") == "corto"


def test_build_report_fail_open():
    rep = build_report(
        {"a": "xx", "b": "yy", "c": "zz"},
        {"a": {"bucket": "obligacion", "confianza": 0.95},
         "b": {"bucket": "riesgo", "confianza": 0.42},
         "c": {"bucket": "sin_accion", "confianza": 0.90}},
        1000, 25)
    assert rep["por_bucket"] == {"obligacion": 1, "riesgo": 1, "sin_accion": 1}
    assert rep["clausulas_a_revision"] == ["b"]
    assert rep["umbral_gate_humano"] == GATE_UMBRAL
    assert rep["costo_usd_estimado"] == round(1000 * 42e-9, 6)


def test_sample_indices():
    assert sample_indices(list(range(10)), 4) == [0, 2, 4, 6]
    assert sample_indices(list(range(3)), 0) == [0, 1, 2]
    assert sample_indices(list(range(3)), 99) == [0, 1, 2]


def test_normalize_document_html(tmp_path):
    p = tmp_path / "pol.html"
    p.write_text(FIXTURE_WP, encoding="utf-8")
    r = normalize_document(p)
    assert r["stats"]["n_clauses_final"] == 3


def test_mcp_initialize_and_tools():
    r = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r["id"] == 1
    assert "tools" in r["result"]["capabilities"]
    assert r["result"]["serverInfo"]["name"] == "jev-norma"
    assert handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_mcp_tools_list():
    r = handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = r["result"]["tools"]
    assert len(tools) == 1 and tools[0]["name"] == "triage"
    assert tools[0]["inputSchema"]["type"] == "object"


def test_mcp_triage_no_api():
    text = ("<p>PRINCIPIOS</p>"
            "<p>La sociedad tratará los datos conforme a la Ley 1581.</p>"
            "<p>Se solicitará la autorización previa, expresa e informada del titular. "
            "Se conservará la prueba de dichas autorizaciones.</p>")
    r = handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "triage", "arguments": {"text": text, "type": "html", "api": False}}})
    assert r["result"].get("isError") is False
    content = json.loads(r["result"]["content"][0]["text"])
    assert content.get("n_clauses", 0) >= 1
    assert content.get("gate") == "SKIPPED (api=false)"


def test_mcp_errors():
    r = handle_message({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "triage", "arguments": {}}})
    assert r["result"]["isError"] is True
    r = handle_message({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
    assert r["result"]["isError"] is True
    r = handle_message({"jsonrpc": "2.0", "id": 7, "method": "bogus"})
    assert r["error"]["code"] == -32601


def test_load_key_no_crash():
    # No debe lanzar aunque no exista la key; devuelve str (vacío si no hay).
    assert isinstance(load_key(), str)