"""Carga "Mascotas-Mascotas.csv" (Airtable): completa raza y cumpleaños de los perros ya cargados con los clientes.
Reglas (Cristina, 20 sep 2026): año 2000 o de 3 cifras = año desconocido → solo día/mes; fecha futura → se guarda día/mes y queda "por revisar";
sin cliente → se salta (son repetidos); sin raza → "Pendiente"; Frenchie → Bulldog Frances."""
import sys, csv, re, sqlite3, datetime, collections
from pathlib import Path
DB = Path(__file__).parent / "data" / "plataforma.db"
def n(s): return " ".join((s or "").split())
def fecha(s):
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{3,4})$", n(s))
    if not m: return None, None, False
    mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try: datetime.date(2024, mm, dd)
    except ValueError: return None, None, True
    md = f"{mm:02d}-{dd:02d}"
    if yy == 2000 or yy < 1000: return None, md, False
    d = datetime.date(yy, mm, dd)
    if d > datetime.date.today(): return None, md, True
    return d.isoformat(), md, False

def main(ruta, cargar=False):
    rows = list(csv.DictReader(open(ruta, "rb").read().decode("utf-8-sig").splitlines()))
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    cols = [r[1] for r in con.execute("PRAGMA table_info(mascotas)")]
    if "cumple_mes_dia" not in cols: con.execute("ALTER TABLE mascotas ADD COLUMN cumple_mes_dia TEXT")
    if "revisar" not in cols: con.execute("ALTER TABLE mascotas ADD COLUMN revisar INTEGER DEFAULT 0")
    cli = {n(r["nombre"]).lower(): r["id"] for r in con.execute("SELECT id, nombre FROM clientes")}
    st = collections.Counter(); revisar = []; sin_raza = []
    for i, r in enumerate(rows, start=2):
        nombre, raza, cliente = n(r["Nombre"]), n(r["Raza"]), n(r["Cliente"])
        if not cliente or cliente.lower() not in cli: st["saltado (sin cliente)"] += 1; continue
        if raza.lower() == "frenchie": raza = "Bulldog Frances"
        if not raza: raza = "Pendiente"; sin_raza.append((nombre, cliente))
        fn, md, rev = fecha(r["Cumpleanos"])
        if rev: revisar.append((nombre, cliente, n(r["Cumpleanos"])))
        cid = cli[cliente.lower()]
        m = con.execute("SELECT id, fecha_nacimiento FROM mascotas WHERE cliente_id=? AND lower(trim(nombre))=?", (cid, nombre.lower())).fetchone()
        if not cargar: st["actualizar" if m else "crear"] += 1; continue
        if m:
            # si ya tenía fecha completa y esta fila trae solo día/mes, no la pisamos
            con.execute("UPDATE mascotas SET raza=?, fecha_nacimiento=COALESCE(?, fecha_nacimiento), cumple_mes_dia=COALESCE(?, cumple_mes_dia), revisar=? WHERE id=?", (raza, fn, md, 1 if rev else 0, m["id"]))
            st["actualizado"] += 1
        else:
            con.execute("INSERT INTO mascotas (cliente_id,nombre,raza,fecha_nacimiento,cumple_mes_dia,revisar) VALUES (?,?,?,?,?,?)", (cid, nombre, raza, fn, md, 1 if rev else 0)); st["creado"] += 1
    if cargar: con.commit()
    print(dict(st)); print("por revisar:", len(revisar), revisar); print("sin raza:", len(sin_raza), sin_raza)

if __name__ == "__main__": main(sys.argv[1], "--cargar" in sys.argv)
