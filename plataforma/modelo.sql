PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS usuarios (
  id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, rol TEXT NOT NULL,  -- admin | logistica | sistema
  activo INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS clientes (
  id INTEGER PRIMARY KEY,
  nombre_pila TEXT NOT NULL,          -- "Harry"
  apellido TEXT,                      -- "Styles" (opcional)
  nombre TEXT NOT NULL,               -- nombre completo, se arma solo: "Harry Styles" (para mostrar y buscar)
  telefono TEXT,                      -- formato fijo 04XX-XXXXXXX; llave para casar con WhatsApp/Tina
  cedula TEXT,                        -- V-12345678 / J-… (opcional, para facturas)
  correo TEXT, origen_excel INTEGER DEFAULT 0,
  ciudad TEXT, estado TEXT, canal_habitual TEXT, creado_en TEXT DEFAULT (datetime('now','localtime')));

CREATE TABLE IF NOT EXISTS mascotas (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  nombre TEXT NOT NULL, raza TEXT, fecha_nacimiento TEXT, cumple_mes_dia TEXT, revisar INTEGER DEFAULT 0, peso_kg REAL, notas TEXT,   -- cumple_mes_dia 'MM-DD' cuando no se sabe el año; revisar=1 si la fecha vino rara
  creado_en TEXT DEFAULT (datetime('now','localtime')));

CREATE TABLE IF NOT EXISTS direcciones (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  etiqueta TEXT, direccion TEXT NOT NULL, zona TEXT, municipio TEXT, ciudad TEXT, estado TEXT, maps TEXT, principal INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS notas_cliente (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  tipo TEXT NOT NULL, texto TEXT NOT NULL, mostrar_en_orden INTEGER DEFAULT 0, mostrar_logistica INTEGER DEFAULT 0,
  privada INTEGER DEFAULT 0, autor_id INTEGER REFERENCES usuarios(id), creado_en TEXT DEFAULT (datetime('now','localtime')));

CREATE TABLE IF NOT EXISTS productos (
  id INTEGER PRIMARY KEY, sku TEXT UNIQUE, nombre TEXT NOT NULL, categoria TEXT, variante TEXT,
  descripcion TEXT,                    -- medidas, notas (no va en el título)
  precio REAL, precio_par REAL,        -- precio_par: precio especial por 2 unidades (bowls, platos)
  costo REAL, activo INTEGER DEFAULT 1, orden INTEGER DEFAULT 100,
  tipo TEXT DEFAULT 'producto',        -- producto | opcion (algo que se agrega a una línea)
  requiere_color INTEGER DEFAULT 0,    -- Slow Chow: elegir plato azul o rosado
  permite_malla INTEGER DEFAULT 0,     -- porche PRO: se le puede agregar la malla
  permite_personalizacion INTEGER DEFAULT 0,
  stock INTEGER DEFAULT 0, minimo INTEGER DEFAULT 0, foto TEXT, descripcion_larga TEXT, peso_kg REAL,
  foto_rosado TEXT);   -- Slow Chow: foto con plato rosado (se muestra al elegir ese color en la orden)

CREATE TABLE IF NOT EXISTS ordenes (
  id INTEGER PRIMARY KEY, numero TEXT UNIQUE, tipo TEXT DEFAULT 'venta',
  cliente_id INTEGER REFERENCES clientes(id), canal TEXT, creada_por INTEGER REFERENCES usuarios(id),
  conversacion TEXT, campana TEXT, ref_externa TEXT,
  estado TEXT DEFAULT 'nueva', estado_pago TEXT DEFAULT 'sin_pago',
  subtotal REAL DEFAULT 0, descuento REAL DEFAULT 0, motivo_descuento TEXT, iva REAL DEFAULT 0, delivery REAL DEFAULT 0, total REAL DEFAULT 0,
  tasa_bcv REAL, comision REAL DEFAULT 0,
  tipo_entrega TEXT, direccion TEXT, zona TEXT, ciudad TEXT, maps TEXT,
  receptor_nombre TEXT, receptor_telefono TEXT,
  despachador TEXT, agencia TEXT, guia TEXT, fecha_prometida TEXT, franja TEXT, fecha_entrega TEXT, fecha_pago TEXT,
  promo_envio TEXT, notas_entrega TEXT, notas TEXT, distribuidor TEXT, saldo_concepto TEXT, forma_pago_prevista TEXT, modalidad_envio TEXT, monto_contra_entrega REAL DEFAULT 0, origen_excel INTEGER DEFAULT 0,
  costo_productos REAL DEFAULT 0, costo_entrega REAL DEFAULT 0,
  creado_en TEXT DEFAULT (datetime('now','localtime')), actualizado_en TEXT DEFAULT (datetime('now','localtime')));

CREATE TABLE IF NOT EXISTS orden_lineas (
  id INTEGER PRIMARY KEY, orden_id INTEGER NOT NULL REFERENCES ordenes(id) ON DELETE CASCADE,
  producto_id INTEGER REFERENCES productos(id), nombre TEXT, cantidad REAL DEFAULT 1, precio REAL DEFAULT 0, costo REAL DEFAULT 0,
  descuento REAL DEFAULT 0, personalizacion TEXT,
  color TEXT,                          -- Slow Chow: azul | rosado
  malla INTEGER DEFAULT 0,             -- 1 si se le agregó la malla
  extras REAL DEFAULT 0,               -- monto de las opciones agregadas (personalización, malla)
  total REAL DEFAULT 0,                -- precio de la línea con cantidad y opciones
  forma_pago TEXT);                    -- solo histórico Excel: forma de pago de esa línea

CREATE TABLE IF NOT EXISTS pagos (
  id INTEGER PRIMARY KEY, orden_id INTEGER NOT NULL REFERENCES ordenes(id) ON DELETE CASCADE,
  forma TEXT NOT NULL, monto_usd REAL NOT NULL, monto_real REAL, moneda TEXT DEFAULT 'USD', tasa REAL,
  cuenta TEXT, referencia TEXT, comprobante TEXT, fecha TEXT,
  estado TEXT DEFAULT 'por_confirmar',   -- por_confirmar | confirmado | rechazado
  verificado_tina INTEGER DEFAULT 0, motivo_revision TEXT, confirmado_por INTEGER REFERENCES usuarios(id), confirmado_en TEXT);

CREATE TABLE IF NOT EXISTS historial (
  id INTEGER PRIMARY KEY, orden_id INTEGER NOT NULL REFERENCES ordenes(id) ON DELETE CASCADE,
  usuario_id INTEGER REFERENCES usuarios(id), accion TEXT NOT NULL, detalle TEXT, motivo TEXT,
  creado_en TEXT DEFAULT (datetime('now','localtime')));

CREATE TABLE IF NOT EXISTS incidencias (
  id INTEGER PRIMARY KEY, orden_id INTEGER NOT NULL REFERENCES ordenes(id) ON DELETE CASCADE,
  clase TEXT DEFAULT 'incidencia', tipo TEXT, descripcion TEXT, responsable TEXT, estado TEXT DEFAULT 'abierta',
  resolucion TEXT, autor_id INTEGER REFERENCES usuarios(id), creado_en TEXT DEFAULT (datetime('now','localtime')), cerrado_en TEXT);

CREATE TABLE IF NOT EXISTS config (clave TEXT PRIMARY KEY, valor TEXT);

-- Packs de repuestos: la compra es una venta; el pack guarda el saldo que Decopet le debe al cliente
CREATE TABLE IF NOT EXISTS packs (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL REFERENCES clientes(id), orden_id INTEGER REFERENCES ordenes(id),
  producto_id INTEGER REFERENCES productos(id), tamano TEXT, unidades INTEGER NOT NULL, entregadas_inicio INTEGER DEFAULT 1,
  estado TEXT DEFAULT 'activo', creado_en TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS entregas_repuesto (
  id INTEGER PRIMARY KEY, pack_id INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE,
  fecha TEXT NOT NULL, tipo_entrega TEXT, despachador TEXT, delivery_cobrado REAL DEFAULT 0, notas TEXT,
  usuario_id INTEGER REFERENCES usuarios(id), creado_en TEXT DEFAULT (datetime('now','localtime')));

-- ---------- FINANZAS
CREATE TABLE IF NOT EXISTS cuentas (
  id INTEGER PRIMARY KEY, codigo TEXT, nombre TEXT UNIQUE NOT NULL, moneda TEXT DEFAULT 'USD',   -- USD | VES | EUR | USDT
  tipo TEXT DEFAULT 'operativa',                                                     -- operativa | inversion | por_cobrar
  saldo_inicial REAL DEFAULT 0, fecha_corte TEXT,                                    -- saldo el día que se empezó a llevar en la plataforma
  personal INTEGER DEFAULT 0,                                                        -- 1 = cuenta personal de Cristina usada para Decopet
  activa INTEGER DEFAULT 1, orden INTEGER DEFAULT 100);

CREATE TABLE IF NOT EXISTS gastos (
  id INTEGER PRIMARY KEY, fecha TEXT NOT NULL, monto_usd REAL NOT NULL, monto_real REAL, moneda TEXT DEFAULT 'USD', tasa REAL,
  categoria TEXT NOT NULL, subcategoria TEXT, descripcion TEXT, proveedor TEXT,
  cuenta_id INTEGER REFERENCES cuentas(id), orden_id INTEGER REFERENCES ordenes(id),
  recurrente INTEGER DEFAULT 0, comprobante TEXT, notas TEXT,
  usuario_id INTEGER REFERENCES usuarios(id), creado_en TEXT DEFAULT (datetime('now','localtime')));

CREATE TABLE IF NOT EXISTS movimientos (
  id INTEGER PRIMARY KEY, fecha TEXT NOT NULL,
  tipo TEXT NOT NULL,   -- transferencia | retiro | aporte | reembolso | prestamo | liquidacion_despachador | ajuste
  cuenta_origen_id INTEGER REFERENCES cuentas(id), cuenta_destino_id INTEGER REFERENCES cuentas(id),
  monto_usd REAL NOT NULL, monto_real REAL, moneda TEXT DEFAULT 'USD', tasa REAL, concepto TEXT, despachador TEXT,
  usuario_id INTEGER REFERENCES usuarios(id), creado_en TEXT DEFAULT (datetime('now','localtime')));

-- ---------- SEGUIMIENTOS (El porche)
CREATE TABLE IF NOT EXISTS seguimientos (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  tipo TEXT NOT NULL,                 -- toca_repuesto | pack_agotandose | como_le_fue
  clave TEXT UNIQUE,                  -- evita duplicar el mismo seguimiento (tipo + cliente + referencia)
  resultado TEXT,                     -- compro | mensaje (enviado, sin respuesta: vuelve a los 3 días) | ya_no_usa | felicitado | pago
  nota TEXT, posponer_hasta TEXT,     -- con 'mensaje' se pospone; al vencer reaparece como 2º intento, 3º...
  intentos INTEGER NOT NULL DEFAULT 1,
  usuario_id INTEGER REFERENCES usuarios(id), hecho_en TEXT DEFAULT (datetime('now','localtime')));

-- ---------- PRODUCTOS: inventario y galería
CREATE TABLE IF NOT EXISTS mov_inventario (
  id INTEGER PRIMARY KEY, producto_id INTEGER NOT NULL REFERENCES productos(id), fecha TEXT NOT NULL,
  tipo TEXT NOT NULL,           -- entrada (producción/compra) | salida (venta) | ajuste
  cantidad INTEGER NOT NULL,    -- positivo entra, negativo sale
  orden_id INTEGER REFERENCES ordenes(id), nota TEXT, usuario_id INTEGER REFERENCES usuarios(id), creado_en TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS fotos (
  id INTEGER PRIMARY KEY, archivo TEXT NOT NULL, cliente_id INTEGER REFERENCES clientes(id), orden_id INTEGER REFERENCES ordenes(id), producto_id INTEGER REFERENCES productos(id),
  canal TEXT DEFAULT 'whatsapp', permiso TEXT DEFAULT 'sin_confirmar',   -- sin_confirmar | autorizado | publicado | no_usar
  instagram TEXT, enlace_publicacion TEXT, nota TEXT, fecha TEXT, usuario_id INTEGER REFERENCES usuarios(id), creado_en TEXT DEFAULT (datetime('now','localtime')));

-- ---------- PRODUCCIÓN
-- produccion.tipo_pedido: 'produccion' (madera: Walter, David) o 'proveedor' (grama, bowls, cartón…)
CREATE TABLE IF NOT EXISTS produccion (
  id INTEGER PRIMARY KEY, producto_id INTEGER REFERENCES productos(id),   -- NULL en muestras/prototipos (todavía no son producto) cantidad INTEGER NOT NULL, recibido INTEGER DEFAULT 0,
  fecha_pedido TEXT NOT NULL, fecha_esperada TEXT, responsable TEXT, costo REAL, nota TEXT,
  estado TEXT DEFAULT 'en_proceso',   -- en_proceso | recibido | cancelado
  usuario_id INTEGER REFERENCES usuarios(id), creado_en TEXT DEFAULT (datetime('now','localtime')), recibido_en TEXT,
  pieza TEXT,    -- lo que se manda a hacer al carpintero
  pagado INTEGER NOT NULL DEFAULT 0,   -- costo: cantidad × precio del proveedor (Taller › Proveedores); pagado=1 cuando se le pagó al carpintero
  barnizado INTEGER NOT NULL DEFAULT 0,   -- cajas de madera: 1 si van barnizadas (se suma el barnizado por caja al monto)
  descripcion TEXT);   -- muestras: qué se está haciendo (caja de madera, comedor, rampa); las cajas no entran al inventario de producto terminado

-- ---------- PAGOS RECURRENTES (compromisos)
-- compromisos.unidad/precio_unitario: para los fijos que se pagan por cantidad (grama por saco)
CREATE TABLE IF NOT EXISTS compromisos (
  id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, categoria TEXT, subcategoria TEXT, proveedor TEXT, monto REAL, moneda TEXT DEFAULT 'USD',
  frecuencia TEXT NOT NULL,     -- semanal | quincenal | mensual
  dia INTEGER,                  -- semanal: 0=lunes..6=domingo · mensual: día del mes (quincenal: 15 y último)
  cuenta_id INTEGER REFERENCES cuentas(id), activo INTEGER DEFAULT 1, nota TEXT, creado_en TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS compromisos_pagos (
  id INTEGER PRIMARY KEY, compromiso_id INTEGER NOT NULL REFERENCES compromisos(id) ON DELETE CASCADE, vence TEXT NOT NULL, gasto_id INTEGER REFERENCES gastos(id),
  pagado_en TEXT DEFAULT (datetime('now','localtime')), UNIQUE(compromiso_id, vence));

-- Repuestos sueltos que el cliente pagó y dejó para entregar después (no son packs)
CREATE TABLE IF NOT EXISTS repuestos_prepagados (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL REFERENCES clientes(id), orden_id INTEGER REFERENCES ordenes(id), linea_id INTEGER REFERENCES orden_lineas(id),
  producto_id INTEGER REFERENCES productos(id), tamano TEXT, pagado_en TEXT, fecha_programada TEXT, tipo_entrega TEXT, despachador TEXT,
  entregado_en TEXT, notas TEXT, usuario_id INTEGER REFERENCES usuarios(id), creado_en TEXT DEFAULT (datetime('now','localtime')));

-- Registro de ventas: histórico del Excel (una fila por línea vendida). Las órdenes nuevas se suman solas al mostrarlo.
CREATE TABLE IF NOT EXISTS registro_ventas (
  id INTEGER PRIMARY KEY, fecha TEXT NOT NULL, cliente TEXT, producto TEXT, precio REAL, cantidad REAL DEFAULT 1, facturacion REAL,
  forma_pago TEXT, origen TEXT DEFAULT 'excel', fila_excel INTEGER, creado_en TEXT DEFAULT (datetime('now','localtime')));

-- ---------- DESPACHADORES (quién entrega los deliveries en Caracas)
CREATE TABLE IF NOT EXISTS despachadores (
  id INTEGER PRIMARY KEY, nombre TEXT NOT NULL UNIQUE, telefono TEXT, activo INTEGER NOT NULL DEFAULT 1, notas TEXT,
  creado_en TEXT DEFAULT (datetime('now','localtime')));

-- ---------- TARIFAS DE DELIVERY por zona (Caracas y alrededores)
CREATE TABLE IF NOT EXISTS tarifas (
  id INTEGER PRIMARY KEY, zona TEXT NOT NULL UNIQUE, tarifa REAL NOT NULL DEFAULT 0,   -- lo que paga el cliente
  pago_despachador REAL,   -- lo que se le paga al despachador por ir a esa zona (si es distinto)
  notas TEXT, orden INTEGER DEFAULT 0);
-- ordenes.pago_despachador: lo que se le debe al despachador por esa orden (se fija al asignar zona/despachador)

-- ---------- PAGOS A DESPACHADORES: cada entrega genera lo que se le debe (el monto del delivery); al pagarle se marcan las órdenes
CREATE TABLE IF NOT EXISTS pagos_despachador (
  id INTEGER PRIMARY KEY, despachador TEXT NOT NULL, fecha TEXT NOT NULL, monto REAL NOT NULL, entregas INTEGER NOT NULL DEFAULT 0,
  nota TEXT, usuario_id INTEGER, creado_en TEXT DEFAULT (datetime('now','localtime')));
-- ordenes.despachador_pagado (0/1) y ordenes.despachador_pago_id → pagos_despachador.id

-- ---------- PROVEEDORES (a quién le compramos la grama, las cajas, la madera…)
CREATE TABLE IF NOT EXISTS proveedores (
  id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, que_vende TEXT, contacto TEXT, telefono TEXT, correo TEXT, ciudad TEXT, direccion TEXT,
  forma_pago TEXT, precios TEXT,      -- (ya no se usa: los precios van en proveedor_items)
  notas TEXT, activo INTEGER NOT NULL DEFAULT 1, creado_en TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS proveedor_items (   -- qué nos vende cada proveedor y a qué precio
  id INTEGER PRIMARY KEY, proveedor_id INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE CASCADE,
  item TEXT NOT NULL, precio REAL, unidad TEXT, orden INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS abonos_produccion (   -- lo que se le va abonando al carpintero por cada pedido de producción
  id INTEGER PRIMARY KEY, produccion_id INTEGER NOT NULL REFERENCES produccion(id) ON DELETE CASCADE,
  fecha TEXT NOT NULL, monto REAL NOT NULL, forma TEXT, nota TEXT, usuario_id INTEGER, creado_en TEXT DEFAULT (datetime('now','localtime')));
-- repuestos_prepagados: delivery (monto), delivery_pagado (0 por cobrar / 1 ya pagado), delivery_forma, monto (lo que pagó por el repuesto)
-- gastos.compra_grande: 1 en compras que duran varios meses (importaciones, lotes trimestrales); Resultados las separa para poder comparar meses

-- Historial mensual traído del Excel "Cash Flow 2026 · Resumen Mensual".
-- Ganancia = Facturación − Gastos (antes del sueldo). Entrada = Ganancia − Sueldo.
CREATE TABLE IF NOT EXISTS resultados_mes (
  mes         TEXT PRIMARY KEY,      -- 'YYYY-MM'
  facturacion REAL DEFAULT 0,
  unidades    INTEGER DEFAULT 0,
  gastos      REAL DEFAULT 0,
  sueldo      REAL DEFAULT 0,
  arrastre    REAL DEFAULT 0,       -- gastos pagados este mes que son de meses anteriores (TDC, importaciones)
  contexto    TEXT,                 -- qué pasó ese mes (terremoto, feria, etc.)
  nota        TEXT
);

-- Un viaje del despachador a la agencia: lleva varios pedidos de envío nacional de una vez
-- y se le paga por el viaje, no por pedido (Tealca $10, cualquier otra $5).
CREATE TABLE IF NOT EXISTS viajes_agencia (
  id INTEGER PRIMARY KEY, fecha TEXT NOT NULL, despachador TEXT NOT NULL, agencia TEXT,
  monto REAL NOT NULL DEFAULT 0, pedidos INTEGER NOT NULL DEFAULT 0,
  pagado INTEGER NOT NULL DEFAULT 0, pago_id INTEGER REFERENCES pagos_despachador(id),
  nota TEXT, usuario_id INTEGER, creado_en TEXT DEFAULT (datetime('now','localtime')));

-- Avisos que deja el taller: "llegaron 2 partidas", "se acabó la cinta". Los lee Cristina en Inicio.
CREATE TABLE IF NOT EXISTS notas_taller (
  id INTEGER PRIMARY KEY, fecha TEXT NOT NULL, texto TEXT NOT NULL,
  usuario_id INTEGER, visto INTEGER NOT NULL DEFAULT 0,
  creado_en TEXT DEFAULT (datetime('now','localtime')));

-- Días que Isaías o Manawa no vinieron: se le descuenta el día de la quincena.
CREATE TABLE IF NOT EXISTS faltas (
  id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, fecha TEXT NOT NULL, nota TEXT,
  usuario_id INTEGER, creado_en TEXT DEFAULT (datetime('now','localtime')),
  UNIQUE(nombre, fecha));

-- Un vencimiento que se salta (no tocaba pagarlo ese mes) queda anotado con su motivo.
-- gasto_id NULL + motivo = saltado; gasto_id lleno = pagado.

-- Fotos extra de un producto (además de la principal), con o sin fondo.
CREATE TABLE IF NOT EXISTS producto_fotos (
  id INTEGER PRIMARY KEY, producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
  archivo TEXT NOT NULL, etiqueta TEXT, orden INTEGER DEFAULT 0,
  creado_en TEXT DEFAULT (datetime('now','localtime')), tipo TEXT NOT NULL DEFAULT 'sin_fondo');

-- De qué está hecho cada producto: el Porche PRO Mediano gasta 1 caja de madera mediana.
CREATE TABLE IF NOT EXISTS receta (
  producto_id INTEGER NOT NULL REFERENCES productos(id),
  insumo_id   INTEGER NOT NULL REFERENCES productos(id),
  cantidad    REAL NOT NULL DEFAULT 1,
  PRIMARY KEY (producto_id, insumo_id));

-- Tasa del BCV por día, con la del próximo día hábil que es la que se cobra.
CREATE TABLE IF NOT EXISTS tasas (
  id INTEGER PRIMARY KEY, fecha_valor TEXT NOT NULL, valor REAL NOT NULL, fuente TEXT NOT NULL,
  obtenido_en TEXT DEFAULT (datetime('now','localtime')), manual INTEGER DEFAULT 0,
  usuario_id INTEGER, nota TEXT, UNIQUE(fecha_valor, fuente));

-- Quién está conectado. El navegador guarda solo una ficha al azar; la clave nunca sale de aquí.
CREATE TABLE IF NOT EXISTS sesiones (
  ficha TEXT PRIMARY KEY, usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
  creada_en TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  vence_en TEXT NOT NULL, visto_en TEXT);


-- Saldo a favor del cliente: cuando paga de más porque no hay vuelto, o cuando queda
-- un resto que no alcanza para un repuesto. Positivo = se le debe; negativo = lo usó.
CREATE TABLE IF NOT EXISTS credito_cliente (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL REFERENCES clientes(id),
  fecha TEXT NOT NULL, monto REAL NOT NULL, motivo TEXT,
  orden_id INTEGER REFERENCES ordenes(id), usuario_id INTEGER,
  creado_en TEXT DEFAULT (datetime('now','localtime')));

-- Intentos de entrar fallidos: para frenar a un robot que pruebe claves sin parar.
CREATE TABLE IF NOT EXISTS intentos (
  id INTEGER PRIMARY KEY, usuario TEXT, ip TEXT, cuando TEXT NOT NULL DEFAULT (datetime('now','localtime')));
