-- Zonas nuevas en Tarifas con el precio que dio Cristina (8 oct 2026). Las del Tuy y Los Altos son fuera de Caracas.

INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Country Club', 5, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Montecristo', 5, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Santa Cecilia', 5, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Santa Sofía', 5, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Colinas de Los Ruices', 5, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Colinas de Valle Arriba', 5, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Lomas de San Román', 5, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Chulavista', 5, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Piedra Azul', 5, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Sorocaima', 5, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'La Rinconada', 8, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Terrazas del Club Hípico', 5, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'El Silencio', 8, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'San José', 8, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'San Juan', 8, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Los Rosales', 8, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Gato Negro', 10, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Lídice', 8, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Nueva Caracas', 8, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Los Magallanes de Catia', 10, 0 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'San Diego de los Altos', 15, 1 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Cúa', 18, 1 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Charallave', 25, 1 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Carrizal', 15, 1 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
INSERT OR IGNORE INTO tarifas (zona, tarifa, fuera_caracas) SELECT 'Ocumare del Tuy', 20, 1 WHERE EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Chacao');
