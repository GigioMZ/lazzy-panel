import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS venta_hora(
  fecha TEXT NOT NULL, hora INTEGER NOT NULL, bruto INTEGER NOT NULL, tickets INTEGER NOT NULL,
  PRIMARY KEY(fecha, hora));
CREATE TABLE IF NOT EXISTS venta_medio_pago(
  fecha TEXT NOT NULL, medio TEXT NOT NULL, bruto INTEGER NOT NULL, tickets INTEGER NOT NULL,
  PRIMARY KEY(fecha, medio));
CREATE TABLE IF NOT EXISTS venta_origen(
  fecha TEXT NOT NULL, origen TEXT NOT NULL, bruto INTEGER NOT NULL, tickets INTEGER NOT NULL,
  PRIMARY KEY(fecha, origen));
CREATE TABLE IF NOT EXISTS venta_producto(
  fecha TEXT NOT NULL, categoria TEXT NOT NULL, producto TEXT NOT NULL,
  cantidad REAL NOT NULL, bruto INTEGER NOT NULL,
  PRIMARY KEY(fecha, categoria, producto));
CREATE TABLE IF NOT EXISTS compra(
  periodo TEXT NOT NULL,            -- periodo tributario AAAA-MM (define el crédito IVA)
  tipo_doc INTEGER NOT NULL,        -- 33 factura, 34 exenta, 61 nota de crédito, 56 nota de débito
  rut TEXT NOT NULL, razon_social TEXT NOT NULL, folio INTEGER NOT NULL,
  fecha_doc TEXT NOT NULL,
  exento INTEGER NOT NULL, neto INTEGER NOT NULL, iva_rec INTEGER NOT NULL,
  iva_no_rec INTEGER NOT NULL, total INTEGER NOT NULL,
  activo_fijo_neto INTEGER NOT NULL DEFAULT 0,
  categoria TEXT,                   -- override manual; si es NULL se usa el mapa de proveedores
  PRIMARY KEY(tipo_doc, rut, folio));
CREATE TABLE IF NOT EXISTS gasto_manual(   -- gastos sin factura electrónica (boletas, efectivo, cuentas)
  id INTEGER PRIMARY KEY, fecha TEXT NOT NULL, descripcion TEXT NOT NULL,
  categoria TEXT NOT NULL, neto INTEGER NOT NULL, iva_rec INTEGER NOT NULL DEFAULT 0);
"""


def connect(path="data/lazzy.db"):
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con
