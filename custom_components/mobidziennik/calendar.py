"""Kalendarz klasowy mobiDziennika jako kalendarz Home Assistant."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import MobiCoordinator
from .const import CONF_STUDENT, DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coord: MobiCoordinator = hass.data[DOMAIN][entry.entry_id]
    if "kalendarz" in coord.modules:
        add([MobiCalendar(coord, entry)])


def _event(ev: dict) -> CalendarEvent:
    start = date.fromisoformat(ev["start"])
    end = date.fromisoformat(ev["koniec"]) + timedelta(days=1)  # HA: koniec wyłączny
    desc = ev.get("opis") or ""
    return CalendarEvent(start=start, end=end, summary=ev["tytul"],
                         description=f"{ev['rodzaj_nazwa']}{': ' + desc if desc else ''}", uid=ev.get("id") or None)


class MobiCalendar(CoordinatorEntity[MobiCoordinator], CalendarEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-star"

    def __init__(self, coord: MobiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{entry.entry_id}_kalendarz_cal"
        self._attr_name = "Kalendarz szkolny"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                            name=f"mobiDziennik {entry.data.get(CONF_STUDENT, '')}".strip(),
                                            entry_type=DeviceEntryType.SERVICE)

    @property
    def _items(self) -> list[dict]:
        return (self.coordinator.data.calendar if self.coordinator.data else []) or []

    @property
    def event(self) -> CalendarEvent | None:
        today = dt_util.now().date().isoformat()
        nxt = [e for e in self._items if e["koniec"] >= today]
        return _event(nxt[0]) if nxt else None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        s, e = start_date.date().isoformat(), end_date.date().isoformat()
        return [_event(ev) for ev in self._items if ev["koniec"] >= s and ev["start"] <= e]
