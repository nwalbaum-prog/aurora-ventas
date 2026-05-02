# pos.py — Blueprint POS para Aurora Bakers
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from datetime import date, datetime
from contextlib import contextmanager
from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('page_login', next=request.path))
        return f(*args, **kwargs)
    return decorated

def pos_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('page_login', next=request.path))
        rol = session.get('user_rol')
        if rol != 'admin' and 'pos' not in session.get('user_permisos', []):
            return redirect(url_for('page_inicio'))
        return f(*args, **kwargs)
    return decorated

@contextmanager
def db():
    import app as _app
    with _app.db() as conn:
        yield conn

def _load_config():
    import app as _app
    return _app._load_config()

def _save_config(data):
    import app as _app
    return _app._save_config(data)

pos_bp = Blueprint('pos', __name__)

# Estado en memoria del carrito activo (un proceso = un local)
_pos_carrito_activo = {
    "turno_id": None,
    "items":    [],
    "total":    0,
    "estado":   "esperando"   # esperando | en_curso | finalizado
}


@pos_bp.route('/pos')
@pos_required
def page_pos():
    with db() as c:
        uid = session.get('user_id')
        turno = c.execute(
            "SELECT * FROM pos_turnos WHERE usuario_id=? AND estado='abierto' ORDER BY id DESC LIMIT 1",
            (uid,)
        ).fetchone()
    if not turno:
        return redirect(url_for('pos.page_caja', msg='Debes abrir la caja antes de vender'))
    return render_template('pos.html', active='pos', turno=dict(turno))


@pos_bp.route('/pos/caja')
@pos_required
def page_caja():
    msg = request.args.get('msg', '')
    uid = session.get('user_id')
    with db() as c:
        turno = c.execute(
            "SELECT * FROM pos_turnos WHERE usuario_id=? AND estado='abierto' ORDER BY id DESC LIMIT 1",
            (uid,)
        ).fetchone()
        promociones = c.execute("SELECT * FROM pos_promociones ORDER BY id DESC").fetchall()
    return render_template('pos_caja.html', active='pos',
                           turno=dict(turno) if turno else None,
                           promociones=[dict(p) for p in promociones],
                           msg=msg)


@pos_bp.route('/pos/cliente')
# No @login_required — customer-facing display, shown on a second monitor without authentication
def page_cliente():
    return render_template('pos_cliente.html')


# ── API: Turnos ───────────────────────────────────────────────────────────────

@pos_bp.route('/api/pos/turno/activo')
@login_required
def api_turno_activo():
    uid = session.get('user_id')
    with db() as c:
        turno = c.execute(
            "SELECT * FROM pos_turnos WHERE usuario_id=? AND estado='abierto' ORDER BY id DESC LIMIT 1",
            (uid,)
        ).fetchone()
    return jsonify({'turno': dict(turno) if turno else None})


@pos_bp.route('/api/pos/turno/abrir', methods=['POST'])
@login_required
def api_turno_abrir():
    uid  = session.get('user_id')
    body = request.get_json(silent=True) or {}
    try:
        monto = float(body.get('monto_inicial', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'monto_inicial debe ser un número'}), 400
    with db() as c:
        existente = c.execute(
            "SELECT id FROM pos_turnos WHERE usuario_id=? AND estado='abierto'", (uid,)
        ).fetchone()
        if existente:
            return jsonify({'error': 'Ya tienes un turno abierto'}), 400
        cur = c.execute(
            "INSERT INTO pos_turnos (usuario_id, fecha_apertura, monto_inicial_efectivo, estado) VALUES (?,?,?,?)",
            (uid, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), monto, 'abierto')
        )
        turno = c.execute("SELECT * FROM pos_turnos WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify({'ok': True, 'turno': dict(turno)})


@pos_bp.route('/api/pos/turno/cerrar', methods=['POST'])
@login_required
def api_turno_cerrar():
    uid  = session.get('user_id')
    body = request.get_json(silent=True) or {}
    try:
        monto_declarado = float(body.get('monto_declarado', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'monto_declarado debe ser un número'}), 400
    with db() as c:
        turno = c.execute(
            "SELECT * FROM pos_turnos WHERE usuario_id=? AND estado='abierto' ORDER BY id DESC LIMIT 1",
            (uid,)
        ).fetchone()
        if not turno:
            return jsonify({'error': 'No hay turno abierto'}), 400
        c.execute(
            "UPDATE pos_turnos SET estado='cerrado', fecha_cierre=?, monto_declarado_efectivo=? WHERE id=?",
            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), monto_declarado, turno['id'])
        )
        turno_actualizado = c.execute("SELECT * FROM pos_turnos WHERE id=?", (turno['id'],)).fetchone()
    _pos_carrito_activo.update({"turno_id": None, "items": [], "total": 0, "estado": "esperando"})
    return jsonify({'ok': True, 'turno': dict(turno_actualizado)})


@pos_bp.route('/api/pos/turno/<int:tid>/resumen')
@login_required
def api_turno_resumen(tid):
    uid = session.get('user_id')
    with db() as c:
        turno = c.execute("SELECT * FROM pos_turnos WHERE id=? AND usuario_id=?", (tid, uid)).fetchone()
        if not turno:
            return jsonify({'error': 'Turno no encontrado'}), 404
        ventas = c.execute(
            """SELECT pv.*, v.total FROM pos_ventas pv JOIN ventas v ON v.id=pv.venta_id
               WHERE pv.turno_id=?""", (tid,)
        ).fetchall()
        total_efectivo = sum(v['monto_efectivo'] for v in ventas)
        total_tarjeta  = sum(v['monto_tarjeta']  for v in ventas)
        total_ventas   = sum(v['total']           for v in ventas)
    return jsonify({
        'turno':          dict(turno),
        'n_ventas':       len(ventas),
        'total_ventas':   total_ventas,
        'total_efectivo': total_efectivo,
        'total_tarjeta':  total_tarjeta,
        'diferencia':     round((turno['monto_declarado_efectivo'] or 0) -
                                (turno['monto_inicial_efectivo'] + total_efectivo), 0)
    })


# ── API: Productos y Frecuentes ───────────────────────────────────────────────

@pos_bp.route('/api/pos/productos')
@login_required
def api_pos_productos():
    q = request.args.get('q', '').strip()
    with db() as c:
        if q:
            productos = c.execute(
                "SELECT id,nombre,precio,stock FROM productos WHERE activo=1 AND nombre LIKE ? ORDER BY nombre LIMIT 20",
                (f'%{q}%',)
            ).fetchall()
        else:
            productos = c.execute(
                "SELECT id,nombre,precio,stock FROM productos WHERE activo=1 ORDER BY nombre LIMIT 50"
            ).fetchall()
        frecuentes_rows = c.execute(
            """SELECT pf.id as frec_id, pf.orden, p.id as producto_id, p.nombre, p.precio, p.stock
               FROM pos_frecuentes pf JOIN productos p ON p.id=pf.producto_id
               WHERE p.activo=1 ORDER BY pf.orden"""
        ).fetchall()
    return jsonify({
        'productos':  [dict(p) for p in productos],
        'frecuentes': [dict(f) for f in frecuentes_rows]
    })


@pos_bp.route('/api/pos/frecuentes', methods=['GET'])
@login_required
def api_frecuentes_list():
    with db() as c:
        rows = c.execute(
            """SELECT pf.id, pf.orden, p.id as producto_id, p.nombre, p.precio, p.stock
               FROM pos_frecuentes pf JOIN productos p ON p.id=pf.producto_id
               WHERE p.activo=1 ORDER BY pf.orden"""
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@pos_bp.route('/api/pos/frecuentes', methods=['POST'])
@login_required
def api_frecuentes_add():
    body = request.get_json(silent=True) or {}
    producto_id = body.get('producto_id')
    if not producto_id:
        return jsonify({'error': 'producto_id requerido'}), 400
    with db() as c:
        if not c.execute("SELECT id FROM productos WHERE id=? AND activo=1", (producto_id,)).fetchone():
            return jsonify({'error': 'Producto no encontrado'}), 404
        existente = c.execute("SELECT id FROM pos_frecuentes WHERE producto_id=?", (producto_id,)).fetchone()
        if existente:
            return jsonify({'error': 'Ya es frecuente'}), 400
        count = c.execute("SELECT COUNT(*) FROM pos_frecuentes").fetchone()[0]
        if count >= 8:
            return jsonify({'error': 'Máximo 8 frecuentes'}), 400
        c.execute("INSERT INTO pos_frecuentes (producto_id, orden) VALUES (?,?)", (producto_id, count))
    return jsonify({'ok': True})


@pos_bp.route('/api/pos/frecuentes/<int:fid>', methods=['DELETE'])
@login_required
def api_frecuentes_delete(fid):
    with db() as c:
        c.execute("DELETE FROM pos_frecuentes WHERE id=?", (fid,))
        if c.rowcount == 0:
            return jsonify({'error': 'Frecuente no encontrado'}), 404
    return jsonify({'ok': True})


# ── API: Promociones ──────────────────────────────────────────────────────────

@pos_bp.route('/api/pos/promociones', methods=['GET'])
@login_required
def api_promociones_list():
    with db() as c:
        rows = c.execute(
            "SELECT * FROM pos_promociones ORDER BY activa DESC, id DESC"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@pos_bp.route('/api/pos/promociones', methods=['POST'])
@login_required
def api_promociones_create():
    body = request.get_json(silent=True) or {}
    nombre = body.get('nombre', '').strip()
    tipo   = body.get('tipo', '')
    if not nombre or tipo not in ('porcentaje', 'fijo', '2x1'):
        return jsonify({'error': 'nombre y tipo (porcentaje/fijo/2x1) requeridos'}), 400
    with db() as c:
        cur = c.execute(
            "INSERT INTO pos_promociones (nombre,tipo,valor,producto_id,activa,fecha_inicio,fecha_fin) VALUES (?,?,?,?,?,?,?)",
            (nombre, tipo, float(body.get('valor', 0)), body.get('producto_id'),
             1, body.get('fecha_inicio'), body.get('fecha_fin'))
        )
        row = c.execute("SELECT * FROM pos_promociones WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify({'ok': True, 'promocion': dict(row)})


@pos_bp.route('/api/pos/promociones/<int:pid>', methods=['PUT'])
@login_required
def api_promociones_update(pid):
    body = request.get_json(silent=True) or {}
    tipo = body.get('tipo', '')
    if tipo not in ('porcentaje', 'fijo', '2x1'):
        return jsonify({'error': 'tipo debe ser porcentaje, fijo o 2x1'}), 400
    with db() as c:
        if not c.execute("SELECT id FROM pos_promociones WHERE id=?", (pid,)).fetchone():
            return jsonify({'error': 'Promoción no encontrada'}), 404
        c.execute(
            "UPDATE pos_promociones SET nombre=?,tipo=?,valor=?,producto_id=?,activa=?,fecha_inicio=?,fecha_fin=? WHERE id=?",
            (body.get('nombre'), tipo, float(body.get('valor', 0)),
             body.get('producto_id'), int(body.get('activa', 1)),
             body.get('fecha_inicio'), body.get('fecha_fin'), pid)
        )
    return jsonify({'ok': True})


@pos_bp.route('/api/pos/promociones/<int:pid>', methods=['DELETE'])
@login_required
def api_promociones_delete(pid):
    with db() as c:
        c.execute("DELETE FROM pos_promociones WHERE id=?", (pid,))
        if c.rowcount == 0:
            return jsonify({'error': 'Promoción no encontrada'}), 404
    return jsonify({'ok': True})


def _aplicar_promociones(items: list) -> tuple:
    """
    Calcula descuento total basado en promociones activas.
    items: [{"producto_id": int, "nombre": str, "cantidad": float, "precio_unitario": float}]
    Retorna: (descuento_total: float, detalle: list[str])
    """
    today = date.today().isoformat()
    with db() as c:
        promos = c.execute(
            """SELECT * FROM pos_promociones WHERE activa=1
               AND (fecha_inicio IS NULL OR fecha_inicio <= ?)
               AND (fecha_fin IS NULL OR fecha_fin >= ?)""",
            (today, today)
        ).fetchall()

    descuento = 0.0
    detalle   = []
    subtotal  = sum(float(i['cantidad']) * float(i['precio_unitario']) for i in items)

    for p in promos:
        if p['tipo'] == 'porcentaje':
            if p['producto_id']:
                for item in items:
                    if int(item['producto_id']) == p['producto_id']:
                        d = round(float(item['cantidad']) * float(item['precio_unitario']) * p['valor'] / 100)
                        descuento += d
                        detalle.append(f"{p['nombre']}: -${d:,.0f}")
            else:
                d = round(subtotal * p['valor'] / 100)
                descuento += d
                detalle.append(f"{p['nombre']}: -${d:,.0f}")

        elif p['tipo'] == 'fijo':
            if p['producto_id']:
                for item in items:
                    if int(item['producto_id']) == p['producto_id']:
                        descuento += p['valor']
                        detalle.append(f"{p['nombre']}: -${p['valor']:,.0f}")
            else:
                descuento += p['valor']
                detalle.append(f"{p['nombre']}: -${p['valor']:,.0f}")

        elif p['tipo'] == '2x1':
            if p['producto_id']:
                for item in items:
                    if int(item['producto_id']) == p['producto_id']:
                        unidades_gratis = int(float(item['cantidad']) // 2)
                        d = round(unidades_gratis * float(item['precio_unitario']))
                        descuento += d
                        detalle.append(f"{p['nombre']}: -${d:,.0f}")

    return max(0, round(descuento)), detalle


# ── API: Venta ────────────────────────────────────────────────────────────────

@pos_bp.route('/api/pos/venta', methods=['POST'])
@login_required
def api_pos_venta():
    # Imported here to avoid circular import at module load; cached by Python after first call
    import dte as dte_mod

    body  = request.get_json(silent=True) or {}
    items = body.get('items', [])
    if not items:
        return jsonify({'error': 'Carrito vacío'}), 400

    # Validate numeric fields in items before doing any DB work
    try:
        for i in items:
            float(i['cantidad'])
            float(i['precio_unitario'])
        monto_efectivo = float(body.get('monto_efectivo', 0))
    except (TypeError, ValueError, KeyError):
        return jsonify({'error': 'Items con campos numéricos inválidos'}), 400

    uid = session.get('user_id')

    with db() as c:
        turno = c.execute(
            "SELECT * FROM pos_turnos WHERE usuario_id=? AND estado='abierto' ORDER BY id DESC LIMIT 1",
            (uid,)
        ).fetchone()

    if not turno:
        return jsonify({'error': 'No hay turno abierto. Abre la caja primero.'}), 400

    descuento, detalle_promos = _aplicar_promociones(items)
    total_bruto = sum(float(i['cantidad']) * float(i['precio_unitario']) for i in items)
    total_final = max(0.0, total_bruto - descuento)

    metodo_pago = body.get('metodo_pago', 'efectivo')
    if metodo_pago == 'efectivo':
        monto_efectivo_real = total_final   # neto que queda en caja (sin vuelto)
        monto_tarjeta       = 0.0
    else:
        monto_efectivo_real = 0.0
        monto_tarjeta       = total_final
    vuelto = round(max(0.0, monto_efectivo - total_final)) if metodo_pago == 'efectivo' else 0

    with db() as c:
        cur = c.execute(
            """INSERT INTO ventas (fecha, canal, total, notas, estado_pago, estado_despacho, con_despacho)
               VALUES (?,?,?,?,?,?,?)""",
            (date.today().isoformat(), 'pos', total_final,
             '; '.join(detalle_promos), 'PAGADO', 'RETIRO EN TIENDA', 0)
        )
        venta_id = cur.lastrowid

        for item in items:
            c.execute(
                "INSERT INTO venta_items (venta_id,producto_id,cantidad,precio_unitario) VALUES (?,?,?,?)",
                (venta_id, item['producto_id'], float(item['cantidad']), float(item['precio_unitario']))
            )
            c.execute("UPDATE productos SET stock=stock-? WHERE id=?",
                      (float(item['cantidad']), item['producto_id']))

        pv_cur = c.execute(
            """INSERT INTO pos_ventas (turno_id,venta_id,metodo_pago,monto_efectivo,monto_tarjeta,vuelto,boleta_estado)
               VALUES (?,?,?,?,?,?,?)""",
            (turno['id'], venta_id, metodo_pago,
             monto_efectivo_real, monto_tarjeta, vuelto, 'pendiente')
        )
        pos_venta_id = pv_cur.lastrowid

    # DTE call is intentionally outside the DB transaction: a sale is always saved,
    # even if Bsale is down. Rows with boleta_estado='error' can be retried manually.
    cfg      = _load_config()
    dte_resp = dte_mod.emit_boleta(items, total_final, cfg)

    with db() as c:
        if dte_resp['ok']:
            c.execute(
                "UPDATE pos_ventas SET boleta_numero=?,boleta_folio=?,boleta_pdf_url=?,boleta_estado='emitida' WHERE id=?",
                (dte_resp['numero'], dte_resp['folio'], dte_resp['pdf_url'], pos_venta_id)
            )
        else:
            c.execute("UPDATE pos_ventas SET boleta_estado='error' WHERE id=?", (pos_venta_id,))

    _pos_carrito_activo.update({"items": [], "total": 0, "estado": "finalizado",
                                "total_cobrado": total_final})

    # Auto plan producción día siguiente (error silencioso para no bloquear la venta)
    try:
        import app as _app
        items_plan = [{'nombre_producto': i['nombre'], 'cantidad': float(i['cantidad'])} for i in items]
        with db() as c:
            _app._auto_plan_produccion(items_plan, c)
    except Exception as _e:
        print(f"[pos] auto_plan_produccion: {_e}")

    return jsonify({
        'ok':        True,
        'venta_id':  venta_id,
        'total':     total_final,
        'descuento': descuento,
        'vuelto':    vuelto,
        'boleta':    dte_resp,
        'promos':    detalle_promos
    })


# ── API: Carrito y Pantalla Cliente ──────────────────────────────────────────

@pos_bp.route('/api/pos/carrito', methods=['POST'])
@login_required
def api_pos_carrito_sync():
    """Cajero sincroniza el estado actual del carrito al servidor (debounced desde JS)."""
    d = request.get_json(silent=True) or {}
    try:
        total = float(d.get('total', 0))
    except (TypeError, ValueError):
        total = 0.0
    _pos_carrito_activo.update({
        "items":  d.get('items', []),
        "total":  total,
        "estado": "en_curso" if d.get('items') else "esperando"
    })
    return jsonify({'ok': True})


@pos_bp.route('/api/pos/cliente/estado')
def api_cliente_estado():
    """Polling desde /pos/cliente — no requiere login."""
    return jsonify(_pos_carrito_activo)


@pos_bp.route('/api/pos/config/dte', methods=['POST'])
@login_required
def api_pos_config_dte():
    d = request.json or {}
    update = {}
    if d.get('bsale_token'):            update['bsale_token']            = d['bsale_token']
    if d.get('bsale_document_type_id'): update['bsale_document_type_id'] = d['bsale_document_type_id']
    if d.get('bsale_price_list_id'):    update['bsale_price_list_id']    = d['bsale_price_list_id']
    _save_config(update)
    return jsonify({'ok': True})
