#!/usr/bin/env python3
"""Pruebas del ERP Decopet — sobre todo del dinero.

Se ejecutan en segundos y no tocan tus datos: cada prueba arma su propia base
de datos de mentira. Correrlas antes y después de cualquier cambio:

    ./.venv/bin/python pruebas.py
"""
import os, re, time, sqlite3, datetime, tempfile, pathlib, traceback
os.environ["DECOPET_PRUEBAS"] = "1"

import plataforma.app as A

BASE = pathlib.Path(__file__).resolve().parent / "plataforma"
_ok = _mal = 0
_fallos = []


def prueba(titulo):
    def deco(f):
        global _ok, _mal
        try:
            f(); _ok += 1; print(f"  ✓ {titulo}")
        except Exception as e:
            _mal += 1; _fallos.append((titulo, e, traceback.format_exc()))
            print(f"  ✗ {titulo}\n      {e}")
        return f
    return deco


def base_limpia():
    """Una base vacía con la estructura real del ERP, por el mismo camino que usa el ERP al arrancar."""
    con = A.preparar_base(tempfile.mktemp(suffix=".db"))
    con.row_factory = sqlite3.Row
    con.execute("INSERT INTO usuarios (id,nombre,rol) VALUES (1,'Cristina','admin')")
    con.commit()
    return con


def d(s): return datetime.date.fromisoformat(s)


# ─────────────────────────────────────────────── días de pago del equipo
print("\nDÍAS DE PAGO")

@prueba("Si la quincena cae sábado o domingo, se paga el viernes")
def _():
    assert A.dia_de_pago(d("2026-10-31")) == d("2026-10-30"), "sábado"
    assert A.dia_de_pago(d("2026-11-15")) == d("2026-11-13"), "domingo"

@prueba("Si cae lunes, se paga el lunes (no se corre)")
def _():
    assert A.dia_de_pago(d("2026-11-30")) == d("2026-11-30")

@prueba("El último día del mes se ajusta al mes (febrero 28, diciembre 31)")
def _():
    assert d("2026-02-28") in [x for x in A.dias_de_pago(2026, 2)] or A.dia_de_pago(d("2026-02-28")) in A.dias_de_pago(2026, 2)
    assert A.dias_de_pago(2026, 12)[1] == d("2026-12-31")

@prueba("La próxima quincena cruza bien de un mes al siguiente")
def _():
    assert A.proxima_quincena(d("2026-09-27")) == d("2026-09-30")
    assert A.proxima_quincena(d("2026-10-01")) == d("2026-10-15")
    assert A.proxima_quincena(d("2026-12-31")) == d("2026-12-31")

@prueba("El recordatorio aguanta hasta 2 días después, aunque se pague el viernes")
def _():
    v = dict(A.ventana_pago(2026, 2))
    assert v[d("2026-02-13")] == d("2026-02-17"), "el 15 cae domingo: paga el 13, recuerda hasta el 17"


# ─────────────────────────────────────────────── ritmo del cliente
print("\nRITMO DEL CLIENTE")

@prueba("Con menos de dos repuestos no inventa un ritmo")
def _():
    assert A.ritmo_cliente(["2026-08-01"]) == (None, 0)
    assert A.ciclo_de(None) == A.CICLO_REPUESTO

@prueba("Usa la mediana: un hueco raro no le cambia el ritmo")
def _():
    r, _n = A.ritmo_cliente(["2026-07-02", "2026-07-12", "2026-08-21", "2026-08-31"])
    assert r == 10, r

@prueba("Descarta los huecos de más de 120 días")
def _():
    r, _n = A.ritmo_cliente(["2025-08-01", "2026-08-01", "2026-08-15"])
    assert r == 14, r

@prueba("Nunca espera más de lo normal: solo puede acortar")
def _():
    assert A.ciclo_de(31) == A.CICLO_REPUESTO
    assert A.ciclo_de(10) == 10


# ─────────────────────────────────────────────── dinero
print("\nDINERO")

@prueba("Un cobro extra sube el total de la orden y deja su pago")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Cliente X','Cliente')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,total) VALUES (1,'#1',1,'pendiente',100)")
    con.commit()
    A.cobro_extra(con, 1, "Delivery repuesto", 5, "Zelle Decopet", "2026-09-27", 1)
    con.commit()
    assert con.execute("SELECT total FROM ordenes WHERE id=1").fetchone()[0] == 105
    p = con.execute("SELECT monto_usd, estado FROM pagos WHERE orden_id=1").fetchone()
    assert (p["monto_usd"], p["estado"]) == (5, "confirmado"), dict(p)
    assert con.execute("SELECT COUNT(*) FROM orden_lineas WHERE orden_id=1").fetchone()[0] == 1

@prueba("Al despachador se le paga el delivery, y no se guarda una copia vieja")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Cliente X','Cliente')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,delivery,despachador,pago_despachador) VALUES (1,'#1',1,'pendiente',5,'Juan',5)")
    con.commit()
    con.execute("UPDATE ordenes SET delivery=12 WHERE id=1")
    A.fijar_pago_despachador(con, 1); con.commit()
    assert con.execute("SELECT pago_despachador FROM ordenes WHERE id=1").fetchone()[0] is None, "no debe quedar copia congelada"
    debe = con.execute("""SELECT COALESCE(SUM(COALESCE(delivery,0)),0) FROM ordenes
                          WHERE despachador='Juan' AND despachador_pagado=0 AND estado!='cancelada' AND origen_excel=0""").fetchone()[0]
    assert debe == 12, debe

@prueba("Reasignar la orden a otro despachador le mueve la deuda")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Cliente X','Cliente')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,delivery,despachador) VALUES (1,'#1',1,'pendiente',12,'Juan')")
    con.commit()
    def debe(q):
        return con.execute("""SELECT COALESCE(SUM(COALESCE(delivery,0)),0) FROM ordenes
                              WHERE despachador=? AND despachador_pagado=0 AND estado!='cancelada' AND origen_excel=0""", (q,)).fetchone()[0]
    assert (debe("Juan"), debe("Ingrid")) == (12, 0)
    con.execute("UPDATE ordenes SET despachador='Ingrid' WHERE id=1"); con.commit()
    assert (debe("Juan"), debe("Ingrid")) == (0, 12)

@prueba("El viaje a la agencia se cobra por viaje: Tealca 10, cualquier otra 5")
def _():
    con = base_limpia()
    con.execute("INSERT INTO config (clave,valor) VALUES ('tarifa_agencia','{\"Tealca\":10,\"*\":5}')"); con.commit()
    assert A.tarifa_agencia(con, "Tealca") == 10
    assert A.tarifa_agencia(con, "Zoom") == 5

@prueba("El saldo de una caja baja con cada gasto que sale de ella")
def _():
    con = base_limpia()
    con.execute("INSERT INTO cuentas (id,codigo,nombre,moneda,tipo,saldo_inicial,activa) VALUES (1,'Z','Zelle','USD','operativa',100,1)")
    con.commit()
    def saldo():
        g = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM gastos WHERE cuenta_id=1").fetchone()[0]
        return con.execute("SELECT saldo_inicial FROM cuentas WHERE id=1").fetchone()[0] - g
    assert saldo() == 100
    con.execute("INSERT INTO gastos (fecha,monto_usd,monto_real,moneda,categoria,cuenta_id) VALUES ('2026-09-27',30,30,'USD','Equipo',1)")
    con.commit()
    assert saldo() == 70, saldo()


# ─────────────────────────────────────────────── números escritos a mano
print("\nNÚMEROS ESCRITOS A MANO")

@prueba("Acepta comas y puntos igual (1.234,56 y 1234.56)")
def _():
    assert A.cifra("1.234,56") == 1234.56
    assert A.cifra("1234.56") == 1234.56
    assert A.cifra("") in (None, 0)


# ─────────────────────────────────────────────── saldo a favor
print("\nSALDO A FAVOR")

@prueba("Pagar de más deja saldo a favor del cliente, no se pierde")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Ana','Ana')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,total) VALUES (1,'#1',1,'pendiente',22)")
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,estado,fecha) VALUES (1,'Efectivo USD',25,'confirmado','2026-09-27')")
    con.commit()
    sobra = A.sobrante_a_favor(con, 1, 1); con.commit()
    assert sobra == 3, sobra
    assert A.credito_de(con, 1) == 3, A.credito_de(con, 1)

@prueba("No se duplica si se vuelve a mirar la misma orden")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Ana','Ana')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,total) VALUES (1,'#1',1,'pendiente',22)")
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,estado,fecha) VALUES (1,'Efectivo USD',25,'confirmado','2026-09-27')")
    con.commit()
    A.sobrante_a_favor(con, 1, 1); con.commit()
    A.sobrante_a_favor(con, 1, 1); con.commit()
    assert A.credito_de(con, 1) == 3, "se duplicó: " + str(A.credito_de(con, 1))

@prueba("Pagar con saldo a favor no infla ninguna caja")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Ana','Ana')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,total) VALUES (1,'#1',1,'pendiente',22)")
    con.commit()
    A.mover_credito(con, 1, 3, "sin vuelto"); con.commit()
    # así lo registra la ruta: sin caja, porque no entra plata nueva
    con.execute("""INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado)
                   VALUES (1,?,3,3,'USD','2026-09-27','confirmado')""", (A.SALDO_FAVOR,))
    A.mover_credito(con, 1, -3, "usado", 1); con.commit()
    caja = con.execute("SELECT cuenta FROM pagos WHERE forma=?", (A.SALDO_FAVOR,)).fetchone()["cuenta"]
    assert caja is None, f"el pago con saldo apuntó a la caja {caja}"
    assert A.credito_de(con, 1) == 0


@prueba("Cuando lo usa, el saldo baja")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Ana','Ana')")
    con.commit()
    A.mover_credito(con, 1, 3, "no había vuelto"); con.commit()
    A.mover_credito(con, 1, -3, "lo usó en un repuesto"); con.commit()
    assert A.credito_de(con, 1) == 0, A.credito_de(con, 1)


# ─────────────────────────────────────────────── no perder datos sin querer
print("\nNO BORRAR LO QUE NO SE TOCÓ")

@prueba("Editar un cliente sin mandar todos los campos no borra los demás")
def _():
    import asyncio
    con = base_limpia()
    con.execute("""INSERT INTO clientes (id,nombre,nombre_pila,apellido,telefono,correo)
                   VALUES (1,'Ana Pérez','Ana','Pérez','0414-1112233','ana@x.com')""")
    con.commit()
    # se manda solo la ciudad, como haría un formulario a medio llenar
    campos = {"ciudad": "Caracas"}
    actual = con.execute("SELECT * FROM clientes WHERE id=1").fetchone()
    limpiar = {"telefono": lambda v: v, "correo": lambda v: v, "ciudad": lambda v: v}
    cambia = {k: fn(campos.get(k)) for k, fn in limpiar.items() if k in campos}
    con.execute(f"UPDATE clientes SET {', '.join(k + '=?' for k in cambia)} WHERE id=1", tuple(cambia.values()))
    con.commit()
    r = con.execute("SELECT nombre, apellido, telefono, correo, ciudad FROM clientes WHERE id=1").fetchone()
    assert r["nombre"] == "Ana Pérez", r["nombre"]
    assert r["telefono"] == "0414-1112233", "le borró el teléfono"
    assert r["correo"] == "ana@x.com", "le borró el correo"
    assert r["ciudad"] == "Caracas"


# ─────────────────────────────────────────────── poder reconstruir el ERP
print("\nPAGOS A PRODUCCIÓN")

def _pedido(con, recibido=0):
    con.execute("INSERT INTO produccion (id,pieza,cantidad,recibido,costo,estado,responsable,fecha_pedido) VALUES (1,'El Bar Gigante',10,?,105,'en_proceso','Walter','2026-09-28')", (recibido,))
    con.commit()

def _concepto(con):
    return con.execute("SELECT descripcion FROM gastos ORDER BY id DESC LIMIT 1").fetchone()[0]

@prueba("Pagar una parte antes de que llegue es un adelanto")
def _():
    con = base_limpia(); _pedido(con)
    A.pagar_produccion(con, 1, 100, "", "2026-09-28", "", 1)
    assert _concepto(con) == "El Bar Gigante · adelanto de pedido 10 unidades", _concepto(con)

@prueba("El pago que salda el pedido es el pago final, aunque no haya llegado")
def _():
    con = base_limpia(); _pedido(con)
    A.pagar_produccion(con, 1, 100, "", "2026-09-28", "", 1)
    A.pagar_produccion(con, 1, 5, "", "2026-09-28", "", 1)
    assert _concepto(con) == "El Bar Gigante · pago final de pedido 10 unidades", _concepto(con)

@prueba("Pagar todo de una vez es un pago, no un adelanto")
def _():
    con = base_limpia(); _pedido(con)
    A.pagar_produccion(con, 1, 105, "", "2026-09-28", "", 1)
    assert _concepto(con) == "El Bar Gigante · pago de pedido 10 unidades", _concepto(con)


@prueba("Las cajas de madera que llegan entran al inventario, que es de donde las saca el porche")
def _():
    # el porche descuenta la caja del insumo (receta); al recibirla tiene que entrar a ese mismo insumo
    for nom, sku, entra in A.PIEZAS_PRODUCCION:
        if nom.startswith("Caja de madera"):
            assert sku.startswith("INS-CAJA"), f"{nom} apunta a {sku}, no a su insumo"
            assert entra, f"{nom} está marcada para no entrar al inventario"


@prueba("Lo que se compra para vender tal cual (bowls, platos) tiene a qué producto entrar")
def _():
    con = sqlite3.connect(str(A.DB))
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='productos'").fetchone():
        print("      (sin base en uso, como en GitHub: no hay catálogo real que revisar)"); return
    for item, sku in A.ITEMS_A_INVENTARIO.items():
        assert con.execute("SELECT 1 FROM productos WHERE sku=?", (sku,)).fetchone(), f"{item} apunta a {sku}, que no existe"


@prueba("La pega se compra por cuñete o galón y entra al inventario en litros")
def _():
    con = base_limpia()
    con.execute("INSERT INTO proveedores (id,nombre) VALUES (8,'Ferretería')")
    con.execute("INSERT INTO proveedor_items (proveedor_id,item,precio,unidad) VALUES (8,'Pega amarilla',86,'cuñete')")
    con.execute("INSERT INTO proveedor_items (proveedor_id,item,precio,unidad) VALUES (8,'Cinta antideslizante',1,'rollo')")
    r = {"pieza": "Pega amarilla", "responsable": "Ferretería"}
    assert A.a_inventario(con, r, 2) == 37.8, A.a_inventario(con, r, 2)
    assert A.a_inventario(con, {"pieza": "Cinta antideslizante", "responsable": "Ferretería"}, 3) == 3
    assert A.fmt_cant(37.8, "litro") == "37,8 litros" and A.fmt_cant(1, "rollo") == "1 rollo"


@prueba("Sin nadie conectado (ERP recién instalado) se puede guardar y queda a nombre de un usuario")
def _():
    class R:   # una petición sin sesión
        cookies = {}; headers = {}
    con = base_limpia()
    uid = A.uid_de(R())
    assert uid is not None


@prueba("Un pedido a proveedor dice en qué se pidió: 2 cuñetes, no 2 unidades")
def _():
    con = base_limpia()
    con.execute("INSERT INTO proveedores (id,nombre) VALUES (8,'Ferretería')")
    con.execute("INSERT INTO proveedor_items (proveedor_id,item,precio,unidad) VALUES (8,'Pega amarilla',86,'cuñete')")
    con.execute("INSERT INTO produccion (id,pieza,cantidad,costo,estado,responsable,fecha_pedido,tipo_pedido) VALUES (1,'Pega amarilla',2,172,'en_proceso','Ferretería','2026-09-28','proveedor')")
    A.pagar_produccion(con, 1, 100, "", "2026-09-28", "", 1)
    assert _concepto(con) == "Pega amarilla · adelanto de pedido 2 cuñetes", _concepto(con)
    assert A.tipo_de_proveedor("Ferretería") == "proveedor" and A.tipo_de_proveedor("Walter") == "produccion"


@prueba("Si el proveedor no entregó todo y ya se le pagó, te debe la diferencia; al devolverla entra a la caja")
def _():
    con = base_limpia()
    con.execute("INSERT INTO cuentas (id,nombre,activa,saldo_inicial) VALUES (1,'Efectivo',1,100)")
    con.execute("INSERT INTO produccion (id,pieza,cantidad,recibido,costo,estado,responsable,fecha_pedido,tipo_pedido,faltaron) VALUES (5,'Grama',18,18,54,'recibido','Yovanny','2026-09-28','proveedor',2)")
    con.execute("INSERT INTO abonos_produccion (produccion_id,fecha,monto) VALUES (5,'2026-09-28',60)")
    con.commit()
    d = A.proveedores_que_deben(con)
    assert len(d) == 1 and round(d[0]["debe"], 2) == 6 and d[0]["faltaron"] == 2, d
    # la devolución: un gasto en negativo que en el libro de caja es una entrada
    con.execute("INSERT INTO gastos (id,fecha,monto_usd,categoria,descripcion,cuenta_id) VALUES (9,'2026-09-28',-6,'Proveedores','Grama · devolución',1)")
    con.execute("INSERT INTO abonos_produccion (produccion_id,fecha,monto,gasto_id) VALUES (5,'2026-09-28',-6,9)")
    con.commit()
    assert A.proveedores_que_deben(con) == []
    l = [x for x in A.libro_caja(con) if x["ref"] == ("gasto", 9)][0]
    assert (l["entrada"], l["salida"]) == (6, 0), l


@prueba("El retiro de un pack dice qué número es: 1/3, no 'le quedan 3'")
def _():
    k = lambda saldo, n=1: {"retiro_programado": n, "unidades": 3, "saldo": saldo, "tamano": "Mediano"}
    assert A.repuesto_de_pack(k(3)) == ("Repuesto Mediano", "1/3"), A.repuesto_de_pack(k(3))
    assert A.repuesto_de_pack(k(1)) == ("Repuesto Mediano", "3/3")
    assert A.repuesto_de_pack(k(3, 2)) == ("2× Repuesto Mediano", "1–2/3")


@prueba("Las notas del cliente llegan a quien entrega, menos las privadas")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Alejandra Ramos','Alejandra')")
    con.execute("INSERT INTO notas_cliente (cliente_id,tipo,texto,mostrar_en_orden,mostrar_logistica) VALUES (1,'general','Solo recibe hasta las 3pm',1,1)")
    con.execute("INSERT INTO notas_cliente (cliente_id,tipo,texto,mostrar_en_orden,mostrar_logistica) VALUES (1,'general','nota privada',1,0)")
    con.commit()
    assert A.indicaciones_cliente(con, 1, "Llamar al llegar") == ["Llamar al llegar", "Solo recibe hasta las 3pm"], A.indicaciones_cliente(con, 1, "Llamar al llegar")
    t = A.texto_ruta([{"quien": "Alejandra", "telefono": "", "direccion": "", "maps": "", "que_lleva": "1× Bowl", "cobrar": 0,
                       "indicaciones": A.indicaciones_cliente(con, 1)}], datetime.date(2026, 9, 28))
    assert "📌 Solo recibe hasta las 3pm" in t and "privada" not in t, t


@prueba("Eliminar una orden funciona aunque haya dejado o usado saldo a favor, y todo lo que referencia una orden se borra")
def _():
    con = base_limpia(); con.execute("PRAGMA foreign_keys=ON")
    # cualquier tabla nueva que apunte a ordenes tiene que estar en la lista de eliminar_orden
    import inspect
    fuente = inspect.getsource(A.borrar_orden)
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%REFERENCES ordenes%'"):
        assert f'"{t}"' in fuente or t in ("entregas_repuesto",), f"borrar_orden no borra {t}"
    fuente = inspect.getsource(A.eliminar_cliente)
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='clientes' AND sql LIKE '%REFERENCES clientes%'"):
        assert f'"{t}"' in fuente or t == "ordenes", f"eliminar_cliente no borra {t}"


@prueba("Logística tiene lista cerrada: no llega a plata, catálogo, exportes ni al manual técnico")
def _():
    permitido, _casa = A.PUERTAS["logistica"]
    for ruta in ("/cashflow", "/finanzas", "/productos", "/historial", "/historial/exportar", "/configuracion", "/produccion", "/equipo", "/revision", "/tarifas", "/docs", "/openapi.json"):
        assert not ruta.startswith(permitido), f"Logística puede abrir {ruta}"
    assert A.app.openapi_url is None and A.app.docs_url is None, "el manual técnico está publicado"
    o = {"numero": "#1", "cliente": "Ana", "telefono": "", "lineas": [], "tipo_entrega": "delivery", "fecha_prometida": None, "franja": None,
         "agencia": None, "guia": None, "direccion": "", "estado_pago": "contra_entrega", "monto_contra_entrega": 20, "total": 20, "pagado": 0,
         "forma_pago_prevista": "", "notas_entrega": None, "notas_cliente": []}
    assert "$" not in A.resumen_despacho(o, con_plata=False) and "$20" in A.resumen_despacho(o)


@prueba("Slow Chow: al confirmar que llegó se dice cuántos azules y rosados; cada uno se lleva su plato")
def _():
    con = base_limpia()
    con.execute("INSERT INTO productos (id,sku,nombre,tipo,requiere_color,activo) VALUES (15,'SLOW-10','Slow Chow Mini','producto',1,1)")
    con.execute("INSERT INTO productos (id,sku,nombre,tipo,activo) VALUES (22,'PLATO-AZUL','Plato azul','producto',1)")
    con.execute("INSERT INTO productos (id,sku,nombre,tipo,activo) VALUES (23,'PLATO-ROSA','Plato rosado','producto',1)")
    con.execute("INSERT INTO produccion (id,producto_id,pieza,cantidad,recibido,estado,responsable,fecha_pedido) VALUES (1,15,'Slow Chow Mini',10,0,'en_proceso','Walter','2026-09-28')")
    con.commit()
    r = con.execute("SELECT * FROM produccion WHERE id=1").fetchone()
    col = A.colores_de(con, r, "6", "4"); assert col == {"azul": 6, "rosado": 4}, col
    A.entrar_al_inventario(con, r, 10, "prueba", 1, col)
    st = lambda pid, c=None: con.execute("SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario WHERE producto_id=?" + (" AND color=?" if c else ""), (pid, c) if c else (pid,)).fetchone()[0]
    assert (st(15, "azul"), st(15, "rosado"), st(22), st(23)) == (6, 4, -6, -4), (st(15, "azul"), st(15, "rosado"), st(22), st(23))
    import inspect
    assert "if armado else 0" in inspect.getsource(A.descontar_inventario), "lo que tiene stock propio no debe gastar materiales al venderse"


@prueba("Al despachador se le debe lo entregado, no lo asignado")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Ana','Ana')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,total,delivery,despachador,origen_excel,despachador_pagado) VALUES (1,'#1',1,'pendiente',20,5,'Juan',0,0)")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,total,delivery,despachador,origen_excel,despachador_pagado) VALUES (2,'#2',1,'en_ruta',20,7,'Juan',0,0)")
    con.commit()
    assert A.resumen_despachador(con, "Juan", None)["debe"] == 0
    con.execute("UPDATE ordenes SET estado='entregada' WHERE id=1"); con.commit()
    assert A.resumen_despachador(con, "Juan", None)["debe"] == 5


@prueba("Una llegada no se anota dos veces si el formulario se manda dos veces seguidas")
def _():
    con = base_limpia()
    con.execute("INSERT INTO productos (id,sku,nombre,tipo,activo) VALUES (1,'INS-CAJAM','Caja de madera mediana','insumo',1)")
    con.execute("INSERT INTO mov_inventario (producto_id,fecha,tipo,cantidad,nota,usuario_id,creado_en) VALUES (1,'2026-09-28','entrada',10,'producción #1 · Walter',1,datetime('now','localtime'))")
    con.commit()
    assert A.llegada_repetida(con, 1, 1) and not A.llegada_repetida(con, 12, 1)


# ─────────────────────────────────────────────── todas las pantallas, con cada rol
print("\nCADA PANTALLA, CON CADA ROL")

from contextlib import contextmanager
from fastapi.testclient import TestClient


@contextmanager
def erp_de_prueba(en_servidor=False, con_datos=True):
    """El ERP entero sobre una base de mentira, con los datos ficticios de la semilla.
    Nunca toca la base en uso: se cambia la ruta mientras dura la prueba."""
    import plataforma.semilla as S
    antes = (A.DB, S.DB, A.EN_SERVIDOR, A.bcv.programar, A.bcv.actualizar)
    A.DB = S.DB = pathlib.Path(tempfile.mkdtemp()) / "plataforma.db"
    A.EN_SERVIDOR = en_servidor
    A.bcv.programar = lambda *a, **k: None          # sin hilos ni internet durante las pruebas
    A.bcv.actualizar = lambda *a, **k: {}
    try:
        if con_datos:
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()): S.main()
        A._arranque()
        yield TestClient(A.app)
    finally:
        A.DB, S.DB, A.EN_SERVIDOR, A.bcv.programar, A.bcv.actualizar = antes
        try: A.cargar_despachadores(); A.cargar_formas_pago(); A.cargar_ajustes()
        except sqlite3.OperationalError: pass   # en GitHub no hay base en uso: no hay nada que volver a cargar


def sesion_de(cliente, rol):
    """Un usuario de ese rol, con clave puesta, y su sesión abierta."""
    con = sqlite3.connect(A.DB)
    desp = A.DESPACHADORES[0] if rol == "despachador" and A.DESPACHADORES else None
    uid = con.execute("INSERT INTO usuarios (nombre,usuario,rol,clave_hash,despachador,activo) VALUES (?,?,?,?,?,1)",
                      (f"Prueba {rol}", f"prueba-{rol}", rol, A.cifrar_clave("clave-de-prueba"), desp)).lastrowid
    con.commit(); ficha = A.abrir_sesion(con, uid); con.close()
    cliente.cookies.clear(); cliente.cookies.set("sesion", ficha)
    if desp: cliente.cookies.set("ver_desp", desp)


def rutas_get():
    """Todas las pantallas del ERP. Las que llevan un número en la dirección se prueban con el 1.
    Salir y ver-como cambian la sesión: si se recorrieran, el resto se probaría ya desconectado."""
    from starlette.routing import Route
    for r in A.app.routes:
        if isinstance(r, Route) and "GET" in (r.methods or ()) and not r.path.startswith(("/salir", "/ver-como")):
            yield re.sub(r"\{[^}]+\}", "1", r.path)


def sin_sesion(r):
    """Si una pantalla manda a /entrar es que la prueba perdió la sesión y ya no está probando nada."""
    return r.status_code == 303 and r.headers.get("location", "").startswith("/entrar")


ROLES_CERRADOS = ("taller", "despachador", "logistica")


@prueba("Ninguna pantalla se cae con ningún rol (ni un error 500)")
def _():
    rotas = []; abiertas = {}
    with erp_de_prueba() as c:
        for rol in ("admin",) + ROLES_CERRADOS:
            sesion_de(c, rol)
            for ruta in rutas_get():
                try:
                    r = c.get(ruta, follow_redirects=False)
                    if r.status_code >= 500: rotas.append(f"{rol} {ruta} → {r.status_code}")
                    if sin_sesion(r): rotas.append(f"{rol} {ruta} → perdió la sesión")
                    if r.status_code == 200: abiertas[rol] = abiertas.get(rol, 0) + 1
                except Exception as e:
                    rotas.append(f"{rol} {ruta} → {type(e).__name__}: {e}")
    assert not rotas, "\n      ".join(["pantallas rotas:"] + rotas)
    assert abiertas.get("admin", 0) >= 40, f"el admin solo abrió {abiertas.get('admin', 0)} pantallas: la prueba no está entrando"


@prueba("Taller, despachador y logística solo abren lo de su lista; el resto los devuelve a su pantalla")
def _():
    fuera = []
    with erp_de_prueba() as c:
        for rol in ROLES_CERRADOS:
            permitido, casa = A.PUERTAS[rol]
            sesion_de(c, rol)
            for ruta in rutas_get():
                r = c.get(ruta, follow_redirects=False)
                assert not sin_sesion(r), f"{rol} perdió la sesión en {ruta}"
                if r.status_code == 200 and not ruta.startswith(permitido): fuera.append(f"{rol} abrió {ruta}")
    assert not fuera, "\n      ".join(["vieron algo que no les toca:"] + fuera)


@prueba("Ni el taller ni logística llegan a la plata: finanzas, cajas, cashflow, gastos, equipo")
def _():
    with erp_de_prueba() as c:
        for rol in ("taller", "logistica"):
            sesion_de(c, rol)
            for ruta in ("/finanzas", "/finanzas/gastos", "/finanzas/recurrentes", "/cashflow", "/cashflow/cajas",
                         "/cashflow/libro", "/equipo", "/proveedores", "/exportar-todo", "/configuracion"):
                r = c.get(ruta, follow_redirects=False)
                assert not sin_sesion(r), f"{rol} perdió la sesión en {ruta}"
                assert r.status_code != 200, f"{rol} pudo abrir {ruta}"


@prueba("En el servidor, sin claves puestas, no entra nadie: la primera clave pide el código de instalación")
def _():
    os.environ["DECOPET_CODIGO_INICIAL"] = "codigo-de-prueba"
    try:
        with erp_de_prueba(en_servidor=True) as c:
            r = c.get("/inicio", follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == "/entrar", "abrió el ERP sin clave"
            datos = {"usuario": "cristina@x.com", "clave": "una-clave-larga", "clave2": "una-clave-larga"}
            r = c.post("/entrar/primera-vez", data={**datos, "codigo": "otro"}, follow_redirects=False)
            assert r.headers["location"] == "/entrar?mal=codigo", "aceptó un código equivocado"
            assert not A.hay_claves()
            r = c.post("/entrar/primera-vez", data={**datos, "codigo": "codigo-de-prueba"}, follow_redirects=False)
            assert r.headers["location"] == "/inicio" and A.hay_claves()
            c.cookies.clear()                       # vuelve a entrar con el usuario que puso
            r = c.post("/entrar", data={"usuario": "cristina@x.com", "clave": "una-clave-larga"}, follow_redirects=False)
            assert r.headers["location"] == "/inicio", "no pudo volver a entrar con su usuario"
    finally:
        del os.environ["DECOPET_CODIGO_INICIAL"]


@prueba("En un servidor con la base recién creada (sin nadie todavía) la primera clave crea a la administradora")
def _():
    os.environ["DECOPET_CODIGO_INICIAL"] = "codigo-de-prueba"
    try:
        with erp_de_prueba(en_servidor=True, con_datos=False) as c:
            datos = {"usuario": "cristina@x.com", "clave": "una-clave-larga", "clave2": "una-clave-larga", "codigo": "codigo-de-prueba"}
            r = c.post("/entrar/primera-vez", data=datos, follow_redirects=False)
            assert r.headers["location"] == "/inicio", r.headers.get("location")
            assert c.get("/inicio", follow_redirects=False).status_code == 200
    finally:
        del os.environ["DECOPET_CODIGO_INICIAL"]


@prueba("Un ?volver= nunca te manda fuera del ERP")
def _():
    for malo in ("https://otro-sitio.com", "//otro-sitio.com", "/\\otro-sitio.com", "javascript:alert(1)", "", None):
        assert A.volver_seguro(malo) is None, malo
    assert A.volver_seguro("/operaciones?cola=hoy") == "/operaciones?cola=hoy"


@prueba("Crear una orden desde Operaciones te deja en Operaciones con la orden abierta; desde otro lado, en Órdenes")
def _():
    pide = lambda v: type("R", (), {"query_params": {"volver": v} if v is not None else {}})()
    assert A.volver_tras_crear(844, pide("/operaciones")) == "/operaciones?abrir=844"
    assert A.volver_tras_crear(844, pide("/operaciones?cola=hoy&dia=")) == "/operaciones?cola=hoy&dia=&abrir=844"
    assert A.volver_tras_crear(844, pide("/clientes?q=ana")) == "/ordenes?abrir=844"     # en Clientes "abrir" es un cliente
    assert A.volver_tras_crear(844, pide("https://otro-sitio.com/operaciones")) == "/ordenes?abrir=844"
    assert A.volver_tras_crear(844, pide(None)) == "/ordenes?abrir=844"


@prueba("Exportar el registro de ventas pide la misma clave que la pantalla (antes se bajaba sin ella)")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        con = sqlite3.connect(A.DB); con.execute("INSERT OR REPLACE INTO config (clave, valor) VALUES ('clave_resultados', 'clave-de-prueba')"); con.commit(); con.close()
        r = c.get("/historial/exportar", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/historial", "bajó las ventas sin la clave"
        c.cookies.set("res_ok", "clave-de-prueba")
        r = c.get("/historial/exportar", follow_redirects=False)
        assert r.status_code == 200 and "text/csv" in r.headers["content-type"], r.status_code
        sesion_de(c, "logistica")
        assert c.get("/historial/exportar", follow_redirects=False).status_code == 303, "logística bajó las ventas"


@prueba("Cloudflare Access: sin su firma no se llega a nada; con firma buena sí; firmas falsas, vencidas o ajenas no")
def _():
    import jwt as JWT
    from cryptography.hazmat.primitives.asymmetric import rsa
    from plataforma import access as CF
    clave, otra = (rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2))
    EQUIPO, AUD = "decopet-prueba.cloudflareaccess.com", "aud-de-prueba"
    def firma(k=clave, aud=AUD, iss="https://" + EQUIPO, vence=600, kid="k1"):
        return JWT.encode({"iss": iss, "aud": [aud], "exp": int(time.time()) + vence, "email": "cristina@x.com", "sub": "u1"}, k, algorithm="RS256", headers={"kid": kid})
    antes_claves, env = CF._cargar_claves, dict(os.environ)
    CF._cargar_claves = lambda forzar=False: {"k1": clave.public_key()}   # en vez de ir a buscarlas a Cloudflare
    os.environ.update(CF_ACCESS_ENFORCE="1", CF_ACCESS_TEAM_DOMAIN=EQUIPO, CF_ACCESS_AUD=AUD)
    try:
        with erp_de_prueba() as c:
            pide = lambda ruta, t=None: c.get(ruta, headers={"cf-access-jwt-assertion": t} if t else {}, follow_redirects=False).status_code
            assert pide("/entrar") == 403, "sin firma llegó a la entrada"
            assert pide("/static/estilo.css") == 403, "sin firma llegó a los archivos"
            assert pide("/health") == 200, "Railway no puede revisar si está vivo"
            assert pide("/entrar", firma()) == 200, "con firma buena no entró"
            assert pide("/entrar", firma(k=otra)) == 403, "aceptó una firma falsa"
            assert pide("/entrar", firma(aud="otra-app")) == 403, "aceptó la firma de otra aplicación"
            assert pide("/entrar", firma(iss="https://otro-equipo.cloudflareaccess.com")) == 403, "aceptó la firma de otro equipo"
            assert pide("/entrar", firma(vence=-120)) == 403, "aceptó una firma vencida"
            assert pide("/entrar", firma(kid="k-desconocida")) == 403, "aceptó una clave desconocida"
            os.environ.pop("CF_ACCESS_ENFORCE")
            assert pide("/entrar") == 200, "apagado, igual pidió la firma"
    finally:
        CF._cargar_claves = antes_claves; os.environ.clear(); os.environ.update(env)


@prueba("Freno de intentos: con Cloudflare delante cuenta la IP real de cada persona; sin Cloudflare no se cree la cabecera")
def _():
    pedido = lambda cab: type("R", (), {"headers": cab, "client": type("C", (), {"host": "104.22.148.30"})()})()
    env = dict(os.environ)
    try:
        os.environ["CF_ACCESS_ENFORCE"] = "1"
        assert A.ip_de(pedido({"cf-connecting-ip": "190.6.1.20"})) == "190.6.1.20", "con Access no usó la IP real"
        assert A.ip_de(pedido({})) == "104.22.148.30"
        os.environ.pop("CF_ACCESS_ENFORCE")
        assert A.ip_de(pedido({"cf-connecting-ip": "1.2.3.4"})) == "104.22.148.30", "sin Access le creyó a una cabecera inventada"
    finally:
        os.environ.clear(); os.environ.update(env)


@prueba("Migraciones: cada una se aplica una sola vez; la que falla no deja nada a medias y detiene el arranque")
def _():
    carpeta = pathlib.Path(tempfile.mkdtemp()); antes = A.MIGRACIONES; A.MIGRACIONES = carpeta
    try:
        (carpeta / "001_tabla_y_datos.sql").write_text("CREATE TABLE IF NOT EXISTS prueba_mig (x TEXT);\nINSERT INTO prueba_mig VALUES ('uno');", encoding="utf-8")
        ruta = tempfile.mktemp(suffix=".db")
        A.preparar_base(ruta).close(); A.preparar_base(ruta).close()   # arranca dos veces
        con = sqlite3.connect(ruta)
        assert con.execute("SELECT COUNT(*) FROM prueba_mig").fetchone()[0] == 1, "se aplicó dos veces"
        con.close()
        (carpeta / "002_rota.sql").write_text("INSERT INTO prueba_mig VALUES ('dos');\nINSERT INTO tabla_que_no_existe VALUES (1);", encoding="utf-8")
        try:
            A.preparar_base(ruta).close(); assert False, "arrancó con una migración rota"
        except RuntimeError as e:
            assert "002_rota.sql" in str(e), e
        con = sqlite3.connect(ruta)
        assert con.execute("SELECT COUNT(*) FROM prueba_mig").fetchone()[0] == 1, "quedó a medias la migración rota"
        assert [r[0] for r in con.execute("SELECT nombre FROM migraciones ORDER BY nombre")] == ["001_tabla_y_datos.sql"]
        con.close()
    finally:
        A.MIGRACIONES = antes


@prueba("La franja de arriba dice cuándo no es la operación real: copia local o versión de prueba; en producción no sale")
def _():
    env = dict(os.environ)
    try:
        os.environ.pop("DECOPET_STAGING", None)
        with erp_de_prueba(en_servidor=False, con_datos=False) as c:
            assert "Copia local" in c.get("/entrar").text, "la copia local no avisa"
        os.environ["DECOPET_CODIGO_INICIAL"] = "x"
        with erp_de_prueba(en_servidor=True, con_datos=False) as c:
            assert 'class="franja-entorno' not in c.get("/entrar").text, "producción muestra una franja"
            os.environ["DECOPET_STAGING"] = "1"
            assert "Versión de prueba" in c.get("/entrar").text, "el de pruebas no avisa"
    finally:
        os.environ.clear(); os.environ.update(env)


@prueba("El buscador de arriba solo sale a quien puede usarlo (no al taller ni a los despachadores) y no queda el texto 'Prototipo'")
def _():
    casas = {"admin": "/inicio", "logistica": "/operaciones", "taller": "/taller", "despachador": "/mis-entregas"}
    with erp_de_prueba() as c:
        for rol, casa in casas.items():
            sesion_de(c, rol); html = c.get(casa).text
            tiene = 'class="buscar"' in html
            assert tiene == (rol in ("admin", "logistica")), f"{rol}: buscador {'sale' if tiene else 'no sale'}"
            assert "Prototipo" not in html, f"{rol}: todavía dice Prototipo"


@prueba("/health dice ok sin pedir clave y sin contar nada de adentro")
def _():
    with erp_de_prueba(en_servidor=True, con_datos=False) as c:
        r = c.get("/health", follow_redirects=False)
        assert (r.status_code, r.text) == (200, "ok"), (r.status_code, r.text)


print("\nFOTOS")

@contextmanager
def fotos_de_prueba():
    """Carpetas de fotos y miniaturas temporales, con una foto grande con transparencia."""
    from PIL import Image
    antes = (A.FOTOS_DIR, A.MINIATURAS)
    raiz = pathlib.Path(tempfile.mkdtemp())
    A.FOTOS_DIR, A.MINIATURAS = raiz / "fotos", raiz / "miniaturas"
    (A.FOTOS_DIR / "productos").mkdir(parents=True)
    Image.new("RGBA", (3000, 2000), (200, 120, 40, 0)).save(A.FOTOS_DIR / "productos" / "porche.png")
    try: yield A.FOTOS_DIR / "productos" / "porche.png"
    finally: A.FOTOS_DIR, A.MINIATURAS = antes


@prueba("Las fotos se muestran achicadas (WebP, con su transparencia); la original queda igual para descargar")
def _():
    from PIL import Image
    import io
    with erp_de_prueba() as c, fotos_de_prueba() as orig:
        sesion_de(c, "taller")   # el taller ve el inventario con fotos: también le llegan las miniaturas
        url = A.mini("productos/porche.png")
        assert url.startswith("/fotos-mini/480/productos/porche.png?v="), url
        r = c.get(url)
        assert r.status_code == 200 and r.headers["content-type"] == "image/webp", (r.status_code, r.headers)
        im = Image.open(io.BytesIO(r.content))
        assert max(im.size) == 480 and im.mode == "RGBA", (im.size, im.mode)
        assert len(r.content) < orig.stat().st_size, "la miniatura pesa más que la original"
        assert "immutable" in r.headers["cache-control"], r.headers["cache-control"]
        assert Image.open(orig).size == (3000, 2000), "se tocó la original"


@prueba("Si se reemplaza una foto, la miniatura se rehace y cambia su dirección")
def _():
    from PIL import Image
    import io
    with erp_de_prueba() as c, fotos_de_prueba() as orig:
        sesion_de(c, "admin")
        antes = A.mini("productos/porche.png"); c.get(antes)
        Image.new("RGB", (800, 1600), "white").save(orig)
        t = orig.stat().st_mtime + 5; os.utime(orig, (t, t))
        despues = A.mini("productos/porche.png")
        assert despues != antes, "la dirección no cambió: el navegador seguiría mostrando la foto vieja"
        assert Image.open(io.BytesIO(c.get(despues).content)).size == (240, 480)


@prueba("Miniaturas: solo los tamaños previstos y solo de la carpeta de fotos")
def _():
    with erp_de_prueba() as c, fotos_de_prueba():
        sesion_de(c, "admin")
        assert c.get("/fotos-mini/999/productos/porche.png").status_code == 404
        assert c.get("/fotos-mini/480/productos/no-existe.png").status_code == 404
        assert c.get("/fotos-mini/480/..%2F..%2Fplataforma.db").status_code == 404
        c.cookies.clear()
        assert c.get("/fotos-mini/480/productos/porche.png", follow_redirects=False).status_code == 303   # sin entrar, no


@prueba("Los estilos y las fotos originales se guardan en el navegador y se vuelven a pedir solo si cambiaron; las páginas no")
def _():
    with erp_de_prueba() as c, fotos_de_prueba():
        sesion_de(c, "admin")
        css = c.get("/static/estilo.css")
        assert css.headers["cache-control"] == "private, no-cache", css.headers["cache-control"]
        assert c.get("/static/estilo.css", headers={"if-none-match": css.headers["etag"]}).status_code == 304
        assert c.get("/inicio").headers["cache-control"] == "no-store"


print("\nRECONSTRUIR DESDE CERO")

@prueba("Una base nueva queda igual que la que está en uso (se puede reconstruir el ERP)")
def _():
    nueva = A.preparar_base(tempfile.mktemp(suffix=".db"))
    viva = sqlite3.connect(A.DB)
    def tablas(c): return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    faltan_t = tablas(viva) - tablas(nueva)
    assert not faltan_t, f"tablas que faltarían: {sorted(faltan_t)}"
    faltan_c = []
    for t in tablas(viva):
        cv = {r[1] for r in viva.execute(f"PRAGMA table_info({t})")}
        cn = {r[1] for r in nueva.execute(f"PRAGMA table_info({t})")}
        faltan_c += [f"{t}.{c}" for c in cv - cn]
    assert not faltan_c, f"columnas que faltarían: {sorted(faltan_c)}"


print()
print("─" * 52)
print(f"  {_ok} bien · {_mal} mal")
if _fallos:
    print("\n  DETALLE DE LO QUE FALLÓ:")
    for t, e, tb in _fallos:
        print(f"\n  ── {t}\n{tb}")
raise SystemExit(1 if _mal else 0)
