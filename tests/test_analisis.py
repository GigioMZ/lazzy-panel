from datetime import date, timedelta

from lazzy import analisis as A
from lazzy.db import connect


def con_semanas():
    con = connect(":memory:")
    d0 = date(2026, 9, 7)  # lunes
    for i in range(56):
        d = d0 + timedelta(days=i)
        if d.weekday() == 6:
            continue
        base = 100000 if d.weekday() == 1 else 200000      # martes flojo
        horas = [10, 11] if d.weekday() == 5 else [9, 10, 11, 12]
        for h in horas:
            v = base // len(horas) * (3 if h == 11 else 1)
            con.execute("INSERT INTO venta_hora VALUES (?,?,?,?)", (d.isoformat(), h, v, 10 if h == 11 else 2))
    con.commit()
    return con, d0 + timedelta(days=55)


def test_dias_mapa_estimacion_y_recomendaciones():
    con, fin = con_semanas()
    dias = A.por_dia_semana(con)
    assert dias[6]["abierto"] is False and dias[1]["venta"] < dias[0]["venta"]
    mapa = A.mapa_horas(con)
    assert [f["nombre"] for f in mapa["filas"]][:2] == ["lunes", "martes"] and mapa["horas"] == [9, 10, 11, 12]
    est = A.estimacion(con, desde=date(2026, 11, 2))
    assert est["dias"][0]["nombre"] == "lunes" and est["dias"][6]["abierto"] is False
    assert est["tendencia"] == 0.0 and est["dias"][0]["venta"] == dias[0]["venta"]
    rec = " | ".join(r["titulo"] for r in A.recomendaciones(dias, mapa, [], []))
    assert "el martes es tu día más flojo" in rec and "Cuándo necesitas más manos" in rec


def test_precio_redondo_y_sugerencias():
    assert A._precio_redondo(4912) == 4990 and A._precio_redondo(3490) == 3490 and A._precio_redondo(3491) == 3590
    con = connect(":memory:")
    con.executemany("INSERT INTO venta_producto VALUES (?,?,?,?,?)", [
        ("2026-10-01", "Pasteleria", "Cheesecake", 100, 449000),   # neto 3.773, costo 2.605 -> 31%
        ("2026-10-01", "Pasteleria", "Tiramisu", 50, 223900),      # neto 3.763, costo 2.900 -> 23%
        ("2026-10-01", "Café C/Leche", "Latte", 100, 349000)])
    con.execute("INSERT INTO venta_hora VALUES ('2026-10-01',9,1,1)")
    cfg = {"empresa": {"iva": 0.19}, "costo_producto": {"Cheesecake": {"costo": 2605, "fuente": "f"},
                                                        "Tiramisu": {"costo": 2900, "fuente": "f"},
                                                        "Latte": {"costo": 800, "fuente": "r"}}}
    p = {x["producto"]: x for x in A.precios_a_revisar(con, cfg, "2026-10", "2026-10")}
    assert "Latte" not in p
    assert p["Cheesecake"]["accion"] == "costo"          # necesitaría $5.690 (+27%)
    assert p["Tiramisu"]["accion"] == "costo" and p["Tiramisu"]["costo_obj"] == round(3763 * 0.55)
    cfg["costo_producto"]["Cheesecake"]["costo"] = 2300   # necesita $4.990: +11%
    c = {x["producto"]: x for x in A.precios_a_revisar(con, cfg, "2026-10", "2026-10")}["Cheesecake"]
    assert c["accion"] == "precio" and c["sugerido"] == 4990 and c["nuevo_margen"] >= 45
    assert c["extra_mes"] == round((4990 / 1.19 - 3773) * 100)
