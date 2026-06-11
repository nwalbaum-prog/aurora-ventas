# Multi-local Aurora Bakers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operar 2 locales con una DB conjunta en Railway: ventas/caja/gastos y stock de terminados por sucursal, traspasos entre locales, perfiles de usuario por función y métricas por local/total.

**Architecture:** Dimensión `sucursal_id` en ventas, pos_turnos, gastos, producto_lotes e inventario (terminados). `productos.stock` = total entre sucursales; invariante por sucursal: inventario(p,s) = Σ lotes(p,s). Producción siempre suma a sucursal 1 (Recoleta). Traspasos mueven stock FIFO con lote espejo. Enforcement de sucursal server-side vía sesión. Deploy: Railway + volumen `/data`.

**Tech Stack:** Flask + SQLite (existente), gunicorn 1 worker, Railway nixpacks.

**Spec:** `docs/superpowers/specs/2026-06-11-multi-local-design.md`

**Convenciones del proyecto (obligatorias):**
- Scripts de página SIEMPRE en `{% block scripts %}`, nunca en `{% block content %}` (`api()` no existe antes).
- Verificar `python -m py_compile app.py pos.py` tras cada cambio en Python.
- ASCII en `print()` (cp1252 en Windows).
- Tras cambios, Flask local requiere reinicio manual (debug=False).

---

### Task 1: Migraciones — sucursales, columnas sucursal_id, rebuild inventario

**Files:**
- Modify: `app.py` (función `init_db`)
- Test: `tests/test_sucursales.py` (nuevo)

- [ ] **Step 1: Test de migración que falla**

Crear `tests/test_sucursales.py`:

```python
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
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_sucursales.py::test_migracion_sucursales -x -q`
Expected: FAIL `no such table: sucursales`

- [ ] **Step 3: Implementar migraciones en `init_db()`**

3a. Al inicio de `init_db()`, antes del primer `executescript` (línea `c.executescript(""" CREATE TABLE IF NOT EXISTS productos`):

```python
        # ── Sucursales (multi-local) ─────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS sucursales (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre    TEXT    NOT NULL,
                direccion TEXT    NOT NULL DEFAULT '',
                activa    INTEGER NOT NULL DEFAULT 1
            )
        """)
        if c.execute("SELECT COUNT(*) FROM sucursales").fetchone()[0] == 0:
            c.executemany(
                "INSERT INTO sucursales (nombre, direccion) VALUES (?,?)",
                [('Recoleta', ''), ('Local 2', '')]
            )
```

3b. Agregar a la lista `migrations` (SQLite no permite FK con DEFAULT distinto de NULL en ALTER — columnas planas). OJO patrón del proyecto: el ALTER solo cubre DBs existentes; en DBs frescas la columna debe venir en el CREATE TABLE (ver 3b-bis). `_col_exists` devuelve True si la tabla no existe aún, así que el ALTER se salta sin error:

```python
            # Multi-local
            ("ventas",         "sucursal_id", "ALTER TABLE ventas ADD COLUMN sucursal_id INTEGER NOT NULL DEFAULT 1"),
            ("pos_turnos",     "sucursal_id", "ALTER TABLE pos_turnos ADD COLUMN sucursal_id INTEGER NOT NULL DEFAULT 1"),
            ("gastos",         "sucursal_id", "ALTER TABLE gastos ADD COLUMN sucursal_id INTEGER NOT NULL DEFAULT 1"),
            ("producto_lotes", "sucursal_id", "ALTER TABLE producto_lotes ADD COLUMN sucursal_id INTEGER NOT NULL DEFAULT 1"),
            ("usuarios",       "sucursal_id", "ALTER TABLE usuarios ADD COLUMN sucursal_id INTEGER DEFAULT NULL"),
```

3b-bis. Para DBs frescas, agregar la columna en los CREATE TABLE correspondientes (las tablas `gastos`, `usuarios`, `pos_turnos` y `producto_lotes` se crean DESPUÉS del loop de migrations, así que el ALTER nunca les aplica en una DB nueva):

- `CREATE TABLE IF NOT EXISTS ventas` (executescript inicial): agregar `sucursal_id INTEGER NOT NULL DEFAULT 1` al final de las columnas.
- `CREATE TABLE IF NOT EXISTS gastos` (executescript ERP): ídem.
- `CREATE TABLE IF NOT EXISTS usuarios`: agregar `sucursal_id INTEGER DEFAULT NULL`.
- `CREATE TABLE IF NOT EXISTS pos_turnos` (executescript POS): agregar `sucursal_id INTEGER NOT NULL DEFAULT 1`.
- `CREATE TABLE IF NOT EXISTS producto_lotes` (executescript POS): ídem.

3c. Reconstrucción de `inventario` (UNIQUE pasa de `ingrediente` a `(ingrediente, bodega, sucursal_id)`). Insertar DESPUÉS del loop `for table, col, sql in migrations:` y ANTES del bloque "Backfill producto_id":

```python
        # Rebuild inventario: UNIQUE(ingrediente) → UNIQUE(ingrediente, bodega, sucursal_id)
        # para permitir stock de terminados por sucursal. Idempotente.
        if _inventario_exists and not _col_exists(c, 'inventario', 'sucursal_id'):
            c.executescript("""
                CREATE TABLE inventario_new (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    ingrediente           TEXT    NOT NULL,
                    stock_kg              REAL    NOT NULL DEFAULT 0,
                    alerta_minimo_kg      REAL    NOT NULL DEFAULT 1,
                    proveedor             TEXT    NOT NULL DEFAULT '',
                    precio_kg             REAL    NOT NULL DEFAULT 0,
                    ultima_actualizacion  TEXT    NOT NULL DEFAULT (date('now')),
                    bodega                TEXT    NOT NULL DEFAULT 'ingredientes',
                    unidad                TEXT    NOT NULL DEFAULT 'kg',
                    categoria             TEXT    NOT NULL DEFAULT '',
                    subcategoria          TEXT    NOT NULL DEFAULT '',
                    producto_id           INTEGER REFERENCES productos(id) ON DELETE SET NULL,
                    sucursal_id           INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(ingrediente, bodega, sucursal_id)
                );
                INSERT INTO inventario_new
                    (id, ingrediente, stock_kg, alerta_minimo_kg, proveedor, precio_kg,
                     ultima_actualizacion, bodega, unidad, categoria, subcategoria, producto_id, sucursal_id)
                SELECT id, ingrediente, stock_kg, alerta_minimo_kg, proveedor, precio_kg,
                       ultima_actualizacion, bodega, unidad, categoria, subcategoria, producto_id, 1
                FROM inventario;
                DROP TABLE inventario;
                ALTER TABLE inventario_new RENAME TO inventario;
            """)
```

ATENCIÓN: `_inventario_exists` se calcula unas líneas más abajo hoy — mover su cálculo (`_inventario_exists = c.execute("SELECT name FROM sqlite_master...")`) ANTES del loop de migrations para poder usarlo aquí.

3d. Actualizar el `CREATE TABLE IF NOT EXISTS inventario` del executescript ERP (sección "Tablas ERP") al esquema nuevo — mismo cuerpo que `inventario_new` de 3c (para DBs frescas).

3e. En el mismo executescript de índices existente, agregar:

```sql
            CREATE INDEX IF NOT EXISTS idx_ventas_sucursal        ON ventas(sucursal_id);
            CREATE INDEX IF NOT EXISTS idx_producto_lotes_suc     ON producto_lotes(sucursal_id);
```

3f. Migraciones legacy de inventario en la lista `migrations` (bodega, unidad, categoria, subcategoria, producto_id): quedan obsoletas tras el rebuild pero son inofensivas (`_col_exists` da True). No tocar.

- [ ] **Step 4: Correr test**

Run: `python -m pytest tests/test_sucursales.py::test_migracion_sucursales -x -q`
Expected: PASS

- [ ] **Step 5: Suite completa + commit**

Run: `python -m pytest tests/ -q` — Expected: todos pasan
```bash
git add app.py tests/test_sucursales.py
git commit -m "feat(multi-local): tabla sucursales + sucursal_id + rebuild inventario"
```

---

### Task 2: Helpers de sucursal, sesión y GET /api/sucursales

**Files:**
- Modify: `app.py` (`_global_auth`, `page_login`, helpers nuevos, ruta nueva)
- Test: `tests/test_sucursales.py`

- [ ] **Step 1: Test que falla**

```python
def test_api_sucursales(client):
    tc, app_mod = client
    r = tc.get('/api/sucursales')
    assert r.status_code == 200
    d = r.get_json()
    assert len(d['sucursales']) == 2
    assert d['fija'] is None  # cajera del conftest no tiene sucursal fija
```

Run: `python -m pytest tests/test_sucursales.py::test_api_sucursales -x -q` — Expected: FAIL 404

- [ ] **Step 2: Implementar**

2a. Helpers — agregar en `app.py` después de `module_required` (sección "Módulos y permisos"):

```python
# ── Sucursales: helpers de contexto ──────────────────────────────────────────

def _sucursal_fija():
    """Sucursal del usuario actual, o None si tiene acceso a todas."""
    return session.get('user_sucursal') or None

def _sucursal_escritura(d):
    """Sucursal a usar al crear venta/turno/gasto. Usuario con sucursal fija:
    la suya SIEMPRE (ignora lo que mande el cliente). Si no, body o 1."""
    fija = _sucursal_fija()
    if fija:
        return int(fija)
    try:
        return int((d or {}).get('sucursal_id') or 1)
    except (TypeError, ValueError):
        return 1

def _sucursal_lectura():
    """Filtro de sucursal para reportes: int o None (= total). Usuario con
    sucursal fija solo ve la suya."""
    fija = _sucursal_fija()
    if fija:
        return int(fija)
    s = request.args.get('sucursal_id', '')
    try:
        return int(s) if s else None
    except ValueError:
        return None

def _suc_filtro(col='sucursal_id'):
    """(sql_extra, params_extra) para anexar a un WHERE existente."""
    suc = _sucursal_lectura()
    return (f" AND {col}=?", [suc]) if suc else ('', [])
```

2b. En `_global_auth`, el SELECT pasa de `"SELECT rol, activo, permisos FROM usuarios WHERE id=?"` a `"SELECT rol, activo, permisos, sucursal_id FROM usuarios WHERE id=?"`, y después de `session['user_permisos'] = permisos` agregar:

```python
        session['user_sucursal'] = u['sucursal_id']
```

2c. En `page_login`, después de `session['user_permisos'] = permisos` agregar:

```python
            session['user_sucursal'] = u['sucursal_id'] if 'sucursal_id' in u.keys() else None
```

2d. Ruta nueva (junto a las páginas, después de `page_despacho`):

```python
@app.route('/api/sucursales')
@login_required
def api_sucursales():
    with db() as c:
        rows = c.execute("SELECT * FROM sucursales WHERE activa=1 ORDER BY id").fetchall()
    return jsonify({'sucursales': [dict(r) for r in rows], 'fija': _sucursal_fija()})

@app.route('/api/sucursales/<int:sid>', methods=['PUT'])
@admin_required
def api_sucursales_update(sid):
    d = request.json or {}
    with db() as c:
        if not c.execute("SELECT id FROM sucursales WHERE id=?", (sid,)).fetchone():
            return jsonify({'error': 'No encontrada'}), 404
        for col in ('nombre', 'direccion', 'activa'):
            if col in d:
                c.execute(f"UPDATE sucursales SET {col}=? WHERE id=?", (d[col], sid))
    return jsonify({'ok': True})
```

- [ ] **Step 3: Test pasa + commit**

Run: `python -m pytest tests/test_sucursales.py -x -q` — Expected: PASS
```bash
git add app.py tests/test_sucursales.py
git commit -m "feat(multi-local): helpers de sucursal + sesion + /api/sucursales"
```

---

### Task 3: Helpers de stock con sucursal + backfill por sucursal

**Files:**
- Modify: `app.py` (`_get_or_create_inv_terminado`, `_descontar_lotes_fifo`, `api_inventario_create`, `api_producto_lotes_create`, `api_producto_lotes_list`, backfill de `init_db`)

- [ ] **Step 1: Firmas nuevas**

`_get_or_create_inv_terminado` completa queda:

```python
def _get_or_create_inv_terminado(c, producto_id, nombre, sucursal_id=1):
    """Obtiene o crea entrada en inventario para un producto terminado en una sucursal."""
    row = c.execute(
        "SELECT id FROM inventario WHERE bodega='productos_terminados' AND producto_id=? AND sucursal_id=?",
        (producto_id, sucursal_id)
    ).fetchone()
    if row:
        return row['id']
    cur = c.execute(
        "INSERT INTO inventario (ingrediente, bodega, stock_kg, unidad, alerta_minimo_kg, producto_id, sucursal_id)"
        " VALUES (?,?,?,?,?,?,?)",
        (nombre, 'productos_terminados', 0, 'unidades', 0, producto_id, sucursal_id)
    )
    return cur.lastrowid
```

`_descontar_lotes_fifo` — firma y queries:

```python
def _descontar_lotes_fifo(c, producto_id, cantidad, venta_id, lote_id_override=None,
                          sucursal_id=1, tipo='venta'):
```

Las 3 queries de lotes agregan `AND sucursal_id=?` con `sucursal_id` al final de los params; el INSERT a `lote_movimientos` usa `tipo` en vez del literal `'venta'`. (La cláusula completa: `WHERE producto_id=? AND cantidad_actual>0 AND sucursal_id=? ORDER BY fecha_elaboracion ASC`.)

`_restaurar_lotes_venta`: SIN cambios (los movimientos referencian lote, que ya tiene sucursal).

- [ ] **Step 2: Callers de producción (siempre sucursal 1 — default, no requieren cambio de args) y upserts**

2a. `api_inventario_create` rama insumos: el `ON CONFLICT(ingrediente)` pasa a `ON CONFLICT(ingrediente, bodega, sucursal_id)` y el INSERT agrega columna `sucursal_id` con valor `1` (insumos centralizados).

2b. `api_producto_lotes_create`: agregar `sucursal = _sucursal_escritura(d)` al inicio; el INSERT a `producto_lotes` agrega columna `sucursal_id` con `sucursal`; `_get_or_create_inv_terminado(c, prod_id, prod['nombre'], sucursal)`.

2c. `api_producto_lotes_list`: el SELECT de lotes agrega `sucursal_id` a las columnas devueltas; sin filtro (muestra todo, la UI agrupa).

2d. Los 3 INSERT a `producto_lotes` de producción (en `api_plan_confirmar`, `api_produccion_batch_hornear`, `api_produccion_manual`): sin cambio de código — la columna `sucursal_id` tiene DEFAULT 1 tanto en el CREATE (DB fresca, Task 1 paso 3b-bis) como en el ALTER (DB existente), y producción siempre es Recoleta. Verificar con un test rápido que un lote creado por `/api/produccion/manual` queda con `sucursal_id=1`.

- [ ] **Step 3: Backfill de `init_db` por sucursal**

Reemplazar el bloque actual "Backfill lotes: si productos.stock supera la suma de lotes vigentes..." completo por:

```python
        # Backfill lotes POR SUCURSAL: inventario es la verdad por local.
        # Si inventario(p,s) > Σ lotes(p,s), crear lote de ajuste en esa sucursal.
        desync_rows = c.execute("""
            SELECT i.producto_id, i.sucursal_id, i.stock_kg,
                   COALESCE((SELECT SUM(pl.cantidad_actual) FROM producto_lotes pl
                             WHERE pl.producto_id = i.producto_id
                               AND pl.sucursal_id = i.sucursal_id), 0) AS lotes
            FROM inventario i JOIN productos p ON p.id = i.producto_id
            WHERE i.bodega='productos_terminados' AND p.activo=1
        """).fetchall()
        for r in desync_rows:
            diff = round((r['stock_kg'] or 0) - r['lotes'], 3)
            if diff > 0:
                c.execute(
                    """INSERT INTO producto_lotes
                       (producto_id, sucursal_id, fecha_elaboracion, cantidad_inicial, cantidad_actual, notas)
                       VALUES (?, ?, date('now'), ?, ?, 'Ajuste sincronización lotes')""",
                    (r['producto_id'], r['sucursal_id'], diff, diff)
                )
        # productos.stock = TOTAL entre sucursales (inventario manda al arrancar)
        c.execute("""
            UPDATE productos SET stock = (
                SELECT COALESCE(SUM(i.stock_kg), 0) FROM inventario i
                WHERE i.bodega='productos_terminados' AND i.producto_id = productos.id)
            WHERE activo=1
              AND id IN (SELECT producto_id FROM inventario WHERE bodega='productos_terminados')
        """)
```

- [ ] **Step 4: Compilar + suite + commit**

Run: `python -m py_compile app.py && python -m pytest tests/ -q` — Expected: todos pasan
```bash
git add app.py
git commit -m "feat(multi-local): stock helpers y backfill por sucursal"
```

---

### Task 4: Ventas ERP y suscripciones por sucursal

**Files:**
- Modify: `app.py` (`api_ventas_create/update/delete`, `api_ventas`, `api_ventas_resumen`, `api_registrar_entrega`, `api_agentes_ventas`)
- Test: `tests/test_sucursales.py`

- [ ] **Step 1: Test que falla**

```python
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
```

Run: `python -m pytest tests/test_sucursales.py::test_venta_descuenta_solo_su_sucursal -x -q` — Expected: FAIL (descuenta de sucursal 1 / sucursal_id=1)

- [ ] **Step 2: Implementar en `api_ventas_create`**

Al inicio (tras calcular `total`): `suc = _sucursal_escritura(d)`.
El INSERT a `ventas` agrega columna `sucursal_id` con valor `suc`.
El UPDATE de inventario agrega `AND sucursal_id=?` (param `suc`).
La llamada FIFO: `_descontar_lotes_fifo(c, int(i['producto_id']), float(i['cantidad']), vid, sucursal_id=suc)`.

- [ ] **Step 3: `api_ventas_update` y `api_ventas_delete`**

En ambos, el fetch inicial de la venta pasa a `SELECT id, sucursal_id FROM ventas WHERE id=?` y se guarda `v_suc = row['sucursal_id']`. Todos los UPDATE de inventario (restaurar y re-descontar) agregan `AND sucursal_id=?` (param `v_suc`). El re-descuento FIFO usa `sucursal_id=v_suc`.

- [ ] **Step 4: Lecturas y resto**

- `api_ventas` (GET lista): después de construir `q` base, agregar:
```python
        sw, sp = _suc_filtro('v.sucursal_id')
        q += sw; params += sp
```
- `api_ventas_resumen`: en la función interna `t(d1,d2)` y en los 2 COUNT de pendientes, anexar `sw`/`sp` igual (filtro `sucursal_id`).
- `api_registrar_entrega`: el UPDATE de inventario agrega `AND sucursal_id=1`; FIFO con `sucursal_id=1` (despachos salen de Recoleta).
- `api_agentes_ventas`: INSERT agrega `sucursal_id` con `int(d.get('sucursal_id') or 1)`.

- [ ] **Step 5: Test pasa + suite + commit**

Run: `python -m pytest tests/test_sucursales.py tests/ -q` — Expected: PASS
```bash
git add app.py tests/test_sucursales.py
git commit -m "feat(multi-local): ventas ERP y suscripciones por sucursal"
```

---

### Task 5: POS por sucursal

**Files:**
- Modify: `pos.py` (`api_turno_abrir`, `api_pos_productos`, `api_pos_venta`)
- Modify: `templates/pos_caja.html` (selector al abrir turno)
- Test: `tests/test_sucursales.py`

- [ ] **Step 1: Test que falla**

```python
def test_pos_venta_usa_sucursal_del_turno(client):
    tc, app_mod = client
    _crear_stock(app_mod, 1, 2, 10)
    r = tc.post('/api/pos/turno/abrir', json={'monto_inicial': 0, 'sucursal_id': 2})
    assert r.status_code == 200
    r = tc.post('/api/pos/venta', json={'items': [{'producto_id': 1, 'nombre': 'Marraqueta',
                                                   'cantidad': 3, 'precio_unitario': 200}],
                                        'metodo_pago': 'efectivo', 'monto_efectivo': 600})
    assert r.status_code == 200
    inv2, lotes2, _ = _stock(app_mod, 1, 2)
    assert inv2 == 7 and lotes2 == 7
    with app_mod.db() as c:
        v = c.execute("SELECT sucursal_id FROM ventas ORDER BY id DESC LIMIT 1").fetchone()
    assert v['sucursal_id'] == 2
```

Run: `python -m pytest tests/test_sucursales.py::test_pos_venta_usa_sucursal_del_turno -x -q` — Expected: FAIL

- [ ] **Step 2: `pos.py`**

2a. `api_turno_abrir` — antes del INSERT:

```python
    fija = session.get('user_sucursal')
    try:
        sucursal = int(fija) if fija else int(body.get('sucursal_id') or 1)
    except (TypeError, ValueError):
        sucursal = 1
```
INSERT agrega columna `sucursal_id` con `sucursal`.

2b. `api_pos_productos` — al inicio del `with db() as c:` obtener la sucursal del turno abierto:

```python
        turno = c.execute(
            "SELECT sucursal_id FROM pos_turnos WHERE usuario_id=? AND estado='abierto' ORDER BY id DESC LIMIT 1",
            (session.get('user_id'),)
        ).fetchone()
        suc = turno['sucursal_id'] if turno else (session.get('user_sucursal') or 1)
```
Los 3 JOIN `LEFT JOIN inventario inv ON inv.producto_id=p.id AND inv.bodega='productos_terminados'` agregan `AND inv.sucursal_id=?` con `suc` como primer parámetro de la query (cuidado con el orden de params en la rama con `q`: `(suc, f'%{q}%')`).

2c. `api_pos_venta` — el `turno` ya se consulta con `SELECT *`; usar `suc = turno['sucursal_id']`. INSERT a `ventas` agrega `sucursal_id` con `suc`; UPDATE inventario agrega `AND sucursal_id=?`; FIFO `_app_mod._descontar_lotes_fifo(c, int(item['producto_id']), float(item['cantidad']), venta_id, sucursal_id=suc)`.

- [ ] **Step 3: Selector en `pos_caja.html`**

Leer el template, ubicar el formulario/modal de "Abrir caja" y la función JS que hace `api('POST','/api/pos/turno/abrir', ...)`. Agregar sobre el botón de abrir:

```html
<div class="form-group" id="suc-group" style="display:none">
  <label class="form-label">Sucursal</label>
  <select id="turno-sucursal" class="form-control"></select>
</div>
```

En `{% block scripts %}` del mismo template:

```js
(async () => {
  const d = await api('GET', '/api/sucursales');
  if (d.fija) return;  // usuario de local fijo: el server decide
  const sel = document.getElementById('turno-sucursal');
  sel.innerHTML = d.sucursales.map(s => `<option value="${s.id}">${s.nombre}</option>`).join('');
  document.getElementById('suc-group').style.display = '';
})();
```

Y en el body del POST de abrir turno agregar `sucursal_id: (document.getElementById('turno-sucursal')||{}).value || 1`.

- [ ] **Step 4: Test pasa + suite + commit**

Run: `python -m py_compile app.py pos.py && python -m pytest tests/ -q` — Expected: PASS
```bash
git add pos.py templates/pos_caja.html tests/test_sucursales.py
git commit -m "feat(multi-local): POS por sucursal (turno, stock, venta)"
```

---

### Task 6: API Traspasos

**Files:**
- Modify: `app.py` (tablas en `init_db`, módulo en `MODULOS`, rutas nuevas)
- Test: `tests/test_sucursales.py`

- [ ] **Step 1: Tests que fallan**

```python
def test_traspaso_feliz(client):
    tc, app_mod = client
    _crear_stock(app_mod, 1, 1, 20)
    _, _, total_antes = _stock(app_mod, 1, 1)
    r = tc.post('/api/traspasos', json={'origen_id': 1, 'destino_id': 2,
                                        'items': [{'producto_id': 1, 'cantidad': 5}]})
    assert r.status_code == 201, r.get_json()
    inv1, lotes1, total = _stock(app_mod, 1, 1)
    inv2, lotes2, _ = _stock(app_mod, 1, 2)
    assert inv2 == 5 and lotes2 == 5
    assert inv1 == lotes1                  # origen sigue cuadrado
    assert total == total_antes            # el total NO cambia
    with app_mod.db() as c:
        lote = c.execute("""SELECT notas FROM producto_lotes WHERE sucursal_id=2
                            ORDER BY id DESC LIMIT 1""").fetchone()
    assert 'Traspaso #' in lote['notas']


def test_traspaso_sin_stock(client):
    tc, app_mod = client
    r = tc.post('/api/traspasos', json={'origen_id': 2, 'destino_id': 1,
                                        'items': [{'producto_id': 1, 'cantidad': 999}]})
    assert r.status_code == 400
    assert 'insuficiente' in r.get_json()['error'].lower()
```

Run: `python -m pytest tests/test_sucursales.py -k traspaso -x -q` — Expected: FAIL 404

- [ ] **Step 2: Tablas (en `init_db`, junto al executescript POS)**

```python
        c.executescript("""
            CREATE TABLE IF NOT EXISTS traspasos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha      TEXT    NOT NULL DEFAULT (datetime('now')),
                origen_id  INTEGER NOT NULL,
                destino_id INTEGER NOT NULL,
                usuario    TEXT    NOT NULL DEFAULT '',
                notas      TEXT    NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS traspaso_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                traspaso_id INTEGER NOT NULL REFERENCES traspasos(id) ON DELETE CASCADE,
                producto_id INTEGER NOT NULL,
                cantidad    REAL    NOT NULL
            );
        """)
```

- [ ] **Step 3: Módulo + rutas**

En `MODULOS`, después de `('inventario', ...)`: `('traspasos', 'Traspasos', 'bi-arrow-left-right'),`

Rutas (sección nueva después del bloque INVENTARIO):

```python
# ════════════════════════════════════════════════════════════════════════════
# TRASPASOS ENTRE SUCURSALES
# ════════════════════════════════════════════════════════════════════════════

@app.route('/traspasos')
@module_required('traspasos')
def page_traspasos():
    return render_template('traspasos.html', active='traspasos')

@app.route('/api/traspasos', methods=['GET'])
@login_required
def api_traspasos_list():
    with db() as c:
        rows = c.execute("""
            SELECT t.*, so.nombre AS origen_nombre, sd.nombre AS destino_nombre
            FROM traspasos t
            JOIN sucursales so ON so.id = t.origen_id
            JOIN sucursales sd ON sd.id = t.destino_id
            ORDER BY t.id DESC LIMIT 100
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d['items'] = [dict(i) for i in c.execute(
                """SELECT ti.*, p.nombre AS producto_nombre
                   FROM traspaso_items ti JOIN productos p ON p.id = ti.producto_id
                   WHERE ti.traspaso_id=?""", (r['id'],)).fetchall()]
            result.append(d)
    return jsonify(result)

@app.route('/api/traspasos', methods=['POST'])
@login_required
def api_traspasos_create():
    d = request.json or {}
    try:
        origen  = int(d.get('origen_id') or 1)
        destino = int(d.get('destino_id') or 0)
    except (TypeError, ValueError):
        return jsonify({'error': 'Sucursales inválidas'}), 400
    items = d.get('items', [])
    if not destino or destino == origen:
        return jsonify({'error': 'Elige una sucursal destino distinta del origen'}), 400
    if not items:
        return jsonify({'error': 'Agrega al menos un producto'}), 400
    fija = _sucursal_fija()
    if fija and origen != int(fija):
        return jsonify({'error': 'Solo puedes traspasar desde tu sucursal'}), 403
    with db() as c:
        if not c.execute("SELECT id FROM sucursales WHERE id=? AND activa=1", (destino,)).fetchone():
            return jsonify({'error': 'Sucursal destino no existe'}), 404
        # Validar TODO el stock antes de mover nada (sin traspasos parciales)
        for it in items:
            pid, cant = int(it['producto_id']), float(it['cantidad'])
            if cant <= 0:
                return jsonify({'error': 'Las cantidades deben ser mayores a 0'}), 400
            row = c.execute(
                """SELECT stock_kg FROM inventario
                   WHERE bodega='productos_terminados' AND producto_id=? AND sucursal_id=?""",
                (pid, origen)).fetchone()
            disponible = row['stock_kg'] if row else 0
            if disponible < cant:
                p = c.execute("SELECT nombre FROM productos WHERE id=?", (pid,)).fetchone()
                nombre = p['nombre'] if p else f'producto {pid}'
                return jsonify({'error': f'Stock insuficiente de {nombre}: hay {disponible:g}, pides {cant:g}'}), 400
        cur = c.execute(
            "INSERT INTO traspasos (origen_id, destino_id, usuario, notas) VALUES (?,?,?,?)",
            (origen, destino, session.get('user_nombre', ''), d.get('notas', '')))
        tid = cur.lastrowid
        for it in items:
            pid, cant = int(it['producto_id']), float(it['cantidad'])
            p = c.execute("SELECT nombre FROM productos WHERE id=?", (pid,)).fetchone()
            c.execute("INSERT INTO traspaso_items (traspaso_id, producto_id, cantidad) VALUES (?,?,?)",
                      (tid, pid, cant))
            # Descontar del origen
            c.execute("""UPDATE inventario SET stock_kg=stock_kg-?, ultima_actualizacion=date('now')
                         WHERE bodega='productos_terminados' AND producto_id=? AND sucursal_id=?""",
                      (cant, pid, origen))
            movimientos = _descontar_lotes_fifo(c, pid, cant, None,
                                                sucursal_id=origen, tipo='traspaso')
            # Sumar al destino con lotes espejo (misma fecha de elaboración)
            inv_dest = _get_or_create_inv_terminado(c, pid, p['nombre'], destino)
            c.execute("""UPDATE inventario SET stock_kg=stock_kg+?, ultima_actualizacion=date('now')
                         WHERE id=?""", (cant, inv_dest))
            movido = 0.0
            for lote_id, cantidad_mov in movimientos:
                fecha_elab = c.execute(
                    "SELECT fecha_elaboracion FROM producto_lotes WHERE id=?", (lote_id,)
                ).fetchone()['fecha_elaboracion']
                c.execute("""INSERT INTO producto_lotes
                             (producto_id, sucursal_id, fecha_elaboracion, cantidad_inicial, cantidad_actual, notas)
                             VALUES (?,?,?,?,?,?)""",
                          (pid, destino, fecha_elab, cantidad_mov, cantidad_mov, f'Traspaso #{tid}'))
                movido += cantidad_mov
            resto = round(cant - movido, 3)
            if resto > 0:  # drift histórico: lotes de origen no cubrían el inventario
                c.execute("""INSERT INTO producto_lotes
                             (producto_id, sucursal_id, fecha_elaboracion, cantidad_inicial, cantidad_actual, notas)
                             VALUES (?,?,date('now'),?,?,?)""",
                          (pid, destino, resto, resto, f'Traspaso #{tid} (ajuste)'))
    return jsonify({'ok': True, 'id': tid}), 201
```

- [ ] **Step 4: Tests pasan + commit**

Run: `python -m pytest tests/test_sucursales.py -q` — Expected: PASS
```bash
git add app.py tests/test_sucursales.py
git commit -m "feat(multi-local): API traspasos con lotes espejo FIFO"
```

---

### Task 7: UI Traspasos + nav

**Files:**
- Create: `templates/traspasos.html`
- Modify: `templates/base.html` (entrada de nav)

- [ ] **Step 1: Template completo**

```html
{% extends "base.html" %}
{% block title %}Traspasos · Aurora Bakers{% endblock %}
{% block page_title %}Traspasos entre locales{% endblock %}

{% block content %}
<div class="card mb-24" style="max-width:640px">
  <div class="card-title"><i class="bi bi-arrow-left-right"></i> Nuevo traspaso</div>
  <div style="display:flex;gap:.75rem;margin-bottom:.75rem">
    <div class="form-group" style="flex:1;margin:0">
      <label class="form-label">Origen</label>
      <select id="t-origen" class="form-control" onchange="renderLineas()"></select>
    </div>
    <div class="form-group" style="flex:1;margin:0">
      <label class="form-label">Destino</label>
      <select id="t-destino" class="form-control"></select>
    </div>
  </div>
  <div id="t-lineas"></div>
  <button class="btn btn-ghost" style="font-size:.8rem" onclick="agregarLinea()">
    <i class="bi bi-plus"></i> Agregar producto</button>
  <div class="form-group" style="margin:.75rem 0 0">
    <input id="t-notas" class="form-control" placeholder="Notas (opcional)" />
  </div>
  <button class="btn btn-primary" style="margin-top:.75rem" onclick="guardarTraspaso()">
    <i class="bi bi-check2-circle"></i> Registrar traspaso</button>
</div>

<div class="card">
  <div class="card-title"><i class="bi bi-clock-history"></i> Historial</div>
  <table class="table" style="width:100%;font-size:.82rem">
    <thead><tr><th>Fecha</th><th>Origen → Destino</th><th>Productos</th><th>Usuario</th></tr></thead>
    <tbody id="t-historial"><tr><td colspan="4" style="color:var(--text-3)">Cargando…</td></tr></tbody>
  </table>
</div>
{% endblock %}

{% block scripts %}
<script>
let SUCURSALES = [], PRODUCTOS = [], STOCK = {};  // STOCK[sucursal][producto_id] = unidades

async function init() {
  const [suc, prods, lotes] = await Promise.all([
    api('GET', '/api/sucursales'),
    api('GET', '/api/productos'),
    api('GET', '/api/producto-lotes'),
  ]);
  SUCURSALES = suc.sucursales;
  PRODUCTOS  = prods;
  STOCK = {};
  for (const p of lotes) {
    for (const l of (p.lotes || [])) {
      const s = l.sucursal_id || 1;
      STOCK[s] = STOCK[s] || {};
      STOCK[s][p.producto_id] = (STOCK[s][p.producto_id] || 0) + l.cantidad_actual;
    }
  }
  const opts = SUCURSALES.map(s => `<option value="${s.id}">${s.nombre}</option>`).join('');
  document.getElementById('t-origen').innerHTML  = opts;
  document.getElementById('t-destino').innerHTML = opts;
  document.getElementById('t-destino').value = SUCURSALES.length > 1 ? SUCURSALES[1].id : '';
  if (suc.fija) {
    document.getElementById('t-origen').value = suc.fija;
    document.getElementById('t-origen').disabled = true;
  }
  agregarLinea();
  loadHistorial();
}

function stockDe(sucursal, pid) {
  return (STOCK[sucursal] && STOCK[sucursal][pid]) || 0;
}

function agregarLinea() {
  const origen = document.getElementById('t-origen').value;
  const div = document.createElement('div');
  div.className = 't-linea';
  div.style.cssText = 'display:flex;gap:.5rem;margin-bottom:.5rem;align-items:center';
  div.innerHTML = `
    <select class="form-control t-prod" style="flex:2" onchange="updStock(this)">
      <option value="">— producto —</option>
      ${PRODUCTOS.map(p => `<option value="${p.id}">${p.nombre}</option>`).join('')}
    </select>
    <input class="form-control t-cant" type="number" min="1" placeholder="Cant." style="flex:1" />
    <span class="t-stock" style="font-size:.72rem;color:var(--text-3);min-width:90px"></span>
    <button class="btn btn-ghost" style="padding:.2rem .5rem" onclick="this.parentElement.remove()">×</button>`;
  document.getElementById('t-lineas').appendChild(div);
}

function updStock(sel) {
  const origen = document.getElementById('t-origen').value;
  const span = sel.parentElement.querySelector('.t-stock');
  span.textContent = sel.value ? `disp: ${stockDe(origen, parseInt(sel.value))}` : '';
}

function renderLineas() {
  document.querySelectorAll('.t-prod').forEach(updStock);
}

async function guardarTraspaso() {
  const items = [...document.querySelectorAll('.t-linea')].map(l => ({
    producto_id: parseInt(l.querySelector('.t-prod').value || 0),
    cantidad: parseFloat(l.querySelector('.t-cant').value || 0),
  })).filter(i => i.producto_id && i.cantidad > 0);
  if (!items.length) { toast('Agrega al menos un producto con cantidad', 'error'); return; }
  try {
    await api('POST', '/api/traspasos', {
      origen_id:  parseInt(document.getElementById('t-origen').value),
      destino_id: parseInt(document.getElementById('t-destino').value),
      items, notas: document.getElementById('t-notas').value,
    });
    toast('Traspaso registrado');
    document.getElementById('t-lineas').innerHTML = '';
    document.getElementById('t-notas').value = '';
    init();
  } catch (e) { toast(e.message, 'error'); }
}

async function loadHistorial() {
  const rows = await api('GET', '/api/traspasos');
  const tb = document.getElementById('t-historial');
  if (!rows.length) { tb.innerHTML = '<tr><td colspan="4" style="color:var(--text-3)">Sin traspasos.</td></tr>'; return; }
  tb.innerHTML = rows.map(t => `
    <tr>
      <td>${t.fecha}</td>
      <td>${t.origen_nombre} → ${t.destino_nombre}</td>
      <td>${t.items.map(i => `${i.producto_nombre} ×${i.cantidad}`).join(', ')}</td>
      <td>${t.usuario || '—'}</td>
    </tr>`).join('');
}

init();
</script>
{% endblock %}
```

- [ ] **Step 2: Nav en `base.html`**

Ubicar la entrada de Inventario en el sidebar (sección PRODUCCIÓN) y agregar debajo, siguiendo EXACTAMENTE el patrón de clases/condición de los ítems vecinos:

```html
{% if user_es_admin or 'traspasos' in user_permisos %}
  <!-- mismo markup que el ítem Inventario, con href="/traspasos",
       icono bi-arrow-left-right, label "Traspasos", active=='traspasos' -->
{% endif %}
```

- [ ] **Step 3: Verificación manual + commit**

Run: `python -m pytest tests/ -q` (sin regresiones) y abrir `/traspasos` logueado como admin: crear traspaso Recoleta→Local 2 de 1 unidad y verlo en historial.
```bash
git add templates/traspasos.html templates/base.html
git commit -m "feat(multi-local): pagina de traspasos + nav"
```

---

### Task 8: Usuarios — sucursal asignada + perfiles preset

**Files:**
- Modify: `app.py` (`PERFILES`, `/api/admin/perfiles`, create/update usuarios)
- Modify: `templates/admin_usuarios.html`
- Test: `tests/test_sucursales.py`

- [ ] **Step 1: Tests que fallan**

```python
def test_perfiles_endpoint(client):
    tc, app_mod = client
    # el conftest crea usuario rol 'usuario'; este endpoint es admin-only
    with app_mod.db() as c:
        from werkzeug.security import generate_password_hash
        c.execute("INSERT INTO usuarios (nombre,email,password,rol) VALUES ('A','a@a.cl',?, 'admin')",
                  (generate_password_hash('x'),))
    ta = app_mod.app.test_client()
    ta.post('/login', data={'email': 'a@a.cl', 'password': 'x'})
    d = ta.get('/api/admin/perfiles').get_json()
    assert set(d['perfiles'].keys()) == {'cajero', 'produccion', 'encargado', 'contador'}
    assert 'pos' in d['perfiles']['cajero']
    assert len(d['sucursales']) == 2


def test_usuario_sucursal_fija_forzada(client):
    tc, app_mod = client
    from werkzeug.security import generate_password_hash
    with app_mod.db() as c:
        c.execute("""INSERT INTO usuarios (nombre,email,password,rol,permisos,sucursal_id)
                     VALUES ('Caja2','c2@a.cl',?,'usuario','["pos","ventas"]',2)""",
                  (generate_password_hash('x'),))
    _crear_stock(app_mod, 1, 1, 10)
    _crear_stock(app_mod, 1, 2, 10)
    t2 = app_mod.app.test_client()
    t2.post('/login', data={'email': 'c2@a.cl', 'password': 'x'})
    # Intenta vender "en sucursal 1": el server fuerza la 2
    r = t2.post('/api/ventas', json={'sucursal_id': 1,
                                     'items': [{'producto_id': 1, 'cantidad': 2, 'precio_unitario': 100}]})
    assert r.status_code == 201
    with app_mod.db() as c:
        v = c.execute("SELECT sucursal_id FROM ventas ORDER BY id DESC LIMIT 1").fetchone()
    assert v['sucursal_id'] == 2
```

Run: `python -m pytest tests/test_sucursales.py -k "perfiles or fija" -x -q` — Expected: FAIL

- [ ] **Step 2: Backend**

2a. Junto a `MODULOS_DEFAULT`:

```python
# Perfiles preset por función — rellenan los checkboxes de módulos al crear usuario
PERFILES = {
    'cajero':     ['pos', 'ventas', 'clientes', 'despacho'],
    'produccion': ['produccion', 'inventario', 'traspasos', 'reporte_produccion', 'agenda'],
    'encargado':  ['pos', 'ventas', 'clientes', 'despacho', 'suscripciones', 'traspasos',
                   'reportes', 'reporte_ventas', 'agenda'],
    'contador':   ['finanzas', 'gastos', 'reportes', 'reporte_ventas'],
}
```

2b. Endpoint (junto a `api_admin_modulos`):

```python
@app.route('/api/admin/perfiles', methods=['GET'])
@admin_required
def api_admin_perfiles():
    with db() as c:
        sucs = [dict(r) for r in c.execute("SELECT id, nombre FROM sucursales WHERE activa=1 ORDER BY id")]
    return jsonify({'perfiles': PERFILES, 'sucursales': sucs})
```

2c. `api_admin_usuarios_create`: el INSERT agrega columna `sucursal_id` con:
```python
    sucursal_id = d.get('sucursal_id') or None
```
2d. `api_admin_usuarios_update`: agregar `'sucursal_id'` al manejo —
```python
        if 'sucursal_id' in d:
            c.execute("UPDATE usuarios SET sucursal_id=? WHERE id=?", (d['sucursal_id'] or None, uid))
```
2e. `api_admin_usuarios_list`: agregar `sucursal_id` a las columnas del SELECT.

- [ ] **Step 3: UI `admin_usuarios.html`**

3a. En el modal, después del `form-group` del Rol, insertar:

```html
        <div class="form-group" style="margin:0">
          <label class="form-label">Sucursal</label>
          <select id="u-sucursal" class="form-control">
            <option value="">Todas (acceso global)</option>
          </select>
        </div>
        <div class="form-group" style="margin:0">
          <label class="form-label">Perfil rápido</label>
          <div id="perfil-btns" style="display:flex;gap:.35rem;flex-wrap:wrap"></div>
        </div>
```

3b. En `{% block scripts %}`:

- Variable global: `let perfilesData = { perfiles: {}, sucursales: [] };`
- En `loadUsuarios()` el `Promise.all` agrega `api('GET', '/api/admin/perfiles')` como tercer elemento → `[usuarios, modulos, perfilesData] = await Promise.all([...])`, y al final:

```js
  document.getElementById('u-sucursal').innerHTML =
    '<option value="">Todas (acceso global)</option>' +
    perfilesData.sucursales.map(s => `<option value="${s.id}">${s.nombre}</option>`).join('');
  const NOMBRES = { cajero:'Cajero', produccion:'Jefe Producción', encargado:'Encargado local', contador:'Contador' };
  document.getElementById('perfil-btns').innerHTML =
    Object.keys(perfilesData.perfiles).map(k =>
      `<button type="button" class="btn btn-ghost" style="font-size:.72rem;padding:.25rem .55rem"
        onclick="aplicarPerfil('${k}')">${NOMBRES[k] || k}</button>`).join('');
```

- Función nueva:

```js
function aplicarPerfil(key) {
  renderPermisosGrid(perfilesData.perfiles[key] || []);
  toast(`Perfil aplicado — ajusta los módulos si necesitas`);
}
```

- `abrirNuevo()`: agregar `document.getElementById('u-sucursal').value = '';`
- `editarUsuario(id)`: agregar `document.getElementById('u-sucursal').value = u.sucursal_id || '';`
- `guardarUsuario()`: agregar a ambos bodies `sucursal_id: document.getElementById('u-sucursal').value || null`.
- Tabla: header agrega `<th>Sucursal</th>` (entre Rol y Estado) y la fila agrega
  `<td style="color:var(--text-2)">${u.sucursal_id ? (perfilesData.sucursales.find(s=>s.id===u.sucursal_id)||{}).nombre || u.sucursal_id : 'Todas'}</td>` — actualizar ambos `colspan="6"` a `colspan="7"`.

- [ ] **Step 4: Tests pasan + commit**

Run: `python -m pytest tests/test_sucursales.py tests/ -q` — Expected: PASS
```bash
git add app.py templates/admin_usuarios.html tests/test_sucursales.py
git commit -m "feat(multi-local): sucursal por usuario + perfiles preset"
```

---

### Task 9: Reportes con filtro de sucursal (backend)

**Files:**
- Modify: `app.py` — endpoints listados abajo
- Test: `tests/test_sucursales.py`

- [ ] **Step 1: Test que falla**

```python
def test_resumen_filtra_por_sucursal(client):
    tc, app_mod = client
    _crear_stock(app_mod, 1, 1, 50)
    _crear_stock(app_mod, 1, 2, 50)
    tc.post('/api/ventas', json={'sucursal_id': 1, 'items': [{'producto_id': 1, 'cantidad': 1, 'precio_unitario': 1000}]})
    tc.post('/api/ventas', json={'sucursal_id': 2, 'items': [{'producto_id': 1, 'cantidad': 1, 'precio_unitario': 3000}]})
    total = tc.get('/api/reportes/resumen?periodo=hoy').get_json()
    s1 = tc.get('/api/reportes/resumen?periodo=hoy&sucursal_id=1').get_json()
    s2 = tc.get('/api/reportes/resumen?periodo=hoy&sucursal_id=2').get_json()
    assert total['ventas_hoy'] == 4000
    assert s1['ventas_hoy'] == 1000
    assert s2['ventas_hoy'] == 3000
```

Run: `python -m pytest tests/test_sucursales.py::test_resumen_filtra_por_sucursal -x -q` — Expected: FAIL (s1 == 4000)

- [ ] **Step 2: Patrón único**

Al inicio de cada endpoint: `sw, sp = _suc_filtro()` (o `_suc_filtro('v.sucursal_id')` si la query usa alias `v`). Cada query de ventas/gastos anexa `{sw}` al WHERE y `+ sp` a los params. Endpoints y queries a tocar:

- `api_reportes_resumen`: ventas_hoy, ventas_ayer, ventas_periodo, ventas_prev, count_periodo, canal_rows, ingresos, por_cobrar, despachos_pendientes (todas `ventas` → `{sw}`/`sp`); gastos (tabla gastos, mismo filtro). `stock_bajo`: si `_sucursal_lectura()` devuelve sucursal, reemplazar la query por:
```python
            stock_bajo = c.execute(
                """SELECT p.nombre, i.stock_kg AS stock FROM inventario i
                   JOIN productos p ON p.id=i.producto_id
                   WHERE i.bodega='productos_terminados' AND i.sucursal_id=? AND p.activo=1
                     AND i.stock_kg <= 5 ORDER BY i.stock_kg ASC LIMIT 10""",
                (_sucursal_lectura(),)).fetchall()
```
(else: query actual sobre `productos.stock`).
- `api_rep_ventas`, `api_rep_canales`, `api_rep_kpis`: queries sobre `ventas` → `{sw}`/`sp`. En `api_rep_kpis` también `horeca`/`cliente`.
- `api_rep_productos`: query con alias `v` → `_suc_filtro('v.sucursal_id')`.
- `api_finanzas_pl`: cobrado_rows, facturado_rows (ventas), gastos_rows (gastos), pendientes_rows (`v.`), canal_rows (`v.`), margenes (en el subquery: `WHERE vn.fecha>=?{swv}` con `_suc_filtro('vn.sucursal_id')`).
- `api_finanzas_flujo_caja`: ventas_por_cobrar agrega `{sw}`; subs_renovacion solo cuenta si el filtro es None o 1 (suscripciones despachan de Recoleta):
```python
            if suc in (None, 1):
                subs_renovacion = c.execute(...)  # query actual
            else:
                subs_renovacion = {'total': 0, 'cnt': 0}
```
(con `suc = _sucursal_lectura()` al inicio).
- `api_movil_stats`: `vsum`, `costo_mes` (alias `v`), `top` (alias `v`), `despachos_pendientes`.

- [ ] **Step 3: Test pasa + suite + commit**

Run: `python -m pytest tests/ -q` — Expected: PASS
```bash
git add app.py tests/test_sucursales.py
git commit -m "feat(multi-local): filtro de sucursal en reportes y finanzas"
```

---

### Task 10: Selector de sucursal en UI de reportes

**Files:**
- Modify: `templates/reportes_resumen.html`, `templates/reporte_ventas.html`, `templates/finanzas.html`, `templates/movil.html`

- [ ] **Step 1: `reportes_resumen.html` (patrón de referencia)**

En el toolbar (después del div `filter-tabs`):

```html
  <select id="sel-sucursal" class="form-control" style="width:auto;display:none;margin-left:.75rem"></select>
```

En `{% block scripts %}` (antes de `loadAll();` final):

```js
let SUC_PARAM = '';
async function initSucursales() {
  const d = await api('GET', '/api/sucursales');
  if (d.fija) return;  // usuario de un solo local: sin selector, el server filtra
  const sel = document.getElementById('sel-sucursal');
  sel.innerHTML = '<option value="">Total (ambos locales)</option>' +
    d.sucursales.map(s => `<option value="${s.id}">${s.nombre}</option>`).join('');
  sel.style.display = '';
  sel.onchange = () => { SUC_PARAM = sel.value ? `&sucursal_id=${sel.value}` : ''; loadAll(); };
}
initSucursales();
```

Y la línea del fetch cambia a:
```js
  const d = await api('GET', `/api/reportes/resumen?periodo=${periodo}${SUC_PARAM}`);
```

- [ ] **Step 2: Replicar en los otros 3 templates**

Mismo snippet HTML + JS (copiar literal). En cada template, ubicar TODAS las llamadas `api('GET', '/api/...')` hacia endpoints filtrables (reportes, finanzas, movil) y anexar `${SUC_PARAM}` (con `?` o `&` según corresponda). En `movil.html` el refresh automático ya llama una función central — anexar ahí.

- [ ] **Step 3: Verificación manual + commit**

Abrir `/reportes` como admin: selector visible, cambia cifras al elegir local. Crear usuario de prueba con sucursal fija → no ve selector.
```bash
git add templates/reportes_resumen.html templates/reporte_ventas.html templates/finanzas.html templates/movil.html
git commit -m "feat(multi-local): selector de sucursal en reportes"
```

---

### Task 11: Backup y restore de DB (admin)

**Files:**
- Modify: `app.py`
- Test: `tests/test_sucursales.py`

- [ ] **Step 1: Test que falla**

```python
def test_backup_db(client):
    tc, app_mod = client
    from werkzeug.security import generate_password_hash
    with app_mod.db() as c:
        c.execute("INSERT INTO usuarios (nombre,email,password,rol) VALUES ('A','adm@a.cl',?, 'admin')",
                  (generate_password_hash('x'),))
    ta = app_mod.app.test_client()
    ta.post('/login', data={'email': 'adm@a.cl', 'password': 'x'})
    r = ta.get('/api/admin/backup-db')
    assert r.status_code == 200
    assert r.data[:16] == b'SQLite format 3\x00'
    # no-admin: bloqueado
    assert tc.get('/api/admin/backup-db').status_code == 403
```

Run: `python -m pytest tests/test_sucursales.py::test_backup_db -x -q` — Expected: FAIL 404

- [ ] **Step 2: Implementar (junto a los endpoints admin)**

Agregar `send_file` al import de flask (línea 5) y:

```python
@app.route('/api/admin/backup-db')
@admin_required
def api_admin_backup_db():
    """Descarga snapshot consistente de la DB (sqlite3 backup API)."""
    import io, tempfile
    fd, tmp = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    try:
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        dst.close(); src.close()
        with open(tmp, 'rb') as f:
            data = f.read()
    finally:
        os.unlink(tmp)
    return send_file(io.BytesIO(data), as_attachment=True,
                     download_name=f'aurora-backup-{date.today()}.db',
                     mimetype='application/octet-stream')


@app.route('/api/admin/importar-db', methods=['POST'])
@admin_required
def api_admin_importar_db():
    """Reemplaza la DB con un archivo subido (migracion inicial / restore)."""
    import tempfile
    archivo = request.files.get('archivo')
    if not archivo:
        return jsonify({'error': 'Adjunta el archivo .db en el campo "archivo"'}), 400
    fd, tmp = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    archivo.save(tmp)
    try:
        chk = sqlite3.connect(tmp)
        ok = chk.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='productos'").fetchone()
        chk.close()
        if not ok:
            os.unlink(tmp)
            return jsonify({'error': 'El archivo no es una DB de Aurora (falta tabla productos)'}), 400
    except sqlite3.DatabaseError:
        os.unlink(tmp)
        return jsonify({'error': 'El archivo no es una base SQLite valida'}), 400
    for suf in ('-wal', '-shm'):
        p = DB_PATH + suf
        if os.path.exists(p):
            os.remove(p)
    os.replace(tmp, DB_PATH)
    init_db()  # aplica migraciones (sucursales, etc.) a la DB importada
    print("[admin] DB importada y migrada")
    return jsonify({'ok': True})
```

- [ ] **Step 3: Test pasa + commit**

Run: `python -m pytest tests/ -q` — Expected: PASS
```bash
git add app.py tests/test_sucursales.py
git commit -m "feat(multi-local): backup y restore de DB para Railway"
```

---

### Task 12: Deploy en Railway + migración de datos

**Files:**
- Modify: `requirements.txt`
- Manual: Railway dashboard/CLI, datos

- [ ] **Step 1: gunicorn a requirements**

`requirements.txt` agrega línea: `gunicorn>=21.0`
```bash
git add requirements.txt && git commit -m "build: gunicorn para Railway"
```

- [ ] **Step 2: Repo GitHub**

Run: `git remote -v` — si no hay remoto:
```bash
gh repo create nwalbaum-prog/aurora-ventas --private --source=. --push
```
Si hay remoto: `git push origin master`.

- [ ] **Step 3: Servicio Railway (manual/CLI — CHECKPOINT con Nico si pide login)**

1. Railway → New Project → Deploy from GitHub repo `aurora-ventas`, rama master.
2. Service → Settings → Volumes → Add volume, mount path `/data`.
3. Variables:
   - `DATA_DIR=/data`
   - `SECRET_KEY=` (generar: `python -c "import secrets;print(secrets.token_hex(32))"`)
   - `VENTAS_API_KEY=` (nueva key fuerte: mismo comando; anotar para el bot)
   - `HTTPS=1`
   - `GOOGLE_PLACES_API_KEY=` (la de aurora_config.json)
   - `ANTHROPIC_API_KEY=`, `SMTP_USER=`, `SMTP_PASS=`, `OWNER_EMAIL=`, `OWNER_PHONE=56994891724`
   - `EVOLUTION_API_URL=`, `EVOLUTION_API_KEY=`, `EVOLUTION_INSTANCE=` (las del config actual)
4. Deploy → verificar `https://<url>/health` responde `{"status":"ok"}`.

- [ ] **Step 4: Importar la DB actual**

Desde el PC local (PowerShell):
```powershell
# login y captura de cookie
curl.exe -s -c cookies.txt -X POST https://<URL>/login -d "email=admin@...&password=..."
# subir la DB local
curl.exe -s -b cookies.txt -F "archivo=@C:\Users\LENOVO\Documents\aurora-ventas\aurora.db" https://<URL>/api/admin/importar-db
```
Verificar: abrir la URL, login, ver ventas históricas y stock. Probar `/api/admin/backup-db` descarga.

- [ ] **Step 5: Re-apuntar el bot y los locales**

1. Proyecto Railway `aurora-bakers`: cambiar env `VENTAS_URL` (o equivalente) a la nueva URL + `VENTAS_API_KEY` nueva. Redeploy.
2. En cada local: acceso directo de Chrome/Edge a la URL (`--app=https://<URL>/pos/caja` para modo kiosko del POS).
3. Crear usuarios reales: Daniel (perfil Jefe de Producción, sucursal Todas), cajeros (perfil Cajero, sucursal fija). Cambiar password del admin default si sigue `aurora2024`.

- [ ] **Step 6: Documentar**

Actualizar CLAUDE.md global y memoria `project_crm_aurora.md`: URL Railway, sucursales, traspasos, perfiles, backup endpoint, y que el server local queda como respaldo/dev.
```bash
git add -A && git commit -m "docs: multi-local desplegado en Railway"
git push
```

---

## Self-review checklist (ejecutar al final)

- Spec coverage: sucursales ✓(T1) helpers/sesión ✓(T2) stock+backfill ✓(T3) ventas/suscripciones ✓(T4) POS ✓(T5) traspasos API ✓(T6) traspasos UI ✓(T7) usuarios/perfiles ✓(T8) reportes backend ✓(T9) reportes UI ✓(T10) backup/import ✓(T11) deploy+bot+docs ✓(T12).
- `python -m pytest tests/ -q` verde al cierre de cada task.
- Invariante verificable en DB tras pruebas manuales: `productos.stock = Σ inventario = Σ lotes` (global) y por sucursal.
