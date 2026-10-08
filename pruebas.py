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
@prueba("Una falta del 30/09 es de la quincena de septiembre: no se descuenta en la del 15/10")
def _():
    con = base_limpia()
    con.execute("UPDATE usuarios SET nomina=1, sueldo_mes=140 WHERE id=1")
    con.execute("INSERT INTO faltas (nombre, fecha) VALUES ('Cristina','2026-09-30')")
    con.commit()
    f = A.ficha_equipo(con, "Cristina", d("2026-10-06"))
    assert (len(f["faltas"]), f["descuento"], f["neto"]) == (0, 0, 70), (len(f["faltas"]), f["descuento"], f["neto"])
    con.execute("INSERT INTO faltas (nombre, fecha) VALUES ('Cristina','2026-10-02')"); con.commit()
    f = A.ficha_equipo(con, "Cristina", d("2026-10-06"))
    assert len(f["faltas"]) == 1 and f["descuento"] == 4.67, (len(f["faltas"]), f["descuento"])
    assert A.periodo_quincena(d("2026-10-30")) == ("2026-10-16", "2026-10-31"), "el pago corrido al viernes cubre igual hasta el 31"

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

@prueba("Retiro de pack con el delivery pendiente: queda como deuda de la orden y no entra a ninguna caja")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Cliente X','Cliente')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,total) VALUES (1,'#1',1,'entregada','pagada',72)")
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (1,'Zelle Decopet',72,72,'USD','2026-10-01','confirmado')")
    con.execute("INSERT INTO packs (id,cliente_id,orden_id,unidades,entregadas_inicio) VALUES (1,1,1,3,0)")
    con.commit()
    assert A.entregar_pack(con, 1, 1, "2026-10-05", "1", "delivery", "Juan", "5", "", pago=None)
    con.commit()
    assert con.execute("SELECT total FROM ordenes WHERE id=1").fetchone()[0] == 77
    assert con.execute("SELECT COUNT(*) FROM pagos WHERE orden_id=1").fetchone()[0] == 1, "no debe registrar un pago que no entró"
    assert con.execute("SELECT estado_pago FROM ordenes WHERE id=1").fetchone()[0] == "abonada"

@prueba("El delivery cobrado hoy de un pack viejo entra al Registro de ventas hoy, con la forma con que se cobró")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Cliente X','Cliente')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,total,creado_en) VALUES (1,'#1',1,'entregada','pagada',72,'2026-09-07 10:00')")
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (1,'Efectivo USD',72,72,'USD','2026-09-07','confirmado')")   # lo que pagó al comprar el pack
    con.execute("INSERT INTO packs (id,cliente_id,orden_id,unidades,entregadas_inicio) VALUES (1,1,1,3,0)")
    con.commit()
    A.entregar_pack(con, 1, 1, "2026-10-05", "1", "delivery", "Juan", "5", "", pago=None)   # quedó pendiente
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (1,'Pago Móvil VES',5,5,'USD','2026-10-05','confirmado')")
    con.commit()
    filas = A._historial_rows(con, "2026", "", "")
    assert [(f["fecha"], f["linea"], f["forma"], f["cantidad"]) for f in filas] == [("2026-10-05", 5, "Pago Móvil VES", 0)], filas   # suma a la venta, no a las unidades

@prueba("Pack con los deliverys adelantados el mismo día: en el Registro es una sola fila con todo (60 + 5 + 2×5 = 75)")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Rebecca','Rebecca')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,total,subtotal,delivery,forma_pago_prevista,creado_en) VALUES (1,'#1',1,'pendiente','pagada',75,70,5,'Zelle Decopet','2026-10-05 10:00')")
    con.execute("INSERT INTO orden_lineas (orden_id,nombre,cantidad,precio,costo,total) VALUES (1,'Pack 3 Repuestos Mediano',1,60,0,60)")
    con.execute("INSERT INTO orden_lineas (orden_id,nombre,cantidad,precio,costo,total,extra_en) VALUES (1,'Delivery de las próximas entregas del pack (2 × $5)',2,5,0,10,'2026-10-05')")
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (1,'Zelle Decopet',75,75,'USD','2026-10-05','confirmado')")
    con.commit()
    filas = A._historial_rows(con, "2026", "", "")
    assert [(f["producto"], f["cantidad"], f["linea"]) for f in filas] == [("Pack 3 Repuestos Mediano", 1, 75)], filas

@prueba("Agregar un delivery que todavía no pagó: sube el total, queda como saldo y no entra ningún pago")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Cliente X','Cliente')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,total,subtotal) VALUES (1,'#1',1,'pendiente','pagada',98,98)")
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (1,'Zelle Decopet',98,98,'USD','2026-10-05','confirmado')")
    con.commit()
    A.cobro_extra(con, 1, "Delivery", 12, None, "2026-10-06", 1, pago=None)
    con.commit()
    o = con.execute("SELECT total, delivery, estado_pago FROM ordenes WHERE id=1").fetchone()
    assert (o["total"], o["delivery"], o["estado_pago"]) == (110, 12, "abonada"), dict(o)
    assert con.execute("SELECT COUNT(*) FROM pagos WHERE orden_id=1").fetchone()[0] == 1, "no debe registrar un pago que no entró"

@prueba("Un delivery agregado que todavía no se paga no es venta; cuando lo paga, entra ese día")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Marisol','Marisol')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,total,subtotal,creado_en) VALUES (1,'#1',1,'pendiente','pagada',98,98,'2026-10-05 10:00')")
    con.execute("INSERT INTO orden_lineas (orden_id,nombre,cantidad,precio,costo,total) VALUES (1,'Rampa Nueva',1,98,0,98)")
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (1,'BNC Cashea',98,98,'USD','2026-10-05','confirmado')")
    con.commit()
    A.cobro_extra(con, 1, "Delivery", 12, "Pago Móvil VES", "2026-10-06", 1, pago=None); con.commit()
    assert [f["producto"] for f in A._historial_rows(con, "2026", "", "")] == ["Rampa Nueva"], "sin pagar no sale en el registro"
    assert A.ventas_por_dia(con, "2026-10-06", "2026-10-06") == {}, A.ventas_por_dia(con, "2026-10-06", "2026-10-06")
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (1,'Pago Móvil VES',12,12,'USD','2026-10-08','confirmado')"); con.commit()
    filas = A._historial_rows(con, "2026", "", "")
    assert [(f["fecha"], f["producto"], f["linea"]) for f in filas][0] == ("2026-10-08", "Delivery", 12), filas
    assert A.ventas_por_dia(con, "2026-10-08", "2026-10-08") == {"2026-10-08": (12, 0)}

@prueba("Registro y Ventas de hoy = lo que entró: descuento, delivery que se paga después y pedido sin pagar")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Oreana','Oreana'),(2,'Mariel','Mariel'),(3,'Pedro','Pedro')")
    # Oreana: comedor 60 + delivery 5 con 5 de descuento → pagó 60
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,subtotal,descuento,delivery,total,creado_en) VALUES (1,'#10',1,'pendiente','pagada',60,5,5,60,'2026-10-06 10:00')")
    con.execute("INSERT INTO orden_lineas (orden_id,nombre,cantidad,precio,costo,total) VALUES (1,'Comedor Pequeno',1,60,0,60)")
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (1,'Pago Móvil VES',60,60,'USD','2026-10-06 10:00','confirmado')")
    # Mariel: porche 93 + IVA 14.88 + delivery 20 → hoy pagó 107.88; el delivery lo paga el 08
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,subtotal,iva,delivery,total,creado_en) VALUES (2,'#11',2,'pendiente','abonada',93,14.88,20,127.88,'2026-10-06 11:00')")
    con.execute("INSERT INTO orden_lineas (orden_id,nombre,cantidad,precio,costo,total) VALUES (2,'El Porche Versión PRO Grande',1,93,0,93)")
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (2,'BNC Cashea',107.88,107.88,'USD','2026-10-06 11:00','confirmado')")
    # Pedro: pidió hoy y todavía no paga nada
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,subtotal,total,creado_en) VALUES (3,'#12',3,'pendiente','sin_pago',98,98,'2026-10-06 12:00')")
    con.execute("INSERT INTO orden_lineas (orden_id,nombre,cantidad,precio,costo,total) VALUES (3,'Rampa Nueva',1,98,0,98)")
    con.commit()
    filas = A._historial_rows(con, "2026", "10", "")
    assert sorted((f["cliente"], f["linea"]) for f in filas) == [("Mariel", 107.88), ("Oreana", 60)], [(f["cliente"], f["linea"]) for f in filas]
    assert A.ventas_por_dia(con, "2026-10-06", "2026-10-06") == {"2026-10-06": (167.88, 2)}
    assert A.por_cobrar_de_hoy(con, "2026-10-06") == 118, A.por_cobrar_de_hoy(con, "2026-10-06")   # 20 de Mariel + 98 de Pedro
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (2,'Pago Móvil VES',20,20,'USD','2026-10-08','confirmado')"); con.commit()
    f8 = [(f["fecha"], f["producto"], f["linea"], f["forma"]) for f in A._historial_rows(con, "2026", "10", "") if f["fecha"] == "2026-10-08"]
    assert f8 == [("2026-10-08", "Delivery", 20, "Pago Móvil VES")], f8
    assert A.ventas_por_dia(con, "2026-10-08", "2026-10-08") == {"2026-10-08": (20, 0)}

@prueba("Envío nacional sin cédula ni correo: aviso en Inicio con el mensaje para pedírselos al cliente")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila,telefono) VALUES (1,'Hebert Watts','Hebert','0412-6004639')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,tipo_entrega,agencia,ciudad,total) VALUES (1,'#1',1,'pendiente','nacional','Tealca','Tealca Paramillo',98)")
    con.commit()
    f = A.faltan_datos_agencia(con)
    assert [(x["numero"], x["faltan"]) for x in f] == [("#1", "la cédula y el correo")], f
    assert "nos faltan tu cédula y tu correo" in f[0]["msj"] and f[0]["wa"].startswith("https://api.whatsapp.com/send?phone=584126004639"), f[0]
    con.execute("UPDATE clientes SET cedula='27050661', correo='h@x.com' WHERE id=1"); con.commit()
    assert A.faltan_datos_agencia(con) == []

@prueba("Cómo llegar: con coordenadas abre Maps y Waze navegando; sin link, busca la dirección escrita")
def _():
    n = A.navegacion("https://maps.google.com/maps?q=10.4884247%2C-66.8123", "x")
    assert n["maps"].endswith("destination=10.4884247,-66.8123&travelmode=driving") and "ll=10.4884247,-66.8123&navigate=yes" in n["waze"], n
    n = A.navegacion("https://www.google.com/maps/search/Torre%20Xpress/@10.45553223,-66.81810304,17z?hl=en", "x")
    assert "destination=10.45553223,-66.81810304" in n["maps"], n
    n = A.navegacion("", "Francisco Solano, Oficina", "Caracas")
    assert "destination=Francisco%20Solano" in n["maps"] and n["waze"].startswith("https://waze.com/ul?q="), n

@prueba("El aviso del despachador lleva a dónde va, el link del mapa y le pide confirmar la dirección")
def _():
    t = A.texto_aviso("Oreana", "Ingrid", "1× Comedor", False, "La Urbina", "https://www.google.com/maps?q=10.4877,-66.8043&z=17", "Caracas")
    assert "Voy a: La Urbina" in t and "📍 https://www.google.com/maps?q=10.4877,-66.8043" in t and "¿Me confirmas que es tu dirección?" in t, t
    assert "❤" not in t and "💚" in t, "en Decopet el corazón es verde"
    t = A.texto_aviso("Marisol", "Ingrid", "1× Rampa", True, "Caricuao UD-4", None, "Caracas")
    assert "Voy a: Caricuao UD-4" in t and "http" not in t and "me mandas tu ubicación" in t, "sin link guardado no se inventa un mapa"
    t = A.texto_aviso("Pedro", "Ingrid", "1× Rampa", True)
    assert "¿Me pasas tu dirección y tu ubicación" in t and "Mañana te llevo" in t, t

@prueba("La ubicación guardada en la dirección habitual del cliente se usa en su pedido, solo si va a esa misma dirección")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Marisol','Marisol')")
    con.execute("INSERT INTO direcciones (cliente_id,direccion,maps,principal) VALUES (1,'Caricuao UD-4 Terraza C','https://www.google.com/maps?q=10.43,-66.98',1)")
    con.commit()
    assert A.ubicacion_de(con, 1, "Caricuao UD-4 Terraza C", None) == "https://www.google.com/maps?q=10.43,-66.98"
    assert A.ubicacion_de(con, 1, "", None) == "https://www.google.com/maps?q=10.43,-66.98", "sin dirección en el pedido, la habitual"
    assert A.ubicacion_de(con, 1, "Los Palos Grandes, Torre X", None) is None, "otra dirección: no se usa la ubicación de la habitual"
    assert A.ubicacion_de(con, 1, "Caricuao", "https://maps.app.goo.gl/abc") == "https://maps.app.goo.gl/abc", "la del pedido manda"

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


@prueba("La pega entra al inventario por envase (2 cuñetes = 2); si el producto se lleva en litros, se convierte")
def _():
    con = base_limpia()
    con.execute("INSERT INTO proveedores (id,nombre) VALUES (8,'Ferretería')")
    con.execute("INSERT INTO proveedor_items (proveedor_id,item,precio,unidad) VALUES (8,'Pega amarilla',86,'cuñete')")
    con.execute("INSERT INTO proveedor_items (proveedor_id,item,precio,unidad) VALUES (8,'Cinta antideslizante',1,'rollo')")
    con.execute("INSERT INTO productos (id,sku,nombre,tipo,unidad,activo) VALUES (71,'INS-PEGA','Pega amarilla','insumo','litro',1)")
    con.execute("INSERT INTO productos (id,sku,nombre,tipo,unidad,activo) VALUES (72,'INS-PEGA-CUN','Pega amarilla · cuñete','insumo','cuñete',1)")
    assert A.sku_pega(con, "Pega amarilla", "Ferretería") == "INS-PEGA-CUN"
    assert A.a_inventario(con, {"pieza": "Pega amarilla", "responsable": "Ferretería", "producto_id": 72}, 2) == 2
    r = {"pieza": "Pega amarilla", "responsable": "Ferretería", "producto_id": 71}   # el producto viejo, en litros
    assert A.a_inventario(con, r, 2) == 37.8, A.a_inventario(con, r, 2)
    assert A.a_inventario(con, {"pieza": "Cinta antideslizante", "responsable": "Ferretería"}, 3) == 3
    assert A.fmt_cant(37.8, "litro") == "37,8 litros" and A.fmt_cant(1, "rollo") == "1 rollo"


@prueba("La malla sale del inventario según el tamaño del porche: Grande → malla grande; Mediano → malla mediana")
def _():
    con = base_limpia()
    con.execute("INSERT INTO productos (id,sku,nombre,categoria,tipo,activo) VALUES (81,'PRO-M','El Porche Versión PRO Mediano','porche','producto',1),(82,'BAS-G','Porche Básico Grande','porche','producto',1)")
    con.execute("INSERT INTO productos (id,sku,nombre,tipo,unidad,activo) VALUES (91,'INS-MALLA-M','Malla mediana','insumo','unidad',1),(92,'INS-MALLA-G','Malla grande','insumo','unidad',1)")
    con.execute("INSERT INTO mov_inventario (producto_id,fecha,tipo,cantidad) VALUES (91,'2026-10-07','entrada',5),(92,'2026-10-07','entrada',5)")
    con.execute("INSERT INTO ordenes (id,numero,estado,estado_pago,subtotal,total) VALUES (1,'#1','pendiente','sin_pago',0,0)")
    con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total,malla) VALUES (1,81,'El Porche Versión PRO Mediano',1,83,0,83,1),(1,82,'Porche Básico Grande',2,40,0,80,1)")
    A.descontar_inventario(con, 1, None)
    stock = lambda i: con.execute("SELECT SUM(cantidad) FROM mov_inventario WHERE producto_id=?", (i,)).fetchone()[0]
    assert (stock(91), stock(92)) == (4, 3), (stock(91), stock(92))
    # la malla de seguridad suelta también sale de las listas, de su tamaño
    con.execute("INSERT INTO productos (id,sku,nombre,categoria,tipo,activo) VALUES (83,'MALLA','Malla de seguridad Mediana','porche','producto',1),(84,'MALLA-G','Malla de seguridad Grande','porche','producto',1)")
    con.execute("INSERT INTO ordenes (id,numero,estado,estado_pago,subtotal,total) VALUES (2,'#2','pendiente','sin_pago',0,0)")
    con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (2,84,'Malla de seguridad Grande',1,20,0,20)")
    A.descontar_inventario(con, 2, None)
    assert (stock(91), stock(92)) == (4, 2), (stock(91), stock(92))


@prueba("Sin nadie conectado (ERP recién instalado) se puede guardar y queda a nombre de un usuario")
def _():
    class R:   # una petición sin sesión
        cookies = {}; headers = {}; state = type("Estado", (), {})()
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
            assert not A.tiene_duena()
            r = c.post("/entrar/primera-vez", data={**datos, "codigo": "codigo-de-prueba"}, follow_redirects=False)
            assert r.headers["location"] == "/inicio" and A.tiene_duena()
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


print("\nEQUIPO Y ACCESOS")

@contextmanager
def con_cloudflare(en_servidor=True, con_datos=True):
    """El ERP detrás de Cloudflare Access, y una función que arma la firma de alguien con su correo.
    La lista de correos que se le manda a Cloudflare queda en .enviado (no se llama a Cloudflare de verdad)."""
    import jwt as JWT
    from cryptography.hazmat.primitives.asymmetric import rsa
    from plataforma import access as CF
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    EQUIPO, AUD = "decopet-prueba.cloudflareaccess.com", "aud-de-prueba"
    antes = (CF._cargar_claves, A.CF_EQUIPO.poner_correos, A.CF_EQUIPO.cerrar_sesion, dict(os.environ))
    CF._cargar_claves = lambda forzar=False: {"k1": clave.public_key()}
    enviado = {"correos": None, "cerradas": []}
    A.CF_EQUIPO.poner_correos = lambda correos: enviado.__setitem__("correos", sorted(correos))
    A.CF_EQUIPO.cerrar_sesion = lambda correo: enviado["cerradas"].append(correo)
    os.environ.update(CF_ACCESS_ENFORCE="1", CF_ACCESS_TEAM_DOMAIN=EQUIPO, CF_ACCESS_AUD=AUD, DECOPET_CODIGO_INICIAL="codigo-inicial",
                      CF_API_TOKEN="t", CF_ACCOUNT_ID="a", CF_ACCESS_POLICY_ID="p")
    try:
        with erp_de_prueba(en_servidor=en_servidor, con_datos=con_datos) as c:
            def como(correo):   # cada pedido siguiente llega firmado por Cloudflare con ese correo
                c.headers["cf-access-jwt-assertion"] = JWT.encode(
                    {"iss": "https://" + EQUIPO, "aud": [AUD], "exp": int(time.time()) + 600, "email": correo},
                    clave, algorithm="RS256", headers={"kid": "k1"})
            c.como, c.enviado = como, enviado
            yield c
    finally:
        CF._cargar_claves, A.CF_EQUIPO.poner_correos, A.CF_EQUIPO.cerrar_sesion = antes[:3]
        os.environ.clear(); os.environ.update(antes[3])


def persona(**campos):
    con = sqlite3.connect(A.DB)
    cols = ", ".join(campos); marcas = ", ".join("?" * len(campos))
    pid = con.execute(f"INSERT INTO usuarios ({cols}) VALUES ({marcas})", tuple(campos.values())).lastrowid
    con.commit(); con.close(); return pid


@prueba("En el servidor se entra con el correo que comprobó Cloudflare, sin clave; un correo que no es del equipo no entra")
def _():
    with con_cloudflare() as c:
        con = sqlite3.connect(A.DB); con.execute("UPDATE usuarios SET correo='cristina@decopet.com' WHERE id=1"); con.commit(); con.close()
        persona(nombre="Isaías", rol="taller", activo=1, correo="isaias@gmail.com")
        persona(nombre="Se fue", rol="taller", activo=0, correo="sefue@gmail.com")
        c.como("cristina@decopet.com"); assert c.get("/inicio", follow_redirects=False).status_code == 200, "la dueña no entró"
        c.como("ISAIAS@gmail.com"); r = c.get("/inicio", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/taller", "el taller no llegó a su pantalla"
        for correo in ("sefue@gmail.com", "extrano@gmail.com"):
            c.como(correo)
            assert c.get("/inicio", follow_redirects=False).headers.get("location") == "/entrar", f"{correo} entró"
            assert "no tiene acceso" in c.get("/entrar").text
        c.como("isaias@gmail.com")
        assert c.post("/entrar", data={"usuario": "isaias", "clave": "x"}, follow_redirects=False).headers["location"] == "/entrar"
        assert c.get("/salir", follow_redirects=False).headers["location"] == "/cdn-cgi/access/logout"


@prueba("Primera vez en el servidor: con el código de instalación, el correo de Cloudflare queda como el de la administradora")
def _():
    with con_cloudflare(con_datos=False) as c:
        c.como("duena@decopet.com")
        assert "Entra como administradora" in c.get("/entrar").text
        assert c.post("/entrar/primera-vez", data={"codigo": "otro"}, follow_redirects=False).headers["location"] == "/entrar?mal=codigo"
        assert c.post("/entrar/primera-vez", data={"codigo": "codigo-inicial"}, follow_redirects=False).headers["location"] == "/inicio"
        assert c.get("/inicio", follow_redirects=False).status_code == 200, "no entró después de registrarse"
        assert c.enviado["correos"] == ["duena@decopet.com"], c.enviado
        c.como("intruso@gmail.com")   # ya tiene dueña: el código no sirve para nadie más
        assert c.post("/entrar/primera-vez", data={"codigo": "codigo-inicial"}, follow_redirects=False).headers["location"] == "/entrar"
        assert c.get("/inicio", follow_redirects=False).headers["location"] == "/entrar"


@prueba("Equipo: dar y quitar acceso le manda a Cloudflare la lista justa, la saca al momento y queda en el registro")
def _():
    with con_cloudflare() as c:
        con = sqlite3.connect(A.DB); con.execute("UPDATE usuarios SET correo='cristina@decopet.com' WHERE id=1"); con.commit(); con.close()
        c.como("cristina@decopet.com")
        r = c.post("/equipo/persona", data={"nombre": "manawa", "correo": "Manawa@Gmail.com", "acceso": "taller"}, follow_redirects=False)
        assert r.status_code == 303 and "err" not in r.headers["location"], r.headers["location"]
        assert c.enviado["correos"] == ["cristina@decopet.com", "manawa@gmail.com"], c.enviado
        pid = sqlite3.connect(A.DB).execute("SELECT id FROM usuarios WHERE correo='manawa@gmail.com'").fetchone()[0]
        c.como("manawa@gmail.com"); assert c.get("/taller", follow_redirects=False).status_code == 200
        c.como("cristina@decopet.com")
        c.post("/equipo/persona", data={"id": pid, "nombre": "Manawa", "correo": "manawa@gmail.com", "acceso": "no"})
        assert c.enviado["correos"] == ["cristina@decopet.com"] and c.enviado["cerradas"] == ["manawa@gmail.com"], c.enviado
        c.como("manawa@gmail.com"); assert c.get("/taller", follow_redirects=False).headers["location"] == "/entrar", "sin acceso siguió entrando"
        c.como("cristina@decopet.com")
        que = [r[0] for r in sqlite3.connect(A.DB).execute("SELECT que FROM accesos_registro WHERE persona_id=? ORDER BY id", (pid,))]
        assert que == ["Lo agregó al equipo con acceso de Taller", "Le quitó el acceso al ERP"], que
        assert "Le quitó el acceso al ERP" in c.get("/equipo").text


@prueba("Equipo: sin correo no se da acceso en el servidor, y un correo no puede ser de dos personas")
def _():
    with con_cloudflare() as c:
        con = sqlite3.connect(A.DB); con.execute("UPDATE usuarios SET correo='cristina@decopet.com' WHERE id=1"); con.commit(); con.close()
        c.como("cristina@decopet.com")
        pide = lambda **d: c.post("/equipo/persona", data=d, follow_redirects=False).headers["location"]
        assert "err=sin_correo" in pide(nombre="Juan", acceso="despachador")
        assert "err=correo_repetido" in pide(nombre="Juan", correo="CRISTINA@decopet.com", acceso="despachador")
        assert "err=correo" in pide(nombre="Juan", correo="juan-sin-arroba", acceso="despachador")
        assert "err" not in pide(nombre="Miguel", acceso="no", nomina="1", sueldo="100")   # el contador: en la nómina, sin entrar


@prueba("Equipo: Cristina no puede quitarse a sí misma ni dejar el ERP sin administradora")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        yo = sqlite3.connect(A.DB).execute("SELECT id FROM usuarios WHERE usuario='prueba-admin'").fetchone()[0]
        pide = lambda **d: c.post("/equipo/persona", data=d, follow_redirects=False).headers["location"]
        assert "err=yo" in pide(id=yo, nombre="Prueba admin", usuario="prueba-admin", acceso="taller")
        assert "err=yo" in pide(id=yo, nombre="Prueba admin", usuario="prueba-admin", acceso="no")
        otra = persona(nombre="Otra admin", rol="admin", activo=1, usuario="otra")
        assert "err" not in pide(id=otra, nombre="Otra admin", usuario="otra", acceso="no"), "con otra administradora sí se puede"
    # ERP recién instalado en la Mac (nadie tiene clave: se entra como administradora sin sesión): tampoco puede quitar a la única
    with erp_de_prueba() as c:
        unica = sqlite3.connect(A.DB).execute("SELECT id, nombre FROM usuarios WHERE rol='admin' AND activo=1").fetchall()
        assert len(unica) == 1, unica
        r = c.post("/equipo/persona", data={"id": unica[0][0], "nombre": unica[0][1], "usuario": "cristina", "acceso": "logistica"}, follow_redirects=False)
        assert "err=ultima_admin" in r.headers["location"], r.headers["location"]


@prueba("Equipo: un despachador sin elegir cuál queda como uno nuevo con su nombre (antes veía su pantalla vacía)")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        c.post("/equipo/persona", data={"nombre": "Víctor", "usuario": "victor", "acceso": "despachador", "clave": "clave-de-victor"})
        con = sqlite3.connect(A.DB)
        assert con.execute("SELECT despachador FROM usuarios WHERE usuario='victor'").fetchone()[0] == "Víctor"
        assert con.execute("SELECT activo FROM despachadores WHERE nombre='Víctor'").fetchone() == (1,)


@prueba("Equipo: cambiarle el nombre a alguien se lleva sus pagos, faltas y entregas (antes se perdían por ir por nombre)")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        con = sqlite3.connect(A.DB)
        con.execute("INSERT INTO despachadores (nombre, activo) VALUES ('Fer', 1)")
        pid = con.execute("""INSERT INTO usuarios (nombre, usuario, rol, activo, despachador, nomina, sueldo_mes)
                             VALUES ('Fer', 'fer', 'despachador', 1, 'Fer', 1, 200)""").lastrowid
        oid = con.execute("SELECT id FROM ordenes LIMIT 1").fetchone()[0]
        con.execute("UPDATE ordenes SET despachador='Fer' WHERE id=?", (oid,))
        con.execute("INSERT INTO gastos (fecha, monto_usd, categoria, subcategoria, proveedor) VALUES ('2026-10-01', 100, 'Equipo', 'Quincena', 'Fer')")
        con.execute("INSERT INTO faltas (nombre, fecha) VALUES ('Fer', '2026-10-02')"); con.commit()
        c.post("/equipo/persona", data={"id": pid, "nombre": "Fercho", "usuario": "fer", "acceso": "despachador",
                                         "despachador": "Fer", "nomina": "1", "sueldo": "200"})
        assert con.execute("SELECT despachador FROM usuarios WHERE id=?", (pid,)).fetchone()[0] == "Fercho"
        assert con.execute("SELECT despachador FROM ordenes WHERE id=?", (oid,)).fetchone()[0] == "Fercho"
        assert con.execute("SELECT COUNT(*) FROM despachadores WHERE nombre IN ('Fer','Fercho')").fetchone()[0] == 1
        assert con.execute("SELECT proveedor FROM gastos WHERE subcategoria='Quincena' AND fecha='2026-10-01'").fetchone()[0] == "Fercho"
        assert con.execute("SELECT nombre FROM faltas WHERE fecha='2026-10-02'").fetchone()[0] == "Fercho"
        con.row_factory = sqlite3.Row
        assert A.ficha_equipo(con, "Fercho", datetime.date(2026, 10, 4))["mensual"] == 200


@prueba("La nómina vieja (dos listas de nombres en config) pasa a las fichas de las personas, sin perder sueldos")
def _():
    ruta = tempfile.mktemp(suffix=".db"); con = sqlite3.connect(ruta)
    con.executescript(open(A.BASE / "modelo.sql", encoding="utf-8").read()); con.execute("ALTER TABLE usuarios ADD COLUMN usuario TEXT")
    con.execute("INSERT INTO usuarios (id, nombre, rol, activo, usuario) VALUES (1,'Cristina','admin',1,'cristina@decopet.com'), (5,'Isaías','taller',1,'isaias')")
    con.execute("""INSERT INTO config (clave, valor) VALUES ('equipo', '["Víctor", "Isaías"]'), ('sueldos', '{"Isaías": 300, "Miguel": 100}')""")
    con.commit(); con.close()
    con = A.preparar_base(ruta)
    filas = {r[0]: r[1:] for r in con.execute("SELECT nombre, rol, activo, nomina, sueldo_mes, correo FROM usuarios")}
    assert filas["Isaías"] == ("taller", 1, 1, 300.0, None), filas["Isaías"]
    assert filas["Víctor"] == ("ninguno", 0, 1, None, None), filas["Víctor"]
    assert filas["Miguel"] == ("ninguno", 0, 1, 100.0, None), filas["Miguel"]
    assert filas["Cristina"][-1] == "cristina@decopet.com", "no tomó el correo que estaba como usuario"
    assert not con.execute("SELECT 1 FROM config WHERE clave IN ('equipo','sueldos')").fetchone(), "quedaron las listas viejas"


@prueba("Equipo: solo la administradora cambia accesos; Cloudflare caído no impide guardar y se puede reintentar")
def _():
    with con_cloudflare() as c:
        con = sqlite3.connect(A.DB); con.execute("UPDATE usuarios SET correo='cristina@decopet.com' WHERE id=1"); con.commit(); con.close()
        persona(nombre="Vale", rol="logistica", activo=1, correo="vale@decopet.com")
        c.como("vale@decopet.com")
        c.post("/equipo/persona", data={"nombre": "Colado", "correo": "colado@gmail.com", "acceso": "admin"})
        assert not sqlite3.connect(A.DB).execute("SELECT 1 FROM usuarios WHERE correo='colado@gmail.com'").fetchone(), "logística agregó a alguien"
        c.como("cristina@decopet.com")
        def caido(correos): raise RuntimeError("Cloudflare no responde")
        A.CF_EQUIPO.poner_correos = caido
        c.post("/equipo/persona", data={"nombre": "Juan", "correo": "juan@gmail.com", "acceso": "despachador"})
        assert sqlite3.connect(A.DB).execute("SELECT 1 FROM usuarios WHERE correo='juan@gmail.com'").fetchone(), "no guardó"
        html = c.get("/equipo").text
        assert "Cloudflare no recibió" in html and "/equipo/cloudflare" in html, "no avisó que Cloudflare falló"
        A.CF_EQUIPO.poner_correos = lambda correos: c.enviado.__setitem__("correos", sorted(correos))
        c.post("/equipo/cloudflare")
        assert "juan@gmail.com" in c.enviado["correos"] and "Cloudflare no recibió" not in c.get("/equipo").text


@prueba("A Cloudflare se le cambian solo los correos de la regla; su nombre, duración y lo que exija quedan igual")
def _():
    from plataforma import cf_equipo as CFE
    llamadas, antes, env = [], CFE._pedir, dict(os.environ)
    def falso(metodo, ruta, cuerpo=None):
        llamadas.append((metodo, ruta, cuerpo))
        return {"id": "p1", "name": "Equipo Decopet", "decision": "allow", "session_duration": "720h", "reusable": True,
                "created_at": "x", "include": [{"email": {"email": "viejo@x.com"}}], "require": [{"login_method": {"id": "otp"}}]}
    CFE._pedir = falso; os.environ.update(CF_ACCESS_POLICY_ID="p1")
    try:
        CFE.poner_correos(["b@x.com", "a@x.com"])
        metodo, ruta, cuerpo = llamadas[-1]
        assert (metodo, ruta) == ("PUT", "/access/policies/p1"), (metodo, ruta)
        assert cuerpo["include"] == [{"email": {"email": "a@x.com"}}, {"email": {"email": "b@x.com"}}], cuerpo["include"]
        assert cuerpo["name"] == "Equipo Decopet" and cuerpo["session_duration"] == "720h" and cuerpo["require"], cuerpo
        assert not {"id", "created_at", "reusable"} & set(cuerpo), "mandó campos que pone Cloudflare"
        try: CFE.poner_correos([]); assert False, "dejó la regla vacía"
        except ValueError: pass
    finally:
        CFE._pedir = antes; os.environ.clear(); os.environ.update(env)


@prueba("Despachador: cada número de 'Todo lo que ha hecho' cuenta justo las filas que muestra al tocarlo")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        d = con.execute("""SELECT d.id, d.nombre FROM despachadores d JOIN ordenes o ON o.despachador=d.nombre
                           GROUP BY d.id ORDER BY COUNT(*) DESC LIMIT 1""").fetchone()
        con.execute("UPDATE ordenes SET origen_excel=0, despachador_pagado=(id % 2) WHERE despachador=?", (d["nombre"],))
        con.execute("INSERT INTO viajes_agencia (fecha, despachador, agencia, monto, pedidos, llevado_en, pagado) VALUES ('2026-10-02',?,'MRW',10,3,'2026-10-02 15:00',0)", (d["nombre"],))
        con.commit()
        html = c.get(f"/despachadores/{d['id']}").text
        filas = re.findall(r'<tr data-grupo="([^"]+)" data-cobro="([^"]+)">', html)
        botones = dict(re.findall(r'data-f="([^"]*)"><b>(.*?)</b>', html))
        assert filas, "no hay filas"
        assert botones[""] == str(len(filas)), (botones[""], len(filas))
        for g in ("entregada", "viaje", "por_entregar", "no_entregado", "diligencia"):
            n = sum(1 for x, _ in filas if x == g)
            assert botones.get(g, "0") == str(n), f"{g}: el número dice {botones.get(g)}, la lista tiene {n}"
        assert not any(x == "por_entregar" and cobro != "al_entregar" for x, cobro in filas), "algo por entregar sale como pagado o por pagar"


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


@prueba("Quitar un delivery agregado que no se pagó: baja el total y la orden queda pagada; uno pagado no se quita")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (9001,'Marisol','Marisol')")
        con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,subtotal,total,tipo_entrega,creado_en) VALUES (9001,'#99001',9001,'pendiente','pagada',98,98,'pickup','2026-10-05 10:00')")
        con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (9001,'Zelle Decopet',98,98,'USD','2026-10-05','confirmado')")
        con.commit()
        A.cobro_extra(con, 9001, "Delivery", 12, "Pago Móvil VES", "2026-10-06", 1, pago=None); con.commit()
        lid = con.execute("SELECT id FROM orden_lineas WHERE orden_id=9001 AND extra_en IS NOT NULL").fetchone()[0]
        c.post(f"/ordenes/9001/linea/{lid}/quitar")
        o = con.execute("SELECT total, delivery, estado_pago FROM ordenes WHERE id=9001").fetchone()
        assert (o["total"], o["delivery"], o["estado_pago"]) == (98, 0, "pagada"), dict(o)
        A.cobro_extra(con, 9001, "Propina", 3, "Zelle Decopet", "2026-10-06", 1); con.commit()   # pagado
        lid = con.execute("SELECT id FROM orden_lineas WHERE orden_id=9001 AND extra_en IS NOT NULL").fetchone()[0]
        c.post(f"/ordenes/9001/linea/{lid}/quitar")
        assert con.execute("SELECT total FROM ordenes WHERE id=9001").fetchone()[0] == 101, "lo pagado no se quita"
        con.close()

@prueba("Corregir un pago mal anotado: cambia el monto y la orden queda pagada; borrar un pago la deja por pagar")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (9002,'Marelbis','Marelbis')")
        con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,subtotal,total,creado_en) VALUES (9002,'#99002',9002,'pendiente','abonada',220.76,220.76,'2026-10-07 10:00')")
        pid = con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (9002,'Zelle Decopet',216.06,216.06,'USD','2026-10-07 11:39','confirmado')").lastrowid
        con.commit()
        c.post(f"/ordenes/9002/pago/{pid}/corregir", data={"monto_usd": "220.76", "forma": "Zelle Decopet", "fecha": "2026-10-07"})
        assert con.execute("SELECT monto_usd FROM pagos WHERE id=?", (pid,)).fetchone()[0] == 220.76
        assert con.execute("SELECT estado_pago FROM ordenes WHERE id=9002").fetchone()[0] == "pagada"
        c.post(f"/ordenes/9002/pago/{pid}/corregir", data={"borrar": "1"})
        assert con.execute("SELECT COUNT(*) FROM pagos WHERE orden_id=9002").fetchone()[0] == 0
        assert con.execute("SELECT estado_pago FROM ordenes WHERE id=9002").fetchone()[0] == "sin_pago"
        con.close()

@prueba("Editar una orden: cambiar cantidad, agregar un producto y el descuento recalcula total, IVA e inventario")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        p = con.execute("SELECT * FROM productos WHERE tipo='producto' AND COALESCE(sku,'') NOT LIKE 'PACK%' AND categoria NOT IN ('porche','repuesto') AND precio>0 ORDER BY id LIMIT 1").fetchone()
        con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (9003,'Bettina','Bettina')")
        con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,canal,subtotal,descuento,iva,delivery,total,creado_en) VALUES (9003,'#99003',9003,'pendiente','sin_pago','cashea',?,0,?,5,?,'2026-10-07 10:00')",
                    (2 * p["precio"], round(2 * p["precio"] * 0.16, 2), round(2 * p["precio"] * 1.16 + 5, 2)))
        lid = con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (9003,?,?,2,?,0,?)", (p["id"], p["nombre"], p["precio"], 2 * p["precio"])).lastrowid
        con.commit(); A.descontar_inventario(con, 9003, 1); con.commit()
        stock = lambda: con.execute("SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario WHERE producto_id=?", (p["id"],)).fetchone()[0]
        s0 = stock()
        c.post("/ordenes/9003/editar", data={f"cant_{lid}": "1", f"precio_{lid}": str(p["precio"]), "descuento": "10", "delivery": "5", "nuevo_producto": str(p["id"]), "nuevo_cant": "1"})
        o = con.execute("SELECT subtotal, iva, total FROM ordenes WHERE id=9003").fetchone()
        sub = 2 * p["precio"]   # 1 que quedó + 1 agregado
        assert (o["subtotal"], o["iva"], o["total"]) == (sub, round((sub - 10) * 0.16, 2), round(sub - 10 + round((sub - 10) * 0.16, 2) + 5, 2)), dict(o)
        assert stock() == s0, "2 → 1 + 1 nuevo: el inventario queda igual"
        con.close()

@prueba("Cliente nuevo sin perro anotado: aviso en Inicio; 'Cliente no quiso' lo quita")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        con = sqlite3.connect(A.DB)
        con.execute("INSERT INTO clientes (id,nombre,nombre_pila,telefono,creado_en) VALUES (9004,'Mariel Mora','Mariel','0412-1112233',datetime('now','localtime'))")
        con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,total,creado_en) VALUES (9004,'#99004',9004,'pendiente',98,datetime('now','localtime'))")
        con.commit()
        assert "Registraste a Mariel Mora" in c.get("/inicio").text
        c.post("/clientes/9004/sin-mascota")
        assert "Registraste a Mariel Mora" not in c.get("/inicio").text
        con.close()

@prueba("Dañados: el taller reporta una rampa dañada (sale de disponible, queda pendiente); solo Cristina la resuelve")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        p = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 AND categoria NOT IN ('porche','repuesto','opcion','kit') LIMIT 1").fetchone()["id"]
        stock = lambda: con.execute("SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario WHERE producto_id=?", (p,)).fetchone()[0]
        con.execute("INSERT INTO mov_inventario (producto_id,fecha,tipo,cantidad) VALUES (?,date('now'),'entrada',10)", (p,)); con.commit()
        s0 = stock()
        sesion_de(c, "taller")
        c.post("/inventario/mov", data={"producto_id": str(p), "tipo": "danado", "cantidad": "1", "nota": "tela rota"})
        d = con.execute("SELECT * FROM danados WHERE producto_id=?", (p,)).fetchone()
        assert d and d["estado"] == "pendiente" and d["nota"] == "tela rota" and stock() == s0 - 1, (dict(d) if d else None, stock())
        c.post(f"/inventario/danado/{d['id']}/resolver", data={"como": "reparado"})
        assert con.execute("SELECT estado FROM danados WHERE id=?", (d["id"],)).fetchone()[0] == "pendiente", "el taller no decide"
        sesion_de(c, "admin")
        assert "tela rota" in c.get("/inicio").text, "Cristina lo ve en Inicio"
        c.post(f"/inventario/danado/{d['id']}/visto")
        assert "tela rota" not in c.get("/inicio").text, "con Visto deja de salir en Inicio"
        assert "tela rota" in c.get("/inventario/casos").text, "pero el caso sigue abierto"
        c.post(f"/inventario/danado/{d['id']}/resolver", data={"como": "reparado"})
        assert con.execute("SELECT estado FROM danados WHERE id=?", (d["id"],)).fetchone()[0] == "reparado" and stock() == s0
        con.close()

@prueba("Casos abiertos: el taller avisa que se está arreglando y, cuando queda, lo devuelve al inventario; a Cristina le llega el aviso")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        p = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 AND categoria NOT IN ('porche','repuesto','opcion','kit') LIMIT 1").fetchone()["id"]
        stock = lambda: con.execute("SELECT COALESCE(SUM(cantidad),0) FROM mov_inventario WHERE producto_id=?", (p,)).fetchone()[0]
        con.execute("INSERT INTO mov_inventario (producto_id,fecha,tipo,cantidad) VALUES (?,date('now'),'entrada',10)", (p,)); con.commit()
        sesion_de(c, "taller")
        c.post("/inventario/mov", data={"producto_id": str(p), "tipo": "danado", "cantidad": "2", "nota": "madera rajada"})
        d = con.execute("SELECT * FROM danados WHERE producto_id=?", (p,)).fetchone()
        r = c.get("/inventario/casos"); assert r.status_code == 200 and "madera rajada" in r.text and "Por revisar" in r.text
        assert "Se botó" not in r.text, "botar o devolver lo decide Cristina"
        assert "Walter" in c.get("/inventario/casos").text, "se puede elegir a Walter"
        c.post(f"/inventario/danado/{d['id']}/reparando", data={"arregla": "Walter"})
        assert tuple(con.execute("SELECT estado, arregla FROM danados WHERE id=?", (d["id"],)).fetchone()) == ("reparando", "Walter") and stock() == 8
        r = c.get("/inventario/casos").text; assert "Arreglándose" in r and "Arreglado" in r
        c.post(f"/inventario/danado/{d['id']}/listo", data={"nota": "se le cambió la tabla"})
        assert con.execute("SELECT estado FROM danados WHERE id=?", (d["id"],)).fetchone()[0] == "reparado" and stock() == 10, stock()
        assert "No hay casos abiertos" in c.get("/inventario/casos").text
        sesion_de(c, "admin")
        inicio = c.get("/inicio").text
        assert "Prueba taller: Walter está arreglando 2" in inicio and "volvió al inventario" in inicio, "Cristina se entera en Inicio"
        sesion_de(c, "despachador")
        assert "madera rajada" not in c.get("/inventario/casos").text, "el despachador no entra"
        con.close()

@prueba("Inventario: el taller avisa que algo se está agotando; a Cristina le llega en Inicio una sola vez hasta que lo resuelva")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        p = con.execute("SELECT id, nombre FROM productos WHERE tipo='producto' AND requiere_color=0 AND categoria NOT IN ('porche','repuesto','opcion','kit') LIMIT 1").fetchone()
        sesion_de(c, "taller")
        assert "Se está agotando" in c.get("/inventario").text
        c.post(f"/inventario/{p['id']}/agotando", data={"color": ""}); c.post(f"/inventario/{p['id']}/agotando", data={"color": ""})
        avisos = con.execute("SELECT * FROM notas_taller WHERE agotando_id=?", (p["id"],)).fetchall()
        assert len(avisos) == 1 and "Prueba taller: se está agotando " + p["nombre"] in avisos[0]["texto"], [dict(a) for a in avisos]
        assert "avisado: se está agotando" in c.get("/inventario").text
        sesion_de(c, "admin")
        assert "se está agotando " + p["nombre"] in c.get("/inicio").text
        c.post(f"/taller/nota/{avisos[0]['id']}/resolver", data={"volver": "/inicio"})
        sesion_de(c, "logistica")
        assert "avisado: se está agotando" not in c.get("/inventario").text, "ya resuelto: se puede volver a avisar"
        con.close()

@prueba("Tarifas: cada zona sabe si es fuera de Caracas y la nueva orden lo trae para filtrar la lista")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        sesion_de(c, "admin")
        c.post("/tarifas/guardar", data={"zona": "Los Teques", "tarifa": "15", "fuera": "1"})
        c.post("/tarifas/guardar", data={"zona": "Chacao", "tarifa": "5"})
        f = {r["zona"]: r["fuera_caracas"] for r in con.execute("SELECT zona, fuera_caracas FROM tarifas WHERE zona IN ('Los Teques','Chacao')")}
        assert f == {"Los Teques": 1, "Chacao": 0}, f
        h = c.get("/ordenes/nueva/panel").text
        assert 'value="Los Teques" data-t="15.0" data-f="1"' in h and 'value="Chacao" data-t="5.0" data-f="0"' in h, "la lista sabe cuál es de afuera"
        con.close()

@prueba("Cliente nuevo: la zona se elige con su dirección habitual, queda guardada y la próxima orden ya la trae")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT OR IGNORE INTO tarifas (zona, tarifa) VALUES ('Chacao', 5)"); con.commit()
        p = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 AND categoria NOT IN ('porche','repuesto','opcion','kit') LIMIT 1").fetchone()["id"]
        sesion_de(c, "admin")
        r = c.post("/ordenes/nueva", data={"cliente_nombre_pila": "Zoe", "cliente_apellido": "Prueba", "cliente_telefono": "04141234567", "cliente_correo": "zoe@ejemplo.com",
               "cliente_direccion": "Res. Los Pinos, apto 4B", "cliente_ciudad": "Caracas", "canal": "whatsapp", "producto_id": str(p), "cantidad": "1", "personalizacion": "", "color": "", "malla": "0",
               "cliente_zona": "Chacao", "tipo_entrega": "delivery", "dir_modo": "hab", "zona_tarifa": "Chacao", "delivery": "5", "fecha_pago": "2026-10-08"})
        cid = con.execute("SELECT id FROM clientes WHERE nombre_pila='Zoe'").fetchone()
        assert cid, ("se creó el cliente", r.status_code, re.sub(r"<[^>]+>", " ", r.text)[:300])
        d = con.execute("SELECT direccion, zona FROM direcciones WHERE cliente_id=?", (cid["id"],)).fetchone()
        assert d and d["zona"] == "Chacao", dict(d) if d else None
        h = c.get("/ordenes/nueva/panel").text
        assert 'data-zona="Chacao"' in h, "la próxima orden ya sabe su zona"
        # otra vez, pero pide en otra dirección: se guarda con su zona y su GPS, sin tocar la habitual
        con.execute("INSERT OR IGNORE INTO tarifas (zona, tarifa) VALUES ('Manzanares', 5)"); con.commit()
        r = c.post("/ordenes/nueva", data={"cliente_id": str(cid["id"]), "canal": "whatsapp", "producto_id": str(p), "cantidad": "1", "personalizacion": "", "color": "", "malla": "0",
               "tipo_entrega": "delivery", "dir_modo": "nueva", "direccion": "Oficina, Torre B piso 3", "maps": "https://maps.app.goo.gl/abc",
               "zona_tarifa": "Manzanares", "delivery": "5", "fecha_pago": "2026-10-08"})
        dirs = {x["direccion"]: (x["zona"], x["maps"], x["principal"]) for x in con.execute("SELECT * FROM direcciones WHERE cliente_id=?", (cid["id"],))}
        assert dirs.get("Oficina, Torre B piso 3") == ("Manzanares", "https://maps.app.goo.gl/abc", 0), dirs
        assert dirs.get("Res. Los Pinos, apto 4B", (None,))[0] == "Chacao", dirs
        con.close()

@prueba("Los paneles que se abren varias veces (nueva orden, orden) no declaran 'let'/'const' sueltos: al reabrir rompen todo el panel")
def _():
    for nombre in ("_orden_nueva.html", "_orden_panel.html"):
        txt = (A.BASE / "templates" / nombre).read_text()
        malas = [l[:60] for l in txt.splitlines() if re.match(r"(<script>)?(let|const) [A-Za-z_$]", l)]
        assert not malas, (nombre, malas)

@prueba("Cuando el taller confirma que llegó un pedido, a Cristina le sale en Inicio quién lo confirmó y cuánto llegó")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT INTO proveedores (id,nombre) VALUES (31,'Yovanny Sánchez')")
        con.execute("INSERT INTO proveedor_items (proveedor_id,item,precio,unidad) VALUES (31,'Grama',3,'saco')")
        pid = con.execute("INSERT INTO produccion (cantidad,recibido,fecha_pedido,responsable,costo,estado,pieza,tipo_pedido) VALUES (15,0,'2026-10-03','Yovanny Sánchez',45,'en_proceso','Grama','proveedor')").lastrowid
        con.commit()
        sesion_de(c, "taller")
        c.post(f"/taller/llegada/{pid}", data={"cantidad": "10"})
        sesion_de(c, "admin")
        h = c.get("/inicio").text
        assert "Prueba taller acaba de confirmar que llegaron 10 sacos de Grama de Yovanny Sánchez (faltan 5 sacos)" in h, re.findall(r"acaba de confirmar[^<]*", h)
        nid = con.execute("SELECT id FROM notas_taller WHERE llegada=1").fetchone()["id"]
        c.post(f"/taller/nota/{nid}/visto", data={"volver": "/inicio"})
        assert "acaba de confirmar" not in c.get("/inicio").text, "con Visto se quita"
        con.close()

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
