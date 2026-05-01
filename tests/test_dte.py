# tests/test_dte.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import json
import dte

ITEMS = [
    {"nombre": "Marraqueta", "cantidad": 6,  "precio_unitario": 200},
    {"nombre": "Kuchen",     "cantidad": 1,  "precio_unitario": 3500},
]

def test_sin_token_retorna_error():
    result = dte.emit_boleta(ITEMS, 4700, {})
    assert result["ok"] is False
    assert result["error"] == "DTE no configurado"
    assert result["folio"] is None

def test_sin_token_con_clave_vacia():
    result = dte.emit_boleta(ITEMS, 4700, {"bsale_token": ""})
    assert result["ok"] is False
    assert result["error"] == "DTE no configurado"

def test_retorna_estructura_correcta_en_error_red(monkeypatch):
    def mock_urlopen(*args, **kwargs):
        raise Exception("Connection refused")
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    result = dte.emit_boleta(ITEMS, 4700, {"bsale_token": "fake_token"})
    assert result["ok"] is False
    assert result["folio"] is None
    assert "Connection refused" in result["error"]

def test_exito_con_respuesta_bsale(monkeypatch):
    import urllib.request
    fake_response_body = json.dumps({
        "number": 42,
        "urlPdf": "https://bsale.io/boleta/42.pdf"
    }).encode('utf-8')

    class FakeResponse:
        def read(self): return fake_response_body
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: FakeResponse())
    result = dte.emit_boleta(ITEMS, 4700, {"bsale_token": "real_token"})
    assert result["ok"] is True
    assert result["folio"] == 42
    assert result["numero"] == "B-42"
    assert result["pdf_url"] == "https://bsale.io/boleta/42.pdf"
    assert result["error"] is None
