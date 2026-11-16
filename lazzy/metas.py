"""Metas del mes, ritmo (a cuánto se va a cerrar el mes en curso) y lista de cierre."""
from datetime import date, timedelta

from . import analisis as A
from . import finance as F
from .db import mes_rango

CLAVES = {"venta": ("Venta sin IVA", "min"), "costo_venta_pct": ("Costo de venta, % de la venta", "max"),
          "resultado": ("Ganancia del mes", "min")}


def leer(con, periodo):
    out = {r["clave"]: r["valor"] for r in con.execute("SELECT * FROM meta WHERE periodo='*'")}
    out.update({r["clave"]: r["valor"] for r in con.execute("SELECT * FROM meta WHERE periodo=?", (periodo,))})
    return out


def guardar(con, periodo, valores, todos=False):
    destino = "*" if todos else periodo
    if todos:     # al cambiar la meta general, se borran las excepciones de este mes para que se vea el cambio
        con.execute("DELETE FROM meta WHERE periodo=?", (periodo,))
    for k, v in valores.items():
        if k not in CLAVES:
            continue
        if v is None:
            con.execute("DELETE FROM meta WHERE periodo=? AND clave=?", (destino, k))
        else:
            con.execute("INSERT OR REPLACE INTO meta VALUES (?,?,?)", (destino, k, float(v)))
    con.commit()


def _cerrados(con, cfg, periodo, n=3):
    ps = [x for x in F.periodos(con) if x < periodo]
    out = []
    for x in reversed(ps):
        p = F.pnl_mes(con, x, cfg)
        if p["tiene_compras"]:
            out.append(p)
        if len(out) == n:
            break
    return out


def ritmo(con, cfg, periodo, p=None, hoy=None):
    """Para un mes sin terminar: cuánto va, qué parte del mes pasó y la venta estimada al cierre
    (lo vendido más lo esperado para cada día que falta, según el mismo día de la semana)."""
    p = p or F.pnl_mes(con, periodo, cfg)
    a, b = (date.fromisoformat(x) for x in mes_rango(periodo))
    fin = b - timedelta(days=1)
    ult = con.execute("SELECT MAX(fecha) FROM venta_hora WHERE fecha >= ? AND fecha < ?", (a.isoformat(), b.isoformat())).fetchone()[0]
    if not ult or date.fromisoformat(ult) >= fin:
        return None
    ult = date.fromisoformat(ult)
    falta = (fin - ult).days
    est = A.estimacion(con, desde=ult + timedelta(days=1), dias=falta)
    iva = cfg["empresa"]["iva"]
    proy = p["ventas"]["neto"] + round(est.get("total", 0) / (1 + iva))
    prev = _cerrados(con, cfg, periodo)
    res = None
    if prev:
        cv = sum(x["costo_venta"] for x in prev) / sum(x["ventas"]["neto"] for x in prev)
        fijos = sum(x["personal"] + x["gasto_operacional"] for x in prev) / len(prev)
        res = round(proy * (1 - cv) - fijos)
    return {"hasta": ult, "avance": round(100 * ((ult - a).days + 1) / ((fin - a).days + 1)),
            "venta": p["ventas"]["neto"], "proy_venta": proy, "proy_resultado": res, "dias_faltan": falta}


def comparar(con, cfg, periodo, p, metas, rit=None):
    """Metas contra lo real (o contra lo proyectado si el mes no termina)."""
    real = {"venta": p["ventas"]["neto"], "costo_venta_pct": p["pct"]["costo_venta"] if p["tiene_compras"] else None,
            "resultado": p["resultado_operacional"] if p["tiene_compras"] else None}
    proy = {"venta": rit["proy_venta"], "resultado": rit["proy_resultado"]} if rit else {}
    out = []
    for k, (nombre, sentido) in CLAVES.items():
        if k not in metas:
            continue
        m, r = metas[k], proy.get(k, real[k])
        if r is None:
            out.append({"clave": k, "nombre": nombre, "meta": m, "real": None, "actual": None, "proyectado": False, "estado": "neutro", "avance": None})
            continue
        cumple = r >= m if sentido == "min" else r <= m
        cerca = (r >= m * 0.95) if sentido == "min" else (r <= m * 1.05)
        out.append({"clave": k, "nombre": nombre, "meta": m, "real": r, "actual": real[k], "proyectado": k in proy,
                    "estado": "ok" if cumple else ("warn" if cerca else "bad"),
                    "avance": round(100 * r / m) if m and sentido == "min" else None})
    return out


def checklist(con, cfg, periodo, p):
    """Lo que hay que tener listo para dar el mes por cerrado (sin inventario)."""
    fin = F.fin_de_mes(periodo)
    rng = mes_rango(periodo)
    fmt = lambda f: f"{f[8:10]}-{f[5:7]}" if f else "nada"
    uv = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0]
    up = con.execute("SELECT MAX(fecha) FROM venta_producto").fetchone()[0]
    sii = con.execute("SELECT COUNT(*) FROM compra WHERE periodo=? AND origen='sii'", (periodo,)).fetchone()[0]
    sin = p["sin_clasificar_detalle"]
    serv = con.execute("SELECT 1 FROM gasto_manual WHERE categoria='servicios' AND fecha >= ? AND fecha < ?", rng).fetchone() or \
        any(c.get("categoria") == "servicios" and not c.get("estimado") for c in cfg.get("costo_fijo", []))
    mp = F.margen_productos(con, periodo, periodo, F.load_cfg(con, fin))
    items = [
        ("Ventas de FUDO hasta el último día", bool(uv and uv >= fin), f"Cargadas hasta el {fmt(uv)}.", "/cargar", "Subir"),
        ("Productos de FUDO hasta el último día", bool(up and up >= fin), f"Cargados hasta el {fmt(up)}.", "/cargar", "Subir"),
        ("Compras del SII del mes", sii > 0, f"{sii} documentos." if sii else "Se descarga del SII los primeros días del mes siguiente.", "/cargar", "Subir"),
        ("Todas las facturas con categoría", not sin,
         "Listo." if not sin else (lambda n: f"{n} proveedor{'es' if n != 1 else ''} sin categoría.")(len({x[1] for x in sin})), "/compras", "Clasificar"),
        ("Cuentas de luz y agua reales", bool(serv), "Cargadas." if serv else "Se está usando el monto estimado. Mándalas al bot.", "/bot", "Ver bot"),
        ("Factura de Transbank del mes", not p["comision"]["estimada"],
         "Llegó." if not p["comision"]["estimada"] else "Todavía es estimada; llega con el SII del mes siguiente.", "/cargar", "Subir"),
        ("Costo cargado de lo que se vendió", (mp["cobertura_pct"] or 0) >= 90, f"{mp['cobertura_pct']}% de la venta tiene costo.", "/productos?grupo=sin", "Completar"),
        ("Tasa de PPM", cfg["empresa"].get("ppm_tasa", 0) > 0,
         "Configurada." if cfg["empresa"].get("ppm_tasa", 0) > 0 else "Sin ella el F29 estimado sale más bajo.", "/costos-fijos", "Configurar"),
    ]
    return [{"texto": t, "ok": ok, "detalle": d, "url": u, "accion": acc} for t, ok, d, u, acc in items]
