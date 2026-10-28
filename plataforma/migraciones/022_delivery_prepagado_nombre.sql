-- Las líneas de deliveries prepagados de un pack se llamaban "Delivery de las próximas entregas del pack (2 × $7)"
-- y en la lista salía "1× Delivery de las próximas…": ahora se llaman "Delivery prepagado" con su cantidad y precio
-- de cada uno ("2× Delivery prepagado"). Las de "Prepagar deliveries" se guardaron con cantidad 1: se toma el número
-- que dice el nombre.
UPDATE orden_lineas
   SET cantidad = CAST(substr(nombre, instr(nombre, '(') + 1, instr(nombre, ' ×') - instr(nombre, '(') - 1) AS INTEGER)
 WHERE nombre LIKE 'Delivery de las próximas entregas del pack (%'
   AND CAST(substr(nombre, instr(nombre, '(') + 1, instr(nombre, ' ×') - instr(nombre, '(') - 1) AS INTEGER) > 0;
UPDATE orden_lineas
   SET precio = ROUND(total / cantidad, 2), nombre = 'Delivery prepagado'
 WHERE nombre LIKE 'Delivery de las próximas entregas del pack (%' AND cantidad > 0;
