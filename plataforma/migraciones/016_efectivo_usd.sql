-- La caja "Efectivo USD Caracas" pasa a llamarse solo "Efectivo USD" (Cristina, 8 oct 2026). Solo cambia el nombre: ningún monto se toca.
UPDATE cuentas SET nombre = 'Efectivo USD' WHERE nombre = 'Efectivo USD Caracas' AND NOT EXISTS (SELECT 1 FROM cuentas WHERE nombre = 'Efectivo USD');
UPDATE pagos SET forma = 'Efectivo USD' WHERE forma = 'Efectivo USD Caracas';
UPDATE pagos SET cuenta = 'Efectivo USD' WHERE cuenta = 'Efectivo USD Caracas';
UPDATE ordenes SET forma_pago_prevista = 'Efectivo USD' WHERE forma_pago_prevista = 'Efectivo USD Caracas';
UPDATE movimientos SET concepto = replace(concepto, 'Efectivo USD Caracas', 'Efectivo USD') WHERE concepto LIKE '%Efectivo USD Caracas%';
UPDATE movimientos SET notas = replace(notas, 'Efectivo USD Caracas', 'Efectivo USD') WHERE notas LIKE '%Efectivo USD Caracas%';
