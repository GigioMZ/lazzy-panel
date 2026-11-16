"""Estado de resultados de un mes: cada línea con su % de la venta, el mes anterior, hace un año y el acumulado
del año, y el detalle de documentos (facturas, gastos, gastos fijos) que forman cada categoría."""
from collections import defaultdict

from . import finance as F
from .db import mes_rango

GRUPOS = [("costo_venta", "Costo de venta", "insumos"), ("personal", "Sueldos", "sueldos"),
          ("gasto_operacional", "Gastos del local", "gastos_local"), ("sin_clasificar", "Facturas sin categoría", "sin_categoria")]
COLOR = {"costo_venta": "#2f6f9f", "personal": "#d9772b", "gasto_operacional": "#d6a300", "sin_clasificar": "#c94f63",
         "resultado": "#1f9e78"}


def menos(periodo, n):
    y, m = int(periodo[:4]), int(periodo[5:7]) - n
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f"{y}-{m:02d}"


def _valor(p, grupo, cat=None):
    if p is None:
        return None
    if cat is None:
        return p[grupo]
    return p["lineas"].get(grupo, {}).get(cat, 0)


def estado(con, cfg, periodo, ps):
    """Filas del estado de resultados con sus comparaciones."""
    cache = {}

    def pnl(x):
        if x not in ps:
            return None
        if x not in cache:
            cache[x] = F.pnl_mes(con, x, cfg)
        return cache[x]

    cur, prev, anio = pnl(periodo), pnl(menos(periodo, 1)), pnl(menos(periodo, 12))
    ytd_meses = [x for x in ps if x[:4] == periodo[:4] and x <= periodo and (x == periodo or pnl(x)["tiene_compras"])]
    ytd = [pnl(x) for x in ytd_meses]

    def fila(tipo, nombre, get, clave=None, grupo=None, cat=None, signo=-1):
        a = get(cur)
        y = sum(get(p) or 0 for p in ytd)
        vy = sum(p["ventas"]["neto"] for p in ytd)
        pv = get(prev) if prev else None
        return {"tipo": tipo, "nombre": nombre, "clave": clave, "grupo": grupo, "cat": cat, "signo": signo,
                "actual": a, "pct": round(100 * a / cur["ventas"]["neto"], 1) if cur["ventas"]["neto"] else None,
                "prev": pv, "var": round(100 * (a / pv - 1), 1) if pv else None,
                "anio": get(anio) if anio else None, "ytd": y, "ytd_pct": round(100 * y / vy, 1) if vy else None}

    filas = [fila("venta", "Venta sin IVA", lambda p: p["ventas"]["neto"], "venta_neta", signo=1)]
    for g, nombre, clave in GRUPOS:
        cats = sorted({c for p in [cur, prev, anio, *ytd] if p for c in p["lineas"].get(g, {})},
                      key=lambda c: -(cur["lineas"].get(g, {}).get(c, 0)))
        if g == "sin_clasificar" and not any(_valor(p, g) for p in [cur, *ytd]):
            continue
        filas.append(fila("grupo", nombre, lambda p, g=g: p[g], clave, g))
        if g != "sin_clasificar":
            for c in cats:
                filas.append(fila("cat", c, lambda p, g=g, c=c: _valor(p, g, c), None, g, c))
        if g == "costo_venta":
            filas.append(fila("sub", "Margen bruto", lambda p: p["margen_bruto"], "margen_bruto", signo=1))
    filas.append(fila("total", "Resultado operacional", lambda p: p["resultado_operacional"], "ganancia", signo=1))
    return {"filas": filas, "cur": cur, "prev": prev, "anio": anio, "ytd_meses": ytd_meses}


def detalle(con, cfg, periodo):
    """Documentos de cada categoría del mes: facturas (con nombre comercial), gastos sin factura, gastos fijos
    y comisión estimada. Devuelve {categoria: [docs]}."""
    alias = {r["rut"]: r["alias"] for r in con.execute("SELECT * FROM proveedor_alias")}
    prov = cfg["proveedores"]
    out = defaultdict(list)
    for r in con.execute("SELECT * FROM compra WHERE fecha_doc >= ? AND fecha_doc < ? ORDER BY fecha_doc", mes_rango(periodo)):
        cat = r["categoria"] or prov.get(r["rut"]) or "sin_clasificar"
        monto = F._sign(r["tipo_doc"]) * (r["neto"] + r["exento"] + r["iva_no_rec"] - r["activo_fijo_neto"])
        out[cat].append({"fecha": r["fecha_doc"], "nombre": alias.get(r["rut"]) or r["razon_social"], "rut": r["rut"],
                         "doc": ("Nota de crédito " if r["tipo_doc"] == 61 else "Factura ") + str(r["folio"]), "monto": monto})
    reales = set()
    for r in con.execute("SELECT * FROM gasto_manual WHERE fecha >= ? AND fecha < ? ORDER BY fecha", mes_rango(periodo)):
        reales.add(r["categoria"])
        out[r["categoria"]].append({"fecha": r["fecha"], "nombre": r["descripcion"], "doc": "Sin factura", "monto": r["neto"]})
    for c in cfg.get("costo_fijo", []):
        if c["desde"] <= periodo <= c.get("hasta", "9999-12") and not (c.get("estimado") and c["categoria"] in reales):
            out[c["categoria"]].append({"fecha": None, "nombre": c["nombre"],
                                        "doc": "Gasto fijo estimado" if c.get("estimado") else "Gasto fijo", "monto": c["monto"]})
    com = F.comision_mes(con, periodo, cfg)
    if com["estimada"]:
        out["comision_tarjetas"].append({"fecha": None, "nombre": "Comisión estimada (aún no llega la factura)",
                                         "doc": "Estimado", "monto": com["monto"]})
    return {k: sorted(v, key=lambda d: -abs(d["monto"])) for k, v in out.items()}


def composicion(p):
    """De cada $1.000 vendidos, cuánto se fue a cada grupo y cuánto quedó (para la barra de arriba)."""
    n = p["ventas"]["neto"]
    if not n:
        return []
    partes = [(g, nombre, p[g]) for g, nombre, _ in GRUPOS if p[g]]
    partes.append(("resultado", "Ganancia" if p["resultado_operacional"] >= 0 else "Pérdida", p["resultado_operacional"]))
    return [{"clave": g, "nombre": nom, "monto": m, "por_mil": round(1000 * m / n), "ancho": max(0, 100 * m / n), "color": COLOR[g]}
            for g, nom, m in partes]
