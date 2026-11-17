"""Bot de Telegram de Lazzy. Ejecutar: python -m lazzy.bot
Variables: TELEGRAM_TOKEN, ANTHROPIC_API_KEY, LAZZY_DB, LAZZY_ARCHIVOS (opcional), LAZZY_MODELO (opcional)."""
import json
import logging
import os
import secrets
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import openpyxl

from . import analisis as A
from . import captura as C
from . import finance as F
from . import insights as I
from . import alertas as AL
from . import iva as IVA
from . import preguntas as P
from . import seguimiento as S
from . import pedidos as PD
from . import stock as ST
from . import vitrina as VT
from .db import connect
from .glosario import CATEGORIAS_UI
from .importers.fudo_report import import_fudo_productos, import_fudo_report
from .importers.rcv import import_rcv_compras, periodo_from_filename

log = logging.getLogger("lazzy.bot")
ZONA = ZoneInfo("America/Santiago")
AYUDA = """Hola, soy el bot de Lazzy. Me puedes mandar:

• Facturas en PDF o foto: las leo, te muestro lo que entendí y las guardo cuando confirmes.
• Cuentas (luz, agua, etc.) en foto, o escritas: "luz 250730, agua 33209".
• Los Excel de FUDO (Ventas, Productos y Stock) y el CSV de compras del SII: los cargo al panel.
• El F29 en PDF: lo guardo y lo comparo con lo que estimó el panel.
• Stock:
  - Llegadas sin factura (sándwiches): "llegaron 10 ciabatta jamón queso y 6 salame".
  - Ajustes después de contar: "quedan 12 brownies" o "hay 5 tiramisú y 8 carrot cake".
  - Merma: "se botaron 2 tiramisú por vencidos" o "merma 1 cheesecake berries".
  - Con cada factura de un producto vinculado te digo cuánto debería quedar.
• Vitrina: "saqué 8 cheesecake berries, 4 brownies". Lo congelado no vence; desde que sale a vitrina cuento sus días
  y el último día te aviso lo que quedaría, con una idea para venderlo. Si quedan menos: "en vitrina quedan 2 cheesecake berries".
• Pedidos: /pedido arma lo que hay que pedir a cada proveedor según lo que se vende, listo para copiar. "/pedido cake" para uno.
• Preguntas: "¿cuánto vendimos la semana pasada?", "¿qué producto deja menos?", "¿cómo va el mes contra la meta?".

Además te aviso solo si un proveedor sube un precio, si un día vendió muy bajo lo normal, si no llega la factura de
un proveedor habitual, si el mes va bajo la meta o si algo queda bajo su stock mínimo.

Comandos:
/resumen: cómo va la semana
/mes: cómo va el mes y a cuánto va a cerrar
/stock: qué debería haber hoy, y lo que está bajo el mínimo
/vitrina: qué hay en vitrina y cuándo vence
/pedido: qué pedir a cada proveedor
/deshacer: borra lo último que guardé (una factura con sus costos, un gasto, un movimiento de stock o de vitrina)
Antes de guardar una factura puedes tocar «Corregir cantidad o precio» para arreglar un producto.
/ayuda: este mensaje

Todo queda en https://panel.lazzycoffee.cl"""


class Telegram:
    def __init__(self, token):
        self.url = f"https://api.telegram.org/bot{token}/"
        self.archivos = f"https://api.telegram.org/file/bot{token}/"
        self.http = httpx.Client(timeout=70)

    def __call__(self, metodo, **datos):
        r = self.http.post(self.url + metodo, json=datos)
        j = r.json()
        if not j.get("ok"):
            raise RuntimeError(f"Telegram {metodo}: {j.get('description')}")
        return j["result"]

    def enviar(self, chat_id, texto, botones=None):
        extra = {"reply_markup": {"inline_keyboard": botones}} if botones else {}
        return self("sendMessage", chat_id=chat_id, text=texto[:4000], disable_web_page_preview=True, **extra)

    def editar(self, chat_id, msg_id, texto, botones=None):
        extra = {"reply_markup": {"inline_keyboard": botones or []}}
        try:
            return self("editMessageText", chat_id=chat_id, message_id=msg_id, text=texto[:4000], **extra)
        except RuntimeError as e:          # "message is not modified"
            log.info("editar: %s", e)

    def bajar(self, file_id):
        f = self("getFile", file_id=file_id)
        return self.http.get(self.archivos + f["file_path"]).content


def botones_confirmar(cid, d):
    fila = [{"text": "✅ Guardar", "callback_data": f"ok:{cid}"}]
    if d.get("clase") == "documento":
        fila.append({"text": "Cambiar categoría", "callback_data": f"cat:{cid}"})
    fila.append({"text": "Descartar", "callback_data": f"no:{cid}"})
    if d.get("clase") == "documento" and d.get("lineas"):
        return [fila, [{"text": "✏️ Corregir cantidad o precio", "callback_data": f"ed:{cid}"}]]
    return [fila]


def botones_lineas(cid, d):
    filas = [[{"text": f"{x.get('cantidad') or 0:g} × {x['descripcion']}"[:40], "callback_data": f"ln:{cid}:{i}"}]
             for i, x in enumerate(d["lineas"][:12])]
    return filas + [[{"text": "‹ Volver", "callback_data": f"vol:{cid}"}]]


def botones_categoria(cid):
    filas, fila = [], []
    for _, items in CATEGORIAS_UI:
        for k, n in items:
            fila.append({"text": n[:30], "callback_data": f"set:{cid}:{k}"})
            if len(fila) == 2:
                filas.append(fila)
                fila = []
    if fila:
        filas.append(fila)
    return filas


class Bot:
    def __init__(self, con, tg, carpeta):
        self.con, self.tg, self.carpeta = con, tg, Path(carpeta)

    # ---------- autorización ----------
    def chat_autorizado(self, chat_id):
        return self.con.execute("SELECT usuario FROM telegram_chat WHERE chat_id=?", (chat_id,)).fetchone()

    def vincular(self, chat_id, nombre, codigo):
        r = self.con.execute("SELECT usuario, expira FROM telegram_codigo WHERE codigo=?", (codigo.strip().upper(),)).fetchone()
        if not r or r["expira"] < datetime.now().isoformat():
            return "Ese código no existe o ya venció. Genera uno nuevo en el panel, en «Bot de Telegram»."
        self.con.execute("INSERT OR REPLACE INTO telegram_chat VALUES (?,?,?,?)",
                         (chat_id, nombre, r["usuario"], datetime.now().isoformat(timespec="seconds")))
        self.con.execute("DELETE FROM telegram_codigo WHERE codigo=?", (codigo.strip().upper(),))
        self.con.commit()
        return f"Listo, quedaste vinculado como {r['usuario']}.\n\n{AYUDA}"

    # ---------- entrada ----------
    def procesar(self, upd):
        if "callback_query" in upd:
            return self.boton(upd["callback_query"])
        m = upd.get("message") or upd.get("edited_message")
        if not m:
            return
        chat = m["chat"]["id"]
        nombre = (m.get("from") or {}).get("first_name", "")
        texto = (m.get("text") or m.get("caption") or "").strip()
        if texto.startswith("/vincular") or texto.startswith("/start "):
            partes = texto.split(maxsplit=1)
            return self.tg.enviar(chat, self.vincular(chat, nombre, partes[1]) if len(partes) > 1
                                  else "Escribe /vincular seguido del código que aparece en el panel.")
        u = self.chat_autorizado(chat)
        if not u:
            return self.tg.enviar(chat, "Este bot es privado. Para usarlo, entra al panel, abre «Bot de Telegram» y mándame el código con /vincular.")
        usuario = u["usuario"]
        try:
            pend = self.pendiente_edicion(chat)
            if pend and (m.get("document") or m.get("photo") or texto.startswith("/")):
                self.pendiente_edicion(chat, borrar=True)
            elif pend and texto:
                return self.corregir(chat, texto, pend)
            if texto in ("/start", "/ayuda", "/help"):
                return self.tg.enviar(chat, AYUDA)
            if texto in ("/resumen", "/semana"):
                return self.tg.enviar(chat, resumen_semana(self.con))
            if texto == "/deshacer":
                return self.tg.enviar(chat, self.deshacer(chat))
            if texto == "/stock":
                return self.tg.enviar(chat, texto_stock(self.con))
            if texto in ("/mes", "/hoy"):
                return self.tg.enviar(chat, resumen_mes(self.con))
            if texto == "/vitrina":
                return self.tg.enviar(chat, texto_vitrina(self.con))
            low = texto.lower().strip("¿?¡! ")
            if low.startswith("/pedido") or low.startswith("pedido ") or low in ("pedido", "qué pedimos", "que pedimos", "qué pido", "que pido"):
                arg = texto.split(maxsplit=1)[1] if len(texto.split(maxsplit=1)) > 1 and low.startswith(("/pedido", "pedido")) else ""
                return self.tg.enviar(chat, texto_pedido(self.con, arg))
            if m.get("document"):
                return self.documento(chat, usuario, m["document"])
            if m.get("photo"):
                foto = max(m["photo"], key=lambda p: p.get("file_size", 0))
                datos = self.tg.bajar(foto["file_id"])
                ruta = self.archivar(datos, "foto.jpg")
                self.tg("sendChatAction", chat_id=chat, action="typing")
                return self.proponer(chat, usuario, C.leer_imagen(self.con, datos), ruta)
            if texto:
                self.tg("sendChatAction", chat_id=chat, action="typing")
                vt = VT.parsear(self.con, texto)
                if vt is not None:
                    return self.tg.enviar(chat, self.vitrina_msg(chat, vt, usuario))
                st = ST.parsear_stock(self.con, texto)
                if st is not None:
                    return self.tg.enviar(chat, self.stock_msg(chat, st, usuario))
                if P.es_pregunta(texto):
                    return self.tg.enviar(chat, P.responder(self.con, texto, hoy=datetime.now(ZONA).date()))
                d = C.leer_texto(self.con, texto, hoy=datetime.now(ZONA).date())
                if not d["gastos"]:      # no era un gasto: se responde como pregunta
                    return self.tg.enviar(chat, P.responder(self.con, texto, hoy=datetime.now(ZONA).date()))
                return self.proponer(chat, usuario, d, None)
        except C.ErrorLectura as e:
            return self.tg.enviar(chat, f"No pude hacerlo: {e}")

    def archivar(self, datos, nombre):
        hoy = datetime.now(ZONA)
        d = self.carpeta / hoy.strftime("%Y-%m")
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{hoy.strftime('%d-%H%M%S')}_{Path(nombre).name[-80:]}"
        p.write_bytes(datos)
        return str(p)

    def documento(self, chat, usuario, doc):
        nombre = doc.get("file_name") or "archivo"
        bajo = nombre.lower()
        if doc.get("file_size", 0) > 20 * 1024 * 1024:
            return self.tg.enviar(chat, "El archivo pesa más de 20 MB; Telegram no me deja bajarlo.")
        datos = self.tg.bajar(doc["file_id"])
        ruta = self.archivar(datos, nombre)
        if bajo.endswith(".pdf") or doc.get("mime_type") == "application/pdf":
            self.tg("sendChatAction", chat_id=chat, action="typing")
            if IVA.es_f29(nombre, C.texto_pdf(datos)):
                return self.tg.enviar(chat, self.f29(datos))
            return self.proponer(chat, usuario, C.leer_pdf(self.con, datos), ruta)
        if doc.get("mime_type", "").startswith("image/"):
            return self.proponer(chat, usuario, C.leer_imagen(self.con, datos, doc["mime_type"]), ruta)
        if bajo.endswith(".xlsx"):
            hojas = openpyxl.load_workbook(ruta, read_only=True).sheetnames
            if ST.es_stock(ruta):
                filas = ST.leer(ruta)
                difs = ST.vista_previa(self.con, F.load_cfg(self.con), filas)
                primero = not self.con.execute("SELECT 1 FROM stock_conteo LIMIT 1").fetchone()
                r = ST.guardar(self.con, filas)
                txt = f"Guardé el stock de {r['productos']} productos."
                if primero:
                    txt += " Es el primero: desde aquí sumo las facturas y resto las ventas."
                elif difs:
                    txt += "\nDiferencias con lo esperado:\n" + "\n".join(
                        f"• {p}: esperaba {e:g}, hay {c:g}" for p, e, c in difs[:10]) + "\nLo que falta queda como merma en https://panel.lazzycoffee.cl/stock"
                else:
                    txt += " Calza con lo esperado."
                return self.tg.enviar(chat, txt)
            if "Detalle" in hojas:
                r = import_fudo_productos(self.con, ruta)
                self.tg.enviar(chat, f"Cargué el reporte de productos de FUDO ({r['venta_producto']} filas).\n\n" + estado_datos(self.con))
                return S.detectar(self.con, avisar_desde=(date.today() - timedelta(days=30)).isoformat())
            if "Evolución de ventas" in hojas:
                r = import_fudo_report(self.con, ruta)
                return self.tg.enviar(chat, f"Cargué el reporte de ventas de FUDO ({r['venta_hora']} horas de venta).\n\n" + estado_datos(self.con))
            return self.tg.enviar(chat, "No reconozco ese Excel. Mándame el reporte de Ventas o el de Productos de FUDO.")
        if bajo.endswith(".csv"):
            if "RUT Proveedor" not in datos[:600].decode("utf-8", "ignore"):
                return self.tg.enviar(chat, "Ese CSV no es el registro de compras del SII.")
            try:
                per = periodo_from_filename(nombre)
            except ValueError:
                return self.tg.enviar(chat, "No pude saber de qué mes es el archivo. No le cambies el nombre que trae del SII.")
            r = import_rcv_compras(self.con, ruta, per)
            return self.tg.enviar(chat, f"Cargué {r['documentos']} documentos de compra del SII de {I.mes_largo(per)}.\n\n" + estado_datos(self.con))
        return self.tg.enviar(chat, "No sé qué hacer con ese tipo de archivo. Mándame PDF, foto, Excel de FUDO o CSV del SII.")

    def stock_msg(self, chat, st, usuario):
        """Llegada sin factura, ajuste de stock («quedan 12 brownies») o merma («se botaron 2 tiramisú por vencidos»)."""
        if not st["items"]:
            return ("No encontré " + (", ".join(f"«{x}»" for x in st["faltan"]) or "esos productos") + " en el stock. Escribe el nombre como "
                    "está en FUDO, por ejemplo «llegaron 10 ciabatta jamón queso», «quedan 12 brownies» o «merma 2 tiramisú».")
        hoy = datetime.now(ZONA).date()
        ref, lineas = {"tipo": "stock", "entradas": [], "conteos": []}, []
        cfg = F.load_cfg(self.con)
        for p, n in st["items"]:
            if st["tipo"] == "ajuste":
                previo = self.con.execute("SELECT * FROM stock_conteo WHERE fecha=? AND producto=?", (hoy.isoformat(), p)).fetchone()
                antes = ST.ajustar(self.con, p, n, hoy.isoformat(), corte_ahora_cl())
                ref["conteos"].append([hoy.isoformat(), p])
                if previo:      # el conteo del mismo día (p. ej. el Excel de FUDO) se reemplazó: /deshacer lo devuelve
                    ref.setdefault("previos", []).append(dict(previo))
                dif = n - antes if antes is not None else 0
                costo = (cfg["costo_producto"].get(p) or {}).get("costo")
                extra = "" if abs(dif) < 1 else (f" Esperaba {antes:g}: {'faltan' if dif < 0 else 'sobran'} {abs(dif):g}"
                                                 + (f" ({I.clp(round(abs(dif) * costo))} {'de merma' if dif < 0 else ''})".replace(" )", ")") if costo else "") + ".")
                lineas.append(f"• {p}: quedan {n:g}.{extra}")
            else:
                signo = 1 if st["tipo"] == "llegada" else -1
                origen = f"bot ({usuario})" if signo > 0 else f"merma bot ({usuario})"
                ref["entradas"].append(ST.registrar_llegada(self.con, p, signo * n, hoy.isoformat(), st["motivo"], origen))
        est = {x["producto"]: x for x in ST.estado(self.con, F.load_cfg(self.con), hoy)}
        if st["tipo"] != "ajuste":
            for p, n in st["items"]:
                e = est[p]
                lineas.append(f"• {'+' if st['tipo'] == 'llegada' else '-'}{n:g} {p}: ahora deberían quedar {e['esperado']:g}"
                              + (" ⚠️ bajo el mínimo" if e["bajo"] else ""))
        titulo = {"llegada": "Anoté la llegada", "ajuste": "Ajusté el stock", "merma": "Anoté la merma"}[st["tipo"]]
        if st["tipo"] == "merma" and st["motivo"]:
            titulo += f" ({st['motivo']})"
        txt = titulo + ":\n" + "\n".join(lineas)
        if st["faltan"]:
            txt += "\nNo reconocí: " + ", ".join(st["faltan"])
        self.con.execute("INSERT INTO captura(chat_id,usuario,creado,estado,tipo,datos,resultado) VALUES (?,?,?,?,?,?,?)",
                         (chat, usuario, datetime.now().isoformat(timespec="seconds"), "guardado", "stock",
                          json.dumps({"clase": "stock", "texto": txt}, ensure_ascii=False), json.dumps(ref)))
        self.con.commit()
        return txt + "\n\nSi algo quedó mal: /deshacer, o escribe «quedan N producto» para corregir."

    def vitrina_msg(self, chat, vt, usuario):
        """«saqué 8 cheesecake berries» (a vitrina) o «en vitrina quedan 2 cheesecake berries»."""
        if not vt["items"]:
            return ("No encontré " + ", ".join(f"«{x}»" for x in vt["faltan"]) + " en el stock. Escribe el nombre como está en FUDO, "
                    "por ejemplo «saqué 8 cheesecake berries, 4 brownies».")
        hoy = datetime.now(ZONA).date()
        ids = [VT.registrar(self.con, p, n, vt["tipo"], hoy.isoformat(), f"bot ({usuario})") for p, n in vt["items"]]
        aj = VT.ajustes(self.con)
        if vt["tipo"] == "saca":
            lineas = []
            for p, n in vt["items"]:
                fin = hoy + timedelta(days=VT.vida(self.con, p, aj) - 1)
                lineas.append(f"• {n:g} {p}: en vitrina hasta el {DIAS_C[fin.weekday()]} {fin.day}/{fin.month}")
            txt = "Anoté lo que salió a vitrina:\n" + "\n".join(lineas) + "\nEl último día te aviso lo que quedaría, a su ritmo de venta."
        else:
            txt = "Anoté lo que hay en vitrina:\n" + "\n".join(f"• {p}: {n:g}" for p, n in vt["items"])
        if vt["faltan"]:
            txt += "\nNo reconocí: " + ", ".join(vt["faltan"])
        self.con.execute("INSERT INTO captura(chat_id,usuario,creado,estado,tipo,datos,resultado) VALUES (?,?,?,?,?,?,?)",
                         (chat, usuario, datetime.now().isoformat(timespec="seconds"), "guardado", "stock",
                          json.dumps({"clase": "stock", "texto": txt}, ensure_ascii=False), json.dumps({"tipo": "vitrina", "ids": ids})))
        self.con.commit()
        return txt + "\n\nSi algo quedó mal: /deshacer."

    def f29(self, datos):
        d = IVA.leer_f29(self.con, datos)
        per = IVA.guardar_f29(self.con, d)
        cfg = F.load_cfg(self.con)
        est = F.iva_mes(self.con, per, cfg)
        dif = (d.get("total") or 0) - est["f29_estimado"]
        txt = (f"Guardé el F29 de {I.mes_largo(per)}: total a pagar {I.clp(d.get('total') or 0)}. "
               f"El panel había estimado {I.clp(est['f29_estimado'])} ({'+' if dif >= 0 else '-'}{I.clp(abs(dif))}).")
        v = d.get("ventas_neto") or F.ventas_mes(self.con, per, cfg["empresa"]["iva"])["neto"]
        if d.get("ppm") and v:
            tasa = 100 * d["ppm"] / v
            txt += f" La tasa de PPM que se desprende es {tasa:.2f}%".replace(".", ",")
            if not cfg["empresa"].get("ppm_tasa"):
                self.con.execute("INSERT OR REPLACE INTO parametro VALUES ('ppm_tasa', ?)", (str(round(tasa / 100, 5)),))
                self.con.commit()
                txt += "; la dejé configurada en el panel."
            else:
                txt += "."
        return txt + " El detalle está en la página IVA."

    def proponer(self, chat, usuario, d, ruta):
        d = C.preparar(self.con, d)
        texto = C.resumen_propuesta(d)
        if texto is None:
            return self.tg.enviar(chat, "No encontré nada que guardar.")
        if d.get("clase") == "documento" and d["tipo"] in ("factura", "factura_exenta", "nota_credito") and not C.es_factura(d):
            return self.tg.enviar(chat, texto)          # sin RUT o folio no se guarda: se duplicaría con el SII
        cid = self.con.execute("INSERT INTO captura(chat_id,usuario,creado,estado,tipo,datos,archivo) VALUES (?,?,?,?,?,?,?)",
                               (chat, usuario, datetime.now().isoformat(timespec="seconds"), "pendiente",
                                d.get("tipo", d["clase"]), json.dumps(d, ensure_ascii=False), ruta)).lastrowid
        self.con.commit()
        botones = botones_categoria(cid) if d.get("clase") == "documento" and not d.get("categoria") else botones_confirmar(cid, d)
        if d.get("clase") == "documento" and not d.get("categoria"):
            texto += "\n\nElige la categoría:"
        self.tg.enviar(chat, texto, botones)

    def boton(self, q):
        chat, msg = q["message"]["chat"]["id"], q["message"]["message_id"]
        self.tg("answerCallbackQuery", callback_query_id=q["id"])
        if not self.chat_autorizado(chat):
            return
        partes = q.get("data", "").split(":")
        cid = int(partes[1])
        fila = self.con.execute("SELECT * FROM captura WHERE id=? AND chat_id=?", (cid, chat)).fetchone()
        if not fila or fila["estado"] != "pendiente":
            return self.tg.editar(chat, msg, q["message"].get("text", "") + "\n\n(ya procesado)")
        d = json.loads(fila["datos"])
        accion = partes[0]
        if accion == "no":
            self.con.execute("UPDATE captura SET estado='descartado' WHERE id=?", (cid,))
            self.con.commit()
            return self.tg.editar(chat, msg, C.resumen_propuesta(d) + "\n\nDescartado.")
        if accion == "ed":
            return self.tg.editar(chat, msg, C.resumen_propuesta(d) + "\n\n¿Qué producto quieres corregir?", botones_lineas(cid, d))
        if accion == "vol":
            self.pendiente_edicion(chat, borrar=True)
            return self.tg.editar(chat, msg, C.resumen_propuesta(d), botones_confirmar(cid, d))
        if accion == "ln":
            i = int(partes[2])
            if i >= len(d.get("lineas", [])):
                return
            self.con.execute("INSERT OR REPLACE INTO parametro VALUES (?,?)", (f"edit_{chat}", f"{cid}:{i}"))
            self.con.commit()
            x = d["lineas"][i]
            return self.tg.editar(chat, msg, C.resumen_propuesta(d) + f"\n\n✏️ {x['descripcion']}: escríbeme lo correcto, por ejemplo "
                                  "«24 x 2900» (cantidad y precio sin IVA), «precio 2900», «precio 3451 con iva», «cantidad 24» o «quitar».",
                                  [[{"text": "‹ Volver", "callback_data": f"vol:{cid}"}]])
        if accion == "cat":
            return self.tg.editar(chat, msg, C.resumen_propuesta(d) + "\n\nElige la categoría:", botones_categoria(cid))
        if accion == "set":
            d["categoria"], d["proveedor_conocido"] = partes[2], False
            self.con.execute("UPDATE captura SET datos=? WHERE id=?", (json.dumps(d, ensure_ascii=False), cid))
            self.con.commit()
            return self.tg.editar(chat, msg, C.resumen_propuesta(d), botones_confirmar(cid, d))
        if accion == "ok":
            try:
                r = C.guardar(self.con, d, fila["usuario"])
            except C.ErrorLectura as e:
                return self.tg.editar(chat, msg, C.resumen_propuesta(d) + f"\n\nNo se guardó: {e}", botones_confirmar(cid, d))
            self.con.execute("UPDATE captura SET estado='guardado', resultado=? WHERE id=?", (json.dumps(r["ref"]), cid))
            self.con.commit()
            txt = C.resumen_propuesta(d) + f"\n\n✅ {r['texto']}"
            if C.es_factura(d):
                st = ST.de_factura(self.con, d["rut"], d["folio"], F.load_cfg(self.con), datetime.now(ZONA).date())
                if st:
                    txt += "\n\n📦 Stock:\n" + "\n".join(f"• +{q:g} {p}: ahora deberían quedar {e:g}" for p, q, e in st)
                    txt += "\nSi no calza, escribe por ejemplo «quedan 30 brownies»."
                elif d.get("lineas") and ST.productos(self.con):
                    txt += "\n\n📦 Estos productos no están vinculados al stock: vincúlalos una vez en el panel (página Stock o ficha del proveedor) y desde la próxima factura se suman solos."
            return self.tg.editar(chat, msg, txt)

    def pendiente_edicion(self, chat, borrar=False):
        r = self.con.execute("SELECT valor FROM parametro WHERE clave=?", (f"edit_{chat}",)).fetchone()
        if borrar and r:
            self.con.execute("DELETE FROM parametro WHERE clave=?", (f"edit_{chat}",))
            self.con.commit()
        return r["valor"] if r else None

    def corregir(self, chat, texto, pend):
        cid, i = (int(x) for x in pend.split(":"))
        fila = self.con.execute("SELECT * FROM captura WHERE id=? AND chat_id=? AND estado='pendiente'", (cid, chat)).fetchone()
        if not fila:
            self.pendiente_edicion(chat, borrar=True)
            return self.tg.enviar(chat, "Esa factura ya no está pendiente.")
        d = json.loads(fila["datos"])
        cambios = C.parsear_edicion(texto)
        if not cambios or i >= len(d["lineas"]):
            return self.tg.enviar(chat, "No entendí la corrección. Escribe por ejemplo «24 x 2900», «precio 2900», «cantidad 24» o «quitar».")
        hecho = C.editar_linea(d, i, cambios)
        d["cambios_precio"] = C.cambios_precio(self.con, d)
        self.con.execute("UPDATE captura SET datos=? WHERE id=?", (json.dumps(d, ensure_ascii=False), cid))
        self.con.commit()
        self.pendiente_edicion(chat, borrar=True)
        self.tg.enviar(chat, hecho + "\n\n" + C.resumen_propuesta(d), botones_confirmar(cid, d))

    def deshacer(self, chat):
        f = self.con.execute("SELECT * FROM captura WHERE chat_id=? AND estado='guardado' ORDER BY id DESC LIMIT 1", (chat,)).fetchone()
        if not f:
            return "No hay nada tuyo que deshacer."
        ref, datos = json.loads(f["resultado"]), json.loads(f["datos"])
        if ref.get("tipo") == "vitrina":
            for i in ref["ids"]:
                self.con.execute("DELETE FROM vitrina WHERE id=?", (i,))
        elif ref.get("tipo") == "stock":
            for i in ref["entradas"]:
                self.con.execute("DELETE FROM stock_entrada WHERE id=?", (i,))
            for fecha, p in ref["conteos"]:
                self.con.execute("DELETE FROM stock_conteo WHERE fecha=? AND producto=? AND creado>=?", (fecha, p, f["creado"]))
            for r in ref.get("previos", []):
                cols = list(r)
                self.con.execute(f"INSERT OR REPLACE INTO stock_conteo({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                 [r[c] for c in cols])
        else:
            C.deshacer(self.con, ref, f["creado"])
        self.con.execute("UPDATE captura SET estado='deshecho' WHERE id=?", (f["id"],))
        self.con.commit()
        resumen = datos.get("texto") if datos.get("clase") == "stock" else C.resumen_propuesta(datos)
        return "Listo, borré lo último que guardé: " + (resumen or "")[:300]


def corte_ahora_cl():
    return ST.corte_ahora(datetime.now(ZONA).replace(tzinfo=None))


def texto_stock(con):
    cfg = F.load_cfg(con)
    filas = ST.estado(con, cfg, datetime.now(ZONA).date())
    if not filas:
        return "Todavía no hay stock cargado. Mándame el Excel de stock de FUDO."
    ult = max(x["fecha"] for x in filas)
    bajos = [x for x in filas if x["bajo"]]
    t = [f"Stock esperado hoy (último conteo {ult[8:10]}/{ult[5:7]}, más llegadas, menos ventas cargadas):"]
    if bajos:
        t.append("\n⚠️ Bajo el mínimo:\n" + "\n".join(f"• {x['producto']}: {x['esperado']:g} (mínimo {x['minimo']:g})" for x in bajos))
    t.append("\n" + "\n".join(f"• {x['producto']}: {x['esperado']:g}" for x in filas if not x["bajo"]))
    total = sum(x["valor"] or 0 for x in filas)
    t.append(f"\nValor al costo: {I.clp(total)} sin IVA.")
    uv = con.execute("SELECT MAX(fecha) FROM venta_producto").fetchone()[0]
    if uv:
        t.append(f"Las ventas están descontadas hasta el {uv[8:10]}/{uv[5:7]}.")
    return "\n".join(t)


DIAS_C = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def texto_vitrina(con):
    hoy = datetime.now(ZONA).date()
    lotes = VT.estado(con, hoy)
    if not lotes:
        return "No hay nada anotado en vitrina. Cuando saquen algo del congelador: «saqué 8 cheesecake berries, 4 brownies»."
    t = ["En vitrina, según lo que sacaron y lo vendido:"]
    for x in sorted(lotes, key=lambda x: (x["dias"], x["producto"])):
        cuando = "hoy es su último día" if x["dias"] == 0 else "mañana es su último día" if x["dias"] == 1 else f"le quedan {x['dias'] + 1} días"
        t.append(f"• {'~' if x['estimado'] else ''}{x['quedan']:g} {x['producto']} (sacados el {x['fecha'][8:10]}/{x['fecha'][5:7]}): {cuando}")
    uv = con.execute("SELECT MAX(fecha) FROM venta_producto").fetchone()[0]
    if any(x["estimado"] for x in lotes) and uv:
        t.append(f"\n~ Las ventas desde el {uv[8:10]}/{uv[5:7]} todavía no se suben, así que las estimo con su ritmo normal. "
                 "Si ven otra cosa: «en vitrina quedan 2 cheesecake berries».")
    return "\n".join(t)


def texto_pedido(con, arg=""):
    gs = PD.sugerido(con, F.load_cfg(con), datetime.now(ZONA).date())
    if not gs:
        return "Para sugerir pedidos necesito el stock: mándame el Excel de stock de FUDO."
    if arg:
        gs = PD.buscar_grupo(gs, arg)
        if not gs:
            return f"No encontré un proveedor o producto parecido a «{arg}». Prueba con /pedido solo."
    else:
        gs = [g for g in gs if g["total"]] or gs[:1]
    t = "\n\n".join(PD.texto_bot(g) for g in gs)
    if any(not g["rut"] for g in gs):
        t += "\n\nPara separar los productos sin proveedor, elijan su proveedor en https://panel.lazzycoffee.cl/stock"
    return t + "\n\nEl objetivo y los días entre pedidos se ajustan en https://panel.lazzycoffee.cl/stock"


def resumen_mes(con):
    from . import metas as MT
    hoy = datetime.now(ZONA).date()
    per = hoy.strftime("%Y-%m")
    cfg = F.load_cfg(con)
    p = F.pnl_mes(con, per, cfg)
    v = p["ventas"]
    if not v["dias_abiertos"]:
        return "Todavía no hay ventas de este mes. Mándame los Excel de FUDO."
    uv = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0]
    t = [f"{I.mes_largo(per).capitalize()}, con ventas hasta el {uv[8:10]}/{uv[5:7]}:",
         f"Vendieron {I.clp(v['bruto'])} con IVA ({I.clp(v['neto'])} sin IVA) en {v['dias_abiertos']} días; "
         f"{I.clp(round(v['bruto'] / v['dias_abiertos']))} por día.",
         f"Costo de lo vendido: {p['pct']['costo_venta']:.0f}% de la venta."]
    r = MT.ritmo(con, cfg, per, p)
    if r:
        t.append(f"A este ritmo el mes cierra en {I.clp(r['proy_venta'])} sin IVA"
                 + (f" y una ganancia cercana a {I.clp(r['proy_resultado'])}." if r["proy_resultado"] is not None else "."))
    meta = MT.leer(con, per).get("venta")
    if meta and r:
        t.append(f"Meta de venta: {I.clp(meta)}: " + ("la cumples." if r["proy_venta"] >= meta else f"faltarían {I.clp(meta - r['proy_venta'])}."))
    eq = p["equilibrio"]["venta_bruta_dia"]
    if eq:
        t.append(f"Para no perder hay que vender {I.clp(eq)} con IVA por día en promedio.")
    return "\n".join(t) + "\n\nhttps://panel.lazzycoffee.cl/mes"


def estado_datos(con):
    f = I.frescura(con)
    return f"Ventas cargadas hasta el {f['ventas']}. Compras del SII hasta {f['compras']}."


def resumen_semana(con, hoy=None):
    hoy = hoy or datetime.now(ZONA).date()
    lunes = hoy - timedelta(days=hoy.weekday())
    a0, a1 = lunes - timedelta(days=7), lunes - timedelta(days=1)
    b0, b1 = lunes - timedelta(days=14), lunes - timedelta(days=8)
    tot = lambda x, y: con.execute("SELECT COALESCE(SUM(bruto),0) b, COALESCE(SUM(tickets),0) t, COUNT(DISTINCT fecha) d "
                                   "FROM venta_hora WHERE fecha BETWEEN ? AND ?", (x.isoformat(), y.isoformat())).fetchone()
    a, b = tot(a0, a1), tot(b0, b1)
    clp = I.clp
    lineas = [f"Resumen de Lazzy, semana del {a0.day}/{a0.month} al {a1.day}/{a1.month}"]
    ultimo = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0]
    if a["b"] and ultimo and ultimo >= a1.isoformat():
        var = f" ({'+' if a['b'] >= b['b'] else ''}{100 * (a['b'] - b['b']) / b['b']:.0f}% vs la semana anterior)" if b["b"] else ""
        lineas.append(f"Vendiste {clp(a['b'])} con IVA en {a['d']} días{var}. {a['t']} ventas, promedio {clp(round(a['b'] / a['t'])) if a['t'] else '—'}.")
    else:
        lineas.append("Todavía no están las ventas completas de la semana pasada: mándame los Excel de FUDO (Ventas y Productos, desde el día 1 del mes).")
    est = A.estimacion(con, desde=lunes)
    if est["dias"]:
        lineas.append(f"Para esta semana se estiman {clp(est['total'])}.")
    from . import metas as MT
    per = hoy.strftime("%Y-%m")
    meta = MT.leer(con, per).get("venta")
    rit = MT.ritmo(con, F.load_cfg(con), per)
    if meta and rit:
        lineas.append(f"El mes va camino a {clp(rit['proy_venta'])} sin IVA; la meta es {clp(meta)} "
                      f"({'la cumples' if rit['proy_venta'] >= meta else 'faltarían ' + clp(meta - rit['proy_venta'])}).")
    ap = IVA.apartar(con, F.load_cfg(con), hoy)
    lineas.append(f"IVA: aparta unos {clp(ap['por_semana'])} esta semana para el F29 de {I.mes_largo(ap['periodo'])} "
                  f"(≈ {clp(ap['cierre'])}, se paga hasta el {ap['pago'].day}/{ap['pago'].month}).")
    if hoy.day <= 7:
        mes_ant = (hoy.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        if not con.execute("SELECT 1 FROM compra WHERE periodo=? AND origen='sii' LIMIT 1", (mes_ant,)).fetchone():
            lineas.append(f"Recuerda descargar del SII el registro de compras de {I.mes_largo(mes_ant)} y mandármelo.")
    sem = con.execute("SELECT COUNT(*) n, COALESCE(SUM(total),0) m FROM compra WHERE origen='bot' AND fecha_doc BETWEEN ? AND ?",
                      (a0.isoformat(), a1.isoformat())).fetchone()
    if sem["n"]:
        lineas.append(f"Facturas registradas por el bot la semana pasada: {sem['n']} ({clp(sem['m'])}).")
    pend = I.pendientes(con, F.load_cfg(con), F.periodos(con), hoy)
    if pend:
        lineas.append("\nPor hacer en el panel:\n" + "\n".join(f"• {p['texto']}" for p in pend[:3]))
    lineas.append("\nhttps://panel.lazzycoffee.cl")
    return "\n".join(lineas)


def nuevo_codigo(con, usuario, minutos=30):
    codigo = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
    con.execute("DELETE FROM telegram_codigo WHERE usuario=? OR expira < ?", (usuario, datetime.now().isoformat()))
    con.execute("INSERT INTO telegram_codigo VALUES (?,?,?)",
                (codigo, usuario, (datetime.now() + timedelta(minutes=minutos)).isoformat()))
    con.commit()
    return codigo


def toca_resumen(con, ahora):
    """Lunes desde las 9:00, una vez por semana."""
    if ahora.weekday() != 0 or ahora.hour < 9:
        return False
    ult = (con.execute("SELECT valor FROM parametro WHERE clave='bot_ultimo_resumen'").fetchone() or [""])[0]
    return ult != ahora.date().isoformat()


def enviar_avisos(con, tg):
    """Manda los avisos pendientes (cambios de precio, alzas de proveedores) a todos los chats vinculados."""
    pend = con.execute("SELECT * FROM notificacion WHERE enviado IS NULL ORDER BY id LIMIT 10").fetchall()
    if not pend:
        return
    chats = [r["chat_id"] for r in con.execute("SELECT chat_id FROM telegram_chat")]
    for n in pend:
        for c in chats:
            tg.enviar(c, n["texto"])
        con.execute("UPDATE notificacion SET enviado=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), n["id"]))
        con.commit()


def main():
    from . import claves
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Las claves se cargan desde el panel. Sin ellas el bot espera; si cambian, se reinicia solo (systemd lo levanta).
    archivo = claves.ruta()
    while not claves.leer().get("TELEGRAM_TOKEN"):
        log.info("esperando claves en %s (se cargan en el panel, página Bot de Telegram)", archivo)
        time.sleep(15)
    os.environ.update({k: v for k, v in claves.leer().items() if k in ("TELEGRAM_TOKEN", "ANTHROPIC_API_KEY")})
    firma = archivo.stat().st_mtime
    token = os.environ["TELEGRAM_TOKEN"]
    db = os.environ.get("LAZZY_DB", "data/lazzy.db")
    con = connect(db)
    tg = Telegram(token)
    bot = Bot(con, tg, os.environ.get("LAZZY_ARCHIVOS", str(Path(db).parent / "archivos")))
    tg("setMyCommands", commands=[{"command": "resumen", "description": "Cómo va la semana"},
                                  {"command": "mes", "description": "Cómo va el mes"},
                                  {"command": "stock", "description": "Qué debería haber hoy"},
                                  {"command": "vitrina", "description": "Qué hay en vitrina y cuándo vence"},
                                  {"command": "pedido", "description": "Qué pedir a cada proveedor"},
                                  {"command": "deshacer", "description": "Borra lo último que guardé"},
                                  {"command": "ayuda", "description": "Qué puedo hacer"}])
    offset, espera = None, 1
    log.info("bot iniciado")
    while True:
        try:
            if archivo.stat().st_mtime != firma:
                log.info("cambiaron las claves; reiniciando")
                raise SystemExit(0)
            ahora = datetime.now(ZONA)
            if AL.toca(con, ahora):
                AL.revisar(con, ahora.date())
            enviar_avisos(con, tg)
            if toca_resumen(con, ahora):
                texto = resumen_semana(con)
                for r in con.execute("SELECT chat_id FROM telegram_chat").fetchall():
                    tg.enviar(r["chat_id"], texto)
                con.execute("INSERT OR REPLACE INTO parametro VALUES ('bot_ultimo_resumen', ?)", (ahora.date().isoformat(),))
                con.commit()
            ups = tg("getUpdates", timeout=50, offset=offset, allowed_updates=["message", "callback_query"])
            for u in ups:
                offset = u["update_id"] + 1
                try:
                    bot.procesar(u)
                except Exception:
                    log.exception("error procesando %s", u.get("update_id"))
                    chat = ((u.get("message") or {}).get("chat") or (u.get("callback_query") or {}).get("message", {}).get("chat") or {}).get("id")
                    if chat:
                        try:
                            tg.enviar(chat, "Algo falló al procesar eso. Inténtalo de nuevo; si sigue fallando, avísale a Guillermo.")
                        except Exception:
                            pass
            espera = 1
        except SystemExit:
            raise
        except Exception:
            log.exception("error de conexión; reintento en %ss", espera)
            time.sleep(espera)
            espera = min(espera * 2, 60)


if __name__ == "__main__":
    main()
