# Spec: Inventario con Categorías, Lotes FIFO y Costeo Real por Ingredientes

**Fecha:** 2026-05-03
**Proyecto:** Aurora Bakers — Sistema de Ventas
**Estado:** Aprobado, pendiente implementación

---

## Resumen

Tres mejoras conectadas al sistema de inventario y costeo de productos:

1. **Categorías/subcategorías en ingredientes** — clasificar ítems de inventario (ej: Harina / Blanca)
2. **Fichas técnicas vinculadas a inventario por FK** — calcular costo teórico real desde precios de ingredientes
3. **Productos terminados con lotes FIFO** — control de stock por fecha de elaboración, mermas, y trazabilidad en ventas

---

## 1. Schema de Base de Datos

### 1.1 Modificaciones a tablas existentes

#### Tabla `inventario` — agregar columnas
```sql
ALTER TABLE inventario ADD COLUMN categoria    TEXT NOT NULL DEFAULT '';
ALTER TABLE inventario ADD COLUMN subcategoria TEXT NOT NULL DEFAULT '';
```

#### Tabla `recetas` — agregar FK a inventario
```sql
ALTER TABLE recetas ADD COLUMN inventario_id INTEGER REFERENCES inventario(id) ON DELETE SET NULL;
```
- El campo `ingrediente TEXT` existente se conserva como display fallback
- Recetas existentes quedan con `inventario_id = NULL` (sin vincular)
- El usuario re-asigna manualmente desde la ficha técnica de cada producto

### 1.2 Nueva tabla `producto_lotes`
```sql
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
```

### 1.3 Nueva tabla `lote_movimientos`
```sql
CREATE TABLE IF NOT EXISTS lote_movimientos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id    INTEGER NOT NULL REFERENCES producto_lotes(id) ON DELETE CASCADE,
    tipo       TEXT    NOT NULL,  -- 'venta' | 'merma' | 'ajuste'
    cantidad   REAL    NOT NULL,  -- negativo = salida, positivo = ingreso/corrección
    venta_id   INTEGER REFERENCES ventas(id) ON DELETE SET NULL,
    notas      TEXT    NOT NULL DEFAULT '',
    creado_en  TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

---

## 2. Módulo: Inventario — Categorías y Subcategorías

### 2.1 Cambios en UI (`templates/inventario.html`)

**Modal crear/editar ingrediente** — agregar dos campos nuevos:
- **Categoría**: `<input type="text" list="datalist-categorias">` con datalist poblado dinámicamente desde las categorías existentes en DB. Permite escribir una nueva.
- **Subcategoría**: `<input type="text" list="datalist-subcategorias">` filtrado por la categoría seleccionada. Se actualiza dinámicamente al cambiar categoría.

**Tabla de ingredientes** — agregar columna `Categoría / Sub`:
```
Ítem | Categoría / Sub     | Stock | Alerta | Estado | Proveedor | Precio/u | Actualizado | Acciones
```
Muestra: `Harina / Blanca` o solo `Levadura` si no tiene subcategoría.

**Filtro** sobre la tabla de ingredientes (bodega `ingredientes`):
- Dropdown "Categoría" con todas las categorías existentes + "Todas"
- Al seleccionar categoría, dropdown "Subcategoría" se puebla dinámicamente

### 2.2 API (`app.py`)

**`GET /api/inventario`** — respuesta ya incluye `categoria` y `subcategoria` (columnas nuevas).

**`POST /api/inventario`** y **`PUT /api/inventario/<id>`** — aceptar y guardar `categoria` y `subcategoria`.

**`GET /api/inventario/categorias`** — nuevo endpoint:
```json
{
  "categorias": ["Harina", "Grasa", "Levadura"],
  "subcategorias": { "Harina": ["Blanca", "Integral"], "Grasa": ["Mantequilla"] }
}
```
Usado para poblar los datalists del modal.

---

## 3. Módulo: Fichas Técnicas → Inventario FK + Costo Teórico

### 3.1 Cambio en tabla `recetas`

Agrega `inventario_id` nullable como FK. La columna `ingrediente` (texto) se mantiene para:
- Mostrar recetas sin vincular con el nombre original
- Fallback visual si el ítem de inventario es eliminado

### 3.2 API de recetas

**`GET /api/recetas/<producto_id>`** — respuesta extendida:
```json
{
  "producto_id": 1,
  "nombre": "Marraqueta",
  "peso_unitario_kg": 0.08,
  "costo_teorico": 45.20,
  "ingredientes": [
    {
      "ingrediente": "harina blanca",
      "inventario_id": 3,
      "porcentaje": 100,
      "precio_kg": 800,
      "gramos_unidad": 80,
      "costo_unitario": 64.0,
      "vinculado": true
    },
    {
      "ingrediente": "sal",
      "inventario_id": null,
      "porcentaje": 2,
      "precio_kg": null,
      "gramos_unidad": 1.6,
      "costo_unitario": null,
      "vinculado": false
    }
  ]
}
```

**`POST /api/recetas/<producto_id>`** — acepta `inventario_id` opcional por ingrediente:
```json
{
  "peso_unitario_kg": 0.08,
  "ingredientes": [
    { "ingrediente": "harina blanca", "inventario_id": 3, "porcentaje": 100 },
    { "ingrediente": "sal", "inventario_id": null, "porcentaje": 2 }
  ]
}
```

**`GET /api/productos/costos`** — nuevo endpoint que devuelve costo teórico calculado para todos los productos:
```json
[
  { "producto_id": 1, "costo_teorico": 45.20, "ingredientes_sin_vincular": 1 }
]
```
Usado para mostrar columna en tabla de productos sin cargar cada receta individualmente.

### 3.3 Costo teórico — fórmula

La fórmula es uniforme para todos los ingredientes incluyendo la harina:

```
costo_teorico = SUM por ingrediente en recetas:
  (peso_unitario_kg × porcentaje / 100) × precio_kg_inventario
```

**Harina en recetas:** En el sistema % panadero, la harina base es implícita (no aparece como fila en la tabla de ingredientes de la ficha técnica). Para el cálculo de costo teórico, la harina **debe estar vinculada como ingrediente explícito** en `recetas` con `porcentaje = 100`. Si la harina no está en `recetas` con `inventario_id` vinculado, el costo teórico se marcará como `⚠️ Parcial` y la UI mostrará un aviso "Vincula la harina para costo completo".

Solo se incluyen ingredientes con `inventario_id` vinculado. Ingredientes sin vincular se excluyen del cálculo con badge de advertencia.

### 3.4 UI en productos (`templates/productos.html`)

**Columna "Costo / Margen"** expandida:
```
Costo real:    $180          ← productos.costo (manual, manda)
Costo teórico: $163 ✓        ← verde si diferencia < 15%
                   ↑ 10%     ← rojo si diferencia > 15%, con % de divergencia
Margen real:   64%
Margen teórico: 67%
```
- Si no hay receta: solo muestra costo real + margen
- Si hay ingredientes sin vincular: `⚠️ Costo teórico parcial`

**Modal Ficha Técnica** — cambios:
- Selector de ingredientes: dropdown vinculado a `inventario` (nombre + precio/u)
  - Formato: `Harina blanca — $800/kg (stock: 45kg)`
- Ingredientes existentes sin vincular: fila con badge `⚠️ Sin vincular` y botón `Asignar →` que abre mini-selector de inventario
- Muestra costo unitario estimado por ingrediente: `80g × $800/kg = $64`
- Footer del modal: `Costo teórico total: $163 | Precio: $500 | Margen teórico: 67.4%`

---

## 4. Módulo: Productos Terminados — Lotes FIFO

### 4.1 Rediseño del tab "Productos terminados" (`templates/inventario.html`)

**El tab ya no muestra ítems de `inventario` con `bodega='productos_terminados'`.**
Muestra todos los productos activos de la tabla `productos`, agrupados con sus lotes.

**Layout por producto:**
```
┌─────────────────────────────────────────────────────────────────┐
│ 🍞 Marraqueta          Stock total: 45 u   [+ Agregar lote]      │
├─────────────────────────────────────────────────────────────────┤
│ 📦 Lote 01/05  12 u / 50 iniciales  merma: 2  ← VENDER PRIMERO  │
│ 📦 Lote 03/05  33 u / 60 iniciales  merma: 0                    │
└─────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│ ⛔ Hallulla             Sin stock    [+ Agregar lote]  │
└──────────────────────────────────────────────────────┘
```

**Por lote — acciones disponibles:**
- **Ajustar cantidad**: suma o resta unidades (tipo `ajuste`)
- **Registrar merma**: resta unidades con motivo (tipo `merma`), incrementa `lote.merma`

### 4.2 API de lotes (`app.py`)

**`GET /api/producto-lotes`** — lista todos los productos con sus lotes:
```json
[
  {
    "producto_id": 1,
    "nombre": "Marraqueta",
    "stock_total": 45,
    "lotes": [
      { "id": 1, "fecha_elaboracion": "2026-05-01", "cantidad_inicial": 50,
        "cantidad_actual": 12, "merma": 2, "notas": "" },
      { "id": 2, "fecha_elaboracion": "2026-05-03", "cantidad_inicial": 60,
        "cantidad_actual": 33, "merma": 0, "notas": "" }
    ]
  }
]
```

**`POST /api/producto-lotes`** — crear nuevo lote:
```json
{ "producto_id": 1, "fecha_elaboracion": "2026-05-03", "cantidad": 60, "notas": "" }
```

**`PUT /api/producto-lotes/<id>/ajustar`** — ajustar cantidad de un lote:
```json
{ "delta": -5, "tipo": "merma", "notas": "Pan viejo no vendido" }
```
Registra en `lote_movimientos`. Si `tipo = "merma"`, incrementa `lote.merma`.

---

## 5. Módulo: Integración — Disponibilidad y FIFO en Ventas

### 5.1 Disponibilidad para venta

Un producto está disponible si `SUM(lote.cantidad_actual) > 0`. Productos sin ningún lote registrado se tratan como `stock_lotes = 0` (sin stock).

**POS (`/api/pos/productos`):**
- Productos con `stock_lotes = 0`: se incluyen en la respuesta pero con flag `sin_stock: true`
- Frontend POS: fila del producto grisada, no clicable, muestra "Sin stock"

**Ventas ERP (`/api/ventas` POST):**
- Antes de crear la venta, valida `SUM(lote.cantidad_actual) >= cantidad_vendida` por producto
- Error 400 si no hay stock suficiente en lotes

### 5.2 FIFO automático al registrar venta

Al crear una venta (POS o ERP), para cada ítem vendido:
1. Obtener lotes del producto ordenados por `fecha_elaboracion ASC` (más antiguos primero)
2. Descontar desde el más antiguo, continuar al siguiente si no alcanza
3. Registrar cada descuento en `lote_movimientos` con `tipo='venta'` y `venta_id`

**Función helper en Python:**
```python
def _descontar_lotes_fifo(c, producto_id, cantidad, venta_id, lote_id_override=None):
    """
    Descuenta `cantidad` del producto desde lotes FIFO.
    Si lote_id_override: descuenta de ese lote específico primero.
    Retorna lista de (lote_id, cantidad_descontada) para trazabilidad.
    """
```

### 5.3 Override de lote en POS

**Flujo en POS (`templates/pos.html`):**
- Al confirmar cobro, si el carrito tiene productos con lotes múltiples: muestra paso previo
- Sección "Lotes a descontar" antes del botón Cobrar:
  ```
  Marraqueta ×10  →  Lote 01/05 (12 disponibles)  [Cambiar lote]
  ```
- "Cambiar lote": dropdown con lotes disponibles del producto
- El `lote_id` seleccionado se envía en el cuerpo de la venta por ítem

**`POST /api/pos/venta`** — body extendido:
```json
{
  "items": [
    { "producto_id": 1, "nombre": "Marraqueta", "precio_unitario": 500,
      "cantidad": 10, "lote_id": 1 }
  ],
  "metodo_pago": "efectivo",
  "monto_efectivo": 5000,
  "total": 5000
}
```
`lote_id` es opcional — si no viene, se usa FIFO automático.

---

## 6. Consideraciones de Implementación

### Orden de implementación sugerido
1. Schema DB (migraciones) — todas las tablas y columnas nuevas
2. Inventario: categorías/subcategorías (API + UI)
3. Fichas técnicas: FK a inventario + costo teórico (API + UI)
4. Productos terminados: lotes FIFO (API + UI en tab inventario)
5. Integración ventas: FIFO automático en POS y ERP

### Stock: sincronización entre `productos.stock` y lotes
- `productos.stock` se mantiene como counter para ventas (existente, no se toca su lógica)
- `SUM(lote.cantidad_actual)` es el stock "real de producción" visible en inventario
- **Crear un lote NO suma a `productos.stock`** — el lote es una unidad de trazabilidad, no el evento de producción. El stock se sigue sumando desde `/produccion` al confirmar hornada (comportamiento existente). Esto evita doble conteo.
- **Al ajustar/mermar un lote:** sí ajusta `productos.stock` con el mismo delta, para mantenerlos en sync
- **Al descontar por venta (FIFO):** actualiza `lote.cantidad_actual` y `productos.stock` simultáneamente

### Productos sin receta
- Pueden existir sin ficha técnica → no tienen costo teórico → columna muestra solo costo real
- No bloquea funcionalidad de lotes ni ventas

### Migración de datos existentes
- Recetas existentes: `inventario_id = NULL` → badge "Sin vincular", usuario re-asigna
- Inventario existente: `categoria = ''`, `subcategoria = ''` → el usuario asigna progresivamente
- `producto_lotes`: tabla nueva vacía → usuario crea lotes desde el tab productos terminados
