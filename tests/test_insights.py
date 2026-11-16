from datetime import date

from lazzy import insights as I
from lazzy.db import connect
from lazzy.finance import CATEGORIAS, load_cfg, pnl_mes
from lazzy.glosario import CAT_NOMBRE, GLOSARIO


def base():
    con = connect(":memory:")
    con.execute("INSERT INTO venta_hora VALUES ('2026-09-01',9,119000,10)")     # neto 100.000
    con.execute("INSERT INTO venta_hora VALUES ('2026-10-01',9,238000,20)")     # neto 200.000
    con.execute("INSERT INTO proveedor VALUES ('1-9','CAFE','cafe')")
    con.execute("INSERT INTO compra VALUES ('2026-10',33,'1-9','CAFE',1,'2026-10-02',0,60000,11400,0,71400,0,NULL,'sii')")
    con.execute("INSERT INTO compra VALUES ('2026-10',33,'2-7','NUEVO',2,'2026-10-03',0,20000,3800,0,23800,0,NULL,'sii')")
    con.execute("INSERT INTO costo_fijo(nombre,categoria,monto,desde) VALUES ('s','remuneraciones',50000,'2026-01')")
    con.execute("INSERT INTO venta_producto VALUES ('2026-10-01','Café','Latte',20,238000)")
    con.commit()
    return con


def test_veredicto_y_desglose():
    con = base()
    cfg = load_cfg(con)
    p = pnl_mes(con, "2026-10", cfg)
    v = I.veredicto(p, parcial=False)
    assert v["con_resultado"] and "te quedaron $70.000" in v["titulo"] and "$350 fueron ganancia" in v["detalle"]
    d = {f["clave"]: f for f in I.desglose(p)}
    assert d["insumos"]["por_mil"] == 300 and d["sueldos"]["por_mil"] == 250 and d["sin_categoria"]["por_mil"] == 100
    assert d["ganancia"]["por_mil"] == 350 and d["ganancia"]["tipo"] == "gana" and d["ganancia"]["ancho"] == 100.0
    assert sum(f["por_mil"] for f in d.values()) == 1000
    # mes sin compras del SII: no se inventa una ganancia
    sin = I.veredicto(pnl_mes(con, "2026-09", cfg), parcial=False)
    assert not sin["con_resultado"] and "falta subir" in sin["detalle"]
    assert not I.veredicto(p, parcial=True)["con_resultado"]
    # pérdida
    con.execute("UPDATE costo_fijo SET monto=200000")
    perdida = I.veredicto(pnl_mes(con, "2026-10", load_cfg(con)), parcial=False)
    assert "perdiste $80.000" in perdida["titulo"] and perdida["tono"] == "bad"


def test_pendientes_y_contexto():
    con = base()
    cfg = load_cfg(con)
    ps = ["2026-09", "2026-10"]
    textos = " | ".join(x["texto"] for x in I.pendientes(con, cfg, ps, hoy=date(2026, 11, 20)))
    assert "1 proveedores sin categoría ($20.000" in textos
    assert "compras del SII de 1 mes (septiembre 2026)" in textos
    assert "cargadas hasta el 1 de octubre" in textos
    assert "1 productos vendidos no tienen costo" in textos and "PPM está en 0" in textos
    ctx = I.contexto(con, cfg, "2026-10", ps)
    assert ctx["prev"]["periodo"] == "2026-09" and ctx["prom_ganancia"] is None   # septiembre no tiene compras
    assert I.variacion(200000, 100000) == 100.0 and I.variacion(5, 0) is None
    assert I.estado(300, 350, 400, False) == "ok" and I.estado(380, 350, 400, False) == "warn" and I.estado(450, 350, 400, False) == "bad"


def test_glosario_cubre_categorias():
    assert set(CAT_NOMBRE) == set(CATEGORIAS)
    assert all(len(t) > 40 and n for n, t in GLOSARIO.values())
