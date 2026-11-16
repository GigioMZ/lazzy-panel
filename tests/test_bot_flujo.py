"""Una conversación completa con el bot, de punta a punta, con la API de Anthropic simulada."""
import io
import json

import openpyxl
import pytest

from lazzy import alertas as AL
from lazzy import bot as B
from lazzy import captura as C
from lazzy import iva as IVA
from lazzy import preguntas as P
from lazzy import proveedores as PV
from lazzy.db import connect

FACT = {"tipo": "factura", "rut_emisor": "77323879-0", "razon_social": "Ana Gonzalez Marroquin Eirl", "rut_receptor": "77618245-1",
        "folio": 7001, "fecha": __import__("datetime").date.today().isoformat(), "neto": 69600, "iva": 13224, "total": 82824, "categoria": "pasteleria", "concepto": "Pastelería",
        "lineas": [{"descripcion": "Brownies Nuez", "cantidad": 24, "precio_unitario": 1300, "total_linea": 31200},
                   {"descripcion": "Cheesecake Berries Individual", "cantidad": 12, "precio_unitario": 3200, "total_linea": 38400}]}


def _stock_xlsx(filas):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Productos"
    ws.append(["Id.", "Categoría", "Código", "Nombre", "Disponibilidad", "Unidad", "Stock", "Unidad", "Stock Mínimo", "Unidad", "Proveedor"])
    for i, (n, q, m) in enumerate(filas):
        ws.append([i, "Pasteleria", None, n, q, "unid.", q, "unid.", m, "unid.", None])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


class TG:
    def __init__(self):
        self.msgs, self.archivo = [], b""

    def __call__(self, *a, **k):
        return {}

    def enviar(self, chat, texto, botones=None):
        self.msgs.append((texto, botones))

    def editar(self, chat, msg, texto, botones=None):
        self.msgs.append((texto, botones))

    def bajar(self, fid):
        return self.archivo

    @property
    def ultimo(self):
        return self.msgs[-1][0]


@pytest.fixture
def b(tmp_path, monkeypatch):
    con = connect(":memory:")
    respuestas = []
    monkeypatch.setattr(C, "llamar_modelo", lambda con, contenido, h, tipo: respuestas.pop(0))
    monkeypatch.setattr(C, "texto_pdf", lambda datos: "x" * 300)
    tg = TG()
    bot = B.Bot(con, tg, tmp_path)
    bot.procesar({"message": {"chat": {"id": 7}, "from": {"first_name": "Javi"}, "text": f"/vincular {B.nuevo_codigo(con, 'javi')}"}})
    from datetime import date, timedelta
    hoy = date.today()
    for f in [(hoy - timedelta(days=k)).isoformat() for k in (6, 5, 4)]:
        con.execute("INSERT INTO venta_hora(fecha,hora,bruto,tickets) VALUES (?,10,150000,20)", (f,))
        con.execute("INSERT INTO venta_producto(fecha,categoria,producto,cantidad,bruto) VALUES (?,'Pasteleria','Brownies',4,9960)", (f,))
    con.commit()

    def enviar(texto=None, archivo=None, nombre="f.pdf"):
        if archivo is not None:
            tg.archivo = archivo
            bot.procesar({"message": {"chat": {"id": 7}, "document": {"file_id": "x", "file_name": nombre}}})
        else:
            bot.procesar({"message": {"chat": {"id": 7}, "text": texto}})
        return tg.ultimo

    def tocar(data):
        bot.procesar({"callback_query": {"id": "q", "data": data, "message": {"chat": {"id": 7}, "message_id": 1, "text": ""}}})
        return tg.ultimo
    return con, tg, enviar, tocar, respuestas


def test_conversacion_completa(b, monkeypatch):
    con, tg, enviar, tocar, resp = b
    assert "/stock" in enviar("/ayuda") and "quedan 12 brownies" in tg.ultimo
    # 1. stock inicial desde el Excel de FUDO
    assert "Es el primero" in enviar(archivo=_stock_xlsx([("Brownies", 10, 4), ("Cheesecake berries", 6, 4), ("Ciabatta Jamon queso", 2, 0)]),
                                     nombre="stock.xlsx")
    # 2. factura: corregir un precio antes de guardar, guardar y ver el stock
    resp.append(dict(FACT))
    texto = enviar(archivo=b"%PDF")
    cid = tg.msgs[-1][1][0][0]["callback_data"].split(":")[1]
    assert "Brownies Nuez" in texto and "Corregir" in tg.msgs[-1][1][1][0]["text"]
    tocar(f"ed:{cid}")
    tocar(f"ln:{cid}:0")
    assert "Corregido: 24 × Brownies Nuez a $1.250" in enviar("precio 1250")
    # primero sin vínculos: avisa que no se suman solos
    assert "no están vinculados al stock" in tocar(f"ok:{cid}")
    assert enviar("/deshacer").startswith("Listo, borré")
    PV.vincular(con, "77323879-0", "brownies nuez", "producto", "Brownies", 1)
    PV.vincular(con, "77323879-0", "cheesecake berries individual", "producto", "Cheesecake berries", 1)
    resp.append(dict(FACT))
    enviar(archivo=b"%PDF")
    cid = tg.msgs[-1][1][0][0]["callback_data"].split(":")[1]
    t = tocar(f"ok:{cid}")
    assert "+24 Brownies: ahora deberían quedar 34" in t and "+12 Cheesecake berries: ahora deberían quedar 18" in t
    # 3. ajustar lo que no calza, merma y llegadas sin factura
    assert "quedan 30. Esperaba 34: faltan 4" in enviar("quedan 30 brownies")
    t = enviar("se botaron 2 cheesecake berries por vencidos")
    assert "Anoté la merma (vencidos)" in t and "-2 Cheesecake berries: ahora deberían quedar 16" in t
    assert con.execute("SELECT origen FROM stock_entrada").fetchone()[0].startswith("merma")
    assert "+10 Ciabatta Jamon queso: ahora deberían quedar 12" in enviar("llegaron 10 ciabatta jamon queso")
    assert "borré lo último" in enviar("/deshacer")
    assert "Ciabatta Jamon queso: 2" in enviar("/stock")
    assert "Brownies: 30" in tg.ultimo and "Cheesecake berries: 16" in tg.ultimo
    # 4. cuentas y gastos escritos
    resp.append({"gastos": [{"concepto": "Luz", "categoria": "servicios", "monto": 250730}]})
    enviar("luz 250730")
    cid = tg.msgs[-1][1][0][0]["callback_data"].split(":")[1]
    assert "Guardé 1 gasto" in tocar(f"ok:{cid}")
    # 5. resúmenes
    assert "Vendieron $" in enviar("/mes") and "Para no perder" in tg.ultimo
    assert "Resumen de Lazzy" in enviar("/resumen")
    # 6. preguntas (Haiku simulado)
    monkeypatch.setattr(P, "responder", lambda con, t, hoy=None: "Vendieron $450.000 con IVA.")
    assert enviar("¿cuánto vendimos este mes?") == "Vendieron $450.000 con IVA."
    # 7. F29 en PDF
    monkeypatch.setattr(IVA, "es_f29", lambda n, t: True)
    monkeypatch.setattr(IVA, "leer_f29", lambda con, d: {"periodo": "2026-10", "total": 500000, "ppm": 10000, "ventas_neto": 4000000})
    assert "Guardé el F29 de octubre 2026" in enviar(archivo=b"%PDF", nombre="F29.pdf") and "la dejé configurada" in tg.ultimo
    # 8. aviso de stock bajo el mínimo
    enviar("quedan 3 brownies")
    from datetime import date
    assert any("Bajo el stock mínimo: Brownies (3, mínimo 4)" in t for t in AL.revisar(con, date.today()))
    # 9. todo lo guardado aparece en el registro del bot
    estados = [r[0] for r in con.execute("SELECT estado FROM captura ORDER BY id")]
    assert "deshecho" in estados and estados.count("guardado") >= 5


def test_bot_vitrina_y_pedido(b):
    con, tg, enviar, tocar, resp = b
    enviar(archivo=_stock_xlsx([("Brownies", 10, 4), ("Cheesecake berries", 6, 4)]), nombre="stock.xlsx")
    t = enviar("saqué 3 brownies y 2 cheesecake berries")
    assert "Anoté lo que salió a vitrina" in t and "• 3 Brownies: en vitrina hasta el" in t
    assert "Brownies" in enviar("/vitrina") and "le quedan 4 días" in tg.ultimo
    assert "Anoté lo que hay en vitrina" in enviar("en vitrina quedan 1 brownies")
    assert "borré lo último" in enviar("/deshacer")
    assert con.execute("SELECT COUNT(*) FROM vitrina").fetchone()[0] == 2
    t = enviar("/pedido")
    assert "📋" in t and "vende 0,4/día" in t
    assert "No encontré un proveedor" in enviar("/pedido zzz")
    assert "📋" in enviar("¿Qué pedimos?")
