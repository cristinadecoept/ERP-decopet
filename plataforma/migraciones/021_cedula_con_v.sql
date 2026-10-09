-- Las cédulas guardadas solo con el número llevan su V adelante, como las demás: 'V-12345678' (Cristina, 9 oct 2026).
UPDATE clientes SET cedula = 'V-' || replace(trim(cedula), '.', '')
 WHERE trim(cedula) GLOB '[0-9]*' AND replace(trim(cedula), '.', '') NOT GLOB '*[^0-9]*';
UPDATE ordenes SET receptor_cedula = 'V-' || replace(trim(receptor_cedula), '.', '')
 WHERE trim(receptor_cedula) GLOB '[0-9]*' AND replace(trim(receptor_cedula), '.', '') NOT GLOB '*[^0-9]*';
