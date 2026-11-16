"""Ofertas: cómo les va a los combos actuales y qué combos nuevos conviene probar, con sus números."""
import difflib
from datetime import date, timedelta

from . import analisis as A
from .captura import _clave

BEBIDAS = ("café", "cafe", "té", "te ", "chocolate", "filtrado", "matcha")
COMIDA = ("pasteler", "sandwich", "dulce", "salado")


def _ultimo(con):
    u = con.execute("SELECT MAX(fecha) FROM venta_producto").fetchone()[0]
    return date.fromisoformat(u) if u else None


def catalogo(con, cfg, dias=90):
    """Precio (con y sin IVA), unidades, costo y categoría de cada producto vendido en los últimos `dias`."""
    fin = _ultimo(con)
    if not fin:
        return {}
    iva, costos = cfg["empresa"]["iva"], cfg["costo_producto"]
    out = {}
    for r in con.execute("SELECT producto, MAX(categoria) cat, SUM(cantidad) q, SUM(bruto) b FROM venta_producto "
                         "WHERE fecha > ? GROUP BY producto HAVING q > 0 AND b > 0", ((fin - timedelta(days=dias)).isoformat(),)):
        c = (costos.get(r["producto"]) or {}).get("costo")
        out[r["producto"]] = {"producto": r["producto"], "categoria": r["cat"], "uds": r["q"], "bruto": round(r["b"] / r["q"]),
                              "neto": round(r["b"] / r["q"] / (1 + iva)), "costo": c}
    return out


def es_combo(nombre):
    return "+" in nombre


def _tipo(p):
    c = (p["categoria"] or "").lower() + " "
    if any(x in c for x in BEBIDAS):
        return "bebida"
    if any(x in c for x in COMIDA):
        return "comida"
    return "otro"


def componente(texto, cat):
    """Producto de la carta al que se refiere una parte del nombre del combo («Cappu» → Cappuccino). Si nombra
    una categoría («Sandwich»), usa el promedio de esa categoría ponderado por lo que se vende."""
    t = _clave(texto)
    simples = {k: v for k, v in cat.items() if not es_combo(k)}
    claves = {_clave(k): k for k in simples if len(_clave(k)) >= 3}
    if len(t) < 3:
        return None
    pref = [claves[k] for k in claves if k.startswith(t) or t.startswith(k)]
    if pref:
        return simples[max(pref, key=lambda k: simples[k]["uds"])]
    m = difflib.get_close_matches(t, list(claves), n=1, cutoff=0.6)
    if m:
        return simples[claves[m[0]]]
    cats = {}
    for p in simples.values():
        if len(_clave(p["categoria"] or "")) >= 3:
            cats.setdefault(_clave(p["categoria"] or ""), []).append(p)
    m = difflib.get_close_matches(t, list(cats), n=1, cutoff=0.6) or [k for k in cats if k.startswith(t[:5])][:1]
    if m:
        ps = cats[m[0]]
        u = sum(p["uds"] for p in ps)
        con_costo = [p for p in ps if p["costo"] is not None]
        uc = sum(p["uds"] for p in con_costo)
        return {"producto": f"{texto.strip()} (promedio)", "categoria": ps[0]["categoria"], "uds": u,
                "bruto": round(sum(p["bruto"] * p["uds"] for p in ps) / u), "neto": round(sum(p["neto"] * p["uds"] for p in ps) / u),
                "costo": round(sum(p["costo"] * p["uds"] for p in con_costo) / uc) if uc else None}
    return None


def _semanas(con, prod, fin, n=4):
    q = lambda a, b: con.execute("SELECT COALESCE(SUM(cantidad),0) FROM venta_producto WHERE producto=? AND fecha > ? AND fecha <= ?",
                                 (prod, a.isoformat(), b.isoformat())).fetchone()[0]
    rec, ant = q(fin - timedelta(days=7 * n), fin), q(fin - timedelta(days=14 * n), fin - timedelta(days=7 * n))
    return rec, ant


def actuales(con, cfg, cat=None):
    """Combos que se venden hoy: cuánto se venden, cuánto descuento dan y cuánto dejan."""
    cat = cat or catalogo(con, cfg)
    fin = _ultimo(con)
    out = []
    for nombre, p in cat.items():
        if not es_combo(nombre):
            continue
        partes = [componente(x, cat) for x in nombre.split("+")]
        rec, ant = _semanas(con, nombre, fin)
        x = {"producto": nombre, "precio": p["bruto"], "uds_semana": round(rec / 4, 1), "tendencia": round(100 * (rec / ant - 1)) if ant else None,
             "partes": [y["producto"] for y in partes if y], "completo": all(partes)}
        if all(partes):
            sep = sum(y["bruto"] for y in partes)
            x.update(separado=sep, descuento=round(100 * (1 - p["bruto"] / sep)) if sep else None)
            costo = p["costo"] if p["costo"] is not None else (sum(y["costo"] for y in partes) if all(y["costo"] is not None for y in partes) else None)
            if costo is not None:
                sep_neto = sum(y["neto"] for y in partes)
                x.update(costo=costo, deja=p["neto"] - costo, margen=round(100 * (1 - costo / p["neto"])) if p["neto"] else None,
                         margen_sep=round(100 * (1 - costo / sep_neto)) if sep_neto else None,
                         deja_mes=round((p["neto"] - costo) * rec / 4 * 4.33))
        out.append(x)
    return sorted(out, key=lambda x: -x["uds_semana"])


def _precio_combo(sep, desc=0.10):
    p = sep * (1 - desc)
    return int(p // 100 * 100 - 10) if p % 100 < 90 else int(p // 100 * 100 + 90)


def _combo(a, b, porque, cuando, iva):
    sep = a["bruto"] + b["bruto"]
    precio = _precio_combo(sep)
    neto = precio / (1 + iva)
    costo = a["costo"] + b["costo"]
    return {"nombre": f"{a['producto']} + {b['producto']}", "partes": [a["producto"], b["producto"]], "separado": sep,
            "precio": precio, "descuento": round(100 * (1 - precio / sep)), "costo": costo, "deja": round(neto - costo),
            "margen": round(100 * (1 - costo / neto)), "porque": porque, "cuando": cuando}


def sugerencias(con, cfg, cat=None, ya=()):
    """Hasta 3 combos nuevos con números: solo productos con costo cargado y que no estén ya combinados."""
    cat = cat or catalogo(con, cfg)
    iva = cfg["empresa"]["iva"]
    simples = [p for k, p in cat.items() if not es_combo(k) and p["costo"] is not None and p["uds"] >= 10]
    beb = sorted([p for p in simples if _tipo(p) == "bebida"], key=lambda p: -p["uds"])
    com = [p for p in simples if _tipo(p) == "comida"]
    if not beb or not com:
        return []
    margen = lambda p: (p["neto"] - p["costo"]) / p["neto"] if p["neto"] else 0
    usados = {_clave(x) for c in ya for x in c}
    dias = A.por_dia_semana(con)
    mapa = A.mapa_horas(con)
    rec = {r["tipo"] + str(i): r["titulo"] for i, r in enumerate(A.recomendaciones(dias, mapa, [], []))}
    flojo = next((t.split(": ", 1)[1] for t in rec.values() if t.startswith("Horario flojo")), None)
    dia = next((t.split("el ", 1)[1].split(" es")[0] for t in rec.values() if t.startswith("Para anuncios")), None)
    out, vistos, comidas = [], set(), set()

    def agregar(a, b, porque, cuando):
        k = frozenset((a["producto"], b["producto"]))
        ya_combo = {_clave(a["producto"])[:5], _clave(b["producto"])[:5]} <= {u[:5] for u in usados}
        if k in vistos or ya_combo or a is b or b["producto"] in comidas:
            return
        comidas.add(b["producto"])
        vistos.add(k)
        out.append(_combo(a, b, porque, cuando, iva))

    top_com = max(com, key=lambda p: p["uds"])
    rentable_poco = sorted([p for p in com if p["uds"] < top_com["uds"] * 0.6], key=lambda p: -margen(p))
    if rentable_poco:
        agregar(beb[0], rentable_poco[0], f"Junta la bebida que más vendes con {rentable_poco[0]['producto']}, que deja buen margen "
                                          "pero se vende poco: el combo lo hace conocido.",
                f"Ideal {flojo.lower()}" if flojo else "Todo el día")
    agregar(beb[0], top_com, "Las dos cosas que más se venden: el combo invita a quien pide una a llevar las dos, y sube el ticket.",
            f"Para anunciar el {dia}" if dia else "Todo el día")
    mejor_beb = max(beb[:5], key=margen)
    for c in sorted(com, key=margen, reverse=True):
        if c["producto"] not in comidas:
            agregar(mejor_beb, c, "La bebida y el dulce que más dejan por unidad: es el combo con mejor margen.", "Todo el día")
            break
    return out[:3]


def resumen(con, cfg):
    cat = catalogo(con, cfg)
    act = actuales(con, cfg, cat)
    return {"actuales": act, "sugeridas": sugerencias(con, cfg, cat, ya=[a["partes"] for a in act])}
