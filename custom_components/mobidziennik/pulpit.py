"""Karta „mobiDziennik” i kreator pulpitu.

* rejestruje plik karty (frontend/mobidziennik-card.js) – ładuje się sam, bez dodawania zasobów,
* trzyma listę uczniów, dla których trzeba utworzyć pulpit (zaznaczone przy dodawaniu integracji),
* udostępnia dwa polecenia WebSocket, z których korzysta karta w przeglądarce administratora:
  mobidziennik/pulpit         – lista uczniów czekających na pulpit,
  mobidziennik/pulpit_gotowy  – oznacza pulpit ucznia jako utworzony.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store

from .const import CARD_URL, CONF_DASHBOARD, CONF_STUDENT, DOMAIN, STORE_KEY, VERSION

_LOGGER = logging.getLogger(__name__)
DATA_STORE = f"{DOMAIN}_pulpit_store"


async def async_setup_frontend(hass: HomeAssistant) -> None:
    """Rejestruje kartę i polecenia WebSocket (raz na start HA)."""
    path = Path(__file__).parent / "frontend" / "mobidziennik-card.js"
    try:
        await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), True)])
    except Exception:  # noqa: BLE001 – brak http (np. testy) nie może blokować czujników
        _LOGGER.warning("mobiDziennik: nie udało się udostępnić pliku karty", exc_info=True)
    url = f"{CARD_URL}?v={VERSION}"
    # Zasób pulpitu (Lovelace) ładuje się po starcie interfejsu – tak jak karty z HACS.
    # „extra_js_url” ładuje się za wcześnie (przed rejestrem elementów interfejsu) i karta byłaby niewidoczna.
    if not await _async_register_resource(hass, url):
        add_extra_js_url(hass, url)

    store: Store[dict[str, Any]] = Store(hass, 1, STORE_KEY)
    data = await store.async_load() or {}
    hass.data[DATA_STORE] = {"store": store, "gotowe": set(data.get("gotowe", []))}

    websocket_api.async_register_command(hass, ws_pulpit)
    websocket_api.async_register_command(hass, ws_pulpit_gotowy)


async def _async_register_resource(hass: HomeAssistant, url: str) -> bool:
    """Dodaje / aktualizuje zasób Lovelace z kartą. False = nie da się (np. pulpity w trybie YAML)."""
    try:
        ll = hass.data.get("lovelace")
        resources = getattr(ll, "resources", None) if ll is not None else None
        if resources is None and isinstance(ll, dict):
            resources = ll.get("resources")
        if resources is None or not hasattr(resources, "async_create_item"):
            return False
        if hasattr(resources, "async_get_info"):
            await resources.async_get_info()  # wczytuje listę zasobów, jeśli jeszcze nie wczytana
        for item in resources.async_items():
            if str(item.get("url", "")).split("?")[0] == CARD_URL:
                if item.get("url") != url:
                    await resources.async_update_item(item["id"], {"res_type": "module", "url": url})
                return True
        await resources.async_create_item({"res_type": "module", "url": url})
        return True
    except Exception:  # noqa: BLE001
        _LOGGER.warning("mobiDziennik: nie udało się dodać zasobu karty – użyję zapasowego ładowania", exc_info=True)
        return False


async def async_reset_dashboard(hass: HomeAssistant, entry_id: str) -> None:
    """Po zaznaczeniu „Utwórz pulpit ponownie” w opcjach."""
    st = hass.data.get(DATA_STORE)
    if st and entry_id in st["gotowe"]:
        st["gotowe"].discard(entry_id)
        await st["store"].async_save({"gotowe": sorted(st["gotowe"])})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "mobidziennik/pulpit"})
@callback
def ws_pulpit(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    st = hass.data.get(DATA_STORE) or {"gotowe": set()}
    devreg = dr.async_get(hass)
    out = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if not entry.data.get(CONF_DASHBOARD, False) or entry.entry_id in st["gotowe"]:
            continue
        dev = devreg.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
        if dev is None:  # czujniki jeszcze się nie utworzyły – spróbujemy przy następnym wejściu
            continue
        out.append({"entry_id": entry.entry_id, "device_id": dev.id,
                    "uczen": entry.data.get(CONF_STUDENT) or entry.title})
    connection.send_result(msg["id"], out)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "mobidziennik/pulpit_gotowy", vol.Required("entry_id"): str})
@websocket_api.async_response
async def ws_pulpit_gotowy(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    st = hass.data.get(DATA_STORE)
    if st is not None:
        st["gotowe"].add(msg["entry_id"])
        await st["store"].async_save({"gotowe": sorted(st["gotowe"])})
    connection.send_result(msg["id"], True)
