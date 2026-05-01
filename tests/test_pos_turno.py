# tests/test_pos_turno.py
import json

def test_no_hay_turno_activo_al_inicio(client):
    tc, _ = client
    r = tc.get('/api/pos/turno/activo')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data['turno'] is None

def test_abrir_turno(client):
    tc, _ = client
    r = tc.post('/api/pos/turno/abrir',
                json={'monto_inicial': 30000},
                content_type='application/json')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data['ok'] is True
    assert data['turno']['estado'] == 'abierto'

def test_no_abrir_dos_turnos(client):
    tc, _ = client
    tc.post('/api/pos/turno/abrir', json={'monto_inicial': 30000},
            content_type='application/json')
    r = tc.post('/api/pos/turno/abrir', json={'monto_inicial': 10000},
                content_type='application/json')
    assert r.status_code == 400

def test_cerrar_turno(client):
    tc, _ = client
    tc.post('/api/pos/turno/abrir', json={'monto_inicial': 30000},
            content_type='application/json')
    r = tc.post('/api/pos/turno/cerrar',
                json={'monto_declarado': 32000},
                content_type='application/json')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data['ok'] is True
    assert data['turno']['estado'] == 'cerrado'

def test_cerrar_sin_turno_abierto_da_error(client):
    tc, _ = client
    r = tc.post('/api/pos/turno/cerrar',
                json={'monto_declarado': 0},
                content_type='application/json')
    assert r.status_code == 400
