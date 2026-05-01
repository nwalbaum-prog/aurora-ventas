# tests/test_pos_venta.py
import json

def _abrir_turno(tc):
    tc.post('/api/pos/turno/abrir', json={'monto_inicial': 30000},
            content_type='application/json')

def test_venta_efectivo_basica(client):
    tc, app_mod = client
    _abrir_turno(tc)
    with app_mod.db() as c:
        prod = c.execute("SELECT id FROM productos WHERE nombre='Marraqueta'").fetchone()

    r = tc.post('/api/pos/venta', content_type='application/json', json={
        'items': [{'producto_id': prod['id'], 'nombre': 'Marraqueta',
                   'cantidad': 6, 'precio_unitario': 200}],
        'metodo_pago': 'efectivo',
        'monto_efectivo': 2000,
        'total': 1200
    })
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data['ok'] is True
    assert data['vuelto'] == 800

def test_venta_descuenta_stock(client):
    tc, app_mod = client
    _abrir_turno(tc)
    with app_mod.db() as c:
        prod = c.execute("SELECT id, stock FROM productos WHERE nombre='Marraqueta'").fetchone()
        stock_inicial = prod['stock']

    tc.post('/api/pos/venta', content_type='application/json', json={
        'items': [{'producto_id': prod['id'], 'nombre': 'Marraqueta',
                   'cantidad': 3, 'precio_unitario': 200}],
        'metodo_pago': 'tarjeta',
        'monto_efectivo': 0,
        'total': 600
    })
    with app_mod.db() as c:
        nuevo_stock = c.execute("SELECT stock FROM productos WHERE id=?", (prod['id'],)).fetchone()['stock']
    assert nuevo_stock == stock_inicial - 3

def test_venta_sin_turno_da_error(client):
    tc, app_mod = client
    with app_mod.db() as c:
        prod = c.execute("SELECT id FROM productos WHERE nombre='Marraqueta'").fetchone()
    r = tc.post('/api/pos/venta', content_type='application/json', json={
        'items': [{'producto_id': prod['id'], 'nombre': 'Marraqueta',
                   'cantidad': 1, 'precio_unitario': 200}],
        'metodo_pago': 'efectivo',
        'monto_efectivo': 200,
        'total': 200
    })
    assert r.status_code == 400

def test_venta_sin_items_da_error(client):
    tc, _ = client
    _abrir_turno(tc)
    r = tc.post('/api/pos/venta', content_type='application/json', json={
        'items': [], 'metodo_pago': 'efectivo', 'monto_efectivo': 0, 'total': 0
    })
    assert r.status_code == 400
