# Módulo Mayoristas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Añadir un módulo B2B de pedidos fijos recurrentes para clientes mayoristas, que alimenta automáticamente producción y despacho al generar ventas semanales.

**Architecture:** Dos tablas nuevas (`mayorista_pedidos` + `mayorista_pedido_lineas`) actúan como plantilla. Un endpoint `POST /api/mayoristas/generar-semana` convierte las plantillas en registros `ventas`+`venta_items` con `canal='MAYORISTA'`, que producción y despacho ya leen sin cambios. UI de una página con lista de mayoristas a la izquierda y panel de edición a la derecha.

**Tech Stack:** Flask, SQLite (WAL), vanilla JS, Bootstrap Icons, templates Jinja2 (extienden `base.html`).

---

### Task 1: Tablas DB + tests

**Files:**
- Modify: `app.py` línea 451 (dentro del bloque `executescript`)
- Test: `tests/test_mayoristas.py`

- [ ] **Step 1: Crear archivo de test**

Crea `tests/test_mayoristas.py`:

```python
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
```

- [ ] **Step 2: Correr test — debe fallar (tablas no existen)**

```
cd C:\Users\LENOVO\Documents\aurora-ventas
venv\Scripts\pytest tests/test_mayoristas.py::test_tablas_existen -v
```

Esperado: FAILED — `assert 'mayorista_pedidos' in tablas`

- [ ] **Step 3: Añadir tablas al `executescript` en `init_db()` de `app.py`**

En `app.py`, localiza la línea que dice `""")` que cierra el bloque `executescript` (alrededor de la línea 452, justo después de `compras_insumos`). Inserta las dos tablas nuevas **antes** de ese cierre:

```python
            CREATE TABLE IF NOT EXISTS mayorista_pedidos (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id   INTEGER NOT NULL REFERENCES clientes(id),
                dia_despacho TEXT    NOT NULL CHECK(dia_despacho IN ('martes','jueves')),
                activo       INTEGER NOT NULL DEFAULT 1,
                notas        TEXT    NOT NULL DEFAULT '',
                created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS mayorista_pedido_lineas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_id   INTEGER NOT NULL REFERENCES mayorista_pedidos(id) ON DELETE CASCADE,
                producto_id INTEGER NOT NULL REFERENCES productos(id),
                cantidad    REAL    NOT NULL DEFAULT 1
            );
```

El bloque final del executescript queda:

```python
            CREATE TABLE IF NOT EXISTS compras_insumos (
                ...
            );
            CREATE TABLE IF NOT EXISTS mayorista_pedidos (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id   INTEGER NOT NULL REFERENCES clientes(id),
                dia_despacho TEXT    NOT NULL CHECK(dia_despacho IN ('martes','jueves')),
                activo       INTEGER NOT NULL DEFAULT 1,
                notas        TEXT    NOT NULL DEFAULT '',
                created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS mayorista_pedido_lineas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_id   INTEGER NOT NULL REFERENCES mayorista_pedidos(id) ON DELETE CASCADE,
                producto_id INTEGER NOT NULL REFERENCES productos(id),
                cantidad    REAL    NOT NULL DEFAULT 1
            );
        """)
```

- [ ] **Step 4: Verificar que app.py compila**

```
python -m py_compile app.py && echo OK
```

Esperado: `OK`

- [ ] **Step 5: Correr todos los tests del archivo**

```
venv\Scripts\pytest tests/test_mayoristas.py -v
```

Esperado: 3 PASSED

- [ ] **Step 6: Commit**

```
git add app.py tests/test_mayoristas.py
git commit -m "feat: add mayorista_pedidos tables + tests"
```

---

### Task 2: Rutas API

**Files:**
- Modify: `app.py` (añadir 5 rutas después del bloque de suscripciones, ~línea 1924)
- Test: `tests/test_mayoristas.py` (ampliar)

- [ ] **Step 1: Añadir tests de API al archivo de test**

Añade al final de `tests/test_mayoristas.py`:

```python
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
```

- [ ] **Step 2: Correr tests nuevos — deben fallar**

```
venv\Scripts\pytest tests/test_mayoristas.py -v -k "api or guardar or generar"
```

Esperado: FAILED (rutas no existen → 404/405)

- [ ] **Step 3: Añadir rutas API a `app.py`**

Localiza `@app.route('/api/reportes/ventas')` (línea ~1924). Inserta el bloque completo de rutas **justo antes** de esa línea:

```python
# ── API: Mayoristas ───────────────────────────────────────────────────────────

def _fecha_despacho_semana(dia: str) -> str:
    """Devuelve la fecha (str YYYY-MM-DD) del martes o jueves de la semana ISO actual."""
    hoy = date.today()
    lunes = hoy - timedelta(days=hoy.weekday())
    offset = 1 if dia == 'martes' else 3
    return str(lunes + timedelta(days=offset))

@app.route('/api/mayoristas', methods=['GET'])
@login_required
def api_mayoristas_list():
    with db() as c:
        clientes = c.execute(
            "SELECT id, nombre FROM clientes WHERE tipo='MAYORISTA' AND id IN "
            "(SELECT DISTINCT cliente_id FROM mayorista_pedidos) ORDER BY nombre"
        ).fetchall()
        result = []
        for cl in clientes:
            pedidos = c.execute(
                "SELECT id, dia_despacho, activo FROM mayorista_pedidos WHERE cliente_id=?",
                (cl['id'],)
            ).fetchall()
            dias = {}
            for p in pedidos:
                dias[p['dia_despacho']] = {'activo': bool(p['activo'])}
            result.append({'id': cl['id'], 'nombre': cl['nombre'], 'dias': dias})
    return jsonify(result)

@app.route('/api/mayoristas/clientes', methods=['GET'])
@login_required
def api_mayoristas_clientes():
    """Lista todos los clientes tipo MAYORISTA para el dropdown."""
    with db() as c:
        rows = c.execute(
            "SELECT id, nombre FROM clientes WHERE tipo='MAYORISTA' ORDER BY nombre"
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/mayoristas', methods=['POST'])
@login_required
def api_mayoristas_create():
    """Marca un cliente existente como MAYORISTA o crea uno nuevo."""
    d = request.json or {}
    with db() as c:
        if d.get('cliente_id'):
            c.execute("UPDATE clientes SET tipo='MAYORISTA' WHERE id=?", (d['cliente_id'],))
            cid = d['cliente_id']
        else:
            nombre = (d.get('nombre') or '').strip()
            if not nombre:
                return jsonify({'error': 'Nombre requerido'}), 400
            cur = c.execute(
                "INSERT INTO clientes (nombre,email,telefono,direccion,notas,tipo) VALUES (?,?,?,?,?,?)",
                (nombre, d.get('email',''), d.get('telefono',''),
                 d.get('direccion',''), d.get('notas',''), 'MAYORISTA')
            )
            cid = cur.lastrowid
    return jsonify({'id': cid}), 201

@app.route('/api/mayoristas/<int:cid>/pedidos', methods=['GET'])
@login_required
def api_mayoristas_pedidos_get(cid):
    with db() as c:
        cl = c.execute("SELECT id, nombre FROM clientes WHERE id=?", (cid,)).fetchone()
        if not cl:
            return jsonify({'error': 'Cliente no encontrado'}), 404
        pedidos_rows = c.execute(
            "SELECT * FROM mayorista_pedidos WHERE cliente_id=?", (cid,)
        ).fetchall()
        pedidos = []
        for p in pedidos_rows:
            lineas = c.execute(
                """SELECT ml.id, ml.producto_id, ml.cantidad,
                          pr.nombre as producto_nombre, pr.precio_mayorista
                   FROM mayorista_pedido_lineas ml
                   JOIN productos pr ON pr.id = ml.producto_id
                   WHERE ml.pedido_id=?""",
                (p['id'],)
            ).fetchall()
            pedidos.append({
                'id': p['id'],
                'dia_despacho': p['dia_despacho'],
                'activo': bool(p['activo']),
                'notas': p['notas'],
                'lineas': [dict(l) for l in lineas],
            })
    return jsonify({'cliente': dict(cl), 'pedidos': pedidos})

@app.route('/api/mayoristas/<int:cid>/pedidos', methods=['PUT'])
@login_required
def api_mayoristas_pedidos_put(cid):
    """Reemplaza la plantilla completa de pedidos de un mayorista."""
    d = request.json or {}
    pedidos_in = d.get('pedidos', [])
    with db() as c:
        if not c.execute("SELECT id FROM clientes WHERE id=?", (cid,)).fetchone():
            return jsonify({'error': 'Cliente no encontrado'}), 404
        for p in pedidos_in:
            dia = p.get('dia_despacho')
            if dia not in ('martes', 'jueves'):
                continue
            existing = c.execute(
                "SELECT id FROM mayorista_pedidos WHERE cliente_id=? AND dia_despacho=?",
                (cid, dia)
            ).fetchone()
            if existing:
                ped_id = existing['id']
                c.execute(
                    "UPDATE mayorista_pedidos SET activo=?, notas=? WHERE id=?",
                    (1 if p.get('activo', True) else 0, p.get('notas',''), ped_id)
                )
                c.execute("DELETE FROM mayorista_pedido_lineas WHERE pedido_id=?", (ped_id,))
            else:
                ped_id = c.execute(
                    "INSERT INTO mayorista_pedidos (cliente_id, dia_despacho, activo, notas) VALUES (?,?,?,?)",
                    (cid, dia, 1 if p.get('activo', True) else 0, p.get('notas',''))
                ).lastrowid
            for linea in p.get('lineas', []):
                if not linea.get('producto_id') or float(linea.get('cantidad', 0)) <= 0:
                    continue
                c.execute(
                    "INSERT INTO mayorista_pedido_lineas (pedido_id, producto_id, cantidad) VALUES (?,?,?)",
                    (ped_id, int(linea['producto_id']), float(linea['cantidad']))
                )
    return jsonify({'ok': True})

@app.route('/api/mayoristas/generar-semana', methods=['POST'])
@login_required
def api_mayoristas_generar_semana():
    """Genera ventas para todos los pedidos mayoristas activos de la semana actual."""
    generadas = 0
    omitidas = 0
    with db() as c:
        pedidos = c.execute(
            "SELECT mp.*, cl.nombre as cliente_nombre "
            "FROM mayorista_pedidos mp "
            "JOIN clientes cl ON cl.id = mp.cliente_id "
            "WHERE mp.activo=1"
        ).fetchall()

        for ped in pedidos:
            fecha_desp = _fecha_despacho_semana(ped['dia_despacho'])
            ya_existe = c.execute(
                "SELECT id FROM ventas WHERE cliente_id=? AND canal='MAYORISTA' AND DATE(fecha)=?",
                (ped['cliente_id'], fecha_desp)
            ).fetchone()
            if ya_existe:
                omitidas += 1
                continue

            lineas = c.execute(
                """SELECT ml.cantidad, ml.producto_id, pr.precio_mayorista, pr.nombre
                   FROM mayorista_pedido_lineas ml
                   JOIN productos pr ON pr.id = ml.producto_id
                   WHERE ml.pedido_id=?""",
                (ped['id'],)
            ).fetchall()
            if not lineas:
                continue

            total = sum(float(l['cantidad']) * float(l['precio_mayorista']) for l in lineas)
            vid = c.execute(
                """INSERT INTO ventas
                   (fecha, cliente_id, canal, total, notas,
                    fecha_despacho, con_despacho, tipo_cliente, estado_pago, estado_despacho)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (fecha_desp, ped['cliente_id'], 'MAYORISTA', total,
                 f"Pedido fijo {ped['dia_despacho']}",
                 fecha_desp, 1, 'MAYORISTA', 'PENDIENTE', 'PENDIENTE')
            ).lastrowid
            for l in lineas:
                c.execute(
                    "INSERT INTO venta_items (venta_id,producto_id,cantidad,precio_unitario) VALUES (?,?,?,?)",
                    (vid, l['producto_id'], float(l['cantidad']), float(l['precio_mayorista']))
                )
            generadas += 1

    return jsonify({'generadas': generadas, 'omitidas': omitidas})
```

- [ ] **Step 4: Verificar compilación**

```
python -m py_compile app.py && echo OK
```

Esperado: `OK`

- [ ] **Step 5: Correr todos los tests**

```
venv\Scripts\pytest tests/test_mayoristas.py -v
```

Esperado: 8 PASSED (3 de Task 1 + 5 de Task 2)

- [ ] **Step 6: Commit**

```
git add app.py tests/test_mayoristas.py
git commit -m "feat: add mayoristas API routes (list, create, pedidos, generar-semana)"
```

---

### Task 3: Ruta de página + navegación

**Files:**
- Modify: `app.py` (añadir ruta `/mayoristas`)
- Modify: `templates/base.html` (añadir ítem nav)

- [ ] **Step 1: Añadir ruta de página en `app.py`**

Localiza el bloque de rutas de página (alrededor de línea 1273). Añade justo después de la ruta de suscripciones:

```python
@app.route('/mayoristas')
@module_required('mayoristas')
def page_mayoristas(): return render_template('mayoristas.html', active='mayoristas')
```

- [ ] **Step 2: Añadir ítem en `templates/base.html`**

Localiza este bloque en `base.html`:
```html
    {% if user_es_admin or 'suscripciones' in user_permisos %}
    <a href="/suscripciones"  class="nav-item {% if active=='suscripciones'  %}active{% endif %}"><i class="bi bi-calendar-check"></i> Suscripciones</a>
    {% endif %}
```

Añade justo **debajo**:
```html
    {% if user_es_admin or 'mayoristas' in user_permisos %}
    <a href="/mayoristas" class="nav-item {% if active=='mayoristas' %}active{% endif %}"><i class="bi bi-shop"></i> Mayoristas</a>
    {% endif %}
```

- [ ] **Step 3: Verificar compilación**

```
python -m py_compile app.py && echo OK
```

Esperado: `OK`

- [ ] **Step 4: Correr todos los tests**

```
venv\Scripts\pytest tests/test_mayoristas.py -v
```

Esperado: 8 PASSED

- [ ] **Step 5: Commit**

```
git add app.py templates/base.html
git commit -m "feat: add mayoristas page route and nav item"
```

---

### Task 4: Template `mayoristas.html`

**Files:**
- Create: `templates/mayoristas.html`

- [ ] **Step 1: Crear el template**

Crea `templates/mayoristas.html`:

```html
{% extends "base.html" %}
{% block title %}Mayoristas{% endblock %}
{% block page_title %}Mayoristas B2B{% endblock %}

{% block content %}

<div class="toolbar">
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <select id="sel-mayorista" class="form-control" style="max-width:280px" onchange="cargarMayorista(this.value)">
      <option value="">Seleccionar mayorista...</option>
    </select>
    <button class="btn btn-secondary btn-sm" onclick="openModalNuevo()">
      <i class="bi bi-plus-lg"></i> Agregar mayorista
    </button>
  </div>
  <div class="toolbar-right">
    <button class="btn btn-primary" id="btn-generar" onclick="generarSemana()">
      <i class="bi bi-calendar-week"></i> Generar semana <span id="lbl-semana"></span>
    </button>
  </div>
</div>

<!-- Resultado generación -->
<div id="banner-gen" style="display:none" class="alert alert-success mb-2"></div>

<!-- Panel mayorista -->
<div id="panel-mayorista" style="display:none">
  <h3 id="nombre-mayorista" style="margin:12px 0 16px"></h3>

  <!-- Día: Martes -->
  <div class="card" style="margin-bottom:16px">
    <div style="padding:12px 16px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border)">
      <strong><i class="bi bi-calendar2-day"></i> Martes</strong>
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer;margin:0">
        <input type="checkbox" id="chk-martes" onchange="toggleDia('martes')"> Activo
      </label>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Producto</th>
          <th style="width:100px">Cantidad</th>
          <th style="width:120px" class="right">Precio Unit.</th>
          <th style="width:120px" class="right">Subtotal</th>
          <th style="width:40px"></th>
        </tr></thead>
        <tbody id="lineas-martes"></tbody>
        <tfoot>
          <tr>
            <td colspan="3" class="right"><strong>Total Martes</strong></td>
            <td class="right"><strong id="total-martes">$0</strong></td>
            <td></td>
          </tr>
        </tfoot>
      </table>
    </div>
    <div style="padding:10px 16px;display:flex;justify-content:space-between;align-items:center">
      <button class="btn btn-secondary btn-sm" onclick="addLinea('martes')">
        <i class="bi bi-plus"></i> Agregar producto
      </button>
      <button class="btn btn-primary btn-sm" onclick="guardarDia('martes')">
        <i class="bi bi-check-lg"></i> Guardar martes
      </button>
    </div>
  </div>

  <!-- Día: Jueves -->
  <div class="card" style="margin-bottom:16px">
    <div style="padding:12px 16px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border)">
      <strong><i class="bi bi-calendar2-day"></i> Jueves</strong>
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer;margin:0">
        <input type="checkbox" id="chk-jueves" onchange="toggleDia('jueves')"> Activo
      </label>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Producto</th>
          <th style="width:100px">Cantidad</th>
          <th style="width:120px" class="right">Precio Unit.</th>
          <th style="width:120px" class="right">Subtotal</th>
          <th style="width:40px"></th>
        </tr></thead>
        <tbody id="lineas-jueves"></tbody>
        <tfoot>
          <tr>
            <td colspan="3" class="right"><strong>Total Jueves</strong></td>
            <td class="right"><strong id="total-jueves">$0</strong></td>
            <td></td>
          </tr>
        </tfoot>
      </table>
    </div>
    <div style="padding:10px 16px;display:flex;justify-content:space-between;align-items:center">
      <button class="btn btn-secondary btn-sm" onclick="addLinea('jueves')">
        <i class="bi bi-plus"></i> Agregar producto
      </button>
      <button class="btn btn-primary btn-sm" onclick="guardarDia('jueves')">
        <i class="bi bi-check-lg"></i> Guardar jueves
      </button>
    </div>
  </div>

  <div style="text-align:right;padding:4px 0 16px">
    <strong>Total semanal: <span id="total-semanal" style="font-size:1.1em">$0</span></strong>
  </div>
</div>

<div id="panel-vacio" style="padding:60px 0;text-align:center;color:var(--text-muted)">
  <i class="bi bi-shop" style="font-size:3rem;display:block;margin-bottom:12px"></i>
  Selecciona un mayorista o agrega uno nuevo
</div>

<!-- Modal: nuevo mayorista -->
<div class="modal-overlay" id="modal-nuevo">
  <div class="modal">
    <div class="modal-header">
      <span class="modal-title">Agregar mayorista</span>
      <button class="modal-close" onclick="closeModal('modal-nuevo')"><i class="bi bi-x-lg"></i></button>
    </div>
    <div style="padding:16px">
      <p style="margin:0 0 12px;color:var(--text-muted);font-size:.9em">
        Selecciona un cliente existente o crea uno nuevo.
      </p>
      <div class="form-group">
        <label>Cliente existente</label>
        <select id="sel-cliente-existente" class="form-control">
          <option value="">— crear nuevo —</option>
        </select>
      </div>
      <div id="form-nuevo-cliente">
        <div class="form-row fr-2">
          <div class="form-group">
            <label>Nombre *</label>
            <input type="text" id="nuevo-nombre" class="form-control" placeholder="Café Ejemplo">
          </div>
          <div class="form-group">
            <label>Teléfono</label>
            <input type="text" id="nuevo-telefono" class="form-control" placeholder="+56 9 ...">
          </div>
        </div>
        <div class="form-group">
          <label>Dirección</label>
          <input type="text" id="nuevo-direccion" class="form-control">
        </div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal('modal-nuevo')">Cancelar</button>
      <button class="btn btn-primary" onclick="crearMayorista()">Agregar</button>
    </div>
  </div>
</div>

<script>
// ── Estado ────────────────────────────────────────────────────────────────────
let productos = [];      // [{id, nombre, precio_mayorista}, ...]
let cidActual = null;    // cliente_id del mayorista seleccionado
let pedidosData = {};    // {martes: {activo, lineas}, jueves: {activo, lineas}}

const $ = id => document.getElementById(id);

// ── Inicialización ────────────────────────────────────────────────────────────
async function init() {
  // Etiqueta semana
  const hoy = new Date();
  const lunes = new Date(hoy); lunes.setDate(hoy.getDate() - hoy.getDay() + 1);
  const viernes = new Date(lunes); viernes.setDate(lunes.getDate() + 4);
  const fmt = d => d.toLocaleDateString('es-CL', {day:'2-digit', month:'short'});
  $('lbl-semana').textContent = `(${fmt(lunes)} – ${fmt(viernes)})`;

  // Cargar productos
  const rp = await fetch('/api/productos');
  productos = (await rp.json()).filter(p => p.activo && p.precio_mayorista > 0);

  // Cargar mayoristas en dropdown
  await cargarDropdown();

  // Cargar clientes todos para modal
  const rc = await fetch('/api/clientes');
  const todos = await rc.json();
  const sel = $('sel-cliente-existente');
  todos.forEach(cl => {
    if (cl.tipo !== 'MAYORISTA') {
      const o = document.createElement('option');
      o.value = cl.id; o.textContent = cl.nombre;
      sel.appendChild(o);
    }
  });

  // Mostrar/ocultar form nuevo según selección
  sel.onchange = () => {
    $('form-nuevo-cliente').style.display = sel.value ? 'none' : '';
  };
}

async function cargarDropdown() {
  const r = await fetch('/api/mayoristas/clientes');
  const lista = await r.json();
  const sel = $('sel-mayorista');
  // Conservar selección actual
  const prevVal = sel.value;
  // Limpiar opciones excepto placeholder
  while (sel.options.length > 1) sel.remove(1);
  lista.forEach(m => {
    const o = document.createElement('option');
    o.value = m.id; o.textContent = m.nombre;
    sel.appendChild(o);
  });
  if (prevVal) sel.value = prevVal;
}

// ── Cargar mayorista ──────────────────────────────────────────────────────────
async function cargarMayorista(cid) {
  if (!cid) {
    $('panel-mayorista').style.display = 'none';
    $('panel-vacio').style.display = '';
    cidActual = null;
    return;
  }
  cidActual = parseInt(cid);
  const r = await fetch(`/api/mayoristas/${cid}/pedidos`);
  const data = await r.json();
  $('nombre-mayorista').textContent = data.cliente.nombre;

  pedidosData = {martes: {activo: false, lineas: []}, jueves: {activo: false, lineas: []}};
  data.pedidos.forEach(p => {
    pedidosData[p.dia_despacho] = {activo: p.activo, lineas: p.lineas};
  });

  renderDia('martes');
  renderDia('jueves');
  $('panel-vacio').style.display = 'none';
  $('panel-mayorista').style.display = '';
}

// ── Render tabla de un día ────────────────────────────────────────────────────
function renderDia(dia) {
  const pd = pedidosData[dia];
  $(`chk-${dia}`).checked = pd.activo;
  const tbody = $(`lineas-${dia}`);
  tbody.innerHTML = '';
  pd.lineas.forEach((l, idx) => agregarFilaDOM(dia, idx, l));
  recalcTotales();
}

function agregarFilaDOM(dia, idx, linea) {
  const tbody = $(`lineas-${dia}`);
  const tr = document.createElement('tr');
  tr.dataset.idx = idx;

  const precioUnit = linea.precio_mayorista || 0;
  const cantidad = parseFloat(linea.cantidad) || 0;

  tr.innerHTML = `
    <td>
      <select class="form-control form-control-sm sel-prod" onchange="onProdChange(this,'${dia}',${idx})">
        <option value="">— seleccionar —</option>
        ${productos.map(p =>
          `<option value="${p.id}" data-precio="${p.precio_mayorista}"
            ${p.id == linea.producto_id ? 'selected' : ''}>${p.nombre}</option>`
        ).join('')}
      </select>
    </td>
    <td>
      <input type="number" class="form-control form-control-sm inp-cant" min="0.1" step="0.5"
        value="${cantidad}" oninput="onCantChange(this,'${dia}',${idx})">
    </td>
    <td class="right precio-unit">${fmtPeso(precioUnit)}</td>
    <td class="right subtotal-linea">${fmtPeso(precioUnit * cantidad)}</td>
    <td>
      <button class="btn btn-sm" style="color:var(--danger);padding:2px 6px"
        onclick="removeLinea('${dia}',${idx})">
        <i class="bi bi-trash"></i>
      </button>
    </td>`;
  tbody.appendChild(tr);
}

function onProdChange(sel, dia, idx) {
  const opt = sel.options[sel.selectedIndex];
  const precio = parseFloat(opt.dataset.precio) || 0;
  pedidosData[dia].lineas[idx].producto_id = parseInt(sel.value) || null;
  pedidosData[dia].lineas[idx].precio_mayorista = precio;
  const tr = sel.closest('tr');
  const cant = parseFloat(tr.querySelector('.inp-cant').value) || 0;
  tr.querySelector('.precio-unit').textContent = fmtPeso(precio);
  tr.querySelector('.subtotal-linea').textContent = fmtPeso(precio * cant);
  recalcTotales();
}

function onCantChange(inp, dia, idx) {
  const cant = parseFloat(inp.value) || 0;
  pedidosData[dia].lineas[idx].cantidad = cant;
  const tr = inp.closest('tr');
  const precio = pedidosData[dia].lineas[idx].precio_mayorista || 0;
  tr.querySelector('.subtotal-linea').textContent = fmtPeso(precio * cant);
  recalcTotales();
}

function addLinea(dia) {
  const idx = pedidosData[dia].lineas.length;
  pedidosData[dia].lineas.push({producto_id: null, cantidad: 1, precio_mayorista: 0});
  agregarFilaDOM(dia, idx, pedidosData[dia].lineas[idx]);
}

function removeLinea(dia, idx) {
  pedidosData[dia].lineas.splice(idx, 1);
  renderDia(dia);
}

function toggleDia(dia) {
  pedidosData[dia].activo = $(`chk-${dia}`).checked;
}

function recalcTotales() {
  let total = 0;
  ['martes','jueves'].forEach(dia => {
    const sub = pedidosData[dia].lineas.reduce((s, l) =>
      s + (parseFloat(l.precio_mayorista)||0) * (parseFloat(l.cantidad)||0), 0);
    $(`total-${dia}`).textContent = fmtPeso(sub);
    total += sub;
  });
  $('total-semanal').textContent = fmtPeso(total);
}

// ── Guardar pedido de un día ──────────────────────────────────────────────────
async function guardarDia(dia) {
  if (!cidActual) return;
  const lineasValidas = pedidosData[dia].lineas.filter(
    l => l.producto_id && parseFloat(l.cantidad) > 0
  );
  const payload = {
    pedidos: [{
      dia_despacho: dia,
      activo: pedidosData[dia].activo,
      notas: '',
      lineas: lineasValidas.map(l => ({
        producto_id: l.producto_id,
        cantidad: parseFloat(l.cantidad)
      }))
    }]
  };
  const r = await fetch(`/api/mayoristas/${cidActual}/pedidos`, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  if (r.ok) {
    mostrarBanner(`Pedido ${dia} guardado correctamente`, 'success');
    cargarMayorista(cidActual);
  } else {
    mostrarBanner('Error al guardar', 'danger');
  }
}

// ── Generar semana ────────────────────────────────────────────────────────────
async function generarSemana() {
  if (!confirm('¿Generar ventas para todos los mayoristas activos esta semana?')) return;
  const r = await fetch('/api/mayoristas/generar-semana', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: '{}'
  });
  const data = await r.json();
  mostrarBanner(
    `Semana generada: ${data.generadas} ventas nuevas, ${data.omitidas} ya existían.`,
    'success'
  );
}

// ── Modal nuevo mayorista ─────────────────────────────────────────────────────
function openModalNuevo() {
  $('nuevo-nombre').value = '';
  $('nuevo-telefono').value = '';
  $('nuevo-direccion').value = '';
  $('sel-cliente-existente').value = '';
  $('form-nuevo-cliente').style.display = '';
  document.getElementById('modal-nuevo').style.display = 'flex';
}

async function crearMayorista() {
  const selExist = $('sel-cliente-existente');
  let body = {};
  if (selExist.value) {
    body = {cliente_id: parseInt(selExist.value)};
  } else {
    const nombre = $('nuevo-nombre').value.trim();
    if (!nombre) { alert('Ingresa un nombre'); return; }
    body = {
      nombre,
      telefono: $('nuevo-telefono').value.trim(),
      direccion: $('nuevo-direccion').value.trim()
    };
  }
  const r = await fetch('/api/mayoristas', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  if (r.ok) {
    const data = await r.json();
    closeModal('modal-nuevo');
    await cargarDropdown();
    $('sel-mayorista').value = data.id;
    cargarMayorista(data.id);
  } else {
    alert('Error al crear mayorista');
  }
}

// ── Utilidades ────────────────────────────────────────────────────────────────
function fmtPeso(n) {
  return '$' + Math.round(n).toLocaleString('es-CL');
}

function mostrarBanner(msg, tipo) {
  const b = $('banner-gen');
  b.className = `alert alert-${tipo} mb-2`;
  b.textContent = msg;
  b.style.display = '';
  setTimeout(() => { b.style.display = 'none'; }, 5000);
}

function closeModal(id) {
  document.getElementById(id).style.display = 'none';
}

// Cerrar modal al clicar overlay
document.getElementById('modal-nuevo').addEventListener('click', function(e) {
  if (e.target === this) closeModal('modal-nuevo');
});

init();
</script>
{% endblock %}
```

- [ ] **Step 2: Verificar compilación completa**

```
python -m py_compile app.py && echo OK
```

Esperado: `OK`

- [ ] **Step 3: Correr todos los tests del proyecto para detectar regresiones**

```
venv\Scripts\pytest tests/ -v --tb=short
```

Esperado: todos los tests anteriores PASSED + los 8 nuevos PASSED

- [ ] **Step 4: Commit final**

```
git add templates/mayoristas.html
git commit -m "feat: add mayoristas.html template — complete B2B wholesale module"
```

---

## Verificación manual (post-implementación)

1. Levantar app: `start.bat`
2. Ir a `http://127.0.0.1:5000/mayoristas`
3. Click "Agregar mayorista" → crear "Café Prueba"
4. Seleccionar en dropdown → debe aparecer panel con Martes y Jueves
5. En Martes: agregar línea → seleccionar producto → ingresar cantidad → guardar
6. Click "Generar semana" → confirmar → ver banner con ventas generadas
7. Ir a `/despacho` → verificar que aparece la venta con `canal=MAYORISTA`
8. Ir a `/produccion` → verificar que el producto mayorista está en el plan
