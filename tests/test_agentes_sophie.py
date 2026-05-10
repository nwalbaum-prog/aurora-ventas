import pytest

AGENT_KEY = 'aurora_agent_2024'
HEADERS = {'X-Agent-Key': AGENT_KEY}


def test_tablas_whatsapp_existen(client):
    tc, app_mod = client
    with app_mod.db() as c:
        tablas = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert 'whatsapp_lid_cache' in tablas
    assert 'whatsapp_conversaciones' in tablas


def test_agenda_tiene_columnas_sophie(client):
    tc, app_mod = client
    with app_mod.db() as c:
        cols = {r['name'] for r in c.execute("PRAGMA table_info(agenda)").fetchall()}
    assert 'tipo_agente' in cols
    assert 'telefono_destino' in cols
    assert 'payload_json' in cols
    assert 'ejecutado_en' in cols


def test_lid_cache_get_vacio(client):
    tc, app_mod = client
    r = tc.get('/api/agentes/lid-cache', headers=HEADERS)
    assert r.status_code == 200
    assert r.get_json() == {}


def test_lid_cache_post_y_get(client):
    tc, app_mod = client
    payload = {'38328439148772': '56994891724', '245139737948287': '56982349644'}
    r = tc.post('/api/agentes/lid-cache', json=payload, headers=HEADERS)
    assert r.status_code == 200
    assert r.get_json()['ok'] is True

    r2 = tc.get('/api/agentes/lid-cache', headers=HEADERS)
    data = r2.get_json()
    assert data['38328439148772'] == '56994891724'
    assert data['245139737948287'] == '56982349644'


def test_lid_cache_sin_key_usa_sesion(client):
    # conftest already logs in via web session — endpoint is accessible
    tc, app_mod = client
    r = tc.get('/api/agentes/lid-cache')
    assert r.status_code == 200  # session auth allows access


def test_conversacion_ciclo_completo(client):
    tc, app_mod = client
    telefono = '56912345678'

    # GET cuando no existe → 404
    r = tc.get(f'/api/agentes/conversaciones/{telefono}', headers=HEADERS)
    assert r.status_code == 404

    # POST crea conversación
    payload = {
        'tipo': 'minorista',
        'mensajes_json': '[{"role":"user","content":"hola"}]',
        'cliente_data_json': '{}',
        'pedido_guardado': 0,
    }
    r = tc.post(f'/api/agentes/conversaciones/{telefono}', json=payload, headers=HEADERS)
    assert r.status_code == 200

    # GET retorna la conversación
    r = tc.get(f'/api/agentes/conversaciones/{telefono}', headers=HEADERS)
    assert r.status_code == 200
    data = r.get_json()
    assert data['tipo'] == 'minorista'
    assert 'hola' in data['mensajes_json']

    # DELETE elimina la conversación
    r = tc.delete(f'/api/agentes/conversaciones/{telefono}', headers=HEADERS)
    assert r.status_code == 200

    r = tc.get(f'/api/agentes/conversaciones/{telefono}', headers=HEADERS)
    assert r.status_code == 404


def test_pedidos_cliente_sin_telefono(client):
    tc, app_mod = client
    r = tc.get('/api/agentes/pedidos-cliente', headers=HEADERS)
    assert r.status_code == 400


def test_pedidos_cliente_no_encontrado(client):
    tc, app_mod = client
    r = tc.get('/api/agentes/pedidos-cliente?telefono=56999999999', headers=HEADERS)
    assert r.status_code == 200
    data = r.get_json()
    assert data['cliente'] is None
    assert data['pedidos'] == []


def test_pedidos_cliente_con_historial(client):
    tc, app_mod = client
    with app_mod.db() as c:
        c.execute(
            "INSERT INTO clientes (nombre, telefono, tipo) VALUES (?,?,?)",
            ('María Test', '56912345678', 'CLIENTE')
        )
        cid = c.execute("SELECT id FROM clientes WHERE telefono='56912345678'").fetchone()['id']
        c.execute(
            "INSERT INTO ventas (cliente_id, total, estado_pago, estado_despacho, fecha_despacho, canal) "
            "VALUES (?,?,?,?,?,?)",
            (cid, 9500, 'PAGADO', 'ENVIADO', '2026-05-10', 'whatsapp')
        )

    r = tc.get('/api/agentes/pedidos-cliente?telefono=56912345678', headers=HEADERS)
    assert r.status_code == 200
    data = r.get_json()
    assert data['cliente']['nombre'] == 'María Test'
    assert data['cliente']['total_pedidos'] == 1
    assert len(data['pedidos']) == 1
    assert data['pedidos'][0]['total'] == 9500


def test_crear_tarea_sophie(client):
    tc, app_mod = client
    payload = {
        'titulo': 'Sophie: mensaje_programado',
        'descripcion': 'Hola Juan, confirma tu pedido del jueves',
        'tipo': 'tarea',
        'fecha': '2026-05-09',
        'hora': '10:00',
        'prioridad': 'alta',
        'tipo_agente': 'sophie_tarea',
        'telefono_destino': '56912345678',
        'payload_json': '{"subtipo":"mensaje_programado","condicion":null}',
    }
    r = tc.post('/api/agentes/agenda', json=payload, headers=HEADERS)
    assert r.status_code == 200
    tarea_id = r.get_json()['id']

    # Consultar tareas sophie pendientes
    r2 = tc.get('/api/agentes/agenda/sophie-pendientes', headers=HEADERS)
    assert r2.status_code == 200
    tareas = r2.get_json()['tareas']
    assert any(t['id'] == tarea_id for t in tareas)


def test_completar_tarea_sophie(client):
    tc, app_mod = client
    with app_mod.db() as c:
        cur = c.execute(
            "INSERT INTO agenda (tipo, titulo, descripcion, fecha, hora, prioridad, "
            "tipo_agente, telefono_destino, payload_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ('tarea', 'Sophie: test', 'mensaje', '2026-05-11', '10:00', 'alta',
             'sophie_tarea', '56912345678', '{}')
        )
        tid = cur.lastrowid

    r = tc.post(f'/api/agentes/agenda/{tid}/completar',
                json={'resultado': 'ok'}, headers=HEADERS)
    assert r.status_code == 200

    with app_mod.db() as c:
        row = c.execute("SELECT * FROM agenda WHERE id=?", (tid,)).fetchone()
    assert row['completado'] == 1
    assert row['ejecutado_en'] is not None
