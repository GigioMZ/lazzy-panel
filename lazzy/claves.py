"""Claves del bot (token de Telegram y API de Anthropic), guardadas en un archivo que solo lee el usuario del panel."""
import os
from pathlib import Path

import httpx


def ruta():
    return Path(os.environ.get("LAZZY_BOT_ENV") or Path(os.environ.get("LAZZY_DB", "data/lazzy.db")).parent / "bot.env")


def leer():
    p, out = ruta(), {}
    if p.exists():
        for ln in p.read_text().splitlines():
            if "=" in ln and not ln.startswith("#"):
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def probar_telegram(token):
    r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15).json()
    if not r.get("ok"):
        raise ValueError("El token de Telegram no funciona. Cópialo de nuevo desde @BotFather.")
    return r["result"]["username"]


def probar_anthropic(clave):
    r = httpx.get("https://api.anthropic.com/v1/models", timeout=15,
                  headers={"x-api-key": clave, "anthropic-version": "2023-06-01"})
    if r.status_code != 200:
        raise ValueError(f"La clave de Anthropic no funciona (respuesta {r.status_code}). Revísala en console.anthropic.com.")


def guardar(token=None, clave=None, probar=True):
    actual = leer()
    if token:
        actual["BOT_NOMBRE"] = probar_telegram(token) if probar else actual.get("BOT_NOMBRE", "")
        actual["TELEGRAM_TOKEN"] = token
    if clave:
        if probar:
            probar_anthropic(clave)
        actual["ANTHROPIC_API_KEY"] = clave
    p = ruta()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("".join(f"{k}={v}\n" for k, v in actual.items()))
    os.replace(tmp, p)
    return actual


def estado():
    c = leer()
    return {"telegram": bool(c.get("TELEGRAM_TOKEN")), "anthropic": bool(c.get("ANTHROPIC_API_KEY")),
            "nombre": c.get("BOT_NOMBRE", "")}
