-- Las zonas de delivery que quedan fuera de Caracas (Cristina, 8 oct 2026): al marcar "Delivery fuera de Caracas"
-- en una orden solo salen estas; con "Delivery Caracas", las demás. Caraballeda cuesta $25, no $5.
UPDATE tarifas SET fuera_caracas = 1
 WHERE zona IN ('Los Teques', 'San Antonio', 'La Guaira', 'Catia La Mar', 'Caraballeda', 'Naiguata', 'Guarenas', 'Guatire', 'El Junquito');
UPDATE tarifas SET tarifa = 25 WHERE zona = 'Caraballeda';
