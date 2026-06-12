"""
Aurora Bakers — Sistema de Ventas
Corre con: python app.py  →  http://127.0.0.1:5000
"""
from flask import Flask, render_template, request, jsonify, redirect, session, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3, os, json, smtplib, urllib.request, urllib.parse, secrets, uuid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, datetime, timedelta
from contextlib import contextmanager

ANTHROPIC_API_KEY    = os.environ.get('ANTHROPIC_API_KEY', '')
GOOGLE_PLACES_API_KEY= os.environ.get('GOOGLE_PLACES_API_KEY', '')

SMTP_HOST   = os.environ.get('SMTP_HOST',  'smtp.gmail.com')
SMTP_PORT   = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER   = os.environ.get('SMTP_USER',  '')
SMTP_PASS   = os.environ.get('SMTP_PASS',  '')
OWNER_EMAIL = os.environ.get('OWNER_EMAIL','')

# ── Directorio de datos persistentes ─────────────────────────────────────────
# En Railway: DATA_DIR=/data (volume persistente). Local: directorio del proyecto
_BASE_DIR_EARLY = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR_EARLY = os.environ.get('DATA_DIR', _BASE_DIR_EARLY)
os.makedirs(_DATA_DIR_EARLY, exist_ok=True)

# ── Configuración runtime (aurora_config.json) ────────────────────────────────
_CONFIG_PATH = os.path.join(_DATA_DIR_EARLY, 'aurora_config.json')

def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def _save_config(data: dict):
    try:
        current = _load_config()
        current.update(data)
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[config] Error guardando: {e}")

def _cfg(key: str, default: str = '') -> str:
    """Lee config: env var tiene prioridad, luego aurora_config.json."""
    return os.environ.get(key.upper(), '') or _load_config().get(key, default)

def _limpiar_texto(s: str) -> str:
    """Quita < y > de texto que entra desde fuentes externas (WhatsApp, Google
    Places, IA). Los templates renderizan estos campos con innerHTML; sin esto
    un nombre malicioso ejecuta HTML/JS en el navegador del CRM."""
    return (s or '').replace('<', '').replace('>', '')

# Fallback a aurora_config.json (gitignoreado) — nunca hardcodear la key aquí
GOOGLE_PLACES_API_KEY = GOOGLE_PLACES_API_KEY or _cfg('google_places_api_key')

def _get_or_create_inv_terminado(c, producto_id, nombre, sucursal_id=1):
    """Obtiene o crea entrada en inventario para un producto terminado en una sucursal."""
    row = c.execute(
        "SELECT id FROM inventario WHERE bodega='productos_terminados' AND producto_id=? AND sucursal_id=?",
        (producto_id, sucursal_id)
    ).fetchone()
    if row:
        return row['id']
    cur = c.execute(
        "INSERT INTO inventario (ingrediente, bodega, stock_kg, unidad, alerta_minimo_kg, producto_id, sucursal_id)"
        " VALUES (?,?,?,?,?,?,?)",
        (nombre, 'productos_terminados', 0, 'unidades', 0, producto_id, sucursal_id)
    )
    return cur.lastrowid


def _auto_plan_produccion(items, c, fecha_destino=None):
    """Agrega/incrementa items en plan_produccion.
    Planifica para fecha_destino (fecha_despacho de la venta) si es futura;
    si no viene o ya pasó, para el día siguiente.
    items: lista de {'nombre_producto': str, 'cantidad': float}
    c: conexión SQLite activa (se ejecuta dentro de la transacción del llamador).
    """
    from datetime import date, timedelta
    hoy    = str(date.today())
    manana = str(date.today() + timedelta(days=1))
    fecha  = fecha_destino if (fecha_destino and fecha_destino > hoy) else manana
    for item in items:
        nombre   = item['nombre_producto']
        cantidad = max(1, round(float(item['cantidad'])))
        pid = item.get('producto_id') or None
        if not pid:
            r = c.execute("SELECT id FROM productos WHERE nombre=?", (nombre,)).fetchone()
            pid = r['id'] if r else None
        existente = c.execute(
            "SELECT id FROM plan_produccion WHERE fecha=? AND nombre_producto=?",
            (fecha, nombre)
        ).fetchone()
        if existente:
            c.execute("UPDATE plan_produccion SET cantidad=cantidad+? WHERE id=?",
                      (cantidad, existente['id']))
        else:
            c.execute(
                """INSERT INTO plan_produccion
                   (fecha, codigo_producto, nombre_producto, cantidad, estado, notas, producto_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (fecha, 'P' + str(pid) if pid else nombre[:4].upper(),
                 nombre, cantidad, 'pendiente', 'Auto-venta', pid)
            )


# ── Mayoristas B2B — helpers ──────────────────────────────────────────────────

def _precio_mayorista(c, mayorista_id, producto_id) -> float:
    """Precio de venta mayorista: precio específico > precio_mayorista > 80% del precio base."""
    row = c.execute(
        "SELECT precio FROM mayoristas_precios WHERE mayorista_id=? AND producto_id=? AND activo=1",
        (mayorista_id, producto_id)
    ).fetchone()
    if row:
        return float(row['precio'])
    prod = c.execute("SELECT precio, precio_mayorista FROM productos WHERE id=?", (producto_id,)).fetchone()
    if not prod:
        return 0.0
    if prod['precio_mayorista'] and float(prod['precio_mayorista']) > 0:
        return float(prod['precio_mayorista'])
    return round(float(prod['precio']) * 0.80, 0)


def _calcular_score_fit(tipo_negocio: str, zona: str, notas: str = '') -> int:
    """Score 0-8 de fit del prospecto para Aurora Bakers."""
    score = 0
    tipo = (tipo_negocio or '').lower()
    notas_l = (notas or '').lower()
    if tipo in ['restaurante', 'café', 'cafetería', 'bistró', 'deli']:
        score += 3
    elif tipo in ['hotel', 'catering', 'oficina', 'cowork', 'tienda gourmet']:
        score += 2
    if zona in COMUNAS_DESPACHO:
        score += 2
    if any(w in notas_l for w in ['desayuno', 'brunch', 'masa madre', 'artesanal']):
        score += 1
    if any(w in notas_l for w in ['panadería propia', 'panaderia propia', 'pan propio']):
        score -= 1
    return score


def _deuda_mayorista(c, mayorista_id: int) -> dict:
    """Resumen de deuda pendiente de un mayorista."""
    rows = c.execute(
        """SELECT monto, fecha_vencimiento FROM mayoristas_cobranza
           WHERE mayorista_id=? AND estado IN ('PENDIENTE','VENCIDO')""",
        (mayorista_id,)
    ).fetchall()
    today = str(date.today())
    total = 0.0
    vencida = 0.0
    mas_antigua_dias = 0
    for r in rows:
        m = float(r['monto'])
        total += m
        fv = r['fecha_vencimiento'] or ''
        if fv and fv < today:
            vencida += m
            try:
                dias = (date.today() - date.fromisoformat(fv)).days
                if dias > mas_antigua_dias:
                    mas_antigua_dias = dias
            except Exception:
                pass
    return {
        'total':           round(total, 0),
        'vencida':         round(vencida, 0),
        'documentos':      len(rows),
        'mas_antigua_dias': mas_antigua_dias,
    }


def _descontar_lotes_fifo(c, producto_id, cantidad, venta_id, lote_id_override=None,
                          sucursal_id=1, tipo='venta'):
    """
    Descuenta `cantidad` unidades del producto desde lotes FIFO (más antiguos primero)
    de una sucursal. Si lote_id_override: intenta descontar de ese lote primero.
    Registra cada movimiento en lote_movimientos.
    Retorna lista de (lote_id, cantidad_descontada).
    """
    if lote_id_override:
        lote_pref = c.execute(
            "SELECT * FROM producto_lotes WHERE id=? AND producto_id=? AND cantidad_actual>0 AND sucursal_id=?",
            (lote_id_override, producto_id, sucursal_id)
        ).fetchall()
        otros = c.execute(
            """SELECT * FROM producto_lotes
               WHERE producto_id=? AND id!=? AND cantidad_actual>0 AND sucursal_id=?
               ORDER BY fecha_elaboracion ASC""",
            (producto_id, lote_id_override, sucursal_id)
        ).fetchall()
        lotes = list(lote_pref) + list(otros)
    else:
        lotes = c.execute(
            """SELECT * FROM producto_lotes
               WHERE producto_id=? AND cantidad_actual>0 AND sucursal_id=?
               ORDER BY fecha_elaboracion ASC""",
            (producto_id, sucursal_id)
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
            (lote['id'], tipo, -descontar, venta_id, '')
        )
        movimientos.append((lote['id'], descontar))
        restante -= descontar

    return movimientos


def _restaurar_lotes_venta(c, venta_id):
    """Reversa los movimientos de lote de una venta: devuelve las unidades a sus
    lotes originales y elimina los movimientos tipo 'venta' asociados."""
    movs = c.execute(
        "SELECT lote_id, cantidad FROM lote_movimientos WHERE venta_id=? AND tipo='venta'",
        (venta_id,)
    ).fetchall()
    for m in movs:
        c.execute(
            "UPDATE producto_lotes SET cantidad_actual=cantidad_actual+? WHERE id=?",
            (-m['cantidad'], m['lote_id'])
        )
    c.execute("DELETE FROM lote_movimientos WHERE venta_id=? AND tipo='venta'", (venta_id,))


def _calcular_orden_produccion(c, fecha_horneado: str) -> dict:
    """
    Reverse scheduling: dado fecha_horneado calcula qué amasar hoy.
    Lee ventas con fecha_despacho=fecha_horneado, agrupa por masa_base,
    aplica matemática de baking_loss + merma + baker's percentage.
    No escribe a la DB. c puede ser cursor de sqlite3 directo o de db().
    """
    rows = c.execute("""
        SELECT p.id AS producto_id, p.nombre, p.peso_unitario_kg,
               p.masa_base, p.baking_loss_pct, p.merma_tecnica_pct,
               SUM(vi.cantidad) AS cantidad_total
        FROM venta_items vi
        JOIN ventas v  ON v.id  = vi.venta_id
        JOIN productos p ON p.id = vi.producto_id
        WHERE v.fecha_despacho = ?
          AND v.estado_despacho != 'CANCELADO'
          AND p.activo = 1
        GROUP BY p.id
    """, (fecha_horneado,)).fetchall()

    sin_masa_base = []
    grupos: dict[str, list] = {}
    for r in rows:
        r = dict(r)
        if not r['masa_base']:
            sin_masa_base.append({'nombre': r['nombre'], 'cantidad': r['cantidad_total'],
                                   'advertencia': 'sin masa_base configurada'})
            continue
        grupos.setdefault(r['masa_base'], []).append(r)

    ordenes = []
    for masa_base, productos in grupos.items():
        # First product is the reference — all products sharing a masa_base are expected to have identical baking/merma percentages.
        ref = productos[0]
        baking_loss = ref['baking_loss_pct'] / 100.0
        merma      = ref['merma_tecnica_pct'] / 100.0

        prods_out = []
        masa_final_kg = 0.0
        for p in productos:
            mf = p['cantidad_total'] * p['peso_unitario_kg']
            masa_final_kg += mf
            prods_out.append({
                'producto_id':    p['producto_id'],
                'nombre':         p['nombre'],
                'cantidad':       p['cantidad_total'],
                'peso_unitario_kg': p['peso_unitario_kg'],
                'masa_final_kg':  round(mf, 3),
            })

        masa_cruda_kg  = masa_final_kg / (1 - baking_loss) if baking_loss < 1 else masa_final_kg
        masa_amasar_kg = masa_cruda_kg * (1 + merma)

        receta_prod_id, max_ings = None, -1
        for p in productos:
            cnt = c.execute("SELECT COUNT(*) FROM recetas WHERE producto_id=?",
                            (p['producto_id'],)).fetchone()[0]
            if cnt > max_ings:
                max_ings, receta_prod_id = cnt, p['producto_id']

        ingredientes_out = []
        alerta_stock = False
        if receta_prod_id and max_ings > 0:
            receta_rows = c.execute("""
                SELECT r.ingrediente, r.porcentaje, r.inventario_id,
                       COALESCE(i.stock_kg, 0) AS stock_actual
                FROM recetas r
                LEFT JOIN inventario i ON i.id = r.inventario_id
                WHERE r.producto_id = ?
            """, (receta_prod_id,)).fetchall()

            sum_pct = sum(r['porcentaje'] for r in receta_rows)
            scale   = masa_amasar_kg / sum_pct if sum_pct > 0 else 0.0

            for r in receta_rows:
                kg = round(r['porcentaje'] * scale, 3)
                suficiente = float(r['stock_actual']) >= kg
                if not suficiente:
                    alerta_stock = True
                ingredientes_out.append({
                    'nombre':       r['ingrediente'],
                    'porcentaje':   r['porcentaje'],
                    'kg':           kg,
                    'inventario_id': r['inventario_id'],
                    'stock_actual': round(float(r['stock_actual']), 3),
                    'suficiente':   suficiente,
                })

        ordenes.append({
            'masa_base':         masa_base,
            'productos':         prods_out,
            'masa_final_kg':     round(masa_final_kg, 3),
            'masa_cruda_kg':     round(masa_cruda_kg, 3),
            'masa_amasar_kg':    round(masa_amasar_kg, 3),
            'baking_loss_pct':   ref['baking_loss_pct'],
            'merma_tecnica_pct': ref['merma_tecnica_pct'],
            'ingredientes':      ingredientes_out,
            'alerta_stock':      alerta_stock,
        })

    return {'ordenes': ordenes, 'sin_masa_base': sin_masa_base}


# ── Evolution API (agente WhatsApp) ───────────────────────────────────────────

def _wa_cfg():
    cfg = _load_config()
    return {
        'url':      os.environ.get('EVOLUTION_API_URL',      cfg.get('wa_url',      'http://localhost:8080')),
        'apikey':   os.environ.get('EVOLUTION_API_KEY',      cfg.get('wa_apikey',   '')),
        'instance': os.environ.get('EVOLUTION_INSTANCE',     cfg.get('wa_instance', 'aurora')),
    }

def _wa_request(method: str, path: str, body: dict = None) -> dict:
    cfg = _wa_cfg()
    url = cfg['url'].rstrip('/') + path
    data = json.dumps(body).encode('utf-8') if body else None
    req  = urllib.request.Request(url, data=data, method=method, headers={
        'Content-Type': 'application/json',
        'apikey': cfg['apikey'],
    })
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode('utf-8'))

def _wa_agent_connected() -> bool:
    try:
        cfg = _wa_cfg()
        if not cfg['apikey']:
            return False
        r = _wa_request('GET', f"/instance/connectionState/{cfg['instance']}")
        state = (r.get('instance', {}).get('state') or r.get('state') or '').lower()
        return state == 'open'
    except Exception:
        return False

def _send_whatsapp_agent(number_raw: str, message: str) -> tuple[bool, str]:
    """Envía mensaje WhatsApp via Evolution API. Retorna (ok, error_msg)."""
    cfg = _wa_cfg()
    if not cfg['apikey']:
        return False, 'Evolution API no configurada'
    num = ''.join(ch for ch in number_raw if ch.isdigit())
    if not num:
        return False, 'Número inválido'
    if not num.startswith('56'):
        num = '56' + num
    try:
        _wa_request('POST', f"/message/sendText/{cfg['instance']}", {
            'number': num,
            'text':   message,
        })
        return True, ''
    except Exception as e:
        return False, str(e)


def send_renewal_email(cliente_nombre: str, cliente_email: str, precio: float, notas: str = '') -> bool:
    """Envía correo de renovación al cliente y copia al owner."""
    if not SMTP_USER or not SMTP_PASS:
        print("[email] SMTP no configurado — correo no enviado")
        return False
    destinatarios = [d for d in [cliente_email, OWNER_EMAIL] if d]
    if not destinatarios:
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = '🍞 Tu suscripción Aurora Bakers está lista para renovar'
        msg['From']    = SMTP_USER
        msg['To']      = ', '.join(destinatarios)
        texto = (
            f"Hola {cliente_nombre},\n\n"
            f"Tu suscripción en Aurora Bakers ha completado sus 4 entregas. "
            f"¡Es momento de renovar para seguir recibiendo tus panes favoritos!\n\n"
            f"Precio de renovación: ${precio:,.0f}\n"
            + (f"Notas: {notas}\n" if notas else "")
            + "\nPara renovar, responde este correo o escríbenos por WhatsApp.\n\n"
            "¡Gracias por ser parte de Aurora Bakers!"
        )
        msg.attach(MIMEText(texto, 'plain', 'utf-8'))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.ehlo(); srv.starttls(); srv.ehlo()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.sendmail(SMTP_USER, destinatarios, msg.as_string())
        print(f"[email] Renovación enviada a {destinatarios}")
        return True
    except Exception as e:
        print(f"[email] Error: {e}")
        return False

app = Flask(__name__)
BASE_DIR  = _BASE_DIR_EARLY
_DATA_DIR = _DATA_DIR_EARLY
DB_PATH   = os.path.join(_DATA_DIR, 'aurora.db')

# API key para agentes (aurora-bakers → aurora-ventas).
# Acepta VENTAS_API_KEY o AGENT_API_KEY como env var; única fuente de verdad.
AGENT_API_KEY = (os.environ.get('VENTAS_API_KEY')
                 or os.environ.get('AGENT_API_KEY')
                 or 'aurora_agent_2024')

# ── Mayoristas B2B — constantes ───────────────────────────────────────────────
MAYORISTAS_ESTADOS_PEDIDO = ['PENDIENTE','CONFIRMADO','EN_PRODUCCION','DESPACHADO','ENTREGADO','CANCELADO']
MAYORISTAS_ESTADOS_PAGO   = ['PENDIENTE','PARCIAL','PAGADO','VENCIDO']
TIPOS_NEGOCIO_B2B = ['restaurante','café','cafetería','hotel','hostal','oficina','cowork',
                     'catering','deli','bistró','tienda gourmet','panadería complementaria']
COMUNAS_DESPACHO = ['Providencia','Las Condes','Vitacura','Ñuñoa','Santiago Centro',
                    'Recoleta','Independencia','La Reina','Macul','San Miguel','Barrio Italia']

# ── Clave secreta para sesiones ───────────────────────────────────────────────
_SK_FILE = os.path.join(_DATA_DIR, '.secret_key')
def _get_secret_key():
    if os.environ.get('SECRET_KEY'):
        return os.environ['SECRET_KEY']
    if os.path.exists(_SK_FILE):
        with open(_SK_FILE) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(_SK_FILE, 'w') as f:
        f.write(key)
    return key

app.secret_key = _get_secret_key()
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE']   = os.environ.get('HTTPS', '') == '1'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# ── Database ──────────────────────────────────────────────────────────────────

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

def _col_exists(c, table, col):
    info = c.execute(f"PRAGMA table_info({table})").fetchall()
    if not info:   # tabla aún no existe → se creará con la columna incluida
        return True
    return any(r['name'] == col for r in info)

def init_db():
    with db() as c:
        # ── Sucursales (multi-local) ─────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS sucursales (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre    TEXT    NOT NULL,
                direccion TEXT    NOT NULL DEFAULT '',
                activa    INTEGER NOT NULL DEFAULT 1
            )
        """)
        if c.execute("SELECT COUNT(*) FROM sucursales").fetchone()[0] == 0:
            c.executemany(
                "INSERT INTO sucursales (nombre, direccion) VALUES (?,?)",
                [('Recoleta', ''), ('Local 2', '')]
            )
        c.executescript("""
            CREATE TABLE IF NOT EXISTS productos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre      TEXT    NOT NULL,
                descripcion TEXT    NOT NULL DEFAULT '',
                precio      REAL    NOT NULL,
                costo       REAL    NOT NULL DEFAULT 0,
                stock       REAL    NOT NULL DEFAULT 0,
                unidad      TEXT    NOT NULL DEFAULT 'unidad',
                activo      INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS clientes (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre        TEXT    NOT NULL,
                email         TEXT    NOT NULL DEFAULT '',
                telefono      TEXT    NOT NULL DEFAULT '',
                direccion     TEXT    NOT NULL DEFAULT '',
                notas         TEXT    NOT NULL DEFAULT '',
                es_suscriptor INTEGER NOT NULL DEFAULT 0,
                creado_en     TEXT    NOT NULL DEFAULT (date('now'))
            );
            CREATE TABLE IF NOT EXISTS suscripciones (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id       INTEGER NOT NULL REFERENCES clientes(id),
                plan             TEXT    NOT NULL,
                precio           REAL    NOT NULL,
                productos_json   TEXT    NOT NULL DEFAULT '[]',
                fecha_inicio     TEXT    NOT NULL,
                fecha_renovacion TEXT    NOT NULL DEFAULT '',
                estado           TEXT    NOT NULL DEFAULT 'activo',
                notas            TEXT    NOT NULL DEFAULT '',
                dia_despacho     TEXT    NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS ventas (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha            TEXT    NOT NULL DEFAULT (date('now')),
                cliente_id       INTEGER REFERENCES clientes(id),
                canal            TEXT    NOT NULL DEFAULT 'local',
                total            REAL    NOT NULL DEFAULT 0,
                notas            TEXT    NOT NULL DEFAULT '',
                creado_en        TEXT    NOT NULL DEFAULT (datetime('now')),
                fecha_despacho   TEXT    NOT NULL DEFAULT '',
                con_despacho     INTEGER NOT NULL DEFAULT 1,
                tipo_cliente     TEXT    NOT NULL DEFAULT 'CLIENTE',
                estado_pago      TEXT    NOT NULL DEFAULT 'PENDIENTE',
                estado_despacho  TEXT    NOT NULL DEFAULT 'PENDIENTE',
                sucursal_id      INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS venta_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                venta_id        INTEGER NOT NULL REFERENCES ventas(id)    ON DELETE CASCADE,
                producto_id     INTEGER NOT NULL REFERENCES productos(id),
                cantidad        REAL    NOT NULL,
                precio_unitario REAL    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS compras_insumos (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha         TEXT    NOT NULL DEFAULT (date('now')),
                inventario_id INTEGER REFERENCES inventario(id) ON DELETE SET NULL,
                ingrediente   TEXT    NOT NULL DEFAULT '',
                cantidad_kg   REAL    NOT NULL DEFAULT 0,
                precio_total  REAL    NOT NULL DEFAULT 0,
                proveedor     TEXT    NOT NULL DEFAULT '',
                factura       TEXT    NOT NULL DEFAULT '',
                notas         TEXT    NOT NULL DEFAULT '',
                creado_en     TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS mayorista_pedidos (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id   INTEGER NOT NULL REFERENCES clientes(id),
                dia_despacho TEXT    NOT NULL CHECK(dia_despacho IN ('martes','jueves')),
                activo       INTEGER NOT NULL DEFAULT 1,
                notas        TEXT    NOT NULL DEFAULT '',
                created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS mayorista_pedido_lineas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_id   INTEGER NOT NULL REFERENCES mayorista_pedidos(id) ON DELETE CASCADE,
                producto_id INTEGER NOT NULL REFERENCES productos(id),
                cantidad    REAL    NOT NULL DEFAULT 1
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_mayorista_pedido
                ON mayorista_pedidos(cliente_id, dia_despacho);
            CREATE TABLE IF NOT EXISTS mayorista_semanas (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                semana       TEXT    NOT NULL UNIQUE,
                fecha_martes TEXT    NOT NULL DEFAULT '',
                fecha_jueves TEXT    NOT NULL DEFAULT '',
                estado       TEXT    NOT NULL DEFAULT 'borrador'
                                     CHECK(estado IN ('borrador','confirmado','cerrado')),
                created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS mayorista_ajustes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                semana_id    INTEGER NOT NULL REFERENCES mayorista_semanas(id) ON DELETE CASCADE,
                cliente_id   INTEGER NOT NULL REFERENCES clientes(id),
                producto_id  INTEGER NOT NULL REFERENCES productos(id),
                dia          TEXT    NOT NULL CHECK(dia IN ('martes','jueves')),
                cantidad     REAL    NOT NULL DEFAULT 0,
                cancelado    INTEGER NOT NULL DEFAULT 0,
                nota         TEXT    NOT NULL DEFAULT '',
                UNIQUE(semana_id, cliente_id, producto_id, dia)
            );
            CREATE TABLE IF NOT EXISTS mayoristas (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre               TEXT    NOT NULL,
                razon_social         TEXT    NOT NULL DEFAULT '',
                rut                  TEXT    NOT NULL DEFAULT '',
                giro                 TEXT    NOT NULL DEFAULT '',
                tipo_negocio         TEXT    NOT NULL DEFAULT '',
                encargado_nombre     TEXT    NOT NULL DEFAULT '',
                encargado_telefono   TEXT    NOT NULL DEFAULT '',
                encargado_email      TEXT    NOT NULL DEFAULT '',
                telefono             TEXT    NOT NULL DEFAULT '',
                email                TEXT    NOT NULL DEFAULT '',
                direccion            TEXT    NOT NULL DEFAULT '',
                zona                 TEXT    NOT NULL DEFAULT '',
                dia_despacho_1       TEXT    NOT NULL DEFAULT '',
                dia_despacho_2       TEXT    NOT NULL DEFAULT '',
                volumen_semanal_panes INTEGER NOT NULL DEFAULT 0,
                condicion_pago       TEXT    NOT NULL DEFAULT 'contado',
                limite_credito       REAL    NOT NULL DEFAULT 0,
                activo               INTEGER NOT NULL DEFAULT 1,
                tiene_factura        INTEGER NOT NULL DEFAULT 0,
                notas                TEXT    NOT NULL DEFAULT '',
                fecha_alta           TEXT    NOT NULL DEFAULT (date('now')),
                fecha_ultimo_pedido  TEXT    NOT NULL DEFAULT '',
                crm_lead_id          INTEGER REFERENCES crm_leads(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS mayoristas_precios (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                mayorista_id  INTEGER REFERENCES mayoristas(id) ON DELETE CASCADE,
                producto_id   INTEGER REFERENCES productos(id) ON DELETE CASCADE,
                precio        REAL    NOT NULL,
                descuento_pct REAL    NOT NULL DEFAULT 0,
                min_unidades  INTEGER NOT NULL DEFAULT 1,
                activo        INTEGER NOT NULL DEFAULT 1,
                UNIQUE(mayorista_id, producto_id)
            );
            CREATE TABLE IF NOT EXISTS mayoristas_pedidos (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                mayorista_id    INTEGER NOT NULL REFERENCES mayoristas(id) ON DELETE RESTRICT,
                fecha_pedido    TEXT    NOT NULL DEFAULT (date('now')),
                fecha_entrega   TEXT    NOT NULL DEFAULT '',
                dia_entrega     TEXT    NOT NULL DEFAULT '',
                estado          TEXT    NOT NULL DEFAULT 'PENDIENTE',
                subtotal        REAL    NOT NULL DEFAULT 0,
                descuento       REAL    NOT NULL DEFAULT 0,
                total           REAL    NOT NULL DEFAULT 0,
                condicion_pago  TEXT    NOT NULL DEFAULT 'contado',
                estado_pago     TEXT    NOT NULL DEFAULT 'PENDIENTE',
                notas           TEXT    NOT NULL DEFAULT '',
                notas_produccion TEXT   NOT NULL DEFAULT '',
                creado_por      TEXT    NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS mayoristas_pedido_items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_id    INTEGER NOT NULL REFERENCES mayoristas_pedidos(id) ON DELETE CASCADE,
                producto_id  INTEGER NOT NULL REFERENCES productos(id) ON DELETE RESTRICT,
                cantidad     INTEGER NOT NULL DEFAULT 1,
                precio_unit  REAL    NOT NULL DEFAULT 0,
                descuento    REAL    NOT NULL DEFAULT 0,
                subtotal     REAL    NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS mayoristas_cobranza (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                mayorista_id    INTEGER NOT NULL REFERENCES mayoristas(id) ON DELETE RESTRICT,
                pedido_id       INTEGER REFERENCES mayoristas_pedidos(id) ON DELETE SET NULL,
                tipo_doc        TEXT    NOT NULL DEFAULT 'boleta',
                numero_doc      TEXT    NOT NULL DEFAULT '',
                monto           REAL    NOT NULL DEFAULT 0,
                fecha_emision   TEXT    NOT NULL DEFAULT (date('now')),
                fecha_vencimiento TEXT  NOT NULL DEFAULT '',
                fecha_pago      TEXT    NOT NULL DEFAULT '',
                estado          TEXT    NOT NULL DEFAULT 'PENDIENTE',
                medio_pago      TEXT    NOT NULL DEFAULT '',
                notas           TEXT    NOT NULL DEFAULT ''
            );
        """)

        # Migraciones: agregar columnas que no existan en tablas previas
        migrations = [
            ("ventas",       "fecha_despacho",   "ALTER TABLE ventas ADD COLUMN fecha_despacho TEXT NOT NULL DEFAULT ''"),
            ("ventas",       "con_despacho",     "ALTER TABLE ventas ADD COLUMN con_despacho INTEGER NOT NULL DEFAULT 1"),
            ("ventas",       "tipo_cliente",     "ALTER TABLE ventas ADD COLUMN tipo_cliente TEXT NOT NULL DEFAULT 'CLIENTE'"),
            ("ventas",       "estado_pago",      "ALTER TABLE ventas ADD COLUMN estado_pago TEXT NOT NULL DEFAULT 'PENDIENTE'"),
            ("ventas",       "estado_despacho",  "ALTER TABLE ventas ADD COLUMN estado_despacho TEXT NOT NULL DEFAULT 'PENDIENTE'"),
            ("suscripciones","dia_despacho",     "ALTER TABLE suscripciones ADD COLUMN dia_despacho TEXT NOT NULL DEFAULT ''"),
            ("clientes",      "tipo",               "ALTER TABLE clientes ADD COLUMN tipo TEXT NOT NULL DEFAULT 'CLIENTE'"),
            ("clientes",      "rut",                "ALTER TABLE clientes ADD COLUMN rut TEXT NOT NULL DEFAULT ''"),
            ("productos",     "precio_mayorista",   "ALTER TABLE productos ADD COLUMN precio_mayorista REAL NOT NULL DEFAULT 0"),
            ("productos",     "categoria",          "ALTER TABLE productos ADD COLUMN categoria TEXT NOT NULL DEFAULT 'pan'"),
            ("suscripciones", "entregas_realizadas","ALTER TABLE suscripciones ADD COLUMN entregas_realizadas INTEGER NOT NULL DEFAULT 0"),
            ("productos",     "peso_unitario_kg",   "ALTER TABLE productos ADD COLUMN peso_unitario_kg REAL NOT NULL DEFAULT 0"),
            ("gastos",        "recurrente",         "ALTER TABLE gastos ADD COLUMN recurrente INTEGER NOT NULL DEFAULT 0"),
            ("inventario",    "bodega",             "ALTER TABLE inventario ADD COLUMN bodega TEXT NOT NULL DEFAULT 'ingredientes'"),
            ("inventario",    "unidad",             "ALTER TABLE inventario ADD COLUMN unidad TEXT NOT NULL DEFAULT 'kg'"),
            ("pos_promociones","cantidad_minima",   "ALTER TABLE pos_promociones ADD COLUMN cantidad_minima INTEGER NOT NULL DEFAULT 0"),
            ("productos",     "subcategoria",       "ALTER TABLE productos ADD COLUMN subcategoria TEXT NOT NULL DEFAULT ''"),
            ("inventario", "categoria",    "ALTER TABLE inventario ADD COLUMN categoria TEXT NOT NULL DEFAULT ''"),
            ("inventario", "subcategoria", "ALTER TABLE inventario ADD COLUMN subcategoria TEXT NOT NULL DEFAULT ''"),
            ("recetas",    "inventario_id","ALTER TABLE recetas ADD COLUMN inventario_id INTEGER REFERENCES inventario(id) ON DELETE SET NULL"),
            ("inventario",      "producto_id",  "ALTER TABLE inventario ADD COLUMN producto_id INTEGER REFERENCES productos(id) ON DELETE SET NULL"),
            ("plan_produccion", "producto_id",  "ALTER TABLE plan_produccion ADD COLUMN producto_id INTEGER REFERENCES productos(id) ON DELETE SET NULL"),
            # Producción masa madre — reverse scheduling
            ("productos",        "masa_base",          "ALTER TABLE productos ADD COLUMN masa_base TEXT NOT NULL DEFAULT ''"),
            ("productos",        "baking_loss_pct",    "ALTER TABLE productos ADD COLUMN baking_loss_pct REAL NOT NULL DEFAULT 0"),
            ("productos",        "merma_tecnica_pct",  "ALTER TABLE productos ADD COLUMN merma_tecnica_pct REAL NOT NULL DEFAULT 0"),
            ("plan_produccion",  "fecha_amasado",      "ALTER TABLE plan_produccion ADD COLUMN fecha_amasado TEXT NOT NULL DEFAULT ''"),
            ("plan_produccion",  "fecha_horneado",     "ALTER TABLE plan_produccion ADD COLUMN fecha_horneado TEXT NOT NULL DEFAULT ''"),
            ("plan_produccion",  "batch_id",           "ALTER TABLE plan_produccion ADD COLUMN batch_id TEXT NOT NULL DEFAULT ''"),
            ("plan_produccion",  "ingredientes_json",  "ALTER TABLE plan_produccion ADD COLUMN ingredientes_json TEXT NOT NULL DEFAULT '[]'"),
            ("agenda", "tipo_agente",       "ALTER TABLE agenda ADD COLUMN tipo_agente TEXT DEFAULT NULL"),
            ("agenda", "telefono_destino",  "ALTER TABLE agenda ADD COLUMN telefono_destino TEXT DEFAULT NULL"),
            ("agenda", "payload_json",      "ALTER TABLE agenda ADD COLUMN payload_json TEXT DEFAULT NULL"),
            ("agenda", "ejecutado_en",      "ALTER TABLE agenda ADD COLUMN ejecutado_en TEXT DEFAULT NULL"),
            ("plan_produccion", "rendimiento_real", "ALTER TABLE plan_produccion ADD COLUMN rendimiento_real REAL DEFAULT NULL"),
            ("crm_leads", "razon_perdida", "ALTER TABLE crm_leads ADD COLUMN razon_perdida TEXT NOT NULL DEFAULT ''"),
            ("crm_leads", "cliente_id",    "ALTER TABLE crm_leads ADD COLUMN cliente_id INTEGER REFERENCES clientes(id) ON DELETE SET NULL"),
            ("crm_leads", "canal_origen_detalle", "ALTER TABLE crm_leads ADD COLUMN canal_origen_detalle TEXT NOT NULL DEFAULT ''"),
            # Automatización CRM
            ("crm_leads", "auto_discovery",       "ALTER TABLE crm_leads ADD COLUMN auto_discovery INTEGER NOT NULL DEFAULT 0"),
            ("crm_leads", "auto_contact_estado",  "ALTER TABLE crm_leads ADD COLUMN auto_contact_estado TEXT NOT NULL DEFAULT ''"),
            ("crm_leads", "auto_score",           "ALTER TABLE crm_leads ADD COLUMN auto_score INTEGER NOT NULL DEFAULT 0"),
            # Módulo Mayoristas B2B — columnas nuevas en crm_leads
            ("crm_leads", "tipo_negocio",             "ALTER TABLE crm_leads ADD COLUMN tipo_negocio TEXT NOT NULL DEFAULT ''"),
            ("crm_leads", "volumen_estimado_semanal",  "ALTER TABLE crm_leads ADD COLUMN volumen_estimado_semanal INTEGER NOT NULL DEFAULT 0"),
            ("crm_leads", "score_fit",                 "ALTER TABLE crm_leads ADD COLUMN score_fit INTEGER NOT NULL DEFAULT 0"),
            ("crm_leads", "nota_degustacion",          "ALTER TABLE crm_leads ADD COLUMN nota_degustacion TEXT NOT NULL DEFAULT ''"),
            ("crm_leads", "fecha_degustacion",         "ALTER TABLE crm_leads ADD COLUMN fecha_degustacion TEXT NOT NULL DEFAULT ''"),
            # Multi-local
            ("ventas",         "sucursal_id", "ALTER TABLE ventas ADD COLUMN sucursal_id INTEGER NOT NULL DEFAULT 1"),
            ("pos_turnos",     "sucursal_id", "ALTER TABLE pos_turnos ADD COLUMN sucursal_id INTEGER NOT NULL DEFAULT 1"),
            ("gastos",         "sucursal_id", "ALTER TABLE gastos ADD COLUMN sucursal_id INTEGER NOT NULL DEFAULT 1"),
            ("producto_lotes", "sucursal_id", "ALTER TABLE producto_lotes ADD COLUMN sucursal_id INTEGER NOT NULL DEFAULT 1"),
            ("usuarios",       "sucursal_id", "ALTER TABLE usuarios ADD COLUMN sucursal_id INTEGER DEFAULT NULL"),
        ]

        c.execute('''CREATE TABLE IF NOT EXISTS crm_etapa_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id    INTEGER NOT NULL REFERENCES crm_leads(id) ON DELETE CASCADE,
            etapa_from TEXT    NOT NULL DEFAULT '',
            etapa_to   TEXT    NOT NULL DEFAULT '',
            usuario    TEXT    NOT NULL DEFAULT '',
            fecha      TEXT    NOT NULL DEFAULT (datetime('now'))
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS crm_segmentos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre        TEXT NOT NULL,
            modulo        TEXT NOT NULL DEFAULT 'B2B',
            filtros_json  TEXT NOT NULL DEFAULT '{}',
            fecha_creacion TEXT NOT NULL DEFAULT (date('now'))
        )''')
        # ── Automatización CRM ────────────────────────────────────────────────
        c.execute('''CREATE TABLE IF NOT EXISTS crm_automatizacion_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha     TEXT    NOT NULL DEFAULT (datetime('now')),
            job       TEXT    NOT NULL DEFAULT '',
            accion    TEXT    NOT NULL DEFAULT '',
            lead_id   INTEGER REFERENCES crm_leads(id) ON DELETE SET NULL,
            detalle   TEXT    NOT NULL DEFAULT '',
            ok        INTEGER NOT NULL DEFAULT 1
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS crm_reglas_seguimiento (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            modulo              TEXT    NOT NULL DEFAULT 'B2B',
            etapa               TEXT    NOT NULL,
            dias_sin_actividad  INTEGER NOT NULL DEFAULT 3,
            accion              TEXT    NOT NULL DEFAULT 'crear_tarea',
            titulo_tarea        TEXT    NOT NULL DEFAULT '',
            prioridad           TEXT    NOT NULL DEFAULT 'media',
            cambio_temperatura  TEXT    NOT NULL DEFAULT '',
            cambio_etapa        TEXT    NOT NULL DEFAULT '',
            activa              INTEGER NOT NULL DEFAULT 1,
            creado_en           TEXT    NOT NULL DEFAULT (datetime('now'))
        )''')
        # Seed reglas default (solo si tabla vacía)
        if c.execute("SELECT COUNT(*) FROM crm_reglas_seguimiento").fetchone()[0] == 0:
            reglas_default = [
                ('B2B', 'PROSPECTO',         3,  'crear_tarea',  'Primer contacto pendiente',           'alta',   '',      ''),
                ('B2B', 'CONTACTADO',        4,  'crear_tarea',  'Hacer seguimiento — sin respuesta',   'alta',   '',      ''),
                ('B2B', 'CONTACTADO',        14, 'enfriar',      'Lead sin respuesta hace 14 días',     'media',  'COLD',  ''),
                ('B2B', 'CONTACTADO',        30, 'marcar_perdido','Cerrar como perdido — no respondió', 'baja',   '',      'PERDIDO'),
                ('B2B', 'MUESTRA_ENVIADA',   3,  'crear_tarea',  'Pedir feedback de la muestra',        'alta',   'WARM',  ''),
                ('B2B', 'MUESTRA_ENVIADA',   10, 'crear_tarea',  'Seguimiento final post-muestra',      'alta',   '',      ''),
                ('B2B', 'NEGOCIACION',       2,  'crear_tarea',  'Avanzar negociación — enviar propuesta','alta', 'HOT',   ''),
                ('B2B', 'NEGOCIACION',       7,  'crear_tarea',  'Negociación estancada — llamar',      'alta',   '',      ''),
                ('B2B', 'CONTRATO_FIRMADO',  3,  'crear_tarea',  'Iniciar onboarding',                  'alta',   '',      'ONBOARDING'),
                ('B2B', 'ONBOARDING',        7,  'crear_tarea',  'Confirmar primera entrega',           'alta',   '',      ''),
                ('B2B', 'CUENTA_ACTIVA',     30, 'crear_tarea',  'Check-in cuenta activa',              'media',  '',      ''),
                ('B2B', 'EN_RIESGO',         2,  'crear_tarea',  'URGENTE — Reactivar cuenta en riesgo','alta',   'HOT',   ''),
                ('B2C', 'NUEVO_CONTACTO',    2,  'crear_tarea',  'Convertir lead a primera compra',     'alta',   '',      ''),
                ('B2C', 'CALIFICADO',        5,  'crear_tarea',  'Cerrar primera compra',               'alta',   '',      ''),
                ('B2C', 'INACTIVO',          30, 'crear_tarea',  'Reactivar cliente inactivo',          'media',  '',      ''),
            ]
            c.executemany(
                """INSERT INTO crm_reglas_seguimiento
                   (modulo,etapa,dias_sin_actividad,accion,titulo_tarea,prioridad,cambio_temperatura,cambio_etapa)
                   VALUES (?,?,?,?,?,?,?,?)""",
                reglas_default
            )
        # Guard: inventario may not exist yet on first-run (created later in this function).
        _inventario_exists = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='inventario'"
        ).fetchone()

        for table, col, sql in migrations:
            if not _col_exists(c, table, col):
                c.execute(sql)

        # Rebuild inventario: UNIQUE(ingrediente) → UNIQUE(ingrediente, bodega, sucursal_id)
        # para permitir stock de terminados por sucursal. Idempotente.
        if _inventario_exists and not _col_exists(c, 'inventario', 'sucursal_id'):
            c.executescript("""
                CREATE TABLE inventario_new (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    ingrediente           TEXT    NOT NULL,
                    stock_kg              REAL    NOT NULL DEFAULT 0,
                    alerta_minimo_kg      REAL    NOT NULL DEFAULT 1,
                    proveedor             TEXT    NOT NULL DEFAULT '',
                    precio_kg             REAL    NOT NULL DEFAULT 0,
                    ultima_actualizacion  TEXT    NOT NULL DEFAULT (date('now')),
                    bodega                TEXT    NOT NULL DEFAULT 'ingredientes',
                    unidad                TEXT    NOT NULL DEFAULT 'kg',
                    categoria             TEXT    NOT NULL DEFAULT '',
                    subcategoria          TEXT    NOT NULL DEFAULT '',
                    producto_id           INTEGER REFERENCES productos(id) ON DELETE SET NULL,
                    sucursal_id           INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(ingrediente, bodega, sucursal_id)
                );
                INSERT INTO inventario_new
                    (id, ingrediente, stock_kg, alerta_minimo_kg, proveedor, precio_kg,
                     ultima_actualizacion, bodega, unidad, categoria, subcategoria, producto_id, sucursal_id)
                SELECT id, ingrediente, stock_kg, alerta_minimo_kg, proveedor, precio_kg,
                       ultima_actualizacion, bodega, unidad, categoria, subcategoria, producto_id, 1
                FROM inventario;
                DROP TABLE inventario;
                ALTER TABLE inventario_new RENAME TO inventario;
            """)

        # Backfill producto_id for productos_terminados rows that predate the FK column.
        # Only runs while unlinked rows exist; idempotent afterwards.
        unlinked = c.execute(
            "SELECT COUNT(*) FROM inventario WHERE bodega='productos_terminados' AND producto_id IS NULL"
        ).fetchone()[0] if _inventario_exists else 0
        if unlinked:
            c.execute("""
                UPDATE inventario
                SET producto_id = (
                    SELECT p.id FROM productos p
                    WHERE p.nombre = inventario.ingrediente LIMIT 1
                )
                WHERE bodega = 'productos_terminados' AND producto_id IS NULL
            """)
            # Reconcile productos.stock for newly linked rows so both counters agree
            c.execute("""
                UPDATE productos
                SET stock = (
                    SELECT inv.stock_kg FROM inventario inv
                    WHERE inv.bodega = 'productos_terminados' AND inv.producto_id = productos.id
                )
                WHERE id IN (
                    SELECT producto_id FROM inventario
                    WHERE bodega = 'productos_terminados'
                      AND producto_id IS NOT NULL AND stock_kg >= 0
                )
            """)

        # Auto-crear filas inventario para productos sin fila en productos_terminados.
        # Garantiza que productos.stock y inventario.stock_kg siempre estén vinculados.
        # Guard: inventario may not exist yet on first-run (created later in this function).
        if _inventario_exists:
            c.execute("""
                INSERT OR IGNORE INTO inventario
                    (ingrediente, bodega, stock_kg, alerta_minimo_kg, unidad,
                     categoria, subcategoria, producto_id, ultima_actualizacion)
                SELECT p.nombre, 'productos_terminados',
                       MAX(0, COALESCE(p.stock,0)), 0, 'unidades',
                       COALESCE(p.categoria,''), COALESCE(p.subcategoria,''),
                       p.id, date('now')
                FROM productos p
                WHERE p.activo=1
                  AND NOT EXISTS (
                      SELECT 1 FROM inventario i
                      WHERE i.bodega='productos_terminados' AND i.producto_id=p.id
                  )
            """)
        # Nota: ya no se clampean negativos en productos.stock. El descuento de
        # ventas es simétrico con la restauración (editar/borrar venta); un stock
        # negativo señala sobreventa real y se corrige con producción o ajuste.

        # Tabla de usuarios
        c.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre      TEXT    NOT NULL,
                email       TEXT    NOT NULL UNIQUE,
                password    TEXT    NOT NULL,
                rol         TEXT    NOT NULL DEFAULT 'usuario',
                activo      INTEGER NOT NULL DEFAULT 1,
                creado_en   TEXT    NOT NULL DEFAULT (datetime('now')),
                ultimo_login TEXT   NOT NULL DEFAULT '',
                permisos    TEXT    NOT NULL DEFAULT '[]',
                sucursal_id INTEGER DEFAULT NULL
            )
        """)
        if not _col_exists(c, 'usuarios', 'permisos'):
            c.execute("ALTER TABLE usuarios ADD COLUMN permisos TEXT NOT NULL DEFAULT '[]'")
        # Admin por defecto si no hay usuarios
        if c.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0:
            c.execute(
                "INSERT INTO usuarios (nombre,email,password,rol) VALUES (?,?,?,?)",
                ('Admin', 'admin@aurorabakers.cl',
                 generate_password_hash('aurora2024'), 'admin')
            )
            print("[auth] Usuario admin creado: admin@aurorabakers.cl / aurora2024")

        # ── Tablas CRM ──────────────────────────────────────────────────────────
        c.executescript("""
            CREATE TABLE IF NOT EXISTS crm_leads (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                modulo           TEXT    NOT NULL DEFAULT 'B2C',
                nombre           TEXT    NOT NULL,
                email            TEXT    NOT NULL DEFAULT '',
                telefono         TEXT    NOT NULL DEFAULT '',
                empresa          TEXT    NOT NULL DEFAULT '',
                cargo            TEXT    NOT NULL DEFAULT '',
                rut              TEXT    NOT NULL DEFAULT '',
                zona             TEXT    NOT NULL DEFAULT '',
                canal_origen     TEXT    NOT NULL DEFAULT 'manual',
                etapa            TEXT    NOT NULL DEFAULT 'NUEVO',
                temperatura      TEXT    NOT NULL DEFAULT 'COLD',
                tags_json        TEXT    NOT NULL DEFAULT '[]',
                propiedades_json TEXT    NOT NULL DEFAULT '{}',
                notas            TEXT    NOT NULL DEFAULT '',
                valor_potencial  REAL    NOT NULL DEFAULT 0,
                convertido       INTEGER NOT NULL DEFAULT 0,
                asignado_a       TEXT    NOT NULL DEFAULT '',
                fecha_creacion   TEXT    NOT NULL DEFAULT (date('now')),
                fecha_ultimo_contacto TEXT NOT NULL DEFAULT '',
                fecha_proximo_contacto TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS crm_interacciones (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id   INTEGER NOT NULL REFERENCES crm_leads(id) ON DELETE CASCADE,
                tipo      TEXT    NOT NULL DEFAULT 'email',
                direccion TEXT    NOT NULL DEFAULT 'saliente',
                asunto    TEXT    NOT NULL DEFAULT '',
                contenido TEXT    NOT NULL DEFAULT '',
                resultado TEXT    NOT NULL DEFAULT '',
                fecha     TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS crm_tareas (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id          INTEGER REFERENCES crm_leads(id) ON DELETE CASCADE,
                titulo           TEXT    NOT NULL,
                descripcion      TEXT    NOT NULL DEFAULT '',
                tipo             TEXT    NOT NULL DEFAULT 'seguimiento',
                prioridad        TEXT    NOT NULL DEFAULT 'media',
                fecha_vencimiento TEXT   NOT NULL DEFAULT '',
                completada       INTEGER NOT NULL DEFAULT 0,
                creado_en        TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS crm_email_templates (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre     TEXT    NOT NULL,
                asunto     TEXT    NOT NULL,
                cuerpo     TEXT    NOT NULL,
                modulo     TEXT    NOT NULL DEFAULT 'ambos',
                activo     INTEGER NOT NULL DEFAULT 1,
                creado_en  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS crm_whatsapp_templates (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre     TEXT    NOT NULL,
                cuerpo     TEXT    NOT NULL,
                modulo     TEXT    NOT NULL DEFAULT 'ambos',
                activo     INTEGER NOT NULL DEFAULT 1,
                creado_en  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # Seed email templates
        if c.execute("SELECT COUNT(*) FROM crm_email_templates").fetchone()[0] == 0:
            c.executemany(
                "INSERT INTO crm_email_templates (nombre,asunto,cuerpo,modulo) VALUES (?,?,?,?)",
                [
                    (
                        "Presentación Aurora Bakers — B2B",
                        "Pan artesanal de masa madre para tu negocio — Aurora Bakers",
                        """Estimado/a {nombre},

Me dirijo a usted desde Aurora Bakers, panadería artesanal especializada en pan de masa madre premium para el sector HORECA y retail gourmet en Santiago.

Nuestros productos destacan por:
• Fermentación lenta de 48 horas con masa madre natural
• Entrega diaria a las 07:00 hrs con temperatura controlada
• Gramaje exacto y consistencia garantizada

Trabajamos con restaurantes, cafeterías, hoteles y tiendas gourmet que valoran la calidad artesanal como propuesta de valor para sus clientes.

¿Podríamos agendar una cata sin compromiso esta semana?

Quedo atento/a,
Aurora Bakers | panypasta.cl""",
                        "B2B"
                    ),
                    (
                        "Primer contacto B2C — Perfil saludable",
                        "Descubre el pan de masa madre Aurora Bakers 🍞",
                        """Hola {nombre},

Te escribimos desde Aurora Bakers, panadería artesanal en Santiago especializada en pan de masa madre de fermentación lenta.

¿Por qué nuestro pan es diferente?
• Sin aditivos ni conservantes
• Fermentación natural de 48 horas — más digestible
• Horneado fresco cada mañana
• Disponible en suscripción semanal con despacho a domicilio

Si te interesa probar nuestra hogaza campesina o integral, escríbenos y con gusto te enviamos una muestra.

¡Saludos!
El equipo Aurora Bakers""",
                        "B2C"
                    ),
                    (
                        "Seguimiento post-visita — HORECA",
                        "Gracias por recibirnos — Propuesta Aurora Bakers",
                        """Estimado/a {nombre},

Fue un gusto conversar sobre cómo Aurora Bakers puede sumar valor a {empresa}.

Como acordamos, adjunto nuestra lista de precios y ficha técnica de productos. Destacamos:

→ Ciabattas y focaccias para servicio de mesa
→ Pan de molde premium para desayuno buffet
→ Hogazas artesanales para corte en sala

Todos nuestros panes se entregan antes de las 07:30 hrs, garantizando frescura máxima.

¿Puedo enviarle una primera muestra sin costo esta semana para que su equipo la evalúe?

Quedo atento,
Aurora Bakers | panypasta.cl""",
                        "B2B"
                    ),
                ]
            )

        # Migración crm_leads (debe correr después de que la tabla exista)
        if not _col_exists(c, 'crm_leads', 'direccion'):
            c.execute("ALTER TABLE crm_leads ADD COLUMN direccion TEXT NOT NULL DEFAULT ''")

        # ── Tabla memoria episódica agentes ──────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS agente_memoria (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha            TEXT    NOT NULL DEFAULT (datetime('now')),
                agente           TEXT    NOT NULL,
                pregunta         TEXT    NOT NULL DEFAULT '',
                respuesta_resumen TEXT   NOT NULL DEFAULT '',
                resultado        TEXT    NOT NULL DEFAULT 'ok',
                aprendizaje      TEXT    NOT NULL DEFAULT ''
            )
        """)

        # ── Tablas WhatsApp persistence ──────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS whatsapp_lid_cache (
                lid        TEXT PRIMARY KEY,
                telefono   TEXT NOT NULL,
                push_name  TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS whatsapp_conversaciones (
                telefono          TEXT PRIMARY KEY,
                tipo              TEXT NOT NULL DEFAULT 'minorista',
                mensajes_json     TEXT NOT NULL DEFAULT '[]',
                cliente_data_json TEXT NOT NULL DEFAULT '{}',
                pedido_guardado   INTEGER NOT NULL DEFAULT 0,
                updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # ── Tablas ERP: Inventario, Producción, Gastos, Agenda, Config ──────────
        c.executescript("""
            CREATE TABLE IF NOT EXISTS inventario (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                ingrediente           TEXT    NOT NULL,
                stock_kg              REAL    NOT NULL DEFAULT 0,
                alerta_minimo_kg      REAL    NOT NULL DEFAULT 1,
                proveedor             TEXT    NOT NULL DEFAULT '',
                precio_kg             REAL    NOT NULL DEFAULT 0,
                ultima_actualizacion  TEXT    NOT NULL DEFAULT (date('now')),
                bodega                TEXT    NOT NULL DEFAULT 'ingredientes',
                unidad                TEXT    NOT NULL DEFAULT 'kg',
                categoria             TEXT    NOT NULL DEFAULT '',
                subcategoria          TEXT    NOT NULL DEFAULT '',
                producto_id           INTEGER REFERENCES productos(id) ON DELETE SET NULL,
                sucursal_id           INTEGER NOT NULL DEFAULT 1,
                UNIQUE(ingrediente, bodega, sucursal_id)
            );
            CREATE TABLE IF NOT EXISTS recetas (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                producto_id  INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
                ingrediente  TEXT    NOT NULL,
                porcentaje   REAL    NOT NULL DEFAULT 0,
                UNIQUE(producto_id, ingrediente)
            );
            CREATE TABLE IF NOT EXISTS plan_produccion (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha            TEXT    NOT NULL,
                codigo_producto  TEXT    NOT NULL,
                nombre_producto  TEXT    NOT NULL,
                cantidad         INTEGER NOT NULL DEFAULT 0,
                estado           TEXT    NOT NULL DEFAULT 'pendiente',
                notas            TEXT    NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS gastos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha       TEXT    NOT NULL DEFAULT (date('now')),
                descripcion TEXT    NOT NULL,
                categoria   TEXT    NOT NULL DEFAULT 'General',
                monto       REAL    NOT NULL DEFAULT 0,
                proveedor   TEXT    NOT NULL DEFAULT '',
                comprobante TEXT    NOT NULL DEFAULT '',
                recurrente  INTEGER NOT NULL DEFAULT 0,
                sucursal_id INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS agenda (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo             TEXT    NOT NULL DEFAULT 'tarea',
                titulo           TEXT    NOT NULL,
                descripcion      TEXT    NOT NULL DEFAULT '',
                fecha            TEXT    NOT NULL DEFAULT (date('now')),
                hora             TEXT    NOT NULL DEFAULT '',
                completado       INTEGER NOT NULL DEFAULT 0,
                prioridad        TEXT    NOT NULL DEFAULT 'media',
                creado_en        TEXT    NOT NULL DEFAULT (datetime('now')),
                responsable      TEXT    NOT NULL DEFAULT '',
                fecha_fin        TEXT    NOT NULL DEFAULT '',
                tipo_agente      TEXT    DEFAULT NULL,
                telefono_destino TEXT    DEFAULT NULL,
                payload_json     TEXT    DEFAULT NULL,
                ejecutado_en     TEXT    DEFAULT NULL
            );
            CREATE TABLE IF NOT EXISTS agenda_subtareas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tarea_id    INTEGER NOT NULL REFERENCES agenda(id) ON DELETE CASCADE,
                titulo      TEXT    NOT NULL,
                responsable TEXT    NOT NULL DEFAULT '',
                fecha_inicio TEXT   NOT NULL DEFAULT '',
                fecha_fin   TEXT    NOT NULL DEFAULT '',
                completado  INTEGER NOT NULL DEFAULT 0,
                orden       INTEGER NOT NULL DEFAULT 0,
                creado_en   TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS config_negocio (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                clave       TEXT    NOT NULL UNIQUE,
                valor       TEXT    NOT NULL DEFAULT '',
                tipo        TEXT    NOT NULL DEFAULT 'texto',
                descripcion TEXT    NOT NULL DEFAULT ''
            );
        """)

        # Migrations: add new columns to existing tables
        if not _col_exists(c, 'agenda', 'responsable'):
            c.execute("ALTER TABLE agenda ADD COLUMN responsable TEXT NOT NULL DEFAULT ''")
        if not _col_exists(c, 'agenda', 'fecha_fin'):
            c.execute("ALTER TABLE agenda ADD COLUMN fecha_fin TEXT NOT NULL DEFAULT ''")

        # Seed inventario inicial con ingredientes base
        if c.execute("SELECT COUNT(*) FROM inventario").fetchone()[0] == 0:
            c.executemany(
                "INSERT INTO inventario (ingrediente, stock_kg, alerta_minimo_kg, precio_kg) VALUES (?,?,?,?)",
                [
                    ("harina_blanca",    20.0, 5.0, 900),
                    ("harina_integral",  10.0, 3.0, 1100),
                    ("masa_madre",        5.0, 1.0, 0),
                    ("agua",            100.0, 10.0, 0),
                    ("sal",               2.0, 0.5, 500),
                    ("nueces",            1.0, 0.3, 8000),
                    ("semillas_mix",      1.0, 0.3, 4500),
                    ("aceite",            2.0, 0.5, 2200),
                ]
            )

        # Seed config negocio con datos base
        if c.execute("SELECT COUNT(*) FROM config_negocio").fetchone()[0] == 0:
            import json as _json
            recetas = {
                "HC":   {"nombre": "Hogaza Campesina", "ingredientes": {"harina_blanca": 500, "agua": 375, "sal": 10, "masa_madre": 100}, "tiempo_hrs": 22, "rendimiento_g": 700},
                "HCN":  {"nombre": "Hogaza Nueces", "ingredientes": {"harina_blanca": 500, "agua": 375, "sal": 10, "masa_madre": 100, "nueces": 150}, "tiempo_hrs": 22, "rendimiento_g": 750},
                "HI":   {"nombre": "Hogaza Integral", "ingredientes": {"harina_integral": 500, "agua": 400, "sal": 10, "masa_madre": 100}, "tiempo_hrs": 24, "rendimiento_g": 700},
                "HIM":  {"nombre": "Hogaza Integral Multisemilla", "ingredientes": {"harina_integral": 500, "agua": 400, "sal": 10, "masa_madre": 100, "semillas_mix": 80}, "tiempo_hrs": 24, "rendimiento_g": 750},
                "PMB":  {"nombre": "Pan Molde Blanco", "ingredientes": {"harina_blanca": 500, "agua": 350, "sal": 10, "masa_madre": 100, "aceite": 30}, "tiempo_hrs": 20, "rendimiento_g": 600},
                "PMI":  {"nombre": "Pan Molde Integral", "ingredientes": {"harina_integral": 500, "agua": 370, "sal": 10, "masa_madre": 100, "aceite": 30}, "tiempo_hrs": 20, "rendimiento_g": 600},
                "PMIM": {"nombre": "Pan Molde Integral Multisemilla", "ingredientes": {"harina_integral": 500, "agua": 370, "sal": 10, "masa_madre": 100, "aceite": 30, "semillas_mix": 60}, "tiempo_hrs": 20, "rendimiento_g": 600},
                "CIA":  {"nombre": "Ciabatta", "ingredientes": {"harina_blanca": 100, "agua": 80, "sal": 2, "masa_madre": 20}, "tiempo_hrs": 18, "rendimiento_g": 120},
            }
            precios_mayoristas = {
                "ciabatta": {"precio": 2400, "formato": "6 unidades"},
                "hogaza_campesina": {"precio": 6500, "formato": "1 unidad"},
                "hogaza_integral": {"precio": 6500, "formato": "1 unidad"},
                "pan_molde_blanco": {"precio": 5800, "formato": "1 unidad"},
                "pan_molde_integral": {"precio": 5800, "formato": "1 unidad"},
                "combo_mixto": {"precio": 24000, "formato": "4 hogazas"},
            }
            configs = [
                ("recetas",             _json.dumps(recetas, ensure_ascii=False),          "json",   "Recetas de producción con ingredientes y tiempos"),
                ("precios_mayoristas",  _json.dumps(precios_mayoristas, ensure_ascii=False),"json",   "Precios para clientes HORECA/mayoristas"),
                ("dias_despacho",       _json.dumps(["martes","miércoles","jueves","viernes","sábado"]), "json", "Días en que se realizan despachos"),
                ("comunas_despacho",    _json.dumps(["Providencia","Ñuñoa","Santiago Centro","Recoleta","Las Condes","Vitacura","La Reina","Macul","San Miguel","Independencia"]), "json", "Comunas con despacho disponible"),
                ("costo_fijo_mensual",  _json.dumps({"Luz":56516,"Gas":47831,"Arriendo":350000,"Internet":19990,"Seguro":15000,"Mantención":20000,"Contabilidad":50000,"Packaging":30000}), "json", "Costos fijos mensuales en CLP"),
                ("nombre_negocio",      "Aurora Bakers",  "texto", "Nombre del negocio"),
                ("telefono_owner",      "56994891724",    "texto", "Teléfono del dueño"),
                ("email_owner",         "nwalbaum@gmail.com", "texto", "Email del dueño"),
                ("url_web",             "panypasta.cl",   "texto", "Sitio web"),
            ]
            c.executemany(
                "INSERT INTO config_negocio (clave, valor, tipo, descripcion) VALUES (?,?,?,?)",
                configs
            )

        # Seed WhatsApp templates
        if c.execute("SELECT COUNT(*) FROM crm_whatsapp_templates").fetchone()[0] == 0:
            c.executemany(
                "INSERT INTO crm_whatsapp_templates (nombre,cuerpo,modulo) VALUES (?,?,?)",
                [
                    (
                        "Presentación B2B — HORECA",
                        """Hola {nombre} 👋

Te escribo desde *Aurora Bakers*, panadería artesanal de masa madre en Santiago.

Trabajamos con restaurantes, cafeterías y hoteles que valoran el pan artesanal de calidad. Entregamos diario antes de las 07:30 hrs.

¿Te interesa recibir una muestra sin costo? Con gusto coordinamos 🍞

Saludos,
Aurora Bakers""",
                        "B2B"
                    ),
                    (
                        "Primer contacto B2C",
                        """Hola {nombre} 😊

Te escribo desde *Aurora Bakers*. Somos una panadería artesanal especializada en pan de masa madre de fermentación lenta — sin conservantes, recién horneado cada mañana.

Tenemos suscripción semanal con despacho a domicilio 🚚

¿Te gustaría probar? ¡Te mandamos más info!

Aurora Bakers 🍞""",
                        "B2C"
                    ),
                    (
                        "Seguimiento post-visita",
                        """Hola {nombre}, un gusto saludarte.

Quería hacer seguimiento a nuestra conversación sobre el pan de masa madre para *{empresa}*.

¿Pudiste evaluar la muestra? Cualquier consulta sobre precios o logística, estoy a disposición 👌

Quedo atento,
Aurora Bakers""",
                        "ambos"
                    ),
                ]
            )

        # ── Tablas POS ──────────────────────────────────────────────────────
        c.executescript("""
            CREATE TABLE IF NOT EXISTS pos_turnos (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id               INTEGER NOT NULL REFERENCES usuarios(id),
                fecha_apertura           TEXT NOT NULL,
                monto_inicial_efectivo   REAL NOT NULL DEFAULT 0,
                fecha_cierre             TEXT,
                monto_declarado_efectivo REAL,
                estado                   TEXT NOT NULL DEFAULT 'abierto',
                sucursal_id              INTEGER NOT NULL DEFAULT 1
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
            CREATE TABLE IF NOT EXISTS producto_lotes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                producto_id       INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
                fecha_elaboracion TEXT    NOT NULL,
                cantidad_inicial  REAL    NOT NULL,
                cantidad_actual   REAL    NOT NULL,
                merma             REAL    NOT NULL DEFAULT 0,
                notas             TEXT    NOT NULL DEFAULT '',
                creado_en         TEXT    NOT NULL DEFAULT (datetime('now')),
                sucursal_id       INTEGER NOT NULL DEFAULT 1
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
            CREATE TABLE IF NOT EXISTS pos_frecuentes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                producto_id INTEGER NOT NULL REFERENCES productos(id),
                orden       INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS pos_promociones (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre           TEXT NOT NULL,
                tipo             TEXT NOT NULL,
                valor            REAL NOT NULL DEFAULT 0,
                producto_id      INTEGER,
                activa           INTEGER NOT NULL DEFAULT 1,
                fecha_inicio     TEXT,
                fecha_fin        TEXT,
                cantidad_minima  INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS traspasos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha      TEXT    NOT NULL DEFAULT (datetime('now')),
                origen_id  INTEGER NOT NULL,
                destino_id INTEGER NOT NULL,
                usuario    TEXT    NOT NULL DEFAULT '',
                notas      TEXT    NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS traspaso_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                traspaso_id INTEGER NOT NULL REFERENCES traspasos(id) ON DELETE CASCADE,
                producto_id INTEGER NOT NULL,
                cantidad    REAL    NOT NULL
            );
        """)

        # Seed productos iniciales
        if c.execute("SELECT COUNT(*) FROM productos").fetchone()[0] == 0:
            c.executemany(
                "INSERT INTO productos (nombre,descripcion,precio,costo,stock,unidad) VALUES (?,?,?,?,?,?)",
                [
                    ("Hogaza Campesina",         "Pan de masa madre artesanal",    4200, 465,  10, "unidad"),
                    ("Hogaza Integral",           "Hogaza integral de masa madre",  4200, 627,  10, "unidad"),
                    ("Hogaza Integral Multisemilla","Con semillas tostadas",        4800, 795,   8, "unidad"),
                    ("Pan Molde Blanco",          "Pan molde suave, rebanado",      4200, 506,  15, "unidad"),
                    ("Pan Molde Integral",        "Pan molde integral, rebanado",   4200, 667,  15, "unidad"),
                    ("Ciabatta",                  "Ciabatta italiana de masa madre",  600, 184,  30, "unidad"),
                ]
            )

        # Índices para columnas calientes (consultas de reportes y joins frecuentes)
        c.executescript("""
            CREATE INDEX IF NOT EXISTS idx_ventas_fecha           ON ventas(fecha);
            CREATE INDEX IF NOT EXISTS idx_ventas_fecha_despacho  ON ventas(fecha_despacho);
            CREATE INDEX IF NOT EXISTS idx_venta_items_venta      ON venta_items(venta_id);
            CREATE INDEX IF NOT EXISTS idx_venta_items_producto   ON venta_items(producto_id);
            CREATE INDEX IF NOT EXISTS idx_crm_leads_telefono     ON crm_leads(telefono);
            CREATE INDEX IF NOT EXISTS idx_producto_lotes_prod    ON producto_lotes(producto_id);
            CREATE INDEX IF NOT EXISTS idx_lote_movimientos_venta ON lote_movimientos(venta_id);
            CREATE INDEX IF NOT EXISTS idx_ventas_sucursal        ON ventas(sucursal_id);
            CREATE INDEX IF NOT EXISTS idx_producto_lotes_suc     ON producto_lotes(sucursal_id);
        """)

        # Backfill lotes POR SUCURSAL: inventario es la verdad por local.
        # Si inventario(p,s) > Σ lotes(p,s), crear lote de ajuste en esa sucursal.
        desync_rows = c.execute("""
            SELECT i.producto_id, i.sucursal_id, i.stock_kg,
                   COALESCE((SELECT SUM(pl.cantidad_actual) FROM producto_lotes pl
                             WHERE pl.producto_id = i.producto_id
                               AND pl.sucursal_id = i.sucursal_id), 0) AS lotes
            FROM inventario i JOIN productos p ON p.id = i.producto_id
            WHERE i.bodega='productos_terminados' AND p.activo=1
        """).fetchall()
        for r in desync_rows:
            diff = round((r['stock_kg'] or 0) - r['lotes'], 3)
            if diff > 0:
                c.execute(
                    """INSERT INTO producto_lotes
                       (producto_id, sucursal_id, fecha_elaboracion, cantidad_inicial, cantidad_actual, notas)
                       VALUES (?, ?, date('now'), ?, ?, 'Ajuste sincronización lotes')""",
                    (r['producto_id'], r['sucursal_id'], diff, diff)
                )
        # productos.stock = TOTAL entre sucursales (inventario manda al arrancar)
        c.execute("""
            UPDATE productos SET stock = (
                SELECT COALESCE(SUM(i.stock_kg), 0) FROM inventario i
                WHERE i.bodega='productos_terminados' AND i.producto_id = productos.id)
            WHERE activo=1
              AND id IN (SELECT producto_id FROM inventario WHERE bodega='productos_terminados')
        """)

# ── Health ───────────────────────────────────────────────────────────────────

@app.route('/health')
def health():
    try:
        with db() as c:
            c.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        db_ok = False
    return jsonify({'status': 'ok' if db_ok else 'degraded', 'db': db_ok, 'ts': datetime.now().isoformat()})


# ── Autenticación global (before_request) ────────────────────────────────────

# Rutas públicas: login, health y la pantalla de cliente del POS (2do monitor)
_RUTAS_PUBLICAS = ('/login', '/logout', '/health', '/pos/cliente', '/api/pos/cliente/estado')

@app.before_request
def _global_auth():
    """Toda ruta requiere sesión web o X-Agent-Key válida, salvo whitelist.
    Con sesión activa, refresca rol/permisos desde la DB para que cambios de
    permisos o desactivación de usuario apliquen sin re-login."""
    p = request.path
    if p in _RUTAS_PUBLICAS or p.startswith('/static/'):
        return

    if session.get('user_id'):
        with db() as c:
            u = c.execute("SELECT rol, activo, permisos, sucursal_id FROM usuarios WHERE id=?",
                          (session['user_id'],)).fetchone()
        if not u or not u['activo']:
            session.clear()
            if p.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('page_login', next=p))
        session['user_rol'] = u['rol']
        permisos = _json_mod.loads(u['permisos'] or '[]')
        if not permisos and u['rol'] != 'admin':
            permisos = MODULOS_DEFAULT
        session['user_permisos'] = permisos
        session['user_sucursal'] = u['sucursal_id']
        return

    key = request.headers.get('X-Agent-Key', '') or request.args.get('key', '')
    if AGENT_API_KEY and key == AGENT_API_KEY:
        return

    if p.startswith('/api/'):
        return jsonify({'error': 'Unauthorized'}), 401
    return redirect(url_for('page_login', next=p))


# ── Autenticación de usuarios ─────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('page_login', next=request.path))
        return f(*args, **kwargs)
    return decorated

def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    with db() as c:
        row = c.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        u = current_user()
        if not u or u['rol'] != 'admin':
            return jsonify({'error': 'Se requiere rol admin'}), 403
        return f(*args, **kwargs)
    return decorated

# ── Módulos y permisos ────────────────────────────────────────────────────────
import json as _json_mod

MODULOS = [
    ('pos',               'POS / Caja',           'bi-cash-register'),
    ('ventas',            'Ventas',               'bi-receipt'),
    ('despacho',          'Despacho',             'bi-truck'),
    ('clientes',          'Clientes',             'bi-people'),
    ('productos',         'Productos',            'bi-basket'),
    ('suscripciones',     'Suscripciones',        'bi-calendar-check'),
    ('mayoristas',        'Mayoristas',           'bi-shop'),
    ('crm',               'CRM',                  'bi-diagram-3'),
    ('reportes',          'Resumen Reportes',     'bi-speedometer2'),
    ('reporte_ventas',    'Reporte Ventas',       'bi-table'),
    ('reporte_produccion','Reporte Producción',   'bi-clipboard-data'),
    ('reporte_despacho',  'Reporte Despacho',     'bi-map'),
    ('finanzas',          'Finanzas',             'bi-currency-dollar'),
    ('produccion',        'Producción',           'bi-fire'),
    ('inventario',        'Inventario',           'bi-boxes'),
    ('traspasos',         'Traspasos',            'bi-arrow-left-right'),
    ('gastos',            'Gastos',               'bi-cash-coin'),
    ('agenda',            'Agenda',               'bi-calendar3'),
    ('config_negocio',    'Configuración',        'bi-gear'),
    ('agentes',           'Agentes',              'bi-robot'),
]
MODULOS_DEFAULT = ['pos', 'ventas', 'clientes', 'despacho', 'agenda']

# Perfiles preset por función — rellenan los checkboxes de módulos al crear usuario
PERFILES = {
    'cajero':     ['pos', 'ventas', 'clientes', 'despacho'],
    'produccion': ['produccion', 'inventario', 'traspasos', 'reporte_produccion', 'agenda'],
    'encargado':  ['pos', 'ventas', 'clientes', 'despacho', 'suscripciones', 'traspasos',
                   'reportes', 'reporte_ventas', 'agenda'],
    'contador':   ['finanzas', 'gastos', 'reportes', 'reporte_ventas'],
}

def has_module(key):
    """True si el usuario actual tiene acceso al módulo."""
    if session.get('user_rol') == 'admin':
        return True
    return key in session.get('user_permisos', [])

def module_required(key):
    """Decorator de ruta: requiere login + permiso de módulo."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get('user_id'):
                return redirect(url_for('page_login', next=request.path))
            if not has_module(key):
                return redirect(url_for('page_inicio'))
            return f(*args, **kwargs)
        return decorated
    return decorator

# ── Sucursales: helpers de contexto ──────────────────────────────────────────

def _sucursal_fija():
    """Sucursal del usuario actual, o None si tiene acceso a todas."""
    return session.get('user_sucursal') or None

def _sucursal_escritura(d):
    """Sucursal a usar al crear venta/turno/gasto. Usuario con sucursal fija:
    la suya SIEMPRE (ignora lo que mande el cliente). Si no, body o 1."""
    fija = _sucursal_fija()
    if fija:
        return int(fija)
    try:
        return int((d or {}).get('sucursal_id') or 1)
    except (TypeError, ValueError):
        return 1

def _sucursal_lectura():
    """Filtro de sucursal para reportes: int o None (= total). Usuario con
    sucursal fija solo ve la suya."""
    fija = _sucursal_fija()
    if fija:
        return int(fija)
    s = request.args.get('sucursal_id', '')
    try:
        return int(s) if s else None
    except ValueError:
        return None

def _suc_filtro(col='sucursal_id'):
    """(sql_extra, params_extra) para anexar a un WHERE existente."""
    suc = _sucursal_lectura()
    return (f" AND {col}=?", [suc]) if suc else ('', [])


@app.context_processor
def inject_nav_permisos():
    return {
        'user_permisos': session.get('user_permisos', []),
        'user_es_admin': session.get('user_rol') == 'admin',
    }

@app.route('/login', methods=['GET','POST'])
def page_login():
    if session.get('user_id'):
        return redirect('/')
    error = None
    if request.method == 'POST':
        email    = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        with db() as c:
            u = c.execute("SELECT * FROM usuarios WHERE email=? AND activo=1", (email,)).fetchone()
        if u and check_password_hash(u['password'], password):
            session.permanent = False  # expira al cerrar el navegador
            session['user_id']     = u['id']
            session['user_nombre'] = u['nombre']
            session['user_rol']    = u['rol']
            raw = u['permisos'] if 'permisos' in u.keys() else '[]'
            permisos = _json_mod.loads(raw or '[]')
            if not permisos and u['rol'] != 'admin':
                permisos = MODULOS_DEFAULT
            session['user_permisos'] = permisos
            session['user_sucursal'] = u['sucursal_id'] if 'sucursal_id' in u.keys() else None
            with db() as c:
                c.execute("UPDATE usuarios SET ultimo_login=? WHERE id=?",
                          (datetime.now().strftime('%Y-%m-%d %H:%M'), u['id']))
            next_url = request.args.get('next', '/')
            # Solo rutas internas: evita open redirect
            if not next_url.startswith('/') or next_url.startswith('//'):
                next_url = '/'
            return redirect(next_url)
        error = 'Correo o contraseña incorrectos'
    return render_template('login.html', error=error)

@app.route('/logout')
def page_logout():
    session.clear()
    return redirect('/login')

# ── Admin: gestión de usuarios ────────────────────────────────────────────────

@app.route('/admin/usuarios')
@login_required
def page_admin_usuarios():
    u = current_user()
    if u['rol'] != 'admin':
        return redirect('/')
    return render_template('admin_usuarios.html', active='admin', yo=u)

@app.route('/api/admin/usuarios', methods=['GET'])
@login_required
def api_admin_usuarios_list():
    u = current_user()
    if u['rol'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    with db() as c:
        rows = c.execute("SELECT id,nombre,email,rol,activo,creado_en,ultimo_login,permisos,sucursal_id FROM usuarios ORDER BY id").fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/admin/modulos', methods=['GET'])
@login_required
def api_admin_modulos():
    """Lista de módulos disponibles para asignar permisos."""
    return jsonify([{'key': k, 'nombre': n, 'icono': i} for k, n, i in MODULOS])

@app.route('/api/admin/perfiles', methods=['GET'])
@admin_required
def api_admin_perfiles():
    with db() as c:
        sucs = [dict(r) for r in c.execute("SELECT id, nombre FROM sucursales WHERE activa=1 ORDER BY id")]
    return jsonify({'perfiles': PERFILES, 'sucursales': sucs})

@app.route('/api/admin/usuarios', methods=['POST'])
@login_required
def api_admin_usuarios_create():
    u = current_user()
    if u['rol'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    d = request.json
    if not d.get('email') or not d.get('password'):
        return jsonify({'error': 'Email y contraseña son requeridos'}), 400
    permisos = _json_mod.dumps(d.get('permisos', MODULOS_DEFAULT))
    sucursal_id = d.get('sucursal_id') or None
    try:
        with db() as c:
            c.execute(
                "INSERT INTO usuarios (nombre,email,password,rol,permisos,sucursal_id) VALUES (?,?,?,?,?,?)",
                (d.get('nombre',''), d['email'].lower().strip(),
                 generate_password_hash(d['password']), d.get('rol','usuario'), permisos, sucursal_id)
            )
        return jsonify({'ok': True}), 201
    except Exception:
        return jsonify({'error': 'El correo ya está registrado'}), 400

@app.route('/api/admin/usuarios/<int:uid>', methods=['PUT'])
@login_required
def api_admin_usuarios_update(uid):
    u = current_user()
    if u['rol'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    d = request.json
    with db() as c:
        if 'password' in d and d['password']:
            c.execute("UPDATE usuarios SET password=? WHERE id=?",
                      (generate_password_hash(d['password']), uid))
        for col in ('nombre', 'email', 'rol', 'activo'):
            if col in d:
                c.execute(f"UPDATE usuarios SET {col}=? WHERE id=?", (d[col], uid))
        if 'permisos' in d:
            c.execute("UPDATE usuarios SET permisos=? WHERE id=?",
                      (_json_mod.dumps(d['permisos']), uid))
        if 'sucursal_id' in d:
            c.execute("UPDATE usuarios SET sucursal_id=? WHERE id=?", (d['sucursal_id'] or None, uid))
    return jsonify({'ok': True})

@app.route('/api/admin/usuarios/<int:uid>', methods=['DELETE'])
@login_required
def api_admin_usuarios_delete(uid):
    u = current_user()
    if u['rol'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    if uid == u['id']:
        return jsonify({'error': 'No puedes eliminar tu propio usuario'}), 400
    with db() as c:
        c.execute("UPDATE usuarios SET activo=0 WHERE id=?", (uid,))
    return jsonify({'ok': True})

@app.route('/api/admin/mi-perfil', methods=['POST'])
@login_required
def api_admin_mi_perfil():
    """Cualquier usuario puede cambiar su propia contraseña."""
    u   = current_user()
    d   = request.json
    old = d.get('password_actual','')
    new = d.get('password_nuevo','')
    if not old or not new:
        return jsonify({'error': 'Completa ambos campos'}), 400
    with db() as c:
        row = c.execute("SELECT password FROM usuarios WHERE id=?", (u['id'],)).fetchone()
        if not check_password_hash(row['password'], old):
            return jsonify({'error': 'Contraseña actual incorrecta'}), 400
        c.execute("UPDATE usuarios SET password=? WHERE id=?",
                  (generate_password_hash(new), u['id']))
    return jsonify({'ok': True})


# ── Páginas ───────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():              return redirect('/ventas')

@app.route('/inicio')
@login_required
def page_inicio():
    """Página de inicio: redirige al primer módulo habilitado del usuario."""
    modulos_orden = ['pos', 'ventas', 'despacho', 'reportes', 'produccion', 'clientes', 'agenda']
    for m in modulos_orden:
        if has_module(m):
            rutas = {
                'pos': '/pos/caja', 'ventas': '/ventas', 'despacho': '/despacho',
                'reportes': '/reportes', 'produccion': '/produccion',
                'clientes': '/clientes', 'agenda': '/agenda',
            }
            return redirect(rutas[m])
    return redirect('/movil')

@app.route('/movil')
@login_required
def page_movil():
    return render_template('movil.html', active='movil')

@app.route('/ventas')
@module_required('ventas')
def page_ventas():        return render_template('ventas.html',        active='ventas')

@app.route('/clientes')
@module_required('clientes')
def page_clientes():      return render_template('clientes.html',      active='clientes')

@app.route('/productos')
@module_required('productos')
def page_productos():     return render_template('productos.html',      active='productos')

@app.route('/suscripciones')
@module_required('suscripciones')
def page_suscripciones(): return render_template('suscripciones.html', active='suscripciones')

@app.route('/mayoristas')
@module_required('mayoristas')
def page_mayoristas(): return render_template('mayoristas.html', active='mayoristas')

@app.route('/mayoristas/clientes')
@module_required('mayoristas')
def page_mayoristas_clientes():
    return render_template('mayoristas_clientes.html', active='mayoristas')

@app.route('/mayoristas/clientes/<int:mid>')
@module_required('mayoristas')
def page_mayoristas_cliente(mid):
    return render_template('mayoristas_cliente.html', active='mayoristas', mayorista_id=mid)

@app.route('/mayoristas/pedidos')
@module_required('mayoristas')
def page_mayoristas_pedidos():
    return render_template('mayoristas_pedidos.html', active='mayoristas')

@app.route('/mayoristas/pedidos/<int:pid>')
@module_required('mayoristas')
def page_mayoristas_pedido(pid):
    return render_template('mayoristas_pedido.html', active='mayoristas', pedido_id=pid)

@app.route('/mayoristas/produccion')
@module_required('mayoristas')
def page_mayoristas_produccion():
    return render_template('mayoristas_produccion.html', active='mayoristas')

@app.route('/mayoristas/precios')
@module_required('mayoristas')
def page_mayoristas_precios():
    return render_template('mayoristas_precios.html', active='mayoristas')

@app.route('/mayoristas/cobranza')
@module_required('mayoristas')
def page_mayoristas_cobranza():
    return render_template('mayoristas_cobranza.html', active='mayoristas')

@app.route('/mayoristas/ingresos')
@module_required('mayoristas')
def page_mayoristas_ingresos():
    return render_template('mayoristas_ingresos.html', active='mayoristas')

@app.route('/mayoristas/crm')
@module_required('mayoristas')
def page_mayoristas_crm():
    return render_template('mayoristas_crm.html', active='mayoristas')

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

@app.route('/reporte-ventas')
@module_required('reporte_ventas')
def page_reporte_ventas(): return render_template('reporte_ventas.html', active='reporte_ventas')

@app.route('/finanzas')
@module_required('finanzas')
def page_finanzas():      return render_template('finanzas.html',        active='finanzas')

@app.route('/despacho')
@module_required('despacho')
def page_despacho():      return render_template('despacho.html',        active='despacho')

# ── API: Sucursales ───────────────────────────────────────────────────────────

@app.route('/api/sucursales')
@login_required
def api_sucursales():
    with db() as c:
        rows = c.execute("SELECT * FROM sucursales WHERE activa=1 ORDER BY id").fetchall()
    return jsonify({'sucursales': [dict(r) for r in rows], 'fija': _sucursal_fija()})

@app.route('/api/sucursales/<int:sid>', methods=['PUT'])
@admin_required
def api_sucursales_update(sid):
    d = request.json or {}
    with db() as c:
        if not c.execute("SELECT id FROM sucursales WHERE id=?", (sid,)).fetchone():
            return jsonify({'error': 'No encontrada'}), 404
        for col in ('nombre', 'direccion', 'activa'):
            if col in d:
                c.execute(f"UPDATE sucursales SET {col}=? WHERE id=?", (d[col], sid))
    return jsonify({'ok': True})

# ── API: Productos ────────────────────────────────────────────────────────────

@app.route('/api/productos', methods=['GET'])
def api_productos():
    with db() as c:
        rows = c.execute("SELECT * FROM productos WHERE activo=1 ORDER BY nombre").fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/productos', methods=['POST'])
def api_productos_create():
    d = request.json
    masa_base         = d.get('masa_base', '').strip()
    baking_loss_pct   = float(d.get('baking_loss_pct', 0) or 0)
    merma_tecnica_pct = float(d.get('merma_tecnica_pct', 0) or 0)
    with db() as c:
        cur = c.execute(
            "INSERT INTO productos (nombre,descripcion,precio,precio_mayorista,costo,stock,unidad,categoria,subcategoria,masa_base,baking_loss_pct,merma_tecnica_pct) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d['nombre'], d.get('descripcion',''), float(d['precio']),
             float(d.get('precio_mayorista',0)),
             float(d.get('costo',0)), float(d.get('stock',0)), d.get('unidad','unidad'),
             d.get('categoria','pan'), d.get('subcategoria',''),
             masa_base, baking_loss_pct, merma_tecnica_pct)
        )
        return jsonify(dict(c.execute("SELECT * FROM productos WHERE id=?", (cur.lastrowid,)).fetchone())), 201

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
                'producto_id':               p['id'],
                'costo_teorico':             round(costo_teorico, 2),
                'ingredientes_sin_vincular': sin_vincular
            })
    return jsonify(resultado)

@app.route('/api/productos/<int:pid>', methods=['PUT'])
def api_productos_update(pid):
    d = request.json
    masa_base         = d.get('masa_base', '').strip()
    baking_loss_pct   = float(d.get('baking_loss_pct', 0) or 0)
    merma_tecnica_pct = float(d.get('merma_tecnica_pct', 0) or 0)
    with db() as c:
        c.execute(
            "UPDATE productos SET nombre=?,descripcion=?,precio=?,precio_mayorista=?,costo=?,stock=?,unidad=?,categoria=?,subcategoria=?,activo=?,masa_base=?,baking_loss_pct=?,merma_tecnica_pct=? WHERE id=?",
            (d['nombre'], d.get('descripcion',''), float(d['precio']),
             float(d.get('precio_mayorista',0)),
             float(d.get('costo',0)), float(d.get('stock',0)), d.get('unidad','unidad'),
             d.get('categoria','pan'), d.get('subcategoria',''), int(d.get('activo',1)),
             masa_base, baking_loss_pct, merma_tecnica_pct, pid)
        )
        return jsonify(dict(c.execute("SELECT * FROM productos WHERE id=?", (pid,)).fetchone()))

@app.route('/api/productos/<int:pid>', methods=['DELETE'])
def api_productos_delete(pid):
    with db() as c:
        c.execute("UPDATE productos SET activo=0 WHERE id=?", (pid,))
        return jsonify({'ok': True})

# ── API: Producto-Lotes ───────────────────────────────────────────────────────

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
                """SELECT id, fecha_elaboracion, cantidad_inicial, cantidad_actual, merma, notas, creado_en, sucursal_id
                   FROM producto_lotes WHERE producto_id=? ORDER BY fecha_elaboracion ASC""",
                (p['id'],)
            ).fetchall()
            stock_lotes = sum(l['cantidad_actual'] for l in lotes)
            resultado.append({
                'producto_id': p['id'],
                'nombre':      p['nombre'],
                'unidad':      p['unidad'] if p['unidad'] else 'u',
                'stock_total': stock_lotes,
                'lotes':       [dict(l) for l in lotes]
            })
    return jsonify(resultado)

@app.route('/api/producto-lotes', methods=['POST'])
@login_required
def api_producto_lotes_create():
    d = request.get_json(silent=True) or {}
    try:
        prod_id = int(d.get('producto_id', 0))
    except (ValueError, TypeError):
        return jsonify({'error': 'producto_id inválido'}), 400
    fecha    = d.get('fecha_elaboracion', str(date.today()))
    cantidad = float(d.get('cantidad', 0))
    notas    = d.get('notas', '')
    if not prod_id or cantidad <= 0:
        return jsonify({'error': 'producto_id y cantidad requeridos'}), 400
    sucursal = _sucursal_escritura(d)
    with db() as c:
        prod = c.execute("SELECT id, nombre FROM productos WHERE id=? AND activo=1", (prod_id,)).fetchone()
        if not prod:
            return jsonify({'error': 'Producto no encontrado'}), 404
        c.execute(
            """INSERT INTO producto_lotes (producto_id, sucursal_id, fecha_elaboracion, cantidad_inicial, cantidad_actual, notas)
               VALUES (?,?,?,?,?,?)""",
            (prod_id, sucursal, fecha, cantidad, cantidad, notas)
        )
        lote_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Crear lote = entra stock: mantener los 3 contadores en sync
        c.execute("UPDATE productos SET stock=stock+? WHERE id=?", (cantidad, prod_id))
        inv_id = _get_or_create_inv_terminado(c, prod_id, prod['nombre'], sucursal)
        c.execute(
            "UPDATE inventario SET stock_kg=stock_kg+?, ultima_actualizacion=date('now') WHERE id=?",
            (cantidad, inv_id)
        )
    return jsonify({'ok': True, 'id': lote_id}), 201

@app.route('/api/producto-lotes/<int:lid>/ajustar', methods=['PUT'])
@login_required
def api_producto_lotes_ajustar(lid):
    d     = request.get_json(silent=True) or {}
    try:
        delta = float(d.get('delta', 0))
    except (ValueError, TypeError):
        return jsonify({'error': 'delta debe ser un número'}), 400
    tipo  = d.get('tipo', 'ajuste')   # 'merma' | 'ajuste'
    tipo  = tipo if tipo in ('merma', 'ajuste') else 'ajuste'
    notas = d.get('notas', '')
    if delta == 0:
        return jsonify({'error': 'delta no puede ser 0'}), 400
    with db() as c:
        lote = c.execute("SELECT * FROM producto_lotes WHERE id=?", (lid,)).fetchone()
        if not lote:
            return jsonify({'error': 'Lote no encontrado'}), 404
        nueva_cantidad = max(0.0, lote['cantidad_actual'] + delta)
        deducido = lote['cantidad_actual'] - nueva_cantidad  # actual amount removed (always >= 0)
        c.execute(
            "UPDATE producto_lotes SET cantidad_actual=? WHERE id=?",
            (nueva_cantidad, lid)
        )
        if tipo == 'merma' and delta < 0:
            c.execute(
                "UPDATE producto_lotes SET merma=merma+? WHERE id=?",
                (deducido, lid)
            )
        c.execute(
            "INSERT INTO lote_movimientos (lote_id, tipo, cantidad, notas) VALUES (?,?,?,?)",
            (lid, tipo, delta, notas)
        )
        # Sincronizar productos.stock e inventario.stock_kg con el delta efectivo:
        # si el lote se clampeó en 0, solo salió lo que realmente había.
        delta_efectivo = nueva_cantidad - lote['cantidad_actual']
        c.execute(
            "UPDATE productos SET stock=stock+? WHERE id=?",
            (delta_efectivo, lote['producto_id'])
        )
        c.execute(
            """UPDATE inventario SET stock_kg=stock_kg+?, ultima_actualizacion=date('now')
               WHERE bodega='productos_terminados' AND producto_id=? AND sucursal_id=?""",
            (delta_efectivo, lote['producto_id'], lote['sucursal_id'])
        )
        lote_updated = c.execute("SELECT * FROM producto_lotes WHERE id=?", (lid,)).fetchone()
    return jsonify(dict(lote_updated))

# ── API: Clientes ─────────────────────────────────────────────────────────────

def _enrich_cliente(c, row):
    r = dict(row)
    r['total_compras'] = c.execute(
        "SELECT COALESCE(SUM(total),0) FROM ventas WHERE cliente_id=?", (r['id'],)).fetchone()[0]
    r['ultima_compra'] = c.execute(
        "SELECT MAX(fecha) FROM ventas WHERE cliente_id=?", (r['id'],)).fetchone()[0]
    r['num_compras'] = c.execute(
        "SELECT COUNT(*) FROM ventas WHERE cliente_id=?", (r['id'],)).fetchone()[0]
    return r

@app.route('/api/clientes', methods=['GET'])
def api_clientes():
    q = request.args.get('q','')
    with db() as c:
        if q:
            rows = c.execute(
                "SELECT * FROM clientes WHERE nombre LIKE ? OR email LIKE ? OR telefono LIKE ? ORDER BY nombre",
                (f'%{q}%',f'%{q}%',f'%{q}%')).fetchall()
        else:
            rows = c.execute("SELECT * FROM clientes ORDER BY nombre").fetchall()
        return jsonify([_enrich_cliente(c, r) for r in rows])

@app.route('/api/clientes', methods=['POST'])
def api_clientes_create():
    d = request.json
    with db() as c:
        cur = c.execute(
            "INSERT INTO clientes (nombre,email,telefono,direccion,notas,es_suscriptor,tipo,rut) VALUES (?,?,?,?,?,?,?,?)",
            (d['nombre'], d.get('email',''), d.get('telefono',''),
             d.get('direccion',''), d.get('notas',''), int(d.get('es_suscriptor',0)),
             d.get('tipo','CLIENTE'), d.get('rut',''))
        )
        return jsonify(_enrich_cliente(c, c.execute("SELECT * FROM clientes WHERE id=?", (cur.lastrowid,)).fetchone())), 201

@app.route('/api/clientes/<int:cid>', methods=['PUT'])
def api_clientes_update(cid):
    d = request.json
    with db() as c:
        c.execute(
            "UPDATE clientes SET nombre=?,email=?,telefono=?,direccion=?,notas=?,es_suscriptor=?,tipo=?,rut=? WHERE id=?",
            (d['nombre'], d.get('email',''), d.get('telefono',''),
             d.get('direccion',''), d.get('notas',''), int(d.get('es_suscriptor',0)),
             d.get('tipo','CLIENTE'), d.get('rut',''), cid)
        )
        return jsonify(_enrich_cliente(c, c.execute("SELECT * FROM clientes WHERE id=?", (cid,)).fetchone()))

@app.route('/api/clientes/<int:cid>', methods=['DELETE'])
def api_clientes_delete(cid):
    with db() as c:
        # Limpiar referencias FK antes de borrar: las ventas históricas se
        # conservan sin cliente; suscripciones y pedidos fijos se eliminan.
        c.execute("UPDATE ventas SET cliente_id=NULL WHERE cliente_id=?", (cid,))
        c.execute("DELETE FROM suscripciones WHERE cliente_id=?", (cid,))
        c.execute("DELETE FROM mayorista_ajustes WHERE cliente_id=?", (cid,))
        c.execute("DELETE FROM mayorista_pedidos WHERE cliente_id=?", (cid,))
        c.execute("DELETE FROM clientes WHERE id=?", (cid,))
        return jsonify({'ok': True})

@app.route('/api/clientes/<int:cid>/ventas')
def api_cliente_ventas(cid):
    with db() as c:
        ventas = c.execute(
            "SELECT * FROM ventas WHERE cliente_id=? ORDER BY fecha DESC, creado_en DESC LIMIT 50", (cid,)
        ).fetchall()
        result = []
        for v in ventas:
            vd = dict(v)
            vd['items'] = [dict(i) for i in c.execute(
                "SELECT vi.*,p.nombre FROM venta_items vi JOIN productos p ON p.id=vi.producto_id WHERE vi.venta_id=?",
                (v['id'],)).fetchall()]
            result.append(vd)
        return jsonify(result)

# ── API: Ventas ───────────────────────────────────────────────────────────────

@app.route('/api/ventas', methods=['GET'])
def api_ventas():
    desde           = request.args.get('desde', str(date.today()))
    hasta           = request.args.get('hasta', str(date.today()))
    canal           = request.args.get('canal', '')
    tipo_cliente    = request.args.get('tipo_cliente', '')
    estado_pago     = request.args.get('estado_pago', '')
    estado_despacho = request.args.get('estado_despacho', '')
    cliente_id      = request.args.get('cliente_id', '')
    con_despacho    = request.args.get('con_despacho', '')
    min_total       = request.args.get('min_total', '')
    max_total       = request.args.get('max_total', '')
    fecha_despacho_desde = request.args.get('fecha_despacho_desde', '')
    fecha_despacho_hasta = request.args.get('fecha_despacho_hasta', '')

    with db() as c:
        q = ("SELECT v.*,c.nombre AS cliente_nombre "
             "FROM ventas v LEFT JOIN clientes c ON c.id=v.cliente_id "
             "WHERE v.fecha BETWEEN ? AND ?")
        params = [desde, hasta]
        if canal:
            q += " AND v.canal=?"; params.append(canal)
        if tipo_cliente:
            q += " AND v.tipo_cliente=?"; params.append(tipo_cliente)
        if estado_pago:
            q += " AND v.estado_pago=?"; params.append(estado_pago)
        if estado_despacho:
            q += " AND v.estado_despacho=?"; params.append(estado_despacho)
        if cliente_id:
            q += " AND v.cliente_id=?"; params.append(int(cliente_id))
        if con_despacho != '':
            q += " AND v.con_despacho=?"; params.append(int(con_despacho))
        if min_total:
            q += " AND v.total>=?"; params.append(float(min_total))
        if max_total:
            q += " AND v.total<=?"; params.append(float(max_total))
        if fecha_despacho_desde:
            q += " AND v.fecha_despacho>=?"; params.append(fecha_despacho_desde)
        if fecha_despacho_hasta:
            q += " AND v.fecha_despacho<=?"; params.append(fecha_despacho_hasta)
        sw, sp = _suc_filtro('v.sucursal_id')
        q += sw; params += sp
        q += " ORDER BY v.fecha DESC, v.creado_en DESC"
        rows = c.execute(q, params).fetchall()
        result = []
        for v in rows:
            vd = dict(v)
            vd['items'] = [dict(i) for i in c.execute(
                "SELECT vi.*,p.nombre FROM venta_items vi JOIN productos p ON p.id=vi.producto_id WHERE vi.venta_id=?",
                (v['id'],)).fetchall()]
            result.append(vd)
        return jsonify(result)

@app.route('/api/ventas', methods=['POST'])
def api_ventas_create():
    d = request.json
    items = d.get('items', [])
    if not items:
        return jsonify({'error': 'Agrega al menos un producto'}), 400

    total = sum(float(i['cantidad']) * float(i['precio_unitario']) for i in items)

    con_despacho = int(d.get('con_despacho', 1))
    # Si es retiro en tienda, estado_despacho siempre es RETIRO EN TIENDA
    if con_despacho == 0:
        estado_despacho = 'RETIRO EN TIENDA'
    else:
        estado_despacho = d.get('estado_despacho', 'PENDIENTE')

    suc = _sucursal_escritura(d)
    with db() as c:
        cur = c.execute(
            """INSERT INTO ventas
               (fecha, cliente_id, canal, total, notas,
                fecha_despacho, con_despacho, tipo_cliente, estado_pago, estado_despacho, sucursal_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                d.get('fecha', str(date.today())),
                d.get('cliente_id') or None,
                d.get('canal', 'local'),
                total,
                d.get('notas', ''),
                d.get('fecha_despacho', ''),
                con_despacho,
                d.get('tipo_cliente', 'CLIENTE'),
                d.get('estado_pago', 'PENDIENTE'),
                estado_despacho,
                suc,
            )
        )
        vid = cur.lastrowid
        items_plan = []
        for i in items:
            c.execute(
                "INSERT INTO venta_items (venta_id,producto_id,cantidad,precio_unitario) VALUES (?,?,?,?)",
                (vid, i['producto_id'], float(i['cantidad']), float(i['precio_unitario']))
            )
            # Descontar stock: inventario, productos y lotes FIFO (3 counters en sync).
            # Sin clamp MAX(0,...): el descuento debe ser simétrico con la
            # restauración al editar/borrar la venta; negativo = sobreventa real.
            c.execute(
                """UPDATE inventario SET stock_kg=stock_kg-?, ultima_actualizacion=date('now')
                   WHERE bodega='productos_terminados' AND producto_id=? AND sucursal_id=?""",
                (float(i['cantidad']), int(i['producto_id']), suc)
            )
            c.execute(
                "UPDATE productos SET stock=stock-? WHERE id=?",
                (float(i['cantidad']), int(i['producto_id']))
            )
            _descontar_lotes_fifo(c, int(i['producto_id']), float(i['cantidad']), vid, sucursal_id=suc)
            p = c.execute("SELECT nombre FROM productos WHERE id=?", (i['producto_id'],)).fetchone()
            if p:
                items_plan.append({'nombre_producto': p['nombre'], 'cantidad': float(i['cantidad']),
                                   'producto_id': int(i['producto_id'])})

        _auto_plan_produccion(items_plan, c, d.get('fecha_despacho', ''))

        # Auto-crear suscripción si el canal es 'suscripcion' y hay cliente
        sub_id = None
        if d.get('canal') == 'suscripcion' and d.get('cliente_id'):
            cid = d['cliente_id']
            # Check if client already has an active subscription
            existing = c.execute(
                "SELECT id FROM suscripciones WHERE cliente_id=? AND estado='activo' ORDER BY id DESC LIMIT 1",
                (cid,)).fetchone()
            if existing:
                sub_id = existing['id']
            else:
                # Build products list from venta items
                prod_list = []
                for i in items:
                    p = c.execute("SELECT nombre FROM productos WHERE id=?", (i['producto_id'],)).fetchone()
                    prod_list.append({
                        'producto_id': i['producto_id'],
                        'nombre': p['nombre'] if p else '',
                        'cantidad': float(i['cantidad'])
                    })
                fecha_str = d.get('fecha', str(date.today()))
                proxima   = str(date.today() + timedelta(days=7))
                sub_cur = c.execute(
                    """INSERT INTO suscripciones
                       (cliente_id,plan,precio,productos_json,fecha_inicio,fecha_renovacion,estado,notas,dia_despacho,entregas_realizadas)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (cid, 'semanal', total,
                     json.dumps(prod_list),
                     fecha_str, proxima,
                     'activo', d.get('notas',''),
                     '', 0)
                )
                sub_id = sub_cur.lastrowid
                c.execute("UPDATE clientes SET es_suscriptor=1 WHERE id=?", (cid,))

        return jsonify({'id': vid, 'total': total, 'suscripcion_id': sub_id}), 201

@app.route('/api/ventas/<int:vid>', methods=['PUT'])
def api_ventas_update(vid):
    """Actualiza una venta: cabecera y/o items con corrección de stock."""
    d = request.json
    with db() as c:
        v_row = c.execute("SELECT id, sucursal_id FROM ventas WHERE id=?", (vid,)).fetchone()
        if not v_row:
            return jsonify({'error': 'Venta no encontrada'}), 404
        v_suc = v_row['sucursal_id']

        # Campos de cabecera editables
        campos = {}
        for campo in ('fecha', 'canal', 'tipo_cliente', 'estado_pago',
                      'estado_despacho', 'fecha_despacho', 'notas'):
            if campo in d:
                campos[campo] = d[campo]
        # cliente_id puede ser null
        if 'cliente_id' in d:
            campos['cliente_id'] = d['cliente_id'] or None

        # Si vienen items: reemplazar con corrección de stock
        if 'items' in d:
            items_nuevos = d['items']
            # Restaurar stock de items anteriores (incluye lotes)
            _restaurar_lotes_venta(c, vid)
            for i in c.execute("SELECT producto_id, cantidad FROM venta_items WHERE venta_id=?", (vid,)).fetchall():
                c.execute(
                    """UPDATE inventario SET stock_kg=stock_kg+?, ultima_actualizacion=date('now')
                       WHERE bodega='productos_terminados' AND producto_id=? AND sucursal_id=?""",
                    (i['cantidad'], i['producto_id'], v_suc)
                )
                c.execute(
                    "UPDATE productos SET stock=stock+? WHERE id=?",
                    (i['cantidad'], i['producto_id'])
                )
            c.execute("DELETE FROM venta_items WHERE venta_id=?", (vid,))
            # Insertar nuevos items y descontar stock
            total = 0.0
            for i in items_nuevos:
                cant   = float(i['cantidad'])
                precio = float(i['precio_unitario'])
                c.execute(
                    "INSERT INTO venta_items (venta_id,producto_id,cantidad,precio_unitario) VALUES (?,?,?,?)",
                    (vid, int(i['producto_id']), cant, precio)
                )
                c.execute(
                    """UPDATE inventario SET stock_kg=stock_kg-?, ultima_actualizacion=date('now')
                       WHERE bodega='productos_terminados' AND producto_id=? AND sucursal_id=?""",
                    (cant, int(i['producto_id']), v_suc)
                )
                c.execute(
                    "UPDATE productos SET stock=stock-? WHERE id=?",
                    (cant, int(i['producto_id']))
                )
                _descontar_lotes_fifo(c, int(i['producto_id']), cant, vid, sucursal_id=v_suc)
                total += cant * precio
            campos['total'] = total

        if campos:
            sets = ', '.join(f'{k}=?' for k in campos)
            vals = list(campos.values()) + [vid]
            c.execute(f"UPDATE ventas SET {sets} WHERE id=?", vals)

        vd = dict(c.execute(
            "SELECT v.*,cl.nombre AS cliente_nombre FROM ventas v LEFT JOIN clientes cl ON cl.id=v.cliente_id WHERE v.id=?",
            (vid,)).fetchone())
        vd['items'] = [dict(i) for i in c.execute(
            "SELECT vi.*,p.nombre FROM venta_items vi JOIN productos p ON p.id=vi.producto_id WHERE vi.venta_id=?",
            (vid,)).fetchall()]
        return jsonify(vd)

@app.route('/api/ventas/<int:vid>', methods=['DELETE'])
def api_ventas_delete(vid):
    with db() as c:
        v_row = c.execute("SELECT id, sucursal_id FROM ventas WHERE id=?", (vid,)).fetchone()
        v_suc = v_row['sucursal_id'] if v_row else 1
        _restaurar_lotes_venta(c, vid)
        for i in c.execute("SELECT producto_id, cantidad FROM venta_items WHERE venta_id=?", (vid,)).fetchall():
            c.execute(
                """UPDATE inventario SET stock_kg=stock_kg+?, ultima_actualizacion=date('now')
                   WHERE bodega='productos_terminados' AND producto_id=? AND sucursal_id=?""",
                (i['cantidad'], i['producto_id'], v_suc)
            )
            c.execute(
                "UPDATE productos SET stock=stock+? WHERE id=?",
                (i['cantidad'], i['producto_id'])
            )
        # pos_ventas no tiene ON DELETE CASCADE → borrar antes
        c.execute("DELETE FROM pos_ventas WHERE venta_id=?", (vid,))
        c.execute("DELETE FROM ventas WHERE id=?", (vid,))
        return jsonify({'ok': True})

@app.route('/api/ventas/resumen')
def api_ventas_resumen():
    today = date.today()
    w0 = today - timedelta(days=today.weekday())
    m0 = today.replace(day=1)
    sw, sp = _suc_filtro()
    with db() as c:
        def t(d1, d2):
            r = c.execute(f"SELECT COALESCE(SUM(total),0),COUNT(*) FROM ventas WHERE fecha BETWEEN ? AND ?{sw}",
                          [str(d1), str(d2)] + sp).fetchone()
            return {'total': r[0], 'count': r[1]}
        pendientes_pago     = c.execute(f"SELECT COUNT(*) FROM ventas WHERE estado_pago='PENDIENTE'{sw}", sp).fetchone()[0]
        pendientes_despacho = c.execute(f"SELECT COUNT(*) FROM ventas WHERE estado_despacho='PENDIENTE'{sw}", sp).fetchone()[0]
        return jsonify({
            'hoy':                t(today, today),
            'semana':             t(w0, today),
            'mes':                t(m0, today),
            'pendientes_pago':    pendientes_pago,
            'pendientes_despacho': pendientes_despacho,
        })

# ── API: Suscripciones ────────────────────────────────────────────────────────

@app.route('/api/suscripciones', methods=['GET'])
def api_suscripciones():
    estado = request.args.get('estado','')
    with db() as c:
        q = ("SELECT s.*,c.nombre AS cliente_nombre,c.telefono AS cliente_telefono,c.email AS cliente_email "
             "FROM suscripciones s JOIN clientes c ON c.id=s.cliente_id")
        params = []
        if estado:
            q += " WHERE s.estado=?"; params.append(estado)
        q += " ORDER BY s.fecha_renovacion ASC NULLS LAST"
        return jsonify([dict(r) for r in c.execute(q, params).fetchall()])

@app.route('/api/suscripciones', methods=['POST'])
def api_suscripciones_create():
    d = request.json
    with db() as c:
        cur = c.execute(
            """INSERT INTO suscripciones
               (cliente_id,plan,precio,productos_json,fecha_inicio,fecha_renovacion,estado,notas,dia_despacho,entregas_realizadas)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (d['cliente_id'], d.get('plan','semanal'), float(d['precio']),
             json.dumps(d.get('productos',[])),
             d['fecha_inicio'], d.get('fecha_renovacion',''),
             d.get('estado','activo'), d.get('notas',''),
             d.get('dia_despacho',''),
             int(d.get('entregas_realizadas', 0)))
        )
        c.execute("UPDATE clientes SET es_suscriptor=1 WHERE id=?", (d['cliente_id'],))
        return jsonify(dict(c.execute("SELECT * FROM suscripciones WHERE id=?", (cur.lastrowid,)).fetchone())), 201

@app.route('/api/suscripciones/<int:sid>', methods=['PUT'])
def api_suscripciones_update(sid):
    d = request.json
    with db() as c:
        c.execute(
            """UPDATE suscripciones
               SET plan=?,precio=?,productos_json=?,fecha_inicio=?,
                   fecha_renovacion=?,estado=?,notas=?,dia_despacho=?,entregas_realizadas=?
               WHERE id=?""",
            (d['plan'], float(d['precio']), json.dumps(d.get('productos',[])),
             d['fecha_inicio'], d.get('fecha_renovacion',''),
             d['estado'], d.get('notas',''),
             d.get('dia_despacho',''),
             int(d.get('entregas_realizadas', 0)), sid)
        )
        if d['estado'] == 'cancelado':
            sub = c.execute("SELECT cliente_id FROM suscripciones WHERE id=?", (sid,)).fetchone()
            if sub:
                others = c.execute(
                    "SELECT COUNT(*) FROM suscripciones WHERE cliente_id=? AND estado='activo' AND id!=?",
                    (sub['cliente_id'], sid)).fetchone()[0]
                if others == 0:
                    c.execute("UPDATE clientes SET es_suscriptor=0 WHERE id=?", (sub['cliente_id'],))
        return jsonify(dict(c.execute("SELECT * FROM suscripciones WHERE id=?", (sid,)).fetchone()))

@app.route('/api/suscripciones/<int:sid>', methods=['DELETE'])
def api_suscripciones_delete(sid):
    with db() as c:
        c.execute("DELETE FROM suscripciones WHERE id=?", (sid,))
        return jsonify({'ok': True})

@app.route('/api/suscripciones/<int:sid>/registrar_entrega', methods=['POST'])
def api_registrar_entrega(sid):
    """Registra una entrega semanal. Si completa el ciclo (4 entregas), envía email de renovación."""
    with db() as c:
        sub = c.execute(
            """SELECT s.*,cl.nombre AS cliente_nombre,cl.email AS cliente_email
               FROM suscripciones s JOIN clientes cl ON cl.id=s.cliente_id
               WHERE s.id=?""", (sid,)).fetchone()
        if not sub:
            return jsonify({'error': 'Suscripción no encontrada'}), 404
        if sub['estado'] != 'activo':
            return jsonify({'error': 'Solo se pueden registrar entregas en suscripciones activas'}), 400

        nuevas_entregas = sub['entregas_realizadas'] + 1
        proxima_entrega = str(date.today() + timedelta(days=7))

        c.execute(
            "UPDATE suscripciones SET entregas_realizadas=?, fecha_renovacion=? WHERE id=?",
            (nuevas_entregas, proxima_entrega, sid)
        )

        try:
            items_sub = json.loads(sub['productos_json'] or '[]')
        except Exception as e:
            items_sub = []
            print(f"[suscripciones] productos_json inválido en sub {sid}: {e}")
        for item in items_sub:
            pid = item.get('producto_id') or item.get('id')
            try:
                qty = float(item.get('cantidad', 1))
            except (TypeError, ValueError):
                qty = 1.0
            if not pid:
                continue
            # Despachos de suscripción salen siempre de Recoleta (sucursal 1)
            c.execute(
                """UPDATE inventario SET stock_kg=stock_kg-?, ultima_actualizacion=date('now')
                   WHERE bodega='productos_terminados' AND producto_id=? AND sucursal_id=1""",
                (qty, int(pid))
            )
            c.execute(
                "UPDATE productos SET stock=stock-? WHERE id=?",
                (qty, int(pid))
            )
            _descontar_lotes_fifo(c, int(pid), qty, None, sucursal_id=1)

        ciclo_completo = nuevas_entregas >= 4
        email_sent = False
        if ciclo_completo:
            email_sent = send_renewal_email(
                sub['cliente_nombre'], sub['cliente_email'],
                sub['precio'], sub['notas']
            )

        updated = dict(c.execute("SELECT * FROM suscripciones WHERE id=?", (sid,)).fetchone())
        updated['ciclo_completo'] = ciclo_completo
        updated['email_sent'] = email_sent
        return jsonify(updated)


@app.route('/api/suscripciones/<int:sid>/renovar', methods=['POST'])
def api_renovar_suscripcion(sid):
    """Reinicia el ciclo de entregas (nuevo pago recibido). Resetea a 0/4."""
    with db() as c:
        sub = c.execute("SELECT * FROM suscripciones WHERE id=?", (sid,)).fetchone()
        if not sub:
            return jsonify({'error': 'Suscripción no encontrada'}), 404
        today_str = str(date.today())
        proxima   = str(date.today() + timedelta(days=7))
        c.execute(
            "UPDATE suscripciones SET entregas_realizadas=0, fecha_inicio=?, fecha_renovacion=?, estado='activo' WHERE id=?",
            (today_str, proxima, sid)
        )
        return jsonify(dict(c.execute("SELECT * FROM suscripciones WHERE id=?", (sid,)).fetchone()))


# ── API: Mayoristas ───────────────────────────────────────────────────────────

def _fecha_despacho_semana(dia: str) -> str:
    """Devuelve la fecha (str YYYY-MM-DD) del martes o jueves próximo (nunca pasado)."""
    hoy = date.today()
    lunes = hoy - timedelta(days=hoy.weekday())
    offset = 1 if dia == 'martes' else 3
    target = lunes + timedelta(days=offset)
    if target < hoy:
        target += timedelta(weeks=1)
    return str(target)

@app.route('/api/mayoristas/clientes', methods=['GET'])
@login_required
def api_mayoristas_clientes():
    """Lista clientes de tabla legacy — usada por panel Plantillas en mayoristas_pedidos.html."""
    with db() as c:
        rows = c.execute(
            "SELECT id, nombre FROM clientes WHERE tipo='MAYORISTA' ORDER BY nombre"
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/mayoristas/legacy/<int:cid>/pedidos', methods=['GET'])
@login_required
def api_mayoristas_pedidos_get(cid):
    with db() as c:
        cl = c.execute("SELECT id, nombre FROM clientes WHERE id=?", (cid,)).fetchone()
        if not cl:
            return jsonify({'error': 'Cliente no encontrado'}), 404
        pedidos_rows = c.execute(
            "SELECT * FROM mayorista_pedidos WHERE cliente_id=?", (cid,)
        ).fetchall()
        pedidos = []
        for p in pedidos_rows:
            lineas = c.execute(
                """SELECT ml.id, ml.producto_id, ml.cantidad,
                          pr.nombre as producto_nombre, pr.precio_mayorista
                   FROM mayorista_pedido_lineas ml
                   JOIN productos pr ON pr.id = ml.producto_id
                   WHERE ml.pedido_id=?""",
                (p['id'],)
            ).fetchall()
            pedidos.append({
                'id': p['id'],
                'dia_despacho': p['dia_despacho'],
                'activo': bool(p['activo']),
                'notas': p['notas'],
                'lineas': [dict(l) for l in lineas],
            })
    return jsonify({'cliente': dict(cl), 'pedidos': pedidos})

@app.route('/api/mayoristas/legacy/<int:cid>/pedidos', methods=['PUT'])
@login_required
def api_mayoristas_pedidos_put(cid):
    """Reemplaza la plantilla completa de pedidos de un mayorista."""
    d = request.json or {}
    pedidos_in = d.get('pedidos', [])
    with db() as c:
        if not c.execute("SELECT id FROM clientes WHERE id=?", (cid,)).fetchone():
            return jsonify({'error': 'Cliente no encontrado'}), 404
        for p in pedidos_in:
            dia = p.get('dia_despacho')
            if dia not in ('martes', 'jueves'):
                continue
            existing = c.execute(
                "SELECT id FROM mayorista_pedidos WHERE cliente_id=? AND dia_despacho=?",
                (cid, dia)
            ).fetchone()
            if existing:
                ped_id = existing['id']
                c.execute(
                    "UPDATE mayorista_pedidos SET activo=?, notas=? WHERE id=?",
                    (1 if p.get('activo', True) else 0, p.get('notas',''), ped_id)
                )
                c.execute("DELETE FROM mayorista_pedido_lineas WHERE pedido_id=?", (ped_id,))
            else:
                ped_id = c.execute(
                    "INSERT INTO mayorista_pedidos (cliente_id, dia_despacho, activo, notas) VALUES (?,?,?,?)",
                    (cid, dia, 1 if p.get('activo', True) else 0, p.get('notas',''))
                ).lastrowid
            for linea in p.get('lineas', []):
                if not linea.get('producto_id') or float(linea.get('cantidad', 0)) <= 0:
                    continue
                c.execute(
                    "INSERT INTO mayorista_pedido_lineas (pedido_id, producto_id, cantidad) VALUES (?,?,?)",
                    (ped_id, int(linea['producto_id']), float(linea['cantidad']))
                )
    return jsonify({'ok': True})

@app.route('/api/mayoristas/generar-semana', methods=['POST'])
@login_required
def api_mayoristas_generar_semana():
    """Genera ventas para todos los pedidos mayoristas activos de la semana actual."""
    generadas = 0
    omitidas = 0
    with db() as c:
        pedidos = c.execute(
            "SELECT mp.*, cl.nombre as cliente_nombre "
            "FROM mayorista_pedidos mp "
            "JOIN clientes cl ON cl.id = mp.cliente_id "
            "WHERE mp.activo=1"
        ).fetchall()

        for ped in pedidos:
            fecha_desp = _fecha_despacho_semana(ped['dia_despacho'])
            ya_existe = c.execute(
                "SELECT id FROM ventas WHERE cliente_id=? AND canal='MAYORISTA' AND DATE(fecha)=?",
                (ped['cliente_id'], fecha_desp)
            ).fetchone()
            if ya_existe:
                omitidas += 1
                continue

            lineas = c.execute(
                """SELECT ml.cantidad, ml.producto_id, pr.precio_mayorista, pr.nombre
                   FROM mayorista_pedido_lineas ml
                   JOIN productos pr ON pr.id = ml.producto_id
                   WHERE ml.pedido_id=?""",
                (ped['id'],)
            ).fetchall()
            if not lineas:
                continue

            total = sum(float(l['cantidad']) * float(l['precio_mayorista']) for l in lineas)
            if total <= 0:
                continue
            vid = c.execute(
                """INSERT INTO ventas
                   (fecha, cliente_id, canal, total, notas,
                    fecha_despacho, con_despacho, tipo_cliente, estado_pago, estado_despacho)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (fecha_desp, ped['cliente_id'], 'MAYORISTA', total,
                 f"Pedido fijo {ped['dia_despacho']}",
                 fecha_desp, 1, 'MAYORISTA', 'PENDIENTE', 'PENDIENTE')
            ).lastrowid
            for l in lineas:
                c.execute(
                    "INSERT INTO venta_items (venta_id,producto_id,cantidad,precio_unitario) VALUES (?,?,?,?)",
                    (vid, l['producto_id'], float(l['cantidad']), float(l['precio_mayorista']))
                )
            generadas += 1

    return jsonify({'generadas': generadas, 'omitidas': omitidas})


# ── API: Mayoristas — vista semanal ──────────────────────────────────────────

def _semana_iso(d: date) -> str:
    return d.strftime('%G-W%V')

def _fechas_semana(semana_str: str):
    """Dado '2026-W21' devuelve (fecha_martes, fecha_jueves) como YYYY-MM-DD."""
    year, week = semana_str.split('-W')
    lunes = date.fromisocalendar(int(year), int(week), 1)
    return str(lunes + timedelta(days=1)), str(lunes + timedelta(days=3))

def _get_or_create_semana(c, semana_str: str) -> int:
    row = c.execute('SELECT id FROM mayorista_semanas WHERE semana=?', (semana_str,)).fetchone()
    if row:
        return row['id']
    martes, jueves = _fechas_semana(semana_str)
    cur = c.execute(
        'INSERT INTO mayorista_semanas(semana,fecha_martes,fecha_jueves) VALUES(?,?,?)',
        (semana_str, martes, jueves)
    )
    return cur.lastrowid

@app.route('/api/mayoristas/semana')
@login_required
def api_mayoristas_semana_get():
    """Vista semanal: plantillas + ajustes para una semana dada."""
    semana = request.args.get('semana') or _semana_iso(date.today())
    with db() as c:
        sem = c.execute('SELECT * FROM mayorista_semanas WHERE semana=?', (semana,)).fetchone()
        martes, jueves = _fechas_semana(semana)

        # Traer todas las plantillas activas
        plantillas = c.execute("""
            SELECT mp.id as pedido_id, mp.cliente_id, mp.dia_despacho as dia,
                   cl.nombre as cliente_nombre,
                   mpl.id as linea_id, mpl.producto_id, mpl.cantidad,
                   pr.nombre as producto_nombre, pr.precio_mayorista
            FROM mayorista_pedidos mp
            JOIN clientes cl ON cl.id = mp.cliente_id
            JOIN mayorista_pedido_lineas mpl ON mpl.pedido_id = mp.id
            JOIN productos pr ON pr.id = mpl.producto_id
            WHERE mp.activo = 1
            ORDER BY cl.nombre, mp.dia_despacho, pr.nombre
        """).fetchall()

        # Ajustes de esta semana (si la semana existe)
        ajustes_map = {}
        if sem:
            ajustes = c.execute("""
                SELECT * FROM mayorista_ajustes WHERE semana_id = ?
            """, (sem['id'],)).fetchall()
            for a in ajustes:
                key = (a['cliente_id'], a['producto_id'], a['dia'])
                ajustes_map[key] = dict(a)

        # Construir líneas con overrides aplicados
        lineas = []
        for p in plantillas:
            key = (p['cliente_id'], p['producto_id'], p['dia'])
            ajuste = ajustes_map.get(key)
            lineas.append({
                'pedido_id':       p['pedido_id'],
                'linea_id':        p['linea_id'],
                'cliente_id':      p['cliente_id'],
                'cliente_nombre':  p['cliente_nombre'],
                'producto_id':     p['producto_id'],
                'producto_nombre': p['producto_nombre'],
                'dia':             p['dia'],
                'cantidad_base':   float(p['cantidad']),
                'cantidad':        float(ajuste['cantidad']) if ajuste and not ajuste['cancelado'] else float(p['cantidad']),
                'cancelado':       bool(ajuste['cancelado']) if ajuste else False,
                'modificado':      ajuste is not None,
                'ajuste_id':       ajuste['id'] if ajuste else None,
                'nota':            ajuste['nota'] if ajuste else '',
                'precio_mayorista':float(p['precio_mayorista']),
            })

        # Líneas extra de ajustes que no corresponden a ninguna plantilla
        plantilla_keys = {(p['cliente_id'], p['producto_id'], p['dia']) for p in plantillas}
        if sem:
            for a in ajustes_map.values():
                key = (a['cliente_id'], a['producto_id'], a['dia'])
                if key not in plantilla_keys and not a['cancelado']:
                    cl = c.execute('SELECT nombre FROM clientes WHERE id=?', (a['cliente_id'],)).fetchone()
                    pr = c.execute('SELECT nombre, precio_mayorista FROM productos WHERE id=?', (a['producto_id'],)).fetchone()
                    if cl and pr:
                        lineas.append({
                            'pedido_id': None, 'linea_id': None,
                            'cliente_id': a['cliente_id'], 'cliente_nombre': cl['nombre'],
                            'producto_id': a['producto_id'], 'producto_nombre': pr['nombre'],
                            'dia': a['dia'],
                            'cantidad_base': 0, 'cantidad': float(a['cantidad']),
                            'cancelado': False, 'modificado': True,
                            'ajuste_id': a['id'], 'nota': a['nota'],
                            'precio_mayorista': float(pr['precio_mayorista']),
                        })

        return jsonify({
            'semana':       semana,
            'fecha_martes': martes,
            'fecha_jueves': jueves,
            'estado':       sem['estado'] if sem else 'sin_generar',
            'lineas':       lineas,
        })

@app.route('/api/mayoristas/semana/ajuste', methods=['POST'])
@login_required
def api_mayoristas_ajuste_post():
    """Crear o reemplazar ajuste de semana para una línea específica."""
    d = request.json or {}
    semana = d.get('semana') or _semana_iso(date.today())
    with db() as c:
        sem_id = _get_or_create_semana(c, semana)
        c.execute("""
            INSERT INTO mayorista_ajustes
                (semana_id, cliente_id, producto_id, dia, cantidad, cancelado, nota)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(semana_id, cliente_id, producto_id, dia)
            DO UPDATE SET cantidad=excluded.cantidad,
                          cancelado=excluded.cancelado,
                          nota=excluded.nota
        """, (sem_id, d['cliente_id'], d['producto_id'], d['dia'],
              float(d.get('cantidad', 0)), 1 if d.get('cancelado') else 0, d.get('nota', '')))
        return jsonify({'ok': True})

@app.route('/api/mayoristas/semana/ajuste/<int:aid>', methods=['DELETE'])
@login_required
def api_mayoristas_ajuste_delete(aid):
    """Elimina un ajuste (restaura al valor de la plantilla)."""
    with db() as c:
        c.execute('DELETE FROM mayorista_ajustes WHERE id=?', (aid,))
    return jsonify({'ok': True})

@app.route('/api/mayoristas/semana/estado', methods=['PUT'])
@login_required
def api_mayoristas_semana_estado():
    d = request.json or {}
    semana = d.get('semana') or _semana_iso(date.today())
    estado = d.get('estado', 'borrador')
    with db() as c:
        _get_or_create_semana(c, semana)
        c.execute('UPDATE mayorista_semanas SET estado=? WHERE semana=?', (estado, semana))
    return jsonify({'ok': True})


@app.route('/api/mayoristas/resumen', methods=['GET'])
@login_required
def api_mayoristas_resumen():
    hoy = date.today()
    hace4s = hoy - timedelta(weeks=4)
    semana = _semana_iso(hoy)
    with db() as c:
        clientes = c.execute(
            "SELECT COUNT(*) FROM clientes WHERE tipo='MAYORISTA'"
        ).fetchone()[0]
        pedidos_activos = c.execute(
            "SELECT COUNT(DISTINCT cliente_id) FROM mayorista_pedidos WHERE activo=1"
        ).fetchone()[0]
        ventas_4s = c.execute(
            "SELECT COALESCE(SUM(total),0) FROM ventas WHERE canal='MAYORISTA' AND fecha>=?",
            (str(hace4s),)
        ).fetchone()[0]
        por_cobrar = c.execute(
            "SELECT COALESCE(SUM(total),0) FROM ventas WHERE canal='MAYORISTA' AND estado_pago='PENDIENTE'"
        ).fetchone()[0]
        sem_row = c.execute(
            "SELECT estado FROM mayorista_semanas WHERE semana=?", (semana,)
        ).fetchone()
        sem_estado = sem_row['estado'] if sem_row else 'sin_generar'
        filas = c.execute("""
            SELECT mp.dia_despacho as dia, cl.nombre as cliente,
                   COUNT(mpl.id) as n_productos,
                   COALESCE(SUM(mpl.cantidad * pr.precio_mayorista), 0) as total
            FROM mayorista_pedidos mp
            JOIN clientes cl ON cl.id = mp.cliente_id
            JOIN mayorista_pedido_lineas mpl ON mpl.pedido_id = mp.id
            JOIN productos pr ON pr.id = mpl.producto_id
            WHERE mp.activo=1
            GROUP BY mp.id
            ORDER BY cl.nombre
        """).fetchall()
    return jsonify({
        'clientes':        clientes,
        'pedidos_activos': pedidos_activos,
        'ventas_4s':       float(ventas_4s),
        'por_cobrar':      float(por_cobrar),
        'semana':          semana,
        'sem_estado':      sem_estado,
        'martes':          [dict(r) for r in filas if r['dia'] == 'martes'],
        'jueves':          [dict(r) for r in filas if r['dia'] == 'jueves'],
    })


@app.route('/api/mayoristas/produccion')
@login_required
def api_mayoristas_produccion():
    """Totales de producción para la semana: sum por producto y día (plantillas + ajustes)."""
    semana = request.args.get('semana') or _semana_iso(date.today())
    with db() as c:
        sem = c.execute('SELECT id FROM mayorista_semanas WHERE semana=?', (semana,)).fetchone()

        plantillas = c.execute("""
            SELECT mp.cliente_id, mpl.producto_id, pr.nombre as producto_nombre,
                   mp.dia_despacho as dia, mpl.cantidad
            FROM mayorista_pedidos mp
            JOIN mayorista_pedido_lineas mpl ON mpl.pedido_id = mp.id
            JOIN productos pr ON pr.id = mpl.producto_id
            WHERE mp.activo = 1
        """).fetchall()

        ajustes_map = {}
        if sem:
            for a in c.execute('SELECT * FROM mayorista_ajustes WHERE semana_id=?', (sem['id'],)).fetchall():
                ajustes_map[(a['cliente_id'], a['producto_id'], a['dia'])] = dict(a)

        totales = {}
        plantilla_keys = set()
        for p in plantillas:
            key = (p['cliente_id'], p['producto_id'], p['dia'])
            plantilla_keys.add(key)
            ajuste = ajustes_map.get(key)
            cantidad = float(p['cantidad'])
            if ajuste:
                if ajuste['cancelado']:
                    continue
                cantidad = float(ajuste['cantidad'])
            k = (p['producto_nombre'], p['dia'])
            totales[k] = totales.get(k, 0) + cantidad

        # Ajustes extra sin plantilla (pedidos puntuales de la semana)
        for key, a in ajustes_map.items():
            if key in plantilla_keys or a['cancelado'] or float(a['cantidad']) <= 0:
                continue
            pr = c.execute('SELECT nombre FROM productos WHERE id=?', (a['producto_id'],)).fetchone()
            if pr:
                k = (pr['nombre'], a['dia'])
                totales[k] = totales.get(k, 0) + float(a['cantidad'])

        result = [{'producto': k[0], 'dia': k[1], 'total': v} for k, v in sorted(totales.items())]
    return jsonify(result)

# ── API: Mayoristas B2B — Clientes ───────────────────────────────────────────

@app.route('/api/mayoristas', methods=['GET'])
@login_required
def api_may_clientes_list():
    q     = request.args.get('q', '').strip()
    zona  = request.args.get('zona', '')
    tipo  = request.args.get('tipo_negocio', '')
    activo = request.args.get('activo', '1')
    con_deuda = request.args.get('con_deuda', '')
    sin_pedido_dias = request.args.get('sin_pedido_dias', '')
    order_by = request.args.get('order_by', 'nombre')
    with db() as c:
        sql = "SELECT * FROM mayoristas WHERE 1=1"
        params = []
        if activo != '':
            sql += " AND activo=?"
            params.append(int(activo))
        if zona:
            sql += " AND zona=?"
            params.append(zona)
        if tipo:
            sql += " AND tipo_negocio=?"
            params.append(tipo)
        if q:
            sql += " AND (nombre LIKE ? OR razon_social LIKE ? OR encargado_nombre LIKE ? OR telefono LIKE ? OR encargado_telefono LIKE ?)"
            params += [f'%{q}%'] * 5
        if sin_pedido_dias:
            cutoff = str(date.today() - timedelta(days=int(sin_pedido_dias)))
            sql += " AND (fecha_ultimo_pedido='' OR fecha_ultimo_pedido < ?)"
            params.append(cutoff)
        valid_orders = {'volumen_semanal_panes': 'volumen_semanal_panes DESC',
                        'fecha_ultimo_pedido': 'fecha_ultimo_pedido DESC',
                        'deuda': 'nombre', 'nombre': 'nombre'}
        sql += f" ORDER BY {valid_orders.get(order_by, 'nombre')}"
        rows = c.execute(sql, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d['deuda'] = _deuda_mayorista(c, r['id'])
            ult = c.execute(
                "SELECT id, fecha_pedido, estado, total FROM mayoristas_pedidos WHERE mayorista_id=? ORDER BY fecha_pedido DESC LIMIT 1",
                (r['id'],)
            ).fetchone()
            d['ultimo_pedido'] = dict(ult) if ult else None
            result.append(d)
        if order_by == 'deuda':
            result.sort(key=lambda x: x['deuda']['total'], reverse=True)
    return jsonify(result)


@app.route('/api/mayoristas', methods=['POST'])
@login_required
def api_may_clientes_create():
    d = request.json or {}
    nombre = (d.get('nombre') or '').strip()
    if not nombre:
        return jsonify({'error': 'Nombre requerido'}), 400
    with db() as c:
        cur = c.execute(
            """INSERT INTO mayoristas
               (nombre, razon_social, rut, giro, tipo_negocio,
                encargado_nombre, encargado_telefono, encargado_email,
                telefono, email, direccion, zona,
                dia_despacho_1, dia_despacho_2, volumen_semanal_panes,
                condicion_pago, limite_credito, activo, tiene_factura, notas)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (nombre, d.get('razon_social',''), d.get('rut',''), d.get('giro',''), d.get('tipo_negocio',''),
             d.get('encargado_nombre',''), d.get('encargado_telefono',''), d.get('encargado_email',''),
             d.get('telefono',''), d.get('email',''), d.get('direccion',''), d.get('zona',''),
             d.get('dia_despacho_1',''), d.get('dia_despacho_2',''), int(d.get('volumen_semanal_panes', 0)),
             d.get('condicion_pago','contado'), float(d.get('limite_credito', 0)),
             1 if d.get('activo', True) else 0,
             1 if d.get('tiene_factura') else 0,
             d.get('notas',''))
        )
        mid = cur.lastrowid
    print(f"[mayoristas] Nuevo cliente B2B id={mid} nombre={nombre}")
    return jsonify({'id': mid}), 201


@app.route('/api/mayoristas/<int:mid>', methods=['GET'])
@login_required
def api_may_clientes_get(mid):
    with db() as c:
        m = c.execute("SELECT * FROM mayoristas WHERE id=?", (mid,)).fetchone()
        if not m:
            return jsonify({'error': 'No encontrado'}), 404
        d = dict(m)
        d['deuda'] = _deuda_mayorista(c, mid)
        pedidos = c.execute(
            "SELECT id, fecha_pedido, fecha_entrega, estado, total, estado_pago FROM mayoristas_pedidos WHERE mayorista_id=? ORDER BY fecha_pedido DESC LIMIT 10",
            (mid,)
        ).fetchall()
        d['pedidos_recientes'] = [dict(p) for p in pedidos]
        interacciones = c.execute(
            """SELECT ci.id, ci.tipo, ci.asunto, ci.fecha, ci.resultado
               FROM crm_interacciones ci
               JOIN crm_leads cl ON cl.id = ci.lead_id
               WHERE cl.id = (SELECT crm_lead_id FROM mayoristas WHERE id=?)
               ORDER BY ci.fecha DESC LIMIT 20""",
            (mid,)
        ).fetchall()
        d['interacciones'] = [dict(i) for i in interacciones]
    return jsonify(d)


@app.route('/api/mayoristas/<int:mid>', methods=['PUT'])
@login_required
def api_may_clientes_update(mid):
    d = request.json or {}
    allowed = ['nombre','razon_social','rut','giro','tipo_negocio','encargado_nombre',
               'encargado_telefono','encargado_email','telefono','email','direccion','zona',
               'dia_despacho_1','dia_despacho_2','volumen_semanal_panes','condicion_pago',
               'limite_credito','activo','tiene_factura','notas']
    sets = [f"{k}=?" for k in allowed if k in d]
    if not sets:
        return jsonify({'error': 'Sin campos para actualizar'}), 400
    vals = [d[k] for k in allowed if k in d]
    vals.append(mid)
    with db() as c:
        if not c.execute("SELECT id FROM mayoristas WHERE id=?", (mid,)).fetchone():
            return jsonify({'error': 'No encontrado'}), 404
        c.execute(f"UPDATE mayoristas SET {', '.join(sets)} WHERE id=?", vals)
    return jsonify({'ok': True})


@app.route('/api/mayoristas/<int:mid>', methods=['DELETE'])
@login_required
def api_may_clientes_delete(mid):
    with db() as c:
        if not c.execute("SELECT id FROM mayoristas WHERE id=?", (mid,)).fetchone():
            return jsonify({'error': 'No encontrado'}), 404
        c.execute("UPDATE mayoristas SET activo=0 WHERE id=?", (mid,))
    return jsonify({'ok': True})


@app.route('/api/mayoristas/<int:mid>/pedidos', methods=['GET'])
@login_required
def api_may_cliente_pedidos(mid):
    with db() as c:
        if not c.execute("SELECT id FROM mayoristas WHERE id=?", (mid,)).fetchone():
            return jsonify({'error': 'No encontrado'}), 404
        rows = c.execute(
            "SELECT * FROM mayoristas_pedidos WHERE mayorista_id=? ORDER BY fecha_pedido DESC",
            (mid,)
        ).fetchall()
        result = []
        for r in rows:
            p = dict(r)
            items = c.execute(
                """SELECT mpi.*, pr.nombre as producto_nombre
                   FROM mayoristas_pedido_items mpi
                   JOIN productos pr ON pr.id = mpi.producto_id
                   WHERE mpi.pedido_id=?""",
                (r['id'],)
            ).fetchall()
            p['items'] = [dict(i) for i in items]
            result.append(p)
    return jsonify(result)


@app.route('/api/mayoristas/<int:mid>/cobranza', methods=['GET'])
@login_required
def api_may_cliente_cobranza(mid):
    with db() as c:
        rows = c.execute(
            "SELECT * FROM mayoristas_cobranza WHERE mayorista_id=? ORDER BY fecha_emision DESC",
            (mid,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/mayoristas/<int:mid>/deuda', methods=['GET'])
@login_required
def api_may_cliente_deuda(mid):
    with db() as c:
        if not c.execute("SELECT id FROM mayoristas WHERE id=?", (mid,)).fetchone():
            return jsonify({'error': 'No encontrado'}), 404
        return jsonify(_deuda_mayorista(c, mid))


@app.route('/api/mayoristas/<int:mid>/contactar', methods=['POST'])
@login_required
def api_may_cliente_contactar(mid):
    d = request.json or {}
    canal = d.get('canal', 'whatsapp')
    mensaje = (d.get('mensaje') or '').strip()
    if not mensaje:
        return jsonify({'error': 'Mensaje requerido'}), 400
    with db() as c:
        m = c.execute("SELECT * FROM mayoristas WHERE id=?", (mid,)).fetchone()
        if not m:
            return jsonify({'error': 'No encontrado'}), 404
        if canal == 'whatsapp':
            tel = m['encargado_telefono'] or m['telefono']
            if not tel:
                return jsonify({'error': 'Sin teléfono registrado'}), 400
            ok, err = _send_whatsapp_agent(tel, mensaje)
            if not ok:
                return jsonify({'error': err}), 500
        if m['crm_lead_id']:
            c.execute(
                """INSERT INTO crm_interacciones (lead_id, tipo, direccion, asunto, contenido, resultado)
                   VALUES (?,?,?,?,?,?)""",
                (m['crm_lead_id'], canal, 'saliente', 'Contacto directo', mensaje, 'enviado')
            )
            c.execute(
                "UPDATE crm_leads SET fecha_ultimo_contacto=? WHERE id=?",
                (str(date.today()), m['crm_lead_id'])
            )
    return jsonify({'ok': True})


# ── API: Mayoristas B2B — Pedidos ────────────────────────────────────────────

@app.route('/api/mayoristas/pedidos', methods=['GET'])
@login_required
def api_may_pedidos_list():
    estado   = request.args.get('estado', '')
    mid      = request.args.get('mayorista_id', '')
    desde    = request.args.get('desde', '')
    hasta    = request.args.get('hasta', '')
    with db() as c:
        sql = "SELECT mp.*, m.nombre as mayorista_nombre FROM mayoristas_pedidos mp JOIN mayoristas m ON m.id=mp.mayorista_id WHERE 1=1"
        params = []
        if estado:
            sql += " AND mp.estado=?"
            params.append(estado)
        if mid:
            sql += " AND mp.mayorista_id=?"
            params.append(int(mid))
        if desde:
            sql += " AND mp.fecha_entrega>=?"
            params.append(desde)
        if hasta:
            sql += " AND mp.fecha_entrega<=?"
            params.append(hasta)
        sql += " ORDER BY mp.fecha_entrega DESC, mp.id DESC"
        rows = c.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/mayoristas/pedidos', methods=['POST'])
@login_required
def api_may_pedidos_create():
    d = request.json or {}
    mid = d.get('mayorista_id')
    if not mid:
        return jsonify({'error': 'mayorista_id requerido'}), 400
    items_in = d.get('items', [])
    if not items_in:
        return jsonify({'error': 'items requeridos'}), 400
    with db() as c:
        m = c.execute("SELECT * FROM mayoristas WHERE id=?", (mid,)).fetchone()
        if not m:
            return jsonify({'error': 'Mayorista no encontrado'}), 404
        subtotal = 0.0
        items_calc = []
        for it in items_in:
            pid = int(it['producto_id'])
            cant = int(it.get('cantidad', 1))
            precio = _precio_mayorista(c, mid, pid)
            desc = float(it.get('descuento', 0))
            sub = round(precio * cant * (1 - desc / 100), 0)
            subtotal += sub
            items_calc.append({'producto_id': pid, 'cantidad': cant, 'precio_unit': precio, 'descuento': desc, 'subtotal': sub})
        descuento_global = float(d.get('descuento', 0))
        total = round(subtotal * (1 - descuento_global / 100), 0)
        cur = c.execute(
            """INSERT INTO mayoristas_pedidos
               (mayorista_id, fecha_pedido, fecha_entrega, dia_entrega, estado,
                subtotal, descuento, total, condicion_pago, estado_pago, notas, notas_produccion, creado_por)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mid, d.get('fecha_pedido', str(date.today())), d.get('fecha_entrega',''),
             d.get('dia_entrega',''), 'PENDIENTE',
             subtotal, descuento_global, total,
             d.get('condicion_pago', m['condicion_pago']),
             'PENDIENTE', d.get('notas',''), d.get('notas_produccion',''),
             session.get('user_nombre', ''))
        )
        pid_new = cur.lastrowid
        for it in items_calc:
            c.execute(
                "INSERT INTO mayoristas_pedido_items (pedido_id,producto_id,cantidad,precio_unit,descuento,subtotal) VALUES (?,?,?,?,?,?)",
                (pid_new, it['producto_id'], it['cantidad'], it['precio_unit'], it['descuento'], it['subtotal'])
            )
        c.execute("UPDATE mayoristas SET fecha_ultimo_pedido=? WHERE id=?", (str(date.today()), mid))
    print(f"[mayoristas] Pedido creado id={pid_new} mayorista={mid} total={total}")
    return jsonify({'id': pid_new, 'total': total}), 201


@app.route('/api/mayoristas/pedidos/<int:pid>', methods=['GET'])
@login_required
def api_may_pedido_get(pid):
    with db() as c:
        p = c.execute(
            "SELECT mp.*, m.nombre as mayorista_nombre FROM mayoristas_pedidos mp JOIN mayoristas m ON m.id=mp.mayorista_id WHERE mp.id=?",
            (pid,)
        ).fetchone()
        if not p:
            return jsonify({'error': 'No encontrado'}), 404
        items = c.execute(
            """SELECT mpi.*, pr.nombre as producto_nombre
               FROM mayoristas_pedido_items mpi
               JOIN productos pr ON pr.id = mpi.producto_id
               WHERE mpi.pedido_id=?""",
            (pid,)
        ).fetchall()
        d = dict(p)
        d['items'] = [dict(i) for i in items]
    return jsonify(d)


@app.route('/api/mayoristas/pedidos/<int:pid>', methods=['PUT'])
@login_required
def api_may_pedido_update(pid):
    d = request.json or {}
    allowed = ['estado', 'notas', 'notas_produccion', 'fecha_entrega', 'dia_entrega', 'estado_pago']
    sets = [f"{k}=?" for k in allowed if k in d]
    if not sets:
        return jsonify({'error': 'Sin campos para actualizar'}), 400
    vals = [d[k] for k in allowed if k in d]
    vals.append(pid)
    with db() as c:
        if not c.execute("SELECT id FROM mayoristas_pedidos WHERE id=?", (pid,)).fetchone():
            return jsonify({'error': 'No encontrado'}), 404
        c.execute(f"UPDATE mayoristas_pedidos SET {', '.join(sets)} WHERE id=?", vals)
    return jsonify({'ok': True})


@app.route('/api/mayoristas/pedidos/<int:pid>/confirmar', methods=['POST'])
@login_required
def api_may_pedido_confirmar(pid):
    with db() as c:
        p = c.execute("SELECT * FROM mayoristas_pedidos WHERE id=?", (pid,)).fetchone()
        if not p:
            return jsonify({'error': 'No encontrado'}), 404
        if p['estado'] != 'PENDIENTE':
            return jsonify({'error': f"Estado actual es {p['estado']}, no PENDIENTE"}), 400
        items = c.execute(
            "SELECT mpi.*, pr.nombre as nombre FROM mayoristas_pedido_items mpi JOIN productos pr ON pr.id=mpi.producto_id WHERE mpi.pedido_id=?",
            (pid,)
        ).fetchall()
        # Insertar en plan_produccion para el día anterior a la entrega
        if p['fecha_entrega']:
            try:
                fecha_horn = str(date.fromisoformat(p['fecha_entrega']) - timedelta(days=1))
            except Exception:
                fecha_horn = p['fecha_entrega']
        else:
            fecha_horn = str(date.today() + timedelta(days=1))
        batch_id = f"MAYORISTA-{pid}"
        for it in items:
            existente = c.execute(
                "SELECT id FROM plan_produccion WHERE fecha=? AND batch_id=? AND nombre_producto=?",
                (fecha_horn, batch_id, it['nombre'])
            ).fetchone()
            if not existente:
                c.execute(
                    """INSERT INTO plan_produccion (fecha, codigo_producto, nombre_producto, cantidad, estado, notas, producto_id, batch_id, fecha_horneado)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (fecha_horn, f"MAY{it['producto_id']}", it['nombre'], it['cantidad'],
                     'pendiente', f"Mayorista pedido #{pid}", it['producto_id'], batch_id, fecha_horn)
                )
        c.execute("UPDATE mayoristas_pedidos SET estado='CONFIRMADO' WHERE id=?", (pid,))
        # Crear doc cobranza si tiene factura
        m = c.execute("SELECT * FROM mayoristas WHERE id=?", (p['mayorista_id'],)).fetchone()
        if m and m['tiene_factura']:
            condicion = p['condicion_pago'] or m['condicion_pago']
            dias_map = {'contado': 0, '7dias': 7, '30dias': 30}
            dias = dias_map.get(condicion, 0)
            fv = str(date.today() + timedelta(days=dias))
            c.execute(
                """INSERT INTO mayoristas_cobranza (mayorista_id, pedido_id, tipo_doc, monto, fecha_vencimiento, estado)
                   VALUES (?,?,?,?,?,?)""",
                (p['mayorista_id'], pid, 'factura', p['total'], fv, 'PENDIENTE')
            )
        # WhatsApp al encargado
        if m:
            tel = m['encargado_telefono'] or m['telefono']
            if tel:
                items_txt = '\n'.join(f"  - {it['nombre']}: {it['cantidad']} uds" for it in items)
                msg = f"Hola {m['encargado_nombre'] or m['nombre']}, confirmamos tu pedido Aurora Bakers:\n{items_txt}\nEntrega: {p['fecha_entrega']}. Total: ${p['total']:,.0f}"
                _send_whatsapp_agent(tel, msg)
    print(f"[mayoristas] Pedido {pid} CONFIRMADO -- plan_produccion fecha={fecha_horn}")
    return jsonify({'ok': True, 'batch_id': batch_id})


@app.route('/api/mayoristas/pedidos/<int:pid>/despachar', methods=['POST'])
@login_required
def api_may_pedido_despachar(pid):
    with db() as c:
        p = c.execute("SELECT * FROM mayoristas_pedidos WHERE id=?", (pid,)).fetchone()
        if not p:
            return jsonify({'error': 'No encontrado'}), 404
        if p['estado'] not in ('CONFIRMADO', 'EN_PRODUCCION'):
            return jsonify({'error': f"Estado actual es {p['estado']}"}), 400
        c.execute("UPDATE mayoristas_pedidos SET estado='DESPACHADO' WHERE id=?", (pid,))
        m = c.execute("SELECT * FROM mayoristas WHERE id=?", (p['mayorista_id'],)).fetchone()
        if m and not m['tiene_factura']:
            c.execute(
                """INSERT INTO mayoristas_cobranza (mayorista_id, pedido_id, tipo_doc, monto, fecha_vencimiento, estado)
                   VALUES (?,?,?,?,?,?)""",
                (p['mayorista_id'], pid, 'boleta', p['total'], str(date.today()), 'PENDIENTE')
            )
    return jsonify({'ok': True})


@app.route('/api/mayoristas/pedidos/<int:pid>/cancelar', methods=['POST'])
@login_required
def api_may_pedido_cancelar(pid):
    d = request.json or {}
    with db() as c:
        if not c.execute("SELECT id FROM mayoristas_pedidos WHERE id=?", (pid,)).fetchone():
            return jsonify({'error': 'No encontrado'}), 404
        c.execute("UPDATE mayoristas_pedidos SET estado='CANCELADO', notas=notas||? WHERE id=?",
                  (f"\n[CANCELADO: {d.get('motivo','')}]", pid))
    return jsonify({'ok': True})


@app.route('/api/mayoristas/pedidos/semana', methods=['GET'])
@login_required
def api_may_pedidos_semana():
    """Pedidos de la semana actual agrupados por día de entrega."""
    hoy = date.today()
    lunes = hoy - timedelta(days=hoy.weekday())
    domingo = lunes + timedelta(days=6)
    with db() as c:
        rows = c.execute(
            """SELECT mp.*, m.nombre as mayorista_nombre
               FROM mayoristas_pedidos mp
               JOIN mayoristas m ON m.id = mp.mayorista_id
               WHERE mp.fecha_entrega BETWEEN ? AND ?
                 AND mp.estado NOT IN ('CANCELADO')
               ORDER BY mp.fecha_entrega, mp.id""",
            (str(lunes), str(domingo))
        ).fetchall()
        result = {}
        for r in rows:
            dia = r['fecha_entrega'] or str(hoy)
            if dia not in result:
                result[dia] = []
            p = dict(r)
            items = c.execute(
                "SELECT mpi.*, pr.nombre as nombre FROM mayoristas_pedido_items mpi JOIN productos pr ON pr.id=mpi.producto_id WHERE mpi.pedido_id=?",
                (r['id'],)
            ).fetchall()
            p['items'] = [dict(i) for i in items]
            result[dia].append(p)
    return jsonify(result)


# ── API: Mayoristas B2B — Cobranza ───────────────────────────────────────────

def _verificar_vencimientos_cobranza():
    """Marca como VENCIDO documentos cuya fecha_vencimiento ya pasó."""
    with db() as c:
        c.execute("""UPDATE mayoristas_cobranza SET estado='VENCIDO'
                     WHERE estado='PENDIENTE' AND fecha_vencimiento != ''
                     AND fecha_vencimiento < date('now')""")


@app.route('/api/mayoristas/cobranza', methods=['GET'])
@login_required
def api_may_cobranza_list():
    estado   = request.args.get('estado', '')
    mid      = request.args.get('mayorista_id', '')
    vencida  = request.args.get('vencida', '')
    with db() as c:
        sql = "SELECT mc.*, m.nombre as mayorista_nombre FROM mayoristas_cobranza mc JOIN mayoristas m ON m.id=mc.mayorista_id WHERE 1=1"
        params = []
        if estado:
            sql += " AND mc.estado=?"
            params.append(estado)
        if mid:
            sql += " AND mc.mayorista_id=?"
            params.append(int(mid))
        if vencida == '1':
            sql += " AND mc.estado='VENCIDO'"
        sql += " ORDER BY mc.fecha_emision DESC"
        rows = c.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/mayoristas/cobranza', methods=['POST'])
@login_required
def api_may_cobranza_create():
    d = request.json or {}
    mid = d.get('mayorista_id')
    if not mid:
        return jsonify({'error': 'mayorista_id requerido'}), 400
    with db() as c:
        if not c.execute("SELECT id FROM mayoristas WHERE id=?", (mid,)).fetchone():
            return jsonify({'error': 'Mayorista no encontrado'}), 404
        cur = c.execute(
            """INSERT INTO mayoristas_cobranza
               (mayorista_id, pedido_id, tipo_doc, numero_doc, monto,
                fecha_emision, fecha_vencimiento, estado, notas)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (mid, d.get('pedido_id'), d.get('tipo_doc','boleta'), d.get('numero_doc',''),
             float(d.get('monto', 0)), d.get('fecha_emision', str(date.today())),
             d.get('fecha_vencimiento',''), d.get('estado','PENDIENTE'), d.get('notas',''))
        )
    return jsonify({'id': cur.lastrowid}), 201


@app.route('/api/mayoristas/cobranza/<int:cob_id>', methods=['PUT'])
@login_required
def api_may_cobranza_update(cob_id):
    d = request.json or {}
    allowed = ['estado', 'numero_doc', 'fecha_pago', 'medio_pago', 'notas', 'monto']
    sets = [f"{k}=?" for k in allowed if k in d]
    if not sets:
        return jsonify({'error': 'Sin campos para actualizar'}), 400
    vals = [d[k] for k in allowed if k in d]
    vals.append(cob_id)
    with db() as c:
        c.execute(f"UPDATE mayoristas_cobranza SET {', '.join(sets)} WHERE id=?", vals)
    return jsonify({'ok': True})


@app.route('/api/mayoristas/cobranza/<int:cob_id>/pagar', methods=['POST'])
@login_required
def api_may_cobranza_pagar(cob_id):
    d = request.json or {}
    medio = d.get('medio_pago', '')
    with db() as c:
        cob = c.execute("SELECT * FROM mayoristas_cobranza WHERE id=?", (cob_id,)).fetchone()
        if not cob:
            return jsonify({'error': 'No encontrado'}), 404
        c.execute(
            "UPDATE mayoristas_cobranza SET estado='PAGADO', fecha_pago=?, medio_pago=? WHERE id=?",
            (str(date.today()), medio, cob_id)
        )
        if cob['pedido_id']:
            pendientes = c.execute(
                "SELECT COUNT(*) FROM mayoristas_cobranza WHERE pedido_id=? AND estado IN ('PENDIENTE','VENCIDO')",
                (cob['pedido_id'],)
            ).fetchone()[0]
            nuevo_estado_pago = 'PAGADO' if pendientes == 0 else 'PARCIAL'
            c.execute("UPDATE mayoristas_pedidos SET estado_pago=? WHERE id=?",
                      (nuevo_estado_pago, cob['pedido_id']))
    return jsonify({'ok': True})


@app.route('/api/mayoristas/cobranza/resumen', methods=['GET'])
@login_required
def api_may_cobranza_resumen():
    _verificar_vencimientos_cobranza()
    with db() as c:
        totales = c.execute("""
            SELECT estado, COUNT(*) as n, COALESCE(SUM(monto),0) as monto
            FROM mayoristas_cobranza
            GROUP BY estado
        """).fetchall()
        prox_7d = c.execute("""
            SELECT COALESCE(SUM(monto),0) as monto
            FROM mayoristas_cobranza
            WHERE estado='PENDIENTE' AND fecha_vencimiento != ''
              AND fecha_vencimiento <= date('now','+7 days')
              AND fecha_vencimiento >= date('now')
        """).fetchone()
        vencida = c.execute(
            "SELECT COALESCE(SUM(monto),0) FROM mayoristas_cobranza WHERE estado='VENCIDO'"
        ).fetchone()[0]
    return jsonify({
        'por_estado': {r['estado']: {'n': r['n'], 'monto': r['monto']} for r in totales},
        'vencida':     float(vencida),
        'proximos_7d': float(prox_7d['monto']),
    })


# ── API: Mayoristas B2B — Precios ─────────────────────────────────────────────

@app.route('/api/mayoristas/precios', methods=['GET'])
@login_required
def api_may_precios_list():
    with db() as c:
        rows = c.execute(
            """SELECT mp.*, m.nombre as mayorista_nombre, pr.nombre as producto_nombre
               FROM mayoristas_precios mp
               JOIN mayoristas m ON m.id=mp.mayorista_id
               JOIN productos pr ON pr.id=mp.producto_id
               WHERE mp.activo=1
               ORDER BY m.nombre, pr.nombre"""
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/mayoristas/<int:mid>/precios', methods=['GET'])
@login_required
def api_may_precios_cliente(mid):
    with db() as c:
        rows = c.execute(
            """SELECT mp.*, pr.nombre as producto_nombre, pr.precio as precio_base, pr.precio_mayorista
               FROM mayoristas_precios mp
               JOIN productos pr ON pr.id=mp.producto_id
               WHERE mp.mayorista_id=? AND mp.activo=1""",
            (mid,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/mayoristas/<int:mid>/precios', methods=['POST'])
@login_required
def api_may_precios_upsert(mid):
    d = request.json or {}
    producto_id = d.get('producto_id')
    precio = d.get('precio')
    if not producto_id or precio is None:
        return jsonify({'error': 'producto_id y precio requeridos'}), 400
    with db() as c:
        c.execute(
            """INSERT INTO mayoristas_precios (mayorista_id, producto_id, precio, descuento_pct, min_unidades, activo)
               VALUES (?,?,?,?,?,1)
               ON CONFLICT(mayorista_id, producto_id)
               DO UPDATE SET precio=excluded.precio, descuento_pct=excluded.descuento_pct,
                             min_unidades=excluded.min_unidades, activo=1""",
            (mid, int(producto_id), float(precio), float(d.get('descuento_pct',0)), int(d.get('min_unidades',1)))
        )
    return jsonify({'ok': True}), 201


@app.route('/api/mayoristas/precios/<int:precio_id>', methods=['DELETE'])
@login_required
def api_may_precios_delete(precio_id):
    with db() as c:
        c.execute("UPDATE mayoristas_precios SET activo=0 WHERE id=?", (precio_id,))
    return jsonify({'ok': True})


@app.route('/api/mayoristas/precios/lista', methods=['GET'])
@login_required
def api_may_precios_lista_completa():
    """Lista de todos los productos con precio general y excepciones por cliente."""
    with db() as c:
        prods = c.execute("SELECT id, nombre, precio, precio_mayorista FROM productos WHERE activo=1 ORDER BY nombre").fetchall()
        excepciones = c.execute(
            """SELECT mp.producto_id, mp.mayorista_id, mp.precio, m.nombre as mayorista_nombre
               FROM mayoristas_precios mp JOIN mayoristas m ON m.id=mp.mayorista_id
               WHERE mp.activo=1"""
        ).fetchall()
        exc_map = {}
        for e in excepciones:
            exc_map.setdefault(e['producto_id'], []).append({'mayorista_id': e['mayorista_id'], 'mayorista_nombre': e['mayorista_nombre'], 'precio': e['precio']})
        result = []
        for p in prods:
            result.append({
                'producto_id': p['id'], 'nombre': p['nombre'],
                'precio_base': p['precio'], 'precio_mayorista': p['precio_mayorista'],
                'excepciones': exc_map.get(p['id'], [])
            })
    return jsonify(result)


# ── API: Mayoristas B2B — Ingresos ───────────────────────────────────────────

@app.route('/api/mayoristas/ingresos', methods=['GET'])
@login_required
def api_may_ingresos():
    periodo = request.args.get('periodo', 'mes')  # semana/mes/año
    dias = {'semana': 7, 'mes': 30, 'año': 365}.get(periodo, 30)
    desde = str(date.today() - timedelta(days=dias - 1))
    with db() as c:
        rows = c.execute(
            """SELECT fecha_pago as fecha, COALESCE(SUM(monto),0) as total
               FROM mayoristas_cobranza
               WHERE estado='PAGADO' AND fecha_pago >= ?
               GROUP BY fecha_pago ORDER BY fecha_pago""",
            (desde,)
        ).fetchall()
        total = c.execute(
            "SELECT COALESCE(SUM(monto),0) FROM mayoristas_cobranza WHERE estado='PAGADO' AND fecha_pago >= ?",
            (desde,)
        ).fetchone()[0]
    return jsonify({'periodo': periodo, 'desde': desde, 'total': float(total), 'series': [dict(r) for r in rows]})


@app.route('/api/mayoristas/ingresos/por-cliente', methods=['GET'])
@login_required
def api_may_ingresos_por_cliente():
    periodo = request.args.get('periodo', 'mes')
    dias = {'semana': 7, 'mes': 30, 'año': 365}.get(periodo, 30)
    desde = str(date.today() - timedelta(days=dias - 1))
    with db() as c:
        rows = c.execute(
            """SELECT m.id, m.nombre, COALESCE(SUM(mc.monto),0) as ingreso,
                      COUNT(DISTINCT mp.id) as pedidos
               FROM mayoristas m
               LEFT JOIN mayoristas_pedidos mp ON mp.mayorista_id=m.id AND mp.fecha_entrega >= ?
               LEFT JOIN mayoristas_cobranza mc ON mc.mayorista_id=m.id AND mc.estado='PAGADO' AND mc.fecha_pago >= ?
               WHERE m.activo=1
               GROUP BY m.id ORDER BY ingreso DESC LIMIT 20""",
            (desde, desde)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/mayoristas/ingresos/por-producto', methods=['GET'])
@login_required
def api_may_ingresos_por_producto():
    periodo = request.args.get('periodo', 'mes')
    dias = {'semana': 7, 'mes': 30, 'año': 365}.get(periodo, 30)
    desde = str(date.today() - timedelta(days=dias - 1))
    with db() as c:
        rows = c.execute(
            """SELECT pr.id, pr.nombre,
                      COALESCE(SUM(mpi.cantidad),0) as unidades,
                      COALESCE(SUM(mpi.subtotal),0) as ingreso
               FROM mayoristas_pedido_items mpi
               JOIN productos pr ON pr.id = mpi.producto_id
               JOIN mayoristas_pedidos mp ON mp.id = mpi.pedido_id
               WHERE mp.fecha_entrega >= ? AND mp.estado NOT IN ('CANCELADO')
               GROUP BY pr.id ORDER BY ingreso DESC LIMIT 20""",
            (desde,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/mayoristas/ingresos/flujo', methods=['GET'])
@login_required
def api_may_ingresos_flujo():
    """Flujo mensual de ingresos de los últimos 12 meses."""
    with db() as c:
        rows = c.execute(
            """SELECT strftime('%Y-%m', fecha_pago) as mes,
                      COALESCE(SUM(monto),0) as total,
                      COUNT(*) as documentos
               FROM mayoristas_cobranza
               WHERE estado='PAGADO'
                 AND fecha_pago >= date('now', '-12 months')
               GROUP BY mes ORDER BY mes"""
        ).fetchall()
    return jsonify([dict(r) for r in rows])


# ── API: Mayoristas B2B — KPIs ────────────────────────────────────────────────

@app.route('/api/mayoristas/kpis', methods=['GET'])
@login_required
def api_may_kpis():
    _verificar_vencimientos_cobranza()
    hoy = date.today()
    lunes = hoy - timedelta(days=hoy.weekday())
    domingo = lunes + timedelta(days=6)
    with db() as c:
        clientes_activos = c.execute("SELECT COUNT(*) FROM mayoristas WHERE activo=1").fetchone()[0]
        vol_semanal = c.execute("SELECT COALESCE(SUM(volumen_semanal_panes),0) FROM mayoristas WHERE activo=1").fetchone()[0]
        pedidos_semana = c.execute(
            "SELECT COUNT(*) FROM mayoristas_pedidos WHERE fecha_entrega BETWEEN ? AND ? AND estado NOT IN ('CANCELADO')",
            (str(lunes), str(domingo))
        ).fetchone()[0]
        fact_mes = c.execute(
            "SELECT COALESCE(SUM(monto),0) FROM mayoristas_cobranza WHERE estado='PAGADO' AND strftime('%Y-%m',fecha_pago)=strftime('%Y-%m','now')"
        ).fetchone()[0]
        por_cobrar = c.execute(
            "SELECT COALESCE(SUM(monto),0) FROM mayoristas_cobranza WHERE estado IN ('PENDIENTE','VENCIDO')"
        ).fetchone()[0]
        deuda_vencida = c.execute(
            "SELECT COALESCE(SUM(monto),0) FROM mayoristas_cobranza WHERE estado='VENCIDO'"
        ).fetchone()[0]
        # Tasa conversion CRM B2B
        total_leads = c.execute("SELECT COUNT(*) FROM crm_leads WHERE modulo='B2B'").fetchone()[0]
        convertidos = c.execute("SELECT COUNT(*) FROM crm_leads WHERE modulo='B2B' AND convertido=1").fetchone()[0]
        tasa = round(convertidos / total_leads * 100, 1) if total_leads > 0 else 0.0
        # Pipeline B2B
        pipeline_rows = c.execute(
            "SELECT etapa, COUNT(*) as n FROM crm_leads WHERE modulo='B2B' AND convertido=0 GROUP BY etapa"
        ).fetchall()
        pipeline = {r['etapa']: r['n'] for r in pipeline_rows}
        # Top clientes
        top = c.execute(
            """SELECT m.id, m.nombre, m.volumen_semanal_panes as volumen,
                      COALESCE(SUM(mc.monto),0) as ltv
               FROM mayoristas m
               LEFT JOIN mayoristas_cobranza mc ON mc.mayorista_id=m.id AND mc.estado='PAGADO'
               WHERE m.activo=1
               GROUP BY m.id ORDER BY ltv DESC LIMIT 5"""
        ).fetchall()
        # Alertas: deuda vencida por cliente
        alertas_rows = c.execute(
            """SELECT m.nombre, COALESCE(SUM(mc.monto),0) as deuda
               FROM mayoristas_cobranza mc
               JOIN mayoristas m ON m.id=mc.mayorista_id
               WHERE mc.estado='VENCIDO'
               GROUP BY mc.mayorista_id
               HAVING deuda > 0
               ORDER BY deuda DESC LIMIT 10"""
        ).fetchall()
        alertas = [f"deuda vencida: {r['nombre']} ${r['deuda']:,.0f}" for r in alertas_rows]
    return jsonify({
        'clientes_activos':            clientes_activos,
        'volumen_semanal_comprometido': float(vol_semanal),
        'pedidos_semana':              pedidos_semana,
        'facturacion_mes':             float(fact_mes),
        'por_cobrar':                  float(por_cobrar),
        'deuda_vencida':               float(deuda_vencida),
        'tasa_conversion_crm':         tasa,
        'pipeline_b2b':                pipeline,
        'top_clientes':                [dict(t) for t in top],
        'alertas':                     alertas,
    })


# ── API: Mayoristas B2B — CRM Pipeline ──────────────────────────────────────

PIPELINE_B2B_MAY = ['DESCUBIERTO','CONTACTADO','DEGUSTACION','NEGOCIANDO','CLIENTE','PAUSADO','PERDIDO']

_REGLAS_PIPELINE_MAY = [
    # (etapa, dias_sin_actividad, titulo_tarea, cambio_etapa_si_aplica)
    ('CONTACTADO',  3, '2do intento WA + email', ''),
    ('CONTACTADO',  7, 'Reintento en 2 semanas', 'PAUSADO'),
    ('DEGUSTACION', 2, 'Confirmar recepcion + pedir feedback', ''),
    ('DEGUSTACION', 5, 'Llamar para seguimiento degustacion', ''),
    ('NEGOCIANDO',  4, 'Revisar propuesta pendiente', ''),
    ('CLIENTE',    30, 'Check-in mensual + oportunidad upsell', ''),
    ('PAUSADO',    45, 'Reactivacion', ''),
]


@app.route('/api/mayoristas/crm/prospectar', methods=['POST'])
@login_required
def api_may_crm_prospectar():
    if not GOOGLE_PLACES_API_KEY:
        return jsonify({'error': 'GOOGLE_PLACES_API_KEY no configurada'}), 400
    d = request.json or {}
    comunas = d.get('comunas', []) or COMUNAS_DESPACHO[:3]
    tipos   = d.get('tipos', ['restaurante','cafetería','café','hotel'])
    max_r   = int(d.get('max', 5))
    creados = 0
    omitidos = 0
    with db() as c:
        for tipo in tipos:
            for zona in comunas:
                results = _buscar_google_places(tipo, zona)
                for r in results:
                    if creados >= max_r:
                        break
                    score = _calcular_score_fit(r.get('tipo_negocio',''), r.get('zona',''), '')
                    if score < 3:
                        omitidos += 1
                        continue
                    tel = r.get('telefono','')
                    nom = r.get('nombre','')
                    dup = None
                    if tel:
                        dup = c.execute("SELECT id FROM crm_leads WHERE telefono=? LIMIT 1", (tel,)).fetchone()
                    if not dup:
                        dup = c.execute("SELECT id FROM crm_leads WHERE nombre=? AND zona=? LIMIT 1", (nom, zona)).fetchone()
                    if dup:
                        omitidos += 1
                        continue
                    c.execute(
                        """INSERT INTO crm_leads
                           (modulo, nombre, empresa, telefono, email, zona, direccion,
                            canal_origen, etapa, temperatura, tipo_negocio, score_fit, notas)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        ('B2B', nom, nom, tel, r.get('email',''), zona,
                         r.get('direccion',''), 'google_places', 'DESCUBIERTO', 'COLD',
                         r.get('tipo_negocio',''), score, '')
                    )
                    creados += 1
    return jsonify({'creados': creados, 'omitidos': omitidos})


@app.route('/api/mayoristas/crm/primer-contacto', methods=['POST'])
@login_required
def api_may_crm_primer_contacto():
    d = request.json or {}
    max_c = int(d.get('max', 5))
    cutoff = str(date.today() - timedelta(days=3))
    contactados = 0
    errores = []
    with db() as c:
        leads = c.execute(
            """SELECT * FROM crm_leads
               WHERE modulo='B2B' AND etapa='DESCUBIERTO'
                 AND (fecha_ultimo_contacto='' OR fecha_ultimo_contacto < ?)
               LIMIT ?""",
            (cutoff, max_c)
        ).fetchall()
        for lead in leads:
            tel = lead['telefono']
            if not tel:
                errores.append(f"{lead['nombre']}: sin teléfono")
                continue
            dias = []
            zona = lead['zona'] or 'Santiago'
            nombre_enc = lead['nombre']
            msj = (
                f"Hola {nombre_enc}, soy Nicolás de Aurora Bakers 🍞\n\n"
                f"Hacemos pan de masa madre artesanal en Recoleta y repartimos a {zona}.\n\n"
                f"¿Les gustaría probar una muestra gratis esta semana? "
                f"Solo necesito dirección y día preferido."
            )
            ok, err = _send_whatsapp_agent(tel, msj)
            hoy = str(date.today())
            c.execute(
                "UPDATE crm_leads SET etapa='CONTACTADO', fecha_ultimo_contacto=? WHERE id=?",
                (hoy, lead['id'])
            )
            c.execute(
                """INSERT INTO crm_interacciones (lead_id, tipo, direccion, asunto, contenido, resultado)
                   VALUES (?,?,?,?,?,?)""",
                (lead['id'], 'whatsapp', 'saliente', 'Primer contacto', msj, 'enviado' if ok else 'error')
            )
            venc_tarea = str(date.today() + timedelta(days=3))
            c.execute(
                """INSERT INTO crm_tareas (lead_id, titulo, tipo, prioridad, fecha_vencimiento)
                   VALUES (?,?,?,?,?)""",
                (lead['id'], 'Seguimiento primer contacto', 'seguimiento', 'alta', venc_tarea)
            )
            if ok:
                contactados += 1
            else:
                errores.append(f"{lead['nombre']}: {err}")
    return jsonify({'contactados': contactados, 'errores': errores})


@app.route('/api/mayoristas/crm/evaluar-pipeline', methods=['POST'])
@login_required
def api_may_crm_evaluar():
    hoy = date.today()
    creadas = 0
    movidos = 0
    with db() as c:
        for etapa, dias, titulo, nueva_etapa in _REGLAS_PIPELINE_MAY:
            cutoff = str(hoy - timedelta(days=dias))
            leads = c.execute(
                """SELECT * FROM crm_leads
                   WHERE modulo='B2B' AND etapa=? AND convertido=0
                     AND (fecha_ultimo_contacto='' OR fecha_ultimo_contacto <= ?)""",
                (etapa, cutoff)
            ).fetchall()
            for lead in leads:
                existe_tarea = c.execute(
                    "SELECT id FROM crm_tareas WHERE lead_id=? AND titulo=? AND completada=0",
                    (lead['id'], titulo)
                ).fetchone()
                if not existe_tarea:
                    venc = str(hoy + timedelta(days=2))
                    c.execute(
                        "INSERT INTO crm_tareas (lead_id, titulo, tipo, prioridad, fecha_vencimiento) VALUES (?,?,?,?,?)",
                        (lead['id'], titulo, 'seguimiento', 'alta', venc)
                    )
                    creadas += 1
                if nueva_etapa:
                    c.execute("UPDATE crm_leads SET etapa=? WHERE id=?", (nueva_etapa, lead['id']))
                    movidos += 1
    return jsonify({'tareas_creadas': creadas, 'leads_movidos': movidos})


@app.route('/api/mayoristas/crm/reporte', methods=['GET'])
@login_required
def api_may_crm_reporte():
    with db() as c:
        pipeline = {e: 0 for e in PIPELINE_B2B_MAY}
        for r in c.execute("SELECT etapa, COUNT(*) as n FROM crm_leads WHERE modulo='B2B' AND convertido=0 GROUP BY etapa").fetchall():
            pipeline[r['etapa']] = r['n']
        nuevos_semana = c.execute(
            "SELECT COUNT(*) FROM crm_leads WHERE modulo='B2B' AND fecha_creacion >= date('now','-7 days')"
        ).fetchone()[0]
        convertidos_mes = c.execute(
            "SELECT COUNT(*) FROM crm_leads WHERE modulo='B2B' AND convertido=1 AND fecha_ultimo_contacto >= date('now','-30 days')"
        ).fetchone()[0]
        tareas_pend = c.execute(
            "SELECT COUNT(*) FROM crm_tareas ct JOIN crm_leads cl ON cl.id=ct.lead_id WHERE cl.modulo='B2B' AND ct.completada=0"
        ).fetchone()[0]
    return jsonify({
        'pipeline':        pipeline,
        'nuevos_semana':   nuevos_semana,
        'convertidos_mes': convertidos_mes,
        'tareas_pendientes': tareas_pend,
    })


@app.route('/api/mayoristas/crm/convertir/<int:lid>', methods=['POST'])
@login_required
def api_may_crm_convertir(lid):
    with db() as c:
        lead = c.execute("SELECT * FROM crm_leads WHERE id=?", (lid,)).fetchone()
        if not lead:
            return jsonify({'error': 'Lead no encontrado'}), 404
        if not lead['nombre']:
            return jsonify({'error': 'Lead sin nombre'}), 400
        cur = c.execute(
            """INSERT INTO mayoristas
               (nombre, tipo_negocio, telefono, email, direccion, zona, notas, crm_lead_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (lead['nombre'], lead['tipo_negocio'] or '', lead['telefono'], lead['email'],
             lead['direccion'] or '', lead['zona'], lead['notas'], lid)
        )
        mid = cur.lastrowid
        c.execute("UPDATE crm_leads SET convertido=1, etapa='CLIENTE' WHERE id=?", (lid,))
        c.execute(
            """INSERT INTO crm_interacciones (lead_id, tipo, direccion, asunto, contenido, resultado)
               VALUES (?,?,?,?,?,?)""",
            (lid, 'sistema', 'interno', 'Conversion a cliente mayorista',
             f'Mayorista ID: {mid}', 'completado')
        )
    print(f"[mayoristas] Lead {lid} convertido a mayorista {mid}")
    return jsonify({'mayorista_id': mid, 'nombre': lead['nombre']})


# ── API: Mayoristas B2B — Produccion Semanal ─────────────────────────────────

@app.route('/api/mayoristas/produccion/semana', methods=['GET'])
@login_required
def api_may_produccion_semana():
    hoy = date.today()
    lunes = hoy - timedelta(days=hoy.weekday())
    domingo = lunes + timedelta(days=6)
    with db() as c:
        rows = c.execute(
            """SELECT mp.id as pedido_id, mp.fecha_entrega, mp.dia_entrega,
                      mp.notas_produccion, mp.estado, m.nombre as mayorista_nombre,
                      mpi.producto_id, mpi.cantidad, pr.nombre as producto_nombre
               FROM mayoristas_pedidos mp
               JOIN mayoristas m ON m.id = mp.mayorista_id
               JOIN mayoristas_pedido_items mpi ON mpi.pedido_id = mp.id
               JOIN productos pr ON pr.id = mpi.producto_id
               WHERE mp.fecha_entrega BETWEEN ? AND ?
                 AND mp.estado NOT IN ('CANCELADO')
               ORDER BY mp.fecha_entrega, m.nombre""",
            (str(lunes), str(domingo))
        ).fetchall()
        dias_data = {}
        for r in rows:
            dia = r['fecha_entrega'] or str(hoy)
            if dia not in dias_data:
                dias_data[dia] = {'total_panes': 0, 'por_producto': {}, 'pedidos': []}
            dias_data[dia]['total_panes'] += r['cantidad']
            prod_n = r['producto_nombre']
            dias_data[dia]['por_producto'][prod_n] = dias_data[dia]['por_producto'].get(prod_n, 0) + r['cantidad']
            pedido_existente = next((p for p in dias_data[dia]['pedidos'] if p['pedido_id'] == r['pedido_id']), None)
            if pedido_existente:
                pedido_existente['items'].append({'producto': prod_n, 'cantidad': r['cantidad']})
            else:
                dias_data[dia]['pedidos'].append({
                    'pedido_id':  r['pedido_id'],
                    'mayorista':  r['mayorista_nombre'],
                    'notas':      r['notas_produccion'],
                    'estado':     r['estado'],
                    'items':      [{'producto': prod_n, 'cantidad': r['cantidad']}]
                })
    semana_str = lunes.strftime('%G-W%V')
    return jsonify({'semana': semana_str, 'dias': dias_data})


@app.route('/api/mayoristas/produccion/dia/<fecha>', methods=['GET'])
@login_required
def api_may_produccion_dia(fecha):
    with db() as c:
        rows = c.execute(
            """SELECT pr.nombre as producto, COALESCE(SUM(mpi.cantidad),0) as cantidad,
                      GROUP_CONCAT(m.nombre || ':' || mpi.cantidad, ' | ') as detalle
               FROM mayoristas_pedidos mp
               JOIN mayoristas_pedido_items mpi ON mpi.pedido_id = mp.id
               JOIN productos pr ON pr.id = mpi.producto_id
               JOIN mayoristas m ON m.id = mp.mayorista_id
               WHERE mp.fecha_entrega=? AND mp.estado NOT IN ('CANCELADO')
               GROUP BY pr.id ORDER BY cantidad DESC""",
            (fecha,)
        ).fetchall()
    return jsonify({'fecha': fecha, 'productos': [dict(r) for r in rows]})


@app.route('/api/mayoristas/produccion/generar', methods=['POST'])
@login_required
def api_may_produccion_generar():
    """Genera entradas en plan_produccion para pedidos CONFIRMADOS sin plan."""
    generados = 0
    with db() as c:
        pedidos = c.execute(
            "SELECT * FROM mayoristas_pedidos WHERE estado='CONFIRMADO' AND fecha_entrega!=''"
        ).fetchall()
        for p in pedidos:
            batch_id = f"MAYORISTA-{p['id']}"
            ya_existe = c.execute(
                "SELECT COUNT(*) FROM plan_produccion WHERE batch_id=?", (batch_id,)
            ).fetchone()[0]
            if ya_existe:
                continue
            try:
                fecha_horn = str(date.fromisoformat(p['fecha_entrega']) - timedelta(days=1))
            except Exception:
                fecha_horn = p['fecha_entrega']
            items = c.execute(
                "SELECT mpi.*, pr.nombre FROM mayoristas_pedido_items mpi JOIN productos pr ON pr.id=mpi.producto_id WHERE mpi.pedido_id=?",
                (p['id'],)
            ).fetchall()
            for it in items:
                c.execute(
                    """INSERT INTO plan_produccion (fecha, codigo_producto, nombre_producto, cantidad, estado, notas, producto_id, batch_id, fecha_horneado)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (fecha_horn, f"MAY{it['producto_id']}", it['nombre'], it['cantidad'],
                     'pendiente', f"Mayorista pedido #{p['id']}", it['producto_id'], batch_id, fecha_horn)
                )
                generados += 1
    return jsonify({'ok': True, 'items_generados': generados})


# ── API: Reportes ─────────────────────────────────────────────────────────────

def _days_for(periodo):
    return {'hoy':1,'semana':7,'mes':30,'3meses':90,'año':365}.get(periodo,30)

@app.route('/api/reportes/ventas')
def api_rep_ventas():
    days  = _days_for(request.args.get('periodo','mes'))
    today = date.today()
    desde = today - timedelta(days=days-1)
    with db() as c:
        rows = c.execute(
            "SELECT fecha,COALESCE(SUM(total),0) AS total FROM ventas WHERE fecha>=? GROUP BY fecha ORDER BY fecha",
            (str(desde),)).fetchall()
        data = {r['fecha']: r['total'] for r in rows}
        labels = [str(desde+timedelta(days=i)) for i in range(days)]
        values = [data.get(l,0) for l in labels]
        return jsonify({'labels': labels, 'values': values})

@app.route('/api/reportes/productos')
def api_rep_productos():
    days  = _days_for(request.args.get('periodo','mes'))
    desde = str(date.today()-timedelta(days=days-1))
    with db() as c:
        rows = c.execute(
            """SELECT p.nombre,
                      COALESCE(SUM(vi.cantidad),0)                      AS cantidad,
                      COALESCE(SUM(vi.cantidad*vi.precio_unitario),0)   AS total
               FROM venta_items vi
               JOIN productos p ON p.id=vi.producto_id
               JOIN ventas    v ON v.id=vi.venta_id
               WHERE v.fecha>=?
               GROUP BY p.id ORDER BY total DESC""",
            (desde,)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/reportes/canales')
def api_rep_canales():
    days  = _days_for(request.args.get('periodo','mes'))
    desde = str(date.today()-timedelta(days=days-1))
    with db() as c:
        rows = c.execute(
            "SELECT canal,COALESCE(SUM(total),0) AS total,COUNT(*) AS count FROM ventas WHERE fecha>=? GROUP BY canal",
            (desde,)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/reportes/kpis')
def api_rep_kpis():
    days  = _days_for(request.args.get('periodo','mes'))
    today = date.today()
    desde = today - timedelta(days=days-1)
    prev  = desde - timedelta(days=days)
    with db() as c:
        def stats(d1, d2):
            r = c.execute("SELECT COALESCE(SUM(total),0),COUNT(*) FROM ventas WHERE fecha BETWEEN ? AND ?",
                          (str(d1),str(d2))).fetchone()
            return r[0], r[1]
        tot,  cnt  = stats(desde, today)
        ptot, pcnt = stats(prev,  desde-timedelta(days=1))
        ticket  = tot/cnt   if cnt  else 0
        pticket = ptot/pcnt if pcnt else 0
        subs  = c.execute("SELECT COUNT(*) FROM suscripciones WHERE estado='activo'").fetchone()[0]
        total_cli = c.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
        nuevos    = c.execute("SELECT COUNT(*) FROM clientes WHERE creado_en>=?", (str(desde),)).fetchone()[0]

        # Breakdown por tipo cliente
        horeca  = c.execute("SELECT COALESCE(SUM(total),0),COUNT(*) FROM ventas WHERE fecha>=? AND tipo_cliente='HORECA'",   (str(desde),)).fetchone()
        cliente = c.execute("SELECT COALESCE(SUM(total),0),COUNT(*) FROM ventas WHERE fecha>=? AND tipo_cliente='CLIENTE'", (str(desde),)).fetchone()

        return jsonify({
            'ventas_total': tot,   'ventas_count': cnt,
            'prev_total':   ptot,  'prev_count':   pcnt,
            'ticket':       ticket,'prev_ticket':  pticket,
            'subs_activas': subs,
            'total_clientes': total_cli,
            'clientes_nuevos': nuevos,
            'horeca_total':   horeca[0],  'horeca_count':   horeca[1],
            'cliente_total':  cliente[0], 'cliente_count':  cliente[1],
        })

# ── API: Reportes ────────────────────────────────────────────────────────────

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
        return hoy.replace(day=1).isoformat(), hoy.isoformat()

    desde, hasta = rango(periodo)
    ayer = (hoy - timedelta(days=1)).isoformat()

    d0 = date.fromisoformat(desde)
    delta = (hoy - d0).days or 1
    prev_hasta = (d0 - timedelta(days=1)).isoformat()
    prev_desde = (d0 - timedelta(days=delta)).isoformat()

    with db() as c:
        ventas_hoy = c.execute(
            "SELECT COALESCE(SUM(total),0) FROM ventas WHERE fecha=?", (hoy.isoformat(),)
        ).fetchone()[0]
        ventas_ayer = c.execute(
            "SELECT COALESCE(SUM(total),0) FROM ventas WHERE fecha=?", (ayer,)
        ).fetchone()[0]
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
        subs = c.execute(
            "SELECT COUNT(*) FROM suscripciones WHERE estado='activo'"
        ).fetchone()[0]
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
        ingresos = c.execute(
            "SELECT COALESCE(SUM(total),0) FROM ventas WHERE fecha BETWEEN ? AND ? AND estado_pago='PAGADO'",
            (desde, hasta)
        ).fetchone()[0]
        gastos = c.execute(
            "SELECT COALESCE(SUM(monto),0) FROM gastos WHERE fecha BETWEEN ? AND ?",
            (desde, hasta)
        ).fetchone()[0]
        por_cobrar = c.execute(
            "SELECT COALESCE(SUM(total),0) FROM ventas WHERE estado_pago='PENDIENTE'"
        ).fetchone()[0]
        prod_hoy = c.execute(
            "SELECT COALESCE(SUM(cantidad),0) FROM plan_produccion WHERE fecha=?",
            (hoy.isoformat(),)
        ).fetchone()[0]
        despachos_pendientes = c.execute(
            """SELECT COUNT(*) FROM ventas
               WHERE fecha_despacho=? AND con_despacho=1
                 AND estado_despacho NOT IN ('DESPACHADO','RETIRO EN TIENDA')""",
            (hoy.isoformat(),)
        ).fetchone()[0]
        stock_bajo = c.execute(
            "SELECT nombre, stock FROM productos WHERE activo=1 AND stock <= 5 ORDER BY stock ASC LIMIT 10"
        ).fetchall()

    return jsonify({
        'periodo': periodo, 'desde': desde, 'hasta': hasta,
        'ventas_hoy': ventas_hoy, 'ventas_ayer': ventas_ayer,
        'ventas_periodo': ventas_periodo, 'ventas_prev': ventas_prev,
        'count_periodo': count_periodo, 'ticket': ticket,
        'suscripciones_activas': subs,
        'por_canal': [dict(r) for r in canal_rows],
        'ingresos_cobrados': ingresos, 'gastos': gastos,
        'resultado': ingresos - gastos,
        'por_cobrar': por_cobrar,
        'prod_planificadas_hoy': prod_hoy,
        'despachos_pendientes_hoy': despachos_pendientes,
        'stock_bajo': [{'nombre': r['nombre'], 'stock': r['stock']} for r in stock_bajo],
    })


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
    else:
        desde, hasta = hoy.replace(day=1).isoformat(), hoy.isoformat()

    with db() as c:
        rows = c.execute(
            """SELECT estado, SUM(cantidad) as total
               FROM plan_produccion WHERE fecha BETWEEN ? AND ?
               GROUP BY estado""",
            (desde, hasta)
        ).fetchall()
        est = {r['estado']: r['total'] for r in rows}
        total_planificado = sum(est.values())
        total_producido   = est.get('horneado', 0) + est.get('listo', 0)
        cumplimiento = round(total_producido / total_planificado * 100, 1) if total_planificado else 0

        dias = c.execute(
            """SELECT fecha,
                      SUM(cantidad) as planificado,
                      SUM(CASE WHEN estado IN ('horneado','listo') THEN cantidad ELSE 0 END) as producido
               FROM plan_produccion WHERE fecha BETWEEN ? AND ?
               GROUP BY fecha ORDER BY fecha""",
            (desde, hasta)
        ).fetchall()

        prods = c.execute(
            """SELECT nombre_producto,
                      SUM(cantidad) as planificado,
                      SUM(CASE WHEN estado IN ('horneado','listo') THEN cantidad ELSE 0 END) as producido
               FROM plan_produccion WHERE fecha BETWEEN ? AND ?
               GROUP BY nombre_producto ORDER BY planificado DESC""",
            (desde, hasta)
        ).fetchall()

    return jsonify({
        'periodo': periodo, 'desde': desde, 'hasta': hasta,
        'kpis': {
            'total_planificado': total_planificado,
            'total_producido':   total_producido,
            'cumplimiento_pct':  cumplimiento,
            'producto_top':      prods[0]['nombre_producto'] if prods else None,
        },
        'por_dia':      [dict(r) for r in dias],
        'por_producto': [dict(r) for r in prods],
    })


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
    else:
        desde, hasta = hoy.replace(day=1).isoformat(), hoy.isoformat()

    with db() as c:
        rows = c.execute(
            """SELECT estado_despacho, COUNT(*) as count
               FROM ventas WHERE con_despacho=1 AND fecha_despacho BETWEEN ? AND ?
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
               FROM ventas WHERE con_despacho=1 AND fecha_despacho BETWEEN ? AND ?
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
               FROM ventas WHERE con_despacho=1 AND fecha_despacho BETWEEN ? AND ?
               GROUP BY segmento ORDER BY total DESC""",
            (desde, hasta)
        ).fetchall()

    return jsonify({
        'periodo': periodo, 'desde': desde, 'hasta': hasta,
        'kpis': {
            'total': total, 'despachados': despachados,
            'pendientes': pendientes, 'tasa_pct': tasa,
        },
        'por_estado': [dict(r) for r in rows],
        'por_dia':    [dict(r) for r in por_dia],
        'por_canal':  [dict(r) for r in por_canal],
    })


# ── API: Resumen para agentes (WhatsApp bot / Railway) ───────────────────────

@app.route('/api/agentes/resumen')
def api_agentes_resumen():
    """
    Endpoint consumido por el sistema multi-agente (aurora-bakers en Railway).
    Devuelve datos consolidados sin necesitar acceso a Google Sheets.
    Auth: la valida _global_auth (X-Agent-Key / ?key= / sesión web).
    """
    today = date.today()
    w0    = today - timedelta(days=today.weekday())
    m0    = today.replace(day=1)
    desde_param = request.args.get('desde', str(m0))
    hasta_param = request.args.get('hasta', str(today))

    with db() as c:
        def t(d1, d2):
            r = c.execute("SELECT COALESCE(SUM(total),0),COUNT(*) FROM ventas WHERE fecha BETWEEN ? AND ?",
                          (str(d1),str(d2))).fetchone()
            return {'total': r[0], 'count': r[1]}

        # KPIs básicos
        kpi_hoy    = t(today, today)
        kpi_semana = t(w0, today)
        kpi_mes    = t(m0, today)

        # Pendientes
        pend_pago     = c.execute("SELECT COUNT(*) FROM ventas WHERE estado_pago='PENDIENTE'").fetchone()[0]
        pend_despacho = c.execute("SELECT COUNT(*) FROM ventas WHERE estado_despacho='PENDIENTE'").fetchone()[0]

        # Despachos de hoy y mañana
        manana = str(today + timedelta(days=1))
        despachos_hoy    = [dict(r) for r in c.execute(
            "SELECT v.*,c.nombre AS cliente_nombre FROM ventas v LEFT JOIN clientes c ON c.id=v.cliente_id WHERE v.fecha_despacho=? AND v.estado_despacho='PENDIENTE'",
            (str(today),)).fetchall()]
        despachos_manana = [dict(r) for r in c.execute(
            "SELECT v.*,c.nombre AS cliente_nombre FROM ventas v LEFT JOIN clientes c ON c.id=v.cliente_id WHERE v.fecha_despacho=? AND v.estado_despacho='PENDIENTE'",
            (manana,)).fetchall()]

        # Suscripciones activas
        subs_activas = c.execute("SELECT COUNT(*) FROM suscripciones WHERE estado='activo'").fetchone()[0]
        subs_renovar = [dict(r) for r in c.execute(
            """SELECT s.*,c.nombre AS cliente_nombre,c.telefono AS cliente_telefono
               FROM suscripciones s JOIN clientes c ON c.id=s.cliente_id
               WHERE s.estado='activo' AND s.fecha_renovacion <= ?
               ORDER BY s.fecha_renovacion""",
            (str(today + timedelta(days=3)),)).fetchall()]

        # Top productos del período
        top_prods = [dict(r) for r in c.execute(
            """SELECT p.nombre, SUM(vi.cantidad) AS cantidad, SUM(vi.cantidad*vi.precio_unitario) AS total
               FROM venta_items vi JOIN productos p ON p.id=vi.producto_id
               JOIN ventas v ON v.id=vi.venta_id
               WHERE v.fecha BETWEEN ? AND ?
               GROUP BY p.id ORDER BY total DESC LIMIT 5""",
            (desde_param, hasta_param)).fetchall()]

        # Breakdown HORECA vs CLIENTE
        horeca  = c.execute("SELECT COALESCE(SUM(total),0),COUNT(*) FROM ventas WHERE fecha>=? AND tipo_cliente='HORECA'",   (desde_param,)).fetchone()
        cliente = c.execute("SELECT COALESCE(SUM(total),0),COUNT(*) FROM ventas WHERE fecha>=? AND tipo_cliente='CLIENTE'", (desde_param,)).fetchone()

        # Clientes inactivos (sin compra en 21 días)
        corte = str(today - timedelta(days=21))
        inactivos = [dict(r) for r in c.execute(
            """SELECT c.nombre, c.telefono, MAX(v.fecha) AS ultima_compra
               FROM clientes c LEFT JOIN ventas v ON v.cliente_id=c.id
               GROUP BY c.id
               HAVING ultima_compra IS NULL OR ultima_compra < ?
               ORDER BY ultima_compra ASC LIMIT 10""",
            (corte,)).fetchall()]

        return jsonify({
            'fecha':          str(today),
            'kpi': {
                'hoy':    kpi_hoy,
                'semana': kpi_semana,
                'mes':    kpi_mes,
            },
            'pendientes': {
                'pago':     pend_pago,
                'despacho': pend_despacho,
            },
            'despachos': {
                'hoy':    despachos_hoy,
                'manana': despachos_manana,
            },
            'suscripciones': {
                'activas': subs_activas,
                'por_renovar': subs_renovar,
            },
            'top_productos': top_prods,
            'segmento': {
                'horeca':  {'total': horeca[0],  'count': horeca[1]},
                'cliente': {'total': cliente[0], 'count': cliente[1]},
            },
            'clientes_inactivos': inactivos,
        })


@app.route('/api/agentes/despachos-hoy')
def api_agentes_despachos():
    """Lista de despachos pendientes para hoy — para el plan de producción.
    Auth: la valida _global_auth (X-Agent-Key / ?key= / sesión web)."""
    fecha = request.args.get('fecha', str(date.today()))
    with db() as c:
        rows = c.execute(
            """SELECT v.*, c.nombre AS cliente_nombre, c.telefono AS cliente_telefono, c.direccion AS cliente_direccion
               FROM ventas v LEFT JOIN clientes c ON c.id=v.cliente_id
               WHERE v.fecha_despacho=? AND v.con_despacho=1
               ORDER BY v.estado_despacho, v.creado_en""",
            (fecha,)).fetchall()
        result = []
        for v in rows:
            vd = dict(v)
            vd['items'] = [dict(i) for i in c.execute(
                "SELECT vi.*,p.nombre FROM venta_items vi JOIN productos p ON p.id=vi.producto_id WHERE vi.venta_id=?",
                (v['id'],)).fetchall()]
            result.append(vd)
        return jsonify({'fecha': fecha, 'despachos': result})

# ── CRM: Páginas ─────────────────────────────────────────────────────────────

@app.route('/crm')
@module_required('crm')
def page_crm():
    return render_template('crm.html', active='crm')

@app.route('/crm/lead/<int:lid>')
@module_required('crm')
def page_crm_lead(lid):
    return render_template('crm_lead.html', active='crm', lead_id=lid)

@app.route('/crm/buscar')
@module_required('crm')
def page_crm_buscar():
    return render_template('crm_buscar.html', active='crm')

@app.route('/crm/email-masivo')
@module_required('crm')
def page_crm_email_masivo():
    return render_template('crm_email_masivo.html', active='crm')

# ── CRM: Helpers ──────────────────────────────────────────────────────────────

PIPELINES = {
    'B2C': ['NUEVO_CONTACTO','CALIFICADO','PRIMERA_COMPRA','RECURRENTE','SUSCRIPTOR_CLUB','INACTIVO'],
    'B2B': ['PROSPECTO','CONTACTADO','MUESTRA_ENVIADA','NEGOCIACION','CONTRATO_FIRMADO','ONBOARDING','CUENTA_ACTIVA','EN_RIESGO'],
}

TAGS_B2C = ['Perfil Saludable','Perfil Familiar','Gourmet','Suscriptor Club','Alérgico Gluten','Vegano','Alto LTV','Reactivar']
TAGS_B2B = ['Cafetería','Restaurante','Hotel','Panadería','Casino','Catering','Alto Volumen','Crédito Aprobado','Riesgo Churn']

TEMPLATES_MENSAJES = {
    'B2C': [
        {
            'id': 'b2c_bienvenida',
            'nombre': '🎉 Bienvenida — Primera Compra',
            'canal': 'whatsapp',
            'asunto': '',
            'cuerpo': "¡Hola {nombre}! 🍞 Soy {remitente} de Aurora Bakers. Gracias por tu primera compra. Tu {producto} fue horneado esta mañana con masa madre de 48 horas de fermentación. ¿Llegó en buenas condiciones? Cualquier consulta, aquí estoy.",
        },
        {
            'id': 'b2c_reabastecimiento',
            'nombre': '⏰ Alerta de Reabastecimiento (5 días)',
            'canal': 'whatsapp',
            'asunto': '',
            'cuerpo': "¡Hola {nombre}! 🌾 Han pasado 5 días desde tu último pan. ¿Ya se te acabó el {producto}? Esta semana tenemos hogazas recién salidas del horno — te reservo una si quieres. 🔥 Responde con 'SÍ' y te la aparto.",
        },
        {
            'id': 'b2c_club_invitacion',
            'nombre': '⭐ Invitación al Club del Pan',
            'canal': 'email',
            'asunto': 'Te invitamos al Club del Pan Aurora — Beneficios exclusivos 🍞',
            'cuerpo': "Hola {nombre},\n\nLlevamos {meses} meses compartiendo el ritual del buen pan contigo. Por eso queremos invitarte al Club del Pan Aurora: entrega semanal garantizada, precio preferencial y acceso anticipado a ediciones especiales.\n\nPlan desde $XX.000/mes · 4 entregas · Sin sorpresas.\n\n¿Te cuento más?\n\nCon gusto,\n{remitente}\nAurora Bakers",
        },
    ],
    'B2B': [
        {
            'id': 'b2b_propuesta_inicial',
            'nombre': '📋 Propuesta Inicial — Envío de Catálogo',
            'canal': 'email',
            'asunto': 'Propuesta de suministro de pan artesanal — Aurora Bakers',
            'cuerpo': "Estimado/a {nombre},\n\nMe dirijo a usted desde Aurora Bakers, panadería artesanal especializada en masa madre premium para el sector HORECA.\n\nNos especializamos en la provisión confiable y estandarizada de:\n• Ciabattas y focaccias para restaurantes\n• Pan de molde premium para hoteles\n• Panes especiales para cafeterías de especialidad\n\nGarantizamos entrega diaria a las 07:00 hrs, gramaje exacto y temperatura controlada.\n\nAdjunto nuestro catálogo técnico con fichas de producto y lista de precios. ¿Podríamos agendar una cata esta semana?\n\nQuedo atento/a,\n{remitente}\nAurora Bakers | {telefono}",
        },
        {
            'id': 'b2b_seguimiento_muestra',
            'nombre': '🎁 Seguimiento Post-Muestra',
            'canal': 'email',
            'asunto': 'Seguimiento — ¿Qué les pareció nuestra muestra?',
            'cuerpo': "Hola {nombre},\n\nEspero que hayas tenido la oportunidad de probar las muestras que enviamos la semana pasada.\n\nNos encantaría saber la opinión de tu equipo, especialmente sobre:\n• Textura y miga de la ciabatta\n• Gramaje y estandarización\n• Condiciones de entrega\n\nSi todo estuvo a la altura, me gustaría que conversemos los volúmenes semanales y condiciones comerciales. ¿Tienes 20 minutos esta semana para una llamada?\n\nSaludos,\n{remitente}",
        },
        {
            'id': 'b2b_alerta_churn',
            'nombre': '🚨 Alerta Riesgo Churn — Llamada Urgente',
            'canal': 'whatsapp',
            'asunto': '',
            'cuerpo': "Hola {nombre}, soy {remitente} de Aurora Bakers. Noté que los últimos días el pedido bajó. ¿Todo bien con el servicio? Si hay algo que no esté funcionando como esperabas — calidad, logística, cantidades — cuéntame y lo resolvemos hoy mismo. Tu operación es prioridad para nosotros. 🙏",
        },
    ],
}

def _enrich_lead(row):
    r = dict(row)
    try:    r['tags']        = json.loads(r.get('tags_json','[]'))
    except: r['tags']        = []
    try:    r['propiedades'] = json.loads(r.get('propiedades_json','{}'))
    except: r['propiedades'] = {}
    return r


def _buscar_google_places(query: str, zona: str, direccion_local: str = '') -> list:
    if not GOOGLE_PLACES_API_KEY:
        return []
    try:
        lugar  = direccion_local if direccion_local else zona
        texto  = f"{query} en {lugar}"
        body   = json.dumps({
            'textQuery':    texto,
            'languageCode': 'es',
            'maxResultCount': 10,
        }).encode('utf-8')
        url    = 'https://places.googleapis.com/v1/places:searchText'
        fields = 'places.displayName,places.formattedAddress,places.rating,places.userRatingCount,places.types,places.id,places.internationalPhoneNumber'
        req    = urllib.request.Request(url, data=body, method='POST', headers={
            'Content-Type':    'application/json',
            'X-Goog-Api-Key':  GOOGLE_PLACES_API_KEY,
            'X-Goog-FieldMask': fields,
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())

        results = []
        for p in data.get('places', []):
            rating  = p.get('rating', 3)
            reviews = p.get('userRatingCount', 0)
            score   = min(100, int(rating / 5 * 100))
            types   = p.get('types', [])
            tipo    = _google_type_label(types)
            nombre  = _limpiar_texto(p.get('displayName', {}).get('text', ''))
            telefono= p.get('internationalPhoneNumber', '')
            results.append({
                'nombre':       nombre,
                'empresa':      nombre,
                'direccion':    _limpiar_texto(p.get('formattedAddress', '')),
                'zona':         zona,
                'telefono':     telefono,
                'email':        '',
                'canal_origen': 'google_places',
                'tipo_negocio': tipo,
                'score':        score,
                'razon':        f"Rating {rating}★ · {reviews} reseñas · {tipo}",
                'place_id':     p.get('id', ''),
            })
        return results
    except Exception as e:
        print(f"[google_places] Error: {e}")
        return []


def _google_type_label(types: list) -> str:
    """Convierte los tipos de Google Places a etiqueta legible."""
    priority = [
        ('restaurant','Restaurante'), ('cafe','Cafetería'), ('bar','Bar'),
        ('hotel','Hotel'), ('lodging','Alojamiento'), ('supermarket','Supermercado'),
        ('grocery_or_supermarket','Almacén / Minimarket'), ('store','Tienda'),
        ('food','Negocio gastronómico'), ('bakery','Panadería'),
        ('meal_takeaway','Comida para llevar'), ('catering_service','Catering'),
    ]
    for key, label in priority:
        if key in types:
            return label
    return types[0].replace('_',' ').capitalize() if types else 'Negocio'


def _buscar_leads_ia(descripcion: str, modulo: str, zona: str) -> list:
    if not ANTHROPIC_API_KEY:
        return []
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        if modulo == 'B2B':
            instruccion = (
                f"Eres un experto en prospección comercial B2B para Aurora Bakers, panadería artesanal "
                f"de masa madre premium en Santiago, Chile.\n"
                f"Categoría buscada: {descripcion}\nZona: {zona}\n\n"
                f"Genera 6 fichas de negocios REALES y TÍPICOS de {zona} que podrían comprar pan artesanal "
                f"(restaurantes, cafeterías, hoteles, almacenes gourmet, casinos corporativos, caterings).\n"
                f"Usa nombres comerciales verosímiles para Chile. Incluye dirección aproximada en {zona}.\n"
                f"Devuelve SOLO este JSON (sin texto adicional):\n"
                f'[{{"nombre":"Nombre del negocio","empresa":"Nombre del negocio","cargo":"Dueño / Encargado de compras",'
                f'"tipo_negocio":"Restaurante|Cafetería|Hotel|Almacén|Casino|Catering","zona":"{zona}",'
                f'"direccion":"Calle aproximada, {zona}","email":"","telefono":"",'
                f'"razon":"Por qué necesita pan artesanal","score":80}}]'
            )
        else:
            instruccion = (
                f"Eres un experto en prospección B2C para Aurora Bakers, panadería artesanal en Santiago.\n"
                f"Perfil buscado: {descripcion}\nZona: {zona}\n\n"
                f"Genera 6 perfiles de personas naturales de {zona} que serían clientes ideales "
                f"(familias, profesionales con perfil saludable, veganos, foodies, etc.).\n"
                f"Devuelve SOLO este JSON:\n"
                f'[{{"nombre":"Nombre Apellido","empresa":"","cargo":"","tipo_negocio":"Consumidor final",'
                f'"zona":"{zona}","direccion":"Sector {zona}","email":"","telefono":"",'
                f'"razon":"Por qué le interesaría el pan artesanal","score":75}}]'
            )
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=1200,
            messages=[{'role':'user','content':instruccion}]
        )
        raw = resp.content[0].text.strip()
        start = raw.find('[')
        end   = raw.rfind(']') + 1
        leads = json.loads(raw[start:end])
        for l in leads:
            l['canal_origen'] = 'ia_sugerido'
            for campo in ('nombre', 'empresa', 'direccion', 'razon', 'cargo'):
                if campo in l:
                    l[campo] = _limpiar_texto(str(l[campo]))
        return leads
    except Exception as e:
        print(f"[ia_leads] Error: {e}")
        return []


def _buscar_leads_keywords(descripcion: str, modulo: str, zona: str) -> list:
    """Fallback: sugerencias predeterminadas basadas en palabras clave."""
    desc_l = descripcion.lower()
    sugerencias = []
    if modulo == 'B2B':
        tipos = []
        if any(x in desc_l for x in ['café','cafeteria','cafetería','coffee']): tipos.append('Cafetería')
        if any(x in desc_l for x in ['restaurant','restaurante','bistro']): tipos.append('Restaurante')
        if any(x in desc_l for x in ['hotel','hostal','apart']): tipos.append('Hotel')
        if any(x in desc_l for x in ['casino','corporat','empresa']): tipos.append('Casino Corporativo')
        if not tipos: tipos = ['Cafetería','Restaurante','Hotel']
        nombres_demo = [
            f'Café Grano Vivo — {zona}',f'Restaurante La Leña — {zona}',
            f'Hotel Boutique Sur — {zona}',f'Bistró El Mercado — {zona}',
            f'Café de Especialidad {zona}',
        ]
        for i, t in enumerate(tipos[:3]):
            sugerencias.append({
                'nombre': nombres_demo[i] if i < len(nombres_demo) else f'{t} {zona}',
                'empresa': nombres_demo[i] if i < len(nombres_demo) else f'{t} {zona}',
                'cargo': 'Administrador / Chef',
                'tipo_negocio': t,
                'zona': zona,
                'email': '',
                'telefono': '',
                'canal_origen': 'sugerencia',
                'razon': f'Perfil típico de cliente HORECA en {zona} con alto consumo de pan artesanal',
                'score': 70,
            })
    else:
        perfiles = []
        if any(x in desc_l for x in ['saludable','integral','fitness']): perfiles.append('Perfil Saludable')
        if any(x in desc_l for x in ['familia','niños','hogar']): perfiles.append('Perfil Familiar')
        if any(x in desc_l for x in ['gourmet','premium','fin de semana']): perfiles.append('Gourmet')
        if not perfiles: perfiles = ['Perfil Saludable']
        for p in perfiles[:3]:
            sugerencias.append({
                'nombre': f'Prospecto {p}',
                'empresa': '',
                'cargo': '',
                'tipo_negocio': p,
                'zona': zona,
                'email': '',
                'telefono': '',
                'canal_origen': 'sugerencia',
                'razon': f'Coincide con el perfil "{p}" buscado en zona {zona}',
                'score': 65,
            })
    return sugerencias


def _smtp_cfg():
    cfg = _load_config()
    return {
        'host': os.environ.get('SMTP_HOST', '') or cfg.get('smtp_host', 'smtp.gmail.com'),
        'port': int(os.environ.get('SMTP_PORT', '0') or cfg.get('smtp_port', 587)),
        'user': os.environ.get('SMTP_USER', '') or cfg.get('smtp_user', ''),
        'pass': os.environ.get('SMTP_PASS', '') or cfg.get('smtp_pass', ''),
        'from': os.environ.get('SMTP_FROM', '') or cfg.get('smtp_from', '') or
                os.environ.get('SMTP_USER', '') or cfg.get('smtp_user', ''),
    }

def _send_crm_email(destinatario: str, asunto: str, cuerpo: str) -> tuple:
    """Retorna (ok: bool, error: str)."""
    s = _smtp_cfg()
    if not s['user'] or not s['pass']:
        return False, 'SMTP no configurado'
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = asunto
        msg['From']    = f"Aurora Bakers <{s['from']}>"
        msg['To']      = destinatario
        msg.attach(MIMEText(cuerpo, 'plain', 'utf-8'))
        with smtplib.SMTP(s['host'], s['port']) as srv:
            srv.ehlo(); srv.starttls(); srv.ehlo()
            srv.login(s['user'], s['pass'])
            srv.sendmail(s['from'], [destinatario], msg.as_string())
        return True, ''
    except Exception as e:
        print(f"[crm_email] Error: {e}")
        return False, str(e)


# ── CRM: API Leads ────────────────────────────────────────────────────────────

@app.route('/api/crm/leads', methods=['GET'])
def api_crm_leads():
    modulo   = request.args.get('modulo','')
    etapa    = request.args.get('etapa','')
    temp     = request.args.get('temperatura','')
    q        = request.args.get('q','')
    with db() as c:
        sql = "SELECT * FROM crm_leads WHERE 1=1"
        params = []
        if modulo: sql += " AND modulo=?";     params.append(modulo)
        if etapa:  sql += " AND etapa=?";      params.append(etapa)
        if temp:   sql += " AND temperatura=?";params.append(temp)
        if q:
            sql += " AND (nombre LIKE ? OR empresa LIKE ? OR email LIKE ? OR telefono LIKE ?)"
            params += [f'%{q}%']*4
        sql += " ORDER BY fecha_creacion DESC"
        rows = c.execute(sql, params).fetchall()
        return jsonify([_enrich_lead(r) for r in rows])


@app.route('/api/crm/leads', methods=['POST'])
def api_crm_leads_create():
    d = request.json
    with db() as c:
        tel   = d.get('telefono', '').strip()
        email_val = d.get('email', '').strip()
        dup = None
        if tel:
            dup = c.execute("SELECT id,nombre FROM crm_leads WHERE telefono=? LIMIT 1", (tel,)).fetchone()
        if not dup and email_val:
            dup = c.execute("SELECT id,nombre FROM crm_leads WHERE email=? LIMIT 1", (email_val,)).fetchone()
        if dup and not d.get('force_create'):
            return jsonify({'error': 'duplicado', 'lead_id': dup['id'], 'nombre': dup['nombre'],
                            'mensaje': f"Ya existe lead con este contacto: {dup['nombre']}"}), 409
        cur = c.execute(
            """INSERT INTO crm_leads
               (modulo,nombre,email,telefono,empresa,cargo,rut,zona,direccion,canal_origen,
                etapa,temperatura,tags_json,propiedades_json,notas,valor_potencial,asignado_a,
                fecha_proximo_contacto)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d.get('modulo','B2C'), d['nombre'], d.get('email',''), d.get('telefono',''),
             d.get('empresa',''), d.get('cargo',''), d.get('rut',''), d.get('zona',''),
             d.get('direccion',''),
             d.get('canal_origen','manual'),
             d.get('etapa', PIPELINES[d.get('modulo','B2C')][0]),
             d.get('temperatura','COLD'),
             json.dumps(d.get('tags',[])),
             json.dumps(d.get('propiedades',{})),
             d.get('notas',''), float(d.get('valor_potencial',0)),
             d.get('asignado_a',''),
             d.get('fecha_proximo_contacto',''))
        )
        return jsonify(_enrich_lead(c.execute("SELECT * FROM crm_leads WHERE id=?", (cur.lastrowid,)).fetchone())), 201


@app.route('/api/crm/leads/<int:lid>', methods=['GET'])
def api_crm_lead_get(lid):
    with db() as c:
        row = c.execute("SELECT * FROM crm_leads WHERE id=?", (lid,)).fetchone()
        if not row: return jsonify({'error':'No encontrado'}), 404
        lead = _enrich_lead(row)
        lead['interacciones'] = [dict(r) for r in c.execute(
            "SELECT * FROM crm_interacciones WHERE lead_id=? ORDER BY fecha DESC", (lid,)).fetchall()]
        lead['tareas'] = [dict(r) for r in c.execute(
            "SELECT * FROM crm_tareas WHERE lead_id=? ORDER BY completada ASC, fecha_vencimiento ASC", (lid,)).fetchall()]
        return jsonify(lead)


@app.route('/api/crm/leads/<int:lid>', methods=['PUT'])
def api_crm_lead_update(lid):
    d = request.json
    with db() as c:
        # Actualizar etapa y/o campos generales
        fields, params = [], []
        for col in ['nombre','email','telefono','empresa','cargo','rut','zona','direccion','etapa',
                    'temperatura','notas','valor_potencial','asignado_a',
                    'fecha_ultimo_contacto','fecha_proximo_contacto','convertido']:
            if col in d:
                fields.append(f"{col}=?")
                params.append(d[col])
        if 'tags' in d:
            fields.append("tags_json=?"); params.append(json.dumps(d['tags']))
        if 'propiedades' in d:
            fields.append("propiedades_json=?"); params.append(json.dumps(d['propiedades']))
        if fields:
            params.append(lid)
            if 'etapa' in d:
                etapa_actual = c.execute("SELECT etapa FROM crm_leads WHERE id=?", (lid,)).fetchone()
                etapa_prev = etapa_actual['etapa'] if etapa_actual else ''
                if etapa_prev != d['etapa']:
                    c.execute(
                        "INSERT INTO crm_etapa_log (lead_id,etapa_from,etapa_to,usuario) VALUES (?,?,?,?)",
                        (lid, etapa_prev, d['etapa'], d.get('usuario', 'sistema'))
                    )
            if d.get('etapa') == 'PERDIDO' and d.get('razon_perdida'):
                fields.append("razon_perdida=?"); params.insert(-1, d['razon_perdida'])
            c.execute(f"UPDATE crm_leads SET {','.join(fields)} WHERE id=?", params)
        if d.get('convertido') == 1:
            lead_row = c.execute("SELECT * FROM crm_leads WHERE id=?", (lid,)).fetchone()
            if lead_row and not lead_row['cliente_id']:
                tel = lead_row['telefono']
                cliente = c.execute("SELECT id FROM clientes WHERE telefono=? LIMIT 1", (tel,)).fetchone() if tel else None
                if cliente:
                    c.execute("UPDATE crm_leads SET cliente_id=? WHERE id=?", (cliente['id'], lid))
                    ltv = c.execute(
                        "SELECT COALESCE(SUM(total),0) FROM ventas WHERE cliente_id=?", (cliente['id'],)
                    ).fetchone()[0]
                    c.execute("UPDATE crm_leads SET valor_potencial=? WHERE id=? AND valor_potencial=0", (ltv, lid))
        return jsonify(_enrich_lead(c.execute("SELECT * FROM crm_leads WHERE id=?", (lid,)).fetchone()))


@app.route('/api/crm/leads/<int:lid>', methods=['DELETE'])
def api_crm_lead_delete(lid):
    with db() as c:
        c.execute("DELETE FROM crm_leads WHERE id=?", (lid,))
        return jsonify({'ok': True})


# ── CRM: Interacciones ────────────────────────────────────────────────────────

@app.route('/api/crm/leads/<int:lid>/interacciones', methods=['GET'])
def api_crm_interacciones_list(lid):
    with db() as c:
        rows = c.execute(
            "SELECT * FROM crm_interacciones WHERE lead_id=? ORDER BY fecha DESC", (lid,)
        ).fetchall()
        return jsonify([dict(r) for r in rows])


@app.route('/api/crm/leads/<int:lid>/interacciones', methods=['POST'])
def api_crm_interaccion_create(lid):
    d = request.json
    with db() as c:
        c.execute(
            "INSERT INTO crm_interacciones (lead_id,tipo,direccion,asunto,contenido,resultado,fecha) VALUES (?,?,?,?,?,?,?)",
            (lid, d.get('tipo','email'), d.get('direccion','saliente'),
             d.get('asunto',''), d.get('contenido',''), d.get('resultado',''),
             d.get('fecha', datetime.now().strftime('%Y-%m-%d %H:%M')))
        )
        # Actualizar fecha_ultimo_contacto
        c.execute("UPDATE crm_leads SET fecha_ultimo_contacto=? WHERE id=?",
                  (str(date.today()), lid))
        return jsonify({'ok': True}), 201


# ── CRM: Email ────────────────────────────────────────────────────────────────

@app.route('/api/crm/leads/<int:lid>/email', methods=['POST'])
def api_crm_send_email(lid):
    d = request.json
    with db() as c:
        lead = c.execute("SELECT * FROM crm_leads WHERE id=?", (lid,)).fetchone()
        if not lead: return jsonify({'error': 'Lead no encontrado'}), 404
        destinatario = d.get('destinatario') or lead['email']
        if not destinatario:
            return jsonify({'error': 'El lead no tiene email registrado'}), 400
        ok, err = _send_crm_email(destinatario, d.get('asunto',''), d.get('cuerpo',''))
        if ok:
            c.execute(
                "INSERT INTO crm_interacciones (lead_id,tipo,direccion,asunto,contenido,resultado,fecha) VALUES (?,?,?,?,?,?,?)",
                (lid, 'email', 'saliente', d.get('asunto',''), d.get('cuerpo',''),
                 'enviado', datetime.now().strftime('%Y-%m-%d %H:%M'))
            )
            c.execute("UPDATE crm_leads SET fecha_ultimo_contacto=? WHERE id=?", (str(date.today()), lid))
        s = _smtp_cfg()
        return jsonify({'ok': ok, 'error': err, 'smtp_configurado': bool(s['user'] and s['pass'])})


# ── CRM: Tareas ───────────────────────────────────────────────────────────────

@app.route('/api/crm/tareas', methods=['GET'])
def api_crm_tareas():
    solo_pendientes = request.args.get('pendientes','1') == '1'
    lid = request.args.get('lead_id','')
    with db() as c:
        sql = """SELECT t.*,l.nombre AS lead_nombre,l.empresa,l.modulo
                 FROM crm_tareas t LEFT JOIN crm_leads l ON l.id=t.lead_id
                 WHERE 1=1"""
        params = []
        if solo_pendientes: sql += " AND t.completada=0"
        if lid: sql += " AND t.lead_id=?"; params.append(int(lid))
        sql += " ORDER BY t.completada ASC, t.fecha_vencimiento ASC, t.prioridad DESC"
        return jsonify([dict(r) for r in c.execute(sql, params).fetchall()])


@app.route('/api/crm/tareas', methods=['POST'])
def api_crm_tarea_create():
    d = request.json
    titulo = d.get('titulo') or d.get('descripcion','Sin título')
    with db() as c:
        cur = c.execute(
            "INSERT INTO crm_tareas (lead_id,titulo,descripcion,tipo,prioridad,fecha_vencimiento) VALUES (?,?,?,?,?,?)",
            (d.get('lead_id'), titulo, d.get('descripcion', titulo),
             d.get('tipo','seguimiento'), d.get('prioridad','media'),
             d.get('vence_en') or d.get('fecha_vencimiento',''))
        )
        return jsonify(dict(c.execute("SELECT * FROM crm_tareas WHERE id=?", (cur.lastrowid,)).fetchone())), 201


@app.route('/api/crm/tareas/<int:tid>', methods=['PUT'])
def api_crm_tarea_update(tid):
    d = request.json
    with db() as c:
        if 'completada' in d:
            c.execute("UPDATE crm_tareas SET completada=? WHERE id=?", (int(d['completada']), tid))
        for col in ['titulo','descripcion','tipo','prioridad','fecha_vencimiento']:
            if col in d:
                c.execute(f"UPDATE crm_tareas SET {col}=? WHERE id=?", (d[col], tid))
        return jsonify(dict(c.execute("SELECT * FROM crm_tareas WHERE id=?", (tid,)).fetchone()))


@app.route('/api/crm/tareas/<int:tid>/completar', methods=['POST'])
def api_crm_tarea_completar(tid):
    with db() as c:
        row = c.execute("SELECT completada FROM crm_tareas WHERE id=?", (tid,)).fetchone()
        if not row: return jsonify({'error': 'No encontrada'}), 404
        nuevo = 0 if row['completada'] else 1
        c.execute("UPDATE crm_tareas SET completada=? WHERE id=?", (nuevo, tid))
        return jsonify({'completada': nuevo})


@app.route('/api/crm/tareas/<int:tid>', methods=['DELETE'])
def api_crm_tarea_delete(tid):
    with db() as c:
        c.execute("DELETE FROM crm_tareas WHERE id=?", (tid,))
        return jsonify({'ok': True})


# ── CRM: KPIs ────────────────────────────────────────────────────────────────

@app.route('/api/crm/kpis')
def api_crm_kpis():
    modulo = request.args.get('modulo', '')
    mes_actual = str(date.today())[:7]  # YYYY-MM
    with db() as c:
        # Filtro parametrizado (nunca interpolar input del request en SQL)
        mod_filter = " AND modulo=?" if modulo else ''
        mod_params = [modulo] if modulo else []
        total  = c.execute(f"SELECT COUNT(*) FROM crm_leads WHERE 1=1{mod_filter}", mod_params).fetchone()[0]
        conv   = c.execute(f"SELECT COUNT(*) FROM crm_leads WHERE convertido=1{mod_filter}", mod_params).fetchone()[0]
        hot    = c.execute(f"SELECT COUNT(*) FROM crm_leads WHERE temperatura='HOT' AND convertido=0{mod_filter}", mod_params).fetchone()[0]
        warm   = c.execute(f"SELECT COUNT(*) FROM crm_leads WHERE temperatura='WARM' AND convertido=0{mod_filter}", mod_params).fetchone()[0]
        cold   = c.execute(f"SELECT COUNT(*) FROM crm_leads WHERE temperatura='COLD' AND convertido=0{mod_filter}", mod_params).fetchone()[0]
        leads_mes = c.execute(
            f"SELECT COUNT(*) FROM crm_leads WHERE strftime('%Y-%m', fecha_creacion)=?{mod_filter}",
            [mes_actual] + mod_params
        ).fetchone()[0]
        tareas_vencidas = c.execute(
            "SELECT COUNT(*) FROM crm_tareas WHERE completada=0 AND fecha_vencimiento!='' AND fecha_vencimiento < ?",
            (str(date.today()),)).fetchone()[0]
        tareas_hoy = c.execute(
            "SELECT COUNT(*) FROM crm_tareas WHERE completada=0 AND fecha_vencimiento=?",
            (str(date.today()),)).fetchone()[0]
        tasa_conv = round(conv / total * 100, 1) if total else 0

        b2c = c.execute("SELECT COUNT(*) FROM crm_leads WHERE modulo='B2C'").fetchone()[0]
        b2b = c.execute("SELECT COUNT(*) FROM crm_leads WHERE modulo='B2B'").fetchone()[0]

        # Pipeline por módulo
        pipeline_b2c = {}
        for etapa in PIPELINES['B2C']:
            pipeline_b2c[etapa] = c.execute(
                "SELECT COUNT(*) FROM crm_leads WHERE modulo='B2C' AND etapa=?", (etapa,)).fetchone()[0]
        pipeline_b2b = {}
        for etapa in PIPELINES['B2B']:
            pipeline_b2b[etapa] = c.execute(
                "SELECT COUNT(*) FROM crm_leads WHERE modulo='B2B' AND etapa=?", (etapa,)).fetchone()[0]

        return jsonify({
            'total': total, 'total_leads': total,
            'b2c': b2c, 'b2b': b2b,
            'convertidos': conv, 'tasa_conversion': tasa_conv,
            'hot': hot, 'warm': warm, 'cold': cold,
            'leads_mes': leads_mes,
            'tareas_vencidas': tareas_vencidas, 'tareas_hoy': tareas_hoy,
            'pipeline_b2c': pipeline_b2c,
            'pipeline_b2b': pipeline_b2b,
        })


# ── CRM: Buscador de leads ────────────────────────────────────────────────────


# -- CRM: Segmentos guardados -------------------------------------------------

# -- CRM: Seguimiento automatico ---------------------------------------------
@app.route('/api/crm/auto-followup', methods=['POST'])
@login_required
def api_crm_auto_followup():
    REGLAS = [
        {'etapa': 'CONTACTADO',  'dias': 3, 'titulo': 'Seguimiento pendiente'},
        {'etapa': 'RESPONDIO',   'dias': 1, 'titulo': 'Responder cliente'},
        {'etapa': 'INTERESADO',  'dias': 2, 'titulo': 'Enviar propuesta'},
        {'etapa': 'PROPUESTA',   'dias': 4, 'titulo': 'Hacer seguimiento propuesta'},
    ]
    creadas = 0
    with db() as c:
        for regla in REGLAS:
            leads = c.execute(
                '''SELECT id,nombre FROM crm_leads
                   WHERE etapa=? AND convertido=0
                   AND (fecha_ultimo_contacto='' OR date(fecha_ultimo_contacto) <= date('now',?))
                   AND id NOT IN (SELECT lead_id FROM crm_tareas WHERE completada=0 AND titulo=?)''',
                (regla['etapa'], f"-{regla['dias']} days", regla['titulo'])
            ).fetchall()
            for lead in leads:
                c.execute(
                    "INSERT INTO crm_tareas (lead_id,titulo,tipo,prioridad,fecha_vencimiento) VALUES (?,?,'seguimiento','alta',date('now','+1 day'))",
                    (lead['id'], regla['titulo'])
                )
                creadas += 1
    return jsonify({'ok': True, 'tareas_creadas': creadas})

@app.route('/api/crm/segmentos', methods=['GET'])
@login_required
def api_crm_segmentos_list():
    with db() as c:
        rows = c.execute("SELECT * FROM crm_segmentos ORDER BY nombre").fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/crm/segmentos', methods=['POST'])
@login_required
def api_crm_segmentos_create():
    d = request.json
    with db() as c:
        cur = c.execute(
            "INSERT INTO crm_segmentos (nombre,modulo,filtros_json) VALUES (?,?,?)",
            (d['nombre'], d.get('modulo','B2B'), json.dumps(d.get('filtros',{})))
        )
        return jsonify({'id': cur.lastrowid, 'nombre': d['nombre']}), 201

@app.route('/api/crm/segmentos/<int:sid>', methods=['DELETE'])
@login_required
def api_crm_segmentos_delete(sid):
    with db() as c:
        c.execute("DELETE FROM crm_segmentos WHERE id=?", (sid,))
        return jsonify({'ok': True})

@app.route('/api/crm/segmentos/<int:sid>/leads', methods=['GET'])
@login_required
def api_crm_segmentos_leads(sid):
    with db() as c:
        seg = c.execute("SELECT * FROM crm_segmentos WHERE id=?", (sid,)).fetchone()
        if not seg: return jsonify({'error': 'No encontrado'}), 404
        filtros = json.loads(seg['filtros_json'])
        where, params = ["modulo=?"], [seg['modulo']]
        for col in ['etapa','temperatura','zona','canal_origen','asignado_a']:
            if filtros.get(col):
                where.append(f"{col}=?"); params.append(filtros[col])
        if filtros.get('sin_contacto_dias'):
            dias = int(filtros['sin_contacto_dias'])
            where.append("(fecha_ultimo_contacto='' OR date(fecha_ultimo_contacto) <= date('now',?))")
            params.append(f'-{dias} days')
        if filtros.get('convertido') is not None:
            where.append("convertido=?"); params.append(int(filtros['convertido']))
        sql = "SELECT * FROM crm_leads WHERE " + " AND ".join(where) + " ORDER BY fecha_creacion DESC"
        rows = c.execute(sql, params).fetchall()
        return jsonify([_enrich_lead(r) for r in rows])

@app.route('/api/crm/buscar', methods=['POST'])
def api_crm_buscar():
    d                = request.json
    descripcion      = d.get('descripcion', '')
    modulo           = d.get('modulo', 'B2B')
    zona             = d.get('zona', 'Santiago')
    canales          = d.get('canales', ['google', 'ia'])
    direccion_local  = d.get('direccion_local', '')

    resultados = []

    if 'google' in canales and modulo == 'B2B':
        resultados += _buscar_google_places(descripcion, zona, direccion_local)

    if 'ia' in canales:
        resultados += _buscar_leads_ia(descripcion, modulo, zona)

    if not resultados:
        resultados += _buscar_leads_keywords(descripcion, modulo, zona)

    resultados.sort(key=lambda x: x.get('score', 0), reverse=True)
    return jsonify(resultados[:15])


# ── CRM: Templates ────────────────────────────────────────────────────────────

@app.route('/api/crm/templates')
def api_crm_templates():
    modulo = request.args.get('modulo', '')
    if modulo:
        return jsonify(TEMPLATES_MENSAJES.get(modulo, []))
    return jsonify(TEMPLATES_MENSAJES)


@app.route('/api/crm/config')
def api_crm_config():
    return jsonify({
        'pipelines':     PIPELINES,
        'tags_b2c':      TAGS_B2C,
        'tags_b2b':      TAGS_B2B,
        'smtp_ok':       bool(SMTP_USER and SMTP_PASS),
        'google_ok':     bool(GOOGLE_PLACES_API_KEY),
        'ia_ok':         bool(ANTHROPIC_API_KEY),
    })


# ── CRM: Email Templates ─────────────────────────────────────────────────────

@app.route('/api/crm/email-templates', methods=['GET'])
def api_crm_email_templates_list():
    with db() as c:
        rows = c.execute("SELECT * FROM crm_email_templates WHERE activo=1 ORDER BY id").fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/crm/email-templates', methods=['POST'])
def api_crm_email_templates_create():
    d = request.json
    with db() as c:
        cur = c.execute(
            "INSERT INTO crm_email_templates (nombre,asunto,cuerpo,modulo) VALUES (?,?,?,?)",
            (d['nombre'], d['asunto'], d['cuerpo'], d.get('modulo','ambos'))
        )
        return jsonify(dict(c.execute("SELECT * FROM crm_email_templates WHERE id=?", (cur.lastrowid,)).fetchone())), 201

@app.route('/api/crm/email-templates/<int:tid>', methods=['PUT'])
def api_crm_email_templates_update(tid):
    d = request.json
    with db() as c:
        for col in ['nombre','asunto','cuerpo','modulo','activo']:
            if col in d:
                c.execute(f"UPDATE crm_email_templates SET {col}=? WHERE id=?", (d[col], tid))
        return jsonify(dict(c.execute("SELECT * FROM crm_email_templates WHERE id=?", (tid,)).fetchone()))

@app.route('/api/crm/email-templates/<int:tid>', methods=['DELETE'])
def api_crm_email_templates_delete(tid):
    with db() as c:
        c.execute("UPDATE crm_email_templates SET activo=0 WHERE id=?", (tid,))
        return jsonify({'ok': True})


# ── CRM: Email Masivo ─────────────────────────────────────────────────────────

@app.route('/api/crm/email-masivo', methods=['POST'])
def api_crm_email_masivo():
    d        = request.json
    lead_ids = d.get('lead_ids', [])
    asunto   = d.get('asunto', '')
    cuerpo   = d.get('cuerpo', '')

    if not lead_ids or not asunto or not cuerpo:
        return jsonify({'error': 'Faltan lead_ids, asunto o cuerpo'}), 400

    enviados, sin_email, errores = [], [], []

    with db() as c:
        for lid in lead_ids:
            lead = c.execute("SELECT * FROM crm_leads WHERE id=?", (lid,)).fetchone()
            if not lead:
                continue
            if not lead['email']:
                sin_email.append({'id': lid, 'nombre': lead['nombre']})
                continue

            # Personalizar variables
            cuerpo_p = cuerpo.replace('{nombre}', lead['nombre'] or '') \
                             .replace('{empresa}', lead['empresa'] or '') \
                             .replace('{telefono}', lead['telefono'] or '')
            asunto_p = asunto.replace('{nombre}', lead['nombre'] or '') \
                             .replace('{empresa}', lead['empresa'] or '')

            ok, err = _send_crm_email(lead['email'], asunto_p, cuerpo_p)
            if ok:
                c.execute(
                    "INSERT INTO crm_interacciones (lead_id,tipo,direccion,asunto,contenido,resultado,fecha) VALUES (?,?,?,?,?,?,?)",
                    (lid, 'email', 'saliente', asunto_p, cuerpo_p, 'enviado',
                     datetime.now().strftime('%Y-%m-%d %H:%M'))
                )
                c.execute("UPDATE crm_leads SET fecha_ultimo_contacto=? WHERE id=?",
                          (str(date.today()), lid))
                enviados.append({'id': lid, 'nombre': lead['nombre'], 'email': lead['email']})
            else:
                errores.append({'id': lid, 'nombre': lead['nombre'], 'email': lead['email'], 'error': err})

    return jsonify({
        'enviados':  len(enviados),
        'sin_email': len(sin_email),
        'errores':   len(errores),
        'detalle_enviados':  enviados,
        'detalle_sin_email': sin_email,
        'detalle_errores':   errores,
    })


# ── WhatsApp masivo ───────────────────────────────────────────────────────────

@app.route('/crm/whatsapp-masivo')
@module_required('crm')
def page_crm_whatsapp_masivo():
    return render_template('crm_whatsapp_masivo.html', active='crm')

@app.route('/api/crm/whatsapp-templates', methods=['GET'])
def api_crm_wa_templates_list():
    with db() as c:
        rows = c.execute("SELECT * FROM crm_whatsapp_templates WHERE activo=1 ORDER BY id").fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/crm/whatsapp-templates', methods=['POST'])
def api_crm_wa_templates_create():
    d = request.json
    with db() as c:
        cur = c.execute(
            "INSERT INTO crm_whatsapp_templates (nombre,cuerpo,modulo) VALUES (?,?,?)",
            (d['nombre'], d['cuerpo'], d.get('modulo', 'ambos'))
        )
        return jsonify(dict(c.execute("SELECT * FROM crm_whatsapp_templates WHERE id=?", (cur.lastrowid,)).fetchone())), 201

@app.route('/api/crm/whatsapp-templates/<int:tid>', methods=['PUT'])
def api_crm_wa_templates_update(tid):
    d = request.json
    with db() as c:
        for col in ('nombre', 'cuerpo', 'modulo'):
            if col in d:
                c.execute(f"UPDATE crm_whatsapp_templates SET {col}=? WHERE id=?", (d[col], tid))
        return jsonify(dict(c.execute("SELECT * FROM crm_whatsapp_templates WHERE id=?", (tid,)).fetchone()))

@app.route('/api/crm/whatsapp-templates/<int:tid>', methods=['DELETE'])
def api_crm_wa_templates_delete(tid):
    with db() as c:
        c.execute("UPDATE crm_whatsapp_templates SET activo=0 WHERE id=?", (tid,))
    return jsonify({'ok': True})

@app.route('/api/crm/whatsapp-masivo', methods=['POST'])
def api_crm_whatsapp_masivo():
    d        = request.json
    lead_ids = d.get('lead_ids', [])
    cuerpo   = d.get('cuerpo', '')

    if not lead_ids or not cuerpo:
        return jsonify({'error': 'Faltan lead_ids o cuerpo'}), 400

    agente_ok   = _wa_agent_connected()
    enviados    = []
    errores     = []
    sin_tel     = []
    links       = []   # fallback si agente no disponible

    with db() as c:
        for lid in lead_ids:
            lead = c.execute("SELECT * FROM crm_leads WHERE id=?", (lid,)).fetchone()
            if not lead:
                continue

            telefono_raw = lead['telefono'] or ''
            num_digits   = ''.join(ch for ch in telefono_raw if ch.isdigit())
            if not num_digits:
                sin_tel.append({'id': lid, 'nombre': lead['nombre']})
                continue

            num_wa = num_digits if num_digits.startswith('56') else '56' + num_digits

            # Personalizar mensaje
            msg = cuerpo.replace('{nombre}',   lead['nombre']   or '') \
                        .replace('{empresa}',  lead['empresa']  or '') \
                        .replace('{telefono}', telefono_raw)

            wa_url = f"https://wa.me/{num_wa}?text={urllib.parse.quote(msg)}"

            if agente_ok:
                ok, err = _send_whatsapp_agent(telefono_raw, msg)
                resultado_str = 'enviado' if ok else 'error'
                c.execute(
                    "INSERT INTO crm_interacciones (lead_id,tipo,direccion,asunto,contenido,resultado,fecha) VALUES (?,?,?,?,?,?,?)",
                    (lid, 'whatsapp', 'saliente', 'WhatsApp masivo', msg, resultado_str,
                     datetime.now().strftime('%Y-%m-%d %H:%M'))
                )
                c.execute("UPDATE crm_leads SET fecha_ultimo_contacto=? WHERE id=?",
                          (str(date.today()), lid))
                item = {'id': lid, 'nombre': lead['nombre'], 'empresa': lead['empresa'] or '',
                        'telefono': telefono_raw, 'mensaje': msg, 'url': wa_url}
                if ok:
                    enviados.append(item)
                else:
                    errores.append({**item, 'error': err})
            else:
                # Sin agente: modo link (el usuario abre manualmente)
                c.execute(
                    "INSERT INTO crm_interacciones (lead_id,tipo,direccion,asunto,contenido,resultado,fecha) VALUES (?,?,?,?,?,?,?)",
                    (lid, 'whatsapp', 'saliente', 'WhatsApp masivo (link)', msg, 'preparado',
                     datetime.now().strftime('%Y-%m-%d %H:%M'))
                )
                c.execute("UPDATE crm_leads SET fecha_ultimo_contacto=? WHERE id=?",
                          (str(date.today()), lid))
                links.append({'id': lid, 'nombre': lead['nombre'], 'empresa': lead['empresa'] or '',
                              'telefono': telefono_raw, 'mensaje': msg, 'url': wa_url})

    return jsonify({
        'agente_conectado': agente_ok,
        'enviados':   len(enviados),
        'errores':    len(errores),
        'sin_tel':    len(sin_tel),
        'links':      len(links),          # modo fallback
        'detalle_enviados': enviados,
        'detalle_errores':  errores,
        'detalle_sin_tel':  sin_tel,
        'detalle_links':    links,
    })


# ═══════════════════════════════════════════════════════════════════════════
# ── CRM: AUTOMATIZACIÓN (jobs + scheduler + endpoints) ──────────────────────
# ═══════════════════════════════════════════════════════════════════════════
# Política de seguridad: NUNCA envía mensajes sin aprobación del owner.
# Los jobs preparan borradores y crean tareas de alta prioridad. El owner
# revisa y envía con un click desde /crm/lead/<id> o /crm/whatsapp-masivo.

# Comunas RM priorizadas para prospección B2B (Aurora Bakers reparte en Santiago).
COMUNAS_RM_B2B = [
    'Providencia', 'Las Condes', 'Vitacura', 'Lo Barnechea', 'Ñuñoa',
    'La Reina', 'Santiago Centro', 'Macul', 'Peñalolén', 'Huechuraba',
    'La Florida', 'Maipú', 'San Miguel',
]

# Tipos de negocio rotados semanalmente (distinto de TIPOS_NEGOCIO_B2B, que es
# la lista plana de tipos válidos definida arriba)
TIPOS_NEGOCIO_B2B_ROTACION = [
    ('restaurantes de especialidad',          'Restaurante'),
    ('cafeterías de café de especialidad',    'Cafetería'),
    ('hoteles boutique',                       'Hotel'),
    ('almacenes gourmet y delicatessen',      'Almacén / Minimarket'),
    ('catering corporativo',                   'Catering'),
    ('casinos corporativos y de empresas',    'Casino'),
    ('panaderías y pastelerías premium',      'Panadería'),
]


def _log_automatizacion(job: str, accion: str, lead_id=None, detalle: str = '', ok: bool = True):
    """Registra una acción de automatización en crm_automatizacion_log."""
    try:
        with db() as c:
            c.execute(
                "INSERT INTO crm_automatizacion_log (job,accion,lead_id,detalle,ok) VALUES (?,?,?,?,?)",
                (job, accion, lead_id, str(detalle)[:500], 1 if ok else 0)
            )
    except Exception as e:
        print(f"[crm_auto] Error logging: {e}")


def _asignar_responsable_auto() -> str:
    """Devuelve el email del responsable al que asignar leads automáticos.
    Por ahora: admin (único usuario). Si hay más usuarios con rol comercial,
    usa round-robin balanceado por carga de leads activos."""
    try:
        with db() as c:
            candidatos = c.execute(
                "SELECT email FROM usuarios WHERE activo=1 AND rol IN ('admin','comercial') ORDER BY id"
            ).fetchall()
            if not candidatos:
                return 'admin@aurorabakers.cl'
            if len(candidatos) == 1:
                return candidatos[0]['email']
            # Round-robin por carga
            mejor = None
            mejor_carga = 10**9
            for u in candidatos:
                carga = c.execute(
                    "SELECT COUNT(*) FROM crm_leads WHERE asignado_a=? AND convertido=0",
                    (u['email'],)
                ).fetchone()[0]
                if carga < mejor_carga:
                    mejor_carga = carga
                    mejor = u['email']
            return mejor or candidatos[0]['email']
    except Exception:
        return 'admin@aurorabakers.cl'


def _normalizar_telefono(tel: str) -> str:
    """Deja solo dígitos para comparación de duplicados."""
    return ''.join(ch for ch in (tel or '') if ch.isdigit())


def _lead_existe(c, nombre: str, telefono: str, email: str, empresa: str = '') -> bool:
    """Verifica si ya existe un lead con mismo teléfono, email o nombre+empresa."""
    tel_norm = _normalizar_telefono(telefono)
    if tel_norm:
        row = c.execute(
            "SELECT id FROM crm_leads WHERE REPLACE(REPLACE(REPLACE(telefono,'+',''),' ',''),'-','') LIKE ? LIMIT 1",
            (f'%{tel_norm[-8:]}%',)
        ).fetchone()
        if row:
            return True
    if email:
        row = c.execute("SELECT id FROM crm_leads WHERE email=? LIMIT 1", (email,)).fetchone()
        if row:
            return True
    if nombre and empresa:
        row = c.execute(
            "SELECT id FROM crm_leads WHERE LOWER(nombre)=? AND LOWER(empresa)=? LIMIT 1",
            (nombre.lower(), empresa.lower())
        ).fetchone()
        if row:
            return True
    return False


def _comuna_y_tipo_de_la_semana():
    """Rota comuna y tipo de negocio según semana del año (determinístico)."""
    iso_week = date.today().isocalendar()[1]
    comuna = COMUNAS_RM_B2B[iso_week % len(COMUNAS_RM_B2B)]
    tipo_desc, tipo_label = TIPOS_NEGOCIO_B2B_ROTACION[iso_week % len(TIPOS_NEGOCIO_B2B_ROTACION)]
    return comuna, tipo_desc, tipo_label


# ─── JOB 1: Búsqueda semanal de leads B2B ─────────────────────────────────────
def _job_buscar_leads_b2b():
    """Busca nuevos leads B2B en una comuna/tipo rotativo. Idempotente."""
    job = 'buscar_leads_b2b'
    try:
        comuna, descripcion, tipo_label = _comuna_y_tipo_de_la_semana()
        _log_automatizacion(job, 'inicio', detalle=f"Buscando {descripcion} en {comuna}")

        resultados = []
        # 1) Google Places (real, prioritario)
        try:
            resultados += _buscar_google_places(descripcion, comuna, '')
        except Exception as e:
            _log_automatizacion(job, 'google_error', detalle=str(e), ok=False)
        # 2) IA Claude (complementa, sobre todo si Google trae poco)
        try:
            resultados += _buscar_leads_ia(descripcion, 'B2B', comuna)
        except Exception as e:
            _log_automatizacion(job, 'ia_error', detalle=str(e), ok=False)
        # 3) Fallback keywords (siempre que las 2 anteriores fallen)
        if not resultados:
            resultados += _buscar_leads_keywords(descripcion, 'B2B', comuna)

        # Ordenar por score, tomar top
        resultados.sort(key=lambda x: x.get('score', 0), reverse=True)
        resultados = resultados[:15]

        creados, duplicados = 0, 0
        responsable = _asignar_responsable_auto()
        hoy = str(date.today())

        with db() as c:
            for r in resultados:
                nombre   = (r.get('nombre') or '').strip()
                empresa  = (r.get('empresa') or nombre).strip()
                if not nombre:
                    continue
                tel      = (r.get('telefono') or '').strip()
                email_v  = (r.get('email') or '').strip()
                if _lead_existe(c, nombre, tel, email_v, empresa):
                    duplicados += 1
                    continue
                # Tags según tipo de negocio
                tipo_neg = r.get('tipo_negocio') or tipo_label
                tags = [tipo_neg] if tipo_neg in TAGS_B2B else []
                tags.append('auto_discovery')
                # Insert
                cur = c.execute("""
                    INSERT INTO crm_leads
                        (modulo, nombre, email, telefono, empresa, cargo, zona, direccion,
                         canal_origen, canal_origen_detalle, etapa, temperatura, tags_json,
                         notas, valor_potencial, asignado_a, fecha_creacion,
                         auto_discovery, auto_contact_estado, auto_score)
                    VALUES ('B2B',?,?,?,?,?,?,?,?,?,'PROSPECTO','COLD',?,?,0,?,?,1,'pendiente_borrador',?)
                """, (
                    nombre, email_v, tel, empresa, r.get('cargo', 'Encargado de compras'),
                    r.get('zona', comuna), r.get('direccion', ''),
                    r.get('canal_origen', 'auto_discovery'),
                    f"Job {job} · semana {date.today().isocalendar()[1]}",
                    json.dumps(tags, ensure_ascii=False),
                    f"Razón: {r.get('razon','')}",
                    responsable, hoy,
                    int(r.get('score', 0) or 0),
                ))
                creados += 1
                _log_automatizacion(job, 'lead_creado', lead_id=cur.lastrowid,
                                    detalle=f"{empresa} ({tipo_neg}) — score {r.get('score',0)}")

        _log_automatizacion(
            job, 'fin',
            detalle=f"Comuna={comuna} tipo={tipo_label} creados={creados} dup={duplicados}"
        )
        return {'ok': True, 'comuna': comuna, 'tipo': tipo_label,
                'creados': creados, 'duplicados': duplicados, 'evaluados': len(resultados)}
    except Exception as e:
        _log_automatizacion(job, 'error_fatal', detalle=str(e), ok=False)
        print(f"[job_buscar_leads_b2b] {e}")
        return {'ok': False, 'error': str(e)}


# ─── JOB 2: Primer contacto automático (borrador + tarea) ─────────────────────
def _job_primer_contacto():
    """Para cada lead PROSPECTO con auto_discovery=1 y sin borrador preparado:
    arma el mensaje email/WhatsApp con la plantilla B2B activa, crea una tarea
    de alta prioridad para que el owner apruebe y envíe."""
    job = 'primer_contacto'
    creadas = 0
    try:
        _log_automatizacion(job, 'inicio')
        with db() as c:
            leads = c.execute("""
                SELECT * FROM crm_leads
                WHERE auto_discovery=1
                  AND auto_contact_estado='pendiente_borrador'
                  AND etapa='PROSPECTO'
                  AND convertido=0
                ORDER BY auto_score DESC, fecha_creacion ASC
                LIMIT 50
            """).fetchall()

            tpl_email = c.execute(
                "SELECT * FROM crm_email_templates WHERE modulo IN ('B2B','ambos') AND activo=1 ORDER BY id LIMIT 1"
            ).fetchone()
            tpl_wa = c.execute(
                "SELECT * FROM crm_whatsapp_templates WHERE modulo IN ('B2B','ambos') AND activo=1 ORDER BY id LIMIT 1"
            ).fetchone()

            for lead in leads:
                lid = lead['id']
                empresa = lead['empresa'] or lead['nombre']
                # Construir descripción de la tarea con los borradores listos
                partes = [f"📋 Aprobar primer contacto B2B con {empresa}", ""]
                if lead['email'] and tpl_email:
                    asunto = (tpl_email['asunto'] or '').replace('{nombre}', lead['nombre'] or '') \
                                                       .replace('{empresa}', empresa)
                    cuerpo = (tpl_email['cuerpo'] or '').replace('{nombre}', lead['nombre'] or '') \
                                                       .replace('{empresa}', empresa)
                    partes += [
                        "── EMAIL (borrador) ──",
                        f"Para: {lead['email']}",
                        f"Asunto: {asunto}",
                        "",
                        cuerpo,
                        "",
                    ]
                if lead['telefono'] and tpl_wa:
                    wa_msg = (tpl_wa['cuerpo'] or '').replace('{nombre}', lead['nombre'] or '') \
                                                    .replace('{empresa}', empresa)
                    partes += [
                        "── WHATSAPP (borrador) ──",
                        f"Para: {lead['telefono']}",
                        wa_msg,
                        "",
                    ]
                if not lead['email'] and not lead['telefono']:
                    partes += [
                        "⚠️ Lead sin email ni teléfono. Investigar contacto antes de avanzar.",
                    ]
                descripcion = "\n".join(partes)

                # Crear tarea de alta prioridad para hoy
                c.execute("""
                    INSERT INTO crm_tareas
                        (lead_id, titulo, descripcion, tipo, prioridad, fecha_vencimiento)
                    VALUES (?,?,?,?,?,?)
                """, (
                    lid,
                    f"📞 Primer contacto B2B — {empresa}",
                    descripcion,
                    'primer_contacto_auto',
                    'alta',
                    str(date.today())
                ))
                # Marcar lead como borrador_listo
                c.execute(
                    "UPDATE crm_leads SET auto_contact_estado='borrador_listo' WHERE id=?",
                    (lid,)
                )
                creadas += 1
                _log_automatizacion(job, 'borrador_creado', lead_id=lid, detalle=empresa)

        _log_automatizacion(job, 'fin', detalle=f"borradores_creados={creadas}")
        return {'ok': True, 'borradores_creados': creadas}
    except Exception as e:
        _log_automatizacion(job, 'error_fatal', detalle=str(e), ok=False)
        print(f"[job_primer_contacto] {e}")
        return {'ok': False, 'error': str(e)}


# ─── JOB 3: Motor de seguimiento por etapa y SLA ──────────────────────────────
def _job_seguimiento_pipeline():
    """Aplica reglas configuradas en crm_reglas_seguimiento: crea tareas,
    cambia temperatura, marca como perdido, mueve etapa cuando corresponde."""
    job = 'seguimiento_pipeline'
    try:
        _log_automatizacion(job, 'inicio')
        acciones = {
            'tareas_creadas': 0,
            'temperatura_cambiada': 0,
            'marcados_perdido': 0,
            'etapa_cambiada': 0,
        }
        with db() as c:
            reglas = c.execute(
                "SELECT * FROM crm_reglas_seguimiento WHERE activa=1"
            ).fetchall()

            for r in reglas:
                # Determinar fecha de referencia: usa fecha_ultimo_contacto si existe,
                # si no, fecha_creacion. Calculamos en SQL con COALESCE.
                leads = c.execute("""
                    SELECT id, nombre, empresa, etapa, temperatura, fecha_ultimo_contacto, fecha_creacion
                    FROM crm_leads
                    WHERE modulo=? AND etapa=? AND convertido=0
                      AND (
                          (fecha_ultimo_contacto!='' AND date(fecha_ultimo_contacto) <= date('now',?))
                          OR
                          (fecha_ultimo_contacto='' AND date(fecha_creacion) <= date('now',?))
                      )
                """, (
                    r['modulo'], r['etapa'],
                    f"-{r['dias_sin_actividad']} days",
                    f"-{r['dias_sin_actividad']} days",
                )).fetchall()

                for lead in leads:
                    lid = lead['id']
                    accion = r['accion']

                    if accion == 'crear_tarea':
                        # Evitar duplicar tarea abierta con el mismo título
                        existe = c.execute(
                            "SELECT id FROM crm_tareas WHERE lead_id=? AND completada=0 AND titulo=? LIMIT 1",
                            (lid, r['titulo_tarea'])
                        ).fetchone()
                        if existe:
                            continue
                        c.execute("""
                            INSERT INTO crm_tareas
                                (lead_id, titulo, descripcion, tipo, prioridad, fecha_vencimiento)
                            VALUES (?,?,?,?,?,date('now','+1 day'))
                        """, (
                            lid, r['titulo_tarea'],
                            f"Auto-generada por regla #{r['id']} — etapa {r['etapa']} +{r['dias_sin_actividad']}d",
                            'seguimiento_auto', r['prioridad'],
                        ))
                        acciones['tareas_creadas'] += 1
                        _log_automatizacion(job, 'tarea_creada', lead_id=lid,
                                            detalle=f"regla={r['id']} → {r['titulo_tarea']}")

                    if r['cambio_temperatura'] and lead['temperatura'] != r['cambio_temperatura']:
                        c.execute("UPDATE crm_leads SET temperatura=? WHERE id=?",
                                  (r['cambio_temperatura'], lid))
                        acciones['temperatura_cambiada'] += 1
                        _log_automatizacion(job, 'temperatura', lead_id=lid,
                                            detalle=f"{lead['temperatura']} → {r['cambio_temperatura']}")

                    if accion == 'enfriar':
                        # Ya manejado por cambio_temperatura arriba; sólo log
                        pass

                    if accion == 'marcar_perdido':
                        c.execute(
                            "UPDATE crm_leads SET etapa='PERDIDO', razon_perdida=? WHERE id=?",
                            (f"Auto: sin actividad +{r['dias_sin_actividad']}d en {r['etapa']}", lid)
                        )
                        c.execute(
                            "INSERT INTO crm_etapa_log (lead_id,etapa_from,etapa_to,usuario) VALUES (?,?,?,?)",
                            (lid, lead['etapa'], 'PERDIDO', 'auto_seguimiento')
                        )
                        acciones['marcados_perdido'] += 1
                        _log_automatizacion(job, 'marcado_perdido', lead_id=lid,
                                            detalle=f"desde etapa {lead['etapa']}")

                    if r['cambio_etapa'] and r['cambio_etapa'] != lead['etapa']:
                        c.execute(
                            "INSERT INTO crm_etapa_log (lead_id,etapa_from,etapa_to,usuario) VALUES (?,?,?,?)",
                            (lid, lead['etapa'], r['cambio_etapa'], 'auto_seguimiento')
                        )
                        c.execute("UPDATE crm_leads SET etapa=? WHERE id=?",
                                  (r['cambio_etapa'], lid))
                        acciones['etapa_cambiada'] += 1
                        _log_automatizacion(job, 'etapa_cambiada', lead_id=lid,
                                            detalle=f"{lead['etapa']} → {r['cambio_etapa']}")

        _log_automatizacion(job, 'fin', detalle=json.dumps(acciones, ensure_ascii=False))
        return {'ok': True, **acciones}
    except Exception as e:
        _log_automatizacion(job, 'error_fatal', detalle=str(e), ok=False)
        print(f"[job_seguimiento_pipeline] {e}")
        return {'ok': False, 'error': str(e)}


# Registry de jobs disponibles para ejecución manual via endpoint
CRM_JOBS = {
    'buscar_leads_b2b':    _job_buscar_leads_b2b,
    'primer_contacto':     _job_primer_contacto,
    'seguimiento_pipeline': _job_seguimiento_pipeline,
}


# ─── ENDPOINTS Automatización CRM ─────────────────────────────────────────────

@app.route('/api/crm/automatizacion/estado')
@login_required
def api_crm_auto_estado():
    """Estado general: último run de cada job + próximas ejecuciones."""
    with db() as c:
        ultimos = {}
        for j in CRM_JOBS.keys():
            row = c.execute(
                "SELECT fecha,accion,detalle,ok FROM crm_automatizacion_log "
                "WHERE job=? AND accion IN ('fin','error_fatal') ORDER BY id DESC LIMIT 1",
                (j,)
            ).fetchone()
            ultimos[j] = dict(row) if row else None
        # Borradores listos por revisar
        pendientes = c.execute(
            "SELECT COUNT(*) FROM crm_leads WHERE auto_contact_estado='borrador_listo'"
        ).fetchone()[0]
        # Próximas (calcula desde scheduler si está activo)
        proximas = _scheduler_proximas_ejecuciones()
        return jsonify({
            'jobs':                ultimos,
            'borradores_listos':   pendientes,
            'proximas_ejecuciones': proximas,
            'scheduler_activo':    bool(_SCHEDULER_INSTANCE),
        })


@app.route('/api/crm/automatizacion/log')
@login_required
def api_crm_auto_log():
    job = request.args.get('job', '')
    limit = min(int(request.args.get('limit', 100)), 500)
    with db() as c:
        sql = ("SELECT l.*, le.nombre AS lead_nombre, le.empresa "
               "FROM crm_automatizacion_log l "
               "LEFT JOIN crm_leads le ON le.id=l.lead_id WHERE 1=1")
        params = []
        if job:
            sql += " AND l.job=?"
            params.append(job)
        sql += " ORDER BY l.id DESC LIMIT ?"
        params.append(limit)
        rows = c.execute(sql, params).fetchall()
        return jsonify([dict(r) for r in rows])


@app.route('/api/crm/automatizacion/ejecutar/<job_name>', methods=['POST'])
@login_required
def api_crm_auto_ejecutar(job_name):
    """Dispara manualmente un job (testing o on-demand)."""
    if job_name not in CRM_JOBS:
        return jsonify({'error': f'Job desconocido. Disponibles: {list(CRM_JOBS.keys())}'}), 400
    try:
        resultado = CRM_JOBS[job_name]()
        return jsonify({'ok': True, 'job': job_name, 'resultado': resultado})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/crm/automatizacion/reglas', methods=['GET'])
@login_required
def api_crm_auto_reglas_list():
    with db() as c:
        rows = c.execute(
            "SELECT * FROM crm_reglas_seguimiento ORDER BY modulo, etapa, dias_sin_actividad"
        ).fetchall()
        return jsonify([dict(r) for r in rows])


@app.route('/api/crm/automatizacion/reglas', methods=['POST'])
@login_required
def api_crm_auto_reglas_create():
    d = request.json
    with db() as c:
        cur = c.execute("""
            INSERT INTO crm_reglas_seguimiento
                (modulo,etapa,dias_sin_actividad,accion,titulo_tarea,prioridad,
                 cambio_temperatura,cambio_etapa,activa)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            d.get('modulo', 'B2B'),
            d['etapa'],
            int(d.get('dias_sin_actividad', 3)),
            d.get('accion', 'crear_tarea'),
            d.get('titulo_tarea', ''),
            d.get('prioridad', 'media'),
            d.get('cambio_temperatura', ''),
            d.get('cambio_etapa', ''),
            int(d.get('activa', 1)),
        ))
        return jsonify({'id': cur.lastrowid, 'ok': True}), 201


@app.route('/api/crm/automatizacion/reglas/<int:rid>', methods=['PUT'])
@login_required
def api_crm_auto_reglas_update(rid):
    d = request.json
    campos_validos = ['modulo','etapa','dias_sin_actividad','accion','titulo_tarea',
                      'prioridad','cambio_temperatura','cambio_etapa','activa']
    sets, params = [], []
    for c_name in campos_validos:
        if c_name in d:
            sets.append(f"{c_name}=?")
            params.append(d[c_name])
    if not sets:
        return jsonify({'ok': True, 'sin_cambios': True})
    params.append(rid)
    with db() as c:
        c.execute(f"UPDATE crm_reglas_seguimiento SET {','.join(sets)} WHERE id=?", params)
        row = c.execute("SELECT * FROM crm_reglas_seguimiento WHERE id=?", (rid,)).fetchone()
        return jsonify(dict(row) if row else {'ok': True})


@app.route('/api/crm/automatizacion/reglas/<int:rid>', methods=['DELETE'])
@login_required
def api_crm_auto_reglas_delete(rid):
    with db() as c:
        c.execute("DELETE FROM crm_reglas_seguimiento WHERE id=?", (rid,))
        return jsonify({'ok': True})


@app.route('/api/crm/automatizacion/borradores')
@login_required
def api_crm_auto_borradores():
    """Lista leads con borrador de primer contacto listo para aprobar."""
    with db() as c:
        rows = c.execute("""
            SELECT l.id, l.nombre, l.empresa, l.telefono, l.email, l.etapa,
                   l.fecha_creacion, l.auto_score,
                   (SELECT t.titulo FROM crm_tareas t
                    WHERE t.lead_id=l.id AND t.tipo='primer_contacto_auto'
                      AND t.completada=0 ORDER BY t.id DESC LIMIT 1) AS tarea_titulo
            FROM crm_leads l
            WHERE l.auto_contact_estado='borrador_listo'
            ORDER BY l.auto_score DESC, l.fecha_creacion ASC
        """).fetchall()
        return jsonify([dict(r) for r in rows])


# ─── SCHEDULER (APScheduler) ──────────────────────────────────────────────────
_SCHEDULER_INSTANCE = None
_SCHEDULER_LOCK_FH = None  # file handle del lock (lo mantenemos abierto)


def _try_acquire_scheduler_lock() -> bool:
    """Solo un worker debe correr el scheduler. Usa flock en Linux (Railway).
    En Windows / sin fcntl, retorna True (single-worker dev)."""
    if os.environ.get('SCHEDULER_DISABLED', '').strip() in ('1', 'true', 'yes'):
        return False
    global _SCHEDULER_LOCK_FH
    try:
        import fcntl
        lock_path = os.path.join(_DATA_DIR_EARLY, '.scheduler.lock')
        fh = open(lock_path, 'w')
        try:
            fcntl.lockf(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _SCHEDULER_LOCK_FH = fh  # mantener abierto
            fh.write(str(os.getpid()))
            fh.flush()
            return True
        except (BlockingIOError, OSError):
            fh.close()
            return False
    except ImportError:
        # Windows: no fcntl, asumir single-worker
        return True


def _scheduler_proximas_ejecuciones():
    """Devuelve dict {job_id: next_run_iso}."""
    if not _SCHEDULER_INSTANCE:
        return {}
    out = {}
    for j in _SCHEDULER_INSTANCE.get_jobs():
        out[j.id] = j.next_run_time.isoformat() if j.next_run_time else None
    return out


def _enviar_reporte_mayoristas_semanal():
    """Envía resumen semanal de mayoristas por WhatsApp al dueño."""
    owner = os.environ.get('OWNER_PHONE', '')
    if not owner:
        return
    with db() as c:
        nuevos  = c.execute("SELECT COUNT(*) FROM crm_leads WHERE modulo='B2B' AND strftime('%W-%Y', fecha_creacion)=strftime('%W-%Y','now')").fetchone()[0]
        activos = c.execute("SELECT COUNT(*) FROM mayoristas WHERE activo=1").fetchone()[0]
        deuda   = c.execute("SELECT COALESCE(SUM(monto),0) FROM mayoristas_cobranza WHERE estado IN ('PENDIENTE','VENCIDO')").fetchone()[0]
        pedidos_semana = c.execute(
            "SELECT COUNT(*) FROM mayoristas_pedidos WHERE strftime('%W-%Y', fecha_entrega)=strftime('%W-%Y','now')"
        ).fetchone()[0]
    msg = (
        f"Mayoristas — Resumen semanal\n\n"
        f"Clientes activos: {activos}\n"
        f"Pedidos esta semana: {pedidos_semana}\n"
        f"Leads nuevos: {nuevos}\n"
        f"Por cobrar: ${float(deuda):,.0f}\n\n"
        f"Buen lunes"
    )
    _send_whatsapp_agent(owner, msg)


def _job_mayoristas_semanal():
    """Job semanal: vencimientos, evaluar pipeline B2B, prospección, primer contacto, reporte."""
    with app.app_context():
        print("[mayoristas_job] Iniciando job semanal")
        _verificar_vencimientos_cobranza()
        # Evaluar pipeline interno
        hoy = date.today()
        with db() as c:
            for etapa, dias, titulo, nueva_etapa in _REGLAS_PIPELINE_MAY:
                cutoff = str(hoy - timedelta(days=dias))
                leads = c.execute(
                    """SELECT * FROM crm_leads WHERE modulo='B2B' AND etapa=? AND convertido=0
                       AND (fecha_ultimo_contacto='' OR fecha_ultimo_contacto <= ?)""",
                    (etapa, cutoff)
                ).fetchall()
                for lead in leads:
                    existe = c.execute(
                        "SELECT id FROM crm_tareas WHERE lead_id=? AND titulo=? AND completada=0",
                        (lead['id'], titulo)
                    ).fetchone()
                    if not existe:
                        venc = str(hoy + timedelta(days=2))
                        c.execute(
                            "INSERT INTO crm_tareas (lead_id, titulo, tipo, prioridad, fecha_vencimiento) VALUES (?,?,?,?,?)",
                            (lead['id'], titulo, 'seguimiento', 'alta', venc)
                        )
                    if nueva_etapa:
                        c.execute("UPDATE crm_leads SET etapa=? WHERE id=?", (nueva_etapa, lead['id']))
        # Prospección si hay API key
        if GOOGLE_PLACES_API_KEY:
            with db() as c:
                for tipo in ['restaurante', 'café', 'hotel']:
                    for zona in COMUNAS_DESPACHO[:3]:
                        if True:
                            results = _buscar_google_places(tipo, zona)
                            for r in results[:3]:
                                score = _calcular_score_fit(r.get('tipo_negocio',''), r.get('zona',''), '')
                                if score < 3:
                                    continue
                                tel = r.get('telefono','')
                                nom = r.get('nombre','')
                                dup = c.execute("SELECT id FROM crm_leads WHERE telefono=? OR nombre=?", (tel, nom)).fetchone()
                                if dup:
                                    continue
                                c.execute(
                                    """INSERT INTO crm_leads (modulo, nombre, empresa, telefono, zona, direccion, canal_origen, etapa, temperatura, tipo_negocio, score_fit)
                                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                                    ('B2B', nom, nom, tel, zona, r.get('direccion',''),
                                     'google_places', 'DESCUBIERTO', 'COLD', r.get('tipo_negocio',''), score)
                                )
        # Primer contacto: prepara borradores para aprobación del owner.
        # Política CRM: los jobs nunca envían mensajes sin aprobación; el envío
        # manual queda en /api/mayoristas/crm/primer-contacto.
        cutoff_c = str(hoy - timedelta(days=3))
        with db() as c:
            leads_desc = c.execute(
                "SELECT * FROM crm_leads WHERE modulo='B2B' AND etapa='DESCUBIERTO' AND (fecha_ultimo_contacto='' OR fecha_ultimo_contacto < ?) LIMIT 5",
                (cutoff_c,)
            ).fetchall()
            for lead in leads_desc:
                tel = lead['telefono']
                if not tel:
                    continue
                existe = c.execute(
                    "SELECT id FROM crm_tareas WHERE lead_id=? AND tipo='primer_contacto_auto' AND completada=0",
                    (lead['id'],)
                ).fetchone()
                if existe:
                    continue
                msj = (
                    f"Hola {lead['nombre']}, soy Nicolas de Aurora Bakers.\n"
                    f"Hacemos pan de masa madre artesanal y repartimos en {lead['zona'] or 'Santiago'}.\n"
                    f"Les gustaria probar una muestra gratis esta semana?"
                )
                c.execute(
                    "INSERT INTO crm_tareas (lead_id, titulo, descripcion, tipo, prioridad, fecha_vencimiento) VALUES (?,?,?,?,?,?)",
                    (lead['id'], f"Aprobar primer contacto — {lead['nombre']}",
                     f"Borrador WhatsApp para {tel}:\n\n{msj}",
                     'primer_contacto_auto', 'alta', str(hoy))
                )
        _enviar_reporte_mayoristas_semanal()
        print("[mayoristas_job] Job semanal completado")


def init_automatizacion_crm():
    """Arranca APScheduler con los 3 jobs CRM. Idempotente — solo arranca 1 vez."""
    global _SCHEDULER_INSTANCE
    if _SCHEDULER_INSTANCE is not None:
        return  # ya iniciado
    if not _try_acquire_scheduler_lock():
        print("[crm_auto] Scheduler omitido (otro worker tiene el lock o deshabilitado)")
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        sched = BackgroundScheduler(timezone='America/Santiago',
                                    daemon=True,
                                    job_defaults={'misfire_grace_time': 3600})
        # Job 1: búsqueda semanal — lunes 09:00
        sched.add_job(_job_buscar_leads_b2b,
                      CronTrigger(day_of_week='mon', hour=9, minute=0),
                      id='buscar_leads_b2b', replace_existing=True)
        # Job 2: primer contacto — diario 10:00
        sched.add_job(_job_primer_contacto,
                      CronTrigger(hour=10, minute=0),
                      id='primer_contacto', replace_existing=True)
        # Job 3: seguimiento pipeline — diario 08:00
        sched.add_job(_job_seguimiento_pipeline,
                      CronTrigger(hour=8, minute=0),
                      id='seguimiento_pipeline', replace_existing=True)
        # Job 4: mayoristas — lunes 07:00
        sched.add_job(_job_mayoristas_semanal,
                      CronTrigger(day_of_week='mon', hour=7, minute=0),
                      id='mayoristas_semanal', replace_existing=True)
        # Job 5: verificar vencimientos cobranza — diario 08:30
        sched.add_job(_verificar_vencimientos_cobranza,
                      CronTrigger(hour=8, minute=30),
                      id='vencimientos_cobranza', replace_existing=True)
        sched.start()
        _SCHEDULER_INSTANCE = sched
        print(f"[crm_auto] APScheduler iniciado (pid={os.getpid()}) — 5 jobs registrados")
    except Exception as e:
        print(f"[crm_auto] No se pudo iniciar APScheduler: {e}")


# ── Configuración CRM ────────────────────────────────────────────────────────

@app.route('/crm/configuracion')
@module_required('crm')
def page_crm_configuracion():
    return render_template('crm_configuracion.html', active='crm')

@app.route('/api/crm/config/status', methods=['GET'])
def api_crm_config_status():
    """Devuelve estado actual de WhatsApp agent y SMTP."""
    s   = _smtp_cfg()
    cfg = _wa_cfg()
    wa_connected = _wa_agent_connected()
    qr = None
    if not wa_connected and cfg['apikey']:
        try:
            r  = _wa_request('GET', f"/instance/connect/{cfg['instance']}")
            qr = r.get('base64') or r.get('qrcode') or r.get('code')
        except Exception:
            pass
    return jsonify({
        'smtp': {
            'configurado': bool(s['user'] and s['pass']),
            'host': s['host'],
            'port': s['port'],
            'user': s['user'],
            'from': s['from'],
        },
        'whatsapp': {
            'configurado': bool(cfg['apikey']),
            'conectado':   wa_connected,
            'url':         cfg['url'],
            'instance':    cfg['instance'],
            'qr':          qr,
        },
    })

@app.route('/api/crm/config/smtp', methods=['POST'])
def api_crm_config_smtp():
    d = request.json
    _save_config({
        'smtp_host': d.get('host', 'smtp.gmail.com'),
        'smtp_port': int(d.get('port', 587)),
        'smtp_user': d.get('user', ''),
        'smtp_pass': d.get('pass', ''),
        'smtp_from': d.get('from', ''),
    })
    # Test si se pide
    if d.get('test_to'):
        ok, err = _send_crm_email(d['test_to'], 'Prueba SMTP — Aurora Bakers',
                                  'Este es un correo de prueba enviado desde el CRM de Aurora Bakers.')
        return jsonify({'ok': ok, 'error': err})
    return jsonify({'ok': True})

@app.route('/api/crm/config/whatsapp', methods=['POST'])
def api_crm_config_whatsapp():
    d = request.json
    _save_config({
        'wa_url':      d.get('url',      'http://localhost:8080'),
        'wa_apikey':   d.get('apikey',   ''),
        'wa_instance': d.get('instance', 'aurora'),
    })
    return jsonify({'ok': True})

@app.route('/api/crm/config/wa-qr', methods=['GET'])
def api_crm_config_wa_qr():
    """Solicita nuevo QR al agente WhatsApp."""
    cfg = _wa_cfg()
    if not cfg['apikey']:
        return jsonify({'error': 'Evolution API no configurada'}), 400
    try:
        r  = _wa_request('GET', f"/instance/connect/{cfg['instance']}")
        qr = r.get('base64') or r.get('qrcode') or r.get('code')
        connected = _wa_agent_connected()
        return jsonify({'qr': qr, 'conectado': connected})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/crm/config/wa-status', methods=['GET'])
def api_crm_config_wa_status():
    connected = _wa_agent_connected()
    return jsonify({'conectado': connected})

@app.route('/api/crm/wa-check-numbers', methods=['POST'])
def api_crm_wa_check_numbers():
    """Verifica qué números tienen WhatsApp usando Evolution API."""
    lead_ids = request.json.get('lead_ids', [])
    if not lead_ids:
        return jsonify({'error': 'Sin lead_ids'}), 400

    cfg = _wa_cfg()
    if not cfg['apikey']:
        return jsonify({'error': 'Evolution API no configurada'}), 400
    if not _wa_agent_connected():
        return jsonify({'error': 'Agente WhatsApp desconectado'}), 503

    # Recopilar números
    leads_info = {}
    with db() as c:
        for lid in lead_ids:
            row = c.execute("SELECT id,nombre,telefono FROM crm_leads WHERE id=?", (lid,)).fetchone()
            if not row or not row['telefono']:
                continue
            num = ''.join(ch for ch in row['telefono'] if ch.isdigit())
            if not num:
                continue
            if not num.startswith('56'):
                num = '56' + num
            leads_info[str(lid)] = {'nombre': row['nombre'], 'telefono': row['telefono'], 'num': num}

    if not leads_info:
        return jsonify({'resultados': []})

    numeros = [v['num'] for v in leads_info.values()]
    try:
        resp = _wa_request('POST', f"/chat/whatsappNumbers/{cfg['instance']}", {'numbers': numeros})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Mapear resultado
    tiene_wa = {r.get('number','').replace('@s.whatsapp.net','').replace('@c.us',''): r.get('exists', False)
                for r in (resp if isinstance(resp, list) else resp.get('numbers', []))}

    resultados = []
    for lid_str, info in leads_info.items():
        num_clean = info['num'].lstrip('+')
        ok = tiene_wa.get(num_clean, tiene_wa.get(info['num'], False))
        resultados.append({
            'lead_id':  int(lid_str),
            'nombre':   info['nombre'],
            'telefono': info['telefono'],
            'tiene_whatsapp': ok,
        })

    return jsonify({'resultados': resultados})


# ════════════════════════════════════════════════════════════════════════════
# INVENTARIO
# ════════════════════════════════════════════════════════════════════════════

@app.route('/inventario')
@module_required('inventario')
def page_inventario():
    return render_template('inventario.html', active='inventario')

@app.route('/api/inventario', methods=['GET'])
@login_required
def api_inventario_list():
    bodega = request.args.get('bodega')
    with db() as c:
        if bodega:
            rows = c.execute("SELECT * FROM inventario WHERE bodega=? ORDER BY ingrediente", (bodega,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM inventario ORDER BY bodega, ingrediente").fetchall()
    return jsonify([dict(r) for r in rows])

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

@app.route('/api/inventario/lista-compras')
@login_required
def api_inventario_lista_compras():
    """Ingredientes bajo punto de reorden + necesidades del plan próximos 7 días."""
    con_plan = request.args.get('con_plan', '1') == '1'
    with db() as c:
        bajo_reorden = c.execute("""
            SELECT i.id, i.ingrediente, i.stock_kg, i.alerta_minimo_kg, i.unidad,
                   i.proveedor, i.precio_kg,
                   (i.alerta_minimo_kg - i.stock_kg) as faltante
            FROM inventario i
            WHERE i.bodega IN ('ingredientes', 'produccion')
              AND i.stock_kg <= i.alerta_minimo_kg
              AND i.alerta_minimo_kg > 0
            ORDER BY faltante DESC
        """).fetchall()
        necesidades = []
        if con_plan:
            desde = date.today().isoformat()
            hasta = (date.today() + timedelta(days=7)).isoformat()
            plan_filas = c.execute("""
                SELECT ingredientes_json FROM plan_produccion
                WHERE fecha_amasado BETWEEN ? AND ? AND estado='pendiente'
                  AND ingredientes_json IS NOT NULL AND ingredientes_json != '[]'
            """, (desde, hasta)).fetchall()
            agg = {}
            for fila in plan_filas:
                try:
                    for ing in json.loads(fila['ingredientes_json'] or '[]'):
                        iid = ing.get('inventario_id')
                        if not iid:
                            continue
                        if iid not in agg:
                            inv = c.execute(
                                "SELECT ingrediente, stock_kg, unidad FROM inventario WHERE id=?", (iid,)
                            ).fetchone()
                            if inv:
                                agg[iid] = {'inventario_id': iid, 'nombre': inv['ingrediente'],
                                            'stock_actual': float(inv['stock_kg']), 'kg_necesario': 0.0,
                                            'unidad': inv['unidad']}
                        if iid in agg:
                            agg[iid]['kg_necesario'] += float(ing.get('kg', 0))
                except Exception:
                    pass
            necesidades = [
                {**v, 'faltante': round(max(0, v['kg_necesario'] - v['stock_actual']), 3)}
                for v in agg.values() if v['kg_necesario'] > v['stock_actual']
            ]
    return jsonify({'bajo_reorden': [dict(r) for r in bajo_reorden], 'necesidades_plan': necesidades})


@app.route('/api/compras-insumos', methods=['GET'])
@login_required
def api_compras_insumos_list():
    desde = request.args.get('desde', (date.today().replace(day=1)).isoformat())
    hasta = request.args.get('hasta', date.today().isoformat())
    with db() as c:
        rows = c.execute("""
            SELECT ci.*, CASE WHEN ci.cantidad_kg > 0 THEN ROUND(ci.precio_total/ci.cantidad_kg,2) ELSE 0 END as precio_kg
            FROM compras_insumos ci
            WHERE ci.fecha BETWEEN ? AND ?
            ORDER BY ci.fecha DESC, ci.creado_en DESC
        """, (desde, hasta)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/compras-insumos', methods=['POST'])
@login_required
def api_compras_insumos_create():
    d = request.get_json(silent=True) or {}
    cantidad_kg  = float(d.get('cantidad_kg', 0))
    precio_total = float(d.get('precio_total', 0))
    inventario_id = d.get('inventario_id') or None
    with db() as c:
        if inventario_id:
            # Obtener nombre del ingrediente
            inv = c.execute("SELECT ingrediente FROM inventario WHERE id=?", (inventario_id,)).fetchone()
            ingrediente = inv['ingrediente'] if inv else d.get('ingrediente', '')
        else:
            ingrediente = d.get('ingrediente', '')
        cur = c.execute("""
            INSERT INTO compras_insumos (fecha, inventario_id, ingrediente, cantidad_kg, precio_total, proveedor, factura, notas)
            VALUES (?,?,?,?,?,?,?,?)
        """, (d.get('fecha', date.today().isoformat()), inventario_id, ingrediente,
              cantidad_kg, precio_total, d.get('proveedor',''), d.get('factura',''), d.get('notas','')))
        rid = cur.lastrowid
        # Sumar al stock del ingrediente
        if inventario_id and cantidad_kg > 0:
            c.execute(
                "UPDATE inventario SET stock_kg=stock_kg+?, ultima_actualizacion=date('now') WHERE id=?",
                (cantidad_kg, inventario_id)
            )
        # Actualizar precio_kg promedio en inventario
        if inventario_id and cantidad_kg > 0 and precio_total > 0:
            c.execute(
                "UPDATE inventario SET precio_kg=? WHERE id=?",
                (round(precio_total / cantidad_kg, 2), inventario_id)
            )
    return jsonify({'ok': True, 'id': rid})


@app.route('/api/compras-insumos/<int:cid>', methods=['DELETE'])
@login_required
def api_compras_insumos_delete(cid):
    with db() as c:
        row = c.execute("SELECT inventario_id, cantidad_kg FROM compras_insumos WHERE id=?", (cid,)).fetchone()
        if row and row['inventario_id'] and row['cantidad_kg']:
            c.execute(
                "UPDATE inventario SET stock_kg=MAX(0, stock_kg-?), ultima_actualizacion=date('now') WHERE id=?",
                (row['cantidad_kg'], row['inventario_id'])
            )
        c.execute("DELETE FROM compras_insumos WHERE id=?", (cid,))
    return jsonify({'ok': True})


@app.route('/api/inventario', methods=['POST'])
@login_required
def api_inventario_create():
    d = request.get_json(silent=True) or {}
    bodega      = d.get('bodega', 'ingredientes')
    producto_id = d.get('producto_id')
    stock       = float(d.get('stock_kg', 0))
    with db() as c:
        if bodega == 'productos_terminados' and producto_id:
            prod = c.execute("SELECT nombre, categoria, subcategoria FROM productos WHERE id=?", (int(producto_id),)).fetchone()
            nombre   = prod['nombre']      if prod else d.get('ingrediente', '')
            categoria   = prod['categoria']   if prod else d.get('categoria', '')
            subcategoria = prod['subcategoria'] if prod else d.get('subcategoria', '')
            sucursal = _sucursal_escritura(d)
            inv_id = _get_or_create_inv_terminado(c, int(producto_id), nombre, sucursal)
            c.execute(
                """UPDATE inventario SET stock_kg=?, alerta_minimo_kg=?, unidad=?,
                   categoria=?, subcategoria=?, ultima_actualizacion=date('now') WHERE id=?""",
                (stock, float(d.get('alerta_minimo_kg', 0)), d.get('unidad', 'unidades'),
                 categoria, subcategoria, inv_id)
            )
            # productos.stock = total entre sucursales
            c.execute("""UPDATE productos SET stock=(
                           SELECT COALESCE(SUM(stock_kg),0) FROM inventario
                           WHERE bodega='productos_terminados' AND producto_id=?)
                         WHERE id=?""", (int(producto_id), int(producto_id)))
        else:
            # Upsert que conserva el id: INSERT OR REPLACE borraba la fila y
            # creaba otra, dejando recetas.inventario_id en NULL (FK SET NULL).
            # Insumos centralizados: siempre sucursal 1.
            c.execute(
                """INSERT INTO inventario
                   (ingrediente, stock_kg, alerta_minimo_kg, proveedor, precio_kg,
                    ultima_actualizacion, bodega, unidad, categoria, subcategoria, sucursal_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,1)
                   ON CONFLICT(ingrediente, bodega, sucursal_id) DO UPDATE SET
                     stock_kg=excluded.stock_kg,
                     alerta_minimo_kg=excluded.alerta_minimo_kg,
                     proveedor=excluded.proveedor,
                     precio_kg=excluded.precio_kg,
                     ultima_actualizacion=excluded.ultima_actualizacion,
                     bodega=excluded.bodega,
                     unidad=excluded.unidad,
                     categoria=excluded.categoria,
                     subcategoria=excluded.subcategoria""",
                (d.get('ingrediente', ''), stock, float(d.get('alerta_minimo_kg', 1)),
                 d.get('proveedor', ''), float(d.get('precio_kg', 0)), date.today().isoformat(),
                 bodega, d.get('unidad', 'kg'),
                 d.get('categoria', ''), d.get('subcategoria', ''))
            )
    return jsonify({'ok': True})

@app.route('/api/inventario/<int:iid>', methods=['PUT'])
@login_required
def api_inventario_update(iid):
    d = request.get_json(silent=True) or {}
    stock = float(d.get('stock_kg', 0))
    with db() as c:
        c.execute(
            """UPDATE inventario SET
               ingrediente=?, stock_kg=?, alerta_minimo_kg=?, proveedor=?, precio_kg=?,
               ultima_actualizacion=?, bodega=?, unidad=?, categoria=?, subcategoria=?
               WHERE id=?""",
            (d.get('ingrediente', ''), stock, float(d.get('alerta_minimo_kg', 1)),
             d.get('proveedor', ''), float(d.get('precio_kg', 0)), date.today().isoformat(),
             d.get('bodega', 'ingredientes'), d.get('unidad', 'kg'),
             d.get('categoria', ''), d.get('subcategoria', ''), iid)
        )
        row = c.execute("SELECT bodega, producto_id FROM inventario WHERE id=?", (iid,)).fetchone()
        if row and row['bodega'] == 'productos_terminados' and row['producto_id']:
            # productos.stock = total entre sucursales
            c.execute("""UPDATE productos SET stock=(
                           SELECT COALESCE(SUM(stock_kg),0) FROM inventario
                           WHERE bodega='productos_terminados' AND producto_id=?)
                         WHERE id=?""", (row['producto_id'], row['producto_id']))
    return jsonify({'ok': True})

@app.route('/api/inventario/<int:iid>', methods=['DELETE'])
@login_required
def api_inventario_delete(iid):
    with db() as c:
        c.execute("DELETE FROM inventario WHERE id=?", (iid,))
    return jsonify({'ok': True})

@app.route('/api/inventario/<int:iid>/ajustar', methods=['POST'])
@login_required
def api_inventario_ajustar(iid):
    d = request.get_json(silent=True) or {}
    delta = float(d.get('delta', 0))
    with db() as c:
        prev = c.execute("SELECT stock_kg FROM inventario WHERE id=?", (iid,)).fetchone()
        if not prev:
            return jsonify({'error': 'No encontrado'}), 404
        # Clamp en 0 y sincronización con el delta efectivo (lo que realmente cambió)
        nueva = max(0.0, prev['stock_kg'] + delta)
        delta_efectivo = nueva - prev['stock_kg']
        c.execute(
            "UPDATE inventario SET stock_kg=?, ultima_actualizacion=? WHERE id=?",
            (nueva, date.today().isoformat(), iid)
        )
        row = c.execute("SELECT * FROM inventario WHERE id=?", (iid,)).fetchone()
        if row and row['bodega'] == 'productos_terminados' and row['producto_id']:
            c.execute(
                "UPDATE productos SET stock=stock+? WHERE id=?",
                (delta_efectivo, row['producto_id'])
            )
    return jsonify(dict(row) if row else {'ok': False})


# ════════════════════════════════════════════════════════════════════════════
# TRASPASOS ENTRE SUCURSALES
# ════════════════════════════════════════════════════════════════════════════

@app.route('/traspasos')
@module_required('traspasos')
def page_traspasos():
    return render_template('traspasos.html', active='traspasos')

@app.route('/api/traspasos', methods=['GET'])
@login_required
def api_traspasos_list():
    with db() as c:
        rows = c.execute("""
            SELECT t.*, so.nombre AS origen_nombre, sd.nombre AS destino_nombre
            FROM traspasos t
            JOIN sucursales so ON so.id = t.origen_id
            JOIN sucursales sd ON sd.id = t.destino_id
            ORDER BY t.id DESC LIMIT 100
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d['items'] = [dict(i) for i in c.execute(
                """SELECT ti.*, p.nombre AS producto_nombre
                   FROM traspaso_items ti JOIN productos p ON p.id = ti.producto_id
                   WHERE ti.traspaso_id=?""", (r['id'],)).fetchall()]
            result.append(d)
    return jsonify(result)

@app.route('/api/traspasos', methods=['POST'])
@login_required
def api_traspasos_create():
    d = request.json or {}
    try:
        origen  = int(d.get('origen_id') or 1)
        destino = int(d.get('destino_id') or 0)
    except (TypeError, ValueError):
        return jsonify({'error': 'Sucursales inválidas'}), 400
    items = d.get('items', [])
    if not destino or destino == origen:
        return jsonify({'error': 'Elige una sucursal destino distinta del origen'}), 400
    if not items:
        return jsonify({'error': 'Agrega al menos un producto'}), 400
    fija = _sucursal_fija()
    if fija and origen != int(fija):
        return jsonify({'error': 'Solo puedes traspasar desde tu sucursal'}), 403
    with db() as c:
        if not c.execute("SELECT id FROM sucursales WHERE id=? AND activa=1", (destino,)).fetchone():
            return jsonify({'error': 'Sucursal destino no existe'}), 404
        # Validar TODO el stock antes de mover nada (sin traspasos parciales)
        for it in items:
            pid, cant = int(it['producto_id']), float(it['cantidad'])
            if cant <= 0:
                return jsonify({'error': 'Las cantidades deben ser mayores a 0'}), 400
            row = c.execute(
                """SELECT stock_kg FROM inventario
                   WHERE bodega='productos_terminados' AND producto_id=? AND sucursal_id=?""",
                (pid, origen)).fetchone()
            disponible = row['stock_kg'] if row else 0
            if disponible < cant:
                p = c.execute("SELECT nombre FROM productos WHERE id=?", (pid,)).fetchone()
                nombre = p['nombre'] if p else f'producto {pid}'
                return jsonify({'error': f'Stock insuficiente de {nombre}: hay {disponible:g}, pides {cant:g}'}), 400
        cur = c.execute(
            "INSERT INTO traspasos (origen_id, destino_id, usuario, notas) VALUES (?,?,?,?)",
            (origen, destino, session.get('user_nombre', ''), d.get('notas', '')))
        tid = cur.lastrowid
        for it in items:
            pid, cant = int(it['producto_id']), float(it['cantidad'])
            p = c.execute("SELECT nombre FROM productos WHERE id=?", (pid,)).fetchone()
            c.execute("INSERT INTO traspaso_items (traspaso_id, producto_id, cantidad) VALUES (?,?,?)",
                      (tid, pid, cant))
            # Descontar del origen
            c.execute("""UPDATE inventario SET stock_kg=stock_kg-?, ultima_actualizacion=date('now')
                         WHERE bodega='productos_terminados' AND producto_id=? AND sucursal_id=?""",
                      (cant, pid, origen))
            movimientos = _descontar_lotes_fifo(c, pid, cant, None,
                                                sucursal_id=origen, tipo='traspaso')
            # Sumar al destino con lotes espejo (misma fecha de elaboración)
            inv_dest = _get_or_create_inv_terminado(c, pid, p['nombre'], destino)
            c.execute("""UPDATE inventario SET stock_kg=stock_kg+?, ultima_actualizacion=date('now')
                         WHERE id=?""", (cant, inv_dest))
            movido = 0.0
            for lote_id, cantidad_mov in movimientos:
                fecha_elab = c.execute(
                    "SELECT fecha_elaboracion FROM producto_lotes WHERE id=?", (lote_id,)
                ).fetchone()['fecha_elaboracion']
                c.execute("""INSERT INTO producto_lotes
                             (producto_id, sucursal_id, fecha_elaboracion, cantidad_inicial, cantidad_actual, notas)
                             VALUES (?,?,?,?,?,?)""",
                          (pid, destino, fecha_elab, cantidad_mov, cantidad_mov, f'Traspaso #{tid}'))
                movido += cantidad_mov
            resto = round(cant - movido, 3)
            if resto > 0:  # drift histórico: lotes de origen no cubrían el inventario
                c.execute("""INSERT INTO producto_lotes
                             (producto_id, sucursal_id, fecha_elaboracion, cantidad_inicial, cantidad_actual, notas)
                             VALUES (?,?,date('now'),?,?,?)""",
                          (pid, destino, resto, resto, f'Traspaso #{tid} (ajuste)'))
    return jsonify({'ok': True, 'id': tid}), 201


# ════════════════════════════════════════════════════════════════════════════
# RECETAS (ingredientes por producto en %)
# ════════════════════════════════════════════════════════════════════════════

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
                      i.precio_kg
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
        'producto_id':               prod['id'],
        'nombre':                    prod['nombre'],
        'peso_unitario_kg':          peso,
        'costo_teorico':             round(costo_teorico, 2),
        'ingredientes_sin_vincular': ingredientes_sin_vincular,
        'ingredientes':              ingredientes_data
    })


@app.route('/api/recetas/<int:producto_id>', methods=['POST'])
@login_required
def api_receta_save(producto_id):
    """Guarda/reemplaza la receta completa de un producto."""
    d = request.get_json(silent=True) or {}
    peso         = float(d.get('peso_unitario_kg', 0))
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


@app.route('/api/recetas/productos', methods=['GET'])
@login_required
def api_recetas_productos():
    """Lista de productos activos con su peso_unitario_kg para el selector de recetas."""
    with db() as c:
        prods = c.execute(
            "SELECT id, nombre, peso_unitario_kg FROM productos WHERE activo=1 ORDER BY nombre"
        ).fetchall()
        ings  = c.execute("SELECT DISTINCT ingrediente FROM inventario ORDER BY ingrediente").fetchall()
    return jsonify({
        'productos':    [dict(p) for p in prods],
        'ingredientes': [r['ingrediente'] for r in ings]
    })


# ════════════════════════════════════════════════════════════════════════════
# PLAN DE PRODUCCIÓN
# ════════════════════════════════════════════════════════════════════════════

@app.route('/produccion')
@module_required('produccion')
def page_produccion():
    return render_template('produccion.html', active='produccion')

@app.route('/api/plan-produccion', methods=['GET'])
@login_required
def api_plan_list():
    fecha = request.args.get('fecha', date.today().isoformat())
    vista = request.args.get('vista', '')

    with db() as c:
        if vista == 'timeline':
            hornear_rows = c.execute("""
                SELECT batch_id, nombre_producto, cantidad, estado,
                       producto_id, fecha_amasado, fecha_horneado, ingredientes_json
                FROM plan_produccion
                WHERE fecha_horneado = ?
                  AND estado IN ('amasado')
                  AND batch_id != ''
                ORDER BY batch_id, nombre_producto
            """, (fecha,)).fetchall()

            amasar_rows = c.execute("""
                SELECT batch_id, nombre_producto, cantidad, estado,
                       producto_id, fecha_amasado, fecha_horneado, ingredientes_json
                FROM plan_produccion
                WHERE fecha_amasado = ?
                  AND estado = 'pendiente'
                  AND batch_id != ''
                ORDER BY batch_id, nombre_producto
            """, (fecha,)).fetchall()

            horneados_rows = c.execute("""
                SELECT batch_id, nombre_producto, cantidad, estado,
                       producto_id, fecha_amasado, fecha_horneado, ingredientes_json
                FROM plan_produccion
                WHERE fecha_amasado = ?
                  AND estado IN ('horneado', 'listo')
                  AND batch_id != ''
                ORDER BY batch_id, nombre_producto
            """, (fecha,)).fetchall()

            def agrupar_por_batch(rows):
                batches = {}
                for r in rows:
                    r = dict(r)
                    bid = r['batch_id']
                    if bid not in batches:
                        prod = c.execute(
                            "SELECT masa_base FROM productos WHERE id=?", (r['producto_id'],)
                        ).fetchone()
                        masa_base = prod['masa_base'] if prod else ''
                        batch_prods = c.execute("""
                            SELECT pp.cantidad, p.peso_unitario_kg
                            FROM plan_produccion pp
                            JOIN productos p ON p.id = pp.producto_id
                            WHERE pp.batch_id = ?
                        """, (bid,)).fetchall()
                        masa_final = sum(bp['cantidad'] * bp['peso_unitario_kg'] for bp in batch_prods)
                        ref_prod = c.execute(
                            "SELECT baking_loss_pct, merma_tecnica_pct FROM productos WHERE id=?",
                            (r['producto_id'],)
                        ).fetchone()
                        if ref_prod:
                            bl = ref_prod['baking_loss_pct'] / 100
                            masa_cruda = masa_final / (1 - bl) if bl < 1 else masa_final
                            masa_amasar = masa_cruda * (1 + ref_prod['merma_tecnica_pct'] / 100)
                        else:
                            masa_amasar = masa_final
                        try:
                            ings = json.loads(r['ingredientes_json'] or '[]')
                        except Exception:
                            ings = []
                        batches[bid] = {
                            'batch_id':       bid,
                            'masa_base':      masa_base,
                            'estado':         r['estado'],
                            'fecha_amasado':  r['fecha_amasado'],
                            'fecha_horneado': r['fecha_horneado'],
                            'masa_amasar_kg': round(masa_amasar, 3),
                            'ingredientes':   ings,
                            'productos':      [],
                        }
                    batches[bid]['productos'].append({
                        'nombre':     r['nombre_producto'],
                        'cantidad':   r['cantidad'],
                        'producto_id': r['producto_id'],
                    })
                return list(batches.values())

            return jsonify({
                'fecha':       fecha,
                'hornear_hoy': agrupar_por_batch(hornear_rows),
                'amasar_hoy':  agrupar_por_batch(amasar_rows),
                'horneados':   agrupar_por_batch(horneados_rows),
            })

        # Backward compat: sin vista=timeline devuelve lista plana
        rows = c.execute(
            "SELECT * FROM plan_produccion WHERE fecha=? ORDER BY nombre_producto", (fecha,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/plan-produccion', methods=['POST'])
@login_required
def api_plan_create():
    d = request.get_json(silent=True) or {}
    with db() as c:
        cur = c.execute(
            "INSERT INTO plan_produccion (fecha, codigo_producto, nombre_producto, cantidad, estado, notas, producto_id) VALUES (?,?,?,?,?,?,?)",
            (d.get('fecha', date.today().isoformat()), d.get('codigo_producto',''),
             d.get('nombre_producto',''), int(d.get('cantidad',0)),
             d.get('estado','pendiente'), d.get('notas',''), d.get('producto_id') or None)
        )
        rid = cur.lastrowid
    return jsonify({'ok': True, 'id': rid})

@app.route('/api/plan-produccion/<int:pid>', methods=['PUT'])
@login_required
def api_plan_update(pid):
    d = request.get_json(silent=True) or {}
    with db() as c:
        c.execute(
            "UPDATE plan_produccion SET cantidad=?, estado=?, notas=? WHERE id=?",
            (int(d.get('cantidad',0)), d.get('estado','pendiente'), d.get('notas',''), pid)
        )
    return jsonify({'ok': True})

@app.route('/api/plan-produccion/<int:pid>', methods=['DELETE'])
@login_required
def api_plan_delete(pid):
    with db() as c:
        c.execute("DELETE FROM plan_produccion WHERE id=?", (pid,))
    return jsonify({'ok': True})

@app.route('/api/plan-produccion/<fecha>/confirmar', methods=['POST'])
def api_plan_confirmar(fecha):
    """
    Confirma la producción de una fecha:
    1. Calcula ingredientes usados según recetas
    2. Descuenta del inventario
    3. Marca todos los ítems del plan como 'listo'
    4. Retorna resumen de descuentos y alertas de stock bajo
    Accesible por UI (login_required) y por agentes (X-Agent-Key).
    """
    with db() as c:
        plan = c.execute(
            "SELECT * FROM plan_produccion WHERE fecha=? AND estado != 'listo'", (fecha,)
        ).fetchall()

        if not plan:
            ya_listos = c.execute(
                "SELECT COUNT(*) FROM plan_produccion WHERE fecha=? AND estado='listo'", (fecha,)
            ).fetchone()[0]
            if ya_listos:
                return jsonify({'ok': False, 'error': f'Producción del {fecha} ya fue confirmada anteriormente'})
            return jsonify({'ok': False, 'error': f'Sin plan de producción pendiente para {fecha}'})

        # Calcular descuentos de ingredientes y sumar stock de pan
        descuentos: dict[str, float] = {}
        stock_sumado = []

        for item in plan:
            nombre   = item['nombre_producto']
            cantidad = item['cantidad']
            pid_plan = item['producto_id']

            # Buscar producto por ID (si lo tiene) o por nombre
            if pid_plan:
                prod = c.execute(
                    "SELECT id, peso_unitario_kg FROM productos WHERE id=?", (pid_plan,)
                ).fetchone()
            else:
                prod = c.execute(
                    "SELECT id, peso_unitario_kg FROM productos WHERE nombre=?", (nombre,)
                ).fetchone()

            if prod:
                # Sumar stock en inventario, productos y crear lote (3 counters en sync)
                inv_id = _get_or_create_inv_terminado(c, prod['id'], nombre)
                c.execute(
                    "UPDATE inventario SET stock_kg=stock_kg+?, ultima_actualizacion=date('now') WHERE id=?",
                    (cantidad, inv_id)
                )
                c.execute(
                    "UPDATE productos SET stock=stock+? WHERE id=?",
                    (cantidad, prod['id'])
                )
                c.execute(
                    """INSERT INTO producto_lotes (producto_id, fecha_elaboracion, cantidad_inicial, cantidad_actual, notas)
                       VALUES (?,?,?,?,?)""",
                    (prod['id'], fecha, cantidad, cantidad, 'Producción confirmada')
                )
                stock_sumado.append({'nombre': nombre, 'cantidad': cantidad})

                # Descontar ingredientes solo si tiene peso y receta configurados
                if prod['peso_unitario_kg'] > 0:
                    total_kg = cantidad * prod['peso_unitario_kg']
                    ings = c.execute(
                        "SELECT ingrediente, porcentaje FROM recetas WHERE producto_id=?",
                        (prod['id'],)
                    ).fetchall()
                    for ing in ings:
                        kg = (ing['porcentaje'] / 100) * total_kg
                        descuentos[ing['ingrediente']] = descuentos.get(ing['ingrediente'], 0) + kg

        # Descontar inventario y recoger alertas
        alertas = []
        for ing, kg in descuentos.items():
            row = c.execute(
                "SELECT id, stock_kg, alerta_minimo_kg FROM inventario WHERE ingrediente=?", (ing,)
            ).fetchone()
            if row:
                nuevo_stock = max(0, row['stock_kg'] - kg)
                c.execute(
                    "UPDATE inventario SET stock_kg=?, ultima_actualizacion=date('now') WHERE id=?",
                    (round(nuevo_stock, 3), row['id'])
                )
                if nuevo_stock <= row['alerta_minimo_kg']:
                    alertas.append({
                        'ingrediente': ing,
                        'stock_kg':    round(nuevo_stock, 3),
                        'minimo_kg':   row['alerta_minimo_kg'],
                    })

        # Marcar plan como listo
        c.execute(
            "UPDATE plan_produccion SET estado='listo' WHERE fecha=? AND estado != 'listo'", (fecha,)
        )

    return jsonify({
        'ok':              True,
        'fecha':           fecha,
        'items_confirmados': len(plan),
        'stock_sumado':    stock_sumado,
        'descuentos':      {k: round(v, 3) for k, v in descuentos.items()},
        'alertas_stock':   alertas,
    })


@app.route('/api/plan-produccion/copiar', methods=['POST'])
@login_required
def api_plan_copiar():
    """Copia el plan de una fecha a otra."""
    d = request.get_json(silent=True) or {}
    desde = d.get('desde')
    hasta = d.get('hasta')
    if not desde or not hasta:
        return jsonify({'error': 'Faltan parámetros desde/hasta'}), 400
    with db() as c:
        rows = c.execute("SELECT * FROM plan_produccion WHERE fecha=?", (desde,)).fetchall()
        for r in rows:
            c.execute(
                "INSERT INTO plan_produccion (fecha, codigo_producto, nombre_producto, cantidad, estado, notas, producto_id) VALUES (?,?,?,?,?,?,?)",
                (hasta, r['codigo_producto'], r['nombre_producto'], r['cantidad'], 'pendiente', r['notas'], r['producto_id'])
            )
    return jsonify({'ok': True, 'copiados': len(rows)})


@app.route('/api/plan-produccion/<fecha>/desde-ventas', methods=['GET'])
@login_required
def api_plan_desde_ventas(fecha):
    """
    Retorna los productos que hay que producir para la fecha indicada,
    sumando las cantidades de venta_items según:
      - Ventas con despacho a domicilio donde fecha_despacho = fecha y no despachadas aún
      - Ventas de retiro en tienda donde fecha de venta = fecha y no despachadas aún
    """
    with db() as c:
        rows = c.execute("""
            SELECT
                p.id          AS producto_id,
                p.nombre      AS nombre_producto,
                SUM(vi.cantidad) AS cantidad_total,
                COUNT(DISTINCT v.id) AS num_ventas,
                GROUP_CONCAT(
                    CASE
                        WHEN c.nombre IS NOT NULL
                        THEN CAST(CAST(vi.cantidad AS INTEGER) AS TEXT) || '× ' || c.nombre
                        ELSE CAST(CAST(vi.cantidad AS INTEGER) AS TEXT) || '× (sin cliente)'
                    END, ', '
                ) AS detalle_clientes
            FROM venta_items vi
            JOIN ventas v  ON v.id  = vi.venta_id
            JOIN productos p ON p.id = vi.producto_id
            LEFT JOIN clientes c ON c.id = v.cliente_id
            WHERE v.estado_despacho != 'DESPACHADO'
              AND (
                  (v.con_despacho = 1 AND v.fecha_despacho = ?)
                  OR
                  (v.con_despacho = 0 AND v.fecha = ?)
              )
            GROUP BY p.id, p.nombre
            ORDER BY cantidad_total DESC
        """, (fecha, fecha)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/plan-produccion/<fecha>/generar-desde-ventas', methods=['POST'])
@login_required
def api_plan_generar_desde_ventas(fecha):
    """
    Genera (o completa) el plan de producción de la fecha a partir de las ventas pendientes.
    Parámetro JSON:
      modo: 'reemplazar'  — borra el plan actual y lo crea desde cero
            'agregar'     — solo añade productos que aún no están en el plan
    """
    d    = request.get_json(silent=True) or {}
    modo = d.get('modo', 'agregar')  # default: no destructivo

    with db() as c:
        # 1. Obtener productos desde ventas
        rows = c.execute("""
            SELECT
                p.id          AS producto_id,
                p.nombre      AS nombre_producto,
                SUM(vi.cantidad) AS cantidad_total,
                GROUP_CONCAT(
                    CASE
                        WHEN c.nombre IS NOT NULL
                        THEN CAST(CAST(vi.cantidad AS INTEGER) AS TEXT) || '× ' || c.nombre
                        ELSE CAST(CAST(vi.cantidad AS INTEGER) AS TEXT) || '× (sin cliente)'
                    END, ', '
                ) AS detalle_clientes
            FROM venta_items vi
            JOIN ventas v  ON v.id  = vi.venta_id
            JOIN productos p ON p.id = vi.producto_id
            LEFT JOIN clientes c ON c.id = v.cliente_id
            WHERE v.estado_despacho != 'DESPACHADO'
              AND (
                  (v.con_despacho = 1 AND v.fecha_despacho = ?)
                  OR
                  (v.con_despacho = 0 AND v.fecha = ?)
              )
            GROUP BY p.id, p.nombre
            ORDER BY cantidad_total DESC
        """, (fecha, fecha)).fetchall()

        if not rows:
            return jsonify({'ok': False, 'error': 'No hay ventas pendientes para esa fecha'}), 400

        if modo == 'reemplazar':
            c.execute("DELETE FROM plan_produccion WHERE fecha=?", (fecha,))
            for r in rows:
                codigo = 'P' + str(r['producto_id'])
                notas  = r['detalle_clientes'] or ''
                c.execute(
                    "INSERT INTO plan_produccion (fecha,codigo_producto,nombre_producto,cantidad,estado,notas,producto_id) VALUES (?,?,?,?,?,?,?)",
                    (fecha, codigo, r['nombre_producto'], int(r['cantidad_total']), 'pendiente', notas, r['producto_id'])
                )
            return jsonify({'ok': True, 'modo': 'reemplazar', 'items': len(rows)})

        else:  # agregar
            existing = {row['nombre_producto'].lower()
                        for row in c.execute("SELECT nombre_producto FROM plan_produccion WHERE fecha=?", (fecha,)).fetchall()}
            agregados = 0
            for r in rows:
                if r['nombre_producto'].lower() not in existing:
                    codigo = 'P' + str(r['producto_id'])
                    notas  = r['detalle_clientes'] or ''
                    c.execute(
                        "INSERT INTO plan_produccion (fecha,codigo_producto,nombre_producto,cantidad,estado,notas,producto_id) VALUES (?,?,?,?,?,?,?)",
                        (fecha, codigo, r['nombre_producto'], int(r['cantidad_total']), 'pendiente', notas, r['producto_id'])
                    )
                    agregados += 1
            return jsonify({'ok': True, 'modo': 'agregar', 'items': len(rows), 'agregados': agregados})


@app.route('/api/plan-produccion/semana')
@login_required
def api_plan_semana():
    """Resumen de producción para 7 días desde la fecha dada."""
    desde_str = request.args.get('desde', date.today().isoformat())
    try:
        desde_d = date.fromisoformat(desde_str)
    except ValueError:
        desde_d = date.today()
    dias = []
    with db() as c:
        for i in range(7):
            d = desde_d + timedelta(days=i)
            filas = c.execute(
                "SELECT nombre_producto, cantidad, estado, batch_id FROM plan_produccion WHERE fecha=? ORDER BY nombre_producto",
                (d.isoformat(),)
            ).fetchall()
            total = sum(f['cantidad'] for f in filas)
            pendiente = sum(f['cantidad'] for f in filas if f['estado'] == 'pendiente')
            horneado  = sum(f['cantidad'] for f in filas if f['estado'] in ('horneado','listo'))
            dias.append({
                'fecha':           d.isoformat(),
                'dia_semana':      d.strftime('%a'),
                'items':           [dict(f) for f in filas],
                'total_unidades':  total,
                'pendiente':       pendiente,
                'horneado':        horneado,
                'tiene_plan':      len(filas) > 0,
            })
    return jsonify(dias)


@app.route('/api/produccion/calcular-orden')
@login_required
def api_produccion_calcular_orden():
    """
    Calcula la orden de trabajo de amasado para una fecha_horneado dada.
    Solo lectura — no escribe a DB.
    Query param: fecha_horneado (YYYY-MM-DD, default: mañana)
    """
    fecha_horneado = request.args.get(
        'fecha_horneado',
        (date.today() + timedelta(days=1)).isoformat()
    )
    try:
        fecha_amasado = (date.fromisoformat(fecha_horneado) - timedelta(days=1)).isoformat()
    except ValueError:
        return jsonify({'error': 'fecha_horneado debe ser YYYY-MM-DD'}), 400

    with db() as c:
        resultado = _calcular_orden_produccion(c, fecha_horneado)

    return jsonify({
        'fecha_amasado':  fecha_amasado,
        'fecha_horneado': fecha_horneado,
        **resultado,
    })


@app.route('/api/produccion/generar-plan', methods=['POST'])
@login_required
def api_produccion_generar_plan():
    """
    Genera el plan de producción en plan_produccion.
    Body JSON: { "fecha_horneado": "YYYY-MM-DD" }
    Reemplaza solo batches en estado 'pendiente'. Nunca toca amasado/horneado.
    """
    d = request.get_json(silent=True) or {}
    fecha_horneado = d.get(
        'fecha_horneado',
        (date.today() + timedelta(days=1)).isoformat()
    )
    try:
        fecha_amasado = (date.fromisoformat(fecha_horneado) - timedelta(days=1)).isoformat()
    except ValueError:
        return jsonify({'error': 'fecha_horneado debe ser YYYY-MM-DD'}), 400

    with db() as c:
        resultado = _calcular_orden_produccion(c, fecha_horneado)

        if not resultado['ordenes']:
            return jsonify({'ok': True, 'batches': [], 'advertencia': 'Sin ventas para esa fecha'}), 200

        batches_creados = []
        for orden in resultado['ordenes']:
            masa_base = orden['masa_base']

            c.execute("""
                DELETE FROM plan_produccion
                WHERE fecha_amasado = ? AND estado = 'pendiente'
                  AND producto_id IN (
                      SELECT id FROM productos WHERE masa_base = ?
                  )
            """, (fecha_amasado, masa_base))

            batch_id = str(uuid.uuid4())
            ings_json = json.dumps(orden['ingredientes'], ensure_ascii=False)

            for prod in orden['productos']:
                c.execute("""
                    INSERT INTO plan_produccion
                        (fecha, nombre_producto, cantidad, estado, producto_id,
                         fecha_amasado, fecha_horneado, batch_id, ingredientes_json, notas)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    fecha_amasado,
                    prod['nombre'],
                    prod['cantidad'],
                    'pendiente',
                    prod['producto_id'],
                    fecha_amasado,
                    fecha_horneado,
                    batch_id,
                    ings_json,
                    '',
                ))

            batches_creados.append({
                'batch_id':       batch_id,
                'masa_base':      masa_base,
                'masa_amasar_kg': orden['masa_amasar_kg'],
                'productos':      [p['nombre'] for p in orden['productos']],
                'alerta_stock':   orden['alerta_stock'],
            })

    return jsonify({
        'ok':             True,
        'fecha_amasado':  fecha_amasado,
        'fecha_horneado': fecha_horneado,
        'batches':        batches_creados,
        'sin_masa_base':  resultado['sin_masa_base'],
    }), 201


@app.route('/api/produccion/batch/<batch_id>/amasar', methods=['POST'])
@login_required
def api_produccion_batch_amasar(batch_id):
    """
    Transición pendiente → amasado.
    Descuenta ingredientes de inventario (bodega=ingredientes).
    Si stock insuficiente: descuenta lo disponible, responde con advertencias (no bloquea).
    """
    with db() as c:
        filas = c.execute(
            "SELECT * FROM plan_produccion WHERE batch_id=?", (batch_id,)
        ).fetchall()
        if not filas:
            return jsonify({'error': 'Batch no encontrado'}), 404

        estado_actual = filas[0]['estado']
        if estado_actual != 'pendiente':
            msgs = {'amasado': 'Este lote ya fue amasado', 'horneado': 'Este lote ya fue horneado'}
            return jsonify({'error': msgs.get(estado_actual, f'Estado inválido: {estado_actual}')}), 400

        # Usar ingredientes_json de la primera fila con datos
        ings_json = None
        for f in filas:
            if f['ingredientes_json'] and f['ingredientes_json'] != '[]':
                ings_json = f['ingredientes_json']
                break

        advertencias = []
        if ings_json:
            try:
                ingredientes = json.loads(ings_json)
            except Exception:
                ingredientes = []

            for ing in ingredientes:
                inv_id = ing.get('inventario_id')
                kg_necesario = float(ing.get('kg', 0))
                if not inv_id or kg_necesario <= 0:
                    continue
                row = c.execute(
                    "SELECT stock_kg, ingrediente FROM inventario WHERE id=?", (inv_id,)
                ).fetchone()
                if not row:
                    continue
                stock_actual = float(row['stock_kg'])
                descontar = min(kg_necesario, stock_actual)
                c.execute(
                    "UPDATE inventario SET stock_kg=MAX(0, stock_kg-?), ultima_actualizacion=date('now') WHERE id=?",
                    (descontar, inv_id)
                )
                if stock_actual < kg_necesario:
                    advertencias.append({
                        'ingrediente': row['ingrediente'],
                        'necesario_kg': kg_necesario,
                        'disponible_kg': stock_actual,
                        'faltante_kg': round(kg_necesario - stock_actual, 3),
                    })

        c.execute(
            "UPDATE plan_produccion SET estado='amasado' WHERE batch_id=?", (batch_id,)
        )

    return jsonify({'ok': True, 'batch_id': batch_id, 'advertencias': advertencias})


@app.route('/api/produccion/batch/<batch_id>/hornear', methods=['POST'])
@login_required
def api_produccion_batch_hornear(batch_id):
    """
    Transición amasado → horneado.
    Suma stock de productos_terminados e inventario para cada producto del batch.
    Valida que el estado sea 'amasado' — rechaza 'pendiente' con mensaje claro.
    """
    with db() as c:
        filas = c.execute(
            "SELECT * FROM plan_produccion WHERE batch_id=?", (batch_id,)
        ).fetchall()
        if not filas:
            return jsonify({'error': 'Batch no encontrado'}), 404

        estado_actual = filas[0]['estado']
        if estado_actual == 'pendiente':
            return jsonify({'error': 'Registra el amasado primero antes de hornear'}), 400
        if estado_actual in ('horneado', 'listo'):
            return jsonify({'error': 'Este lote ya fue horneado'}), 400
        if estado_actual != 'amasado':
            return jsonify({'error': f'Estado inválido para hornear: {estado_actual}'}), 400

        d_body = request.get_json(silent=True) or {}
        rendimiento_real = d_body.get('rendimiento_real')

        # Si hay rendimiento_real, distribuirlo proporcionalmente entre filas del batch
        total_planificado = sum(f['cantidad'] for f in filas if f['producto_id'])
        stock_sumado = []
        for fila in filas:
            prod_id = fila['producto_id']
            nombre  = fila['nombre_producto']
            cantidad_plan = fila['cantidad']
            if not prod_id:
                continue
            # Rendimiento real distribuido proporcionalmente
            if rendimiento_real is not None and total_planificado > 0:
                cantidad = round(float(rendimiento_real) * (cantidad_plan / total_planificado), 2)
            else:
                cantidad = cantidad_plan
            inv_id = _get_or_create_inv_terminado(c, prod_id, nombre)
            c.execute(
                "UPDATE inventario SET stock_kg=stock_kg+?, ultima_actualizacion=date('now') WHERE id=?",
                (cantidad, inv_id)
            )
            c.execute(
                "UPDATE productos SET stock=stock+? WHERE id=?",
                (cantidad, prod_id)
            )
            c.execute(
                """INSERT INTO producto_lotes (producto_id, fecha_elaboracion, cantidad_inicial, cantidad_actual, notas)
                   VALUES (?,?,?,?,?)""",
                (prod_id, fila['fecha_horneado'] or date.today().isoformat(),
                 cantidad, cantidad, f'Batch {batch_id}')
            )
            stock_sumado.append({'nombre': nombre, 'planificado': cantidad_plan, 'real': cantidad})

        rend_guardar = float(rendimiento_real) if rendimiento_real is not None else total_planificado
        c.execute(
            "UPDATE plan_produccion SET estado='horneado', rendimiento_real=? WHERE batch_id=?",
            (rend_guardar, batch_id)
        )

    diferencia = round(rend_guardar - total_planificado, 2) if rendimiento_real is not None else 0
    return jsonify({
        'ok': True, 'batch_id': batch_id, 'stock_sumado': stock_sumado,
        'planificado': total_planificado, 'real': rend_guardar, 'diferencia': diferencia
    })


@app.route('/api/produccion/manual', methods=['POST'])
@login_required
def api_produccion_manual():
    """
    Carga manual de producción (venta a calle, producción extra, etc.).
    Suma stock a inventario y productos, y registra en plan_produccion como horneado.
    Body: { "producto_id": int, "cantidad": float, "fecha": "YYYY-MM-DD", "notas": str }
    """
    d = request.get_json(silent=True) or {}
    producto_id = d.get('producto_id')
    cantidad    = float(d.get('cantidad', 0))
    fecha       = d.get('fecha') or date.today().isoformat()
    notas       = d.get('notas', 'Carga manual')

    if not producto_id or cantidad <= 0:
        return jsonify({'error': 'producto_id y cantidad > 0 son obligatorios'}), 400

    with db() as c:
        prod = c.execute("SELECT nombre FROM productos WHERE id=?", (producto_id,)).fetchone()
        if not prod:
            return jsonify({'error': 'Producto no encontrado'}), 404
        nombre  = prod['nombre']
        codigo  = 'P' + str(producto_id)

        inv_id = _get_or_create_inv_terminado(c, producto_id, nombre)
        c.execute(
            "UPDATE inventario SET stock_kg=stock_kg+?, ultima_actualizacion=date('now') WHERE id=?",
            (cantidad, inv_id)
        )
        c.execute(
            "UPDATE productos SET stock=stock+? WHERE id=?",
            (cantidad, producto_id)
        )

        batch_id = f"manual-{uuid.uuid4().hex[:8]}"
        c.execute(
            """INSERT INTO producto_lotes (producto_id, fecha_elaboracion, cantidad_inicial, cantidad_actual, notas)
               VALUES (?,?,?,?,?)""",
            (producto_id, fecha, cantidad, cantidad, f'Batch {batch_id}')
        )
        c.execute(
            """INSERT INTO plan_produccion
               (fecha, codigo_producto, nombre_producto, cantidad, estado, notas, producto_id,
                fecha_amasado, fecha_horneado, batch_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (fecha, codigo, nombre, cantidad, 'horneado', notas, producto_id, fecha, fecha, batch_id)
        )

    return jsonify({'ok': True, 'nombre': nombre, 'cantidad': cantidad, 'fecha': fecha})


# ════════════════════════════════════════════════════════════════════════════
# GASTOS
# ════════════════════════════════════════════════════════════════════════════

@app.route('/gastos')
@module_required('gastos')
def page_gastos():
    return render_template('gastos.html', active='gastos')

@app.route('/api/gastos', methods=['GET'])
@login_required
def api_gastos_list():
    desde = request.args.get('desde', (datetime.now().replace(day=1)).strftime('%Y-%m-%d'))
    hasta = request.args.get('hasta', date.today().isoformat())
    solo_fijos = request.args.get('fijos') == '1'
    with db() as c:
        if solo_fijos:
            rows = c.execute(
                "SELECT * FROM gastos WHERE recurrente=1 ORDER BY descripcion"
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM gastos WHERE fecha BETWEEN ? AND ? ORDER BY fecha DESC",
                (desde, hasta)
            ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/gastos', methods=['POST'])
@login_required
def api_gastos_create():
    d = request.get_json(silent=True) or {}
    with db() as c:
        cur = c.execute(
            "INSERT INTO gastos (fecha, descripcion, categoria, monto, proveedor, comprobante, recurrente) VALUES (?,?,?,?,?,?,?)",
            (d.get('fecha', date.today().isoformat()), d.get('descripcion',''),
             d.get('categoria','General'), float(d.get('monto',0)),
             d.get('proveedor',''), d.get('comprobante',''), 1 if d.get('recurrente') else 0)
        )
        rid = cur.lastrowid
    return jsonify({'ok': True, 'id': rid})

@app.route('/api/gastos/<int:gid>', methods=['PUT'])
@login_required
def api_gastos_update(gid):
    d = request.get_json(silent=True) or {}
    with db() as c:
        c.execute(
            "UPDATE gastos SET fecha=?, descripcion=?, categoria=?, monto=?, proveedor=?, comprobante=?, recurrente=? WHERE id=?",
            (d.get('fecha', date.today().isoformat()), d.get('descripcion',''),
             d.get('categoria','General'), float(d.get('monto',0)),
             d.get('proveedor',''), d.get('comprobante',''), 1 if d.get('recurrente') else 0, gid)
        )
    return jsonify({'ok': True})

@app.route('/api/gastos/<int:gid>', methods=['DELETE'])
@login_required
def api_gastos_delete(gid):
    with db() as c:
        c.execute("DELETE FROM gastos WHERE id=?", (gid,))
    return jsonify({'ok': True})

@app.route('/api/gastos/resumen')
@login_required
def api_gastos_resumen():
    mes_inicio = datetime.now().replace(day=1).strftime('%Y-%m-%d')
    with db() as c:
        total_mes = c.execute(
            "SELECT COALESCE(SUM(monto),0) FROM gastos WHERE fecha >= ?", (mes_inicio,)
        ).fetchone()[0]
        por_categoria = c.execute(
            "SELECT categoria, COALESCE(SUM(monto),0) as total FROM gastos WHERE fecha >= ? GROUP BY categoria ORDER BY total DESC",
            (mes_inicio,)
        ).fetchall()
    return jsonify({'total_mes': total_mes, 'por_categoria': [dict(r) for r in por_categoria]})

@app.route('/api/gastos/aplicar-fijos', methods=['POST'])
@login_required
def api_gastos_aplicar_fijos():
    """Crea registros de gastos para el mes actual a partir de los gastos recurrentes."""
    from datetime import date as _date
    hoy = _date.today()
    mes_inicio = hoy.replace(day=1).isoformat()
    mes_fin    = (hoy.replace(day=28) + __import__('datetime').timedelta(days=4)).replace(day=1).isoformat()
    with db() as c:
        fijos = c.execute("SELECT * FROM gastos WHERE recurrente=1").fetchall()
        creados = 0
        for f in fijos:
            ya_existe = c.execute(
                "SELECT id FROM gastos WHERE recurrente=0 AND descripcion=? AND fecha BETWEEN ? AND ?",
                (f['descripcion'], mes_inicio, mes_fin)
            ).fetchone()
            if not ya_existe:
                c.execute(
                    "INSERT INTO gastos (fecha, descripcion, categoria, monto, proveedor, comprobante, recurrente) VALUES (?,?,?,?,?,?,0)",
                    (mes_inicio, f['descripcion'], f['categoria'], f['monto'], f['proveedor'], '')
                )
                creados += 1
    return jsonify({'ok': True, 'creados': creados})


# ════════════════════════════════════════════════════════════════════════════
# DASHBOARD MÓVIL
# ════════════════════════════════════════════════════════════════════════════

@app.route('/api/movil/stats')
@login_required
def api_movil_stats():
    from datetime import date as _date, timedelta
    hoy        = _date.today()
    semana_ini = (hoy - timedelta(days=hoy.weekday())).isoformat()
    mes_ini    = hoy.replace(day=1).isoformat()
    mes_ant    = (hoy.replace(day=1) - timedelta(days=1)).replace(day=1).isoformat()
    mes_ant_fin= (hoy.replace(day=1) - timedelta(days=1)).isoformat()

    with db() as c:
        def vsum(desde, hasta):
            r = c.execute("SELECT COALESCE(SUM(total),0), COUNT(*) FROM ventas WHERE fecha BETWEEN ? AND ?",
                          (desde, hasta)).fetchone()
            return float(r[0]), int(r[1])

        v_hoy,  t_hoy    = vsum(hoy.isoformat(), hoy.isoformat())
        v_sem,  t_sem    = vsum(semana_ini, hoy.isoformat())
        v_mes,  _        = vsum(mes_ini, hoy.isoformat())
        v_ant,  _        = vsum(mes_ant, mes_ant_fin)

        costo_mes = c.execute(
            "SELECT COALESCE(SUM(vi.cantidad * vi.precio_unitario * (p.costo / NULLIF(p.precio,0))),0) "
            "FROM venta_items vi JOIN ventas v ON v.id=vi.venta_id "
            "JOIN productos p ON p.id=vi.producto_id WHERE v.fecha >= ?", (mes_ini,)
        ).fetchone()[0]
        gasto_mes = c.execute(
            "SELECT COALESCE(SUM(monto),0) FROM gastos WHERE fecha >= ?", (mes_ini,)
        ).fetchone()[0]
        margen = ((v_mes - costo_mes) / v_mes * 100) if v_mes > 0 else 0

        despachos_pendientes = c.execute(
            "SELECT COUNT(*) FROM ventas WHERE estado_despacho='PENDIENTE' AND con_despacho=1"
        ).fetchone()[0]

        top = c.execute(
            """SELECT p.nombre, SUM(vi.cantidad) as qty, SUM(vi.cantidad * vi.precio_unitario) as total
               FROM venta_items vi JOIN ventas v ON v.id=vi.venta_id JOIN productos p ON p.id=vi.producto_id
               WHERE v.fecha >= ? GROUP BY p.nombre ORDER BY total DESC LIMIT 5""",
            (semana_ini,)
        ).fetchall()

    return jsonify({
        'ventas_hoy': v_hoy, 'transacciones_hoy': t_hoy,
        'ventas_semana': v_sem, 'trans_semana': t_sem,
        'ventas_mes': v_mes, 'ventas_mes_anterior': v_ant,
        'gastos_mes': gasto_mes, 'margen_mes': round(margen, 1),
        'despachos_pendientes': despachos_pendientes,
        'top_productos': [{'nombre': r['nombre'], 'qty': r['qty'], 'total': r['total']} for r in top],
    })

@app.route('/api/movil/stock-critico')
@login_required
def api_movil_stock_critico():
    with db() as c:
        rows = c.execute(
            "SELECT nombre, stock, unidad FROM productos WHERE activo=1 AND stock <= 5 ORDER BY stock ASC LIMIT 8"
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/movil/ultimas-ventas')
@login_required
def api_movil_ultimas_ventas():
    with db() as c:
        rows = c.execute(
            """SELECT v.id, v.fecha, v.total, v.canal,
                      COALESCE(c.nombre,'—') as cliente
               FROM ventas v LEFT JOIN clientes c ON c.id=v.cliente_id
               ORDER BY v.id DESC LIMIT 10"""
        ).fetchall()
    return jsonify([dict(r) for r in rows])


# ════════════════════════════════════════════════════════════════════════════
# AGENDA
# ════════════════════════════════════════════════════════════════════════════

@app.route('/agenda')
@module_required('agenda')
def page_agenda():
    return render_template('agenda.html', active='agenda')

@app.route('/api/agenda', methods=['GET'])
@login_required
def api_agenda_list():
    solo_pendientes = request.args.get('pendientes', '0') == '1'
    fecha = request.args.get('fecha')
    with db() as c:
        if fecha:
            rows = c.execute(
                "SELECT * FROM agenda WHERE fecha=? ORDER BY prioridad DESC, hora",
                (fecha,)
            ).fetchall()
        elif solo_pendientes:
            rows = c.execute(
                "SELECT * FROM agenda WHERE completado=0 ORDER BY fecha, prioridad DESC",
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM agenda ORDER BY fecha ASC, prioridad DESC"
            ).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            subs = c.execute(
                "SELECT * FROM agenda_subtareas WHERE tarea_id=? ORDER BY orden, id",
                (item['id'],)
            ).fetchall()
            item['subtareas'] = [dict(s) for s in subs]
            result.append(item)
    return jsonify(result)

@app.route('/api/agenda', methods=['POST'])
@login_required
def api_agenda_create():
    d = request.get_json(silent=True) or {}
    with db() as c:
        cur = c.execute(
            "INSERT INTO agenda (tipo, titulo, descripcion, fecha, hora, completado, prioridad, responsable, fecha_fin) VALUES (?,?,?,?,?,?,?,?,?)",
            (d.get('tipo','tarea'), d.get('titulo',''), d.get('descripcion',''),
             d.get('fecha', date.today().isoformat()), d.get('hora',''),
             0, d.get('prioridad','media'), d.get('responsable',''), d.get('fecha_fin',''))
        )
        rid = cur.lastrowid
    return jsonify({'ok': True, 'id': rid})

@app.route('/api/agenda/<int:aid>', methods=['PUT'])
@login_required
def api_agenda_update(aid):
    d = request.get_json(silent=True) or {}
    with db() as c:
        c.execute(
            "UPDATE agenda SET tipo=?, titulo=?, descripcion=?, fecha=?, hora=?, prioridad=?, responsable=?, fecha_fin=? WHERE id=?",
            (d.get('tipo','tarea'), d.get('titulo',''), d.get('descripcion',''),
             d.get('fecha', date.today().isoformat()), d.get('hora',''),
             d.get('prioridad','media'), d.get('responsable',''), d.get('fecha_fin',''), aid)
        )
    return jsonify({'ok': True})

@app.route('/api/agenda/<int:aid>/completar', methods=['POST'])
@login_required
def api_agenda_completar(aid):
    with db() as c:
        c.execute("UPDATE agenda SET completado=1 WHERE id=?", (aid,))
    return jsonify({'ok': True})

@app.route('/api/agenda/<int:aid>', methods=['DELETE'])
@login_required
def api_agenda_delete(aid):
    with db() as c:
        c.execute("DELETE FROM agenda_subtareas WHERE tarea_id=?", (aid,))
        c.execute("DELETE FROM agenda WHERE id=?", (aid,))
    return jsonify({'ok': True})

@app.route('/api/agenda/<int:aid>/subtareas', methods=['POST'])
@login_required
def api_agenda_subtarea_create(aid):
    d = request.get_json(silent=True) or {}
    with db() as c:
        cur = c.execute(
            "INSERT INTO agenda_subtareas (tarea_id, titulo, responsable, fecha_inicio, fecha_fin, completado, orden) VALUES (?,?,?,?,?,0,?)",
            (aid, d.get('titulo',''), d.get('responsable',''),
             d.get('fecha_inicio',''), d.get('fecha_fin',''),
             d.get('orden', 0))
        )
        rid = cur.lastrowid
    return jsonify({'ok': True, 'id': rid})

@app.route('/api/agenda/subtareas/<int:sid>', methods=['PUT'])
@login_required
def api_agenda_subtarea_update(sid):
    d = request.get_json(silent=True) or {}
    with db() as c:
        c.execute(
            "UPDATE agenda_subtareas SET titulo=?, responsable=?, fecha_inicio=?, fecha_fin=?, orden=? WHERE id=?",
            (d.get('titulo',''), d.get('responsable',''),
             d.get('fecha_inicio',''), d.get('fecha_fin',''),
             d.get('orden', 0), sid)
        )
    return jsonify({'ok': True})

@app.route('/api/agenda/subtareas/<int:sid>', methods=['DELETE'])
@login_required
def api_agenda_subtarea_delete(sid):
    with db() as c:
        c.execute("DELETE FROM agenda_subtareas WHERE id=?", (sid,))
    return jsonify({'ok': True})

@app.route('/api/agenda/subtareas/<int:sid>/completar', methods=['POST'])
@login_required
def api_agenda_subtarea_completar(sid):
    with db() as c:
        row = c.execute("SELECT completado FROM agenda_subtareas WHERE id=?", (sid,)).fetchone()
        nuevo = 0 if (row and row['completado']) else 1
        c.execute("UPDATE agenda_subtareas SET completado=? WHERE id=?", (nuevo, sid))
    return jsonify({'ok': True, 'completado': nuevo})


# ════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DEL NEGOCIO
# ════════════════════════════════════════════════════════════════════════════

@app.route('/configuracion-negocio')
@module_required('config_negocio')
def page_config_negocio():
    return render_template('config_negocio.html', active='config_negocio')

@app.route('/api/config-negocio', methods=['GET'])
@login_required
def api_config_negocio_list():
    with db() as c:
        rows = c.execute("SELECT * FROM config_negocio ORDER BY clave").fetchall()
    result = {}
    for r in rows:
        import json as _j
        val = r['valor']
        if r['tipo'] == 'json':
            try:
                val = _j.loads(val)
            except Exception:
                pass
        result[r['clave']] = {'id': r['id'], 'valor': val, 'tipo': r['tipo'], 'descripcion': r['descripcion']}
    return jsonify(result)

@app.route('/api/config-negocio/<clave>', methods=['POST'])
@login_required
def api_config_negocio_update(clave):
    import json as _j
    d = request.get_json(silent=True) or {}
    valor = d.get('valor', '')
    if isinstance(valor, (dict, list)):
        valor = _j.dumps(valor, ensure_ascii=False)
    tipo = d.get('tipo', 'texto')
    descripcion = d.get('descripcion', '')
    with db() as c:
        existing = c.execute("SELECT id FROM config_negocio WHERE clave=?", (clave,)).fetchone()
        if existing:
            c.execute("UPDATE config_negocio SET valor=?, tipo=?, descripcion=? WHERE clave=?",
                      (valor, tipo, descripcion, clave))
        else:
            c.execute("INSERT INTO config_negocio (clave, valor, tipo, descripcion) VALUES (?,?,?,?)",
                      (clave, valor, tipo, descripcion))
    return jsonify({'ok': True})


# ════════════════════════════════════════════════════════════════════════════
# ENDPOINTS AGENTES — todas las rutas /api/agentes/*
# ════════════════════════════════════════════════════════════════════════════

@app.route('/api/agentes/inventario')
def api_agentes_inventario():
    """Stock actual + alertas de reposición para el agente de producción."""
    agent_key = request.args.get('key', '')
    with db() as c:
        rows = c.execute("SELECT * FROM inventario ORDER BY ingrediente").fetchall()
    items = [dict(r) for r in rows]
    alertas = [i for i in items if i['stock_kg'] <= i['alerta_minimo_kg']]
    return jsonify({'inventario': items, 'alertas_reposicion': alertas, 'total_items': len(items)})

@app.route('/api/agentes/produccion/<fecha>')
def api_agentes_produccion_fecha(fecha):
    """Plan de producción de una fecha + ingredientes necesarios."""
    import json as _j
    with db() as c:
        plan = c.execute(
            "SELECT * FROM plan_produccion WHERE fecha=? ORDER BY nombre_producto", (fecha,)
        ).fetchall()
        config_row = c.execute("SELECT valor FROM config_negocio WHERE clave='recetas'").fetchone()

    recetas = {}
    if config_row:
        try:
            recetas = _j.loads(config_row['valor'])
        except Exception:
            pass

    plan_list = [dict(r) for r in plan]

    # Calcular ingredientes necesarios
    ingredientes_necesarios = {}
    for item in plan_list:
        codigo = item['codigo_producto'].upper()
        cantidad = item['cantidad']
        if codigo in recetas:
            for ing, gramos in recetas[codigo].get('ingredientes', {}).items():
                ingredientes_necesarios[ing] = ingredientes_necesarios.get(ing, 0) + (gramos * cantidad)

    return jsonify({
        'fecha': fecha,
        'plan': plan_list,
        'ingredientes_necesarios': ingredientes_necesarios,
        'total_piezas': sum(i['cantidad'] for i in plan_list),
    })

@app.route('/api/agentes/gastos')
def api_agentes_gastos():
    """Gastos del mes actual para el agente de finanzas."""
    mes_inicio = datetime.now().replace(day=1).strftime('%Y-%m-%d')
    hoy = date.today().isoformat()
    with db() as c:
        gastos = c.execute(
            "SELECT * FROM gastos WHERE fecha BETWEEN ? AND ? ORDER BY fecha DESC",
            (mes_inicio, hoy)
        ).fetchall()
        total_mes = c.execute(
            "SELECT COALESCE(SUM(monto),0) FROM gastos WHERE fecha >= ?", (mes_inicio,)
        ).fetchone()[0]
        por_cat = c.execute(
            "SELECT categoria, COALESCE(SUM(monto),0) as total FROM gastos WHERE fecha >= ? GROUP BY categoria",
            (mes_inicio,)
        ).fetchall()
    return jsonify({
        'gastos': [dict(r) for r in gastos],
        'total_mes': total_mes,
        'por_categoria': [dict(r) for r in por_cat],
    })

@app.route('/api/agentes/agenda')
def api_agentes_agenda():
    """Tareas y eventos pendientes para el orquestador."""
    hoy = date.today().isoformat()
    with db() as c:
        hoy_items = c.execute(
            "SELECT * FROM agenda WHERE fecha=? AND completado=0 ORDER BY prioridad DESC",
            (hoy,)
        ).fetchall()
        proximos = c.execute(
            "SELECT * FROM agenda WHERE fecha > ? AND completado=0 ORDER BY fecha, prioridad DESC LIMIT 10",
            (hoy,)
        ).fetchall()
        vencidos = c.execute(
            "SELECT * FROM agenda WHERE fecha < ? AND completado=0 ORDER BY fecha",
            (hoy,)
        ).fetchall()
    return jsonify({
        'hoy': [dict(r) for r in hoy_items],
        'proximos': [dict(r) for r in proximos],
        'vencidos': [dict(r) for r in vencidos],
    })

@app.route('/api/agentes/config')
def api_agentes_config():
    """Configuración completa del negocio para los agentes."""
    import json as _j
    with db() as c:
        rows = c.execute("SELECT * FROM config_negocio").fetchall()
    result = {}
    for r in rows:
        val = r['valor']
        if r['tipo'] == 'json':
            try:
                val = _j.loads(val)
            except Exception:
                pass
        result[r['clave']] = val
    return jsonify(result)

@app.route('/api/agentes/agenda', methods=['POST'])
def api_agentes_agenda_crear():
    """Permite a los agentes crear tareas/eventos en la agenda del dueño."""
    d = request.get_json(silent=True) or {}
    with db() as c:
        cur = c.execute(
            "INSERT INTO agenda "
            "(tipo, titulo, descripcion, fecha, hora, prioridad, "
            "tipo_agente, telefono_destino, payload_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                d.get('tipo', 'tarea'),
                d.get('titulo', ''),
                d.get('descripcion', ''),
                d.get('fecha', date.today().isoformat()),
                d.get('hora', ''),
                d.get('prioridad', 'media'),
                d.get('tipo_agente'),
                d.get('telefono_destino'),
                d.get('payload_json'),
            )
        )
        rid = cur.lastrowid
    return jsonify({'ok': True, 'id': rid})


@app.route('/api/agentes/agenda/sophie-pendientes')
def api_agenda_sophie_pendientes():
    """Tareas sophie_tarea pendientes cuya fecha+hora ya llegó."""
    from datetime import datetime as _dt
    ahora = _dt.now().strftime('%Y-%m-%d %H:%M')
    with db() as c:
        rows = c.execute(
            "SELECT * FROM agenda "
            "WHERE tipo_agente='sophie_tarea' AND completado=0 "
            "AND (fecha || ' ' || COALESCE(hora,'00:00')) <= ? "
            "ORDER BY fecha, hora",
            (ahora,)
        ).fetchall()
    return jsonify({'tareas': [dict(r) for r in rows]})


@app.route('/api/agentes/agenda/<int:tid>/completar', methods=['POST'])
def api_agentes_agenda_completar(tid):
    """Marca una tarea como completada y registra ejecutado_en."""
    from datetime import datetime as _dt
    with db() as c:
        c.execute(
            "UPDATE agenda SET completado=1, ejecutado_en=? WHERE id=?",
            (_dt.now().isoformat(), tid)
        )
    return jsonify({'ok': True})

@app.route('/api/agentes/gastos', methods=['POST'])
def api_agentes_gastos_crear():
    """Permite a los agentes registrar un gasto."""
    d = request.get_json(silent=True) or {}
    with db() as c:
        cur = c.execute(
            "INSERT INTO gastos (fecha, descripcion, categoria, monto, proveedor) VALUES (?,?,?,?,?)",
            (d.get('fecha', date.today().isoformat()), d.get('descripcion',''),
             d.get('categoria','General'), float(d.get('monto',0)), d.get('proveedor',''))
        )
        rid = cur.lastrowid
    return jsonify({'ok': True, 'id': rid})

@app.route('/api/agentes/inventario/descontar', methods=['POST'])
def api_agentes_inventario_descontar():
    """Descuenta ingredientes del inventario después de producción."""
    import json as _j
    d = request.get_json(silent=True) or {}
    ingredientes = d.get('ingredientes', {})  # {nombre: gramos}
    with db() as c:
        for nombre, gramos in ingredientes.items():
            kg = float(gramos) / 1000
            c.execute(
                "UPDATE inventario SET stock_kg = MAX(0, stock_kg - ?), ultima_actualizacion=? WHERE ingrediente=?",
                (kg, date.today().isoformat(), nombre)
            )
    return jsonify({'ok': True, 'descontados': list(ingredientes.keys())})


# ════════════════════════════════════════════════════════════════════════════
# ENDPOINTS AGENTES — CRM
# ════════════════════════════════════════════════════════════════════════════


# -- CRM: Captura desde WhatsApp (Sophie) ------------------------------------
@app.route('/api/agentes/crm/whatsapp-lead', methods=['POST'])
def api_crm_whatsapp_lead():
    # Auth: la valida _global_auth (X-Agent-Key / sesión web)
    d = request.json
    telefono = (d.get('telefono') or '').strip()
    nombre   = _limpiar_texto((d.get('nombre') or d.get('pushName') or 'Sin nombre').strip())
    mensaje  = d.get('mensaje', '')
    if not telefono:
        return jsonify({'error': 'telefono requerido'}), 400
    with db() as c:
        lead = c.execute("SELECT * FROM crm_leads WHERE telefono=? LIMIT 1", (telefono,)).fetchone()
        if lead:
            c.execute("UPDATE crm_leads SET fecha_ultimo_contacto=? WHERE id=?", (str(date.today()), lead['id']))
            if mensaje:
                c.execute(
                    "INSERT INTO crm_interacciones (lead_id,tipo,direccion,asunto,contenido,fecha) VALUES (?,?,?,?,?,?)",
                    (lead['id'], 'whatsapp', 'entrante', 'Mensaje WhatsApp', mensaje[:500],
                     datetime.now().strftime('%Y-%m-%d %H:%M'))
                )
            return jsonify({'action': 'updated', 'lead_id': lead['id'], 'nombre': lead['nombre']})
        else:
            cur = c.execute(
                "INSERT INTO crm_leads (modulo,nombre,telefono,canal_origen,canal_origen_detalle,etapa,temperatura,fecha_ultimo_contacto) VALUES ('B2C',?,?,'whatsapp','Sophie','NUEVO','COLD',?)",
                (nombre, telefono, str(date.today()))
            )
            lid = cur.lastrowid
            if mensaje:
                c.execute(
                    "INSERT INTO crm_interacciones (lead_id,tipo,direccion,asunto,contenido,fecha) VALUES (?,?,?,?,?,?)",
                    (lid, 'whatsapp', 'entrante', 'Primer contacto WhatsApp', mensaje[:500],
                     datetime.now().strftime('%Y-%m-%d %H:%M'))
                )
            return jsonify({'action': 'created', 'lead_id': lid, 'nombre': nombre}), 201

@app.route('/api/agentes/crm/leads', methods=['GET'])
def api_agentes_crm_leads():
    """Lista leads con filtro opcional por etapa y módulo."""
    etapa  = request.args.get('etapa', '')
    modulo = request.args.get('modulo', '')
    limit  = int(request.args.get('limit', 200))
    with db() as c:
        sql = "SELECT * FROM crm_leads WHERE 1=1"
        params = []
        if etapa:  sql += " AND etapa=?";  params.append(etapa)
        if modulo: sql += " AND modulo=?"; params.append(modulo)
        sql += " ORDER BY fecha_creacion DESC LIMIT ?"
        params.append(limit)
        rows = c.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/agentes/crm/leads', methods=['POST'])
def api_agentes_crm_leads_crear():
    """Crea un nuevo lead desde un agente."""
    import json as _j
    d = request.get_json(silent=True) or {}
    modulo = d.get('modulo', 'B2B')
    etapa_default = PIPELINES[modulo][0] if modulo in PIPELINES else 'PROSPECTO'
    with db() as c:
        cur = c.execute("""
            INSERT INTO crm_leads
            (modulo,nombre,email,telefono,empresa,cargo,rut,zona,direccion,canal_origen,etapa,temperatura,notas,valor_potencial,fecha_creacion)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            modulo,
            _limpiar_texto(d.get('nombre','')),
            d.get('email',''),
            d.get('telefono',''),
            _limpiar_texto(d.get('empresa','')),
            d.get('cargo',''),
            d.get('rut',''),
            d.get('zona',''),
            d.get('direccion',''),
            d.get('canal_origen','agente'),
            d.get('etapa', etapa_default),
            d.get('temperatura','COLD'),
            d.get('notas',''),
            float(d.get('valor_potencial',0)),
            str(date.today()),
        ))
        lid = cur.lastrowid
    return jsonify({'ok': True, 'id': lid})


@app.route('/api/agentes/crm/leads/<int:lid>/mover', methods=['POST'])
def api_agentes_crm_mover(lid):
    """Mueve un lead a una nueva etapa y registra la actividad."""
    d = request.get_json(silent=True) or {}
    nueva_etapa = d.get('etapa', '')
    nota = d.get('nota', '')
    if not nueva_etapa:
        return jsonify({'error': 'etapa requerida'}), 400
    hoy = str(date.today())
    with db() as c:
        c.execute("UPDATE crm_leads SET etapa=?, fecha_ultimo_contacto=? WHERE id=?",
                  (nueva_etapa, hoy, lid))
        if nota:
            c.execute("INSERT INTO crm_interacciones (lead_id,tipo,direccion,asunto,contenido,resultado) VALUES (?,?,?,?,?,?)",
                      (lid, 'nota', 'interno', 'Cambio etapa', f"→ {nueva_etapa}: {nota}", 'ok'))
    return jsonify({'ok': True})


@app.route('/api/agentes/crm/leads/<int:lid>/interaccion', methods=['POST'])
def api_agentes_crm_interaccion(lid):
    """Registra una interacción (envío email, WA, llamada, etc.)."""
    d = request.get_json(silent=True) or {}
    hoy = str(date.today())
    with db() as c:
        c.execute("""
            INSERT INTO crm_interacciones (lead_id,tipo,direccion,asunto,contenido,resultado,fecha)
            VALUES (?,?,?,?,?,?,?)
        """, (
            lid,
            d.get('tipo','email'),
            d.get('direccion','saliente'),
            d.get('asunto',''),
            d.get('contenido','')[:1000],
            d.get('resultado','enviado'),
            d.get('fecha', str(datetime.now())),
        ))
        c.execute("UPDATE crm_leads SET fecha_ultimo_contacto=? WHERE id=?", (hoy, lid))
    return jsonify({'ok': True})


@app.route('/api/agentes/crm/pipeline', methods=['GET'])
def api_agentes_crm_pipeline():
    """Retorna todos los leads agrupados por etapa."""
    modulo = request.args.get('modulo', 'B2B')
    etapas = PIPELINES.get(modulo, PIPELINES['B2B'])
    with db() as c:
        pipeline = {}
        for etapa in etapas:
            rows = c.execute(
                "SELECT * FROM crm_leads WHERE modulo=? AND etapa=? ORDER BY valor_potencial DESC",
                (modulo, etapa)
            ).fetchall()
            pipeline[etapa] = [dict(r) for r in rows]
    return jsonify({'modulo': modulo, 'etapas': etapas, 'pipeline': pipeline})


@app.route('/api/agentes/crm/seguimientos', methods=['GET'])
def api_agentes_crm_seguimientos():
    """Leads que necesitan seguimiento según fecha_proximo_contacto."""
    hoy = str(date.today())
    with db() as c:
        vencidos = c.execute(
            "SELECT * FROM crm_leads WHERE fecha_proximo_contacto != '' AND fecha_proximo_contacto <= ? AND convertido=0 ORDER BY temperatura DESC, fecha_proximo_contacto",
            (hoy,)
        ).fetchall()
        sin_contacto = c.execute(
            "SELECT * FROM crm_leads WHERE fecha_ultimo_contacto='' AND convertido=0 ORDER BY fecha_creacion LIMIT 20"
        ).fetchall()
    return jsonify({
        'vencidos': [dict(r) for r in vencidos],
        'sin_contacto': [dict(r) for r in sin_contacto],
        'total': len(vencidos) + len(sin_contacto),
    })


@app.route('/api/agentes/crm/metricas', methods=['GET'])
def api_agentes_crm_metricas():
    """Métricas globales del CRM."""
    mes_actual = str(date.today())[:7]
    semana_atras = str(date.today() - timedelta(days=7))
    with db() as c:
        total   = c.execute("SELECT COUNT(*) FROM crm_leads").fetchone()[0]
        conv    = c.execute("SELECT COUNT(*) FROM crm_leads WHERE convertido=1").fetchone()[0]
        hot     = c.execute("SELECT COUNT(*) FROM crm_leads WHERE temperatura='HOT' AND convertido=0").fetchone()[0]
        nuevos_semana = c.execute(
            "SELECT COUNT(*) FROM crm_leads WHERE fecha_creacion >= ?", (semana_atras,)
        ).fetchone()[0]
        inter_semana = c.execute(
            "SELECT COUNT(*) FROM crm_interacciones WHERE fecha >= ?", (semana_atras,)
        ).fetchone()[0]
        por_etapa_b2b = {}
        for etapa in PIPELINES['B2B']:
            por_etapa_b2b[etapa] = c.execute(
                "SELECT COUNT(*) FROM crm_leads WHERE modulo='B2B' AND etapa=?", (etapa,)
            ).fetchone()[0]
        por_etapa_b2c = {}
        for etapa in PIPELINES['B2C']:
            por_etapa_b2c[etapa] = c.execute(
                "SELECT COUNT(*) FROM crm_leads WHERE modulo='B2C' AND etapa=?", (etapa,)
            ).fetchone()[0]
        tasa_conv = round(conv / total * 100, 1) if total else 0

    return jsonify({
        'total': total,
        'convertidos': conv,
        'hot': hot,
        'tasa_conversion': tasa_conv,
        'nuevos_semana': nuevos_semana,
        'interacciones_semana': inter_semana,
        'pipeline_b2b': por_etapa_b2b,
        'pipeline_b2c': por_etapa_b2c,
    })


@app.route('/api/agentes/crm/leads/<int:lid>', methods=['GET'])
def api_agentes_crm_lead_get(lid):
    """Retorna un lead por ID con sus interacciones."""
    with db() as c:
        lead = c.execute("SELECT * FROM crm_leads WHERE id=?", (lid,)).fetchone()
        if not lead:
            return jsonify({'error': 'Lead no encontrado'}), 404
        inters = c.execute(
            "SELECT * FROM crm_interacciones WHERE lead_id=? ORDER BY fecha DESC LIMIT 20", (lid,)
        ).fetchall()
    return jsonify({'lead': dict(lead), 'interacciones': [dict(i) for i in inters]})


@app.route('/api/agentes/memoria', methods=['POST'])
def api_agentes_memoria_guardar():
    """Guarda un episodio de memoria de agente."""
    d = request.get_json(silent=True) or {}
    with db() as c:
        c.execute(
            "INSERT INTO agente_memoria (agente,pregunta,respuesta_resumen,resultado,aprendizaje) VALUES (?,?,?,?,?)",
            (d.get('agente',''), d.get('pregunta','')[:500],
             d.get('respuesta_resumen','')[:1000],
             d.get('resultado','ok'), d.get('aprendizaje',''))
        )
        # Mantener solo últimos 500 episodios por agente
        c.execute("""
            DELETE FROM agente_memoria WHERE id IN (
                SELECT id FROM agente_memoria WHERE agente=?
                ORDER BY id DESC LIMIT -1 OFFSET 500
            )
        """, (d.get('agente',''),))
    return jsonify({'ok': True})


@app.route('/api/agentes/memoria/<agente>', methods=['GET'])
def api_agentes_memoria_leer(agente):
    """Retorna los últimos episodios de un agente."""
    limit = int(request.args.get('limit', 5))
    with db() as c:
        rows = c.execute(
            "SELECT * FROM agente_memoria WHERE agente=? ORDER BY id DESC LIMIT ?",
            (agente, limit)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/agentes/ventas', methods=['POST'])
def api_agentes_ventas():
    """Crea una venta WhatsApp desde agentes. Sin items con producto_id — solo notas."""
    d = request.get_json(silent=True) or {}
    with db() as c:
        cur = c.execute(
            """INSERT INTO ventas
               (fecha, cliente_id, canal, total, notas,
                fecha_despacho, con_despacho, tipo_cliente, estado_pago, estado_despacho, sucursal_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                d.get('fecha', str(date.today())),
                d.get('cliente_id') or None,
                d.get('canal', 'whatsapp'),
                float(d.get('total', 0)),
                d.get('notas', ''),
                d.get('fecha_despacho', ''),
                int(d.get('con_despacho', 1)),
                d.get('tipo_cliente', 'CLIENTE'),
                d.get('estado_pago', 'PENDIENTE'),
                d.get('estado_despacho', 'PENDIENTE'),
                int(d.get('sucursal_id') or 1),
            )
        )
    return jsonify({'id': cur.lastrowid, 'ok': True})


@app.route('/api/agentes/lid-cache', methods=['GET'])
def api_lid_cache_get():
    with db() as c:
        rows = c.execute("SELECT lid, telefono FROM whatsapp_lid_cache").fetchall()
    return jsonify({r['lid']: r['telefono'] for r in rows})


@app.route('/api/agentes/lid-cache', methods=['POST'])
def api_lid_cache_post():
    data = request.get_json(silent=True) or {}
    with db() as c:
        for lid, telefono in data.items():
            c.execute(
                "INSERT INTO whatsapp_lid_cache (lid, telefono, updated_at) VALUES (?,?,datetime('now')) "
                "ON CONFLICT(lid) DO UPDATE SET telefono=excluded.telefono, updated_at=excluded.updated_at",
                (str(lid), str(telefono))
            )
    return jsonify({'ok': True, 'total': len(data)})


@app.route('/api/agentes/conversaciones/<telefono>', methods=['GET'])
def api_conversacion_get(telefono):
    with db() as c:
        row = c.execute(
            "SELECT * FROM whatsapp_conversaciones WHERE telefono=?", (telefono,)
        ).fetchone()
    if not row:
        return jsonify({}), 404
    return jsonify(dict(row))


@app.route('/api/agentes/conversaciones/<telefono>', methods=['POST'])
def api_conversacion_post(telefono):
    data = request.get_json(silent=True) or {}
    with db() as c:
        c.execute(
            "INSERT INTO whatsapp_conversaciones "
            "(telefono, tipo, mensajes_json, cliente_data_json, pedido_guardado, updated_at) "
            "VALUES (?,?,?,?,?,datetime('now')) "
            "ON CONFLICT(telefono) DO UPDATE SET "
            "tipo=excluded.tipo, mensajes_json=excluded.mensajes_json, "
            "cliente_data_json=excluded.cliente_data_json, "
            "pedido_guardado=excluded.pedido_guardado, updated_at=excluded.updated_at",
            (
                telefono,
                data.get('tipo', 'minorista'),
                data.get('mensajes_json', '[]'),
                data.get('cliente_data_json', '{}'),
                int(data.get('pedido_guardado', 0)),
            )
        )
    return jsonify({'ok': True})


@app.route('/api/agentes/conversaciones/<telefono>', methods=['DELETE'])
def api_conversacion_delete(telefono):
    with db() as c:
        c.execute("DELETE FROM whatsapp_conversaciones WHERE telefono=?", (telefono,))
    return jsonify({'ok': True})


@app.route('/api/agentes/pedidos-cliente')
def api_pedidos_cliente():
    telefono = request.args.get('telefono', '').strip()
    if not telefono:
        return jsonify({'error': 'telefono requerido'}), 400

    tel_norm = telefono.replace(' ', '').replace('-', '').replace('+', '')
    with db() as c:
        cliente = c.execute(
            "SELECT * FROM clientes "
            "WHERE replace(replace(replace(telefono,' ',''),'-',''),'+','')=? LIMIT 1",
            (tel_norm,)
        ).fetchone()

        if not cliente:
            return jsonify({'pedidos': [], 'cliente': None})

        cliente_dict = dict(cliente)
        cid = cliente_dict['id']

        pedidos = c.execute(
            "SELECT v.id, v.estado_pago, v.estado_despacho, v.fecha_despacho, v.total, v.canal, "
            "GROUP_CONCAT(p.nombre || ' x' || CAST(CAST(vi.cantidad AS INTEGER) AS TEXT), ', ') AS items "
            "FROM ventas v "
            "LEFT JOIN venta_items vi ON vi.venta_id = v.id "
            "LEFT JOIN productos p ON p.id = vi.producto_id "
            "WHERE v.cliente_id=? "
            "GROUP BY v.id ORDER BY v.fecha DESC LIMIT 5",
            (cid,)
        ).fetchall()

        total_pedidos = c.execute(
            "SELECT COUNT(*) FROM ventas WHERE cliente_id=?", (cid,)
        ).fetchone()[0]

    pedidos_list = [
        {
            'id': dict(p)['id'],
            'estado': f"{dict(p)['estado_pago']} / {dict(p)['estado_despacho']}",
            'fecha_entrega': dict(p)['fecha_despacho'] or '',
            'total': dict(p)['total'],
            'items': dict(p)['items'] or 'Pedido WhatsApp',
        }
        for p in pedidos
    ]

    return jsonify({
        'pedidos': pedidos_list,
        'cliente': {
            'id': cid,
            'nombre': cliente_dict.get('nombre', ''),
            'tipo': cliente_dict.get('tipo', 'CLIENTE'),
            'total_pedidos': total_pedidos,
        }
    })


@app.route('/api/agentes/estado')
def api_agentes_estado():
    """Dashboard unificado: actividad reciente, métricas clave, estado del sistema."""
    import json as _j
    with db() as c:
        # Actividad reciente (últimas 30 interacciones)
        actividad = [dict(r) for r in c.execute(
            "SELECT * FROM agente_memoria ORDER BY id DESC LIMIT 30"
        ).fetchall()]

        # CRM métricas
        crm_total   = c.execute("SELECT COUNT(*) FROM crm_leads").fetchone()[0]
        crm_hoy     = c.execute(
            "SELECT COUNT(*) FROM crm_leads WHERE date(fecha_proximo_contacto)<=date('now')"
            " AND etapa NOT IN ('GANADO','PERDIDO')"
        ).fetchone()[0]
        crm_etapas  = {r['etapa']: r['cnt'] for r in c.execute(
            "SELECT etapa, COUNT(*) as cnt FROM crm_leads GROUP BY etapa"
        ).fetchall()}

        # Inventario alertas
        inv_alertas = [dict(r) for r in c.execute(
            "SELECT ingrediente, stock_kg, alerta_minimo_kg FROM inventario"
            " WHERE stock_kg <= alerta_minimo_kg"
        ).fetchall()]
        inv_total   = c.execute("SELECT COUNT(*) FROM inventario").fetchone()[0]

        # Agenda tareas pendientes
        agenda_hoy  = c.execute(
            "SELECT COUNT(*) FROM agenda WHERE completado=0 AND date(fecha)=date('now')"
        ).fetchone()[0]
        agenda_tot  = c.execute(
            "SELECT COUNT(*) FROM agenda WHERE completado=0"
        ).fetchone()[0]

        # Producción hoy
        plan_hoy    = [dict(r) for r in c.execute(
            "SELECT codigo_producto, nombre_producto, cantidad, estado"
            " FROM plan_produccion WHERE fecha=date('now')"
        ).fetchall()]
        plan_piezas = sum(p['cantidad'] for p in plan_hoy)
        plan_listos = sum(1 for p in plan_hoy if p['estado'] == 'listo')

        # Gastos del mes
        mes_actual  = datetime.now().strftime('%Y-%m')
        gastos_mes  = c.execute(
            "SELECT COALESCE(SUM(monto),0) FROM gastos WHERE strftime('%Y-%m',fecha)=?",
            (mes_actual,)
        ).fetchone()[0]

    return jsonify({
        'ts': datetime.now().isoformat(),
        'actividad': actividad,
        'crm': {
            'total': crm_total,
            'seguimientos_hoy': crm_hoy,
            'por_etapa': crm_etapas,
        },
        'inventario': {
            'total_items': inv_total,
            'alertas': inv_alertas,
        },
        'agenda': {
            'pendientes_hoy': agenda_hoy,
            'total_pendientes': agenda_tot,
        },
        'produccion': {
            'items_hoy': len(plan_hoy),
            'piezas_hoy': plan_piezas,
            'listos': plan_listos,
        },
        'finanzas': {
            'gastos_mes': round(gastos_mes, 0),
        },
    })


@app.route('/agentes')
@module_required('agentes')
def page_agentes():
    railway_url = os.environ.get('RAILWAY_BAKERS_URL', 'https://web-production-40d5b.up.railway.app')
    return render_template('agentes.html', active='agentes', railway_url=railway_url)


@app.route('/api/agentes/crm/leads/<int:lid>/proximo-contacto', methods=['POST'])
def api_agentes_crm_proximo_contacto(lid):
    """Programa la fecha de próximo contacto."""
    d = request.get_json(silent=True) or {}
    fecha = d.get('fecha', '')
    with db() as c:
        c.execute("UPDATE crm_leads SET fecha_proximo_contacto=? WHERE id=?", (fecha, lid))
    return jsonify({'ok': True})


# ════════════════════════════════════════════════════════════════════════════
# FINANZAS
# ════════════════════════════════════════════════════════════════════════════

@app.route('/api/finanzas/pl')
@login_required
def api_finanzas_pl():
    """P&L mensual: ingresos vs gastos de los últimos N meses."""
    meses = int(request.args.get('meses', 6))
    hoy = date.today()
    with db() as c:
        # Ingresos cobrados (PAGADO)
        cobrado_rows = c.execute("""
            SELECT strftime('%Y-%m', fecha) as mes, COALESCE(SUM(total),0) as total
            FROM ventas
            WHERE fecha >= date('now', ? || ' months') AND estado_pago='PAGADO'
            GROUP BY mes ORDER BY mes
        """, (f'-{meses}',)).fetchall()
        # Facturado total (incluyendo pendientes)
        facturado_rows = c.execute("""
            SELECT strftime('%Y-%m', fecha) as mes, COALESCE(SUM(total),0) as total
            FROM ventas
            WHERE fecha >= date('now', ? || ' months')
            GROUP BY mes ORDER BY mes
        """, (f'-{meses}',)).fetchall()
        # Gastos
        gastos_rows = c.execute("""
            SELECT strftime('%Y-%m', fecha) as mes, COALESCE(SUM(monto),0) as total
            FROM gastos
            WHERE fecha >= date('now', ? || ' months')
            GROUP BY mes ORDER BY mes
        """, (f'-{meses}',)).fetchall()
        # Cobros pendientes con aging
        pendientes_rows = c.execute("""
            SELECT v.id, v.fecha, v.total, v.notas, v.fecha_despacho,
                   c.nombre as cliente_nombre, c.telefono as cliente_telefono,
                   CAST(julianday('now') - julianday(v.fecha) AS INTEGER) as dias_pendiente
            FROM ventas v LEFT JOIN clientes c ON c.id=v.cliente_id
            WHERE v.estado_pago='PENDIENTE'
            ORDER BY v.fecha ASC
        """).fetchall()
        # Ventas por canal (período seleccionado)
        canal_rows = c.execute("""
            SELECT v.canal,
                   COUNT(*) as num_ventas,
                   COALESCE(SUM(v.total),0) as facturado,
                   COALESCE(SUM(CASE WHEN v.estado_pago='PAGADO' THEN v.total ELSE 0 END),0) as cobrado
            FROM ventas v
            WHERE v.fecha >= date('now', ? || ' months')
            GROUP BY v.canal ORDER BY cobrado DESC
        """, (f'-{meses}',)).fetchall()
        # Márgenes por producto (mes actual)
        mes_ini = hoy.replace(day=1).isoformat()
        # Subquery: el filtro de fecha debe excluir las filas de venta_items,
        # no solo dejar vn en NULL (si va en el ON del LEFT JOIN suma histórico).
        margenes = c.execute("""
            SELECT p.nombre, p.precio, p.costo,
                   CASE WHEN p.precio>0 THEN ROUND((p.precio-p.costo)*100.0/p.precio,1) ELSE 0 END as margen_pct,
                   COALESCE(s.ventas_cnt, 0) as ventas_cnt,
                   COALESCE(s.unidades, 0)   as unidades,
                   COALESCE(s.facturado, 0)  as facturado
            FROM productos p
            LEFT JOIN (
                SELECT vi.producto_id,
                       COUNT(vi.id)                          as ventas_cnt,
                       SUM(vi.cantidad)                      as unidades,
                       SUM(vi.cantidad*vi.precio_unitario)   as facturado
                FROM venta_items vi
                JOIN ventas vn ON vn.id=vi.venta_id
                WHERE vn.fecha>=?
                GROUP BY vi.producto_id
            ) s ON s.producto_id=p.id
            WHERE p.activo=1
            ORDER BY facturado DESC
        """, (mes_ini,)).fetchall()

    # Merge por mes
    meses_set  = sorted(set([r['mes'] for r in cobrado_rows] + [r['mes'] for r in gastos_rows] + [r['mes'] for r in facturado_rows]))
    cobr_map   = {r['mes']: r['total'] for r in cobrado_rows}
    fact_map   = {r['mes']: r['total'] for r in facturado_rows}
    gst_map    = {r['mes']: r['total'] for r in gastos_rows}
    pl = []
    for mes in meses_set:
        cobr = cobr_map.get(mes, 0)
        fact = fact_map.get(mes, 0)
        gst  = gst_map.get(mes, 0)
        pl.append({'mes': mes, 'cobrado': cobr, 'facturado': fact, 'gastos': gst, 'utilidad': cobr - gst})

    # Aging buckets
    pendientes = []
    for r in pendientes_rows:
        d = dict(r)
        dias = d['dias_pendiente'] or 0
        d['aging'] = '0-30 días' if dias <= 30 else ('31-60 días' if dias <= 60 else '+60 días')
        pendientes.append(d)

    return jsonify({
        'pl': pl,
        'pendientes': pendientes,
        'canales': [dict(r) for r in canal_rows],
        'margenes': [dict(r) for r in margenes],
        'resumen': {
            'total_pendiente':       sum(r['total'] for r in pendientes_rows),
            'total_pendiente_count': len(pendientes_rows),
            'aging_30':  sum(r['total'] for r in pendientes_rows if (r['dias_pendiente'] or 0) <= 30),
            'aging_60':  sum(r['total'] for r in pendientes_rows if 30 < (r['dias_pendiente'] or 0) <= 60),
            'aging_mas': sum(r['total'] for r in pendientes_rows if (r['dias_pendiente'] or 0) > 60),
        }
    })


@app.route('/api/finanzas/flujo-caja')
@login_required
def api_finanzas_flujo_caja():
    """Proyección de flujo de caja para las próximas 4 semanas."""
    hoy = date.today()
    semanas = []
    with db() as c:
        gastos_fijos_total = c.execute(
            "SELECT COALESCE(SUM(monto),0) FROM gastos WHERE recurrente=1"
        ).fetchone()[0]
        for i in range(4):
            inicio = hoy + timedelta(weeks=i)
            fin    = hoy + timedelta(weeks=i+1) - timedelta(days=1)
            ventas_por_cobrar = c.execute("""
                SELECT COALESCE(SUM(total),0) as total, COUNT(*) as cnt
                FROM ventas
                WHERE estado_pago='PENDIENTE'
                  AND (fecha_despacho BETWEEN ? AND ? OR (fecha_despacho='' AND fecha BETWEEN ? AND ?))
            """, (inicio.isoformat(), fin.isoformat(), inicio.isoformat(), fin.isoformat())).fetchone()
            subs_renovacion = c.execute("""
                SELECT COALESCE(SUM(precio),0) as total, COUNT(*) as cnt
                FROM suscripciones
                WHERE estado='activo' AND fecha_renovacion BETWEEN ? AND ?
            """, (inicio.isoformat(), fin.isoformat())).fetchone()
            ingresos = (ventas_por_cobrar['total'] or 0) + (subs_renovacion['total'] or 0)
            semanas.append({
                'semana':       i + 1,
                'desde':        inicio.isoformat(),
                'hasta':        fin.isoformat(),
                'ventas_cobro': ventas_por_cobrar['total'] or 0,
                'ventas_cnt':   ventas_por_cobrar['cnt']   or 0,
                'subs_cobro':   subs_renovacion['total']   or 0,
                'subs_cnt':     subs_renovacion['cnt']     or 0,
                'ingresos':     round(ingresos, 0),
                'gastos_fijos': round(gastos_fijos_total / 4, 0),
                'flujo_neto':   round(ingresos - gastos_fijos_total / 4, 0),
            })
    return jsonify(semanas)


@app.route('/api/finanzas/pendientes/<int:vid>/cobrar', methods=['POST'])
@login_required
def api_finanzas_cobrar(vid):
    """Marca una venta como PAGADO."""
    with db() as c:
        c.execute("UPDATE ventas SET estado_pago='PAGADO' WHERE id=?", (vid,))
    return jsonify({'ok': True})


# ── POS Blueprint ────────────────────────────────────────────────────────────
from pos import pos_bp
app.register_blueprint(pos_bp)

# ── Arranque ──────────────────────────────────────────────────────────────────

# Inicializar DB al importar (necesario para gunicorn, además de __main__)
with app.app_context():
    init_db()
    init_automatizacion_crm()

if __name__ == '__main__':
    init_db()
    init_automatizacion_crm()
    print()
    print("  Aurora Bakers -- Sistema de Ventas")
    print("  Abre:  http://127.0.0.1:5000")
    print("  Cierra con:  Ctrl+C")
    print()
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('HOST', '0.0.0.0')
    app.run(debug=False, host=host, port=port)
