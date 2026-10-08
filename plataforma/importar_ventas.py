"""Migra SOLO la hoja VENTAS del Excel "DECOPET - Historial de Ventas" al Registro de ventas.
Uso:  ./.venv/bin/python -m plataforma.importar_ventas "/ruta/al/archivo.xlsx" [--cargar]
Sin --cargar solo revisa y muestra el resumen; con --cargar escribe en la base."""
import sys, re, datetime, sqlite3, collections
from pathlib import Path
import openpyxl

DB = Path(__file__).parent / "data" / "plataforma.db"

# Productos viejos que hay que crear (sku, nombre, categoria, activo)
NUEVOS = [("BAS-M", "Porche Básico Mediano", "porche", 0), ("BAS-G", "Porche Básico Grande", "porche", 0), ("BAS", "Porche Básico", "porche", 0),
          ("PACK4", "Pack 4 Repuestos", "repuesto", 1), ("PACK8", "Pack 8 Repuestos", "repuesto", 1), ("BANDANA", "Bandana", "accesorio", 0),
          ("SPRAY", "Spray", "accesorio", 0), ("CAMA", "Cama", "accesorio", 0), ("ARNES", "Arnés", "accesorio", 0), ("CAJA-MADERA", "Caja de madera", "accesorio", 0),
          ("COLLAB", "Collab", "otro", 0), ("PROPINA", "Propina", "otro", 0), ("DIFERENCIA", "Diferencia", "otro", 0), ("DELIVERY", "Delivery", "servicio", 1), ("OTRO", "Otro", "otro", 0)]

MAPA = {"PORCHE M": "PRO-M", "PORCHE L": "PRO-G", "PRO MEDIANO": "PRO-M", "PRO GRANDE": "PRO-G",
        "BOX": "BAS", "BASICO M": "BAS-M", "BASICO L": "BAS-G",
        "BOX M": "REP-M", "BOX L": "REP-G", "REPUESTO M": "REP-M", "REPUESTO L": "REP-G",
        "PACK 3 REPUESTOS MEDIANO": "PACK3-M", "PACK 3 REPUESTOS GRANDE": "PACK3-G", "PACK 4 REPUESTOS": "PACK4", "PACK 8 REPUESTOS": "PACK8",
        "MALLA": "MALLA", "RAMPA": "RAMPA-MINI", "RAMPA VIEJA": "RAMPA-MINI", "RAMPA NUEVA": "RAMPA-N", "RAMPA 2": "RAMPA-N", "RAMPA 2.0": "RAMPA-N",
        "COMEDOR": "COM-20", "COMEDORE": "COM-20", "COMEDOR PP": "COM-20", "COMEDOR 20CM": "COM-20", "COMEDOR 15CM": "COM-15", "MINI": "COM-15", "COMEDOR 10CM": "COM-10", "XS": "COM-10",
        "BAR": "BAR-25", "BAR 25CM": "BAR-25", "BAR XL": "BAR-30", "BAR 30CM": "BAR-30",
        "MESA 1": "SLOW-10", "MESA 2": "SLOW-15", "MESA 3": "SLOW-20", "MESA 4": "SLOW-30",
        "SLOW CHOW 10CM": "SLOW-10", "SLOW CHOW 15CM": "SLOW-15", "SLOW CHOW 20CM": "SLOW-20", "SLOW CHOW 25CM": "SLOW-20", "SLOW CHOW 30CM": "SLOW-30",
        "PLATOS": "BOWL-M", "PLATO": "BOWL-M", "BANDANA": "BANDANA", "SPRAY": "SPRAY", "CAMA": "CAMA", "ARNES": "ARNES", "CAJA DE MADERA": "CAJA-MADERA", "COLLAB": "COLLAB",
        "TIP": "PROPINA", "PROPINA": "PROPINA", "DIFERENCIA": "DIFERENCIA", "CONSGINACION": "OTRO", "VENMO": "OTRO",
        "DELIVERY": "DELIVERY", "DELIVERY CASHEA": "DELIVERY",
        "LASER": "OPC-PERSO", "PERSONAL": "OPC-PERSO", "PERSONALIZACION": "OPC-PERSO", "PERSONA": "OPC-PERSO", "PERSONALIZA": "OPC-PERSO", "PERSONALIZ": "OPC-PERSO", "GRABADO": "OPC-PERSO"}

PAGOS = {"PAGO MOVIL": "Pago Móvil", "CASH": "Efectivo USD", "CASH JUAN": "Efectivo USD", "CASH PM": "Efectivo USD", "ZELLE": "Zelle",
         "BNC CASHEA BOLOS": "Cashea BNC", "CASHEA": "Cashea BNC", "USDT BINANCE": "Binance USDT", "BINANCE": "Binance USDT", "BINAN": "Binance USDT",
         "PIPOL PAY": "Pipol Pay", "VENMO": "Venmo", "PAY PAL": "PayPal", "PAYPAL": "PayPal", "FACEBANK": "Facebank", "VIDAPETS": "Transferencia USD"}
CUENTA = {"Pago Móvil": "Pago Movil BVC", "Efectivo USD": "Caja", "Zelle": "Zelle", "Cashea BNC": "Cashea BNC", "Binance USDT": "Caja USDT",
          "Pipol Pay": "Pipol Pay", "Venmo": "Venmo", "PayPal": "Pay Pal", "Facebank": "Facebank", "Transferencia USD": "Caja"}

SIN_NOMBRE = {"", "XX", "XXXXXXXX", "SIN NOMBRE", "X", "XXX", "XXXX", "?", "-"}


def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def fecha_de(v):
    if isinstance(v, datetime.datetime): d = v.date()
    elif isinstance(v, str):
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", v) or re.match(r"\s*(\d{1,2})/(\d{2})(\d{2})\s*$", v)   # también "24/0323" (sin la segunda barra)
        if not m: return None
        dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3)); yy = yy + 2000 if yy < 100 else yy
        try: d = datetime.date(yy, mm, dd)
        except ValueError: return None
    else: return None
    return d if 2022 <= d.year <= 2026 else None


def leer(ruta):
    ws = openpyxl.load_workbook(ruta, data_only=True, read_only=True)["VENTAS"]
    filas = []; ultima = None; avisos = collections.defaultdict(list)
    for i, r in enumerate(ws.iter_rows(values_only=True), start=1):
        if i == 1: continue
        if not any(x not in (None, "") for x in r[:9]): continue
        f = fecha_de(r[0])
        if f is None:
            avisos["fecha corregida"].append((i, r[0], r[3])); f = ultima or datetime.date(2023, 3, 17)
        ultima = f
        nombre = " ".join(str(r[3] or "").split())
        if nombre.upper() in SIN_NOMBRE: nombre = "Sin nombre"; avisos["sin nombre"].append((i, r[3]))
        sku_x = str(r[4] or "").strip().upper()
        sku = MAPA.get(sku_x)
        if sku is None: avisos["producto sin mapa"].append((i, sku_x)); sku = "OTRO"
        precio = num(r[5]) or 0; cant = num(r[6]) or 0; fact = num(r[7])   # cantidad tal cual (los delivery traen 0)
        if fact is None:   # dos celdas tachadas en el Excel; Cristina confirmó los montos reales (20 sep 2026)
            fact = {2686: 68.0, 2689: 69.0}.get(i, 0.0); avisos["facturación corregida a mano"].append((i, r[7], nombre, fact))
        forma_x = str(r[8] or "").strip().upper(); forma = PAGOS.get(forma_x)
        if forma is None and forma_x: avisos["forma de pago ignorada"].append((i, forma_x))
        filas.append(dict(fila=i, fecha=f, cliente=nombre, sku=sku, sku_x=sku_x, precio=precio, cantidad=cant, total=fact, forma=forma, forma_x=forma_x))
    return filas, avisos


def cargar(filas):
    """Escribe el histórico en registro_ventas (NO crea órdenes ni clientes: Órdenes se llena con el CRM)."""
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS registro_ventas (id INTEGER PRIMARY KEY, fecha TEXT NOT NULL, cliente TEXT, producto TEXT, precio REAL, cantidad REAL DEFAULT 1, facturacion REAL,
                   forma_pago TEXT, origen TEXT DEFAULT 'excel', fila_excel INTEGER, creado_en TEXT DEFAULT (datetime('now','localtime')))""")
    con.execute("UPDATE productos SET nombre='Slow Chow 25cm', descripcion=REPLACE(COALESCE(descripcion,''),'20','25') WHERE sku='SLOW-20'")
    orden_max = con.execute("SELECT COALESCE(MAX(orden),0) FROM productos").fetchone()[0]
    for k, (sku, nombre, cat, act) in enumerate(NUEVOS, start=1):
        con.execute("INSERT OR IGNORE INTO productos (sku,nombre,categoria,precio,tipo,activo,orden) VALUES (?,?,?,?,?,?,?)", (sku, nombre, cat, 0, "producto", act, orden_max + k))
    prod = {r["sku"]: r["nombre"] for r in con.execute("SELECT sku, nombre FROM productos")}
    con.execute("DELETE FROM registro_ventas WHERE origen='excel'")
    for x in filas:
        con.execute("INSERT INTO registro_ventas (fecha,cliente,producto,precio,cantidad,facturacion,forma_pago,origen,fila_excel) VALUES (?,?,?,?,?,?,?,'excel',?)",
                    (x["fecha"].isoformat(), x["cliente"], prod[x["sku"]], x["precio"], x["cantidad"], x["total"], x["forma"] or "Sin registro", x["fila"]))
    con.commit()
    return len(filas)


if __name__ == "__main__":
    ruta = sys.argv[1]; filas, avisos = leer(ruta)
    print(f"Líneas: {len(filas)} · facturación {sum(f['total'] for f in filas):,.2f}")
    for k, v in avisos.items(): print(f"- {k}: {len(v)} → {v[:6]}")
    if "--cargar" in sys.argv:
        n = cargar(filas); print(f"Cargadas {n} líneas en el Registro de ventas")
