"""Czujniki mobiDziennika (zgodne z Aurora / Vultron dashboard)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import MobiCoordinator
from .const import CONF_STUDENT, DOMAIN

HEAVY = frozenset({"lekcje", "oceny", "wiadomosci", "bledy", "sprawdziany", "frekwencja", "uwagi", "wydarzenia",
                   "zadania", "zastepstwa", "przedmioty", "wiersze", "komunikaty", "platnosci", "tresc", "dni",
                   "historia", "jutro", "najblizsze"})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coord: MobiCoordinator = hass.data[DOMAIN][entry.entry_id]
    mods = set(coord.modules)
    ents: list[SensorEntity] = [LuckySensor(coord, entry), StatusSensor(coord, entry)]
    for mod, classes in (
        ("plan", (PlanSensor,)), ("wiadomosci", (MessagesSensor,)), ("oceny", (GradesSensor, AverageSensor)),
        ("sprawdziany", (ExamsSensor, NextExamSensor)), ("frekwencja", (AttendanceSensor, UnexcusedSensor)),
        ("uwagi", (RemarksSensor,)), ("kalendarz", (CalendarListSensor,)), ("zadania", (HomeworkSensor,)),
        ("zastepstwa", (SubstitutionsSensor,)), ("statystyka", (StatsSensor,)), ("zachowanie", (BehaviorSensor,)),
        ("komunikaty", (AnnouncementsSensor,)), ("platnosci", (PaymentsSensor,)), ("usprawiedliwienia", (ExcuseSensor,)),
    ):
        if mod in mods:
            ents += [c(coord, entry) for c in classes]
    add(ents)


class _Base(CoordinatorEntity[MobiCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _unrecorded_attributes = HEAVY

    def __init__(self, coord: MobiCoordinator, entry: ConfigEntry, key: str, name: str, icon: str) -> None:
        super().__init__(coord)
        self._key = key
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

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _midnight(_now) -> None:
            # po północy „dziś” to już inny dzień – przelicz bez czekania na pobranie danych
            self.async_write_ha_state()

        self.async_on_remove(async_track_time_change(self.hass, _midnight, hour=0, minute=0, second=5))

    def _attrs(self) -> dict[str, Any]:
        return {"last_update": self._d.last_update}

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # „mobidziennik” = rodzaj czujnika – po nim karta mobiDziennik znajduje swoje encje
        return {"mobidziennik": self._key, **self._attrs()}


class PlanSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "plan", "Plan", "mdi:calendar-clock")
        self._attr_native_unit_of_measurement = "lekcji"

    @property
    def native_value(self) -> int:
        today = dt_util.now().date().isoformat()
        return sum(1 for l in self._d.lessons if l.get("d") == today)

    def _attrs(self) -> dict[str, Any]:
        now = dt_util.now()
        today = now.date().isoformat()
        hhmm = now.strftime("%H:%M")
        dzis = [l for l in self._d.lessons if l.get("d") == today]
        # najbliższy dzień nauki: dziś (jeśli lekcje jeszcze trwają) albo kolejny dzień z lekcjami
        koniec = (dzis[-1].get("g") or "-").split("-")[-1] if dzis else ""
        if dzis and (not koniec or koniec > hhmm):
            dzien = today
        else:
            dzien = next((l["d"] for l in self._d.lessons if l.get("d", "") > today), None)
        return {
            "last_update": self._d.last_update,
            "lekcje": self._d.lessons,
            "najblizszy_dzien": dzien,
            "najblizszy_dzien_lekcji": sum(1 for l in self._d.lessons if l.get("d") == dzien) if dzien else 0,
        }


class MessagesSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "wiadomosci", "Wiadomości", "mdi:email-outline")
        self._attr_native_unit_of_measurement = "nieprzeczytanych"

    @property
    def native_value(self) -> int:
        return sum(1 for m in self._d.messages if not m.get("przeczytana"))

    def _attrs(self) -> dict[str, Any]:
        return {"last_update": self._d.last_update, "wiadomosci": self._d.messages[:30]}


class GradesSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "oceny", "Oceny", "mdi:star-circle-outline")
        self._attr_native_unit_of_measurement = "ocen"

    @property
    def native_value(self) -> int:
        return len(self._d.grades)

    def _attrs(self) -> dict[str, Any]:
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

    def _attrs(self) -> dict[str, Any]:
        return {"last_update": self._d.last_update}


class StatusSensor(_Base):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c, e):
        super().__init__(c, e, "status", "Status", "mdi:school-outline")

    @property
    def native_value(self) -> str:
        return "błędy" if self._d.errors else "ok"

    def _attrs(self) -> dict[str, Any]:
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

    def _attrs(self) -> dict[str, Any]:
        return {"last_update": self._d.last_update, "sprawdziany": self._d.exams}


class AttendanceSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "frekwencja", "Frekwencja", "mdi:account-check-outline")
        self._attr_native_unit_of_measurement = "wpisów"

    @property
    def native_value(self) -> int:
        return len(self._d.attendance)

    def _attrs(self) -> dict[str, Any]:
        return {
            "last_update": self._d.last_update,
            "frekwencja": self._d.attendance,
            **_attendance_counts(self._d.attendance),
        }


class RemarksSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "uwagi", "Uwagi i pochwały", "mdi:comment-alert-outline")
        self._attr_native_unit_of_measurement = "uwag"

    @property
    def native_value(self) -> int:
        return len(self._d.remarks)

    def _attrs(self) -> dict[str, Any]:
        return {"last_update": self._d.last_update, "uwagi": self._d.remarks}


def _attendance_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """Podsumowanie frekwencji według statusu."""
    unexcused = excused = late = 0
    for a in items:
        st = (a.get("status") or "").lower()
        if "spóźn" in st:
            late += 1
        elif "nieusprawiedliw" in st:
            unexcused += 1
        elif "usprawiedliw" in st:
            excused += 1
    return {"nieusprawiedliwione": unexcused, "usprawiedliwione": excused, "spoznienia": late}


class NextExamSensor(_Base):
    """Najbliższy zapowiedziany sprawdzian / kartkówka (data i godzina)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, c, e):
        super().__init__(c, e, "najblizszy_sprawdzian", "Najbliższy sprawdzian", "mdi:calendar-alert")

    def _next(self) -> tuple[datetime, dict[str, Any]] | None:
        now = dt_util.now()
        for ex in self._d.exams:
            iso = ex.get("data_iso")
            if not iso:
                continue
            when = dt_util.as_local(datetime.fromisoformat(iso).replace(tzinfo=dt_util.get_default_time_zone()))
            if when >= now - timedelta(hours=1):
                return when, ex
        return None

    @property
    def native_value(self) -> datetime | None:
        nxt = self._next()
        return nxt[0] if nxt else None

    def _attrs(self) -> dict[str, Any]:
        nxt = self._next()
        if not nxt:
            return {"last_update": self._d.last_update}
        when, ex = nxt
        return {
            "last_update": self._d.last_update,
            "przedmiot": ex.get("przedmiot"),
            "rodzaj": ex.get("rodzaj"),
            "tematyka": ex.get("tematyka"),
            "dni_do": (when.date() - dt_util.now().date()).days,
        }


class AverageSensor(_Base):
    """Średnia ze średnich przedmiotów (oceny liczone do średniej)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, c, e):
        super().__init__(c, e, "srednia", "Średnia ocen", "mdi:chart-line")

    def _avgs(self) -> dict[str, float]:
        subjects: dict[str, list[float]] = {}
        for g in self._d.grades:
            if g.get("wartosc") and g.get("do_sredniej"):
                subjects.setdefault(g["przedmiot"], []).append(g["wartosc"])
        return {k: round(sum(v) / len(v), 2) for k, v in subjects.items() if v}

    @property
    def native_value(self) -> float | None:
        avgs = self._avgs()
        return round(sum(avgs.values()) / len(avgs), 2) if avgs else None

    def _attrs(self) -> dict[str, Any]:
        avgs = self._avgs()
        best = max(avgs, key=avgs.get) if avgs else None
        worst = min(avgs, key=avgs.get) if avgs else None
        return {
            "last_update": self._d.last_update,
            "srednie": dict(sorted(avgs.items(), key=lambda kv: kv[1], reverse=True)),
            "najlepszy_przedmiot": best,
            "najslabszy_przedmiot": worst,
        }


class UnexcusedSensor(_Base):
    """Liczba nieusprawiedliwionych nieobecności (do szybkich automatyzacji)."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, c, e):
        super().__init__(c, e, "nieusprawiedliwione", "Nieusprawiedliwione nieobecności", "mdi:account-cancel-outline")
        self._attr_native_unit_of_measurement = "godzin"

    @property
    def native_value(self) -> int:
        return _attendance_counts(self._d.attendance)["nieusprawiedliwione"]

    def _attrs(self) -> dict[str, Any]:
        return {"last_update": self._d.last_update, **_attendance_counts(self._d.attendance)}


def _today() -> str:
    return dt_util.now().date().isoformat()


class CalendarListSensor(_Base):
    """Kalendarz klasowy jako lista (dla karty i automatyzacji)."""

    def __init__(self, c, e):
        super().__init__(c, e, "kalendarz", "Kalendarz klasowy", "mdi:calendar-star")
        self._attr_native_unit_of_measurement = "wydarzeń"

    def _upcoming(self, days: int) -> list[dict[str, Any]]:
        t = dt_util.now().date()
        last = (t + timedelta(days=days)).isoformat()
        return [ev for ev in self._d.calendar if ev["koniec"] >= t.isoformat() and ev["start"] <= last]

    @property
    def native_value(self) -> int:
        return len(self._upcoming(7))

    def _attrs(self) -> dict[str, Any]:
        jutro = (dt_util.now().date() + timedelta(days=1)).isoformat()
        return {
            "last_update": self._d.last_update,
            "klasa": self._d.class_name,
            "wydarzenia": self._d.calendar,
            "najblizsze": self._upcoming(30)[:15],
            "jutro": [ev for ev in self._d.calendar if ev["start"] <= jutro <= ev["koniec"]],
            "zaczyna_sie_jutro": [ev for ev in self._d.calendar if ev["start"] == jutro],
        }


class _ListSensor(_Base):
    _src = ""
    _list_attr = ""

    def _items(self) -> list[dict[str, Any]]:
        return getattr(self._d, self._src) or []

    @property
    def native_value(self) -> int:
        return len(self._items())

    def _attrs(self) -> dict[str, Any]:
        return {"last_update": self._d.last_update, self._list_attr: self._items()}


class HomeworkSensor(_ListSensor):
    _src, _list_attr = "homework", "zadania"

    def __init__(self, c, e):
        super().__init__(c, e, "zadania", "Zadania domowe", "mdi:notebook-edit-outline")
        self._attr_native_unit_of_measurement = "zadań"


class SubstitutionsSensor(_ListSensor):
    _src, _list_attr = "substitutions", "zastepstwa"

    def __init__(self, c, e):
        super().__init__(c, e, "zastepstwa", "Zastępstwa", "mdi:account-switch-outline")
        self._attr_native_unit_of_measurement = "zastępstw"


class AnnouncementsSensor(_ListSensor):
    _src, _list_attr = "announcements", "komunikaty"

    def __init__(self, c, e):
        super().__init__(c, e, "komunikaty", "Komunikaty", "mdi:bullhorn-outline")
        self._attr_native_unit_of_measurement = "komunikatów"


class PaymentsSensor(_ListSensor):
    _src, _list_attr = "payments", "platnosci"

    def __init__(self, c, e):
        super().__init__(c, e, "platnosci", "Płatności", "mdi:cash-multiple")
        self._attr_native_unit_of_measurement = "płatności"


class StatsSensor(_Base):
    """Frekwencja w % (cały rok) + rozbicie na przedmioty."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, c, e):
        super().__init__(c, e, "statystyka", "Frekwencja procentowa", "mdi:percent-circle-outline")
        self._attr_native_unit_of_measurement = "%"

    @property
    def native_value(self) -> float | None:
        return (self._d.stats.get("podsumowanie") or {}).get("procent")

    def _attrs(self) -> dict[str, Any]:
        st = self._d.stats or {}
        return {"last_update": self._d.last_update, "podsumowanie": st.get("podsumowanie") or {},
                "przedmioty": st.get("przedmioty") or [],
                "ponizej_50": [p["przedmiot"] for p in st.get("przedmioty") or [] if p["procent"] is not None and p["procent"] < 50]}


class BehaviorSensor(_Base):
    def __init__(self, c, e):
        super().__init__(c, e, "zachowanie", "Zachowanie", "mdi:emoticon-outline")

    @property
    def native_value(self) -> str:
        return (self._d.behavior or {}).get("ocena") or "brak"

    def _attrs(self) -> dict[str, Any]:
        return {"last_update": self._d.last_update, "wiersze": (self._d.behavior or {}).get("wiersze", [])}


class ExcuseSensor(_Base):
    """Przygotowane (jeszcze niewysłane) usprawiedliwienie."""

    def __init__(self, c, e):
        super().__init__(c, e, "usprawiedliwienie", "Usprawiedliwienie", "mdi:email-edit-outline")

    @property
    def native_value(self) -> str:
        return "do_wyslania" if self.coordinator.draft else "brak"

    def _attrs(self) -> dict[str, Any]:
        d = self.coordinator.draft or {}
        hist = self.coordinator.saved.get("historia", [])
        return {
            "last_update": self._d.last_update,
            "temat": d.get("temat", ""),
            "tresc": d.get("tresc", ""),
            "pozycji": d.get("pozycji", 0),
            "nieobecnosci": d.get("nieobecnosci", 0),
            "spoznienia_do_usprawiedliwienia": d.get("spoznienia", 0),
            "dni": d.get("dni", []),
            "zakres": d.get("zakres", ""),
            "przygotowano": d.get("przygotowano", ""),
            "podpisy": self.coordinator.signatures(),
            "ostatnio_wyslane": hist[-1] if hist else None,
            "historia": hist[-10:],
        }
