# tests/conftest.py
import pytest, os, sys

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    for mod in list(sys.modules.keys()):
        if mod in ('app', 'pos', 'dte'):
            del sys.modules[mod]

    import app as app_mod
    app_mod.init_db()
    app_mod.app.config['TESTING'] = True
    app_mod.app.config['WTF_CSRF_ENABLED'] = False

    with app_mod.db() as c:
        from werkzeug.security import generate_password_hash
        c.execute(
            "INSERT INTO usuarios (nombre,email,password,rol) VALUES (?,?,?,?)",
            ('Cajera', 'cajera@test.cl', generate_password_hash('test123'), 'usuario')
        )
        c.execute(
            "INSERT INTO productos (nombre,precio,costo,stock,activo) VALUES (?,?,?,?,1)",
            ('Marraqueta', 200, 80, 100)
        )
        c.execute(
            "INSERT INTO productos (nombre,precio,costo,stock,activo) VALUES (?,?,?,?,1)",
            ('Kuchen', 3500, 1200, 10)
        )

    with app_mod.app.test_client() as tc:
        tc.post('/login', data={'email': 'cajera@test.cl', 'password': 'test123'},
                follow_redirects=True)
        yield tc, app_mod
