-- La pega amarilla se cuenta por envase, como la cuentan en el taller (Cristina, 7 oct 2026): cuñete, galón y ¼ de galón.
-- Antes era un solo producto en litros (INS-PEGA). Si tiene algo cargado queda visible para pasarlo a su presentación;
-- si está en cero se apaga.
INSERT OR IGNORE INTO productos (sku, nombre, categoria, tipo, unidad, activo, orden)
  SELECT 'INS-PEGA-CUN', 'Pega amarilla · cuñete', 'insumo', 'insumo', 'cuñete', 1, 902 WHERE EXISTS (SELECT 1 FROM productos WHERE sku = 'INS-PEGA');
INSERT OR IGNORE INTO productos (sku, nombre, categoria, tipo, unidad, activo, orden)
  SELECT 'INS-PEGA-GAL', 'Pega amarilla · galón', 'insumo', 'insumo', 'galón', 1, 902 WHERE EXISTS (SELECT 1 FROM productos WHERE sku = 'INS-PEGA');
INSERT OR IGNORE INTO productos (sku, nombre, categoria, tipo, unidad, activo, orden)
  SELECT 'INS-PEGA-14', 'Pega amarilla · ¼ de galón', 'insumo', 'insumo', 'unidad', 1, 902 WHERE EXISTS (SELECT 1 FROM productos WHERE sku = 'INS-PEGA');
-- Lo que Isaías cargó el 7/10 como "10 litros" era 1 cuñete (Cristina): pasa a su envase.
INSERT INTO mov_inventario (producto_id, fecha, tipo, cantidad, nota, usuario_id)
  SELECT (SELECT id FROM productos WHERE sku = 'INS-PEGA-CUN'), date('now','localtime'), 'entrada', 1,
         'conteo del 7/10: estaba anotado como 10 litros, era 1 cuñete', (SELECT usuario_id FROM mov_inventario m JOIN productos p ON p.id = m.producto_id WHERE p.sku = 'INS-PEGA' LIMIT 1)
   WHERE (SELECT SUM(m.cantidad) FROM mov_inventario m JOIN productos p ON p.id = m.producto_id WHERE p.sku = 'INS-PEGA') = 10;
DELETE FROM mov_inventario
 WHERE producto_id = (SELECT id FROM productos WHERE sku = 'INS-PEGA')
   AND (SELECT SUM(m.cantidad) FROM mov_inventario m JOIN productos p ON p.id = m.producto_id WHERE p.sku = 'INS-PEGA') = 10
   AND EXISTS (SELECT 1 FROM mov_inventario m JOIN productos p ON p.id = m.producto_id WHERE p.sku = 'INS-PEGA-CUN' AND m.nota LIKE 'conteo del 7/10%');
UPDATE productos SET activo = 0
 WHERE sku = 'INS-PEGA' AND COALESCE((SELECT SUM(m.cantidad) FROM mov_inventario m WHERE m.producto_id = productos.id), 0) = 0;
UPDATE productos SET nombre = 'Pega amarilla (en litros) · pasar a su envase'
 WHERE sku = 'INS-PEGA' AND activo = 1;
