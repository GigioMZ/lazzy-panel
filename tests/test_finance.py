from lazzy.db import connect
from lazzy.finance import costo_receta, iva_mes, load_cfg, margen_productos, pnl_mes, seed_from_config
from lazzy.importers.rcv import import_rcv_compras, periodo_from_filename

CFG = {
    "empresa": {"iva": 0.19, "ppm_tasa": 0.01},
    "costo_fijo": [{"nombre": "s", "categoria": "remuneraciones", "monto": 300, "desde": "2026-01"},
                   {"nombre": "viejo", "categoria": "honorarios", "monto": 999, "desde": "2020-01", "hasta": "2025-12"}],
    "proveedores": {"1-9": "cafe", "2-7": "comision_tarjetas"},
    "categorias": {"cafe": "costo_venta", "comision_tarjetas": "gasto_operacional",
                   "remuneraciones": "personal", "honorarios": "gasto_operacional"},
}
HDR = ("Nro;Tipo Doc;Tipo Compra;RUT Proveedor;Razon Social;Folio;Fecha Docto;Fecha Recepcion;Fecha Acuse;"
       "Monto Exento;Monto Neto;Monto IVA Recuperable;Monto Iva No Recuperable;Codigo IVA No Rec.;Monto Total;"
       "Monto Neto Activo Fijo;IVA Activo Fijo\n")


def setup(tmp_path):
    con = connect(":memory:")
    con.execute("INSERT INTO venta_hora VALUES ('2026-10-01',9,1190,2)")
    con.execute("INSERT INTO venta_hora VALUES ('2026-10-02',9,1190,2)")
    f = tmp_path / "RCV_COMPRA_REGISTRO_1-9_202610_2.csv"
    f.write_text(HDR +
                 "1;33;Del Giro;1-9;CAFE SA;10;01/10/2026;x;;0;400;76;;;476;;\n"
                 "2;61;Del Giro;1-9;CAFE SA;11;05/10/2026;x;;0;100;19;;;119;;\n"
                 "3;33;Del Giro;2-7;TBK;12;05/10/2026;x;;0;50;10;;;60;;\n"
                 "4;33;Del Giro;3-5;NUEVO;13;05/10/2026;x;;0;200;38;;;238;;\n", encoding="utf-8")
    return con, f


def test_periodo_from_filename():
    assert periodo_from_filename("RCV_COMPRA_REGISTRO_77618245-1_202610_2.csv") == "2026-10"
    assert periodo_from_filename("RCV_COMPRA_REGISTRO_77618245-1_202610.csv") == "2026-10"
    assert periodo_from_filename("RCV_COMPRA_REGISTRO_77618245-1_202610 (2).csv") == "2026-10"


def test_pnl_and_iva(tmp_path):
    con, f = setup(tmp_path)
    assert import_rcv_compras(con, f)["documentos"] == 4
    import_rcv_compras(con, f)  # idempotente
    assert con.execute("SELECT COUNT(*) FROM compra").fetchone()[0] == 4
    p = pnl_mes(con, "2026-10", CFG)
    assert p["ventas"]["neto"] == 2000 and p["ventas"]["iva_debito"] == 380
    assert p["costo_venta"] == 300            # 400 - nota de crédito 100
    assert p["personal"] == 300 and p["gasto_operacional"] == 50   # costo fijo vencido no cuenta
    assert p["sin_clasificar"] == 200
    assert p["resultado_operacional"] == 2000 - 300 - 300 - 50 - 200
    assert p["pct"]["prime_cost"] == 30.0
    # equilibrio: fijos 300 / margen de contribución (1 - 350/2000)
    assert p["equilibrio"]["venta_neta_mes"] == round(300 / (1 - 350 / 2000))
    i = iva_mes(con, "2026-10", CFG)
    assert i["credito"] == 76 - 19 + 10 + 38 and i["iva_a_pagar"] == 380 - 105 and i["ppm"] == 20


def test_override_categoria(tmp_path):
    con, f = setup(tmp_path)
    import_rcv_compras(con, f)
    con.execute("UPDATE compra SET categoria='cafe' WHERE folio=13")
    import_rcv_compras(con, f)  # reimportar no pisa la clasificación manual
    p = pnl_mes(con, "2026-10", CFG)
    assert p["sin_clasificar"] == 0 and p["costo_venta"] == 500


def test_margen_productos():
    con = connect(":memory:")
    con.executemany("INSERT INTO venta_producto VALUES (?,?,?,?,?)", [
        ("2026-10-01", "Café", "Latte", 10, 11900),      # neto 10000 -> 1000 c/u
        ("2026-10-02", "Café", "Latte", 10, 11900),
        ("2026-10-01", "Pastel", "Torta", 2, 4760),      # neto 4000 -> 2000 c/u
        ("2026-10-01", "Otros", "Sin costo", 1, 1190),
        ("2026-09-01", "Café", "Latte", 99, 99999),      # fuera de rango
    ])
    cfg = {"empresa": {"iva": 0.19}, "costo_producto": {"Latte": {"costo": 300, "fuente": "x"},
                                                        "Torta": {"costo": 1500, "fuente": "x"}}}
    r = margen_productos(con, "2026-10", "2026-10", cfg)
    d = {x["producto"]: x for x in r["productos"]}
    assert d["Latte"]["cantidad"] == 20 and d["Latte"]["margen_pct"] == 70.0 and d["Latte"]["contribucion"] == 14000
    assert d["Torta"]["margen_unit"] == 500 and d["Torta"]["contribucion"] == 1000
    assert d["Latte"]["clase"] == "estrella" and d["Torta"]["clase"] == "perro"
    assert d["Sin costo"]["costo"] is None and d["Sin costo"]["clase"] is None
    assert r["cobertura_pct"] == round(100 * 24000 / 25000, 1)


def test_seed_y_receta():
    con = connect(":memory:")
    cfg = {"empresa": {"iva": 0.19, "ppm_tasa": 0.0025}, "proveedores": {"1-9": "cafe"},
           "costo_fijo": [{"nombre": "s", "categoria": "remuneraciones", "monto": 300, "desde": "2026-01"}],
           "costo_producto": {"Torta": {"costo": 1500, "fuente": "factura"}, "Latte": {"costo": 1, "fuente": "x"}},
           "insumo": [{"nombre": "Café", "unidad": "g", "cantidad": 1000, "precio": 20000},
                      {"nombre": "Leche", "unidad": "ml", "cantidad": 1000, "precio": 1000}],
           "receta": {"Latte": {"Café": 18, "Leche": 300}}}
    seed_from_config(con, cfg)
    seed_from_config(con, cfg)   # idempotente
    assert con.execute("SELECT COUNT(*) FROM costo_fijo").fetchone()[0] == 1
    assert costo_receta(con, "Latte") == 660 and costo_receta(con, "Torta") is None
    c = load_cfg(con)
    assert c["costo_producto"]["Latte"] == {"costo": 660, "fuente": "receta"}    # la receta manda sobre el costo fijo
    assert c["costo_producto"]["Torta"]["costo"] == 1500 and c["empresa"]["ppm_tasa"] == 0.0025
    con.execute("UPDATE insumo SET precio_formato=2000 WHERE nombre='Leche'")
    assert load_cfg(con)["costo_producto"]["Latte"]["costo"] == 960


def test_factura_cuenta_en_su_mes_y_el_iva_en_su_periodo(tmp_path):
    con, f = setup(tmp_path)
    f.write_text(HDR + "1;33;Del Giro;1-9;CAFE SA;10;28/09/2026;x;;0;400;76;;;476;;\n", encoding="utf-8")
    import_rcv_compras(con, f)                        # archivo del periodo 2026-10
    con.execute("INSERT INTO venta_hora VALUES ('2026-09-30',9,1190,1)")
    assert pnl_mes(con, "2026-09", CFG)["costo_venta"] == 400      # resultados: mes de la factura
    assert pnl_mes(con, "2026-10", CFG)["costo_venta"] == 0
    assert iva_mes(con, "2026-10", CFG)["credito"] == 76          # IVA: periodo del SII
    assert iva_mes(con, "2026-09", CFG)["credito"] == 0


def test_cambio_de_anio():
    from lazzy.db import mes_rango
    from lazzy.finance import fin_de_mes
    assert mes_rango("2026-12") == ("2026-12-01", "2027-01-01") and fin_de_mes("2027-02") == "2027-02-28"
    con = connect(":memory:")
    con.execute("INSERT INTO venta_hora VALUES ('2026-12-31',9,1190,1)")
    con.execute("INSERT INTO venta_hora VALUES ('2027-01-01',9,2380,1)")
    cfg = dict(CFG, costo_fijo=[{"nombre": "s", "categoria": "remuneraciones", "monto": 300, "desde": "2026-11", "hasta": "2026-12"},
                                {"nombre": "s2", "categoria": "remuneraciones", "monto": 500, "desde": "2027-01"}])
    assert pnl_mes(con, "2026-12", cfg)["ventas"]["neto"] == 1000 and pnl_mes(con, "2026-12", cfg)["personal"] == 300
    assert pnl_mes(con, "2027-01", cfg)["ventas"]["neto"] == 2000 and pnl_mes(con, "2027-01", cfg)["personal"] == 500


def test_comision_tarjetas_facturada_y_estimada():
    from lazzy.finance import comision_mes
    con = connect(":memory:")
    cfg = dict(CFG, proveedores={"9-9": "comision_tarjetas"})
    for m, deb, cre in (("2026-09", 900000, 100000), ("2026-10", 1800000, 200000)):
        con.execute("INSERT INTO venta_hora VALUES (?,9,?,1)", (m + "-15", deb + cre))
        con.execute("INSERT INTO venta_medio_pago VALUES (?, 'Tarj. Débito', ?, 1)", (m + "-15", deb))
        con.execute("INSERT INTO venta_medio_pago VALUES (?, 'Tarj. Crédito', ?, 1)", (m + "-15", cre))
    con.execute("INSERT INTO compra VALUES ('2026-10',33,'9-9','TBK',1,'2026-09-28',0,20000,3800,0,23800,0,NULL,'sii')")
    con.execute("INSERT INTO compra VALUES ('2026-10',61,'9-9','TBK',2,'2026-09-30',0,1000,190,0,1190,0,NULL,'sii')")
    sep = comision_mes(con, "2026-09", cfg)
    assert sep["facturado"] == 19000 and not sep["estimada"] and sep["pct"] == 1.9
    octu = comision_mes(con, "2026-10", cfg)        # aún sin factura: se estima con la tasa de septiembre
    assert octu["estimada"] and octu["monto"] == 38000 and octu["tasa"]["fuente"] == "calculada"
    assert pnl_mes(con, "2026-10", cfg)["lineas"]["gasto_operacional"]["comision_tarjetas"] == 38000
    cfg["empresa"] = dict(cfg["empresa"], comision_debito=1.0, comision_credito=2.5)
    assert comision_mes(con, "2026-10", cfg)["monto"] == 18000 + 5000


def test_historial_de_costos():
    from datetime import date
    from lazzy.finance import registrar_costo, registrar_insumo
    con = connect(":memory:")
    seed_from_config(con, {"empresa": {"iva": 0.19}, "costo_producto": {"Torta": {"costo": 1000, "fuente": "factura"}},
                           "insumo": [{"nombre": "Leche", "unidad": "ml", "cantidad": 1000, "precio": 1000}],
                           "receta": {"Latte": {"Leche": 300}}})
    registrar_costo(con, "Torta", 1500, "manual", hoy=date(2027, 1, 10))
    con.execute("UPDATE insumo SET precio_formato=2000")
    registrar_insumo(con, 1, hoy=date(2027, 1, 10))
    viejo, nuevo = load_cfg(con, "2026-12-31")["costo_producto"], load_cfg(con, "2027-01-31")["costo_producto"]
    assert viejo["Torta"]["costo"] == 1000 and nuevo["Torta"]["costo"] == 1500
    assert viejo["Latte"]["costo"] == 300 and nuevo["Latte"]["costo"] == 600
    assert load_cfg(con)["costo_producto"]["Torta"]["costo"] == 1500


def test_reimportar_borra_ventas_anuladas(tmp_path):
    import openpyxl
    from datetime import datetime
    from lazzy.importers.fudo_report import import_fudo_report
    con = connect(":memory:")
    def xlsx(filas):
        wb = openpyxl.Workbook(); wb.remove(wb.active)
        for nombre in ("Evolución de ventas", "Medios de pago", "Origen de ventas"):
            ws = wb.create_sheet(nombre); ws.append(["h"] * 5)
        for d, h, v in filas:
            wb["Evolución de ventas"].append([datetime.fromisoformat(d), None, h, v, 1])
        p = tmp_path / "v.xlsx"; wb.save(p); return p
    import_fudo_report(con, xlsx([("2026-10-01", 9, 1000), ("2026-10-01", 10, 500), ("2026-10-02", 9, 700)]))
    import_fudo_report(con, xlsx([("2026-10-01", 9, 1000), ("2026-10-02", 9, 700)]))   # la venta de las 10 se anuló
    assert con.execute("SELECT COUNT(*), SUM(bruto) FROM venta_hora").fetchone()[:] == (2, 1700)
