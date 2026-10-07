"""Работа с Tor: проверка, что трафик идёт через Tor, и смена выходного IP (SIGNAL NEWNYM).

Tor поднимается отдельным контейнером (папка tor/, сервис tor в docker-compose.yml).
Включается в settings.env: USE_TOR=1.
"""
import logging
import socket
import threading
import time

import requests

from config import TM_PROXY, TOR_CONTROL_HOST, TOR_CONTROL_PASSWORD, TOR_CONTROL_PORT

log = logging.getLogger(__name__)

_lock = threading.Lock()
_last_newnym = 0.0
NEWNYM_MIN_INTERVAL = 10  # Tor сам не даёт менять цепочку чаще раза в ~10 с


def enabled() -> bool:
    return bool(TM_PROXY)


def proxies() -> dict | None:
    return {"http": TM_PROXY, "https": TM_PROXY} if TM_PROXY else None


def _control(*commands: str) -> list[str]:
    with socket.create_connection((TOR_CONTROL_HOST, TOR_CONTROL_PORT), timeout=10) as s:
        f = s.makefile("rwb")
        replies = []
        for cmd in (f'AUTHENTICATE "{TOR_CONTROL_PASSWORD}"', *commands):
            f.write((cmd + "\r\n").encode())
            f.flush()
            line = f.readline().decode().strip()
            if not line.startswith("250"):
                raise RuntimeError(f"Tor ControlPort: '{cmd.split()[0]}' -> {line}")
            replies.append(line)
        f.write(b"QUIT\r\n")
        f.flush()
        return replies


def new_identity() -> bool:
    """Просит Tor построить новые цепочки -> новый выходной IP для новых соединений."""
    global _last_newnym
    with _lock:
        wait = NEWNYM_MIN_INTERVAL - (time.monotonic() - _last_newnym)
        if wait > 0:
            time.sleep(wait)
        try:
            _control("SIGNAL NEWNYM")
        except (OSError, RuntimeError) as e:
            log.warning("Не удалось сменить IP через Tor: %s", e)
            return False
        _last_newnym = time.monotonic()
    time.sleep(2)  # даём Tor построить цепочку
    return True


def exit_ip() -> str | None:
    try:
        r = requests.get("https://check.torproject.org/api/ip", proxies=proxies(), timeout=30)
        data = r.json()
        return f"{data.get('IP')} (Tor: {'да' if data.get('IsTor') else 'НЕТ'})"
    except (requests.RequestException, ValueError) as e:
        return f"не удалось определить ({type(e).__name__})"


def check() -> None:
    """Пишет в лог, через какой IP идут запросы. Вызывается один раз при старте шага."""
    if enabled():
        log.info("Transfermarkt: запросы через Tor (%s), выходной IP: %s", TM_PROXY, exit_ip())
