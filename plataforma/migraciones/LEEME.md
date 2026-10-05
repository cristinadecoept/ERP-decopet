# Migraciones

Cambios de la base que el ERP no hace solo al arrancar. Agregar una **tabla** o una **columna** nueva no
necesita migración: basta con ponerla en `modelo.sql` o en `COLUMNAS` (app.py).

Sí la necesitan: renombrar o quitar algo, mover datos de un lado a otro, o cargar valores iniciales que un
cambio necesita (por ejemplo, las tarifas de una función nueva).

- Un archivo por cambio: `NNN_que_hace.sql` (`001_tarifas_diligencia_iniciales.sql`, `002_…`). Se aplican en
  orden de número.
- Cada archivo se aplica **una sola vez** en cada base (la Mac, el servidor). La tabla `migraciones` anota cuáles
  ya se hicieron.
- Si un archivo falla, no queda nada a medias de él y el ERP no arranca. En el servidor sigue la versión
  anterior hasta que se arregle.
- **Nunca** se cambia un archivo que ya se aplicó: si hay que corregir algo, se agrega otro con el número siguiente.
- Escribirlo para que también funcione en una base recién creada (las pruebas arman bases nuevas):
  `INSERT OR IGNORE`, `WHERE NOT EXISTS`, etc.
