// Landing page: looping phone preview, live "Try it" box (local model only), theme toggle.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch { /* */ } } };

  // theme (shared key with the app)
  const th = store.get('theme'); if (th) document.documentElement.dataset.theme = th;
  $('themeBtn').onclick = () => {
    const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    document.documentElement.dataset.theme = next; store.set('theme', next);
  };

  // ---------------- phone preview: a scripted visit on loop
  const SCRIPT = [
    ['you', 'सुनीता देवी, ID 1001, ANC visit', null, 'visit', 'ANC', 180],
    ['sakhi', 'Sunita Devi, ID 1001, ANC विज़िट, नोट किया.'],
    ['you', 'वज़न 58 किलो, BP 120 by 80', null, 'weight', '58 kg', 210],
    ['fill', null, null, 'bp', '120/80'],
    ['sakhi', 'वज़न 58 किलो, BP 120/80, नोट किया.'],
    ['you', 'नहीं, BP 150 by 100 है और तेज़ सिर दर्द', null, 'bp', '150/100', 240],
    ['alert', 'ध्यान दीजिए: खतरे का संकेत. अभी PHC रेफ़र कीजिए.'],
    ['alarm'],
    ['you', 'Hb 10.5, बस save करो', null, 'hb', '10.5', 190],
    ['sakhi', 'Sunita Devi, ID 1001, ANC… सब सही है?'],
    ['you', 'हाँ सही है'],
    ['sakhi', 'सेव हो गया, दीदी. याद रखिए: आज ही PHC रेफ़र करें.'],
  ];
  const chat = $('pChat'), card = $('pCard'), alarm = $('pAlarm');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function reset() {
    chat.innerHTML = ''; alarm.hidden = true;
    card.querySelectorAll('[data-f]').forEach((d) => { d.className = ''; d.querySelector('b').textContent = '—'; });
  }
  function fill(f, v, bad) {
    const d = card.querySelector(`[data-f="${f}"]`); if (!d) return;
    d.querySelector('b').textContent = v; d.className = bad ? 'bad' : 'fill';
    if (!bad) setTimeout(() => { if (d.className === 'fill') d.className = ''; }, 1400);
  }
  const pm = $('pMascot');
  let talkTimer = null;
  function talk(ms) {  // the preview has no audio: animate the mouth for roughly the length of the line
    if (!window.Mascot) return;
    Mascot.set(pm, 'speaking'); clearInterval(talkTimer);
    const t0 = performance.now();
    talkTimer = setInterval(() => {
      const el = performance.now() - t0;
      if (el > ms) { clearInterval(talkTimer); Mascot.mouth(pm, 0); Mascot.set(pm, 'listening'); return; }
      Mascot.mouth(pm, 0.25 + 0.75 * Math.abs(Math.sin(el / 95)) * Math.random());
    }, 90);
  }
  function bubble(kind, text) {
    if (window.Mascot) {
      if (kind === 'you') Mascot.set(pm, 'hearing');
      else talk(Math.min(2600, 380 + text.length * 32));
      if (kind === 'alert') Mascot.flag(pm, 'worried', 4500);
      if (kind === 'sakhi' && /सेव हो गया|Saved/.test(text)) Mascot.flag(pm, 'happy', 3000);
    }
    const b = document.createElement('div'); b.className = 'bub ' + kind;
    b.innerHTML = `<small>${kind === 'you' ? 'ASHA' : kind === 'alert' ? 'SAKHI · ESCALATION' : 'SAKHI'}</small>${esc(text)}`;
    chat.appendChild(b);
    while (chat.children.length > 4) chat.firstChild.remove();
  }
  async function loop() {
    for (;;) {
      reset();
      for (const [kind, text, , f, v, lat] of SCRIPT) {
        if (kind === 'you') { bubble('you', text); await sleep(700); if (f) fill(f, v, f === 'bp' && v === '150/100'); if (lat) $('pLat').textContent = `⏱ ${lat} ms`; }
        else if (kind === 'fill') fill(f, v);
        else if (kind === 'alarm') { alarm.hidden = false; await sleep(1800); alarm.hidden = true; }
        else bubble(kind, text);
        await sleep(kind === 'fill' ? 100 : 1200);
      }
      await sleep(2600);
    }
  }
  if (reduce) { SCRIPT.filter((s) => s[0] === 'you' || s[0] === 'sakhi').slice(0, 4).forEach((s) => bubble(s[0], s[1])); fill('visit', 'ANC'); fill('weight', '58 kg'); fill('bp', '120/80'); }
  else loop();

  // ---------------- Try it: how the LOCAL model reads a sentence
  const LABEL = { beneficiary_id: 'ID', beneficiary_name: 'name', beneficiary_type: 'type', visit_type: 'visit', weight_kg: 'weight kg',
    bp_systolic: 'BP top', bp_diastolic: 'BP bottom', hb_g_dl: 'Hb g/dL', temperature_f: 'temp °F', temperature_c: 'temp °C',
    respiratory_rate: 'breaths/min', ifa_tablets_given: 'IFA tablets', vaccinations_given_today: 'vaccines today', next_visit_date: 'next visit' };
  async function understand(text) {
    const out = $('tryOut');
    const t0 = performance.now();
    let r;
    try {
      r = await (await fetch('/api/understand', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) })).json();
    } catch { out.innerHTML = '<div class="res"><span class="res-badge ai">server offline</span></div>'; return; }
    const ms = Math.round(performance.now() - t0);
    let h = '<div class="res">';
    if (r.local) h += `<span class="res-badge local">⚡ understood locally · no AI call · ${ms} ms round-trip</span>`;
    else h += `<span class="res-badge ai">→ sent to the AI · ${esc(r.why || '')}</span>`;
    if (r.danger && r.danger.length) h += `<span class="res-badge danger">🚨 ${r.danger.map((d) => esc(d.label_en)).join(' · ')} → refer to PHC now</span>`;
    const f = Object.entries(r.set || {});
    if (f.length) h += '<div class="res-fields">' + f.map(([k, v]) => `<span><b>${esc(LABEL[k] || k)}</b>${esc(Array.isArray(v) ? v.join(', ') : v)}</span>`).join('') + '</div>';
    if (r.say && r.say.length) h += `<div class="res-say">${esc(r.say.join(' '))}</div>`;
    out.innerHTML = h + '</div>';
  }
  $('tryForm').onsubmit = (e) => { e.preventDefault(); const v = $('tryIn').value.trim(); if (v) understand(v); };
  $('tryChips').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; $('tryIn').value = b.textContent; understand(b.textContent); };
  understand($('tryIn').value);
})();
