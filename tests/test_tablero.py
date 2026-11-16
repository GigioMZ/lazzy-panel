from datetime import date

from lazzy import tablero as T
from lazzy.db import connect
from lazzy.finance import load_cfg


def test_semana_con_recetas_y_prorrateo():
    con = connect(":memory:")
    for d in ("2026-11-02", "2026-11-03"):                       # lunes y martes
        con.execute("INSERT INTO venta_hora VALUES (?,10,119000,10)", (d,))
        con.execute("INSERT INTO venta_medio_pago VALUES (?,'Tarj. Débito',119000,10)", (d,))
        con.execute("INSERT INTO venta_producto VALUES (?,'Café','Latte',20,59500)", (d,))      # neto 50.000
        con.execute("INSERT INTO venta_producto VALUES (?,'Café','Mocca',10,59500)", (d,))      # sin costo
    con.execute("INSERT INTO producto_costo VALUES ('Latte',750,'f',NULL)")                   # 30% del precio
    con.execute("INSERT INTO costo_fijo(nombre,categoria,monto,desde) VALUES ('s','remuneraciones',300000,'2026-01')")
    con.execute("INSERT INTO parametro VALUES ('comision_debito','2')")
    con.execute("INSERT INTO parametro VALUES ('comision_credito','3')")
    con.commit()
    cfg = load_cfg(con)
    r = T.resultado(con, cfg, date(2026, 11, 2), date(2026, 11, 8), hoy="2026-12")
    assert r["neto"] == 200000 and r["tickets"] == 20 and r["dias"] == 2
    assert r["costo_venta"] == 60000      # Latte 30.000 real + Mocca estimado al 30% de su categoría
    assert r["cobertura"] == 50
    assert r["sueldos"] == 70000          # 300.000 × 7/30
    assert r["comision"] == 4760          # 2% de 238.000
    assert r["ganancia"] == 200000 - 60000 - 70000 - 4760
    assert T.anterior(date(2026, 11, 2), date(2026, 11, 8)) == (date(2026, 10, 26), date(2026, 11, 1))
    p = T.presets(date(2027, 1, 3))       # domingo, cruzando el año
    assert p["semana_pasada"][1:] == (date(2026, 12, 21), date(2026, 12, 27))
    assert p["mes_pasado"][1:] == (date(2026, 12, 1), date(2026, 12, 31)) and p["anio"][1] == date(2027, 1, 1)
    dias = T.ventas_diarias(con, cfg, date(2026, 11, 2), date(2026, 11, 8))
    assert len(dias) == 7 and dias[0]["etiqueta"] == "lun 2" and dias[0]["neto"] == 100000 and dias[6]["neto"] == 0
    assert len(T.agrupar(T.ventas_diarias(con, cfg, date(2026, 1, 1), date(2026, 11, 8)), 312)) == 11
