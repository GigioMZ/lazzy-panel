import hashlib
import hmac
import secrets


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310_000).hex()


def set_password(con, nombre, password):
    salt = secrets.token_hex(16)
    con.execute("INSERT OR REPLACE INTO usuario VALUES (?,?,?)", (nombre, _hash(password, salt), salt))
    con.commit()


def check_password(con, nombre, password):
    r = con.execute("SELECT hash, salt FROM usuario WHERE nombre=?", (nombre,)).fetchone()
    if not r:
        _hash(password, "00" * 16)  # mismo costo de tiempo aunque el usuario no exista
        return False
    return hmac.compare_digest(r["hash"], _hash(password, r["salt"]))
