# pos.py — Blueprint POS para Aurora Bakers
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from datetime import date, datetime
from app import db, login_required, current_user, _load_config, _save_config

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
def page_cliente():
    return render_template('pos_cliente.html')
