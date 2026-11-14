import argparse

from .db import connect
from .finance import load_config, margen_productos, medios_pago, pnl_mes
from .importers.fudo_report import import_fudo_productos, import_fudo_report
from .importers.rcv import import_rcv_compras


def clp(x):
    return "—" if x is None else f"${x:,.0f}".replace(",", ".")


def print_pnl(p):
    v, pc = p["ventas"], p["pct"]
    L = lambda t, m, q="": print(f"{t:<38}{clp(m):>14}{q:>9}")
    print(f"\nESTADO DE RESULTADOS {p['periodo']}  ({v['dias_abiertos']} días, {v['tickets']} tickets, "
          f"ticket {clp(v['ticket_promedio'])})")
    L("Ventas brutas (FUDO)", v["bruto"]); L("Ventas netas", v["neto"], "100%")
    for g, t, k in (("costo_venta", "Costo de venta (compras)", "costo_venta"),):
        L(t, -p[g], f"{pc[k]}%")
        for c, m in sorted(p["lineas"].get(g, {}).items(), key=lambda x: -x[1]): L(f"   {c}", -m)
    L("MARGEN BRUTO", p["margen_bruto"], f"{pc['margen_bruto']}%")
    L("Personal", -p["personal"], f"{pc['personal']}%")
    L("Gastos operacionales", -p["gasto_operacional"], f"{pc['gasto_operacional']}%")
    for c, m in sorted(p["lineas"].get("gasto_operacional", {}).items(), key=lambda x: -x[1]): L(f"   {c}", -m)
    if p["sin_clasificar"]:
        L("Sin clasificar", -p["sin_clasificar"])
        for rs, rut, m in p["sin_clasificar_detalle"]: L(f"   {rs[:30]} {rut}", -m)
    L("RESULTADO OPERACIONAL", p["resultado_operacional"], f"{pc['resultado']}%")
    print(f"Prime cost: {pc['prime_cost']}%   Equilibrio: {clp(p['equilibrio']['venta_bruta_dia'])} brutos/día")
    i = p["iva"]
    print(f"IVA: débito {clp(i['debito'])} − crédito {clp(i['credito'])} = {clp(i['iva_a_pagar'])}"
          f"   PPM {clp(i['ppm'])}   F29 estimado {clp(i['f29_estimado'])}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lazzy")
    ap.add_argument("--db", default="data/lazzy.db"); ap.add_argument("--config", default="config/lazzy.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("import-fudo").add_argument("path")
    r = sub.add_parser("import-rcv"); r.add_argument("path"); r.add_argument("--periodo")
    sub.add_parser("import-productos").add_argument("path")
    sub.add_parser("report").add_argument("periodo")
    r = sub.add_parser("resumen"); r.add_argument("desde"); r.add_argument("hasta")
    r = sub.add_parser("productos"); r.add_argument("desde"); r.add_argument("hasta")
    a = ap.parse_args(argv)
    con = connect(a.db)
    if a.cmd == "import-fudo":
        print(import_fudo_report(con, a.path))
    elif a.cmd == "import-rcv":
        print(import_rcv_compras(con, a.path, a.periodo))
    elif a.cmd == "import-productos":
        print(import_fudo_productos(con, a.path))
    elif a.cmd == "resumen":
        cfg = load_config(a.config)
        y, m = map(int, a.desde.split("-"))
        print(f"{'mes':<8}{'venta neta':>12}{'costo vta':>11}{'%':>6}{'personal':>10}{'gastos op':>11}{'sin clasif':>11}{'resultado':>11}{'%':>6}{'IVA a pagar':>12}")
        while f"{y}-{m:02d}" <= a.hasta:
            p = pnl_mes(con, f"{y}-{m:02d}", cfg)
            print(f"{p['periodo']:<8}{clp(p['ventas']['neto']):>12}{clp(p['costo_venta']):>11}{p['pct']['costo_venta']:>6}"
                  f"{clp(p['personal']):>10}{clp(p['gasto_operacional']):>11}{clp(p['sin_clasificar']):>11}"
                  f"{clp(p['resultado_operacional']):>11}{p['pct']['resultado']:>6}{clp(p['iva']['iva_a_pagar']):>12}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    elif a.cmd == "productos":
        r = margen_productos(con, a.desde, a.hasta, load_config(a.config))
        print(f"Cobertura de costos: {r['cobertura_pct']}% de la venta")
        print(f"{'producto':<30}{'cant':>6}{'venta neta':>12}{'precio':>8}{'costo':>7}{'margen%':>8}{'contrib':>11}  clase / fuente")
        for d in r["productos"]:
            print(f"{d['producto'][:29]:<30}{d['cantidad']:>6.0f}{clp(d['neto']):>12}{clp(d['precio_neto']):>8}"
                  f"{clp(d['costo']):>7}{(str(d['margen_pct']) if d['margen_pct'] is not None else '—'):>8}"
                  f"{clp(d['contribucion']):>11}  {d['clase'] or 'SIN COSTO'} {d['fuente'] or ''}")
    else:
        p = pnl_mes(con, a.periodo, load_config(a.config))
        print_pnl(p)
        print("Medios de pago:", ", ".join(f"{m['medio']} {clp(m['bruto'])}" for m in medios_pago(con, a.periodo)))


if __name__ == "__main__":
    main()
