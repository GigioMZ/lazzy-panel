from datetime import date, timedelta

from lazzy import estado as E
from lazzy import metas as MT
from lazzy import ofertas as O
from lazzy import simulador as SIM
from lazzy.db import connect
from lazzy.finance import load_cfg


def _base():
    con = connect(":memory:")
    con.execute("INSERT INTO costo_fijo(nombre,categoria,monto,desde) VALUES ('Sueldo','remuneraciones',600000,'2026-01')")
    con.execute("INSERT INTO proveedor(rut,nombre,categoria) VALUES ('1-9','LECHE SA','leche')")
    d = date(2026, 8, 1)
    while d < date(2026, 11, 14):
        if d.weekday() < 6:
            f = d.isoformat()
            con.execute("INSERT INTO venta_hora(fecha,hora,bruto,tickets) VALUES (?,?,?,?)", (f, 10, 77760, 20))
            con.execute("INSERT INTO venta_producto(fecha,categoria,producto,cantidad,bruto) VALUES (?,?,?,?,?)", (f, "Café C/Leche", "Latte", 10, 34900))
            con.execute("INSERT INTO venta_producto(fecha,categoria,producto,cantidad,bruto) VALUES (?,?,?,?,?)", (f, "Pasteleria", "Brownies", 6, 14940))
            con.execute("INSERT INTO venta_producto(fecha,categoria,producto,cantidad,bruto) VALUES (?,?,?,?,?)", (f, "Pasteleria", "Croissant", 2, 5980))
            con.execute("INSERT INTO venta_producto(fecha,categoria,producto,cantidad,bruto) VALUES (?,?,?,?,?)", (f, "Promos", "Cappu + Brownie", 1, 4490))
            con.execute("INSERT INTO venta_producto(fecha,categoria,producto,cantidad,bruto) VALUES (?,?,?,?,?)", (f, "Café C/Leche", "Cappuccino", 5, 17450))
        d += timedelta(days=1)
    for i, m in enumerate(("2026-08", "2026-09", "2026-10")):
        con.execute("INSERT INTO compra(periodo,tipo_doc,rut,razon_social,folio,fecha_doc,exento,neto,iva_rec,iva_no_rec,total,activo_fijo_neto,origen) "
                    "VALUES (?,33,'1-9','LECHE SA',?,?,0,?,0,0,0,0,'sii')", (m, i + 1, m + "-10", 800000 + 100000 * i))
    for p, c in (("Latte", 900), ("Brownies", 700), ("Croissant", 600), ("Cappuccino", 800)):
        con.execute("INSERT INTO producto_costo VALUES (?,?,?,?)", (p, c, "manual", "2026-01-01"))
    con.commit()
    return con


def test_estado_de_resultados_con_comparaciones_y_detalle():
    con = _base()
    cfg = load_cfg(con)
    ps = ["2026-08", "2026-09", "2026-10", "2026-11"]
    e = E.estado(con, cfg, "2026-10", ps)
    f = {x["nombre"]: x for x in e["filas"]}
    # octubre: 27 días × (10 Latte × 900 + 6 Brownies × 700 + 2 Croissant × 600 + 5 Cappuccino × 800 + 1 combo × 1.500)
    assert f["Costo de lo vendido"]["actual"] == 27 * (9000 + 4200 + 1200 + 4000 + 1500)
    assert f["Venta sin IVA"]["pct"] == 100.0 and "Pasteleria" in f and "Promos" in f
    assert e["ytd_meses"] == ["2026-08", "2026-09", "2026-10"] and f["Resultado operacional"]["anio"] is None
    assert e["cur"]["compras"]["total"] == 1000000 and e["cur"]["cobertura"] == 100
    det = E.detalle(con, cfg, "2026-10")
    assert det["leche"][0]["doc"] == "Factura 3" and det["remuneraciones"][0]["nombre"] == "Sueldo"
    assert E.detalle_costo(e["cur"])["Pasteleria"][0]["nombre"] == "Brownies"
    pu = E.puente(con, cfg, "2026-10", ps)
    assert len(pu["filas"]) == 3 and pu["filas"][-1]["compras"] == 1000000
    assert pu["filas"][-1]["dif"] == 1000000 - f["Costo de lo vendido"]["actual"] and pu["alerta"]
    assert sum(x["ancho"] for x in E.composicion(e["cur"])) > 99


def test_metas_ritmo_y_lista_de_cierre():
    con = _base()
    cfg = load_cfg(con)
    MT.guardar(con, "2026-11", {"venta": 3000000, "resultado": 500000}, todos=True)
    MT.guardar(con, "2026-12", {"venta": 5000000})
    assert MT.leer(con, "2026-11")["venta"] == 3000000 and MT.leer(con, "2026-12")["venta"] == 5000000
    from lazzy.finance import pnl_mes
    p = pnl_mes(con, "2026-11", cfg)
    r = MT.ritmo(con, cfg, "2026-11", p)
    assert r["hasta"] == date(2026, 11, 13) and r["proy_venta"] > p["ventas"]["neto"] and r["avance"] == 43
    c = {x["clave"]: x for x in MT.comparar(con, cfg, "2026-11", p, MT.leer(con, "2026-11"), r)}
    assert c["venta"]["proyectado"] and c["venta"]["real"] == r["proy_venta"]
    lista = {x["texto"]: x["ok"] for x in MT.checklist(con, cfg, "2026-10", pnl_mes(con, "2026-10", cfg))}
    assert lista["Compras del SII del mes"] and lista["Ventas de FUDO hasta el último día"] and not lista["Tasa de PPM"]


def test_simulador_y_ofertas():
    con = _base()
    cfg = load_cfg(con)
    b = SIM.base(con, cfg, hoy=date(2026, 11, 16))
    assert b["meses"] == ["2026-10", "2026-09", "2026-08"] and b["venta"] > 0 and b["sueldos"] == 600000
    assert SIM.elasticidad(con, cfg)["valor"] == -0.5
    of = O.resumen(con, cfg)
    a = of["actuales"][0]
    assert a["producto"] == "Cappu + Brownie" and a["partes"] == ["Cappuccino", "Brownies"]
    assert a["separado"] == 3490 + 2490 and a["costo"] == 1500 and a["margen_sep"] > a["margen"]
    assert of["sugeridas"] and all(s["precio"] % 100 == 90 for s in of["sugeridas"])
    assert all("Brownies" not in s["partes"] or "Cappuccino" not in s["partes"] for s in of["sugeridas"])
