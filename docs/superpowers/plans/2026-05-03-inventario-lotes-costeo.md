# Inventario Categorías + Lotes FIFO + Costeo Real — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar categorías/subcategorías a ingredientes de inventario, vincular fichas técnicas a inventario por FK para calcular costo teórico, y crear un sistema de lotes FIFO para productos terminados con integración en ventas y POS.

**Architecture:** Todas las rutas y lógica DB viven en `app.py` (Flask) y `pos.py` (Blueprint POS). La UI es vanilla JS + Jinja2 en templates HTML. El FIFO se implementa con un helper `_descontar_lotes_fifo()` compartido por ventas ERP y POS. Los lotes son la fuente de verdad para disponibilidad de venta.

**Tech Stack:** Python Flask, SQLite (`aurora.db`), vanilla JS, Bootstrap Icons, Jinja2.

---

## Archivos modificados / creados

| Archivo | Cambio |
|---------|--------|
| `app.py` | Migraciones DB, API inventario categorías, API recetas FK+costeo, API producto-lotes, helper FIFO, integrar FIFO en ventas ERP |
| `pos.py` | Stock por lotes en api_pos_productos, FIFO en api_pos_venta |
| `templates/inventario.html` | Modal con categorías, tabla + filtros, tab productos_terminados rediseñado |
| `templates/productos.html` | Ficha técnica con vinculación inventario, columna costo real vs teórico |
| `templates/pos.html` | Productos sin stock grayed, sección lotes antes de cobrar |

---

## Task 1: Schema DB — Migraciones

**Files:**
- Modify: `app.py` (función `init_db()`, líneas ~271-289 migrations list y bloque CREATE TABLE)

- [ ] **Step 1: Agregar CREATE TABLE para tablas nuevas en init_db()**

Busca en `app.py` el bloque con `CREATE TABLE IF NOT EXISTS pos_ventas` dentro de `init_db()`. Agrega las dos tablas nuevas **después** de ese bloque, antes del cierre `""")`:

```python
            CREATE TABLE IF NOT EXISTS producto_lotes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                producto_id       INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
                fecha_elaboracion TEXT    NOT NULL,
                cantidad_inicial  REAL    NOT NULL,
                cantidad_actual   REAL    NOT NULL,
                merma             REAL    NOT NULL DEFAULT 0,
                notas             TEXT    NOT NULL DEFAULT '',
                creado_en         TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS lote_movimientos (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                lote_id   INTEGER NOT NULL REFERENCES producto_lotes(id) ON DELETE CASCADE,
                tipo      TEXT    NOT NULL,
                cantidad  REAL    NOT NULL,
                venta_id  INTEGER REFERENCES ventas(id) ON DELETE SET NULL,
                notas     TEXT    NOT NULL DEFAULT '',
                creado_en TEXT    NOT NULL DEFAULT (datetime('now'))
            );
```

- [ ] **Step 2: Agregar migraciones a la lista `migrations`**

En `app.py`, en la lista `migrations` (después de la entrada de `subcategoria`), agrega:

```python
    ("inventario", "categoria",    "ALTER TABLE inventario ADD COLUMN categoria TEXT NOT NULL DEFAULT ''"),
    ("inventario", "subcategoria", "ALTER TABLE inventario ADD COLUMN subcategoria TEXT NOT NULL DEFAULT ''"),
    ("recetas",    "inventario_id","ALTER TABLE recetas ADD COLUMN inventario_id INTEGER REFERENCES inventario(id) ON DELETE SET NULL"),
```

- [ ] **Step 3: Verificar que la app arranca y las columnas existen**

Ejecuta: `cd C:\Users\LENOVO\Documents\aurora-ventas && venv\Scripts\python.exe app.py`

Luego en otra terminal ejecuta:
```
venv\Scripts\python.exe -c "
import sqlite3, os
conn = sqlite3.connect('aurora.db')
print([r[1] for r in conn.execute('PRAGMA table_info(inventario)').fetchall()])
print([r[1] for r in conn.execute('PRAGMA table_info(recetas)').fetchall()])
print([r[1] for r in conn.execute('PRAGMA table_info(producto_lotes)').fetchall()])
print([r[1] for r in conn.execute('PRAGMA table_info(lote_movimientos)').fetchall()])
"
```
Esperado — inventario incluye `categoria`, `subcategoria`; recetas incluye `inventario_id`; ambas tablas nuevas existen con todas sus columnas.

- [ ] **Step 4: Commit**

```
git add app.py
git commit -m "feat: add DB schema for inventario categorias, recetas FK, producto_lotes, lote_movimientos"
```

---

## Task 2: API Inventario — Categorías y Subcategorías

**Files:**
- Modify: `app.py` (funciones `api_inventario_create`, `api_inventario_update`, agregar `api_inventario_categorias`)

- [ ] **Step 1: Extender api_inventario_create para guardar categoría y subcategoría**

Reemplaza la función `api_inventario_create` completa:

```python
@app.route('/api/inventario', methods=['POST'])
@login_required
def api_inventario_create():
    d = request.get_json(silent=True) or {}
    with db() as c:
        c.execute(
            """INSERT OR REPLACE INTO inventario
               (ingrediente, stock_kg, alerta_minimo_kg, proveedor, precio_kg,
                ultima_actualizacion, bodega, unidad, categoria, subcategoria)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (d.get('ingrediente',''), float(d.get('stock_kg',0)), float(d.get('alerta_minimo_kg',1)),
             d.get('proveedor',''), float(d.get('precio_kg',0)), date.today().isoformat(),
             d.get('bodega','ingredientes'), d.get('unidad','kg'),
             d.get('categoria',''), d.get('subcategoria',''))
        )
    return jsonify({'ok': True})
```

- [ ] **Step 2: Extender api_inventario_update para guardar categoría y subcategoría**

Reemplaza la función `api_inventario_update` completa (la que tiene `methods=['PUT']` y `iid` como parámetro):

```python
@app.route('/api/inventario/<int:iid>', methods=['PUT'])
@login_required
def api_inventario_update(iid):
    d = request.get_json(silent=True) or {}
    with db() as c:
        c.execute(
            """UPDATE inventario SET
               stock_kg=?, alerta_minimo_kg=?, proveedor=?, precio_kg=?,
               ultima_actualizacion=?, bodega=?, unidad=?, categoria=?, subcategoria=?
               WHERE id=?""",
            (float(d.get('stock_kg',0)), float(d.get('alerta_minimo_kg',1)),
             d.get('proveedor',''), float(d.get('precio_kg',0)), date.today().isoformat(),
             d.get('bodega','ingredientes'), d.get('unidad','kg'),
             d.get('categoria',''), d.get('subcategoria',''), iid)
        )
    return jsonify({'ok': True})
```

> **Nota:** Las rutas de inventario en `app.py` pueden tener barras invertidas en el código fuente (`\api\inventario\<int:iid>`). Si es así, cámbialas a forward slashes al mismo tiempo: `'/api/inventario/<int:iid>'`.

- [ ] **Step 3: Agregar endpoint GET /api/inventario/categorias**

Agrega esta función justo después de `api_inventario_list`:

```python
@app.route('/api/inventario/categorias', methods=['GET'])
@login_required
def api_inventario_categorias():
    with db() as c:
        rows = c.execute(
            "SELECT DISTINCT categoria, subcategoria FROM inventario WHERE categoria != '' ORDER BY categoria, subcategoria"
        ).fetchall()
    cats = {}
    for r in rows:
        cat = r['categoria']
        sub = r['subcategoria']
        if cat not in cats:
            cats[cat] = []
        if sub and sub not in cats[cat]:
            cats[cat].append(sub)
    return jsonify({
        'categorias': sorted(cats.keys()),
        'subcategorias': cats
    })
```

- [ ] **Step 4: Verificar manualmente**

Con la app corriendo, abre `http://127.0.0.1:5000/api/inventario/categorias` en el browser. Debe devolver `{"categorias": [], "subcategorias": {}}` (vacío porque los ingredientes aún no tienen categorías asignadas).

- [ ] **Step 5: Commit**

```
git add app.py
git commit -m "feat: add categoria/subcategoria to inventario API"
```

---

## Task 3: UI Inventario — Modal y Tabla con Categorías

**Files:**
- Modify: `templates/inventario.html`

- [ ] **Step 1: Agregar campos al modal de ingrediente**

En `inventario.html`, busca el modal de crear/editar ingrediente (el que tiene los campos `f-nombre`, `f-bodega`, `f-stock`, etc.). Agrega estos dos campos **después** del campo `f-proveedor`:

```html
<div>
  <label class="label">Categoría</label>
  <input id="f-categoria" type="text" class="input" list="dl-categorias"
         placeholder="Ej: Harina, Grasa, Levadura…" style="width:100%"
         oninput="filtrarSubcategorias()">
  <datalist id="dl-categorias"></datalist>
</div>
<div>
  <label class="label">Subcategoría</label>
  <input id="f-subcategoria" type="text" class="input" list="dl-subcategorias"
         placeholder="Ej: Blanca, Integral…" style="width:100%">
  <datalist id="dl-subcategorias"></datalist>
</div>
```

- [ ] **Step 2: Agregar columna Categoría en la tabla de ingredientes**

Busca la fila `<tr>` del `<thead>` de la tabla de ingredientes (bodega `ingredientes`). Agrega el `<th>` de categoría después de "Ítem":

```html
<th>Ítem</th>
<th>Categoría</th>
```

Y en la función JS que renderiza las filas de la tabla (busca donde se genera el HTML de cada ítem de inventario), agrega la celda de categoría:

```javascript
const catLabel = item.categoria
  ? (item.subcategoria ? `${item.categoria} / ${item.subcategoria}` : item.categoria)
  : '<span style="color:var(--muted)">—</span>';
// Agrega en la fila: <td style="font-size:.8rem">${catLabel}</td>
```

- [ ] **Step 3: Agregar barra de filtro por categoría sobre la tabla**

Busca el div que contiene la tabla de ingredientes y agrega una barra de filtros antes de la tabla:

```html
<div style="display:flex;gap:.5rem;margin-bottom:.75rem;flex-wrap:wrap" id="cat-filter-bar">
  <select id="f-cat-filter" class="input" style="width:160px" onchange="filtrarPorCategoria()">
    <option value="">Todas las categorías</option>
  </select>
  <select id="f-subcat-filter" class="input" style="width:160px" onchange="filtrarPorCategoria()">
    <option value="">Todas las subcategorías</option>
  </select>
</div>
```

- [ ] **Step 4: Agregar lógica JS para datalists y filtros de categoría**

En el bloque `<script>` de `inventario.html`, agrega estas funciones y llama a `cargarCategorias()` en la función de init:

```javascript
let categoriasData = { categorias: [], subcategorias: {} };

async function cargarCategorias() {
  categoriasData = await api('GET', '/api/inventario/categorias');
  // Poblar datalist del modal
  document.getElementById('dl-categorias').innerHTML =
    categoriasData.categorias.map(c => `<option value="${c}">`).join('');
  // Poblar select de filtro
  const sel = document.getElementById('f-cat-filter');
  sel.innerHTML = '<option value="">Todas las categorías</option>' +
    categoriasData.categorias.map(c => `<option value="${c}">${c}</option>`).join('');
}

function filtrarSubcategorias() {
  const cat = document.getElementById('f-categoria').value;
  const subs = categoriasData.subcategorias[cat] || [];
  document.getElementById('dl-subcategorias').innerHTML =
    subs.map(s => `<option value="${s}">`).join('');
}

function filtrarPorCategoria() {
  const cat    = document.getElementById('f-cat-filter').value;
  const subcat = document.getElementById('f-subcat-filter').value;
  // Actualizar subcategorías disponibles en el segundo select
  const subs = cat ? (categoriasData.subcategorias[cat] || []) : [];
  const selSub = document.getElementById('f-subcat-filter');
  selSub.innerHTML = '<option value="">Todas las subcategorías</option>' +
    subs.map(s => `<option value="${s}">${s}</option>`).join('');
  // Filtrar items mostrados (llama a la función existente de render con filtro)
  renderInventario(cat, subcat);
}
```

- [ ] **Step 5: Extender la función que abre el modal de edición**

Busca la función JS que abre el modal para editar un ingrediente (probablemente `editarItem(item)` o similar). Agrega estas líneas para poblar los campos nuevos:

```javascript
document.getElementById('f-categoria').value    = item.categoria    || '';
document.getElementById('f-subcategoria').value = item.subcategoria || '';
filtrarSubcategorias();
```

- [ ] **Step 6: Incluir categoría y subcategoría al guardar el ingrediente**

Busca la función JS que hace el POST o PUT al guardar el modal de ingrediente. Agrega estos campos al body del fetch:

```javascript
categoria:    document.getElementById('f-categoria').value.trim(),
subcategoria: document.getElementById('f-subcategoria').value.trim(),
```

- [ ] **Step 7: Verificar manualmente**

Abre `http://127.0.0.1:5000/inventario`, ve al tab Ingredientes, abre el modal "Nuevo ingrediente". Verifica que aparecen los campos Categoría y Subcategoría. Crea un ingrediente "Harina blanca" con categoría "Harina" y subcategoría "Blanca". Guarda y verifica que la columna de la tabla lo muestra.

- [ ] **Step 8: Commit**

```
git add templates/inventario.html
git commit -m "feat: add categoria/subcategoria to inventario UI"
```

---

## Task 4: API Recetas — FK inventario_id + Costo Teórico

**Files:**
- Modify: `app.py` (funciones `api_receta_get`, `api_receta_save`, agregar `api_productos_costos`)

- [ ] **Step 1: Reemplazar api_receta_get para incluir FK y calcular costo teórico**

```python
@app.route('/api/recetas/<int:producto_id>', methods=['GET'])
@login_required
def api_receta_get(producto_id):
    with db() as c:
        prod = c.execute(
            "SELECT id, nombre, peso_unitario_kg FROM productos WHERE id=?",
            (producto_id,)
        ).fetchone()
        if not prod:
            return jsonify({'error': 'Producto no encontrado'}), 404
        rows = c.execute(
            """SELECT r.ingrediente, r.porcentaje, r.inventario_id,
                      i.precio_kg, i.unidad, i.ingrediente AS inv_nombre
               FROM recetas r
               LEFT JOIN inventario i ON i.id = r.inventario_id
               WHERE r.producto_id = ?
               ORDER BY r.ingrediente""",
            (producto_id,)
        ).fetchall()

    peso = prod['peso_unitario_kg'] or 0
    costo_teorico = 0.0
    ingredientes_sin_vincular = 0
    ingredientes_data = []

    for r in rows:
        gramos        = round(peso * r['porcentaje'] / 100 * 1000, 2)
        vinculado     = r['inventario_id'] is not None
        costo_unitario = None
        if vinculado and r['precio_kg']:
            costo_unitario = round((gramos / 1000) * r['precio_kg'], 2)
            costo_teorico += costo_unitario
        if not vinculado:
            ingredientes_sin_vincular += 1
        ingredientes_data.append({
            'ingrediente':    r['ingrediente'],
            'inventario_id':  r['inventario_id'],
            'porcentaje':     r['porcentaje'],
            'precio_kg':      r['precio_kg'],
            'gramos_unidad':  gramos,
            'costo_unitario': costo_unitario,
            'vinculado':      vinculado
        })

    return jsonify({
        'producto_id':             prod['id'],
        'nombre':                  prod['nombre'],
        'peso_unitario_kg':        peso,
        'costo_teorico':           round(costo_teorico, 2),
        'ingredientes_sin_vincular': ingredientes_sin_vincular,
        'ingredientes':            ingredientes_data
    })
```

- [ ] **Step 2: Reemplazar api_receta_save para aceptar inventario_id**

```python
@app.route('/api/recetas/<int:producto_id>', methods=['POST'])
@login_required
def api_receta_save(producto_id):
    d = request.get_json(silent=True) or {}
    peso        = float(d.get('peso_unitario_kg', 0))
    ingredientes = d.get('ingredientes', [])
    with db() as c:
        if not c.execute("SELECT id FROM productos WHERE id=?", (producto_id,)).fetchone():
            return jsonify({'error': 'Producto no encontrado'}), 404
        c.execute("UPDATE productos SET peso_unitario_kg=? WHERE id=?", (peso, producto_id))
        c.execute("DELETE FROM recetas WHERE producto_id=?", (producto_id,))
        for ing in ingredientes:
            nombre = ing.get('ingrediente', '').strip()
            pct    = float(ing.get('porcentaje', 0))
            inv_id = ing.get('inventario_id') or None
            if nombre and pct > 0:
                c.execute(
                    "INSERT INTO recetas (producto_id, ingrediente, porcentaje, inventario_id) VALUES (?,?,?,?)",
                    (producto_id, nombre, pct, inv_id)
                )
    return jsonify({'ok': True})
```

- [ ] **Step 3: Agregar endpoint GET /api/productos/costos**

Agrega esta función en `app.py` después de `api_productos` (GET):

```python
@app.route('/api/productos/costos', methods=['GET'])
@login_required
def api_productos_costos():
    """Retorna costo teórico calculado para cada producto activo con receta."""
    with db() as c:
        prods = c.execute("SELECT id, peso_unitario_kg FROM productos WHERE activo=1").fetchall()
        resultado = []
        for p in prods:
            rows = c.execute(
                """SELECT r.porcentaje, i.precio_kg
                   FROM recetas r
                   LEFT JOIN inventario i ON i.id = r.inventario_id
                   WHERE r.producto_id = ? AND r.inventario_id IS NOT NULL AND i.precio_kg > 0""",
                (p['id'],)
            ).fetchall()
            sin_vincular = c.execute(
                "SELECT COUNT(*) FROM recetas WHERE producto_id=? AND inventario_id IS NULL",
                (p['id'],)
            ).fetchone()[0]
            peso = p['peso_unitario_kg'] or 0
            costo_teorico = sum((peso * r['porcentaje'] / 100) * r['precio_kg'] for r in rows)
            resultado.append({
                'producto_id':             p['id'],
                'costo_teorico':           round(costo_teorico, 2),
                'ingredientes_sin_vincular': sin_vincular
            })
    return jsonify(resultado)
```

- [ ] **Step 4: Verificar**

Con la app corriendo: `GET http://127.0.0.1:5000/api/productos/costos` debe devolver un array con entradas por cada producto activo. Para un producto con ingredientes vinculados muestra `costo_teorico > 0`.

- [ ] **Step 5: Commit**

```
git add app.py
git commit -m "feat: recetas API with inventario FK, costo teorico calculation"
```

---

## Task 5: UI Ficha Técnica — Vinculación con Inventario + Costo Teórico

**Files:**
- Modify: `templates/productos.html`

- [ ] **Step 1: Cargar ingredientes de inventario al abrir la ficha técnica**

En `productos.html`, en la función JS que abre el modal de ficha técnica (`abrirFicha(pid)` o similar), agrega una llamada para cargar los ingredientes de inventario:

```javascript
async function abrirFicha(pid) {
  // Carga receta actual con FK y costo teórico
  const receta = await api('GET', `/api/recetas/${pid}`);
  // Carga ingredientes de inventario disponibles para vincular
  const inventario = await api('GET', '/api/inventario?bodega=ingredientes');
  fichaInventario = inventario;  // guarda en variable global
  renderFichaModal(receta);
  document.getElementById('modal-ficha').style.display = 'flex';
}
```

Declara `let fichaInventario = [];` en el scope global del script.

- [ ] **Step 2: Actualizar función que genera fila de ingrediente en ficha técnica**

La función que genera el HTML de cada fila de ingrediente en la ficha técnica (busca donde se genera el `<select>` o `<input>` del ingrediente) debe ser reemplazada para:
- Mostrar un `<select>` con opciones del inventario (id + nombre + precio)
- Si el ingrediente tiene `inventario_id`, pre-seleccionar esa opción
- Si no tiene `inventario_id` (`vinculado: false`), marcar la fila con badge `⚠️ Sin vincular`
- Mostrar columna de costo unitario calculado

```javascript
function fichaIngredienteRow(ing, idx) {
  const vinculado = ing.inventario_id != null;
  const badge = vinculado ? '' : '<span style="background:rgba(234,179,8,.15);color:#ca8a04;font-size:.68rem;padding:1px 5px;border-radius:8px;margin-left:4px">⚠️ Sin vincular</span>';
  const costoStr = ing.costo_unitario != null
    ? `<span style="color:var(--muted);font-size:.75rem">$${Math.round(ing.costo_unitario).toLocaleString('es-CL')}</span>`
    : '<span style="color:var(--muted);font-size:.75rem">—</span>';

  const opciones = fichaInventario.map(i =>
    `<option value="${i.id}" data-precio="${i.precio_kg}" ${i.id == ing.inventario_id ? 'selected' : ''}>${i.ingrediente} — $${i.precio_kg}/kg</option>`
  ).join('');

  return `<tr data-idx="${idx}">
    <td>
      <select class="input input-sm ficha-inv-sel" data-idx="${idx}" style="min-width:160px"
              onchange="fichaActualizarInv(${idx}, this)">
        <option value="">— Seleccionar —</option>
        ${opciones}
      </select>
      ${badge}
      <input type="hidden" class="ficha-nombre" value="${ing.ingrediente}">
    </td>
    <td><input type="number" class="input input-sm ficha-pct" value="${ing.porcentaje}" min="0" step="0.1"
               style="width:70px" onchange="fichaActualizarPct(${idx}, this)"></td>
    <td style="font-size:.8rem;color:var(--muted)">${ing.gramos_unidad}g</td>
    <td>${costoStr}</td>
    <td><button onclick="fichaEliminarFila(${idx})" style="background:none;border:none;color:var(--muted);cursor:pointer">✕</button></td>
  </tr>`;
}
```

- [ ] **Step 3: Agregar función fichaActualizarInv**

```javascript
function fichaActualizarInv(idx, sel) {
  const invId = parseInt(sel.value) || null;
  const inv   = fichaInventario.find(i => i.id === invId);
  fichaIngredientes[idx].inventario_id = invId;
  fichaIngredientes[idx].ingrediente   = inv ? inv.ingrediente : fichaIngredientes[idx].ingrediente;
  fichaIngredientes[idx].precio_kg     = inv ? inv.precio_kg : null;
  renderFichaIngredientes();
}
```

- [ ] **Step 4: Agregar footer de costo teórico al modal de ficha técnica**

Busca el footer o la zona de botones del modal de ficha técnica. Agrega antes de los botones:

```html
<div id="ficha-costo-footer" style="font-size:.82rem;color:var(--muted);padding:.5rem 0;border-top:1px solid var(--border);margin-bottom:.5rem">
  Costo teórico: <strong id="ficha-costo-teorico" style="color:var(--accent)">—</strong>
  <span id="ficha-margen-teorico" style="margin-left:.75rem"></span>
  <span id="ficha-sin-vincular-warn" style="margin-left:.5rem;color:#ca8a04;display:none">
    ⚠️ Costo parcial (hay ingredientes sin vincular)
  </span>
</div>
```

Y en la función que recalcula al cambiar valores de la ficha (`renderFichaIngredientes` o equivalente), añade:

```javascript
function actualizarCostoFooter() {
  const peso = parseFloat(document.getElementById('ficha-harina-kg').value) || 0;
  const precio = parseFloat(document.getElementById('p-precio')?.value) || 0;
  let costo = 0;
  let sinVincular = 0;
  for (const ing of fichaIngredientes) {
    if (ing.inventario_id && ing.precio_kg) {
      costo += (peso * ing.porcentaje / 100) * ing.precio_kg;
    } else {
      sinVincular++;
    }
  }
  document.getElementById('ficha-costo-teorico').textContent =
    costo > 0 ? '$' + Math.round(costo).toLocaleString('es-CL') : '—';
  const margenEl = document.getElementById('ficha-margen-teorico');
  if (costo > 0 && precio > 0) {
    const m = ((precio - costo) / precio * 100).toFixed(1);
    margenEl.textContent = `· Margen teórico: ${m}%`;
    margenEl.style.color = m >= 50 ? 'var(--green)' : m >= 30 ? 'var(--yellow)' : 'var(--red)';
  } else {
    margenEl.textContent = '';
  }
  document.getElementById('ficha-sin-vincular-warn').style.display =
    sinVincular > 0 ? '' : 'none';
}
```

Llama a `actualizarCostoFooter()` dentro de `renderFichaIngredientes()`.

- [ ] **Step 5: Incluir inventario_id al guardar la ficha técnica**

En la función JS que llama a `POST /api/recetas/:id`, incluye `inventario_id` en cada ingrediente:

```javascript
ingredientes: fichaIngredientes.map(i => ({
  ingrediente:  i.ingrediente,
  porcentaje:   i.porcentaje,
  inventario_id: i.inventario_id || null
}))
```

- [ ] **Step 6: Verificar manualmente**

Abre `/productos`, haz click en "Ficha técnica" de cualquier producto. Verifica que el selector de ingredientes muestra opciones del inventario con precio. Si el producto tenía ingredientes guardados, aparecen con badge `⚠️ Sin vincular`. Vincula uno y verifica que aparece el costo teórico en el footer.

- [ ] **Step 7: Commit**

```
git add templates/productos.html
git commit -m "feat: ficha tecnica linked to inventario FK with costo teorico"
```

---

## Task 6: UI Productos — Columna Costo Real vs Teórico

**Files:**
- Modify: `templates/productos.html`

- [ ] **Step 1: Cargar costos teóricos al cargar la tabla de productos**

En la función JS que carga y renderiza la lista de productos (probablemente `cargarProductos()` o `load()`), agrega una llamada paralela:

```javascript
const [productos, costos] = await Promise.all([
  api('GET', '/api/productos'),
  api('GET', '/api/productos/costos')
]);
// Crear mapa producto_id -> {costo_teorico, ingredientes_sin_vincular}
const costosMap = {};
for (const c of costos) costosMap[c.producto_id] = c;
renderTablaProductos(productos, costosMap);
```

- [ ] **Step 2: Actualizar columna Costo/Margen en la tabla de productos**

Busca la función o template string que genera las filas `<tr>` de la tabla de productos. Reemplaza la celda de Costo/Margen por:

```javascript
function celdaCostoMargen(p, costoInfo) {
  const precioR = p.precio || 0;
  const costoR  = p.costo  || 0;
  const margenR = precioR > 0 ? ((precioR - costoR) / precioR * 100).toFixed(1) : '—';
  const mColorR = parseFloat(margenR) >= 50 ? 'var(--green)' : parseFloat(margenR) >= 30 ? 'var(--yellow)' : 'var(--red)';

  let teoricoHtml = '';
  if (costoInfo && costoInfo.costo_teorico > 0) {
    const costoT  = costoInfo.costo_teorico;
    const margenT = precioR > 0 ? ((precioR - costoT) / precioR * 100).toFixed(1) : '—';
    const diff    = costoR > 0 ? Math.abs(costoT - costoR) / costoR * 100 : 0;
    const tColor  = diff <= 15 ? 'var(--green)' : 'var(--red)';
    const parcial = costoInfo.ingredientes_sin_vincular > 0
      ? '<span title="Costo parcial" style="font-size:.65rem;color:#ca8a04">⚠️</span>' : '';
    teoricoHtml = `<div style="font-size:.72rem;color:${tColor}">
      Teórico: $${Math.round(costoT).toLocaleString('es-CL')} ${parcial}
    </div>
    <div style="font-size:.72rem;color:var(--muted)">M.teo: ${margenT}%</div>`;
  }

  return `<td class="right">
    <div class="mono" style="font-size:.82rem">$${Math.round(costoR).toLocaleString('es-CL')}</div>
    <div style="font-size:.75rem;color:${mColorR}">M: ${margenR}%</div>
    ${teoricoHtml}
  </td>`;
}
```

Reemplaza la generación de la celda de costo en el render de filas por `celdaCostoMargen(p, costosMap[p.id])`.

- [ ] **Step 3: Verificar**

Abre `/productos`. La columna Costo/Margen debe mostrar el costo real + margen. Para productos con receta vinculada, también muestra costo teórico en color verde/rojo dependiendo de la divergencia.

- [ ] **Step 4: Commit**

```
git add templates/productos.html
git commit -m "feat: show costo teorico vs costo real in productos table"
```

---

## Task 7: API Producto-Lotes

**Files:**
- Modify: `app.py` (agregar 3 nuevas rutas)

- [ ] **Step 1: Agregar GET /api/producto-lotes**

Agrega estas funciones en `app.py`, en la sección de APIs (cerca de las rutas de productos):

```python
@app.route('/api/producto-lotes', methods=['GET'])
@login_required
def api_producto_lotes_list():
    """Todos los productos activos con sus lotes, ordenados FIFO."""
    with db() as c:
        prods = c.execute(
            "SELECT id, nombre, stock, unidad FROM productos WHERE activo=1 ORDER BY nombre"
        ).fetchall()
        resultado = []
        for p in prods:
            lotes = c.execute(
                """SELECT id, fecha_elaboracion, cantidad_inicial, cantidad_actual, merma, notas, creado_en
                   FROM producto_lotes WHERE producto_id=? ORDER BY fecha_elaboracion ASC""",
                (p['id'],)
            ).fetchall()
            stock_lotes = sum(l['cantidad_actual'] for l in lotes)
            resultado.append({
                'producto_id': p['id'],
                'nombre':      p['nombre'],
                'unidad':      p['unidad'],
                'stock_total': stock_lotes,
                'lotes':       [dict(l) for l in lotes]
            })
    return jsonify(resultado)
```

- [ ] **Step 2: Agregar POST /api/producto-lotes**

```python
@app.route('/api/producto-lotes', methods=['POST'])
@login_required
def api_producto_lotes_create():
    d = request.get_json(silent=True) or {}
    prod_id  = int(d.get('producto_id', 0))
    fecha    = d.get('fecha_elaboracion', str(date.today()))
    cantidad = float(d.get('cantidad', 0))
    notas    = d.get('notas', '')
    if not prod_id or cantidad <= 0:
        return jsonify({'error': 'producto_id y cantidad requeridos'}), 400
    with db() as c:
        if not c.execute("SELECT id FROM productos WHERE id=? AND activo=1", (prod_id,)).fetchone():
            return jsonify({'error': 'Producto no encontrado'}), 404
        c.execute(
            """INSERT INTO producto_lotes (producto_id, fecha_elaboracion, cantidad_inicial, cantidad_actual, notas)
               VALUES (?,?,?,?,?)""",
            (prod_id, fecha, cantidad, cantidad, notas)
        )
        lote_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    return jsonify({'ok': True, 'id': lote_id}), 201
```

- [ ] **Step 3: Agregar PUT /api/producto-lotes/<id>/ajustar**

```python
@app.route('/api/producto-lotes/<int:lid>/ajustar', methods=['PUT'])
@login_required
def api_producto_lotes_ajustar(lid):
    d     = request.get_json(silent=True) or {}
    delta = float(d.get('delta', 0))
    tipo  = d.get('tipo', 'ajuste')   # 'merma' | 'ajuste'
    notas = d.get('notas', '')
    if delta == 0:
        return jsonify({'error': 'delta no puede ser 0'}), 400
    with db() as c:
        lote = c.execute("SELECT * FROM producto_lotes WHERE id=?", (lid,)).fetchone()
        if not lote:
            return jsonify({'error': 'Lote no encontrado'}), 404
        nueva_cantidad = max(0.0, lote['cantidad_actual'] + delta)
        c.execute(
            "UPDATE producto_lotes SET cantidad_actual=? WHERE id=?",
            (nueva_cantidad, lid)
        )
        if tipo == 'merma' and delta < 0:
            c.execute(
                "UPDATE producto_lotes SET merma=merma+? WHERE id=?",
                (abs(delta), lid)
            )
        c.execute(
            "INSERT INTO lote_movimientos (lote_id, tipo, cantidad, notas) VALUES (?,?,?,?)",
            (lid, tipo, delta, notas)
        )
        # Sincronizar productos.stock
        c.execute(
            "UPDATE productos SET stock=MAX(0, stock+?) WHERE id=?",
            (delta, lote['producto_id'])
        )
        lote_updated = c.execute("SELECT * FROM producto_lotes WHERE id=?", (lid,)).fetchone()
    return jsonify(dict(lote_updated))
```

- [ ] **Step 4: Verificar**

Con la app corriendo:
```
# Verificar GET
curl http://127.0.0.1:5000/api/producto-lotes
# Debe retornar array con todos los productos y lotes vacíos []
```

O desde browser: `GET /api/producto-lotes` → array de productos con `lotes: []`.

- [ ] **Step 5: Commit**

```
git add app.py
git commit -m "feat: add producto-lotes CRUD API (list, create, adjust)"
```

---

## Task 8: UI Productos Terminados — Tab con Lotes FIFO

**Files:**
- Modify: `templates/inventario.html`

- [ ] **Step 1: Reemplazar el contenido del tab productos_terminados**

En `inventario.html`, busca el `<div>` o `<section>` que corresponde al tab `productos_terminados` (el que actualmente muestra ítems de inventario con `bodega='productos_terminados'`). Reemplaza su contenido por:

```html
<div id="tab-productos-terminados" style="display:none">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
    <h3 style="margin:0;font-size:.9rem">Productos en Stock por Lote</h3>
    <span style="font-size:.75rem;color:var(--muted)">Orden FIFO — el primer lote se vende primero</span>
  </div>
  <div id="lotes-list"></div>
</div>
```

- [ ] **Step 2: Agregar función JS cargarLotes()**

```javascript
async function cargarLotes() {
  const data = await api('GET', '/api/producto-lotes');
  const el = document.getElementById('lotes-list');
  if (!data.length) {
    el.innerHTML = '<p style="color:var(--muted);text-align:center;padding:2rem">Sin productos</p>';
    return;
  }
  el.innerHTML = data.map(p => {
    const sinStock = p.stock_total === 0;
    const headerStyle = sinStock
      ? 'background:rgba(239,68,68,.06);border:1px solid rgba(239,68,68,.2)'
      : 'background:var(--bg-2);border:1px solid var(--border)';
    const stockBadge = sinStock
      ? '<span style="background:rgba(239,68,68,.12);color:#ef4444;font-size:.72rem;padding:2px 8px;border-radius:10px;font-weight:600">⛔ Sin stock</span>'
      : `<span style="background:rgba(34,197,94,.12);color:#16a34a;font-size:.72rem;padding:2px 8px;border-radius:10px;font-weight:600">${p.stock_total} ${p.unidad || 'u'}</span>`;

    const lotesHtml = p.lotes.length ? p.lotes.map((l, i) => `
      <div style="display:grid;grid-template-columns:auto 1fr 1fr 1fr auto auto;gap:.5rem;align-items:center;
                  padding:.4rem .75rem;border-top:1px solid var(--border);font-size:.8rem">
        ${i === 0 ? '<span style="background:rgba(99,102,241,.12);color:#6366f1;font-size:.65rem;padding:1px 5px;border-radius:6px;font-weight:600;white-space:nowrap">FIFO ▶</span>'
                  : '<span></span>'}
        <span>📦 Lote <strong>${l.fecha_elaboracion}</strong></span>
        <span style="color:var(--muted)">${l.cantidad_actual} / ${l.cantidad_inicial} iniciales</span>
        <span style="color:${l.merma > 0 ? '#ef4444' : 'var(--muted)'}">merma: ${l.merma}</span>
        <button class="btn btn-ghost btn-sm" style="font-size:.72rem;padding:.2rem .5rem"
                onclick="abrirAjusteLote(${l.id},'ajuste','${p.nombre}')">Ajustar</button>
        <button class="btn btn-ghost btn-sm" style="font-size:.72rem;padding:.2rem .5rem;color:#ef4444"
                onclick="abrirAjusteLote(${l.id},'merma','${p.nombre}')">Merma</button>
      </div>`).join('')
      : '<div style="padding:.4rem .75rem;font-size:.78rem;color:var(--muted);border-top:1px solid var(--border)">Sin lotes registrados</div>';

    return `
      <div style="${headerStyle};border-radius:.5rem;margin-bottom:.75rem;overflow:hidden">
        <div style="display:flex;justify-content:space-between;align-items:center;padding:.6rem .75rem">
          <span style="font-weight:600;font-size:.88rem">🍞 ${p.nombre}</span>
          <div style="display:flex;align-items:center;gap:.5rem">
            ${stockBadge}
            <button class="btn btn-sm btn-secondary" style="font-size:.75rem;padding:.25rem .6rem"
                    onclick="abrirNuevoLote(${p.producto_id},'${p.nombre}')">+ Agregar lote</button>
          </div>
        </div>
        ${lotesHtml}
      </div>`;
  }).join('');
}
```

- [ ] **Step 3: Agregar modal de nuevo lote y modal de ajuste**

Agrega estos modales al final de `inventario.html` (antes del `{% endblock %}`):

```html
<!-- Modal nuevo lote -->
<div id="modal-nuevo-lote" class="modal" style="display:none">
  <div class="modal-dialog" style="max-width:360px">
    <div class="modal-header"><h4>Agregar lote — <span id="nuevo-lote-nombre"></span></h4></div>
    <div class="modal-body" style="display:flex;flex-direction:column;gap:.75rem">
      <input type="hidden" id="nuevo-lote-pid">
      <div><label class="label">Fecha de elaboración</label>
           <input id="nuevo-lote-fecha" type="date" class="input" style="width:100%"></div>
      <div><label class="label">Cantidad</label>
           <input id="nuevo-lote-cant" type="number" class="input" min="1" step="1" style="width:100%"></div>
      <div><label class="label">Notas (opcional)</label>
           <input id="nuevo-lote-notas" type="text" class="input" style="width:100%" placeholder="Ej: Hornada de la mañana"></div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-ghost" onclick="document.getElementById('modal-nuevo-lote').style.display='none'">Cancelar</button>
      <button class="btn btn-primary" onclick="guardarNuevoLote()">Guardar</button>
    </div>
  </div>
</div>

<!-- Modal ajuste/merma -->
<div id="modal-ajuste-lote" class="modal" style="display:none">
  <div class="modal-dialog" style="max-width:340px">
    <div class="modal-header"><h4 id="ajuste-lote-titulo">Ajustar lote</h4></div>
    <div class="modal-body" style="display:flex;flex-direction:column;gap:.75rem">
      <input type="hidden" id="ajuste-lote-id">
      <input type="hidden" id="ajuste-lote-tipo">
      <div><label class="label">Cantidad (negativo = quitar, positivo = sumar)</label>
           <input id="ajuste-lote-delta" type="number" class="input" step="1" style="width:100%" placeholder="Ej: -5"></div>
      <div><label class="label">Motivo</label>
           <input id="ajuste-lote-notas" type="text" class="input" style="width:100%" placeholder="Ej: Pan vencido"></div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-ghost" onclick="document.getElementById('modal-ajuste-lote').style.display='none'">Cancelar</button>
      <button class="btn btn-primary" onclick="guardarAjusteLote()">Guardar</button>
    </div>
  </div>
</div>
```

- [ ] **Step 4: Agregar funciones JS para modales de lotes**

```javascript
function abrirNuevoLote(pid, nombre) {
  document.getElementById('nuevo-lote-pid').value   = pid;
  document.getElementById('nuevo-lote-nombre').textContent = nombre;
  document.getElementById('nuevo-lote-fecha').value = new Date().toISOString().split('T')[0];
  document.getElementById('nuevo-lote-cant').value  = '';
  document.getElementById('nuevo-lote-notas').value = '';
  document.getElementById('modal-nuevo-lote').style.display = 'flex';
}

async function guardarNuevoLote() {
  const pid    = parseInt(document.getElementById('nuevo-lote-pid').value);
  const fecha  = document.getElementById('nuevo-lote-fecha').value;
  const cant   = parseFloat(document.getElementById('nuevo-lote-cant').value);
  const notas  = document.getElementById('nuevo-lote-notas').value;
  if (!fecha || !cant || cant <= 0) { toast('Completa fecha y cantidad', 'error'); return; }
  await api('POST', '/api/producto-lotes', { producto_id: pid, fecha_elaboracion: fecha, cantidad: cant, notas });
  document.getElementById('modal-nuevo-lote').style.display = 'none';
  toast('Lote registrado', 'success');
  cargarLotes();
}

function abrirAjusteLote(lid, tipo, nombre) {
  document.getElementById('ajuste-lote-id').value      = lid;
  document.getElementById('ajuste-lote-tipo').value    = tipo;
  document.getElementById('ajuste-lote-titulo').textContent =
    tipo === 'merma' ? `Registrar merma — ${nombre}` : `Ajustar lote — ${nombre}`;
  document.getElementById('ajuste-lote-delta').value  = '';
  document.getElementById('ajuste-lote-notas').value  = '';
  document.getElementById('modal-ajuste-lote').style.display = 'flex';
}

async function guardarAjusteLote() {
  const lid   = parseInt(document.getElementById('ajuste-lote-id').value);
  const tipo  = document.getElementById('ajuste-lote-tipo').value;
  const delta = parseFloat(document.getElementById('ajuste-lote-delta').value);
  const notas = document.getElementById('ajuste-lote-notas').value;
  if (isNaN(delta) || delta === 0) { toast('Ingresa una cantidad distinta de 0', 'error'); return; }
  await api('PUT', `/api/producto-lotes/${lid}/ajustar`, { delta, tipo, notas });
  document.getElementById('modal-ajuste-lote').style.display = 'none';
  toast('Ajuste guardado', 'success');
  cargarLotes();
}
```

- [ ] **Step 5: Llamar cargarLotes() al activar el tab**

Busca la lógica JS de cambio de tab en `inventario.html` (probablemente un `onclick` en los tabs). Agrega una llamada a `cargarLotes()` cuando se active el tab de `productos_terminados`:

```javascript
// En la función o manejador que activa tabs:
if (bodega === 'productos_terminados') {
  cargarLotes();
}
```

- [ ] **Step 6: Verificar**

Abre `/inventario`, tab "Productos terminados". Debe mostrar todos los productos activos. Haz click en "+ Agregar lote" de cualquier producto, ingresa fecha y cantidad, guarda. El lote debe aparecer con etiqueta "FIFO ▶". Prueba "Merma" con delta `-2`.

- [ ] **Step 7: Commit**

```
git add templates/inventario.html
git commit -m "feat: productos terminados tab with FIFO lot management UI"
```

---

## Task 9: Helper FIFO + Integración en Ventas ERP

**Files:**
- Modify: `app.py` (agregar `_descontar_lotes_fifo`, modificar `api_ventas_create`, modificar `api_ventas_delete`)

- [ ] **Step 1: Agregar función helper _descontar_lotes_fifo**

Agrega esta función en `app.py` cerca de las otras funciones helper (como `_auto_plan_produccion`):

```python
def _descontar_lotes_fifo(c, producto_id, cantidad, venta_id, lote_id_override=None):
    """
    Descuenta `cantidad` unidades del producto desde lotes FIFO (más antiguos primero).
    Si lote_id_override: intenta descontar de ese lote primero antes de los demás.
    Registra cada movimiento en lote_movimientos.
    Retorna lista de (lote_id, cantidad_descontada).
    """
    if lote_id_override:
        lote_pref = c.execute(
            "SELECT * FROM producto_lotes WHERE id=? AND producto_id=? AND cantidad_actual>0",
            (lote_id_override, producto_id)
        ).fetchall()
        otros = c.execute(
            """SELECT * FROM producto_lotes
               WHERE producto_id=? AND id!=? AND cantidad_actual>0
               ORDER BY fecha_elaboracion ASC""",
            (producto_id, lote_id_override)
        ).fetchall()
        lotes = list(lote_pref) + list(otros)
    else:
        lotes = c.execute(
            """SELECT * FROM producto_lotes
               WHERE producto_id=? AND cantidad_actual>0
               ORDER BY fecha_elaboracion ASC""",
            (producto_id,)
        ).fetchall()

    movimientos = []
    restante = cantidad
    for lote in lotes:
        if restante <= 0:
            break
        descontar = min(restante, lote['cantidad_actual'])
        c.execute(
            "UPDATE producto_lotes SET cantidad_actual=cantidad_actual-? WHERE id=?",
            (descontar, lote['id'])
        )
        c.execute(
            """INSERT INTO lote_movimientos (lote_id, tipo, cantidad, venta_id, notas)
               VALUES (?,?,?,?,?)""",
            (lote['id'], 'venta', -descontar, venta_id, '')
        )
        movimientos.append((lote['id'], descontar))
        restante -= descontar

    return movimientos
```

- [ ] **Step 2: Integrar FIFO en api_ventas_create**

En `api_ventas_create`, dentro del bloque `with db() as c:`, **después** de los INSERT de `venta_items` y UPDATE de `productos.stock`, agrega la llamada al helper FIFO por cada item:

```python
        # FIFO: descontar lotes por cada item vendido
        for i in items:
            lote_id = i.get('lote_id') or None
            stock_lotes = c.execute(
                "SELECT COALESCE(SUM(cantidad_actual),0) FROM producto_lotes WHERE producto_id=?",
                (i['producto_id'],)
            ).fetchone()[0]
            if stock_lotes > 0:
                _descontar_lotes_fifo(c, i['producto_id'], float(i['cantidad']), vid, lote_id)
```

Coloca este bloque justo después del loop `for i in items:` que inserta `venta_items`.

- [ ] **Step 3: Restaurar lotes al eliminar una venta**

En `api_ventas_delete`, después de restaurar el stock de `productos`, agrega la restauración de lotes:

```python
        # Restaurar lote_movimientos: revertir todos los movimientos de esta venta
        movs = c.execute(
            """SELECT lm.lote_id, lm.cantidad FROM lote_movimientos lm
               WHERE lm.venta_id=? AND lm.tipo='venta'""",
            (vid,)
        ).fetchall()
        for m in movs:
            c.execute(
                "UPDATE producto_lotes SET cantidad_actual=cantidad_actual+? WHERE id=?",
                (abs(m['cantidad']), m['lote_id'])
            )
        c.execute("DELETE FROM lote_movimientos WHERE venta_id=?", (vid,))
```

Agrega esto **antes** del `c.execute("DELETE FROM ventas ...")`.

- [ ] **Step 4: Verificar manualmente**

1. En `/inventario` tab Productos Terminados, agrega un lote de Marraqueta con 20 unidades.
2. Ve a `/ventas` o crea una venta de 5 Marraquetas.
3. Vuelve a `/inventario` tab Productos Terminados → el lote debe mostrar 15 unidades.
4. Ve a `/reporte_ventas`, elimina esa venta → el lote debe volver a 20.

- [ ] **Step 5: Commit**

```
git add app.py
git commit -m "feat: FIFO lot deduction in ventas ERP create/delete"
```

---

## Task 10: POS — Disponibilidad por Lotes y FIFO

**Files:**
- Modify: `pos.py` (api_pos_productos, api_pos_venta)
- Modify: `templates/pos.html` (productos sin stock, sección lotes antes de cobrar)

- [ ] **Step 1: Agregar stock_lotes en api_pos_productos**

Reemplaza la función `api_pos_productos` completa en `pos.py`:

```python
@pos_bp.route('/api/pos/productos')
@login_required
def api_pos_productos():
    q = request.args.get('q', '').strip()
    with db() as c:
        if q:
            productos = c.execute(
                """SELECT p.id, p.nombre, p.precio, p.stock, p.categoria, p.subcategoria,
                          COALESCE(SUM(pl.cantidad_actual), 0) AS stock_lotes
                   FROM productos p
                   LEFT JOIN producto_lotes pl ON pl.producto_id = p.id
                   WHERE p.activo=1 AND p.nombre LIKE ?
                   GROUP BY p.id
                   ORDER BY p.categoria, p.subcategoria, p.nombre
                   LIMIT 20""",
                (f'%{q}%',)
            ).fetchall()
        else:
            productos = c.execute(
                """SELECT p.id, p.nombre, p.precio, p.stock, p.categoria, p.subcategoria,
                          COALESCE(SUM(pl.cantidad_actual), 0) AS stock_lotes
                   FROM productos p
                   LEFT JOIN producto_lotes pl ON pl.producto_id = p.id
                   WHERE p.activo=1
                   GROUP BY p.id
                   ORDER BY p.categoria, p.subcategoria, p.nombre
                   LIMIT 100"""
            ).fetchall()
        frecuentes_rows = c.execute(
            """SELECT pf.id AS frec_id, pf.orden, p.id AS producto_id, p.nombre, p.precio, p.stock
               FROM pos_frecuentes pf JOIN productos p ON p.id=pf.producto_id
               WHERE p.activo=1 ORDER BY pf.orden"""
        ).fetchall()
    prods_data = []
    for p in productos:
        d = dict(p)
        d['sin_stock'] = d['stock_lotes'] == 0
        prods_data.append(d)
    return jsonify({
        'productos':  prods_data,
        'frecuentes': [dict(f) for f in frecuentes_rows]
    })
```

- [ ] **Step 2: Integrar FIFO en api_pos_venta**

En `pos.py`, dentro de `api_pos_venta`, busca el bloque `with db() as c:` que hace los INSERT de `venta_items` y UPDATE de `productos.stock`. Después del loop `for item in items:`, agrega el FIFO:

```python
        # FIFO: descontar lotes por item
        import app as _app_module
        for item in items:
            lote_id = item.get('lote_id') or None
            stock_lotes = c.execute(
                "SELECT COALESCE(SUM(cantidad_actual),0) FROM producto_lotes WHERE producto_id=?",
                (item['producto_id'],)
            ).fetchone()[0]
            if stock_lotes > 0:
                _app_module._descontar_lotes_fifo(
                    c, item['producto_id'], float(item['cantidad']), venta_id, lote_id
                )
```

- [ ] **Step 3: Mostrar productos sin stock como deshabilitados en POS**

En `templates/pos.html`, en la función `prodRowHtml(p)`, modifica para que los productos sin stock queden grayed y no clicables:

```javascript
function prodRowHtml(p) {
  const promo    = promoActivas.find(x => x.producto_id == p.id);
  const sinStock = p.sin_stock;
  const rowStyle = sinStock
    ? 'background:var(--bg-2);border:1px solid var(--border);border-radius:.4rem;padding:.45rem .75rem;margin-bottom:.3rem;display:flex;justify-content:space-between;align-items:center;opacity:.45;pointer-events:none'
    : 'background:var(--bg-2);border:1px solid var(--border);border-radius:.4rem;padding:.45rem .75rem;margin-bottom:.3rem;display:flex;justify-content:space-between;align-items:center;cursor:pointer';
  const stockLabel = sinStock
    ? '<span style="font-size:.7rem;color:#ef4444;font-weight:600">Sin stock</span>'
    : `<span style="font-size:.7rem;color:var(--text-3)">Stock: ${p.stock}</span>`;
  const clickAttr = sinStock ? '' : `onclick='agregarProducto(${p.id},${JSON.stringify(p.nombre)},${p.precio},this)'`;
  return `<div class="prod-row" data-pid="${p.id}" ${clickAttr} style="${rowStyle}">
    <div>
      <span style="font-size:.85rem">${p.nombre}</span>
      ${promo ? `<span style="font-size:.7rem;color:var(--blue);margin-left:.4rem">🏷️ ${promo.nombre}</span>` : ''}
      <span style="margin-left:.5rem">${stockLabel}</span>
    </div>
    <span style="color:${sinStock ? 'var(--text-3)' : 'var(--accent)'};font-weight:600;font-size:.85rem">${fmt(p.precio)}</span>
  </div>`;
}
```

- [ ] **Step 4: Agregar sección de lotes antes del botón Cobrar**

En `pos.html`, busca el div con id `monto-ef-wrap` (justo antes del botón Cobrar). Agrega después de ese div:

```html
<div id="lotes-carrito-wrap" style="display:none;margin-bottom:.4rem;font-size:.75rem">
  <div style="font-size:.68rem;color:var(--text-3);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-bottom:.25rem">Lotes a descontar</div>
  <div id="lotes-carrito-list"></div>
</div>
```

- [ ] **Step 5: Agregar lógica JS para mostrar y cambiar lotes en POS**

En el `<script>` de `pos.html`, agrega:

```javascript
let lotesDisponibles = {};  // producto_id -> [{id, fecha_elaboracion, cantidad_actual}]

async function cargarLotesCarrito() {
  if (!carrito.length) {
    document.getElementById('lotes-carrito-wrap').style.display = 'none';
    return;
  }
  // Obtener lotes de todos los productos en el carrito
  const r = await fetch('/api/producto-lotes');
  const data = await r.json();
  lotesDisponibles = {};
  for (const p of data) {
    if (p.lotes.length > 0) lotesDisponibles[p.producto_id] = p.lotes;
  }
  const tieneMultiples = carrito.some(i => (lotesDisponibles[i.producto_id] || []).length > 1);
  const wrap = document.getElementById('lotes-carrito-wrap');
  if (!tieneMultiples) { wrap.style.display = 'none'; return; }

  wrap.style.display = '';
  document.getElementById('lotes-carrito-list').innerHTML = carrito.map(i => {
    const lotes = lotesDisponibles[i.producto_id] || [];
    if (lotes.length <= 1) return '';
    const opciones = lotes.map(l =>
      `<option value="${l.id}">${l.fecha_elaboracion} (${l.cantidad_actual} disp.)</option>`
    ).join('');
    return `<div style="display:flex;justify-content:space-between;align-items:center;padding:.2rem 0">
      <span style="flex:1">${i.nombre}</span>
      <select class="input" style="font-size:.72rem;padding:.15rem .3rem;width:160px"
              data-pid="${i.producto_id}" onchange="setLoteOverride(${i.producto_id},this.value)">
        ${opciones}
      </select>
    </div>`;
  }).join('');
}

function setLoteOverride(pid, lote_id) {
  const item = carrito.find(i => i.producto_id == pid);
  if (item) item.lote_id = parseInt(lote_id) || null;
}
```

Llama a `cargarLotesCarrito()` al momento de `selectPago()` (cuando el usuario selecciona método de pago):

```javascript
function selectPago(tipo) {
  // ... código existente ...
  cargarLotesCarrito();  // ← agregar esta línea al final
}
```

- [ ] **Step 6: Incluir lote_id en el body de la venta POS**

En la función `cobrar()`, en el `body` del fetch a `/api/pos/venta`, actualiza el array de items para incluir `lote_id`:

```javascript
items: carrito.map(i => ({
  producto_id:    i.producto_id,
  nombre:         i.nombre,
  precio_unitario: i.precio_unitario,
  cantidad:       i.cantidad,
  lote_id:        i.lote_id || null
})),
```

- [ ] **Step 7: Verificar flujo completo en POS**

1. Ve a `/inventario` → Productos terminados → agrega lote de 10 Marraquetas.
2. Ve a `/pos` → verifica que Marraqueta aparece disponible (no grayed).
3. Elimina el lote o ajústalo a 0 → recarga POS → Marraqueta debe aparecer grayed con "Sin stock".
4. Agrega de nuevo el lote → agrega Marraqueta al carrito → selecciona pago → si hay múltiples lotes, aparece selector de lote.
5. Cobra → verifica en inventario que el lote disminuyó.

- [ ] **Step 8: Commit**

```
git add pos.py templates/pos.html
git commit -m "feat: POS lot availability, FIFO deduction, lot override before checkout"
```

---

## Verificación Final

- [ ] Ejecuta la app: `venv\Scripts\python.exe app.py`
- [ ] Flujo completo: crear ingrediente con categoría → abrir ficha técnica → vincular ingrediente → ver costo teórico en tabla de productos
- [ ] Flujo lotes: crear lote en productos terminados → vender en POS → verificar descuento FIFO → eliminar venta → verificar restauración
- [ ] Producto sin stock no clicable en POS
