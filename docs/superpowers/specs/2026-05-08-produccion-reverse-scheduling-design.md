# Spec: Producción con Reverse Scheduling (Masa Madre)
**Fecha:** 2026-05-08  
**Sistema:** Aurora Bakers ERP — Flask + SQLite  
**Módulos afectados:** `/produccion`, `/inventario`, `app.py`, `templates/produccion.html`

---

## Contexto y Problema

El módulo de producción actual asume producción inmediata: se confirma un plan y ese mismo día el stock de productos terminados sube. Esto no refleja la realidad de la panadería de fermentación larga (masa madre), donde:

- El amasado ocurre en **T-0** (hoy)
- El horneado ocurre en **T+1** (mañana)
- Los productos despachados en T+1 responden a ventas con `fecha_despacho = T+1`

El sistema debe calcular hacia atrás (Reverse Scheduling) qué y cuánto amasar hoy para satisfacer los despachos de mañana.

---

## Decisiones de Diseño

| Decisión | Elección | Razón |
|----------|----------|-------|
| masa_base | Campo de texto en `productos` | Productos del mismo grupo comparten receta idéntica; no requiere tabla separada |
| plan_produccion | Extender tabla existente | Minimal, no rompe queries actuales |
| Migración datos existentes | Reset manual | El usuario archiva registros viejos; parte limpio |
| UI | Timeline unificada por día | Panadero ve en una sola vista qué hornea y qué amasa |

---

## 1. Migraciones de Schema

### 1.1 `productos` — 3 columnas nuevas

```sql
ALTER TABLE productos ADD COLUMN masa_base TEXT NOT NULL DEFAULT '';
ALTER TABLE productos ADD COLUMN baking_loss_pct REAL NOT NULL DEFAULT 0;
ALTER TABLE productos ADD COLUMN merma_tecnica_pct REAL NOT NULL DEFAULT 0;
```

| Campo | Tipo | Descripción | Ejemplo |
|-------|------|-------------|---------|
| `masa_base` | TEXT | Identificador de grupo de masa. Productos con el mismo valor se consolidan en un lote. | `"Masa Madre Trigo"` |
| `baking_loss_pct` | REAL | % de pérdida de peso por evaporación en el horno | `15.0` (= 15%) |
| `merma_tecnica_pct` | REAL | % de masa perdida en bowl/amasado | `2.0` (= 2%) |

Productos sin `masa_base` configurada aparecen como advertencia en la UI pero no bloquean el plan.

### 1.2 `plan_produccion` — 4 columnas nuevas

```sql
ALTER TABLE plan_produccion ADD COLUMN fecha_amasado TEXT NOT NULL DEFAULT '';
ALTER TABLE plan_produccion ADD COLUMN fecha_horneado TEXT NOT NULL DEFAULT '';
ALTER TABLE plan_produccion ADD COLUMN batch_id TEXT NOT NULL DEFAULT '';
ALTER TABLE plan_produccion ADD COLUMN ingredientes_json TEXT NOT NULL DEFAULT '[]';
```

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `fecha_amasado` | TEXT | Fecha en que se amasa (T-0). Al insertar, se establece igual que `fecha` para backward compat. |
| `fecha_horneado` | TEXT | Fecha en que se hornea y despacha (T+1). |
| `batch_id` | TEXT | UUID que agrupa todas las filas de una misma `masa_base` + `fecha_amasado`. |
| `ingredientes_json` | TEXT | Snapshot JSON de kg por ingrediente, calculado al generar el plan. Se guarda en **todas las filas del batch** (valor idéntico, redundante pero evita queries complejas). Formato: `[{"nombre": "Harina Blanca", "kg": 6.10, "inventario_id": 3}, ...]` |

### 1.3 Dominio de `estado` (ampliado)

| Valor | Significado | Trigger de inventario |
|-------|-------------|----------------------|
| `pendiente` | Plan generado, no ejecutado | — |
| `amasado` | Masa preparada | Descuenta ingredientes (bodega=ingredientes) |
| `horneado` | Productos listos | Suma stock (bodega=productos_terminados + productos.stock) |

El valor `listo` (estado anterior) se trata como `horneado` en queries de lectura para backward compat.

---

## 2. Motor de Cálculo

### 2.1 Matemática de Reverse Scheduling

Para cada grupo `masa_base` dado `fecha_horneado`:

```
masa_final_kg  = Σ( cantidad_i × peso_unitario_kg_i )

masa_cruda_kg  = masa_final_kg / (1 - baking_loss_pct / 100)

masa_amasar_kg = masa_cruda_kg × (1 + merma_tecnica_pct / 100)
```

Los valores `baking_loss_pct` y `merma_tecnica_pct` se toman del **primer producto del grupo** (todos comparten la misma masa base, por lo tanto los mismos porcentajes).

### 2.2 Baker's Percentage → Kilogramos

```
scale          = masa_amasar_kg / Σ( porcentaje_i )   [suma de TODOS los ingredientes]

kg_ingrediente = porcentaje_i × scale
```

Este cálculo es agnóstico al tipo de harina: funciona con harina simple, mixta (blanca + integral) o cualquier combinación, sin necesitar identificar cuál ingrediente es "la harina".

**Ejemplo:**  
Receta: harina_blanca=60, harina_integral=40, agua=75, sal=2, levadura=1 → sum=178  
masa_amasar=18 kg → scale=18/178=0.1011  
harina_blanca= 60×0.1011 = 6.07 kg, agua= 75×0.1011 = 7.58 kg, etc.

### 2.3 Fuente de la receta para el batch

Se usa la receta del producto con `masa_base` = grupo que tenga más ingredientes en `recetas`. Si ningún producto del grupo tiene receta, el batch se genera sin desglose de ingredientes y se muestra advertencia.

---

## 3. Endpoints API

### `GET /api/produccion/calcular-orden`

**Query params:** `fecha_horneado` (YYYY-MM-DD, default: mañana)

**Lógica:**
1. Query: `ventas JOIN venta_items JOIN productos WHERE fecha_despacho = fecha_horneado AND estado_despacho != 'CANCELADO'`
2. Group by `masa_base`
3. Aplicar matemática de reverse scheduling
4. Leer receta del grupo, calcular kg por ingrediente
5. Cruzar con `inventario.stock_kg` para flag `suficiente`

**No escribe a DB.** Solo calcula y devuelve.

**Response 200:**
```json
{
  "fecha_amasado": "2026-05-08",
  "fecha_horneado": "2026-05-09",
  "ordenes": [
    {
      "masa_base": "Masa Madre Trigo",
      "productos": [
        { "nombre": "Hogaza Campesina", "cantidad": 10, "peso_unitario_kg": 0.8, "masa_final_kg": 8.0 },
        { "nombre": "Baguette",         "cantidad": 20, "peso_unitario_kg": 0.35, "masa_final_kg": 7.0 }
      ],
      "masa_final_kg": 15.0,
      "masa_cruda_kg": 17.65,
      "masa_amasar_kg": 18.00,
      "baking_loss_pct": 15.0,
      "merma_tecnica_pct": 2.0,
      "ingredientes": [
        { "nombre": "Harina Blanca", "porcentaje": 60, "kg": 6.07, "inventario_id": 3, "stock_actual": 25.0, "suficiente": true },
        { "nombre": "Agua",          "porcentaje": 75, "kg": 7.58, "inventario_id": 7, "stock_actual": 50.0, "suficiente": true },
        { "nombre": "Sal",           "porcentaje": 2,  "kg": 0.20, "inventario_id": 5, "stock_actual": 0.5,  "suficiente": false }
      ],
      "alerta_stock": true
    }
  ],
  "sin_masa_base": [
    { "nombre": "Pan Pita", "cantidad": 5, "advertencia": "sin masa_base configurada" }
  ]
}
```

---

### `POST /api/produccion/generar-plan`

**Body:** `{ "fecha_horneado": "YYYY-MM-DD" }`

**Lógica:**
1. Ejecuta el mismo cálculo de `calcular-orden`
2. Si ya existe un plan con `fecha_amasado = hoy` para alguna `masa_base` **en estado `pendiente`**, elimina esas filas y regenera. Batches en estado `amasado` o `horneado` **nunca se tocan** (trabajo ya ejecutado).
3. Por cada orden: genera un `batch_id` (UUID4), inserta una fila en `plan_produccion` por cada producto del grupo
4. Todas las filas del batch reciben el mismo `ingredientes_json` (ver schema)
5. Retorna los batches creados

---

### `GET /api/plan-produccion?fecha=YYYY-MM-DD` (refactorizado)

Retorna dos secciones para el timeline:
```json
{
  "hornear_hoy": [ /* batches con fecha_horneado=fecha, estado=amasado */ ],
  "amasar_hoy":  [ /* batches con fecha_amasado=fecha, estado=pendiente */ ],
  "fecha": "2026-05-08"
}
```

Cada batch en la respuesta incluye: `batch_id`, `masa_base`, `estado`, `productos[]`, `masa_amasar_kg`, `ingredientes[]`.

---

### `POST /api/produccion/batch/<batch_id>/amasar`

**Validaciones:**
- estado debe ser `pendiente` → 400 si no
- Si stock insuficiente: descuenta lo disponible, incluye `advertencias[]` en respuesta (no bloquea)

**Trigger inventario:**
```
Para cada ingrediente en ingredientes_json del batch principal:
  UPDATE inventario
  SET stock_kg = MAX(0, stock_kg - kg_calculado)
  WHERE id = inventario_id
```

**Actualiza:** `plan_produccion SET estado='amasado' WHERE batch_id=?`

---

### `POST /api/produccion/batch/<batch_id>/hornear`

**Validaciones:**
- estado debe ser `amasado` → 400 si intento de saltarse el paso
- estado debe ser `pendiente` → 400 con mensaje "Registra el amasado primero"

**Trigger inventario:**
```
Para cada fila del batch (cada producto):
  _get_or_create_inv_terminado(producto_id, nombre)
  UPDATE inventario SET stock_kg = stock_kg + cantidad WHERE bodega='productos_terminados' AND producto_id=?
  UPDATE productos SET stock = stock + cantidad WHERE id=?
```

**Actualiza:** `plan_produccion SET estado='horneado' WHERE batch_id=?`

---

## 4. UI — Timeline de Producción

### Vista principal `/produccion`

Selector de fecha (default: hoy) con navegación ← → por días.

**Sección "🔥 Hornear hoy"**
- Muestra batches con `fecha_horneado = fecha_seleccionada` y `estado = amasado`
- Badge naranja "EN HORNEADO"
- Card: masa_base, productos con cantidades, masa total
- Botón: "✓ Marcar como Horneado"

**Sección "🌾 Amasar hoy"**
- Muestra batches con `fecha_amasado = fecha_seleccionada` y `estado = pendiente`
- Badge gris "PENDIENTE"
- Card: masa_base, productos, masa_amasar_kg, desglose de ingredientes en kg
- Alerta roja si algún ingrediente tiene `suficiente = false`
- Botón: "✓ Marcar como Amasado"

**Botón "Generar plan desde ventas"**
- Llama `GET calcular-orden` → muestra preview modal con tabla de órdenes y alertas de stock
- Modal tiene botón "Confirmar y guardar plan" → llama `POST generar-plan`
- Si ya existe plan del día: pregunta "¿Reemplazar plan existente?"

### Comportamiento edge cases en UI

| Caso | Comportamiento |
|------|---------------|
| Sin ventas para fecha_horneado | Sección "Amasar hoy" vacía con mensaje "No hay pedidos para mañana" |
| Productos sin masa_base | Lista separada con badge amarillo "Sin configurar" |
| Ingrediente sin stock suficiente | Card con borde rojo, badge "⚠ Stock insuficiente", botón amasar sigue disponible |
| Batch ya horneado | Aparece en sección con badge verde "HORNEADO", sin botón de acción |

---

## 5. Refactor del Confirmar Anterior

El endpoint `POST /api/plan-produccion/confirmar` actual (que hace todo en un paso) se **mantiene** para planes generados antes del refactor (compatibilidad con estado `listo`). Para planes nuevos con `batch_id` se usan los nuevos endpoints.

---

## 6. Archivos a Modificar

| Archivo | Cambios |
|---------|---------|
| `app.py` | Migraciones en `init_db()`, 5 nuevos endpoints, refactor `api_plan_produccion_get` |
| `templates/produccion.html` | Reescritura completa de la vista (timeline) |
| `templates/productos.html` | Añadir campos masa_base, baking_loss_pct, merma_tecnica_pct al modal |

---

## 7. Fuera de Scope (esta iteración)

- Notificaciones push al panadero cuando hay lotes listos para hornear
- Multi-turno (varios batches del mismo masa_base en un día)
- Integración con plan automático desde suscripciones recurrentes
- Historial de merma real vs técnica para calibrar porcentajes
