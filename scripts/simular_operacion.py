"""Arma una base del ERP con datos REALES de Airtable para simular la operación, y comprueba que cuadre.

    python scripts/simular_operacion.py --ultimos 200      baja de Airtable (solo lectura), arma la base y valida
    python scripts/simular_operacion.py --sin-descargar    arma la base con los CSV que ya se bajaron

Pasos: (1) bajar de Airtable → (2) base nueva con el catálogo, sin clientes ni órdenes → (3) cargar clientes, mascotas y
pedidos con las reglas de los importadores → (4) comparar contra Airtable: conteos y dinero.
La base queda en plataforma/data/simulacion/plataforma.db (fuera de git: tiene datos de clientes reales).
Para probarla en local:  DECOPET_DATOS=plataforma/data/simulacion  uvicorn plataforma.app:app
"""
import os, sys, csv, shutil, sqlite3, argparse, subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CSV = RAIZ / "plataforma" / "data" / "airtable"
SIM = RAIZ / "plataforma" / "data" / "simulacion"
PY = sys.executable
ENV = {**os.environ, "DECOPET_DATOS": str(SIM), "DECOPET_STAGING": "1", "PYTHONUTF8": "1"}   # STAGING: la semilla se niega a borrar si no


def correr(args, titulo):
    print(f"\n▸ {titulo}")
    r = subprocess.run([PY, *args], cwd=RAIZ, env=ENV, capture_output=True, text=True, encoding="utf-8")
    sal = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        print(sal[-1500:]); sys.exit(f"Falló: {titulo}")
    return sal


def m(s): return float((s or "0").replace("$", "").replace(",", "") or 0)
def leer(n): return list(csv.DictReader(open(CSV / n, encoding="utf-8-sig")))


def validar():
    cli, masc, ped = leer("Clientes-Ficha clientes.csv"), leer("Mascotas-Mascotas.csv"), leer("Pedidos-Todos los pedidos.csv")
    con = sqlite3.connect(SIM / "plataforma.db")
    q = lambda s: con.execute(s).fetchone()[0] or 0
    # Regla de Cristina: un saldo negativo era delivery pagado aparte → se suma al total y al delivery de esa orden.
    extra = round(sum(-m(r["Saldo pendiente"]) for r in ped if m(r["Saldo pendiente"]) < 0), 2)
    filas = [("clientes", len(cli), q("SELECT COUNT(*) FROM clientes WHERE nombre!='Sin nombre'")),
             ("mascotas", len(masc), q("SELECT COUNT(*) FROM mascotas")),
             ("órdenes", len(ped), q("SELECT COUNT(*) FROM ordenes")),
             ("total vendido $", round(sum(m(r["Total"]) for r in ped) + extra, 2), round(q("SELECT SUM(total) FROM ordenes"), 2)),
             ("delivery $", round(sum(m(r["Delivery"]) for r in ped) + extra, 2), round(q("SELECT SUM(delivery) FROM ordenes"), 2)),
             ("pagado $", round(sum(m(r["Monto pagado"]) for r in ped), 2), round(q("SELECT SUM(monto_usd) FROM pagos"), 2))]
    print(f"\n{'':20}{'Airtable':>12}{'ERP':>12}")
    mal = 0
    for t, a, e in filas:
        ok = abs(a - e) < 0.015; mal += not ok
        print(f"{t:20}{a:>12,.2f}{e:>12,.2f}  {'ok' if ok else '<-- NO CUADRA'}")
    print(f"\n(el total y el delivery incluyen ${extra:,.2f} de saldos negativos, por la regla de delivery pagado aparte)")
    sin_lineas = q("SELECT COUNT(*) FROM ordenes o WHERE NOT EXISTS (SELECT 1 FROM orden_lineas l WHERE l.orden_id=o.id)")
    print(f"órdenes sin ningún producto: {sin_lineas}" + ("   <-- revisar" if sin_lineas else ""))
    return mal + (1 if sin_lineas else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ultimos", type=int, default=200, metavar="N", help="cuántos pedidos recientes bajar (por defecto 200)")
    ap.add_argument("--sin-descargar", action="store_true", help="usar los CSV que ya están bajados")
    a = ap.parse_args()
    if not a.sin_descargar:
        print(correr(["scripts/airtable_descargar.py", "--ultimos", str(a.ultimos)], f"Bajando de Airtable los {a.ultimos} pedidos más recientes"))
    for f in ("Clientes-Ficha clientes.csv", "Mascotas-Mascotas.csv", "Pedidos-Todos los pedidos.csv"):
        if not (CSV / f).exists(): sys.exit(f"Falta {f}. Corre sin --sin-descargar.")
    shutil.rmtree(SIM, ignore_errors=True)
    correr(["plataforma/semilla.py", "--vacia"], "Base nueva con el catálogo, sin clientes ni órdenes")
    print(correr(["-m", "plataforma.importar_clientes", str(CSV / "Clientes-Ficha clientes.csv"), "--cargar"], "Cargando clientes").splitlines()[-1])
    print(correr(["-m", "plataforma.importar_mascotas", str(CSV / "Mascotas-Mascotas.csv"), "--cargar"], "Cargando mascotas").splitlines()[0])
    print(correr(["-m", "plataforma.importar_pedidos", str(CSV / "Pedidos-Todos los pedidos.csv"), "--cargar"], "Cargando pedidos").splitlines()[-1])
    mal = validar()
    print("\n" + ("Todo cuadra. Base lista en plataforma/data/simulacion/" if not mal else f"{mal} cosa(s) no cuadran: revisar antes de subir."))
    sys.exit(1 if mal else 0)


if __name__ == "__main__":
    main()
