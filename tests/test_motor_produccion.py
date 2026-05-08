"""
Tests del motor de cálculo de producción (reverse scheduling + baker's %).
Usa SQLite en memoria — no toca aurora.db.
"""
import sys, os, json, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def make_db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY, nombre TEXT, peso_unitario_kg REAL DEFAULT 0,
            stock REAL DEFAULT 0, activo INTEGER DEFAULT 1,
            masa_base TEXT DEFAULT '', baking_loss_pct REAL DEFAULT 0,
            merma_tecnica_pct REAL DEFAULT 0
        );
        CREATE TABLE ventas (
            id INTEGER PRIMARY KEY, fecha TEXT, fecha_despacho TEXT,
            estado_despacho TEXT DEFAULT 'PENDIENTE', canal TEXT DEFAULT 'local',
            total REAL DEFAULT 0
        );
        CREATE TABLE venta_items (
            id INTEGER PRIMARY KEY, venta_id INTEGER, producto_id INTEGER, cantidad REAL
        );
        CREATE TABLE recetas (
            id INTEGER PRIMARY KEY, producto_id INTEGER, ingrediente TEXT,
            porcentaje REAL, inventario_id INTEGER
        );
        CREATE TABLE inventario (
            id INTEGER PRIMARY KEY, ingrediente TEXT, bodega TEXT,
            stock_kg REAL DEFAULT 0, alerta_minimo_kg REAL DEFAULT 0,
            producto_id INTEGER
        );
    """)
    conn.commit()
    return conn

def seed_base(conn):
    c = conn.cursor()
    c.execute("INSERT INTO productos VALUES (1,'Hogaza Campesina',0.8,0,1,'Masa Madre Trigo',15.0,2.0)")
    c.execute("INSERT INTO productos VALUES (2,'Baguette',0.35,0,1,'Masa Madre Trigo',15.0,2.0)")
    c.execute("INSERT INTO productos VALUES (3,'Pan Pita',0.15,0,1,'',0,0)")
    c.execute("INSERT INTO recetas VALUES (1,1,'Harina Blanca',60,1)")
    c.execute("INSERT INTO recetas VALUES (2,1,'Harina Integral',40,2)")
    c.execute("INSERT INTO recetas VALUES (3,1,'Agua',75,3)")
    c.execute("INSERT INTO recetas VALUES (4,1,'Sal',2,4)")
    c.execute("INSERT INTO inventario VALUES (1,'Harina Blanca','ingredientes',25.0,2.0,NULL)")
    c.execute("INSERT INTO inventario VALUES (2,'Harina Integral','ingredientes',8.0,2.0,NULL)")
    c.execute("INSERT INTO inventario VALUES (3,'Agua','ingredientes',50.0,5.0,NULL)")
    c.execute("INSERT INTO inventario VALUES (4,'Sal','ingredientes',0.1,0.5,NULL)")
    c.execute("INSERT INTO ventas VALUES (1,'2026-05-08','2026-05-09','PENDIENTE','delivery',0)")
    c.execute("INSERT INTO venta_items VALUES (1,1,1,10)")
    c.execute("INSERT INTO venta_items VALUES (2,1,2,20)")
    c.execute("INSERT INTO venta_items VALUES (3,1,3,5)")
    conn.commit()


def test_masa_final_correcta():
    conn = make_db(); seed_base(conn)
    from app import _calcular_orden_produccion
    resultado = _calcular_orden_produccion(conn.cursor(), '2026-05-09')
    orden = resultado['ordenes'][0]
    assert abs(orden['masa_final_kg'] - 15.0) < 0.01, f"masa_final esperada 15.0, got {orden['masa_final_kg']}"
    print("PASS test_masa_final_correcta")
    conn.close()


def test_reverse_scheduling_math():
    conn = make_db(); seed_base(conn)
    from app import _calcular_orden_produccion
    orden = _calcular_orden_produccion(conn.cursor(), '2026-05-09')['ordenes'][0]
    assert abs(orden['masa_cruda_kg'] - 17.647) < 0.01, f"masa_cruda esperada ~17.647, got {orden['masa_cruda_kg']}"
    assert abs(orden['masa_amasar_kg'] - 18.0) < 0.01, f"masa_amasar esperada ~18.0, got {orden['masa_amasar_kg']}"
    print("PASS test_reverse_scheduling_math")
    conn.close()


def test_baker_percentage_scale():
    conn = make_db(); seed_base(conn)
    from app import _calcular_orden_produccion
    ings = _calcular_orden_produccion(conn.cursor(), '2026-05-09')['ordenes'][0]['ingredientes']
    ing_map = {i['nombre']: i for i in ings}
    masa_amasar = 18.0
    suma_pct = 177
    scale = masa_amasar / suma_pct
    assert abs(ing_map['Harina Blanca']['kg'] - 60 * scale) < 0.01
    assert abs(ing_map['Sal']['kg'] - 2 * scale) < 0.01
    print("PASS test_baker_percentage_scale")
    conn.close()


def test_sin_masa_base_separados():
    conn = make_db(); seed_base(conn)
    from app import _calcular_orden_produccion
    resultado = _calcular_orden_produccion(conn.cursor(), '2026-05-09')
    nombres_sin = [p['nombre'] for p in resultado['sin_masa_base']]
    assert 'Pan Pita' in nombres_sin, f"Pan Pita debe estar en sin_masa_base, got {nombres_sin}"
    print("PASS test_sin_masa_base_separados")
    conn.close()


def test_alerta_stock_insuficiente():
    conn = make_db(); seed_base(conn)
    from app import _calcular_orden_produccion
    orden = _calcular_orden_produccion(conn.cursor(), '2026-05-09')['ordenes'][0]
    sal = next(i for i in orden['ingredientes'] if i['nombre'] == 'Sal')
    assert sal['suficiente'] is False, "Sal debería ser insuficiente"
    assert orden['alerta_stock'] is True, "alerta_stock debería ser True"
    print("PASS test_alerta_stock_insuficiente")
    conn.close()


def test_sin_ventas_retorna_vacio():
    conn = make_db(); seed_base(conn)
    from app import _calcular_orden_produccion
    resultado = _calcular_orden_produccion(conn.cursor(), '2099-01-01')
    assert resultado['ordenes'] == [], f"esperado [], got {resultado['ordenes']}"
    assert resultado['sin_masa_base'] == []
    print("PASS test_sin_ventas_retorna_vacio")
    conn.close()


if __name__ == '__main__':
    test_masa_final_correcta()
    test_reverse_scheduling_math()
    test_baker_percentage_scale()
    test_sin_masa_base_separados()
    test_alerta_stock_insuficiente()
    test_sin_ventas_retorna_vacio()
    print("\nTodos los tests pasaron.")
