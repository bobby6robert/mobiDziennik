"""Czujniki mobiDziennika (zgodne z Aurora / Vultron dashboard)."""
from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MobiCoordinator
from .const import CONF_STUDENT, DOMAIN

HEAVY = frozenset({"lekcje", "oceny", "wiadomosci", "bledy", "sprawdziany", "frekwencja", "uwagi"})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coord: MobiCoordinator = hass.data[DOMAIN][entry.entry_id]
    add([
        PlanSensor(coord, entry),
        MessagesSensor(coord, entry),
        GradesSensor(coord, entry),
        LuckySensor(coord, entry),
        StatusSensor(coord, entry),
        ExamsSensor(coord, entry),
        AttendanceSensor(coord, entry),
        RemarksSensor(coord, entry),
    ])


class _Base(CoordinatorEntity[MobiCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _unrecorded_attributes = HEAVY

    def __init__(self, coord: MobiCoordinator, entry: ConfigEntry, key: str, name: str, icon: str) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        student = entry.data.get(CONF_STUDENT, "")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"mobiDziennik {student}".strip(),
            manufacturer="mobiDziennik (WizjaNet)",
            model=f"{coord.client.server}.mobidziennik.pl",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _d(self):
        return self.coordinator.data


class PlanSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "plan", "Plan", "mdi:calendar-clock")
        self._attr_native_unit_of_measurement = "lekcji"

    @property
    def native_value(self) -> int:
        today = date.today().isoformat()
        return sum(1 for l in self._d.lessons if l.get("d") == today)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_update": self._d.last_update, "lekcje": self._d.lessons}


class MessagesSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "wiadomosci", "Wiadomości", "mdi:email-outline")
        self._attr_native_unit_of_measurement = "nieprzeczytanych"

    @property
    def native_value(self) -> int:
        return sum(1 for m in self._d.messages if not m.get("przeczytana"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_update": self._d.last_update, "wiadomosci": self._d.messages[:30]}


class GradesSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "oceny", "Oceny", "mdi:star-circle-outline")
        self._attr_native_unit_of_measurement = "ocen"

    @property
    def native_value(self) -> int:
        return len(self._d.grades)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        grades = sorted(self._d.grades, key=lambda g: g.get("data") or "", reverse=True)
        subjects: dict[str, list[float]] = {}
        for g in self._d.grades:
            if g.get("wartosc") and g.get("do_sredniej"):
                subjects.setdefault(g["przedmiot"], []).append(g["wartosc"])
        avgs = {k: round(sum(v) / len(v), 2) for k, v in subjects.items() if v}
        return {"last_update": self._d.last_update, "oceny": grades[:60], "srednie": avgs}


class LuckySensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "szczesliwy_numerek", "Szczęśliwy numerek", "mdi:clover")

    @property
    def native_value(self):
        return self._d.lucky_number

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_update": self._d.last_update}


class StatusSensor(_Base):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c, e):
        super().__init__(c, e, "status", "Status", "mdi:school-outline")

    @property
    def native_value(self) -> str:
        return "błędy" if self._d.errors else "ok"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "last_update": self._d.last_update,
            "bledy": self._d.errors,
            "lekcji": len(self._d.lessons),
            "ocen": len(self._d.grades),
            "wiadomosci_liczba": len(self._d.messages),
            "sprawdziany_liczba": len(self._d.exams),
            "frekwencja_liczba": len(self._d.attendance),
            "uwagi_liczba": len(self._d.remarks),
        }


class ExamsSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "sprawdziany", "Sprawdziany", "mdi:file-document-edit-outline")
        self._attr_native_unit_of_measurement = "zapowiedzianych"

    @property
    def native_value(self) -> int:
        return len(self._d.exams)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_update": self._d.last_update, "sprawdziany": self._d.exams}


class AttendanceSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "frekwencja", "Frekwencja", "mdi:account-check-outline")
        self._attr_native_unit_of_measurement = "wpisów"

    @property
    def native_value(self) -> int:
        return len(self._d.attendance)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_update": self._d.last_update, "frekwencja": self._d.attendance}


class RemarksSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "uwagi", "Uwagi i pochwały", "mdi:comment-alert-outline")
        self._attr_native_unit_of_measurement = "uwag"

    @property
    def native_value(self) -> int:
        return len(self._d.remarks)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_update": self._d.last_update, "uwagi": self._d.remarks}
