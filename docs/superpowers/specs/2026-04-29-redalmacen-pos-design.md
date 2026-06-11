# Diseño: Funcionalidades RedAlmacen para Aurora Bakers
**Fecha:** 2026-04-29  
**Estado:** Aprobado por usuario

---

## Resumen

Agregar al sistema Aurora Bakers cuatro módulos inspirados en RedAlmacen/PuntoAlmacen:

1. **Boleta Electrónica SII** — emisión de boletas válidas tributariamente (DTE tipo 39)
2. **Interfaz POS** — punto de venta para cajero con soporte de barcode scanner
3. **Sistema de Promociones** — precios mayoristas, descuentos y combos
4. **Pantalla Cliente** — display en tiempo real accesible desde cualquier dispositivo en la red

---

## Arquitectura General

Enfoque: **Flask Blueprints en archivos separados**, sin modificar la lógica existente de `app.py`. Solo se agregan 3 imports y 3 `register_blueprint` al final de `app.py`.

### Estructura de archivos nuevos

```
aurora-ventas/
├── app.py                          (existente — solo 6 líneas nuevas al final)
├── blueprints/
│   ├── __init__.py
│   ├── pos.py                      (POS + pantalla cliente)
│   ├── boleta.py                   (SII DTE boleta electrónica)
│   └── promociones.py              (gestión de precios y descuentos)
├── templates/
│   ├── pos.html                    (interfaz cajero)
│   ├── pos_cliente.html            (pantalla cliente — solo lectura)
│   └── promociones.html            (gestión CRUD de promociones)
└── certificados/                   (no incluir en git — agregar a .gitignore)
    ├── 17704304-4.pfx              (certificado digital — ya disponible)
    └── caf_39.xml                  (CAF folios — descargar desde SII portal)
```

### Registro en app.py

```python
from blueprints.pos import pos_bp
from blueprints.boleta import boleta_bp
from blueprints.promociones import promociones_bp
app.register_blueprint(pos_bp)
app.register_blueprint(boleta_bp)
app.register_blueprint(promociones_bp)
```

### Dependencias Python nuevas

```
lxml          # construcción del XML DTE
cryptography  # leer .pfx y firmar
signxml       # firma XML xmldsig (estándar SII)
reportlab     # generar PDF de boleta con timbre
```

---

## Módulo 1: Boleta Electrónica SII

### Descripción

Emitir boletas electrónicas válidas ante el SII usando el certificado digital del emisor y los folios CAF autorizados. El flujo es completamente transparente desde el POS: el cajero solo presiona "Emitir boleta".

### Flujo de emisión

```
POS cobra venta
  → genera XML DTE tipo 39 (boleta electrónica)
  → firma XML con certificado .pfx (xmldsig)
  → envía a SII via SOAP (SendBOLETA)
  → SII devuelve TrackID
  → guarda boleta con estado "enviada"
  → genera PDF con timbre electrónico (código QR + folio)
  → muestra PDF al cajero / envía a impresora
```

### Tabla DB: `boletas_emitidas`

| Campo | Tipo | Descripción |
|---|---|---|
| id | INTEGER PK | |
| folio | INTEGER | Número de folio consumido del CAF |
| pos_venta_id | INTEGER FK | Venta POS asociada |
| rut_receptor | TEXT | RUT del cliente (66666666-6 si no se especifica) |
| monto_neto | INTEGER | |
| monto_iva | INTEGER | |
| monto_total | INTEGER | |
| xml_dte | TEXT | XML firmado completo |
| track_id | TEXT | ID de seguimiento SII |
| estado | TEXT | enviada / aceptada / rechazada / anulada |
| pdf_path | TEXT | Ruta al PDF generado (en `static/boletas/<folio>.pdf`) |
| fecha_emision | DATETIME | |

### Configuración SII (nueva sección en `/crm/configuracion`)

- Ruta al certificado `.pfx` (default: `certificados/17704304-4.pfx`)
- Contraseña del certificado (guardada en `aurora_config.json`)
- Ruta al CAF `.xml` (subir desde interfaz web)
- RUT emisor, razón social, giro, dirección
- Ambiente: `certificacion` (maullin.sii.cl) o `produccion` (palena.sii.cl)

### Gestión de folios

- Aurora lleva el contador de folios internamente (último folio usado + 1)
- Si quedan menos de 10 folios disponibles: alerta en dashboard y en POS
- Si el CAF se agota: bloquea emisión de boletas con mensaje de error claro

### Manejo de errores SII

| Error | Acción |
|---|---|
| SII no responde | Guarda con estado "pendiente", reintenta al consultar el estado desde configuración |
| Rechazo por folio duplicado | Incrementa folio y reintenta |
| Rechazo por certificado | Muestra error en configuración |
| Sin CAF cargado | Bloquea emisión, muestra instrucciones |

---

## Módulo 2: Interfaz POS

### Descripción

Pantalla de punto de venta diseñada para velocidad. Funciona con teclado/mouse y con lector de código de barras (el scanner actúa como teclado, termina con Enter).

### Rutas

| Ruta | Descripción |
|---|---|
| `GET /pos` | Pantalla principal del cajero |
| `GET /pos/cliente` | Pantalla cliente (solo lectura, cualquier dispositivo) |
| `POST /api/pos/sesion/abrir` | Abrir sesión de caja |
| `POST /api/pos/sesion/cerrar` | Cerrar sesión con arqueo |
| `GET /api/pos/sesion/activa` | Estado de sesión actual |
| `POST /api/pos/venta` | Registrar venta completada |
| `GET /api/pos/carro` | Estado actual del carro (para pantalla cliente) |
| `GET /api/pos/productos/buscar` | Buscar productos por nombre o código |

### Sesiones de caja: tabla `pos_sesiones`

| Campo | Tipo | Descripción |
|---|---|---|
| id | INTEGER PK | |
| cajero | TEXT | Nombre del cajero |
| fecha_apertura | DATETIME | |
| fecha_cierre | DATETIME | NULL si está abierta |
| fondo_inicial | INTEGER | Efectivo al abrir |
| total_ventas | INTEGER | Suma de ventas de la sesión |
| total_efectivo | INTEGER | Efectivo al arquear |
| diferencia | INTEGER | total_efectivo - (fondo_inicial + ventas_efectivo) |
| estado | TEXT | abierta / cerrada |

### Ventas POS: tablas `pos_ventas` y `pos_venta_items`

**pos_ventas:**
- sesion_id, cliente_id (opcional), subtotal, descuento_total, total, medio_pago, boleta_id, fecha

**pos_venta_items:**
- venta_id, producto_id, cantidad, precio_unitario, tipo_precio (normal/mayorista/promocion), promocion_id, subtotal

### Pantalla POS (`/pos`)

**Layout de dos columnas:**
- **Izquierda (60%):** barra de búsqueda + grid de productos con foto/nombre/precio
- **Derecha (40%):** carro de compras con ítems, subtotales, descuentos aplicados, total, campo "recibido", vuelto calculado

**Selector de tipo precio:**
- Normal / Mayorista / Promoción activa (se activa automáticamente si aplica)

**Teclas rápidas:**
- `F1` — foco en barra de búsqueda
- `F2` — ir a cobrar
- `Esc` — vaciar carro (con confirmación)
- `Enter` en búsqueda — agregar primer resultado al carro

**Medios de pago:** Efectivo / Débito / Crédito / Transferencia

**Al cobrar:**
- Opción de emitir boleta electrónica (con o sin RUT del cliente)
- Si se emite boleta: llama a `boleta.py` en background, muestra folio asignado
- Imprime ticket o muestra PDF según configuración

### Pantalla Cliente (`/pos/cliente`)

- Página HTML/CSS sin controles, solo lectura
- Polling `GET /api/pos/carro` cada 1 segundo
- Muestra: lista de ítems del carro actual + total
- Al completar venta: pantalla "¡Gracias por su compra!" por 5 segundos
- Pensada para segundo monitor o tablet en el mesón

---

## Módulo 3: Sistema de Promociones

### Descripción

Gestión de precios alternativos y descuentos. El POS evalúa automáticamente las promociones activas al agregar productos al carro.

### Tipos de promoción

| Tipo | Descripción | Configuración |
|---|---|---|
| `mayorista` | Precio especial desde N unidades | precio_especial + cantidad_minima |
| `descuento_pct` | Porcentaje de descuento | porcentaje (0-100) + fechas |
| `precio_fijo` | Precio fijo temporal | precio + fechas |
| `cantidad` | Lleva N paga M (ej: 3x2) | cantidad_lleva + cantidad_paga |
| `combo` | Grupo de productos a precio especial | lista de productos + precio combo |

### Tabla `promociones`

| Campo | Tipo | Descripción |
|---|---|---|
| id | INTEGER PK | |
| nombre | TEXT | Ej: "2x1 Medialunas" |
| tipo | TEXT | mayorista/descuento_pct/precio_fijo/cantidad/combo |
| producto_id | INTEGER FK | NULL si es combo multi-producto |
| valor | REAL | Porcentaje, precio, o cantidad según tipo |
| cantidad_minima | INTEGER | Para tipo mayorista y cantidad |
| cantidad_paga | INTEGER | Para tipo cantidad (ej: paga 2 de 3) |
| productos_combo | TEXT | JSON de product_ids para combos |
| fecha_inicio | DATE | NULL = sin límite de inicio |
| fecha_fin | DATE | NULL = sin vencimiento |
| activa | BOOLEAN | |

### Lógica de aplicación en POS

1. Al agregar producto: buscar promociones activas hoy para ese producto
2. Si hay múltiples promociones: aplicar la más beneficiosa para el cliente
3. Tipo mayorista: se activa cuando la cantidad en carro alcanza el mínimo
4. Tipo cantidad: al agregar el ítem N, el precio del ítem M se aplica gratis
5. El carro muestra el precio original tachado y el precio con descuento

### Gestión (`/promociones`)

- CRUD completo con formulario por tipo
- Vista de tabla con estado (activa/vencida/futura) y badge de tipo
- Indicador en el sidebar de cuántas promociones están activas ahora

---

## Integración con módulos existentes

- **Productos:** el POS usa la tabla `productos` existente; se agrega campo `codigo_barra` si no existe
- **Clientes:** en el POS se puede seleccionar cliente para precio mayorista o historial
- **Ventas:** las ventas del POS se registran también en la tabla `ventas` existente para no romper reportes actuales
- **Reportes:** el dashboard existente suma automáticamente ventas POS al total del día

---

## Requisitos previos del usuario

Antes de usar boleta electrónica:

1. **Descargar CAF:** Portal SII → Servicios Online → Factura Electrónica → Solicitar Folios → Tipo 39 → Descargar XML
2. **Subir CAF** a Aurora desde `/crm/configuracion`
3. **Verificar certificado:** ya disponible en `Downloads/17704304-4.pfx`
4. **Configurar en Aurora:** RUT emisor (17704304-4), razón social, giro, dirección, contraseña certificado
5. **Probar en ambiente certificación** antes de producción

---

## Dependencias externas

| Librería | Uso | Instalar |
|---|---|---|
| lxml | XML DTE | `pip install lxml` |
| cryptography | Leer .pfx | `pip install cryptography` |
| signxml | Firma xmldsig | `pip install signxml` |
| reportlab | PDF boleta | `pip install reportlab` |

---

## Fuera de alcance

- Hardware POS (cajón de dinero, impresora térmica): el usuario gestiona el hardware
- Boleta de honor / factura electrónica (tipo 33): solo boleta tipo 39
- Multi-sucursal: una sola instancia/local
- App móvil nativa: la pantalla cliente cubre el caso de uso de consulta móvil
