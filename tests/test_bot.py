import json
from datetime import date, datetime

import pytest

from lazzy import bot as B
from lazzy import captura as C
from lazzy.db import connect
from lazzy.finance import load_cfg, pnl_mes

FACTURA = {"tipo": "factura", "rut_emisor": "77.323.879-0", "razon_social": "Ana Gonzalez Marroquin Eirl",
           "rut_receptor": "77.618.245-1", "folio": 6176, "fecha": "2026-11-05", "neto": 104496, "iva": 19854,
           "total": 124350, "categoria": "pasteleria", "concepto": "Pastelería",
           "lineas": [{"descripcion": "Cheesecake Berries Individual", "cantidad": 24, "precio_unitario": 3100, "total_linea": 74400},
                      {"descripcion": "Carrot Cake", "cantidad": 12, "precio_unitario": 2100, "total_linea": 25200},
                      {"descripcion": "Brownies Nuez", "cantidad": 12, "precio_unitario": 1490, "total_linea": 17880},
                      {"descripcion": "Delivery La Florida", "cantidad": 1, "precio_unitario": 6870, "total_linea": 6870}]}


class TG:
    def __init__(self):
        self.enviados, self.editados = [], []

    def __call__(self, metodo, **k):
        return {}

    def enviar(self, chat, texto, botones=None):
        self.enviados.append((chat, texto, botones))

    def editar(self, chat, msg, texto, botones=None):
        self.editados.append((chat, texto, botones))

    def bajar(self, file_id):
        return b"%PDF-falso"


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    con = connect(":memory:")
    respuestas = []
    monkeypatch.setattr(C, "llamar_modelo", lambda con, contenido, h, tipo: respuestas.pop(0))
    monkeypatch.setattr(C, "texto_pdf", lambda datos: "x" * 300)
    tg = TG()
    bot = B.Bot(con, tg, tmp_path)
    codigo = B.nuevo_codigo(con, "javi")
    bot.procesar({"message": {"chat": {"id": 7}, "from": {"first_name": "Javi"}, "text": f"/vincular {codigo}"}})
    return con, tg, bot, respuestas


def boton(bot, tg, data):
    bot.procesar({"callback_query": {"id": "q", "data": data, "message": {"chat": {"id": 7}, "message_id": 1, "text": ""}}})
    return tg.editados[-1][1]


def test_vinculacion(entorno):
    con, tg, bot, _ = entorno
    assert "quedaste vinculado como javi" in tg.enviados[-1][1]
    bot.procesar({"message": {"chat": {"id": 99}, "text": "hola"}})
    assert "privado" in tg.enviados[-1][1]
    bot.procesar({"message": {"chat": {"id": 99}, "text": "/vincular ZZZZZZ"}})
    assert "no existe" in tg.enviados[-1][1]


def test_factura_pdf_con_precios_con_iva(entorno):
    con, tg, bot, resp = entorno
    resp.append(dict(FACTURA))
    bot.procesar({"message": {"chat": {"id": 7}, "document": {"file_id": "f", "file_name": "factura.pdf", "mime_type": "application/pdf"}}})
    texto, botones = tg.enviados[-1][1], tg.enviados[-1][2]
    assert "Factura 6176" in texto and "a $2.605 c/u sin IVA" in texto      # 3.100 con IVA -> 2.605 sin IVA
    cid = botones[0][0]["callback_data"].split(":")[1]
    assert "Guardé la factura 6176" in boton(bot, tg, f"ok:{cid}")
    c = con.execute("SELECT * FROM compra").fetchone()
    assert (c["rut"], c["folio"], c["neto"], c["origen"], c["periodo"]) == ("77323879-0", 6176, 104496, "bot", "2026-11")
    assert con.execute("SELECT categoria FROM proveedor WHERE rut='77323879-0'").fetchone()[0] == "pasteleria"
    assert con.execute("SELECT COUNT(*) FROM factura_linea").fetchone()[0] == 4
    # el SII trae la misma factura después: no se duplica
    con.execute("INSERT INTO compra(periodo,tipo_doc,rut,razon_social,folio,fecha_doc,exento,neto,iva_rec,iva_no_rec,total) "
                "VALUES ('2026-11',33,'77323879-0','Ana',6176,'2026-11-05',0,104496,19854,0,124350) "
                "ON CONFLICT(tipo_doc,rut,folio) DO UPDATE SET periodo=excluded.periodo")
    assert con.execute("SELECT COUNT(*) FROM compra").fetchone()[0] == 1
    # siguiente factura con el cheesecake más caro: avisa
    f2 = json.loads(json.dumps(FACTURA)); f2["folio"] = 6200; f2["fecha"] = "2026-11-12"
    f2["lineas"][0].update(precio_unitario=3400, total_linea=81600); f2["lineas"] = f2["lineas"][:1]
    f2.update(neto=68571, iva=13029, total=81600)
    resp.append(f2)
    bot.procesar({"message": {"chat": {"id": 7}, "document": {"file_id": "f", "file_name": "f2.pdf", "mime_type": "application/pdf"}}})
    assert "Cheesecake Berries Individual subió 10%" in tg.enviados[-1][1] and "(proveedor conocido)" in tg.enviados[-1][1]
    # deshacer
    cid = tg.enviados[-1][2][0][0]["callback_data"].split(":")[1]
    boton(bot, tg, f"ok:{cid}")
    bot.procesar({"message": {"chat": {"id": 7}, "text": "/deshacer"}})
    assert con.execute("SELECT COUNT(*) FROM compra WHERE folio=6200").fetchone()[0] == 0


def test_factura_sin_folio_no_se_guarda(entorno):
    con, tg, bot, resp = entorno
    f = dict(FACTURA); f.pop("folio")
    resp.append(f)
    bot.procesar({"message": {"chat": {"id": 7}, "photo": [{"file_id": "p", "file_size": 10}]}})
    assert "No pude leer el RUT del proveedor o el número" in tg.enviados[-1][1] and tg.enviados[-1][2] is None


def test_texto_cuentas_reemplaza_estimado(entorno):
    con, tg, bot, resp = entorno
    con.execute("INSERT INTO costo_fijo(nombre,categoria,monto,desde,estimado) VALUES ('Cuentas','servicios',234000,'2026-01',1)")
    con.execute("INSERT INTO venta_hora VALUES ('2026-11-02',9,1190000,10)")
    con.commit()
    antes = pnl_mes(con, "2026-11", load_cfg(con))["gasto_operacional"]
    resp.append({"gastos": [{"concepto": "Luz", "categoria": "servicios", "monto": 250730, "fecha": "2026-11-10"},
                            {"concepto": "Agua", "categoria": "servicios", "monto": 33209}]})
    bot.procesar({"message": {"chat": {"id": 7}, "text": "luz 250730, agua 33209"}})
    assert "Luz: $250.730" in tg.enviados[-1][1]
    cid = tg.enviados[-1][2][0][0]["callback_data"].split(":")[1]
    assert "Guardé 2 gastos" in boton(bot, tg, f"ok:{cid}")
    assert boton(bot, tg, f"ok:{cid}").endswith("(ya procesado)")          # doble clic no duplica
    despues = pnl_mes(con, "2026-11", load_cfg(con))["gasto_operacional"]
    assert despues - antes == 250730 + 33209 - 234000


def test_cambiar_categoria_y_descartar(entorno):
    con, tg, bot, resp = entorno
    f = dict(FACTURA, categoria=None, rut_emisor="11.111.111-1")
    resp.append(f)
    bot.procesar({"message": {"chat": {"id": 7}, "document": {"file_id": "f", "file_name": "x.pdf"}}})
    assert "Elige la categoría" in tg.enviados[-1][1]
    cid = tg.enviados[-1][2][0][0]["callback_data"].split(":")[1]
    assert "Categoría: Café" in boton(bot, tg, f"set:{cid}:cafe")
    assert boton(bot, tg, f"no:{cid}").endswith("Descartado.")
    assert con.execute("SELECT COUNT(*) FROM compra").fetchone()[0] == 0


def test_normalizar_y_resumen():
    d = C.normalizar_doc({"tipo": "factura", "rut_emisor": "76.633.213- 7", "folio": "1713", "fecha": "2026-11-04",
                          "neto": 99800, "iva": 18962, "total": 118762, "categoria": "pasteleria", "concepto": "x",
                          "lineas": [{"descripcion": "Tiramisú S/A", "cantidad": 32, "precio_unitario": 2900, "total_linea": 92800},
                                     {"descripcion": "Despacho", "cantidad": 1, "precio_unitario": 7000, "total_linea": 7000}]})
    assert d["rut"] == "76633213-7" and d["folio"] == 1713 and not d["precios_con_iva"] and d["lineas"][0]["precio_neto"] == 2900
    malo = C.normalizar_doc({"tipo": "factura", "fecha": "x", "neto": 100, "iva": 19, "total": 500, "categoria": "nada", "concepto": ""})
    assert malo["categoria"] is None and "no cuadran" in malo["avisos"][0]


def test_resumen_semanal():
    con = connect(":memory:")
    for d, v in (("2026-11-09", 200000), ("2026-11-10", 100000), ("2026-11-02", 150000)):
        con.execute("INSERT INTO venta_hora VALUES (?,10,?,10)", (d, v))
    con.execute("INSERT INTO venta_hora VALUES ('2026-11-15',10,0,0)")
    t = B.resumen_semana(con, hoy=date(2026, 11, 16))
    assert "semana del 9/11 al 15/11" in t and "Vendiste $300.000" in t and "+100% vs la semana anterior" in t
    assert B.toca_resumen(con, datetime(2026, 11, 16, 9, 5)) and not B.toca_resumen(con, datetime(2026, 11, 17, 9, 5))


def test_claves_en_archivo_privado(tmp_path, monkeypatch):
    import os
    import stat
    from lazzy import claves
    monkeypatch.setenv("LAZZY_BOT_ENV", str(tmp_path / "bot.env"))
    monkeypatch.setattr(claves, "probar_telegram", lambda t: "lazzy_bot")
    monkeypatch.setattr(claves, "probar_anthropic", lambda c: None)
    assert claves.estado() == {"telegram": False, "anthropic": False, "nombre": ""}
    claves.guardar("123:abc", None)
    claves.guardar(None, "sk-ant-x")
    assert claves.leer() == {"BOT_NOMBRE": "lazzy_bot", "TELEGRAM_TOKEN": "123:abc", "ANTHROPIC_API_KEY": "sk-ant-x"}
    assert stat.S_IMODE(os.stat(tmp_path / "bot.env").st_mode) == 0o600
    def mala(t):
        raise ValueError("El token de Telegram no funciona.")
    monkeypatch.setattr(claves, "probar_telegram", mala)
    with pytest.raises(ValueError):
        claves.guardar("malo", None)
    assert claves.leer()["TELEGRAM_TOKEN"] == "123:abc"


def _pdf(bot, resp, datos):
    resp.append(dict(datos))
    bot.procesar({"message": {"chat": {"id": 7}, "document": {"file_id": "f", "file_name": "f.pdf", "mime_type": "application/pdf"}}})


def test_corregir_un_producto_antes_de_guardar(entorno):
    con, tg, bot, resp = entorno
    _pdf(bot, resp, FACTURA)
    botones = tg.enviados[-1][2]
    assert botones[1][0]["callback_data"].startswith("ed:")
    cid = botones[0][0]["callback_data"].split(":")[1]
    assert "¿Qué producto quieres corregir?" in boton(bot, tg, f"ed:{cid}")
    assert "escríbeme lo correcto" in boton(bot, tg, f"ln:{cid}:0")
    bot.procesar({"message": {"chat": {"id": 7}, "text": "no sé"}})
    assert "No entendí la corrección" in tg.enviados[-1][1]
    bot.procesar({"message": {"chat": {"id": 7}, "text": "24 x 2900"}})
    assert "Corregido: 24 × Cheesecake Berries Individual a $2.900" in tg.enviados[-1][1]
    assert "El detalle corregido suma" in tg.enviados[-1][1]
    boton(bot, tg, f"ln:{cid}:3")
    bot.procesar({"message": {"chat": {"id": 7}, "text": "quitar"}})
    boton(bot, tg, f"ok:{cid}")
    filas = con.execute("SELECT texto, cantidad, precio_neto FROM factura_linea ORDER BY n").fetchall()
    assert len(filas) == 3 and tuple(filas[0]) == ("Cheesecake Berries Individual", 24, 2900)
    assert con.execute("SELECT neto FROM compra WHERE folio=6176").fetchone()[0] == 104496      # el total de la factura no cambia
    resp.append({"gastos": [{"concepto": "Luz", "categoria": "servicios", "monto": 250730}]})
    bot.procesar({"message": {"chat": {"id": 7}, "text": "luz 250730"}})                         # ya no hay edición pendiente
    assert "No entendí la corrección" not in tg.enviados[-1][1]


def test_deshacer_devuelve_costos_y_detalle(entorno):
    from lazzy import proveedores as PV
    con, tg, bot, resp = entorno
    con.execute("INSERT INTO venta_producto(fecha,categoria,producto,cantidad,bruto) VALUES ('2026-11-01','Pasteleria','Cheesecake berries',5,22450)")
    _pdf(bot, resp, FACTURA)
    boton(bot, tg, "ok:" + tg.enviados[-1][2][0][0]["callback_data"].split(":")[1])
    PV.vincular(con, "77323879-0", "cheesecake berries individual", "producto", "Cheesecake berries", 1)
    costo1 = con.execute("SELECT costo FROM producto_costo WHERE producto='Cheesecake berries'").fetchone()[0]
    f2 = dict(FACTURA, folio=6200, fecha="2026-11-12", lineas=[{"descripcion": "Cheesecake Berries Individual", "cantidad": 24,
                                                                "precio_unitario": 3500, "total_linea": 84000}], neto=70588, iva=13412, total=84000)
    _pdf(bot, resp, f2)
    boton(bot, tg, "ok:" + tg.enviados[-1][2][0][0]["callback_data"].split(":")[1])
    assert con.execute("SELECT costo FROM producto_costo WHERE producto='Cheesecake berries'").fetchone()[0] > costo1
    assert con.execute("SELECT COUNT(*) FROM evento WHERE tipo='costo'").fetchone()[0] == 1
    bot.procesar({"message": {"chat": {"id": 7}, "text": "/deshacer"}})
    assert con.execute("SELECT COUNT(*) FROM compra WHERE folio=6200").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM factura_linea WHERE folio=6200").fetchone()[0] == 0
    assert con.execute("SELECT costo FROM producto_costo WHERE producto='Cheesecake berries'").fetchone()[0] == costo1
    assert con.execute("SELECT COUNT(*) FROM evento WHERE tipo='costo'").fetchone()[0] == 0
    # factura que ya estaba por el SII: al deshacer se va el detalle que puso el bot y la factura queda
    con.execute("INSERT INTO compra VALUES ('2026-11',33,'77323879-0','Ana',6300,'2026-11-14',0,70588,13412,0,84000,0,NULL,'sii',NULL)")
    _pdf(bot, resp, dict(f2, folio=6300, fecha="2026-11-14"))
    assert "ya está en el panel" in tg.enviados[-1][1]
    boton(bot, tg, "ok:" + tg.enviados[-1][2][0][0]["callback_data"].split(":")[1])
    assert con.execute("SELECT COUNT(*) FROM factura_linea WHERE folio=6300").fetchone()[0] == 1
    bot.procesar({"message": {"chat": {"id": 7}, "text": "/deshacer"}})
    assert con.execute("SELECT COUNT(*) FROM factura_linea WHERE folio=6300").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM compra WHERE folio=6300").fetchone()[0] == 1
    assert con.execute("SELECT costo FROM producto_costo WHERE producto='Cheesecake berries'").fetchone()[0] == costo1
