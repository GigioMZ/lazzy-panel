from datetime import date, timedelta

from lazzy import alertas as AL
from lazzy import pedidos as PD
from lazzy import proveedores as PV
from lazzy import stock as ST
from lazzy import vitrina as VT
from lazzy.db import connect
from lazzy.finance import load_cfg

D = date(2026, 11, 10)      # día en que se saca a vitrina


def _base(ult=D + timedelta(days=1)):
    """Cheesecake: vende 2 al día; ventas cargadas hasta «ult». Stock contado el 1/11."""
    con = connect(":memory:")
    d = date(2026, 10, 1)
    while d <= ult:
        con.execute("INSERT INTO venta_producto(fecha,categoria,producto,cantidad,bruto) VALUES (?,'Pasteleria','Cheesecake berries',2,7980)", (d.isoformat(),))
        con.execute("INSERT INTO venta_producto(fecha,categoria,producto,cantidad,bruto) VALUES (?,'Pasteleria','Brownies',1,2490)", (d.isoformat(),))
        d += timedelta(days=1)
    con.execute("INSERT INTO producto_costo(producto,costo,fuente) VALUES ('Cheesecake berries',2605,'factura')")
    ST.guardar(con, [{"producto": "Cheesecake berries", "cantidad": 40, "minimo": 4}, {"producto": "Brownies", "cantidad": 30, "minimo": 2}],
               "2026-11-01", "2026-11-01")
    con.commit()
    return con


def test_texto_de_vitrina():
    con = _base()
    assert VT.parsear(con, "saqué 8 cheesecake berries, 4 brownies")["items"] == [("Cheesecake berries", 8), ("Brownies", 4)]
    assert VT.parsear(con, "hoy sacamos a vitrina 3 brownies")["items"] == [("Brownies", 3)]
    assert VT.parsear(con, "en vitrina quedan 2 cheesecake berries")["tipo"] == "conteo"
    assert VT.parsear(con, "quedan 2 cheesecake berries") is None       # eso es stock total, no vitrina


def test_tandas_se_venden_en_orden_y_avisan_el_ultimo_dia():
    con = _base()
    VT.registrar(con, "Cheesecake berries", 8, fecha=D.isoformat())
    # día 2: vendió 2 (real) el 10 y 2 el 11; el 12 se estima con su ritmo (2)
    x = VT.estado(con, D + timedelta(days=2))[0]
    assert (x["quedan"], x["dias"], x["estimado"]) == (4, 1, False)
    x = VT.estado(con, D + timedelta(days=3))[0]
    assert (x["quedan"], x["dias"], x["estimado"]) == (2, 0, True)
    avisos = AL.vitrina(con, D + timedelta(days=3))
    assert "~2 Cheesecake berries sacados el 10/11: hoy es su último día" in avisos[0]
    # 25% menos ($2.900) quedaría bajo el costo ($2.605 + IVA): sugiere lo más bajo sin perder
    assert "Ofrézcanlo a $3.100 (22% menos; sin perder plata)" in avisos[0]
    assert AL.vitrina(con, D + timedelta(days=3)) == []        # una sola vez
    assert VT.estado(con, D + timedelta(days=4)) == []          # al día siguiente ya se dio por vencido


def test_merma_y_conteo_de_vitrina():
    con = _base()
    VT.registrar(con, "Cheesecake berries", 8, fecha=D.isoformat())
    VT.registrar(con, "Cheesecake berries", 6, fecha=(D + timedelta(days=1)).isoformat())
    ST.registrar_llegada(con, "Cheesecake berries", -1, (D + timedelta(days=1)).isoformat(), "vencido", "merma bot")
    lotes = VT.estado(con, D + timedelta(days=2))
    assert [(x["fecha"][-2:], x["quedan"]) for x in lotes] == [("10", 3), ("11", 6)]     # 8 − 2 − 2 − 1 merma
    VT.registrar(con, "Cheesecake berries", 5, tipo="conteo", fecha=(D + timedelta(days=2)).isoformat())
    lotes = VT.estado(con, D + timedelta(days=2))
    assert [x["quedan"] for x in lotes] == [5]                    # lo que queda es de la tanda más nueva
    VT.guardar_ajuste(con, "Cheesecake berries", vida=2)
    assert VT.estado(con, D + timedelta(days=2))[0]["dias"] == 0


def test_pedido_sugerido():
    con = _base(ult=D)
    hoy = D + timedelta(days=3)          # 2 días sin ventas subidas: se estiman
    cfg = load_cfg(con)
    gs = PD.sugerido(con, cfg, hoy)
    ch = next(i for g in gs for i in g["items"] if i["producto"] == "Cheesecake berries")
    # conteo 40 el 1/11, vendió 2/día del 2 al 10 (18) → 22; menos 2 días estimados (4) → 18. Objetivo 2 × 7 + 4 = 18
    assert (ch["ritmo"], ch["hoy"], ch["objetivo"], ch["pedir"]) == (2, 18, 18, 0)
    con.execute("INSERT INTO factura_linea(tipo_doc,rut,folio,n,descripcion,cantidad,precio_neto,total_neto) "
                "VALUES (33,'77323879-0',1,1,'cheesecake berries individual',12,2600,31200)")
    con.execute("INSERT INTO compra VALUES ('2026-10',33,'77323879-0','Cake House',1,'2026-10-20',0,31200,5928,0,37128,0,NULL,'sii',NULL)")
    PV.vincular(con, "77323879-0", "cheesecake berries individual", "producto", "Cheesecake berries", 6)
    PD.guardar_dias(con, "77323879-0", 14)
    gs = PD.sugerido(con, cfg, hoy)
    g = PD.buscar_grupo(gs, "cake")[0]
    ch = g["items"][0]
    # la factura de octubre es anterior al conteo: no suma. Objetivo 2 × 14 + 4 = 32 → pedir 14 = 3 cajas de 6
    assert (g["dias"], ch["objetivo"], ch["pedir"], ch["packs"]) == (14, 32, 14, 3)
    assert "- 3 × cheesecake berries individual (14 unidades)" in PD.mensaje(g)
    VT.guardar_ajuste(con, "Cheesecake berries", objetivo=20)
    assert PD.buscar_grupo(PD.sugerido(con, cfg, hoy), "cake")[0]["items"][0]["pedir"] == 2


def test_pagina_de_stock_con_vitrina_pedido_y_ajustes(tmp_path):
    import re
    from test_web import client, login
    c, con = client(tmp_path)
    tok = login(c)
    ST.guardar(con, [{"producto": "Latte", "cantidad": 3, "minimo": 5}], date.today().isoformat(), date.today().isoformat())
    VT.registrar(con, "Latte", 2)
    t = c.get("/stock").text
    assert "En vitrina" in t and "Pedido sugerido" in t and "Guardar ajustes" in t
    r = c.post("/stock", data={"csrf": tok, "accion": "ajustes", "vida:Latte": "2", "obj:Latte": "12", "prov:Latte": ""})
    assert r.status_code == 303 and VT.ajustes(con)["Latte"]["vida"] == 2 and VT.ajustes(con)["Latte"]["objetivo"] == 12
    t = c.get("/stock").text
    assert re.search(r"<b>12</b>", t)    # objetivo fijo 12; con las ventas atrasadas, lo que había ya se habría vendido
