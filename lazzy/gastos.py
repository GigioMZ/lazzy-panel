"""Gastos fijos: cuentas de luz y agua esperadas contra reales, y turnos del personal con la venta de cada turno."""
import re
from collections import defaultdict
from datetime import date, timedelta

from .db import mes_rango

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
CLAVES = {"luz": r"luz|enel|cge|electric", "agua": r"agua|sanitari|aguas andinas|smapa", "gas": r"\bgas\b|metrogas|abastible|lipigas"}
ESPERADO = {"luz": 240000, "agua": 30000}   # montos de la cuenta, con IVA (se cambian en Gastos fijos)


def tipo_cuenta(texto):
    t = (texto or "").lower()
    return next((k for k, rx in CLAVES.items() if re.search(rx, t)), None)


def esperado(con):
    par = {r["clave"]: r["valor"] for r in con.execute("SELECT * FROM parametro WHERE clave LIKE 'esperado_%'")}
    return {k: int(float(par.get(f"esperado_{k}", v))) for k, v in ESPERADO.items()}


def servicios_mes(con, meses=6, hoy=None):
    """Por mes: cuenta real de luz y de agua (bot o factura del SII) y lo esperado."""
    hoy = hoy or date.today()
    esp = esperado(con)
    out = []
    y, m = hoy.year, hoy.month
    for _ in range(meses):
        per = f"{y}-{m:02d}"
        real = defaultdict(int)
        a, b = mes_rango(per)
        for r in con.execute("SELECT descripcion, neto, iva_rec FROM gasto_manual WHERE categoria='servicios' AND fecha >= ? AND fecha < ?", (a, b)):
            k = tipo_cuenta(r["descripcion"])
            if k:
                real[k] += (r["neto"] or 0) + (r["iva_rec"] or 0)
        for r in con.execute("SELECT c.razon_social, c.total, c.tipo_doc FROM compra c LEFT JOIN proveedor p ON p.rut=c.rut "
                             "WHERE COALESCE(c.categoria, p.categoria)='servicios' AND c.fecha_doc >= ? AND c.fecha_doc < ?", (a, b)):
            k = tipo_cuenta(r["razon_social"])
            if k:
                real[k] += r["total"] * (-1 if r["tipo_doc"] == 61 else 1)
        out.append({"periodo": per, **{k: {"real": real.get(k) or None, "esperado": esp[k]} for k in esp}})
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def _horas(t):
    h, m = t.split(":")
    return int(h) + int(m) / 60


def turnos(con):
    return [dict(r) for r in con.execute("SELECT * FROM turno ORDER BY persona, dia, desde")]


def analisis_turnos(con, desde=None, hasta=None, semanas=8):
    """Venta de cada turno en un rango de fechas (por defecto, las últimas `semanas` con ventas cargadas).
    La venta de cada hora se asigna a quien está de turno; una hora partida (16:30) se reparte en proporción."""
    fin = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0]
    if not fin:
        return {"personas": [], "turnos": []}
    hasta = min(hasta or date.fromisoformat(fin), date.fromisoformat(fin))
    desde = desde or hasta - timedelta(days=7 * semanas - 1)
    ts = turnos(con)
    por_turno = {t["id"]: {"venta": 0.0, "tickets": 0.0, "dias": set()} for t in ts}
    for r in con.execute("SELECT fecha, hora, bruto, tickets FROM venta_hora WHERE fecha BETWEEN ? AND ?",
                         (desde.isoformat(), hasta.isoformat())):
        d = date.fromisoformat(r["fecha"]).weekday()
        for t in ts:
            if t["dia"] != d:
                continue
            a, b = _horas(t["desde"]), _horas(t["hasta"])
            frac = max(0.0, min(b, r["hora"] + 1) - max(a, r["hora"]))
            if frac:
                x = por_turno[t["id"]]
                x["venta"] += r["bruto"] * frac
                x["tickets"] += r["tickets"] * frac
                if r["bruto"] > 0:
                    x["dias"].add(r["fecha"])
    filas = []
    for t in ts:
        x = por_turno[t["id"]]
        n, h = len(x["dias"]), _horas(t["hasta"]) - _horas(t["desde"])
        filas.append(dict(t, dia_nombre=DIAS[t["dia"]], horas=h, dias_trabajados=n, venta=round(x["venta"]),
                          venta_dia=round(x["venta"] / n) if n else 0, tickets_dia=round(x["tickets"] / n, 1) if n else 0,
                          por_hora=round(x["venta"] / n / h) if n and h else 0))
    personas = []
    for nombre in sorted({f["persona"] for f in filas}):
        fs = [f for f in filas if f["persona"] == nombre]
        horas = sum(f["horas"] * f["dias_trabajados"] for f in fs)
        venta = sum(f["venta"] for f in fs)
        personas.append({"persona": nombre, "horas": horas, "venta": venta, "dias": sum(f["dias_trabajados"] for f in fs),
                         "horas_semana": sum(f["horas"] for f in fs), "por_hora": round(venta / horas) if horas else 0,
                         "en_resultado": all(f["en_resultado"] for f in fs)})
    return {"personas": personas, "turnos": filas, "desde": desde, "hasta": hasta}


def costos_personas(con, cfg, tur, iva=0.19):
    """Para cada persona: cuánto costó en el rango (pago mensual prorrateado por días) y cuánto margen dejó lo vendido
    en su turno (venta sin IVA menos el costo de lo vendido, al % del rango). Si una sola persona está en el estado de
    resultados y no tiene pago anotado, su costo es el de sueldos e imposiciones."""
    from . import finance as F
    if not tur["personas"]:
        return
    pagos = {r["clave"][5:]: int(float(r["valor"])) for r in con.execute("SELECT * FROM parametro WHERE clave LIKE 'pago_%'")}
    en_res = [x for x in tur["personas"] if x["en_resultado"]]
    sueldos = sum(c["monto"] for c in cfg["costo_fijo"] if c["categoria"] == "remuneraciones"
                  and c["desde"] <= tur["hasta"].strftime("%Y-%m") <= c["hasta"])
    c = F.costo_vendido(con, tur["desde"].isoformat(), tur["hasta"].isoformat(), cfg)
    pct_costo = c["total"] / c["venta_productos"] if c["venta_productos"] else 0.4
    dias = (tur["hasta"] - tur["desde"]).days + 1
    for x in tur["personas"]:
        x["pago"] = pagos.get(x["persona"])
        x["pago_origen"] = "anotado"
        if not x["pago"] and x["en_resultado"] and len(en_res) == 1 and sueldos:
            x["pago"], x["pago_origen"] = sueldos, "sueldos del estado de resultados"
        x["neto"] = round(x["venta"] / (1 + iva))
        x["margen"] = round(x["neto"] * (1 - pct_costo))
        if x["pago"]:
            x["costo"] = round(x["pago"] * dias / 30.44)
            x["costo_hora"] = round(x["costo"] / x["horas"]) if x["horas"] else None
            x["deja"] = x["margen"] - x["costo"]
            x["veces"] = round(x["margen"] / x["costo"], 1) if x["costo"] else None
    tur["pct_costo"] = round(100 * pct_costo, 1)
    tur["dias_rango"] = dias


def horas_por_turno(con, dia=0, desde=None, hasta=None):
    """Venta promedio por hora de un día de la semana en el rango, con quién está en cada hora (para el gráfico)."""
    fin = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0]
    if not fin:
        return []
    hasta = (hasta or date.fromisoformat(fin)).isoformat()
    ini = (desde or date.fromisoformat(fin) - timedelta(days=55)).isoformat()
    filas = con.execute("SELECT hora, SUM(bruto) b, COUNT(DISTINCT fecha) n FROM venta_hora WHERE fecha BETWEEN ? AND ? AND "
                        "CAST(strftime('%w', fecha) AS INTEGER) = ? GROUP BY hora ORDER BY hora", (ini, hasta, (dia + 1) % 7)).fetchall()
    ts = [t for t in turnos(con) if t["dia"] == dia]
    out = []
    for r in filas:
        quien = [t["persona"] for t in ts if _horas(t["desde"]) < r["hora"] + 1 and _horas(t["hasta"]) > r["hora"]]
        out.append({"hora": r["hora"], "venta": round(r["b"] / max(r["n"], 1)), "quien": " y ".join(quien) or "nadie"})
    return out
