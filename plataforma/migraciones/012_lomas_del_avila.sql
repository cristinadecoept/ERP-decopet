-- La zona se llama "Lomas del Ávila" (estaba como "Lomas de Avila") y así es como se escribe en las direcciones.
UPDATE tarifas SET zona = 'Lomas del Ávila' WHERE zona = 'Lomas de Avila' AND NOT EXISTS (SELECT 1 FROM tarifas WHERE zona = 'Lomas del Ávila');
UPDATE direcciones SET zona = 'Lomas del Ávila' WHERE zona = 'Lomas de Avila';
UPDATE ordenes SET zona = 'Lomas del Ávila' WHERE zona = 'Lomas de Avila';
