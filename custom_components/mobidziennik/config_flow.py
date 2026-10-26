"""Konfiguracja integracji mobiDziennik z interfejsu HA."""
from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
import logging

from homeassistant.helpers.aiohttp_client import async_create_clientsession

_LOGGER = logging.getLogger(__name__)

from .api import MobiAuthError, MobiConnectionError, MobiDziennikClient
from .const import CONF_DASHBOARD, CONF_SCAN_MINUTES, CONF_SERVER, CONF_STUDENT, DEFAULT_SCAN_MINUTES, DOMAIN


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_SERVER, default=defaults.get(CONF_SERVER, "")): str,
        vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_STUDENT, default=defaults.get(CONF_STUDENT, "")): str,
        vol.Optional(CONF_DASHBOARD, default=defaults.get(CONF_DASHBOARD, True)): bool,
    })


async def _validate(hass, data: dict[str, Any]) -> str | None:
    session = async_create_clientsession(hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar())
    client = MobiDziennikClient(session, data[CONF_SERVER], data[CONF_USERNAME], data[CONF_PASSWORD])
    try:
        await client.login()
    except MobiAuthError as err:
        _LOGGER.warning("mobiDziennik – logowanie odrzucone: %s", err)
        return "invalid_auth"
    except MobiConnectionError as err:
        _LOGGER.warning("mobiDziennik – błąd połączenia: %s", err)
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("mobiDziennik – nieoczekiwany błąd podczas logowania")
        return "cannot_connect"
    finally:
        await session.close()
    return None


class MobiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_SERVER] = (
                user_input[CONF_SERVER].strip().lower().replace("https://", "").replace("http://", "").split(".")[0]
            )
            await self.async_set_unique_id(f"{user_input[CONF_SERVER]}_{user_input[CONF_USERNAME].lower()}")
            self._abort_if_unique_id_configured()
            err = await _validate(self.hass, user_input)
            if err:
                errors["base"] = err
            else:
                return self.async_create_entry(title=f"mobiDziennik {user_input[CONF_STUDENT]}", data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema(user_input or {}), errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            err = await _validate(self.hass, data)
            if err:
                errors["base"] = err
            else:
                self.hass.config_entries.async_update_entry(entry, data=data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        return self.async_show_form(step_id="reauth_confirm",
                                    data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return MobiOptionsFlow()


class MobiOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        entry = self.config_entry
        if user_input is not None:
            if user_input.pop(CONF_DASHBOARD, False):
                # „Utwórz pulpit ponownie” – karta w przeglądarce administratora zrobi to sama
                from .pulpit import async_reset_dashboard

                self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_DASHBOARD: True})
                await async_reset_dashboard(self.hass, entry.entry_id)
            return self.async_create_entry(title="", data=user_input)
        current = entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(CONF_SCAN_MINUTES, default=current): vol.All(int, vol.Range(min=10, max=240)),
            vol.Optional(CONF_DASHBOARD, default=False): bool,
        }))
