#!/usr/bin/env python3
"""Pruebas del ERP Decopet — sobre todo del dinero.

Se ejecutan en segundos y no tocan tus datos: cada prueba arma su propia base
de datos de mentira. Correrlas antes y después de cualquier cambio:

    ./.venv/bin/python pruebas.py
"""
import os, re, html, time, sqlite3, datetime, tempfile, pathlib, traceback
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
    A.cobro_extra(con, 1, "Delivery repuesto", 5, "Zelle", "2026-09-27", 1)
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
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (1,'Zelle',72,72,'USD','2026-10-01','confirmado')")
    con.execute("INSERT INTO packs (id,cliente_id,orden_id,unidades,entregadas_inicio) VALUES (1,1,1,3,0)")
    con.commit()
    assert A.entregar_pack(con, 1, 1, "2026-10-05", "1", "delivery", "Juan", "5", "", pago=None)
    con.commit()
    assert con.execute("SELECT total FROM ordenes WHERE id=1").fetchone()[0] == 77
    assert con.execute("SELECT COUNT(*) FROM pagos WHERE orden_id=1").fetchone()[0] == 1, "no debe registrar un pago que no entró"
    assert con.execute("SELECT estado_pago FROM ordenes WHERE id=1").fetchone()[0] == "abonada"

@prueba("Pack con el delivery pagado antes del ERP: no entra como venta de hoy, no se cobra al entregar y al despachador sí se le paga")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Cliente X','Cliente')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,total) VALUES (1,'#1',1,'entregada','pagada',72)")
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (1,'Zelle',72,72,'USD','2026-06-01','confirmado')")
    con.execute("INSERT INTO packs (id,cliente_id,orden_id,unidades,entregadas_inicio) VALUES (1,1,1,3,2)")
    con.commit()
    A.pack_programar(None, 1, fecha="2026-10-10", tipo_entrega="delivery", despachador="Juan", retiro="1", delivery="5", delivery_pagado="antes", notas="", pago_forma="", diferencia="", diferencia_pagada="0", volver="", direccion="", maps="", agencia="", destino="", retira_pregunta="", retira_nombre="", retira_cedula="", retira_telefono="", retira_correo="", con=con)
    for _ in range(2):   # reprogramar con "Sí" ya elegido tampoco lo vuelve a cobrar
        A.pack_programar(None, 1, fecha="2026-10-11", tipo_entrega="delivery", despachador="Juan", retiro="1", delivery="5", delivery_pagado="1", notas="", pago_forma="", diferencia="", diferencia_pagada="0", volver="", direccion="", maps="", agencia="", destino="", retira_pregunta="", retira_nombre="", retira_cedula="", retira_telefono="", retira_correo="", con=con)
    assert con.execute("SELECT total FROM ordenes WHERE id=1").fetchone()[0] == 72
    assert con.execute("SELECT COUNT(*) FROM pagos").fetchone()[0] == 1
    A.entregar_pack(con, 1, 1, "2026-10-11", "1", "", "", "5", "Pago Móvil VES"); con.commit()   # aunque al entregar digan que cobró, no entra otra vez
    assert con.execute("SELECT total FROM ordenes WHERE id=1").fetchone()[0] == 72
    assert [tuple(r) for r in con.execute("SELECT despachador, monto FROM viajes_despachador")] == [("Juan", 5)]

@prueba("Entrega de pack en otra dirección: el despachador y el aviso de WhatsApp llevan la nueva; al entregar se vuelve a la habitual")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila,telefono) VALUES (1,'Karen X','Karen','0414-1234567')")
    con.execute("INSERT INTO direcciones (cliente_id,direccion,maps,principal) VALUES (1,'Casa de siempre','https://maps.app.goo.gl/vieja',1)")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,total) VALUES (1,'#1',1,'entregada','pagada',72)")
    con.execute("INSERT INTO packs (id,cliente_id,orden_id,unidades,entregadas_inicio) VALUES (1,1,1,3,2)")
    con.commit()
    hoy = datetime.date.today().isoformat()
    A.pack_programar(None, 1, fecha=hoy, tipo_entrega="delivery", despachador="Juan", retiro="1", delivery="5", delivery_pagado="antes", notas="", pago_forma="",
                     diferencia="", diferencia_pagada="0", volver="", direccion="Oficina nueva, piso 3", maps="https://maps.app.goo.gl/nueva", agencia="", destino="", retira_pregunta="", retira_nombre="", retira_cedula="", retira_telefono="", retira_correo="", con=con)
    f = [x for x in A.ruta_despachador(con, "Juan", hoy) if x["kind"] == "pack"][0]
    assert (f["direccion"], f["maps"]) == ("Oficina nueva, piso 3", "https://maps.app.goo.gl/nueva"), f
    assert "Oficina nueva, piso 3" in A.texto_aviso(f["quien"], "Juan", f["que_lleva"], False, f["direccion"], f["maps"])
    assert con.execute("SELECT direccion FROM direcciones WHERE cliente_id=1").fetchone()[0] == "Casa de siempre"   # la ficha no cambia
    A.entregar_pack(con, 1, 1, hoy, "1", "", "", "0", ""); con.commit()
    assert con.execute("SELECT direccion_programada FROM packs WHERE id=1").fetchone()[0] is None

@prueba("El '$ por cobrar' del Inicio lleva a una lista con esas mismas órdenes (también las que se pagan al entregar)")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Cliente X','Cliente')")
    hoy = datetime.date.today().isoformat()
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,total,origen_excel,creado_en) VALUES (1,'#1',1,'pendiente','contra_entrega',60,0,? || ' 10:00')", (hoy,))
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,total,origen_excel,creado_en) VALUES (2,'#2',1,'pendiente','abonada',20,0,? || ' 10:00')", (hoy,))
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (2,'Zelle',15,15,'USD',?,'confirmado')", (hoy,))
    con.commit()
    assert A.por_cobrar_de_hoy(con, hoy) == 65
    filas = A.cargar_ordenes(con, {"vista": "por_cobrar_hoy", "estado": "todas"}, "admin")
    assert sorted(o["numero"] for o in filas) == ["#1", "#2"], [o["numero"] for o in filas]

@prueba("El despachador marca entregado el repuesto prepagado y cobra $65: $5 al delivery y $60 al pack para después, que queda pagado y con sus 3 repuestos")
def _():
    con = base_limpia()
    con.execute("INSERT INTO usuarios (id,nombre,rol) VALUES (2,'Juan','despachador')")
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Darwin X','Darwin')")
    con.execute("INSERT INTO productos (id,sku,nombre,precio,activo) VALUES (5,'PACK3-M','Pack 3 Repuestos Mediano',60,1)")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,total,origen_excel) VALUES (1,'#1',1,'entregada','pagada',120,2)")
    hoy = datetime.date.today().isoformat()
    con.execute("INSERT INTO repuestos_prepagados (id,orden_id,cliente_id,tamano,fecha_programada,tipo_entrega,despachador,delivery,delivery_pagado) VALUES (1,1,1,'Mediano',?,'delivery','Juan',5,0)", (hoy,))
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega) VALUES (2,'#2',1,'pendiente','contra_entrega',60,0,'delivery')")
    con.execute("INSERT INTO orden_lineas (orden_id,nombre,producto_id,cantidad,precio,total) VALUES (2,'Pack 3 Repuestos Mediano',5,1,60,60)")
    con.execute("INSERT INTO packs (id,cliente_id,orden_id,producto_id,tamano,unidades,entregadas_inicio,estado) VALUES (1,1,2,5,'Mediano',3,0,'activo')")
    con.commit()
    class Pedido:   # lo mínimo que la ruta lee del pedido web: es Cristina (admin) marcando por el despachador
        cookies = {}; headers = {}; url = type("u", (), {"path": "/mis-entregas"})()
    viejo = (A.quien_es, A.rol_de, A.uid_de)
    A.quien_es = lambda r: {"rol": "admin", "despachador": None}; A.rol_de = lambda r: "admin"; A.uid_de = lambda r: 1
    try:
        A.mis_entregas_retiro(Pedido(), "prep", 1, "entregado", forma_recibida="Efectivo USD", monto_recibido="65", fue="", motivo="", forma_otro="", con=con)
    finally:
        A.quien_es, A.rol_de, A.uid_de = viejo
    o = con.execute("SELECT estado_pago FROM ordenes WHERE id=2").fetchone()
    assert o["estado_pago"] == "pagada", dict(o)
    assert con.execute("SELECT SUM(monto_usd) FROM pagos WHERE orden_id=2 AND estado='confirmado'").fetchone()[0] == 60
    assert con.execute("SELECT entregado_en IS NOT NULL FROM repuestos_prepagados WHERE id=1").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM entregas_repuesto WHERE pack_id=1").fetchone()[0] == 0   # el pack no se toca
    assert A.deudas_sin_entrega(con, 1) == []

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
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,total,subtotal,delivery,forma_pago_prevista,creado_en) VALUES (1,'#1',1,'pendiente','pagada',75,70,5,'Zelle','2026-10-05 10:00')")
    con.execute("INSERT INTO orden_lineas (orden_id,nombre,cantidad,precio,costo,total) VALUES (1,'Pack 3 Repuestos Mediano',1,60,0,60)")
    con.execute("INSERT INTO orden_lineas (orden_id,nombre,cantidad,precio,costo,total,extra_en) VALUES (1,'Delivery de las próximas entregas del pack (2 × $5)',2,5,0,10,'2026-10-05')")
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (1,'Zelle',75,75,'USD','2026-10-05','confirmado')")
    con.commit()
    filas = A._historial_rows(con, "2026", "", "")
    assert [(f["producto"], f["cantidad"], f["linea"]) for f in filas] == [("Pack 3 Repuestos Mediano", 1, 75)], filas

@prueba("Agregar un delivery que todavía no pagó: sube el total, queda como saldo y no entra ningún pago")
def _():
    con = base_limpia()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Cliente X','Cliente')")
    con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,total,subtotal) VALUES (1,'#1',1,'pendiente','pagada',98,98)")
    con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (1,'Zelle',98,98,'USD','2026-10-05','confirmado')")
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

@prueba("Cómo llegar: con coordenadas abre Maps y Waze navegando; sin link no hay botones (no se busca la dirección escrita)")
def _():
    n = A.navegacion("https://maps.google.com/maps?q=10.4884247%2C-66.8123", "x")
    assert n["maps"].endswith("destination=10.4884247,-66.8123&travelmode=driving") and "ll=10.4884247,-66.8123&navigate=yes" in n["waze"], n
    n = A.navegacion("https://www.google.com/maps/search/Torre%20Xpress/@10.45553223,-66.81810304,17z?hl=en", "x")
    assert "destination=10.45553223,-66.81810304" in n["maps"], n
    n = A.navegacion("", "Francisco Solano, Oficina", "Caracas")
    assert not n["maps"] and not n["waze"], n
    n = A.navegacion("https://maps.app.goo.gl/abc", "Las mercedes")
    assert n["maps"] == "https://maps.app.goo.gl/abc" and not n["waze"], n

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


@prueba("Logística tiene lista cerrada: no llega a plata, exportes ni al manual técnico")
def _():
    permitido, _casa = A.PUERTAS["logistica"]
    for ruta in ("/cashflow", "/finanzas", "/historial", "/historial/exportar", "/configuracion", "/produccion", "/equipo", "/revision", "/tarifas", "/docs", "/openapi.json"):
        assert not ruta.startswith(permitido), f"Logística puede abrir {ruta}"
    assert A.app.openapi_url is None and A.app.docs_url is None, "el manual técnico está publicado"
    o = {"numero": "#1", "cliente": "Ana", "telefono": "", "lineas": [], "tipo_entrega": "delivery", "fecha_prometida": None, "franja": None,
         "agencia": None, "guia": None, "direccion": "", "estado_pago": "contra_entrega", "monto_contra_entrega": 20, "total": 20, "pagado": 0,
         "forma_pago_prevista": "", "notas_entrega": None, "notas_cliente": []}
    assert "$" not in A.resumen_despacho(o, con_plata=False) and "$20" in A.resumen_despacho(o)


@prueba("Analítica: el que ya había comprado (en el ERP o en el Excel de antes) sale en 'Volvieron a comprar' con lo de antes y lo de ahora, no en 'cómo llegaron'")
def _():
    con = base_limpia()
    for i, n in ((901, "Rampa Prueba"), (902, "Porche Prueba")):
        con.execute("INSERT INTO productos (id,sku,nombre,tipo,precio,activo) VALUES (?,?,?,'producto',98,1)", (i, f"T{i}", n))
    p = con.execute("SELECT id, nombre FROM productos WHERE id IN (901,902) ORDER BY id").fetchall()
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (1,'Michel Prueba','Michel')")
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (2,'Ana Excel','Ana')")
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila,origen) VALUES (3,'Rosa Nueva','Rosa','Publicidad (anuncio)')")
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (4,'Luis Nuevo','Luis')")
    con.execute("INSERT INTO clientes (id,nombre,nombre_pila,origen) VALUES (5,'Juana Vieja','Juana','Ya era cliente')")
    for oid, cid, dia, prod in ((1, 1, '2026-10-09', p[0]), (2, 1, '2026-11-08', p[1]), (3, 2, '2026-11-08', p[1]), (4, 3, '2026-11-08', p[1]), (5, 4, '2026-11-08', p[1]), (6, 5, '2026-11-08', p[1])):
        con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,total,origen_excel,creado_en) VALUES (?,?,?,'entregada','pagada',98,0,?)", (oid, f"#{oid}", cid, dia + " 10:00"))
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,?,1,98,98)", (oid, prod["id"], prod["nombre"]))
    con.execute("INSERT INTO registro_ventas (fecha,cliente,producto,precio) VALUES ('2026-09-20','ana excel ','Rampa Nueva',98)")
    con.commit()
    m = A.resumen_mes(con, datetime.date(2026, 11, 1))
    assert dict(m["como_llegaron"]) == {"Publicidad (anuncio)": 1, "Otros": 1}, m["como_llegaron"]
    v = {x["nombre"]: (x["antes"], x["ahora"]) for x in m["volvieron"]}
    assert v["Michel Prueba"] == (p[0]["nombre"], [p[1]["nombre"]]), v
    assert v["Ana Excel"] == ("Rampa Nueva", [p[1]["nombre"]]), v
    assert "Juana Vieja" in v and (m["nuevos"], m["repiten"]) == (2, 3), (m["nuevos"], m["repiten"])


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


@prueba("Logística ve el catálogo con precio de venta, sin costos ni márgenes, y no puede cambiar nada")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "logistica")
        r = c.get("/productos")
        assert r.status_code == 200 and "Catálogo" in r.text, r.status_code
        assert 'name="costo_' not in r.text and "cat-margen dinero" not in r.text and ">Costo<" not in r.text and "Guardar catálogo" not in r.text, "logística ve costos o puede guardar"
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        p = con.execute("SELECT id, nombre, precio FROM productos WHERE tipo='producto' AND precio>0 ORDER BY id LIMIT 1").fetchone()
        c.post("/productos/guardar", data={f"nombre_{p['id']}": "Otro nombre", f"precio_{p['id']}": "1"})
        q = con.execute("SELECT nombre, precio FROM productos WHERE id=?", (p["id"],)).fetchone()
        assert (q["nombre"], q["precio"]) == (p["nombre"], p["precio"]), "logística cambió el catálogo"
        con.close()


@prueba("Quitar un delivery agregado que no se pagó: baja el total y la orden queda pagada; uno pagado no se quita")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (9001,'Marisol','Marisol')")
        con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,subtotal,total,tipo_entrega,creado_en) VALUES (9001,'#99001',9001,'pendiente','pagada',98,98,'pickup','2026-10-05 10:00')")
        con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (9001,'Zelle',98,98,'USD','2026-10-05','confirmado')")
        con.commit()
        A.cobro_extra(con, 9001, "Delivery", 12, "Pago Móvil VES", "2026-10-06", 1, pago=None); con.commit()
        lid = con.execute("SELECT id FROM orden_lineas WHERE orden_id=9001 AND extra_en IS NOT NULL").fetchone()[0]
        c.post(f"/ordenes/9001/linea/{lid}/quitar")
        o = con.execute("SELECT total, delivery, estado_pago FROM ordenes WHERE id=9001").fetchone()
        assert (o["total"], o["delivery"], o["estado_pago"]) == (98, 0, "pagada"), dict(o)
        A.cobro_extra(con, 9001, "Propina", 3, "Zelle", "2026-10-06", 1); con.commit()   # pagado
        lid = con.execute("SELECT id FROM orden_lineas WHERE orden_id=9001 AND extra_en IS NOT NULL").fetchone()[0]
        c.post(f"/ordenes/9001/linea/{lid}/quitar")
        assert con.execute("SELECT total FROM ordenes WHERE id=9001").fetchone()[0] == 101, "lo pagado no se quita"
        con.close()

@prueba("Pasar a pick-up: si Cristina dice quitar el delivery, baja el total (y si ya lo pagó le queda a favor); si dice dejarlo, sigue debiendo")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (9011,'Keymerid','Keymerid')")
        for oid in (9011, 9012, 9013):
            con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,subtotal,delivery,total,tipo_entrega,despachador,creado_en) VALUES (?,?,9011,'pendiente','abonada',100,5,105,'delivery','Cristina','2026-11-07 10:00')", (oid, f"#{oid}"))
            con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (?,'Zelle',100,100,'USD','2026-11-07','confirmado')", (oid,))
        con.execute("UPDATE pagos SET monto_usd=105, monto_real=105 WHERE orden_id=9013"); con.execute("UPDATE ordenes SET estado_pago='pagada' WHERE id=9013")
        con.commit()
        c.post("/ordenes/9011/entrega", data={"tipo_entrega": "pickup", "quitar_delivery": "1"})
        o = con.execute("SELECT total, delivery, estado_pago, despachador FROM ordenes WHERE id=9011").fetchone()
        assert (o["total"], o["delivery"], o["estado_pago"], o["despachador"]) == (100, 0, "pagada", None), dict(o)
        c.post("/ordenes/9012/entrega", data={"tipo_entrega": "pickup", "quitar_delivery": "0"})
        o = con.execute("SELECT total, delivery, estado_pago FROM ordenes WHERE id=9012").fetchone()
        assert (o["total"], o["delivery"], o["estado_pago"]) == (105, 5, "abonada"), dict(o)
        c.post("/ordenes/9013/entrega", data={"tipo_entrega": "pickup", "quitar_delivery": "1"})
        assert con.execute("SELECT total FROM ordenes WHERE id=9013").fetchone()[0] == 100
        assert A.credito_de(con, 9011) == 5, "lo que pagó de delivery le queda a favor"
        con.close()

@prueba("Corregir un pago mal anotado: cambia el monto y la orden queda pagada; borrar un pago la deja por pagar")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (9002,'Marelbis','Marelbis')")
        con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,estado_pago,subtotal,total,creado_en) VALUES (9002,'#99002',9002,'pendiente','abonada',220.76,220.76,'2026-10-07 10:00')")
        pid = con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (9002,'Zelle',216.06,216.06,'USD','2026-10-07 11:39','confirmado')").lastrowid
        con.commit()
        c.post(f"/ordenes/9002/pago/{pid}/corregir", data={"monto_usd": "220.76", "forma": "Zelle", "fecha": "2026-10-07"})
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
        # cambiarle el nombre a una zona la cambia también en los clientes que la tienen
        con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (71,'Carmen Prueba','Carmen')"); con.execute("INSERT INTO direcciones (cliente_id,direccion,zona,principal) VALUES (71,'Av Panteon','Chacao',1)"); con.commit()
        tid = con.execute("SELECT id FROM tarifas WHERE zona='Chacao'").fetchone()[0]
        c.post("/tarifas/guardar", data={"id": str(tid), "zona": "Chacao Centro", "tarifa": "5"})
        assert con.execute("SELECT zona FROM direcciones WHERE cliente_id=71").fetchone()[0] == "Chacao Centro"
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
        o2 = con.execute("SELECT zona, direccion FROM ordenes WHERE cliente_id=? ORDER BY id DESC LIMIT 1", (cid["id"],)).fetchone()
        assert (o2["zona"], o2["direccion"]) == ("Manzanares", "Oficina, Torre B piso 3"), dict(o2)
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

@prueba("Falta la ubicación: desde Inicio se pega el link de Maps y queda en la dirección habitual del cliente")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT INTO clientes (id,nombre,nombre_pila,telefono) VALUES (51,'Paola Prueba','Paola','04141112233')")
        con.execute("INSERT INTO direcciones (cliente_id,etiqueta,direccion,principal) VALUES (51,'Principal','Res Isla de Aves, urb Manzanares',1)")
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,subtotal,total,tipo_entrega) VALUES ('#77',51,'pendiente','pagada',24,29,'delivery')").lastrowid
        con.commit()
        sesion_de(c, "admin")
        h = c.get("/inicio").text
        assert f"/ordenes/{oid}/ubicacion" in h and "Subir" in h
        c.post(f"/ordenes/{oid}/ubicacion", data={"maps": "https://maps.app.goo.gl/xyz"})
        assert con.execute("SELECT maps FROM direcciones WHERE cliente_id=51").fetchone()[0] == "https://maps.app.goo.gl/xyz"
        assert f"/ordenes/{oid}/ubicacion" not in c.get("/inicio").text, "ya no falta"
        con.close()

@prueba("Pack ya creado: prepagar los deliveries de las próximas entregas entra a su orden y esas entregas no se cobran")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT INTO clientes (id,nombre,nombre_pila,telefono) VALUES (53,'Karen Prueba','Karen','04141112235')")
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,subtotal,delivery,total,tipo_entrega) VALUES ('#79',53,'pendiente','pagada',60,7,67,'delivery')").lastrowid
        con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (?,'Binance USDT',67,67,'USD','2026-10-28','confirmado')", (oid,))
        prod = con.execute("SELECT id FROM productos WHERE sku='PACK3-M'").fetchone()
        prod = prod[0] if prod else con.execute("INSERT INTO productos (sku,nombre,precio,activo) VALUES ('PACK3-M','Pack 3 Repuestos Mediano',60,1)").lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Pack 3 Repuestos Mediano',1,60,60)", (oid, prod))
        pid = con.execute("INSERT INTO packs (cliente_id,orden_id,producto_id,unidades,entregadas_inicio,estado) VALUES (53,?,?,3,1,'activo')", (oid, prod)).lastrowid
        con.commit()
        sesion_de(c, "admin")
        assert f"/packs/{pid}/prepagar-deliveries" in c.get("/clientes/53").text
        assert f"/packs/{pid}/prepagar-deliveries" in c.get(f"/ordenes/{oid}/panel").text, "también desde la orden, debajo del pack"
        c.post(f"/packs/{pid}/prepagar-deliveries", data={"cuantos": "5", "tarifa": "7", "forma": "Binance USDT", "fecha": "2026-10-28"})
        k = con.execute("SELECT deliveries_prepagados, tarifa_prepagada FROM packs WHERE id=?", (pid,)).fetchone()
        assert (k[0], k[1]) == (2, 7), "quedan 2 entregas: no se prepagan más de las que faltan"
        o = con.execute("SELECT total, estado_pago FROM ordenes WHERE id=?", (oid,)).fetchone()
        assert o["total"] == 81 and o["estado_pago"] == "pagada", dict(o)
        assert con.execute("SELECT SUM(monto_usd) FROM pagos WHERE orden_id=?", (oid,)).fetchone()[0] == 81
        assert f"/packs/{pid}/prepagar-deliveries" not in c.get("/clientes/53").text, "ya no queda nada por prepagar"
        assert "2 deliveries de las próximas entregas pagados" in c.get(f"/ordenes/{oid}/panel").text
        l = con.execute("SELECT cantidad, precio FROM orden_lineas WHERE orden_id=? AND nombre='Delivery prepagado'", (oid,)).fetchone()
        assert l and (l[0], l[1]) == (2, 7), "en la orden se lee '2× Delivery prepagado'"
        assert "Deliveries pagados · todos" in c.get("/packs").text, "el de hoy va en la orden: con los 2 que vienen ya están todos"
        c.post(f"/packs/{pid}/programar", data={"fecha": "2026-11-15", "tipo_entrega": "delivery", "delivery": "7", "delivery_pagado": "0"})
        assert con.execute("SELECT delivery_pagado FROM packs WHERE id=?", (pid,)).fetchone()[0] == 1, "la próxima entrega ya está pagada"
        con.close()

@prueba("Registro de ventas: con varios pagos el mismo día, el pack sale con la forma con que se pagó (unas fechas traen hora y otras no)")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT INTO clientes (id,nombre,nombre_pila,telefono) VALUES (54,'Karen Registro','Karen','04141112236')")
        oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,subtotal,delivery,total,tipo_entrega,creado_en)
                             VALUES ('#80',54,'pendiente','pagada',74,7,81,'delivery','2026-10-28 10:40:00')""").lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,nombre,cantidad,precio,total) VALUES (?,'Pack 3 Repuestos Mediano',1,60,60)", (oid,))
        con.execute("INSERT INTO orden_lineas (orden_id,nombre,cantidad,precio,costo,total,extra_en) VALUES (?,'Delivery prepagado',2,7,0,14,'2026-10-28')", (oid,))
        for forma, monto, fecha in (("Binance USDT", 60, "2026-10-28 10:48:00"), ("Pago Móvil VES", 14, "2026-10-28"), ("Pago Móvil VES", 7, "2026-10-28 11:56:00")):
            con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (?,?,?,?,'USD',?,'confirmado')", (oid, forma, monto, monto, fecha))
        con.commit()
        filas = {f["producto"]: f for f in A._entradas_de(con, oid)}
        assert filas["Pack 3 Repuestos Mediano"]["forma"] == "Binance USDT", filas["Pack 3 Repuestos Mediano"]["forma"]
        assert filas["Delivery"]["forma"] == "Pago Móvil VES" and filas["Delivery"]["linea"] == 21, filas["Delivery"]
        con.close()

@prueba("Subir ubicación: sirve el link sin https:// o con texto alrededor; la dirección escrita no se guarda y se avisa")
def _():
    assert A.link_de_maps("maps.app.goo.gl/abc123") == "https://maps.app.goo.gl/abc123"
    assert A.link_de_maps("Mi ubicación: https://maps.app.goo.gl/abc123 gracias") == "https://maps.app.goo.gl/abc123"
    assert A.link_de_maps("10.4806, -66.9036") == "https://www.google.com/maps?q=10.4806,-66.9036"
    assert A.link_de_maps("Res Isla de Aves, urb Manzanares, torre B") == ""
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT INTO clientes (id,nombre,nombre_pila,telefono) VALUES (52,'Fer Prueba','Fer','04141112234')")
        con.execute("INSERT INTO direcciones (cliente_id,etiqueta,direccion,principal) VALUES (52,'Principal','La Trinidad, calle 2',1)")
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,subtotal,total,tipo_entrega) VALUES ('#78',52,'pendiente','pagada',24,29,'delivery')").lastrowid
        con.commit()
        sesion_de(c, "admin")
        r = c.post(f"/ordenes/{oid}/ubicacion", data={"maps": "La Trinidad, calle 2, casa 5"})
        assert "No se guardó" in r.text and f"/ordenes/{oid}/ubicacion" in r.text, "la dirección escrita no cuenta como ubicación"
        c.post(f"/ordenes/{oid}/ubicacion", data={"maps": "maps.app.goo.gl/sinhttps"})
        assert con.execute("SELECT maps FROM direcciones WHERE cliente_id=52").fetchone()[0] == "https://maps.app.goo.gl/sinhttps"
        con.close()

@prueba("Efectivo en la calle: lo que cobra un despachador queda a su nombre hasta que te lo entrega")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT OR REPLACE INTO config (clave, valor) VALUES ('ventas_auto','0')")
        con.execute("INSERT OR IGNORE INTO cuentas (codigo,nombre,moneda,tipo,activa,orden) VALUES ('001','Efectivo USD','USD','operativa',1,1)")
        did = con.execute("INSERT INTO despachadores (nombre, activo) VALUES ('Ingrid Prueba', 1)").lastrowid
        con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (61,'Rosa Prueba','Rosa')")
        o1 = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,subtotal,delivery,total,tipo_entrega,despachador) VALUES ('#81',61,'pendiente','contra_entrega',40,5,45,'delivery','Ingrid Prueba')").lastrowid
        con.commit(); A.cargar_formas_pago()
        sesion_de(c, "admin")
        c.post(f"/ordenes/{o1}/estado", data={"estado": "entregada", "monto_recibido": "45"})
        p = con.execute("SELECT * FROM pagos WHERE orden_id=?", (o1,)).fetchone()
        assert p and p["lo_tiene"] == "Ingrid Prueba" and p["monto_usd"] == 45 and not p["rendido_en"], dict(p) if p else None
        h = c.get("/inicio").text
        assert "Ingrid Prueba tiene $45.00 tuyos en efectivo" in h, re.findall(r"Ingrid[^<]{0,60}", h)
        assert not [e for e in A.efectivo_por_registrar(con) if e["numero"] == "#81"], "mientras lo tiene ella, no está por registrar"
        assert "Ingrid Prueba" in c.get("/cashflow").text, "al pasar el mouse por la caja de efectivo se ve quién lo tiene"
        h = c.get(f"/despachadores/{did}").text
        sesion_de(c, "despachador")
        con.execute("UPDATE usuarios SET despachador='Ingrid Prueba' WHERE usuario='prueba-despachador'"); con.commit()
        c.cookies.set("ver_desp", "Ingrid Prueba")
        assert "En caja" in c.get("/mis-entregas").text, "el despachador ve el efectivo tuyo que tiene"
        sesion_de(c, "admin"); h = c.get(f"/despachadores/{did}").text
        assert "efectivo que cobró" in h and ">Caja<" in h, (re.findall(r"<title>[^<]*", h), len(h))
        # te entrega el efectivo aparte: entra a la caja de efectivo (al despachador se le paga por otro lado)
        c.post(f"/despachadores/{did}/efectivo")
        assert con.execute("SELECT rendido_en FROM pagos WHERE orden_id=?", (o1,)).fetchone()[0], "quedó entregado"
        caja = con.execute("SELECT id FROM cuentas WHERE nombre='Efectivo USD'").fetchone()[0]
        entra = con.execute("SELECT COALESCE(SUM(monto_usd),0) FROM movimientos WHERE cuenta_destino_id=?", (caja,)).fetchone()[0]
        assert entra == 45, entra
        assert not [e for e in A.efectivo_por_registrar(con) if e["numero"] == "#81"], "no se pide registrar a mano lo que ya entró solo"
        assert "tuyos en efectivo" not in c.get("/inicio").text
        con.close()

@prueba("Un pedido a otra dirección no muestra la zona de la dirección habitual")
def _():
    hab = {"direccion": "Res. Los Pinos, Prados del Este", "zona": "Prados del Este", "ciudad": "Caracas", "maps": "https://maps.app.goo.gl/x"}
    o = A.completar_direccion({"tipo_entrega": "delivery", "direccion": "Las mercedes", "zona": None, "ciudad": None, "maps": None}, hab)
    assert o["zona"] is None and o["maps"] is None and o["ciudad"] == "Caracas", o
    o = A.completar_direccion({"tipo_entrega": "delivery", "direccion": None, "zona": None, "ciudad": None, "maps": None}, hab)
    assert o["zona"] == "Prados del Este" and o["maps"], o

@prueba("Pack con malla: la malla va con el primer repuesto (esa entrega dice '+ malla'); las siguientes no")
def _():
    con = base_limpia()
    con.execute("INSERT INTO productos (id,sku,nombre,categoria,tipo,activo) VALUES (95,'PACK3-M','Pack 3 Repuestos Mediano','repuesto','producto',1)")
    con.execute("INSERT INTO ordenes (id,numero,estado,estado_pago,subtotal,total) VALUES (5,'#5','pendiente','pagada',80,80)")
    con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total,malla) VALUES (5,95,'Pack 3 Repuestos Mediano',1,60,0,80,1)")
    k = {"retiro_programado": 1, "unidades": 3, "saldo": 3, "tamano": "Mediano", "orden_id": 5, "producto_id": 95}
    assert A.repuesto_de_pack(k, con) == ("Repuesto Mediano + malla", "1/3"), A.repuesto_de_pack(k, con)
    k["saldo"] = 2
    assert A.repuesto_de_pack(k, con) == ("Repuesto Mediano", "2/3"), A.repuesto_de_pack(k, con)

@prueba("Varios pedidos al mismo proveedor que tocan pagar hoy salen en un solo aviso con el total")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB)
        for costo in (30, 45, 30):
            con.execute("INSERT INTO produccion (cantidad,recibido,fecha_pedido,fecha_esperada,responsable,costo,estado,pieza,tipo_pedido) VALUES (10,10,'2026-10-03',date('now','localtime'),'Yovanny Prueba',?,'recibido','Grama','proveedor')", (costo,))
        con.commit()
        sesion_de(c, "admin"); h = c.get("/inicio").text
        assert h.count("Hoy toca pagar Grama (3 pedidos) a Yovanny Prueba") == 1 and "105.00" in h, re.findall(r"Hoy toca pagar[^<]*", h)
        con.close()

@prueba("Un despachador que va acumulando (no cobra los viernes) no sale en el aviso del viernes")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB)
        did = con.execute("INSERT INTO despachadores (nombre, activo) VALUES ('Ingrid Acumula', 1)").lastrowid; con.commit()
        sesion_de(c, "admin")
        assert "Se le paga los viernes" in c.get(f"/despachadores/{did}").text
        c.post(f"/despachadores/{did}/viernes", data={"cobra": "0"})
        assert con.execute("SELECT cobra_viernes FROM despachadores WHERE id=?", (did,)).fetchone()[0] == 0
        c.post(f"/despachadores/{did}/viernes", data={"cobra": "1"})
        assert con.execute("SELECT cobra_viernes FROM despachadores WHERE id=?", (did,)).fetchone()[0] == 1
        con.close()

@prueba("La cédula escrita solo con el número se guarda con su V adelante; el RIF queda con su J")
def _():
    assert A.normalizar_cedula("12345678") == "V-12345678"
    assert A.normalizar_cedula(" 12.345.678 ") == "V-12345678"
    assert A.normalizar_cedula("v12345678") == "V-12345678"
    assert A.normalizar_cedula("V-12345678") == "V-12345678"
    assert A.normalizar_cedula("j-40123456-7") == "J-40123456-7"
    assert A.normalizar_cedula("E-84414421") == "E-84414421"
    assert A.normalizar_cedula("") == ""
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        c.post("/clientes/nuevo", data={"nombre_pila": "Cedula Sola", "telefono": "04140000008", "cedula": "12345678"})
        con = sqlite3.connect(A.DB)
        assert con.execute("SELECT cedula FROM clientes WHERE nombre='Cedula Sola'").fetchone()[0] == "V-12345678"
        con.close()

@prueba("Estado + ciudad: la ciudad de la lista trae su estado; solo el estado deja la ciudad por preguntar; no se adivina la capital")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        con = sqlite3.connect(A.DB)
        assert A.lugar(con, "maracaibo") == ("Maracaibo", "Zulia")
        assert A.lugar(con, "", "Miranda") == ("Pendiente", "Miranda")
        assert A.lugar(con, "Miranda") == ("Pendiente", "Miranda")
        assert A.lugar(con, "Margarita") == ("Pendiente", "Nueva Esparta")
        assert A.lugar(con, "", "") == (None, None)
        c.post("/clientes/nuevo", data={"nombre_pila": "Ocumare Uno", "telefono": "04140000007", "estado": "Miranda", "ciudad": "Ocumare del Tuy"})
        assert con.execute("SELECT ciudad, estado FROM clientes WHERE nombre='Ocumare Uno'").fetchone() == ("Ocumare del Tuy", "Miranda")
        assert A.lugar(con, "OCUMARE DEL TUY", "") == ("Ocumare del Tuy", "Miranda")   # escrita distinto, queda igual que la primera
        assert ("Ocumare del Tuy", "Miranda") in A.ciudades_conocidas(con)
        h = c.get("/clientes/nuevo/panel").text
        assert 'name="estado"' in h and 'data-e="Zulia"' in h
        # Tina solo escribe en Ciudad lo que dijo el cliente, sin tocar el Estado: se guarda igual y lo que falta queda pendiente
        for n, ciu in (("Isla Uno", "Margarita"), ("Sin Estado", "Pueblo Raro")):
            c.post("/clientes/nuevo", data={"nombre_pila": n, "telefono": "04140000006", "ciudad": ciu})
        assert con.execute("SELECT ciudad, estado FROM clientes WHERE nombre='Isla Uno'").fetchone() == ("Pendiente", "Nueva Esparta")
        assert con.execute("SELECT ciudad, estado FROM clientes WHERE nombre='Sin Estado'").fetchone() == ("Pueblo Raro", None)
        assert "Sin Estado" in c.get("/clientes?falta=estado").text and "Isla Uno" in c.get("/clientes?falta=ciudad").text
        con.execute("INSERT INTO tarifas (zona, tarifa, fuera_caracas) VALUES ('Los Palos Grandes', 5, 0), ('El Junquito', 10, 1)"); con.commit()
        assert A.lugar(con, "los palos grandes") == ("Caracas", "Distrito Capital")   # una zona de Caracas de Tarifas es Caracas
        assert A.lugar(con, "El Junquito")[0] != "Caracas"                              # una de fuera de Caracas no se adivina
        con.close()

@prueba("Un cliente nuevo con solo el GPS (sin dirección escrita) guarda el GPS y la orden lo dice")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        c.post("/clientes/nuevo", data={"nombre_pila": "Solo Gps", "telefono": "04140000009", "ciudad": "Caracas",
                                        "direccion": "", "maps": "https://maps.app.goo.gl/prueba"})
        con = sqlite3.connect(A.DB)
        cid, = con.execute("SELECT id FROM clientes WHERE nombre='Solo Gps'").fetchone()
        assert con.execute("SELECT direccion, maps, principal FROM direcciones WHERE cliente_id=?", (cid,)).fetchone() == ("", "https://maps.app.goo.gl/prueba", 1)
        assert "Falta la dirección escrita" in c.get(f"/clientes/{cid}").text
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

@prueba("Pack para después sin pagar: el cobro sale junto con la entrega de su repuesto prepagado (Operaciones y lista del despachador)")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); hoy = datetime.date.today().isoformat(); desp = A.DESPACHADORES[0]
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Darwin Prueba','Darwin')").lastrowid
        pid = con.execute("INSERT INTO productos (sku,nombre,categoria,tipo,activo,precio) VALUES ('PACK3-X','Pack 3 Repuestos Prueba','repuesto','producto',1,60)").lastrowid
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,subtotal,total,origen_excel) VALUES ('#PK1',?,'pendiente','contra_entrega',60,60,0)", (cid,)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,'Pack 3 Repuestos Prueba',1,60,0,60)", (oid, pid))
        con.execute("INSERT INTO packs (cliente_id,orden_id,producto_id,unidades,entregadas_inicio) VALUES (?,?,?,3,0)", (cid, oid, pid))
        con.execute("INSERT INTO repuestos_prepagados (cliente_id,tamano,fecha_programada,tipo_entrega,despachador,delivery,delivery_pagado) VALUES (?,'Mediano',?,'delivery',?,5,0)", (cid, hoy, desp))
        con.commit()
        sesion_de(c, "admin"); h = c.get("/operaciones?cola=hoy").text
        assert f'action="/ordenes/{oid}/cobrar' in h and 'value="60.00"' in h and "Pack 3 Repuestos Prueba" in h, "el cobro del pack no sale en Operaciones"
        con.execute("UPDATE ordenes SET estado_pago='sin_pago' WHERE id=?", (oid,)); con.commit()
        h = c.get("/operaciones?cola=hoy&q=Darwin").text
        assert h.count(f'action="/ordenes/{oid}/cobrar') == 1, "sin pagar: el pack sale una sola vez, junto con la entrega"
        c2 = sqlite3.connect(A.DB); c2.row_factory = sqlite3.Row
        filas = [f for f in A.ruta_despachador(c2, desp, hoy) if f["cid"] == cid]
        txt = A.texto_ruta(filas, datetime.date.today())
        assert "COBRAR $5.00" in txt and "COBRAR $60.00 · Pack 3 Repuestos Prueba" in txt and "Total a cobrar: $65.00" in txt, txt
        c2.close(); con.close()


@prueba("Un pedido que se paga al entregar sale en el Inicio para cobrar, y en 'Con saldo pendiente' tiene su botón de cobro")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB)
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega) VALUES ('#CE1',(SELECT MIN(id) FROM clientes),'en_ruta','contra_entrega',100,0,'delivery')").lastrowid
        con.commit()
        sesion_de(c, "admin")
        assert "#CE1" in c.get("/operaciones?cola=con_saldo").text and f"/ordenes/{oid}/cobrar" in c.get("/operaciones?cola=con_saldo").text
        assert "con saldo pendiente" in c.get("/inicio").text


@prueba("Desde la orden abierta se puede registrar un cobro (Cristina sí; logística no ve plata)")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB)
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel) VALUES ('#CB1',(SELECT MIN(id) FROM clientes),'pendiente','abonada',65,0)").lastrowid
        con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (?,'Efectivo USD',5,5,'USD',date('now'),'confirmado')", (oid,)); con.commit()
        sesion_de(c, "logistica"); assert "Registrar cobro" not in c.get(f"/ordenes/{oid}/panel").text
        sesion_de(c, "admin"); h = c.get(f"/ordenes/{oid}/panel").text
        assert "Registrar cobro" in h and 'value="60.00"' in h
        c.post(f"/ordenes/{oid}/cobrar?estado=todas", data={"forma": "Efectivo USD", "monto_usd": "60"}, follow_redirects=False)
        assert sqlite3.connect(A.DB).execute("SELECT estado_pago FROM ordenes WHERE id=?", (oid,)).fetchone()[0] == "pagada"


@prueba("Forma de pago con monto 0: la orden queda sin pagar, recuerda cómo va a pagar y el cobro ya trae esa forma")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        p = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 AND categoria NOT IN ('porche','repuesto','opcion','kit') LIMIT 1").fetchone()["id"]
        cid = con.execute("SELECT MIN(id) FROM clientes").fetchone()[0]
        fo = con.execute("SELECT nombre FROM cuentas WHERE activa=1 AND COALESCE(cobra,1)=1 AND nombre NOT LIKE 'Efectivo%' ORDER BY orden, codigo LIMIT 1 OFFSET 1").fetchone()[0]
        sesion_de(c, "admin")
        c.post("/ordenes/nueva", data={"cliente_id": str(cid), "canal": "whatsapp", "producto_id": str(p), "cantidad": "1", "personalizacion": "", "color": "", "malla": "0",
               "tipo_entrega": "pickup", "pago_forma": fo, "pago_monto": "0", "pago_ref": "", "fecha_pago": "2026-10-08"})
        o = con.execute("SELECT * FROM ordenes WHERE cliente_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        assert (o["forma_pago_prevista"], o["estado_pago"], o["fecha_pago"]) == (fo, "sin_pago", None), dict(o)
        assert not con.execute("SELECT 1 FROM pagos WHERE orden_id=?", (o["id"],)).fetchone(), "no entró plata"
        assert o["creado_en"][:10] == datetime.date.today().isoformat(), "sin pago, la orden no se fecha con la fecha de pago"
        h = c.get(f"/ordenes/{o['id']}/panel").text
        assert f'<option value="{fo}" selected>' in h, "el cobro ya trae la forma con que va a pagar"
        con.close()


@prueba("Entrega de pack en Operaciones: se le cambia el despachador desde la lista, y marcarla en ruta no se lo borra")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat()
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Patricia Prueba','Patricia')").lastrowid
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel) VALUES ('#PK1',?,'entregada','pagada',72,0)", (cid,)).lastrowid
        pid = con.execute("INSERT INTO packs (cliente_id,orden_id,unidades,entregadas_inicio) VALUES (?,?,3,1)", (cid, oid)).lastrowid
        con.commit()
        A.pack_programar(None, pid, fecha=hoy, tipo_entrega="delivery", despachador="Juan", retiro="1", delivery="5", delivery_pagado="antes", notas="", pago_forma="",
                         diferencia="", diferencia_pagada="0", volver="", direccion="", maps="", agencia="", destino="", retira_pregunta="", retira_nombre="", retira_cedula="", retira_telefono="", retira_correo="", con=con)
        sesion_de(c, "logistica")
        assert f'action="/packs/{pid}/campo"' in c.get("/operaciones").text, "la entrega del pack tiene su lista de despachadores"
        c.post(f"/packs/{pid}/campo", data={"despachador": "Ingrid", "volver": "/operaciones"}, follow_redirects=False)
        assert con.execute("SELECT despachador_programado FROM packs WHERE id=?", (pid,)).fetchone()[0] == "Ingrid"
        c.post(f"/packs/{pid}/campo", data={"en_ruta": "1", "volver": "/operaciones"}, follow_redirects=False)
        assert con.execute("SELECT despachador_programado, en_ruta FROM packs WHERE id=?", (pid,)).fetchone()[:] == ("Ingrid", 1)
        sesion_de(c, "taller")
        c.post(f"/packs/{pid}/campo", data={"despachador": "Juan"}, follow_redirects=False)
        assert con.execute("SELECT despachador_programado FROM packs WHERE id=?", (pid,)).fetchone()[0] == "Ingrid", "el taller no coordina"
        con.close()


@prueba("Entrega de pack con el delivery sin pagar: se cobra desde Operaciones antes de llevarla, entra a su orden y al entregar no se cobra otra vez")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat()
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Patricia Prueba','Patricia')").lastrowid
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel) VALUES ('#PK2',?,'entregada','pagada',72,0)", (cid,)).lastrowid
        con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (?,'Zelle',72,72,'USD','2026-10-01','confirmado')", (oid,))
        pid = con.execute("INSERT INTO packs (cliente_id,orden_id,unidades,entregadas_inicio) VALUES (?,?,3,1)", (cid, oid)).lastrowid
        con.commit()
        A.pack_programar(None, pid, fecha=hoy, tipo_entrega="delivery", despachador="Juan", retiro="1", delivery="5", delivery_pagado="0", notas="", pago_forma="",
                         diferencia="", diferencia_pagada="0", volver="", direccion="", maps="", agencia="", destino="", retira_pregunta="", retira_nombre="", retira_cedula="", retira_telefono="", retira_correo="", con=con)
        sesion_de(c, "logistica")
        assert f'action="/packs/{pid}/cobrar-delivery' in c.get("/operaciones").text, "logística ve el botón para cobrar el delivery"
        c.post(f"/packs/{pid}/cobrar-delivery", data={"forma": "Zelle", "monto_usd": "5", "referencia": "123", "volver": "/operaciones"}, follow_redirects=False)
        assert con.execute("SELECT delivery_pagado FROM packs WHERE id=?", (pid,)).fetchone()[0] == 1
        assert con.execute("SELECT total FROM ordenes WHERE id=?", (oid,)).fetchone()[0] == 77
        assert con.execute("SELECT COUNT(*) FROM pagos WHERE orden_id=? AND fecha=? AND monto_usd=5", (oid, hoy)).fetchone()[0] == 1, "entra como venta de hoy"
        c.post(f"/packs/{pid}/cobrar-delivery", data={"forma": "Zelle", "monto_usd": "5"}, follow_redirects=False)   # doble clic: no cobra dos veces
        assert con.execute("SELECT total FROM ordenes WHERE id=?", (oid,)).fetchone()[0] == 77
        assert f'action="/packs/{pid}/cobrar-delivery' not in c.get("/operaciones").text, "ya pagado: no sale el botón"
        A.entregar_pack(con, pid, 1, hoy, "1", "", "", "5", "Efectivo USD"); con.commit()
        assert con.execute("SELECT total FROM ordenes WHERE id=?", (oid,)).fetchone()[0] == 77, "al entregar no se cobra otra vez"
        assert [tuple(r) for r in con.execute("SELECT despachador, monto FROM viajes_despachador WHERE pack_id=?", (pid,))] == [("Juan", 5)], "al despachador sí se le paga"
        con.close()


@prueba("Nota para el taller: se guarda al editar la entrega y sale en Hoy en el taller, en el pick-up y en lo que lleva el despachador")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat()
        p = con.execute("SELECT id, nombre, precio, costo FROM productos WHERE tipo='producto' AND categoria NOT IN ('opcion','kit') LIMIT 1").fetchone()
        ids = []
        for nombre, te, desp in (("Notita", "pickup", None), ("Llevita", "delivery", "Juan")):
            cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES (?,?)", (nombre + " Prueba", nombre)).lastrowid
            oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,despachador,fecha_prometida) VALUES (?,?,'pendiente','pagada',24,0,?,?,?)",
                              ("#N" + nombre, cid, te, desp, hoy)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,?,1,?,?,?)", (oid, p["id"], p["nombre"], p["precio"], p["costo"], p["precio"]))
            ids.append(oid)
        con.commit()
        sesion_de(c, "admin")
        c.post(f"/ordenes/{ids[0]}/entrega", data={"tipo_entrega": "pickup", "fecha_prometida": hoy, "nota_taller": "ponerle lazo rojo"})
        c.post(f"/ordenes/{ids[1]}/entrega", data={"tipo_entrega": "delivery", "despachador": "Juan", "fecha_prometida": hoy, "nota_taller": "revisar tornillos"})
        assert con.execute("SELECT nota_taller FROM ordenes WHERE id=?", (ids[0],)).fetchone()[0] == "ponerle lazo rojo"
        A.montar_uno(None, con, "orden", ids[1]); con.commit()   # lo del despachador lo ve el taller cuando él le da Montar
        sesion_de(c, "taller"); h = c.get("/taller").text
        assert "ponerle lazo rojo" in h and "revisar tornillos" in h, "el taller no ve la nota"
        con.close()


@prueba("Montar: el despachador, desde Mis entregas, le dice al taller que le monte lo suyo; al darle Listo a Cristina le llega el aviso y en Operaciones dice ✓ montado")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat(); yo = A.DESPACHADORES[0]
        pid = con.execute("INSERT INTO productos (sku,nombre,tipo,precio) VALUES ('CARRO1','Porche Lote','producto',90)").lastrowid
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Lote Prueba','Lote')").lastrowid
        def orden(num, desp=yo, fecha=hoy):
            oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,despachador,fecha_prometida)
                                 VALUES (?,?,'pendiente','pagada',90,0,'delivery',?,?)""", (num, cid, desp, fecha)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Porche Lote',1,90,90)", (oid, pid)); con.commit()
            return oid
        o1, o2 = orden("#L1"), orden("#L2")
        otro = orden("#L9", desp="Otro Prueba")
        manana = orden("#L3", fecha=(datetime.date.today() + datetime.timedelta(days=1)).isoformat())
        sesion_de(c, "taller"); h = c.get("/taller").text
        assert "Porche Lote" not in h and "Montar a" not in h, "el taller no lo ve solo"
        sesion_de(c, "admin"); h = c.get("/operaciones").text
        assert "Montar" not in h.replace("montar", "") and "✓ montado" not in h, "Cristina no tiene botón: en cada pedido no sale nada hasta que se monta"
        sesion_de(c, "despachador"); h = c.get("/mis-entregas").text
        assert f'name="item" value="orden:{o1}"><button class="btn chico">Montar' in h and f'name="item" value="orden:{o2}"><button class="btn chico">Montar' in h, "cada pedido con su botón Montar"
        c.post("/mis-entregas/montar", data={"item": f"orden:{o1}"}); c.post("/mis-entregas/montar", data={"item": f"orden:{otro}"})   # uno ajeno no cuela
        assert [r[0] for r in con.execute("SELECT orden_id FROM cargas_taller")] == [o1], "solo lo que tocó, y solo lo suyo"
        h = c.get("/mis-entregas").text
        assert f'name="item" value="orden:{o2}"><button class="btn chico">Montar' in h and 'name="quitar" value="1"><button class="btn sec chico" title="Ya se lo pediste al taller: tocar para quitárselo">Deshacer' in h, "lo que no tocó sigue para la próxima ronda"
        c.post("/mis-entregas/montar", data={"item": f"orden:{o1}", "quitar": "1"})   # se equivocó: lo quita
        assert con.execute("SELECT COUNT(*) FROM cargas_taller").fetchone()[0] == 0
        for o in (o1, o2, o1): c.post("/mis-entregas/montar", data={"item": f"orden:{o}"})
        mios = {r[0] for r in con.execute("SELECT orden_id FROM cargas_taller WHERE orden_id IS NOT NULL")}
        assert {o1, o2} <= mios and otro not in mios and manana not in mios, "solo lo suyo y de hoy"
        assert con.execute("SELECT COUNT(*) FROM cargas_taller WHERE orden_id=?", (o1,)).fetchone()[0] == 1, "dos toques: una sola vez"
        h = c.get("/mis-entregas").text
        assert '<button class="btn chico">Montar' not in h.split('class="dia-t hoy"')[1].split("mañana")[0] and h.count(">Deshacer</button>") >= 2
        sesion_de(c, "taller"); h = c.get("/taller").text
        assert h.count(f"Montar a {yo}") == 1 and "2× Porche Lote" in h, "todo lo suyo junto, con un solo Listo"
        assert "Otro Prueba" not in h
        sesion_de(c, "admin"); h = c.get("/operaciones").text
        assert h.count(">por montar</span>") >= 2 and "✓ montado" not in h, "lo pidió y el taller no lo ha montado: en Operaciones dice 'por montar'"
        sesion_de(c, "taller")
        con.execute("UPDATE ordenes SET despachador='Otro Prueba' WHERE id=?", (o2,)); con.commit()   # le cambiaron el despachador: sale con el nuevo
        h = c.get("/taller").text; assert "Montar a Otro Prueba" in h
        con.execute("UPDATE ordenes SET despachador=? WHERE id=?", (yo, o2)); con.commit()
        con.execute("UPDATE ordenes SET estado='en_ruta' WHERE id=?", (o2,)); con.commit()   # salió en ruta: deja de verse en el taller
        h = c.get("/taller").text; assert "1× Porche Lote" in h and "2× Porche Lote" not in h
        con.execute("UPDATE ordenes SET estado='pendiente' WHERE id=?", (o2,)); con.commit()
        con.execute("UPDATE usuarios SET nombre='Isaías Prueba' WHERE rol='taller'"); con.commit()
        c.post("/taller/montado", data={"despachador": yo})
        assert "Porche Lote" not in c.get("/taller").text, "ya montado, sale de su pantalla"
        n = con.execute("SELECT texto, visto FROM notas_taller WHERE carga_id IS NOT NULL").fetchone()
        assert n and n["texto"].startswith(f"Isaías montó lo de {yo}: ") and "2× Porche Lote" in n["texto"] and n["visto"] == 0, dict(n)
        sesion_de(c, "despachador"); assert "✓ Montado" in c.get("/mis-entregas").text
        sesion_de(c, "admin")
        assert f"Isaías montó lo de {yo}" in c.get("/inicio").text, "a Cristina le llega el aviso"
        assert c.get("/operaciones").text.count("✓ montado") >= 2, "cada pedido dice ✓ montado"
        assert ">por montar</span>" not in c.get("/operaciones").text, "ya montado: no dice 'por montar'"
        n_antes = c.get("/operaciones").text.count("✓ montado")
        con.execute("UPDATE ordenes SET estado='en_ruta' WHERE id=?", (o1,)); con.commit()
        assert c.get("/operaciones").text.count("✓ montado") == n_antes - 1, "salió en ruta: ya no dice ✓ montado"
        con.execute("UPDATE ordenes SET estado='pendiente' WHERE id=?", (o1,)); con.commit()
        k1 = con.execute("SELECT id FROM cargas_taller WHERE orden_id=?", (o1,)).fetchone()[0]
        assert f'action="/taller/carga/{k1}/deshacer"' in c.get("/operaciones").text
        sesion_de(c, "logistica"); c.post(f"/taller/carga/{k1}/deshacer")
        assert con.execute("SELECT COUNT(*) FROM cargas_taller WHERE id=?", (k1,)).fetchone()[0] == 1, "solo Cristina lo quita"
        sesion_de(c, "admin"); c.post(f"/taller/carga/{k1}/deshacer")
        assert con.execute("SELECT COUNT(*) FROM cargas_taller WHERE orden_id=?", (o1,)).fetchone()[0] == 0, "se quitó el ✓ montado"
        sesion_de(c, "despachador"); assert f'name="item" value="orden:{o1}"><button class="btn chico">Montar' in c.get("/mis-entregas").text, "el despachador lo puede volver a pedir"
        sesion_de(c, "admin")
        orden("#L4")   # entra otro: el despachador puede volver a pedir, solo ese
        o4 = con.execute("SELECT id FROM ordenes WHERE numero='#L4'").fetchone()[0]
        sesion_de(c, "despachador"); c.post("/mis-entregas/montar", data={"item": f"orden:{o4}"})
        sesion_de(c, "taller"); h = c.get("/taller").text
        assert 'action="/taller/montar-quitar"' not in h, "el taller no ve Quitar"
        c.post("/taller/montar-quitar", data={"despachador": yo})
        assert con.execute("SELECT COUNT(*) FROM cargas_taller WHERE orden_id=?", (o4,)).fetchone()[0] == 1, "el taller no puede quitarlo"
        sesion_de(c, "admin"); c.cookies.set("ver_como", "taller")
        assert 'action="/taller/montar-quitar"' in c.get("/taller").text, "Cristina mirando como el taller sí ve Quitar"
        c.post("/taller/montar-quitar", data={"despachador": yo})
        assert con.execute("SELECT COUNT(*) FROM cargas_taller WHERE orden_id=?", (o4,)).fetchone()[0] == 0, "Cristina lo quita"
        c.cookies.delete("ver_como")
        sesion_de(c, "despachador"); assert f'name="item" value="orden:{o4}"><button class="btn chico">Montar' in c.get("/mis-entregas").text
        sesion_de(c, "logistica"); c.post("/mis-entregas/montar", data={"item": f"orden:{o4}"})
        sesion_de(c, "taller"); c.post("/mis-entregas/montar", data={"item": f"orden:{o4}"})
        assert con.execute("SELECT COUNT(*) FROM cargas_taller WHERE listo_en IS NULL").fetchone()[0] == 0, "ni logística ni el taller lo piden"
        con.close()


@prueba("Montar un repuesto de pack: al entregarlo se borra, así el próximo retiro del mismo pack se vuelve a pedir montar")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat(); yo = A.DESPACHADORES[0]
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Pack Ronda','Pack')").lastrowid
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega) VALUES ('#PR1',?,'entregada','pagada',72,0,'delivery')", (cid,)).lastrowid
        pid = con.execute("INSERT INTO packs (cliente_id,orden_id,unidades,entregadas_inicio,tamano,fecha_programada,tipo_programado,despachador_programado,retiro_programado) VALUES (?,?,3,1,'Grande',?,'delivery',?,1)", (cid, oid, hoy, yo)).lastrowid
        con.commit()
        assert A.montar_de(con, yo).get(f"pack:{pid}") == "falta"
        A.montar_uno(None, con, "pack", pid); con.commit()
        assert A.montar_de(con, yo).get(f"pack:{pid}") == "pendiente"
        A.entregar_pack(con, pid, None, hoy, "1", "delivery", yo); con.commit()
        con.execute("UPDATE packs SET fecha_programada=?, tipo_programado='delivery', despachador_programado=?, retiro_programado=1 WHERE id=?", (hoy, yo, pid)); con.commit()
        assert A.montar_de(con, yo).get(f"pack:{pid}") == "falta", "el siguiente retiro no sale como ya montado"
        con.close()


@prueba("Viajes a la agencia: lo que se asigna al mismo despachador y la misma oficina entra al viaje que ya está por llevar (una sola ida); los separados de antes se juntan")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat(); ayer = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        manana = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        ofi = list(A.oficinas_agencia(con).get("Tealca") or {"": 0})[0]
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Viaje Junto','Viaje')").lastrowid; con.commit()
        pr = con.execute("SELECT id FROM productos WHERE COALESCE(tipo,'producto')='producto' LIMIT 1").fetchone()[0]
        def orden(n):
            oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,agencia) VALUES (?,?,'pendiente','pagada',60,0,'nacional','Tealca')", (n, cid)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,cantidad,precio) VALUES (?,?,1,60)", (oid, pr)); con.commit(); return oid
        sesion_de(c, "admin")
        asignar = lambda oid, fecha, desp="Pepa Viaje": c.post("/viajes/crear", data={"despachador": desp, "agencia": "Tealca", "oficina": ofi, "monto": "10", "fecha": fecha, "orden_id": str(oid), "volver": "/operaciones"})
        a, b, d, e, m = orden("#VJ1"), orden("#VJ2"), orden("#VJ3"), orden("#VJ4"), orden("#VJ5")
        asignar(a, hoy); asignar(b, hoy); asignar(d, hoy, "Otra Persona"); asignar(m, manana)
        vs = con.execute("SELECT id, despachador, pedidos, monto, fecha FROM viajes_agencia WHERE despachador IN ('Pepa Viaje','Otra Persona') ORDER BY id").fetchall()
        assert [(v["despachador"], v["pedidos"], v["monto"], v["fecha"]) for v in vs] == [("Pepa Viaje", 2, 10, hoy), ("Otra Persona", 1, 10, hoy), ("Pepa Viaje", 1, 10, manana)], [dict(v) for v in vs]
        assert con.execute("SELECT viaje_id FROM ordenes WHERE id=?", (a,)).fetchone()[0] == con.execute("SELECT viaje_id FROM ordenes WHERE id=?", (b,)).fetchone()[0], "una sola ida"
        v1 = vs[0]["id"]; con.execute("UPDATE viajes_agencia SET llevado_en=datetime('now') WHERE id=?", (v1,)); con.commit()
        asignar(e, hoy)   # el de antes ya se llevó: esto es otra ida
        assert con.execute("SELECT viaje_id FROM ordenes WHERE id=?", (e,)).fetchone()[0] != v1
        # los que quedaron separados antes de este cambio se juntan con la migración
        con.execute("DELETE FROM viajes_agencia WHERE despachador='Separado'")
        ids = []
        for f_, mo in ((ayer, 10), (hoy, 0), (hoy, 0)):
            vid = con.execute("INSERT INTO viajes_agencia (fecha,despachador,agencia,oficina,monto,pedidos) VALUES (?, 'Separado','Tealca',?,?,1)", (f_, ofi, mo)).lastrowid
            o_ = orden(f"#SP{vid}"); con.execute("UPDATE ordenes SET viaje_id=? WHERE id=?", (vid, o_)); ids.append(vid)
        con.execute("INSERT INTO viajes_agencia (fecha,despachador,agencia,oficina,monto,pedidos) VALUES (?, 'Separado','Tealca',?,10,1)", (manana, ofi)); con.commit()
        con.commit(); con.close()
        m_ = sqlite3.connect(A.DB, timeout=10); m_.executescript((A.MIGRACIONES / "024_juntar_viajes_misma_oficina.sql").read_text()); m_.close()
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        vs = con.execute("SELECT id, pedidos, monto, fecha FROM viajes_agencia WHERE despachador='Separado' ORDER BY id").fetchall()
        assert [(v["id"], v["pedidos"], v["monto"], v["fecha"]) for v in vs][0] == (ids[0], 3, 10, hoy) and len(vs) == 2, [dict(v) for v in vs]
        assert con.execute("SELECT COUNT(*) FROM ordenes WHERE viaje_id=?", (ids[0],)).fetchone()[0] == 3, "los paquetes pasan al viaje que queda"
        con.close()


@prueba("Taller: el pick-up dice solo el nombre de pila, aunque la ficha del cliente no lo tenga aparte")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB)
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Cristy Jose Cartagena Infante','')").lastrowid
        oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,fecha_prometida)
                             VALUES ('#PK1',?,'pendiente','pagada',90,0,'pickup',date('now','localtime'))""", (cid,)).lastrowid
        pid = con.execute("INSERT INTO productos (sku,nombre,tipo,precio) VALUES ('PK-NOMBRE','Porche Nombre','producto',90)").lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Porche Nombre',1,90,90)", (oid, pid)); con.commit(); con.close()
        sesion_de(c, "taller"); h = c.get("/taller").text
        assert "<b>Cristy</b>" in h and "Cartagena" not in h


@prueba("Taller: cada pick-up sale el día que le toca (y si se atrasa sigue saliendo); el de otro día no")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        d = lambda n: (datetime.date.today() + datetime.timedelta(days=n)).isoformat()
        p = con.execute("SELECT id, nombre, precio, costo FROM productos WHERE tipo='producto' AND categoria NOT IN ('opcion','kit') LIMIT 1").fetchone()
        for nombre, fecha in (("Hoyita", d(0)), ("Atrasadita", d(-2)), ("Jueveta", d(1))):
            cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES (?,?)", (nombre + " Prueba", nombre)).lastrowid
            oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,fecha_prometida) VALUES (?,?,'pendiente','pagada',24,0,'pickup',?)",
                              ("#T" + nombre, cid, fecha)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,?,1,?,?,?)", (oid, p["id"], p["nombre"], p["precio"], p["costo"], p["precio"]))
        con.commit()
        sesion_de(c, "taller"); h = c.get("/taller").text
        assert "Hoyita" in h and "Atrasadita" in h, "hoy y atrasados salen"
        assert "Jueveta" not in h, "el de mañana todavía no"
        con.close()


@prueba("Pedido a proveedor que llegó con más de lo pedido: Cristina lo acepta y suben la cantidad y lo que se le debe")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        pid = con.execute("""INSERT INTO produccion (cantidad,recibido,fecha_pedido,responsable,costo,estado,pieza,tipo_pedido)
                             VALUES (10,15,'2026-10-12','Yovanny Sánchez',30,'recibido','Grama','proveedor')""").lastrowid
        con.commit()
        sesion_de(c, "admin")
        assert f"/produccion/{pid}/aceptar-demas" in c.get("/produccion?tipo=proveedor&ver=todas").text, "sale el aviso con su botón"
        sesion_de(c, "taller"); c.post(f"/produccion/{pid}/aceptar-demas", follow_redirects=False)
        assert con.execute("SELECT cantidad FROM produccion WHERE id=?", (pid,)).fetchone()[0] == 10, "el taller no lo acepta"
        sesion_de(c, "admin"); c.post(f"/produccion/{pid}/aceptar-demas", follow_redirects=False)
        r = con.execute("SELECT cantidad, costo, pagado FROM produccion WHERE id=?", (pid,)).fetchone()
        assert tuple(r) == (15, 45.0, 0), tuple(r)
        assert f"/produccion/{pid}/aceptar-demas" not in c.get("/produccion?tipo=proveedor&ver=todas").text, "ya aceptado: no sale más"
        con.close()


@prueba("Despachador: al marcar entregado tiene que elegir cómo pagó (no viene 'Efectivo' puesto); corregir un efectivo a otra forma le quita esa plata de la mano")
def _():
    txt = (A.BASE / "templates" / "mis_entregas.html").read_text()
    assert '<select name="forma_recibida" required' in txt and '<option value="" selected disabled>Elegir…</option>' in txt
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Patricia Prueba','Patricia')").lastrowid
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel) VALUES ('#EF1',?,'entregada','pagada',77,0)", (cid,)).lastrowid
        pid = con.execute("""INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado,lo_tiene)
                             VALUES (?,'Efectivo USD',5,5,'USD',date('now'),'confirmado','Ingrid')""", (oid,)).lastrowid
        con.commit()
        sesion_de(c, "admin")
        c.post(f"/ordenes/{oid}/pago/{pid}/corregir", data={"monto_usd": "5", "forma": "Zelle"}, follow_redirects=False)
        assert tuple(con.execute("SELECT forma, lo_tiene FROM pagos WHERE id=?", (pid,)).fetchone()) == ("Zelle", None)
        con.close()


@prueba("Operaciones: una entrega de pack se pasa a otro día desde su fila ('Otro día'), sin tocar despachador ni delivery")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat(); manana = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Alexandra Prueba','Alexandra')").lastrowid
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel) VALUES ('#RP1',?,'entregada','pagada',96,0)", (cid,)).lastrowid
        pid = con.execute("INSERT INTO packs (cliente_id,orden_id,unidades,entregadas_inicio) VALUES (?,?,4,2)", (cid, oid)).lastrowid
        con.commit()
        A.pack_programar(None, pid, fecha=hoy, tipo_entrega="delivery_fuera", despachador="Cristina", retiro="1", delivery="10", delivery_pagado="antes", notas="", pago_forma="",
                         diferencia="", diferencia_pagada="0", volver="", direccion="", maps="", agencia="", destino="", retira_pregunta="", retira_nombre="", retira_cedula="", retira_telefono="", retira_correo="", con=con)
        sesion_de(c, "logistica")
        h = c.get("/operaciones").text
        assert f'action="/packs/{pid}/campo" class="cobro-f"' in h and "Otro día" in h
        c.post(f"/packs/{pid}/campo", data={"fecha": manana, "volver": "/operaciones"}, follow_redirects=False)
        k = con.execute("SELECT fecha_programada, despachador_programado, delivery_programado, delivery_pagado FROM packs WHERE id=?", (pid,)).fetchone()
        assert tuple(k) == (manana, "Cristina", 10.0, 1), tuple(k)
        assert "Alexandra" not in c.get("/operaciones?cola=hoy").text and "Alexandra" in c.get("/operaciones?cola=manana").text
        sesion_de(c, "taller"); c.post(f"/packs/{pid}/campo", data={"fecha": hoy}, follow_redirects=False)
        assert con.execute("SELECT fecha_programada FROM packs WHERE id=?", (pid,)).fetchone()[0] == manana, "el taller no reprograma"
        con.close()


@prueba("Operaciones: una orden normal que no ha salido se pasa a otro día desde su fila ('Otro día'); en ruta ya no")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat(); manana = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        p = con.execute("SELECT id, nombre, precio, costo FROM productos WHERE tipo='producto' AND categoria NOT IN ('opcion','kit') LIMIT 1").fetchone()
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Reprogramada Prueba','Reprogramada')").lastrowid
        oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,fecha_prometida,despachador)
                             VALUES ('#OD1',?,'pendiente','pagada',24,0,'delivery',?,'Juan')""", (cid, hoy)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,?,1,?,?,?)", (oid, p["id"], p["nombre"], p["precio"], p["costo"], p["precio"]))
        con.commit()
        sesion_de(c, "logistica")
        assert f'action="/ordenes/{oid}/fecha' in c.get("/operaciones").text
        c.post(f"/ordenes/{oid}/fecha?volver=/operaciones", data={"fecha": manana}, follow_redirects=False)
        assert tuple(con.execute("SELECT fecha_prometida, despachador FROM ordenes WHERE id=?", (oid,)).fetchone()) == (manana, "Juan")
        con.execute("UPDATE ordenes SET estado='en_ruta' WHERE id=?", (oid,)); con.commit()
        c.post(f"/ordenes/{oid}/fecha?volver=/operaciones", data={"fecha": hoy}, follow_redirects=False)
        assert con.execute("SELECT fecha_prometida FROM ordenes WHERE id=?", (oid,)).fetchone()[0] == manana, "en ruta no se reprograma así"
        sesion_de(c, "taller"); con.execute("UPDATE ordenes SET estado='pendiente' WHERE id=?", (oid,)); con.commit()
        c.post(f"/ordenes/{oid}/fecha", data={"fecha": hoy}, follow_redirects=False)
        assert con.execute("SELECT fecha_prometida FROM ordenes WHERE id=?", (oid,)).fetchone()[0] == manana, "el taller no reprograma"
        con.close()

@prueba("Cash flow: el detalle de una caja flota sobre la pantalla (no cambia el panel, que al deslizarse lo cerraba)")
def _():
    css = (A.BASE / "static" / "estilo.css").read_text(); js = (A.BASE / "static" / "controles.js").read_text()
    assert ".cf-lado:has(.cf-det:hover)" not in css, "cambiar el panel al pasar el mouse lo devolvía arriba y la nota se cerraba"
    assert ".cf-det-pop{position:fixed}" in css and "function ubicarDetalleCaja" in js


@prueba("Viaje a la agencia: viene con la fecha del pedido y sale en Operaciones el día que toca llevarlo, no antes")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        lunes = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        p = con.execute("SELECT id, nombre, precio, costo FROM productos WHERE tipo='producto' AND categoria NOT IN ('opcion','kit') LIMIT 1").fetchone()
        con.execute("UPDATE ordenes SET estado='entregada' WHERE tipo_entrega='nacional'")   # solo este pedido esperando la agencia
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,cedula) VALUES ('David Prueba','David','V12345678')").lastrowid
        oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,fecha_prometida,agencia)
                             VALUES ('#VJ1',?,'pendiente','pagada',24,0,'nacional',?,'Zoom')""", (cid, lunes)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,?,1,?,?,?)", (oid, p["id"], p["nombre"], p["precio"], p["costo"], p["precio"]))
        con.commit()
        sesion_de(c, "admin")
        h = c.get("/operaciones").text
        f_ = [x for x in h.split('action="/viajes/crear"')[1:] if "#VJ1" in x.split("</form>")[0]][0].split("</form>")[0]
        assert f'name="fecha" value="{lunes}"' in f_, "la fecha del viaje viene con la del pedido"
        vid = con.execute("INSERT INTO viajes_agencia (fecha,despachador,agencia,monto,pedidos,usuario_id) VALUES (?,'Cristina','Zoom',5,1,1)", (lunes,)).lastrowid
        con.execute("UPDATE ordenes SET viaje_id=? WHERE id=?", (vid, oid)); con.commit()
        assert f"/viajes/{vid}/llevado" not in c.get("/operaciones?cola=hoy").text, "hoy no sale: es para el lunes"
        assert f"/viajes/{vid}/llevado" in c.get(f"/operaciones?cola=dia&dia={lunes}").text and f"/viajes/{vid}/llevado" in c.get("/operaciones?cola=todo").text
        con.close()


@prueba("Seguimientos: uno ya marcado se corrige desde Contactados (resultado, nota) sin contar otro intento ni cambiar cuándo se hizo")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Daniel Prueba','Daniel')").lastrowid
        con.execute("""INSERT INTO seguimientos (cliente_id,tipo,clave,resultado,posponer_hasta,intentos,hecho_en)
                       VALUES (?,'repuesto','rep-prueba-1','mensaje','2026-10-20',1,'2026-10-17 23:39:00')""", (cid,))
        con.commit()
        sesion_de(c, "admin")
        assert 'name="corrige" value="1"' in c.get("/seguimientos?ver=hechos").text, "cada contactado tiene su 'Cambiar'"
        c.post("/seguimientos/hecho", data={"clave": "rep-prueba-1", "cliente_id": str(cid), "tipo": "repuesto", "resultado": "compro", "nota": "pidió por IG",
                                            "corrige": "1", "volver": "/seguimientos?ver=hechos"}, follow_redirects=False)
        r = con.execute("SELECT resultado, nota, posponer_hasta, intentos, hecho_en FROM seguimientos WHERE clave='rep-prueba-1'").fetchone()
        assert tuple(r) == ("compro", "pidió por IG", None, 1, "2026-10-17 23:39:00"), tuple(r)
        con.close()


@prueba("Viaje a la agencia: 'Ya los llevé' deja entregada la orden pagada, sin mover dinero; la que tiene algo por cobrar sigue pendiente y ya entregada o cancelada no se toca")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Juan Agencia','Juan')").lastrowid
        vid = con.execute("INSERT INTO viajes_agencia (fecha,despachador,agencia,monto,pedidos) VALUES (date('now'),?,'MRW',5,6)", (A.DESPACHADORES[0],)).lastrowid
        pro = con.execute("SELECT id, nombre FROM productos WHERE sku='PRO-M'").fetchone()
        def orden(n, estado, pago):
            oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,agencia,viaje_id,fecha_entrega)
                                 VALUES (?,?,?,?,50,0,'nacional','MRW',?,?)""", (n, cid, estado, pago, vid, "2026-10-01" if estado == "entregada" else None)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,?,1,50,0,50)", (oid, pro["id"], pro["nombre"]))
            return oid
        pagada, en_ruta = orden("#V1", "pendiente", "pagada"), orden("#V2", "en_ruta", "pagada")
        debe, contra = orden("#V3", "pendiente", "abonada"), orden("#V4", "pendiente", "contra_entrega")
        vieja, cancelada = orden("#V5", "entregada", "pagada"), orden("#V6", "cancelada", "pagada")
        con.commit()
        dinero = lambda: [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("pagos", "movimientos", "mov_inventario")]
        antes = dinero()
        sesion_de(c, "admin")
        pantalla = c.get("/operaciones?cola=todo&tipo=nacional").text
        assert "#V1" in pantalla and "#V3" in pantalla, "antes de llevarlos salen las dos"
        sesion_de(c, "despachador")   # lo toca el despachador desde su pantalla
        c.post(f"/viajes/{vid}/llevado", data={"volver": "/mis-entregas"}, follow_redirects=False)
        sesion_de(c, "admin")
        pantalla = c.get("/operaciones?cola=todo&tipo=nacional").text
        assert "#V1" not in pantalla and "#V3" in pantalla, "la pagada deja de salirle a Cristina; la que debe sigue"
        fila = lambda oid: con.execute("SELECT estado, estado_pago, fecha_entrega FROM ordenes WHERE id=?", (oid,)).fetchone()
        hoy = datetime.date.today().isoformat()
        for oid in (pagada, en_ruta):
            assert fila(oid)["estado"] == "entregada" and fila(oid)["estado_pago"] == "pagada" and fila(oid)["fecha_entrega"].startswith(hoy), tuple(fila(oid))
            assert con.execute("SELECT COUNT(*) FROM historial WHERE orden_id=? AND detalle LIKE '%llevó a MRW%'", (oid,)).fetchone()[0] == 1
        assert tuple(fila(debe))[:2] == ("pendiente", "abonada") and tuple(fila(contra))[:2] == ("pendiente", "contra_entrega"), "con algo por cobrar la cierra Cristina"
        assert tuple(fila(vieja)) == ("entregada", "pagada", "2026-10-01"), "la fecha de una ya entregada no cambia"
        assert fila(cancelada)["estado"] == "cancelada"
        assert dinero() == antes, "no se mueve dinero ni inventario"
        c.post(f"/viajes/{vid}/llevado", data={"volver": "/operaciones"}, follow_redirects=False)
        assert con.execute("SELECT COUNT(*) FROM historial WHERE orden_id=? AND detalle LIKE '%llevó a MRW%'", (pagada,)).fetchone()[0] == 1, "tocarlo dos veces no repite nada"
        con.close()


def _cliente_con_repuesto_vencido(con, sku="PRO-M"):
    """Andrea, con Milo, que recibió su porche hace 40 días: le toca el primer repuesto."""
    cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono,porche_version) VALUES ('Andrea Mazhar','Andrea','04145539011','PRO')").lastrowid
    con.execute("INSERT INTO mascotas (cliente_id,nombre) VALUES (?,'Milo')", (cid,))
    pro = con.execute("SELECT id, nombre FROM productos WHERE sku=?", (sku,)).fetchone()
    entrega = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
    oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,fecha_entrega) VALUES ('#AN1',?,'entregada','pagada',88,0,?)", (cid, entrega)).lastrowid
    con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,?,1,88,0,88)", (oid, pro["id"], pro["nombre"]))
    con.commit()
    return cid, f"seg:{cid}:{entrega}"


@prueba("Seguimientos: cada intento abre un WhatsApp distinto, con su nombre y el de la mascota; después del descanso vuelve a empezar desde el primero")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid, clave = _cliente_con_repuesto_vencido(con)
        sesion_de(c, "admin")
        def el_mensaje():
            return next(s_ for s_ in A.seguimientos_pendientes(con) if s_["cliente_id"] == cid)
        vistos = []
        for intentos in (0, 1, 2, 3, 4, 5):
            if intentos:
                con.execute("INSERT OR REPLACE INTO seguimientos (cliente_id,tipo,clave,resultado,posponer_hasta,intentos) VALUES (?,'primer_repuesto',?,'mensaje',?,?)",
                            (cid, clave, datetime.date.today().isoformat(), intentos))
                con.commit()
            s_ = el_mensaje()
            toca = intentos % 4 + 1
            assert s_["intento"] == toca and s_["estado"] == (f"{toca}º intento" if toca > 1 else "Pendiente"), s_
            m = A.mensajes_repuesto(con, s_)
            vistos.append(m[0])
            pagina = html.unescape(c.get("/seguimientos").text)
            assert f'data-msj="{m[0]}"' in pagina, f"el botón de WhatsApp abre el del intento {intentos + 1}"
            if toca > 1:
                assert "Andrea" in m[0] and "Milo" in m[0], m[0]
                assert len(set(m)) == 3, "'Otra versión' ofrece los tres"
            if toca > 2: assert "mediano" in m[0], m[0]
        assert len(set(vistos[:4])) == 4, "1º, 2º, 3º y 4º son distintos"
        assert vistos[4] == vistos[0] and vistos[5] == vistos[1], "después del descanso empieza de nuevo"
        con.close()


@prueba("Seguimientos: sin respuesta sale a los 3 días, a la semana y a las 2 semanas; luego descansa 2 meses. El cobro sigue cada 3 días y corregir no cuenta otro intento")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid, clave = _cliente_con_repuesto_vencido(con)
        sesion_de(c, "admin")
        hoy = datetime.date.today()
        def marcar(clave_, tipo, **extra):
            c.post("/seguimientos/hecho", data=dict(clave=clave_, cliente_id=cid, tipo=tipo, resultado="mensaje", **extra), follow_redirects=False)
            r = con.execute("SELECT posponer_hasta, intentos FROM seguimientos WHERE clave=?", (clave_,)).fetchone()
            return (datetime.date.fromisoformat(r["posponer_hasta"]) - hoy).days, r["intentos"]
        assert [marcar(clave, "primer_repuesto")[0] for _ in range(5)] == [3, 7, 14, 60, 3]
        assert marcar(clave, "primer_repuesto", corrige="1") == (3, 5), "corregir no cuenta otro intento"
        assert [marcar("cobro:1:2026-10-01", "cobro")[0] for _ in range(5)] == [3, 3, 3, 3, 3]
        assert A.estado_seg({"tipo": "cobro", "resultado": "mensaje", "posponer_hasta": "2026-11-04", "intentos": 4}, hoy) == "5º intento"
        con.close()


@prueba("Seguimientos: el mensaje de reintento se adapta a pack, prepagado, varios perros y datos que faltan; el que pidió otro día vuelve al primero")
def _():
    con = base_limpia()
    cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Lyndia Perez','Lyndia')").lastrowid
    con.execute("INSERT INTO mascotas (cliente_id,nombre) VALUES (?,'Toby')", (cid,))
    con.execute("INSERT INTO mascotas (cliente_id,nombre) VALUES (?,'Luna')", (cid,))
    base = {"cliente_id": cid, "tamano": "Grande", "dias": 35, "intento": 2}
    pack = A.mensajes_repuesto(con, dict(base, tipo="repuesto", pack=True))[0]
    assert "¿Cómo siguen Toby y Luna?" in pack and "repuesto grande de tu pack" in pack and "Lyndia" in pack, pack
    pagado = A.mensajes_repuesto(con, dict(base, tipo="prepagado"))[0]
    assert "repuesto grande que tienes pagado" in pagado, pagado
    cuarto = A.mensajes_repuesto(con, dict(base, tipo="repuesto", pack=True, intento=4))[0]
    assert "cuando Toby y Luna necesiten el repuesto grande de tu pack" in cuarto, cuarto
    normal = A.mensajes_repuesto(con, dict(base, tipo="repuesto", pack=False))[0]
    assert "pack" not in normal and "pagado" not in normal and "  " not in normal, normal
    assert "pack" not in A.mensajes_repuesto(con, dict(base, tipo="primer_repuesto"))[0]
    # sin nombre, sin perro y sin tamaño la frase sigue bien escrita
    vacio = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('','')").lastrowid
    for t in A.mensajes_repuesto(con, {"cliente_id": vacio, "tipo": "repuesto", "tamano": "—", "intento": 3}):
        assert " ," not in t and "  " not in t and " para " not in t.replace(" para ver", "") and "{" not in t, t
    assert A.mensajes_repuesto(con, {"cliente_id": vacio, "tipo": "repuesto", "tamano": "—", "intento": 2})[1].startswith("¡Buenas!")
    # nada de malla ni cinta, y el corazón siempre verde
    todos = " ".join(A.REINTENTOS_OFRECER + A.REINTENTOS_PAGADO).lower()
    assert not any(x in todos for x in ("malla", "cinta", "❤")), todos
    # los repuestos se hacen al pedir: no están guardados ni se apartan
    assert not any(x in todos for x in ("está aquí", "apart", "esperando", "fastidiar", "cariño")), todos
    # el Básico no compra repuestos: esto no le cambia su mensaje
    assert A.mensajes_repuesto(con, dict(base, tipo="basico")) == A.mensajes_repuesto(con, dict(base, tipo="basico", intento=1))
    # primer intento: lo que se cambia es el repuesto, también en 'Otra versión'
    primeros = A.mensajes_repuesto(con, dict(base, tipo="repuesto", intento=1))
    assert not any("grama" in m or "de el " in m for m in primeros), primeros
    assert "cambiar el repuesto del porche de Toby y Luna" in primeros[2], primeros[2]
    assert A.mensajes_repuesto(con, dict(base, tipo="repuesto", intento=1))[0].startswith("¡Hola Lyndia! 👋 ¿Cómo van Toby y Luna con el porche? Ya van 5 semanas, así que seguro ya toca cambiar el repuesto 🌱 ¿Te preparamos el grande? 💚")
    assert A.intento_seg(None) == 1
    assert A.intento_seg({"tipo": "repuesto", "resultado": "mensaje", "posponer_hasta": "2026-11-04", "intentos": 3}) == 4
    assert A.intento_seg({"tipo": "repuesto", "resultado": "fecha", "posponer_hasta": "2026-11-04", "intentos": 3}) == 1, "dijo qué día lo quiere: no es insistirle"
    con.close()


@prueba("Operaciones: Pick-up no lleva despachador; si retira otra persona sale su nombre; el delivery sigue con su despachador")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Linda Pickup','Linda')").lastrowid
        pro = con.execute("SELECT id, nombre FROM productos WHERE sku='PRO-M'").fetchone()
        def orden(n, tipo, receptor=None):
            oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,receptor_nombre,fecha_prometida)
                                 VALUES (?,?,'pendiente','pagada',50,0,?,?,date('now'))""", (n, cid, tipo, receptor)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,?,1,50,0,50)", (oid, pro["id"], pro["nombre"]))
        orden("#P1", "pickup"); orden("#P2", "pickup", "Rosa Vecina")
        con.commit()
        sesion_de(c, "admin")
        solo = c.get("/operaciones?cola=todo&tipo=pickup").text
        assert "#P1" in solo and "#P2" in solo, "salen los pick-ups"
        tabla = solo[solo.index('class="ops-t'):]
        assert "<th>Despachador</th>" not in tabla and 'data-t="Despachador"' not in tabla and "retira el cliente" not in tabla, "sin despachador en Pick-up"
        assert "retira Rosa Vecina" in tabla, "el que retira por el cliente no se pierde"
        orden("#P3", "delivery"); con.commit()
        juntos = c.get("/operaciones?cola=todo").text
        grupos = re.findall(r'op-card-h"><b>([^<]+)', juntos)
        assert "Pick-up" in grupos and "Delivery Caracas" in grupos and "#P3" in juntos, grupos
        assert juntos.count("<th>Despachador</th>") == len(grupos) - 1, "todos con su columna, menos Pick-up"
        por_status = c.get("/operaciones?cola=todo&vista=status").text
        assert "<th>Despachador</th>" in por_status and "retira el cliente" not in por_status, "en una tabla mezclada el pick-up queda vacío"
        con.close()


@prueba("Taller: un repuesto con malla dice '+ malla' en lo que se lleva cada despachador y en los pick-ups")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat()
        rep = con.execute("SELECT id, nombre, precio, costo FROM productos WHERE sku='REP-G'").fetchone()
        for nombre, te, desp in (("Sandra", "delivery", "Cristina"), ("Marilyn", "pickup", None)):
            cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES (?,?)", (nombre + " Prueba", nombre)).lastrowid
            oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,fecha_prometida,despachador)
                                 VALUES (?,?,'pendiente','pagada',44,0,?,?,?)""", ("#M" + nombre, cid, te, hoy, desp)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total,malla) VALUES (?,?,?,1,?,?,?,1)",
                        (oid, rep["id"], rep["nombre"], rep["precio"], rep["costo"], rep["precio"]))
        con.commit()
        sesion_de(c, "taller"); h = c.get("/taller").text
        assert f"1× {rep['nombre']} + malla" in h, "el despachador se lleva el repuesto con su malla"
        i = h.index("Marilyn"); assert "+ malla" in h[i:i + 400], "el pick-up también lo dice"
        con.close()


@prueba("Ficha del cliente: '+ Repuesto que dejó pagado' (del histórico de Airtable) lo deja en Repuestos prepagados para programarlo")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,porche_version,porche_tamano) VALUES ('Maria Carmen Prueba','Maria Carmen','PRO','Mediano')").lastrowid
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel) VALUES ('#AT1',?,'entregada','pagada',112,1)", (cid,)).lastrowid
        pro = con.execute("SELECT id, nombre FROM productos WHERE sku LIKE 'PRO-%' LIMIT 1").fetchone()
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,?,1,88,0,88)", (oid, pro["id"], pro["nombre"]))
        con.commit()
        sesion_de(c, "admin")
        assert 'action="/prepagados/nuevo"' in c.get(f"/clientes/{cid}").text
        r = c.post("/prepagados/nuevo", data={"cliente_id": str(cid), "tamano": "Mediano", "pagado_en": "2026-10-02", "notas": "venía en la orden #AT1"}, follow_redirects=False)
        assert r.headers["location"] == "/prepagados?ver=sin_programar"
        k = con.execute("SELECT tamano, pagado_en, entregado_en, fecha_programada FROM repuestos_prepagados WHERE cliente_id=?", (cid,)).fetchone()
        assert tuple(k) == ("Mediano", "2026-10-02", None, None), tuple(k)
        assert "Maria Carmen" in c.get("/prepagados?ver=sin_programar").text
        con.close()


@prueba("Repuesto prepagado: activarlo con el delivery 'ya pagado' pide con qué se pagó; sin forma no se cae, queda por cobrar")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat()
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Carmen Prueba','Carmen')").lastrowid
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel) VALUES ('#PP1',?,'entregada','pagada',24,0)", (cid,)).lastrowid
        con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (?,'Zelle',24,24,'USD','2026-10-02','confirmado')", (oid,))
        rid = con.execute("INSERT INTO repuestos_prepagados (cliente_id,orden_id,tamano,pagado_en) VALUES (?,?,'Mediano','2026-10-02')", (cid, oid)).lastrowid
        con.commit()
        sesion_de(c, "admin")
        assert 'name="delivery_forma"' in c.get("/prepagados?ver=sin_programar").text, "pregunta con qué se pagó el delivery"
        r = c.post(f"/prepagados/{rid}/programar", data={"fecha": hoy, "tipo_entrega": "delivery", "despachador": "Cristina", "delivery": "5", "delivery_pagado": "1", "delivery_forma": ""}, follow_redirects=False)
        assert r.status_code == 303 and con.execute("SELECT delivery_pagado FROM repuestos_prepagados WHERE id=?", (rid,)).fetchone()[0] == 0, "sin forma: queda por cobrar"
        c.post(f"/prepagados/{rid}/programar", data={"fecha": hoy, "tipo_entrega": "delivery", "despachador": "Cristina", "delivery": "5", "delivery_pagado": "1", "delivery_forma": "Zelle"}, follow_redirects=False)
        assert con.execute("SELECT delivery_pagado FROM repuestos_prepagados WHERE id=?", (rid,)).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM pagos WHERE orden_id=? AND forma='Zelle' AND monto_usd=5", (oid,)).fetchone()[0] == 1
        con.close()


@prueba("Espera aviso: el pedido sale de Hoy, Retrasadas, Inicio y el taller hasta que se le pone fecha; queda en Todo")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        ayer = (datetime.date.today() - datetime.timedelta(days=1)).isoformat(); manana = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        p = con.execute("SELECT id, nombre, precio, costo FROM productos WHERE tipo='producto' AND categoria NOT IN ('opcion','kit') LIMIT 1").fetchone()
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Yuliana Prueba','Yuliana')").lastrowid
        oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,fecha_prometida)
                             VALUES ('#EA1',?,'pendiente','pagada',24,0,'pickup',?)""", (cid, ayer)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,?,1,?,?,?)", (oid, p["id"], p["nombre"], p["precio"], p["costo"], p["precio"]))
        con.commit()
        sesion_de(c, "admin")
        assert "#EA1" in c.get("/operaciones?cola=retrasadas").text
        c.post(f"/ordenes/{oid}/fecha?volver=/operaciones", data={"espera": "1"}, follow_redirects=False)
        assert con.execute("SELECT espera_aviso FROM ordenes WHERE id=?", (oid,)).fetchone()[0] == 1
        for cola in ("hoy", "retrasadas"): assert "#EA1" not in c.get(f"/operaciones?cola={cola}").text, cola
        assert "espera aviso" in c.get("/operaciones?cola=todo").text
        assert "Yuliana" not in c.get("/inicio").text.split("Seguimientos")[0], "en el Inicio no sale como entrega del día"
        sesion_de(c, "taller"); assert "Yuliana" not in c.get("/taller").text
        sesion_de(c, "admin")
        c.post(f"/ordenes/{oid}/fecha?volver=/operaciones", data={"fecha": manana}, follow_redirects=False)
        assert tuple(con.execute("SELECT espera_aviso, fecha_prometida FROM ordenes WHERE id=?", (oid,)).fetchone()) == (0, manana)
        assert "#EA1" in c.get("/operaciones?cola=manana").text
        con.close()


@prueba("Mascota que falleció: sale con 🕊️, no se felicita su cumpleaños, no sale en los mensajes; si era la única, se pausan los repuestos")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today()
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,porche_version) VALUES ('Lola Dueña Prueba','Ana','PRO')").lastrowid
        mid = con.execute("INSERT INTO mascotas (cliente_id,nombre,raza,cumple_mes_dia) VALUES (?,'Lolita','Poodle',?)", (cid, hoy.strftime("%m-%d"))).lastrowid
        pro = con.execute("SELECT id, nombre FROM productos WHERE sku='PRO-M'").fetchone()
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,fecha_entrega) VALUES ('#LO1',?,'entregada','pagada',88,0,?)",
                          (cid, (hoy - datetime.timedelta(days=60)).isoformat())).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,?,1,88,0,88)", (oid, pro["id"], pro["nombre"]))
        con.commit()
        assert any(x["perro"] == "Lolita" for x in A.cumples_proximos(con, 0)), "viva: sale su cumpleaños"
        assert any(s_["cliente_id"] == cid for s_ in A.seguimientos_pendientes(con)), "viva: le toca repuesto"
        sesion_de(c, "admin")
        c.post(f"/clientes/{cid}/perro", data={"id": str(mid), "nombre": "Lolita", "raza": "Poodle", "cumple": hoy.strftime("%d/%m"), "notas": "", "fallecida": "1"}, follow_redirects=False)
        assert con.execute("SELECT fallecida FROM mascotas WHERE id=?", (mid,)).fetchone()[0] == hoy.isoformat()
        assert not any(x["perro"] == "Lolita" for x in A.cumples_proximos(con, 0)), "no se felicita"
        assert not any(s_["cliente_id"] == cid for s_ in A.seguimientos_pendientes(con)), "era su único perro: se pausan los repuestos"
        assert "Lolita" not in " ".join(A.mensajes_repuesto(con, {"cliente_id": cid}))
        assert "Lolita</b> 🕊️" in c.get(f"/clientes/{cid}").text
        con.execute("INSERT INTO mascotas (cliente_id,nombre) VALUES (?,'Toby')", (cid,)); con.commit()
        assert any(s_["cliente_id"] == cid for s_ in A.seguimientos_pendientes(con)), "perro nuevo: vuelven los repuestos"
        c.post(f"/clientes/{cid}/perro", data={"id": str(mid), "nombre": "Lolita", "raza": "Poodle", "cumple": "", "notas": ""}, follow_redirects=False)
        assert con.execute("SELECT fallecida FROM mascotas WHERE id=?", (mid,)).fetchone()[0] is None, "desmarcarla la vuelve a la normalidad"
        con.close()


def _seguimientos_viejos():
    """Una base con clientes de Airtable: Alesia (repuesto hace 71 días), uno de hace 300, un Básico, uno solo en el
    Registro de ventas, uno sin porche y uno normal del ERP (porche hace 21 días: le toca hoy)."""
    con = base_limpia(); hoy = datetime.date.today()
    for sku, nombre, cat in (("REP-G", "Repuesto Grande", "repuesto"), ("REP-M", "Repuesto Mediano", "repuesto"), ("PRO-M", "Porche PRO Mediano", "porche")):
        con.execute("INSERT INTO productos (sku,nombre,categoria) VALUES (?,?,?)", (sku, nombre, cat))
    def pedido(cid, sku, hace):
        p_ = con.execute("SELECT id, nombre FROM productos WHERE sku=?", (sku,)).fetchone()
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,fecha_entrega) VALUES (?,?,'entregada','pagada',27,0,?)",
                          (f"#AT{cid}{hace}", cid, (hoy - datetime.timedelta(days=hace)).isoformat())).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,?,1,27,0,27)", (oid, p_["id"], p_["nombre"]))
    ids = {}
    for k, nombre, version, tam in (("alesia", "Alesia Prueba", "PRO", "Grande"), ("vieja", "Vieja Prueba", "PRO", None), ("basico", "Básico Prueba", "Básico", None),
                                    ("registro", "Registro Prueba", "PRO", "Mediano"), ("sin_ficha", "Sin Porche Prueba", None, None), ("normal", "Normal Prueba", None, None)):
        ids[k] = con.execute("INSERT INTO clientes (nombre,nombre_pila,porche_version,porche_tamano) VALUES (?,?,?,?)", (nombre, nombre.split()[0], version, tam)).lastrowid
    pedido(ids["alesia"], "REP-G", 140); pedido(ids["alesia"], "REP-G", 71)
    pedido(ids["vieja"], "REP-M", 300)
    pedido(ids["basico"], "REP-M", 60)
    pedido(ids["sin_ficha"], "REP-M", 40)
    pedido(ids["normal"], "PRO-M", 21)
    con.execute("INSERT INTO registro_ventas (fecha,cliente,producto,precio,cantidad) VALUES (?,' registro prueba','Repuesto',24,1)", ((hoy - datetime.timedelta(days=200)).isoformat(),))
    con.commit()
    return con, ids


def _con_ajustes(viejos_por_dia):
    antes = (A.VIEJOS_POR_DIA, A.INTEGRAR_DESDE)
    A.VIEJOS_POR_DIA, A.INTEGRAR_DESDE = viejos_por_dia, datetime.date.today().isoformat()   # como si se publicara hoy
    return antes


@prueba("Seguimientos: los clientes viejos de Airtable entran (ficha PRO, Básico, solo en el Registro de ventas), aunque lleven meses sin comprar; el que no tiene porche no")
def _():
    antes = _con_ajustes(10)
    try:
        con, ids = _seguimientos_viejos()
        segs = {s_["cliente_id"]: s_ for s_ in A.seguimientos_pendientes(con)}
        assert segs[ids["alesia"]]["tipo"] == "repuesto" and segs[ids["alesia"]]["que"] == "Repuesto Grande", segs.get(ids["alesia"])
        assert ids["vieja"] in segs, "300 días sin comprar: igual hay que escribirle"
        assert segs[ids["basico"]]["tipo"] == "basico", "ficha Básico: se le pregunta cómo le fue"
        assert segs[ids["registro"]]["que"] == "Repuesto Mediano", "sin pedidos en el ERP: su última compra sale del Registro de ventas"
        assert ids["sin_ficha"] not in segs, "sin porche ni ficha: no es miembro"
        con.execute("INSERT INTO seguimientos (cliente_id,tipo,clave,resultado,intentos) VALUES (?,?,?,'ya_no_usa',1)", (ids["vieja"], "repuesto", segs[ids["vieja"]]["clave"]))
        con.commit()
        assert ids["vieja"] not in {s_["cliente_id"] for s_ in A.seguimientos_pendientes(con)}, "'Ya no lo usa': deja de salir"
    finally:
        A.VIEJOS_POR_DIA, A.INTEGRAR_DESDE = antes


@prueba("Seguimientos: los clientes viejos entran de a poco a 'Hoy' (los que compraron hace menos primero); los del día no cambian y al día siguiente siguen en Hoy marcados 'Atrasado'")
def _():
    antes = _con_ajustes(2)
    try:
        con, ids = _seguimientos_viejos()
        hoy_ = lambda: [s_["cliente_id"] for s_ in A.seguimientos_pendientes(con) if s_.get("fase") == "hoy"]
        primeros = hoy_()
        assert ids["normal"] in primeros, "el cliente normal del ERP no cuenta en el cupo"
        viejos_hoy = [c_ for c_ in primeros if c_ != ids["normal"]]
        assert viejos_hoy == [ids["basico"], ids["alesia"]], f"2 por día, el que compró hace menos primero (60 y 71 días): {viejos_hoy}"
        assert hoy_() == primeros, "al volver a abrir no se suman otros"
        ayer = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        con.execute("UPDATE seguimientos_integrados SET dia=?", (ayer,)); con.commit()   # como si hubieran entrado ayer
        segs = {s_["cliente_id"]: s_ for s_ in A.seguimientos_pendientes(con)}
        assert segs[ids["alesia"]]["fase"] == "hoy" and segs[ids["alesia"]]["estado"] == "Atrasado · 1 d", "no se contactó ayer: sigue en Hoy, marcado Atrasado"
        assert segs[ids["registro"]]["fase"] == "hoy" and segs[ids["vieja"]]["fase"] == "hoy", "hoy entran los 2 siguientes"
    finally:
        A.VIEJOS_POR_DIA, A.INTEGRAR_DESDE = antes

@prueba("Crear orden: la casilla 'Registrar en Cash flow' solo la tiene Cristina; anota cada pago en su caja con la fecha de pago, y el efectivo no entra")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT OR REPLACE INTO config (clave, valor) VALUES ('ventas_auto','0')")
        con.execute("INSERT OR IGNORE INTO cuentas (codigo,nombre,moneda,tipo,activa,orden) VALUES ('001','Efectivo USD','USD','operativa',1,1)")
        con.commit(); A.cargar_formas_pago()
        p = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 AND categoria NOT IN ('porche','repuesto','opcion','kit') LIMIT 1").fetchone()["id"]
        con.execute("UPDATE productos SET precio=100 WHERE id=?", (p,))
        cid = con.execute("SELECT MIN(id) FROM clientes").fetchone()[0]
        fo = con.execute("SELECT nombre FROM cuentas WHERE activa=1 AND COALESCE(cobra,1)=1 AND nombre NOT LIKE 'Efectivo%' ORDER BY orden, codigo LIMIT 1 OFFSET 1").fetchone()[0]
        con.commit()
        def crear(**extra):
            c.post("/ordenes/nueva", data={"cliente_id": str(cid), "canal": "whatsapp", "producto_id": str(p), "cantidad": "1", "personalizacion": "", "color": "", "malla": "0",
                   "tipo_entrega": "pickup", "pago_forma": [fo, "Efectivo USD"], "pago_monto": ["60", "40"], "pago_ref": ["", ""], "fecha_pago": "2026-10-08", **extra})
            return con.execute("SELECT * FROM ordenes WHERE cliente_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        libro = lambda: con.execute("SELECT m.*, k.nombre caja FROM movimientos m JOIN cuentas k ON k.id=m.cuenta_destino_id WHERE m.categoria='Ventas'").fetchall()
        sesion_de(c, "admin")
        assert 'name="en_cashflow"' in c.get("/ordenes/nueva/panel").text
        crear()
        assert not libro(), "sin marcar no entra nada"
        o = crear(en_cashflow="1")
        ms = libro()
        assert len(ms) == 1 and (ms[0]["caja"], ms[0]["monto_usd"], ms[0]["fecha"]) == (fo, 60, "2026-10-08"), [dict(m) for m in ms]
        assert o["numero"] in ms[0]["concepto"] and fo in ms[0]["concepto"], ms[0]["concepto"]
        assert con.execute("SELECT en_cashflow FROM pagos WHERE orden_id=? AND forma=?", (o["id"], fo)).fetchone()[0] == 1
        sesion_de(c, "logistica")
        assert 'name="en_cashflow"' not in c.get("/ordenes/nueva/panel").text, "logística no la ve"
        crear(en_cashflow="1")
        assert len(libro()) == 1, "aunque llegue marcada, de logística no entra"
        sesion_de(c, "admin")
        con.execute("UPDATE config SET valor='1' WHERE clave='ventas_auto'"); con.commit()
        assert 'name="en_cashflow"' not in c.get("/ordenes/nueva/panel").text, "si las ventas entran solas, no hace falta"
        crear(en_cashflow="1")
        assert len(libro()) == 1, "no se cuenta dos veces"
        con.close()


@prueba("Paga otro día: la orden sale de 'Con saldo pendiente' y del aviso de Inicio hasta ese día; sigue debiendo, la entrega no cambia y el despachador no puede marcarlo")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy_ = datetime.date.today(); viernes = (hoy_ + datetime.timedelta(days=2)).isoformat()
        con.execute("INSERT INTO clientes (id,nombre,nombre_pila) VALUES (71,'Marianna Prueba','Marianna')")
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,subtotal,delivery,total,tipo_entrega,fecha_prometida) VALUES ('#71',71,'pendiente','sin_pago',24,5,29,'delivery',?)", (viernes,)).lastrowid
        p = con.execute("SELECT id FROM productos WHERE tipo='producto' LIMIT 1").fetchone()["id"]
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,'Repuesto Grande',1,24,0,24)", (oid, p))
        con.commit()
        sesion_de(c, "admin")
        avisos = lambda: int(re.search(r"(\d+) ordene?s? con saldo pendiente", c.get("/inicio").text).group(1))
        n_antes = avisos()
        h = c.get("/operaciones?cola=con_saldo").text
        assert "Marianna Prueba" in h and "Paga otro día" in h
        c.post(f"/ordenes/{oid}/paga-el", data={"fecha": viernes})
        o = con.execute("SELECT * FROM ordenes WHERE id=?", (oid,)).fetchone()
        assert (o["paga_el"], o["fecha_prometida"], o["estado_pago"]) == (viernes, viernes, "sin_pago"), dict(o)
        assert "Marianna Prueba" not in c.get("/operaciones?cola=con_saldo").text, "no sale hasta el viernes"
        assert avisos() == n_antes - 1, "ni en el aviso de Inicio"
        h = c.get("/operaciones?cola=todo").text
        assert "Marianna Prueba" in h and "Paga el" in h, "en Todo sigue, con su día de pago"
        assert "Paga el" in c.get(f"/ordenes/{oid}/panel").text, "al abrir el pedido se ve y se puede cambiar"
        con.execute("UPDATE ordenes SET paga_el=? WHERE id=?", (hoy_.isoformat(),oid)); con.commit()
        assert "Marianna Prueba" in c.get("/operaciones?cola=con_saldo").text, "llegó el día: vuelve sola"
        c.post(f"/ordenes/{oid}/paga-el", data={"fecha": viernes}); c.post(f"/ordenes/{oid}/paga-el", data={"fecha": ""})
        assert con.execute("SELECT paga_el FROM ordenes WHERE id=?", (oid,)).fetchone()[0] is None, "Quitar la devuelve a la lista"
        sesion_de(c, "despachador")
        c.post(f"/ordenes/{oid}/paga-el", data={"fecha": viernes})
        assert con.execute("SELECT paga_el FROM ordenes WHERE id=?", (oid,)).fetchone()[0] is None, "el despachador no puede"
        con.close()


@prueba("Pedido viejo: queda en la ficha con su fecha y lo pagado, sin tocar ventas, Registro, cajas ni inventario; lo que se cobre después sí cuenta")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT OR REPLACE INTO config (clave, valor) VALUES ('ventas_auto','0')")
        con.execute("INSERT OR IGNORE INTO cuentas (codigo,nombre,moneda,tipo,activa,orden) VALUES ('001','Efectivo USD','USD','operativa',1,1)")
        con.commit(); A.cargar_formas_pago()
        hoy_ = datetime.date.today(); hoy_s = hoy_.isoformat(); dia = (hoy_ - datetime.timedelta(days=36)).isoformat()
        rep = con.execute("SELECT id FROM productos WHERE sku='REP-G'").fetchone()["id"]
        otro = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 AND categoria NOT IN ('porche','repuesto','opcion','kit') LIMIT 1").fetchone()["id"]
        cid = con.execute("SELECT MIN(id) FROM clientes").fetchone()[0]
        fo = con.execute("SELECT nombre FROM cuentas WHERE activa=1 AND COALESCE(cobra,1)=1 AND nombre NOT LIKE 'Efectivo%' ORDER BY orden, codigo LIMIT 1 OFFSET 1").fetchone()[0]
        con.commit()
        def crear(pid=rep, cant="2", forma=fo, monto="51.04", fecha=dia, **extra):
            return c.post("/ordenes/nueva", data={"cliente_id": str(cid), "canal": "cashea", "producto_id": str(pid), "cantidad": cant, "personalizacion": "", "color": "", "malla": "0",
                          "prepagar": "1" if pid == rep else "0", "tipo_entrega": "pickup", "pago_forma": [forma], "pago_monto": [monto], "pago_ref": [""], "fecha_pago": fecha, **extra})
        ultima = lambda: con.execute("SELECT * FROM ordenes WHERE cliente_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        ventas = lambda d: A.ventas_por_dia(con, d, d).get(d, (0, 0))[0]
        registro = lambda oid: [r for r in A._historial_rows(con, "", "", "") if r.get("oid") == oid]
        sesion_de(c, "logistica")
        assert 'name="pedido_viejo"' not in c.get("/ordenes/nueva/panel").text, "logística no la ve"
        crear(pedido_viejo="1")
        assert ultima()["origen_excel"] == 0, "aunque llegue marcada, de logística es una orden normal"
        sesion_de(c, "admin")
        pantalla = c.get("/ordenes/nueva/panel").text
        assert 'name="pedido_viejo"' not in pantalla and "Pedido viejo" not in pantalla and 'name="en_cashflow"' in pantalla, "Cristina la quitó de la pantalla; Registrar en Cash flow sigue"
        n = con.execute("SELECT COUNT(*) FROM ordenes").fetchone()[0]
        assert crear(pedido_viejo="1", fecha=hoy_s).status_code >= 400, "con fecha de hoy no es viejo"
        assert con.execute("SELECT COUNT(*) FROM ordenes").fetchone()[0] == n
        v_hoy, v_dia = ventas(hoy_s), ventas(dia)
        inv = con.execute("SELECT COUNT(*) FROM mov_inventario").fetchone()[0]
        crear(pedido_viejo="1", en_cashflow="1")
        o = ultima(); oid = o["id"]
        assert (o["origen_excel"], o["estado"], o["estado_pago"], o["total"]) == (A.PEDIDO_VIEJO, "entregada", "pagada", 51.04), dict(o)
        assert (o["creado_en"][:10], o["fecha_pago"], o["fecha_entrega"], o["factura_hecha"]) == (dia, dia, dia, 1), dict(o)
        assert [p["fecha"][:10] for p in con.execute("SELECT fecha FROM pagos WHERE orden_id=?", (oid,))] == [dia]
        assert (ventas(hoy_s), ventas(dia)) == (v_hoy, v_dia), "no suma ni hoy ni el día viejo"
        assert not registro(oid), "no se copia en el Registro"
        assert con.execute("SELECT COUNT(*) FROM movimientos WHERE concepto LIKE ?", (f"%{o['numero']}%",)).fetchone()[0] == 0, "no entra a Cash flow"
        assert con.execute("SELECT COUNT(*) FROM mov_inventario").fetchone()[0] == inv, "no descuenta inventario"
        assert A.credito_de(con, cid) == 0, "el precio de hoy no deja saldo a favor ni deuda"
        assert o["numero"] in c.get(f"/clientes/{cid}").text, "queda en la ficha"
        reps = con.execute("SELECT * FROM repuestos_prepagados WHERE orden_id=? AND entregado_en IS NULL", (oid,)).fetchall()
        assert len(reps) == 2 and reps[0]["pagado_en"] == dia, [dict(r) for r in reps]
        con.execute("UPDATE repuestos_prepagados SET delivery=5, tipo_entrega='delivery' WHERE id=?", (reps[0]["id"],)); con.commit()
        c.post(f"/prepagados/{reps[0]['id']}/cobrar-delivery", data={"forma": fo, "monto_usd": "5"})
        assert ventas(hoy_s) == round(v_hoy + 5, 2) and ventas(dia) == v_dia, "el delivery de hoy sí es venta de hoy"
        assert [(r["fecha"], r["linea"]) for r in registro(oid)] == [(hoy_s, 5)], "y entra al Registro ese día"
        crear(pid=otro, cant="1", forma="Efectivo USD", monto="500", pedido_viejo="1")
        o = ultima()
        assert (o["estado_pago"], o["monto_contra_entrega"], o["total"]) == ("pagada", 0, 500), "en efectivo también ya entró; pagó más que hoy: sin saldo a favor"
        inv = con.execute("SELECT COUNT(*) FROM mov_inventario").fetchone()[0]
        lid = con.execute("SELECT id FROM orden_lineas WHERE orden_id=?", (o["id"],)).fetchone()[0]
        c.post(f"/ordenes/{o['id']}/editar", data={f"cant_{lid}": "1", f"precio_{lid}": "500", "descuento": "0", "delivery": "0"})
        assert con.execute("SELECT COUNT(*) FROM mov_inventario").fetchone()[0] == inv, "editarlo tampoco descuenta inventario"
        assert A.credito_de(con, cid) == 0
        con.close()


@prueba("Orden anotada tarde: lo pagado cuenta el día de la fecha de pago, no hoy; sale en el Registro ese día y las ventas de hoy no cambian")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy_ = datetime.date.today(); hoy_s = hoy_.isoformat(); dia = (hoy_ - datetime.timedelta(days=3)).isoformat()
        p = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 AND categoria NOT IN ('porche','repuesto','opcion','kit') LIMIT 1").fetchone()["id"]
        con.execute("UPDATE productos SET precio=100 WHERE id=?", (p,))
        rep = con.execute("SELECT id FROM productos WHERE sku='REP-G'").fetchone()["id"]
        cid = con.execute("SELECT MIN(id) FROM clientes").fetchone()[0]
        fo = con.execute("SELECT nombre FROM cuentas WHERE activa=1 AND COALESCE(cobra,1)=1 AND nombre NOT LIKE 'Efectivo%' ORDER BY orden, codigo LIMIT 1 OFFSET 1").fetchone()[0]
        con.commit()
        def crear(fecha, pid=p, monto="100", **extra):
            c.post("/ordenes/nueva", data={"cliente_id": str(cid), "canal": "whatsapp", "producto_id": str(pid), "cantidad": "1", "personalizacion": "", "color": "", "malla": "0",
                   "tipo_entrega": "pickup", "pago_forma": [fo], "pago_monto": [monto], "pago_ref": [""], "fecha_pago": fecha, **extra})
            return con.execute("SELECT * FROM ordenes WHERE cliente_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        ventas = lambda d: A.ventas_por_dia(con, d, d).get(d, (0, 0))[0]
        sesion_de(c, "admin")
        v_hoy, v_dia = ventas(hoy_s), ventas(dia)
        o = crear(dia)
        assert con.execute("SELECT substr(fecha,1,10) FROM pagos WHERE orden_id=?", (o["id"],)).fetchone()[0] == dia
        assert (ventas(hoy_s), ventas(dia)) == (v_hoy, round(v_dia + 100, 2)), "cuenta su día, no hoy"
        assert [r["fecha"] for r in A._historial_rows(con, "", "", "") if r.get("oid") == o["id"]] == [dia], "y en el Registro también"
        o = crear(hoy_s)
        assert con.execute("SELECT substr(fecha,1,10) FROM pagos WHERE orden_id=?", (o["id"],)).fetchone()[0] == hoy_s
        assert ventas(hoy_s) == round(v_hoy + 100, 2), "la de hoy sigue contando hoy"
        precio = con.execute("SELECT precio FROM productos WHERE id=?", (rep,)).fetchone()[0]
        o = crear(dia, pid=rep, monto=str(precio), prepagar="1")
        assert con.execute("SELECT pagado_en FROM repuestos_prepagados WHERE orden_id=?", (o["id"],)).fetchone()[0] == dia
        con.close()


@prueba("Ficha del despachador: 'Llevando ahora' es solo lo que ya salió; un pedido pendiente para otro día o un pack sin nada que llevar no salen")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy_ = datetime.date.today(); manana = (hoy_ + datetime.timedelta(days=2)).isoformat()
        d = con.execute("SELECT id, nombre FROM despachadores LIMIT 1").fetchone()
        cid = con.execute("SELECT MIN(id) FROM clientes").fetchone()[0]
        p = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 LIMIT 1").fetchone()["id"]
        pk = con.execute("SELECT id FROM productos WHERE sku LIKE 'PACK%' LIMIT 1").fetchone()["id"]
        def orden(num, estado, fecha, prod):
            oid = con.execute("INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,total,tipo_entrega,despachador,fecha_prometida) VALUES (?,?,?,?,?,?,?,?,?)",
                              (num, cid, "whatsapp", estado, "pagada", 10, "delivery", d["nombre"], fecha)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'x',1,10,10)", (oid, prod))
            return oid
        orden("#77001", "en_ruta", hoy_.isoformat(), p)
        orden("#77002", "pendiente", manana, p)
        o3 = orden("#77003", "en_ruta", hoy_.isoformat(), pk)
        con.execute("INSERT INTO packs (cliente_id,orden_id,producto_id,unidades,entregadas_inicio,estado) VALUES (?,?,?,3,0,'activo')", (cid, o3, pk))
        con.commit(); sesion_de(c, "admin")
        h = c.get(f"/despachadores/{d['id']}").text
        hoy_h = h[h.index("Lo que le toca hoy"):h.index("<!--fin-hoy-->")]
        assert "Llevando ahora" not in h, "lo de hoy en ruta ya se ve en su parada: no se repite abajo"
        assert ">en ruta</span>" in hoy_h, "la de hoy que ya salió dice 'en ruta' en su parada"
        assert "#77002" not in h.split("Lo que ha hecho")[0] and "#77003" not in h.split("Lo que ha hecho")[0]
        con.execute("UPDATE ordenes SET fecha_prometida=? WHERE numero='#77001'", ((hoy_ - datetime.timedelta(days=1)).isoformat(),))
        con.execute("UPDATE ordenes SET estado='en_ruta', fecha_prometida=? WHERE numero='#77002'", (manana,)); con.commit()
        h = c.get(f"/despachadores/{d['id']}").text
        h = h[h.index("Llevando ahora"):h.index("<!--fin-llevando-->")]
        assert "#77002" in h and "#77003" not in h, "en la calle y no es de hoy: sale en 'Llevando ahora'"
        con.close()


@prueba("Ficha del despachador: los viajes a la agencia por llevar salen en 'Lo que le toca hoy' y en su mensaje con los datos completos para la agencia; el de otro día y el ya llevado no")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy_ = datetime.date.today(); hoy_s = hoy_.isoformat(); despues = (hoy_ + datetime.timedelta(days=2)).isoformat()
        d = con.execute("SELECT id, nombre FROM despachadores LIMIT 1").fetchone()
        cid = con.execute("INSERT INTO clientes (nombre_pila,nombre,telefono,cedula,correo) VALUES ('Paquete','Paquete Prueba','04141234567','V-99887766','paquete@prueba.test')").lastrowid
        p = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 LIMIT 1").fetchone()["id"]
        def viaje(agencia, fecha, num, llevado=None):
            v = con.execute("INSERT INTO viajes_agencia (fecha,despachador,agencia,monto,pedidos,llevado_en) VALUES (?,?,?,5,1,?)", (fecha, d["nombre"], agencia, llevado)).lastrowid
            o = con.execute("INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,total,tipo_entrega,ciudad,viaje_id) VALUES (?,?,'whatsapp','pendiente','pagada',10,'nacional','Maracay',?)", (num, cid, v)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Comedero',1,10,10)", (o, p))
        con.execute("UPDATE despachadores SET telefono='04141112233' WHERE id=?", (d["id"],))
        viaje("AgenciaHoy", hoy_s, "#78001"); viaje("AgenciaDespues", despues, "#78002"); viaje("AgenciaLista", hoy_s, "#78003", hoy_s + " 10:00")
        con.commit(); sesion_de(c, "admin")
        h = c.get(f"/despachadores/{d['id']}").text
        h = h[h.index("Lo que le toca hoy"):h.index("<!--fin-hoy-->")]
        assert "AgenciaHoy" in h and "Mandárselo por WhatsApp" in h, h[:500]
        assert "AgenciaDespues" not in h and "AgenciaLista" not in h
        assert "Llevar a AgenciaHoy" in h and "Paquete Prueba" in h and "Maracay" in h
        # Cristina, 12 nov: copiado para la agencia, con todos los datos y su emoji, para que no se equivoquen
        assert "🪪 C.I. V-99887766" in h and "📧 paquete@prueba.test" in h and "👤 Paquete Prueba" in h, "la cédula y el correo van, como los pide la agencia"
        con.close()


@prueba("Envío nacional: va la cédula de quien retira en la agencia; si es un tercero, nunca la del cliente; una entrega normal no lleva cédula")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid = con.execute("INSERT INTO clientes (nombre_pila,nombre,telefono,cedula,correo) VALUES ('Vero','Vero Prueba','04141234567','V-22222222','vero@prueba.test')").lastrowid
        v = con.execute("INSERT INTO viajes_agencia (fecha,despachador,agencia,monto,pedidos) VALUES (date('now'),'Ingrid','Zoom',5,3)").lastrowid
        def orden(num, tipo="nacional", recibe=None, ced=None, correo=None):
            return con.execute("""INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,total,tipo_entrega,direccion,ciudad,viaje_id,receptor_nombre,receptor_cedula,receptor_correo)
                                  VALUES (?,?,'whatsapp','pendiente','pagada',10,?,'Oficina','Maracay',?,?,?,?)""", (num, cid, tipo, v if tipo == "nacional" else None, recibe, ced, correo)).lastrowid
        tercero = orden("#79001", recibe="Desiree", ced="V-11111111", correo="desiree@prueba.test")
        sin_ced = orden("#79002", recibe="Otra Persona")
        cliente = orden("#79003"); normal = orden("#79004", tipo="delivery")
        con.commit()
        res = lambda oid: A.resumen_despacho(A.cargar_orden(con, oid))
        assert "C.I. V-11111111" in res(tercero) and "V-22222222" not in res(tercero)
        assert "Falta la cédula" in res(sin_ced) and "V-22222222" not in res(sin_ced), "un tercero sin cédula no toma la del cliente"
        assert "C.I. V-22222222" in res(cliente), "si retira el cliente, la suya"
        assert "C.I." not in res(normal) and "Falta la cédula" not in res(normal)
        p = {o["numero"]: o for o in A.viajes_por_llevar(con, "Ingrid")[0]["paquetes"]}
        assert (p["#79001"]["cedula"], p["#79001"]["correo"]) == ("V-11111111", "desiree@prueba.test")
        assert (p["#79002"]["cedula"], p["#79002"]["correo"]) == (None, None), "faltan, y la pantalla lo avisa"
        assert (p["#79003"]["cedula"], p["#79003"]["correo"]) == ("V-22222222", "vero@prueba.test")
        con.close()


@prueba("Entrega de pack por envío nacional: pide agencia y destino, sale igual que un pedido en Operaciones y al entregarla se limpia")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat()
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,ciudad) VALUES ('Rita Prueba','Rita','Mérida')").lastrowid
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,agencia,ciudad) VALUES ('#PK7',?,'entregada','pagada',72,0,'nacional','MRW','Mérida · MRW Centro')", (cid,)).lastrowid
        pid = con.execute("INSERT INTO packs (cliente_id,orden_id,unidades,entregadas_inicio,tamano) VALUES (?,?,3,1,'Mediano')", (cid, oid)).lastrowid
        con.commit(); sesion_de(c, "admin")
        h = c.get(f"/clientes/{cid}").text
        assert 'name="destino"' in h and 'value="Mérida · MRW Centro"' in h, "propone el destino de la compra"
        assert 'name="destino"' in c.get("/packs").text
        c.post(f"/packs/{pid}/programar", data={"fecha": hoy, "tipo_entrega": "nacional", "retiro": "1", "agencia": "Tealca", "destino": "Tealca Mérida Norte"}, follow_redirects=False)
        k = con.execute("SELECT agencia_programada, direccion_programada FROM packs WHERE id=?", (pid,)).fetchone()
        assert k[:] == ("Tealca", "Tealca Mérida Norte"), k[:]
        h = c.get("/operaciones").text
        assert "Tealca Mérida Norte" in h and "#PK7" in h and "1× Repuesto Mediano" in h
        assert f'action="/packs/{pid}/campo"' in h and 'name="agencia"' in h, "la agencia se cambia desde la fila"
        c.post(f"/packs/{pid}/campo", data={"agencia": "Zoom", "volver": "/operaciones"}, follow_redirects=False)
        assert con.execute("SELECT agencia_programada FROM packs WHERE id=?", (pid,)).fetchone()[0] == "Zoom"
        sesion_de(c, "taller"); c.post(f"/packs/{pid}/campo", data={"agencia": "MRW"}, follow_redirects=False)
        assert con.execute("SELECT agencia_programada FROM packs WHERE id=?", (pid,)).fetchone()[0] == "Zoom", "el taller no coordina"
        A.entregar_pack(con, pid, None, hoy, "1", "", "", "0", "", ""); con.commit()
        k = con.execute("SELECT agencia_programada, direccion_programada FROM packs WHERE id=?", (pid,)).fetchone()
        assert k[:] == (None, None), "la próxima entrega se pregunta de nuevo"
        con.execute("UPDATE packs SET fecha_programada=?, retiro_programado=1 WHERE id=?", (hoy, pid)); con.commit()   # programado desde Seguimientos: solo la fecha
        sesion_de(c, "admin"); h = c.get("/operaciones").text
        assert "Mérida · MRW Centro" in h and "<option selected>MRW</option>" in h, "va a donde fue la compra"
        con.close()


@prueba("Repuesto de pack por envío nacional: entra a 'Llevar a la agencia' junto con los pedidos; 'Ya los llevé' lo deja entregado sin pagar delivery y el siguiente queda aparte")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat()
        d = con.execute("SELECT id, nombre FROM despachadores WHERE nombre=?", (A.DESPACHADORES[0],)).fetchone()
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,cedula,telefono,correo) VALUES ('Rita Prueba','Rita','V-111','0414','rita@prueba.test')").lastrowid
        oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,agencia,ciudad,receptor_nombre,receptor_cedula)
                             VALUES ('#PK8',?,'entregada','pagada',72,0,'nacional','MRW','Mérida · MRW Centro','Tercero Prueba','V-222')""", (cid,)).lastrowid
        pid = con.execute("INSERT INTO packs (cliente_id,orden_id,unidades,entregadas_inicio,tamano) VALUES (?,?,3,0,'Mediano')", (cid, oid)).lastrowid
        otra = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,agencia) VALUES ('#OT1',?,'pendiente','pagada',10,0,'nacional','Tealca')", (cid,)).lastrowid
        pr = con.execute("SELECT id, nombre, precio FROM productos WHERE sku NOT LIKE 'PACK%' AND COALESCE(tipo,'producto')!='opcion' LIMIT 1").fetchone()
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,?,1,?,0,?)", (otra, pr["id"], pr["nombre"], pr["precio"], pr["precio"]))
        con.commit(); sesion_de(c, "admin")
        por_llevar = lambda: int((re.search(r"(\d+) (?:orden|órdenes) por llevar a la agencia", c.get("/").text) or [0, 0])[1])
        antes = por_llevar()
        c.post(f"/packs/{pid}/programar", data={"fecha": hoy, "tipo_entrega": "nacional", "retiro": "1", "agencia": "Tealca", "destino": "Tealca Mérida Norte"}, follow_redirects=False)
        assert A.packs_por_llevar(con)[0]["pack_id"] == pid
        h = c.get("/operaciones?tipo=nacional").text
        assert f'name="pack_id" value="{pid}"' in h and f'name="orden_id" value="{otra}"' in h
        assert por_llevar() == antes + 1, "Inicio cuenta el repuesto"
        datos = {"despachador": d["nombre"], "agencia": "Tealca", "oficina": list(A.oficinas_agencia(con).get("Tealca") or {"": 0})[0], "monto": "10", "fecha": hoy, "orden_id": str(otra), "pack_id": str(pid), "volver": "/operaciones"}
        c.post("/viajes/crear", data=datos, follow_redirects=False)
        v = con.execute("SELECT id, pedidos, monto FROM viajes_agencia ORDER BY id DESC LIMIT 1").fetchone()
        assert (v["pedidos"], v["monto"]) == (2, 10), "un solo viaje con los dos"
        assert con.execute("SELECT viaje_id FROM packs WHERE id=?", (pid,)).fetchone()[0] == v["id"] and not A.packs_por_llevar(con)
        p = {o["numero"]: o for o in A.viajes_por_llevar(con, d["nombre"])[0]["paquetes"]}
        assert p["#PK8"]["recibe"] == "Tercero Prueba" and p["#PK8"]["cedula"] == "V-222" and p["#PK8"]["direccion"] == "Tealca Mérida Norte", p["#PK8"]
        assert "1× Repuesto Mediano" in p["#PK8"]["lleva"] and "1/3" in p["#PK8"]["lleva"]
        t = A.texto_ruta([], datetime.date.today(), A.viajes_por_llevar(con, d["nombre"]))
        assert "📍 Tealca Mérida Norte" in t and "👤 Tercero Prueba" in t and "🪪 C.I. V-222" in t and "\n\n👤 " in t, "el mensaje lleva los datos para la agencia, con espacio entre clientes"
        assert "🪪 C.I. V-222" in c.get(f"/despachadores/{d['id']}").text and "Copiar datos" in c.get(f"/despachadores/{d['id']}").text
        assert [g["agencia"] or g["cosas"] for g in A.salidas_de_hoy(con, pack_id=pid)] == ["1× Repuesto Mediano"], "solo ese repuesto, nada más del día"
        A.montar_uno(None, con, "pack", pid); con.commit()   # el taller lo ve cuando el despachador le da Montar
        assert [r[0] or r[1] for r in con.execute("SELECT agencia, cosas FROM cargas_taller")] == ["1× Repuesto Mediano"]
        assert "Repuesto Mediano" in c.get("/taller").text
        assert "Tealca Mérida Norte" in c.get(f"/despachadores/{d['id']}").text
        sesion_de(c, "despachador"); h = c.get("/mis-entregas").text
        assert "Tealca Mérida Norte" in h and "1× Repuesto Mediano" in h and "V-222" in h, "datos de quien retira"
        sesion_de(c, "admin")
        ficha = c.get(f"/clientes/{cid}").text
        assert 'name="retira_nombre" value="Tercero Prueba"' in ficha and 'name="retira_cedula" value="V-222"' in ficha, "propone quien retiró la compra"
        esta = {"fecha": hoy, "tipo_entrega": "nacional", "retiro": "1", "agencia": "Tealca", "destino": "Tealca Valencia", "retira_pregunta": "1"}
        c.post(f"/packs/{pid}/programar", data=esta | {"retira_nombre": "Otra Gente", "retira_cedula": "V-333", "retira_telefono": "0412", "retira_correo": "otra@prueba.test"}, follow_redirects=False)
        p = {o["numero"]: o for o in A.viajes_por_llevar(con, d["nombre"])[0]["paquetes"]}["#PK8"]
        assert (p["recibe"], p["cedula"], p["recibe_tel"], p["correo"], p["direccion"]) == ("Otra Gente", "V-333", "0412", "otra@prueba.test", "Tealca Valencia"), p
        assert con.execute("SELECT viaje_id FROM packs WHERE id=?", (pid,)).fetchone()[0] == v["id"], "cambiar los datos no lo saca del viaje"
        c.post(f"/packs/{pid}/programar", data=esta | {"retira_nombre": "", "retira_cedula": "V-333"}, follow_redirects=False)
        p = {o["numero"]: o for o in A.viajes_por_llevar(con, d["nombre"])[0]["paquetes"]}["#PK8"]
        assert (p["recibe"], p["cedula"], p["correo"]) == (None, "V-111", "rita@prueba.test"), "vacío = retira el cliente, con sus datos"
        c.post(f"/viajes/{v['id']}/borrar", data={"volver": "/operaciones"}, follow_redirects=False)
        assert con.execute("SELECT viaje_id FROM packs WHERE id=?", (pid,)).fetchone()[0] is None and A.packs_por_llevar(con), "al deshacer vuelve a la lista"
        c.post("/viajes/crear", data=datos, follow_redirects=False)
        v = con.execute("SELECT id FROM viajes_agencia ORDER BY id DESC LIMIT 1").fetchone()
        c.post(f"/viajes/{v['id']}/llevado", data={"volver": "/operaciones"}, follow_redirects=False)
        e = con.execute("SELECT tipo_entrega, despachador FROM entregas_repuesto WHERE pack_id=?", (pid,)).fetchall()
        assert [tuple(x) for x in e] == [("nacional", d["nombre"])], "queda entregado"
        assert con.execute("SELECT fecha_programada, viaje_id, agencia_programada, retira_nombre FROM packs WHERE id=?", (pid,)).fetchone()[:] == (None, None, None, None)
        assert con.execute("SELECT COUNT(*) FROM viajes_despachador WHERE pack_id=?", (pid,)).fetchone()[0] == 0, "se le paga el viaje, no un delivery"
        assert con.execute("SELECT estado FROM ordenes WHERE id=?", (otra,)).fetchone()[0] == "entregada"
        con.close()


@prueba("Entrega de pack por envío nacional sin oficina: Operaciones ofrece 'Cargar oficina' y al guardarla sale el destino; el taller no puede")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat()
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,ciudad) VALUES ('Rita Prueba','Rita','Mérida')").lastrowid
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel) VALUES ('#PK9',?,'entregada','pagada',72,0)", (cid,)).lastrowid
        pid = con.execute("INSERT INTO packs (cliente_id,orden_id,unidades,entregadas_inicio,tamano,fecha_programada,tipo_programado,retiro_programado) VALUES (?,?,3,1,'Mediano',?,'nacional',1)", (cid, oid, hoy)).lastrowid
        con.commit(); sesion_de(c, "taller")
        c.post(f"/packs/{pid}/campo", data={"destino": "Tealca Mérida"}, follow_redirects=False)
        assert con.execute("SELECT direccion_programada FROM packs WHERE id=?", (pid,)).fetchone()[0] is None, "el taller no coordina"
        sesion_de(c, "logistica"); h = c.get("/operaciones?tipo=nacional").text
        assert "Cargar oficina" in h
        c.post(f"/packs/{pid}/campo", data={"destino": "Tealca Mérida Norte", "volver": "/operaciones"}, follow_redirects=False)
        h = c.get("/operaciones?tipo=nacional").text
        assert "Tealca Mérida Norte" in h and "Cargar oficina" not in h
        # la compra solo dice la ciudad: esta entrega sigue sin oficina, aunque la fila muestre 'Mérida'
        con.execute("UPDATE ordenes SET tipo_entrega='nacional', agencia='Tealca', ciudad='Mérida' WHERE id=?", (oid,))
        con.execute("UPDATE packs SET direccion_programada=NULL, tipo_programado=NULL WHERE id=?", (pid,)); con.commit()
        h = c.get("/operaciones?tipo=nacional").text
        assert "Cargar oficina" in h and 'name="destino" required value="Mérida"' in h
        con.close()


@prueba("Pack que no se lleva ninguno hoy: sale de Operaciones, queda en Packs con los 3 por entregar y el delivery pagado no se pierde; si cambia de idea no cuenta doble")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat()
        sesion_de(c, "logistica")
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Lili Prueba','Lili')").lastrowid
        pr = con.execute("SELECT id FROM productos WHERE sku LIKE 'PACK%' OR nombre LIKE 'Pack%' LIMIT 1").fetchone()[0]
        oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,delivery,tipo_entrega,despachador,origen_excel)
                             VALUES ('#PK8',?,'pendiente','pagada',90,7,'delivery','Cristina',0)""", (cid,)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Pack 3 Repuestos Grande',1,76,76)", (oid, pr))
        con.execute("INSERT INTO orden_lineas (orden_id,nombre,cantidad,precio,costo,total,extra_en) VALUES (?,'Delivery prepagado',2,7,0,14,?)", (oid, hoy))
        con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (?,'Zelle',90,90,'USD',?,'confirmado')", (oid, hoy))
        kid = con.execute("""INSERT INTO packs (cliente_id,orden_id,producto_id,unidades,entregadas_inicio,estado,creado_en,deliveries_prepagados,tarifa_prepagada)
                             VALUES (?,?,?,3,1,'activo',?,2,7)""", (cid, oid, pr, hoy)).lastrowid
        con.commit()
        assert "Lili Prueba" in c.get("/operaciones?cola=todo").text
        c.post(f"/ordenes/{oid}/pack/{kid}/hoy", data={"cuantos": "0"}, follow_redirects=False)
        assert "Lili Prueba" not in c.get("/operaciones?cola=todo").text, "ya no es una entrega"
        k = con.execute("SELECT * FROM packs WHERE id=?", (kid,)).fetchone()
        assert k["deliveries_prepagados"] == 3 and k["fecha_programada"] is None, "los 3 deliveries pagados quedan para los 3 repuestos"
        assert con.execute("SELECT despachador FROM ordenes WHERE id=?", (oid,)).fetchone()[0] is None
        assert "Lili Prueba" in c.get("/packs").text
        c.post(f"/ordenes/{oid}/pack/{kid}/hoy", data={"cuantos": "1"}, follow_redirects=False)
        assert con.execute("SELECT deliveries_prepagados FROM packs WHERE id=?", (kid,)).fetchone()[0] == 2
        assert "Lili Prueba" in c.get("/operaciones?cola=todo").text
        con.close()


@prueba("Mensajes de cumpleaños: el 10% nombra lo que el cliente no tiene; si lo tiene todo, solo el saludo; el regalo queda claro que es de ellos")
def _():
    ms = A.mensajes_cumple("Naya", ["Comedor", "Rampa"])
    assert len(ms) == 4 and len(set(ms)) == 4
    assert all("Naya" in m and "💚" in m and "un comedor o una rampa" in m and "10% de descuento" in m for m in ms)
    assert all(("le quieren regalar" in m) or ("quieren regalarle" in m) for m in ms)
    ms = A.mensajes_cumple("Naya", ["Rampa"])
    assert all("una rampa" in m and "comedor" not in m for m in ms), "solo lo que le falta"
    assert all("un comedor" in m for m in A.mensajes_cumple("Naya", ["2 comedores"]))
    ms = A.mensajes_cumple("Naya", [])
    assert all("10%" not in m and "regal" not in m for m in ms), "ya lo tiene todo: solo el saludo"
    assert A.mensajes_cumple("Nicolás martini González", [])[0].startswith("¡Feliz cumple a Nicolás!")


@prueba("Operaciones: el WhatsApp de una entrega de hoy abre con el mensaje que toca: si debe, recordar el pago; delivery, la hora en blanco; pick-up, a qué hora viene")
def _():
    from urllib.parse import unquote_plus
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat(); man = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        pr = con.execute("SELECT id FROM productos WHERE COALESCE(tipo,'producto')='producto' LIMIT 1").fetchone()[0]
        def orden(nombre, tel, te, ep, total, pagado, fecha=None):
            cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES (?,?,?)", (nombre, nombre.split()[0], tel)).lastrowid
            oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,tipo_entrega,despachador,fecha_prometida,origen_excel)
                                 VALUES (?,?,'pendiente',?,?,?,?,?,0)""", (f"#W{cid}", cid, ep, total, te, "Cristina" if te != "pickup" else None, fecha)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Rampa',1,?,?)", (oid, pr, total, total))
            if pagado: con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,monto_real,moneda,fecha,estado) VALUES (?,'Zelle',?,?,'USD',?,'confirmado')", (oid, pagado, pagado, hoy))
        orden("Marta Debe", "04141110001", "delivery", "abonada", 50, 21)
        orden("Pedro Pagado", "04141110002", "delivery", "pagada", 50, 50)
        orden("Luisa Retira", "04141110003", "pickup", "pagada", 50, 50)
        orden("Tomas Manana", "04141110004", "delivery", "pagada", 50, 50, man)
        con.commit(); sesion_de(c, "logistica")
        h = unquote_plus(html.unescape(c.get("/operaciones?cola=todo").text))
        assert "¡Hoooola Marta! Recuerda pagar tu saldo pendiente de $29.00 para el pedido de hoy 💚" in h
        assert "¡Hoooola Pedro! Tu pedido llega hoy entre __ y __ 💚" in h
        assert "¡Hoooola Luisa! ¿Más o menos a qué hora vienes? 💚" in h
        assert "Tomas" in h and "¡Hoooola Tomas" not in h, "otro día: el chat abre vacío"
        con.close()


@prueba("Envío nacional: el WhatsApp de la orden abre con el saludo para mandar la guía, sin agencia ni número (los pone quien escribe), también ya entregada")
def _():
    from urllib.parse import unquote_plus
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Nora Lejos','Nora','04141110009')").lastrowid
        oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,tipo_entrega,agencia,guia,origen_excel)
                             VALUES ('#G1',?,'entregada','pagada',50,'nacional','Zoom','123456789',0)""", (cid,)).lastrowid
        con.commit(); sesion_de(c, "logistica")
        h = unquote_plus(html.unescape(c.get(f"/ordenes/{oid}/panel").text))
        msj = "¡Hoooola Nora! Aquí te envío tu guía para que le hagas seguimiento. Cuando te llegue, mándanos una foto 📸 y síguenos en Instagram @_decopet 💚"
        assert msj in h and msj + " " not in h
        con.execute("UPDATE ordenes SET tipo_entrega='delivery' WHERE id=?", (oid,)); con.commit()
        assert "Hoooola" not in unquote_plus(html.unescape(c.get(f"/ordenes/{oid}/panel").text)), "solo envío nacional"
        con.close()


@prueba("Cobrar 'Debe' en Operaciones: la casilla 'Registrar en Cash flow' es solo de Cristina y anota el cobro en su caja (pedido y delivery de pack)")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT OR REPLACE INTO config (clave, valor) VALUES ('ventas_auto','0')"); con.commit(); A.cargar_formas_pago()
        fo = con.execute("SELECT nombre FROM cuentas WHERE activa=1 AND COALESCE(cobra,1)=1 AND nombre NOT LIKE 'Efectivo%' ORDER BY orden, codigo LIMIT 1").fetchone()[0]
        pr = con.execute("SELECT id FROM productos WHERE COALESCE(tipo,'producto')='producto' LIMIT 1").fetchone()[0]
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Carmen Debe','Carmen','04141110005')").lastrowid
        oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,tipo_entrega,despachador,origen_excel)
                             VALUES ('#CF1',?,'pendiente','sin_pago',30,'delivery','Cristina',0)""", (cid,)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Rampa',1,30,30)", (oid, pr))
        kid = con.execute("""INSERT INTO packs (cliente_id,orden_id,producto_id,unidades,entregadas_inicio,estado,fecha_programada,tipo_programado,delivery_programado)
                             VALUES (?,?,?,3,1,'activo',date('now','localtime'),'delivery',5)""", (cid, oid, pr)).lastrowid
        con.commit()
        libro = lambda: con.execute("SELECT m.* FROM movimientos m WHERE m.categoria='Ventas'").fetchall()
        sesion_de(c, "admin")
        assert 'name="en_cashflow"' in c.get("/operaciones?cola=todo").text
        c.post(f"/ordenes/{oid}/cobrar", data={"forma": fo, "monto_usd": "10", "volver": "/operaciones"}, follow_redirects=False)
        assert not libro(), "sin marcar no entra nada"
        c.post(f"/ordenes/{oid}/cobrar", data={"forma": fo, "monto_usd": "20", "en_cashflow": "1", "volver": "/operaciones"}, follow_redirects=False)
        ms = libro(); assert len(ms) == 1 and ms[0]["monto_usd"] == 20 and "#CF1" in ms[0]["concepto"], [dict(m) for m in ms]
        assert con.execute("SELECT COUNT(*) FROM pagos WHERE orden_id=? AND en_cashflow=1", (oid,)).fetchone()[0] == 1
        c.post(f"/packs/{kid}/cobrar-delivery", data={"forma": fo, "monto_usd": "5", "en_cashflow": "1", "volver": "/operaciones"}, follow_redirects=False)
        assert len(libro()) == 2 and libro()[-1]["monto_usd"] == 5
        sesion_de(c, "logistica")
        assert 'name="en_cashflow"' not in c.get("/operaciones?cola=todo").text, "logística no la ve"
        con.close()


@prueba("Envío nacional a domicilio: se marca al crear y al editar, y sale como '🚪 A DOMICILIO' con la dirección en la orden, Operaciones y el viaje a la agencia")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        pr = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 AND categoria NOT IN ('porche','repuesto','opcion','kit') LIMIT 1").fetchone()["id"]
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono,correo,cedula) VALUES ('Dani Puerta','Dani','04145550001','d@x.com','V-1234567')").lastrowid
        con.commit(); sesion_de(c, "admin")
        def crear(**extra):
            c.post("/ordenes/nueva", data={"cliente_id": str(cid), "canal": "whatsapp", "producto_id": str(pr), "cantidad": "1", "personalizacion": "", "color": "", "malla": "0",
                   "tipo_entrega": "nacional", "agencia": "Tealca", "oficina_agencia": "Av Ravell con calle Carabobo · Barcelona", "pago_forma": [""], "pago_monto": [""], "pago_ref": [""], **extra})
            return con.execute("SELECT * FROM ordenes WHERE cliente_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        o = crear()
        assert o["a_domicilio"] == 0, "lo normal es la oficina de la agencia"
        o = crear(a_domicilio="1")
        assert o["a_domicilio"] == 1 and o["direccion"].startswith("Av Ravell")
        assert "🚪 A domicilio" in c.get(f"/ordenes/{o['id']}/panel").text
        assert "🚪 A domicilio" in c.get("/operaciones?cola=todo").text
        vid = con.execute("INSERT INTO viajes_agencia (fecha,despachador,agencia,monto,pedidos,usuario_id) VALUES (date('now','localtime'),'Cristina','Tealca',5,1,1)").lastrowid
        con.execute("UPDATE ordenes SET viaje_id=? WHERE id=?", (vid, o["id"])); con.commit()
        paq = [p for v in A.viajes_por_llevar(con, "Cristina") for p in v["paquetes"]]
        assert any(p["direccion"] == "🚪 A DOMICILIO: Av Ravell con calle Carabobo · Barcelona" for p in paq), [p["direccion"] for p in paq]
        # editar: se quita y vuelve a la oficina
        c.post(f"/ordenes/{o['id']}/entrega", data={"tipo_entrega": "nacional", "agencia": "Tealca", "ciudad_nac": "Barcelona", "direccion": "Av Ravell con calle Carabobo · Barcelona"}, follow_redirects=False)
        assert con.execute("SELECT a_domicilio FROM ordenes WHERE id=?", (o["id"],)).fetchone()[0] == 0
        c.post(f"/ordenes/{o['id']}/entrega", data={"tipo_entrega": "nacional", "agencia": "Tealca", "ciudad_nac": "Barcelona", "a_domicilio": "1", "direccion_dom": "Calle 5, casa 12"}, follow_redirects=False)
        r = con.execute("SELECT a_domicilio, direccion FROM ordenes WHERE id=?", (o["id"],)).fetchone()
        assert (r[0], r[1]) == (1, "Calle 5, casa 12")
        con.close()


@prueba("Exportar órdenes baja solo lo filtrado: el mes, el estado y el producto elegidos; el miembro PRO sale con su coronita")
def _():
    import io, openpyxl
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Exporta Mes','Exporta','04145550099')").lastrowid
        for num, cre, est in (("#90001", "2026-10-20 10:00:00", "entregada"), ("#90002", "2026-11-03 10:00:00", "entregada"), ("#90003", "2026-11-04 10:00:00", "cancelada")):
            con.execute("INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,creado_en,subtotal,total) VALUES (?,?,'whatsapp',?,'pagada',?,10,10)", (num, cid, est, cre))
        con.commit(); sesion_de(c, "admin")
        def numeros(url):
            r = c.get(url); assert r.status_code == 200, r.status_code
            ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
            return {str(v) for row in ws.iter_rows(values_only=True) for v in row if str(v).startswith("#9000")}
        assert numeros("/ordenes/exportar") >= {"#90001", "#90002", "#90003"}, "sin filtro baja todo"
        assert numeros("/ordenes/exportar?mes=2026-11") == {"#90002", "#90003"}
        assert numeros("/ordenes/exportar?mes=2026-11&estado=entregada") == {"#90002"}
        h = c.get("/ordenes?mes=2026-11&estado=entregada").text
        assert "Noviembre 2026" in h and "/ordenes/exportar?estado=entregada&amp;mes=2026-11" in h, "el botón lleva los filtros puestos"
        pro = con.execute("SELECT id FROM productos WHERE nombre LIKE 'El Porche Versión PRO%' ORDER BY id LIMIT 1").fetchone()["id"]
        oid = con.execute("SELECT id FROM ordenes WHERE numero='#90002'").fetchone()["id"]
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio) VALUES (?,?,'El Porche Versión PRO',1,10)", (oid, pro)); con.commit()
        assert numeros(f"/ordenes/exportar?mes=2026-11&producto={pro}") == {"#90002"}, "por producto"
        assert numeros("/ordenes/exportar?mes=2026-11&producto=c:porche") == {"#90002"}, "por categoría"
        assert numeros("/ordenes/exportar?mes=2026-11&producto=c:rampa") == set()
        assert "Todos los porches" in h
        assert 'class="corona"' not in h
        con.execute("UPDATE clientes SET porche_version='PRO' WHERE id=?", (cid,)); con.commit()
        assert 'class="corona"' in c.get("/ordenes?mes=2026-11&estado=entregada").text, "el miembro PRO lleva su coronita en Órdenes"
        con.close()


@prueba("Analítica por ciudad: solo el nombre de la ciudad, nunca la dirección de la oficina de la agencia")
def _():
    f = A.ciudad_de_texto
    assert f("Agencia Tealca, CC Los Chaguaramos, Barcelona") == "Barcelona"
    assert f("Agencia MRW 20080 esquina calle 12 de Barrio Obrero. San Cristóbal Edo Táchira.") == "San Cristóbal"
    assert f("Zoom Puerto Ordaz, Centro Comercial Santo Tome III, Los Olivos") == "Puerto Ordaz"
    assert f("merida") == "Mérida" and f("Ocumare del Tuy") == "Ocumare del Tuy"
    assert f("Av Venezuela entre calles 39 y 40 No 39-55 Código postal 3001") is None
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono,ciudad) VALUES ('Ciu Dad','Ciu','04145550123','Valencia')").lastrowid
        for n, ciu in (("#90011", "Tealca CC Gran Bazar, Maracaibo, Zulia"), ("#90012", "Av Venezuela entre calles 39 y 40 No 39-55")):
            con.execute("INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,creado_en,subtotal,total,tipo_entrega,ciudad) VALUES (?,?,'whatsapp','entregada','pagada','2026-03-03 10:00:00',10,10,'nacional',?)", (n, cid, ciu))
        con.commit()
        # un mes sin otras órdenes de la base de prueba: en el mes en curso hay más ciudades y la lista muestra solo 8
        ciudades = {x["k"] for x in A.resumen_mes(con, A.datetime.date(2026, 3, 1))["ciudades"]}
        assert {"Maracaibo", "Valencia"} <= ciudades and not any("," in x for x in ciudades), ciudades
        con.close()


@prueba("Nueva orden con cliente nuevo: se anota cómo nos conoció y, si fue recomendación, quién lo trajo; sale en Analítica")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        pr = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 AND categoria NOT IN ('porche','repuesto','opcion','kit') LIMIT 1").fetchone()["id"]
        rid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Quien Recomienda','Quien','04145550777')").lastrowid
        con.commit(); sesion_de(c, "admin")
        assert "¿Cómo llegó?" in c.get("/ordenes/nueva/panel").text and "Publicidad (anuncio)" in c.get("/ordenes/nueva/panel").text
        c.post("/ordenes/nueva", data={"cliente_nombre_pila": "Nueva", "cliente_apellido": "Referida", "cliente_telefono": "04145550778", "cliente_correo": "n@x.com",
               "cliente_origen": "Recomendación de otro cliente", "cliente_referido_id": str(rid), "canal": "whatsapp", "producto_id": str(pr), "cantidad": "1",
               "personalizacion": "", "color": "", "malla": "0", "tipo_entrega": "pickup", "pago_forma": [""], "pago_monto": [""], "pago_ref": [""]})
        cl = con.execute("SELECT origen, referido_id FROM clientes WHERE telefono LIKE '%5550778'").fetchone()
        assert (cl["origen"], cl["referido_id"]) == ("Recomendación de otro cliente", rid), dict(cl)
        r = A.resumen_mes(con, A.datetime.date.today().replace(day=1))
        assert ("Recomendación de otro cliente", 1) in r["como_llegaron"], r["como_llegaron"]
        # también desde Clientes → Nuevo cliente
        assert "¿Cómo llegó?" in c.get("/clientes/nuevo/panel").text
        c.post("/clientes/nuevo", data={"nombre_pila": "Desde", "apellido": "Clientes", "telefono": "04145550779", "correo": "d@c.com", "origen": "Publicidad (anuncio)"}, follow_redirects=False)
        assert con.execute("SELECT origen FROM clientes WHERE telefono LIKE '%5550779'").fetchone()[0] == "Publicidad (anuncio)"
        con.close()


@prueba("Ficha del cliente con porche: repuesto cada 30 días o menos sale en verde con 👍; más de 40 sale en rojo; más de 40 días sin comprar marca 'Recuperar' en Último pedido")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        pro = con.execute("SELECT id FROM productos WHERE sku LIKE 'PRO-%' LIMIT 1").fetchone()["id"]
        rep = con.execute("SELECT id FROM productos WHERE categoria='repuesto' AND sku LIKE 'REP-%' LIMIT 1").fetchone()["id"]
        hoy = A.datetime.date.today()
        def cliente(tel, dias):
            cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES (?,?,?)", ("Ritmo " + tel, "Ritmo", tel)).lastrowid
            for i, (d, prod) in enumerate([(dias[0] + 5, pro)] + [(x, rep) for x in dias]):
                f = (hoy - A.datetime.timedelta(days=d)).isoformat() + " 10:00:00"
                oid = con.execute("INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,creado_en,subtotal,total) VALUES (?,?,'whatsapp','entregada','pagada',?,10,10)", (f"#{tel[-4:]}{i}", cid, f)).lastrowid
                con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio) VALUES (?,?,'x',1,10)", (oid, prod))
            con.commit(); return cid
        sesion_de(c, "admin")
        bien = c.get(f"/clientes/{cliente('04145551001', [75, 50, 25, 3])}").text
        assert "ritmo-bien" in bien and "Recuperar" not in bien
        mal = c.get(f"/clientes/{cliente('04145551002', [200, 140, 80])}").text
        assert "ritmo-mal" in mal and "Recuperar ↗" in mal and "https://api.whatsapp.com/send?phone=584145551002&amp;text=" in mal   # el botón abre su WhatsApp con el mensaje
        assert "15%25%20de%20descuento" in mal   # 80 días sin repuesto: el mensaje ofrece 15% en el próximo
        assert "sin%20cambiar%20el%20repuesto.%20" in mal   # sin mascota guardada: sin 'de ...'
        # buen ritmo pero hace más de 40 días que no compra: el rojo va en 'Último pedido', no en su ritmo
        atr = c.get(f"/clientes/{cliente('04145551003', [120, 100, 80, 60])}").text
        assert "ritmo-bien" in atr and atr.count("Recuperar") == 1
        assert "15%25%20de%20descuento" not in atr   # justo 60 días: todavía sin descuento (es a partir de más de 60)
        con.close()


@prueba("Ficha del cliente · Qué ha comprado: muestra las fechas de cada compra (×2 si ese día llevó dos), no un total que confunde")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        rep = con.execute("SELECT id FROM productos WHERE categoria='repuesto' AND activo=1 AND tipo='producto' LIMIT 1").fetchone()["id"]
        hoy = A.datetime.date.today()
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Fechas X','Fechas','04145552001')").lastrowid
        for i, (d, n) in enumerate([(0, 2), (20, 1), (40, 1), (60, 1)]):
            f = (hoy - A.datetime.timedelta(days=d)).isoformat() + " 10:00:00"
            oid = con.execute("INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,creado_en,subtotal,total) VALUES (?,?,'whatsapp','entregada','pagada',?,10,10)", (f"#F{i}", cid, f)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio) VALUES (?,?,'x',?,10)", (oid, rep, n))
        con.commit(); sesion_de(c, "admin")
        h = c.get(f"/clientes/{cid}").text
        f20 = (hoy - A.datetime.timedelta(days=20)).strftime("%d/%m"); f60 = (hoy - A.datetime.timedelta(days=60)).strftime("%d/%m")
        assert "hoy ×2, " + f20 in h and "…" in h and f60 not in h.split("Qué ha comprado")[1].split("</div></div>")[0]
        assert "5×" not in h and "última vez" not in h
        con.close()


@prueba("Ficha del cliente · Qué ha comprado: el pack activo dice cuántos repuestos le quedan ('quedan 1 de 3'); el pack completo no")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        pk = con.execute("SELECT id FROM productos WHERE nombre LIKE 'Pack 3%' AND activo=1 AND tipo='producto' LIMIT 1").fetchone()["id"]
        def cliente(tel, inicio):
            cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES (?,?,?)", ("Pack " + tel, "Pack", tel)).lastrowid
            oid = con.execute("INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,subtotal,total) VALUES (?,?,'whatsapp','entregada','pagada',10,10)", ("#P" + tel[-3:], cid)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio) VALUES (?,?,'x',1,10)", (oid, pk))
            con.execute("INSERT INTO packs (cliente_id,orden_id,producto_id,unidades,entregadas_inicio) VALUES (?,?,?,3,?)", (cid, oid, pk, inicio))
            con.commit(); return cid
        sesion_de(c, "admin")
        assert "quedan 2 de 3" in c.get(f"/clientes/{cliente('04145553001', 1)}").text
        assert "quedan-pack" not in c.get(f"/clientes/{cliente('04145553002', 3)}").text
        con.close()


@prueba("Ficha del cliente · Cómo paga: 'Pago Móvil' y 'Pago Móvil VES' salen juntos en una sola barra")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB)
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Paga X','Paga','04145554001')").lastrowid
        for i, f in enumerate(["Pago Móvil", "Pago Móvil", "Pago Móvil VES"]):
            oid = con.execute("INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,subtotal,total) VALUES (?,?,'whatsapp','entregada','pagada',10,10)", (f"#M{i}", cid)).lastrowid
            con.execute("INSERT INTO pagos (orden_id,forma,monto_usd,fecha) VALUES (?,?,10,date('now'))", (oid, f))
        con.commit(); con.close(); sesion_de(c, "admin")
        h = c.get(f"/clientes/{cid}").text.split("Cómo paga")[1].split("</div></div>")[0]
        assert "Pago Móvil VES" not in h and h.count("Pago Móvil") == 1 and "<em>3</em>" in h


@prueba("Seguimientos · Contactados: no mezcla los cumpleaños felicitados con los seguimientos de repuesto")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB)
        a = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Repuesto Contactado','R','04145555001')").lastrowid
        b = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Cumple Felicitado','C','04145555002')").lastrowid
        con.execute("INSERT INTO seguimientos (cliente_id,tipo,clave,resultado) VALUES (?,'repuesto','seg:x','enviado')", (a,))
        con.execute("INSERT INTO seguimientos (cliente_id,tipo,clave,resultado) VALUES (?,'cumple','cumple_hoy:x','felicitado')", (b,))
        con.commit(); con.close(); sesion_de(c, "admin")
        h = c.get("/seguimientos?ver=hechos").text
        assert "Repuesto Contactado" in h and "Cumple Felicitado" not in h


@prueba("Clientes · Datos pendientes: a los de envío nacional no se les pide dirección (se les manda por agencia); a los de Caracas sí")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB)
        def cli(nombre, tel, estado, te):
            cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono,correo,ciudad,estado) VALUES (?,?,?,'x@x.com','X',?)", (nombre, nombre, tel, estado)).lastrowid
            con.execute("INSERT INTO mascotas (cliente_id,nombre,raza) VALUES (?,'Toby','Poodle')", (cid,))
            if te: con.execute("INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,tipo_entrega,subtotal,total) VALUES (?,?,'whatsapp','entregada','pagada',?,10,10)", ("#N" + tel[-3:], cid, te))
        cli("Nacional Por Agencia", "04145556001", "Zulia", "nacional")
        cli("Caracas Sin Direccion", "04145556002", "Distrito Capital", "delivery")
        cli("Zulia Sin Pedidos", "04145556003", "Zulia", None)
        con.commit(); con.close(); sesion_de(c, "admin")
        h = c.get("/clientes?ver=pendientes&falta=direccion").text
        assert "Caracas Sin Direccion" in h and "Nacional Por Agencia" not in h and "Zulia Sin Pedidos" not in h


@prueba("Migración 023: archiva (no borra) la oficina guardada como dirección de clientes solo de envío nacional fuera de Caracas y Miranda")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB)
        def cli(nombre, tel, estado, tipos, dire, ciudad):
            cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono,estado) VALUES (?,?,?,?)", (nombre, nombre, tel, estado)).lastrowid
            for i, te in enumerate(tipos):
                con.execute("INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,tipo_entrega,subtotal,total) VALUES (?,?,'whatsapp','entregada','pagada',?,10,10)", (f"#A{tel[-3:]}{i}", cid, te))
            return con.execute("INSERT INTO direcciones (cliente_id,etiqueta,direccion,ciudad,principal) VALUES (?,'Principal',?,?,1)", (cid, dire, ciudad)).lastrowid
        oficina = cli("Solo Nacional", "04145557001", "Zulia", ["nacional", "nacional"], "Tealca · Bella Vista", "Maracaibo")
        mixto = cli("Nacional Y Delivery", "04145557002", "Zulia", ["nacional", "delivery"], "Casa en Caracas", "Caracas")
        miranda = cli("Nacional Miranda", "04145557003", "Miranda", ["nacional"], "Charallave calle 3", "Charallave")
        caracas = cli("Nacional Caracas", "04145557004", "Zulia", ["nacional"], "Los Chaguaramos", "Caracas")
        con.execute("DELETE FROM migraciones WHERE nombre='023_archivar_oficinas_envio_nacional.sql'"); con.commit()
        A.aplicar_migraciones(con)
        quedan = {r[0] for r in con.execute("SELECT id FROM direcciones")}
        arch = {r[0]: r[1] for r in con.execute("SELECT id, direccion FROM direcciones_archivadas")}
        assert oficina not in quedan and arch.get(oficina) == "Tealca · Bella Vista"
        assert {mixto, miranda, caracas} <= quedan and not ({mixto, miranda, caracas} & set(arch))
        con.close()


@prueba("Saldo a favor a la vista: chip en la ficha del cliente y monto junto al nombre en la lista de clientes")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB)
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Con Saldo','Con','04145558001')").lastrowid
        sin = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Sin Saldo','Sin','04145558002')").lastrowid
        con.execute("INSERT INTO credito_cliente (cliente_id,fecha,monto,motivo) VALUES (?,date('now'),15,'prueba')", (cid,))
        con.commit(); con.close(); sesion_de(c, "admin")
        assert "chip-saldo" in c.get(f"/clientes/{cid}").text and "chip-saldo" not in c.get(f"/clientes/{sin}").text
        h = c.get("/clientes?q=Saldo").text
        assert 'title="Saldo a favor $15.00"' in h


@prueba("Cliente nuevo con solo el GPS: Inicio avisa que falta la dirección escrita y se escribe ahí mismo")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        c.post("/clientes/nuevo", data={"nombre_pila": "Solo Gps", "telefono": "04140000101", "maps": "https://maps.app.goo.gl/abc"})
        c.post("/clientes/nuevo", data={"nombre_pila": "Con Todo", "telefono": "04140000102", "direccion": "Res. Los Pinos", "maps": "https://maps.app.goo.gl/xyz"})
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid, did = con.execute("SELECT c.id, d.id FROM clientes c JOIN direcciones d ON d.cliente_id=c.id WHERE c.nombre='Solo Gps'").fetchone()
        con.close()
        h = c.get("/inicio").text
        assert "Solo Gps: tiene el GPS pero falta la dirección escrita" in h and "Con Todo: tiene el GPS" not in h
        sesion_de(c, "logistica")
        assert "Solo Gps: tiene el GPS pero falta" in c.get("/inicio").text
        r = c.post(f"/clientes/{cid}/direccion", data={"id": did, "etiqueta": "Principal", "maps": "https://maps.app.goo.gl/abc", "direccion": "Torre B, apto 4", "volver": "/inicio"}, follow_redirects=False)
        assert r.headers["location"] == "/inicio"
        con = sqlite3.connect(A.DB)
        assert con.execute("SELECT direccion, maps FROM direcciones WHERE id=?", (did,)).fetchone() == ("Torre B, apto 4", "https://maps.app.goo.gl/abc")
        con.close()
        assert "Solo Gps: tiene el GPS" not in c.get("/inicio").text



@prueba("Cliente viejo con '(solo GPS)': sale en Inicio solo si tiene un delivery por entregar")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        con = sqlite3.connect(A.DB)
        for i, n in ((901, "Paola Vieja"), (902, "Otra Vieja")):
            con.execute("INSERT INTO clientes (id,nombre,nombre_pila,creado_en) VALUES (?,?,?,'2025-01-01')", (i, n, n))
            con.execute("INSERT INTO direcciones (cliente_id,etiqueta,direccion,maps,principal) VALUES (?,'Principal','(solo GPS)','https://maps.app.goo.gl/p',1)", (i,))
        con.execute("INSERT INTO ordenes (numero,cliente_id,estado,tipo_entrega,total) VALUES ('#9901',901,'pendiente','delivery',24)")
        con.execute("INSERT INTO ordenes (numero,cliente_id,estado,tipo_entrega,total) VALUES ('#9902',902,'entregada','delivery',24)")
        con.commit(); con.close()
        h = c.get("/inicio").text
        assert "Paola Vieja: tiene el GPS pero falta la dirección escrita" in h and "Otra Vieja: tiene el GPS" not in h


@prueba("Ficha del cliente: 'sin cédula' sale solo si su pedido es envío nacional, no por la ciudad (La Guaira va por delivery)")
def _():
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        con = sqlite3.connect(A.DB)
        a = con.execute("INSERT INTO clientes (nombre,nombre_pila,ciudad,estado) VALUES ('Aurelio Guaira','Aurelio','La Guaira','La Guaira')").lastrowid
        b = con.execute("INSERT INTO clientes (nombre,nombre_pila,ciudad,estado) VALUES ('Eudys Tigre','Eudys','El Tigre','Anzoátegui')").lastrowid
        con.execute("INSERT INTO ordenes (numero,cliente_id,estado,tipo_entrega,total) VALUES ('#C1',?,'pendiente','delivery_fuera',100)", (a,))
        con.execute("INSERT INTO ordenes (numero,cliente_id,estado,tipo_entrega,total) VALUES ('#C2',?,'pendiente','nacional',100)", (b,))
        con.commit(); con.close()
        assert "necesaria para envío nacional" not in c.get(f"/clientes/{a}").text
        assert "necesaria para envío nacional" in c.get(f"/clientes/{b}").text


@prueba("Ficha del despachador: las paradas de hoy dicen 'por montar' / 'en ruta'; sin nada en la calle ni por pagar esas tarjetas no salen; el período filtra lo que ha hecho")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy_ = datetime.date.today(); yo = A.DESPACHADORES[0]
        did = con.execute("SELECT id FROM despachadores WHERE nombre=?", (yo,)).fetchone()[0]
        con.execute("UPDATE ordenes SET despachador=NULL WHERE despachador=?", (yo,)); con.execute("DELETE FROM viajes_agencia WHERE despachador=?", (yo,))
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Parada Prueba','Parada')").lastrowid
        p = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 LIMIT 1").fetchone()["id"]
        def orden(num, estado, fecha):
            o = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,despachador,fecha_prometida,fecha_entrega)
                               VALUES (?,?,?,'pagada',10,0,'delivery',?,?,?)""", (num, cid, estado, yo, fecha, fecha)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'x',1,10,10)", (o, p)); return o
        o1 = orden("#P1", "pendiente", hoy_.isoformat())
        con.execute("UPDATE ordenes SET despachador_pagado=1 WHERE despachador=?", (yo,))
        viejo = orden("#P0", "entregada", (hoy_ - datetime.timedelta(days=70)).isoformat())
        con.execute("UPDATE ordenes SET despachador_pagado=1 WHERE id=?", (viejo,)); con.commit()
        sesion_de(c, "admin"); h = c.get(f"/despachadores/{did}").text
        assert "Llevando ahora" not in h and ">Por pagar</h3>" not in h, "vacías: no salen"
        assert "no tiene efectivo tuyo" not in h
        con.execute("INSERT INTO cargas_taller (despachador, cosas, orden_id) VALUES (?,?,?)", (yo, "1× x", o1)); con.commit()
        h = c.get(f"/despachadores/{did}").text
        hoy_h = h[h.index("Lo que le toca hoy"):h.index("<!--fin-hoy-->")]
        assert ">por montar</span>" in hoy_h and "1 parada" in hoy_h
        assert "#P0" in h and "#P0" not in c.get(f"/despachadores/{did}?periodo=semana").text, "esta semana no trae lo de hace 70 días"
        assert "#P1" in c.get(f"/despachadores/{did}?periodo=semana").text
        con.execute("UPDATE ordenes SET estado='en_ruta' WHERE id=?", (o1,)); con.commit()
        h = c.get(f"/despachadores/{did}").text
        assert ">en ruta</span>" in h[h.index("Lo que le toca hoy"):h.index("<!--fin-hoy-->")] and "Llevando ahora" not in h
        con.close()


@prueba("Operaciones: el cobro del delivery anotado como línea del pedido no sale en Productos; solo lo que se lleva")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB)
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Cristy Linea','Cristy')").lastrowid
        p = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 LIMIT 1").fetchone()[0]
        o = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,fecha_prometida)
                           VALUES ('#9301',?,'pendiente','pagada',113,0,'delivery',date('now','localtime'))""", (cid,)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Porche Visible',1,108,108)", (o, p))
        con.execute("INSERT INTO orden_lineas (orden_id,nombre,cantidad,precio,total,extra_en) VALUES (?,'Delivery',1,5,5,date('now'))", (o,))
        con.commit(); con.close()
        sesion_de(c, "admin"); h = c.get("/operaciones?cola=todo").text
        fila = h[h.index("Cristy Linea"):][:3000]
        assert "Porche Visible" in fila and "1× Delivery" not in fila


@prueba("Operaciones: 'Quitar entrega' en un retiro de pack programado por error lo deja sin programar y sale de la lista; un delivery ya cobrado no se pierde")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy_ = datetime.date.today().isoformat()
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Veronica Packerror','Veronica')").lastrowid
        pk = con.execute("SELECT id FROM productos WHERE sku LIKE 'PACK%' LIMIT 1").fetchone()["id"]
        o = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega) VALUES ('#8345',?,'entregada','pagada',72,0,'delivery')", (cid,)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Pack',1,72,72)", (o, pk))
        def pack(pagado):
            return con.execute("""INSERT INTO packs (cliente_id,orden_id,producto_id,unidades,entregadas_inicio,estado,fecha_programada,tipo_programado,despachador_programado,delivery_programado,delivery_pagado,retiro_programado)
                                  VALUES (?,?,?,3,2,'activo',?,'delivery','Ingrid',5,?,1)""", (cid, o, pk, hoy_, pagado)).lastrowid
        k1 = pack(0); con.commit()
        sesion_de(c, "admin")
        assert "Veronica Packerror" in c.get("/operaciones?cola=todo").text
        c.post(f"/packs/{k1}/desprogramar", data={"volver": "/operaciones"})
        r = con.execute("SELECT * FROM packs WHERE id=?", (k1,)).fetchone()
        assert r["fecha_programada"] is None and r["despachador_programado"] is None and r["delivery_programado"] is None and r["estado"] == "activo"
        assert "Veronica Packerror" not in c.get("/operaciones?cola=todo").text
        con.execute("DELETE FROM packs WHERE id=?", (k1,)); k2 = pack(1); con.commit()
        c.post(f"/packs/{k2}/desprogramar", data={"volver": "/operaciones"})
        r = con.execute("SELECT * FROM packs WHERE id=?", (k2,)).fetchone()
        assert r["fecha_programada"] is None and r["delivery_pagado"] == 1 and r["delivery_programado"] == 5, "lo ya cobrado queda anotado"
        sesion_de(c, "taller"); c.post(f"/packs/{k2}/desprogramar")
        con.close()


@prueba("Buscar sin acentos: 'veronica gomez' encuentra a 'Verónica Gómez' en Clientes, Órdenes y Mascotas")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB)
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,apellido,telefono) VALUES ('Verónica Gómez','Verónica','Gómez','0414-3981273')").lastrowid
        con.execute("INSERT INTO mascotas (cliente_id,nombre,raza) VALUES (?,'Ñoño','Poodle')", (cid,))
        con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel) VALUES ('#9555',?,'entregada','pagada',24,0)", (cid,))
        con.commit(); con.close()
        sesion_de(c, "admin")
        for q in ("veronica gomez", "VERONICA", "gómez"):
            assert "Verónica Gómez" in c.get("/clientes", params={"q": q}).text, q
        assert "#9555" in c.get("/ordenes", params={"q": "veronica gomez", "estado": "todas"}).text
        assert "Ñoño" in c.get("/mascotas", params={"q": "nono"}).text


@prueba("Entrega de pack: en Operaciones se anota que recibe otra persona; le sale al despachador con su teléfono y se puede volver a 'recibe el cliente'")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy_ = datetime.date.today().isoformat(); yo = A.DESPACHADORES[0]
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Paola Avilarecibe','Paola','0414-1111111')").lastrowid
        pk = con.execute("SELECT id FROM productos WHERE sku LIKE 'PACK%' LIMIT 1").fetchone()["id"]
        o = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega) VALUES ('#8802',?,'entregada','pagada',72,0,'delivery')", (cid,)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Pack',1,72,72)", (o, pk))
        k = con.execute("""INSERT INTO packs (cliente_id,orden_id,producto_id,unidades,entregadas_inicio,estado,fecha_programada,tipo_programado,despachador_programado,delivery_programado,delivery_pagado,retiro_programado)
                           VALUES (?,?,?,3,1,'activo',?,'delivery',?,5,1,1)""", (cid, o, pk, hoy_, yo)).lastrowid
        con.commit()
        sesion_de(c, "admin")
        h = c.get(f"/ordenes/{o}/panel").text
        assert "1ª entrega del pack" in h and "Próxima entrega · repuesto 2 de 3" in h and f"<b>Despachador</b><span>{yo}</span>" in h, \
            "en el detalle se distingue la 1ª entrega del próximo repuesto y quién lo lleva (Cristina, 12 nov)"
        h = c.get("/operaciones?cola=todo").text
        assert 'class="pk-recibe"' in h and f'id="dlg-pk-{k}"' in h, "un iconito en la pastilla del pack abre '¿Quién recibe?'"
        assert f"abrirPanel('/ordenes/{o}/panel')" in h and f'href="/clientes/{cid}" onclick="event.stopPropagation()"' in h, \
            "tocar el retiro abre el pedido; tocar el nombre, la ficha del cliente (Cristina, 12 nov)"
        c.post(f"/packs/{k}/campo", data={"recibe": "1", "recibe_nombre": "Luis Tercero", "recibe_telefono": "04147778899", "volver": "/operaciones"})
        h = c.get("/operaciones?cola=todo").text
        assert 'value="Luis Tercero"' in h and 'value="0414-7778899"' in h and 'class="pk-recibe si"' in h, "se ve al tocar el iconito, no en la fila"
        assert "Recibe <b>" not in h and "Cambiar quién recibe" not in h, "la fila no se recarga de texto (Cristina, 12 nov)"
        f = next(f for f in A.ruta_despachador(con, yo, hoy_) if f.get("rid") == k)
        assert f["recibe"] == "Luis Tercero" and f["recibe_tel"] == "0414-7778899"
        assert "Recibe: Luis Tercero" in A.texto_ruta([f], datetime.date.today())
        sesion_de(c, "despachador")
        c.post(f"/mis-entregas/retiro/pack/{k}/incidencia", data={"tipo": "No estaba", "descripcion": "no abrió nadie"})
        assert tuple(con.execute("SELECT orden_id, tipo, responsable FROM incidencias WHERE descripcion='no abrió nadie'").fetchone()) == (o, "No estaba", yo), \
            "'Hubo un problema' también en un retiro de pack: queda en su orden"
        assert c.get("/mis-entregas").text.count(f"/mis-entregas/retiro/pack/{k}/incidencia") == 1
        sesion_de(c, "admin")
        c.post(f"/packs/{k}/campo", data={"recibe": "1", "recibe_nombre": "", "volver": "/operaciones"})
        assert tuple(con.execute("SELECT retira_nombre, retira_telefono FROM packs WHERE id=?", (k,)).fetchone()) == (None, None)
        # un pedido normal de delivery también tiene el iconito, por si el cliente cambia de planes
        o2 = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,despachador,fecha_prometida) VALUES ('#8803',?,'pendiente','pagada',60,0,'delivery',?,?)", (cid, yo, hoy_)).lastrowid
        o3 = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,fecha_prometida) VALUES ('#8804',?,'pendiente','pagada',60,0,'pickup',?)", (cid, hoy_)).lastrowid
        pr = con.execute("SELECT id FROM productos WHERE tipo='producto' LIMIT 1").fetchone()["id"]
        for x in (o2, o3): con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Comedero',1,60,60)", (x, pr))
        con.commit()
        h = c.get("/operaciones?cola=todo").text
        assert f'id="dlg-rc-{o2}"' in h and f'action="/ordenes/{o2}/recibe"' in h, "cualquier delivery tiene el iconito de quién recibe"
        assert h.count(f'href="/clientes/{cid}" onclick="event.stopPropagation()"') >= 3, "en todas las filas el nombre lleva a la ficha, no solo en los packs"
        assert f'id="dlg-rc-{o3}"' not in h, "un pick-up no"
        c.post(f"/ordenes/{o2}/recibe", data={"recibe_nombre": "Ana Vecina", "recibe_telefono": "04241234567", "volver": "/operaciones"})
        assert tuple(con.execute("SELECT receptor_nombre, receptor_telefono FROM ordenes WHERE id=?", (o2,)).fetchone()) == ("Ana Vecina", "0424-1234567")
        assert any(f.get("recibe") == "Ana Vecina" for f in A.ruta_despachador(con, yo, hoy_)), "le sale al despachador"
        c.post(f"/ordenes/{o2}/recibe", data={"recibe_nombre": "", "volver": "/operaciones"})
        assert tuple(con.execute("SELECT receptor_nombre, receptor_telefono FROM ordenes WHERE id=?", (o2,)).fetchone()) == (None, None)
        sesion_de(c, "taller")
        c.post(f"/ordenes/{o2}/recibe", data={"recibe_nombre": "Intruso"})
        assert con.execute("SELECT receptor_nombre FROM ordenes WHERE id=?", (o2,)).fetchone()[0] is None, "el taller no lo cambia"
        con.close()


@prueba("Mis entregas: Hoy y Mañana se distinguen a simple vista; lo de mañana va en su propia caja, sin la palabra 'hoy'")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        yo = A.DESPACHADORES[0]; man = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Francisco Mañanero','Francisco','0414-3325001')").lastrowid
        pr = con.execute("SELECT id FROM productos WHERE tipo='producto' LIMIT 1").fetchone()["id"]
        o = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,despachador,fecha_prometida,direccion) VALUES ('#7701',?,'pendiente','pagada',60,0,'delivery',?,?,'Lomas del Campo')", (cid, yo, man)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Comedero',1,60,60)", (o, pr)); con.commit(); con.close()
        sesion_de(c, "despachador"); h = c.get("/mis-entregas").text
        hoy_h, resto = h.split('class="dia-t hoy"')[1].split('class="manana-caja"')
        assert "Francisco" not in hoy_h and "Francisco" in resto.split("Lo que has entregado")[0]
        assert A.fmt_dia(man) in resto and "para hoy" not in resto, "nada que diga 'hoy' en lo de mañana: apurado se lee mal (Cristina, 12 nov)"


@prueba("Adelanto que no alcanza para una entrega: la fila más vieja dice cuánto cubrió y cuánto falta; las filas suman lo mismo que 'te deben'")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT OR IGNORE INTO cuentas (codigo,nombre,moneda,tipo,activa,orden) VALUES ('001','Efectivo USD','USD','operativa',1,1)")
        yo = A.DESPACHADORES[0]; did = con.execute("SELECT id FROM despachadores WHERE nombre=?", (yo,)).fetchone()["id"]
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Rosa Adelanto','Rosa','0414-0000001')").lastrowid
        for n, f in enumerate(("2026-11-07", "2026-11-08", "2026-11-09")):
            con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,despachador,delivery,fecha_entrega,despachador_pagado) VALUES (?,?,'entregada','pagada',60,0,'delivery',?,5,?,0)", (f"#61{n}", cid, yo, f))
        con.commit(); A.cargar_formas_pago()
        sesion_de(c, "admin")
        c.post(f"/despachadores/{did}/adelanto", data={"monto": "3", "forma": "Efectivo USD", "fecha": datetime.date.today().isoformat()})
        r = A.resumen_despachador(con, yo, datetime.date.today())
        assert r["debe"] == 12 and r["adelanto_resta"] == 3, (r["debe"], r["adelanto_resta"])
        h = c.get(f"/despachadores/{did}").text; t = re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", h))
        assert t.count("faltan $2.00") == 1 and 'title="$3.00 ya salieron del adelanto"' in h, "en la ficha de Cristina: la más vieja, $3 cubiertos, faltan $2"
        # al despachador no: con adelantos seguidos confunde (Cristina, 12 nov). Ve la tarifa de cada entrega y 'te deben' arriba
        h = c.get("/mis-entregas", params={"quien": yo}).text; t = re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", h))
        ult = t.split("Últimas entregas")[1].split("Últimos pagos")[0]
        assert "faltan" not in ult and "sin pagar" not in ult and "Tarifa" in ult and ult.count("$5.00") == 3, ult
        assert "$12.00 Te deben" in t
        # un pago saldado entero con el adelanto no sale como "$0.00" (Cristina, 12 nov)
        con.execute("INSERT INTO pagos_despachador (despachador,fecha,monto,entregas,adelanto_usado,nota,confirmado_en) VALUES (?,date('now','localtime'),630,118,630,'Saldado con el adelanto',datetime('now','localtime'))", (yo,)); con.commit()
        pg = c.get("/mis-entregas", params={"quien": yo}).text.split("Últimos pagos")[1]
        assert "con tu adelanto" in pg and "$0.00" not in pg and "ya te lo habían adelantado" not in pg
        con.close()


@prueba("Mis entregas: cuando se le paga todo y queda en cero, ya no le salen las entregas ni los pagos de antes; lo que sigue debiendo o no ha confirmado, sí")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        yo = A.DESPACHADORES[0]
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Corte Prueba','Corte','0414-0000002')").lastrowid
        p1 = con.execute("INSERT INTO pagos_despachador (despachador,fecha,monto,entregas,confirmado_en) VALUES (?,'2026-11-03',5,1,'2026-11-03 18:00')", (yo,)).lastrowid
        con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,despachador,delivery,fecha_entrega,despachador_pagado,despachador_pago_id,zona) VALUES ('#5501',?,'entregada','pagada',60,0,'delivery',?,5,'2026-11-02',1,?,'ZonaVieja')", (cid, yo, p1))
        o2 = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,despachador,delivery,fecha_entrega,despachador_pagado,zona) VALUES ('#5502',?,'entregada','pagada',60,0,'delivery',?,7,'2026-11-05',0,'ZonaNueva')", (cid, yo)).lastrowid
        con.commit()
        sesion_de(c, "despachador"); h = c.get("/mis-entregas").text
        assert "Últimas entregas" in h and "Últimos pagos" in h and ">Tarifa<" in h
        assert "ZonaNueva" in h and "ZonaVieja" not in h, "quedó en cero el 03/11: lo de antes no sale; lo que hizo después, sí"
        assert "$5.00" not in h.split("Últimos pagos")[1], "el pago que lo dejó en cero tampoco"
        # se le paga lo nuevo: mientras no confirme que le llegó, lo ve; al confirmar, queda todo limpio
        p2 = con.execute("INSERT INTO pagos_despachador (despachador,fecha,monto,entregas) VALUES (?,'2026-11-06',7,1)", (yo,)).lastrowid
        con.execute("UPDATE ordenes SET despachador_pagado=1, despachador_pago_id=? WHERE id=?", (p2, o2)); con.commit()
        h = c.get("/mis-entregas").text
        assert "Sí, me llegó" in h and "$7.00" in h.split("Últimos pagos")[1] and "ZonaNueva" in h
        c.post(f"/mis-entregas/pago/{p2}/confirmar")
        h = c.get("/mis-entregas").text
        assert "ZonaNueva" not in h and "No tienes entregas nuevas" in h and "No tienes pagos nuevos" in h
        con.close()


@prueba("La primera entrega de un pack se escribe igual que los retiros: 📦 1/3, no 'Lleva 1 de 3'")
def _():
    h = str(A.tpl.env.filters["lleva"]("1× Pack 3 Repuestos Grande⟪Lleva 1 de 3⟫"))
    assert 'class="pack-tag lleva-pk"' in h and "📦</span>1/3" in h and "Lleva 1 de 3" not in h, h


@prueba("Detalle de un pedido con pack: salen también las entregas ya hechas (2ª…) con su día y despachador; sin el resumen viejo de la 1ª")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Patricia Pack','Patricia','0414-5576141')").lastrowid
        pk = con.execute("SELECT id FROM productos WHERE sku LIKE 'PACK%' LIMIT 1").fetchone()["id"]
        o = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,despachador,fecha_entrega) VALUES ('#9037',?,'entregada','pagada',72,0,'delivery','Cristina','2026-09-16')", (cid,)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Pack 3 Repuestos Mediano',1,72,72)", (o, pk))
        k = con.execute("INSERT INTO packs (cliente_id,orden_id,producto_id,unidades,entregadas_inicio,estado) VALUES (?,?,?,3,1,'activo')", (cid, o, pk)).lastrowid
        con.execute("INSERT INTO entregas_repuesto (pack_id,fecha,tipo_entrega,despachador) VALUES (?,'2026-10-16','delivery','Ingrid')", (k,))
        con.commit(); con.close()
        sesion_de(c, "admin"); h = c.get(f"/ordenes/{o}/panel").text
        assert "2ª entrega · repuesto 2 de 3" in h and "<span>Ingrid</span>" in h and "1ª entrega del pack" in h
        assert "Resumen para el despachador" not in h and "Resumen de la 1ª entrega" not in h, "ya se entregó: el resumen no hace falta"


@prueba("Operaciones: el resumen por despachador es lo de la pestaña (Mañana lo de mañana, Todo todo), con retiros de pack, sin contar entregas, y plegado")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        yo = A.DESPACHADORES[0]; hoy_ = datetime.date.today(); man = (hoy_ + datetime.timedelta(days=1)).isoformat()
        pr = con.execute("SELECT id FROM productos WHERE tipo='producto' LIMIT 1").fetchone()["id"]
        for num, nom, f in (("#7801", "Hoy Resumen", hoy_.isoformat()), ("#7802", "Manana Resumen", man)):
            cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES (?,?,'0414-1110000')", (nom, nom.split()[0])).lastrowid
            o = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,despachador,fecha_prometida) VALUES (?,?,'pendiente','pagada',60,0,'delivery',?,?)", (num, cid, yo, f)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Comedero',1,60,60)", (o, pr))
        # un retiro de pack para mañana (como Verónica y Patricia): también va en el resumen
        pk = con.execute("SELECT id FROM productos WHERE sku LIKE 'PACK%' LIMIT 1").fetchone()["id"]
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Veronica Retiro','Veronica','0414-1110001')").lastrowid
        o = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega) VALUES ('#7803',?,'entregada','pagada',72,0,'delivery')", (cid,)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Pack 3 Repuestos Mediano',1,72,72)", (o, pk))
        con.execute("INSERT INTO packs (cliente_id,orden_id,producto_id,tamano,unidades,entregadas_inicio,estado,fecha_programada,tipo_programado,despachador_programado,direccion_programada) VALUES (?,?,?,'Mediano',3,1,'activo',?,'delivery',?,'Los Palos Grandes')", (cid, o, pk, man, yo))
        con.commit(); con.close()
        sesion_de(c, "admin")
        res = lambda cola: c.get("/operaciones", params={"cola": cola}).text.split('id="resumenes"')[1].split("</details>")[0]
        assert "#7802" in res("manana") and "#7801" not in res("manana") and A.fmt_dia(man) in res("manana")
        assert "Veronica Retiro" in res("manana") and "📦 2/3" in res("manana") and "Los Palos Grandes" in res("manana"), res("manana")
        assert "#7801" in res("hoy") and "#7802" not in res("hoy") and "Veronica Retiro" not in res("hoy")
        assert all(x in res("todo") for x in ("#7801", "#7802", "Veronica Retiro")), "Todo: todo lo que hay en Todo"
        assert "entregas</span>" not in res("manana") and "entrega</span>" not in res("manana"), "sin el conteo de entregas"
        assert '<details id="resumenes" class="res-desp">' in c.get("/operaciones").text, "plegado: se abre al tocarlo"


@prueba("Mis entregas › Qué lleva: un producto por línea y el pack con su cajita (📦 3/3, no '3/3 del pack')")
def _():
    f = A.tpl.env.filters["lleva_items"]
    h = str(f("1× El Porche Versión PRO Mediano · 1× El Porche Versión PRO Grande"))
    assert h.count('class="ll-it"') == 2 and "Grande</div>" in h, h
    h = str(f("Repuesto Mediano · 3/3 del pack"))
    assert "📦</span>3/3" in h and "del pack" not in h, h
    h = str(f("1× Repuesto Grande · 1/3 · 1× Comedero plato azul"))
    assert "📦</span>1/3" in h and h.count('class="ll-it"') == 2 and 'pl-azul' in h, h
    assert "📦 3/3" in A.texto_ruta([{"quien": "Patricia", "telefono": None, "direccion": None, "maps": None, "que_lleva": "Repuesto Mediano · 3/3 del pack", "cobrar": 0}], datetime.date(2026, 11, 12))


@prueba("Pedidos a proveedores › Editar: el monto sigue a la cantidad (13 → 18 sacos) y hay campo de nota que no se borra")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        pid = con.execute("INSERT INTO produccion (pieza,cantidad,fecha_pedido,responsable,costo,nota,tipo_pedido) VALUES ('Grama',13,date('now','localtime'),'Yovanny Sánchez',39,'llega en la mañana','proveedor')").lastrowid
        con.commit(); con.close()
        sesion_de(c, "admin"); h = c.get("/produccion", params={"tipo": "proveedor", "ver": "todas"}).text
        f = h.split(f'action="/produccion/{pid}/editar"')[1].split("</form>")[0]
        assert 'data-pu="3.0"' in f and "m.dataset.pu*this.value" in f, "al cambiar la cantidad, el monto se recalcula con el precio de cada uno"
        assert 'name="nota" value="llega en la mañana"' in f, "la nota se ve y se puede cambiar al editar"


@prueba("Retiro de un pack: se le pone horario y nota desde el detalle (como a cualquier pedido) y salen en Operaciones y en el resumen")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        yo = A.DESPACHADORES[0]; man = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        pk = con.execute("SELECT id FROM productos WHERE sku LIKE 'PACK%' LIMIT 1").fetchone()["id"]
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Patricia Blanco','Patricia','0414-5576141')").lastrowid
        o = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega) VALUES ('#9037',?,'entregada','pagada',72,0,'delivery')", (cid,)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Pack 3 Repuestos Mediano',1,72,72)", (o, pk))
        k = con.execute("INSERT INTO packs (cliente_id,orden_id,producto_id,tamano,unidades,entregadas_inicio,estado,fecha_programada,tipo_programado,despachador_programado,direccion_programada) VALUES (?,?,?,'Mediano',3,2,'activo',?,'delivery',?,'Lomas de Las Mercedes')", (cid, o, pk, man, yo)).lastrowid
        con.commit(); con.close()
        sesion_de(c, "admin")
        h = c.get(f"/ordenes/{o}/panel").text
        assert f'action="/packs/{k}/campo"' in h and 'name="franja"' in h and 'name="nota"' in h, "el retiro se puede editar desde el detalle"
        r = c.post(f"/packs/{k}/campo", data={"franja": "1 – 4 pm", "nota": "Tocar el timbre de abajo", "volver": "/operaciones?cola=manana"}, follow_redirects=False)
        assert r.headers["location"] == "/operaciones?cola=manana"
        h = c.get(f"/ordenes/{o}/panel").text
        assert "1 – 4 pm" in h and "Tocar el timbre de abajo" in h
        h = c.get("/operaciones", params={"cola": "manana"}).text
        assert "1 – 4 pm" in h and "Tocar el timbre de abajo" in h
        res = h.split('id="resumenes"')[1].split("</details>")[0]
        assert "1 – 4 pm" in res and "Tocar el timbre de abajo" in res, res


@prueba("Detalle de un pedido: el resumen para el despachador va plegado (se abre al tocarlo), no ocupa todo el panel")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Regina Serfaty','Regina','0414-3001102')").lastrowid
        pr = con.execute("SELECT id FROM productos WHERE tipo='producto' LIMIT 1").fetchone()["id"]
        o = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,despachador) VALUES ('#9304',?,'pendiente','pagada',216,0,'delivery','Ingrid')", (cid,)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'El Porche Versión PRO Mediano',1,108,108)", (o, pr))
        con.commit(); con.close()
        sesion_de(c, "admin"); h = c.get(f"/ordenes/{o}/panel").text
        assert '<details class="res-desp res-orden"><summary>Resumen para el despachador</summary>' in h and f'id="res-{o}"' in h


@prueba("Operaciones › Pago: 'Debe $5.00' es texto como 'Pagado' (sin botón con sombra), alineado con la columna")
def _():
    css = open(os.path.join(os.path.dirname(A.__file__), "static", "estilo.css"), encoding="utf-8").read()
    r = css.split(".ops-t .op-pago summary.btn{")[1].split("}")[0]
    assert "padding:0" in r and "background:none" in r and "box-shadow:none" in r, r


@prueba("Seguimientos: 'El perro no se adaptó' se puede marcar en cualquier seguimiento de repuesto, no solo en el primero")
def _():
    for tipo in ("primer_repuesto", "repuesto", "*"):
        assert "no_se_adapto" in A.RESULTADOS_POR_TIPO.get(tipo, A.RESULTADOS_POR_TIPO["*"]), tipo


@prueba("Seguimientos: cuando sale 'Lo quiere hoy', el WhatsApp le recuerda que lo pidió (no el mensaje de ofrecer de la primera vez)")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Cristina Yajuri','Cristina','0414-2168776')").lastrowid
        base = dict(cliente_id=cid, tipo="repuesto", tamano="Mediano", dias=35, intento=1)
        m = A.mensajes_repuesto(con, dict(base, estado="Lo quiere hoy"))
        assert m == ["¡Hola Cristina! 👋 Te escribo por el repuesto que nos pediste para esta semana 🌱 ¿Qué día te lo llevamos? 💚"], m
        m = A.mensajes_repuesto(con, dict(base, estado="Lo quiere hoy", pack=True))
        assert "repuesto que tienes pagado, que nos pediste" in m[0], m
        m = A.mensajes_repuesto(con, dict(base, estado="Pendiente"))
        assert "nos pediste" not in m[0] and "¿Te preparamos" in m[0], "si no lo pidió, sigue el de ofrecer"


@prueba("Seguimiento del Básico: el WhatsApp invita a pasarse a la Versión PRO (el Básico no lleva repuesto), en Inicio y en Seguimientos")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Julio Cesar Morales Novo','Julio Cesar','0424-1699883')").lastrowid
        con.execute("INSERT INTO mascotas (cliente_id,nombre) VALUES (?,'Eugenio')", (cid,)); con.commit()
        m = A.mensajes_repuesto(con, dict(cliente_id=cid, tipo="basico", dias=54, intento=3, estado="3º intento"))
        assert all("Versión PRO" in x for x in m) and not any("¿Te preparamos" in x for x in m), m
        assert m[0].startswith("¡Hola Julio Cesar! 👋 ¿Cómo va Eugenio con el porche Básico?"), m[0]
        con.close()
    src = open(A.__file__, encoding="utf-8").read()
    assert '("primer_repuesto", "repuesto", "prepagado", "basico") and x.get("cliente_id")' in src, "Seguimientos también arma el mensaje del Básico"


@prueba("Despachadores: cada tarjeta dice cuánto lleva hoy y mañana (no las últimas zonas escritas a mano)")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        yo = A.DESPACHADORES[0]; hoy_ = datetime.date.today(); man = (hoy_ + datetime.timedelta(days=1)).isoformat()
        con.execute("UPDATE ordenes SET despachador=NULL WHERE despachador=?", (yo,))
        pr = con.execute("SELECT id FROM productos WHERE tipo='producto' LIMIT 1").fetchone()["id"]
        for num, f in (("#7901", hoy_.isoformat()), ("#7902", hoy_.isoformat()), ("#7903", man)):
            cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Cliente Zona','Cliente','0414-1110000')").lastrowid
            o = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,despachador,fecha_prometida,zona) VALUES (?,?,'pendiente','pagada',60,0,'delivery',?,?,'Contael Las Mercedes')", (num, cid, yo, f)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Comedero',1,60,60)", (o, pr))
        con.commit(); con.close()
        sesion_de(c, "admin"); h = c.get("/despachadores").text
        tarjeta = h.split(f"<b style=\"font-size:17px\">{yo}</b>")[1].split("</a>")[0]
        assert "Hoy · 2" in tarjeta and "Mañana · 1" in tarjeta, tarjeta
        assert "Últimas zonas" not in h and "Contael Las Mercedes" not in h


@prueba("Operaciones: una hora en la franja sale con su reloj (pastilla), para que no pase desapercibida")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        pr = con.execute("SELECT id FROM productos WHERE tipo='producto' LIMIT 1").fetchone()["id"]
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Andrea González','Andrea','0414-1110000')").lastrowid
        o = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,despachador,fecha_prometida,franja) VALUES ('#9303',?,'pendiente','pagada',60,0,'delivery',?,date('now','localtime'),'5pm')", (cid, A.DESPACHADORES[0])).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Comedero',1,60,60)", (o, pr))
        con.commit(); con.close()
        sesion_de(c, "admin"); h = c.get("/operaciones").text
        assert re.search(r'<span class="pack-tag hora-tag"><svg[^>]*>.*?</svg>5pm</span>', h), "la hora va en su pastilla con el reloj"


@prueba("Hoy en el taller: lo de Tealca (Catia) no dice 'embalar', aunque no tenga viaje; otra oficina de Tealca u otra agencia, sí")
def _():
    assert A.no_se_embala("Tealca", "Catia") and A.no_se_embala("tealca", None) and A.no_se_embala("Tealca", "")
    assert not A.no_se_embala("Tealca", "Los Palos Grandes") and not A.no_se_embala("MRW", None) and not A.no_se_embala(None, None)
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        pid = con.execute("INSERT INTO productos (sku,nombre,tipo,precio) VALUES ('BAR9','Bar Prueba','producto',60)").lastrowid
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES ('Envio Nac','Envio','04145559901')").lastrowid
        def orden(num, agencia, oficina, desp):
            vid = con.execute("INSERT INTO viajes_agencia (fecha,despachador,agencia,oficina,monto,pedidos) VALUES (date('now','localtime'),?,?,?,5,1)", (desp, agencia, oficina)).lastrowid
            oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,tipo_entrega,agencia,viaje_id,subtotal,total,origen_excel)
                                 VALUES (?,?,'whatsapp','pendiente','pagada','nacional',?,?,60,60,0)""", (num, cid, agencia, vid)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,cantidad,precio) VALUES (?,?,1,60)", (oid, pid))
        orden("#T1", "Tealca", "Catia", "Ingrid")
        orden("#T2", "Tealca", "Los Palos Grandes", "Cristina")
        orden("#T3", "MRW", None, "Cristina")
        # sin viaje: una asignada directo a un despachador y otra que todavía nadie lleva
        for num, desp in (("#T4", "Juan"), ("#T5", None)):
            oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,tipo_entrega,agencia,despachador,subtotal,total,origen_excel)
                                 VALUES (?,?,'whatsapp','pendiente','pagada','nacional','Tealca',?,60,60,0)""", (num, cid, desp)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,cantidad,precio) VALUES (?,?,1,60)", (oid, pid))
        con.commit(); sesion_de(c, "admin")
        for (oid,) in con.execute("SELECT id FROM ordenes WHERE numero LIKE '#T%'").fetchall(): A.montar_uno(None, con, "orden", oid)
        con.commit()
        con.close(); sesion_de(c, "taller")
        h = c.get("/taller").text
        ingrid = h.split("Montar a Ingrid")[1].split('class="tarjeta"')[0]
        cristina = h.split("Montar a Cristina")[1].split('class="tarjeta"')[0]
        assert "1× Bar Prueba" in ingrid and "embalar" not in ingrid and "Tealca" not in ingrid, ingrid
        assert "2× Bar Prueba" in cristina and "va a la agencia · embalar" in cristina and "Tealca Catia" not in cristina, cristina
        juan = h.split("Montar a Juan")[1].split('class="tarjeta"')[0]
        para = h.split("Embalar para Tealca")[1].split('class="tarjeta"')[0]
        assert "1× Bar Prueba" in juan and "embalar" not in juan, juan
        assert "1× Bar Prueba" in para and "embalar" not in para, para



@prueba("Analítica: gráfica de ventas acumuladas día a día, el mes en curso hasta hoy contra el mes anterior a la misma fecha")
def _():
    import datetime as dt
    falsas = {"2026-11-02": (100, 1), "2026-11-05": (50, 1), "2026-10-03": (80, 1), "2026-10-20": (200, 2)}
    original = A.ventas_por_dia
    A.ventas_por_dia = lambda con, d, h: {k: v for k, v in falsas.items() if d <= k <= h}
    try:
        g = A.grafica_ventas_mes(None, dt.date(2026, 11, 1), dt.date(2026, 10, 1), dt.date(2026, 11, 10))
    finally:
        A.ventas_por_dia = original
    assert len(g["este"]) == 10 and len(g["antes"]) == 31 and g["n"] == 31
    assert g["ultimo"] == {"dia": 10, "v": 0, "acum": 150} and g["antes_mismo_dia"] == 80
    assert g["techo"] == 300 and g["mes_antes"] == "octubre"
    assert g["linea"].startswith("0.00,100.00") and g["linea_antes"].endswith("100.00,6.67")
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        h = c.get("/analitica").text
        assert "Ventas día a día" in h and 'class="an-p-este"' in h
        assert 'class="tarjeta an-mapa"' in h and 'id="dlg-mapa"' in h and "Dónde se vendió" in h   # la vista previa abre el mapa de ese mes en una ventana



@prueba("Mapa de ventas: suma por ciudad y por zona de Caracas (la de Tarifas o el GPS), filtra por producto y solo lo ve la dueña")
def _():
    assert A.zona_ccs("Chacao / Altamira") == "Chacao" and A.zona_ccs("ALTO HATILLO") == "Alto Hatillo" and A.zona_ccs("el hatillo") == "El Hatillo"
    assert A.zona_ccs("La Campina") == "La Campiña" and A.zona_ccs("el peñon") == "El Peñón" and A.zona_ccs("La Guairita") == "La Guairita"   # las que faltaban
    assert all(10.3 < la < 10.6 and -67.1 < lo < -66.7 for la, lo in A.COORD_ZONAS_CCS.values())   # todas caen dentro de Caracas
    assert A.zona_ccs("Sebucan") == "Sebucán" and A.zona_ccs("Zona desconocida") is None and A.zona_ccs(None) is None
    assert A.coords_de_link("https://www.google.com/maps?q=10.4961,-66.8492") == (10.4961, -66.8492) and A.coords_de_link("https://maps.app.goo.gl/abc") is None
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        p1 = con.execute("INSERT INTO productos (sku,nombre,tipo,precio) VALUES ('MAPA1','Rampa Mapa','producto',80)").lastrowid
        p2 = con.execute("INSERT INTO productos (sku,nombre,tipo,precio) VALUES ('MAPA2','Bowl Mapa','producto',20)").lastrowid
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono,ciudad) VALUES ('Mapa Uno','Mapa','04145551201','Caracas')").lastrowid
        def orden(num, ciudad, zona, maps, lineas):
            tot = sum(pr * n for _, n, pr in lineas)
            oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,creado_en,subtotal,total,tipo_entrega,ciudad,zona,maps,origen_excel)
                                 VALUES (?,?,'whatsapp','entregada','pagada','2026-03-05 10:00:00',?,?,'delivery',?,?,?,0)""", (num, cid, tot, tot, ciudad, zona, maps)).lastrowid
            for pid, n, pr in lineas:
                con.execute("INSERT INTO orden_lineas (orden_id,producto_id,cantidad,precio,total) VALUES (?,?,?,?,?)", (oid, pid, n, pr, pr * n))
        orden("#M1", "Caracas", "Chacao / Altamira", None, [(p1, 1, 80)])
        orden("#M2", "Caracas", "Chacao", None, [(p2, 2, 20)])
        orden("#M3", "Caracas", None, "https://www.google.com/maps?q=10.4301,-66.8201", [(p2, 1, 20)])
        orden("#M4", "Caracas", None, None, [(p1, 1, 80)])
        orden("#M5", "Agencia Tealca, CC Las Trinitarias, Barquisimeto", None, None, [(p1, 1, 80)])
        orden("#M6", "Pueblo Raro", None, None, [(p2, 1, 20)])
        con.commit()
        # la zona o el GPS que están en la ficha del cliente (no en la orden) también ubican la orden
        con2 = sqlite3.connect(A.DB); con2.row_factory = sqlite3.Row
        def cliente_con(nombre, tel, direccion, zona, maps):
            k = con2.execute("INSERT INTO clientes (nombre,nombre_pila,telefono,ciudad) VALUES (?,?,?,'Caracas')", (nombre, nombre, tel)).lastrowid
            con2.execute("INSERT INTO direcciones (cliente_id,direccion,zona,maps,principal) VALUES (?,?,?,?,1)", (k, direccion, zona, maps))
            oid = con2.execute("""INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,creado_en,subtotal,total,tipo_entrega,ciudad,origen_excel)
                                  VALUES (?,?,'whatsapp','entregada','pagada','2026-04-05 10:00:00',10,10,'delivery','Caracas',0)""", ("#F" + nombre, k)).lastrowid
            con2.execute("INSERT INTO orden_lineas (orden_id,producto_id,cantidad,precio,total) VALUES (?,?,1,10,10)", (oid, p2))
        cliente_con("Dir", "04145551301", "Calle 3, Los Palos Grandes, edif. Sol", None, None)
        cliente_con("Gps", "04145551302", "Casa azul", None, "https://www.google.com/maps?q=10.4301,-66.8201")
        cliente_con("Corto", "04145551303", "Casa verde", None, "https://maps.app.goo.gl/abc")
        cliente_con("Rara", "04145551304", "Casa roja", "Zona Inventada", None)
        k = con2.execute("INSERT INTO clientes (nombre,nombre_pila,telefono,ciudad) VALUES ('Retira','Retira','04145551305','Caracas')").lastrowid
        oid = con2.execute("""INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,creado_en,subtotal,total,tipo_entrega,ciudad,origen_excel)
                              VALUES ('#FRetira',?,'whatsapp','entregada','pagada','2026-04-06 10:00:00',10,10,'pickup','Caracas',0)""", (k,)).lastrowid
        con2.execute("INSERT INTO orden_lineas (orden_id,producto_id,cantidad,precio,total) VALUES (?,?,1,10,10)", (oid, p2))   # vino a retirar
        con2.commit()
        f = A.datos_mapa(con2, "2026-04-01", "2026-04-30")
        assert [(x["k"], x["n"]) for x in f["zonas"]] == [("Los Palos Grandes", 1)], f["zonas"]
        assert [p["numero"] for p in f["puntos"]] == ["#FGps"], f["puntos"]
        assert {o["numero"]: o["porque"] for o in f["sin_zona"]["ordenes"]} == {"#FCorto": "el GPS es un enlace corto, sin coordenadas",
                                                                                "#FRara": "zona “Zona Inventada” todavía no está en el mapa"}, f["sin_zona"]
        assert "#FRetira" not in str(f) and f["total"] == 40   # lo que vienen a retirar no está en el mapa (es de delivery y envíos)
        con2.close()
        d = A.datos_mapa(con, "2026-03-01", "2026-03-31")
        ciu = {x["k"]: (x["n"], x["m"]) for x in d["ciudades"]}
        assert ciu == {"Caracas": (4, 220), "Barquisimeto": (1, 80)}, ciu
        assert [(x["k"], x["n"], x["m"]) for x in d["zonas"]] == [("Chacao", 2, 120)], d["zonas"]
        assert [(p["ll"], p["m"], p["numero"]) for p in d["puntos"]] == [((10.4301, -66.8201), 20, "#M3")], d["puntos"]
        assert (d["sin_zona"]["n"], d["sin_zona"]["m"], [o["numero"] for o in d["sin_zona"]["ordenes"]]) == (1, 80, ["#M4"]), d["sin_zona"]
        assert d["sin_zona"]["ordenes"][0]["porque"] == "sin dirección ni GPS"
        assert [x["k"] for x in d["sin_ciudad"]] == ["Pueblo Raro"] and d["total"] == 320
        r = A.datos_mapa(con, "2026-03-01", "2026-03-31", p1)
        assert {x["k"]: x["m"] for x in r["ciudades"]} == {"Caracas": 160, "Barquisimeto": 80} and r["total"] == 240
        con.close()
        sesion_de(c, "admin")
        h = c.get("/mapa?mes=2026-03&parcial=1").text   # el pedazo que la ventana de Analítica pide
        assert "Dónde se vendió" in h and "Barquisimeto" in h and 'class="mp-datos"' in h and "<nav>" not in h
        an = c.get("/analitica").text
        assert 'href="/mapa"' not in an and "referrerPolicy: 'strict-origin'" in an   # ya no es sección aparte en el menú
        r = c.get("/mapa?mes=2026-03", follow_redirects=False)   # enlaces viejos: a Analítica con el mapa abierto
        assert r.status_code == 303 and r.headers["location"] == "/analitica?mapa=1&mes=2026-03"
        # sin decir de qué sitio se pide, OpenStreetMap bloquea el mapa ("Access blocked"): el ERP no manda referer por defecto (va arriba, en Analítica)
        assert "no pudo ubicar" in h and "ver cuáles" in h and "<b>#M4</b>" in h and "<b>#M3</b>" in h   # las órdenes sin zona se pueden ver y abrir
        assert "WhatsApp ↗" in h and "wa.me" in h   # y escribirle al cliente para pedirle la zona
        assert c.get("/mapa?mes=todo&parcial=1").status_code == 200 and c.get(f"/mapa?mes=2026-03&producto={p1}&parcial=1").status_code == 200
        for rol in ("logistica", "taller"):
            sesion_de(c, rol)
            r = c.get("/mapa?parcial=1", follow_redirects=False)
            assert r.status_code in (302, 303) and "Dónde se vendió" not in r.text, rol



@prueba("Clientes que se quedan: segmentos, nuevos que vuelven en 60 días, qué compran después, seguimientos, adaptación y qué se pide junto")
def _():
    con = base_limpia()
    pid = {k: con.execute("INSERT INTO productos (sku,nombre,tipo,precio) VALUES (?,?,'producto',?)", (k, n, pr)).lastrowid
           for k, n, pr in (("CPRO", "Porche PRO", 93), ("CREP", "Repuesto", 24), ("CRAM", "Rampa", 80), ("CBOW", "Bowl", 20))}
    num = iter(range(1, 999))
    def cli(nombre, tam=None):
        return con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono,porche_tamano) VALUES (?,?,?,?)", (nombre, nombre, f"0414{next(num):07d}", tam)).lastrowid
    def orden(cid, dia, *prods):
        tot = sum(con.execute("SELECT precio FROM productos WHERE sku=?", (p,)).fetchone()[0] for p in prods)
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,creado_en,subtotal,total) VALUES (?,?,'whatsapp','entregada','pagada',?,?,?)",
                          (f"#CC{next(num)}", cid, dia + " 10:00:00", tot, tot)).lastrowid
        for p in prods:
            pr = con.execute("SELECT precio FROM productos WHERE sku=?", (p,)).fetchone()[0]
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,(SELECT nombre FROM productos WHERE sku=?),1,?,?)", (oid, pid[p], p, pr, pr))
    ana, beto, caro, dani, eva = cli("Ana", "Mediano"), cli("Beto", "Grande"), cli("Caro"), cli("Dani"), cli("Eva")
    orden(ana, "2026-09-01", "CPRO"); orden(ana, "2026-10-01", "CREP"); orden(ana, "2026-11-01", "CREP")
    orden(beto, "2026-09-05", "CRAM"); orden(beto, "2026-09-20", "CPRO", "CBOW")
    orden(caro, "2026-05-01", "CPRO", "CBOW")
    orden(dani, "2026-11-05", "CREP")
    for dia in ("2026-08-01", "2026-08-31", "2026-09-30", "2026-10-30"): orden(eva, dia, "CREP")
    for cid, tipo, res, inten, hecho in ((ana, "primer_repuesto", "compro", 1, "2026-10-01"), (beto, "primer_repuesto", "no_se_adapto", 2, "2026-10-20"),
                                         (caro, "primer_repuesto", "mensaje", 1, "2026-06-01"), (dani, "cumple", "felicitado", 1, "2026-11-01")):
        con.execute("INSERT INTO seguimientos (cliente_id,tipo,clave,resultado,intentos,hecho_en) VALUES (?,?,?,?,?,?)", (cid, tipo, f"{tipo}-{cid}", res, inten, hecho))
    con.execute("INSERT INTO mascotas (cliente_id,nombre,fecha_nacimiento) VALUES (?,'Toby','2026-03-01')", (ana,))
    con.execute("INSERT INTO mascotas (cliente_id,nombre,fecha_nacimiento) VALUES (?,'Rex','2015-01-01')", (beto,))
    con.commit()
    d = A.crecimiento_clientes(con, hoy=datetime.date(2026, 11, 10))
    assert d["seg_total"] == 5 and d["seg"]["perdido"] == 1 and d["seg"]["nuevo"] == 1, d["seg"]
    nm = {m["mes"]: (m["nuevos"], m["volvieron"], m["pct"], m["abierto"]) for m in d["nuevos_mes"]}
    assert nm == {"2026-11": (1, 0, 0, True), "2026-09": (2, 2, 100, False), "2026-08": (1, 1, 100, False), "2026-05": (1, 0, 0, False)}, nm
    cam = {(c["antes"], c["despues"]): c["n"] for c in d["caminos"]}
    assert cam == {("Porche PRO", "Repuesto"): 1, ("Rampa", "Porche PRO"): 1, ("Rampa", "Bowl"): 1, ("Repuesto", "Repuesto"): 1}, cam
    assert [(j["a"], j["b"], j["n"]) for j in d["juntos"]] == [("Bowl", "Porche PRO", 2)]
    assert d["vuelta"] == [{"k": "Repuesto", "dias": 30, "n": 4}], d["vuelta"]
    pr = d["res_tipo"][0]
    assert pr["tipo"] == "Primer repuesto" and pr["total"] == 3 and dict(pr["res"]) == {"Compró": 1, "El perro no se adaptó": 1, "Mensaje enviado": 1}
    assert [i["c"] for i in d["intentos"]] == [1, 0, 0, 0]
    assert [(g["k"], g["si"], g["no"], g["pct_no"]) for g in d["adapt"]["tamano"]] == [("Grande", 0, 1, 100), ("Mediano", 1, 0, 0)]
    assert [(g["k"], g["si"], g["no"]) for g in d["adapt"]["edad"]] == [("Cachorro (menos de 1 año)", 1, 0), ("Mayor (8 años o más)", 0, 1)]
    assert d["cumple"] == {"felicitados": 1, "compraron": 1} and d["basico"] == {"total": 0, "pro": 0}
    con.close()
    with erp_de_prueba() as c:
        sesion_de(c, "admin")
        h = c.get("/crecimiento-clientes").text
        assert "Clientes que se quedan" in h and "¿Vuelven a comprar?" in h
        assert 'href="/crecimiento-clientes"' not in c.get("/analitica").text   # la dueña lo sacó del menú hasta pensarlo mejor
        for rol in ("logistica", "taller"):
            sesion_de(c, rol)
            r = c.get("/crecimiento-clientes", follow_redirects=False)
            assert r.status_code in (302, 303), rol


@prueba("Analítica › Miembros PRO › ver quiénes: muestra exactamente los nuevos de ese mes (porche PRO o kit completo), ni uno más")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        pro = con.execute("INSERT INTO productos (sku,nombre,tipo,precio) VALUES ('PRO-PRUEBA','Porche PRO prueba','producto',93)").lastrowid
        kit = con.execute("INSERT INTO productos (sku,nombre,tipo,precio) VALUES ('KIT-COMPLETO-PRUEBA','Kit completo prueba','producto',150)").lastrowid
        def orden(nombre, dia, pid):
            cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES (?,?,?)", (nombre, nombre, "0414" + str(abs(hash(nombre)) % 10**7).zfill(7))).lastrowid
            oid = con.execute("INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,creado_en,subtotal,total) VALUES (?,?,'whatsapp','entregada','pagada',?,93,93)",
                              ("#N" + nombre, cid, dia + " 10:00:00")).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,cantidad,precio,total) VALUES (?,?,1,93,93)", (oid, pid))
            return cid
        orden("Nueva Pro", "2025-02-10", pro); orden("Nueva Kit", "2025-02-20", kit)
        viejo = orden("Viejo Pro", "2025-01-05", pro)
        oid = con.execute("INSERT INTO ordenes (numero,cliente_id,canal,estado,estado_pago,creado_en,subtotal,total) VALUES ('#Nviejo2',?,'whatsapp','entregada','pagada','2025-02-15 10:00:00',93,93)", (viejo,)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,cantidad,precio,total) VALUES (?,?,1,93,93)", (oid, pro))   # volvió a comprar: no es nuevo
        con.commit()
        n = A.resumen_mes(con, datetime.date(2025, 2, 1))["pro_nuevos"]
        con.close()
        assert n == 2, n
        sesion_de(c, "admin")
        assert 'href="/miembros?nuevos=' in c.get("/analitica").text
        h = c.get("/miembros?nuevos=2025-02").text
        assert "Nueva Pro" in h and "Nueva Kit" in h and "Viejo Pro" not in h
        assert "Nuevos de Febrero 2025" in h and f'<span class="bf-n">{n}</span>' in h
        assert "Viejo Pro" in c.get("/miembros").text


@prueba("Mis entregas: si recibe otra persona, tocar el WhatsApp del cliente avisa quién recibe de verdad")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        desp = A.DESPACHADORES[0]
        pro = con.execute("SELECT id, nombre FROM productos WHERE sku='PRO-M'").fetchone()
        def orden(n, nombre, tel, receptor=None, rtel=None):
            cid = con.execute("INSERT INTO clientes (nombre,nombre_pila,telefono) VALUES (?,?,?)", (nombre, nombre, tel)).lastrowid
            oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,direccion,zona,despachador,receptor_nombre,receptor_telefono,fecha_prometida)
                                 VALUES (?,?,'pendiente','pagada',50,0,'delivery','Calle 1','Chacao',?,?,?,date('now'))""", (n, cid, desp, receptor, rtel)).lastrowid
            con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,costo,total) VALUES (?,?,?,1,50,0,50)", (oid, pro["id"], pro["nombre"]))
        orden("#R1", "Clienta Tercero", "04145550001", "Vecina Recibe", "04145550002")
        orden("#R2", "Clienta Sola", "04145550003")
        con.commit(); con.close()
        sesion_de(c, "despachador")
        h = c.get("/mis-entregas").text
        assert 'data-cliente="Clienta Tercero" data-recibe="Vecina Recibe"' in h and "data-recibe-href=" in h, "el WhatsApp del cliente sabe quién recibe"
        assert h.count('data-recibe="') == 1, "si recibe el mismo cliente, no hay aviso"
        assert "no es quien recibe" in h


@prueba("Despachador: un adelanto sale como último pago y queda en el historial con su concepto")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT OR IGNORE INTO cuentas (codigo,nombre,moneda,tipo,activa,orden) VALUES ('001','Efectivo USD','USD','operativa',1,1)")
        did = con.execute("INSERT INTO despachadores (nombre, activo) VALUES ('Ingrid Adel', 1)").lastrowid
        con.execute("INSERT INTO pagos_despachador (despachador, fecha, monto, entregas) VALUES ('Ingrid Adel', '2026-01-05', 30, 3)")
        con.commit(); A.cargar_formas_pago()
        sesion_de(c, "admin")
        hoy = datetime.date.today().isoformat()
        c.post(f"/despachadores/{did}/adelanto", data={"monto": "20", "forma": "Efectivo USD", "fecha": hoy, "nota": "para la moto"})
        r = A.resumen_despachador(con, "Ingrid Adel", datetime.date.today())
        assert r["ultimo_pago"]["monto"] == 20 and r["ultimo_pago"]["adelanto"], r["ultimo_pago"]
        h = c.get(f"/despachadores/{did}").text
        assert "para la moto" in h and "· adelanto" in h and ">Concepto<" in h
        assert "Último pago: $20.00 de adelanto" in re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", c.get("/despachadores").text)).replace(" :", ":")
        con.close()


@prueba("Despachador: el adelanto salda lo que ya hizo (lo más viejo primero), queda en su historial y la lista dice al día (sin zonas: ahora Hoy y Mañana)")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        con.execute("INSERT OR IGNORE INTO cuentas (codigo,nombre,moneda,tipo,activa,orden) VALUES ('001','Efectivo USD','USD','operativa',1,1)")
        did = con.execute("INSERT INTO despachadores (nombre, activo) VALUES ('Juan Adel', 1)").lastrowid
        cid = con.execute("INSERT INTO clientes (nombre_pila,nombre) VALUES ('Ana','Ana Prueba')").lastrowid
        hoy = datetime.date.today()
        for i, z in enumerate(["Z1", "", "Z2", "Z3", "Z4", "Z5"]):
            con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,delivery,tipo_entrega,despachador,zona,fecha_entrega)
                           VALUES (?,?,'entregada','pagada',40,5,'delivery','Juan Adel',?,?)""", (f"#AD{i}", cid, z, (hoy - datetime.timedelta(days=i + 1)).isoformat()))
        con.commit(); A.cargar_formas_pago()
        sesion_de(c, "admin")
        c.post(f"/despachadores/{did}/adelanto", data={"monto": "22", "forma": "Efectivo USD", "fecha": hoy.isoformat(), "nota": "gasolina"})
        pagadas = [r[0] for r in con.execute("SELECT numero FROM ordenes WHERE despachador='Juan Adel' AND despachador_pagado=1 ORDER BY numero")]
        assert pagadas == ["#AD2", "#AD3", "#AD4", "#AD5"], pagadas   # $20 de las 4 más viejas; con $2 no alcanza otra
        r = A.resumen_despachador(con, "Juan Adel", hoy)
        assert r["debe"] == 8 and r["adelanto_libre"] == 0 and r["ultimo_pago"]["monto"] == 22, (r["debe"], r["ultimo_pago"])
        h = c.get(f"/despachadores/{did}").text
        assert "Saldado con el adelanto" in h and "gasolina" in h and 'id="dlg-pagos"' in h
        c.post(f"/despachadores/{did}/adelanto", data={"monto": "10", "forma": "Efectivo USD", "fecha": hoy.isoformat()})
        r = A.resumen_despachador(con, "Juan Adel", hoy)
        assert r["debe"] == 0 and r["n_debe"] == 0, r["debe"]
        l = c.get("/despachadores").text
        card = l[l.index("Juan Adel"):][:3000]
        assert "al día" in card and "le debes por" not in card and "Sin zona" not in card
        assert not any(z in card for z in ("Z1", "Z2", "Z3", "Z4", "Z5")), "las zonas ya no salen: ahora dice Hoy y Mañana (Cristina, 12 nov)"
        con.close()


@prueba("Un pack que se compró sin llevar nada no sale 'por entregar' del despachador; un envío sin ciudad toma la de la ficha del cliente y va en lo que se copia para la agencia")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        did = con.execute("INSERT INTO despachadores (nombre, activo) VALUES ('Desp Pack', 1)").lastrowid
        cid = con.execute("INSERT INTO clientes (nombre_pila,nombre,ciudad,cedula) VALUES ('Darwin','Darwin Pack','Valencia','V-1')").lastrowid
        p = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 LIMIT 1").fetchone()["id"]
        o = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,tipo_entrega,despachador) VALUES ('#PKD',?,'pendiente','pagada',72,'delivery','Desp Pack')", (cid,)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Pack 3',1,72,72)", (o, p))
        con.execute("INSERT INTO packs (cliente_id,orden_id,producto_id,unidades,entregadas_inicio) VALUES (?,?,?,3,0)", (cid, o, p))
        v = con.execute("INSERT INTO viajes_agencia (fecha,despachador,agencia,oficina,monto,pedidos) VALUES (date('now'),'Desp Pack','Tealca','Catia',10,1)").lastrowid
        e = con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,tipo_entrega,direccion,viaje_id) VALUES ('#ENV',?,'pendiente','pagada',40,'nacional','Oficina Dr. Portillo',?)", (cid, v)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Comedero',1,40,40)", (e, p))
        con.commit(); sesion_de(c, "admin")
        h = c.get(f"/despachadores/{did}").text
        assert "#PKD" not in h, "el pack va por Packs, no queda pendiente con el despachador"
        assert A.resumen_despachador(con, "Desp Pack", datetime.date.today())["n_sin_entregar"] == 0
        con.execute("INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,tipo_entrega,despachador,espera_aviso,creado_en) VALUES ('#AVI',?,'pendiente','pagada',40,'delivery','Desp Pack',1,'2026-01-01 10:00')", (cid,))
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (last_insert_rowid(),?,'Comedero',1,40,40)", (p,)); con.commit()
        h = c.get(f"/despachadores/{did}").text
        assert "#AVI" in h and "espera que el cliente avise" in h and "atrasado" not in h, "esperando aviso no es atrasado"
        assert "📍 Oficina Dr. Portillo · Valencia" in A.copia_agencia(A.viajes_por_llevar(con, "Desp Pack")[0]["paquetes"][0])
        op = c.get("/operaciones?cola=todo&tipo=nacional").text
        fila = op[op.index("#ENV") - 3000:op.index("#ENV") + 3000]
        assert "Valencia" in fila, "la ciudad de la ficha del cliente"
        con.close()


@prueba("Crear orden: se elige la fecha de entrega (ya pagó, pero es para mañana) y sale mañana, no hoy")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        p = con.execute("SELECT id FROM productos WHERE tipo='producto' AND requiere_color=0 AND categoria NOT IN ('porche','repuesto','opcion','kit') LIMIT 1").fetchone()["id"]
        con.execute("UPDATE productos SET precio=100 WHERE id=?", (p,))
        cid = con.execute("SELECT MIN(id) FROM clientes").fetchone()[0]
        con.commit(); A.cargar_formas_pago()
        fo = con.execute("SELECT nombre FROM cuentas WHERE activa=1 AND COALESCE(cobra,1)=1 AND nombre NOT LIKE 'Efectivo%' ORDER BY orden, codigo LIMIT 1 OFFSET 1").fetchone()[0]
        sesion_de(c, "admin")
        assert 'name="fecha_prometida"' in c.get("/ordenes/nueva/panel").text
        hoy = datetime.date.today(); manana = (hoy + datetime.timedelta(days=1)).isoformat()
        c.post("/ordenes/nueva", data={"cliente_id": str(cid), "canal": "whatsapp", "producto_id": str(p), "cantidad": "1", "personalizacion": "", "color": "", "malla": "0",
               "tipo_entrega": "pickup", "pago_forma": [fo], "pago_monto": ["100"], "pago_ref": [""], "fecha_pago": hoy.isoformat(), "fecha_prometida": manana})
        o = con.execute("SELECT * FROM ordenes WHERE cliente_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        assert o["fecha_prometida"] == manana and o["estado"] == "pendiente", dict(o)
        assert o["numero"] not in c.get("/operaciones?cola=hoy").text and o["numero"] in c.get("/operaciones?cola=manana").text
        con.close()


@prueba("Nombre y apellido: si se escribe todo en Nombre, el ERP lo separa (al crear, al editar y en los que ya estaban)")
def _():
    assert A.partir_nombre("Andrea González", None) == ("Andrea", "González")
    assert A.partir_nombre("María José Pérez", "") == ("María José", "Pérez")
    assert A.partir_nombre("Andrea González Pérez", None) == ("Andrea", "González Pérez")
    assert A.partir_nombre("Ana María de la Rosa Díaz", None)[0] == "Ana María"
    assert A.partir_nombre("Andrea", None) == ("Andrea", None)
    assert A.partir_nombre("Andrea González", "Pérez") == ("Andrea González", "Pérez"), "si ya trae apellido no se toca"
    assert A.partir_nombre("Cristina Alejandra Raffalli Gomez", None) == ("Cristina Alejandra", "Raffalli Gomez")
    assert A.partir_nombre("andrea valentina del valle lopez marrero", None) == ("Andrea Valentina del Valle", "Lopez Marrero")
    assert A.partir_nombre("Ana María de la Rosa Díaz", None) == ("Ana María", "de la Rosa Díaz")
    assert A.partir_nombre("María de los Ángeles", None) == ("María de los Ángeles", None), "dudoso: a mano"
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        viejo = con.execute("INSERT INTO clientes (nombre_pila,nombre,telefono) VALUES ('Luisa Martínez','Luisa Martínez','04140000001')").lastrowid
        compuesto = con.execute("INSERT INTO clientes (nombre_pila,nombre,telefono) VALUES ('María José Pérez','María José Pérez','04140000002')").lastrowid
        uno = con.execute("INSERT INTO clientes (nombre_pila,nombre,telefono) VALUES ('Luisa','Luisa','04140000003')").lastrowid
        con.commit()
        con.executescript(open(os.path.join(A.BASE, "migraciones", "025_separar_nombre_y_apellido.sql"), encoding="utf-8").read())
        fila = lambda i: tuple(con.execute("SELECT nombre_pila, apellido, nombre FROM clientes WHERE id=?", (i,)).fetchone())
        assert fila(viejo) == ("Luisa", "Martínez", "Luisa Martínez"), fila(viejo)
        assert fila(compuesto)[:2] == ("María José", "Pérez"), fila(compuesto)
        assert fila(uno)[:2] == ("Luisa", None)
        sesion_de(c, "admin")
        cuatro = con.execute("INSERT INTO clientes (nombre_pila,nombre,telefono) VALUES ('Cristina Alejandra Raffalli Gomez','Cristina Alejandra Raffalli Gomez','04140000004')").lastrowid
        angeles = con.execute("INSERT INTO clientes (nombre_pila,nombre,telefono) VALUES ('María de los Ángeles','María de los Ángeles','04140000005')").lastrowid
        con.commit()
        con.executescript(open(os.path.join(A.BASE, "migraciones", "026_separar_nombre_cuatro_palabras.sql"), encoding="utf-8").read())
        assert fila(cuatro) == ("Cristina Alejandra", "Raffalli Gomez", "Cristina Alejandra Raffalli Gomez"), fila(cuatro)
        assert fila(angeles)[:2] == ("María de los Ángeles", None), fila(angeles)
        assert 'name="apellido" required' not in c.get("/clientes").text + open(os.path.join(A.BASE, "templates", "_cliente_nuevo.html"), encoding="utf-8").read(), "el apellido no es obligatorio: si se deja vacío, se separa solo"
        c.post("/clientes/nuevo", data={"nombre_pila": "andrea gonzález", "apellido": "", "telefono": "04121302994"})
        assert tuple(con.execute("SELECT nombre_pila, apellido, nombre FROM clientes ORDER BY id DESC LIMIT 1").fetchone()) == ("Andrea", "González", "Andrea González")
        l = c.get("/clientes?ver=pendientes&falta=apellido").text
        assert "Luisa" in l and "falta=apellido" in l, "se pueden ver los que tienen solo el nombre"
        c.post(f"/clientes/{uno}/editar", data={"nombre_pila": "Luisa Fernández"})
        assert fila(uno) == ("Luisa", "Fernández", "Luisa Fernández"), fila(uno)
        con.close()


@prueba("Montar: Cristina mirando como despachador (sin haberlo elegido en la lista) también le manda al taller lo que toca, igual que el despachador")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today().isoformat(); yo = A.DESPACHADORES[0]
        pid = con.execute("INSERT INTO productos (sku,nombre,tipo,precio) VALUES ('CARRO2','Porche Ver','producto',90)").lastrowid
        cid = con.execute("INSERT INTO clientes (nombre,nombre_pila) VALUES ('Ver Prueba','Ver')").lastrowid
        oid = con.execute("""INSERT INTO ordenes (numero,cliente_id,estado,estado_pago,total,origen_excel,tipo_entrega,despachador,fecha_prometida)
                             VALUES ('#VC1',?,'pendiente','pagada',90,0,'delivery',?,?)""", (cid, yo, hoy)).lastrowid
        con.execute("INSERT INTO orden_lineas (orden_id,producto_id,nombre,cantidad,precio,total) VALUES (?,?,'Porche Ver',1,90,90)", (oid, pid)); con.commit()
        sesion_de(c, "admin"); c.cookies.set("ver_como", "despachador")
        h = c.get("/mis-entregas").text
        assert f'name="quien" value="{yo}"><input type="hidden" name="item" value="orden:{oid}">' in h
        c.post("/mis-entregas/montar", data={"item": f"orden:{oid}", "quien": yo})
        assert [r[0] for r in con.execute("SELECT orden_id FROM cargas_taller")] == [oid], "el botón no hacía nada"
        sesion_de(c, "taller"); assert "Porche Ver" in c.get("/taller").text
        con.close()


@prueba("Producción › Editar: se cambia '¿Cuándo lo pagas?' y el aviso de Inicio se mueve a ese día")
def _():
    with erp_de_prueba() as c:
        con = sqlite3.connect(A.DB); con.row_factory = sqlite3.Row
        hoy = datetime.date.today()
        pid = con.execute("""INSERT INTO produccion (pieza,cantidad,costo,estado,responsable,fecha_pedido,fecha_esperada,fecha_pago)
                             VALUES ('Rampa Fecha Pago',5,90,'en_proceso','Walter',?,?,?)""", (hoy.isoformat(), hoy.isoformat(), hoy.isoformat())).lastrowid
        con.commit()
        sesion_de(c, "admin")
        assert "Hoy toca pagar Rampa Fecha Pago" in c.get("/inicio").text
        assert 'name="fecha_pago" value="' + hoy.isoformat() + '"' in c.get("/produccion?ver=todas").text, "se ve en Editar"
        viernes = (hoy + datetime.timedelta(days=2)).isoformat()
        c.post(f"/produccion/{pid}/editar", data={"cantidad": "5", "responsable": "Walter", "fecha_pedido": hoy.isoformat(), "fecha_esperada": hoy.isoformat(),
                                                  "costo": "90", "fecha_pago": viernes}, follow_redirects=False)
        assert con.execute("SELECT fecha_pago FROM produccion WHERE id=?", (pid,)).fetchone()[0] == viernes
        assert "Hoy toca pagar Rampa Fecha Pago" not in c.get("/inicio").text, "hoy ya no toca: es otro día"
        con.close()


print()
print("─" * 52)
print(f"  {_ok} bien · {_mal} mal")
if _fallos:
    print("\n  DETALLE DE LO QUE FALLÓ:")
    for t, e, tb in _fallos:
        print(f"\n  ── {t}\n{tb}")
raise SystemExit(1 if _mal else 0)
