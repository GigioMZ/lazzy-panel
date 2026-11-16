"""Lectura en simple de los números: veredicto del mes, desglose por cada $1.000 y lista de pendientes."""
from datetime import date

from . import finance as F

MESES = "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split()
META = {"insumos": 350, "insumos_sueldos": 600, "ganancia": 100}   # por cada $1.000 vendidos


def mes_largo(p):
    return f"{MESES[int(p[5:7]) - 1]} {p[:4]}"


def mes_solo(p):
    return MESES[int(p[5:7]) - 1]


def clp(x):
    return ("-" if x < 0 else "") + f"${abs(x):,.0f}".replace(",", ".")


def por_mil(p, monto):
    n = p["ventas"]["neto"]
    return round(1000 * monto / n) if n else 0


def estado(valor, verde, amarillo, alto_es_bueno):
    bien = valor >= verde if alto_es_bueno else valor <= verde
    medio = valor >= amarillo if alto_es_bueno else valor <= amarillo
    return "ok" if bien else "warn" if medio else "bad"


def veredicto(p, parcial):
    v, mes = p["ventas"], mes_solo(p["periodo"])
    if parcial:
        return {"titulo": f"{mes.capitalize()} va en {clp(v['neto'])} vendidos.",
                "detalle": "El mes todavía no termina, así que la ganancia se calcula cuando cierre y estén las facturas del SII.",
                "tono": "neutro", "con_resultado": False}
    if not p["tiene_compras"]:
        return {"titulo": f"En {mes} vendiste {clp(v['neto'])}.",
                "detalle": "No se puede saber cuánto ganaste: falta subir el archivo de compras del SII de este mes.",
                "tono": "neutro", "con_resultado": False}
    r, pm = p["resultado_operacional"], por_mil(p, p["resultado_operacional"])
    sc = p["sin_clasificar"]
    ojo = (f" Ojo: {clp(sc)} son facturas sin categoría; si alguna es compra de equipos, la ganancia real es mayor."
           if v["neto"] and sc / v["neto"] >= 0.05 else "")
    if r >= 0:
        return {"titulo": f"En {mes} te quedaron {clp(r)} de ganancia.",
                "detalle": f"De cada $1.000 que vendiste, ${pm} fueron ganancia. Es antes del impuesto a la renta y de lo que retiren los socios.{ojo}",
                "tono": estado(pm, META["ganancia"], 50, True), "con_resultado": True}
    return {"titulo": f"En {mes} perdiste {clp(-r)}.",
            "detalle": f"Gastaste {clp(1000 - pm)} por cada $1.000 que vendiste. Revisa abajo en qué se fue.{ojo}",
            "tono": "bad", "con_resultado": True}


def desglose(p):
    """Filas de '¿en qué se fue cada $1.000 que vendiste?'. ancho = % de la barra."""
    filas = [("insumos", "Costo de lo vendido", p["costo_venta"], f"lo sano: hasta ${META['insumos']}"),
             ("sueldos", "Sueldos", p["personal"], ""),
             ("gastos_local", "Gastos del local", p["gasto_operacional"], "")]
    if p["sin_clasificar"]:
        filas.append(("sin_categoria", "Facturas sin categoría", p["sin_clasificar"], "ponles categoría"))
    out = [{"clave": k, "nombre": n, "monto": m, "por_mil": por_mil(p, m), "meta": meta, "tipo": "costo"}
           for k, n, m, meta in filas]
    r = p["resultado_operacional"]
    out.append({"clave": "ganancia", "nombre": "Ganancia" if r >= 0 else "Pérdida", "monto": r,
                "por_mil": por_mil(p, r), "meta": f"lo sano: ${META['ganancia']} o más",
                "tipo": "gana" if r >= 0 else "pierde"})
    tope = max([abs(f["por_mil"]) for f in out] + [1])
    for f in out:
        f["ancho"] = round(100 * abs(f["por_mil"]) / tope, 1)
    return out


def contexto(con, cfg, per, ps):
    """Comparaciones: mes anterior y promedio de ganancia de los 3 meses anteriores con datos completos."""
    antes = [x for x in ps if x < per]
    prev = F.pnl_mes(con, antes[-1], cfg) if antes else None
    comp = [x for x in (F.pnl_mes(con, m, cfg) for m in antes[-3:]) if x["tiene_compras"]]
    hace_un_anio = f"{int(per[:4]) - 1}{per[4:]}"
    anio = F.pnl_mes(con, hace_un_anio, cfg) if hace_un_anio in ps else None
    return {"prev": prev, "n_prom": len(comp), "anio": anio,
            "prom_ganancia": round(sum(x["resultado_operacional"] for x in comp) / len(comp)) if comp else None}


def variacion(actual, anterior):
    if not anterior:
        return None
    return round(100 * (actual - anterior) / anterior, 1)


def pendientes(con, cfg, ps, hoy=None):
    """Cosas concretas por hacer para que los números sean confiables o para ganar más."""
    hoy = hoy or date.today()
    out = []
    r = con.execute("SELECT COUNT(DISTINCT c.rut) n, COALESCE(SUM(c.neto),0) m FROM compra c "
                    "LEFT JOIN proveedor p ON p.rut=c.rut WHERE p.rut IS NULL AND c.categoria IS NULL").fetchone()
    if r["n"]:
        out.append({"texto": f"{r['n']} proveedores sin categoría ({clp(r['m'])} en facturas). Mientras tanto la ganancia sale más baja de lo real o los insumos quedan incompletos.",
                    "url": "/compras", "accion": "Poner categoría"})
    cerrados = [x for x in ps if x < hoy.strftime("%Y-%m")]
    con_compras = {x[0] for x in con.execute("SELECT DISTINCT periodo FROM compra")}
    faltan = [x for x in cerrados if x not in con_compras]
    if faltan:
        rango = mes_largo(faltan[0]) if len(faltan) == 1 else f"{mes_largo(faltan[0])} a {mes_largo(faltan[-1])}"
        out.append({"texto": f"Falta el archivo de compras del SII de {len(faltan)} {'mes' if len(faltan) == 1 else 'meses'} ({rango}). Sin eso no se puede calcular la ganancia de esos meses.",
                    "url": "/cargar", "accion": "Subir archivos"})
    ult = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0]
    if ult and (hoy - date.fromisoformat(ult)).days > 7:
        d = date.fromisoformat(ult)
        out.append({"texto": f"Las ventas están cargadas hasta el {d.day} de {MESES[d.month - 1]}. Sube los reportes nuevos de FUDO para ver los días que faltan.",
                    "url": "/cargar", "accion": "Subir ventas"})
    if ps:
        m = F.margen_productos(con, ps[max(len(ps) - 3, 0)], ps[-1], cfg)
        sin = [d for d in m["productos"] if d["costo"] is None]
        if sin:
            out.append({"texto": f"{len(sin)} productos vendidos no tienen costo cargado (son el {100 - m['cobertura_pct']:.0f}% de tu venta). Sin costo no se sabe cuánto dejan.".replace(".0%", "%"),
                        "url": "/productos?grupo=sin", "accion": "Cargar costos"})
        flojos = sorted([d for d in m["productos"] if d["clase"] == "caballo" and d["margen_pct"] < 35],
                        key=lambda d: -d["cantidad"])[:3]
        if flojos:
            nombres = ", ".join(f"{d['producto']} ({d['margen_pct']:.0f}%)" for d in flojos)
            out.append({"texto": f"Se venden mucho pero dejan poco margen: {nombres}. Subir un poco el precio o negociar el costo aquí es lo que más mueve la ganancia.",
                        "url": "/productos?grupo=caballo", "accion": "Ver productos"})
    if not cfg["empresa"]["ppm_tasa"]:
        out.append({"texto": "La tasa de PPM está en 0, así que el pago mensual al SII sale más bajo de lo real. Pídele la tasa a la contadora.",
                    "url": "/costos-fijos", "accion": "Poner tasa"})
    return out


def frescura(con):
    v = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0]
    c = con.execute("SELECT MAX(periodo) FROM compra").fetchone()[0]
    d = date.fromisoformat(v) if v else None
    return {"ventas": f"{d.day} de {MESES[d.month - 1]}" if d else None, "compras": mes_largo(c) if c else None}
