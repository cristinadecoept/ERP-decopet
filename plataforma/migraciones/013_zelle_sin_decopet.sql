-- La caja "Zelle Decopet" pasa a llamarse solo "Zelle" (Cristina, 8 oct 2026). Solo cambia el nombre: ningún monto se toca.
UPDATE cuentas SET nombre = 'Zelle' WHERE nombre = 'Zelle Decopet' AND NOT EXISTS (SELECT 1 FROM cuentas WHERE nombre = 'Zelle');
UPDATE pagos SET forma = 'Zelle' WHERE forma = 'Zelle Decopet';
UPDATE pagos SET cuenta = 'Zelle' WHERE cuenta = 'Zelle Decopet';
UPDATE ordenes SET forma_pago_prevista = 'Zelle' WHERE forma_pago_prevista = 'Zelle Decopet';
UPDATE movimientos SET concepto = replace(concepto, 'Zelle Decopet', 'Zelle') WHERE concepto LIKE '%Zelle Decopet%';
UPDATE movimientos SET notas = replace(notas, 'Zelle Decopet', 'Zelle') WHERE notas LIKE '%Zelle Decopet%';
