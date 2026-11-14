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

## Estado
- [x] Fase 1: base de datos, importadores FUDO/SII, estado de resultados, IVA, punto de equilibrio
- [x] Fase 1b: ventas por producto + costos -> margen por producto
- [ ] Fase 2: panel web con login
- [ ] Fase 3: bot de Telegram (facturas, cierre diario, alertas)
- [ ] Fase 4: sincronización API FUDO (requiere Plan Pro)
