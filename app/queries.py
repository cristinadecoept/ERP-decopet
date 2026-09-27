"""Consultas de negocio: métricas del tablero, alertas de recompra, saldos de caja."""
import datetime, json
from app.db import conectar

MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]


def hoy():
    return datetime.date.today()


def rango_mes(fecha: datetime.date):
    ini = fecha.replace(day=1)
    fin = (ini + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
    return ini.isoformat(), fin.isoformat()


def kpis_periodo(con, ini, fin):
    r = con.execute("""SELECT COUNT(*) n, COALESCE(SUM(total),0) fact, COALESCE(AVG(total),0) ticket,
                              COUNT(DISTINCT cliente_id) clientes
                       FROM pedidos WHERE fecha BETWEEN ? AND ? AND estado != 'cancelado'""", (ini, fin)).fetchone()
    g = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM gastos WHERE fecha BETWEEN ? AND ?", (ini, fin)).fetchone()[0]
    nuevos = con.execute("""SELECT COUNT(*) FROM clientes c WHERE (SELECT MIN(fecha) FROM pedidos p WHERE p.cliente_id=c.id) BETWEEN ? AND ?""", (ini, fin)).fetchone()[0]
    return {"pedidos": r["n"], "facturacion": r["fact"], "ticket": r["ticket"], "clientes": r["clientes"], "gastos": g,
            "ganancia": r["fact"] - g, "clientes_nuevos": nuevos}


def tablero(con):
    h = hoy()
    ini, fin = rango_mes(h)
    mes_ant = (h.replace(day=1) - datetime.timedelta(days=1))
    ini_a, fin_a = rango_mes(mes_ant)
    d = {"mes": kpis_periodo(con, ini, fin), "mes_anterior": kpis_periodo(con, ini_a, fin_a),
         "hoy": kpis_periodo(con, h.isoformat(), h.isoformat()),
         "nombre_mes": f"{MESES[h.month-1]} {h.year}"}
    d["top_productos"] = con.execute("""SELECT p.nombre, p.categoria, SUM(i.cantidad) unidades, SUM(i.total) monto
        FROM pedido_items i JOIN productos p ON p.id=i.producto_id JOIN pedidos pe ON pe.id=i.pedido_id
        WHERE pe.fecha BETWEEN ? AND ? AND pe.estado!='cancelado' GROUP BY p.id ORDER BY monto DESC LIMIT 8""", (ini, fin)).fetchall()
    d["por_canal"] = con.execute("""SELECT canal, COUNT(*) n, SUM(total) monto FROM pedidos
        WHERE fecha BETWEEN ? AND ? AND estado!='cancelado' GROUP BY canal ORDER BY monto DESC""", (ini, fin)).fetchall()
    d["por_metodo"] = con.execute("""SELECT m.nombre, m.moneda_recibida, SUM(pa.monto_usd) monto FROM pagos pa
        JOIN metodos_pago m ON m.id=pa.metodo_id WHERE pa.fecha BETWEEN ? AND ? GROUP BY m.id ORDER BY monto DESC""", (ini, fin)).fetchall()
    d["ultimos_12"] = con.execute("""SELECT substr(fecha,1,7) mes, COUNT(*) n, SUM(total) monto FROM pedidos
        WHERE estado!='cancelado' AND fecha >= date('now','-12 months','start of month') GROUP BY 1 ORDER BY 1""").fetchall()
    d["pendientes"] = con.execute("""SELECT estado, COUNT(*) n FROM pedidos WHERE estado NOT IN ('entregado','cancelado') GROUP BY estado""").fetchall()
    d["top_clientes"] = con.execute("""SELECT c.id, c.nombre, COUNT(p.id) n, SUM(p.total) monto, MAX(p.fecha) ultima
        FROM clientes c JOIN pedidos p ON p.cliente_id=c.id WHERE p.estado!='cancelado'
        GROUP BY c.id ORDER BY monto DESC LIMIT 8""").fetchall()
    d["gastos_categoria"] = con.execute("""SELECT categoria, SUM(monto_usd) monto FROM gastos WHERE fecha BETWEEN ? AND ?
        GROUP BY categoria ORDER BY monto DESC""", (ini, fin)).fetchall()
    d["riesgo"] = clientes_recompra(con, limite=8)
    return d


def clientes_recompra(con, limite=50, dias_min=None):
    """Clientes con Porche PRO cuyo último pedido (de lo que sea) fue hace más del ciclo esperado de repuesto."""
    ciclo = int(con.execute("SELECT valor FROM configuracion WHERE clave='dias_ciclo_repuesto'").fetchone()[0])
    dias_min = dias_min or ciclo
    return con.execute(f"""
        SELECT c.id, c.nombre, c.telefono,
               MIN(CASE WHEN pr.es_porche_pro=1 THEN pe.fecha END) primer_porche,
               MAX(pe.fecha) ultimo_pedido,
               CAST(julianday('now') - julianday(MAX(pe.fecha)) AS INTEGER) dias_sin_pedir,
               SUM(CASE WHEN pr.es_repuesto=1 THEN i.cantidad ELSE 0 END) repuestos_comprados,
               COUNT(DISTINCT pe.id) pedidos
        FROM clientes c
        JOIN pedidos pe ON pe.cliente_id=c.id AND pe.estado!='cancelado'
        JOIN pedido_items i ON i.pedido_id=pe.id
        JOIN productos pr ON pr.id=i.producto_id
        GROUP BY c.id
        HAVING primer_porche IS NOT NULL AND dias_sin_pedir BETWEEN ? AND 120
        ORDER BY dias_sin_pedir ASC LIMIT ?""", (dias_min, limite)).fetchall()


def saldos_caja(con):
    corte = (con.execute("SELECT valor FROM configuracion WHERE clave='fecha_corte_caja'").fetchone() or [None])[0] or "2100-01-01"
    return con.execute("""
        SELECT c.*, 
          (SELECT COALESCE(SUM(monto_usd),0) FROM pagos pa WHERE pa.caja_id=c.id AND pa.confirmado=1 AND pa.fecha>=?) ingresos,
          (SELECT COALESCE(SUM(monto_usd),0) FROM gastos g WHERE g.caja_id=c.id AND g.fecha>=?) egresos,
          (SELECT COALESCE(SUM(CASE WHEN tipo IN ('ingreso','transferencia_in','ajuste') THEN monto_usd ELSE -monto_usd END),0)
             FROM movimientos_caja m WHERE m.caja_id=c.id AND m.fecha>=?) movimientos
        FROM cajas c WHERE c.activa=1 ORDER BY c.orden""", (corte, corte, corte)).fetchall(), corte


def categorias_gasto(con):
    return json.loads(con.execute("SELECT valor FROM configuracion WHERE clave='categorias_gasto'").fetchone()[0])
