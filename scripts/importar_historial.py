"""
Importa el Excel "DECOPET - Historial de Ventas.xlsx" (hoja VENTAS) a la base de datos.

Reglas:
 - Cada fila del Excel es una línea de producto. Las filas del mismo día y mismo cliente se agrupan en UN pedido.
 - El nombre del producto se normaliza usando la tabla producto_alias (PORCHE L = PRO GRANDE, etc.).
 - La forma de pago se normaliza usando metodo_pago_alias.
 - FACTURACION es lo que realmente se cobró por la línea (incluye delivery si aplica).
 - Se puede correr varias veces: borra lo importado antes (origen='importado_excel') y lo vuelve a cargar.
"""
import sys, re, datetime, collections, warnings
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openpyxl
from app.db import conectar, inicializar

warnings.filterwarnings("ignore")
ARCHIVO = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Downloads" / "DECOPET - Historial de Ventas.xlsx"


def parse_fecha(v):
    if isinstance(v, datetime.datetime):
        return v.date() if 2022 <= v.year <= 2030 else None
    if isinstance(v, datetime.date):
        return v
    if isinstance(v, (int, float)):
        return None
    s = str(v).strip()
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", s)
    if m:
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.match(r"^(\d{1,2})[/\-.](\d{2})(\d{2})$", s)   # '24/0323' -> 24/03/23 (falta una barra)
        if not m:
            return None
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
    if y < 100:
        y += 2000
    if not (2022 <= y <= 2027):
        return None
    try:
        return datetime.date(y, mo, d)
    except ValueError:
        return None


def limpiar_nombre(s):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s


def clave_cliente(nombre):
    return re.sub(r"[^a-z0-9]", "", nombre.lower())


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def main():
    inicializar()
    con = conectar()
    alias_prod = {r["alias"]: r["producto_id"] for r in con.execute("SELECT * FROM producto_alias")}
    alias_pago = {r["alias"]: r["metodo_id"] for r in con.execute("SELECT * FROM metodo_pago_alias")}
    metodo_info = {r["id"]: r for r in con.execute("SELECT * FROM metodos_pago")}
    sin_id = [r["id"] for r in metodo_info.values() if r["nombre"] == "Sin identificar"][0]
    delivery_id = con.execute("SELECT id FROM productos WHERE sku='DELIVERY'").fetchone()[0]

    # Limpiar importación anterior
    con.execute("DELETE FROM pedidos WHERE origen='importado_excel'")
    con.execute("DELETE FROM clientes WHERE id NOT IN (SELECT DISTINCT cliente_id FROM pedidos WHERE cliente_id IS NOT NULL) AND airtable_id IS NULL")

    wb = openpyxl.load_workbook(ARCHIVO, read_only=True, data_only=True)
    ws = wb["VENTAS"]
    filas = list(ws.iter_rows(values_only=True))
    hdr = [str(h).strip().upper() if h else "" for h in filas[0]]
    col = {h: i for i, h in enumerate(hdr)}
    idx = lambda name: col.get(name)

    # Agrupar por (fecha, cliente)
    grupos = collections.OrderedDict()
    descartadas, prod_desconocidos, pago_desconocidos = [], collections.Counter(), collections.Counter()
    for n, r in enumerate(filas[1:], start=2):
        if not any(v is not None for v in r[:9]):
            continue
        fecha = parse_fecha(r[idx("FECHA")])
        cliente = limpiar_nombre(r[idx("CLIENTE")])
        sku = limpiar_nombre(r[idx("SKU")]).upper()
        if not fecha or not cliente or not sku:
            descartadas.append((n, r[:9]))
            continue
        precio = num(r[idx("PRECIO")])
        cant = num(r[idx("CANTIDAD")], 1) or 1
        fact = num(r[idx("FACTURACION")], precio * cant)
        pago = limpiar_nombre(r[idx("FORMA DE PAGO")]).upper()
        orden_nro = r[idx("ORDEN #")] if idx("ORDEN #") is not None else None
        key = (fecha, clave_cliente(cliente))
        grupos.setdefault(key, {"fecha": fecha, "cliente": cliente, "lineas": [], "orden_nro": orden_nro})
        grupos[key]["lineas"].append((sku, precio, cant, fact, pago, n))

    # Clientes existentes (por nombre normalizado)
    clientes = {clave_cliente(r["nombre"]): r["id"] for r in con.execute("SELECT id,nombre FROM clientes")}

    creados = 0
    for (fecha, ck), g in grupos.items():
        if ck not in clientes:
            cur = con.execute("INSERT INTO clientes (nombre, canal_origen, creado_en) VALUES (?,?,?)",
                              (g["cliente"], "whatsapp", fecha.isoformat()))
            clientes[ck] = cur.lastrowid
        cid = clientes[ck]

        subtotal = delivery = 0.0
        items, pagos_por_metodo = [], collections.OrderedDict()
        for sku, precio, cant, fact, pago, n in g["lineas"]:
            pid = alias_prod.get(sku)
            if pid is None:
                prod_desconocidos[sku] += 1
            if pid == delivery_id:
                delivery += fact
            else:
                subtotal += fact
                items.append((pid, None if pid else sku, cant, precio, fact))
            mid = alias_pago.get(pago) if pago else None
            if pago and mid is None:
                pago_desconocidos[pago] += 1
            pagos_por_metodo[mid or sin_id] = pagos_por_metodo.get(mid or sin_id, 0) + fact

        total = subtotal + delivery
        canal = "whatsapp"
        nombres_pago = [metodo_info[m]["nombre"] for m in pagos_por_metodo]
        if "Cashea" in nombres_pago: canal = "cashea"
        elif "Vidapets (consignación)" in nombres_pago: canal = "vidapets"
        elif "Duwu" in nombres_pago: canal = "duwu"

        cur = con.execute("""INSERT INTO pedidos (fecha, cliente_id, canal, estado, tipo_entrega, fecha_entrega,
                             subtotal, delivery, total, origen, notas, creado_en)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (fecha.isoformat(), cid, canal, "entregado",
                           "delivery_caracas" if delivery > 0 else None, fecha.isoformat(),
                           round(subtotal, 2), round(delivery, 2), round(total, 2), "importado_excel",
                           f"Orden Excel #{g['orden_nro']}" if g["orden_nro"] else None, fecha.isoformat()))
        pedido_id = cur.lastrowid
        con.execute("UPDATE pedidos SET numero=? WHERE id=?", (f"P-{fecha.year}-{pedido_id:05d}", pedido_id))
        for pid, desc, cant, precio, fact in items:
            con.execute("INSERT INTO pedido_items (pedido_id, producto_id, descripcion, cantidad, precio_unitario, total) VALUES (?,?,?,?,?,?)",
                        (pedido_id, pid, desc, cant, precio, round(fact, 2)))
        for mid, monto in pagos_por_metodo.items():
            info = metodo_info[mid]
            con.execute("INSERT INTO pagos (pedido_id, fecha, metodo_id, caja_id, monto_usd, moneda, confirmado) VALUES (?,?,?,?,?,?,1)",
                        (pedido_id, fecha.isoformat(), mid, info["caja_id"], round(monto, 2), info["moneda_recibida"]))
        if canal == "cashea":
            con.execute("INSERT INTO cashea_ordenes (pedido_id, monto_total, estado_cobro, notas) VALUES (?,?,?,?)",
                        (pedido_id, round(total, 2), "desconocido", "Importado del Excel; sin detalle de cuotas"))
        creados += 1

    con.commit()
    print(f"Pedidos creados: {creados}  |  Clientes: {len(clientes)}  |  Filas descartadas: {len(descartadas)}")
    if prod_desconocidos:
        print("Productos NO reconocidos (quedan como texto libre):", dict(prod_desconocidos))
    if pago_desconocidos:
        print("Formas de pago NO reconocidas (quedan como 'Sin identificar'):", dict(pago_desconocidos))
    if descartadas:
        print("Ejemplos de filas descartadas (fecha o cliente inválidos):")
        for n, r in descartadas[:8]:
            print("   fila", n, r)
    con.close()


if __name__ == "__main__":
    main()
