"""Base para el simulador «¿qué pasa si?»: el mes promedio de los últimos meses cerrados y la sensibilidad
al precio medida en sus propios cambios de precio."""
from datetime import date

from . import analisis as A
from . import metas as MT
from . import seguimiento as S


def base(con, cfg, hoy=None):
    meses = MT._cerrados(con, cfg, (hoy or date.today()).strftime("%Y-%m"), 3)
    if not meses:
        return None
    n = len(meses)
    prom = lambda f: round(sum(f(p) for p in meses) / n)
    com = prom(lambda p: p["lineas"].get("gasto_operacional", {}).get("comision_tarjetas", 0))
    venta = prom(lambda p: p["ventas"]["neto"])
    iva = cfg["empresa"]["iva"]
    dias = [{"nombre": d["nombre"], "venta": round(d["venta"] / (1 + iva))} for d in A.por_dia_semana(con) if d["abierto"]]
    cerrados = [d["nombre"] for d in A.por_dia_semana(con) if not d["abierto"]]
    return {"meses": [p["periodo"] for p in meses], "venta": venta, "costo": prom(lambda p: p["costo_venta"]),
            "sueldos": prom(lambda p: p["personal"]), "gastos": prom(lambda p: p["gasto_operacional"]) - com,
            "comision_pct": com / venta if venta else 0, "otros": prom(lambda p: p["sin_clasificar"]),
            "dias_abiertos": prom(lambda p: p["ventas"]["dias_abiertos"]), "dias": dias, "cerrados": cerrados}


def elasticidad(con, cfg):
    """La sensibilidad al precio medida en el cambio de precios con más productos (si hay datos suficientes)."""
    S.asegurar(con)
    mejor = None
    for g in [x for x in S.lista(con, cfg) if x["tipo"] == "precio"]:
        p = (g.get("medida") or {}).get("prod") or {}
        if p.get("elasticidad") is None or p.get("sin_base") or p.get("pocos_datos") or g["medida"]["midiendo"]:
            continue
        if not mejor or len(g["eventos"]) > len(mejor["g"]["eventos"]):
            mejor = {"g": g, "valor": max(-3.0, min(0.0, p["elasticidad"]))}
    if not mejor:
        return {"valor": -0.5, "texto": "No hay un cambio de precio medido todavía; se usa -0,5, un valor común en cafeterías."}
    g = mejor["g"]
    f = g["fecha"]
    return {"valor": mejor["valor"], "texto": f"Medida en el cambio de precio del {f[8:10]}-{f[5:7]}-{f[:4]} "
                                               f"({len(g['eventos'])} productos)."}
