from werkzeug.security import generate_password_hash


def login_admin(tc, app_mod):
    with app_mod.db() as c:
        c.execute(
            "INSERT OR IGNORE INTO usuarios (nombre,email,password,rol) VALUES (?,?,?,?)",
            ('Admin', 'admin@test.cl', generate_password_hash('test123'), 'admin')
        )
    tc.post('/login', data={'email': 'admin@test.cl', 'password': 'test123'},
            follow_redirects=True)


def test_resumen_hoy(client):
    tc, app_mod = client
    login_admin(tc, app_mod)
    r = tc.get('/api/reportes/resumen?periodo=hoy')
    assert r.status_code == 200
    data = r.get_json()
    assert 'ventas_hoy' in data
    assert 'por_canal' in data
    assert 'ingresos_cobrados' in data
    assert 'gastos' in data
    assert 'por_cobrar' in data
    assert 'despachos_pendientes_hoy' in data
    assert 'stock_bajo' in data


def test_resumen_semana(client):
    tc, app_mod = client
    login_admin(tc, app_mod)
    r = tc.get('/api/reportes/resumen?periodo=semana')
    assert r.status_code == 200
    data = r.get_json()
    assert 'ventas_periodo' in data


def test_resumen_mes(client):
    tc, app_mod = client
    login_admin(tc, app_mod)
    r = tc.get('/api/reportes/resumen?periodo=mes')
    assert r.status_code == 200


def test_reporte_produccion(client):
    tc, app_mod = client
    login_admin(tc, app_mod)
    r = tc.get('/api/reportes/produccion?periodo=mes')
    assert r.status_code == 200
    data = r.get_json()
    assert 'kpis' in data
    assert 'por_dia' in data
    assert 'por_producto' in data
    assert 'total_planificado' in data['kpis']
    assert 'total_producido' in data['kpis']


def test_reporte_despacho(client):
    tc, app_mod = client
    login_admin(tc, app_mod)
    r = tc.get('/api/reportes/despacho?periodo=mes')
    assert r.status_code == 200
    data = r.get_json()
    assert 'kpis' in data
    assert 'por_dia' in data
    assert 'por_canal' in data
    assert 'total' in data['kpis']
