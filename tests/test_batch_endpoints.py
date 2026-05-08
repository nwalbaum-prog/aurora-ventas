"""Tests de los endpoints amasar y hornear de batch."""
import sys, os, json, sqlite3, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def get_client():
    import app as _app
    _app.app.config['TESTING'] = True
    client = _app.app.test_client()
    # Inject session directly — avoids password dependency on real DB
    with _app.db() as c:
        u = c.execute("SELECT id FROM usuarios WHERE email='admin@aurorabakers.cl' LIMIT 1").fetchone()
        uid = u['id'] if u else 1
    with client.session_transaction() as sess:
        sess['user_id']      = uid
        sess['user_nombre']  = 'Admin'
        sess['user_rol']     = 'admin'
        sess['user_permisos'] = []
    return client, _app


def seed_batch(estado='pendiente'):
    """Inserta un batch de prueba en aurora.db y retorna su batch_id."""
    import app as _app
    batch_id = str(uuid.uuid4())
    ings = json.dumps([
        {'nombre': 'Harina Blanca', 'kg': 5.0, 'inventario_id': None, 'suficiente': True}
    ])
    with _app.db() as c:
        # Asegurar que existe un producto con masa_base
        prod = c.execute("SELECT id FROM productos WHERE masa_base != '' LIMIT 1").fetchone()
        prod_id = prod['id'] if prod else 1
        c.execute("""
            INSERT INTO plan_produccion
                (fecha, codigo_producto, nombre_producto, cantidad, estado, producto_id,
                 fecha_amasado, fecha_horneado, batch_id, ingredientes_json, notas)
            VALUES (date('now'), 'TEST-HOG', 'Test Hogaza', 5, ?, ?, date('now'), date('now','+1 day'), ?, ?, '')
        """, (estado, prod_id, batch_id, ings))
    return batch_id


def test_amasar_pendiente_ok():
    """Un batch pendiente puede pasar a amasado."""
    client, _app = get_client()
    batch_id = seed_batch('pendiente')
    r = client.post(f'/api/produccion/batch/{batch_id}/amasar')
    data = json.loads(r.data)
    assert r.status_code == 200, f"esperado 200, got {r.status_code}: {data}"
    assert data.get('ok') is True, f"esperado ok=True, got {data}"
    with _app.db() as c:
        estado = c.execute(
            "SELECT estado FROM plan_produccion WHERE batch_id=? LIMIT 1", (batch_id,)
        ).fetchone()['estado']
    assert estado == 'amasado', f"estado esperado 'amasado', got '{estado}'"
    print("PASS test_amasar_pendiente_ok")


def test_amasar_ya_amasado_rechaza():
    """Un batch en estado amasado no puede amasarse de nuevo."""
    client, _ = get_client()
    batch_id = seed_batch('amasado')
    r = client.post(f'/api/produccion/batch/{batch_id}/amasar')
    assert r.status_code == 400, f"esperado 400, got {r.status_code}"
    print("PASS test_amasar_ya_amasado_rechaza")


def test_hornear_amasado_ok():
    """Un batch amasado puede pasar a horneado."""
    client, _app = get_client()
    batch_id = seed_batch('amasado')
    r = client.post(f'/api/produccion/batch/{batch_id}/hornear')
    data = json.loads(r.data)
    assert r.status_code == 200, f"esperado 200, got {r.status_code}: {data}"
    assert data.get('ok') is True
    with _app.db() as c:
        estado = c.execute(
            "SELECT estado FROM plan_produccion WHERE batch_id=? LIMIT 1", (batch_id,)
        ).fetchone()['estado']
    assert estado == 'horneado', f"estado esperado 'horneado', got '{estado}'"
    print("PASS test_hornear_amasado_ok")


def test_hornear_pendiente_rechaza():
    """Un batch pendiente no puede hornearse sin amasar primero."""
    client, _ = get_client()
    batch_id = seed_batch('pendiente')
    r = client.post(f'/api/produccion/batch/{batch_id}/hornear')
    assert r.status_code == 400, f"esperado 400, got {r.status_code}"
    data = json.loads(r.data)
    assert 'amasado' in data.get('error', '').lower(), f"mensaje debe mencionar amasado: {data}"
    print("PASS test_hornear_pendiente_rechaza")


def test_hornear_inexistente_404():
    """Batch inexistente retorna 404."""
    client, _ = get_client()
    r = client.post('/api/produccion/batch/no-existe-uuid/hornear')
    assert r.status_code == 404, f"esperado 404, got {r.status_code}"
    print("PASS test_hornear_inexistente_404")


if __name__ == '__main__':
    test_amasar_pendiente_ok()
    test_amasar_ya_amasado_rechaza()
    test_hornear_amasado_ok()
    test_hornear_pendiente_rechaza()
    test_hornear_inexistente_404()
    print("\nTodos los tests de batch pasaron.")
