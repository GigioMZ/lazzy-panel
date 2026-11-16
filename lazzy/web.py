"""Panel web. Ejecutar: uvicorn lazzy.web:app  (variables: LAZZY_DB, LAZZY_SECRET, LAZZY_HTTPS=1)."""
import json
import os
import secrets
import tempfile
import time
try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib
from datetime import date, timedelta
from pathlib import Path

import openpyxl
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
from . import historico as H
from . import proveedores as PV
from . import seguimiento as S
from .auth import check_password
from .db import connect
from .glosario import CAT_NOMBRE, CATEGORIAS_UI, GLOSARIO, GRUPO_NOMBRE
from .importers.fudo_report import import_fudo_productos, import_fudo_report
from .importers.rcv import import_rcv_compras

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
T.env.filters["cat"] = lambda c: CAT_NOMBRE.get(c, "Sin categoría") if c else "Sin categoría"
T.env.filters["grupo"] = lambda g: GRUPO_NOMBRE.get(g, "")


def tip(clave):
    """Botón «?» con la explicación en simple de un término del glosario."""
    nombre, texto = GLOSARIO[clave]
    return Markup('<button type="button" class="tip" aria-label="Qué significa {n}"><span class="tip-i" aria-hidden="true">?</span>'
                  '<span class="tip-t" role="tooltip"><b>{n}</b>{t}</span></button>').format(n=nombre, t=texto)


T.env.globals.update(tip=tip, G=GLOSARIO, CATS_UI=CATEGORIAS_UI, GRUPOS=GRUPO_NOMBRE)
NAV = [("Ver", [("/", "Tablero"), ("/mes", "Cierre del mes"), ("/decisiones", "Decisiones"), ("/seguimiento", "Seguimiento"), ("/resultados", "Mes a mes"), ("/productos", "Productos"),
                 ("/impuestos", "IVA")]),
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
    datos = {"dias": dias, "dias_a": [x["neto"] for x in dias_a][:len(dias)],
             "cat": TB.por_categoria(con, cfg, d0, d1), "medios": TB.medios(con, d0, d1),
             "top": TB.top_productos(con, cfg, d0, d1), "semanas": sem,
             "cascada": [r["neto"], r["costo_venta"], r["sueldos"], r["gastos"], r["ganancia"]]}
    return page(request, "tablero.html", r=r, ra=ra, pre=pre, clave=clave, nombre=nombre, d0=d0, d1=d1, a0=a0, a1=a1,
                ultimo=ultimo, datos=json.dumps(datos, default=str), n=n,
                var={k: var(r[k], ra[k]) for k in ("neto", "costo_venta", "margen", "ganancia", "tickets", "ticket")},
                incompleto=d1 > ultimo, cmp=cmp, sin_comparar=sin_comparar, sparks=sparks, base_q=base_q,
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
    return page(request, "resumen.html", p=p, per=per, ps=ps, v=v, des=I.desglose(p) if v["con_resultado"] else [],
                ctx=ctx, kpi=kpi, pend=I.pendientes(con, cfg, ps), chart=json.dumps(chart), pico=pico,
                medios=F.medios_pago(con, per), top=top, parcial=parcial,
                ant=ps[i - 1] if i > 0 else None, sig=ps[i + 1] if i < len(ps) - 1 else None)


@app.get("/resultados", response_class=HTMLResponse)
def resultados(request: Request):
    user(request)
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
    return page(request, "productos.html", r=r, desde=desde, hasta=hasta, ps=ps, conteo=conteo,
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
    venta = con.execute("SELECT SUM(bruto) b, SUM(cantidad) q FROM venta_producto WHERE producto=? AND fecha >= date('now','-90 day')", (p,)).fetchone()
    iva = F.load_cfg(con)["empresa"]["iva"]
    precio = round(venta["b"] / venta["q"] / (1 + iva)) if venta["q"] else None
    total = F.costo_receta(con, p)
    costo = total if total is not None else (fijo["costo"] if fijo else None)
    return page(request, "receta.html", producto=p, lineas=lineas, total=total, fijo=fijo, costo=costo, iva=iva,
                precio=precio, precio_bruto=round(venta["b"] / venta["q"]) if venta["q"] else None,
                vendidos=round(venta["q"] or 0), insumos=[dict(r) for r in con.execute("SELECT * FROM insumo ORDER BY nombre")])


@app.post("/receta")
async def receta_post(request: Request):
    f = await form(request)
    con, prod = db(), str(f["producto"])
    if f.get("accion") == "quitar":
        con.execute("DELETE FROM receta WHERE producto=? AND insumo_id=?", (prod, int(f["insumo_id"])))
    else:
        con.execute("INSERT OR REPLACE INTO receta VALUES (?,?,?)",
                    (prod, int(f["insumo_id"]), float(str(f["cantidad"]).replace(",", "."))))
    con.commit()
    return RedirectResponse(f"/receta?p={prod}", 303)


@app.get("/insumos", response_class=HTMLResponse)
def insumos(request: Request):
    user(request)
    rows = [dict(r) for r in db().execute(
        "SELECT i.*, (SELECT COUNT(*) FROM receta r WHERE r.insumo_id=i.id) usos FROM insumo i ORDER BY nombre")]
    return page(request, "insumos.html", rows=rows)


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


@app.get("/seguimiento", response_class=HTMLResponse)
def seguimiento(request: Request):
    user(request)
    con = db()
    cfg = F.load_cfg(con)
    if not con.execute("SELECT 1 FROM parametro WHERE clave='seg_detectado'").fetchone():   # primera vez: revisa todo el historial
        S.detectar(con)
        con.execute("INSERT OR REPLACE INTO parametro VALUES ('seg_detectado', ?)", (date.today().isoformat(),))
        con.commit()
    tipo = request.query_params.get("tipo") or None
    prod = request.query_params.get("producto") or None
    grupos = S.lista(con, cfg, tipo if tipo in S.TIPOS else None, prod)
    conteo = {t: con.execute("SELECT COUNT(*) FROM evento WHERE estado='activo' AND tipo=?", (t,)).fetchone()[0] for t in S.TIPOS}
    prods = [r[0] for r in con.execute("SELECT DISTINCT producto FROM venta_producto ORDER BY producto")]
    return page(request, "seguimiento.html", grupos=grupos, tipo=tipo, producto=prod, conteo=conteo, tipos=S.TIPOS,
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


@app.get("/costos-fijos", response_class=HTMLResponse)
def costos_fijos(request: Request):
    user(request)
    con = db()
    cfg = F.load_cfg(con)
    return page(request, "costos_fijos.html", rows=[dict(r) for r in con.execute("SELECT * FROM costo_fijo ORDER BY categoria, nombre")],
                ppm=cfg["empresa"]["ppm_tasa"] * 100, emp=cfg["empresa"],
                calc=F.tasa_tarjetas(con, dict(cfg, empresa=dict(cfg["empresa"], comision_debito=None, comision_credito=None))))


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
    return page(request, "impuestos.html", rows=rows, ppm=cfg["empresa"]["ppm_tasa"] * 100)


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
    return page(request, "decisiones.html", dias=dias, mapa=mapa, est=A.estimacion(con), precios=precios,
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
    return page(request, "ayuda.html")


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
                en_curso=any(t["estado"] == "en curso" for t in trabajos))


@app.post("/cargar")
async def cargar_post(request: Request):
    f = await form(request)
    con, msgs, lote = db(), [], []
    for up in f.getlist("archivos"):
        name = getattr(up, "filename", "") or ""
        if not name:
            continue
        data = await up.read()
        try:
            if len(data) > (80 if name.lower().endswith(".zip") else 20) * 1024 * 1024:
                raise ValueError("archivo demasiado grande (máximo 20 MB, o 80 MB si es ZIP)")
            with tempfile.NamedTemporaryFile(suffix=Path(name).suffix.lower()) as tmp:
                tmp.write(data)
                tmp.flush()
                low = name.lower()
                if low.endswith(".pdf") or low.endswith(".zip"):
                    lote.append((name, data))
                    continue
                if low.endswith(".csv"):
                    if "compra" not in low:
                        raise ValueError("por ahora solo se carga el registro de COMPRAS del SII")
                    from .importers.rcv import periodo_from_filename
                    r = import_rcv_compras(con, tmp.name, periodo_from_filename(name))
                    msgs.append(("ok", f"{name}: {r['documentos']} documentos de compra, periodo {r['periodo']}"))
                elif low.endswith(".xlsx"):
                    hojas = openpyxl.load_workbook(tmp.name, read_only=True).sheetnames
                    if "Detalle" in hojas:
                        msgs.append(("ok", f"{name}: {import_fudo_productos(con, tmp.name)['venta_producto']} filas de ventas por producto"))
                        n = S.detectar(con, avisar_desde=(date.today() - timedelta(days=30)).isoformat())
                        if n:
                            msgs.append(("ok", f"Detecté {len(n)} cambios nuevos en la carta (precios o productos). Míralos en Seguimiento."))
                    elif "Evolución de ventas" in hojas:
                        msgs.append(("ok", f"{name}: {import_fudo_report(con, tmp.name)['venta_hora']} horas de venta"))
                    else:
                        raise ValueError("no reconozco este Excel (espero Reporte de Ventas o de Productos de FUDO)")
                elif low.endswith(".toml"):
                    F.seed_from_config(con, tomllib.loads(data.decode("utf-8")))
                    msgs.append(("ok", f"{name}: configuración inicial cargada"))
                else:
                    raise ValueError("formato no soportado (.xlsx de FUDO, .csv del SII, PDF o ZIP de facturas)")
        except Exception as e:  # se informa al usuario archivo por archivo
            msgs.append(("bad", f"{name}: {e}"))
    if lote:
        if not os.environ.get("ANTHROPIC_API_KEY") and not __import__("lazzy.claves", fromlist=["leer"]).leer().get("ANTHROPIC_API_KEY"):
            msgs.append(("bad", "Para leer facturas en PDF falta la clave de Anthropic (página Bot de Telegram)."))
        else:
            H.lanzar(DB_PATH, lote)
            msgs.append(("ok", "Estoy leyendo las facturas en segundo plano. El avance aparece aquí abajo; puedes seguir usando el panel."))
    request.session["msgs"] = msgs
    return RedirectResponse("/cargar", 303)
