"""IVA: plata para apartar, fecha del F29, cuadre con el F29 presentado e IVA que se pierde en compras sin factura."""
from datetime import date, datetime, timedelta

from . import finance as F
from . import metas as MT

DIA_F29 = 20   # quienes emiten factura electrónica declaran y pagan el F29 por internet hasta el 20 del mes siguiente

HERR_F29 = {
    "name": "registrar_f29",
    "description": "Datos del Formulario 29 (declaración mensual de IVA y PPM) del SII.",
    "input_schema": {"type": "object", "properties": {
        "periodo": {"type": "string", "description": "Mes declarado, AAAA-MM"},
        "ventas_neto": {"type": "integer", "description": "Base imponible de ventas y servicios (código 563)"},
        "debito": {"type": "integer", "description": "Total débitos (código 538)"},
        "credito": {"type": "integer", "description": "Total créditos (código 537)"},
        "ppm": {"type": "integer", "description": "PPM neto determinado (código 62)"},
        "total": {"type": "integer", "description": "Total a pagar dentro del plazo (código 91)"}},
        "required": ["periodo", "total"]},
}


def es_f29(nombre, texto):
    t = (texto or "").upper()
    return "FORMULARIO 29" in t or "F29" in (nombre or "").upper() or ("DECLARACION MENSUAL" in t and "IVA" in t)


def fecha_pago(periodo):
    """El 20 del mes siguiente; si cae sábado o domingo, el lunes (los feriados no se consideran)."""
    y, m = int(periodo[:4]), int(periodo[5:7])
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    d = date(y, m, DIA_F29)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def apartar(con, cfg, hoy=None):
    """Para el mes en curso: F29 acumulado, estimado al cierre y cuánto apartar por semana."""
    hoy = hoy or date.today()
    per = hoy.strftime("%Y-%m")
    iv = F.iva_mes(con, per, cfg)
    p = F.pnl_mes(con, per, cfg)
    r = MT.ritmo(con, cfg, per, p)
    tasa = cfg["empresa"]["iva"]
    if r:
        factor = r["proy_venta"] / p["ventas"]["neto"] if p["ventas"]["neto"] else 1
        debito = round(iv["debito"] * factor)
        # el crédito del mes casi siempre se completa con el archivo del SII; se usa el promedio de los meses cerrados
        cerr = MT._cerrados(con, cfg, per)
        cred = round(sum(F.iva_mes(con, x["periodo"], cfg)["credito"] for x in cerr) / len(cerr)) if cerr else iv["credito"]
        credito = max(cred, iv["credito"])
        ppm = round(r["proy_venta"] * cfg["empresa"].get("ppm_tasa", 0))
        cierre = max(debito - credito, 0) + ppm
    else:
        cierre = iv["f29_estimado"]
        debito, credito, ppm = iv["debito"], iv["credito"], iv["ppm"]
    pago = fecha_pago(per)
    semanas = max(1, round(((pago - hoy).days) / 7))
    return {"periodo": per, "acumulado": iv["f29_estimado"], "debito_hoy": iv["debito"], "cierre": cierre, "debito": debito, "credito": credito, "ppm": ppm,
            "pago": pago, "dias": (pago - hoy).days, "por_semana": round(cierre / semanas / 1000) * 1000, "semanas": semanas,
            "tasa": tasa}


def guardar_f29(con, d):
    per = str(d.get("periodo") or "")[:7]
    if len(per) != 7 or per[4] != "-":
        raise ValueError("No encontré el mes del F29.")
    con.execute("INSERT OR REPLACE INTO f29 VALUES (?,?,?,?,?,?,?)",
                (per, d.get("debito"), d.get("credito"), d.get("ppm"), d.get("total"), d.get("ventas_neto"),
                 datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return per


def leer_f29(con, datos):
    from . import captura as C
    texto = C.texto_pdf(datos)
    if texto:
        contenido = [{"type": "text", "text": "Formulario 29:\n" + texto[:12000]}]
    else:
        import base64
        contenido = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                     "data": base64.b64encode(datos).decode()}}]
    return C.llamar_modelo(con, contenido, HERR_F29, "f29")


def cuadre(con, cfg):
    """F29 presentados contra lo que estimó el panel, y la tasa de PPM que se desprende de cada uno."""
    out = []
    for r in con.execute("SELECT * FROM f29 ORDER BY periodo DESC LIMIT 12"):
        est = F.iva_mes(con, r["periodo"], cfg)
        v = F.ventas_mes(con, r["periodo"], cfg["empresa"]["iva"])["neto"]
        base = r["ventas_neto"] or v
        out.append({"periodo": r["periodo"], "real": dict(r), "est": est,
                    "dif": (r["total"] or 0) - est["f29_estimado"],
                    "tasa_ppm": round(100 * r["ppm"] / base, 3) if r["ppm"] and base else None})
    return out


def perdido(con, anio=None):
    """Compras pagadas sin factura (boletas, cuentas a nombre de persona): su IVA no se pudo descontar."""
    anio = anio or date.today().year
    filas = [dict(r) for r in con.execute(
        "SELECT descripcion, categoria, SUM(neto) monto, COUNT(*) n FROM gasto_manual WHERE fecha >= ? AND fecha < ? "
        "AND COALESCE(iva_rec,0) = 0 AND categoria NOT IN ('remuneraciones','honorarios') GROUP BY descripcion, categoria "
        "ORDER BY monto DESC", (f"{anio}-01-01", f"{anio + 1}-01-01"))]
    total = sum(f["monto"] for f in filas)
    return {"filas": filas[:10], "total": total, "iva": round(total * 0.19 / 1.19), "anio": anio}

