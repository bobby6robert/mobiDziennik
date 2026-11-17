"""Treść usprawiedliwienia nieobecności i spóźnień (wiadomość do wychowawcy)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def item_key(i: dict[str, Any]) -> str:
    return f"{i['data']}|{i.get('nr') or i.get('godzina')}|{i['symbol']}"


def school_year_start(today: date) -> date:
    return date(today.year if today.month >= 9 else today.year - 1, 9, 1)


def excuse_weeks(zakres: str, today: date) -> list[date]:
    """Poniedziałki tygodni do sprawdzenia.

    tydzien   – ostatni zakończony tydzień (w sobotę/niedzielę: bieżący, w tygodniu: poprzedni),
    wszystkie – od początku roku szkolnego do bieżącego tygodnia.
    """
    this_monday = today - timedelta(days=today.weekday())
    if zakres == "wszystkie":
        start = school_year_start(today)
        start -= timedelta(days=start.weekday())
        out = []
        d = start
        while d <= this_monday:
            out.append(d)
            d += timedelta(days=7)
        return out
    return [this_monday if today.weekday() >= 5 else this_monday - timedelta(days=7)]


def _lesson(i: dict[str, Any]) -> str:
    return str(i["nr"]) if i.get("nr") else (i.get("godzina") or "?")


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " i " + parts[-1]


def build_excuse(items: list[dict[str, Any]], gender: str, student_full: str, student_name: str) -> dict[str, Any]:
    """items: wpisy ze „Szczegółowej frekwencji” z symbolem | (nieobecność) lub s (spóźnienie)."""
    days: dict[str, dict[str, list[dict]]] = {}
    for i in sorted(items, key=lambda x: (x["data"], x.get("nr") or 0)):
        kind = "spoznienie" if i["symbol"] == "s" else "nieobecnosc"
        days.setdefault(i["data"], {"nieobecnosc": [], "spoznienie": []})[kind].append(i)
    has_abs = any(d["nieobecnosc"] for d in days.values())
    has_late = any(d["spoznienie"] for d in days.values())
    what = "nieobecności i spóźnień" if has_abs and has_late else ("spóźnień" if has_late else "nieobecności")
    child = "mojej córki" if str(gender).lower().startswith(("c", "d", "f")) else "mojego syna"
    lines = []
    for day, kinds in days.items():
        d = date.fromisoformat(day)
        weekday = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"][d.weekday()]
        parts = []
        if kinds["nieobecnosc"]:
            nrs = [_lesson(x) for x in kinds["nieobecnosc"]]
            parts.append(f"nieobecność na {_join(nrs)} godzinie lekcyjnej" if len(nrs) == 1
                         else f"nieobecność na {_join(nrs)} godzinach lekcyjnych")
        if kinds["spoznienie"]:
            nrs = [_lesson(x) for x in kinds["spoznienie"]]
            parts.append(f"spóźnienie na {_join(nrs)} godzinę lekcyjną" if len(nrs) == 1
                         else f"spóźnienia na {_join(nrs)} godziny lekcyjne")
        lines.append(f"– {d.strftime('%d.%m.%Y')} ({weekday}): " + ", ".join(parts))
    intro = f"Proszę o usprawiedliwienie {what} {child} w dniach:" if len(days) > 1 \
        else f"Proszę o usprawiedliwienie {what} {child} w dniu:"
    text = "Dzień dobry,\n\n" + intro + "\n" + "\n".join(lines)
    name = " ".join(reversed(student_full.split(" ", 1))) if student_full else student_name
    return {
        "temat": f"Usprawiedliwienie – {name}".strip(" –"),
        "tresc": text,
        "pozycji": len(items),
        "nieobecnosci": sum(len(d["nieobecnosc"]) for d in days.values()),
        "spoznienia": sum(len(d["spoznienie"]) for d in days.values()),
        "dni": [{"data": k, "nieobecnosci": [_lesson(x) for x in v["nieobecnosc"]],
                 "spoznienia": [_lesson(x) for x in v["spoznienie"]]} for k, v in days.items()],
    }
