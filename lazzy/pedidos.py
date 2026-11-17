"""Pedido sugerido por proveedor: lo que hay que pedir para llegar al próximo pedido con stock, según lo que se vende.
Pedido = stock objetivo − stock de hoy. Stock objetivo = venta diaria × días hasta el próximo pedido + stock mínimo
(o un objetivo fijo si lo definieron). El stock de hoy descuenta, al ritmo normal, los días cuyas ventas no se suben aún."""
import math
from datetime import date

from . import finance as F
from . import stock as ST
from . import vitrina as VT
from .insights import clp
from .proveedores import nombre as nombre_prov

DIAS = 7        # días entre pedidos si no se configuró otro
MINIMO = 50000  # pedido mínimo de cada proveedor, con IVA, si no se configuró otro


def dias_proveedor(con, rut):
    r = con.execute("SELECT valor FROM parametro WHERE clave=?", (f"pedido_dias:{rut}",)).fetchone()
    return int(float(r[0])) if r else DIAS


def minimo_proveedor(con, rut):
    r = con.execute("SELECT valor FROM parametro WHERE clave=?", (f"pedido_minimo:{rut}",)).fetchone()
    return int(float(r[0])) if r else MINIMO


def guardar_minimo(con, rut, monto):
    con.execute("INSERT OR REPLACE INTO parametro VALUES (?,?)", (f"pedido_minimo:{rut}", str(int(monto))))
    con.commit()


def _unidades(i):
    return i["packs"] * i["divisor"] if i["packs"] else i["pedir"]


def _poner(i, unidades):
    i["packs"] = math.ceil(unidades / i["divisor"]) if i["divisor"] and i["divisor"] > 1 else None
    i["pedir"] = _unidades(i) if i["packs"] else unidades


def completar_minimo(g, costos, iva):
    """Si el pedido no llega al mínimo del proveedor, adelanta unidades de lo que se acaba antes (lo que menos días
    alcanza a cubrir) hasta llegar. Si ni adelantando 3 períodos llega, lo deja y avisa que conviene juntar."""
    precio = {i["producto"]: costos.get(i["producto"]) for i in g["items"]}
    valor = lambda: round(sum(_unidades(i) * (precio[i["producto"]] or 0) for i in g["items"]) * (1 + iva))
    g["valor"], g["agregado"], g["no_alcanza"] = valor(), {}, False
    g["sin_precio"] = [i["producto"] for i in g["items"] if i["pedir"] and not precio[i["producto"]]]
    if not g["total"] or not g["rut"] or g["valor"] >= g["minimo"]:
        return g
    candidatos = [i for i in g["items"] if i["ritmo"] > 0 and precio[i["producto"]]]
    tope = 3 * g["dias"]
    while candidatos and valor() < g["minimo"]:
        i = min(candidatos, key=lambda i: (i["hoy"] + _unidades(i)) / i["ritmo"])
        if (i["hoy"] + _unidades(i)) / i["ritmo"] >= tope:
            break
        antes = _unidades(i)
        _poner(i, antes + (i["divisor"] if i["divisor"] and i["divisor"] > 1 else 1))
        g["agregado"][i["producto"]] = g["agregado"].get(i["producto"], 0) + _unidades(i) - antes
    g["valor"] = valor()
    g["no_alcanza"] = g["valor"] < g["minimo"]
    g["total"] = sum(1 for i in g["items"] if i["pedir"])
    return g


def guardar_dias(con, rut, dias):
    con.execute("INSERT OR REPLACE INTO parametro VALUES (?,?)", (f"pedido_dias:{rut}", str(int(dias))))
    con.commit()


def proveedor_de(con):
    """producto → (rut, clave en la factura, divisor, texto en la factura, seguro). Sin vínculo, el proveedor sugerido."""
    textos = {(r[0], r[1]): r[2] for r in con.execute(
        "SELECT rut, descripcion, MAX(COALESCE(texto, descripcion)) FROM factura_linea GROUP BY 1, 2")}
    out = {}
    for r in con.execute("SELECT rut, clave, destino, divisor FROM vinculo WHERE tipo='producto'"):
        out[r["destino"]] = (r["rut"], r["clave"], r["divisor"] or 1, textos.get((r["rut"], r["clave"])) or r["clave"], True)
    for p, a in VT.ajustes(con).items():     # proveedor elegido a mano en el panel
        if a.get("proveedor") and p not in out:
            out[p] = (a["proveedor"], None, 1, p, True)
    for s in ST.sugerencias(con):
        out.setdefault(s["producto"], (s["rut"], s["clave"], 1, s["texto"], False))
    return out


def proveedores(con):
    """Proveedores que venden productos del stock (para elegir a mano)."""
    return [(r[0], nombre_prov(con, r[0])) for r in con.execute(
        "SELECT DISTINCT c.rut FROM compra c WHERE c.rut IN (SELECT rut FROM proveedor WHERE categoria IN ('pasteleria','sandwiches')) "
        "OR c.rut IN (SELECT rut FROM vinculo WHERE tipo='producto') ORDER BY 1")]


def sugerido(con, cfg, hoy=None):
    hoy = hoy or date.today()
    est = ST.estado(con, cfg, hoy)
    if not est:
        return []
    prods = [x["producto"] for x in est]
    _, ult, ritmo = VT.ventas_diarias(con, prods, hoy)
    atraso = min((hoy - date.fromisoformat(ult)).days - 1, 14) if ult else 0    # más de 2 semanas sin ventas: no se estima más
    prov, aj = proveedor_de(con), VT.ajustes(con)
    grupos = {}
    for x in est:
        p = x["producto"]
        v = ritmo.get(p, 0)
        rut, clave, div, texto, seguro = prov.get(p, ("", None, 1, p, False))
        dias = dias_proveedor(con, rut) if rut else DIAS
        hoy_q = max(x["esperado"] - v * max(atraso, 0), 0)
        fijo = (aj.get(p) or {}).get("objetivo")
        if not v and fijo is None:      # no se vendió en 4 semanas: no se sugiere (producto que salió de carta)
            continue
        objetivo = fijo if fijo is not None else math.ceil(v * dias + (x["minimo"] or 0))
        if not objetivo:
            continue
        pedir = max(math.ceil(objetivo - hoy_q - 0.25), 0)
        g = grupos.setdefault(rut, {"rut": rut, "nombre": nombre_prov(con, rut) if rut else "Sin proveedor asignado",
                                    "dias": dias, "items": []})
        g["items"].append({"producto": p, "ritmo": round(v, 1), "hoy": round(hoy_q, 1), "objetivo": objetivo, "fijo": fijo is not None,
                           "pedir": pedir, "texto": texto if rut else p, "divisor": div, "seguro": seguro,
                           "packs": math.ceil(pedir / div) if div and div > 1 else None})
    costos = {p: (c or {}).get("costo") for p, c in cfg["costo_producto"].items()}
    for g in grupos.values():
        g["total"] = sum(1 for i in g["items"] if i["pedir"])
        g["minimo"] = minimo_proveedor(con, g["rut"]) if g["rut"] else 0
        completar_minimo(g, costos, cfg["empresa"]["iva"])
        g["items"].sort(key=lambda i: (-i["pedir"], i["producto"]))
    return sorted(grupos.values(), key=lambda g: (g["rut"] == "", -g["total"], g["nombre"]))


def buscar_grupo(grupos, texto):
    t = F._norm(texto)
    return [g for g in grupos if t and (t in F._norm(g["nombre"]) or any(t in F._norm(i["producto"]) for i in g["items"]))]


def mensaje(g):
    """Texto para copiar y mandar al proveedor."""
    lineas = [f"- {i['packs']:g} × {i['texto']} ({i['pedir']:g} unidades)" if i["packs"] else f"- {i['pedir']:g} {i['texto']}"
              for i in g["items"] if i["pedir"]]
    return "Hola! Para el próximo pedido de Lazzy Coffee:\n" + "\n".join(lineas) + "\nGracias!"


def texto_bot(g, ult=None):
    t = [f"📋 {g['nombre']} (pedido cada {g['dias']} días):"]
    for i in g["items"]:
        base = f"objetivo fijo {i['objetivo']:g}" if i["fijo"] else f"vende {i['ritmo']:g}/día".replace(".", ",")
        t.append(f"• {i['producto']}: hay ~{round(i['hoy']):g}, {base} → pedir {i['pedir']:g}" + ("" if i["seguro"] or not g["rut"] else " (proveedor probable)"))
    if g["total"] and g["rut"]:
        t.append(f"Total aprox.: {clp(g['valor'])} con IVA (mínimo {clp(g['minimo'])})")
        if g["agregado"]:
            t.append("Para llegar al mínimo adelanté: " + ", ".join(f"+{q:g} {p}" for p, q in g["agregado"].items())
                     + " (lo que se acaba antes)")
        if g["no_alcanza"]:
            t.append("⚠️ Ni adelantando 3 pedidos llega al mínimo: conviene juntarlo con el próximo o con otro producto de este proveedor.")
        if g["sin_precio"]:
            t.append("Sin costo cargado (no suman al total): " + ", ".join(g["sin_precio"]))
    if g["total"]:
        t.append("\nPara copiar:\n" + mensaje(g))
    else:
        t.append("No hace falta pedir por ahora.")
    return "\n".join(t)
