"""Arma una base del ERP con datos REALES de Airtable para simular la operación, y comprueba que cuadre.

    python scripts/simular_operacion.py --ultimos 200      baja de Airtable (solo lectura), arma la base y valida
    python scripts/simular_operacion.py --sin-descargar    arma la base con los CSV que ya se bajaron
    python scripts/simular_operacion.py --sobre copia.db   parte de una base real (p. ej. la de Cristina) en vez de la semilla

Pasos: (1) bajar de Airtable → (2) base de partida: la semilla (catálogo, sin clientes ni órdenes) o la que se pase con
--sobre → (3) cargar clientes, mascotas y pedidos con las reglas de los importadores → (4) comparar lo AGREGADO contra
Airtable: conteos y dinero.
La base queda en plataforma/data/simulacion/plataforma.db (fuera de git: tiene datos de clientes reales).
El archivo de --sobre nunca se modifica: se trabaja sobre una copia.
Para probarla en local:  DECOPET_DATOS=plataforma/data/simulacion  uvicorn plataforma.app:app
"""
import os, sys, csv, shutil, sqlite3, argparse, subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CSV = RAIZ / "plataforma" / "data" / "airtable"
SIM = RAIZ / "plataforma" / "data" / "simulacion"
PY = sys.executable
ENV = {**os.environ, "DECOPET_DATOS": str(SIM), "DECOPET_STAGING": "1", "PYTHONUTF8": "1"}   # STAGING: la semilla se niega a borrar si no
CONSULTAS = {"clientes": "SELECT COUNT(*) FROM clientes WHERE nombre!='Sin nombre'", "mascotas": "SELECT COUNT(*) FROM mascotas",
             "órdenes": "SELECT COUNT(*) FROM ordenes", "total vendido $": "SELECT SUM(total) FROM ordenes",
             "delivery $": "SELECT SUM(delivery) FROM ordenes", "pagado $": "SELECT SUM(monto_usd) FROM pagos"}


def correr(args, titulo, env=None):
    print(f"\n▸ {titulo}")
    r = subprocess.run([PY, *args], cwd=RAIZ, env=env or ENV, capture_output=True, text=True, encoding="utf-8")
    sal = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        print(sal[-1500:]); sys.exit(f"Falló: {titulo}")
    return sal


def m(s): return float((s or "0").replace("$", "").replace(",", "") or 0)
def leer(n): return list(csv.DictReader(open(CSV / n, encoding="utf-8-sig")))


def contar():
    """Lo que tiene la base. Si se parte de una base real, se compara solo lo que se agregó."""
    con = sqlite3.connect(SIM / "plataforma.db")
    try: return {k: (con.execute(v).fetchone()[0] or 0) for k, v in CONSULTAS.items()}
    finally: con.close()


def validar(antes):
    cli, masc, ped = leer("Clientes-Ficha clientes.csv"), leer("Mascotas-Mascotas.csv"), leer("Pedidos-Todos los pedidos.csv")
    despues = contar()
    # Regla de Cristina: un saldo negativo era delivery pagado aparte → se suma al total y al delivery de esa orden.
    extra = round(sum(-m(r["Saldo pendiente"]) for r in ped if m(r["Saldo pendiente"]) < 0), 2)
    esperado = {"clientes": len(cli), "mascotas": len(masc), "órdenes": len(ped),
                "total vendido $": sum(m(r["Total"]) for r in ped) + extra,
                "delivery $": sum(m(r["Delivery"]) for r in ped) + extra,
                "pagado $": sum(m(r["Monto pagado"]) for r in ped)}
    print(f"\n{'':20}{'Airtable':>12}{'agregado':>12}")
    mal = 0
    for k in CONSULTAS:
        a, e = round(esperado[k], 2), round(despues[k] - antes[k], 2)
        ok = abs(a - e) < 0.015; mal += not ok
        print(f"{k:20}{a:>12,.2f}{e:>12,.2f}  {'ok' if ok else '<-- NO CUADRA'}")
    print(f"\n(el total y el delivery incluyen ${extra:,.2f} de saldos negativos, por la regla de delivery pagado aparte)")
    con = sqlite3.connect(SIM / "plataforma.db")
    sin_lineas = con.execute("SELECT COUNT(*) FROM ordenes o WHERE NOT EXISTS (SELECT 1 FROM orden_lineas l WHERE l.orden_id=o.id)").fetchone()[0]
    cajas = {r[0] for r in con.execute("SELECT nombre FROM cuentas")}
    sin_caja = sorted({r[0] for r in con.execute("SELECT DISTINCT cuenta FROM pagos WHERE cuenta IS NOT NULL")} - cajas)
    con.close()
    print(f"órdenes sin ningún producto: {sin_lineas}" + ("   <-- revisar" if sin_lineas else ""))
    print(f"pagos en cajas que no existen: {sin_caja or 'ninguno'}" + ("   <-- revisar" if sin_caja else ""))
    return mal + (1 if sin_lineas else 0) + (1 if sin_caja else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ultimos", type=int, default=200, metavar="N", help="cuántos pedidos recientes bajar (por defecto 200)")
    ap.add_argument("--sin-descargar", action="store_true", help="usar los CSV que ya están bajados")
    ap.add_argument("--sobre", metavar="ARCHIVO", help="partir de esta base en vez de la semilla (no se modifica: se usa una copia)")
    a = ap.parse_args()
    if a.sobre and not Path(a.sobre).is_file(): sys.exit(f"No encuentro {a.sobre}")
    if not a.sin_descargar:
        # el descargador guarda en <DECOPET_DATOS>/airtable: se le apunta a CSV.parent, porque SIM se borra entero más abajo
        print(correr(["scripts/airtable_descargar.py", "--ultimos", str(a.ultimos)], f"Bajando de Airtable los {a.ultimos} pedidos más recientes",
                     env={**ENV, "DECOPET_DATOS": str(CSV.parent)}))
    for f in ("Clientes-Ficha clientes.csv", "Mascotas-Mascotas.csv", "Pedidos-Todos los pedidos.csv"):
        if not (CSV / f).exists(): sys.exit(f"Falta {f}. Corre sin --sin-descargar.")
    shutil.rmtree(SIM, ignore_errors=True)
    if a.sobre:
        print(f"\n▸ Partiendo de {a.sobre} (sobre una copia; el original no se toca)")
        SIM.mkdir(parents=True)
        origen = sqlite3.connect(f"file:{Path(a.sobre).resolve().as_posix()}?mode=ro", uri=True)
        destino = sqlite3.connect(SIM / "plataforma.db")
        origen.backup(destino); origen.close(); destino.close()
    else:
        correr(["plataforma/semilla.py", "--vacia"], "Base nueva con el catálogo, sin clientes ni órdenes")
    # La estructura al día con este código (las columnas que falten), igual que al arrancar el ERP
    correr(["-c", "import plataforma.app as A; A.preparar_base(A.DB).close()"], "Poniendo la estructura de la base al día")
    antes = contar()
    print(correr(["-m", "plataforma.importar_clientes", str(CSV / "Clientes-Ficha clientes.csv"), "--cargar"], "Cargando clientes").splitlines()[-1])
    print(correr(["-m", "plataforma.importar_mascotas", str(CSV / "Mascotas-Mascotas.csv"), "--cargar"], "Cargando mascotas").splitlines()[0])
    sal = correr(["-m", "plataforma.importar_pedidos", str(CSV / "Pedidos-Todos los pedidos.csv"), "--cargar"], "Cargando pedidos").splitlines()
    for linea in sal:
        if linea.startswith("- producto") or linea.startswith("Cargados"): print(linea)
    mal = validar(antes)
    print("\n" + ("Todo cuadra. Base lista en plataforma/data/simulacion/" if not mal else f"{mal} cosa(s) no cuadran: revisar antes de usarla."))
    sys.exit(1 if mal else 0)


if __name__ == "__main__":
    main()
