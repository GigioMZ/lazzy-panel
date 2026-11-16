# lazzy-panel

Panel financiero de Lazzy Coffee: ventas FUDO + compras SII -> estado de resultados, IVA y KPIs.

## Uso
```
pip install -r requirements.txt
python -m lazzy.cli import-fudo "Reporte-Ventas.xlsx"                  # export de FUDO
python -m lazzy.cli import-rcv RCV_COMPRA_REGISTRO_<rut>_<AAAAMM>.csv   # registro de compras SII
python -m lazzy.cli report 2026-10
pytest -q
```
Los imports son idempotentes. Parámetros del negocio: copiar `config/lazzy.example.toml` a `config/lazzy.toml` (no se versiona).
Los datos (`data/`) no se versionan.

## Panel web
```
python -m lazzy.cli seed                 # carga inicial desde config/lazzy.toml (o subir el .toml en /cargar)
python -m lazzy.cli user guillermo       # crea usuario
uvicorn lazzy.web:app --port 8765
```
Servidor: `sudo sh deploy/install.sh panel.lazzycoffee.cl` (Ubuntu + Caddy + systemd; se actualiza solo desde GitHub cada 5 min).

## Bot de Telegram
`sudo sh deploy/install_bot.sh` pide el token y la clave de Anthropic (modelo por defecto: `claude-haiku-5-5`, cambiable con `LAZZY_MODELO`).
Cada persona se vincula desde el panel, página «Bot de Telegram».

## Estado
- [x] Fase 1: base de datos, importadores FUDO/SII, estado de resultados, IVA, punto de equilibrio
- [x] Fase 1b: ventas por producto + costos -> margen por producto
- [x] Fase 2: panel web con login (resumen, resultados, productos y recetas, insumos, compras, costos fijos, impuestos, carga de archivos)
- [x] Fase 2b: rediseño con la marca, lenguaje simple, ayudas «?», veredicto del mes, desglose por cada $1.000, pendientes y mapa de productos
- [x] Fase 2c: compras por fecha del documento, comisión de tarjetas (facturada o estimada), historial de costos, migraciones, respaldos diarios
- [x] Fase 3: bot de Telegram (facturas PDF/foto, cuentas escritas, archivos FUDO/SII, avisos de precio, resumen semanal)
- [ ] Fase 4: sincronización API FUDO (requiere Plan Pro)
