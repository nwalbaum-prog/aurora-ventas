# Producción Reverse Scheduling — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adaptar el módulo de producción para panadería de fermentación larga: cálculo inverso desde ventas del día siguiente, agrupación por masa base, y flujo de dos pasos amasado → horneado con triggers de inventario separados.

**Architecture:** Se extiende `plan_produccion` con 4 columnas nuevas y `productos` con 3. Un helper puro `_calcular_orden_produccion()` en `app.py` contiene toda la matemática (baker's %, reverse scheduling). Cinco nuevos endpoints REST consumen ese helper. La UI de `/produccion` se reescribe como timeline de dos secciones.

**Tech Stack:** Python 3, Flask, SQLite (sqlite3), Jinja2, Vanilla JS, Bootstrap Icons. Sin dependencias nuevas. Tests: scripts Python standalone con SQLite en memoria.

---

## Mapa de Archivos

| Archivo | Acción | Responsabilidad |
|---------|--------|-----------------|
| `app.py` | Modificar | Migraciones init_db, helper `_calcular_orden_produccion`, 5 endpoints nuevos, refactor `api_plan_list` |
| `templates/produccion.html` | Reescribir | Timeline UI: secciones amasar/hornear, modal preview generar plan |
| `templates/productos.html` | Modificar | Añadir campos masa_base, baking_loss_pct, merma_tecnica_pct al modal |
| `tests/test_motor_produccion.py` | Crear | Tests del motor de cálculo matemático |
| `tests/test_batch_endpoints.py` | Crear | Tests de endpoints amasar/hornear |

---

## Task 1: Migraciones de Schema

**Files:**
- Modify: `app.py` (función `init_db`, sección de migrations list ~línea 341)

- [ ] **Step 1.1: Añadir las 7 columnas nuevas a la lista de migrations en `init_db()`**

Localiza la lista `migrations = [...]` en `init_db()` (alrededor de la línea 341). Añade al final de la lista, antes del cierre `]`:

```python
            # Producción masa madre — reverse scheduling
            ("productos",        "masa_base",          "ALTER TABLE productos ADD COLUMN masa_base TEXT NOT NULL DEFAULT ''"),
            ("productos",        "baking_loss_pct",    "ALTER TABLE productos ADD COLUMN baking_loss_pct REAL NOT NULL DEFAULT 0"),
            ("productos",        "merma_tecnica_pct",  "ALTER TABLE productos ADD COLUMN merma_tecnica_pct REAL NOT NULL DEFAULT 0"),
            ("plan_produccion",  "fecha_amasado",      "ALTER TABLE plan_produccion ADD COLUMN fecha_amasado TEXT NOT NULL DEFAULT ''"),
            ("plan_produccion",  "fecha_horneado",     "ALTER TABLE plan_produccion ADD COLUMN fecha_horneado TEXT NOT NULL DEFAULT ''"),
            ("plan_produccion",  "batch_id",           "ALTER TABLE plan_produccion ADD COLUMN batch_id TEXT NOT NULL DEFAULT ''"),
            ("plan_produccion",  "ingredientes_json",  "ALTER TABLE plan_produccion ADD COLUMN ingredientes_json TEXT NOT NULL DEFAULT '[]'"),
```

- [ ] **Step 1.2: Verificar que las migraciones aplican**

```bash
venv/Scripts/python.exe -c "
import app
import sqlite3
conn = sqlite3.connect('aurora.db')
c = conn.cursor()
cols_prod = [r[1] for r in c.execute('PRAGMA table_info(productos)').fetchall()]
cols_plan = [r[1] for r in c.execute('PRAGMA table_info(plan_produccion)').fetchall()]
assert 'masa_base' in cols_prod, 'FALTA masa_base en productos'
assert 'baking_loss_pct' in cols_prod, 'FALTA baking_loss_pct en productos'
assert 'merma_tecnica_pct' in cols_prod, 'FALTA merma_tecnica_pct en productos'
assert 'fecha_amasado' in cols_plan, 'FALTA fecha_amasado en plan_produccion'
assert 'fecha_horneado' in cols_plan, 'FALTA fecha_horneado en plan_produccion'
assert 'batch_id' in cols_plan, 'FALTA batch_id en plan_produccion'
assert 'ingredientes_json' in cols_plan, 'FALTA ingredientes_json en plan_produccion'
print('OK — todas las columnas presentes')
conn.close()
"
```

Resultado esperado: `OK — todas las columnas presentes`

- [ ] **Step 1.3: Commit**

```bash
git add app.py
git commit -m "feat: schema migrations para reverse scheduling (masa_base, batch_id, fechas)"
```

---

## Task 2: Helper `_calcular_orden_produccion` + Tests

**Files:**
- Modify: `app.py` (insertar función helper cerca de los otros helpers como `_get_or_create_inv_terminado`, alrededor de línea 52)
- Create: `tests/test_motor_produccion.py`

- [ ] **Step 2.1: Crear el archivo de test con DB en memoria**

Crea `tests/test_motor_produccion.py`:

```python
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
    """Inserta datos mínimos: 2 productos con masa_base compartida + receta + venta mañana."""
    c = conn.cursor()
    # Productos: Hogaza y Baguette comparten masa_base "Masa Madre Trigo"
    c.execute("INSERT INTO productos VALUES (1,'Hogaza Campesina',0.8,0,1,'Masa Madre Trigo',15.0,2.0)")
    c.execute("INSERT INTO productos VALUES (2,'Baguette',0.35,0,1,'Masa Madre Trigo',15.0,2.0)")
    # Producto sin masa_base
    c.execute("INSERT INTO productos VALUES (3,'Pan Pita',0.15,0,1,'',0,0)")
    # Receta del producto 1 (baker's %)
    c.execute("INSERT INTO recetas VALUES (1,1,'Harina Blanca',60,1)")
    c.execute("INSERT INTO recetas VALUES (2,1,'Harina Integral',40,2)")
    c.execute("INSERT INTO recetas VALUES (3,1,'Agua',75,3)")
    c.execute("INSERT INTO recetas VALUES (4,1,'Sal',2,4)")
    # Inventario ingredientes
    c.execute("INSERT INTO inventario VALUES (1,'Harina Blanca','ingredientes',25.0,2.0,NULL)")
    c.execute("INSERT INTO inventario VALUES (2,'Harina Integral','ingredientes',8.0,2.0,NULL)")
    c.execute("INSERT INTO inventario VALUES (3,'Agua','ingredientes',50.0,5.0,NULL)")
    c.execute("INSERT INTO inventario VALUES (4,'Sal','ingredientes',0.1,0.5,NULL)")   # insuficiente
    # Venta con fecha_despacho = mañana
    c.execute("INSERT INTO ventas VALUES (1,'2026-05-08','2026-05-09','PENDIENTE','delivery',0)")
    c.execute("INSERT INTO venta_items VALUES (1,1,1,10)")   # 10 Hogaza
    c.execute("INSERT INTO venta_items VALUES (2,1,2,20)")   # 20 Baguette
    c.execute("INSERT INTO venta_items VALUES (3,1,3,5)")    # 5 Pan Pita (sin masa_base)
    conn.commit()


def test_masa_final_correcta():
    """masa_final_kg = Σ(cantidad × peso_unitario_kg)"""
    conn = make_db(); seed_base(conn)
    from app import _calcular_orden_produccion
    resultado = _calcular_orden_produccion(conn.cursor(), '2026-05-09')
    orden = resultado['ordenes'][0]
    # Hogaza: 10 × 0.8 = 8.0, Baguette: 20 × 0.35 = 7.0 → total 15.0
    assert abs(orden['masa_final_kg'] - 15.0) < 0.01, f"masa_final esperada 15.0, got {orden['masa_final_kg']}"
    print("PASS test_masa_final_correcta")
    conn.close()


def test_reverse_scheduling_math():
    """
    masa_cruda  = 15.0 / (1 - 0.15) = 17.647
    masa_amasar = 17.647 * (1 + 0.02) = 18.0
    """
    conn = make_db(); seed_base(conn)
    from app import _calcular_orden_produccion
    orden = _calcular_orden_produccion(conn.cursor(), '2026-05-09')['ordenes'][0]
    assert abs(orden['masa_cruda_kg'] - 17.647) < 0.01, f"masa_cruda esperada ~17.647, got {orden['masa_cruda_kg']}"
    assert abs(orden['masa_amasar_kg'] - 18.0) < 0.01, f"masa_amasar esperada ~18.0, got {orden['masa_amasar_kg']}"
    print("PASS test_reverse_scheduling_math")
    conn.close()


def test_baker_percentage_scale():
    """
    sum_pct = 60+40+75+2 = 177
    scale   = 18.0 / 177 = 0.10169
    harina_blanca = 60 * scale = 6.101 kg
    sal = 2 * scale = 0.203 kg
    """
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
    """Productos sin masa_base van a sin_masa_base[], no a ordenes[]."""
    conn = make_db(); seed_base(conn)
    from app import _calcular_orden_produccion
    resultado = _calcular_orden_produccion(conn.cursor(), '2026-05-09')
    nombres_sin = [p['nombre'] for p in resultado['sin_masa_base']]
    assert 'Pan Pita' in nombres_sin, f"Pan Pita debe estar en sin_masa_base, got {nombres_sin}"
    print("PASS test_sin_masa_base_separados")
    conn.close()


def test_alerta_stock_insuficiente():
    """Sal tiene 0.1 kg, necesita ~0.2 → alerta_stock=True, suficiente=False."""
    conn = make_db(); seed_base(conn)
    from app import _calcular_orden_produccion
    orden = _calcular_orden_produccion(conn.cursor(), '2026-05-09')['ordenes'][0]
    sal = next(i for i in orden['ingredientes'] if i['nombre'] == 'Sal')
    assert sal['suficiente'] is False, "Sal debería ser insuficiente"
    assert orden['alerta_stock'] is True, "alerta_stock debería ser True"
    print("PASS test_alerta_stock_insuficiente")
    conn.close()


def test_sin_ventas_retorna_vacio():
    """Sin ventas para la fecha, ordenes=[] y sin_masa_base=[]."""
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
```

- [ ] **Step 2.2: Ejecutar tests — deben FALLAR porque la función no existe aún**

```bash
venv/Scripts/python.exe tests/test_motor_produccion.py
```

Resultado esperado: `ImportError` o `AttributeError: module 'app' has no attribute '_calcular_orden_produccion'`

- [ ] **Step 2.3: Implementar `_calcular_orden_produccion` en `app.py`**

Añade esta función en `app.py` justo después de `_descontar_lotes_fifo` (alrededor de línea 144):

```python
def _calcular_orden_produccion(c, fecha_horneado: str) -> dict:
    """
    Reverse scheduling: dado fecha_horneado calcula qué amasar hoy.
    Lee ventas con fecha_despacho=fecha_horneado, agrupa por masa_base,
    aplica matemática de baking_loss + merma + baker's percentage.
    No escribe a la DB. c puede ser cursor de sqlite3 directo o de db().
    """
    rows = c.execute("""
        SELECT p.id AS producto_id, p.nombre, p.peso_unitario_kg,
               p.masa_base, p.baking_loss_pct, p.merma_tecnica_pct,
               SUM(vi.cantidad) AS cantidad_total
        FROM venta_items vi
        JOIN ventas v  ON v.id  = vi.venta_id
        JOIN productos p ON p.id = vi.producto_id
        WHERE v.fecha_despacho = ?
          AND v.estado_despacho != 'CANCELADO'
          AND p.activo = 1
        GROUP BY p.id
    """, (fecha_horneado,)).fetchall()

    sin_masa_base = []
    grupos: dict[str, list] = {}
    for r in rows:
        r = dict(r)
        if not r['masa_base']:
            sin_masa_base.append({'nombre': r['nombre'], 'cantidad': r['cantidad_total'],
                                   'advertencia': 'sin masa_base configurada'})
            continue
        grupos.setdefault(r['masa_base'], []).append(r)

    ordenes = []
    for masa_base, productos in grupos.items():
        ref = productos[0]
        baking_loss = ref['baking_loss_pct'] / 100.0
        merma      = ref['merma_tecnica_pct'] / 100.0

        prods_out = []
        masa_final_kg = 0.0
        for p in productos:
            mf = p['cantidad_total'] * p['peso_unitario_kg']
            masa_final_kg += mf
            prods_out.append({
                'producto_id':    p['producto_id'],
                'nombre':         p['nombre'],
                'cantidad':       p['cantidad_total'],
                'peso_unitario_kg': p['peso_unitario_kg'],
                'masa_final_kg':  round(mf, 3),
            })

        masa_cruda_kg  = masa_final_kg / (1 - baking_loss) if baking_loss < 1 else masa_final_kg
        masa_amasar_kg = masa_cruda_kg * (1 + merma)

        # Elegir producto con más ingredientes en receta como referencia del grupo
        receta_prod_id, max_ings = None, -1
        for p in productos:
            cnt = c.execute("SELECT COUNT(*) FROM recetas WHERE producto_id=?",
                            (p['producto_id'],)).fetchone()[0]
            if cnt > max_ings:
                max_ings, receta_prod_id = cnt, p['producto_id']

        ingredientes_out = []
        alerta_stock = False
        if receta_prod_id and max_ings > 0:
            receta_rows = c.execute("""
                SELECT r.ingrediente, r.porcentaje, r.inventario_id,
                       COALESCE(i.stock_kg, 0) AS stock_actual
                FROM recetas r
                LEFT JOIN inventario i ON i.id = r.inventario_id
                WHERE r.producto_id = ?
            """, (receta_prod_id,)).fetchall()

            sum_pct = sum(r['porcentaje'] for r in receta_rows)
            scale   = masa_amasar_kg / sum_pct if sum_pct > 0 else 0.0

            for r in receta_rows:
                kg = round(r['porcentaje'] * scale, 3)
                suficiente = float(r['stock_actual']) >= kg
                if not suficiente:
                    alerta_stock = True
                ingredientes_out.append({
                    'nombre':       r['ingrediente'],
                    'porcentaje':   r['porcentaje'],
                    'kg':           kg,
                    'inventario_id': r['inventario_id'],
                    'stock_actual': round(float(r['stock_actual']), 3),
                    'suficiente':   suficiente,
                })

        ordenes.append({
            'masa_base':         masa_base,
            'productos':         prods_out,
            'masa_final_kg':     round(masa_final_kg, 3),
            'masa_cruda_kg':     round(masa_cruda_kg, 3),
            'masa_amasar_kg':    round(masa_amasar_kg, 3),
            'baking_loss_pct':   ref['baking_loss_pct'],
            'merma_tecnica_pct': ref['merma_tecnica_pct'],
            'ingredientes':      ingredientes_out,
            'alerta_stock':      alerta_stock,
        })

    return {'ordenes': ordenes, 'sin_masa_base': sin_masa_base}
```

- [ ] **Step 2.4: Ejecutar tests — deben PASAR**

```bash
venv/Scripts/python.exe tests/test_motor_produccion.py
```

Resultado esperado:
```
PASS test_masa_final_correcta
PASS test_reverse_scheduling_math
PASS test_baker_percentage_scale
PASS test_sin_masa_base_separados
PASS test_alerta_stock_insuficiente
PASS test_sin_ventas_retorna_vacio

Todos los tests pasaron.
```

- [ ] **Step 2.5: Commit**

```bash
git add app.py tests/test_motor_produccion.py
git commit -m "feat: helper _calcular_orden_produccion con baker% y reverse scheduling"
```

---

## Task 3: `GET /api/produccion/calcular-orden`

**Files:**
- Modify: `app.py` (añadir endpoint después de `api_plan_generar_desde_ventas`, ~línea 3320)

- [ ] **Step 3.1: Añadir el endpoint en `app.py`**

Añade después del último endpoint de plan_produccion existente:

```python
@app.route('/api/produccion/calcular-orden')
@login_required
def api_produccion_calcular_orden():
    """
    Calcula la orden de trabajo de amasado para una fecha_horneado dada.
    Lee ventas con fecha_despacho = fecha_horneado, aplica reverse scheduling.
    Solo lectura — no escribe a DB.
    Query param: fecha_horneado (YYYY-MM-DD, default: mañana)
    """
    from datetime import date, timedelta
    fecha_horneado = request.args.get(
        'fecha_horneado',
        (date.today() + timedelta(days=1)).isoformat()
    )
    fecha_amasado = (
        date.fromisoformat(fecha_horneado) - timedelta(days=1)
    ).isoformat()

    with db() as c:
        resultado = _calcular_orden_produccion(c, fecha_horneado)

    return jsonify({
        'fecha_amasado':  fecha_amasado,
        'fecha_horneado': fecha_horneado,
        **resultado,
    })
```

- [ ] **Step 3.2: Verificar que el endpoint responde correctamente**

```bash
venv/Scripts/python.exe -c "
import app, json
app.app.config['TESTING'] = True
client = app.app.test_client()
# Hacer login como admin
client.post('/login', data={'email':'admin@aurorabakers.cl','password':'aurora2024'}, follow_redirects=True)
r = client.get('/api/produccion/calcular-orden?fecha_horneado=2099-01-01')
data = json.loads(r.data)
assert 'fecha_amasado' in data, 'falta fecha_amasado'
assert 'fecha_horneado' in data, 'falta fecha_horneado'
assert 'ordenes' in data, 'falta ordenes'
assert data['ordenes'] == [], f'sin ventas en 2099, esperado [], got {data[\"ordenes\"]}'
print('OK — endpoint calcular-orden responde correctamente')
"
```

Resultado esperado: `OK — endpoint calcular-orden responde correctamente`

- [ ] **Step 3.3: Commit**

```bash
git add app.py
git commit -m "feat: GET /api/produccion/calcular-orden (reverse scheduling, solo lectura)"
```

---

## Task 4: `POST /api/produccion/generar-plan`

**Files:**
- Modify: `app.py` (añadir endpoint a continuación del Task 3)

- [ ] **Step 4.1: Añadir el endpoint en `app.py`**

```python
@app.route('/api/produccion/generar-plan', methods=['POST'])
@login_required
def api_produccion_generar_plan():
    """
    Genera el plan de producción en plan_produccion a partir del cálculo de calcular-orden.
    Body JSON: { "fecha_horneado": "YYYY-MM-DD" }
    - Reemplaza solo batches en estado 'pendiente' para la fecha_amasado resultante.
    - Batches en estado 'amasado' o 'horneado' nunca se tocan.
    - Cada masa_base recibe un batch_id (UUID4).
    - ingredientes_json se guarda en todas las filas del batch.
    """
    import uuid
    from datetime import date, timedelta
    d = request.get_json(silent=True) or {}
    fecha_horneado = d.get(
        'fecha_horneado',
        (date.today() + timedelta(days=1)).isoformat()
    )
    fecha_amasado = (
        date.fromisoformat(fecha_horneado) - timedelta(days=1)
    ).isoformat()

    with db() as c:
        resultado = _calcular_orden_produccion(c, fecha_horneado)

        if not resultado['ordenes']:
            return jsonify({'ok': True, 'batches': [], 'advertencia': 'Sin ventas para esa fecha'}), 200

        batches_creados = []
        for orden in resultado['ordenes']:
            masa_base = orden['masa_base']

            # Borrar solo filas pendientes de esta masa_base + fecha_amasado
            c.execute("""
                DELETE FROM plan_produccion
                WHERE fecha_amasado = ? AND estado = 'pendiente'
                  AND producto_id IN (
                      SELECT id FROM productos WHERE masa_base = ?
                  )
            """, (fecha_amasado, masa_base))

            batch_id = str(uuid.uuid4())
            ings_json = json.dumps(orden['ingredientes'], ensure_ascii=False)

            for prod in orden['productos']:
                c.execute("""
                    INSERT INTO plan_produccion
                        (fecha, nombre_producto, cantidad, estado, producto_id,
                         fecha_amasado, fecha_horneado, batch_id, ingredientes_json, notas)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    fecha_amasado,
                    prod['nombre'],
                    prod['cantidad'],
                    'pendiente',
                    prod['producto_id'],
                    fecha_amasado,
                    fecha_horneado,
                    batch_id,
                    ings_json,
                    '',
                ))

            batches_creados.append({
                'batch_id':      batch_id,
                'masa_base':     masa_base,
                'masa_amasar_kg': orden['masa_amasar_kg'],
                'productos':     [p['nombre'] for p in orden['productos']],
                'alerta_stock':  orden['alerta_stock'],
            })

    return jsonify({
        'ok':              True,
        'fecha_amasado':   fecha_amasado,
        'fecha_horneado':  fecha_horneado,
        'batches':         batches_creados,
        'sin_masa_base':   resultado['sin_masa_base'],
    }), 201
```

- [ ] **Step 4.2: Verificar que generar-plan crea las filas correctas**

```bash
venv/Scripts/python.exe -c "
import app, json, sqlite3
app.app.config['TESTING'] = True
client = app.app.test_client()
client.post('/login', data={'email':'admin@aurorabakers.cl','password':'aurora2024'}, follow_redirects=True)

# Llamar con fecha donde sabemos que hay ventas (o simplemente verificar estructura)
r = client.post('/api/produccion/generar-plan',
    json={'fecha_horneado': '2099-01-01'},
    content_type='application/json')
data = json.loads(r.data)
assert data['ok'] is True, f'Error: {data}'
assert 'batches' in data, 'falta batches'
assert data['batches'] == [], 'sin ventas en 2099, batches debe ser []'
print('OK — generar-plan responde sin errores')
"
```

Resultado esperado: `OK — generar-plan responde sin errores`

- [ ] **Step 4.3: Commit**

```bash
git add app.py
git commit -m "feat: POST /api/produccion/generar-plan con batch_id y limpieza idempotente"
```

---

## Task 5: Refactor `GET /api/plan-produccion` — respuesta timeline

**Files:**
- Modify: `app.py` (función `api_plan_list`, ~línea 3048)

El endpoint actual devuelve una lista plana. Lo extendemos para que cuando se pase `?vista=timeline` retorne las dos secciones (`amasar_hoy` y `hornear_hoy`). El comportamiento sin el parámetro queda intacto para backward compat.

- [ ] **Step 5.1: Modificar `api_plan_list` en `app.py`**

Reemplaza la función `api_plan_list` completa:

```python
@app.route('/api/plan-produccion', methods=['GET'])
@login_required
def api_plan_list():
    fecha = request.args.get('fecha', date.today().isoformat())
    vista = request.args.get('vista', '')

    with db() as c:
        if vista == 'timeline':
            # --- Sección "Hornear hoy": batch_id amasados que hornean esta fecha ---
            hornear_rows = c.execute("""
                SELECT batch_id, nombre_producto, cantidad, estado,
                       producto_id, fecha_amasado, fecha_horneado, ingredientes_json
                FROM plan_produccion
                WHERE fecha_horneado = ?
                  AND estado IN ('amasado')
                  AND batch_id != ''
                ORDER BY batch_id, nombre_producto
            """, (fecha,)).fetchall()

            # --- Sección "Amasar hoy": pendientes que se amasan esta fecha ---
            amasar_rows = c.execute("""
                SELECT batch_id, nombre_producto, cantidad, estado,
                       producto_id, fecha_amasado, fecha_horneado, ingredientes_json
                FROM plan_produccion
                WHERE fecha_amasado = ?
                  AND estado = 'pendiente'
                  AND batch_id != ''
                ORDER BY batch_id, nombre_producto
            """, (fecha,)).fetchall()

            # --- Batches horneados del día (para mostrar como completados) ---
            horneados_rows = c.execute("""
                SELECT batch_id, nombre_producto, cantidad, estado,
                       producto_id, fecha_amasado, fecha_horneado, ingredientes_json
                FROM plan_produccion
                WHERE fecha_amasado = ?
                  AND estado IN ('horneado', 'listo')
                  AND batch_id != ''
                ORDER BY batch_id, nombre_producto
            """, (fecha,)).fetchall()

            def agrupar_por_batch(rows):
                batches = {}
                for r in rows:
                    r = dict(r)
                    bid = r['batch_id']
                    if bid not in batches:
                        # masa_base viene del producto
                        prod = c.execute(
                            "SELECT masa_base FROM productos WHERE id=?", (r['producto_id'],)
                        ).fetchone()
                        masa_base = prod['masa_base'] if prod else ''
                        # masa_amasar_kg: suma del peso de todos los productos del batch
                        batch_prods = c.execute("""
                            SELECT pp.cantidad, p.peso_unitario_kg
                            FROM plan_produccion pp
                            JOIN productos p ON p.id = pp.producto_id
                            WHERE pp.batch_id = ?
                        """, (bid,)).fetchall()
                        masa_final = sum(bp['cantidad'] * bp['peso_unitario_kg'] for bp in batch_prods)
                        # Recuperar baking_loss y merma del primer producto
                        ref_prod = c.execute(
                            "SELECT baking_loss_pct, merma_tecnica_pct FROM productos WHERE id=?",
                            (r['producto_id'],)
                        ).fetchone()
                        if ref_prod and ref_prod['baking_loss_pct'] > 0:
                            masa_cruda = masa_final / (1 - ref_prod['baking_loss_pct'] / 100)
                            masa_amasar = masa_cruda * (1 + ref_prod['merma_tecnica_pct'] / 100)
                        else:
                            masa_amasar = masa_final

                        try:
                            ings = json.loads(r['ingredientes_json'] or '[]')
                        except Exception:
                            ings = []

                        batches[bid] = {
                            'batch_id':      bid,
                            'masa_base':     masa_base,
                            'estado':        r['estado'],
                            'fecha_amasado': r['fecha_amasado'],
                            'fecha_horneado': r['fecha_horneado'],
                            'masa_amasar_kg': round(masa_amasar, 3),
                            'ingredientes':  ings,
                            'productos':     [],
                        }
                    batches[bid]['productos'].append({
                        'nombre':   r['nombre_producto'],
                        'cantidad': r['cantidad'],
                        'producto_id': r['producto_id'],
                    })
                return list(batches.values())

            return jsonify({
                'fecha':       fecha,
                'hornear_hoy': agrupar_por_batch(hornear_rows),
                'amasar_hoy':  agrupar_por_batch(amasar_rows),
                'horneados':   agrupar_por_batch(horneados_rows),
            })

        # Comportamiento original (sin vista=timeline) — backward compat
        rows = c.execute(
            "SELECT * FROM plan_produccion WHERE fecha=? ORDER BY nombre_producto", (fecha,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])
```

- [ ] **Step 5.2: Verificar backward compat y timeline**

```bash
venv/Scripts/python.exe -c "
import app, json
app.app.config['TESTING'] = True
client = app.app.test_client()
client.post('/login', data={'email':'admin@aurorabakers.cl','password':'aurora2024'}, follow_redirects=True)

# Backward compat: sin vista=timeline devuelve lista plana
r1 = client.get('/api/plan-produccion?fecha=2026-05-08')
d1 = json.loads(r1.data)
assert isinstance(d1, list), f'sin timeline debe retornar lista, got {type(d1)}'

# Timeline: con vista=timeline devuelve dict con secciones
r2 = client.get('/api/plan-produccion?fecha=2026-05-08&vista=timeline')
d2 = json.loads(r2.data)
assert 'amasar_hoy' in d2, 'falta amasar_hoy'
assert 'hornear_hoy' in d2, 'falta hornear_hoy'
assert 'horneados' in d2, 'falta horneados'
print('OK — api_plan_list compatible y timeline funciona')
"
```

- [ ] **Step 5.3: Commit**

```bash
git add app.py
git commit -m "feat: GET /api/plan-produccion soporta vista=timeline con secciones amasar/hornear"
```

---

## Task 6: `POST /api/produccion/batch/<batch_id>/amasar`

**Files:**
- Modify: `app.py` (añadir después de los endpoints de Task 4)
- Create: `tests/test_batch_endpoints.py`

- [ ] **Step 6.1: Crear tests para el endpoint amasar**

Crea `tests/test_batch_endpoints.py`:

```python
"""Tests de los endpoints amasar y hornear de batch."""
import sys, os, json, sqlite3, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def get_client():
    import app as _app
    _app.app.config['TESTING'] = True
    client = _app.app.test_client()
    client.post('/login',
        data={'email': 'admin@aurorabakers.cl', 'password': 'aurora2024'},
        follow_redirects=True)
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
                (fecha, nombre_producto, cantidad, estado, producto_id,
                 fecha_amasado, fecha_horneado, batch_id, ingredientes_json, notas)
            VALUES (date('now'), 'Test Hogaza', 5, ?, ?, date('now'), date('now','+1 day'), ?, ?, '')
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
```

- [ ] **Step 6.2: Ejecutar tests — deben FALLAR (endpoints no existen)**

```bash
venv/Scripts/python.exe tests/test_batch_endpoints.py 2>&1 | head -20
```

Resultado esperado: error 404 en el primer test (ruta no definida).

- [ ] **Step 6.3: Implementar `POST /api/produccion/batch/<batch_id>/amasar` en `app.py`**

```python
@app.route('/api/produccion/batch/<batch_id>/amasar', methods=['POST'])
@login_required
def api_produccion_batch_amasar(batch_id):
    """
    Transición pendiente → amasado.
    Descuenta ingredientes de inventario (bodega=ingredientes).
    Si stock insuficiente: descuenta lo disponible, responde con advertencias (no bloquea).
    """
    with db() as c:
        filas = c.execute(
            "SELECT * FROM plan_produccion WHERE batch_id=?", (batch_id,)
        ).fetchall()
        if not filas:
            return jsonify({'error': 'Batch no encontrado'}), 404

        estado_actual = filas[0]['estado']
        if estado_actual != 'pendiente':
            msgs = {'amasado': 'Este lote ya fue amasado', 'horneado': 'Este lote ya fue horneado'}
            return jsonify({'error': msgs.get(estado_actual, f'Estado inválido: {estado_actual}')}), 400

        # Usar ingredientes_json de la primera fila con datos
        ings_json = None
        for f in filas:
            if f['ingredientes_json'] and f['ingredientes_json'] != '[]':
                ings_json = f['ingredientes_json']
                break

        advertencias = []
        if ings_json:
            try:
                ingredientes = json.loads(ings_json)
            except Exception:
                ingredientes = []

            for ing in ingredientes:
                inv_id = ing.get('inventario_id')
                kg_necesario = float(ing.get('kg', 0))
                if not inv_id or kg_necesario <= 0:
                    continue
                row = c.execute(
                    "SELECT stock_kg, ingrediente FROM inventario WHERE id=?", (inv_id,)
                ).fetchone()
                if not row:
                    continue
                stock_actual = float(row['stock_kg'])
                descontar = min(kg_necesario, stock_actual)
                c.execute(
                    "UPDATE inventario SET stock_kg=MAX(0, stock_kg-?), ultima_actualizacion=date('now') WHERE id=?",
                    (descontar, inv_id)
                )
                if stock_actual < kg_necesario:
                    advertencias.append({
                        'ingrediente': row['ingrediente'],
                        'necesario_kg': kg_necesario,
                        'disponible_kg': stock_actual,
                        'faltante_kg': round(kg_necesario - stock_actual, 3),
                    })

        c.execute(
            "UPDATE plan_produccion SET estado='amasado' WHERE batch_id=?", (batch_id,)
        )

    return jsonify({'ok': True, 'batch_id': batch_id, 'advertencias': advertencias})
```

- [ ] **Step 6.4: Ejecutar tests parciales — amasar deben pasar**

```bash
venv/Scripts/python.exe -c "
import tests.test_batch_endpoints as t
t.test_amasar_pendiente_ok()
t.test_amasar_ya_amasado_rechaza()
print('Tests amasar OK')
"
```

Resultado esperado: `PASS` en ambos tests de amasar.

- [ ] **Step 6.5: Commit parcial**

```bash
git add app.py tests/test_batch_endpoints.py
git commit -m "feat: POST /api/produccion/batch/<id>/amasar con descuento ingredientes"
```

---

## Task 7: `POST /api/produccion/batch/<batch_id>/hornear`

**Files:**
- Modify: `app.py` (añadir a continuación del endpoint de Task 6)

- [ ] **Step 7.1: Implementar el endpoint hornear en `app.py`**

```python
@app.route('/api/produccion/batch/<batch_id>/hornear', methods=['POST'])
@login_required
def api_produccion_batch_hornear(batch_id):
    """
    Transición amasado → horneado.
    Suma stock de productos_terminados e inventario para cada producto del batch.
    Valida que el estado sea 'amasado' — rechaza 'pendiente' con mensaje claro.
    """
    with db() as c:
        filas = c.execute(
            "SELECT * FROM plan_produccion WHERE batch_id=?", (batch_id,)
        ).fetchall()
        if not filas:
            return jsonify({'error': 'Batch no encontrado'}), 404

        estado_actual = filas[0]['estado']
        if estado_actual == 'pendiente':
            return jsonify({'error': 'Registra el amasado primero antes de hornear'}), 400
        if estado_actual in ('horneado', 'listo'):
            return jsonify({'error': 'Este lote ya fue horneado'}), 400
        if estado_actual != 'amasado':
            return jsonify({'error': f'Estado inválido para hornear: {estado_actual}'}), 400

        stock_sumado = []
        for fila in filas:
            prod_id = fila['producto_id']
            nombre  = fila['nombre_producto']
            cantidad = fila['cantidad']
            if not prod_id:
                continue
            inv_id = _get_or_create_inv_terminado(c, prod_id, nombre)
            c.execute(
                "UPDATE inventario SET stock_kg=stock_kg+?, ultima_actualizacion=date('now') WHERE id=?",
                (cantidad, inv_id)
            )
            c.execute(
                "UPDATE productos SET stock=stock+? WHERE id=?",
                (cantidad, prod_id)
            )
            stock_sumado.append({'nombre': nombre, 'cantidad': cantidad})

        c.execute(
            "UPDATE plan_produccion SET estado='horneado' WHERE batch_id=?", (batch_id,)
        )

    return jsonify({'ok': True, 'batch_id': batch_id, 'stock_sumado': stock_sumado})
```

- [ ] **Step 7.2: Ejecutar todos los tests de batch — deben pasar**

```bash
venv/Scripts/python.exe tests/test_batch_endpoints.py
```

Resultado esperado:
```
PASS test_amasar_pendiente_ok
PASS test_amasar_ya_amasado_rechaza
PASS test_hornear_amasado_ok
PASS test_hornear_pendiente_rechaza
PASS test_hornear_inexistente_404

Todos los tests de batch pasaron.
```

- [ ] **Step 7.3: Commit**

```bash
git add app.py
git commit -m "feat: POST /api/produccion/batch/<id>/hornear con suma de stock"
```

---

## Task 8: Modal de Productos — 3 campos nuevos

**Files:**
- Modify: `templates/productos.html`

Los campos se añaden al modal existente de creación/edición de producto, junto a los campos actuales de `precio`, `costo`, `stock`, `unidad`. Los tres campos son opcionales (solo aplican a productos que entran al flujo de producción).

- [ ] **Step 8.1: Localizar el modal en `templates/productos.html`**

```bash
grep -n "modal\|peso_unitario\|categoria\|subcategoria" templates/productos.html | head -20
```

Busca la sección del modal de edición/creación (probablemente un `<div id="modal-prod">`).

- [ ] **Step 8.2: Añadir los tres campos al modal**

Dentro del modal, busca el campo de `peso_unitario_kg` (ya existente) y añade los tres nuevos campos inmediatamente después:

```html
<!-- Añadir después del campo peso_unitario_kg -->
<div style="border-top:1px solid var(--border);margin:1rem 0;padding-top:1rem">
  <p style="font-size:.78rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:.75rem">
    Producción Masa Madre
  </p>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:.75rem">
    <div>
      <label class="form-label">Masa Base</label>
      <input type="text" id="f-masa-base" class="form-control" placeholder="ej. Masa Madre Trigo"
             title="Productos con la misma Masa Base se agrupan en un lote al planificar producción">
    </div>
    <div>
      <label class="form-label">Baking Loss %</label>
      <input type="number" id="f-baking-loss" class="form-control" min="0" max="50" step="0.5"
             placeholder="ej. 15" title="% de peso que pierde la masa al hornearse">
    </div>
    <div>
      <label class="form-label">Merma Técnica %</label>
      <input type="number" id="f-merma-tecnica" class="form-control" min="0" max="20" step="0.5"
             placeholder="ej. 2" title="% de masa perdida en bowl y amasado">
    </div>
  </div>
</div>
```

- [ ] **Step 8.3: Actualizar la función `abrirModal` en el JS del template**

Dentro de la función JavaScript que puebla el modal al editar (busca donde se asignan los valores a los campos `f-precio`, `f-stock`, etc.), añade:

```javascript
document.getElementById('f-masa-base').value    = p.masa_base    || '';
document.getElementById('f-baking-loss').value  = p.baking_loss_pct  || '';
document.getElementById('f-merma-tecnica').value = p.merma_tecnica_pct || '';
```

Y en la función de reset/nuevo producto (donde se limpian los campos):

```javascript
document.getElementById('f-masa-base').value    = '';
document.getElementById('f-baking-loss').value  = '';
document.getElementById('f-merma-tecnica').value = '';
```

- [ ] **Step 8.4: Incluir los campos en el body del fetch POST/PUT**

Busca el `fetch('/api/productos', ...)` o `fetch('/api/productos/${id}', ...)` en el JS del template y añade al body:

```javascript
masa_base:          document.getElementById('f-masa-base').value.trim(),
baking_loss_pct:    parseFloat(document.getElementById('f-baking-loss').value)  || 0,
merma_tecnica_pct:  parseFloat(document.getElementById('f-merma-tecnica').value) || 0,
```

- [ ] **Step 8.5: Actualizar `api_productos_create` y `api_productos_update` en `app.py`**

En `api_productos_create` (POST `/api/productos`, ~línea 1125), añade los tres campos al INSERT:

```python
# En la lista de columnas del INSERT, añadir:
# ..., masa_base, baking_loss_pct, merma_tecnica_pct
# En los valores:
masa_base         = d.get('masa_base', '').strip()
baking_loss_pct   = float(d.get('baking_loss_pct', 0) or 0)
merma_tecnica_pct = float(d.get('merma_tecnica_pct', 0) or 0)
```

Busca el INSERT en esa función y añade los tres campos. Ejemplo del patrón a seguir (adaptar a la firma exacta del INSERT existente):

```python
c.execute(
    """INSERT INTO productos
       (nombre, descripcion, precio, costo, stock, unidad, activo,
        precio_mayorista, categoria, subcategoria, peso_unitario_kg,
        masa_base, baking_loss_pct, merma_tecnica_pct)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
    (nombre, descripcion, precio, costo, stock, unidad, 1,
     precio_mayorista, categoria, subcategoria, peso_unitario_kg,
     masa_base, baking_loss_pct, merma_tecnica_pct)
)
```

Hacer lo mismo para `api_productos_update` (PUT `/api/productos/<pid>`, ~línea 1166).

- [ ] **Step 8.6: Verificar que el API acepta y guarda los campos**

```bash
venv/Scripts/python.exe -c "
import app, json
app.app.config['TESTING'] = True
client = app.app.test_client()
client.post('/login', data={'email':'admin@aurorabakers.cl','password':'aurora2024'}, follow_redirects=True)

# Actualizar Hogaza Campesina (id=1) con masa_base
r = client.put('/api/productos/1',
    json={'masa_base': 'Masa Madre Trigo', 'baking_loss_pct': 15.0, 'merma_tecnica_pct': 2.0},
    content_type='application/json')
assert r.status_code in (200, 204), f'Error: {r.status_code} {r.data}'

# Verificar que se guardó
import sqlite3
conn = sqlite3.connect('aurora.db')
row = conn.execute('SELECT masa_base, baking_loss_pct, merma_tecnica_pct FROM productos WHERE id=1').fetchone()
conn.close()
assert row[0] == 'Masa Madre Trigo', f'masa_base no guardada: {row[0]}'
assert row[1] == 15.0, f'baking_loss_pct no guardada: {row[1]}'
print('OK — campos de producción guardados correctamente')
"
```

- [ ] **Step 8.7: Commit**

```bash
git add app.py templates/productos.html
git commit -m "feat: campos masa_base, baking_loss_pct, merma_tecnica_pct en modal de productos"
```

---

## Task 9: UI Timeline — reescribir `templates/produccion.html`

**Files:**
- Modify: `templates/produccion.html` (reescritura completa del JS y secciones principales, manteniendo el `{% extends 'base.html' %}`)

- [ ] **Step 9.1: Reemplazar el contenido del bloque `{% block content %}` con el nuevo layout**

```html
{% block content %}
<div style="max-width:900px;margin:0 auto;padding:1rem">

  <!-- Navegación de fecha -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.5rem">
    <button class="btn btn-secondary btn-sm" onclick="cambiarFecha(-1)">← Anterior</button>
    <div style="text-align:center">
      <h2 style="margin:0;font-size:1.1rem;font-weight:700" id="titulo-fecha">Cargando...</h2>
      <input type="date" id="fecha-selector" style="margin-top:.25rem;font-size:.85rem"
             onchange="cargarTimeline(this.value)">
    </div>
    <button class="btn btn-secondary btn-sm" onclick="cambiarFecha(1)">Siguiente →</button>
  </div>

  <!-- Botón generar plan -->
  <div style="margin-bottom:1.5rem;text-align:right">
    <button class="btn btn-primary" onclick="abrirModalGenerar()">
      <i class="bi bi-calendar-plus"></i> Generar plan desde ventas
    </button>
  </div>

  <!-- Sección HORNEAR HOY -->
  <div style="margin-bottom:2rem">
    <h3 style="font-size:.9rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
               color:#ea580c;margin-bottom:.75rem">
      🔥 Hornear hoy
      <span style="font-weight:400;color:var(--muted);font-size:.8rem;text-transform:none">
        — lotes amasados ayer, listos para el horno
      </span>
    </h3>
    <div id="lista-hornear">
      <p style="color:var(--muted);font-size:.9rem">Cargando...</p>
    </div>
  </div>

  <!-- Sección AMASAR HOY -->
  <div style="margin-bottom:2rem">
    <h3 style="font-size:.9rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
               color:#16a34a;margin-bottom:.75rem">
      🌾 Amasar hoy
      <span style="font-weight:400;color:var(--muted);font-size:.8rem;text-transform:none">
        — para despachos de mañana
      </span>
    </h3>
    <div id="lista-amasar">
      <p style="color:var(--muted);font-size:.9rem">Cargando...</p>
    </div>
  </div>

  <!-- Sección HORNEADOS (completados) -->
  <div id="section-horneados" style="display:none;margin-bottom:2rem">
    <h3 style="font-size:.9rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
               color:var(--muted);margin-bottom:.75rem">
      ✓ Completados hoy
    </h3>
    <div id="lista-horneados"></div>
  </div>

</div>

<!-- Modal: Generar Plan desde Ventas -->
<div id="modal-generar" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);
     z-index:1000;align-items:center;justify-content:center">
  <div style="background:var(--surface);border-radius:.75rem;padding:1.5rem;
               width:min(95vw,680px);max-height:85vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,.3)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
      <h3 style="margin:0;font-size:1rem;font-weight:700">Generar plan de producción</h3>
      <button class="btn btn-secondary btn-sm" onclick="cerrarModalGenerar()">✕</button>
    </div>
    <div id="modal-generar-content">
      <p style="color:var(--muted)">Calculando...</p>
    </div>
    <div style="display:flex;gap:.75rem;margin-top:1.25rem;justify-content:flex-end" id="modal-generar-footer" style="display:none">
      <button class="btn btn-secondary" onclick="cerrarModalGenerar()">Cancelar</button>
      <button class="btn btn-primary" id="btn-confirmar-plan" onclick="confirmarPlan()">
        Confirmar y guardar plan
      </button>
    </div>
  </div>
</div>
{% endblock %}

{% block scripts %}
<script>
const hoy = new Date().toISOString().slice(0,10);
let fechaActual = hoy;
let planPreview  = null;

// ── Helpers ────────────────────────────────────────────────────────────────

function fmtFecha(iso) {
  const d = new Date(iso + 'T12:00:00');
  return d.toLocaleDateString('es-CL', {weekday:'long', day:'numeric', month:'long', year:'numeric'});
}

function badgeEstado(estado) {
  const cfg = {
    pendiente: ['#e2e8f0','#475569','PENDIENTE'],
    amasado:   ['#fed7aa','#c2410c','EN HORNEADO'],
    horneado:  ['#dcfce7','#15803d','HORNEADO'],
    listo:     ['#dcfce7','#15803d','HORNEADO'],
  };
  const [bg, color, label] = cfg[estado] || ['#e2e8f0','#475569', estado.toUpperCase()];
  return `<span style="background:${bg};color:${color};font-size:.7rem;padding:2px 8px;
                border-radius:10px;font-weight:700;text-transform:uppercase">${label}</span>`;
}

function cardBatch(batch, tipo) {
  const alertaBorder = batch.ingredientes?.some(i => !i.suficiente)
    ? 'border-color:#ef4444;' : '';

  const btns = {
    hornear: `<button class="btn btn-primary btn-sm" onclick="marcarHorneado('${batch.batch_id}')">
                ✓ Marcar como Horneado
              </button>`,
    amasar: `<button class="btn btn-primary btn-sm" onclick="marcarAmasado('${batch.batch_id}')">
               ✓ Marcar como Amasado
             </button>`,
    completado: `<span style="color:#16a34a;font-size:.82rem;font-weight:600">✓ Completado</span>`,
  }[tipo];

  const ingsHtml = batch.ingredientes?.length
    ? `<div style="margin-top:.75rem;padding-top:.75rem;border-top:1px solid var(--border)">
         <p style="font-size:.75rem;color:var(--muted);font-weight:600;margin-bottom:.35rem">INGREDIENTES</p>
         <div style="display:flex;flex-wrap:wrap;gap:.4rem">
           ${batch.ingredientes.map(i => {
             const alertaIng = !i.suficiente
               ? 'background:#fee2e2;color:#991b1b;'
               : 'background:var(--bg);color:var(--text);';
             const alertaTxt = !i.suficiente
               ? ` ⚠ solo ${i.stock_actual} kg disponibles` : '';
             return `<span style="${alertaIng}border:1px solid var(--border);border-radius:.35rem;
                          font-size:.75rem;padding:2px 8px">
                       ${i.nombre}: <strong>${i.kg} kg</strong>${alertaTxt}
                     </span>`;
           }).join('')}
         </div>
       </div>` : '';

  return `
  <div style="background:var(--surface);border:1px solid var(--border);${alertaBorder}
              border-radius:.5rem;padding:1rem;margin-bottom:.75rem">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:.5rem">
      <div>
        <span style="font-weight:700;font-size:.95rem">${batch.masa_base || 'Sin masa base'}</span>
        ${badgeEstado(batch.estado)}
        <div style="font-size:.82rem;color:var(--muted);margin-top:.2rem">
          ${batch.productos.map(p => `${p.nombre} ×${p.cantidad}`).join(' · ')}
        </div>
        <div style="font-size:.8rem;color:var(--muted);margin-top:.1rem">
          ${batch.masa_amasar_kg ? `${batch.masa_amasar_kg} kg a amasar` : ''}
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:.75rem">
        ${btns}
      </div>
    </div>
    ${ingsHtml}
  </div>`;
}

// ── Carga timeline ──────────────────────────────────────────────────────────

async function cargarTimeline(fecha) {
  fechaActual = fecha;
  document.getElementById('fecha-selector').value = fecha;
  document.getElementById('titulo-fecha').textContent = fmtFecha(fecha);

  const r = await fetch(`/api/plan-produccion?fecha=${fecha}&vista=timeline`);
  const data = await r.json();

  // Hornear hoy
  const elH = document.getElementById('lista-hornear');
  if (data.hornear_hoy.length) {
    elH.innerHTML = data.hornear_hoy.map(b => cardBatch(b, 'hornear')).join('');
  } else {
    elH.innerHTML = '<p style="color:var(--muted);font-size:.9rem">No hay lotes para hornear hoy.</p>';
  }

  // Amasar hoy
  const elA = document.getElementById('lista-amasar');
  if (data.amasar_hoy.length) {
    elA.innerHTML = data.amasar_hoy.map(b => cardBatch(b, 'amasar')).join('');
  } else {
    elA.innerHTML = `<p style="color:var(--muted);font-size:.9rem">
      Sin plan de amasado. Usa <em>"Generar plan desde ventas"</em> para calcularlo.
    </p>`;
  }

  // Horneados (completados)
  const elC = document.getElementById('lista-horneados');
  const secC = document.getElementById('section-horneados');
  if (data.horneados.length) {
    elC.innerHTML = data.horneados.map(b => cardBatch(b, 'completado')).join('');
    secC.style.display = '';
  } else {
    secC.style.display = 'none';
  }
}

function cambiarFecha(delta) {
  const d = new Date(fechaActual + 'T12:00:00');
  d.setDate(d.getDate() + delta);
  cargarTimeline(d.toISOString().slice(0,10));
}

// ── Acciones de batch ────────────────────────────────────────────────────────

async function marcarAmasado(batchId) {
  if (!confirm('¿Confirmar que este lote fue amasado?')) return;
  const r = await fetch(`/api/produccion/batch/${batchId}/amasar`, {method:'POST'});
  const data = await r.json();
  if (!data.ok) { alert(data.error || 'Error'); return; }
  if (data.advertencias?.length) {
    const msgs = data.advertencias.map(a =>
      `${a.ingrediente}: faltaron ${a.faltante_kg} kg`).join('\n');
    alert(`Amasado registrado con stock insuficiente:\n${msgs}`);
  }
  cargarTimeline(fechaActual);
}

async function marcarHorneado(batchId) {
  if (!confirm('¿Confirmar que este lote fue horneado y está listo?')) return;
  const r = await fetch(`/api/produccion/batch/${batchId}/hornear`, {method:'POST'});
  const data = await r.json();
  if (!data.ok) { alert(data.error || 'Error'); return; }
  cargarTimeline(fechaActual);
}

// ── Modal Generar Plan ────────────────────────────────────────────────────────

async function abrirModalGenerar() {
  document.getElementById('modal-generar').style.display = 'flex';
  document.getElementById('modal-generar-footer').style.display = 'none';
  document.getElementById('modal-generar-content').innerHTML =
    '<p style="color:var(--muted)">Calculando orden de trabajo...</p>';
  planPreview = null;

  const fechaHorneado = (() => {
    const d = new Date(fechaActual + 'T12:00:00');
    d.setDate(d.getDate() + 1);
    return d.toISOString().slice(0,10);
  })();

  const r = await fetch(`/api/produccion/calcular-orden?fecha_horneado=${fechaHorneado}`);
  const data = await r.json();
  planPreview = data;

  if (!data.ordenes.length && !data.sin_masa_base.length) {
    document.getElementById('modal-generar-content').innerHTML =
      `<p style="color:var(--muted)">Sin ventas con fecha_despacho ${fechaHorneado}. No hay nada que planificar.</p>`;
    return;
  }

  let html = `<p style="font-size:.85rem;color:var(--muted);margin-bottom:1rem">
    Amasado: <strong>${data.fecha_amasado}</strong> →
    Horneado/Despacho: <strong>${data.fecha_horneado}</strong>
  </p>`;

  for (const orden of data.ordenes) {
    const alerta = orden.alerta_stock
      ? '<span style="color:#ef4444;font-weight:600">⚠ Stock insuficiente</span>' : '';
    html += `
      <div style="border:1px solid var(--border);border-radius:.5rem;padding:.85rem;margin-bottom:.75rem;
                  ${orden.alerta_stock ? 'border-color:#fca5a5' : ''}">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.5rem">
          <strong>${orden.masa_base}</strong>
          <span style="font-size:.82rem;color:var(--muted)">${orden.masa_amasar_kg} kg a amasar ${alerta}</span>
        </div>
        <div style="font-size:.82rem;color:var(--muted);margin-bottom:.5rem">
          ${orden.productos.map(p => `${p.nombre} ×${p.cantidad}`).join(' · ')}
        </div>
        <table style="width:100%;font-size:.8rem;border-collapse:collapse">
          <thead>
            <tr style="color:var(--muted)">
              <th style="text-align:left;padding:2px 4px">Ingrediente</th>
              <th style="text-align:right;padding:2px 4px">Necesario</th>
              <th style="text-align:right;padding:2px 4px">Stock</th>
              <th style="text-align:right;padding:2px 4px">Estado</th>
            </tr>
          </thead>
          <tbody>
            ${orden.ingredientes.map(i => `
              <tr style="border-top:1px solid var(--border)">
                <td style="padding:3px 4px">${i.nombre}</td>
                <td style="text-align:right;padding:3px 4px">${i.kg} kg</td>
                <td style="text-align:right;padding:3px 4px">${i.stock_actual} kg</td>
                <td style="text-align:right;padding:3px 4px">
                  ${i.suficiente
                    ? '<span style="color:#16a34a">✓ OK</span>'
                    : '<span style="color:#ef4444">⚠ Insuf.</span>'}
                </td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  }

  if (data.sin_masa_base.length) {
    html += `<div style="background:#fef9c3;border-radius:.4rem;padding:.65rem;font-size:.82rem;margin-top:.75rem">
      <strong>Sin masa_base configurada (no se planifican):</strong>
      ${data.sin_masa_base.map(p => `${p.nombre} ×${p.cantidad}`).join(', ')}
    </div>`;
  }

  document.getElementById('modal-generar-content').innerHTML = html;
  document.getElementById('modal-generar-footer').style.display = 'flex';
}

async function confirmarPlan() {
  if (!planPreview) return;
  document.getElementById('btn-confirmar-plan').disabled = true;
  document.getElementById('btn-confirmar-plan').textContent = 'Guardando...';

  const r = await fetch('/api/produccion/generar-plan', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({fecha_horneado: planPreview.fecha_horneado}),
  });
  const data = await r.json();

  cerrarModalGenerar();
  if (data.ok) {
    cargarTimeline(fechaActual);
  } else {
    alert(data.error || 'Error al guardar el plan');
  }
}

function cerrarModalGenerar() {
  document.getElementById('modal-generar').style.display = 'none';
  document.getElementById('btn-confirmar-plan').disabled = false;
  document.getElementById('btn-confirmar-plan').textContent = 'Confirmar y guardar plan';
  planPreview = null;
}

// Init
cargarTimeline(hoy);
</script>
{% endblock %}
```

- [ ] **Step 9.2: Verificar que el servidor levanta sin errores de template**

```bash
venv/Scripts/python.exe -c "
import app
app.app.config['TESTING'] = True
client = app.app.test_client()
client.post('/login', data={'email':'admin@aurorabakers.cl','password':'aurora2024'}, follow_redirects=True)
r = client.get('/produccion')
assert r.status_code == 200, f'Error {r.status_code}: {r.data[:200]}'
assert b'Hornear hoy' in r.data or b'cargarTimeline' in r.data, 'Template no contiene el nuevo contenido'
print('OK — /produccion carga sin errores')
"
```

- [ ] **Step 9.3: Commit final**

```bash
git add templates/produccion.html
git commit -m "feat: timeline de produccion con secciones amasar/hornear y modal generar plan"
```

---

## Task 10: Verificación end-to-end

- [ ] **Step 10.1: Levantar el servidor y verificar flujo completo**

```bash
venv/Scripts/python.exe app.py
```

Abrir http://127.0.0.1:5000 y verificar:

1. Ir a **Productos** → editar cualquier producto → verificar que aparecen campos "Masa Base", "Baking Loss %", "Merma Técnica %"
2. Configurar al menos dos productos con la misma `masa_base` (ej. `"Masa Madre Trigo"`) y valores `baking_loss_pct=15`, `merma_tecnica_pct=2`
3. Asegurarse de que hay una venta con `fecha_despacho = mañana` en el sistema
4. Ir a **Producción** → verificar que aparece la vista timeline (dos secciones)
5. Hacer clic en **"Generar plan desde ventas"** → debe abrir modal con la orden de trabajo calculada
6. Confirmar el plan → los batches aparecen en la sección "🌾 Amasar hoy"
7. Hacer clic en **"Marcar como Amasado"** → el batch desaparece de "Amasar hoy"
8. Navegar al día siguiente → el batch aparece en **"🔥 Hornear hoy"**
9. Hacer clic en **"Marcar como Horneado"** → el stock de productos y de inventario sube
10. Verificar en `/inventario` (pestaña Prod. Terminados) y en `/productos` que el stock subió correctamente

- [ ] **Step 10.2: Correr todos los tests**

```bash
venv/Scripts/python.exe tests/test_motor_produccion.py
venv/Scripts/python.exe tests/test_batch_endpoints.py
```

Resultado esperado: todos los tests pasan.

- [ ] **Step 10.3: Commit final de cierre**

```bash
git add .
git commit -m "feat: produccion reverse scheduling completo — tests, API, UI timeline"
```

---

## Self-Review del Plan

| Requisito del spec | Task que lo implementa |
|--------------------|------------------------|
| Schema migrations productos (masa_base, baking_loss, merma) | Task 1 + Task 8 |
| Schema migrations plan_produccion (fecha_amasado, fecha_horneado, batch_id, ingredientes_json) | Task 1 |
| Dominio estado: pendiente → amasado → horneado | Task 6, 7 |
| Motor: masa_final, masa_cruda, masa_amasar | Task 2 (función _calcular_orden_produccion) |
| Baker's percentage agnóstico a tipo de harina | Task 2 |
| Agrupación por masa_base (Hogaza + Baguette juntos) | Task 2 |
| GET /api/produccion/calcular-orden | Task 3 |
| POST /api/produccion/generar-plan | Task 4 |
| GET /api/plan-produccion?vista=timeline | Task 5 |
| POST batch/amasar — descuenta ingredientes | Task 6 |
| POST batch/hornear — suma productos terminados | Task 7 |
| Validaciones de estado (guardrails) | Task 6, 7 |
| Modal productos con 3 campos nuevos | Task 8 |
| UI Timeline con secciones amasar/hornear | Task 9 |
| backward compat api_plan_list sin parámetro | Task 5 |
| api_plan_confirmar (/fecha/confirmar) se mantiene | No tocado ✓ |
