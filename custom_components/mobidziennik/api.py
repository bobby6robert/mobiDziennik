"""Klient mobiDziennika: logowanie jak przeglądarka + odczyt stron WWW.

Budowa stron (adresy, klasy CSS, układ planu) opracowana na podstawie
otwartego kodu aplikacji Szkolny.eu. Logowanie NIE korzysta z kluczy
partnerskich – wypełnia zwykły formularz logowania ze strony szkoły.
"""
from __future__ import annotations

import html as htmllib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from .const import USER_AGENT

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
    lucky_number: int | None = None
    last_update: str = ""
    errors: list[str] = field(default_factory=list)
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

    # ── HTTP ───────────────────────────────────────────────
    async def _get(self, path: str) -> str:
        url = path if path.startswith("http") else self.base + path
        try:
            async with self._s.get(url, headers={"User-Agent": USER_AGENT}, timeout=aiohttp.ClientTimeout(total=30)) as r:
                text = await r.text()
                if r.status >= 400:
                    raise MobiConnectionError(f"GET {path}: HTTP {r.status}")
                return text
        except (aiohttp.ClientError, TimeoutError) as err:
            raise MobiConnectionError(f"GET {path}: {err}") from err

    @staticmethod
    def _is_login_page(text: str) -> bool:
        low = text.lower()
        return ('type="password"' in low or "type='password'" in low) and "wyloguj" not in low

    # ── LOGOWANIE ─────────────────────────────────────────
    async def login(self) -> None:
        page = await self._get("/dziennik/")
        low = page.lower()
        # strona błędnej subdomeny: "Uuuups... nieprawidłowy adres" (uwaga: okno resetu hasła
        # zawiera "nieprawidłowy adres e-mail", więc sprawdzamy dokładny komunikat)
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
        _LOGGER.debug("mobiDziennik: logowanie %s %s pola=%s", method, action, list(data))
        try:
            if method == "get":
                async with self._s.get(action, params=data, headers={"User-Agent": USER_AGENT}) as r:
                    await r.text()
            else:
                async with self._s.post(action, data=data, headers={"User-Agent": USER_AGENT,
                                                                   "Referer": self.base + "/dziennik/"}) as r:
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
        if "Problemy z wydajnością" in text:
            raise MobiConnectionError("Serwer mobiDziennika zgłasza problemy z wydajnością")
        return text

    # ── SYNCHRONIZACJA ───────────────────────────────────
    async def fetch_all(self, keep_raw: bool = False) -> MobiData:
        out = MobiData(last_update=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        if today.weekday() >= 5:
            monday += timedelta(days=7)
        semester = 1 if today.month >= 9 or today.month == 1 else 2

        pages: dict[str, str] = {}
        for key, path in (
            ("plan_biezacy", f"/dziennik/planlekcji?typ=podstawowy&tydzien={monday.isoformat()}"),
            ("plan_nastepny", f"/dziennik/planlekcji?typ=podstawowy&tydzien={(monday + timedelta(days=7)).isoformat()}"),
            ("oceny", f"/dziennik/oceny?semestr={semester}"),
            ("wiadomosci", "/dziennik/wiadomosci"),
        ):
            try:
                pages[key] = await self._page(path)
            except MobiAuthError:
                raise
            except MobiConnectionError as err:
                out.errors.append(f"{key}: {err}")

        for key in ("plan_biezacy", "plan_nastepny"):
            if key in pages:
                start = monday if key == "plan_biezacy" else monday + timedelta(days=7)
                try:
                    out.lessons += parse_timetable(pages[key], start)
                except Exception as err:  # noqa: BLE001
                    out.errors.append(f"{key}: parser {err!r}")
        if "oceny" in pages:
            try:
                out.grades = parse_grades(pages["oceny"])
            except Exception as err:  # noqa: BLE001
                out.errors.append(f"oceny: parser {err!r}")
        if "wiadomosci" in pages:
            try:
                out.messages = parse_messages(pages["wiadomosci"], today)
            except Exception as err:  # noqa: BLE001
                out.errors.append(f"wiadomosci: parser {err!r}")
        for txt in pages.values():
            n = parse_lucky_number(txt)
            if n is not None:
                out.lucky_number = n
                break
        if keep_raw:
            out.raw_pages = pages
        return out

    def message_url(self, msg_id: str) -> str:
        return f"{self.base}/dziennik/wiadodebrana/?id={msg_id}"

    async def fetch_message(self, msg_id: str) -> dict[str, Any]:
        """Treść jednej odebranej wiadomości.

        UWAGA: otwarcie nieprzeczytanej wiadomości oznacza ją w mobiDzienniku jako przeczytaną
        (nauczyciel widzi czas odczytu) – dlatego automatycznie pobieramy tylko przeczytane.
        """
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
    """Strona /dziennik/wiadodebrana/?id=… -> {'tresc': zwykły tekst, 'zalaczniki': [nazwy]}."""
    soup = BeautifulSoup(text, "html.parser")
    content = soup.select_one("#content") or soup
    body = content.select_one(".wiadomosc_tresc")
    attachments = [_text(m.group(2)) for m in RE_ATTACHMENT.finditer(text)]
    if body is None:
        return {"tresc": "", "zalaczniki": attachments}
    # blok z nadawcą / czasem przeczytania (nie jest częścią treści)
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
                continue  # krótka nazwa przedmiotu
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
