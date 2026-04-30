# POS Aurora Bakers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar un módulo POS (Punto de Venta) a Aurora Ventas con caja rápida, boleta electrónica Bsale, pantalla cliente y control de turnos.

**Architecture:** Flask Blueprint en `pos.py` registrado en `app.py`. La lógica DTE Bsale va en `dte.py`. Las ventas POS se guardan en la tabla `ventas` existente (canal=`'pos'`) más una tabla `pos_ventas` con datos de caja.

**Tech Stack:** Python Flask Blueprint, SQLite, vanilla JS (debounce + polling), Bsale REST API (`urllib.request`), Bootstrap Icons, Jinja2.

---

## File Map

| Archivo | Acción | Responsabilidad |
|---------|--------|-----------------|
| `app.py` | Modificar | Agregar 4 tablas POS en `init_db()`, registrar blueprint |
| `pos.py` | Crear | Blueprint con todas las rutas POS y estado en memoria |
| `dte.py` | Crear | Integración Bsale — única función pública `emit_boleta()` |
| `templates/pos_caja.html` | Crear | Apertura/cierre de turno + gestión de promociones |
| `templates/pos_cliente.html` | Crear | Pantalla cliente Aurora branded (polling) |
| `templates/pos.html` | Crear | Pantalla cajero: búsqueda + frecuentes + carrito |
| `templates/base.html` | Modificar | Agregar "POS / Caja" en sidebar |
| `templates/crm_configuracion.html` | Modificar | Agregar sección DTE/Bsale |
| `tests/conftest.py` | Crear | Fixtures Flask test client con DB en memoria |
| `tests/test_dte.py` | Crear | Tests para dte.py |
| `tests/test_pos_turno.py` | Crear | Tests para API de turnos |
| `tests/test_pos_venta.py` | Crear | Tests para API de venta |

---

## Task 1: Test scaffold + DB tables + Blueprint skeleton

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Modify: `app.py` líneas ~598-600 (antes del seed de productos) y ~3468-3470 (antes del arranque)
- Create: `pos.py`

- [ ] **Step 1.1: Crear tests/__init__.py**

```python
# tests/__init__.py
```

- [ ] **Step 1.2: Crear tests/conftest.py**

```python
# tests/conftest.py
import pytest, os, sys

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    for mod in list(sys.modules.keys()):
        if mod in ('app', 'pos', 'dte'):
            del sys.modules[mod]

    import app as app_mod
    app_mod.init_db()
    app_mod.app.config['TESTING'] = True
    app_mod.app.config['WTF_CSRF_ENABLED'] = False

    with app_mod.db() as c:
        from werkzeug.security import generate_password_hash
        c.execute(
            "INSERT INTO usuarios (nombre,email,password,rol) VALUES (?,?,?,?)",
            ('Cajera', 'cajera@test.cl', generate_password_hash('test123'), 'usuario')
        )
        c.execute(
            "INSERT INTO productos (nombre,precio,costo,stock,activo) VALUES (?,?,?,?,1)",
            ('Marraqueta', 200, 80, 100)
        )
        c.execute(
            "INSERT INTO productos (nombre,precio,costo,stock,activo) VALUES (?,?,?,?,1)",
            ('Kuchen', 3500, 1200, 10)
        )

    with app_mod.app.test_client() as tc:
        tc.post('/login', data={'email': 'cajera@test.cl', 'password': 'test123'},
                follow_redirects=True)
        yield tc, app_mod
```

- [ ] **Step 1.3: Agregar 4 tablas POS en init_db() de app.py**

Insertar antes de la línea `# Seed productos iniciales` (≈ línea 601):

```python
        # ── Tablas POS ──────────────────────────────────────────────────────
        c.executescript("""
            CREATE TABLE IF NOT EXISTS pos_turnos (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id               INTEGER,
                fecha_apertura           TEXT NOT NULL,
                monto_inicial_efectivo   REAL NOT NULL DEFAULT 0,
                fecha_cierre             TEXT,
                monto_declarado_efectivo REAL,
                estado                   TEXT NOT NULL DEFAULT 'abierto'
            );
            CREATE TABLE IF NOT EXISTS pos_ventas (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                turno_id       INTEGER NOT NULL REFERENCES pos_turnos(id),
                venta_id       INTEGER NOT NULL REFERENCES ventas(id),
                metodo_pago    TEXT NOT NULL,
                monto_efectivo REAL NOT NULL DEFAULT 0,
                monto_tarjeta  REAL NOT NULL DEFAULT 0,
                vuelto         REAL NOT NULL DEFAULT 0,
                boleta_numero  TEXT,
                boleta_folio   INTEGER,
                boleta_pdf_url TEXT,
                boleta_estado  TEXT NOT NULL DEFAULT 'pendiente'
            );
            CREATE TABLE IF NOT EXISTS pos_frecuentes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                producto_id INTEGER NOT NULL REFERENCES productos(id),
                orden       INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS pos_promociones (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre      TEXT NOT NULL,
                tipo        TEXT NOT NULL,
                valor       REAL NOT NULL DEFAULT 0,
                producto_id INTEGER,
                activa      INTEGER NOT NULL DEFAULT 1,
                fecha_inicio TEXT,
                fecha_fin    TEXT
            );
        """)
```

- [ ] **Step 1.4: Registrar el Blueprint en app.py**

Insertar antes de `# ── Arranque ──` (≈ línea 3470):

```python
# ── POS Blueprint ────────────────────────────────────────────────────────────
from pos import pos_bp
app.register_blueprint(pos_bp)
```

- [ ] **Step 1.5: Crear pos.py con esqueleto del Blueprint**

```python
# pos.py — Blueprint POS para Aurora Bakers
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from datetime import date, datetime
from app import db, login_required, current_user, _load_config, _save_config

pos_bp = Blueprint('pos', __name__)

# Estado en memoria del carrito activo (un proceso = un local)
_pos_carrito_activo = {
    "turno_id": None,
    "items":    [],
    "total":    0,
    "estado":   "esperando"   # esperando | en_curso | finalizado
}


@pos_bp.route('/pos')
@login_required
def page_pos():
    with db() as c:
        uid = session.get('user_id')
        turno = c.execute(
            "SELECT * FROM pos_turnos WHERE usuario_id=? AND estado='abierto' ORDER BY id DESC LIMIT 1",
            (uid,)
        ).fetchone()
    if not turno:
        return redirect(url_for('pos.page_caja', msg='Debes abrir la caja antes de vender'))
    return render_template('pos.html', active='pos', turno=dict(turno))


@pos_bp.route('/pos/caja')
@login_required
def page_caja():
    msg = request.args.get('msg', '')
    uid = session.get('user_id')
    with db() as c:
        turno = c.execute(
            "SELECT * FROM pos_turnos WHERE usuario_id=? AND estado='abierto' ORDER BY id DESC LIMIT 1",
            (uid,)
        ).fetchone()
        promociones = c.execute("SELECT * FROM pos_promociones ORDER BY id DESC").fetchall()
    return render_template('pos_caja.html', active='pos',
                           turno=dict(turno) if turno else None,
                           promociones=[dict(p) for p in promociones],
                           msg=msg)


@pos_bp.route('/pos/cliente')
def page_cliente():
    return render_template('pos_cliente.html')
```

- [ ] **Step 1.6: Verificar que la app arranca sin errores**

```bash
cd C:\Users\LENOVO\Documents\aurora-ventas
venv\Scripts\python.exe -c "import app; print('OK')"
```

Resultado esperado: `OK` (sin traceback)

- [ ] **Step 1.7: Commit**

```bash
git add tests/__init__.py tests/conftest.py pos.py app.py
git commit -m "feat(pos): Blueprint skeleton + 4 DB tables + test scaffold"
```

---

## Task 2: dte.py — Integración Bsale

**Files:**
- Create: `dte.py`
- Create: `tests/test_dte.py`

- [ ] **Step 2.1: Escribir tests/test_dte.py (tests fallan primero)**

```python
# tests/test_dte.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import dte

ITEMS = [
    {"nombre": "Marraqueta", "cantidad": 6,  "precio_unitario": 200},
    {"nombre": "Kuchen",     "cantidad": 1,  "precio_unitario": 3500},
]

def test_sin_token_retorna_error():
    result = dte.emit_boleta(ITEMS, 4700, {})
    assert result["ok"] is False
    assert result["error"] == "DTE no configurado"
    assert result["folio"] is None

def test_sin_token_con_clave_vacia():
    result = dte.emit_boleta(ITEMS, 4700, {"bsale_token": ""})
    assert result["ok"] is False
    assert "DTE no configurado" in result["error"]

def test_retorna_estructura_correcta_en_error_red(monkeypatch):
    def mock_urlopen(*args, **kwargs):
        raise Exception("Connection refused")
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    result = dte.emit_boleta(ITEMS, 4700, {"bsale_token": "fake_token"})
    assert result["ok"] is False
    assert result["folio"] is None
    assert "Connection refused" in result["error"]
```

- [ ] **Step 2.2: Ejecutar tests — deben fallar**

```bash
venv\Scripts\python.exe -m pytest tests/test_dte.py -v
```

Resultado esperado: `ERROR` o `ImportError` (dte no existe)

- [ ] **Step 2.3: Crear dte.py**

```python
# dte.py — Integración Bsale para emisión de boleta electrónica
import urllib.request
import json

BSALE_API_URL = "https://api.bsale.io/v1/documents.json"


def emit_boleta(items: list, total: float, config: dict) -> dict:
    """
    Emite una boleta electrónica via Bsale API.

    items:  [{"nombre": str, "cantidad": float, "precio_unitario": float}]
    total:  float — total a cobrar (con IVA incluido)
    config: {"bsale_token": str, "bsale_document_type_id": int, "bsale_price_list_id": int}

    Retorna: {"ok": bool, "folio": int|None, "pdf_url": str|None,
              "numero": str|None, "error": str|None}
    """
    token = config.get('bsale_token', '').strip()
    if not token:
        return {"ok": False, "folio": None, "pdf_url": None, "numero": None,
                "error": "DTE no configurado"}

    doc_type_id   = int(config.get('bsale_document_type_id', 39))
    price_list_id = int(config.get('bsale_price_list_id', 1))

    body = {
        "documentTypeId": doc_type_id,
        "officeId":       1,
        "priceListId":    price_list_id,
        "details": [
            {
                "quantity":      item["cantidad"],
                "comment":       item["nombre"],
                "grossUnitValue": round(float(item["precio_unitario"]))
            }
            for item in items
        ],
        "payments": [
            {
                "paymentTypeId": 1,
                "amount":        round(float(total)),
                "recordDate":    ""
            }
        ]
    }

    data = json.dumps(body).encode('utf-8')
    req  = urllib.request.Request(
        BSALE_API_URL,
        data=data,
        method='POST',
        headers={
            'access_token': token,
            'Content-Type': 'application/json',
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode('utf-8'))

        folio   = resp.get('number') or resp.get('folio')
        pdf_url = resp.get('urlPdf') or resp.get('dynamicLink', '')

        return {
            "ok":      True,
            "folio":   folio,
            "pdf_url": pdf_url,
            "numero":  f"B-{folio}" if folio else None,
            "error":   None
        }
    except Exception as e:
        return {
            "ok":      False,
            "folio":   None,
            "pdf_url": None,
            "numero":  None,
            "error":   str(e)
        }
```

- [ ] **Step 2.4: Ejecutar tests — deben pasar**

```bash
venv\Scripts\python.exe -m pytest tests/test_dte.py -v
```

Resultado esperado:
```
PASSED tests/test_dte.py::test_sin_token_retorna_error
PASSED tests/test_dte.py::test_sin_token_con_clave_vacia
PASSED tests/test_dte.py::test_retorna_estructura_correcta_en_error_red
3 passed
```

- [ ] **Step 2.5: Commit**

```bash
git add dte.py tests/test_dte.py
git commit -m "feat(pos): dte.py Bsale integration with tests"
```

---

## Task 3: API de Turnos

**Files:**
- Modify: `pos.py`
- Create: `tests/test_pos_turno.py`

- [ ] **Step 3.1: Escribir tests/test_pos_turno.py**

```python
# tests/test_pos_turno.py
import json

def test_no_hay_turno_activo_al_inicio(client):
    tc, _ = client
    r = tc.get('/api/pos/turno/activo')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data['turno'] is None

def test_abrir_turno(client):
    tc, _ = client
    r = tc.post('/api/pos/turno/abrir',
                json={'monto_inicial': 30000},
                content_type='application/json')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data['ok'] is True
    assert data['turno']['estado'] == 'abierto'

def test_no_abrir_dos_turnos(client):
    tc, _ = client
    tc.post('/api/pos/turno/abrir', json={'monto_inicial': 30000},
            content_type='application/json')
    r = tc.post('/api/pos/turno/abrir', json={'monto_inicial': 10000},
                content_type='application/json')
    assert r.status_code == 400

def test_cerrar_turno(client):
    tc, _ = client
    tc.post('/api/pos/turno/abrir', json={'monto_inicial': 30000},
            content_type='application/json')
    r = tc.post('/api/pos/turno/cerrar',
                json={'monto_declarado': 32000},
                content_type='application/json')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data['ok'] is True
    assert data['turno']['estado'] == 'cerrado'

def test_cerrar_sin_turno_abierto_da_error(client):
    tc, _ = client
    r = tc.post('/api/pos/turno/cerrar',
                json={'monto_declarado': 0},
                content_type='application/json')
    assert r.status_code == 400
```

- [ ] **Step 3.2: Ejecutar tests — deben fallar**

```bash
venv\Scripts\python.exe -m pytest tests/test_pos_turno.py -v
```

Resultado esperado: `FAILED` (rutas no existen aún)

- [ ] **Step 3.3: Agregar rutas de turno en pos.py**

Agregar al final de `pos.py` (antes del EOF):

```python
# ── API: Turnos ───────────────────────────────────────────────────────────────

@pos_bp.route('/api/pos/turno/activo')
@login_required
def api_turno_activo():
    uid = session.get('user_id')
    with db() as c:
        turno = c.execute(
            "SELECT * FROM pos_turnos WHERE usuario_id=? AND estado='abierto' ORDER BY id DESC LIMIT 1",
            (uid,)
        ).fetchone()
    return jsonify({'turno': dict(turno) if turno else None})


@pos_bp.route('/api/pos/turno/abrir', methods=['POST'])
@login_required
def api_turno_abrir():
    uid = session.get('user_id')
    with db() as c:
        existente = c.execute(
            "SELECT id FROM pos_turnos WHERE usuario_id=? AND estado='abierto'", (uid,)
        ).fetchone()
        if existente:
            return jsonify({'error': 'Ya tienes un turno abierto'}), 400
        monto = float(request.json.get('monto_inicial', 0))
        cur = c.execute(
            "INSERT INTO pos_turnos (usuario_id, fecha_apertura, monto_inicial_efectivo, estado) VALUES (?,?,?,?)",
            (uid, datetime.now().strftime('%Y-%m-%d %H:%M'), monto, 'abierto')
        )
        turno = c.execute("SELECT * FROM pos_turnos WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify({'ok': True, 'turno': dict(turno)})


@pos_bp.route('/api/pos/turno/cerrar', methods=['POST'])
@login_required
def api_turno_cerrar():
    uid = session.get('user_id')
    with db() as c:
        turno = c.execute(
            "SELECT * FROM pos_turnos WHERE usuario_id=? AND estado='abierto' ORDER BY id DESC LIMIT 1",
            (uid,)
        ).fetchone()
        if not turno:
            return jsonify({'error': 'No hay turno abierto'}), 400
        monto_declarado = float(request.json.get('monto_declarado', 0))
        c.execute(
            "UPDATE pos_turnos SET estado='cerrado', fecha_cierre=?, monto_declarado_efectivo=? WHERE id=?",
            (datetime.now().strftime('%Y-%m-%d %H:%M'), monto_declarado, turno['id'])
        )
        turno_actualizado = c.execute("SELECT * FROM pos_turnos WHERE id=?", (turno['id'],)).fetchone()
    _pos_carrito_activo.update({"turno_id": None, "items": [], "total": 0, "estado": "esperando"})
    return jsonify({'ok': True, 'turno': dict(turno_actualizado)})


@pos_bp.route('/api/pos/turno/<int:tid>/resumen')
@login_required
def api_turno_resumen(tid):
    with db() as c:
        turno = c.execute("SELECT * FROM pos_turnos WHERE id=?", (tid,)).fetchone()
        if not turno:
            return jsonify({'error': 'Turno no encontrado'}), 404
        ventas = c.execute(
            """SELECT pv.*, v.total FROM pos_ventas pv JOIN ventas v ON v.id=pv.venta_id
               WHERE pv.turno_id=?""", (tid,)
        ).fetchall()
        total_efectivo = sum(v['monto_efectivo'] for v in ventas)
        total_tarjeta  = sum(v['monto_tarjeta']  for v in ventas)
        total_ventas   = sum(v['total']           for v in ventas)
    return jsonify({
        'turno':          dict(turno),
        'n_ventas':       len(ventas),
        'total_ventas':   total_ventas,
        'total_efectivo': total_efectivo,
        'total_tarjeta':  total_tarjeta,
        'diferencia':     round((turno['monto_declarado_efectivo'] or 0) -
                                (turno['monto_inicial_efectivo'] + total_efectivo), 0)
    })
```

- [ ] **Step 3.4: Ejecutar tests — deben pasar**

```bash
venv\Scripts\python.exe -m pytest tests/test_pos_turno.py -v
```

Resultado esperado: `5 passed`

- [ ] **Step 3.5: Commit**

```bash
git add pos.py tests/test_pos_turno.py
git commit -m "feat(pos): turno API — abrir, cerrar, resumen"
```

---

## Task 4: API de Productos y Frecuentes

**Files:**
- Modify: `pos.py`

- [ ] **Step 4.1: Agregar rutas de productos y frecuentes en pos.py**

```python
# ── API: Productos y Frecuentes ───────────────────────────────────────────────

@pos_bp.route('/api/pos/productos')
@login_required
def api_pos_productos():
    q = request.args.get('q', '').strip()
    with db() as c:
        if q:
            productos = c.execute(
                "SELECT id,nombre,precio,stock FROM productos WHERE activo=1 AND nombre LIKE ? ORDER BY nombre LIMIT 20",
                (f'%{q}%',)
            ).fetchall()
        else:
            productos = c.execute(
                "SELECT id,nombre,precio,stock FROM productos WHERE activo=1 ORDER BY nombre LIMIT 50"
            ).fetchall()
        frecuentes_rows = c.execute(
            """SELECT pf.id as frec_id, pf.orden, p.id, p.nombre, p.precio, p.stock
               FROM pos_frecuentes pf JOIN productos p ON p.id=pf.producto_id
               WHERE p.activo=1 ORDER BY pf.orden"""
        ).fetchall()
    return jsonify({
        'productos':  [dict(p) for p in productos],
        'frecuentes': [dict(f) for f in frecuentes_rows]
    })


@pos_bp.route('/api/pos/frecuentes', methods=['GET'])
@login_required
def api_frecuentes_list():
    with db() as c:
        rows = c.execute(
            """SELECT pf.id, pf.orden, p.id as producto_id, p.nombre, p.precio
               FROM pos_frecuentes pf JOIN productos p ON p.id=pf.producto_id
               ORDER BY pf.orden"""
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@pos_bp.route('/api/pos/frecuentes', methods=['POST'])
@login_required
def api_frecuentes_add():
    d = request.json
    producto_id = d.get('producto_id')
    if not producto_id:
        return jsonify({'error': 'producto_id requerido'}), 400
    with db() as c:
        existente = c.execute("SELECT id FROM pos_frecuentes WHERE producto_id=?", (producto_id,)).fetchone()
        if existente:
            return jsonify({'error': 'Ya es frecuente'}), 400
        count = c.execute("SELECT COUNT(*) FROM pos_frecuentes").fetchone()[0]
        if count >= 8:
            return jsonify({'error': 'Máximo 8 frecuentes'}), 400
        c.execute("INSERT INTO pos_frecuentes (producto_id, orden) VALUES (?,?)", (producto_id, count))
    return jsonify({'ok': True})


@pos_bp.route('/api/pos/frecuentes/<int:fid>', methods=['DELETE'])
@login_required
def api_frecuentes_delete(fid):
    with db() as c:
        c.execute("DELETE FROM pos_frecuentes WHERE id=?", (fid,))
    return jsonify({'ok': True})
```

- [ ] **Step 4.2: Verificar manualmente**

```bash
venv\Scripts\python.exe -m pytest tests/ -v
```

Resultado esperado: todos los tests anteriores siguen pasando.

- [ ] **Step 4.3: Commit**

```bash
git add pos.py
git commit -m "feat(pos): productos y frecuentes API"
```

---

## Task 5: API de Promociones

**Files:**
- Modify: `pos.py`

- [ ] **Step 5.1: Agregar rutas de promociones en pos.py**

```python
# ── API: Promociones ──────────────────────────────────────────────────────────

@pos_bp.route('/api/pos/promociones', methods=['GET'])
@login_required
def api_promociones_list():
    with db() as c:
        rows = c.execute(
            "SELECT * FROM pos_promociones ORDER BY activa DESC, id DESC"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@pos_bp.route('/api/pos/promociones', methods=['POST'])
@login_required
def api_promociones_create():
    d = request.json
    nombre = d.get('nombre', '').strip()
    tipo   = d.get('tipo', '')
    if not nombre or tipo not in ('porcentaje', 'fijo', '2x1'):
        return jsonify({'error': 'nombre y tipo (porcentaje/fijo/2x1) requeridos'}), 400
    with db() as c:
        cur = c.execute(
            "INSERT INTO pos_promociones (nombre,tipo,valor,producto_id,activa,fecha_inicio,fecha_fin) VALUES (?,?,?,?,?,?,?)",
            (nombre, tipo, float(d.get('valor', 0)), d.get('producto_id'),
             1, d.get('fecha_inicio'), d.get('fecha_fin'))
        )
        row = c.execute("SELECT * FROM pos_promociones WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify({'ok': True, 'promocion': dict(row)})


@pos_bp.route('/api/pos/promociones/<int:pid>', methods=['PUT'])
@login_required
def api_promociones_update(pid):
    d = request.json
    with db() as c:
        c.execute(
            "UPDATE pos_promociones SET nombre=?,tipo=?,valor=?,producto_id=?,activa=?,fecha_inicio=?,fecha_fin=? WHERE id=?",
            (d.get('nombre'), d.get('tipo'), float(d.get('valor', 0)),
             d.get('producto_id'), int(d.get('activa', 1)),
             d.get('fecha_inicio'), d.get('fecha_fin'), pid)
        )
    return jsonify({'ok': True})


@pos_bp.route('/api/pos/promociones/<int:pid>', methods=['DELETE'])
@login_required
def api_promociones_delete(pid):
    with db() as c:
        c.execute("DELETE FROM pos_promociones WHERE id=?", (pid,))
    return jsonify({'ok': True})


def _aplicar_promociones(items: list) -> tuple:
    """
    Calcula descuento total basado en promociones activas.
    items: [{"producto_id": int, "nombre": str, "cantidad": float, "precio_unitario": float}]
    Retorna: (descuento_total: float, detalle: list[str])
    """
    today = date.today().isoformat()
    with db() as c:
        promos = c.execute(
            """SELECT * FROM pos_promociones WHERE activa=1
               AND (fecha_inicio IS NULL OR fecha_inicio <= ?)
               AND (fecha_fin IS NULL OR fecha_fin >= ?)""",
            (today, today)
        ).fetchall()

    descuento = 0.0
    detalle   = []
    subtotal  = sum(i['cantidad'] * i['precio_unitario'] for i in items)

    for p in promos:
        if p['tipo'] == 'porcentaje':
            if p['producto_id']:
                for item in items:
                    if item['producto_id'] == p['producto_id']:
                        d = round(item['cantidad'] * item['precio_unitario'] * p['valor'] / 100)
                        descuento += d
                        detalle.append(f"{p['nombre']}: -${d:,.0f}")
            else:
                d = round(subtotal * p['valor'] / 100)
                descuento += d
                detalle.append(f"{p['nombre']}: -${d:,.0f}")

        elif p['tipo'] == 'fijo':
            if p['producto_id']:
                for item in items:
                    if item['producto_id'] == p['producto_id']:
                        descuento += p['valor']
                        detalle.append(f"{p['nombre']}: -${p['valor']:,.0f}")
            else:
                descuento += p['valor']
                detalle.append(f"{p['nombre']}: -${p['valor']:,.0f}")

        elif p['tipo'] == '2x1':
            if p['producto_id']:
                for item in items:
                    if item['producto_id'] == p['producto_id']:
                        unidades_gratis = int(item['cantidad'] // 2)
                        d = unidades_gratis * item['precio_unitario']
                        descuento += d
                        detalle.append(f"{p['nombre']}: -${d:,.0f}")

    return round(descuento), detalle
```

- [ ] **Step 5.2: Verificar que tests siguen pasando**

```bash
venv\Scripts\python.exe -m pytest tests/ -v
```

Resultado esperado: todos los tests pasan.

- [ ] **Step 5.3: Commit**

```bash
git add pos.py
git commit -m "feat(pos): promociones CRUD + lógica _aplicar_promociones"
```

---

## Task 6: API de Venta — el corazón del POS

**Files:**
- Modify: `pos.py`
- Create: `tests/test_pos_venta.py`

- [ ] **Step 6.1: Escribir tests/test_pos_venta.py**

```python
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
```

- [ ] **Step 6.2: Ejecutar tests — deben fallar**

```bash
venv\Scripts\python.exe -m pytest tests/test_pos_venta.py -v
```

Resultado esperado: `FAILED` (ruta no existe)

- [ ] **Step 6.3: Agregar ruta POST /api/pos/venta en pos.py**

```python
# ── API: Venta ────────────────────────────────────────────────────────────────

@pos_bp.route('/api/pos/venta', methods=['POST'])
@login_required
def api_pos_venta():
    import dte as dte_mod

    d     = request.json
    items = d.get('items', [])
    if not items:
        return jsonify({'error': 'Carrito vacío'}), 400

    uid = session.get('user_id')

    with db() as c:
        turno = c.execute(
            "SELECT * FROM pos_turnos WHERE usuario_id=? AND estado='abierto' ORDER BY id DESC LIMIT 1",
            (uid,)
        ).fetchone()

    if not turno:
        return jsonify({'error': 'No hay turno abierto. Abre la caja primero.'}), 400

    # Calcular descuento por promociones
    descuento, detalle_promos = _aplicar_promociones(items)
    total_bruto = sum(float(i['cantidad']) * float(i['precio_unitario']) for i in items)
    total_final = max(0, total_bruto - descuento)

    metodo_pago    = d.get('metodo_pago', 'efectivo')
    monto_efectivo = float(d.get('monto_efectivo', 0))
    monto_tarjeta  = total_final if metodo_pago == 'tarjeta' else 0.0
    if metodo_pago == 'efectivo':
        monto_efectivo_real = monto_efectivo
        monto_tarjeta       = 0.0
    else:
        monto_efectivo_real = 0.0
        monto_tarjeta       = total_final
    vuelto = round(max(0, monto_efectivo_real - total_final)) if metodo_pago == 'efectivo' else 0

    with db() as c:
        cur = c.execute(
            """INSERT INTO ventas (fecha, canal, total, notas, estado_pago, estado_despacho, con_despacho)
               VALUES (?,?,?,?,?,?,?)""",
            (date.today().isoformat(), 'pos', total_final,
             '; '.join(detalle_promos), 'PAGADO', 'RETIRO EN TIENDA', 0)
        )
        venta_id = cur.lastrowid

        for item in items:
            c.execute(
                "INSERT INTO venta_items (venta_id,producto_id,cantidad,precio_unitario) VALUES (?,?,?,?)",
                (venta_id, item['producto_id'], float(item['cantidad']), float(item['precio_unitario']))
            )
            c.execute("UPDATE productos SET stock=stock-? WHERE id=?",
                      (float(item['cantidad']), item['producto_id']))

        pv_cur = c.execute(
            """INSERT INTO pos_ventas (turno_id,venta_id,metodo_pago,monto_efectivo,monto_tarjeta,vuelto,boleta_estado)
               VALUES (?,?,?,?,?,?,?)""",
            (turno['id'], venta_id, metodo_pago,
             monto_efectivo_real, monto_tarjeta, vuelto, 'pendiente')
        )
        pos_venta_id = pv_cur.lastrowid

    # Emitir boleta electrónica (no-blocking: fallo no cancela la venta)
    cfg      = _load_config()
    dte_resp = dte_mod.emit_boleta(items, total_final, cfg)

    with db() as c:
        if dte_resp['ok']:
            c.execute(
                "UPDATE pos_ventas SET boleta_numero=?,boleta_folio=?,boleta_pdf_url=?,boleta_estado='emitida' WHERE id=?",
                (dte_resp['numero'], dte_resp['folio'], dte_resp['pdf_url'], pos_venta_id)
            )
        else:
            c.execute("UPDATE pos_ventas SET boleta_estado='error' WHERE id=?", (pos_venta_id,))

    # Resetear pantalla cliente
    _pos_carrito_activo.update({"items": [], "total": 0, "estado": "finalizado",
                                 "total_cobrado": total_final})

    return jsonify({
        'ok':          True,
        'venta_id':    venta_id,
        'total':       total_final,
        'descuento':   descuento,
        'vuelto':      vuelto,
        'boleta':      dte_resp,
        'promos':      detalle_promos
    })
```

- [ ] **Step 6.4: Ejecutar tests — deben pasar**

```bash
venv\Scripts\python.exe -m pytest tests/test_pos_venta.py -v
```

Resultado esperado: `4 passed`

- [ ] **Step 6.5: Ejecutar todos los tests**

```bash
venv\Scripts\python.exe -m pytest tests/ -v
```

Resultado esperado: todos los tests pasan.

- [ ] **Step 6.6: Commit**

```bash
git add pos.py tests/test_pos_venta.py
git commit -m "feat(pos): venta API — efectivo/tarjeta, stock, DTE, promociones"
```

---

## Task 7: Carrito sync + Cliente estado API

**Files:**
- Modify: `pos.py`

- [ ] **Step 7.1: Agregar rutas de carrito y pantalla cliente en pos.py**

```python
# ── API: Carrito y Pantalla Cliente ──────────────────────────────────────────

@pos_bp.route('/api/pos/carrito', methods=['POST'])
@login_required
def api_pos_carrito_sync():
    """Cajero sincroniza el estado actual del carrito al servidor (debounced desde JS)."""
    d = request.json
    _pos_carrito_activo.update({
        "items":  d.get('items', []),
        "total":  float(d.get('total', 0)),
        "estado": "en_curso" if d.get('items') else "esperando"
    })
    return jsonify({'ok': True})


@pos_bp.route('/api/pos/cliente/estado')
def api_cliente_estado():
    """Polling desde /pos/cliente — no requiere login."""
    return jsonify(_pos_carrito_activo)
```

- [ ] **Step 7.2: Verificar que todos los tests siguen pasando**

```bash
venv\Scripts\python.exe -m pytest tests/ -v
```

Resultado esperado: todos los tests pasan.

- [ ] **Step 7.3: Commit**

```bash
git add pos.py
git commit -m "feat(pos): carrito sync + cliente estado API"
```

---

## Task 8: Template pos_caja.html

**Files:**
- Create: `templates/pos_caja.html`

- [ ] **Step 8.1: Crear templates/pos_caja.html**

```html
{% extends "base.html" %}
{% block title %}Caja — Aurora Bakers{% endblock %}
{% block page_title %}POS / Caja{% endblock %}

{% block content %}
{% if msg %}<div class="alert alert-warning">{{ msg }}</div>{% endif %}

<!-- Tabs -->
<div class="tabs" style="display:flex;gap:.5rem;margin-bottom:1.5rem;border-bottom:1px solid var(--border);padding-bottom:.75rem">
  <button class="btn btn-sm" id="tab-caja"   onclick="showTab('caja')"   style="font-weight:600">📦 Caja</button>
  <button class="btn btn-sm" id="tab-promos" onclick="showTab('promos')">🏷️ Promociones</button>
</div>

<!-- TAB: CAJA -->
<div id="pane-caja">
{% if not turno %}
<!-- Apertura -->
<div class="card" style="max-width:420px;margin:0 auto">
  <div class="card-header"><h3>Abrir caja</h3></div>
  <div class="card-body">
    <label class="form-label">Efectivo inicial en caja</label>
    <input type="number" id="monto-inicial" class="form-control" placeholder="30000" min="0">
    <button class="btn btn-primary mt-3 w-100" onclick="abrirCaja()">Abrir caja y empezar a vender</button>
  </div>
</div>
{% else %}
<!-- Cierre -->
<div class="card" style="max-width:480px;margin:0 auto">
  <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">
    <h3>Caja abierta</h3>
    <span class="badge badge-success">● Activa</span>
  </div>
  <div class="card-body">
    <div class="stat-row"><span>Apertura</span><strong>{{ turno.fecha_apertura }}</strong></div>
    <div class="stat-row"><span>Efectivo inicial</span><strong id="ef-inicial">${{ '{:,.0f}'.format(turno.monto_inicial_efectivo) }}</strong></div>
    <div class="stat-row"><span>Ventas efectivo</span><strong id="sum-ef">cargando…</strong></div>
    <div class="stat-row"><span>Ventas tarjeta</span><strong id="sum-tj">cargando…</strong></div>
    <div class="stat-row"><span>Total ventas</span><strong id="sum-tot">cargando…</strong></div>
    <hr>
    <label class="form-label">Efectivo contado en caja al cerrar</label>
    <input type="number" id="monto-declarado" class="form-control" placeholder="0" min="0">
    <div id="diferencia-msg" style="margin-top:.5rem;font-size:.85rem"></div>
    <button class="btn btn-danger mt-3 w-100" onclick="cerrarCaja()">Cerrar caja</button>
    <a href="/pos" class="btn btn-primary mt-2 w-100">Ir a la caja →</a>
  </div>
</div>
{% endif %}
</div>

<!-- TAB: PROMOCIONES -->
<div id="pane-promos" style="display:none">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
    <h3>Promociones activas</h3>
    <button class="btn btn-primary btn-sm" onclick="modalPromo()">+ Nueva promoción</button>
  </div>
  <table class="table" id="tabla-promos">
    <thead><tr><th>Nombre</th><th>Tipo</th><th>Valor</th><th>Estado</th><th></th></tr></thead>
    <tbody></tbody>
  </table>
</div>

<!-- Modal promoción -->
<div id="modal-promo" class="modal" style="display:none">
  <div class="modal-dialog">
    <div class="modal-header"><h4 id="modal-promo-title">Nueva promoción</h4><button onclick="cerrarModalPromo()">✕</button></div>
    <div class="modal-body">
      <input type="hidden" id="promo-id">
      <label>Nombre</label><input id="promo-nombre" class="form-control" placeholder="Ej: Descuento de bienvenida">
      <label class="mt-2">Tipo</label>
      <select id="promo-tipo" class="form-control">
        <option value="porcentaje">Porcentaje (%)</option>
        <option value="fijo">Monto fijo ($)</option>
        <option value="2x1">2x1</option>
      </select>
      <label class="mt-2">Valor (% o $, ignorado en 2x1)</label>
      <input id="promo-valor" class="form-control" type="number" min="0" placeholder="0">
      <label class="mt-2">Fecha inicio (opcional)</label>
      <input id="promo-inicio" class="form-control" type="date">
      <label class="mt-2">Fecha fin (opcional)</label>
      <input id="promo-fin" class="form-control" type="date">
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="cerrarModalPromo()">Cancelar</button>
      <button class="btn btn-primary" onclick="guardarPromo()">Guardar</button>
    </div>
  </div>
</div>

<script>
const TURNO_ID = {{ turno.id if turno else 'null' }};

function showTab(t) {
  document.getElementById('pane-caja').style.display   = t === 'caja'   ? '' : 'none';
  document.getElementById('pane-promos').style.display = t === 'promos' ? '' : 'none';
}

async function abrirCaja() {
  const monto = parseFloat(document.getElementById('monto-inicial').value) || 0;
  const r = await fetch('/api/pos/turno/abrir', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({monto_inicial: monto})
  });
  if (r.ok) location.href = '/pos';
  else { const d = await r.json(); toast(d.error, 'error'); }
}

async function cerrarCaja() {
  const monto = parseFloat(document.getElementById('monto-declarado').value) || 0;
  if (!confirm('¿Confirmas cerrar la caja?')) return;
  const r = await fetch('/api/pos/turno/cerrar', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({monto_declarado: monto})
  });
  if (r.ok) { toast('Caja cerrada'); setTimeout(() => location.reload(), 1000); }
  else { const d = await r.json(); toast(d.error, 'error'); }
}

async function cargarResumen() {
  if (!TURNO_ID) return;
  const r = await fetch(`/api/pos/turno/${TURNO_ID}/resumen`);
  const d = await r.json();
  document.getElementById('sum-ef').textContent  = `$${d.total_efectivo.toLocaleString('es-CL')}`;
  document.getElementById('sum-tj').textContent  = `$${d.total_tarjeta.toLocaleString('es-CL')}`;
  document.getElementById('sum-tot').textContent = `$${d.total_ventas.toLocaleString('es-CL')}`;
  document.getElementById('monto-declarado').addEventListener('input', () => {
    const declarado = parseFloat(document.getElementById('monto-declarado').value) || 0;
    const esperado  = d.turno.monto_inicial_efectivo + d.total_efectivo;
    const diff = declarado - esperado;
    const el   = document.getElementById('diferencia-msg');
    el.textContent = diff >= 0 ? `Sobrante: $${diff.toLocaleString('es-CL')}` : `Faltante: $${Math.abs(diff).toLocaleString('es-CL')}`;
    el.style.color = diff >= 0 ? 'var(--green)' : 'var(--red)';
  });
}

async function cargarPromos() {
  const r = await fetch('/api/pos/promociones');
  const promos = await r.json();
  const tbody = document.querySelector('#tabla-promos tbody');
  tbody.innerHTML = promos.map(p => `
    <tr>
      <td>${p.nombre}</td>
      <td>${p.tipo}</td>
      <td>${p.tipo === '2x1' ? '—' : p.tipo === 'porcentaje' ? p.valor + '%' : '$' + p.valor.toLocaleString('es-CL')}</td>
      <td><span class="badge ${p.activa ? 'badge-success' : 'badge-secondary'}">${p.activa ? 'Activa' : 'Inactiva'}</span></td>
      <td>
        <button class="btn btn-sm btn-secondary" onclick="editarPromo(${p.id})">Editar</button>
        <button class="btn btn-sm btn-danger"    onclick="eliminarPromo(${p.id})">Eliminar</button>
      </td>
    </tr>`).join('');
}

function modalPromo() {
  document.getElementById('promo-id').value     = '';
  document.getElementById('promo-nombre').value = '';
  document.getElementById('promo-tipo').value   = 'porcentaje';
  document.getElementById('promo-valor').value  = '';
  document.getElementById('modal-promo-title').textContent = 'Nueva promoción';
  document.getElementById('modal-promo').style.display = 'flex';
}
function cerrarModalPromo() { document.getElementById('modal-promo').style.display = 'none'; }

async function editarPromo(id) {
  const r = await fetch('/api/pos/promociones');
  const promos = await r.json();
  const p = promos.find(x => x.id === id);
  if (!p) return;
  document.getElementById('promo-id').value     = p.id;
  document.getElementById('promo-nombre').value = p.nombre;
  document.getElementById('promo-tipo').value   = p.tipo;
  document.getElementById('promo-valor').value  = p.valor;
  document.getElementById('modal-promo-title').textContent = 'Editar promoción';
  document.getElementById('modal-promo').style.display = 'flex';
}

async function guardarPromo() {
  const id     = document.getElementById('promo-id').value;
  const body   = {
    nombre: document.getElementById('promo-nombre').value.trim(),
    tipo:   document.getElementById('promo-tipo').value,
    valor:  parseFloat(document.getElementById('promo-valor').value) || 0,
    fecha_inicio: document.getElementById('promo-inicio').value || null,
    fecha_fin:    document.getElementById('promo-fin').value    || null,
    activa: 1
  };
  const url    = id ? `/api/pos/promociones/${id}` : '/api/pos/promociones';
  const method = id ? 'PUT' : 'POST';
  const r = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  if (r.ok) { cerrarModalPromo(); cargarPromos(); toast('Promoción guardada'); }
  else { const d = await r.json(); toast(d.error, 'error'); }
}

async function eliminarPromo(id) {
  if (!confirm('¿Eliminar promoción?')) return;
  await fetch(`/api/pos/promociones/${id}`, {method: 'DELETE'});
  cargarPromos();
}

cargarResumen();
cargarPromos();
</script>
{% endblock %}
```

- [ ] **Step 8.2: Probar en browser**

```bash
venv\Scripts\python.exe app.py
```

Abrir http://127.0.0.1:5000/pos/caja — debe mostrar el formulario de apertura.

- [ ] **Step 8.3: Commit**

```bash
git add templates/pos_caja.html
git commit -m "feat(pos): template pos_caja.html — apertura, cierre y promociones"
```

---

## Task 9: Template pos_cliente.html

**Files:**
- Create: `templates/pos_cliente.html`

- [ ] **Step 9.1: Crear templates/pos_cliente.html**

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Aurora Bakers — Pantalla Cliente</title>
  <link rel="stylesheet" href="/static/css/style.css">
  <style>
    body { background:#0f0f1a; color:#fff; margin:0; font-family:system-ui,sans-serif; min-height:100vh; display:flex; flex-direction:column; }
    .header { background:linear-gradient(135deg,#c8860a,#f5a623); padding:1.2rem 2rem; display:flex; align-items:center; gap:1rem; }
    .header h1 { margin:0; font-size:1.6rem; color:#fff; }
    .header .tagline { font-size:.85rem; color:rgba(255,255,255,.8); margin:0; }
    .content { flex:1; padding:2rem; display:flex; flex-direction:column; }
    .estado-esperando { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:1rem; }
    .estado-esperando .emoji { font-size:5rem; }
    .estado-esperando p { color:#555; font-size:1.1rem; }
    .items-table { width:100%; border-collapse:collapse; }
    .items-table th { color:#888; font-size:.75rem; text-transform:uppercase; letter-spacing:.05em; padding:.5rem .75rem; border-bottom:1px solid #2a2a3a; text-align:left; }
    .items-table td { padding:.6rem .75rem; border-bottom:1px solid #1a1a2a; font-size:1rem; }
    .items-table .precio { text-align:right; color:#f5a623; font-weight:600; }
    .total-bar { background:#1a1a2e; border-radius:.75rem; padding:1.25rem 1.5rem; display:flex; justify-content:space-between; align-items:center; margin-top:1.5rem; }
    .total-bar .label { color:#888; font-size:.9rem; }
    .total-bar .monto { font-size:2.5rem; font-weight:800; color:#fff; }
    .gracias { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:1rem; text-align:center; }
    .gracias h2 { font-size:2.5rem; color:#f5a623; margin:0; }
    .gracias .total-gracias { font-size:3.5rem; font-weight:900; color:#fff; }
    .gracias p { color:#888; font-size:1rem; }
  </style>
</head>
<body>

<div class="header">
  <div style="font-size:2rem">🥐</div>
  <div>
    <h1>Aurora Bakers</h1>
    <p class="tagline">El pan de tu barrio</p>
  </div>
</div>

<div class="content" id="main-content">
  <!-- Se rellena por JS según estado -->
</div>

<script>
let _estadoAnterior = null;
let _timerGracias   = null;

function fmt(n) { return '$' + Math.round(n).toLocaleString('es-CL'); }

function renderEsperando() {
  document.getElementById('main-content').innerHTML = `
    <div class="estado-esperando">
      <div class="emoji">🍞</div>
      <p>Esperando próxima atención…</p>
    </div>`;
}

function renderEnCurso(items, total) {
  const filas = items.map(i =>
    `<tr>
       <td>${i.nombre}</td>
       <td style="text-align:center">${i.cantidad}</td>
       <td class="precio">${fmt(i.precio_unitario)}</td>
       <td class="precio">${fmt(i.cantidad * i.precio_unitario)}</td>
     </tr>`
  ).join('');
  document.getElementById('main-content').innerHTML = `
    <table class="items-table">
      <thead><tr><th>Producto</th><th style="text-align:center">Cant.</th><th style="text-align:right">Precio</th><th style="text-align:right">Subtotal</th></tr></thead>
      <tbody>${filas}</tbody>
    </table>
    <div class="total-bar">
      <span class="label">TOTAL</span>
      <span class="monto">${fmt(total)}</span>
    </div>`;
}

function renderGracias(total) {
  document.getElementById('main-content').innerHTML = `
    <div class="gracias">
      <div style="font-size:4rem">✅</div>
      <h2>¡Gracias por tu compra!</h2>
      <div class="total-gracias">${fmt(total)}</div>
      <p>Que disfrutes tu pan 🍞</p>
    </div>`;
  if (_timerGracias) clearTimeout(_timerGracias);
  _timerGracias = setTimeout(renderEsperando, 5000);
}

async function poll() {
  try {
    const r = await fetch('/api/pos/cliente/estado');
    const d = await r.json();
    if (d.estado !== _estadoAnterior) {
      _estadoAnterior = d.estado;
      if (d.estado === 'esperando')   renderEsperando();
      if (d.estado === 'en_curso')    renderEnCurso(d.items, d.total);
      if (d.estado === 'finalizado')  renderGracias(d.total_cobrado || d.total);
    } else if (d.estado === 'en_curso') {
      renderEnCurso(d.items, d.total);
    }
  } catch (e) { /* red caída — silencioso */ }
}

renderEsperando();
setInterval(poll, 2000);
</script>
</body>
</html>
```

- [ ] **Step 9.2: Probar en browser**

Abrir http://127.0.0.1:5000/pos/cliente — debe mostrar el logo Aurora y "Esperando próxima atención…"

- [ ] **Step 9.3: Commit**

```bash
git add templates/pos_cliente.html
git commit -m "feat(pos): template pos_cliente.html — pantalla Aurora branded con polling"
```

---

## Task 10: Template pos.html — Pantalla cajero

**Files:**
- Create: `templates/pos.html`

- [ ] **Step 10.1: Crear templates/pos.html**

```html
{% extends "base.html" %}
{% block title %}POS — Aurora Bakers{% endblock %}
{% block page_title %}POS — Punto de Venta{% endblock %}

{% block content %}
<div style="display:flex;gap:1rem;height:calc(100vh - 120px)">

  <!-- Panel izquierdo: productos -->
  <div style="flex:1;display:flex;flex-direction:column;gap:.75rem;overflow:hidden">

    <!-- Barra superior: turno + búsqueda -->
    <div style="display:flex;align-items:center;gap:.75rem">
      <span class="badge badge-success" style="white-space:nowrap">● {{ turno.fecha_apertura }}</span>
      <input id="search-input" type="text" class="form-control" placeholder="🔍 Buscar producto por nombre… (ESC limpia)"
             oninput="debounceBuscar()" onkeydown="handleSearchKey(event)"
             style="flex:1">
      <a href="/pos/caja" class="btn btn-sm btn-secondary">Caja</a>
    </div>

    <!-- Frecuentes -->
    <div>
      <div style="font-size:.72rem;color:var(--text-3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:.4rem">⚡ Frecuentes</div>
      <div id="grid-frecuentes" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:.4rem"></div>
    </div>

    <!-- Lista de productos -->
    <div style="flex:1;overflow-y:auto">
      <div style="font-size:.72rem;color:var(--text-3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:.4rem">Todos los productos</div>
      <div id="lista-productos"></div>
    </div>
  </div>

  <!-- Panel derecho: carrito -->
  <div style="width:260px;display:flex;flex-direction:column;background:var(--bg-2);border-radius:.75rem;border:1px solid var(--border);overflow:hidden">

    <div style="padding:.75rem 1rem;border-bottom:1px solid var(--border);font-size:.8rem;font-weight:600;color:var(--text-2)">🛒 Carrito</div>

    <div id="carrito-items" style="flex:1;overflow-y:auto;padding:.5rem .75rem;display:flex;flex-direction:column;gap:.4rem">
      <p style="color:var(--text-3);font-size:.8rem;text-align:center;margin-top:2rem">Carrito vacío</p>
    </div>

    <!-- Resumen y pago -->
    <div style="padding:.75rem;border-top:1px solid var(--border)">
      <div id="promos-aplicadas" style="margin-bottom:.4rem"></div>
      <div style="display:flex;justify-content:space-between;margin-bottom:.25rem;font-size:.8rem;color:var(--text-3)">
        <span>Subtotal</span><span id="subtotal-val">$0</span>
      </div>
      <div style="display:flex;justify-content:space-between;margin-bottom:.75rem;font-weight:700;font-size:1.1rem">
        <span>Total</span><span id="total-val">$0</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.4rem;margin-bottom:.4rem">
        <button class="btn btn-sm" id="btn-ef"  onclick="selectPago('efectivo')"
                style="background:var(--bg-3);border:1px solid var(--border)">💵 Efectivo</button>
        <button class="btn btn-sm" id="btn-tj"  onclick="selectPago('tarjeta')"
                style="background:var(--bg-3);border:1px solid var(--border)">💳 Tarjeta</button>
      </div>
      <div id="monto-ef-wrap" style="display:none;margin-bottom:.4rem">
        <input id="monto-recibido" class="form-control form-control-sm" type="number" min="0" placeholder="Monto recibido" oninput="calcVuelto()">
        <div id="vuelto-display" style="color:var(--green);font-size:.8rem;margin-top:.25rem"></div>
      </div>
      <button class="btn btn-primary w-100" id="btn-cobrar" onclick="cobrar()" disabled>Cobrar</button>
    </div>
  </div>
</div>

<!-- Modal resultado venta -->
<div id="modal-venta" class="modal" style="display:none">
  <div class="modal-dialog" style="max-width:380px">
    <div class="modal-header"><h4>✅ Venta registrada</h4></div>
    <div class="modal-body" id="modal-venta-body"></div>
    <div class="modal-footer">
      <button class="btn btn-primary w-100" onclick="nuevaVenta()">Nueva venta</button>
    </div>
  </div>
</div>

<script>
const carrito     = [];
let metodoPago    = null;
let todosProductos = [];
let frecuentes    = [];
let promoActivas  = [];
let searchTimer   = null;

function fmt(n) { return '$' + Math.round(n).toLocaleString('es-CL'); }

// ── Carga inicial ─────────────────────────────────────────────────────────────
async function init() {
  const [rProd, rPromo] = await Promise.all([
    fetch('/api/pos/productos'),
    fetch('/api/pos/promociones')
  ]);
  const dProd  = await rProd.json();
  const dPromo = await rPromo.json();

  todosProductos = dProd.productos;
  frecuentes     = dProd.frecuentes;
  promoActivas   = dPromo.filter(p => p.activa);

  renderFrecuentes();
  renderProductos(todosProductos);
}

// ── Frecuentes ────────────────────────────────────────────────────────────────
function renderFrecuentes() {
  const g = document.getElementById('grid-frecuentes');
  g.innerHTML = frecuentes.map(f =>
    `<button class="btn btn-sm" onclick="agregarProducto(${f.id},${JSON.stringify(f.nombre)},${f.precio})"
             style="background:var(--bg-3);border:1px solid var(--border);padding:.4rem .3rem;font-size:.75rem;text-align:center;line-height:1.2">
       🍞<br>${f.nombre}<br><span style="color:var(--green)">${fmt(f.precio)}</span>
     </button>`
  ).join('') + `<button class="btn btn-sm" onclick="location.href='/pos/caja#promos'"
     style="background:var(--bg-2);border:1px dashed var(--border);padding:.4rem;font-size:.75rem;color:var(--text-3)">
     +<br>Agregar
   </button>`;
}

// ── Búsqueda ──────────────────────────────────────────────────────────────────
function debounceBuscar() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(buscar, 300);
}

async function buscar() {
  const q = document.getElementById('search-input').value.trim();
  if (!q) { renderProductos(todosProductos); return; }
  const r = await fetch(`/api/pos/productos?q=${encodeURIComponent(q)}`);
  const d = await r.json();
  renderProductos(d.productos);
}

function handleSearchKey(e) {
  if (e.key === 'Escape') {
    document.getElementById('search-input').value = '';
    renderProductos(todosProductos);
  }
  if (e.key === 'Enter') {
    const lista = document.querySelectorAll('#lista-productos .prod-row');
    if (lista.length > 0) lista[0].click();
  }
}

function renderProductos(prods) {
  const el = document.getElementById('lista-productos');
  if (!prods.length) { el.innerHTML = '<p style="color:var(--text-3);font-size:.8rem;padding:.5rem">Sin resultados</p>'; return; }
  el.innerHTML = prods.map(p => {
    const promo = promoActivas.find(x => x.producto_id == p.id);
    return `<div class="prod-row" onclick="agregarProducto(${p.id},${JSON.stringify(p.nombre)},${p.precio})"
          style="background:var(--bg-2);border:1px solid var(--border);border-radius:.4rem;padding:.45rem .75rem;
                 margin-bottom:.3rem;display:flex;justify-content:space-between;align-items:center;cursor:pointer">
      <div>
        <span style="font-size:.85rem">${p.nombre}</span>
        ${promo ? `<span style="font-size:.7rem;color:var(--blue);margin-left:.4rem">🏷️ ${promo.nombre}</span>` : ''}
        <span style="font-size:.7rem;color:var(--text-3);margin-left:.5rem">Stock: ${p.stock}</span>
      </div>
      <span style="color:var(--accent);font-weight:600;font-size:.85rem">${fmt(p.precio)}</span>
    </div>`;
  }).join('');
}

// ── Carrito ───────────────────────────────────────────────────────────────────
function agregarProducto(id, nombre, precio) {
  const existing = carrito.find(i => i.producto_id == id);
  if (existing) { existing.cantidad++; }
  else { carrito.push({producto_id: id, nombre, precio_unitario: precio, cantidad: 1}); }
  renderCarrito();
  syncCarrito();
}

function cambiarCantidad(id, delta) {
  const item = carrito.find(i => i.producto_id == id);
  if (!item) return;
  item.cantidad += delta;
  if (item.cantidad <= 0) carrito.splice(carrito.indexOf(item), 1);
  renderCarrito();
  syncCarrito();
}

function renderCarrito() {
  const el       = document.getElementById('carrito-items');
  const subtotal = carrito.reduce((s, i) => s + i.cantidad * i.precio_unitario, 0);
  const { descuento, detalle } = calcDescuento();
  const total    = Math.max(0, subtotal - descuento);

  if (!carrito.length) {
    el.innerHTML = '<p style="color:var(--text-3);font-size:.8rem;text-align:center;margin-top:2rem">Carrito vacío</p>';
  } else {
    el.innerHTML = carrito.map(i => `
      <div style="background:var(--bg-3);border-radius:.4rem;padding:.4rem .5rem">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <span style="font-size:.8rem;flex:1">${i.nombre}</span>
          <button onclick="cambiarCantidad(${i.producto_id},${-(i.cantidad)})"
                  style="background:none;border:none;color:var(--text-3);cursor:pointer;font-size:.9rem;padding:0 2px">✕</button>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:.25rem">
          <div style="display:flex;align-items:center;gap:.25rem">
            <button onclick="cambiarCantidad(${i.producto_id},-1)"
                    style="background:var(--bg-2);border:1px solid var(--border);border-radius:.25rem;width:20px;height:20px;font-size:.8rem;cursor:pointer">-</button>
            <span style="font-size:.85rem;min-width:1.5rem;text-align:center">${i.cantidad}</span>
            <button onclick="cambiarCantidad(${i.producto_id},1)"
                    style="background:var(--bg-2);border:1px solid var(--border);border-radius:.25rem;width:20px;height:20px;font-size:.8rem;cursor:pointer">+</button>
          </div>
          <span style="color:var(--accent);font-size:.85rem;font-weight:600">${fmt(i.cantidad * i.precio_unitario)}</span>
        </div>
      </div>`).join('');
  }

  document.getElementById('subtotal-val').textContent = fmt(subtotal);
  document.getElementById('total-val').textContent    = fmt(total);
  document.getElementById('promos-aplicadas').innerHTML = detalle.map(d =>
    `<div style="font-size:.72rem;color:var(--green);background:rgba(0,200,0,.08);border-radius:.25rem;padding:.2rem .4rem;margin-bottom:.2rem">🏷️ ${d}</div>`
  ).join('');

  document.getElementById('btn-cobrar').disabled = carrito.length === 0 || !metodoPago;
  calcVuelto();
}

function calcDescuento() {
  const today = new Date().toISOString().split('T')[0];
  let descuento = 0;
  const detalle = [];
  const subtotal = carrito.reduce((s, i) => s + i.cantidad * i.precio_unitario, 0);

  for (const p of promoActivas) {
    if (p.fecha_inicio && p.fecha_inicio > today) continue;
    if (p.fecha_fin   && p.fecha_fin   < today) continue;

    if (p.tipo === 'porcentaje') {
      if (p.producto_id) {
        const item = carrito.find(i => i.producto_id == p.producto_id);
        if (item) { const d = Math.round(item.cantidad * item.precio_unitario * p.valor / 100); descuento += d; detalle.push(`${p.nombre}: -${fmt(d)}`); }
      } else { const d = Math.round(subtotal * p.valor / 100); descuento += d; detalle.push(`${p.nombre}: -${fmt(d)}`); }
    } else if (p.tipo === 'fijo') {
      if (p.producto_id) {
        if (carrito.find(i => i.producto_id == p.producto_id)) { descuento += p.valor; detalle.push(`${p.nombre}: -${fmt(p.valor)}`); }
      } else { descuento += p.valor; detalle.push(`${p.nombre}: -${fmt(p.valor)}`); }
    } else if (p.tipo === '2x1' && p.producto_id) {
      const item = carrito.find(i => i.producto_id == p.producto_id);
      if (item && item.cantidad >= 2) {
        const gratis = Math.floor(item.cantidad / 2);
        const d = gratis * item.precio_unitario;
        descuento += d; detalle.push(`${p.nombre}: -${fmt(d)}`);
      }
    }
  }
  return { descuento, detalle };
}

// ── Pago ──────────────────────────────────────────────────────────────────────
function selectPago(tipo) {
  metodoPago = tipo;
  document.getElementById('btn-ef').style.background = tipo === 'efectivo' ? 'var(--primary)' : 'var(--bg-3)';
  document.getElementById('btn-tj').style.background = tipo === 'tarjeta'  ? 'var(--primary)' : 'var(--bg-3)';
  document.getElementById('monto-ef-wrap').style.display = tipo === 'efectivo' ? '' : 'none';
  document.getElementById('btn-cobrar').disabled = carrito.length === 0;
  calcVuelto();
}

function calcVuelto() {
  if (metodoPago !== 'efectivo') return;
  const { descuento } = calcDescuento();
  const subtotal = carrito.reduce((s, i) => s + i.cantidad * i.precio_unitario, 0);
  const total    = Math.max(0, subtotal - descuento);
  const recibido = parseFloat(document.getElementById('monto-recibido').value) || 0;
  const vuelto   = Math.max(0, recibido - total);
  const el       = document.getElementById('vuelto-display');
  el.textContent = recibido > 0 ? `Vuelto: ${fmt(vuelto)}` : '';
}

// ── Cobrar ────────────────────────────────────────────────────────────────────
async function cobrar() {
  if (!carrito.length || !metodoPago) return;
  const { descuento } = calcDescuento();
  const subtotal     = carrito.reduce((s, i) => s + i.cantidad * i.precio_unitario, 0);
  const total        = Math.max(0, subtotal - descuento);
  const monto_ef     = metodoPago === 'efectivo'
    ? (parseFloat(document.getElementById('monto-recibido').value) || 0) : 0;

  document.getElementById('btn-cobrar').disabled = true;
  document.getElementById('btn-cobrar').textContent = 'Procesando…';

  const r = await fetch('/api/pos/venta', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      items:          carrito,
      metodo_pago:    metodoPago,
      monto_efectivo: monto_ef,
      total
    })
  });
  const d = await r.json();

  document.getElementById('btn-cobrar').textContent = 'Cobrar';
  if (!r.ok) { toast(d.error || 'Error al procesar venta', 'error'); document.getElementById('btn-cobrar').disabled = false; return; }

  let boletaHtml = '';
  if (d.boleta && d.boleta.ok) {
    boletaHtml = `<p style="color:var(--green)">✅ Boleta ${d.boleta.numero} emitida
      ${d.boleta.pdf_url ? `<a href="${d.boleta.pdf_url}" target="_blank" style="margin-left:.5rem">Ver PDF</a>` : ''}</p>`;
  } else if (d.boleta) {
    boletaHtml = `<p style="color:var(--yellow)">⚠️ Boleta pendiente (${d.boleta.error || 'DTE no configurado'})</p>`;
  }

  document.getElementById('modal-venta-body').innerHTML = `
    <p><strong>Total cobrado:</strong> ${fmt(d.total)}</p>
    ${d.descuento ? `<p style="color:var(--green)">Descuento aplicado: -${fmt(d.descuento)}</p>` : ''}
    ${metodoPago === 'efectivo' ? `<p><strong>Vuelto:</strong> <span style="font-size:1.4rem;color:var(--accent)">${fmt(d.vuelto)}</span></p>` : ''}
    ${boletaHtml}`;
  document.getElementById('modal-venta').style.display = 'flex';
}

function nuevaVenta() {
  carrito.length = 0;
  metodoPago     = null;
  document.getElementById('modal-venta').style.display  = 'none';
  document.getElementById('monto-recibido').value       = '';
  document.getElementById('btn-ef').style.background    = 'var(--bg-3)';
  document.getElementById('btn-tj').style.background    = 'var(--bg-3)';
  document.getElementById('monto-ef-wrap').style.display = 'none';
  renderCarrito();
  document.getElementById('search-input').focus();
}

// ── Sync carrito a servidor (debounced) ───────────────────────────────────────
let syncTimer = null;
function syncCarrito() {
  clearTimeout(syncTimer);
  syncTimer = setTimeout(async () => {
    const { descuento } = calcDescuento();
    const subtotal = carrito.reduce((s, i) => s + i.cantidad * i.precio_unitario, 0);
    const total    = Math.max(0, subtotal - descuento);
    await fetch('/api/pos/carrito', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ items: carrito, total })
    });
  }, 500);
}

init();
</script>
{% endblock %}
```

- [ ] **Step 10.2: Probar flujo completo en browser**

```
1. Abrir http://127.0.0.1:5000/pos/caja → abrir caja con $30.000
2. Redirige a /pos → buscar "Marraqueta" → hacer clic → aparece en carrito
3. Hacer clic en "Efectivo" → ingresar 1000 → ver vuelto
4. Hacer clic en "Cobrar" → debe aparecer modal con resultado
5. En otra pestaña abrir http://127.0.0.1:5000/pos/cliente → debe actualizar en tiempo real
```

- [ ] **Step 10.3: Commit**

```bash
git add templates/pos.html
git commit -m "feat(pos): template pos.html — pantalla cajero layout híbrido"
```

---

## Task 11: Sidebar + DTE config + wiring final

**Files:**
- Modify: `templates/base.html`
- Modify: `templates/crm_configuracion.html`

- [ ] **Step 11.1: Agregar POS en sidebar de base.html**

En `templates/base.html`, insertar después de la línea de Ventas (línea 18):

```html
    <a href="/pos"           class="nav-item {% if active=='pos'           %}active{% endif %}"><i class="bi bi-cash-register"></i> POS / Caja</a>
```

- [ ] **Step 11.2: Agregar sección DTE en crm_configuracion.html**

Buscar en `templates/crm_configuracion.html` la sección de configuración SMTP y agregar antes o después:

```html
<!-- Sección DTE / Boleta Electrónica -->
<div class="card mb-3">
  <div class="card-header"><h4><i class="bi bi-receipt"></i> DTE / Boleta Electrónica (Bsale)</h4></div>
  <div class="card-body">
    <div class="form-group mb-2">
      <label>Bsale API Token</label>
      <input type="password" id="bsale-token" class="form-control" placeholder="Token de Bsale">
    </div>
    <div class="form-group mb-2">
      <label>ID Tipo Documento (boleta = 39)</label>
      <input type="number" id="bsale-doc-type" class="form-control" value="39">
    </div>
    <div class="form-group mb-3">
      <label>ID Lista de Precios (default = 1)</label>
      <input type="number" id="bsale-price-list" class="form-control" value="1">
    </div>
    <button class="btn btn-primary" onclick="guardarDTE()">Guardar configuración DTE</button>
    <span id="dte-status" style="margin-left:.75rem;font-size:.85rem"></span>
  </div>
</div>

<script>
async function cargarConfigDTE() {
  const r = await fetch('/api/crm/config/status');
  const d = await r.json();
  if (d.bsale_token) document.getElementById('bsale-token').value     = '••••••••';
  if (d.bsale_document_type_id) document.getElementById('bsale-doc-type').value   = d.bsale_document_type_id;
  if (d.bsale_price_list_id)    document.getElementById('bsale-price-list').value  = d.bsale_price_list_id;
}

async function guardarDTE() {
  const token = document.getElementById('bsale-token').value.trim();
  const body  = {
    bsale_document_type_id: parseInt(document.getElementById('bsale-doc-type').value),
    bsale_price_list_id:    parseInt(document.getElementById('bsale-price-list').value),
  };
  if (token && !token.startsWith('•')) body.bsale_token = token;

  const r = await fetch('/api/pos/config/dte', {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
  });
  const d = await r.json();
  document.getElementById('dte-status').textContent = d.ok ? '✅ Guardado' : '❌ Error';
}

cargarConfigDTE();
</script>
```

- [ ] **Step 11.3: Agregar ruta /api/pos/config/dte en pos.py**

```python
@pos_bp.route('/api/pos/config/dte', methods=['POST'])
@login_required
def api_pos_config_dte():
    d = request.json
    update = {}
    if d.get('bsale_token'):           update['bsale_token']            = d['bsale_token']
    if d.get('bsale_document_type_id'): update['bsale_document_type_id'] = d['bsale_document_type_id']
    if d.get('bsale_price_list_id'):    update['bsale_price_list_id']    = d['bsale_price_list_id']
    _save_config(update)
    return jsonify({'ok': True})
```

- [ ] **Step 11.4: Ejecutar todos los tests**

```bash
venv\Scripts\python.exe -m pytest tests/ -v
```

Resultado esperado: todos los tests pasan.

- [ ] **Step 11.5: Probar flujo completo en browser**

```
1. Ir a /crm/configuracion → ingresar Bsale token → guardar
2. Ir a /pos/caja → abrir caja
3. Ir a /pos → agregar productos → cobrar → confirmar boleta
4. Ir a /pos/caja → ver resumen y cerrar caja
5. Verificar que la venta aparece en /ventas y /reportes
```

- [ ] **Step 11.6: Commit final**

```bash
git add templates/base.html templates/crm_configuracion.html pos.py
git commit -m "feat(pos): sidebar POS, DTE config, wiring final — módulo completo"
```

---

## Self-review

**Spec coverage:**
- ✅ Blueprint pos.py + dte.py separado → Tasks 1-2
- ✅ 4 tablas DB → Task 1
- ✅ 14 rutas API → Tasks 3-7, 11
- ✅ Flujo de venta → Task 6
- ✅ Apertura/cierre de caja con arqueo → Task 3, 8
- ✅ Pantalla cliente Aurora branded → Task 9
- ✅ Carrito sync (POST /api/pos/carrito) → Task 7
- ✅ Búsqueda + frecuentes + carrito → Task 10
- ✅ Promociones (%, fijo, 2x1) → Task 5, 10
- ✅ Bsale DTE → Task 2, 6, 11
- ✅ Sidebar + DTE config → Task 11
- ✅ Restricción turno requerido → Tasks 3, 6
- ✅ Venta falla gracefully si Bsale no responde → Task 6

**Tipos consistentes:**
- `_aplicar_promociones` definida en Task 5, usada en Task 6 ✅
- `_pos_carrito_activo` definido en Task 1, actualizado en Tasks 6 y 7 ✅
- `emit_boleta(items, total, config)` definida en Task 2, llamada en Task 6 ✅
