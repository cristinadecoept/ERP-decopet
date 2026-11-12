-- Viajes a la agencia (Cristina, 12 nov 2026): cada envío asignado abría un viaje nuevo, aunque el mismo despachador
-- ya tuviera otro por llevar a la misma oficina. Es una sola ida: se juntan en el más viejo, con todos sus paquetes.
-- Solo los que todavía no se llevaron ni se pagaron, y que son de hoy o atrasados (los de otro día quedan aparte).
-- Se paga una sola ida: la tarifa más alta de los que se juntan, no la suma.
CREATE TEMP TABLE _juntar AS
SELECT v.id,
       (SELECT MIN(v2.id) FROM viajes_agencia v2
         WHERE v2.despachador = v.despachador AND COALESCE(v2.agencia,'') = COALESCE(v.agencia,'')
           AND COALESCE(v2.oficina,'') = COALESCE(v.oficina,'')
           AND v2.llevado_en IS NULL AND v2.pagado = 0 AND v2.fecha <= date('now','localtime')) AS destino
  FROM viajes_agencia v
 WHERE v.llevado_en IS NULL AND v.pagado = 0 AND v.fecha <= date('now','localtime');

DELETE FROM _juntar WHERE id = destino;

UPDATE viajes_agencia
   SET pedidos = pedidos + (SELECT COALESCE(SUM(v.pedidos),0) FROM viajes_agencia v JOIN _juntar j ON j.id = v.id WHERE j.destino = viajes_agencia.id),
       monto   = MAX(monto, (SELECT COALESCE(MAX(v.monto),0) FROM viajes_agencia v JOIN _juntar j ON j.id = v.id WHERE j.destino = viajes_agencia.id)),
       fecha   = MAX(fecha, (SELECT COALESCE(MAX(v.fecha),'') FROM viajes_agencia v JOIN _juntar j ON j.id = v.id WHERE j.destino = viajes_agencia.id))
 WHERE id IN (SELECT destino FROM _juntar);

UPDATE ordenes SET viaje_id = (SELECT destino FROM _juntar WHERE _juntar.id = ordenes.viaje_id)
 WHERE viaje_id IN (SELECT id FROM _juntar);
UPDATE packs SET viaje_id = (SELECT destino FROM _juntar WHERE _juntar.id = packs.viaje_id)
 WHERE viaje_id IN (SELECT id FROM _juntar);

DELETE FROM viajes_agencia WHERE id IN (SELECT id FROM _juntar);
DROP TABLE _juntar;
