"""Ayuda: rutina semanal con lo que está pendiente hoy, y ejemplos del glosario con los números de Lazzy."""
from datetime import date, timedelta

from . import finance as F
from . import insights as I
from . import iva as IVA


def rutina(con, hoy=None):
    hoy = hoy or date.today()
    uv = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0]
    up = con.execute("SELECT MAX(fecha) FROM venta_producto").fetchone()[0]
    domingo = hoy - timedelta(days=hoy.weekday() + 1)
    ant = (hoy.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    sii = con.execute("SELECT 1 FROM compra WHERE periodo=? AND origen='sii'", (ant,)).fetchone()
    f29 = con.execute("SELECT 1 FROM f29 WHERE periodo=?", (ant,)).fetchone()
    bot = con.execute("SELECT MAX(fecha_doc) FROM compra WHERE origen='bot'").fetchone()[0]
    pago = IVA.fecha_pago(ant)
    st = con.execute("SELECT MAX(fecha) FROM stock_conteo").fetchone()[0]
    vt = con.execute("SELECT MAX(fecha) FROM vitrina").fetchone()[0]
    return [
        {"quien": "javi", "corto": "Mandar las facturas al bot", "cuando": "Cada día", "que": "Mandar al bot cada factura apenas llega (PDF o foto) y las cuentas de luz y agua.",
         "ok": bool(bot and (hoy - date.fromisoformat(bot)).days <= 4), "estado": f"última por el bot: {bot}" if bot else "ninguna todavía"},
        {"quien": "javi", "corto": "Anotar lo que sale a vitrina", "cuando": "Cada vez que saquen algo del congelador", "que": "Escribirle al bot lo que salió a vitrina: «saqué 8 cheesecake berries, 4 brownies». "
         "Con eso avisa el último día de cada tanda.", "ok": bool(vt and (hoy - date.fromisoformat(vt)).days <= 3),
         "estado": f"última anotada: {vt}" if vt else "nada anotado todavía", "url": "/stock"},
        {"quien": "javi", "corto": "Revisar el pedido sugerido", "cuando": "Antes de cada pedido", "que": "Pedir al bot /pedido (o mirar Stock): sale lo que hay que pedir a cada proveedor, listo para copiar.",
         "ok": None, "url": "/stock"},
        {"quien": "guille", "frec": "semanal", "corto": "Subir ventas y productos de FUDO", "cuando": "Lunes", "que": "Subir los dos reportes de FUDO (Ventas y Productos), desde el día 1 del mes hasta el domingo.",
         "ok": bool(uv and up and uv >= domingo.isoformat() and up >= domingo.isoformat()),
         "estado": f"cargado hasta el {uv}" if uv else "nada cargado", "url": "/cargar"},
        {"quien": "guille", "frec": "mensual", "corto": "Subir el stock de FUDO", "cuando": "Con cada ajuste y al cierre del mes", "que": "Exportar el stock de FUDO y subirlo (o mandarlo al bot): cuando anoten merma, "
         "después de contar la vitrina y el último día del mes.", "ok": bool(st and (hoy - date.fromisoformat(st)).days <= 10),
         "estado": f"último stock: {st}" if st else "todavía no hay stock cargado", "url": "/stock"},
        {"quien": "todos", "corto": "Leer el resumen de la semana", "cuando": "Lunes", "que": "Leer el resumen del bot y mirar el Tablero y Seguimiento (5 minutos).", "ok": None, "url": "/"},
        {"quien": "guille", "frec": "mensual", "corto": f"Subir compras del SII de {I.mes_largo(ant)}", "cuando": "Primeros días del mes", "que": f"Descargar del SII el registro de compras de {I.mes_largo(ant)} y subirlo.",
         "ok": bool(sii), "estado": "listo" if sii else "falta", "url": "/cargar"},
        {"quien": "guille", "corto": "Revisar el cierre del mes", "cuando": "Primeros días del mes", "que": "Revisar la lista de cierre del mes anterior y clasificar proveedores nuevos.",
         "ok": None, "url": f"/mes?periodo={ant}"},
        {"quien": "guille", "frec": "mensual", "corto": f"Pagar el F29 (hasta el {pago.day}/{pago.month})", "pago": pago,
         "cuando": f"Hasta el {pago.day}/{pago.month}", "que": f"Pagar el F29 de {I.mes_largo(ant)} y mandarle el PDF al bot.",
         "ok": bool(f29), "estado": "recibido" if f29 else "falta el PDF", "url": "/impuestos"},
    ]


def pendientes(con, hoy=None):
    """Lo que falta hacer ahora (para la campanita del panel): tareas de la rutina sin hacer y vínculos de stock por revisar."""
    from . import stock as ST
    hoy = hoy or date.today()
    out = [{"texto": r["corto"], "detalle": r.get("estado"), "url": r.get("url", "/ayuda"), "quien": r["quien"]}
           for r in rutina(con, hoy) if r["ok"] is False]
    n = len(ST.sugerencias(con))
    if n:
        out.append({"texto": f"Vincular {n} producto{'s' if n > 1 else ''} de facturas con el stock", "detalle": "un clic cada uno",
                    "url": "/stock", "quien": "guille"})
    return out


def recordatorios(con, hoy=None):
    """Lo que el bot recuerda a las 9:30, por persona: los lunes todo lo pendiente de la semana y del mes; el día 1, lo del
    cierre; el último día del mes, exportar el stock; y el F29 tres días antes de su vencimiento y el mismo día."""
    hoy = hoy or date.today()
    tareas = [r for r in rutina(con, hoy) if r.get("frec")]
    manana = hoy + timedelta(days=1)
    porq = {}

    def agregar(quien, texto):
        for q in (("javi", "guille") if quien == "todos" else (quien,)):
            if texto not in porq.setdefault(q, []):
                porq[q].append(texto)
    for r in tareas:
        if r["ok"] is not False:
            continue
        if hoy.weekday() == 0 or (hoy.day == 1 and r["frec"] == "mensual"):
            agregar(r["quien"], r["corto"] + (f" ({r['estado']})" if r.get("estado") and r["frec"] == "semanal" else ""))
        if r.get("pago") and (r["pago"] - hoy).days in (3, 0):
            agregar(r["quien"], r["corto"] + (": vence hoy" if r["pago"] == hoy else ": vence en 3 días"))
    if manana.month != hoy.month:
        agregar("guille", "Hoy es el último día del mes: al cerrar, exportar el stock de FUDO y subirlo (o mandárselo al bot)")
    return {q: "⏰ Para hoy:\n" + "\n".join(f"• {t}" for t in ts) + "\n\nTodo en https://panel.lazzycoffee.cl/ayuda"
            for q, ts in porq.items() if ts}


def ejemplos(con):
    """Una frase con sus números para las palabras más usadas del glosario (último mes con facturas del SII)."""
    cfg = F.load_cfg(con)
    hoy = date.today().strftime("%Y-%m")
    ps = [p for p in F.periodos(con) if p < hoy]
    p = next((F.pnl_mes(con, x, cfg) for x in reversed(ps) if F.pnl_mes(con, x, cfg)["tiene_compras"]), None)
    if not p:
        return {}
    c, m = I.clp, I.mes_largo(p["periodo"])
    pc = lambda x: f"{x:.1f}".replace(".", ",") + "%"
    v = p["ventas"]
    out = {
        "venta_neta": f"En {m} cobraron {c(v['bruto'])} con IVA; sin IVA son {c(v['neto'])}, que es lo que es de ustedes.",
        "margen_bruto": f"En {m}: {c(v['neto'])} de venta menos {c(p['costo_venta'])} de costo de venta = {c(p['margen_bruto'])} ({pc(p['pct']['margen_bruto'])}).",
        "ganancia": f"En {m} quedó {c(p['resultado_operacional'])} después de todos los costos: {pc(p['pct']['resultado'])} de lo vendido.",
        "ticket": f"En {m} hubo {v['tickets']:,} ventas con un promedio de {c(v['ticket_promedio'])} con IVA cada una.".replace(",", "."),
        "insumos_sueldos": f"En {m}, insumos más sueldos fueron {pc(p['pct']['prime_cost'])} de la venta: de cada $1.000 se fueron {c(round(p['pct']['prime_cost'] * 10))}.",
        "equilibrio": f"Con los costos de {m}, había que vender {c(p['equilibrio']['venta_neta_mes'])} sin IVA al mes para no perder.",
        "comision_tarjetas": f"En {m} la comisión de tarjetas fue {c(p['comision']['monto'])}, {str(p['comision']['pct']).replace('.', ',')}% de lo cobrado con tarjeta.",
    }
    iv = F.iva_mes(con, p["periodo"], cfg)
    out["f29"] = (f"En {m}: IVA de las ventas {c(iv['debito'])} menos IVA de las compras {c(iv['credito'])} = {c(iv['iva_a_pagar'])}, "
                  f"más PPM {c(iv['ppm'])}.")
    return out
