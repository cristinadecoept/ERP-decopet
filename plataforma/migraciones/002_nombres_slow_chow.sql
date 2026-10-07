-- Los Slow Chow se llaman por su medida (Cristina, 7 oct 2026): 10 cm Pequeño, 15 cm Mediano, 25 cm Grande, 30 cm Gigante.
-- Antes estaban corridos (10 cm "Mini", 15 cm "Pequeño", 20 cm "Mediano"). Solo cambia el nombre del producto: el SKU, el
-- precio, el inventario y los pedidos que ya se hicieron (que guardan el nombre con que se vendieron) quedan igual.
UPDATE productos SET nombre = 'Slow Chow Pequeño 10 cm' WHERE sku = 'SLOW-10';
UPDATE productos SET nombre = 'Slow Chow Mediano 15 cm' WHERE sku = 'SLOW-15';
UPDATE productos SET nombre = 'Slow Chow Grande 25 cm'  WHERE sku = 'SLOW-20';
UPDATE productos SET nombre = 'Slow Chow Gigante 30 cm' WHERE sku = 'SLOW-30';
UPDATE productos SET nombre = 'Base Slow Chow Pequeño 10 cm' WHERE sku = 'INS-SLOW10';
UPDATE productos SET nombre = 'Base Slow Chow Mediano 15 cm' WHERE sku = 'INS-SLOW15';
UPDATE productos SET nombre = 'Base Slow Chow Grande 25 cm'  WHERE sku = 'INS-SLOW20';
UPDATE productos SET nombre = 'Base Slow Chow Gigante 30 cm' WHERE sku = 'INS-SLOW30';
