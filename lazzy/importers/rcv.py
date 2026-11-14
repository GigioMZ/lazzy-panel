"""Importa el Registro de Compras del SII (CSV 'RCV_COMPRA_REGISTRO_<rut>_<AAAAMM>.csv')."""
import csv
import re
from datetime import datetime


def _i(v):
    v = (v or "").strip()
    return int(v) if v else 0


def periodo_from_filename(name):
    m = re.search(r"_(\d{4})(\d{2})(?:[_.(]|$)", name)
    if not m:
        raise ValueError(f"No pude leer el periodo AAAAMM del nombre: {name}")
    return f"{m.group(1)}-{m.group(2)}"


def import_rcv_compras(con, path, periodo=None):
    periodo = periodo or periodo_from_filename(str(path).rsplit("/", 1)[-1])
    n = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if not (r.get("Folio") or "").strip():
                continue
            con.execute(
                """INSERT INTO compra(periodo,tipo_doc,rut,razon_social,folio,fecha_doc,exento,neto,
                       iva_rec,iva_no_rec,total,activo_fijo_neto)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(tipo_doc,rut,folio) DO UPDATE SET
                     periodo=excluded.periodo, razon_social=excluded.razon_social,
                     fecha_doc=excluded.fecha_doc, exento=excluded.exento, neto=excluded.neto,
                     iva_rec=excluded.iva_rec, iva_no_rec=excluded.iva_no_rec, total=excluded.total,
                     activo_fijo_neto=excluded.activo_fijo_neto""",
                (periodo, _i(r["Tipo Doc"]), r["RUT Proveedor"].strip(), r["Razon Social"].strip(),
                 _i(r["Folio"]), datetime.strptime(r["Fecha Docto"].strip(), "%d/%m/%Y").date().isoformat(),
                 _i(r["Monto Exento"]), _i(r["Monto Neto"]), _i(r["Monto IVA Recuperable"]),
                 _i(r["Monto Iva No Recuperable"]), _i(r["Monto Total"]), _i(r["Monto Neto Activo Fijo"])),
            )
            n += 1
    con.commit()
    return {"periodo": periodo, "documentos": n}
