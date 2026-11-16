"""Tablero por período (semana, mes, rango libre): ventas, costo de lo vendido, margen y ganancia estimada.

Para períodos cortos las facturas no sirven (se compra una vez para varias semanas), así que:
- el costo de lo vendido sale de las recetas y costos de cada producto (como Shopify);
- sueldos y gastos del local se prorratean por día desde el mes;
- la comisión de tarjetas se calcula con la tasa sobre lo cobrado con tarjeta.
El cierre real de cada mes, con facturas, está en «Mes a mes»."""
import calendar
from datetime import date, timedelta

from . import finance as F
from .db import mes_rango

DIAS_CORTO = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES = "ene feb mar abr may jun jul ago sep oct nov dic".split()


def presets(ultimo):
    """Períodos rápidos, relativos al último día con ventas cargadas."""
    hoy = ultimo
    lunes = hoy - timedelta(days=hoy.weekday())
    ini_mes = hoy.replace(day=1)
    fin_mes_ant = ini_mes - timedelta(days=1)
    return {
        "semana": ("Esta semana", lunes, hoy),
        "semana_pasada": ("Semana pasada", lunes - timedelta(days=7), lunes - timedelta(days=1)),
        "mes": ("Este mes", ini_mes, hoy),
        "mes_pasado": ("Mes pasado", fin_mes_ant.replace(day=1), fin_mes_ant),
        "30": ("Últimos 30 días", hoy - timedelta(days=29), hoy),
        "90": ("Últimos 90 días", hoy - timedelta(days=89), hoy),
        "anio": ("Este año", hoy.replace(month=1, day=1), hoy),
    }


def anterior(d0, d1, modo="periodo"):
    """Período con que se compara: los n días anteriores, o el mismo período hace un año
    (52 semanas antes para rangos cortos, así calzan los días de la semana; mismas fechas para rangos largos)."""
    n = (d1 - d0).days + 1
    if modo != "anio":
        return d0 - timedelta(days=n), d0 - timedelta(days=1)
    if n <= 45:
        return d0 - timedelta(days=364), d1 - timedelta(days=364)

    def atras(d):
        try:
            return d.replace(year=d.year - 1)
        except ValueError:          # 29 de febrero
            return d.replace(year=d.year - 1, day=28)
    return atras(d0), atras(d1)


def _meses(d0, d1):
    p, out = d0.replace(day=1), []
    while p <= d1:
        out.append(p.strftime("%Y-%m"))
        p = (p + timedelta(days=32)).replace(day=1)
    return out


def _fraccion(periodo, d0, d1):
    a, b = (date.fromisoformat(x) for x in mes_rango(periodo))
    dias_mes = (b - a).days
    dentro = (min(d1, b - timedelta(days=1)) - max(d0, a)).days + 1
    return max(0, dentro) / dias_mes


def _gastos_mes(con, cfg, periodo, cache, hoy):
    """Sueldos y gastos del local de un mes, sin comisión de tarjetas. Si el mes aún no cierra, los gastos
    del local se toman del promedio de los 3 últimos meses completos (sus facturas todavía no llegan)."""
    if periodo in cache:
        return cache[periodo]
    p = F.pnl_mes(con, periodo, cfg)
    com = p["lineas"].get("gasto_operacional", {}).get("comision_tarjetas", 0)
    pers, gop = p["personal"], p["gasto_operacional"] - com
    if not (p["tiene_compras"] and periodo < hoy):
        previos = [x for x in F.periodos(con) if x < periodo][-6:]
        comp = [q for q in (F.pnl_mes(con, x, cfg) for x in previos) if q["tiene_compras"]][-3:]
        if comp:
            gop = max(gop, round(sum(q["gasto_operacional"] - q["lineas"].get("gasto_operacional", {}).get("comision_tarjetas", 0)
                                     for q in comp) / len(comp)))
    cache[periodo] = (pers, gop)
    return cache[periodo]


def resultado(con, cfg, d0, d1, cache=None, hoy=None):
    cache = {} if cache is None else cache
    hoy = hoy or date.today().strftime("%Y-%m")
    iva = cfg["empresa"]["iva"]
    a, b = d0.isoformat(), d1.isoformat()
    v = con.execute("SELECT COALESCE(SUM(bruto),0) b, COALESCE(SUM(tickets),0) t, COUNT(DISTINCT fecha) d "
                    "FROM venta_hora WHERE fecha BETWEEN ? AND ?", (a, b)).fetchone()
    neto = round(v["b"] / (1 + iva))
    # costo de lo vendido con recetas/costos; lo que no tiene costo se estima con el % de su categoría
    costos = cfg["costo_producto"]
    filas = con.execute("SELECT categoria, producto, SUM(cantidad) q, SUM(bruto) b FROM venta_producto "
                        "WHERE fecha BETWEEN ? AND ? GROUP BY categoria, producto", (a, b)).fetchall()
    cat_v, cat_c = {}, {}
    for r in filas:
        if r["producto"] in costos and costos[r["producto"]]["costo"] is not None:
            cat_v[r["categoria"]] = cat_v.get(r["categoria"], 0) + r["b"] / (1 + iva)
            cat_c[r["categoria"]] = cat_c.get(r["categoria"], 0) + r["q"] * costos[r["producto"]]["costo"]
    tasa_total = sum(cat_c.values()) / sum(cat_v.values()) if cat_v else 0.35
    cv, cv_est, vp = 0.0, 0.0, 0.0
    for r in filas:
        nb = r["b"] / (1 + iva)
        vp += nb
        if r["producto"] in costos and costos[r["producto"]]["costo"] is not None:
            cv += r["q"] * costos[r["producto"]]["costo"]
        else:
            t = cat_c[r["categoria"]] / cat_v[r["categoria"]] if cat_v.get(r["categoria"]) else tasa_total
            cv_est += nb * t
    if vp and neto and abs(vp - neto) / neto > 0.02:      # productos no cargados para todo el rango
        cv, cv_est = cv * neto / vp, cv_est * neto / vp
    costo_venta = round(cv + cv_est)
    # comisión de tarjetas
    tarj = {"debito": 0, "credito": 0}
    for r in con.execute("SELECT medio, SUM(bruto) b FROM venta_medio_pago WHERE fecha BETWEEN ? AND ? GROUP BY medio", (a, b)):
        m = r["medio"].lower()
        if "tarj" in m or "débito" in m or "crédito" in m:
            tarj["credito" if "cr" in m else "debito"] += r["b"]
    tasa = F.tasa_tarjetas(con, cfg)
    comision = round(tarj["debito"] * tasa["debito"] / 100 + tarj["credito"] * tasa["credito"] / 100) if tasa else 0
    sueldos = gastos = 0.0
    for p in _meses(d0, d1):
        f = _fraccion(p, d0, d1)
        pe, go = _gastos_mes(con, cfg, p, cache, hoy)
        sueldos, gastos = sueldos + pe * f, gastos + go * f
    sueldos, gastos = round(sueldos), round(gastos + comision)
    margen = neto - costo_venta
    ganancia = margen - sueldos - gastos
    compras = con.execute("SELECT COALESCE(SUM(CASE WHEN tipo_doc=61 THEN -c.neto ELSE c.neto END),0) FROM compra c "
                          "JOIN proveedor p ON p.rut=c.rut WHERE c.fecha_doc BETWEEN ? AND ? AND p.categoria IN (%s)"
                          % ",".join("'%s'" % k for k, g in F.CATEGORIAS.items() if g == "costo_venta"), (a, b)).fetchone()[0]
    pct = lambda x: round(100 * x / neto, 1) if neto else None
    return {"desde": d0, "hasta": d1, "bruto": v["b"], "neto": neto, "tickets": v["t"], "dias": v["d"],
            "ticket": round(v["b"] / v["t"]) if v["t"] else 0, "costo_venta": costo_venta,
            "costo_estimado": round(cv_est), "cobertura": round(100 * (1 - cv_est / (cv + cv_est)), 0) if cv + cv_est else 0,
            "margen": margen, "margen_pct": pct(margen), "sueldos": sueldos, "gastos": gastos, "comision": comision,
            "ganancia": ganancia, "ganancia_pct": pct(ganancia), "compras_insumos": compras, "tarjetas": tarj}


def ventas_diarias(con, cfg, d0, d1):
    iva = cfg["empresa"]["iva"]
    m = {r["fecha"]: r for r in con.execute("SELECT fecha, SUM(bruto) b, SUM(tickets) t FROM venta_hora "
                                             "WHERE fecha BETWEEN ? AND ? GROUP BY fecha", (d0.isoformat(), d1.isoformat()))}
    out, d = [], d0
    while d <= d1:
        r = m.get(d.isoformat())
        out.append({"fecha": d.isoformat(), "etiqueta": f"{DIAS_CORTO[d.weekday()]} {d.day}",
                    "neto": round(r["b"] / (1 + iva)) if r else 0, "tickets": r["t"] if r else 0})
        d += timedelta(days=1)
    return out


def agrupar(dias, n):
    """Para rangos largos, agrupa por semana (lunes) o por mes."""
    if n <= 45:
        return dias
    grupos, orden = {}, []
    for x in dias:
        d = date.fromisoformat(x["fecha"])
        if n <= 200:
            k = d - timedelta(days=d.weekday())
            et = f"sem {k.day} {MESES[k.month - 1]}"
        else:
            k = d.replace(day=1)
            et = f"{MESES[k.month - 1]} {str(k.year)[2:]}"
        if k not in grupos:
            grupos[k] = {"fecha": k.isoformat(), "etiqueta": et, "neto": 0, "tickets": 0}
            orden.append(k)
        grupos[k]["neto"] += x["neto"]
        grupos[k]["tickets"] += x["tickets"]
    return [grupos[k] for k in orden]


def por_categoria(con, cfg, d0, d1, top=5):
    iva = cfg["empresa"]["iva"]
    rows = [(r["categoria"], round(r["b"] / (1 + iva))) for r in con.execute(
        "SELECT categoria, SUM(bruto) b FROM venta_producto WHERE fecha BETWEEN ? AND ? GROUP BY categoria ORDER BY b DESC",
        (d0.isoformat(), d1.isoformat())) if r["b"] > 0]
    if len(rows) > top + 1:
        rows = rows[:top] + [("Otros", sum(x[1] for x in rows[top:]))]
    return [{"nombre": n, "neto": v} for n, v in rows]


def top_productos(con, cfg, d0, d1, n=10):
    p0, p1 = d0.strftime("%Y-%m"), d1.strftime("%Y-%m")
    iva, costos = cfg["empresa"]["iva"], cfg["costo_producto"]
    out = []
    for r in con.execute("SELECT producto, SUM(cantidad) q, SUM(bruto) b FROM venta_producto WHERE fecha BETWEEN ? AND ? "
                         "GROUP BY producto HAVING b > 0", (d0.isoformat(), d1.isoformat())):
        neto = r["b"] / (1 + iva)
        c = costos.get(r["producto"], {}).get("costo")
        out.append({"producto": r["producto"], "cantidad": round(r["q"]), "neto": round(neto),
                    "ganancia": round(neto - c * r["q"]) if c is not None else None})
    return {"venta": sorted(out, key=lambda x: -x["neto"])[:n],
            "ganancia": sorted([x for x in out if x["ganancia"] is not None], key=lambda x: -x["ganancia"])[:n]}


def medios(con, d0, d1):
    return [{"nombre": r["medio"].replace("Tarj.", "Tarjeta de").capitalize(), "bruto": r["b"]} for r in con.execute(
        "SELECT medio, SUM(bruto) b FROM venta_medio_pago WHERE fecha BETWEEN ? AND ? GROUP BY medio HAVING b > 0 ORDER BY b DESC",
        (d0.isoformat(), d1.isoformat()))]


def semanas(con, cfg, ultimo, n=12):
    lunes = ultimo - timedelta(days=ultimo.weekday())
    cache, out = {}, []
    for i in range(n, 0, -1):
        a = lunes - timedelta(days=7 * i)
        r = resultado(con, cfg, a, a + timedelta(days=6), cache)
        out.append({"etiqueta": f"{a.day} {MESES[a.month - 1]}", "neto": r["neto"], "margen": r["margen"], "ganancia": r["ganancia"],
                    "costo_venta": r["costo_venta"], "sueldos_gastos": r["sueldos"] + r["gastos"], "tickets": r["tickets"]})
    return out


def spark(valores, ancho=120, alto=30):
    """Mini gráfico de línea en SVG para las tarjetas (sin ejes; el detalle está en el gráfico de 12 semanas)."""
    v = [x for x in valores if x is not None]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i * ancho / (len(v) - 1):.1f},{alto - 3 - (x - lo) * (alto - 6) / rng:.1f}" for i, x in enumerate(v))
    return (f'<svg class="spark" viewBox="0 0 {ancho} {alto}" preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{pts}" vector-effect="non-scaling-stroke"/></svg>')
