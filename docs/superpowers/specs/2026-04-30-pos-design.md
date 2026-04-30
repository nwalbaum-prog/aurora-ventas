# Módulo POS — Aurora Bakers
**Fecha:** 2026-04-30  
**Estado:** Aprobado, listo para implementar

---

## Resumen

Agregar un módulo Punto de Venta (POS) a Aurora Ventas inspirado en PuntoAlmacen/RedAlmacen. Reemplaza el formulario ERP de `/ventas` como interfaz de caja rápida para ventas en mostrador. Las ventas POS se registran en la tabla `ventas` existente (canal=`'pos'`) para aparecer en todos los reportes sin cambios.

---

## Arquitectura

**Enfoque:** Flask Blueprint en archivo separado.

`app.py` recibe una sola línea nueva:
```python
from pos import pos_bp
app.register_blueprint(pos_bp)
```

### Archivos nuevos
```
aurora-ventas/
├── pos.py                        # Blueprint Flask — todas las rutas POS
├── dte.py                        # Integración Bsale (boleta electrónica)
├── templates/
│   ├── pos.html                  # Pantalla cajero (layout híbrido)
│   ├── pos_cliente.html          # Pantalla cliente (segundo display)
│   └── pos_caja.html             # Apertura / cierre de turno
```

---

## Base de Datos

Cuatro tablas nuevas, creadas en `init_db()` con `CREATE TABLE IF NOT EXISTS`:

### `pos_turnos`
```sql
CREATE TABLE IF NOT EXISTS pos_turnos (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id              INTEGER,
    fecha_apertura          TEXT NOT NULL,
    monto_inicial_efectivo  REAL NOT NULL DEFAULT 0,
    fecha_cierre            TEXT,
    monto_declarado_efectivo REAL,
    estado                  TEXT NOT NULL DEFAULT 'abierto'  -- abierto / cerrado
)
```

### `pos_ventas`
Extiende cada venta con datos específicos de caja:
```sql
CREATE TABLE IF NOT EXISTS pos_ventas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    turno_id        INTEGER NOT NULL REFERENCES pos_turnos(id),
    venta_id        INTEGER NOT NULL REFERENCES ventas(id),
    metodo_pago     TEXT NOT NULL,   -- efectivo / tarjeta
    monto_efectivo  REAL DEFAULT 0,
    monto_tarjeta   REAL DEFAULT 0,
    vuelto          REAL DEFAULT 0,
    boleta_numero   TEXT,
    boleta_folio    INTEGER,
    boleta_pdf_url  TEXT,
    boleta_estado   TEXT DEFAULT 'pendiente'  -- pendiente / emitida / error
)
```

### `pos_frecuentes`
Productos fijados en la grilla rápida del cajero:
```sql
CREATE TABLE IF NOT EXISTS pos_frecuentes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    orden       INTEGER NOT NULL DEFAULT 0
)
```

### `pos_promociones`
```sql
CREATE TABLE IF NOT EXISTS pos_promociones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre          TEXT NOT NULL,
    tipo            TEXT NOT NULL,  -- porcentaje / fijo / 2x1
    valor           REAL NOT NULL DEFAULT 0,
    producto_id     INTEGER,        -- NULL = aplica a todo el carrito
    activa          INTEGER NOT NULL DEFAULT 1,
    fecha_inicio    TEXT,
    fecha_fin       TEXT
)
```

---

## Rutas

### Páginas (en `pos.py`)

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/pos` | Pantalla cajero — requiere turno abierto, si no redirige a `/pos/caja` |
| GET | `/pos/cliente` | Pantalla cliente — pública, sin login |
| GET | `/pos/caja` | Apertura / cierre de turno |

### API (en `pos.py`)

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/pos/productos` | Busca productos + devuelve frecuentes |
| GET | `/api/pos/turno/activo` | Turno abierto del usuario actual |
| POST | `/api/pos/turno/abrir` | Abre turno con monto inicial |
| POST | `/api/pos/turno/cerrar` | Cierra turno con arqueo final |
| POST | `/api/pos/venta` | Procesa venta completa |
| POST | `/api/pos/carrito` | Cajero sincroniza carrito actual al servidor (debounced) |
| GET | `/api/pos/cliente/estado` | Estado carrito para pantalla cliente (polling) |
| GET | `/api/pos/frecuentes` | Lista productos frecuentes |
| POST | `/api/pos/frecuentes` | Agrega / reordena frecuente |
| DELETE | `/api/pos/frecuentes/<id>` | Elimina frecuente |
| GET | `/api/pos/promociones` | Lista promociones activas |
| POST | `/api/pos/promociones` | Crea promoción |
| PUT | `/api/pos/promociones/<id>` | Edita promoción |
| DELETE | `/api/pos/promociones/<id>` | Elimina promoción |

---

## Flujo de una Venta

```
1. Cajero abre /pos → sistema verifica turno abierto
2. Cajero busca producto (texto) o hace clic en frecuente
3. Producto se agrega al carrito JS (en memoria del browser)
4. Pantalla /pos/cliente actualiza via polling cada 2s (GET /api/pos/cliente/estado)
5. Cajero presiona "Cobrar" → selecciona método (Efectivo / Tarjeta)
   - Efectivo: ingresa monto recibido → sistema calcula vuelto
   - Tarjeta: solo registra método, sin monto específico
6. POST /api/pos/venta:
   a. INSERT en ventas (canal='pos') + descuenta stock
   b. INSERT en pos_ventas con metodo_pago, vuelto
   c. Llama dte.emit_boleta() → Bsale API
   d. Si Bsale falla: venta se guarda igual, boleta_estado='pendiente'
7. Cajero ve: vuelto + número boleta + link PDF
8. Pantalla cliente muestra "¡Gracias por tu compra!" por 5s, luego vuelve a espera
```

---

## Integración DTE — Bsale (`dte.py`)

### Función pública
```python
def emit_boleta(items: list, total: float, config: dict) -> dict:
    """
    items: [{"nombre": str, "cantidad": float, "precio_unitario": float}]
    config: {"bsale_token": str, "bsale_document_type_id": int, "bsale_price_list_id": int}
    returns: {"ok": bool, "folio": int, "pdf_url": str, "numero": str, "error": str}
    """
```

### Config (en `aurora_config.json`)
```json
{
  "bsale_token": "",
  "bsale_document_type_id": 39,
  "bsale_price_list_id": 1
}
```

La configuración se agrega a `/crm/configuracion` con una sección "DTE / Boleta Electrónica". Si `bsale_token` está vacío, `emit_boleta()` retorna `{"ok": False, "error": "DTE no configurado"}` y la venta se guarda igual.

### Endpoint Bsale
```
POST https://api.bsale.io/v1/documents.json
Headers: access_token: {bsale_token}
Body: { "documentTypeId": 39, "officeId": 1, "details": [...], "payments": [...] }
```

---

## Pantalla Cliente (`/pos/cliente`)

- Ruta pública (sin `@login_required`)
- Muestra en tiempo real el carrito activo usando polling cada 2 segundos a `/api/pos/cliente/estado`
- El estado del carrito se guarda en un dict en memoria del servidor (`_pos_carrito_activo`)
- Se actualiza con cada cambio del cajero
- Estilo: **Aurora branded** — logo, colores dorados Aurora, fondo oscuro
- Estados:
  - **Esperando** — pantalla en espera con logo Aurora
  - **En curso** — lista de items + total en tiempo real
  - **Finalizado** — "¡Gracias por tu compra! Total: $X" por 5 segundos, luego vuelve a espera

---

## Pantalla Cajero (`/pos`)

- **Layout híbrido:** barra de búsqueda arriba + grilla de frecuentes + lista completa + carrito a la derecha
- Búsqueda: filtra productos en tiempo real (JS, sin request por cada tecla — debounce 300ms)
- Frecuentes: hasta 8 botones configurables, clic agrega directamente al carrito
- Carrito: botones +/- por item, botón eliminar, subtotal y total siempre visibles
- Promociones activas: se muestran como badge en el producto y descuento en el carrito
- Métodos de pago: Efectivo (con campo monto recibido y cálculo de vuelto) o Tarjeta
- Atajos de teclado: `ESC` limpia búsqueda, `Enter` en búsqueda agrega primer resultado

---

## Apertura / Cierre de Caja (`/pos/caja`)

### Apertura
- Formulario simple: monto inicial en efectivo
- Crea registro en `pos_turnos` con `estado='abierto'`
- Un usuario solo puede tener un turno abierto a la vez

### Cierre
- Muestra resumen del turno: N° ventas, total efectivo esperado, total tarjeta
- Campo para ingresar efectivo contado físicamente
- Calcula diferencia (sobrante / faltante)
- Guarda `monto_declarado_efectivo` y `estado='cerrado'`

---

## Promociones

Aplicadas automáticamente al agregar items al carrito (lógica en JS + verificación en backend al procesar venta):

- **Porcentaje:** descuento X% sobre el total o sobre un producto específico
- **Fijo:** descuenta $X del total o de un producto
- **2x1:** al agregar 2 unidades del mismo producto, la segunda es gratis

Gestión de promociones: desde `/pos/caja` en una pestaña "Promociones" junto a la apertura/cierre.

---

## Sidebar

Agregar entrada "POS / Caja" en `templates/base.html` sidebar, agrupado junto a Ventas.

---

## Restricciones

- Si no hay turno abierto, `/pos` redirige a `/pos/caja` con mensaje "Debes abrir la caja antes de vender"
- La boleta electrónica requiere configurar `bsale_token` en `/crm/configuracion`
- Si Bsale no está configurado o falla, la venta se guarda igual — boleta queda `pendiente` y se puede reintentar
- El carrito vive en el browser (JS) — si se recarga la página se pierde (aceptable para uso en caja)
