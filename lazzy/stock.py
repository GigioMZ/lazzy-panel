"""Stock de productos que se compran hechos (pastelería, sándwiches). Parte de un conteo (el stock de FUDO),
suma lo que llega por facturas vinculadas y resta lo vendido. Al subir un conteo nuevo, la diferencia con lo
esperado es merma (o entradas sin factura), y el stock vuelve a partir del conteo."""
from datetime import date, datetime, timedelta

import openpyxl

from . import finance as F


def es_stock(ruta):
    try:
        ws = openpyxl.load_workbook(ruta, read_only=True)
        if "Productos" not in ws.sheetnames:
            return False
        cab = next(ws["Productos"].iter_rows(values_only=True), ())
        return "Nombre" in cab and "Stock" in cab
    except Exception:
        return False


def leer(ruta):
    ws = openpyxl.load_workbook(ruta, read_only=True)["Productos"]
    filas = list(ws.iter_rows(values_only=True))
    cab = list(filas[0])
    i_n, i_s = cab.index("Nombre"), cab.index("Stock")
    i_m = cab.index("Stock Mínimo") if "Stock Mínimo" in cab else None
    out = []
    for r in filas[1:]:
        if r[i_n] and isinstance(r[i_s], (int, float)):
            out.append({"producto": str(r[i_n]).strip(), "cantidad": float(r[i_s]),
                        "minimo": float(r[i_m]) if i_m is not None and isinstance(r[i_m], (int, float)) else None})
    return out


def corte_ahora(ahora=None):
    """Hasta qué día de ventas refleja un conteo subido ahora: de tarde, incluye el día; de mañana, hasta ayer."""
    ahora = ahora or datetime.now()
    return (ahora.date() if ahora.hour >= 15 else ahora.date() - timedelta(days=1)).isoformat()


def guardar(con, filas, fecha=None, corte=None):
    fecha = fecha or date.today().isoformat()
    corte = corte or corte_ahora()
    ahora = datetime.now().isoformat(timespec="microseconds")
    con.execute("DELETE FROM stock_conteo WHERE fecha=?", (fecha,))
    con.executemany("INSERT INTO stock_conteo(fecha,producto,cantidad,minimo,corte,creado) VALUES (?,?,?,?,?,?)",
                    [(fecha, f["producto"], f["cantidad"], f.get("minimo"), corte, ahora) for f in filas])
    con.commit()
    return {"productos": len(filas), "fecha": fecha, "corte": corte}


def _movimientos(con, productos):
    """Entradas (facturas vinculadas y llegadas anotadas, también merma anotada con signo menos) y salidas (ventas, también
    dentro de combos) por producto. Cada movimiento es (fecha, cantidad, creado): «creado» permite ordenar contra un
    conteo del mismo día lo que se anotó después de subirlo."""
    ent, sal = {}, {}
    for r in con.execute("SELECT producto p, fecha f, cantidad q, creado c FROM stock_entrada"):
        ent.setdefault(r["p"], []).append((r["f"], r["q"], r["c"]))
    for r in con.execute("SELECT v.destino p, c.fecha_doc f, c.creado c, SUM(l.cantidad * v.divisor) q FROM vinculo v "
                         "JOIN factura_linea l ON l.rut=v.rut AND l.descripcion=v.clave "
                         "JOIN compra c ON c.tipo_doc=l.tipo_doc AND c.rut=l.rut AND c.folio=l.folio "
                         "WHERE v.tipo='producto' AND l.tipo_doc<>61 GROUP BY 1, 2, 3"):
        ent.setdefault(r["p"], []).append((r["f"], r["q"] or 0, r["c"]))
    nombres = [r[0] for r in con.execute("SELECT DISTINCT producto FROM venta_producto")]
    partes = {n: [x for x in F.componentes(n, nombres) if x] for n in nombres if "+" in n}
    for r in con.execute("SELECT producto p, fecha f, SUM(cantidad) q FROM venta_producto GROUP BY 1, 2"):
        for prod in ([r["p"]] if r["p"] not in partes else partes[r["p"]]):
            if prod in productos:
                sal.setdefault(prod, []).append((r["f"], r["q"], None))
    return ent, sal


def _despues(mov, conteo):
    """¿El movimiento es posterior al conteo? Los del mismo día del corte cuentan después solo si se anotaron después."""
    f, _, creado = mov
    if conteo is None:
        return True
    if f != conteo["corte"]:
        return f > conteo["corte"]
    return bool(creado and conteo.get("creado") and creado >= conteo["creado"])


def _suma(movs, a, b=None):
    """Movimientos posteriores al conteo a y no posteriores al conteo b (b puede ser una fecha AAAA-MM-DD)."""
    if isinstance(b, str):
        b = {"corte": b, "creado": "9999"}
    return sum(m[1] for m in movs if _despues(m, a) and (b is None or not _despues(m, b)))


def _conteos(con):
    por = {}
    for r in con.execute("SELECT * FROM stock_conteo ORDER BY fecha"):
        por.setdefault(r["producto"], []).append(dict(r))
    return por


def estado(con, cfg, hoy=None):
    """Stock esperado hoy de cada producto contado, con su valor al costo."""
    hoy = (hoy or date.today()).isoformat()
    por = _conteos(con)
    ent, sal = _movimientos(con, set(por))
    costos = cfg["costo_producto"]
    out = []
    for p, cs in por.items():
        u = cs[-1]
        e, s = _suma(ent.get(p, []), u, hoy), _suma(sal.get(p, []), u, hoy)
        esperado = u["cantidad"] + e - s
        c = (costos.get(p) or {}).get("costo")
        out.append({"producto": p, "conteo": u["cantidad"], "fecha": u["fecha"], "entradas": e, "ventas": s, "esperado": esperado,
                    "minimo": u["minimo"], "bajo": u["minimo"] is not None and esperado <= u["minimo"], "costo": c,
                    "valor": round(max(esperado, 0) * c) if c is not None else None})
    return sorted(out, key=lambda x: x["producto"])


def ajustes(con, cfg):
    """Cada vez que se subió un conteo: lo esperado contra lo contado, producto por producto."""
    por = _conteos(con)
    ent, sal = _movimientos(con, set(por))
    costos = cfg["costo_producto"]
    out = []
    for p, cs in por.items():
        for a, b in zip(cs, cs[1:]):
            e, s = _suma(ent.get(p, []), a, b), _suma(sal.get(p, []), a, b)
            esperado = a["cantidad"] + e - s
            dif = b["cantidad"] - esperado
            c = (costos.get(p) or {}).get("costo")
            out.append({"producto": p, "fecha": b["fecha"], "desde": a["fecha"], "antes": a["cantidad"], "entradas": e, "ventas": s,
                        "esperado": esperado, "contado": b["cantidad"], "dif": dif, "costo": c,
                        "valor": round(dif * c) if c is not None else None})
    return sorted(out, key=lambda x: (x["fecha"], x["producto"]), reverse=True)


def merma_mes(con, periodo, cfg):
    """Valor de lo que faltó en los conteos de un mes (neto por producto; un sobrante no es ganancia)."""
    if not con.execute("SELECT 1 FROM stock_conteo WHERE fecha LIKE ? LIMIT 1", (periodo + "%",)).fetchone():
        return 0
    por = {}
    for a in ajustes(con, cfg):
        if a["fecha"][:7] == periodo and a["valor"] is not None:
            por[a["producto"]] = por.get(a["producto"], 0) + a["valor"]
    return -sum(v for v in por.values() if v < 0)


def valor(con, cfg, fecha):
    """Valor al costo del stock al cierre de un día. None si todavía no había ningún conteo."""
    por = _conteos(con)
    ent, sal = _movimientos(con, set(por))
    costos = cfg["costo_producto"]
    total, hay = 0, False
    for p, cs in por.items():
        prev = [c for c in cs if c["corte"] <= fecha]
        if not prev:
            continue
        hay = True
        u = prev[-1]
        q = u["cantidad"] + _suma(ent.get(p, []), u, fecha) - _suma(sal.get(p, []), u, fecha)
        total += max(q, 0) * ((costos.get(p) or {}).get("costo") or 0)
    return round(total) if hay else None


def productos(con):
    return [r[0] for r in con.execute("SELECT DISTINCT producto FROM stock_conteo ORDER BY 1")]


def buscar(con, texto):
    """Producto del stock al que se refiere un texto («ciabatta jamon» → Ciabatta Jamon queso)."""
    t = F._norm(texto)
    ps = {F._norm(p): p for p in productos(con)}
    if t in ps:
        return ps[t]
    pal = set(t.split())
    cands = [(len(pal & set(k.split())) / max(len(pal), 1), -len(k), p) for k, p in ps.items() if pal & set(k.split())]
    cands = [c for c in cands if c[0] >= 0.99] or [c for c in cands if c[0] >= 0.6]
    if not cands or len([k for k in ps if pal <= set(k.split())]) > 1:
        return None     # «cheesecake» puede ser berries, prestigio o choconaranja: mejor preguntar que adivinar
    return max(cands)[2]


def parecidos(con, texto):
    """Productos que calzan con un nombre ambiguo («cheesecake» → los tres cheesecakes)."""
    pal = set(F._norm(texto).split())
    return [p for p in productos(con) if pal and pal <= set(F._norm(p).split())]


def registrar_llegada(con, producto, cantidad, fecha=None, nota=None, origen="panel"):
    """Llegada (cantidad positiva) o merma anotada (negativa, origen que empieza con «merma»). Devuelve el id."""
    cur = con.execute("INSERT INTO stock_entrada(fecha,producto,cantidad,nota,origen,creado) VALUES (?,?,?,?,?,?)",
                      (fecha or date.today().isoformat(), producto, float(cantidad), nota, origen, datetime.now().isoformat(timespec="microseconds")))
    con.commit()
    return cur.lastrowid


def ajustar(con, producto, cantidad, fecha=None, corte=None):
    """Conteo de un solo producto («quedan 12 brownies»). Devuelve lo que esperaba el panel antes del ajuste."""
    from . import finance as F
    esp = next((x["esperado"] for x in estado(con, F.load_cfg(con)) if x["producto"] == producto), None)
    fecha = fecha or date.today().isoformat()
    minimo = con.execute("SELECT minimo FROM stock_conteo WHERE producto=? ORDER BY fecha DESC LIMIT 1", (producto,)).fetchone()
    con.execute("DELETE FROM stock_conteo WHERE fecha=? AND producto=?", (fecha, producto))
    con.execute("INSERT INTO stock_conteo(fecha,producto,cantidad,minimo,corte,creado) VALUES (?,?,?,?,?,?)",
                (fecha, producto, float(cantidad), minimo[0] if minimo else None, corte or corte_ahora(),
                 datetime.now().isoformat(timespec="microseconds")))
    con.commit()
    return esp


def merma_anotada(con, periodo, cfg):
    """Valor de lo anotado como merma (se botó, se regaló) en el mes."""
    costos = cfg["costo_producto"]
    return round(sum(-r["cantidad"] * ((costos.get(r["producto"]) or {}).get("costo") or 0) for r in con.execute(
        "SELECT producto, cantidad FROM stock_entrada WHERE fecha LIKE ? AND origen LIKE 'merma%'", (periodo + "%",))))


def bajo_minimo(con, cfg, hoy=None):
    return [x for x in estado(con, cfg, hoy) if x["bajo"]]


def de_factura(con, rut, folio, cfg, hoy=None):
    """Productos del stock que llegaron en una factura, con lo que debería quedar ahora."""
    llegados = {r["destino"]: r["q"] for r in con.execute(
        "SELECT v.destino, SUM(l.cantidad * v.divisor) q FROM factura_linea l JOIN vinculo v ON v.rut=l.rut AND v.clave=l.descripcion "
        "WHERE l.rut=? AND l.folio=? AND v.tipo='producto' GROUP BY v.destino", (rut, folio))}
    est = {x["producto"]: x for x in estado(con, cfg, hoy)}
    return [(p, q, est[p]["esperado"]) for p, q in llegados.items() if p in est]


_LLEGADA = r"^\s*(?:llegaron|lleg[oó]|entraron|recibimos|trajimos|ingresaron)\s+(.+)$"
_AJUSTE = r"^\s*(?:quedan|queda|hay|tenemos|stock(?: de)?)\s+(.+)$"
_MERMA = r"^\s*(?:merma|se botaron|se bot[oó]|botamos|se venci[oó]|se vencieron|se cay[oó]|se cayeron|regalamos|se regal[oó])\s+(?:de\s+)?(.+)$"


def _items(con, texto):
    import re
    out, faltan = [], []
    texto, _, motivo = texto.partition(" por ")
    for parte in re.split(r",|;|\sy\s|\n", texto):
        m2 = re.match(r"\s*(\d+(?:[.,]\d+)?)\s+(?:de\s+)?(.+?)\s*$", parte) or re.match(r"\s*(.+?)\s+(\d+(?:[.,]\d+)?)\s*$", parte)
        if not m2:
            continue
        n, nombre = (m2.group(1), m2.group(2)) if m2.group(1)[0].isdigit() else (m2.group(2), m2.group(1))
        p = buscar(con, nombre)
        if p:
            out.append((p, float(n.replace(",", "."))))
        else:
            ops = parecidos(con, nombre)
            faltan.append(nombre.strip() + (f" (¿{', '.join(ops[:-1])} o {ops[-1]}?)" if len(ops) > 1 else ""))
    return {"items": out, "faltan": faltan, "motivo": motivo.strip()[:80] or None}


def parsear_stock(con, texto):
    """Mensajes de stock: llegada, ajuste o merma. None si el texto no es de stock."""
    import re
    for tipo, rx in (("llegada", _LLEGADA), ("ajuste", _AJUSTE), ("merma", _MERMA)):
        m = re.match(rx, texto.strip(), re.I)
        if m:
            r = _items(con, m.group(1))
            if tipo != "llegada" and not r["items"] and not r["faltan"]:
                return None
            return dict(r, tipo=tipo)
    return None


def parsear_llegada(con, texto):
    """«llegaron 10 ciabatta jamon queso y 6 salame» → [(producto, 10), (producto, 6)]. None si no es una llegada."""
    import re
    m = re.match(_LLEGADA, texto.strip(), re.I)
    if not m:
        return None
    out, faltan = [], []
    for parte in re.split(r",|;|\sy\s|\n", m.group(1)):
        m2 = re.match(r"\s*(\d+(?:[.,]\d+)?)\s+(?:de\s+)?(.+?)\s*$", parte)
        if not m2:
            continue
        p = buscar(con, m2.group(2))
        (out.append((p, float(m2.group(1).replace(",", ".")))) if p else faltan.append(m2.group(2).strip()))
    return {"items": out, "faltan": faltan}


def sugerencias(con):
    """Para productos del stock sin factura vinculada: la línea de factura que más se le parece."""
    from .proveedores import nombre as nombre_prov
    lineas = con.execute("SELECT l.rut, l.descripcion, MAX(COALESCE(l.texto, l.descripcion)) texto FROM factura_linea l "
                         "WHERE NOT EXISTS (SELECT 1 FROM vinculo v WHERE v.rut=l.rut AND v.clave=l.descripcion) GROUP BY 1, 2").fetchall()
    out = []
    for p in productos(con):
        if con.execute("SELECT 1 FROM vinculo WHERE tipo='producto' AND destino=?", (p,)).fetchone():
            continue
        pal = set(F._norm(p).split())
        mejor = None
        for ln in lineas:
            k = set(ln["descripcion"].split())
            score = len(pal & k) / len(pal) if pal else 0
            if score >= 0.99 and (not mejor or len(k) < len(mejor[1]["descripcion"].split())):
                mejor = (score, ln)
        if mejor:
            ln = mejor[1]
            out.append({"producto": p, "rut": ln["rut"], "clave": ln["descripcion"], "texto": ln["texto"], "proveedor": nombre_prov(con, ln["rut"])})
    return out


def vista_previa(con, cfg, filas):
    """Qué va a cambiar al guardar un conteo: diferencias contra lo esperado."""
    esp = {x["producto"]: x for x in estado(con, cfg)}
    difs = []
    for f in filas:
        e = esp.get(f["producto"])
        if e and abs(f["cantidad"] - e["esperado"]) >= 1:
            difs.append((f["producto"], e["esperado"], f["cantidad"]))
    return difs
