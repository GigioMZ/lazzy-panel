"""Preguntas en lenguaje normal por el bot: se arma un resumen compacto de los datos del panel y Haiku responde
solo con eso. Solo lectura: nunca cambia nada."""
import re
from datetime import date, timedelta

import httpx

from . import analisis as A
from . import finance as F
from . import gastos as G
from . import insights as I
from . import metas as MT
from . import proveedores as PV
from . import seguimiento as S
from .captura import API, MODELO, ErrorLectura
from .glosario import CAT_NOMBRE

INSTRUCCIONES = """Eres el asistente de datos de Lazzy Coffee, una cafetería de especialidad en La Florida, Santiago.
Respondes por Telegram a los dueños. Usa SOLO los datos de abajo; si algo no está, dilo y sugiere dónde verlo en el panel
(https://panel.lazzycoffee.cl). Montos en pesos chilenos con punto de miles (ej. $1.250.000). Di siempre si un monto es con o
sin IVA. Sé breve: 2 a 6 frases o una lista corta, sin títulos ni markdown con asteriscos. Si te piden una recomendación,
da una concreta con su número y advierte la incertidumbre en una frase. Escribe en español de Chile, cercano y claro."""

PREGUNTA = re.compile(r"\?|^\s*¿|^\s*(cu[aá]nt|c[oó]mo|qu[eé]|cu[aá]l|cu[aá]ndo|d[oó]nde|por ?qu[eé]|dame|dime|mu[eé]strame|"
                      r"analiza|compara|conviene|deber[ií]a|hay |vale la pena|recomienda|explica|resume)", re.I)
GASTO = re.compile(r"[a-záéíóúñ]+\s*:?\s*\$?\s*\d{1,3}(?:[.\s]?\d{3})+\b|\b\d{4,}\b", re.I)


def es_pregunta(texto):
    """Las preguntas van a Haiku con los datos; «luz 250730» sigue siendo un gasto."""
    return bool(PREGUNTA.search(texto)) and not (GASTO.search(texto) and "?" not in texto)


def _semana(con, a, b):
    r = con.execute("SELECT COALESCE(SUM(bruto),0) b, COALESCE(SUM(tickets),0) t, COUNT(DISTINCT fecha) d FROM venta_hora "
                    "WHERE fecha BETWEEN ? AND ?", (a.isoformat(), b.isoformat())).fetchone()
    return f"{a.day}/{a.month} al {b.day}/{b.month}: {I.clp(r['b'])} con IVA, {r['t']} ventas, {r['d']} días"


def contexto(con, hoy=None):
    hoy = hoy or date.today()
    cfg = F.load_cfg(con)
    iva = cfg["empresa"]["iva"]
    out = [f"Hoy: {hoy.isoformat()}."]
    uv = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0]
    out.append(f"Ventas de FUDO cargadas hasta: {uv or 'nada'}. Facturas: hasta {con.execute('SELECT MAX(fecha_doc) FROM compra').fetchone()[0]}.")
    if uv:
        u = date.fromisoformat(uv)
        lunes = u - timedelta(days=u.weekday())
        out.append("Semanas (lunes a domingo): " + " | ".join(_semana(con, lunes - timedelta(days=7 * k), lunes - timedelta(days=7 * k - 6))
                                                              for k in range(0, 5)))
    ps = F.periodos(con)
    cur = hoy.strftime("%Y-%m")
    lineas = []
    for per in ps[-6:]:
        p = F.pnl_mes(con, per, cfg)
        cats = ", ".join(f"{CAT_NOMBRE.get(k, k)} {I.clp(v)}"
                         for g in ("costo_venta", "gasto_operacional") for k, v in sorted(p["lineas"].get(g, {}).items(), key=lambda x: -x[1])[:4])
        lineas.append(f"{per}{' (en curso)' if per >= cur else ''}{'' if p['tiene_compras'] else ' (sin facturas del SII)'}: venta sin IVA "
                      f"{I.clp(p['ventas']['neto'])}, costo de venta {I.clp(p['costo_venta'])} ({p['pct']['costo_venta']}%), sueldos "
                      f"{I.clp(p['personal'])}, gastos del local {I.clp(p['gasto_operacional'])}, sin categoría {I.clp(p['sin_clasificar'])}, "
                      f"resultado {I.clp(p['resultado_operacional'])} ({p['pct']['resultado']}%), ticket promedio {I.clp(p['ventas']['ticket_promedio'])} "
                      f"con IVA, {p['ventas']['dias_abiertos']} días. Principales: {cats}.")
    out.append("Estado de resultados por mes:\n" + "\n".join(lineas))
    m = MT.leer(con, cur)
    r = MT.ritmo(con, cfg, cur)
    if m:
        out.append(f"Metas de {cur}: " + ", ".join(f"{k} {v:g}" for k, v in m.items()))
    if r:
        out.append(f"Proyección {cur}: venta sin IVA {I.clp(r['proy_venta'])}, ganancia ≈ {I.clp(r['proy_resultado'] or 0)}; "
                   f"van {r['avance']}% del mes.")
    dias = A.por_dia_semana(con)
    out.append("Venta promedio por día de la semana (con IVA): " + ", ".join(
        f"{d['nombre']} {I.clp(d['venta'])}" if d["abierto"] else f"{d['nombre']} cerrado" for d in dias))
    mapa = A.mapa_horas(con)
    recs = A.recomendaciones(dias, mapa, [], [])
    out.append("Observaciones: " + " | ".join(x["titulo"] for x in recs[:5]))
    est = A.estimacion(con)
    if est.get("dias"):
        out.append(f"Estimación próxima semana: {I.clp(est['total'])} con IVA.")
    if ps:
        a = ps[max(0, len(ps) - 3)]
        mp = F.margen_productos(con, a, ps[-1], cfg)
        filas = sorted(mp["productos"], key=lambda x: -(x.get("cantidad") or 0))[:25]
        out.append("Productos (últimos 3 meses; precio con IVA, costo sin IVA, margen sobre precio sin IVA): " + "; ".join(
            f"{x['producto']}: {x['cantidad']:.0f} uds, {I.clp(x['precio_bruto'])}, costo {I.clp(x['costo']) if x.get('costo') is not None else 'sin costo'}"
            f"{', margen ' + str(round(x['margen_pct'])) + '%' if x.get('margen_pct') is not None else ''}, {x.get('clase') or ''}" for x in filas))
        pr = A.precios_a_revisar(con, cfg, a, ps[-1])
        if pr:
            out.append("Productos a revisar: " + "; ".join(
                f"{x['producto']} precio {I.clp(x['precio_bruto'])}, margen {x['margen_pct']}% (meta {x['meta']}%): "
                + (f"subir precio a {I.clp(x['sugerido'])}" if x.get("sugerido") else f"bajar costo a {I.clp(x.get('costo_obj') or 0)}")
                for x in pr[:6]))
    grupos = S.lista(con, cfg)[:8]
    if grupos:
        out.append("Seguimiento de decisiones: " + " | ".join(f"{g['fecha']} {g['eventos'][0]['titulo'] if len(g['eventos']) == 1 else str(len(g['eventos'])) + ' cambios de precio'}: {g['veredicto'][1]}"
                                                            for g in grupos))
    movs = PV.movimientos(con, dias=90, n=8)
    if movs:
        out.append("Cambios de precio de proveedores (sin IVA): " + "; ".join(
            f"{x['proveedor']} {x['texto']} {I.clp(x['anterior'])}→{I.clp(x['ultimo'])} ({x['cambio']:+.0f}%)" for x in movs))
    provs = PV.resumen(con)[:10]
    out.append("Proveedores (total histórico sin IVA): " + "; ".join(f"{p['alias'] or p['razon']} {I.clp(p['neto'])} ({p['cat'] or 'sin categoría'})" for p in provs))
    t = G.analisis_turnos(con)
    if t["personas"]:
        out.append("Turnos (venta con IVA, últimas 8 semanas): " + "; ".join(
            f"{p['persona']} {p['horas_semana']:g} h/semana, vende {I.clp(p['venta_semana'])} por semana en su turno, {I.clp(p['por_hora'])} por hora"
            + ("" if p["en_resultado"] else " (no está en el estado de resultados)") for p in t["personas"]))
    ivas = F.iva_mes(con, cur, cfg)
    out.append(f"IVA estimado {cur} hasta ahora: débito {I.clp(ivas['debito'])}, crédito {I.clp(ivas['credito'])}, PPM {I.clp(ivas['ppm'])}, "
               f"F29 estimado {I.clp(ivas['f29_estimado'])}. IVA {iva * 100:.0f}%.")
    return "\n".join(out)


def responder(con, pregunta, hoy=None, http=httpx):
    from .claves import leer
    import os
    clave = os.environ.get("ANTHROPIC_API_KEY") or leer().get("ANTHROPIC_API_KEY")
    if not clave:
        raise ErrorLectura("Falta configurar la clave de la API de Anthropic.")
    datos = contexto(con, hoy)
    r = http.post(API, timeout=90, headers={"x-api-key": clave, "anthropic-version": "2023-06-01"}, json={
        "model": MODELO, "max_tokens": 700,
        "system": [{"type": "text", "text": INSTRUCCIONES}, {"type": "text", "text": "DATOS DEL PANEL:\n" + datos}],
        "messages": [{"role": "user", "content": pregunta[:1500]}]})
    if r.status_code >= 400:
        raise ErrorLectura(f"La API respondió {r.status_code}.")
    j = r.json()
    u = j.get("usage", {})
    from datetime import datetime
    con.execute("INSERT INTO api_uso VALUES (?,?,?,?)", (datetime.now().isoformat(timespec="seconds"), "pregunta",
                                                        u.get("input_tokens", 0), u.get("output_tokens", 0)))
    con.commit()
    txt = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text").strip()
    return txt.replace("**", "") or "No supe responder eso con los datos del panel."
