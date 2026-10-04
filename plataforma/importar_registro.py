"""Carga la hoja VENTAS del Excel "DECOPET - Historial de Ventas" en el Registro de ventas TAL CUAL.
Cristina (3 oct 2026): es solo un registro; los datos inconsistentes NO se corrigen ni se cambian.
- Cliente, SKU, precio, cantidad, facturación, forma de pago, cuotas y orden # se guardan como vienen (vacío = vacío).
- La fecha se guarda también como venía (fecha_original). Para poder ordenar y filtrar por mes se lee la fecha:
  si es fecha de Excel se usa; si es texto tipo "18/03/23" se lee ese día. Si no se puede leer, queda sin fecha
  (no se inventa) y se muestra el texto original. Años imposibles (2003, 0203, 2032, 2022) toman el año de la fila anterior.
Uso:  ./.venv/bin/python -m plataforma.importar_registro "/ruta/archivo.xlsx" [--cargar]
Con --cargar VACÍA el registro (solo las filas del Excel) y lo vuelve a llenar."""
import sys, re, os, datetime, sqlite3
from pathlib import Path
import openpyxl

DB = Path(os.environ.get("DECOPET_DATOS") or (Path(__file__).parent / "data")) / "plataforma.db"
COLS_EXTRA = [("fecha_original", "TEXT"), ("inicial", "REAL"), ("cuota1", "REAL"), ("cuota2", "REAL"), ("cuota3", "REAL"), ("orden_excel", "TEXT")]


def fecha_iso(v):
    """Solo lee la fecha (no la corrige). None si no se puede leer."""
    if isinstance(v, datetime.datetime): return v.date().isoformat()
    if isinstance(v, datetime.date): return v.isoformat()
    if isinstance(v, str):
        m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*$", v)
        if m:
            dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
            yy = yy + 2000 if yy < 100 else yy
            try: return datetime.date(yy, mm, dd).isoformat()
            except ValueError: return None
    return None


def texto(v):
    if v is None: return None
    if isinstance(v, datetime.datetime): return v.strftime("%d/%m/%Y")
    if isinstance(v, float) and v.is_integer(): return str(int(v))
    return str(v)


def numero(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def leer(ruta):
    ws = openpyxl.load_workbook(ruta, data_only=True, read_only=True)["VENTAS"]
    filas, sin_fecha = [], []
    for i, r in enumerate(ws.iter_rows(values_only=True), start=1):
        if i == 1: continue
        r = list(r) + [None] * 14
        if not any(x not in (None, "") for x in r[:9]): continue   # fila vacía
        f = fecha_iso(r[0])
        # año mal tipeado (2022, 2003, 0203, 2032…): el Excel arranca en marzo 2023 y va en orden, así que
        # se toma el año de la fila anterior. Cristina, 3 oct 2026. La fecha escrita se guarda igual en fecha_original.
        previa = next((x["fecha"] for x in reversed(filas) if x["fecha"]), None)
        if f and previa and not (2023 <= int(f[:4]) <= datetime.date.today().year): f = previa[:4] + f[4:]
        if f is None: sin_fecha.append((i, r[0]))
        filas.append(dict(fila=i, fecha=f or "", fecha_original=texto(r[0]), cliente=texto(r[3]), producto=texto(r[4]),
                          precio=numero(r[5]), cantidad=numero(r[6]), facturacion=numero(r[7]), forma_pago=texto(r[8]),
                          inicial=numero(r[9]), cuota1=numero(r[10]), cuota2=numero(r[11]), cuota3=numero(r[12]), orden_excel=texto(r[13])))
    return filas, sin_fecha


def cargar(filas):
    con = sqlite3.connect(DB)
    cols = [c[1] for c in con.execute("PRAGMA table_info(registro_ventas)")]
    for c, t in COLS_EXTRA:
        if c not in cols: con.execute(f"ALTER TABLE registro_ventas ADD COLUMN {c} {t}")
    con.execute("DELETE FROM registro_ventas WHERE origen='excel'")
    con.executemany("""INSERT INTO registro_ventas (fecha,fecha_original,cliente,producto,precio,cantidad,facturacion,forma_pago,inicial,cuota1,cuota2,cuota3,orden_excel,origen,fila_excel)
                       VALUES (:fecha,:fecha_original,:cliente,:producto,:precio,:cantidad,:facturacion,:forma_pago,:inicial,:cuota1,:cuota2,:cuota3,:orden_excel,'excel',:fila)""", filas)
    con.commit(); return len(filas)


if __name__ == "__main__":
    filas, sin_fecha = leer(sys.argv[1])
    print(f"Filas: {len(filas)} · facturación sumada: {sum(f['facturacion'] or 0 for f in filas):,.2f}")
    print(f"Sin fecha legible (se guardan con su texto original): {len(sin_fecha)} → {sin_fecha[:10]}")
    print("Años que trae:", sorted({f['fecha'][:4] for f in filas if f['fecha']}))
    if "--cargar" in sys.argv: print("Cargadas", cargar(filas))
