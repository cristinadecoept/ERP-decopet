"""Conexión a la base de datos y datos iniciales (catálogo, cajas, métodos de pago)."""
import os, sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DECOPET_DB", BASE / "data" / "decopet.db"))


def conectar() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def inicializar():
    con = conectar()
    con.executescript((BASE / "app" / "schema.sql").read_text())
    sembrar(con)
    con.commit()
    con.close()


# ---------------------------------------------------------------
# CATÁLOGO: (sku, nombre, categoria, precio, activo, es_repuesto, es_porche_pro, orden, [alias...])
# ---------------------------------------------------------------
PRODUCTOS = [
    ("PRO-G",   "Porche PRO Grande (90x60)",        "porche",   93,  1, 0, 1, 10, ["PRO GRANDE", "PORCHE L", "PORCHE GRANDE"]),
    ("PRO-M",   "Porche PRO Mediano (68x48)",       "porche",   83,  1, 0, 1, 11, ["PRO MEDIANO", "PORCHE M", "PORCHE MEDIANO"]),
    ("BAS-G",   "Porche Básico Grande",             "porche",   40,  1, 0, 0, 20, ["BASICO L", "BOX L", "BASICO GRANDE"]),
    ("BAS-M",   "Porche Básico Mediano",            "porche",   40,  1, 0, 0, 21, ["BASICO M", "BOX M", "BASICO MEDIANO"]),
    ("BOX-2023","Box de grama (versión original 2023)", "porche", None, 0, 0, 0, 29, ["BOX"]),
    ("REP-G",   "Repuesto Grande",                  "repuesto", 22,  1, 1, 0, 30, ["REPUESTO L", "REPUESTO GRANDE"]),
    ("REP-M",   "Repuesto Mediano",                 "repuesto", 22,  1, 1, 0, 31, ["REPUESTO M", "REPUESTO MEDIANO"]),
    ("PACK3-G", "Pack 3 Repuestos Grande",          "repuesto", 54,  1, 1, 0, 32, ["PACK 3 REPUESTOS GRANDE"]),
    ("PACK3-M", "Pack 3 Repuestos Mediano",         "repuesto", 54,  1, 1, 0, 33, ["PACK 3 REPUESTOS MEDIANO"]),
    ("PACK4",   "Pack 4 Repuestos",                 "repuesto", None, 0, 1, 0, 34, ["PACK 4 REPUESTOS"]),
    ("PACK8",   "Pack 8 Repuestos",                 "repuesto", None, 0, 1, 0, 35, ["PACK 8 REPUESTOS"]),
    ("RAMPA-N", "Rampa Nueva (hasta 50kg)",         "rampa",    98,  1, 0, 0, 40, ["RAMPA NUEVA"]),
    ("RAMPA-MINI","Rampa Mini (hasta 10kg)",        "rampa",    75,  1, 0, 0, 41, ["MINI", "RAMPA MINI"]),
    ("RAMPA-V", "Rampa (versión anterior)",         "rampa",    None, 0, 0, 0, 49, ["RAMPA", "RAMPA 2", "RAMPA 2.0", "RAMPA VIEJA"]),
    ("COM-10",  "Comedor 10cm",                     "comedor",  56,  1, 0, 0, 50, ["COMEDOR 10CM"]),
    ("COM-15",  "Comedor 15cm",                     "comedor",  56,  1, 0, 0, 51, ["COMEDOR 15CM"]),
    ("COM-20",  "Comedor 20cm",                     "comedor",  56,  1, 0, 0, 52, ["COMEDOR 20CM"]),
    ("COM",     "Comedor (talla sin especificar)",  "comedor",  56,  0, 0, 0, 53, ["COMEDOR", "COMEDORE", "COMEDOR PP", "MESA 1", "MESA 2", "MESA 3", "MESA 4", "XS"]),
    ("BAR-25",  "El Bar 25cm",                      "comedor",  56,  1, 0, 0, 55, ["BAR 25CM"]),
    ("BAR-30",  "El Bar 30cm",                      "comedor",  56,  1, 0, 0, 56, ["BAR 30CM"]),
    ("BAR",     "El Bar (talla sin especificar)",   "comedor",  56,  0, 0, 0, 57, ["BAR", "BAR XL"]),
    ("SLOW-10", "Slow Chow 10cm",                   "comedor",  56,  1, 0, 0, 60, ["SLOW CHOW 10CM"]),
    ("SLOW-15", "Slow Chow 15cm",                   "comedor",  56,  1, 0, 0, 61, ["SLOW CHOW 15CM"]),
    ("SLOW-20", "Slow Chow 20cm",                   "comedor",  56,  1, 0, 0, 62, ["SLOW CHOW 20CM"]),
    ("SLOW-25", "Slow Chow 25cm",                   "comedor",  56,  1, 0, 0, 63, ["SLOW CHOW 25CM"]),
    ("SLOW-30", "Slow Chow 30cm",                   "comedor",  56,  1, 0, 0, 64, ["SLOW CHOW 30CM"]),
    ("MALLA",   "Malla",                            "extra",    20,  1, 0, 0, 70, []),
    ("PERSO",   "Personalización (nombre)",         "extra",    10,  1, 0, 0, 71, ["PERSONALIZACION", "PERSONAL", "PERSONA", "PERSONALIZA", "PERSONALIZ", "GRABADO"]),
    ("PLATOS",  "Platos de acero (par)",            "extra",    None, 1, 0, 0, 72, ["PLATO"]),
    ("CAJA-MAD","Caja de madera",                   "extra",    None, 1, 0, 0, 73, ["CAJA DE MADERA"]),
    ("BANDANA", "Bandana (descontinuada)",          "extra",    None, 0, 0, 0, 80, []),
    ("SPRAY",   "Spray (descontinuado)",            "extra",    None, 0, 0, 0, 81, []),
    ("LASER",   "Láser (descontinuado)",            "extra",    None, 0, 0, 0, 82, []),
    ("DELIVERY","Delivery",                         "servicio", None, 1, 0, 0, 90, ["DELIVERY CASHEA", "ENVIO"]),
    ("PROPINA", "Propina",                          "servicio", None, 1, 0, 0, 91, ["TIP"]),
    ("DIFERENCIA","Diferencia / ajuste",            "servicio", None, 1, 0, 0, 92, []),
    ("CONSIG",  "Consignación (ajuste)",            "servicio", None, 0, 0, 0, 93, ["CONSIGNACION", "CONSGINACION"]),
]

CAJAS = [  # (nombre, moneda, orden)
    ("Caja Cris", "USD", 1),
    ("Juan Despachos", "USD", 2),
    ("Zelle Decopet", "USD", 3),
    ("Binance Cripto Investment", "USDT", 4),
    ("Caja USDT", "USDT", 5),
    ("Pago Móvil BVC", "VES", 6),
    ("Folionet Stock Investment", "USD", 7),
    ("Cuentas por Cobrar", "USD", 8),
    ("Mercado Pago", "USD", 9),
    ("PayPal", "USD", 10),
    ("Facebank", "USD", 11),
    ("Pipol Pay", "USD", 12),
    ("Venmo", "USD", 13),
    ("Efectivo EUR", "EUR", 14),
]

METODOS = [  # (nombre, moneda_recibida, caja, orden, [alias...])
    ("Pago Móvil",     "VES",  "Pago Móvil BVC", 1, ["PAGO MOVIL", "PAGOMOVIL", "PM", "CASH PM", "PASO 3PM", "PASO 3PM 80", "PASO 43 PM", "PASO 6PM", "PASO 5PM", "PASO 13PM"]),
    ("Efectivo USD",   "USD",  "Caja Cris",      2, ["CASH", "EFECTIVO", "CASH JUAN", "EFECTIVO USD"]),
    ("Zelle",          "USD",  "Zelle Decopet",  3, []),
    ("Cashea",         "VES",  "Pago Móvil BVC", 4, ["BNC CASHEA BOLOS", "CASHEA BOLOS"]),
    ("Binance USDT",   "USDT", "Caja USDT",      5, ["USDT BINANCE", "BINANCE", "BINAN", "USDT"]),
    ("Pipol Pay",      "USD",  "Pipol Pay",      6, ["PIPOLPAY"]),
    ("Venmo",          "USD",  "Venmo",          7, []),
    ("PayPal",         "USD",  "PayPal",         8, ["PAY PAL"]),
    ("Facebank",       "USD",  "Facebank",       9, []),
    ("Bizum",          "EUR",  "Efectivo EUR",  10, []),
    ("Efectivo EUR",   "EUR",  "Efectivo EUR",  11, ["EUROS"]),
    ("Mercado Pago",   "USD",  "Mercado Pago",  12, []),
    ("Vidapets (consignación)", "USD", "Cuentas por Cobrar", 20, ["VIDAPETS"]),
    ("Duwu",           "USD",  "Cuentas por Cobrar", 21, []),
    ("Por cobrar",     "USD",  "Cuentas por Cobrar", 30, ["CREDITO", "ANOTADO", "ABONADOS"]),
    ("Sin identificar","USD",  None,             99, []),
]

DESPACHADORES = [("Ingrid", "motorizado"), ("Juan", "motorizado"), ("Motorizado externo", "motorizado"),
                 ("Isaías", "interno"), ("Manawa", "interno"), ("Tealca", "agencia"), ("MRW", "agencia"), ("Zoom", "agencia")]

CATEGORIAS_GASTO = {
    "Compras / Proveedores": ["Cajas de cartón", "Grama", "Botellones de agua", "Placas personalizadas", "Placas de bambú", "Bowls de acero", "Platos alimentación lenta", "Pega amarilla", "Papel burbuja", "Otros"],
    "Producción": ["Rampas viejas", "Rampas nuevas", "Comedor Slow Chow", "Comedor tradicional", "El Bar", "Caja mediana", "Caja grande", "Otro"],
    "Sueldos": ["Pago semanal", "Pago quincena", "Pago mensual"],
    "Servicios": ["Pago alquiler", "Mantenimiento general", "Mantenimiento jardín", "Comisión Amerant", "Línea telefónica (personal)", "Línea telefónica (Decopet)", "Otros servicios"],
    "Publicidad / Marketing": ["Contenido para redes", "Material publicitario", "Meta Ads", "Otros marketing"],
    "Envíos / Logística": ["Courier puerta a puerta", "Despachos en Caracas", "Empaquetado por agencia", "Otros envíos"],
    "Plataformas (Shopify/Cashea/Meta)": ["Cashea", "Meta", "Shopify", "ChatGPT", "CapCut", "KAI (bot Tina)", "Otros"],
    "Impuestos (SENIAT/IVA)": ["IVA (SENIAT)", "Compromisos SENIAT", "Otros impuestos"],
    "Carro (Mantenimiento)": ["Mantenimiento", "Repuestos"],
    "Gasolina": ["Gasolina"],
    "Extras / Otros": ["Otros"],
}


def sembrar(con):
    """Carga catálogo, cajas y métodos de pago solo si no existen (se puede correr muchas veces)."""
    for sku, nombre, cat, precio, activo, es_rep, es_pro, orden, aliases in PRODUCTOS:
        con.execute("""INSERT INTO productos (sku,nombre,categoria,precio,activo,es_repuesto,es_porche_pro,orden)
                       VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(sku) DO UPDATE SET nombre=excluded.nombre, categoria=excluded.categoria,
                       activo=excluded.activo, es_repuesto=excluded.es_repuesto, es_porche_pro=excluded.es_porche_pro, orden=excluded.orden""",
                    (sku, nombre, cat, precio, activo, es_rep, es_pro, orden))
        pid = con.execute("SELECT id FROM productos WHERE sku=?", (sku,)).fetchone()[0]
        for a in set(aliases + [sku, nombre.upper()]):
            con.execute("INSERT OR REPLACE INTO producto_alias (alias, producto_id) VALUES (?,?)", (a.strip().upper(), pid))

    for nombre, moneda, orden in CAJAS:
        con.execute("INSERT INTO cajas (nombre,moneda,orden) VALUES (?,?,?) ON CONFLICT(nombre) DO UPDATE SET moneda=excluded.moneda, orden=excluded.orden",
                    (nombre, moneda, orden))

    for nombre, moneda, caja, orden, aliases in METODOS:
        caja_id = con.execute("SELECT id FROM cajas WHERE nombre=?", (caja,)).fetchone()[0] if caja else None
        con.execute("""INSERT INTO metodos_pago (nombre,moneda_recibida,caja_id,orden) VALUES (?,?,?,?)
                       ON CONFLICT(nombre) DO UPDATE SET moneda_recibida=excluded.moneda_recibida, caja_id=excluded.caja_id, orden=excluded.orden""",
                    (nombre, moneda, caja_id, orden))
        mid = con.execute("SELECT id FROM metodos_pago WHERE nombre=?", (nombre,)).fetchone()[0]
        for a in set(aliases + [nombre.upper()]):
            con.execute("INSERT OR REPLACE INTO metodo_pago_alias (alias, metodo_id) VALUES (?,?)", (a.strip().upper(), mid))

    for nombre, tipo in DESPACHADORES:
        con.execute("INSERT OR IGNORE INTO despachadores (nombre,tipo) VALUES (?,?)", (nombre, tipo))

    import json
    con.execute("INSERT OR REPLACE INTO configuracion (clave,valor) VALUES ('categorias_gasto',?)", (json.dumps(CATEGORIAS_GASTO, ensure_ascii=False),))
    con.execute("INSERT OR IGNORE INTO configuracion (clave,valor) VALUES ('dias_ciclo_repuesto','35')")


if __name__ == "__main__":
    inicializar()
    print("Base de datos lista en", DB_PATH)
