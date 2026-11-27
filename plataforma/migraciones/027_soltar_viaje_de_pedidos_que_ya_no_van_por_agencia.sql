-- Pedidos que se pasaron de envío nacional a delivery o pick-up después de asignarlos a un viaje a la agencia
-- (Cristina, 27 nov 2026: #9370 seguía saliendo en el viaje a MRW aunque ya se había entregado en Caracas).
-- Se sueltan del viaje y el viaje cuenta un pedido menos por cada uno. Desde ahora Editar entrega lo hace solo.
UPDATE viajes_agencia
SET pedidos = MAX(0, pedidos - (SELECT COUNT(*) FROM ordenes o WHERE o.viaje_id = viajes_agencia.id AND o.tipo_entrega <> 'nacional'))
WHERE llevado_en IS NULL
  AND EXISTS (SELECT 1 FROM ordenes o WHERE o.viaje_id = viajes_agencia.id AND o.tipo_entrega <> 'nacional');

UPDATE ordenes SET viaje_id = NULL
WHERE tipo_entrega <> 'nacional'
  AND viaje_id IN (SELECT id FROM viajes_agencia WHERE llevado_en IS NULL);
