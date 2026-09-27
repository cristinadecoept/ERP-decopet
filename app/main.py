"""Decopet ERP — aplicación web. Cada función @app.get / @app.post es una pantalla o una acción."""
import datetime, json
from pathlib import Path
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.db import conectar, inicializar
from app import queries as q

BASE = Path(__file__).resolve().parent
app = FastAPI(title="Decopet ERP")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
tpl = Jinja2Templates(directory=BASE / "templates")

ESTADOS = ["pendiente_pago", "pagado", "en_preparacion", "asignado", "en_camino", "entregado", "cancelado"]
ESTADO_LABEL = {"pendiente_pago": "Pendiente de pago", "pagado": "Pagado", "en_preparacion": "En preparación",
                "asignado": "Asignado", "en_camino": "En camino", "entregado": "Entregado", "cancelado": "Cancelado"}
CANALES = ["whatsapp", "cashea", "duwu", "vidapets", "shopify", "instagram", "otro"]
TIPOS_ENTREGA = {"delivery_caracas": "Delivery Caracas", "pickup": "Pickup", "nacional": "Envío nacional"}


def fmt_usd(v):
    try: return f"${v:,.2f}"
    except (TypeError, ValueError): return "—"

def fmt_fecha(s):
    if not s: return "—"
    try: return datetime.date.fromisoformat(str(s)[:10]).strftime("%d/%m/%Y")
    except ValueError: return str(s)

tpl.env.filters["usd"] = fmt_usd
tpl.env.filters["fecha"] = fmt_fecha
tpl.env.globals.update(ESTADO_LABEL=ESTADO_LABEL, ESTADOS=ESTADOS, CANALES=CANALES, TIPOS_ENTREGA=TIPOS_ENTREGA)


@app.on_event("startup")
def _startup():
    inicializar()


def db():
    con = conectar()
    try: yield con
    finally: con.close()


def render(request, nombre, **ctx):
    ctx.update(request=request, hoy=datetime.date.today().isoformat())
    return tpl.TemplateResponse(nombre, ctx)


# ------------------------------------------------------------------ TABLERO
@app.get("/", response_class=HTMLResponse)
def tablero(request: Request, con=Depends(db)):
    return render(request, "tablero.html", d=q.tablero(con))


# ------------------------------------------------------------------ PEDIDOS
@app.get("/pedidos", response_class=HTMLResponse)
def pedidos(request: Request, estado: str = "activos", desde: str = "", hasta: str = "", buscar: str = "", con=Depends(db)):
    sql = """SELECT p.*, c.nombre cliente, d.nombre despachador FROM pedidos p
             LEFT JOIN clientes c ON c.id=p.cliente_id LEFT JOIN despachadores d ON d.id=p.despachador_id WHERE 1=1"""
    args = []
    if estado == "activos": sql += " AND p.estado NOT IN ('entregado','cancelado')"
    elif estado != "todos": sql += " AND p.estado=?"; args.append(estado)
    if desde: sql += " AND p.fecha>=?"; args.append(desde)
    if hasta: sql += " AND p.fecha<=?"; args.append(hasta)
    if buscar: sql += " AND (c.nombre LIKE ? OR p.numero LIKE ?)"; args += [f"%{buscar}%", f"%{buscar}%"]
    sql += " ORDER BY p.fecha DESC, p.id DESC LIMIT 300"
    rows = con.execute(sql, args).fetchall()
    return render(request, "pedidos.html", pedidos=rows, estado=estado, desde=desde, hasta=hasta, buscar=buscar)


@app.get("/pedidos/nuevo", response_class=HTMLResponse)
def pedido_nuevo(request: Request, cliente_id: int = None, con=Depends(db)):
    productos = con.execute("SELECT * FROM productos WHERE activo=1 ORDER BY orden").fetchall()
    metodos = con.execute("SELECT * FROM metodos_pago WHERE activo=1 ORDER BY orden").fetchall()
    despachadores = con.execute("SELECT * FROM despachadores WHERE activo=1").fetchall()
    cliente = con.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,)).fetchone() if cliente_id else None
    return render(request, "pedido_form.html", productos=productos, metodos=metodos, despachadores=despachadores, cliente=cliente)


@app.get("/api/clientes")
def api_clientes(buscar: str = "", con=Depends(db)):
    rows = con.execute("""SELECT id, nombre, telefono, ciudad, zona FROM clientes
                          WHERE nombre LIKE ? OR telefono LIKE ? ORDER BY nombre LIMIT 12""", (f"%{buscar}%", f"%{buscar}%")).fetchall()
    return [dict(r) for r in rows]


@app.post("/pedidos/nuevo")
async def pedido_crear(request: Request, con=Depends(db)):
    f = await request.form()
    cliente_id = f.get("cliente_id") or None
    if not cliente_id and f.get("cliente_nombre"):
        cur = con.execute("INSERT INTO clientes (nombre, telefono, ciudad, zona, canal_origen) VALUES (?,?,?,?,?)",
                          (f["cliente_nombre"].strip(), f.get("cliente_telefono"), f.get("cliente_ciudad") or "Caracas", f.get("cliente_zona"), f.get("canal")))
        cliente_id = cur.lastrowid
    fecha = f.get("fecha") or datetime.date.today().isoformat()
    delivery = float(f.get("delivery") or 0)
    items, subtotal = [], 0.0
    for pid, cant, precio, perso in zip(f.getlist("item_producto"), f.getlist("item_cantidad"), f.getlist("item_precio"), f.getlist("item_perso")):
        if not pid: continue
        cant, precio = float(cant or 1), float(precio or 0)
        items.append((int(pid), cant, precio, cant * precio, perso or None)); subtotal += cant * precio
    total = subtotal + delivery
    cur = con.execute("""INSERT INTO pedidos (fecha, cliente_id, canal, estado, tipo_entrega, agencia, despachador_id, subtotal, delivery, total, notas, origen)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,'manual')""",
                      (fecha, cliente_id, f.get("canal"), f.get("estado") or "pendiente_pago", f.get("tipo_entrega") or None,
                       f.get("agencia") or None, f.get("despachador_id") or None, subtotal, delivery, total, f.get("notas") or None))
    pid_ = cur.lastrowid
    con.execute("UPDATE pedidos SET numero=? WHERE id=?", (f"P-{fecha[:4]}-{pid_:05d}", pid_))
    for prod, cant, precio, tot, perso in items:
        con.execute("INSERT INTO pedido_items (pedido_id, producto_id, cantidad, precio_unitario, total, personalizacion) VALUES (?,?,?,?,?,?)",
                    (pid_, prod, cant, precio, tot, perso))
        con.execute("INSERT INTO movimientos_inventario (producto_id, fecha, tipo, cantidad, pedido_id) VALUES (?,?,?,?,?)", (prod, fecha, "venta", -cant, pid_))
        con.execute("UPDATE productos SET stock = stock - ? WHERE id=?", (cant, prod))
    if f.get("pago_metodo") and float(f.get("pago_monto") or 0) > 0:
        _registrar_pago(con, pid_, fecha, int(f["pago_metodo"]), float(f["pago_monto"]), f.get("pago_referencia"))
    if f.get("canal") == "cashea":
        con.execute("INSERT INTO cashea_ordenes (pedido_id, monto_total, referencia) VALUES (?,?,?)", (pid_, total, f.get("cashea_ref") or None))
    con.commit()
    return RedirectResponse(f"/pedidos/{pid_}", status_code=303)


def _registrar_pago(con, pedido_id, fecha, metodo_id, monto, referencia=None, monto_moneda=None, tasa=None):
    m = con.execute("SELECT * FROM metodos_pago WHERE id=?", (metodo_id,)).fetchone()
    con.execute("""INSERT INTO pagos (pedido_id, fecha, metodo_id, caja_id, monto_usd, monto_moneda, moneda, tasa, referencia, confirmado)
                   VALUES (?,?,?,?,?,?,?,?,?,1)""", (pedido_id, fecha, metodo_id, m["caja_id"], monto, monto_moneda, m["moneda_recibida"], tasa, referencia))
    pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE pedido_id=? AND confirmado=1", (pedido_id,)).fetchone()[0]
    p = con.execute("SELECT total, estado FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    if p["estado"] == "pendiente_pago" and pagado >= p["total"] - 0.01:
        con.execute("UPDATE pedidos SET estado='pagado', actualizado_en=datetime('now','localtime') WHERE id=?", (pedido_id,))


@app.get("/pedidos/{pedido_id}", response_class=HTMLResponse)
def pedido_detalle(request: Request, pedido_id: int, con=Depends(db)):
    p = con.execute("""SELECT p.*, c.nombre cliente, c.telefono, c.zona, c.direccion, c.ciudad, d.nombre despachador
                       FROM pedidos p LEFT JOIN clientes c ON c.id=p.cliente_id LEFT JOIN despachadores d ON d.id=p.despachador_id WHERE p.id=?""", (pedido_id,)).fetchone()
    if p is None:
        return RedirectResponse("/pedidos", status_code=303)
    items = con.execute("SELECT i.*, pr.nombre producto FROM pedido_items i LEFT JOIN productos pr ON pr.id=i.producto_id WHERE pedido_id=?", (pedido_id,)).fetchall()
    pagos = con.execute("SELECT pa.*, m.nombre metodo FROM pagos pa LEFT JOIN metodos_pago m ON m.id=pa.metodo_id WHERE pedido_id=? ORDER BY fecha", (pedido_id,)).fetchall()
    pagado = sum(x["monto_usd"] for x in pagos if x["confirmado"])
    metodos = con.execute("SELECT * FROM metodos_pago WHERE activo=1 ORDER BY orden").fetchall()
    despachadores = con.execute("SELECT * FROM despachadores WHERE activo=1").fetchall()
    cashea = con.execute("SELECT * FROM cashea_ordenes WHERE pedido_id=?", (pedido_id,)).fetchone()
    resumen = _resumen_despacho(p, items)
    return render(request, "pedido.html", p=p, items=items, pagos=pagos, pagado=pagado, metodos=metodos,
                  despachadores=despachadores, cashea=cashea, resumen=resumen)


def _resumen_despacho(p, items):
    lineas = [f"📦 Pedido {p['numero']} — {p['cliente'] or 'Cliente'}"]
    if p["telefono"]: lineas.append(f"📱 {p['telefono']}")
    for i in items:
        nombre = i["producto"] or i["descripcion"]
        extra = f" (personalizado: {i['personalizacion']})" if i["personalizacion"] else ""
        lineas.append(f"• {int(i['cantidad']) if i['cantidad'] == int(i['cantidad']) else i['cantidad']} x {nombre}{extra}")
    if p["tipo_entrega"]: lineas.append(f"🚚 {TIPOS_ENTREGA.get(p['tipo_entrega'], p['tipo_entrega'])}" + (f" — {p['agencia']}" if p["agencia"] else ""))
    if p["zona"] or p["direccion"]: lineas.append(f"📍 {p['zona'] or ''} {p['direccion'] or ''}".strip())
    pend = (p["total"] or 0)
    lineas.append(f"💵 Total: ${pend:,.2f}")
    if p["notas"]: lineas.append(f"📝 {p['notas']}")
    return "\n".join(lineas)


@app.post("/pedidos/{pedido_id}/estado")
def pedido_estado(pedido_id: int, estado: str = Form(...), despachador_id: str = Form(""), fecha_entrega: str = Form(""), con=Depends(db)):
    sets, args = ["estado=?", "actualizado_en=datetime('now','localtime')"], [estado]
    if despachador_id: sets.append("despachador_id=?"); args.append(int(despachador_id))
    if estado == "entregado": sets.append("fecha_entrega=?"); args.append(fecha_entrega or datetime.date.today().isoformat())
    args.append(pedido_id)
    con.execute(f"UPDATE pedidos SET {', '.join(sets)} WHERE id=?", args); con.commit()
    return RedirectResponse(f"/pedidos/{pedido_id}", status_code=303)


@app.post("/pedidos/{pedido_id}/pago")
def pedido_pago(pedido_id: int, metodo_id: int = Form(...), monto: float = Form(...), fecha: str = Form(""), referencia: str = Form(""),
                monto_moneda: str = Form(""), tasa: str = Form(""), con=Depends(db)):
    _registrar_pago(con, pedido_id, fecha or datetime.date.today().isoformat(), metodo_id, monto, referencia or None,
                    float(monto_moneda) if monto_moneda else None, float(tasa) if tasa else None)
    con.commit()
    return RedirectResponse(f"/pedidos/{pedido_id}", status_code=303)


@app.post("/pedidos/{pedido_id}/notas")
def pedido_notas(pedido_id: int, notas: str = Form(""), tipo_entrega: str = Form(""), agencia: str = Form(""), con=Depends(db)):
    con.execute("UPDATE pedidos SET notas=?, tipo_entrega=?, agencia=?, actualizado_en=datetime('now','localtime') WHERE id=?",
                (notas or None, tipo_entrega or None, agencia or None, pedido_id)); con.commit()
    return RedirectResponse(f"/pedidos/{pedido_id}", status_code=303)


# ------------------------------------------------------------------ CLIENTES
@app.get("/clientes", response_class=HTMLResponse)
def clientes(request: Request, buscar: str = "", filtro: str = "", con=Depends(db)):
    sql = """SELECT c.*, COUNT(p.id) pedidos, COALESCE(SUM(p.total),0) monto, MAX(p.fecha) ultimo,
                    EXISTS(SELECT 1 FROM pedido_items i JOIN productos pr ON pr.id=i.producto_id JOIN pedidos pp ON pp.id=i.pedido_id
                           WHERE pp.cliente_id=c.id AND pr.es_porche_pro=1) tiene_pro
             FROM clientes c LEFT JOIN pedidos p ON p.cliente_id=c.id AND p.estado!='cancelado' WHERE 1=1"""
    args = []
    if buscar: sql += " AND (c.nombre LIKE ? OR c.telefono LIKE ? OR c.correo LIKE ?)"; args += [f"%{buscar}%"] * 3
    sql += " GROUP BY c.id"
    if filtro == "pro": sql += " HAVING tiene_pro=1"
    if filtro == "top": sql += " ORDER BY monto DESC"
    elif filtro == "recientes": sql += " ORDER BY ultimo DESC"
    else: sql += " ORDER BY ultimo DESC"
    sql += " LIMIT 200"
    rows = con.execute(sql, args).fetchall()
    return render(request, "clientes.html", clientes=rows, buscar=buscar, filtro=filtro)


@app.get("/clientes/recompra", response_class=HTMLResponse)
def clientes_recompra(request: Request, con=Depends(db)):
    ciclo = con.execute("SELECT valor FROM configuracion WHERE clave='dias_ciclo_repuesto'").fetchone()[0]
    return render(request, "recompra.html", rows=q.clientes_recompra(con, limite=200), ciclo=ciclo)


@app.get("/clientes/nuevo", response_class=HTMLResponse)
def cliente_nuevo(request: Request):
    return render(request, "cliente_form.html", c=None)


@app.post("/clientes/nuevo")
async def cliente_crear(request: Request, con=Depends(db)):
    f = await request.form()
    cur = con.execute("INSERT INTO clientes (nombre, telefono, correo, ciudad, zona, direccion, canal_origen, notas) VALUES (?,?,?,?,?,?,?,?)",
                      (f["nombre"].strip(), f.get("telefono"), f.get("correo"), f.get("ciudad"), f.get("zona"), f.get("direccion"), f.get("canal_origen"), f.get("notas")))
    cid = cur.lastrowid
    if f.get("mascota_nombre"):
        con.execute("INSERT INTO mascotas (cliente_id, nombre, raza, fecha_nacimiento, tamano) VALUES (?,?,?,?,?)",
                    (cid, f["mascota_nombre"], f.get("mascota_raza"), f.get("mascota_nacimiento") or None, f.get("mascota_tamano")))
    con.commit()
    return RedirectResponse(f"/clientes/{cid}", status_code=303)


@app.get("/clientes/{cliente_id}", response_class=HTMLResponse)
def cliente_detalle(request: Request, cliente_id: int, con=Depends(db)):
    c = con.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,)).fetchone()
    if c is None:
        return RedirectResponse("/clientes", status_code=303)
    mascotas = con.execute("SELECT * FROM mascotas WHERE cliente_id=?", (cliente_id,)).fetchall()
    pedidos = con.execute("SELECT * FROM pedidos WHERE cliente_id=? ORDER BY fecha DESC", (cliente_id,)).fetchall()
    items = con.execute("""SELECT i.pedido_id, pr.nombre, i.cantidad FROM pedido_items i LEFT JOIN productos pr ON pr.id=i.producto_id
                           JOIN pedidos p ON p.id=i.pedido_id WHERE p.cliente_id=?""", (cliente_id,)).fetchall()
    por_pedido = {}
    for i in items: por_pedido.setdefault(i["pedido_id"], []).append(i)
    resumen = con.execute("""SELECT COUNT(*) n, COALESCE(SUM(total),0) monto, MIN(fecha) primero, MAX(fecha) ultimo FROM pedidos WHERE cliente_id=? AND estado!='cancelado'""", (cliente_id,)).fetchone()
    return render(request, "cliente.html", c=c, mascotas=mascotas, pedidos=pedidos, por_pedido=por_pedido, resumen=resumen)


@app.post("/clientes/{cliente_id}/editar")
async def cliente_editar(request: Request, cliente_id: int, con=Depends(db)):
    f = await request.form()
    con.execute("""UPDATE clientes SET nombre=?, telefono=?, correo=?, ciudad=?, zona=?, direccion=?, canal_origen=?, notas=?, actualizado_en=datetime('now','localtime') WHERE id=?""",
                (f["nombre"].strip(), f.get("telefono"), f.get("correo"), f.get("ciudad"), f.get("zona"), f.get("direccion"), f.get("canal_origen"), f.get("notas"), cliente_id))
    con.commit()
    return RedirectResponse(f"/clientes/{cliente_id}", status_code=303)


@app.post("/clientes/{cliente_id}/mascota")
def mascota_crear(cliente_id: int, nombre: str = Form(...), raza: str = Form(""), fecha_nacimiento: str = Form(""), tamano: str = Form(""), con=Depends(db)):
    con.execute("INSERT INTO mascotas (cliente_id, nombre, raza, fecha_nacimiento, tamano) VALUES (?,?,?,?,?)", (cliente_id, nombre, raza, fecha_nacimiento or None, tamano)); con.commit()
    return RedirectResponse(f"/clientes/{cliente_id}", status_code=303)


# ------------------------------------------------------------------ PRODUCTOS
@app.get("/productos", response_class=HTMLResponse)
def productos(request: Request, con=Depends(db)):
    rows = con.execute("""SELECT p.*, 
        (SELECT COALESCE(SUM(i.cantidad),0) FROM pedido_items i JOIN pedidos pe ON pe.id=i.pedido_id WHERE i.producto_id=p.id AND pe.fecha>=date('now','-30 days')) vendidos_30,
        (SELECT COALESCE(SUM(i.cantidad),0) FROM pedido_items i JOIN pedidos pe ON pe.id=i.pedido_id WHERE i.producto_id=p.id AND pe.fecha>=date('now','-365 days')) vendidos_365
        FROM productos p ORDER BY activo DESC, orden""").fetchall()
    return render(request, "productos.html", productos=rows)


@app.post("/productos/{producto_id}")
def producto_editar(producto_id: int, precio: str = Form(""), stock: str = Form("0"), activo: str = Form("0"), con=Depends(db)):
    con.execute("UPDATE productos SET precio=?, stock=?, activo=? WHERE id=?", (float(precio) if precio else None, int(stock or 0), 1 if activo == "1" else 0, producto_id)); con.commit()
    return RedirectResponse("/productos", status_code=303)


@app.post("/productos/{producto_id}/produccion")
def produccion(producto_id: int, cantidad: int = Form(...), fecha: str = Form(""), notas: str = Form(""), con=Depends(db)):
    fecha = fecha or datetime.date.today().isoformat()
    con.execute("INSERT INTO movimientos_inventario (producto_id, fecha, tipo, cantidad, notas) VALUES (?,?,?,?,?)", (producto_id, fecha, "produccion", cantidad, notas or None))
    con.execute("UPDATE productos SET stock = stock + ? WHERE id=?", (cantidad, producto_id)); con.commit()
    return RedirectResponse("/productos", status_code=303)


# ------------------------------------------------------------------ CAJA Y GASTOS
@app.get("/caja", response_class=HTMLResponse)
def caja(request: Request, con=Depends(db)):
    cajas, corte = q.saldos_caja(con)
    movs = con.execute("""SELECT 'pago' tipo, pa.fecha, c.nombre caja, pa.monto_usd monto, 'Pedido ' || p.numero || ' — ' || COALESCE(cl.nombre,'') concepto, p.id pedido_id
                          FROM pagos pa JOIN cajas c ON c.id=pa.caja_id JOIN pedidos p ON p.id=pa.pedido_id LEFT JOIN clientes cl ON cl.id=p.cliente_id WHERE pa.fecha>=?
                          UNION ALL
                          SELECT 'gasto', g.fecha, c.nombre, -g.monto_usd, g.categoria || ' — ' || COALESCE(g.descripcion,''), NULL
                          FROM gastos g LEFT JOIN cajas c ON c.id=g.caja_id WHERE g.fecha>=?
                          UNION ALL
                          SELECT m.tipo, m.fecha, c.nombre, CASE WHEN m.tipo IN ('ingreso','transferencia_in','ajuste') THEN m.monto_usd ELSE -m.monto_usd END, m.concepto, NULL
                          FROM movimientos_caja m JOIN cajas c ON c.id=m.caja_id WHERE m.fecha>=?
                          ORDER BY fecha DESC LIMIT 100""", (corte, corte, corte)).fetchall()
    return render(request, "caja.html", cajas=cajas, corte=corte if corte != "2100-01-01" else None, movs=movs)


@app.post("/caja/corte")
async def caja_corte(request: Request, con=Depends(db)):
    f = await request.form()
    con.execute("INSERT OR REPLACE INTO configuracion (clave, valor) VALUES ('fecha_corte_caja', ?)", (f["fecha_corte"],))
    for k, v in f.items():
        if k.startswith("saldo_"):
            con.execute("UPDATE cajas SET saldo_inicial=? WHERE id=?", (float(v or 0), int(k[6:])))
    con.commit()
    return RedirectResponse("/caja", status_code=303)


@app.post("/caja/movimiento")
def caja_movimiento(caja_id: int = Form(...), tipo: str = Form(...), monto: float = Form(...), fecha: str = Form(""), concepto: str = Form(""),
                    caja_destino: str = Form(""), con=Depends(db)):
    fecha = fecha or datetime.date.today().isoformat()
    if tipo == "transferencia" and caja_destino:
        con.execute("INSERT INTO movimientos_caja (caja_id, fecha, tipo, monto_usd, concepto) VALUES (?,?,?,?,?)", (caja_id, fecha, "transferencia_out", monto, concepto))
        con.execute("INSERT INTO movimientos_caja (caja_id, fecha, tipo, monto_usd, concepto) VALUES (?,?,?,?,?)", (int(caja_destino), fecha, "transferencia_in", monto, concepto))
    else:
        con.execute("INSERT INTO movimientos_caja (caja_id, fecha, tipo, monto_usd, concepto) VALUES (?,?,?,?,?)", (caja_id, fecha, tipo, monto, concepto))
    con.commit()
    return RedirectResponse("/caja", status_code=303)


@app.get("/gastos", response_class=HTMLResponse)
def gastos(request: Request, mes: str = "", con=Depends(db)):
    mes = mes or datetime.date.today().strftime("%Y-%m")
    rows = con.execute("SELECT g.*, c.nombre caja FROM gastos g LEFT JOIN cajas c ON c.id=g.caja_id WHERE substr(g.fecha,1,7)=? ORDER BY g.fecha DESC, g.id DESC", (mes,)).fetchall()
    por_cat = con.execute("SELECT categoria, SUM(monto_usd) monto FROM gastos WHERE substr(fecha,1,7)=? GROUP BY categoria ORDER BY monto DESC", (mes,)).fetchall()
    cajas = con.execute("SELECT * FROM cajas WHERE activa=1 ORDER BY orden").fetchall()
    return render(request, "gastos.html", gastos=rows, por_cat=por_cat, cajas=cajas, mes=mes, categorias=q.categorias_gasto(con),
                  total=sum(r["monto"] for r in por_cat))


@app.post("/gastos")
async def gasto_crear(request: Request, con=Depends(db)):
    f = await request.form()
    con.execute("""INSERT INTO gastos (fecha, categoria, subcategoria, descripcion, proveedor, cantidad, monto_usd, moneda, forma_pago, caja_id, referencia, notas)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f["fecha"], f["categoria"], f.get("subcategoria"), f.get("descripcion"), f.get("proveedor"), float(f.get("cantidad") or 0) or None,
                 float(f["monto_usd"]), f.get("moneda") or "USD", f.get("forma_pago"), int(f["caja_id"]) if f.get("caja_id") else None, f.get("referencia"), f.get("notas")))
    con.commit()
    return RedirectResponse(f"/gastos?mes={f['fecha'][:7]}", status_code=303)


@app.post("/gastos/{gasto_id}/borrar")
def gasto_borrar(gasto_id: int, con=Depends(db)):
    g = con.execute("SELECT fecha FROM gastos WHERE id=?", (gasto_id,)).fetchone()
    con.execute("DELETE FROM gastos WHERE id=?", (gasto_id,)); con.commit()
    return RedirectResponse(f"/gastos?mes={g['fecha'][:7] if g else ''}", status_code=303)


# ------------------------------------------------------------------ CASHEA
@app.get("/cashea", response_class=HTMLResponse)
def cashea(request: Request, estado: str = "", con=Depends(db)):
    sql = """SELECT co.*, p.numero, p.fecha, p.total, c.nombre cliente FROM cashea_ordenes co JOIN pedidos p ON p.id=co.pedido_id
             LEFT JOIN clientes c ON c.id=p.cliente_id WHERE 1=1"""
    args = []
    if estado: sql += " AND co.estado_cobro=?"; args.append(estado)
    sql += " ORDER BY p.fecha DESC LIMIT 300"
    rows = con.execute(sql, args).fetchall()
    resumen = con.execute("""SELECT estado_cobro, COUNT(*) n, SUM(monto_total) monto, SUM(monto_cobrado) cobrado FROM cashea_ordenes GROUP BY estado_cobro""").fetchall()
    return render(request, "cashea.html", rows=rows, resumen=resumen, estado=estado)


@app.post("/cashea/{cashea_id}")
def cashea_actualizar(cashea_id: int, referencia: str = Form(""), estado_cobro: str = Form(...), monto_cobrado: str = Form("0"), fecha_reporte: str = Form(""), notas: str = Form(""), con=Depends(db)):
    con.execute("UPDATE cashea_ordenes SET referencia=?, estado_cobro=?, monto_cobrado=?, fecha_reporte=?, notas=? WHERE id=?",
                (referencia or None, estado_cobro, float(monto_cobrado or 0), fecha_reporte or None, notas or None, cashea_id)); con.commit()
    return RedirectResponse("/cashea", status_code=303)


# ------------------------------------------------------------------ REPORTES
@app.get("/reportes", response_class=HTMLResponse)
def reportes(request: Request, anio: str = "", con=Depends(db)):
    anio = anio or str(datetime.date.today().year)
    meses = con.execute("""SELECT substr(fecha,1,7) mes, COUNT(*) pedidos, SUM(total) fact, AVG(total) ticket, COUNT(DISTINCT cliente_id) clientes,
                           (SELECT COALESCE(SUM(monto_usd),0) FROM gastos g WHERE substr(g.fecha,1,7)=substr(p.fecha,1,7)) gastos
                           FROM pedidos p WHERE substr(fecha,1,4)=? AND estado!='cancelado' GROUP BY 1 ORDER BY 1""", (anio,)).fetchall()
    categorias = con.execute("""SELECT substr(pe.fecha,1,7) mes, pr.categoria, SUM(i.cantidad) unidades, SUM(i.total) monto
                                FROM pedido_items i JOIN productos pr ON pr.id=i.producto_id JOIN pedidos pe ON pe.id=i.pedido_id
                                WHERE substr(pe.fecha,1,4)=? AND pe.estado!='cancelado' GROUP BY 1,2 ORDER BY 1""", (anio,)).fetchall()
    tabla = {}
    for r in categorias: tabla.setdefault(r["mes"], {})[r["categoria"]] = r
    anios = [r[0] for r in con.execute("SELECT DISTINCT substr(fecha,1,4) FROM pedidos ORDER BY 1 DESC")]
    return render(request, "reportes.html", meses=meses, tabla=tabla, anio=anio, anios=anios, cats=["porche", "repuesto", "rampa", "comedor", "extra"])
