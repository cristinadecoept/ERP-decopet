"""Carga la ficha de clientes exportada de Airtable ("Clientes-Ficha clientes.csv") en la sección Clientes.
Uso:  ./.venv/bin/python -m plataforma.importar_clientes "/ruta/archivo.csv" [--cargar]
Reglas acordadas con Cristina (20 sep 2026): correo/teléfono/ciudad vacíos → "Pendiente" (resaltado y filtrable);
Duwu es cliente B2B; los perros con apellido son reales; los nombres con paréntesis se dejan tal cual."""
import sys, csv, re, sqlite3, collections
from pathlib import Path

DB = Path(__file__).parent / "data" / "plataforma.db"
NO_MAIL = {"pendiente", "no tiene", "no tiene disponible", "no quiere", "no", "n/a", "na", "-", "x"}
EXTERIOR = ("1", "34", "52", "54", "56", "32", "39", "44", "49", "57", "51", "506", "507", "351")


def n(s): return " ".join((s or "").split())


def telefono(s):
    d = re.sub(r"\D", "", s or "")
    if not d: return None, None
    if d.startswith("58") and len(d) == 12: d = "0" + d[2:]
    if len(d) == 11 and d.startswith("0") and d[1] in "24": return f"{d[:4]}-{d[4:]}", None
    if len(d) == 10 and d[0] in "24": return f"0{d[:3]}-{d[3:]}", None
    if len(d) == 9 and d[0] in "24": return f"0{d[:3]}-{d[3:]}", "teléfono incompleto (9 dígitos)"
    for cc in sorted(EXTERIOR, key=len, reverse=True):
        if d.startswith(cc) and len(d) >= 10: return f"+{cc} {d[len(cc):]}", None
    return s.strip(), "teléfono con formato raro"


def leer(ruta):
    rows = list(csv.DictReader(open(ruta, "rb").read().decode("utf-8-sig").splitlines()))
    out = []; avisos = collections.defaultdict(list)
    for i, r in enumerate(rows, start=2):
        nombre = n(r["Nombre"])
        if not nombre: avisos["fila vacía (saltada)"].append(i); continue
        tel_x, mail = n(r["Teléfono"]), n(r["Mail"]).replace(" @", "@").replace("@ ", "@")
        if "@" in tel_x and re.fullmatch(r"[\d\s()\-+]+", mail or "x"):   # venían al revés
            tel_x, mail = mail, tel_x; avisos["teléfono y correo estaban al revés (corregido)"].append((i, nombre))
        elif "@" in tel_x: mail = mail or tel_x; tel_x = ""; avisos["correo estaba en teléfono"].append((i, nombre))
        elif mail and re.fullmatch(r"[\d\s()\-+]{7,}", mail) and not tel_x: tel_x, mail = mail, ""; avisos["teléfono estaba en correo (corregido)"].append((i, nombre))
        tel, av = telefono(tel_x)
        if av: avisos[av].append((i, nombre, tel))
        if mail.lower() in NO_MAIL or (mail and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", mail)):
            if mail and mail.lower() not in NO_MAIL: avisos["correo inválido → Pendiente"].append((i, nombre, mail))
            mail = ""
        ced = n(r["Cedula"]).replace(".", "")
        if ced and "@" in ced and not mail: mail = ced if "." in ced.split("@")[1] else ced.replace("hotmailcom", "hotmail.com").replace("gmailcom", "gmail.com"); avisos["correo estaba en cédula (movido)"].append((i, nombre)); ced = ""
        if ced and not re.match(r"^[VvEeJjGgPp]?-?\d{5,10}$", ced): avisos["cédula descartada"].append((i, nombre, ced)); ced = ""
        if ced and re.match(r"^\d{10}$", ced): avisos["cédula descartada"].append((i, nombre, ced)); ced = ""   # era un teléfono
        if ced: ced = (ced if "-" in ced else ("V-" + ced if ced[0].isdigit() else ced[0].upper() + "-" + ced[1:])).upper()
        ver, tam = n(r["Version Porche"]).upper(), n(r["Tamano Porche"])
        ver = {"PRO": "PRO", "BASICO": "Básico", "NO TIENE": "no", "": None}.get(ver, ver); tam = tam if tam in ("Mediano", "Grande") else None
        perros = [p.strip() for p in re.split(r"[,;/]| y ", n(r["Mascotas"])) if p.strip()]
        out.append(dict(fila=i, nombre=nombre, telefono=tel, correo=mail, cedula=ced or None, ciudad=n(r["Ciudad"]), direccion=n(r["Dirección habitual"]), gps=n(r["GPS"]),
                        notas=n(r["Notas"]), perros=perros, porche_version=ver, porche_tamano=tam, canal="duwu" if nombre.lower() == "duwu" else None))
    dup = collections.Counter(c["nombre"].lower() for c in out)
    avisos["nombre repetido (se cargan los dos)"] = [k for k, v in dup.items() if v > 1]
    return out, avisos


def cargar(clientes):
    con = sqlite3.connect(DB)
    cols = [r[1] for r in con.execute("PRAGMA table_info(clientes)")]
    for c in ("porche_version", "porche_tamano"):
        if c not in cols: con.execute(f"ALTER TABLE clientes ADD COLUMN {c} TEXT")
    for c in clientes:
        partes = c["nombre"].split(" ", 1)
        cur = con.execute("INSERT INTO clientes (nombre_pila,apellido,nombre,telefono,cedula,correo,ciudad,canal_habitual,origen_excel,porche_version,porche_tamano) VALUES (?,?,?,?,?,?,?,?,1,?,?)",
                          (partes[0], partes[1] if len(partes) > 1 else None, c["nombre"], c["telefono"] or "Pendiente", c["cedula"], c["correo"] or "Pendiente", c["ciudad"] or "Pendiente", c["canal"], c["porche_version"], c["porche_tamano"]))
        cid = cur.lastrowid
        if c["direccion"] or c["gps"]:
            con.execute("INSERT INTO direcciones (cliente_id,etiqueta,direccion,ciudad,maps,principal) VALUES (?,?,?,?,?,1)", (cid, "Principal", c["direccion"] or "(solo GPS)", c["ciudad"] or None, c["gps"] or None))
        for p in c["perros"]: con.execute("INSERT INTO mascotas (cliente_id,nombre) VALUES (?,?)", (cid, p))
        if c["notas"]: con.execute("INSERT INTO notas_cliente (cliente_id,tipo,texto,mostrar_en_orden,mostrar_logistica,autor_id) VALUES (?,?,?,1,1,1)", (cid, "general", c["notas"]))
    con.commit(); return len(clientes)


if __name__ == "__main__":
    clientes, avisos = leer(sys.argv[1])
    print(f"Clientes: {len(clientes)}")
    for k, v in avisos.items(): print(f"- {k}: {len(v)} → {v[:6]}")
    print("sin teléfono →", sum(1 for c in clientes if not c["telefono"]), "| sin correo →", sum(1 for c in clientes if not c["correo"]), "| sin ciudad →", sum(1 for c in clientes if not c["ciudad"]))
    if "--cargar" in sys.argv: print("Cargados", cargar(clientes))
