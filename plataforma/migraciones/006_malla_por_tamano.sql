-- La malla se deja lista por tamaño (Cristina, 7 oct 2026): mediana para los porches medianos, grande para los grandes.
-- La "Malla" de la 004 pasa a ser la mediana (todavía no tenía nada cargado) y se agrega la grande.
UPDATE productos SET sku = 'INS-MALLA-M', nombre = 'Malla mediana' WHERE sku = 'INS-MALLA';
INSERT OR IGNORE INTO productos (sku, nombre, categoria, tipo, unidad, activo, orden)
  SELECT 'INS-MALLA-G', 'Malla grande', 'insumo', 'insumo', 'unidad', 1, 906 WHERE EXISTS (SELECT 1 FROM productos WHERE sku = 'INS-MALLA-M');

-- La malla que se vende suelta también va por tamaño: la de siempre queda como la mediana y se agrega la grande, igual en todo lo demás.
UPDATE productos SET nombre = 'Malla de seguridad Mediana' WHERE sku = 'MALLA';
INSERT OR IGNORE INTO productos (sku, nombre, categoria, variante, descripcion, precio, precio_par, costo, activo, orden, tipo, requiere_color,
                                 permite_malla, permite_personalizacion, stock, minimo, foto, descripcion_larga, peso_kg, foto_rosado, canales, unidad, proveedor)
  SELECT 'MALLA-G', 'Malla de seguridad Grande', categoria, variante, descripcion, precio, precio_par, costo, activo, orden + 0.5, tipo, requiere_color,
         permite_malla, permite_personalizacion, stock, minimo, foto, descripcion_larga, peso_kg, foto_rosado, canales, unidad, proveedor
  FROM productos WHERE sku = 'MALLA';
