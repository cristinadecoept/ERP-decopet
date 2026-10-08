-- El Porche Básico (mediano y grande) también puede llevar malla (Cristina, 8 oct 2026).
UPDATE productos SET permite_malla = 1 WHERE sku IN ('BAS-M', 'BAS-G');
