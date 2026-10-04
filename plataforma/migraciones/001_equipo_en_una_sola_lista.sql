-- El equipo pasa a ser una sola lista: la de usuarios. Antes la nómina eran dos listas de nombres en config
-- ("equipo" y "sueldos") sin relación con las cuentas. Ahora cada persona es una fila, entre o no al ERP.

-- El correo con el que se entra por Cloudflare: el que ya estaba escrito como usuario.
UPDATE usuarios SET correo = lower(trim(usuario))
 WHERE correo IS NULL AND usuario LIKE '%_@_%._%';

-- Los nombres de la nómina (los de "equipo" y los que tienen sueldo).
CREATE TEMP TABLE nomina_vieja AS
  SELECT DISTINCT trim(j.value) nombre FROM config c, json_each(c.valor) j
   WHERE c.clave = 'equipo' AND json_valid(c.valor) AND trim(j.value) != ''
  UNION
  SELECT DISTINCT trim(j.key) FROM config c, json_each(c.valor) j
   WHERE c.clave = 'sueldos' AND json_valid(c.valor) AND trim(j.key) != '';

-- Quien no tenía cuenta entra a la lista sin acceso al ERP (el contador, por ejemplo).
INSERT INTO usuarios (nombre, rol, activo, creado_en)
SELECT n.nombre, 'ninguno', 0, date('now') FROM nomina_vieja n
 WHERE NOT EXISTS (SELECT 1 FROM usuarios u WHERE u.nombre = n.nombre AND u.rol != 'sistema');

-- Si hay dos con el mismo nombre, la nómina va a la que puede entrar.
UPDATE usuarios SET nomina = 1
 WHERE id IN (SELECT (SELECT u.id FROM usuarios u WHERE u.nombre = n.nombre AND u.rol != 'sistema'
                       ORDER BY u.activo DESC, u.id LIMIT 1) FROM nomina_vieja n);

UPDATE usuarios SET sueldo_mes = (
  SELECT CAST(j.value AS REAL) FROM config c, json_each(c.valor) j
   WHERE c.clave = 'sueldos' AND json_valid(c.valor) AND trim(j.key) = usuarios.nombre)
 WHERE nomina = 1;

DROP TABLE nomina_vieja;
DELETE FROM config WHERE clave IN ('equipo', 'sueldos');
