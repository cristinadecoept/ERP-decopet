-- Clientes de envío nacional (Cristina, 7 nov 2026): en su ficha quedó como dirección la oficina de la agencia
-- (vino así de la ficha de Airtable). La ficha es para la casa del cliente; la oficina va en cada pedido.
-- No se borra: se pasa a direcciones_archivadas, de donde se puede devolver.
-- Solo clientes con TODOS sus pedidos por envío nacional y que no son de Caracas ni Miranda.
CREATE TABLE IF NOT EXISTS direcciones_archivadas (
  id INTEGER PRIMARY KEY, cliente_id INTEGER, etiqueta TEXT, direccion TEXT, zona TEXT, municipio TEXT, ciudad TEXT,
  estado TEXT, maps TEXT, principal INTEGER, motivo TEXT, archivada_en TEXT DEFAULT (datetime('now','localtime')));

INSERT INTO direcciones_archivadas (id, cliente_id, etiqueta, direccion, zona, municipio, ciudad, estado, maps, principal, motivo)
SELECT d.id, d.cliente_id, d.etiqueta, d.direccion, d.zona, d.municipio, d.ciudad, d.estado, d.maps, d.principal, 'oficina de envío nacional'
  FROM direcciones d JOIN clientes c ON c.id = d.cliente_id
 WHERE d.cliente_id IN (SELECT cliente_id FROM ordenes WHERE estado != 'cancelada' AND tipo_entrega IS NOT NULL
                         GROUP BY cliente_id HAVING SUM(tipo_entrega != 'nacional') = 0)
   AND COALESCE(c.estado, '') NOT IN ('Distrito Capital', 'Miranda')
   AND COALESCE(d.ciudad, '') NOT IN ('Caracas')
   AND COALESCE(d.estado, '') NOT IN ('Distrito Capital', 'Miranda')
   AND NOT EXISTS (SELECT 1 FROM direcciones_archivadas a WHERE a.id = d.id);

DELETE FROM direcciones WHERE id IN (SELECT id FROM direcciones_archivadas WHERE motivo = 'oficina de envío nacional');
