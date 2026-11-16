"""Prueba de humo en el servidor: renderiza cada página del panel con los datos reales, sin login.
Uso: sudo -u lazzy /opt/lazzy-panel/.venv/bin/python /opt/lazzy-panel/deploy/smoke.py"""
import os
import sys

os.environ.setdefault("LAZZY_DB", "/var/lib/lazzy/lazzy.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lazzy.web as web  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from lazzy import finance as F  # noqa: E402

web.user = lambda request: "smoke"          # salta el login solo dentro de este proceso
c = TestClient(web.app, follow_redirects=False)
con = web.db()
ps = F.periodos(con)
compras = [r[0] for r in con.execute("SELECT DISTINCT periodo FROM compra ORDER BY 1")]
print("meses con ventas:", ps[0], "a", ps[-1], "| compras SII:", ", ".join(compras))
print("version de la base:", con.execute("PRAGMA user_version").fetchone()[0])
print("usuarios:", ", ".join(r[0] for r in con.execute("SELECT nombre FROM usuario")))
mal = 0
provs = [f"/proveedor?rut={r[0]}" for r in con.execute("SELECT rut FROM compra GROUP BY rut ORDER BY COUNT(*) DESC LIMIT 3")]
for u in ["/", "/?p=mes_pasado", "/?p=90", "/?p=anio", "/?desde=2026-10-05&hasta=2026-10-20", "/mes", "/decisiones", "/resultados", "/productos", "/insumos", "/compras", "/costos-fijos", "/impuestos", "/cargar", "/ayuda", "/bot",
          "/receta?p=Latte", "/seguimiento", "/seguimiento?tipo=precio", "/seguimiento?tipo=costo"] + provs + [f"/mes?periodo={p}" for p in ps]:
    r = c.get(u)
    ok = r.status_code == 200
    mal += not ok
    if not ok or not u.startswith("/mes?periodo"):
        print(("OK " if ok else "ERROR ") + str(r.status_code), u)
print("periodos del resumen revisados:", len(ps), "| errores:", mal)
cfg = F.load_cfg(con)
for p in compras:
    x = F.pnl_mes(con, p, cfg)
    print(f"{p}: venta neta {x['ventas']['neto']:,} | resultado {x['resultado_operacional']:,} ({x['pct']['resultado']}%)"
          f" | sin clasificar {x['sin_clasificar']:,} | IVA {x['iva']['iva_a_pagar']:,}".replace(",", "."))
m = F.margen_productos(con, compras[0] if compras else ps[0], ps[-1], cfg)
print("costeado:", m["cobertura_pct"], "% de la venta | productos con receta:",
      con.execute("SELECT COUNT(DISTINCT producto) FROM receta").fetchone()[0],
      "| insumos:", con.execute("SELECT COUNT(*) FROM insumo").fetchone()[0],
      "| costos fijos:", con.execute("SELECT COUNT(*) FROM costo_fijo").fetchone()[0])
sys.exit(1 if mal else 0)
