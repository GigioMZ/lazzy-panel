"""Importa el 'Reporte de Ventas' de FUDO (xlsx). Reimportar es idempotente (upsert por fecha)."""
import openpyxl

SHEETS = {
    "Evolución de ventas": ("venta_hora", lambda r: (r[0].date().isoformat(), int(r[2]), round(r[3]), int(r[4]))),
    "Medios de pago": ("venta_medio_pago", lambda r: (r[0].date().isoformat(), str(r[1]).strip(), round(r[2]), int(r[3]))),
    "Origen de ventas": ("venta_origen", lambda r: (r[0].date().isoformat(), str(r[1]).strip(), round(r[2]), int(r[3]))),
}


def import_fudo_report(con, path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    counts = {}
    for sheet, (table, parse) in SHEETS.items():
        if sheet not in wb.sheetnames:
            raise ValueError(f"Falta la hoja '{sheet}' en {path}")
        rows = [parse(r) for r in list(wb[sheet].iter_rows(values_only=True))[1:] if r and r[0] is not None]
        con.executemany(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?)", rows)
        counts[table] = len(rows)
    con.commit()
    return counts


def import_fudo_productos(con, path):
    """Importa el 'Reporte de Productos' de FUDO (hoja Detalle: una fila por día y producto)."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if "Detalle" not in wb.sheetnames:
        raise ValueError(f"Falta la hoja 'Detalle' en {path}")
    agg = {}
    for r in list(wb["Detalle"].iter_rows(values_only=True))[1:]:
        if not r or r[0] is None:
            continue
        k = (r[0].date().isoformat(), str(r[1]).strip(), str(r[3]).strip())
        q, m = agg.get(k, (0, 0))
        agg[k] = (q + (r[4] or 0), m + (r[5] or 0))
    con.executemany("INSERT OR REPLACE INTO venta_producto VALUES (?,?,?,?,?)",
                    [(*k, q, round(m)) for k, (q, m) in agg.items()])
    con.commit()
    return {"venta_producto": len(agg)}
