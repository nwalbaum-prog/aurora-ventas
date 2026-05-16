import pytest, json
from datetime import date, timedelta

# ── helpers ────────────────────────────────────────────────────────────────────

def _admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    import sys
    for mod in list(sys.modules.keys()):
        if mod in ('app', 'pos', 'dte'):
            del sys.modules[mod]
    import app as app_mod
    app_mod.init_db()
    app_mod.app.config['TESTING'] = True
    with app_mod.db() as c:
        from werkzeug.security import generate_password_hash
        c.execute(
            "INSERT INTO usuarios (nombre,email,password,rol) VALUES (?,?,?,?)",
            ('Admin', 'admin@test.cl', generate_password_hash('test'), 'admin')
        )
        c.execute(
            "INSERT INTO clientes (nombre,tipo) VALUES (?,?)",
            ('Café Prueba', 'MAYORISTA')
        )
        c.execute(
            "INSERT INTO productos (nombre,precio,precio_mayorista,costo,stock,activo) VALUES (?,?,?,?,?,1)",
            ('Hogaza', 4500, 3500, 900, 100)
        )
        c.execute(
            "INSERT INTO productos (nombre,precio,precio_mayorista,costo,stock,activo) VALUES (?,?,?,?,?,1)",
            ('Pan Molde', 4200, 3200, 700, 100)
        )
    with app_mod.app.test_client() as tc:
        tc.post('/login', data={'email': 'admin@test.cl', 'password': 'test'},
                follow_redirects=True)
        yield tc, app_mod


@pytest.fixture
def admin(tmp_path, monkeypatch):
    yield from _admin_client(tmp_path, monkeypatch)


# ── tests tablas ───────────────────────────────────────────────────────────────

def test_tablas_existen(admin):
    tc, app_mod = admin
    with app_mod.db() as c:
        tablas = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert 'mayorista_pedidos' in tablas
    assert 'mayorista_pedido_lineas' in tablas


def test_crear_pedido_y_lineas(admin):
    tc, app_mod = admin
    with app_mod.db() as c:
        cid = c.execute("SELECT id FROM clientes WHERE tipo='MAYORISTA'").fetchone()['id']
        pid1 = c.execute("SELECT id FROM productos WHERE nombre='Hogaza'").fetchone()['id']
        pid2 = c.execute("SELECT id FROM productos WHERE nombre='Pan Molde'").fetchone()['id']

        ped_id = c.execute(
            "INSERT INTO mayorista_pedidos (cliente_id, dia_despacho) VALUES (?,?)",
            (cid, 'martes')
        ).lastrowid
        c.execute(
            "INSERT INTO mayorista_pedido_lineas (pedido_id, producto_id, cantidad) VALUES (?,?,?)",
            (ped_id, pid1, 5)
        )
        c.execute(
            "INSERT INTO mayorista_pedido_lineas (pedido_id, producto_id, cantidad) VALUES (?,?,?)",
            (ped_id, pid2, 3)
        )

    with app_mod.db() as c:
        ped = c.execute("SELECT * FROM mayorista_pedidos WHERE id=?", (ped_id,)).fetchone()
        lineas = c.execute(
            "SELECT * FROM mayorista_pedido_lineas WHERE pedido_id=?", (ped_id,)
        ).fetchall()
    assert ped['dia_despacho'] == 'martes'
    assert ped['activo'] == 1
    assert len(lineas) == 2


def test_cascade_delete_lineas(admin):
    tc, app_mod = admin
    with app_mod.db() as c:
        cid = c.execute("SELECT id FROM clientes WHERE tipo='MAYORISTA'").fetchone()['id']
        pid = c.execute("SELECT id FROM productos LIMIT 1").fetchone()['id']
        ped_id = c.execute(
            "INSERT INTO mayorista_pedidos (cliente_id, dia_despacho) VALUES (?,?)",
            (cid, 'jueves')
        ).lastrowid
        c.execute(
            "INSERT INTO mayorista_pedido_lineas (pedido_id, producto_id, cantidad) VALUES (?,?,?)",
            (ped_id, pid, 2)
        )
    with app_mod.db() as c:
        c.execute("DELETE FROM mayorista_pedidos WHERE id=?", (ped_id,))
    with app_mod.db() as c:
        lineas = c.execute(
            "SELECT * FROM mayorista_pedido_lineas WHERE pedido_id=?", (ped_id,)
        ).fetchall()
    assert len(lineas) == 0

# ── tests API ──────────────────────────────────────────────────────────────────

def test_get_mayoristas_vacio(admin):
    tc, _ = admin
    r = tc.get('/api/mayoristas')
    assert r.status_code == 200
    assert r.get_json() == []


def test_get_mayoristas_lista(admin):
    tc, app_mod = admin
    with app_mod.db() as c:
        cid = c.execute("SELECT id FROM clientes WHERE tipo='MAYORISTA'").fetchone()['id']
        pid = c.execute("SELECT id FROM productos LIMIT 1").fetchone()['id']
        ped_id = c.execute(
            "INSERT INTO mayorista_pedidos (cliente_id, dia_despacho) VALUES (?,?)",
            (cid, 'martes')
        ).lastrowid
        c.execute(
            "INSERT INTO mayorista_pedido_lineas (pedido_id, producto_id, cantidad) VALUES (?,?,?)",
            (ped_id, pid, 5)
        )
    r = tc.get('/api/mayoristas')
    data = r.get_json()
    assert len(data) == 1
    assert data[0]['nombre'] == 'Café Prueba'


def test_guardar_pedidos(admin):
    tc, app_mod = admin
    with app_mod.db() as c:
        cid = c.execute("SELECT id FROM clientes WHERE tipo='MAYORISTA'").fetchone()['id']
        pid1 = c.execute("SELECT id FROM productos WHERE nombre='Hogaza'").fetchone()['id']
        pid2 = c.execute("SELECT id FROM productos WHERE nombre='Pan Molde'").fetchone()['id']

    payload = {
        'pedidos': [
            {
                'dia_despacho': 'martes',
                'activo': True,
                'notas': '',
                'lineas': [
                    {'producto_id': pid1, 'cantidad': 5},
                    {'producto_id': pid2, 'cantidad': 3},
                ]
            }
        ]
    }
    r = tc.put(f'/api/mayoristas/{cid}/pedidos',
               data=json.dumps(payload), content_type='application/json')
    assert r.status_code == 200

    with app_mod.db() as c:
        pedidos = c.execute(
            "SELECT * FROM mayorista_pedidos WHERE cliente_id=?", (cid,)
        ).fetchall()
        assert len(pedidos) == 1
        lineas = c.execute(
            "SELECT * FROM mayorista_pedido_lineas WHERE pedido_id=?", (pedidos[0]['id'],)
        ).fetchall()
        assert len(lineas) == 2


def test_generar_semana_crea_venta(admin):
    tc, app_mod = admin
    with app_mod.db() as c:
        cid = c.execute("SELECT id FROM clientes WHERE tipo='MAYORISTA'").fetchone()['id']
        pid = c.execute("SELECT id FROM productos WHERE nombre='Hogaza'").fetchone()['id']
        ped_id = c.execute(
            "INSERT INTO mayorista_pedidos (cliente_id, dia_despacho, activo) VALUES (?,?,1)",
            (cid, 'martes')
        ).lastrowid
        c.execute(
            "INSERT INTO mayorista_pedido_lineas (pedido_id, producto_id, cantidad) VALUES (?,?,?)",
            (ped_id, pid, 4)
        )

    r = tc.post('/api/mayoristas/generar-semana',
                data='{}', content_type='application/json')
    assert r.status_code == 200
    data = r.get_json()
    assert data['generadas'] >= 1

    with app_mod.db() as c:
        ventas = c.execute(
            "SELECT * FROM ventas WHERE canal='MAYORISTA' AND cliente_id=?", (cid,)
        ).fetchall()
    assert len(ventas) == 1
    assert ventas[0]['estado_despacho'] == 'PENDIENTE'


def test_generar_semana_idempotente(admin):
    tc, app_mod = admin
    with app_mod.db() as c:
        cid = c.execute("SELECT id FROM clientes WHERE tipo='MAYORISTA'").fetchone()['id']
        pid = c.execute("SELECT id FROM productos WHERE nombre='Hogaza'").fetchone()['id']
        ped_id = c.execute(
            "INSERT INTO mayorista_pedidos (cliente_id, dia_despacho, activo) VALUES (?,?,1)",
            (cid, 'martes')
        ).lastrowid
        c.execute(
            "INSERT INTO mayorista_pedido_lineas (pedido_id, producto_id, cantidad) VALUES (?,?,?)",
            (ped_id, pid, 4)
        )

    tc.post('/api/mayoristas/generar-semana',
            data='{}', content_type='application/json')
    tc.post('/api/mayoristas/generar-semana',
            data='{}', content_type='application/json')

    with app_mod.db() as c:
        ventas = c.execute(
            "SELECT * FROM ventas WHERE canal='MAYORISTA' AND cliente_id=?", (cid,)
        ).fetchall()
    assert len(ventas) == 1
