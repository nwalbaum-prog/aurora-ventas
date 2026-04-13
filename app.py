"""
Aurora Bakers — Sistema de Ventas
Corre con: python app.py  →  http://127.0.0.1:5000
"""
from flask import Flask, render_template, request, jsonify, redirect, session, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3, os, json, smtplib, urllib.request, urllib.parse, secrets
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, datetime, timedelta
from contextlib import contextmanager

ANTHROPIC_API_KEY    = os.environ.get('ANTHROPIC_API_KEY', '')
GOOGLE_PLACES_API_KEY= os.environ.get('GOOGLE_PLACES_API_KEY', 'AIzaSyA7_nd5CxsV22JmJfyhPedvxhVWAGxiBis')

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

# API key para agentes (aurora-bakers → aurora-ventas)
AGENT_API_KEY = os.environ.get('VENTAS_API_KEY', 'aurora_agent_2024')

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
    return any(r['name'] == col for r in info)

def init_db():
    with db() as c:
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
                estado_despacho  TEXT    NOT NULL DEFAULT 'PENDIENTE'
            );
            CREATE TABLE IF NOT EXISTS venta_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                venta_id        INTEGER NOT NULL REFERENCES ventas(id)    ON DELETE CASCADE,
                producto_id     INTEGER NOT NULL REFERENCES productos(id),
                cantidad        REAL    NOT NULL,
                precio_unitario REAL    NOT NULL
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
        ]
        for table, col, sql in migrations:
            if not _col_exists(c, table, col):
                c.execute(sql)

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
                ultimo_login TEXT   NOT NULL DEFAULT ''
            )
        """)
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

        # ── Tablas ERP: Inventario, Producción, Gastos, Agenda, Config ──────────
        c.executescript("""
            CREATE TABLE IF NOT EXISTS inventario (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                ingrediente           TEXT    NOT NULL UNIQUE,
                stock_kg              REAL    NOT NULL DEFAULT 0,
                alerta_minimo_kg      REAL    NOT NULL DEFAULT 1,
                proveedor             TEXT    NOT NULL DEFAULT '',
                precio_kg             REAL    NOT NULL DEFAULT 0,
                ultima_actualizacion  TEXT    NOT NULL DEFAULT (date('now'))
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
                comprobante TEXT    NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS agenda (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo        TEXT    NOT NULL DEFAULT 'tarea',
                titulo      TEXT    NOT NULL,
                descripcion TEXT    NOT NULL DEFAULT '',
                fecha       TEXT    NOT NULL DEFAULT (date('now')),
                hora        TEXT    NOT NULL DEFAULT '',
                completado  INTEGER NOT NULL DEFAULT 0,
                prioridad   TEXT    NOT NULL DEFAULT 'media',
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


# ── Autenticación de agentes (before_request) ────────────────────────────────

@app.before_request
def _validate_agent_key():
    """Valida X-Agent-Key en rutas /api/agentes/*.
    Permite acceso si: key correcta O sesión web activa (admin en navegador)."""
    if request.path.startswith('/api/agentes/'):
        key = request.headers.get('X-Agent-Key', '')
        if key == AGENT_API_KEY:
            return  # key correcta
        if session.get('user_id'):
            return  # sesión web activa
        if AGENT_API_KEY:
            return jsonify({'error': 'Unauthorized'}), 401


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
            session.permanent = True
            session['user_id']   = u['id']
            session['user_nombre'] = u['nombre']
            session['user_rol']    = u['rol']
            with db() as c:
                c.execute("UPDATE usuarios SET ultimo_login=? WHERE id=?",
                          (datetime.now().strftime('%Y-%m-%d %H:%M'), u['id']))
            next_url = request.args.get('next', '/')
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
        rows = c.execute("SELECT id,nombre,email,rol,activo,creado_en,ultimo_login FROM usuarios ORDER BY id").fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/admin/usuarios', methods=['POST'])
@login_required
def api_admin_usuarios_create():
    u = current_user()
    if u['rol'] != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    d = request.json
    if not d.get('email') or not d.get('password'):
        return jsonify({'error': 'Email y contraseña son requeridos'}), 400
    try:
        with db() as c:
            c.execute(
                "INSERT INTO usuarios (nombre,email,password,rol) VALUES (?,?,?,?)",
                (d.get('nombre',''), d['email'].lower().strip(),
                 generate_password_hash(d['password']), d.get('rol','usuario'))
            )
        return jsonify({'ok': True}), 201
    except Exception as e:
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

@app.route('/ventas')
@login_required
def page_ventas():        return render_template('ventas.html',        active='ventas')

@app.route('/clientes')
@login_required
def page_clientes():      return render_template('clientes.html',      active='clientes')

@app.route('/productos')
@login_required
def page_productos():     return render_template('productos.html',      active='productos')

@app.route('/suscripciones')
@login_required
def page_suscripciones(): return render_template('suscripciones.html', active='suscripciones')

@app.route('/reportes')
@login_required
def page_reportes():      return render_template('reportes.html',       active='reportes')

# ── API: Productos ────────────────────────────────────────────────────────────

@app.route('/api/productos', methods=['GET'])
def api_productos():
    with db() as c:
        rows = c.execute("SELECT * FROM productos WHERE activo=1 ORDER BY nombre").fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/productos', methods=['POST'])
def api_productos_create():
    d = request.json
    with db() as c:
        cur = c.execute(
            "INSERT INTO productos (nombre,descripcion,precio,precio_mayorista,costo,stock,unidad,categoria) VALUES (?,?,?,?,?,?,?,?)",
            (d['nombre'], d.get('descripcion',''), float(d['precio']),
             float(d.get('precio_mayorista',0)),
             float(d.get('costo',0)), float(d.get('stock',0)), d.get('unidad','unidad'),
             d.get('categoria','pan'))
        )
        return jsonify(dict(c.execute("SELECT * FROM productos WHERE id=?", (cur.lastrowid,)).fetchone())), 201

@app.route('/api/productos/<int:pid>', methods=['PUT'])
def api_productos_update(pid):
    d = request.json
    with db() as c:
        c.execute(
            "UPDATE productos SET nombre=?,descripcion=?,precio=?,precio_mayorista=?,costo=?,stock=?,unidad=?,categoria=?,activo=? WHERE id=?",
            (d['nombre'], d.get('descripcion',''), float(d['precio']),
             float(d.get('precio_mayorista',0)),
             float(d.get('costo',0)), float(d.get('stock',0)), d.get('unidad','unidad'),
             d.get('categoria','pan'), int(d.get('activo',1)), pid)
        )
        return jsonify(dict(c.execute("SELECT * FROM productos WHERE id=?", (pid,)).fetchone()))

@app.route('/api/productos/<int:pid>', methods=['DELETE'])
def api_productos_delete(pid):
    with db() as c:
        c.execute("UPDATE productos SET activo=0 WHERE id=?", (pid,))
        return jsonify({'ok': True})

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

    with db() as c:
        cur = c.execute(
            """INSERT INTO ventas
               (fecha, cliente_id, canal, total, notas,
                fecha_despacho, con_despacho, tipo_cliente, estado_pago, estado_despacho)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
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
            )
        )
        vid = cur.lastrowid
        for i in items:
            c.execute(
                "INSERT INTO venta_items (venta_id,producto_id,cantidad,precio_unitario) VALUES (?,?,?,?)",
                (vid, i['producto_id'], float(i['cantidad']), float(i['precio_unitario']))
            )
            c.execute("UPDATE productos SET stock=stock-? WHERE id=?",
                      (float(i['cantidad']), i['producto_id']))

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
    """Actualiza campos de estado de una venta (pago, despacho)."""
    d = request.json
    with db() as c:
        # Solo permitir actualizar estado_pago y estado_despacho
        if 'estado_pago' in d:
            c.execute("UPDATE ventas SET estado_pago=? WHERE id=?", (d['estado_pago'], vid))
        if 'estado_despacho' in d:
            c.execute("UPDATE ventas SET estado_despacho=? WHERE id=?", (d['estado_despacho'], vid))
        if 'fecha_despacho' in d:
            c.execute("UPDATE ventas SET fecha_despacho=? WHERE id=?", (d['fecha_despacho'], vid))
        row = c.execute(
            "SELECT v.*,c.nombre AS cliente_nombre FROM ventas v LEFT JOIN clientes c ON c.id=v.cliente_id WHERE v.id=?",
            (vid,)).fetchone()
        if not row:
            return jsonify({'error': 'Venta no encontrada'}), 404
        vd = dict(row)
        vd['items'] = [dict(i) for i in c.execute(
            "SELECT vi.*,p.nombre FROM venta_items vi JOIN productos p ON p.id=vi.producto_id WHERE vi.venta_id=?",
            (vid,)).fetchall()]
        return jsonify(vd)

@app.route('/api/ventas/<int:vid>', methods=['DELETE'])
def api_ventas_delete(vid):
    with db() as c:
        for i in c.execute("SELECT * FROM venta_items WHERE venta_id=?", (vid,)).fetchall():
            c.execute("UPDATE productos SET stock=stock+? WHERE id=?", (i['cantidad'], i['producto_id']))
        c.execute("DELETE FROM ventas WHERE id=?", (vid,))
        return jsonify({'ok': True})

@app.route('/api/ventas/resumen')
def api_ventas_resumen():
    today = date.today()
    w0 = today - timedelta(days=today.weekday())
    m0 = today.replace(day=1)
    with db() as c:
        def t(d1, d2):
            r = c.execute("SELECT COALESCE(SUM(total),0),COUNT(*) FROM ventas WHERE fecha BETWEEN ? AND ?",
                          (str(d1),str(d2))).fetchone()
            return {'total': r[0], 'count': r[1]}
        pendientes_pago     = c.execute("SELECT COUNT(*) FROM ventas WHERE estado_pago='PENDIENTE'").fetchone()[0]
        pendientes_despacho = c.execute("SELECT COUNT(*) FROM ventas WHERE estado_despacho='PENDIENTE'").fetchone()[0]
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


# ── API: Reportes ─────────────────────────────────────────────────────────────

def _days_for(periodo):
    return {'semana':7,'mes':30,'3meses':90,'año':365}.get(periodo,30)

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

# ── API: Resumen para agentes (WhatsApp bot / Railway) ───────────────────────

@app.route('/api/agentes/resumen')
def api_agentes_resumen():
    """
    Endpoint consumido por el sistema multi-agente (aurora-bakers en Railway).
    Devuelve datos consolidados sin necesitar acceso a Google Sheets.
    Protegido con API key simple via header X-Agent-Key o query ?key=
    """
    key = request.args.get('key') or request.headers.get('X-Agent-Key', '')
    expected = os.environ.get('AGENT_API_KEY', 'aurora_agent_2024')
    if key != expected:
        return jsonify({'error': 'Unauthorized'}), 401

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
    """Lista de despachos pendientes para hoy — para el plan de producción."""
    key = request.args.get('key') or request.headers.get('X-Agent-Key', '')
    expected = os.environ.get('AGENT_API_KEY', 'aurora_agent_2024')
    if key != expected:
        return jsonify({'error': 'Unauthorized'}), 401

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
@login_required
def page_crm():
    return render_template('crm.html', active='crm')

@app.route('/crm/lead/<int:lid>')
@login_required
def page_crm_lead(lid):
    return render_template('crm_lead.html', active='crm', lead_id=lid)

@app.route('/crm/buscar')
@login_required
def page_crm_buscar():
    return render_template('crm_buscar.html', active='crm')

@app.route('/crm/email-masivo')
@login_required
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
            nombre  = p.get('displayName', {}).get('text', '')
            telefono= p.get('internationalPhoneNumber', '')
            results.append({
                'nombre':       nombre,
                'empresa':      nombre,
                'direccion':    p.get('formattedAddress', ''),
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
            c.execute(f"UPDATE crm_leads SET {','.join(fields)} WHERE id=?", params)
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
        mod_filter = f" AND modulo='{modulo}'" if modulo else ''
        total  = c.execute(f"SELECT COUNT(*) FROM crm_leads WHERE 1=1{mod_filter}").fetchone()[0]
        conv   = c.execute(f"SELECT COUNT(*) FROM crm_leads WHERE convertido=1{mod_filter}").fetchone()[0]
        hot    = c.execute(f"SELECT COUNT(*) FROM crm_leads WHERE temperatura='HOT' AND convertido=0{mod_filter}").fetchone()[0]
        warm   = c.execute(f"SELECT COUNT(*) FROM crm_leads WHERE temperatura='WARM' AND convertido=0{mod_filter}").fetchone()[0]
        cold   = c.execute(f"SELECT COUNT(*) FROM crm_leads WHERE temperatura='COLD' AND convertido=0{mod_filter}").fetchone()[0]
        leads_mes = c.execute(
            f"SELECT COUNT(*) FROM crm_leads WHERE strftime('%Y-%m', fecha_creacion)=?{mod_filter}", (mes_actual,)
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
@login_required
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


# ── Configuración CRM ────────────────────────────────────────────────────────

@app.route('/crm/configuracion')
@login_required
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
@login_required
def page_inventario():
    return render_template('inventario.html', active='inventario')

@app.route('/api/inventario', methods=['GET'])
@login_required
def api_inventario_list():
    with db() as c:
        rows = c.execute("SELECT * FROM inventario ORDER BY ingrediente").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/inventario', methods=['POST'])
@login_required
def api_inventario_create():
    d = request.get_json(silent=True) or {}
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO inventario (ingrediente, stock_kg, alerta_minimo_kg, proveedor, precio_kg, ultima_actualizacion) VALUES (?,?,?,?,?,?)",
            (d.get('ingrediente',''), float(d.get('stock_kg',0)), float(d.get('alerta_minimo_kg',1)),
             d.get('proveedor',''), float(d.get('precio_kg',0)), date.today().isoformat())
        )
    return jsonify({'ok': True})

@app.route('/api/inventario/<int:iid>', methods=['PUT'])
@login_required
def api_inventario_update(iid):
    d = request.get_json(silent=True) or {}
    with db() as c:
        c.execute(
            "UPDATE inventario SET stock_kg=?, alerta_minimo_kg=?, proveedor=?, precio_kg=?, ultima_actualizacion=? WHERE id=?",
            (float(d.get('stock_kg',0)), float(d.get('alerta_minimo_kg',1)),
             d.get('proveedor',''), float(d.get('precio_kg',0)), date.today().isoformat(), iid)
        )
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
        c.execute(
            "UPDATE inventario SET stock_kg = MAX(0, stock_kg + ?), ultima_actualizacion=? WHERE id=?",
            (delta, date.today().isoformat(), iid)
        )
        row = c.execute("SELECT * FROM inventario WHERE id=?", (iid,)).fetchone()
    return jsonify(dict(row) if row else {'ok': False})


# ════════════════════════════════════════════════════════════════════════════
# PLAN DE PRODUCCIÓN
# ════════════════════════════════════════════════════════════════════════════

@app.route('/produccion')
@login_required
def page_produccion():
    return render_template('produccion.html', active='produccion')

@app.route('/api/plan-produccion', methods=['GET'])
@login_required
def api_plan_list():
    fecha = request.args.get('fecha', date.today().isoformat())
    with db() as c:
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
            "INSERT INTO plan_produccion (fecha, codigo_producto, nombre_producto, cantidad, estado, notas) VALUES (?,?,?,?,?,?)",
            (d.get('fecha', date.today().isoformat()), d.get('codigo_producto',''),
             d.get('nombre_producto',''), int(d.get('cantidad',0)),
             d.get('estado','pendiente'), d.get('notas',''))
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
    import json as _j
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

        config_row = c.execute("SELECT valor FROM config_negocio WHERE clave='recetas'").fetchone()

    recetas = {}
    if config_row:
        try:
            recetas = _j.loads(config_row['valor'])
        except Exception:
            pass

    # Calcular ingredientes totales a descontar
    descuentos: dict[str, float] = {}
    for item in plan:
        codigo   = item['codigo_producto'].upper()
        cantidad = item['cantidad']
        if codigo in recetas:
            for ing, gramos in recetas[codigo].get('ingredientes', {}).items():
                descuentos[ing] = descuentos.get(ing, 0) + (gramos * cantidad / 1000)  # → kg

    # Descontar inventario y recoger alertas
    alertas = []
    with db() as c:
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
        'ok':        True,
        'fecha':     fecha,
        'items_confirmados': len(plan),
        'descuentos': {k: round(v, 3) for k, v in descuentos.items()},
        'alertas_stock': alertas,
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
                "INSERT INTO plan_produccion (fecha, codigo_producto, nombre_producto, cantidad, estado, notas) VALUES (?,?,?,?,?,?)",
                (hasta, r['codigo_producto'], r['nombre_producto'], r['cantidad'], 'pendiente', r['notas'])
            )
    return jsonify({'ok': True, 'copiados': len(rows)})


# ════════════════════════════════════════════════════════════════════════════
# GASTOS
# ════════════════════════════════════════════════════════════════════════════

@app.route('/gastos')
@login_required
def page_gastos():
    return render_template('gastos.html', active='gastos')

@app.route('/api/gastos', methods=['GET'])
@login_required
def api_gastos_list():
    desde = request.args.get('desde', (datetime.now().replace(day=1)).strftime('%Y-%m-%d'))
    hasta = request.args.get('hasta', date.today().isoformat())
    with db() as c:
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
            "INSERT INTO gastos (fecha, descripcion, categoria, monto, proveedor, comprobante) VALUES (?,?,?,?,?,?)",
            (d.get('fecha', date.today().isoformat()), d.get('descripcion',''),
             d.get('categoria','General'), float(d.get('monto',0)),
             d.get('proveedor',''), d.get('comprobante',''))
        )
        rid = cur.lastrowid
    return jsonify({'ok': True, 'id': rid})

@app.route('/api/gastos/<int:gid>', methods=['PUT'])
@login_required
def api_gastos_update(gid):
    d = request.get_json(silent=True) or {}
    with db() as c:
        c.execute(
            "UPDATE gastos SET fecha=?, descripcion=?, categoria=?, monto=?, proveedor=?, comprobante=? WHERE id=?",
            (d.get('fecha', date.today().isoformat()), d.get('descripcion',''),
             d.get('categoria','General'), float(d.get('monto',0)),
             d.get('proveedor',''), d.get('comprobante',''), gid)
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


# ════════════════════════════════════════════════════════════════════════════
# AGENDA
# ════════════════════════════════════════════════════════════════════════════

@app.route('/agenda')
@login_required
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
                "SELECT * FROM agenda ORDER BY fecha DESC, prioridad DESC"
            ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/agenda', methods=['POST'])
@login_required
def api_agenda_create():
    d = request.get_json(silent=True) or {}
    with db() as c:
        cur = c.execute(
            "INSERT INTO agenda (tipo, titulo, descripcion, fecha, hora, completado, prioridad) VALUES (?,?,?,?,?,?,?)",
            (d.get('tipo','tarea'), d.get('titulo',''), d.get('descripcion',''),
             d.get('fecha', date.today().isoformat()), d.get('hora',''),
             0, d.get('prioridad','media'))
        )
        rid = cur.lastrowid
    return jsonify({'ok': True, 'id': rid})

@app.route('/api/agenda/<int:aid>', methods=['PUT'])
@login_required
def api_agenda_update(aid):
    d = request.get_json(silent=True) or {}
    with db() as c:
        c.execute(
            "UPDATE agenda SET tipo=?, titulo=?, descripcion=?, fecha=?, hora=?, prioridad=? WHERE id=?",
            (d.get('tipo','tarea'), d.get('titulo',''), d.get('descripcion',''),
             d.get('fecha', date.today().isoformat()), d.get('hora',''),
             d.get('prioridad','media'), aid)
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
        c.execute("DELETE FROM agenda WHERE id=?", (aid,))
    return jsonify({'ok': True})


# ════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DEL NEGOCIO
# ════════════════════════════════════════════════════════════════════════════

@app.route('/configuracion-negocio')
@login_required
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
            "INSERT INTO agenda (tipo, titulo, descripcion, fecha, hora, prioridad) VALUES (?,?,?,?,?,?)",
            (d.get('tipo','tarea'), d.get('titulo',''), d.get('descripcion',''),
             d.get('fecha', date.today().isoformat()), d.get('hora',''),
             d.get('prioridad','media'))
        )
        rid = cur.lastrowid
    return jsonify({'ok': True, 'id': rid})

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
            d.get('nombre',''),
            d.get('email',''),
            d.get('telefono',''),
            d.get('empresa',''),
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
@login_required
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


# ── Arranque ──────────────────────────────────────────────────────────────────

# Inicializar DB al importar (necesario para gunicorn, además de __main__)
with app.app_context():
    init_db()

if __name__ == '__main__':
    init_db()
    print()
    print("  Aurora Bakers -- Sistema de Ventas")
    print("  Abre:  http://127.0.0.1:5000")
    print("  Cierra con:  Ctrl+C")
    print()
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('HOST', '0.0.0.0')
    app.run(debug=False, host=host, port=port)
