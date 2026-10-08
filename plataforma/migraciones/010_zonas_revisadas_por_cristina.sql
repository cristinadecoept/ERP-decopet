-- Zonas de la dirección habitual que Cristina revisó una por una en la lista (8 oct 2026).
-- Solo las que ya existen en Tarifas; las zonas nuevas se agregan aparte con su precio.
UPDATE direcciones SET zona = 'El Marqués' WHERE id IN (23, 314);
UPDATE direcciones SET zona = 'Los Palos Grandes' WHERE id = 302;
UPDATE direcciones SET zona = 'Lomas de Avila' WHERE id IN (364, 269, 311);
UPDATE direcciones SET zona = 'Los Jardines del Valle' WHERE id IN (257, 334);
UPDATE direcciones SET zona = 'Colinas de La Tahona' WHERE id = 26;
UPDATE direcciones SET zona = 'El Paraiso' WHERE id IN (173, 343);
UPDATE direcciones SET zona = 'La Florida' WHERE id = 162;
UPDATE direcciones SET zona = 'Petare' WHERE id = 99;
UPDATE direcciones SET zona = 'Los Caobos' WHERE id = 253;
UPDATE direcciones SET zona = 'Bello Monte' WHERE id = 228;
UPDATE direcciones SET zona = 'El Llanito' WHERE id = 190;
UPDATE direcciones SET zona = 'La Miranda' WHERE id = 3;
UPDATE direcciones SET zona = 'El Panteón' WHERE id IN (277, 240);
UPDATE direcciones SET zona = 'El Rosal' WHERE id = 145;

-- Zonas nuevas en Tarifas, con el precio que dio Cristina (todas de Caracas).
INSERT OR IGNORE INTO tarifas (zona, tarifa) SELECT 'Colinas de Tamanaco', 5 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa) SELECT 'Ciudad Tiuna', 12 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa) SELECT 'Avenida Fuerzas Armadas', 5 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa) SELECT 'Vizcaya', 5 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa) SELECT 'Las Marías', 6 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
UPDATE direcciones SET zona = 'Colinas de Tamanaco' WHERE id = 320;
UPDATE direcciones SET zona = 'Ciudad Tiuna' WHERE id = 351;
UPDATE direcciones SET zona = 'Avenida Fuerzas Armadas' WHERE id = 232;
UPDATE direcciones SET zona = 'Vizcaya' WHERE id = 211;
UPDATE direcciones SET zona = 'Las Marías' WHERE id = 98;
-- "Francisco Solano" queda en Sabana Grande.
UPDATE direcciones SET zona = 'Sabana Grande' WHERE id = 379 OR zona = 'Francisco Solano';
-- "Avenida Francisco de Miranda" es la zona Francisco de Miranda que ya estaba en Tarifas.
UPDATE direcciones SET zona = 'Francisco de Miranda' WHERE id = 361;
