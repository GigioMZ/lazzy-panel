"""Panel web. Ejecutar: uvicorn lazzy.web:app  (variables: LAZZY_DB, LAZZY_SECRET, LAZZY_HTTPS=1)."""
import json
from urllib.parse import quote
import os
import secrets
import time
try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from markupsafe import Markup, escape

from . import finance as F
from . import analisis as A
from . import insights as I
from . import tablero as TB
from . import estado as E
from . import historico as H
from . import carga as CG
from . import gastos as GS
from . import iva as IVA
from . import metas as MT
from . import ofertas as O
from . import simulador as SIM
from . import stock as ST
from . import proveedores as PV
from . import seguimiento as S
from .auth import check_password
from .db import connect
from .glosario import CAT_NOMBRE, CATEGORIAS_UI, GLOSARIO, GRUPO_NOMBRE

DB_PATH = os.environ.get("LAZZY_DB", "data/lazzy.db")
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _secret():
    if os.environ.get("LAZZY_SECRET"):
        return os.environ["LAZZY_SECRET"]
    p = Path(DB_PATH).with_name("session.secret")
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_hex(32))
        p.chmod(0o600)
    return p.read_text().strip()


app.add_middleware(SessionMiddleware, secret_key=_secret(), max_age=14 * 86400, same_site="lax",
                   https_only=os.environ.get("LAZZY_HTTPS") == "1")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
T = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
T.env.filters["clp"] = lambda x: "—" if x is None else ("-" if x < 0 else "") + f"${abs(x):,.0f}".replace(",", ".")
T.env.filters["num"] = lambda x: f"{x:,.0f}".replace(",", ".")
T.env.filters["pct"] = lambda x, d=1: "—" if x is None else f"{x:.{d}f}".replace(".", ",") + "%"
MESES = "ene feb mar abr may jun jul ago sep oct nov dic".split()
T.env.filters["mes"] = lambda p: f"{MESES[int(p[5:7]) - 1]} {p[:4]}"
T.env.filters["mesl"] = I.mes_largo
T.env.filters["cat"] = lambda c: CAT_NOMBRE.get(c, c) if c else "Sin categoría"
T.env.filters["grupo"] = lambda g: GRUPO_NOMBRE.get(g, "")


def tip(clave):
    """Botón «?» con la explicación en simple de un término del glosario."""
    nombre, texto = GLOSARIO[clave]
    return Markup('<button type="button" class="tip" aria-label="Qué significa {n}"><span class="tip-i" aria-hidden="true">?</span>'
                  '<span class="tip-t" role="tooltip"><b>{n}</b>{t}</span></button>').format(n=nombre, t=texto)


T.env.globals.update(tip=tip, G=GLOSARIO, CATS_UI=CATEGORIAS_UI, GRUPOS=GRUPO_NOMBRE)
NAV = [("Ver", [("/", "Tablero"), ("/mes", "Cierre del mes"), ("/decisiones", "Decisiones"), ("/seguimiento", "Seguimiento"), ("/resultados", "Mes a mes"), ("/productos", "Productos"),
                 ("/stock", "Stock"), ("/turnos", "Turnos"), ("/impuestos", "IVA")]),
       ("Actualizar", [("/bot", "Bot de Telegram"), ("/cargar", "Subir archivos"), ("/compras", "Facturas y proveedores"),
                       ("/insumos", "Insumos y precios"), ("/costos-fijos", "Gastos fijos")]),
       ("", [("/ayuda", "Ayuda")])]
_con = None
_fails = {}


def db():
    global _con
    if _con is None:
        _con = connect(DB_PATH)
    return _con


class Redirect(HTTPException):
    def __init__(self, url):
        super().__init__(status_code=303, headers={"Location": url})


def user(request: Request):
    if not request.session.get("user"):
        raise Redirect("/login")
    return request.session["user"]


async def form(request: Request):
    user(request)
    f = await request.form()
    if not secrets.compare_digest(str(f.get("csrf", "")), request.session.get("csrf", "x")):
        raise HTTPException(403, "Sesión expirada, recarga la página")
    return f


def volver(f, defecto):
    v = str(f.get("volver") or "")
    return v if v.startswith("/") and not v.startswith("//") else defecto


def page(request, name, **ctx):
    u = request.session.get("user")
    return T.TemplateResponse(request, name, {"user": u, "csrf": request.session.get("csrf"), "cats": F.CATEGORIAS,
                                              "nav": NAV, "fresc": I.frescura(db()) if u else None, **ctx})


def month_add(p, n):
    y, m = int(p[:4]), int(p[5:7]) + n
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f"{y}-{m:02d}"


def rango(request, con, meses=6):
    ps = F.periodos(con) or [date.today().strftime("%Y-%m")]
    hasta = request.query_params.get("hasta") or ps[-1]
    desde = request.query_params.get("desde") or max(month_add(hasta, -(meses - 1)), ps[0])
    lista, p = [], desde
    while p <= hasta and len(lista) < 36:
        lista.append(p)
        p = month_add(p, 1)
    return desde, hasta, lista, ps


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return page(request, "login.html", error=None)


@app.post("/login")
async def login_post(request: Request):
    f = await request.form()
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
    n, t0 = _fails.get(ip, (0, 0))
    if n >= 5 and time.time() - t0 < 300:
        return page(request, "login.html", error="Demasiados intentos. Espera 5 minutos.")
    if check_password(db(), str(f.get("usuario", "")).strip().lower(), str(f.get("clave", ""))):
        _fails.pop(ip, None)
        request.session.update(user=str(f["usuario"]).strip().lower(), csrf=secrets.token_hex(16))
        return RedirectResponse("/", 303)
    _fails[ip] = (n + 1 if time.time() - t0 < 300 else 1, time.time())
    return page(request, "login.html", error="Usuario o clave incorrectos.")


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)


@app.get("/", response_class=HTMLResponse)
def tablero(request: Request):
    user(request)
    con = db()
    u = con.execute("SELECT MAX(fecha) FROM venta_hora").fetchone()[0]
    if not u:
        return RedirectResponse("/cargar", 303)
    ultimo = date.fromisoformat(u)
    pre = TB.presets(ultimo)
    q = request.query_params
    clave = q.get("p") or ("" if q.get("desde") else "semana_pasada")
    try:
        if clave in pre:
            nombre, d0, d1 = pre[clave]
        else:
            d0, d1 = date.fromisoformat(q["desde"]), date.fromisoformat(q["hasta"])
            if d1 < d0:
                d0, d1 = d1, d0
            d1 = min(d1, d0 + timedelta(days=730))
            nombre, clave = "Rango elegido", ""
    except (KeyError, ValueError):
        nombre, d0, d1 = pre["semana_pasada"]
        clave = "semana_pasada"
    cfg = F.load_cfg(con, d1.isoformat())
    cache = {}
    cmp = "anio" if q.get("cmp") == "anio" else "periodo"
    a0, a1 = TB.anterior(d0, d1, cmp)
    r, ra = TB.resultado(con, cfg, d0, d1, cache), TB.resultado(con, cfg, a0, a1, cache)
    n = (d1 - d0).days + 1
    dias = TB.agrupar(TB.ventas_diarias(con, cfg, d0, d1), n)
    dias_a = TB.agrupar(TB.ventas_diarias(con, cfg, a0, a1), n)
    sin_comparar = cmp == "anio" and not ra["neto"]
    var = lambda x, y: I.variacion(x, y) if y and not sin_comparar else None
    sem = TB.semanas(con, cfg, ultimo)
    sem_tarjetas = sem if d1 >= ultimo - timedelta(days=ultimo.weekday() + 1) else TB.semanas(con, cfg, min(d1, ultimo) + timedelta(days=7))
    sparks = {k: Markup(TB.spark([s[k] for s in sem_tarjetas])) for k in ("neto", "costo_venta", "margen", "sueldos_gastos", "ganancia", "tickets")}
    base_q = f"p={clave}" if clave else f"desde={d0}&hasta={d1}"
    meta_mes = None
    if d0.day == 1 and d0.month == d1.month and d0.year == d1.year and ((d1 + timedelta(days=1)).day == 1 or d1 >= ultimo):
        per_m = d0.strftime("%Y-%m")
        mv = MT.leer(con, per_m).get("venta")
        if mv:
            rit = MT.ritmo(con, F.load_cfg(con), per_m)
            real = rit["venta"] if rit else r["neto"]
            meta_mes = {"meta": mv, "real": real, "pct": round(100 * real / mv), "rit": rit, "per": per_m}
    datos = {"dias": dias, "dias_a": [x["neto"] for x in dias_a][:len(dias)],
             "cat": TB.por_categoria(con, cfg, d0, d1), "medios": TB.medios(con, d0, d1),
             "top": TB.top_productos(con, cfg, d0, d1), "semanas": sem,
             "cascada": [r["neto"], r["costo_venta"], r["sueldos"], r["gastos"], r["ganancia"]]}
    return page(request, "tablero.html", r=r, ra=ra, pre=pre, clave=clave, nombre=nombre, d0=d0, d1=d1, a0=a0, a1=a1,
                ultimo=ultimo, datos=json.dumps(datos, default=str), n=n,
                var={k: var(r[k], ra[k]) for k in ("neto", "costo_venta", "margen", "ganancia", "tickets", "ticket")},
                incompleto=d1 > ultimo, cmp=cmp, meta_mes=meta_mes, sin_comparar=sin_comparar, sparks=sparks, base_q=base_q,
                sem_desde=sem_tarjetas[0]["etiqueta"] if sem_tarjetas else "")


@app.get("/mes", response_class=HTMLResponse)
def resumen(request: Request):
    user(request)
    con = db()
    cfg = F.load_cfg(con)
    ps = F.periodos(con)
    if not ps:
        return RedirectResponse("/cargar", 303)
    hoy = date.today().strftime("%Y-%m")
    cerrados = [x for x in ps if x < hoy] or ps
    per = request.query_params.get("periodo") or cerrados[-1]
    if per not in ps:
        per = cerrados[-1]
    parcial = per >= hoy
    p = F.pnl_mes(con, per, cfg)
    ctx = I.contexto(con, cfg, per, ps)
    v = I.veredicto(p, parcial)
    serie = [F.pnl_mes(con, x, cfg) for x in ps[-12:]]
    horas = F.ventas_por_hora(con, per)
    chart = {"labels": [f"{MESES[int(x['periodo'][5:7]) - 1]} {x['periodo'][2:4]}" for x in serie],
             "ventas": [x["ventas"]["neto"] for x in serie],
             "ganancia": [x["resultado_operacional"] if x["tiene_compras"] and x["periodo"] < hoy else None for x in serie],
             "horas": horas}
    pico = max(horas, key=lambda h: h["bruto"])["hora"] if horas else None
    dia = round(p["ventas"]["bruto"] / p["ventas"]["dias_abiertos"]) if p["ventas"]["dias_abiertos"] else 0
    eq = p["equilibrio"]["venta_bruta_dia"]
    kpi = {"var_venta": I.variacion(p["ventas"]["neto"], ctx["prev"]["ventas"]["neto"]) if ctx["prev"] else None,
           "var_anio": I.variacion(p["ventas"]["neto"], ctx["anio"]["ventas"]["neto"]) if ctx["anio"] else None,
           "insumos": I.por_mil(p, p["costo_venta"]), "ins_sue": I.por_mil(p, p["costo_venta"] + p["personal"]),
           "e_insumos": I.estado(I.por_mil(p, p["costo_venta"]), 350, 400, False),
           "e_ins_sue": I.estado(I.por_mil(p, p["costo_venta"] + p["personal"]), 600, 650, False),
           "dia": dia, "e_dia": ("ok" if dia >= eq else "bad") if eq else None,
           "dif_prom": p["resultado_operacional"] - ctx["prom_ganancia"] if ctx["prom_ganancia"] is not None else None}
    top = F.margen_productos(con, per, per, F.load_cfg(con, F.fin_de_mes(per)))
    i = ps.index(per)
    metas = MT.leer(con, per)
    rit = MT.ritmo(con, cfg, per, p)
    return page(request, "resumen.html", metas=metas, rit=rit, cmp_metas=MT.comparar(con, cfg, per, p, metas, rit),
                lista=MT.checklist(con, cfg, per, p) if not parcial else [], msg=request.session.pop("msg", None), p=p, per=per, ps=ps, v=v, des=I.desglose(p) if v["con_resultado"] else [],
                ctx=ctx, kpi=kpi, pend=I.pendientes(con, cfg, ps), chart=json.dumps(chart), pico=pico,
                medios=F.medios_pago(con, per), top=top, parcial=parcial,
                ant=ps[i - 1] if i > 0 else None, sig=ps[i + 1] if i < len(ps) - 1 else None)


@app.post("/metas")
async def metas_post(request: Request):
    f = await form(request)
    per = str(f.get("periodo", ""))[:7]
    num = lambda k: (lambda v: float(v) if v else None)(str(f.get(k, "")).replace(".", "").replace(",", ".").replace("$", "").replace("%", "").strip())
    try:
        vals = {"venta": num("venta"), "costo_venta_pct": num("costo_venta_pct"), "resultado": num("resultado")}
    except ValueError:
        request.session["msg"] = "No entendí uno de los números. Escríbelos sin letras, por ejemplo 4500000."
        return RedirectResponse(f"/mes?periodo={per}#metas", 303)
    MT.guardar(db(), per, vals, todos=f.get("alcance") == "todos")
    request.session["msg"] = "Metas guardadas."
    return RedirectResponse(f"/mes?periodo={per}#metas", 303)


@app.get("/resultados", response_class=HTMLResponse)
def resultados(request: Request):
    user(request)
    if request.query_params.get("vista") != "tabla":
        return estado_resultados(request)
    con = db()
    cfg = F.load_cfg(con)
    desde, hasta, lista, ps = rango(request, con)
    cols = [F.pnl_mes(con, x, cfg) for x in lista]
    grupos = [("costo_venta", "Insumos y productos", "insumos"), ("personal", "Sueldos", "sueldos"),
              ("gasto_operacional", "Gastos del local", "gastos_local"),
              ("sin_clasificar", "Facturas sin categoría", "sin_categoria")]
    cats = {g: sorted({c for p in cols for c in p["lineas"].get(g, {})}) for g, _, _ in grupos}
    hoy = date.today().strftime("%Y-%m")
    for p in cols:
        p["completo"] = p["tiene_compras"] and p["periodo"] < hoy
    comp = [p for p in cols if p["completo"]]
    n = len(comp) or 1
    prom = {k: round(sum(p[k] for p in comp) / n) for k in
            ("costo_venta", "personal", "gasto_operacional", "sin_clasificar", "margen_bruto", "resultado_operacional")}
    prom.update(ventas=round(sum(p["ventas"]["neto"] for p in comp) / n), n=len(comp))
    return page(request, "resultados.html", cols=cols, grupos=grupos, lineas=cats, desde=desde, hasta=hasta, ps=ps,
                prom=prom, tasa=F.tasa_tarjetas(con, cfg))


def estado_resultados(request):
    con = db()
    cfg = F.load_cfg(con)
    ps = F.periodos(con)
    if not ps:
        return RedirectResponse("/cargar", 303)
    hoy = date.today().strftime("%Y-%m")
    con_compras = [x for x in ps if x < hoy and con.execute("SELECT 1 FROM compra WHERE fecha_doc >= ? AND fecha_doc < ? LIMIT 1",
                                                             F.mes_rango(x)).fetchone()]
    per = request.query_params.get("periodo") or (con_compras[-1] if con_compras else ps[-1])
    if per not in ps:
        per = ps[-1]
    est = E.estado(con, cfg, per, ps)
    desde_graf = con_compras[0] if con_compras else ps[0]
    serie = [F.pnl_mes(con, x, cfg) for x in ps[-12:] if x >= desde_graf]
    chart = {"labels": [f"{MESES[int(x['periodo'][5:7]) - 1]} {x['periodo'][2:4]}" for x in serie],
             "ventas": [x["ventas"]["neto"] for x in serie],
             "resultado": [x["resultado_operacional"] if x["tiene_compras"] and x["periodo"] < hoy else None for x in serie],
             "pct": [x["pct"]["resultado"] if x["tiene_compras"] and x["periodo"] < hoy else None for x in serie],
             "actual": [x["periodo"] == per for x in serie]}
    i = ps.index(per)
    det = E.detalle(con, cfg, per)
    det.update(E.detalle_costo(est["cur"]))
    return page(request, "estado.html", e=est, per=per, ps=ps, det=det, comp=E.composicion(est["cur"]),
                puente=E.puente(con, cfg, per, ps), compras=est["cur"]["compras"],
                parcial=per >= hoy, ant=ps[i - 1] if i else None, sig=ps[i + 1] if i < len(ps) - 1 else None,
                anio_ant=E.menos(per, 12), chart=json.dumps(chart), colores=E.COLOR)


@app.get("/productos", response_class=HTMLResponse)
def productos(request: Request):
    user(request)
    con = db()
    desde, hasta, _, ps = rango(request, con, 3)
    r = F.margen_productos(con, desde, hasta, F.load_cfg(con, F.fin_de_mes(hasta)))
    puntos = [{"x": d["cantidad"], "y": d["margen_unit"], "n": d["producto"], "c": d["contribucion"]}
              for d in r["productos"] if d["costo"] is not None]
    conteo = {g: sum(1 for d in r["productos"] if d["clase"] == g) for g in GRUPO_NOMBRE}
    conteo["sin"] = sum(1 for d in r["productos"] if d["costo"] is None)
    S.asegurar(con)
    segs = {x[0] for x in con.execute("SELECT DISTINCT producto FROM evento WHERE estado='activo' AND producto IS NOT NULL")}
    nuevos = [dict(x) for x in con.execute("SELECT * FROM producto_manual m WHERE NOT EXISTS "
                                           "(SELECT 1 FROM venta_producto v WHERE v.producto=m.producto) ORDER BY creado DESC")]
    cats_fudo = [x[0] for x in con.execute("SELECT DISTINCT categoria FROM venta_producto WHERE categoria IS NOT NULL ORDER BY 1")]
    return page(request, "productos.html", r=r, desde=desde, hasta=hasta, ps=ps, conteo=conteo, segs=segs,
                todos=todos_productos(con), nuevos=nuevos, cats_fudo=cats_fudo,
                of=O.resumen(con, F.load_cfg(con)),
                grupo=request.query_params.get("grupo", ""),
                chart=json.dumps({"puntos": puntos, "umbral": r["umbral"]}))


@app.post("/productos/costo")
async def producto_costo(request: Request):
    f = await form(request)
    con = db()
    prod, costo = str(f["producto"]), str(f.get("costo", "")).replace(".", "").strip()
    F.registrar_costo(con, prod, int(costo) if costo else None, "manual")
    con.commit()
    return RedirectResponse(volver(f, "/productos"), 303)


@app.get("/receta", response_class=HTMLResponse)
def receta(request: Request, p: str):
    user(request)
    con = db()
    lineas = [dict(r) for r in con.execute(
        "SELECT i.id, i.nombre, i.unidad, r.cantidad, r.cantidad*i.precio_formato/i.cantidad_formato costo "
        "FROM receta r JOIN insumo i ON i.id=r.insumo_id WHERE r.producto=? ORDER BY costo DESC", (p,))]
    fijo = con.execute("SELECT * FROM producto_costo WHERE producto=?", (p,)).fetchone()
    ult = con.execute("SELECT MAX(fecha) FROM venta_producto WHERE producto=?", (p,)).fetchone()[0]
    venta = con.execute("SELECT SUM(bruto) b, SUM(cantidad) q FROM venta_producto WHERE producto=? AND fecha >= date(?, '-28 day')",
                        (p, ult or "2000-01-01")).fetchone()
    manual = con.execute("SELECT * FROM producto_manual WHERE producto=?", (p,)).fetchone()
    if not ult and not manual and not lineas and not fijo:
        raise HTTPException(404, "Ese producto no existe. Créalo desde Productos.")
    iva = F.load_cfg(con)["empresa"]["iva"]
    fudo = round(venta["b"] / venta["q"]) if venta["q"] else None
    precio_bruto = (manual["precio"] if manual and manual["precio"] else None) or fudo
    precio = round(precio_bruto / (1 + iva)) if precio_bruto else None
    total = F.costo_receta(con, p)
    costo = total if total is not None else (fijo["costo"] if fijo else None)
    vendidos = con.execute("SELECT SUM(cantidad) FROM venta_producto WHERE producto=? AND fecha >= date(?, '-89 day')",
                           (p, ult or "2000-01-01")).fetchone()[0]
    return page(request, "receta.html", producto=p, lineas=lineas, total=total, fijo=fijo, costo=costo, iva=iva,
                precio=precio, precio_bruto=precio_bruto, precio_fudo=fudo, manual=manual, en_fudo=bool(ult),
                vendidos=round(vendidos or 0), todos=todos_productos(con), activo="/productos",
                insumos=[dict(r) for r in con.execute("SELECT * FROM insumo ORDER BY nombre")])


def todos_productos(con):
    return [r[0] for r in con.execute("SELECT producto FROM (SELECT DISTINCT producto FROM venta_producto UNION "
                                      "SELECT producto FROM producto_manual) WHERE producto <> '-' ORDER BY producto")]


@app.post("/producto")
async def producto_post(request: Request):
    """Crear un producto (antes de que exista en FUDO) o cambiar su precio de carta en el panel."""
    f = await form(request)
    con = db()
    nombre = " ".join(str(f.get("producto", "")).split())[:80]
    if not nombre:
        return RedirectResponse("/productos", 303)
    precio = str(f.get("precio", "")).replace(".", "").replace("$", "").strip()
    precio = int(precio) if precio.isdigit() else None
    act = con.execute("SELECT * FROM producto_manual WHERE producto=?", (nombre,)).fetchone()
    cat = str(f.get("categoria") or (act["categoria"] if act else "") or "").strip()[:60] or None
    con.execute("INSERT OR REPLACE INTO producto_manual VALUES (?,?,?,?)",
                (nombre, cat, precio, act["creado"] if act else date.today().isoformat()))
    con.commit()
    return RedirectResponse(f"/receta?p={quote(nombre)}", 303)


@app.post("/receta")
async def receta_post(request: Request):
    f = await form(request)
    con, prod = db(), str(f["producto"])
    if f.get("accion") == "nuevo_insumo":         # crea el insumo y lo agrega a la receta en un paso
        iid = con.execute("INSERT INTO insumo(nombre,unidad,cantidad_formato,precio_formato,actualizado) VALUES (?,?,?,?,?)",
                          (str(f["nombre"]).strip()[:60], str(f["unidad"]).strip()[:10] or "un",
                           float(str(f["formato"]).replace(",", ".")), int(str(f["precio"]).replace(".", "").replace("$", "")),
                           date.today().isoformat())).lastrowid
        F.registrar_insumo(con, iid)
        con.execute("INSERT OR REPLACE INTO receta VALUES (?,?,?)", (prod, iid, float(str(f["cantidad"]).replace(",", "."))))
        con.commit()
        return RedirectResponse(f"/receta?p={quote(prod)}", 303)
    if f.get("accion") == "quitar":
        con.execute("DELETE FROM receta WHERE producto=? AND insumo_id=?", (prod, int(f["insumo_id"])))
    else:
        con.execute("INSERT OR REPLACE INTO receta VALUES (?,?,?)",
                    (prod, int(f["insumo_id"]), float(str(f["cantidad"]).replace(",", "."))))
    con.commit()
    return RedirectResponse(f"/receta?p={quote(prod)}", 303)


@app.get("/insumos", response_class=HTMLResponse)
def insumos(request: Request):
    user(request)
    con = db()
    rows = [dict(r) for r in con.execute(
        "SELECT i.*, (SELECT COUNT(*) FROM receta r WHERE r.insumo_id=i.id) usos FROM insumo i ORDER BY nombre")]
    u = con.execute("SELECT MAX(fecha) FROM venta_producto").fetchone()[0] or date.today().isoformat()
    desde = (date.fromisoformat(u) - timedelta(days=89)).isoformat()
    uds = {r["producto"]: r["q"] / 3 for r in con.execute(
        "SELECT producto, SUM(cantidad) q FROM venta_producto WHERE fecha >= ? GROUP BY producto", (desde,))}
    origen = {}
    for v in con.execute("SELECT * FROM vinculo WHERE tipo='insumo'"):
        origen.setdefault(int(v["destino"]), set()).add(PV.nombre(con, v["rut"]))
    for i in rows:
        uso = sum(uds.get(r["producto"], 0) * r["cantidad"] for r in con.execute("SELECT * FROM receta WHERE insumo_id=?", (i["id"],)))
        i["gasto_mes"] = round(uso * i["precio_formato"] / i["cantidad_formato"])
        i["impacto10"] = round(i["gasto_mes"] * 0.10)
        i["origen"] = ", ".join(sorted(origen.get(i["id"], [])))
    return page(request, "insumos.html", rows=sorted(rows, key=lambda i: -i["gasto_mes"]))


@app.post("/insumos")
async def insumos_post(request: Request):
    f = await form(request)
    con = db()
    vals = (str(f["nombre"]).strip(), str(f["unidad"]).strip(), float(str(f["cantidad"]).replace(",", ".")),
            int(str(f["precio"]).replace(".", "")), date.today().isoformat())
    if f.get("id"):
        iid = int(f["id"])
        con.execute("UPDATE insumo SET nombre=?, unidad=?, cantidad_formato=?, precio_formato=?, actualizado=? WHERE id=?",
                    (*vals, iid))
    else:
        iid = con.execute("INSERT INTO insumo(nombre,unidad,cantidad_formato,precio_formato,actualizado) VALUES (?,?,?,?,?)",
                          vals).lastrowid
    F.registrar_insumo(con, iid)
    con.commit()
    return RedirectResponse("/insumos", 303)


@app.get("/compras", response_class=HTMLResponse)
def compras(request: Request):
    user(request)
    con = db()
    pers = [r[0] for r in con.execute("SELECT DISTINCT periodo FROM compra ORDER BY 1 DESC")]
    per = request.query_params.get("periodo") or (pers[0] if pers else "")
    rows = [dict(r) for r in con.execute(
        "SELECT c.*, COALESCE(c.categoria, p.categoria) cat FROM compra c LEFT JOIN proveedor p ON p.rut=c.rut "
        "WHERE c.periodo=? ORDER BY c.fecha_doc", (per,))]
    prov = PV.resumen(con)
    alias = {r["rut"]: r["alias"] for r in con.execute("SELECT * FROM proveedor_alias")}
    gastos = [dict(r) for r in con.execute("SELECT * FROM gasto_manual ORDER BY fecha DESC, id DESC LIMIT 60")]
    return page(request, "compras.html", rows=rows, prov=prov, per=per, pers=pers, gastos=gastos, alias=alias,
                movs=PV.movimientos(con))


@app.get("/proveedor", response_class=HTMLResponse)
def proveedor(request: Request, rut: str):
    user(request)
    con = db()
    info = con.execute("SELECT MAX(razon_social) razon, COUNT(*) n, MIN(fecha_doc) desde, MAX(fecha_doc) hasta, "
                       "SUM(CASE WHEN tipo_doc=61 THEN -neto ELSE neto END) neto FROM compra WHERE rut=?", (rut,)).fetchone()
    if not info["n"] and not con.execute("SELECT 1 FROM factura_linea WHERE rut=?", (rut,)).fetchone():
        raise HTTPException(404, "Proveedor no encontrado")
    cat = con.execute("SELECT categoria FROM proveedor WHERE rut=?", (rut,)).fetchone()
    alias = con.execute("SELECT alias FROM proveedor_alias WHERE rut=?", (rut,)).fetchone()
    prods = PV.productos(con, rut)
    facturas = [dict(r) for r in con.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM factura_linea l WHERE l.tipo_doc=c.tipo_doc AND l.rut=c.rut AND l.folio=c.folio) lineas "
        "FROM compra c WHERE c.rut=? ORDER BY c.fecha_doc DESC LIMIT 12", (rut,))]
    for fa in facturas:
        fa["detalle"] = [dict(r) for r in con.execute(
            "SELECT COALESCE(texto, descripcion) d, cantidad, precio_neto, total_neto FROM factura_linea "
            "WHERE tipo_doc=? AND rut=? AND folio=? ORDER BY n", (fa["tipo_doc"], rut, fa["folio"]))]
    lista_p, lista_i = PV.destinos(con)
    hist = {"series": [{"n": x["texto"], "d": [{"x": h["fecha"], "y": h["precio"]} for h in x["historial"]]}
                       for x in sorted(prods, key=lambda x: -x["veces"])[:6] if len(x["historial"]) > 1],
            "meses": PV.compras_por_mes(con, rut)}
    return page(request, "proveedor.html", rut=rut, info=info, cat=cat["categoria"] if cat else None,
                alias=alias["alias"] if alias else "", nombre=PV.nombre(con, rut, info["razon"]), prods=prods,
                facturas=facturas, lista_p=lista_p, lista_i=lista_i, chart=json.dumps(hist), activo="/compras",
                hay_hist=bool(hist["series"]),
                msg=request.session.pop("msg", None))


@app.post("/proveedor/alias")
async def proveedor_alias(request: Request):
    f = await form(request)
    rut = str(f["rut"])
    PV.poner_alias(db(), rut, str(f.get("alias", "")))
    return RedirectResponse(f"/proveedor?rut={rut}", 303)


@app.post("/proveedor/vinculos")
async def proveedor_vinculos(request: Request):
    f = await form(request)
    con, rut, cambios, n = db(), str(f["rut"]), [], 0
    for clave in f.getlist("clave"):
        dest = str(f.get(f"d:{clave}", ""))
        div = str(f.get(f"x:{clave}", "1")).replace(",", ".").strip() or "1"
        try:
            div = float(div)
        except ValueError:
            div = 1.0
        tipo, _, destino = dest.partition(":")
        if dest == "nuevo:insumo":
            iid = PV.crear_insumo(con, rut, clave)
            if not iid:
                continue
            tipo, destino = "insumo", str(iid)
        actual = con.execute("SELECT tipo, destino, divisor FROM vinculo WHERE rut=? AND clave=?", (rut, clave)).fetchone()
        if actual and (actual["tipo"], actual["destino"], actual["divisor"]) == (tipo, destino, div):
            continue
        if not dest and not actual:
            continue
        cambios += PV.vincular(con, rut, clave, tipo, destino, div)
        n += 1
    request.session["msg"] = (f"Guardé {n} vínculo{'s' if n != 1 else ''}. Los costos de esos productos ya se toman de las facturas."
                              if n else "No había cambios que guardar.")
    return RedirectResponse(f"/proveedor?rut={rut}", 303)


@app.get("/stock", response_class=HTMLResponse)
def stock_page(request: Request):
    user(request)
    con = db()
    cfg = F.load_cfg(con)
    filas = ST.estado(con, cfg)
    aj = [a for a in ST.ajustes(con, cfg) if abs(a["dif"]) >= 1][:60]
    ult = con.execute("SELECT MAX(fecha) FROM stock_conteo").fetchone()[0]
    sin_vinculo = [r[0] for r in con.execute(
        "SELECT DISTINCT s.producto FROM stock_conteo s WHERE NOT EXISTS (SELECT 1 FROM vinculo v WHERE v.tipo='producto' AND v.destino=s.producto) "
        "ORDER BY 1")]
    mes = date.today().strftime("%Y-%m")
    return page(request, "stock.html", filas=filas, ajustes=aj, ultimo=ult, sin_vinculo=sin_vinculo,
                valor=sum(f["valor"] or 0 for f in filas), merma=ST.merma_mes(con, mes, cfg), mes=mes,
                dias=(date.today() - date.fromisoformat(ult)).days if ult else None)


@app.get("/seguimiento", response_class=HTMLResponse)
def seguimiento(request: Request):
    user(request)
    con = db()
    cfg = F.load_cfg(con)
    S.asegurar(con)
    tipo = request.query_params.get("tipo") or None
    prod = request.query_params.get("producto") or None
    grupos = S.lista(con, cfg, tipo if tipo in S.TIPOS else None, prod)
    if not prod:                                   # lo que tiene pocos datos va al final, plegado
        grupos = [g for g in grupos if not g["debil"]] + [g for g in grupos if g["debil"]]
    n_debiles = 0 if prod else sum(1 for g in grupos if g["debil"])
    conteo = {t: con.execute("SELECT COUNT(*) FROM evento WHERE estado='activo' AND tipo=?", (t,)).fetchone()[0] for t in S.TIPOS}
    prods = [r[0] for r in con.execute("SELECT DISTINCT producto FROM venta_producto ORDER BY producto")]
    return page(request, "seguimiento.html", n_debiles=n_debiles, n_fuertes=len(grupos) - n_debiles, grupos=grupos, tipo=tipo, producto=prod, conteo=conteo, tipos=S.TIPOS,
                prods=prods, hoy=date.today().isoformat(), chart=json.dumps([g["semanas"] for g in grupos]))


@app.post("/seguimiento")
async def seguimiento_post(request: Request):
    f = await form(request)
    con = db()
    acc = f.get("accion")
    if acc == "nueva":
        titulo = str(f.get("titulo", "")).strip()[:140]
        fecha = str(f.get("fecha") or date.today().isoformat())[:10]
        if titulo:
            S.crear(con, fecha, "decision", str(f.get("producto") or "") or None, titulo, {}, "manual",
                    str(f.get("nota", "")).strip()[:500] or None)
    elif acc in ("ocultar", "nota"):
        ids = [int(x) for x in str(f.get("ids", "")).split(",") if x.isdigit()]
        for i in ids:
            if acc == "ocultar":
                con.execute("UPDATE evento SET estado='oculto' WHERE id=?", (i,))
            else:
                con.execute("UPDATE evento SET nota=? WHERE id=?", (str(f.get("nota", "")).strip()[:500] or None, i))
    con.commit()
    return RedirectResponse(volver(f, "/seguimiento"), 303)


@app.post("/compras/proveedor")
async def compras_proveedor(request: Request):
    f = await form(request)
    con, cat = db(), str(f.get("categoria", ""))
    if cat in F.CATEGORIAS:
        con.execute("INSERT INTO proveedor(rut, nombre, categoria) VALUES (?,?,?) "
                    "ON CONFLICT(rut) DO UPDATE SET categoria=excluded.categoria", (str(f["rut"]), str(f.get("nombre", "")), cat))
    else:
        con.execute("DELETE FROM proveedor WHERE rut=?", (str(f["rut"]),))
    con.commit()
    return RedirectResponse(volver(f, "/compras"), 303)


@app.get("/turnos", response_class=HTMLResponse)
def turnos_page(request: Request):
    return costos_fijos(request, "turnos.html")


@app.get("/costos-fijos", response_class=HTMLResponse)
def costos_fijos(request: Request, plantilla: str = "costos_fijos.html"):
    user(request)
    if plantilla not in ("costos_fijos.html", "turnos.html"):
        plantilla = "costos_fijos.html"
    con = db()
    cfg = F.load_cfg(con)
    ps0 = F.periodos(con)
    serv = [x for x in GS.servicios_mes(con, 8) if not ps0 or x["periodo"] >= ps0[0]]
    tur = GS.analisis_turnos(con)
    pagos = {r["clave"][5:]: int(float(r["valor"])) for r in con.execute("SELECT * FROM parametro WHERE clave LIKE 'pago_%'")}
    for x in tur["personas"]:
        x["pago"] = pagos.get(x["persona"])
        if x["pago"] and x["horas_semana"]:
            x["costo_hora"] = round(x["pago"] / (x["horas_semana"] * 4.33))
            x["veces"] = round(x["por_hora"] / x["costo_hora"], 1) if x["costo_hora"] else None
    horas = {d: GS.horas_por_turno(con, d) for d in range(6)}
    return page(request, plantilla, serv=serv, tur=tur, esp=GS.esperado(con), dias=GS.DIAS,
                graf=json.dumps({"serv": serv, "horas": horas}), msg=request.session.pop("msg", None),
                rows=[dict(r) for r in con.execute("SELECT * FROM costo_fijo ORDER BY categoria, nombre")],
                ppm=cfg["empresa"]["ppm_tasa"] * 100, emp=cfg["empresa"],
                calc=F.tasa_tarjetas(con, dict(cfg, empresa=dict(cfg["empresa"], comision_debito=None, comision_credito=None))))


@app.post("/turnos")
async def turnos_post(request: Request):
    f = await form(request)
    con = db()
    acc = f.get("accion")
    if acc == "borrar":
        con.execute("DELETE FROM turno WHERE id=?", (int(f["id"]),))
    elif acc == "agregar":
        import re as _re
        desde, hasta = str(f.get("desde", "")), str(f.get("hasta", ""))
        if not (_re.fullmatch(r"\d{2}:\d{2}", desde) and _re.fullmatch(r"\d{2}:\d{2}", hasta)) or desde >= hasta:
            request.session["msg"] = "Revisa las horas del turno (formato 09:00 y que termine después de empezar)."
            return RedirectResponse("/turnos", 303)
        dias = [int(d) for d in f.getlist("dia") if str(d).isdigit() and 0 <= int(d) <= 6]
        for d in dias:
            con.execute("INSERT INTO turno(persona,dia,desde,hasta,en_resultado) VALUES (?,?,?,?,?)",
                        (str(f["persona"]).strip()[:30], d, desde, hasta, 1 if f.get("en_resultado") else 0))
    elif acc == "pago":
        v = str(f.get("pago", "")).replace(".", "").replace("$", "").strip()
        clave = "pago_" + str(f["persona"]).strip()[:30]
        if v.isdigit():
            con.execute("INSERT OR REPLACE INTO parametro VALUES (?,?)", (clave, v))
        else:
            con.execute("DELETE FROM parametro WHERE clave=?", (clave,))
    elif acc == "esperado":
        for k in ("luz", "agua"):
            v = str(f.get(k, "")).replace(".", "").replace("$", "").strip()
            if v.isdigit():
                con.execute("INSERT OR REPLACE INTO parametro VALUES (?,?)", (f"esperado_{k}", v))
    con.commit()
    return RedirectResponse("/costos-fijos#cuentas" if acc == "esperado" else "/turnos", 303)


@app.post("/costos-fijos")
async def costos_fijos_post(request: Request):
    f = await form(request)
    con = db()
    if f.get("accion") == "ppm":
        con.execute("INSERT OR REPLACE INTO parametro VALUES ('ppm_tasa', ?)",
                    (str(float(str(f["ppm"]).replace(",", ".")) / 100),))
    elif f.get("accion") == "tarjetas":
        for k in ("comision_debito", "comision_credito"):
            v = str(f.get(k, "")).replace(",", ".").strip()
            con.execute("INSERT OR REPLACE INTO parametro VALUES (?, ?)", (k, str(float(v)) if v else ""))
    elif f.get("accion") == "borrar":
        con.execute("DELETE FROM costo_fijo WHERE id=?", (int(f["id"]),))
    else:
        vals = (str(f["nombre"]).strip(), str(f["categoria"]), int(str(f["monto"]).replace(".", "")),
                str(f["desde"]), str(f.get("hasta") or "") or None, 1 if f.get("estimado") else 0)
        if vals[1] not in F.CATEGORIAS:
            raise HTTPException(400, "Categoría inválida")
        if f.get("id"):
            con.execute("UPDATE costo_fijo SET nombre=?, categoria=?, monto=?, desde=?, hasta=?, estimado=? WHERE id=?", (*vals, int(f["id"])))
        else:
            con.execute("INSERT INTO costo_fijo(nombre,categoria,monto,desde,hasta,estimado) VALUES (?,?,?,?,?,?)", vals)
    con.commit()
    return RedirectResponse("/costos-fijos", 303)


@app.get("/impuestos", response_class=HTMLResponse)
def impuestos(request: Request):
    user(request)
    con = db()
    cfg = F.load_cfg(con)
    _, _, lista, _ = rango(request, con, 12)
    rows = [{"periodo": x, **F.iva_mes(con, x, cfg),
             "tiene_compras": bool(con.execute("SELECT 1 FROM compra WHERE periodo=? LIMIT 1", (x,)).fetchone())}
            for x in reversed(lista)]
    return page(request, "impuestos.html", rows=rows, ppm=cfg["empresa"]["ppm_tasa"] * 100, ap=IVA.apartar(con, cfg),
                cuadre=IVA.cuadre(con, cfg), perdido=IVA.perdido(con), msg=request.session.pop("msg", None))


@app.post("/f29")
async def f29_post(request: Request):
    f = await form(request)
    con = db()
    if f.get("accion") == "tasa":
        con.execute("INSERT OR REPLACE INTO parametro VALUES ('ppm_tasa', ?)", (str(float(str(f["tasa"]).replace(",", ".")) / 100),))
        con.commit()
        request.session["msg"] = "Tasa de PPM actualizada."
        return RedirectResponse("/impuestos", 303)
    num = lambda k: int(str(f.get(k) or "0").replace(".", "").replace("$", "").strip() or 0)
    try:
        IVA.guardar_f29(con, {"periodo": str(f.get("periodo", "")), "debito": num("debito"), "credito": num("credito"),
                              "ppm": num("ppm"), "total": num("total"), "ventas_neto": num("ventas_neto") or None})
        request.session["msg"] = "F29 guardado."
    except ValueError as e:
        request.session["msg"] = str(e)
    return RedirectResponse("/impuestos", 303)


@app.get("/decisiones", response_class=HTMLResponse)
def decisiones(request: Request):
    user(request)
    con = db()
    cfg = F.load_cfg(con)
    ps = F.periodos(con)
    if not ps:
        return RedirectResponse("/cargar", 303)
    hoy = date.today().strftime("%Y-%m")
    cerrados = [x for x in ps if x < hoy] or ps
    desde, hasta = cerrados[max(len(cerrados) - 3, 0)], cerrados[-1]
    dias, mapa = A.por_dia_semana(con), A.mapa_horas(con)
    precios, tend = A.precios_a_revisar(con, F.load_cfg(con), desde, hasta), A.tendencia_productos(con)
    sim = SIM.base(con, cfg)
    if sim:
        sim["elasticidad"] = SIM.elasticidad(con, cfg)
    return page(request, "decisiones.html", sim=sim, sim_json=json.dumps(sim, default=str), dias=dias, mapa=mapa, est=A.estimacion(con), precios=precios,
                tend=tend, rec=A.recomendaciones(dias, mapa, precios, tend), desde=desde, hasta=hasta,
                ini=A.ventanas(con)[0])


@app.get("/bot", response_class=HTMLResponse)
def bot_pagina(request: Request):
    u = user(request)
    con = db()
    mes = date.today().strftime("%Y-%m")
    uso = con.execute("SELECT COUNT(*) n, COALESCE(SUM(entrada),0) e, COALESCE(SUM(salida),0) s FROM api_uso "
                      "WHERE fecha >= ?", (mes + "-01",)).fetchone()
    caps = [dict(r) | {"d": json.loads(r["datos"])} for r in con.execute(
        "SELECT * FROM captura ORDER BY id DESC LIMIT 25")]
    from . import claves
    est = claves.estado()
    return page(request, "bot.html", codigo=request.session.pop("codigo", None), uso=dict(uso),
                chats=[dict(r) for r in con.execute("SELECT * FROM telegram_chat ORDER BY creado")], caps=caps,
                bot_nombre=est["nombre"], claves=est, msgs=request.session.pop("msgs", []))


@app.post("/bot")
async def bot_post(request: Request):
    f = await form(request)
    con = db()
    if f.get("accion") == "codigo":
        from .bot import nuevo_codigo
        request.session["codigo"] = nuevo_codigo(con, request.session["user"])
    elif f.get("accion") == "claves":
        from . import claves
        token, clave = str(f.get("token", "")).strip(), str(f.get("clave", "")).strip()
        try:
            if not token and not clave:
                raise ValueError("No pegaste ninguna clave.")
            claves.guardar(token or None, clave or None)
            request.session["msgs"] = [("ok", "Claves guardadas y probadas. El bot se conecta en menos de un minuto.")]
        except (ValueError, OSError) as e:
            request.session["msgs"] = [("bad", str(e))]
        except Exception:
            request.session["msgs"] = [("bad", "No pude comprobar las claves: el servidor no logró conectarse. Intenta de nuevo.")]
    elif f.get("accion") == "desvincular":
        con.execute("DELETE FROM telegram_chat WHERE chat_id=?", (int(f["chat_id"]),))
        con.commit()
    return RedirectResponse("/bot", 303)


@app.post("/gastos/borrar")
async def gasto_borrar(request: Request):
    f = await form(request)
    con = db()
    con.execute("DELETE FROM gasto_manual WHERE id=?", (int(f["id"]),))
    con.commit()
    return RedirectResponse("/compras", 303)


@app.get("/ayuda", response_class=HTMLResponse)
def ayuda(request: Request):
    user(request)
    con = db()
    from . import ayuda as AY
    return page(request, "ayuda.html", rutina=AY.rutina(con), ej=AY.ejemplos(con))


@app.get("/cargar", response_class=HTMLResponse)
def cargar(request: Request):
    user(request)
    con = db()
    trabajos = [dict(r) for r in con.execute("SELECT * FROM trabajo ORDER BY id DESC LIMIT 3")]
    estado = {"ventas": con.execute("SELECT MIN(fecha), MAX(fecha) FROM venta_hora").fetchone(),
              "productos": con.execute("SELECT MIN(fecha), MAX(fecha) FROM venta_producto").fetchone(),
              "compras": [r[0] for r in con.execute("SELECT DISTINCT periodo FROM compra ORDER BY 1")],
              "lineas": con.execute("SELECT COUNT(*) FROM factura_linea").fetchone()[0]}
    return page(request, "cargar.html", estado=estado, msgs=request.session.pop("msgs", []), trabajos=trabajos,
                pendiente=_pendiente(request.session.get("pendiente")), cal=CG.calendario(con),
                en_curso=any(t["estado"] == "en curso" for t in trabajos))


PENDIENTES = Path(DB_PATH).parent / "pendientes"


def _post_importacion(con, tipos, msgs):
    if "productos" in tipos:
        n = S.detectar(con, avisar_desde=(date.today() - timedelta(days=30)).isoformat())
        if n:
            msgs.append(("ok", f"Detecté {len(n)} cambios nuevos en la carta (precios o productos). Míralos en Seguimiento."))


@app.post("/cargar")
async def cargar_post(request: Request):
    """PDF y ZIP se leen en segundo plano. Los Excel de FUDO y el CSV del SII quedan en espera con una vista previa
    de lo que van a cambiar, y se guardan al confirmar."""
    f = await form(request)
    con, msgs, lote, previas = db(), [], [], []
    token = secrets.token_hex(8)
    carpeta = PENDIENTES / token
    for up in f.getlist("archivos"):
        name = Path(getattr(up, "filename", "") or "").name
        if not name:
            continue
        data = await up.read()
        low = name.lower()
        try:
            if len(data) > (80 if low.endswith(".zip") else 20) * 1024 * 1024:
                raise ValueError("archivo demasiado grande (máximo 20 MB, o 80 MB si es ZIP)")
            if low.endswith(".pdf") or low.endswith(".zip"):
                lote.append((name, data))
            elif low.endswith(".toml"):
                F.seed_from_config(con, tomllib.loads(data.decode("utf-8")))
                msgs.append(("ok", f"{name}: configuración inicial cargada"))
            else:
                carpeta.mkdir(parents=True, exist_ok=True)
                ruta = carpeta / name
                ruta.write_bytes(data)
                previas.append(CG.vista_previa(con, name, str(ruta)))
        except Exception as e:  # se informa al usuario archivo por archivo
            msgs.append(("bad", f"{name}: {e}"))
    if lote:
        if not os.environ.get("ANTHROPIC_API_KEY") and not __import__("lazzy.claves", fromlist=["leer"]).leer().get("ANTHROPIC_API_KEY"):
            msgs.append(("bad", "Para leer facturas en PDF falta la clave de Anthropic (página Bot de Telegram)."))
        else:
            H.lanzar(DB_PATH, lote)
            msgs.append(("ok", "Estoy leyendo las facturas en segundo plano. El avance aparece aquí abajo; puedes seguir usando el panel."))
    if previas:          # la vista previa queda junto a los archivos; la sesión solo guarda el código
        (carpeta / "previa.json").write_text(json.dumps(previas, ensure_ascii=False))
        request.session["pendiente"] = token
    request.session["msgs"] = msgs
    return RedirectResponse("/cargar", 303)


def _pendiente(token):
    if not token or not isinstance(token, str) or not token.isalnum():
        return None
    f = PENDIENTES / token / "previa.json"
    return {"token": token, "previas": json.loads(f.read_text())} if f.exists() else None


@app.post("/cargar/confirmar")
async def cargar_confirmar(request: Request):
    f = await form(request)
    pend = _pendiente(request.session.pop("pendiente", None))
    con, msgs, tipos = db(), [], set()
    if pend and f.get("accion") == "guardar" and str(f.get("token")) == pend["token"]:
        carpeta = PENDIENTES / pend["token"]
        for x in pend["previas"]:
            ruta = carpeta / x["nombre"]
            try:
                t, m = CG.importar(con, x["nombre"], str(ruta))
                tipos.add(t)
                msgs.append(("ok", m))
            except Exception as e:
                msgs.append(("bad", f"{x['nombre']}: {e}"))
        _post_importacion(con, tipos, msgs)
    elif pend:
        msgs.append(("ok", "No se guardó nada."))
    if pend:
        import shutil
        shutil.rmtree(PENDIENTES / pend["token"], ignore_errors=True)
    request.session["msgs"] = msgs
    return RedirectResponse("/cargar", 303)
