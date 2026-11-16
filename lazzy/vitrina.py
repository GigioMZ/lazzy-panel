"""Vitrina: lo que se saca del congelador a la vitrina («saqué 8 cheesecake berries, 4 brownies»). Congelado no vence;
en vitrina dura unos días (vida útil por producto). Cada tanda se va vendiendo en orden de llegada (lo más antiguo
primero) y el último día avisa lo que, a su ritmo, todavía no se habría vendido, con una idea para venderlo."""
import math
import re
from datetime import date, datetime, timedelta

from . import finance as F
from . import stock as ST

VIDA = 4        # días en vitrina si no se configuró otro
_SACA = r"^\s*(?:hoy\s+)?(?:saqu[eé]|sacamos|sac[oó]|saco|pas[eé]|pasamos|puse|pusimos)\s+(?:a (?:la )?vitrina:?\s+)?(.+)$"
_CONTEO = r"^\s*(?:en (?:la )?)?vitrina:?\s+(?:quedan|queda|hay)\s+(.+)$"


def parsear(con, texto):
    for tipo, rx in (("saca", _SACA), ("conteo", _CONTEO)):
        m = re.match(rx, texto.strip(), re.I)
        if m:
            cuerpo = re.sub(r"\s+(?:a|en) (?:la )?vitrina\s*$", "", m.group(1), flags=re.I)
            r = ST._items(con, cuerpo)
            if r["items"] or r["faltan"]:
                return dict(r, tipo=tipo)
    return None


def registrar(con, producto, cantidad, tipo="saca", fecha=None, origen=None):
    cur = con.execute("INSERT INTO vitrina(fecha,producto,cantidad,tipo,origen,creado) VALUES (?,?,?,?,?,?)",
                      (fecha or date.today().isoformat(), producto, float(cantidad), tipo, origen,
                       datetime.now().isoformat(timespec="microseconds")))
    con.commit()
    return cur.lastrowid


def ajustes(con):
    return {r["producto"]: dict(r) for r in con.execute("SELECT * FROM producto_stock")}


def vida(con, producto, aj=None):
    a = (aj if aj is not None else ajustes(con)).get(producto) or {}
    return int(a.get("vida") or VIDA)


def guardar_ajuste(con, producto, vida=None, objetivo=None, proveedor=None):
    con.execute("INSERT INTO producto_stock(producto, vida, objetivo, proveedor) VALUES (?,?,?,?) "
                "ON CONFLICT(producto) DO UPDATE SET vida=excluded.vida, objetivo=excluded.objetivo, proveedor=excluded.proveedor",
                (producto, vida, objetivo, proveedor or None))
    con.commit()


def ventas_diarias(con, productos, hoy):
    """Ventas reales por producto y día (combos incluidos), la última fecha con ventas cargadas y el ritmo diario
    de las últimas 4 semanas cargadas (para estimar los días cuyas ventas todavía no se suben)."""
    _, sal = ST._movimientos(con, set(productos))
    ult = con.execute("SELECT MAX(fecha) FROM venta_producto").fetchone()[0]
    reales, ritmo = {}, {}
    desde = (date.fromisoformat(ult) - timedelta(days=27)).isoformat() if ult else None
    for p, movs in sal.items():
        for f, q, _ in movs:
            reales.setdefault(p, {})[f] = reales.get(p, {}).get(f, 0) + q
        ritmo[p] = sum(q for f, q, _ in movs if desde and f >= desde) / 28
    return reales, ult, ritmo


def _mermas(con):
    out = {}
    for r in con.execute("SELECT producto, fecha, -SUM(cantidad) q FROM stock_entrada WHERE origen LIKE 'merma%' GROUP BY 1, 2"):
        out.setdefault(r["producto"], {})[r["fecha"]] = r["q"]
    return out


def estado(con, hoy=None):
    """Tandas que siguen en vitrina con lo que se estima que queda de cada una. Las ventas de los días aún no subidos
    desde FUDO se estiman con el ritmo de las últimas 4 semanas."""
    hoy = hoy or date.today()
    filas = [dict(r) for r in con.execute("SELECT * FROM vitrina WHERE fecha <= ? ORDER BY fecha, creado", (hoy.isoformat(),))]
    if not filas:
        return []
    prods = {f["producto"] for f in filas}
    reales, ult, ritmo = ventas_diarias(con, prods, hoy)
    mermas, aj = _mermas(con), ajustes(con)
    out = []
    for p in sorted(prods):
        movs = [f for f in filas if f["producto"] == p]
        v = vida(con, p, aj)
        lotes, d = [], date.fromisoformat(movs[0]["fecha"])
        estimado = False
        while d <= hoy:
            ds = d.isoformat()
            for f in movs:
                if f["fecha"] == ds and f["tipo"] == "saca":
                    lotes.append({"producto": p, "fecha": ds, "sacados": f["cantidad"], "quedan": f["cantidad"],
                                  "ultimo_dia": (d + timedelta(days=v - 1)).isoformat(), "vida": v})
            if d < hoy:     # lo de hoy todavía no se vende
                if ult and ds <= ult:
                    usa = reales.get(p, {}).get(ds, 0)
                else:
                    usa, estimado = ritmo.get(p, 0), True
                usa += mermas.get(p, {}).get(ds, 0)
                for lote in lotes:
                    t = min(lote["quedan"], usa)
                    lote["quedan"] -= t
                    usa -= t
            conteos = [f for f in movs if f["fecha"] == ds and f["tipo"] == "conteo"]
            if conteos:     # «en vitrina quedan 3»: lo que hay son las tandas más nuevas
                resto = conteos[-1]["cantidad"]
                for lote in reversed(lotes):
                    lote["quedan"] = min(lote["sacados"], resto)
                    resto -= lote["quedan"]
            if d < hoy:     # al cerrar su último día, lo que quedaba se da por vencido
                lotes = [x for x in lotes if x["ultimo_dia"] > ds]
            d += timedelta(days=1)
        for x in (x for x in lotes if x["quedan"] > 0.05):
            x["quedan"] = round(x["quedan"], 1)
            x["dias"] = (date.fromisoformat(x["ultimo_dia"]) - hoy).days
            x["estimado"] = estimado
            x["ritmo"] = round(ritmo.get(p, 0), 1)
            out.append(x)
    return out


def precio_promedio(con, producto):
    r = con.execute("SELECT SUM(bruto), SUM(cantidad) FROM venta_producto WHERE producto=? AND fecha >= date('now','-60 day')",
                    (producto,)).fetchone()
    if not r[1]:
        r = con.execute("SELECT SUM(bruto), SUM(cantidad) FROM venta_producto WHERE producto=?", (producto,)).fetchone()
    return r[0] / r[1] if r[1] else None


def idea(con, cfg, producto, quedan):
    """Una forma concreta de vender lo que vence: un combo que ya existe y un precio rebajado que no pierda plata."""
    from .insights import clp
    combos = [r[0] for r in con.execute("SELECT DISTINCT producto FROM venta_producto WHERE producto LIKE '%+%'")
              if producto in F.componentes(r[0], [producto])]
    precio = precio_promedio(con, producto)
    costo = (cfg["costo_producto"].get(producto) or {}).get("costo")
    opciones = []
    if combos:
        opciones.append(f"en «{combos[0]}»")
    if precio:
        oferta = math.floor(precio * 0.75 / 100) * 100
        if costo is not None:
            piso = math.ceil(costo * (1 + cfg["empresa"]["iva"]) / 100) * 100      # bajo esto, se vende bajo el costo
            if oferta < piso < precio:
                oferta = piso
            g = round(oferta / (1 + cfg["empresa"]["iva"]) - costo)
            nota = f"todavía deja {clp(g)} c/u" if g > 0 else "sin perder plata" if g == 0 else f"pierde {clp(-g)} c/u, menos que botarlo"
            opciones.append(f"a {clp(oferta)} ({round(100 * (1 - oferta / precio))}% menos; {nota})")
        else:
            opciones.append(f"a {clp(oferta)} (25% menos)")
    txt = ("Ofrézcanlo " + " o ".join(opciones) + ", y " if opciones else "") + ("súbanlo a historias: «último del día»" if quedan <= 1 else f"súbanlo a historias: «últimos {quedan:g} del día»")
    return txt[0].upper() + txt[1:]


def por_vencer(con, hoy):
    """Tandas cuyo último día es hoy o mañana y que, a su ritmo, todavía no se habrían vendido."""
    return [x for x in estado(con, hoy) if x["dias"] in (0, 1) and x["quedan"] >= 0.5]


def texto_lote(x):
    q = math.ceil(x["quedan"] - 0.25)
    cuando = "hoy es su último día" if x["dias"] == 0 else "mañana es su último día"
    return f"{'~' if x['estimado'] else ''}{q:g} {x['producto']} sacados el {x['fecha'][8:10]}/{x['fecha'][5:7]}: {cuando}"
