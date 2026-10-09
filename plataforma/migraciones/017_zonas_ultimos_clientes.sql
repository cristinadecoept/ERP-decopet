-- Zonas de los últimos clientes que Cristina revisó (8 oct 2026). Solo las que existen en Tarifas.

UPDATE direcciones SET zona = 'Bello Monte' WHERE id IN (93, 141);
UPDATE direcciones SET zona = 'Campo Alegre' WHERE id = 97;
UPDATE direcciones SET zona = 'Catia' WHERE id = 307;
UPDATE direcciones SET zona = 'Cumbres de Curumo' WHERE id = 252;
UPDATE direcciones SET zona = 'El Hatillo' WHERE id = 47;
UPDATE direcciones SET zona = 'El Marqués' WHERE id = 146;
UPDATE direcciones SET zona = 'El Paraiso' WHERE id = 111;
UPDATE direcciones SET zona = 'Horizonte' WHERE id = 287;
UPDATE direcciones SET zona = 'La California' WHERE id = 283;
UPDATE direcciones SET zona = 'La Candelaria' WHERE id = 165;
UPDATE direcciones SET zona = 'La Florida' WHERE id IN (157, 229, 327);
UPDATE direcciones SET zona = 'Las Acacias' WHERE id = 254;
UPDATE direcciones SET zona = 'Quinta Crespo' WHERE id = 51;
UPDATE direcciones SET zona = 'San Martín' WHERE id = 363;
UPDATE direcciones SET zona = 'Santa Eduvigis' WHERE id = 35;
UPDATE direcciones SET zona = 'Santa Fe' WHERE id IN (70, 323, 337);

-- En Tarifas "Av Panteon" pasó a llamarse "Avenida Panteon": los clientes y pedidos con el nombre viejo se ponen al día.
UPDATE direcciones SET zona = 'Avenida Panteon' WHERE zona = 'Av Panteon' AND EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Avenida Panteon');
UPDATE ordenes SET zona = 'Avenida Panteon' WHERE zona = 'Av Panteon' AND EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Avenida Panteon');
