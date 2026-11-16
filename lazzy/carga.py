"""Subir archivos: reconocer cada archivo, mostrar qué va a cambiar antes de guardarlo, y el calendario de datos."""
from datetime import date, timedelta

import openpyxl

from .db import connect, mes_rango
from .importers.fudo_report import import_fudo_productos, import_fudo_report
from .importers.rcv import import_rcv_compras, periodo_from_filename


def tipo(nombre, ruta):
    low = nombre.lower()
    if low.endswith(".csv"):
        if "compra" not in low:
            raise ValueError("por ahora solo se carga el registro de COMPRAS del SII")
        return "sii"
    if low.endswith(".xlsx"):
        hojas = openpyxl.load_workbook(ruta, read_only=True).sheetnames
        if "Detalle" in hojas:
            return "productos"
        if "Evolución de ventas" in hojas:
            return "ventas"
        raise ValueError("no reconozco este Excel (espero Reporte de Ventas o de Productos de FUDO)")
    raise ValueError("formato no soportado (.xlsx de FUDO, .csv del SII, PDF o ZIP de facturas)")


def importar(con, nombre, ruta, t=None):
    t = t or tipo(nombre, ruta)
    if t == "sii":
        r = import_rcv_compras(con, ruta, periodo_from_filename(nombre))
        return t, f"{nombre}: {r['documentos']} documentos de compra, periodo {r['periodo']}"
    if t == "productos":
        return t, f"{nombre}: {import_fudo_productos(con, ruta)['venta_producto']} filas de ventas por producto"
    return t, f"{nombre}: {import_fudo_report(con, ruta)['venta_hora']} horas de venta"


def _fmt(f):
    return f"{int(f[8:10])}/{int(f[5:7])}"


def vista_previa(con, nombre, ruta):
    """Importa en una base temporal y compara con lo que ya hay. No toca la base real."""
    t = tipo(nombre, ruta)
    mem = connect(":memory:")
    importar(mem, nombre, ruta, t)
    clp = lambda x: ("-" if x < 0 else "") + f"${abs(x):,.0f}".replace(",", ".")
    if t == "ventas":
        a, b, n, tot = mem.execute("SELECT MIN(fecha), MAX(fecha), COUNT(DISTINCT fecha), COALESCE(SUM(bruto),0) FROM venta_hora").fetchone()
        if not a:
            return {"tipo": t, "nombre": nombre, "titulo": "Ventas de FUDO", "lineas": ["El archivo no trae ventas."], "ok": False}
        nuevo = {r[0]: r[1] for r in mem.execute("SELECT fecha, SUM(bruto) FROM venta_hora GROUP BY fecha")}
        viejo = {r[0]: r[1] for r in con.execute("SELECT fecha, SUM(bruto) FROM venta_hora WHERE fecha BETWEEN ? AND ? GROUP BY fecha", (a, b))}
        cambian = [f for f in nuevo if f in viejo and abs(nuevo[f] - viejo[f]) > 1]
        lineas = [f"Del {_fmt(a)} al {_fmt(b)}: {n} días con ventas, {clp(tot)} con IVA."]
        nuevos = len([f for f in nuevo if f not in viejo])
        lineas.append(f"{nuevos} días nuevos." if nuevos else "No trae días nuevos.")
        if viejo:
            lineas.append(f"Reemplaza {len(viejo)} días que ya estaban" + (f"; cambian {len(cambian)} (ventas anuladas o corregidas): "
                          + ", ".join(f"{_fmt(f)} {clp(viejo[f])} → {clp(nuevo[f])}" for f in sorted(cambian)[:5]) if cambian else ", sin cambios") + ".")
        return {"tipo": t, "nombre": nombre, "titulo": "Ventas de FUDO", "lineas": lineas, "ok": True}
    if t == "productos":
        a, b, u, tot = mem.execute("SELECT MIN(fecha), MAX(fecha), COALESCE(SUM(cantidad),0), COALESCE(SUM(bruto),0) FROM venta_producto").fetchone()
        if not a:
            return {"tipo": t, "nombre": nombre, "titulo": "Productos de FUDO", "lineas": ["El archivo no trae ventas."], "ok": False}
        prods = {r[0] for r in mem.execute("SELECT DISTINCT producto FROM venta_producto")}
        antes = {r[0] for r in con.execute("SELECT DISTINCT producto FROM venta_producto")}
        nuevos = sorted(prods - antes)
        lineas = [f"Del {_fmt(a)} al {_fmt(b)}: {u:,.0f} unidades de {len(prods)} productos, {clp(tot)} con IVA.".replace(",", ".", 1)]
        if nuevos:
            lineas.append("Productos que aparecen por primera vez (entran a Seguimiento): " + ", ".join(nuevos[:8]) + ("…" if len(nuevos) > 8 else ""))
        v = con.execute("SELECT COALESCE(SUM(bruto),0) FROM venta_hora WHERE fecha BETWEEN ? AND ?", (a, b)).fetchone()[0]
        if v and abs(v - tot) > v * 0.03:
            lineas.append(f"Ojo: en esas fechas las ventas de FUDO suman {clp(v)} y los productos {clp(tot)}. Revisa que ambos reportes tengan las mismas fechas.")
        return {"tipo": t, "nombre": nombre, "titulo": "Productos de FUDO", "lineas": lineas, "ok": True}
    per, n, neto = mem.execute("SELECT MAX(periodo), COUNT(*), COALESCE(SUM(CASE WHEN tipo_doc=61 THEN -neto ELSE neto END),0) FROM compra").fetchone()
    ya = sum(1 for r in mem.execute("SELECT tipo_doc, rut, folio FROM compra")
             if con.execute("SELECT 1 FROM compra WHERE tipo_doc=? AND rut=? AND folio=?", tuple(r)).fetchone())
    sin = [r[0] for r in mem.execute("SELECT DISTINCT razon_social FROM compra c WHERE rut NOT IN (SELECT rut FROM proveedor)")]
    sin = [x for x in sin if not con.execute("SELECT 1 FROM compra c JOIN proveedor p ON p.rut=c.rut WHERE c.razon_social=?", (x,)).fetchone()]
    lineas = [f"Compras de {per}: {n} documentos, {clp(neto)} sin IVA.", f"{n - ya} nuevos, {ya} ya estaban (se actualizan sin duplicar)."]
    if sin:
        lineas.append("Proveedores sin categoría: " + ", ".join(sin[:6]) + ". Se clasifican después en Facturas y proveedores.")
    return {"tipo": t, "nombre": nombre, "titulo": "Compras del SII", "lineas": lineas, "ok": True}


def calendario(con, hoy=None, semanas=8):
    """Qué días tienen ventas y productos de FUDO, y qué meses tienen SII, cuentas, Transbank y F29."""
    hoy = hoy or date.today()
    lunes = hoy - timedelta(days=hoy.weekday()) - timedelta(days=7 * (semanas - 1))
    v = {r[0] for r in con.execute("SELECT DISTINCT fecha FROM venta_hora WHERE fecha >= ?", (lunes.isoformat(),))}
    p = {r[0] for r in con.execute("SELECT DISTINCT fecha FROM venta_producto WHERE fecha >= ?", (lunes.isoformat(),))}
    # un día de la semana se toma como cerrado si en las últimas 8 semanas con datos casi nunca tuvo ventas
    ult = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0] or hoy.isoformat()
    desde = (date.fromisoformat(ult) - timedelta(days=55)).isoformat()
    cerrados = {d for d in range(7) if con.execute(
        "SELECT COUNT(DISTINCT fecha) FROM venta_hora WHERE fecha >= ? AND CAST(strftime('%w', fecha) AS INTEGER)=? AND bruto > 0",
        (desde, (d + 1) % 7)).fetchone()[0] < 2}
    filas = []
    for s in range(semanas):
        dias = []
        for d in range(7):
            f = lunes + timedelta(days=7 * s + d)
            fs = f.isoformat()
            estado = ("futuro" if f >= hoy else "cerrado" if d in cerrados and fs not in v else
                      "ok" if fs in v and fs in p else "medio" if fs in v or fs in p else "falta")
            dias.append({"fecha": f, "estado": estado, "v": fs in v, "p": fs in p})
        filas.append(dias)
    from .gastos import servicios_mes
    serv = {x["periodo"]: x for x in servicios_mes(con, 6, hoy)}
    meses = []
    for per in sorted(serv):
        a, b = mes_rango(per)
        meses.append({"periodo": per, "cerrado": b <= hoy.isoformat(),
                      "sii": bool(con.execute("SELECT 1 FROM compra WHERE periodo=? AND origen='sii'", (per,)).fetchone()),
                      "luz": bool(serv[per]["luz"]["real"]), "agua": bool(serv[per]["agua"]["real"]),
                      "transbank": bool(con.execute("SELECT 1 FROM compra WHERE fecha_doc >= ? AND fecha_doc < ? AND razon_social LIKE '%TRANSBANK%'", (a, b)).fetchone()),
                      "f29": bool(con.execute("SELECT 1 FROM f29 WHERE periodo=?", (per,)).fetchone())})
    return {"semanas": filas, "meses": meses}
