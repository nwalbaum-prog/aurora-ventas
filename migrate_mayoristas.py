"""
Migración one-time: sistema legacy (clientes tipo=MAYORISTA) → tabla mayoristas.
Corre ONCE. Es idempotente: aborta si ya hay datos en mayoristas.
"""
import sqlite3
from datetime import date

DB = 'aurora.db'

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
con.execute("PRAGMA foreign_keys = ON")

# Guard: idempotente
existing = con.execute("SELECT COUNT(*) FROM mayoristas").fetchone()[0]
if existing > 0:
    print(f"ABORT: mayoristas ya tiene {existing} registros. Migración ya ejecutada.")
    con.close()
    exit(0)

clientes = con.execute(
    "SELECT id,nombre,email,telefono,direccion,notas FROM clientes WHERE tipo='MAYORISTA' ORDER BY id"
).fetchall()

print(f"Migrando {len(clientes)} clientes...")

mapping = {}  # clientes.id → mayoristas.id

for c in clientes:
    cid = c['id']

    # Días de despacho desde pedidos activos
    dias = con.execute(
        "SELECT dia_despacho FROM mayorista_pedidos WHERE cliente_id=? AND activo=1 ORDER BY id",
        (cid,)
    ).fetchall()
    dia1 = dias[0]['dia_despacho'] if len(dias) > 0 else ''
    dia2 = dias[1]['dia_despacho'] if len(dias) > 1 else ''

    cur = con.execute("""
        INSERT INTO mayoristas
            (nombre, telefono, email, direccion, notas,
             dia_despacho_1, dia_despacho_2, activo, condicion_pago, fecha_alta)
        VALUES (?,?,?,?,?,?,?,1,'contado',?)
    """, (
        c['nombre'],
        c['telefono'] or '',
        c['email'] or '',
        c['direccion'] or '',
        c['notas'] or '',
        dia1, dia2,
        str(date.today())
    ))
    new_mid = cur.lastrowid
    mapping[cid] = new_mid
    print(f"  [{cid}] {c['nombre']} -> mayoristas.id={new_mid}  dias=[{dia1},{dia2}]")

print()
print("Migrando pedidos activos...")

pedidos = con.execute(
    "SELECT * FROM mayorista_pedidos WHERE activo=1 ORDER BY id"
).fetchall()

for p in pedidos:
    cid = p['cliente_id']
    if cid not in mapping:
        print(f"  SKIP pedido {p['id']}: cliente_id={cid} no migrado")
        continue

    new_mid = mapping[cid]
    fecha = (p['created_at'] or str(date.today()))[:10]

    cur = con.execute("""
        INSERT INTO mayoristas_pedidos
            (mayorista_id, fecha_pedido, fecha_entrega, dia_entrega,
             estado, subtotal, descuento, total, condicion_pago, estado_pago, notas)
        VALUES (?,?,?,?,'ENTREGADO',0,0,0,'contado','PENDIENTE',?)
    """, (new_mid, fecha, fecha, p['dia_despacho'], p['notas'] or ''))
    new_pid = cur.lastrowid

    lineas = con.execute(
        "SELECT * FROM mayorista_pedido_lineas WHERE pedido_id=?", (p['id'],)
    ).fetchall()

    total = 0.0
    for l in lineas:
        prod = con.execute(
            "SELECT precio, precio_mayorista FROM productos WHERE id=?", (l['producto_id'],)
        ).fetchone()
        if not prod:
            continue
        precio = prod['precio_mayorista'] if prod['precio_mayorista'] else prod['precio']
        subtotal = precio * l['cantidad']
        total += subtotal
        con.execute("""
            INSERT INTO mayoristas_pedido_items
                (pedido_id, producto_id, cantidad, precio_unit, descuento, subtotal)
            VALUES (?,?,?,?,0,?)
        """, (new_pid, l['producto_id'], int(l['cantidad']), precio, subtotal))

    con.execute(
        "UPDATE mayoristas_pedidos SET subtotal=?, total=? WHERE id=?",
        (total, total, new_pid)
    )
    print(f"  pedido legacy {p['id']} -> mayoristas_pedidos.id={new_pid}  total=${total:,.0f}")

con.commit()
con.close()
print()
print("Migración completa.")
print("Mapping final:", {k: v for k, v in mapping.items()})
