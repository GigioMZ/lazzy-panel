import importlib
import os

from fastapi.testclient import TestClient


def client(tmp_path):
    os.environ.update(LAZZY_DB=str(tmp_path / "t.db"), LAZZY_SECRET="x" * 32)
    import lazzy.web as web
    importlib.reload(web)
    from lazzy.auth import set_password
    con = web.db()
    set_password(con, "ana", "clave-larga-123")
    con.execute("INSERT INTO venta_hora VALUES ('2026-10-01',9,119000,10)")
    con.execute("INSERT INTO venta_producto VALUES ('2026-10-01','Café','Latte',10,35000)")
    con.execute("INSERT INTO compra VALUES ('2026-10',33,'1-9','CAFE SA',1,'2026-10-02',0,20000,3800,0,23800,0,NULL)")
    con.commit()
    return TestClient(web.app, follow_redirects=False), con


def login(c):
    assert c.post("/login", data={"usuario": "ana", "clave": "mala"}).status_code == 200
    assert c.post("/login", data={"usuario": "Ana", "clave": "clave-larga-123"}).status_code == 303
    import re
    return re.search(r'name="csrf" value="(\w+)"', c.get("/insumos").text).group(1)


def test_requiere_login(tmp_path):
    c, _ = client(tmp_path)
    for url in ("/", "/resultados", "/productos", "/insumos", "/compras", "/costos-fijos", "/impuestos", "/cargar", "/ayuda"):
        r = c.get(url)
        assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.post("/insumos", data={"nombre": "x"}).status_code == 303


def test_paginas_y_edicion(tmp_path):
    c, con = client(tmp_path)
    csrf = login(c)
    for url in ("/?periodo=2026-10", "/resultados", "/productos", "/insumos", "/compras", "/costos-fijos",
                "/impuestos", "/cargar", "/receta?p=Latte", "/ayuda", "/?periodo=1999-01"):
        assert c.get(url).status_code == 200, url
    inicio = c.get("/?periodo=2026-10").text
    assert "proveedores sin categoría" in inicio and "Facturas sin categoría" in inicio
    assert 'class="tip"' in inicio and "te quedaron" in inicio
    # sin token CSRF no se puede escribir
    assert c.post("/insumos", data={"nombre": "Leche", "unidad": "ml", "cantidad": "1000", "precio": "1000"}).status_code == 403
    c.post("/insumos", data={"csrf": csrf, "nombre": "Leche", "unidad": "ml", "cantidad": "1000", "precio": "1.000"})
    iid = con.execute("SELECT id FROM insumo").fetchone()[0]
    c.post("/receta", data={"csrf": csrf, "producto": "Latte", "insumo_id": iid, "cantidad": "300"})
    assert "El costo sale de la receta" in c.get("/productos?desde=2026-10&hasta=2026-10").text
    from lazzy.finance import load_cfg
    assert load_cfg(con)["costo_producto"]["Latte"]["costo"] == 300
    c.post("/compras/proveedor", data={"csrf": csrf, "rut": "1-9", "nombre": "CAFE SA", "categoria": "cafe"})
    assert load_cfg(con)["proveedores"] == {"1-9": "cafe"}
    c.post("/costos-fijos", data={"csrf": csrf, "nombre": "Sueldo", "categoria": "remuneraciones", "monto": "500.000", "desde": "2026-01"})
    c.post("/costos-fijos", data={"csrf": csrf, "accion": "ppm", "ppm": "0,25"})
    cfg = load_cfg(con)
    assert cfg["costo_fijo"][0]["monto"] == 500000 and cfg["empresa"]["ppm_tasa"] == 0.0025
    c.post("/productos/costo", data={"csrf": csrf, "producto": "Torta", "costo": "1.500"})
    assert load_cfg(con)["costo_producto"]["Torta"] == {"costo": 1500, "fuente": "manual"}


def test_carga_archivo(tmp_path):
    c, con = client(tmp_path)
    csrf = login(c)
    csv = ("Nro;Tipo Doc;Tipo Compra;RUT Proveedor;Razon Social;Folio;Fecha Docto;Fecha Recepcion;Fecha Acuse;Monto Exento;"
           "Monto Neto;Monto IVA Recuperable;Monto Iva No Recuperable;Codigo IVA No Rec.;Monto Total;Monto Neto Activo Fijo\n"
           "1;33;Del Giro;2-7;LECHE SA;5;03/11/2026;x;;0;1000;190;;;1190;\n")
    r = c.post("/cargar", data={"csrf": csrf}, files=[("archivos", ("RCV_COMPRA_REGISTRO_1-9_202611.csv", csv, "text/csv")),
                                                      ("archivos", ("malo.pdf", b"x", "application/pdf"))])
    assert r.status_code == 303
    t = c.get("/cargar").text
    assert "1 documentos de compra, periodo 2026-11" in t and "formato no soportado" in t
    assert con.execute("SELECT COUNT(*) FROM compra WHERE periodo='2026-11'").fetchone()[0] == 1
