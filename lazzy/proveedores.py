"""Proveedores: alias, productos comprados con su historial de precio, y vínculo de cada línea de factura
con un producto de la carta o un insumo de receta, para que los costos se actualicen solos con cada factura."""
import difflib
import re
from datetime import date

from . import finance as F
from . import seguimiento as S
from .captura import _clave

UMBRAL_EVENTO = 3.0   # % de cambio de precio de proveedor que se registra en Seguimiento


def nombre(con, rut, razon=None):
    r = con.execute("SELECT alias FROM proveedor_alias WHERE rut=?", (rut,)).fetchone()
    if r:
        return r["alias"]
    if razon:
        return razon
    r2 = con.execute("SELECT razon_social FROM compra WHERE rut=? LIMIT 1", (rut,)).fetchone()
    return (r2 and r2[0]) or rut


def poner_alias(con, rut, alias):
    alias = (alias or "").strip()[:60]
    if alias:
        con.execute("INSERT OR REPLACE INTO proveedor_alias VALUES (?,?)", (rut, alias))
    else:
        con.execute("DELETE FROM proveedor_alias WHERE rut=?", (rut,))
    con.commit()


def lineas(con, rut, clave=None):
    q = ("SELECT l.*, c.fecha_doc, c.tipo_doc AS td FROM factura_linea l JOIN compra c ON c.tipo_doc=l.tipo_doc AND c.rut=l.rut "
         "AND c.folio=l.folio WHERE l.rut=? AND l.tipo_doc<>61 AND l.precio_neto > 0")
    args = [rut]
    if clave:
        q += " AND l.descripcion=?"
        args.append(clave)
    return [dict(r) for r in con.execute(q + " ORDER BY c.fecha_doc, l.folio", args)]


def productos(con, rut):
    """Lo que le compramos a un proveedor: último precio, variación, cantidades y vínculo."""
    por = {}
    for ln in lineas(con, rut):
        x = por.setdefault(ln["descripcion"], {"clave": ln["descripcion"], "texto": ln.get("texto") or ln["descripcion"].capitalize(),
                                                "historial": [], "cantidad": 0, "veces": 0})
        x["historial"].append({"fecha": ln["fecha_doc"], "precio": ln["precio_neto"], "folio": ln["folio"]})
        x["cantidad"] += ln["cantidad"] or 0
        x["veces"] += 1
        x["texto"] = ln.get("texto") or x["texto"]
    vinc = {r["clave"]: dict(r) for r in con.execute("SELECT * FROM vinculo WHERE rut=?", (rut,))}
    out = []
    for x in por.values():
        h = x["historial"]
        x["ultimo"], x["fecha"] = h[-1]["precio"], h[-1]["fecha"]
        x["primero"] = h[0]["precio"]
        anteriores = [y["precio"] for y in h[:-1] if abs(y["precio"] - x["ultimo"]) > 0.5]
        x["anterior"] = anteriores[-1] if anteriores else None
        x["cambio"] = round(100 * (x["ultimo"] / x["anterior"] - 1), 1) if x["anterior"] else None
        x["vinculo"] = vinc.get(x["clave"])
        x["sugerido"] = None if x["vinculo"] else sugerir(con, x["clave"])
        out.append(x)
    return sorted(out, key=lambda x: x["fecha"], reverse=True)


def destinos(con):
    prods = [r[0] for r in con.execute("SELECT DISTINCT producto FROM venta_producto ORDER BY producto")]
    ins = [dict(r) for r in con.execute("SELECT id, nombre, unidad, cantidad_formato FROM insumo ORDER BY nombre")]
    return prods, ins


def sugerir(con, clave):
    prods, ins = destinos(con)
    opciones = {_clave(p): ("producto", p) for p in prods}
    opciones.update({_clave(i["nombre"]): ("insumo", str(i["id"])) for i in ins})
    m = difflib.get_close_matches(clave, list(opciones), n=1, cutoff=0.6)
    if not m:   # coincidencia por palabras: "cheesecake berries individual" ~ "cheesecake berries"
        pal = set(clave.split())
        mejor = max(opciones, key=lambda k: len(pal & set(k.split())) / max(len(set(k.split())), 1), default=None)
        if mejor and len(pal & set(mejor.split())) / max(len(set(mejor.split())), 1) >= 0.99:
            m = [mejor]
    return {"tipo": opciones[m[0]][0], "destino": opciones[m[0]][1]} if m else None


FORMATO = re.compile(r"(\d+(?:[.,]\d+)?)\s*(KG|KGS|KILO|G|GR|GRS|L|LT|LTS|ML|CC)\b", re.I)


def formato(texto):
    """«Q.LAM.400G.GAUDA» → (400, 'g'); «LECHE 1L» → (1000, 'ml'). Si no dice, (1, 'un')."""
    m = FORMATO.search((texto or "").replace(".", " "))
    if not m:
        return 1.0, "un"
    n, u = float(m.group(1).replace(",", ".")), m.group(2).lower()
    if u in ("kg", "kgs", "kilo"):
        return n * 1000, "g"
    if u in ("l", "lt", "lts"):
        return n * 1000, "ml"
    return n, ("ml" if u in ("ml", "cc") else "g")


def crear_insumo(con, rut, clave):
    """Crea un insumo a partir de una línea de factura (nombre, formato y último precio) y lo deja vinculado."""
    ln = lineas(con, rut, clave)
    if not ln:
        return None
    texto = ln[-1].get("texto") or clave
    cant, unidad = formato(texto)
    nombre = " ".join(texto.replace(".", " ").split()).capitalize()[:60]
    iid = con.execute("INSERT INTO insumo(nombre,unidad,cantidad_formato,precio_formato,actualizado) VALUES (?,?,?,?,?)",
                      (nombre, unidad, cant, round(ln[-1]["precio_neto"]), ln[-1]["fecha_doc"])).lastrowid
    con.commit()
    return iid


def vincular(con, rut, clave, tipo, destino, divisor=1.0):
    if tipo not in ("producto", "insumo") or not destino:
        con.execute("DELETE FROM vinculo WHERE rut=? AND clave=?", (rut, clave))
        con.commit()
        return []
    con.execute("INSERT OR REPLACE INTO vinculo VALUES (?,?,?,?,?)", (rut, clave, tipo, destino, max(float(divisor or 1), 0.0001)))
    con.commit()
    return recalcular(con, rut, clave)


def recalcular(con, rut, clave, avisar=False):
    """Pasa los precios de las facturas al costo del producto o insumo vinculado, con su fecha.
    Devuelve los cambios del último precio, para avisar."""
    v = con.execute("SELECT * FROM vinculo WHERE rut=? AND clave=?", (rut, clave)).fetchone()
    if not v:
        return []
    hist = lineas(con, rut, clave)
    if not hist:
        return []
    prov = nombre(con, rut)
    cambios, previo = [], None
    for ln in hist:
        costo = ln["precio_neto"] / v["divisor"]
        if v["tipo"] == "producto":
            con.execute("INSERT OR REPLACE INTO producto_costo_hist VALUES (?,?,?,?)", (v["destino"], ln["fecha_doc"], round(costo), "factura"))
            etiqueta = v["destino"]
        else:
            ins = con.execute("SELECT * FROM insumo WHERE id=?", (int(v["destino"]),)).fetchone()
            if not ins:
                return []
            costo = costo  # precio del formato del insumo
            con.execute("INSERT OR REPLACE INTO insumo_hist VALUES (?,?,?,?)", (ins["id"], ln["fecha_doc"], ins["cantidad_formato"], round(costo)))
            etiqueta = ins["nombre"]
        if previo and abs(costo / previo - 1) * 100 >= UMBRAL_EVENTO:
            datos = {"antes": round(previo), "ahora": round(costo), "proveedor": prov, "rut": rut, "destino_tipo": v["tipo"],
                     "insumo_id": int(v["destino"]) if v["tipo"] == "insumo" else None, "folio": ln["folio"]}
            eid = S.crear(con, ln["fecha_doc"], "costo", etiqueta,
                          f"{prov} {'subió' if costo > previo else 'bajó'} {etiqueta}: de ${round(previo):,} a ${round(costo):,} sin IVA".replace(",", "."), datos)
            if eid and ln is hist[-1]:
                cambios.append(datos | {"etiqueta": etiqueta, "variacion": round(100 * (costo / previo - 1), 1)})
        previo = costo
    ult = hist[-1]
    costo = round(ult["precio_neto"] / v["divisor"])
    if v["tipo"] == "producto":
        act = con.execute("SELECT actualizado FROM producto_costo WHERE producto=?", (v["destino"],)).fetchone()
        if not act or (act["actualizado"] or "") <= ult["fecha_doc"]:
            con.execute("INSERT OR REPLACE INTO producto_costo VALUES (?,?,?,?)", (v["destino"], costo, "factura", ult["fecha_doc"]))
    else:
        act = con.execute("SELECT actualizado FROM insumo WHERE id=?", (int(v["destino"]),)).fetchone()
        if act and (act["actualizado"] or "") <= ult["fecha_doc"]:
            con.execute("UPDATE insumo SET precio_formato=?, actualizado=? WHERE id=?", (costo, ult["fecha_doc"], int(v["destino"])))
    con.commit()
    if avisar and cambios:
        for c in cambios:
            S.notificar(con, f"⚠️ {c['proveedor']} {'subió' if c['variacion'] > 0 else 'bajó'} {c['etiqueta']} {abs(c['variacion']):.0f}%: "
                             f"de ${c['antes']:,} a ${c['ahora']:,} sin IVA. Ya actualicé el costo.".replace(",", "."))
        con.commit()
    return cambios


def recalcular_factura(con, rut, avisar=False):
    cambios = []
    for (clave,) in con.execute("SELECT clave FROM vinculo WHERE rut=?", (rut,)).fetchall():
        cambios += recalcular(con, rut, clave, avisar)
    return cambios


def movimientos(con, dias=120, n=12):
    """Productos de proveedores cuyo precio más cambió (último contra el anterior distinto)."""
    out = []
    for (rut,) in con.execute("SELECT DISTINCT rut FROM factura_linea").fetchall():
        for x in productos(con, rut):
            if x["cambio"] is not None and (date.today() - date.fromisoformat(x["fecha"])).days <= dias:
                out.append(x | {"rut": rut, "proveedor": nombre(con, rut)})
    return sorted(out, key=lambda x: -abs(x["cambio"]))[:n]


def resumen(con):
    """Lista de proveedores con alias, categoría, totales y última compra."""
    return [dict(r) for r in con.execute(
        "SELECT c.rut, MAX(c.razon_social) razon, a.alias, p.categoria cat, COUNT(*) n, MAX(c.fecha_doc) ultima, "
        "SUM(CASE WHEN c.tipo_doc=61 THEN -c.neto ELSE c.neto END) neto, "
        "(SELECT COUNT(DISTINCT descripcion) FROM factura_linea l WHERE l.rut=c.rut) productos "
        "FROM compra c LEFT JOIN proveedor p ON p.rut=c.rut LEFT JOIN proveedor_alias a ON a.rut=c.rut "
        "GROUP BY c.rut ORDER BY (p.categoria IS NOT NULL), neto DESC")]


def compras_por_mes(con, rut, meses=12):
    return [dict(r) for r in con.execute(
        "SELECT substr(fecha_doc,1,7) mes, SUM(CASE WHEN tipo_doc=61 THEN -neto ELSE neto END) neto, COUNT(*) n FROM compra "
        "WHERE rut=? GROUP BY mes ORDER BY mes DESC LIMIT ?", (rut, meses))][::-1]
