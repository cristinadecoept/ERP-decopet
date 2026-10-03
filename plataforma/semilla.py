"""Datos de prueba coherentes (ficticios) para probar la plataforma. Se puede volver a correr: borra y recrea."""
import random, datetime, sqlite3, json, sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = Path(__file__).resolve().parent
DB = Path(os.environ.get("DECOPET_DATOS") or (BASE / "data")) / "plataforma.db"   # la misma base que abre el ERP
R = random.Random(7)
HOY = datetime.date.today()

PRODUCTOS = [  # sku, nombre, categoria, descripcion, precio, precio_par, costo, tipo, requiere_color, permite_malla, permite_personalizacion
    ("PRO-M", "El Porche Versión PRO Mediano", "porche", "68 × 48 cm", 83, None, 33, "producto", 0, 1, 1),
    ("PRO-G", "El Porche Versión PRO Grande", "porche", "90 × 60 cm", 93, None, 38, "producto", 0, 1, 1),
    ("REP-M", "Repuesto Mediano", "repuesto", "68 × 48 cm", 22, None, 9, "producto", 0, 0, 0),
    ("REP-G", "Repuesto Grande", "repuesto", "90 × 60 cm", 22, None, 9.5, "producto", 0, 0, 0),
    ("PACK3-M", "Pack 3 Repuestos Mediano", "repuesto", "3 unidades", 54, None, 27, "producto", 0, 0, 0),
    ("PACK3-G", "Pack 3 Repuestos Grande", "repuesto", "3 unidades", 54, None, 28.5, "producto", 0, 0, 0),
    ("MALLA", "Malla de seguridad", "porche", "también se agrega como opción a El Porche Versión PRO", 20, None, 8, "producto", 0, 0, 0),
    ("RAMPA-N", "Rampa Nueva", "rampa", "88 × 40 × 8 cm · niveles 51/42/32 · hasta 50 kg", 98, None, 45, "producto", 0, 0, 1),
    ("RAMPA-MINI", "Rampa Para Perros Mini", "rampa", "100 × 40 cm · niveles 56/48/27 · hasta 10 kg", 75, None, 35, "producto", 0, 0, 1),
    ("COM-10", "Comedor 10cm", "comedor", None, 56, None, 24, "producto", 0, 0, 1),
    ("COM-15", "Comedor 15cm", "comedor", None, 56, None, 24, "producto", 0, 0, 1),
    ("COM-20", "Comedor 20cm", "comedor", None, 56, None, 25, "producto", 0, 0, 1),
    ("BAR-25", "El Bar Grande", "comedor", None, 56, None, 27, "producto", 0, 0, 1),
    ("BAR-30", "El Bar Gigante", "comedor", None, 56, None, 27, "producto", 0, 0, 1),
    ("SLOW-10", "Slow Chow 10cm", "comedor", "elegir plato azul o rosado", 56, None, 26, "producto", 1, 0, 1),
    ("SLOW-15", "Slow Chow 15cm", "comedor", "elegir plato azul o rosado", 56, None, 26, "producto", 1, 0, 1),
    ("SLOW-20", "Slow Chow 20cm", "comedor", "elegir plato azul o rosado", 56, None, 26, "producto", 1, 0, 1),
    ("SLOW-30", "Slow Chow 30cm", "comedor", "elegir plato azul o rosado", 56, None, 27, "producto", 1, 0, 1),
    ("BOWL-P", "Bowl pequeño", "accesorio", "1 × $10 · 2 × $15", 10, 15, 4, "producto", 0, 0, 0),
    ("BOWL-M", "Bowl mediano", "accesorio", "1 × $10 · 2 × $15", 10, 15, 4.5, "producto", 0, 0, 0),
    ("BOWL-G", "Bowl grande", "accesorio", "1 × $10 · 2 × $15", 10, 15, 5, "producto", 0, 0, 0),
    ("PLATO-AZUL", "Plato azul", "accesorio", "plato de alimentación lenta · 1 × $15 · 2 × $25", 15, 25, 6, "producto", 0, 0, 0),
    ("PLATO-ROSA", "Plato rosado", "accesorio", "plato de alimentación lenta · 1 × $15 · 2 × $25", 15, 25, 6, "producto", 0, 0, 0),
    # Opciones (se agregan a una línea, no se venden como producto)
    ("OPC-PERSO", "Personalización con nombre", "opcion", None, 10, None, 3, "opcion", 0, 0, 0),
    ("OPC-MALLA", "Malla agregada al porche", "opcion", None, 20, None, 8, "opcion", 0, 0, 0),
    # Productos que ya se vendieron en Airtable y el importador de pedidos necesita. Precios: tabla Producto de Airtable.
    # Costos: Airtable no los guarda; son estimados (por analogía con repuestos/porches) y NO sirven para ver márgenes reales.
    ("BAS-M", "El Porche Básico Mediano", "porche", "68 × 48 cm", 38, None, 15, "producto", 0, 0, 1),
    ("BAS-G", "El Porche Básico Grande", "porche", "90 × 60 cm", 38, None, 16, "producto", 0, 0, 1),
    ("PACK4", "Pack 4 Repuestos Cashea", "repuesto", "4 unidades", 88, None, 36, "producto", 0, 0, 0),
    ("PACK8", "Pack 8 Repuestos Cashea", "repuesto", "8 unidades", 176, None, 72, "producto", 0, 0, 0),
]

ZONAS_CCS = ["La Castellana", "Los Palos Grandes", "Prados del Este", "El Hatillo", "La Trinidad", "Chacao", "Las Mercedes",
             "Santa Fe", "La Florida", "Los Naranjos", "Colinas de Bello Monte", "Altamira", "La Boyera", "Macaracuay", "El Cafetal"]
OTRAS = [("Valencia", "Carabobo"), ("Maracaibo", "Zulia"), ("Maracaibo", "Zulia"), ("Los Teques", "Miranda"), ("Guarenas", "Miranda"), ("Lechería", "Anzoátegui"), ("Barquisimeto", "Lara"), ("Mérida", "Mérida"), ("Maracay", "Aragua")]
PERFILES = [  # nombre, tel, ciudad, estado, zona, direccion, canal, forma, tipo_entrega, notas, agencia_preferida, perro(nombre, raza)
    ("Cristina Raffalli", "0414-1234567", "Caracas", "Distrito Capital", "La Castellana", "Res. Los Pinos, apto 4B", "whatsapp", "Pago Móvil", "delivery",
     [("entrega", "Recibe Juan, el conserje. Avisar cuando el motorizado esté llegando. No entregar después de las 6:00 p. m.", 1, 1, 0)], None, ("Toby", "Beagle")),
    ("Harry Styles", "0412-7654321", "Caracas", "Distrito Capital", "Prados del Este", None, "cashea", "BNC", "pickup",
     [("atencion", "Muy detallista con el empaque. Revisar que la caja vaya impecable.", 1, 1, 0)], None, ("Luna", "Golden Retriever")),
    ("Gustavo Cerati", "0424-5551122", "Maracaibo", "Zulia", None, "Retira en Vivero Maracaibo", "whatsapp", "Zelle", "distribuidor",
     [("entrega", "Avisar por WhatsApp cuando el pedido esté en el vivero.", 0, 1, 0)], None, ("Simón", "Labrador")),
    ("Alejandra Pieschacón", "0416-3339900", "Valencia", "Carabobo", None, "Agencia Tealca Valencia Norte", "whatsapp", "Binance USDT", "nacional",
     [("pago", "Paga en USDT desde Binance; el comprobante a veces llega al día siguiente.", 1, 0, 0)], "Tealca", ("Nala", "Salchicha / Dachshund")),
    ("Chappell Roan", "0426-8887766", "La Guaira", "La Guaira", "Caraballeda", "Conj. Res. Playa Azul, casa 7, portón negro frente a la panadería", "whatsapp", "Efectivo USD", "delivery_fuera",
     [("entrega", "Casa con portón negro, frente a la panadería. El perro ladra pero no muerde.", 1, 1, 0)], None, ("Rocky", "Pitbull")),
    ("Mariana Vega", "0414-2223344", "Caracas", "Distrito Capital", "Los Palos Grandes", "Edif. Doral, piso 3, apto 3A", "whatsapp", "Zelle", "delivery", [], None, ("Coco", "Yorkshire")),
    ("Ricardo Hernández", "0412-4445566", "Caracas", "Distrito Capital", "El Hatillo", "Qta. Monte Alto, calle La Cima", "cashea", "BNC", "delivery",
     [("entrega", "Edificio sin ascensor, piso 4. Llamar al llegar, no tocar timbre (bebé durmiendo).", 1, 1, 0)], None, ("Max", "Pastor Alemán")),
    ("Sofía Garmendia", "0424-6667788", "Caracas", "Distrito Capital", "Las Mercedes", "Res. El Rosal, PH-2", "whatsapp", "Pago Móvil", "pickup", [], None, ("Milo", "Pug")),
    ("Luis Dagostini", "0416-8889900", "Caracas", "Distrito Capital", "Santa Fe", "Conj. Res. Vista Hermosa, casa 12", "whatsapp", "Efectivo USD", "delivery", [], None, ("Bruno", "Bóxer")),
    ("Patricia González", "0414-1112233", "Caracas", "Distrito Capital", "Colinas de Bello Monte", "Edif. Las Acacias, apto 7A", "whatsapp", "Pago Móvil", "delivery",
     [("comercial", "Tiene dos perros grandes; solo ha comprado un comedor.", 0, 0, 1)], None, ("Thor", "Rottweiler")),
    ("Daniela Sanz", "0412-3334455", "Caracas", "Distrito Capital", "Altamira", "Res. Altamira Suites, apto 12B", "cashea", "BNC", "pickup", [], None, ("Lola", "Maltés")),
    ("Fernando Rangel", "0424-9990011", "Barquisimeto", "Lara", None, "Agencia MRW Barquisimeto Este", "whatsapp", "Zelle", "nacional", [], "MRW", ("Zeus", "Husky Siberiano")),
    ("Isabella Rojas", "0416-5556677", "Mérida", "Mérida", None, "Agencia Zoom Mérida Centro", "cashea", "BNC", "nacional", [], "Zoom", ("Kira", "Border Collie")),
    ("Eduardo Paz", "0414-7778899", "Lechería", "Anzoátegui", None, "Agencia Tealca Lechería", "whatsapp", "PayPal", "nacional", [], "Tealca", ("Bolt", "Mestizo")),
    ("Natalia Hobaica", "0412-6667788", "Maracay", "Aragua", None, "Agencia MRW Maracay Las Delicias", "whatsapp", "Pago Móvil", "nacional", [], "MRW", ("Canela", "Cocker Spaniel")),
    ("Diego Freites", "0424-1213141", "Los Teques", "Miranda", "El Tambor", "Urb. El Tambor, casa 22", "whatsapp", "Efectivo USD", "delivery_fuera", [], None, ("Firulais", "Mestizo")),
    ("Lucía Requena", "0416-2324252", "Guatire", "Miranda", "Castillejo", "Conj. Res. Castillejo, torre 2, apto 5C", "whatsapp", "Zelle", "delivery_fuera", [], None, ("Pepa", "Bulldog Francés")),
    ("Miguel Atias", "0414-3435363", "Maracaibo", "Zulia", None, "Retira en Vivero Maracaibo", "whatsapp", "Pago Móvil", "distribuidor", [], None, ("Sasha", "Schnauzer")),
    ("Carolina Rodríguez", "0412-4546474", "Caracas", "Distrito Capital", "La Trinidad", "Res. Los Naranjos, casa 4", "shopify", "Zelle", "delivery", [], None, ("Duque", "Gran Danés")),
    ("Jorge Martínez", "0424-5657585", "Caracas", "Distrito Capital", "Chacao", "Edif. Chacao Plaza, apto 9", "whatsapp", "Pago Móvil", "delivery", [], None, ("Pipo", "Chihuahua")),
]
NOMBRES = [p[0] for p in PERFILES]
NOTAS = [
    ("entrega", "Recibe Juan, el conserje. Avisar cuando el motorizado esté llegando. No entregar después de las 6:00 p. m.", 1, 1, 0),
    ("entrega", "Edificio sin ascensor, piso 4. Llamar al llegar, no tocar timbre (bebé durmiendo).", 1, 1, 0),
    ("entrega", "Prefiere entregas en la mañana antes de las 11.", 0, 1, 0),
    ("pago", "Siempre paga por Zelle desde la cuenta de su esposo (Carlos R.).", 1, 0, 0),
    ("atencion", "Cliente muy detallista con el empaque. Revisar que la caja vaya impecable.", 1, 1, 0),
    ("comercial", "Tiene dos perros grandes; solo ha comprado un comedor.", 0, 0, 1),
    ("producto", "Su porche Grande está en un balcón techado; el repuesto le dura ~5 semanas.", 0, 0, 0),
    ("entrega", "Casa con portón negro, frente a la panadería. El perro ladra pero no muerde.", 1, 1, 0),
]
CANALES = ["whatsapp"] * 6 + ["cashea"] * 3 + ["shopify", "duwu"]
FRANJAS = ["10 am – 1 pm", "1 – 4 pm", "4 – 7 pm", "mañana", "tarde"]
DESPACHADORES = ["Juan", "Ingrid", "Juan", "Juan", "Carlos (moto)"]
AGENCIAS = ["Tealca", "MRW", "Zoom"]
FORMAS = {"whatsapp": ["Pago Móvil", "Pago Móvil", "Zelle", "Efectivo USD", "Binance USDT", "Efectivo USD", "Efectivo Bs"], "cashea": ["BNC"], "shopify": ["Zelle"], "duwu": ["Transferencia (Duwu)"]}
CUENTAS = {"Pago Móvil": "Pago Móvil BVC", "Zelle": "Zelle Decopet", "Efectivo USD": "Caja despachador", "Efectivo Bs": "Caja despachador (Bs)", "Efectivo EUR": "Caja EUR", "Binance USDT": "Caja USDT", "BNC": "BNC (Bs)", "Transferencia (Duwu)": "Pago Móvil BVC", "PayPal": "PayPal"}
TASA = 152.4  # se reemplaza por la tasa real si se pudo leer del BCV


def main():
    # En un servidor esta semilla BORRA la base. Solo se permite en el de prueba (DECOPET_STAGING=1).
    if os.environ.get("DECOPET_DATOS") and os.environ.get("DECOPET_STAGING") != "1":
        sys.exit("No: esto borraría la base del servidor. La semilla solo corre en staging (DECOPET_STAGING=1).")
    DB.parent.mkdir(parents=True, exist_ok=True)
    for f in (DB, DB.with_name(DB.name + "-wal"), DB.with_name(DB.name + "-shm")):   # con WAL la base son tres archivos
        if f.exists(): f.unlink()
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    con.executescript((BASE / "modelo.sql").read_text())
    con.executemany("INSERT INTO usuarios (id,nombre,rol) VALUES (?,?,?)", [(1, "Cristina", "admin"), (2, "Vale (logística)", "logistica"), (3, "Tina", "sistema")])
    # Plantilla de cajas de Cristina (códigos y nombres de su cash flow; saldos en cero)
    CAJAS = [("001", "Caja", "USD", "operativa", 0), ("002", "Juan Despachos", "USD", "operativa", 0), ("003", "Zelle Decopet", "USD", "operativa", 0), ("004", "Binance Cripto Investment", "USDT", "inversion", 0),
             ("005", "Caja USDT", "USDT", "operativa", 0), ("006", "Pago Movil BVC", "VES", "operativa", 0), ("007", "Folionet Stock Investment", "USD", "inversion", 0), ("008", "Cuentas Por Cobrar", "USD", "por_cobrar", 0),
             ("009", "Mercado Pago", "USD", "operativa", 0), ("010", "Casa Prados", "USD", "operativa", 0), ("011", "Pay Pal", "USD", "operativa", 0), ("012", "Amerant", "USD", "operativa", 0),
             ("013", "Dolares Argentina", "USD", "operativa", 0), ("014", "Credito Personal", "USD", "por_cobrar", 1), ("015", "Euros", "EUR", "operativa", 0), ("016", "Cuenta Dolares BVC Camila", "USD", "operativa", 0),
             ("017", "Cooper Startup Investment", "USD", "inversion", 0), ("018", "Cashea BNC", "VES", "operativa", 0), ("019", "Facebank", "USD", "operativa", 0), ("020", "WISE", "USD", "operativa", 0),
             ("021", "Comision Zelle Venta", "USD", "operativa", 0), ("022", "Deposito Depto o Credito", "USD", "por_cobrar", 0), ("024", "Polymarket", "USD", "inversion", 0), ("025", "Pesos", "USD", "operativa", 0),
             ("026", "Venmo", "USD", "operativa", 0), ("027", "Pipol Pay", "USD", "operativa", 0)]
    con.executemany("INSERT OR IGNORE INTO cuentas (codigo,nombre,moneda,tipo,personal,orden) VALUES (?,?,?,?,?,CAST(? AS INTEGER))", [(c, n, m, tp, pe, c) for c, n, m, tp, pe in CAJAS])
    CATS = {"Compras y proveedores": ["Grama", "Cajas de cartón", "Placas", "Bowls y platos", "Pega", "Papel burbuja", "Otros"], "Producción": ["porches", "Rampas", "Comedores", "El Bar", "Slow Chow", "Otro"],
            "Sueldos": ["Cristina", "Isaías", "Manawa", "Logística", "Otro"], "Bonos": ["Bono"], "Servicios": ["Luz", "Internet", "Teléfono", "Mantenimiento", "Otro"], "Alquiler": ["Alquiler"],
            "Publicidad y marketing": ["Meta Ads", "Contenido", "Material", "Otro"], "Envíos y logística": ["Pago a despachadores", "Courier", "Empaque", "Otro"],
            "Plataformas": ["Shopify", "Cashea", "Meta", "Tina (KAI)", "Hosting", "Otras"], "Impuestos": ["IVA", "SENIAT", "Otros"], "Vehículo": ["Mantenimiento", "Repuestos"], "Gasolina": ["Gasolina"],
            "Oficina y limpieza": ["Oficina", "Limpieza"], "Contabilidad y legal": ["Contador", "Legal"], "Posventa": ["Reposiciones", "Devoluciones"], "Donaciones": ["Donación"], "Otros gastos": ["Otro"]}
    con.execute("INSERT OR REPLACE INTO config (clave, valor) VALUES ('categorias_gasto', ?)", (json.dumps(CATS, ensure_ascii=False),))
    con.executemany("INSERT INTO productos (sku,nombre,categoria,descripcion,precio,precio_par,costo,tipo,requiere_color,permite_malla,permite_personalizacion,orden) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [p + (i,) for i, p in enumerate(PRODUCTOS)])
    prods = {r["sku"]: dict(r) for r in con.execute("SELECT * FROM productos")}
    global TASA
    from plataforma import bcv
    con.commit()
    est = bcv.actualizar(DB)
    if est.get("valor"): TASA = est["valor"]
    con.execute("INSERT INTO config VALUES ('promo_envio', ?)", (json.dumps({"nombre": "Cashea Envíos · MRW", "hasta": "2026-10-31", "canal": "cashea", "productos": ["RAMPA-N", "RAMPA-MINI", "COM-10", "COM-15", "COM-20", "SLOW-15", "BAR-30"]}),))

    if "--vacia" in sys.argv:  # base limpia: catálogo, usuarios y tasa, sin clientes ni órdenes
        con.commit(); print("Base limpia: sin clientes ni órdenes."); return

    # Clientes (5 perfiles fijos para probar con claridad)
    for cid, (nombre, tel, ciudad, estado, zona, direccion, canal, forma, tipo_entrega, notas, ag_pref, perro) in enumerate(PERFILES, start=1):
        np_, ap = nombre.split(" ", 1)
        con.execute("INSERT INTO clientes (id,nombre_pila,apellido,nombre,telefono,cedula,correo,ciudad,estado,canal_habitual) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (cid, np_, ap, nombre, tel, f"V-{R.randint(10000000, 29999999)}", np_.lower() + "@gmail.com", ciudad, estado, canal))
        if direccion:
            con.execute("INSERT INTO direcciones (cliente_id,etiqueta,direccion,zona,municipio,ciudad,estado,principal) VALUES (?,?,?,?,?,?,?,1)",
                        (cid, "Casa", direccion, zona, {"La Castellana": "Chacao", "El Hatillo": "El Hatillo", "Prados del Este": "Baruta"}.get(zona), ciudad, estado))
        for tp, txt, en_orden, en_log, priv in notas:
            con.execute("INSERT INTO notas_cliente (cliente_id,tipo,texto,mostrar_en_orden,mostrar_logistica,privada,autor_id) VALUES (?,?,?,?,?,?,1)", (cid, tp, txt, en_orden, en_log, priv))
        con.execute("INSERT INTO mascotas (cliente_id,nombre,raza) VALUES (?,?,?)", (cid, perro[0], perro[1]))

    # Órdenes
    ESTADOS_PESO = [("por_revisar", 2), ("confirmada", 6), ("coordinada", 10), ("entregada", 10), ("cancelada", 1)]
    pool = [e for e, w in ESTADOS_PESO for _ in range(w)]
    numero = 10230
    for k in range(24):
        estado = R.choice(pool)
        if k < len(PERFILES) and PERFILES[k][8] == "nacional": estado = ["coordinada", "coordinada", "coordinada", "entregada", "confirmada"][[i for i, p in enumerate(PERFILES) if p[8] == "nacional"].index(k)]
        dias = R.randint(0, 2) if estado in ("por_revisar", "confirmada", "coordinada") else R.randint(0, 12)
        fecha = HOY - datetime.timedelta(days=dias)
        hmax = min(19, datetime.datetime.now().hour - 1) if dias == 0 else 19
        hora = f"{R.randint(8, max(8, hmax)):02d}:{R.choice(['05','20','35','50'])}"
        cid = (k + 1) if k < len(PERFILES) else [1, 3, 4, 7][k - len(PERFILES)]  # 20 clientes con una orden + 4 recompras
        cl = con.execute("SELECT * FROM clientes WHERE id=?", (cid,)).fetchone()
        perfil = PERFILES[cid - 1]
        canal = perfil[6] if R.random() < 0.8 else R.choice(["whatsapp", "cashea"])
        fuera = cl["ciudad"] != "Caracas"
        # productos
        lineas = []
        TAMANO = {"Beagle": "M", "Golden Retriever": "G", "Labrador": "G", "Salchicha / Dachshund": "M", "Pitbull": "G", "Yorkshire": "M", "Pastor Alemán": "G", "Pug": "M", "Bóxer": "G", "Rottweiler": "G", "Maltés": "M",
                  "Husky Siberiano": "G", "Border Collie": "G", "Mestizo": "M", "Cocker Spaniel": "M", "Bulldog Francés": "M", "Schnauzer": "M", "Gran Danés": "G", "Chihuahua": "M"}
        tam = TAMANO.get(perfil[11][1], "M")
        chico = perfil[11][1] in ("Yorkshire", "Chihuahua", "Maltés", "Pug", "Salchicha / Dachshund", "Bulldog Francés")
        principal = R.choice([f"PRO-{tam}", f"PRO-{tam}", f"REP-{tam}", f"PACK3-{tam}", "RAMPA-MINI" if chico else "RAMPA-N", "COM-15" if chico else "BAR-30", "SLOW-15"])
        if k >= len(PERFILES): principal = f"PACK3-{tam}" if k % 2 else f"REP-{tam}"  # recompras: repuestos o packs
        lineas.append((principal, R.choice([1, 1, 1, 2]) if principal.startswith("REP") else 1, R.choice([None, None, "Toby", "Luna", "Simón"]) if principal.startswith(("PRO", "COM", "BAR")) else None))

        if canal == "cashea" and principal.startswith(("PRO", "REP", "PACK")) and R.random() < 0.7: principal_ok = True
        subtotal = sum(prods[s]["precio"] * c + (10 if perso else 0) for s, c, perso in lineas)
        costo = sum(prods[s]["costo"] * c for s, c, _ in lineas)
        descuento = R.choice([0, 0, 0, 0, 5, 10]) if canal == "whatsapp" else 0
        iva = round((subtotal - descuento) * 0.16, 2) if canal == "cashea" else 0
        # entrega
        promo = None
        distribuidor = None
        tipo_entrega = perfil[8]
        if tipo_entrega == "distribuidor": delivery = 0; distribuidor = "Vivero Maracaibo"
        elif tipo_entrega == "nacional":
            delivery = 0
            if canal == "cashea" and not principal.startswith(("PRO", "REP", "PACK")): promo = "Cashea Envíos · MRW"
        elif tipo_entrega == "pickup": delivery = 0
        elif tipo_entrega == "delivery_fuera": delivery = R.choice([15, 18, 20])
        else: delivery = R.choice([5, 5, 6, 8])
        modalidad = None
        if tipo_entrega == "nacional": modalidad = "pagado_decopet" if (promo or k % 2) else "cobro_destino"
        total = round(subtotal - descuento + iva + delivery, 2)
        forma = "BNC" if canal == "cashea" else perfil[7]
        if canal == "cashea" and estado == "por_revisar": estado = "coordinada"  # Cashea aprueba la compra; no hay pago que revisar
        contra_entrega = forma.startswith("Efectivo") and tipo_entrega != "nacional"
        if contra_entrega and estado == "por_revisar": estado = "coordinada"
        # estado de pago según estado
        if estado == "por_revisar": estado_pago = "por_confirmar"; estado = "pendiente"
        elif estado == "cancelada": estado_pago = R.choice(["sin_pago", "reembolsada"])
        elif contra_entrega: estado_pago = "contra_entrega" if estado != "entregada" else "pagada"
        elif canal in ("cashea", "duwu"): estado_pago = "pagada" if (estado == "entregada" and dias > 8) else "por_cobrar"
        else: estado_pago = R.choice(["pagada"] * 8 + ["abonada"])
        dir_ = con.execute("SELECT * FROM direcciones WHERE cliente_id=? AND principal=1", (cid,)).fetchone()
        receptor = (None, None)
        if tipo_entrega == "nacional" and R.random() < 0.5: receptor = (R.choice(["Sra. Rosa (mamá)", "Pedro (esposo)", "Portería del edificio"]), f"04{R.choice(['12','14','24'])}-{R.randint(1000000, 9999999)}")
        elif cid == 1: receptor = ("Juan, el conserje", None)
        coordinada = estado in ("coordinada", "entregada")
        if estado in ("coordinada", "confirmada"): estado = "pendiente"
        if tipo_entrega == "nacional": fecha_prom = (fecha + datetime.timedelta(days=2)).isoformat()   # margen de 2 días para despachar
        elif R.random() < 0.15: fecha_prom = (HOY + datetime.timedelta(days=R.choice([1, 2]))).isoformat()  # el cliente pidió otro día
        else: fecha_prom = fecha.isoformat()  # mismo día
        if estado == "coordinada" and R.random() < 0.3: fecha_prom = (HOY - datetime.timedelta(days=1)).isoformat()  # retrasada
        despachador = (R.choice(DESPACHADORES) if tipo_entrega in ("delivery", "delivery_fuera") else None) if coordinada else None
        agencia = perfil[10] if tipo_entrega == "nacional" and coordinada else None  # reparte Tealca / MRW / Zoom
        maps = f"https://maps.app.goo.gl/{R.choice(['x7Qk2','p9Lm4','aB3cD','Zt8Wq','mN5rV'])}{R.randint(100,999)}" if tipo_entrega in ("delivery", "delivery_fuera") and R.random() < 0.6 else None
        saldo_concepto = R.choice(["Faltó el delivery ($5)", "Pagó la mitad, resto al recibir", "Diferencia por cambio de tamaño"]) if estado_pago == "abonada" else None
        guia = f"{agencia[:2].upper()}{R.randint(100000000, 999999999)}" if agencia and (estado == "entregada" or R.random() < 0.5) else None
        fecha_ent = fecha_prom if estado == "entregada" else None
        nota_log = con.execute("SELECT texto FROM notas_cliente WHERE cliente_id=? AND mostrar_logistica=1", (cid,)).fetchone()
        numero += 1
        cur = con.execute("""INSERT INTO ordenes (numero,tipo,cliente_id,canal,creada_por,conversacion,ref_externa,estado,estado_pago,subtotal,descuento,motivo_descuento,iva,delivery,total,tasa_bcv,comision,
            tipo_entrega,direccion,zona,ciudad,receptor_nombre,receptor_telefono,despachador,agencia,guia,fecha_prometida,franja,fecha_entrega,promo_envio,notas_entrega,costo_productos,costo_entrega,creado_en,actualizado_en,maps,distribuidor,saldo_concepto,forma_pago_prevista,modalidad_envio)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"#{numero}", "venta", cid, canal, 3 if canal == "whatsapp" else (1 if canal == "duwu" else 3), f"wa-{R.randint(10000, 99999)}" if canal == "whatsapp" else None,
             f"CSH-{R.randint(100000, 999999)}" if canal == "cashea" else (f"#{R.randint(1000, 1100)}" if canal == "shopify" else None),
             estado, estado_pago, subtotal, descuento, "Promo septiembre" if descuento else None, iva, delivery, total, TASA, round(total * 0.06, 2) if canal == "cashea" else 0,
             tipo_entrega, (dir_["direccion"] if dir_ else (f"Agencia {agencia or 'Tealca'} {cl['ciudad']}" if tipo_entrega == "nacional" else None)), (dir_["zona"] if dir_ else None), cl["ciudad"], receptor[0], receptor[1],
             despachador, agencia, guia, fecha_prom, R.choice(FRANJAS) if coordinada and tipo_entrega == "delivery" else ("11:30 am" if tipo_entrega == "pickup" and coordinada else None),
             fecha_ent, promo, nota_log["texto"] if nota_log else None, costo, (R.choice([3, 4, 5]) if despachador and estado == "entregada" else 0),
             f"{fecha.isoformat()} {hora}", f"{fecha.isoformat()} {hora}", maps, distribuidor, saldo_concepto, forma, modalidad))
        oid = cur.lastrowid
        for s, c, perso in lineas:
            extras = (10 if perso else 0) + (20 if s == "MALLA" else 0)
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,personalizacion,color,malla,extras,total) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (oid, prods[s]["id"], prods[s]["nombre"], c, prods[s]["precio"], prods[s]["costo"], perso, "azul" if s.startswith("SLOW") else None, 1 if s == "MALLA" else 0, extras, prods[s]["precio"] * c + extras))
        # pagos
        if estado_pago in ("por_confirmar", "pagada", "abonada", "reembolsada"):
            monto = total if estado_pago != "abonada" else round(total * 0.5, 2)
            en_bs = forma in ("Pago Móvil", "Efectivo Bs")
            digital = forma in ("Pago Móvil", "Zelle", "Binance USDT")
            motivo = R.choice(["Monto no coincide: llegaron Bs 12.000 y la orden son Bs 12.700", "Referencia no encontrada en el banco", "Captura ilegible", "Transferencia desde tercero, verificar titular"]) if estado_pago == "por_confirmar" else None
            con.execute("""INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,tasa,cuenta,referencia,fecha,estado,verificado_tina,motivo_revision,confirmado_por,confirmado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (oid, forma, monto, round(monto * TASA, 2) if en_bs else monto, "VES" if en_bs else "USD", TASA if en_bs else None, CUENTAS.get(forma), str(R.randint(100000, 999999)),
                         f"{fecha.isoformat()} {hora}", "por_confirmar" if estado_pago == "por_confirmar" else "confirmado", 0 if estado_pago == "por_confirmar" else (1 if digital else 0), motivo,
                         None if estado_pago == "por_confirmar" else (3 if digital else 1), None if estado_pago == "por_confirmar" else f"{fecha.isoformat()} {hora}"))
        elif estado_pago == "por_cobrar" and canal == "cashea":
            # Supuesto: Cashea transfiere la inicial (25 %) al BNC el mismo día; las cuotas llegan después
            inicial = round(total * 0.25, 2)
            con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,tasa,cuenta,referencia,fecha,estado,confirmado_por,confirmado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (oid, "BNC", inicial, round(inicial * TASA, 2), "VES", TASA, "BNC (Bs)", f"Inicial Cashea {R.randint(100000, 999999)}", f"{fecha.isoformat()} {hora}", "confirmado", 1, f"{fecha.isoformat()} {hora}"))
        # historial
        con.execute("INSERT INTO historial (orden_id,usuario_id,accion,detalle,creado_en) VALUES (?,?,?,?,?)", (oid, 3 if canal == "whatsapp" else 1, "creada", f"Orden creada por canal {canal}", f"{fecha.isoformat()} {hora}"))
        if estado != "nueva":
            con.execute("INSERT INTO historial (orden_id,usuario_id,accion,detalle,creado_en) VALUES (?,?,?,?,?)", (oid, 1 if estado_pago in ("pagada", "por_cobrar") else 3, "estado", f"→ {estado}", f"{fecha.isoformat()} {hora}"))
        if estado == "cancelada":
            con.execute("INSERT INTO historial (orden_id,usuario_id,accion,detalle,motivo,creado_en) VALUES (?,?,?,?,?,?)", (oid, 1, "cancelada", "Orden cancelada", R.choice(["Cliente desistió", "Sin stock del tamaño", "Error nuestro en el precio"]), f"{fecha.isoformat()} {hora}"))
        if estado == "pendiente" and coordinada and R.random() < 0.2:
            con.execute("INSERT INTO incidencias (orden_id,clase,tipo,descripcion,responsable,estado,autor_id) VALUES (?,?,?,?,?,?,2)",
                        (oid, "incidencia", R.choice(["retraso", "direccion", "entrega_fallida"]), R.choice(["El cliente no contestó en la primera visita.", "La dirección no coincide con el Maps.", "Motorizado se retrasó por lluvia."]), despachador or "—", "abierta"))
    con.commit()
    print("Datos de prueba listos:", con.execute("SELECT COUNT(*) FROM ordenes").fetchone()[0], "órdenes,", con.execute("SELECT COUNT(*) FROM clientes").fetchone()[0], "clientes")


if __name__ == "__main__":
    main()
