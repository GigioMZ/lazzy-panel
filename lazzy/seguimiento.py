"""Seguimiento de decisiones: detecta cambios de precio de la carta, productos nuevos y alzas de proveedores,
y mide su efecto comparando las 4 semanas antes con las 4 semanas después."""
import json
import statistics as st
from datetime import date, datetime, timedelta

from . import finance as F

VENTANA = 28          # días antes y después
MIN_DIAS = 12         # días abiertos después para dar un resultado
TIPOS = {"precio": "Cambio de precio de carta", "nuevo": "Producto u oferta nueva",
         "costo": "Cambio de precio de proveedor", "decision": "Decisión anotada"}


def _existe(con, tipo, producto, fecha, dias=10):
    f = date.fromisoformat(fecha)
    return con.execute("SELECT 1 FROM evento WHERE tipo=? AND COALESCE(producto,'')=COALESCE(?,'') AND fecha BETWEEN ? AND ?",
                       (tipo, producto, (f - timedelta(days=dias)).isoformat(), (f + timedelta(days=dias)).isoformat())).fetchone()


def crear(con, fecha, tipo, producto, titulo, datos=None, origen="auto", nota=None):
    if origen == "auto" and _existe(con, tipo, producto, fecha):
        return None
    cur = con.execute("INSERT INTO evento(fecha,tipo,producto,titulo,datos,origen,nota,creado) VALUES (?,?,?,?,?,?,?,?)",
                      (fecha, tipo, producto, titulo, json.dumps(datos or {}, ensure_ascii=False), origen, nota,
                       datetime.now().isoformat(timespec="seconds")))
    return cur.lastrowid


def notificar(con, texto):
    con.execute("INSERT INTO notificacion(creado, texto) VALUES (?,?)", (datetime.now().isoformat(timespec="seconds"), texto))


def _n1(x):
    return f"{x:.1f}".replace(".", ",")


def _redondo(x):
    return int(round(x / 10.0) * 10)


def detectar(con, avisar_desde=None):
    """Busca en las ventas cambios de precio sostenidos y productos nuevos. Devuelve los eventos creados."""
    nuevos = []
    ini = con.execute("SELECT MIN(fecha) FROM venta_producto").fetchone()[0]
    if not ini:
        return nuevos
    for (prod,) in con.execute("SELECT producto FROM venta_producto GROUP BY producto HAVING SUM(cantidad) >= 15").fetchall():
        if any(x in prod.lower() for x in ("genérico", "generico", "varios", "propina")):
            continue
        filas = [(r["fecha"], r["b"] / r["q"]) for r in con.execute(
            "SELECT fecha, SUM(cantidad) q, SUM(bruto) b FROM venta_producto WHERE producto=? GROUP BY fecha "
            "HAVING q > 0 AND b > 0 ORDER BY fecha", (prod,))]
        ps = [x[1] for x in filas]
        primera = filas[0][0] if filas else None
        if primera and (date.fromisoformat(primera) - date.fromisoformat(ini)).days > 21:
            eid = crear(con, primera, "nuevo", prod, f"Empezó a venderse {prod}", {"precio": _redondo(st.median(ps[:7]))})
            if eid:
                nuevos.append(eid)
        i = 7
        while i <= len(ps) - 4:            # cambio sostenido: mediana de 7 días antes vs después, con 5 de 7 días estables
            a, b = st.median(ps[i - 7:i]), st.median(ps[i:i + 7])
            if a and abs(b / a - 1) >= 0.04:
                sig = ps[i:i + 7]
                if sum(1 for x in sig if abs(x / b - 1) <= 0.02) >= min(5, len(sig) - 1) and abs(ps[i] / b - 1) <= 0.02:
                    antes, ahora = _redondo(a), _redondo(b)
                    eid = crear(con, filas[i][0], "precio", prod,
                                f"{prod}: de ${antes:,} a ${ahora:,}".replace(",", "."), {"antes": antes, "ahora": ahora})
                    if eid:
                        nuevos.append(eid)
                    i += 7
                    continue
            i += 1
    con.commit()
    if nuevos and avisar_desde:
        recientes = [dict(r) for r in con.execute(
            f"SELECT * FROM evento WHERE id IN ({','.join('?' * len(nuevos))}) AND fecha >= ?", (*nuevos, avisar_desde))]
        if recientes:
            lineas = [f"• {r['titulo']} ({r['fecha'][8:10]}/{r['fecha'][5:7]})" for r in recientes]
            notificar(con, "Detecté cambios en la carta y empecé a seguirlos:\n" + "\n".join(lineas)
                      + "\n\nEl efecto se ve en https://panel.lazzycoffee.cl/seguimiento")
            con.commit()
    return nuevos


def asegurar(con):
    """La primera vez revisa todo el historial de ventas (sin avisar por el bot)."""
    if not con.execute("SELECT 1 FROM parametro WHERE clave='seg_detectado'").fetchone():
        detectar(con)
        con.execute("INSERT OR REPLACE INTO parametro VALUES ('seg_detectado', ?)", (date.today().isoformat(),))
        con.commit()


def _dias_abiertos(con, a, b):
    return con.execute("SELECT COUNT(DISTINCT fecha) FROM venta_hora WHERE fecha BETWEEN ? AND ? AND bruto > 0",
                       (a.isoformat(), b.isoformat())).fetchone()[0]


def _producto(con, prods, a, b, costos, iva):
    q = ",".join("?" * len(prods))
    r = con.execute(f"SELECT producto, SUM(cantidad) q, SUM(bruto) b FROM venta_producto WHERE producto IN ({q}) "
                    f"AND fecha BETWEEN ? AND ? GROUP BY producto", (*prods, a.isoformat(), b.isoformat())).fetchall()
    uds = sum(x["q"] for x in r)
    neto = sum(x["b"] for x in r) / (1 + iva)
    costo = sum(x["q"] * (costos.get(x["producto"], {}).get("costo") or 0) for x in r)
    sin_costo = any(costos.get(x["producto"], {}).get("costo") is None for x in r)
    return uds, neto, neto - costo, sin_costo


def ventanas(fecha, ultimo):
    f = date.fromisoformat(fecha)
    return (f - timedelta(days=VENTANA), f - timedelta(days=1)), (f, min(f + timedelta(days=VENTANA - 1), ultimo))


def medir(con, ev, ultimo, cfg=None):
    """Efecto de un evento (o grupo de eventos): por día abierto, antes contra después."""
    cfg = cfg or F.load_cfg(con)
    iva, costos = cfg["empresa"]["iva"], cfg["costo_producto"]
    prods = ev.get("productos") or ([ev["producto"]] if ev.get("producto") else [])
    (a0, a1), (b0, b1) = ventanas(ev["fecha"], ultimo)
    da, db = _dias_abiertos(con, a0, a1), _dias_abiertos(con, b0, b1)
    out = {"dias_antes": da, "dias_despues": db, "midiendo": db < MIN_DIAS, "ventana": [a0, a1, b0, b1]}
    tot = lambda x, y: con.execute("SELECT COALESCE(SUM(bruto),0) b, COALESCE(SUM(tickets),0) t FROM venta_hora "
                                   "WHERE fecha BETWEEN ? AND ?", (x.isoformat(), y.isoformat())).fetchone()
    ta, tb = tot(a0, a1), tot(b0, b1)
    if da and db:
        out["local"] = {"antes": ta["b"] / da, "despues": tb["b"] / db,
                        "cambio": round(100 * ((tb["b"] / db) / (ta["b"] / da) - 1), 1) if ta["b"] else None}
    if prods and da and db and ev.get("tipo") != "nuevo" and len(prods) > 1:
        # en un grupo solo se comparan los productos que ya se vendían antes del cambio
        prods = [x for x in prods if _producto(con, [x], a0, a1, costos, iva)[0] >= 3] or prods
    if prods and da and db:
        ua, na, ca, s1 = _producto(con, prods, a0, a1, costos, iva)
        ub, nb, cb, s2 = _producto(con, prods, b0, b1, costos, iva)
        pa, pb = (na / ua if ua else None), (nb / ub if ub else None)
        out["prod"] = {"uds_antes": ua / da, "uds_despues": ub / db, "precio_antes": pa, "precio_despues": pb,
                       "contrib_antes": ca / da, "contrib_despues": cb / db, "sin_costo": s1 or s2,
                       "venta_antes": na / da, "venta_despues": nb / db,
                       "cambio_uds": round(100 * ((ub / db) / (ua / da) - 1), 1) if ua else None,
                       "cambio_precio": round(100 * (pb / pa - 1), 1) if pa and pb else None}
        p = out["prod"]
        if len(prods) > 1:      # en grupos, el cambio de precio es el promedio de cada producto ponderado por sus ventas
            pond = tot_u = 0.0
            for x in prods:
                u0, n0, _, _ = _producto(con, [x], a0, a1, costos, iva)
                u1, n1, _, _ = _producto(con, [x], b0, b1, costos, iva)
                if u0 and u1:
                    pond += u0 * ((n1 / u1) / (n0 / u0) - 1)
                    tot_u += u0
            p["cambio_precio"] = round(100 * pond / tot_u, 1) if tot_u else None
        p["pocos_datos"] = ub < 20 if ev.get("tipo") == "nuevo" else (ua < 20 or ub < 20)
        p["sin_base"] = ev.get("tipo") != "nuevo" and ua < 3
        if p["cambio_uds"] is not None and p["cambio_precio"]:
            p["elasticidad"] = round(p["cambio_uds"] / p["cambio_precio"], 2)
        p["efecto_mes"] = round((p["contrib_despues"] - p["contrib_antes"]) * 26)     # ~26 días abiertos al mes
    return out


def veredicto(ev, m):
    if m["midiendo"]:
        return ("neutro", f"Midiendo: van {m['dias_despues']} de {MIN_DIAS} días abiertos para tener un resultado.")
    p = m.get("prod")
    if not p:
        if m.get("local") and m["local"]["cambio"] is not None:
            c = m["local"]["cambio"]
            return ("ok" if c >= 0 else "bad", f"Las ventas del local por día {'subieron' if c >= 0 else 'bajaron'} {abs(c):.0f}% contra las 4 semanas anteriores.")
        return ("neutro", "Sin datos suficientes para medir.")
    e = p["efecto_mes"]
    clp = lambda x: ("-" if x < 0 else "") + f"${abs(x):,.0f}".replace(",", ".")
    contexto = ""
    if m.get("local") and m["local"]["cambio"] is not None:
        c = round(m["local"]["cambio"])
        contexto = " En el mismo tiempo, la venta total del local se mantuvo." if c == 0 else \
            f" En el mismo tiempo, la venta total del local {'subió' if c > 0 else 'bajó'} {abs(c)}%."
    duda = " Ojo: son pocas ventas, el resultado puede ser casualidad." if p.get("pocos_datos") else ""
    if ev["tipo"] == "nuevo":
        if not p["uds_despues"]:
            return ("neutro", "No se vendió en las 4 semanas siguientes al lanzamiento." + duda)
        u = p["uds_despues"]
        ritmo = f"{_n1(u)} por día" if u >= 0.5 else f"{_n1(u * 6)} por semana"
        if p["sin_costo"]:
            return ("neutro", f"En sus primeras 4 semanas vendió {ritmo} ({clp(round(p['venta_despues'] * 26))} al mes sin IVA). "
                              "Falta cargarle el costo para saber cuánto deja." + duda)
        return ("ok" if p["contrib_despues"] > 0 else "bad",
                f"En sus primeras 4 semanas vendió {ritmo} y dejó {clp(round(p['contrib_despues'] * 26))} al mes." + duda)
    if p.get("sin_base"):
        u = p["uds_despues"]
        return ("neutro", "En las 4 semanas anteriores casi no se vendía (o no estaba en la carta), así que no hay con qué comparar. "
                          f"Desde el cambio vende {_n1(u)} por día." if u >= 0.5 else
                          "En las 4 semanas anteriores casi no se vendía, así que no hay con qué comparar.")
    cp, cu = p.get("cambio_precio"), p.get("cambio_uds")
    if cp and cu is not None and ev["tipo"] in ("precio", "grupo"):
        if cp > 0:
            lectura = ("se siguen vendiendo igual o más pese a la subida" if cu >= -2 else
                       f"se venden {abs(cu):.0f}% menos, menos de lo que subió el precio ({cp:.0f}%): la subida conviene" if cu > -cp else
                       f"se venden {abs(cu):.0f}% menos, más de lo que subió el precio ({cp:.0f}%)"
                       + (", pero cada unidad deja tanto más que el total sube" if e >= 0 and not p["sin_costo"] else ""))
        else:
            lectura = (f"la baja de precio subió las ventas {cu:.0f}%" if cu > 0 else "la baja de precio no aumentó las ventas")
        if p["sin_costo"]:
            v = round((p["venta_despues"] - p["venta_antes"]) * 26)
            return ("ok" if v >= 0 else "bad", f"Vendes {clp(abs(v))} {'más' if v >= 0 else 'menos'} al mes sin IVA: {lectura}. "
                                               f"Falta cargar su costo para saber cuánto deja.{contexto}{duda}")
        return ("ok" if e >= 0 else "bad", f"{'Ganas' if e >= 0 else 'Pierdes'} {clp(abs(e))} al mes: {lectura}.{contexto}{duda}")
    return ("ok" if e >= 0 else "bad", f"Efecto en lo que dejan estos productos: {clp(e)} al mes.{contexto}{duda}")


def agrupar(eventos, dias=12):
    """Cambios de precio de carta cercanos en el tiempo se ven como una sola decisión."""
    out, grupo = [], None
    for ev in sorted(eventos, key=lambda e: e["fecha"]):
        if ev["tipo"] == "precio" and grupo and (date.fromisoformat(ev["fecha"]) - date.fromisoformat(grupo["fin"])).days <= dias:
            grupo["eventos"].append(ev)
            grupo["fin"] = ev["fecha"]
            continue
        if ev["tipo"] == "precio":
            grupo = {"tipo": "precio", "fecha": ev["fecha"], "fin": ev["fecha"], "eventos": [ev]}
            out.append(grupo)
        else:
            grupo = None
            out.append({"tipo": ev["tipo"], "fecha": ev["fecha"], "fin": ev["fecha"], "eventos": [ev]})
    for g in out:
        if g["tipo"] == "precio" and len(g["eventos"]) > 1:
            g["tipo_med"] = "grupo"
    return out


def lista(con, cfg, tipo=None, producto=None):
    ultimo = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0]
    if not ultimo:
        return []
    ultimo = date.fromisoformat(ultimo)
    q, args = "SELECT * FROM evento WHERE estado='activo'", []
    if producto:
        q += " AND producto=?"
        args.append(producto)
    evs = [dict(r) | {"datos": json.loads(r["datos"] or "{}")} for r in con.execute(q + " ORDER BY fecha", args)]
    grupos = agrupar([e for e in evs if not tipo or e["tipo"] == tipo])
    for g in grupos:
        base = {"fecha": g["fecha"], "tipo": g.get("tipo_med", g["tipo"])}
        if g["tipo"] in ("precio", "nuevo"):
            base["productos"] = [e["producto"] for e in g["eventos"]]
        elif g["tipo"] == "decision" and g["eventos"][0].get("producto"):
            base["producto"] = g["eventos"][0]["producto"]
        g["medida"] = medir(con, base, ultimo, cfg) if g["tipo"] != "costo" else None
        if g["tipo"] == "costo":
            g["medida"] = impacto_costo(con, g["eventos"][0], cfg)
            mes = g["medida"]["mes"]
            g["veredicto"] = (("neutro", "Todavía no hay ventas de los productos que usan esto para calcular el efecto.") if not mes else
                              ("bad" if mes > 0 else "ok",
                               ("Te cuesta" if mes > 0 else "Te ahorra") + f" ${abs(mes):,.0f} más al mes con lo que vendes hoy.".replace(",", ".")))
        else:
            g["veredicto"] = veredicto({"tipo": base["tipo"]}, g["medida"])
        if g["tipo"] == "precio" and len(g["eventos"]) > 1:
            for e in g["eventos"]:
                e["medida"] = medir(con, {"fecha": e["fecha"], "productos": [e["producto"]], "tipo": "precio"}, ultimo, cfg)
        pm = (g.get("medida") or {}).get("prod") or {}
        g["debil"] = g["tipo"] in ("precio", "nuevo") and not g["medida"]["midiendo"] and bool(pm.get("pocos_datos") or pm.get("sin_base") or not pm)
        prods = base.get("productos") or ([base["producto"]] if base.get("producto") else [])
        g["semanas"] = semanas_alrededor(con, g["fecha"], prods, ultimo) if g["tipo"] != "costo" else []
    return sorted(grupos, key=lambda g: g["fecha"], reverse=True)


def impacto_costo(con, ev, cfg):
    """Cuánto cambia al mes lo que cuesta lo vendido por un cambio de costo de proveedor."""
    d = ev["datos"]
    delta = (d.get("ahora") or 0) - (d.get("antes") or 0)
    ult = con.execute("SELECT MAX(fecha) FROM venta_producto").fetchone()[0]
    hasta = date.fromisoformat(ult) if ult else date.today()
    desde = hasta - timedelta(days=89)
    if d.get("destino_tipo") == "insumo":
        filas = con.execute("SELECT r.producto, r.cantidad, i.cantidad_formato FROM receta r JOIN insumo i ON i.id=r.insumo_id "
                            "WHERE i.id=?", (d.get("insumo_id"),)).fetchall()
        afect = {r["producto"]: r["cantidad"] / r["cantidad_formato"] for r in filas}
    else:
        afect = {ev["producto"]: 1.0}
    mes = 0.0
    det = []
    for prod, frac in afect.items():
        u = con.execute("SELECT COALESCE(SUM(cantidad),0) FROM venta_producto WHERE producto=? AND fecha BETWEEN ? AND ?",
                        (prod, desde.isoformat(), hasta.isoformat())).fetchone()[0] / 3
        mes += u * delta * frac
        det.append({"producto": prod, "uds_mes": round(u), "delta_unidad": round(delta * frac)})
    return {"mes": round(mes), "afectados": sorted(det, key=lambda x: -x["uds_mes"])}


def semanas_alrededor(con, fecha, prods, ultimo, n=8):
    """Unidades (o venta del local) por semana, n semanas antes y hasta n después."""
    f = date.fromisoformat(fecha)
    lunes = f - timedelta(days=f.weekday())
    out = []
    for i in range(-n, n):
        a = lunes + timedelta(days=7 * i)
        if a > ultimo:
            break
        b = a + timedelta(days=6)
        if prods:
            q = ",".join("?" * len(prods))
            v = con.execute(f"SELECT COALESCE(SUM(cantidad),0) FROM venta_producto WHERE producto IN ({q}) AND fecha BETWEEN ? AND ?",
                            (*prods, a.isoformat(), b.isoformat())).fetchone()[0]
        else:
            v = con.execute("SELECT COALESCE(SUM(bruto),0) FROM venta_hora WHERE fecha BETWEEN ? AND ?",
                            (a.isoformat(), b.isoformat())).fetchone()[0]
        out.append({"semana": f"{a.day}/{a.month}", "valor": round(v), "despues": i >= 0})
    return out
