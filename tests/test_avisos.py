from datetime import date

from lazzy import ayuda as A
from lazzy import bot as B
from lazzy.db import connect


def test_recordatorios_por_persona():
    con = connect(":memory:")
    con.execute("INSERT INTO venta_hora VALUES ('2026-11-06',10,100000,10)")
    con.execute("INSERT INTO venta_producto VALUES ('2026-11-06','Café','Latte',3,9000)")
    lunes = A.recordatorios(con, date(2026, 11, 16))
    assert "Subir ventas y productos de FUDO" in lunes["guille"] and "javi" not in lunes
    assert "Subir compras del SII de octubre 2026" in lunes["guille"]
    assert A.recordatorios(con, date(2026, 11, 18)) == {}
    assert "vence en 3 días" in A.recordatorios(con, date(2026, 11, 17))["guille"]       # F29 vence el 20/11
    assert "último día del mes" in A.recordatorios(con, date(2026, 11, 30))["guille"]
    assert A.recordatorios(con, date(2026, 11, 19)) == {}

    enviados = []

    class TG:
        def enviar(self, chat, texto, botones=None):
            enviados.append((chat, texto))
    con.execute("INSERT INTO telegram_chat VALUES (1,'Javiera','javi','x'), (2,'Guille','guillermo','x')")
    B.enviar_recordatorios(con, TG(), date(2026, 11, 16))
    assert [c for c, _ in enviados] == [2]


def test_campanita_en_el_panel(tmp_path):
    from test_web import client, login
    c, con = client(tmp_path)
    login(c)
    t = c.get("/").text
    assert 'class="avisos"' in t and "Subir ventas y productos de FUDO" in t and "Ver la rutina completa" in t
