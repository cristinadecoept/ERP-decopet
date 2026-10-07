-- Comedores y El Bar con su medida en el nombre, para no confundirlos al contar el inventario (Cristina, 7 oct 2026).
-- Solo cambia el nombre: SKU, precio, inventario y pedidos ya hechos quedan igual.
UPDATE productos SET nombre = 'Comedor Mini 10 cm'     WHERE sku = 'COM-10';
UPDATE productos SET nombre = 'Comedor Pequeño 15 cm'  WHERE sku = 'COM-15';
UPDATE productos SET nombre = 'Comedor Mediano 20 cm'  WHERE sku = 'COM-20';
UPDATE productos SET nombre = 'El Bar Grande 25 cm'    WHERE sku = 'BAR-25';
UPDATE productos SET nombre = 'El Bar Gigante 30 cm'   WHERE sku = 'BAR-30';
