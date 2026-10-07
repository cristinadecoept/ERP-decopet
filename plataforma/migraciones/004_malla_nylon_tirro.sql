-- Materiales que se cuentan en el inventario (Cristina, 7 oct 2026): malla y nylon por unidad, tirro por rollo.
-- INS-MALLA es el que el ERP ya descuenta al vender un porche con malla (antes no existía y no se descontaba nada).
-- Solo donde ya está el catálogo de materiales (una base recién creada arranca vacía y los trae la semilla o el usuario).
INSERT OR IGNORE INTO productos (sku, nombre, categoria, tipo, unidad, activo, orden)
  SELECT 'INS-MALLA', 'Malla', 'insumo', 'insumo', 'unidad', 1, 906 WHERE EXISTS (SELECT 1 FROM productos WHERE sku = 'INS-CAJAM');
INSERT OR IGNORE INTO productos (sku, nombre, categoria, tipo, unidad, activo, orden)
  SELECT 'INS-NYLON', 'Nylon', 'insumo', 'insumo', 'unidad', 1, 906 WHERE EXISTS (SELECT 1 FROM productos WHERE sku = 'INS-CAJAM');
INSERT OR IGNORE INTO productos (sku, nombre, categoria, tipo, unidad, activo, orden)
  SELECT 'INS-TIRRO', 'Tirro', 'insumo', 'insumo', 'rollo', 1, 906 WHERE EXISTS (SELECT 1 FROM productos WHERE sku = 'INS-CAJAM');
