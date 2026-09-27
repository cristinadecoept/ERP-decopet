"""Plataforma Decopet — pantallas. Parte 1: Órdenes."""
import datetime, json, sqlite3, re, os, subprocess, secrets, threading, time, hashlib
from pathlib import Path
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from plataforma import bcv

BASE = Path(__file__).resolve().parent
DB = BASE / "data" / "plataforma.db"
app = FastAPI(title="Decopet")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
(BASE / "data" / "fotos").mkdir(parents=True, exist_ok=True)
app.mount("/fotos", StaticFiles(directory=BASE / "data" / "fotos"), name="fotos")
tpl = Jinja2Templates(directory=BASE / "templates")

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


def cobro_extra(con, oid, concepto, monto, forma, fecha, uid, referencia=None, nota=None):
    """El cliente le agrega algo a una orden que ya existe (delivery, personalización, propina…).
    Entra dentro de la orden: sube su total y queda el pago registrado. Al Cash flow no va: eso lo lleva Cristina a mano."""
    monto = round(float(monto or 0), 2)
    if monto <= 0 or not oid: return None
    fecha = fecha or datetime.date.today().isoformat()
    con.execute("""INSERT INTO orden_lineas (orden_id, nombre, cantidad, precio, costo, total, forma_pago)
                   VALUES (?,?,1,?,0,?,?)""", (oid, concepto, monto, monto, forma or None))
    con.execute("UPDATE ordenes SET subtotal=COALESCE(subtotal,0)+?, total=COALESCE(total,0)+? WHERE id=?", (monto, monto, oid))
    if (concepto or "").lower().startswith("delivery"):
        con.execute("UPDATE ordenes SET delivery=COALESCE(delivery,0)+? WHERE id=?", (monto, oid))
    con.execute("""INSERT INTO pagos (orden_id, forma, monto_usd, monto_real, moneda, referencia, fecha, estado, confirmado_por, confirmado_en)
                   VALUES (?,?,?,?, 'USD', ?, ?, 'confirmado', ?, ?)""",
                (oid, forma or None, monto, monto, (referencia or "").strip() or None, fecha, uid, fecha))
    o = con.execute("SELECT total FROM ordenes WHERE id=?", (oid,)).fetchone()
    pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (oid,)).fetchone()[0]
    con.execute("UPDATE ordenes SET estado_pago=? WHERE id=? AND estado_pago NOT IN ('por_cobrar','reembolsada')",
                (estado_pago_de(pagado, o["total"]), oid))
    fijar_fecha_pago(con, oid)
    registrar(con, oid, uid, "pago", f"{concepto} {fmt_usd(monto)} · {forma or 'sin forma'}" + (f" · {nota}" if nota else ""))
    return monto


def fijar_fecha_pago(con, oid):
    """La fecha de pago de la orden = el día del primer pago confirmado. Solo se pone si está vacía."""
    con.execute("""UPDATE ordenes SET fecha_pago=(SELECT substr(MIN(COALESCE(p.fecha,p.confirmado_en)),1,10) FROM pagos p
                   WHERE p.orden_id=ordenes.id AND p.estado='confirmado') WHERE id=? AND (fecha_pago IS NULL OR fecha_pago='')""", (oid,))


def estado_pago_de(pagado, total):
    """pagada si cubre el total; abonada SOLO si abonó una parte; si no ha pagado nada sigue 'por pagar'."""
    if pagado >= total - 0.01: return "pagada"
    return "abonada" if pagado > 0.009 else "sin_pago"

P_LABEL = {"sin_pago": "Por pagar", "por_confirmar": "Por revisar", "rechazado": "Pago rechazado", "abonada": "Pago parcial", "pagada": "Pagada", "contra_entrega": "Contra entrega",
           "por_cobrar": "Cashea · cuotas pendientes", "reembolsada": "Reembolsada"}
P_SUB = {"sin_pago": "por pagar", "por_confirmar": "por revisar", "rechazado": "PAGO RECHAZADO", "abonada": "pago parcial", "pagada": "", "contra_entrega": "CONTRA ENTREGA", "por_cobrar": "cuotas pendientes", "reembolsada": "reembolsada"}
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
DESPACHADORES = ["Ingrid", "Cristina", "Fernando"]   # se reemplaza al arrancar con los activos de la tabla despachadores

def cargar_despachadores():
    con = sqlite3.connect(DB)
    DESPACHADORES[:] = [r[0] for r in con.execute("SELECT nombre FROM despachadores WHERE activo=1 ORDER BY nombre")]
    con.close()
AGENCIAS = ["Tealca", "MRW", "Zoom", "Domesa", "Liberty Express", "Otra"]
# Quién puede hacer qué (regla de la Parte 1)
PROVEEDORES_VISIBLES_TALLER = ("Walter",)   # al taller solo le hace falta saber quién le trae la madera


def proveedor_visible(rol, nombre):
    """Los proveedores son contactos comerciales de Cristina: el taller solo ve a Walter, con quien trata a diario."""
    if rol != "taller": return nombre
    return nombre if (nombre or "") in PROVEEDORES_VISIBLES_TALLER else None


ORIGENES = ["Instagram", "Recomendación de otro cliente", "Página web / Google", "Cashea", "Nos vio en la calle",
            "Feria o evento", "Duwu", "Vidapets", "Ya era cliente", "Otro"]   # cómo nos conoció, distinto del canal por donde pidió

CONCEPTOS_EXTRA = ["Delivery", "Personalización", "Propina", "Repuesto adicional", "Ajuste", "Otro"]   # cosas que un cliente le agrega a una orden ya hecha

PERMISOS = {
    "admin": {"crear", "pago_por_confirmar", "confirmar_pago", "rechazar_pago", "precios", "coordinar", "despachar", "entregar", "editar_entrega", "incidencia", "reprogramar", "cancelar", "ver_dinero", "contra_entrega"},
    "logistica": {"crear", "coordinar", "entregar", "editar_entrega", "incidencia", "reprogramar"},
    "taller": {"taller"},   # Isaías y Manawa: solo su pantalla. Nada de clientes, órdenes ni dinero.
    # El despachador SÍ ve dinero, pero solo el suyo: lo que se le debe por sus entregas.
    # No ve el de la empresa ni el de nadie más. Por eso es un rol aparte de Logística.
    "despachador": {"entregar", "mis_entregas", "incidencia"},
    "invitado": set(),      # nadie conectado: no puede hacer nada hasta entrar
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
tpl.env.filters.update(usd=usd_html, fecha=fmt_fecha, hace=hace, dia=fmt_dia, wa=wa)
CIUDADES_VE = ["Caracas", "Los Teques", "Guarenas", "Guatire", "La Guaira", "Valencia", "Maracay", "Maracaibo", "Barquisimeto", "Puerto Ordaz", "Ciudad Bolívar", "Puerto La Cruz", "Barcelona", "Lechería",
               "Mérida", "San Cristóbal", "Maturín", "Cumaná", "Porlamar", "Valera", "Punto Fijo", "Coro", "Cabimas", "Acarigua", "Guanare", "San Felipe", "Barinas", "El Tigre", "Carúpano", "Charallave", "Cúa"]
tpl.env.filters["fromiso"] = lambda v: datetime.date.fromisoformat(v) if v else None
tpl.env.globals.update(ORIGENES=ORIGENES, proveedor_visible=proveedor_visible, CONCEPTOS_EXTRA=CONCEPTOS_EXTRA, CIUDADES_VE=CIUDADES_VE, RAZAS=RAZAS, MODALIDAD=MODALIDAD, P_SUB=P_SUB, DISTRIBUIDORES=DISTRIBUIDORES, ESTADOS=ESTADOS, E_LABEL=E_LABEL, P_LABEL=P_LABEL, ENTREGA=ENTREGA, CANAL=CANAL, FORMAS_PAGO=FORMAS_PAGO, DESPACHADORES=DESPACHADORES, AGENCIAS=AGENCIAS, SIGUIENTE=SIGUIENTE)


def db():
    con = sqlite3.connect(DB, check_same_thread=False); con.row_factory = sqlite3.Row; con.execute("PRAGMA foreign_keys=ON")
    try: yield con
    finally: con.close()

# Cada rol restringido tiene su lista de lo que puede abrir. Todo lo demás lo devuelve a su pantalla.
PUERTAS = {
    "taller":      (("/taller", "/inventario", "/static", "/fotos", "/ver-como", "/favicon", "/salir", "/entrar"), "/taller"),
    "despachador": (("/mis-entregas", "/ordenes/", "/static", "/fotos", "/favicon", "/salir", "/entrar"), "/mis-entregas"),
}
TALLER_PERMITIDO = PUERTAS["taller"][0]


ABIERTO = ("/entrar", "/static", "/favicon", "/salir")   # lo único que se puede abrir sin haber entrado


@app.middleware("http")
async def puerta(request: Request, call_next):
    """La puerta del ERP. Se comprueba aquí y no página por página, para que valga
    también para lo que se agregue después:
      · sin haber entrado, solo la pantalla de entrada
      · el taller y los despachadores, solo lo suyo"""
    ruta = request.url.path
    if not ruta.startswith(ABIERTO):
        if hay_claves() and not quien_es(request):
            return RedirectResponse("/entrar", status_code=303)
        permitido, casa = PUERTAS.get(rol_de(request), (None, None))
        if permitido and not ruta.startswith(permitido):
            return RedirectResponse(casa, status_code=303)
    return await call_next(request)


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
    ("notas_taller", "produccion_id", "INTEGER"),
    ("notas_taller", "resuelto", "INTEGER NOT NULL DEFAULT 0"), ("notas_taller", "resuelto_en", "TEXT"),
    ("ordenes", "despachador_pagado", "INTEGER NOT NULL DEFAULT 0"),
    ("ordenes", "despachador_pago_id", "INTEGER"), ("ordenes", "en_registro", "INTEGER DEFAULT 0"),
    ("ordenes", "factura_fecha", "TEXT"), ("ordenes", "factura_hecha", "INTEGER DEFAULT 0"),
    ("ordenes", "factura_numero", "TEXT"), ("ordenes", "factura_por", "INTEGER"),
    ("ordenes", "pago_despachador", "REAL"), ("ordenes", "receptor_cedula", "TEXT"),
    ("ordenes", "receptor_correo", "TEXT"), ("ordenes", "requiere_factura", "INTEGER DEFAULT 0"),
    ("ordenes", "viaje_id", "INTEGER"),
    ("packs", "deliveries_prepagados", "INTEGER DEFAULT 0"), ("packs", "delivery_pagado", "INTEGER"),
    ("packs", "delivery_programado", "REAL"), ("packs", "despachador_programado", "TEXT"),
    ("packs", "fecha_programada", "TEXT"), ("packs", "nota_programada", "TEXT"),
    ("packs", "retiro_programado", "INTEGER"), ("packs", "tipo_programado", "TEXT"),
    ("produccion", "cantidad", "INTEGER NOT NULL DEFAULT 1"), ("produccion", "fecha_pago", "TEXT"),
    ("produccion", "recibido", "INTEGER DEFAULT 0"), ("produccion", "tipo_pedido", "TEXT DEFAULT 'produccion'"),
    ("productos", "canales", "TEXT"), ("productos", "proveedor", "TEXT"), ("productos", "unidad", "TEXT"),
    ("pagos", "en_cashflow", "INTEGER NOT NULL DEFAULT 0"),   # ya lo pasó Cristina al libro a mano
    ("usuarios", "usuario", "TEXT"), ("usuarios", "clave_hash", "TEXT"), ("usuarios", "creado_en", "TEXT"),
    ("usuarios", "despachador", "TEXT"),   # a qué despachador corresponde este usuario
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
    con.executescript((BASE / "modelo.sql").read_text())
    hay = {}
    for tabla, col, tipo in COLUMNAS:
        if tabla not in hay: hay[tabla] = {r[1] for r in con.execute(f"PRAGMA table_info({tabla})")}
        if col not in hay[tabla]:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}"); hay[tabla].add(col)
    con.commit(); return con


@app.on_event("startup")
def _arranque():
    con = preparar_base(DB)
    if not con.execute("SELECT 1 FROM despachadores").fetchone():   # primera vez: la lista fija pasa a la tabla (los históricos quedan inactivos)
        for n in DESPACHADORES: con.execute("INSERT OR IGNORE INTO despachadores (nombre, activo) VALUES (?,1)", (n,))
        for (n,) in con.execute("SELECT DISTINCT despachador FROM ordenes WHERE despachador IS NOT NULL AND despachador!=''").fetchall():
            con.execute("INSERT OR IGNORE INTO despachadores (nombre, activo) VALUES (?,0)", (n,))
        con.commit()
    con.close(); cargar_despachadores(); cargar_formas_pago(); cargar_ajustes()
    if os.environ.get("DECOPET_PRUEBAS") != "1": arrancar_respaldo()   # las pruebas no respaldan
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


def capitalizar(nombre):
    """Si el nombre viene todo en minúscula, lo pone con mayúscula inicial (Cristina Raffalli). Si ya trae mayúsculas, se respeta tal cual."""
    nombre = " ".join((nombre or "").split())
    if not nombre or nombre == nombre.upper(): return nombre       # todo en MAYÚSCULAS se respeta
    minus = {"de", "del", "la", "las", "los", "y", "da", "di", "do", "dos", "van", "von", "e"}
    return " ".join(w.lower() if (i and w.lower() in minus) else "-".join((p[:1].upper() + p[1:]) if p and p[0].islower() else p for p in w.split("-")) for i, w in enumerate(nombre.split()))


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


def abrir_sesion(con, uid):
    ficha = secrets.token_urlsafe(32)
    vence = (datetime.datetime.now() + datetime.timedelta(seconds=DURACION_SESION)).isoformat(" ", "seconds")
    con.execute("DELETE FROM sesiones WHERE vence_en < datetime('now','localtime')")
    con.execute("INSERT INTO sesiones (ficha, usuario_id, vence_en) VALUES (?,?,?)", (ficha, uid, vence))
    con.commit(); return ficha


def quien_es(request: Request):
    """El usuario conectado, o None. Se lee de la ficha del navegador, no de un rol escrito a mano."""
    ficha = request.cookies.get("sesion")
    if not ficha: return None
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    try:
        u = con.execute("""SELECT u.* FROM sesiones s JOIN usuarios u ON u.id=s.usuario_id
                           WHERE s.ficha=? AND s.vence_en >= datetime('now','localtime') AND u.activo=1""", (ficha,)).fetchone()
        if u: con.execute("UPDATE sesiones SET visto_en=datetime('now','localtime') WHERE ficha=?", (ficha,)); con.commit()
        return dict(u) if u else None
    finally:
        con.close()


def hay_claves(con=None):
    """¿Ya se puso alguna clave? Si no, el ERP todavía no tiene dueño."""
    propio = con is None
    if propio: con = sqlite3.connect(DB)
    try:
        return bool(con.execute("SELECT 1 FROM usuarios WHERE clave_hash IS NOT NULL AND activo=1").fetchone())
    except sqlite3.OperationalError:
        return False
    finally:
        if propio: con.close()


def rol_de(request: Request):
    u = quien_es(request)
    if not u: return "admin" if not hay_claves() else "invitado"
    # solo el administrador puede mirar el ERP como si fuera otro, para revisarlo
    if u["rol"] == "admin":
        ver = request.cookies.get("ver_como")
        if ver in PERMISOS: return ver
    return u["rol"]


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
    resp = tpl.TemplateResponse(nombre, ctx, headers={"Cache-Control": "no-store"})   # Safari guardaba paneles viejos
    # al salir de Resultados se cierra la sesión: si vuelve, pide la clave otra vez
    if not request.url.path.startswith(("/finanzas", "/historial")) and request.cookies.get("res_ok"):
        resp.delete_cookie("res_ok")
    return resp


@app.get("/entrar", response_class=HTMLResponse)
def entrar(request: Request, mal: str = "", con=Depends(db)):
    if quien_es(request): return RedirectResponse("/inicio", status_code=303)
    return render(request, "entrar.html", seccion="entrar", primera_vez=not hay_claves(con),
                  mal=mal, sin_menu=True)


@app.post("/entrar")
def entrar_post(request: Request, usuario: str = Form(""), clave: str = Form(""), con=Depends(db)):
    u = con.execute("SELECT * FROM usuarios WHERE lower(TRIM(usuario))=? AND activo=1",
                    (usuario.strip().lower(),)).fetchone()
    if not (u and clave_correcta(clave, u["clave_hash"])):
        return RedirectResponse("/entrar?mal=1", status_code=303)
    casa = PUERTAS.get(u["rol"], (None, "/inicio"))[1]
    r = RedirectResponse(casa, status_code=303)
    r.set_cookie("sesion", abrir_sesion(con, u["id"]), max_age=DURACION_SESION, httponly=True, samesite="lax")
    r.delete_cookie("ver_como"); r.delete_cookie("rol")
    return r


@app.post("/entrar/primera-vez")
def entrar_primera(request: Request, clave: str = Form(""), clave2: str = Form(""), con=Depends(db)):
    """La primera vez, Cristina pone su propia clave. Nadie más la ve nunca, ni queda escrita."""
    if hay_claves(con): return RedirectResponse("/entrar", status_code=303)
    if len(clave.strip()) < 6 or clave != clave2:
        return RedirectResponse("/entrar?mal=" + ("corta" if len(clave.strip()) < 6 else "distinta"), status_code=303)
    u = con.execute("SELECT * FROM usuarios WHERE rol='admin' AND activo=1 ORDER BY id LIMIT 1").fetchone()
    con.execute("UPDATE usuarios SET clave_hash=? WHERE id=?", (cifrar_clave(clave.strip()), u["id"])); con.commit()
    r = RedirectResponse("/inicio", status_code=303)
    r.set_cookie("sesion", abrir_sesion(con, u["id"]), max_age=DURACION_SESION, httponly=True, samesite="lax")
    r.delete_cookie("rol")
    return r


@app.get("/salir")
def salir(request: Request, con=Depends(db)):
    f = request.cookies.get("sesion")
    if f: con.execute("DELETE FROM sesiones WHERE ficha=?", (f,)); con.commit()
    r = RedirectResponse("/entrar", status_code=303)
    r.delete_cookie("sesion"); r.delete_cookie("ver_como"); r.delete_cookie("rol"); r.delete_cookie("res_ok")
    return r


@app.get("/ver-como/{rol}")
def ver_como(request: Request, rol: str, volver: str = "/ordenes"):
    """Vista previa: el administrador mira el ERP como lo vería otro. No cambia quién eres."""
    u = quien_es(request)
    if u and u["rol"] != "admin": return RedirectResponse("/inicio", status_code=303)
    r = RedirectResponse(volver, status_code=303)
    if rol == "admin": r.delete_cookie("ver_como")
    else: r.set_cookie("ver_como", rol if rol in PERMISOS else "admin", samesite="lax")
    return r

@app.get("/")
def raiz(): return RedirectResponse("/inicio", status_code=303)


@app.get("/inicio", response_class=HTMLResponse)
def inicio(request: Request, con=Depends(db)):
    rol = rol_de(request); hoy = datetime.date.today(); h = hoy.isoformat(); mes = hoy.strftime("%Y-%m")
    activas = cargar_ordenes(con, {"estado": "activas"}, rol)
    c = {
        "por_revisar": sum(1 for o in activas if o["estado_pago"] == "por_confirmar"),
        "incidencias": con.execute("SELECT COUNT(*) FROM incidencias WHERE estado='abierta'").fetchone()[0],
        "sin_coordinar": sum(1 for o in activas if not o["coordinada"]),
        "hoy": sum(1 for o in activas if (o["fecha_prometida"] or h) <= h),
        "retrasadas": sum(1 for o in activas if any(a[0] == "retrasada" for a in o["alertas"])),
        "por_facturar": con.execute("""SELECT COUNT(*) FROM ordenes WHERE estado!='cancelada'
                                        AND (requiere_factura=1 OR canal='cashea') AND COALESCE(factura_hecha,0)=0""").fetchone()[0] if rol == "admin" else 0,
        # envío nacional que nadie ha llevado a la oficina: se acumulan, no dependen del día prometido
        "por_llevar": con.execute("""SELECT COUNT(*) FROM ordenes WHERE tipo_entrega='nacional' AND viaje_id IS NULL
                                     AND origen_excel=0 AND estado IN ('pendiente','en_ruta')""").fetchone()[0],
    }
    deudas = con.execute("""SELECT COUNT(*) n, COALESCE(SUM(total - (SELECT COALESCE(SUM(monto_usd),0) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado')),0) s
                            FROM ordenes o WHERE estado!='cancelada' AND estado_pago IN ('abonada','sin_pago','rechazado')""").fetchone()
    c["con_saldo"], c["saldo_total"] = deudas["n"], deudas["s"]
    # retiros de pack y repuestos prepagados PROGRAMADOS para hoy (o atrasados): los que de verdad se entregan
    packs = sum(1 for k in cargar_packs(con) if k["saldo"] > 0 and k["fecha_programada"] and k["fecha_programada"] <= h)
    packs += sum(1 for r in cargar_prepagados(con) if r["fecha_programada"] and r["fecha_programada"] <= h)
    c["packs"] = packs
    # miembros que cruzaron el umbral hoy (36 días exactos sin repuesto)
    toca = con.execute("""SELECT COUNT(*) FROM (SELECT c.id, MAX(CASE WHEN p.categoria='repuesto' OR p.sku LIKE 'PRO-%' THEN substr(o.creado_en,1,10) END) ult
        FROM clientes c JOIN ordenes o ON o.cliente_id=c.id AND o.estado!='cancelada' JOIN orden_lineas l ON l.orden_id=o.id JOIN productos p ON p.id=l.producto_id
        GROUP BY c.id HAVING SUM(CASE WHEN p.sku LIKE 'PRO-%' THEN 1 ELSE 0 END) > 0 AND julianday(?) - julianday(ult) BETWEEN 36 AND 42)""", (h,)).fetchone()[0]
    cumples_todos = cumples_proximos(con, 2)
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
                   "lista_cumples": (cumples_l + cumples_hechos)[:3], "lista_rep": (rep_l + rep_h)[:3], "lista_cobro": (cobro_l + cobro_h)[:3], "lista": segs[:12],
                   "tot_rep": len(rep_l) + len(rep_h), "tot_cumples": len(cumples_l) + len(cumples_hechos), "tot_cobro": len(cobro_l) + len(cobro_h),
                   "hechos_rep": len(rep_h), "hechos_cumples": len(cumples_hechos), "hechos_cobro": len(cobro_h)}
    c["fotos"] = con.execute("SELECT COUNT(*) FROM fotos WHERE permiso='sin_confirmar'").fetchone()[0]
    v = con.execute("SELECT COUNT(*) n, COALESCE(SUM(total),0) venta FROM ordenes WHERE substr(creado_en,1,10)=? AND estado!='cancelada'", (h,)).fetchone()
    dias_mes = max(1, hoy.day - 1)
    prom = con.execute("SELECT COALESCE(SUM(total),0) FROM ordenes WHERE substr(creado_en,1,7)=? AND substr(creado_en,1,10)<? AND estado!='cancelada'", (mes, h)).fetchone()[0] / dias_mes
    disponible = sum(x["saldo"] for x in saldos(con) if x["activa"]) if rol == "admin" else 0   # todas las cajas, igual que en Cash flow
    # ventas por día, últimos 14 días
    dias14 = [(hoy - datetime.timedelta(days=13 - i)) for i in range(14)]
    por_dia = {r[0]: (r[1], r[2]) for r in con.execute("SELECT substr(creado_en,1,10), SUM(total), COUNT(*) FROM ordenes WHERE estado!='cancelada' AND substr(creado_en,1,10)>=? GROUP BY 1", (dias14[0].isoformat(),))}
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
        ant = datetime.date(hoy.year, hoy.month, 1) - datetime.timedelta(days=1)
        # el día de pago que toca ahora: el más reciente que ya llegó, hasta 2 días después
        tocan = [p for p, fin in ventana_pago(ant.year, ant.month) + ventana_pago(hoy.year, hoy.month) if p <= hoy <= fin]
        c["es_quincena"] = any(f == hoy for f in tocan)
        c["quincena_falta"] = []
        if tocan:
            f = max(tocan); desde = (f - datetime.timedelta(days=3)).isoformat()
            c["quincena_falta"] = [n for n in (cfg_json(con, "sueldos", {}) or {})
                                   if not con.execute("""SELECT 1 FROM gastos WHERE categoria='Equipo' AND subcategoria='Quincena'
                                                         AND TRIM(COALESCE(proveedor,''))=? AND fecha>=?""", (n, desde)).fetchone()]
        c["quincena_n"] = len(c["quincena_falta"])

    deuda_desp = con.execute("""SELECT despachador, SUM(COALESCE(delivery, 0)) m, COUNT(*) n FROM ordenes
                                WHERE despachador IS NOT NULL AND despachador!='' AND estado!='cancelada' AND origen_excel=0 AND despachador_pagado=0 GROUP BY 1 HAVING m>0""").fetchall() if rol == "admin" else []
    viajes_desp = con.execute("SELECT despachador, SUM(monto) m FROM viajes_agencia WHERE pagado=0 GROUP BY 1 HAVING m>0").fetchall() if rol == "admin" else []
    c["desp_debe"] = sum(r["m"] for r in deuda_desp) + sum(r["m"] for r in viajes_desp)   # entregas + viajes a la agencia
    # pedidos cuyo día de pago llegó (la grama se paga los viernes aunque llegue el lunes)
    c["toca_pagar_prov"] = [dict(r) for r in con.execute("""SELECT pr.id, pr.pieza, pr.responsable, pr.fecha_pago,
                            pr.costo - COALESCE((SELECT SUM(a.monto) FROM abonos_produccion a WHERE a.produccion_id=pr.id),0) debe
                            FROM produccion pr WHERE pr.estado!='cancelado' AND pr.fecha_pago IS NOT NULL AND pr.fecha_pago<=?
                              AND pr.costo - COALESCE((SELECT SUM(a.monto) FROM abonos_produccion a WHERE a.produccion_id=pr.id),0) > 0.009
                            ORDER BY pr.fecha_pago""", (h,))] if rol == "admin" else []
    # a los despachadores se les paga los LUNES: el resto de la semana el aviso solo estorba mientras se acumulan entregas
    viejo = con.execute("""SELECT MIN(COALESCE(fecha_entrega, substr(creado_en,1,10))) FROM ordenes
                           WHERE despachador IS NOT NULL AND despachador!='' AND estado!='cancelada'
                             AND origen_excel=0 AND despachador_pagado=0""").fetchone()[0]
    lunes = hoy - datetime.timedelta(days=hoy.weekday())           # el lunes de esta semana
    atrasado = bool(viejo and datetime.date.fromisoformat(viejo[:10]) < lunes and hoy.weekday() != 0)
    c["toca_pagar_desp"] = hoy.weekday() == 0 or atrasado          # el lunes, o si ya se pasó el lunes sin pagar
    c["desp_atrasado"] = atrasado
    c["desp_n"] = len({r["despachador"] for r in deuda_desp} | {r["despachador"] for r in viajes_desp}); c["es_lunes"] = hoy.weekday() == 0
    # porches que el taller ya dejó armados, esperando venta
    armados = con.execute("""SELECT p.nombre, (SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario m WHERE m.producto_id=p.id) listos
                             FROM productos p WHERE p.activo=1 AND p.categoria='porche'
                             AND EXISTS (SELECT 1 FROM receta r WHERE r.producto_id=p.id) ORDER BY p.orden""").fetchall()
    armados = [dict(a) | {"corto": a["nombre"].replace("El Porche Versión PRO ", "")} for a in armados]
    n_armados = sum(a["listos"] for a in armados)
    avisos_taller = con.execute("SELECT * FROM notas_taller WHERE resuelto=0 AND visto=0 ORDER BY id DESC LIMIT 5").fetchall() if rol == "admin" else []
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
    llega = [dict(r) for r in con.execute("""SELECT pr.cantidad - pr.recibido faltan, pr.fecha_esperada, COALESCE(pr.pieza, p.nombre) nombre, pr.responsable FROM produccion pr LEFT JOIN productos p ON p.id=pr.producto_id
        WHERE pr.estado='en_proceso' AND pr.fecha_esperada IS NOT NULL AND pr.fecha_esperada <= ? ORDER BY pr.fecha_esperada""", (h,))]
    proximos = [dict(r) for r in con.execute("""SELECT pr.cantidad - pr.recibido faltan, pr.fecha_esperada, COALESCE(pr.pieza, p.nombre) nombre, pr.responsable, (pr.fecha_esperada < ?) atrasado FROM produccion pr LEFT JOIN productos p ON p.id=pr.producto_id
        WHERE pr.estado='en_proceso' AND (pr.fecha_esperada IS NULL OR pr.fecha_esperada != ?) ORDER BY pr.fecha_esperada IS NULL, pr.fecha_esperada LIMIT 8""", (h, h))]
    # lo que llega hoy y lo que le debes a proveedores suben a la franja de avisos
    c["llegan_hoy"] = sum(l["faltan"] for l in llega); c["llegan_hoy_n"] = len(llega)
    # para Pedidos es solo un recordatorio: no tiene acceso a Taller, así que se le dice qué llega y ya
    c["llegan_hoy_qué"] = " · ".join(f"{int(l['faltan'])}× {l['nombre']}" for l in llega[:3])
    # para Cristina es operativo: de quién llega, y qué trae cada uno
    por_quien = {}
    for l in llega: por_quien.setdefault(l["responsable"] or "sin asignar", []).append(f"{int(l['faltan'])}× {l['nombre']}")
    c["llegan_hoy_de"] = " · ".join(f"{q}: {', '.join(v[:3])}" + (f" y {len(v) - 3} más" if len(v) > 3 else "")
                                    for q, v in list(por_quien.items())[:3])
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


def cargar_ordenes(con, filtros, rol):
    sql = """SELECT o.*, c.nombre cliente, c.telefono, u.nombre creada_por_nombre,
             (SELECT d.direccion FROM direcciones d WHERE d.cliente_id=o.cliente_id ORDER BY d.principal DESC, d.id LIMIT 1) cli_direccion,
             (SELECT d.zona      FROM direcciones d WHERE d.cliente_id=o.cliente_id ORDER BY d.principal DESC, d.id LIMIT 1) cli_zona,
             (SELECT d.ciudad    FROM direcciones d WHERE d.cliente_id=o.cliente_id ORDER BY d.principal DESC, d.id LIMIT 1) cli_ciudad,
             (SELECT d.maps      FROM direcciones d WHERE d.cliente_id=o.cliente_id ORDER BY d.principal DESC, d.id LIMIT 1) cli_maps,
             (SELECT GROUP_CONCAT(CAST(l.cantidad AS INTEGER) || '× ' || l.nombre || COALESCE(' ' || l.color, '') || CASE WHEN l.malla THEN ' +malla' ELSE '' END || CASE WHEN l.personalizacion IS NOT NULL THEN ' ✎' ELSE '' END, ' · ') FROM orden_lineas l WHERE l.orden_id=o.id) productos,
             (SELECT GROUP_CONCAT(CAST(l.cantidad AS INTEGER) || '× ' || l.nombre || COALESCE(' ' || l.color, '') || CASE WHEN l.malla THEN ' +malla' ELSE '' END || CASE WHEN l.personalizacion IS NOT NULL THEN ' ✎' ELSE '' END, ' · ') FROM orden_lineas l
              WHERE l.orden_id=o.id AND NOT EXISTS (SELECT 1 FROM repuestos_prepagados rp WHERE rp.linea_id=l.id AND rp.entregado_en IS NULL)
                AND NOT EXISTS (SELECT 1 FROM packs k WHERE k.orden_id=o.id AND k.producto_id=l.producto_id AND k.entregadas_inicio=0)) productos_hoy,
             (SELECT GROUP_CONCAT(CAST(l.cantidad AS INTEGER) || '× ' || l.nombre || COALESCE(' ' || l.color, '') || CASE WHEN l.malla THEN ' +malla' ELSE '' END || CASE WHEN l.personalizacion IS NOT NULL THEN ' ✎' ELSE '' END
                 || CASE WHEN EXISTS (SELECT 1 FROM repuestos_prepagados rp WHERE rp.linea_id=l.id AND rp.entregado_en IS NULL) THEN '@PEND' ELSE '' END
                 || COALESCE((SELECT '@PACK' || (k.unidades - k.entregadas_inicio - (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=k.id)) || '/' || k.unidades
                              FROM packs k WHERE k.orden_id=o.id AND k.producto_id=l.producto_id
                              AND k.unidades - k.entregadas_inicio - (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=k.id) > 0 LIMIT 1), ''), '||') FROM orden_lineas l WHERE l.orden_id=o.id) lineas_txt,
             (SELECT COUNT(*) FROM incidencias i WHERE i.orden_id=o.id AND i.estado='abierta') incidencias,
             (SELECT forma FROM pagos p WHERE p.orden_id=o.id ORDER BY id LIMIT 1) forma_pago,
             (SELECT MIN(COALESCE(confirmado_en, fecha)) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado') pagada_en,
             (SELECT COALESCE(SUM(monto_usd),0) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado') pagado,
             (SELECT motivo_revision FROM pagos p WHERE p.orden_id=o.id AND p.estado='por_confirmar' ORDER BY id DESC LIMIT 1) motivo_revision,
             (SELECT COUNT(*) FROM repuestos_prepagados rp WHERE rp.orden_id=o.id AND rp.entregado_en IS NULL) prepagados_pend
             FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id LEFT JOIN usuarios u ON u.id=o.creada_por WHERE 1=1"""
    args = []
    if filtros.get("estado") == "activas": sql += " AND o.estado NOT IN ('entregada','cancelada')"
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
    o["lineas"] = con.execute("SELECT * FROM orden_lineas WHERE orden_id=?", (oid,)).fetchall()
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
    o["mascotas"] = con.execute("SELECT * FROM mascotas WHERE cliente_id=?", (o["cliente_id"],)).fetchall()
    o["ganancia"] = round((o["total"] or 0) - (o["iva"] or 0) - (o["comision"] or 0) - (o["costo_productos"] or 0) - (o["costo_entrega"] or 0), 2)
    o["margen"] = round(o["ganancia"] / o["total"] * 100, 1) if o["total"] else 0
    o["resumen"] = resumen_despacho(o)
    return o


def precio_linea(p, cantidad):
    """Precio base de una línea: aplica el precio por par cuando existe (bowls, platos)."""
    c = int(cantidad)
    if p["precio_par"] and c >= 2:
        return (c // 2) * p["precio_par"] + (c % 2) * p["precio"]
    return p["precio"] * cantidad


def descripcion_linea(l):
    partes = [f"{int(l['cantidad'])} × {l['nombre']}"]
    if l["color"]: partes.append(f"plato {l['color']}")
    if l["malla"]: partes.append("+ malla")
    if l["personalizacion"]: partes.append(f"personalizado: {l['personalizacion']}")
    return " · ".join(partes)


def resumen_despacho(o):
    L = [f"📦 {o['numero']} — {o['cliente']}" + (f" · {o['telefono']}" if o["telefono"] else "")]
    for l in o["lineas"]:
        L.append("• " + descripcion_linea(l))
    ent = ENTREGA.get(o["tipo_entrega"], "")
    if o["fecha_prometida"]: ent += f" · {fmt_fecha(o['fecha_prometida'])}" + (f" {o['franja']}" if o["franja"] else "")
    if o["agencia"]: ent += f" · {o['agencia']}" + (f" guía {o['guia']}" if o["guia"] else "")
    if o["tipo_entrega"] == "nacional" and o["modalidad_envio"]: ent += f" · {MODALIDAD[o['modalidad_envio']].lower()}"
    L.append(f"🚚 {ent}")
    if o["tipo_entrega"] == "distribuidor": L.append(f"🏪 Retira en {o['distribuidor'] or 'distribuidor'} ({o['ciudad'] or ''})")
    elif o["tipo_entrega"] != "pickup" and o["direccion"]:
        L.append(f"📍 {o['zona'] + ', ' if o['zona'] else ''}{o['direccion']}" + (f" — recibe {o['receptor_nombre']}" + (f" {o['receptor_telefono']}" if o["receptor_telefono"] else "") if o["receptor_nombre"] else ""))
        if o["maps"]: L.append(f"🗺 {o['maps']}")
    if o["estado_pago"] == "contra_entrega":
        pend = o["monto_contra_entrega"] or (o["total"] - o["pagado"])
        L.append(f"💵 CONTRA ENTREGA: cobrar {fmt_usd(pend)} en efectivo" + (f" (ya pagó {fmt_usd(o['pagado'])} por {o['forma_pago_prevista'].split(' + ')[0]})" if o["pagado"] > 0 else ""))
    elif o["estado_pago"] == "abonada": L.append(f"💵 Abonó {fmt_usd(o['pagado'])}; falta {fmt_usd(o['total'] - o['pagado'])}")
    elif o["estado_pago"] == "sin_pago": L.append(f"💵 Por cobrar {fmt_usd(o['total'])}")
    else: L.append("✅ Pagado, no cobrar nada")
    if o["notas_entrega"]: L.append(f"📝 {o['notas_entrega']}")
    return "\n".join(L)


@app.get("/ordenes/nueva/panel", response_class=HTMLResponse)
def nueva_panel(request: Request, cliente: int = 0, con=Depends(db)):
    productos = con.execute("SELECT * FROM productos WHERE activo=1 AND tipo='producto' ORDER BY orden").fetchall()
    opciones = {r["sku"]: r for r in con.execute("SELECT * FROM productos WHERE tipo='opcion'")}
    clientes = con.execute("""SELECT c.*, (SELECT direccion || COALESCE(' · ' || zona,'') FROM direcciones d WHERE d.cliente_id=c.id AND principal=1) dir,
                              (SELECT GROUP_CONCAT(m.nombre || COALESCE(' (' || m.raza || ')',''), ', ') FROM mascotas m WHERE m.cliente_id=c.id) perros FROM clientes c ORDER BY nombre""").fetchall()
    pre = con.execute("SELECT nombre FROM clientes WHERE id=?", (cliente,)).fetchone() if cliente else None
    return render(request, "_orden_nueva.html", productos=productos, opciones=opciones, clientes=clientes, tasa=tasa_hoy(con), precliente=pre["nombre"] if pre else "", tarifas=con.execute("SELECT zona, tarifa FROM tarifas ORDER BY orden, tarifa, zona").fetchall())


@app.get("/ordenes/{oid}/panel", response_class=HTMLResponse)
def orden_panel(request: Request, oid: int, con=Depends(db)):
    o = cargar_orden(con, oid)
    if not o: return HTMLResponse("<p>No existe.</p>")
    return render(request, "_orden_panel.html", o=o)


def registrar(con, oid, uid, accion, detalle=None, motivo=None):
    con.execute("INSERT INTO historial (orden_id,usuario_id,accion,detalle,motivo) VALUES (?,?,?,?,?)", (oid, uid, accion, detalle, motivo))
    con.execute("UPDATE ordenes SET actualizado_en=datetime('now','localtime') WHERE id=?", (oid,))


def volver(oid, request):
    v = request.query_params.get("volver")
    if v: return RedirectResponse(v, status_code=303)
    return RedirectResponse(f"/ordenes?estado={request.query_params.get('estado','todas')}&abrir={oid}", status_code=303)


@app.post("/ordenes/{oid}/estado")
def cambiar_estado(request: Request, oid: int, estado: str = Form(...), motivo: str = Form(""), monto_recibido: str = Form(""), moneda_recibida: str = Form("USD"), fecha: str = Form(""), con=Depends(db)):
    rol = rol_de(request); uid = usuario_id(rol, request)
    if PERMISO_ESTADO.get(estado) not in PERMISOS[rol]: return volver(oid, request)
    o = cargar_orden(con, oid)
    yo = quien_es(request)
    if yo and yo["rol"] == "despachador" and o["despachador"] != yo["despachador"]:
        return RedirectResponse("/mis-entregas", status_code=303)   # solo sus propias entregas
    if estado in ("entregada", "en_ruta") and not coordinada(o):
        registrar(con, oid, uid, "bloqueado", "Falta coordinar (despachador / agencia / distribuidor) antes de marcar entregado"); con.commit(); return volver(oid, request)
    sets = ["estado=?"]; args = [estado]
    if estado == "entregada":
        fe = fecha.strip() or datetime.date.today().isoformat()   # se puede registrar una entrega de otro día
        sets.append("fecha_entrega=?"); args.append(fe if fe != datetime.date.today().isoformat() else datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
        if o["estado_pago"] == "contra_entrega":
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
            if monto:
                falto = round(o["total"] - o["pagado"] - monto, 2)
                registrar(con, oid, uid, "pago", f"Cobrado contra entrega {fmt_usd(monto)} en efectivo el {fe} → {caja_efectivo(con)}"
                          + (f" · quedan {fmt_usd(falto)} por cobrar" if falto > 0.009 else ""))
            else:
                registrar(con, oid, uid, "pago", f"Entregado sin cobrar: quedan {fmt_usd(falta)} por cobrar")
            if monto and not o["fecha_pago"] and nuevo_estado == "pagada":
                sets.append("fecha_pago=?"); args.append(fe)   # el día que se entrega es el día que pagaron
    if estado == "pendiente":
        con.execute("UPDATE pagos SET estado='confirmado', confirmado_por=?, confirmado_en=datetime('now','localtime') WHERE orden_id=? AND estado='por_confirmar'", (uid, oid))
        pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (oid,)).fetchone()[0]
        sets.append("estado_pago=?"); args.append(estado_pago_de(pagado, o["total"]))
    if estado == "cancelada":
        if not motivo: return volver(oid, request)
        if o["pagado"] > 0: sets.append("estado_pago='reembolsada'")
    args.append(oid)
    con.execute(f"UPDATE ordenes SET {', '.join(sets)} WHERE id=?", args)
    registrar(con, oid, uid, "estado", f"{E_LABEL[o['estado']]} → {E_LABEL[estado]}", motivo or None)
    con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/pago/confirmar")
def confirmar_pago(request: Request, oid: int, con=Depends(db)):
    rol = rol_de(request); uid = usuario_id(rol)
    if "confirmar_pago" not in PERMISOS[rol]: return volver(oid, request)
    con.execute("UPDATE pagos SET estado='confirmado', confirmado_por=?, confirmado_en=datetime('now','localtime') WHERE orden_id=? AND estado='por_confirmar'", (uid, oid))
    o = con.execute("SELECT total FROM ordenes WHERE id=?", (oid,)).fetchone()
    pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (oid,)).fetchone()[0]
    con.execute("UPDATE ordenes SET estado_pago=? WHERE id=?", (estado_pago_de(pagado, o["total"]), oid)); fijar_fecha_pago(con, oid)
    registrar(con, oid, uid, "pago", f"Pago confirmado por Cristina ({fmt_usd(pagado)})"); con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/pago/rechazar")
def rechazar_pago(request: Request, oid: int, motivo: str = Form(""), con=Depends(db)):
    rol = rol_de(request); uid = usuario_id(rol)
    if "rechazar_pago" not in PERMISOS[rol]: return volver(oid, request)
    con.execute("UPDATE pagos SET estado='rechazado' WHERE orden_id=? AND estado='por_confirmar'", (oid,))
    con.execute("UPDATE ordenes SET estado_pago='rechazado' WHERE id=?", (oid,))
    registrar(con, oid, uid, "pago", "Pago rechazado", motivo or None); con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/contra-entrega")
def contra_entrega(request: Request, oid: int, con=Depends(db)):
    rol = rol_de(request)
    if "contra_entrega" not in PERMISOS[rol]: return volver(oid, request)
    con.execute("UPDATE ordenes SET estado_pago='contra_entrega' WHERE id=? AND estado_pago IN ('sin_pago','rechazado','por_confirmar')", (oid,))
    registrar(con, oid, usuario_id(rol), "estado", "Autorizada salida contra entrega (efectivo) → Confirmada"); con.commit(); return volver(oid, request)


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
    registrar(con, oid, usuario_id(rol), "pago", f"Pago reportado: {forma} {fmt_usd(monto)} ref {f.get('referencia') or '—'}"); con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/factura")
def factura_toggle(request: Request, oid: int, hecha: str = Form("0"), requiere: str = Form(""), numero: str = Form(""), fecha: str = Form(""), con=Depends(db)):
    """Marcar la factura hecha guarda su número y su fecha: así queda constancia de que se hizo, no solo un visto."""
    rol = rol_de(request)
    if "ver_dinero" not in PERMISOS[rol]: return volver(oid, request)
    uid = usuario_id(rol)
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
                      forma: str = Form(""), fecha: str = Form(""), referencia: str = Form(""), con=Depends(db)):
    """El cliente agrega algo a una orden que ya existe y lo paga: sube el total de esa orden y queda el pago."""
    rol = rol_de(request)
    if "confirmar_pago" not in PERMISOS[rol]: return volver(oid, request)
    c = (concepto_otro.strip() if concepto == "Otro" else concepto.strip()) or "Cobro adicional"
    m = cifra(monto)
    if m > 0 and forma.strip():
        cobro_extra(con, oid, c, m, forma.strip(), (fecha or "").strip() or None, usuario_id(rol), referencia)
        con.commit()
    return volver(oid, request)


@app.post("/ordenes/{oid}/cobrar")
async def cobrar_saldo(request: Request, oid: int, con=Depends(db)):
    """Cristina registra un cobro y lo deja confirmado de una (sin pasar por 'por revisar')."""
    rol = rol_de(request); f = await request.form(); uid = usuario_id(rol)
    if "confirmar_pago" not in PERMISOS[rol]: return volver(oid, request)
    o = con.execute("SELECT total FROM ordenes WHERE id=?", (oid,)).fetchone()
    monto = float(f.get("monto_usd") or 0); forma = f.get("forma") or "Efectivo USD"
    if monto <= 0: return volver(oid, request)
    en_bs = es_bolivares(forma); tasa = tasa_hoy(con)["valor"]
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,tasa,cuenta,referencia,fecha,estado,confirmado_por,confirmado_en) VALUES (?,?,?,?,?,?,?,?,?,'confirmado',?,datetime('now','localtime'))",
                (oid, forma, monto, monto * tasa if en_bs else monto, "VES" if en_bs else "USD", tasa if en_bs else None, FORMA_CUENTA.get(forma), f.get("referencia") or None, datetime.date.today().isoformat(), uid))
    pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (oid,)).fetchone()[0]
    con.execute("UPDATE ordenes SET estado_pago=? WHERE id=?", (estado_pago_de(pagado, o["total"]), oid)); fijar_fecha_pago(con, oid)
    registrar(con, oid, uid, "pago", f"Cobro registrado: {forma} {fmt_usd(monto)}" + (f" · queda {fmt_usd(o['total'] - pagado)}" if pagado < o["total"] - 0.01 else " · saldada")); con.commit(); return volver(oid, request)


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
    if cambios: registrar(con, oid, usuario_id(rol), "entrega", "; ".join(cambios))
    con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/revertir")
def revertir_estado(request: Request, oid: int, con=Depends(db)):
    """Vuelve la orden a Pendiente (deshace 'En ruta' o 'Entregado'). No toca los pagos."""
    rol = rol_de(request)
    if "entregar" not in PERMISOS[rol]: return volver(oid, request)
    o = con.execute("SELECT estado FROM ordenes WHERE id=?", (oid,)).fetchone()
    con.execute("UPDATE ordenes SET estado='pendiente', fecha_entrega=NULL, actualizado_en=datetime('now','localtime') WHERE id=?", (oid,))
    registrar(con, oid, usuario_id(rol), "estado", f"Vuelve a Pendiente (estaba {E_LABEL.get(o['estado'], o['estado'])})"); con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/eliminar")
def eliminar_orden(request: Request, oid: int, con=Depends(db)):
    """Borra la orden por completo (solo Cristina). Se usa para pruebas o errores de carga; para una venta real que se cae, usar Cancelar."""
    rol = rol_de(request)
    if "ver_dinero" not in PERMISOS[rol]: return volver(oid, request)
    con.execute("DELETE FROM entregas_repuesto WHERE pack_id IN (SELECT id FROM packs WHERE orden_id=?)", (oid,))
    for tb in ("packs", "repuestos_prepagados", "pagos", "historial", "incidencias", "orden_lineas", "gastos", "mov_inventario", "fotos"): con.execute(f"DELETE FROM {tb} WHERE orden_id=?", (oid,))
    con.execute("DELETE FROM ordenes WHERE id=?", (oid,)); con.commit()
    return RedirectResponse(request.query_params.get("volver") or "/ordenes", status_code=303)


@app.get("/ordenes/{oid}/eliminar")
def eliminar_orden_get(oid: int):   # si alguien recarga la página tras eliminar, volver a Órdenes en vez de mostrar un error
    return RedirectResponse("/ordenes", status_code=303)


@app.post("/ordenes/{oid}/guia")
def poner_guia(request: Request, oid: int, guia: str = Form(""), agencia: str = Form(""), con=Depends(db)):
    rol = rol_de(request)
    if "coordinar" not in PERMISOS[rol]: return volver(oid, request)
    con.execute("UPDATE ordenes SET guia=COALESCE(NULLIF(?,''),guia), agencia=COALESCE(NULLIF(?,''),agencia) WHERE id=?", (guia.strip(), agencia.strip(), oid))
    registrar(con, oid, usuario_id(rol), "guia", f"Guía {guia.strip() or '—'}" + (f" · {agencia}" if agencia else "")); con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/despachador")
def asignar_despachador(request: Request, oid: int, despachador: str = Form(""), despachador_otro: str = Form(""), con=Depends(db)):
    rol = rol_de(request)
    if "coordinar" not in PERMISOS[rol]: return volver(oid, request)
    if despachador == "__otro__": despachador = despachador_otro.strip()
    con.execute("UPDATE ordenes SET despachador=? WHERE id=?", (despachador or None, oid)); fijar_pago_despachador(con, oid)
    registrar(con, oid, usuario_id(rol), "despachador", f"Asignado: {despachador or '—'}"); con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/incidencia")
def nueva_incidencia(request: Request, oid: int, tipo: str = Form(...), descripcion: str = Form(""), clase: str = Form("incidencia"), con=Depends(db)):
    rol = rol_de(request)
    if "incidencia" not in PERMISOS[rol]: return volver(oid, request)
    o = con.execute("SELECT despachador FROM ordenes WHERE id=?", (oid,)).fetchone()
    con.execute("INSERT INTO incidencias (orden_id,clase,tipo,descripcion,responsable,autor_id) VALUES (?,?,?,?,?,?)", (oid, clase, tipo, descripcion, o["despachador"], usuario_id(rol)))
    if tipo == "entrega_fallida":
        con.execute("UPDATE ordenes SET estado='pendiente', despachador=NULL WHERE id=?", (oid,))
    registrar(con, oid, usuario_id(rol), clase, f"{tipo}: {descripcion}"); con.commit(); return volver(oid, request)


@app.post("/ordenes/{oid}/incidencia/{iid}/cerrar")
def cerrar_incidencia(request: Request, oid: int, iid: int, resolucion: str = Form(""), con=Depends(db)):
    con.execute("UPDATE incidencias SET estado='cerrada', resolucion=?, cerrado_en=datetime('now','localtime') WHERE id=?", (resolucion, iid))
    registrar(con, oid, usuario_id(rol_de(request)), "incidencia_cerrada", resolucion); con.commit(); return volver(oid, request)


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
                    subprocess.run(["/bin/bash", str(BASE.parent / "scripts" / "respaldo.sh")],
                                   capture_output=True, text=True, timeout=120)
            except Exception:
                pass          # un respaldo fallido nunca puede tumbar el ERP
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


def _sirve(v):
    """'Pendiente' es el relleno que se guardaba cuando un dato no se pedía: cuenta como vacío."""
    return bool(v and str(v).strip() and str(v).strip().lower() != "pendiente")


@app.post("/ordenes/nueva")
async def crear_orden(request: Request, con=Depends(db)):
    rol = rol_de(request); f = await request.form(); uid = usuario_id(rol)
    cid = f.get("cliente_id") or None; cliente_recien_creado = False
    nac = f.get("tipo_entrega") == "nacional"
    if not cid and (f.get("cliente_nombre_pila") or "").strip():
        if not _sirve(f.get("cliente_telefono")): return _falta("El teléfono del cliente es obligatorio.")
        if not _sirve(f.get("cliente_correo")): return _falta("El correo del cliente es obligatorio.")
        cliente_recien_creado = True
        np_, ap = f["cliente_nombre_pila"].strip(), (f.get("cliente_apellido") or "").strip() or None
        cur = con.execute("INSERT INTO clientes (nombre_pila,apellido,nombre,telefono,cedula,correo,ciudad,canal_habitual,origen,referido_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (np_, ap, nombre_completo(np_, ap), normalizar_telefono(f.get("cliente_telefono")) or "Pendiente", (f.get("cliente_cedula") or "").strip().upper() or None,
                           (f.get("cliente_correo") or "").strip() or "Pendiente", (f.get("cliente_ciudad") or f.get("ciudad") or "").strip() or "Pendiente", f.get("canal"),
                           (f.get("cliente_origen") or "").strip() or None,
                           int(f["cliente_referido_id"]) if (f.get("cliente_referido_id") or "").isdigit() else None))
        cid = cur.lastrowid
        guardar_mascotas(con, cid, f, prefijo="cliente_mascota_")
        if (f.get("cliente_direccion") or "").strip():   # dirección habitual del cliente nuevo
            con.execute("INSERT INTO direcciones (cliente_id,etiqueta,direccion,ciudad,maps,principal) VALUES (?,?,?,?,?,1)", (cid, "Principal", f["cliente_direccion"].strip(), (f.get("cliente_ciudad") or "").strip() or None, (f.get("cliente_maps") or "").strip() or None))
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
            if cliente_recien_creado and f.get("ciudad"): con.execute("UPDATE clientes SET ciudad=? WHERE id=? AND ciudad='Pendiente'", (f["ciudad"], cid))
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
    px = float(f.get("personalizacion_extra") or 0)
    if px > 0:   # personalización cobrada aparte (monto libre): entra como línea de opción
        lineas.append((OPC["OPC-PERSO"], 1, (f.get("personalizacion_nombre") or "").strip() or None, None, 0, 0, px, False)); subtotal += px; costo += OPC["OPC-PERSO"]["costo"] or 0
    descuento = float(f.get("descuento") or 0); canal = f.get("canal") or "whatsapp"
    iva = round((subtotal - descuento) * 0.16, 2) if (canal == "cashea" or f.get("factura") == "1") else 0
    delivery = float(f.get("delivery") or 0); total = round(subtotal - descuento + iva + delivery, 2)
    ultimo = con.execute("SELECT MAX(CAST(substr(numero,2) AS INTEGER)) FROM ordenes").fetchone()[0] or 0
    hoy_d = datetime.date.today()
    fecha_auto = f.get("fecha_prometida") or hoy_d.isoformat()
    # Pagos (puede ser mixto): forma + monto + referencia por línea
    pagos_in = [(fo, float(mo or 0), re_) for fo, mo, re_ in zip(f.getlist("pago_forma"), f.getlist("pago_monto"), f.getlist("pago_ref")) if fo and float(mo or 0) > 0]
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
            dprep = max(packs_deliv_por_linea.get(li, 1) - max(ini, 1), 0)   # deliveries pagados hoy para retiros futuros
            for _ in range(int(c)):
                con.execute("INSERT INTO packs (cliente_id,orden_id,producto_id,tamano,unidades,entregadas_inicio,estado,creado_en,deliveries_prepagados) VALUES (?,?,?,?,?,?,?,datetime('now','localtime'),?)",
                            (cid, oid, p["id"], "Grande" if p["sku"].endswith("G") else ("Mediano" if p["sku"].endswith("M") else None), unid, ini, "completo" if ini >= unid else "activo", dprep))
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
    digital = sum(m for fo, m, _ in pagos_in if not fo.startswith("Efectivo"))
    efectivo = sum(m for fo, m, _ in pagos_in if fo.startswith("Efectivo"))
    for fo, m, ref in pagos_in:
        if fo.startswith("Efectivo"): continue  # el efectivo se registra al entregar
        con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,tasa,cuenta,referencia,fecha,estado,confirmado_por,confirmado_en) VALUES (?,?,?,?,?,?,?,?,?,'confirmado',?,?)",
                    (oid, fo, m, round(m * tasa_v, 2) if es_bolivares(fo) else m, "VES" if es_bolivares(fo) else "USD", tasa_v if es_bolivares(fo) else None, FORMA_CUENTA.get(fo), ref or None, ahora, uid, ahora))
    # Cashea se trata como cualquier canal: si Cristina marca el pago completo, la orden queda pagada.
    # El seguimiento de las cuotas de Cashea queda para más adelante.
    if digital >= total - 0.01: ep = "pagada"
    elif digital + efectivo >= total - 0.01: ep = "contra_entrega"
    else: ep = estado_pago_de(digital, total)
    con.execute("UPDATE ordenes SET estado_pago=?, monto_contra_entrega=? WHERE id=?", (ep, round(efectivo, 2) if ep == "contra_entrega" else 0, oid))
    if len(pagos_in) > 1: registrar(con, oid, uid, "pago", "Pago mixto: " + ", ".join(f"{fo} {fmt_usd(m)}" for fo, m, _ in pagos_in))
    actualizar_porche_cliente(con, oid); fijar_pago_despachador(con, oid)
    con.commit(); return RedirectResponse(f"/ordenes?abrir={oid}", status_code=303)


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
@app.get("/operaciones", response_class=HTMLResponse)
def operaciones(request: Request, cola: str = "hoy", tipo: str = "", agencia: str = "", dia: str = "", desp: str = "", vista: str = "tipo", q: str = "", con=Depends(db)):
    rol = rol_de(request); hoy_d = datetime.date.today(); hoy = hoy_d.isoformat(); manana = (hoy_d + datetime.timedelta(days=1)).isoformat()
    activas = cargar_ordenes(con, {"estado": "activas"}, rol)
    con_saldo = cargar_ordenes(con, {"estado": "con_saldo"}, rol) if "ver_dinero" in PERMISOS[rol] else []
    for o in con_saldo:
        if o["id"] not in {a["id"] for a in activas}: activas.append(o)
    for o in activas:
        o["nota_log"] = o["notas_entrega"] or (con.execute("SELECT texto FROM notas_cliente WHERE cliente_id=? AND mostrar_logistica=1 LIMIT 1", (o["cliente_id"],)).fetchone() or [None])[0]
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
                              fecha_op=k["fecha_programada"], fecha_prometida=k["fecha_programada"], productos=f"{k['retiro_programado'] or 1}× Repuesto {k['tamano'] or ''} · pack de {k['unidades']}, le quedan {k['saldo']}", nota_log=k["nota_programada"], tipo_entrega=te, franja=None,
                              receptor_nombre=None, agencia=None, guia=None, distribuidor=None, despachador=k["despachador_programado"], ciudad=(d["ciudad"] if d else k["ciudad"]), zona=None,
                              direccion=(d["direccion"] if d else None), maps=(d["maps"] if d else None), estado_pago=("pagada" if (not k["delivery_programado"] or k["delivery_pagado"]) else "contra_entrega"), estado="pendiente", coordinada=bool(k["despachador_programado"] or te == "pickup"), total=k["delivery_programado"] or 0, pagado=0, monto_contra_entrega=(k["delivery_programado"] if (k["delivery_programado"] and not k["delivery_pagado"]) else None), telefono=k["telefono"]))
    if cola in ("hoy", "manana", "dia", "todo", "sin_coordinar") and not desp:
        dia_ref = {"hoy": hoy, "manana": manana, "dia": dia}.get(cola)
        for r in cargar_prepagados(con):
            if not r["fecha_programada"]: continue
            if cola == "hoy" and r["fecha_programada"] > hoy: continue
            if cola in ("manana", "dia") and r["fecha_programada"] != dia_ref: continue
            if cola == "sin_coordinar" and (r["tipo_entrega"] not in ("delivery", "delivery_fuera") or r["despachador"]): continue
            if tipo and r["tipo_entrega"] != tipo: continue
            lista.append(dict(id=None, es_prepagado=True, rid=r["id"], cliente=r["cliente"], cliente_id=r["cliente_id"], numero=r["orden"] or "", alertas=[], incidencias=0,
                              fecha_op=r["fecha_programada"], fecha_prometida=r["fecha_programada"], productos=f"1× Repuesto {r['tamano'] or ''} · prepagado", nota_log=r["notas"], tipo_entrega=r["tipo_entrega"], franja=None,
                              receptor_nombre=None, agencia=r["agencia"], guia=None, distribuidor=None, despachador=r["despachador"], ciudad=r["ciudad"], zona=None, direccion=r["direccion"], maps=r["maps"],
                              estado_pago="pagada", estado=("en_ruta" if r["en_ruta"] else "pendiente"),
                              coordinada=bool(r["despachador"] or r["agencia"] or r["tipo_entrega"] == "pickup"), total=0, pagado=0, monto_contra_entrega=None, telefono=r["telefono"],
                              delivery=r["delivery"], delivery_pagado=r["delivery_pagado"]))   # para preguntar si cobró el delivery al entregar
    lista.sort(key=lambda o: (o["fecha_op"], o["franja"] or "", o["id"] or 0))
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
            por_desp.setdefault(o["despachador"], []).append(resumen_despacho(cargar_orden(con, o["id"])))
    # envío nacional: pedidos que todavía nadie ha llevado a la oficina de la agencia
    por_llevar = con.execute("""SELECT o.id, o.numero, o.agencia, o.ciudad, o.estado, c.nombre cliente,
                                       COALESCE(o.fecha_prometida, substr(o.creado_en,1,10)) fecha
                                FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id
                                WHERE o.tipo_entrega='nacional' AND o.viaje_id IS NULL AND o.origen_excel=0
                                  AND o.estado IN ('pendiente','en_ruta') ORDER BY o.agencia, o.id""").fetchall()
    por_agencia = {}
    for o in por_llevar: por_agencia.setdefault(o["agencia"] or "", []).append(o)
    por_agencia = dict(sorted(por_agencia.items(), key=lambda kv: (kv[0] == "", kv[0])))
    viajes = con.execute("""SELECT v.*, (SELECT COUNT(*) FROM ordenes o WHERE o.viaje_id=v.id) n_ordenes
                            FROM viajes_agencia v WHERE v.pagado=0 ORDER BY v.fecha DESC, v.id DESC""").fetchall()
    tarifas_ag = {a: tarifa_agencia(con, a) for a in AGENCIAS}
    return render(request, "operaciones.html", seccion="operaciones", grupos=grupos, cola=cola, colas=colas, conteos=conteos, por_desp=por_desp, total=len(lista), hoy_iso=hoy, manana_iso=manana,
                  por_llevar=por_llevar, por_agencia=por_agencia, viajes=viajes, tarifas_ag=tarifas_ag,
                  tipo=tipo, agencia=agencia, conteos_tipo=conteos_tipo, conteos_ag=conteos_ag, dia=dia, desp=desp, despachadores=despachadores, conteos_desp=conteos_desp, vista=vista, fecha_larga=fecha_larga, q=q)


# ------------------------------------------------------------------ FINANZAS (solo Cristina)
# forma de pago → caja donde cae la plata
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


def cargar_formas_pago():
    """Las formas de pago son las cajas: así nunca falta una ni sobra una que ya no usas."""
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    cajas = [r["nombre"] for r in con.execute("SELECT nombre FROM cuentas WHERE activa=1 ORDER BY orden")]
    con.close()
    if cajas:
        FORMAS_PAGO[:] = cajas
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
def cashflow(request: Request, caja: str = "", mes: str = "", con=Depends(db)):
    """La caja del negocio arriba, las inversiones y lo personal aparte, y registrar una entrada o salida en dos clics."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    cs = saldos(con)
    lineas = libro_caja(con, int(caja) if caja else None, mes or None)
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
    equipo = cfg_json(con, "equipo", ["Víctor", "Isaías", "Manawa"])   # la subcategoría dice qué le pagaste; aquí va quién
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
    return render(request, "cashflow.html", seccion="cashflow", cuentas=cs, activas=activas,
                  efectivo_pend=efectivo_por_registrar(con),
                  lineas=lineas[:300], caja=caja, mes=mes, meses=meses, total=total, arcos=arcos, TIPOS_MOV=TIPOS_MOV,
                  cats=cats, cats_ent=cats_ent, provs=provs, a_quien=A_QUIEN, a_quien_ent=A_QUIEN_ENT, de_quien=DE_QUIEN, orden_ent=[k for k in ORDEN_ENT if k in cats_ent])


@app.post("/cashflow/linea")
async def cashflow_linea(request: Request, con=Depends(db)):
    """Una línea del libro. Todo se registra en dólares: la caja es solo la forma de pago."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    f = await request.form(); uid = usuario_id(rol_de(request))
    fecha = f.get("fecha") or datetime.date.today().isoformat()
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
    else:
        grande = 1 if f.get("compra_grande") else 0
        con.execute("""INSERT INTO gastos (fecha, monto_usd, monto_real, moneda, categoria, subcategoria, descripcion, proveedor,
                       cantidad, unidad, comprobante, notas, cuenta_id, compra_grande, usuario_id)
                       VALUES (?,?,?,'USD',?,?,?,?,?,?,?,?,?,?,?)""",
                    (fecha, monto, monto, categoria, subcategoria, g("descripcion"), g("proveedor"),
                     cifra(f.get("cantidad")) or None, g("unidad"), g("comprobante"), g("notas"), caja, grande, uid))
    con.commit(); return RedirectResponse("/cashflow", status_code=303)


@app.get("/cashflow/cajas", response_class=HTMLResponse)
def cajas_config(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    return render(request, "cajas.html", seccion="cashflow", cuentas=saldos(con))


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
    cajas = sorted([c for c in cs if c["tipo"] == "operativa" and (c["saldo"] or c["activa"])], key=lambda c: -c["saldo"])
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
                           caja=por_id.get(g["cuenta_id"]), entrada=0, salida=g["monto_usd"], ref=("gasto", g["id"]),
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


@app.get("/cashflow/libro", response_class=HTMLResponse)
def libro(request: Request, caja: str = "", mes: str = "", con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    cs = saldos(con)
    lineas = libro_caja(con, int(caja) if caja else None, mes or None)
    meses = sorted({l["fecha"][:7] for l in libro_caja(con)}, reverse=True)
    return render(request, "libro.html", seccion="cashflow", lineas=lineas[:500], cuentas=cs, caja=caja, mes=mes, meses=meses, total=sum(c["saldo"] for c in cs if c["tipo"] == "operativa"))


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


EMPRESA_CAMPOS = [("razon_social", "Razón social"), ("rif", "RIF"), ("direccion_fiscal", "Domicilio fiscal"),
                  ("registro", "Registro mercantil"), ("constitucion", "Fecha de constitución"),
                  ("contador", "Contador"), ("contador_tel", "Teléfono del contador")]


@app.get("/configuracion", response_class=HTMLResponse)
def configuracion(request: Request, con=Depends(db), ok: str = "", err: str = ""):
    """Todo lo que Cristina puede cambiar sin pedirlo: reglas, sueldos, datos de la empresa y el respaldo."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    def val(k, d=""):
        r = con.execute("SELECT valor FROM config WHERE clave=?", (k,)).fetchone()
        return r[0] if r and r[0] is not None else d
    eq = cfg_json(con, "equipo", ["Víctor", "Isaías", "Manawa"])
    sue = cfg_json(con, "sueldos", {}) or {}
    return render(request, "configuracion.html", seccion="configuracion",
                  empresa=cfg_json(con, "empresa", {}) or {}, EMPRESA_CAMPOS=EMPRESA_CAMPOS,
                  documentos=cfg_json(con, "documentos", []) or [],
                  usuarios=con.execute("SELECT * FROM usuarios WHERE rol!='sistema' ORDER BY activo DESC, rol, nombre").fetchall(),
                  ROLES=ROLES, yo=quien_es(request),
                  despachadores_l=[r["nombre"] for r in con.execute("SELECT nombre FROM despachadores WHERE activo=1 ORDER BY nombre")],
                  equipo=eq, sueldos=sue, ciclo=CICLO_REPUESTO, iva=round(IVA * 100, 2),
                  clave=val("clave_resultados"), ventas_auto=val("ventas_auto", "0") == "1",
                  cashflow_desde=val("cashflow_desde"), tarifa_agencia=cfg_json(con, "tarifa_agencia", {}) or {},
                  respaldos=lista_respaldos(), ok=ok, err=err)


def lista_respaldos():
    """Los respaldos que hay en iCloud: cuántos son y cuándo fue el último."""
    d = Path(os.path.expanduser("~/Library/CloudStorage")).glob("iCloudDrive*/Decopet respaldos")
    for carpeta in d:
        f = sorted(carpeta.glob("decopet-2*.db"), key=lambda x: x.stat().st_mtime, reverse=True)
        if f:
            t = datetime.datetime.fromtimestamp(f[0].stat().st_mtime)
            return {"n": len(f), "ultimo": t.strftime("%d/%m/%Y %H:%M"), "ts": t,
                    "dias": (datetime.date.today() - t.date()).days, "carpeta": str(carpeta)}
        return {"n": 0, "ultimo": None, "dias": None, "carpeta": str(carpeta)}
    return {"n": 0, "ultimo": None, "dias": None, "carpeta": None}


DOCS = BASE / "data" / "documentos"
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


ROLES = {"admin": "Administradora · lo ve todo", "logistica": "Logística · órdenes y clientes, sin dinero",
         "taller": "Taller · solo su pantalla", "despachador": "Despachador · solo sus entregas"}


@app.post("/configuracion/usuario")
def usuario_guardar(request: Request, id: int = Form(0), nombre: str = Form(""), usuario: str = Form(""),
                    rol: str = Form("logistica"), despachador: str = Form(""), clave: str = Form(""),
                    activo: str = Form(""), borrar: str = Form(""), con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    yo = quien_es(request)
    if borrar and id:
        if yo and yo["id"] == id: return RedirectResponse("/configuracion?err=yo", status_code=303)
        con.execute("UPDATE usuarios SET activo=0 WHERE id=?", (id,))   # nunca se borra: el historial lo nombra
        con.execute("DELETE FROM sesiones WHERE usuario_id=?", (id,)); con.commit()
        return RedirectResponse("/configuracion?ok=usuario", status_code=303)
    u = (usuario or "").strip().lower()
    if not (nombre.strip() and u): return RedirectResponse("/configuracion?err=usuario", status_code=303)
    otro = con.execute("SELECT id FROM usuarios WHERE lower(TRIM(usuario))=? AND id!=?", (u, id or 0)).fetchone()
    if otro: return RedirectResponse("/configuracion?err=repetido", status_code=303)
    if clave.strip() and len(clave.strip()) < 6: return RedirectResponse("/configuracion?err=corta", status_code=303)
    if id:
        con.execute("UPDATE usuarios SET nombre=?, usuario=?, rol=?, despachador=?, activo=? WHERE id=?",
                    (nombre.strip(), u, rol, despachador.strip() or None, 1 if activo else 0, id))
        if not activo: con.execute("DELETE FROM sesiones WHERE usuario_id=?", (id,))
    else:
        cur = con.execute("INSERT INTO usuarios (nombre,usuario,rol,despachador,activo,creado_en) VALUES (?,?,?,?,1,date('now'))",
                          (nombre.strip(), u, rol, despachador.strip() or None))
        id = cur.lastrowid
    if clave.strip():
        con.execute("UPDATE usuarios SET clave_hash=? WHERE id=?", (cifrar_clave(clave.strip()), id))
        con.execute("DELETE FROM sesiones WHERE usuario_id=? AND ficha!=?", (id, request.cookies.get("sesion") or ""))
    con.commit(); return RedirectResponse("/configuracion?ok=usuario", status_code=303)


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
        t = {}
        for k in f.keys():
            if k.startswith("ag_") and (f.get(k) or "").strip():
                t[k[3:]] = float(cifra(f.get(k)) or 0)
        if t: poner("tarifa_agencia", json.dumps(t, ensure_ascii=False))
    elif bloque == "empresa":
        poner("empresa", json.dumps({k: (f.get(k) or "").strip() for k, _ in EMPRESA_CAMPOS}, ensure_ascii=False))
    elif bloque == "equipo":
        # nombre y sueldo van emparejados por posición, no por el nombre: "Isaías" lleva acento
        # y usarlo como nombre de campo perdía su sueldo al guardar.
        crudos = f.getlist("nombre"); montos = f.getlist("sueldo")
        nombres, sue = [], {}
        for i, n in enumerate(crudos):
            n = (n or "").strip()
            if not n: continue
            nombres.append(n)
            v = cifra(montos[i] if i < len(montos) else "")
            if v: sue[n] = v
        if nombres:
            poner("equipo", json.dumps(nombres, ensure_ascii=False))
            poner("sueldos", json.dumps(sue, ensure_ascii=False))
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


def _resultados_meses(con, anio):
    """Las filas de Resultados. Antes de la fecha de arranque manda el historial del Excel; desde ahí lo calcula el ERP."""
    desde = finanzas_desde(con)   # Finanzas solo mira órdenes desde la fecha que Cristina active; antes, el historial
    # una venta cuenta el día que entró la plata (fecha_pago); si todavía no han pagado, el día que se hizo la orden
    DIA_VENTA = "substr(COALESCE(NULLIF(o.fecha_pago,''), o.creado_en),1,10)"
    vivo = con.execute(f"""SELECT substr({DIA_VENTA},1,7) m, SUM(o.total) facturacion, COUNT(*) n
                           FROM ordenes o WHERE o.estado!='cancelada' AND o.origen_excel=0
                           AND ? IS NOT NULL AND {DIA_VENTA}>=? AND substr({DIA_VENTA},1,4)=? GROUP BY 1""", (desde, desde, anio)).fetchall()
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
    meses = []
    for m in sorted(set(vivos) | set(hist)):
        h, v = hist.get(m), vivos.get(m)
        usar_hist = h and (not desde or m < desde[:7])   # desde que el ERP está activo, el mes se calcula solo
        if usar_hist:
            meses.append({"m": m, "facturacion": h["facturacion"] or 0, "unidades": h["unidades"] or 0, "gastos": h["gastos"] or 0,
                          "sueldo": h["sueldo"] or 0, "grandes": h["arrastre"] or 0, "n": 0, "historial": True,
                          "nota": h["nota"] or "", "contexto": h["contexto"] or ""})
        elif v:
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
    return render(request, "finanzas.html", seccion="finanzas", meses=meses, anio=anio, anios=anios, por_cat=por_cat, historial=historial, cashflow_desde=desde, MESES_N=MESES_N)


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
                (oid, "BNC", monto, round(monto * (tasa or 0), 2), "VES", tasa, FORMA_CUENTA["BNC"], referencia or "Cuota Cashea", f + " 12:00", usuario_id(rol_de(request)), f + " 12:00"))
    o = con.execute("SELECT total, (SELECT COALESCE(SUM(monto_usd),0) FROM pagos p WHERE p.orden_id=ordenes.id AND p.estado='confirmado') cobrado FROM ordenes WHERE id=?", (oid,)).fetchone()
    if o["cobrado"] >= o["total"] - 0.01: con.execute("UPDATE ordenes SET estado_pago='pagada' WHERE id=?", (oid,))
    registrar(con, oid, usuario_id(rol_de(request)), "pago", f"Cuota Cashea liquidada {fmt_usd(monto)} → BNC"); con.commit()
    return RedirectResponse("/finanzas/cashea", status_code=303)


@app.get("/finanzas/gastos", response_class=HTMLResponse)
def gastos(request: Request, mes: str = "", categoria: str = "", vista: str = "semana", semana: str = "", anio: str = "", con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    hoy_d = datetime.date.today()
    if anio and not mes: mes = f"{anio}-{hoy_d.month:02d}" if anio == str(hoy_d.year) else f"{anio}-01"
    mes = mes or hoy_d.strftime("%Y-%m"); anio = mes[:4]
    tot_mes = {r[0]: r[1] for r in con.execute("SELECT substr(fecha,6,2), SUM(monto_usd) FROM gastos WHERE substr(fecha,1,4)=? GROUP BY 1", (anio,))}
    anios = sorted({r[0] for r in con.execute("SELECT DISTINCT substr(fecha,1,4) FROM gastos")} | {str(hoy_d.year)}, reverse=True)
    sql = "SELECT g.*, cu.nombre cuenta, u.nombre usuario FROM gastos g LEFT JOIN cuentas cu ON cu.id=g.cuenta_id LEFT JOIN usuarios u ON u.id=g.usuario_id WHERE substr(g.fecha,1,7)=?"; args = [mes]
    if categoria: sql += " AND g.categoria=?"; args.append(categoria)
    rows = [dict(r) for r in con.execute(sql + " ORDER BY g.fecha DESC, g.id DESC", args)]
    # semana del mes (1..5) para agrupar como en el Excel de Cristina
    semanas = {}
    for r in rows:
        d = datetime.date.fromisoformat(r["fecha"]); n = (d.day - 1) // 7 + 1
        semanas.setdefault(n, {"n": n, "desde": d.replace(day=(n - 1) * 7 + 1), "gastos": [], "total": 0.0})
        semanas[n]["gastos"].append(r); semanas[n]["total"] += r["monto_usd"]
    tot_semanas = {k: v["total"] for k, v in semanas.items()}
    if semana: semanas = {k: v for k, v in semanas.items() if str(k) == semana}; rows = [r for r in rows if str((datetime.date.fromisoformat(r["fecha"]).day - 1) // 7 + 1) == semana]
    semanas = [semanas[k] for k in sorted(semanas, reverse=True)]
    por_cat = con.execute("SELECT categoria, SUM(monto_usd) monto, COUNT(*) n FROM gastos WHERE substr(fecha,1,7)=? GROUP BY 1 ORDER BY 2 DESC", (mes,)).fetchall()
    cats = json.loads(con.execute("SELECT valor FROM config WHERE clave='categorias_gasto'").fetchone()[0])
    cuentas = con.execute("SELECT * FROM cuentas WHERE activa=1 ORDER BY orden").fetchall()
    meses = [r[0] for r in con.execute("SELECT DISTINCT substr(fecha,1,7) FROM gastos ORDER BY 1 DESC")]
    if mes not in meses: meses.insert(0, mes)
    return render(request, "gastos.html", seccion="gastos", gastos=rows, semanas=semanas, por_cat=por_cat, total=sum(r["monto"] for r in por_cat), cats=cats, cuentas=cuentas, mes=mes, meses=meses, tasa=tasa_hoy(con), categoria=categoria, vista=vista,
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
                 f.get("descripcion") or None, f.get("proveedor") or None, int(f["cuenta_id"]) if f.get("cuenta_id") else None, 1 if f.get("recurrente") else 0, f.get("notas") or None, usuario_id(rol_de(request)),
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

    # 2 · lo pagado a proveedores debe existir como gasto
    huerf = con.execute("SELECT COUNT(*) FROM abonos_produccion WHERE gasto_id IS NULL").fetchone()[0]
    chequeo("Cada pago a proveedor tiene su gasto", huerf == 0, f"{huerf} pagos sin gasto" if huerf else "todos con gasto")

    # 3 · lo cobrado en una orden no puede pasarse del total
    mal = [f"#{r['numero']}" for r in con.execute("""SELECT o.numero FROM ordenes o WHERE o.estado!='cancelada'
             AND COALESCE((SELECT SUM(monto_usd) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado'),0) > o.total + 0.01""")]
    chequeo("Ninguna orden tiene cobrado más de su total", not mal, ", ".join(mal[:8]) or "todas correctas")

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

    # 6 · el respaldo — mismo dato que muestra Configuración, para que no haya dos verdades
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
    filas = [(m["nombre"], m["raza"], m["cumple"], _fecha(m["fecha_nacimiento"]), m["peso"], m["cliente"], m["telefono"], m["ciudad"])
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
    cols = [("Fecha", 12, "f"), ("Categoría", 22, ""), ("Subcategoría", 22, ""), ("Qué fue", 32, ""), ("Pagado a", 20, ""),
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
                 concepto + (f" · {f['nota'].strip()}" if (f.get("nota") or "").strip() else ""), None, usuario_id(rol_de(request))))
    con.commit(); return RedirectResponse("/cashflow", status_code=303)


# ------------------------------------------------------------------ HISTORIAL DE VENTAS
MESES_N = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

def _historial_rows(con, anio, mes, q):
    """Registro de ventas = histórico del Excel + órdenes de la plataforma (y las de Airtable que Cristina cruce).
    Reglas de Cristina: el delivery va dentro de la facturación del producto si se pagó con la misma forma; solo va en línea aparte
    si se pagó con otra forma de pago. Dentro del mismo día, el orden es el de llegada (como en su Excel)."""
    args = []; cond = ""
    if anio: cond += " AND substr(fecha,1,4)=?"; args.append(anio)
    if mes: cond += " AND substr(fecha,6,2)=?"; args.append(f"{int(mes):02d}")
    if q: cond += " AND (cliente LIKE ? OR producto LIKE ? OR numero LIKE ?)"; args += [f"%{q}%"] * 3
    sql = f"""SELECT * FROM (
        SELECT NULL oid, '' numero, fecha, cliente, producto, precio, cantidad, facturacion linea, forma_pago forma, NULL color, 0 malla, NULL personalizacion, 'excel' origen, fila_excel llegada, 0 lid
          FROM registro_ventas
        UNION ALL
        SELECT o.id oid, o.numero, substr(o.creado_en,1,10) fecha, c.nombre cliente, l.nombre producto, l.precio, l.cantidad, l.total linea, o.forma_pago_prevista forma, l.color, l.malla, l.personalizacion, 'orden' origen,
               1000000 + CAST(substr(o.numero,2) AS INTEGER) llegada, l.id lid
          FROM ordenes o JOIN clientes c ON c.id=o.cliente_id JOIN orden_lineas l ON l.orden_id=o.id WHERE o.estado!='cancelada' AND (o.origen_excel=0 OR COALESCE(o.en_registro,0)=1)
      ) WHERE 1=1 {cond} ORDER BY fecha DESC, llegada DESC, lid DESC"""   # lo último registrado, primero (el Excel al revés)
    out = []
    for r in con.execute(sql, args):
        d = dict(r)
        try:
            f = datetime.date.fromisoformat(d["fecha"]); d["semana"] = (f.day - 1) // 7 + 1; d["mes"] = MESES_N[f.month - 1].capitalize()   # semana del mes, como en su Excel
        except Exception:
            d["semana"] = ""; d["mes"] = ""
        out.append(d)
    # delivery de las órdenes: dentro del producto (misma forma de pago) o línea aparte (otra forma)
    deliv = {r["id"]: dict(r) for r in con.execute("""SELECT o.id, o.delivery, o.forma_pago_prevista forma,
                 (SELECT GROUP_CONCAT(DISTINCT p.forma) FROM pagos p WHERE p.orden_id=o.id AND p.estado='confirmado') formas
                 FROM ordenes o WHERE COALESCE(o.delivery,0)>0 AND o.estado!='cancelada' AND (o.origen_excel=0 OR COALESCE(o.en_registro,0)=1)""")}
    res = []; ya = set()
    for d in out:
        res.append(d)
        oid = d["oid"]
        if oid in deliv and oid not in ya and d["origen"] == "orden":
            ya.add(oid); dv = deliv[oid]; formas = (dv["formas"] or "").split(",")
            if len([x for x in formas if x]) <= 1:   # una sola forma de pago: el delivery va dentro de la facturación
                d["linea"] = round((d["linea"] or 0) + dv["delivery"], 2)
            else:   # varias formas: línea aparte con la última forma
                res.append(dict(d, producto="Delivery", precio=dv["delivery"], cantidad=0, linea=dv["delivery"], forma=formas[-1], color=None, malla=0, personalizacion=None))
    return res


@app.get("/historial", response_class=HTMLResponse)
def historial(request: Request, anio: str = "", mes: str = "", semana: str = "", q: str = "", con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)   # el registro de ventas es dinero
    if not resultados_abierto(request, con):   # lleva la misma clave que Resultados
        return render(request, "clave.html", seccion="historial", titulo="Registro de ventas",
                      texto="Todas las ventas registradas desde 2023.", volver="/historial",
                      mal=request.query_params.get("mal"))
    con_datos = {r[0] for r in con.execute("SELECT DISTINCT substr(creado_en,1,4) FROM ordenes")} | {r[0] for r in con.execute("SELECT DISTINCT substr(fecha,1,4) FROM registro_ventas")}
    anios = [str(a) for a in range(datetime.date.today().year, 2022, -1)]
    for a in sorted(con_datos - set(anios), reverse=True): anios.append(a)
    todos = anio == "todos"
    if todos: anio = ""
    elif not anio and not q: anio = str(datetime.date.today().year)
    rows = _historial_rows(con, anio, mes, q)
    if semana and mes: rows = [r for r in rows if str(r["semana"]) == semana]   # semana del mes (1–5), como en su Excel
    tot = sum(r["linea"] or 0 for r in rows)
    ordenes_ids = rows            # "Ventas" = líneas, igual que las filas de su Excel
    unidades = sum(r["cantidad"] or 0 for r in rows)
    # ticket promedio por PEDIDO (un cliente en un día = un pedido), no por línea: el delivery no cuenta como compra aparte
    pedidos = {(r["oid"] if r["oid"] else (r["fecha"], (r["cliente"] or "").lower())) for r in rows}
    ticket = tot / len(pedidos) if pedidos else 0
    por_mes = []
    return render(request, "historial.html", seccion="historial", mes_actual=str(datetime.date.today().month), rows=rows[:2000], total=tot, n_ordenes=len(ordenes_ids), unidades=unidades,
                  anio=anio, mes=mes, semana=semana, q=q, anios=anios, por_mes=por_mes, meses=MESES_N, truncado=len(rows) > 2000, todos=todos, hoy_anio=str(datetime.date.today().year), ticket=ticket, n_pedidos=len(pedidos))


@app.get("/historial/exportar")
def historial_exportar(anio: str = "", mes: str = "", semana: str = "", q: str = "", con=Depends(db)):
    import csv, io
    from fastapi.responses import StreamingResponse
    rows = _historial_rows(con, anio, mes, q)
    if semana and mes: rows = [r for r in rows if str(r["semana"]) == semana]
    buf = io.StringIO(); w = csv.writer(buf, delimiter=";")
    w.writerow(["Fecha", "Semana", "Mes", "Cliente", "Producto", "Precio", "Cantidad", "Facturación", "Forma de pago"])
    for r in rows:
        w.writerow([r["fecha"], r["semana"], r["mes"], r["cliente"], r["producto"] + (f" · plato {r['color']}" if r["color"] else "") + (" + malla" if r["malla"] else ""), r["precio"], int(r["cantidad"] or 1), r["linea"], r["forma"] or ""])
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
    if ver != "todos" and not q: lista = [d for d in lista if d["situacion"] == {"toca": "toca repuesto", "aldia": "al día", "inactivos": "inactivo", "sin_historial": "sin historial"}[ver]]
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
        ext = (a.filename.rsplit(".", 1)[-1] or "jpg").lower()[:5]; rosado = f.get("variante") == "rosado"
        nombre = f"producto-{pid}{'-rosado' if rosado else ''}.{ext}"
        (BASE / "data" / "fotos" / nombre).write_bytes(await a.read())
        con.execute(f"UPDATE productos SET {'foto_rosado' if rosado else 'foto'}=? WHERE id=?", (nombre, pid)); con.commit()
    return RedirectResponse("/productos", status_code=303)


@app.get("/inventario", response_class=HTMLResponse)
def inventario(request: Request, con=Depends(db)):
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
    prods = filas
    movs = con.execute("SELECT m.*, p.nombre producto, u.nombre usuario FROM mov_inventario m JOIN productos p ON p.id=m.producto_id LEFT JOIN usuarios u ON u.id=m.usuario_id ORDER BY m.id DESC LIMIT 40").fetchall()
    return render(request, "inventario.html", seccion="inventario", productos=prods, movs=movs)


@app.post("/inventario/mov")
def inventario_mov(request: Request, producto_id: int = Form(...), tipo: str = Form(...), cantidad: int = Form(...),
                   nota: str = Form(""), fecha: str = Form(""), color: str = Form(""), con=Depends(db)):
    q = abs(cantidad) if tipo == "entrada" else (-abs(cantidad) if tipo == "salida" else cantidad)
    con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, nota, color, usuario_id) VALUES (?,?,?,?,?,?,?)",
                (producto_id, fecha or datetime.date.today().isoformat(), tipo, q, nota or None, (color or "").lower() or None, usuario_id(rol_de(request))))
    con.commit(); return RedirectResponse("/inventario", status_code=303)


def descontar_inventario(con, oid, uid):
    """Al crear una orden sale del inventario el producto y, si tiene receta, los materiales que consume
    (un Porche PRO se lleva una caja de madera, una placa y una caja de cartón)."""
    hoy = datetime.date.today().isoformat()
    for l in con.execute("SELECT l.*, p.sku, p.nombre, p.categoria FROM orden_lineas l JOIN productos p ON p.id=l.producto_id WHERE l.orden_id=?", (oid,)).fetchall():
        cant = int(l["cantidad"])
        armado = l["categoria"] in ("porche", "repuesto")   # se arma el mismo día: no tiene stock propio
        # …salvo que el taller haya adelantado trabajo: si hay porches ya armados, la venta sale de ahí
        listos = con.execute("SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario WHERE producto_id=?", (l["producto_id"],)).fetchone()[0] if armado else 0
        de_listos = min(cant, max(int(listos), 0))
        if not armado or de_listos:
            con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, orden_id, color, nota, usuario_id) VALUES (?,?,?,?,?,?,?,?)",
                        (l["producto_id"], hoy, "salida", -(de_listos if armado else cant), oid, (l["color"] or "").lower() or None,
                         "ya estaba armado" if armado else None, uid))
        resto = cant - de_listos   # lo que no estaba armado se arma ahora y gasta sus materiales
        for r in con.execute("""SELECT r.insumo_id, r.cantidad, i.nombre FROM receta r JOIN productos i ON i.id=r.insumo_id
                                WHERE r.producto_id=?""", (l["producto_id"],)):
            if resto <= 0: break
            con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, orden_id, nota, usuario_id) VALUES (?,?,?,?,?,?,?)",
                        (r["insumo_id"], hoy, "salida", -int(r["cantidad"] * resto), oid, f"para {resto}× {l['nombre']}", uid))
        if l["malla"]:
            m = con.execute("SELECT id FROM productos WHERE sku='INS-MALLA'").fetchone()
            if m: con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, orden_id, nota, usuario_id) VALUES (?,?,?,?,?,?,?)", (m["id"], hoy, "salida", -cant, oid, "malla agregada al porche", uid))


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
    lista_js = [dict(archivo=r["archivo"], producto=r["producto"], tipo=r["tipo"], etiqueta=r["etiqueta"]) for r in rows]
    return render(request, "galeria.html", seccion="galeria", fotos=rows, producto=producto, tipo=tipo, conteos=conteos, prods=prods, total=sum(conteos.values()), lista_js=lista_js, grupos=grupos)


@app.post("/galeria/subir")
async def galeria_subir(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/galeria", status_code=303)
    f = await request.form(); pid = int(f["producto_id"]); tipo = f.get("tipo") or "sin_fondo"
    for a in f.getlist("archivo"):
        if not getattr(a, "filename", None): continue
        ext = (a.filename.rsplit(".", 1)[-1] or "jpg").lower()[:5]
        nombre = f"{pid}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}-{abs(hash(a.filename)) % 100000}.{ext}"
        (BASE / "data" / "fotos" / "productos" / nombre).write_bytes(await a.read())
        con.execute("INSERT INTO producto_fotos (producto_id, archivo, tipo, etiqueta) VALUES (?,?,?,?)", (pid, nombre, tipo, f.get("etiqueta") or None))
    con.commit(); return RedirectResponse(f"/galeria?producto={pid}", status_code=303)


@app.post("/galeria/{fid}/borrar")
def galeria_borrar(request: Request, fid: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/galeria", status_code=303)
    r = con.execute("SELECT * FROM producto_fotos WHERE id=?", (fid,)).fetchone()
    if r:
        con.execute("DELETE FROM producto_fotos WHERE id=?", (fid,))
        if not con.execute("SELECT 1 FROM producto_fotos WHERE archivo=?", (r["archivo"],)).fetchone():   # el mismo archivo puede servir a varios productos
            try: (BASE / "data" / "fotos" / "productos" / r["archivo"]).unlink()
            except FileNotFoundError: pass
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
    # El único piso es desde cuándo está en uso el ERP, para no arrastrar cosas de antes.
    desde = finanzas_desde(con)
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
                        "unidad": c["unidad"], "precio_unitario": c["precio_unitario"]})
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


def ficha_equipo(con, nombre, hoy):
    """Lo que le has pagado a una persona del equipo, y qué adelantos quedan por descontar."""
    mensual = (cfg_json(con, "sueldos", {}) or {}).get(nombre)   # el sueldo se guarda por mes; se paga en dos quincenas
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
    # las faltas se descuentan igual que los adelantos: solo las de esta quincena
    faltas = con.execute("""SELECT * FROM faltas WHERE nombre=? AND fecha>? ORDER BY fecha DESC""",
                         (nombre, ult_q or "0000")).fetchall()
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
                (nombre, fecha or datetime.date.today().isoformat(), nota.strip() or None, usuario_id(rol_de(request))))
    con.commit(); return RedirectResponse("/equipo", status_code=303)


@app.post("/equipo/falta/{fid}/borrar")
def equipo_falta_borrar(request: Request, fid: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    con.execute("DELETE FROM faltas WHERE id=?", (fid,)); con.commit()
    return RedirectResponse("/equipo", status_code=303)


@app.get("/equipo", response_class=HTMLResponse)
def equipo(request: Request, con=Depends(db)):
    """Isaías, Manawa y Víctor: lo que se les ha pagado. Solo Cristina — el taller no llega aquí."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    hoy = datetime.date.today()
    gente = [ficha_equipo(con, n, hoy) for n in cfg_json(con, "equipo", ["Víctor", "Isaías", "Manawa"])]
    return render(request, "equipo.html", seccion="equipo", gente=gente, hoy_iso=hoy.isoformat(),
                  CUENTAS=con.execute("SELECT * FROM cuentas WHERE activa=1 ORDER BY orden").fetchall())


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


@app.get("/cashflow/arqueo", response_class=HTMLResponse)
def arqueo(request: Request, con=Depends(db), ok: str = ""):
    """Contar la plata de verdad y compararla con lo que dice el ERP.
    Todo lo demás compara el ERP consigo mismo; esto lo ata al mundo real."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    cs = [c for c in saldos(con) if c["tipo"] == "operativa"]
    ult = {r["cuenta_id"]: r for r in con.execute("""SELECT a.* FROM arqueos a
              JOIN (SELECT cuenta_id, MAX(id) m FROM arqueos GROUP BY cuenta_id) x ON x.m=a.id""")}
    hist = con.execute("""SELECT a.*, c.nombre caja FROM arqueos a JOIN cuentas c ON c.id=a.cuenta_id
                          ORDER BY a.id DESC LIMIT 40""").fetchall()
    return render(request, "arqueo.html", seccion="arqueo", cuentas=cs, ult=ult, hist=hist, ok=ok,
                  hoy_iso=datetime.date.today().isoformat())


@app.post("/cashflow/arqueo")
async def arqueo_guardar(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    f = await request.form()
    fecha = (f.get("fecha") or datetime.date.today().isoformat()).strip()
    uid = usuario_id(rol_de(request), request)
    ajustar = bool(f.get("ajustar"))
    n = 0
    for c in saldos(con):
        v = f.get(f"contado_{c['id']}")
        if v is None or not str(v).strip(): continue      # solo las cajas que contó
        contado = cifra(v) or 0.0
        dif = round(contado - c["saldo"], 2)
        cur = con.execute("""INSERT INTO arqueos (fecha, cuenta_id, saldo_erp, contado, diferencia, ajustado, nota, usuario_id)
                             VALUES (?,?,?,?,?,?,?,?)""",
                          (fecha, c["id"], c["saldo"], contado, dif, 1 if (ajustar and dif) else 0,
                           (f.get(f"nota_{c['id']}") or "").strip() or None, uid))
        n += 1
        # si pide cuadrar, se anota la diferencia como gasto o entrada: el ERP nunca "corrige" en silencio
        if ajustar and dif:
            desc = f"Arqueo {fecha} · {c['nombre']}"
            if dif < 0:
                con.execute("""INSERT INTO gastos (fecha, monto_usd, monto_real, moneda, categoria, subcategoria,
                               descripcion, cuenta_id, usuario_id) VALUES (?,?,?,'USD','Ajuste','Faltante',?,?,?)""",
                            (fecha, abs(dif), abs(dif), desc, c["id"], uid))
            else:
                con.execute("""INSERT INTO movimientos (fecha, tipo, monto_usd, monto_real, moneda, cuenta_destino_id,
                               categoria, subcategoria, notas, usuario_id)
                               VALUES (?, 'entrada', ?, ?, 'USD', ?, 'Ajustes', 'Corrección de saldo', ?, ?)""",
                            (fecha, dif, dif, c["id"], desc, uid))
    con.commit()
    return RedirectResponse(f"/cashflow/arqueo?ok={n}", status_code=303)


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
    con.execute("""INSERT INTO compromisos (nombre, categoria, subcategoria, proveedor, monto, moneda, frecuencia, dia, cuenta_id, nota, unidad, precio_unitario)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f["nombre"].strip(), f.get("categoria") or None, f.get("subcategoria") or None, f.get("proveedor") or None, float(f["monto"]) if f.get("monto") else None, f.get("moneda") or "USD",
                 f["frecuencia"], int(f["dia"]) if f.get("dia") not in (None, "") else None, int(f["cuenta_id"]) if f.get("cuenta_id") else None, f.get("nota") or None,
                 (f.get("unidad") or "").strip() or None, cifra(f.get("precio_unitario")) or None))
    con.commit(); return RedirectResponse("/finanzas/recurrentes", status_code=303)


@app.post("/finanzas/recurrentes/{cid}/pagar")
def recurrente_pagar(request: Request, cid: int, vence: str = Form(...), monto: float = Form(...), cuenta_id: str = Form(""),
                     fecha: str = Form(""), cantidad: str = Form(""), volver: str = Form("/finanzas/recurrentes"), con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    c = con.execute("SELECT * FROM compromisos WHERE id=?", (cid,)).fetchone(); tasa = tasa_hoy(con)["valor"] or 0
    monto_usd = round(monto / tasa, 2) if (c["moneda"] == "VES" and tasa) else monto
    cant = cifra(cantidad) or None
    desc = c["nombre"] + (f" · {cant:g} {c['unidad']}" if cant and c["unidad"] else "")
    cur = con.execute("""INSERT INTO gastos (fecha, monto_usd, monto_real, moneda, tasa, categoria, subcategoria, descripcion, proveedor, cuenta_id,
                         cantidad, unidad, recurrente, usuario_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                      ((fecha or "").strip() or datetime.date.today().isoformat(), monto_usd, monto, c["moneda"], tasa if c["moneda"] == "VES" else None, c["categoria"] or "Otros gastos", c["subcategoria"], desc, c["proveedor"],
                       int(cuenta_id) if cuenta_id else c["cuenta_id"], cant, c["unidad"], usuario_id(rol_de(request))))
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
    ("Caja de madera mediana", "PRO-M", False), ("Caja de madera grande", "PRO-G", False),
    ("Comedor Mini", "COM-10", True), ("Comedor Pequeño", "COM-15", True), ("Comedor Mediano", "COM-20", True),
    ("El Bar Grande", "BAR-25", True), ("El Bar Gigante", "BAR-30", True),
    ("Slow Chow Mini", "SLOW-10", True), ("Slow Chow Pequeño", "SLOW-15", True), ("Slow Chow Mediano", "SLOW-20", True), ("Slow Chow Gigante", "SLOW-30", True),
    ("Rampa Nueva", "RAMPA-N", True), ("Rampa Para Perros Mini", "RAMPA-MINI", True),
    ("Muestra / prototipo", None, False),   # lo que hace David cuando se prueba un producto nuevo; lleva descripción y precio a mano
]
# nombre de la pieza → ítem del proveedor (para sacar el precio de Taller › Proveedores)
PIEZA_ITEM = {"Caja de madera mediana": "Caja de madera mediana", "Caja de madera grande": "Caja de madera grande",
              "Rampa Nueva": "Rampa Nueva", "Rampa Para Perros Mini": "Rampa Para Perros Mini"}

def precio_pieza(con, pieza, responsable):
    """Precio unitario que cobra el carpintero por esa pieza (comedores y slow chow usan 'Comedores')."""
    item = PIEZA_ITEM.get(pieza, "Comedores (todos los tamaños)" if pieza.startswith(("Comedor", "El Bar", "Slow Chow")) else None)
    if not item and con.execute("SELECT 1 FROM proveedor_items WHERE item=?", (pieza,)).fetchone(): item = pieza
    if not item: return None
    def precio(it):
        r = con.execute("""SELECT i.precio FROM proveedor_items i JOIN proveedores p ON p.id=i.proveedor_id
                           WHERE i.item=? AND (p.nombre=? OR ?='') ORDER BY p.nombre=? DESC LIMIT 1""", (it, responsable or "", responsable or "", responsable or "")).fetchone()
        return r["precio"] if r else None
    return precio(item)

def precio_barnizado(con, responsable):
    r = con.execute("""SELECT i.precio FROM proveedor_items i JOIN proveedores p ON p.id=i.proveedor_id WHERE i.item='Barnizado de caja' AND (p.nombre=? OR ?='') LIMIT 1""", (responsable or "", responsable or "")).fetchone()
    return r["precio"] if r else 0

def volver_produccion(con, pid):
    r = con.execute("SELECT COALESCE(tipo_pedido,'produccion') t FROM produccion WHERE id=?", (pid,)).fetchone()
    return RedirectResponse(f"/produccion?tipo={r['t'] if r else 'produccion'}", status_code=303)


PROVEEDORES_MADERA = ("Walter", "David")   # lo que se manda a hacer; el resto son pedidos a proveedores


def tipo_de_proveedor(nombre):
    return "produccion" if (nombre or "") in PROVEEDORES_MADERA else "proveedor"


@app.get("/produccion", response_class=HTMLResponse)
def produccion(request: Request, ver: str = "en_proceso", q: str = "", debe: str = "", con=Depends(db)):
    """Todo lo que está pedido y no ha llegado: la madera que manda a hacer y lo que le compra a un proveedor."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)   # Taller es de Cristina
    if (q or debe) and ver == "en_proceso": ver = "todas"   # al buscar, o al venir de "le debes a X", se mira todo
    saldo_sql = "pr.costo - COALESCE((SELECT SUM(a.monto) FROM abonos_produccion a WHERE a.produccion_id=pr.id),0)"
    rows = con.execute(f"""SELECT pr.*, COALESCE(pr.pieza, p.nombre) producto FROM produccion pr LEFT JOIN productos p ON p.id=pr.producto_id
                          WHERE (?='todas' OR pr.estado=?)
                          AND (?='' OR pr.pieza LIKE ? OR pr.responsable LIKE ? OR pr.descripcion LIKE ? OR pr.nota LIKE ?)
                          AND (?='' OR (pr.responsable=? AND pr.estado!='cancelado' AND pr.costo IS NOT NULL AND {saldo_sql} > 0.009))
                          ORDER BY COALESCE(pr.fecha_esperada, pr.fecha_pedido), pr.id""",
                       (ver, ver, q, f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", debe, debe)).fetchall()
    n = {r[0]: r[1] for r in con.execute("SELECT estado, COUNT(*) FROM produccion GROUP BY 1")}
    por_pagar = con.execute("""SELECT COALESCE(pr.responsable,'—') quien, SUM(pr.costo - COALESCE((SELECT SUM(a.monto) FROM abonos_produccion a WHERE a.produccion_id=pr.id),0)) monto, COUNT(*) n
                               FROM produccion pr WHERE pr.estado!='cancelado' AND pr.costo IS NOT NULL
                               AND pr.costo - COALESCE((SELECT SUM(a.monto) FROM abonos_produccion a WHERE a.produccion_id=pr.id),0) > 0.009 GROUP BY 1 ORDER BY monto DESC""").fetchall()
    rows = [dict(r) | {"abonado": con.execute("SELECT COALESCE(SUM(monto),0) FROM abonos_produccion WHERE produccion_id=?", (r["id"],)).fetchone()[0],
                       "abonos": con.execute("SELECT * FROM abonos_produccion WHERE produccion_id=? ORDER BY fecha, id", (r["id"],)).fetchall()} for r in rows]
    quienes = [r[0] for r in con.execute("SELECT nombre FROM proveedores WHERE activo=1 ORDER BY (nombre='Walter') DESC, nombre")]
    piezas = [p[0] for p in PIEZAS_PRODUCCION]
    for r in con.execute("SELECT DISTINCT item FROM proveedor_items ORDER BY item"):
        if r["item"] not in piezas: piezas.append(r["item"])
    precios = {c: {z: precio_pieza(con, z, c) for z in piezas} | {"__barnizado": precio_barnizado(con, c)} for c in quienes}
    return render(request, "produccion.html", seccion="produccion", rows=rows, piezas=piezas,
                  carpinteros=quienes, ver=ver, n=n, debe=debe, por_pagar=por_pagar, FORMAS_PAGO=FORMAS_PAGO, precios=precios)


@app.post("/produccion")
async def produccion_crear(request: Request, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    """Un pedido puede traer varios productos (a Walter le pides comedores y cajas a la vez).
    Cada producto queda como su propia línea, porque se recibe y se paga por separado."""
    f = await request.form()
    piezas_ok = {p[0] for p in PIEZAS_PRODUCCION} | {r[0] for r in con.execute("SELECT DISTINCT item FROM proveedor_items")}
    quien = (f.get("responsable") or "").strip() or None
    fped = f.get("fecha_pedido") or datetime.date.today().isoformat()
    fesp = f.get("fecha_esperada") or None
    uid = usuario_id(rol_de(request))
    descs = f.getlist("descripcion"); barns = f.getlist("barnizado")
    lineas, i_barn = [], 0
    for idx, (pieza, cant, costo_l) in enumerate(zip(f.getlist("pieza"), f.getlist("cantidad"), f.getlist("costo_linea"))):
        if pieza not in piezas_ok or not (cant or "").strip(): continue
        cantidad = int(cifra(cant))
        if cantidad <= 0: continue
        barn = 1 if (pieza.startswith("Caja de madera") and barns and len(barns) > i_barn) else 0
        if pieza.startswith("Caja de madera"): i_barn += 1
        costo = cifra(costo_l) or None
        if costo is None:   # sin monto escrito: cantidad × precio del proveedor (+ barnizado por caja)
            pu = precio_pieza(con, pieza, quien)
            if pu is not None: costo = round((pu + (precio_barnizado(con, quien) if barn else 0)) * cantidad, 2)
        lineas.append((pieza, cantidad, costo, barn, (descs[idx].strip() if idx < len(descs) and descs[idx] else None)))
    if not lineas: return RedirectResponse("/produccion", status_code=303)

    ids = []
    for pieza, cantidad, costo, barn, desc in lineas:
        sku = next((s_ for (nom, s_, _) in PIEZAS_PRODUCCION if nom == pieza), None)
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
    con.commit(); return RedirectResponse("/produccion", status_code=303)


@app.post("/produccion/{pid}/recibir")
def produccion_recibir(request: Request, pid: int, cantidad: int = Form(...), con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)   # Taller es de Cristina
    r = con.execute("SELECT * FROM produccion WHERE id=?", (pid,)).fetchone()
    if r:
        hoy = datetime.date.today().isoformat(); uid = usuario_id(rol_de(request))
        terminado = next((ok for (nom, _, ok) in PIEZAS_PRODUCCION if nom == (r["pieza"] or "")), True)
        if terminado and r["producto_id"]:   # comedores y rampas llegan listos; las cajas de madera y las muestras no son producto terminado
            con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, nota, usuario_id) VALUES (?,?,?,?,?,?)", (r["producto_id"], hoy, "entrada", cantidad, f"producción #{pid}" + (f" · {r['responsable']}" if r["responsable"] else ""), uid))
        total = r["recibido"] + cantidad
        con.execute("UPDATE produccion SET recibido=?, estado=?, recibido_en=? WHERE id=?", (total, "recibido" if total >= r["cantidad"] else "en_proceso", hoy if total >= r["cantidad"] else None, pid))
        con.commit()
    return RedirectResponse("/produccion", status_code=303)


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
    if recibido:
        cant_gasto = recibido
        desc = f"{pr['pieza']} · {recibido}" + (f" recibidos de {pedido}" if recibido != pedido else "")
    else:
        cant_gasto = None          # adelanto: todavía no ha llegado nada, así que no se cuenta cantidad
        desc = f"{pr['pieza']} · adelanto de {pedido} pedidos"
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
        pagar_produccion(con, pid, m, forma, fecha or datetime.date.today().isoformat(), nota, usuario_id(rol_de(request)))
        con.commit()
    return RedirectResponse("/produccion?ver=todas", status_code=303)


@app.post("/produccion/{pid}/editar")
async def produccion_editar(request: Request, pid: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/produccion", status_code=303)
    f = await request.form(); g = lambda k: (f.get(k) or "").strip() or None
    con.execute("""UPDATE produccion SET cantidad=?, responsable=?, fecha_pedido=?, fecha_esperada=?, costo=?, nota=?, barnizado=?, descripcion=? WHERE id=?""",
                (int(f.get("cantidad") or 1), g("responsable"), g("fecha_pedido") or datetime.date.today().isoformat(), g("fecha_esperada"),
                 float(f["costo"].replace(",", ".")) if g("costo") else None, g("nota"), 1 if f.get("barnizado") == "1" else 0, g("descripcion"), pid))
    r = con.execute("SELECT costo, (SELECT COALESCE(SUM(monto),0) FROM abonos_produccion WHERE produccion_id=?) ab FROM produccion WHERE id=?", (pid, pid)).fetchone()
    con.execute("UPDATE produccion SET pagado=? WHERE id=?", (1 if r["costo"] is not None and r["ab"] >= r["costo"] - 0.009 else 0, pid))
    con.commit(); return RedirectResponse("/produccion?ver=todas", status_code=303)


@app.post("/produccion/abono/{aid}/borrar")
def produccion_abono_borrar(request: Request, aid: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/produccion", status_code=303)
    r = con.execute("SELECT produccion_id FROM abonos_produccion WHERE id=?", (aid,)).fetchone()
    if r:
        con.execute("DELETE FROM abonos_produccion WHERE id=?", (aid,)); con.execute("UPDATE produccion SET pagado=0 WHERE id=?", (r["produccion_id"],)); con.commit()
    return RedirectResponse("/produccion?ver=todas", status_code=303)


@app.post("/produccion/{pid}/deshacer")
def produccion_deshacer(request: Request, pid: int, con=Depends(db)):
    """Se marcó recibido por error: vuelve a 'en proceso' con lo recibido en 0 y borra las entradas de inventario que generó."""
    if not solo_admin(request): return RedirectResponse("/produccion", status_code=303)
    con.execute("DELETE FROM mov_inventario WHERE nota LIKE ?", (f"producción #{pid}%",))
    con.execute("UPDATE produccion SET recibido=0, estado='en_proceso', recibido_en=NULL WHERE id=? AND estado!='cancelado'", (pid,))
    con.commit(); return RedirectResponse("/produccion", status_code=303)


@app.post("/produccion/{pid}/cerrar")
def produccion_cerrar(request: Request, pid: int, con=Depends(db)):
    """Llegaron menos de los que pediste y no van a mandar el resto: el pedido se cierra con lo que llegó."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    r = con.execute("SELECT cantidad, recibido, costo FROM produccion WHERE id=?", (pid,)).fetchone()
    if r and (r["recibido"] or 0) > 0 and r["recibido"] < r["cantidad"]:
        # el costo baja en proporción a lo que de verdad llegó, para no quedar debiendo lo que no te mandaron
        unit = (r["costo"] or 0) / r["cantidad"] if r["cantidad"] else 0
        con.execute("UPDATE produccion SET cantidad=?, costo=?, estado='recibido', recibido_en=? WHERE id=?",
                    (r["recibido"], round(unit * r["recibido"], 2), datetime.date.today().isoformat(), pid))
        con.commit()
    return RedirectResponse("/produccion", status_code=303)


@app.post("/produccion/{pid}/cancelar")
def produccion_cancelar(request: Request, pid: int, con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    con.execute("UPDATE produccion SET estado='cancelado' WHERE id=?", (pid,)); con.commit(); return RedirectResponse("/produccion", status_code=303)


# ------------------------------------------------------------------ SEGUIMIENTOS (El Porche)
RESULTADOS = {"compro": "Compró", "mensaje": "Mensaje enviado", "fecha": "Lo quiere otro día", "ya_no_usa": "Ya no lo usa", "felicitado": "Felicitado", "pago": "Pagó",
              "paso_pro": "Se pasó a la Versión PRO", "otro_basico": "Compró otro Básico", "no_le_interesa": "No le interesa por ahora",
              "lo_pensara": "Lo pensará", "no_responde": "No responde", "otro": "Otro"}   # los 3 últimos: solo para leer registros viejos
# qué opciones se ofrecen según el tipo de seguimiento
RESULTADOS_POR_TIPO = {"cumple": ["felicitado"], "cobro": ["pago", "mensaje"], "basico": ["paso_pro", "otro_basico", "mensaje", "fecha", "no_le_interesa"], "*": ["compro", "mensaje", "fecha", "ya_no_usa"]}
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

def seguimientos_pendientes(con, umbral=None, ventana=7):
    """Seguimientos personalizados: cada miembro PRO se activa según SU fecha de entrega (del porche o del último repuesto)
    más su intervalo propio (cada cuánto compra repuesto; si no hay historial, 30 días). Queda 'hoy' durante 7 días;
    si no se contactó, pasa a 'atrasado' (se ve en Seguimientos, no en Inicio)."""
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
        saldo_pack = con.execute("SELECT COALESCE(SUM(k.unidades - k.entregadas_inicio - (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=k.id)),0) FROM packs k WHERE k.cliente_id=?", (m["id"],)).fetchone()[0]
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
                        tipo_txt=("Hoy es el cumple · felicitar" if faltan == 0 else (f"Faltan {faltan} día{'s' if faltan != 1 else ''} · ofrécele un regalo" if faltan <= 2 else f"en {faltan} días")),
                        hecho=bool(r), estado=(estado_resultado(r) if r else ("Pendiente" if faltan <= 2 else "Próximo"))))
    out.sort(key=lambda s: s["dias"])
    return out


@app.get("/seguimientos", response_class=HTMLResponse)
def seguimientos(request: Request, ver: str = "pendientes", tipo: str = "", q: str = "", con=Depends(db)):
    sincronizar_packs(con)
    todos = seguimientos_pendientes(con)
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
                (cliente_id, tipo, clave, resultado, nota or None, hasta, usuario_id(rol_de(request)), (prev["intentos"] if prev else 0) + 1))
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
        cobro_extra(con, r["orden_id"], "Delivery repuesto", dl, f_pago, datetime.date.today().isoformat(), usuario_id(rol_de(request)))
        con.execute("UPDATE repuestos_prepagados SET delivery_forma=? WHERE id=?", (f_pago, rid))
    con.commit(); return RedirectResponse(volver or "/prepagados", status_code=303)


@app.post("/prepagados/{rid}/campo")
def prepagado_campo(request: Request, rid: int, despachador: str = Form(""), agencia: str = Form(""), en_ruta: str = Form(""), volver: str = Form(""), con=Depends(db)):
    """Ajustes rápidos desde Operaciones: quién lo lleva, por cuál agencia, o marcarlo en ruta."""
    if "coordinar" not in PERMISOS[rol_de(request)]: return RedirectResponse("/operaciones", status_code=303)
    if despachador: con.execute("UPDATE repuestos_prepagados SET despachador=? WHERE id=?", (despachador, rid))
    if agencia: con.execute("UPDATE repuestos_prepagados SET agencia=? WHERE id=?", (agencia, rid))
    if en_ruta: con.execute("UPDATE repuestos_prepagados SET en_ruta=? WHERE id=?", (1 if en_ruta == "1" else 0, rid))
    con.commit(); return RedirectResponse(volver or "/operaciones", status_code=303)


@app.post("/prepagados/{rid}/entregar")
def prepagado_entregar(request: Request, rid: int, tipo_entrega: str = Form(""), despachador: str = Form(""), delivery_cobrado: str = Form(""), delivery_forma: str = Form(""), fecha: str = Form(""), volver: str = Form(""), con=Depends(db)):
    con.execute("UPDATE repuestos_prepagados SET entregado_en=?, tipo_entrega=COALESCE(NULLIF(?,''),tipo_entrega), despachador=COALESCE(NULLIF(?,''),despachador), usuario_id=? WHERE id=?",
                (fecha.strip() or datetime.date.today().isoformat(), tipo_entrega, despachador, usuario_id(rol_de(request)), rid))
    r = con.execute("SELECT orden_id, delivery, delivery_pagado FROM repuestos_prepagados WHERE id=?", (rid,)).fetchone()
    if delivery_cobrado == "1" and r and not r["delivery_pagado"]:   # el despachador cobró el delivery al entregar
        con.execute("UPDATE repuestos_prepagados SET delivery_pagado=1, delivery_forma=? WHERE id=?", (delivery_forma or None, rid))
        if (r["delivery"] or 0) > 0 and r["orden_id"]:   # ese cobro entra dentro de la orden del cliente
            cobro_extra(con, r["orden_id"], "Delivery repuesto", r["delivery"], delivery_forma or None,
                        fecha.strip() or datetime.date.today().isoformat(), usuario_id(rol_de(request)))
    con.commit(); return RedirectResponse(volver or "/prepagados", status_code=303)


@app.post("/prepagados/nuevo")
def prepagado_nuevo(request: Request, cliente_id: int = Form(...), tamano: str = Form("Grande"), pagado_en: str = Form(""), monto: str = Form(""), notas: str = Form(""), con=Depends(db)):
    """Registrar a mano un repuesto que el cliente dejó pagado (por ejemplo, del histórico)."""
    con.execute("INSERT INTO repuestos_prepagados (cliente_id,tamano,pagado_en,monto,notas,usuario_id) VALUES (?,?,?,?,?,?)", (cliente_id, tamano, pagado_en or datetime.date.today().isoformat(), float(monto.replace(",", ".")) if monto.strip() else None, notas or None, usuario_id(rol_de(request))))
    con.commit(); return RedirectResponse("/prepagados", status_code=303)


def cargar_packs(con):
    """Packs de repuestos con su saldo, entregas y fecha estimada del próximo repuesto."""
    sincronizar_packs(con)
    cols = [r[1] for r in con.execute("PRAGMA table_info(packs)")]
    for c, tp in (("fecha_programada", "TEXT"), ("tipo_programado", "TEXT"), ("despachador_programado", "TEXT"), ("nota_programada", "TEXT"), ("retiro_programado", "INTEGER"), ("delivery_programado", "REAL"), ("delivery_pagado", "INTEGER"), ("deliveries_prepagados", "INTEGER")):
        if c not in cols: con.execute(f"ALTER TABLE packs ADD COLUMN {c} {tp}")
    rows = con.execute("""SELECT k.*, c.nombre cliente, c.telefono, c.ciudad, o.numero orden, o.tipo_entrega,
        (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=k.id) entregas_posteriores,
        (SELECT MAX(fecha) FROM entregas_repuesto e WHERE e.pack_id=k.id) ultima_entrega
        FROM packs k JOIN clientes c ON c.id=k.cliente_id LEFT JOIN ordenes o ON o.id=k.orden_id ORDER BY k.creado_en DESC""").fetchall()
    hoy = datetime.date.today(); lista = []
    for r in rows:
        d = dict(r); d["entregadas"] = d["entregadas_inicio"] + d["entregas_posteriores"]; d["saldo"] = d["unidades"] - d["entregadas"]
        ref = d["ultima_entrega"] or d["creado_en"][:10]
        d["dias_ultima"] = (hoy - datetime.date.fromisoformat(ref)).days
        d["entregas"] = con.execute("SELECT e.*, u.nombre usuario FROM entregas_repuesto e LEFT JOIN usuarios u ON u.id=e.usuario_id WHERE pack_id=? ORDER BY fecha", (d["id"],)).fetchall()
        fechas = [d["creado_en"][:10]] + [e["fecha"] for e in d["entregas"]]
        d["duraciones"] = [(datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days for a, b in zip(fechas, fechas[1:])]
        d["promedio"] = round(sum(d["duraciones"]) / len(d["duraciones"])) if d["duraciones"] else None
        d["proximo"] = (datetime.date.fromisoformat(ref) + datetime.timedelta(days=ciclo_de(d["promedio"]))).isoformat()   # 21 días, o su ritmo si retira más seguido
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
def pack_programar(request: Request, pid: int, fecha: str = Form(""), tipo_entrega: str = Form(""), despachador: str = Form(""), notas: str = Form(""), retiro: str = Form(""), delivery: str = Form("0"), delivery_pagado: str = Form("0"), pago_forma: str = Form(""), volver: str = Form(""), con=Depends(db)):
    k = con.execute("SELECT * FROM packs WHERE id=?", (pid,)).fetchone()
    dl = float(delivery or 0) if tipo_entrega in ("delivery", "delivery_fuera") else 0.0
    pagado = 1 if delivery_pagado == "1" and dl > 0 else 0
    if k and (k["deliveries_prepagados"] or 0) > 0 and dl > 0:   # ya lo pagó por adelantado con el pack: no se cobra de nuevo
        pagado = 2; dl_prep = dl; dl = 0.0
    saldo_k = (k["unidades"] - k["entregadas_inicio"] - con.execute("SELECT COUNT(*) FROM entregas_repuesto WHERE pack_id=?", (pid,)).fetchone()[0]) if k else 1
    cuantos_prog = max(1, min(int(retiro) if retiro.isdigit() else 1, max(saldo_k, 1)))   # cuántos repuestos se lleva ese día
    con.execute("UPDATE packs SET fecha_programada=?, tipo_programado=?, despachador_programado=?, nota_programada=?, retiro_programado=?, delivery_programado=?, delivery_pagado=? WHERE id=?",
                (fecha or None, tipo_entrega or None, despachador or None, notas or None, cuantos_prog, dl, pagado, pid))
    if pagado == 2:
        con.execute("UPDATE packs SET delivery_programado=?, delivery_pagado=1 WHERE id=?", (dl_prep, pid))
    if pagado == 1 and k and k["orden_id"]:   # el delivery ya lo pagó: entra a la orden del pack de una
        uid = usuario_id(rol_de(request)); forma = pago_forma or "Pago Móvil"
        con.execute("UPDATE ordenes SET delivery=COALESCE(delivery,0)+?, total=COALESCE(total,0)+?, actualizado_en=datetime('now','localtime') WHERE id=?", (dl, dl, k["orden_id"]))
        con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,cuenta,fecha,estado,confirmado_por,confirmado_en) VALUES (?,?,?,?,'USD',?,?,'confirmado',?,datetime('now','localtime'))",
                    (k["orden_id"], forma, dl, dl, FORMA_CUENTA.get(forma), datetime.date.today().isoformat(), uid))
        registrar(con, k["orden_id"], uid, "pack", f"Retiro programado {fecha} · delivery {fmt_usd(dl)} pagado ({forma})")
    con.commit(); return RedirectResponse(volver or "/packs", status_code=303)


@app.post("/packs/{pid}/entregar")
def pack_entregar(request: Request, pid: int, fecha: str = Form(""), cuantos: str = Form("1"), tipo_entrega: str = Form(""), despachador: str = Form(""), delivery_cobrado: str = Form("0"), pago_forma: str = Form(""), notas: str = Form(""), volver: str = Form(""), con=Depends(db)):
    k = con.execute("SELECT k.*, (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=k.id) n FROM packs k WHERE id=?", (pid,)).fetchone()
    if not k or k["entregadas_inicio"] + k["n"] >= k["unidades"]: return RedirectResponse("/packs", status_code=303)
    saldo = k["unidades"] - k["entregadas_inicio"] - k["n"]
    n_retiros = max(1, min(int(cuantos) if cuantos.isdigit() else 1, saldo))   # puede llevarse varios de una vez
    for i in range(n_retiros):
        con.execute("INSERT INTO entregas_repuesto (pack_id, fecha, tipo_entrega, despachador, delivery_cobrado, notas, usuario_id) VALUES (?,?,?,?,?,?,?)",
                (pid, fecha or datetime.date.today().isoformat(), tipo_entrega or k["tipo_programado"] or None, despachador or k["despachador_programado"] or None, float(delivery_cobrado or 0) if i == 0 else 0.0, notas or None, usuario_id(rol_de(request))))
    con.execute("UPDATE packs SET fecha_programada=NULL, tipo_programado=NULL, despachador_programado=NULL, nota_programada=NULL, retiro_programado=NULL, delivery_programado=NULL, delivery_pagado=NULL, estado=CASE WHEN entregadas_inicio + (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=packs.id) >= unidades THEN 'completo' ELSE estado END WHERE id=?", (pid,))
    dc = float(delivery_cobrado or 0)
    if k["delivery_pagado"]: dc = 0.0   # ya se cobró al programar (o venía prepagado con el pack)
    if (k["deliveries_prepagados"] or 0) > 0 and (tipo_entrega or k["tipo_programado"]) in ("delivery", "delivery_fuera"):
        con.execute("UPDATE packs SET deliveries_prepagados=deliveries_prepagados-1 WHERE id=?", (pid,)); dc = 0.0
    if dc > 0 and k["orden_id"]:   # el delivery del retiro se suma a la orden original del pack (total, pago y gasto del cliente)
        con.execute("UPDATE ordenes SET delivery=COALESCE(delivery,0)+?, total=COALESCE(total,0)+?, actualizado_en=datetime('now','localtime') WHERE id=?", (dc, dc, k["orden_id"]))
        forma = pago_forma or "Efectivo USD"
        con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,cuenta,fecha,estado,confirmado_por,confirmado_en) VALUES (?,?,?,?,'USD',?,?,'confirmado',?,datetime('now','localtime'))",
                    (k["orden_id"], forma, dc, dc, FORMA_CUENTA.get(forma), fecha or datetime.date.today().isoformat(), usuario_id(rol_de(request))))
        registrar(con, k["orden_id"], usuario_id(rol_de(request)), "pack", f"Retiro de repuesto · delivery {fmt_usd(dc)} ({forma})")
    con.commit(); return RedirectResponse("/prepagados", status_code=303)


# ------------------------------------------------------------------ CLIENTES (mínimo; ficha completa en la Parte 3)
@app.get("/clientes", response_class=HTMLResponse)
def clientes(request: Request, q: str = "", ver: str = "todos", ciudad: str = "", origen: str = "", con=Depends(db)):
    return _clientes(request, q, ver, ciudad, con, origen=origen)


@app.get("/mascotas", response_class=HTMLResponse)
def mascotas_lista(request: Request, q: str = "", ver: str = "mascotas", raza: str = "", con=Depends(db)):
    return _clientes(request, q, "cumples" if ver == "cumples" else "mascotas", "", con, raza=raza)


def _clientes(request, q, ver, ciudad, con, raza="", origen=""):
    base = """SELECT c.*, (SELECT direccion FROM direcciones d WHERE d.cliente_id=c.id AND principal=1) dir,
             (SELECT COUNT(*) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada') n_ordenes,
             (SELECT COALESCE(SUM(total),0) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada') gastado,
             (SELECT MAX(creado_en) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada') ultima,
             (SELECT MIN(creado_en) FROM ordenes o WHERE o.cliente_id=c.id AND o.estado!='cancelada') primera,
             (SELECT GROUP_CONCAT(m.nombre || COALESCE(' (' || m.raza || ')',''), ', ') FROM mascotas m WHERE m.cliente_id=c.id) perros,
             (SELECT COUNT(*) FROM mascotas m WHERE m.cliente_id=c.id AND (m.raza='Pendiente' OR m.revisar=1)) perro_pend,
             (SELECT COUNT(*) FROM mascotas m WHERE m.cliente_id=c.id) n_perros,
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
        r["pendientes"] = [k for k in ("telefono", "correo", "ciudad") if r[k] == "Pendiente"] + (["perro"] if r["perro_pend"] else [])
    conteos = {"todos": len(rows), "pro": sum(1 for r in rows if r["pro"]), "basico": sum(1 for r in rows if r["basico"]), "pendientes": sum(1 for r in rows if r["pendientes"])}
    for k in TIPOS_CLIENTE: conteos[k] = sum(1 for r in rows if r["tipo"] == k)
    ciudades = {}
    for r in rows:
        if r["ciudad"]: ciudades[r["ciudad"].strip()] = ciudades.get(r["ciudad"].strip(), 0) + 1
    for cdd in CIUDADES_VE: ciudades.setdefault(cdd, 0)
    ciudades = sorted(ciudades.items(), key=lambda x: (-x[1], x[0]))
    if q and ver != "mascotas":
        ql = q.lower(); rows = [r for r in rows if any(ql in (r[k] or "").lower() for k in ("nombre", "telefono", "correo", "cedula", "perros", "ciudad"))]
    elif ver == "pro": rows = [r for r in rows if r["pro"]]
    elif ver == "basico": rows = [r for r in rows if r["basico"]]
    elif ver == "pendientes": rows = [r for r in rows if r["pendientes"]]
    elif ver in TIPOS_CLIENTE: rows = [r for r in rows if r["tipo"] == ver]
    if ciudad: rows = [r for r in rows if ciudad.strip().lower() in (r["ciudad"] or "").lower()]
    if origen: rows = [r for r in rows if (r["origen"] or "Sin registrar") == origen]
    cumples = cumples_proximos(con, 2)
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
    return render(request, "clientes.html", seccion=("mascotas" if ver in ("mascotas", "cumples") else "clientes"), clientes=rows[:200], q=q, ver=ver, total=conteos["todos"], conteos=conteos, truncado=len(rows) > 200, cumples=cumples, RESULTADOS=RESULTADOS, RPT=RESULTADOS_POR_TIPO, TIPOS_CLIENTE=TIPOS_CLIENTE, ciudad=ciudad, ciudades=ciudades, origen=origen,
                  origenes=sorted({(r["origen"] or "Sin registrar") for r in con.execute("SELECT origen FROM clientes")}), mascotas=mascotas, razas_top=razas_top, raza=raza)


@app.get("/clientes/nuevo/panel", response_class=HTMLResponse)
def cliente_nuevo_panel(request: Request, con=Depends(db)):
    otros = con.execute("SELECT id, nombre FROM clientes ORDER BY nombre").fetchall()
    return render(request, "_cliente_nuevo.html", otros=otros)


@app.post("/clientes/nuevo")
async def cliente_crear(request: Request, con=Depends(db)):
    f = await request.form()
    nombre_pila, apellido = f["nombre_pila"].strip(), (f.get("apellido") or "").strip() or None
    cur = con.execute("INSERT INTO clientes (nombre_pila,apellido,nombre,telefono,cedula,correo,ciudad,estado,canal_habitual,origen,referido_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (nombre_pila, apellido, nombre_completo(nombre_pila, apellido), normalizar_telefono(f.get("telefono")), (f.get("cedula") or "").strip().upper() or None,
                       f.get("correo") or None, f.get("ciudad") or None, f.get("estado_geo") or None, f.get("canal_habitual") or None,
                       (f.get("origen") or "").strip() or None, int(f["referido_id"]) if (f.get("referido_id") or "").isdigit() else None))
    cid = cur.lastrowid
    if f.get("direccion"):
        con.execute("INSERT INTO direcciones (cliente_id,etiqueta,direccion,zona,municipio,ciudad,estado,maps,principal) VALUES (?,?,?,?,?,?,?,?,1)",
                    (cid, "Principal", f["direccion"], f.get("zona") or None, f.get("municipio") or None, f.get("ciudad") or None, f.get("estado_geo") or None, f.get("maps") or None))
    if f.get("nota"):
        con.execute("INSERT INTO notas_cliente (cliente_id,tipo,texto,mostrar_en_orden,mostrar_logistica,autor_id) VALUES (?,?,?,?,?,?)",
                    (cid, "general", f["nota"], 1, 0 if f.get("nota_privada") else 1, usuario_id(rol_de(request))))
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


def oportunidades_cliente(lineas, perros):
    """Qué le falta a este hogar, con el criterio de Cristina:
    el porche y la rampa se comparten (uno por casa); el comedor es uno por perro.
    Nunca sugiere la cinta (va pegada a la rampa, no es un producto)."""
    n = lambda pref: sum(int(r["n"] or 0) for r in lineas if r["sku"] and r["sku"].startswith(pref))
    por_cat = lambda cat: sum(int(r["n"] or 0) for r in lineas if r["categoria"] == cat and not (r["sku"] or "").startswith("MALLA"))
    nombres = ", ".join(p["nombre"] for p in perros) or "su perro"
    np = max(len(perros), 1)
    porches = n("PRO-"); comedores = por_cat("comedor"); rampas = por_cat("rampa")
    out = []
    if not porches: out.append(("El Porche Versión PRO", "Todavía no tiene porche."))
    if comedores < np:
        faltan = np - comedores
        titulo = "Comedor" if faltan == 1 else f"{faltan} comedores"
        # lo que vale es la cuenta: 3 perros y 2 comedores. Lo demás ella ya lo sabe.
        out.append((titulo, f"{np} perro{'s' if np != 1 else ''} y {comedores} comedor{'es' if comedores != 1 else ''}."
                            if comedores else f"{nombres}: sin comedor."))
    if not rampas: out.append(("Rampa", "No tiene rampa."))
    if porches and not n("MALLA") and not n("OPC-MALLA"): out.append(("Malla", "Tiene porche y nunca ha llevado malla."))
    if n("REP-") >= 2 and not n("PACK3"): out.append(("Pack 3 repuestos", f"{n('REP-')} repuestos sueltos y ningún pack."))
    return out


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
    packs = con.execute("""SELECT k.*, (SELECT COUNT(*) FROM entregas_repuesto e WHERE e.pack_id=k.id) entregadas,
                           (SELECT MAX(fecha) FROM entregas_repuesto e WHERE e.pack_id=k.id) ult FROM packs k WHERE k.cliente_id=? ORDER BY k.id DESC""", (cid,)).fetchall()
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
    return render(request, "cliente.html", seccion="clientes", c=c, perros=perros, dirs=dirs, notas=notas, ordenes=ordenes, comprado=comprado, catalogo=catalogo,
                  refirio=refirio, lo_trajo=lo_trajo,
                  packs=packs, entregas=entregas, segs=segs, fotos=fotos, total=total, n_ordenes=n, primera=primera, ultima=ultima, dias_sin=dias_sin,
                  etiquetas=etiquetas, ritmo=ritmo, confianza=confianza, ult_rep=ult_rep, proximo=proximo, porche=porche, nums=nums, saldo_pack=saldo_pack,
                  oportunidades=oportunidades_cliente(lineas, perros), formas=formas, canales=canales, entregas_tipo=entregas_tipo,
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
        "nombre_pila":    lambda v: (v or "").strip() or actual["nombre_pila"],   # sin nombre no se queda
        "apellido":       lambda v: (v or "").strip() or None,
        "telefono":       lambda v: normalizar_telefono(v),
        "cedula":         lambda v: (v or "").strip().upper() or None,
        "correo":         lambda v: (v or "").strip() or None,
        "ciudad":         lambda v: (v or "").strip() or None,
        "porche_version": lambda v: (v or "").strip() or None,
    }
    campos = {k: fn(f.get(k)) for k, fn in limpiar.items() if k in f}
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
        con.execute("UPDATE direcciones SET etiqueta=?, direccion=?, maps=?, ciudad=? WHERE id=? AND cliente_id=?", (f.get("etiqueta") or "Principal", f["direccion"], f.get("maps") or None, f.get("ciudad") or None, did, cid))
    elif f.get("direccion"):
        primera = con.execute("SELECT COUNT(*) FROM direcciones WHERE cliente_id=?", (cid,)).fetchone()[0] == 0
        con.execute("INSERT INTO direcciones (cliente_id,etiqueta,direccion,maps,ciudad,principal) VALUES (?,?,?,?,?,?)", (cid, f.get("etiqueta") or ("Principal" if primera else "Otra"), f["direccion"], f.get("maps") or None, f.get("ciudad") or None, 1 if primera else 0))
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
                    (cid, "general", f["texto"].strip(), 1, 0 if f.get("privada") else 1, usuario_id(rol_de(request))))
    con.commit(); return RedirectResponse(f"/clientes/{cid}", 303)


# ------------------------------------------------------------------ DESPACHADORES
def fijar_pago_despachador(con, oid):
    """Al despachador se le paga lo mismo que el cliente pagó de delivery, siempre.
    No se guarda una copia: se lee del delivery cada vez. Así, si cambias el delivery o le pasas
    la orden a otro despachador, lo que le debes se ajusta solo. Las tarifas por zona son solo
    referencia para chequear; no deciden lo que se le paga."""
    con.execute("UPDATE ordenes SET pago_despachador=NULL WHERE id=?", (oid,))

def tarifa_agencia(con, agencia):
    """Llevar los pedidos a la agencia se paga por viaje, no por pedido: lleve 1 o lleve 6, es la misma tarifa."""
    t = cfg_json(con, "tarifa_agencia", {}) or {}
    return float(t.get(agencia, t.get("*", 5)))


def resumen_despachador(con, nombre, hoy):
    """Lo que se le debe: cada orden asignada (no cancelada) suma su pago hasta que la marcas pagada (los lunes)."""
    s = con.execute("""SELECT SUM(CASE WHEN despachador_pagado=0 THEN COALESCE(delivery, 0) ELSE 0 END) debe,
                              SUM(CASE WHEN despachador_pagado=0 THEN 1 ELSE 0 END) n_debe,
                              SUM(CASE WHEN despachador_pagado=0 AND estado!='entregada' THEN 1 ELSE 0 END) n_sin_entregar
                       FROM ordenes WHERE despachador=? AND estado!='cancelada' AND origen_excel=0""", (nombre,)).fetchone()
    ult = con.execute("SELECT fecha, monto FROM pagos_despachador WHERE despachador=? ORDER BY fecha DESC, id DESC LIMIT 1", (nombre,)).fetchone()
    zonas = con.execute("""SELECT COALESCE(NULLIF(zona,''), 'Sin zona') z, COUNT(*) n FROM ordenes
                           WHERE despachador=? AND estado!='cancelada' AND origen_excel=0 AND despachador_pagado=0 GROUP BY 1 ORDER BY 2 DESC LIMIT 6""", (nombre,)).fetchall()
    v = con.execute("SELECT COALESCE(SUM(monto),0) m, COUNT(*) n FROM viajes_agencia WHERE despachador=? AND pagado=0", (nombre,)).fetchone()
    r = dict(s)
    r["debe"] = (r["debe"] or 0) + v["m"]          # los viajes a la agencia se le pagan igual que las entregas
    r["n_viajes"] = v["n"]; r["debe_viajes"] = v["m"]   # se cuentan aparte: son viajes, no entregas
    return r | {"zonas": zonas, "ultimo_pago": ult}


@app.get("/mis-entregas", response_class=HTMLResponse)
def mis_entregas(request: Request, con=Depends(db)):
    """La pantalla del despachador: lo que le toca hoy, lo que se le debe y lo que ha entregado.
    Ve dinero, pero solo el suyo: nunca el de la empresa ni el de otro despachador."""
    u = quien_es(request)
    nombre = (u or {}).get("despachador")
    if rol_de(request) == "admin" and not nombre:
        nombre = request.query_params.get("quien", "")       # para que Cristina pueda ver cómo se ve
    if not nombre: return RedirectResponse("/inicio", status_code=303)
    hoy = datetime.date.today()
    r = resumen_despachador(con, nombre, hoy)
    ruta = ruta_despachador(con, nombre, hoy.isoformat())
    hist = con.execute("""SELECT o.numero, COALESCE(o.fecha_entrega, substr(o.creado_en,1,10)) fecha,
                          COALESCE(o.delivery,0) pago, o.estado, o.despachador_pagado,
                          COALESCE(NULLIF(c.nombre_pila,''), c.nombre) quien
                          FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id
                          WHERE o.despachador=? AND o.estado!='cancelada' AND o.origen_excel=0
                          ORDER BY fecha DESC, o.id DESC LIMIT 60""", (nombre,)).fetchall()
    mes = hoy.strftime("%Y-%m")
    pagos = con.execute("SELECT fecha, monto, entregas FROM pagos_despachador WHERE despachador=? ORDER BY fecha DESC LIMIT 12", (nombre,)).fetchall()
    return render(request, "mis_entregas.html", seccion="mis_entregas", quien=nombre, r=r, ruta=ruta,
                  ruta_cobrar=sum(f["cobrar"] for f in ruta),
                  hist=hist, pagos=pagos, hoy_iso=hoy.isoformat(),
                  ganado_mes=round(sum(h["pago"] for h in hist if (h["fecha"] or "")[:7] == mes), 2),
                  ganado_todo=round(sum(h["pago"] for h in hist), 2),
                  n_entregadas=sum(1 for h in hist if h["estado"] == "entregada"))


@app.post("/mis-entregas/{oid}/incidencia")
def mi_incidencia(request: Request, oid: int, tipo: str = Form("Otro"), descripcion: str = Form(""), con=Depends(db)):
    """El despachador cuenta lo que pasó en la puerta. Es quien lo vio."""
    u = quien_es(request)
    if not (u and u["despachador"]): return RedirectResponse("/inicio", status_code=303)
    o = con.execute("SELECT despachador FROM ordenes WHERE id=?", (oid,)).fetchone()
    if not o or o["despachador"] != u["despachador"]: return RedirectResponse("/mis-entregas", status_code=303)
    con.execute("INSERT INTO incidencias (orden_id,clase,tipo,descripcion,responsable,autor_id) VALUES (?,?,?,?,?,?)",
                (oid, "incidencia", tipo, descripcion.strip(), u["despachador"], u["id"]))
    registrar(con, oid, u["id"], "incidencia", f"{tipo}: {descripcion.strip()[:120]}")
    con.commit(); return RedirectResponse("/mis-entregas", status_code=303)


@app.get("/despachadores", response_class=HTMLResponse)
def despachadores(request: Request, q: str = "", con=Depends(db)):
    hoy = datetime.date.today()
    rows = [dict(d) | resumen_despachador(con, d["nombre"], hoy) for d in con.execute("SELECT * FROM despachadores WHERE nombre LIKE ? ORDER BY activo DESC, nombre", (f"%{q}%",))]
    return render(request, "despachadores.html", seccion="despachadores", despachadores=rows, q=q)


def ruta_despachador(con, nombre, hoy):
    """La lista que se le manda al despachador por WhatsApp: a quién, dónde y qué lleva.
    Va el nombre de pila, la dirección y el teléfono — sin eso no puede entregar. No va cédula,
    ni correo, ni el total de la orden. El monto solo aparece si tiene que cobrarlo en la puerta."""
    filas = []
    for o in con.execute("""SELECT o.id, o.numero, o.total, o.estado_pago, o.tipo_entrega,
                            COALESCE(NULLIF(c.nombre_pila,''), c.nombre) quien, c.telefono,
                            o.direccion, o.maps, o.zona, o.ciudad, c.id cid
                            FROM ordenes o JOIN clientes c ON c.id=o.cliente_id
                            WHERE o.despachador=? AND o.estado IN ('pendiente','en_ruta') AND o.origen_excel=0
                              AND o.tipo_entrega NOT IN ('pickup','distribuidor')
                              AND COALESCE(o.fecha_prometida, substr(o.creado_en,1,10)) <= ?
                            ORDER BY o.zona, o.id""", (nombre, hoy)):
        d = dict(o)
        if not _sirve(d["direccion"]) or not d["maps"]:      # la orden hereda la dirección del cliente si no trae una
            dd = con.execute("SELECT direccion, maps FROM direcciones WHERE cliente_id=? ORDER BY principal DESC, id LIMIT 1", (d["cid"],)).fetchone()
            if dd:
                if not _sirve(d["direccion"]): d["direccion"] = dd["direccion"]
                if not d["maps"] and not _sirve(o["direccion"]): d["maps"] = dd["maps"]
        pagado = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM pagos WHERE orden_id=? AND estado='confirmado'", (d["id"],)).fetchone()[0]
        falta = round((d["total"] or 0) - pagado, 2)
        # solo lo que el despachador tiene que cobrar en la puerta; lo demás no es asunto suyo
        d["cobrar"] = falta if (d["estado_pago"] in ("contra_entrega", "sin_pago", "abonada", "rechazado") and falta > 0) else 0
        d["que_lleva"] = lo_que_lleva(con, d["id"], lambda ya, n, t: f"{ya + 1}/{t}" if n <= 1 else f"{ya + 1}-{ya + n}/{t}")[0]
        filas.append(d)
    return filas


def texto_ruta(filas, hoy):
    """El mensaje tal cual se le manda por WhatsApp."""
    out = [f"Decopet · {fecha_larga(hoy)}", ""]
    for i, f in enumerate(filas, 1):
        out.append(f"{i}. {f['quien']}" + (f" · {f['telefono']}" if f["telefono"] else ""))
        if f["direccion"]: out.append(f"   {f['direccion']}")
        if f["maps"]: out.append(f"   {f['maps']}")
        out.append(f"   {f['que_lleva']}")
        if f["cobrar"]: out.append(f"   COBRAR ${f['cobrar']:,.2f}")
        out.append("")
    cobros = sum(f["cobrar"] for f in filas)
    if cobros: out.append(f"Total a cobrar: ${cobros:,.2f}")
    return "\n".join(out).strip()


@app.get("/despachadores/{did}", response_class=HTMLResponse)
def despachador_ficha(request: Request, did: int, con=Depends(db)):
    d = con.execute("SELECT * FROM despachadores WHERE id=?", (did,)).fetchone()
    if not d: return RedirectResponse("/despachadores", status_code=303)
    hoy = datetime.date.today(); r = resumen_despachador(con, d["nombre"], hoy)
    pendientes = con.execute("""SELECT o.id, o.numero, COALESCE(o.fecha_entrega, substr(o.creado_en,1,10)) fecha, COALESCE(o.delivery, 0) pago, o.zona, o.ciudad, o.estado, c.nombre cliente
                                FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id
                                WHERE o.despachador=? AND o.estado!='cancelada' AND o.despachador_pagado=0 AND o.origen_excel=0 ORDER BY fecha DESC, o.id DESC""", (d["nombre"],)).fetchall()
    en_curso = con.execute("""SELECT o.id, o.numero, COALESCE(o.fecha_entrega, substr(o.creado_en,1,10)) fecha, o.delivery, o.zona, o.ciudad, o.estado, c.nombre cliente
                              FROM ordenes o LEFT JOIN clientes c ON c.id=o.cliente_id WHERE o.despachador=? AND o.estado IN ('pendiente','en_ruta') ORDER BY fecha""", (d["nombre"],)).fetchall()
    pagos = con.execute("SELECT * FROM pagos_despachador WHERE despachador=? ORDER BY fecha DESC, id DESC LIMIT 30", (d["nombre"],)).fetchall()
    zonas_todas = con.execute("""SELECT COALESCE(NULLIF(zona,''), 'Sin zona') z, COUNT(*) n, SUM(COALESCE(delivery, 0)) monto FROM ordenes
                                 WHERE despachador=? AND estado!='cancelada' AND origen_excel=0 AND despachador_pagado=0 GROUP BY 1 ORDER BY 2 DESC""", (d["nombre"],)).fetchall()
    viajes = con.execute("""SELECT v.*, (SELECT COUNT(*) FROM ordenes o WHERE o.viaje_id=v.id) n_ordenes
                            FROM viajes_agencia v WHERE v.despachador=? AND v.pagado=0 ORDER BY v.fecha DESC, v.id DESC""", (d["nombre"],)).fetchall()
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
                                   WHERE despachador=?""", (d["nombre"],)).fetchone()
    record["viajes"] = viajes_hechos["n"]; record["viajes_monto"] = round(viajes_hechos["m"], 2)
    return render(request, "despachador.html", seccion="despachadores", FORMAS_PAGO=FORMAS_PAGO, d=d, r=r, pendientes=pendientes, en_curso=en_curso, pagos=pagos, zonas=zonas_todas, viajes=viajes,
                  ruta=ruta, ruta_texto=texto_ruta(ruta, hoy), ruta_cobrar=sum(f["cobrar"] for f in ruta),
                  hist=hist, record=record)


@app.post("/despachadores/{did}/pagar")
async def despachador_pagar(request: Request, did: int, con=Depends(db)):
    """Le pagaste al despachador: las entregas marcadas quedan saldadas y se guarda el pago."""
    if not solo_admin(request): return RedirectResponse(f"/despachadores/{did}", status_code=303)
    f = await request.form(); d = con.execute("SELECT * FROM despachadores WHERE id=?", (did,)).fetchone()
    ids = [int(x) for x in f.getlist("orden_id")]
    vids = [int(x) for x in f.getlist("viaje_id")]
    if d and (ids or vids):
        monto = 0.0
        if ids:
            q = ",".join("?" * len(ids))
            monto += con.execute(f"SELECT COALESCE(SUM(COALESCE(delivery, 0)),0) FROM ordenes WHERE id IN ({q}) AND despachador=? AND despachador_pagado=0", (*ids, d["nombre"])).fetchone()[0]
        if vids:
            qv = ",".join("?" * len(vids))
            monto += con.execute(f"SELECT COALESCE(SUM(monto),0) FROM viajes_agencia WHERE id IN ({qv}) AND despachador=? AND pagado=0", (*vids, d["nombre"])).fetchone()[0]
        uid = usuario_id(rol_de(request)); fecha = f.get("fecha") or datetime.date.today().isoformat()
        nota = (f.get("nota") or "").strip() or None
        cur = con.execute("INSERT INTO pagos_despachador (despachador, fecha, monto, entregas, nota, usuario_id) VALUES (?,?,?,?,?,?)",
                          (d["nombre"], fecha, monto, len(ids) + len(vids), nota, uid))
        if ids: con.execute(f"UPDATE ordenes SET despachador_pagado=1, despachador_pago_id=? WHERE id IN ({','.join('?' * len(ids))}) AND despachador=?", (cur.lastrowid, *ids, d["nombre"]))
        if vids: con.execute(f"UPDATE viajes_agencia SET pagado=1, pago_id=? WHERE id IN ({','.join('?' * len(vids))}) AND despachador=?", (cur.lastrowid, *vids, d["nombre"]))
        if monto > 0:   # pagarle a un despachador es un gasto: tiene que llegar a Gastos y al libro de caja
            forma = f.get("forma") or ""
            cuenta = con.execute("SELECT id FROM cuentas WHERE nombre=? AND activa=1", (FORMA_CUENTA.get(forma),)).fetchone()
            det = []
            if ids: det.append(f"{len(ids)} entrega{'s' if len(ids) != 1 else ''}")
            if vids: det.append(f"{len(vids)} viaje{'s' if len(vids) != 1 else ''} a agencia")
            con.execute("""INSERT INTO gastos (fecha, monto_usd, monto_real, moneda, categoria, subcategoria, descripcion, proveedor,
                           cantidad, cuenta_id, notas, usuario_id) VALUES (?,?,?,'USD','Despachadores','Pago semanal',?,?,?,?,?,?)""",
                        (fecha, monto, monto, " · ".join(det), d["nombre"], len(ids) + len(vids),
                         cuenta["id"] if cuenta else None, nota, uid))
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
    monto = cifra(f.get("monto")) if (f.get("monto") or "").strip() else tarifa_agencia(con, agencia)
    fecha = f.get("fecha") or datetime.date.today().isoformat()
    uid = usuario_id(rol_de(request))
    cur = con.execute("""INSERT INTO viajes_agencia (fecha, despachador, agencia, monto, pedidos, nota, usuario_id)
                         VALUES (?,?,?,?,?,?,?)""", (fecha, desp, agencia or None, monto, len(filas), (f.get("nota") or "").strip() or None, uid))
    vid = cur.lastrowid
    con.execute(f"UPDATE ordenes SET viaje_id=?, agencia=COALESCE(NULLIF(agencia,''),?) WHERE id IN ({','.join('?' * len(filas))})",
                (vid, agencia or None, *[r["id"] for r in filas]))
    for r in filas: registrar(con, r["id"], uid, "despachador", f"{desp} lo llevó a {agencia or 'la agencia'}")
    con.commit()
    return RedirectResponse(volver, status_code=303)


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
        if viejo and viejo["nombre"] != nombre:   # las órdenes y los pagos guardan el nombre
            con.execute("UPDATE ordenes SET despachador=? WHERE despachador=?", (nombre, viejo["nombre"])); con.execute("UPDATE pagos_despachador SET despachador=? WHERE despachador=?", (nombre, viejo["nombre"]))
    elif nombre:
        con.execute("INSERT OR IGNORE INTO despachadores (nombre, telefono, notas, activo) VALUES (?,?,?,1)", (nombre, normalizar_telefono(telefono) or None, notas or None))
    con.commit(); cargar_despachadores()
    return RedirectResponse(f"/despachadores/{id}" if id else "/despachadores", status_code=303)


# ------------------------------------------------------------------ TARIFAS DE DELIVERY
@app.get("/tarifas", response_class=HTMLResponse)
def tarifas(request: Request, q: str = "", con=Depends(db)):
    rows = con.execute("SELECT * FROM tarifas WHERE zona LIKE ? ORDER BY orden, tarifa, zona", (f"%{q}%",)).fetchall()
    ta = cfg_json(con, "tarifa_agencia", {}) or {}
    return render(request, "tarifas.html", seccion="tarifas", tarifas=rows, tarifas_ag={a: float(ta.get(a, ta.get("*", 5))) for a in AGENCIAS})


@app.post("/tarifas/guardar")
def tarifas_guardar(request: Request, id: int = Form(0), zona: str = Form(...), tarifa: str = Form("0"), pago: str = Form(""), notas: str = Form(""), borrar: str = Form(""), con=Depends(db)):
    if not solo_admin(request): return RedirectResponse("/tarifas", status_code=303)
    monto = float((tarifa or "0").replace(",", ".") or 0); pago_d = float(pago.replace(",", ".")) if pago.strip() else None
    if borrar and id: con.execute("DELETE FROM tarifas WHERE id=?", (id,))
    elif id: con.execute("UPDATE tarifas SET zona=?, tarifa=?, pago_despachador=?, notas=? WHERE id=?", (zona.strip(), monto, pago_d, notas.strip() or None, id))
    elif zona.strip(): con.execute("INSERT OR IGNORE INTO tarifas (zona, tarifa, pago_despachador, notas) VALUES (?,?,?,?)", (zona.strip(), monto, pago_d, notas.strip() or None))
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
                            FROM orden_lineas l JOIN productos p ON p.id=l.producto_id WHERE l.orden_id=?""", (oid,)):
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
    for o in con.execute("""SELECT o.id, COALESCE(NULLIF(c.nombre_pila,''), c.nombre) quien,
                            COALESCE(o.fecha_prometida, substr(o.creado_en,1,10)) fecha,
                            (CASE WHEN o.estado_pago IN ('sin_pago','abonada','contra_entrega','rechazado') THEN 1 ELSE 0 END) falta_cobrar
                            FROM ordenes o JOIN clientes c ON c.id=o.cliente_id
                            WHERE o.tipo_entrega='pickup' AND o.estado IN ('pendiente','en_ruta') AND o.origen_excel=0"""):
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
    def sale(quien_lleva, producto, n, agencia=False):
        g = salidas.setdefault(quien_lleva, {"quien_lleva": quien_lleva, "cosas": {}, "agencia": {}})
        d = g["agencia"] if agencia else g["cosas"]   # lo de la agencia va aparte: eso hay que embalarlo
        d[producto] = d.get(producto, 0) + n

    for o in con.execute("""SELECT o.id, o.despachador, o.tipo_entrega FROM ordenes o
                            WHERE o.tipo_entrega IN ('delivery','delivery_fuera','nacional')
                              AND o.estado IN ('pendiente','en_ruta') AND o.origen_excel=0
                              AND COALESCE(o.fecha_prometida, substr(o.creado_en,1,10)) <= ?""", (hoy,)):
        # quien lleva es quien lleva, aunque una parte vaya a la agencia: un solo Juan, no dos
        lleva = (o["despachador"] or "").strip() or "Sin despachador"
        a_agencia = o["tipo_entrega"] == "nacional"
        for l in con.execute("""SELECT COALESCE(NULLIF(l.nombre,''), p.nombre) nombre, l.cantidad, l.color, p.sku
                                FROM orden_lineas l JOIN productos p ON p.id=l.producto_id WHERE l.orden_id=?""", (o["id"],)):
            if (l["sku"] or "").startswith("PACK"):   # de un pack no sale "el pack": salen los repuestos que le tocan
                k = con.execute("SELECT tamano, entregadas_inicio FROM packs WHERE orden_id=? AND producto_id=(SELECT id FROM productos WHERE sku=?)",
                                (o["id"], l["sku"])).fetchone()
                sale(lleva, f"Repuesto {(k['tamano'] if k else '') or ''}".strip(), (k["entregadas_inicio"] if k else 1) or 1, a_agencia)
            else:
                sale(lleva, l["nombre"] + (f" {l['color']}" if l["color"] else ""), int(l["cantidad"]), a_agencia)
    for k in cargar_packs(con):
        if k["saldo"] > 0 and k["fecha_programada"] and k["fecha_programada"] <= hoy and (k["tipo_programado"] or k["tipo_entrega"]) != "pickup":
            sale((k["despachador_programado"] or "").strip() or "Sin despachador",
                 f"Repuesto {k['tamano'] or ''}".strip(), min(k["retiro_programado"] or 1, k["saldo"]))
    for r in cargar_prepagados(con):
        if r["fecha_programada"] and r["fecha_programada"] <= hoy and r["tipo_entrega"] != "pickup":
            sale((r["despachador"] or "").strip() or "Sin despachador", f"Repuesto {r['tamano'] or ''}".strip(), 1)

    for g in salidas.values():
        for k_ in ("cosas", "agencia"):
            g[k_] = " · ".join(f"{n}× {nom}" for nom, n in sorted(g[k_].items()))
    salidas = sorted(salidas.values(), key=lambda g: (g["quien_lleva"] == "Sin despachador", g["quien_lleva"]))
    llegadas = con.execute("""SELECT pr.id, pr.cantidad, pr.recibido, pr.fecha_esperada, pr.responsable, pr.pieza, pr.descripcion,
                              COALESCE(NULLIF(pr.pieza,''), p.nombre) producto FROM produccion pr LEFT JOIN productos p ON p.id=pr.producto_id
                              WHERE pr.estado NOT IN ('recibido','cancelado','cancelada')
                                AND (pr.fecha_esperada IS NULL OR pr.fecha_esperada <= ?)
                              ORDER BY COALESCE(pr.fecha_esperada,'9999'), pr.id""", (hoy,)).fetchall()
    armables = []
    for p_ in con.execute("""SELECT p.id, p.nombre,
                             (SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario m WHERE m.producto_id=p.id) listos
                             FROM productos p WHERE p.activo=1 AND EXISTS (SELECT 1 FROM receta r WHERE r.producto_id=p.id)
                             ORDER BY p.orden"""):
        # con lo que hay en el depósito, ¿cuántos se pueden armar? y sobre todo: ¿qué material es el que frena?
        materiales = []
        for r in con.execute("""SELECT i.nombre, r.cantidad necesita,
                                COALESCE((SELECT SUM(cantidad) FROM mov_inventario m WHERE m.producto_id=r.insumo_id),0) hay
                                FROM receta r JOIN productos i ON i.id=r.insumo_id WHERE r.producto_id=?""", (p_["id"],)):
            materiales.append({"nombre": r["nombre"], "hay": int(r["hay"]), "da_para": int(r["hay"] // r["necesita"]) if r["necesita"] else 0})
        tope = min(materiales, key=lambda m: m["da_para"]) if materiales else None
        armables.append(dict(p_) | {"alcanza": tope["da_para"] if tope else 0, "materiales": materiales, "tope": tope})
    notas = con.execute("SELECT * FROM notas_taller ORDER BY id DESC LIMIT 8").fetchall()
    return render(request, "taller.html", seccion="taller", pickups=pickups, salidas=salidas, llegadas=llegadas, armables=armables, notas=notas, hoy_iso=hoy, fecha_larga=fecha_larga())


@app.post("/taller/{oid}/entregado")
def taller_entregado(request: Request, oid: int, con=Depends(db)):
    """El taller entrega un pick-up: se marca solo y Cristina lo ve sin tener que preguntar."""
    if not solo_taller(request): return RedirectResponse("/operaciones", status_code=303)
    o = con.execute("SELECT tipo_entrega, estado FROM ordenes WHERE id=?", (oid,)).fetchone()
    if o and o["tipo_entrega"] == "pickup" and o["estado"] in ("pendiente", "en_ruta"):
        hoy = datetime.date.today().isoformat(); uid = usuario_id(rol_de(request))
        con.execute("UPDATE ordenes SET estado='entregada', fecha_entrega=?, actualizado_en=datetime('now','localtime') WHERE id=?", (hoy, oid))
        registrar(con, oid, uid, "estado", "Entregado en pick-up (taller)")
        con.commit()
    return RedirectResponse("/taller", status_code=303)


@app.post("/taller/pack/{pid}/entregado")
def taller_pack_entregado(request: Request, pid: int, con=Depends(db)):
    """El cliente vino a buscar los repuestos de su pack y el taller se los entregó."""
    if not solo_taller(request): return RedirectResponse("/operaciones", status_code=303)
    k = next((k for k in cargar_packs(con) if k["id"] == pid), None)
    if k and k["saldo"] > 0:
        hoy = datetime.date.today().isoformat(); uid = usuario_id(rol_de(request))
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
                   WHERE id=? AND entregado_en IS NULL""", (datetime.date.today().isoformat(), usuario_id(rol_de(request)), rid))
    con.commit()
    return RedirectResponse("/taller", status_code=303)


@app.post("/taller/nota")
def taller_nota(request: Request, texto: str = Form(""), con=Depends(db)):
    """Un aviso del taller para Cristina: algo llegó mal, se acabó un material, pasó algo."""
    if not solo_taller(request): return RedirectResponse("/operaciones", status_code=303)
    if texto.strip():
        con.execute("INSERT INTO notas_taller (fecha, texto, usuario_id) VALUES (?,?,?)",
                    (datetime.date.today().isoformat(), texto.strip()[:400], usuario_id(rol_de(request))))
        con.commit()
    return RedirectResponse("/taller", status_code=303)


@app.get("/taller/avisos", response_class=HTMLResponse)
def taller_avisos(request: Request, con=Depends(db)):
    """Lo que el taller te avisó. Se queda aquí hasta que lo resuelvas, no se borra al verlo."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    filas = con.execute("""SELECT n.*, u.nombre quien, pr.pieza, pr.responsable, pr.cantidad, pr.recibido
                           FROM notas_taller n LEFT JOIN usuarios u ON u.id=n.usuario_id
                           LEFT JOIN produccion pr ON pr.id=n.produccion_id
                           ORDER BY n.resuelto, n.id DESC""").fetchall()
    return render(request, "avisos.html", seccion="avisos", avisos=filas,
                  pendientes=sum(1 for f in filas if not f["resuelto"]))


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
def taller_armar(request: Request, producto_id: int = Form(...), cantidad: str = Form("0"), con=Depends(db)):
    """Armaron porches por adelantado: sale la materia prima y entran porches listos.
    Así la caja de madera no se cuenta dos veces (una como caja y otra al vender)."""
    if not solo_taller(request): return RedirectResponse("/operaciones", status_code=303)
    n = int(cifra(cantidad)) if cantidad.strip() else 0
    p = con.execute("SELECT id, nombre FROM productos WHERE id=? AND activo=1", (producto_id,)).fetchone()
    if p and n > 0:
        hoy = datetime.date.today().isoformat(); uid = usuario_id(rol_de(request))
        for r in con.execute("SELECT insumo_id, cantidad FROM receta WHERE producto_id=?", (producto_id,)):
            con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, nota, usuario_id) VALUES (?,?,?,?,?,?)",
                        (r["insumo_id"], hoy, "salida", -int(r["cantidad"] * n), f"para armar {n}× {p['nombre']}", uid))
        con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, nota, usuario_id) VALUES (?,?,?,?,?,?)",
                    (producto_id, hoy, "entrada", n, "armado en el taller", uid))
        con.commit()
    return RedirectResponse("/taller", status_code=303)


@app.post("/taller/llegada/{pid}")
def taller_llegada(request: Request, pid: int, cantidad: str = Form("0"), nota: str = Form(""), con=Depends(db)):
    """Llegó un pedido: el taller confirma cuántos llegaron de verdad (pueden ser menos de los esperados)."""
    if not solo_taller(request): return RedirectResponse("/operaciones", status_code=303)
    r = con.execute("SELECT * FROM produccion WHERE id=?", (pid,)).fetchone()
    n = int(cifra(cantidad)) if cantidad.strip() else 0
    if r and n > 0:
        hoy = datetime.date.today().isoformat(); uid = usuario_id(rol_de(request))
        terminado = next((ok for (nom, _, ok) in PIEZAS_PRODUCCION if nom == (r["pieza"] or "")), True)
        if terminado and r["producto_id"]:
            con.execute("INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, nota, usuario_id) VALUES (?,?,?,?,?,?)",
                        (r["producto_id"], hoy, "entrada", n, f"producción #{pid}" + (f" · {r['responsable']}" if r["responsable"] else "") + " · confirmado en taller" + (f" · {nota.strip()}" if nota.strip() else ""), uid))
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
def analitica(request: Request, con=Depends(db)):
    """Qué se vende y qué no. Lo de los clientes vive en Clientes, no se repite aquí."""
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)
    productos = con.execute("""SELECT l.nombre, SUM(l.cantidad) u, SUM(l.total) fact,
                               MAX(substr(o.creado_en,1,10)) ultima,
                               (SELECT p.costo FROM productos p WHERE p.nombre=l.nombre) costo
                               FROM orden_lineas l JOIN ordenes o ON o.id=l.orden_id
                               WHERE o.estado!='cancelada' AND l.producto_id IS NOT NULL
                               GROUP BY l.nombre ORDER BY fact DESC""").fetchall()
    productos = [dict(p) | {"deja": round(p["fact"] - (p["costo"] or 0) * p["u"], 2) if p["costo"] else None} for p in productos]
    quietos = con.execute("""SELECT p.nombre, (SELECT MAX(substr(o.creado_en,1,10)) FROM orden_lineas l
                             JOIN ordenes o ON o.id=l.orden_id WHERE l.producto_id=p.id AND o.estado!='cancelada') ultima
                             FROM productos p WHERE p.activo=1 AND p.tipo='producto' AND p.categoria NOT IN ('opcion')
                             ORDER BY ultima IS NOT NULL, ultima LIMIT 12""").fetchall()
    tot_u = sum(p["u"] for p in productos); tot_f = sum(p["fact"] for p in productos)
    return render(request, "analitica.html", seccion="analitica", productos=productos, quietos=quietos,
                  tot_u=tot_u, tot_f=tot_f)


@app.get("/{seccion}", response_class=HTMLResponse)
def pendiente(request: Request, seccion: str):
    if seccion not in SECCIONES: return RedirectResponse("/ordenes", status_code=303)
    if not solo_admin(request): return RedirectResponse("/operaciones", status_code=303)   # marketing, analítica y configuración son de Cristina
    return render(request, "pendiente.html", seccion=seccion, titulo=SECCIONES[seccion])
