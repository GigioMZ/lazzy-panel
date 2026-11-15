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
    for rut, cat in cfg.get("proveedores", {}).items():
        con.execute("INSERT OR IGNORE INTO proveedor(rut, categoria) VALUES (?,?)", (rut, cat))
    if not con.execute("SELECT 1 FROM costo_fijo LIMIT 1").fetchone():
        for c in cfg.get("costo_fijo", []):
            con.execute("INSERT INTO costo_fijo(nombre,categoria,monto,desde,hasta) VALUES (?,?,?,?,?)",
                        (c["nombre"], c["categoria"], c["monto"], c["desde"], c.get("hasta")))
    for prod, c in cfg.get("costo_producto", {}).items():
        con.execute("INSERT OR IGNORE INTO producto_costo(producto,costo,fuente) VALUES (?,?,?)",
                    (prod, c["costo"], c["fuente"]))
    for i in cfg.get("insumo", []):
        con.execute("INSERT OR IGNORE INTO insumo(nombre,unidad,cantidad_formato,precio_formato) VALUES (?,?,?,?)",
                    (i["nombre"], i["unidad"], i["cantidad"], i["precio"]))
    ids = {r["nombre"]: r["id"] for r in con.execute("SELECT id, nombre FROM insumo")}
    for prod, lineas in cfg.get("receta", {}).items():
        if con.execute("SELECT 1 FROM receta WHERE producto=? LIMIT 1", (prod,)).fetchone():
            continue
        for nombre, cant in lineas.items():
            con.execute("INSERT INTO receta VALUES (?,?,?)", (prod, ids[nombre], cant))
    con.commit()


def costo_receta(con, producto):
    r = con.execute("SELECT SUM(r.cantidad * i.precio_formato / i.cantidad_formato) c, COUNT(*) n "
                    "FROM receta r JOIN insumo i ON i.id = r.insumo_id WHERE r.producto=?", (producto,)).fetchone()
    return round(r["c"]) if r["n"] else None


def load_cfg(con):
    """Configuración vigente leída de la base (lo que se edita en el panel)."""
    par = {r["clave"]: r["valor"] for r in con.execute("SELECT * FROM parametro")}
    costos = {r["producto"]: {"costo": r["costo"], "fuente": r["fuente"]}
              for r in con.execute("SELECT * FROM producto_costo")}
    for r in con.execute("SELECT DISTINCT producto FROM receta"):
        costos[r["producto"]] = {"costo": costo_receta(con, r["producto"]), "fuente": "receta"}
    return {
        "empresa": {"iva": float(par.get("iva", 0.19)), "ppm_tasa": float(par.get("ppm_tasa", 0))},
        "costo_fijo": [dict(r) | {"hasta": r["hasta"] or "9999-12"} for r in con.execute("SELECT * FROM costo_fijo")],
        "proveedores": {r["rut"]: r["categoria"] for r in con.execute("SELECT rut, categoria FROM proveedor")},
        "categorias": CATEGORIAS, "costo_producto": costos,
    }


def _sign(tipo_doc):
    return -1 if tipo_doc == 61 else 1


def ventas_mes(con, periodo, iva):
    bruto, tickets, dias = con.execute(
        "SELECT COALESCE(SUM(bruto),0), COALESCE(SUM(tickets),0), COUNT(DISTINCT fecha) "
        "FROM venta_hora WHERE substr(fecha,1,7)=?", (periodo,)).fetchone()
    neto = round(bruto / (1 + iva))
    return {"bruto": bruto, "neto": neto, "iva_debito": bruto - neto, "tickets": tickets,
            "dias_abiertos": dias, "ticket_promedio": round(bruto / tickets) if tickets else 0}


def compras_por_categoria(con, periodo, cfg):
    prov, out = cfg["proveedores"], defaultdict(int)
    detalle_sin = []
    for r in con.execute("SELECT * FROM compra WHERE periodo=?", (periodo,)):
        cat = r["categoria"] or prov.get(r["rut"]) or "sin_clasificar"
        monto = _sign(r["tipo_doc"]) * (r["neto"] + r["exento"] + r["iva_no_rec"] - r["activo_fijo_neto"])
        out[cat] += monto
        if cat == "sin_clasificar":
            detalle_sin.append((r["razon_social"], r["rut"], monto))
    for r in con.execute("SELECT categoria, SUM(neto) n FROM gasto_manual WHERE substr(fecha,1,7)=? "
                         "GROUP BY categoria", (periodo,)):
        out[r["categoria"]] += r["n"]
    return dict(out), detalle_sin


def costos_fijos(cfg, periodo):
    out = defaultdict(int)
    for c in cfg.get("costo_fijo", []):
        if c["desde"] <= periodo and periodo <= c.get("hasta", "9999-12"):
            out[c["categoria"]] += c["monto"]
    return dict(out)


def iva_mes(con, periodo, cfg):
    v = ventas_mes(con, periodo, cfg["empresa"]["iva"])
    credito = sum(_sign(r["tipo_doc"]) * r["iva_rec"]
                  for r in con.execute("SELECT tipo_doc, iva_rec FROM compra WHERE periodo=?", (periodo,)))
    credito += con.execute("SELECT COALESCE(SUM(iva_rec),0) FROM gasto_manual WHERE substr(fecha,1,7)=?",
                           (periodo,)).fetchone()[0]
    ppm = round(v["neto"] * cfg["empresa"].get("ppm_tasa", 0))
    return {"debito": v["iva_debito"], "credito": credito, "iva_a_pagar": v["iva_debito"] - credito,
            "ppm": ppm, "f29_estimado": max(v["iva_debito"] - credito, 0) + ppm}


def pnl_mes(con, periodo, cfg):
    v = ventas_mes(con, periodo, cfg["empresa"]["iva"])
    cats, sin = compras_por_categoria(con, periodo, cfg)
    for k, m in costos_fijos(cfg, periodo).items():
        cats[k] = cats.get(k, 0) + m
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
        "iva": iva_mes(con, periodo, cfg),
    }


def ventas_por_hora(con, periodo):
    return [dict(r) for r in con.execute(
        "SELECT hora, SUM(bruto) bruto, SUM(tickets) tickets FROM venta_hora "
        "WHERE substr(fecha,1,7)=? GROUP BY hora ORDER BY hora", (periodo,))]


def medios_pago(con, periodo):
    return [dict(r) for r in con.execute(
        "SELECT medio, SUM(bruto) bruto FROM venta_medio_pago WHERE substr(fecha,1,7)=? "
        "GROUP BY medio ORDER BY bruto DESC", (periodo,))]


def margen_productos(con, desde, hasta, cfg):
    """Margen por producto entre dos periodos AAAA-MM (inclusive).

    clase (ingeniería de menú, solo productos con costo): estrella = popular y rentable,
    caballo = popular y poco rentable, puzzle = rentable y poco popular, perro = ninguna.
    """
    iva, costos = cfg["empresa"]["iva"], cfg.get("costo_producto", {})
    rows = []
    for r in con.execute(
            "SELECT categoria, producto, SUM(cantidad) q, SUM(bruto) b FROM venta_producto "
            "WHERE substr(fecha,1,7) BETWEEN ? AND ? GROUP BY categoria, producto HAVING b > 0", (desde, hasta)):
        neto = round(r["b"] / (1 + iva))
        c = costos.get(r["producto"])
        d = {"categoria": r["categoria"], "producto": r["producto"], "cantidad": r["q"], "neto": neto,
             "precio_neto": round(neto / r["q"]) if r["q"] else 0, "costo": None, "fuente": None,
             "margen_unit": None, "margen_pct": None, "contribucion": None, "clase": None}
        if c and r["q"]:
            d.update(costo=c["costo"], fuente=c["fuente"], margen_unit=d["precio_neto"] - c["costo"],
                     margen_pct=round(100 * (1 - c["costo"] / d["precio_neto"]), 1) if d["precio_neto"] else None,
                     contribucion=round(neto - c["costo"] * r["q"]))
        rows.append(d)
    con_costo = [d for d in rows if d["costo"] is not None]
    if con_costo:
        q_med = sum(d["cantidad"] for d in con_costo) / len(con_costo)
        m_med = sum(d["contribucion"] for d in con_costo) / sum(d["cantidad"] for d in con_costo)
        for d in con_costo:
            pop, rent = d["cantidad"] >= q_med, d["margen_unit"] >= m_med
            d["clase"] = {(1, 1): "estrella", (1, 0): "caballo", (0, 1): "puzzle", (0, 0): "perro"}[(pop, rent)]
    total = sum(d["neto"] for d in rows)
    return {"productos": sorted(rows, key=lambda d: -d["neto"]),
            "cobertura_pct": round(100 * sum(d["neto"] for d in con_costo) / total, 1) if total else 0.0}


def periodos(con):
    return [r[0] for r in con.execute("SELECT DISTINCT substr(fecha,1,7) FROM venta_hora ORDER BY 1")]
