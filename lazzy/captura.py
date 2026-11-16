"""Lectura de facturas, cuentas y mensajes con Claude (Haiku) y registro en la base.

Flujo: leer_* devuelve una propuesta (dict). El bot la muestra, la persona confirma, y guardar() la escribe.
"""
import base64
import json
import os
import re
import unicodedata
from datetime import date, datetime

import httpx

from .finance import CATEGORIAS
from .glosario import CAT_NOMBRE

MODELO = os.environ.get("LAZZY_MODELO", "claude-haiku-5-5")
API = "https://api.anthropic.com/v1/messages"
IVA = 0.19
CATS_TEXTO = "\n".join(f"- {k}: {v}" for k, v in CAT_NOMBRE.items())

SISTEMA = f"""Lees documentos y mensajes de Lazzy Coffee, una cafetería en Santiago de Chile.
Montos en pesos chilenos como enteros, sin puntos ni símbolos (ej: "$ 104.496" -> 104496).
RUT con guion y dígito verificador, sin puntos (ej: "77.323.879-0" -> "77323879-0").
Fechas en formato AAAA-MM-DD.
Categorías posibles (usa la clave):
{CATS_TEXTO}
En el detalle de productos, algunas facturas usan coma decimal: "2.352,941" es 2352.941 y "1,3" es 1.3.
Pistas: Transbank = comision_tarjetas; Enel/CGE (luz), Aguas Andinas (agua) = servicios; Entel/Movistar = telecom;
pasteles, tortas, galletas, brownies = pasteleria; vasos, tapas, bombillas = envases."""

HERR_DOC = {
    "name": "registrar_documento",
    "description": "Registra los datos de una factura, nota de crédito, boleta o cuenta de servicio.",
    "input_schema": {"type": "object", "properties": {
        "tipo": {"type": "string", "enum": ["factura", "factura_exenta", "nota_credito", "boleta", "cuenta_servicio", "otro"]},
        "rut_emisor": {"type": "string", "description": "RUT de quien emite (el proveedor), no el del cliente"},
        "razon_social": {"type": "string", "description": "Nombre del proveedor"},
        "rut_receptor": {"type": "string", "description": "RUT del cliente al que va dirigido el documento"},
        "folio": {"type": "integer", "description": "Número del documento"},
        "fecha": {"type": "string", "description": "Fecha de emisión AAAA-MM-DD"},
        "neto": {"type": "integer"}, "exento": {"type": "integer"}, "iva": {"type": "integer"},
        "total": {"type": "integer", "description": "Total a pagar"},
        "lineas": {"type": "array", "items": {"type": "object", "properties": {
            "descripcion": {"type": "string"},
            "cantidad": {"type": "number", "description": "Unidades reales. Si la columna dice 1 pero la descripción empieza "
                         "con el número de unidades (ej: '12 PIE DE LIMON'), usa ese número"},
            "precio_unitario": {"type": "number", "description": "Tal como aparece en el documento"},
            "total_linea": {"type": "number", "description": "Tal como aparece en el documento"}},
            "required": ["descripcion"]}},
        "categoria": {"type": "string", "enum": list(CATEGORIAS)},
        "concepto": {"type": "string", "description": "Descripción corta, ej: 'Luz octubre' o 'Pastelería'"}},
        "required": ["tipo", "fecha", "total", "categoria", "concepto"]},
}

HERR_TEXTO = {
    "name": "registrar_gastos",
    "description": "Gastos o pagos que la persona informa por escrito. Lista vacía si el mensaje no informa ningún gasto.",
    "input_schema": {"type": "object", "properties": {"gastos": {"type": "array", "items": {"type": "object", "properties": {
        "concepto": {"type": "string"}, "categoria": {"type": "string", "enum": list(CATEGORIAS)},
        "monto": {"type": "integer", "description": "Monto pagado en pesos"},
        "fecha": {"type": "string", "description": "AAAA-MM-DD si la menciona; si no, vacío"}},
        "required": ["concepto", "categoria", "monto"]}}}, "required": ["gastos"]},
}


class ErrorLectura(Exception):
    pass


def llamar_modelo(con, contenido, herramienta, tipo):
    """Llama a la API de Anthropic forzando la herramienta, y registra el uso de tokens."""
    clave = os.environ.get("ANTHROPIC_API_KEY")
    if not clave:                      # el panel lee la clave del mismo archivo que usa el bot
        from .claves import leer
        clave = leer().get("ANTHROPIC_API_KEY")
    if not clave:
        raise ErrorLectura("Falta configurar la clave de la API de Anthropic en el servidor.")
    r = httpx.post(API, timeout=120, headers={"x-api-key": clave, "anthropic-version": "2023-06-01"}, json={
        "model": MODELO, "max_tokens": 2500, "system": SISTEMA, "tools": [herramienta],
        "tool_choice": {"type": "tool", "name": herramienta["name"]},
        "messages": [{"role": "user", "content": contenido}]})
    if r.status_code >= 400:
        raise ErrorLectura(f"La API respondió {r.status_code}: {r.text[:200]}")
    j = r.json()
    u = j.get("usage", {})
    con.execute("INSERT INTO api_uso VALUES (?,?,?,?)", (datetime.now().isoformat(timespec="seconds"), tipo,
                                                        u.get("input_tokens", 0), u.get("output_tokens", 0)))
    con.commit()
    for b in j.get("content", []):
        if b.get("type") == "tool_use":
            return b["input"]
    raise ErrorLectura("No pude interpretar la respuesta.")


def texto_pdf(datos):
    import io
    try:
        import pypdf
        lector = pypdf.PdfReader(io.BytesIO(datos))
        return "\n".join((p.extract_text() or "") for p in lector.pages[:4]).strip()
    except Exception:
        return ""


def leer_pdf(con, datos):
    texto = texto_pdf(datos)
    if len(re.sub(r"\s", "", texto)) > 150:     # PDF electrónico: se manda solo el texto (más barato)
        contenido = [{"type": "text", "text": "Texto extraído de un PDF. Registra el documento:\n\n" + texto[:12000]}]
        tipo = "pdf_texto"
    else:                                       # PDF escaneado: se manda el documento completo
        contenido = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                     "data": base64.b64encode(datos).decode()}},
                     {"type": "text", "text": "Registra este documento."}]
        tipo = "pdf_imagen"
    return normalizar_doc(llamar_modelo(con, contenido, HERR_DOC, tipo))


def leer_imagen(con, datos, media="image/jpeg"):
    contenido = [{"type": "image", "source": {"type": "base64", "media_type": media, "data": base64.b64encode(datos).decode()}},
                 {"type": "text", "text": "Registra este documento (factura, boleta o cuenta)."}]
    return normalizar_doc(llamar_modelo(con, contenido, HERR_DOC, "imagen"))


def leer_texto(con, texto, hoy=None):
    r = llamar_modelo(con, [{"type": "text", "text": f"Hoy es {(hoy or date.today()).isoformat()}. Mensaje:\n{texto}"}],
                      HERR_TEXTO, "texto")
    gastos = []
    for g in r.get("gastos", []):
        if g.get("monto") and g.get("categoria") in CATEGORIAS:
            gastos.append({"concepto": g["concepto"].strip()[:80], "categoria": g["categoria"], "monto": int(g["monto"]),
                           "fecha": _fecha(g.get("fecha")) or (hoy or date.today()).isoformat()})
    return {"clase": "gastos", "gastos": gastos}


def _fecha(v):
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def rut_limpio(r):
    r = re.sub(r"[^0-9kK-]", "", str(r or "")).upper()
    if r and "-" not in r and len(r) > 1:
        r = r[:-1] + "-" + r[-1]
    return r


def normalizar_doc(d):
    """Valida y completa lo que leyó el modelo. Los precios de las líneas quedan siempre sin IVA."""
    tipo = d.get("tipo", "otro")
    out = {"clase": "documento", "tipo": tipo, "rut": rut_limpio(d.get("rut_emisor")),
           "razon_social": (d.get("razon_social") or "").strip()[:120], "rut_receptor": rut_limpio(d.get("rut_receptor")),
           "folio": int(d["folio"]) if str(d.get("folio") or "").isdigit() else None,
           "fecha": _fecha(d.get("fecha")) or date.today().isoformat(),
           "neto": int(d.get("neto") or 0), "exento": int(d.get("exento") or 0), "iva": int(d.get("iva") or 0),
           "total": int(d.get("total") or 0), "categoria": d.get("categoria") if d.get("categoria") in CATEGORIAS else None,
           "concepto": (d.get("concepto") or "").strip()[:80], "avisos": []}
    if out["tipo"] in ("factura", "nota_credito") and not out["neto"] and out["total"]:
        out["neto"] = round(out["total"] / (1 + IVA))
        out["iva"] = out["total"] - out["neto"]
    if out["neto"] and abs(out["neto"] + out["iva"] + out["exento"] - out["total"]) > 3:
        out["avisos"].append("Los montos no cuadran (neto + IVA ≠ total). Revisa antes de guardar.")
    lineas = [dict(x) for x in d.get("lineas") or [] if x.get("descripcion")]
    suma = sum(float(x.get("total_linea") or 0) for x in lineas)
    base = out["neto"] + out["exento"]
    if suma and base and abs(suma / 1000 - base) <= base * 0.03:   # «2.352,941» leído como 2352941: la coma era decimal
        for x in lineas:
            for k in ("precio_unitario", "total_linea"):
                if x.get(k):
                    x[k] = float(x[k]) / 1000
        suma /= 1000
    con_iva = bool(suma) and out["neto"] and abs(suma - out["total"]) < abs(suma - out["neto"] - out["exento"])
    out["precios_con_iva"] = bool(con_iva)
    f = 1 / (1 + IVA) if con_iva else 1
    out["lineas"] = []
    for x in lineas:
        ln = {"descripcion": x["descripcion"].strip()[:120], "cantidad": float(x.get("cantidad") or 0) or None,
              "precio_neto": round(float(x.get("precio_unitario") or 0) * f, 1) or None,
              "total_neto": round(float(x.get("total_linea") or 0) * f) or None}
        ln2, cambio = cantidad_en_descripcion(ln)
        if cambio:
            out["avisos"].append(f"Leí «{ln['descripcion']}» como {ln2['cantidad']} unidades de ${round(ln2['precio_neto']):,} sin IVA c/u.".replace(",", "."))
        out["lineas"].append(ln2)
    return out


UNIDADES = r"(?:k|kg|kgs|kilo|kilos|g|gr|grs|gramos|l|lt|lts|litro|litros|ml|cc|cm|mm|oz|%)\b"
_CANT = re.compile(r"^\s*(\d{1,3})\s*(?:x|un\.?|uds?\.?|unidades|porciones)?\s+(?!" + UNIDADES + r")(?=[^\W\d_])(.+)$", re.I)


def cantidad_en_descripcion(ln):
    """«12 PIE DE LIMON» con cantidad 1 y $25.210 son 12 unidades de $2.101. Devuelve la línea corregida y si cambió.
    No toca «2 LITROS LECHE» ni «500G SALAME» (el número es el formato, no la cantidad)."""
    m = _CANT.match(ln.get("descripcion") or "")
    if m and ln.get("cantidad") == int(m.group(1)) and int(m.group(1)) >= 2:   # ya venía bien: solo limpia el nombre
        return dict(ln, descripcion=m.group(2).strip()), False
    if not m or (ln.get("cantidad") or 1) != 1:
        return ln, False
    n = int(m.group(1))
    if not 2 <= n <= 200:
        return ln, False
    total = ln.get("total_neto") or ln.get("precio_neto")
    if not total:
        return ln, False
    return dict(ln, descripcion=m.group(2).strip(), cantidad=n, precio_neto=round(total / n, 1), total_neto=round(total)), True


def es_factura(d):
    return d.get("tipo") in ("factura", "factura_exenta", "nota_credito") and d.get("rut") and d.get("folio")


def _clave(t):
    t = unicodedata.normalize("NFKD", t.lower())
    return re.sub(r"[^a-z0-9 ]", "", "".join(c for c in t if not unicodedata.combining(c))).strip()


def preparar(con, d):
    """Agrega a la propuesta lo que ya se sabe en la base: categoría del proveedor, duplicados y cambios de precio."""
    rut_emp = (con.execute("SELECT valor FROM parametro WHERE clave='rut_empresa'").fetchone() or [""])[0]
    if d["clase"] == "gastos":
        return d
    if d["tipo"] in ("factura", "factura_exenta", "nota_credito") and not es_factura(d):
        d["avisos"].append("No pude leer el RUT del proveedor o el número de la factura. Mándala en PDF o con una foto más nítida.")
    if d.get("rut_receptor") and rut_emp and d["rut_receptor"] != rut_emp and es_factura(d):
        d["avisos"].append(f"Esta factura está a nombre del RUT {d['rut_receptor']}, no de Lazzy. El IVA no se puede recuperar.")
    if d.get("rut"):
        p = con.execute("SELECT categoria FROM proveedor WHERE rut=?", (d["rut"],)).fetchone()
        if p:
            d["categoria"], d["proveedor_conocido"] = p["categoria"], True
    if es_factura(d):
        td = tipo_doc(d)
        if con.execute("SELECT 1 FROM compra WHERE tipo_doc=? AND rut=? AND folio=?", (td, d["rut"], d["folio"])).fetchone():
            d["duplicada"] = True
        d["cambios_precio"] = cambios_precio(con, d)
    return d


def tipo_doc(d):
    return {"nota_credito": 61, "factura_exenta": 34}.get(d["tipo"], 33)


def cambios_precio(con, d, umbral=3.0):
    out = []
    for ln in d.get("lineas", []):
        if not ln.get("precio_neto"):
            continue
        prev = con.execute(
            "SELECT l.precio_neto, c.fecha_doc FROM factura_linea l JOIN compra c ON c.tipo_doc=l.tipo_doc AND c.rut=l.rut "
            "AND c.folio=l.folio WHERE l.rut=? AND l.folio<>? AND c.fecha_doc<=? AND l.precio_neto>0 AND l.descripcion=? "
            "ORDER BY c.fecha_doc DESC LIMIT 1", (d["rut"], d["folio"], d["fecha"], _clave(ln["descripcion"]))).fetchone()
        if prev:
            var = 100 * (ln["precio_neto"] - prev["precio_neto"]) / prev["precio_neto"]
            if abs(var) >= umbral:
                out.append({"descripcion": ln["descripcion"], "antes": prev["precio_neto"], "ahora": ln["precio_neto"],
                            "variacion": round(var, 1), "desde": prev["fecha_doc"]})
    return out


def guardar(con, d, usuario=None):
    """Escribe la propuesta confirmada. Devuelve una frase con lo que quedó registrado."""
    ahora = datetime.now().isoformat(timespec="microseconds")
    if d["clase"] == "gastos":
        n = 0
        for g in d["gastos"]:
            if con.execute("SELECT 1 FROM gasto_manual WHERE fecha=? AND categoria=? AND neto=? AND descripcion=?",
                           (g["fecha"], g["categoria"], g["monto"], g["concepto"])).fetchone():
                continue
            con.execute("INSERT INTO gasto_manual(fecha,descripcion,categoria,neto,iva_rec,origen,creado,usuario) "
                        "VALUES (?,?,?,?,0,'bot',?,?)", (g["fecha"], g["concepto"], g["categoria"], g["monto"], ahora, usuario))
            n += 1
        con.commit()
        return {"texto": f"Guardé {n} gasto{'s' if n != 1 else ''}." if n else "Esos gastos ya estaban registrados.",
                "ref": {"tipo": "gastos"}}
    if not d.get("categoria") and not (es_factura(d) and d.get("historico")):
        raise ErrorLectura("Falta elegir la categoría.")
    if es_factura(d):
        td = tipo_doc(d)
        if not d.get("proveedor_conocido") and d.get("categoria"):
            con.execute("INSERT INTO proveedor(rut,nombre,categoria) VALUES (?,?,?) ON CONFLICT(rut) DO UPDATE SET categoria=excluded.categoria",
                        (d["rut"], d["razon_social"], d["categoria"]))
        ya = con.execute("SELECT 1 FROM compra WHERE tipo_doc=? AND rut=? AND folio=?", (td, d["rut"], d["folio"])).fetchone()
        tenia = con.execute("SELECT 1 FROM factura_linea WHERE tipo_doc=? AND rut=? AND folio=?", (td, d["rut"], d["folio"])).fetchone()
        if not ya:
            con.execute("INSERT INTO compra(periodo,tipo_doc,rut,razon_social,folio,fecha_doc,exento,neto,iva_rec,iva_no_rec,"
                        "total,activo_fijo_neto,categoria,origen,creado) VALUES (?,?,?,?,?,?,?,?,?,0,?,0,NULL,'bot',?)",
                        (d["fecha"][:7], td, d["rut"], d["razon_social"], d["folio"], d["fecha"], d["exento"], d["neto"],
                         d["iva"], d["total"], ahora))
        else:
            con.execute("UPDATE compra SET creado=? WHERE tipo_doc=? AND rut=? AND folio=? AND creado IS NULL", (ahora, td, d["rut"], d["folio"]))
        con.execute("DELETE FROM factura_linea WHERE tipo_doc=? AND rut=? AND folio=?", (td, d["rut"], d["folio"]))
        con.executemany("INSERT INTO factura_linea VALUES (?,?,?,?,?,?,?,?,?)",
                        [(td, d["rut"], d["folio"], i, _clave(x["descripcion"]), x["cantidad"], x["precio_neto"], x["total_neto"],
                          x["descripcion"]) for i, x in enumerate(d.get("lineas", []))])
        con.commit()
        from .proveedores import nombre as nombre_prov, recalcular_factura
        cambios = recalcular_factura(con, d["rut"])
        prov = nombre_prov(con, d["rut"], d["razon_social"])
        cat = CAT_NOMBRE.get(d.get("categoria"), "sin categoría")
        txt = (f"Factura {d['folio']} de {prov} ya estaba en el panel; guardé su detalle." if ya else
               f"Guardé la factura {d['folio']} de {prov} por ${d['total']:,} en {cat}.".replace(",", "."))
        for c in cambios:
            txt += (f"\nActualicé el costo de {c['etiqueta']}: de ${c['antes']:,} a ${c['ahora']:,} sin IVA "
                    f"({c['variacion']:+.0f}%). Queda en Seguimiento.").replace(",", ".")
        return {"texto": txt, "ref": {"tipo": "compra", "tipo_doc": td, "rut": d["rut"], "folio": d["folio"], "nueva": not ya,
                                   "detalle_nuevo": not tenia, "fecha": d["fecha"]}}
    # boleta, cuenta de servicio u otro sin factura: gasto sin crédito de IVA
    monto = d["total"] or d["neto"]
    if not monto:
        raise ErrorLectura("No encontré el monto a pagar.")
    concepto = d["concepto"] or d["razon_social"] or CAT_NOMBRE[d["categoria"]]
    cur = con.execute("INSERT INTO gasto_manual(fecha,descripcion,categoria,neto,iva_rec,origen,creado,usuario) "
                      "VALUES (?,?,?,?,0,'bot',?,?)", (d["fecha"], concepto, d["categoria"], monto, ahora, usuario))
    con.commit()
    return {"texto": f"Guardé {concepto} por ${monto:,} en {CAT_NOMBRE[d['categoria']]}.".replace(",", "."),
            "ref": {"tipo": "gasto", "id": cur.lastrowid}}


_NUM = r"\$?\s*(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)"


def _num(t):
    return float(t.replace(".", "").replace(",", "."))


def parsear_edicion(texto):
    """«24 x 2900», «24 a $2.900», «cantidad 24», «precio 2900 con iva», «quitar». Devuelve los cambios o None."""
    t = texto.lower().strip()
    if re.fullmatch(r"(quitar|borrar|eliminar|sacar)( l[ií]nea| producto)?\.?", t):
        return {"quitar": True}
    out = {}
    m = re.search(_NUM + r"\s*(?:x|por|a|unidades a|un a|uds a)\s*" + _NUM, t)
    if m:
        out = {"cantidad": _num(m.group(1)), "precio": _num(m.group(2))}
    else:
        m = re.search(r"\b(?:cantidad|cant|unidades|son)\b\D{0,6}?" + _NUM, t)
        if m:
            out["cantidad"] = _num(m.group(1))
        m = re.search(r"(?:\bprecio|\bvalor|c/u|\bcada un[oa])\D{0,8}?" + _NUM, t)
        if m:
            out["precio"] = _num(m.group(1))
    if not out:
        return None
    if "precio" in out and re.search(r"con iva|iva incluido|bruto", t):
        out["precio"] = round(out["precio"] / (1 + IVA), 1)
    return out


def editar_linea(d, i, cambios):
    """Aplica una corrección a la línea i de la propuesta. Los totales de la factura no cambian (son los del SII)."""
    ln = d["lineas"][i]
    if cambios.get("quitar"):
        d["lineas"].pop(i)
        return f"Quité «{ln['descripcion']}» del detalle."
    if "cantidad" in cambios:
        ln["cantidad"] = cambios["cantidad"]
    if "precio" in cambios:
        ln["precio_neto"] = cambios["precio"]
    if ln.get("cantidad") and ln.get("precio_neto"):
        ln["total_neto"] = round(ln["cantidad"] * ln["precio_neto"])
    d["editado"] = True
    return (f"Corregido: {ln.get('cantidad') or 0:g} × {ln['descripcion']} a ${round(ln.get('precio_neto') or 0):,} c/u sin IVA.").replace(",", ".")


def deshacer(con, ref, creado=None):
    """Borra lo que guardó el bot. Si la factura ya estaba por el SII, queda la factura y se va solo el detalle que
    agregó el bot. También devuelve los costos que esa factura había actualizado."""
    if ref["tipo"] == "compra":
        if ref.get("nueva") or ref.get("detalle_nuevo"):
            con.execute("DELETE FROM factura_linea WHERE tipo_doc=? AND rut=? AND folio=?", (ref["tipo_doc"], ref["rut"], ref["folio"]))
        if ref.get("nueva"):
            con.execute("DELETE FROM compra WHERE tipo_doc=? AND rut=? AND folio=? AND origen='bot'", (ref["tipo_doc"], ref["rut"], ref["folio"]))
        if ref.get("fecha") and (ref.get("nueva") or ref.get("detalle_nuevo")):
            from .proveedores import revertir
            revertir(con, ref["rut"], ref["folio"], ref["fecha"])
    elif ref["tipo"] == "gasto":
        con.execute("DELETE FROM gasto_manual WHERE id=? AND origen='bot'", (ref["id"],))
    elif ref["tipo"] == "gastos" and creado:
        con.execute("DELETE FROM gasto_manual WHERE origen='bot' AND creado>=?", (creado,))
    con.commit()


def resumen_propuesta(d):
    """Texto que el bot muestra antes de confirmar."""
    clp = lambda x: f"${x:,}".replace(",", ".")
    if d["clase"] == "gastos":
        if not d["gastos"]:
            return None
        filas = "\n".join(f"• {g['concepto']}: {clp(g['monto'])} ({CAT_NOMBRE[g['categoria']]}, {g['fecha']})" for g in d["gastos"])
        return f"Entendí estos gastos:\n{filas}\n\n¿Los guardo?"
    nombre = {"factura": "Factura", "factura_exenta": "Factura exenta", "nota_credito": "Nota de crédito",
              "boleta": "Boleta", "cuenta_servicio": "Cuenta", "otro": "Documento"}[d["tipo"]]
    t = [f"{nombre}{' ' + str(d['folio']) if d.get('folio') else ''} de {d['razon_social'] or 'proveedor sin nombre'}",
         f"Fecha: {d['fecha']}"]
    if d["neto"]:
        t.append(f"Neto {clp(d['neto'])} + IVA {clp(d['iva'])}" + (f" + exento {clp(d['exento'])}" if d["exento"] else ""))
    t.append(f"Total: {clp(d['total'])}")
    t.append(f"Categoría: {CAT_NOMBRE.get(d['categoria'], 'sin categoría')}" + (" (proveedor conocido)" if d.get("proveedor_conocido") else ""))
    if d.get("lineas"):
        t.append("")
        for x in d["lineas"][:12]:
            q = f"{x['cantidad']:g} × " if x.get("cantidad") else ""
            pr = f" a {clp(round(x['precio_neto']))} c/u sin IVA" if x.get("precio_neto") else ""
            t.append(f"• {q}{x['descripcion']}{pr}")
    for c in d.get("cambios_precio") or []:
        flecha = "subió" if c["variacion"] > 0 else "bajó"
        t.append(f"\n⚠️ {c['descripcion']} {flecha} {abs(c['variacion']):.0f}%: de {clp(round(c['antes']))} a {clp(round(c['ahora']))} sin IVA (antes: {c['desde']}).")
    if d.get("duplicada"):
        t.append("\nEsta factura ya está en el panel (llegó por el SII o se mandó antes). Si confirmas, solo se guarda el detalle.")
    if d.get("editado") and d.get("neto"):
        suma = sum(x.get("total_neto") or 0 for x in d.get("lineas", []))
        if abs(suma - d["neto"] - d.get("exento", 0)) > max(50, d["neto"] * 0.01):
            t.append(f"\nEl detalle corregido suma {clp(round(suma))} sin IVA y la factura dice {clp(d['neto'])}. El total de la factura "
                     "no cambia; los costos de los productos usan el detalle corregido.")
    for a in d.get("avisos", []):
        t.append(f"\n❗ {a}")
    return "\n".join(t)
