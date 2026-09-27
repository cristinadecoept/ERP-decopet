"""Tasa oficial BCV: se lee de bcv.org.ve (fuente oficial) y, si falla, de un respaldo. Guarda historial diario y nunca recalcula órdenes viejas."""
import re, json, datetime, sqlite3, threading, time, warnings
import requests, urllib3
urllib3.disable_warnings()
warnings.filterwarnings("ignore")

SQL = """CREATE TABLE IF NOT EXISTS tasas (
  id INTEGER PRIMARY KEY, fecha_valor TEXT NOT NULL, valor REAL NOT NULL, fuente TEXT NOT NULL,
  obtenido_en TEXT DEFAULT (datetime('now','localtime')), manual INTEGER DEFAULT 0, usuario_id INTEGER, nota TEXT,
  UNIQUE(fecha_valor, fuente));"""
MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}


def _bcv_oficial():
    """Lee la página del BCV. El certificado del BCV no está bien configurado (problema de ellos), por eso verify=False."""
    html = requests.get("https://www.bcv.org.ve/", timeout=25, verify=False, headers={"User-Agent": "Mozilla/5.0 (Decopet)"}).text
    m = re.search(r'id="dolar".*?<strong[^>]*>\s*([\d.,]+)', html, re.S)
    f = re.search(r'Fecha Valor:.*?content="(\d{4}-\d{2}-\d{2})', html, re.S)
    if not m: raise ValueError("No se encontró la tasa USD en la página del BCV")
    valor = float(m.group(1).replace(".", "").replace(",", "."))
    return f.group(1) if f else datetime.date.today().isoformat(), valor, "BCV (bcv.org.ve)"


def _respaldo():
    d = requests.get("https://ve.dolarapi.com/v1/dolares/oficial", timeout=15).json()
    return d["fechaActualizacion"][:10], float(d["promedio"]), "Respaldo: dolarapi (replica BCV)"


def actualizar(db_path, forzar=False):
    con = sqlite3.connect(db_path); con.execute(SQL)
    ahora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    estado = {"ultimo_intento": ahora, "error": None}
    for fn in (_bcv_oficial, _respaldo):
        try:
            fecha, valor, fuente = fn()
            con.execute("INSERT OR IGNORE INTO tasas (fecha_valor, valor, fuente) VALUES (?,?,?)", (fecha, valor, fuente))
            estado["fuente"] = fuente; estado["valor"] = valor; estado["fecha_valor"] = fecha
            break
        except Exception as e:
            estado["error"] = (estado["error"] or "") + f"{fn.__name__}: {type(e).__name__} {str(e)[:80]}. "
    con.execute("INSERT OR REPLACE INTO config (clave, valor) VALUES ('bcv_estado', ?)", (json.dumps(estado),))
    con.commit(); con.close()
    return estado


def tasa_actual(con):
    """La tasa que se cobra: la del próximo día hábil con tasa publicada.
    En fin de semana no vale la del viernes, sino la del lunes — que el BCV publica el viernes por la tarde.
    Entre semana esa tasa es la de hoy, así que la misma regla sirve para los dos casos."""
    con.execute(SQL)
    hoy = datetime.date.today().isoformat()
    r = con.execute("""SELECT * FROM tasas WHERE fecha_valor >= ?
                       ORDER BY fecha_valor ASC, manual DESC, CASE WHEN fuente LIKE 'BCV%' THEN 0 ELSE 1 END, id DESC LIMIT 1""", (hoy,)).fetchone()
    if not r:   # todavía no publicaron la próxima: se usa la última que haya
        r = con.execute("""SELECT * FROM tasas ORDER BY fecha_valor DESC, manual DESC, CASE WHEN fuente LIKE 'BCV%' THEN 0 ELSE 1 END, id DESC LIMIT 1""").fetchone()
    est = con.execute("SELECT valor FROM config WHERE clave='bcv_estado'").fetchone()
    estado = json.loads(est[0]) if est else {}
    if not r: return {"valor": None, "fecha_valor": None, "fuente": None, "estado": estado, "alerta": "Sin tasa cargada"}
    d = dict(zip(r.keys(), r)) if hasattr(r, "keys") else dict(id=r[0], fecha_valor=r[1], valor=r[2], fuente=r[3], obtenido_en=r[4], manual=r[5])
    alerta = None
    if estado.get("error") and not estado.get("valor"): alerta = "No se pudo actualizar en el último intento"
    elif (datetime.date.today() - datetime.date.fromisoformat(d["fecha_valor"])).days > 4: alerta = "La tasa tiene más de 4 días"
    d["es_futura"] = d["fecha_valor"] > datetime.date.today().isoformat()
    d["estado"] = estado; d["alerta"] = alerta
    return d


def programar(db_path, cada_horas=6):
    """Actualiza al arrancar y luego cada N horas, en segundo plano."""
    def bucle():
        while True:
            try: actualizar(db_path)
            except Exception: pass
            time.sleep(cada_horas * 3600)
    threading.Thread(target=bucle, daemon=True).start()
