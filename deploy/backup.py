"""Copia diaria de la base de datos. Guarda 60 días. Uso: python deploy/backup.py /var/lib/lazzy/lazzy.db"""
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

db = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/lazzy/lazzy.db")
dest = db.parent / "respaldos"
dest.mkdir(mode=0o700, exist_ok=True)
out = dest / f"lazzy-{date.today().isoformat()}.db"
src, dst = sqlite3.connect(db), sqlite3.connect(out)
src.backup(dst)        # copia consistente aunque el panel esté escribiendo
dst.close(); src.close()
for f in dest.glob("lazzy-*.db"):
    if f.stat().st_mtime < time.time() - 60 * 86400:
        f.unlink()
print("respaldo:", out)
