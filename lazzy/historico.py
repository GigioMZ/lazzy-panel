"""Carga en lote de facturas antiguas (PDF sueltos o un ZIP, por ejemplo la exportación del grupo de WhatsApp).
Corre en segundo plano: lee cada factura con Haiku y guarda su detalle de productos y precios."""
import io
import threading
import zipfile
from datetime import datetime

from . import captura as C
from .db import connect

MAX_ARCHIVOS = 300


def _pdfs(nombre, datos):
    if nombre.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            for i in z.infolist():
                base = i.filename.rsplit("/", 1)[-1]
                if base.lower().endswith(".pdf") and i.file_size < 15 * 1024 * 1024 and not base.startswith("."):
                    yield base, z.read(i)
    elif nombre.lower().endswith(".pdf"):
        yield nombre, datos


def descartar(nombre, texto):
    """Comprobantes de transferencia y documentos que no son facturas: no gastan llamadas a la API."""
    t = texto.lower()
    if nombre.lower().startswith("comprobante") or ("transferencia" in t and "operaci" in t and "factura" not in t):
        return "comprobante de transferencia"
    return None


def crear(con, total):
    cur = con.execute("INSERT INTO trabajo(creado,tipo,estado,total,hechos,mensaje) VALUES (?,?,?,?,0,'')",
                      (datetime.now().isoformat(timespec="seconds"), "facturas", "en curso", total))
    con.commit()
    return cur.lastrowid


def procesar(con, tid, archivos, leer=C.leer_pdf):
    rut_emp = (con.execute("SELECT valor FROM parametro WHERE clave='rut_empresa'").fetchone() or [""])[0]
    r = {"facturas": 0, "lineas": 0, "omitidos": 0, "errores": []}
    for i, (nombre, datos) in enumerate(archivos, 1):
        try:
            motivo = descartar(nombre, C.texto_pdf(datos))
            if motivo:
                r["omitidos"] += 1
            else:
                d = C.preparar(con, leer(con, datos))
                if d.get("clase") != "documento" or not C.es_factura(d) or not d.get("lineas"):
                    r["omitidos"] += 1
                elif d.get("rut_receptor") and rut_emp and d["rut_receptor"] != rut_emp:
                    r["omitidos"] += 1
                else:
                    d["historico"] = True
                    C.guardar(con, d, "carga")
                    r["facturas"] += 1
                    r["lineas"] += len(d["lineas"])
        except Exception as e:  # una factura ilegible no detiene el resto
            r["errores"].append(f"{nombre}: {str(e)[:120]}")
        con.execute("UPDATE trabajo SET hechos=?, mensaje=? WHERE id=?", (i, resumen(r), tid))
        con.commit()
    con.execute("UPDATE trabajo SET estado='listo', mensaje=? WHERE id=?", (resumen(r), tid))
    con.commit()
    return r


def resumen(r):
    txt = f"{r['facturas']} facturas con {r['lineas']} productos guardados; {r['omitidos']} archivos omitidos (comprobantes u otros)."
    if r["errores"]:
        txt += f" {len(r['errores'])} no se pudieron leer: " + "; ".join(r["errores"][:5])
    return txt


def lanzar(db_path, archivos):
    """archivos: lista de (nombre, bytes). Devuelve el id del trabajo, que avanza en un hilo aparte."""
    pdfs = [x for nombre, datos in archivos for x in _pdfs(nombre, datos)][:MAX_ARCHIVOS]
    con = connect(db_path)
    tid = crear(con, len(pdfs))
    threading.Thread(target=procesar, args=(connect(db_path), tid, pdfs), daemon=True).start()
    return tid
