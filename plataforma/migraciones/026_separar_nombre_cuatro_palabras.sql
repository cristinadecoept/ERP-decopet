-- Nombre y apellido, segunda parte (Cristina, 12 nov 2026): los que se escribieron con cuatro palabras en Nombre y el
-- Apellido vacío ('Cristina Alejandra Raffalli Gomez') → dos al nombre y dos al apellido. Si la segunda palabra es
-- 'de', 'del', 'la'… ('María de los Ángeles…') no se toca: se completa a mano desde Clientes › Datos pendientes › Apellido.
-- La columna 'nombre' (el nombre completo) no cambia.
CREATE TEMP TABLE _partir4 AS
SELECT id, TRIM(nombre_pila) np FROM clientes
WHERE TRIM(COALESCE(apellido, '')) = ''
  AND length(TRIM(nombre_pila)) - length(replace(TRIM(nombre_pila), ' ', '')) = 3;

CREATE TEMP TABLE _partes4 AS
SELECT id,
       substr(np, 1, instr(np, ' ') - 1) w1,
       substr(np, instr(np, ' ') + 1) r1
FROM _partir4;

CREATE TEMP TABLE _partes4b AS
SELECT id, w1,
       substr(r1, 1, instr(r1, ' ') - 1) w2,
       substr(r1, instr(r1, ' ') + 1) apellidos
FROM _partes4;

UPDATE clientes SET
  nombre_pila = (SELECT w1 || ' ' || w2 FROM _partes4b p WHERE p.id = clientes.id),
  apellido    = (SELECT apellidos FROM _partes4b p WHERE p.id = clientes.id)
WHERE id IN (SELECT id FROM _partes4b WHERE lower(w2) NOT IN ('de', 'del', 'la', 'las', 'los', 'y'));

DROP TABLE _partir4; DROP TABLE _partes4; DROP TABLE _partes4b;
