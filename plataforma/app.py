"""Plataforma Decopet — pantallas. Parte 1: Órdenes."""
import datetime, json, sqlite3, re, os, subprocess, secrets, threading, time, hashlib

from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from plataforma import bcv
from plataforma import access as CF_ACCESS
from plataforma import cf_equipo as CF_EQUIPO

BASE = Path(__file__).resolve().parent
# Dónde viven los datos. En la Mac es la carpeta de siempre; en un servidor se le dice
# con DECOPET_DATOS, para que el programa y los datos no estén en el mismo sitio.
DATOS = Path(os.environ.get("DECOPET_DATOS") or (BASE / "data"))
DATOS.mkdir(parents=True, exist_ok=True)
DB = DATOS / "plataforma.db"
# En un servidor (con DECOPET_DATOS) el ERP nunca queda abierto: en la Mac, sin claves puestas,
# entraba cualquiera como administradora porque solo se abría desde la propia Mac. En internet,
# la primera clave pide además el código de instalación (DECOPET_CODIGO_INICIAL).
EN_SERVIDOR = bool(os.environ.get("DECOPET_DATOS"))
# Dónde está corriendo este ERP, para la franja de arriba: en una copia (la Mac, la PC del programador) o en el de
# pruebas se ve un aviso, para no anotar una venta real donde no llega a la operación. En producción no se ve nada.
def entorno():
    return "local" if not EN_SERVIDOR else ("pruebas" if os.environ.get("DECOPET_STAGING") == "1" else None)
DOCS_DIR = DATOS / "documentos"
FOTOS_DIR = DATOS / "fotos"
FOTOS_PRODUCTOS = FOTOS_DIR / "productos"
for _d in (DOCS_DIR, FOTOS_DIR, FOTOS_PRODUCTOS): _d.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Decopet", docs_url=None, redoc_url=None, openapi_url=None)   # sin manual técnico público: nadie necesita ver cómo está hecho por dentro
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.mount("/fotos", StaticFiles(directory=FOTOS_DIR), name="fotos")
tpl = Jinja2Templates(directory=BASE / "templates")

# Miniaturas. Las fotos de producto pesan hasta 5 MB y en pantalla se ven en cuadritos: la galería entera eran 37 MB.
# Cada foto se achica una sola vez (WebP, conserva la transparencia) y queda guardada con los datos. La original no se
# toca: es la que se descarga. La ?v= cambia cuando se reemplaza la foto, así el navegador puede guardar la miniatura.
MINIATURAS = DATOS / "miniaturas"
ANCHOS_MINI = (160, 480, 1600)   # la línea de una orden · las tarjetas · la foto abierta en grande

def mini(ruta, ancho=480):
    if not ruta: return None
    try: v = int((FOTOS_DIR / ruta).stat().st_mtime)
    except OSError: return f"/fotos/{ruta}"
    return f"/fotos-mini/{ancho}/{ruta}?v={v}"

tpl.env.globals["mini"] = mini
tpl.env.filters["mini"] = mini


def hacer_mini(ruta, ancho):
    """La miniatura de una foto, hecha si falta o si la foto cambió. None si no existe o no se puede achicar."""
    from PIL import Image, ImageOps
    orig = (FOTOS_DIR / ruta).resolve()
    if ancho not in ANCHOS_MINI or not orig.is_relative_to(FOTOS_DIR.resolve()) or not orig.is_file(): return None
    dest = MINIATURAS / str(ancho) / f"{ruta}.webp"
    if dest.exists() and dest.stat().st_mtime >= orig.stat().st_mtime: return dest
    try:
        with Image.open(orig) as im:
            im = ImageOps.exif_transpose(im)   # las fotos del teléfono vienen acostadas y con la vuelta anotada aparte
            im.thumbnail((ancho, ancho))
            im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P", "PA") else "RGB")
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f"{dest.name}.{threading.get_ident()}.tmp")   # dos pedidos a la vez no se pisan
            im.save(tmp, "WEBP", quality=80, method=4)
            os.replace(tmp, dest)
        return dest
    except Exception as e:   # HEIC, una foto dañada
        print(f"MINIATURA no se pudo hacer de {ruta}: {e}", flush=True)
        return None


def preparar_miniaturas():
    """Al arrancar, en segundo plano: deja hechas las miniaturas de tarjeta, para que la primera visita no espere."""
    for f in sorted(FOTOS_DIR.rglob("*")):
        if f.is_file(): hacer_mini(f.relative_to(FOTOS_DIR).as_posix(), 480)


@app.get("/fotos-mini/{ancho}/{ruta:path}")
def foto_mini(ancho: int, ruta: str):
    dest = hacer_mini(ruta, ancho)
    if dest: return FileResponse(dest, media_type="image/webp")
    if ancho in ANCHOS_MINI and (FOTOS_DIR / ruta).resolve().is_relative_to(FOTOS_DIR.resolve()) and (FOTOS_DIR / ruta).is_file():
        return RedirectResponse(f"/fotos/{ruta}", status_code=307)   # existe pero no se puede achicar: se muestra entera
    return PlainTextResponse("No existe", status_code=404)

ESTADOS = ["pendiente", "en_ruta", "entregada", "cancelada"]
E_LABEL = {"pendiente": "Pendiente", "en_ruta": "En ruta", "entregada": "Entregado", "cancelada": "Cancelada"}
CERRADOS = ("entregada", "cancelada")


def cifra(v):
    """'1.234,56', '1234.56' o vacío → float. Para los campos donde Cristina pega números del Excel."""
    t = str(v or "").strip().replace("$", "").replace(" ", "")
    if not t: return 0.0
    if "," in t and "." in t:
        # el separador que va de último es el decimal: 1.760,50 y 1,760.50 valen lo mismo
        sep = "," if t.rfind(",") > t.rfind(".") else "."
        t = t.replace("," if sep == "." else ".", "").replace(sep, ".")
    elif "," in t: t = t.replace(",", ".")
    try: return float(t)
    except ValueError: return 0.0


def cobro_extra(con, oid, concepto, monto, forma, fecha, uid, referencia=None, nota=None, pago="confirmado"):
    """El cliente le agrega algo a una orden que ya existe (delivery de un retiro, personalización, propina…).
    Entra dentro de la orden: sube su total y queda anotado el día en que entró, que es cuando cuenta como venta
    (la venta original no cambia de día). pago: 'confirmado', 'por_confirmar' (lo reportó el despachador) o None (se cobra después)."""
    monto = round(float(monto or 0), 2)
    if monto <= 0 or not oid: return None
    fecha = fecha or datetime.date.today().isoformat()
    con.execute("""INSERT INTO orden_lineas (orden_id, nombre, cantidad, precio, costo, total, forma_pago, extra_en)
                   VALUES (?,?,1,?,0,?,?,?)""", (oid, concepto, monto, monto, forma or None, fecha[:10]))
    con.execute("UPDATE ordenes SET subtotal=COALESCE(subtotal,0)+?, total=COALESCE(total,0)+? WHERE id=?", (monto, monto, oid))
    if (concepto or "").strip().lower() == "delivery":
        # el delivery de ESTE pedido que se pagó después: cuenta para pagarle al despachador que lo lleve.
        # El de un retiro de pack o de un prepagado no: ese viaje se le paga aparte (viajes_despachador).
        con.execute("UPDATE ordenes SET delivery=COALESCE(delivery,0)+? WHERE id=?", (monto, oid))
    if pago:
        conf = pago == "confirmado"
        con.execute("""INSERT INTO pagos (orden_id, forma, monto_usd, monto_real, moneda, cuenta, referencia, fecha, estado, confirmado_por, confirmado_en)
                       VALUES (?,?,?,?, 'USD', ?, ?, ?, ?, ?, ?)""",
                    (oid, forma or None, monto, monto, caja_de(forma), (referencia or "").strip() or None, fecha, pago,
                     uid if conf else None, fecha if conf else None))
    o = con.execute("SELECT total FROM ordenes WHERE id=?", (oid,)).fetchone()
    pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (oid,)).fetchone()[0]
    ep = "por_confirmar" if pago == "por_confirmar" else estado_pago_de(pagado, o["total"])
    con.execute("UPDATE ordenes SET estado_pago=? WHERE id=? AND estado_pago NOT IN ('por_cobrar','reembolsada')", (ep, oid))
    fijar_fecha_pago(con, oid)
    registrar(con, oid, uid, "pago", f"{concepto} {fmt_usd(monto)} · " + (f"{forma or 'sin forma'}" if pago == "confirmado" else
              (f"{forma} · por revisar" if pago else "se cobra después")) + (f" · {nota}" if nota else ""))
    return monto


def forma_por_cobrar(con, oid):
    """Cómo dijo el cliente que va a pagar lo que agregó y todavía no pagó (ej. el delivery 'por Pago Móvil')."""
    r = con.execute("""SELECT l.forma_pago FROM orden_lineas l WHERE l.orden_id=? AND l.extra_en IS NOT NULL AND l.forma_pago IS NOT NULL
                       AND NOT EXISTS (SELECT 1 FROM pagos p WHERE p.orden_id=l.orden_id AND p.estado='confirmado' AND p.forma=l.forma_pago
                                       AND ABS(p.monto_usd - l.total) < 0.01 AND substr(p.fecha,1,10) >= l.extra_en)
                       ORDER BY l.id DESC LIMIT 1""", (oid,)).fetchone()
    return r[0] if r else None


def ventas_por_dia(con, desde, hasta):
    """Ventas de cada día = lo que los clientes PAGARON ese día (Cristina, 6 oct): un pedido cuenta el día que se paga, y lo
    que se paga después (el delivery de un retiro de pack, un saldo) cuenta el día que entra. Lo que no se ha pagado no es venta.
    n = cuántos pedidos se pagaron ese día. Las entradas del Cash flow no cuentan: solo pedidos."""
    out = {}
    for r in entradas_ordenes(con, desde, hasta):
        x = out.setdefault(r["fecha"], [0, set()])
        x[0] += r["linea"] or 0
        if r["tipo"] == "pedido": x[1].add(r["oid"])
    return {d: (round(m, 2), len(n)) for d, (m, n) in out.items()}


def entradas_ordenes(con, desde="0000", hasta="9999", oids=None):
    """Lo que entró por cada orden, en filas como las del Registro de ventas, cada una el día en que se pagó:
    · 'pedido': lo que se compró, el día del primer pago, por lo que se pagó ese día (el delivery adentro si va con la misma forma)
    · 'saldo':  lo que faltaba del pedido y se pagó otro día (casi siempre el delivery)
    · 'extra':  lo que se le agregó después (delivery de un retiro de pack…), el día que quedó pagado
    Lo que todavía no se pagó no sale."""
    if oids is None:
        oids = [r[0] for r in con.execute("""SELECT DISTINCT o.id FROM ordenes o JOIN pagos p ON p.orden_id=o.id
                                             WHERE o.estado!='cancelada' AND p.estado='confirmado' AND substr(p.fecha,1,10) BETWEEN ? AND ?""",
                                          (desde, hasta))]
    filas = []
    for oid in oids:
        filas += [f for f in _entradas_de(con, oid) if desde <= f["fecha"] <= hasta]
    return filas


def _entradas_de(con, oid):
    o = con.execute("SELECT o.*, c.nombre cliente FROM ordenes o JOIN clientes c ON c.id=o.cliente_id WHERE o.id=?", (oid,)).fetchone()
    if not o or o["estado"] == "cancelada": return []
    pagos = con.execute("""SELECT id, substr(fecha,1,10) dia, monto_usd, forma FROM pagos WHERE orden_id=? AND estado='confirmado'
                           AND fecha IS NOT NULL AND fecha!='' ORDER BY fecha, id""", (oid,)).fetchall()
    if not pagos: return []
    dia_o = (o["creado_en"] or "")[:10]
    lineas = con.execute("SELECT * FROM orden_lineas WHERE orden_id=? ORDER BY id", (oid,)).fetchall()
    ivaf = 1 + o["iva"] / o["subtotal"] if (o["iva"] or 0) > 0 and (o["subtotal"] or 0) > 0 else 1
    despues = [l for l in lineas if l["extra_en"] and l["extra_en"] != dia_o]          # se agregó otro día
    es_deliv = lambda l: "delivery" in (l["nombre"] or "").lower()
    base = round((o["total"] or 0) - sum(l["total"] or 0 for l in despues), 2)        # el pedido tal como se vendió ese día
    comun = dict(oid=oid, numero=o["numero"], cliente=o["cliente"], origen="orden", fecha_original=None, dia_orden=dia_o)
    filas_p = []
    for l in lineas:
        if l["extra_en"] and l["extra_en"] != dia_o: continue
        if l["extra_en"] and es_deliv(l): continue                                      # delivery adelantado el mismo día: va con el delivery
        filas_p.append(dict(comun, producto=l["nombre"], precio=l["precio"], cantidad=0 if es_deliv(l) else l["cantidad"],
                            linea=round((l["total"] or 0) * (1 if l["extra_en"] else ivaf), 2), color=l["color"], malla=l["malla"],
                            personalizacion=l["personalizacion"], lid=l["id"], tipo="pedido"))
    deliv = round(base - sum(f["linea"] for f in filas_p), 2)                          # delivery neto (con descuentos ya restados)
    por_dia = {}
    for p in pagos: por_dia.setdefault(p["dia"], []).append(p)
    dias = sorted(por_dia)
    out = []
    # el día del primer pago: el pedido, por lo que se pagó ese día
    d1 = dias[0]; pag1 = round(sum(p["monto_usd"] or 0 for p in por_dia[d1]), 2)
    falta = round(max(base - pag1, 0), 2)
    sobra = round(max(pag1 - base, 0), 2)
    formas1 = list(dict.fromkeys(p["forma"] for p in por_dia[d1] if p["forma"]))
    forma1 = formas1[0] if formas1 else (o["forma_pago_prevista"] or None)
    del_pagado = round(deliv - falta, 2)
    for f in filas_p: f.update(fecha=d1, forma=forma1)
    if del_pagado < 0 and filas_p:                                                       # pagó menos que los productos: se muestra lo que entró
        quita = -del_pagado
        for f in reversed(filas_p):
            q = min(quita, f["linea"]); f["linea"] = round(f["linea"] - q, 2); quita = round(quita - q, 2)
            if quita <= 0: break
        del_pagado = 0
    if filas_p:
        out += filas_p
        if del_pagado > 0.009:
            if len(formas1) <= 1: filas_p[-1]["linea"] = round(filas_p[-1]["linea"] + del_pagado, 2)   # misma forma: dentro del producto
            else: out.append(dict(filas_p[-1], producto="Delivery", precio=del_pagado, cantidad=0, linea=del_pagado, forma=formas1[-1],
                                  color=None, malla=0, personalizacion=None, lid=filas_p[-1]["lid"] + 0.5))
    elif base > 0.009 and pag1 - sobra > 0.009:
        out.append(dict(comun, producto="Delivery", precio=pag1 - sobra, cantidad=0, linea=round(pag1 - sobra, 2), forma=forma1,
                        color=None, malla=0, personalizacion=None, lid=0, tipo="pedido", fecha=d1))
    # los días siguientes (y lo que sobró del primero): primero lo que faltaba del pedido, después lo que se agregó
    pend = [[l, l["total"] or 0] for l in sorted(despues, key=lambda l: (l["extra_en"], l["id"]))]
    for dia in dias:
        monto = sobra if dia == d1 else round(sum(p["monto_usd"] or 0 for p in por_dia[dia]), 2)
        forma = (por_dia[dia][-1]["forma"] or None)
        pid = por_dia[dia][-1]["id"]
        if falta > 0.009 and monto > 0.009 and dia != d1:
            a_ = round(min(monto, falta), 2)
            out.append(dict(comun, producto="Delivery" if falta <= max(deliv, 0) + 0.01 else "Saldo del pedido", precio=a_, cantidad=0,
                            linea=a_, forma=forma, color=None, malla=0, personalizacion=None, lid=5000000 + pid, tipo="saldo", fecha=dia))
            falta = round(falta - a_, 2); monto = round(monto - a_, 2)
        for x in pend:
            if monto <= 0.009: break
            l = x[0]
            if x[1] <= 0.009 or l["extra_en"] > dia: continue
            a_ = round(min(monto, x[1]), 2); x[1] = round(x[1] - a_, 2); monto = round(monto - a_, 2)
            if x[1] <= 0.009:   # quedó pagado completo: entra ese día
                out.append(dict(comun, producto=l["nombre"], precio=l["total"], cantidad=0 if es_deliv(l) else l["cantidad"], linea=l["total"],
                                forma=l["forma_pago"] or forma, color=l["color"], malla=l["malla"], personalizacion=l["personalizacion"],
                                lid=l["id"], tipo="extra", fecha=dia))
    return out


def por_cobrar_de_hoy(con, h):
    """Lo que los pedidos de hoy (y lo que se les agregó hoy) todavía deben: se muestra aparte, no es venta hasta que entre."""
    r = con.execute("""SELECT COALESCE(SUM(o.total - (SELECT COALESCE(SUM(p.monto_usd),0) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado')),0)
                       FROM ordenes o WHERE o.estado!='cancelada' AND o.estado_pago NOT IN ('por_cobrar','reembolsada')
                         AND (substr(o.creado_en,1,10)=? OR EXISTS (SELECT 1 FROM orden_lineas l WHERE l.orden_id=o.id AND l.extra_en=?))
                         AND o.total - (SELECT COALESCE(SUM(p.monto_usd),0) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado') > 0.009""",
                    (h, h)).fetchone()[0]
    return round(r or 0, 2)


def fijar_fecha_pago(con, oid):
    """La fecha de pago de la orden = el día del primer pago confirmado. Solo se pone si está vacía."""
    con.execute("""UPDATE ordenes SET fecha_pago=(SELECT substr(MIN(COALESCE(p.fecha,p.confirmado_en)),1,10) FROM pagos p
                   WHERE p.orden_id=ordenes.id AND p.estado='confirmado') WHERE id=? AND (fecha_pago IS NULL OR fecha_pago='')""", (oid,))


def estado_pago_de(pagado, total):
    """pagada si cubre el total; abonada SOLO si abonó una parte; si no ha pagado nada sigue 'por pagar'."""
    if pagado >= total - 0.01: return "pagada"
    return "abonada" if pagado > 0.009 else "sin_pago"

P_LABEL = {"sin_pago": "Por pagar", "por_confirmar": "Por revisar", "rechazado": "Pago rechazado", "abonada": "Pago parcial", "pagada": "Pagada", "contra_entrega": "Falta pagar",
           "por_cobrar": "Cashea · cuotas pendientes", "reembolsada": "Reembolsada"}
P_SUB = {"sin_pago": "por pagar", "por_confirmar": "por revisar", "rechazado": "PAGO RECHAZADO", "abonada": "pago parcial", "pagada": "", "contra_entrega": "falta pagar", "por_cobrar": "cuotas pendientes", "reembolsada": "reembolsada"}
RAZAS = ["Mestizo", "Akita", "Basset Hound", "Beagle", "Bichón Frisé", "Border Collie", "Boston Terrier", "Bóxer", "Bulldog Francés", "Bulldog Inglés", "Bull Terrier", "Caniche / Poodle",
         "Cavalier King Charles", "Chihuahua", "Chow Chow", "Cocker Spaniel", "Corgi", "Dálmata", "Doberman", "Dogo Argentino", "Golden Retriever", "Gran Danés", "Husky Siberiano", "Jack Russell",
         "Labrador", "Lhasa Apso", "Maltés", "Mastín", "Pastor Alemán", "Pastor Australiano", "Pequinés", "Pinscher", "Pitbull", "Pomerania", "Pug", "Rottweiler", "Salchicha / Dachshund",
         "Samoyedo", "San Bernardo", "Schnauzer", "Shar Pei", "Shih Tzu", "Terrier", "Weimaraner", "Westie", "Yorkshire"]
MODALIDAD = {"cobro_destino": "A cobro en destino", "pagado_decopet": "Envío pagado por Decopet"}
ENTREGA = {"pickup": "Pick-up", "delivery": "Delivery Caracas", "delivery_fuera": "Delivery fuera de Caracas", "nacional": "Envío nacional", "distribuidor": "Distribuidor", "otro": "Otro"}
DISTRIBUIDORES = ["Vivero Maracaibo"]
CANAL = {"whatsapp": "WhatsApp", "cashea": "Cashea", "web": "Página web", "shopify": "Shopify", "duwu": "Duwu", "vidapets": "Vidapets", "instagram": "Instagram", "presencial": "Presencial", "otro": "Otro"}
def cfg_json(con, clave, por_defecto=None):
    r = con.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
    return json.loads(r[0]) if r and r[0] else (por_defecto if por_defecto is not None else {})


FORMAS_PAGO = ["Pago Móvil", "Zelle", "Efectivo USD"]   # se reemplaza al arrancar con las cajas activas
FORMAS_COBRO = list(FORMAS_PAGO)   # solo las cajas donde un cliente puede pagar (se reemplaza al arrancar)
DESPACHADORES = ["Ingrid", "Cristina", "Fernando"]   # se reemplaza al arrancar con los activos de la tabla despachadores

def cargar_despachadores():
    con = sqlite3.connect(DB)
    DESPACHADORES[:] = [r[0] for r in con.execute("SELECT nombre FROM despachadores WHERE activo=1 ORDER BY nombre")]
    con.close()
AGENCIAS = ["Tealca", "MRW", "Zoom", "Domesa", "Liberty Express", "Otra"]
# Quién puede hacer qué (regla de la Parte 1)
PROVEEDORES_VISIBLES_TALLER = ("Walter",)   # al taller solo le hace falta saber quién le trae la madera


def proveedor_visible(rol, nombre):
    """Los proveedores son contactos comerciales de Cristina y solo los ve ella.
    El taller ve únicamente a Walter, con quien trata a diario; Logística, a nadie."""
    if rol == "admin": return nombre
    if rol == "taller": return nombre if (nombre or "") in PROVEEDORES_VISIBLES_TALLER else None
    return None


ORIGENES = ["Instagram", "Recomendación de otro cliente", "Página web / Google", "Cashea", "Nos vio en la calle",
            "Feria o evento", "Duwu", "Vidapets", "Ya era cliente", "Otro"]   # cómo nos conoció, distinto del canal por donde pidió

CONCEPTOS_EXTRA = ["Delivery", "Personalización", "Propina", "Repuesto adicional", "Ajuste", "Otro"]   # cosas que un cliente le agrega a una orden ya hecha

PERMISOS = {
    "admin": {"ver_cobros", "crear", "pago_por_confirmar", "confirmar_pago", "rechazar_pago", "precios", "coordinar", "despachar", "entregar", "editar_entrega", "incidencia", "reprogramar", "cancelar", "ver_dinero", "contra_entrega"},
    # ver_cobros: precios, totales y pagos de una orden (lo necesita para vender y coordinar).
    # ver_dinero: Cash flow, Resultados, márgenes, costos, cajas. Eso solo Cristina.
    "logistica": {"ver_cobros", "crear", "coordinar", "entregar", "editar_entrega", "incidencia", "reprogramar"},
    "taller": {"taller"},   # Isaías y Manawa: solo su pantalla. Nada de clientes, órdenes ni dinero.
    # El despachador SÍ ve dinero, pero solo el suyo: lo que se le debe por sus entregas.
    # No ve el de la empresa ni el de nadie más. Por eso es un rol aparte de Logística.
    "despachador": {"entregar", "mis_entregas", "incidencia"},
    "invitado": set(),      # nadie conectado: no puede hacer nada hasta entrar
    "ninguno": set(),       # está en el equipo (la nómina) pero no entra al ERP
    "sistema": set(),
}
# Tina (usuario de sistema) confirma sola los pagos digitales cuyo comprobante coincide; lo que no coincide queda "por revisar" para Cristina.
# Siguiente paso "natural" desde cada estado
SIGUIENTE = {"pendiente": ("entregada", "Marcar entregado"), "en_ruta": ("entregada", "Marcar entregado")}
PERMISO_ESTADO = {"pendiente": "confirmar_pago", "entregada": "entregar", "cancelada": "cancelar", "en_ruta": "entregar"}


def fmt_usd(v):
    try: return f"−${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"
    except (TypeError, ValueError): return "—"

def fmt_fecha(s, hora=False):
    if not s: return "—"
    s = str(s)
    try:
        d = datetime.datetime.fromisoformat(s[:16]) if len(s) > 10 else datetime.date.fromisoformat(s[:10])
    except ValueError: return s
    hoy = datetime.date.today(); dd = d.date() if isinstance(d, datetime.datetime) else d
    txt = "hoy" if dd == hoy else ("ayer" if dd == hoy - datetime.timedelta(days=1) else ("mañana" if dd == hoy + datetime.timedelta(days=1) else dd.strftime("%d/%m")))
    if hora and isinstance(d, datetime.datetime): txt += d.strftime(" %H:%M")
    return txt

def hace(s):
    if not s: return ""
    try: d = datetime.datetime.fromisoformat(str(s)[:16])
    except ValueError: return ""
    m = max(0, int((datetime.datetime.now() - d).total_seconds() // 60))
    if m < 60: return f"{m} min"
    if m < 48 * 60: return f"{m // 60} h"
    return f"{m // 1440} d"

def fmt_dia(s):
    """Fecha absoluta corta con día de la semana: 'lun 14/09'."""
    if not s: return "—"
    try: d = datetime.date.fromisoformat(str(s)[:10])
    except ValueError: return str(s)
    return f"{['lun','mar','mié','jue','vie','sáb','dom'][d.weekday()]} {d.strftime('%d/%m')}"

from markupsafe import Markup, escape
def usd_html(v):
    """Monto envuelto en <span class="dinero"> para poder ocultarlo con el botón del ojo."""
    return Markup(f'<span class="dinero">{escape(fmt_usd(v))}</span>')

def wa(tel):
    """Link de WhatsApp a partir de un teléfono venezolano 04XX-XXXXXXX."""
    s = str(tel or ""); d = "".join(ch for ch in s if ch.isdigit())
    if not d: return ""
    if s.strip().startswith("+"): return "https://wa.me/" + d
    if d.startswith("58"): return "https://wa.me/" + d
    return "https://wa.me/58" + d.lstrip("0")
def faltan_datos_agencia(con):
    """Envíos nacionales por despachar a los que les falta un dato que la agencia va a pedir (Cristina, 6 oct):
    oficina, cédula, teléfono o correo. Salen en Inicio para escribirle al cliente antes de que el despachador vaya."""
    out = []
    for o in con.execute("""SELECT o.id, o.numero, o.agencia, NULLIF(TRIM(o.direccion),'') direccion, NULLIF(TRIM(o.ciudad),'') ciudad,
                            NULLIF(TRIM(o.receptor_telefono),'') recibe_tel, COALESCE(NULLIF(c.nombre_pila,''), c.nombre) quien,
                            NULLIF(TRIM(c.telefono),'') telefono, NULLIF(TRIM(c.cedula),'') cedula, NULLIF(TRIM(c.correo),'') correo
                            FROM ordenes o JOIN clientes c ON c.id=o.cliente_id
                            WHERE o.tipo_entrega='nacional' AND o.estado IN ('pendiente','en_ruta') AND o.origen_excel=0
                            ORDER BY COALESCE(o.fecha_prometida, o.creado_en), o.id"""):
        faltan = [n for n, ok in (("la oficina", o["direccion"] or o["ciudad"]), ("la cédula", o["cedula"]),
                                  ("el teléfono", o["recibe_tel"] or o["telefono"]), ("el correo", o["correo"])) if not ok]
        if not faltan: continue
        lista = faltan[0] if len(faltan) == 1 else ", ".join(faltan[:-1]) + " y " + faltan[-1]
        ag = o["agencia"] or "la agencia"
        pide = lista.replace("la oficina", f"la oficina de {ag} donde lo vas a retirar").replace("la cédula", "tu cédula") \
                    .replace("el teléfono", "un teléfono de contacto").replace("el correo", "tu correo")
        msj = (f"¡Hola{' ' + o['quien'] if o['quien'] else ''}! 👋🏻 Te escribimos de Decopet 💚 Para enviarte tu pedido por {ag} "
               + (f"nos falta {pide}: la agencia lo pide para el envío. ¿Nos lo pasas? 🙌🏻" if len(faltan) == 1 else
                  f"nos faltan {pide}: la agencia los pide para el envío. ¿Nos los pasas? 🙌🏻"))
        tel = o["telefono"]
        out.append({"id": o["id"], "numero": o["numero"], "quien": o["quien"], "agencia": ag, "faltan": lista, "msj": msj,
                    "wa": ("https://api.whatsapp.com/send?phone=" + wa(tel).rsplit("/", 1)[1] + "&text=" + quote(msj)) if wa(tel) else ""})
    return out


def navegacion(maps, direccion=None, ciudad=None):
    """Botones 'Cómo llegar' del despachador: Google Maps y Waze ya navegando. Si el link trae coordenadas se usan
    (q=10.45,-66.81 · @10.45,-66.81); si no, Google abre el mismo link y Waze busca la dirección escrita."""
    from urllib.parse import unquote
    m = re.search(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)", unquote(maps or ""))
    if m:
        ll = f"{m.group(1)},{m.group(2)}"
        return {"maps": f"https://www.google.com/maps/dir/?api=1&destination={ll}&travelmode=driving",
                "waze": f"https://waze.com/ul?ll={ll}&navigate=yes", "exacto": True}
    texto = ", ".join(x for x in (direccion, ciudad or "Caracas") if x)   # sin link: es una búsqueda, puede no ser exacta (la pantalla lo dice)
    g = (maps if (maps or "").startswith("http") else ("https://" + maps if maps else "")) or \
        (f"https://www.google.com/maps/dir/?api=1&destination={quote(texto)}" if direccion else "")
    return {"maps": g, "waze": f"https://waze.com/ul?q={quote(texto)}&navigate=yes" if direccion else "", "exacto": bool(maps)}


def falta_ubicacion(con):
    """Deliverys por entregar sin link de ubicación (ni en el pedido ni en la dirección del cliente): sale en Inicio
    para pedirle la ubicación al cliente antes de que el despachador salga (Cristina, 6 oct)."""
    out = []
    for o in con.execute("""SELECT o.id, o.numero, o.cliente_id, o.direccion, o.maps, COALESCE(NULLIF(c.nombre_pila,''), c.nombre) quien, NULLIF(TRIM(c.telefono),'') telefono
                            FROM ordenes o JOIN clientes c ON c.id=o.cliente_id
                            WHERE o.tipo_entrega IN ('delivery','delivery_fuera') AND o.estado IN ('pendiente','en_ruta') AND o.origen_excel=0
                            ORDER BY COALESCE(o.fecha_prometida, o.creado_en), o.id"""):
        if ubicacion_de(con, o["cliente_id"], o["direccion"], o["maps"]): continue
        msj = (f"¡Hola{' ' + o['quien'] if o['quien'] else ''}! 👋🏻 Te escribimos de Decopet 💚 Para llevarte tu pedido, "
               f"¿nos compartes tu ubicación por aquí? 📍 Así el despachador llega directo 🙌🏻")
        out.append({"id": o["id"], "numero": o["numero"], "quien": o["quien"], "msj": msj,
                    "wa": ("https://api.whatsapp.com/send?phone=" + wa(o["telefono"]).rsplit("/", 1)[1] + "&text=" + quote(msj)) if wa(o["telefono"]) else ""})
    return out


def link_ubicacion(maps, direccion=None, ciudad=None):
    """El link que se le manda al cliente para que vea en el mapa a dónde va el despachador (un punto, no una ruta)."""
    from urllib.parse import unquote
    m = re.search(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)", unquote(maps or ""))
    if m: return f"https://www.google.com/maps?q={m.group(1)},{m.group(2)}"
    if maps: return maps if maps.startswith("http") else "https://" + maps
    return ""   # nunca un mapa armado con la dirección escrita: Maps puede ubicarla mal (Cristina, 6 oct)


def texto_aviso(cliente, despachador, lleva, manana=False, direccion=None, maps=None, ciudad=None):
    """El aviso que el despachador le manda al cliente antes de llevarle el pedido (Cristina, 6 oct: con 💚🐶👋🏻👀🙌🏻; en Decopet
    el corazón siempre es verde, nunca rojo). Lleva a dónde va y el link del mapa, y le pide confirmar que es su dirección."""
    que = re.sub(r"[⟪⟫]", "", str(lleva or "")).strip()
    lin = [f"¡Hola{' ' + cliente if cliente else ''}! 👋🏻 Soy {despachador} de Decopet 💚",
           (f"Mañana te llevo tu pedido: {que} 🐶" if manana else f"Estoy por llevarte tu pedido: {que} 🐶")]
    link = link_ubicacion(maps, direccion, ciudad)
    if link:   # ubicación guardada de verdad (la que mandó el cliente)
        lin.append(f"Voy a: {direccion}" if direccion else "Voy a esta ubicación:")
        lin.append(f"📍 {link}")
        lin.append("¿Me confirmas que es tu dirección? 🙏🏻")
    elif direccion:   # solo dirección escrita: se la muestra y le pide la ubicación, sin inventar un mapa
        lin.append(f"Voy a: {direccion}")
        lin.append("¿Me confirmas que es tu dirección y me mandas tu ubicación por aquí? 📍🙏🏻")
    else:
        lin.append("¿Me pasas tu dirección y tu ubicación por aquí? 📍")
    lin.append("Te aviso cuando esté en camino 🙌🏻" if manana else "Te aviso cuando esté cerca 👀")
    return "\n".join(lin)


def wa_aviso(tel, cliente, despachador, lleva, manana=False, direccion=None, maps=None, ciudad=None):
    """WhatsApp del cliente con el aviso ya escrito, para que el despachador solo le dé a enviar. Al tocarlo además se
    copia (ver mis_entregas.html): WhatsApp Web daña los emojis que llegan por enlace y así se pueden pegar bien."""
    base = wa(tel)
    if not base: return ""
    return "https://api.whatsapp.com/send?phone=" + base.rsplit("/", 1)[1] + "&text=" + quote(texto_aviso(cliente, despachador, lleva, manana, direccion, maps, ciudad))


tpl.env.filters.update(usd=usd_html, fecha=fmt_fecha, hace=hace, dia=fmt_dia, wa=wa)
tpl.env.globals["wa_aviso"] = wa_aviso
tpl.env.globals["texto_aviso"] = texto_aviso
tpl.env.globals["navegacion"] = navegacion
def wa_api(tel):
    """Chat de WhatsApp por el enlace directo (api.whatsapp.com): wa.me daña los emojis del texto al redirigir."""
    b = wa(tel)
    return ("https://api.whatsapp.com/send?phone=" + b.rsplit("/", 1)[1]) if b else ""
tpl.env.filters["wa_api"] = wa_api
tpl.env.globals["wa_texto"] = lambda tel, txt: (wa_api(tel) + "&text=" + quote(txt or "")) if wa_api(tel) else ""
# ¿esta caja empieza como la forma que dijo el despachador? ('Zelle' → 'Zelle Decopet')
tpl.env.tests["lower_empieza"] = lambda caja, dijo: bool(dijo) and (caja or "").lower().startswith((dijo or "").lower())
CIUDADES_VE = ["Caracas", "Los Teques", "Guarenas", "Guatire", "La Guaira", "Valencia", "Maracay", "Maracaibo", "Barquisimeto", "Puerto Ordaz", "Ciudad Bolívar", "Puerto La Cruz", "Barcelona", "Lechería",
               "Mérida", "San Cristóbal", "Maturín", "Cumaná", "Porlamar", "Valera", "Punto Fijo", "Coro", "Cabimas", "Acarigua", "Guanare", "San Felipe", "Barinas", "El Tigre", "Carúpano", "Charallave", "Cúa",
               "Puerto Cabello", "San Antonio de los Altos", "Higuerote", "Anaco", "Ciudad Ojeda", "San Francisco", "Tucacas", "Cagua", "Turmero", "Catia La Mar", "Quíbor", "Tucupita",
               "San Juan de los Morros", "San Carlos", "San Fernando de Apure", "Trujillo", "Puerto Ayacucho"]
# A qué estado pertenece cada ciudad: el estado se llena solo, nadie lo escribe.
ESTADO_DE_CIUDAD = {"Caracas": "Distrito Capital", "Los Teques": "Miranda", "Guarenas": "Miranda", "Guatire": "Miranda", "Charallave": "Miranda", "Cúa": "Miranda",
                    "La Guaira": "La Guaira", "Valencia": "Carabobo", "Maracay": "Aragua", "Maracaibo": "Zulia", "Cabimas": "Zulia", "Barquisimeto": "Lara",
                    "Puerto Ordaz": "Bolívar", "Ciudad Bolívar": "Bolívar", "Puerto La Cruz": "Anzoátegui", "Barcelona": "Anzoátegui", "Lechería": "Anzoátegui", "El Tigre": "Anzoátegui",
                    "Mérida": "Mérida", "San Cristóbal": "Táchira", "Maturín": "Monagas", "Cumaná": "Sucre", "Carúpano": "Sucre", "Porlamar": "Nueva Esparta",
                    "Valera": "Trujillo", "Punto Fijo": "Falcón", "Coro": "Falcón", "Acarigua": "Portuguesa", "Guanare": "Portuguesa", "San Felipe": "Yaracuy", "Barinas": "Barinas",
                    "Puerto Cabello": "Carabobo", "San Antonio de los Altos": "Miranda", "Higuerote": "Miranda", "Anaco": "Anzoátegui", "Ciudad Ojeda": "Zulia",
                    "San Francisco": "Zulia", "Tucacas": "Falcón", "Cagua": "Aragua", "Turmero": "Aragua", "Catia La Mar": "La Guaira", "Quíbor": "Lara", "Tucupita": "Delta Amacuro",
                    "San Juan de los Morros": "Guárico", "San Carlos": "Cojedes", "San Fernando de Apure": "Apure", "Trujillo": "Trujillo", "Puerto Ayacucho": "Amazonas"}
# Si el cliente dice solo el estado, se le pone la ciudad principal de ese estado (decisión de Cristina, 3 oct 2026: filtrar solo por ciudad)
CIUDAD_DEL_ESTADO = {"Amazonas": "Puerto Ayacucho", "Anzoátegui": "Barcelona", "Apure": "San Fernando de Apure", "Aragua": "Maracay", "Barinas": "Barinas",
                     "Bolívar": "Ciudad Bolívar", "Carabobo": "Valencia", "Cojedes": "San Carlos", "Delta Amacuro": "Tucupita", "Distrito Capital": "Caracas",
                     "Falcón": "Coro", "Guárico": "San Juan de los Morros", "La Guaira": "La Guaira", "Lara": "Barquisimeto", "Mérida": "Mérida",
                     "Miranda": "Los Teques", "Monagas": "Maturín", "Nueva Esparta": "Porlamar", "Portuguesa": "Guanare", "Sucre": "Cumaná",
                     "Táchira": "San Cristóbal", "Trujillo": "Trujillo", "Yaracuy": "San Felipe", "Zulia": "Maracaibo"}
ESTADOS_VE = ["Amazonas", "Anzoátegui", "Apure", "Aragua", "Barinas", "Bolívar", "Carabobo", "Cojedes", "Delta Amacuro", "Distrito Capital", "Falcón", "Guárico",
              "La Guaira", "Lara", "Mérida", "Miranda", "Monagas", "Nueva Esparta", "Portuguesa", "Sucre", "Táchira", "Trujillo", "Yaracuy", "Zulia"]
def _llave_lugar(s):
    """'  CARACAS ' / 'caracas' / 'Merida' → 'caracas' / 'merida': sin acentos, mayúsculas ni espacios de más."""
    import unicodedata
    s = unicodedata.normalize("NFD", (s or "").strip().lower())
    return " ".join("".join(c for c in s if unicodedata.category(c) != "Mn").replace(".", " ").split())
_CIUDAD_POR_LLAVE = {_llave_lugar(c): c for c in CIUDADES_VE} | {"ccs": "Caracas", "distrito capital": "Caracas", "dtto capital": "Caracas", "puerto la cruz": "Puerto La Cruz",
                                                                    "pto la cruz": "Puerto La Cruz", "pto ordaz": "Puerto Ordaz",
                                                                    "lecherias": "Lechería", "las adjuntas": "Caracas", "por definir": "Pendiente"}
_ESTADO_POR_LLAVE = {_llave_lugar(e): e for e in ESTADOS_VE} | {"edo miranda": "Miranda", "estado miranda": "Miranda", "vargas": "La Guaira", "dtto capital": "Distrito Capital",
                                                              "margarita": "Nueva Esparta", "isla de margarita": "Nueva Esparta"}
def normalizar_ciudad(texto):
    """Lo que escribió la persona (o el bot) → (ciudad, estado), siempre igual para poder filtrar.
    'caracas' → ('Caracas', 'Distrito Capital'). Si dijo un estado y no la ciudad ('Miranda') → ('Pendiente', 'Miranda'):
    queda marcado para preguntarle. Una ciudad que no está en la lista se guarda tal cual, con mayúscula inicial."""
    t = (texto or "").strip()
    if not t: return None, None
    if t.lower().startswith(("edo.", "edo ", "estado ")): t = t.split(" ", 1)[-1] if " " in t else t[4:]
    k = _llave_lugar(t)
    if k in _CIUDAD_POR_LLAVE:
        c = _CIUDAD_POR_LLAVE[k]; return c, ESTADO_DE_CIUDAD.get(c)
    if k in _ESTADO_POR_LLAVE:
        e = _ESTADO_POR_LLAVE[k]; return CIUDAD_DEL_ESTADO.get(e, "Pendiente"), e
    if k == "pendiente": return "Pendiente", None
    return " ".join(p.lower() if i and p.lower() in ("de", "del", "la", "las", "los", "el") else p[:1].upper() + p[1:] for i, p in enumerate(t.split())), None
def fmt_cant(v, unidad=None):
    """37.8 → '37,8' y 20.0 → '20'. Con unidad: '37,8 litros', '1 rollo'."""
    v = round(float(v or 0), 1)
    n = str(int(v)) if v.is_integer() else f"{v:.1f}".replace(".", ",")
    if not unidad: return n
    if v == 1: return f"{n} {unidad}"
    if unidad.endswith("ón"): return f"{n} {unidad[:-2]}ones"   # galón → galones (sin tilde en el plural)
    if unidad.endswith("ete"): return f"{n} {unidad}s"          # cuñete → cuñetes
    return f"{n} {unidad}{'s' if unidad[-1] in 'aeiou' else 'es'}"
tpl.env.filters["cant"] = fmt_cant
# el color del plato (Slow Chow, comedores) resaltado, para no entregar el que no es
def _platos(t):
    t = t if isinstance(t, Markup) else escape(t or "")
    return Markup(re.sub(r"(?<![\w-])(?:plato )?(azul|rosado)\b", r'<span class="pl-\1">plato \1</span>', str(t)))
tpl.env.filters["platos"] = _platos
# "Pack 3 Repuestos Mediano⟪Lleva 1 de 3⟫" → el producto y, debajo, resaltado cuánto se lleva hoy
tpl.env.filters["lleva"] = lambda t: _platos(Markup(re.sub(r"⟪(.*?)⟫", r'<span class="lleva-tag">\1</span>', str(escape(t or "")))))
# "el 14/09", pero "hoy" / "ayer" / "mañana" sin el "el" delante (no "desde el hoy")
tpl.env.filters["el_fecha"] = lambda v, hora=False: (lambda t: t if t in ("hoy", "ayer", "mañana", "—") or t.split(" ")[0] in ("hoy", "ayer", "mañana") else "el " + t)(fmt_fecha(v, hora))
tpl.env.filters["fromiso"] = lambda v: datetime.date.fromisoformat(v) if v else None
tpl.env.globals.update(entorno=entorno, ORIGENES=ORIGENES, proveedor_visible=proveedor_visible, CONCEPTOS_EXTRA=CONCEPTOS_EXTRA, CIUDADES_VE=CIUDADES_VE, RAZAS=RAZAS, MODALIDAD=MODALIDAD, P_SUB=P_SUB, DISTRIBUIDORES=DISTRIBUIDORES, ESTADOS=ESTADOS, E_LABEL=E_LABEL, P_LABEL=P_LABEL, ENTREGA=ENTREGA, CANAL=CANAL, FORMAS_PAGO=FORMAS_PAGO, FORMAS_COBRO=FORMAS_COBRO, DESPACHADORES=DESPACHADORES, AGENCIAS=AGENCIAS, SIGUIENTE=SIGUIENTE)


def db():
    con = sqlite3.connect(DB, check_same_thread=False); con.row_factory = sqlite3.Row; con.execute("PRAGMA foreign_keys=ON")
    try: yield con
    finally: con.close()

# Cada rol restringido tiene su lista de lo que puede abrir. Todo lo demás lo devuelve a su pantalla.
PUERTAS = {
    "taller":      (("/taller", "/inventario", "/static", "/fotos", "/ver-como", "/favicon", "/salir", "/entrar"), "/taller"),
    "despachador": (("/mis-entregas", "/tarifas", "/ordenes/", "/viajes/", "/static", "/fotos", "/ver-como", "/favicon", "/salir", "/entrar"), "/mis-entregas"),
    # Logística coordina y entrega: ve órdenes, clientes y despachos, nunca plata ni catálogo con precios.
    # Lista cerrada: una página nueva no la ve hasta que se agregue aquí a propósito.
    "logistica":   (("/inicio", "/operaciones", "/ordenes", "/clientes", "/mascotas", "/inventario", "/despachadores",
                     "/packs", "/prepagados", "/seguimientos", "/miembros", "/static", "/fotos", "/ver-como", "/favicon", "/salir", "/entrar"), "/operaciones"),
}
TALLER_PERMITIDO = PUERTAS["taller"][0]


ABIERTO = ("/entrar", "/static", "/favicon", "/salir", "/robots.txt")   # lo único que se puede abrir sin haber entrado

MAX_SUBIDA = 25 * 1024 * 1024        # nadie necesita subir más de 25 MB de una vez

# Instrucciones para el navegador y para los buscadores. Van en cada respuesta.
ESCUDOS = {
    "X-Robots-Tag": "noindex, nofollow",      # que ningún buscador lo guarde
    "X-Content-Type-Options": "nosniff",      # que no adivine el tipo de un archivo
    "X-Frame-Options": "DENY",                # que nadie meta el ERP dentro de otra página
    "Referrer-Policy": "same-origin",         # que no cuente a dónde vas
    "Cache-Control": "no-store",              # que no deje páginas guardadas en el disco
}


def base_responde():
    try:
        con = sqlite3.connect(DB, timeout=2)
        try: con.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone(); return True
        finally: con.close()
    except sqlite3.Error:
        return False


def viene_de_fuera(request):
    """Una orden que llega desde otra página web. Así funciona el engaño de hacerte hacer clic
    en un sitio cualquiera para que tu navegador, ya con tu sesión abierta, haga algo aquí."""
    if request.method not in ("POST", "PUT", "DELETE"): return False
    origen = request.headers.get("origin") or request.headers.get("referer") or ""
    if not origen: return False                      # sin dato no se puede juzgar; el navegador siempre lo manda
    from urllib.parse import urlparse
    return urlparse(origen).netloc != (request.headers.get("host") or "")


@app.middleware("http")
async def puerta(request: Request, call_next):
    """La puerta del ERP. Se comprueba aquí y no página por página, para que valga
    también para lo que se agregue después:
      · sin haber entrado, solo la pantalla de entrada
      · el taller y los despachadores, solo lo suyo"""
    ruta = request.url.path

    def con_escudos(r):
        for k, v in ESCUDOS.items(): r.headers[k] = v
        return r

    if ruta == "/robots.txt":
        return con_escudos(PlainTextResponse("User-agent: *\nDisallow: /\n"))
    if ruta == "/favicon.ico":   # la huellita de Decopet en la pestaña (el navegador la pide aquí aunque la página diga otra cosa)
        return con_escudos(FileResponse(BASE / "static" / "favicon.ico", media_type="image/x-icon", headers={"Cache-Control": "public, max-age=604800"}))
    if ruta == "/health":   # para Railway: ¿está vivo y puede leer la base? Sin entrar y sin contar nada más.
        vivo = base_responde()
        return con_escudos(PlainTextResponse("ok" if vivo else "mal", status_code=200 if vivo else 503))
    # Cloudflare Access (solo en el servidor, con CF_ACCESS_ENFORCE=1): sin su firma no se llega a nada, ni a la
    # pantalla de entrada ni a las fotos. A quien entra se le dice solo "Forbidden"; el motivo queda en el registro.
    if CF_ACCESS.activo():
        try:
            request.state.cf_access = CF_ACCESS.validar(request.headers.get("cf-access-jwt-assertion", ""))
        except Exception as e:
            print(f"ACCESS rechazó {request.method} {ruta}: {e}", flush=True)
            return con_escudos(PlainTextResponse("Forbidden", status_code=403))
    if viene_de_fuera(request):
        return con_escudos(JSONResponse({"error": "Esa orden no salió de tu ERP"}, status_code=403))
    try:
        if int(request.headers.get("content-length") or 0) > MAX_SUBIDA:
            return con_escudos(JSONResponse({"error": "Eso pesa demasiado"}, status_code=413))
    except ValueError:
        return con_escudos(JSONResponse({"error": "Orden mal formada"}, status_code=400))
    if not ruta.startswith(ABIERTO):
        if (tiene_duena() or EN_SERVIDOR) and not quien_es(request):
            return con_escudos(RedirectResponse("/entrar", status_code=303))
        permitido, casa = PUERTAS.get(rol_de(request), (None, None))
        if permitido and not ruta.startswith(permitido):
            return con_escudos(RedirectResponse(casa, status_code=303))
        # De las órdenes, el despachador solo puede marcar las suyas (en camino / entregada). Nunca abrir la ficha ni exportar.
        if rol_de(request) == "despachador" and ruta.startswith("/ordenes/") and not (
                request.method == "POST" and re.fullmatch(r"/ordenes/\d+/(estado|no-recibio)", ruta)):
            return con_escudos(RedirectResponse(casa, status_code=303))
        # De los viajes a la agencia, el despachador solo puede decir "ya los llevé". Crear o borrar viajes, no.
        if rol_de(request) == "despachador" and ruta.startswith("/viajes/") and not (
                request.method == "POST" and re.fullmatch(r"/viajes/\d+/llevado", ruta)):
            return con_escudos(RedirectResponse(casa, status_code=303))
    resp = con_escudos(await call_next(request))
    # Fotos, logos y estilos sí se guardan en el navegador (solo en él: "private"). Las miniaturas con su ?v= no cambian
    # nunca; lo demás se vuelve a pedir solo si cambió (el servidor contesta "igual que antes" sin mandarlo otra vez).
    if ruta.startswith(("/static/", "/fotos")) and resp.status_code in (200, 304):
        fija = ruta.startswith("/fotos-mini/") and "v" in request.query_params
        resp.headers["Cache-Control"] = "private, max-age=31536000, immutable" if fija else "private, no-cache"
    return resp


# Columnas que se fueron agregando con el tiempo y no están en modelo.sql.
# Se aplican al arrancar, así una base nueva queda igual que la que está en uso.
COLUMNAS = (
    ("abonos_produccion", "gasto_id", "INTEGER"),
    ("clientes", "origen", "TEXT"), ("clientes", "origen_nota", "TEXT"),
    ("clientes", "porche_tamano", "TEXT"), ("clientes", "porche_version", "TEXT"),
    ("clientes", "referido_id", "INTEGER"),
    ("compromisos", "precio_unitario", "REAL"), ("compromisos", "unidad", "TEXT"),
    ("compromisos_pagos", "motivo", "TEXT"),
    ("gastos", "cantidad", "REAL"), ("gastos", "compra_grande", "INTEGER NOT NULL DEFAULT 0"),
    ("gastos", "unidad", "TEXT"),
    ("mov_inventario", "color", "TEXT"),
    ("movimientos", "categoria", "TEXT"), ("movimientos", "comprobante", "TEXT"),
    ("movimientos", "notas", "TEXT"), ("movimientos", "subcategoria", "TEXT"),
    ("tarifas", "fuera_caracas", "INTEGER NOT NULL DEFAULT 0"),
    ("danados", "reparando_en", "TEXT"), ("danados", "reparando_por", "INTEGER"), ("danados", "arregla", "TEXT"), ("danados", "visto", "INTEGER NOT NULL DEFAULT 0"),
    ("notas_taller", "produccion_id", "INTEGER"), ("notas_taller", "danado_id", "INTEGER"),
    ("notas_taller", "agotando_id", "INTEGER"), ("notas_taller", "agotando_color", "TEXT"),
    ("notas_taller", "resuelto", "INTEGER NOT NULL DEFAULT 0"), ("notas_taller", "resuelto_en", "TEXT"),
    ("ordenes", "despachador_pagado", "INTEGER NOT NULL DEFAULT 0"),
    ("pagos_despachador", "adelanto_usado", "REAL NOT NULL DEFAULT 0"), ("pagos_despachador", "confirmado_en", "TEXT"),
    ("pagos_despachador", "reclamo_monto", "REAL"), ("pagos_despachador", "reclamo_nota", "TEXT"), ("pagos_despachador", "reclamo_en", "TEXT"),
    ("pagos_despachador", "reclamo_resuelto", "TEXT"), ("pagos_despachador", "gasto_id", "INTEGER"),
    ("orden_lineas", "perso_lista", "INTEGER NOT NULL DEFAULT 0"), ("orden_lineas", "perso_lista_en", "TEXT"),
    ("orden_lineas", "extra_en", "TEXT"),   # cobro que se agregó después de la compra: el día en que entró
    ("viajes_despachador", "tipo", "TEXT NOT NULL DEFAULT 'fallido'"), ("viajes_despachador", "pack_id", "INTEGER"),
    ("viajes_despachador", "prepagado_id", "INTEGER"), ("viajes_despachador", "por_aprobar", "INTEGER NOT NULL DEFAULT 0"),
    ("despachadores", "cobra_viernes", "INTEGER NOT NULL DEFAULT 1"),
    ("cuentas", "con_detalle", "INTEGER NOT NULL DEFAULT 0"),   # 1 = al pasar el mouse muestra quién debe (Cuentas Por Cobrar)   # 0 = se le paga cuando Cristina decida (la despachadora Cristina)
    ("packs", "en_ruta", "INTEGER NOT NULL DEFAULT 0"),
    ("ordenes", "despachador_pago_id", "INTEGER"), ("ordenes", "en_registro", "INTEGER DEFAULT 0"),
    ("ordenes", "factura_fecha", "TEXT"), ("ordenes", "factura_hecha", "INTEGER DEFAULT 0"),
    ("ordenes", "factura_numero", "TEXT"), ("ordenes", "factura_por", "INTEGER"),
    ("ordenes", "pago_despachador", "REAL"), ("ordenes", "receptor_cedula", "TEXT"),
    ("ordenes", "receptor_correo", "TEXT"), ("ordenes", "requiere_factura", "INTEGER DEFAULT 0"),
    ("ordenes", "viaje_id", "INTEGER"),
    ("packs", "deliveries_prepagados", "INTEGER DEFAULT 0"), ("packs", "delivery_pagado", "INTEGER"), ("packs", "tarifa_prepagada", "REAL"), ("packs", "delivery_diferencia", "REAL"), ("packs", "diferencia_pagada", "INTEGER"),
    ("packs", "delivery_programado", "REAL"), ("packs", "despachador_programado", "TEXT"),
    ("packs", "fecha_programada", "TEXT"), ("packs", "nota_programada", "TEXT"),
    ("packs", "retiro_programado", "INTEGER"), ("packs", "tipo_programado", "TEXT"),
    ("produccion", "cantidad", "INTEGER NOT NULL DEFAULT 1"), ("produccion", "fecha_pago", "TEXT"),
    ("produccion", "faltaron", "INTEGER"),
    ("cuentas", "cobra", "INTEGER DEFAULT 1"),
    ("mov_inventario", "lote", "TEXT"),   # los movimientos de un mismo armado van juntos, para poder deshacerlo   # ¿se usa para cobrarle a un cliente? las de inversión o personales, no   # al cerrar un pedido incompleto: cuántos no llegaron
    ("produccion", "recibido", "INTEGER DEFAULT 0"), ("produccion", "tipo_pedido", "TEXT DEFAULT 'produccion'"),
    ("productos", "canales", "TEXT"), ("productos", "proveedor", "TEXT"), ("productos", "unidad", "TEXT"),
    ("pagos", "en_cashflow", "INTEGER NOT NULL DEFAULT 0"),   # ya lo pasó Cristina al libro a mano
    ("viajes_agencia", "llevado_en", "TEXT"),
    ("viajes_agencia", "oficina", "TEXT"),
    ("clientes", "sin_mascota_ok", "TEXT"),   # el cliente no quiso dar los datos de su perro: ya no se le recuerda en Inicio (fecha)   # a qué oficina de esa agencia lo lleva (Tealca: Los Palos Grandes $5 o Catia $10)
    ("registro_ventas", "fecha_original", "TEXT"), ("registro_ventas", "inicial", "REAL"), ("registro_ventas", "cuota1", "REAL"),   # el Excel tal cual
    ("registro_ventas", "cuota2", "REAL"), ("registro_ventas", "cuota3", "REAL"), ("registro_ventas", "orden_excel", "TEXT"),   # vacío = asignado; con fecha = ya los llevó a la agencia (recién ahí se le debe)
    ("usuarios", "usuario", "TEXT"), ("usuarios", "clave_hash", "TEXT"), ("usuarios", "creado_en", "TEXT"),
    ("usuarios", "despachador", "TEXT"),   # a qué despachador corresponde este usuario
    ("usuarios", "correo", "TEXT"),        # con el que entra por Cloudflare (en el servidor no hay claves del ERP)
    ("usuarios", "nomina", "INTEGER NOT NULL DEFAULT 0"), ("usuarios", "sueldo_mes", "REAL"),   # está en la nómina de Equipo
    ("usuarios", "visto_en", "TEXT"),      # la última vez que abrió algo del ERP
    ("repuestos_prepagados", "agencia", "TEXT"),
    ("repuestos_prepagados", "delivery", "REAL NOT NULL DEFAULT 0"),
    ("repuestos_prepagados", "delivery_forma", "TEXT"),
    ("repuestos_prepagados", "delivery_pagado", "INTEGER NOT NULL DEFAULT 0"),
    ("repuestos_prepagados", "en_ruta", "INTEGER NOT NULL DEFAULT 0"),
    ("repuestos_prepagados", "envio", "TEXT"), ("repuestos_prepagados", "monto", "REAL"),
)


def preparar_base(ruta):
    """Deja una base lista para usar: crea las tablas y agrega las columnas que falten.
    Sirve igual para la base en uso y para una recién creada."""
    con = sqlite3.connect(ruta)
    # WAL: quien lee no espera a quien guarda. Con varias personas a la vez, más la tasa BCV y el
    # respaldo trabajando de fondo, sin esto aparece "database is locked". Queda grabado en la base.
    con.execute("PRAGMA journal_mode=WAL")
    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "viajes_fallidos" in tablas and "viajes_despachador" not in tablas:   # la primera versión se llamaba así
        con.execute("ALTER TABLE viajes_fallidos RENAME TO viajes_despachador")
    con.executescript((BASE / "modelo.sql").read_text())
    hay = {}
    for tabla, col, tipo in COLUMNAS:
        if tabla not in hay: hay[tabla] = {r[1] for r in con.execute(f"PRAGMA table_info({tabla})")}
        if col not in hay[tabla]:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}"); hay[tabla].add(col)
    con.commit()
    aplicar_migraciones(con)
    return con


MIGRACIONES = BASE / "migraciones"


def aplicar_migraciones(con):
    """Cambios de la base que no son "agregar una tabla o columna": renombrar, mover datos, cargar valores iniciales.
    Cada uno es un archivo plataforma/migraciones/NNN_que_hace.sql y se aplica UNA sola vez, en orden, en cada base
    (la Mac, el servidor). Así un cambio que hace Claude llega igual a producción al desplegar.
    Si uno falla, no queda nada a medias de ese archivo y el ERP no arranca: en el servidor sigue la versión anterior."""
    con.execute("CREATE TABLE IF NOT EXISTS migraciones (nombre TEXT PRIMARY KEY, aplicada_en TEXT DEFAULT (datetime('now','localtime')))")
    hechas = {r[0] for r in con.execute("SELECT nombre FROM migraciones")}
    for f in sorted(MIGRACIONES.glob("[0-9][0-9][0-9]_*.sql")):
        if f.name in hechas: continue
        try:
            nombre = f.name.replace("'", "''")
            con.executescript(f"BEGIN;\n{f.read_text(encoding='utf-8')}\n;\nINSERT INTO migraciones (nombre) VALUES ('{nombre}');\nCOMMIT;")
        except sqlite3.Error as e:
            if con.in_transaction: con.rollback()
            raise RuntimeError(f"La migración {f.name} falló y no se aplicó nada de ella: {e}") from e


@app.on_event("startup")
def _arranque():
    con = preparar_base(DB)
    if not con.execute("SELECT 1 FROM despachadores").fetchone():   # primera vez: la lista fija pasa a la tabla (los históricos quedan inactivos)
        for n in DESPACHADORES: con.execute("INSERT OR IGNORE INTO despachadores (nombre, activo) VALUES (?,1)", (n,))
        for (n,) in con.execute("SELECT DISTINCT despachador FROM ordenes WHERE despachador IS NOT NULL AND despachador!=''").fetchall():
            con.execute("INSERT OR IGNORE INTO despachadores (nombre, activo) VALUES (?,0)", (n,))
        con.commit()
    con.close(); cargar_despachadores(); cargar_formas_pago(); cargar_ajustes()
    if os.environ.get("DECOPET_PRUEBAS") != "1":   # las pruebas no respaldan ni preparan fotos
        arrancar_respaldo()
        threading.Thread(target=preparar_miniaturas, daemon=True, name="miniaturas").start()
    bcv.programar(DB)


def tasa_hoy(con):
    return bcv.tasa_actual(con)


def normalizar_telefono(s):
    """Deja el teléfono como 04XX-XXXXXXX (o +58…). Si no se puede, lo devuelve limpio de espacios."""
    if not s: return None
    d = re.sub(r"\D", "", s)
    if d.startswith("58") and len(d) == 12: d = "0" + d[2:]
    if len(d) == 11 and d.startswith("0"): return f"{d[:4]}-{d[4:]}"
    if len(d) == 10 and d[0] in "24": return f"0{d[:3]}-{d[3:]}"
    return s.strip()


def guardar_mascotas(con, cid, f, prefijo="mascota_"):
    """Nombre + raza + cumpleaños. El cumpleaños puede venir como fecha completa, o como día/mes (25/09) con año opcional."""
    cumples = f.getlist(prefijo + "cumple"); anios = f.getlist(prefijo + "anio")
    for i, (n, r, fn, pk) in enumerate(zip(f.getlist(prefijo + "nombre"), f.getlist(prefijo + "raza"), f.getlist(prefijo + "nacimiento"), f.getlist(prefijo + "peso"))):
        if not (n and n.strip()): continue
        md = None
        c = (cumples[i] if i < len(cumples) else "").strip(); a = (anios[i] if i < len(anios) else "").strip()
        m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})$", c)
        if m: md = f"{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        if md and re.match(r"^(19|20)\d{2}$", a): fn = f"{a}-{md}"; md = None
        con.execute("INSERT INTO mascotas (cliente_id,nombre,raza,fecha_nacimiento,cumple_mes_dia,peso_kg) VALUES (?,?,?,?,?,?)", (cid, n.strip(), (r or "").strip() or None, fn or None, md, float(pk) if pk else None))


def capitalizar(nombre, inicio=True):
    """Nombres siempre con mayúscula inicial y el resto en minúscula: 'CRISTINA RAFFALLI' y 'cristina raffalli' → 'Cristina Raffalli'.
    'de', 'del', 'la', 'los'… van en minúscula salvo al empezar el nombre ('María de los Ángeles', 'Roberto dos Santos').
    Respeta guiones y apóstrofos: 'Pérez-Gómez', 'O’Brien'. inicio=False es para el apellido suelto (la partícula va en minúscula)."""
    import unicodedata
    nombre = unicodedata.normalize("NFC", " ".join((nombre or "").split()))   # "i" + tilde suelta → "í": si no, "MaríA"
    if not nombre: return nombre
    minus = {"de", "del", "la", "las", "los", "y"}   # Di Giacomo, Da Silva, Dos Santos van con mayúscula
    def palabra(w): return re.sub(r"[^\W\d_]+", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), w)   # cada tramo de letras: O’brien → O’Brien
    return " ".join(w.lower() if ((i or not inicio) and w.lower() in minus) else palabra(w) for i, w in enumerate(nombre.split()))


def nombre_completo(nombre_pila, apellido):
    return capitalizar(" ".join(x.strip() for x in (nombre_pila or "", apellido or "") if x and x.strip()))


# ------------------------------------------------------------------ QUIÉN ERES
# La clave nunca se guarda: se guarda una huella de la que no se puede volver atrás.
def _huella(clave, sal):
    return hashlib.pbkdf2_hmac("sha256", clave.encode(), bytes.fromhex(sal), 200_000).hex()


def cifrar_clave(clave):
    sal = secrets.token_hex(16)
    return f"{sal}${_huella(clave, sal)}"


def clave_correcta(clave, guardado):
    if not guardado or "$" not in (guardado or ""): return False
    sal, huella = guardado.split("$", 1)
    return secrets.compare_digest(_huella(clave or "", sal), huella)


DURACION_SESION = 12 * 60 * 60     # 12 horas: una jornada
MAX_INTENTOS, VENTANA_INTENTOS = 8, 15     # 8 intentos fallidos en 15 minutos y se cierra


def ip_de(request):
    """La IP de quien entra, para el freno de intentos. Con Cloudflare delante, la que llega es la de Cloudflare
    (sería la misma para todos): la real viene en CF-Connecting-IP. Solo se le cree con Access activo, porque
    entonces nadie llega sin pasar por Cloudflare; si no, cualquiera podría inventarse esa cabecera."""
    if CF_ACCESS.activo() and request.headers.get("cf-connecting-ip"): return request.headers["cf-connecting-ip"].strip()
    return (request.client.host if request.client else "") or ""


def frenado(con, usuario, ip):
    """¿Ya probó demasiadas veces? Frena al robot que prueba claves una tras otra."""
    con.execute("DELETE FROM intentos WHERE cuando < datetime('now','localtime','-1 hour')")
    n = con.execute("""SELECT COUNT(*) FROM intentos
                       WHERE (usuario=? OR ip=?) AND cuando >= datetime('now','localtime',?)""",
                    (usuario, ip, f"-{VENTANA_INTENTOS} minutes")).fetchone()[0]
    return n >= MAX_INTENTOS


def anotar_intento(con, usuario, ip):
    con.execute("INSERT INTO intentos (usuario, ip) VALUES (?,?)", (usuario, ip)); con.commit()


def cookie_segura(request):
    """Solo por HTTPS cuando el ERP esté publicado. En tu Mac no aplica."""
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def abrir_sesion(con, uid):
    ficha = secrets.token_urlsafe(32)
    vence = (datetime.datetime.now() + datetime.timedelta(seconds=DURACION_SESION)).isoformat(" ", "seconds")
    con.execute("DELETE FROM sesiones WHERE vence_en < datetime('now','localtime')")
    con.execute("INSERT INTO sesiones (ficha, usuario_id, vence_en) VALUES (?,?,?)", (ficha, uid, vence))
    con.commit(); return ficha


def correo_cf(request):
    """El correo que Cloudflare comprobó con su código. Solo existe con Access activo (en el servidor)."""
    if not CF_ACCESS.activo(): return ""
    return ((getattr(request.state, "cf_access", None) or {}).get("email") or "").strip().lower()


def quien_es(request: Request):
    """La persona conectada, o None.
    En el servidor es aquella cuyo correo comprobó Cloudflare: ahí no hay claves del ERP, solo el código que le
    llega por correo. En la Mac, la de la ficha de sesión del navegador (entró con su clave)."""
    if hasattr(request.state, "quien"): return request.state.quien   # se pregunta varias veces en un mismo pedido
    con = sqlite3.connect(DB, timeout=0.5); con.row_factory = sqlite3.Row
    try:
        if CF_ACCESS.activo():
            correo = correo_cf(request)
            u = correo and con.execute("""SELECT * FROM usuarios WHERE lower(correo)=? AND activo=1
                                          AND rol NOT IN ('ninguno','sistema')""", (correo,)).fetchone()
        else:
            ficha = request.cookies.get("sesion")
            u = ficha and con.execute("""SELECT u.* FROM sesiones s JOIN usuarios u ON u.id=s.usuario_id
                                         WHERE s.ficha=? AND s.vence_en >= datetime('now','localtime') AND u.activo=1
                                         AND u.rol!='ninguno'""", (ficha,)).fetchone()
        # "visto por última vez": como mucho cada 5 minutos, y si la base está ocupada guardando otra
        # cosa (esta misma petición a mitad de un cambio), se deja para la próxima. Nunca debe tumbar nada.
        if u:
            try:
                con.execute("""UPDATE usuarios SET visto_en=datetime('now','localtime') WHERE id=?
                               AND (visto_en IS NULL OR visto_en < datetime('now','localtime','-5 minutes'))""", (u["id"],)); con.commit()
            except sqlite3.OperationalError:
                pass
        request.state.quien = dict(u) if u else None
        return request.state.quien
    finally:
        con.close()


def tiene_duena(con=None):
    """¿El ERP ya tiene con quién entrar? En el servidor, una administradora con correo; en la Mac, alguien con
    clave. Si no, es la primera vez: la pantalla de entrada se lo pide a la dueña."""
    propio = con is None
    if propio: con = sqlite3.connect(DB)
    try:
        sql = ("SELECT 1 FROM usuarios WHERE rol='admin' AND activo=1 AND correo IS NOT NULL" if CF_ACCESS.activo()
               else "SELECT 1 FROM usuarios WHERE clave_hash IS NOT NULL AND activo=1")
        return bool(con.execute(sql).fetchone())
    except sqlite3.OperationalError:
        return False
    finally:
        if propio: con.close()


def rol_de(request: Request):
    u = quien_es(request)
    if not u: return "admin" if not tiene_duena() and not EN_SERVIDOR else "invitado"
    # solo el administrador puede mirar el ERP como si fuera otro, para revisarlo
    if u["rol"] == "admin":
        ver = request.cookies.get("ver_como")
        if ver in PERMISOS: return ver
    return u["rol"]


def uid_de(request):
    """Quién está haciendo esto. Si hay sesión, es esa persona — no el rol genérico.
    Así el historial dice "Isaías" y no "Taller"."""
    u = quien_es(request)
    return u["id"] if u else usuario_id(rol_de(request))


def usuario_id(rol, request=None):
    if request is not None:
        u = quien_es(request)
        if u: return u["id"]
    return {"admin": 1, "logistica": 2, "taller": 4}.get(rol, 1)


def solo_taller(request):
    return rol_de(request) in ("taller", "admin")

def render(request, nombre, **ctx):
    rol = rol_de(request)
    ctx["v_css"] = int((BASE / "static" / "estilo.css").stat().st_mtime)  # evita que el navegador use una copia vieja del estilo
    if "tasa" not in ctx:
        con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; ctx["tasa"] = tasa_hoy(con); con.close()
    ctx.update(request=request, rol=rol, puede=PERMISOS[rol], hoy=datetime.date.today().isoformat(),
               seccion=ctx.get("seccion", ""), usuario=ctx.get("usuario") or quien_es(request),
               viendo_como=request.cookies.get("ver_como") or "")
    if rol in ("admin", "logistica", "taller") and "casos_abiertos" not in ctx:
        try:
            con = sqlite3.connect(DB); ctx["casos_abiertos"] = con.execute("SELECT COUNT(*) FROM danados WHERE estado IN ('pendiente','reparando')").fetchone()[0]; con.close()
        except sqlite3.Error: ctx["casos_abiertos"] = 0
    resp = tpl.TemplateResponse(nombre, ctx, headers={"Cache-Control": "no-store"})   # Safari guardaba paneles viejos
    # al salir de Resultados se cierra la sesión: si vuelve, pide la clave otra vez
    if not request.url.path.startswith(("/finanzas", "/historial")) and request.cookies.get("res_ok"):
        resp.delete_cookie("res_ok")
    return resp


@app.get("/entrar", response_class=HTMLResponse)
def entrar(request: Request, mal: str = "", con=Depends(db)):
    u = quien_es(request)
    if u: return RedirectResponse(PUERTAS.get(u["rol"], (None, "/inicio"))[1], status_code=303)
    # en el servidor no se escribe clave: Cloudflare ya comprobó el correo. Si llega aquí es que ese correo no está
    # en el equipo (o que es la primera vez y la dueña todavía no se registró).
    return render(request, "entrar.html", seccion="entrar", primera_vez=not tiene_duena(con),
                  pide_codigo=EN_SERVIDOR, por_correo=CF_ACCESS.activo(), correo=correo_cf(request), mal=mal, sin_menu=True)


@app.post("/entrar")
def entrar_post(request: Request, usuario: str = Form(""), clave: str = Form(""), con=Depends(db)):
    if CF_ACCESS.activo(): return RedirectResponse("/entrar", status_code=303)   # en el servidor no hay claves del ERP
    ip = ip_de(request)
    quien = usuario.strip().lower()
    if frenado(con, quien, ip): return RedirectResponse("/entrar?mal=frenado", status_code=303)
    u = con.execute("SELECT * FROM usuarios WHERE lower(TRIM(usuario))=? AND activo=1 AND rol!='ninguno'", (quien,)).fetchone()
    if not (u and clave_correcta(clave, u["clave_hash"])):
        anotar_intento(con, quien, ip)
        return RedirectResponse("/entrar?mal=1", status_code=303)
    con.execute("DELETE FROM intentos WHERE usuario=? OR ip=?", (quien, ip))   # entró bien: borrón y cuenta nueva
    casa = PUERTAS.get(u["rol"], (None, "/inicio"))[1]
    r = RedirectResponse(casa, status_code=303)
    r.set_cookie("sesion", abrir_sesion(con, u["id"]), max_age=DURACION_SESION, httponly=True,
                 samesite="strict", secure=cookie_segura(request))
    r.delete_cookie("ver_como"); r.delete_cookie("rol")
    return r


@app.post("/entrar/primera-vez")
def entrar_primera(request: Request, clave: str = Form(""), clave2: str = Form(""), usuario: str = Form(""),
                   codigo: str = Form(""), con=Depends(db)):
    """La primera vez, la dueña se registra. En la Mac pone su propia clave (nadie más la ve, ni queda escrita).
    En el servidor, con el código de instalación, su correo de Cloudflare queda como el de la administradora."""
    if tiene_duena(con): return RedirectResponse("/entrar", status_code=303)
    correo = correo_cf(request)
    if CF_ACCESS.activo() and not correo: return RedirectResponse("/entrar", status_code=303)
    if EN_SERVIDOR:
        esperado = os.environ.get("DECOPET_CODIGO_INICIAL", "")
        ip = ip_de(request)
        if frenado(con, "primera-vez", ip): return RedirectResponse("/entrar?mal=frenado", status_code=303)
        if not (esperado and secrets.compare_digest(codigo.strip().encode(), esperado.encode())):
            anotar_intento(con, "primera-vez", ip)
            return RedirectResponse("/entrar?mal=codigo", status_code=303)
    if not CF_ACCESS.activo() and (len(clave.strip()) < 8 or clave != clave2):
        return RedirectResponse("/entrar?mal=" + ("corta" if len(clave.strip()) < 8 else "distinta"), status_code=303)
    u = con.execute("SELECT * FROM usuarios WHERE rol='admin' AND activo=1 ORDER BY id LIMIT 1").fetchone()
    if not u:   # base recién creada en un servidor: todavía no existe nadie a quien ponerle la clave
        con.execute("INSERT INTO usuarios (nombre, rol, activo, creado_en) VALUES ('Cristina', 'admin', 1, date('now'))")
        u = con.execute("SELECT * FROM usuarios WHERE rol='admin' AND activo=1 ORDER BY id LIMIT 1").fetchone()
    if CF_ACCESS.activo():
        con.execute("UPDATE usuarios SET correo=? WHERE id=?", (correo, u["id"]))
        anotar_acceso(con, u["id"], u["id"], f"Se registró como administradora con {correo}")
        con.commit(); sincronizar_cloudflare(con)
        return RedirectResponse("/inicio", status_code=303)
    con.execute("UPDATE usuarios SET clave_hash=?, usuario=COALESCE(?, usuario) WHERE id=?",   # sin usuario no podría volver a entrar
                (cifrar_clave(clave.strip()), usuario.strip().lower() or None, u["id"])); con.commit()
    r = RedirectResponse("/inicio", status_code=303)
    r.set_cookie("sesion", abrir_sesion(con, u["id"]), max_age=DURACION_SESION, httponly=True,
                 samesite="strict", secure=cookie_segura(request))
    r.delete_cookie("rol")
    return r


@app.get("/salir")
def salir(request: Request, con=Depends(db)):
    f = request.cookies.get("sesion")
    if f: con.execute("DELETE FROM sesiones WHERE ficha=?", (f,)); con.commit()
    # con Cloudflare, salir es cerrar su sesión de Cloudflare: la próxima vez le pide el código otra vez
    r = RedirectResponse("/cdn-cgi/access/logout" if CF_ACCESS.activo() else "/entrar", status_code=303)
    r.delete_cookie("sesion"); r.delete_cookie("ver_como"); r.delete_cookie("rol"); r.delete_cookie("res_ok")
    return r


@app.get("/ver-como/{rol}")
def ver_como(request: Request, rol: str, volver: str = "/ordenes", quien: str = ""):
    """Vista previa: el administrador mira el ERP como lo vería otro. No cambia quién eres.
    Como despachador hay que elegir cuál, porque cada uno ve solo lo suyo."""
    u = quien_es(request)
    if u and u["rol"] != "admin": return RedirectResponse("/inicio", status_code=303)
    if rol == "despachador" and volver in ("/ordenes", "/inicio"): volver = "/mis-entregas"
    if rol == "admin" and volver.startswith("/mis-entregas"): volver = "/inicio"
    r = RedirectResponse(volver, status_code=303)
    if rol == "admin": r.delete_cookie("ver_como"); r.delete_cookie("ver_desp")
    else: r.set_cookie("ver_como", rol if rol in PERMISOS else "admin", samesite="lax")
    if rol == "despachador" and quien in DESPACHADORES: r.set_cookie("ver_desp", quien, samesite="lax")
    return r

@app.get("/")
def raiz(): return RedirectResponse("/inicio", status_code=303)


def por_personalizar(con):
    """Pedidos con algo que hay que personalizar y que nadie ha marcado como listo todavía."""
    out = []
    for l in con.execute("""SELECT l.id, l.orden_id, o.numero, l.nombre, l.personalizacion, p.tipo,
                            COALESCE(NULLIF(c.nombre_pila,''), c.nombre) cliente
                            FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id LEFT JOIN clientes c ON c.id=o.cliente_id
                            LEFT JOIN productos p ON p.id=l.producto_id
                            WHERE TRIM(COALESCE(l.personalizacion,''))!='' AND COALESCE(l.perso_lista,0)=0
                              AND o.estado NOT IN ('cancelada','entregada') AND COALESCE(o.origen_excel,0)=0
                            ORDER BY o.id, l.id"""):
        producto = l["nombre"]
        if l["tipo"] == "opcion":   # la personalización se cobró aparte: va en los productos del pedido que se pueden personalizar
            en = [r[0] for r in con.execute("""SELECT l2.nombre FROM orden_lineas l2 JOIN productos p2 ON p2.id=l2.producto_id
                                               WHERE l2.orden_id=? AND p2.permite_personalizacion=1 ORDER BY l2.id""", (l["orden_id"],))]
            producto = " o ".join(en) if en else ""
        out.append(dict(l) | {"producto": producto})
    return out


@app.post("/ordenes/{oid}/personalizacion/{lid}")
def personalizacion_estado(request: Request, oid: int, lid: int, lista: str = Form("1"), volver: str = Form("/inicio"), con=Depends(db)):
    """Ya se personalizó (o no, me equivoqué). Lo marcan Cristina o logística."""
    if rol_de(request) not in ("admin", "logistica"): return RedirectResponse("/inicio", status_code=303)
    hecho = lista == "1"
    con.execute("UPDATE orden_lineas SET perso_lista=?, perso_lista_en=? WHERE id=? AND orden_id=?",
                (1 if hecho else 0, datetime.datetime.now().strftime("%Y-%m-%d %H:%M") if hecho else None, lid, oid))
    con.commit()
    return RedirectResponse(volver if volver.startswith("/") else "/inicio", status_code=303)


@app.get("/inicio", response_class=HTMLResponse)
def inicio(request: Request, con=Depends(db)):
    rol = rol_de(request); hoy = datetime.date.today(); h = hoy.isoformat(); mes = hoy.strftime("%Y-%m")
    activas = cargar_ordenes(con, {"estado": "activas"}, rol)
    c = {
        # también los de órdenes ya entregadas: el despachador anota un Pago Móvil en la puerta y hay que revisarlo
        "por_revisar": con.execute("SELECT COUNT(*) FROM ordenes WHERE estado_pago='por_confirmar' AND estado!='cancelada'").fetchone()[0],
        "incidencias": con.execute("SELECT COUNT(*) FROM incidencias WHERE estado='abierta'").fetchone()[0],
        "sin_coordinar": sum(1 for o in activas if not o["coordinada"]),
        "hoy": sum(1 for o in activas if (o["fecha_prometida"] or h) <= h),
        "retrasadas": sum(1 for o in activas if any(a[0] == "retrasada" for a in o["alertas"])),
        "por_facturar": con.execute("""SELECT COUNT(*) FROM ordenes WHERE estado!='cancelada'
                                        AND (requiere_factura=1 OR canal='cashea') AND COALESCE(factura_hecha,0)=0""").fetchone()[0] if rol == "admin" else 0,
        # envío nacional que nadie ha llevado a la oficina: se acumulan, no dependen del día prometido
        "por_llevar": con.execute("""SELECT COUNT(*) FROM ordenes WHERE tipo_entrega='nacional' AND viaje_id IS NULL
                                     AND origen_excel=0 AND estado IN ('pendiente','en_ruta') AND """ + HAY_QUE_ENTREGAR("ordenes")).fetchone()[0],
    }
    deudas = con.execute("""SELECT COUNT(*) n, COALESCE(SUM(total - (SELECT COALESCE(SUM(monto_usd),0) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado')),0) s
                            FROM ordenes o WHERE estado!='cancelada' AND estado_pago IN ('abonada','sin_pago','rechazado')""").fetchone()
    c["con_saldo"], c["saldo_total"] = deudas["n"], deudas["s"]
    c["personalizar"] = por_personalizar(con)
    c["incid_lista"] = [dict(r) for r in con.execute("""SELECT i.id, i.orden_id, i.tipo, i.descripcion, i.responsable, o.numero,
                            COALESCE(NULLIF(cl.nombre_pila,''), cl.nombre) cliente
                            FROM incidencias i JOIN ordenes o ON o.id=i.orden_id LEFT JOIN clientes cl ON cl.id=o.cliente_id
                            WHERE i.estado='abierta' ORDER BY i.id DESC LIMIT 10""")]
    c["reclamos_desp"] = [dict(r) for r in con.execute("""SELECT p.*, d.id did FROM pagos_despachador p LEFT JOIN despachadores d ON d.nombre=p.despachador
                              WHERE p.reclamo_en IS NOT NULL AND p.reclamo_resuelto IS NULL ORDER BY p.reclamo_en""")] if rol == "admin" else []
    c["mis_pend"] = [dict(r) for r in con.execute("""SELECT * FROM pendientes WHERE hecho_en IS NULL AND fecha IS NOT NULL AND fecha <= ?
                              ORDER BY fecha, id""", (h,))] if rol == "admin" else []
    c["dil_aprobar"] = [dict(r) for r in con.execute("""SELECT v.despachador, v.monto, v.motivo, v.fecha, d.id did FROM viajes_despachador v
                              LEFT JOIN despachadores d ON d.nombre=v.despachador WHERE v.por_aprobar=1 ORDER BY v.fecha, v.id""")] if rol == "admin" else []
    # retiros de pack y repuestos prepagados PROGRAMADOS para hoy (o atrasados): los que de verdad se entregan
    packs = sum(1 for k in cargar_packs(con) if k["saldo"] > 0 and k["fecha_programada"] and k["fecha_programada"] <= h)
    packs += sum(1 for r in cargar_prepagados(con) if r["fecha_programada"] and r["fecha_programada"] <= h)
    c["packs"] = packs
    # miembros que cruzaron el umbral hoy (36 días exactos sin repuesto)
    toca = con.execute("""SELECT COUNT(*) FROM (SELECT c.id, MAX(CASE WHEN p.categoria='repuesto' OR p.sku LIKE 'PRO-%' THEN substr(o.creado_en,1,10) END) ult
        FROM clientes c JOIN ordenes o ON o.cliente_id=c.id AND o.estado!='cancelada' JOIN orden_lineas l ON l.orden_id=o.id JOIN productos p ON p.id=l.producto_id
        GROUP BY c.id HAVING SUM(CASE WHEN p.sku LIKE 'PRO-%' THEN 1 ELSE 0 END) > 0 AND julianday(?) - julianday(ult) BETWEEN 36 AND 42)""", (h,)).fetchone()[0]
    cumples_todos = cumples_proximos(con, 0)   # Cristina: solo los cumpleaños de hoy
    segs = [s for s in cumples_todos if not s["hecho"]] + [s for s in seguimientos_pendientes(con) if s.get("fase", "hoy") == "hoy"]
    c["toca"] = toca; c["seguimientos"] = len(segs)
    cumples_l = [s for s in segs if s["tipo"] == "cumple"]; rep_l = [s for s in segs if s["tipo"] in ("primer_repuesto", "repuesto", "prepagado", "basico")]; cobro_l = [s for s in segs if s["tipo"] == "cobro"]
    # los contactados HOY se quedan a la vista con el resultado elegido (mañana ya no salen)
    hechos_hoy = [dict(r) for r in con.execute("""SELECT s.*, c.nombre cliente, c.telefono, c.porche_tamano FROM seguimientos s JOIN clientes c ON c.id=s.cliente_id
                                                  WHERE substr(s.hecho_en,1,10)=? AND s.tipo!='cumple' ORDER BY s.hecho_en DESC""", (h,))]
    for r in hechos_hoy:
        r.update(hecho=True, estado=estado_resultado(r), que=(f"Repuesto {r['porche_tamano'] or ''}".strip() if r["tipo"] != "cobro" else (r["nota"] or "Saldo pendiente")),
                 tipo_txt=("Le toca primer repuesto" if r["tipo"] == "primer_repuesto" else ("Cobrar saldo" if r["tipo"] == "cobro" else "Le toca repuesto")))
    cumples_hechos = [s for s in cumples_todos if s["hecho"]]
    rep_h = [r for r in hechos_hoy if r["tipo"] != "cobro"]; cobro_h = [r for r in hechos_hoy if r["tipo"] == "cobro"]
    seg_resumen = {"total": len(segs), "primer": sum(1 for s in segs if s["tipo"] == "primer_repuesto"), "repuesto": len(rep_l), "cumples": len(cumples_l),
                   "lista_cumples": (cumples_l + cumples_hechos)[:3], "lista_rep": [dict(x, mensajes=mensajes_repuesto(con, x)) if x.get("cliente_id") else x for x in (rep_l + rep_h)[:3]], "lista_cobro": (cobro_l + cobro_h)[:3], "lista": segs[:12],
                   "tot_rep": len(rep_l) + len(rep_h), "tot_cumples": len(cumples_l) + len(cumples_hechos), "tot_cobro": len(cobro_l) + len(cobro_h),
                   "hechos_rep": len(rep_h), "hechos_cumples": len(cumples_hechos), "hechos_cobro": len(cobro_h)}
    c["fotos"] = con.execute("SELECT COUNT(*) FROM fotos WHERE permiso='sin_confirmar'").fetchone()[0]
    c["faltan_agencia"] = faltan_datos_agencia(con)
    c["danados"] = danados_pendientes(con)
    c["falta_ubicacion"] = falta_ubicacion(con)
    # clientes nuevos (desde que arrancó el registro) que compraron y no tienen ningún perro anotado (Cristina, 7 oct)
    desde_r = (con.execute("SELECT valor FROM config WHERE clave='registro_desde'").fetchone() or ["2026-10-03"])[0]
    c["sin_mascota"] = [dict(r) for r in con.execute("""SELECT c.id, c.nombre, COALESCE(NULLIF(c.nombre_pila,''), c.nombre) quien, c.telefono FROM clientes c
                        WHERE substr(c.creado_en,1,10) >= ? AND c.sin_mascota_ok IS NULL
                          AND NOT EXISTS (SELECT 1 FROM mascotas m WHERE m.cliente_id=c.id)
                          AND EXISTS (SELECT 1 FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada')
                        ORDER BY c.creado_en DESC LIMIT 20""", (desde_r,))]
    for x in c["sin_mascota"]:   # el mismo mensaje con que Cristina pide los datos del perro
        x["msj"] = f"¡Hola {x['quien']}! 👋🏻 Para nuestra base de datos, ¿crees que nos puedes pasar porfa el nombre, raza y cumpleaños de tu perro? 🐶💚"
        x["wa"] = (wa_api(x["telefono"]) + "&text=" + quote(x["msj"])) if wa_api(x["telefono"]) else ""
    vh = ventas_por_dia(con, h, h).get(h, (0, 0)); v = {"venta": vh[0], "n": vh[1]}
    v["extras"] = sum(1 for x in entradas_ordenes(con, h, h) if x["tipo"] != "pedido")   # cobros sueltos que entraron hoy (delivery de un pack, un saldo…)
    v["por_cobrar"] = por_cobrar_de_hoy(con, h)
    dias_mes = max(1, hoy.day - 1)
    prom = sum(m for d, (m, n) in ventas_por_dia(con, mes + "-01", h).items() if d < h) / dias_mes
    disponible = sum(x["saldo"] for x in saldos(con) if x["activa"]) if rol == "admin" else 0   # todas las cajas, igual que en Cash flow
    # ventas por día, últimos 14 días
    dias14 = [(hoy - datetime.timedelta(days=13 - i)) for i in range(14)]
    por_dia = ventas_por_dia(con, dias14[0].isoformat(), h)
    serie = [(d, por_dia.get(d.isoformat(), (0, 0))[0], por_dia.get(d.isoformat(), (0, 0))[1]) for d in dias14]
    # entregas de hoy por tipo
    tipos = {}
    for o in activas:
        if (o["fecha_prometida"] or h) <= h: tipos[o["tipo_entrega"] or "otro"] = tipos.get(o["tipo_entrega"] or "otro", 0) + 1
    pagos_pend = pagos_pendientes(con, 1) if rol == "admin" else []   # avisa el día antes, no con una semana
    efectivo = efectivo_por_registrar(con) if rol == "admin" else []
    c["efectivo_n"] = len(efectivo)
    c["efectivo_total"] = round(sum(x["monto_usd"] or 0 for x in efectivo), 2)
    # quincena del equipo: el 15 y el último día del mes, corridos al viernes si caen domingo
    if rol == "admin":
        c["es_quincena"], c["quincena_falta"] = quincena_pendiente(con, hoy)
        c["quincena_n"] = len(c["quincena_falta"])

    deuda_desp = con.execute("""SELECT despachador, SUM(COALESCE(delivery, 0)) m, COUNT(*) n FROM ordenes
                                WHERE despachador IS NOT NULL AND despachador!='' AND estado='entregada' AND origen_excel=0 AND despachador_pagado=0 GROUP BY 1 HAVING m>0""").fetchall() if rol == "admin" else []
    viajes_desp = con.execute("SELECT despachador, SUM(monto) m FROM viajes_agencia WHERE pagado=0 AND llevado_en IS NOT NULL GROUP BY 1 HAVING m>0").fetchall() if rol == "admin" else []
    fallidos_desp = con.execute("SELECT despachador, SUM(monto) m FROM viajes_despachador WHERE pagado=0 AND por_aprobar=0 GROUP BY 1 HAVING m>0").fetchall() if rol == "admin" else []
    por_desp = {}
    for r_ in list(deuda_desp) + list(viajes_desp) + list(fallidos_desp): por_desp[r_["despachador"]] = por_desp.get(r_["despachador"], 0) + r_["m"]
    # entregas + viajes a la agencia, menos lo que ya se le adelantó a cada uno
    por_desp = {n: m - adelanto_despachador(con, n)[1] for n, m in por_desp.items()}
    por_desp = {n: m for n, m in por_desp.items() if m > 0.009}
    # a quien no cobra los viernes (Cristina como despachadora) no se le avisa: ella decide cuándo se paga
    sin_viernes = {r[0] for r in con.execute("SELECT nombre FROM despachadores WHERE cobra_viernes=0")}
    por_desp = {n: m for n, m in por_desp.items() if n not in sin_viernes}
    c["desp_debe"] = round(sum(por_desp.values()), 2)
    # pedidos cuyo día de pago llegó (la grama se paga los viernes aunque llegue el lunes)
    # El resto se paga el día de pago si se puso uno; si no, el día de entrega; y si no tiene
    # ninguna de las dos, cuando llega algo. Un aviso por pedido, con lo que falta pagar.
    c["toca_pagar_prov"] = [dict(r) for r in con.execute("""SELECT pr.id, COALESCE(pr.pieza, p.nombre) pieza, pr.responsable,
                            COALESCE(pr.fecha_pago, pr.fecha_esperada) fecha_pago,
                            pr.costo - COALESCE((SELECT SUM(a.monto) FROM abonos_produccion a WHERE a.produccion_id=pr.id),0) debe,
                            COALESCE(pr.tipo_pedido,'produccion') tipo
                            FROM produccion pr LEFT JOIN productos p ON p.id=pr.producto_id
                            WHERE pr.estado!='cancelado' AND pr.costo IS NOT NULL
                              AND pr.costo - COALESCE((SELECT SUM(a.monto) FROM abonos_produccion a WHERE a.produccion_id=pr.id),0) > 0.009
                              AND (COALESCE(pr.fecha_pago, pr.fecha_esperada) <= ?
                                   OR (COALESCE(pr.fecha_pago, pr.fecha_esperada) IS NULL AND (pr.recibido > 0 OR pr.estado!='en_proceso')))
                            ORDER BY 4""", (h,))] if rol == "admin" else []
    c["prov_deben"] = proveedores_que_deben(con) if rol == "admin" else []   # no entregó todo y ya se le había pagado
    # a los despachadores se les paga los VIERNES: el resto de la semana el aviso solo estorba mientras se acumulan entregas
    viejo = con.execute("""SELECT MIN(COALESCE(fecha_entrega, substr(creado_en,1,10))) FROM ordenes
                           WHERE despachador IS NOT NULL AND despachador!='' AND estado='entregada'
                             AND origen_excel=0 AND despachador_pagado=0
                             AND despachador NOT IN (SELECT nombre FROM despachadores WHERE cobra_viernes=0)""").fetchone()[0]
    viernes = hoy - datetime.timedelta(days=(hoy.weekday() - 4) % 7)   # el último viernes (hoy, si es viernes)
    atrasado = bool(viejo and datetime.date.fromisoformat(viejo[:10]) < viernes and hoy.weekday() != 4)
    c["toca_pagar_desp"] = hoy.weekday() == 4 or atrasado          # el viernes, o si ya se pasó el viernes sin pagar
    c["desp_atrasado"] = atrasado
    c["desp_n"] = len(por_desp); c["es_viernes"] = hoy.weekday() == 4
    # porches que el taller ya dejó armados, esperando venta
    armados = con.execute("""SELECT p.nombre, (SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario m WHERE m.producto_id=p.id) listos
                             FROM productos p WHERE p.activo=1 AND p.categoria='porche'
                             AND EXISTS (SELECT 1 FROM receta r WHERE r.producto_id=p.id) ORDER BY p.orden""").fetchall()
    armados = [dict(a) | {"corto": a["nombre"].replace("El Porche Versión PRO ", "")} for a in armados]
    n_armados = sum(a["listos"] for a in armados)
    avisos_taller = con.execute("SELECT * FROM notas_taller WHERE (resuelto=0 OR danado_id IS NOT NULL) AND visto=0 ORDER BY id DESC LIMIT 5").fetchall() if rol == "admin" else []
    c["avisos_pendientes"] = con.execute("SELECT COUNT(*) FROM notas_taller WHERE resuelto=0").fetchone()[0] if rol == "admin" else 0
    cuentas_act = con.execute("SELECT id, codigo, nombre FROM cuentas WHERE activa=1 ORDER BY orden").fetchall() if rol == "admin" else []
    hoy_lista = sorted([o for o in activas if (o["fecha_prometida"] or h) <= h], key=lambda o: (o["coordinada"], o["tipo_entrega"] or ""))[:6]
    # lo que está por agotarse. Lo que se arma en el día (porches, repuestos, packs) no cuenta:
    # nunca tiene stock, así que siempre diría "agotado" sin que signifique nada.
    bajos = []
    for r in con.execute("""SELECT p.nombre, p.minimo, (SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario m WHERE m.producto_id=p.id) stock,
        (SELECT COALESCE(SUM(l.cantidad),0) FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id WHERE l.producto_id=p.id AND o.estado!='cancelada' AND o.creado_en>=date('now','-30 days')) v30
        FROM productos p WHERE p.tipo IN ('producto','insumo') AND p.activo=1
        AND p.categoria NOT IN ('porche','repuesto','opcion','kit')"""):
        d = dict(r); ritmo = d["v30"] / 30.0
        d["dias"] = int(d["stock"] / ritmo) if ritmo > 0 and d["stock"] > 0 else None
        bajo_minimo = d["minimo"] > 0 and d["stock"] <= d["minimo"]
        pronto = d["dias"] is not None and d["dias"] <= 14
        if bajo_minimo or pronto or (d["stock"] <= 0 and d["v30"] > 0): bajos.append(d)
    bajos.sort(key=lambda b: (b["dias"] if b["dias"] is not None else (-1 if b["stock"] <= 0 else 999)))
    llega = [dict(r) for r in con.execute("""SELECT pr.cantidad - pr.recibido faltan, pr.fecha_esperada, COALESCE(pr.pieza, p.nombre) nombre, pr.responsable,
        COALESCE(pr.tipo_pedido,'produccion') tipo FROM produccion pr LEFT JOIN productos p ON p.id=pr.producto_id
        WHERE pr.estado='en_proceso' AND pr.fecha_esperada IS NOT NULL AND pr.fecha_esperada <= ? ORDER BY pr.fecha_esperada""", (h,))]
    proximos = [dict(r) for r in con.execute("""SELECT pr.cantidad - pr.recibido faltan, pr.fecha_esperada, COALESCE(pr.pieza, p.nombre) nombre, pr.responsable, (pr.fecha_esperada < ?) atrasado FROM produccion pr LEFT JOIN productos p ON p.id=pr.producto_id
        WHERE pr.estado='en_proceso' AND pr.fecha_esperada != ? AND pr.fecha_esperada <= ?
        ORDER BY pr.fecha_esperada LIMIT 8""", (h, h, (hoy + datetime.timedelta(days=7)).isoformat()))]   # "esta semana": lo que llega en los próximos 7 días (y lo atrasado)
    # lo que llega hoy y lo que le debes a proveedores suben a la franja de avisos
    c["llegan_hoy"] = sum(l["faltan"] for l in llega); c["llegan_hoy_n"] = len(llega)
    # para Pedidos es solo un recordatorio: no tiene acceso a Taller, así que se le dice qué llega y ya
    c["llegan_hoy_qué"] = " · ".join(f"{int(l['faltan'])}× {l['nombre']}" for l in llega[:3])
    # para Cristina es operativo: de quién llega, y qué trae cada uno. Producción (Walter, David)
    # y pedidos a proveedores van por separado: no es lo mismo que llegue madera que la grama.
    def de_quien(tipo):
        por_quien = {}
        for l in llega:
            if l["tipo"] == tipo: por_quien.setdefault(l["responsable"] or "sin asignar", []).append(f"{int(l['faltan'])}× {l['nombre']}")
        return " · ".join(f"{q}: {', '.join(v[:3])}" + (f" y {len(v) - 3} más" if len(v) > 3 else "")
                          for q, v in list(por_quien.items())[:3])
    c["llegan_hoy_de"] = de_quien("produccion"); c["llegan_prov_de"] = de_quien("proveedor")
    c["atrasados"] = [dict(r) for r in con.execute("""SELECT COALESCE(pr.pieza, p.nombre) nombre, pr.cantidad - pr.recibido faltan, pr.responsable, pr.fecha_esperada
                       FROM produccion pr LEFT JOIN productos p ON p.id=pr.producto_id
                       WHERE pr.estado='en_proceso' AND pr.fecha_esperada IS NOT NULL AND pr.fecha_esperada < ? ORDER BY pr.fecha_esperada""", (h,))]
    d = con.execute("""SELECT COUNT(DISTINCT COALESCE(pr.responsable,'—')) n, COALESCE(SUM(pr.costo - COALESCE((SELECT SUM(a.monto) FROM abonos_produccion a WHERE a.produccion_id=pr.id),0)),0) monto
                       FROM produccion pr WHERE pr.estado!='cancelado' AND pr.costo IS NOT NULL
                       AND pr.costo - COALESCE((SELECT SUM(a.monto) FROM abonos_produccion a WHERE a.produccion_id=pr.id),0) > 0.009""").fetchone()
    c["prov_debe"] = d["monto"]; c["prov_n"] = d["n"]
    return render(request, "inicio.html", seccion="inicio", c=c, v=v, prom=prom, disponible=disponible, fecha_larga=fecha_larga(), serie=serie, tipos=tipos, hoy_lista=hoy_lista, bajos=bajos, llega=llega, proximos=proximos, seg=seg_resumen, pagos_pend=pagos_pend, cuentas_act=cuentas_act, armados=armados, n_armados=n_armados, avisos_taller=avisos_taller, RESULTADOS=RESULTADOS, RPT=RESULTADOS_POR_TIPO, hoy_iso=datetime.date.today().isoformat())


DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
def fecha_larga(d=None):
    d = d or datetime.date.today(); return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month-1]}"

SECCIONES = {"proveedores": "Proveedores",
             "marketing": "Marketing", "analitica": "Analítica", "configuracion": "Configuración"}



# ------------------------------------------------------------------ ÓRDENES
def alertas(o):
    """Alertas automáticas de la Parte 1 (sección 6)."""
    a = []; hoy = datetime.date.today().isoformat(); ahora = datetime.datetime.now()
    def horas(s):
        try: return (ahora - datetime.datetime.fromisoformat(str(s)[:16])).total_seconds() / 3600
        except ValueError: return 0
    activa = o["estado"] == "pendiente"
    if activa and o["fecha_prometida"] and o["fecha_prometida"] < hoy:
        a.append(("retrasada", f"Retrasada · prometida {fmt_fecha(o['fecha_prometida'])}"))
    elif activa and o.get("pagada_en") and horas(o["pagada_en"]) > 72:
        a.append(("retrasada", f"Retrasada · {int(horas(o['pagada_en']) // 24)} días sin entregar"))
    if o["estado_pago"] == "por_confirmar" and horas(o["actualizado_en"]) > 2: a.append(("pago", "Pago por revisar +2 h"))
    if o["estado_pago"] == "rechazado": a.append(("pago", "Pago rechazado · contactar cliente"))
    if o["estado"] == "pendiente" and not coordinada(o) and horas(o["actualizado_en"]) > 4: a.append(("coordinar", "Sin coordinar"))
    if o["estado"] == "pendiente" and coordinada(o) and o["fecha_prometida"] == hoy: a.append(("hoy", "Entrega hoy"))
    return a


def coordinada(o):
    """Coordinada = se sabe con quién/cómo sale. La fecha se asume el mismo día salvo que el cliente pida otra."""
    te = o.get("tipo_entrega")
    if te in ("delivery", "delivery_fuera"): return bool(o.get("despachador"))
    if te == "nacional": return bool(o.get("agencia"))   # la guía se agrega cuando la agencia la da
    if te == "distribuidor": return bool(o.get("distribuidor"))
    return True  # pick-up: no necesita nada más


def completar_direccion(o, principal):
    """La dirección y el GPS guardados del cliente valen para la orden cuando ésta no trae los suyos.
    El GPS solo se hereda si la dirección es la habitual: si la orden va a otro lado, mandaría al despachador al sitio equivocado."""
    if not principal or o.get("tipo_entrega") in ("pickup", "distribuidor"): return o
    norm = lambda x: " ".join((x or "").lower().split())
    propia, hab = norm(o.get("direccion")), norm(principal["direccion"])
    if not propia and principal["direccion"]: o["direccion"] = principal["direccion"]
    for k in ("zona", "ciudad"):
        if not (o.get(k) or "").strip() and principal[k]: o[k] = principal[k]
    misma = (not propia) or propia == hab or propia in hab or hab in propia
    if not (o.get("maps") or "").strip() and principal["maps"] and misma: o["maps"] = principal["maps"]
    return o


# Un pedido tiene algo que entregar AHORA si le queda al menos un producto que no sea un repuesto dejado
# pagado para después, ni un pack del que no se lleva ninguno hoy, ni un cobro agregado después (delivery, propina…).
# Si no, no es una entrega: no sale en Operaciones, ni en el Taller, ni en la lista del despachador.
def HAY_QUE_ENTREGAR(a="o"):
    return f"""EXISTS (SELECT 1 FROM orden_lineas lx JOIN productos px ON px.id=lx.producto_id
                WHERE lx.orden_id={a}.id AND COALESCE(px.tipo,'producto')!='opcion' AND lx.extra_en IS NULL
                  AND NOT EXISTS (SELECT 1 FROM repuestos_prepagados rx WHERE rx.linea_id=lx.id)
                  AND NOT EXISTS (SELECT 1 FROM packs kx WHERE kx.orden_id={a}.id AND kx.producto_id=lx.producto_id AND kx.entregadas_inicio=0))"""


def pasar_entrega_a_prepagado(con, oid, uid):
    """Si el pedido no trae nada para entregar hoy (solo repuestos prepagados, o un pack del que no se lleva ninguno)
    y aun así se llenó cómo se va a entregar, eso no es una entrega de hoy: queda guardado en el repuesto (o el pack)
    para cuando el cliente lo active. Si el delivery ya se pagó en el pedido, queda como pagado y no se vuelve a cobrar.
    El pedido se queda sin despachador, así el delivery no se le paga dos veces."""
    o = con.execute("SELECT * FROM ordenes o WHERE o.id=? AND NOT " + HAY_QUE_ENTREGAR(), (oid,)).fetchone()
    if not o or not (o["tipo_entrega"] or o["despachador"]): return
    pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado IN ('confirmado','por_confirmar')", (oid,)).fetchone()[0]
    deliv = float(o["delivery"] or 0); pago_d = 1 if (deliv > 0 and pagado >= (o["total"] or 0) - 0.01) else 0
    te = o["tipo_entrega"] if o["tipo_entrega"] in ("delivery", "delivery_fuera", "pickup", "nacional") else None
    r = con.execute("SELECT id FROM repuestos_prepagados WHERE orden_id=? AND entregado_en IS NULL ORDER BY id LIMIT 1", (oid,)).fetchone()
    k = None if r else con.execute("SELECT id FROM packs WHERE orden_id=? ORDER BY id LIMIT 1", (oid,)).fetchone()
    if r:
        con.execute("UPDATE repuestos_prepagados SET tipo_entrega=?, despachador=?, agencia=?, delivery=?, delivery_pagado=? WHERE id=?",
                    (te, o["despachador"], o["agencia"], deliv, pago_d, r["id"]))
    elif k:
        con.execute("""UPDATE packs SET tipo_programado=?, despachador_programado=?, delivery_programado=?,
                       deliveries_prepagados=COALESCE(deliveries_prepagados,0)+? WHERE id=?""", (te, o["despachador"], deliv, pago_d, k["id"]))
    else: return
    con.execute("UPDATE ordenes SET despachador=NULL WHERE id=?", (oid,))
    registrar(con, oid, uid, "estado", "Hoy no se entrega nada: la entrega" + (f" ({ENTREGA.get(te, te)}" if te else " (") +
              (f", {o['despachador']}" if o["despachador"] else "") + (f", delivery {fmt_usd(deliv)}{' ya pagado' if pago_d else ''}" if deliv else "") +
              ") queda guardada para cuando el cliente lo active")


def cargar_ordenes(con, filtros, rol):
    sql = """SELECT o.*, c.nombre cliente, c.telefono, u.nombre creada_por_nombre,
             (SELECT d.direccion FROM direcciones d WHERE d.cliente_id=o.cliente_id ORDER BY d.principal DESC, d.id LIMIT 1) cli_direccion,
             (SELECT d.zona      FROM direcciones d WHERE d.cliente_id=o.cliente_id ORDER BY d.principal DESC, d.id LIMIT 1) cli_zona,
             (SELECT d.ciudad    FROM direcciones d WHERE d.cliente_id=o.cliente_id ORDER BY d.principal DESC, d.id LIMIT 1) cli_ciudad,
             (SELECT d.maps      FROM direcciones d WHERE d.cliente_id=o.cliente_id ORDER BY d.principal DESC, d.id LIMIT 1) cli_maps,
             COALESCE((SELECT SUM(cc.monto) FROM credito_cliente cc WHERE cc.cliente_id=o.cliente_id),0) credito,
             (SELECT GROUP_CONCAT(CAST(l.cantidad AS INTEGER) || '× ' || l.nombre || COALESCE(' ' || l.color, '') || CASE WHEN l.malla THEN ' +malla' ELSE '' END || CASE WHEN TRIM(COALESCE(l.personalizacion,''))!='' THEN ' ✎ «' || l.personalizacion || '»' ELSE '' END, ' · ') FROM orden_lineas l WHERE l.orden_id=o.id) productos,
             (SELECT GROUP_CONCAT(COALESCE((SELECT CAST(l.cantidad AS INTEGER) || '× ' || l.nombre || '⟪Lleva ' || k.entregadas_inicio || ' de ' || k.unidades || '⟫' FROM packs k WHERE k.orden_id=o.id AND k.producto_id=l.producto_id AND k.entregadas_inicio>0 AND o.estado!='entregada' LIMIT 1), CAST(l.cantidad AS INTEGER) || '× ' || l.nombre || COALESCE(' ' || l.color, '') || CASE WHEN l.malla THEN ' +malla' ELSE '' END || CASE WHEN TRIM(COALESCE(l.personalizacion,''))!='' THEN ' ✎ «' || l.personalizacion || '»' ELSE '' END), ' · ') FROM orden_lineas l
              WHERE l.orden_id=o.id AND NOT EXISTS (SELECT 1 FROM repuestos_prepagados rp WHERE rp.linea_id=l.id AND rp.entregado_en IS NULL)
                AND NOT EXISTS (SELECT 1 FROM packs k WHERE k.orden_id=o.id AND k.producto_id=l.producto_id AND k.entregadas_inicio=0)) productos_hoy,
             (SELECT GROUP_CONCAT(COALESCE((SELECT CAST(l.cantidad AS INTEGER) || '× ' || l.nombre || '⟪Lleva ' || k.entregadas_inicio || ' de ' || k.unidades || '⟫' FROM packs k WHERE k.orden_id=o.id AND k.producto_id=l.producto_id AND k.entregadas_inicio>0 AND o.estado!='entregada' LIMIT 1), CAST(l.cantidad AS INTEGER) || '× ' || l.nombre || COALESCE(' ' || l.color, '') || CASE WHEN l.malla THEN ' +malla' ELSE '' END || CASE WHEN TRIM(COALESCE(l.personalizacion,''))!='' THEN ' ✎ «' || l.personalizacion || '»' ELSE '' END)
                 || CASE WHEN EXISTS (SELECT 1 FROM repuestos_prepagados rp WHERE rp.linea_id=l.id AND rp.entregado_en IS NULL) THEN '@PEND' ELSE '' END
                 || COALESCE((SELECT '@PACK' || (k.unidades - k.entregadas_inicio - (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=k.id)) || '/' || k.unidades
                              FROM packs k WHERE k.orden_id=o.id AND k.producto_id=l.producto_id
                              AND (o.estado='entregada' OR k.entregadas_inicio=0)   -- antes de entregar no se adelanta cuántos le quedan
                              AND k.unidades - k.entregadas_inicio - (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=k.id) > 0 LIMIT 1), ''), '||') FROM orden_lineas l WHERE l.orden_id=o.id) lineas_txt,
             (SELECT COUNT(*) FROM incidencias i WHERE i.orden_id=o.id AND i.estado='abierta') incidencias,
             (SELECT forma FROM pagos p WHERE p.orden_id=o.id ORDER BY id LIMIT 1) forma_pago,
             (SELECT MIN(COALESCE(confirmado_en, fecha)) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado') pagada_en,
             (SELECT COALESCE(SUM(monto_usd),0) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado') pagado,
             (SELECT motivo_revision FROM pagos p WHERE p.orden_id=o.id AND p.estado='por_confirmar' ORDER BY id DESC LIMIT 1) motivo_revision,
             (SELECT COUNT(*) FROM repuestos_prepagados rp WHERE rp.orden_id=o.id AND rp.entregado_en IS NULL) prepagados_pend
             FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id LEFT JOIN usuarios u ON u.id=o.creada_por WHERE 1=1"""
    args = []
    if filtros.get("estado") == "activas": sql += " AND o.estado NOT IN ('entregada','cancelada') AND " + HAY_QUE_ENTREGAR()
    elif filtros.get("estado") == "por_revisar": sql += " AND o.estado_pago='por_confirmar'"
    elif filtros.get("estado") == "con_saldo": sql += " AND o.estado!='cancelada' AND o.estado_pago IN ('abonada','sin_pago','rechazado')"
    elif filtros.get("estado") == "pack_pend": sql += """ AND EXISTS (SELECT 1 FROM packs k WHERE k.orden_id=o.id
        AND k.unidades - k.entregadas_inicio - (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=k.id) > 0)"""
    elif filtros.get("estado") == "repuesto_pend": sql += " AND EXISTS (SELECT 1 FROM repuestos_prepagados rp WHERE rp.orden_id=o.id AND rp.entregado_en IS NULL)"
    elif filtros.get("estado") == "por_facturar": sql += " AND o.estado!='cancelada' AND (o.requiere_factura=1 OR o.canal='cashea') AND COALESCE(o.factura_hecha,0)=0"
    elif filtros.get("estado") == "con_factura": sql += " AND o.estado!='cancelada' AND (o.requiere_factura=1 OR o.canal='cashea')"
    elif filtros.get("estado") == "facturadas": sql += " AND o.estado!='cancelada' AND (o.requiere_factura=1 OR o.canal='cashea') AND COALESCE(o.factura_hecha,0)=1"
    elif filtros.get("estado") and filtros["estado"] != "todas": sql += " AND o.estado=?"; args.append(filtros["estado"])
    if filtros.get("agencia"): sql += " AND o.agencia=?"; args.append(filtros["agencia"])
    if filtros.get("forma"): sql += " AND EXISTS (SELECT 1 FROM pagos p WHERE p.orden_id=o.id AND p.forma LIKE ?)"; args.append(f"%{filtros['forma']}%")   # forma de pago (también combinadas: 'Pago Móvil + Cashea BNC')
    for k in ("estado_pago", "tipo_entrega", "canal"):
        if k == "estado_pago" and filtros.get(k) == "por_confirmar": sql += " AND o.estado_pago IN ('por_confirmar','rechazado')"
        elif filtros.get(k): sql += f" AND o.{k}=?"; args.append(filtros[k])
    hoy = datetime.date.today().isoformat()
    if filtros.get("vista") == "hoy": sql += " AND o.fecha_prometida=? AND o.estado NOT IN ('entregada','cancelada')"; args.append(hoy)
    if filtros.get("vista") == "retrasadas": sql += " AND o.estado='pendiente'"  # se afina abajo con la regla de alertas
    if filtros.get("vista") == "sin_coordinar": sql += " AND o.estado='pendiente'"
    if filtros.get("vista") == "incidencias": sql += " AND EXISTS (SELECT 1 FROM incidencias i WHERE i.orden_id=o.id AND i.estado='abierta')"
    if filtros.get("vista") == "por_cobrar_clientes": sql += " AND o.estado_pago='abonada' AND o.estado!='cancelada'"
    if filtros.get("vista") == "creadas_hoy": sql += " AND substr(o.creado_en,1,10)=? AND o.estado!='cancelada'"; args.append(hoy)
    if filtros.get("q"): sql += " AND (c.nombre LIKE ? OR o.numero LIKE ? OR c.telefono LIKE ? OR o.guia LIKE ?)"; args += [f"%{filtros['q']}%"] * 4
    sql += " ORDER BY CAST(substr(o.numero,2) AS INTEGER) DESC LIMIT 300"
    rows = [dict(r) for r in con.execute(sql, args)]
    for r in rows:
        completar_direccion(r, {"direccion": r["cli_direccion"], "zona": r["cli_zona"], "ciudad": r["cli_ciudad"], "maps": r["cli_maps"]} if r["cli_direccion"] or r["cli_maps"] else None)
        r["coordinada"] = coordinada(r)
        r["alertas"] = alertas(r)
        r["forma_pago"] = r["forma_pago"] or r["forma_pago_prevista"] or ("BNC" if r["canal"] == "cashea" else None)
    if filtros.get("vista") == "retrasadas": rows = [r for r in rows if any(a[0] == "retrasada" for a in r["alertas"])]
    return rows


VISTAS = {"hoy": "Entregas de hoy", "retrasadas": "Retrasadas", "sin_coordinar": "Pendientes", "incidencias": "Con incidencias abiertas", "creadas_hoy": "Creadas hoy", "por_cobrar_clientes": "Por cobrar a clientes"}

@app.get("/ordenes", response_class=HTMLResponse)
def ordenes(request: Request, estado: str = "todas", estado_pago: str = "", forma: str = "", tipo_entrega: str = "", canal: str = "", agencia: str = "", q: str = "", vista: str = "", abrir: int = 0, nueva: int = 0, con=Depends(db)):
    rol = rol_de(request)
    if vista: estado = "todas"
    filtros = dict(estado=estado, estado_pago=estado_pago, forma=forma, tipo_entrega=tipo_entrega, canal=canal, agencia=agencia, q=q, vista=vista)
    rows = cargar_ordenes(con, filtros, rol)
    conteos = {r["estado"]: r["n"] for r in con.execute("SELECT estado, COUNT(*) n FROM ordenes GROUP BY estado")}
    conteos["todas"] = sum(conteos.values())
    conteos["activas"] = sum(v for k, v in conteos.items() if k not in ("entregada", "cancelada", "todas"))
    conteos["por_revisar"] = con.execute("SELECT COUNT(*) FROM ordenes WHERE estado_pago='por_confirmar'").fetchone()[0]
    conteos["con_saldo"] = con.execute("SELECT COUNT(*) FROM ordenes WHERE estado!='cancelada' AND estado_pago IN ('abonada','sin_pago','rechazado')").fetchone()[0]
    conteos["pack_pend"] = con.execute("""SELECT COUNT(DISTINCT k.orden_id) FROM packs k WHERE k.orden_id IS NOT NULL
        AND k.unidades - k.entregadas_inicio - (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=k.id) > 0""").fetchone()[0]
    conteos["repuesto_pend"] = con.execute("SELECT COUNT(DISTINCT orden_id) FROM repuestos_prepagados WHERE orden_id IS NOT NULL AND entregado_en IS NULL").fetchone()[0]
    conteos["por_facturar"] = con.execute("SELECT COUNT(*) FROM ordenes WHERE estado!='cancelada' AND (requiere_factura=1 OR canal='cashea') AND COALESCE(factura_hecha,0)=0").fetchone()[0]
    retrasadas = sum(1 for r in cargar_ordenes(con, {"estado": "activas"}, rol) if any(a[0] == "retrasada" for a in r["alertas"]))
    return render(request, "ordenes.html", seccion="ordenes", ordenes=rows, f=filtros, conteos=conteos, retrasadas=retrasadas, abrir=abrir, nueva=nueva, vista_nombre=VISTAS.get(vista))


def cargar_orden(con, oid):
    o = con.execute("""SELECT o.*, c.nombre cliente, c.telefono, c.correo, c.ciudad cliente_ciudad, u.nombre creada_por_nombre,
                       (SELECT MIN(COALESCE(confirmado_en, fecha)) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado') pagada_en
                       FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id LEFT JOIN usuarios u ON u.id=o.creada_por WHERE o.id=?""", (oid,)).fetchone()
    if not o: return None
    o = dict(o); o["alertas"] = alertas(o); o["coordinada"] = coordinada(o)
    o["lineas"] = con.execute("""SELECT l.*, k.id pack_id, k.unidades pack_unidades, k.entregadas_inicio pack_hoy FROM orden_lineas l
                                 LEFT JOIN packs k ON k.orden_id=l.orden_id AND k.producto_id=l.producto_id WHERE l.orden_id=?""", (oid,)).fetchall()
    o["pagos"] = con.execute("SELECT p.*, u.nombre confirmado_por_nombre FROM pagos p LEFT JOIN usuarios u ON u.id=p.confirmado_por WHERE orden_id=? ORDER BY id", (oid,)).fetchall()
    o["pagado"] = sum(p["monto_usd"] for p in o["pagos"] if p["estado"] == "confirmado")
    o["historial"] = con.execute("SELECT h.*, u.nombre usuario FROM historial h LEFT JOIN usuarios u ON u.id=h.usuario_id WHERE orden_id=? ORDER BY h.id DESC", (oid,)).fetchall()
    o["incidencias"] = con.execute("SELECT i.*, u.nombre autor FROM incidencias i LEFT JOIN usuarios u ON u.id=i.autor_id WHERE orden_id=? ORDER BY i.id DESC", (oid,)).fetchall()
    o["notas_cliente"] = con.execute("SELECT * FROM notas_cliente WHERE cliente_id=? AND (mostrar_en_orden=1 OR mostrar_logistica=1)", (o["cliente_id"],)).fetchall()
    o["direcciones"] = con.execute("SELECT * FROM direcciones WHERE cliente_id=?", (o["cliente_id"],)).fetchall()
    principal = next((d for d in o["direcciones"] if d["principal"]), o["direcciones"][0] if o["direcciones"] else None)
    completar_direccion(o, principal)
    norm = lambda s: " ".join((s or "").lower().split())
    o["dir_habitual"] = principal["direccion"] if principal else None
    a, b = norm(principal["direccion"]) if principal else "", norm(o["direccion"])
    o["dir_es_habitual"] = bool(a and b and (a == b or a in b or b in a))
    o["personalizaciones"] = [l["personalizacion"] for l in o["lineas"] if l["personalizacion"]]
    o["saldo"] = round((o["total"] or 0) - (o["pagado"] or 0), 2)
    # lo que dejó a favor en esta orden (pagó de más) y lo que usó de su saldo a favor para pagarla
    o["a_favor"] = round(con.execute("SELECT COALESCE(SUM(monto),0) FROM credito_cliente WHERE orden_id=? AND monto>0", (oid,)).fetchone()[0], 2)
    o["mascotas"] =con.execute("SELECT * FROM mascotas WHERE cliente_id=?", (o["cliente_id"],)).fetchall()
    o["ganancia"] = round((o["total"] or 0) - (o["iva"] or 0) - (o["comision"] or 0) - (o["costo_productos"] or 0) - (o["costo_entrega"] or 0), 2)
    o["margen"] = round(o["ganancia"] / o["total"] * 100, 1) if o["total"] else 0
    o["resumen"] = resumen_despacho(o)
    o["resumen_sin_plata"] = resumen_despacho(o, con_plata=False)
    return o


def precio_linea(p, cantidad):
    """Precio base de una línea: aplica el precio por par cuando existe (bowls, platos)."""
    c = int(cantidad)
    if p["precio_par"] and c >= 2:
        return (c // 2) * p["precio_par"] + (c % 2) * p["precio"]
    return p["precio"] * cantidad


def descripcion_linea(l):
    ks = l.keys() if hasattr(l, "keys") else []
    if "pack_unidades" in ks and l["pack_unidades"]:   # de un pack no sale el pack: salen los que se lleva hoy
        hoy_ = l["pack_hoy"] or 0
        return f"{int(l['cantidad'])} × {l['nombre']} · lleva {hoy_} de {l['pack_unidades']}" if hoy_ else f"{l['nombre']} · hoy no se lleva ninguno"
    partes = [f"{int(l['cantidad'])} × {l['nombre']}"]
    if l["color"]: partes.append(f"plato {l['color']}")
    if l["malla"]: partes.append("+ malla")
    if l["personalizacion"]: partes.append(f"personalizado: {l['personalizacion']}")
    return " · ".join(partes)


def con_global_prepagados(oid):
    """Líneas de la orden que son repuestos pagados para después y todavía no se entregaron."""
    c = sqlite3.connect(DB)
    try: return c.execute("SELECT linea_id FROM repuestos_prepagados WHERE orden_id=? AND entregado_en IS NULL AND linea_id IS NOT NULL", (oid,)).fetchall()
    finally: c.close()


def tel_wa(t):
    """Teléfono como WhatsApp lo vuelve tocable: solo y con código de país. '0424-2431884' → '+58 424 2431884'."""
    d = re.sub(r"\D", "", t or "")
    if not d or (t or "").strip().lower() == "pendiente": return None
    if (t or "").strip().startswith("+"): return t.strip()
    if d.startswith("58") and len(d) == 12: d = d[2:]
    if d.startswith("0") and len(d) == 11: d = d[1:]
    return f"+58 {d[:3]} {d[3:]}" if len(d) == 10 else (t or "").strip()


def resumen_despacho(o, con_plata=True):
    L = [f"📦 {o['numero']} — {o['cliente']}"]
    if tel_wa(o["telefono"]): L.append(f"📞 {tel_wa(o['telefono'])}")   # en su propia línea: así WhatsApp deja tocarlo
    prep = {r[0] for r in con_global_prepagados(o["id"])} if o.get("id") else set()
    for l in o["lineas"]:
        ks = l.keys() if hasattr(l, "keys") else []
        if ("id" in ks and l["id"] in prep) or ("extra_en" in ks and l["extra_en"]): continue   # prepagado / cobro agregado: no se lleva hoy
        L.append("• " + descripcion_linea(l))
    ent = ENTREGA.get(o["tipo_entrega"], "")
    if o["fecha_prometida"]: ent += f" · {fmt_fecha(o['fecha_prometida'])}" + (f" {o['franja']}" if o["franja"] else "")
    if o["agencia"]: ent += f" · {o['agencia']}" + (f" guía {o['guia']}" if o["guia"] else "")
    if o["tipo_entrega"] == "nacional" and o["modalidad_envio"]: ent += f" · {MODALIDAD[o['modalidad_envio']].lower()}"
    L.append(f"🚚 {ent}")
    if o["tipo_entrega"] == "distribuidor": L.append(f"🏪 Retira en {o['distribuidor'] or 'distribuidor'} ({o['ciudad'] or ''})")
    elif o["tipo_entrega"] != "pickup" and o["direccion"]:
        L.append(f"📍 {o['zona'] + ', ' if o['zona'] else ''}{o['direccion']}" + (f" — recibe {o['receptor_nombre']}" + (f" {o['receptor_telefono']}" if o["receptor_telefono"] else "") if o["receptor_nombre"] else ""))
        mp = o["maps"]
        if not _sirve(mp) and o.get("cliente_id"):   # la ubicación guardada en la ficha del cliente para esa dirección
            c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
            try: mp = ubicacion_de(c, o["cliente_id"], o["direccion"], mp)
            finally: c.close()
        if mp: L.append(f"🗺 {mp}")
    if not con_plata:   # para quien no ve dinero: qué hacer, sin montos (el despachador ve el suyo en su pantalla)
        if o["estado_pago"] in ("contra_entrega", "abonada", "sin_pago", "rechazado"): L.append("💵 Falta pagar")
        else: L.append("✅ Pagado, no cobrar nada")
    elif o["estado_pago"] in ("contra_entrega", "abonada", "sin_pago", "rechazado") and o["total"] - o["pagado"] > 0.009:
        c = sqlite3.connect(DB)   # esta función no recibe la conexión (igual que con_global_prepagados)
        try: fp = forma_por_cobrar(c, o["id"]) if o.get("id") else None
        finally: c.close()
        L.append(f"💵 Falta pagar {fmt_usd(o['total'] - o['pagado'])}" + (f" · por {fp}" if fp else ""))
    else: L.append("✅ Pagado, no cobrar nada")
    if o["notas_entrega"]: L.append(f"📝 {o['notas_entrega']}")
    for n in o.get("notas_cliente") or []:   # lo que siempre hay que saber de este cliente
        if n["mostrar_logistica"] and n["texto"] != o["notas_entrega"]: L.append(f"📌 {n['texto']}")
    return "\n".join(L)


@app.get("/ordenes/nueva/panel", response_class=HTMLResponse)
def nueva_panel(request: Request, cliente: int = 0, con=Depends(db)):
    productos = con.execute("SELECT * FROM productos WHERE activo=1 AND tipo='producto' ORDER BY orden").fetchall()
    opciones = {r["sku"]: r for r in con.execute("SELECT * FROM productos WHERE tipo='opcion'")}
    clientes = con.execute("""SELECT c.*, (SELECT direccion || COALESCE(' · ' || zona,'') FROM direcciones d WHERE d.cliente_id=c.id AND principal=1) dir,
                              (SELECT GROUP_CONCAT(m.nombre || COALESCE(' (' || m.raza || ')',''), ', ') FROM mascotas m WHERE m.cliente_id=c.id) perros,
                              (SELECT ROUND(COALESCE(SUM(k.monto),0),2) FROM credito_cliente k WHERE k.cliente_id=c.id) credito,
                              (SELECT GROUP_CONCAT(n.texto, ' · ') FROM notas_cliente n WHERE n.cliente_id=c.id AND n.mostrar_logistica=1) notas
                              FROM clientes c ORDER BY nombre""").fetchall()
    pre = con.execute("SELECT nombre FROM clientes WHERE id=?", (cliente,)).fetchone() if cliente else None
    return render(request, "_orden_nueva.html", productos=productos, opciones=opciones, clientes=clientes, tasa=tasa_hoy(con), precliente=pre["nombre"] if pre else "", tarifas=con.execute("SELECT zona, tarifa, fuera_caracas FROM tarifas ORDER BY orden, tarifa, zona").fetchall())


@app.get("/ordenes/{oid}/panel", response_class=HTMLResponse)
def orden_panel(request: Request, oid: int, con=Depends(db)):
    o = cargar_orden(con, oid)
    if not o: return HTMLResponse("<p>No existe.</p>")
    vendibles = con.execute("""SELECT id, nombre, precio, requiere_color FROM productos WHERE activo=1 AND tipo NOT IN ('opcion','insumo') AND COALESCE(sku,'') NOT LIKE 'PACK%'
                               ORDER BY categoria, nombre""").fetchall()
    return render(request, "_orden_panel.html", o=o, vendibles=vendibles)


def registrar(con, oid, uid, accion, detalle=None, motivo=None):
    con.execute("INSERT INTO historial (orden_id,usuario_id,accion,detalle,motivo) VALUES (?,?,?,?,?)", (oid, uid, accion, detalle, motivo))
    con.execute("UPDATE ordenes SET actualizado_en=datetime('now','localtime') WHERE id=?", (oid,))


def volver_seguro(v):
    """Solo direcciones de este mismo ERP: un enlace armado con ?volver=https://otro-sitio no puede mandarte afuera."""
    return v if v and v.startswith("/") and not v.startswith("//") and "\\" not in v else None


def volver(oid, request):
    v = volver_seguro(request.query_params.get("volver"))
    if v: return RedirectResponse(v, status_code=303)
    return RedirectResponse(f"/ordenes?estado={request.query_params.get('estado','todas')}&abrir={oid}", status_code=303)


def volver_tras_crear(oid, request):
    """Crear una orden desde Operaciones te deja en Operaciones, con la orden recién creada abierta; desde otro lado, en Órdenes.
    Solo Operaciones: en otras pantallas "abrir" quiere decir otra cosa (en Clientes abre un cliente)."""
    v = volver_seguro(request.query_params.get("volver"))
    if v and v.startswith("/operaciones"): return f"{v}{'&' if '?' in v else '?'}abrir={oid}"
    return f"/ordenes?abrir={oid}"


@app.post("/ordenes/{oid}/estado")
def cambiar_estado(request: Request, oid: int, estado: str = Form(...), motivo: str = Form(""), monto_recibido: str = Form(""), moneda_recibida: str = Form("USD"), fecha: str = Form(""), forma_recibida: str = Form(""), forma_otro: str = Form(""), con=Depends(db)):
    rol = rol_de(request); uid = uid_de(request)
    if PERMISO_ESTADO.get(estado) not in PERMISOS[rol]: return volver(oid, request)
    o = cargar_orden(con, oid)
    if not o: return RedirectResponse("/ordenes", status_code=303)
    yo = quien_es(request)
    if yo and yo["rol"] == "despachador" and o["despachador"] != yo["despachador"]:
        return RedirectResponse("/mis-entregas", status_code=303)   # solo sus propias entregas
    if estado in ("entregada", "en_ruta") and not coordinada(o):
        registrar(con, oid, uid, "bloqueado", "Falta coordinar (despachador / agencia / distribuidor) antes de marcar entregado"); con.commit(); return volver(oid, request)
    sets = ["estado=?"]; args = [estado]
    if estado == "entregada":
        fe = fecha.strip() or datetime.date.today().isoformat()   # se puede registrar una entrega de otro día
        sets.append("fecha_entrega=?"); args.append(fe if fe != datetime.date.today().isoformat() else datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
        # lo que el despachador cobró en la puerta: contra entrega, o el resto de una orden que quedó con saldo
        cobro_puerta = o["estado_pago"] == "contra_entrega" or (
            o["estado_pago"] in ("abonada", "sin_pago", "rechazado") and (str(monto_recibido).strip() != "" or forma_recibida))
        forma_recibida = forma_del_despachador(forma_recibida, forma_otro)
        despues = forma_recibida == "despues"   # cliente de la casa: se le entrega y Cristina le cobra después
        digital = forma_recibida and not despues and not forma_recibida.startswith("Efectivo")
        if cobro_puerta and (despues or digital):
            falta = round(o["total"] - o["pagado"], 2)
            monto = 0.0 if despues else max(0.0, round(float(cifra(monto_recibido) or 0) if str(monto_recibido).strip() else falta, 2))
            if monto:   # pagó por Zelle, Pago Móvil…: lo dice el despachador, Cristina confirma que llegó
                tasa = tasa_hoy(con)["valor"] or 0; en_bs = es_bolivares(forma_recibida) and tasa
                con.execute("""INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,tasa,cuenta,fecha,estado)
                               VALUES (?,?,?,?,?,?,?,?,'por_confirmar')""",
                            (oid, forma_recibida, monto, round(monto * tasa, 2) if en_bs else monto, "VES" if en_bs else "USD",
                             tasa if en_bs else None, caja_de(forma_recibida), fe))
                sets.append("estado_pago=?"); args.append("por_confirmar")
                registrar(con, oid, uid, "pago", f"Al entregar pagó {fmt_usd(monto)} por {forma_recibida}: por revisar"
                          + (f" · quedan {fmt_usd(falta - monto)}" if falta - monto > 0.009 else ""))
            else:
                sets.append("estado_pago=?"); args.append(estado_pago_de(o["pagado"], o["total"]))
                registrar(con, oid, uid, "pago", f"Entregado sin cobrar: quedan {fmt_usd(falta)} · lo cobra Cristina después")
        elif cobro_puerta:
            falta = round(o["total"] - o["pagado"], 2)
            monto = float(cifra(monto_recibido)) if str(monto_recibido).strip() else float(o["monto_contra_entrega"] or falta)
            monto = max(0.0, round(monto, 2))
            # Todo se paga por adelantado menos el efectivo: lo que se cobra en la puerta
            # es efectivo, y el despachador no elige caja ni forma.
            if yo and yo["rol"] == "despachador": moneda_recibida = "USD"
            if monto:
                con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,cuenta,fecha,estado,confirmado_por,confirmado_en) VALUES (?,?,?,?,?,?,?,?)",
                            (oid, {"USD": "Efectivo USD", "Bs": "Efectivo Bs", "EUR": "Efectivo EUR"}.get(moneda_recibida, "Efectivo USD"), monto,
                             caja_efectivo(con), fe, "confirmado", uid, datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
            # la orden queda como lo que de verdad cobró: si trajo menos, queda con saldo, no "pagada"
            nuevo_estado = estado_pago_de(o["pagado"] + monto, o["total"])
            sets.append("estado_pago=?"); args.append(nuevo_estado)
            sobra = sobrante_a_favor(con, oid, uid, fe)   # pagó de más porque no había vuelto
            if sobra: registrar(con, oid, uid, "pago", f"Pagó {fmt_usd(sobra)} de más: le quedan a favor")
            if monto:
                falto = round(o["total"] - o["pagado"] - monto, 2)
                registrar(con, oid, uid, "pago", f"Cobrado al entregar {fmt_usd(monto)} en efectivo el {fe} → {caja_efectivo(con)}"
                          + (f" · quedan {fmt_usd(falto)} por cobrar" if falto > 0.009 else ""))
            elif falta > 0.009:
                registrar(con, oid, uid, "pago", f"Entregado sin cobrar: quedan {fmt_usd(falta)} por cobrar")
            if monto and not o["fecha_pago"] and nuevo_estado == "pagada":
                sets.append("fecha_pago=?"); args.append(fe)   # el día que se entrega es el día que pagaron
    if estado == "pendiente":
        con.execute("UPDATE pagos SET estado='confirmado', confirmado_por=?, confirmado_en=datetime('now','localtime') WHERE orden_id=? AND estado='por_confirmar'", (uid, oid))
        pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (oid,)).fetchone()[0]
        sets.append("estado_pago=?"); args.append(estado_pago_de(pagado, o["total"]))
        if sobrante_a_favor(con, oid, uid): registrar(con, oid, uid, "pago", "Pagó de más: el sobrante le queda a favor")
    if estado == "cancelada":
        if not motivo: return volver(oid, request)
        if o["pagado"] > 0: sets.append("estado_pago='reembolsada'")
    args.append(oid)
    con.execute(f"UPDATE ordenes SET {', '.join(sets)} WHERE id=?", args)
    registrar(con, oid, uid, "estado", f"{E_LABEL[o['estado']]} → {E_LABEL[estado]}", motivo or None)
    con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/pago/confirmar")
def confirmar_pago(request: Request, oid: int, caja: str = Form(""), con=Depends(db)):
    rol = rol_de(request); uid = uid_de(request)
    if "confirmar_pago" not in PERMISOS[rol]: return volver(oid, request)
    # el despachador dijo "Zelle", "Pago Móvil"…: aquí se anota en qué caja entró de verdad
    if caja in MONEDA_CAJA:
        tasa = tasa_hoy(con)["valor"] or 0; en_bs = MONEDA_CAJA.get(caja) == "VES" and tasa
        reportados = con.execute("SELECT id, forma, monto_usd FROM pagos WHERE orden_id=? AND estado='por_confirmar' AND cuenta IS NULL", (oid,)).fetchall()
        con.execute("""UPDATE pagos SET cuenta=?, moneda=?, tasa=?, monto_real=ROUND(monto_usd * ?, 2)
                       WHERE orden_id=? AND estado='por_confirmar' AND cuenta IS NULL""",
                    (caja, "VES" if en_bs else "USD", tasa if en_bs else None, tasa if en_bs else 1, oid))
        # Confirmar que llegó = la venta entra a esa caja en Cash flow, sin tener que anotarla otra vez.
        # (Si las ventas entran solas al libro, no se agrega nada: ya aparecen.)
        cid = con.execute("SELECT id FROM cuentas WHERE nombre=?", (caja,)).fetchone()
        if cid and reportados and not ventas_automaticas(con):
            od = con.execute("SELECT o.numero, COALESCE(NULLIF(c.nombre_pila,''), c.nombre) cliente FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id WHERE o.id=?", (oid,)).fetchone()
            for p in reportados:
                forma = (p["forma"] or "").replace(" (según despachador)", "")
                con.execute("""INSERT INTO movimientos (fecha, tipo, cuenta_destino_id, monto_usd, monto_real, moneda, concepto, categoria, notas, usuario_id)
                               VALUES (?,'entrada',?,?,?,'USD',?,'Ventas',?,?)""",
                            (datetime.date.today().isoformat(), cid[0], p["monto_usd"], p["monto_usd"],
                             f"Ventas · {od['numero']} · {od['cliente'] or ''} · {forma}", "Cobrado por el despachador, confirmado al llegar", uid))
                con.execute("UPDATE pagos SET en_cashflow=1 WHERE id=?", (p["id"],))
    con.execute("UPDATE pagos SET estado='confirmado', confirmado_por=?, confirmado_en=datetime('now','localtime') WHERE orden_id=? AND estado='por_confirmar'", (uid, oid))
    o = con.execute("SELECT total FROM ordenes WHERE id=?", (oid,)).fetchone()
    pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (oid,)).fetchone()[0]
    con.execute("UPDATE ordenes SET estado_pago=? WHERE id=?", (estado_pago_de(pagado, o["total"]), oid)); fijar_fecha_pago(con, oid)
    sobra = sobrante_a_favor(con, oid, uid)
    registrar(con, oid, uid, "pago", f"Pago confirmado por Cristina ({fmt_usd(pagado)})" + (f" · {fmt_usd(sobra)} le quedan a favor" if sobra else ""))
    con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/pago/rechazar")
def rechazar_pago(request: Request, oid: int, motivo: str = Form(""), con=Depends(db)):
    rol = rol_de(request); uid = uid_de(request)
    if "rechazar_pago" not in PERMISOS[rol]: return volver(oid, request)
    con.execute("UPDATE pagos SET estado='rechazado' WHERE orden_id=? AND estado='por_confirmar'", (oid,))
    con.execute("UPDATE ordenes SET estado_pago='rechazado' WHERE id=?", (oid,))
    registrar(con, oid, uid, "pago", "Pago rechazado", motivo or None); con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/contra-entrega")
def contra_entrega(request: Request, oid: int, con=Depends(db)):
    rol = rol_de(request)
    if "contra_entrega" not in PERMISOS[rol]: return volver(oid, request)
    con.execute("UPDATE ordenes SET estado_pago='contra_entrega' WHERE id=? AND estado_pago IN ('sin_pago','rechazado','por_confirmar')", (oid,))
    registrar(con, oid, uid_de(request), "estado", "Autorizado que pague al recibir → Confirmada"); con.commit(); return volver(oid, request)


@app.post("/clientes/{cid}/sin-mascota")
def cliente_sin_mascota(request: Request, cid: int, con=Depends(db)):
    """'El cliente no quiso' dar los datos de su perro: deja de salir el aviso en Inicio."""
    if rol_de(request) not in ("admin", "logistica"): return RedirectResponse("/inicio", status_code=303)
    con.execute("UPDATE clientes SET sin_mascota_ok=date('now','localtime') WHERE id=?", (cid,)); con.commit()
    return RedirectResponse("/inicio", status_code=303)


@app.post("/ordenes/{oid}/editar")
async def editar_orden(request: Request, oid: int, con=Depends(db)):
    """Editar lo que se vendió (Cristina, 7 oct: 'editar una orden debería ser muchísimo más fácil'): cantidad y precio de
    cada producto, quitar o agregar productos, descuento y delivery. Recalcula IVA (Cashea / factura), total y si está
    pagada, y rehace las salidas de inventario de la orden. Los packs, los repuestos prepagados y los cobros agregados
    después no se tocan aquí (tienen su propio manejo)."""
    if "confirmar_pago" not in PERMISOS[rol_de(request)]: return volver(oid, request)
    f = await request.form(); uid = uid_de(request)
    o = con.execute("SELECT * FROM ordenes WHERE id=?", (oid,)).fetchone()
    if not o or o["estado"] == "cancelada": return volver(oid, request)
    dia_o = (o["creado_en"] or "")[:10]
    lineas = con.execute("""SELECT l.*, p.sku, p.costo costo_p,
                            EXISTS (SELECT 1 FROM repuestos_prepagados r WHERE r.linea_id=l.id) prepagada
                            FROM orden_lineas l LEFT JOIN productos p ON p.id=l.producto_id WHERE l.orden_id=? ORDER BY l.id""", (oid,)).fetchall()
    fija = lambda l: bool(l["extra_en"]) or (l["sku"] or "").startswith("PACK") or l["prepagada"] or not l["producto_id"]
    cambios = []
    for l in lineas:
        if fija(l): continue
        if f.get(f"quitar_{l['id']}"):
            con.execute("DELETE FROM orden_lineas WHERE id=?", (l["id"],)); cambios.append(f"quitó {l['cantidad']:g}× {l['nombre']}"); continue
        c = cifra(f.get(f"cant_{l['id']}")) if (f.get(f"cant_{l['id']}") or "").strip() else l["cantidad"]
        pu = cifra(f.get(f"precio_{l['id']}")) if (f.get(f"precio_{l['id']}") or "").strip() else l["precio"]
        if c <= 0:
            con.execute("DELETE FROM orden_lineas WHERE id=?", (l["id"],)); cambios.append(f"quitó {l['cantidad']:g}× {l['nombre']}"); continue
        if abs(c - (l["cantidad"] or 0)) > 1e-9 or abs(pu - (l["precio"] or 0)) > 1e-9:
            con.execute("UPDATE orden_lineas SET cantidad=?, precio=?, total=? WHERE id=?", (c, pu, round(pu * c + (l["extras"] or 0), 2), l["id"]))
            cambios.append(f"{l['nombre']}: {l['cantidad']:g}× {fmt_usd(l['precio'] or 0)} → {c:g}× {fmt_usd(pu)}")
        col = f.get(f"color_{l['id']}")
        if col in PLATO_DE_COLOR and col != (l["color"] or ""):
            con.execute("UPDATE orden_lineas SET color=? WHERE id=?", (col, l["id"])); cambios.append(f"{l['nombre']}: plato {l['color'] or 'sin color'} → {col}")
    for pid_n, cant_n in zip(f.getlist("nuevo_producto"), f.getlist("nuevo_cant")):
        if not pid_n: continue
        p = con.execute("SELECT * FROM productos WHERE id=? AND tipo NOT IN ('opcion','insumo')", (pid_n,)).fetchone()
        if not p or (p["sku"] or "").startswith("PACK"): continue
        col = f.get("nuevo_color") if p["requiere_color"] else None
        if p["requiere_color"] and col not in PLATO_DE_COLOR:
            con.rollback(); return _falta("Falta elegir el color del plato (azul o rosado) del Slow Chow que agregaste.")
        c = cifra(cant_n) or 1
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total,color) VALUES (?,?,?,?,?,?,?,?)",
                    (oid, p["id"], p["nombre"], c, p["precio"], p["costo"], round(precio_linea(p, c), 2), col))
        cambios.append(f"agregó {c:g}× {p['nombre']}" + (f" (plato {col})" if col else ""))
    lineas = con.execute("SELECT l.*, p.costo costo_p FROM orden_lineas l LEFT JOIN productos p ON p.id=l.producto_id WHERE l.orden_id=?", (oid,)).fetchall()
    normales = [l for l in lineas if not l["extra_en"]]
    if not normales: con.rollback(); return volver(oid, request)   # una orden sin productos: para eso está Cancelar
    extras = [l for l in lineas if l["extra_en"]]
    del_extra = sum(l["total"] or 0 for l in extras if (l["nombre"] or "").strip().lower() == "delivery")
    descuento = cifra(f.get("descuento")) if (f.get("descuento") or "").strip() else (o["descuento"] or 0)
    delivery_b = cifra(f.get("delivery")) if (f.get("delivery") or "").strip() else round((o["delivery"] or 0) - del_extra, 2)
    sub_n = round(sum(l["total"] or 0 for l in normales), 2)
    con_iva = (o["iva"] or 0) > 0 or o["canal"] == "cashea" or o["requiere_factura"]
    iva = round((sub_n - descuento) * 0.16, 2) if con_iva else 0
    subtotal = round(sub_n + sum(l["total"] or 0 for l in extras), 2)
    total = round(subtotal - descuento + iva + delivery_b, 2)
    for n, a, b in (("descuento", o["descuento"] or 0, descuento), ("delivery", round((o["delivery"] or 0) - del_extra, 2), delivery_b), ("total", o["total"] or 0, total)):
        if abs((a or 0) - (b or 0)) > 0.009: cambios.append(f"{n}: {fmt_usd(a)} → {fmt_usd(b)}")
    con.execute("""UPDATE ordenes SET subtotal=?, descuento=?, iva=?, delivery=?, total=?, costo_productos=?, comision=? WHERE id=?""",
                (subtotal, descuento, iva, round(delivery_b + del_extra, 2), total,
                 round(sum((l["costo_p"] if l["costo_p"] is not None else (l["costo"] or 0)) * (l["cantidad"] or 0) for l in normales), 2),
                 round(total * 0.06, 2) if o["canal"] == "cashea" else (o["comision"] or 0), oid))
    # el inventario: se rehacen las salidas de esta orden con lo que quedó
    con.execute("DELETE FROM mov_inventario WHERE orden_id=? AND tipo='salida'", (oid,))
    descontar_inventario(con, oid, uid)
    pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (oid,)).fetchone()[0]
    if o["estado_pago"] not in ("por_cobrar", "reembolsada", "por_confirmar", "contra_entrega") or pagado >= total - 0.01:
        con.execute("UPDATE ordenes SET estado_pago=? WHERE id=? AND estado_pago NOT IN ('por_cobrar','reembolsada')", (estado_pago_de(pagado, total), oid))
    fijar_pago_despachador(con, oid)
    if pagado > total + 0.009: cambios.append(f"ojo: ya había pagado {fmt_usd(pagado)}, {fmt_usd(pagado - total)} de más (devolver o dejar a favor)")
    if cambios: registrar(con, oid, uid, "editada", "Orden editada · " + "; ".join(cambios))
    con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/pago/{pid}/corregir")
async def corregir_pago(request: Request, oid: int, pid: int, con=Depends(db)):
    """Corregir un pago mal anotado: monto, forma, fecha o referencia; o borrarlo si fue un error (Cristina, 7 oct:
    'necesito corregir el monto pagado y no sé cómo'). La orden recalcula si está pagada, abonada o por pagar."""
    if "confirmar_pago" not in PERMISOS[rol_de(request)]: return volver(oid, request)
    f = await request.form()
    p = con.execute("SELECT * FROM pagos WHERE id=? AND orden_id=?", (pid, oid)).fetchone()
    if not p: return volver(oid, request)
    if f.get("borrar"):
        con.execute("DELETE FROM pagos WHERE id=?", (pid,))
        registrar(con, oid, uid_de(request), "pago", f"Se borró el pago {p['forma'] or ''} {fmt_usd(p['monto_usd'] or 0)} (estaba mal anotado)")
    else:
        monto = cifra(f.get("monto_usd")) if (f.get("monto_usd") or "").strip() else p["monto_usd"]
        forma = (f.get("forma") or p["forma"] or "").strip() or None
        fecha = (f.get("fecha") or "").strip() or (p["fecha"] or "")[:10]
        ref = (f.get("referencia") or "").strip() or None
        en_bs = es_bolivares(forma)
        tasa = p["tasa"] or (tasa_hoy(con)["valor"] if en_bs else None)
        real = round(monto * tasa, 2) if en_bs and tasa else monto
        hora = (p["fecha"] or "")[10:] if (p["fecha"] or "")[:10] == fecha else ""
        con.execute("""UPDATE pagos SET monto_usd=?, monto_real=?, moneda=?, tasa=?, forma=?, cuenta=?, fecha=?, referencia=? WHERE id=?""",
                    (monto, real, "VES" if en_bs else "USD", tasa if en_bs else None, forma, caja_de(forma) or p["cuenta"], fecha + hora, ref, pid))
        cambios = [f"{n}: {a} → {b}" for n, a, b in (("monto", fmt_usd(p["monto_usd"] or 0), fmt_usd(monto)), ("forma", p["forma"], forma),
                                                     ("fecha", (p["fecha"] or "")[:10], fecha), ("referencia", p["referencia"] or "—", ref or "—")) if str(a) != str(b)]
        if cambios: registrar(con, oid, uid_de(request), "pago", "Pago corregido · " + "; ".join(cambios))
    o = con.execute("SELECT total, estado_pago FROM ordenes WHERE id=?", (oid,)).fetchone()
    pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (oid,)).fetchone()[0]
    if o["estado_pago"] not in ("por_cobrar", "reembolsada", "por_confirmar", "contra_entrega") or pagado >= (o["total"] or 0) - 0.01:
        con.execute("UPDATE ordenes SET estado_pago=? WHERE id=? AND estado_pago NOT IN ('por_cobrar','reembolsada')", (estado_pago_de(pagado, o["total"] or 0), oid))
    con.execute("UPDATE ordenes SET fecha_pago=NULL WHERE id=?", (oid,)); fijar_fecha_pago(con, oid)
    con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/pago")
async def registrar_pago(request: Request, oid: int, con=Depends(db)):
    rol = rol_de(request); f = await request.form()
    if "pago_por_confirmar" not in PERMISOS[rol]: return volver(oid, request)
    monto = float(f.get("monto_usd") or 0); forma = f.get("forma")
    en_bs = es_bolivares(forma)
    tasa = tasa_hoy(con)["valor"]
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,tasa,cuenta,referencia,fecha,estado) VALUES (?,?,?,?,?,?,?,?,?,'por_confirmar')",
                (oid, forma, monto, float(f.get("monto_real") or (monto * tasa if en_bs else monto)), "VES" if en_bs else "USD", tasa if en_bs else None, f.get("cuenta") or FORMA_CUENTA.get(forma), f.get("referencia"), datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    con.execute("UPDATE ordenes SET estado_pago='por_confirmar' WHERE id=? AND estado_pago IN ('sin_pago','rechazado')", (oid,))
    registrar(con, oid, uid_de(request), "pago", f"Pago reportado: {forma} {fmt_usd(monto)} ref {f.get('referencia') or '—'}"); con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/factura")
def factura_toggle(request: Request, oid: int, hecha: str = Form("0"), requiere: str = Form(""), numero: str = Form(""), fecha: str = Form(""), con=Depends(db)):
    """Marcar la factura hecha guarda su número y su fecha: así queda constancia de que se hizo, no solo un visto."""
    rol = rol_de(request)
    if "ver_dinero" not in PERMISOS[rol]: return volver(oid, request)
    uid = uid_de(request)
    if requiere: con.execute("UPDATE ordenes SET requiere_factura=? WHERE id=?", (1 if requiere == "1" else 0, oid))
    elif hecha == "1":
        num = (numero or "").strip() or None
        con.execute("UPDATE ordenes SET factura_hecha=1, factura_numero=?, factura_fecha=?, factura_por=? WHERE id=?",
                    (num, (fecha or datetime.date.today().isoformat()), uid, oid))
        registrar(con, oid, uid, "factura", f"Factura fiscal hecha{' · N° ' + num if num else ''}")
    else:
        con.execute("UPDATE ordenes SET factura_hecha=0, factura_numero=NULL, factura_fecha=NULL, factura_por=NULL WHERE id=?", (oid,))
        registrar(con, oid, uid, "factura", "Factura fiscal pendiente otra vez")
    con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/cobro-extra")
def orden_cobro_extra(request: Request, oid: int, concepto: str = Form(""), concepto_otro: str = Form(""), monto: str = Form("0"),
                      forma: str = Form(""), fecha: str = Form(""), referencia: str = Form(""), no_pagado: str = Form(""), con=Depends(db)):
    """El cliente agrega algo a una orden que ya existe y lo paga: sube el total de esa orden y queda el pago."""
    rol = rol_de(request)
    if "confirmar_pago" not in PERMISOS[rol]: return volver(oid, request)
    c = (concepto_otro.strip() if concepto == "Otro" else concepto.strip()) or "Cobro adicional"
    m = cifra(monto)
    if m > 0 and forma.strip():
        # "todavía no lo pagó": queda como saldo de la orden con la forma en que lo va a pagar (se cobra al entregar o sale en Seguimientos)
        if no_pagado: fecha, referencia = "", ""   # se anota hoy; el día y la referencia se ponen cuando pague
        cobro_extra(con, oid, c, m, forma.strip(), (fecha or "").strip() or None, uid_de(request), referencia,
                    pago=None if no_pagado else "confirmado")
        con.commit()
    return volver(oid, request)


@app.post("/ordenes/{oid}/linea/{lid}/quitar")
def quitar_cobro_extra(request: Request, oid: int, lid: int, con=Depends(db)):
    """Quitar un cobro que se agregó a la orden y todavía no se pagó (Cristina, 7 oct: Marisol iba por delivery y al final
    pasa a buscar la rampa; el delivery de $12 ya no va). Baja el total; si era el delivery, también lo que se le paga al
    despachador. Lo que ya se pagó no se quita aquí."""
    if "confirmar_pago" not in PERMISOS[rol_de(request)]: return volver(oid, request)
    l = con.execute("SELECT * FROM orden_lineas WHERE id=? AND orden_id=? AND extra_en IS NOT NULL", (lid, oid)).fetchone()
    o = con.execute("SELECT total FROM ordenes WHERE id=?", (oid,)).fetchone()
    if not l or not o: return volver(oid, request)
    pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (oid,)).fetchone()[0]
    if (o["total"] or 0) - pagado < (l["total"] or 0) - 0.01: return volver(oid, request)   # ya se pagó: no se quita
    m = l["total"] or 0
    con.execute("DELETE FROM orden_lineas WHERE id=?", (lid,))
    con.execute("UPDATE ordenes SET subtotal=MAX(COALESCE(subtotal,0)-?,0), total=MAX(COALESCE(total,0)-?,0) WHERE id=?", (m, m, oid))
    if (l["nombre"] or "").strip().lower() == "delivery":
        con.execute("UPDATE ordenes SET delivery=MAX(COALESCE(delivery,0)-?,0) WHERE id=?", (m, oid))
    t = con.execute("SELECT total FROM ordenes WHERE id=?", (oid,)).fetchone()[0]
    con.execute("UPDATE ordenes SET estado_pago=? WHERE id=? AND estado_pago NOT IN ('por_cobrar','reembolsada')", (estado_pago_de(pagado, t), oid))
    fijar_pago_despachador(con, oid)
    registrar(con, oid, uid_de(request), "pago", f"Se quitó el cobro «{l['nombre']}» de {fmt_usd(m)} (no se había pagado)")
    con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/cobrar")
async def cobrar_saldo(request: Request, oid: int, con=Depends(db)):
    """Cristina o logística registran un cobro y queda confirmado de una. ('Por revisar' es solo para lo que reporta un despachador.)"""
    rol = rol_de(request); f = await request.form(); uid = uid_de(request)
    if "confirmar_pago" not in PERMISOS[rol] and "ver_cobros" not in PERMISOS[rol]: return volver(oid, request)
    o = con.execute("SELECT total, cliente_id FROM ordenes WHERE id=?", (oid,)).fetchone()
    monto = float(cifra(f.get("monto_usd")) or 0); forma = f.get("forma") or "Efectivo USD"
    if monto <= 0: return volver(oid, request)
    # Pagar con lo que ya tenía a favor: no entra plata nueva, se gasta la que ya había entrado
    if forma == SALDO_FAVOR:
        disponible = credito_de(con, o["cliente_id"])
        monto = round(min(monto, disponible), 2)
        if monto <= 0: return volver(oid, request)
        con.execute("""INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado,confirmado_por,confirmado_en)
                       VALUES (?,?,?,?,'USD',?,'confirmado',?,datetime('now','localtime'))""",
                    (oid, SALDO_FAVOR, monto, monto, datetime.date.today().isoformat(), uid))
        mover_credito(con, o["cliente_id"], -monto, "Usado en una compra", oid, uid)
        pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (oid,)).fetchone()[0]
        con.execute("UPDATE ordenes SET estado_pago=? WHERE id=?", (estado_pago_de(pagado, o["total"]), oid)); fijar_fecha_pago(con, oid)
        queda = credito_de(con, o["cliente_id"])
        registrar(con, oid, uid, "pago", f"Usó {fmt_usd(monto)} de su saldo a favor"
                  + (f" · le quedan {fmt_usd(queda)}" if queda > 0.009 else " · no le queda saldo"))
        con.commit(); return volver(oid, request)
    en_bs = es_bolivares(forma); tasa = tasa_hoy(con)["valor"]
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,tasa,cuenta,referencia,fecha,estado,confirmado_por,confirmado_en) VALUES (?,?,?,?,?,?,?,?,?,'confirmado',?,datetime('now','localtime'))",
                (oid, forma, monto, monto * tasa if en_bs else monto, "VES" if en_bs else "USD", tasa if en_bs else None, FORMA_CUENTA.get(forma), f.get("referencia") or None, datetime.date.today().isoformat(), uid))
    pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (oid,)).fetchone()[0]
    con.execute("UPDATE ordenes SET estado_pago=? WHERE id=?", (estado_pago_de(pagado, o["total"]), oid)); fijar_fecha_pago(con, oid)
    sobra = sobrante_a_favor(con, oid, uid)
    registrar(con, oid, uid, "pago", f"Cobro registrado: {forma} {fmt_usd(monto)}"
              + (f" · queda {fmt_usd(o['total'] - pagado)}" if pagado < o["total"] - 0.01 else " · saldada")
              + (f" · {fmt_usd(sobra)} le quedan a favor" if sobra else ""))
    con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/entrega")
async def editar_entrega(request: Request, oid: int, con=Depends(db)):
    rol = rol_de(request); f = await request.form()
    if "editar_entrega" not in PERMISOS[rol]: return volver(oid, request)
    campos = ["tipo_entrega", "direccion", "zona", "maps", "ciudad", "receptor_nombre", "receptor_telefono", "despachador", "agencia", "guia", "distribuidor", "fecha_prometida", "franja", "notas_entrega", "modalidad_envio", "forma_pago_prevista"]
    f = dict(f)
    te = f.get("tipo_entrega")
    # cada tipo de entrega solo guarda lo que le corresponde
    if te == "nacional": f["ciudad"] = f.get("ciudad_nac") or f.get("ciudad"); f["despachador"] = ""; f["distribuidor"] = ""; f["franja"] = ""
    elif te == "distribuidor": f["ciudad"] = f.get("ciudad_dist") or f.get("ciudad"); f["despachador"] = ""; f["agencia"] = ""; f["guia"] = ""; f["modalidad_envio"] = ""; f["franja"] = ""
    elif te == "pickup": f["despachador"] = ""; f["agencia"] = ""; f["guia"] = ""; f["modalidad_envio"] = ""; f["distribuidor"] = ""
    else: f["agencia"] = ""; f["guia"] = ""; f["modalidad_envio"] = ""; f["distribuidor"] = ""
    if "ciudad" not in f or f.get("ciudad") is None: f["ciudad"] = con.execute("SELECT ciudad FROM ordenes WHERE id=?", (oid,)).fetchone()[0]
    if not f.get("fecha_prometida"): f["fecha_prometida"] = datetime.date.today().isoformat()
    if f.get("despachador") == "__otro__": f["despachador"] = (f.get("despachador_otro") or "").strip()
    o = con.execute("SELECT * FROM ordenes WHERE id=?", (oid,)).fetchone()
    cambios = [f"{c}: '{o[c] or ''}' → '{f.get(c) or ''}'" for c in campos if (o[c] or "") != (f.get(c) or "")]
    con.execute(f"UPDATE ordenes SET {', '.join(c + '=?' for c in campos)} WHERE id=?", [f.get(c) or None for c in campos] + [oid])
    fijar_pago_despachador(con, oid)
    if cambios: registrar(con, oid, uid_de(request), "entrega", "; ".join(cambios))
    con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/revertir")
def revertir_estado(request: Request, oid: int, con=Depends(db)):
    """Vuelve la orden a Pendiente (deshace 'En ruta' o 'Entregado'). No toca los pagos."""
    rol = rol_de(request)
    if "entregar" not in PERMISOS[rol]: return volver(oid, request)
    o = con.execute("SELECT estado FROM ordenes WHERE id=?", (oid,)).fetchone()
    con.execute("UPDATE ordenes SET estado='pendiente', fecha_entrega=NULL, actualizado_en=datetime('now','localtime') WHERE id=?", (oid,))
    registrar(con, oid, uid_de(request), "estado", f"Vuelve a Pendiente (estaba {E_LABEL.get(o['estado'], o['estado'])})"); con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/eliminar")
def eliminar_orden(request: Request, oid: int, con=Depends(db)):
    """Borra la orden por completo (solo Cristina). Se usa para pruebas o errores de carga; para una venta real que se cae, usar Cancelar."""
    rol = rol_de(request)
    if "ver_dinero" not in PERMISOS[rol]: return volver(oid, request)
    borrar_orden(con, oid); con.commit()
    return RedirectResponse(request.query_params.get("volver") or "/ordenes", status_code=303)


def borrar_orden(con, oid):
    """Borra una orden y todo lo que cuelga de ella. No hace commit."""
    con.execute("DELETE FROM entregas_repuesto WHERE pack_id IN (SELECT id FROM packs WHERE orden_id=?)", (oid,))
    # el saldo a favor que dejó o que usó esta orden también se va: borrarla es como si nunca hubiera existido
    for tb in ("packs", "repuestos_prepagados", "pagos", "historial", "incidencias", "orden_lineas", "gastos", "mov_inventario", "fotos", "credito_cliente", "viajes_despachador"): con.execute(f"DELETE FROM {tb} WHERE orden_id=?", (oid,))
    con.execute("DELETE FROM ordenes WHERE id=?", (oid,))


@app.post("/clientes/{cid}/eliminar")
def eliminar_cliente(request: Request, cid: int, confirmar: str = Form(""), con=Depends(db)):
    """Borra un cliente con todo lo suyo: órdenes, mascotas, direcciones, notas, packs, saldo a favor.
    Solo Cristina, y solo si escribe el nombre del cliente: no se puede deshacer."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    c = con.execute("SELECT nombre FROM clientes WHERE id=?", (cid,)).fetchone()
    if not c: return RedirectResponse("/clientes", status_code=303)
    if " ".join(confirmar.split()).lower() != " ".join((c["nombre"] or "").split()).lower():
        return RedirectResponse(f"/clientes/{cid}?err=confirmar", status_code=303)
    for (oid,) in con.execute("SELECT id FROM ordenes WHERE cliente_id=?", (cid,)).fetchall(): borrar_orden(con, oid)
    con.execute("DELETE FROM entregas_repuesto WHERE pack_id IN (SELECT id FROM packs WHERE cliente_id=?)", (cid,))
    for tb in ("packs", "repuestos_prepagados", "seguimientos", "fotos", "credito_cliente", "notas_cliente", "direcciones", "mascotas"):
        con.execute(f"DELETE FROM {tb} WHERE cliente_id=?", (cid,))
    con.execute("UPDATE clientes SET referido_id=NULL WHERE referido_id=?", (cid,))   # a quienes recomendó no se les borra nada
    con.execute("DELETE FROM clientes WHERE id=?", (cid,)); con.commit()
    return RedirectResponse("/clientes", status_code=303)


@app.get("/ordenes/{oid}/eliminar")
def eliminar_orden_get(oid: int):   # si alguien recarga la página tras eliminar, volver a Órdenes en vez de mostrar un error
    return RedirectResponse("/ordenes", status_code=303)


@app.post("/ordenes/{oid}/guia")
def poner_guia(request: Request, oid: int, guia: str = Form(""), agencia: str = Form(""), con=Depends(db)):
    rol = rol_de(request)
    if "coordinar" not in PERMISOS[rol]: return volver(oid, request)
    con.execute("UPDATE ordenes SET guia=COALESCE(NULLIF(?,''),guia), agencia=COALESCE(NULLIF(?,''),agencia) WHERE id=?", (guia.strip(), agencia.strip(), oid))
    registrar(con, oid, uid_de(request), "guia", f"Guía {guia.strip() or '—'}" + (f" · {agencia}" if agencia else "")); con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/despachador")
def asignar_despachador(request: Request, oid: int, despachador: str = Form(""), despachador_otro: str = Form(""), con=Depends(db)):
    rol = rol_de(request)
    if "coordinar" not in PERMISOS[rol]: return volver(oid, request)
    if despachador == "__otro__": despachador = despachador_otro.strip()
    con.execute("UPDATE ordenes SET despachador=? WHERE id=?", (despachador or None, oid)); fijar_pago_despachador(con, oid)
    registrar(con, oid, uid_de(request), "despachador", f"Asignado: {despachador or '—'}"); con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/incidencia")
def nueva_incidencia(request: Request, oid: int, tipo: str = Form(...), descripcion: str = Form(""), clase: str = Form("incidencia"), con=Depends(db)):
    rol = rol_de(request)
    if "incidencia" not in PERMISOS[rol]: return volver(oid, request)
    o = con.execute("SELECT despachador FROM ordenes WHERE id=?", (oid,)).fetchone()
    con.execute("INSERT INTO incidencias (orden_id,clase,tipo,descripcion,responsable,autor_id) VALUES (?,?,?,?,?,?)", (oid, clase, tipo, descripcion, o["despachador"], uid_de(request)))
    if tipo == "entrega_fallida":
        con.execute("UPDATE ordenes SET estado='pendiente', despachador=NULL WHERE id=?", (oid,))
    registrar(con, oid, uid_de(request), clase, f"{tipo}: {descripcion}"); con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/incidencia/{iid}/cerrar")
def cerrar_incidencia(request: Request, oid: int, iid: int, resolucion: str = Form(""), con=Depends(db)):
    con.execute("UPDATE incidencias SET estado='cerrada', resolucion=?, cerrado_en=datetime('now','localtime') WHERE id=?", (resolucion, iid))
    registrar(con, oid, uid_de(request), "incidencia_cerrada", resolucion); con.commit(); return volver(oid, request)


CICLO_REPUESTO = 21   # regla Decopet: se hace seguimiento cada 21 días; si el cliente compra más seguido, se usa su ritmo
                      # (valor de arranque: lo reemplaza cargar_ajustes() con lo que haya en Configuración)

def ciclo_de(ritmo):
    """Cada cuántos días tocarle: su ritmo si compra más seguido que 21 días; si se tarda más, igual se le escribe a los 21."""
    return min(ritmo, CICLO_REPUESTO) if ritmo else CICLO_REPUESTO

IVA = 0.16   # IVA Venezuela: el catálogo muestra el precio con IVA al lado (para Cashea y facturas)


def respaldo_al_dia(horas=12):
    """¿El último respaldo es de hace menos de N horas?"""
    r = lista_respaldos()
    if not r.get("ts"): return False
    return (datetime.datetime.now() - r["ts"]).total_seconds() < horas * 3600


def arrancar_respaldo():
    """El respaldo se hace desde el propio ERP, no con un programa aparte: macOS no deja que
    un proceso programado lea dentro de Documentos. Mientras el ERP esté abierto se respalda solo."""
    def bucle():
        time.sleep(15)        # deja que el ERP termine de cargar antes del primer intento
        while True:
            try:
                if not respaldo_al_dia():
                    r = subprocess.run(["/bin/bash", str(BASE.parent / "scripts" / "respaldo.sh")],
                                       capture_output=True, text=True, timeout=120)
                    if r.returncode != 0:   # no tumba el ERP, pero tiene que quedar a la vista en el registro
                        print("RESPALDO FALLÓ:", (r.stderr or r.stdout or "sin mensaje")[-600:], flush=True)
            except Exception as e:
                print("RESPALDO FALLÓ:", repr(e), flush=True)   # un respaldo fallido nunca puede tumbar el ERP
            time.sleep(30 * 60)
    threading.Thread(target=bucle, daemon=True).start()


def cargar_ajustes():
    """Trae de Configuración las reglas que antes estaban clavadas en el código.
    Se llama al arrancar y cada vez que Cristina guarda, para que el cambio valga sin reiniciar."""
    global CICLO_REPUESTO, IVA
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    try:
        r = con.execute("SELECT clave, valor FROM config WHERE clave IN ('ciclo_repuesto','iva')").fetchall()
        v = {x["clave"]: x["valor"] for x in r}
        if v.get("ciclo_repuesto"): CICLO_REPUESTO = int(float(v["ciclo_repuesto"]))
        if v.get("iva"): IVA = float(v["iva"])
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()

PORCHE_SKU = {"PRO-M": ("PRO", "Mediano"), "PRO-G": ("PRO", "Grande"), "KIT-COMPLETO-M": ("PRO", "Mediano"), "KIT-COMPLETO-G": ("PRO", "Grande"),
              "BAS-M": ("Básico", "Mediano"), "BAS-G": ("Básico", "Grande"), "BAS": ("Básico", None)}

def actualizar_porche_cliente(con, oid):
    """Si la orden trae un porche, ese es el porche actual del cliente (cambio de tamaño incluido)."""
    r = con.execute("""SELECT o.cliente_id, p.sku FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id JOIN productos p ON p.id=l.producto_id
                       WHERE l.orden_id=? AND p.sku IN ('PRO-M','PRO-G','KIT-COMPLETO-M','KIT-COMPLETO-G','BAS-M','BAS-G','BAS') ORDER BY l.id DESC LIMIT 1""", (oid,)).fetchone()
    if not r or not r["cliente_id"]: return
    v, tam = PORCHE_SKU[r["sku"]]
    # un tamaño nuevo se SUMA (puede tener dos porches, uno por perro); si en realidad lo cambió, se quita el viejo en la ficha
    actual = con.execute("SELECT porche_tamano FROM clientes WHERE id=?", (r["cliente_id"],)).fetchone()[0] or ""
    tams = [x.strip() for x in actual.split(",") if x.strip()]
    if tam and tam not in tams: tams.append(tam)
    tams = [x for x in ("Mediano", "Grande") if x in tams]
    con.execute("UPDATE clientes SET porche_version=?, porche_tamano=? WHERE id=?", (v, ", ".join(tams) or None, r["cliente_id"]))

def _falta(msg):
    return HTMLResponse(f"<p style='font-family:sans-serif;padding:30px'>{msg} Vuelve atrás y agrégalo.</p>", 400)


def ubicacion_de(con, cid, direccion, maps):
    """El link de ubicación de una entrega: el del pedido; si no trae, el que está guardado en la ficha del cliente para
    ESA misma dirección (Cristina, 6 oct: la ubicación se guarda en la dirección habitual del cliente y sirve para todos
    sus pedidos). Si el pedido va a otra dirección, no se usa la ubicación de la habitual."""
    if _sirve(maps): return maps
    filas = con.execute("""SELECT direccion, maps FROM direcciones WHERE cliente_id=? AND NULLIF(TRIM(maps),'') IS NOT NULL
                           ORDER BY principal DESC, id""", (cid,)).fetchall()
    if not filas: return None
    if not _sirve(direccion): return filas[0]["maps"]
    norm = lambda t: re.sub(r"\W+", "", (t or "").lower())
    for f in filas:
        a, b = norm(f["direccion"]), norm(direccion)
        if a and b and (a == b or a in b or b in a): return f["maps"]
    return None


def _sirve(v):
    """'Pendiente' es el relleno que se guardaba cuando un dato no se pedía: cuenta como vacío."""
    return bool(v and str(v).strip() and str(v).strip().lower() != "pendiente")


@app.post("/ordenes/nueva")
async def crear_orden(request: Request, con=Depends(db)):
    rol = rol_de(request); f = await request.form(); uid = uid_de(request)
    cid = f.get("cliente_id") or None; cliente_recien_creado = False
    nac = f.get("tipo_entrega") == "nacional"
    if not cid and (f.get("cliente_nombre_pila") or "").strip():
        if not _sirve(f.get("cliente_telefono")): return _falta("El teléfono del cliente es obligatorio.")
        if not _sirve(f.get("cliente_correo")): return _falta("El correo del cliente es obligatorio.")
        cliente_recien_creado = True
        np_, ap = capitalizar(f["cliente_nombre_pila"]), capitalizar(f.get("cliente_apellido"), inicio=False) or None
        ciu, edo = normalizar_ciudad(f.get("cliente_ciudad") or f.get("ciudad"))
        cur = con.execute("INSERT INTO clientes (nombre_pila,apellido,nombre,telefono,cedula,correo,ciudad,estado,canal_habitual,origen,referido_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (np_, ap, nombre_completo(np_, ap), normalizar_telefono(f.get("cliente_telefono")) or "Pendiente", (f.get("cliente_cedula") or "").strip().upper() or None,
                           (f.get("cliente_correo") or "").strip() or "Pendiente", ciu or "Pendiente", edo, f.get("canal"),
                           (f.get("cliente_origen") or "").strip() or None,
                           int(f["cliente_referido_id"]) if (f.get("cliente_referido_id") or "").isdigit() else None))
        cid = cur.lastrowid
        guardar_mascotas(con, cid, f, prefijo="cliente_mascota_")
        if (f.get("cliente_direccion") or "").strip():   # dirección habitual del cliente nuevo
            con.execute("INSERT INTO direcciones (cliente_id,etiqueta,direccion,ciudad,estado,maps,principal) VALUES (?,?,?,?,?,?,1)", (cid, "Principal", f["cliente_direccion"].strip(), *normalizar_ciudad(f.get("cliente_ciudad")), (f.get("cliente_maps") or "").strip() or None))
    if cid and not cliente_recien_creado:   # cliente que ya existía al que le faltaban datos: se completan desde la orden
        cl = con.execute("SELECT telefono, correo, cedula FROM clientes WHERE id=?", (cid,)).fetchone()
        for campo, dato in (("telefono", normalizar_telefono(f.get("cl_add_telefono"))), ("correo", (f.get("cl_add_correo") or "").strip()), ("cedula", (f.get("cl_add_cedula") or "").strip().upper())):
            if dato and not _sirve(cl[campo]): con.execute(f"UPDATE clientes SET {campo}=? WHERE id=?", (dato, cid))
        cl = con.execute("SELECT telefono, correo, cedula FROM clientes WHERE id=?", (cid,)).fetchone()
        if not _sirve(cl["telefono"]): con.rollback(); return _falta("El teléfono del cliente es obligatorio.")
        if not _sirve(cl["correo"]): con.rollback(); return _falta("El correo del cliente es obligatorio.")
    if nac:   # la agencia pide una cédula: la del cliente, o la del tercero que va a retirar
        ced_ter = (f.get("receptor_cedula") or "").strip().upper()
        ced_cli = (f.get("cliente_cedula") or f.get("cl_add_cedula") or f.get("cedula_envio") or "").strip().upper()
        if ced_cli and cid and not _sirve(con.execute("SELECT cedula FROM clientes WHERE id=?", (cid,)).fetchone()[0]):
            con.execute("UPDATE clientes SET cedula=? WHERE id=?", (ced_cli, cid))
        tiene_cli = bool(cid and _sirve(con.execute("SELECT cedula FROM clientes WHERE id=?", (cid,)).fetchone()[0]))
        if not (tiene_cli or ced_ter):
            con.rollback(); return _falta("Para un envío nacional hace falta la cédula del cliente o la de quien recibe (la pide la agencia).")
    if cid and not (f.get("cliente_nombre_pila") or "").strip(): guardar_mascotas(con, cid, f, prefijo="cliente_mascota_")   # cliente existente que agrega mascota
    class _Form:   # el formulario con algunos campos reemplazados (dirección habitual del cliente), sin perder getlist
        def __init__(s, base, extra): s.b, s.e = base, extra
        def get(s, k, d=None): return s.e[k] if k in s.e else s.b.get(k, d)
        def getlist(s, k): return s.b.getlist(k)
        def __getitem__(s, k): return s.e[k] if k in s.e else s.b[k]
    if cid and f.get("dir_modo", "hab") == "hab" and f.get("tipo_entrega") not in ("pickup", "distribuidor"):
        d = con.execute("SELECT * FROM direcciones WHERE cliente_id=? ORDER BY principal DESC, id LIMIT 1", (cid,)).fetchone()
        if d: f = _Form(f, {"direccion": d["direccion"], "zona": f.get("zona_tarifa") or d["zona"] or "", "ciudad": d["ciudad"] or f.get("ciudad") or "Caracas", "maps": d["maps"] or ""})
    if f.get("zona_tarifa") and not (f.get("zona") or "").strip(): f = _Form(f, {"zona": f.get("zona_tarifa")})
    elif cid and f.get("dir_modo") == "nueva" and (f.get("direccion") or "").strip():
        # dirección nueva: se guarda como otra dirección del cliente (no reemplaza la habitual)
        if not con.execute("SELECT 1 FROM direcciones WHERE cliente_id=? AND direccion=?", (cid, f["direccion"])).fetchone():
            # cliente recién creado: esta es su dirección habitual; cliente existente: se guarda como dirección adicional
            con.execute("INSERT INTO direcciones (cliente_id,etiqueta,direccion,zona,ciudad,maps,principal) VALUES (?,?,?,?,?,?,?)", (cid, "Principal" if cliente_recien_creado else "Otra", f["direccion"], f.get("zona") or None, f.get("ciudad") or None, f.get("maps") or None, 1 if cliente_recien_creado else 0))
            ciu, edo = normalizar_ciudad(f.get("ciudad"))
            if cliente_recien_creado and ciu and ciu != "Pendiente": con.execute("UPDATE clientes SET ciudad=?, estado=COALESCE(?, estado) WHERE id=? AND ciudad='Pendiente'", (ciu, edo, cid))
    tasa = tasa_hoy(con)["valor"]
    lineas, subtotal, costo = [], 0.0, 0.0
    OPC = {r["sku"]: r for r in con.execute("SELECT * FROM productos WHERE tipo='opcion'")}
    prepagar = f.getlist("prepagar"); pack_inicio = f.getlist("pack_inicio"); pack_deliv = f.getlist("pack_deliv"); packs_inicio_por_linea = {}; packs_deliv_por_linea = {}
    for idx, (pid, cant, perso, color, malla) in enumerate(zip(f.getlist("producto_id"), f.getlist("cantidad"), f.getlist("personalizacion"), f.getlist("color"), f.getlist("malla"))):
        if not pid: continue
        p = con.execute("SELECT * FROM productos WHERE id=?", (pid,)).fetchone(); c = float(cant or 1)
        base = precio_linea(p, c)
        extras = (OPC["OPC-PERSO"]["precio"] if (perso and p["permite_personalizacion"]) else 0) + (OPC["OPC-MALLA"]["precio"] if (malla == "1" and p["permite_malla"]) else 0)
        extras_costo = (OPC["OPC-PERSO"]["costo"] if (perso and p["permite_personalizacion"]) else 0) + (OPC["OPC-MALLA"]["costo"] if (malla == "1" and p["permite_malla"]) else 0)
        lineas.append((p, c, perso or None, color if p["requiere_color"] else None, 1 if (malla == "1" and p["permite_malla"]) else 0, extras, base + extras, (idx < len(prepagar) and prepagar[idx] == "1")))
        if (p["sku"] or "").startswith("PACK"):
            pi = pack_inicio[idx] if idx < len(pack_inicio) else ""
            packs_inicio_por_linea[len(lineas) - 1] = int(pi) if pi.isdigit() else 1
            pd = pack_deliv[idx] if idx < len(pack_deliv) else ""
            packs_deliv_por_linea[len(lineas) - 1] = int(pd) if pd.isdigit() else 0
        subtotal += base + extras; costo += (p["costo"] or 0) * c + extras_costo
    if not lineas:
        con.rollback(); return HTMLResponse("<p style='font-family:sans-serif;padding:30px'>La orden no tiene productos. Vuelve atrás y agrega al menos uno.</p>", 400)
    if any(p["requiere_color"] and color not in PLATO_DE_COLOR for (p, _c, _pe, color, *_r) in lineas):   # sin color no se sabe qué plato sale del inventario
        con.rollback(); return _falta("Falta elegir el color del plato (azul o rosado) del Slow Chow.")
    px = float(f.get("personalizacion_extra") or 0)
    if px > 0:   # personalización cobrada aparte (monto libre): entra como línea de opción
        lineas.append((OPC["OPC-PERSO"], 1, (f.get("personalizacion_nombre") or "").strip() or None, None, 0, 0, px, False)); subtotal += px; costo += OPC["OPC-PERSO"]["costo"] or 0
    descuento = float(f.get("descuento") or 0); canal = f.get("canal") or "whatsapp"
    iva = round((subtotal - descuento) * 0.16, 2) if (canal == "cashea" or f.get("factura") == "1") else 0
    delivery = float(f.get("delivery") or 0); total = round(subtotal - descuento + iva + delivery, 2)
    # pack con delivery: los deliveries de las próximas entregas que deja pagados hoy se cobran ya (cantidad × tarifa de hoy)
    con_delivery = f.get("tipo_entrega") in ("delivery", "delivery_fuera") and delivery > 0
    deliv_pack = []   # (línea, entregas futuras pagadas por pack, monto)
    for li, (p, c, *_r) in enumerate(lineas):
        if li in packs_deliv_por_linea:
            ini = packs_inicio_por_linea.get(li, 1)
            k = max(packs_deliv_por_linea[li] - 1, 0) if (con_delivery and ini >= 1) else 0
            packs_deliv_por_linea[li] = k + 1 if k else 0
            if k: deliv_pack.append((li, k, round(k * int(c) * delivery, 2)))
    monto_dpack = round(sum(m for _, _, m in deliv_pack), 2)
    subtotal += monto_dpack; total = round(total + monto_dpack, 2)
    ultimo = con.execute("SELECT MAX(CAST(substr(numero,2) AS INTEGER)) FROM ordenes").fetchone()[0] or 0
    hoy_d = datetime.date.today()
    fecha_auto = f.get("fecha_prometida") or hoy_d.isoformat()
    # Pagos (puede ser mixto): forma + monto + referencia por línea
    pagos_in = [(fo, float(mo or 0), re_) for fo, mo, re_ in zip(f.getlist("pago_forma"), f.getlist("pago_monto"), f.getlist("pago_ref")) if fo and float(mo or 0) > 0]
    # pagar con lo que tenía a favor: nunca más de lo que de verdad tiene
    disponible = credito_de(con, cid) if cid else 0
    for i, (fo, mo, re_) in enumerate(pagos_in):
        if fo == SALDO_FAVOR:
            usa = round(min(mo, disponible), 2); disponible -= usa; pagos_in[i] = (fo, usa, re_)
    pagos_in = [p for p in pagos_in if p[1] > 0]
    cur = con.execute("""INSERT INTO ordenes (numero,tipo,cliente_id,canal,creada_por,ref_externa,estado,estado_pago,subtotal,descuento,motivo_descuento,iva,delivery,total,tasa_bcv,comision,
                         tipo_entrega,direccion,zona,ciudad,receptor_nombre,receptor_telefono,fecha_prometida,franja,notas_entrega,notas,costo_productos,forma_pago_prevista,modalidad_envio,maps)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (f"#{ultimo + 1}", f.get("tipo") or "venta", cid, canal, uid, f.get("ref_externa") or None, "pendiente",
                       "pagada", subtotal, descuento, f.get("motivo_descuento") or None, iva, delivery, total, tasa,
                       round(total * 0.06, 2) if canal == "cashea" else 0, f.get("tipo_entrega") or None, f.get("direccion") or None, f.get("zona") or None, f.get("ciudad") or None,
                       f.get("receptor_nombre") or None, f.get("receptor_telefono") or None, fecha_auto, f.get("franja") or None, f.get("notas_entrega") or None, f.get("notas") or None, costo,
                       " + ".join(dict.fromkeys(fo for fo, _, _ in pagos_in)) or ("BNC" if canal == "cashea" else None), f.get("modalidad_envio") or ("cobro_destino" if f.get("tipo_entrega") == "nacional" else None), f.get("maps") or None))
    oid = cur.lastrowid
    for li, (p, c, perso, color, malla, extras, total_linea, prepago) in enumerate(lineas):
        cur_l = con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,personalizacion,color,malla,extras,total) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (oid, p["id"], p["nombre"], c, total_linea if p["sku"] == "OPC-PERSO" else p["precio"], p["costo"], perso, color, malla, extras, total_linea))
        if (p["sku"] or "").startswith("PACK"):   # abre el pack con los repuestos que se lleva hoy
            unid = {"PACK3-M": 3, "PACK3-G": 3, "PACK4-M": 4, "PACK4-G": 4, "PACK8-M": 8, "PACK8-G": 8}.get(p["sku"], 3); ini = min(packs_inicio_por_linea.get(li, 1), unid)
            if "deliveries_prepagados" not in [r[1] for r in con.execute("PRAGMA table_info(packs)")]: con.execute("ALTER TABLE packs ADD COLUMN deliveries_prepagados INTEGER DEFAULT 0")
            dprep = max(packs_deliv_por_linea.get(li, 0) - 1, 0)   # deliveries de las próximas entregas, pagados hoy con la orden
            for _ in range(int(c)):
                con.execute("INSERT INTO packs (cliente_id,orden_id,producto_id,tamano,unidades,entregadas_inicio,estado,creado_en,deliveries_prepagados,tarifa_prepagada) VALUES (?,?,?,?,?,?,?,datetime('now','localtime'),?,?)",
                            (cid, oid, p["id"], "Grande" if p["sku"].endswith("G") else ("Mediano" if p["sku"].endswith("M") else None), unid, ini, "completo" if ini >= unid else "activo", dprep, delivery if dprep else None))
        for li_d, k_d, m_d in deliv_pack:
            if li_d == li:   # queda anotado dentro de la orden, como lo que se cobra aparte del producto
                con.execute("""INSERT INTO orden_lineas (orden_id, nombre, cantidad, precio, costo, total, extra_en)
                               VALUES (?,?,?,?,0,?,?)""", (oid, f"Delivery de las próximas entregas del pack ({k_d * int(c)} × ${delivery:g})", k_d * int(c), delivery, m_d, hoy_d.isoformat()))
        if prepago and (p["sku"] or "").startswith("REP-"):
            for _ in range(int(c)):
                con.execute("INSERT INTO repuestos_prepagados (cliente_id,orden_id,linea_id,producto_id,tamano,pagado_en,usuario_id) VALUES (?,?,?,?,?,?,?)",
                            (cid, oid, cur_l.lastrowid, p["id"], "Grande" if p["sku"] == "REP-G" else "Mediano", hoy_d.isoformat(), uid))
    if canal == "cashea" or f.get("factura") == "1": con.execute("UPDATE ordenes SET requiere_factura=1 WHERE id=?", (oid,))
    desp = f.get("despachador") or ""
    if desp == "__otro__": desp = (f.get("despachador_otro") or "").strip()
    if f.get("tipo_entrega") == "nacional" and (f.get("oficina_agencia") or "").strip():
        con.execute("UPDATE ordenes SET direccion=?, ciudad=COALESCE(NULLIF(ciudad,''), ?) WHERE id=?", (f["oficina_agencia"].strip(), f["oficina_agencia"].strip().split("·")[-1].strip(), oid))
    if (f.get("receptor_correo") or f.get("receptor_cedula")):
        cols = [r[1] for r in con.execute("PRAGMA table_info(ordenes)")]
        for c_ in ("receptor_correo", "receptor_cedula"):
            if c_ not in cols: con.execute(f"ALTER TABLE ordenes ADD COLUMN {c_} TEXT")
        con.execute("UPDATE ordenes SET receptor_correo=?, receptor_cedula=? WHERE id=?", ((f.get("receptor_correo") or "").strip() or None, (f.get("receptor_cedula") or "").strip().upper() or None, oid))
    con.execute("UPDATE ordenes SET despachador=COALESCE(NULLIF(?,''),despachador), agencia=COALESCE(NULLIF(?,''),agencia), guia=COALESCE(NULLIF(?,''),guia) WHERE id=?", (desp, f.get("agencia") or "", f.get("guia") or "", oid))
    if f.get("status") == "entregada":
        fe = (f.get("fecha_entrega") or hoy_d.isoformat()).strip()
        con.execute("UPDATE ordenes SET estado='entregada', fecha_entrega=?, fecha_prometida=? WHERE id=?", (fe, fe, oid)); registrar(con, oid, uid, "estado", f"Entregada el {fe} (registrada al crear)")
    fp = (f.get("fecha_pago") or "").strip()   # vacía = contra entrega: se llena sola el día que se entrega
    con.execute("UPDATE ordenes SET fecha_pago=? WHERE id=?", (fp or None, oid))
    if fp and fp != hoy_d.isoformat():   # fecha de pago distinta a hoy: la orden se fecha ese día (como en Airtable)
        con.execute("UPDATE ordenes SET creado_en=? || ' 12:00:00', fecha_prometida=COALESCE(fecha_prometida, ?) WHERE id=?", (fp, fp, oid))
        con.execute("UPDATE pagos SET fecha=? WHERE orden_id=?", (fp, oid))
    registrar(con, oid, uid, "creada", f"Orden creada por canal {canal}")
    descontar_inventario(con, oid, uid)
    ahora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M"); tasa_v = tasa
    confirma = "confirmar_pago" in PERMISOS[rol]
    digital = sum(m for fo, m, _ in pagos_in if not fo.startswith("Efectivo"))
    efectivo = sum(m for fo, m, _ in pagos_in if fo.startswith("Efectivo"))
    for fo, m, ref in pagos_in:
        if fo.startswith("Efectivo"): continue  # el efectivo se registra al entregar
        if fo == SALDO_FAVOR:   # no entra plata nueva a ninguna caja: se gasta la que ya había entrado
            con.execute("""INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado,confirmado_por,confirmado_en)
                           VALUES (?,?,?,?,'USD',?,'confirmado',?,?)""", (oid, SALDO_FAVOR, m, m, ahora, uid, ahora))
            mover_credito(con, cid, -m, "Usado en una compra", oid, uid)
            queda = credito_de(con, cid)
            registrar(con, oid, uid, "pago", f"Usó {fmt_usd(m)} de su saldo a favor" + (f" · le quedan {fmt_usd(queda)}" if queda > 0.009 else " · no le queda saldo"))
            continue
        # si lo registra alguien que no confirma pagos (Logística), queda por revisar hasta que Cristina lo vea
        est = "confirmado" if confirma else "por_confirmar"
        con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,tasa,cuenta,referencia,fecha,estado,confirmado_por,confirmado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (oid, fo, m, round(m * tasa_v, 2) if es_bolivares(fo) else m, "VES" if es_bolivares(fo) else "USD", tasa_v if es_bolivares(fo) else None, FORMA_CUENTA.get(fo), ref or None, ahora, est,
                     uid if confirma else None, ahora if confirma else None))
    # Cashea se trata como cualquier canal: si Cristina marca el pago completo, la orden queda pagada.
    # El seguimiento de las cuotas de Cashea queda para más adelante.
    por_revisar = 0 if confirma else sum(m for fo, m, _ in pagos_in if not fo.startswith("Efectivo") and fo != SALDO_FAVOR)
    if por_revisar > 0.009: ep = "por_confirmar"
    elif digital >= total - 0.01: ep = "pagada"
    elif digital + efectivo >= total - 0.01: ep = "contra_entrega"
    else: ep = estado_pago_de(digital, total)
    con.execute("UPDATE ordenes SET estado_pago=?, monto_contra_entrega=? WHERE id=?", (ep, round(efectivo, 2) if ep == "contra_entrega" else 0, oid))
    if len(pagos_in) > 1: registrar(con, oid, uid, "pago", "Pago mixto: " + ", ".join(f"{fo} {fmt_usd(m)}" for fo, m, _ in pagos_in))
    sobra = sobrante_a_favor(con, oid, uid, fp or None)   # pagó de más al crear la orden (no había vuelto)
    if sobra: registrar(con, oid, uid, "pago", f"Pagó {fmt_usd(sobra)} de más: le quedan a favor")
    actualizar_porche_cliente(con, oid); fijar_pago_despachador(con, oid)
    pasar_entrega_a_prepagado(con, oid, uid)
    con.commit(); return RedirectResponse(volver_tras_crear(oid, request), status_code=303)


@app.get("/tasa", response_class=HTMLResponse)
def tasa_pagina(request: Request, con=Depends(db)):
    hist = con.execute("SELECT t.*, u.nombre usuario FROM tasas t LEFT JOIN usuarios u ON u.id=t.usuario_id ORDER BY fecha_valor DESC, id DESC LIMIT 60").fetchall()
    return render(request, "tasa.html", seccion="configuracion", tasa=tasa_hoy(con), hist=hist)


@app.post("/tasa/actualizar")
def tasa_actualizar(request: Request):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    bcv.actualizar(DB, forzar=True); return RedirectResponse("/tasa", status_code=303)


@app.post("/tasa/manual")
def tasa_manual(request: Request, valor: float = Form(...), fecha_valor: str = Form(...), nota: str = Form(...), con=Depends(db)):
    if rol_de(request) != "admin": return RedirectResponse("/tasa", status_code=303)
    con.execute("INSERT OR REPLACE INTO tasas (fecha_valor, valor, fuente, manual, usuario_id, nota) VALUES (?,?,?,1,1,?)", (fecha_valor, valor, "Corrección manual", nota)); con.commit()
    return RedirectResponse("/tasa", status_code=303)


# ------------------------------------------------------------------ OPERACIONES
def repuesto_de_pack(k):
    """Qué se lleva de un pack y qué número es: ('Repuesto Mediano', '1/3').
    El saldo es lo que quedaba antes de esta entrega, así que el primero de hoy es el siguiente."""
    n = k["retiro_programado"] or 1; u = k["unidades"] or 0; i = u - (k["saldo"] or 0) + 1
    que = f"Repuesto {k['tamano'] or ''}".strip()
    if n == 1: return que, f"{i}/{u}"
    return f"{n}× {que}", f"{i}–{i + n - 1}/{u}"


@app.get("/operaciones", response_class=HTMLResponse)
def operaciones(request: Request, cola: str = "hoy", tipo: str = "", agencia: str = "", dia: str = "", desp: str = "", vista: str = "tipo", q: str = "", con=Depends(db)):
    rol = rol_de(request); hoy_d = datetime.date.today(); hoy = hoy_d.isoformat(); manana = (hoy_d + datetime.timedelta(days=1)).isoformat()
    activas = cargar_ordenes(con, {"estado": "activas"}, rol)
    # logística también cobra (ve los pagos de los pedidos), así que ve la cola de saldos
    con_saldo = cargar_ordenes(con, {"estado": "con_saldo"}, rol) if "ver_cobros" in PERMISOS[rol] else []
    for o in con_saldo:
        if o["id"] not in {a["id"] for a in activas}: activas.append(o)
    for o in activas:
        o["nota_log"] = " · ".join(indicaciones_cliente(con, o["cliente_id"], o["notas_entrega"])) or None
        o["pendiente_desde"] = o["pagada_en"] or o["creado_en"]
        o["fecha_op"] = o["fecha_prometida"] or hoy
    if dia and dia not in (hoy, manana): cola = "dia"
    elif dia == manana: cola = "manana"; dia = ""
    ids_saldo = {o["id"] for o in con_saldo}
    def es(o, k):
        if k == "con_saldo": return o["id"] in ids_saldo
        if o["estado"] not in ("pendiente", "en_ruta"): return False        # entregadas solo cuentan en "con saldo"
        if k == "retrasadas": return any(a[0] == "retrasada" for a in o["alertas"])
        if k == "incidencias": return o["incidencias"] > 0
        if k == "sin_coordinar": return not o["coordinada"]
        if k == "hoy": return o["fecha_op"] <= hoy          # lo de hoy incluye lo que quedó atrás
        if k == "manana": return o["fecha_op"] == manana
        if k == "dia": return o["fecha_op"] == dia
        return True
    colas = {"hoy": "Hoy", "manana": "Mañana", "dia": "Otro día", "retrasadas": "Retrasadas", "sin_coordinar": "Sin coordinar", "incidencias": "Incidencias", "con_saldo": "Con saldo pendiente", "todo": "Todo"}
    conteos = {k: sum(1 for o in activas if es(o, k)) for k in colas}
    pk = [k for k in cargar_packs(con) if k["saldo"] > 0 and k["fecha_programada"]]
    conteos["hoy"] += sum(1 for k in pk if k["fecha_programada"] <= hoy); conteos["manana"] += sum(1 for k in pk if k["fecha_programada"] == manana); conteos["todo"] += len(pk)
    pre = cargar_prepagados(con)
    conteos["hoy"] += sum(1 for r in pre if r["fecha_programada"] and r["fecha_programada"] <= hoy)
    conteos["manana"] += sum(1 for r in pre if r["fecha_programada"] == manana)
    conteos["todo"] += sum(1 for r in pre if r["fecha_programada"])
    lista = [o for o in activas if es(o, cola)]
    conteos_tipo = {k: sum(1 for o in lista if o["tipo_entrega"] == k) for k in ENTREGA}
    conteos_ag = {a: sum(1 for o in lista if o["tipo_entrega"] == "nacional" and o["agencia"] == a) for a in AGENCIAS[:-1]}
    despachadores = sorted({o["despachador"] for o in lista if o["despachador"]} | set(DESPACHADORES))
    conteos_desp = {d: sum(1 for o in lista if o["despachador"] == d) for d in despachadores}
    conteos_desp["__sin__"] = sum(1 for o in lista if not o["despachador"] and o["tipo_entrega"] in ("delivery", "delivery_fuera"))
    if tipo: lista = [o for o in lista if o["tipo_entrega"] == tipo]
    if tipo == "nacional" and agencia: lista = [o for o in lista if o["agencia"] == agencia]
    if desp == "__sin__": lista = [o for o in lista if not o["despachador"] and o["tipo_entrega"] in ("delivery", "delivery_fuera")]
    elif desp: lista = [o for o in lista if o["despachador"] == desp]
    if q.strip():   # búsqueda dentro de lo que hay en operaciones (no manda a Órdenes)
        qq = q.strip().lower()
        lista = [o for o in lista if qq in " ".join(str(o.get(k) or "") for k in ("cliente", "numero", "productos", "direccion", "ciudad", "despachador", "agencia", "receptor_nombre")).lower()
                 or qq.replace("-", "") in str(o.get("telefono") or "").replace("-", "")]
    # retiros de packs PROGRAMADOS (el cliente pidió su repuesto y se le puso día): entran como una entrega más
    if cola in ("hoy", "manana", "dia", "todo", "sin_coordinar") and not desp:
        dia_ref = {"hoy": hoy, "manana": manana, "dia": dia}.get(cola)
        for k in cargar_packs(con):
            if k["saldo"] <= 0 or not k["fecha_programada"]: continue
            if cola == "hoy" and k["fecha_programada"] > hoy: continue
            if cola in ("manana", "dia") and k["fecha_programada"] != dia_ref: continue
            if cola == "sin_coordinar" and (k["tipo_programado"] not in ("delivery", "delivery_fuera") or k["despachador_programado"]): continue
            d = con.execute("SELECT * FROM direcciones WHERE cliente_id=? ORDER BY principal DESC, id LIMIT 1", (k["cliente_id"],)).fetchone()
            te = k["tipo_programado"] or k["tipo_entrega"]
            if tipo and te != tipo: continue
            lista.append(dict(id=None, es_pack=True, pack_id=k["id"], saldo=k["saldo"], cuantos_prog=k["retiro_programado"] or 1, cliente=k["cliente"], cliente_id=k["cliente_id"], numero=k["orden"] or "pack", alertas=[], incidencias=0,
                              fecha_op=k["fecha_programada"], fecha_prometida=k["fecha_programada"], productos=repuesto_de_pack(k)[0], pack_pos=repuesto_de_pack(k)[1], nota_log=" · ".join(indicaciones_cliente(con, k["cliente_id"], k["nota_programada"])) or None, tipo_entrega=te, franja=None,
                              receptor_nombre=None, agencia=None, guia=None, distribuidor=None, despachador=k["despachador_programado"], ciudad=(d["ciudad"] if d else k["ciudad"]), zona=None,
                              direccion=(d["direccion"] if d else None), maps=(d["maps"] if d else None), estado_pago=("pagada" if (not k["delivery_programado"] or k["delivery_pagado"]) else "contra_entrega"), estado=("en_ruta" if k["en_ruta"] else "pendiente"), coordinada=bool(k["despachador_programado"] or te == "pickup"), total=k["delivery_programado"] or 0, pagado=0, monto_contra_entrega=(k["delivery_programado"] if (k["delivery_programado"] and not k["delivery_pagado"]) else None), telefono=k["telefono"]))
    if cola in ("hoy", "manana", "dia", "todo", "sin_coordinar") and not desp:
        dia_ref = {"hoy": hoy, "manana": manana, "dia": dia}.get(cola)
        for r in cargar_prepagados(con):
            if not r["fecha_programada"]: continue
            if cola == "hoy" and r["fecha_programada"] > hoy: continue
            if cola in ("manana", "dia") and r["fecha_programada"] != dia_ref: continue
            if cola == "sin_coordinar" and (r["tipo_entrega"] not in ("delivery", "delivery_fuera") or r["despachador"]): continue
            if tipo and r["tipo_entrega"] != tipo: continue
            lista.append(dict(id=None, es_prepagado=True, rid=r["id"], cliente=r["cliente"], cliente_id=r["cliente_id"], numero=r["orden"] or "", alertas=[], incidencias=0,
                              fecha_op=r["fecha_programada"], fecha_prometida=r["fecha_programada"], productos=f"1× Repuesto {r['tamano'] or ''} · prepagado", nota_log=" · ".join(indicaciones_cliente(con, r["cliente_id"], r["notas"])) or None, tipo_entrega=r["tipo_entrega"], franja=None,
                              receptor_nombre=None, agencia=r["agencia"], guia=None, distribuidor=None, despachador=r["despachador"], ciudad=r["ciudad"], zona=None, direccion=r["direccion"], maps=r["maps"],
                              estado_pago="pagada", estado=("en_ruta" if r["en_ruta"] else "pendiente"),
                              coordinada=bool(r["despachador"] or r["agencia"] or r["tipo_entrega"] == "pickup"), total=0, pagado=0, monto_contra_entrega=None, telefono=r["telefono"],
                              delivery=r["delivery"], delivery_pagado=r["delivery_pagado"]))   # para preguntar si cobró el delivery al entregar
    lista.sort(key=lambda o: (o["fecha_op"], o["franja"] or "", o["id"] or 0))
    for o in lista:   # volvió de la calle sin entregar: que se note, para no confundirlo con uno nuevo
        if o.get("id") and o["estado"] == "pendiente":
            h_ = con.execute("SELECT detalle, creado_en FROM historial WHERE orden_id=? AND accion='estado' ORDER BY id DESC LIMIT 1", (o["id"],)).fetchone()
            if h_ and (h_["detalle"] or "").startswith("En ruta → Pendiente: no se pudo entregar"): o["no_entregado"] = h_["creado_en"]
    if vista == "status":
        grupos = {"Por coordinar": [], "Pendiente por entregar": [], "En ruta": []}
        for o in lista:
            k = "En ruta" if o["estado"] == "en_ruta" else ("Pendiente por entregar" if o["coordinada"] else "Por coordinar")
            grupos[k].append(o)
        grupos = {k: v for k, v in grupos.items() if v}
    elif vista == "desp":
        grupos = {}
        for o in lista:
            if o["tipo_entrega"] == "pickup": continue      # pick-up no lo entrega nadie
            elif o["tipo_entrega"] == "nacional": k = f"Agencia · {o['agencia']}" if o["agencia"] else "Envío nacional · sin agencia"
            elif o["tipo_entrega"] == "distribuidor": k = f"Distribuidor · {o['distribuidor'] or '—'}"
            else: k = o["despachador"] or "Sin despachador asignado"
            grupos.setdefault(k, []).append(o)
        grupos = dict(sorted(grupos.items(), key=lambda kv: (kv[0].startswith("Sin "), kv[0].startswith("Agencia"), kv[0].startswith("Distribuidor"), kv[0])))
    else:
        grupos = {ENTREGA[k]: [o for o in lista if o["tipo_entrega"] == k] for k in ENTREGA}
        grupos["Sin tipo de entrega"] = [o for o in lista if not o["tipo_entrega"]]
        grupos = {k: v for k, v in grupos.items() if v}
    por_desp = {}
    for o in activas:
        if o["despachador"] and o["fecha_op"] <= hoy and o["estado"] in ("pendiente", "en_ruta"):
            por_desp.setdefault(o["despachador"], []).append(resumen_despacho(cargar_orden(con, o["id"]), con_plata="ver_cobros" in PERMISOS[rol]))
    # envío nacional: pedidos que todavía nadie ha llevado a la oficina de la agencia
    por_llevar = con.execute("""SELECT o.id, o.numero, o.agencia, o.ciudad, o.estado, c.nombre cliente,
                                       COALESCE(o.fecha_prometida, substr(o.creado_en,1,10)) fecha
                                FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id
                                WHERE o.tipo_entrega='nacional' AND o.viaje_id IS NULL AND o.origen_excel=0
                                  AND o.estado IN ('pendiente','en_ruta') AND """ + HAY_QUE_ENTREGAR() + """ ORDER BY o.agencia, o.id""").fetchall()
    por_agencia = {}
    for o in por_llevar: por_agencia.setdefault(o["agencia"] or "", []).append(o)
    por_agencia = dict(sorted(por_agencia.items(), key=lambda kv: (kv[0] == "", kv[0])))
    viajes = con.execute("""SELECT v.*, (SELECT COUNT(*) FROM ordenes o WHERE o.viaje_id=v.id) n_ordenes
                            FROM viajes_agencia v WHERE v.pagado=0 AND v.llevado_en IS NULL
                            ORDER BY v.fecha DESC, v.id DESC""").fetchall()
    # en Operaciones solo lo que falta llevar. Lo ya llevado pasa a la ficha del despachador (ahí se deshace si hizo falta).
    # Pagarlo no es de aquí: se le paga el viernes con lo demás, desde su ficha de despachador.
    tarifas_ag = {a: tarifa_agencia(con, a) for a in AGENCIAS}
    oficinas_ag = oficinas_agencia(con)
    return render(request, "operaciones.html", seccion="operaciones", grupos=grupos, cola=cola, colas=colas, conteos=conteos, por_desp=por_desp, total=len(lista), hoy_iso=hoy, manana_iso=manana,
                  por_llevar=por_llevar, por_agencia=por_agencia, viajes=viajes, tarifas_ag=tarifas_ag, oficinas_ag=oficinas_ag,
                  tipo=tipo, agencia=agencia, conteos_tipo=conteos_tipo, conteos_ag=conteos_ag, dia=dia, desp=desp, despachadores=despachadores, conteos_desp=conteos_desp, vista=vista, fecha_larga=fecha_larga, q=q)


# ------------------------------------------------------------------ FINANZAS (solo Cristina)
# forma de pago → caja donde cae la plata
# Lo que el despachador puede decir que le pagaron. No ve las cajas: si no es efectivo, el pago le llega
# a Cristina "por revisar" sin caja, y ella elige en cuál entró al confirmarlo.
FORMAS_DESPACHADOR = ["Zelle", "Binance", "Pago Móvil", "Otro"]
def forma_del_despachador(forma, otro=""):
    """'Zelle' → 'Zelle (según despachador)'. Lo que no es de la lista se deja igual."""
    if forma not in FORMAS_DESPACHADOR: return forma
    if forma == "Otro" and (otro or "").strip(): forma = (otro or "").strip()[:40]
    return f"{forma} (según despachador)"
def caja_de(forma):
    """La caja donde entra un pago con esa forma, o None si no es una caja (lo reportó el despachador y falta asignarla)."""
    return FORMA_CUENTA.get(forma or "") or (forma if forma in MONEDA_CAJA else None)
FORMA_CUENTA = {}   # forma de pago → nombre de caja. Ahora son lo mismo; el dict queda para los nombres viejos.
NOMBRES_VIEJOS = {"Pago Móvil": "Pago Móvil VES", "BNC": "BNC Cashea", "Zelle": "Zelle Decopet",
                  "Efectivo USD": "Efectivo USD Caracas", "Efectivo Bs": "Efectivo USD Caracas",
                  "Efectivo EUR": "Efectivo Euros", "Binance USDT": "Binance USDT Investment",
                  "PayPal": "Wise", "Transferencia USD/EUR": "Amerant", "Saldo a favor": "Cuentas Por Cobrar"}


MONEDA_CAJA = {}   # nombre de caja → moneda, para saber cuándo el monto viene en Bs


def _monedas_cajas():
    con = sqlite3.connect(DB); r = con.execute("SELECT nombre, moneda FROM cuentas").fetchall(); con.close(); return r


def es_bolivares(forma):
    """Cobra en bolívares si la caja donde entra está en VES."""
    return MONEDA_CAJA.get(FORMA_CUENTA.get(forma or "", forma or ""), "USD") == "VES"


def mapa_embed(maps, direccion="", ciudad=""):
    """Mapa incrustado de Google (sin clave) a partir del link GPS: usa las coordenadas si el link las trae,
    el nombre del sitio si es un /place/, y si no (links cortos maps.app.goo.gl) la dirección escrita."""
    import urllib.parse
    u = urllib.parse.unquote(maps or "")
    m = re.search(r"[?&]q=(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", u) or re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", u)
    if m: q = f"{m.group(1)},{m.group(2)}"
    else:
        p = re.search(r"/maps/place/([^/?]+)", u)
        q = p.group(1).replace("+", " ") if p else ", ".join(x for x in ((direccion or "").strip(), (ciudad or "").strip(), "Venezuela") if x)
    return "https://maps.google.com/maps?q=" + urllib.parse.quote(q) + "&z=16&output=embed"


tpl.env.globals["mapa_embed"] = mapa_embed


CODIGO_CAJA = {}   # nombre de la caja → su número (001, 002…), para mostrarlo en las listas


def caja_con_numero(nombre):
    """En las listas cada caja va con su número delante: '003 · Zelle Decopet'. Así se busca más rápido."""
    c = CODIGO_CAJA.get(nombre or "")
    return f"{c} · {nombre}" if c else (nombre or "")


tpl.env.filters["caja"] = caja_con_numero


def cargar_formas_pago():
    """Las formas de pago son las cajas: así nunca falta una ni sobra una que ya no usas."""
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    filas = con.execute("SELECT nombre, codigo, COALESCE(cobra,1) cobra FROM cuentas WHERE activa=1 ORDER BY orden, codigo").fetchall()
    cajas = [r["nombre"] for r in filas]
    CODIGO_CAJA.clear(); CODIGO_CAJA.update({r["nombre"]: r["codigo"] for r in filas if r["codigo"]})
    con.close()
    if cajas:
        FORMAS_PAGO[:] = cajas                                        # para pagar: todas
        FORMAS_COBRO[:] = [r["nombre"] for r in filas if r["cobra"]]   # para cobrarle a un cliente: sin las de inversión ni personales
        MONEDA_CAJA.clear()
        MONEDA_CAJA.update(dict(_monedas_cajas()))
        FORMA_CUENTA.clear()
        FORMA_CUENTA.update({c: c for c in cajas})        # la forma es la caja
        FORMA_CUENTA.update({v: w for v, w in NOMBRES_VIEJOS.items() if w in cajas})   # lo registrado antes sigue apuntando bien

CONCEPTO = {"pago": "Venta", "gasto": "Pago", "transferencia": "Swap", "retiro": "Retiro", "aporte": "Aporte", "reembolso": "Reembolso", "prestamo": "Préstamo", "liquidacion_despachador": "Liquidación", "ajuste": "Ajuste"}
TIPOS_MOV = {"transferencia": "Transferencia entre cuentas", "retiro": "Retiro de Cristina", "aporte": "Aporte de Cristina", "reembolso": "Reembolso a cliente",
             "prestamo": "Préstamo", "liquidacion_despachador": "Liquidación de despachador (efectivo recibido)", "ajuste": "Ajuste de saldo"}


def solo_admin(request):
    return rol_de(request) == "admin"


def efectivo_por_registrar(con):
    """Lo que se cobró en efectivo en la puerta y todavía no has pasado al Cash flow.
    Como las ventas no entran solas al libro, sin esto no hay quien te lo recuerde."""
    if ventas_automaticas(con): return []          # si entran solas, no hay nada que pasar
    desde = finanzas_desde(con) or "0000-01-01"
    return [dict(r) for r in con.execute("""SELECT p.id, p.monto_usd, p.fecha, p.forma, p.cuenta,
                   o.numero, o.despachador, COALESCE(NULLIF(c.nombre_pila,''), c.nombre) cliente
                   FROM pagos p JOIN ordenes o ON o.id=p.orden_id LEFT JOIN clientes c ON c.id=o.cliente_id
                   WHERE p.estado='confirmado' AND p.en_cashflow=0 AND p.forma LIKE 'Efectivo%'
                     AND substr(p.fecha,1,10) >= ? AND o.origen_excel=0
                   ORDER BY p.fecha DESC, p.id DESC""", (desde,))]


@app.post("/cashflow/efectivo-registrado")
async def efectivo_registrado(request: Request, con=Depends(db)):
    """Marcar que ya lo pasaste al libro. No mueve plata: solo apaga el recordatorio."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    f = await request.form()
    ids = [int(x) for x in f.getlist("pago_id") if str(x).isdigit()]
    if ids:
        con.execute(f"UPDATE pagos SET en_cashflow=1 WHERE id IN ({','.join('?' * len(ids))})", ids)
        con.commit()
    return RedirectResponse(f.get("volver") or "/cashflow", status_code=303)


# ------------------------------------------------------------------ SALDO A FAVOR
SALDO_FAVOR = "Saldo a favor"


def credito_de(con, cliente_id):
    """Lo que el cliente tiene a favor. Sale de pagar de más cuando no hay vuelto."""
    r = con.execute("SELECT COALESCE(SUM(monto),0) FROM credito_cliente WHERE cliente_id=?", (cliente_id,)).fetchone()
    return round(r[0] or 0, 2)


def mover_credito(con, cliente_id, monto, motivo, oid=None, uid=None, fecha=None):
    """Positivo: se le queda debiendo. Negativo: lo usó en una compra."""
    if not cliente_id or abs(monto) < 0.005: return
    con.execute("INSERT INTO credito_cliente (cliente_id, fecha, monto, motivo, orden_id, usuario_id) VALUES (?,?,?,?,?,?)",
                (cliente_id, fecha or datetime.date.today().isoformat(), round(monto, 2), motivo, oid, uid))


def sobrante_a_favor(con, oid, uid, fecha=None):
    """Si en una orden se cobró más que su total, el sobrante queda a favor del cliente
    en vez de quedar como un error. Devuelve cuánto quedó a favor."""
    o = con.execute("SELECT cliente_id, total FROM ordenes WHERE id=?", (oid,)).fetchone()
    if not o: return 0
    pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (oid,)).fetchone()[0]
    ya = con.execute("SELECT COALESCE(SUM(monto),0) FROM credito_cliente WHERE orden_id=? AND monto>0", (oid,)).fetchone()[0]
    sobra = round(pagado - (o["total"] or 0) - ya, 2)
    if sobra > 0.009:
        mover_credito(con, o["cliente_id"], sobra, "Pagó de más (sin vuelto)", oid, uid, fecha)
        return sobra
    return 0


@app.post("/clientes/{cid}/credito")
def credito_manual(request: Request, cid: int, monto: str = Form("0"), motivo: str = Form(""), accion: str = Form(""),
                   volver_a: str = Form(""), con=Depends(db)):
    """Anotar o descontar un saldo a favor a mano (para corregir: lo normal es que se genere y se use solo)."""
    if "confirmar_pago" not in PERMISOS[rol_de(request)]: return RedirectResponse("/operaciones", status_code=303)
    m = cifra(monto) or 0
    if accion == "restar": m = -min(abs(m), credito_de(con, cid))
    elif accion == "sumar": m = abs(m)
    if m: mover_credito(con, cid, m, motivo.strip() or ("Saldo a favor" if m > 0 else "Corrección"), None, uid_de(request))
    con.commit(); return RedirectResponse(volver_a or f"/clientes/{cid}", status_code=303)


@app.post("/clientes/{cid}/credito/devolver")
def credito_devolver(request: Request, cid: int, monto: str = Form("0"), caja: str = Form(""), fecha: str = Form(""), con=Depends(db)):
    """El cliente prefirió que le devuelvan la plata en vez de usarla en otra compra.
    Sale de la caja que elijas y su saldo a favor baja. No es un gasto: es plata de él que vuelve."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    m = round(min(cifra(monto) or 0, credito_de(con, cid)), 2)
    cta = con.execute("SELECT id, nombre FROM cuentas WHERE id=? AND activa=1", (int(caja) if caja.isdigit() else 0,)).fetchone()
    if m > 0 and cta:
        f = fecha or datetime.date.today().isoformat(); uid = uid_de(request)
        nombre = con.execute("SELECT nombre FROM clientes WHERE id=?", (cid,)).fetchone()[0]
        con.execute("""INSERT INTO movimientos (fecha, tipo, cuenta_origen_id, monto_usd, monto_real, moneda, concepto, categoria, usuario_id)
                       VALUES (?,'salida',?,?,?,'USD',?,'Devolución a cliente',?)""", (f, cta["id"], m, m, f"Devolución de saldo a favor · {nombre}", uid))
        mover_credito(con, cid, -m, f"Se le devolvió la plata ({cta['nombre']})", None, uid, f)
        con.commit()
    return RedirectResponse(f"/clientes/{cid}", status_code=303)


def caja_efectivo(con):
    """Dónde entra la plata cobrada en la puerta: el efectivo general.
    A propósito NO se busca una caja con el nombre del despachador — las cajas son de
    Cristina y no tienen relación con quién entrega."""
    r = con.execute("SELECT nombre FROM cuentas WHERE nombre LIKE 'Efectivo USD%' AND activa=1 ORDER BY orden LIMIT 1").fetchone()
    return r["nombre"] if r else "Efectivo USD"


def saldos(con):
    """Saldo por cuenta = saldo inicial + pagos confirmados − gastos ± movimientos, desde la fecha de corte."""
    out = []
    for c in con.execute("SELECT * FROM cuentas WHERE activa=1 ORDER BY orden"):
        corte = c["fecha_corte"] or "1900-01-01"
        desde = (con.execute("SELECT valor FROM config WHERE clave='cashflow_desde'").fetchone() or [None])[0]
        ing = 0 if not ventas_automaticas(con) else con.execute("""SELECT COALESCE(SUM(p.monto_usd),0) FROM pagos p JOIN ordenes o ON o.id=p.orden_id
                             WHERE p.estado='confirmado' AND p.cuenta=? AND o.origen_excel=0 AND ?2 IS NOT NULL AND substr(COALESCE(p.confirmado_en,p.fecha),1,10)>=? AND substr(COALESCE(p.confirmado_en,p.fecha),1,10)>=?2""", (c["nombre"], desde, corte)).fetchone()[0]
        gas = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM gastos WHERE cuenta_id=? AND fecha>=?", (c["id"], corte)).fetchone()[0]
        m_in = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM movimientos WHERE cuenta_destino_id=? AND fecha>=?", (c["id"], corte)).fetchone()[0]
        m_out = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM movimientos WHERE cuenta_origen_id=? AND fecha>=?", (c["id"], corte)).fetchone()[0]
        d = dict(c); d.update(ingresos=ing, gastos=gas, entradas=m_in, salidas=m_out, saldo=round(c["saldo_inicial"] + ing - gas + m_in - m_out, 2)); out.append(d)
    return out


@app.get("/cashflow", response_class=HTMLResponse)
def cashflow(request: Request, caja: str = "", mes: str = "", q: str = "", con=Depends(db)):
    """La caja del negocio arriba, las inversiones y lo personal aparte, y registrar una entrada o salida en dos clics."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    cs = saldos(con)
    lineas = libro_caja(con, int(caja) if caja else None, mes or None)
    if q.strip():   # el buscador de arriba busca dentro del libro (concepto, detalle o caja)
        qq = q.strip().lower()
        lineas = [l for l in lineas if qq in " ".join(str(x or "") for x in (l.get("concepto"), l.get("detalle"), l["caja"]["nombre"] if l.get("caja") else "")).lower()]
    meses = sorted({l["fecha"][:7] for l in libro_caja(con)}, reverse=True)

    activas = [c for c in cs if c["activa"]]
    total = round(sum(c["saldo"] for c in activas), 2)   # todas son cajas de Decopet: no se separan
    PAL = ["#5A9075", "#E1782E", "#3f6d56", "#E4B18F", "#ABBABA", "#F89980", "#c9d6cf", "#8fb3a1"]

    # dona: las cajas con saldo (hasta 6 + "otras"), si no es ilegible como en el Excel
    import math
    pos = sorted([c for c in activas if c["saldo"] > 0.005], key=lambda c: -c["saldo"])
    suma = sum(c["saldo"] for c in pos) or 1
    partes = [(c["nombre"], c["saldo"], c["saldo"] / suma) for c in pos[:6]]
    if len(pos) > 6:
        resto = sum(c["saldo"] for c in pos[6:]); partes.append(("Otras cajas", resto, resto / suma))
    VUELTA = 2 * math.pi * 70   # la dona se dibuja con dash sobre un círculo: más simple y siempre pinta bien
    arcos = []; recorrido = 0.0
    for k, (nombre, monto, frac) in enumerate(partes):
        largo = frac * VUELTA
        arcos.append({"largo": round(largo, 2), "vuelta": round(VUELTA, 2), "offset": round(-recorrido, 2),
                      "color": PAL[k % len(PAL)], "nombre": nombre, "monto": monto, "pct": round(frac * 100, 1)})
        recorrido += largo
    cats = cfg_json(con, "categorias_gasto"); cats_ent = cfg_json(con, "categorias_entrada")
    ORDEN_ENT = ["Ventas", "Ajustes", "Cobros", "Inversiones", "Otro"]   # el orden en que se ven, no alfabético
    # subcategorías ya usadas: lo que Cristina escribió alguna vez queda disponible la próxima
    for tabla in ("gastos", "movimientos"):
        for r in con.execute(f"SELECT DISTINCT categoria, subcategoria FROM {tabla} WHERE categoria IS NOT NULL AND subcategoria IS NOT NULL"):
            destino = cats if tabla == "gastos" else cats_ent
            if r["categoria"] in destino and r["subcategoria"] not in destino[r["categoria"]]:
                destino[r["categoria"]].insert(-1, r["subcategoria"])
    # a quién le pagas cambia según la categoría: no tiene sentido ofrecer proveedores cuando pagas una quincena
    todos_prov = [r[0] for r in con.execute("SELECT nombre FROM proveedores WHERE activo=1 ORDER BY nombre")]
    desps = [r[0] for r in con.execute("SELECT nombre FROM despachadores WHERE activo=1 ORDER BY nombre")]
    equipo = [r[0] for r in con.execute("SELECT nombre FROM usuarios WHERE nomina=1 ORDER BY nombre")]   # la subcategoría dice qué le pagaste; aquí va quién
    A_QUIEN = {
        "Producción":           [x for x in todos_prov if x in ("Walter", "David")],
        "Proveedores":          [x for x in todos_prov if x not in ("Walter", "David", "Ferretería")],
        "Materiales e insumos": [x for x in todos_prov if x == "Ferretería"] + ["Ferretería"],
        "Equipo":               equipo,
        "Sueldo Cristina":      ["Cristina"],
        "Despachadores":        desps,
        "Envíos":               desps + ["Zoom", "MRW", "Tealca", "Domesa", "Liberty Express"],
        "Servicios":            ["Ingrid", "Víctor", "Corpoelec", "Cantv", "Movistar", "Digitel", "Hidrocapital"],
        "Posventa":             [],
        "Suscripciones":        ["Apple", "Canva", "Shopify", "Claude", "ChatGPT", "Meta", "Tina (KAI)"],
        "Publicidad y marketing": ["Meta", "iVirtual"],
        "Comisiones":           ["Cashea", "Banco"],
        "Impuestos y legal":    ["SENIAT", "Contador", "Miguel"],
        "Vehículo":             [],
        "Otros gastos":         [],
    }
    # lo que Cristina escribió alguna vez queda como opción para la próxima
    for r in con.execute("""SELECT DISTINCT categoria, proveedor FROM gastos WHERE categoria IS NOT NULL AND TRIM(COALESCE(proveedor,''))!=''
                            UNION SELECT DISTINCT categoria, notas FROM movimientos WHERE 0"""):
        A_QUIEN.setdefault(r["categoria"], []).append(r["proveedor"])
    # en una entrada la pregunta es de quién viene la plata
    A_QUIEN_ENT = {
        "Ventas":      [],
        "Cobros":      [],
        "Inversiones": ["Binance", "Folionet", "Cooper Startup", "Polymarket"],
        "Ajustes":     [],
        "Otro":        ["Cristina"],
    }
    for r in con.execute("""SELECT DISTINCT categoria, notas FROM movimientos WHERE tipo='entrada' AND categoria IS NOT NULL AND TRIM(COALESCE(notas,''))!=''"""):
        A_QUIEN_ENT.setdefault(r["categoria"], []).append(r["notas"])
    A_QUIEN_ENT = {k: sorted(set(v)) for k, v in A_QUIEN_ENT.items()}
    A_QUIEN = {k: sorted(set(v)) for k, v in A_QUIEN.items()}
    # qué subcategoría se le compra a quién, para rellenarlo solo (Grama → Yovanny)
    DE_QUIEN = {"Grama": "Yovanny Sánchez", "Bowls y platos": "Starry Lee", "Placas de bambú": "Starry Lee",
                "Cinta antideslizante": "Starry Lee", "Cajas de cartón": "Cartónica", "Tela de rampa": "El Castillo",
                "Grabado láser": "Robert", "Cajas de madera": "Walter", "Barnizado": "Walter", "Comedores": "Walter",
                "Rampas": "Walter", "El Bar": "Walter", "Slow Chow": "Walter", "Muestras y prototipos": "David", "Almuerzos": "", "Uniformes": "",
                "Pega amarilla": "Ferretería", "Bolsas": "Ferretería", "Cintas": "Ferretería",
                "Meta Ads": "Meta", "Agencia iVirtual": "iVirtual", "Tina (KAI)": "Tina (KAI)", "Verificado de Instagram": "Meta",
                "Alquiler Ingrid": "Ingrid", "Alquiler Víctor": "Víctor", "Luz": "Corpoelec", "Agua": "Hidrocapital",
                "Teléfono Decopet": "Movistar", "Teléfono Cristina": "Movistar"}
    provs = [dict(r) for r in con.execute("SELECT id, nombre FROM proveedores ORDER BY nombre")]
    detalle = detalle_cajas(con)
    return render(request, "cashflow.html", seccion="cashflow", q=q, cuentas=cs, activas=activas, detalle=detalle,
                  efectivo_pend=efectivo_por_registrar(con),
                  lineas=lineas[:300], caja=caja, mes=mes, meses=meses, total=total, arcos=arcos, TIPOS_MOV=TIPOS_MOV,
                  cats=cats, cats_ent=cats_ent, provs=provs, a_quien=A_QUIEN, a_quien_ent=A_QUIEN_ENT, de_quien=DE_QUIEN, orden_ent=[k for k in ORDEN_ENT if k in cats_ent])


@app.post("/cashflow/linea")
async def cashflow_linea(request: Request, con=Depends(db)):
    """Una línea del libro. Todo se registra en dólares: la caja es solo la forma de pago."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    f = await request.form(); uid = uid_de(request)
    fecha = f.get("fecha") or datetime.date.today().isoformat()
    if not (f.get("caja_id") or "").isdigit(): return RedirectResponse("/cashflow", status_code=303)   # sin caja no se registra
    caja = int(f["caja_id"]); monto = cifra(f.get("monto"))
    if monto <= 0: return RedirectResponse("/cashflow", status_code=303)
    g = lambda k: (f.get(k) or "").strip() or None
    categoria, subcategoria = g("categoria") or "Otros gastos", g("subcategoria")
    # el concepto del libro se arma solo con lo que ella eligió: nunca queda una línea muda
    concepto = " · ".join(x for x in ((categoria if f.get("modo") == "entrada" else (subcategoria or categoria)), g("descripcion")) if x)
    if f.get("cantidad") and cifra(f.get("cantidad")):
        concepto += f" · {cifra(f.get('cantidad')):g} {g('unidad') or ''}".rstrip()

    if f.get("modo") == "swap":   # la plata cambia de caja: no es gasto ni ingreso
        destino = int(f["destino"]) if f.get("destino") else None
        if not destino or destino == caja: return RedirectResponse("/cashflow", status_code=303)
        nota = g("descripcion_swap")
        con.execute("""INSERT INTO movimientos (fecha, tipo, cuenta_origen_id, cuenta_destino_id, monto_usd, monto_real, moneda, concepto, notas, usuario_id)
                       VALUES (?,'transferencia',?,?,?,?,'USD',?,?,?)""",
                    (fecha, caja, destino, monto, monto, nota or "Swap entre cajas", nota, uid))
    elif f.get("modo") == "entrada":
        con.execute("""INSERT INTO movimientos (fecha, tipo, cuenta_destino_id, monto_usd, monto_real, moneda, concepto, categoria, subcategoria, comprobante, notas, usuario_id)
                       VALUES (?,'entrada',?,?,?,'USD',?,?,?,?,?,?)""",
                    (fecha, caja, monto, monto, concepto, categoria, subcategoria, g("comprobante"), g("notas"), uid))
    elif categoria.lower().startswith("ajuste"):   # un ajuste que baja la caja NO es un gasto: no va a Gastos ni a Resultados
        con.execute("""INSERT INTO movimientos (fecha, tipo, cuenta_origen_id, monto_usd, monto_real, moneda, concepto, categoria, subcategoria, comprobante, notas, usuario_id)
                       VALUES (?,'ajuste',?,?,?,'USD',?,?,?,?,?,?)""",
                    (fecha, caja, monto, monto, " · ".join(x for x in ("Ajuste", subcategoria, g("descripcion")) if x), categoria, subcategoria, g("comprobante"), g("notas"), uid))
    else:
        grande = 1 if f.get("compra_grande") else 0
        con.execute("""INSERT INTO gastos (fecha, monto_usd, monto_real, moneda, categoria, subcategoria, descripcion, proveedor,
                       cantidad, unidad, comprobante, notas, cuenta_id, compra_grande, usuario_id)
                       VALUES (?,?,?,'USD',?,?,?,?,?,?,?,?,?,?,?)""",
                    (fecha, monto, monto, categoria, subcategoria, g("descripcion"), g("proveedor"),
                     cifra(f.get("cantidad")) or None, g("unidad"), g("comprobante"), g("notas"), caja, grande, uid))
    con.commit(); return RedirectResponse("/cashflow", status_code=303)


def detalle_cajas(con):
    """Por cada caja con detalle: quién debe, por qué y cuánto. Es solo una nota: el saldo de la caja no cambia."""
    out = {}
    for r in con.execute("""SELECT d.* FROM caja_detalle d JOIN cuentas c ON c.id=d.cuenta_id WHERE c.con_detalle=1
                            ORDER BY d.cuenta_id, d.orden, d.id"""):
        out.setdefault(r["cuenta_id"], []).append(r)
    return out


@app.post("/cashflow/detalle")
def cashflow_detalle(request: Request, cuenta_id: int = Form(...), id: str = Form(""), responsable: str = Form(""),
                     concepto: str = Form(""), monto: str = Form(""), borrar: str = Form(""), con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/cashflow", status_code=303)
    if id.isdigit() and borrar: con.execute("DELETE FROM caja_detalle WHERE id=?", (int(id),))
    elif responsable.strip() or concepto.strip():
        m = cifra(monto) or 0
        if id.isdigit(): con.execute("UPDATE caja_detalle SET responsable=?, concepto=?, monto=? WHERE id=?", (responsable.strip(), concepto.strip(), m, int(id)))
        else: con.execute("INSERT INTO caja_detalle (cuenta_id, responsable, concepto, monto) VALUES (?,?,?,?)", (cuenta_id, responsable.strip(), concepto.strip(), m))
    con.commit(); return RedirectResponse("/cashflow/cajas#detalle", status_code=303)


@app.get("/cashflow/cajas", response_class=HTMLResponse)
def cajas_config(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    return render(request, "cajas.html", seccion="cashflow", cuentas=saldos(con), detalle=detalle_cajas(con))


def graficos_cashflow(con, cs, dias=60):
    """Datos para los gráficos: saldo operativo día a día, entradas/salidas por semana, saldo por caja."""
    hoy = datetime.date.today(); ini = hoy - datetime.timedelta(days=dias - 1)
    lineas = list(reversed(libro_caja(con)))  # cronológico
    saldo_ini = sum(c["saldo_inicial"] for c in cs if c["tipo"] == "operativa")
    serie = []; saldo = saldo_ini; idx = 0
    por_dia = {}
    for l in lineas:
        if l["caja"] and l["caja"]["tipo"] == "operativa":
            d = por_dia.setdefault(l["fecha"], [0.0, 0.0]); d[0] += l["entrada"]; d[1] += l["salida"]
    acum = saldo_ini
    for k in sorted(por_dia):
        if k < ini.isoformat(): acum += por_dia[k][0] - por_dia[k][1]
    for i in range(dias):
        d = (ini + datetime.timedelta(days=i)).isoformat()
        e, s = por_dia.get(d, (0.0, 0.0)); acum += e - s
        serie.append((d, round(acum, 2), e, s))
    semanas = {}
    for d, _, e, s in serie:
        w = datetime.date.fromisoformat(d); lun = (w - datetime.timedelta(days=w.weekday())).isoformat()
        x = semanas.setdefault(lun, [0.0, 0.0]); x[0] += e; x[1] += s
    cajas = sorted([c for c in cs if c["tipo"] == "operativa" and (c["saldo"] or c["activa"])], key=lambda c: (c["codigo"] or "999"))
    return {"serie": serie, "semanas": sorted(semanas.items()), "cajas": cajas, "max_caja": max([abs(c["saldo"]) for c in cajas] + [1])}


def ventas_automaticas(con):
    """Si está apagado, Cristina registra las ventas a mano en Cash flow y el ERP no las mete solo."""
    r = con.execute("SELECT valor FROM config WHERE clave='ventas_auto'").fetchone()
    return not r or r[0] == "1"


def libro_caja(con, caja_id=None, mes=None):
    """Todas las líneas del libro (ventas, pagos, swaps, ajustes) con saldo acumulado, como el cash flow de Cristina."""
    cajas = {c["nombre"]: c for c in con.execute("SELECT * FROM cuentas")}
    por_id = {c["id"]: c for c in cajas.values()}
    lineas = []
    desde = (con.execute("SELECT valor FROM config WHERE clave='cashflow_desde'").fetchone() or [None])[0]
    if ventas_automaticas(con):
        for pa in con.execute("""SELECT pa.*, o.numero, o.id oid, c.nombre cliente FROM pagos pa JOIN ordenes o ON o.id=pa.orden_id JOIN clientes c ON c.id=o.cliente_id
                                 WHERE pa.estado='confirmado' AND o.origen_excel=0 AND ?1 IS NOT NULL AND substr(COALESCE(pa.confirmado_en,pa.fecha),1,10) >= ?1""", (desde,)):
            cj = cajas.get(pa["cuenta"])
            lineas.append(dict(fecha=(pa["confirmado_en"] or pa["fecha"])[:10], cuando=(pa["confirmado_en"] or pa["fecha"] or ""), concepto="Venta",
                               detalle=f"{pa['numero']} · {pa['cliente']} · {pa['forma']}", caja=cj,
                               entrada=pa["monto_usd"], salida=0, ref=("orden", pa["oid"]), moneda=pa["moneda"], real=pa["monto_real"]))
    for g in con.execute("SELECT * FROM gastos"):
        lineas.append(dict(fecha=g["fecha"], cuando=g["creado_en"] or "", concepto=g["descripcion"] or "Pago",
                           detalle=f"Gasto · {g['categoria']}" + (f" · {g['proveedor']}" if g["proveedor"] else ""),
                           caja=por_id.get(g["cuenta_id"]),   # un gasto en negativo es plata que vuelve (una devolución)
                           entrada=max(-(g["monto_usd"] or 0), 0), salida=max(g["monto_usd"] or 0, 0), ref=("gasto", g["id"]),
                           moneda=g["moneda"], real=g["monto_real"]))
    for m in con.execute("SELECT * FROM movimientos"):
        nombre = (m["concepto"] or CONCEPTO.get(m["tipo"], m["tipo"])).split(" · ")[0]; det = m["concepto"] or nombre
        if m["tipo"] == "transferencia":   # en un swap el detalle útil es de qué caja a qué caja
            o, d = por_id.get(m["cuenta_origen_id"]), por_id.get(m["cuenta_destino_id"])
            nombre = "Swap · " + (m["notas"] or "entre cajas")
            det = f"{o['nombre'] if o else '—'} → {d['nombre'] if d else '—'}"
        if m["cuenta_origen_id"]: lineas.append(dict(fecha=m["fecha"], cuando=m["creado_en"] or "", concepto=nombre, detalle=det, caja=por_id.get(m["cuenta_origen_id"]), entrada=0, salida=m["monto_usd"], ref=("mov", m["id"]), moneda=m["moneda"], real=m["monto_real"]))
        if m["cuenta_destino_id"]: lineas.append(dict(fecha=m["fecha"], cuando=m["creado_en"] or "", concepto=nombre, detalle=det, caja=por_id.get(m["cuenta_destino_id"]), entrada=m["monto_usd"], salida=0, ref=("mov", m["id"]), moneda=m["moneda"], real=m["monto_real"]))
    lineas.sort(key=lambda l: (l["fecha"], l.get("cuando") or "", l["ref"][1]))
    saldo = sum(c["saldo_inicial"] for c in cajas.values() if c["activa"])
    for l in lineas:
        if l["caja"]: saldo += l["entrada"] - l["salida"]
        l["saldo"] = round(saldo, 2)
    if caja_id: lineas = [l for l in lineas if l["caja"] and l["caja"]["id"] == caja_id]
    if mes: lineas = [l for l in lineas if l["fecha"][:7] == mes]
    return list(reversed(lineas))


@app.post("/cashflow/cuentas")
async def cuentas_guardar(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    f = await request.form()
    for k, v in f.items():
        if k.startswith("saldo_"): con.execute("UPDATE cuentas SET saldo_inicial=? WHERE id=?", (cifra(v), int(k[6:])))   # acepta coma o punto
        elif k.startswith("nombre_") and v.strip():
            cid = int(k[7:]); viejo = con.execute("SELECT nombre FROM cuentas WHERE id=?", (cid,)).fetchone()[0]
            if viejo != v.strip():
                con.execute("UPDATE cuentas SET nombre=? WHERE id=?", (v.strip(), cid))
                con.execute("UPDATE pagos SET cuenta=? WHERE cuenta=?", (v.strip(), viejo))  # los pagos guardan la caja por nombre
        elif k.startswith("codigo_"): con.execute("UPDATE cuentas SET codigo=?, orden=? WHERE id=?", (v.strip(), int(v) if v.strip().isdigit() else 999, int(k[7:])))
        elif k.startswith("tipo_"): con.execute("UPDATE cuentas SET tipo=? WHERE id=?", (v, int(k[5:])))
        elif k.startswith("moneda_"): con.execute("UPDATE cuentas SET moneda=? WHERE id=?", (v, int(k[7:])))
        elif k.startswith("personal_"): con.execute("UPDATE cuentas SET personal=? WHERE id=?", (1 if v == "1" else 0, int(k[9:])))
        elif k.startswith("cobra_"): con.execute("UPDATE cuentas SET cobra=? WHERE id=?", (1 if v == "1" else 0, int(k[6:])))
    if f.get("fecha_corte"): con.execute("UPDATE cuentas SET fecha_corte=?", (f["fecha_corte"],))
    if f.get("nueva_cuenta"):
        sig = con.execute("SELECT MAX(CAST(codigo AS INTEGER)) FROM cuentas").fetchone()[0] or 0
        con.execute("INSERT OR IGNORE INTO cuentas (codigo, nombre, moneda, tipo, orden) VALUES (?,?,?,?,?)", (f"{sig + 1:03d}", f["nueva_cuenta"].strip(), f.get("nueva_moneda") or "USD", f.get("nuevo_tipo") or "operativa", sig + 1))
    for k, v in f.items():
        if k.startswith("activa_"): con.execute("UPDATE cuentas SET activa=? WHERE id=?", (1 if v == "1" else 0, int(k[7:])))
    con.commit(); cargar_formas_pago()
    return RedirectResponse("/cashflow", status_code=303)


def finanzas_desde(con):
    """Fecha desde la que Finanzas y Cash flow toman en cuenta las órdenes de la plataforma. None = todavía nada (Cristina avisa cuándo)."""
    r = con.execute("SELECT valor FROM config WHERE clave='cashflow_desde'").fetchone()
    return r[0] if r and r[0] else None


def resultados_abierto(request: Request, con):
    """Resultados pide clave: es lo más sensible del ERP y el selector de rol no es una puerta de verdad."""
    r = con.execute("SELECT valor FROM config WHERE clave='clave_resultados'").fetchone()
    return not (r and r[0]) or request.cookies.get("res_ok") == (r[0] if r else "")


EMPRESA_CAMPOS = [("razon_social", "Razón social"), ("rif", "RIF"),
                  ("correo", "Correo de la empresa"), ("telefono", "Teléfono de la empresa"),
                  ("direccion_fiscal", "Domicilio fiscal"), ("registro", "Registro mercantil"),
                  ("constitucion", "Fecha de constitución"),
                  ("contador", "Contador"), ("contador_tel", "Teléfono del contador")]


@app.get("/configuracion", response_class=HTMLResponse)
def configuracion(request: Request, con=Depends(db), ok: str = "", err: str = ""):
    """Todo lo que Cristina puede cambiar sin pedirlo: reglas, datos de la empresa y el respaldo.
    Las personas, sus accesos y sus sueldos están en Equipo."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    def val(k, d=""):
        r = con.execute("SELECT valor FROM config WHERE clave=?", (k,)).fetchone()
        return r[0] if r and r[0] is not None else d
    return render(request, "configuracion.html", seccion="configuracion",
                  empresa=cfg_json(con, "empresa", {}) or {}, EMPRESA_CAMPOS=EMPRESA_CAMPOS,
                  documentos=cfg_json(con, "documentos", []) or [],
                  cats=cfg_json(con, "categorias_gasto", {}) or {},
                  cats_uso={f"{r[0]}|{r[1] or ''}": r[2] for r in con.execute(
                      "SELECT categoria, subcategoria, COUNT(*) FROM gastos GROUP BY categoria, subcategoria")},
                  cats_uso_cat={r[0]: r[1] for r in con.execute(
                      "SELECT categoria, COUNT(*) FROM gastos GROUP BY categoria")},
                  ciclo=CICLO_REPUESTO, iva=round(IVA * 100, 2),
                  clave=val("clave_resultados"), ventas_auto=val("ventas_auto", "0") == "1",
                  cashflow_desde=val("cashflow_desde"),
                  respaldos=lista_respaldos(), ok=ok, err=err)


def carpetas_respaldo():
    """Dónde se dejan las copias. En la Mac, las nubes que estén instaladas.
    En un servidor, la carpeta que diga DECOPET_RESPALDOS."""
    fijo = os.environ.get("DECOPET_RESPALDOS")
    if fijo:
        p_ = Path(fijo); p_.mkdir(parents=True, exist_ok=True); return [p_]
    nube = Path(os.path.expanduser("~/Library/CloudStorage"))
    return [c for c in sorted(nube.glob("*/Decopet respaldos")) if c.is_dir()] if nube.is_dir() else []


def lista_respaldos():
    """Cuántas copias hay y cuándo fue la última."""
    for carpeta in carpetas_respaldo():
        f = sorted(carpeta.glob("decopet-2*.db"), key=lambda x: x.stat().st_mtime, reverse=True)
        if f:
            t = datetime.datetime.fromtimestamp(f[0].stat().st_mtime)
            return {"n": len(f), "ultimo": t.strftime("%d/%m/%Y %H:%M"), "ts": t,
                    "dias": (datetime.date.today() - t.date()).days, "carpeta": str(carpeta)}
        return {"n": 0, "ultimo": None, "dias": None, "carpeta": str(carpeta)}
    return {"n": 0, "ultimo": None, "dias": None, "carpeta": None}


DOCS = DOCS_DIR
TIPOS_DOC = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


@app.post("/configuracion/documento")
async def documento_subir(request: Request, con=Depends(db)):
    """Guarda un documento de la empresa (RIF, acta constitutiva, patente) dentro del ERP."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    f = await request.form(); a = f.get("archivo")
    if not (a and getattr(a, "filename", None)): return RedirectResponse("/configuracion?err=doc", status_code=303)
    ext = (a.filename.rsplit(".", 1)[-1] if "." in a.filename else "").lower()[:5]
    if ext not in TIPOS_DOC: return RedirectResponse("/configuracion?err=tipo", status_code=303)
    datos = await a.read()
    if len(datos) > 20 * 1024 * 1024: return RedirectResponse("/configuracion?err=grande", status_code=303)
    DOCS.mkdir(parents=True, exist_ok=True)
    # el nombre del archivo lo pone el ERP: los nombres con acentos o espacios dan problemas.
    # Lleva un trozo al azar porque dos archivos subidos en el mismo segundo se pisaban.
    nombre = f"doc-{datetime.datetime.now():%Y%m%d%H%M%S}-{secrets.token_hex(3)}.{ext}"
    (DOCS / nombre).write_bytes(datos)
    docs = cfg_json(con, "documentos", []) or []
    docs.insert(0, {"archivo": nombre, "etiqueta": (f.get("etiqueta") or a.filename).strip()[:80],
                    "subido": datetime.date.today().isoformat(), "kb": max(1, round(len(datos) / 1024))})
    con.execute("INSERT INTO config (clave, valor) VALUES ('documentos', ?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                (json.dumps(docs, ensure_ascii=False),)); con.commit()
    return RedirectResponse("/configuracion?ok=doc", status_code=303)


@app.get("/configuracion/documento/{nombre}")
def documento_ver(request: Request, nombre: str, con=Depends(db)):
    """Se sirve con clave de rol a propósito: son papeles de la empresa, no fotos de producto."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    ruta = (DOCS / nombre).resolve()
    if not str(ruta).startswith(str(DOCS.resolve())) or not ruta.exists():   # que nadie se salga de la carpeta
        return RedirectResponse("/configuracion", status_code=303)
    d = next((x for x in (cfg_json(con, "documentos", []) or []) if x["archivo"] == nombre), None)
    ext = nombre.rsplit(".", 1)[-1].lower()
    return FileResponse(ruta, media_type=TIPOS_DOC.get(ext, "application/octet-stream"),
                        filename=((d["etiqueta"] if d else nombre).replace("/", "-") + "." + ext))


@app.post("/configuracion/documento/{nombre}/borrar")
def documento_borrar(request: Request, nombre: str, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    docs = [x for x in (cfg_json(con, "documentos", []) or []) if x["archivo"] != nombre]
    con.execute("UPDATE config SET valor=? WHERE clave='documentos'", (json.dumps(docs, ensure_ascii=False),)); con.commit()
    ruta = (DOCS / nombre).resolve()
    if str(ruta).startswith(str(DOCS.resolve())) and ruta.exists(): ruta.unlink()
    return RedirectResponse("/configuracion?ok=doc", status_code=303)


@app.post("/configuracion/categoria")
def categoria_guardar(request: Request, categoria: str = Form(""), sub: str = Form(""),
                      renombrar: str = Form(""), borrar: str = Form(""), con=Depends(db)):
    """Agregar, renombrar o quitar categorías y subcategorías de gasto.
    Nunca se borra una que esté en uso: se perdería de qué era ese gasto."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    cats = cfg_json(con, "categorias_gasto", {}) or {}
    cat, sb, nuevo_n = categoria.strip(), sub.strip(), renombrar.strip()

    def en_uso(c, s_=None):
        if s_:
            return con.execute("SELECT 1 FROM gastos WHERE categoria=? AND subcategoria=? LIMIT 1", (c, s_)).fetchone() \
                or con.execute("SELECT 1 FROM compromisos WHERE categoria=? AND subcategoria=? LIMIT 1", (c, s_)).fetchone()
        return con.execute("SELECT 1 FROM gastos WHERE categoria=? LIMIT 1", (c,)).fetchone() \
            or con.execute("SELECT 1 FROM compromisos WHERE categoria=? LIMIT 1", (c,)).fetchone()

    if not cat: return RedirectResponse("/configuracion?err=cat", status_code=303)
    if borrar:
        if en_uso(cat, sb or None): return RedirectResponse("/configuracion?err=enuso", status_code=303)
        if sb: cats[cat] = [x for x in cats.get(cat, []) if x != sb]
        else: cats.pop(cat, None)
    elif nuevo_n:
        if sb:                                        # renombrar una subcategoría
            cats[cat] = [nuevo_n if x == sb else x for x in cats.get(cat, [])]
            con.execute("UPDATE gastos SET subcategoria=? WHERE categoria=? AND subcategoria=?", (nuevo_n, cat, sb))
            con.execute("UPDATE compromisos SET subcategoria=? WHERE categoria=? AND subcategoria=?", (nuevo_n, cat, sb))
        else:                                         # renombrar la categoría entera
            if nuevo_n != cat and cat in cats:
                cats = {(nuevo_n if k == cat else k): v for k, v in cats.items()}
                con.execute("UPDATE gastos SET categoria=? WHERE categoria=?", (nuevo_n, cat))
                con.execute("UPDATE compromisos SET categoria=? WHERE categoria=?", (nuevo_n, cat))
    else:
        cats.setdefault(cat, [])
        if sb and sb not in cats[cat]: cats[cat].append(sb)
    con.execute("INSERT INTO config (clave, valor) VALUES ('categorias_gasto', ?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                (json.dumps(cats, ensure_ascii=False),))
    con.commit(); return RedirectResponse("/configuracion?ok=cat", status_code=303)


@app.post("/configuracion/respaldo")


def configuracion_respaldo(request: Request):
    """Copia la base de datos a iCloud ahora mismo."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    try:
        r = subprocess.run(["/bin/bash", str(BASE.parent / "scripts" / "respaldo.sh")],
                           capture_output=True, text=True, timeout=60)
        return RedirectResponse("/configuracion?" + ("ok=respaldo" if r.returncode == 0 else "err=respaldo"), status_code=303)
    except Exception:
        return RedirectResponse("/configuracion?err=respaldo", status_code=303)


@app.post("/configuracion/guardar")
async def configuracion_guardar(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    f = await request.form()
    def poner(clave, valor):
        con.execute("INSERT INTO config (clave, valor) VALUES (?,?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (clave, valor))

    bloque = f.get("bloque", "")
    if bloque == "reglas":
        c = cifra(f.get("ciclo", "")) or 0
        if 1 <= c <= 365: poner("ciclo_repuesto", str(int(c)))
        i = cifra(f.get("iva", ""))
        if i is not None and 0 <= i <= 100: poner("iva", str(round(i / 100, 4)))
        # el viaje a la agencia se edita en Logística › Tarifas, no aquí
    elif bloque == "empresa":
        poner("empresa", json.dumps({k: (f.get(k) or "").strip() for k, _ in EMPRESA_CAMPOS}, ensure_ascii=False))
    elif bloque == "erp":
        poner("ventas_auto", "1" if f.get("ventas_auto") else "0")
        d = (f.get("cashflow_desde") or "").strip()
        if d: poner("cashflow_desde", d)
        nueva = (f.get("clave") or "").strip()
        if nueva: poner("clave_resultados", nueva)
    con.commit(); cargar_ajustes()
    return RedirectResponse("/configuracion?ok=" + (bloque or "1"), status_code=303)


@app.post("/clave/verificar")


@app.post("/clave/verificar")
def clave_verificar(clave: str = Form(""), con=Depends(db)):
    """Comprueba la clave sin que salga del servidor: el navegador solo recibe sí o no.
    La usa el ojo para volver a mostrar los montos: esconder es libre, mostrar pide clave."""
    r = con.execute("SELECT valor FROM config WHERE clave='clave_resultados'").fetchone()
    return JSONResponse({"ok": not (r and r[0]) or clave.strip() == r[0]})


@app.post("/finanzas/entrar")
def finanzas_entrar(request: Request, clave: str = Form(""), volver: str = Form("/finanzas"), con=Depends(db)):
    r = con.execute("SELECT valor FROM config WHERE clave='clave_resultados'").fetchone()
    destino = volver if volver in ("/finanzas", "/historial") else "/finanzas"
    resp = RedirectResponse(destino + ("" if (r and clave.strip() == r[0]) else "?mal=1"), status_code=303)
    if r and clave.strip() == r[0]:
        resp.set_cookie("res_ok", r[0], max_age=30 * 60, samesite="lax")   # y además se cierra al salir de la sección
    return resp


@app.get("/finanzas/salir")
def finanzas_salir():
    resp = RedirectResponse("/inicio", status_code=303); resp.delete_cookie("res_ok"); return resp


def resultados_auto(con):
    """Apagado: Resultados muestra solo los meses que Cristina carga a mano, hasta que confíe en que el ERP los calcule solo."""
    r = con.execute("SELECT valor FROM config WHERE clave='resultados_auto'").fetchone()
    return bool(r and r[0] == "1")


def _resultados_meses(con, anio):
    """Las filas de Resultados. Antes de la fecha de arranque manda el historial del Excel; desde ahí lo calcula el ERP."""
    desde = finanzas_desde(con)   # Finanzas solo mira órdenes desde la fecha que Cristina active; antes, el historial
    # una venta cuenta el día que entró la plata (fecha_pago); si todavía no han pagado, el día que se hizo la orden
    DIA_VENTA = "substr(COALESCE(NULLIF(o.fecha_pago,''), o.creado_en),1,10)"
    # lo que se cobró después de la compra (el delivery de un retiro, por ejemplo) cuenta en el mes en que entró
    EXTRAS = "COALESCE((SELECT SUM(l.total) FROM orden_lineas l WHERE l.orden_id=o.id AND l.extra_en IS NOT NULL),0)"
    vivo = [dict(r) for r in con.execute(f"""SELECT substr({DIA_VENTA},1,7) m, SUM(o.total - {EXTRAS}) facturacion, COUNT(*) n
                           FROM ordenes o WHERE o.estado!='cancelada' AND o.origen_excel=0
                           AND ? IS NOT NULL AND {DIA_VENTA}>=? AND substr({DIA_VENTA},1,4)=? GROUP BY 1""", (desde, desde, anio))]
    for m_, x_ in con.execute("""SELECT substr(l.extra_en,1,7), SUM(l.total) FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id
                                 WHERE l.extra_en IS NOT NULL AND o.estado!='cancelada' AND o.origen_excel=0
                                 AND ? IS NOT NULL AND l.extra_en>=? AND substr(l.extra_en,1,4)=? GROUP BY 1""", (desde, desde, anio)):
        fila = next((v_ for v_ in vivo if v_["m"] == m_), None)
        if fila: fila["facturacion"] = (fila["facturacion"] or 0) + x_
        else: vivo.append({"m": m_, "facturacion": x_, "n": 0})
    unidades = {r[0]: r[1] for r in con.execute(f"""SELECT substr({DIA_VENTA},1,7), SUM(l.cantidad) FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id
                                                    WHERE o.estado!='cancelada' AND o.origen_excel=0
                                                    AND ? IS NOT NULL AND {DIA_VENTA}>=? AND substr({DIA_VENTA},1,4)=? GROUP BY 1""", (desde, desde, anio))}
    # el sueldo de Cristina va en su propia columna, así que NO se cuenta dentro de Gastos (si no, se restaría dos veces)
    SUELDO = "categoria='Sueldo Cristina'"
    ACTIVO = "? IS NOT NULL AND fecha>=?"   # antes de la fecha de arranque manda el historial, no la plataforma
    gastos_m = {r[0]: r[1] for r in con.execute(f"SELECT substr(fecha,1,7), SUM(monto_usd) FROM gastos WHERE NOT ({SUELDO}) AND {ACTIVO} AND substr(fecha,1,4)=? GROUP BY 1", (desde, desde, anio))}
    sueldos = {r[0]: r[1] for r in con.execute(f"SELECT substr(fecha,1,7), SUM(monto_usd) FROM gastos WHERE {SUELDO} AND {ACTIVO} AND substr(fecha,1,4)=? GROUP BY 1", (desde, desde, anio))}
    grandes = {r[0]: r[1] for r in con.execute(f"SELECT substr(fecha,1,7), SUM(monto_usd) FROM gastos WHERE compra_grande=1 AND {ACTIVO} AND substr(fecha,1,4)=? GROUP BY 1", (desde, desde, anio))}
    vivos = {r["m"]: dict(r) for r in vivo}
    for m in set(gastos_m) | set(sueldos):
        if m.startswith(anio): vivos.setdefault(m, {"m": m, "facturacion": 0, "n": 0})
    hist = {r["mes"]: dict(r) for r in con.execute("SELECT * FROM resultados_mes WHERE substr(mes,1,4)=? ORDER BY mes", (anio,))}
    auto = resultados_auto(con)
    meses = []
    for m in sorted(set(vivos) | set(hist)):
        h, v = hist.get(m), vivos.get(m)
        # un mes cargado a mano siempre manda; el cálculo del ERP solo entra si Cristina lo prende (resultados_auto)
        if h:
            meses.append({"m": m, "facturacion": h["facturacion"] or 0, "unidades": h["unidades"] or 0, "gastos": h["gastos"] or 0,
                          "sueldo": h["sueldo"] or 0, "grandes": h["arrastre"] or 0, "n": 0, "historial": True,
                          "nota": h["nota"] or "", "contexto": h["contexto"] or ""})
        elif v and auto:
            meses.append({"m": m, "facturacion": v.get("facturacion") or 0, "unidades": unidades.get(m, 0), "gastos": gastos_m.get(m, 0),
                          "sueldo": sueldos.get(m, 0), "grandes": grandes.get(m, 0), "n": v.get("n") or 0, "historial": False, "nota": "", "contexto": ""})
    for m in meses:
        m["ganancia"] = round(m["facturacion"] - m["gastos"], 2); m["entrada"] = round(m["ganancia"] - m["sueldo"], 2)
        m["ganancia_comp"] = round(m["ganancia"] + (m["grandes"] or 0), 2)   # sin los gastos que no son de ese mes
    # cada mes contra el anterior: solo el porcentaje, para que se lea aunque los montos estén ocultos
    for i, m in enumerate(meses):
        ant = meses[i - 1] if i else None
        for campo in ("facturacion", "unidades", "entrada"):
            v, va = m[campo], (ant[campo] if ant else None)
            m["d_" + campo] = round((v - va) / abs(va) * 100) if (va not in (None, 0)) else None
        m["mes_ant"] = MESES_N[int(ant["m"][5:]) - 1][:3] if ant else None
    return meses


@app.get("/finanzas", response_class=HTMLResponse)
def finanzas(request: Request, anio: str = "", mal: str = "", con=Depends(db)):
    """Resultados mes a mes, en el método de Cristina: Ganancia = Facturación − Gastos (antes de su sueldo); Entrada = Ganancia − Sueldo.
    Los meses anteriores a cashflow_desde salen del historial cargado del Excel; desde esa fecha, la plataforma los llena sola."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    if not resultados_abierto(request, con): return render(request, "clave.html", seccion="finanzas", mal=mal)
    anio = anio or str(datetime.date.today().year)
    desde = finanzas_desde(con)
    meses = _resultados_meses(con, anio)
    anios = sorted({r[0] for r in con.execute("SELECT DISTINCT substr(creado_en,1,4) FROM ordenes UNION SELECT DISTINCT substr(fecha,1,4) FROM gastos UNION SELECT DISTINCT substr(mes,1,4) FROM resultados_mes")} | {anio}, reverse=True)
    por_cat = con.execute("SELECT categoria, SUM(monto_usd) monto FROM gastos WHERE substr(fecha,1,4)=? GROUP BY 1 ORDER BY 2 DESC", (anio,)).fetchall()
    historial = con.execute("SELECT * FROM resultados_mes WHERE substr(mes,1,4)=? ORDER BY mes", (anio,)).fetchall()
    return render(request, "finanzas.html", seccion="finanzas", meses=meses, anio=anio, anios=anios, por_cat=por_cat, historial=historial, cashflow_desde=desde, auto=resultados_auto(con), MESES_N=MESES_N)


@app.get("/finanzas/exportar")
def resultados_exportar(request: Request, anio: str = "", con=Depends(db)):
    """Resultados mes a mes, en el método de Cristina."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    if not resultados_abierto(request, con): return RedirectResponse("/finanzas", status_code=303)
    anio = anio or str(datetime.date.today().year)
    meses = _resultados_meses(con, anio)
    cols = [("Mes", 14, ""), ("Facturación", 14, "$"), ("Unidades", 11, "n"), ("Gastos", 13, "$"),
            ("Ganancia", 13, "$"), ("Sueldo Cristina", 14, "$"), ("Entrada", 13, "$"), ("Margen", 10, "%"), ("Origen", 12, "")]
    filas = [(MESES_N[int(m["m"][5:]) - 1].capitalize(), m["facturacion"], m["unidades"],
              m["gastos"], m["ganancia"], m["sueldo"], m["entrada"],
              round(m["ganancia"] / m["facturacion"] * 100) if m["facturacion"] else 0,
              "Excel" if m["historial"] else "ERP") for m in meses]
    tf = sum(m["facturacion"] for m in meses); tg = sum(m["gastos"] for m in meses); ts = sum(m["sueldo"] for m in meses)
    filas.append((f"Total {anio}", tf, sum(m["unidades"] for m in meses), tg, tf - tg, ts, tf - tg - ts,
                  round((tf - tg) / tf * 100) if tf else 0, ""))
    return hoja_excel([(f"Resultados {anio}", cols, filas, True)], f"Decopet resultados {anio}.xlsx", "Resultados")


@app.post("/finanzas/mes")
def finanzas_mes(request: Request, mes: str = Form(""), facturacion: str = Form("0"), unidades: str = Form("0"),
                 gastos: str = Form("0"), sueldo: str = Form("0"), arrastre: str = Form("0"), contexto: str = Form(""),
                 borrar: str = Form(""), con=Depends(db)):
    """Historial mensual que Cristina trae de su Excel. Solo se guardan los cuatro números que ella lleva."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    mes = (mes or "").strip()[:7]
    if len(mes) != 7: return RedirectResponse("/finanzas", status_code=303)
    if borrar:
        con.execute("DELETE FROM resultados_mes WHERE mes=?", (mes,))
    else:
        con.execute("""INSERT INTO resultados_mes (mes,facturacion,unidades,gastos,sueldo,arrastre,contexto,nota) VALUES (?,?,?,?,?,?,?,'cargado a mano')
                       ON CONFLICT(mes) DO UPDATE SET facturacion=excluded.facturacion, unidades=excluded.unidades, gastos=excluded.gastos,
                       sueldo=excluded.sueldo, arrastre=excluded.arrastre, contexto=excluded.contexto""",
                    (mes, cifra(facturacion), int(cifra(unidades)), cifra(gastos), cifra(sueldo), cifra(arrastre), (contexto or "").strip() or None))
    con.commit(); return RedirectResponse(f"/finanzas?anio={mes[:4]}", status_code=303)


@app.get("/finanzas/cashea", response_class=HTMLResponse)
def finanzas_cashea(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    return render(request, "pendiente.html", seccion="cashea", titulo="Cashea")


@app.post("/finanzas/cashea/{oid}/cuota")
def cashea_cuota(request: Request, oid: int, monto: float = Form(...), fecha: str = Form(""), referencia: str = Form(""), con=Depends(db)):
    """Registra una cuota liquidada por Cashea (entra por BNC)."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    f = fecha or datetime.date.today().isoformat(); tasa = tasa_hoy(con)["valor"]
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,tasa,cuenta,referencia,fecha,estado,confirmado_por,confirmado_en) VALUES (?,?,?,?,?,?,?,?,?,'confirmado',?,?)",
                (oid, "BNC", monto, round(monto * (tasa or 0), 2), "VES", tasa, FORMA_CUENTA["BNC"], referencia or "Cuota Cashea", f + " 12:00", uid_de(request), f + " 12:00"))
    o = con.execute("SELECT total, (SELECT COALESCE(SUM(monto_usd),0) FROM pagos p WHERE p.orden_id=ordenes.id AND p.estado='confirmado') cobrado FROM ordenes WHERE id=?", (oid,)).fetchone()
    if o["cobrado"] >= o["total"] - 0.01: con.execute("UPDATE ordenes SET estado_pago='pagada' WHERE id=?", (oid,))
    registrar(con, oid, uid_de(request), "pago", f"Cuota Cashea liquidada {fmt_usd(monto)} → BNC"); con.commit()
    return RedirectResponse("/finanzas/cashea", status_code=303)


def agrupar_pagos_produccion(con, rows):
    """Un pedido a un proveedor se paga en partes (adelanto y pago final) y cada parte es un gasto, porque la plata salió
    en días y cajas distintas (Cash flow las necesita así). En Gastos, Cristina quiere ver el pedido una sola vez:
    "20 × Caja de madera grande · $250". Se juntan las partes de un mismo pedido que caen en el mes que se está viendo."""
    ids = [r["id"] for r in rows]
    if not ids: return rows
    de = {r["gasto_id"]: dict(r) for r in con.execute(f"""SELECT a.gasto_id, p.id pid, p.pieza, p.cantidad, p.recibido, p.costo
                 FROM abonos_produccion a JOIN produccion p ON p.id=a.produccion_id WHERE a.gasto_id IN ({','.join('?' * len(ids))})""", ids)}
    juntos, out = {}, []
    for r in rows:
        p = de.get(r["id"])
        if not p: out.append(r); continue
        if p["pid"] not in juntos:
            cant = int(p["recibido"] or 0) or int(p["cantidad"] or 0)
            g = dict(r, agrupado=True, partes=[], descripcion=f"{cant} unidades", cantidad=cant, unidad=None, notas=None, monto_usd=0.0, monto_real=0.0, moneda="USD", _cajas=[], _costo=p["costo"])
            juntos[p["pid"]] = g; out.append(g)
        g = juntos[p["pid"]]
        g["partes"].append(f"{fmt_fecha(r['fecha'])}: {fmt_usd(r['monto_usd'])}" + (f" ({r['cuenta']})" if r.get("cuenta") else ""))
        g["monto_usd"] = round(g["monto_usd"] + r["monto_usd"], 2); g["monto_real"] = g["monto_usd"]
        g["fecha"] = max(g["fecha"], r["fecha"])
        if r.get("cuenta") and r["cuenta"] not in g["_cajas"]: g["_cajas"].append(r["cuenta"])
    for g in juntos.values():
        g["cuenta"] = " · ".join(g["_cajas"]) or None
        if g["_costo"] and g["monto_usd"] < g["_costo"] - 0.009: g["notas"] = f"falta pagar {fmt_usd(g['_costo'] - g['monto_usd'])}"
    out.sort(key=lambda r: (r["fecha"], r["id"]), reverse=True)
    return out


@app.get("/finanzas/gastos", response_class=HTMLResponse)
def gastos(request: Request, mes: str = "", categoria: str = "", vista: str = "semana", semana: str = "", anio: str = "", q: str = "", con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    hoy_d = datetime.date.today()
    if anio and not mes: mes = f"{anio}-{hoy_d.month:02d}" if anio == str(hoy_d.year) else f"{anio}-01"
    mes = mes or hoy_d.strftime("%Y-%m"); anio = mes[:4]
    tot_mes = {r[0]: r[1] for r in con.execute("SELECT substr(fecha,6,2), SUM(monto_usd) FROM gastos WHERE substr(fecha,1,4)=? GROUP BY 1", (anio,))}
    anios = sorted({r[0] for r in con.execute("SELECT DISTINCT substr(fecha,1,4) FROM gastos")} | {str(hoy_d.year)}, reverse=True)
    sql = "SELECT g.*, cu.nombre cuenta, u.nombre usuario FROM gastos g LEFT JOIN cuentas cu ON cu.id=g.cuenta_id LEFT JOIN usuarios u ON u.id=g.usuario_id WHERE substr(g.fecha,1,7)=?"; args = [mes]
    if categoria: sql += " AND g.categoria=?"; args.append(categoria)
    rows = agrupar_pagos_produccion(con, [dict(r) for r in con.execute(sql + " ORDER BY g.fecha DESC, g.id DESC", args)])
    # semana del mes (1..5) para agrupar como en el Excel de Cristina
    semanas = {}
    for r in rows:
        d = datetime.date.fromisoformat(r["fecha"]); n = (d.day - 1) // 7 + 1
        semanas.setdefault(n, {"n": n, "desde": d.replace(day=(n - 1) * 7 + 1), "gastos": [], "total": 0.0})
        semanas[n]["gastos"].append(r); semanas[n]["total"] += r["monto_usd"]
    tot_semanas = {k: v["total"] for k, v in semanas.items()}
    if semana: semanas = {k: v for k, v in semanas.items() if str(k) == semana}; rows = [r for r in rows if str((datetime.date.fromisoformat(r["fecha"]).day - 1) // 7 + 1) == semana]
    semanas = [semanas[k] for k in sorted(semanas, reverse=True)]
    if q.strip():   # el buscador de arriba: busca en todos los meses por concepto, proveedor, categoría o nota
        like = f"%{q.strip()}%"
        rows = agrupar_pagos_produccion(con, [dict(r) for r in con.execute("""SELECT g.*, cu.nombre cuenta, u.nombre usuario FROM gastos g LEFT JOIN cuentas cu ON cu.id=g.cuenta_id
                    LEFT JOIN usuarios u ON u.id=g.usuario_id WHERE g.descripcion LIKE ? OR g.proveedor LIKE ? OR g.categoria LIKE ? OR g.subcategoria LIKE ? OR g.notas LIKE ?
                    ORDER BY g.fecha DESC, g.id DESC""", (like,) * 5)])
        semanas = [{"n": 0, "desde": None, "gastos": rows, "total": sum(r["monto_usd"] for r in rows)}] if rows else []
    por_cat = con.execute("SELECT categoria, SUM(monto_usd) monto, COUNT(*) n FROM gastos WHERE substr(fecha,1,7)=? GROUP BY 1 ORDER BY 2 DESC", (mes,)).fetchall()
    cats = json.loads(con.execute("SELECT valor FROM config WHERE clave='categorias_gasto'").fetchone()[0])
    cuentas = con.execute("SELECT * FROM cuentas WHERE activa=1 ORDER BY orden").fetchall()
    meses = [r[0] for r in con.execute("SELECT DISTINCT substr(fecha,1,7) FROM gastos ORDER BY 1 DESC")]
    if mes not in meses: meses.insert(0, mes)
    return render(request, "gastos.html", seccion="gastos", q=q, busqueda=q, gastos=rows, semanas=semanas, por_cat=por_cat, total=sum(r["monto"] for r in por_cat), cats=cats, cuentas=cuentas, mes=mes, meses=meses, tasa=tasa_hoy(con), categoria=categoria, vista=vista,
                  semana=semana, anio=anio, anios=anios, tot_mes=tot_mes, tot_semanas=tot_semanas, MESES_N=MESES_N)


@app.post("/finanzas/gastos")
async def gasto_crear(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    f = await request.form(); tasa = tasa_hoy(con)["valor"] or 0
    moneda = f.get("moneda") or "USD"; monto_real = float(f.get("monto") or 0)
    monto_usd = round(monto_real / tasa, 2) if (moneda == "VES" and tasa) else monto_real
    con.execute("""INSERT INTO gastos (fecha, monto_usd, monto_real, moneda, tasa, categoria, subcategoria, descripcion, proveedor, cuenta_id, recurrente, notas, usuario_id, compra_grande)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f.get("fecha") or datetime.date.today().isoformat(), monto_usd, monto_real, moneda, tasa if moneda == "VES" else None, f["categoria"], f.get("subcategoria") or None,
                 f.get("descripcion") or None, f.get("proveedor") or None, int(f["cuenta_id"]) if f.get("cuenta_id") else None, 1 if f.get("recurrente") else 0, f.get("notas") or None, uid_de(request),
                 1 if f.get("compra_grande") else 0))
    con.commit(); return RedirectResponse(f"/finanzas/gastos?mes={(f.get('fecha') or datetime.date.today().isoformat())[:7]}", status_code=303)


@app.post("/finanzas/gastos/{gid}/borrar")
def gasto_borrar(request: Request, gid: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    g = con.execute("SELECT fecha FROM gastos WHERE id=?", (gid,)).fetchone(); con.execute("DELETE FROM gastos WHERE id=?", (gid,)); con.commit()
    return RedirectResponse(f"/finanzas/gastos?mes={g['fecha'][:7] if g else ''}", status_code=303)


@app.get("/revision", response_class=HTMLResponse)
def revision(request: Request, con=Depends(db)):
    """Comprueba que los números del ERP cuadren entre sí. Si algo no cuadra, lo dice."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    ch = []
    def chequeo(nombre, ok, detalle):
        ch.append({"nombre": nombre, "ok": ok, "detalle": detalle})

    # 0 · los productos de cada orden tienen que sumar su subtotal (detecta líneas sueltas o perdidas)
    malas = []
    for o in con.execute("""SELECT o.id, o.numero, o.subtotal,
                            COALESCE((SELECT SUM(l.total) FROM orden_lineas l WHERE l.orden_id=o.id),0) lineas
                            FROM ordenes o WHERE o.estado!='cancelada'"""):
        if abs((o["lineas"] or 0) - (o["subtotal"] or 0)) > 0.01:
            malas.append(f"{o['numero']}: productos {fmt_usd(o['lineas'])} vs subtotal {fmt_usd(o['subtotal'])}")
    n_ord = con.execute("SELECT COUNT(*) FROM ordenes WHERE estado!='cancelada'").fetchone()[0]
    chequeo("Los productos de cada orden suman su total", not malas, "; ".join(malas[:6]) or f"{n_ord} órdenes revisadas")

    # 0b · nada puede quedar apuntando a una orden que ya no existe
    sueltas = []
    for tb, etiqueta in (("orden_lineas", "productos"), ("pagos", "pagos"), ("mov_inventario", "movimientos de inventario"),
                         ("packs", "packs"), ("repuestos_prepagados", "repuestos prepagados")):
        n = con.execute(f"SELECT COUNT(*) FROM {tb} WHERE orden_id IS NOT NULL AND orden_id NOT IN (SELECT id FROM ordenes)").fetchone()[0]
        if n: sueltas.append(f"{n} {etiqueta}")
    chequeo("No hay nada suelto de órdenes borradas", not sueltas, "; ".join(sueltas) or "todo apunta a una orden que existe")

    # 1 · cada caja: saldo = inicial + lo que entró − lo que salió
    desc = []
    for c in saldos(con):
        esperado = round(c["saldo_inicial"] + c["ingresos"] - c["gastos"] + c["entradas"] - c["salidas"], 2)
        if abs(esperado - c["saldo"]) > 0.01: desc.append(f"{c['nombre']}: {fmt_usd(c['saldo'])} vs {fmt_usd(esperado)}")
    chequeo("El saldo de cada caja cuadra con sus movimientos", not desc, "; ".join(desc) or f"{len(saldos(con))} cajas revisadas")
    # lo que se pagó de más tiene que estar como saldo a favor del cliente, no perdido en la orden
    de_mas = [f"{r['numero']} {r['cliente'] or ''}: pagó {fmt_usd(r['sobra'])} de más sin saldo a favor"
              for r in con.execute("""SELECT o.numero, cl.nombre cliente,
                    (SELECT COALESCE(SUM(monto_usd),0) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado') - o.total
                    - (SELECT COALESCE(SUM(monto),0) FROM credito_cliente k WHERE k.orden_id=o.id AND k.monto>0) sobra
                    FROM ordenes o LEFT JOIN clientes cl ON cl.id=o.cliente_id WHERE o.estado!='cancelada'""") if r["sobra"] > 0.009]
    chequeo("Lo pagado de más quedó como saldo a favor", not de_mas, "; ".join(de_mas[:6]) or "ninguna orden cobrada de más sin su saldo a favor")

    # 2 · lo pagado a proveedores debe existir como gasto
    huerf = con.execute("SELECT COUNT(*) FROM abonos_produccion WHERE gasto_id IS NULL").fetchone()[0]
    chequeo("Cada pago a proveedor tiene su gasto", huerf == 0, f"{huerf} pagos sin gasto" if huerf else "todos con gasto")

    # 3 · lo cobrado de más tiene que estar a favor del cliente, no perdido
    mal = [f"#{r['numero']}" for r in con.execute("""SELECT o.numero FROM ordenes o WHERE o.estado!='cancelada'
             AND COALESCE((SELECT SUM(monto_usd) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado'),0)
               > o.total + COALESCE((SELECT SUM(cc.monto) FROM credito_cliente cc WHERE cc.orden_id=o.id AND cc.monto>0),0) + 0.01""")]
    chequeo("Lo cobrado de más quedó a favor del cliente", not mal, ", ".join(mal[:8]) or "todas correctas")

    # 4 · una orden marcada pagada tiene que tener los pagos
    inc = [f"#{r['numero']}" for r in con.execute("""SELECT o.numero FROM ordenes o WHERE o.estado_pago='pagada'
             AND COALESCE((SELECT SUM(monto_usd) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado'),0) < o.total - 0.01""")]
    chequeo("Las órdenes 'pagadas' tienen sus pagos completos", not inc, ", ".join(inc[:8]) or "todas correctas")

    # 5 · una venta anotada a mano además de la que entra sola (solo aplica si el ERP las mete solo)
    dobles = [] if not ventas_automaticas(con) else [dict(r) for r in con.execute("""SELECT m.fecha, m.monto_usd, m.concepto FROM movimientos m
                WHERE m.tipo='entrada' AND m.categoria='Ventas' AND EXISTS (
                  SELECT 1 FROM pagos p JOIN ordenes o ON o.id=p.orden_id
                  WHERE p.estado='confirmado' AND o.origen_excel=0
                  AND substr(COALESCE(p.confirmado_en,p.fecha),1,10)=substr(m.fecha,1,10)
                  AND ABS(p.monto_usd - m.monto_usd) < 0.01)""")]
    chequeo("Ninguna venta está contada dos veces", not dobles,
            "; ".join(f"{d['fecha']} {fmt_usd(d['monto_usd'])} · {d['concepto']}" for d in dobles[:5])
            or ("registras las ventas a mano: el ERP no mete ninguna, así que no puede duplicar" if not ventas_automaticas(con)
                else "las ventas cobradas entran solas desde las órdenes; no hay ninguna repetida a mano"))

    # 6 · una entrega sin fecha rompe el seguimiento: no se sabe desde cuándo contar
    sin_fecha = [f"#{r['numero']}" for r in con.execute("""SELECT numero FROM ordenes
                   WHERE estado='entregada' AND (fecha_entrega IS NULL OR TRIM(fecha_entrega)='') AND origen_excel=0""")]
    chequeo("Cada entrega tiene su fecha", not sin_fecha, ", ".join(sin_fecha[:8]) or "todas la tienen")

    # 7 · un producto en negativo significa que salió algo que no estaba cargado
    neg = [r["nombre"] for r in con.execute("""SELECT p.nombre FROM productos p WHERE p.activo=1
             AND COALESCE((SELECT SUM(m.cantidad) FROM mov_inventario m WHERE m.producto_id=p.id),0) < 0""")]
    chequeo("Ningún producto quedó en negativo", not neg,
            ", ".join(neg[:6]) + " · salió algo que no estaba cargado" if neg else "todo en cero o más")

    # 8 · alguien marcado como activo pero sin clave no puede entrar, aunque parezca que sí
    sin_clave = [r["nombre"] for r in con.execute("""SELECT nombre FROM usuarios
                   WHERE activo=1 AND rol!='sistema' AND (clave_hash IS NULL OR clave_hash='')""")]
    chequeo("Todos los que pueden entrar tienen clave", not sin_clave,
            ", ".join(sin_clave[:6]) + " · no pueden entrar todavía" if sin_clave else "todos con clave")

    # 9 · el respaldo — mismo dato que muestra Configuración, para que no haya dos verdades
    r_ = lista_respaldos()
    if r_["ultimo"]:
        chequeo("Hay un respaldo fuera de la Mac", r_["dias"] <= 2,
                f"último {r_['ultimo']}" + (f" · hace {r_['dias']} días" if r_["dias"] > 0 else " · hoy") + f" · {r_['n']} copias")
    else:
        chequeo("Hay un respaldo fuera de la Mac", False,
                "todo está en el disco de la Mac. Si se daña, se pierde. Respáldalo desde Configuración.")

    n_mal = sum(1 for c in ch if not c["ok"])
    return render(request, "revision.html", seccion="revision", ch=ch, n_mal=n_mal)


def hoja_excel(hojas, nombre, titulo=None):
    """Arma un .xlsx con la identidad de Decopet: logo, verde de la marca, formato de moneda y fila fija."""
    import io, os
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.drawing.image import Image as XLImage
    from fastapi.responses import StreamingResponse

    VERDE, VERDE_OSC, TINTE, TINTA = "FF5A9075", "FF3F6D56", "FFEEF4F0", "FF1C2622"
    LOGO = os.path.join(os.path.dirname(__file__), "static", "logo-excel.png")
    wb = Workbook(); wb.remove(wb.active)

    for hoja in hojas:
        titulo_h, cols, filas = hoja[0], hoja[1], hoja[2]
        ws = wb.create_sheet(titulo_h[:31])
        ws.sheet_view.showGridLines = False
        ancho_total = len(cols)

        # cabecera de marca: logo a la izquierda y el nombre del reporte a la derecha
        if os.path.exists(LOGO):
            img = XLImage(LOGO); img.width, img.height = 150, 60; img.anchor = "A1"
            ws.add_image(img)
        ws.row_dimensions[1].height = 24; ws.row_dimensions[2].height = 24
        ws.merge_cells(start_row=1, start_column=2, end_row=2, end_column=max(ancho_total, 2))
        cab = ws.cell(row=1, column=2, value=(titulo or nombre.replace(".xlsx", "")) + " · " + titulo_h)
        cab.font = Font(bold=True, size=14, color=VERDE_OSC)
        cab.alignment = Alignment(horizontal="right", vertical="center")
        ws.cell(row=3, column=1, value=f"Generado el {datetime.date.today():%d/%m/%Y}").font = Font(size=9, color="FF9AA39F")

        fila_enc = 5
        for j, (titulo_col, _, _) in enumerate(cols, start=1):
            celda = ws.cell(row=fila_enc, column=j, value=titulo_col)
            celda.font = Font(bold=True, color="FFFFFFFF", size=11)
            celda.fill = PatternFill("solid", fgColor=VERDE)
            celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[fila_enc].height = 24

        for fila in filas:
            ws.append([]) if False else None
        for i, fila in enumerate(filas, start=fila_enc + 1):
            for j, valor in enumerate(fila, start=1): ws.cell(row=i, column=j, value=valor)

        borde = Border(bottom=Side(style="thin", color="FFE3E8E5"))
        ultima = fila_enc + len(filas)
        for i in range(fila_enc + 1, ultima + 1):
            for j, (_, _, tipo) in enumerate(cols, start=1):
                celda = ws.cell(row=i, column=j)
                celda.border = borde; celda.font = Font(size=11, color=TINTA)
                if (i - fila_enc) % 2 == 0: celda.fill = PatternFill("solid", fgColor=TINTE)
                if tipo == "$": celda.number_format = '"$"#,##0.00'; celda.alignment = Alignment(horizontal="right")
                elif tipo == "n": celda.number_format = "#,##0"; celda.alignment = Alignment(horizontal="right")
                elif tipo == "%": celda.number_format = '0"%"'; celda.alignment = Alignment(horizontal="right")
                elif tipo == "f": celda.number_format = "DD/MM/YYYY"; celda.alignment = Alignment(horizontal="center")
        if len(hoja) > 3 and hoja[3] and ultima > fila_enc:   # última fila en negrita (totales)
            for j in range(1, ancho_total + 1):
                c_ = ws.cell(row=ultima, column=j)
                c_.font = Font(bold=True, size=11, color=VERDE_OSC)
                c_.fill = PatternFill("solid", fgColor="FFDCE8E1")

        for j, (_, ancho, _) in enumerate(cols, start=1):
            ws.column_dimensions[get_column_letter(j)].width = ancho
        ws.freeze_panes = ws.cell(row=fila_enc + 1, column=1).coordinate
        if filas: ws.auto_filter.ref = f"A{fila_enc}:{get_column_letter(ancho_total)}{ultima}"

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


def _fecha(v):
    try: return datetime.date.fromisoformat(str(v)[:10])
    except Exception: return None


@app.get("/exportar-todo")
def exportar_todo(request: Request, con=Depends(db)):
    """Tu negocio entero en un Excel que se lee sin el ERP. Si un día el ERP no está,
    con este archivo sigues teniendo tus clientes, tus ventas y tus cuentas."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    F = lambda x: _fecha(x) if x else None
    hojas = []

    hojas.append(("Clientes",
        [("Cliente", 26, ""), ("Teléfono", 16, ""), ("Correo", 26, ""), ("Cédula", 14, ""), ("Ciudad", 16, ""),
         ("Porche", 20, ""), ("Órdenes", 9, "n"), ("Comprado", 13, "$"), ("Saldo a favor", 13, "$"),
         ("Última compra", 14, "f"), ("Cliente desde", 14, "f")],
        [(r["nombre"], r["telefono"], r["correo"], r["cedula"], r["ciudad"],
          " ".join(x for x in (r["porche_version"], r["porche_tamano"]) if x) or None,
          r["n"], r["gastado"], r["credito"] or None, F(r["ultima"]), F(r["creado_en"]))
         for r in con.execute("""SELECT c.*,
             (SELECT COUNT(*) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada') n,
             (SELECT COALESCE(SUM(o.total),0) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada') gastado,
             (SELECT COALESCE(SUM(cc.monto),0) FROM credito_cliente cc WHERE cc.cliente_id=c.id) credito,
             (SELECT MAX(substr(o.creado_en,1,10)) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada') ultima
             FROM clientes c ORDER BY c.nombre""")]))

    hojas.append(("Órdenes",
        [("Orden", 10, ""), ("Fecha", 13, "f"), ("Cliente", 24, ""), ("Entrega", 16, ""), ("Despachador", 14, ""),
         ("Estado", 13, ""), ("Pago", 14, ""), ("Total", 12, "$"), ("Pagado", 12, "$"), ("Delivery", 10, "$"), ("Entregada", 13, "f")],
        [(r["numero"], F(r["creado_en"]), r["cliente"], ENTREGA.get(r["tipo_entrega"] or "", r["tipo_entrega"]), r["despachador"],
          E_LABEL.get(r["estado"], r["estado"]), P_LABEL.get(r["estado_pago"], r["estado_pago"]),
          r["total"], r["pagado"], r["delivery"], F(r["fecha_entrega"]))
         for r in con.execute("""SELECT o.*, c.nombre cliente,
             (SELECT COALESCE(SUM(p.monto_usd),0) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado') pagado
             FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id ORDER BY o.id""")]))

    hojas.append(("Productos vendidos",
        [("Orden", 10, ""), ("Fecha", 13, "f"), ("Cliente", 24, ""), ("Producto", 30, ""),
         ("Cantidad", 10, "n"), ("Precio", 12, "$"), ("Total", 12, "$")],
        [(r["numero"], F(r["creado_en"]), r["cliente"], r["producto"], r["cantidad"], r["precio"], r["total"])
         for r in con.execute("""SELECT o.numero, o.creado_en, c.nombre cliente,
             COALESCE(NULLIF(l.nombre,''), p.nombre) producto, l.cantidad, l.precio, l.total
             FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id
             LEFT JOIN clientes c ON c.id=o.cliente_id LEFT JOIN productos p ON p.id=l.producto_id
             WHERE o.estado!='cancelada' ORDER BY o.id, l.id""")]))

    hojas.append(("Pagos recibidos",
        [("Fecha", 13, "f"), ("Orden", 10, ""), ("Cliente", 24, ""), ("Forma", 18, ""), ("Caja", 20, ""),
         ("Monto USD", 12, "$"), ("Estado", 13, "")],
        [(F(r["fecha"]), r["numero"], r["cliente"], r["forma"], r["cuenta"], r["monto_usd"], r["estado"])
         for r in con.execute("""SELECT p.*, o.numero, c.nombre cliente FROM pagos p
             JOIN ordenes o ON o.id=p.orden_id LEFT JOIN clientes c ON c.id=o.cliente_id
             ORDER BY p.fecha, p.id""")]))

    hojas.append(("Gastos",
        [("Fecha", 13, "f"), ("Categoría", 22, ""), ("Subcategoría", 20, ""), ("Qué", 28, ""),
         ("Pagado a", 20, ""), ("Caja", 20, ""), ("Monto USD", 12, "$")],
        [(F(r["fecha"]), r["categoria"], r["subcategoria"], r["descripcion"], r["proveedor"], r["caja"], r["monto_usd"])
         for r in con.execute("""SELECT g.*, cu.nombre caja FROM gastos g LEFT JOIN cuentas cu ON cu.id=g.cuenta_id
             ORDER BY g.fecha, g.id""")]
        + [("", "TOTAL GASTADO", "", "", "", "", con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM gastos").fetchone()[0])],
        True))

    cs = saldos(con)
    hojas.append(("Cajas",
        [("Caja", 24, ""), ("Moneda", 9, ""), ("Saldo inicial", 14, "$"), ("Ingresos", 12, "$"),
         ("Gastos", 12, "$"), ("Entradas", 12, "$"), ("Salidas", 12, "$"), ("Saldo", 13, "$")],
        [(c["nombre"], c["moneda"], c["saldo_inicial"], c["ingresos"], c["gastos"], c["entradas"], c["salidas"], c["saldo"])
         for c in cs]
        + [("TOTAL EN CAJA", "", None, None, None, None, None,
            round(sum(c["saldo"] for c in cs if c["tipo"] == "operativa"), 2))],
        True))

    hojas.append(("Inventario",
        [("Producto", 30, ""), ("Tipo", 12, ""), ("Disponible", 12, "n"), ("Mínimo", 10, "n"), ("Proveedor", 18, "")],
        [(r["nombre"], r["tipo"], r["hay"], r["minimo"], r["proveedor"])
         for r in con.execute("""SELECT p.nombre, p.tipo, p.minimo, p.proveedor,
             COALESCE((SELECT SUM(m.cantidad) FROM mov_inventario m WHERE m.producto_id=p.id),0) hay
             FROM productos p WHERE p.activo=1 ORDER BY (p.tipo='insumo') DESC, p.orden""")]))

    hojas.append(("Proveedores",
        [("Proveedor", 24, ""), ("Teléfono", 16, ""), ("Qué le compras", 40, ""), ("Notas", 30, "")],
        [(r["nombre"], r["telefono"], r["items"], r["notas"])
         for r in con.execute("""SELECT p.*, (SELECT GROUP_CONCAT(i.item, ' · ') FROM proveedor_items i WHERE i.proveedor_id=p.id) items
             FROM proveedores p ORDER BY p.nombre""")]))

    hojas.append(("Producción",
        [("Pedido", 8, "n"), ("Qué", 26, ""), ("A quién", 18, ""), ("Pedidos", 9, "n"), ("Llegaron", 9, "n"),
         ("Costo", 12, "$"), ("Abonado", 12, "$"), ("Estado", 14, ""), ("Se esperaba", 13, "f")],
        [(r["id"], r["pieza"], r["responsable"], r["cantidad"], r["recibido"], r["costo"], r["abonado"],
          r["estado"], F(r["fecha_esperada"]))
         for r in con.execute("""SELECT pr.*, (SELECT COALESCE(SUM(a.monto),0) FROM abonos_produccion a WHERE a.produccion_id=pr.id) abonado
             FROM produccion pr ORDER BY pr.id""")]))

    hojas.append(("Despachadores",
        [("Despachador", 20, ""), ("Teléfono", 16, ""), ("Entregas", 10, "n"), ("Ganado", 12, "$"),
         ("Se le debe", 12, "$"), ("Activo", 9, "")],
        [(d["nombre"], d["telefono"], d["n"], d["ganado"], d["debe"], "Sí" if d["activo"] else "No")
         for d in con.execute("""SELECT d.*,
             (SELECT COUNT(*) FROM ordenes o WHERE o.despachador=d.nombre AND o.estado!='cancelada' AND o.origen_excel=0) n,
             (SELECT COALESCE(SUM(COALESCE(o.delivery,0)),0) FROM ordenes o WHERE o.despachador=d.nombre AND o.estado!='cancelada' AND o.origen_excel=0) ganado,
             (SELECT COALESCE(SUM(COALESCE(o.delivery,0)),0) FROM ordenes o WHERE o.despachador=d.nombre AND o.estado!='cancelada' AND o.origen_excel=0 AND o.despachador_pagado=0) debe
             FROM despachadores d ORDER BY d.activo DESC, d.nombre""")]))

    hojas.append(("Pagos fijos",
        [("Pago", 24, ""), ("A quién", 18, ""), ("Cada cuánto", 22, ""), ("Monto", 12, "$"),
         ("Categoría", 22, ""), ("Activo", 9, "")],
        [(r["nombre"], r["proveedor"], {"semanal": "Todas las semanas", "quincenal": "15 y último",
          "mensual": "Una vez al mes", "inicio_mes": "Primeros días del mes"}.get(r["frecuencia"], r["frecuencia"]),
          r["monto"], f"{r['categoria']} · {r['subcategoria'] or ''}".strip(" ·"), "Sí" if r["activo"] else "No")
         for r in con.execute("SELECT * FROM compromisos ORDER BY activo DESC, nombre")]))

    hojas.append(("Mascotas",
        [("Perro", 20, ""), ("Raza", 20, ""), ("Dueño", 24, ""), ("Cumpleaños", 13, "f"), ("Notas", 30, "")],
        [(m["nombre"], m["raza"], m["cliente"], F(m["fecha_nacimiento"]), m["notas"])
         for m in con.execute("""SELECT m.*, c.nombre cliente FROM mascotas m
             LEFT JOIN clientes c ON c.id=m.cliente_id ORDER BY c.nombre, m.nombre""")]))

    hojas.append(("Direcciones",
        [("Cliente", 24, ""), ("Dirección", 46, ""), ("Zona", 16, ""), ("Ciudad", 16, ""), ("Principal", 10, "")],
        [(d["cliente"], d["direccion"], d["zona"], d["ciudad"], "Sí" if d["principal"] else "")
         for d in con.execute("""SELECT d.*, c.nombre cliente FROM direcciones d
             JOIN clientes c ON c.id=d.cliente_id ORDER BY c.nombre, d.principal DESC""")]))

    hojas.append(("Movimientos de caja",
        [("Fecha", 13, "f"), ("Tipo", 12, ""), ("De", 20, ""), ("A", 20, ""), ("Concepto", 30, ""), ("Monto USD", 12, "$")],
        [(F(m["fecha"]), m["tipo"], m["origen"], m["destino"], m["concepto"] or m["notas"], m["monto_usd"])
         for m in con.execute("""SELECT m.*, co.nombre origen, cd.nombre destino FROM movimientos m
             LEFT JOIN cuentas co ON co.id=m.cuenta_origen_id LEFT JOIN cuentas cd ON cd.id=m.cuenta_destino_id
             ORDER BY m.fecha, m.id""")]))

    hojas.append(("Equipo",
        [("Nombre", 20, ""), ("Correo", 28, ""), ("Acceso al ERP", 18, ""), ("En la nómina", 13, ""), ("Sueldo al mes", 14, "$")],
        [(u["nombre"], u["correo"] or u["usuario"], ACCESOS.get(u["rol"], u["rol"]) if u["activo"] else "No entra",
          "Sí" if u["nomina"] else "No", u["sueldo_mes"])
         for u in con.execute("SELECT * FROM usuarios WHERE rol!='sistema' ORDER BY activo DESC, rol, nombre")]))

    hojas.append(("Repuestos pendientes",
        [("Cliente", 24, ""), ("Tipo", 14, ""), ("Tamaño", 12, ""), ("Faltan", 9, "n"), ("Desde", 13, "f")],
        [(k["cliente"], "Pack", k["tamano"], k["saldo"], F(k["creado_en"])) for k in cargar_packs(con) if k["saldo"] > 0]
        + [(r["cliente"], "Prepagado", r["tamano"], 1, F(r["pagado_en"])) for r in cargar_prepagados(con)]))

    return hoja_excel(hojas, f"decopet-{datetime.date.today():%Y-%m-%d}", "Los datos del ERP")


@app.get("/clientes/exportar")
def clientes_exportar(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    cols = [("Cliente", 26, ""), ("Teléfono", 16, ""), ("Correo", 26, ""), ("Cédula", 14, ""), ("Ciudad", 18, ""),
            ("Canal", 14, ""), ("Porche", 22, ""), ("Órdenes", 10, "n"), ("Comprado", 14, "$"),
            ("Última compra", 14, "f"), ("Cliente desde", 14, "f")]
    filas = [(r["nombre"], r["telefono"], r["correo"], r["cedula"], r["ciudad"], CANAL.get(r["canal_habitual"] or "", r["canal_habitual"]),
              " ".join(x for x in (r["porche_version"], r["porche_tamano"]) if x) or None,
              r["n"], r["gastado"], _fecha(r["ultima"]), _fecha(r["creado_en"]))
             for r in con.execute("""SELECT c.*, (SELECT COUNT(*) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada') n,
                    (SELECT COALESCE(SUM(o.total),0) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada') gastado,
                    (SELECT MAX(substr(o.creado_en,1,10)) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada') ultima
                    FROM clientes c ORDER BY c.nombre""")]
    return hoja_excel([("Clientes", cols, filas)], "Decopet clientes.xlsx", "Clientes")


@app.get("/mascotas/exportar")
def mascotas_exportar(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    cols = [("Mascota", 22, ""), ("Raza", 22, ""), ("Cumpleaños", 14, ""), ("Nacimiento", 14, "f"),
            ("Peso (kg)", 11, "n"), ("Dueño", 26, ""), ("Teléfono", 16, ""), ("Ciudad", 18, "")]
    filas = [(m["nombre"], m["raza"], m["cumple_mes_dia"], _fecha(m["fecha_nacimiento"]), m["peso_kg"], m["cliente"], m["telefono"], m["ciudad"])
             for m in con.execute("""SELECT m.*, c.nombre cliente, c.telefono, c.ciudad FROM mascotas m
                                     LEFT JOIN clientes c ON c.id=m.cliente_id ORDER BY c.nombre, m.nombre""")]
    return hoja_excel([("Mascotas", cols, filas)], "Decopet mascotas.xlsx", "Mascotas")


@app.get("/ordenes/exportar")
def ordenes_exportar(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    cols = [("Orden", 10, ""), ("Fecha", 12, "f"), ("Fecha de pago", 13, "f"), ("Cliente", 24, ""), ("Canal", 13, ""),
            ("Productos", 40, ""), ("Entrega", 16, ""), ("Ciudad", 16, ""), ("Estado", 14, ""), ("Pago", 15, ""),
            ("Subtotal", 12, "$"), ("Delivery", 11, "$"), ("Total", 12, "$"), ("Cobrado", 12, "$")]
    filas = []
    for o in con.execute("""SELECT o.*, c.nombre cliente,
            (SELECT GROUP_CONCAT(CAST(l.cantidad AS INTEGER) || '× ' || l.nombre, ' · ') FROM orden_lineas l WHERE l.orden_id=o.id) productos,
            COALESCE((SELECT SUM(p.monto_usd) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado'),0) cobrado
            FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id ORDER BY o.creado_en DESC"""):
        filas.append((o["numero"], _fecha(o["creado_en"]), _fecha(o["fecha_pago"]), o["cliente"], CANAL.get(o["canal"] or "", o["canal"]),
                      o["productos"], ENTREGA.get(o["tipo_entrega"] or "", o["tipo_entrega"]), o["ciudad"],
                      E_LABEL.get(o["estado"] or "", o["estado"]), P_LABEL.get(o["estado_pago"] or "", o["estado_pago"]),
                      o["subtotal"], o["delivery"], o["total"], o["cobrado"]))
    return hoja_excel([("Órdenes", cols, filas)], "Decopet ordenes.xlsx", "Órdenes")


@app.get("/finanzas/gastos/exportar")
def gastos_exportar(request: Request, anio: str = "", con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    anio = anio or str(datetime.date.today().year)
    cols = [("Fecha", 12, "f"), ("Categoría", 22, ""), ("Subcategoría", 22, ""), ("Concepto", 32, ""), ("Pagado a", 20, ""),
            ("Cantidad", 11, "n"), ("Unidad", 12, ""), ("Caja", 22, ""), ("Ref/Factura", 16, ""), ("Compra grande", 13, ""), ("Monto", 13, "$")]
    filas = [(_fecha(g["fecha"]), g["categoria"], g["subcategoria"], g["descripcion"], g["proveedor"], g["cantidad"], g["unidad"],
              g["caja"], g["comprobante"], "Sí" if g["compra_grande"] else "", g["monto_usd"])
             for g in con.execute("""SELECT g.*, cu.nombre caja FROM gastos g LEFT JOIN cuentas cu ON cu.id=g.cuenta_id
                                     WHERE substr(g.fecha,1,4)=? ORDER BY g.fecha, g.id""", (anio,))]
    filas.append((None, "TOTAL", None, None, None, None, None, None, None, None, round(sum(f[-1] or 0 for f in filas), 2)))
    return hoja_excel([(f"Gastos {anio}", cols, filas, True)], f"Decopet gastos {anio}.xlsx", "Gastos")


@app.get("/cashflow/exportar")
def cashflow_exportar(request: Request, caja: str = "", mes: str = "", con=Depends(db)):
    """El libro de caja y los saldos, en un Excel listo para leer."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    lineas = libro_caja(con, int(caja) if caja else None, mes or None)
    cols_libro = [("Fecha", 12, "f"), ("Concepto", 38, ""), ("Detalle", 34, ""), ("Nro.", 7, ""),
                  ("Caja", 24, ""), ("Entradas", 13, "$"), ("Salidas", 13, "$"), ("Saldo", 14, "$")]
    filas = [(datetime.date.fromisoformat(l["fecha"][:10]), l["concepto"], l["detalle"],
              (l["caja"]["codigo"] if l["caja"] else ""), (l["caja"]["nombre"] if l["caja"] else ""),
              l["entrada"] or None, l["salida"] or None, l["saldo"]) for l in lineas]
    cols_cajas = [("Nro.", 7, ""), ("Caja", 28, ""), ("Moneda", 10, ""), ("Saldo", 15, "$")]
    cs = [c for c in saldos(con) if c["activa"]]
    filas_cajas = [(c["codigo"], c["nombre"], c["moneda"], c["saldo"]) for c in cs]
    filas_cajas.append(("", "TOTAL", "", round(sum(c["saldo"] for c in cs), 2)))
    nombre = f"Decopet cash flow{(' ' + mes) if mes else ''}.xlsx"
    return hoja_excel([("Libro de caja", cols_libro, filas), ("Saldo por caja", cols_cajas, filas_cajas)], nombre)


@app.post("/cashflow/{tipo}/{lid}/borrar")
def cashflow_borrar(request: Request, tipo: str, lid: int, con=Depends(db)):
    """Borra una línea del libro escrita a mano. Los cobros de una orden no se borran desde aquí."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    if tipo == "gasto":
        # si venía de pagarle a un proveedor, se deshace también el abono para que el saldo vuelva a quedar bien
        ab = con.execute("SELECT produccion_id FROM abonos_produccion WHERE gasto_id=?", (lid,)).fetchone()
        if ab:
            con.execute("DELETE FROM abonos_produccion WHERE gasto_id=?", (lid,))
            con.execute("UPDATE produccion SET pagado=0 WHERE id=?", (ab["produccion_id"],))
        con.execute("DELETE FROM compromisos_pagos WHERE gasto_id=?", (lid,))   # vuelve a aparecer como pendiente
        con.execute("DELETE FROM gastos WHERE id=?", (lid,))
    elif tipo == "mov":
        con.execute("DELETE FROM movimientos WHERE id=?", (lid,))
    con.commit(); return RedirectResponse("/cashflow", status_code=303)


@app.post("/finanzas/movimientos")
async def movimiento_crear(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    f = await request.form(); tasa = tasa_hoy(con)["valor"] or 0
    moneda = f.get("moneda") or "USD"; monto_real = float(f.get("monto") or 0)
    monto_usd = round(monto_real / tasa, 2) if (moneda == "VES" and tasa) else monto_real
    origen = int(f["origen"]) if f.get("origen") else None; destino = int(f["destino"]) if f.get("destino") else None
    if not origen and not destino: return RedirectResponse("/cashflow", status_code=303)
    tipo = "transferencia" if (origen and destino) else ("salida" if origen else "entrada")
    concepto = (f.get("concepto") or "").strip() or ("Swap" if tipo == "transferencia" else "Ajuste")
    con.execute("INSERT INTO movimientos (fecha, tipo, cuenta_origen_id, cuenta_destino_id, monto_usd, monto_real, moneda, tasa, concepto, despachador, usuario_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f.get("fecha") or datetime.date.today().isoformat(), tipo, origen, destino, monto_usd, monto_real, moneda, tasa if moneda == "VES" else None,
                 concepto + (f" · {f['nota'].strip()}" if (f.get("nota") or "").strip() else ""), None, uid_de(request)))
    con.commit(); return RedirectResponse("/cashflow", status_code=303)


# ------------------------------------------------------------------ CALENDARIO Y PENDIENTES
def _sumar_mes(d):
    m = d.month % 12 + 1; a = d.year + (d.month == 12)
    return datetime.date(a, m, min(d.day, calendar.monthrange(a, m)[1]))


def eventos_mes(con, anio, mes):
    """Lo que el ERP ya sabe que pasa cada día del mes. Solo para verlo: se maneja desde su sección."""
    ini = datetime.date(anio, mes, 1); fin = datetime.date(anio, mes, calendar.monthrange(anio, mes)[1])
    a, b = ini.isoformat(), fin.isoformat(); ev = {}
    def pon(f, tipo, txt, href):
        ev.setdefault(f[:10], []).append({"tipo": tipo, "txt": txt, "href": href})
    hoy = datetime.date.today().isoformat()
    # lo pendiente de días pasados no se queda en ese día: se corre a hoy (igual que en Operaciones), hasta que se entregue
    for r in con.execute("""SELECT CASE WHEN substr(fecha_prometida,1,10) < ? THEN ? ELSE substr(fecha_prometida,1,10) END f,
                            SUM(substr(fecha_prometida,1,10) < ?) atras, COUNT(*) n FROM ordenes
                            WHERE estado IN ('pendiente','en_ruta') AND origen_excel=0 AND fecha_prometida IS NOT NULL GROUP BY 1""", (hoy, hoy, hoy)):
        if not (a <= r["f"] <= b): continue
        al_dia = r["n"] - r["atras"]
        if al_dia: pon(r["f"], "entrega", f"{al_dia} entrega{'s' if al_dia != 1 else ''} programada{'s' if al_dia != 1 else ''}", f"/operaciones?dia={r['f']}")
        if r["atras"]: pon(r["f"], "entrega", f"{r['atras']} entrega{'s' if r['atras'] != 1 else ''} que quedó atrás", "/operaciones")
    # lo ya entregado queda en el día en que de verdad se entregó (tachado), para que el calendario cuente lo que pasó
    for r in con.execute("""SELECT substr(fecha_entrega,1,10) f, COUNT(*) n FROM ordenes WHERE estado='entregada' AND origen_excel=0
                            AND substr(fecha_entrega,1,10) BETWEEN ? AND ? GROUP BY 1""", (a, b)):
        pon(r["f"], "pagado", f"✓ {r['n']} entregada{'s' if r['n'] != 1 else ''}", f"/ordenes?estado=todas")
    for tb, que in (("packs", "retiro de pack"), ("repuestos_prepagados", "repuesto prepagado")):
        try:
            for r in con.execute(f"SELECT fecha_programada f, COUNT(*) n FROM {tb} WHERE fecha_programada BETWEEN ? AND ? GROUP BY 1", (a, b)):
                pon(r["f"], "entrega", f"{r['n']} {que}{'s' if r['n'] != 1 else ''}", f"/operaciones?dia={r['f'][:10]}")
        except sqlite3.OperationalError: pass
    pagados = {(r[0], r[1]) for r in con.execute("SELECT compromiso_id, vence FROM compromisos_pagos")}
    for c in con.execute("SELECT * FROM compromisos WHERE activo=1"):
        for v in vencimientos(c, ini, fin):
            ya = (c["id"], v.isoformat()) in pagados
            txt = ("✓ " if ya else "Pagar: ") + c["nombre"] + (f" · hasta el {min(v.day + (c['dia'] or 5) - 1, fin.day)}" if c["frecuencia"] == "inicio_mes" and not ya else "")
            pon(v.isoformat(), "pagado" if ya else "pago", txt, "/finanzas/recurrentes")
    for d in dias_de_pago(anio, mes): pon(d.isoformat(), "pago", "Quincena del equipo", "/equipo")
    d = ini
    while d <= fin:
        if d.weekday() == 4: pon(d.isoformat(), "pago", "Pagar a despachadores", "/despachadores")
        d += datetime.timedelta(days=1)
    for r in con.execute("""SELECT pr.fecha_esperada f, COALESCE(pr.pieza, p.nombre) nombre, pr.cantidad - pr.recibido faltan, pr.responsable
                            FROM produccion pr LEFT JOIN productos p ON p.id=pr.producto_id
                            WHERE pr.estado='en_proceso' AND pr.fecha_esperada BETWEEN ? AND ?""", (a, b)):
        pon(r["f"], "llega", f"Llega {r['faltan']:g} {r['nombre']}" + (f" · {r['responsable']}" if r["responsable"] else ""), "/produccion")
    cumples = {}   # un solo renglón por día con todos los perros, si no tapan el calendario
    for m in con.execute("""SELECT m.nombre, m.fecha_nacimiento, m.cumple_mes_dia FROM mascotas m JOIN clientes c ON c.id=m.cliente_id
                            WHERE COALESCE(m.cumple_mes_dia,'')!='' OR COALESCE(m.fecha_nacimiento,'')!=''"""):
        md = m["cumple_mes_dia"] or (m["fecha_nacimiento"] or "")[5:10]
        if len(md) == 5 and md[:2] == f"{mes:02d}":
            try: cumples.setdefault(datetime.date(anio, mes, int(md[3:])).isoformat(), []).append(m["nombre"])
            except ValueError: pass
    for f, ns in cumples.items():
        pon(f, "cumple", f"🎂 {len(ns)} cumpleaños: " + ", ".join(ns) if len(ns) > 1 else f"🎂 {ns[0]}", "/mascotas?ver=cumples")
    return ev


@app.get("/calendario", response_class=HTMLResponse)
def calendario(request: Request, mes: str = "", dia: str = "", con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/inicio", status_code=303)
    hoy = datetime.date.today()
    try: a, m = (int(x) for x in mes.split("-")) if mes else (hoy.year, hoy.month)
    except ValueError: a, m = hoy.year, hoy.month
    ini = datetime.date(a, m, 1); fin = datetime.date(a, m, calendar.monthrange(a, m)[1])
    ev = eventos_mes(con, a, m)
    for p in con.execute("SELECT * FROM pendientes WHERE hecho_en IS NULL AND fecha BETWEEN ? AND ?", (ini.isoformat(), fin.isoformat())):
        ev.setdefault(p["fecha"], []).insert(0, {"tipo": "mio", "txt": p["texto"], "href": None, "id": p["id"]})
    semanas = calendar.Calendar(firstweekday=0).monthdatescalendar(a, m)
    pend = [dict(r) for r in con.execute("SELECT * FROM pendientes WHERE hecho_en IS NULL ORDER BY fecha IS NULL, fecha, id")]
    hechos = con.execute("SELECT * FROM pendientes WHERE hecho_en IS NOT NULL ORDER BY hecho_en DESC LIMIT 10").fetchall()
    ant = (ini - datetime.timedelta(days=1)).strftime("%Y-%m"); sig = (fin + datetime.timedelta(days=1)).strftime("%Y-%m")
    dia_sel = dia if dia[:7] == f"{a}-{m:02d}" else (hoy.isoformat() if (a, m) == (hoy.year, hoy.month) else ini.isoformat())
    notas = con.execute("SELECT * FROM notas ORDER BY fijada DESC, editado_en DESC").fetchall()
    return render(request, "calendario.html", seccion="calendario", notas=notas, semanas=semanas, ev=ev, mes_n=m, anio=a, titulo_mes=f"{MESES[m - 1].capitalize()} {a}",
                  ant=ant, sig=sig, pend=pend, hechos=hechos, hoy_d=hoy, dia_sel=dia_sel, mes_q=f"{a}-{m:02d}")


@app.post("/pendientes/nuevo")
def pendiente_nuevo(request: Request, texto: str = Form(""), fecha: str = Form(""), repetir: str = Form(""), volver: str = Form("/calendario"), con=Depends(db)):
    if solo_admin(request) and texto.strip():
        con.execute("INSERT INTO pendientes (texto, fecha, repetir, usuario_id) VALUES (?,?,?,?)",
                    (texto.strip(), fecha.strip() or None, repetir if (repetir in ("semanal", "mensual") and fecha.strip()) else None, uid_de(request)))
        con.commit()
    return RedirectResponse(volver or "/calendario", status_code=303)


@app.post("/pendientes/{pid}/hecho")
def pendiente_hecho(request: Request, pid: int, volver: str = Form("/calendario"), con=Depends(db)):
    """Listo. Si se repite, en vez de cerrarse pasa a la próxima fecha."""
    p = con.execute("SELECT * FROM pendientes WHERE id=?", (pid,)).fetchone()
    if solo_admin(request) and p:
        if p["repetir"] and p["fecha"]:
            f = datetime.date.fromisoformat(p["fecha"]); hoy = datetime.date.today()
            while f <= hoy: f = f + datetime.timedelta(days=7) if p["repetir"] == "semanal" else _sumar_mes(f)
            con.execute("UPDATE pendientes SET fecha=? WHERE id=?", (f.isoformat(), pid))
        else:
            con.execute("UPDATE pendientes SET hecho_en=datetime('now','localtime') WHERE id=?", (pid,))
        con.commit()
    return RedirectResponse(volver or "/calendario", status_code=303)


@app.post("/pendientes/{pid}/editar")
def pendiente_editar(request: Request, pid: int, texto: str = Form(""), fecha: str = Form(""), repetir: str = Form(""), borrar: str = Form(""),
                     reabrir: str = Form(""), volver: str = Form("/calendario"), con=Depends(db)):
    if solo_admin(request):
        if borrar: con.execute("DELETE FROM pendientes WHERE id=?", (pid,))
        elif reabrir: con.execute("UPDATE pendientes SET hecho_en=NULL WHERE id=?", (pid,))
        elif texto.strip():
            con.execute("UPDATE pendientes SET texto=?, fecha=?, repetir=? WHERE id=?",
                        (texto.strip(), fecha.strip() or None, repetir if (repetir in ("semanal", "mensual") and fecha.strip()) else None, pid))
        con.commit()
    return RedirectResponse(volver or "/calendario", status_code=303)


@app.get("/notas", response_class=HTMLResponse)
def notas_lista(request: Request, q: str = "", con=Depends(db)):
    return RedirectResponse("/calendario#notas", status_code=303)   # las notas viven en el Calendario, debajo de Pendientes


@app.post("/notas/guardar")
def notas_guardar(request: Request, id: str = Form(""), titulo: str = Form(""), texto: str = Form(""), borrar: str = Form(""),
                  fijar: str = Form(""), volver: str = Form("/calendario"), con=Depends(db)):
    if solo_admin(request):
        if id.isdigit() and borrar: con.execute("DELETE FROM notas WHERE id=?", (int(id),))
        elif id.isdigit() and fijar: con.execute("UPDATE notas SET fijada=1-fijada WHERE id=?", (int(id),))
        elif titulo.strip() or texto.strip():
            if id.isdigit(): con.execute("UPDATE notas SET titulo=?, texto=?, editado_en=datetime('now','localtime') WHERE id=?", (titulo.strip(), texto.strip(), int(id)))
            else: con.execute("INSERT INTO notas (titulo, texto) VALUES (?,?)", (titulo.strip(), texto.strip()))
        con.commit()
    return RedirectResponse((volver or "/calendario") + "#notas", status_code=303)


# ------------------------------------------------------------------ HISTORIAL DE VENTAS
MESES_N = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# Cristina (3 oct 2026): el registro es el Excel tal cual (llega hasta el 2 oct) + toda orden que se CREA en el ERP desde el 3 de octubre (entregada o no; las canceladas no).
# Lo de antes ya está en el Excel; por eso los pedidos migrados o creados antes no entran (no se duplica).
EN_REGISTRO = """((o.origen_excel=0 AND o.estado!='cancelada'   -- entra al crearse la orden, se haya entregado o no (Cristina, 3 oct)
                   AND substr(o.creado_en,1,10) >= COALESCE((SELECT valor FROM config WHERE clave='registro_desde'), '2026-10-03'))
                  OR COALESCE(o.en_registro,0)=1)"""

def _historial_rows(con, anio, mes, q):
    """Registro de ventas = histórico del Excel + lo que entró por las órdenes del ERP (Cristina, 6 oct: solo lo pagado,
    cada cosa el día que se pagó; el delivery de un retiro de pack que se paga después es una venta de ese día).
    El delivery va dentro de la facturación del producto si se pagó con la misma forma; si no, en línea aparte.
    Dentro del mismo día, el orden es el de llegada (como en su Excel)."""
    args = []; cond = ""
    if anio: cond += " AND substr(fecha,1,4)=?"; args.append(anio)
    if mes: cond += " AND substr(fecha,6,2)=?"; args.append(f"{int(mes):02d}")
    if q: cond += " AND (cliente LIKE ? OR producto LIKE ?)"; args += [f"%{q}%"] * 2
    out = [dict(r) for r in con.execute(f"""SELECT NULL oid, '' numero, fecha, cliente, producto, precio, cantidad, facturacion linea, forma_pago forma,
                                            NULL color, 0 malla, NULL personalizacion, 'excel' origen, fila_excel llegada, 0 lid, fecha_original, NULL dia_orden
                                            FROM registro_ventas WHERE 1=1 {cond}""", args)]
    # las órdenes del ERP: las que entran al registro (creadas desde el 3 oct) completas; de las de antes (ya están en el Excel),
    # solo lo que se les agregó y se cobró desde esa fecha
    desde_r = (con.execute("SELECT valor FROM config WHERE clave='registro_desde'").fetchone() or ["2026-10-03"])[0]
    del_registro = {r[0] for r in con.execute("SELECT o.id FROM ordenes o WHERE " + EN_REGISTRO)}
    con_extras = {r[0] for r in con.execute("SELECT DISTINCT orden_id FROM orden_lineas WHERE extra_en >= ?", (desde_r,))}
    lo = f"{anio}-{int(mes):02d}-01" if anio and mes else (f"{anio}-01-01" if anio else "0000")
    hi = f"{anio}-{int(mes):02d}-31" if anio and mes else (f"{anio}-12-31" if anio else "9999")
    qq = (q or "").lower()
    numeros = {}
    def llegada(fecha):   # va con las órdenes creadas hasta ese día, como venían llegando
        if fecha not in numeros:
            numeros[fecha] = con.execute("SELECT MAX(CAST(substr(numero,2) AS INTEGER)) FROM ordenes WHERE substr(creado_en,1,10)<=?", (fecha,)).fetchone()[0] or 0
        return 1000000 + numeros[fecha]
    for f in entradas_ordenes(con, lo, hi, oids=sorted(del_registro | con_extras)):
        if f["oid"] not in del_registro and not (f["tipo"] == "extra" and f["fecha"] >= desde_r): continue
        if mes and not anio and f["fecha"][5:7] != f"{int(mes):02d}": continue
        if qq and qq not in (f["cliente"] or "").lower() and qq not in (f["producto"] or "").lower() and qq not in (f["numero"] or "").lower(): continue
        f["llegada"] = 1000000 + int((f["numero"] or "#0")[1:] or 0) if f["tipo"] == "pedido" and f["fecha"] == f["dia_orden"] else llegada(f["fecha"])
        out.append(f)
    for d in out:
        try:
            fe = datetime.date.fromisoformat(d["fecha"]); d["semana"] = (fe.day - 1) // 7 + 1; d["mes"] = MESES_N[fe.month - 1].capitalize()   # semana del mes, como en su Excel
        except Exception:
            d["semana"] = ""; d["mes"] = ""
    out.sort(key=lambda d: (d["llegada"] or 0, d["lid"] or 0), reverse=True)   # lo último registrado primero (las órdenes del ERP van después de la última fila del Excel)
    return out


@app.get("/historial", response_class=HTMLResponse)
def historial(request: Request, anio: str = "", mes: str = "", semana: str = "", q: str = "", con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)   # el registro de ventas es dinero
    if not resultados_abierto(request, con):   # lleva la misma clave que Resultados
        return render(request, "clave.html", seccion="historial", titulo="Registro de ventas",
                      texto="", volver="/historial",
                      mal=request.query_params.get("mal"))
    con_datos = {r[0] for r in con.execute("SELECT DISTINCT substr(creado_en,1,4) FROM ordenes")} | {r[0] for r in con.execute("SELECT DISTINCT substr(fecha,1,4) FROM registro_ventas")}
    anios = [str(a) for a in range(datetime.date.today().year, 2022, -1)]
    for a in sorted(con_datos - set(anios), reverse=True):
        if a and a.isdigit() and len(a) == 4: anios.append(a)   # sin el vacío de las filas del Excel sin fecha legible
    todos = anio == "todos"
    if todos: anio = ""
    elif not anio and not q: anio = str(datetime.date.today().year)
    rows = _historial_rows(con, anio, mes, q)
    if semana and mes: rows = [r for r in rows if str(r["semana"]) == semana]   # semana del mes (1–5), como en su Excel
    tot = sum(r["linea"] or 0 for r in rows)
    # Cristina (5 oct): "la venta es el pedido, el delivery es un plus". Ventas = pedidos (en el Excel: un cliente en un día);
    # el delivery suma a la facturación pero no es una venta ni una unidad.
    productos = [r for r in rows if "delivery" not in (r["producto"] or "").lower()]
    unidades = sum(r["cantidad"] or 0 for r in productos)
    pedidos = {(r["oid"] if r["oid"] else (r["fecha"], (r["cliente"] or "").lower())) for r in productos}
    ordenes_ids = pedidos
    ticket = tot / len(pedidos) if pedidos else 0
    por_mes = []
    return render(request, "historial.html", seccion="historial", mes_actual=str(datetime.date.today().month), rows=rows[:2000], total=tot, n_ordenes=len(ordenes_ids), unidades=unidades,
                  anio=anio, mes=mes, semana=semana, q=q, anios=anios, por_mes=por_mes, meses=MESES_N, truncado=len(rows) > 2000, todos=todos, hoy_anio=str(datetime.date.today().year), ticket=ticket, n_pedidos=len(pedidos))


@app.get("/historial/exportar")
def historial_exportar(request: Request, anio: str = "", mes: str = "", semana: str = "", q: str = "", con=Depends(db)):
    # lo mismo que pide la pantalla: sin esto, cualquiera que supiera la dirección bajaba todas las ventas sin la clave
    if not solo_admin(request) or not resultados_abierto(request, con): return RedirectResponse("/historial", status_code=303)
    import csv, io
    from fastapi.responses import StreamingResponse
    rows = _historial_rows(con, anio, mes, q)
    if semana and mes: rows = [r for r in rows if str(r["semana"]) == semana]
    buf = io.StringIO(); w = csv.writer(buf, delimiter=";")
    w.writerow(["Fecha", "Semana", "Mes", "Cliente", "Producto", "Precio", "Cantidad", "Facturación", "Forma de pago"])
    for r in rows:
        w.writerow([r["fecha"], r["semana"], r["mes"], r["cliente"], (r["producto"] or "—") + (f" · plato {r['color']}" if r["color"] else "") + (" + malla" if r["malla"] else ""), r["precio"], int(r["cantidad"] or 1), r["linea"], r["forma"] or ""])
    buf.seek(0)
    nombre = f"decopet-ventas-{anio or 'todo'}{('-' + mes) if mes else ''}.csv"
    return StreamingResponse(iter(["\ufeff" + buf.getvalue()]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename={nombre}"})


# ------------------------------------------------------------------ MIEMBROS VERSIÓN PRO
@app.get("/miembros", response_class=HTMLResponse)
def miembros(request: Request, q: str = "", ver: str = "todos", con=Depends(db)):
    rows = con.execute("""
      SELECT c.id, c.nombre, c.telefono, c.ciudad,
        MIN(CASE WHEN p.sku LIKE 'PRO-%' THEN substr(o.creado_en,1,10) END) desde,
        COALESCE(c.porche_tamano, GROUP_CONCAT(DISTINCT CASE WHEN p.sku='PRO-G' THEN 'Grande' WHEN p.sku='PRO-M' THEN 'Mediano' END)) tamanos,
        SUM(CASE WHEN p.categoria='repuesto' THEN l.cantidad * (CASE WHEN p.sku LIKE 'PACK3%' THEN 3 WHEN p.sku LIKE 'PACK4%' THEN 4 WHEN p.sku LIKE 'PACK8%' THEN 8 ELSE 1 END) ELSE 0 END) repuestos,
        MAX(CASE WHEN p.categoria='repuesto' THEN substr(o.creado_en,1,10) END) ultimo_repuesto,
        (SELECT GROUP_CONCAT(m.nombre, ', ') FROM mascotas m WHERE m.cliente_id=c.id) perros
      FROM clientes c JOIN ordenes o ON o.cliente_id=c.id AND o.estado!='cancelada'
      JOIN orden_lineas l ON l.orden_id=o.id JOIN productos p ON p.id=l.producto_id
      WHERE (c.nombre LIKE ? OR c.telefono LIKE ?)
      GROUP BY c.id HAVING desde IS NOT NULL ORDER BY COALESCE(ultimo_repuesto, desde) DESC""", (f"%{q}%", f"%{q}%")).fetchall()
    hoy = datetime.date.today()
    lista = []
    for r in rows:
        d = dict(r); ref = d["ultimo_repuesto"] or d["desde"]
        d["dias"] = (hoy - datetime.date.fromisoformat(ref)).days if ref else None
        d["situacion"] = "al día" if d["dias"] is not None and d["dias"] <= 35 else ("toca repuesto" if d["dias"] is not None and d["dias"] <= 90 else "inactivo")
        # cuándo le toca el próximo: 21 días desde el último repuesto (o desde que compró el porche)
        d["proximo"] = (datetime.date.fromisoformat(ref) + datetime.timedelta(days=21)).isoformat() if ref else None
        lista.append(d)
    # miembros que vienen de Airtable (Versión Porche = PRO) y todavía no tienen órdenes cargadas
    ids = {d["id"] for d in lista}
    for r in con.execute("""SELECT c.id, c.nombre, c.telefono, c.ciudad, c.porche_tamano, (SELECT GROUP_CONCAT(m.nombre, ', ') FROM mascotas m WHERE m.cliente_id=c.id) perros
                            FROM clientes c WHERE c.porche_version='PRO' AND (c.nombre LIKE ? OR c.telefono LIKE ?)""", (f"%{q}%", f"%{q}%")):
        if r["id"] in ids: continue
        lista.append(dict(id=r["id"], nombre=r["nombre"], telefono=r["telefono"], ciudad=r["ciudad"], perros=r["perros"], desde=None, tamanos=r["porche_tamano"], repuestos=0, ultimo_repuesto=None, dias=None, situacion="sin historial", proximo=None))
    conteos = {k: sum(1 for d in lista if d["situacion"] == v) for k, v in (("toca", "toca repuesto"), ("aldia", "al día"), ("inactivos", "inactivo"), ("sin_historial", "sin historial"))}
    conteos["todos"] = len(lista)
    # el último resultado de su seguimiento de repuesto: "el perro no se adaptó" / "ya no lo usa"
    no_usa = {r[0]: r[1] for r in con.execute("""SELECT cliente_id, resultado FROM seguimientos s WHERE resultado IN ('no_se_adapto','ya_no_usa')
                 AND hecho_en = (SELECT MAX(hecho_en) FROM seguimientos s2 WHERE s2.cliente_id=s.cliente_id AND s2.tipo IN ('primer_repuesto','repuesto'))""")}
    for d in lista: d["no_usa"] = no_usa.get(d["id"])
    conteos["no_adapto"] = sum(1 for d in lista if d["no_usa"] == "no_se_adapto"); conteos["no_usa"] = sum(1 for d in lista if d["no_usa"] == "ya_no_usa")
    if ver in ("no_adapto", "no_usa") and not q: lista = [d for d in lista if d["no_usa"] == {"no_adapto": "no_se_adapto", "no_usa": "ya_no_usa"}[ver]]
    elif ver != "todos" and not q: lista = [d for d in lista if d["situacion"] == {"toca": "toca repuesto", "aldia": "al día", "inactivos": "inactivo", "sin_historial": "sin historial"}[ver]]
    return render(request, "miembros.html", hoy_d=hoy, seccion="miembros", miembros=lista[:300], q=q, ver=ver, conteos=conteos, truncado=len(lista) > 300)


# ------------------------------------------------------------------ PRODUCTOS: catálogo · inventario · galería
CANALES_VENTA = {"whatsapp": "WhatsApp", "instagram": "Instagram", "web": "Página web", "cashea": "Cashea", "duwu": "Duwu", "vidapets": "Vidapets", "presencial": "Presencial"}

@app.get("/productos", response_class=HTMLResponse)
def catalogo(request: Request, canal: str = "", q: str = "", con=Depends(db)):
    prods = con.execute("SELECT * FROM productos WHERE (?='' OR nombre LIKE ? OR sku LIKE ?) ORDER BY tipo DESC, activo DESC, orden", (q, f"%{q}%", f"%{q}%")).fetchall()
    cats = sorted({p["categoria"] for p in prods if p["categoria"]})
    conteo_canal = {k: sum(1 for p in prods if p["tipo"] == "producto" and p["activo"] and k in (p["canales"] or "").split(",")) for k in CANALES_VENTA}
    if canal: prods = [p for p in prods if p["tipo"] != "producto" or canal in (p["canales"] or "").split(",")]
    return render(request, "catalogo.html", seccion="catalogo", IVA=IVA, productos=prods, cats=cats, canal=canal, CANALES_VENTA=CANALES_VENTA, conteo_canal=conteo_canal)


@app.post("/productos/guardar")
async def catalogo_guardar(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/productos", status_code=303)
    f = await request.form()
    ids = {int(k.split("_")[1]) for k in f.keys() if k.startswith("nombre_")}
    for i in ids:
        g = lambda k: (f.get(f"{k}_{i}") or "").strip()
        num = lambda k: float(g(k)) if g(k) else None
        con.execute("""UPDATE productos SET nombre=?, categoria=?, descripcion=?, precio=?, precio_par=?, costo=?, activo=?, requiere_color=?, permite_malla=?, permite_personalizacion=?, minimo=?, descripcion_larga=?, peso_kg=? WHERE id=?""",
                    (g("nombre"), g("categoria") or None, g("descripcion") or None, num("precio"), num("precio_par"), num("costo"), 1 if f.get(f"activo_{i}") else 0,
                     1 if f.get(f"color_{i}") else 0, 1 if f.get(f"malla_{i}") else 0, 1 if f.get(f"perso_{i}") else 0, int(num("minimo") or 0), g("larga") or None, num("peso"), i))
        if f"canales_presentes_{i}" in f:   # solo si el producto estaba en pantalla (el filtro por canal oculta los demás)
            con.execute("UPDATE productos SET canales=? WHERE id=?", (",".join(k for k in CANALES_VENTA if f.get(f"canal_{k}_{i}")) or None, i))
    if (f.get("nuevo_nombre") or "").strip():
        sku = (f.get("nuevo_sku") or f["nuevo_nombre"]).strip().upper().replace(" ", "-")[:20]
        con.execute("INSERT OR IGNORE INTO productos (sku,nombre,categoria,precio,costo,tipo,orden) VALUES (?,?,?,?,?,?,500)",
                    (sku, f["nuevo_nombre"].strip(), f.get("nuevo_categoria") or "otro", float(f.get("nuevo_precio") or 0), float(f.get("nuevo_costo") or 0) or None, f.get("nuevo_tipo") or "producto"))
    con.commit(); return RedirectResponse("/productos" + (f"?canal={f['canal_filtro']}" if f.get("canal_filtro") else ""), status_code=303)


@app.post("/productos/{pid}/foto")
async def producto_foto(request: Request, pid: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/productos", status_code=303)
    f = await request.form(); a = f.get("foto")
    if a and getattr(a, "filename", None):
        ext = re.sub(r"[^a-z0-9]", "", a.filename.rsplit(".", 1)[-1].lower())[:5] or "jpg"; rosado = f.get("variante") == "rosado"
        nombre = f"producto-{pid}{'-rosado' if rosado else ''}.{ext}"
        (FOTOS_DIR / nombre).write_bytes(await a.read())
        con.execute(f"UPDATE productos SET {'foto_rosado' if rosado else 'foto'}=? WHERE id=?", (nombre, pid)); con.commit()
    return RedirectResponse("/productos", status_code=303)


@app.get("/inventario", response_class=HTMLResponse)
def inventario(request: Request, q: str = "", con=Depends(db)):
    prods = con.execute("""SELECT p.*, NULL color, (SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario m WHERE m.producto_id=p.id) stock_calc,
        (SELECT COALESCE(SUM(l.cantidad),0) FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id WHERE l.producto_id=p.id AND o.estado!='cancelada' AND o.creado_en>=date('now','-30 days')) vendidos_30
        FROM productos p WHERE p.tipo IN ('producto','insumo') AND p.activo=1
        AND (p.categoria NOT IN ('porche','repuesto','opcion','kit')
             OR EXISTS (SELECT 1 FROM receta r WHERE r.producto_id=p.id))
        ORDER BY (p.tipo='insumo') DESC, p.orden""").fetchall()
    # los que llevan color (Slow Chow) se muestran separados: azul y rosado se cuentan aparte
    filas = []
    for p in prods:
        if not p["requiere_color"]: filas.append(dict(p)); continue
        for col in ("azul", "rosado"):
            st = con.execute("SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario WHERE producto_id=? AND color=?", (p["id"], col)).fetchone()[0]
            v30 = con.execute("""SELECT COALESCE(SUM(l.cantidad),0) FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id
                                 WHERE l.producto_id=? AND lower(COALESCE(l.color,''))=? AND o.estado!='cancelada'
                                 AND o.creado_en>=date('now','-30 days')""", (p["id"], col)).fetchone()[0]
            filas.append(dict(p) | {"color": col, "stock_calc": st, "vendidos_30": v30,
                                    "minimo": (p["minimo"] or 0) // 2})
        # lo que quedó anotado sin color (no debería pasar) se ve aparte para poder corregirlo, en vez de perderse
        sc = con.execute("SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario WHERE producto_id=? AND COALESCE(color,'') NOT IN ('azul','rosado')", (p["id"],)).fetchone()[0]
        if abs(sc) > 1e-9: filas.append(dict(p) | {"color": None, "sin_color": True, "stock_calc": sc, "vendidos_30": 0, "minimo": 0})
    prods = filas
    if q.strip(): prods = [p for p in prods if q.strip().lower() in (p["nombre"] or "").lower()]
    movs = con.execute("SELECT m.*, p.nombre producto, u.nombre usuario FROM mov_inventario m JOIN productos p ON p.id=m.producto_id LEFT JOIN usuarios u ON u.id=m.usuario_id ORDER BY m.id DESC LIMIT 40").fetchall()
    danados = danados_pendientes(con)
    for p in prods:   # cuántos de esa fila están dañados (con su color, si lleva)
        p["danados"] = sum(d["cantidad"] for d in danados if d["producto_id"] == p["id"] and (not p.get("color") or d["color"] == p.get("color")))
    avisados = {(r[0], r[1] or None) for r in con.execute("SELECT agotando_id, agotando_color FROM notas_taller WHERE resuelto=0 AND agotando_id IS NOT NULL")}
    for p in prods: p["avisado"] = (p["id"], p.get("color") or None) in avisados
    return render(request, "inventario.html", seccion="inventario", q=q, productos=prods, movs=movs, danados=danados)


@app.post("/inventario/{pid}/agotando")
def inventario_agotando(request: Request, pid: int, color: str = Form(""), con=Depends(db)):
    """El taller le avisa a Cristina que algo se está acabando. Le llega a Inicio hasta que lo marque resuelto."""
    if rol_de(request) not in ("admin", "logistica", "taller"): return RedirectResponse("/inventario", status_code=303)
    p = con.execute("SELECT id, nombre, unidad, tipo FROM productos WHERE id=?", (pid,)).fetchone()
    color = (color or "").lower() if (color or "").lower() in PLATO_DE_COLOR else None
    ya = con.execute("SELECT 1 FROM notas_taller WHERE resuelto=0 AND agotando_id=? AND COALESCE(agotando_color,'')=?", (pid, color or "")).fetchone()
    if p and not ya:
        quedan = con.execute("SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario WHERE producto_id=?" + (" AND color=?" if color else ""), (pid, color) if color else (pid,)).fetchone()[0]
        quien = (quien_es(request) or {}).get("nombre") or "El taller"
        texto = f"{quien}: se está agotando {p['nombre']}" + (f" plato {color}" if color else "") + f" (quedan {fmt_cant(quedan, p['unidad'] if p['tipo'] == 'insumo' and p['unidad'] not in (None, 'unidad') else None)})"
        con.execute("INSERT INTO notas_taller (fecha, texto, usuario_id, agotando_id, agotando_color) VALUES (?,?,?,?,?)",
                    (datetime.date.today().isoformat(), texto, uid_de(request), pid, color))
        con.commit()
    return RedirectResponse("/inventario", status_code=303)


@app.get("/inventario/casos", response_class=HTMLResponse)
def inventario_casos(request: Request, con=Depends(db)):
    """Casos abiertos: lo dañado que todavía no se resolvió (por revisar o arreglándose) y lo último que se cerró."""
    cerrados = con.execute("""SELECT d.*, p.nombre producto, u.nombre quien, r.nombre cerro FROM danados d JOIN productos p ON p.id=d.producto_id
                              LEFT JOIN usuarios u ON u.id=d.usuario_id LEFT JOIN usuarios r ON r.id=d.resuelto_por
                              WHERE d.estado NOT IN ('pendiente','reparando') ORDER BY d.resuelto_en DESC, d.id DESC LIMIT 15""").fetchall()
    return render(request, "casos.html", seccion="casos", danados=danados_pendientes(con), cerrados=cerrados, arregladores=arregladores(con), externos=PROVEEDORES_VISIBLES_TALLER)


def arregladores(con):
    """Quién puede llevarse algo dañado para arreglarlo: la gente del taller y Walter (el carpintero)."""
    return [r[0] for r in con.execute("SELECT nombre FROM usuarios WHERE rol='taller' AND activo=1 AND nombre!='Taller' ORDER BY nombre")] + list(PROVEEDORES_VISIBLES_TALLER)


def aviso_de_caso(con, request, d, texto):
    """Lo que hace el taller con un caso le llega a Cristina en Inicio, una sola vez."""
    if rol_de(request) == "admin": return
    con.execute("INSERT INTO notas_taller (fecha, texto, usuario_id, resuelto, resuelto_en, danado_id) VALUES (?,?,?,1,?,?)",
                (datetime.date.today().isoformat(), f"{(quien_es(request) or {}).get('nombre') or 'El taller'}: {texto}", uid_de(request), datetime.date.today().isoformat(), d["id"]))


def nombre_caso(d):
    n = d['cantidad']; return f"{int(n) if float(n).is_integer() else n} {d['nombre']}" + (f" plato {d['color']}" if d["color"] else "")


@app.post("/inventario/danado/{did}/visto")
def inventario_danado_visto(request: Request, did: int, con=Depends(db)):
    """Cristina ya vio el reporte: deja de salir en Inicio (el caso sigue abierto en Casos abiertos)."""
    if not solo_admin(request): return RedirectResponse("/inicio", status_code=303)
    con.execute("UPDATE danados SET visto=1 WHERE id=?", (did,)); con.commit()
    return RedirectResponse("/inicio", status_code=303)


@app.post("/inventario/danado/{did}/reparando")
def inventario_danado_reparando(request: Request, did: int, arregla: str = Form(""), con=Depends(db)):
    """Avisa quién se lo llevó y lo está arreglando (Isaías, Manawa o Walter)."""
    if rol_de(request) not in ("admin", "logistica", "taller"): return RedirectResponse("/inventario", status_code=303)
    d = con.execute("SELECT d.*, p.nombre FROM danados d JOIN productos p ON p.id=d.producto_id WHERE d.id=? AND d.estado='pendiente'", (did,)).fetchone()
    if d:
        arregla = arregla.strip()[:60] or None
        con.execute("UPDATE danados SET estado='reparando', reparando_en=?, reparando_por=?, arregla=? WHERE id=?", (datetime.date.today().isoformat(), uid_de(request), arregla, did))
        aviso_de_caso(con, request, d, f"{arregla + ' está arreglando' if arregla else 'se está arreglando'} {nombre_caso(d)}")
        con.commit()
    return RedirectResponse("/inventario/casos", status_code=303)


@app.post("/inventario/danado/{did}/listo")
def inventario_danado_listo(request: Request, did: int, nota: str = Form(""), con=Depends(db)):
    """Arreglado: vuelve a disponible en el inventario y el caso se cierra."""
    if rol_de(request) not in ("admin", "logistica", "taller"): return RedirectResponse("/inventario", status_code=303)
    d = con.execute("SELECT d.*, p.nombre FROM danados d JOIN productos p ON p.id=d.producto_id WHERE d.id=? AND d.estado IN ('pendiente','reparando')", (did,)).fetchone()
    if d:
        hoy = datetime.date.today().isoformat()
        con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, nota, color, usuario_id) VALUES (?,?,?,?,?,?,?)",
                    (d["producto_id"], hoy, "reparado", d["cantidad"], "se reparó, vuelve a disponible" + (f": {nota.strip()}" if nota.strip() else ""), d["color"], uid_de(request)))
        con.execute("UPDATE danados SET estado='reparado', resuelto_en=?, resuelto_por=?, resolucion=? WHERE id=?", (hoy, uid_de(request), nota.strip() or None, did))
        aviso_de_caso(con, request, d, f"{nombre_caso(d)} arreglado: volvió al inventario")
        con.commit()
    return RedirectResponse("/inventario/casos", status_code=303)


@app.post("/inventario/danado/{did}/resolver")
def inventario_danado_resolver(request: Request, did: int, como: str = Form(...), nota: str = Form(""), con=Depends(db)):
    """Qué pasó con lo dañado: se reparó (vuelve a disponible), se botó, o se devolvió al proveedor."""
    if not solo_admin(request): return RedirectResponse("/inventario", status_code=303)
    d = con.execute("SELECT d.*, p.nombre FROM danados d JOIN productos p ON p.id=d.producto_id WHERE d.id=? AND d.estado IN ('pendiente','reparando')", (did,)).fetchone()
    if not d or como not in ("reparado", "desechado", "devuelto"): return RedirectResponse("/inventario/casos", status_code=303)
    hoy = datetime.date.today().isoformat()
    if como == "reparado":
        con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, nota, color, usuario_id) VALUES (?,?,?,?,?,?,?)",
                    (d["producto_id"], hoy, "reparado", d["cantidad"], "se reparó, vuelve a disponible" + (f": {nota.strip()}" if nota.strip() else ""), d["color"], uid_de(request)))
    con.execute("UPDATE danados SET estado=?, resuelto_en=?, resuelto_por=?, resolucion=? WHERE id=?", (como, hoy, uid_de(request), nota.strip() or None, did))
    con.commit(); return RedirectResponse("/inventario/casos", status_code=303)


def danados_pendientes(con):
    return [dict(r) for r in con.execute("""SELECT d.*, p.nombre producto, u.nombre quien, COALESCE(d.arregla, a.nombre) quien_arregla FROM danados d JOIN productos p ON p.id=d.producto_id
                                           LEFT JOIN usuarios u ON u.id=d.usuario_id LEFT JOIN usuarios a ON a.id=d.reparando_por
                                           WHERE d.estado IN ('pendiente','reparando') ORDER BY d.fecha, d.id""")]


@app.post("/inventario/mov")
def inventario_mov(request: Request, producto_id: str = Form(...), tipo: str = Form(...), cantidad: str = Form(...),
                   nota: str = Form(""), fecha: str = Form(""), color: str = Form(""), con=Depends(db)):
    # el Slow Chow viene como "15:rosado" (cada color es su propia opción en la lista)
    producto_id, _, col = producto_id.partition(":")
    if not producto_id.isdigit(): return RedirectResponse("/inventario", status_code=303)
    producto_id = int(producto_id); color = col or color
    cantidad = cifra(cantidad) or 0
    cantidad = int(cantidad) if float(cantidad).is_integer() else round(cantidad, 2)
    lleva_color = con.execute("SELECT requiere_color FROM productos WHERE id=?", (producto_id,)).fetchone()
    if not (lleva_color and lleva_color[0]): color = ""   # el formulario manda el color aunque esté escondido
    elif (color or "").lower() not in PLATO_DE_COLOR: return RedirectResponse("/inventario", status_code=303)   # Slow Chow siempre con su color
    if tipo == "danado":   # está pero está malo: sale de lo disponible y queda pendiente de qué hacer con él
        if not cantidad: return RedirectResponse("/inventario", status_code=303)
        con.execute("INSERT INTO danados (producto_id, color, cantidad, nota, fecha, usuario_id) VALUES (?,?,?,?,?,?)",
                    (producto_id, (color or "").lower() or None, abs(cantidad), nota.strip() or None, fecha or datetime.date.today().isoformat(), uid_de(request)))
        con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, nota, color, usuario_id) VALUES (?,?,?,?,?,?,?)",
                    (producto_id, fecha or datetime.date.today().isoformat(), "dañado", -abs(cantidad), "dañado" + (f": {nota.strip()}" if nota.strip() else ""),
                     (color or "").lower() or None, uid_de(request)))
        con.commit(); return RedirectResponse("/inventario/casos", status_code=303)
    q = abs(cantidad) if tipo == "entrada" else (-abs(cantidad) if tipo == "salida" else cantidad)
    con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, nota, color, usuario_id) VALUES (?,?,?,?,?,?,?)",
                (producto_id, fecha or datetime.date.today().isoformat(), tipo, q, nota or None, (color or "").lower() or None, uid_de(request)))
    con.commit(); return RedirectResponse("/inventario", status_code=303)


def descontar_inventario(con, oid, uid):
    """Al crear una orden sale del inventario el producto y, si tiene receta, los materiales que consume
    (un Porche PRO se lleva una caja de madera, una placa y una caja de cartón)."""
    hoy = datetime.date.today().isoformat()
    for l in con.execute("SELECT l.*, p.sku, p.nombre, p.categoria FROM orden_lineas l JOIN productos p ON p.id=l.producto_id WHERE l.orden_id=?", (oid,)).fetchall():
        cant = int(l["cantidad"])
        if (l["sku"] or "") in ("MALLA", "MALLA-G"):   # la malla suelta sale de las que el taller dejó listas, de su tamaño
            m = con.execute("SELECT id FROM productos WHERE sku=?", ("INS-MALLA-G" if l["sku"] == "MALLA-G" else "INS-MALLA-M",)).fetchone()
            if m: con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, orden_id, nota, usuario_id) VALUES (?,?,?,?,?,?,?)", (m["id"], hoy, "salida", -cant, oid, f"{cant}× {l['nombre']}", uid))
            continue
        armado = l["categoria"] in ("porche", "repuesto")   # se arma el mismo día: no tiene stock propio
        # …salvo que el taller haya adelantado trabajo: si hay porches ya armados, la venta sale de ahí
        listos = con.execute("SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario WHERE producto_id=?", (l["producto_id"],)).fetchone()[0] if armado else 0
        de_listos = min(cant, max(int(listos), 0))
        if not armado or de_listos:
            con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, orden_id, color, nota, usuario_id) VALUES (?,?,?,?,?,?,?,?)",
                        (l["producto_id"], hoy, "salida", -(de_listos if armado else cant), oid, (l["color"] or "").lower() or None,
                         "ya estaba armado" if armado else None, uid))
        resto = (cant - de_listos) if armado else 0   # lo que no estaba armado se arma ahora y gasta sus materiales; lo que tiene stock propio ya los gastó al armarse
        for r in con.execute("""SELECT r.insumo_id, r.cantidad, i.nombre FROM receta r JOIN productos i ON i.id=r.insumo_id
                                WHERE r.producto_id=?""", (l["producto_id"],)):
            if resto <= 0: break
            con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, orden_id, nota, usuario_id) VALUES (?,?,?,?,?,?,?)",
                        (r["insumo_id"], hoy, "salida", -int(r["cantidad"] * resto), oid, f"para {resto}× {l['nombre']}", uid))
        if l["malla"]:   # la malla que el taller deja lista, del tamaño del porche (Grande → grande; lo demás → mediana)
            sku_m = "INS-MALLA-G" if (l["sku"] or "").upper().endswith("-G") else "INS-MALLA-M"
            m = con.execute("SELECT id FROM productos WHERE sku=?", (sku_m,)).fetchone()
            if m: con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, orden_id, nota, usuario_id) VALUES (?,?,?,?,?,?,?)", (m["id"], hoy, "salida", -cant, oid, f"malla para {cant}× {l['nombre']}", uid))


@app.get("/galeria", response_class=HTMLResponse)
def galeria(request: Request, producto: int = 0, tipo: str = "", con=Depends(db)):
    """Galería de fotos de producto: sin fondo, fondo blanco y montaje (con perro). Para verlas grandes y descargarlas."""
    sql = "SELECT f.*, p.nombre producto, p.sku FROM producto_fotos f JOIN productos p ON p.id=f.producto_id WHERE 1=1"; args = []
    if producto: sql += " AND f.producto_id=?"; args.append(producto)
    if tipo: sql += " AND f.tipo=?"; args.append(tipo)
    rows = con.execute(sql + " ORDER BY p.orden, CASE f.tipo WHEN 'sin_fondo' THEN 0 WHEN 'fondo_blanco' THEN 1 ELSE 2 END, f.orden, f.id", args).fetchall()
    grupos = []   # por producto, en el orden del catálogo
    for r in rows:
        if not grupos or grupos[-1]["id"] != r["producto_id"]: grupos.append(dict(id=r["producto_id"], nombre=r["producto"], fotos=[]))
        grupos[-1]["fotos"].append(r)
    conteos = {r[0]: r[1] for r in con.execute("SELECT tipo, COUNT(*) FROM producto_fotos" + (" WHERE producto_id=?" if producto else "") + " GROUP BY 1", (producto,) if producto else ())}
    prods = con.execute("""SELECT p.id, p.nombre, (SELECT COUNT(*) FROM producto_fotos f WHERE f.producto_id=p.id) n FROM productos p
                           WHERE p.tipo='producto' AND p.activo=1 ORDER BY p.orden""").fetchall()
    lista_js = [dict(archivo=r["archivo"], grande=mini(f"productos/{r['archivo']}", 1600), producto=r["producto"], tipo=r["tipo"], etiqueta=r["etiqueta"]) for r in rows]
    return render(request, "galeria.html", seccion="galeria", fotos=rows, producto=producto, tipo=tipo, conteos=conteos, prods=prods, total=sum(conteos.values()), lista_js=lista_js, grupos=grupos)


@app.post("/galeria/subir")
async def galeria_subir(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/galeria", status_code=303)
    f = await request.form(); pid = int(f["producto_id"]); tipo = f.get("tipo") or "sin_fondo"
    for a in f.getlist("archivo"):
        if not getattr(a, "filename", None): continue
        ext = re.sub(r"[^a-z0-9]", "", a.filename.rsplit(".", 1)[-1].lower())[:5] or "jpg"   # solo letras y números: el nombre lo eligió el navegador
        nombre = f"{pid}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}-{abs(hash(a.filename)) % 100000}.{ext}"
        (FOTOS_PRODUCTOS / nombre).write_bytes(await a.read())
        con.execute("INSERT INTO producto_fotos (producto_id, archivo, tipo, etiqueta) VALUES (?,?,?,?)", (pid, nombre, tipo, f.get("etiqueta") or None))
    con.commit(); return RedirectResponse(f"/galeria?producto={pid}", status_code=303)


@app.post("/galeria/{fid}/borrar")
def galeria_borrar(request: Request, fid: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/galeria", status_code=303)
    r = con.execute("SELECT * FROM producto_fotos WHERE id=?", (fid,)).fetchone()
    if r:
        con.execute("DELETE FROM producto_fotos WHERE id=?", (fid,))
        if not con.execute("SELECT 1 FROM producto_fotos WHERE archivo=?", (r["archivo"],)).fetchone():   # el mismo archivo puede servir a varios productos
            for f in [FOTOS_PRODUCTOS / r["archivo"]] + [MINIATURAS / str(a) / "productos" / f"{r['archivo']}.webp" for a in ANCHOS_MINI]:
                f.unlink(missing_ok=True)
        con.commit()
    return RedirectResponse(f"/galeria?producto={r['producto_id']}" if r else "/galeria", status_code=303)


# ------------------------------------------------------------------ PAGOS RECURRENTES
import calendar
DIAS_SEM = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

def vencimientos(c, desde, hasta):
    """Fechas en que vence un compromiso entre dos fechas (incluidas)."""
    out = []; d = desde
    while d <= hasta:
        if c["frecuencia"] == "semanal" and d.weekday() == (c["dia"] if c["dia"] is not None else 4): out.append(d)
        elif c["frecuencia"] == "quincenal" and d.day in (15, calendar.monthrange(d.year, d.month)[1]): out.append(d)
        elif c["frecuencia"] == "mensual" and d.day == min(c["dia"] or 1, calendar.monthrange(d.year, d.month)[1]): out.append(d)
        elif c["frecuencia"] == "inicio_mes" and d.day == 1: out.append(d)   # se paga dentro de los primeros días del mes
        d += datetime.timedelta(days=1)
    return out


def pagos_pendientes(con, dias_adelante=7):
    """Compromisos vencidos (últimos 30 días) o por vencer en los próximos días, que aún no se han pagado."""
    hoy = datetime.date.today(); out = []
    pagados = {(r[0], r[1]) for r in con.execute("SELECT compromiso_id, vence FROM compromisos_pagos")}
    # Un pago sin hacer no caduca: sigue apareciendo hasta que lo pagues o digas que ese mes no tocaba.
    # El único piso es desde cuándo se cuentan los pagos fijos, para no arrastrar cosas de antes.
    # Va aparte del Cash flow (pagos_desde): Cristina quiso los avisos antes de que arranque el libro.
    r = con.execute("SELECT valor FROM config WHERE clave='pagos_desde'").fetchone()
    desde = (r[0] if r and r[0] else None) or finanzas_desde(con)
    piso = datetime.date.fromisoformat(desde) if desde else hoy - datetime.timedelta(days=365)
    for c in con.execute("SELECT * FROM compromisos WHERE activo=1"):
        for v in vencimientos(c, piso, hoy + datetime.timedelta(days=dias_adelante)):
            if (c["id"], v.isoformat()) in pagados: continue
            # "primeros N días del mes" no está vencido el día 2: tiene su ventana
            gracia = (c["dia"] or 5) - 1 if c["frecuencia"] == "inicio_mes" else 0
            limite = v + datetime.timedelta(days=gracia)
            estado = "proximo" if v > hoy else ("vencido" if hoy > limite else "hoy")
            out.append({"id": c["id"], "nombre": c["nombre"], "monto": c["monto"], "moneda": c["moneda"], "vence": v.isoformat(), "dias": (v - hoy).days,
                        "estado": estado, "limite": limite.isoformat(), "gracia": gracia, "proveedor": c["proveedor"],
                        "cuenta_id": c["cuenta_id"], "categoria": c["categoria"], "subcategoria": c["subcategoria"],
                        "unidad": c["unidad"], "precio_unitario": c["precio_unitario"], "frecuencia": c["frecuencia"]})
    out.sort(key=lambda x: x["vence"]); return out


def dia_de_pago(f):
    """Si el día de pago cae sábado o domingo se les paga el viernes antes. Si cae lunes, el lunes."""
    return f - datetime.timedelta(days=f.weekday() - 4) if f.weekday() >= 5 else f


def dias_de_pago(anio, mes):
    """Los dos días en que se le paga al equipo ese mes: el 15 y el último, corridos si caen domingo."""
    return [dia_de_pago(datetime.date(anio, mes, d)) for d in (15, calendar.monthrange(anio, mes)[1])]


def ventana_pago(anio, mes):
    """Por cada quincena: el día en que se paga y hasta cuándo se sigue recordando si no se pagó.
    El recordatorio no se apaga el domingo: aguanta hasta 2 días después del 15 (o del último)."""
    out = []
    for d in (15, calendar.monthrange(anio, mes)[1]):
        o = datetime.date(anio, mes, d)
        out.append((dia_de_pago(o), o + datetime.timedelta(days=2)))
    return out


def proxima_quincena(hoy):
    for f in dias_de_pago(hoy.year, hoy.month):
        if f >= hoy: return f
    sig = datetime.date(hoy.year, hoy.month, 1) + datetime.timedelta(days=32)
    return dias_de_pago(sig.year, sig.month)[0]


def periodo_quincena(pago):
    """Los días que cubre la quincena que se paga ese día: del 1 al 15, o del 16 al último del mes.
    El día de pago puede haberse corrido al viernes; el período es siempre el del calendario."""
    if pago.day <= 15:
        return pago.replace(day=1).isoformat(), pago.replace(day=15).isoformat()
    return pago.replace(day=16).isoformat(), pago.replace(day=calendar.monthrange(pago.year, pago.month)[1]).isoformat()


def quincena_pendiente(con, hoy):
    """(¿hoy es día de pago?, a quién le falta cobrar la quincena que toca ahora)."""
    ant = datetime.date(hoy.year, hoy.month, 1) - datetime.timedelta(days=1)
    # el día de pago que toca ahora: el más reciente que ya llegó, hasta 2 días después
    tocan = [p for p, fin in ventana_pago(ant.year, ant.month) + ventana_pago(hoy.year, hoy.month) if p <= hoy <= fin]
    arranque = finanzas_desde(con)   # las quincenas de antes del arranque del ERP se pagaron por fuera (en el Excel): no se avisan
    if arranque: tocan = [p for p in tocan if p.isoformat() >= arranque]
    if not tocan: return False, []
    desde = (max(tocan) - datetime.timedelta(days=3)).isoformat()
    falta = [n for (n,) in con.execute("SELECT nombre FROM usuarios WHERE nomina=1 AND sueldo_mes > 0")
             if not con.execute("""SELECT 1 FROM gastos WHERE categoria='Equipo' AND subcategoria='Quincena'
                                   AND TRIM(COALESCE(proveedor,''))=? AND fecha>=?""", (n, desde)).fetchone()]
    return any(f == hoy for f in tocan), falta


def ficha_equipo(con, nombre, hoy):
    """Lo que le has pagado a una persona del equipo, y qué adelantos quedan por descontar."""
    r = con.execute("SELECT sueldo_mes FROM usuarios WHERE nombre=? AND nomina=1", (nombre,)).fetchone()
    mensual = (r[0] if r else None) or None   # el sueldo se guarda por mes; se paga en dos quincenas
    # todo lo que se le pagó a esa persona: al contador se le paga en "Impuestos y legal", no en "Equipo"
    pagos = con.execute("""SELECT g.id, g.fecha, g.monto_usd, g.monto_real, g.moneda, g.categoria, g.subcategoria,
                           g.descripcion, g.notas, cu.nombre caja,
                           (SELECT vence FROM compromisos_pagos cp WHERE cp.gasto_id=g.id) tocaba
                           FROM gastos g LEFT JOIN cuentas cu ON cu.id=g.cuenta_id
                           WHERE TRIM(COALESCE(g.proveedor,''))=? ORDER BY g.fecha DESC, g.id DESC""", (nombre,)).fetchall()
    mes = hoy.strftime("%Y-%m")
    # los adelantos se descuentan de la próxima quincena: cuentan los pedidos después de la última que se pagó
    ult_q = next((p["fecha"] for p in pagos if p["subcategoria"] == "Quincena"), None)
    adelantos = [p for p in pagos if p["subcategoria"] == "Adelanto" and (not ult_q or p["fecha"] > ult_q)]
    vence = proxima_quincena(hoy)
    # lo que no va por quincena se lleva con pagos recurrentes. Una persona puede tener varios:
    # a Víctor se le paga el sueldo semanal y además el alquiler del mes.
    comps = []
    for c_ in con.execute("""SELECT * FROM compromisos WHERE activo=1 AND TRIM(COALESCE(proveedor,''))=?
                             ORDER BY frecuencia, nombre""", (nombre,)):
        v = vencimientos(c_, hoy, hoy + datetime.timedelta(days=62))
        comps.append(dict(c_) | {"prox": v[0].isoformat() if v else None})
    quincena = round(mensual / 2, 2) if mensual else None
    diario = round(mensual / 30, 2) if mensual else 0     # el día vale el sueldo del mes entre 30
    # las faltas que se descuentan son solo las del período de la quincena que toca (Cristina, 6 oct): la del 15 cubre
    # del 1 al 15 y la de fin de mes del 16 al último día. Una falta del 30/09 es de septiembre, no de la del 15/10.
    desde_f, hasta_f = periodo_quincena(vence)
    faltas = con.execute("""SELECT * FROM faltas WHERE nombre=? AND fecha>=? AND fecha<=? ORDER BY fecha DESC""",
                         (nombre, desde_f, hasta_f)).fetchall()
    descuento = round(len(faltas) * diario, 2)
    neto = round(quincena - sum(p["monto_usd"] or 0 for p in adelantos) - descuento, 2) if quincena else None
    return {"nombre": nombre, "pagos": pagos, "mensual": mensual, "quincena": quincena,
            "comps": comps,
            "diario": diario, "faltas": faltas, "descuento": descuento, "neto": neto,
            "mes": sum(p["monto_usd"] or 0 for p in pagos if (p["fecha"] or "")[:7] == mes),
            "total": sum(p["monto_usd"] or 0 for p in pagos),
            "adelantos": adelantos, "debe_adelantos": sum(p["monto_usd"] or 0 for p in adelantos),
            "ultima_quincena": ult_q, "vence": vence.isoformat(), "dias": (vence - hoy).days}


@app.post("/equipo/falta")
def equipo_falta(request: Request, nombre: str = Form(...), fecha: str = Form(""), nota: str = Form(""), con=Depends(db)):
    """No vino: se le anota el día para descontárselo de la quincena."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    con.execute("INSERT OR IGNORE INTO faltas (nombre, fecha, nota, usuario_id) VALUES (?,?,?,?)",
                (nombre, fecha or datetime.date.today().isoformat(), nota.strip() or None, uid_de(request)))
    con.commit(); return RedirectResponse("/equipo", status_code=303)


@app.post("/equipo/falta/{fid}/borrar")
def equipo_falta_borrar(request: Request, fid: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    con.execute("DELETE FROM faltas WHERE id=?", (fid,)); con.commit()
    return RedirectResponse("/equipo", status_code=303)


@app.post("/equipo/pagar")
def equipo_pagar(request: Request, nombre: str = Form(...), que: str = Form("Quincena"), monto: str = Form(""),
                 cuenta_id: str = Form(""), fecha: str = Form(""), nota: str = Form(""), con=Depends(db)):
    """Pagarle a alguien del equipo desde su ficha: sale de la caja elegida y queda en gastos (Equipo)."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    usd = cifra(monto) if monto else 0
    cu = con.execute("SELECT * FROM cuentas WHERE id=? AND activa=1", (int(cuenta_id),)).fetchone() if cuenta_id.isdigit() else None
    if not usd or usd <= 0 or not cu: return RedirectResponse("/equipo", status_code=303)   # sin monto o sin caja no se anota
    tasa = tasa_hoy(con)["valor"] or 0
    en_bs = cu["moneda"] == "VES" and tasa
    que = que if que in ("Quincena", "Adelanto", "Bono", "Día extra") else "Quincena"
    con.execute("""INSERT INTO gastos (fecha, monto_usd, monto_real, moneda, tasa, categoria, subcategoria, descripcion, proveedor,
                   cuenta_id, usuario_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                ((fecha or "").strip() or datetime.date.today().isoformat(), round(usd, 2), round(usd * tasa, 2) if en_bs else round(usd, 2),
                 "VES" if en_bs else "USD", tasa if en_bs else None, "Equipo", que,
                 f"{que} {nombre}" + (f" · {nota.strip()}" if nota.strip() else ""), nombre, cu["id"], uid_de(request)))
    con.commit(); return RedirectResponse("/equipo", status_code=303)


# ------------------------------------------------------------------ EQUIPO: cada persona, su acceso al ERP y su nómina
# Una sola lista (la tabla usuarios): quien entra al ERP, quien está en la nómina, o las dos cosas. En el servidor se
# entra con el correo (Cloudflare manda el código) y la lista de correos que Cloudflare deja pasar se arma desde aquí.
ACCESOS = {"admin": "Administradora", "logistica": "Logística", "taller": "Taller", "despachador": "Despachador"}
QUE_VE = {
    "admin": "Todo: ventas, dinero, cajas, configuración y el equipo.",
    "logistica": "Órdenes, clientes, despachos, inventario, packs y seguimientos. Ve los cobros de cada orden, no el dinero de la empresa.",
    "taller": "Su pantalla de producción y el inventario. Nada de clientes, órdenes ni dinero.",
    "despachador": "Sus entregas, lo que se le debe y las tarifas de delivery. No ve las de los demás.",
}
CORREO_OK = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
TABLAS_DESPACHADOR = ("ordenes", "entregas_repuesto", "movimientos", "repuestos_prepagados", "pagos_despachador",
                      "viajes_agencia", "viajes_despachador")


def renombrar_despachador(con, viejo, nuevo):
    """Las entregas, los pagos y los viajes guardan el nombre del despachador: cambia en todos a la vez."""
    con.execute("UPDATE despachadores SET nombre=? WHERE nombre=?", (nuevo, viejo))
    for t in TABLAS_DESPACHADOR: con.execute(f"UPDATE {t} SET despachador=? WHERE despachador=?", (nuevo, viejo))
    con.execute("UPDATE usuarios SET despachador=? WHERE despachador=?", (nuevo, viejo))


def renombrar_en_nomina(con, viejo, nuevo):
    """Los pagos, los pagos fijos y las faltas guardan el nombre de la persona."""
    con.execute("UPDATE gastos SET proveedor=? WHERE TRIM(COALESCE(proveedor,''))=?", (nuevo, viejo))
    con.execute("UPDATE compromisos SET proveedor=? WHERE TRIM(COALESCE(proveedor,''))=?", (nuevo, viejo))
    con.execute("UPDATE faltas SET nombre=? WHERE nombre=?", (nuevo, viejo))


def anotar_acceso(con, quien_id, persona_id, que):
    con.execute("INSERT INTO accesos_registro (quien_id, persona_id, que) VALUES (?,?,?)", (quien_id, persona_id, que))


def sincronizar_cloudflare(con):
    """Le manda a Cloudflare los correos de quienes tienen acceso. Si falla, el ERP igual guarda el cambio y la
    pantalla Equipo avisa con un botón para reintentar. Sin las llaves de Cloudflare (la Mac) no hace nada."""
    if not CF_EQUIPO.configurado(): return None
    correos = [r[0] for r in con.execute("""SELECT DISTINCT lower(correo) FROM usuarios WHERE activo=1 AND correo IS NOT NULL
                                            AND rol NOT IN ('ninguno','sistema')""")]
    ahora = datetime.datetime.now().isoformat(" ", "seconds")
    try:
        CF_EQUIPO.poner_correos(correos); estado = {"ok": True, "cuando": ahora}
    except Exception as e:
        print(f"CLOUDFLARE no recibió la lista del equipo: {e}", flush=True)
        estado = {"ok": False, "cuando": ahora, "error": str(e)[:300]}
    con.execute("INSERT OR REPLACE INTO config (clave, valor) VALUES ('cloudflare_equipo', ?)", (json.dumps(estado),)); con.commit()
    return estado


def cerrar_sesiones(con, persona):
    """Lo saca ya: en la Mac borra sus sesiones; en el servidor le pide a Cloudflare que le vuelva a pedir el código."""
    con.execute("DELETE FROM sesiones WHERE usuario_id=?", (persona["id"],))
    if persona["correo"] and CF_EQUIPO.configurado():
        try: CF_EQUIPO.cerrar_sesion(persona["correo"])
        except Exception as e: print(f"CLOUDFLARE no cerró la sesión de {persona['correo']}: {e}", flush=True)


def bloqueado(con, persona):
    """¿Probó mal la clave demasiadas veces? Solo en la Mac: en el servidor no hay claves."""
    if CF_ACCESS.activo() or not persona["usuario"]: return False
    return con.execute("""SELECT COUNT(*) FROM intentos WHERE usuario=? AND cuando >= datetime('now','localtime',?)""",
                       (persona["usuario"].strip().lower(), f"-{VENTANA_INTENTOS} minutes")).fetchone()[0] >= MAX_INTENTOS


@app.get("/equipo", response_class=HTMLResponse)
def equipo(request: Request, err: str = "", abrir: str = "", con=Depends(db)):
    """Todo el equipo en una pantalla: quién entra al ERP y qué ve, y la nómina. Solo Cristina."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    hoy = datetime.date.today()
    personas = [dict(r) for r in con.execute("""SELECT * FROM usuarios WHERE rol!='sistema'
                                                ORDER BY activo DESC, CASE rol WHEN 'admin' THEN 0 WHEN 'logistica' THEN 1
                                                WHEN 'taller' THEN 2 WHEN 'despachador' THEN 3 ELSE 4 END, nombre""")]
    ahora = datetime.datetime.now()
    for p in personas:
        visto = datetime.datetime.fromisoformat(p["visto_en"]) if p["visto_en"] else None
        p["conectado"] = bool(visto and p["activo"] and ahora - visto < datetime.timedelta(minutes=10))
        p["visto"] = visto.isoformat(" ", "minutes") if visto else None
        p["bloqueado"] = p["activo"] and bloqueado(con, p)
    gente = []   # la nómina: sus pagos y sus faltas
    lunes = hoy - datetime.timedelta(days=hoy.weekday())
    for n in [p["nombre"] for p in personas if p["nomina"]]:
        f = ficha_equipo(con, n, hoy)
        fl = [dict(r) for r in con.execute("SELECT * FROM faltas WHERE nombre=? AND substr(fecha,1,4)=? ORDER BY fecha DESC", (n, str(hoy.year)))]
        f.update(faltas_anio=fl, n_sem=sum(1 for x in fl if x["fecha"] >= lunes.isoformat()),
                 n_mes=sum(1 for x in fl if x["fecha"][:7] == hoy.isoformat()[:7]),
                 id=next(p["id"] for p in personas if p["nomina"] and p["nombre"] == n))
        gente.append(f)
    nombres = {p["id"]: p["nombre"] for p in personas}
    registro = [dict(r) | {"quien": nombres.get(r["quien_id"], "—"), "persona": nombres.get(r["persona_id"], "—")}
                for r in con.execute("SELECT * FROM accesos_registro ORDER BY id DESC LIMIT 60")]
    return render(request, "equipo.html", seccion="equipo", personas=personas, gente=gente, hoy_iso=hoy.isoformat(),
                  falta_quincena=quincena_pendiente(con, hoy)[1], registro=registro, err=err, abrir=abrir,
                  yo=quien_es(request), ACCESOS=ACCESOS, QUE_VE=QUE_VE, por_correo=CF_ACCESS.activo(),
                  cloudflare=cfg_json(con, "cloudflare_equipo", None) if CF_EQUIPO.configurado() else None,
                  despachadores_l=[r[0] for r in con.execute("SELECT nombre FROM despachadores ORDER BY activo DESC, nombre")],
                  CUENTAS=con.execute("""SELECT * FROM cuentas WHERE activa=1 AND tipo='operativa' AND moneda IN ('USD','VES')
                                         ORDER BY orden""").fetchall())


@app.post("/equipo/persona")
def equipo_persona(request: Request, id: int = Form(0), nombre: str = Form(""), correo: str = Form(""),
                   acceso: str = Form("no"), despachador: str = Form(""), nomina: str = Form(""), sueldo: str = Form(""),
                   usuario: str = Form(""), clave: str = Form(""), con=Depends(db)):
    """Agregar a alguien o cambiar su ficha: nombre, correo, qué ve en el ERP (o si no entra), y su nómina."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    yo = quien_es(request)
    antes = dict(con.execute("SELECT * FROM usuarios WHERE id=? AND rol!='sistema'", (id,)).fetchone() or {}) if id else {}
    if id and not antes: return RedirectResponse("/equipo", status_code=303)
    mal = lambda e: RedirectResponse(f"/equipo?err={e}" + (f"&abrir={id}" if id else "&abrir=nuevo"), status_code=303)
    nombre, correo, usuario = capitalizar(nombre.strip()), correo.strip().lower(), usuario.strip().lower()
    entra = acceso in ACCESOS
    if not nombre: return mal("nombre")
    if correo and not CORREO_OK.fullmatch(correo): return mal("correo")
    if entra and CF_ACCESS.activo() and not correo: return mal("sin_correo")   # en el servidor se entra con el correo
    if correo and con.execute("SELECT 1 FROM usuarios WHERE lower(correo)=? AND id!=?", (correo, id)).fetchone(): return mal("correo_repetido")
    if not CF_ACCESS.activo():   # en la Mac se entra con usuario y clave
        usuario = usuario or correo
        if entra and not usuario: return mal("sin_usuario")
        if usuario and con.execute("SELECT 1 FROM usuarios WHERE lower(TRIM(usuario))=? AND id!=?", (usuario, id)).fetchone(): return mal("usuario_repetido")
        if clave.strip() and len(clave.strip()) < 8: return mal("corta")
    else:
        usuario = antes.get("usuario")
    en_nomina = bool(nomina)
    if en_nomina and con.execute("SELECT 1 FROM usuarios WHERE nombre=? AND nomina=1 AND id!=?", (nombre, id)).fetchone():
        return mal("nombre_repetido")   # la nómina va por nombre: dos con el mismo se mezclarían los pagos
    # nunca puede quedar el ERP sin administradora, ni Cristina quitarse a sí misma
    if yo and antes and yo["id"] == id and acceso != "admin": return mal("yo")
    if antes.get("rol") == "admin" and antes.get("activo") and acceso != "admin" and \
            not con.execute("SELECT 1 FROM usuarios WHERE rol='admin' AND activo=1 AND id!=?", (id,)).fetchone():
        return mal("ultima_admin")
    # cambiar el nombre: los pagos, las faltas y (si el despachador lleva su nombre) las entregas cambian con él
    if antes and antes["nombre"] != nombre:
        if antes["nomina"]: renombrar_en_nomina(con, antes["nombre"], nombre)
        if antes.get("despachador") == antes["nombre"] and \
                not con.execute("SELECT 1 FROM despachadores WHERE nombre=?", (nombre,)).fetchone():
            renombrar_despachador(con, antes["nombre"], nombre)
            if despachador.strip() == antes["nombre"]: despachador = nombre
            antes["despachador"] = nombre
    # el despachador es uno de la lista de Despachadores: el que se elige, o uno nuevo con su nombre
    desp = None
    if acceso == "despachador":
        desp = despachador.strip() or nombre
        if not con.execute("SELECT 1 FROM despachadores WHERE nombre=?", (desp,)).fetchone():
            con.execute("INSERT INTO despachadores (nombre, activo) VALUES (?,1)", (desp,))
        else:
            con.execute("UPDATE despachadores SET activo=1 WHERE nombre=?", (desp,))
    rol = acceso if entra else (antes.get("rol") if antes.get("rol") in ACCESOS else "ninguno")   # sin acceso conserva su rol de antes
    valores = (nombre, correo or None, usuario or None, rol, 1 if entra else 0, desp if entra else antes.get("despachador"),
               1 if en_nomina else 0, cifra(sueldo) if (en_nomina and sueldo.strip()) else None)
    if id:
        con.execute("""UPDATE usuarios SET nombre=?, correo=?, usuario=?, rol=?, activo=?, despachador=?, nomina=?, sueldo_mes=?
                       WHERE id=?""", valores + (id,))
    else:
        id = con.execute("""INSERT INTO usuarios (nombre, correo, usuario, rol, activo, despachador, nomina, sueldo_mes, creado_en)
                            VALUES (?,?,?,?,?,?,?,?,date('now'))""", valores).lastrowid
    yo_id = yo["id"] if yo else None
    # lo que cambió en su acceso queda anotado
    if not antes:
        anotar_acceso(con, yo_id, id, f"Lo agregó al equipo" + (f" con acceso de {ACCESOS[acceso]}" if entra else " sin acceso al ERP"))
    else:
        if antes["activo"] and not entra: anotar_acceso(con, yo_id, id, "Le quitó el acceso al ERP")
        elif entra and not antes["activo"]: anotar_acceso(con, yo_id, id, f"Le dio acceso de {ACCESOS[acceso]}")
        elif entra and antes["rol"] != acceso: anotar_acceso(con, yo_id, id, f"Le cambió el acceso de {ACCESOS.get(antes['rol'], antes['rol'])} a {ACCESOS[acceso]}")
        if (antes.get("correo") or "") != correo: anotar_acceso(con, yo_id, id, f"Cambió su correo a {correo}" if correo else "Le quitó el correo")
        if antes["nombre"] != nombre: anotar_acceso(con, yo_id, id, f"Le cambió el nombre de {antes['nombre']} a {nombre}")
    if clave.strip() and not CF_ACCESS.activo():
        con.execute("UPDATE usuarios SET clave_hash=? WHERE id=?", (cifrar_clave(clave.strip()), id))
        con.execute("DELETE FROM sesiones WHERE usuario_id=? AND ficha!=?", (id, request.cookies.get("sesion") or ""))
        anotar_acceso(con, yo_id, id, "Le puso una clave nueva")
    con.commit(); cargar_despachadores()
    # si perdió el acceso o cambió de correo, sale ya: no espera a que se le venza la sesión
    if antes and antes["activo"] and (not entra or (antes.get("correo") or "") != correo):
        cerrar_sesiones(con, antes); con.commit()
    if not antes or entra != bool(antes["activo"]) or (antes.get("correo") or "") != correo: sincronizar_cloudflare(con)
    return RedirectResponse(f"/equipo#p{id}", status_code=303)


@app.post("/equipo/persona/{pid}/cerrar-sesion")
def equipo_cerrar_sesion(request: Request, pid: int, con=Depends(db)):
    """Perdió el teléfono, o se lo prestó a alguien: tiene que volver a entrar con el código."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    p = con.execute("SELECT * FROM usuarios WHERE id=? AND rol!='sistema'", (pid,)).fetchone()
    if p:
        cerrar_sesiones(con, p); anotar_acceso(con, (quien_es(request) or {}).get("id"), pid, "Le cerró la sesión"); con.commit()
    return RedirectResponse(f"/equipo#p{pid}", status_code=303)


@app.post("/equipo/persona/{pid}/desbloquear")
def equipo_desbloquear(request: Request, pid: int, con=Depends(db)):
    """Se equivocó de clave demasiadas veces: puede volver a probar ya, sin esperar los 15 minutos."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    p = con.execute("SELECT * FROM usuarios WHERE id=?", (pid,)).fetchone()
    if p and p["usuario"]:
        con.execute("DELETE FROM intentos WHERE usuario=?", (p["usuario"].strip().lower(),))
        anotar_acceso(con, (quien_es(request) or {}).get("id"), pid, "Lo desbloqueó"); con.commit()
    return RedirectResponse(f"/equipo#p{pid}", status_code=303)


@app.post("/equipo/cloudflare")
def equipo_cloudflare(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    sincronizar_cloudflare(con)
    return RedirectResponse("/equipo", status_code=303)


@app.post("/finanzas/recurrentes/{cid}/saltar")
def recurrente_saltar(request: Request, cid: int, vence: str = Form(...), motivo: str = Form(""), con=Depends(db)):
    """Este mes no tocaba pagarlo (se lo regalaron, no hubo servicio…). Sale del recordatorio
    pero queda anotado con el motivo: no se borra, se marca."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    con.execute("INSERT OR IGNORE INTO compromisos_pagos (compromiso_id, vence, gasto_id, motivo) VALUES (?,?,NULL,?)",
                (cid, vence, motivo.strip() or "No tocaba este mes"))
    con.commit(); return RedirectResponse("/finanzas/recurrentes", status_code=303)


@app.post("/finanzas/recurrentes/{cid}/reactivar")
def recurrente_reactivar(request: Request, cid: int, vence: str = Form(...), con=Depends(db)):
    """Me equivoqué: ese pago sí tocaba. Vuelve a la lista."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    con.execute("DELETE FROM compromisos_pagos WHERE compromiso_id=? AND vence=? AND gasto_id IS NULL", (cid, vence))
    con.commit(); return RedirectResponse("/finanzas/recurrentes", status_code=303)


@app.get("/finanzas/recurrentes", response_class=HTMLResponse)
def recurrentes(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    comps = con.execute("SELECT c.*, cu.nombre cuenta FROM compromisos c LEFT JOIN cuentas cu ON cu.id=c.cuenta_id ORDER BY c.activo DESC, c.nombre").fetchall()
    cats = json.loads(con.execute("SELECT valor FROM config WHERE clave='categorias_gasto'").fetchone()[0])
    cuentas = con.execute("SELECT * FROM cuentas WHERE activa=1 ORDER BY orden").fetchall()
    hist = con.execute("""SELECT cp.*, c.nombre, g.monto_usd, cu.nombre caja FROM compromisos_pagos cp
                          JOIN compromisos c ON c.id=cp.compromiso_id LEFT JOIN gastos g ON g.id=cp.gasto_id
                          LEFT JOIN cuentas cu ON cu.id=g.cuenta_id ORDER BY cp.id DESC LIMIT 30""").fetchall()
    return render(request, "recurrentes.html", seccion="recurrentes", comps=comps, pend=pagos_pendientes(con, 14), cats=cats, cuentas=cuentas, hist=hist, DIAS_SEM=DIAS_SEM)


@app.post("/finanzas/recurrentes")
async def recurrente_crear(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    f = await request.form()
    datos = (f["nombre"].strip(), f.get("categoria") or None, f.get("subcategoria") or None, f.get("proveedor") or None, cifra(f.get("monto")) if f.get("monto") else None, f.get("moneda") or "USD",
             f["frecuencia"], int(f["dia"]) if f.get("dia") not in (None, "") and f["frecuencia"] != "quincenal" else None,
             int(f["cuenta_id"]) if f.get("cuenta_id") else None, f.get("nota") or None,
             (f.get("unidad") or "").strip() or None, cifra(f.get("precio_unitario")) or None)
    cid = int(f["id"]) if (f.get("id") or "").isdigit() else 0
    if cid:   # editar uno que ya existe: lo pagado antes no cambia, lo que viene sigue la regla nueva
        con.execute("""UPDATE compromisos SET nombre=?, categoria=?, subcategoria=?, proveedor=?, monto=?, moneda=?, frecuencia=?, dia=?,
                       cuenta_id=?, nota=?, unidad=?, precio_unitario=? WHERE id=?""", datos + (cid,))
    else:
        con.execute("""INSERT INTO compromisos (nombre, categoria, subcategoria, proveedor, monto, moneda, frecuencia, dia, cuenta_id, nota, unidad, precio_unitario)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", datos)
    con.commit(); return RedirectResponse("/finanzas/recurrentes", status_code=303)


@app.post("/finanzas/recurrentes/{cid}/pagar")
def recurrente_pagar(request: Request, cid: int, vence: str = Form(...), monto: float = Form(...), cuenta_id: str = Form(""),
                     fecha: str = Form(""), cantidad: str = Form(""), volver: str = Form("/finanzas/recurrentes"), con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    c = con.execute("SELECT * FROM compromisos WHERE id=?", (cid,)).fetchone(); tasa = tasa_hoy(con)["valor"] or 0
    if not (cuenta_id or c["cuenta_id"]): return RedirectResponse(volver, status_code=303)   # sin caja no se sabe de dónde salió
    monto_usd = round(monto / tasa, 2) if (c["moneda"] == "VES" and tasa) else monto
    cant = cifra(cantidad) or None
    desc = c["nombre"] + (f" · {cant:g} {c['unidad']}" if cant and c["unidad"] else "")
    cur = con.execute("""INSERT INTO gastos (fecha, monto_usd, monto_real, moneda, tasa, categoria, subcategoria, descripcion, proveedor, cuenta_id,
                         cantidad, unidad, recurrente, usuario_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                      ((fecha or "").strip() or datetime.date.today().isoformat(), monto_usd, monto, c["moneda"], tasa if c["moneda"] == "VES" else None, c["categoria"] or "Otros gastos", c["subcategoria"], desc, c["proveedor"],
                       int(cuenta_id) if cuenta_id else c["cuenta_id"], cant, c["unidad"], uid_de(request)))
    con.execute("INSERT OR IGNORE INTO compromisos_pagos (compromiso_id, vence, gasto_id) VALUES (?,?,?)", (cid, vence, cur.lastrowid)); con.commit()
    return RedirectResponse(volver, status_code=303)


@app.post("/finanzas/recurrentes/{cid}/toggle")
def recurrente_toggle(request: Request, cid: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    con.execute("UPDATE compromisos SET activo = 1 - activo WHERE id=?", (cid,)); con.commit(); return RedirectResponse("/finanzas/recurrentes", status_code=303)


# ------------------------------------------------------------------ PRODUCCIÓN
# Lo que se manda a producir (trabajo de carpintería). Cada pieza apunta al producto del catálogo al que pertenece;
# las cajas de madera son solo una parte de El Porche (falta grama, cartón, placa), así que al recibirlas NO entran como producto terminado.
PIEZAS_PRODUCCION = [
    # las cajas entran como insumo: el porche las descuenta de ahí al venderse (ver receta)
    ("Caja de madera mediana", "INS-CAJAM", True), ("Caja de madera grande", "INS-CAJAG", True),
    ("Comedor Mini 10 cm", "COM-10", True), ("Comedor Pequeño 15 cm", "COM-15", True), ("Comedor Mediano 20 cm", "COM-20", True),
    ("El Bar Grande 25 cm", "BAR-25", True), ("El Bar Gigante 30 cm", "BAR-30", True),
    # Walter entrega el Slow Chow sin plato; al confirmar que llegó, el taller dice cuántos van azules y cuántos rosados
    ("Slow Chow Pequeño 10 cm", "SLOW-10", True), ("Slow Chow Mediano 15 cm", "SLOW-15", True), ("Slow Chow Grande 25 cm", "SLOW-20", True), ("Slow Chow Gigante 30 cm", "SLOW-30", True),
    ("Rampa Nueva", "RAMPA-N", True), ("Rampa Para Perros Mini", "RAMPA-MINI", True),
    ("Muestra / prototipo", None, False),   # lo que hace David cuando se prueba un producto nuevo; lleva descripción y precio a mano
]
# Lo que se le compra a un proveedor y se vende tal cual: al recibirlo entra al inventario.
ITEMS_A_INVENTARIO = {
    "Bowl pequeño": "BOWL-P", "Bowl mediano": "BOWL-M", "Bowl grande": "BOWL-G",
    "Plato de alimentación lenta azul": "PLATO-AZUL", "Plato de alimentación lenta rosado": "PLATO-ROSA",
    # materiales: no se venden, pero se lleva cuánto queda (aproximado)
    "Pega amarilla": "INS-PEGA", "Cinta antideslizante": "INS-CINTA", "Tela de rampa": "INS-TELA",
    "Placas de bambú Decopet": "INS-BAMBU", "Bolsas negras": "INS-BOLSA",
}
# El plato que se le pone a un Slow Chow según su color.
PLATO_DE_COLOR = {"azul": "PLATO-AZUL", "rosado": "PLATO-ROSA"}
# Se compra en una unidad y se lleva en otra: la pega viene por cuñete o galón y se cuenta en litros.
LITROS_POR = {"cuñete": 18.9, "cunete": 18.9, "galón": 3.785, "galon": 3.785}


def a_inventario(con, r, n):
    """Cuánto entra al inventario cuando llegan n de lo que se pidió, en la unidad en que se lleva el stock.
    Solo se pasa a litros si el producto se lleva en litros; la pega por envase (cuñete, galón) entra tal cual."""
    u = con.execute("""SELECT pi.unidad FROM proveedor_items pi LEFT JOIN proveedores pv ON pv.id=pi.proveedor_id
                       WHERE pi.item=? ORDER BY (pv.nombre=?) DESC LIMIT 1""", (r["pieza"] or "", r["responsable"] or "")).fetchone()
    pid = r["producto_id"] if "producto_id" in r.keys() else None
    dest = con.execute("SELECT unidad FROM productos WHERE id=?", (pid,)).fetchone() if pid else None
    if dest and (dest[0] or "").strip().lower() not in ("litro", "litros"): return n
    f = LITROS_POR.get((u[0] or "").strip().lower()) if u else None
    return round(n * f, 2) if f else n


def sku_pega(con, pieza, quien):
    """La pega va al envase en que se compra: cuñete, galón o ¼ de galón."""
    u = con.execute("""SELECT pi.unidad FROM proveedor_items pi LEFT JOIN proveedores pv ON pv.id=pi.proveedor_id
                       WHERE pi.item=? ORDER BY (pv.nombre=?) DESC LIMIT 1""", (pieza, quien or "")).fetchone()
    u = (u[0] if u else "").strip().lower()
    sku = "INS-PEGA-14" if ("1/4" in u or "¼" in u or "cuarto" in u) else ("INS-PEGA-GAL" if u.startswith("gal") else "INS-PEGA-CUN")
    return sku if con.execute("SELECT 1 FROM productos WHERE sku=? AND activo=1", (sku,)).fetchone() else "INS-PEGA"
# nombre de la pieza → ítem del proveedor (para sacar el precio de Taller › Proveedores)
PIEZA_ITEM = {"Caja de madera mediana": "Caja de madera mediana", "Caja de madera grande": "Caja de madera grande",
              "Rampa Nueva": "Rampa Nueva", "Rampa Para Perros Mini": "Rampa Para Perros Mini"}

def item_de_pieza(con, pieza):
    """Con qué ítem del catálogo del proveedor se cobra esa pieza (comedores y slow chow usan 'Comedores')."""
    item = PIEZA_ITEM.get(pieza, "Comedores (todos los tamaños)" if pieza.startswith(("Comedor", "El Bar", "Slow Chow")) else None)
    if not item and con.execute("SELECT 1 FROM proveedor_items WHERE item=?", (pieza,)).fetchone(): item = pieza
    return item


def guardar_precio(con, pieza, responsable, precio):
    """Cambió el precio: queda como el nuevo del catálogo de ese proveedor para los próximos pedidos."""
    item = item_de_pieza(con, pieza)
    if not item or not responsable or precio is None or precio <= 0: return
    con.execute("""UPDATE proveedor_items SET precio=? WHERE item=? AND proveedor_id=(SELECT id FROM proveedores WHERE nombre=?)""",
                (round(precio, 2), item, responsable))


def precio_pieza(con, pieza, responsable):
    """Precio unitario que cobra el carpintero por esa pieza (comedores y slow chow usan 'Comedores')."""
    item = item_de_pieza(con, pieza)
    if not item: return None
    def precio(it):
        r = con.execute("""SELECT i.precio FROM proveedor_items i JOIN proveedores p ON p.id=i.proveedor_id
                           WHERE i.item=? AND (p.nombre=? OR ?='') ORDER BY p.nombre=? DESC LIMIT 1""", (it, responsable or "", responsable or "", responsable or "")).fetchone()
        return r["precio"] if r else None
    return precio(item)

def precio_barnizado(con, responsable):
    r = con.execute("""SELECT i.precio FROM proveedor_items i JOIN proveedores p ON p.id=i.proveedor_id WHERE i.item='Barnizado de caja' AND (p.nombre=? OR ?='') LIMIT 1""", (responsable or "", responsable or "")).fetchone()
    return r["precio"] if r else 0

def volver_produccion(con, pid, extra=""):
    """Después de pagar, recibir o editar, de vuelta a la pantalla de donde vino: Producción o Pedidos a proveedores."""
    r = con.execute("SELECT COALESCE(tipo_pedido,'produccion') t FROM produccion WHERE id=?", (pid,)).fetchone()
    return RedirectResponse(f"/produccion?tipo={r['t'] if r else 'produccion'}{extra}", status_code=303)


def unidad_pedido(con, pr):
    """En qué se pidió: cuñete, rollo, metro… Lo dice el catálogo del proveedor; si no, unidades."""
    u = con.execute("""SELECT pi.unidad FROM proveedor_items pi LEFT JOIN proveedores pv ON pv.id=pi.proveedor_id
                       WHERE pi.item=? ORDER BY (pv.nombre=?) DESC LIMIT 1""", (pr["pieza"] or "", pr["responsable"] or "")).fetchone()
    return ((u[0] if u else None) or "unidad").split("(")[0].strip().lower() or "unidad"   # "caja (tapa y fondo)" → caja


PROVEEDORES_MADERA = ("Walter", "David")   # lo que se manda a hacer; el resto son pedidos a proveedores
OTRO = "Otro"   # en Pedidos a proveedores: algo que no está en el catálogo del proveedor


def tipo_de_proveedor(nombre):
    return "produccion" if (nombre or "") in PROVEEDORES_MADERA else "proveedor"


@app.get("/produccion", response_class=HTMLResponse)
def produccion(request: Request, ver: str = "en_proceso", q: str = "", debe: str = "", tipo: str = "produccion",
               mes: str = "", semana: str = "", anio: str = "", con=Depends(db)):
    """Dos pantallas con la misma mecánica (pedir, pagar, recibir):
      · produccion: la madera que se manda a hacer (Walter, David)
      · proveedor:  lo que se le compra a un proveedor (pega, cinta, tela, placas, bolsas…)"""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)   # Taller es de Cristina
    tipo = "proveedor" if tipo == "proveedor" else "produccion"
    if (q or debe) and ver == "en_proceso": ver = "todas"   # al buscar, o al venir de "le debes a X", se mira todo
    saldo_sql = "pr.costo - COALESCE((SELECT SUM(a.monto) FROM abonos_produccion a WHERE a.produccion_id=pr.id),0)"
    es_tipo = "COALESCE(pr.tipo_pedido,'produccion')=?"
    rows = con.execute(f"""SELECT pr.*, COALESCE(pr.pieza, p.nombre) producto, p.requiere_color FROM produccion pr LEFT JOIN productos p ON p.id=pr.producto_id
                          WHERE {es_tipo} AND (?='todas' OR pr.estado=?)
                          AND (?='' OR pr.pieza LIKE ? OR pr.responsable LIKE ? OR pr.descripcion LIKE ? OR pr.nota LIKE ?)
                          AND (?='' OR (pr.responsable=? AND pr.estado!='cancelado' AND pr.costo IS NOT NULL AND {saldo_sql} > 0.009))
                          ORDER BY COALESCE(pr.fecha_esperada, pr.fecha_pedido), pr.id""",
                       (tipo, ver, ver, q, f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", debe, debe)).fetchall()
    n = {r[0]: r[1] for r in con.execute(f"SELECT estado, COUNT(*) FROM produccion pr WHERE {es_tipo} GROUP BY 1", (tipo,))}
    por_pagar = con.execute(f"""SELECT COALESCE(pr.responsable,'—') quien, SUM({saldo_sql}) monto, COUNT(*) n
                               FROM produccion pr WHERE {es_tipo} AND pr.estado!='cancelado' AND pr.costo IS NOT NULL
                               AND {saldo_sql} > 0.009 GROUP BY 1 ORDER BY monto DESC""", (tipo,)).fetchall()
    rows = [dict(r) | {"abonado": con.execute("SELECT COALESCE(SUM(monto),0) FROM abonos_produccion WHERE produccion_id=?", (r["id"],)).fetchone()[0],
                       "abonos": con.execute("SELECT * FROM abonos_produccion WHERE produccion_id=? ORDER BY fecha, id", (r["id"],)).fetchall()} for r in rows]
    # por mes y semana, como en Gastos: la semana 1 son los días 1 al 7, la 2 del 8 al 14…
    hoy_d = datetime.date.today()
    anios = sorted({int(r[0]) for r in con.execute("SELECT DISTINCT substr(fecha_pedido,1,4) FROM produccion WHERE fecha_pedido IS NOT NULL")} | {hoy_d.year}, reverse=True)
    anio = int(mes[:4]) if mes else (int(anio) if anio.isdigit() else hoy_d.year)
    n_sem = lambda f: (datetime.date.fromisoformat(f).day - 1) // 7 + 1
    if mes: rows = [r for r in rows if (r["fecha_pedido"] or "")[:7] == mes]
    if mes and semana.isdigit(): rows = [r for r in rows if n_sem(r["fecha_pedido"]) == int(semana)]
    rows.sort(key=lambda r: (r["fecha_pedido"] or "", r["id"]), reverse=True)   # lo más nuevo arriba
    grupos = []
    for r in rows:
        d = datetime.date.fromisoformat(r["fecha_pedido"]); ns = n_sem(r["fecha_pedido"])
        if not grupos or grupos[-1]["clave"] != (d.year, d.month, ns):
            grupos.append({"clave": (d.year, d.month, ns), "n": ns, "desde": d.replace(day=(ns - 1) * 7 + 1),
                           "mes": MESES_N[d.month - 1] + (f" {d.year}" if d.year != hoy_d.year else ""), "rows": [], "total": 0.0})
        grupos[-1]["rows"].append(r); grupos[-1]["total"] += r["costo"] or 0
    quienes =[r[0] for r in con.execute("SELECT nombre FROM proveedores WHERE activo=1 ORDER BY (nombre='Walter') DESC, nombre")
               if (r[0] in PROVEEDORES_MADERA) == (tipo == "produccion")]
    if tipo == "produccion":
        piezas = [p[0] for p in PIEZAS_PRODUCCION]
    else:   # lo que venden los proveedores (su catálogo en Taller › Proveedores)
        piezas = [r[0] for r in con.execute("""SELECT DISTINCT i.item FROM proveedor_items i JOIN proveedores p ON p.id=i.proveedor_id
                                                WHERE p.activo=1 AND p.nombre NOT IN (?,?) ORDER BY i.item""", PROVEEDORES_MADERA)] + [OTRO]
    precios = {c: {z: precio_pieza(con, z, c) for z in piezas} | {"__barnizado": precio_barnizado(con, c)} for c in quienes}
    # qué vende cada proveedor, para que al elegirlo solo salga lo suyo (la madera se deja igual: son piezas, no su catálogo)
    vende = {} if tipo == "produccion" else {
        c: [r[0] for r in con.execute("""SELECT i.item FROM proveedor_items i JOIN proveedores p ON p.id=i.proveedor_id
                                         WHERE p.nombre=? ORDER BY i.item""", (c,))] for c in quienes}
    return render(request, "produccion.html", seccion="produccion" if tipo == "produccion" else "compras", tipo=tipo, rows=rows, piezas=piezas, vende=vende,
                  grupos=grupos, mes=mes, semana=semana, anio=anio, anios=anios, MESES_N=MESES_N,
                  carpinteros=quienes, ver=ver, n=n, debe=debe, por_pagar=por_pagar, FORMAS_PAGO=FORMAS_PAGO, precios=precios)


@app.post("/produccion")
async def produccion_crear(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    """Un pedido puede traer varios productos (a Walter le pides comedores y cajas a la vez).
    Cada producto queda como su propia línea, porque se recibe y se paga por separado."""
    f = await request.form()
    piezas_ok = {p[0] for p in PIEZAS_PRODUCCION} | {r[0] for r in con.execute("SELECT DISTINCT item FROM proveedor_items")} | {OTRO}
    quien = (f.get("responsable") or "").strip() or None
    fped = f.get("fecha_pedido") or datetime.date.today().isoformat()
    fesp = f.get("fecha_esperada") or None
    uid = uid_de(request)
    descs = f.getlist("descripcion"); barns = f.getlist("barnizado")
    precio_nuevo = set(f.getlist("precio_nuevo"))   # líneas donde Cristina marcó "guardar como precio nuevo"
    lineas, i_barn = [], 0
    for idx, (pieza, cant, costo_l) in enumerate(zip(f.getlist("pieza"), f.getlist("cantidad"), f.getlist("costo_linea"))):
        if pieza not in piezas_ok or not (cant or "").strip(): continue
        cantidad = int(cifra(cant))
        if cantidad <= 0: continue
        if pieza == OTRO:   # algo que no está en el catálogo: el pedido se llama como lo escribió
            escrito = (descs[idx] if idx < len(descs) else "").strip()
            if not escrito: continue
            lineas.append((escrito, cantidad, cifra(costo_l) or None, 0, None)); continue
        barn = 1 if (pieza.startswith("Caja de madera") and barns and len(barns) > i_barn) else 0
        if pieza.startswith("Caja de madera"): i_barn += 1
        costo = cifra(costo_l) or None
        if costo is None:   # sin monto escrito: cantidad × precio del proveedor (+ barnizado por caja)
            pu = precio_pieza(con, pieza, quien)
            if pu is not None: costo = round((pu + (precio_barnizado(con, quien) if barn else 0)) * cantidad, 2)
        elif str(idx) in precio_nuevo:
            guardar_precio(con, pieza, quien, costo / cantidad - (precio_barnizado(con, quien) if barn else 0))
        lineas.append((pieza, cantidad, costo, barn, (descs[idx].strip() if idx < len(descs) and descs[idx] else None)))
    if not lineas: return RedirectResponse("/produccion", status_code=303)

    ids = []
    for pieza, cantidad, costo, barn, desc in lineas:
        sku = next((s_ for (nom, s_, _) in PIEZAS_PRODUCCION if nom == pieza), None) or ITEMS_A_INVENTARIO.get(pieza)
        if sku == "INS-PEGA": sku = sku_pega(con, pieza, quien)
        pid_prod = con.execute("SELECT id FROM productos WHERE sku=?", (sku,)).fetchone()[0] if sku else None
        cur = con.execute("""INSERT INTO produccion (producto_id, pieza, cantidad, fecha_pedido, fecha_esperada, responsable, costo, nota, usuario_id, barnizado, descripcion, tipo_pedido, fecha_pago)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (pid_prod, pieza, cantidad, fped, fesp, quien, costo, f.get("nota") or None, uid, barn, desc, tipo_de_proveedor(quien),
                           f.get("fecha_pago") or None))
        ids.append((cur.lastrowid, costo or 0))

    ab = cifra(f.get("abono_monto"))
    if ab > 0:   # el abono se reparte entre las líneas, en proporción a lo que cuesta cada una
        total = sum(c for _, c in ids) or 1
        repartido = 0.0
        for k, (pid_l, costo_l) in enumerate(ids):
            monto = round(ab - repartido, 2) if k == len(ids) - 1 else round(ab * costo_l / total, 2)
            repartido += monto
            if monto > 0:
                pagar_produccion(con, pid_l, monto, f.get("abono_forma"), f.get("abono_fecha") or fped, "abono al hacer el pedido", uid)
    con.commit(); return RedirectResponse(f"/produccion?tipo={tipo_de_proveedor(quien)}", status_code=303)


def llegada_repetida(con, pid, uid):
    """¿Se acaba de anotar una llegada de este mismo pedido? Un doble clic manda el formulario dos veces."""
    return bool(con.execute("""SELECT 1 FROM mov_inventario WHERE nota LIKE ? AND usuario_id IS ? AND tipo='entrada'
                               AND creado_en >= datetime('now','localtime','-8 seconds')""", (f"producción #{pid} %", uid)).fetchone())


def entrar_al_inventario(con, r, n, nota, uid, colores=None):
    """Lo que llegó de un pedido entra al inventario. Si el producto va por color (Slow Chow),
    entra separado por color y cada uno se lleva su plato."""
    hoy = datetime.date.today().isoformat()
    terminado = next((ok for (nom, _, ok) in PIEZAS_PRODUCCION if nom == (r["pieza"] or "")), True)
    if not (terminado and r["producto_id"]): return
    if colores:
        for col, k in colores.items():
            if k <= 0: continue
            con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, color, nota, usuario_id) VALUES (?,?,?,?,?,?,?)",
                        (r["producto_id"], hoy, "entrada", k, col, nota, uid))
            plato = con.execute("SELECT id FROM productos WHERE sku=?", (PLATO_DE_COLOR[col],)).fetchone()
            if plato: con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, nota, usuario_id) VALUES (?,?,?,?,?,?)",
                                  (plato["id"], hoy, "salida", -k, f"puesto en {k}× {r['pieza']}", uid))
    else:
        con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, nota, usuario_id) VALUES (?,?,?,?,?,?)",
                    (r["producto_id"], hoy, "entrada", a_inventario(con, r, n), nota, uid))


def colores_de(con, r, azul, rosado):
    """Si lo que llegó va por color, cuántos de cada uno; si no, None."""
    if not r["producto_id"]: return None
    p = con.execute("SELECT requiere_color FROM productos WHERE id=?", (r["producto_id"],)).fetchone()
    if not (p and p["requiere_color"]): return None
    return {"azul": int(cifra(azul) or 0), "rosado": int(cifra(rosado) or 0)}


@app.post("/produccion/{pid}/recibir")
def produccion_recibir(request: Request, pid: int, cantidad: str = Form("0"), azul: str = Form(""), rosado: str = Form(""), con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)   # Taller es de Cristina
    r = con.execute("SELECT * FROM produccion WHERE id=?", (pid,)).fetchone()
    col = colores_de(con, r, azul, rosado) if r else None
    cantidad = sum(col.values()) if col else int(cifra(cantidad) or 0)
    if r and cantidad > 0 and not llegada_repetida(con, pid, uid_de(request)):
        hoy = datetime.date.today().isoformat(); uid = uid_de(request)
        # comedores, rampas y cajas entran al inventario; las muestras no
        entrar_al_inventario(con, r, cantidad, f"producción #{pid}" + (f" · {r['responsable']}" if r["responsable"] else ""), uid, col)
        total = r["recibido"] + cantidad
        con.execute("UPDATE produccion SET recibido=?, estado=?, recibido_en=? WHERE id=?", (total, "recibido" if total >= r["cantidad"] else "en_proceso", hoy if total >= r["cantidad"] else None, pid))
        con.commit()
    return volver_produccion(con, pid)


def pagar_produccion(con, pid, monto, forma, fecha, nota, uid):
    """Le pagas (todo o una parte) a un proveedor por un pedido. Crea el gasto, entra al libro de caja y baja el saldo.
    Lo usan las dos puertas: el abono al crear el pedido y el botón Pagar de la lista."""
    pr = con.execute("SELECT * FROM produccion WHERE id=?", (pid,)).fetchone()
    if not pr or monto <= 0: return
    categoria = cfg_json(con, "categoria_por_proveedor", {}).get(pr["responsable"] or "", "Proveedores")
    cuenta = con.execute("SELECT id FROM cuentas WHERE nombre=? AND activa=1", (FORMA_CUENTA.get(forma or ""),)).fetchone()
    ya = con.execute("SELECT COALESCE(SUM(monto),0) FROM abonos_produccion WHERE produccion_id=?", (pid,)).fetchone()[0]
    queda = (pr["costo"] or 0) - ya - monto
    # el gasto deja escrito el total del pedido, lo pagado y lo que falta: así se entiende sin abrir Producción
    total = pr["costo"] or 0
    detalle = (f"Abono · pedido {fmt_usd(total)} · pagado {fmt_usd(ya + monto)} · quedan {fmt_usd(queda)}"
               if queda > 0.009 else f"Pago completo · pedido {fmt_usd(total)}")
    if nota: detalle += f" · {nota}"
    # el gasto tiene que decir lo que de verdad llegó, no lo que se pidió: si llegaron 8 de 10, son 8
    recibido = int(pr["recibido"] or 0); pedido = int(pr["cantidad"] or 0)
    en = fmt_cant(pedido, unidad_pedido(con, pr))    # "20 unidades", "2 cuñetes", "1 rollo"
    if queda <= 0.009:
        # con este pago queda saldado: no es un adelanto, llegue o no la mercancía
        cant_gasto = recibido or None
        desc = f"{pr['pieza']} · " + ("pago final" if ya > 0.009 else "pago") + f" de pedido {en}"
    elif recibido:
        cant_gasto = recibido
        desc = f"{pr['pieza']} · {recibido}" + (f" recibidos de {pedido}" if recibido != pedido else "")
    else:
        cant_gasto = None          # adelanto: todavía no ha llegado nada, así que no se cuenta cantidad
        desc = f"{pr['pieza']} · adelanto de pedido {en}"
    cur = con.execute("""INSERT INTO gastos (fecha, monto_usd, monto_real, moneda, categoria, subcategoria, descripcion, proveedor,
                         cantidad, cuenta_id, notas, usuario_id) VALUES (?,?,?,'USD',?,?,?,?,?,?,?,?)""",
                      (fecha, monto, monto, categoria, pr["pieza"], desc, pr["responsable"], cant_gasto,
                       cuenta["id"] if cuenta else None, detalle, uid))
    con.execute("INSERT INTO abonos_produccion (produccion_id, fecha, monto, forma, nota, usuario_id, gasto_id) VALUES (?,?,?,?,?,?,?)",
                (pid, fecha, monto, forma or None, nota or None, uid, cur.lastrowid))
    con.execute("UPDATE produccion SET pagado=? WHERE id=?", (1 if queda <= 0.009 else 0, pid))


@app.post("/produccion/{pid}/abonar")
def produccion_abonar(request: Request, pid: int, monto: str = Form(...), forma: str = Form(""), fecha: str = Form(""), nota: str = Form(""), con=Depends(db)):
    """Le pagaste al proveedor por este pedido (puede ser el total o una parte)."""
    if not solo_admin(request): return RedirectResponse("/produccion", status_code=303)
    m = cifra(monto)
    if m > 0:
        pagar_produccion(con, pid, m, forma, fecha or datetime.date.today().isoformat(), nota, uid_de(request))
        con.commit()
    return volver_produccion(con, pid, "&ver=todas")


@app.post("/produccion/{pid}/editar")
async def produccion_editar(request: Request, pid: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/produccion", status_code=303)
    f = await request.form(); g = lambda k: (f.get(k) or "").strip() or None
    con.execute("""UPDATE produccion SET cantidad=?, responsable=?, fecha_pedido=?, fecha_esperada=?, costo=?, nota=?, barnizado=?, descripcion=? WHERE id=?""",
                (int(f.get("cantidad") or 1), g("responsable"), g("fecha_pedido") or datetime.date.today().isoformat(), g("fecha_esperada"),
                 float(f["costo"].replace(",", ".")) if g("costo") else None, g("nota"), 1 if f.get("barnizado") == "1" else 0, g("descripcion"), pid))
    r = con.execute("SELECT costo, (SELECT COALESCE(SUM(monto),0) FROM abonos_produccion WHERE produccion_id=?) ab FROM produccion WHERE id=?", (pid, pid)).fetchone()
    con.execute("UPDATE produccion SET pagado=? WHERE id=?", (1 if r["costo"] is not None and r["ab"] >= r["costo"] - 0.009 else 0, pid))
    con.commit(); return volver_produccion(con, pid, "&ver=todas")


@app.post("/produccion/abono/{aid}/borrar")
def produccion_abono_borrar(request: Request, aid: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/produccion", status_code=303)
    r = con.execute("SELECT produccion_id FROM abonos_produccion WHERE id=?", (aid,)).fetchone()
    if r:
        con.execute("DELETE FROM abonos_produccion WHERE id=?", (aid,)); con.execute("UPDATE produccion SET pagado=0 WHERE id=?", (r["produccion_id"],)); con.commit()
        return volver_produccion(con, r["produccion_id"], "&ver=todas")
    return RedirectResponse("/produccion?ver=todas", status_code=303)


@app.post("/produccion/{pid}/deshacer")
def produccion_deshacer(request: Request, pid: int, con=Depends(db)):
    """Se marcó recibido por error: vuelve a 'en proceso' con lo recibido en 0 y borra las entradas de inventario que generó."""
    if not solo_admin(request): return RedirectResponse("/produccion", status_code=303)
    con.execute("DELETE FROM mov_inventario WHERE nota LIKE ?", (f"producción #{pid}%",))
    con.execute("UPDATE produccion SET recibido=0, estado='en_proceso', recibido_en=NULL WHERE id=? AND estado!='cancelado'", (pid,))
    con.commit(); return volver_produccion(con, pid)


@app.post("/produccion/{pid}/cerrar")
def produccion_cerrar(request: Request, pid: int, con=Depends(db)):
    """Llegaron menos de los que pediste y no van a mandar el resto: el pedido se cierra con lo que llegó."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    r = con.execute("SELECT cantidad, recibido, costo FROM produccion WHERE id=?", (pid,)).fetchone()
    if r and (r["recibido"] or 0) > 0 and r["recibido"] < r["cantidad"]:
        # el costo baja en proporción a lo que de verdad llegó, para no quedar debiendo lo que no te mandaron
        unit = (r["costo"] or 0) / r["cantidad"] if r["cantidad"] else 0
        con.execute("UPDATE produccion SET cantidad=?, costo=?, estado='recibido', recibido_en=?, faltaron=? WHERE id=?",
                    (r["recibido"], round(unit * r["recibido"], 2), datetime.date.today().isoformat(), r["cantidad"] - r["recibido"], pid))
        con.commit()
    return volver_produccion(con, pid)


def proveedores_que_deben(con):
    """Pedidos donde se pagó más de lo que llegó: el proveedor te debe la diferencia hasta que te la devuelva."""
    return [dict(r) for r in con.execute("""SELECT pr.id, COALESCE(pr.pieza, p.nombre) pieza, pr.responsable, pr.faltaron,
                COALESCE((SELECT SUM(a.monto) FROM abonos_produccion a WHERE a.produccion_id=pr.id),0) - pr.costo debe
                FROM produccion pr LEFT JOIN productos p ON p.id=pr.producto_id
                WHERE pr.estado!='cancelado' AND pr.costo IS NOT NULL
                  AND COALESCE((SELECT SUM(a.monto) FROM abonos_produccion a WHERE a.produccion_id=pr.id),0) - pr.costo > 0.009
                ORDER BY pr.id""")]


@app.post("/produccion/{pid}/devolucion")
def produccion_devolucion(request: Request, pid: int, monto: str = Form(...), forma: str = Form(""), fecha: str = Form(""),
                          volver: str = Form(""), con=Depends(db)):
    """El proveedor te devolvió lo que pagaste de más. Entra a la caja que elijas y el gasto del pedido baja."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    pr = con.execute("SELECT * FROM produccion WHERE id=?", (pid,)).fetchone()
    m = cifra(monto)
    if pr and m > 0:
        f = fecha or datetime.date.today().isoformat(); uid = uid_de(request)
        cuenta = con.execute("SELECT id FROM cuentas WHERE nombre=? AND activa=1", (FORMA_CUENTA.get(forma or ""),)).fetchone()
        categoria = cfg_json(con, "categoria_por_proveedor", {}).get(pr["responsable"] or "", "Proveedores")
        falta = f"{pr['faltaron']} no entregad{'a' if pr['faltaron'] == 1 else 'as'}" if pr["faltaron"] else "pagado de más"
        # un gasto en negativo: baja lo gastado en ese pedido y en Cash flow entra como plata que vuelve
        cur = con.execute("""INSERT INTO gastos (fecha, monto_usd, monto_real, moneda, categoria, subcategoria, descripcion, proveedor,
                             cuenta_id, notas, usuario_id) VALUES (?,?,?,'USD',?,?,?,?,?,?,?)""",
                          (f, -m, -m, categoria, pr["pieza"], f"{pr['pieza']} · devolución ({falta})", pr["responsable"],
                           cuenta["id"] if cuenta else None, f"Te devolvió {fmt_usd(m)}", uid))
        con.execute("INSERT INTO abonos_produccion (produccion_id, fecha, monto, forma, nota, usuario_id, gasto_id) VALUES (?,?,?,?,?,?,?)",
                    (pid, f, -m, forma or None, "devolución", uid, cur.lastrowid))
        con.commit()
    if volver.startswith("/"): return RedirectResponse(volver, status_code=303)
    return volver_produccion(con, pid, "&ver=todas")


@app.post("/produccion/{pid}/cancelar")
def produccion_cancelar(request: Request, pid: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    con.execute("UPDATE produccion SET estado='cancelado' WHERE id=?", (pid,)); con.commit(); return volver_produccion(con, pid)


# ------------------------------------------------------------------ SEGUIMIENTOS (El Porche)
RESULTADOS = {"compro": "Compró", "mensaje": "Mensaje enviado", "fecha": "Lo quiere otro día", "ya_no_usa": "Ya no lo usa", "no_se_adapto": "El perro no se adaptó", "felicitado": "Felicitado", "pago": "Pagó",
              "paso_pro": "Se pasó a la Versión PRO", "otro_basico": "Compró otro Básico", "no_le_interesa": "No le interesa por ahora",
              "lo_pensara": "Lo pensará", "no_responde": "No responde", "otro": "Otro"}   # los 3 últimos: solo para leer registros viejos
# qué opciones se ofrecen según el tipo de seguimiento
RESULTADOS_POR_TIPO = {"primer_repuesto": ["compro", "mensaje", "fecha", "no_se_adapto", "ya_no_usa"], "cumple": ["felicitado"], "cobro": ["pago", "mensaje"], "basico": ["paso_pro", "otro_basico", "mensaje", "fecha", "no_le_interesa"], "*": ["compro", "mensaje", "fecha", "ya_no_usa"]}
REINTENTO_DIAS = 3   # "Mensaje enviado" sin respuesta → vuelve a aparecer a los 3 días

def estado_resultado(r):
    """Texto del resultado elegido en Contactado: 'Mensaje enviado', 'Compró', 'Lo quiere el jue 25/09'..."""
    if r["resultado"] == "fecha" and r["posponer_hasta"]: return f"Lo quiere el {fmt_dia(r['posponer_hasta'])}"
    return RESULTADOS.get(r["resultado"], r["resultado"] or "Hecho")

def estado_seg(r, hoy):
    """Texto de estado de un seguimiento que vuelve a salir: 'Pendiente', '2º intento' o 'Lo quiere hoy / para el jue 25/09'."""
    if not r: return "Pendiente"
    if r["resultado"] == "fecha" and r["posponer_hasta"]:
        return "Lo quiere hoy" if r["posponer_hasta"] == hoy.isoformat() else f"Lo quería el {fmt_dia(r['posponer_hasta'])}"
    return f"{r['intentos'] + 1}º intento"

def seguimientos_pendientes(con, umbral=None, ventana=1):
    """Seguimientos personalizados: cada miembro PRO se activa según SU fecha de entrega (del porche o del último repuesto)
    más su intervalo propio (cada cuánto compra repuesto; si no hay historial, 21 días). Es 'hoy' solo el día exacto
    (Cristina, 3 oct 2026); desde el día siguiente, si no se contactó, pasa a 'atrasado' (se ve en Seguimientos, no en Inicio)."""
    if umbral is None: umbral = CICLO_REPUESTO
    hoy = datetime.date.today(); h = hoy.isoformat(); out = []
    hechos = {r["clave"]: r for r in con.execute("SELECT * FROM seguimientos")}
    def vivo(clave):
        r = hechos.get(clave); return (r is None) or (r["posponer_hasta"] and r["posponer_hasta"] <= h)
    for m in con.execute("""SELECT c.id, c.nombre, c.telefono,
        MAX(CASE WHEN p.sku LIKE 'PRO-%' THEN COALESCE(o.fecha_entrega, substr(o.creado_en,1,10)) END) porche,
        MAX(CASE WHEN p.sku='PRO-G' THEN 'Grande' WHEN p.sku='PRO-M' THEN 'Mediano' END) tamano,
        MAX(CASE WHEN p.categoria='repuesto' THEN COALESCE(o.fecha_entrega, substr(o.creado_en,1,10)) END) ult_rep
        FROM clientes c JOIN ordenes o ON o.cliente_id=c.id AND o.estado!='cancelada' JOIN orden_lineas l ON l.orden_id=o.id JOIN productos p ON p.id=l.producto_id
        GROUP BY c.id HAVING porche IS NOT NULL"""):
        # fechas de repuesto (compras + retiros de pack) → ritmo propio
        # Lo que cuenta es el día que el cliente RECIBIÓ un repuesto, no el día que lo pagó.
        # Por eso las líneas que quedaron prepagadas se excluyen aquí y entran abajo con su fecha de entrega.
        fechas = [r[0][:10] for r in con.execute("""SELECT DISTINCT COALESCE(o.fecha_entrega, substr(o.creado_en,1,10)) FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id JOIN productos p ON p.id=l.producto_id
                                                    WHERE o.cliente_id=? AND o.estado!='cancelada' AND p.categoria='repuesto'
                                                      AND NOT EXISTS (SELECT 1 FROM repuestos_prepagados rp WHERE rp.linea_id=l.id)""", (m["id"],))]
        fechas += [r[0][:10] for r in con.execute("SELECT e.fecha FROM entregas_repuesto e JOIN packs k ON k.id=e.pack_id WHERE k.cliente_id=?", (m["id"],))]
        fechas += [r[0][:10] for r in con.execute("SELECT entregado_en FROM repuestos_prepagados WHERE cliente_id=? AND entregado_en IS NOT NULL", (m["id"],))]
        # A propósito NO se mira el Registro de ventas: ese es el librito histórico y cruza por
        # nombre escrito, que puede no coincidir. El ritmo sale solo de las órdenes y los packs,
        # donde el cliente es el mismo registro.
        ritmo, _ = ritmo_cliente(fechas)
        intervalo = ciclo_de(ritmo)
        saldo_pack = con.execute("SELECT COALESCE(SUM(k.unidades - (CASE WHEN k.orden_id IS NULL OR (SELECT o_.estado FROM ordenes o_ WHERE o_.id=k.orden_id)='entregada' THEN k.entregadas_inicio ELSE 0 END) - (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=k.id)),0) FROM packs k WHERE k.cliente_id=?", (m["id"],)).fetchone()[0]
        ult = max(fechas) if fechas else None
        ref = max([x for x in (ult, m["porche"]) if x]); dias = (hoy - datetime.date.fromisoformat(ref[:10])).days
        if dias < intervalo or dias > 120: continue
        primero = ult is None or ult < m["porche"]
        clave = f"seg:{m['id']}:{ref[:10]}"
        if not vivo(clave): continue
        prepag = con.execute("SELECT COUNT(*) FROM repuestos_prepagados WHERE cliente_id=? AND entregado_en IS NULL", (m["id"],)).fetchone()[0]
        atraso = dias - intervalo
        out.append(dict(cliente_id=m["id"], cliente=m["nombre"], telefono=m["telefono"], tamano=m["tamano"] or "—", clave=clave, dias=dias, intervalo=intervalo, atraso=atraso,
                        fase=("hoy" if atraso < ventana else "atrasado"),
                        tipo="primer_repuesto" if primero else ("prepagado" if prepag else "repuesto"),
                        tipo_txt=("Le toca primer repuesto" if primero else (f"Tiene {prepag} repuesto{'s' if prepag > 1 else ''} pagado · coordinar entrega" if prepag else ("Le toca repuesto del pack" if saldo_pack > 0 else "Le toca repuesto"))) + (f" · cada {intervalo} d" if intervalo != CICLO_REPUESTO else ""),
                        estado=estado_seg(hechos.get(clave), hoy)))
    for s in out: s["que"] = f"Repuesto {s['tamano']}"
    out.sort(key=lambda s: (s["fase"] != "hoy", s["atraso"]))
    # Porche Básico: no compra repuestos. A los 21 días se le pregunta cómo le fue y se le ofrece pasar a la Versión PRO.
    for b in con.execute("""SELECT c.id, c.nombre, c.telefono,
        MAX(CASE WHEN p.sku LIKE 'BAS%' THEN COALESCE(o.fecha_entrega, substr(o.creado_en,1,10)) END) basico,
        MAX(CASE WHEN p.sku LIKE 'PRO-%' THEN 1 ELSE 0 END) tiene_pro
        FROM clientes c JOIN ordenes o ON o.cliente_id=c.id AND o.estado!='cancelada' JOIN orden_lineas l ON l.orden_id=o.id JOIN productos p ON p.id=l.producto_id
        GROUP BY c.id HAVING basico IS NOT NULL AND tiene_pro=0"""):
        dias = (hoy - datetime.date.fromisoformat(b["basico"][:10])).days
        if dias < CICLO_REPUESTO or dias > 120: continue
        clave = f"basico:{b['id']}:{b['basico'][:10]}"
        if not vivo(clave): continue
        atraso = dias - CICLO_REPUESTO
        out.append(dict(cliente_id=b["id"], cliente=b["nombre"], telefono=b["telefono"], tamano="", clave=clave, dias=dias, intervalo=CICLO_REPUESTO, atraso=atraso,
                        fase=("hoy" if atraso < ventana else "atrasado"), tipo="basico", que="Porche Básico",
                        tipo_txt="¿Cómo le fue con el Básico? · ofrecer Versión PRO", estado=estado_seg(hechos.get(clave), hoy)))

    for o in con.execute("""SELECT o.id, o.numero, o.total, o.fecha_entrega, o.creado_en, c.id cid, c.nombre, c.telefono,
                            (SELECT COALESCE(SUM(monto_usd),0) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado') pagado
                            FROM ordenes o JOIN clientes c ON c.id=o.cliente_id WHERE o.estado='entregada' AND o.estado_pago IN ('abonada','sin_pago','rechazado')"""):
        ref = (o["fecha_entrega"] or o["creado_en"])[:10]; dias = (hoy - datetime.date.fromisoformat(ref)).days
        if dias < 3: continue
        clave = f"cobro:{o['id']}:{ref}"
        if not vivo(clave): continue
        out.append(dict(cliente_id=o["cid"], cliente=o["nombre"], telefono=o["telefono"], tamano="", que=f"Debe {fmt_usd(o['total'] - o['pagado'])} · {o['numero']}", clave=clave, dias=dias,
                        tipo="cobro", tipo_txt=f"Cobrar saldo · entregada hace {dias} días", estado=estado_seg(hechos.get(clave), hoy), orden_id=o["id"]))
    return out


def cumple_de(m, hoy):
    """Devuelve (próximo cumpleaños, edad que cumple o None, mes-día). Acepta fecha completa o solo mes-día (año desconocido)."""
    nac = None; md = (m["cumple_mes_dia"] or "") if "cumple_mes_dia" in m.keys() else ""
    try: nac = datetime.date.fromisoformat((m["fecha_nacimiento"] or "")[:10])
    except ValueError: nac = None
    if nac: md = f"{nac.month:02d}-{nac.day:02d}"
    if not md: return None, None, None
    mm, dd = int(md[:2]), int(md[3:5])
    def en(anio):
        try: return datetime.date(anio, mm, dd)
        except ValueError: return datetime.date(anio, mm, 28)
    cumple = en(hoy.year)
    if cumple < hoy: cumple = en(hoy.year + 1)
    return cumple, (cumple.year - nac.year if nac else None), md


def cumples_proximos(con, ventana=30):
    """Perros que cumplen años en los próximos días. Dos avisos independientes: 2 días antes (ofrecer regalo) y el mismo día (felicitar)."""
    hoy = datetime.date.today(); h = hoy.isoformat(); out = []
    hechos = {r["clave"]: r for r in con.execute("SELECT * FROM seguimientos WHERE tipo='cumple'")}
    for m in con.execute("SELECT m.id mid, m.nombre perro, m.raza, m.fecha_nacimiento, m.cumple_mes_dia, c.id, c.nombre, c.telefono FROM mascotas m JOIN clientes c ON c.id=m.cliente_id WHERE (m.fecha_nacimiento IS NOT NULL AND m.fecha_nacimiento!='') OR (m.cumple_mes_dia IS NOT NULL AND m.cumple_mes_dia!='')"):
        cumple, edad, _ = cumple_de(m, hoy)
        if not cumple: continue
        faltan = (cumple - hoy).days
        if faltan > ventana: continue
        fase = "hoy" if faltan == 0 else "previo"
        clave = f"cumple_{fase}:{m['mid']}:{cumple.year}"
        r = hechos.get(clave)
        out.append(dict(cliente_id=m["id"], cliente=m["nombre"], telefono=m["telefono"], perro=m["perro"], raza=m["raza"], edad=edad, fecha=cumple.isoformat(), dias=faltan,
                        tamano="", que=(f"🎂 {m['perro']} cumple {edad} año{'s' if edad != 1 else ''}" if edad else f"🎂 Cumpleaños de {m['perro']}"), clave=clave, tipo="cumple", activo=faltan <= 2,
                        tipo_txt=("Felicitar" if faltan == 0 else ("Ofrecer regalo" if faltan <= 2 else f"en {faltan} días")),
                        hecho=bool(r), estado=(estado_resultado(r) if r else ("Pendiente" if faltan <= 2 else "Próximo")),
                        ofrecer=[t for t, _ in oportunidades_de(con, m["id"])] if faltan <= 2 else [],
                        hogar=hogar_de(con, m["id"], hoy) if faltan <= 2 else None))
        if faltan <= 2: out[-1]["mensajes"] = mensajes_cumple(m["perro"], edad, out[-1]["ofrecer"])
    out.sort(key=lambda s: s["dias"])
    return out


@app.get("/seguimientos", response_class=HTMLResponse)
def seguimientos(request: Request, ver: str = "pendientes", tipo: str = "", q: str = "", con=Depends(db)):
    sincronizar_packs(con)
    todos = [dict(x, mensajes=mensajes_repuesto(con, x)) if x.get("tipo") in ("primer_repuesto", "repuesto", "prepagado") and x.get("cliente_id") else x
             for x in seguimientos_pendientes(con)]
    n_atr = sum(1 for s in todos if s.get("fase") == "atrasado")
    pend = [s for s in todos if (s.get("fase", "hoy") == "atrasado") == (ver == "atrasados")] if ver != "hechos" else todos
    if tipo: pend = [s for s in (todos if ver != "hechos" else pend) if s["tipo"] == tipo]   # al filtrar por tipo se ven de una vez los de hoy y los atrasados
    if q: pend = [s for s in pend if q.lower() in (s["cliente"] or "").lower() or q in (s["telefono"] or "")]
    hechos = con.execute("SELECT s.*, c.nombre cliente, c.telefono, u.nombre usuario FROM seguimientos s JOIN clientes c ON c.id=s.cliente_id LEFT JOIN usuarios u ON u.id=s.usuario_id ORDER BY s.hecho_en DESC LIMIT 200").fetchall()
    return render(request, "seguimientos.html", seccion="seguimientos", pend=pend, hechos=hechos, ver=ver, tipo=tipo, total=len([s for s in todos if s.get("fase", "hoy") == "hoy"]), n_atr=n_atr, RESULTADOS=RESULTADOS, RPT=RESULTADOS_POR_TIPO, q=q, hoy_iso=datetime.date.today().isoformat())


@app.post("/seguimientos/hecho")
def seguimiento_hecho(request: Request, clave: str = Form(...), cliente_id: int = Form(...), tipo: str = Form(...), resultado: str = Form(...), nota: str = Form(""), fecha: str = Form(""), volver: str = Form("/seguimientos"), con=Depends(db)):
    hoy = datetime.date.today()
    hasta = None
    if resultado == "mensaje": hasta = (hoy + datetime.timedelta(days=REINTENTO_DIAS)).isoformat()
    elif resultado == "fecha":
        hasta = fecha or hoy.isoformat()   # vuelve a salir ese día: "lo quiere para hoy"
        # si tiene pack con saldo o repuesto prepagado, se programa el retiro para esa fecha y ya sale en Operaciones
        k = next((k for k in cargar_packs(con) if k["cliente_id"] == cliente_id and k["saldo"] > 0 and not k["fecha_programada"]), None)
        if k: con.execute("UPDATE packs SET fecha_programada=?, nota_programada=?, retiro_programado=1 WHERE id=?", (hasta, nota or None, k["id"]))
        else:
            r = next((r for r in cargar_prepagados(con) if r["cliente_id"] == cliente_id and not r["fecha_programada"]), None)
            if r: con.execute("UPDATE repuestos_prepagados SET fecha_programada=?, notas=? WHERE id=?", (hasta, nota or None, r["id"]))
    prev = con.execute("SELECT intentos FROM seguimientos WHERE clave=?", (clave,)).fetchone()
    con.execute("INSERT OR REPLACE INTO seguimientos (cliente_id, tipo, clave, resultado, nota, posponer_hasta, usuario_id, intentos) VALUES (?,?,?,?,?,?,?,?)",
                (cliente_id, tipo, clave, resultado, nota or None, hasta, uid_de(request), (prev["intentos"] if prev else 0) + 1))
    con.commit(); return RedirectResponse(volver, status_code=303)


# ------------------------------------------------------------------ PACKS DE REPUESTOS
def sincronizar_packs(con):
    """Crea un pack por cada línea de 'Pack 3 Repuestos' vendida que aún no tenga pack (compra ≠ consumo)."""
    UNIDADES = {"PACK3-M": 3, "PACK3-G": 3, "PACK4-M": 4, "PACK4-G": 4, "PACK8-M": 8, "PACK8-G": 8}
    for l in con.execute("""SELECT l.id lid, l.cantidad, o.id oid, o.cliente_id, p.id pid, p.sku FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id JOIN productos p ON p.id=l.producto_id
                            WHERE p.sku LIKE 'PACK%' AND o.estado!='cancelada' AND o.origen_excel=0 AND NOT EXISTS (SELECT 1 FROM packs k WHERE k.orden_id=o.id AND k.producto_id=p.id)""").fetchall():
        for _ in range(int(l["cantidad"])):
            con.execute("INSERT INTO packs (cliente_id, orden_id, producto_id, tamano, unidades, entregadas_inicio, creado_en) VALUES (?,?,?,?,?,?,(SELECT creado_en FROM ordenes WHERE id=?))",
                        (l["cliente_id"], l["oid"], l["pid"], "Grande" if l["sku"].endswith("G") else ("Mediano" if l["sku"].endswith("M") else None), UNIDADES.get(l["sku"], 3), 1, l["oid"]))
    con.commit()


def cargar_prepagados(con, solo_pendientes=True):
    sql = """SELECT r.*, c.nombre cliente, c.telefono, c.ciudad, o.numero orden,
             -- su pedido todavía no salió y lleva otras cosas: el repuesto se puede mandar en esa misma entrega
             (o.estado IN ('pendiente','en_ruta') AND """ + HAY_QUE_ENTREGAR() + """) puede_ir_junto,
             (SELECT direccion FROM direcciones d WHERE d.cliente_id=c.id ORDER BY principal DESC, id LIMIT 1) direccion,
             (SELECT maps FROM direcciones d WHERE d.cliente_id=c.id ORDER BY principal DESC, id LIMIT 1) maps
             FROM repuestos_prepagados r JOIN clientes c ON c.id=r.cliente_id LEFT JOIN ordenes o ON o.id=r.orden_id"""
    if solo_pendientes: sql += " WHERE r.entregado_en IS NULL"
    rows = [dict(r) for r in con.execute(sql + " ORDER BY r.entregado_en IS NOT NULL, r.fecha_programada IS NULL, r.fecha_programada, r.pagado_en")]
    hoy = datetime.date.today()
    for r in rows: r["dias"] = (hoy - datetime.date.fromisoformat((r["pagado_en"] or r["creado_en"])[:10])).days
    return rows


@app.get("/prepagados", response_class=HTMLResponse)
def prepagados(request: Request, ver: str = "sin_programar", q: str = "", con=Depends(db)):
    rows = cargar_prepagados(con, solo_pendientes=(ver != "todos"))
    if ver == "sin_programar": rows = [r for r in rows if not r["fecha_programada"]]
    elif ver == "programados": rows = [r for r in rows if r["fecha_programada"]]
    if q: rows = [r for r in rows if q.lower() in (r["cliente"] or "").lower() or q in (r["telefono"] or "")]
    n_sin = sum(1 for r in cargar_prepagados(con) if not r["fecha_programada"]); n_prog = sum(1 for r in cargar_prepagados(con) if r["fecha_programada"])
    clientes = con.execute("SELECT id, nombre FROM clientes ORDER BY nombre").fetchall()
    return render(request, "prepagados.html", seccion="prepagados", rows=rows, ver=ver, n_sin=n_sin, n_prog=n_prog, hoy_iso=datetime.date.today().isoformat(), clientes=clientes, q=q)


@app.post("/prepagados/{rid}/programar")
def prepagado_programar(request: Request, rid: int, fecha: str = Form(""), tipo_entrega: str = Form(""), despachador: str = Form(""), agencia: str = Form(""), notas: str = Form(""), delivery: str = Form("0"),
                        delivery_pagado: str = Form(""), delivery_forma: str = Form(""), envio: str = Form(""), envio_monto: str = Form(""), volver: str = Form(""), con=Depends(db)):
    """Delivery: monto que cobra el despachador. Envío nacional: normalmente va a cobro en destino; si lo paga Decopet se guarda el monto."""
    monto = lambda s: float((s or "0").replace(",", ".") or 0)
    if tipo_entrega == "nacional":
        env = envio or "destino"; dl = monto(envio_monto) if env == "decopet" else 0.0; pag = 1 if env == "decopet" else 0
    else:
        env = None; dl = monto(delivery) if tipo_entrega in ("delivery", "delivery_fuera") else 0.0; pag = 1 if (delivery_pagado == "1" and dl > 0) else 0
    r = con.execute("SELECT orden_id, delivery, delivery_pagado FROM repuestos_prepagados WHERE id=?", (rid,)).fetchone()
    con.execute("UPDATE repuestos_prepagados SET fecha_programada=?, tipo_entrega=?, despachador=?, agencia=?, notas=?, delivery=?, delivery_pagado=?, envio=? WHERE id=?",
                (fecha or None, tipo_entrega or None, despachador or None, agencia or None, notas or None, dl, pag, env, rid))
    if pag == 1 and dl > 0 and not (r and r["delivery_pagado"]) and r and r["orden_id"]:   # lo paga ahora: entra dentro de su orden
        f_pago = (delivery_forma or "").strip() or None
        cobro_extra(con, r["orden_id"], "Delivery repuesto", dl, f_pago, datetime.date.today().isoformat(), uid_de(request))
        con.execute("UPDATE repuestos_prepagados SET delivery_forma=? WHERE id=?", (f_pago, rid))
    con.commit(); return RedirectResponse(volver or "/prepagados", status_code=303)


@app.post("/prepagados/{rid}/con-pedido")
def prepagado_con_pedido(request: Request, rid: int, volver: str = Form(""), con=Depends(db)):
    """El cliente lo quiere ya, y su pedido todavía no ha salido: el repuesto deja de ser 'para después'
    y va en esa misma entrega (mismo despachador, mismo delivery, sin cobrar nada extra)."""
    if "coordinar" not in PERMISOS[rol_de(request)]: return RedirectResponse(volver or "/prepagados", status_code=303)
    r = con.execute("SELECT * FROM repuestos_prepagados WHERE id=? AND entregado_en IS NULL", (rid,)).fetchone()
    o = con.execute("SELECT id, estado FROM ordenes WHERE id=?", (r["orden_id"],)).fetchone() if r and r["orden_id"] else None
    if r and o and o["estado"] in ("pendiente", "en_ruta"):
        con.execute("DELETE FROM repuestos_prepagados WHERE id=?", (rid,))
        registrar(con, o["id"], uid_de(request), "pack", f"El repuesto {r['tamano'] or ''} que era para después va en esta misma entrega")
        con.commit()
        return RedirectResponse(volver or f"/ordenes?estado=todas&abrir={o['id']}", status_code=303)
    return RedirectResponse(volver or "/prepagados", status_code=303)


@app.post("/prepagados/{rid}/campo")
def prepagado_campo(request: Request, rid: int, despachador: str = Form(""), agencia: str = Form(""), en_ruta: str = Form(""), volver: str = Form(""), con=Depends(db)):
    """Ajustes rápidos desde Operaciones: quién lo lleva, por cuál agencia, o marcarlo en ruta."""
    if "coordinar" not in PERMISOS[rol_de(request)]: return RedirectResponse("/operaciones", status_code=303)
    if despachador: con.execute("UPDATE repuestos_prepagados SET despachador=? WHERE id=?", (despachador, rid))
    if agencia: con.execute("UPDATE repuestos_prepagados SET agencia=? WHERE id=?", (agencia, rid))
    if en_ruta: con.execute("UPDATE repuestos_prepagados SET en_ruta=? WHERE id=?", (1 if en_ruta == "1" else 0, rid))
    con.commit(); return RedirectResponse(volver or "/operaciones", status_code=303)


def entregar_prepagado(con, rid, uid, tipo_entrega="", despachador="", delivery_cobrado="", delivery_forma="", fecha="", pago="confirmado"):
    """Se entregó un repuesto que el cliente ya había pagado. Si el delivery se cobra ahora, entra a su orden (contado hoy);
    si lo llevó un despachador, se le anota su delivery para pagarle."""
    fe = fecha.strip() or datetime.date.today().isoformat()
    con.execute("UPDATE repuestos_prepagados SET entregado_en=?, en_ruta=0, tipo_entrega=COALESCE(NULLIF(?,''),tipo_entrega), despachador=COALESCE(NULLIF(?,''),despachador), usuario_id=? WHERE id=?",
                (fe, tipo_entrega, despachador, uid, rid))
    r = con.execute("SELECT orden_id, delivery, delivery_pagado, tipo_entrega, despachador FROM repuestos_prepagados WHERE id=?", (rid,)).fetchone()
    if not r: return
    if r["tipo_entrega"] in ("delivery", "delivery_fuera"):
        pago_retiro_despachador(con, r["despachador"], float(r["delivery"] or 0), fe, uid, orden_id=r["orden_id"], prepagado_id=rid)
    if delivery_cobrado == "1" and not r["delivery_pagado"]:   # el delivery se cobró al entregar (o queda para después)
        con.execute("UPDATE repuestos_prepagados SET delivery_pagado=1, delivery_forma=? WHERE id=?", (delivery_forma or None, rid))
        if (r["delivery"] or 0) > 0 and r["orden_id"]:
            cobro_extra(con, r["orden_id"], "Delivery repuesto", r["delivery"], delivery_forma or None, fe, uid, pago=pago)


@app.post("/prepagados/{rid}/entregar")
def prepagado_entregar(request: Request, rid: int, tipo_entrega: str = Form(""), despachador: str = Form(""), delivery_cobrado: str = Form(""), delivery_forma: str = Form(""), fecha: str = Form(""), volver: str = Form(""), con=Depends(db)):
    entregar_prepagado(con, rid, uid_de(request), tipo_entrega, despachador, delivery_cobrado, delivery_forma, fecha)
    con.commit(); return RedirectResponse(volver or "/prepagados", status_code=303)


@app.post("/prepagados/nuevo")
def prepagado_nuevo(request: Request, cliente_id: int = Form(...), tamano: str = Form("Grande"), pagado_en: str = Form(""), monto: str = Form(""), notas: str = Form(""), con=Depends(db)):
    """Registrar a mano un repuesto que el cliente dejó pagado (por ejemplo, del histórico)."""
    con.execute("INSERT INTO repuestos_prepagados (cliente_id,tamano,pagado_en,monto,notas,usuario_id) VALUES (?,?,?,?,?,?)", (cliente_id, tamano, pagado_en or datetime.date.today().isoformat(), float(monto.replace(",", ".")) if monto.strip() else None, notas or None, uid_de(request)))
    con.commit(); return RedirectResponse("/prepagados", status_code=303)


def cargar_packs(con):
    """Packs de repuestos con su saldo, entregas y fecha estimada del próximo repuesto."""
    sincronizar_packs(con)
    cols = [r[1] for r in con.execute("PRAGMA table_info(packs)")]
    for c, tp in (("fecha_programada", "TEXT"), ("tipo_programado", "TEXT"), ("despachador_programado", "TEXT"), ("nota_programada", "TEXT"), ("retiro_programado", "INTEGER"), ("delivery_programado", "REAL"), ("delivery_pagado", "INTEGER"), ("deliveries_prepagados", "INTEGER"), ("en_ruta", "INTEGER NOT NULL DEFAULT 0")):
        if c not in cols: con.execute(f"ALTER TABLE packs ADD COLUMN {c} {tp}")
    rows = con.execute("""SELECT k.*, c.nombre cliente, c.telefono, c.ciudad, o.numero orden, o.tipo_entrega, o.estado orden_estado,
        (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=k.id) entregas_posteriores,
        (SELECT MAX(fecha) FROM entregas_repuesto e WHERE e.pack_id=k.id) ultima_entrega
        FROM packs k JOIN clientes c ON c.id=k.cliente_id LEFT JOIN ordenes o ON o.id=k.orden_id ORDER BY k.creado_en DESC""").fetchall()
    hoy = datetime.date.today(); lista = []
    for r in rows:
        d = dict(r)
        # los que se lleva el día de la compra cuentan cuando esa orden se entrega, no antes
        if d.get("orden_estado") not in (None, "entregada"): d["entregadas_inicio"] = 0
        d["entregadas"] = d["entregadas_inicio"] + d["entregas_posteriores"]; d["saldo"] = d["unidades"] - d["entregadas"]
        ref = d["ultima_entrega"] or d["creado_en"][:10]
        d["dias_ultima"] = (hoy - datetime.date.fromisoformat(ref)).days
        d["entregas"] = con.execute("SELECT e.*, u.nombre usuario FROM entregas_repuesto e LEFT JOIN usuarios u ON u.id=e.usuario_id WHERE pack_id=? ORDER BY fecha", (d["id"],)).fetchall()
        # las entregas migradas de Airtable no traían su fecha real (se puso la de la compra): no sirven para medir cuánto le dura
        aprox = lambda e: "fecha aproximada" in (e["notas"] or "")
        if any(aprox(e) for e in d["entregas"]): d["duraciones"] = []
        else:
            fechas = [d["creado_en"][:10]] + [e["fecha"] for e in d["entregas"]]
            d["duraciones"] = [(datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days for a, b in zip(fechas, fechas[1:])]
        d["vencido"] = False
        d["promedio"] = round(sum(d["duraciones"]) / len(d["duraciones"])) if d["duraciones"] else None
        d["proximo"] = (datetime.date.fromisoformat(ref) + datetime.timedelta(days=ciclo_de(d["promedio"]))).isoformat()   # 21 días, o su ritmo si retira más seguido
        d["vencido"] = d["proximo"] < hoy.isoformat()   # ya debería haber pedido el siguiente: hay que escribirle
        lista.append(d)
    return lista


@app.get("/packs", response_class=HTMLResponse)
def packs(request: Request, ver: str = "activos", q: str = "", con=Depends(db), modo: str = "packs"):
    hoy = datetime.date.today(); lista = []
    for d in cargar_packs(con):
        if q and q.lower() not in (d["cliente"] or "").lower() and q not in (d["telefono"] or ""): continue
        if ver == "activos" and d["saldo"] <= 0: continue
        if ver == "agotados" and d["saldo"] > 0: continue
        lista.append(d)
    abierto = None
    if ver == "todos":   # Historial: lista de clientes; al abrir uno se ven sus packs con cada retiro
        abierto = int(request.query_params.get("cliente") or 0) or None
        for d in lista:
            d["retiros"] = con.execute("""SELECT e.*, u.nombre usuario FROM entregas_repuesto e LEFT JOIN usuarios u ON u.id=e.usuario_id
                                          WHERE e.pack_id=? ORDER BY e.fecha, e.id""", (d["id"],)).fetchall() if d["cliente_id"] == abierto else []
        lista.sort(key=lambda d: ((d["cliente"] or "").lower(), d["creado_en"] or ""))
    return render(request, "packs.html", seccion=("prepagados" if modo == "prepagados" else "packs"), packs=lista, ver=ver, hoy_iso=hoy.isoformat(), modo=modo, q=q, abierto=abierto)


@app.post("/packs/{pid}/programar")
def pack_programar(request: Request, pid: int, fecha: str = Form(""), tipo_entrega: str = Form(""), despachador: str = Form(""), notas: str = Form(""), retiro: str = Form(""), delivery: str = Form("0"), delivery_pagado: str = Form("0"), pago_forma: str = Form(""), diferencia: str = Form(""), diferencia_pagada: str = Form("0"), volver: str = Form(""), con=Depends(db)):
    k = con.execute("SELECT * FROM packs WHERE id=?", (pid,)).fetchone()
    dl = float(delivery or 0) if tipo_entrega in ("delivery", "delivery_fuera") else 0.0
    pagado = 1 if delivery_pagado == "1" and dl > 0 else 0
    dif = 0.0
    if k and (k["deliveries_prepagados"] or 0) > 0 and tipo_entrega in ("delivery", "delivery_fuera"):   # ya lo pagó por adelantado con el pack: no se cobra de nuevo
        dif = round(max(float(diferencia or 0), 0), 2)   # salvo una diferencia (un sitio más lejos): se cobra aparte y al despachador se le paga todo
        pagado = 2; dl_prep = (k["tarifa_prepagada"] or dl or 5) + dif; dl = 0.0
    saldo_k = (k["unidades"] - k["entregadas_inicio"] - con.execute("SELECT COUNT(*) FROM entregas_repuesto WHERE pack_id=?", (pid,)).fetchone()[0]) if k else 1
    cuantos_prog = max(1, min(int(retiro) if retiro.isdigit() else 1, max(saldo_k, 1)))   # cuántos repuestos se lleva ese día
    con.execute("UPDATE packs SET fecha_programada=?, tipo_programado=?, despachador_programado=?, nota_programada=?, retiro_programado=?, delivery_programado=?, delivery_pagado=? WHERE id=?",
                (fecha or None, tipo_entrega or None, despachador or None, notas or None, cuantos_prog, dl, pagado, pid))
    if pagado == 2:
        ya = k["diferencia_pagada"] and (k["delivery_diferencia"] or 0) > 0   # si ya la había cobrado al programar, no se cobra otra vez
        dif_pag = 1 if (ya or (diferencia_pagada == "1" and dif > 0)) else 0
        con.execute("UPDATE packs SET delivery_programado=?, delivery_pagado=1, delivery_diferencia=?, diferencia_pagada=? WHERE id=?", (dl_prep, dif or None, dif_pag if dif else None, pid))
        if dif > 0 and dif_pag and not ya and k["orden_id"]:
            cobro_extra(con, k["orden_id"], "Diferencia de delivery · entrega de pack", dif, pago_forma or "Pago Móvil", datetime.date.today().isoformat(), uid_de(request),
                        nota=f"entrega programada para el {fmt_fecha(fecha)}" if fecha else None)
    if pagado == 1 and k and k["orden_id"]:   # el delivery ya lo pagó: entra a la orden del pack, contado el día de hoy
        cobro_extra(con, k["orden_id"], "Delivery entrega de pack", dl, pago_forma or "Pago Móvil", datetime.date.today().isoformat(), uid_de(request),
                    nota=f"entrega programada para el {fmt_fecha(fecha)}" if fecha else None)
    con.commit(); return RedirectResponse(volver or "/packs", status_code=303)


def pago_retiro_despachador(con, desp, monto, fecha, uid, orden_id=None, pack_id=None, prepagado_id=None, motivo=None, tipo="retiro"):
    """Al despachador se le paga el delivery de lo que llevó, sea un pedido, un retiro de pack o un prepagado."""
    if not (desp or "").strip() or not monto or monto <= 0: return
    con.execute("""INSERT INTO viajes_despachador (tipo, orden_id, pack_id, prepagado_id, fecha, despachador, monto, motivo, usuario_id)
                   VALUES (?,?,?,?,?,?,?,?,?)""", (tipo, orden_id, pack_id, prepagado_id, fecha, desp.strip(), round(monto, 2), motivo, uid))


def entregar_pack(con, pid, uid, fecha="", cuantos="1", tipo_entrega="", despachador="", delivery_cobrado="0", pago_forma="", notas="", pago="confirmado"):
    """Se entregaron repuestos de un pack. El delivery que se cobra en ese momento entra a su orden (contado el día de hoy)
    y, si lo llevó un despachador, se le anota para pagarle el delivery."""
    k = con.execute("SELECT k.*, (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=k.id) n FROM packs k WHERE id=?", (pid,)).fetchone()
    if not k or k["entregadas_inicio"] + k["n"] >= k["unidades"]: return False
    saldo = k["unidades"] - k["entregadas_inicio"] - k["n"]
    n_retiros = max(1, min(int(cuantos) if cuantos.isdigit() else 1, saldo))   # puede llevarse varios de una vez
    for i in range(n_retiros):
        con.execute("INSERT INTO entregas_repuesto (pack_id, fecha, tipo_entrega, despachador, delivery_cobrado, notas, usuario_id) VALUES (?,?,?,?,?,?,?)",
                (pid, fecha or datetime.date.today().isoformat(), tipo_entrega or k["tipo_programado"] or None, despachador or k["despachador_programado"] or None, float(delivery_cobrado or 0) if i == 0 else 0.0, notas or None, uid))
    te = tipo_entrega or k["tipo_programado"] or ""; quien = despachador or k["despachador_programado"] or ""
    if te in ("delivery", "delivery_fuera"):   # lo que pagó el cliente de delivery por este retiro, cobrado ahora o antes
        pago_retiro_despachador(con, quien, float(k["delivery_programado"] or 0) or float(delivery_cobrado or 0), fecha or datetime.date.today().isoformat(), uid,
                                orden_id=k["orden_id"], pack_id=pid)
    con.execute("UPDATE packs SET en_ruta=0, fecha_programada=NULL, tipo_programado=NULL, despachador_programado=NULL, nota_programada=NULL, retiro_programado=NULL, delivery_programado=NULL, delivery_pagado=NULL, estado=CASE WHEN entregadas_inicio + (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=packs.id) >= unidades THEN 'completo' ELSE estado END WHERE id=?", (pid,))
    dc = float(delivery_cobrado or 0)
    if k["delivery_pagado"]: dc = 0.0   # ya se cobró al programar (o venía prepagado con el pack)
    if (k["deliveries_prepagados"] or 0) > 0 and (tipo_entrega or k["tipo_programado"]) in ("delivery", "delivery_fuera"):
        con.execute("UPDATE packs SET deliveries_prepagados=deliveries_prepagados-1 WHERE id=?", (pid,)); dc = 0.0
    if dc > 0 and k["orden_id"]:   # el delivery del retiro entra a la orden original del pack, contado el día que se cobra
        forma = pago_forma or ("Efectivo USD" if pago == "confirmado" else "")
        cobro_extra(con, k["orden_id"], "Delivery entrega de pack", dc, forma, fecha or datetime.date.today().isoformat(), uid, pago=pago)
    if (k["delivery_diferencia"] or 0) > 0 and not k["diferencia_pagada"] and k["orden_id"]:   # la diferencia de delivery que quedó por cobrar al entregar
        forma = pago_forma or ("Efectivo USD" if pago == "confirmado" else "")
        cobro_extra(con, k["orden_id"], "Diferencia de delivery · entrega de pack", k["delivery_diferencia"], forma, fecha or datetime.date.today().isoformat(), uid, pago=pago)
    con.execute("UPDATE packs SET delivery_diferencia=NULL, diferencia_pagada=NULL WHERE id=?", (pid,))
    return True


@app.post("/packs/{pid}/entregar")
def pack_entregar(request: Request, pid: int, fecha: str = Form(""), cuantos: str = Form("1"), tipo_entrega: str = Form(""), despachador: str = Form(""), delivery_cobrado: str = Form("0"), pago_forma: str = Form(""), notas: str = Form(""), volver: str = Form(""), con=Depends(db)):
    pendiente = pago_forma == "pendiente"   # el delivery no se cobró: queda como saldo de la orden y sale en Seguimientos para cobrarlo
    if not entregar_pack(con, pid, uid_de(request), fecha, cuantos, tipo_entrega, despachador, delivery_cobrado, "" if pendiente else pago_forma, notas,
                         pago=None if pendiente else "confirmado"):
        return RedirectResponse("/packs", status_code=303)
    con.commit(); return RedirectResponse(volver or "/prepagados", status_code=303)


def retiro_no_recibio(con, tipo, rid, uid, fue="", motivo="", fecha=""):
    """Un retiro de pack o un prepagado iba en ruta y no se entregó. Vuelve a programado; si el despachador llegó
    al sitio se le paga el viaje. Sin fecha nueva queda por programar otra vez."""
    tabla = "packs" if tipo == "pack" else "repuestos_prepagados"
    k = con.execute(f"SELECT * FROM {tabla} WHERE id=?", (rid,)).fetchone()
    if not k or not k["en_ruta"]: return
    desp = (k["despachador_programado"] if tipo == "pack" else k["despachador"]) or ""
    monto = float((k["delivery_programado"] if tipo == "pack" else k["delivery"]) or 0)
    hoy_ = datetime.date.today().isoformat()
    texto = "No se pudo entregar el repuesto"
    if fue == "1" and desp:
        pago_retiro_despachador(con, desp, monto, hoy_, uid, orden_id=k["orden_id"], motivo=motivo.strip() or None, tipo="fallido",
                                **({"pack_id": rid} if tipo == "pack" else {"prepagado_id": rid}))
        texto += f" · {desp} fue al sitio: se le paga el viaje ({fmt_usd(monto)})"
    elif fue == "0": texto += f" · {desp or 'el despachador'} no llegó a ir"
    f = (fecha or "").strip()
    if tipo == "pack":
        if f: con.execute("UPDATE packs SET en_ruta=0, fecha_programada=? WHERE id=?", (f, rid))
        else: con.execute("UPDATE packs SET en_ruta=0, despachador_programado=NULL WHERE id=?", (rid,))
    else:
        if f: con.execute("UPDATE repuestos_prepagados SET en_ruta=0, fecha_programada=? WHERE id=?", (f, rid))
        else: con.execute("UPDATE repuestos_prepagados SET en_ruta=0, despachador=NULL WHERE id=?", (rid,))
    texto += (f" · se vuelve a llevar {fmt_fecha(f) if fmt_fecha(f) in ('hoy', 'mañana') else 'el ' + fmt_fecha(f)}" if f else " · por coordinar de nuevo")
    if k["orden_id"]: registrar(con, k["orden_id"], uid, "estado", texto, motivo.strip() or None)


@app.post("/packs/{pid}/campo")
def pack_campo(request: Request, pid: int, en_ruta: str = Form(""), volver: str = Form(""), con=Depends(db)):
    """Marcar en ruta un retiro de pack (o devolverlo si se tocó sin querer)."""
    if "coordinar" not in PERMISOS[rol_de(request)]: return RedirectResponse("/operaciones", status_code=303)
    if en_ruta: con.execute("UPDATE packs SET en_ruta=? WHERE id=?", (1 if en_ruta == "1" else 0, pid))
    con.commit(); return RedirectResponse(volver or "/operaciones", status_code=303)


@app.post("/packs/{pid}/no-recibio")
def pack_no_recibio(request: Request, pid: int, fue: str = Form(""), motivo: str = Form(""), fecha: str = Form(""), volver: str = Form(""), con=Depends(db)):
    if rol_de(request) not in ("admin", "logistica"): return RedirectResponse("/operaciones", status_code=303)
    retiro_no_recibio(con, "pack", pid, uid_de(request), fue, motivo, fecha); con.commit()
    return RedirectResponse(volver or "/operaciones", status_code=303)


@app.post("/prepagados/{rid}/no-recibio")
def prepagado_no_recibio(request: Request, rid: int, fue: str = Form(""), motivo: str = Form(""), fecha: str = Form(""), volver: str = Form(""), con=Depends(db)):
    if rol_de(request) not in ("admin", "logistica"): return RedirectResponse("/operaciones", status_code=303)
    retiro_no_recibio(con, "prep", rid, uid_de(request), fue, motivo, fecha); con.commit()
    return RedirectResponse(volver or "/operaciones", status_code=303)


# ------------------------------------------------------------------ CLIENTES (mínimo; ficha completa en la Parte 3)
@app.get("/clientes", response_class=HTMLResponse)
def clientes(request: Request, q: str = "", ver: str = "todos", ciudad: str = "", origen: str = "", falta: str = "", con=Depends(db)):
    return _clientes(request, q, ver, ciudad, con, origen=origen, falta=falta)


@app.get("/mascotas", response_class=HTMLResponse)
def mascotas_lista(request: Request, q: str = "", ver: str = "mascotas", raza: str = "", con=Depends(db)):
    return _clientes(request, q, "cumples" if ver == "cumples" else "mascotas", "", con, raza=raza)


# lo que le puede faltar a la ficha de un cliente, en el orden en que se muestra
FALTA_CLIENTE = {"telefono": "teléfono", "correo": "correo", "ciudad": "ciudad", "direccion": "dirección", "mascota": "mascota", "raza": "raza del perro"}

def _clientes(request, q, ver, ciudad, con, raza="", origen="", falta=""):
    base = """SELECT c.*, (SELECT direccion FROM direcciones d WHERE d.cliente_id=c.id AND principal=1) dir,
             (SELECT COUNT(*) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada') n_ordenes,
             (SELECT COALESCE(SUM(total),0) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada') gastado,
             (SELECT MAX(creado_en) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada') ultima,
             (SELECT MIN(creado_en) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada') primera,
             (SELECT GROUP_CONCAT(m.nombre || COALESCE(' (' || m.raza || ')',''), ', ') FROM mascotas m WHERE m.cliente_id=c.id) perros,
             (SELECT COUNT(*) FROM mascotas m WHERE m.cliente_id=c.id AND (m.raza='Pendiente' OR m.revisar=1)) perro_pend,
             (SELECT COUNT(*) FROM mascotas m WHERE m.cliente_id=c.id) n_perros,
             (SELECT COUNT(*) FROM direcciones d WHERE d.cliente_id=c.id) n_dir,
             (SELECT COUNT(*) FROM notas_cliente nc WHERE nc.cliente_id=c.id) n_notas,
             (SELECT GROUP_CONCAT(nc.texto, ' · ') FROM notas_cliente nc WHERE nc.cliente_id=c.id) notas_txt,
             (SELECT canal FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada' GROUP BY canal ORDER BY COUNT(*) DESC LIMIT 1) canal_top,
             EXISTS(SELECT 1 FROM ordenes o JOIN orden_lineas l ON l.orden_id=o.id JOIN productos p ON p.id=l.producto_id WHERE o.cliente_id=c.id AND o.estado!='cancelada' AND p.sku LIKE 'PRO-%') pro,
             COALESCE((SELECT GROUP_CONCAT(DISTINCT CASE p.sku WHEN 'PRO-M' THEN 'PRO Mediano' WHEN 'PRO-G' THEN 'PRO Grande' WHEN 'BAS-M' THEN 'Básico Mediano' WHEN 'BAS-G' THEN 'Básico Grande' END)
              FROM ordenes o JOIN orden_lineas l ON l.orden_id=o.id JOIN productos p ON p.id=l.producto_id WHERE o.cliente_id=c.id AND o.estado!='cancelada' AND (p.sku LIKE 'PRO-%' OR p.sku LIKE 'BAS-%')),
              CASE WHEN c.porche_version IN ('PRO','Básico') THEN c.porche_version || COALESCE(' ' || c.porche_tamano,'') END) porches
             FROM clientes c"""
    rows = [dict(r) for r in con.execute(base + " ORDER BY ultima DESC NULLS LAST, c.nombre")]
    hoy = datetime.date.today(); cv = corte_vip(con)
    for r in rows:
        r["dias"] = (hoy - datetime.date.fromisoformat(r["ultima"][:10])).days if r["ultima"] else None
        r["tipo"] = tipo_cliente(r["n_ordenes"], r["dias"], r["gastado"] or 0, cv)
    for r in rows:
        r["pro"] = r["pro"] or (r["porche_version"] == "PRO")
        r["basico"] = (r["porches"] or "").startswith("Básico") or r["porche_version"] == "Básico"
        r["pendientes"] = ([k for k in ("telefono", "correo", "ciudad") if (r[k] or "Pendiente") == "Pendiente"]
                           + (["direccion"] if not r["n_dir"] else []) + (["mascota"] if not r["n_perros"] else [])
                           + (["raza"] if r["perro_pend"] else []))
    creditos = {r[0]: round(r[1], 2) for r in con.execute("SELECT cliente_id, SUM(monto) FROM credito_cliente GROUP BY cliente_id HAVING SUM(monto) > 0.009")}
    for r in rows: r["credito"] = creditos.get(r["id"], 0)
    # el último resultado de su seguimiento de repuesto: "el perro no se adaptó" / "ya no lo usa" (para filtrarlos después)
    no_usa = {r[0]: r[1] for r in con.execute("""SELECT cliente_id, resultado FROM seguimientos s WHERE resultado IN ('no_se_adapto','ya_no_usa')
                 AND hecho_en = (SELECT MAX(hecho_en) FROM seguimientos s2 WHERE s2.cliente_id=s.cliente_id AND s2.tipo IN ('primer_repuesto','repuesto'))""")}
    for r in rows: r["no_usa"] = no_usa.get(r["id"])
    conteos = {"todos": len(rows), "pro": sum(1 for r in rows if r["pro"]), "basico": sum(1 for r in rows if r["basico"]),
               "pendientes": sum(1 for r in rows if r["pendientes"]), "credito": sum(1 for r in rows if r["credito"]),
               "notas": sum(1 for r in rows if r["n_notas"]), "no_adapto": sum(1 for r in rows if r["no_usa"] == "no_se_adapto"),
               "no_usa": sum(1 for r in rows if r["no_usa"] == "ya_no_usa")}
    falta_n = {k: sum(1 for r in rows if k in r["pendientes"]) for k in FALTA_CLIENTE}
    for k in TIPOS_CLIENTE: conteos[k] = sum(1 for r in rows if r["tipo"] == k)
    ciudades = {}
    for r in rows:
        if r["ciudad"]: ciudades[r["ciudad"].strip()] = ciudades.get(r["ciudad"].strip(), 0) + 1
    # el filtro muestra solo ciudades con clientes: una ciudad en 0 parecía un error ("no hay nada en Trujillo")
    ciudades = sorted(ciudades.items(), key=lambda x: (-x[1], x[0]))
    estados_cl = {}
    for r in rows:
        if r["estado"]: estados_cl[r["estado"]] = estados_cl.get(r["estado"], 0) + 1
    estados_cl = sorted(estados_cl.items(), key=lambda x: (-x[1], x[0]))
    if q and ver != "mascotas":
        ql = q.lower(); rows = [r for r in rows if any(ql in (r[k] or "").lower() for k in ("nombre", "telefono", "correo", "cedula", "perros", "ciudad", "estado"))]
    elif ver == "pro": rows = [r for r in rows if r["pro"]]
    elif ver == "basico": rows = [r for r in rows if r["basico"]]
    elif ver == "pendientes": rows = [r for r in rows if ((falta in r["pendientes"]) if falta else r["pendientes"])]
    elif ver == "notas": rows = [r for r in rows if r["n_notas"]]
    elif ver == "no_adapto": rows = [r for r in rows if r["no_usa"] == "no_se_adapto"]
    elif ver == "no_usa": rows = [r for r in rows if r["no_usa"] == "ya_no_usa"]
    elif ver == "credito": rows = sorted([r for r in rows if r["credito"]], key=lambda r: -r["credito"])
    elif ver in TIPOS_CLIENTE: rows = [r for r in rows if r["tipo"] == ver]
    if ciudad.startswith("edo:"): rows = [r for r in rows if (r["estado"] or "") == ciudad[4:]]   # filtro por estado completo
    elif ciudad: rows = [r for r in rows if ciudad.strip().lower() in (r["ciudad"] or "").lower()]
    if origen: rows = [r for r in rows if (r["origen"] or "Sin registrar") == origen]
    cumples = cumples_proximos(con, 0)   # solo los de hoy
    conteos["cumples"] = sum(1 for s in cumples if s["activo"] and not s["hecho"])
    mascotas = []
    if ver == "mascotas":
        hoy = datetime.date.today()
        for m in con.execute("""SELECT m.*, c.nombre cliente, c.telefono, c.ciudad FROM mascotas m JOIN clientes c ON c.id=m.cliente_id
                                WHERE (? = '' OR m.nombre LIKE ? OR m.raza LIKE ? OR c.nombre LIKE ?) AND (? = '' OR m.raza = ?) ORDER BY m.nombre COLLATE NOCASE""", (q, f"%{q}%", f"%{q}%", f"%{q}%", raza, raza)):
            d = dict(m); cumple, edad, md = cumple_de(m, hoy)
            d["cumple"] = cumple.isoformat() if cumple else None; d["faltan"] = (cumple - hoy).days if cumple else None
            d["edad"] = (edad - (1 if cumple and cumple.year > hoy.year else 0)) if edad is not None else None
            mascotas.append(d)
        rows = []
    conteos["mascotas"] = con.execute("SELECT COUNT(*) FROM mascotas").fetchone()[0]
    razas_top = con.execute("SELECT raza, COUNT(*) n FROM mascotas WHERE raza IS NOT NULL AND raza!='' AND raza!='Pendiente' GROUP BY raza ORDER BY raza COLLATE NOCASE").fetchall() if ver == "mascotas" else []
    return render(request, "clientes.html", falta=falta, falta_n=falta_n, FALTA_CLIENTE=FALTA_CLIENTE, seccion=("mascotas" if ver in ("mascotas", "cumples") else "clientes"), clientes=rows[:200], q=q, ver=ver, total=conteos["todos"], conteos=conteos, truncado=len(rows) > 200, cumples=cumples, RESULTADOS=RESULTADOS, RPT=RESULTADOS_POR_TIPO, TIPOS_CLIENTE=TIPOS_CLIENTE, ciudad=ciudad, ciudades=ciudades, estados_cl=estados_cl, origen=origen,
                  origenes=sorted({(r["origen"] or "Sin registrar") for r in con.execute("SELECT origen FROM clientes")}), mascotas=mascotas, razas_top=razas_top, raza=raza)


@app.get("/clientes/nuevo/panel", response_class=HTMLResponse)
def cliente_nuevo_panel(request: Request, con=Depends(db)):
    otros = con.execute("SELECT id, nombre FROM clientes ORDER BY nombre").fetchall()
    return render(request, "_cliente_nuevo.html", otros=otros)


@app.post("/clientes/nuevo")
async def cliente_crear(request: Request, con=Depends(db)):
    f = await request.form()
    nombre_pila, apellido = capitalizar(f["nombre_pila"]), capitalizar(f.get("apellido"), inicio=False) or None
    ciu, edo = normalizar_ciudad(f.get("ciudad")); edo = edo or f.get("estado_geo") or None
    cur = con.execute("INSERT INTO clientes (nombre_pila,apellido,nombre,telefono,cedula,correo,ciudad,estado,canal_habitual,origen,referido_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (nombre_pila, apellido, nombre_completo(nombre_pila, apellido), normalizar_telefono(f.get("telefono")), (f.get("cedula") or "").strip().upper() or None,
                       f.get("correo") or None, ciu, edo, f.get("canal_habitual") or None,
                       (f.get("origen") or "").strip() or None, int(f["referido_id"]) if (f.get("referido_id") or "").isdigit() else None))
    cid = cur.lastrowid
    if f.get("direccion"):
        con.execute("INSERT INTO direcciones (cliente_id,etiqueta,direccion,zona,municipio,ciudad,estado,maps,principal) VALUES (?,?,?,?,?,?,?,?,1)",
                    (cid, "Principal", f["direccion"], f.get("zona") or None, f.get("municipio") or None, ciu, edo, f.get("maps") or None))
    if f.get("nota"):
        con.execute("INSERT INTO notas_cliente (cliente_id,tipo,texto,mostrar_en_orden,mostrar_logistica,autor_id) VALUES (?,?,?,?,?,?)",
                    (cid, "general", f["nota"], 1, 0 if f.get("nota_privada") else 1, uid_de(request)))
    guardar_mascotas(con, cid, f)
    con.commit()
    return RedirectResponse(f"/clientes?abrir={cid}", status_code=303)


# Secciones aún no diseñadas (debe ir al final para no capturar /ordenes)


# ------------------------------------------------------------------ TIPO DE CLIENTE (segmento automático)
TIPOS_CLIENTE = {"vip": "VIP", "frecuente": "Frecuente", "activo": "Activo", "nuevo": "Nuevo", "inactivo": "Inactivo", "perdido": "Perdido", "sin_compras": "Sin compras"}
def tipo_cliente(n, dias, gastado, corte_vip):
    """Segmento según compras, días sin comprar y gasto. VIP = está en el 10 % que más gasta y sigue activo."""
    if not n: return "sin_compras"
    if dias is None: return "activo"
    if dias > 180: return "perdido"
    if dias > 90: return "inactivo"
    if corte_vip and gastado >= corte_vip and n >= 2: return "vip"
    if n >= 4 and dias <= 60: return "frecuente"
    if n == 1 and dias <= 60: return "nuevo"
    return "activo"


def corte_vip(con):
    tot = sorted((r[0] for r in con.execute("SELECT SUM(total) FROM ordenes WHERE estado!='cancelada' GROUP BY cliente_id")), reverse=True)
    if len(tot) < 5: return None
    return tot[max(0, len(tot) // 10 - 1)]


# ------------------------------------------------------------------ FICHA DE CLIENTE
def ritmo_cliente(fechas):
    """Intervalo propio del cliente entre compras de repuesto (mediana en días) y cuánta confianza hay."""
    fs = sorted({f for f in fechas if f})
    if len(fs) < 2: return None, 0
    ds = [(datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days for a, b in zip(fs, fs[1:])]
    ds = [d for d in ds if 5 <= d <= 120] or ds
    ds.sort(); med = ds[len(ds) // 2]
    return med, len(ds)


def oportunidades_cliente(lineas, perros, ya_tiene_porche=False, tiene_basico=False):
    """Qué le falta a este hogar, con el criterio de Cristina:
    el porche y la rampa se comparten (uno por casa); el comedor es uno por perro.
    Nunca sugiere la cinta (va pegada a la rampa, no es un producto) ni la malla (solo para perros que escarban)."""
    n = lambda pref: sum(int(r["n"] or 0) for r in lineas if r["sku"] and r["sku"].startswith(pref))
    por_cat = lambda cat: sum(int(r["n"] or 0) for r in lineas if r["categoria"] == cat and not (r["sku"] or "").startswith("MALLA"))
    nombres = ", ".join(p["nombre"] for p in perros) or "su perro"
    np = max(len(perros), 1)
    # el porche puede venir de antes del ERP (Airtable dice "Miembro PRO / Básico" aunque aquí no haya pedido)
    porches = n("PRO-") or n("BAS-") or (1 if ya_tiene_porche else 0); comedores = por_cat("comedor"); rampas = por_cat("rampa")
    out = []
    if not porches: out.append(("El Porche Versión PRO", "Todavía no tiene porche."))
    # con el Básico puede querer otro Básico o pasarse al PRO (Cristina, 3 oct 2026)
    elif (n("BAS-") or tiene_basico) and not n("PRO-"): out.append(("Otro Básico o pasarse al PRO", "Tiene el Porche Básico."))
    if comedores < np:
        faltan = np - comedores
        titulo = "Comedor" if faltan == 1 else f"{faltan} comedores"
        # lo que vale es la cuenta: 3 perros y 2 comedores. Lo demás ella ya lo sabe.
        out.append((titulo, f"{np} perro{'s' if np != 1 else ''} y {comedores} comedor{'es' if comedores != 1 else ''}."
                            if comedores else f"{nombres}: sin comedor."))
    if not rampas: out.append(("Rampa", "No tiene rampa."))
    # la malla NO se sugiere: es solo para perros que escarban, y eso no se sabe por lo que compró (Cristina, 3 oct 2026)
    if n("REP-") >= 2 and not n("PACK3"): out.append(("Pack 3 repuestos", f"{n('REP-')} repuestos sueltos y ningún pack."))
    return out


def hogar_de(con, cid, hoy):
    """Todo lo del hogar para decidir qué ofrecer sin salir de la página: sus perros, su porche y lo que ya compró."""
    c = con.execute("SELECT porche_version, porche_tamano FROM clientes WHERE id=?", (cid,)).fetchone()
    perros = []
    for m in con.execute("SELECT * FROM mascotas WHERE cliente_id=? ORDER BY id", (cid,)):
        _, edad, _ = cumple_de(m, hoy)
        nac = m["fecha_nacimiento"] or ""
        anios = None
        if len(nac) >= 10:
            try:
                f = datetime.date.fromisoformat(nac[:10]); anios = hoy.year - f.year - ((hoy.month, hoy.day) < (f.month, f.day))
            except ValueError: pass
        perros.append({"nombre": m["nombre"], "raza": m["raza"], "anios": anios, "peso": m["peso_kg"], "notas": m["notas"]})
    compras = con.execute("""SELECT l.nombre, CAST(SUM(l.cantidad) AS INTEGER) n FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id
                             JOIN productos p ON p.id=l.producto_id WHERE o.cliente_id=? AND o.estado!='cancelada' AND p.tipo='producto'
                             GROUP BY l.nombre ORDER BY MAX(o.creado_en) DESC""", (cid,)).fetchall()
    porche = " ".join(x for x in ((c["porche_version"] if c else None), (c["porche_tamano"] if c else None)) if x)
    return {"perros": perros, "porche": porche, "compras": [dict(r) for r in compras]}


# Mensajes de cumpleaños para WhatsApp. Tono de la marca (ejemplo de Cristina, 3 oct 2026): cercano, con humor, corto,
# 💚 como firma; la oferta va al final y suave ("nos avisan"), nunca insistente. Sin adjetivos con género: no sabemos si es macho o hembra.
SALUDOS_CUMPLE = [
    "¡Feliz cumple a {p}! 🥳 Hoy se acepta repetir plato. Bueno, {p} acepta todos los días 😂💚",
    "¡Hoy cumple {p}! 🎂 Que hoy haya premios dobles y siestas largas 💚",
    "¡Feliz cumpleaños, {p}! 🐾{e} Que lo celebren con muchos cariños y algún premio extra 😄💚",
    "¡Hoy es el día de {p}! 🎉 Feliz cumple de parte de todo el equipo Decopet 💚",
]
OFERTA_CUMPLE = {   # varias formas de ofrecer cada cosa: cada saludo va con una distinta, así no terminan todos igual
    "Comedor": ["Si quieren estrenarlo con un comedor nuevo, nos avisan.",
                "Y si el regalo es un comedor nuevo, aquí estamos 🍽️",
                "¿Un comedor nuevo para celebrar? Nos escriben y se lo preparamos.",
                "Por cierto, un comedor a su altura es el regalo perfecto para comer cómodo 😉"],
    "Rampa": ["Si quieren regalarle una rampa para que suba y baje con más comodidad, nos avisan.",
              "¿Y si este año el regalo es una rampa? Nos avisan 🐾",
              "Una rampa es un regalo que se agradece todos los días. Si la quieren, nos escriben.",
              "Si quieren cuidarle las patitas, la rampa es buena idea. Aquí estamos."],
    "El Porche Versión PRO": ["Si quieren regalarle su propio porche, nos avisan 🌿",
                              "¿Y si el regalo es su propio porche de grama natural? 🌿",
                              "Un porche propio sería el mejor regalo. Nos escriben si les provoca.",
                              "Si quieren darle su rincón de grama en casa, el porche es para eso 🌱"],
    "Otro Básico o pasarse al PRO": ["Y si quieren celebrarlo con otro porche o pasándose al PRO, nos avisan 🌿",
                                     "¿Y si el regalo es subir al porche PRO? Nos avisan 🌿",
                                     "Si les hace falta un segundo porche o quieren pasarse al PRO, aquí estamos.",
                                     "Buen día para pensar en el PRO… o en un segundo porche 😉"],
    "Pack 3 repuestos": ["Y si toca reponer, el pack de 3 repuestos sale mejor 😉",
                         "Si ya toca grama nueva, el pack de 3 les rinde más 🌱",
                         "Y para celebrar con grama fresca, el pack de 3 repuestos es buena idea.",
                         "Por cierto, el pack de 3 repuestos les ahorra viajes 😉"],
}


def mensajes_cumple(perro, edad, ofrecer):
    """Varias versiones del mensaje de felicitación; Cristina elige o cambia antes de enviarlo."""
    ofertas = next((OFERTA_CUMPLE[t] for t in ofrecer if t in OFERTA_CUMPLE),
                   OFERTA_CUMPLE["Comedor"] if any(t.endswith("comedores") for t in ofrecer) else [])
    e = f" {edad} año{'s' if edad != 1 else ''}." if edad else ""
    partes = (perro or "").split()
    perro = partes[0] if len(partes) > 2 else perro   # "Nicolás martini González" → "Nicolás" (a veces trae el apellido de la familia)
    return [(s.format(p=perro, e=e) + (" " + ofertas[i % len(ofertas)] if ofertas else "")).strip() for i, s in enumerate(SALUDOS_CUMPLE)]


def mensajes_repuesto(con, s):
    """Mensajes para ofrecer el repuesto, con el nombre de la persona y de sus perros. Mismo tono que los de cumpleaños."""
    c = con.execute("SELECT nombre, nombre_pila FROM clientes WHERE id=?", (s["cliente_id"],)).fetchone()
    quien = (c["nombre_pila"] or (c["nombre"] or "").split(" ")[0]).strip() if c else ""
    perros = [((r[0] or "").split() or [""])[0] for r in con.execute("SELECT nombre FROM mascotas WHERE cliente_id=? ORDER BY id", (s["cliente_id"],))]
    perros = [p for p in perros if p]
    de = (" y ".join([", ".join(perros[:-1]), perros[-1]]) if len(perros) > 1 else perros[0]) if perros else ""
    tam = (s.get("tamano") if s.get("tamano") not in (None, "—") else s.get("porche_tamano")) or ""   # los ya contactados traen el del cliente
    tam = tam.lower(); tam = f" {tam}" if tam in ("mediano", "grande") else ""
    sem = round((s.get("dias") or 0) / 7)
    hola = f"¡Hola {quien}! 👋" if quien else "¡Hola! 👋"
    if s.get("tipo") == "prepagado":
        return [f"{hola} Ya te toca el repuesto{tam} que tienes pagado 🌱 ¿Qué día te lo llevamos? 💚"]
    porche = f"el porche de {de}" if de else "el porche"
    al_porche = "al" + porche[2:]
    va = "van" if len(perros) > 1 else "va"
    v = [f"{hola} ¿Cómo {va} {de or 'todo'} con el porche? Ya van {sem} semanas, así que la grama seguro está pidiendo cambio 🌱 ¿Te preparamos el repuesto{tam}? 💚" if sem >= 2 else
         f"{hola} ¿Cómo {va} {de or 'todo'} con el porche? 🌱 ¿Te preparamos el repuesto{tam}? 💚",
         f"{hola} Ya le toca grama nueva {al_porche} 🌱 ¿Te enviamos el repuesto{tam}? 💚",
         f"{hola} Pasamos a recordarte que ya es buen momento para cambiar la grama de {porche} 🌿 Si quieres, te lo coordinamos esta semana 💚"]
    if s.get("tipo") != "primer_repuesto":
        v.append(f"{hola} Ya va tocando repuesto para {porche} 🌱 Si te sirve, el pack de 3 sale mejor y te olvidas por un tiempo 😉💚")
    return v


def oportunidades_de(con, cid):
    """Lo que se le puede ofrecer a este cliente (lo mismo que sale en su ficha). Para el cumpleaños de su perro."""
    c = con.execute("SELECT porche_version FROM clientes WHERE id=?", (cid,)).fetchone()
    perros = [dict(m) for m in con.execute("SELECT nombre FROM mascotas WHERE cliente_id=? ORDER BY id", (cid,))]
    lineas = con.execute("""SELECT p.sku, p.categoria, SUM(l.cantidad) n FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id
                            JOIN productos p ON p.id=l.producto_id WHERE o.cliente_id=? AND o.estado!='cancelada' GROUP BY p.id""", (cid,)).fetchall()
    return oportunidades_cliente(lineas, perros, ya_tiene_porche=bool(c and c["porche_version"] in ("PRO", "Básico")),
                                 tiene_basico=bool(c and c["porche_version"] == "Básico"))


@app.get("/clientes/{cid}", response_class=HTMLResponse)
def cliente_ficha(request: Request, cid: int, con=Depends(db)):
    c = con.execute("SELECT * FROM clientes WHERE id=?", (cid,)).fetchone()
    if not c: return RedirectResponse("/clientes", 303)
    rol = rol_de(request); hoy = datetime.date.today()
    perros = [dict(m) for m in con.execute("SELECT * FROM mascotas WHERE cliente_id=? ORDER BY id", (cid,))]
    for m in perros:
        m["edad"] = m["cumple"] = m["faltan"] = None
        cumple, edad_cumple, md = cumple_de(m, hoy)
        if cumple:
            m["cumple"] = cumple.isoformat(); m["faltan"] = (cumple - hoy).days; m["md"] = md
            if edad_cumple is not None: m["edad"] = edad_cumple - 1 if cumple.year == hoy.year + 1 or (cumple > hoy) else edad_cumple
    dirs = con.execute("SELECT * FROM direcciones WHERE cliente_id=? ORDER BY principal DESC, id", (cid,)).fetchall()
    notas = con.execute("SELECT n.*, u.nombre autor FROM notas_cliente n LEFT JOIN usuarios u ON u.id=n.autor_id WHERE cliente_id=? ORDER BY n.id DESC", (cid,)).fetchall()
    if rol != "admin": notas = [n for n in notas if n["mostrar_logistica"]]
    ordenes = con.execute("""SELECT o.*, (SELECT GROUP_CONCAT(CAST(l.cantidad AS INTEGER) || '× ' || l.nombre, ', ') FROM orden_lineas l WHERE l.orden_id=o.id) productos
                             FROM ordenes o WHERE o.cliente_id=? ORDER BY o.creado_en DESC""", (cid,)).fetchall()
    validas = [o for o in ordenes if o["estado"] != "cancelada"]
    lineas = con.execute("""SELECT p.sku, p.nombre, p.id pid, p.categoria, SUM(l.cantidad) n, MAX(substr(o.creado_en,1,10)) ultima
                            FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id JOIN productos p ON p.id=l.producto_id
                            WHERE o.cliente_id=? AND o.estado!='cancelada' GROUP BY p.id""", (cid,)).fetchall()
    comprado = {r["pid"]: r for r in lineas}
    catalogo = con.execute("SELECT * FROM productos WHERE tipo='producto' AND activo=1 ORDER BY orden").fetchall()
    for c_, tp in (("fecha_programada", "TEXT"), ("tipo_programado", "TEXT"), ("despachador_programado", "TEXT"), ("nota_programada", "TEXT")):
        if c_ not in [r[1] for r in con.execute("PRAGMA table_info(packs)")]: con.execute(f"ALTER TABLE packs ADD COLUMN {c_} {tp}")
    # los que se lleva el día de la compra cuentan cuando esa orden se entrega, no antes
    packs = [dict(r) | {"entregadas_inicio": r["ini_real"]} for r in con.execute("""SELECT k.*, (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=k.id) entregadas,
                           (SELECT MAX(fecha) FROM entregas_repuesto e WHERE e.pack_id=k.id) ult, (CASE WHEN k.orden_id IS NULL OR (SELECT o_.estado FROM ordenes o_ WHERE o_.id=k.orden_id)='entregada' THEN k.entregadas_inicio ELSE 0 END) ini_real
                           FROM packs k WHERE k.cliente_id=? ORDER BY k.id DESC""", (cid,))]
    entregas = con.execute("""SELECT e.*, k.tamano, k.unidades, k.id pack, o.numero orden,
                              (SELECT COUNT(*) FROM entregas_repuesto e2 WHERE e2.pack_id=e.pack_id AND (e2.fecha < e.fecha OR (e2.fecha = e.fecha AND e2.id <= e.id))) + k.entregadas_inicio n_retiro,
                              u.nombre usuario
                              FROM entregas_repuesto e JOIN packs k ON k.id=e.pack_id LEFT JOIN ordenes o ON o.id=k.orden_id LEFT JOIN usuarios u ON u.id=e.usuario_id
                              WHERE k.cliente_id=? ORDER BY e.fecha DESC, e.id DESC""", (cid,)).fetchall()
    segs = con.execute("SELECT s.*, u.nombre usuario FROM seguimientos s LEFT JOIN usuarios u ON u.id=s.usuario_id WHERE cliente_id=? ORDER BY hecho_en DESC", (cid,)).fetchall()
    fotos = con.execute("SELECT * FROM fotos WHERE cliente_id=? ORDER BY id DESC", (cid,)).fetchall()
    # El Porche: fechas de repuesto (compras sueltas + entregas de pack) → ritmo propio
    fechas_rep = [r[0] for r in con.execute("""SELECT DISTINCT substr(o.creado_en,1,10) FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id JOIN productos p ON p.id=l.producto_id
                                               WHERE o.cliente_id=? AND o.estado!='cancelada' AND p.categoria='repuesto'""", (cid,))]
    fechas_rep += [e["fecha"] for e in entregas]
    porche = next((r for r in lineas if r["sku"] and r["sku"].startswith("PRO-")), None)
    ritmo, confianza = ritmo_cliente(fechas_rep)
    ult_rep = max(fechas_rep) if fechas_rep else (porche["ultima"] if porche else None)
    proximo = (datetime.date.fromisoformat(ult_rep) + datetime.timedelta(days=ciclo_de(ritmo))) if ult_rep else None
    total = sum(o["total"] or 0 for o in validas)
    primera = min((o["creado_en"] for o in validas), default=None)
    ultima = max((o["creado_en"] for o in validas), default=None)
    dias_sin = (hoy - datetime.date.fromisoformat(ultima[:10])).days if ultima else None
    etiquetas = []
    if porche: etiquetas.append("Miembro PRO" + (f" · {c['porche_tamano']}" if c["porche_tamano"] else (f" · {'Grande' if porche['sku']=='PRO-G' else 'Mediano'}" if porche["sku"] in ("PRO-G", "PRO-M") else "")))
    elif c["porche_version"] == "PRO": etiquetas.append("Miembro PRO" + (f" · {c['porche_tamano']}" if c["porche_tamano"] else ""))
    elif c["porche_version"] == "Básico": etiquetas.append("Porche Básico" + (f" · {c['porche_tamano']}" if c["porche_tamano"] else ""))
    saldo_pack = sum(max(k["unidades"] - k["entregadas_inicio"] - k["entregadas"], 0) for k in packs)
    if saldo_pack: etiquetas.append(f"Pack activo · le quedan {saldo_pack}")
    prepagados = con.execute("SELECT COUNT(*) FROM repuestos_prepagados WHERE cliente_id=? AND entregado_en IS NULL", (cid,)).fetchone()[0]
    if prepagados: etiquetas.append(f"{prepagados} repuesto{'s' if prepagados > 1 else ''} pagado{'s' if prepagados > 1 else ''} por entregar")
    if dias_sin is not None and dias_sin > 90: etiquetas.append("Inactivo")
    if not validas: etiquetas.append("Sin compras")
    formas = con.execute("SELECT forma, COUNT(*) n FROM pagos p JOIN ordenes o ON o.id=p.orden_id WHERE o.cliente_id=? AND o.estado!='cancelada' GROUP BY forma ORDER BY n DESC", (cid,)).fetchall()
    canales = con.execute("SELECT canal, COUNT(*) n FROM ordenes WHERE cliente_id=? AND estado!='cancelada' GROUP BY canal ORDER BY n DESC", (cid,)).fetchall()
    entregas_tipo = con.execute("SELECT tipo_entrega, COUNT(*) n FROM ordenes WHERE cliente_id=? AND estado!='cancelada' AND tipo_entrega IS NOT NULL GROUP BY 1 ORDER BY n DESC", (cid,)).fetchall()
    # números del cliente
    fechas_o = sorted({o["creado_en"][:10] for o in validas})
    gaps = [(datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days for a, b in zip(fechas_o, fechas_o[1:])]
    n = len(validas)
    mayor = max(validas, key=lambda o: o["total"] or 0) if validas else None
    favorito = max(lineas, key=lambda r: r["n"]) if lineas else None
    unidades = sum(r["n"] for r in lineas)
    por_mes = {}
    for o in validas: por_mes[o["creado_en"][:7]] = por_mes.get(o["creado_en"][:7], 0) + (o["total"] or 0)
    meses = []
    d = hoy.replace(day=1)
    for i in range(11, -1, -1):
        m = (d.year, d.month - i); y, mm = m[0] + (m[1] - 1) // 12, (m[1] - 1) % 12 + 1
        k = f"{y:04d}-{mm:02d}"; meses.append((MESES_N[mm - 1][:3].capitalize(), por_mes.get(k, 0)))
    # posición frente a los demás clientes
    totales = [r[0] for r in con.execute("SELECT COALESCE(SUM(total),0) FROM ordenes WHERE estado!='cancelada' GROUP BY cliente_id")]
    mejor_que = round(100 * sum(1 for x in totales if x < total) / len(totales)) if len(totales) > 1 and total else None
    promedio_cliente = sum(totales) / len(totales) if totales else 0
    problemas = con.execute("SELECT COUNT(*) FROM incidencias i JOIN ordenes o ON o.id=i.orden_id WHERE o.cliente_id=?", (cid,)).fetchone()[0]
    rechazados = con.execute("SELECT COUNT(*) FROM pagos p JOIN ordenes o ON o.id=p.orden_id WHERE o.cliente_id=? AND p.estado='rechazado'", (cid,)).fetchone()[0]
    canceladas = sum(1 for o in ordenes if o["estado"] == "cancelada")
    tipo = tipo_cliente(n, dias_sin, total, corte_vip(con)); etiquetas.insert(0, TIPOS_CLIENTE[tipo])
    etiquetas = [e for e in etiquetas if e not in ("Inactivo", "Sin compras") or e == TIPOS_CLIENTE[tipo]]
    etiquetas = list(dict.fromkeys(etiquetas))
    nums = dict(n=n, total=total, ticket=total / n if n else 0, primera=primera, ultima=ultima, dias_sin=dias_sin,
                cada=round(sum(gaps) / len(gaps)) if gaps else None, mayor=mayor, favorito=favorito, unidades=unidades, meses=meses, max_mes=max((v for _, v in meses), default=0),
                mejor_que=mejor_que, promedio_cliente=promedio_cliente, problemas=problemas, rechazados=rechazados, canceladas=canceladas,
                repuestos=sum(r["n"] for r in lineas if r["categoria"] == "repuesto"))
    refirio = con.execute("""SELECT id, nombre, substr(creado_en,1,10) desde,
                             COALESCE((SELECT SUM(total) FROM ordenes o WHERE o.cliente_id=clientes.id AND o.estado!='cancelada'),0) gastado
                             FROM clientes WHERE referido_id=? ORDER BY id""", (c["id"],)).fetchall()
    lo_trajo = con.execute("SELECT id, nombre FROM clientes WHERE id=?", (c["referido_id"],)).fetchone() if c["referido_id"] else None
    # lo que ha pasado en sus entregas: queda en su ficha para siempre, abierto o ya resuelto
    incid = con.execute("""SELECT i.*, o.numero, substr(i.creado_en,1,10) fecha FROM incidencias i JOIN ordenes o ON o.id=i.orden_id
                           WHERE o.cliente_id=? ORDER BY i.id DESC""", (cid,)).fetchall()
    return render(request, "cliente.html", seccion="clientes", incid=incid,
                  credito=credito_de(con, cid),
                  credito_mov=con.execute("""SELECT k.*, o.numero, o.total,
                        (SELECT COALESCE(SUM(p.monto_usd),0) FROM pagos p WHERE p.orden_id=k.orden_id AND p.estado='confirmado' AND p.forma!=?) pagado
                        FROM credito_cliente k LEFT JOIN ordenes o ON o.id=k.orden_id
                        WHERE k.cliente_id=? ORDER BY k.id DESC LIMIT 12""", (SALDO_FAVOR, cid)).fetchall(),
                  cajas=con.execute("SELECT id, nombre FROM cuentas WHERE activa=1 ORDER BY orden").fetchall(), c=c, perros=perros, dirs=dirs, notas=notas, ordenes=ordenes, comprado=comprado, catalogo=catalogo,
                  refirio=refirio, lo_trajo=lo_trajo,
                  packs=packs, entregas=entregas, segs=segs, fotos=fotos, total=total, n_ordenes=n, primera=primera, ultima=ultima, dias_sin=dias_sin,
                  etiquetas=etiquetas, ritmo=ritmo, confianza=confianza, ult_rep=ult_rep, proximo=proximo, porche=porche, nums=nums, saldo_pack=saldo_pack, n_prepagados=prepagados,
                  oportunidades=oportunidades_cliente(lineas, perros, ya_tiene_porche=(c["porche_version"] in ("PRO", "Básico")), tiene_basico=(c["porche_version"] == "Básico")), formas=formas, canales=canales, entregas_tipo=entregas_tipo,
                  ENTREGA=ENTREGA, E_LABEL=E_LABEL, P_LABEL=P_LABEL, RESULTADOS=RESULTADOS)


@app.post("/clientes/{cid}/editar")
async def cliente_editar(request: Request, cid: int, con=Depends(db)):
    if "coordinar" not in PERMISOS[rol_de(request)]: return RedirectResponse("/operaciones", status_code=303)
    f = await request.form()
    # Solo se cambia lo que el formulario trae de verdad. Si un campo no viene, se deja como
    # estaba: un formulario incompleto no puede borrarle el teléfono ni el apellido a un cliente.
    actual = con.execute("SELECT * FROM clientes WHERE id=?", (cid,)).fetchone()
    if not actual: return RedirectResponse("/clientes", status_code=303)
    limpiar = {
        "nombre_pila":    lambda v: capitalizar(v) or actual["nombre_pila"],   # sin nombre no se queda
        "apellido":       lambda v: capitalizar(v, inicio=False) or None,
        "telefono":       lambda v: normalizar_telefono(v),
        "cedula":         lambda v: (v or "").strip().upper() or None,
        "correo":         lambda v: (v or "").strip() or None,
        "ciudad":         lambda v: (v or "").strip() or None,
        "porche_version": lambda v: (v or "").strip() or None,
    }
    campos = {k: fn(f.get(k)) for k, fn in limpiar.items() if k in f}
    if "ciudad" in f: campos["ciudad"], campos["estado"] = normalizar_ciudad(f.get("ciudad"))
    if "porche_tamano" in f:
        campos["porche_tamano"] = ", ".join(x for x in ("Mediano", "Grande") if x in f.getlist("porche_tamano")) or None
    if "nombre_pila" in campos or "apellido" in campos:
        campos["nombre"] = nombre_completo(campos.get("nombre_pila", actual["nombre_pila"]),
                                           campos.get("apellido", actual["apellido"]))
    if campos:
        con.execute(f"UPDATE clientes SET {', '.join(k + '=?' for k in campos)} WHERE id=?", (*campos.values(), cid))
        con.commit()
    return RedirectResponse(f"/clientes/{cid}", 303)


@app.post("/clientes/{cid}/direccion")
async def cliente_direccion(request: Request, cid: int, con=Depends(db)):
    if "coordinar" not in PERMISOS[rol_de(request)]: return RedirectResponse("/operaciones", status_code=303)
    f = await request.form(); did = f.get("id")
    if f.get("borrar") and did:
        con.execute("DELETE FROM direcciones WHERE id=? AND cliente_id=?", (did, cid))
    elif did:
        vieja = con.execute("SELECT direccion FROM direcciones WHERE id=? AND cliente_id=?", (did, cid)).fetchone()
        con.execute("UPDATE direcciones SET etiqueta=?, direccion=?, maps=?, ciudad=? WHERE id=? AND cliente_id=?", (f.get("etiqueta") or "Principal", f["direccion"], f.get("maps") or None, normalizar_ciudad(f.get("ciudad"))[0], did, cid))
        # las órdenes que todavía no se entregan y usaban esa dirección, se corrigen también:
        # si no, Operaciones y el despachador siguen viendo la vieja
        if vieja:
            n = con.execute("""UPDATE ordenes SET direccion=?, maps=COALESCE(?, maps), ciudad=COALESCE(?, ciudad)
                               WHERE cliente_id=? AND estado IN ('pendiente','en_ruta') AND tipo_entrega NOT IN ('pickup','distribuidor','nacional')
                                 AND (direccion=? OR direccion IS NULL OR direccion='')""",
                            (f["direccion"], f.get("maps") or None, normalizar_ciudad(f.get("ciudad"))[0], cid, vieja["direccion"])).rowcount
            for (oid,) in con.execute("SELECT id FROM ordenes WHERE cliente_id=? AND estado IN ('pendiente','en_ruta') AND direccion=?", (cid, f["direccion"])).fetchall() if n else []:
                registrar(con, oid, uid_de(request), "entrega", "Dirección actualizada desde la ficha del cliente")
    elif f.get("direccion"):
        primera = con.execute("SELECT COUNT(*) FROM direcciones WHERE cliente_id=?", (cid,)).fetchone()[0] == 0
        con.execute("INSERT INTO direcciones (cliente_id,etiqueta,direccion,maps,ciudad,principal) VALUES (?,?,?,?,?,?)", (cid, f.get("etiqueta") or ("Principal" if primera else "Otra"), f["direccion"], f.get("maps") or None, normalizar_ciudad(f.get("ciudad"))[0], 1 if primera else 0))
    con.commit(); return RedirectResponse(f"/clientes/{cid}", 303)


def parsear_cumple(txt):
    """'21/09/2024' -> ('2024-09-21', None); '21/09' -> (None, '09-21'); otro -> (None, None)."""
    txt = (txt or "").strip()
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{4}))?$", txt)
    if not m: return None, None
    d, mo, a = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (1 <= d <= 31 and 1 <= mo <= 12): return None, None
    if a: return f"{a}-{mo:02d}-{d:02d}", None
    return None, f"{mo:02d}-{d:02d}"

@app.post("/clientes/{cid}/perro")
async def cliente_perro(request: Request, cid: int, con=Depends(db)):
    if "coordinar" not in PERMISOS[rol_de(request)]: return RedirectResponse("/operaciones", status_code=303)
    f = await request.form(); mid = f.get("id")
    if f.get("borrar") and mid:
        con.execute("DELETE FROM mascotas WHERE id=? AND cliente_id=?", (mid, cid))
    elif mid:
        fn, md = parsear_cumple(f.get("cumple"))
        con.execute("UPDATE mascotas SET nombre=?, raza=?, fecha_nacimiento=?, cumple_mes_dia=?, revisar=0, notas=? WHERE id=? AND cliente_id=?",
                    (f["nombre"].strip(), (f.get("raza") or "").strip() or None, fn, md, f.get("notas") or None, mid, cid))
    elif f.get("nombre", "").strip():
        fn, md = parsear_cumple(f.get("cumple"))
        con.execute("INSERT INTO mascotas (cliente_id,nombre,raza,fecha_nacimiento,cumple_mes_dia,notas) VALUES (?,?,?,?,?,?)",
                    (cid, f["nombre"].strip(), (f.get("raza") or "").strip() or None, fn, md, f.get("notas") or None))
    con.commit(); return RedirectResponse(f"/clientes/{cid}", 303)


@app.post("/clientes/{cid}/nota")
async def cliente_nota(request: Request, cid: int, con=Depends(db)):
    f = await request.form()
    if f.get("borrar"):
        con.execute("DELETE FROM notas_cliente WHERE id=? AND cliente_id=?", (f["borrar"], cid))
    elif f.get("texto", "").strip():
        con.execute("INSERT INTO notas_cliente (cliente_id,tipo,texto,mostrar_en_orden,mostrar_logistica,autor_id) VALUES (?,?,?,?,?,?)",
                    (cid, "general", f["texto"].strip(), 1, 0 if f.get("privada") else 1, uid_de(request)))
    con.commit(); return RedirectResponse(f"/clientes/{cid}", 303)


# ------------------------------------------------------------------ DESPACHADORES
def fijar_pago_despachador(con, oid):
    """Al despachador se le paga lo mismo que el cliente pagó de delivery, siempre.
    No se guarda una copia: se lee del delivery cada vez. Así, si cambias el delivery o le pasas
    la orden a otro despachador, lo que le debes se ajusta solo. Las tarifas por zona son solo
    referencia para chequear; no deciden lo que se le paga."""
    con.execute("UPDATE ordenes SET pago_despachador=NULL WHERE id=?", (oid,))

OFICINAS_AGENCIA = {"Tealca": {"Los Palos Grandes": 5.0, "Catia": 10.0}}   # valor de arranque; se cambia en Tarifas

def oficinas_agencia(con):
    """Las agencias en que se paga distinto según la oficina a la que se lleva (Cristina, 6 oct: Tealca Los Palos
    Grandes $5, Tealca Catia $10). Las demás agencias no se distinguen por oficina."""
    return cfg_json(con, "oficinas_agencia", OFICINAS_AGENCIA) or OFICINAS_AGENCIA


def tarifa_agencia(con, agencia):
    """Llevar los pedidos a la agencia se paga por viaje, no por pedido: lleve 1 o lleve 6, es la misma tarifa."""
    t = cfg_json(con, "tarifa_agencia", {}) or {}
    return float(t.get(agencia, t.get("*", 5)))


def adelanto_despachador(con, nombre):
    """Los adelantos que se le dieron (gasto Despachadores › Adelanto a su nombre, desde su ficha o desde Gastos)
    y cuánto de eso queda por descontar de sus entregas."""
    lista = con.execute("""SELECT g.id, g.fecha, g.monto_usd, g.notas, cu.nombre caja FROM gastos g LEFT JOIN cuentas cu ON cu.id=g.cuenta_id
                           WHERE g.categoria='Despachadores' AND g.subcategoria='Adelanto' AND TRIM(COALESCE(g.proveedor,''))=?
                           ORDER BY g.fecha DESC, g.id DESC""", (nombre,)).fetchall()
    usado = con.execute("SELECT COALESCE(SUM(adelanto_usado),0) FROM pagos_despachador WHERE despachador=?", (nombre,)).fetchone()[0]
    return lista, round(max(sum(a["monto_usd"] or 0 for a in lista) - usado, 0), 2)


def resumen_despachador(con, nombre, hoy):
    """Lo que se le debe: cada orden asignada (no cancelada) suma su pago hasta que la marcas pagada (los lunes)."""
    # se le debe lo que ya ENTREGÓ: asignado no es ganado (se puede cambiar el despachador, o pasar a pick-up)
    s = con.execute("""SELECT SUM(CASE WHEN despachador_pagado=0 AND estado='entregada' THEN COALESCE(delivery, 0) ELSE 0 END) debe,
                              SUM(CASE WHEN despachador_pagado=0 AND estado='entregada' THEN 1 ELSE 0 END) n_debe,
                              SUM(CASE WHEN estado IN ('pendiente','en_ruta') THEN 1 ELSE 0 END) n_sin_entregar
                       FROM ordenes WHERE despachador=? AND estado!='cancelada' AND origen_excel=0""", (nombre,)).fetchone()
    ult = con.execute("SELECT fecha, monto FROM pagos_despachador WHERE despachador=? ORDER BY fecha DESC, id DESC LIMIT 1", (nombre,)).fetchone()
    zonas = con.execute("""SELECT COALESCE(NULLIF(zona,''), 'Sin zona') z, COUNT(*) n FROM ordenes
                           WHERE despachador=? AND estado='entregada' AND origen_excel=0 AND despachador_pagado=0 GROUP BY 1 ORDER BY 2 DESC LIMIT 6""", (nombre,)).fetchall()
    v = con.execute("SELECT COALESCE(SUM(monto),0) m, COUNT(*) n FROM viajes_agencia WHERE despachador=? AND pagado=0 AND llevado_en IS NOT NULL", (nombre,)).fetchone()
    r = dict(s)
    r["debe"] = (r["debe"] or 0) + v["m"]          # los viajes a la agencia se le pagan igual que las entregas
    r["n_viajes"] = v["n"]; r["debe_viajes"] = v["m"]   # se cuentan aparte: son viajes, no entregas
    vf = con.execute("""SELECT COALESCE(SUM(monto),0) m, COALESCE(SUM(tipo='fallido'),0) nf, COALESCE(SUM(tipo='retiro'),0) nr,
                        COALESCE(SUM(tipo='diligencia'),0) nd FROM viajes_despachador WHERE despachador=? AND pagado=0 AND por_aprobar=0""", (nombre,)).fetchone()
    # retiros de pack / prepagados que llevó, y viajes en que fue y no le recibieron: también se le pagan
    # un retiro de repuesto que llevó es una entrega más: se cuenta junto con las demás
    r["debe"] += vf["m"]; r["n_fallidos"] = vf["nf"]; r["n_debe"] = (r["n_debe"] or 0) + vf["nr"]; r["n_retiros"] = 0
    r["n_diligencias"] = vf["nd"]   # encargos sueltos que no van con un pedido (buscar tela, llevar algo al taller…)
    # las que anotó él mismo no cuentan hasta que Cristina las apruebe
    r["n_dil_aprobar"] = con.execute("SELECT COUNT(*) FROM viajes_despachador WHERE despachador=? AND por_aprobar=1", (nombre,)).fetchone()[0]
    # lo que se le adelantó se descuenta de lo que se le debe; si adelantaste más de lo que ha hecho, queda a favor tuyo
    r["adelantos"], r["adelanto"] = adelanto_despachador(con, nombre)
    r["debe_bruto"] = r["debe"]
    r["adelanto_resta"] = round(min(r["adelanto"], r["debe"]), 2)
    r["debe"] = round(r["debe"] - r["adelanto_resta"], 2)
    r["adelanto_libre"] = round(r["adelanto"] - r["adelanto_resta"], 2)
    return r | {"zonas": zonas, "ultimo_pago": ult}


@app.get("/mis-entregas", response_class=HTMLResponse)
def mis_entregas(request: Request, con=Depends(db)):
    """La pantalla del despachador: lo que le toca hoy, lo que se le debe y lo que ha entregado.
    Ve dinero, pero solo el suyo: nunca el de la empresa ni el de otro despachador."""
    u = quien_es(request)
    nombre = (u or {}).get("despachador")
    viendo = (not u or u["rol"] == "admin")   # Cristina mirando cómo lo ve un despachador
    if viendo:
        nombre = (request.query_params.get("quien") or request.cookies.get("ver_desp") or "").strip()
        if nombre not in DESPACHADORES: nombre = DESPACHADORES[0] if DESPACHADORES else ""
    if not nombre: return RedirectResponse("/inicio", status_code=303)
    hoy = datetime.date.today()
    r = resumen_despachador(con, nombre, hoy)
    ruta = ruta_despachador(con, nombre, hoy.isoformat())
    hist = con.execute("""SELECT o.numero, COALESCE(o.fecha_entrega, substr(o.creado_en,1,10)) fecha,
                          COALESCE(o.delivery,0) pago, o.estado, o.despachador_pagado,
                          COALESCE(NULLIF(c.nombre_pila,''), c.nombre) quien, NULLIF(TRIM(o.zona),'') zona,
                          COALESCE(NULLIF(TRIM(o.direccion),''), (SELECT d.direccion FROM direcciones d WHERE d.cliente_id=o.cliente_id
                                                                  ORDER BY d.principal DESC, d.id LIMIT 1)) direccion
                          FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id
                          WHERE o.despachador=? AND o.estado!='cancelada' AND o.origen_excel=0
                            -- lo que ya le pagaron y él confirmó que le llegó se le quita de la vista (Cristina lo sigue viendo en su ficha)
                            AND NOT (o.despachador_pagado=1 AND (SELECT p.confirmado_en FROM pagos_despachador p WHERE p.id=o.despachador_pago_id) IS NOT NULL)
                          ORDER BY fecha DESC, o.id DESC LIMIT 60""", (nombre,)).fetchall()
    mes = hoy.strftime("%Y-%m")
    pagos = con.execute("""SELECT id, fecha, monto, entregas, adelanto_usado, reclamo_monto, reclamo_nota, reclamo_en FROM pagos_despachador
                           WHERE despachador=? AND confirmado_en IS NULL ORDER BY fecha DESC, id DESC""", (nombre,)).fetchall()
    ult_conf = con.execute("SELECT MAX(substr(confirmado_en,1,10)) FROM pagos_despachador WHERE despachador=?", (nombre,)).fetchone()[0]
    if ult_conf: r["adelantos"] = [a for a in r["adelantos"] if (a["fecha"] or "") > ult_conf or r["adelanto_libre"] > 0]
    for f in ruta:
        if f.get("kind") != "orden": continue   # los retiros ya traen si van en ruta
        o_ = con.execute("SELECT estado FROM ordenes WHERE id=?", (f["id"],)).fetchone() if f.get("id") else None
        f["en_ruta"] = bool(o_ and o_["estado"] == "en_ruta")
    hist_todo = sorted([dict(h) for h in hist] + viajes_hist(con, nombre, vigentes=True), key=lambda h: h["fecha"] or "", reverse=True)
    viajes_pend = con.execute("""SELECT v.*, (SELECT group_concat(COALESCE(NULLIF(c.nombre_pila,''), c.nombre) || ' ' || o.numero, ' · ') FROM ordenes o
                                 LEFT JOIN clientes c ON c.id=o.cliente_id WHERE o.viaje_id=v.id) cuales
                                 FROM viajes_agencia v WHERE v.despachador=? AND v.llevado_en IS NULL ORDER BY v.fecha""", (nombre,)).fetchall()
    # lo que la agencia le va a pedir por cada paquete: a quién va, cédula, teléfono, a qué oficina y qué lleva
    viajes_pend = [dict(v) for v in viajes_pend]
    for v in viajes_pend:
        v["paquetes"] = []
        for o in con.execute("""SELECT o.id, o.numero, o.ciudad, o.direccion, o.modalidad_envio, NULLIF(TRIM(o.receptor_nombre),'') recibe, NULLIF(TRIM(o.receptor_telefono),'') recibe_tel,
                                c.nombre, c.cedula, c.telefono, NULLIF(TRIM(c.correo),'') correo FROM ordenes o JOIN clientes c ON c.id=o.cliente_id WHERE o.viaje_id=? ORDER BY o.id""", (v["id"],)):
            o = dict(o)
            if (o["recibe"] or "").startswith("otra persona"): o["recibe"] = None   # texto genérico de la migración, no es un nombre
            o["lleva"] = lo_que_lleva(con, o["id"], lambda ya, n, t: f"{ya + 1}/{t}" if n <= 1 else f"{ya + 1}-{ya + n}/{t}")[0]
            v["paquetes"].append(o)
    # "Lo que has entregado" es solo lo que ya hizo: lo pendiente o en ruta está en "Lo que te toca hoy"
    hist_todo = [h for h in hist_todo if h["estado"] not in ("pendiente", "en_ruta")]
    # lo que le toca mañana, para que se organice (Cristina, 6 oct): solo para mirar, se marca el día que toca
    clave_r = lambda f: (f.get("kind"), f.get("id") or f.get("rid"))
    de_hoy = {clave_r(f) for f in ruta}
    manana = (hoy + datetime.timedelta(days=1)).isoformat()
    ruta_manana = [f for f in ruta_despachador(con, nombre, manana) if clave_r(f) not in de_hoy]
    # los viajes a la agencia también van el día que tocan: el de mañana no es de hoy (el atrasado sí sigue en hoy)
    viajes_manana = [v for v in viajes_pend if (v["fecha"] or "")[:10] == manana]
    viajes_pend = [v for v in viajes_pend if (v["fecha"] or "")[:10] <= hoy.isoformat()]
    return render(request, "mis_entregas.html", seccion="mis_entregas", quien=nombre, viendo=viendo, r=r, ruta=ruta, ruta_manana=ruta_manana, viajes_manana=viajes_manana,
                  ruta_cobrar=sum(f["cobrar"] for f in ruta),
                  hist=hist_todo, pagos=pagos, hoy_iso=hoy.isoformat(), viajes_pend=viajes_pend, tarifas_dil=tarifas_diligencia(con),
                  ganado_mes=round(sum(h["pago"] for h in hist if h["estado"] == "entregada" and (h["fecha"] or "")[:7] == mes), 2),
                  ganado_todo=round(sum(h["pago"] for h in hist if h["estado"] == "entregada"), 2),
                  n_entregadas=sum(1 for h in hist_todo if h["estado"] == "entregada" and not (h["quien"] or "").startswith("Viaje a ")))   # un viaje a la agencia no es una entrega


@app.post("/ordenes/{oid}/pack/{kid}/hoy")
def pack_cuantos_hoy(request: Request, oid: int, kid: int, cuantos: str = Form(""), con=Depends(db)):
    """El cliente cambió de idea antes de que le entregaran: se lleva otra cantidad del pack con esta entrega."""
    if "coordinar" not in PERMISOS[rol_de(request)]: return volver(oid, request)
    o = con.execute("SELECT estado FROM ordenes WHERE id=?", (oid,)).fetchone()
    k = con.execute("SELECT * FROM packs WHERE id=? AND orden_id=?", (kid, oid)).fetchone()
    if o and k and o["estado"] in ("pendiente", "en_ruta") and cuantos.isdigit():
        ya = con.execute("SELECT COUNT(*) FROM entregas_repuesto WHERE pack_id=?", (kid,)).fetchone()[0]
        n = max(0, min(int(cuantos), k["unidades"] - ya))
        if n != k["entregadas_inicio"]:
            con.execute("UPDATE packs SET entregadas_inicio=? WHERE id=?", (n, kid))
            registrar(con, oid, uid_de(request), "pack", f"Se lleva {n} de {k['unidades']} con esta entrega (antes {k['entregadas_inicio']})")
            con.commit()
    return volver(oid, request)


@app.post("/ordenes/{oid}/no-recibio")
def orden_no_recibio(request: Request, oid: int, motivo: str = Form(""), fecha: str = Form(""), fue: str = Form(""),
                     volver: str = "/operaciones", con=Depends(db)):
    """Iba en ruta y no se pudo entregar. Vuelve a pendiente. Si el despachador llegó hasta el sitio,
    ese viaje se le paga igual; si no llegó a ir, no. Sin fecha nueva, queda sin despachador para volver a coordinarla."""
    rol = rol_de(request); u = quien_es(request)
    es_desp = rol == "despachador"
    if rol not in ("admin", "logistica") and not es_desp: return RedirectResponse("/inicio", status_code=303)
    o = con.execute("SELECT estado, despachador, COALESCE(delivery,0) delivery FROM ordenes WHERE id=?", (oid,)).fetchone()
    if es_desp: volver = "/mis-entregas"
    if es_desp and u and u["rol"] == "despachador" and (not o or o["despachador"] != u["despachador"]):
        return RedirectResponse(volver, status_code=303)   # solo las suyas
    if o and o["estado"] == "en_ruta":
        f = "" if es_desp else (fecha or "").strip()
        hoy_ = datetime.date.today().isoformat()
        texto = "En ruta → Pendiente: no se pudo entregar"
        if fue == "1" and o["despachador"]:
            con.execute("INSERT INTO viajes_despachador (orden_id, fecha, despachador, monto, motivo, usuario_id) VALUES (?,?,?,?,?,?)",
                        (oid, hoy_, o["despachador"], o["delivery"], motivo.strip() or None, uid_de(request)))
            texto += f" · {o['despachador']} fue al sitio: se le paga el viaje ({fmt_usd(o['delivery'])})"
        elif fue == "0":
            texto += f" · {o['despachador'] or 'el despachador'} no llegó a ir"
        if f:
            texto += f" · se vuelve a llevar {fmt_fecha(f) if fmt_fecha(f) in ('hoy', 'mañana') else 'el ' + fmt_fecha(f)}"
            con.execute("UPDATE ordenes SET estado='pendiente', fecha_prometida=?, actualizado_en=datetime('now','localtime') WHERE id=?", (f, oid))
        else:   # sin fecha: vuelve a "sin coordinar" para que Cristina o logística decidan cuándo y con quién
            texto += " · por coordinar de nuevo"
            con.execute("UPDATE ordenes SET estado='pendiente', despachador=NULL, actualizado_en=datetime('now','localtime') WHERE id=?", (oid,))
        registrar(con, oid, uid_de(request), "estado", texto, motivo.strip() or None)
        con.commit()
    return RedirectResponse(volver if volver.startswith("/") else "/operaciones", status_code=303)


@app.post("/mis-entregas/pago/{pid}/reclamo")
def mis_entregas_reclamo_pago(request: Request, pid: int, llego: str = Form(""), nota: str = Form(""), con=Depends(db)):
    """Le llegó menos de lo que dice el pago: se lo avisa a Cristina con cuánto le llegó y por qué cree que falta."""
    u = quien_es(request); yo = (u or {}).get("despachador")
    es_admin = rol_de(request) == "admin" or (u and u["rol"] == "admin")
    p = con.execute("SELECT despachador FROM pagos_despachador WHERE id=?", (pid,)).fetchone()
    if p and (es_admin or (yo and p["despachador"] == yo)):
        con.execute("""UPDATE pagos_despachador SET reclamo_monto=?, reclamo_nota=?, reclamo_en=datetime('now','localtime'), reclamo_resuelto=NULL
                       WHERE id=? AND confirmado_en IS NULL""", (cifra(llego) if llego.strip() else None, nota.strip() or None, pid))
        con.commit()
    return RedirectResponse("/mis-entregas", status_code=303)


@app.post("/despachadores/pago/{pid}/resuelto")
def despachador_reclamo_resuelto(request: Request, pid: int, nota: str = Form(""), como: str = Form(""),
                                 monto: str = Form(""), cuenta_id: str = Form(""), con=Depends(db)):
    """Cristina aclaró el reclamo del despachador (le mandó lo que faltaba, o era un error suyo).
    Si lo que faltaba salió de OTRA caja, el pago original se reparte: esa parte sale de la otra caja."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    p = con.execute("SELECT * FROM pagos_despachador WHERE id=?", (pid,)).fetchone()
    if p and como == "otra_caja":
        x = round(float(cifra(monto) or 0), 2)
        cu = con.execute("SELECT id, nombre FROM cuentas WHERE id=? AND activa=1", (int(cuenta_id),)).fetchone() if cuenta_id.isdigit() else None
        gid = p["gasto_id"] or (con.execute("""SELECT id FROM gastos WHERE categoria='Despachadores' AND proveedor=? AND fecha=?
                                               ORDER BY ABS(monto_usd - ?) LIMIT 1""", (p["despachador"], p["fecha"], (p["monto"] or 0) - (p["adelanto_usado"] or 0))).fetchone() or [None])[0]
        g = con.execute("SELECT * FROM gastos WHERE id=?", (gid,)).fetchone() if gid else None
        if not (x > 0 and cu and g and x < (g["monto_usd"] or 0)):
            d = con.execute("SELECT id FROM despachadores WHERE nombre=?", (p["despachador"],)).fetchone()
            return RedirectResponse(f"/despachadores/{d['id']}" if d else "/despachadores", status_code=303)
        # de la caja original salió menos; lo que faltaba salió de la otra. El total pagado no cambia.
        con.execute("UPDATE gastos SET monto_usd=monto_usd-?, monto_real=monto_real-? WHERE id=?", (x, x, g["id"]))
        con.execute("""INSERT INTO gastos (fecha, monto_usd, monto_real, moneda, categoria, subcategoria, descripcion, proveedor, cuenta_id, usuario_id)
                       VALUES (?,?,?,'USD','Despachadores','Pago semanal',?,?,?,?)""",
                    (datetime.date.today().isoformat(), x, x, f"Lo que faltaba del pago del {fmt_dia(p['fecha'])}", p["despachador"], cu["id"], uid_de(request)))
        como = f"le mandé {fmt_usd(x)} que faltaban desde {cu['nombre']}"
    con.execute("UPDATE pagos_despachador SET reclamo_resuelto=? WHERE id=?",
                (("el " + datetime.datetime.now().strftime("%d/%m") + (" · " + como.strip() if como.strip() else "") + (" · " + nota.strip() if nota.strip() else "")), pid))
    con.commit()
    d = con.execute("SELECT id FROM despachadores WHERE nombre=?", (p["despachador"],)).fetchone() if p else None
    return RedirectResponse(f"/despachadores/{d['id']}" if d else "/despachadores", status_code=303)


@app.post("/mis-entregas/pago/{pid}/confirmar")
def mis_entregas_confirmar_pago(request: Request, pid: int, con=Depends(db)):
    """El despachador confirma que el pago le llegó: desde ahí ese pago y lo que cubría salen de su pantalla."""
    u = quien_es(request); yo = (u or {}).get("despachador")
    es_admin = rol_de(request) == "admin" or (u and u["rol"] == "admin")
    p = con.execute("SELECT despachador FROM pagos_despachador WHERE id=?", (pid,)).fetchone()
    if p and (es_admin or (yo and p["despachador"] == yo)):
        con.execute("UPDATE pagos_despachador SET confirmado_en=datetime('now','localtime') WHERE id=? AND confirmado_en IS NULL", (pid,))
        con.commit()
    return RedirectResponse("/mis-entregas", status_code=303)


@app.post("/mis-entregas/retiro/{kind}/{rid}/{accion}")
def mis_entregas_retiro(request: Request, kind: str, rid: int, accion: str, forma_recibida: str = Form(""), monto_recibido: str = Form(""),
                        fue: str = Form(""), motivo: str = Form(""), forma_otro: str = Form(""), con=Depends(db)):
    """El despachador con un retiro de pack o un repuesto prepagado: sale, se arrepiente, lo entrega o no pudo entregarlo."""
    u = quien_es(request); yo = (u or {}).get("despachador")
    es_admin = rol_de(request) == "admin" or (u and u["rol"] == "admin")
    tabla, col = ("packs", "despachador_programado") if kind == "pack" else ("repuestos_prepagados", "despachador")
    k = con.execute(f"SELECT * FROM {tabla} WHERE id=?", (rid,)).fetchone()
    if not k or not (es_admin or (yo and k[col] == yo)): return RedirectResponse("/mis-entregas", status_code=303)
    uid = uid_de(request)
    if accion == "salgo": con.execute(f"UPDATE {tabla} SET en_ruta=1 WHERE id=?", (rid,))
    elif accion == "aun-no": con.execute(f"UPDATE {tabla} SET en_ruta=0 WHERE id=?", (rid,))
    elif accion == "no-pude": retiro_no_recibio(con, "pack" if kind == "pack" else "prep", rid, uid, fue, motivo)
    elif accion == "entregado":
        debe = float((k["delivery_programado"] if kind == "pack" else k["delivery"]) or 0) if not k["delivery_pagado"] else 0
        forma_recibida = forma_del_despachador(forma_recibida, forma_otro)
        despues = forma_recibida == "despues"
        efectivo = not despues and (forma_recibida or "Efectivo").startswith("Efectivo")
        forma = caja_efectivo(con) if efectivo else (forma_recibida if not despues else "")
        pago = None if despues else ("confirmado" if efectivo else "por_confirmar")   # Zelle, Pago Móvil…: Cristina confirma que llegó
        monto = (float(cifra(monto_recibido) or 0) if str(monto_recibido).strip() else debe) if not despues else debe
        if kind == "pack":
            entregar_pack(con, rid, uid, cuantos=str(k["retiro_programado"] or 1), delivery_cobrado=str(monto if debe else 0), pago_forma=forma, pago=pago)
        else:
            entregar_prepagado(con, rid, uid, delivery_cobrado="1" if debe else "", delivery_forma=forma, pago=pago)
    con.commit(); return RedirectResponse("/mis-entregas", status_code=303)


@app.post("/mis-entregas/{oid}/aun-no")
def mis_entregas_aun_no(request: Request, oid: int, con=Depends(db)):
    """Tocó 'Voy saliendo' sin querer: vuelve a pendiente. Solo su propia orden, y solo si está en ruta."""
    u = quien_es(request); yo = (u or {}).get("despachador")
    o = con.execute("SELECT despachador, estado FROM ordenes WHERE id=?", (oid,)).fetchone()
    if o and o["estado"] == "en_ruta" and (o["despachador"] == yo or rol_de(request) == "admin" or (u and u["rol"] == "admin")):
        con.execute("UPDATE ordenes SET estado='pendiente', actualizado_en=datetime('now','localtime') WHERE id=?", (oid,))
        registrar(con, oid, uid_de(request), "estado", "En ruta → Pendiente (todavía no había salido)"); con.commit()
    return RedirectResponse("/mis-entregas", status_code=303)


@app.post("/mis-entregas/{oid}/incidencia")
def mi_incidencia(request: Request, oid: int, tipo: str = Form("Otro"), descripcion: str = Form(""), con=Depends(db)):
    """El despachador cuenta lo que pasó en la puerta. Es quien lo vio."""
    u = quien_es(request)
    es_admin = rol_de(request) == "admin" or (u and u["rol"] == "admin")   # Cristina probando "ver como"
    if not (u and (u["despachador"] or es_admin)): return RedirectResponse("/inicio", status_code=303)
    o = con.execute("SELECT despachador FROM ordenes WHERE id=?", (oid,)).fetchone()
    if not o or (not es_admin and o["despachador"] != u["despachador"]): return RedirectResponse("/mis-entregas", status_code=303)
    quien = u["despachador"] or o["despachador"]
    con.execute("INSERT INTO incidencias (orden_id,clase,tipo,descripcion,responsable,autor_id) VALUES (?,?,?,?,?,?)",
                (oid, "incidencia", tipo, descripcion.strip(), quien, u["id"]))
    registrar(con, oid, u["id"], "incidencia", f"{quien} avisó · {tipo}: {descripcion.strip()[:120]}")
    con.commit(); return RedirectResponse("/mis-entregas", status_code=303)


@app.get("/despachadores", response_class=HTMLResponse)
def despachadores(request: Request, q: str = "", con=Depends(db)):
    hoy = datetime.date.today()
    rows = [dict(d) | resumen_despachador(con, d["nombre"], hoy) for d in con.execute("SELECT * FROM despachadores WHERE nombre LIKE ? ORDER BY activo DESC, nombre", (f"%{q}%",))]
    return render(request, "despachadores.html", seccion="despachadores", despachadores=rows, q=q)


def indicaciones_cliente(con, cid, extra=None):
    """Lo que hay que saber para entregarle a este cliente: sus notas que no son privadas
    ('solo recibe hasta las 3pm', 'necesita ayuda para subir') y la nota de esta entrega, si hay."""
    notas = [r[0] for r in con.execute("SELECT texto FROM notas_cliente WHERE cliente_id=? AND mostrar_logistica=1 ORDER BY id", (cid,)) if r[0]]
    extra = (extra or "").strip()
    return ([extra] if extra and extra not in notas else []) + notas


def viajes_hist(con, nombre, vigentes=False):
    """Para su historial: los retiros de repuesto que llevó y los viajes en que no le recibieron,
    así no desaparecen de su lista aunque el pedido haya vuelto a pendiente o se haya asignado a otro."""
    out = []
    for v in con.execute("""SELECT v.*, o.numero, COALESCE(NULLIF(c.nombre_pila,''), c.nombre) quien, c.nombre cliente,
                            (SELECT d.direccion FROM direcciones d WHERE d.cliente_id=o.cliente_id ORDER BY d.principal DESC, d.id LIMIT 1) direccion,
                            (SELECT d.zona FROM direcciones d WHERE d.cliente_id=o.cliente_id ORDER BY d.principal DESC, d.id LIMIT 1) zona,
                            o.ciudad FROM viajes_despachador v LEFT JOIN ordenes o ON o.id=v.orden_id LEFT JOIN clientes c ON c.id=o.cliente_id
                            WHERE v.despachador=? """ + ("AND NOT (v.pagado=1 AND (SELECT p.confirmado_en FROM pagos_despachador p WHERE p.id=v.pago_id) IS NOT NULL) " if vigentes else "") +
                            """ORDER BY v.fecha DESC, v.id DESC LIMIT 60""", (nombre,)):
        if v["tipo"] == "diligencia":
            out.append({"id": None, "numero": None, "fecha": v["fecha"], "pago": v["monto"], "quien": "Diligencia", "cliente": None, "zona": None,
                        "direccion": v["motivo"], "ciudad": None, "despachador_pagado": v["pagado"], "estado": "diligencia", "nota": None, "que": None,
                        "por_aprobar": v["por_aprobar"]})
            continue
        out.append({"id": v["orden_id"], "numero": v["numero"], "fecha": v["fecha"], "pago": v["monto"], "quien": v["quien"], "cliente": v["cliente"],
                    "zona": v["zona"], "direccion": v["direccion"], "ciudad": v["ciudad"], "despachador_pagado": v["pagado"],
                    "estado": "no_entregado" if v["tipo"] == "fallido" else "entregada", "nota": v["motivo"],
                    "que": None})
    # los viajes a la agencia (MRW, Tealca…) también son trabajo suyo y se le pagan: que se vean en su lista
    for v in con.execute("""SELECT * FROM viajes_agencia WHERE despachador=? AND llevado_en IS NOT NULL """ + ("AND NOT (pagado=1 AND (SELECT p.confirmado_en FROM pagos_despachador p WHERE p.id=pago_id) IS NOT NULL) " if vigentes else "") +
                         """ORDER BY fecha DESC, id DESC LIMIT 60""", (nombre,)):
        n = v["pedidos"] or 0
        out.append({"id": None, "numero": None, "fecha": v["fecha"], "pago": v["monto"], "quien": f"Viaje a {v['agencia'] or 'la agencia'}" + (f" {v['oficina']}" if v["oficina"] else ""), "cliente": None,
                    "zona": None, "direccion": f"llevó {n} pedido{'s' if n != 1 else ''}" if n else None, "ciudad": None,
                    "despachador_pagado": v["pagado"], "estado": "entregada", "nota": v["nota"], "que": None})
    return out


def ruta_despachador(con, nombre, hoy):
    """La lista que se le manda al despachador por WhatsApp: a quién, dónde y qué lleva.
    Va el nombre de pila, la dirección y el teléfono — sin eso no puede entregar. No va cédula,
    ni correo, ni el total de la orden. El monto solo aparece si tiene que cobrarlo en la puerta."""
    filas = []
    for o in con.execute("""SELECT o.id, o.numero, o.total, o.estado_pago, o.tipo_entrega,
                            COALESCE(NULLIF(c.nombre_pila,''), c.nombre) quien, c.telefono,
                            o.direccion, o.maps, o.zona, o.ciudad, c.id cid, o.notas_entrega,
                            NULLIF(TRIM(o.receptor_nombre),'') recibe, NULLIF(TRIM(o.receptor_telefono),'') recibe_tel
                            FROM ordenes o JOIN clientes c ON c.id=o.cliente_id
                            WHERE o.despachador=? AND o.estado IN ('pendiente','en_ruta') AND o.origen_excel=0 AND """ + HAY_QUE_ENTREGAR() + """
                              AND o.tipo_entrega NOT IN ('pickup','distribuidor')
                              AND COALESCE(o.fecha_prometida, substr(o.creado_en,1,10)) <= ?
                            ORDER BY o.zona, o.id""", (nombre, hoy)):
        d = dict(o)
        if not _sirve(d["direccion"]) or not d["maps"]:      # la orden hereda la dirección del cliente si no trae una
            dd = con.execute("SELECT direccion, maps FROM direcciones WHERE cliente_id=? ORDER BY principal DESC, id LIMIT 1", (d["cid"],)).fetchone()
            if dd and not _sirve(d["direccion"]): d["direccion"] = dd["direccion"]
            d["maps"] = ubicacion_de(con, d["cid"], o["direccion"], d["maps"])
        pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (d["id"],)).fetchone()[0]
        falta = round((d["total"] or 0) - pagado, 2)
        # solo lo que el despachador tiene que cobrar en la puerta; lo demás no es asunto suyo
        d["cobrar"] = falta if (d["estado_pago"] in ("contra_entrega", "sin_pago", "abonada", "rechazado") and falta > 0) else 0
        d["cobrar_forma"] = forma_por_cobrar(con, d["id"]) if d["cobrar"] else None
        d["que_lleva"] = lo_que_lleva(con, d["id"], lambda ya, n, t: f"{ya + 1}/{t}" if n <= 1 else f"{ya + 1}-{ya + n}/{t}")[0]
        d["indicaciones"] = indicaciones_cliente(con, d["cid"], d["notas_entrega"])
        d["kind"] = "orden"
        d["avisos"] = [dict(r) for r in con.execute("SELECT tipo, descripcion FROM incidencias WHERE orden_id=? AND estado='abierta' ORDER BY id", (d["id"],))]
        filas.append(d)
    # retiros de pack y repuestos prepagados que le tocan: son entregas del mismo pedido, con sus mismos pasos
    def cli(cid):
        c = con.execute("SELECT COALESCE(NULLIF(nombre_pila,''), nombre) quien, telefono FROM clientes WHERE id=?", (cid,)).fetchone()
        dd = con.execute("SELECT direccion, maps, zona FROM direcciones WHERE cliente_id=? ORDER BY principal DESC, id LIMIT 1", (cid,)).fetchone()
        return {"quien": c["quien"] if c else "", "telefono": c["telefono"] if c else None,
                "direccion": dd["direccion"] if dd else None, "maps": dd["maps"] if dd else None, "zona": dd["zona"] if dd else None}
    for k in cargar_packs(con):
        if k["saldo"] > 0 and (k["despachador_programado"] or "") == nombre and k["fecha_programada"] and k["fecha_programada"] <= hoy \
           and (k["tipo_programado"] or k["tipo_entrega"]) in ("delivery", "delivery_fuera"):
            que, pos = repuesto_de_pack(k)
            filas.append(cli(k["cliente_id"]) | {"id": None, "kind": "pack", "rid": k["id"], "numero": k["orden"], "cid": k["cliente_id"],
                         "que_lleva": f"{que} · {pos} del pack", "en_ruta": bool(k.get("en_ruta")),
                         "cobrar": float(k["delivery_programado"] or 0) if not k["delivery_pagado"] else 0,
                         "indicaciones": indicaciones_cliente(con, k["cliente_id"], k["nota_programada"])})
    for r in cargar_prepagados(con):
        if (r["despachador"] or "") == nombre and r["fecha_programada"] and r["fecha_programada"] <= hoy and r["tipo_entrega"] in ("delivery", "delivery_fuera"):
            filas.append(cli(r["cliente_id"]) | {"id": None, "kind": "prep", "rid": r["id"], "numero": r["orden"], "cid": r["cliente_id"],
                         "que_lleva": f"1× Repuesto {r['tamano'] or ''} · ya pagado".strip(), "en_ruta": bool(r.get("en_ruta")),
                         "cobrar": float(r["delivery"] or 0) if not r["delivery_pagado"] else 0,
                         "indicaciones": indicaciones_cliente(con, r["cliente_id"], r["notas"])})
    return filas


def texto_ruta(filas, hoy):
    """El mensaje tal cual se le manda por WhatsApp."""
    out = [f"Decopet · {fecha_larga(hoy)}", ""]
    for i, f in enumerate(filas, 1):
        out.append(f"{i}. {f['quien']}")
        if tel_wa(f["telefono"]): out.append(f"   📞 {tel_wa(f['telefono'])}")   # solo en su línea y con +58: en WhatsApp se toca para llamar o escribir
        if f.get("recibe"):
            out.append(f"   Recibe: {f['recibe']}")
            if tel_wa(f.get("recibe_tel")): out.append(f"   📞 {tel_wa(f['recibe_tel'])}")
        if f["direccion"]: out.append(f"   {f['direccion']}")
        if f["maps"]: out.append(f"   {f['maps']}")
        out.append(f"   {f['que_lleva']}")
        for t in f.get("indicaciones") or []: out.append(f"   📌 {t}")
        if f["cobrar"]: out.append(f"   COBRAR ${f['cobrar']:,.2f}" + (f" · por {f['cobrar_forma']}" if f.get("cobrar_forma") else ""))
        out.append("")
    cobros = sum(f["cobrar"] for f in filas)
    if cobros: out.append(f"Total a cobrar: ${cobros:,.2f}")
    return "\n".join(out).strip()


@app.get("/despachadores/{did}", response_class=HTMLResponse)
def despachador_ficha(request: Request, did: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/despachadores", status_code=303)   # lo que se le debe y se le pagó: solo Cristina
    d = con.execute("SELECT * FROM despachadores WHERE id=?", (did,)).fetchone()
    if not d: return RedirectResponse("/despachadores", status_code=303)
    hoy = datetime.date.today(); r = resumen_despachador(con, d["nombre"], hoy)
    pendientes = con.execute("""SELECT o.id, o.numero, COALESCE(o.fecha_entrega, substr(o.creado_en,1,10)) fecha, COALESCE(o.delivery, 0) pago, o.zona, o.ciudad, o.estado, c.nombre cliente
                                FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id
                                WHERE o.despachador=? AND o.estado='entregada' AND o.despachador_pagado=0 AND o.origen_excel=0 ORDER BY fecha DESC, o.id DESC""", (d["nombre"],)).fetchall()
    en_curso = con.execute("""SELECT o.id, o.numero, COALESCE(o.fecha_entrega, substr(o.creado_en,1,10)) fecha, o.delivery, o.zona, o.ciudad, o.estado, c.nombre cliente
                              FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id WHERE o.despachador=? AND o.estado IN ('pendiente','en_ruta') ORDER BY fecha""", (d["nombre"],)).fetchall()
    pagos = con.execute("SELECT * FROM pagos_despachador WHERE despachador=? ORDER BY fecha DESC, id DESC LIMIT 30", (d["nombre"],)).fetchall()
    zonas_todas = con.execute("""SELECT COALESCE(NULLIF(zona,''), 'Sin zona') z, COUNT(*) n, SUM(COALESCE(delivery, 0)) monto FROM ordenes
                                 WHERE despachador=? AND estado='entregada' AND origen_excel=0 AND despachador_pagado=0 GROUP BY 1 ORDER BY 2 DESC""", (d["nombre"],)).fetchall()
    viajes = con.execute("""SELECT v.*, (SELECT COUNT(*) FROM ordenes o WHERE o.viaje_id=v.id) n_ordenes
                            FROM viajes_agencia v WHERE v.despachador=? AND v.pagado=0 AND v.llevado_en IS NOT NULL ORDER BY v.fecha DESC, v.id DESC""", (d["nombre"],)).fetchall()
    fallidos = con.execute("""SELECT f.*, o.numero, c.nombre cliente, o.zona, o.ciudad FROM viajes_despachador f
                              LEFT JOIN ordenes o ON o.id=f.orden_id LEFT JOIN clientes c ON c.id=o.cliente_id
                              WHERE f.despachador=? AND f.pagado=0 AND f.por_aprobar=0 ORDER BY f.fecha DESC, f.id DESC""", (d["nombre"],)).fetchall()
    dil_aprobar = con.execute("SELECT * FROM viajes_despachador WHERE despachador=? AND por_aprobar=1 ORDER BY fecha, id", (d["nombre"],)).fetchall()
    ruta = ruta_despachador(con, d["nombre"], hoy.isoformat())
    # su récord: todo lo que ha entregado, pagado o no. Sin esto, al marcar pagado se perdía el rastro.
    hist = con.execute("""SELECT o.id, o.numero, COALESCE(o.fecha_entrega, substr(o.creado_en,1,10)) fecha,
                          COALESCE(o.delivery,0) pago, o.zona, o.ciudad, o.estado, o.despachador_pagado,
                          c.nombre cliente FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id
                          WHERE o.despachador=? AND o.estado!='cancelada' AND o.origen_excel=0
                          ORDER BY fecha DESC, o.id DESC""", (d["nombre"],)).fetchall()
    mes = hoy.strftime("%Y-%m")
    record = {"n": len(hist), "entregadas": sum(1 for h in hist if h["estado"] == "entregada"),
              "n_mes": sum(1 for h in hist if (h["fecha"] or "")[:7] == mes),
              "total": round(sum(h["pago"] for h in hist), 2),
              "mes": round(sum(h["pago"] for h in hist if (h["fecha"] or "")[:7] == mes), 2),
              "primera": hist[-1]["fecha"] if hist else None}
    viajes_hechos = con.execute("""SELECT COUNT(*) n, COALESCE(SUM(monto),0) m FROM viajes_agencia
                                   WHERE despachador=? AND llevado_en IS NOT NULL""", (d["nombre"],)).fetchone()
    record["viajes"] = viajes_hechos["n"]; record["viajes_monto"] = round(viajes_hechos["m"], 2)
    hist = sorted([dict(h) for h in hist] + viajes_hist(con, d["nombre"]), key=lambda h: h["fecha"] or "", reverse=True)
    # por estado, sin contar nada dos veces (Cristina, 6 oct): entregas (pedidos y retiros de pack), viajes a la agencia,
    # diligencias, lo que tiene asignado y todavía no entrega, y las veces que fue y no le recibieron
    es_viaje = lambda h: h["estado"] == "entregada" and (h.get("quien") or "").startswith("Viaje a")
    ent = [h for h in hist if h["estado"] == "entregada" and not es_viaje(h)]
    hechos = [h for h in hist if h["estado"] in ("entregada", "diligencia", "no_entregado")]   # lo que ya se le paga
    record.update(n=len(hist), entregadas=len(ent), viajes=sum(1 for h in hist if es_viaje(h)),
                  por_entregar=sum(1 for h in hist if h["estado"] in ("pendiente", "en_ruta")),
                  fallidos=sum(1 for h in hist if h["estado"] == "no_entregado"),
                  diligencias=sum(1 for h in hist if h["estado"] == "diligencia"),
                  total=round(sum(h["pago"] or 0 for h in hechos), 2),
                  pagado=round(sum(h["pago"] or 0 for h in hechos if h["despachador_pagado"]), 2),
                  por_pagar=round(sum(h["pago"] or 0 for h in hechos if not h["despachador_pagado"]), 2),
                  primera=hist[-1]["fecha"] if hist else None)
    # cada fila dice a qué grupo pertenece, para que al tocar un número de arriba la lista muestre justo esas
    for h in hist:
        h["grupo"] = "viaje" if es_viaje(h) else ("por_entregar" if h["estado"] in ("pendiente", "en_ruta") else h["estado"])
        h["cobro"] = "al_entregar" if h["grupo"] == "por_entregar" else ("pagado" if h["despachador_pagado"] else "por_pagar")
    return render(request, "despachador.html", seccion="despachadores", FORMAS_PAGO=FORMAS_PAGO,
                  CUENTAS_OP=con.execute("SELECT id, nombre FROM cuentas WHERE activa=1 AND tipo='operativa' ORDER BY orden").fetchall(), d=d, r=r, pendientes=pendientes, en_curso=en_curso, pagos=pagos, zonas=zonas_todas, viajes=viajes, fallidos=fallidos, dil_aprobar=dil_aprobar,
                  tarifas_dil=tarifas_diligencia(con), ruta=ruta, ruta_texto=texto_ruta(ruta, hoy), ruta_cobrar=sum(f["cobrar"] for f in ruta),
                  hist=hist, record=record)


@app.post("/despachadores/{did}/adelanto")
def despachador_adelanto(request: Request, did: int, monto: str = Form(""), forma: str = Form(""), fecha: str = Form(""),
                         nota: str = Form(""), con=Depends(db)):
    """Le adelantaste plata: sale de la caja ya, y se le va descontando de las entregas que haga."""
    if not solo_admin(request): return RedirectResponse(f"/despachadores/{did}", status_code=303)
    d = con.execute("SELECT * FROM despachadores WHERE id=?", (did,)).fetchone()
    usd = cifra(monto) if monto else 0
    cuenta = con.execute("SELECT id FROM cuentas WHERE nombre=? AND activa=1", (FORMA_CUENTA.get(forma, forma),)).fetchone()
    if d and usd and usd > 0 and cuenta:
        con.execute("""INSERT INTO gastos (fecha, monto_usd, monto_real, moneda, categoria, subcategoria, descripcion, proveedor,
                       cuenta_id, notas, usuario_id) VALUES (?,?,?,'USD','Despachadores','Adelanto',?,?,?,?,?)""",
                    ((fecha or "").strip() or datetime.date.today().isoformat(), round(usd, 2), round(usd, 2),
                     f"Adelanto {d['nombre']}", d["nombre"], cuenta["id"], nota.strip() or None, uid_de(request)))
        con.commit()
    return RedirectResponse(f"/despachadores/{did}", status_code=303)


@app.post("/despachadores/{did}/diligencia")
def despachador_diligencia(request: Request, did: int, que: str = Form(""), monto: str = Form(""), fecha: str = Form(""), con=Depends(db)):
    """Hizo un encargo que no va con ningún pedido. Queda en lo que se le debe y se le paga con lo de la semana."""
    if not solo_admin(request): return RedirectResponse(f"/despachadores/{did}", status_code=303)
    d = con.execute("SELECT nombre FROM despachadores WHERE id=?", (did,)).fetchone()
    usd = cifra(monto) if monto else 0
    if d and que.strip() and usd and usd > 0:
        pago_retiro_despachador(con, d["nombre"], usd, (fecha or "").strip() or datetime.date.today().isoformat(), uid_de(request),
                                motivo=que.strip()[:1].upper() + que.strip()[1:], tipo="diligencia")
        con.commit()
    return RedirectResponse(f"/despachadores/{did}", status_code=303)


@app.post("/despachadores/{did}/diligencia/{vid}/aprobar")
def despachador_diligencia_aprobar(request: Request, did: int, vid: int, monto: str = Form(""), con=Depends(db)):
    """La anotó el despachador: Cristina la revisa (puede ajustar el monto) y desde ahí cuenta para pagarle."""
    usd = cifra(monto) if monto else 0
    if solo_admin(request) and usd and usd > 0:
        con.execute("UPDATE viajes_despachador SET por_aprobar=0, monto=? WHERE id=? AND tipo='diligencia' AND pagado=0", (round(usd, 2), vid)); con.commit()
    return RedirectResponse(f"/despachadores/{did}", status_code=303)


@app.post("/mis-entregas/diligencia")
def mis_entregas_diligencia(request: Request, tid: str = Form(""), detalle: str = Form(""), fecha: str = Form(""), con=Depends(db)):
    """El despachador anota él mismo una diligencia. Solo elige de la lista: el precio lo pone Cristina, nunca él.
    Queda por aprobar: no cuenta hasta que Cristina la revise."""
    u = quien_es(request)
    nombre = (u or {}).get("despachador")
    if not u or u["rol"] == "admin": nombre = (request.cookies.get("ver_desp") or "").strip() or (DESPACHADORES[0] if DESPACHADORES else "")
    t = con.execute("SELECT nombre, precio FROM tarifas_diligencia WHERE id=?", (int(tid),)).fetchone() if tid.isdigit() else None
    if nombre and t and t["precio"] > 0:
        hoy = datetime.date.today().isoformat()
        f = (fecha or "").strip(); det = detalle.strip()
        con.execute("""INSERT INTO viajes_despachador (tipo, fecha, despachador, monto, motivo, usuario_id, por_aprobar)
                       VALUES ('diligencia',?,?,?,?,?,1)""", (f if f and f <= hoy else hoy, nombre, t["precio"], t["nombre"] + (f" · {det}" if det else ""), uid_de(request)))
        con.commit()
    return RedirectResponse("/mis-entregas", status_code=303)


@app.post("/despachadores/{did}/diligencia/{vid}/borrar")
def despachador_diligencia_borrar(request: Request, did: int, vid: int, con=Depends(db)):
    """Se anotó por error. Solo si todavía no se le ha pagado."""
    if solo_admin(request):
        con.execute("DELETE FROM viajes_despachador WHERE id=? AND tipo='diligencia' AND pagado=0", (vid,)); con.commit()
    return RedirectResponse(f"/despachadores/{did}", status_code=303)


@app.post("/despachadores/{did}/pagar")
async def despachador_pagar(request: Request, did: int, con=Depends(db)):
    """Le pagaste al despachador: las entregas marcadas quedan saldadas y se guarda el pago."""
    if not solo_admin(request): return RedirectResponse(f"/despachadores/{did}", status_code=303)
    f = await request.form(); d = con.execute("SELECT * FROM despachadores WHERE id=?", (did,)).fetchone()
    ids = [int(x) for x in f.getlist("orden_id")]
    vids = [int(x) for x in f.getlist("viaje_id")]
    fids = [int(x) for x in f.getlist("fallido_id")]
    if d and (ids or vids or fids):
        monto = 0.0
        if ids:
            q = ",".join("?" * len(ids))
            monto += con.execute(f"SELECT COALESCE(SUM(COALESCE(delivery, 0)),0) FROM ordenes WHERE id IN ({q}) AND despachador=? AND despachador_pagado=0 AND estado='entregada'", (*ids, d["nombre"])).fetchone()[0]
        if vids:
            qv = ",".join("?" * len(vids))
            monto += con.execute(f"SELECT COALESCE(SUM(monto),0) FROM viajes_agencia WHERE id IN ({qv}) AND despachador=? AND pagado=0 AND llevado_en IS NOT NULL", (*vids, d["nombre"])).fetchone()[0]
        if fids:
            qf = ",".join("?" * len(fids))
            monto += con.execute(f"SELECT COALESCE(SUM(monto),0) FROM viajes_despachador WHERE id IN ({qf}) AND despachador=? AND pagado=0 AND por_aprobar=0", (*fids, d["nombre"])).fetchone()[0]
        uid = uid_de(request); fecha = f.get("fecha") or datetime.date.today().isoformat()
        nota = (f.get("nota") or "").strip() or None
        # primero se descuenta lo que se le adelantó: eso ya salió de caja cuando se lo diste
        usado = round(min(adelanto_despachador(con, d["nombre"])[1], monto), 2)
        forma = f.get("forma") or ""
        cuenta = con.execute("SELECT id FROM cuentas WHERE nombre=? AND activa=1", (FORMA_CUENTA.get(forma, forma),)).fetchone()
        if monto - usado > 0.009 and not cuenta: return RedirectResponse(f"/despachadores/{did}?err=caja#pagar", status_code=303)   # falta decir de qué caja
        parcial = round(float(cifra(f.get("monto_pago")) or 0), 2) if (f.get("monto_pago") or "").strip() else 0
        if 0 < parcial < monto - usado - 0.009:
            # le paga solo una parte: queda como adelanto y se descuenta cuando se le pague el resto. Las entregas siguen por pagar.
            con.execute("""INSERT INTO gastos (fecha, monto_usd, monto_real, moneda, categoria, subcategoria, descripcion, proveedor, cuenta_id, notas, usuario_id)
                           VALUES (?,?,?,'USD','Despachadores','Adelanto',?,?,?,?,?)""",
                        (fecha, parcial, parcial, f"Adelanto {d['nombre']} · parte de lo que se le debe", d["nombre"], cuenta["id"], nota, uid))
            con.commit(); return RedirectResponse(f"/despachadores/{did}", status_code=303)
        cur = con.execute("INSERT INTO pagos_despachador (despachador, fecha, monto, entregas, nota, usuario_id, adelanto_usado) VALUES (?,?,?,?,?,?,?)",
                          (d["nombre"], fecha, monto, len(ids) + len(vids) + len(fids), nota, uid, usado))
        if ids: con.execute(f"UPDATE ordenes SET despachador_pagado=1, despachador_pago_id=? WHERE id IN ({','.join('?' * len(ids))}) AND despachador=? AND estado='entregada'", (cur.lastrowid, *ids, d["nombre"]))
        if vids: con.execute(f"UPDATE viajes_agencia SET pagado=1, pago_id=? WHERE id IN ({','.join('?' * len(vids))}) AND despachador=? AND llevado_en IS NOT NULL", (cur.lastrowid, *vids, d["nombre"]))
        if fids: con.execute(f"UPDATE viajes_despachador SET pagado=1, pago_id=? WHERE id IN ({','.join('?' * len(fids))}) AND despachador=? AND por_aprobar=0", (cur.lastrowid, *fids, d["nombre"]))
        if monto - usado > 0.009:   # pagarle a un despachador es un gasto: tiene que llegar a Gastos y al libro de caja
            det = []
            nr = 0
            if fids:
                nr = con.execute(f"SELECT COALESCE(SUM(tipo='retiro'),0) FROM viajes_despachador WHERE id IN ({','.join('?' * len(fids))})", fids).fetchone()[0]
            if ids or nr: det.append(f"{len(ids) + nr} entrega{'s' if len(ids) + nr != 1 else ''}")
            if vids: det.append(f"{len(vids)} viaje{'s' if len(vids) != 1 else ''} a agencia")
            if fids:
                qf = ",".join("?" * len(fids))
                nf = con.execute(f"SELECT COALESCE(SUM(tipo='fallido'),0) FROM viajes_despachador WHERE id IN ({qf})", fids).fetchone()[0]
                if nf: det.append(f"{nf} viaje{'s' if nf != 1 else ''} sin entregar")
                nd = con.execute(f"SELECT COALESCE(SUM(tipo='diligencia'),0) FROM viajes_despachador WHERE id IN ({qf})", fids).fetchone()[0]
                if nd: det.append(f"{nd} diligencia{'s' if nd != 1 else ''}")
            g = con.execute("""INSERT INTO gastos (fecha, monto_usd, monto_real, moneda, categoria, subcategoria, descripcion, proveedor,
                           cantidad, cuenta_id, notas, usuario_id) VALUES (?,?,?,'USD','Despachadores','Pago semanal',?,?,?,?,?,?)""",
                        (fecha, round(monto - usado, 2), round(monto - usado, 2),
                         " · ".join(det) + (f" · menos ${usado:.2f} de adelanto" if usado else ""), d["nombre"], None,   # sin "c/u": cada entrega vale distinto
                         cuenta["id"] if cuenta else None, nota, uid))
            con.execute("UPDATE pagos_despachador SET gasto_id=? WHERE id=?", (g.lastrowid, cur.lastrowid))
        con.commit()
    return RedirectResponse(f"/despachadores/{did}", status_code=303)


# ------------------------------------------------------------------ VIAJES A LA AGENCIA
@app.post("/viajes/crear")
async def viaje_crear(request: Request, con=Depends(db)):
    """Un despachador se lleva varios pedidos de envío nacional a la oficina de la agencia.
    Se le paga por el viaje (Tealca $10, cualquier otra $5), lleve uno o lleve seis."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    f = await request.form(); volver = f.get("volver") or "/operaciones?cola=todo&tipo=nacional"
    desp = (f.get("despachador") or "").strip()
    ids = [int(x) for x in f.getlist("orden_id")]
    if not desp or not ids: return RedirectResponse(volver, status_code=303)
    q = ",".join("?" * len(ids))
    filas = con.execute(f"SELECT id, agencia FROM ordenes WHERE id IN ({q}) AND viaje_id IS NULL", ids).fetchall()
    if not filas: return RedirectResponse(volver, status_code=303)
    agencia = (f.get("agencia") or "").strip() or next((r["agencia"] for r in filas if r["agencia"]), "")
    ofis = oficinas_agencia(con).get(agencia) or {}
    oficina = (f.get("oficina") or "").strip() if ofis else ""
    if ofis and oficina not in ofis: return RedirectResponse(volver, status_code=303)   # en Tealca hay que decir a cuál oficina
    monto = cifra(f.get("monto")) if (f.get("monto") or "").strip() else (ofis[oficina] if oficina else tarifa_agencia(con, agencia))
    fecha = f.get("fecha") or datetime.date.today().isoformat()
    uid = uid_de(request)
    cur = con.execute("""INSERT INTO viajes_agencia (fecha, despachador, agencia, oficina, monto, pedidos, nota, usuario_id)
                         VALUES (?,?,?,?,?,?,?,?)""", (fecha, desp, agencia or None, oficina or None, monto, len(filas), (f.get("nota") or "").strip() or None, uid))
    vid = cur.lastrowid
    con.execute(f"UPDATE ordenes SET viaje_id=?, agencia=COALESCE(NULLIF(agencia,''),?) WHERE id IN ({','.join('?' * len(filas))})",
                (vid, agencia or None, *[r["id"] for r in filas]))
    for r in filas: registrar(con, r["id"], uid, "despachador", f"{desp} lo va a llevar a {agencia or 'la agencia'}" + (f" {oficina}" if oficina else ""))
    con.commit()
    return RedirectResponse(volver, status_code=303)


@app.post("/viajes/{vid}/llevado")
def viaje_llevado(request: Request, vid: int, volver: str = Form(""), con=Depends(db)):
    """Ya los llevó a la agencia: recién aquí el viaje cuenta como hecho y se le debe."""
    u = quien_es(request); yo = (u or {}).get("despachador")
    v = con.execute("SELECT * FROM viajes_agencia WHERE id=?", (vid,)).fetchone()
    es_admin = rol_de(request) == "admin" or (u and u["rol"] == "admin")   # Cristina probando "ver como"
    puede = es_admin or "coordinar" in PERMISOS[rol_de(request)] or (yo and v and v["despachador"] == yo)
    if v and puede and not v["llevado_en"]:
        con.execute("UPDATE viajes_agencia SET llevado_en=datetime('now','localtime') WHERE id=?", (vid,))
        for o in con.execute("SELECT id FROM ordenes WHERE viaje_id=?", (vid,)).fetchall():
            registrar(con, o["id"], uid_de(request), "despachador", f"{v['despachador']} lo llevó a {v['agencia'] or 'la agencia'}")
        con.commit()
    return RedirectResponse(volver or ("/mis-entregas" if yo else "/operaciones?cola=todo&tipo=nacional"), status_code=303)


@app.post("/viajes/{vid}/borrar")
def viaje_borrar(request: Request, vid: int, volver: str = Form(""), con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    v = con.execute("SELECT * FROM viajes_agencia WHERE id=? AND pagado=0", (vid,)).fetchone()
    if v:
        con.execute("UPDATE ordenes SET viaje_id=NULL WHERE viaje_id=?", (vid,))
        con.execute("DELETE FROM viajes_agencia WHERE id=?", (vid,))
        con.commit()
    return RedirectResponse(volver or "/operaciones", status_code=303)


@app.post("/despachadores/guardar")
def despachadores_guardar(request: Request, id: int = Form(0), nombre: str = Form(...), telefono: str = Form(""), notas: str = Form(""), activo: str = Form("1"), con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/despachadores", status_code=303)
    nombre = capitalizar(nombre.strip())
    if id:
        viejo = con.execute("SELECT nombre FROM despachadores WHERE id=?", (id,)).fetchone()
        con.execute("UPDATE despachadores SET nombre=?, telefono=?, notas=?, activo=? WHERE id=?", (nombre, normalizar_telefono(telefono) or None, notas or None, 1 if activo == "1" else 0, id))
        if viejo and viejo["nombre"] != nombre: renombrar_despachador(con, viejo["nombre"], nombre)   # todo lo suyo guarda el nombre
    elif nombre:
        con.execute("INSERT OR IGNORE INTO despachadores (nombre, telefono, notas, activo) VALUES (?,?,?,1)", (nombre, normalizar_telefono(telefono) or None, notas or None))
    con.commit(); cargar_despachadores()
    return RedirectResponse(f"/despachadores/{id}" if id else "/despachadores", status_code=303)


# ------------------------------------------------------------------ TARIFAS DE DELIVERY
@app.get("/tarifas", response_class=HTMLResponse)
def tarifas(request: Request, q: str = "", con=Depends(db)):
    rows = con.execute("SELECT * FROM tarifas WHERE zona LIKE ? ORDER BY orden, tarifa, zona", (f"%{q}%",)).fetchall()
    ta = cfg_json(con, "tarifa_agencia", {}) or {}
    return render(request, "tarifas.html", seccion="tarifas", tarifas=rows, tarifas_dil=tarifas_diligencia(con), tarifas_ag={a: float(ta.get(a, ta.get("*", 5))) for a in AGENCIAS}, oficinas_ag=oficinas_agencia(con))


@app.post("/tarifas/guardar")
def tarifas_guardar(request: Request, id: int = Form(0), zona: str = Form(...), tarifa: str = Form("0"), pago: str = Form(""), notas: str = Form(""), borrar: str = Form(""),
                    fuera: str = Form(""), con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/tarifas", status_code=303)
    monto = float((tarifa or "0").replace(",", ".") or 0); pago_d = float(pago.replace(",", ".")) if pago.strip() else None
    if borrar and id: con.execute("DELETE FROM tarifas WHERE id=?", (id,))
    elif id: con.execute("UPDATE tarifas SET zona=?, tarifa=?, pago_despachador=?, notas=?, fuera_caracas=? WHERE id=?", (zona.strip(), monto, pago_d, notas.strip() or None, 1 if fuera == "1" else 0, id))
    elif zona.strip(): con.execute("INSERT OR IGNORE INTO tarifas (zona, tarifa, pago_despachador, notas, fuera_caracas) VALUES (?,?,?,?,?)", (zona.strip(), monto, pago_d, notas.strip() or None, 1 if fuera == "1" else 0))
    con.commit(); return RedirectResponse("/tarifas", status_code=303)


def tarifas_diligencia(con):
    return con.execute("SELECT * FROM tarifas_diligencia ORDER BY orden, nombre").fetchall()


@app.post("/tarifas/diligencias")
async def tarifas_diligencias(request: Request, con=Depends(db)):
    """Precio de referencia de cada diligencia. Solo Cristina lo cambia."""
    if not solo_admin(request): return RedirectResponse("/tarifas", status_code=303)
    f = await request.form(); nombre = (f.get("nombre") or "").strip(); tid = f.get("id")
    if tid and f.get("borrar"): con.execute("DELETE FROM tarifas_diligencia WHERE id=?", (int(tid),))
    elif nombre:
        precio = cifra(f.get("precio") or "0") or 0
        if tid: con.execute("UPDATE tarifas_diligencia SET nombre=?, precio=? WHERE id=?", (nombre, precio, int(tid)))
        else: con.execute("INSERT OR IGNORE INTO tarifas_diligencia (nombre, precio) VALUES (?,?)", (nombre, precio))
    con.commit(); return RedirectResponse("/tarifas", status_code=303)


@app.post("/tarifas/agencias")
async def tarifas_agencias(request: Request, con=Depends(db)):
    """Lo que le pagas al despachador por llevar los pedidos a cada oficina. Es por viaje, no por pedido."""
    if not solo_admin(request): return RedirectResponse("/tarifas", status_code=303)
    f = await request.form()
    t = {}
    for a in AGENCIAS:
        v = (f.get(f"ag_{a}") or "").strip()
        if v: t[a] = cifra(v)
    t["*"] = cifra(f.get("ag_otra") or "5")
    ofis = {a: dict(o) for a, o in oficinas_agencia(con).items()}
    for a, o in ofis.items():
        for nom in o:
            v = (f.get(f"of_{a}_{nom}") or "").strip()
            if v: o[nom] = cifra(v)
    con.execute("INSERT INTO config (clave,valor) VALUES ('oficinas_agencia',?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (json.dumps(ofis, ensure_ascii=False),))
    con.execute("INSERT INTO config (clave,valor) VALUES ('tarifa_agencia',?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (json.dumps(t, ensure_ascii=False),))
    con.commit(); return RedirectResponse("/tarifas", status_code=303)


# ------------------------------------------------------------------ PROVEEDORES
@app.get("/proveedores", response_class=HTMLResponse)
def proveedores(request: Request, q: str = "", con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    rows = [dict(r) for r in con.execute("SELECT * FROM proveedores WHERE activo=1 AND (nombre LIKE ? OR que_vende LIKE ? OR contacto LIKE ?) ORDER BY nombre", (f"%{q}%", f"%{q}%", f"%{q}%"))]
    for r in rows: r["items"] = [dict(i) for i in con.execute("SELECT * FROM proveedor_items WHERE proveedor_id=? ORDER BY orden, id", (r["id"],))]
    # tarjetas repartidas en 3 columnas equilibradas por alto aproximado (las más llenas primero)
    peso = lambda r: 6 + len(r["items"]) * 2.2 + (len(r["notas"] or "") // 55) * 1.2 + bool(r["telefono"]) + bool(r["contacto"]) + bool(r["ciudad"]) + len((r["que_vende"] or "").split(",")) * .4
    rows.sort(key=lambda r: -peso(r))
    columnas = [[], [], []]; altos = [0, 0, 0]
    for r in rows:
        k = altos.index(min(altos)); columnas[k].append(r); altos[k] += peso(r)
    cats = [c for c in json.loads(con.execute("SELECT valor FROM config WHERE clave='categorias_gasto'").fetchone()[0]).get("Compras y proveedores", [])]
    return render(request, "proveedores.html", seccion="proveedores", proveedores=rows, columnas=columnas, q=q, cats=cats)


@app.post("/proveedores/guardar")
async def proveedores_guardar(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/proveedores", status_code=303)
    f = await request.form(); g = lambda k: (f.get(k) or "").strip() or None
    pid = int(f.get("id") or 0)
    if f.get("borrar") and pid: con.execute("UPDATE proveedores SET activo=0 WHERE id=?", (pid,))
    elif pid:
        con.execute("UPDATE proveedores SET nombre=?, que_vende=?, contacto=?, telefono=?, correo=?, ciudad=?, direccion=?, forma_pago=?, precios=?, notas=? WHERE id=?",
                    (g("nombre"), g("que_vende"), g("contacto"), normalizar_telefono(g("telefono")) or None, g("correo"), g("ciudad"), g("direccion"), g("forma_pago"), g("precios"), g("notas"), pid))
    elif g("nombre"):
        pid = con.execute("INSERT INTO proveedores (nombre, que_vende, contacto, telefono, correo, ciudad, direccion, forma_pago, precios, notas) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (g("nombre"), g("que_vende"), g("contacto"), normalizar_telefono(g("telefono")) or None, g("correo"), g("ciudad"), g("direccion"), g("forma_pago"), g("precios"), g("notas"))).lastrowid
    if pid and not f.get("borrar"):   # lista de lo que vende con precios (se reemplaza completa)
        con.execute("DELETE FROM proveedor_items WHERE proveedor_id=?", (pid,))
        for i, (it, pr, un) in enumerate(zip(f.getlist("item"), f.getlist("item_precio"), f.getlist("item_unidad"))):
            if it.strip(): con.execute("INSERT INTO proveedor_items (proveedor_id, item, precio, unidad, orden) VALUES (?,?,?,?,?)", (pid, it.strip(), float(pr.replace(",", ".")) if pr.strip() else None, un.strip() or None, i))
    con.commit(); return RedirectResponse("/proveedores", status_code=303)


# ------------------------------------------------------------------ TALLER (Isaías y Manawa)
def lo_que_lleva(con, oid, cuenta):
    """Qué va dentro de una entrega y cuántas piezas son, en palabras del taller.
    De un pack no se lleva "el pack", sino los repuestos que le tocan hoy."""
    partes = []; piezas = 0
    for l in con.execute("""SELECT COALESCE(NULLIF(l.nombre,''), p.nombre) nombre, l.cantidad, l.color, p.sku
                            FROM orden_lineas l JOIN productos p ON p.id=l.producto_id WHERE l.orden_id=?
                              AND COALESCE(p.tipo,'producto')!='opcion' AND l.extra_en IS NULL
                              AND NOT EXISTS (SELECT 1 FROM repuestos_prepagados r WHERE r.linea_id=l.id AND r.entregado_en IS NULL)""", (oid,)):
        # lo que quedó pagado para después no sale hoy: se entrega cuando el cliente lo active
        if (l["sku"] or "").startswith("PACK"):
            k = con.execute("SELECT tamano, unidades, entregadas_inicio FROM packs WHERE orden_id=? AND producto_id=(SELECT id FROM productos WHERE sku=?)",
                            (oid, l["sku"])).fetchone()
            n = (k["entregadas_inicio"] if k else 1) or 1
            partes.append(f"{n}× Repuesto {(k['tamano'] if k else '') or ''}".strip() + (f" · {cuenta(0, n, k['unidades'])}" if k else ""))
        else:
            n = int(l["cantidad"])
            partes.append(f"{n}× {l['nombre']}" + (f" {l['color']}" if l["color"] else ""))
        piezas += n
    return " · ".join(partes), piezas


@app.get("/taller", response_class=HTMLResponse)
def taller_hoy(request: Request, con=Depends(db)):
    """La pantalla del taller: qué hay que entregar en pick-up y qué debe llegar hoy.
    A propósito muestra lo mínimo del cliente: el nombre de pila y qué se lleva. Ni teléfono, ni correo, ni montos."""
    if not solo_taller(request): return RedirectResponse("/operaciones", status_code=303)
    hoy = datetime.date.today().isoformat()
    def cuenta(ya, n, total):
        """Cuál de todos se está llevando: 2/3, o 2-3/8 si se lleva varios de una vez."""
        return f"{ya + 1}/{total}" if n <= 1 else f"{ya + 1}-{ya + n}/{total}"

    pickups = []
    for o in con.execute("""SELECT o.id, COALESCE(NULLIF(c.nombre_pila,''), c.nombre) quien, NULLIF(TRIM(o.receptor_nombre),'') recibe,
                            COALESCE(o.fecha_prometida, substr(o.creado_en,1,10)) fecha,
                            (CASE WHEN o.estado_pago IN ('sin_pago','abonada','contra_entrega','rechazado') THEN 1 ELSE 0 END) falta_cobrar
                            FROM ordenes o JOIN clientes c ON c.id=o.cliente_id
                            WHERE o.tipo_entrega='pickup' AND o.estado IN ('pendiente','en_ruta') AND o.origen_excel=0 AND """ + HAY_QUE_ENTREGAR()):
        pickups.append(dict(o) | {"tipo": "orden", "que_lleva": lo_que_lleva(con, o["id"], cuenta)[0], "accion": f"/taller/{o['id']}/entregado"})
    # retiros de pack que el cliente viene a buscar
    for k in cargar_packs(con):
        if k["saldo"] > 0 and k["fecha_programada"] and (k["tipo_programado"] or k["tipo_entrega"]) == "pickup":
            n = k["retiro_programado"] or 1
            pickups.append({"quien": (k["cliente"] or "").split()[0], "fecha": k["fecha_programada"], "falta_cobrar": 0, "tipo": "pack",
                            "que_lleva": f"{n}× Repuesto {k['tamano'] or ''}".strip()
                                          + " · " + cuenta(k["unidades"] - k["saldo"], n, k["unidades"]),
                            "accion": f"/taller/pack/{k['id']}/entregado"})
    # repuestos que ya pagó y viene a buscar
    for r in cargar_prepagados(con):
        if r["fecha_programada"] and r["tipo_entrega"] == "pickup":
            pickups.append({"quien": (r["cliente"] or "").split()[0], "fecha": r["fecha_programada"], "falta_cobrar": 0, "tipo": "prepagado",
                            "que_lleva": f"1× Repuesto {r['tamano'] or ''} · ya pagado".strip(),
                            "accion": f"/taller/prepagado/{r['id']}/entregado"})
    pickups.sort(key=lambda x: (x["fecha"], x["quien"]))

    # Lo que se lleva cada despachador hoy: para que en el taller sepan qué entregarle cuando pasa por la casa.
    # Sin nombres de clientes y sin fechas: es una lista de bultos, todo lo de hoy.
    salidas = {}
    def sale(quien_lleva, producto, n, agencia=False, falta=False):
        g = salidas.setdefault(quien_lleva, {"quien_lleva": quien_lleva, "cosas": {}, "agencia": {}, "falta": falta})
        d = g["agencia"] if agencia else g["cosas"]   # lo de la agencia va aparte: eso hay que embalarlo
        d[producto] = d.get(producto, 0) + n

    for o in con.execute("""SELECT o.id, COALESCE(NULLIF(TRIM(o.despachador),''),   -- un envío nacional lo lleva quien tiene el viaje a la agencia
                                   (SELECT v.despachador FROM viajes_agencia v WHERE v.id=o.viaje_id AND v.llevado_en IS NULL)) despachador,
                            o.tipo_entrega, o.agencia FROM ordenes o
                            WHERE o.tipo_entrega IN ('delivery','delivery_fuera','nacional')
                              AND o.estado='pendiente' AND o.origen_excel=0   -- en ruta = ya salió del taller
                              AND """ + HAY_QUE_ENTREGAR() + """
                              -- con viaje a la agencia asignado, manda el día del viaje (el pedido puede decir hoy y el viaje ser mañana)
                              AND COALESCE((SELECT v.fecha FROM viajes_agencia v WHERE v.id=o.viaje_id AND v.llevado_en IS NULL),
                                           o.fecha_prometida, substr(o.creado_en,1,10)) <= ?""", (hoy,)):
        # quien lleva es quien lleva, aunque una parte vaya a la agencia: un solo Juan, no dos
        a_agencia = o["tipo_entrega"] == "nacional"
        # un envío nacional sin despachador no es "sin despachador": va a una agencia (MRW, Tealca…) y falta quién lo lleve hasta allá
        falta = not (o["despachador"] or "").strip()
        lleva = (o["despachador"] or "").strip() or ((f"Para {o['agencia']}" if o["agencia"] else "Para la agencia") if a_agencia else "Sin despachador")
        for l in con.execute("""SELECT COALESCE(NULLIF(l.nombre,''), p.nombre) nombre, l.cantidad, l.color, p.sku, p.tipo,
                                TRIM(COALESCE(l.personalizacion,'')) perso
                                FROM orden_lineas l JOIN productos p ON p.id=l.producto_id WHERE l.orden_id=? AND l.extra_en IS NULL
                                  AND NOT EXISTS (SELECT 1 FROM repuestos_prepagados r WHERE r.linea_id=l.id AND r.entregado_en IS NULL)""", (o["id"],)):
            if l["tipo"] == "opcion":   # una opción no es un bulto; si es la placa con nombre, que se vea el nombre
                if l["perso"]: sale(lleva, f"Placa con el nombre “{l['perso']}”", int(l["cantidad"] or 1), a_agencia, falta)
                continue
            if (l["sku"] or "").startswith("PACK"):   # de un pack no sale "el pack": salen los repuestos que le tocan
                k = con.execute("SELECT tamano, entregadas_inicio FROM packs WHERE orden_id=? AND producto_id=(SELECT id FROM productos WHERE sku=?)",
                                (o["id"], l["sku"])).fetchone()
                sale(lleva, f"Repuesto {(k['tamano'] if k else '') or ''}".strip(), (k["entregadas_inicio"] if k else 1) or 1, a_agencia, falta)
            else:
                sale(lleva, l["nombre"] + (f" {l['color']}" if l["color"] else "")
                     + (f" ✎ personalizado “{l['perso']}”" if l["perso"] else ""), int(l["cantidad"]), a_agencia, falta)
    for k in cargar_packs(con):
        if k["saldo"] > 0 and k["fecha_programada"] and k["fecha_programada"] <= hoy and (k["tipo_programado"] or k["tipo_entrega"]) != "pickup" and not k.get("en_ruta"):
            sale((k["despachador_programado"] or "").strip() or "Sin despachador",
                 f"Repuesto {k['tamano'] or ''}".strip(), min(k["retiro_programado"] or 1, k["saldo"]))
    for r in cargar_prepagados(con):
        if r["fecha_programada"] and r["fecha_programada"] <= hoy and r["tipo_entrega"] != "pickup" and not r.get("en_ruta"):
            sale((r["despachador"] or "").strip() or "Sin despachador", f"Repuesto {r['tamano'] or ''}".strip(), 1)

    for g in salidas.values():
        for k_ in ("cosas", "agencia"):
            g[k_] = " · ".join(f"{n}× {nom}" for nom, n in sorted(g[k_].items()))
    salidas = sorted(salidas.values(), key=lambda g: (g["falta"] or g["quien_lleva"] == "Sin despachador", g["quien_lleva"]))
    llegadas = con.execute("""SELECT pr.id, pr.cantidad, pr.recibido, pr.fecha_esperada, pr.responsable, pr.pieza, pr.descripcion,
                              COALESCE(NULLIF(pr.pieza,''), p.nombre) producto, p.requiere_color FROM produccion pr LEFT JOIN productos p ON p.id=pr.producto_id
                              WHERE pr.estado NOT IN ('recibido','cancelado','cancelada')
                                AND (pr.fecha_esperada IS NULL OR pr.fecha_esperada <= ?)
                              ORDER BY COALESCE(pr.fecha_esperada,'9999'), pr.id""", (hoy,)).fetchall()
    armables = []
    stock = lambda pid, col=None: con.execute("SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario WHERE producto_id=?" + (" AND color=?" if col else ""),
                                              (pid, col) if col else (pid,)).fetchone()[0]
    for p_ in con.execute("""SELECT p.id, p.nombre, p.requiere_color FROM productos p
                             WHERE p.activo=1 AND EXISTS (SELECT 1 FROM receta r WHERE r.producto_id=p.id) ORDER BY p.orden"""):
        receta_ = [dict(r) for r in con.execute("""SELECT i.id, i.nombre, r.cantidad necesita FROM receta r JOIN productos i ON i.id=r.insumo_id
                                                    WHERE r.producto_id=?""", (p_["id"],))]
        # un Slow Chow se arma por color: además de la base, lleva un plato de ese color
        for col in (tuple(PLATO_DE_COLOR) if p_["requiere_color"] else (None,)):
            lleva = receta_ + ([dict(con.execute("SELECT id, nombre, 1 necesita FROM productos WHERE sku=?", (PLATO_DE_COLOR[col],)).fetchone())] if col else [])
            # con lo que hay en el depósito, ¿cuántos se pueden armar? y sobre todo: ¿qué material es el que frena?
            materiales = [{"nombre": m["nombre"], "hay": int(stock(m["id"])), "da_para": int(stock(m["id"]) // m["necesita"]) if m["necesita"] else 0} for m in lleva]
            tope = min(materiales, key=lambda m: m["da_para"]) if materiales else None
            armables.append({"id": p_["id"], "nombre": p_["nombre"], "color": col, "listos": stock(p_["id"], col),
                             "alcanza": tope["da_para"] if tope else 0, "materiales": materiales, "tope": tope})
    notas = con.execute("SELECT * FROM notas_taller ORDER BY id DESC LIMIT 8").fetchall()
    armados_hoy = con.execute("""SELECT m.lote, m.cantidad, m.color, p.nombre, u.nombre quien, substr(m.creado_en,12,5) hora
                                 FROM mov_inventario m JOIN productos p ON p.id=m.producto_id LEFT JOIN usuarios u ON u.id=m.usuario_id
                                 WHERE m.lote IS NOT NULL AND m.tipo='entrada' AND m.fecha=? ORDER BY m.id DESC""", (hoy,)).fetchall()
    return render(request, "taller.html", seccion="taller", pickups=pickups, salidas=salidas, llegadas=llegadas, armables=armables, armados_hoy=armados_hoy, notas=notas, hoy_iso=hoy, fecha_larga=fecha_larga())


@app.post("/taller/{oid}/entregado")
def taller_entregado(request: Request, oid: int, con=Depends(db)):
    """El taller entrega un pick-up: se marca solo y Cristina lo ve sin tener que preguntar."""
    if not solo_taller(request): return RedirectResponse("/operaciones", status_code=303)
    o = con.execute("SELECT tipo_entrega, estado FROM ordenes WHERE id=?", (oid,)).fetchone()
    if o and o["tipo_entrega"] == "pickup" and o["estado"] in ("pendiente", "en_ruta"):
        ahora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M"); uid = uid_de(request)   # día y hora en que se lo llevó
        con.execute("UPDATE ordenes SET estado='entregada', fecha_entrega=?, actualizado_en=datetime('now','localtime') WHERE id=?", (ahora, oid))
        registrar(con, oid, uid, "estado", "Entregado en pick-up (taller)")
        con.commit()
    return RedirectResponse("/taller", status_code=303)


@app.post("/taller/pack/{pid}/entregado")
def taller_pack_entregado(request: Request, pid: int, con=Depends(db)):
    """El cliente vino a buscar los repuestos de su pack y el taller se los entregó."""
    if not solo_taller(request): return RedirectResponse("/operaciones", status_code=303)
    k = next((k for k in cargar_packs(con) if k["id"] == pid), None)
    if k and k["saldo"] > 0:
        hoy = datetime.date.today().isoformat(); uid = uid_de(request)
        for _ in range(min(k["retiro_programado"] or 1, k["saldo"])):
            con.execute("""INSERT INTO entregas_repuesto (pack_id, fecha, tipo_entrega, delivery_cobrado, notas, usuario_id)
                           VALUES (?,?, 'pickup', 0, 'retirado en el taller', ?)""", (pid, hoy, uid))
        con.execute("""UPDATE packs SET fecha_programada=NULL, tipo_programado=NULL, despachador_programado=NULL, nota_programada=NULL,
                       retiro_programado=NULL, delivery_programado=NULL, delivery_pagado=NULL,
                       estado=CASE WHEN entregadas_inicio + (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=packs.id) >= unidades THEN 'completo' ELSE estado END
                       WHERE id=?""", (pid,))
        con.commit()
    return RedirectResponse("/taller", status_code=303)


@app.post("/taller/prepagado/{rid}/entregado")
def taller_prepagado_entregado(request: Request, rid: int, con=Depends(db)):
    """Un repuesto que ya estaba pagado y el cliente vino a buscar."""
    if not solo_taller(request): return RedirectResponse("/operaciones", status_code=303)
    con.execute("""UPDATE repuestos_prepagados SET entregado_en=?, tipo_entrega='pickup', usuario_id=?
                   WHERE id=? AND entregado_en IS NULL""", (datetime.date.today().isoformat(), uid_de(request), rid))
    con.commit()
    return RedirectResponse("/taller", status_code=303)


@app.post("/taller/nota")
def taller_nota(request: Request, texto: str = Form(""), con=Depends(db)):
    """Un aviso del taller para Cristina: algo llegó mal, se acabó un material, pasó algo."""
    if not solo_taller(request): return RedirectResponse("/operaciones", status_code=303)
    if texto.strip():
        con.execute("INSERT INTO notas_taller (fecha, texto, usuario_id) VALUES (?,?,?)",
                    (datetime.date.today().isoformat(), texto.strip()[:400], uid_de(request)))
        con.commit()
    return RedirectResponse("/taller", status_code=303)


@app.get("/taller/avisos", response_class=HTMLResponse)
def taller_avisos(request: Request, ver: str = "sin_resolver", mes: str = "", anio: str = "", con=Depends(db)):
    """Lo que el taller te avisó. Se queda aquí hasta que lo resuelvas, no se borra al verlo.
    Arranca mostrando solo lo sin resolver; lo resuelto se mira aparte, por mes."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    todas = con.execute("""SELECT n.*, u.nombre quien, pr.pieza, pr.responsable, pr.cantidad, pr.recibido,
                           COALESCE(pr.tipo_pedido,'produccion') tipo
                           FROM notas_taller n LEFT JOIN usuarios u ON u.id=n.usuario_id
                           LEFT JOIN produccion pr ON pr.id=n.produccion_id
                           ORDER BY n.fecha DESC, n.id DESC""").fetchall()
    n = {"sin_resolver": sum(1 for f in todas if not f["resuelto"]), "resueltos": sum(1 for f in todas if f["resuelto"])}
    if ver not in ("sin_resolver", "resueltos", "todos"): ver = "sin_resolver"
    filas = [f for f in todas if ver == "todos" or (ver == "resueltos") == bool(f["resuelto"])]
    hoy_d = datetime.date.today()
    anios = sorted({int((f["fecha"] or "0000")[:4]) for f in todas if f["fecha"]} | {hoy_d.year}, reverse=True)
    anio = int(mes[:4]) if mes else (int(anio) if anio.isdigit() else hoy_d.year)
    if mes: filas = [f for f in filas if (f["fecha"] or "")[:7] == mes]
    grupos = []   # por mes, lo más nuevo arriba
    for f in filas:
        k = (f["fecha"] or "")[:7]
        if not grupos or grupos[-1]["k"] != k:
            y, m = (int(k[:4]), int(k[5:7])) if len(k) == 7 else (hoy_d.year, hoy_d.month)
            grupos.append({"k": k, "titulo": MESES_N[m - 1].capitalize() + (f" {y}" if y != hoy_d.year else ""), "avisos": []})
        grupos[-1]["avisos"].append(f)
    return render(request, "avisos.html", seccion="avisos", grupos=grupos, ver=ver, n=n, mes=mes, anio=anio, anios=anios,
                  MESES_N=MESES_N, pendientes=n["sin_resolver"])


@app.post("/taller/nota/{nid}/resolver")
def taller_nota_resolver(request: Request, nid: int, volver: str = Form("/taller/avisos"), con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    con.execute("UPDATE notas_taller SET resuelto=1, visto=1, resuelto_en=? WHERE id=?",
                (datetime.date.today().isoformat(), nid)); con.commit()
    return RedirectResponse(volver, status_code=303)


@app.post("/taller/nota/{nid}/reabrir")
def taller_nota_reabrir(request: Request, nid: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    con.execute("UPDATE notas_taller SET resuelto=0, visto=0, resuelto_en=NULL WHERE id=?", (nid,)); con.commit()
    return RedirectResponse("/taller/avisos", status_code=303)


@app.post("/taller/nota/{nid}/visto")
def taller_nota_visto(request: Request, nid: int, volver: str = Form("/inicio"), con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    con.execute("UPDATE notas_taller SET visto=1 WHERE id=?", (nid,)); con.commit()
    return RedirectResponse(volver, status_code=303)


@app.post("/taller/armar")
def taller_armar(request: Request, producto_id: int = Form(...), cantidad: str = Form("0"), color: str = Form(""), con=Depends(db)):
    """Armaron porches por adelantado: sale la materia prima y entran porches listos.
    Así la caja de madera no se cuenta dos veces (una como caja y otra al vender)."""
    if not solo_taller(request): return RedirectResponse("/operaciones", status_code=303)
    n = int(cifra(cantidad)) if cantidad.strip() else 0
    p = con.execute("SELECT id, nombre, requiere_color FROM productos WHERE id=? AND activo=1", (producto_id,)).fetchone()
    color = (color or "").strip().lower() or None
    if p and p["requiere_color"] and color not in PLATO_DE_COLOR: p = None   # un Slow Chow sin color no se puede armar
    if p and n > 0:
        hoy = datetime.date.today().isoformat(); uid = uid_de(request)
        que = f"{n}× {p['nombre']}" + (f" plato {color}" if color else "")
        gasta = [(r["insumo_id"], r["cantidad"]) for r in con.execute("SELECT insumo_id, cantidad FROM receta WHERE producto_id=?", (producto_id,))]
        if color:
            plato = con.execute("SELECT id FROM productos WHERE sku=?", (PLATO_DE_COLOR[color],)).fetchone()
            if plato: gasta.append((plato["id"], 1))
        lote = secrets.token_hex(6)
        for insumo, c in gasta:
            con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, nota, usuario_id, lote) VALUES (?,?,?,?,?,?,?)",
                        (insumo, hoy, "salida", -int(c * n), f"para armar {que}", uid, lote))
        con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, color, nota, usuario_id, lote) VALUES (?,?,?,?,?,?,?,?)",
                    (producto_id, hoy, "entrada", n, color, "armado en el taller", uid, lote))
        con.commit()
    return RedirectResponse("/taller", status_code=303)


@app.post("/taller/armar/deshacer")
def taller_armar_deshacer(request: Request, lote: str = Form(""), con=Depends(db)):
    """Se equivocaron al anotar lo que armaron: se borra ese armado completo (vuelve el material). Solo lo de hoy."""
    if not solo_taller(request): return RedirectResponse("/operaciones", status_code=303)
    if lote: con.execute("DELETE FROM mov_inventario WHERE lote=? AND fecha=?", (lote, datetime.date.today().isoformat())); con.commit()
    return RedirectResponse("/taller", status_code=303)


@app.post("/taller/llegada/{pid}")
def taller_llegada(request: Request, pid: int, cantidad: str = Form("0"), azul: str = Form(""), rosado: str = Form(""), nota: str = Form(""), con=Depends(db)):
    """Llegó un pedido: el taller confirma cuántos llegaron de verdad (pueden ser menos de los esperados).
    Un Slow Chow se confirma por color: cuántos llevan plato azul y cuántos rosado."""
    if not solo_taller(request): return RedirectResponse("/operaciones", status_code=303)
    r = con.execute("SELECT * FROM produccion WHERE id=?", (pid,)).fetchone()
    col = colores_de(con, r, azul, rosado) if r else None
    n = sum(col.values()) if col else (int(cifra(cantidad)) if cantidad.strip() else 0)
    if r and n > 0 and not llegada_repetida(con, pid, uid_de(request)):
        hoy = datetime.date.today().isoformat(); uid = uid_de(request)
        entrar_al_inventario(con, r, n, f"producción #{pid}" + (f" · {r['responsable']}" if r["responsable"] else "") + " · confirmado en taller" + (f" · {nota.strip()}" if nota.strip() else ""), uid, col)
        total = (r["recibido"] or 0) + n
        con.execute("UPDATE produccion SET recibido=?, estado=?, recibido_en=? WHERE id=?",
                    (total, "recibido" if total >= r["cantidad"] else "en_proceso", hoy if total >= r["cantidad"] else None, pid))
        if nota.strip():   # si algo vino mal, que Cristina se entere sin que se lo cuenten
            con.execute("INSERT INTO notas_taller (fecha, texto, usuario_id, produccion_id) VALUES (?,?,?,?)",
                        (hoy, f"Llegada de {r['responsable'] or 'un pedido'}: {nota.strip()}", uid, pid))
        con.commit()
    return RedirectResponse("/taller", status_code=303)


# ------------------------------------------------------------------ REFERIDOS
@app.get("/referidos", response_class=HTMLResponse)
def referidos(request: Request, con=Depends(db)):
    """Quién trae gente y qué han comprado los que trajo."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    refs = con.execute("""SELECT r.id, r.nombre quien, COUNT(*) n,
                          COALESCE(SUM((SELECT SUM(total) FROM ordenes x WHERE x.cliente_id=c.id AND x.estado!='cancelada')),0) trajo
                          FROM clientes c JOIN clientes r ON r.id=c.referido_id
                          GROUP BY r.id ORDER BY n DESC, trajo DESC""").fetchall()
    llegaron = con.execute("""SELECT c.id, c.nombre, substr(c.creado_en,1,10) desde, r.id ref_id, r.nombre lo_trajo,
                              COALESCE((SELECT SUM(total) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada'),0) gastado,
                              (SELECT o.canal FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada' ORDER BY o.id LIMIT 1) canal,
                              EXISTS (SELECT 1 FROM ordenes o JOIN orden_lineas l ON l.orden_id=o.id JOIN productos p ON p.id=l.producto_id
                                      WHERE o.cliente_id=c.id AND o.estado!='cancelada' AND p.categoria='porche') compro_porche
                              FROM clientes c JOIN clientes r ON r.id=c.referido_id ORDER BY c.id DESC LIMIT 40""").fetchall()
    # el premio solo aplica si escribió por WhatsApp y compró un Porche
    llegaron = [dict(l) | {"aplica": bool(l["compro_porche"]) and (l["canal"] or "") == "whatsapp",
                           "por_que": (("llegó por " + CANAL.get(l["canal"], l["canal"] or "otro canal")) if (l["canal"] or "") != "whatsapp"
                                       else ("no compró Porche" if not l["compro_porche"] else ""))} for l in llegaron]
    total_cl = con.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
    n_ref = len(llegaron)
    resumen = {"refieren": len(refs), "llegaron": n_ref, "premiables": sum(1 for l in llegaron if l["aplica"]), "total_cl": total_cl,
               "pct": round(n_ref / total_cl * 100) if total_cl else 0,
               "comprado": sum(l["gastado"] for l in llegaron)}
    return render(request, "referidos.html", seccion="referidos", refs=refs, llegaron=llegaron, r=resumen)


# ------------------------------------------------------------------ ANALÍTICA
@app.get("/analitica", response_class=HTMLResponse)
def analitica(request: Request, mes: str = "", con=Depends(db)):
    """El cierre de un mes: cuánto se vendió, de dónde vino, qué se vendió y cuánta gente pide repuestos y packs.
    Cada número se compara con el mes anterior. La plata fina (costos, gastos, resultado) vive en Finanzas."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    hoy = datetime.date.today()
    try: m0 = datetime.date.fromisoformat((mes or hoy.strftime("%Y-%m")) + "-01")
    except ValueError: m0 = hoy.replace(day=1)
    ant = (m0 - datetime.timedelta(days=1)).replace(day=1)
    sig = (m0 + datetime.timedelta(days=32)).replace(day=1)
    a, b = resumen_mes(con, m0), resumen_mes(con, ant)
    def var(x, y):   # cuánto cambió frente al mes anterior
        if not y: return None
        return round((x - y) / y * 100)
    comp = {k: var(a[k], b[k]) for k in ("ventas", "pedidos", "ticket", "clientes", "nuevos", "repiten", "rep_clientes", "rep_unidades", "pack_clientes", "packs")}
    return render(request, "analitica.html", seccion="analitica", am=a, bm=b, comp=comp,
                  mes_txt=f"{MESES_N[m0.month - 1].capitalize()} {m0.year}", mes_ant=ant.strftime("%Y-%m"),
                  mes_sig=sig.strftime("%Y-%m") if sig <= hoy.replace(day=1) else None, CANAL=CANAL)


def resumen_mes(con, m0):
    """Los números de un mes (m0 = día 1). Ventas por el día en que entraron, como en Inicio."""
    fin = ((m0 + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)).isoformat()
    ini = m0.isoformat(); rango = (ini, fin)
    ventas = round(sum(m for m, n in ventas_por_dia(con, ini, fin).values()), 2)
    ords = con.execute("""SELECT o.id, o.cliente_id, o.canal, o.total, COALESCE(NULLIF(TRIM(o.ciudad),''), c.ciudad, 'Sin ciudad') ciudad,
                          (SELECT MIN(substr(x.creado_en,1,10)) FROM ordenes x WHERE x.cliente_id=o.cliente_id AND x.estado!='cancelada') primera,
                          c.origen
                          FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id
                          WHERE o.estado!='cancelada' AND COALESCE(o.origen_excel,0)=0 AND substr(o.creado_en,1,10) BETWEEN ? AND ?""", rango).fetchall()
    pedidos = len(ords)
    clientes = {o["cliente_id"] for o in ords}
    nuevos = {o["cliente_id"] for o in ords if (o["primera"] or "") >= ini}
    def agrupa(clave):
        d = {}
        for o in ords:
            k = o[clave] or "—"; d.setdefault(k, [0, 0.0]); d[k][0] += 1; d[k][1] += o["total"] or 0
        return sorted(({"k": k, "n": v[0], "m": round(v[1], 2), "pct": round(v[1] / ventas * 100) if ventas else 0} for k, v in d.items()), key=lambda x: -x["m"])
    origen = {}
    for o in ords:
        if o["cliente_id"] in nuevos: origen[o["cliente_id"]] = o["origen"] or "Sin anotar"
    como_llegaron = {}
    for v in origen.values(): como_llegaron[v] = como_llegaron.get(v, 0) + 1
    lineas = con.execute("""SELECT l.nombre, p.sku, p.categoria, SUM(l.cantidad) u, SUM(l.total) f, COUNT(DISTINCT o.cliente_id) cl
                            FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id LEFT JOIN productos p ON p.id=l.producto_id
                            WHERE o.estado!='cancelada' AND COALESCE(o.origen_excel,0)=0 AND l.extra_en IS NULL AND l.producto_id IS NOT NULL
                              AND COALESCE(p.tipo,'producto')!='opcion' AND substr(o.creado_en,1,10) BETWEEN ? AND ?
                            GROUP BY l.nombre ORDER BY u DESC, f DESC""", rango).fetchall()
    def suma(filtro, campo="u"): return sum(l[campo] or 0 for l in lineas if filtro(l["sku"] or ""))
    def clientes_de(filtro):
        return con.execute(f"""SELECT COUNT(DISTINCT o.cliente_id) FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id JOIN productos p ON p.id=l.producto_id
                               WHERE o.estado!='cancelada' AND COALESCE(o.origen_excel,0)=0 AND substr(o.creado_en,1,10) BETWEEN ? AND ? AND {filtro}""", rango).fetchone()[0]
    es_rep = lambda k: k.startswith("REP-"); es_pack = lambda k: k.startswith("PACK")
    entregas_rep = (con.execute("SELECT COUNT(*) FROM entregas_repuesto WHERE fecha BETWEEN ? AND ?", rango).fetchone()[0]
                    + con.execute("SELECT COUNT(*) FROM repuestos_prepagados WHERE substr(entregado_en,1,10) BETWEEN ? AND ?", rango).fetchone()[0])
    vendidos = {l["nombre"] for l in lineas}
    quietos = [r[0] for r in con.execute("""SELECT nombre FROM productos WHERE activo=1 AND tipo='producto' ORDER BY orden, nombre""") if r[0] not in vendidos]
    return {
        "ventas": ventas, "pedidos": pedidos, "ticket": round(ventas / pedidos, 2) if pedidos else 0,
        "clientes": len(clientes), "nuevos": len(nuevos), "repiten": len(clientes - nuevos),
        "canales": agrupa("canal"), "ciudades": agrupa("ciudad")[:8],
        "como_llegaron": sorted(como_llegaron.items(), key=lambda x: -x[1]),
        "top": [dict(l) for l in lineas[:8]],
        "rep_clientes": clientes_de("p.sku LIKE 'REP-%'"), "rep_unidades": int(suma(es_rep)),
        "pack_clientes": clientes_de("p.sku LIKE 'PACK%'"), "packs": int(suma(es_pack)),
        "prepagados": con.execute("SELECT COUNT(*) FROM repuestos_prepagados WHERE substr(creado_en,1,10) BETWEEN ? AND ?", rango).fetchone()[0],
        "entregas_rep": entregas_rep,
        "porches": [{"k": l["nombre"], "u": int(l["u"])} for l in lineas if (l["categoria"] or "") in ("porche", "kit")],
        "quietos": quietos[:15],
        # Miembros PRO: quienes tienen un porche PRO (por su primera compra de uno), a fin de ese mes y los que se sumaron en el mes
        "pro_total": con.execute("""SELECT COUNT(*) FROM (SELECT o.cliente_id, MIN(substr(o.creado_en,1,10)) d FROM orden_lineas l
                                    JOIN ordenes o ON o.id=l.orden_id JOIN productos p ON p.id=l.producto_id
                                    WHERE o.estado!='cancelada' AND (p.sku LIKE 'PRO-%' OR p.sku LIKE 'KIT-COMPLETO-%') GROUP BY o.cliente_id) WHERE d <= ?""", (fin,)).fetchone()[0],
        "pro_nuevos": con.execute("""SELECT COUNT(*) FROM (SELECT o.cliente_id, MIN(substr(o.creado_en,1,10)) d FROM orden_lineas l
                                     JOIN ordenes o ON o.id=l.orden_id JOIN productos p ON p.id=l.producto_id
                                     WHERE o.estado!='cancelada' AND (p.sku LIKE 'PRO-%' OR p.sku LIKE 'KIT-COMPLETO-%') GROUP BY o.cliente_id) WHERE d BETWEEN ? AND ?""", rango).fetchone()[0],
    }


@app.get("/{seccion}", response_class=HTMLResponse)
def pendiente(request: Request, seccion: str):
    if seccion not in SECCIONES: return RedirectResponse("/ordenes", status_code=303)
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)   # marketing, analítica y configuración son de Cristina
    return render(request, "pendiente.html", seccion=seccion, titulo=SECCIONES[seccion])
