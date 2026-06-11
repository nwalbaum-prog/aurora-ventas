# POS + Promociones + Pantalla Cliente — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar interfaz de punto de venta, sistema de promociones y pantalla cliente a Aurora Bakers.

**Architecture:** Flask Blueprints en archivos separados (`blueprints/pos.py`, `blueprints/promociones.py`) con un helper `blueprints/db.py` compartido. Estado del carro en dict de módulo en `pos.py`. Las ventas POS se registran también en la tabla `ventas` existente para que los reportes actuales no se rompan.

**Tech Stack:** Flask Blueprints, SQLite, Bootstrap Icons, vanilla JS con polling para pantalla cliente, pytest para tests.

---

## Mapa de archivos

**Crear:**
- `blueprints/__init__.py` — vacío
- `blueprints/db.py` — context manager `db()` compartido
- `blueprints/pos.py` — rutas POS + pantalla cliente + `init_pos_tables()`
- `blueprints/promociones.py` — rutas promociones + `init_promociones_tables()`
- `templates/pos.html` — interfaz cajero
- `templates/pos_cliente.html` — pantalla cliente (solo lectura)
- `templates/promociones.html` — gestión CRUD de promociones
- `tests/__init__.py` — vacío
- `tests/conftest.py` — fixture Flask test client
- `tests/test_promociones.py` — tests API promociones
- `tests/test_pos.py` — tests API POS

**Modificar:**
- `requirements.txt` — agregar `pytest>=8.0`
- `.gitignore` — agregar `certificados/`, `static/boletas/`
- `app.py` — registrar blueprints y llamar init_tables (últimas ~10 líneas)
- `templates/base.html` — agregar POS y Promociones al sidebar

---

## Task 1: Instalar pytest y crear estructura de carpetas

**Files:**
- Modify: `requirements.txt`
- Create: `blueprints/__init__.py`
- Create: `tests/__init__.py`
- Modify: `.gitignore`

- [ ] **Step 1: Actualizar requirements.txt**

```
flask>=3.0
werkzeug>=3.0
gunicorn>=21.0
pytest>=8.0
```

- [ ] **Step 2: Instalar pytest**

```bash
cd C:\Users\LENOVO\Documents\aurora-ventas
venv\Scripts\pip install pytest
```
Expected: `Successfully installed pytest-...`

- [ ] **Step 3: Crear carpetas y archivos vacíos**

```bash
mkdir blueprints
mkdir tests
echo. > blueprints\__init__.py
echo. > tests\__init__.py
```

- [ ] **Step 4: Crear/actualizar .gitignore**

Crear `C:\Users\LENOVO\Documents\aurora-ventas\.gitignore` con:
```
certificados/
static/boletas/
*.pyc
__pycache__/
venv/
.env
.secret_key
aurora.db
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt blueprints/__init__.py tests/__init__.py .gitignore
git commit -m "chore: setup blueprints/ and tests/ structure, add pytest"
```

---

## Task 2: blueprints/db.py — helper compartido

**Files:**
- Create: `blueprints/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_db.py`:
```python
import os, tempfile
os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())

from blueprints.db import db

def test_db_context_manager_commits():
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS _test_helper (x INTEGER)")
        c.execute("INSERT INTO _test_helper VALUES (42)")
    with db() as c:
        row = c.execute("SELECT x FROM _test_helper").fetchone()
        assert row['x'] == 42

def test_db_context_manager_rollbacks_on_error():
    try:
        with db() as c:
            c.execute("CREATE TABLE IF NOT EXISTS _test_rollback (x INTEGER)")
            c.execute("INSERT INTO _test_rollback VALUES (1)")
            raise ValueError("forzar rollback")
    except ValueError:
        pass
    with db() as c:
        count = c.execute("SELECT COUNT(*) FROM _test_rollback").fetchone()[0]
        assert count == 0
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
cd C:\Users\LENOVO\Documents\aurora-ventas
venv\Scripts\pytest tests/test_db.py -v
```
Expected: `ModuleNotFoundError: No module named 'blueprints.db'`

- [ ] **Step 3: Crear blueprints/db.py**

```python
import os, sqlite3
from contextlib import contextmanager

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(os.environ.get('DATA_DIR', _BASE_DIR), 'aurora.db')


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

- [ ] **Step 4: Ejecutar y verificar que pasa**

```bash
venv\Scripts\pytest tests/test_db.py -v
```
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add blueprints/db.py tests/test_db.py
git commit -m "feat: add shared db() context manager in blueprints/db.py"
```

---

## Task 3: Tablas DB — promociones y POS

**Files:**
- Create: `blueprints/promociones.py` (solo `init_promociones_tables()` por ahora)
- Create: `blueprints/pos.py` (solo `init_pos_tables()` por ahora)
- Create: `tests/conftest.py`
- Modify: `app.py` (registrar blueprints + llamar init_tables)

- [ ] **Step 1: Escribir test que falla**

Crear `tests/conftest.py`:
```python
import os, tempfile, pytest

_TEST_DIR = tempfile.mkdtemp()
os.environ['DATA_DIR'] = _TEST_DIR

import app as aurora_app
from blueprints.pos import pos_bp, init_pos_tables
from blueprints.promociones import promociones_bp, init_promociones_tables

with aurora_app.app.app_context():
    aurora_app.init_db()
    init_pos_tables()
    init_promociones_tables()

@pytest.fixture
def client():
    aurora_app.app.config['TESTING'] = True
    with aurora_app.app.test_client() as c:
        yield c
```

Crear `tests/test_tablas.py`:
```python
from blueprints.db import db

def test_tablas_pos_existen():
    with db() as c:
        tablas = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert 'pos_sesiones' in tablas
        assert 'pos_ventas' in tablas
        assert 'pos_venta_items' in tablas
        assert 'promociones' in tablas

def test_columna_codigo_barra_en_productos():
    with db() as c:
        cols = [r['name'] for r in c.execute("PRAGMA table_info(productos)").fetchall()]
        assert 'codigo_barra' in cols
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
venv\Scripts\pytest tests/test_tablas.py -v
```
Expected: `ImportError` o `AssertionError`

- [ ] **Step 3: Crear blueprints/promociones.py (esqueleto con init)**

```python
from flask import Blueprint
from .db import db

promociones_bp = Blueprint('promociones', __name__, url_prefix='/promociones')


def init_promociones_tables():
    with db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS promociones (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre          TEXT    NOT NULL,
                tipo            TEXT    NOT NULL DEFAULT 'descuento_pct',
                producto_id     INTEGER REFERENCES productos(id),
                valor           REAL    NOT NULL DEFAULT 0,
                cantidad_minima INTEGER NOT NULL DEFAULT 1,
                cantidad_paga   INTEGER NOT NULL DEFAULT 1,
                productos_combo TEXT    NOT NULL DEFAULT '[]',
                fecha_inicio    TEXT,
                fecha_fin       TEXT,
                activa          INTEGER NOT NULL DEFAULT 1
            );
        """)
```

- [ ] **Step 4: Crear blueprints/pos.py (esqueleto con init)**

```python
from flask import Blueprint
from .db import db

pos_bp = Blueprint('pos', __name__, url_prefix='/pos')

_carro = {
    'items': [],
    'subtotal': 0,
    'descuento': 0,
    'total': 0,
    'completado': False,
}


def init_pos_tables():
    with db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS pos_sesiones (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                cajero          TEXT    NOT NULL DEFAULT 'Cajero',
                fecha_apertura  TEXT    NOT NULL DEFAULT (datetime('now')),
                fecha_cierre    TEXT,
                fondo_inicial   INTEGER NOT NULL DEFAULT 0,
                total_ventas    INTEGER NOT NULL DEFAULT 0,
                total_efectivo  INTEGER,
                diferencia      INTEGER,
                estado          TEXT    NOT NULL DEFAULT 'abierta'
            );
            CREATE TABLE IF NOT EXISTS pos_ventas (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sesion_id       INTEGER NOT NULL REFERENCES pos_sesiones(id),
                cliente_id      INTEGER REFERENCES clientes(id),
                subtotal        INTEGER NOT NULL DEFAULT 0,
                descuento_total INTEGER NOT NULL DEFAULT 0,
                total           INTEGER NOT NULL DEFAULT 0,
                medio_pago      TEXT    NOT NULL DEFAULT 'efectivo',
                monto_recibido  INTEGER NOT NULL DEFAULT 0,
                vuelto          INTEGER NOT NULL DEFAULT 0,
                boleta_id       INTEGER,
                fecha           TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS pos_venta_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                venta_id        INTEGER NOT NULL REFERENCES pos_ventas(id) ON DELETE CASCADE,
                producto_id     INTEGER NOT NULL REFERENCES productos(id),
                cantidad        REAL    NOT NULL,
                precio_unitario INTEGER NOT NULL,
                tipo_precio     TEXT    NOT NULL DEFAULT 'normal',
                promocion_id    INTEGER REFERENCES promociones(id),
                subtotal        INTEGER NOT NULL DEFAULT 0
            );
        """)
        # Migración: agregar codigo_barra a productos si no existe
        cols = [r['name'] for r in c.execute("PRAGMA table_info(productos)").fetchall()]
        if 'codigo_barra' not in cols:
            c.execute("ALTER TABLE productos ADD COLUMN codigo_barra TEXT NOT NULL DEFAULT ''")
```

- [ ] **Step 5: Modificar app.py — registrar blueprints**

Buscar el bloque `# ── Arranque ──` al final de `app.py` (línea ~3470) y agregar ANTES de él:

```python
# ── Blueprints ────────────────────────────────────────────────────────────────
from blueprints.pos import pos_bp, init_pos_tables
from blueprints.promociones import promociones_bp, init_promociones_tables
app.register_blueprint(pos_bp)
app.register_blueprint(promociones_bp)
```

Y modificar el bloque de arranque para llamar init_tables:

```python
# ── Arranque ──────────────────────────────────────────────────────────────────

with app.app_context():
    init_db()
    init_pos_tables()
    init_promociones_tables()

if __name__ == '__main__':
    init_db()
    init_pos_tables()
    init_promociones_tables()
    print()
    print("  Aurora Bakers -- Sistema de Ventas")
    print("  Abre:  http://127.0.0.1:5000")
    print("  Cierra con:  Ctrl+C")
    print()
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('HOST', '0.0.0.0')
    app.run(debug=False, host=host, port=port)
```

- [ ] **Step 6: Ejecutar test y verificar que pasa**

```bash
venv\Scripts\pytest tests/test_tablas.py -v
```
Expected: `2 passed`

- [ ] **Step 7: Verificar que la app arranca**

```bash
venv\Scripts\python app.py
```
Expected: sin errores, "Aurora Bakers -- Sistema de Ventas" en consola. Ctrl+C para salir.

- [ ] **Step 8: Commit**

```bash
git add blueprints/pos.py blueprints/promociones.py tests/conftest.py tests/test_tablas.py app.py
git commit -m "feat: add POS and promociones DB tables and blueprint scaffolding"
```

---

## Task 4: Promociones — API CRUD

**Files:**
- Modify: `blueprints/promociones.py` (agregar todas las rutas)
- Create: `tests/test_promociones.py`

- [ ] **Step 1: Escribir tests que fallan**

Crear `tests/test_promociones.py`:
```python
import json

def test_listar_promociones_vacio(client):
    r = client.get('/api/promociones')
    assert r.status_code == 200
    assert json.loads(r.data) == []

def test_crear_promocion_descuento(client):
    data = {
        'nombre': 'Lunes 10% off',
        'tipo': 'descuento_pct',
        'valor': 10,
        'activa': True
    }
    r = client.post('/api/promociones', json=data)
    assert r.status_code == 201
    body = json.loads(r.data)
    assert body['id'] > 0

def test_crear_promocion_mayorista(client):
    data = {
        'nombre': 'Precio mayorista',
        'tipo': 'mayorista',
        'valor': 1800,
        'cantidad_minima': 12,
        'activa': True
    }
    r = client.post('/api/promociones', json=data)
    assert r.status_code == 201

def test_actualizar_promocion(client):
    # Crear primero
    r = client.post('/api/promociones', json={
        'nombre': 'Promo test', 'tipo': 'descuento_pct', 'valor': 5, 'activa': True
    })
    pid = json.loads(r.data)['id']
    # Actualizar
    r = client.put(f'/api/promociones/{pid}', json={'activa': False})
    assert r.status_code == 200
    # Verificar
    r = client.get('/api/promociones')
    promos = json.loads(r.data)
    p = next(x for x in promos if x['id'] == pid)
    assert p['activa'] == 0

def test_eliminar_promocion(client):
    r = client.post('/api/promociones', json={
        'nombre': 'Para borrar', 'tipo': 'precio_fijo', 'valor': 999, 'activa': True
    })
    pid = json.loads(r.data)['id']
    r = client.delete(f'/api/promociones/{pid}')
    assert r.status_code == 200
    r = client.get('/api/promociones')
    ids = [x['id'] for x in json.loads(r.data)]
    assert pid not in ids

def test_evaluar_promo_descuento_pct(client):
    # Primero necesitamos un producto — usamos el más simple posible
    from blueprints.db import db
    with db() as c:
        pid = c.execute(
            "INSERT INTO productos (nombre,descripcion,precio,costo) VALUES (?,?,?,?)",
            ('Pan test', '', 1000, 0)
        ).lastrowid
    # Crear promo 20% off para ese producto
    client.post('/api/promociones', json={
        'nombre': '20% pan', 'tipo': 'descuento_pct',
        'valor': 20, 'producto_id': pid, 'activa': True
    })
    r = client.get(f'/api/promociones/evaluar?producto_id={pid}&cantidad=1')
    assert r.status_code == 200
    body = json.loads(r.data)
    assert body['precio_final'] == 800
    assert body['tipo_precio'] == 'promocion'
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
venv\Scripts\pytest tests/test_promociones.py -v
```
Expected: `404` o `AttributeError` porque las rutas no existen aún.

- [ ] **Step 3: Implementar rutas en blueprints/promociones.py**

Reemplazar el contenido de `blueprints/promociones.py` con:

```python
from flask import Blueprint, jsonify, request, render_template
from datetime import date
from .db import db

promociones_bp = Blueprint('promociones', __name__, url_prefix='/promociones')


def init_promociones_tables():
    with db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS promociones (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre          TEXT    NOT NULL,
                tipo            TEXT    NOT NULL DEFAULT 'descuento_pct',
                producto_id     INTEGER REFERENCES productos(id),
                valor           REAL    NOT NULL DEFAULT 0,
                cantidad_minima INTEGER NOT NULL DEFAULT 1,
                cantidad_paga   INTEGER NOT NULL DEFAULT 1,
                productos_combo TEXT    NOT NULL DEFAULT '[]',
                fecha_inicio    TEXT,
                fecha_fin       TEXT,
                activa          INTEGER NOT NULL DEFAULT 1
            );
        """)


def _promo_activa_hoy(p) -> bool:
    hoy = date.today().isoformat()
    if p['fecha_inicio'] and p['fecha_inicio'] > hoy:
        return False
    if p['fecha_fin'] and p['fecha_fin'] < hoy:
        return False
    return bool(p['activa'])


def evaluar_precio(c, producto_id: int, cantidad: int, tipo_solicitado: str = 'normal'):
    """Devuelve (precio_final, tipo_precio, promocion_id)."""
    prod = c.execute("SELECT * FROM productos WHERE id=?", (producto_id,)).fetchone()
    if not prod:
        return 0, 'normal', None

    precio_base = int(prod['precio'])

    if tipo_solicitado == 'mayorista':
        pm = int(prod['precio_mayorista'] or 0)
        if pm > 0:
            return pm, 'mayorista', None

    hoy = date.today().isoformat()
    promos = c.execute("""
        SELECT * FROM promociones
        WHERE activa=1
          AND (producto_id=? OR producto_id IS NULL)
          AND (fecha_inicio IS NULL OR fecha_inicio <= ?)
          AND (fecha_fin   IS NULL OR fecha_fin   >= ?)
          AND tipo IN ('descuento_pct','precio_fijo')
    """, (producto_id, hoy, hoy)).fetchall()

    mejor = precio_base
    mejor_id = None

    for p in promos:
        if p['tipo'] == 'descuento_pct':
            candidato = round(precio_base * (1 - p['valor'] / 100))
        elif p['tipo'] == 'precio_fijo':
            candidato = int(p['valor'])
        else:
            continue
        if candidato < mejor:
            mejor = candidato
            mejor_id = p['id']

    tipo = 'promocion' if mejor_id else 'normal'
    return mejor, tipo, mejor_id


# ── Página ────────────────────────────────────────────────────────────────────

@promociones_bp.route('/')
def index():
    return render_template('promociones.html', active='promociones')


# ── API ───────────────────────────────────────────────────────────────────────

@promociones_bp.route('/api/promociones', methods=['GET'])
def api_listar():
    with db() as c:
        rows = c.execute("""
            SELECT p.*, pr.nombre AS producto_nombre
            FROM promociones p
            LEFT JOIN productos pr ON p.producto_id = pr.id
            ORDER BY p.activa DESC, p.id DESC
        """).fetchall()
        return jsonify([dict(r) for r in rows])


@promociones_bp.route('/api/promociones', methods=['POST'])
def api_crear():
    d = request.get_json(force=True)
    with db() as c:
        cur = c.execute("""
            INSERT INTO promociones
              (nombre, tipo, producto_id, valor, cantidad_minima, cantidad_paga,
               productos_combo, fecha_inicio, fecha_fin, activa)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            d.get('nombre', ''),
            d.get('tipo', 'descuento_pct'),
            d.get('producto_id'),
            float(d.get('valor', 0)),
            int(d.get('cantidad_minima', 1)),
            int(d.get('cantidad_paga', 1)),
            d.get('productos_combo', '[]'),
            d.get('fecha_inicio'),
            d.get('fecha_fin'),
            1 if d.get('activa', True) else 0,
        ))
        return jsonify({'id': cur.lastrowid}), 201


@promociones_bp.route('/api/promociones/<int:pid>', methods=['PUT'])
def api_actualizar(pid):
    d = request.get_json(force=True)
    fields, vals = [], []
    for k in ('nombre', 'tipo', 'producto_id', 'valor', 'cantidad_minima',
              'cantidad_paga', 'productos_combo', 'fecha_inicio', 'fecha_fin', 'activa'):
        if k in d:
            fields.append(f"{k}=?")
            vals.append(d[k])
    if not fields:
        return jsonify({'error': 'nada que actualizar'}), 400
    vals.append(pid)
    with db() as c:
        c.execute(f"UPDATE promociones SET {', '.join(fields)} WHERE id=?", vals)
    return jsonify({'ok': True})


@promociones_bp.route('/api/promociones/<int:pid>', methods=['DELETE'])
def api_eliminar(pid):
    with db() as c:
        c.execute("DELETE FROM promociones WHERE id=?", (pid,))
    return jsonify({'ok': True})


@promociones_bp.route('/api/promociones/evaluar')
def api_evaluar():
    producto_id = request.args.get('producto_id', type=int)
    cantidad    = request.args.get('cantidad', 1, type=int)
    tipo        = request.args.get('tipo', 'normal')
    if not producto_id:
        return jsonify({'error': 'producto_id requerido'}), 400
    with db() as c:
        precio, tipo_precio, promo_id = evaluar_precio(c, producto_id, cantidad, tipo)
    return jsonify({'precio_final': precio, 'tipo_precio': tipo_precio, 'promocion_id': promo_id})
```

- [ ] **Step 4: Ejecutar y verificar que los tests pasan**

```bash
venv\Scripts\pytest tests/test_promociones.py -v
```
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add blueprints/promociones.py tests/test_promociones.py
git commit -m "feat: promociones API CRUD with price evaluation logic"
```

---

## Task 5: Promociones — template HTML

**Files:**
- Create: `templates/promociones.html`

- [ ] **Step 1: Crear templates/promociones.html**

```html
{% extends "base.html" %}
{% block title %}Promociones — Aurora Bakers{% endblock %}
{% block page_title %}Promociones{% endblock %}

{% block content %}
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1.5rem">
  <div id="badge-activas" style="font-size:.85rem;color:var(--text-3)"></div>
  <button class="btn btn-primary" onclick="abrirModal()">
    <i class="bi bi-plus-lg"></i> Nueva promoción
  </button>
</div>

<table class="table" id="tabla-promos">
  <thead>
    <tr>
      <th>Nombre</th><th>Tipo</th><th>Producto</th><th>Valor</th>
      <th>Vigencia</th><th>Estado</th><th></th>
    </tr>
  </thead>
  <tbody id="tbody-promos"></tbody>
</table>

<!-- Modal crear/editar -->
<div id="modal-promo" class="modal" style="display:none">
  <div class="modal-backdrop" onclick="cerrarModal()"></div>
  <div class="modal-box" style="max-width:520px">
    <div class="modal-header">
      <h3 id="modal-titulo">Nueva promoción</h3>
      <button onclick="cerrarModal()" class="modal-close"><i class="bi bi-x-lg"></i></button>
    </div>
    <div class="modal-body">
      <input type="hidden" id="promo-id">
      <div class="form-group">
        <label>Nombre</label>
        <input type="text" id="promo-nombre" class="form-control" placeholder="Ej: Lunes 10% off">
      </div>
      <div class="form-group">
        <label>Tipo</label>
        <select id="promo-tipo" class="form-control" onchange="actualizarFormTipo()">
          <option value="descuento_pct">Descuento porcentual</option>
          <option value="precio_fijo">Precio fijo especial</option>
          <option value="cantidad">Por cantidad (2x1, 3x2)</option>
          <option value="mayorista">Precio mayorista</option>
        </select>
      </div>
      <div class="form-group" id="campo-producto">
        <label>Producto (dejar vacío para aplicar a todos)</label>
        <select id="promo-producto" class="form-control">
          <option value="">— Todos los productos —</option>
        </select>
      </div>
      <div class="form-group" id="campo-valor">
        <label id="label-valor">Porcentaje de descuento (%)</label>
        <input type="number" id="promo-valor" class="form-control" min="0" step="0.01">
      </div>
      <div id="campos-cantidad" style="display:none">
        <div class="form-group">
          <label>Cantidad que lleva el cliente</label>
          <input type="number" id="promo-lleva" class="form-control" min="2" value="3">
        </div>
        <div class="form-group">
          <label>Cantidad que paga</label>
          <input type="number" id="promo-paga" class="form-control" min="1" value="2">
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem">
        <div class="form-group">
          <label>Fecha inicio (opcional)</label>
          <input type="date" id="promo-inicio" class="form-control">
        </div>
        <div class="form-group">
          <label>Fecha fin (opcional)</label>
          <input type="date" id="promo-fin" class="form-control">
        </div>
      </div>
      <label style="display:flex;align-items:center;gap:.5rem;cursor:pointer">
        <input type="checkbox" id="promo-activa" checked>
        Activa ahora
      </label>
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="cerrarModal()">Cancelar</button>
      <button class="btn btn-primary" onclick="guardarPromo()">Guardar</button>
    </div>
  </div>
</div>

<script>
let promos = [];
let productos = [];

async function cargar() {
  const [rp, rprod] = await Promise.all([
    api('/api/promociones'),
    api('/api/productos')
  ]);
  promos = rp;
  productos = rprod;
  renderTabla();
  renderProductosSelect();
  renderBadge();
}

function renderBadge() {
  const activas = promos.filter(p => p.activa).length;
  document.getElementById('badge-activas').textContent =
    `${activas} promoción${activas !== 1 ? 'es' : ''} activa${activas !== 1 ? 's' : ''}`;
}

function renderProductosSelect() {
  const sel = document.getElementById('promo-producto');
  sel.innerHTML = '<option value="">— Todos los productos —</option>';
  productos.forEach(p => {
    sel.innerHTML += `<option value="${p.id}">${p.nombre}</option>`;
  });
}

const TIPOS = {
  descuento_pct: 'Descuento %',
  precio_fijo:   'Precio fijo',
  cantidad:      'Cantidad',
  mayorista:     'Mayorista',
};

function renderTabla() {
  const tbody = document.getElementById('tbody-promos');
  if (!promos.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-3)">Sin promociones — crea una para comenzar</td></tr>';
    return;
  }
  tbody.innerHTML = promos.map(p => {
    const vigencia = p.fecha_inicio || p.fecha_fin
      ? `${p.fecha_inicio || '∞'} → ${p.fecha_fin || '∞'}`
      : 'Sin límite';
    const estadoBadge = p.activa
      ? '<span class="badge" style="background:var(--success-bg);color:var(--success)">Activa</span>'
      : '<span class="badge" style="background:var(--bg-2);color:var(--text-3)">Inactiva</span>';
    return `<tr>
      <td>${p.nombre}</td>
      <td>${TIPOS[p.tipo] || p.tipo}</td>
      <td>${p.producto_nombre || '— Todos —'}</td>
      <td>${formatValor(p)}</td>
      <td style="font-size:.8rem">${vigencia}</td>
      <td>${estadoBadge}</td>
      <td>
        <button class="btn-icon" onclick="editarPromo(${p.id})" title="Editar"><i class="bi bi-pencil"></i></button>
        <button class="btn-icon" onclick="eliminarPromo(${p.id})" title="Eliminar" style="color:var(--danger)"><i class="bi bi-trash"></i></button>
      </td>
    </tr>`;
  }).join('');
}

function formatValor(p) {
  if (p.tipo === 'descuento_pct') return `${p.valor}% off`;
  if (p.tipo === 'precio_fijo')   return `$${Number(p.valor).toLocaleString('es-CL')}`;
  if (p.tipo === 'cantidad')      return `${p.cantidad_minima}x${p.cantidad_paga}`;
  if (p.tipo === 'mayorista')     return `$${Number(p.valor).toLocaleString('es-CL')} (desde ${p.cantidad_minima} u.)`;
  return p.valor;
}

function actualizarFormTipo() {
  const tipo = document.getElementById('promo-tipo').value;
  const lv   = document.getElementById('label-valor');
  const fv   = document.getElementById('campo-valor');
  const fc   = document.getElementById('campos-cantidad');
  fc.style.display = tipo === 'cantidad' ? '' : 'none';
  fv.style.display = tipo === 'cantidad' ? 'none' : '';
  if (tipo === 'descuento_pct') lv.textContent = 'Porcentaje de descuento (%)';
  if (tipo === 'precio_fijo')   lv.textContent = 'Precio especial ($)';
  if (tipo === 'mayorista')     lv.textContent = 'Precio mayorista ($)';
}

function abrirModal(promo = null) {
  document.getElementById('promo-id').value    = promo?.id || '';
  document.getElementById('promo-nombre').value = promo?.nombre || '';
  document.getElementById('promo-tipo').value   = promo?.tipo || 'descuento_pct';
  document.getElementById('promo-producto').value = promo?.producto_id || '';
  document.getElementById('promo-valor').value  = promo?.valor || '';
  document.getElementById('promo-lleva').value  = promo?.cantidad_minima || 3;
  document.getElementById('promo-paga').value   = promo?.cantidad_paga || 2;
  document.getElementById('promo-inicio').value = promo?.fecha_inicio || '';
  document.getElementById('promo-fin').value    = promo?.fecha_fin || '';
  document.getElementById('promo-activa').checked = promo ? Boolean(promo.activa) : true;
  document.getElementById('modal-titulo').textContent = promo ? 'Editar promoción' : 'Nueva promoción';
  actualizarFormTipo();
  document.getElementById('modal-promo').style.display = '';
}

function cerrarModal() {
  document.getElementById('modal-promo').style.display = 'none';
}

function editarPromo(id) {
  abrirModal(promos.find(p => p.id === id));
}

async function guardarPromo() {
  const tipo = document.getElementById('promo-tipo').value;
  const data = {
    nombre:          document.getElementById('promo-nombre').value.trim(),
    tipo,
    producto_id:     document.getElementById('promo-producto').value || null,
    valor:           parseFloat(document.getElementById('promo-valor').value || 0),
    cantidad_minima: parseInt(document.getElementById('promo-lleva').value),
    cantidad_paga:   parseInt(document.getElementById('promo-paga').value),
    fecha_inicio:    document.getElementById('promo-inicio').value || null,
    fecha_fin:       document.getElementById('promo-fin').value || null,
    activa:          document.getElementById('promo-activa').checked,
  };
  if (!data.nombre) { toast('Ingresa un nombre', 'error'); return; }
  const id = document.getElementById('promo-id').value;
  const url = id ? `/api/promociones/${id}` : '/api/promociones';
  const method = id ? 'PUT' : 'POST';
  const r = await fetch(url, { method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) });
  if (!r.ok) { toast('Error al guardar', 'error'); return; }
  toast(id ? 'Promoción actualizada' : 'Promoción creada');
  cerrarModal();
  cargar();
}

async function eliminarPromo(id) {
  if (!confirm('¿Eliminar esta promoción?')) return;
  await api(`/api/promociones/${id}`, {method:'DELETE'});
  toast('Promoción eliminada');
  cargar();
}

cargar();
</script>
{% endblock %}
```

- [ ] **Step 2: Verificar en el navegador**

Arrancar la app:
```bash
venv\Scripts\python app.py
```
Abrir `http://127.0.0.1:5000/promociones/`
Expected: página con tabla vacía y botón "Nueva promoción". Crear una y verificar que aparece en la tabla.

- [ ] **Step 3: Commit**

```bash
git add templates/promociones.html
git commit -m "feat: promociones management page with CRUD UI"
```

---

## Task 6: POS — Sesiones de caja y búsqueda de productos

**Files:**
- Modify: `blueprints/pos.py` (agregar rutas de sesión y búsqueda)
- Create: `tests/test_pos.py`

- [ ] **Step 1: Escribir tests que fallan**

Crear `tests/test_pos.py`:
```python
import json

def test_no_hay_sesion_activa_al_inicio(client):
    r = client.get('/api/pos/sesion/activa')
    assert r.status_code == 200
    assert json.loads(r.data)['sesion'] is None

def test_abrir_sesion(client):
    r = client.post('/api/pos/sesion/abrir', json={
        'cajero': 'Ana', 'fondo_inicial': 50000
    })
    assert r.status_code == 201
    body = json.loads(r.data)
    assert body['id'] > 0

def test_sesion_activa_despues_de_abrir(client):
    client.post('/api/pos/sesion/abrir', json={'cajero': 'Ana', 'fondo_inicial': 50000})
    r = client.get('/api/pos/sesion/activa')
    sesion = json.loads(r.data)['sesion']
    assert sesion is not None
    assert sesion['cajero'] == 'Ana'

def test_no_puede_abrir_dos_sesiones(client):
    client.post('/api/pos/sesion/abrir', json={'cajero': 'Ana', 'fondo_inicial': 0})
    r = client.post('/api/pos/sesion/abrir', json={'cajero': 'Pedro', 'fondo_inicial': 0})
    assert r.status_code == 409

def test_cerrar_sesion(client):
    client.post('/api/pos/sesion/abrir', json={'cajero': 'Ana', 'fondo_inicial': 10000})
    r = client.post('/api/pos/sesion/cerrar', json={'total_efectivo': 10000})
    assert r.status_code == 200
    r = client.get('/api/pos/sesion/activa')
    assert json.loads(r.data)['sesion'] is None

def test_buscar_productos(client):
    from blueprints.db import db
    with db() as c:
        c.execute("INSERT INTO productos (nombre,descripcion,precio,costo) VALUES (?,?,?,?)",
                  ('Hallulla grande', '', 500, 0))
    r = client.get('/api/pos/productos/buscar?q=hallulla')
    assert r.status_code == 200
    resultados = json.loads(r.data)
    assert len(resultados) >= 1
    assert resultados[0]['nombre'] == 'Hallulla grande'

def test_buscar_por_codigo_barra(client):
    from blueprints.db import db
    with db() as c:
        c.execute("""INSERT INTO productos (nombre,descripcion,precio,costo,codigo_barra)
                     VALUES (?,?,?,?,?)""", ('Pan barra', '', 700, 0, '7891234567890'))
    r = client.get('/api/pos/productos/buscar?q=7891234567890')
    resultados = json.loads(r.data)
    assert len(resultados) >= 1
    assert resultados[0]['codigo_barra'] == '7891234567890'
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
venv\Scripts\pytest tests/test_pos.py -v
```
Expected: `404` en las rutas que no existen aún.

- [ ] **Step 3: Agregar rutas de sesión y búsqueda a blueprints/pos.py**

Reemplazar el contenido de `blueprints/pos.py` con:

```python
from flask import Blueprint, jsonify, request, render_template
from datetime import datetime
from .db import db
from .promociones import evaluar_precio

pos_bp = Blueprint('pos', __name__, url_prefix='/pos')

_carro = {
    'items': [],
    'subtotal': 0,
    'descuento': 0,
    'total': 0,
    'completado': False,
}


def init_pos_tables():
    with db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS pos_sesiones (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                cajero          TEXT    NOT NULL DEFAULT 'Cajero',
                fecha_apertura  TEXT    NOT NULL DEFAULT (datetime('now')),
                fecha_cierre    TEXT,
                fondo_inicial   INTEGER NOT NULL DEFAULT 0,
                total_ventas    INTEGER NOT NULL DEFAULT 0,
                total_efectivo  INTEGER,
                diferencia      INTEGER,
                estado          TEXT    NOT NULL DEFAULT 'abierta'
            );
            CREATE TABLE IF NOT EXISTS pos_ventas (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sesion_id       INTEGER NOT NULL REFERENCES pos_sesiones(id),
                cliente_id      INTEGER REFERENCES clientes(id),
                subtotal        INTEGER NOT NULL DEFAULT 0,
                descuento_total INTEGER NOT NULL DEFAULT 0,
                total           INTEGER NOT NULL DEFAULT 0,
                medio_pago      TEXT    NOT NULL DEFAULT 'efectivo',
                monto_recibido  INTEGER NOT NULL DEFAULT 0,
                vuelto          INTEGER NOT NULL DEFAULT 0,
                boleta_id       INTEGER,
                fecha           TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS pos_venta_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                venta_id        INTEGER NOT NULL REFERENCES pos_ventas(id) ON DELETE CASCADE,
                producto_id     INTEGER NOT NULL REFERENCES productos(id),
                cantidad        REAL    NOT NULL,
                precio_unitario INTEGER NOT NULL,
                tipo_precio     TEXT    NOT NULL DEFAULT 'normal',
                promocion_id    INTEGER REFERENCES promociones(id),
                subtotal        INTEGER NOT NULL DEFAULT 0
            );
        """)
        cols = [r['name'] for r in c.execute("PRAGMA table_info(productos)").fetchall()]
        if 'codigo_barra' not in cols:
            c.execute("ALTER TABLE productos ADD COLUMN codigo_barra TEXT NOT NULL DEFAULT ''")


# ── Sesiones de caja ──────────────────────────────────────────────────────────

@pos_bp.route('/api/pos/sesion/activa')
def api_sesion_activa():
    with db() as c:
        row = c.execute(
            "SELECT * FROM pos_sesiones WHERE estado='abierta' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return jsonify({'sesion': dict(row) if row else None})


@pos_bp.route('/api/pos/sesion/abrir', methods=['POST'])
def api_sesion_abrir():
    with db() as c:
        abierta = c.execute(
            "SELECT id FROM pos_sesiones WHERE estado='abierta'"
        ).fetchone()
        if abierta:
            return jsonify({'error': 'Ya hay una sesión abierta'}), 409
        d = request.get_json(force=True)
        cur = c.execute(
            "INSERT INTO pos_sesiones (cajero, fondo_inicial) VALUES (?,?)",
            (d.get('cajero', 'Cajero'), int(d.get('fondo_inicial', 0)))
        )
        return jsonify({'id': cur.lastrowid}), 201


@pos_bp.route('/api/pos/sesion/cerrar', methods=['POST'])
def api_sesion_cerrar():
    with db() as c:
        sesion = c.execute(
            "SELECT * FROM pos_sesiones WHERE estado='abierta' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not sesion:
            return jsonify({'error': 'No hay sesión abierta'}), 404
        d = request.get_json(force=True)
        total_efectivo = int(d.get('total_efectivo', 0))
        diferencia = total_efectivo - (sesion['fondo_inicial'] + sesion['total_ventas'])
        c.execute("""
            UPDATE pos_sesiones
            SET estado='cerrada', fecha_cierre=datetime('now'),
                total_efectivo=?, diferencia=?
            WHERE id=?
        """, (total_efectivo, diferencia, sesion['id']))
        return jsonify({'diferencia': diferencia})


# ── Búsqueda de productos ─────────────────────────────────────────────────────

@pos_bp.route('/api/pos/productos/buscar')
def api_buscar_productos():
    q = request.args.get('q', '').strip()
    with db() as c:
        if q:
            rows = c.execute("""
                SELECT id, nombre, precio, precio_mayorista, codigo_barra, stock, unidad
                FROM productos
                WHERE activo=1
                  AND (nombre LIKE ? OR codigo_barra=?)
                ORDER BY nombre LIMIT 20
            """, (f'%{q}%', q)).fetchall()
        else:
            rows = c.execute("""
                SELECT id, nombre, precio, precio_mayorista, codigo_barra, stock, unidad
                FROM productos WHERE activo=1
                ORDER BY nombre LIMIT 50
            """).fetchall()
        return jsonify([dict(r) for r in rows])


# ── Carro (estado para pantalla cliente) ─────────────────────────────────────

@pos_bp.route('/api/pos/carro')
def api_carro():
    return jsonify(_carro)


# ── Páginas ───────────────────────────────────────────────────────────────────

@pos_bp.route('/')
def index():
    return render_template('pos.html', active='pos')


@pos_bp.route('/cliente')
def cliente():
    return render_template('pos_cliente.html')
```

- [ ] **Step 4: Ejecutar tests**

```bash
venv\Scripts\pytest tests/test_pos.py -v
```
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add blueprints/pos.py tests/test_pos.py
git commit -m "feat: POS session management and product search API"
```

---

## Task 7: POS — Cobro y registro de venta

**Files:**
- Modify: `blueprints/pos.py` (agregar ruta `/api/pos/venta`)
- Modify: `tests/test_pos.py` (agregar tests de cobro)

- [ ] **Step 1: Agregar tests de cobro a tests/test_pos.py**

Agregar al final de `tests/test_pos.py`:
```python
def _abrir_sesion(client):
    client.post('/api/pos/sesion/abrir', json={'cajero': 'Ana', 'fondo_inicial': 50000})

def _crear_producto(precio=1000):
    from blueprints.db import db
    with db() as c:
        return c.execute(
            "INSERT INTO productos (nombre,descripcion,precio,costo) VALUES (?,?,?,?)",
            ('Pan test', '', precio, 0)
        ).lastrowid

def test_cobrar_venta_efectivo(client):
    _abrir_sesion(client)
    pid = _crear_producto(1000)
    r = client.post('/api/pos/venta', json={
        'items': [{'producto_id': pid, 'cantidad': 2, 'tipo_precio': 'normal'}],
        'medio_pago': 'efectivo',
        'monto_recibido': 5000,
    })
    assert r.status_code == 201
    body = json.loads(r.data)
    assert body['total'] == 2000
    assert body['vuelto'] == 3000

def test_cobrar_sin_sesion_da_409(client):
    pid = _crear_producto()
    r = client.post('/api/pos/venta', json={
        'items': [{'producto_id': pid, 'cantidad': 1, 'tipo_precio': 'normal'}],
        'medio_pago': 'efectivo',
        'monto_recibido': 1000,
    })
    assert r.status_code == 409

def test_venta_registrada_en_tabla_ventas(client):
    _abrir_sesion(client)
    pid = _crear_producto(500)
    client.post('/api/pos/venta', json={
        'items': [{'producto_id': pid, 'cantidad': 1, 'tipo_precio': 'normal'}],
        'medio_pago': 'transferencia',
        'monto_recibido': 500,
    })
    from blueprints.db import db
    with db() as c:
        venta = c.execute(
            "SELECT * FROM ventas WHERE canal='POS' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert venta is not None
        assert venta['total'] == 500
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

```bash
venv\Scripts\pytest tests/test_pos.py::test_cobrar_venta_efectivo -v
```
Expected: `404`

- [ ] **Step 3: Agregar ruta /api/pos/venta a blueprints/pos.py**

Agregar después de la ruta `api_carro` en `blueprints/pos.py`:

```python
@pos_bp.route('/api/pos/venta', methods=['POST'])
def api_cobrar():
    global _carro
    d = request.get_json(force=True)
    items = d.get('items', [])
    if not items:
        return jsonify({'error': 'Sin items'}), 400

    with db() as c:
        sesion = c.execute(
            "SELECT * FROM pos_sesiones WHERE estado='abierta' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not sesion:
            return jsonify({'error': 'No hay sesión de caja abierta'}), 409

        # Calcular totales aplicando promociones
        subtotal = 0
        descuento_total = 0
        items_calculados = []

        for item in items:
            prod_id   = int(item['producto_id'])
            cantidad  = float(item.get('cantidad', 1))
            tipo_sol  = item.get('tipo_precio', 'normal')

            prod = c.execute("SELECT precio FROM productos WHERE id=?", (prod_id,)).fetchone()
            if not prod:
                continue

            precio_original = int(prod['precio'])
            precio_final, tipo_real, promo_id = evaluar_precio(c, prod_id, int(cantidad), tipo_sol)

            linea_subtotal = int(precio_final * cantidad)
            linea_descuento = int((precio_original - precio_final) * cantidad)
            subtotal += linea_subtotal
            descuento_total += linea_descuento
            items_calculados.append({
                'producto_id':    prod_id,
                'cantidad':       cantidad,
                'precio_unitario':precio_final,
                'tipo_precio':    tipo_real,
                'promocion_id':   promo_id,
                'subtotal':       linea_subtotal,
            })

        total = subtotal
        medio_pago      = d.get('medio_pago', 'efectivo')
        monto_recibido  = int(d.get('monto_recibido', total))
        vuelto          = max(0, monto_recibido - total)

        # Insertar en pos_ventas
        pv_id = c.execute("""
            INSERT INTO pos_ventas
              (sesion_id, cliente_id, subtotal, descuento_total, total,
               medio_pago, monto_recibido, vuelto)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            sesion['id'], d.get('cliente_id'), subtotal, descuento_total,
            total, medio_pago, monto_recibido, vuelto
        )).lastrowid

        for it in items_calculados:
            c.execute("""
                INSERT INTO pos_venta_items
                  (venta_id, producto_id, cantidad, precio_unitario,
                   tipo_precio, promocion_id, subtotal)
                VALUES (?,?,?,?,?,?,?)
            """, (pv_id, it['producto_id'], it['cantidad'], it['precio_unitario'],
                  it['tipo_precio'], it['promocion_id'], it['subtotal']))

        # Registrar también en tabla ventas existente (para reportes)
        v_id = c.execute("""
            INSERT INTO ventas (canal, total, estado_pago)
            VALUES ('POS', ?, 'PAGADO')
        """, (total,)).lastrowid

        for it in items_calculados:
            c.execute("""
                INSERT INTO venta_items (venta_id, producto_id, cantidad, precio_unitario)
                VALUES (?,?,?,?)
            """, (v_id, it['producto_id'], it['cantidad'], it['precio_unitario']))

        # Actualizar total_ventas de la sesión
        c.execute("""
            UPDATE pos_sesiones SET total_ventas = total_ventas + ? WHERE id=?
        """, (total, sesion['id']))

    # Actualizar estado del carro
    _carro = {
        'items': [],
        'subtotal': 0,
        'descuento': 0,
        'total': 0,
        'completado': True,
    }

    return jsonify({
        'id': pv_id,
        'total': total,
        'vuelto': vuelto,
        'descuento_total': descuento_total,
    }), 201
```

También agregar ruta para actualizar el carro desde el front (necesario para pantalla cliente):

```python
@pos_bp.route('/api/pos/carro', methods=['PUT'])
def api_carro_actualizar():
    global _carro
    d = request.get_json(force=True)
    _carro.update(d)
    _carro['completado'] = False
    return jsonify({'ok': True})
```

- [ ] **Step 4: Ejecutar tests**

```bash
venv\Scripts\pytest tests/test_pos.py -v
```
Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add blueprints/pos.py tests/test_pos.py
git commit -m "feat: POS payment processing with promotions integration and ventas sync"
```

---

## Task 8: POS — Template cajero

**Files:**
- Create: `templates/pos.html`

- [ ] **Step 1: Crear templates/pos.html**

```html
{% extends "base.html" %}
{% block title %}Punto de Venta — Aurora Bakers{% endblock %}
{% block page_title %}Punto de Venta{% endblock %}

{% block content %}
<!-- Banner sesión cerrada -->
<div id="banner-sin-sesion" style="display:none;background:var(--warning-bg);color:var(--warning);padding:.75rem 1rem;border-radius:8px;margin-bottom:1rem;display:flex;justify-content:space-between;align-items:center">
  <span><i class="bi bi-exclamation-triangle-fill"></i> No hay sesión de caja abierta</span>
  <button class="btn btn-sm" onclick="abrirModalSesion()" style="background:var(--warning);color:#fff">Abrir caja</button>
</div>

<div id="pos-activo" style="display:none">
  <div style="display:grid;grid-template-columns:1fr 380px;gap:1rem;height:calc(100vh - 140px)">

    <!-- COLUMNA IZQUIERDA: búsqueda + grid -->
    <div style="display:flex;flex-direction:column;gap:.75rem;overflow:hidden">
      <div style="position:relative">
        <i class="bi bi-upc-scan" style="position:absolute;left:.75rem;top:50%;transform:translateY(-50%);color:var(--text-3)"></i>
        <input type="text" id="buscar" placeholder="F1 · Buscar por nombre o escanear código de barras..."
          style="width:100%;padding:.6rem .75rem .6rem 2.5rem;border:1px solid var(--border);border-radius:8px;font-size:.95rem"
          oninput="buscarProductos(this.value)"
          onkeydown="if(event.key==='Enter'){agregarPrimero()}">
      </div>

      <!-- Selector tipo precio -->
      <div style="display:flex;gap:.5rem;align-items:center;font-size:.85rem">
        <span style="color:var(--text-3)">Precio:</span>
        <button id="btn-normal"    onclick="setTipoPrecio('normal')"    class="btn-precio activo">Normal</button>
        <button id="btn-mayorista" onclick="setTipoPrecio('mayorista')" class="btn-precio">Mayorista</button>
        <button id="btn-promo"     onclick="setTipoPrecio('promo')"     class="btn-precio">Promoción</button>
      </div>

      <div id="grid-productos" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.5rem;overflow-y:auto;flex:1"></div>
    </div>

    <!-- COLUMNA DERECHA: carro -->
    <div style="display:flex;flex-direction:column;background:var(--bg-1);border:1px solid var(--border);border-radius:12px;overflow:hidden">
      <div style="padding:.75rem 1rem;border-bottom:1px solid var(--border);font-weight:600;font-size:.95rem">
        <i class="bi bi-cart3"></i> Carro
      </div>

      <div id="carro-items" style="flex:1;overflow-y:auto;padding:.5rem"></div>

      <div style="padding:.75rem 1rem;border-top:1px solid var(--border)">
        <div style="display:flex;justify-content:space-between;font-size:.85rem;color:var(--text-3);margin-bottom:.3rem">
          <span>Subtotal</span><span id="lbl-subtotal">$0</span>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:.85rem;color:var(--success);margin-bottom:.3rem" id="fila-descuento" style="display:none">
          <span>Descuento</span><span id="lbl-descuento">$0</span>
        </div>
        <div style="display:flex;justify-content:space-between;font-weight:700;font-size:1.3rem;margin-bottom:.75rem">
          <span>Total</span><span id="lbl-total">$0</span>
        </div>

        <select id="medio-pago" class="form-control" style="margin-bottom:.5rem">
          <option value="efectivo">Efectivo</option>
          <option value="debito">Débito</option>
          <option value="credito">Crédito</option>
          <option value="transferencia">Transferencia</option>
        </select>

        <div id="bloque-efectivo">
          <input type="number" id="monto-recibido" class="form-control" placeholder="Monto recibido"
            style="margin-bottom:.5rem" oninput="calcularVuelto()">
          <div style="display:flex;justify-content:space-between;font-size:.9rem;margin-bottom:.75rem">
            <span style="color:var(--text-3)">Vuelto</span>
            <span id="lbl-vuelto" style="font-weight:600">$0</span>
          </div>
        </div>

        <button id="btn-cobrar" onclick="cobrar(false)"
          style="width:100%;padding:.75rem;background:var(--primary);color:#fff;border:none;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer;margin-bottom:.4rem">
          <i class="bi bi-cash-stack"></i> F2 · Cobrar
        </button>
        <button onclick="cobrar(true)"
          style="width:100%;padding:.5rem;background:var(--bg-2);border:1px solid var(--border);border-radius:8px;font-size:.85rem;cursor:pointer;margin-bottom:.4rem">
          <i class="bi bi-receipt"></i> Cobrar + Emitir boleta
        </button>
        <button onclick="vaciarCarro()"
          style="width:100%;padding:.4rem;background:transparent;border:1px solid var(--border);border-radius:8px;font-size:.8rem;color:var(--text-3);cursor:pointer">
          Esc · Vaciar carro
        </button>
      </div>
    </div>
  </div>
</div>

<!-- Modal abrir sesión -->
<div id="modal-sesion" class="modal" style="display:none">
  <div class="modal-backdrop"></div>
  <div class="modal-box" style="max-width:360px">
    <div class="modal-header">
      <h3>Abrir caja</h3>
    </div>
    <div class="modal-body">
      <div class="form-group">
        <label>Nombre del cajero</label>
        <input type="text" id="ses-cajero" class="form-control" value="Cajero">
      </div>
      <div class="form-group">
        <label>Fondo inicial (efectivo)</label>
        <input type="number" id="ses-fondo" class="form-control" value="0" min="0">
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-primary" onclick="confirmarAbrirSesion()">Abrir caja</button>
    </div>
  </div>
</div>

<!-- Modal cerrar sesión -->
<div id="modal-cerrar" class="modal" style="display:none">
  <div class="modal-backdrop"></div>
  <div class="modal-box" style="max-width:360px">
    <div class="modal-header">
      <h3>Cerrar caja</h3>
    </div>
    <div class="modal-body">
      <p style="color:var(--text-3);font-size:.85rem">Cuenta el efectivo en caja y regístralo:</p>
      <div class="form-group">
        <label>Total efectivo en caja</label>
        <input type="number" id="cierre-efectivo" class="form-control" value="0">
      </div>
      <div id="cierre-diferencia" style="font-size:.85rem;margin-top:.5rem"></div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="document.getElementById('modal-cerrar').style.display='none'">Cancelar</button>
      <button class="btn btn-primary" onclick="confirmarCerrarSesion()">Cerrar caja</button>
    </div>
  </div>
</div>

<style>
.btn-precio {
  padding:.3rem .7rem;border:1px solid var(--border);border-radius:6px;background:var(--bg-1);cursor:pointer;font-size:.82rem;
}
.btn-precio.activo {
  background:var(--primary);color:#fff;border-color:var(--primary);
}
.prod-card {
  padding:.6rem;border:1px solid var(--border);border-radius:8px;cursor:pointer;background:var(--bg-0);
  transition:border-color .15s;user-select:none;
}
.prod-card:hover { border-color:var(--primary); }
.prod-card .nombre { font-size:.82rem;font-weight:600;margin-bottom:.2rem; }
.prod-card .precio { font-size:.9rem;color:var(--primary);font-weight:700; }
.item-carro {
  display:flex;align-items:center;gap:.5rem;padding:.4rem .5rem;border-bottom:1px solid var(--border);font-size:.85rem;
}
.item-carro .nombre { flex:1; }
.item-carro .precio { color:var(--text-3);white-space:nowrap; }
.item-carro .badge-promo { font-size:.7rem;background:var(--success-bg);color:var(--success);padding:.1rem .35rem;border-radius:4px; }
</style>

<script>
let carro = [];
let tipoPrecio = 'normal';
let sesionActiva = null;

function fmt(n) { return '$' + Math.round(n).toLocaleString('es-CL'); }

function setTipoPrecio(tipo) {
  tipoPrecio = tipo;
  ['normal','mayorista','promo'].forEach(t =>
    document.getElementById('btn-'+t).classList.toggle('activo', t === tipo)
  );
}

async function inicializar() {
  const r = await api('/api/pos/sesion/activa');
  sesionActiva = r.sesion;
  document.getElementById('banner-sin-sesion').style.display = sesionActiva ? 'none' : 'flex';
  document.getElementById('pos-activo').style.display = sesionActiva ? '' : 'none';
  if (sesionActiva) {
    await cargarProductos('');
    document.getElementById('buscar').focus();
  }
}

async function cargarProductos(q) {
  const prods = await api(`/api/pos/productos/buscar?q=${encodeURIComponent(q)}`);
  const grid = document.getElementById('grid-productos');
  grid.innerHTML = prods.map(p => `
    <div class="prod-card" onclick="agregarAlCarro(${p.id},'${p.nombre.replace(/'/g,"\\'")}',${p.precio},${p.precio_mayorista||0},'${p.codigo_barra||''}')">
      <div class="nombre">${p.nombre}</div>
      <div class="precio">${fmt(p.precio)}</div>
      ${p.stock > 0 ? `<div style="font-size:.72rem;color:var(--text-3)">${p.stock} ${p.unidad}</div>` : ''}
    </div>
  `).join('');
}

let _buscarTimer;
function buscarProductos(q) {
  clearTimeout(_buscarTimer);
  _buscarTimer = setTimeout(() => cargarProductos(q), 200);
}

function agregarPrimero() {
  const primer = document.querySelector('.prod-card');
  if (primer) primer.click();
}

async function agregarAlCarro(id, nombre, precioNormal, precioMayorista, codigo) {
  // Obtener precio con promociones aplicadas
  const tipo = tipoPrecio === 'promo' ? 'normal' : tipoPrecio;
  const ev = await api(`/api/promociones/evaluar?producto_id=${id}&cantidad=1&tipo=${tipo}`);

  const precio = ev.precio_final;
  const tipoReal = ev.tipo_precio !== 'normal' ? ev.tipo_precio
    : tipoPrecio === 'mayorista' ? 'mayorista' : 'normal';

  const idx = carro.findIndex(x => x.id === id && x.tipo_precio === tipoReal);
  if (idx >= 0) {
    carro[idx].cantidad++;
    carro[idx].subtotal = carro[idx].cantidad * carro[idx].precio;
  } else {
    carro.push({ id, nombre, precio, precio_original: precioNormal,
                 cantidad: 1, subtotal: precio, tipo_precio: tipoReal,
                 promocion_id: ev.promocion_id });
  }

  renderCarro();
  sincronizarCarroServidor();
}

function cambiarCantidad(idx, delta) {
  carro[idx].cantidad += delta;
  if (carro[idx].cantidad <= 0) {
    carro.splice(idx, 1);
  } else {
    carro[idx].subtotal = carro[idx].cantidad * carro[idx].precio;
  }
  renderCarro();
  sincronizarCarroServidor();
}

function renderCarro() {
  const el = document.getElementById('carro-items');
  if (!carro.length) {
    el.innerHTML = '<div style="text-align:center;color:var(--text-3);padding:2rem;font-size:.85rem">Sin productos — busca o escanea</div>';
    actualizarTotales(0, 0);
    return;
  }
  el.innerHTML = carro.map((item, i) => `
    <div class="item-carro">
      <div class="nombre">
        ${item.nombre}
        ${item.tipo_precio !== 'normal' ? `<span class="badge-promo">${item.tipo_precio}</span>` : ''}
      </div>
      <div style="display:flex;align-items:center;gap:.3rem">
        <button onclick="cambiarCantidad(${i},-1)" style="width:22px;height:22px;border:1px solid var(--border);border-radius:4px;cursor:pointer;background:var(--bg-1)">-</button>
        <span style="min-width:20px;text-align:center">${item.cantidad}</span>
        <button onclick="cambiarCantidad(${i},1)" style="width:22px;height:22px;border:1px solid var(--border);border-radius:4px;cursor:pointer;background:var(--bg-1)">+</button>
      </div>
      <div class="precio">${fmt(item.subtotal)}</div>
    </div>
  `).join('');

  const subtotal = carro.reduce((s, x) => s + x.cantidad * x.precio_original, 0);
  const total    = carro.reduce((s, x) => s + x.subtotal, 0);
  const descuento = subtotal - total;
  actualizarTotales(total, descuento);
}

function actualizarTotales(total, descuento) {
  document.getElementById('lbl-subtotal').textContent = fmt(total + descuento);
  document.getElementById('lbl-descuento').textContent = '-' + fmt(descuento);
  document.getElementById('fila-descuento').style.display = descuento > 0 ? 'flex' : 'none';
  document.getElementById('lbl-total').textContent = fmt(total);
  calcularVuelto();
}

function calcularVuelto() {
  const total    = carro.reduce((s, x) => s + x.subtotal, 0);
  const recibido = parseFloat(document.getElementById('monto-recibido').value || 0);
  const vuelto   = Math.max(0, recibido - total);
  document.getElementById('lbl-vuelto').textContent = fmt(vuelto);
}

document.getElementById('medio-pago').addEventListener('change', function() {
  document.getElementById('bloque-efectivo').style.display =
    this.value === 'efectivo' ? '' : 'none';
});

async function sincronizarCarroServidor() {
  const total = carro.reduce((s, x) => s + x.subtotal, 0);
  await fetch('/api/pos/carro', {
    method: 'PUT',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      items: carro.map(x => ({ nombre: x.nombre, cantidad: x.cantidad, subtotal: x.subtotal })),
      total,
      subtotal: total,
      completado: false,
    })
  });
}

async function cobrar(conBoleta = false) {
  if (!carro.length) { toast('El carro está vacío', 'error'); return; }
  const medio = document.getElementById('medio-pago').value;
  const recibido = parseInt(document.getElementById('monto-recibido').value || 0);
  const total = carro.reduce((s, x) => s + x.subtotal, 0);

  if (medio === 'efectivo' && recibido < total) {
    toast('Monto recibido insuficiente', 'error'); return;
  }

  const body = {
    items: carro.map(x => ({
      producto_id: x.id,
      cantidad: x.cantidad,
      tipo_precio: x.tipo_precio,
    })),
    medio_pago: medio,
    monto_recibido: medio === 'efectivo' ? recibido : total,
  };

  const r = await fetch('/api/pos/venta', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(body)
  });

  if (!r.ok) { toast('Error al registrar venta', 'error'); return; }
  const data = await r.json();

  if (conBoleta && data.id) {
    // Plan 2: emitir boleta
    toast(`Venta #${data.id} registrada. Boleta: próximamente`, 'info');
  } else {
    toast(`Venta registrada · Total ${fmt(data.total)} · Vuelto ${fmt(data.vuelto)}`);
  }

  carro = [];
  document.getElementById('monto-recibido').value = '';
  renderCarro();
  document.getElementById('buscar').value = '';
  cargarProductos('');
  document.getElementById('buscar').focus();
  await fetch('/api/pos/carro', {
    method: 'PUT',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ items:[], total:0, completado: true })
  });
}

function vaciarCarro() {
  if (!carro.length) return;
  if (!confirm('¿Vaciar el carro?')) return;
  carro = [];
  renderCarro();
}

// Atajos de teclado
document.addEventListener('keydown', e => {
  if (e.key === 'F1') { e.preventDefault(); document.getElementById('buscar').focus(); }
  if (e.key === 'F2') { e.preventDefault(); cobrar(false); }
  if (e.key === 'Escape') { vaciarCarro(); }
});

// Modal sesión
function abrirModalSesion() {
  document.getElementById('modal-sesion').style.display = '';
}

async function confirmarAbrirSesion() {
  const r = await fetch('/api/pos/sesion/abrir', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      cajero: document.getElementById('ses-cajero').value,
      fondo_inicial: parseInt(document.getElementById('ses-fondo').value || 0)
    })
  });
  if (!r.ok) { toast('Error al abrir sesión', 'error'); return; }
  document.getElementById('modal-sesion').style.display = 'none';
  toast('Caja abierta');
  inicializar();
}

async function confirmarCerrarSesion() {
  const efectivo = parseInt(document.getElementById('cierre-efectivo').value || 0);
  await fetch('/api/pos/sesion/cerrar', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ total_efectivo: efectivo })
  });
  document.getElementById('modal-cerrar').style.display = 'none';
  toast('Caja cerrada');
  inicializar();
}

// Mostrar diferencia en tiempo real en modal cierre
document.getElementById('cierre-efectivo').addEventListener('input', function() {
  if (!sesionActiva) return;
  const efectivo = parseInt(this.value || 0);
  const diff = efectivo - (sesionActiva.fondo_inicial + sesionActiva.total_ventas);
  const el = document.getElementById('cierre-diferencia');
  el.style.color = diff >= 0 ? 'var(--success)' : 'var(--danger)';
  el.textContent = `Diferencia: ${diff >= 0 ? '+' : ''}${fmt(diff)}`;
});

inicializar();
</script>
{% endblock %}
```

- [ ] **Step 2: Verificar en el navegador**

Arrancar la app y abrir `http://127.0.0.1:5000/pos/`
Expected:
1. Banner "No hay sesión de caja" visible → hacer clic en "Abrir caja" → ingresar cajero y fondo → caja abierta
2. Buscar un producto por nombre → aparece en el grid → hacer clic → se agrega al carro
3. Cambiar cantidad con + / - en el carro
4. Seleccionar "Efectivo", ingresar monto recibido → vuelto se calcula automáticamente
5. Hacer clic en "Cobrar" → toast de confirmación → carro se vacía
6. Tecla F1 focaliza búsqueda, F2 activa cobro

- [ ] **Step 3: Commit**

```bash
git add templates/pos.html
git commit -m "feat: POS cajero UI with barcode scanner support and keyboard shortcuts"
```

---

## Task 9: Pantalla cliente

**Files:**
- Create: `templates/pos_cliente.html`

- [ ] **Step 1: Crear templates/pos_cliente.html**

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Aurora Bakers — Pantalla Cliente</title>
  <link rel="stylesheet" href="/static/css/style.css">
  <style>
    body {
      margin: 0; padding: 0;
      background: var(--bg-0);
      font-family: system-ui, sans-serif;
      display: flex; flex-direction: column;
      min-height: 100vh;
    }
    .header {
      background: var(--primary);
      color: #fff;
      padding: 1rem 2rem;
      display: flex;
      align-items: center;
      gap: 1rem;
    }
    .header img { height: 40px; }
    .header h1 { font-size: 1.3rem; margin: 0; }
    .content {
      flex: 1;
      padding: 2rem;
      max-width: 800px;
      margin: 0 auto;
      width: 100%;
    }
    .items-tabla { width: 100%; border-collapse: collapse; margin-bottom: 1.5rem; }
    .items-tabla th {
      text-align: left; padding: .6rem .75rem;
      border-bottom: 2px solid var(--border);
      color: var(--text-3); font-size: .85rem;
    }
    .items-tabla td {
      padding: .7rem .75rem;
      border-bottom: 1px solid var(--border);
      font-size: 1rem;
    }
    .items-tabla td.precio { text-align: right; font-weight: 600; }
    .total-box {
      display: flex; justify-content: space-between; align-items: center;
      background: var(--bg-1);
      border: 2px solid var(--primary);
      border-radius: 12px;
      padding: 1.25rem 1.5rem;
      font-size: 1.6rem;
      font-weight: 700;
    }
    .gracias {
      display: none;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 60vh;
      text-align: center;
    }
    .gracias .icono { font-size: 5rem; color: var(--success); }
    .gracias h2 { font-size: 2.5rem; margin: 1rem 0 .5rem; }
    .gracias p { color: var(--text-3); font-size: 1.1rem; }
    .vacio {
      display: flex; flex-direction: column; align-items: center;
      justify-content: center; min-height: 60vh; text-align: center;
      color: var(--text-3);
    }
    .vacio .icono { font-size: 4rem; margin-bottom: 1rem; }
  </style>
</head>
<body>
  <div class="header">
    <img src="/static/img/logo.png" alt="Aurora Bakers" onerror="this.style.display='none'">
    <h1>Aurora Bakers</h1>
  </div>

  <div class="content">
    <div id="vista-vacia" class="vacio">
      <div class="icono">🛒</div>
      <p>Esperando productos...</p>
    </div>

    <div id="vista-carro" style="display:none">
      <table class="items-tabla">
        <thead>
          <tr><th>Producto</th><th>Cant.</th><th style="text-align:right">Subtotal</th></tr>
        </thead>
        <tbody id="tbody-items"></tbody>
      </table>
      <div class="total-box">
        <span>Total</span>
        <span id="lbl-total">$0</span>
      </div>
    </div>

    <div id="vista-gracias" class="gracias">
      <div class="icono">✅</div>
      <h2>¡Gracias por su compra!</h2>
      <p>Vuelva pronto a Aurora Bakers</p>
    </div>
  </div>

  <script>
    function fmt(n) { return '$' + Math.round(n).toLocaleString('es-CL'); }

    let ultimoEstado = null;
    let timerGracias = null;

    async function poll() {
      try {
        const r = await fetch('/api/pos/carro');
        const data = await r.json();

        if (data.completado) {
          mostrarVista('gracias');
          clearTimeout(timerGracias);
          timerGracias = setTimeout(() => mostrarVista('vacia'), 5000);
        } else if (data.items && data.items.length > 0) {
          mostrarVista('carro');
          renderItems(data.items, data.total);
        } else {
          mostrarVista('vacia');
        }
      } catch (e) { /* sin conexión, esperar */ }
    }

    function mostrarVista(v) {
      document.getElementById('vista-vacia').style.display   = v === 'vacia'   ? '' : 'none';
      document.getElementById('vista-carro').style.display   = v === 'carro'   ? '' : 'none';
      document.getElementById('vista-gracias').style.display = v === 'gracias' ? 'flex' : 'none';
    }

    function renderItems(items, total) {
      document.getElementById('tbody-items').innerHTML = items.map(it => `
        <tr>
          <td>${it.nombre}</td>
          <td>${it.cantidad}</td>
          <td class="precio">${fmt(it.subtotal)}</td>
        </tr>
      `).join('');
      document.getElementById('lbl-total').textContent = fmt(total);
    }

    setInterval(poll, 1000);
    poll();
  </script>
</body>
</html>
```

- [ ] **Step 2: Verificar en el navegador**

1. Abrir `http://127.0.0.1:5000/pos/` en una ventana (cajero)
2. Abrir `http://127.0.0.1:5000/pos/cliente` en otra ventana o tablet
3. Agregar productos en el cajero → aparecen en la pantalla cliente en ~1 segundo
4. Cobrar → pantalla cliente muestra "¡Gracias por su compra!" por 5 segundos → vuelve a espera

- [ ] **Step 3: Commit**

```bash
git add templates/pos_cliente.html
git commit -m "feat: customer display at /pos/cliente with real-time polling"
```

---

## Task 10: Sidebar + enlaces finales

**Files:**
- Modify: `templates/base.html` (agregar POS y Promociones al sidebar)

- [ ] **Step 1: Agregar POS y Promociones al sidebar de base.html**

En `templates/base.html`, localizar el bloque del sidebar (después de la línea con `/ventas`) y agregar:

```html
    <a href="/pos/"          class="nav-item {% if active=='pos'          %}active{% endif %}"><i class="bi bi-cash-register"></i> Punto de Venta</a>
    <a href="/promociones/"  class="nav-item {% if active=='promociones'  %}active{% endif %}"><i class="bi bi-tag"></i> Promociones</a>
```

Agregar justo después de la línea de `/ventas`:
```html
    <a href="/ventas"        class="nav-item {% if active=='ventas'        %}active{% endif %}"><i class="bi bi-receipt"></i> Ventas</a>
    <a href="/pos/"          class="nav-item {% if active=='pos'           %}active{% endif %}"><i class="bi bi-cash-register"></i> Punto de Venta</a>
    <a href="/promociones/"  class="nav-item {% if active=='promociones'   %}active{% endif %}"><i class="bi bi-tag"></i> Promociones</a>
```

- [ ] **Step 2: Verificar sidebar en el navegador**

Abrir cualquier página → sidebar debe mostrar "Punto de Venta" y "Promociones" con sus íconos.

- [ ] **Step 3: Ejecutar todos los tests**

```bash
venv\Scripts\pytest tests/ -v
```
Expected: todos los tests pasan (mínimo 16).

- [ ] **Step 4: Commit final**

```bash
git add templates/base.html
git commit -m "feat: add POS and Promociones to sidebar navigation"
```

---

## Verificación final

- [ ] Abrir caja → agregar productos → cobrar → carro se vacía → pantalla cliente muestra "Gracias"
- [ ] Crear promoción 10% off → agregar producto en POS con modo "Promoción" → precio baja 10%
- [ ] Escanear código de barras (o escribir el código) → producto se agrega automáticamente
- [ ] Cerrar caja → ingresar efectivo contado → ver diferencia
- [ ] Abrir `/reportes` → ventas del día deben incluir las ventas POS
