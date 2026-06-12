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
