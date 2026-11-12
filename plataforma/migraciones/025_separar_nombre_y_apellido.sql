-- Nombre y apellido (Cristina, 12 nov 2026): si se escribió todo en Nombre y el Apellido quedó vacío, el nombre va a
-- Nombre y el apellido a Apellido ('Andrea González' → 'Andrea' + 'González'). Desde ahora lo hace el ERP al guardar;
-- esto arregla a los clientes que ya quedaron así. Solo nombres de dos o tres palabras (con cuatro o más no se toca).
-- Con tres palabras, si empieza por un nombre que suele ser compuesto (María José…), van dos al nombre y una al apellido.
-- La columna 'nombre' (el nombre completo) no cambia.
CREATE TEMP TABLE _partir AS
SELECT id, TRIM(nombre_pila) np,
       substr(TRIM(nombre_pila), 1, instr(TRIM(nombre_pila), ' ') - 1) w1,
       substr(TRIM(nombre_pila), instr(TRIM(nombre_pila), ' ') + 1) resto
FROM clientes
WHERE TRIM(COALESCE(apellido, '')) = '' AND instr(TRIM(COALESCE(nombre_pila, '')), ' ') > 0;

-- dos palabras: una y una
UPDATE clientes SET nombre_pila = (SELECT w1 FROM _partir p WHERE p.id = clientes.id),
                    apellido    = (SELECT resto FROM _partir p WHERE p.id = clientes.id)
WHERE id IN (SELECT id FROM _partir WHERE instr(resto, ' ') = 0);

-- tres palabras
UPDATE clientes SET
  nombre_pila = (SELECT CASE WHEN w1 IN ('María','Maria','José','Jose','Juan','Ana','Luis','Carlos','Jesús','Jesus','Luz','Rosa','Miguel','Pedro','Manuel','Mary','Ángel','Angel')
                             THEN w1 || ' ' || substr(resto, 1, instr(resto, ' ') - 1) ELSE w1 END FROM _partir p WHERE p.id = clientes.id),
  apellido    = (SELECT CASE WHEN w1 IN ('María','Maria','José','Jose','Juan','Ana','Luis','Carlos','Jesús','Jesus','Luz','Rosa','Miguel','Pedro','Manuel','Mary','Ángel','Angel')
                             THEN substr(resto, instr(resto, ' ') + 1) ELSE resto END FROM _partir p WHERE p.id = clientes.id)
WHERE id IN (SELECT id FROM _partir WHERE instr(resto, ' ') > 0 AND instr(substr(resto, instr(resto, ' ') + 1), ' ') = 0);

DROP TABLE _partir;
