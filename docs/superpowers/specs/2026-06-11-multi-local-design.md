# Multi-local Aurora Bakers — Diseño

**Fecha:** 2026-06-11
**Estado:** Aprobado por Nico

## Objetivo

Operar 2 locales con una sola base de datos: ventas, caja y gastos separados por
local, stock de productos terminados por local con traspasos, métricas por local
y total para el dueño, y perfiles de usuario por función. El sistema corre en
Railway; los locales acceden por navegador.

## Decisiones tomadas

| Decisión | Elección |
|----------|----------|
| Servidor central | Railway (volumen persistente para `aurora.db`) |
| Alcance separación | Ventas, caja POS, gastos y stock de terminados por local. Producción e insumos centralizados en Recoleta |
| Perfiles de usuario | Cajero, Jefe de Producción, Encargado de local, Contador (+ admin existente) |
| Acceso en locales | Solo navegador, sin instalación local. Sin internet = POS de ese local no opera (riesgo asumido) |

## 1. Arquitectura de despliegue

- Servicio Railway nuevo `aurora-ventas`, builder nixpacks, `railway.toml` ya
  existente (1 worker gunicorn — SQLite no tolera escrituras concurrentes).
- Volumen montado en `/data`, env `DATA_DIR=/data`.
- Env vars: `SECRET_KEY`, `VENTAS_API_KEY`, `HTTPS=1`, `SMTP_*`,
  `GOOGLE_PLACES_API_KEY`, `ANTHROPIC_API_KEY`, `OWNER_PHONE`,
  `EVOLUTION_API_*`.
- `gunicorn` se agrega a `requirements.txt` (falta hoy).
- Scheduler APScheduler: lock con fcntl ya funciona en Linux/1 worker.
- Bot aurora-bakers: cambiar su `VENTAS_URL` a la URL Railway (queda estable;
  elimina la dependencia del túnel para este flujo).

### Backup e importación (endpoints admin nuevos)

- `GET /api/admin/backup-db` — descarga `aurora.db` (snapshot consistente vía
  `sqlite3 backup API`). Railway no respalda volúmenes: este es el mecanismo
  de respaldo, manual o automatizable después.
- `POST /api/admin/importar-db` — sube un archivo `.db` y reemplaza la base
  (valida que sea SQLite y que tenga tabla `productos`). Se usa una vez para
  migrar la DB actual; queda como mecanismo de restore. Solo rol admin.

## 2. Modelo de datos — sucursales

### Tabla nueva

```sql
CREATE TABLE sucursales (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    direccion TEXT NOT NULL DEFAULT '',
    activa INTEGER NOT NULL DEFAULT 1
);
-- seed: (1, 'Recoleta'), (2, 'Local 2') — nombres editables vía config
```

### Columnas nuevas (`sucursal_id INTEGER REFERENCES sucursales(id)`)

| Tabla | Default migración | Notas |
|-------|------------------|-------|
| `ventas` | 1 | toda venta pertenece a un local |
| `pos_turnos` | 1 | la caja es de un local |
| `gastos` | 1 | selector al registrar |
| `producto_lotes` | 1 | el lote vive en un local |
| `inventario` | 1 | solo relevante para `bodega='productos_terminados'`; insumos siempre sucursal 1 |
| `usuarios` | NULL | NULL = acceso a todas las sucursales |

### Migración de `inventario`

`inventario.ingrediente` es UNIQUE hoy; con stock por local se necesitan
varias filas del mismo producto. Migración por reconstrucción de tabla
(CREATE nueva con `UNIQUE(ingrediente, bodega, sucursal_id)` → INSERT SELECT →
DROP → RENAME), idempotente en `init_db()`. Filas existentes quedan en
sucursal 1. La fila de sucursal 2 para un producto se crea on-demand
(`_get_or_create_inv_terminado(c, producto_id, nombre, sucursal_id)`).

### Invariante de stock (actualizada)

- `productos.stock` = TOTAL entre sucursales.
- Por sucursal: `inventario.stock_kg(p, s)` = `Σ producto_lotes.cantidad_actual(p, s)`.
- Global: `productos.stock(p)` = `Σ_s inventario.stock_kg(p, s)`.
- Backfill idempotente de `init_db()` se mantiene, ahora por sucursal.

### Flujos de stock

- **Producción** (confirmar plan, hornear batch, carga manual, crear lote):
  siempre suma a sucursal 1 (Recoleta — único horno).
- **Venta** (ERP, POS): descuenta inventario + lotes FIFO **de la sucursal de
  la venta**. `_descontar_lotes_fifo` gana dimensión sucursal;
  `_restaurar_lotes_venta` no cambia (los movimientos referencian lote, que ya
  tiene sucursal).
- **Entrega de suscripción**: descuenta de sucursal 1 (los despachos a
  domicilio salen de Recoleta).
- Nota implementación: el upsert de `api_inventario_create` usa
  `ON CONFLICT(ingrediente)` hoy — debe actualizarse al nuevo conflict target
  `(ingrediente, bodega, sucursal_id)` tras la reconstrucción.
- **Traspaso**: ver sección 3.

## 3. Módulo Traspasos (nuevo)

- Tabla `traspasos` (id, fecha, origen_id, destino_id, usuario, notas) +
  `traspaso_items` (traspaso_id, producto_id, cantidad).
- API: `POST /api/traspasos` (origen, destino, items), `GET /api/traspasos`
  (historial con filtros), `GET /api/traspasos/<id>`.
- Lógica: por item, descuenta FIFO de lotes del origen (reusa
  `_descontar_lotes_fifo` con sucursal y `tipo='traspaso'` en
  `lote_movimientos`) y crea/incrementa lote espejo en destino con la misma
  `fecha_elaboracion` (trazabilidad de frescura). Actualiza filas de
  `inventario` de ambas sucursales. `productos.stock` no cambia (es total).
  Si el origen no tiene stock suficiente: error 400, no traspasos parciales.
- Página `/traspasos`: formulario (destino, productos+cantidades, stock
  disponible visible) + historial. Módulo `traspasos` en `MODULOS`.

## 4. Usuarios por función

### Sucursal por usuario

- `usuarios.sucursal_id`: fijo (cajeros, encargados) o NULL = todas (Nico,
  Daniel, contador).
- `_global_auth` carga `session['user_sucursal']` junto con permisos.
- **Enforcement servidor**: si el usuario tiene sucursal fija, toda escritura
  (venta, turno, gasto) usa SU sucursal ignorando lo que mande el cliente, y
  toda lectura filtrada por sucursal fuerza la suya.

### Perfiles preset (botones en `/admin/usuarios` que marcan checkboxes)

| Perfil | Módulos |
|--------|---------|
| Cajero/Vendedor | pos, ventas, clientes, despacho |
| Jefe de Producción | produccion, inventario, traspasos, reporte_produccion, agenda |
| Encargado de local | pos, ventas, clientes, despacho, suscripciones, traspasos, reportes, reporte_ventas, agenda |
| Contador/Finanzas | finanzas, gastos, reportes, reporte_ventas |

Los presets solo rellenan los checkboxes existentes — el sistema de permisos
finos por módulo no cambia. Al crear usuario se elige perfil + sucursal.

## 5. Reportes con filtro de sucursal

- Selector "Recoleta / Local 2 / Total" (Total default) en: Resumen reportes,
  Reporte Ventas, Finanzas (P&L, aging, flujo caja), Dashboard móvil.
- Endpoints aceptan `?sucursal_id=`; usuario con sucursal fija: el parámetro
  se ignora y se fuerza la suya (sin selector en UI).
- Reporte producción y despacho: sin cambio (producción centralizada);
  despacho gana columna sucursal informativa.

## 6. POS multi-local

- Turno se abre en la sucursal del usuario (fija) o con selector (admin).
- `pos.html` muestra stock de la sucursal del turno (productos y frecuentes,
  `sin_stock` por sucursal).
- Pantalla cliente (`/pos/cliente`): sin cambio (estado en memoria por
  proceso único — válido también en Railway con 1 worker; las dos cajas
  comparten proceso pero el carrito activo es uno solo por instancia.
  **Limitación conocida**: si ambos locales usan la pantalla-cliente a la
  vez se pisan; se documenta y se resuelve después si molesta).

## 7. Migración y pruebas

- Migraciones idempotentes en `init_db()`: tabla sucursales + seed, columnas
  `sucursal_id` (DEFAULT 1 / NULL en usuarios), reconstrucción de
  `inventario`, índices `(sucursal_id)` en ventas y producto_lotes.
- DB actual se importa a Railway vía `/api/admin/importar-db`; el primer
  arranque aplica las migraciones. Histórico completo queda en Recoleta.
- Tests: suite actual adaptada (conftest crea sucursales) + nuevos:
  traspaso feliz, traspaso sin stock, venta descuenta solo su sucursal,
  usuario con sucursal fija no ve/escribe la otra, presets de perfil,
  backup/import endpoints.

## Fuera de alcance

- Modo offline para POS sin internet.
- Producción/insumos por local (local 2 no hornea).
- Sincronización multi-DB.
- Custom domain (se puede agregar en Railway después).
