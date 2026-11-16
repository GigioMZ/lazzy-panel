from datetime import date, timedelta

from lazzy import captura as C
from lazzy import historico as H
from lazzy import proveedores as PV
from lazzy import seguimiento as S
from lazzy.db import connect
from lazzy.finance import load_cfg

INI = date(2026, 6, 1)


def _ventas(con, dias=70, cambio=35, prod="Latte", p0=3500, p1=3990, q0=20, q1=19):
    for i in range(dias):
        f = (INI + timedelta(days=i)).isoformat()
        q, p = (q0, p0) if i < cambio else (q1, p1)
        con.execute("INSERT INTO venta_producto(fecha,categoria,producto,cantidad,bruto) VALUES (?,?,?,?,?)", (f, "Café", prod, q, q * p))
        con.execute("INSERT INTO venta_hora(fecha,hora,bruto,tickets) VALUES (?,?,?,?)", (f, 12, 100000, 40))
    con.commit()


def _con():
    return connect(":memory:")


def test_detecta_cambio_de_precio_y_mide_efecto():
    con = _con()
    _ventas(con)
    con.execute("INSERT INTO producto_costo VALUES ('Latte', 900, 'manual', '2026-01-01')")
    ids = S.detectar(con, avisar_desde="2026-06-01")
    ev = con.execute("SELECT * FROM evento WHERE tipo='precio'").fetchall()
    assert len(ev) == 1 and ev[0]["fecha"] == (INI + timedelta(days=35)).isoformat() and ids
    assert "3.500" in ev[0]["titulo"] and "3.990" in ev[0]["titulo"]
    assert con.execute("SELECT COUNT(*) FROM notificacion").fetchone()[0] == 1
    assert S.detectar(con) == []                       # no duplica al volver a subir
    g = S.lista(con, load_cfg(con))[0]
    p = g["medida"]["prod"]
    assert round(p["cambio_precio"]) == 14 and round(p["cambio_uds"]) == -5
    assert p["efecto_mes"] > 0 and g["veredicto"][0] == "ok"
    assert -0.5 < p["elasticidad"] < 0


def test_producto_nuevo_y_midiendo():
    con = _con()
    _ventas(con, dias=70, cambio=99)
    for i in range(60, 70):
        f = (INI + timedelta(days=i)).isoformat()
        con.execute("INSERT INTO venta_producto(fecha,categoria,producto,cantidad,bruto) VALUES (?,?,?,?,?)", (f, "Pastelería", "Tiramisú", 3, 3 * 3990))
    con.commit()
    S.detectar(con)
    g = [x for x in S.lista(con, load_cfg(con)) if x["tipo"] == "nuevo"][0]
    assert g["eventos"][0]["producto"] == "Tiramisú"
    assert g["medida"]["midiendo"] and g["veredicto"][0] == "neutro"


def test_decision_manual_mide_el_local():
    con = _con()
    _ventas(con, cambio=99)
    S.crear(con, "2026-07-06", "decision", None, "Abrimos los sábados", {}, "manual")
    g = S.lista(con, load_cfg(con), "decision")[0]
    assert g["medida"]["local"]["cambio"] == 0 and "se mantuvo" in g["veredicto"][1] or g["veredicto"][0] in ("ok", "bad")


def _factura(con, folio, fecha, precio, desc="Cheesecake Berries Individual"):
    d = {"clase": "documento", "tipo": "factura", "rut": "77323879-0", "razon_social": "Ana Gonzalez Marroquin Eirl",
         "folio": folio, "fecha": fecha, "neto": precio * 10, "exento": 0, "iva": round(precio * 1.9), "total": round(precio * 11.9),
         "categoria": "pasteleria", "lineas": [{"descripcion": desc, "cantidad": 10, "precio_neto": precio, "total_neto": precio * 10}],
         "avisos": []}
    return C.guardar(con, C.preparar(con, d), "javi")


def test_alias_vinculo_y_costo_desde_factura():
    con = _con()
    _ventas(con, prod="Cheesecake berries", p0=4490, p1=4490)
    assert PV.nombre(con, "77323879-0") == "Cake House"
    _factura(con, 1, "2026-06-10", 3000)
    _factura(con, 2, "2026-07-20", 3300)
    prods = PV.productos(con, "77323879-0")
    assert prods[0]["texto"] == "Cheesecake Berries Individual" and prods[0]["cambio"] == 10.0
    assert prods[0]["sugerido"] == {"tipo": "producto", "destino": "Cheesecake berries"}
    PV.vincular(con, "77323879-0", prods[0]["clave"], "producto", "Cheesecake berries", 1)
    assert con.execute("SELECT costo, fuente FROM producto_costo WHERE producto='Cheesecake berries'").fetchone()[:] == (3300, "factura")
    assert load_cfg(con, "2026-06-30")["costo_producto"]["Cheesecake berries"]["costo"] == 3000   # historial por fecha
    ev = con.execute("SELECT * FROM evento WHERE tipo='costo'").fetchone()
    assert ev["fecha"] == "2026-07-20" and "Cake House subió" in ev["titulo"]
    r = _factura(con, 3, "2026-08-05", 3500)                                   # factura nueva por el bot: avisa y actualiza
    assert "Actualicé el costo de Cheesecake berries" in r["texto"]
    assert con.execute("SELECT costo FROM producto_costo WHERE producto='Cheesecake berries'").fetchone()[0] == 3500
    g = S.lista(con, load_cfg(con), "costo")[0]
    assert g["veredicto"][0] == "bad" and g["medida"]["mes"] > 0
    assert PV.movimientos(con, dias=10000)[0]["proveedor"] == "Cake House"
    PV.poner_alias(con, "77323879-0", "")
    assert PV.nombre(con, "77323879-0") == "Ana Gonzalez Marroquin Eirl"


def test_vinculo_a_insumo_con_divisor():
    con = _con()
    iid = con.execute("INSERT INTO insumo(nombre,unidad,cantidad_formato,precio_formato,actualizado) VALUES ('Leche','ml',1000,900,'2026-01-01')").lastrowid
    con.execute("INSERT INTO receta VALUES ('Latte', ?, 250)", (iid,))
    _factura(con, 7, "2026-06-10", 12000, "Leche Entera 1L x 12")
    PV.vincular(con, "77323879-0", C._clave("Leche Entera 1L x 12"), "insumo", str(iid), 12)
    assert con.execute("SELECT precio_formato FROM insumo WHERE id=?", (iid,)).fetchone()[0] == 1000
    assert load_cfg(con)["costo_producto"]["Latte"]["costo"] == 250


def test_carga_historica_omite_comprobantes_y_otras_empresas():
    con = _con()
    con.execute("INSERT INTO parametro VALUES ('rut_empresa','77618245-1') ON CONFLICT DO NOTHING")
    lectura = {"a.pdf": {"tipo": "factura", "rut_emisor": "77323879-0", "razon_social": "Ana", "rut_receptor": "77618245-1",
                         "folio": 9, "fecha": "2026-05-02", "neto": 1000, "iva": 190, "total": 1190,
                         "lineas": [{"descripcion": "Brownie", "cantidad": 1, "precio_unitario": 1000, "total_linea": 1000}]},
               "b.pdf": {"tipo": "factura", "rut_emisor": "1-9", "razon_social": "X", "rut_receptor": "11111111-1", "folio": 3,
                         "fecha": "2026-05-02", "neto": 1000, "iva": 190, "total": 1190,
                         "lineas": [{"descripcion": "Algo", "cantidad": 1, "precio_unitario": 1000, "total_linea": 1000}]}}
    archivos = [("a.pdf", b"a.pdf"), ("b.pdf", b"b.pdf"), ("Comprobante_123.pdf", b"x")]
    tid = H.crear(con, len(archivos))
    r = H.procesar(con, tid, archivos, leer=lambda c, datos: C.normalizar_doc(lectura[datos.decode()]))
    assert r["facturas"] == 1 and r["omitidos"] == 2 and not r["errores"]
    assert con.execute("SELECT texto FROM factura_linea").fetchone()[0] == "Brownie"
    assert con.execute("SELECT estado FROM trabajo WHERE id=?", (tid,)).fetchone()[0] == "listo"
