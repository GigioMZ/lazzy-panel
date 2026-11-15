"""Textos de ayuda del panel: cada término en lenguaje simple. clave -> (nombre, explicación)."""

GLOSARIO = {
    "venta_neta": ("Venta sin IVA", "Lo que vendiste en el mes, descontando el IVA. El IVA no es plata tuya: lo cobras y después se lo pagas al SII. Por eso todos los cálculos del panel usan la venta sin IVA."),
    "venta_bruta": ("Venta con IVA", "El total que pagaron los clientes en caja, tal como sale en FUDO. Incluye el 19% de IVA."),
    "ganancia": ("Ganancia del mes", "Lo que queda de la venta después de pagar insumos, sueldos y gastos del local. Todavía no descuenta el impuesto a la renta (se paga una vez al año) ni lo que retiren los socios."),
    "insumos": ("Insumos y productos", "Todo lo que compras para vender: café, leche, té, pastelería, sándwiches, bebidas, vasos y envases. Se toma de las facturas del SII y de los gastos fijos que tú cargues. Lo sano en una cafetería es gastar aquí hasta $350 de cada $1.000 vendidos."),
    "sueldos": ("Sueldos", "Sueldos más imposiciones y aportes del empleador. Se cargan en Gastos fijos."),
    "gastos_local": ("Gastos del local", "Lo que cuesta tener el local funcionando y que no es insumo ni sueldo: luz, agua, internet, alarma, comisión de Transbank, contadora, publicidad, arreglos."),
    "sin_categoria": ("Facturas sin categoría", "Facturas de proveedores que el panel todavía no sabe si son insumos, gastos o compra de equipos. Mientras no les pongas categoría, se restan de la ganancia pero no aparecen en ningún grupo."),
    "insumos_sueldos": ("Insumos + sueldos", "La suma de los dos costos más grandes de una cafetería. Es el número que más miran los dueños de restaurantes y cafés: si pasa de $600 por cada $1.000 vendidos, es muy difícil que quede ganancia."),
    "margen_bruto": ("Lo que queda después de insumos", "De cada venta, la parte que sobra una vez pagado lo que costó el producto. Con eso tienes que pagar sueldos y gastos del local, y lo que sobre es ganancia."),
    "equilibrio": ("Venta mínima diaria", "Cuánto tienes que vender por día abierto (con IVA, como lo ves en caja) para cubrir sueldos y gastos del local. Si vendes menos que esto, ese día pierdes plata; todo lo que vendas por encima empieza a dejar ganancia."),
    "ticket": ("Promedio por venta", "Cuánto gasta en promedio cada cliente por compra, con IVA. Sube cuando vendes combos o agregas un pastel al café."),
    "iva_pagar": ("IVA a pagar", "El IVA que cobraste en tus ventas menos el IVA que ya pagaste en tus compras con factura. La diferencia se le paga al SII todos los meses en el formulario 29. Este número es una estimación: el oficial lo calcula la contadora."),
    "iva_cobrado": ("IVA que cobraste", "El 19% incluido en lo que pagaron tus clientes. No es tuyo: se lo debes al SII."),
    "iva_compras": ("IVA de tus compras", "El 19% que pagaste en las facturas de tus proveedores. Se descuenta del IVA que debes. Solo cuenta si la compra tiene factura a nombre de la empresa."),
    "ppm": ("PPM", "Pago Provisional Mensual: un adelanto del impuesto a la renta que se paga cada mes junto con el IVA. Es un porcentaje de la venta sin IVA; la tasa te la dice la contadora o aparece en tu formulario 29."),
    "f29": ("Formulario 29", "La declaración mensual al SII donde se paga el IVA y el PPM. La presenta la contadora."),
    "precio_carta": ("Precio de carta", "El precio que paga el cliente, con IVA incluido. Se calcula como el promedio de lo cobrado en FUDO en el período, así que incluye descuentos y promociones."),
    "costo_unidad": ("Te cuesta", "Lo que te cuesta a ti cada unidad, sin IVA. Sale de la receta (insumos × cantidades) o del precio que te cobra el proveedor si lo compras hecho."),
    "deja_unidad": ("Te deja por unidad", "Precio de venta sin IVA menos lo que te cuesta. Es la plata que queda por cada unidad vendida para pagar sueldos, gastos y ganancia."),
    "margen": ("Margen", "Qué parte del precio (sin IVA) te queda después de pagar el costo del producto. 70% significa que de cada $1.000, $700 quedan y $300 son costo. En café lo normal es 70% o más; en pastelería comprada, 40% a 50%."),
    "aporte": ("Ganancia total del producto", "Lo que te deja por unidad multiplicado por las unidades vendidas en el período. Muestra qué productos pagan realmente las cuentas del local."),
    "grupo": ("Grupo", "Clasificación automática según cuánto se vende y cuánto deja cada producto, comparado con el resto de tu carta."),
    "g_estrella": ("Estrella", "Se vende mucho y deja buena plata por unidad. Cuídalo: no le subas el costo ni lo escondas en la carta."),
    "g_caballo": ("Deja poco", "Se vende mucho pero deja poca plata por unidad. Es donde más conviene actuar: subir un poco el precio, negociar el costo o achicar la porción."),
    "g_puzzle": ("Se vende poco", "Deja buena plata por unidad pero se vende poco. Conviene mostrarlo más: recomendarlo, ponerlo en promoción o en una foto."),
    "g_perro": ("Débil", "Se vende poco y deja poco. Evalúa cambiarlo, subirle el precio o sacarlo de la carta."),
    "cobertura": ("Productos con costo cargado", "Qué parte de tu venta corresponde a productos a los que ya les pusiste costo. Mientras más cerca de 100%, más confiables son los márgenes."),
    "receta": ("Receta", "La lista de insumos y cantidades que lleva un producto. El panel multiplica cada cantidad por el precio del insumo para saber cuánto te cuesta."),
    "formato_compra": ("Formato de compra", "Cómo viene el insumo cuando lo compras: por ejemplo un saco de 1.000 g o una caja de 1.000 vasos. Con el precio del formato el panel calcula cuánto vale cada gramo, mililitro o unidad."),
    "categoria": ("Categoría", "A qué grupo pertenece el gasto. Decide en qué línea aparece: insumos, sueldos, gastos del local o compra de equipos."),
    "equipos": ("Compra de equipos", "Máquinas, muebles y cosas que duran años. No se restan de la ganancia del mes porque no son un gasto de operar: son una inversión."),
    "por_compras": ("Costo por compras", "Cada factura cuenta en el mes de su fecha, que es cuando se compró, no cuando se usó lo comprado. Si un mes compras café para dos meses, ese mes se ve peor y el siguiente mejor. Por eso conviene mirar el promedio de varios meses. Además, una factura de fin de mes puede aparecer recién en el archivo del SII del mes siguiente: al subirlo, el mes anterior se corrige solo."),
    "estimacion": ("Venta estimada", "Lo que deberías vender ese día si todo sigue como viene: el promedio del mismo día de la semana en las últimas 8 semanas, ajustado por si las ventas vienen subiendo o bajando. No conoce feriados, lluvia ni eventos. El rango muestra lo normal entre un día flojo y uno bueno."),
    "tendencia": ("Tendencia", "Compara las ventas de las últimas 4 semanas con las 4 semanas anteriores. Si es positiva, vienes creciendo."),
    "mapa_horas": ("Mapa de horas", "Cuánto vendes en promedio en cada hora de cada día de la semana, en las últimas 12 semanas. Más oscuro es más venta. Sirve para decidir turnos, horarios de promoción y cuándo poner anuncios."),
    "precio_sugerido": ("Precio sugerido", "El precio de carta (con IVA) más bajo, terminado en 90, con que el producto llega al margen esperado para su tipo: 70% en lo que se prepara en el local y 45% en lo que se compra hecho. Si habría que subir más de 20%, no se sugiere precio: el problema es el costo."),
    "extra_mes": ("Ganancia extra al mes", "Cuánto más ganarías al mes con el cambio, si se vendiera la misma cantidad que hoy. Es un máximo: si subes el precio puede que se venda algo menos."),
    "comision_tarjetas": ("Comisión de tarjetas", "Lo que cobra Transbank por cada venta con tarjeta. No te llega una cuenta para pagar: Transbank lo descuenta de lo que te deposita y emite facturas electrónicas a nombre de la empresa, que aparecen en el registro de compras del SII. La comisión de débito es más baja que la de crédito."),
    "comision_estimada": ("Comisión estimada", "Transbank factura a fin de mes. Mientras no llegue esa factura, el panel calcula la comisión con la tasa: la que pongas en Gastos fijos para débito y crédito, o si no la pones, la tasa promedio de las facturas de Transbank de los últimos 3 meses. Cuando llega la factura real, se usa la real."),
    "nota_credito": ("Nota de crédito", "Documento con que un proveedor anula o descuenta una factura. Se resta de las compras."),
    "gasto_fijo": ("Gasto fijo", "Un gasto que se repite todos los meses por un monto parecido y que no llega como factura al SII: sueldos, imposiciones, cuentas a nombre de una persona, compras sin factura."),
}

# Nombres legibles de las categorías, agrupadas como aparecen en los selectores.
CATEGORIAS_UI = [
    ("Insumos y productos para vender", [
        ("cafe", "Café"), ("leche", "Leche"), ("te", "Té, chai y matcha"), ("pasteleria", "Pastelería y galletas"),
        ("sandwiches", "Sándwiches"), ("bebidas", "Bebidas y jugos"), ("envases", "Vasos y envases"),
        ("otros_insumos", "Otros insumos (chocolate, jarabes)")]),
    ("Sueldos", [("remuneraciones", "Sueldos e imposiciones")]),
    ("Gastos del local", [
        ("servicios", "Luz, agua y gas"), ("telecom", "Internet y teléfono"), ("seguridad", "Alarma y seguridad"),
        ("comision_tarjetas", "Comisión de tarjetas (Transbank)"), ("comision_delivery", "Comisión de delivery"),
        ("honorarios", "Contadora y honorarios"), ("marketing", "Publicidad"),
        ("mantencion", "Arreglos y mantención"), ("otros_gastos", "Otros gastos")]),
    ("No es gasto del mes", [("activo_fijo", "Compra de equipos (inversión)")]),
]
CAT_NOMBRE = {k: n for _, items in CATEGORIAS_UI for k, n in items}
GRUPO_NOMBRE = {"estrella": "Estrella", "caballo": "Deja poco", "puzzle": "Se vende poco", "perro": "Débil"}
