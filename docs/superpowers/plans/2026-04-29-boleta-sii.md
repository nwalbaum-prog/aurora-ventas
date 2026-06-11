# Boleta Electrónica SII — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emitir boletas electrónicas válidas ante el SII (DTE tipo 39) directamente desde el POS de Aurora Bakers.

**Architecture:** Blueprint `blueprints/boleta.py` con tres responsabilidades separadas en funciones puras: generación del XML DTE, firma xmldsig con el certificado .pfx, y envío SOAP al SII. La configuración (certificado, CAF, datos emisor) se almacena en `aurora_config.json`. Los PDFs se generan con reportlab y se guardan en `static/boletas/`.

**Prerequisito:** El plan POS+Promociones (`2026-04-29-pos-promociones.md`) debe estar completamente implementado antes de ejecutar este plan.

**Tech Stack:** Flask Blueprint, lxml (XML DTE), cryptography (leer .pfx), signxml (firma xmldsig), reportlab (PDF), requests (SOAP SII), pytest.

---

## Mapa de archivos

**Crear:**
- `blueprints/boleta.py` — lógica DTE + rutas API
- `templates/pos_boleta_config.html` — sección SII en /crm/configuracion (partial incluido)
- `tests/test_boleta.py` — tests unitarios de generación y firma
- `static/boletas/` — carpeta para PDFs (crear vacía con .gitkeep)

**Modificar:**
- `requirements.txt` — agregar lxml, cryptography, signxml, reportlab, requests
- `app.py` — registrar boleta_bp + llamar init_boleta_tables
- `templates/crm_configuracion.html` — agregar sección SII
- `blueprints/pos.py` — conectar "Cobrar + Emitir boleta" a la API de boleta

---

## Task 1: Instalar dependencias SII

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Actualizar requirements.txt**

```
flask>=3.0
werkzeug>=3.0
gunicorn>=21.0
pytest>=8.0
lxml>=5.0
cryptography>=42.0
signxml>=3.2
reportlab>=4.0
requests>=2.31
```

- [ ] **Step 2: Instalar paquetes**

```bash
cd C:\Users\LENOVO\Documents\aurora-ventas
venv\Scripts\pip install lxml cryptography signxml reportlab requests
```
Expected: `Successfully installed lxml-... cryptography-... signxml-... reportlab-... requests-...`

- [ ] **Step 3: Verificar imports**

```bash
venv\Scripts\python -c "from lxml import etree; from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates; from signxml import XMLSigner; from reportlab.pdfgen import canvas; import requests; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Crear carpeta static/boletas con .gitkeep**

```bash
mkdir static\boletas
echo. > static\boletas\.gitkeep
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt static/boletas/.gitkeep
git commit -m "chore: add SII DTE dependencies (lxml, cryptography, signxml, reportlab)"
```

---

## Task 2: blueprints/boleta.py — estructura base y tabla DB

**Files:**
- Create: `blueprints/boleta.py`
- Create: `tests/test_boleta.py`
- Modify: `app.py`

- [ ] **Step 1: Escribir test que falla**

Crear `tests/test_boleta.py`:
```python
import os, tempfile
os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())

from blueprints.db import db
from blueprints.boleta import init_boleta_tables

def setup_module():
    # Necesita tablas base primero
    import app as a
    from blueprints.pos import init_pos_tables
    from blueprints.promociones import init_promociones_tables
    with a.app.app_context():
        a.init_db()
        init_pos_tables()
        init_promociones_tables()
        init_boleta_tables()

def test_tabla_boletas_existe():
    with db() as c:
        tablas = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert 'boletas_emitidas' in tablas

def test_tabla_boletas_columnas():
    with db() as c:
        cols = [r['name'] for r in c.execute("PRAGMA table_info(boletas_emitidas)").fetchall()]
        for col in ('id','folio','pos_venta_id','monto_total','xml_dte','track_id','estado','pdf_path'):
            assert col in cols, f"Falta columna: {col}"
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
venv\Scripts\pytest tests/test_boleta.py::test_tabla_boletas_existe -v
```
Expected: `ImportError: No module named 'blueprints.boleta'`

- [ ] **Step 3: Crear blueprints/boleta.py con estructura base**

```python
import os
from flask import Blueprint, jsonify, request
from .db import db

boleta_bp = Blueprint('boleta', __name__, url_prefix='/boleta')

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def init_boleta_tables():
    with db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS boletas_emitidas (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                folio          INTEGER NOT NULL,
                pos_venta_id   INTEGER REFERENCES pos_ventas(id),
                rut_receptor   TEXT    NOT NULL DEFAULT '66666666-6',
                monto_neto     INTEGER NOT NULL DEFAULT 0,
                monto_iva      INTEGER NOT NULL DEFAULT 0,
                monto_total    INTEGER NOT NULL DEFAULT 0,
                xml_dte        TEXT    NOT NULL DEFAULT '',
                track_id       TEXT    NOT NULL DEFAULT '',
                estado         TEXT    NOT NULL DEFAULT 'pendiente',
                pdf_path       TEXT    NOT NULL DEFAULT '',
                fecha_emision  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
        """)
```

- [ ] **Step 4: Registrar blueprint en app.py**

Agregar en el bloque `# ── Blueprints ──` de `app.py`:

```python
from blueprints.boleta import boleta_bp, init_boleta_tables
app.register_blueprint(boleta_bp)
```

Y en ambos bloques de init (el `with app.app_context()` y el `if __name__ == '__main__'`):
```python
init_boleta_tables()
```

- [ ] **Step 5: Ejecutar tests**

```bash
venv\Scripts\pytest tests/test_boleta.py -v
```
Expected: `2 passed`

- [ ] **Step 6: Commit**

```bash
git add blueprints/boleta.py tests/test_boleta.py app.py
git commit -m "feat: boleta electrónica blueprint scaffold with DB table"
```

---

## Task 3: Leer certificado .pfx y parsear CAF

**Files:**
- Modify: `blueprints/boleta.py`
- Modify: `tests/test_boleta.py`

- [ ] **Step 1: Escribir tests**

Agregar a `tests/test_boleta.py`:
```python
import pytest

CERT_PATH = r'C:\Users\LENOVO\Downloads\17704304-4.pfx'
CERT_PASS = b'1991'

def test_cargar_certificado_pfx():
    from blueprints.boleta import cargar_certificado
    if not os.path.exists(CERT_PATH):
        pytest.skip("Certificado no disponible en este entorno")
    clave, cert, chain = cargar_certificado(CERT_PATH, CERT_PASS)
    assert clave is not None
    assert cert is not None

def test_parsear_caf_xml():
    from blueprints.boleta import parsear_caf
    # CAF mínimo de ejemplo para test (estructura real SII)
    caf_xml = b"""<?xml version="1.0"?>
<AUTORIZACION>
  <CAF version="1.0">
    <DA>
      <RE>17704304-4</RE>
      <RS>AURORA BAKERS</RS>
      <TD>39</TD>
      <RNG><D>1</D><H>100</H></RNG>
      <FA>2024-01-01</FA>
      <RSAPK><M>ABC123</M><E>AQAB</E></RSAPK>
      <IDK>100</IDK>
    </DA>
    <FRMT algoritmo="SHA1withRSA">FIRMA_CAF</FRMT>
  </CAF>
</AUTORIZACION>"""
    info = parsear_caf(caf_xml)
    assert info['rut_emisor'] == '17704304-4'
    assert info['tipo_dte'] == 39
    assert info['folio_desde'] == 1
    assert info['folio_hasta'] == 100

def test_proximo_folio_inicial():
    from blueprints.boleta import proximo_folio
    with db() as c:
        folio = proximo_folio(c, folio_desde=1)
    assert folio == 1

def test_proximo_folio_incrementa():
    from blueprints.boleta import proximo_folio
    with db() as c:
        c.execute("INSERT INTO boletas_emitidas (folio,monto_total) VALUES (?,?)", (1, 1000))
        c.execute("INSERT INTO boletas_emitidas (folio,monto_total) VALUES (?,?)", (2, 2000))
        folio = proximo_folio(c, folio_desde=1)
    assert folio == 3
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

```bash
venv\Scripts\pytest tests/test_boleta.py -v
```
Expected: `ImportError: cannot import name 'cargar_certificado'`

- [ ] **Step 3: Implementar cargar_certificado, parsear_caf y proximo_folio en blueprints/boleta.py**

Agregar estas funciones al archivo `blueprints/boleta.py`:

```python
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
from lxml import etree


def cargar_certificado(pfx_path: str, password: bytes):
    """Lee un .pfx y retorna (private_key, certificate, ca_chain)."""
    with open(pfx_path, 'rb') as f:
        data = f.read()
    clave, cert, chain = load_key_and_certificates(data, password)
    return clave, cert, chain


def parsear_caf(caf_bytes: bytes) -> dict:
    """Extrae metadata del archivo CAF XML del SII."""
    root = etree.fromstring(caf_bytes)
    da = root.find('.//DA')
    return {
        'rut_emisor':  da.findtext('RE'),
        'razon_social':da.findtext('RS'),
        'tipo_dte':    int(da.findtext('TD')),
        'folio_desde': int(da.find('RNG/D').text),
        'folio_hasta': int(da.find('RNG/H').text),
        'fecha_auth':  da.findtext('FA'),
        'caf_xml':     etree.tostring(root.find('.//CAF'), encoding='unicode'),
    }


def proximo_folio(c, folio_desde: int) -> int:
    """Retorna el próximo folio disponible (último usado + 1, mínimo folio_desde)."""
    row = c.execute(
        "SELECT MAX(folio) as ultimo FROM boletas_emitidas WHERE estado != 'anulada'"
    ).fetchone()
    ultimo = row['ultimo'] or 0
    return max(folio_desde, ultimo + 1)
```

- [ ] **Step 4: Ejecutar tests**

```bash
venv\Scripts\pytest tests/test_boleta.py -v
```
Expected: `test_cargar_certificado_pfx` pasa si el .pfx existe, los demás pasan todos.

- [ ] **Step 5: Commit**

```bash
git add blueprints/boleta.py tests/test_boleta.py
git commit -m "feat: certificate loading, CAF parsing, and folio management"
```

---

## Task 4: Generar XML DTE tipo 39

**Files:**
- Modify: `blueprints/boleta.py`
- Modify: `tests/test_boleta.py`

El XML DTE tipo 39 tiene esta estructura (encoding ISO-8859-1 requerido por SII):

```
<DTE version="1.0">
  <Documento ID="F000001T039">
    <Encabezado>
      <IdDoc>
        <TipoDTE>39</TipoDTE>
        <Folio>1</Folio>
        <FchEmis>2024-01-15</FchEmis>
        <IndServicio>3</IndServicio>
        <MntBruto>1</MntBruto>
      </IdDoc>
      <Emisor>
        <RUTEmisor>17704304-4</RUTEmisor>
        <RznSoc>Aurora Bakers</RznSoc>
        <GiroEmis>Panaderia y Pasteleria</GiroEmis>
        <DirOrigen>Direccion Local</DirOrigen>
        <CmnaOrigen>Santiago</CmnaOrigen>
      </Emisor>
      <Totales>
        <MntTotal>1000</MntTotal>
      </Totales>
    </Encabezado>
    <Detalle>
      <NroLinDet>1</NroLinDet>
      <NmbItem>Pan de molde</NmbItem>
      <QtyItem>2.00</QtyItem>
      <UnmdItem>UN</UnmdItem>
      <PrcItem>500</PrcItem>
      <MontoItem>1000</MontoItem>
    </Detalle>
  </Documento>
</DTE>
```

- [ ] **Step 1: Escribir tests**

Agregar a `tests/test_boleta.py`:
```python
def test_generar_xml_dte():
    from blueprints.boleta import generar_xml_dte
    emisor = {
        'rut': '17704304-4',
        'razon_social': 'Aurora Bakers',
        'giro': 'Panaderia',
        'direccion': 'Calle 123',
        'comuna': 'Santiago',
    }
    items = [
        {'nombre': 'Pan de molde', 'cantidad': 2, 'precio': 500, 'subtotal': 1000},
    ]
    xml_bytes = generar_xml_dte(
        folio=1,
        fecha='2024-01-15',
        emisor=emisor,
        items=items,
        total=1000,
        rut_receptor='66666666-6',
    )
    root = etree.fromstring(xml_bytes)
    assert root.find('.//TipoDTE').text == '39'
    assert root.find('.//Folio').text == '1'
    assert root.find('.//RUTEmisor').text == '17704304-4'
    assert root.find('.//MntTotal').text == '1000'
    assert root.find('.//NmbItem').text == 'Pan de molde'
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
venv\Scripts\pytest tests/test_boleta.py::test_generar_xml_dte -v
```
Expected: `ImportError: cannot import name 'generar_xml_dte'`

- [ ] **Step 3: Implementar generar_xml_dte en blueprints/boleta.py**

Agregar a `blueprints/boleta.py`:

```python
def generar_xml_dte(folio: int, fecha: str, emisor: dict,
                    items: list, total: int,
                    rut_receptor: str = '66666666-6') -> bytes:
    """Genera el XML DTE tipo 39 sin firmar."""
    doc_id = f"F{folio:09d}T039"

    dte = etree.Element('DTE', version='1.0')
    documento = etree.SubElement(dte, 'Documento', ID=doc_id)

    # Encabezado
    enc = etree.SubElement(documento, 'Encabezado')
    iddoc = etree.SubElement(enc, 'IdDoc')
    etree.SubElement(iddoc, 'TipoDTE').text = '39'
    etree.SubElement(iddoc, 'Folio').text   = str(folio)
    etree.SubElement(iddoc, 'FchEmis').text = fecha
    etree.SubElement(iddoc, 'IndServicio').text = '3'
    etree.SubElement(iddoc, 'MntBruto').text    = '1'

    em = etree.SubElement(enc, 'Emisor')
    etree.SubElement(em, 'RUTEmisor').text = emisor['rut']
    etree.SubElement(em, 'RznSoc').text    = emisor['razon_social']
    etree.SubElement(em, 'GiroEmis').text  = emisor.get('giro', '')
    etree.SubElement(em, 'DirOrigen').text = emisor.get('direccion', '')
    etree.SubElement(em, 'CmnaOrigen').text = emisor.get('comuna', '')

    rec = etree.SubElement(enc, 'Receptor')
    etree.SubElement(rec, 'RUTRecep').text = rut_receptor
    etree.SubElement(rec, 'RznSocRecep').text = 'Sin Nombre'

    tots = etree.SubElement(enc, 'Totales')
    etree.SubElement(tots, 'MntTotal').text = str(total)

    # Detalles
    for i, item in enumerate(items, 1):
        det = etree.SubElement(documento, 'Detalle')
        etree.SubElement(det, 'NroLinDet').text  = str(i)
        etree.SubElement(det, 'NmbItem').text    = item['nombre']
        etree.SubElement(det, 'QtyItem').text    = f"{float(item['cantidad']):.2f}"
        etree.SubElement(det, 'UnmdItem').text   = 'UN'
        etree.SubElement(det, 'PrcItem').text    = str(int(item['precio']))
        etree.SubElement(det, 'MontoItem').text  = str(int(item['subtotal']))

    return etree.tostring(dte, encoding='ISO-8859-1', xml_declaration=True)
```

- [ ] **Step 4: Ejecutar tests**

```bash
venv\Scripts\pytest tests/test_boleta.py -v
```
Expected: todos los tests hasta aquí pasan.

- [ ] **Step 5: Commit**

```bash
git add blueprints/boleta.py tests/test_boleta.py
git commit -m "feat: DTE tipo 39 XML generation"
```

---

## Task 5: Firmar XML con certificado (xmldsig)

**Files:**
- Modify: `blueprints/boleta.py`
- Modify: `tests/test_boleta.py`

El SII requiere que el DTE esté firmado con el estándar XMLDSig, firmando el elemento `<Documento>` referenciado por su atributo `ID`.

- [ ] **Step 1: Escribir test**

Agregar a `tests/test_boleta.py`:
```python
def test_firmar_xml_dte():
    from blueprints.boleta import generar_xml_dte, firmar_xml_dte
    if not os.path.exists(CERT_PATH):
        pytest.skip("Certificado no disponible")

    emisor = {
        'rut': '17704304-4', 'razon_social': 'Aurora Bakers',
        'giro': 'Panaderia', 'direccion': 'Calle 123', 'comuna': 'Santiago',
    }
    xml_sin_firma = generar_xml_dte(
        folio=1, fecha='2024-01-15', emisor=emisor,
        items=[{'nombre': 'Pan', 'cantidad': 1, 'precio': 1000, 'subtotal': 1000}],
        total=1000,
    )
    xml_firmado = firmar_xml_dte(xml_sin_firma, CERT_PATH, CERT_PASS)
    assert b'<Signature' in xml_firmado
    root = etree.fromstring(xml_firmado)
    assert root.find('.//{http://www.w3.org/2000/09/xmldsig#}Signature') is not None
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
venv\Scripts\pytest tests/test_boleta.py::test_firmar_xml_dte -v
```
Expected: `ImportError: cannot import name 'firmar_xml_dte'`

- [ ] **Step 3: Implementar firmar_xml_dte en blueprints/boleta.py**

Agregar a `blueprints/boleta.py`:

```python
from cryptography.hazmat.primitives import serialization
from cryptography.x509 import Certificate
from signxml import XMLSigner, methods


def firmar_xml_dte(xml_bytes: bytes, pfx_path: str, password: bytes) -> bytes:
    """Firma el XML DTE con el certificado .pfx usando xmldsig."""
    clave, cert, chain = cargar_certificado(pfx_path, password)

    # Exportar clave privada a PEM
    pem_key = clave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # Exportar certificado a PEM
    pem_cert = cert.public_bytes(serialization.Encoding.PEM)

    root = etree.fromstring(xml_bytes)

    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm='rsa-sha1',
        digest_algorithm='sha1',
        c14n_algorithm='http://www.w3.org/TR/2001/REC-xml-c14n-20010315',
    )

    # El SII requiere que se firme el elemento Documento referenciado por ID
    doc_id = root.find('.//Documento').get('ID')
    signed_root = signer.sign(
        root,
        key=pem_key,
        cert=pem_cert,
        reference_uri=f'#{doc_id}',
    )

    return etree.tostring(signed_root, encoding='ISO-8859-1', xml_declaration=True)
```

- [ ] **Step 4: Ejecutar tests**

```bash
venv\Scripts\pytest tests/test_boleta.py -v
```
Expected: `test_firmar_xml_dte` pasa si el certificado está disponible.

- [ ] **Step 5: Commit**

```bash
git add blueprints/boleta.py tests/test_boleta.py
git commit -m "feat: XML DTE signing with xmldsig using .pfx certificate"
```

---

## Task 6: Envío al SII y seguimiento de estado

**Files:**
- Modify: `blueprints/boleta.py`
- Modify: `tests/test_boleta.py`

El SII recibe el DTE envuelto en un `<EnvioBOLETA>` con los datos del emisor, firmado también. El endpoint es:
- Certificación: `https://maullin.sii.cl/cgi_dte/UPL/DTEUpload`
- Producción:    `https://palena.sii.cl/cgi_dte/UPL/DTEUpload`

La respuesta es un XML con un `<TRACKID>`.

- [ ] **Step 1: Escribir test (usa mock para no conectar al SII real)**

Agregar a `tests/test_boleta.py`:
```python
from unittest.mock import patch, MagicMock

def test_crear_envio_boleta_xml():
    from blueprints.boleta import crear_envio_boleta
    if not os.path.exists(CERT_PATH):
        pytest.skip("Certificado no disponible")

    caf_xml = b"""<?xml version="1.0"?>
<AUTORIZACION><CAF version="1.0"><DA>
  <RE>17704304-4</RE><RS>AURORA</RS><TD>39</TD>
  <RNG><D>1</D><H>100</H></RNG><FA>2024-01-01</FA>
  <RSAPK><M>ABC</M><E>AQAB</E></RSAPK><IDK>100</IDK>
</DA><FRMT algoritmo="SHA1withRSA">SIG</FRMT></CAF></AUTORIZACION>"""

    emisor = {'rut': '17704304-4', 'razon_social': 'Aurora Bakers',
               'giro': 'Panaderia', 'direccion': 'Calle 123', 'comuna': 'Santiago'}

    from blueprints.boleta import generar_xml_dte, firmar_xml_dte
    xml = generar_xml_dte(1, '2024-01-15', emisor,
                          [{'nombre': 'Pan', 'cantidad': 1, 'precio': 1000, 'subtotal': 1000}], 1000)
    xml_firmado = firmar_xml_dte(xml, CERT_PATH, CERT_PASS)
    envio = crear_envio_boleta(xml_firmado, emisor, CERT_PATH, CERT_PASS)
    assert b'<EnvioBOLETA' in envio or b'<EnvioDTE' in envio

def test_enviar_al_sii_mock():
    from blueprints.boleta import enviar_al_sii
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '<RECEPCIONDTE><TRACKID>12345</TRACKID><STATUS>0</STATUS></RECEPCIONDTE>'

    with patch('blueprints.boleta.requests.post', return_value=mock_response):
        track_id, error = enviar_al_sii(b'<xml/>', '17704304-4', ambiente='certificacion')
    assert track_id == '12345'
    assert error == ''
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
venv\Scripts\pytest tests/test_boleta.py::test_enviar_al_sii_mock -v
```
Expected: `ImportError`

- [ ] **Step 3: Implementar crear_envio_boleta y enviar_al_sii en blueprints/boleta.py**

Agregar a `blueprints/boleta.py`:

```python
import requests as _requests
from datetime import datetime


SII_URLS = {
    'certificacion': 'https://maullin.sii.cl/cgi_dte/UPL/DTEUpload',
    'produccion':    'https://palena.sii.cl/cgi_dte/UPL/DTEUpload',
}


def crear_envio_boleta(xml_dte_firmado: bytes, emisor: dict,
                       pfx_path: str, password: bytes) -> bytes:
    """Envuelve el DTE firmado en un EnvioBOLETA y lo firma."""
    dte_root = etree.fromstring(xml_dte_firmado)
    ahora = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

    envio = etree.Element('EnvioBOLETA',
                          version='1.0',
                          xmlns='http://www.sii.cl/SiiDte')
    set_dte = etree.SubElement(envio, 'SetDTE', ID='SetDoc')
    caratula = etree.SubElement(set_dte, 'Caratula', version='1.0')
    etree.SubElement(caratula, 'RutEmisor').text    = emisor['rut']
    etree.SubElement(caratula, 'RutEnvia').text     = emisor['rut']
    etree.SubElement(caratula, 'RutReceptor').text  = '60803000-K'  # SII
    etree.SubElement(caratula, 'FchResol').text     = '2024-01-01'
    etree.SubElement(caratula, 'NroResol').text     = '0'
    etree.SubElement(caratula, 'TmstFirmaEnv').text = ahora
    etree.SubElement(caratula, 'SubTotDTE',
                     TpoDTE='39', NroDTE='1')
    set_dte.append(dte_root)

    # Firmar el SetDTE
    clave, cert, _ = cargar_certificado(pfx_path, password)
    pem_key  = clave.private_bytes(serialization.Encoding.PEM,
                                   serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption())
    pem_cert = cert.public_bytes(serialization.Encoding.PEM)

    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm='rsa-sha1',
        digest_algorithm='sha1',
        c14n_algorithm='http://www.w3.org/TR/2001/REC-xml-c14n-20010315',
    )
    envio_firmado = signer.sign(envio, key=pem_key, cert=pem_cert,
                                reference_uri='#SetDoc')
    return etree.tostring(envio_firmado, encoding='ISO-8859-1', xml_declaration=True)


def enviar_al_sii(envio_bytes: bytes, rut_emisor: str,
                  ambiente: str = 'certificacion') -> tuple:
    """Envía el EnvioBOLETA al SII. Retorna (track_id, error_msg)."""
    url = SII_URLS.get(ambiente, SII_URLS['certificacion'])
    rut_sin_dv = rut_emisor.split('-')[0].replace('.', '')

    try:
        r = _requests.post(
            url,
            files={'archivo': ('envio.xml', envio_bytes, 'application/xml')},
            data={'RUT_SENDER': rut_sin_dv, 'RUT_COMPANY': rut_sin_dv},
            timeout=30,
        )
        root = etree.fromstring(r.text.encode('utf-8'))
        status = root.findtext('.//STATUS') or root.findtext('.//ESTADO') or '-1'
        if status == '0':
            track_id = root.findtext('.//TRACKID') or ''
            return track_id, ''
        else:
            glosa = root.findtext('.//GLOSA') or root.findtext('.//DESCRIPCION') or f'Estado SII: {status}'
            return '', glosa
    except Exception as e:
        return '', str(e)
```

- [ ] **Step 4: Ejecutar tests**

```bash
venv\Scripts\pytest tests/test_boleta.py -v
```
Expected: todos los tests pasan (los que requieren certificado se saltan si no está disponible).

- [ ] **Step 5: Commit**

```bash
git add blueprints/boleta.py tests/test_boleta.py
git commit -m "feat: SII submission with EnvioBOLETA envelope and SOAP upload"
```

---

## Task 7: Generar PDF de boleta con reportlab

**Files:**
- Modify: `blueprints/boleta.py`
- Modify: `tests/test_boleta.py`

- [ ] **Step 1: Escribir test**

Agregar a `tests/test_boleta.py`:
```python
import tempfile

def test_generar_pdf_boleta():
    from blueprints.boleta import generar_pdf_boleta
    items = [
        {'nombre': 'Pan de molde', 'cantidad': 2, 'precio': 500, 'subtotal': 1000},
        {'nombre': 'Hallulla', 'cantidad': 1, 'precio': 300, 'subtotal': 300},
    ]
    emisor = {
        'rut': '17704304-4', 'razon_social': 'Aurora Bakers',
        'giro': 'Panaderia', 'direccion': 'Calle 123', 'comuna': 'Santiago',
    }
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
        pdf_path = f.name

    generar_pdf_boleta(
        path=pdf_path,
        folio=1,
        fecha='2024-01-15',
        emisor=emisor,
        items=items,
        total=1300,
    )
    assert os.path.exists(pdf_path)
    assert os.path.getsize(pdf_path) > 1000  # tiene contenido
    os.unlink(pdf_path)
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
venv\Scripts\pytest tests/test_boleta.py::test_generar_pdf_boleta -v
```
Expected: `ImportError: cannot import name 'generar_pdf_boleta'`

- [ ] **Step 3: Implementar generar_pdf_boleta en blueprints/boleta.py**

Agregar a `blueprints/boleta.py`:

```python
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib import colors


def generar_pdf_boleta(path: str, folio: int, fecha: str,
                       emisor: dict, items: list, total: int,
                       track_id: str = '') -> None:
    """Genera un PDF de boleta electrónica con los datos del DTE."""
    ancho, alto = 80 * mm, 297 * mm  # ancho ticket impresora térmica
    c = rl_canvas.Canvas(path, pagesize=(ancho, alto))

    y = alto - 10 * mm

    def write(texto, size=8, bold=False, center=False):
        nonlocal y
        c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
        if center:
            c.drawCentredString(ancho / 2, y, texto)
        else:
            c.drawString(5 * mm, y, texto)
        y -= (size + 2)

    def linea():
        nonlocal y
        c.setStrokeColor(colors.grey)
        c.line(3 * mm, y, ancho - 3 * mm, y)
        y -= 4

    write(emisor.get('razon_social', 'Aurora Bakers'), size=11, bold=True, center=True)
    write(f"RUT: {emisor.get('rut', '')}", center=True)
    write(emisor.get('giro', ''), size=7, center=True)
    write(emisor.get('direccion', ''), size=7, center=True)
    linea()
    write('BOLETA ELECTRÓNICA', size=10, bold=True, center=True)
    write(f'N° {folio}', size=9, bold=True, center=True)
    write(f'Fecha: {fecha}', size=7, center=True)
    linea()

    for item in items:
        write(item['nombre'][:28], size=8)
        desc = f"  {item['cantidad']} x ${int(item['precio']):,}".replace(',', '.')
        precio_str = f"${int(item['subtotal']):,}".replace(',', '.')
        c.setFont('Helvetica', 7)
        c.drawString(5 * mm, y, desc)
        c.drawRightString(ancho - 3 * mm, y, precio_str)
        y -= 9

    linea()
    c.setFont('Helvetica-Bold', 10)
    c.drawString(5 * mm, y, 'TOTAL')
    c.drawRightString(ancho - 3 * mm, y, f"${int(total):,}".replace(',', '.'))
    y -= 14

    if track_id:
        linea()
        write(f'Track ID SII: {track_id}', size=6, center=True)

    write('', size=6)
    write('Documento tributario electrónico', size=6, center=True)
    write('válido ante el SII', size=6, center=True)

    c.save()
```

- [ ] **Step 4: Ejecutar tests**

```bash
venv\Scripts\pytest tests/test_boleta.py -v
```
Expected: todos los tests pasan.

- [ ] **Step 5: Commit**

```bash
git add blueprints/boleta.py tests/test_boleta.py
git commit -m "feat: PDF receipt generation with reportlab (80mm ticket format)"
```

---

## Task 8: API de emisión y configuración SII

**Files:**
- Modify: `blueprints/boleta.py` (agregar rutas API)
- Modify: `templates/crm_configuracion.html` (agregar sección SII)

- [ ] **Step 1: Agregar rutas a blueprints/boleta.py**

Agregar al final de `blueprints/boleta.py`:

```python
import json as _json


def _sii_cfg() -> dict:
    """Lee config SII desde aurora_config.json."""
    try:
        cfg_path = os.path.join(_BASE_DIR, 'aurora_config.json')
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = _json.load(f)
    except Exception:
        cfg = {}
    return {
        'pfx_path':    cfg.get('sii_pfx_path', os.path.join(_BASE_DIR, 'certificados', '17704304-4.pfx')),
        'pfx_pass':    cfg.get('sii_pfx_pass', '').encode('utf-8'),
        'caf_path':    cfg.get('sii_caf_path', os.path.join(_BASE_DIR, 'certificados', 'caf_39.xml')),
        'rut':         cfg.get('sii_rut', ''),
        'razon_social':cfg.get('sii_razon_social', ''),
        'giro':        cfg.get('sii_giro', ''),
        'direccion':   cfg.get('sii_direccion', ''),
        'comuna':      cfg.get('sii_comuna', ''),
        'ambiente':    cfg.get('sii_ambiente', 'certificacion'),
    }


def _save_sii_cfg(data: dict):
    cfg_path = os.path.join(_BASE_DIR, 'aurora_config.json')
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = _json.load(f)
    except Exception:
        cfg = {}
    cfg.update(data)
    with open(cfg_path, 'w', encoding='utf-8') as f:
        _json.dump(cfg, f, indent=2, ensure_ascii=False)


@boleta_bp.route('/api/boleta/estado-config')
def api_estado_config():
    cfg = _sii_cfg()
    tiene_cert = os.path.exists(cfg['pfx_path'])
    tiene_caf  = os.path.exists(cfg['caf_path'])
    folios_restantes = None
    caf_info = None

    if tiene_caf:
        try:
            with open(cfg['caf_path'], 'rb') as f:
                info = parsear_caf(f.read())
            caf_info = info
            with db() as c:
                proximo = proximo_folio(c, info['folio_desde'])
                folios_restantes = info['folio_hasta'] - proximo + 1
        except Exception as e:
            caf_info = {'error': str(e)}

    return jsonify({
        'tiene_cert':        tiene_cert,
        'tiene_caf':         tiene_caf,
        'folios_restantes':  folios_restantes,
        'caf_info':          caf_info,
        'rut':               cfg['rut'],
        'razon_social':      cfg['razon_social'],
        'ambiente':          cfg['ambiente'],
    })


@boleta_bp.route('/api/boleta/config', methods=['POST'])
def api_guardar_config():
    d = request.get_json(force=True)
    _save_sii_cfg({
        'sii_rut':          d.get('rut', ''),
        'sii_razon_social': d.get('razon_social', ''),
        'sii_giro':         d.get('giro', ''),
        'sii_direccion':    d.get('direccion', ''),
        'sii_comuna':       d.get('comuna', ''),
        'sii_pfx_path':     d.get('pfx_path', ''),
        'sii_pfx_pass':     d.get('pfx_pass', ''),
        'sii_ambiente':     d.get('ambiente', 'certificacion'),
    })
    return jsonify({'ok': True})


@boleta_bp.route('/api/boleta/subir-caf', methods=['POST'])
def api_subir_caf():
    if 'caf' not in request.files:
        return jsonify({'error': 'No se recibió archivo'}), 400
    f = request.files['caf']
    os.makedirs(os.path.join(_BASE_DIR, 'certificados'), exist_ok=True)
    ruta = os.path.join(_BASE_DIR, 'certificados', 'caf_39.xml')
    f.save(ruta)
    try:
        with open(ruta, 'rb') as fh:
            info = parsear_caf(fh.read())
        _save_sii_cfg({'sii_caf_path': ruta})
        return jsonify({'ok': True, 'folio_desde': info['folio_desde'],
                        'folio_hasta': info['folio_hasta']})
    except Exception as e:
        return jsonify({'error': f'CAF inválido: {e}'}), 400


@boleta_bp.route('/api/boleta/emitir', methods=['POST'])
def api_emitir():
    """Emite una boleta electrónica para una venta POS existente."""
    d = request.get_json(force=True)
    pos_venta_id = d.get('pos_venta_id')
    rut_receptor = d.get('rut_receptor', '66666666-6')

    cfg = _sii_cfg()

    if not cfg['rut']:
        return jsonify({'error': 'Configura los datos SII primero'}), 400
    if not os.path.exists(cfg['pfx_path']):
        return jsonify({'error': 'Certificado .pfx no encontrado'}), 400
    if not os.path.exists(cfg['caf_path']):
        return jsonify({'error': 'CAF no cargado — descárgalo desde SII y súbelo en configuración'}), 400

    with db() as c:
        venta = c.execute("SELECT * FROM pos_ventas WHERE id=?", (pos_venta_id,)).fetchone()
        if not venta:
            return jsonify({'error': 'Venta no encontrada'}), 404

        items_db = c.execute("""
            SELECT pvi.*, p.nombre
            FROM pos_venta_items pvi
            JOIN productos p ON pvi.producto_id = p.id
            WHERE pvi.venta_id=?
        """, (pos_venta_id,)).fetchall()

        with open(cfg['caf_path'], 'rb') as f:
            caf_info = parsear_caf(f.read())

        folio = proximo_folio(c, caf_info['folio_desde'])
        if folio > caf_info['folio_hasta']:
            return jsonify({'error': 'Folios agotados — solicita más en el portal SII'}), 400

        from datetime import date
        fecha_hoy = date.today().isoformat()
        emisor = {
            'rut':          cfg['rut'],
            'razon_social': cfg['razon_social'],
            'giro':         cfg['giro'],
            'direccion':    cfg['direccion'],
            'comuna':       cfg['comuna'],
        }
        items = [{'nombre': it['nombre'], 'cantidad': it['cantidad'],
                  'precio': it['precio_unitario'], 'subtotal': it['subtotal']}
                 for it in items_db]
        total = venta['total']

        xml_sin_firma = generar_xml_dte(folio, fecha_hoy, emisor, items, total, rut_receptor)
        xml_firmado   = firmar_xml_dte(xml_sin_firma, cfg['pfx_path'], cfg['pfx_pass'])
        envio         = crear_envio_boleta(xml_firmado, emisor, cfg['pfx_path'], cfg['pfx_pass'])
        track_id, error = enviar_al_sii(envio, cfg['rut'], cfg['ambiente'])

        os.makedirs(os.path.join(_BASE_DIR, 'static', 'boletas'), exist_ok=True)
        pdf_path = os.path.join(_BASE_DIR, 'static', 'boletas', f'boleta_{folio}.pdf')
        generar_pdf_boleta(pdf_path, folio, fecha_hoy, emisor, items, total, track_id)

        estado = 'enviada' if track_id else 'pendiente'
        cur = c.execute("""
            INSERT INTO boletas_emitidas
              (folio, pos_venta_id, rut_receptor, monto_total,
               xml_dte, track_id, estado, pdf_path)
            VALUES (?,?,?,?,?,?,?,?)
        """, (folio, pos_venta_id, rut_receptor, total,
              xml_firmado.decode('iso-8859-1'), track_id, estado,
              f'/static/boletas/boleta_{folio}.pdf'))

        c.execute("UPDATE pos_ventas SET boleta_id=? WHERE id=?",
                  (cur.lastrowid, pos_venta_id))

    return jsonify({
        'ok': True,
        'folio': folio,
        'track_id': track_id,
        'error_sii': error,
        'pdf_url': f'/static/boletas/boleta_{folio}.pdf',
        'estado': estado,
    })
```

- [ ] **Step 2: Verificar que el servidor arranca sin errores**

```bash
venv\Scripts\python app.py
```
Expected: sin errores, servidor en http://127.0.0.1:5000

- [ ] **Step 3: Agregar sección SII en templates/crm_configuracion.html**

Abrir `templates/crm_configuracion.html` y agregar esta sección antes del cierre del `{% block content %}`:

```html
<!-- ── Sección SII ─────────────────────────────────────────────────────────── -->
<div class="card" style="margin-top:1.5rem">
  <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">
    <h3><i class="bi bi-file-earmark-text"></i> Boleta Electrónica SII</h3>
    <span id="sii-estado-badge"></span>
  </div>
  <div class="card-body">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">
      <div class="form-group">
        <label>RUT Emisor</label>
        <input type="text" id="sii-rut" class="form-control" placeholder="17704304-4">
      </div>
      <div class="form-group">
        <label>Razón Social</label>
        <input type="text" id="sii-razon" class="form-control" placeholder="Aurora Bakers">
      </div>
      <div class="form-group">
        <label>Giro</label>
        <input type="text" id="sii-giro" class="form-control" placeholder="Panaderia y Pasteleria">
      </div>
      <div class="form-group">
        <label>Dirección</label>
        <input type="text" id="sii-dir" class="form-control">
      </div>
      <div class="form-group">
        <label>Comuna</label>
        <input type="text" id="sii-comuna" class="form-control">
      </div>
      <div class="form-group">
        <label>Ambiente</label>
        <select id="sii-ambiente" class="form-control">
          <option value="certificacion">Certificación (pruebas SII)</option>
          <option value="produccion">Producción</option>
        </select>
      </div>
      <div class="form-group">
        <label>Certificado .pfx (ruta en servidor)</label>
        <input type="text" id="sii-pfx-path" class="form-control"
          placeholder="C:\Users\LENOVO\Downloads\17704304-4.pfx">
      </div>
      <div class="form-group">
        <label>Contraseña certificado</label>
        <input type="password" id="sii-pfx-pass" class="form-control">
      </div>
    </div>
    <button class="btn btn-primary" onclick="guardarConfigSII()" style="margin-top:.75rem">
      Guardar configuración SII
    </button>

    <hr style="margin:1.5rem 0">

    <div style="display:flex;align-items:center;gap:1rem;flex-wrap:wrap">
      <div>
        <div style="font-size:.85rem;font-weight:600;margin-bottom:.4rem">
          <i class="bi bi-file-earmark-code"></i> CAF (Folios autorizados)
        </div>
        <div id="caf-info" style="font-size:.82rem;color:var(--text-3)">Cargando...</div>
      </div>
      <div>
        <label class="btn btn-secondary" style="cursor:pointer">
          <i class="bi bi-upload"></i> Subir CAF (.xml)
          <input type="file" accept=".xml" onchange="subirCAF(this)" style="display:none">
        </label>
      </div>
    </div>
  </div>
</div>

<script>
async function cargarEstadoSII() {
  const data = await api('/api/boleta/estado-config');
  document.getElementById('sii-rut').value      = data.rut || '';
  document.getElementById('sii-razon').value    = data.razon_social || '';
  document.getElementById('sii-ambiente').value = data.ambiente || 'certificacion';

  const badge = document.getElementById('sii-estado-badge');
  if (data.tiene_cert && data.tiene_caf) {
    badge.innerHTML = '<span class="badge" style="background:var(--success-bg);color:var(--success)"><i class="bi bi-check-circle"></i> Listo</span>';
  } else {
    const faltantes = [];
    if (!data.tiene_cert) faltantes.push('certificado');
    if (!data.tiene_caf)  faltantes.push('CAF');
    badge.innerHTML = `<span class="badge" style="background:var(--warning-bg);color:var(--warning)">Falta: ${faltantes.join(', ')}</span>`;
  }

  const cafEl = document.getElementById('caf-info');
  if (data.caf_info && !data.caf_info.error) {
    cafEl.innerHTML = `Folios ${data.caf_info.folio_desde}–${data.caf_info.folio_hasta} · <strong>${data.folios_restantes}</strong> disponibles`;
    if (data.folios_restantes < 10) {
      cafEl.innerHTML += ' <span style="color:var(--danger);font-weight:600">⚠ Solicitar más folios en SII</span>';
    }
  } else if (data.tiene_caf) {
    cafEl.textContent = `Error leyendo CAF: ${data.caf_info?.error || 'desconocido'}`;
  } else {
    cafEl.textContent = 'Sin CAF — descarga el archivo en sii.cl → Servicios Online → Factura Electrónica → Solicitar Folios → Tipo 39';
  }
}

async function guardarConfigSII() {
  await api('/api/boleta/config', {
    method: 'POST',
    body: JSON.stringify({
      rut:          document.getElementById('sii-rut').value,
      razon_social: document.getElementById('sii-razon').value,
      giro:         document.getElementById('sii-giro').value,
      direccion:    document.getElementById('sii-dir').value,
      comuna:       document.getElementById('sii-comuna').value,
      ambiente:     document.getElementById('sii-ambiente').value,
      pfx_path:     document.getElementById('sii-pfx-path').value,
      pfx_pass:     document.getElementById('sii-pfx-pass').value,
    })
  });
  toast('Configuración SII guardada');
  cargarEstadoSII();
}

async function subirCAF(input) {
  const form = new FormData();
  form.append('caf', input.files[0]);
  const r = await fetch('/api/boleta/subir-caf', { method: 'POST', body: form });
  const data = await r.json();
  if (data.ok) {
    toast(`CAF cargado — folios ${data.folio_desde} a ${data.folio_hasta}`);
    cargarEstadoSII();
  } else {
    toast(data.error || 'Error al subir CAF', 'error');
  }
}

// Cargar estado SII cuando se renderiza la página de configuración
cargarEstadoSII();
</script>
```

- [ ] **Step 4: Verificar en el navegador**

Ir a `http://127.0.0.1:5000/crm/configuracion`
Expected: nueva sección "Boleta Electrónica SII" visible con campos de configuración y estado del CAF.

- [ ] **Step 5: Commit**

```bash
git add blueprints/boleta.py templates/crm_configuracion.html
git commit -m "feat: SII boleta emission API and configuration UI"
```

---

## Task 9: Conectar "Emitir boleta" en el POS

**Files:**
- Modify: `blueprints/pos.py` (actualizar ruta `/api/pos/venta` para soportar boleta)
- Modify: `templates/pos.html` (activar botón "Cobrar + Emitir boleta")

- [ ] **Step 1: Actualizar función cobrar() en pos.html para llamar a la API de boleta**

En `templates/pos.html`, buscar el comentario `// Plan 2: emitir boleta` en la función `cobrar()` y reemplazarlo:

```javascript
  if (conBoleta && data.id) {
    // Llamar API de boleta
    const br = await fetch('/api/boleta/emitir', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ pos_venta_id: data.id, rut_receptor: '66666666-6' })
    });
    const bd = await br.json();
    if (bd.ok) {
      toast(`Boleta N°${bd.folio} emitida${bd.track_id ? ' · Track: '+bd.track_id : ''}`);
      if (bd.pdf_url) {
        window.open(bd.pdf_url, '_blank');
      }
    } else {
      toast(`Venta registrada pero error en boleta: ${bd.error}`, 'error');
    }
  } else {
    toast(`Venta registrada · Total ${fmt(data.total)} · Vuelto ${fmt(data.vuelto)}`);
  }
```

- [ ] **Step 2: Verificar flujo completo en el navegador**

Pre-requisito: configurar SII en `/crm/configuracion` con ambiente "certificacion", subir CAF, ingresar ruta del certificado y contraseña.

1. Abrir `/pos/` → abrir caja → agregar productos
2. Hacer clic en "Cobrar + Emitir boleta"
3. Expected: toast con número de folio, PDF se abre en nueva pestaña

- [ ] **Step 3: Ejecutar todos los tests**

```bash
venv\Scripts\pytest tests/ -v
```
Expected: todos los tests pasan.

- [ ] **Step 4: Commit final**

```bash
git add templates/pos.html blueprints/pos.py
git commit -m "feat: wire up boleta emission from POS checkout flow"
```

---

## Verificación final

- [ ] Ir a `/crm/configuracion` → sección SII → ingresar RUT, razón social, giro, dirección, contraseña del certificado
- [ ] Subir CAF descargado del SII (si no está disponible, el botón "Cobrar + Emitir boleta" mostrará el error)
- [ ] Crear una venta en el POS → "Cobrar + Emitir boleta" → boleta emitida con folio y PDF
- [ ] Revisar `static/boletas/boleta_N.pdf` — debe tener el formato de ticket con todos los datos
- [ ] En ambiente certificación, verificar track_id en portal SII

## Notas importantes

**Antes de usar en producción:**
1. Obtener el CAF desde sii.cl con ambiente de producción
2. Cambiar ambiente a "Producción" en configuración
3. Hacer pruebas completas en certificación primero (SII tiene portal de pruebas)
4. El RUT receptor `66666666-6` es el valor estándar para boletas sin identificar al cliente
