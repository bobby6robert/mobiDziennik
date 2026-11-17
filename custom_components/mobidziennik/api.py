"""Klient mobiDziennika: logowanie jak przeglądarka + odczyt stron WWW.

Budowa stron (adresy, klasy CSS, układ planu) opracowana na podstawie
otwartego kodu aplikacji Szkolny.eu. Logowanie NIE korzysta z kluczy
partnerskich – wypełnia zwykły formularz logowania ze strony szkoły.
"""
from __future__ import annotations

import asyncio
import html as htmllib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from .const import DEFAULT_MODULES, USER_AGENT

_LOGGER = logging.getLogger(__name__)

MONTHS_FULL = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6,
    "lipca": 7, "sierpnia": 8, "września": 9, "października": 10, "listopada": 11, "grudnia": 12,
}
MONTHS_SHORT = {
    "sty": 1, "lut": 2, "mar": 3, "kwi": 4, "maj": 5, "cze": 6,
    "lip": 7, "sie": 8, "wrz": 9, "paź": 10, "lis": 11, "gru": 12,
}

RE_LUCKY = re.compile(r'class="szczesliwy_numerek".*?>0?([0-9]+)/?0?([0-9]+)?</a>', re.S)
RE_SUBJECT = re.compile(r"<div.*?>\n*\s*(.+?)\s*\n*(?:<.*?)??</div>", re.S)
RE_CATEGORY = re.compile(r">(?:&nbsp;|\xa0)(.+?):</span>", re.S)
RE_GRADE_DETAILS = re.compile(
    r"<strong.*?>(.+?)</strong>.*?<sup>.+?</sup>.*?(?:<small>\((.+?)\)</small>.*?)?<span>.*?"
    r"Wartość oceny:.*?<strong>([0-9.]+)</strong>.*?Wpisał\(a\):.*?<strong>(.+?)</strong>.*?"
    r"(?:Komentarz:.*?<strong>(.+?)</strong>)?</span>",
    re.S,
)
RE_GRADE_ADDED = re.compile(
    r"Wpisano:.*?<strong>.+?,\s([0-9]+)\s(.+?)\s([0-9]{4}),\sgodzina\s([0-9:]+)</strong>", re.S
)
RE_TT_TOP = re.compile(r'<div class="plansc_top">.+?</div></div>', re.S)
RE_TT_LEFT = re.compile(r'<div class="plansc_godz">.+?</div></div>', re.S)
RE_TT_CELL = re.compile(
    r'<div class="plansc_cnt_w" style="(.+?)">.*?style="(.+?)"[^>]*?title="(.+?)"[^>]*>\s*(.*?)\s*</div>', re.S
)
RE_ACCOUNT = re.compile(r"zalogowany jako\s*<strong>(.+?)</strong>", re.S)
RE_TAGS = re.compile(r"<[^>]+>")
RE_ATTACHMENT = re.compile(
    r'href="(https://[^"]+?\.mobidziennik\.pl/[^"]+?&(?:amp;)?zalacznik(?:_rozwiazania)?=[0-9]+)"[^>]*>(.+?)</a>', re.S
)
RE_META_DIV = re.compile(r"czas przeczytania|nadawca:|odbiorc", re.I)
MAX_BODY = 3000


class MobiAuthError(Exception):
    """Błędny login lub hasło."""


class MobiConnectionError(Exception):
    """Problem z połączeniem lub nieoczekiwana odpowiedź."""


@dataclass
class MobiData:
    """Wynik jednej synchronizacji."""

    lessons: list[dict[str, Any]] = field(default_factory=list)
    grades: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    exams: list[dict[str, Any]] = field(default_factory=list)
    attendance: list[dict[str, Any]] = field(default_factory=list)
    remarks: list[dict[str, Any]] = field(default_factory=list)
    calendar: list[dict[str, Any]] = field(default_factory=list)
    homework: list[dict[str, Any]] = field(default_factory=list)
    substitutions: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    behavior: dict[str, Any] = field(default_factory=dict)
    announcements: list[dict[str, Any]] = field(default_factory=list)
    payments: list[dict[str, Any]] = field(default_factory=list)
    class_name: str = ""
    student_full: str = ""
    lucky_number: int | None = None
    last_update: str = ""
    errors: list[str] = field(default_factory=list)
    failed: set[str] = field(default_factory=set)
    raw_pages: dict[str, str] = field(default_factory=dict)


def _css(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in style.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _pct(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v.strip().rstrip("%"))
    except ValueError:
        return None


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(RE_TAGS.sub(" ", s))).strip()


def _date_full(s: str) -> date | None:
    """'12 października 2026' -> date."""
    parts = s.strip().split()
    try:
        return date(int(parts[2]), MONTHS_FULL[parts[1].lower()], int(parts[0]))
    except (IndexError, KeyError, ValueError):
        return None


class MobiDziennikClient:
    """Sesja z jednym kontem mobiDziennika."""

    def __init__(self, session: aiohttp.ClientSession, server: str, login: str, password: str) -> None:
        self._s = session
        self.server = server.strip().lower().replace("https://", "").split(".")[0]
        self.base = f"https://{self.server}.mobidziennik.pl"
        self._login = login
        self._password = password
        self._logged_in = False
        self.account_name = ""

    # ── HTTP ───────────────────────────────────────────────
    async def _get(self, path: str) -> str:
        url = path if path.startswith("http") else self.base + path
        # chwilowe błędy sieci/DNS: do 3 prób (po 3 s i 10 s)
        for attempt, pause in enumerate((3, 10, None)):
            try:
                async with self._s.get(url, headers={"User-Agent": USER_AGENT}, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    text = await r.text()
                    if r.status >= 500 and pause is not None:
                        raise aiohttp.ClientResponseError(r.request_info, (), status=r.status)
                    if r.status >= 400:
                        raise MobiConnectionError(f"GET {path}: HTTP {r.status}")
                    return text
            except (aiohttp.ClientError, TimeoutError) as err:
                if pause is None:
                    raise MobiConnectionError(f"GET {path}: {err}") from err
                _LOGGER.debug("mobiDziennik: GET %s nieudane (%s), ponowna próba %d", path, err, attempt + 1)
                await asyncio.sleep(pause)
        raise MobiConnectionError(f"GET {path}: brak odpowiedzi")

    async def _post(self, path: str, data: Any) -> str:
        if not self._logged_in:
            await self.login()
        url = self.base + path
        try:
            async with self._s.post(url, data=data, headers={"User-Agent": USER_AGENT, "Referer": url},
                                    timeout=aiohttp.ClientTimeout(total=30)) as r:
                text = await r.text()
                if r.status >= 400:
                    raise MobiConnectionError(f"POST {path}: HTTP {r.status}")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise MobiConnectionError(f"POST {path}: {err}") from err
        if text.strip() == "Nie jestes zalogowany" or self._is_login_page(text):
            self._logged_in = False
            raise MobiConnectionError(f"POST {path}: sesja wygasła")
        return text

    @staticmethod
    def _is_login_page(text: str) -> bool:
        low = text.lower()
        return ('type="password"' in low or "type='password'" in low) and "wyloguj" not in low

    # ── LOGOWANIE ─────────────────────────────────────────
    async def login(self) -> None:
        page = await self._get("/dziennik/")
        low = page.lower()
        if "uuuups" in low and "nieprawidłowy adres" in low and 'type="password"' not in low:
            raise MobiConnectionError("Nieprawidłowy adres szkoły (strona 'Uuuups')")
        if not self._is_login_page(page):
            self._logged_in = True
            return
        soup = BeautifulSoup(page, "html.parser")
        form = None
        for f in soup.find_all("form"):
            if f.find("input", attrs={"type": "password"}):
                form = f
                break
        if form is None:
            _LOGGER.warning("mobiDziennik: brak formularza logowania. Początek strony: %s", page[:1500])
            raise MobiConnectionError("Nie znaleziono formularza logowania")

        data: dict[str, str] = {}
        user_field = None
        pass_field = None
        for inp in form.find_all(["input", "button"]):
            name = inp.get("name")
            if not name:
                continue
            typ = (inp.get("type") or "text").lower()
            if typ == "password" and pass_field is None:
                pass_field = name
            elif typ in ("text", "email") and user_field is None:
                user_field = name
            elif typ in ("checkbox", "radio"):
                if inp.has_attr("checked"):
                    data[name] = inp.get("value", "on")
            elif typ not in ("submit", "button", "image", "reset"):
                data[name] = inp.get("value", "")
            elif typ == "submit" and inp.get("value"):
                data.setdefault(name, inp.get("value"))
        if not user_field or not pass_field:
            raise MobiConnectionError(f"Formularz logowania bez pól login/hasło ({user_field}, {pass_field})")
        data[user_field] = self._login
        data[pass_field] = self._password
        action = urljoin(self.base + "/dziennik/", form.get("action") or "/dziennik/")
        method = (form.get("method") or "post").lower()
        try:
            if method == "get":
                async with self._s.get(action, params=data, headers={"User-Agent": USER_AGENT}) as r:
                    await r.text()
            else:
                async with self._s.post(action, data=data, headers={"User-Agent": USER_AGENT, "Referer": self.base + "/dziennik/"}) as r:
                    await r.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise MobiConnectionError(f"Logowanie: {err}") from err

        check = await self._get("/dziennik/")
        if self._is_login_page(check):
            raise MobiAuthError("Nieprawidłowy login lub hasło")
        self._logged_in = True

    async def _page(self, path: str) -> str:
        if not self._logged_in:
            await self.login()
        text = await self._get(path)
        if text.strip() == "Nie jestes zalogowany" or self._is_login_page(text):
            self._logged_in = False
            await self.login()
            text = await self._get(path)
        m = RE_ACCOUNT.search(text)
        if m:
            self.account_name = _text(m.group(1))
        if "Problemy z wydajnością" in text:
            raise MobiConnectionError("Serwer mobiDziennika zgłasza problemy z wydajnością")
        return text

    # ── SYNCHRONIZACJA ───────────────────────────────────
    async def fetch_all(self, keep_raw: bool = False, modules: list[str] | None = None) -> MobiData:
        mods = set(modules if modules is not None else DEFAULT_MODULES)
        out = MobiData(last_update=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        if today.weekday() >= 5:
            monday += timedelta(days=7)
        semester = 1 if today.month >= 9 or today.month == 1 else 2

        wanted = (
            ("plan", "plan_biezacy", f"/dziennik/planlekcji?typ=podstawowy&tydzien={monday.isoformat()}"),
            ("plan", "plan_nastepny", f"/dziennik/planlekcji?typ=podstawowy&tydzien={(monday + timedelta(days=7)).isoformat()}"),
            ("oceny", "oceny", f"/dziennik/oceny?semestr={semester}"),
            ("wiadomosci", "wiadomosci", "/dziennik/wiadomosci"),
            ("sprawdziany", "sprawdziany", "/dziennik/sprawdziany"),
            ("frekwencja", "frekwencja", "/dziennik/obecnosci"),
            ("uwagi", "uwagi", "/dziennik/uwagi"),
            ("kalendarz", "kalendarz", "/dziennik/kalendarzklasowy"),
            ("zadania", "zadania", "/dziennik/zadaniadomowe"),
            ("zastepstwa", "zastepstwa", "/dziennik/zastepstwa"),
            ("statystyka", "statystyka", "/dziennik/statystykafrekwencji"),
            ("zachowanie", "zachowanie", "/dziennik/zachowanie"),
            ("komunikaty", "komunikaty", "/dziennik/komunikaty"),
            ("platnosci", "platnosci", "/dziennik/zbiorki"),
        )
        pages: dict[str, str] = {}
        for mod, key, path in wanted:
            if mod not in mods:
                continue
            try:
                pages[key] = await self._page(path)
            except MobiAuthError:
                raise
            except MobiConnectionError as err:
                out.errors.append(f"{key}: {err}")
                out.failed.add(key)

        for key in ("plan_biezacy", "plan_nastepny"):
            if key in pages:
                start = monday if key == "plan_biezacy" else monday + timedelta(days=7)
                try:
                    out.lessons += parse_timetable(pages[key], start)
                except Exception as err:
                    out.errors.append(f"{key}: parser {err!r}")
                    out.failed.add(key)
        out.lessons = dedupe_lessons(out.lessons)

        def run(key: str, fn, attr: str) -> None:
            if key not in pages:
                return
            try:
                setattr(out, attr, fn(pages[key]))
            except Exception as err:  # noqa: BLE001
                out.errors.append(f"{key}: parser {err!r}")
                out.failed.add(key)

        run("oceny", parse_grades, "grades")
        run("wiadomosci", lambda t: parse_messages(t, today), "messages")
        run("sprawdziany", parse_exams, "exams")
        run("frekwencja", parse_attendance, "attendance")
        run("uwagi", parse_remarks, "remarks")
        run("kalendarz", parse_calendar, "calendar")
        run("zadania", lambda t: parse_list_page(t, "zadania"), "homework")
        run("zastepstwa", lambda t: parse_list_page(t, "zastepstwa"), "substitutions")
        run("statystyka", parse_stats, "stats")
        run("zachowanie", parse_behavior, "behavior")
        run("komunikaty", lambda t: parse_list_page(t, "komunikaty"), "announcements")
        run("platnosci", parse_payments, "payments")
        if "kalendarz" in pages:
            out.class_name, out.student_full = parse_class_info(pages["kalendarz"])

        for txt in pages.values():
            n = parse_lucky_number(txt)
            if n is not None:
                out.lucky_number = n
                break
        if keep_raw:
            out.raw_pages = pages
        return out

    # ── FREKWENCJA TYGODNIOWA / USPRAWIEDLIWIENIA ──────────
    async def fetch_week_attendance(self, monday: date) -> list[dict[str, Any]]:
        """Szczegółowa frekwencja z jednego tygodnia (numery lekcji + symbole . | + s z)."""
        page = await self._page("/dziennik/frekwencja")
        uczen = parse_student_id(page)
        if uczen is None:
            raise MobiConnectionError("frekwencja: nie znaleziono ucznia")
        text = await self._post("/dziennik/frekwencja", {"uczen": uczen, "data_poniedzialek": monday.isoformat()})
        return parse_attendance_week(text)

    async def class_info(self) -> tuple[str, str]:
        return parse_class_info(await self._page("/dziennik/kalendarzklasowy"))

    async def tutors(self) -> list[dict[str, str]]:
        """Wychowawcy, do których rodzic może pisać: [{id, nazwisko, klasa}]."""
        await self._page("/dziennik/dodajwiadomosc")
        text = await self._post("/dziennik/odbiorcyWiadomosci", {"typ": "8", "odpowiedz": "0", "wiadomosci": "true"})
        return parse_recipients(text)

    async def send_message(self, recipient_id: str, subject: str, body_html: str) -> None:
        """Wysyła wiadomość (grupa „Wychowawcy”) i sprawdza, czy jest w „Wysłanych”."""
        await self._page("/dziennik/dodajwiadomosc")
        await self._post("/dziennik/dodajwiadomosc", [
            ("nazwa", subject), ("typodbiorcow", "8"), ("odbiorcy[]", str(recipient_id)), ("tresc", body_html),
        ])
        sent = await self._page("/dziennik/wiadomosciwyslane")
        if htmllib.escape(subject, quote=False) not in sent and subject not in sent:
            raise MobiConnectionError("Wiadomość nie pojawiła się w „Wysłanych” – sprawdź mobiDziennik")

    def message_url(self, msg_id: str) -> str:
        return f"{self.base}/dziennik/wiadodebrana/?id={msg_id}"

    async def fetch_message(self, msg_id: str) -> dict[str, Any]:
        text = await self._page(f"/dziennik/wiadodebrana/?id={msg_id}")
        return parse_message_body(text)


# ── PARSERY ──────────────────────────────────────────────
def parse_lucky_number(text: str) -> int | None:
    m = RE_LUCKY.search(text)
    return int(m.group(1)) if m else None


def parse_messages(text: str, today: date) -> list[dict[str, Any]]:
    if "Brak wiadomości odebranych" in text:
        return []
    soup = BeautifulSoup(text, "html.parser")
    table = soup.find(class_="spis")
    if table is None:
        return []
    out = []
    year, month = today.year, today.month
    for row in table.find_all(class_="podswietl"):
        tds = row.find_all("td", recursive=False) or row.find_all("td")
        if len(tds) < 5:
            continue
        subject = "".join(t for t in tds[0].find_all(string=True, recursive=False)).strip() or tds[0].get_text(" ", strip=True)
        sender = "".join(t for t in tds[3].find_all(string=True, recursive=False)).strip() or tds[3].get_text(" ", strip=True)
        raw_date = tds[4].get_text(" ", strip=True)
        parts = [p for p in re.split(r"[,\s]+", raw_date) if p]
        dt_str = raw_date
        try:
            d, mon, hm = int(parts[1]), MONTHS_SHORT[parts[2].lower()], parts[3]
            if mon > month:
                year -= 1
            month = mon
            dt_str = f"{year:04d}-{mon:02d}-{d:02d} {hm[:5]}"
        except (IndexError, KeyError, ValueError):
            pass
        read = False
        if len(tds) > 5:
            span = tds[5].find("span")
            read = bool(span and "wiadomosc_przeczytana" in (span.get("class") or []))
        out.append({
            "id": row.get("rel"),
            "data": dt_str,
            "nadawca": re.sub(r"\s+", " ", sender),
            "temat": re.sub(r"\s+", " ", subject),
            "tresc": "",
            "przeczytana": read,
            "zalacznik": bool(tds[0].find("a")),
        })
    return out


def parse_message_body(text: str) -> dict[str, Any]:
    soup = BeautifulSoup(text, "html.parser")
    content = soup.select_one("#content") or soup
    body = content.select_one(".wiadomosc_tresc")
    attachments = [_text(m.group(2)) for m in RE_ATTACHMENT.finditer(text)]
    if body is None:
        return {"tresc": "", "zalaczniki": attachments}
    for div in body.find_all("div"):
        if div.parent is not None and RE_META_DIV.search(div.get_text(" ", strip=True)[:200]):
            div.decompose()
    for a in body.find_all("a"):
        href = a.get("href") or ""
        label = a.get_text(" ", strip=True)
        a.replace_with(f"{label} ({href})" if href.startswith("http") and href not in label else label)
    for br in body.find_all("br"):
        br.replace_with("\n")
    for blk in body.find_all(["p", "li", "div", "tr", "h1", "h2", "h3", "h4"]):
        blk.append("\n")
    raw = body.get_text()
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in raw.splitlines()]
    txt = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if len(txt) > MAX_BODY:
        txt = txt[:MAX_BODY].rstrip() + "…"
    return {"tresc": txt, "zalaczniki": attachments}


def parse_grades(text: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(text, "html.parser")
    out: list[dict[str, Any]] = []
    for table in soup.select("table.spis"):
        subject, category = "", ""
        for el in table.find_all(["a", "span", "div"]):
            outer = str(el)
            if el.name == "div":
                m = RE_SUBJECT.search(outer)
                if m and "<" not in m.group(1):
                    subject = _text(m.group(1))
            elif el.name == "span":
                m = RE_CATEGORY.search(outer)
                if m:
                    category = _text(m.group(1))
            elif el.name == "a" and el.get("rel"):
                inner = el.decode_contents()
                det = RE_GRADE_DETAILS.search(inner)
                if not det:
                    continue
                added = None
                m = RE_GRADE_ADDED.search(inner)
                if m:
                    try:
                        mon = MONTHS_FULL.get(m.group(2).lower(), 1)
                        added = f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(1)):02d} {m.group(4)[:5]}"
                    except ValueError:
                        added = None
                out.append({
                    "id": el.get("rel") if isinstance(el.get("rel"), str) else " ".join(el.get("rel")),
                    "przedmiot": subject,
                    "kategoria": category,
                    "ocena": _text(det.group(1)),
                    "opis": _text(det.group(2) or ""),
                    "wartosc": float(det.group(3)) if det.group(3) else None,
                    "nauczyciel": _text(det.group(4)),
                    "komentarz": _text(det.group(5) or ""),
                    "data": added,
                    "do_sredniej": not re.search(r"Liczona do średniej:.*?<strong>nie", inner, re.S),
                })
    return out


EXAM_KINDS = {
    "kar": "Kartkówka",
    "spr": "Sprawdzian",
    "pra": "Praca klasowa",
    "tes": "Test",
    "odp": "Odpowiedź ustna",
    "zad": "Zadanie domowe",
    "pro": "Projekt",
    "dyk": "Dyktando",
}
RE_EXAM_DATE = re.compile(r"(\d{1,2})\s+([^\s\d]+)\s+(\d{4})(?:.*?(\d{1,2}):(\d{2}))?")


def _exam_kind(cell: Any, text: str) -> str:
    """Pełna nazwa rodzaju – mobiDziennik skraca ją do 'kar...', 'spr...'."""
    for el in [cell, *cell.find_all(True)]:
        title = (el.get("title") or "").strip()
        if title and not title.endswith("..."):
            title = _text(title)
            return title[:1].upper() + title[1:]
    clean = text.replace("...", "").replace("…", "").strip()
    return EXAM_KINDS.get(clean[:3].lower(), clean or text)


def _exam_datetime(s: str) -> datetime | None:
    """'26 paź 2026 pon. - 09:45' -> datetime (bez strefy)."""
    m = RE_EXAM_DATE.search(s)
    if not m:
        return None
    month_word = m.group(2).lower().rstrip(".")
    month = MONTHS_SHORT.get(month_word[:3]) or MONTHS_FULL.get(month_word)
    if not month:
        return None
    try:
        return datetime(int(m.group(3)), month, int(m.group(1)),
                        int(m.group(4) or 0), int(m.group(5) or 0))
    except ValueError:
        return None


def parse_exams(text: str) -> list[dict[str, Any]]:
    """Parser dla zapowiedzianych sprawdzianów ze strony /dziennik/sprawdziany."""
    soup = BeautifulSoup(text, "html.parser")
    out: list[dict[str, Any]] = []

    for row in soup.select("table.spis tr, tr.podswietl"):
        tds = row.find_all("td")
        cols = [c.get_text(" ", strip=True) for c in tds]
        if len(cols) < 4:
            continue
        if "Data" in cols[0] or "Przedmiot" in cols[0]:
            continue

        dt = _text(cols[0])
        col1 = _text(cols[1])
        if re.match(r"^\d+\s*[a-z]{0,3}\d{0,2}$", col1, re.I):  # klasa/grupa, np. „2a”, „1 G2”
            item = {
                "data": dt,
                "przedmiot": _text(cols[2]),
                "rodzaj": _exam_kind(tds[3], _text(cols[3])),
                "tematyka": _text(cols[4]) if len(cols) > 4 else "",
            }
        else:
            item = {
                "data": dt,
                "przedmiot": col1,
                "rodzaj": _exam_kind(tds[2], _text(cols[2])),
                "tematyka": _text(cols[3]),
                "nauczyciel": _text(cols[4]) if len(cols) > 4 else "",
            }
        when = _exam_datetime(dt)
        item["data_iso"] = when.isoformat() if when else None
        out.append(item)
    out.sort(key=lambda e: e.get("data_iso") or "9999")
    return out


def parse_attendance(text: str) -> list[dict[str, Any]]:
    """Parser dla frekwencji (nieobecności / spóźnienia) ze strony /dziennik/obecnosci."""
    soup = BeautifulSoup(text, "html.parser")
    out: list[dict[str, Any]] = []

    # Iterujemy po głównych wierszach z datami nieobecności
    rows = soup.select("tr.podswietl")
    for row in rows:
        tds = row.find_all("td")
        if not tds:
            continue
        data_str = _text(tds[0].get_text())
        
        # Następny wiersz zawiera szczegóły (zagnieżdżoną tabelę z lekcjami)
        next_row = row.find_next_sibling("tr")
        if not next_row:
            continue
            
        sub_table = next_row.find("table", class_="spis")
        if not sub_table:
            continue
            
        for sub_tr in sub_table.find_all("tr"):
            sub_tds = sub_tr.find_all("td")
            if len(sub_tds) >= 4:
                godzina = _text(sub_tds[0].get_text())
                przedmiot = _text(sub_tds[1].get_text())
                nauczyciel = _text(sub_tds[2].get_text())
                
                # Pobieramy status z atrybutu alt obrazka (np. "Nieobecność nieusprawiedliwiona")
                img = sub_tds[3].find("img")
                status = img.get("alt", "Nieobecność") if img else "Nieobecność"
                
                out.append({
                    "data": data_str,
                    "godzina": godzina,
                    "status": status,
                    "przedmiot": przedmiot,
                    "nauczyciel": nauczyciel,
                })
    return out


def parse_remarks(text: str) -> list[dict[str, Any]]:
    """Parser dla uwag i pochwał ze strony /dziennik/uwagi."""
    soup = BeautifulSoup(text, "html.parser")
    out: list[dict[str, Any]] = []

    for row in soup.select("table.spis tr, tr.podswietl"):
        cols = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        if len(cols) >= 2 and "Data" not in cols[0]:
            out.append({
                "data": _text(cols[0]),
                "nauczyciel": _text(cols[1]),
                "tresc": _text(cols[2]) if len(cols) > 2 else "",
                "punkty": _text(cols[3]) if len(cols) > 3 else "",
            })
    return out


def dedupe_lessons(lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Usuwa powtórzone lekcje (ten sam dzień, godzina, przedmiot, sala, nauczyciel, status)."""
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for l in lessons:
        k = (l.get("d"), l.get("g"), l.get("p"), l.get("s"), l.get("n"), l.get("st"))
        if k in seen:
            continue
        seen.add(k)
        out.append(l)
    return out


def parse_timetable(html: str, week_start: date) -> list[dict[str, Any]]:
    """Plan tygodniowy: bloki pozycjonowane w % (lewo = dzień, góra = godzina)."""
    ranges: list[tuple[float, float, date]] = []
    top = RE_TT_TOP.search(html)
    if top:
        pos = 0.0
        for el in BeautifulSoup(top.group(0), "html.parser").select("div > div"):
            w = _pct(_css(el.get("style", "")).get("width"))
            d = _date_full(el.get("title", ""))
            if w is None:
                continue
            if d is not None:
                ranges.append((pos, pos + w, d))
            pos += w
    hours: dict[int, tuple[str, int | None]] = {}
    left = RE_TT_LEFT.search(html)
    if left:
        for el in BeautifulSoup(left.group(0), "html.parser").select("div > div"):
            t = _pct(_css(el.get("style", "")).get("top"))
            vals = el.get_text(" ", strip=True).split(" ")
            if t is None or not vals or ":" not in vals[0]:
                continue
            num = int(vals[1]) if len(vals) > 1 and vals[1].isdigit() else None
            hours[int(round(t * 100))] = (vals[0].zfill(5), num)

    def nearest_hour(key: int) -> tuple[str, int | None] | None:
        if key in hours:
            return hours[key]
        best = min(hours, key=lambda k: abs(k - key), default=None)
        return hours[best] if best is not None and abs(best - key) <= 30 else None

    out: list[dict[str, Any]] = []
    for m in RE_TT_CELL.finditer(html):
        css = _css(f"{m.group(1)};{m.group(2)}")
        lft, tp, wd, ht = (_pct(css.get(k)) for k in ("left", "top", "width", "height"))
        if None in (lft, tp, wd, ht):
            continue
        mid = lft + wd / 2
        day = next((d for a, b, d in ranges if a <= mid <= b), None)
        start = nearest_hour(int(round(tp * 100)))
        end = nearest_hour(int(round((tp + ht) * 100)))
        title_raw = htmllib.unescape(m.group(3))
        body_raw = m.group(4)

        def lines(s: str) -> list[str]:
            s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
            s = s.replace("<small>", "\n$").replace("</small>", "\n").replace("<b>", "%").replace("</b>", "%")
            s = RE_TAGS.sub("", s)
            return [re.sub(r"\s+", " ", x).strip() for x in s.split("\n") if x.strip()]

        items = lines(title_raw) + lines(body_raw)
        typ, subject, teacher, room = None, None, None, None
        clean: list[str] = []
        for it in items:
            if it.startswith("$"):
                typ = it.strip("$ ").strip()
                continue
            if re.match(r"^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$", it):
                continue
            mroom = re.search(r"\(([^)]*)\)", it)
            if mroom and room is None:
                room = mroom.group(1)
                it = it.replace(f"({room})", "").strip()
            if it.startswith("%") and it.endswith("%"):
                continue
            it = it.replace("%", "").strip()
            if it:
                clean.append(it)
        if clean:
            subject = clean[0]
        for it in clean[1:]:
            if " - " in it:
                teacher = it.split(" - ", 1)[1].strip()
                break
        if teacher is None and len(clean) > 1:
            teacher = clean[1]
        if day is None or start is None or not subject:
            continue
        out.append({
            "d": day.isoformat(),
            "g": f"{start[0]}-{end[0] if end else ''}",
            "nr": start[1],
            "p": subject,
            "s": room or "",
            "n": teacher or "",
            "st": typ or "",
        })
    out.sort(key=lambda x: (x["d"], x["g"]))
    return out


# ── NOWE MODUŁY (1.2.0) ─────────────────────────────────
CAL_COLORS = {
    "#3366cc": ("informacja", "Informacja"),
    "#008928": ("wydarzenie", "Wydarzenie klasy"),
    "#efd459": ("dyzur", "Dyżur"),
    "#527f3f": ("wlasny", "Własny wpis"),
    "#ab0001": ("sprawdzian", "Sprawdzian"),
    "#c54449": ("kartkowka", "Kartkówka"),
    "#b66000": ("wycieczka", "Wycieczka"),
    "#590000": ("szkolne", "Kalendarz szkolny"),
    "#d6d7d9": ("wolne", "Dni wolne"),
}
RE_CAL_EVENTS = re.compile(r"events\s*:\s*\[")
RE_CAL_OBJ = re.compile(r'\{"title".*?\}(?=\s*[,\]])', re.S)
RE_CLASS = re.compile(r"<h1>\s*(.+?)\s*-\s*kalendarz klasowy\s*<small>\(\s*(.+?)\s*\)</small>", re.S)
RE_DATE_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
RE_DATE_PL = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
WEEKDAYS_PL = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]


def _content(text: str):
    soup = BeautifulSoup(text, "html.parser")
    root = soup.find(id="content") or soup.body or soup
    for tag in root.find_all(["script", "style", "form"]):
        tag.decompose()
    return root


def parse_class_info(text: str) -> tuple[str, str]:
    """(klasa, 'Nazwisko Imię') z nagłówka kalendarza klasowego."""
    m = RE_CLASS.search(text)
    if not m:
        return "", ""
    return _text(m.group(2)), _text(m.group(1))


def parse_calendar(text: str) -> list[dict[str, Any]]:
    """Wpisy kalendarza klasowego (osadzone w stronie jako lista zdarzeń FullCalendar)."""
    m = RE_CAL_EVENTS.search(text)
    if not m:
        return []
    import json

    raw: list[dict[str, Any]] = []
    try:
        raw, _ = json.JSONDecoder().raw_decode(text, m.end() - 1)
    except ValueError:
        for o in RE_CAL_OBJ.finditer(text[m.end():]):
            try:
                raw.append(json.loads(o.group(0)))
            except ValueError:
                continue
    _, student = parse_class_info(text)
    out: list[dict[str, Any]] = []
    for e in raw:
        start = str(e.get("start") or "")[:10]
        if not RE_DATE_ISO.fullmatch(start):
            continue
        end = str(e.get("end") or "")[:10] or start
        if not RE_DATE_ISO.fullmatch(end) or end < start:
            end = start
        kind, label = CAL_COLORS.get(str(e.get("color") or "").lower(), ("informacja", "Informacja"))
        src = str(e.get("id") or "").split(";")
        if src and src[0] == "sprawdzian" and kind not in ("sprawdzian", "kartkowka"):
            kind, label = "sprawdzian", "Sprawdzian"
        if src and src[0] == "wycieczka":
            kind, label = "wycieczka", "Wycieczka"
        if src and src[0] == "dzien_wolny":
            kind, label = "wolne", "Dni wolne"
        title = _text(str(e.get("title") or ""))
        comment = _text(str(e.get("comment") or ""))
        if title.endswith("...") and comment:
            title, comment = comment, ""
        if comment == title or (kind == "dyzur" and comment.lower() == "dyżurny"):
            comment = ""
        out.append({
            "id": str(e.get("id") or ""),
            "start": start,
            "koniec": end,
            "tytul": title,
            "opis": comment,
            "rodzaj": kind,
            "rodzaj_nazwa": label,
            "kolor": str(e.get("color") or ""),
            "moj": bool(kind == "dyzur" and student and student.lower() in title.lower()),
        })
    out.sort(key=lambda x: (x["start"], x["tytul"]))
    return out


def parse_list_page(text: str, kind: str) -> list[dict[str, Any]]:
    """Strony-listy (zadania, zastępstwa, komunikaty): „Brak …” = pusto, inaczej wiersze tabel lub bloki."""
    root = _content(text)
    full = root.get_text(" ", strip=True)
    if re.search(r"\bBrak (zadań|zaplanowanych zastępstw|komunikatów)\b", full) and not root.select("table.spis tr td"):
        return []
    out: list[dict[str, Any]] = []
    for tr in root.select("table.spis tr"):
        tds = tr.find_all("td", recursive=False)
        cols = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tds]
        cols = [c for c in cols if c]
        if not cols or tr.find("th"):
            continue
        txt = " · ".join(cols)
        out.append({"tekst": txt, "kolumny": cols, "data": _first_date(txt)})
    if not out:
        for blk in root.find_all(["div", "p", "li"]):
            if blk.find(["div", "p", "li", "table"]):
                continue
            txt = re.sub(r"\s+", " ", blk.get_text(" ", strip=True))
            if len(txt) > 15 and not txt.startswith("Brak"):
                out.append({"tekst": txt, "kolumny": [txt], "data": _first_date(txt)})
    return out[:50]


def _first_date(txt: str) -> str:
    m = RE_DATE_ISO.search(txt)
    if m:
        return m.group(0)
    m = RE_DATE_PL.search(txt)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


def _num(s: str) -> float:
    try:
        return float(s.replace(",", ".")) if s.strip() else 0.0
    except ValueError:
        return 0.0


def parse_stats(text: str) -> dict[str, Any]:
    """Statystyka frekwencji – kolumny „Cały rok” (ob, sp, zw, uspr, nuspr, og, F) dla każdego przedmiotu."""
    soup = BeautifulSoup(text, "html.parser")
    table = soup.select_one("table.frekwencja") or soup.select_one("table.spis")
    out: dict[str, Any] = {"przedmioty": [], "podsumowanie": {}}
    if table is None:
        return out
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        name = _text(tds[0].get_text())
        vals = [t.get_text(strip=True) for t in tds[1:]]
        block = vals[-7:] if len(vals) >= 7 else vals
        ob, sp, zw, us, nu, og, pr = (_num(v) for v in block)
        row = {"przedmiot": name, "obecnosci": int(ob), "spoznienia": int(sp), "zwolnienia": int(zw),
               "usprawiedliwione": int(us), "nieusprawiedliwione": int(nu), "lekcji": int(og),
               "procent": round(pr, 2) if og else None}
        if name.lower().startswith("podsumowanie"):
            out["podsumowanie"] = row
        elif name:
            out["przedmioty"].append(row)
    out["przedmioty"].sort(key=lambda r: (r["procent"] is None, r["procent"] if r["procent"] is not None else 0))
    return out


def parse_behavior(text: str) -> dict[str, Any]:
    root = _content(text)
    rows: list[list[str]] = []
    for tr in root.select("table tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) >= 2:
            rows.append([_text(cells[0].get_text()), _text(cells[1].get_text()).strip("- ").strip()])
    current = ""
    for label in ("Ocena roczna", "Ocena przewidywana roczna", "Ocena śródroczna", "Ocena przewidywana śródroczna"):
        val = next((v for k, v in rows if k == label and v), "")
        if val:
            current = val
            break
    return {"wiersze": rows, "ocena": current}


def parse_payments(text: str) -> list[dict[str, Any]]:
    root = _content(text)
    out: list[dict[str, Any]] = []
    for table in root.select("table.spis"):
        h = table.find("h1")
        if not h or "płatności" not in h.get_text().lower():
            continue
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            cols = [_text(t.get_text()) for t in tds]
            cols = [c for c in cols if c]
            if len(cols) < 2 or tr.find("h1"):
                continue
            txt = " · ".join(cols)
            amount = re.search(r"(\d+[.,]\d{2})\s*zł", txt)
            out.append({"tekst": txt, "kolumny": cols, "kwota": amount.group(1).replace(",", ".") if amount else ""})
    return out


def parse_student_id(text: str) -> str | None:
    m = re.search(r'name="uczen"[^>]*>\s*<option[^>]*value="(\d+)"', text) or re.search(r'name="uczen"\s+value="(\d+)"', text)
    if m:
        return m.group(1)
    soup = BeautifulSoup(text, "html.parser")
    sel = soup.find("select", attrs={"name": "uczen"})
    if sel:
        opt = sel.find("option", selected=True) or sel.find("option")
        if opt and opt.get("value"):
            return opt["value"]
    return None


def parse_attendance_week(text: str) -> list[dict[str, Any]]:
    """Tydzień ze „Szczegółowej frekwencji”: [{data, dzien, nr, godzina, przedmiot, symbol}]."""
    soup = BeautifulSoup(text, "html.parser")
    root = soup.find(id="content") or soup
    table = root.find("table", class_="spis")
    if table is None:
        return []
    trs = table.find_all("tr", recursive=False) or table.find_all("tr")
    if len(trs) < 3:
        return []
    days: list[tuple[str, int]] = []
    for td in trs[0].find_all("td")[1:]:
        m = RE_DATE_ISO.search(td.get_text())
        if m:
            days.append((m.group(0), int(td.get("colspan", 1))))
    slots: list[tuple[int | None, str, str]] = []
    for td in trs[1].find_all("td")[1:]:
        a = td.find("a")
        span = a.find("span") if a else None
        nr_txt = (a.find(string=True, recursive=False) or "") if a else ""
        nr = int(re.sub(r"\D", "", nr_txt)) if re.sub(r"\D", "", nr_txt) else None
        hours = ""
        subj = ""
        if span:
            sp_txt = span.get_text(" ", strip=True)
            mh = re.match(r"(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})", sp_txt)
            hours = mh.group(1) if mh else ""
            subj = " / ".join(_text(s.get_text()) for s in span.find_all("strong"))
        slots.append((nr, hours, subj))
    student_row = next((tr for tr in trs[2:] if tr.get("rel")), None) or trs[2]
    cells = student_row.find_all("td")[1:]
    out: list[dict[str, Any]] = []
    idx = 0
    for day, span_n in days:
        for _ in range(span_n):
            if idx >= len(cells):
                break
            sym = cells[idx].get_text(strip=True)
            nr, hours, subj = slots[idx] if idx < len(slots) else (None, "", "")
            if sym and sym != ".":
                d = date.fromisoformat(day)
                out.append({"data": day, "dzien": WEEKDAYS_PL[d.weekday()], "nr": nr, "godzina": hours,
                            "przedmiot": subj, "symbol": sym})
            idx += 1
    return out


def parse_recipients(text: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(text, "html.parser")
    out: list[dict[str, str]] = []
    for head in soup.select(".wiadomosci_odbiorcy_klasa_naglowek"):
        klasa = re.sub(r"[◀▶\s]+$", "", head.get_text(" ", strip=True)).strip()
        cnt = soup.select_one(f'.wiadomosci_odbiorcy_klasa_cnt[data-klasa="{head.get("data-klasa")}"]')
        for lab in (cnt.find_all("label") if cnt else []):
            inp = lab.find("input", attrs={"name": re.compile("odbiorcy")})
            if inp and inp.get("value"):
                out.append({"id": inp["value"], "nazwisko": _text(lab.get_text()), "klasa": klasa})
    if not out:
        for inp in soup.find_all("input", attrs={"name": re.compile("odbiorcy")}):
            lab = inp.find_parent("label")
            out.append({"id": inp.get("value", ""), "nazwisko": _text(lab.get_text()) if lab else "", "klasa": ""})
    return [r for r in out if r["id"]]


def _norm_class(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def pick_tutor(tutors: list[dict[str, str]], class_name: str) -> dict[str, str] | None:
    if len(tutors) == 1:
        return tutors[0]
    cls = _norm_class(class_name)
    return next((t for t in tutors if cls and _norm_class(t["klasa"]) == cls), None)
