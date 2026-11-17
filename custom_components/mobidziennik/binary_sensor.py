"""Dyżur ucznia (z kalendarza klasowego)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
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
        add([DutySensor(coord, entry)])


class DutySensor(CoordinatorEntity[MobiCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:broom"

    def __init__(self, coord: MobiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{entry.entry_id}_dyzur"
        self._attr_name = "Dyżur"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                            name=f"mobiDziennik {entry.data.get(CONF_STUDENT, '')}".strip(),
                                            entry_type=DeviceEntryType.SERVICE)

    def _mine(self) -> list[dict]:
        data = self.coordinator.data
        return [e for e in (data.calendar if data else []) if e.get("moj")]

    @property
    def is_on(self) -> bool:
        t = dt_util.now().date().isoformat()
        return any(e["start"] <= t <= e["koniec"] for e in self._mine())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = dt_util.now().date().isoformat()
        nxt = next((e for e in self._mine() if e["koniec"] >= t), None)
        return {"mobidziennik": "dyzur", "najblizszy_od": nxt["start"] if nxt else None,
                "najblizszy_do": nxt["koniec"] if nxt else None}
