"""Baja de Airtable las tablas que el ERP necesita y las deja como los CSV que ya entienden los importadores
(plataforma/importar_clientes.py, importar_mascotas.py, importar_pedidos.py).

SOLO LEE: usa únicamente GET. No hay forma de que este script cambie nada en Airtable.

Qué hace falta (una vez), en un archivo .env en la raíz del proyecto (está en .gitignore, nunca se sube):
    AIRTABLE_TOKEN=pat...        token personal con permisos data.records:read y schema.bases:read, solo a esta base
    AIRTABLE_BASE=app...         el ID de la base (está en la URL de Airtable: airtable.com/appXXXX/...)

Uso:
    python scripts/airtable_descargar.py --esquema            solo muestra las tablas, vistas y campos que hay hoy
    python scripts/airtable_descargar.py --ultimos 200        los 200 pedidos más recientes, con sus clientes y mascotas
    python scripts/airtable_descargar.py                      todo

Los CSV quedan en plataforma/data/airtable/ (fuera de git: tienen teléfonos, correos y direcciones de clientes reales)."""
import os, sys, csv, re, time, argparse, collections
from pathlib import Path
import requests

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = Path(os.environ.get("DECOPET_DATOS") or (RAIZ / "plataforma" / "data")) / "airtable"
API = "https://api.airtable.com/v0"

# (tabla en Airtable, vista que usaban para exportar el CSV, archivo que lee el importador, columnas que el importador pide siempre)
TABLAS = {
    "clientes": ("Clientes", "Ficha clientes", "Clientes-Ficha clientes.csv",
                 ["Nombre", "Teléfono", "Mail", "Cedula", "Ciudad", "Dirección habitual", "GPS", "Notas", "Mascotas", "Version Porche", "Tamano Porche"]),
    "mascotas": ("Mascotas", "Mascotas", "Mascotas-Mascotas.csv", ["Nombre", "Raza", "Cumpleanos", "Cliente"]),
    "pedidos": ("Pedidos", "Todos los pedidos", "Pedidos-Todos los pedidos.csv",
                ["ID", "Cliente", "Fecha Pago", "Status", "Forma de Pago", "Tipo de Entrega", "Canal", "Despachador", "Agencia", "Ciudad", "Dirección nueva", "Dirección habitual",
                 "Fecha Entrega", "Delivery", "Personalizacion", "Descuento", "Monto pagado", "Total", "Saldo pendiente", "Productos", "Retira el cliente", "Notas",
                 "Repuestos incluidos", "Repuestos entregados", "Repuestos pendientes", "Delivery Entregas"]),
}
ID_REGISTRO = re.compile(r"^rec[A-Za-z0-9]{14}$")


def cabeceras():
    cargar_env()
    token, base = os.environ.get("AIRTABLE_TOKEN", "").strip(), os.environ.get("AIRTABLE_BASE", "").strip()
    if not token or not base:
        sys.exit("Faltan AIRTABLE_TOKEN y AIRTABLE_BASE en el archivo .env (mira las instrucciones al principio de este archivo).")
    return {"Authorization": f"Bearer {token}"}, base


def cargar_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(RAIZ / ".env")
    except ImportError:
        pass


def leer(url, cab, params=None):
    """GET con reintento: Airtable permite 5 pedidos por segundo y responde 429 si te pasas."""
    for intento in range(6):
        r = requests.get(url, headers=cab, params=params, timeout=60)
        if r.status_code == 429: time.sleep(2 + intento * 2); continue
        if r.status_code >= 400:
            raise SystemExit(f"Airtable respondió {r.status_code} en {url.split('?')[0]}: {r.text[:300]}")
        return r.json()
    raise SystemExit("Airtable sigue diciendo 'demasiados pedidos'; vuelve a intentar en un minuto.")


def esquema(cab, base):
    return leer(f"{API}/meta/bases/{base}/tables", cab)["tables"]


def registros(cab, base, tabla, vista=None, ids_tabla=None):
    """Todos los registros de una tabla (en páginas de 100), en el orden de la vista."""
    out, params = [], {"pageSize": 100, "returnFieldsByFieldId": "false"}
    if vista: params["view"] = vista
    while True:
        d = leer(f"{API}/{base}/{requests.utils.quote(tabla)}", cab, params)
        out += d["records"]
        if not d.get("offset"): return out
        params["offset"] = d["offset"]; time.sleep(0.22)


def valor(v, nombres):
    """Un valor de Airtable → el texto que traería un CSV exportado a mano."""
    if v is None: return ""
    if isinstance(v, bool): return "checked" if v else ""
    if isinstance(v, list):
        partes = []
        for x in v:
            if x is None or isinstance(x, dict): continue            # adjuntos (comprobantes): no se bajan
            if isinstance(x, str) and ID_REGISTRO.match(x): x = nombres.get(x, "")   # vínculo a otra tabla → su nombre
            if x != "": partes.append(str(x))
        return ", ".join(partes)
    if isinstance(v, dict): return v.get("name") or v.get("email") or ""
    return str(v)


def a_filas(regs, nombres, obligatorias):
    cols = list(obligatorias)
    for r in regs:
        for k in r["fields"]:
            if k not in cols: cols.append(k)
    return cols, [{c: valor(r["fields"].get(c), nombres) for c in cols} for r in regs]


def escribir(nombre, cols, filas):
    SALIDA.mkdir(parents=True, exist_ok=True)
    with open(SALIDA / nombre, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(filas)
    return SALIDA / nombre


def principal(tabla_esquema):
    return tabla_esquema["fields"][0]["name"] if tabla_esquema["fields"] else None


def main():
    ap = argparse.ArgumentParser(description="Baja de Airtable lo que el ERP necesita (solo lectura).")
    ap.add_argument("--esquema", action="store_true", help="solo mostrar las tablas, vistas y campos que hay hoy")
    ap.add_argument("--ultimos", type=int, default=0, metavar="N", help="solo los N pedidos más recientes (y sus clientes y mascotas)")
    a = ap.parse_args()
    cab, base = cabeceras()
    tablas = esquema(cab, base)

    if a.esquema:
        for t in tablas:
            print(f"\n## {t['name']}   vistas: {', '.join(v['name'] for v in t.get('views', []))}")
            for f in t["fields"]: print(f"   - {f['name']}  ({f['type']})")
        return

    por_nombre = {t["name"]: t for t in tablas}
    for clave, (tabla, vista, _, _) in TABLAS.items():
        if tabla not in por_nombre: sys.exit(f"En esa base no hay una tabla llamada '{tabla}'. Tablas que hay: {', '.join(por_nombre)}")

    def bajar(clave):
        tabla, vista, _, _ = TABLAS[clave]
        vistas = {v["name"] for v in por_nombre[tabla].get("views", [])}
        usar = vista if vista in vistas else None
        if not usar: print(f"  aviso: la tabla {tabla} ya no tiene la vista '{vista}'; se baja sin vista (columnas puede que distintas)")
        regs = registros(cab, base, tabla, usar)
        print(f"  {tabla}: {len(regs)} registros")
        return regs

    print("Bajando de Airtable (solo lectura)…")
    clientes, mascotas, pedidos = bajar("clientes"), bajar("mascotas"), bajar("pedidos")
    nombre_de = {r["id"]: valor(r["fields"].get(principal(por_nombre["Clientes"])), {}) for r in clientes}
    nombre_de.update({r["id"]: valor(r["fields"].get(principal(por_nombre["Mascotas"])), {}) for r in mascotas})

    if a.ultimos:
        orden = lambda r: (r["fields"].get("Fecha Pago") or "", r["fields"].get("ID") or 0)
        pedidos = sorted(pedidos, key=orden, reverse=True)[:a.ultimos]
        usados = {c for p in pedidos for c in (p["fields"].get("Cliente") or [])}
        clientes = [c for c in clientes if c["id"] in usados]
        mascotas = [m for m in mascotas if set(m["fields"].get("Cliente") or []) & usados]
        pedidos.sort(key=lambda r: r["fields"].get("ID") or 0)
        print(f"  muestra: {len(pedidos)} pedidos · {len(clientes)} clientes · {len(mascotas)} mascotas")

    for clave, regs in (("clientes", clientes), ("mascotas", mascotas), ("pedidos", pedidos)):
        _, _, archivo, obligatorias = TABLAS[clave]
        cols, filas = a_filas(regs, nombre_de, obligatorias)
        faltan = [c for c in obligatorias if not any(f[c] for f in filas)]
        ruta = escribir(archivo, cols, filas)
        print(f"→ {ruta.relative_to(RAIZ) if RAIZ in ruta.parents else ruta}  ({len(filas)} filas)" +
              (f"\n   columnas que el importador pide y vienen VACÍAS: {', '.join(faltan)}" if faltan else ""))


if __name__ == "__main__":
    main()
