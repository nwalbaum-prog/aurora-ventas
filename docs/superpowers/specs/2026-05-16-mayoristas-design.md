# Módulo Mayoristas — Diseño

**Fecha:** 2026-05-16  
**Proyecto:** aurora-ventas (Flask + SQLite)  
**Estado:** Aprobado

---

## Resumen

Módulo para gestionar pedidos fijos recurrentes de clientes mayoristas (HORECA/B2B). Cada mayorista tiene una plantilla de pedido por día de despacho (martes y/o jueves), con múltiples líneas de producto. Un botón "Generar semana" convierte las plantillas en ventas reales que alimentan producción y despacho sin cambios en esos módulos.

---

## Base de datos

### Tablas nuevas

```sql
CREATE TABLE mayorista_pedidos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id   INTEGER NOT NULL REFERENCES clientes(id),
    dia_despacho TEXT    NOT NULL CHECK(dia_despacho IN ('martes','jueves')),
    activo       INTEGER NOT NULL DEFAULT 1,
    notas        TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE mayorista_pedido_lineas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pedido_id   INTEGER NOT NULL REFERENCES mayorista_pedidos(id) ON DELETE CASCADE,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    cantidad    REAL    NOT NULL DEFAULT 1
);
```

### Invariantes
- Un cliente puede tener como máximo 1 registro en `mayorista_pedidos` por `dia_despacho`.
- `clientes.tipo = 'MAYORISTA'` es el filtro para el dropdown de selección.
- Las ventas generadas usan `canal = 'MAYORISTA'` para identificación.

### Migraciones (en lista `migrations` de `init_db()`)
Se agregan como entradas `(tabla, columna_check, sql)` usando el patrón existente con `_col_exists`. Como son tablas nuevas, el check es sobre la propia tabla:
```python
("mayorista_pedidos",       "id", "CREATE TABLE IF NOT EXISTS mayorista_pedidos (id INTEGER PRIMARY KEY AUTOINCREMENT, cliente_id INTEGER NOT NULL REFERENCES clientes(id), dia_despacho TEXT NOT NULL CHECK(dia_despacho IN ('martes','jueves')), activo INTEGER NOT NULL DEFAULT 1, notas TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT (datetime('now')))"),
("mayorista_pedido_lineas", "id", "CREATE TABLE IF NOT EXISTS mayorista_pedido_lineas (id INTEGER PRIMARY KEY AUTOINCREMENT, pedido_id INTEGER NOT NULL REFERENCES mayorista_pedidos(id) ON DELETE CASCADE, producto_id INTEGER NOT NULL REFERENCES productos(id), cantidad REAL NOT NULL DEFAULT 1)"),
```

---

## Rutas

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/mayoristas` | Vista principal del módulo |
| GET | `/api/mayoristas` | Lista mayoristas con sus pedidos activos |
| POST | `/api/mayoristas` | Crear nuevo mayorista (crea cliente tipo MAYORISTA o actualiza tipo de existente) |
| GET | `/api/mayoristas/<id>/pedidos` | Pedidos fijos de un mayorista (ambos días) |
| PUT | `/api/mayoristas/<id>/pedidos` | Guardar plantilla completa (reemplaza líneas) |
| POST | `/api/mayoristas/generar-semana` | Genera ventas para todos los mayoristas activos de la semana actual |

---

## Interfaz (`mayoristas.html`)

### Layout general
Dos columnas: lista de mayoristas (izquierda) + detalle del seleccionado (derecha).

### Panel izquierdo
- Dropdown con todos los clientes `tipo='MAYORISTA'` + botón "Agregar mayorista"
- Modal de agregar: dropdown de clientes existentes (para marcar como MAYORISTA) o formulario de cliente nuevo
- Indicador por mayorista: estado de generación de la semana actual (Generado / Pendiente / Sin pedidos)

### Panel derecho
- Título con nombre del mayorista
- Dos secciones colapsables: **Martes** y **Jueves**
- Cada sección:
  - Tabla de líneas: producto (select), cantidad (input numérico), precio mayorista (readonly), subtotal
  - Botón "Agregar línea", botón eliminar por línea
  - Toggle activo/inactivo para ese día
  - Subtotal del día
- Botón "Guardar cambios" por sección
- Total semanal al pie

### Barra de acción
- Botón **"Generar semana [fecha lunes – viernes]"**
- Genera ventas idempotente: omite clientes que ya tienen venta MAYORISTA en esa fecha

---

## Lógica de generación semanal

```
Para cada mayorista_pedido WHERE activo=1:
    fecha_despacho = fecha del martes o jueves de la semana ISO actual (lunes–domingo);
                    si el día ya pasó en la semana actual, se usa igual (la venta queda registrada con esa fecha)
    Si NO existe venta WHERE cliente_id=pedido.cliente_id 
                        AND canal='MAYORISTA' 
                        AND DATE(fecha)=fecha_despacho:
        INSERT INTO ventas (cliente_id, fecha, canal, estado_pago, estado_despacho, total)
        Para cada línea del pedido:
            INSERT INTO venta_items (venta_id, producto_id, cantidad, precio_unitario)
            precio_unitario = productos.precio_mayorista
        UPDATE ventas SET total = SUM(cantidad * precio_unitario)
```

### Modificaciones semana a semana
- **Cambio puntual** (esa semana): editar directamente la venta generada en módulo Ventas.
- **Cambio permanente**: editar la plantilla en `/mayoristas`.
- **Cancelar semana**: eliminar la venta generada desde Ventas, o no generar (la plantilla queda intacta).

---

## Integración con módulos existentes

| Módulo | Cambio requerido | Mecanismo |
|--------|-----------------|-----------|
| Producción | Ninguno | Lee `ventas` por fecha; ventas MAYORISTA aparecen automáticamente |
| Despacho | Ninguno | Filtra `ventas WHERE estado_despacho='PENDIENTE'`; incluye MAYORISTA |
| Finanzas | Ninguno | `canal='MAYORISTA'` ya es un valor válido en el P&L por canal |
| Navegación | Agregar ítem "Mayoristas" en `base.html` bajo sección Ventas | Manual |

---

## Archivos a modificar / crear

| Archivo | Acción |
|---------|--------|
| `app.py` | Migraciones + 6 rutas nuevas |
| `templates/mayoristas.html` | Crear (nuevo template) |
| `templates/base.html` | Agregar ítem de navegación |

---

## Fuera de alcance

- Precios negociados por cliente (se usa `precio_mayorista` global del producto).
- Notificaciones automáticas al mayorista.
- Historial de cambios de plantilla.
- Generación automática vía cron (el usuario presiona el botón manualmente cada semana).
