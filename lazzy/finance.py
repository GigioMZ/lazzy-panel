"""Estado de resultados, IVA y KPIs mensuales.

Convenciones:
- Ventas FUDO vienen con IVA incluido -> neto = bruto / (1 + iva).
- Compras se imputan al periodo tributario del registro SII (mismo criterio que el F29).
- Notas de crédito (tipo 61) restan.
- El costo de venta es por COMPRAS del mes (no por consumo) hasta que exista conteo de inventario.
"""
try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib
from collections import defaultdict
from datetime import date

from .db import mes_rango


CATEGORIAS = {  # categoría -> grupo del estado de resultados
    "cafe": "costo_venta", "leche": "costo_venta", "te": "costo_venta", "pasteleria": "costo_venta",
    "sandwiches": "costo_venta", "bebidas": "costo_venta", "envases": "costo_venta",
    "otros_insumos": "costo_venta",
    "remuneraciones": "personal",
    "comision_tarjetas": "gasto_operacional", "comision_delivery": "gasto_operacional",
    "seguridad": "gasto_operacional", "telecom": "gasto_operacional", "servicios": "gasto_operacional",
    "marketing": "gasto_operacional", "mantencion": "gasto_operacional", "honorarios": "gasto_operacional",
    "otros_gastos": "gasto_operacional",
    "activo_fijo": "inversion",          # equipos: no es gasto del mes
}


def load_config(path="config/lazzy.toml"):
    with open(path, "rb") as f:
        return tomllib.load(f)


def seed_from_config(con, cfg):
    """Carga inicial de parámetros desde el toml. No pisa lo ya editado en el panel."""
    for k in ("iva", "ppm_tasa"):
        con.execute("INSERT OR IGNORE INTO parametro VALUES (?,?)", (k, str(cfg["empresa"].get(k, 0))))
    if cfg["empresa"].get("rut"):
        con.execute("INSERT OR IGNORE INTO parametro VALUES ('rut_empresa', ?)", (cfg["empresa"]["rut"],))
    for rut, cat in cfg.get("proveedores", {}).items():
        con.execute("INSERT OR IGNORE INTO proveedor(rut, categoria) VALUES (?,?)", (rut, cat))
    if not con.execute("SELECT 1 FROM costo_fijo LIMIT 1").fetchone():
        for c in cfg.get("costo_fijo", []):
            con.execute("INSERT INTO costo_fijo(nombre,categoria,monto,desde,hasta) VALUES (?,?,?,?,?)",
                        (c["nombre"], c["categoria"], c["monto"], c["desde"], c.get("hasta")))
    for prod, c in cfg.get("costo_producto", {}).items():
        con.execute("INSERT OR IGNORE INTO producto_costo(producto,costo,fuente) VALUES (?,?,?)",
                    (prod, c["costo"], c["fuente"]))
        con.execute("INSERT OR IGNORE INTO producto_costo_hist VALUES (?,?,?,?)", (prod, "2000-01-01", c["costo"], c["fuente"]))
    for i in cfg.get("insumo", []):
        con.execute("INSERT OR IGNORE INTO insumo(nombre,unidad,cantidad_formato,precio_formato) VALUES (?,?,?,?)",
                    (i["nombre"], i["unidad"], i["cantidad"], i["precio"]))
    con.execute("INSERT OR IGNORE INTO insumo_hist SELECT id, '2000-01-01', cantidad_formato, precio_formato FROM insumo")
    ids = {r["nombre"]: r["id"] for r in con.execute("SELECT id, nombre FROM insumo")}
    for prod, lineas in cfg.get("receta", {}).items():
        if con.execute("SELECT 1 FROM receta WHERE producto=? LIMIT 1", (prod,)).fetchone():
            continue
        for nombre, cant in lineas.items():
            con.execute("INSERT INTO receta VALUES (?,?,?)", (prod, ids[nombre], cant))
    con.commit()


def _precios_insumo(con, fecha=None):
    """Precio por unidad de cada insumo vigente en una fecha (por defecto, el actual)."""
    if not fecha:
        return {r["id"]: r["precio_formato"] / r["cantidad_formato"] for r in con.execute("SELECT * FROM insumo")}
    out = {}
    for r in con.execute("SELECT * FROM insumo_hist WHERE desde <= ? ORDER BY desde", (fecha,)):
        out[r["insumo_id"]] = r["precio_formato"] / r["cantidad_formato"]
    for r in con.execute("SELECT * FROM insumo"):          # insumos creados después de esa fecha
        out.setdefault(r["id"], r["precio_formato"] / r["cantidad_formato"])
    return out


def costo_receta(con, producto, fecha=None, precios=None):
    precios = precios if precios is not None else _precios_insumo(con, fecha)
    filas = con.execute("SELECT insumo_id, cantidad FROM receta WHERE producto=?", (producto,)).fetchall()
    return round(sum(r["cantidad"] * precios.get(r["insumo_id"], 0) for r in filas)) if filas else None


def registrar_costo(con, producto, costo, fuente, hoy=None):
    """Cambia el costo fijo de un producto guardando el historial (costo None = quitar)."""
    hoy = (hoy or date.today()).isoformat()
    if costo is None:
        con.execute("DELETE FROM producto_costo WHERE producto=?", (producto,))
    else:
        con.execute("INSERT OR REPLACE INTO producto_costo VALUES (?,?,?,?)", (producto, costo, fuente, hoy))
    con.execute("INSERT OR REPLACE INTO producto_costo_hist VALUES (?,?,?,?)", (producto, hoy, costo, fuente))


def registrar_insumo(con, insumo_id, hoy=None):
    r = con.execute("SELECT * FROM insumo WHERE id=?", (insumo_id,)).fetchone()
    con.execute("INSERT OR REPLACE INTO insumo_hist VALUES (?,?,?,?)",
                (insumo_id, (hoy or date.today()).isoformat(), r["cantidad_formato"], r["precio_formato"]))


def _costos_fijos_producto(con, fecha=None):
    if not fecha:
        return {r["producto"]: {"costo": r["costo"], "fuente": r["fuente"]} for r in con.execute("SELECT * FROM producto_costo")}
    out = {}
    for r in con.execute("SELECT * FROM producto_costo_hist WHERE desde <= ? ORDER BY desde", (fecha,)):
        if r["costo"] is None:
            out.pop(r["producto"], None)
        else:
            out[r["producto"]] = {"costo": r["costo"], "fuente": r["fuente"]}
    for r in con.execute("SELECT * FROM producto_costo"):   # costo cargado por primera vez después de esa fecha
        if r["producto"] not in out and not con.execute(
                "SELECT 1 FROM producto_costo_hist WHERE producto=? AND desde <= ?", (r["producto"], fecha)).fetchone():
            out[r["producto"]] = {"costo": r["costo"], "fuente": r["fuente"]}
    return out


def fin_de_mes(periodo):
    return date.fromordinal(date.fromisoformat(mes_rango(periodo)[1]).toordinal() - 1).isoformat()


def load_cfg(con, fecha=None):
    """Configuración leída de la base. Con fecha (AAAA-MM-DD), los costos son los vigentes ese día."""
    par = {r["clave"]: r["valor"] for r in con.execute("SELECT * FROM parametro")}
    costos = _costos_fijos_producto(con, fecha)
    precios = _precios_insumo(con, fecha)
    for r in con.execute("SELECT DISTINCT producto FROM receta"):
        costos[r["producto"]] = {"costo": costo_receta(con, r["producto"], precios=precios), "fuente": "receta"}
    tasa = lambda k: float(par[k]) if par.get(k) not in (None, "") else None
    return {
        "empresa": {"iva": float(par.get("iva", 0.19)), "ppm_tasa": float(par.get("ppm_tasa", 0)),
                    "comision_debito": tasa("comision_debito"), "comision_credito": tasa("comision_credito")},
        "costo_fijo": [dict(r) | {"hasta": r["hasta"] or "9999-12"} for r in con.execute("SELECT * FROM costo_fijo")],
        "proveedores": {r["rut"]: r["categoria"] for r in con.execute("SELECT rut, categoria FROM proveedor")},
        "categorias": CATEGORIAS, "costo_producto": costos,
    }


def _sign(tipo_doc):
    return -1 if tipo_doc == 61 else 1


def ventas_mes(con, periodo, iva):
    bruto, tickets, dias = con.execute(
        "SELECT COALESCE(SUM(bruto),0), COALESCE(SUM(tickets),0), COUNT(DISTINCT fecha) "
        "FROM venta_hora WHERE fecha >= ? AND fecha < ?", mes_rango(periodo)).fetchone()
    neto = round(bruto / (1 + iva))
    return {"bruto": bruto, "neto": neto, "iva_debito": bruto - neto, "tickets": tickets,
            "dias_abiertos": dias, "ticket_promedio": round(bruto / tickets) if tickets else 0}


def compras_por_categoria(con, periodo, cfg):
    prov, out = cfg["proveedores"], defaultdict(int)
    detalle_sin = []
    # Resultados: cada factura cuenta en el mes de su fecha (cuando se compró o se prestó el servicio).
    # El IVA, en cambio, va en el periodo en que se registró en el SII (ver iva_mes).
    for r in con.execute("SELECT * FROM compra WHERE fecha_doc >= ? AND fecha_doc < ?", mes_rango(periodo)):
        cat = r["categoria"] or prov.get(r["rut"]) or "sin_clasificar"
        monto = _sign(r["tipo_doc"]) * (r["neto"] + r["exento"] + r["iva_no_rec"] - r["activo_fijo_neto"])
        out[cat] += monto
        if cat == "sin_clasificar":
            detalle_sin.append((r["razon_social"], r["rut"], monto))
    for r in con.execute("SELECT categoria, SUM(neto) n FROM gasto_manual WHERE fecha >= ? AND fecha < ? "
                         "GROUP BY categoria", mes_rango(periodo)):
        out[r["categoria"]] += r["n"]
    return dict(out), detalle_sin


def costos_fijos(cfg, periodo, reales=()):
    """Gastos fijos del mes. Los marcados como estimados no cuentan si ese mes ya hay gastos reales
    de la misma categoría (por ejemplo, la cuenta de luz mandada al bot)."""
    out = defaultdict(int)
    for c in cfg.get("costo_fijo", []):
        if c["desde"] <= periodo and periodo <= c.get("hasta", "9999-12"):
            if c.get("estimado") and c["categoria"] in reales:
                continue
            out[c["categoria"]] += c["monto"]
    return dict(out)


def iva_mes(con, periodo, cfg):
    v = ventas_mes(con, periodo, cfg["empresa"]["iva"])
    credito = sum(_sign(r["tipo_doc"]) * r["iva_rec"]
                  for r in con.execute("SELECT tipo_doc, iva_rec FROM compra WHERE periodo=?", (periodo,)))
    credito += con.execute("SELECT COALESCE(SUM(iva_rec),0) FROM gasto_manual WHERE fecha >= ? AND fecha < ?",
                           mes_rango(periodo)).fetchone()[0]
    ppm = round(v["neto"] * cfg["empresa"].get("ppm_tasa", 0))
    return {"debito": v["iva_debito"], "credito": credito, "iva_a_pagar": v["iva_debito"] - credito,
            "ppm": ppm, "f29_estimado": max(v["iva_debito"] - credito, 0) + ppm}


def pnl_mes(con, periodo, cfg):
    v = ventas_mes(con, periodo, cfg["empresa"]["iva"])
    cats, sin = compras_por_categoria(con, periodo, cfg)
    reales = {r[0] for r in con.execute("SELECT DISTINCT categoria FROM gasto_manual WHERE fecha >= ? AND fecha < ?",
                                         mes_rango(periodo))}
    for k, m in costos_fijos(cfg, periodo, reales).items():
        cats[k] = cats.get(k, 0) + m
    com = comision_mes(con, periodo, cfg)
    if com["estimada"]:
        cats["comision_tarjetas"] = cats.get("comision_tarjetas", 0) + com["monto"]
    grupos = cfg["categorias"]
    lineas = defaultdict(dict)
    for cat, monto in cats.items():
        lineas[grupos.get(cat, "sin_clasificar")][cat] = monto
    tot = {g: sum(d.values()) for g, d in lineas.items()}
    cv, pers = tot.get("costo_venta", 0), tot.get("personal", 0)
    gop, sc = tot.get("gasto_operacional", 0), tot.get("sin_clasificar", 0)
    n = v["neto"]
    pct = lambda x: round(100 * x / n, 1) if n else 0.0
    margen_bruto = n - cv
    resultado = margen_bruto - pers - gop - sc     # "inversion" (activo fijo) no resta al resultado
    fijos = pers + sum(m for c, m in lineas.get("gasto_operacional", {}).items() if c != "comision_tarjetas")
    mc = 1 - (cv + lineas.get("gasto_operacional", {}).get("comision_tarjetas", 0)) / n if n else 0
    return {
        "periodo": periodo, "ventas": v, "lineas": {g: dict(d) for g, d in lineas.items()},
        "costo_venta": cv, "margen_bruto": margen_bruto, "personal": pers, "gasto_operacional": gop,
        "sin_clasificar": sc, "sin_clasificar_detalle": sin, "inversion": tot.get("inversion", 0),
        "tiene_compras": bool(con.execute("SELECT 1 FROM compra WHERE periodo=? LIMIT 1", (periodo,)).fetchone()), "resultado_operacional": resultado,
        "pct": {"costo_venta": pct(cv), "margen_bruto": pct(margen_bruto), "personal": pct(pers),
                "prime_cost": pct(cv + pers), "gasto_operacional": pct(gop), "resultado": pct(resultado)},
        "equilibrio": {"venta_neta_mes": round(fijos / mc) if mc > 0 else None,
                       "venta_bruta_dia": round(fijos / mc * (1 + cfg["empresa"]["iva"]) / v["dias_abiertos"])
                       if mc > 0 and v["dias_abiertos"] else None},
        "iva": iva_mes(con, periodo, cfg), "comision": com,
    }


TARJETAS = {"debito": "Tarj. Débito", "credito": "Tarj. Crédito"}


def ventas_tarjeta(con, periodo):
    out = {"debito": 0, "credito": 0}
    for r in con.execute("SELECT medio, SUM(bruto) b FROM venta_medio_pago WHERE fecha >= ? AND fecha < ? GROUP BY medio",
                         mes_rango(periodo)):
        m = r["medio"].lower()
        if "tarj" in m or "débito" in m or "crédito" in m or "debito" in m or "credito" in m:
            out["credito" if "cr" in m else "debito"] += r["b"]
    return out


def _facturado_tarjetas(con, periodo, cfg):
    ruts = [rut for rut, c in cfg["proveedores"].items() if c == "comision_tarjetas"]
    if not ruts:
        return 0, 0
    q = ",".join("?" * len(ruts))
    r = con.execute(f"SELECT COALESCE(SUM(CASE WHEN tipo_doc=61 THEN -neto ELSE neto END),0) m, COUNT(*) n FROM compra "
                    f"WHERE rut IN ({q}) AND fecha_doc >= ? AND fecha_doc < ?", (*ruts, *mes_rango(periodo))).fetchone()
    return r["m"], r["n"]


def tasa_tarjetas(con, cfg, antes_de=None, meses=3):
    """Comisión sin IVA como % de lo cobrado con tarjeta. Configurada por tipo de tarjeta, o calculada con las
    facturas de Transbank de los últimos meses completos."""
    e = cfg["empresa"]
    if e.get("comision_debito") is not None and e.get("comision_credito") is not None:
        return {"debito": e["comision_debito"], "credito": e["comision_credito"], "fuente": "configurada"}
    ps = [x[0] for x in con.execute("SELECT DISTINCT substr(fecha,1,7) FROM venta_hora ORDER BY 1")]
    usados, fact, vent = [], 0, 0
    for p in reversed(ps):
        if antes_de and p >= antes_de:
            continue
        m, n = _facturado_tarjetas(con, p, cfg)
        if n:
            t = ventas_tarjeta(con, p)
            fact, vent = fact + m, vent + t["debito"] + t["credito"]
            usados.append(p)
        if len(usados) == meses:
            break
    if not vent:
        return None
    t = 100 * fact / vent
    return {"debito": t, "credito": t, "fuente": "calculada", "meses": sorted(usados)}


def comision_mes(con, periodo, cfg):
    """Comisión de tarjetas del mes: la facturada si ya llegó la factura, si no, estimada con la tasa."""
    ventas = ventas_tarjeta(con, periodo)
    fact, n = _facturado_tarjetas(con, periodo, cfg)
    tasa = tasa_tarjetas(con, cfg, antes_de=None if n else periodo)
    est = None
    if tasa:
        est = round(ventas["debito"] * tasa["debito"] / 100 + ventas["credito"] * tasa["credito"] / 100)
    total = ventas["debito"] + ventas["credito"]
    return {"ventas": ventas, "facturado": fact, "documentos": n, "estimada": not n and bool(est),
            "monto": fact if n else (est or 0), "esperada": est, "tasa": tasa,
            "pct": round(100 * (fact if n else (est or 0)) / total, 2) if total else None}


def ventas_por_hora(con, periodo):
    return [dict(r) for r in con.execute(
        "SELECT hora, SUM(bruto) bruto, SUM(tickets) tickets FROM venta_hora "
        "WHERE fecha >= ? AND fecha < ? GROUP BY hora ORDER BY hora", mes_rango(periodo))]


def medios_pago(con, periodo):
    return [dict(r) for r in con.execute(
        "SELECT medio, SUM(bruto) bruto FROM venta_medio_pago WHERE fecha >= ? AND fecha < ? "
        "GROUP BY medio ORDER BY bruto DESC", mes_rango(periodo))]


def margen_productos(con, desde, hasta, cfg):
    """Margen por producto entre dos periodos AAAA-MM (inclusive).

    clase (ingeniería de menú, solo productos con costo): estrella = popular y rentable,
    caballo = popular y poco rentable, puzzle = rentable y poco popular, perro = ninguna.
    """
    iva, costos = cfg["empresa"]["iva"], cfg.get("costo_producto", {})
    rows = []
    for r in con.execute(
            "SELECT categoria, producto, SUM(cantidad) q, SUM(bruto) b FROM venta_producto "
            "WHERE fecha >= ? AND fecha < ? GROUP BY categoria, producto HAVING b > 0",
            (mes_rango(desde)[0], mes_rango(hasta)[1])):
        neto = round(r["b"] / (1 + iva))
        c = costos.get(r["producto"])
        d = {"categoria": r["categoria"], "producto": r["producto"], "cantidad": r["q"], "neto": neto,
             "precio_neto": round(neto / r["q"]) if r["q"] else 0,
             "precio_bruto": round(r["b"] / r["q"]) if r["q"] else 0, "costo": None, "fuente": None,
             "margen_unit": None, "margen_pct": None, "contribucion": None, "clase": None}
        if c and r["q"]:
            d.update(costo=c["costo"], fuente=c["fuente"], margen_unit=d["precio_neto"] - c["costo"],
                     margen_pct=round(100 * (1 - c["costo"] / d["precio_neto"]), 1) if d["precio_neto"] else None,
                     contribucion=round(neto - c["costo"] * r["q"]))
        rows.append(d)
    con_costo = [d for d in rows if d["costo"] is not None]
    umbral = None
    if con_costo:
        q_med = sum(d["cantidad"] for d in con_costo) / len(con_costo)
        m_med = sum(d["contribucion"] for d in con_costo) / sum(d["cantidad"] for d in con_costo)
        umbral = {"cantidad": round(q_med, 1), "margen": round(m_med)}
        for d in con_costo:
            pop, rent = d["cantidad"] >= q_med, d["margen_unit"] >= m_med
            d["clase"] = {(1, 1): "estrella", (1, 0): "caballo", (0, 1): "puzzle", (0, 0): "perro"}[(pop, rent)]
    total = sum(d["neto"] for d in rows)
    return {"productos": sorted(rows, key=lambda d: -d["neto"]), "umbral": umbral,
            "cobertura_pct": round(100 * sum(d["neto"] for d in con_costo) / total, 1) if total else 0.0}


def periodos(con):
    return [r[0] for r in con.execute("SELECT DISTINCT substr(fecha,1,7) FROM venta_hora ORDER BY 1")]
