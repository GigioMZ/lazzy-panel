"""Panel web. Ejecutar: uvicorn lazzy.web:app  (variables: LAZZY_DB, LAZZY_SECRET, LAZZY_HTTPS=1)."""
import json
import os
import secrets
import tempfile
import time
import tomllib
from datetime import date
from pathlib import Path

import openpyxl
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import finance as F
from .auth import check_password
from .db import connect
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
MESES = "ene feb mar abr may jun jul ago sep oct nov dic".split()
T.env.filters["mes"] = lambda p: f"{MESES[int(p[5:7]) - 1]} {p[:4]}"
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


def page(request, name, **ctx):
    return T.TemplateResponse(request, name, {"user": request.session.get("user"),
                                              "csrf": request.session.get("csrf"), "cats": F.CATEGORIAS, **ctx})


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


def semaforo(p):
    pc = p["pct"]
    s = lambda v, verde, amarillo, alto_es_bueno: (
        "ok" if (v >= verde if alto_es_bueno else v <= verde)
        else "warn" if (v >= amarillo if alto_es_bueno else v <= amarillo) else "bad")
    return {"margen_bruto": s(pc["margen_bruto"], 65, 60, True), "costo_venta": s(pc["costo_venta"], 35, 40, False),
            "prime_cost": s(pc["prime_cost"], 60, 65, False), "resultado": s(pc["resultado"], 10, 5, True)}


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
    p = F.pnl_mes(con, per, cfg)
    serie = [F.pnl_mes(con, x, cfg) for x in ps[-12:]]
    chart = {"labels": [T.env.filters["mes"](x["periodo"]) for x in serie],
             "ventas": [x["ventas"]["neto"] for x in serie],
             "resultado": [x["resultado_operacional"] if x["tiene_compras"] else None for x in serie],
             "horas": F.ventas_por_hora(con, per)}
    top = F.margen_productos(con, per, per, cfg)
    return page(request, "resumen.html", p=p, per=per, ps=ps, sem=semaforo(p), chart=json.dumps(chart),
                medios=F.medios_pago(con, per), top=top, parcial=per >= hoy)


@app.get("/resultados", response_class=HTMLResponse)
def resultados(request: Request):
    user(request)
    con = db()
    cfg = F.load_cfg(con)
    desde, hasta, lista, ps = rango(request, con)
    cols = [F.pnl_mes(con, x, cfg) for x in lista]
    grupos = [("costo_venta", "Costo de venta"), ("personal", "Personal"),
              ("gasto_operacional", "Gastos operacionales"), ("sin_clasificar", "Sin clasificar")]
    cats = {g: sorted({c for p in cols for c in p["lineas"].get(g, {})}) for g, _ in grupos}
    return page(request, "resultados.html", cols=cols, grupos=grupos, lineas=cats, desde=desde, hasta=hasta, ps=ps)


@app.get("/productos", response_class=HTMLResponse)
def productos(request: Request):
    user(request)
    con = db()
    desde, hasta, _, ps = rango(request, con, 3)
    r = F.margen_productos(con, desde, hasta, F.load_cfg(con))
    return page(request, "productos.html", r=r, desde=desde, hasta=hasta, ps=ps)


@app.post("/productos/costo")
async def producto_costo(request: Request):
    f = await form(request)
    con = db()
    prod, costo = str(f["producto"]), str(f.get("costo", "")).replace(".", "").strip()
    if costo:
        con.execute("INSERT OR REPLACE INTO producto_costo VALUES (?,?,?,?)",
                    (prod, int(costo), "manual", date.today().isoformat()))
    else:
        con.execute("DELETE FROM producto_costo WHERE producto=?", (prod,))
    con.commit()
    return RedirectResponse(str(f.get("volver") or "/productos"), 303)


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
    return page(request, "receta.html", producto=p, lineas=lineas, total=F.costo_receta(con, p), fijo=fijo,
                precio=precio, insumos=[dict(r) for r in con.execute("SELECT * FROM insumo ORDER BY nombre")])


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
        con.execute("UPDATE insumo SET nombre=?, unidad=?, cantidad_formato=?, precio_formato=?, actualizado=? WHERE id=?",
                    (*vals, int(f["id"])))
    else:
        con.execute("INSERT INTO insumo(nombre,unidad,cantidad_formato,precio_formato,actualizado) VALUES (?,?,?,?,?)", vals)
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
    prov = [dict(r) for r in con.execute(
        "SELECT c.rut, MAX(c.razon_social) nombre, p.categoria cat, COUNT(*) n, "
        "SUM(CASE WHEN c.tipo_doc=61 THEN -c.neto ELSE c.neto END) neto "
        "FROM compra c LEFT JOIN proveedor p ON p.rut=c.rut GROUP BY c.rut ORDER BY (p.categoria IS NOT NULL), neto DESC")]
    return page(request, "compras.html", rows=rows, prov=prov, per=per, pers=pers)


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
    return RedirectResponse("/compras", 303)


@app.get("/costos-fijos", response_class=HTMLResponse)
def costos_fijos(request: Request):
    user(request)
    con = db()
    return page(request, "costos_fijos.html", rows=[dict(r) for r in con.execute("SELECT * FROM costo_fijo ORDER BY categoria, nombre")],
                ppm=F.load_cfg(con)["empresa"]["ppm_tasa"] * 100)


@app.post("/costos-fijos")
async def costos_fijos_post(request: Request):
    f = await form(request)
    con = db()
    if f.get("accion") == "ppm":
        con.execute("INSERT OR REPLACE INTO parametro VALUES ('ppm_tasa', ?)",
                    (str(float(str(f["ppm"]).replace(",", ".")) / 100),))
    elif f.get("accion") == "borrar":
        con.execute("DELETE FROM costo_fijo WHERE id=?", (int(f["id"]),))
    else:
        vals = (str(f["nombre"]).strip(), str(f["categoria"]), int(str(f["monto"]).replace(".", "")),
                str(f["desde"]), str(f.get("hasta") or "") or None)
        if vals[1] not in F.CATEGORIAS:
            raise HTTPException(400, "Categoría inválida")
        if f.get("id"):
            con.execute("UPDATE costo_fijo SET nombre=?, categoria=?, monto=?, desde=?, hasta=? WHERE id=?", (*vals, int(f["id"])))
        else:
            con.execute("INSERT INTO costo_fijo(nombre,categoria,monto,desde,hasta) VALUES (?,?,?,?,?)", vals)
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


@app.get("/cargar", response_class=HTMLResponse)
def cargar(request: Request):
    user(request)
    con = db()
    estado = {"ventas": con.execute("SELECT MIN(fecha), MAX(fecha) FROM venta_hora").fetchone(),
              "productos": con.execute("SELECT MIN(fecha), MAX(fecha) FROM venta_producto").fetchone(),
              "compras": [r[0] for r in con.execute("SELECT DISTINCT periodo FROM compra ORDER BY 1")]}
    return page(request, "cargar.html", estado=estado, msgs=request.session.pop("msgs", []))


@app.post("/cargar")
async def cargar_post(request: Request):
    f = await form(request)
    con, msgs = db(), []
    for up in f.getlist("archivos"):
        name = getattr(up, "filename", "") or ""
        if not name:
            continue
        data = await up.read()
        try:
            if len(data) > 20 * 1024 * 1024:
                raise ValueError("archivo de más de 20 MB")
            with tempfile.NamedTemporaryFile(suffix=Path(name).suffix.lower()) as tmp:
                tmp.write(data)
                tmp.flush()
                low = name.lower()
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
                    elif "Evolución de ventas" in hojas:
                        msgs.append(("ok", f"{name}: {import_fudo_report(con, tmp.name)['venta_hora']} horas de venta"))
                    else:
                        raise ValueError("no reconozco este Excel (espero Reporte de Ventas o de Productos de FUDO)")
                elif low.endswith(".toml"):
                    F.seed_from_config(con, tomllib.loads(data.decode("utf-8")))
                    msgs.append(("ok", f"{name}: configuración inicial cargada"))
                else:
                    raise ValueError("formato no soportado (.xlsx de FUDO, .csv del SII)")
        except Exception as e:  # se informa al usuario archivo por archivo
            msgs.append(("bad", f"{name}: {e}"))
    request.session["msgs"] = msgs
    return RedirectResponse("/cargar", 303)
