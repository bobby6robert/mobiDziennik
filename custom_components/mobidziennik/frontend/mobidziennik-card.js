/*
 * mobiDziennik – karta dla Home Assistant (ładowana automatycznie przez integrację).
 *
 *   type: custom:mobidziennik-card
 *   device: <id urządzenia „mobiDziennik …”>       (w edytorze wybiera się z listy)
 *   sekcje: [plan, sprawdziany, wiadomosci, oceny, frekwencja]   (opcjonalnie, domyślnie wszystkie)
 *   naglowek: true                                  (imię, numerek, średnia, nieprzeczytane)
 *
 * Dodatkowo: kreator pulpitu – gdy przy dodawaniu integracji zaznaczono „Utwórz pulpit”,
 * przeglądarka administratora sama tworzy pulpit „mobiDziennik” z widokiem dla ucznia.
 */
const MD_VERSION = '1.1.0';
const MD_SECTIONS = [
  ['plan', 'Plan lekcji'],
  ['sprawdziany', 'Sprawdziany i kartkówki'],
  ['wiadomosci', 'Wiadomości'],
  ['oceny', 'Oceny i średnie'],
  ['frekwencja', 'Frekwencja i uwagi'],
];
const MD_DAYS = ['niedziela', 'poniedziałek', 'wtorek', 'środa', 'czwartek', 'piątek', 'sobota'];
const MD_DAYS_SHORT = ['nd', 'pn', 'wt', 'śr', 'cz', 'pt', 'sb'];

const mdEsc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const mdIso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const mdParse = (iso) => { const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); };
const mdDayDiff = (iso) => Math.round((mdParse(iso) - mdParse(mdIso(new Date()))) / 86400000);
const mdDM = (iso) => { const d = mdParse(iso); return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}`; };

/** Encje jednego ucznia: {plan: stateObj, oceny: stateObj, …} */
function mdEntities(hass, deviceId) {
  const out = {};
  if (!hass || !deviceId) return out;
  for (const [eid, ent] of Object.entries(hass.entities || {})) {
    if (ent.device_id !== deviceId) continue;
    const st = hass.states[eid];
    const key = st && st.attributes && st.attributes.mobidziennik;
    if (key) out[key] = st;
  }
  return out;
}

/** Urządzenia mobiDziennika (uczniowie) */
function mdDevices(hass) {
  const ids = new Set();
  for (const [eid, ent] of Object.entries(hass.entities || {})) {
    const st = hass.states[eid];
    if (ent.device_id && st && st.attributes && st.attributes.mobidziennik) ids.add(ent.device_id);
  }
  return [...ids].map((id) => ({ id, name: (hass.devices && hass.devices[id] && (hass.devices[id].name_by_user || hass.devices[id].name)) || 'mobiDziennik' }));
}

const mdStudentName = (hass, deviceId) => {
  const d = hass.devices && hass.devices[deviceId];
  return ((d && (d.name_by_user || d.name)) || 'mobiDziennik').replace(/^mobiDziennik\s*/i, '') || 'Uczeń';
};

class MobiDziennikCard extends HTMLElement {
  static getConfigElement() { return document.createElement('mobidziennik-card-editor'); }

  static getStubConfig(hass) {
    const dev = mdDevices(hass)[0];
    return { device: dev ? dev.id : '', sekcje: MD_SECTIONS.map((s) => s[0]), naglowek: true };
  }

  setConfig(config) {
    this._config = { naglowek: true, ...config };
    if (!Array.isArray(this._config.sekcje) || !this._config.sekcje.length) this._config.sekcje = MD_SECTIONS.map((s) => s[0]);
    this._dayOffset = 0;
    this._allAvg = false;
    this._sig = '';
    if (!this.shadowRoot) {
      this.attachShadow({ mode: 'open' });
      this.shadowRoot.addEventListener('click', (ev) => this._onClick(ev));
    }
    this._render(true);
  }

  set hass(hass) {
    this._hass = hass;
    const ents = mdEntities(hass, this._config && this._config.device);
    const sig = Object.values(ents).map((s) => s.entity_id + s.last_updated).join('|') + '|' + new Date().getMinutes();
    if (sig !== this._sig) { this._sig = sig; this._render(); }
  }

  connectedCallback() { this._timer = setInterval(() => this._render(), 60000); }
  disconnectedCallback() { clearInterval(this._timer); }

  getCardSize() { return 2 + 3 * ((this._config && this._config.sekcje.length) || 1); }
  getGridOptions() { return { columns: 12, min_columns: 6 }; }

  _onClick(ev) {
    const a = ev.target.closest('[data-act]');
    if (!a) return;
    const act = a.dataset.act;
    if (act === 'prev') this._dayOffset -= 1;
    else if (act === 'next') this._dayOffset += 1;
    else if (act === 'today') this._dayOffset = 0;
    else if (act === 'avg') this._allAvg = !this._allAvg;
    else if (act === 'more') {
      const ev2 = new Event('hass-more-info', { bubbles: true, composed: true });
      ev2.detail = { entityId: a.dataset.entity };
      this.dispatchEvent(ev2);
      return;
    }
    this._render();
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const hass = this._hass;
    let body = '';
    if (!hass) body = '<div class="empty">Ładowanie…</div>';
    else if (!this._config.device) body = '<div class="empty">Wybierz ucznia w ustawieniach karty.</div>';
    else {
      const e = mdEntities(hass, this._config.device);
      if (!Object.keys(e).length) body = '<div class="empty">Nie znaleziono czujników tego ucznia. Sprawdź, czy integracja mobiDziennik działa (wersja 1.1.0 lub nowsza).</div>';
      else {
        if (this._config.naglowek) body += this._header(hass, e);
        for (const key of this._config.sekcje) {
          const fn = { plan: this._plan, sprawdziany: this._exams, wiadomosci: this._messages, oceny: this._grades, frekwencja: this._attendance }[key];
          if (fn) body += fn.call(this, e);
        }
      }
    }
    this.shadowRoot.innerHTML = `<style>${MD_CSS}</style><ha-card>${body}</ha-card>`;
  }

  _header(hass, e) {
    const chip = (icon, text, cls = '', ent = '') => `<span class="chip ${cls}" ${ent ? `data-act="more" data-entity="${ent}"` : ''}><ha-icon icon="${icon}"></ha-icon>${text}</span>`;
    const chips = [];
    const n = e.szczesliwy_numerek;
    if (n && !['unknown', 'unavailable', 'None', ''].includes(n.state)) chips.push(chip('mdi:clover', `Numerek ${mdEsc(n.state)}`, 'ok', n.entity_id));
    const av = e.srednia;
    if (av && !isNaN(parseFloat(av.state))) chips.push(chip('mdi:chart-line', `Średnia ${parseFloat(av.state).toFixed(2)}`, '', av.entity_id));
    const msg = e.wiadomosci;
    if (msg && parseInt(msg.state, 10) > 0) chips.push(chip('mdi:email-alert-outline', `${msg.state} nieprzeczyt.`, 'warn', msg.entity_id));
    const nu = e.nieusprawiedliwione;
    if (nu && parseInt(nu.state, 10) > 0) chips.push(chip('mdi:account-cancel-outline', `${nu.state} nieuspr.`, 'bad', nu.entity_id));
    const stt = e.status;
    if (stt && stt.state !== 'ok') chips.push(chip('mdi:alert-circle-outline', 'Część danych nie wczytana', 'bad', stt.entity_id));
    const upd = (e.plan || stt || {}).attributes;
    const time = upd && upd.last_update ? String(upd.last_update).slice(11, 16) : '';
    return `<div class="head"><div class="av"><ha-icon icon="mdi:account-school"></ha-icon></div>
      <div class="hn"><div class="name">${mdEsc(mdStudentName(hass, this._config.device))}</div>
      <div class="sub">mobiDziennik${time ? ` · aktualizacja ${time}` : ''}</div></div></div>
      ${chips.length ? `<div class="chips">${chips.join('')}</div>` : ''}`;
  }

  _title(icon, text, extra = '') {
    return `<div class="st"><ha-icon icon="${icon}"></ha-icon><span>${text}</span>${extra}</div>`;
  }

  _plan(e) {
    const p = e.plan;
    if (!p) return '';
    const lessons = p.attributes.lekcje || [];
    const days = [...new Set(lessons.map((l) => l.d))].sort();
    const today = mdIso(new Date());
    const now = new Date().toTimeString().slice(0, 5);
    let base = days.indexOf(p.attributes.najblizszy_dzien);
    if (base < 0) {
      base = days.findIndex((d) => d >= today);
      const td = lessons.filter((l) => l.d === today);
      if (td.length && (td[td.length - 1].g.split('-')[1] || '99') <= now) base = days.findIndex((d) => d > today);
    }
    if (!days.length || base < 0) {
      return this._title('mdi:calendar-clock', 'Plan lekcji') + '<div class="empty">Brak lekcji w pobranym planie (bieżący i następny tydzień).</div>';
    }
    const idx = Math.max(0, Math.min(days.length - 1, base + this._dayOffset));
    this._dayOffset = idx - base;
    const day = days[idx];
    const diff = mdDayDiff(day);
    const dname = diff === 0 ? 'Dziś' : diff === 1 ? 'Jutro' : MD_DAYS[mdParse(day).getDay()].replace(/^./, (c) => c.toUpperCase());
    const nav = `<span class="nav"><button data-act="prev" ${idx <= 0 ? 'disabled' : ''} title="Poprzedni dzień">‹</button>
      ${this._dayOffset ? '<button data-act="today" title="Najbliższy dzień">•</button>' : ''}
      <button data-act="next" ${idx >= days.length - 1 ? 'disabled' : ''} title="Następny dzień">›</button></span>`;
    let h = this._title('mdi:calendar-clock', `${dname} <small>${mdDM(day)}</small>`, nav);
    h += '<div class="list">';
    for (const l of lessons.filter((x) => x.d === day)) {
      const [g1, g2] = (l.g || '').split('-');
      const cls = day === today ? ((g2 && g2 <= now) ? 'past' : (g1 <= now ? 'now' : '')) : (day < today ? 'past' : '');
      const sub = [l.s && `sala ${mdEsc(l.s)}`, l.n && mdEsc(l.n)].filter(Boolean).join(' · ');
      h += `<div class="row ${cls}${l.st ? ' changed' : ''}">
        <div class="time"><b>${mdEsc(g1 || '')}</b><span>${mdEsc(g2 || '')}</span></div>
        <div class="main"><div class="t">${l.nr != null ? `<span class="nr">${mdEsc(l.nr)}</span>` : ''}${mdEsc(l.p)}</div>
        ${sub ? `<div class="s">${sub}</div>` : ''}${l.st ? `<div class="tag warn">${mdEsc(l.st)}</div>` : ''}</div>
        ${cls === 'now' ? '<span class="badge ok">teraz</span>' : ''}</div>`;
    }
    return h + '</div>';
  }

  _exams(e) {
    const s = e.sprawdziany;
    if (!s) return '';
    const today = mdIso(new Date());
    const list = (s.attributes.sprawdziany || []).filter((x) => !x.data_iso || x.data_iso.slice(0, 10) >= today).slice(0, 6);
    let h = this._title('mdi:file-document-edit-outline', 'Sprawdziany i kartkówki');
    if (!list.length) return h + '<div class="empty">Brak zapowiedzianych sprawdzianów 🎉</div>';
    h += '<div class="list">';
    for (const x of list) {
      const iso = x.data_iso ? x.data_iso.slice(0, 10) : null;
      const d = iso != null ? mdDayDiff(iso) : null;
      const when = d == null ? mdEsc(x.data) : d === 0 ? 'dziś' : d === 1 ? 'jutro' : `za ${d} dni`;
      const cls = d != null && d <= 1 ? 'bad' : d != null && d <= 3 ? 'warn' : '';
      const kind = String(x.rodzaj || '').toLowerCase();
      h += `<div class="row"><div class="time"><b>${iso ? MD_DAYS_SHORT[mdParse(iso).getDay()] : ''}</b><span>${iso ? mdDM(iso) : ''}</span></div>
        <div class="main"><div class="t">${mdEsc(x.przedmiot)} <span class="tag ${kind.startsWith('kart') ? '' : 'acc'}">${mdEsc(x.rodzaj || '')}</span></div>
        ${x.tematyka ? `<div class="s">${mdEsc(x.tematyka)}</div>` : ''}</div>
        <span class="badge ${cls}">${when}</span></div>`;
    }
    return h + '</div>';
  }

  _messages(e) {
    const m = e.wiadomosci;
    if (!m) return '';
    const list = (m.attributes.wiadomosci || []).slice(0, 6);
    let h = this._title('mdi:email-outline', 'Wiadomości', parseInt(m.state, 10) > 0 ? `<span class="badge warn">${mdEsc(m.state)} nowe</span>` : '');
    if (!list.length) return h + '<div class="empty">Brak wiadomości.</div>';
    h += '<div class="list">';
    for (const x of list) {
      const unread = !x.przeczytana;
      const inner = `<div class="main"><div class="t">${unread ? '<span class="dot"></span>' : ''}${mdEsc(x.temat || '(bez tematu)')}${x.zalacznik ? ' <ha-icon class="clip" icon="mdi:paperclip"></ha-icon>' : ''}</div>
        <div class="s">${mdEsc(x.nadawca || '')}${x.data ? ` · ${mdEsc(x.data)}` : ''}</div></div>`;
      h += x.url ? `<a class="row msg ${unread ? 'unread' : ''}" href="${mdEsc(x.url)}" target="_blank" rel="noopener">${inner}<ha-icon class="go" icon="mdi:open-in-new"></ha-icon></a>`
        : `<div class="row msg ${unread ? 'unread' : ''}">${inner}</div>`;
    }
    return h + '</div>';
  }

  _gradeCls(v) {
    if (v == null || isNaN(v)) return '';
    return v >= 5 ? 'g5' : v >= 4 ? 'g4' : v >= 3 ? 'g3' : v >= 2 ? 'g2' : 'g1';
  }

  _grades(e) {
    const g = e.oceny;
    if (!g) return '';
    const grades = (g.attributes.oceny || []).slice(0, 8);
    const avgs = Object.entries(g.attributes.srednie || {}).sort((a, b) => b[1] - a[1]);
    let h = this._title('mdi:star-circle-outline', 'Ostatnie oceny');
    if (!grades.length) h += '<div class="empty">Brak ocen w tym semestrze.</div>';
    else {
      h += '<div class="list">';
      for (const x of grades) {
        const sub = [x.kategoria, x.opis].filter(Boolean).map(mdEsc).join(' · ');
        h += `<div class="row"><span class="grade ${this._gradeCls(x.wartosc)}">${mdEsc(x.ocena)}</span>
          <div class="main"><div class="t">${mdEsc(x.przedmiot)}</div>${sub ? `<div class="s">${sub}</div>` : ''}</div>
          <span class="date">${mdEsc(x.data || '')}</span></div>`;
      }
      h += '</div>';
    }
    if (avgs.length) {
      const show = this._allAvg ? avgs : avgs.slice(0, 5);
      h += this._title('mdi:chart-bar', 'Średnie przedmiotów');
      h += '<div class="avgs">';
      for (const [subj, v] of show) {
        h += `<div class="avg"><span class="an">${mdEsc(subj)}</span><span class="bar"><i class="${this._gradeCls(v)}" style="width:${Math.max(4, Math.min(100, (v / 6) * 100))}%"></i></span><b>${Number(v).toFixed(2)}</b></div>`;
      }
      if (avgs.length > 5) h += `<button class="link" data-act="avg">${this._allAvg ? 'Pokaż mniej' : `Pokaż wszystkie (${avgs.length})`}</button>`;
      h += '</div>';
    }
    return h;
  }

  _attendance(e) {
    const f = e.frekwencja;
    const u = e.uwagi;
    let h = '';
    if (f) {
      const a = f.attributes;
      h += this._title('mdi:account-check-outline', 'Frekwencja');
      h += `<div class="tiles">
        <div class="tile ${a.nieusprawiedliwione ? 'bad' : ''}"><b>${a.nieusprawiedliwione ?? 0}</b><span>nieusprawiedliwione</span></div>
        <div class="tile"><b>${a.usprawiedliwione ?? 0}</b><span>usprawiedliwione</span></div>
        <div class="tile ${a.spoznienia ? 'warn' : ''}"><b>${a.spoznienia ?? 0}</b><span>spóźnienia</span></div></div>`;
      const list = (a.frekwencja || []).filter((x) => !/obecn/i.test(x.status || '') || /nieobecn/i.test(x.status || '')).slice(0, 5);
      if (list.length) {
        h += '<div class="list">';
        for (const x of list) {
          const st = String(x.status || '').toLowerCase();
          const cls = st.includes('nieusprawiedliw') ? 'bad' : st.includes('spóźn') ? 'warn' : '';
          h += `<div class="row"><div class="main"><div class="t">${mdEsc(x.przedmiot || 'lekcja')}</div><div class="s">${mdEsc(x.data || '')}${x.godzina ? ` · ${mdEsc(x.godzina)}` : ''}</div></div><span class="badge ${cls}">${mdEsc(x.status || '')}</span></div>`;
        }
        h += '</div>';
      }
    }
    if (u) {
      const list = (u.attributes.uwagi || []).slice(0, 3);
      h += this._title('mdi:comment-alert-outline', 'Uwagi i pochwały');
      if (!list.length) h += '<div class="empty">Brak wpisów.</div>';
      else {
        h += '<div class="list">';
        for (const x of list) {
          const pts = String(x.punkty || '').trim();
          const cls = pts.startsWith('-') ? 'bad' : pts && pts !== '0' ? 'ok' : '';
          h += `<div class="row"><div class="main"><div class="t wrap">${mdEsc(x.tresc)}</div><div class="s">${mdEsc(x.nauczyciel || '')}${x.data ? ` · ${mdEsc(x.data)}` : ''}</div></div>${pts ? `<span class="badge ${cls}">${mdEsc(pts)}</span>` : ''}</div>`;
        }
        h += '</div>';
      }
    }
    return h;
  }
}

const MD_CSS = `
  ha-card { padding: 16px; }
  .head { display:flex; align-items:center; gap:12px; }
  .av { width:44px; height:44px; border-radius:50%; display:grid; place-items:center; background:rgba(var(--rgb-primary-color, 3,169,244),.15); color:var(--primary-color); flex:none; }
  .name { font-size:20px; font-weight:600; color:var(--primary-text-color); }
  .sub { font-size:13px; color:var(--secondary-text-color); }
  .chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }
  .chip { display:inline-flex; align-items:center; gap:4px; padding:4px 10px; border-radius:999px; font-size:13px; cursor:pointer;
          background:var(--secondary-background-color); color:var(--primary-text-color); border:1px solid var(--divider-color); }
  .chip ha-icon { --mdc-icon-size:16px; color:var(--secondary-text-color); }
  .chip.ok ha-icon { color:var(--success-color, #43a047); } .chip.warn ha-icon { color:var(--warning-color, #ffa600); } .chip.bad ha-icon { color:var(--error-color, #db4437); }
  .st { display:flex; align-items:center; gap:8px; margin:18px 0 6px; font-weight:600; font-size:15px; color:var(--primary-text-color); }
  .st:first-child { margin-top:0; }
  .st ha-icon { --mdc-icon-size:20px; color:var(--primary-color); }
  .st small { font-weight:400; color:var(--secondary-text-color); margin-left:4px; }
  .st > span:first-of-type { flex:1; }
  .nav { display:flex; gap:4px; }
  .nav button { width:30px; height:30px; border-radius:50%; border:1px solid var(--divider-color); background:none; color:var(--primary-text-color); font-size:18px; line-height:1; cursor:pointer; }
  .nav button:disabled { opacity:.3; cursor:default; }
  .list { display:flex; flex-direction:column; }
  .row { display:flex; align-items:center; gap:12px; padding:8px 0; border-top:1px solid var(--divider-color); color:inherit; text-decoration:none; }
  .list .row:first-child { border-top:none; }
  .row.past { opacity:.5; }
  .row.now { background:rgba(var(--rgb-primary-color, 3,169,244),.08); border-radius:10px; padding-left:8px; padding-right:8px; border-top-color:transparent; }
  .row.changed .t { text-decoration:line-through; text-decoration-color:var(--warning-color, #ffa600); }
  .time { width:46px; flex:none; display:flex; flex-direction:column; font-size:13px; color:var(--secondary-text-color); }
  .time b { color:var(--primary-text-color); font-size:14px; }
  .main { flex:1; min-width:0; }
  .t { font-size:14.5px; color:var(--primary-text-color); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .t.wrap { white-space:normal; }
  .s { font-size:12.5px; color:var(--secondary-text-color); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .nr { display:inline-block; min-width:18px; margin-right:6px; padding:0 4px; border-radius:6px; font-size:11.5px; text-align:center; background:var(--secondary-background-color); color:var(--secondary-text-color); }
  .tag { display:inline-block; margin-top:2px; padding:0 6px; border-radius:6px; font-size:11.5px; background:var(--secondary-background-color); color:var(--secondary-text-color); text-decoration:none; }
  .tag.acc { background:rgba(var(--rgb-primary-color, 3,169,244),.15); color:var(--primary-color); }
  .tag.warn { background:rgba(255,166,0,.15); color:var(--warning-color, #ffa600); }
  .badge { flex:none; padding:2px 8px; border-radius:999px; font-size:12px; background:var(--secondary-background-color); color:var(--secondary-text-color); }
  .badge.ok { background:rgba(67,160,71,.15); color:var(--success-color, #43a047); }
  .badge.warn { background:rgba(255,166,0,.15); color:var(--warning-color, #ffa600); }
  .badge.bad { background:rgba(219,68,55,.15); color:var(--error-color, #db4437); }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--primary-color); margin-right:6px; vertical-align:middle; }
  .msg.unread .t { font-weight:600; }
  .clip { --mdc-icon-size:14px; color:var(--secondary-text-color); }
  .go { --mdc-icon-size:16px; color:var(--secondary-text-color); flex:none; }
  .grade { width:34px; height:34px; flex:none; border-radius:10px; display:grid; place-items:center; font-weight:700; font-size:15px; background:var(--secondary-background-color); color:var(--primary-text-color); }
  .g5 { background:rgba(67,160,71,.18) !important; color:var(--success-color, #43a047) !important; }
  .g4 { background:rgba(3,169,244,.16) !important; color:var(--info-color, #039be5) !important; }
  .g3 { background:rgba(158,158,158,.18) !important; }
  .g2 { background:rgba(255,166,0,.18) !important; color:var(--warning-color, #ffa600) !important; }
  .g1 { background:rgba(219,68,55,.18) !important; color:var(--error-color, #db4437) !important; }
  .date { font-size:12px; color:var(--secondary-text-color); flex:none; }
  .avgs { display:flex; flex-direction:column; gap:6px; }
  .avg { display:grid; grid-template-columns:minmax(0,1fr) 90px 40px; gap:8px; align-items:center; font-size:13.5px; color:var(--primary-text-color); }
  .an { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .avg b { text-align:right; }
  .bar { height:8px; border-radius:4px; background:var(--secondary-background-color); overflow:hidden; }
  .bar i { display:block; height:100%; border-radius:4px; background:var(--primary-color); }
  .bar i.g5 { background:var(--success-color, #43a047) !important; } .bar i.g2, .bar i.g1 { background:var(--warning-color, #ffa600) !important; }
  .link { align-self:flex-start; background:none; border:none; padding:4px 0; color:var(--primary-color); cursor:pointer; font-size:13px; }
  .tiles { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin-bottom:6px; }
  .tile { border-radius:12px; padding:8px; text-align:center; background:var(--secondary-background-color); }
  .tile b { display:block; font-size:20px; color:var(--primary-text-color); }
  .tile span { font-size:11.5px; color:var(--secondary-text-color); }
  .tile.bad b { color:var(--error-color, #db4437); } .tile.warn b { color:var(--warning-color, #ffa600); }
  .empty { color:var(--secondary-text-color); font-size:14px; padding:6px 0; }
`;

class MobiDziennikCardEditor extends HTMLElement {
  setConfig(config) { this._config = { ...config }; this._render(); }
  set hass(hass) { this._hass = hass; this._render(); }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement('ha-form');
      this._form.computeLabel = (s) => ({ device: 'Uczeń', sekcje: 'Co pokazać', naglowek: 'Nagłówek (imię, numerek, średnia, nowe wiadomości)' }[s.name] || s.name);
      this._form.addEventListener('value-changed', (ev) => {
        this._config = { ...this._config, ...ev.detail.value };
        const e2 = new Event('config-changed', { bubbles: true, composed: true });
        e2.detail = { config: this._config };
        this.dispatchEvent(e2);
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = { sekcje: MD_SECTIONS.map((s) => s[0]), naglowek: true, ...this._config };
    this._form.schema = [
      { name: 'device', required: true, selector: { device: { integration: 'mobidziennik' } } },
      { name: 'sekcje', selector: { select: { multiple: true, mode: 'list', options: MD_SECTIONS.map(([value, label]) => ({ value, label })) } } },
      { name: 'naglowek', selector: { boolean: {} } },
    ];
  }
}

if (!customElements.get('mobidziennik-card')) customElements.define('mobidziennik-card', MobiDziennikCard);
if (!customElements.get('mobidziennik-card-editor')) customElements.define('mobidziennik-card-editor', MobiDziennikCardEditor);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === 'mobidziennik-card')) {
  window.customCards.push({
    type: 'mobidziennik-card',
    name: 'mobiDziennik',
    description: 'Plan lekcji, sprawdziany, wiadomości, oceny i frekwencja ucznia – bez pisania kodu.',
    preview: true,
    documentationURL: 'https://github.com/bobby6robert/mobiDziennik',
  });
}

/* ── Kreator pulpitu ─────────────────────────────────────────────────────── */
const MD_DASH = 'mobi-dziennik';
const mdSlug = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/ł/g, 'l')
  .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

function mdView(p, existing) {
  let path = mdSlug(p.uczen) || 'uczen';
  while (existing.has(path)) path += '-2';
  const card = (sekcje, naglowek = false) => ({ type: 'custom:mobidziennik-card', device: p.device_id, sekcje, naglowek });
  return {
    title: p.uczen,
    path,
    icon: 'mdi:account-school',
    type: 'sections',
    max_columns: 3,
    sections: [
      { type: 'grid', cards: [card(['plan'], true)] },
      { type: 'grid', cards: [card(['sprawdziany']), card(['wiadomosci'])] },
      { type: 'grid', cards: [card(['oceny']), card(['frekwencja'])] },
    ],
  };
}

async function mdCreateDashboards(hass) {
  const pending = await hass.callWS({ type: 'mobidziennik/pulpit' });
  if (!pending || !pending.length) return;
  const dashboards = await hass.callWS({ type: 'lovelace/dashboards/list' });
  if (!dashboards.some((d) => d.url_path === MD_DASH)) {
    await hass.callWS({ type: 'lovelace/dashboards/create', url_path: MD_DASH, title: 'mobiDziennik', icon: 'mdi:school-outline', show_in_sidebar: true, require_admin: false, mode: 'storage' });
  }
  let config;
  try { config = await hass.callWS({ type: 'lovelace/config', url_path: MD_DASH }); } catch (err) { config = null; }
  if (!config || !Array.isArray(config.views)) config = { title: 'mobiDziennik', views: [] };
  const created = [];
  for (const p of pending) {
    const already = config.views.some((v) => JSON.stringify(v).includes(p.device_id));
    if (!already) {
      config.views.push(mdView(p, new Set(config.views.map((v) => v.path))));
      created.push(p.uczen);
    }
  }
  if (created.length) await hass.callWS({ type: 'lovelace/config/save', url_path: MD_DASH, config });
  for (const p of pending) await hass.callWS({ type: 'mobidziennik/pulpit_gotowy', entry_id: p.entry_id });
  if (created.length) {
    const ha = document.querySelector('home-assistant');
    if (ha) {
      const ev = new Event('hass-notification', { bubbles: true, composed: true });
      ev.detail = { message: `Utworzono pulpit mobiDziennik: ${created.join(', ')} (menu boczne)` };
      ha.dispatchEvent(ev);
    }
  }
}

(function mdAutoDashboard() {
  if (window.__mdAutoDashboard) return;
  window.__mdAutoDashboard = true;
  let busy = false;
  let timer = null;
  const run = async (hass) => {
    if (busy) return;
    busy = true;
    try { await mdCreateDashboards(hass); } catch (err) { console.warn('mobiDziennik – kreator pulpitu:', err); }
    busy = false;
  };
  let tries = 0;
  const wait = setInterval(() => {
    tries += 1;
    const ha = document.querySelector('home-assistant');
    const hass = ha && ha.hass;
    if (hass && hass.connection && hass.user) {
      clearInterval(wait);
      if (!hass.user.is_admin) return;
      run(hass);
      // nowe czujniki (np. właśnie dodany uczeń) → sprawdź ponownie za chwilę
      hass.connection.subscribeEvents(() => {
        clearTimeout(timer);
        timer = setTimeout(() => run(document.querySelector('home-assistant').hass), 4000);
      }, 'entity_registry_updated').catch(() => {});
    } else if (tries > 120) clearInterval(wait);
  }, 1000);
})();

console.info(`%c mobiDziennik %c ${MD_VERSION} `, 'background:#1565c0;color:#fff;border-radius:3px 0 0 3px', 'background:#e3f2fd;color:#1565c0;border-radius:0 3px 3px 0');
