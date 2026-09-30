"""El esquema de la base, definido una sola vez.

Reemplaza a plataforma/modelo.sql + la tabla COLUMNAS de app.py. Es la fuente de
verdad de la estructura: de aquí sale la migración inicial de Alembic y las pruebas
arman su base temporal con metadata.create_all().

Los tipos se mantienen como en la base en uso —fechas en TEXT (ISO), dinero en Real,
banderas en Integer 0/1, JSON en Text— para que la migración de datos sea una copia
fila a fila y ninguna regla de negocio cambie de comportamiento. Pasar a DATE/NUMERIC
queda como limpieza futura.
"""
from sqlalchemy import (MetaData, Table, Column, Integer, Text, Float,
                       ForeignKey, PrimaryKeyConstraint, UniqueConstraint, text)

metadata = MetaData()

# El mismo formato que daba SQLite: '2026-09-30 01:45:00'. Lo que lee el ERP hace
# substr(creado_en,1,10) o fromisoformat(x[:16]), así que hay que conservarlo.
AHORA = text("to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')")

usuarios = Table(
    "usuarios", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("nombre", Text, nullable=False),
    Column("rol", Text, nullable=False),
    Column("activo", Integer, nullable=True, server_default=text('1')),
    Column("usuario", Text, nullable=True),
    Column("clave_hash", Text, nullable=True),
    Column("creado_en", Text, nullable=True),
    Column("despachador", Text, nullable=True),
)

clientes = Table(
    "clientes", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("nombre_pila", Text, nullable=False),
    Column("apellido", Text, nullable=True),
    Column("nombre", Text, nullable=False),
    Column("telefono", Text, nullable=True),
    Column("cedula", Text, nullable=True),
    Column("correo", Text, nullable=True),
    Column("origen_excel", Integer, nullable=True, server_default=text('0')),
    Column("ciudad", Text, nullable=True),
    Column("estado", Text, nullable=True),
    Column("canal_habitual", Text, nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    Column("origen", Text, nullable=True),
    Column("origen_nota", Text, nullable=True),
    Column("porche_tamano", Text, nullable=True),
    Column("porche_version", Text, nullable=True),
    Column("referido_id", Integer, nullable=True),
)

mascotas = Table(
    "mascotas", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("cliente_id", Integer, ForeignKey("clientes.id", ondelete='CASCADE'), nullable=False),
    Column("nombre", Text, nullable=False),
    Column("raza", Text, nullable=True),
    Column("fecha_nacimiento", Text, nullable=True),
    Column("cumple_mes_dia", Text, nullable=True),
    Column("revisar", Integer, nullable=True, server_default=text('0')),
    Column("peso_kg", Float, nullable=True),
    Column("notas", Text, nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
)

direcciones = Table(
    "direcciones", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("cliente_id", Integer, ForeignKey("clientes.id", ondelete='CASCADE'), nullable=False),
    Column("etiqueta", Text, nullable=True),
    Column("direccion", Text, nullable=False),
    Column("zona", Text, nullable=True),
    Column("municipio", Text, nullable=True),
    Column("ciudad", Text, nullable=True),
    Column("estado", Text, nullable=True),
    Column("maps", Text, nullable=True),
    Column("principal", Integer, nullable=True, server_default=text('0')),
)

notas_cliente = Table(
    "notas_cliente", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("cliente_id", Integer, ForeignKey("clientes.id", ondelete='CASCADE'), nullable=False),
    Column("tipo", Text, nullable=False),
    Column("texto", Text, nullable=False),
    Column("mostrar_en_orden", Integer, nullable=True, server_default=text('0')),
    Column("mostrar_logistica", Integer, nullable=True, server_default=text('0')),
    Column("privada", Integer, nullable=True, server_default=text('0')),
    Column("autor_id", Integer, ForeignKey("usuarios.id"), nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
)

productos = Table(
    "productos", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("sku", Text, nullable=True, unique=True),
    Column("nombre", Text, nullable=False),
    Column("categoria", Text, nullable=True),
    Column("variante", Text, nullable=True),
    Column("descripcion", Text, nullable=True),
    Column("precio", Float, nullable=True),
    Column("precio_par", Float, nullable=True),
    Column("costo", Float, nullable=True),
    Column("activo", Integer, nullable=True, server_default=text('1')),
    Column("orden", Integer, nullable=True, server_default=text('100')),
    Column("tipo", Text, nullable=True, server_default=text("'producto'")),
    Column("requiere_color", Integer, nullable=True, server_default=text('0')),
    Column("permite_malla", Integer, nullable=True, server_default=text('0')),
    Column("permite_personalizacion", Integer, nullable=True, server_default=text('0')),
    Column("stock", Integer, nullable=True, server_default=text('0')),
    Column("minimo", Integer, nullable=True, server_default=text('0')),
    Column("foto", Text, nullable=True),
    Column("descripcion_larga", Text, nullable=True),
    Column("peso_kg", Float, nullable=True),
    Column("foto_rosado", Text, nullable=True),
    Column("canales", Text, nullable=True),
    Column("proveedor", Text, nullable=True),
    Column("unidad", Text, nullable=True),
)

ordenes = Table(
    "ordenes", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("numero", Text, nullable=True, unique=True),
    Column("tipo", Text, nullable=True, server_default=text("'venta'")),
    Column("cliente_id", Integer, ForeignKey("clientes.id"), nullable=True),
    Column("canal", Text, nullable=True),
    Column("creada_por", Integer, ForeignKey("usuarios.id"), nullable=True),
    Column("conversacion", Text, nullable=True),
    Column("campana", Text, nullable=True),
    Column("ref_externa", Text, nullable=True),
    Column("estado", Text, nullable=True, server_default=text("'nueva'")),
    Column("estado_pago", Text, nullable=True, server_default=text("'sin_pago'")),
    Column("subtotal", Float, nullable=True, server_default=text('0')),
    Column("descuento", Float, nullable=True, server_default=text('0')),
    Column("motivo_descuento", Text, nullable=True),
    Column("iva", Float, nullable=True, server_default=text('0')),
    Column("delivery", Float, nullable=True, server_default=text('0')),
    Column("total", Float, nullable=True, server_default=text('0')),
    Column("tasa_bcv", Float, nullable=True),
    Column("comision", Float, nullable=True, server_default=text('0')),
    Column("tipo_entrega", Text, nullable=True),
    Column("direccion", Text, nullable=True),
    Column("zona", Text, nullable=True),
    Column("ciudad", Text, nullable=True),
    Column("maps", Text, nullable=True),
    Column("receptor_nombre", Text, nullable=True),
    Column("receptor_telefono", Text, nullable=True),
    Column("despachador", Text, nullable=True),
    Column("agencia", Text, nullable=True),
    Column("guia", Text, nullable=True),
    Column("fecha_prometida", Text, nullable=True),
    Column("franja", Text, nullable=True),
    Column("fecha_entrega", Text, nullable=True),
    Column("fecha_pago", Text, nullable=True),
    Column("promo_envio", Text, nullable=True),
    Column("notas_entrega", Text, nullable=True),
    Column("notas", Text, nullable=True),
    Column("distribuidor", Text, nullable=True),
    Column("saldo_concepto", Text, nullable=True),
    Column("forma_pago_prevista", Text, nullable=True),
    Column("modalidad_envio", Text, nullable=True),
    Column("monto_contra_entrega", Float, nullable=True, server_default=text('0')),
    Column("origen_excel", Integer, nullable=True, server_default=text('0')),
    Column("costo_productos", Float, nullable=True, server_default=text('0')),
    Column("costo_entrega", Float, nullable=True, server_default=text('0')),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    Column("actualizado_en", Text, nullable=True, server_default=AHORA),
    Column("despachador_pagado", Integer, nullable=False, server_default=text('0')),
    Column("despachador_pago_id", Integer, nullable=True),
    Column("en_registro", Integer, nullable=True, server_default=text('0')),
    Column("factura_fecha", Text, nullable=True),
    Column("factura_hecha", Integer, nullable=True, server_default=text('0')),
    Column("factura_numero", Text, nullable=True),
    Column("factura_por", Integer, nullable=True),
    Column("pago_despachador", Float, nullable=True),
    Column("receptor_cedula", Text, nullable=True),
    Column("receptor_correo", Text, nullable=True),
    Column("requiere_factura", Integer, nullable=True, server_default=text('0')),
    Column("viaje_id", Integer, nullable=True),
)

orden_lineas = Table(
    "orden_lineas", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("orden_id", Integer, ForeignKey("ordenes.id", ondelete='CASCADE'), nullable=False),
    Column("producto_id", Integer, ForeignKey("productos.id"), nullable=True),
    Column("nombre", Text, nullable=True),
    Column("cantidad", Float, nullable=True, server_default=text('1')),
    Column("precio", Float, nullable=True, server_default=text('0')),
    Column("costo", Float, nullable=True, server_default=text('0')),
    Column("descuento", Float, nullable=True, server_default=text('0')),
    Column("personalizacion", Text, nullable=True),
    Column("color", Text, nullable=True),
    Column("malla", Integer, nullable=True, server_default=text('0')),
    Column("extras", Float, nullable=True, server_default=text('0')),
    Column("total", Float, nullable=True, server_default=text('0')),
    Column("forma_pago", Text, nullable=True),
    Column("perso_lista", Integer, nullable=False, server_default=text('0')),
    Column("perso_lista_en", Text, nullable=True),
    Column("extra_en", Text, nullable=True),
)

pagos = Table(
    "pagos", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("orden_id", Integer, ForeignKey("ordenes.id", ondelete='CASCADE'), nullable=False),
    Column("forma", Text, nullable=False),
    Column("monto_usd", Float, nullable=False),
    Column("monto_real", Float, nullable=True),
    Column("moneda", Text, nullable=True, server_default=text("'USD'")),
    Column("tasa", Float, nullable=True),
    Column("cuenta", Text, nullable=True),
    Column("referencia", Text, nullable=True),
    Column("comprobante", Text, nullable=True),
    Column("fecha", Text, nullable=True),
    Column("estado", Text, nullable=True, server_default=text("'por_confirmar'")),
    Column("verificado_tina", Integer, nullable=True, server_default=text('0')),
    Column("motivo_revision", Text, nullable=True),
    Column("confirmado_por", Integer, ForeignKey("usuarios.id"), nullable=True),
    Column("confirmado_en", Text, nullable=True),
    Column("en_cashflow", Integer, nullable=False, server_default=text('0')),
)

historial = Table(
    "historial", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("orden_id", Integer, ForeignKey("ordenes.id", ondelete='CASCADE'), nullable=False),
    Column("usuario_id", Integer, ForeignKey("usuarios.id"), nullable=True),
    Column("accion", Text, nullable=False),
    Column("detalle", Text, nullable=True),
    Column("motivo", Text, nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
)

incidencias = Table(
    "incidencias", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("orden_id", Integer, ForeignKey("ordenes.id", ondelete='CASCADE'), nullable=False),
    Column("clase", Text, nullable=True, server_default=text("'incidencia'")),
    Column("tipo", Text, nullable=True),
    Column("descripcion", Text, nullable=True),
    Column("responsable", Text, nullable=True),
    Column("estado", Text, nullable=True, server_default=text("'abierta'")),
    Column("resolucion", Text, nullable=True),
    Column("autor_id", Integer, ForeignKey("usuarios.id"), nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    Column("cerrado_en", Text, nullable=True),
)

config = Table(
    "config", metadata,
    Column("clave", Text, nullable=True, primary_key=True),
    Column("valor", Text, nullable=True),
)

packs = Table(
    "packs", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("cliente_id", Integer, ForeignKey("clientes.id"), nullable=False),
    Column("orden_id", Integer, ForeignKey("ordenes.id"), nullable=True),
    Column("producto_id", Integer, ForeignKey("productos.id"), nullable=True),
    Column("tamano", Text, nullable=True),
    Column("unidades", Integer, nullable=False),
    Column("entregadas_inicio", Integer, nullable=True, server_default=text('1')),
    Column("estado", Text, nullable=True, server_default=text("'activo'")),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    Column("deliveries_prepagados", Integer, nullable=True, server_default=text('0')),
    Column("delivery_pagado", Integer, nullable=True),
    Column("delivery_programado", Float, nullable=True),
    Column("despachador_programado", Text, nullable=True),
    Column("fecha_programada", Text, nullable=True),
    Column("nota_programada", Text, nullable=True),
    Column("retiro_programado", Integer, nullable=True),
    Column("tipo_programado", Text, nullable=True),
    Column("en_ruta", Integer, nullable=False, server_default=text('0')),
)

entregas_repuesto = Table(
    "entregas_repuesto", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("pack_id", Integer, ForeignKey("packs.id", ondelete='CASCADE'), nullable=False),
    Column("fecha", Text, nullable=False),
    Column("tipo_entrega", Text, nullable=True),
    Column("despachador", Text, nullable=True),
    Column("delivery_cobrado", Float, nullable=True, server_default=text('0')),
    Column("notas", Text, nullable=True),
    Column("usuario_id", Integer, ForeignKey("usuarios.id"), nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
)

cuentas = Table(
    "cuentas", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("codigo", Text, nullable=True),
    Column("nombre", Text, nullable=False, unique=True),
    Column("moneda", Text, nullable=True, server_default=text("'USD'")),
    Column("tipo", Text, nullable=True, server_default=text("'operativa'")),
    Column("saldo_inicial", Float, nullable=True, server_default=text('0')),
    Column("fecha_corte", Text, nullable=True),
    Column("personal", Integer, nullable=True, server_default=text('0')),
    Column("activa", Integer, nullable=True, server_default=text('1')),
    Column("orden", Integer, nullable=True, server_default=text('100')),
    Column("cobra", Integer, nullable=True, server_default=text('1')),
)

gastos = Table(
    "gastos", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("fecha", Text, nullable=False),
    Column("monto_usd", Float, nullable=False),
    Column("monto_real", Float, nullable=True),
    Column("moneda", Text, nullable=True, server_default=text("'USD'")),
    Column("tasa", Float, nullable=True),
    Column("categoria", Text, nullable=False),
    Column("subcategoria", Text, nullable=True),
    Column("descripcion", Text, nullable=True),
    Column("proveedor", Text, nullable=True),
    Column("cuenta_id", Integer, ForeignKey("cuentas.id"), nullable=True),
    Column("orden_id", Integer, ForeignKey("ordenes.id"), nullable=True),
    Column("recurrente", Integer, nullable=True, server_default=text('0')),
    Column("comprobante", Text, nullable=True),
    Column("notas", Text, nullable=True),
    Column("usuario_id", Integer, ForeignKey("usuarios.id"), nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    Column("cantidad", Float, nullable=True),
    Column("compra_grande", Integer, nullable=False, server_default=text('0')),
    Column("unidad", Text, nullable=True),
)

movimientos = Table(
    "movimientos", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("fecha", Text, nullable=False),
    Column("tipo", Text, nullable=False),
    Column("cuenta_origen_id", Integer, ForeignKey("cuentas.id"), nullable=True),
    Column("cuenta_destino_id", Integer, ForeignKey("cuentas.id"), nullable=True),
    Column("monto_usd", Float, nullable=False),
    Column("monto_real", Float, nullable=True),
    Column("moneda", Text, nullable=True, server_default=text("'USD'")),
    Column("tasa", Float, nullable=True),
    Column("concepto", Text, nullable=True),
    Column("despachador", Text, nullable=True),
    Column("usuario_id", Integer, ForeignKey("usuarios.id"), nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    Column("categoria", Text, nullable=True),
    Column("comprobante", Text, nullable=True),
    Column("notas", Text, nullable=True),
    Column("subcategoria", Text, nullable=True),
)

seguimientos = Table(
    "seguimientos", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("cliente_id", Integer, ForeignKey("clientes.id", ondelete='CASCADE'), nullable=False),
    Column("tipo", Text, nullable=False),
    Column("clave", Text, nullable=True, unique=True),
    Column("resultado", Text, nullable=True),
    Column("nota", Text, nullable=True),
    Column("posponer_hasta", Text, nullable=True),
    Column("intentos", Integer, nullable=False, server_default=text('1')),
    Column("usuario_id", Integer, ForeignKey("usuarios.id"), nullable=True),
    Column("hecho_en", Text, nullable=True, server_default=AHORA),
)

mov_inventario = Table(
    "mov_inventario", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("producto_id", Integer, ForeignKey("productos.id"), nullable=False),
    Column("fecha", Text, nullable=False),
    Column("tipo", Text, nullable=False),
    Column("cantidad", Integer, nullable=False),
    Column("orden_id", Integer, ForeignKey("ordenes.id"), nullable=True),
    Column("nota", Text, nullable=True),
    Column("usuario_id", Integer, ForeignKey("usuarios.id"), nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    Column("color", Text, nullable=True),
    Column("lote", Text, nullable=True),
)

fotos = Table(
    "fotos", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("archivo", Text, nullable=False),
    Column("cliente_id", Integer, ForeignKey("clientes.id"), nullable=True),
    Column("orden_id", Integer, ForeignKey("ordenes.id"), nullable=True),
    Column("producto_id", Integer, ForeignKey("productos.id"), nullable=True),
    Column("canal", Text, nullable=True, server_default=text("'whatsapp'")),
    Column("permiso", Text, nullable=True, server_default=text("'sin_confirmar'")),
    Column("instagram", Text, nullable=True),
    Column("enlace_publicacion", Text, nullable=True),
    Column("nota", Text, nullable=True),
    Column("fecha", Text, nullable=True),
    Column("usuario_id", Integer, ForeignKey("usuarios.id"), nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
)

produccion = Table(
    "produccion", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("producto_id", Integer, ForeignKey("productos.id"), nullable=True),
    Column("fecha_pedido", Text, nullable=False),
    Column("fecha_esperada", Text, nullable=True),
    Column("responsable", Text, nullable=True),
    Column("costo", Float, nullable=True),
    Column("nota", Text, nullable=True),
    Column("estado", Text, nullable=True, server_default=text("'en_proceso'")),
    Column("usuario_id", Integer, ForeignKey("usuarios.id"), nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    Column("recibido_en", Text, nullable=True),
    Column("pieza", Text, nullable=True),
    Column("pagado", Integer, nullable=False, server_default=text('0')),
    Column("barnizado", Integer, nullable=False, server_default=text('0')),
    Column("descripcion", Text, nullable=True),
    Column("cantidad", Integer, nullable=False, server_default=text('1')),
    Column("fecha_pago", Text, nullable=True),
    Column("faltaron", Integer, nullable=True),
    Column("recibido", Integer, nullable=True, server_default=text('0')),
    Column("tipo_pedido", Text, nullable=True, server_default=text("'produccion'")),
)

compromisos = Table(
    "compromisos", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("nombre", Text, nullable=False),
    Column("categoria", Text, nullable=True),
    Column("subcategoria", Text, nullable=True),
    Column("proveedor", Text, nullable=True),
    Column("monto", Float, nullable=True),
    Column("moneda", Text, nullable=True, server_default=text("'USD'")),
    Column("frecuencia", Text, nullable=False),
    Column("dia", Integer, nullable=True),
    Column("cuenta_id", Integer, ForeignKey("cuentas.id"), nullable=True),
    Column("activo", Integer, nullable=True, server_default=text('1')),
    Column("nota", Text, nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    Column("precio_unitario", Float, nullable=True),
    Column("unidad", Text, nullable=True),
)

compromisos_pagos = Table(
    "compromisos_pagos", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("compromiso_id", Integer, ForeignKey("compromisos.id", ondelete='CASCADE'), nullable=False),
    Column("vence", Text, nullable=False),
    Column("gasto_id", Integer, ForeignKey("gastos.id"), nullable=True),
    Column("pagado_en", Text, nullable=True, server_default=AHORA),
    Column("motivo", Text, nullable=True),
    UniqueConstraint('compromiso_id', 'vence'),
)

repuestos_prepagados = Table(
    "repuestos_prepagados", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("cliente_id", Integer, ForeignKey("clientes.id"), nullable=False),
    Column("orden_id", Integer, ForeignKey("ordenes.id"), nullable=True),
    Column("linea_id", Integer, ForeignKey("orden_lineas.id"), nullable=True),
    Column("producto_id", Integer, ForeignKey("productos.id"), nullable=True),
    Column("tamano", Text, nullable=True),
    Column("pagado_en", Text, nullable=True),
    Column("fecha_programada", Text, nullable=True),
    Column("tipo_entrega", Text, nullable=True),
    Column("despachador", Text, nullable=True),
    Column("entregado_en", Text, nullable=True),
    Column("notas", Text, nullable=True),
    Column("usuario_id", Integer, ForeignKey("usuarios.id"), nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    Column("agencia", Text, nullable=True),
    Column("delivery", Float, nullable=False, server_default=text('0')),
    Column("delivery_forma", Text, nullable=True),
    Column("delivery_pagado", Integer, nullable=False, server_default=text('0')),
    Column("en_ruta", Integer, nullable=False, server_default=text('0')),
    Column("envio", Text, nullable=True),
    Column("monto", Float, nullable=True),
)

registro_ventas = Table(
    "registro_ventas", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("fecha", Text, nullable=False),
    Column("cliente", Text, nullable=True),
    Column("producto", Text, nullable=True),
    Column("precio", Float, nullable=True),
    Column("cantidad", Float, nullable=True, server_default=text('1')),
    Column("facturacion", Float, nullable=True),
    Column("forma_pago", Text, nullable=True),
    Column("origen", Text, nullable=True, server_default=text("'excel'")),
    Column("fila_excel", Integer, nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
)

despachadores = Table(
    "despachadores", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("nombre", Text, nullable=False, unique=True),
    Column("telefono", Text, nullable=True),
    Column("activo", Integer, nullable=False, server_default=text('1')),
    Column("notas", Text, nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
)

tarifas = Table(
    "tarifas", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("zona", Text, nullable=False, unique=True),
    Column("tarifa", Float, nullable=False, server_default=text('0')),
    Column("pago_despachador", Float, nullable=True),
    Column("notas", Text, nullable=True),
    Column("orden", Integer, nullable=True, server_default=text('0')),
)

pagos_despachador = Table(
    "pagos_despachador", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("despachador", Text, nullable=False),
    Column("fecha", Text, nullable=False),
    Column("monto", Float, nullable=False),
    Column("entregas", Integer, nullable=False, server_default=text('0')),
    Column("nota", Text, nullable=True),
    Column("usuario_id", Integer, nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    Column("adelanto_usado", Float, nullable=False, server_default=text('0')),
)

proveedores = Table(
    "proveedores", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("nombre", Text, nullable=False),
    Column("que_vende", Text, nullable=True),
    Column("contacto", Text, nullable=True),
    Column("telefono", Text, nullable=True),
    Column("correo", Text, nullable=True),
    Column("ciudad", Text, nullable=True),
    Column("direccion", Text, nullable=True),
    Column("forma_pago", Text, nullable=True),
    Column("precios", Text, nullable=True),
    Column("notas", Text, nullable=True),
    Column("activo", Integer, nullable=False, server_default=text('1')),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
)

proveedor_items = Table(
    "proveedor_items", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("proveedor_id", Integer, ForeignKey("proveedores.id", ondelete='CASCADE'), nullable=False),
    Column("item", Text, nullable=False),
    Column("precio", Float, nullable=True),
    Column("unidad", Text, nullable=True),
    Column("orden", Integer, nullable=True, server_default=text('0')),
)

abonos_produccion = Table(
    "abonos_produccion", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("produccion_id", Integer, ForeignKey("produccion.id", ondelete='CASCADE'), nullable=False),
    Column("fecha", Text, nullable=False),
    Column("monto", Float, nullable=False),
    Column("forma", Text, nullable=True),
    Column("nota", Text, nullable=True),
    Column("usuario_id", Integer, nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    Column("gasto_id", Integer, nullable=True),
)

resultados_mes = Table(
    "resultados_mes", metadata,
    Column("mes", Text, nullable=True, primary_key=True),
    Column("facturacion", Float, nullable=True, server_default=text('0')),
    Column("unidades", Integer, nullable=True, server_default=text('0')),
    Column("gastos", Float, nullable=True, server_default=text('0')),
    Column("sueldo", Float, nullable=True, server_default=text('0')),
    Column("arrastre", Float, nullable=True, server_default=text('0')),
    Column("contexto", Text, nullable=True),
    Column("nota", Text, nullable=True),
)

viajes_agencia = Table(
    "viajes_agencia", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("fecha", Text, nullable=False),
    Column("despachador", Text, nullable=False),
    Column("agencia", Text, nullable=True),
    Column("monto", Float, nullable=False, server_default=text('0')),
    Column("pedidos", Integer, nullable=False, server_default=text('0')),
    Column("pagado", Integer, nullable=False, server_default=text('0')),
    Column("pago_id", Integer, ForeignKey("pagos_despachador.id"), nullable=True),
    Column("nota", Text, nullable=True),
    Column("usuario_id", Integer, nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
)

viajes_despachador = Table(
    "viajes_despachador", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("tipo", Text, nullable=False, server_default=text("'fallido'")),
    Column("orden_id", Integer, ForeignKey("ordenes.id"), nullable=True),
    Column("pack_id", Integer, nullable=True),
    Column("prepagado_id", Integer, nullable=True),
    Column("fecha", Text, nullable=False),
    Column("despachador", Text, nullable=False),
    Column("monto", Float, nullable=False, server_default=text('0')),
    Column("motivo", Text, nullable=True),
    Column("pagado", Integer, nullable=False, server_default=text('0')),
    Column("pago_id", Integer, ForeignKey("pagos_despachador.id"), nullable=True),
    Column("usuario_id", Integer, nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
)

notas_taller = Table(
    "notas_taller", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("fecha", Text, nullable=False),
    Column("texto", Text, nullable=False),
    Column("usuario_id", Integer, nullable=True),
    Column("visto", Integer, nullable=False, server_default=text('0')),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    Column("produccion_id", Integer, nullable=True),
    Column("resuelto", Integer, nullable=False, server_default=text('0')),
    Column("resuelto_en", Text, nullable=True),
)

faltas = Table(
    "faltas", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("nombre", Text, nullable=False),
    Column("fecha", Text, nullable=False),
    Column("nota", Text, nullable=True),
    Column("usuario_id", Integer, nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    UniqueConstraint('nombre', 'fecha'),
)

producto_fotos = Table(
    "producto_fotos", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("producto_id", Integer, ForeignKey("productos.id", ondelete='CASCADE'), nullable=False),
    Column("archivo", Text, nullable=False),
    Column("etiqueta", Text, nullable=True),
    Column("orden", Integer, nullable=True, server_default=text('0')),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
    Column("tipo", Text, nullable=False, server_default=text("'sin_fondo'")),
)

receta = Table(
    "receta", metadata,
    Column("producto_id", Integer, ForeignKey("productos.id"), nullable=False),
    Column("insumo_id", Integer, ForeignKey("productos.id"), nullable=False),
    Column("cantidad", Float, nullable=False, server_default=text('1')),
    PrimaryKeyConstraint('producto_id', 'insumo_id'),
)

tasas = Table(
    "tasas", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("fecha_valor", Text, nullable=False),
    Column("valor", Float, nullable=False),
    Column("fuente", Text, nullable=False),
    Column("obtenido_en", Text, nullable=True, server_default=AHORA),
    Column("manual", Integer, nullable=True, server_default=text('0')),
    Column("usuario_id", Integer, nullable=True),
    Column("nota", Text, nullable=True),
    UniqueConstraint('fecha_valor', 'fuente'),
)

sesiones = Table(
    "sesiones", metadata,
    Column("ficha", Text, nullable=True, primary_key=True),
    Column("usuario_id", Integer, ForeignKey("usuarios.id"), nullable=False),
    Column("creada_en", Text, nullable=False, server_default=AHORA),
    Column("vence_en", Text, nullable=False),
    Column("visto_en", Text, nullable=True),
)

credito_cliente = Table(
    "credito_cliente", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("cliente_id", Integer, ForeignKey("clientes.id"), nullable=False),
    Column("fecha", Text, nullable=False),
    Column("monto", Float, nullable=False),
    Column("motivo", Text, nullable=True),
    Column("orden_id", Integer, ForeignKey("ordenes.id"), nullable=True),
    Column("usuario_id", Integer, nullable=True),
    Column("creado_en", Text, nullable=True, server_default=AHORA),
)

intentos = Table(
    "intentos", metadata,
    Column("id", Integer, nullable=True, primary_key=True),
    Column("usuario", Text, nullable=True),
    Column("ip", Text, nullable=True),
    Column("cuando", Text, nullable=False, server_default=AHORA),
)

TABLAS = sorted(metadata.tables)
