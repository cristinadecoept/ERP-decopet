-- Muchas direcciones habituales viejas son solo el nombre de la zona ("La Tahona") y no tenían la zona puesta,
-- así que la orden la pedía otra vez (Cristina, 8 oct 2026). Se completa solo cuando la dirección es exactamente
-- una zona de Tarifas (sin contar mayúsculas ni acentos). Las demás se completan al hacerles el próximo pedido.
UPDATE direcciones SET zona = (
  SELECT t.zona FROM tarifas t
   WHERE replace(replace(replace(replace(replace(replace(lower(trim(t.zona)),'á','a'),'é','e'),'í','i'),'ó','o'),'ú','u'),'ñ','n')
       = replace(replace(replace(replace(replace(replace(lower(trim(direcciones.direccion)),'á','a'),'é','e'),'í','i'),'ó','o'),'ú','u'),'ñ','n')
   LIMIT 1)
 WHERE (zona IS NULL OR trim(zona) = '')
   AND EXISTS (SELECT 1 FROM tarifas t
                WHERE replace(replace(replace(replace(replace(replace(lower(trim(t.zona)),'á','a'),'é','e'),'í','i'),'ó','o'),'ú','u'),'ñ','n')
                    = replace(replace(replace(replace(replace(replace(lower(trim(direcciones.direccion)),'á','a'),'é','e'),'í','i'),'ó','o'),'ú','u'),'ñ','n'));
