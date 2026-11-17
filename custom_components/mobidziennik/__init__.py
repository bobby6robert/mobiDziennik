"""Integracja mobiDziennik dla Home Assistant."""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

import voluptuous as vol

from .api import MobiAuthError, MobiConnectionError, MobiData, MobiDziennikClient, dedupe_lessons, pick_tutor
from .const import (CONF_GENDER, CONF_MODULES, CONF_SCAN_MINUTES, CONF_SERVER, CONF_SIGNATURES, CONF_STUDENT,
                    DEBUG_DIR, DEFAULT_MODULES, DEFAULT_SCAN_MINUTES, DOMAIN)
from .usprawiedliwienie import build_excuse, excuse_weeks, item_key

_LOGGER = logging.getLogger(__name__)
MAX_BODIES = 15
PLATFORMS = [Platform.SENSOR, Platform.CALENDAR, Platform.BINARY_SENSOR]
EVENT = f"{DOMAIN}_usprawiedliwienie"

# czujnik -> moduł (czujniki modułów wyłączonych w ustawieniach są usuwane)
KEY_MODULE = {
    "plan": "plan", "wiadomosci": "wiadomosci", "oceny": "oceny", "srednia": "oceny",
    "sprawdziany": "sprawdziany", "najblizszy_sprawdzian": "sprawdziany", "frekwencja": "frekwencja",
    "nieusprawiedliwione": "frekwencja", "uwagi": "uwagi", "kalendarz": "kalendarz", "kalendarz_cal": "kalendarz",
    "dyzur": "kalendarz", "zadania": "zadania", "zastepstwa": "zastepstwa", "statystyka": "statystyka",
    "zachowanie": "zachowanie", "komunikaty": "komunikaty", "platnosci": "platnosci",
    "usprawiedliwienie": "usprawiedliwienia",
}


def entry_modules(entry: ConfigEntry) -> list[str]:
    mods = list(entry.options.get(CONF_MODULES, DEFAULT_MODULES))
    if "usprawiedliwienia" in mods and "frekwencja" not in mods:
        mods.append("frekwencja")
    return mods
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Karta mobiDziennik + kreator pulpitu (raz na start)."""
    from .pulpit import async_setup_frontend

    await async_setup_frontend(hass)
    return True


class MobiCoordinator(DataUpdateCoordinator[MobiData]):
    """Pobiera dane z mobiDziennika co X minut."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        minutes = entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN}_{entry.entry_id}",
                         update_interval=timedelta(minutes=minutes))
        session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
        self.client = MobiDziennikClient(session, entry.data[CONF_SERVER],
                                         entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
        # treści wiadomości (id -> {'tresc', 'zalaczniki'}) – pobierane raz, trzymane w pamięci
        self.bodies: dict[str, dict] = {}
        self.entry = entry
        self.modules = entry_modules(entry)
        self.store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")
        self.saved: dict = {"wyslane": [], "historia": [], "szkic": None}
        self.busy = False

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

    def _keep_previous(self, data: MobiData) -> None:
        """Gdy strona się nie pobrała (np. chwilowy błąd DNS), zostaw ostatnie dobre dane zamiast pustych."""
        prev = self.data
        if prev is None or not data.failed:
            return
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        if today.weekday() >= 5:
            monday += timedelta(days=7)
        lost = set()
        if "plan_biezacy" in data.failed:
            lost.add(monday)
        if "plan_nastepny" in data.failed:
            lost.add(monday + timedelta(days=7))
        if lost:
            def week(l: dict) -> date:
                d = date.fromisoformat(l["d"])
                return d - timedelta(days=d.weekday())
            keep = [l for l in prev.lessons if week(l) in lost]
            data.lessons = sorted(dedupe_lessons(data.lessons + keep), key=lambda x: (x["d"], x["g"]))
        for key, attr in (("oceny", "grades"), ("wiadomosci", "messages"), ("sprawdziany", "exams"),
                          ("frekwencja", "attendance"), ("uwagi", "remarks"), ("kalendarz", "calendar"),
                          ("zadania", "homework"), ("zastepstwa", "substitutions"), ("statystyka", "stats"),
                          ("zachowanie", "behavior"), ("komunikaty", "announcements"), ("platnosci", "payments")):
            if key in data.failed:
                setattr(data, attr, getattr(prev, attr))
        if data.lucky_number is None:
            data.lucky_number = prev.lucky_number
        if not data.class_name:
            data.class_name, data.student_full = prev.class_name, prev.student_full

    async def _async_update_data(self) -> MobiData:
        try:
            data = await self.client.fetch_all(modules=self.modules)
            # automatycznie tylko PRZECZYTANE (otwarcie nieprzeczytanej oznacza ją jako przeczytaną)
            todo = [str(m["id"]) for m in data.messages[:MAX_BODIES]
                    if m.get("id") and m.get("przeczytana") and str(m["id"]) not in self.bodies][:8]
            await self._load_bodies(data, todo)
        except MobiAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MobiConnectionError as err:
            raise UpdateFailed(str(err)) from err
        self._keep_previous(data)
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


    # ── USPRAWIEDLIWIENIA ─────────────────────────────────
    @property
    def draft(self) -> dict | None:
        return self.saved.get("szkic")

    async def _save(self) -> None:
        self.saved["wyslane"] = self.saved.get("wyslane", [])[-2000:]
        self.saved["historia"] = self.saved.get("historia", [])[-30:]
        await self.store.async_save(self.saved)
        self.async_update_listeners()

    def signatures(self) -> list[str]:
        raw = self.entry.options.get(CONF_SIGNATURES, "") or ""
        out = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
        if not out and self.client.account_name:
            out = [self.client.account_name]
        return out

    async def prepare_excuse(self, zakres: str) -> dict:
        if self.busy:
            raise HomeAssistantError("Usprawiedliwienie jest właśnie przygotowywane")
        self.busy = True
        try:
            items: list[dict] = []
            for monday in excuse_weeks(zakres, date.today()):
                try:
                    items += await self.client.fetch_week_attendance(monday)
                except MobiAuthError as err:
                    raise HomeAssistantError(f"mobiDziennik: {err}") from err
                except MobiConnectionError as err:
                    raise HomeAssistantError(f"mobiDziennik ({monday}): {err}") from err
            sent = set(self.saved.get("wyslane", []))
            todo = [i for i in items if i["symbol"] in ("|", "s") and item_key(i) not in sent]
            student = self.data.student_full if self.data else ""
            if not student:
                _cls, student = await self.client.class_info()
            if not todo:
                self.saved["szkic"] = None
                await self._save()
                self.hass.bus.async_fire(EVENT, {"entry_id": self.entry.entry_id, "typ": "brak", "zakres": zakres})
                return {"pozycji": 0}
            draft = build_excuse(todo, self.entry.options.get(CONF_GENDER, "syn"), student,
                                 self.entry.data.get(CONF_STUDENT, ""))
            draft.update({"zakres": zakres, "przygotowano": dt_now_str(), "klucze": [item_key(i) for i in todo]})
            self.saved["szkic"] = draft
            await self._save()
            self.hass.bus.async_fire(EVENT, {"entry_id": self.entry.entry_id, "typ": "przygotowane", "zakres": zakres,
                                             "pozycji": draft["pozycji"], "tresc": draft["tresc"], "temat": draft["temat"],
                                             "podpisy": self.signatures()})
            return {k: v for k, v in draft.items() if k != "klucze"}
        finally:
            self.busy = False

    async def send_excuse(self, podpis: str | None) -> dict:
        draft = self.draft
        if not draft:
            raise HomeAssistantError("Brak przygotowanego usprawiedliwienia")
        sigs = self.signatures()
        podpis = (podpis or (sigs[0] if sigs else "")).strip()
        if not podpis:
            raise HomeAssistantError("Podaj podpis (imię i nazwisko rodzica) w ustawieniach integracji")
        klasa = self.data.class_name if self.data else ""
        if not klasa:
            klasa, _ = await self.client.class_info()
        try:
            tutor = pick_tutor(await self.client.tutors(), klasa)
            if tutor is None:
                raise HomeAssistantError(f"Nie znaleziono wychowawcy klasy {klasa or '?'}")
            body = draft["tresc"] + f"\n\nZ poważaniem\n{podpis}"
            html = "".join(f"<p>{p.replace(chr(10), '<br />')}</p>" for p in _html_paras(body))
            await self.client.send_message(tutor["id"], draft["temat"], html)
        except MobiConnectionError as err:
            self.hass.bus.async_fire(EVENT, {"entry_id": self.entry.entry_id, "typ": "blad", "blad": str(err)})
            raise HomeAssistantError(f"Nie wysłano: {err}") from err
        self.saved["wyslane"] = self.saved.get("wyslane", []) + draft.get("klucze", [])
        self.saved.setdefault("historia", []).append({"wyslano": dt_now_str(), "podpis": podpis, "do": tutor["nazwisko"],
                                                      "temat": draft["temat"], "pozycji": draft["pozycji"],
                                                      "zakres": draft.get("zakres")})
        self.saved["szkic"] = None
        await self._save()
        self.hass.bus.async_fire(EVENT, {"entry_id": self.entry.entry_id, "typ": "wyslane", "podpis": podpis,
                                         "do": tutor["nazwisko"]})
        return {"wyslano_do": tutor["nazwisko"], "podpis": podpis}

    async def discard_excuse(self) -> None:
        self.saved["szkic"] = None
        await self._save()
        self.hass.bus.async_fire(EVENT, {"entry_id": self.entry.entry_id, "typ": "odrzucone"})


def dt_now_str() -> str:
    from homeassistant.util import dt as dt_util

    return dt_util.now().strftime("%Y-%m-%d %H:%M")


def _html_paras(text: str) -> list[str]:
    import html as htmllib

    return [htmllib.escape(p) for p in text.split("\n\n")]


def _coords(hass: HomeAssistant, call: ServiceCall) -> list[MobiCoordinator]:
    coords = list(hass.data.get(DOMAIN, {}).values())
    eid = call.data.get("entry_id")
    if eid:
        coords = [c for c in coords if c.entry.entry_id == eid]
    if not coords:
        raise HomeAssistantError("Nie znaleziono konta mobiDziennik")
    return coords


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = MobiCoordinator(hass, entry)
    coordinator.saved = (await coordinator.store.async_load()) or coordinator.saved
    await coordinator.async_config_entry_first_refresh()
    # usuń czujniki modułów wyłączonych w ustawieniach
    reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(reg, entry.entry_id):
        key = ent.unique_id.removeprefix(f"{entry.entry_id}_")
        mod = KEY_MODULE.get(key)
        if mod and mod not in coordinator.modules:
            reg.async_remove(ent.entity_id)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))

    if not hass.services.has_service(DOMAIN, "zapisz_strony"):
        async def _dump(call: ServiceCall) -> None:
            """Zapisuje surowe strony do /config/mobidziennik_debug (diagnostyka parserów)."""
            path = hass.config.path(DEBUG_DIR)
            for coord in hass.data.get(DOMAIN, {}).values():
                data = await coord.client.fetch_all(keep_raw=True, modules=coord.modules)

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

        async def _prepare(call: ServiceCall) -> ServiceResponse:
            res = {}
            for coord in _coords(hass, call):
                if "usprawiedliwienia" in coord.modules:
                    res[coord.entry.entry_id] = await coord.prepare_excuse(call.data.get("zakres", "tydzien"))
            return res

        async def _send(call: ServiceCall) -> ServiceResponse:
            res = {}
            for coord in _coords(hass, call):
                if coord.draft:
                    res[coord.entry.entry_id] = await coord.send_excuse(call.data.get("podpis"))
            return res

        async def _discard(call: ServiceCall) -> None:
            for coord in _coords(hass, call):
                await coord.discard_excuse()

        hass.services.async_register(DOMAIN, "przygotuj_usprawiedliwienie", _prepare, schema=vol.Schema({
            vol.Optional("zakres", default="tydzien"): vol.In(["tydzien", "wszystkie"]),
            vol.Optional("entry_id"): str}), supports_response=SupportsResponse.OPTIONAL)
        hass.services.async_register(DOMAIN, "wyslij_usprawiedliwienie", _send, schema=vol.Schema({
            vol.Optional("podpis"): str, vol.Optional("entry_id"): str}), supports_response=SupportsResponse.OPTIONAL)
        hass.services.async_register(DOMAIN, "odrzuc_usprawiedliwienie", _discard, schema=vol.Schema({
            vol.Optional("entry_id"): str}))
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
