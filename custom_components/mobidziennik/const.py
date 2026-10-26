"""Stałe integracji mobiDziennik."""
DOMAIN = "mobidziennik"

CONF_SERVER = "server"
CONF_STUDENT = "student"
CONF_SCAN_MINUTES = "scan_minutes"
CONF_DASHBOARD = "pulpit"

DEFAULT_SCAN_MINUTES = 30
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 HomeAssistant-mobiDziennik/1.1.2"
)
DEBUG_DIR = "mobidziennik_debug"

VERSION = "1.1.2"
CARD_URL = "/mobidziennik_static/mobidziennik-card.js"
STORE_KEY = "mobidziennik_pulpit"
