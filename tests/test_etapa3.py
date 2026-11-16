from datetime import date, timedelta

from lazzy import alertas as AL
from lazzy import captura as C
from lazzy import gastos as G
from lazzy import iva as IVA
from lazzy import preguntas as P
from lazzy import proveedores as PV
from lazzy.db import connect
from lazzy.finance import load_cfg

from test_web import client, login


def test_cantidad_en_la_descripcion_y_coma_decimal():
    d = C.normalizar_doc({"tipo": "factura", "rut_emisor": "78461541-3", "folio": 64, "fecha": "2026-11-13", "neto": 50420,
                          "iva": 9580, "total": 60000, "lineas": [
                              {"descripcion": "12 PIE DE LIMON", "cantidad": 1, "precio_unitario": 25210, "total_linea": 25210},
                              {"descripcion": "6 CHOCONARANJA", "cantidad": 1, "precio_unitario": 12605, "total_linea": 12605},
                              {"descripcion": "6 PRESTIGIO", "cantidad": 6, "precio_unitario": 2100.8, "total_linea": 12605}]})
    l0, l1, l2 = d["lineas"]
    assert (l0["descripcion"], l0["cantidad"], round(l0["precio_neto"])) == ("PIE DE LIMON", 12, 2101)
    assert l1["cantidad"] == 6 and l2["descripcion"] == "PRESTIGIO" and any("12 unidades" in a for a in d["avisos"])
    d = C.normalizar_doc({"tipo": "factura", "rut_emisor": "99583570-3", "folio": 210842, "fecha": "2026-11-16", "neto": 72699,
                          "iva": 13813, "total": 86512, "lineas": [
                              {"descripcion": "Q.LAM.400G.GAUDA VAQUITA", "cantidad": 10, "precio_unitario": 2352941, "total_linea": 23529410},
                              {"descripcion": "Q.FRESCO LA VAQUITA", "cantidad": 1.3, "precio_unitario": 4159664, "total_linea": 5407563},
                              {"descripcion": "OTROS", "cantidad": 1, "precio_unitario": 43762000, "total_linea": 43762000}]})
    assert round(d["lineas"][0]["precio_neto"]) == 2353 and d["lineas"][1]["cantidad"] == 1.3
    assert PV.formato("Q.LAM.400G.GAUDA VAQUITA") == (400, "g")


def test_migracion_corrige_lineas_guardadas(tmp_path):
    import sqlite3
    from lazzy import db as D
    ruta = str(tmp_path / "v.db")
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    con.executescript(D.SCHEMA)
    for m in D.MIGRACIONES[:4]:
        m(con)
    con.execute("PRAGMA user_version=4")
    con.execute("INSERT INTO factura_linea VALUES (33,'78461541-3',64,0,'12 pie de limon',1,25210,25210,'12 PIE DE LIMON')")
    con.commit()
    con.close()
    con = connect(ruta)
    r = con.execute("SELECT * FROM factura_linea").fetchone()
    assert (r["descripcion"], r["cantidad"], round(r["precio_neto"])) == ("pie de limon", 12, 2101)
    assert con.execute("SELECT categoria FROM proveedor WHERE rut='99583570-3'").fetchone()[0] == "sandwiches"
    assert con.execute("SELECT COUNT(*) FROM turno WHERE persona='Tupaj' AND en_resultado=0").fetchone()[0] == 6


def test_preguntas_ruteo_y_contexto():
    assert not P.es_pregunta("luz 250730, agua 33209") and not P.es_pregunta("pagué gas 15000")
    assert P.es_pregunta("¿cuánto vendimos la semana pasada?") and P.es_pregunta("dame los productos que menos dejan")
    con = connect(":memory:")
    con.execute("INSERT INTO venta_hora(fecha,hora,bruto,tickets) VALUES ('2026-11-10',10,50000,5)")
    con.execute("INSERT INTO venta_producto(fecha,categoria,producto,cantidad,bruto) VALUES ('2026-11-10','Café','Latte',5,17450)")
    t = P.contexto(con, hoy=date(2026, 11, 16))
    assert "Semanas" in t and "Latte" in t and "Turnos" in t

    class Http:
        def post(self, url, **kw):
            assert "DATOS DEL PANEL" in kw["json"]["system"][1]["text"]

            class R:
                status_code = 200

                def json(self):
                    return {"content": [{"type": "text", "text": "Vendieron **$50.000** con IVA."}], "usage": {}}
            return R()
    import os
    os.environ["ANTHROPIC_API_KEY"] = "x"
    try:
        assert P.responder(con, "¿cuánto vendimos?", hoy=date(2026, 11, 16), http=Http()) == "Vendieron $50.000 con IVA."
    finally:
        del os.environ["ANTHROPIC_API_KEY"]


def test_alertas_dia_bajo_y_factura_faltante():
    con = connect(":memory:")
    hoy = date(2026, 11, 16)
    for k in range(1, 9):
        con.execute("INSERT INTO venta_hora(fecha,hora,bruto,tickets) VALUES (?,10,?,10)", ((date(2026, 11, 11) - timedelta(days=7 * k)).isoformat(), 200000 + k * 1000))
    con.execute("INSERT INTO venta_hora(fecha,hora,bruto,tickets) VALUES ('2026-11-11',10,90000,4)")
    con.execute("INSERT INTO proveedor(rut,nombre,categoria) VALUES ('2-7','LECHE SA','leche')")
    for i, f in enumerate(("2026-09-20", "2026-09-27", "2026-10-04", "2026-10-11", "2026-10-18")):
        con.execute("INSERT INTO compra VALUES (?,33,'2-7','LECHE SA',?,?,0,1000,190,0,1190,0,NULL,'sii',NULL)", (f[:7], i, f))
    con.execute("INSERT INTO compra VALUES ('2026-11',33,'9-9','OTRO',1,'2026-11-14',0,1000,190,0,1190,0,NULL,'bot',NULL)")
    t = AL.revisar(con, hoy)
    assert any("miércoles 11/11" in x for x in t) and any("LECHE SA cada 7 días" in x for x in t)
    assert AL.revisar(con, hoy) == []                      # no repite
    assert con.execute("SELECT COUNT(*) FROM notificacion").fetchone()[0] == len(t)


def test_iva_fecha_f29_y_apartar():
    assert IVA.fecha_pago("2026-11") == date(2026, 12, 21)    # 20/12 es domingo
    assert IVA.fecha_pago("2026-09") == date(2026, 10, 20)
    con = connect(":memory:")
    con.execute("INSERT INTO venta_hora(fecha,hora,bruto,tickets) VALUES ('2026-11-10',10,119000,5)")
    ap = IVA.apartar(con, load_cfg(con), hoy=date(2026, 11, 16))
    assert ap["pago"] == date(2026, 12, 21) and ap["por_semana"] >= 0
    IVA.guardar_f29(con, {"periodo": "2026-10", "total": 500000, "ppm": 10000, "ventas_neto": 4000000})
    assert IVA.cuadre(con, load_cfg(con))[0]["tasa_ppm"] == 0.25
    assert IVA.es_f29("F29_octubre.pdf", "") and IVA.es_f29("x.pdf", "FORMULARIO 29 DECLARACION")


def test_turnos_y_cuentas():
    con = connect(":memory:")
    for h, b in ((10, 20000), (16, 10000), (17, 30000)):
        con.execute("INSERT INTO venta_hora(fecha,hora,bruto,tickets) VALUES ('2026-11-09',?,?,2)", (h, b))
    a = G.analisis_turnos(con)
    javi = next(t for t in a["turnos"] if t["persona"] == "Javi" and t["dia"] == 0)
    tupaj = next(t for t in a["turnos"] if t["persona"] == "Tupaj" and t["dia"] == 0)
    assert javi["venta_dia"] == 25000 and tupaj["venta_dia"] == 35000      # la hora de las 16 se reparte mitad y mitad
    con.execute("INSERT INTO gasto_manual(fecha,descripcion,categoria,neto,iva_rec,origen) VALUES ('2026-11-05','Luz Enel','servicios',300000,0,'bot')")
    s = G.servicios_mes(con, 1, hoy=date(2026, 11, 16))[0]
    assert s["luz"]["real"] == 300000 and s["agua"]["real"] is None
    assert any("luz" in x for x in AL.servicios(con, date(2026, 11, 16)))


def test_ficha_de_producto_e_insumo_desde_factura(tmp_path):
    c, con = client(tmp_path)
    csrf = login(c)
    r = c.post("/producto", data={"csrf": csrf, "producto": "Sándwich jamón queso", "categoria": "Sandwiches", "precio": "4.990"})
    assert r.status_code == 303
    t = c.get(r.headers["location"]).text
    assert "todavía no aparece en las ventas de FUDO" in t and "$4.990" in t
    c.post("/receta", data={"csrf": csrf, "producto": "Sándwich jamón queso", "accion": "nuevo_insumo", "nombre": "Queso gauda",
                            "formato": "400", "unidad": "g", "precio": "2353", "cantidad": "40"})
    from lazzy.finance import costo_receta
    assert round(costo_receta(con, "Sándwich jamón queso")) == 235
    assert "Sándwich jamón queso" in c.get("/productos").text
    c.post("/producto", data={"csrf": csrf, "producto": "Latte", "precio": "3690"})
    assert "En FUDO se está cobrando $3.500" in c.get("/receta?p=Latte").text
    con.execute("INSERT INTO factura_linea VALUES (33,'1-9',1,0,'q lam 400g gauda vaquita',10,2353,23530,'Q.LAM.400G.GAUDA VAQUITA')")
    con.commit()
    c.post("/proveedor/vinculos", data={"csrf": csrf, "rut": "1-9", "clave": "q lam 400g gauda vaquita", "d:q lam 400g gauda vaquita": "nuevo:insumo"})
    i = con.execute("SELECT * FROM insumo WHERE nombre LIKE 'Q lam%'").fetchone()
    assert (i["cantidad_formato"], i["unidad"], i["precio_formato"]) == (400, "g", 2353)
    assert con.execute("SELECT tipo FROM vinculo WHERE rut='1-9'").fetchone()[0] == "insumo"
    for u in ("/costos-fijos", "/impuestos", "/ayuda", "/cargar"):
        assert c.get(u).status_code == 200
    c.post("/turnos", data={"csrf": csrf, "accion": "pago", "persona": "Tupaj", "pago": "400.000"})
    assert "vende" in c.get("/costos-fijos").text and con.execute("SELECT valor FROM parametro WHERE clave='pago_Tupaj'").fetchone()[0] == "400000"


def test_stock_suma_facturas_resta_ventas_y_mide_merma():
    from lazzy import stock as ST
    from lazzy.finance import pnl_mes
    con = connect(":memory:")
    con.execute("INSERT INTO producto_costo VALUES ('Brownies', 1000, 'manual', '2026-01-01')")
    con.execute("INSERT INTO producto_costo VALUES ('Cappuccino', 800, 'manual', '2026-01-01')")
    for f, p, q, b in (("2026-11-03", "Brownies", 8, 19920), ("2026-11-04", "Cappu + Brownie", 2, 8980), ("2026-11-04", "Cappuccino", 1, 3490)):
        con.execute("INSERT INTO venta_producto(fecha,categoria,producto,cantidad,bruto) VALUES (?,?,?,?,?)", (f, "Pasteleria", p, q, b))
    con.execute("INSERT INTO venta_hora(fecha,hora,bruto,tickets) VALUES ('2026-11-03',10,32390,5)")
    con.execute("INSERT INTO compra VALUES ('2026-11',33,'1-9','CAKE',5,'2026-11-02',0,20000,3800,0,23800,0,NULL,'bot',NULL)")
    con.execute("INSERT INTO factura_linea VALUES (33,'1-9',5,0,'brownie nuez',20,1000,20000,'Brownie Nuez')")
    con.execute("INSERT INTO vinculo VALUES ('1-9','brownie nuez','producto','Brownies',1)")
    con.execute("INSERT INTO proveedor(rut,nombre,categoria) VALUES ('1-9','CAKE','pasteleria')")
    ST.guardar(con, [{"producto": "Brownies", "cantidad": 5, "minimo": 4}], "2026-11-01", "2026-11-01")
    cfg = load_cfg(con)
    assert cfg["costo_producto"]["Cappu + Brownie"] == {"costo": 1800, "fuente": "combo"}
    e = ST.estado(con, cfg, hoy=date(2026, 11, 10))[0]
    assert (e["entradas"], e["ventas"], e["esperado"], e["valor"]) == (20, 10, 15, 15000)       # 5 + 20 - 8 - 2 del combo
    assert ST.vista_previa(con, cfg, [{"producto": "Brownies", "cantidad": 12}]) == [("Brownies", 15, 12)]
    ST.guardar(con, [{"producto": "Brownies", "cantidad": 12, "minimo": 4}], "2026-11-10", "2026-11-10")
    a = ST.ajustes(con, cfg)[0]
    assert (a["esperado"], a["contado"], a["dif"], a["valor"]) == (15, 12, -3, -3000)
    assert ST.merma_mes(con, "2026-11", cfg) == 3000 and ST.valor(con, cfg, "2026-11-30") == 12000
    p = pnl_mes(con, "2026-11", cfg)
    assert p["merma"] == 3000 and p["lineas"]["costo_venta"]["Merma y ajustes de stock"] == 3000
    # las dos cuentas calzan: compras = costo de lo vendido + merma + aumento de stock + diferencia
    from lazzy import estado as E
    f = E.puente(con, cfg, "2026-11", ["2026-10", "2026-11"])["filas"][0]
    assert f["compras"] == 20000 and f["merma"] == 3000 and f["dstock"] is None


def test_stock_se_sube_por_el_panel(tmp_path):
    import openpyxl
    c, con = client(tmp_path)
    csrf = login(c)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Productos"
    ws.append(["Id.", "Categoría", "Código", "Nombre", "Disponibilidad", "Unidad", "Stock", "Unidad", "Stock Mínimo", "Unidad", "Proveedor"])
    ws.append([1, "Pasteleria", None, "Brownies", 16, "unid.", 16, "unid.", 4, "unid.", "Cakehouse"])
    wb.create_sheet("Ingredientes")
    ruta = tmp_path / "stock.xlsx"
    wb.save(ruta)
    c.post("/cargar", data={"csrf": csrf}, files=[("archivos", ("stock.xlsx", ruta.read_bytes(), "application/octet-stream"))])
    t = c.get("/cargar").text
    assert "Stock de FUDO" in t and "Es el primer stock" in t
    import re
    c.post("/cargar/confirmar", data={"csrf": csrf, "token": re.search(r'name="token" value="([0-9a-f]+)"', t).group(1), "accion": "guardar"})
    assert con.execute("SELECT cantidad, minimo FROM stock_conteo").fetchone()[:] == (16, 4)
    t = c.get("/stock").text
    assert "Brownies" in t and "no tienen una factura vinculada" in t
    assert c.get("/turnos").status_code == 200 and "Compras de insumos" in c.get("/resultados?periodo=2026-10").text


def test_stock_por_bot_llegadas_y_sugerencias(tmp_path):
    import io
    import openpyxl
    from lazzy import bot as B
    from lazzy import stock as ST
    con = connect(":memory:")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Productos"
    ws.append(["Id.", "Categoría", "Código", "Nombre", "Disponibilidad", "Unidad", "Stock", "Unidad", "Stock Mínimo", "Unidad", "Proveedor"])
    ws.append([1, "Pasteleria", None, "Brownies", 16, "unid.", 16, "unid.", 4, "unid.", "Cakehouse"])
    ws.append([2, "Sandwiches", None, "Ciabatta Jamon queso", 2, "unid.", 2, "unid.", 0, "unid.", None])
    ws.append([3, "Sandwiches", None, "Ciabatta Salame Queso", 6, "unid.", 6, "unid.", 0, "unid.", None])
    b = io.BytesIO()
    wb.save(b)

    class TG:
        env = []

        def __call__(self, *a, **k):
            return {}

        def enviar(self, c, t, botones=None):
            self.env.append(t)

        def bajar(self, fid):
            return b.getvalue()
    tg = TG()
    bot = B.Bot(con, tg, tmp_path)
    con.execute("INSERT INTO telegram_chat VALUES (7,'Javi','javi','2026-11-16')")
    bot.procesar({"message": {"chat": {"id": 7}, "document": {"file_id": "x", "file_name": "stock.xlsx"}}})
    assert "Guardé el stock de 3 productos" in tg.env[-1] and "Es el primero" in tg.env[-1]
    bot.procesar({"message": {"chat": {"id": 7}, "text": "llegaron 10 ciabatta jamon queso y 6 salame"}})
    assert "+10 Ciabatta Jamon queso: ahora deberían quedar 12" in tg.env[-1] and "+6 Ciabatta Salame Queso: ahora deberían quedar 12" in tg.env[-1]
    bot.procesar({"message": {"chat": {"id": 7}, "text": "llegaron 3 tortas de mazapan"}})
    assert "No encontré «tortas de mazapan»" in tg.env[-1]
    con.execute("INSERT INTO factura_linea VALUES (33,'1-9',5,0,'brownies nuez',12,1252,15024,'Brownies Nuez')")
    s = ST.sugerencias(con)
    assert [(x["producto"], x["clave"]) for x in s] == [("Brownies", "brownies nuez")]
