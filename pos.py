# pos.py — Blueprint POS para Aurora Bakers
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from datetime import date, datetime
from app import db, login_required, _load_config, _save_config

pos_bp = Blueprint('pos', __name__)

# Estado en memoria del carrito activo (un proceso = un local)
_pos_carrito_activo = {
    "turno_id": None,
    "items":    [],
    "total":    0,
    "estado":   "esperando"   # esperando | en_curso | finalizado
}


@pos_bp.route('/pos')
@login_required
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
@login_required
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
    uid = session.get('user_id')
    with db() as c:
        existente = c.execute(
            "SELECT id FROM pos_turnos WHERE usuario_id=? AND estado='abierto'", (uid,)
        ).fetchone()
        if existente:
            return jsonify({'error': 'Ya tienes un turno abierto'}), 400
        monto = float(request.json.get('monto_inicial', 0))
        cur = c.execute(
            "INSERT INTO pos_turnos (usuario_id, fecha_apertura, monto_inicial_efectivo, estado) VALUES (?,?,?,?)",
            (uid, datetime.now().strftime('%Y-%m-%d %H:%M'), monto, 'abierto')
        )
        turno = c.execute("SELECT * FROM pos_turnos WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify({'ok': True, 'turno': dict(turno)})


@pos_bp.route('/api/pos/turno/cerrar', methods=['POST'])
@login_required
def api_turno_cerrar():
    uid = session.get('user_id')
    with db() as c:
        turno = c.execute(
            "SELECT * FROM pos_turnos WHERE usuario_id=? AND estado='abierto' ORDER BY id DESC LIMIT 1",
            (uid,)
        ).fetchone()
        if not turno:
            return jsonify({'error': 'No hay turno abierto'}), 400
        monto_declarado = float(request.json.get('monto_declarado', 0))
        c.execute(
            "UPDATE pos_turnos SET estado='cerrado', fecha_cierre=?, monto_declarado_efectivo=? WHERE id=?",
            (datetime.now().strftime('%Y-%m-%d %H:%M'), monto_declarado, turno['id'])
        )
        turno_actualizado = c.execute("SELECT * FROM pos_turnos WHERE id=?", (turno['id'],)).fetchone()
    _pos_carrito_activo.update({"turno_id": None, "items": [], "total": 0, "estado": "esperando"})
    return jsonify({'ok': True, 'turno': dict(turno_actualizado)})


@pos_bp.route('/api/pos/turno/<int:tid>/resumen')
@login_required
def api_turno_resumen(tid):
    with db() as c:
        turno = c.execute("SELECT * FROM pos_turnos WHERE id=?", (tid,)).fetchone()
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
