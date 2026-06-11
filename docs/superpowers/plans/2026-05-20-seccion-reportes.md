# Sección Reportes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Crear sección REPORTES en el sidebar con página Resumen de KPIs, reubicar reportes existentes, y agregar reporte de producción y despacho. Eliminar Dashboard (movil).

**Architecture:** Nuevas rutas en `app.py` bajo el prefijo `/reportes/*`. La página Resumen llama a un endpoint `/api/reportes/resumen?periodo=` que agrega datos de ventas, producción, despacho y finanzas en una sola respuesta. Los reportes de producción y despacho tienen sus propios endpoints. El nav en `base.html` se reorganiza en sección REPORTES.

**Tech Stack:** Flask, SQLite (WAL), Jinja2, Chart.js 4.4, Bootstrap Icons 1.11

---

## Files

| Acción | Archivo | Responsabilidad |
|--------|---------|-----------------|
| Modify | `app.py` | Nuevas rutas, API endpoints, MODULOS actualizado |
| Modify | `templates/base.html` | Nav reestructurado |
| Create | `templates/reportes_resumen.html` | Página resumen KPIs |
| Create | `templates/reporte_produccion.html` | Reporte producción |
| Create | `templates/reporte_despacho.html` | Reporte despacho |
| Create | `tests/test_reportes.py` | Tests endpoints nuevos |

---

## Task 1: Actualizar MODULOS, rutas de página y redirigir `/reportes`

**Files:**
- Modify: `app.py` (sección MODULOS ~línea 1097, rutas ~línea 1320)

- [ ] **Step 1: Agregar módulos nuevos a la lista MODULOS**

Reemplazar en `app.py` el bloque MODULOS (línea ~1097):

```python
MODULOS = [
    ('pos',              'POS / Caja',          'bi-cash-register'),
    ('ventas',           'Ventas',              'bi-receipt'),
    ('despacho',         'Despacho',            'bi-truck'),
    ('clientes',         'Clientes',            'bi-people'),
    ('productos',        'Productos',           'bi-basket'),
    ('suscripciones',    'Suscripciones',       'bi-calendar-check'),
    ('mayoristas',       'Mayoristas',          'bi-shop'),
    ('crm',              'CRM',                 'bi-diagram-3'),
    ('reportes',         'Resumen Reportes',    'bi-speedometer2'),
    ('reporte_ventas',   'Reporte Ventas',      'bi-table'),
    ('reporte_produccion','Reporte Producción', 'bi-clipboard-data'),
    ('reporte_despacho', 'Reporte Despacho',    'bi-map'),
    ('finanzas',         'Finanzas',            'bi-currency-dollar'),
    ('produccion',       'Producción',          'bi-fire'),
    ('inventario',       'Inventario',          'bi-boxes'),
    ('gastos',           'Gastos',              'bi-cash-coin'),
    ('agenda',           'Agenda',              'bi-calendar3'),
    ('config_negocio',   'Configuración',       'bi-gear'),
    ('agentes',          'Agentes',             'bi-robot'),
]
```

- [ ] **Step 2: Cambiar ruta `/reportes` a resumen y agregar `/reportes/financiero`**

Localizar en `app.py` (~línea 1320) las rutas de reportes y reemplazar:

```python
# ANTES:
@app.route('/reportes')
@module_required('reportes')
def page_reportes():      return render_template('reportes.html',       active='reportes')
```

Por:

```python
@app.route('/reportes')
@module_required('reportes')
def page_reportes():
    return render_template('reportes_resumen.html', active='reportes')

@app.route('/reportes/financiero')
@module_required('reportes')
def page_reportes_financiero():
    return render_template('reportes.html', active='reportes_financiero')

@app.route('/reportes/produccion')
@module_required('reporte_produccion')
def page_reportes_produccion():
    return render_template('reporte_produccion.html', active='reporte_produccion')

@app.route('/reportes/despacho')
@module_required('reporte_despacho')
def page_reportes_despacho():
    return render_template('reporte_despacho.html', active='reporte_despacho')
```

- [ ] **Step 3: Verificar sintaxis**

```
python -m py_compile app.py && echo OK
```

Esperado: `OK`

- [ ] **Step 4: Commit**

```
git add app.py
git commit -m "feat: agregar rutas /reportes/financiero, /reportes/produccion, /reportes/despacho"
```

---

## Task 2: Endpoint `/api/reportes/resumen`

**Files:**
- Modify: `app.py` (agregar después del bloque de `/api/reportes/kpis` ~línea 2390)
- Create: `tests/test_reportes.py`

- [ ] **Step 1: Escribir test**

```python
# tests/test_reportes.py
import pytest
from werkzeug.security import generate_password_hash


def login_admin(tc, app_mod):
    with app_mod.db() as c:
        c.execute(
            "INSERT OR IGNORE INTO usuarios (nombre,email,password,rol) VALUES (?,?,?,?)",
            ('Admin', 'admin@test.cl', generate_password_hash('test123'), 'admin')
        )
    tc.post('/login', data={'email': 'admin@test.cl', 'password': 'test123'},
            follow_redirects=True)


def test_resumen_hoy(client):
    tc, app_mod = client
    login_admin(tc, app_mod)
    r = tc.get('/api/reportes/resumen?periodo=hoy')
    assert r.status_code == 200
    data = r.get_json()
    assert 'ventas_hoy' in data
    assert 'por_canal' in data
    assert 'ingresos_cobrados' in data
    assert 'gastos' in data
    assert 'por_cobrar' in data
    assert 'despachos_pendientes_hoy' in data
    assert 'stock_bajo' in data


def test_resumen_semana(client):
    tc, app_mod = client
    login_admin(tc, app_mod)
    r = tc.get('/api/reportes/resumen?periodo=semana')
    assert r.status_code == 200
    data = r.get_json()
    assert 'ventas_periodo' in data


def test_resumen_mes(client):
    tc, app_mod = client
    login_admin(tc, app_mod)
    r = tc.get('/api/reportes/resumen?periodo=mes')
    assert r.status_code == 200
```

- [ ] **Step 2: Correr test para verificar que falla**

```
python -m pytest tests/test_reportes.py -v
```

Esperado: FAIL — `404` en `/api/reportes/resumen`

- [ ] **Step 3: Implementar endpoint en `app.py`**

Agregar después de `api_rep_kpis` (~línea 2420):

```python
@app.route('/api/reportes/resumen')
@login_required
def api_reportes_resumen():
    """KPIs agregados para la página Resumen de Reportes."""
    periodo = request.args.get('periodo', 'mes')
    hoy = date.today()

    def rango(p):
        if p == 'hoy':
            return hoy.isoformat(), hoy.isoformat()
        if p == 'semana':
            lun = hoy - timedelta(days=hoy.weekday())
            return lun.isoformat(), hoy.isoformat()
        # mes
        return hoy.replace(day=1).isoformat(), hoy.isoformat()

    desde, hasta = rango(periodo)
    ayer = (hoy - timedelta(days=1)).isoformat()

    # rango período anterior (igual duración)
    d0 = date.fromisoformat(desde)
    delta = (hoy - d0).days or 1
    prev_hasta = (d0 - timedelta(days=1)).isoformat()
    prev_desde = (d0 - timedelta(days=delta)).isoformat()

    with db() as c:
        # Ventas hoy
        ventas_hoy = c.execute(
            "SELECT COALESCE(SUM(total),0) FROM ventas WHERE fecha=?", (hoy.isoformat(),)
        ).fetchone()[0]
        ventas_ayer = c.execute(
            "SELECT COALESCE(SUM(total),0) FROM ventas WHERE fecha=?", (ayer,)
        ).fetchone()[0]

        # Ventas período
        ventas_periodo = c.execute(
            "SELECT COALESCE(SUM(total),0) FROM ventas WHERE fecha BETWEEN ? AND ?",
            (desde, hasta)
        ).fetchone()[0]
        ventas_prev = c.execute(
            "SELECT COALESCE(SUM(total),0) FROM ventas WHERE fecha BETWEEN ? AND ?",
            (prev_desde, prev_hasta)
        ).fetchone()[0]

        count_periodo = c.execute(
            "SELECT COUNT(*) FROM ventas WHERE fecha BETWEEN ? AND ?",
            (desde, hasta)
        ).fetchone()[0]
        ticket = ventas_periodo / count_periodo if count_periodo else 0

        # Suscripciones activas
        subs = c.execute(
            "SELECT COUNT(*) FROM suscripciones WHERE estado='activo'"
        ).fetchone()[0]

        # Por canal (segmentado: mayorista, suscripcion, pos, online)
        canal_rows = c.execute("""
            SELECT
              CASE
                WHEN tipo_cliente='MAYORISTA' THEN 'mayorista'
                WHEN canal='suscripcion'      THEN 'suscripcion'
                WHEN canal='local'            THEN 'pos'
                ELSE 'online'
              END as segmento,
              COUNT(*) as count,
              COALESCE(SUM(total),0) as total
            FROM ventas
            WHERE fecha BETWEEN ? AND ?
            GROUP BY segmento
        """, (desde, hasta)).fetchall()
        por_canal = [dict(r) for r in canal_rows]

        # Finanzas: ingresos cobrados y gastos en el período
        ingresos = c.execute(
            "SELECT COALESCE(SUM(total),0) FROM ventas WHERE fecha BETWEEN ? AND ? AND estado_pago='PAGADO'",
            (desde, hasta)
        ).fetchone()[0]
        gastos = c.execute(
            "SELECT COALESCE(SUM(monto),0) FROM gastos WHERE fecha BETWEEN ? AND ?",
            (desde, hasta)
        ).fetchone()[0]

        # Por cobrar total (todas las ventas pendientes, no solo el período)
        por_cobrar = c.execute(
            "SELECT COALESCE(SUM(total),0) FROM ventas WHERE estado_pago='PENDIENTE'"
        ).fetchone()[0]

        # Producción: unidades planificadas hoy
        prod_hoy = c.execute(
            "SELECT COALESCE(SUM(cantidad),0) FROM plan_produccion WHERE fecha=?",
            (hoy.isoformat(),)
        ).fetchone()[0]

        # Despachos pendientes hoy
        despachos_pendientes = c.execute(
            """SELECT COUNT(*) FROM ventas
               WHERE fecha_despacho=? AND con_despacho=1
                 AND estado_despacho NOT IN ('DESPACHADO','RETIRO EN TIENDA')""",
            (hoy.isoformat(),)
        ).fetchone()[0]

        # Stock bajo (productos activos con stock <= 0 o muy bajo)
        stock_bajo = c.execute(
            """SELECT nombre, stock FROM productos
               WHERE activo=1 AND stock <= 5
               ORDER BY stock ASC LIMIT 10"""
        ).fetchall()

    return jsonify({
        'periodo': periodo,
        'desde': desde,
        'hasta': hasta,
        'ventas_hoy': ventas_hoy,
        'ventas_ayer': ventas_ayer,
        'ventas_periodo': ventas_periodo,
        'ventas_prev': ventas_prev,
        'count_periodo': count_periodo,
        'ticket': ticket,
        'suscripciones_activas': subs,
        'por_canal': por_canal,
        'ingresos_cobrados': ingresos,
        'gastos': gastos,
        'resultado': ingresos - gastos,
        'por_cobrar': por_cobrar,
        'prod_planificadas_hoy': prod_hoy,
        'despachos_pendientes_hoy': despachos_pendientes,
        'stock_bajo': [{'nombre': r['nombre'], 'stock': r['stock']} for r in stock_bajo],
    })
```

- [ ] **Step 4: Correr tests**

```
python -m pytest tests/test_reportes.py -v
```

Esperado: 3 tests PASS

- [ ] **Step 5: Commit**

```
git add app.py tests/test_reportes.py
git commit -m "feat: agregar /api/reportes/resumen con KPIs agregados"
```

---

## Task 3: Endpoint `/api/reportes/produccion`

**Files:**
- Modify: `app.py`
- Modify: `tests/test_reportes.py`

- [ ] **Step 1: Agregar test**

Añadir al final de `tests/test_reportes.py`:

```python
def test_reporte_produccion(client):
    tc, app_mod = client
    login_admin(tc, app_mod)
    r = tc.get('/api/reportes/produccion?periodo=mes')
    assert r.status_code == 200
    data = r.get_json()
    assert 'kpis' in data
    assert 'por_dia' in data
    assert 'por_producto' in data
    assert 'total_planificado' in data['kpis']
    assert 'total_producido' in data['kpis']
```

- [ ] **Step 2: Verificar que falla**

```
python -m pytest tests/test_reportes.py::test_reporte_produccion -v
```

Esperado: FAIL — 404

- [ ] **Step 3: Implementar endpoint en `app.py`**

Agregar después de `api_reportes_resumen`:

```python
@app.route('/api/reportes/produccion')
@login_required
def api_reportes_produccion():
    """Reporte de producción por período."""
    periodo = request.args.get('periodo', 'mes')
    hoy = date.today()

    if periodo == 'semana':
        lun = hoy - timedelta(days=hoy.weekday())
        desde, hasta = lun.isoformat(), hoy.isoformat()
    elif periodo == '3meses':
        desde = (hoy.replace(day=1) - timedelta(days=60)).isoformat()
        hasta = hoy.isoformat()
    else:  # mes
        desde, hasta = hoy.replace(day=1).isoformat(), hoy.isoformat()

    with db() as c:
        # KPIs globales
        rows = c.execute(
            """SELECT estado, SUM(cantidad) as total
               FROM plan_produccion
               WHERE fecha BETWEEN ? AND ?
               GROUP BY estado""",
            (desde, hasta)
        ).fetchall()
        est = {r['estado']: r['total'] for r in rows}
        total_planificado = sum(est.values())
        total_producido   = est.get('horneado', 0) + est.get('listo', 0)
        cumplimiento = round(total_producido / total_planificado * 100, 1) if total_planificado else 0

        # Por día
        dias = c.execute(
            """SELECT fecha,
                      SUM(cantidad) as planificado,
                      SUM(CASE WHEN estado IN ('horneado','listo') THEN cantidad ELSE 0 END) as producido
               FROM plan_produccion
               WHERE fecha BETWEEN ? AND ?
               GROUP BY fecha ORDER BY fecha""",
            (desde, hasta)
        ).fetchall()

        # Por producto
        prods = c.execute(
            """SELECT nombre_producto,
                      SUM(cantidad) as planificado,
                      SUM(CASE WHEN estado IN ('horneado','listo') THEN cantidad ELSE 0 END) as producido
               FROM plan_produccion
               WHERE fecha BETWEEN ? AND ?
               GROUP BY nombre_producto
               ORDER BY planificado DESC""",
            (desde, hasta)
        ).fetchall()

    return jsonify({
        'periodo': periodo,
        'desde': desde,
        'hasta': hasta,
        'kpis': {
            'total_planificado': total_planificado,
            'total_producido':   total_producido,
            'cumplimiento_pct':  cumplimiento,
            'producto_top':      prods[0]['nombre_producto'] if prods else None,
        },
        'por_dia':      [dict(r) for r in dias],
        'por_producto': [dict(r) for r in prods],
    })
```

- [ ] **Step 4: Correr tests**

```
python -m pytest tests/test_reportes.py -v
```

Esperado: 4 tests PASS

- [ ] **Step 5: Commit**

```
git add app.py tests/test_reportes.py
git commit -m "feat: agregar /api/reportes/produccion"
```

---

## Task 4: Endpoint `/api/reportes/despacho`

**Files:**
- Modify: `app.py`
- Modify: `tests/test_reportes.py`

- [ ] **Step 1: Agregar test**

Añadir al final de `tests/test_reportes.py`:

```python
def test_reporte_despacho(client):
    tc, app_mod = client
    login_admin(tc, app_mod)
    r = tc.get('/api/reportes/despacho?periodo=mes')
    assert r.status_code == 200
    data = r.get_json()
    assert 'kpis' in data
    assert 'por_dia' in data
    assert 'por_canal' in data
    assert 'total' in data['kpis']
```

- [ ] **Step 2: Verificar que falla**

```
python -m pytest tests/test_reportes.py::test_reporte_despacho -v
```

Esperado: FAIL — 404

- [ ] **Step 3: Implementar endpoint en `app.py`**

Agregar después de `api_reportes_produccion`:

```python
@app.route('/api/reportes/despacho')
@login_required
def api_reportes_despacho():
    """Reporte de despacho por período."""
    periodo = request.args.get('periodo', 'mes')
    hoy = date.today()

    if periodo == 'semana':
        lun = hoy - timedelta(days=hoy.weekday())
        desde, hasta = lun.isoformat(), hoy.isoformat()
    elif periodo == '3meses':
        desde = (hoy.replace(day=1) - timedelta(days=60)).isoformat()
        hasta = hoy.isoformat()
    else:  # mes
        desde, hasta = hoy.replace(day=1).isoformat(), hoy.isoformat()

    with db() as c:
        rows = c.execute(
            """SELECT estado_despacho, COUNT(*) as count
               FROM ventas
               WHERE con_despacho=1 AND fecha_despacho BETWEEN ? AND ?
               GROUP BY estado_despacho""",
            (desde, hasta)
        ).fetchall()
        estados = {r['estado_despacho']: r['count'] for r in rows}
        total = sum(estados.values())
        despachados = estados.get('DESPACHADO', 0) + estados.get('RETIRO EN TIENDA', 0)
        pendientes  = estados.get('PENDIENTE', 0)
        tasa = round(despachados / total * 100, 1) if total else 0

        por_dia = c.execute(
            """SELECT fecha_despacho as fecha,
                      COUNT(*) as total,
                      SUM(CASE WHEN estado_despacho='DESPACHADO' THEN 1 ELSE 0 END) as despachados,
                      SUM(CASE WHEN estado_despacho='PENDIENTE' THEN 1 ELSE 0 END) as pendientes,
                      SUM(CASE WHEN estado_despacho='EN PREPARACION' THEN 1 ELSE 0 END) as preparacion,
                      SUM(CASE WHEN estado_despacho='RETIRO EN TIENDA' THEN 1 ELSE 0 END) as retiro
               FROM ventas
               WHERE con_despacho=1 AND fecha_despacho BETWEEN ? AND ?
               GROUP BY fecha_despacho ORDER BY fecha_despacho""",
            (desde, hasta)
        ).fetchall()

        por_canal = c.execute(
            """SELECT
                 CASE
                   WHEN tipo_cliente='MAYORISTA' THEN 'Mayorista'
                   WHEN canal='suscripcion'      THEN 'Suscripción'
                   WHEN canal='local'            THEN 'POS'
                   ELSE 'Online'
                 END as segmento,
                 COUNT(*) as total,
                 SUM(CASE WHEN estado_despacho='DESPACHADO' THEN 1 ELSE 0 END) as despachados
               FROM ventas
               WHERE con_despacho=1 AND fecha_despacho BETWEEN ? AND ?
               GROUP BY segmento ORDER BY total DESC""",
            (desde, hasta)
        ).fetchall()

    return jsonify({
        'periodo': periodo,
        'desde': desde,
        'hasta': hasta,
        'kpis': {
            'total':       total,
            'despachados': despachados,
            'pendientes':  pendientes,
            'tasa_pct':    tasa,
        },
        'por_estado': [dict(r) for r in rows],
        'por_dia':    [dict(r) for r in por_dia],
        'por_canal':  [dict(r) for r in por_canal],
    })
```

- [ ] **Step 4: Correr tests**

```
python -m pytest tests/test_reportes.py -v
```

Esperado: 5 tests PASS

- [ ] **Step 5: Commit**

```
git add app.py tests/test_reportes.py
git commit -m "feat: agregar /api/reportes/despacho"
```

---

## Task 5: Template `reportes_resumen.html`

**Files:**
- Create: `templates/reportes_resumen.html`

- [ ] **Step 1: Crear el template**

```html
{% extends "base.html" %}
{% block title %}Resumen — Aurora Bakers{% endblock %}
{% block page_title %}Resumen{% endblock %}
{% block content %}

<!-- Toggle período -->
<div class="toolbar mb-24">
  <div class="filter-tabs" id="periodo-tabs">
    <button class="filter-tab" onclick="setPeriodo(this,'hoy')">Hoy</button>
    <button class="filter-tab active" onclick="setPeriodo(this,'semana')">Semana</button>
    <button class="filter-tab" onclick="setPeriodo(this,'mes')">Mes</button>
  </div>
</div>

<!-- Fila 1: KPIs ventas -->
<div class="kpi-grid mb-24" id="kpi-ventas"></div>

<!-- Fila 2: Por canal -->
<div class="kpi-grid mb-24" id="kpi-canales"></div>

<!-- Fila 3: Finanzas -->
<div class="kpi-grid mb-24" id="kpi-finanzas"></div>

<!-- Fila 4: Operaciones -->
<div class="kpi-grid mb-24" id="kpi-ops"></div>

<!-- Alertas stock bajo -->
<div id="alerta-stock" style="display:none" class="mb-24">
  <div class="card">
    <div class="card-title" style="color:var(--warning)">
      <i class="bi bi-exclamation-triangle-fill"></i> Stock bajo
    </div>
    <div id="stock-lista"></div>
  </div>
</div>

{% endblock %}

{% block scripts %}
<script>
let periodo = 'semana';

const CANAL_LABEL = { mayorista:'Mayorista', suscripcion:'Suscripción', pos:'POS / Local', online:'Online' };
const CANAL_ICON  = { mayorista:'bi-shop', suscripcion:'bi-calendar-check', pos:'bi-cash-register', online:'bi-globe' };
const CANAL_COLOR = { mayorista:'#6aade8', suscripcion:'#4ab870', pos:'#C8312E', online:'#a855f7' };

function setPeriodo(btn, v) {
  document.querySelectorAll('#periodo-tabs .filter-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  periodo = v;
  loadAll();
}

function pct(curr, prev) {
  if (!prev) return '';
  const p = ((curr - prev) / prev * 100).toFixed(1);
  const up = curr >= prev;
  return `<span class="${up ? 'up' : 'down'}">${up ? '▲' : '▼'} ${Math.abs(p)}%</span>`;
}

function kpiCard(icon, label, value, sub, color) {
  return `<div class="kpi-card">
    <div class="kpi-label"><i class="bi ${icon}" style="color:${color || 'var(--accent)'}"></i> ${label}</div>
    <div class="kpi-value">${value}</div>
    ${sub ? `<div class="kpi-sub">${sub}</div>` : ''}
  </div>`;
}

async function loadAll() {
  const d = await api('GET', `/api/reportes/resumen?periodo=${periodo}`);

  // Fila 1: KPIs ventas
  const periodoLabel = { hoy:'vs ayer', semana:'vs semana anterior', mes:'vs mes anterior' }[periodo] || '';
  document.getElementById('kpi-ventas').innerHTML = [
    kpiCard('bi-currency-dollar', 'Ventas hoy',       clp(d.ventas_hoy),       pct(d.ventas_hoy, d.ventas_ayer), '#C8312E'),
    kpiCard('bi-graph-up',        'Ventas período',   clp(d.ventas_periodo),   pct(d.ventas_periodo, d.ventas_prev) + ' ' + periodoLabel, '#C8956B'),
    kpiCard('bi-calculator',      'Ticket promedio',  clp(d.ticket),           `${d.count_periodo} ventas`, '#e0a832'),
    kpiCard('bi-calendar-check',  'Suscripciones',    d.suscripciones_activas, 'activas', '#4ab870'),
  ].join('');

  // Fila 2: Por canal
  const totalCanal = d.por_canal.reduce((s, c) => s + c.total, 0) || 1;
  const canalesOrden = ['pos', 'online', 'suscripcion', 'mayorista'];
  const canalMap = Object.fromEntries(d.por_canal.map(c => [c.segmento, c]));
  document.getElementById('kpi-canales').innerHTML = canalesOrden.map(seg => {
    const c = canalMap[seg] || { total: 0, count: 0 };
    const pctSeg = (c.total / totalCanal * 100).toFixed(0);
    return kpiCard(
      CANAL_ICON[seg],
      CANAL_LABEL[seg],
      clp(c.total),
      `${c.count || 0} ventas · ${pctSeg}%`,
      CANAL_COLOR[seg]
    );
  }).join('');

  // Fila 3: Finanzas
  const resColor = d.resultado >= 0 ? '#4ab870' : '#e85555';
  document.getElementById('kpi-finanzas').innerHTML = [
    kpiCard('bi-check-circle',  'Ingresos cobrados', clp(d.ingresos_cobrados), 'en el período', '#4ab870'),
    kpiCard('bi-cash-coin',     'Gastos',            clp(d.gastos),           'en el período', '#e85555'),
    kpiCard('bi-bar-chart-line','Resultado',         clp(d.resultado),         d.resultado >= 0 ? 'positivo' : 'negativo', resColor),
    kpiCard('bi-clock-history', 'Por cobrar',        clp(d.por_cobrar),       'total pendiente', '#e0a832'),
  ].join('');

  // Fila 4: Operaciones
  document.getElementById('kpi-ops').innerHTML = [
    kpiCard('bi-fire',           'Unidades plan hoy',       d.prod_planificadas_hoy,   'en plan de producción', '#e0a832'),
    kpiCard('bi-truck',          'Despachos pendientes',    d.despachos_pendientes_hoy,'para hoy', d.despachos_pendientes_hoy > 0 ? '#e85555' : '#4ab870'),
  ].join('');

  // Stock bajo
  if (d.stock_bajo && d.stock_bajo.length) {
    document.getElementById('alerta-stock').style.display = '';
    document.getElementById('stock-lista').innerHTML = d.stock_bajo.map(p =>
      `<div class="flex-between mb-8">
        <span><i class="bi bi-basket"></i> ${p.nombre}</span>
        <span style="color:var(--warning);font-weight:600">${p.stock} und</span>
      </div>`
    ).join('');
  } else {
    document.getElementById('alerta-stock').style.display = 'none';
  }
}

loadAll();
</script>
{% endblock %}
```

- [ ] **Step 2: Verificar compilación Python (templates no requieren py_compile)**

Arrancar Flask y navegar a `http://127.0.0.1:5000/reportes`. Verificar que carga sin errores 500.

- [ ] **Step 3: Commit**

```
git add templates/reportes_resumen.html
git commit -m "feat: template reportes_resumen.html con KPIs por canal y finanzas"
```

---

## Task 6: Template `reporte_produccion.html`

**Files:**
- Create: `templates/reporte_produccion.html`

- [ ] **Step 1: Crear el template**

```html
{% extends "base.html" %}
{% block title %}Reporte Producción — Aurora Bakers{% endblock %}
{% block page_title %}Reporte Producción{% endblock %}
{% block content %}

<!-- Toggle período -->
<div class="toolbar mb-24">
  <div class="filter-tabs" id="periodo-tabs">
    <button class="filter-tab active" onclick="setPeriodo(this,'mes')">Mes</button>
    <button class="filter-tab" onclick="setPeriodo(this,'semana')">Semana</button>
    <button class="filter-tab" onclick="setPeriodo(this,'3meses')">3 meses</button>
  </div>
</div>

<!-- KPIs -->
<div class="kpi-grid mb-24" id="kpi-row"></div>

<!-- Gráfico: kg por día -->
<div class="card mb-24">
  <div class="card-title"><i class="bi bi-graph-up"></i> Unidades por día</div>
  <div class="chart-wrap"><canvas id="chart-dias"></canvas></div>
</div>

<!-- Tabla por producto -->
<div class="card">
  <div class="card-title"><i class="bi bi-table"></i> Por producto</div>
  <div style="overflow-x:auto">
    <table class="table" style="margin:0">
      <thead><tr>
        <th>Producto</th>
        <th class="right">Planificado</th>
        <th class="right">Producido</th>
        <th class="right">Cumplimiento</th>
      </tr></thead>
      <tbody id="tabla-prods"></tbody>
    </table>
  </div>
</div>

{% endblock %}
{% block scripts %}
<script>
let periodo = 'mes';
let chartDias;

const COLORS = { accent:'#C8312E', success:'#4ab870', warning:'#e0a832', info:'#6aade8' };

function setPeriodo(btn, v) {
  document.querySelectorAll('#periodo-tabs .filter-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  periodo = v;
  loadAll();
}

async function loadAll() {
  const d = await api('GET', `/api/reportes/produccion?periodo=${periodo}`);
  const k = d.kpis;

  // KPIs
  document.getElementById('kpi-row').innerHTML = [
    { icon:'bi-list-check',      label:'Planificado',   val: k.total_planificado + ' und', color:'#6aade8' },
    { icon:'bi-check2-all',      label:'Producido',     val: k.total_producido + ' und',  color:COLORS.success },
    { icon:'bi-percent',         label:'Cumplimiento',  val: k.cumplimiento_pct + '%',     color: k.cumplimiento_pct >= 80 ? COLORS.success : COLORS.warning },
    { icon:'bi-trophy',          label:'Top producto',  val: k.producto_top || '—',        color:COLORS.accent },
  ].map(x => `<div class="kpi-card">
    <div class="kpi-label"><i class="bi ${x.icon}" style="color:${x.color}"></i> ${x.label}</div>
    <div class="kpi-value" style="font-size:1.1rem">${x.val}</div>
  </div>`).join('');

  // Gráfico línea por día
  const labels = d.por_dia.map(r => {
    const dt = new Date(r.fecha + 'T12:00:00');
    return dt.toLocaleDateString('es-CL', {day:'numeric', month:'short'});
  });
  if (chartDias) chartDias.destroy();
  chartDias = new Chart(document.getElementById('chart-dias'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label:'Planificado', data: d.por_dia.map(r => r.planificado),
          backgroundColor: COLORS.info + '66', borderColor: COLORS.info, borderWidth:1, borderRadius:3 },
        { label:'Producido',   data: d.por_dia.map(r => r.producido),
          backgroundColor: COLORS.success + '99', borderColor: COLORS.success, borderWidth:1, borderRadius:3 },
      ]
    },
    options: {
      responsive:true, maintainAspectRatio:false,
      plugins: { legend:{ display:true, position:'top', labels:{ color:'#c9d1d9', font:{size:11} } } },
      scales: {
        x: { grid:{ color:'rgba(48,54,61,.6)' }, ticks:{ color:'#8b949e', font:{size:11} } },
        y: { grid:{ color:'rgba(48,54,61,.6)' }, ticks:{ color:'#8b949e', font:{size:11} },
             title:{ display:true, text:'Unidades', color:'#8b949e', font:{size:11} } }
      }
    }
  });

  // Tabla por producto
  document.getElementById('tabla-prods').innerHTML = d.por_producto.map(p => {
    const cum = p.planificado ? Math.round(p.producido / p.planificado * 100) : 0;
    const color = cum >= 80 ? COLORS.success : cum >= 50 ? COLORS.warning : COLORS.accent;
    return `<tr>
      <td>${p.nombre_producto}</td>
      <td class="right">${p.planificado}</td>
      <td class="right">${p.producido}</td>
      <td class="right" style="color:${color};font-weight:600">${cum}%</td>
    </tr>`;
  }).join('') || `<tr><td colspan="4" style="text-align:center;padding:2rem;color:var(--muted)">Sin datos</td></tr>`;
}

loadAll();
</script>
<style>
.right { text-align:right !important; }
.table th { text-align:left;font-size:.7rem;font-weight:700;text-transform:uppercase;
  letter-spacing:.05em;color:var(--muted);padding:.5rem .75rem;border-bottom:2px solid var(--border); }
.table td { padding:.6rem .75rem;border-bottom:1px solid var(--border);color:var(--text);vertical-align:middle; }
.table tr:last-child td { border-bottom:none; }
.table tr:hover td { background:var(--hover,rgba(0,0,0,.03)); }
</style>
{% endblock %}
```

- [ ] **Step 2: Commit**

```
git add templates/reporte_produccion.html
git commit -m "feat: template reporte_produccion.html"
```

---

## Task 7: Template `reporte_despacho.html`

**Files:**
- Create: `templates/reporte_despacho.html`

- [ ] **Step 1: Crear el template**

```html
{% extends "base.html" %}
{% block title %}Reporte Despacho — Aurora Bakers{% endblock %}
{% block page_title %}Reporte Despacho{% endblock %}
{% block content %}

<!-- Toggle período -->
<div class="toolbar mb-24">
  <div class="filter-tabs" id="periodo-tabs">
    <button class="filter-tab active" onclick="setPeriodo(this,'mes')">Mes</button>
    <button class="filter-tab" onclick="setPeriodo(this,'semana')">Semana</button>
    <button class="filter-tab" onclick="setPeriodo(this,'3meses')">3 meses</button>
  </div>
</div>

<!-- KPIs -->
<div class="kpi-grid mb-24" id="kpi-row"></div>

<!-- Gráfico: despachos por día -->
<div class="card mb-24">
  <div class="card-title"><i class="bi bi-truck"></i> Despachos por día</div>
  <div class="chart-wrap"><canvas id="chart-dias"></canvas></div>
</div>

<!-- Por canal -->
<div class="card">
  <div class="card-title"><i class="bi bi-pie-chart"></i> Por segmento</div>
  <div id="tabla-canal" style="padding:.75rem 1rem"></div>
</div>

{% endblock %}
{% block scripts %}
<script>
let periodo = 'mes';
let chartDias;

const COLORS = { success:'#4ab870', warning:'#e0a832', accent:'#C8312E', info:'#6aade8' };

function setPeriodo(btn, v) {
  document.querySelectorAll('#periodo-tabs .filter-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  periodo = v;
  loadAll();
}

async function loadAll() {
  const d = await api('GET', `/api/reportes/despacho?periodo=${periodo}`);
  const k = d.kpis;
  const tasaColor = k.tasa_pct >= 80 ? COLORS.success : k.tasa_pct >= 50 ? COLORS.warning : COLORS.accent;

  // KPIs
  document.getElementById('kpi-row').innerHTML = [
    { icon:'bi-truck',         label:'Total despachos',   val: k.total,       color: COLORS.info },
    { icon:'bi-check-circle',  label:'Despachados',       val: k.despachados, color: COLORS.success },
    { icon:'bi-clock-history', label:'Pendientes',        val: k.pendientes,  color: k.pendientes > 0 ? COLORS.warning : COLORS.success },
    { icon:'bi-percent',       label:'Tasa cumplimiento', val: k.tasa_pct + '%', color: tasaColor },
  ].map(x => `<div class="kpi-card">
    <div class="kpi-label"><i class="bi ${x.icon}" style="color:${x.color}"></i> ${x.label}</div>
    <div class="kpi-value">${x.val}</div>
  </div>`).join('');

  // Gráfico barras apiladas por día
  const labels = d.por_dia.map(r => {
    const dt = new Date(r.fecha + 'T12:00:00');
    return dt.toLocaleDateString('es-CL', {day:'numeric', month:'short'});
  });
  if (chartDias) chartDias.destroy();
  chartDias = new Chart(document.getElementById('chart-dias'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label:'Despachado',   data: d.por_dia.map(r => r.despachados),
          backgroundColor: COLORS.success + '99', borderColor: COLORS.success, borderWidth:1 },
        { label:'Pendiente',    data: d.por_dia.map(r => r.pendientes),
          backgroundColor: COLORS.warning + '99', borderColor: COLORS.warning, borderWidth:1 },
        { label:'En preparación', data: d.por_dia.map(r => r.preparacion),
          backgroundColor: COLORS.info + '66', borderColor: COLORS.info, borderWidth:1 },
      ]
    },
    options: {
      responsive:true, maintainAspectRatio:false,
      plugins: {
        legend:{ display:true, position:'top', labels:{ color:'#c9d1d9', font:{size:11} } }
      },
      scales: {
        x: { stacked:true, grid:{ color:'rgba(48,54,61,.6)' }, ticks:{ color:'#8b949e', font:{size:11} } },
        y: { stacked:true, grid:{ color:'rgba(48,54,61,.6)' }, ticks:{ color:'#8b949e', font:{size:11} } }
      }
    }
  });

  // Tabla por segmento
  const totalSeg = d.por_canal.reduce((s, r) => s + r.total, 0) || 1;
  document.getElementById('tabla-canal').innerHTML = d.por_canal.map(r => {
    const pct = (r.total / totalSeg * 100).toFixed(0);
    const tasaSeg = r.total ? Math.round(r.despachados / r.total * 100) : 0;
    const c = tasaSeg >= 80 ? COLORS.success : tasaSeg >= 50 ? COLORS.warning : COLORS.accent;
    return `<div class="flex-between mb-8">
      <span style="font-weight:600">${r.segmento}</span>
      <span style="display:flex;gap:1.5rem;align-items:center">
        <span class="text-muted text-sm">${r.total} despachos (${pct}%)</span>
        <span style="color:${c};font-weight:700;width:3.5rem;text-align:right">${tasaSeg}%</span>
      </span>
    </div>`;
  }).join('') || '<div style="color:var(--muted);font-size:.85rem">Sin despachos en el período</div>';
}

loadAll();
</script>
{% endblock %}
```

- [ ] **Step 2: Commit**

```
git add templates/reporte_despacho.html
git commit -m "feat: template reporte_despacho.html"
```

---

## Task 8: Actualizar nav en `base.html`

**Files:**
- Modify: `templates/base.html`

- [ ] **Step 1: Restructurar el sidebar**

Reemplazar todo el bloque `<nav class="sidebar-nav">` (líneas 26-88) por:

```html
  <nav class="sidebar-nav">
    <div class="nav-divider">VENTAS</div>
    {% if user_es_admin or 'pos' in user_permisos %}
    <a href="/pos/caja"      class="nav-item {% if active=='pos'           %}active{% endif %}"><i class="bi bi-cash-register"></i> POS / Caja</a>
    {% endif %}
    {% if user_es_admin or 'ventas' in user_permisos %}
    <a href="/ventas"        class="nav-item {% if active=='ventas'        %}active{% endif %}"><i class="bi bi-receipt"></i> Ventas</a>
    {% endif %}
    {% if user_es_admin or 'clientes' in user_permisos %}
    <a href="/clientes"      class="nav-item {% if active=='clientes'      %}active{% endif %}"><i class="bi bi-people"></i> Clientes</a>
    {% endif %}
    {% if user_es_admin or 'suscripciones' in user_permisos %}
    <a href="/suscripciones" class="nav-item {% if active=='suscripciones' %}active{% endif %}"><i class="bi bi-calendar-check"></i> Suscripciones</a>
    {% endif %}
    {% if user_es_admin or 'mayoristas' in user_permisos %}
    <a href="/mayoristas"    class="nav-item {% if active=='mayoristas'    %}active{% endif %}"><i class="bi bi-shop"></i> Mayoristas</a>
    {% endif %}
    {% if user_es_admin or 'crm' in user_permisos %}
    <a href="/crm"           class="nav-item {% if active=='crm'           %}active{% endif %}"><i class="bi bi-diagram-3"></i> CRM</a>
    {% endif %}

    <div class="nav-divider">PRODUCCIÓN</div>
    {% if user_es_admin or 'productos' in user_permisos %}
    <a href="/productos"  class="nav-item {% if active=='productos'  %}active{% endif %}"><i class="bi bi-basket"></i> Productos</a>
    {% endif %}
    {% if user_es_admin or 'produccion' in user_permisos %}
    <a href="/produccion" class="nav-item {% if active=='produccion' %}active{% endif %}"><i class="bi bi-fire"></i> Producción</a>
    {% endif %}
    {% if user_es_admin or 'despacho' in user_permisos %}
    <a href="/despacho"   class="nav-item {% if active=='despacho'   %}active{% endif %}"><i class="bi bi-truck"></i> Despacho</a>
    {% endif %}
    {% if user_es_admin or 'inventario' in user_permisos %}
    <a href="/inventario" class="nav-item {% if active=='inventario' %}active{% endif %}"><i class="bi bi-boxes"></i> Inventario</a>
    {% endif %}

    <div class="nav-divider">FINANZAS</div>
    {% if user_es_admin or 'gastos' in user_permisos %}
    <a href="/gastos"   class="nav-item {% if active=='gastos'   %}active{% endif %}"><i class="bi bi-cash-coin"></i> Gastos</a>
    {% endif %}
    {% if user_es_admin or 'finanzas' in user_permisos %}
    <a href="/finanzas" class="nav-item {% if active=='finanzas' %}active{% endif %}"><i class="bi bi-currency-dollar"></i> Finanzas</a>
    {% endif %}

    <div class="nav-divider">REPORTES</div>
    {% if user_es_admin or 'reportes' in user_permisos %}
    <a href="/reportes"             class="nav-item {% if active=='reportes'           %}active{% endif %}"><i class="bi bi-speedometer2"></i> Resumen</a>
    {% endif %}
    {% if user_es_admin or 'reporte_ventas' in user_permisos %}
    <a href="/reporte-ventas"       class="nav-item {% if active=='reporte_ventas'     %}active{% endif %}"><i class="bi bi-table"></i> Reporte Ventas</a>
    {% endif %}
    {% if user_es_admin or 'reportes' in user_permisos %}
    <a href="/reportes/financiero"  class="nav-item {% if active=='reportes_financiero'%}active{% endif %}"><i class="bi bi-bar-chart-line"></i> Reporte Financiero</a>
    {% endif %}
    {% if user_es_admin or 'reporte_produccion' in user_permisos %}
    <a href="/reportes/produccion"  class="nav-item {% if active=='reporte_produccion' %}active{% endif %}"><i class="bi bi-clipboard-data"></i> Reporte Producción</a>
    {% endif %}
    {% if user_es_admin or 'reporte_despacho' in user_permisos %}
    <a href="/reportes/despacho"    class="nav-item {% if active=='reporte_despacho'   %}active{% endif %}"><i class="bi bi-map"></i> Reporte Despacho</a>
    {% endif %}

    <div class="nav-divider">SISTEMA</div>
    {% if user_es_admin or 'agentes' in user_permisos %}
    <a href="/agentes"             class="nav-item {% if active=='agentes'      %}active{% endif %}"><i class="bi bi-robot"></i> Agentes</a>
    {% endif %}
    {% if user_es_admin %}
    <a href="/admin/usuarios"      class="nav-item {% if active=='admin'        %}active{% endif %}"><i class="bi bi-people-fill"></i> Usuarios</a>
    {% endif %}
    {% if user_es_admin or 'config_negocio' in user_permisos %}
    <a href="/configuracion-negocio" class="nav-item {% if active=='config_negocio' %}active{% endif %}"><i class="bi bi-gear"></i> Configuración</a>
    {% endif %}
    {% if user_es_admin or 'agenda' in user_permisos %}
    <a href="/agenda"              class="nav-item {% if active=='agenda'       %}active{% endif %}"><i class="bi bi-calendar3"></i> Agenda</a>
    {% endif %}
  </nav>
```

- [ ] **Step 2: Verificar compilación y arrancar Flask**

```
python -m py_compile app.py && echo OK
```

Navegar a `http://127.0.0.1:5000`. Verificar:
- Sidebar muestra sección REPORTES con 5 ítems
- Dashboard desaparece de SISTEMA
- Reporte Ventas ya no aparece en VENTAS
- Reporte Financiero ya no aparece en FINANZAS

- [ ] **Step 3: Commit**

```
git add templates/base.html
git commit -m "feat: reestructurar nav con sección REPORTES y eliminar Dashboard"
```

---

## Self-review

**Spec coverage:**
- [x] Sección REPORTES en nav → Task 8
- [x] Resumen con KPIs ventas/canal/finanzas/ops → Tasks 2 + 5
- [x] Reporte Ventas movido (solo nav) → Task 8
- [x] Reporte Financiero movido a `/reportes/financiero` → Tasks 1 + 8
- [x] Reporte Producción → Tasks 1 + 3 + 6
- [x] Reporte Despacho → Tasks 1 + 4 + 7
- [x] Eliminar Dashboard de nav → Task 8
- [x] Nuevos módulos en MODULOS → Task 1
- [x] Tests para los 3 endpoints nuevos → Tasks 2 + 3 + 4

**Inconsistencias corregidas:**
- `active='reportes_financiero'` en la ruta `page_reportes_financiero` coincide con el check en base.html `active=='reportes_financiero'`
- `timedelta` ya está importado en app.py (usado en múltiples lugares existentes)
- `date` ya importado en app.py

**Placeholder scan:** Sin TBDs ni TODOs — todo tiene código concreto.
