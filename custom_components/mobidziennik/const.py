"""Stałe integracji mobiDziennik."""
DOMAIN = "mobidziennik"

CONF_SERVER = "server"
CONF_STUDENT = "student"
CONF_SCAN_MINUTES = "scan_minutes"
CONF_DASHBOARD = "pulpit"
CONF_MODULES = "moduly"
CONF_GENDER = "plec"
CONF_SIGNATURES = "podpisy"

# moduł -> etykieta w ustawieniach (kolejność = kolejność na liście)
MODULES = {
    "plan": "Plan lekcji",
    "oceny": "Oceny i średnie",
    "wiadomosci": "Wiadomości",
    "sprawdziany": "Sprawdziany i kartkówki",
    "frekwencja": "Frekwencja (nieobecności)",
    "uwagi": "Uwagi i pochwały",
    "kalendarz": "Kalendarz klasowy (wydarzenia, wycieczki, dni wolne, dyżury)",
    "zadania": "Zadania domowe",
    "zastepstwa": "Zastępstwa",
    "statystyka": "Statystyka frekwencji (procent z przedmiotów)",
    "zachowanie": "Zachowanie",
    "komunikaty": "Komunikaty szkoły",
    "platnosci": "Płatności (zbiórki klasowe)",
    "usprawiedliwienia": "Usprawiedliwienia (wiadomość do wychowawcy)",
}
DEFAULT_MODULES = list(MODULES)

DEFAULT_SCAN_MINUTES = 30
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 HomeAssistant-mobiDziennik/1.2.0"
)
DEBUG_DIR = "mobidziennik_debug"

VERSION = "1.2.0"
CARD_URL = "/mobidziennik_static/mobidziennik-card.js"
STORE_KEY = "mobidziennik_pulpit"
