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


def analisis_turnos(con, semanas=8, hoy=None):
    """Venta (con IVA) de cada turno: promedio por día, por hora trabajada y por semana, con las últimas `semanas`.
    Las horas partidas (16:30) se reparten en proporción."""
    fin = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0]
    if not fin:
        return {"personas": [], "turnos": []}
    fin = date.fromisoformat(fin)
    ini = fin - timedelta(days=7 * semanas - 1)
    por = defaultdict(lambda: defaultdict(float))       # dia -> hora -> venta total en el período
    tick = defaultdict(lambda: defaultdict(float))
    dias = defaultdict(set)
    for r in con.execute("SELECT fecha, hora, bruto, tickets FROM venta_hora WHERE fecha BETWEEN ? AND ?", (ini.isoformat(), fin.isoformat())):
        d = date.fromisoformat(r["fecha"]).weekday()
        por[d][r["hora"]] += r["bruto"]
        tick[d][r["hora"]] += r["tickets"]
        if r["bruto"] > 0:
            dias[d].add(r["fecha"])
    filas = []
    for t in turnos(con):
        a, b = _horas(t["desde"]), _horas(t["hasta"])
        n = len(dias[t["dia"]]) or 1
        v = k = 0.0
        for h in range(int(a), int(b + 0.999)):
            frac = max(0.0, min(b, h + 1) - max(a, h))
            v += por[t["dia"]].get(h, 0) * frac
            k += tick[t["dia"]].get(h, 0) * frac
        filas.append(dict(t, dia_nombre=DIAS[t["dia"]], horas=b - a, venta_dia=round(v / n), tickets_dia=round(k / n, 1),
                          por_hora=round(v / n / (b - a)) if b > a else 0))
    personas = []
    for nombre in sorted({f["persona"] for f in filas}):
        fs = [f for f in filas if f["persona"] == nombre]
        h = sum(f["horas"] for f in fs)
        v = sum(f["venta_dia"] for f in fs)
        personas.append({"persona": nombre, "horas_semana": h, "venta_semana": v, "por_hora": round(v / h) if h else 0,
                         "en_resultado": all(f["en_resultado"] for f in fs), "dias": len(fs)})
    return {"personas": personas, "turnos": filas, "desde": ini, "hasta": fin}


def horas_por_turno(con, dia=0):
    """Venta promedio por hora de un día, con quién está en cada hora (para el gráfico)."""
    fin = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0]
    if not fin:
        return []
    ini = (date.fromisoformat(fin) - timedelta(days=55)).isoformat()
    filas = con.execute("SELECT hora, SUM(bruto) b, COUNT(DISTINCT fecha) n FROM venta_hora WHERE fecha >= ? AND "
                        "CAST(strftime('%w', fecha) AS INTEGER) = ? GROUP BY hora ORDER BY hora", (ini, (dia + 1) % 7)).fetchall()
    ts = [t for t in turnos(con) if t["dia"] == dia]
    out = []
    for r in filas:
        quien = [t["persona"] for t in ts if _horas(t["desde"]) < r["hora"] + 1 and _horas(t["hasta"]) > r["hora"]]
        out.append({"hora": r["hora"], "venta": round(r["b"] / max(r["n"], 1)), "quien": " y ".join(quien) or "nadie"})
    return out
