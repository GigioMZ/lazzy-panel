"""Avisos automáticos del bot. Se revisan una vez al día y cada uno se manda una sola vez."""
import logging
import statistics as st
from datetime import date, datetime, timedelta

from . import finance as F
from . import insights as I
from . import metas as MT
from . import proveedores as PV
from . import seguimiento as S

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
ALZA = 8.0          # % de alza de un proveedor que merece aviso


def _nuevo(con, clave):
    if con.execute("SELECT 1 FROM alerta WHERE clave=?", (clave,)).fetchone():
        return False
    con.execute("INSERT INTO alerta VALUES (?,?)", (clave, datetime.now().isoformat(timespec="seconds")))
    return True


def dias_bajos(con, hoy):
    """Días de los últimos 10 que vendieron bastante menos que lo normal para ese día de la semana."""
    out = []
    ventas = {r["fecha"]: r["b"] for r in con.execute(
        "SELECT fecha, SUM(bruto) b FROM venta_hora WHERE fecha >= ? GROUP BY fecha", ((hoy - timedelta(days=80)).isoformat(),))}
    for f, b in sorted(ventas.items()):
        d = date.fromisoformat(f)
        if (hoy - d).days > 10:
            continue
        prev = [ventas.get((d - timedelta(days=7 * k)).isoformat()) for k in range(1, 9)]
        prev = [x for x in prev if x]
        if len(prev) < 4:
            continue
        m, s = st.mean(prev), st.pstdev(prev)
        if b < m * 0.75 and b < m - 1.3 * s and _nuevo(con, f"bajo:{f}"):
            out.append(f"📉 El {DIAS[d.weekday()]} {d.day}/{d.month} se vendió {I.clp(b)} con IVA, bastante bajo lo normal para un "
                       f"{DIAS[d.weekday()]} ({I.clp(m)} en promedio). ¿Pasó algo? Si fue puntual (lluvia, corte, feriado), "
                       "anótalo en Seguimiento.")
    return out


def facturas_faltantes(con, hoy):
    """Proveedores habituales de insumos cuya factura se está demorando más de lo normal."""
    ult_global = con.execute("SELECT MAX(fecha_doc) FROM compra").fetchone()[0]
    if not ult_global or (hoy - date.fromisoformat(ult_global)).days > 5:   # sin facturas recientes, no hay con qué comparar
        return []
    cfg = F.load_cfg(con)
    out = []
    for (rut,) in con.execute("SELECT rut FROM compra WHERE fecha_doc >= ? AND tipo_doc <> 61 GROUP BY rut HAVING COUNT(*) >= 4",
                              ((hoy - timedelta(days=120)).isoformat(),)).fetchall():
        if cfg["categorias"].get(cfg["proveedores"].get(rut)) != "costo_venta":
            continue
        fechas = sorted({date.fromisoformat(r[0]) for r in con.execute(
            "SELECT fecha_doc FROM compra WHERE rut=? AND tipo_doc <> 61 AND fecha_doc >= ?", (rut, (hoy - timedelta(days=120)).isoformat()))})
        if len(fechas) < 4:
            continue
        cada = st.median([(b - a).days for a, b in zip(fechas, fechas[1:])])
        hace = (hoy - fechas[-1]).days
        if hace >= 7 and hace > max(1.8 * cada, cada + 5) and _nuevo(con, f"falta:{rut}:{fechas[-1]}"):
            out.append(f"🧾 Normalmente llega una factura de {PV.nombre(con, rut)} cada {cada:.0f} días y la última es del "
                       f"{fechas[-1].day}/{fechas[-1].month} (hace {hace}). Si ya llegó, mándamela.")
    return out


def alzas(con, hoy):
    """Productos de proveedores que subieron ALZA% o más en facturas de los últimos 10 días."""
    out = []
    for x in PV.movimientos(con, dias=10, n=30):
        if x["cambio"] >= ALZA and _nuevo(con, f"alza:{x['rut']}:{x['clave']}:{x['fecha']}"):
            out.append(f"⚠️ {x['proveedor']} subió {x['texto']} {x['cambio']:.0f}%: de {I.clp(x['anterior'])} a {I.clp(x['ultimo'])} sin IVA.")
    return out


def metas(con, hoy):
    """Mes cerrado con costo de venta sobre la meta, y mes en curso que va bajo la meta de venta."""
    out = []
    cfg = F.load_cfg(con)
    ant = (hoy.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    p = F.pnl_mes(con, ant, cfg)
    m = MT.leer(con, ant)
    if p["tiene_compras"] and con.execute("SELECT 1 FROM compra WHERE periodo=? AND origen='sii'", (ant,)).fetchone():
        if m.get("costo_venta_pct") and p["pct"]["costo_venta"] > m["costo_venta_pct"] and _nuevo(con, f"cv:{ant}"):
            out.append(f"📊 En {I.mes_largo(ant)} el costo de venta fue {p['pct']['costo_venta']:.0f}% de la venta, sobre la meta de "
                       f"{m['costo_venta_pct']:.0f}%. El detalle por categoría está en Mes a mes.")
        if p["pct"]["prime_cost"] > 60 and _nuevo(con, f"prime:{ant}"):
            out.append(f"📊 En {I.mes_largo(ant)} insumos más sueldos fueron {p['pct']['prime_cost']:.0f}% de la venta (sano: bajo 60%).")
    cur = hoy.strftime("%Y-%m")
    mc = MT.leer(con, cur)
    if mc.get("venta") and 12 <= hoy.day <= 20:
        r = MT.ritmo(con, cfg, cur)
        if r and r["proy_venta"] < mc["venta"] * 0.92 and _nuevo(con, f"ritmo:{cur}"):
            out.append(f"🎯 Al ritmo normal, {I.mes_largo(cur)} cerraría con {I.clp(r['proy_venta'])} de venta sin IVA, bajo la meta de "
                       f"{I.clp(mc['venta'])} (faltarían {I.clp(mc['venta'] - r['proy_venta'])}). Buen momento para un anuncio o una oferta.")
    return out


def servicios(con, hoy):
    """Cuenta de luz o agua bastante sobre lo esperado."""
    from .gastos import servicios_mes
    out = []
    for s in servicios_mes(con, meses=2):
        for k in ("luz", "agua"):
            x = s[k]
            if x["real"] and x["esperado"] and x["real"] > x["esperado"] * 1.15 and _nuevo(con, f"serv:{k}:{s['periodo']}"):
                out.append(f"💡 La cuenta de {k} de {I.mes_largo(s['periodo'])} fue {I.clp(x['real'])}, "
                           f"{100 * (x['real'] / x['esperado'] - 1):.0f}% sobre lo esperado ({I.clp(x['esperado'])}).")
    return out


def stock_bajo(con, hoy):
    """Productos que quedaron bajo su mínimo (una vez por producto y día)."""
    from . import stock as ST
    bajos = [x for x in ST.bajo_minimo(con, F.load_cfg(con), hoy) if _nuevo(con, f"stock:{x['producto']}:{hoy}")]
    if not bajos:
        return []
    return ["📦 Bajo el stock mínimo: " + ", ".join(f"{x['producto']} ({x['esperado']:g}, mínimo {x['minimo']:g})" for x in bajos)
            + ". Si ya llegó el pedido, mándame la factura o escribe «llegaron N producto»."]


def vitrina(con, hoy):
    """Lo que en vitrina llega a su último día hoy o mañana y, a su ritmo, todavía no se habría vendido."""
    from . import vitrina as VT
    cfg = F.load_cfg(con)
    lotes = [x for x in VT.por_vencer(con, hoy) if _nuevo(con, f"vitrina:{x['producto']}:{x['fecha']}:{x['dias']}")]
    if not lotes:
        return []
    t = ["🍰 Vitrina:"]
    for x in lotes:
        t.append(f"• {VT.texto_lote(x)}. {VT.idea(con, cfg, x['producto'], max(round(x['quedan']), 1))}.")
    if any(x["estimado"] for x in lotes):
        t.append("(~ estimado con su ritmo de venta, porque las ventas de los últimos días no se suben aún).")
    t.append("Si quedan otros: «en vitrina quedan 2 cheesecake berries». Lo que se bote: «se botaron 2 cheesecake berries por vencidos».")
    return ["\n".join(t)]


def revisar(con, hoy=None):
    """Corre todos los avisos y los deja en la cola del bot. Devuelve los textos nuevos."""
    hoy = hoy or date.today()
    textos = []
    for fn in (alzas, dias_bajos, facturas_faltantes, metas, servicios, stock_bajo, vitrina):
        try:
            textos += fn(con, hoy)
        except Exception as e:  # un aviso que falla no detiene los demás
            logging.getLogger("lazzy.alertas").warning("aviso %s falló: %s", fn.__name__, e)
    textos = [t for t in textos if t]
    for t in textos:
        S.notificar(con, t)
    con.commit()
    return textos


def toca(con, ahora):
    """Una vez al día, desde las 9:30."""
    if (ahora.hour, ahora.minute) < (9, 30):
        return False
    ult = (con.execute("SELECT valor FROM parametro WHERE clave='alertas_ultimo'").fetchone() or [""])[0]
    if ult == ahora.date().isoformat():
        return False
    con.execute("INSERT OR REPLACE INTO parametro VALUES ('alertas_ultimo', ?)", (ahora.date().isoformat(),))
    con.commit()
    return True
