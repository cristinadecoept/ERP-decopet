"""Carga "Pedidos-Todos los pedidos.csv" (Airtable, 2026) en ÓRDENES. NUNCA toca el Registro de ventas (origen_excel=2).
Uso: ./.venv/bin/python -m plataforma.importar_pedidos "/ruta/archivo.csv" [--cargar]
Decisiones de Cristina (20 sep 2026): fechas 2020/2004 → 2026; saldo negativo = delivery pagado aparte; Tarek Atta total $22; Regalo → orden tipo regalo sin pago;
"Repuesto (pack / orden previa)" era error del bot → Pago Móvil; fecha de pago posterior a la de entrega → se iguala a la de entrega;
columnas Excel/LDP/Incluir en resumen se ignoran, salvo Excel='R F' que marca factura fiscal hecha; Cashea siempre requiere factura fiscal."""
import sys, csv, re, os, sqlite3, datetime, collections
from pathlib import Path
DB = Path(os.environ.get("DECOPET_DATOS") or (Path(__file__).parent / "data")) / "plataforma.db"   # la misma base que abre el ERP

def n(s): return " ".join((s or "").split())
def money(s): return float((s or "0").replace("$", "").replace(",", "") or 0)

PROD = {"pro mediano": ("PRO-M", None, 0), "pro grande": ("PRO-G", None, 0), "pro mediano + malla": ("PRO-M", None, 1), "pro grande + malla": ("PRO-G", None, 1),
        "repuesto grande": ("REP-G", None, 0), "repuesto mediano": ("REP-M", None, 0), "repuesto grande + malla": ("REP-G", None, 1), "repuesto mediano + malla": ("REP-M", None, 1),
        "rampa estándar": ("RAMPA-MINI", None, 0), "rampa estandar": ("RAMPA-MINI", None, 0), "rampa nueva": ("RAMPA-N", None, 0),
        "pack 3 repuestos medianos": ("PACK3-M", None, 0), "pack 3 repuestos grandes": ("PACK3-G", None, 0), "pack 4 repuestos cashea": ("PACK4", None, 0), "pack 8 repuestos cashea": ("PACK8", None, 0),
        "comedor 10cm": ("COM-10", None, 0), "comedor 15cm": ("COM-15", None, 0), "comedor 20cm": ("COM-20", None, 0), "bar 25cm": ("BAR-25", None, 0), "bar 30cm": ("BAR-30", None, 0),
        "básico grande": ("BAS-G", None, 0), "básico mediano": ("BAS-M", None, 0), "basico grande": ("BAS-G", None, 0), "basico mediano": ("BAS-M", None, 0),
        "malla por separado": ("MALLA", None, 0), "bowl pequeno": ("BOWL-P", None, 0), "bowl pequeño": ("BOWL-P", None, 0)}
for cm, sku in (("10", "SLOW-10"), ("15", "SLOW-15"), ("20", "SLOW-20"), ("25", "SLOW-20"), ("30", "SLOW-30")):
    for col in ("azul", "rosado"): PROD[f"slow chow {cm}cm {col}"] = (sku, col, 0)
PAGO = {"pago movil": "Pago Móvil", "bnc": "Cashea BNC", "zelle": "Zelle", "efectivo": "Efectivo USD", "binance": "Binance USDT", "venmo": "Venmo", "pay pal": "PayPal", "paypal": "PayPal", "pipol pay": "Pipol Pay",
        "repuesto (pack / orden previa)": "Pago Móvil"}
# Caja donde entró cada pago: los nombres de las cajas reales (los mismos que NOMBRES_VIEJOS en app.py).
CUENTA = {"Pago Móvil": "Pago Móvil VES", "Cashea BNC": "BNC Cashea", "Zelle": "Zelle", "Efectivo USD": "Efectivo USD Caracas", "Binance USDT": "Binance USDT", "Venmo": "Venmo", "PayPal": "Wise", "Pipol Pay": "Pipol Pay"}
# Productos que se vendieron en Airtable y pueden no estar en el catálogo: si faltan se crean INACTIVOS (no aparecen para vender). Precio de Airtable.
HISTORICOS = {"BAS-M": ("Porche Básico Mediano", "porche", 38), "BAS-G": ("Porche Básico Grande", "porche", 38),
              "PACK4": ("Pack 4 Repuestos Cashea", "repuesto", 88), "PACK8": ("Pack 8 Repuestos Cashea", "repuesto", 176)}
AGENCIAS = {"tealca": "Tealca", "mrw": "MRW", "zoom": "Zoom", "liberty express": "Liberty Express"}
DESPACH = {"ingrid": "Ingrid", "juan": "Juan", "cristina": "Cristina", "tony": "Tony", "fernando": "Fernando"}

def fecha_iso(s):
    s = n(s)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s): return s
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None

# Paola Ávila #8802: el pack no traía incluidos/entregados. Cristina (3 oct 2026): recibió 1, le quedan 2.
PACK_SIN_DATOS = {8802: (3, 1)}
# Pedidos que en Airtable no tenían cliente. Cristina (3 oct 2026) dijo de quién son.
SIN_NOMBRE = {8775: "Natalia Demirdjian", 8928: "Gaudy Leyanet Martinez Gascon"}
# Fichas repetidas que Cristina unió (3 oct 2026): sus pedidos van a la ficha que quedó
UNIDOS = {"madeleine baez": 2205, "dania ramire": 315, "andrea fernandez": 2194}   # ids de la base de Cristina
def status_x(r): return n(r["Status"]).lower()
def clave(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", n(s).casefold()) if unicodedata.category(c) != "Mn")

def agrupar(pedidos, avisos):
    """En Airtable, el repuesto prepagado se separaba en otro pedido ("Repuesto pendiente de entrega") el mismo día de la compra,
    solo para controlar la entrega. En el ERP es UNA compra: el repuesto pasa a la orden principal como "lo recibe otro día".
    Cristina (3 oct 2026): el cliente pagó las dos cosas (Porche $83 + repuesto $22 = $105), salvo que el pago del pedido
    principal ya incluyera el del repuesto (pagado > total), en cuyo caso no se suma dos veces."""
    grupos = collections.defaultdict(list)
    for p in pedidos: grupos[(clave(p["cliente"]), p["fecha_pago"])].append(p)
    fuera = set()
    for g in grupos.values():
        prin = next((p for p in g if not p["prepago"]), None)
        for p in g:
            if not p["prepago"] or prin is None: continue
            # ¿lo que sobraba en el pago del principal era justo el repuesto? (Mariacarmen: pagó $112 en una orden de $88 → los $24 del repuesto)
            if "sobrante" not in prin: prin["sobrante"] = round(prin["pagado"] - prin["total_raw"], 2)   # se mide una sola vez, con el pago original
            ya_incluido = p["pagado"] > 0 and prin["sobrante"] >= p["pagado"] - 0.01
            if ya_incluido:   # ese sobrante no era "delivery aparte": se deshace esa regla y entra el repuesto
                if not prin.get("deshecho"): prin["total"], prin["delivery"] = prin["total_raw"], prin["delivery_raw"]; prin["deshecho"] = True
                prin["sobrante"] = round(prin["sobrante"] - p["pagado"], 2)
            prin["lineas"] += p["lineas"]; prin["total"] = round(prin["total"] + p["total"], 2)
            prin["delivery"] += p["delivery"]; prin["perso"] += p["perso"]; prin["descuento"] += p["descuento"]
            if not ya_incluido: prin["pagado"] = round(prin["pagado"] + p["pagado"], 2)
            prin["formas"] = list(dict.fromkeys(prin["formas"] + p["formas"]))
            prin["saldo"] = round(max(prin["total"] - prin["pagado"], 0), 2)
            prin["absorbe"].append(p["id"]); fuera.add(p["id"])
            avisos["repuesto pendiente unido a su compra"].append((p["id"], "→", prin["id"], p["cliente"], "pago ya incluido" if ya_incluido else f"+${p['pagado']:g}"))
    solos = [p for p in pedidos if p["prepago"] and p["id"] not in fuera]
    for p in solos: avisos["repuesto pendiente sin compra el mismo día (orden propia)"].append((p["id"], p["cliente"]))
    return [p for p in pedidos if p["id"] not in fuera]

def leer(ruta):
    rows = list(csv.DictReader(open(ruta, "rb").read().decode("utf-8-sig").splitlines()))
    out = []; avisos = collections.defaultdict(list)
    for r in rows:
        oid = int(r["ID"]); cliente = n(r["Cliente"]) or SIN_NOMBRE.get(int(r["ID"])) or "Sin nombre"
        if cliente == "Sin nombre": avisos["sin nombre"].append(oid)
        fp = fecha_iso(r["Fecha Pago"]) or "2026-01-01"
        if fp[:4] in ("2020", "2004"): fp = "2026" + fp[4:]; avisos["fecha corregida a 2026"].append((oid, cliente))
        fe = fecha_iso(r["Fecha Entrega"])
        if fe and fe < fp: avisos["fecha pago igualada a la de entrega"].append((oid, cliente, fp, fe)); fp = fe
        lineas = []
        for it in re.split(r"\s*,\s*", n(r["Productos"])):
            m = re.match(r"^(.*?)\s*x\s*(\d+)$", it); nombre, cant = (m.group(1).strip(), int(m.group(2))) if m else (it, 1)
            k = nombre.lower()
            if k not in PROD: avisos["producto sin mapa"].append((oid, nombre)); continue
            lineas.append((PROD[k], cant, False))
        total, pagado, saldo = money(r["Total"]), money(r["Monto pagado"]), money(r["Saldo pendiente"])
        delivery, perso, desc = money(r["Delivery"]), money(r["Personalizacion"]), money(r["Descuento"])
        deliv_entregas = money(r["Delivery Entregas"])
        if cliente.lower() == "tarek atta" and total == 1: total = 22; saldo = 0; avisos["total corregido (Tarek Atta → $22)"].append(oid)
        total_raw, delivery_raw = total, delivery
        if saldo < 0: delivery += -saldo; total += -saldo; saldo = 0; avisos["saldo negativo → delivery pagado aparte"].append((oid, cliente))
        forma_x = n(r["Forma de Pago"]).lower(); regalo = forma_x == "regalo"
        formas = ([PAGO[forma_x]] if forma_x in PAGO else [PAGO.get(f.strip(), None) for f in forma_x.split("/")]) if not regalo else []
        if any(f is None for f in formas): avisos["forma de pago sin mapa"].append((oid, forma_x)); formas = [f for f in formas if f]
        if "repuesto (pack" in forma_x: avisos["forma 'Repuesto (pack…)' → Pago Móvil"].append(oid)
        canal = "cashea" if n(r["Canal"]).lower() == "cashea" else "whatsapp"
        te = n(r["Tipo de Entrega"]).lower(); desp_x = n(r["Despachador"]).lower(); ag_x = n(r["Agencia"]).lower(); ciudad = n(r["Ciudad"]) or None
        agencia = AGENCIAS.get(ag_x) or AGENCIAS.get(desp_x); despachador = DESPACH.get(desp_x)
        if te == "pick up": tipo = "pickup"
        elif te == "envio nacional" or (te == "pendientes de contacto" and agencia): tipo = "nacional"
        elif ciudad and ciudad.lower() not in ("caracas", "ccs", "caracas."): tipo = "delivery_fuera" if not agencia else "nacional"
        else: tipo = "delivery"
        if te == "pendientes de contacto": avisos["'Pendientes de contacto' → según despachador"].append((oid, cliente, tipo))
        status = n(r["Status"]).lower(); entregada = status != "pagado por coordinar"   # solo "Pagado por coordinar" queda pendiente; los packs pendientes se manejan como retiros
        inc = int(n(r["Repuestos incluidos"]) or 0); ent = int(n(r["Repuestos entregados"]) or 0); pend = int(n(r["Repuestos pendientes"]) or 0)
        if oid in PACK_SIN_DATOS: inc, ent = PACK_SIN_DATOS[oid]; pend = inc - ent; avisos["pack sin datos → decisión de Cristina"].append((oid, cliente, f"{ent} de {inc} entregados"))
        prepago = "repuesto pendiente" in status_x(r)   # "Repuesto pendiente de entrega": el repuesto está pagado y todavía no lo recibe
        if prepago: lineas = [(sku, cant, True) for sku, cant, _ in lineas]
        out.append(dict(id=oid, cliente=cliente, prepago=prepago, total_raw=round(total_raw, 2), delivery_raw=delivery_raw, absorbe=[], fecha_pago=fp, fecha_entrega=fe, lineas=lineas, total=round(total, 2), pagado=round(pagado, 2), saldo=round(saldo, 2), delivery=delivery, perso=perso, descuento=desc,
                        deliv_entregas=deliv_entregas, formas=formas, regalo=regalo, canal=canal, tipo=tipo, agencia=agencia, despachador=despachador, ciudad=ciudad,
                        dir_nueva=n(r["Dirección nueva"]) or None, dir_hab=n(r["Dirección habitual"]) or None, retira=n(r["Retira el cliente"]), notas=n(r["Notas"]) or None,
                        entregada=entregada, status=n(r["Status"]), pack_inc=inc, pack_ent=ent, pack_pend=pend, factura_hecha=(canal == "cashea"), requiere_factura=(canal == "cashea")))   # Cristina: las facturas de Cashea están al día
    return agrupar(out, avisos), avisos

def cargar(pedidos):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    cols = [r[1] for r in con.execute("PRAGMA table_info(ordenes)")]
    for c in ("requiere_factura", "factura_hecha"):
        if c not in cols: con.execute(f"ALTER TABLE ordenes ADD COLUMN {c} INTEGER DEFAULT 0")
    prod = {r["sku"]: dict(r) for r in con.execute("SELECT * FROM productos")}
    faltan = {sku for p in pedidos for (sku, _, _), _, _ in p["lineas"]} - set(prod)
    for sku in sorted(faltan & set(HISTORICOS)):
        nombre, cat, precio = HISTORICOS[sku]
        con.execute("INSERT INTO productos (sku,nombre,categoria,precio,costo,tipo,activo) VALUES (?,?,?,?,0,'producto',0)", (sku, nombre, cat, precio))
        print(f"- producto {sku} no estaba en el catálogo: creado inactivo ({nombre}, ${precio})")
    if faltan - set(HISTORICOS):   # antes esto revertía todo sin decir por qué
        sys.exit(f"Faltan en el catálogo estos productos que los pedidos necesitan: {sorted(faltan - set(HISTORICOS))}. No se cargó nada.")
    prod = {r["sku"]: dict(r) for r in con.execute("SELECT * FROM productos")}
    opc_malla = prod["OPC-MALLA"]; opc_perso = prod["OPC-PERSO"]
    filas_cli = con.execute("SELECT id, nombre FROM clientes").fetchall()
    exacto = {n(r["nombre"]).casefold(): r["id"] for r in filas_cli}
    sin_tilde = collections.defaultdict(set)
    for r in filas_cli: sin_tilde[clave(r["nombre"])].add(r["id"])
    def buscar(nombre):
        if nombre.casefold() in exacto: return exacto[nombre.casefold()]
        if clave(nombre) in UNIDOS: return UNIDOS[clave(nombre)]
        ids = sin_tilde.get(clave(nombre), set())
        return next(iter(ids)) if len(ids) == 1 else None
    sin_cliente = []
    tasa = (con.execute("SELECT valor FROM tasas ORDER BY fecha_valor DESC LIMIT 1").fetchone() or [0])[0]
    n_ok = 0
    for p in pedidos:
        cid = buscar(p["cliente"])
        if not cid: sin_cliente.append((p["id"], p["cliente"])); continue   # no se inventan clientes: se reporta
        subtotal = round(p["total"] - p["delivery"] - p["perso"] + p["descuento"], 2)
        base = sum(prod[sku]["precio"] * cant for (sku, _, _), cant, _ in p["lineas"]) or 1
        estado_pago = "pagada" if p["regalo"] or p["saldo"] <= 0.01 else ("abonada" if p["pagado"] > 0 else "sin_pago")
        if p["canal"] == "cashea" and estado_pago == "pagada": estado_pago = "pagada"
        forma_txt = " + ".join(dict.fromkeys(p["formas"])) or ("Regalo" if p["regalo"] else None)
        cur = con.execute("""INSERT INTO ordenes (numero,tipo,cliente_id,canal,creada_por,estado,estado_pago,subtotal,descuento,iva,delivery,total,tasa_bcv,comision,tipo_entrega,direccion,ciudad,despachador,agencia,
                             fecha_prometida,fecha_entrega,notas_entrega,forma_pago_prevista,modalidad_envio,origen_excel,requiere_factura,factura_hecha,creado_en,actualizado_en) VALUES (?,?,?,?,1,?,?,?,?,0,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (f"#{p['id']}", "regalo" if p["regalo"] else "venta", cid, p["canal"], "entregada" if p["entregada"] else "pendiente", estado_pago, subtotal, p["descuento"], p["delivery"], p["total"], tasa,
                           p["tipo"], p["dir_nueva"] or p["dir_hab"], p["ciudad"], p["despachador"], p["agencia"], p["fecha_entrega"] or p["fecha_pago"], p["fecha_entrega"] if p["entregada"] else None,
                           p["notas"], forma_txt, "cobro_destino" if p["tipo"] == "nacional" else None,
                           # entregada = historia (2: no cuenta para despachadores, rutas ni caja). Pendiente = trabajo vivo: el ERP la maneja (0)
                           0 if not p["entregada"] else 2, 1 if p["requiere_factura"] else 0, 1 if p["factura_hecha"] else 0, p["fecha_pago"] + " 12:00:00", p["fecha_pago"] + " 12:00:00"))
        oid = cur.lastrowid
        for (sku, color, malla), cant, prepago in p["lineas"]:
            pr = prod[sku]; extras = (opc_malla["precio"] if malla else 0) * cant
            parte = round(subtotal * (pr["precio"] * cant / base), 2) if base else 0
            li = con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,color,malla,extras,total) VALUES (?,?,?,?,?,?,?,?,?,?)", (oid, pr["id"], pr["nombre"], cant, pr["precio"], pr["costo"], color, 1 if malla else 0, extras, parte)).lastrowid
            if prepago and sku.startswith("REP-"):   # igual que "Lo recibe otro día" en Nueva orden: un repuesto por recibir por cada unidad
                for _ in range(cant):
                    con.execute("INSERT INTO repuestos_prepagados (cliente_id,orden_id,linea_id,producto_id,tamano,pagado_en,monto,notas,usuario_id) VALUES (?,?,?,?,?,?,?,?,1)",
                                (cid, oid, li, pr["id"], "Grande" if sku == "REP-G" else "Mediano", p["fecha_pago"], 0 if p["regalo"] else round(parte / cant, 2), f"migrado de Airtable (#{p['id']})" + (" · regalo" if p["regalo"] else "")))
            if sku.startswith("PACK") and p["pack_inc"]:
                k = con.execute("INSERT INTO packs (cliente_id,orden_id,producto_id,tamano,unidades,entregadas_inicio,estado,creado_en) VALUES (?,?,?,?,?,?,?,?)",
                                (cid, oid, pr["id"], "Grande" if sku.endswith("G") else ("Mediano" if sku.endswith("M") else None), p["pack_inc"], 1 if p["pack_ent"] >= 1 else 0, "activo" if p["pack_pend"] > 0 else "completo", p["fecha_pago"] + " 12:00:00")).lastrowid
                extra_deliv = p["deliv_entregas"]; retiros = max(p["pack_ent"] - 1, 0)   # "Delivery Entregas" = delivery cobrado en los retiros (aparte del de la orden)
                for i in range(retiros):   # retiros posteriores: no tenemos fechas exactas en Airtable, se usa la fecha de entrega/pago
                    con.execute("INSERT INTO entregas_repuesto (pack_id,fecha,tipo_entrega,delivery_cobrado,notas,usuario_id) VALUES (?,?,?,?,?,1)", (k, p["fecha_entrega"] or p["fecha_pago"], p["tipo"], round(extra_deliv / retiros, 2), "migrado de Airtable (fecha aproximada)"))
        if p["perso"]: con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,?,?,?,?)", (oid, opc_perso["id"], opc_perso["nombre"], 1, p["perso"], p["perso"]))
        if p["pagado"] > 0 and not p["regalo"] and p["formas"]:
            con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,cuenta,fecha,estado,confirmado_por,confirmado_en) VALUES (?,?,?,?,'USD',?,?,'confirmado',1,?)",
                        (oid, forma_txt, p["pagado"], p["pagado"], CUENTA.get(p["formas"][0]), p["fecha_pago"], p["fecha_pago"] + " 12:00:00"))
        if p["retira"] == "No, un tercero": con.execute("UPDATE ordenes SET receptor_nombre=? WHERE id=?", ("otra persona (ver notas)", oid))
        if p["absorbe"]: con.execute("INSERT INTO historial (orden_id,usuario_id,accion,detalle) VALUES (?,1,'migración',?)",
                                     (oid, f"En Airtable el repuesto por recibir estaba aparte: {', '.join('#' + str(x) for x in p['absorbe'])}. Aquí es la misma compra."))
        n_ok += 1
    if sin_cliente: print("PEDIDOS SIN CLIENTE (no se cargaron):", len(sin_cliente), sin_cliente[:20])
    con.commit(); return n_ok

if __name__ == "__main__":
    ped, avisos = leer(sys.argv[1]); print(f"Pedidos: {len(ped)} · total ${sum(p['total'] for p in ped):,.2f}")
    for k, v in avisos.items(): print(f"- {k}: {len(v)} → {v[:5]}")
    print("packs con saldo:", sum(1 for p in ped if p["pack_pend"] > 0), "| pendientes de entrega:", sum(1 for p in ped if not p["entregada"]), "| cashea (requiere factura):", sum(1 for p in ped if p["requiere_factura"]), "| factura hecha:", sum(1 for p in ped if p["factura_hecha"]))
    if "--cargar" in sys.argv: print("Cargados", cargar(ped))
