# tests/test_sucursales.py
import pytest


def test_migracion_sucursales(client):
    tc, app_mod = client
    with app_mod.db() as c:
        rows = c.execute("SELECT id, nombre FROM sucursales ORDER BY id").fetchall()
        assert len(rows) == 2
        assert rows[0]['nombre'] == 'Recoleta'
        cols_ventas = [r['name'] for r in c.execute("PRAGMA table_info(ventas)")]
        assert 'sucursal_id' in cols_ventas
        for tabla in ('pos_turnos', 'gastos', 'producto_lotes', 'inventario', 'usuarios'):
            cols = [r['name'] for r in c.execute(f"PRAGMA table_info({tabla})")]
            assert 'sucursal_id' in cols, tabla
        # inventario permite el mismo producto en 2 sucursales
        c.execute("""INSERT INTO inventario (ingrediente, bodega, stock_kg, producto_id, sucursal_id)
                     VALUES ('Marraqueta', 'productos_terminados', 5, 1, 2)""")


def test_api_sucursales(client):
    tc, app_mod = client
    r = tc.get('/api/sucursales')
    assert r.status_code == 200
    d = r.get_json()
    assert len(d['sucursales']) == 2
    assert d['fija'] is None  # cajera del conftest no tiene sucursal fija


def _crear_stock(app_mod, pid, sucursal, cantidad):
    """Crea stock directo en una sucursal (lote + inventario + total)."""
    with app_mod.db() as c:
        c.execute("""INSERT INTO producto_lotes (producto_id, sucursal_id, fecha_elaboracion,
                     cantidad_inicial, cantidad_actual) VALUES (?,?,date('now'),?,?)""",
                  (pid, sucursal, cantidad, cantidad))
        inv = c.execute("""SELECT id FROM inventario WHERE bodega='productos_terminados'
                           AND producto_id=? AND sucursal_id=?""", (pid, sucursal)).fetchone()
        if inv:
            c.execute("UPDATE inventario SET stock_kg=stock_kg+? WHERE id=?", (cantidad, inv['id']))
        else:
            c.execute("""INSERT INTO inventario (ingrediente, bodega, stock_kg, unidad, producto_id, sucursal_id)
                         SELECT nombre, 'productos_terminados', ?, 'unidades', id, ? FROM productos WHERE id=?""",
                      (cantidad, sucursal, pid))
        c.execute("UPDATE productos SET stock=stock+? WHERE id=?", (cantidad, pid))


def _stock(app_mod, pid, sucursal):
    with app_mod.db() as c:
        inv = c.execute("""SELECT stock_kg FROM inventario WHERE bodega='productos_terminados'
                           AND producto_id=? AND sucursal_id=?""", (pid, sucursal)).fetchone()
        lotes = c.execute("""SELECT COALESCE(SUM(cantidad_actual),0) FROM producto_lotes
                             WHERE producto_id=? AND sucursal_id=?""", (pid, sucursal)).fetchone()[0]
        total = c.execute("SELECT stock FROM productos WHERE id=?", (pid,)).fetchone()['stock']
    return (inv['stock_kg'] if inv else 0, lotes, total)


def test_venta_descuenta_solo_su_sucursal(client):
    tc, app_mod = client
    _crear_stock(app_mod, 1, 2, 10)  # 10 unidades en Local 2 (seed deja stock inicial en suc 1)
    inv1_antes, _, total_antes = _stock(app_mod, 1, 1)
    r = tc.post('/api/ventas', json={'sucursal_id': 2,
                                     'items': [{'producto_id': 1, 'cantidad': 4, 'precio_unitario': 1000}]})
    assert r.status_code == 201
    inv2, lotes2, total = _stock(app_mod, 1, 2)
    assert inv2 == 6 and lotes2 == 6
    inv1, _, _ = _stock(app_mod, 1, 1)
    assert inv1 == inv1_antes            # sucursal 1 intacta
    assert total == total_antes - 4      # total baja
    with app_mod.db() as c:
        v = c.execute("SELECT sucursal_id FROM ventas ORDER BY id DESC LIMIT 1").fetchone()
    assert v['sucursal_id'] == 2


def test_produccion_manual_suma_a_sucursal_1(client):
    tc, app_mod = client
    r = tc.post('/api/produccion/manual', json={'producto_id': 1, 'cantidad': 3})
    assert r.status_code in (200, 201)
    with app_mod.db() as c:
        lote = c.execute(
            "SELECT sucursal_id FROM producto_lotes WHERE producto_id=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert lote['sucursal_id'] == 1
