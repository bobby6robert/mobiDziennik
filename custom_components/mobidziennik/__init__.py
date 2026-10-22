"""Integracja mobiDziennik dla Home Assistant."""
from __future__ import annotations

import logging
import os
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MobiAuthError, MobiConnectionError, MobiData, MobiDziennikClient
from .const import CONF_SCAN_MINUTES, CONF_SERVER, DEBUG_DIR, DEFAULT_SCAN_MINUTES, DOMAIN

_LOGGER = logging.getLogger(__name__)
MAX_BODIES = 15
PLATFORMS = [Platform.SENSOR]


class MobiCoordinator(DataUpdateCoordinator[MobiData]):
    """Pobiera dane z mobiDziennika co X minut."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        minutes = entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_{entry.entry_id}",
                         update_interval=timedelta(minutes=minutes))
        session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
        self.client = MobiDziennikClient(session, entry.data[CONF_SERVER],
                                         entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
        # treści wiadomości (id -> {'tresc', 'zalaczniki'}) – pobierane raz, trzymane w pamięci
        self.bodies: dict[str, dict] = {}

    async def _load_bodies(self, data: MobiData, ids: list[str]) -> None:
        for msg_id in ids:
            try:
                self.bodies[msg_id] = await self.client.fetch_message(msg_id)
            except MobiAuthError:
                raise
            except MobiConnectionError as err:
                data.errors.append(f"wiadomosc {msg_id}: {err}")

    def _apply_bodies(self, data: MobiData) -> None:
        for m in data.messages:
            mid = str(m.get("id") or "")
            if not mid:
                continue
            m["url"] = self.client.message_url(mid)
            b = self.bodies.get(mid)
            if b:
                m["tresc"] = b.get("tresc", "")
                m["zalaczniki"] = b.get("zalaczniki", [])
                m["tresc_pobrana"] = True
            else:
                m["tresc_pobrana"] = False

    async def _async_update_data(self) -> MobiData:
        try:
            data = await self.client.fetch_all()
            # automatycznie tylko PRZECZYTANE (otwarcie nieprzeczytanej oznacza ją jako przeczytaną)
            todo = [str(m["id"]) for m in data.messages[:MAX_BODIES]
                    if m.get("id") and m.get("przeczytana") and str(m["id"]) not in self.bodies][:8]
            await self._load_bodies(data, todo)
        except MobiAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MobiConnectionError as err:
            raise UpdateFailed(str(err)) from err
        self._apply_bodies(data)
        if data.errors:
            _LOGGER.warning("mobiDziennik – część danych nie została odczytana: %s", "; ".join(data.errors))
        return data

    async def read_unread(self, msg_id: str | None = None) -> None:
        """Pobiera treść nieprzeczytanych (oznacza je w mobiDzienniku jako przeczytane)."""
        data = self.data
        if data is None:
            return
        ids = [msg_id] if msg_id else [str(m["id"]) for m in data.messages[:MAX_BODIES]
                                        if m.get("id") and not m.get("przeczytana")]
        try:
            await self._load_bodies(data, [i for i in ids if i])
        except MobiAuthError as err:
            _LOGGER.error("mobiDziennik: %s", err)
            return
        await self.async_request_refresh()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = MobiCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))

    if not hass.services.has_service(DOMAIN, "zapisz_strony"):
        async def _dump(call: ServiceCall) -> None:
            """Zapisuje surowe strony do /config/mobidziennik_debug (diagnostyka parserów)."""
            path = hass.config.path(DEBUG_DIR)
            for coord in hass.data.get(DOMAIN, {}).values():
                data = await coord.client.fetch_all(keep_raw=True)

                def _write() -> None:
                    os.makedirs(path, exist_ok=True)
                    for name, html in data.raw_pages.items():
                        with open(os.path.join(path, f"{coord.client.server}_{name}.html"), "w", encoding="utf-8") as f:
                            f.write(html)
                await hass.async_add_executor_job(_write)
            _LOGGER.warning("mobiDziennik: strony zapisane w %s", path)

        async def _refresh(call: ServiceCall) -> None:
            for coord in hass.data.get(DOMAIN, {}).values():
                await coord.async_request_refresh()

        async def _read(call: ServiceCall) -> None:
            msg_id = call.data.get("id")
            for coord in hass.data.get(DOMAIN, {}).values():
                await coord.read_unread(str(msg_id) if msg_id else None)

        hass.services.async_register(DOMAIN, "przeczytaj", _read)
        hass.services.async_register(DOMAIN, "zapisz_strony", _dump)
        hass.services.async_register(DOMAIN, "odswiez", _refresh)
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok
