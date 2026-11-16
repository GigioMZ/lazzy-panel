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
CREATE TABLE IF NOT EXISTS proveedor(rut TEXT PRIMARY KEY, nombre TEXT, categoria TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS costo_fijo(
  id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, categoria TEXT NOT NULL, monto INTEGER NOT NULL,
  desde TEXT NOT NULL, hasta TEXT);
CREATE TABLE IF NOT EXISTS producto_costo(      -- costo neto unitario fijado a mano o desde factura
  producto TEXT PRIMARY KEY, costo INTEGER NOT NULL, fuente TEXT NOT NULL, actualizado TEXT);
CREATE TABLE IF NOT EXISTS insumo(              -- precio neto por formato de compra
  id INTEGER PRIMARY KEY, nombre TEXT UNIQUE NOT NULL, unidad TEXT NOT NULL,
  cantidad_formato REAL NOT NULL, precio_formato INTEGER NOT NULL, actualizado TEXT);
CREATE TABLE IF NOT EXISTS receta(              -- si un producto tiene receta, su costo se calcula desde aquí
  producto TEXT NOT NULL, insumo_id INTEGER NOT NULL REFERENCES insumo(id), cantidad REAL NOT NULL,
  PRIMARY KEY(producto, insumo_id));
CREATE TABLE IF NOT EXISTS parametro(clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usuario(nombre TEXT PRIMARY KEY, hash TEXT NOT NULL, salt TEXT NOT NULL);
"""


def _m1(con):
    """Historial de costos: cada cambio queda con su fecha, así los meses pasados usan el costo de su época."""
    con.executescript("""
    CREATE TABLE IF NOT EXISTS producto_costo_hist(
      producto TEXT NOT NULL, desde TEXT NOT NULL, costo INTEGER, fuente TEXT, PRIMARY KEY(producto, desde));
    CREATE TABLE IF NOT EXISTS insumo_hist(
      insumo_id INTEGER NOT NULL, desde TEXT NOT NULL, cantidad_formato REAL NOT NULL, precio_formato INTEGER NOT NULL,
      PRIMARY KEY(insumo_id, desde));
    CREATE INDEX IF NOT EXISTS ix_compra_fecha ON compra(fecha_doc);
    CREATE INDEX IF NOT EXISTS ix_compra_periodo ON compra(periodo);
    """)
    con.execute("INSERT OR IGNORE INTO producto_costo_hist SELECT producto, '2000-01-01', costo, fuente FROM producto_costo")
    con.execute("INSERT OR IGNORE INTO insumo_hist SELECT id, '2000-01-01', cantidad_formato, precio_formato FROM insumo")


def _m2(con):
    """Bot de Telegram: detalle de facturas, gastos con origen, vinculación de chats y uso de la API."""
    con.executescript("""
    ALTER TABLE compra ADD COLUMN origen TEXT DEFAULT 'sii';
    ALTER TABLE gasto_manual ADD COLUMN origen TEXT DEFAULT 'panel';
    ALTER TABLE gasto_manual ADD COLUMN creado TEXT;
    ALTER TABLE gasto_manual ADD COLUMN usuario TEXT;
    ALTER TABLE costo_fijo ADD COLUMN estimado INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE IF NOT EXISTS factura_linea(
      tipo_doc INTEGER NOT NULL, rut TEXT NOT NULL, folio INTEGER NOT NULL, n INTEGER NOT NULL,
      descripcion TEXT NOT NULL, cantidad REAL, precio_neto REAL, total_neto INTEGER,
      PRIMARY KEY(tipo_doc, rut, folio, n));
    CREATE TABLE IF NOT EXISTS telegram_chat(chat_id INTEGER PRIMARY KEY, nombre TEXT, usuario TEXT, creado TEXT);
    CREATE TABLE IF NOT EXISTS telegram_codigo(codigo TEXT PRIMARY KEY, usuario TEXT NOT NULL, expira TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS captura(
      id INTEGER PRIMARY KEY, chat_id INTEGER, usuario TEXT, creado TEXT NOT NULL, estado TEXT NOT NULL,
      tipo TEXT, datos TEXT NOT NULL, archivo TEXT, resultado TEXT);
    CREATE TABLE IF NOT EXISTS api_uso(fecha TEXT NOT NULL, tipo TEXT, entrada INTEGER, salida INTEGER);
    """)
    # Estimaciones que se reemplazan cuando llega el gasto real del mes por el bot
    con.execute("UPDATE costo_fijo SET estimado=1 WHERE nombre LIKE 'Cuentas%' OR nombre LIKE 'Sándwiches%'")
    con.execute("INSERT OR IGNORE INTO parametro VALUES ('rut_empresa', '77618245-1')")


def _m3(con):
    """Alias de proveedores, vínculos factura→producto/insumo, seguimiento de decisiones y avisos."""
    con.executescript("""
    CREATE TABLE IF NOT EXISTS proveedor_alias(rut TEXT PRIMARY KEY, alias TEXT NOT NULL);
    ALTER TABLE factura_linea ADD COLUMN texto TEXT;
    CREATE TABLE IF NOT EXISTS vinculo(
      rut TEXT NOT NULL, clave TEXT NOT NULL, tipo TEXT NOT NULL, destino TEXT NOT NULL,
      divisor REAL NOT NULL DEFAULT 1, PRIMARY KEY(rut, clave));
    CREATE TABLE IF NOT EXISTS evento(
      id INTEGER PRIMARY KEY, fecha TEXT NOT NULL, tipo TEXT NOT NULL, producto TEXT, titulo TEXT,
      datos TEXT, origen TEXT NOT NULL DEFAULT 'auto', nota TEXT, estado TEXT NOT NULL DEFAULT 'activo', creado TEXT);
    CREATE INDEX IF NOT EXISTS ix_evento ON evento(tipo, producto, fecha);
    CREATE TABLE IF NOT EXISTS notificacion(id INTEGER PRIMARY KEY, creado TEXT, texto TEXT NOT NULL, enviado TEXT);
    CREATE TABLE IF NOT EXISTS trabajo(
      id INTEGER PRIMARY KEY, creado TEXT, tipo TEXT, estado TEXT, total INTEGER, hechos INTEGER, mensaje TEXT);
    """)
    # Nombres comerciales confirmados (el resto se pone en la ficha de cada proveedor)
    con.executemany("INSERT OR IGNORE INTO proveedor_alias VALUES (?,?)",
                    [("77323879-0", "Cake House"), ("96954210-2", "Surlat")])


# Cada cambio de estructura se agrega al final de esta lista; nunca se edita uno ya publicado.
def _m4(con):
    """Metas mensuales (periodo '*' = vale para todos los meses)."""
    con.executescript("""
    CREATE TABLE IF NOT EXISTS meta(periodo TEXT NOT NULL, clave TEXT NOT NULL, valor REAL NOT NULL, PRIMARY KEY(periodo, clave));
    """)


MIGRACIONES = [_m1, _m2, _m3, _m4]


def migrar(con):
    v = con.execute("PRAGMA user_version").fetchone()[0]
    for i, m in enumerate(MIGRACIONES[v:], start=v + 1):
        m(con)
        con.execute(f"PRAGMA user_version = {i}")
        con.commit()


def connect(path="data/lazzy.db"):
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL") if path != ":memory:" else None
    con.executescript(SCHEMA)
    migrar(con)
    return con


def mes_rango(periodo):
    """'2026-12' -> ('2026-12-01', '2027-01-01'): sirve para consultas por fecha que usan el índice."""
    y, m = int(periodo[:4]), int(periodo[5:7])
    y2, m2 = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{y}-{m:02d}-01", f"{y2}-{m2:02d}-01"
