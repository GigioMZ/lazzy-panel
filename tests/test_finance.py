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
                 "1;33;Del Giro;1-9;CAFE SA;10;28/09/2026;x;;0;400;76;;;476;;\n"
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
