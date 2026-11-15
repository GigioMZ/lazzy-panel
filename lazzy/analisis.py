"""Análisis para decidir: días y horas, estimación de venta, precios a revisar y tendencia de productos."""
import math
import statistics
from datetime import date, timedelta

from . import finance as F
from .insights import clp

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
# Margen mínimo esperado según la categoría de FUDO. Preparados en el local deben dejar más.
META_MARGEN = {"Café C/Leche": 70, "Café S/Leche": 70, "Café Frío": 70, "Té C/Leche": 70, "Té": 70,
               "Chocolate C/Leche": 70, "Filtrado": 70, "Agregados": 70}
META_DEFECTO = 45
SUBIDA_MAX = 0.20     # sobre esto no se sugiere precio: el problema es el costo


def _ultimo(con):
    u = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0]
    return date.fromisoformat(u) if u else None


def ventanas(con, semanas=12):
    fin = _ultimo(con)
    return (fin - timedelta(days=7 * semanas - 1), fin) if fin else (None, None)


def por_dia_semana(con, semanas=12):
    ini, fin = ventanas(con, semanas)
    if not fin:
        return []
    dias = {}
    for r in con.execute("SELECT fecha, SUM(bruto) b, SUM(tickets) t, COUNT(*) h FROM venta_hora "
                         "WHERE fecha BETWEEN ? AND ? GROUP BY fecha", (ini.isoformat(), fin.isoformat())):
        d = date.fromisoformat(r["fecha"]).weekday()
        dias.setdefault(d, []).append((r["b"], r["t"], r["h"]))
    out = []
    for d in range(7):
        xs = dias.get(d, [])
        if len(xs) < 2:
            out.append({"dia": d, "nombre": DIAS[d], "abierto": False})
            continue
        ventas = [x[0] for x in xs]
        out.append({"dia": d, "nombre": DIAS[d], "abierto": True, "n": len(xs),
                    "venta": round(statistics.mean(ventas)), "tickets": round(statistics.mean(x[1] for x in xs)),
                    "desv": round(statistics.pstdev(ventas)), "horas": round(statistics.mean(x[2] for x in xs)),
                    "por_hora": round(statistics.mean(x[0] / x[2] for x in xs))})
    tope = max([x["venta"] for x in out if x["abierto"]] + [1])
    for x in out:
        x["ancho"] = round(100 * x["venta"] / tope, 1) if x["abierto"] else 0
    return out


def mapa_horas(con, semanas=12):
    """Promedio de venta y de ventas (tickets) por día de la semana y hora, sobre los días que abrió."""
    ini, fin = ventanas(con, semanas)
    if not fin:
        return {"horas": [], "filas": []}
    abiertos, celdas = {}, {}
    for r in con.execute("SELECT fecha, hora, bruto, tickets FROM venta_hora WHERE fecha BETWEEN ? AND ?",
                         (ini.isoformat(), fin.isoformat())):
        d = date.fromisoformat(r["fecha"]).weekday()
        abiertos.setdefault(d, set()).add(r["fecha"])
        c = celdas.setdefault((d, r["hora"]), [0, 0])
        c[0] += r["bruto"]
        c[1] += r["tickets"]
    horas = sorted({h for _, h in celdas})
    filas, valores = [], []
    for d in range(7):
        n = len(abiertos.get(d, ()))
        if n < 2:
            continue
        cel = []
        for h in horas:
            b, t = celdas.get((d, h), (0, 0))
            v = {"hora": h, "venta": round(b / n), "tickets": round(t / n, 1)}
            cel.append(v)
            if b:
                valores.append(v["venta"])
        filas.append({"dia": d, "nombre": DIAS[d], "celdas": cel})
    tope = max(valores + [1])
    for f in filas:
        for c in f["celdas"]:
            c["nivel"] = c["venta"] / tope
            c["fondo"] = _azul(c["nivel"]) if c["venta"] else "transparent"
            c["claro"] = c["nivel"] < 0.55
    return {"horas": horas, "filas": filas, "tope": tope}


def _azul(t):
    """Escala de un solo tono, de crema claro a azul Lazzy."""
    a, b = (0xF3, 0xEA, 0xD3), (0x1F, 0x4E, 0x73)
    t = max(0.0, min(1.0, t)) ** 0.8
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def estimacion(con, desde=None, dias=7):
    """Venta esperada: promedio del mismo día de la semana en 8 semanas, ajustado por la tendencia reciente."""
    fin = _ultimo(con)
    if not fin:
        return {"dias": [], "tendencia": None}
    tot = lambda a, b: con.execute("SELECT COALESCE(SUM(bruto),0) FROM venta_hora WHERE fecha BETWEEN ? AND ?",
                                   (a.isoformat(), b.isoformat())).fetchone()[0]
    rec, ant = tot(fin - timedelta(days=27), fin), tot(fin - timedelta(days=55), fin - timedelta(days=28))
    tend = rec / ant if ant else 1.0
    factor = max(0.85, min(1.15, tend))
    base = {x["dia"]: x for x in por_dia_semana(con, 8)}
    desde = desde or max(date.today(), fin + timedelta(days=1))
    out = []
    for i in range(dias):
        f = desde + timedelta(days=i)
        b = base.get(f.weekday())
        if not b or not b["abierto"]:
            out.append({"fecha": f, "nombre": DIAS[f.weekday()], "abierto": False})
            continue
        v = round(b["venta"] * factor)
        out.append({"fecha": f, "nombre": DIAS[f.weekday()], "abierto": True, "venta": v,
                    "min": max(0, round(v - b["desv"])), "max": round(v + b["desv"]), "tickets": round(b["tickets"] * factor)})
    return {"dias": out, "tendencia": round(100 * (tend - 1), 1), "factor": factor, "topado": factor != tend,
            "total": sum(x["venta"] for x in out if x["abierto"])}


def _precio_redondo(bruto):
    """Sube al siguiente precio terminado en 90 (2.990, 3.490, 3.590...)."""
    return int(math.ceil((bruto - 90) / 100.0) * 100 + 90)


def precios_a_revisar(con, cfg, desde, hasta):
    m = F.margen_productos(con, desde, hasta, cfg)
    iva = cfg["empresa"]["iva"]
    n_meses = max(1, len([p for p in F.periodos(con) if desde <= p <= hasta]))
    out = []
    for d in m["productos"]:
        if d["costo"] is None or not d["precio_neto"] or d["cantidad"] < 5:
            continue
        meta = META_MARGEN.get(d["categoria"], META_DEFECTO)
        if d["margen_pct"] >= meta - 2:      # tolerancia: 1 o 2 puntos no justifican cambiar la carta
            continue
        neto_obj = d["costo"] / (1 - meta / 100)
        sugerido = _precio_redondo(neto_obj * (1 + iva))
        subida = sugerido / d["precio_bruto"] - 1 if d["precio_bruto"] else 0
        x = {**d, "meta": meta, "por_mes": round(d["cantidad"] / n_meses)}
        if subida <= SUBIDA_MAX:
            nuevo_neto = sugerido / (1 + iva)
            x.update(accion="precio", sugerido=sugerido, subida=round(100 * subida, 1),
                     nuevo_margen=round(100 * (1 - d["costo"] / nuevo_neto), 1),
                     extra_mes=round((nuevo_neto - d["precio_neto"]) * x["por_mes"]))
        else:
            x.update(accion="costo", costo_obj=round(d["precio_neto"] * (1 - meta / 100)),
                     extra_mes=round((d["costo"] - d["precio_neto"] * (1 - meta / 100)) * x["por_mes"]))
        out.append(x)
    return sorted(out, key=lambda x: -x["extra_mes"])


def tendencia_productos(con, minimo=8):
    """Unidades de las últimas 4 semanas contra las 4 anteriores."""
    fin = con.execute("SELECT MAX(fecha) FROM venta_producto").fetchone()[0]
    if not fin:
        return []
    fin = date.fromisoformat(fin)
    a0, a1 = fin - timedelta(days=55), fin - timedelta(days=28)
    out = []
    for r in con.execute(
            "SELECT producto, SUM(CASE WHEN fecha > ? THEN cantidad ELSE 0 END) rec, "
            "SUM(CASE WHEN fecha <= ? THEN cantidad ELSE 0 END) ant FROM venta_producto "
            "WHERE fecha BETWEEN ? AND ? GROUP BY producto", (a1.isoformat(), a1.isoformat(), a0.isoformat(), fin.isoformat())):
        if max(r["rec"], r["ant"]) < minimo:
            continue
        cambio = round(100 * (r["rec"] - r["ant"]) / r["ant"], 1) if r["ant"] else None
        out.append({"producto": r["producto"], "rec": round(r["rec"]), "ant": round(r["ant"]), "cambio": cambio})
    return sorted(out, key=lambda x: -(x["cambio"] if x["cambio"] is not None else 999))


def recomendaciones(dias, mapa, precios, tend):
    """Frases accionables a partir de los análisis."""
    out = []
    semana = [d for d in dias if d["abierto"] and d["dia"] < 5]
    if semana:
        flojo = min(semana, key=lambda d: d["venta"])
        fuerte = max(semana, key=lambda d: d["venta"])
        out.append({"tipo": "anuncios", "titulo": f"Para anuncios: el {flojo['nombre']} es tu día más flojo",
                    "texto": f"Vendes en promedio {clp(flojo['venta'])} el {flojo['nombre']} contra {clp(fuerte['venta'])} el {fuerte['nombre']}. "
                             "Un anuncio o una promoción ese día tiene más espacio para sumar clientes que en un día que ya está lleno."})
    # bloque de 2 horas más flojo y más fuerte de lunes a viernes, con el local abierto
    bloques = []
    for f in mapa["filas"]:
        if f["dia"] >= 5:
            continue
        cs = [c for c in f["celdas"] if c["venta"]][1:-1]   # sin la hora de apertura ni la de cierre
        for a, b in zip(cs, cs[1:]):
            if b["hora"] == a["hora"] + 1:
                bloques.append((a["venta"] + b["venta"], a["tickets"] + b["tickets"], f["nombre"], a["hora"]))
    if bloques:
        bajos = sorted(bloques)[:3]
        horas = {}
        for _, _, d, h in bajos:
            horas.setdefault(h, []).append(d)
        h, ds = max(horas.items(), key=lambda x: len(x[1]))
        out.append({"tipo": "anuncios", "titulo": f"Horario flojo: de {h}:00 a {h + 2}:00",
                    "texto": f"Es el bloque con menos venta ({', '.join(ds)}). Sirve para una promoción de horario (por ejemplo café + pastel) o para concentrar ahí los anuncios."})
        altos = sorted(bloques, reverse=True)[:3]
        t = ", ".join(f"{d} de {h}:00 a {h + 2}:00" for _, tk, d, h in altos)
        out.append({"tipo": "apoyo", "titulo": "Cuándo necesitas más manos",
                    "texto": f"Las horas más cargadas son {t}, con unas {round(altos[0][1])} ventas en esas 2 horas. "
                             "Si alguna vez se suma una segunda persona o haces una activación en el local, que sea en esos horarios."})
    if precios:
        p = [x for x in precios if x["accion"] == "precio"][:3]
        if p:
            total = sum(x["extra_mes"] for x in p)
            nombres = ", ".join(f"{x['producto']} de {clp(x['precio_bruto'])} a {clp(x['sugerido'])}" for x in p)
            out.append({"tipo": "precio", "titulo": "Precios que conviene subir",
                        "texto": f"{nombres}. Si se siguen vendiendo igual, son unos {clp(total)} más de ganancia al mes. "
                                 + "Ojo: cuando subieron el NY Roll se vendió menos, así que conviene subir de a uno y mirar las ventas dos semanas."})
        c = [x for x in precios if x["accion"] == "costo"][:2]
        if c:
            out.append({"tipo": "precio", "titulo": "Productos donde el problema es el costo",
                        "texto": "; ".join(f"{x['producto']}: para dejar {x['meta']}% tendría que costarte {clp(x['costo_obj'])} y hoy te cuesta {clp(x['costo'])}" for x in c)
                                 + ". Subir el precio no alcanza sin espantar clientes: conviene negociar con el proveedor o cambiarlo."})
    caen = [x for x in tend if x["cambio"] is not None and x["cambio"] <= -25 and x["ant"] >= 15]
    if caen:
        out.append({"tipo": "producto", "titulo": "Productos que se están vendiendo menos",
                    "texto": ", ".join(f"{x['producto']} ({x['cambio']:.0f}%)" for x in caen[:4])
                             + " en las últimas 4 semanas contra las 4 anteriores. Revisa si cambió algo: precio, presentación, disponibilidad."})
    return out
