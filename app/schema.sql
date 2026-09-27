-- ============================================================
--  DECOPET ERP — Esqueleto de la base de datos (v1.0)
--  Cada bloque es una "tabla": una lista de registros del mismo tipo.
-- ============================================================

PRAGMA foreign_keys = ON;

-- ---------- CLIENTES Y MASCOTAS ----------
CREATE TABLE IF NOT EXISTS clientes (
  id            INTEGER PRIMARY KEY,
  nombre        TEXT NOT NULL,
  telefono      TEXT,
  correo        TEXT,
  ciudad        TEXT,              -- Caracas / otra ciudad
  zona          TEXT,              -- zona de Caracas (para delivery)
  direccion     TEXT,
  canal_origen  TEXT,              -- whatsapp, cashea, duwu, vidapets, shopify, instagram, referido
  notas         TEXT,
  airtable_id   TEXT UNIQUE,       -- para sincronizar con Airtable (Tina)
  creado_en     TEXT DEFAULT (datetime('now','localtime')),
  actualizado_en TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS mascotas (
  id               INTEGER PRIMARY KEY,
  cliente_id       INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  nombre           TEXT NOT NULL,
  raza             TEXT,
  fecha_nacimiento TEXT,           -- AAAA-MM-DD
  tamano           TEXT,           -- mini / mediano / grande
  notas            TEXT
);

-- ---------- CATÁLOGO ----------
CREATE TABLE IF NOT EXISTS productos (
  id            INTEGER PRIMARY KEY,
  sku           TEXT UNIQUE NOT NULL,   -- código corto interno
  nombre        TEXT NOT NULL,
  categoria     TEXT NOT NULL,          -- porche, repuesto, rampa, comedor, extra, servicio
  precio        REAL,                   -- precio actual en USD (NULL = variable, ej. delivery)
  activo        INTEGER DEFAULT 1,      -- 0 = ya no se vende (ej. bandana)
  es_repuesto   INTEGER DEFAULT 0,      -- sirve para calcular ciclo de recompra
  es_porche_pro INTEGER DEFAULT 0,      -- habilita recompra de repuestos
  stock         INTEGER DEFAULT 0,      -- unidades terminadas disponibles
  orden         INTEGER DEFAULT 100     -- orden de aparición en listas
);

-- Nombres viejos / variantes con los que se escribió el mismo producto en los Excel.
CREATE TABLE IF NOT EXISTS producto_alias (
  alias       TEXT PRIMARY KEY,
  producto_id INTEGER NOT NULL REFERENCES productos(id)
);

-- ---------- PEDIDOS ----------
CREATE TABLE IF NOT EXISTS pedidos (
  id             INTEGER PRIMARY KEY,
  numero         TEXT UNIQUE,            -- ej. P-2026-00123
  fecha          TEXT NOT NULL,          -- fecha del pedido AAAA-MM-DD
  cliente_id     INTEGER REFERENCES clientes(id),
  canal          TEXT DEFAULT 'whatsapp',-- whatsapp, cashea, duwu, vidapets, shopify, otro
  estado         TEXT DEFAULT 'pendiente_pago',
     -- pendiente_pago -> pagado -> en_preparacion -> asignado -> en_camino -> entregado | cancelado
  tipo_entrega   TEXT,                   -- delivery_caracas, pickup, nacional
  agencia        TEXT,                   -- Tealca, MRW, Zoom... (envío nacional)
  despachador_id INTEGER REFERENCES despachadores(id),
  fecha_entrega  TEXT,
  subtotal       REAL DEFAULT 0,         -- suma de productos
  delivery       REAL DEFAULT 0,         -- costo de envío cobrado
  total          REAL DEFAULT 0,         -- lo que paga el cliente (USD referencia)
  notas          TEXT,
  resumen_despacho TEXT,                 -- texto listo para mandar al despachador
  airtable_id    TEXT UNIQUE,
  origen         TEXT DEFAULT 'manual',  -- manual, airtable, importado_excel
  creado_en      TEXT DEFAULT (datetime('now','localtime')),
  actualizado_en TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS pedido_items (
  id              INTEGER PRIMARY KEY,
  pedido_id       INTEGER NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
  producto_id     INTEGER REFERENCES productos(id),
  descripcion     TEXT,                  -- texto libre si no matchea un producto
  cantidad        REAL DEFAULT 1,
  precio_unitario REAL DEFAULT 0,
  total           REAL DEFAULT 0,
  personalizacion TEXT                   -- nombre del perro grabado, etc.
);

-- ---------- PAGOS Y CAJA ----------
CREATE TABLE IF NOT EXISTS cajas (
  id      INTEGER PRIMARY KEY,
  nombre  TEXT UNIQUE NOT NULL,          -- Caja Cris, Zelle Decopet, Binance, ...
  moneda  TEXT DEFAULT 'USD',            -- USD, VES, EUR, USDT
  saldo_inicial REAL DEFAULT 0,          -- saldo al momento de empezar a usar el ERP (fecha de corte)
  activa  INTEGER DEFAULT 1,
  orden   INTEGER DEFAULT 100
);

CREATE TABLE IF NOT EXISTS metodos_pago (
  id              INTEGER PRIMARY KEY,
  nombre          TEXT UNIQUE NOT NULL,  -- Pago Móvil, Zelle, Cashea, Efectivo USD ...
  moneda_recibida TEXT DEFAULT 'USD',    -- en qué moneda entra realmente el dinero
  caja_id         INTEGER REFERENCES cajas(id),  -- a qué caja entra por defecto
  activo          INTEGER DEFAULT 1,
  orden           INTEGER DEFAULT 100
);

CREATE TABLE IF NOT EXISTS metodo_pago_alias (
  alias     TEXT PRIMARY KEY,
  metodo_id INTEGER NOT NULL REFERENCES metodos_pago(id)
);

CREATE TABLE IF NOT EXISTS pagos (
  id           INTEGER PRIMARY KEY,
  pedido_id    INTEGER NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
  fecha        TEXT NOT NULL,
  metodo_id    INTEGER REFERENCES metodos_pago(id),
  caja_id      INTEGER REFERENCES cajas(id),
  monto_usd    REAL NOT NULL,            -- valor en USD de referencia
  monto_moneda REAL,                     -- monto real recibido (ej. bolívares)
  moneda       TEXT DEFAULT 'USD',
  tasa         REAL,                     -- tasa usada si fue en bolívares
  referencia   TEXT,                     -- nro de comprobante
  confirmado   INTEGER DEFAULT 1,
  notas        TEXT
);

CREATE TABLE IF NOT EXISTS movimientos_caja (
  id         INTEGER PRIMARY KEY,
  caja_id    INTEGER NOT NULL REFERENCES cajas(id),
  fecha      TEXT NOT NULL,
  tipo       TEXT NOT NULL,              -- ingreso, egreso, transferencia_in, transferencia_out, ajuste
  monto_usd  REAL NOT NULL,
  concepto   TEXT,
  pago_id    INTEGER REFERENCES pagos(id) ON DELETE SET NULL,
  gasto_id   INTEGER REFERENCES gastos(id) ON DELETE SET NULL
);

-- ---------- GASTOS ----------
CREATE TABLE IF NOT EXISTS gastos (
  id           INTEGER PRIMARY KEY,
  fecha        TEXT NOT NULL,
  categoria    TEXT NOT NULL,            -- Compras/Proveedores, Producción, Sueldos, ...
  subcategoria TEXT,
  descripcion  TEXT,
  proveedor    TEXT,
  cantidad     REAL,
  monto_usd    REAL NOT NULL,
  moneda       TEXT DEFAULT 'USD',
  forma_pago   TEXT,
  caja_id      INTEGER REFERENCES cajas(id),
  referencia   TEXT,
  notas        TEXT
);

-- ---------- CASHEA (30% de los pedidos) ----------
CREATE TABLE IF NOT EXISTS cashea_ordenes (
  id             INTEGER PRIMARY KEY,
  pedido_id      INTEGER UNIQUE REFERENCES pedidos(id) ON DELETE CASCADE,
  referencia     TEXT,                   -- nro de orden Cashea
  monto_total    REAL,
  inicial        REAL,
  num_cuotas     INTEGER DEFAULT 3,
  estado_cobro   TEXT DEFAULT 'pendiente', -- pendiente, parcial, liquidado
  monto_cobrado  REAL DEFAULT 0,
  fecha_reporte  TEXT,                   -- mes del reporte donde apareció
  envio_gratis   INTEGER DEFAULT 0,
  notas          TEXT
);

CREATE TABLE IF NOT EXISTS cashea_cuotas (
  id          INTEGER PRIMARY KEY,
  cashea_id   INTEGER NOT NULL REFERENCES cashea_ordenes(id) ON DELETE CASCADE,
  numero      INTEGER,                   -- 0 = inicial, 1..3 cuotas
  monto       REAL,
  fecha_pago  TEXT,
  pagada      INTEGER DEFAULT 0
);

-- ---------- CONSIGNACIÓN (Vidapets) ----------
CREATE TABLE IF NOT EXISTS consignaciones (
  id           INTEGER PRIMARY KEY,
  tienda       TEXT NOT NULL,            -- Vidapets
  fecha        TEXT NOT NULL,
  tipo         TEXT NOT NULL,            -- entrega (mercancía enviada), venta_reportada, nota_cobro, pago_recibido
  producto_id  INTEGER REFERENCES productos(id),
  cantidad     REAL,
  monto_usd    REAL,
  referencia   TEXT,
  notas        TEXT
);

-- ---------- EQUIPO / DESPACHOS ----------
CREATE TABLE IF NOT EXISTS despachadores (
  id     INTEGER PRIMARY KEY,
  nombre TEXT UNIQUE NOT NULL,
  tipo   TEXT DEFAULT 'motorizado',      -- motorizado, interno (Isaías/Manawa), agencia
  activo INTEGER DEFAULT 1
);

-- ---------- INVENTARIO / PRODUCCIÓN ----------
CREATE TABLE IF NOT EXISTS movimientos_inventario (
  id          INTEGER PRIMARY KEY,
  producto_id INTEGER NOT NULL REFERENCES productos(id),
  fecha       TEXT NOT NULL,
  tipo        TEXT NOT NULL,             -- produccion, venta, ajuste, consignacion
  cantidad    REAL NOT NULL,             -- positivo entra, negativo sale
  pedido_id   INTEGER REFERENCES pedidos(id) ON DELETE SET NULL,
  notas       TEXT
);

-- ---------- CONFIGURACIÓN ----------
CREATE TABLE IF NOT EXISTS configuracion (
  clave TEXT PRIMARY KEY,
  valor TEXT
);

CREATE INDEX IF NOT EXISTS idx_pedidos_fecha ON pedidos(fecha);
CREATE INDEX IF NOT EXISTS idx_pedidos_cliente ON pedidos(cliente_id);
CREATE INDEX IF NOT EXISTS idx_pedidos_estado ON pedidos(estado);
CREATE INDEX IF NOT EXISTS idx_items_pedido ON pedido_items(pedido_id);
CREATE INDEX IF NOT EXISTS idx_pagos_pedido ON pagos(pedido_id);
CREATE INDEX IF NOT EXISTS idx_gastos_fecha ON gastos(fecha);
