"""Importa 'DECOPET - Historial de Ventas.xlsx' (hoja VENTAS, desde marzo 2023) a la plataforma.
Cada fila del Excel es una línea; filas del mismo día y cliente = una orden. Se puede repetir: borra lo importado antes."""
import sys, re, datetime, collections, warnings, sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openpyxl
warnings.filterwarnings("ignore")
DB = Path(__file__).resolve().parent / "data" / "plataforma.db"
ARCHIVO = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else Path.home() / "Downloads" / "DECOPET - Historial de Ventas.xlsx"

# nombre viejo en el Excel → SKU del catálogo actual (None = queda como texto, sin producto)
ALIAS = {
    "PRO GRANDE": "PRO-G", "PORCHE L": "PRO-G", "PORCHE GRANDE": "PRO-G", "PRO MEDIANO": "PRO-M", "PORCHE M": "PRO-M", "PORCHE MEDIANO": "PRO-M",
    "REPUESTO L": "REP-G", "REPUESTO GRANDE": "REP-G", "REPUESTO M": "REP-M", "REPUESTO MEDIANO": "REP-M",
    "PACK 3 REPUESTOS GRANDE": "PACK3-G", "PACK 3 REPUESTOS MEDIANO": "PACK3-M",
    "BASICO L": "BAS-G", "BOX L": "BAS-G", "BASICO M": "BAS-M", "BOX M": "BAS-M", "BOX": "BOX-2023",
    "RAMPA NUEVA": "RAMPA-N", "MINI": "RAMPA-MINI", "RAMPA MINI": "RAMPA-MINI", "RAMPA": "RAMPA-V", "RAMPA 2": "RAMPA-V", "RAMPA 2.0": "RAMPA-V", "RAMPA VIEJA": "RAMPA-V",
    "COMEDOR 10CM": "COM-10", "COMEDOR 15CM": "COM-15", "COMEDOR 20CM": "COM-20", "COMEDOR": "COM-SE", "COMEDORE": "COM-SE", "COMEDOR PP": "COM-SE", "MESA 1": "COM-SE", "MESA 2": "COM-SE", "MESA 3": "COM-SE", "MESA 4": "COM-SE", "XS": "COM-SE",
    "BAR 25CM": "BAR-25", "BAR 30CM": "BAR-30", "BAR": "BAR-SE", "BAR XL": "BAR-SE",
    "SLOW CHOW 10CM": "SLOW-10", "SLOW CHOW 15CM": "SLOW-15", "SLOW CHOW 20CM": "SLOW-20", "SLOW CHOW 25CM": "SLOW-25", "SLOW CHOW 30CM": "SLOW-30",
    "MALLA": "MALLA", "PERSONALIZACION": "OPC-PERSO", "PERSONAL": "OPC-PERSO", "PERSONA": "OPC-PERSO", "PERSONALIZA": "OPC-PERSO", "PERSONALIZ": "OPC-PERSO", "GRABADO": "OPC-PERSO",
    "PLATOS": "PLATOS-2023", "PLATO": "PLATOS-2023", "PACK 4 REPUESTOS": "PACK4", "PACK 8 REPUESTOS": "PACK8",
}
DESCONTINUADOS = [  # sku, nombre, categoria (entran al catálogo como inactivos para que el historial cuadre)
    ("BAS-G", "porche Básico Grande", "porche"), ("BAS-M", "porche Básico Mediano", "porche"), ("BOX-2023", "Box de grama (2023)", "porche"),
    ("RAMPA-V", "Rampa (versión anterior)", "rampa"), ("COM-SE", "Comedor (talla sin especificar)", "comedor"), ("BAR-SE", "El Bar (talla sin especificar)", "comedor"),
    ("SLOW-25", "Slow Chow 25cm", "comedor"), ("PLATOS-2023", "Platos de acero (par)", "accesorio"), ("PACK4", "Pack 4 Repuestos", "repuesto"), ("PACK8", "Pack 8 Repuestos", "repuesto"),
]
PAGOS = {  # forma en el Excel → (forma actual, canal)
    "PAGO MOVIL": ("Pago Móvil", None), "PAGOMOVIL": ("Pago Móvil", None), "CASH PM": ("Pago Móvil", None), "PASO 3PM": ("Pago Móvil", None), "PASO 3PM 80": ("Pago Móvil", None), "PASO 43 PM": ("Pago Móvil", None), "PASO 6PM": ("Pago Móvil", None), "PASO 5PM": ("Pago Móvil", None), "PASO 13PM": ("Pago Móvil", None),
    "CASH": ("Efectivo USD", None), "EFECTIVO": ("Efectivo USD", None), "CASH JUAN": ("Efectivo USD", None), "ZELLE": ("Zelle", None),
    "CASHEA": ("BNC", "cashea"), "BNC CASHEA BOLOS": ("BNC", "cashea"), "CASHEA BOLOS": ("BNC", "cashea"),
    "USDT BINANCE": ("Binance USDT", None), "BINANCE": ("Binance USDT", None), "BINAN": ("Binance USDT", None), "PIPOL PAY": ("Pipol Pay", None), "VENMO": ("Venmo", None),
    "PAY PAL": ("PayPal", None), "PAYPAL": ("PayPal", None), "FACEBANK": ("Facebank", None), "VIDAPETS": ("Consignación Vidapets", "vidapets"), "DUWU": ("Transferencia (Duwu)", "duwu"),
    "CREDITO": ("Crédito", None), "ANOTADO": ("Crédito", None), "ABONADOS": ("Crédito", None),
}


def parse_fecha(v):
    if isinstance(v, datetime.datetime): return v.date() if 2022 <= v.year <= 2027 else None
    if isinstance(v, datetime.date): return v
    s = str(v or "").strip()
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", s) or re.match(r"^(\d{1,2})[/\-.](\d{2})(\d{2})$", s)
    if not m: return None
    d, mo, y = int(m[1]), int(m[2]), int(m[3]); y = y + 2000 if y < 100 else y
    if not (2022 <= y <= 2027): return None
    try: return datetime.date(y, mo, d)
    except ValueError: return None


def num(v, d=0.0):
    try: return float(v)
    except (TypeError, ValueError): return d


def main():
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    for sku, nombre, cat in DESCONTINUADOS:
        con.execute("INSERT OR IGNORE INTO productos (sku,nombre,categoria,precio,costo,activo,orden,tipo) VALUES (?,?,?,NULL,NULL,0,900,'producto')", (sku, nombre, cat))
    skus = {r["sku"]: r["id"] for r in con.execute("SELECT id, sku FROM productos")}
    con.execute("DELETE FROM ordenes WHERE origen_excel=1")
    con.execute("DELETE FROM clientes WHERE origen_excel=1 AND id NOT IN (SELECT DISTINCT cliente_id FROM ordenes WHERE cliente_id IS NOT NULL)")

    ws = openpyxl.load_workbook(ARCHIVO, read_only=True, data_only=True)["VENTAS"]
    filas = list(ws.iter_rows(values_only=True)); hdr = [str(h).strip().upper() if h else "" for h in filas[0]]; col = {h: i for i, h in enumerate(hdr)}
    grupos = collections.OrderedDict(); desconocidos = collections.Counter(); descartadas = 0
    for r in filas[1:]:
        if not any(v is not None for v in r[:9]): continue
        fecha = parse_fecha(r[col["FECHA"]]); cliente = re.sub(r"\s+", " ", str(r[col["CLIENTE"]] or "")).strip(); sku = str(r[col["SKU"]] or "").strip().upper()
        if not fecha or not cliente or not sku: descartadas += 1; continue
        precio, cant = num(r[col["PRECIO"]]), num(r[col["CANTIDAD"]], 1) or 1
        fact = num(r[col["FACTURACION"]], precio * cant); pago = str(r[col["FORMA DE PAGO"]] or "").strip().upper()
        grupos.setdefault((fecha, re.sub(r"[^a-z0-9]", "", cliente.lower())), {"fecha": fecha, "cliente": cliente, "lineas": []})["lineas"].append((sku, precio, cant, fact, pago))

    clientes = {re.sub(r"[^a-z0-9]", "", r["nombre"].lower()): r["id"] for r in con.execute("SELECT id, nombre FROM clientes")}
    n = 0
    for (fecha, ck), g in grupos.items():
        if ck not in clientes:
            partes = g["cliente"].split(" ", 1)
            cur = con.execute("INSERT INTO clientes (nombre_pila, apellido, nombre, canal_habitual, origen_excel, creado_en) VALUES (?,?,?,?,1,?)",
                              (partes[0], partes[1] if len(partes) > 1 else None, g["cliente"], "whatsapp", f"{fecha.isoformat()} 12:00"))
            clientes[ck] = cur.lastrowid
        cid = clientes[ck]
        subtotal = delivery = 0.0; lineas = []; formas = collections.OrderedDict(); canal = "whatsapp"
        for sku, precio, cant, fact, pago in g["lineas"]:
            if sku in ("DELIVERY", "DELIVERY CASHEA", "ENVIO"): delivery += fact; continue
            pid = skus.get(ALIAS.get(sku)) if ALIAS.get(sku) else None
            if pid is None and sku not in ("TIP", "PROPINA", "DIFERENCIA", "CONSIGNACION", "CONSGINACION"): desconocidos[sku] += 1
            nombre = con.execute("SELECT nombre FROM productos WHERE id=?", (pid,)).fetchone()[0] if pid else sku.title()
            lineas.append((pid, nombre, cant, precio, fact)); subtotal += fact
            forma, c = PAGOS.get(pago, (pago.title() if pago else "Sin identificar", None))
            if c: canal = c
            formas[forma] = formas.get(forma, 0) + fact
        total = round(subtotal + delivery, 2)
        n += 1
        cur = con.execute("""INSERT INTO ordenes (numero, tipo, cliente_id, canal, creada_por, estado, estado_pago, subtotal, delivery, total, tipo_entrega, fecha_prometida, fecha_entrega,
                             forma_pago_prevista, costo_productos, origen_excel, creado_en, actualizado_en) VALUES (?,?,?,?,1,'entregada','pagada',?,?,?,?,?,?,?,NULL,1,?,?)""",
                          (f"#{n}", "venta", cid, canal, round(subtotal, 2), round(delivery, 2), total, "delivery" if delivery > 0 else None, fecha.isoformat(), f"{fecha.isoformat()} 12:00",
                           " + ".join(formas.keys()), f"{fecha.isoformat()} 12:00", f"{fecha.isoformat()} 12:00"))
        oid = cur.lastrowid
        for pid, nombre, cant, precio, fact in lineas:
            con.execute("INSERT INTO orden_lineas (orden_id, producto_id, nombre, cantidad, precio, total) VALUES (?,?,?,?,?,?)", (oid, pid, nombre, cant, precio, round(fact, 2)))
        for forma, monto in formas.items():
            con.execute("INSERT INTO pagos (orden_id, forma, monto_usd, fecha, estado, confirmado_por, confirmado_en) VALUES (?,?,?,?,'confirmado',1,?)", (oid, forma, round(monto + (delivery if forma == list(formas)[0] else 0), 2), f"{fecha.isoformat()} 12:00", f"{fecha.isoformat()} 12:00"))
    con.commit()
    print(f"Historial importado: {n} órdenes · {len(clientes)} clientes · {descartadas} filas descartadas")
    if desconocidos: print("Sin producto en el catálogo (quedan como texto):", dict(desconocidos))


if __name__ == "__main__":
    main()
