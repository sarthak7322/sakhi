// Sakhi phone client.
// mic -> AudioWorklet (16 kHz PCM16 frames) -> /ws ; plays streamed 24 kHz PCM ; renders the live record,
// the speech pipeline and a per-turn voice log built from the same event stream the server persists.
(() => {
  const $ = (id) => document.getElementById(id);
  const TTS_RATE = 24000;
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };
  let lang = store.get('lang') || 'hi-IN';
  let ws, micCtx, playCtx, micStream, workletNode, micAnalyser, playAnalyser, playGain;
  let nextPlay = 0, sources = [], minTurn = 0, muted = false, useMic = true, wantOpen = false;
  let state = 'collecting', lastRecord = null, signInfo = {}, shownDanger = new Set();
  const entries = [];            // live voice-log events (same shape as data/voicelogs/*.jsonl)
  let viewEntries = null;        // a past session picked in the log tab
  let lastUserTurn = 0, lastAgentTurn = -1;
  let liveSession = null; let reported = new Set(); let openReport = null;

  // ------------------------------------------------------------------ i18n
  const L = {
    hi: {
      tab_visit: 'विज़िट', tab_log: 'वॉइस लॉग', tab_follow: 'फ़ॉलो-अप', tab_register: 'रजिस्टर', tab_records: 'रिकॉर्ड',
      step1: 'बोलिए', step2: 'जाँच', step3: 'सेव', new: 'नई विज़िट', try: 'बोलकर देखिए:',
      visit_type: 'विज़िट', weight: 'वज़न', bp: 'बीपी', hb: 'हीमोग्लोबिन', temp: 'तापमान', rr: 'सांस/मिनट',
      ifa: 'IFA गोली', vgiven: 'आज टीके', next: 'अगली विज़िट', notes: 'नोट्स', vdue: 'टीके बाकी:',
      listening: 'सुन रही हूँ… बोलिए', hearing: 'आप बोल रही हैं…', thinking: 'समझ रही हूँ…', speaking: 'सखी बोल रही है…',
      idle: 'कनेक्ट हो रहा है…', muted: 'माइक बंद है', typed: 'टाइप करके बताइए',
      done: 'बोल दिया', yes: 'हाँ, सही है', suggested: 'सुझाव', newb: 'नया लाभार्थी', danger: 'खतरे का संकेत — अभी PHC रेफ़र करें',
      you_label: 'आप · ASHA', patient: 'मरीज़', edit_hint: 'किसी भी खाने पर टैप करके बदलें', saved_t: 'रिकॉर्ड सेव हो गया', next_visit_btn: 'अगली विज़िट',
      none: 'कोई फ़ॉलो-अप नहीं', urgent: 'तुरंत', high: 'टीके', normal: 'अगली विज़िट', check: 'जाँचें',
    },
    en: {
      tab_visit: 'Visit', tab_log: 'Voice log', tab_follow: 'Follow-ups', tab_register: 'Register', tab_records: 'Records',
      step1: 'Speak', step2: 'Check', step3: 'Saved', new: 'New visit', try: 'Try saying:',
      visit_type: 'Visit', weight: 'Weight', bp: 'BP', hb: 'Hb', temp: 'Temperature', rr: 'Breaths/min',
      ifa: 'IFA tablets', vgiven: 'Vaccines today', next: 'Next visit', notes: 'Notes', vdue: 'Vaccines due:',
      listening: 'Listening… speak', hearing: 'You are speaking…', thinking: 'Understanding…', speaking: 'Sakhi is speaking…',
      idle: 'Connecting…', muted: 'Microphone off', typed: 'Type what she says',
      done: 'Done speaking', yes: 'Yes, correct', suggested: 'suggested', newb: 'new beneficiary', danger: 'Danger sign — refer to PHC now',
      you_label: 'You · ASHA', patient: 'Patient', edit_hint: 'Tap any field to correct it', saved_t: 'Record saved', next_visit_btn: 'Next visit',
      none: 'No follow-ups', urgent: 'Urgent', high: 'Vaccines', normal: 'Next visits', check: 'To verify',
    },
  };
  const hiUI = () => ['hi-IN', 'mr-IN'].includes(lang);
  const t = (k) => (hiUI() ? L.hi : L.en)[k] ?? L.en[k] ?? k;
  function applyI18n() { document.querySelectorAll('[data-t]').forEach((el) => { el.textContent = t(el.dataset.t); }); }

  const TRY = {
    hi: ['सुनीता देवी, ID 1001, ANC visit', 'वज़न 58 किलो, BP 120 by 80', 'Hb 10.5, IFA की 30 गोली दी', 'नहीं, वज़न 56 किलो है',
      'बच्चा दूध नहीं पी रहा', 'ANC क्या होता है?', 'बस, save करो', 'हाँ सही है'],
    en: ['Sunita Devi, ID 1001, ANC visit', 'Weight 58 kilo, BP 120 by 80', 'Hb 10.5, gave 30 IFA tablets', 'No, weight is 56 kilo',
      'She has severe headache and blurred vision', 'What is PNC?', 'That is all, save it', 'Yes, correct'],
  };
  const DEMOS = {
    hi: [
      { title: 'नवजात HBNC · खतरे का संकेत', sub: 'Newborn visit: low feeding and fever trigger an instant PHC referral',
        lines: ['Fatima Bano ke bachche ka HBNC visit, ID 1009', 'बच्चे का weight 2.1 किलो, temperature 99.8',
          'बच्चा दूध ठीक से नहीं पी रहा', 'बस, save करो', 'हाँ सही है'] },
      { title: 'ANC · आवाज़ से सुधार', sub: 'Pregnancy check-up with a spoken correction and read-back',
        lines: ['सुनीता देवी, ID 1001, ANC visit', 'वज़न 58 किलो, BP 120 by 80', 'Hb 10.5, IFA की 30 गोली दी',
          'नहीं नहीं, वज़न 56 किलो है', 'Td-2 का टीका आज लगा', 'हो गया, save करो', 'हाँ सही है'] },
      { title: 'High BP + सिर दर्द', sub: 'Numbers and words both detect a maternal danger sign',
        lines: ['रेखा कुमारी ID 1002 का ANC visit', 'BP 150 by 100 है और तेज़ सिर दर्द हो रहा है, धुंधला दिख रहा है',
          'save करो', 'हाँ सही है'] },
      { title: 'टीकाकरण · बच्चा', sub: 'Child immunisation: vaccines due are computed from the schedule',
        lines: ['आरव सिंह, ID 2001, टीकाकरण visit', 'वज़न 4.6 किलो', 'आज Penta-1, OPV-1 और Rota-1 लगा', 'बस हो गया', 'हाँ सही है'] },
    ],
    en: [
      { title: 'Newborn HBNC · danger sign', sub: 'Low feeding and fever trigger an instant PHC referral',
        lines: ['HBNC visit for Fatima Bano\'s baby, ID 1009', 'Baby weight 2.1 kilo, temperature 99.8', 'The baby is not feeding well',
          'That is all, save it', 'Yes, correct'] },
      { title: 'ANC · voice correction', sub: 'Pregnancy check-up with a spoken correction and read-back',
        lines: ['Sunita Devi, ID 1001, ANC visit', 'Weight 58 kilo, BP 120 by 80', 'Hb 10.5, gave 30 IFA tablets',
          'No no, weight is 56 kilo', 'Td-2 injection given today', 'Done, save it', 'Yes, correct'] },
      { title: 'High BP + headache', sub: 'Numbers and words both detect a maternal danger sign',
        lines: ['ANC visit for Rekha Kumari, ID 1002', 'BP 150 by 100 and she has severe headache with blurred vision', 'Save it', 'Yes, correct'] },
    ],
  };

  // ------------------------------------------------------------------ theme
  const savedTheme = store.get('theme'); if (savedTheme) document.documentElement.dataset.theme = savedTheme;
  $('themeBtn').onclick = () => {
    const dark = document.documentElement.dataset.theme !== 'light';
    document.documentElement.dataset.theme = dark ? 'light' : 'dark'; store.set('theme', document.documentElement.dataset.theme);
  };

  // ------------------------------------------------------------------ setup
  let LANGS = [];
  fetch('/api/config').then((r) => r.json()).then((cfg) => {
    LANGS = cfg.languages;
    const grid = $('langs');
    cfg.languages.forEach((l) => {
      const b = document.createElement('button');
      b.innerHTML = `${esc(l.native)}<small>${esc(l.english)}</small>`;
      b.setAttribute('aria-pressed', String(l.code === lang));
      b.onclick = () => { lang = l.code; [...grid.children].forEach((c) => c.setAttribute('aria-pressed', 'false')); b.setAttribute('aria-pressed', 'true'); };
      grid.appendChild(b);
    });
    $('stackLine').textContent = `Gnani Prisma v3.0 STT · Timbre v3.0 TTS · ${cfg.llm} · streaming WebSockets`;
    if (!cfg.ready) showSetupErr('Server is missing an API key (Gnani or LLM). See README.');
  }).catch(() => showSetupErr('Cannot reach the server.'));
  fetch('/api/danger-signs').then((r) => r.json()).then((list) => { list.forEach((s) => { signInfo[s.id] = s; }); });
  $('ashaId').value = store.get('ashaId') || '';
  function showSetupErr(m) { const e = $('setupErr'); e.textContent = m; e.hidden = false; }

  let testStream = null, testCtx = null;
  async function micTest() {
    if (testStream) { stopMicTest(); return; }
    const msg = $('micTestMsg');
    try {
      msg.textContent = 'Allow the microphone…';
      testStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (e) { msg.textContent = '✗ No microphone access: ' + e.message; testStream = null; return; }
    testCtx = new AudioContext();
    const an = testCtx.createAnalyser(); an.fftSize = 512;
    testCtx.createMediaStreamSource(testStream).connect(an);
    const data = new Uint8Array(an.fftSize); let heard = 0;
    $('micTestBtn').textContent = '■ Stop test'; msg.textContent = 'Say something…'; msg.className = 'muted small';
    const tick = () => {
      if (!testStream) return;
      an.getByteTimeDomainData(data);
      let peak = 0; for (const v of data) peak = Math.max(peak, Math.abs(v - 128));
      const lvl = Math.min(1, peak / 70);
      $('micMeter').style.width = `${Math.round(lvl * 100)}%`;
      if (lvl > 0.25 && ++heard > 6) { msg.textContent = '✓ Microphone works'; msg.className = 'small ok'; }
      requestAnimationFrame(tick);
    };
    tick();
    setTimeout(stopMicTest, 15000);
  }
  function stopMicTest() {
    if (!testStream) return;
    testStream.getTracks().forEach((tr) => tr.stop()); testStream = null;
    try { testCtx.close(); } catch { /* closed */ }
    $('micMeter').style.width = '0'; $('micTestBtn').textContent = '🎧 Test microphone';
  }
  $('micTestBtn').onclick = micTest;

  async function begin(withMic) {
    stopMicTest();
    store.set('lang', lang); store.set('ashaId', $('ashaId').value.trim());
    useMic = withMic;
    const btn = withMic ? $('startBtn') : $('startTypedBtn'); const label = btn.innerHTML;
    if (withMic) { btn.disabled = true; btn.textContent = '🎙️ Allow microphone…'; }
    try {
      await startAudio(withMic);
    } catch (e) {
      showSetupErr('Microphone permission is needed (' + e.message + '). You can also start without the microphone.');
      return;
    } finally { btn.disabled = false; btn.innerHTML = label; }
    $('setup').hidden = true; $('app').hidden = false;
    document.documentElement.lang = lang.slice(0, 2);
    applyI18n(); renderTryChips(); renderRecord(null); renderLog(); drawWave(); fillLangSwitch();
    if (!withMic) { $('kbForm').hidden = false; $('muteBtn').hidden = true; document.body.classList.add('typed-mode'); }
    wantOpen = true; connect();
    try { await navigator.wakeLock?.request('screen'); } catch { /* optional */ }
  }
  function fillLangSwitch() {
    const sel = $('langSwitch');
    sel.innerHTML = LANGS.map((l) => `<option value="${esc(l.code)}">${esc(l.native)}</option>`).join('');
    sel.value = lang;
  }
  $('langSwitch').onchange = () => {
    lang = $('langSwitch').value; store.set('lang', lang);
    document.documentElement.lang = lang.slice(0, 2);
    applyI18n(); renderTryChips(); if (lastRecord) renderRecord(lastRecord);
    toast('🗣️ ' + $('langSwitch').selectedOptions[0].textContent);
    flushPlayback();
    if (ws) ws.close(); // onclose reconnects and starts a new session in the new language
  };
  $('startBtn').onclick = () => begin(true);
  if (new URLSearchParams(location.search).get('demo') === '1') {
    $('demoInvite').hidden = false;
    $('demoInviteBtn').onclick = async () => {
      await begin(false);
      const wait = setInterval(() => { if (ws && ws.readyState === 1 && LANGS.length) { clearInterval(wait); $('demoBtn').click(); } }, 200);
    };
  }
  $('startTypedBtn').onclick = () => begin(false);

  // ------------------------------------------------------------------ audio
  async function startAudio(withMic) {
    playCtx = new AudioContext();
    playGain = playCtx.createGain();
    playAnalyser = playCtx.createAnalyser(); playAnalyser.fftSize = 256;
    playGain.connect(playAnalyser); playAnalyser.connect(playCtx.destination);
    if (!withMic) return;
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    micCtx = new AudioContext();
    await micCtx.audioWorklet.addModule('/static/capture-worklet.js');
    const src = micCtx.createMediaStreamSource(micStream);
    workletNode = new AudioWorkletNode(micCtx, 'capture-16k');
    micAnalyser = micCtx.createAnalyser(); micAnalyser.fftSize = 256;
    src.connect(micAnalyser);
    workletNode.port.onmessage = (e) => { if (!muted && ws && ws.readyState === 1) ws.send(e.data.pcm); };
    src.connect(workletNode);
  }

  function playChunk(buf) {
    const turn = new DataView(buf).getUint32(0, true);
    if (turn < minTurn) return; // stale audio from an interrupted turn
    const pcm = new Int16Array(buf, 4);
    const ab = playCtx.createBuffer(1, pcm.length, TTS_RATE);
    const ch = ab.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
    const node = playCtx.createBufferSource();
    node.buffer = ab; node.connect(playGain);
    const start = Math.max(nextPlay, playCtx.currentTime + 0.03);
    node.start(start);
    nextPlay = start + ab.duration;
    sources.push(node);
    node.onended = () => { sources = sources.filter((s) => s !== node); if (!sources.length) { setOrb('listening'); pipe(null); } };
    setOrb('speaking'); pipe('pTTS');
  }
  function flushPlayback() {
    sources.forEach((s) => { try { s.stop(); } catch { /* already stopped */ } });
    sources = []; nextPlay = 0;
    if (playCtx.state === 'suspended') playCtx.resume();
  }

  // live waveform: Sakhi's voice while she speaks, otherwise the microphone
  const wave = $('wave'); const wctx = wave.getContext('2d'); const buf = new Uint8Array(128);
  let mouthO = 0, lastMouth = -1;
  function drawWave() {
    const w = wave.clientWidth, h = wave.clientHeight, dpr = devicePixelRatio || 1;
    if (wave.width !== w * dpr) { wave.width = w * dpr; wave.height = h * dpr; }
    wctx.setTransform(dpr, 0, 0, dpr, 0, 0); wctx.clearRect(0, 0, w, h);
    const speaking = sources.length > 0;
    const an = speaking ? playAnalyser : (muted ? null : micAnalyser);
    const css = getComputedStyle(document.documentElement);
    wctx.fillStyle = (speaking ? css.getPropertyValue('--brand') : css.getPropertyValue('--ok')).trim() || '#b4462b';
    const bars = Math.max(16, Math.floor(w / 6));
    if (an) an.getByteTimeDomainData(buf); else buf.fill(128);
    for (let i = 0; i < bars; i++) {
      let peak = 0;
      const a = Math.floor((i / bars) * buf.length), b = Math.floor(((i + 1) / bars) * buf.length);
      for (let j = a; j < b; j++) peak = Math.max(peak, Math.abs(buf[j] - 128));
      const bh = Math.max(2, Math.min(h, (peak / 128) * h * 2.4));
      wctx.globalAlpha = 0.35 + 0.65 * Math.min(1, peak / 40);
      wctx.fillRect(i * (w / bars) + 1, (h - bh) / 2, Math.max(2, w / bars - 2), bh);
    }
    wctx.globalAlpha = 1;
    // mascot lip-sync: mouth opening follows Sakhi's actual voice level
    let target = 0;
    if (speaking && playAnalyser) {
      let sum = 0; for (let k = 0; k < buf.length; k++) { const d = (buf[k] - 128) / 128; sum += d * d; }
      const rms = Math.sqrt(sum / buf.length);
      // voice level -> opening (TTS speech is ~0.05-0.25 RMS); add syllable-like movement while there is sound,
      // and close fully in the pauses between words
      const syll = 0.55 + 0.45 * Math.abs(Math.sin(performance.now() / 85));
      target = rms > 0.012 ? Math.min(1, 0.25 + rms * 9) * syll : 0;
    }
    mouthO = target > mouthO ? mouthO * 0.35 + target * 0.65 : mouthO * 0.6 + target * 0.4;  // open fast, close softer
    if (window.Mascot && Math.abs(mouthO - lastMouth) > 0.02) { Mascot.mouth($('mascot'), mouthO); lastMouth = mouthO; }
    requestAnimationFrame(drawWave);
  }

  // ------------------------------------------------------------------ socket
  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.binaryType = 'arraybuffer';
    setConn('wait', 'connecting');
    ws.onopen = () => ws.send(JSON.stringify({ type: 'start', lang, asha_id: store.get('ashaId') || '' }));
    ws.onmessage = (e) => (typeof e.data === 'string' ? onMsg(JSON.parse(e.data)) : playChunk(e.data));
    ws.onclose = () => { setConn('off', 'offline'); setOrb('idle'); if (wantOpen) setTimeout(connect, 1500); };
  }
  function send(obj) { if (ws && ws.readyState === 1) { ws.send(JSON.stringify(obj)); return true; } toast('Not connected yet'); return false; }
  function setConn(cls, text) { $('conn').className = 'conn ' + cls; $('conn').lastElementChild.textContent = text; }

  function onMsg(m) {
    if (LOGGED.has(m.type)) { entries.push(m); scheduleLog(); }
    switch (m.type) {
      case 'ready':
        setConn('on', 'live'); setOrb(useMic ? 'listening' : 'typed');
        if (window.Mascot) { Mascot.flag($('mascot'), 'waking', 1000); Mascot.flag($('mascot'), 'waving', 4200); }
        $('sessionId').textContent = m.session_id || ''; liveSession = m.session_id || null;
        $('pSTTv').textContent = m.stt || 'prisma-v3.0'; $('pLLMv').textContent = (m.llm || '').split(':').pop();
        $('pTTSv').textContent = m.tts || 'timbre-v3.0';
        break;
      case 'speech_start': setOrb('hearing'); pipe('pSTT'); break;
      case 'pause_playback': playCtx.suspend(); break;   // maybe barge-in: hold until the transcript decides
      case 'resume_playback': playCtx.resume(); break;   // it was only noise
      case 'barge_in': minTurn = m.turn + 1; flushPlayback(); break;
      case 'processing': setOrb('thinking'); showYou('…', true); pipe('pSTT'); break;
      case 'transcript': if (!m.text) setOrb(sources.length ? 'speaking' : 'listening'); break;
      case 'user_turn':
        $('bubble').classList.remove('alert'); window.Mascot && Mascot.unflag($('mascot'), 'waving');
        lastUserTurn = m.turn; showYou(m.text); setOrb('thinking'); pipe('pDNG'); setTimeout(() => pipe('pLLM'), 120); break;
      case 'agent_text': lastAgentTurn = m.turn; showSakhi(m.text); break;
      case 'record': lastRecord = m; state = m.state; renderRecord(m); break;
      case 'danger': navigator.vibrate?.([300, 120, 300, 120, 300]); pipe('pDNG', true); window.Mascot && Mascot.flag($('mascot'), 'worried', 9000); break;
      case 'saved': showSaved(m); loadFollowups(); window.Mascot && (Mascot.unflag($('mascot'), 'worried'), Mascot.flag($('mascot'), 'happy', 5000)); break;
      case 'metrics': updateKpis(); if (m.turn > 0) { $('pLLMv').textContent = m.model === 'local' ? '⚡ local (no API)' : m.model === 'learned' ? '🎓 learned (no API)' : String(m.model || '').split('/').pop(); loadLearned(); } break;
      case 'error': toast('⚠ ' + (m.message || 'error')); break;
      case 'status': setConn('wait', m.message || '…'); setTimeout(() => setConn('on', 'live'), 2500); break;
      case 'net':
        if (m.online) { setConn('on', 'live'); toast(hiUI() ? '✓ इंटरनेट वापस आ गया' : '✓ Back online'); }
        else { setConn('wait', hiUI() ? 'इंटरनेट नहीं' : 'offline'); toast(hiUI() ? 'इंटरनेट नहीं है — दोबारा जोड़ रही हूँ…' : 'No internet — reconnecting…'); }
        break;
    }
  }
  const LOGGED = new Set(['ready', 'user_turn', 'agent_text', 'danger', 'barge_in', 'metrics', 'turn_info', 'event', 'saved', 'error']);

  // ------------------------------------------------------------------ live visit view
  function setOrb(s) {
    if (muted && useMic && s !== 'speaking') s = 'muted';
    if (!useMic && s === 'listening') s = 'typed';
    $('orb').className = 'orb ' + s;
    $('orbState').textContent = t(s === 'typed' ? 'typed' : s);
    window.Mascot && Mascot.set($('mascot'), { idle: 'sleep', typed: 'listening' }[s] || s);
    $('bubble').classList.toggle('speaking', s === 'speaking');
  }
  function showYou(text, interim) {
    $('capYou').hidden = false; $('capYouText').textContent = interim ? text : '“' + text + '”';
    $('capYou').classList.toggle('interim', !!interim);
  }
  function showSakhi(text) {
    $('bubble').classList.toggle('alert', /PHC/.test(text) && /खतरे|danger|Attention|ध्यान/i.test(text));
    $('bubbleText').innerHTML = text.split(/(\s+)/).map((w, i) => (w.trim() ? `<span class="w" style="animation-delay:${Math.min(i, 60) * 28}ms">${esc(w)}</span>` : w)).join('');
  }
  let pipeTimer;
  function pipe(id, alert) {
    ['pSTT', 'pDNG', 'pLLM', 'pTTS'].forEach((p) => $(p).classList.toggle('active', p === id && !alert));
    if (alert) { $('pDNG').classList.add('alert'); clearTimeout(pipeTimer); pipeTimer = setTimeout(() => $('pDNG').classList.remove('alert'), 4000); }
  }

  const DANGER_FIELD = { MAT_HIGH_BP: 'bp', MAT_SEVERE_ANAEMIA: 'hb', NB_FEVER: 'temp', MAT_FEVER: 'temp', NB_COLD: 'temp',
    CH_FEVER: 'temp', NB_FAST_BREATHING: 'rr', NB_LOW_WEIGHT: 'weight' };
  let prevVals = {};
  function renderRecord(m) {
    const r = m ? m.record : { danger_signs: [], warnings: [] };
    const st = m ? m.state : 'collecting';
    // stepper + main button
    const order = ['collecting', 'confirming', 'saved'];
    $('stepper').querySelectorAll('li').forEach((li) => {
      const i = order.indexOf(li.dataset.s), cur = order.indexOf(st);
      li.className = i < cur || st === 'saved' ? 'done' : i === cur ? 'now' : '';
    });
    const yes = st === 'confirming';
    $('doneBtn').classList.toggle('yes', yes); $('doneBtn').classList.toggle('primary', !yes);
    $('doneBtn').querySelector('span').textContent = yes ? t('yes') : t('done');
    $('doneBtn').querySelector('.dico').innerHTML = `<svg class="ic"><use href="#${yes ? 'i-check' : 'i-stop'}"/></svg>`;

    // header
    const type = r.beneficiary_type || '';
    $('bName').textContent = r.beneficiary_name || (r.beneficiary_id ? (hiUI() ? 'लाभार्थी' : 'Beneficiary') : '—');
    $('bId').textContent = r.beneficiary_id || '—';
    const av = $('avatar'); av.className = 'avatar ' + type;
    av.textContent = r.beneficiary_name ? r.beneficiary_name.replace(/^Baby of /, '').split(/\s+/).map((w) => w[0]).slice(0, 2).join('') : ({ pregnant: '🤰', postpartum: '👩', newborn: '👶', child: '🧒' }[type] || '?');
    const ctx = r.context || {};
    const tags = [type && typeLabel(type), ctx.village, ctx.gestation_weeks != null ? `${ctx.gestation_weeks} ${hiUI() ? 'हफ़्ते' : 'weeks'}` : null,
      ctx.age_days != null ? `${ctx.age_days} ${hiUI() ? 'दिन' : 'days'}` : null].filter(Boolean);
    $('bMeta').innerHTML = tags.map((x) => `<span class="tag">${esc(x)}</span>`).join('') +
      (r.beneficiary_id && !r.registered ? `<span class="tag new">${t('newb')}</span>` : '');

    // tiles
    const bad = new Set((r.danger_signs || []).map((d) => DANGER_FIELD[d.id]).filter(Boolean));
    const temp = r.temperature_c != null ? `${r.temperature_c} °C` : r.temperature_f != null ? `${r.temperature_f} °F` : null;
    const next = r.next_visit_date ? fmtDate(r.next_visit_date) : r.next_visit_suggested ? fmtDate(r.next_visit_suggested) : null;
    const tiles = [
      ['visit_type', '🩺', r.visit_type],
      ['weight', '⚖️', r.weight_kg != null ? `${r.weight_kg} kg` : null],
      ['bp', '💓', r.bp_systolic ? `${r.bp_systolic}/${r.bp_diastolic ?? '?'}` : null],
      ['hb', '🩸', r.hb_g_dl != null ? `${r.hb_g_dl} g/dL` : null],
      ['temp', '🌡️', temp],
      ['rr', '🫁', r.respiratory_rate],
      ['ifa', '💊', r.ifa_tablets_given === false ? (hiUI() ? 'नहीं' : 'none') : r.ifa_tablets_given],
      ['vgiven', '💉', (r.vaccinations_given_today || []).join(', ') || null],
      ['next', '📅', next, !r.next_visit_date && r.next_visit_suggested],
      ['notes', '📝', r.notes, false, true],
    ];
    const box = $('fields'); box.innerHTML = '';
    tiles.forEach(([k, icon, v, sug, wide]) => {
      if (wide && v == null) return;
      const d = document.createElement('div');
      d.tabIndex = 0; d.setAttribute('role', 'button'); d.dataset.k = k;
      d.onclick = () => openEdit(k, r);
      d.onkeydown = (e) => { if (e.key === 'Enter') openEdit(k, r); };
      d.className = 'tile' + (v == null || v === '' ? ' empty' : '') + (bad.has(k) ? ' bad' : '') + (wide ? ' wide' : '');
      d.innerHTML = `<div class="tl"><span>${icon}</span>${esc(t(k))}${sug ? `<span class="sug">${esc(t('suggested'))}</span>` : ''}</div>
        <div class="tv">${v == null || v === '' ? '—' : esc(v)}</div>`;
      const key = String(v);
      if (v != null && prevVals[k] !== undefined && prevVals[k] !== key) { d.classList.add('flash', 'pop'); setTimeout(() => d.classList.remove('flash', 'pop'), 1500); }
      prevVals[k] = key;
      box.appendChild(d);
    });
    const due = r.vaccinations_due || [];
    $('vacc').hidden = !due.length;
    $('vacc').innerHTML = `<span class="muted">${esc(t('vdue'))}</span>` + due.map((v) => `<span class="vchip">💉 ${esc(v)}</span>`).join('');
    const FK = { weight_kg: 'weight', hb_g_dl: 'hb', temperature_c: 'temp', temperature_f: 'temp', respiratory_rate: 'rr',
      bp_systolic: 'bp', bp_diastolic: 'bp', ifa_tablets_given: 'ifa', bp: 'bp' };
    const checks = (r.checks || []).map((c) => {
      const name = t(FK[c.field] || c.field);
      if (c.kind === 'bp_order') return hiUI() ? `BP ${c.value}: ऊपर वाला नंबर आम तौर पर बड़ा होता है — जाँच लें` : `BP ${c.value}: the top number is usually the bigger one — please check`;
      return hiUI() ? `${name} ${c.value} असामान्य लग रहा है (आम तौर पर ${c.lo}–${c.hi}) — दोबारा जाँच लें`
        : `${name} ${c.value} looks unusual (usually ${c.lo}–${c.hi}) — please check again`;
    });
    $('warn').hidden = !checks.length;
    $('warn').innerHTML = checks.map((x) => `<div>⚠ ${esc(x)}</div>`).join('');

    // progress: required fields count double
    const filled = [r.beneficiary_id, r.beneficiary_id, r.visit_type, r.visit_type, r.weight_kg ?? r.bp_systolic ?? r.hb_g_dl ?? temp,
      next, st === 'saved' ? 1 : null].filter((x) => x != null && x !== '').length;
    $('progBar').style.width = `${Math.round((filled / 7) * 100)}%`;

    // danger
    const ds = r.danger_signs || [];
    $('card').classList.toggle('flagged', ds.length > 0);
    $('card').classList.toggle('saved', st === 'saved');
    const bar = $('dangerBar');
    bar.hidden = !ds.length;
    bar.innerHTML = `<span class="db-ico">🚨</span><div>${esc(t('danger'))}<small>${ds.map((d) => esc(signLabel(d))).join(' · ')}</small></div>`;
    const fresh = ds.filter((d) => !shownDanger.has(d.id));
    if (fresh.length) { fresh.forEach((d) => shownDanger.add(d.id)); showAlarm(ds); }
    if (!ds.length) shownDanger = new Set();
    if (!m || !r.beneficiary_id) prevVals = {};
  }
  function typeLabel(x) {
    return (hiUI() ? { pregnant: 'गर्भवती', postpartum: 'प्रसव के बाद', newborn: 'नवजात', child: 'बच्चा' }
      : { pregnant: 'pregnant', postpartum: 'postpartum', newborn: 'newborn', child: 'child' })[x] || x;
  }
  function signLabel(d) { return hiUI() ? (d.label_hi || d.label_en) : (d.label_en || d.label_hi); }

  function showAlarm(ds) {
    $('alarmTitle').textContent = hiUI() ? 'खतरे का संकेत' : 'Danger sign';
    $('alarmList').innerHTML = ds.map((d) => `<li><button class="unflag" data-unflag="${esc(d.id)}" title="${hiUI() ? 'गलत निशान हटाएँ' : 'Remove a wrong flag'}">${hiUI() ? 'गलत है?' : 'Wrong?'}</button>${esc(signLabel(d))}<small>${(d.source === 'llm' ? d.utterance : d.evidence) ? (hiUI() ? 'सुना: ' : 'heard: ') + '“' + esc(d.source === 'llm' ? d.utterance : d.evidence) + '” · ' : ''}${esc(d.guideline || '')}</small></li>`).join('');
    $('alarm').hidden = false;
    clearTimeout(showAlarm.tm); showAlarm.tm = setTimeout(() => { $('alarm').hidden = true; }, 12000);
  }
  $('alarmOk').onclick = () => { $('alarm').hidden = true; };
  $('alarmList').onclick = (e) => {
    const b = e.target.closest('[data-unflag]'); if (!b) return;
    clearTimeout(showAlarm.tm);
    if (!b.classList.contains('armed')) {  // two taps: a danger flag is never removed by accident
      b.classList.add('armed'); b.textContent = hiUI() ? 'हटाने के लिए फिर टैप करें' : 'Tap again to remove';
      setTimeout(() => { if (b.isConnected) { b.classList.remove('armed'); b.textContent = hiUI() ? 'गलत है?' : 'Wrong?'; } }, 4000);
      return;
    }
    send({ type: 'unflag', sign: b.dataset.unflag, reason: 'marked wrong on screen' });
    shownDanger.delete(b.dataset.unflag);
    b.closest('li').remove();
    toast(hiUI() ? 'निशान हटाया गया (वॉइस लॉग में दर्ज)' : 'Flag removed (recorded in the voice log)');
    if (!$('alarmList').children.length) $('alarm').hidden = true;
  };

  function renderTryChips() {
    const list = TRY[hiUI() ? 'hi' : 'en'];
    $('tryChips').innerHTML = '';
    list.forEach((s) => {
      const b = document.createElement('button'); b.className = 'chip-btn'; b.textContent = s;
      b.onclick = () => sayText(s, 'typed');
      $('tryChips').appendChild(b);
    });
  }
  function sayText(text, source) {
    if (send({ type: 'text', text, source })) { showYou(text); }
  }

  // ------------------------------------------------------------------ KPIs
  function updateKpis() {
    const ms = entries.filter((e) => e.type === 'metrics' && e.turn > 0).map((e) => e.ttfa_ms);
    $('kTurns').textContent = entries.filter((e) => e.type === 'user_turn').length;
    $('kBarge').textContent = entries.filter((e) => e.type === 'barge_in' && e.turn > 0).length;
    const mets = entries.filter((e) => e.type === 'metrics' && e.turn > 0);
    const local = mets.filter((e) => e.model === 'local' || e.model === 'learned').length;
    $('savedCalls').hidden = !mets.length;
    $('localN').textContent = local; $('turnN').textContent = mets.length; $('callsSaved').textContent = local;
    if (!ms.length) return;
    const last = ms[ms.length - 1];
    const med = [...ms].sort((a, b) => a - b)[Math.floor(ms.length / 2)];
    $('kLast').textContent = `${last}`; $('kMed').textContent = `${med}`;
    const lb = $('latBadge'); lb.textContent = `⏱ ${last} ms`;
    lb.className = 'lat-badge ' + (last < 1000 ? 'good' : last < 1800 ? 'mid' : 'bad');
    const max = Math.max(1500, ...ms);
    $('sparks').innerHTML = ms.slice(-24).map((v) => `<i class="${v < 1000 ? '' : v < 1800 ? 'mid' : 'bad'}" style="height:${Math.max(6, (v / max) * 100)}%" title="${v} ms"></i>`).join('');
  }

  async function loadLearned() {
    try { const st = await (await fetch('/api/learned')).json(); $('learnedN').textContent = st.active; } catch { /* offline */ }
  }

  // ------------------------------------------------------------------ voice log
  function buildTurns(list) {
    const turns = new Map(); let cur = 0; let who = { name: null, id: null }; let resetNext = false;
    const T = (id) => { if (!turns.has(id)) turns.set(id, { id, lines: [], dangers: [] }); return turns.get(id); };
    for (const e of list) {
      switch (e.type) {
        case 'ready': T(0).lines.push({ k: 'ev', kind: 'session', text: `session ${e.session_id || ''} · ${e.lang} · ${e.llm || ''}` }); break;
        case 'user_turn':
          if (resetNext) { who = { name: null, id: null }; resetNext = false; }
          cur = e.turn; Object.assign(T(cur), { user: e.text, source: e.source, merged: e.merged_from, lang: e.detected_lang, stt: e.stt_latency_ms, t: e.t, who: { ...who } }); break;
        case 'agent_text': { const tt = T(e.turn); tt.lines.push({ k: tt.dangers.length && /PHC/.test(e.text) && !tt.escSaid ? 'esc' : 'sakhi', text: e.text }); if (tt.dangers.length && /PHC/.test(e.text)) tt.escSaid = true; break; }
        case 'danger': { const tt = T(cur); tt.dangers.push(...e.signs); tt.escSaid = false; tt.lines.push({ k: 'ev', kind: 'danger', text: e.signs.map((s) => (signInfo[s] ? (hiUI() ? signInfo[s].hi : signInfo[s].en) : s)).join(' · ') }); break; }
        case 'event': T(e.turn || cur).lines.push({ k: 'ev', kind: e.kind, text: e.text }); break;
        case 'barge_in': if (e.turn > 0) { T(e.turn).cut = true; T(e.turn).lines.push({ k: 'ev', kind: 'barge', text: 'Sakhi stopped talking: ASHA started speaking (barge-in)' }); } break;
        case 'turn_info': {
          const c = e.record || e.changed || {};  // full record snapshot (newer logs) has the register name too
          if (c.beneficiary_name || c.beneficiary_id) { who = { name: c.beneficiary_name || who.name, id: c.beneficiary_id || who.id }; T(e.turn).who = { ...who }; }
          T(e.turn).info = e; break;
        }
        case 'metrics': T(e.turn).m = e; break;
        case 'saved': resetNext = true; T(cur).saved = true; T(cur).lines.push({ k: 'ev', kind: 'saved', text: `record ${e.record_id || (e.record && e.record.record_id) || ''} saved` }); break;
        case 'error': T(cur).lines.push({ k: 'ev', kind: 'busy', text: e.message || 'error' }); break;
      }
    }
    return [...turns.values()].sort((a, b) => a.id - b.id);
  }
  const EV_LABEL = { danger: 'danger', merge: 'merged', hold: 'waiting', echo: 'echo', noise: 'noise', validate: 'check', guard: 'guard',
    readback: 'read-back', barge: 'barge-in', saved: 'saved', session: 'session', busy: 'busy', edit: 'edited', local: 'local', learned: 'learned', offline: 'offline', online: 'online', unflag: 'flag removed' };
  function turnHtml(tt, compact) {
    if (tt.id === 0 && compact) return '';
    const src = tt.source === 'voice' ? '🎙 voice' : tt.source === 'demo' ? '▶ demo' : tt.source ? '⌨ typed' : '';
    const info = tt.info || {}; const m = tt.m || {};
    const changed = Object.entries(info.changed || {}).filter(([, v]) => v != null && v !== '' && !(Array.isArray(v) && !v.length));
    let h = `<div class="turn${tt.dangers.length ? ' danger' : ''}${tt.cut ? ' cut' : ''}">
      <div class="t-head"><span class="t-num">${tt.id === 0 ? 'Start' : '#' + tt.id}</span>
      ${src ? `<span class="badge ${tt.source !== 'voice' ? 'typed' : ''}">${src}</span>` : ''}
      ${tt.lang && !compact ? `<span class="badge">${esc(tt.lang)}</span>` : ''}
      ${info.intent ? `<span class="badge intent">${esc(info.intent)}</span>` : ''}
      ${tt.who && (tt.who.name || tt.who.id) ? `<span class="badge patient">👤 ${esc(tt.who.name || t('patient'))}${tt.who.id ? ' · ' + esc(tt.who.id) : ''}</span>` : ''}
      ${tt.saved ? '<span class="badge saved">saved ✓</span>' : ''}
      ${m.model === 'learned' ? '<span class="badge learned">🎓 learned · no LLM</span>' : m.model === 'local' ? '<span class="badge local">⚡ local · no LLM</span>' : m.model && !compact ? `<span class="badge">${esc(String(m.model).split('/').pop())}</span>` : ''}</div>`;
    if (tt.user) h += `<div class="msg user"><span class="mi" title="${esc(t('you_label'))}">🗣️</span><span class="mt"><small class="who-lbl">${esc(t('you_label'))}</small>${esc(tt.user)}</span></div>`;
    for (const ln of tt.lines) {
      if (ln.k === 'ev') {
        if (compact && ['session', 'noise'].includes(ln.kind)) continue;
        h += `<div class="ev ${ln.kind}"><b>${esc(EV_LABEL[ln.kind] || ln.kind)}</b><span>${esc(ln.text)}</span></div>`;
      } else h += `<div class="msg ${ln.k}"><span class="mi">${ln.k === 'esc' ? '🚨' : '🤖'}</span><span class="mt">${esc(ln.text)}</span></div>`;
    }
    if (changed.length) h += `<div class="changes">${changed.map(([k, v]) => `<span class="chg">${esc(k)} = ${esc(Array.isArray(v) ? v.join(', ') : v)}</span>`).join('')}</div>`;
    if (info.unclear && !compact) h += `<div class="ev"><b>unclear</b><span>${esc(info.unclear)}</span></div>`;
    if (m.ttfa_ms != null) {
      const stt = m.stt_latency_ms || 0, llmMs = m.ttft_ms || 0, rest = Math.max(0, m.ttfa_ms - llmMs), scale = 3000;
      const pct = (x) => `${Math.min(100, (x / scale) * 100)}%`;
      h += `<div class="timing"><div class="tbar"><i class="s" style="width:${pct(stt)}"></i><i class="l" style="width:${pct(llmMs)}"></i><i class="t" style="width:${pct(rest)}"></i></div>
        <div class="tnums"><span>first audio <b>${m.ttfa_ms} ms</b></span>${!compact ? `${tt.source === 'voice' ? `<span>STT ${stt} ms</span>` : ''}<span>${m.model === 'local' || m.model === 'learned' ? `${m.model} model ${m.local_ms} ms` : `LLM 1st token ${llmMs} ms`}</span><span>incl. end-of-speech wait ${m.ttfa_from_speech_end_ms} ms</span>` : ''}</div></div>`;
    }
    if (!compact && tt.id > 0) {
      const key = `${currentLogSession()}:${tt.id}`;
      if (reported.has(key)) h += '<div class="rep-done">⚑ reported, thank you. It is in the bug report</div>';
      else if (openReport === tt.id) h += `<form class="rep-form" data-turn="${tt.id}">
          <input name="problem" placeholder="What went wrong? e.g. Hb heard as 310, it was 10" required>
          <input name="expected" placeholder="What should Sakhi have done? (optional)">
          <div class="rep-actions"><button type="button" class="btn small" data-cancel>Cancel</button><button class="btn small primary">⚑ Save report</button></div>
        </form>`;
      else h += `<button class="rep-btn" data-report="${tt.id}">⚑ Report a problem</button>`;
    }
    return h + '</div>';
  }
  function currentLogSession() { return viewEntries ? $('logPick').value : liveSession; }
  let logPending = false;
  function scheduleLog() { if (!logPending) { logPending = true; requestAnimationFrame(() => { logPending = false; renderLog(); }); } }
  function renderLog() {
    const live = buildTurns(entries);
    const mini = $('miniLog');
    const miniTurns = live.filter((x) => x.id > 0).slice(-6);
    mini.innerHTML = miniTurns.length ? miniTurns.map((x) => turnHtml(x, true)).join('') : '<div class="empty-state">Turns appear here as she speaks</div>';
    mini.scrollTop = mini.scrollHeight;
    const n = live.filter((x) => x.id > 0).length;
    $('logCount').hidden = !n; $('logCount').textContent = n;
    if (viewEntries) return;
    if ($('fullLog').contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return; // don't wipe a report being typed
    const full = $('fullLog');
    const atBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
    full.innerHTML = live.length ? live.map((x) => turnHtml(x, false)).join('') : '<div class="empty-state">No turns yet</div>';
    if (atBottom && $('view-log').classList.contains('active')) window.scrollTo(0, document.body.scrollHeight);
  }
  async function loadSessions() {
    try {
      const list = await (await fetch('/api/voicelogs')).json();
      const pick = $('logPick'); const v = pick.value;
      pick.innerHTML = '<option value="live">● Live session</option>' + list.map((s) => `<option value="${esc(s.id)}">${esc(s.id)} · ${s.turns} turns${s.dangers ? ' · 🚨' + s.dangers : ''}${s.saves ? ' · 💾' + s.saves : ''}</option>`).join('');
      pick.value = [...pick.options].some((o) => o.value === v) ? v : 'live';
    } catch { /* offline */ }
  }
  function rerenderLog() { if (viewEntries) $('fullLog').innerHTML = buildTurns(viewEntries).map((x) => turnHtml(x, false)).join(''); else renderLog(); }
  $('fullLog').addEventListener('click', (e) => {
    const r = e.target.closest('[data-report]');
    if (r) { openReport = +r.dataset.report; rerenderLog(); setTimeout(() => $('fullLog').querySelector('.rep-form input')?.focus(), 30); return; }
    if (e.target.closest('[data-cancel]')) { openReport = null; rerenderLog(); }
  });
  $('fullLog').addEventListener('submit', async (e) => {
    const f = e.target.closest('.rep-form'); if (!f) return;
    e.preventDefault();
    const sid = currentLogSession(); if (!sid) { toast('No session yet'); return; }
    const body = { session: sid, turn: +f.dataset.turn, problem: f.problem.value.trim(), expected: f.expected.value.trim() };
    try {
      const res = await fetch('/api/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!res.ok) throw new Error(res.status);
      reported.add(`${sid}:${body.turn}`); openReport = null; document.activeElement?.blur(); rerenderLog(); loadDsStats(); toast('⚑ Report saved');
    } catch { toast('Could not save the report'); }
  });
  async function loadDsStats() {
    try {
      const [st, fb] = await Promise.all([(await fetch('/api/dataset/summary')).json(), (await fetch('/api/feedback')).json()]);
      reported = new Set(fb.map((x) => `${x.session}:${x.turn}`));
      const h = st.handled_by || {};
      $('dsStats').innerHTML = [['sessions', st.sessions], ['turns', st.turns], ['understood locally', Math.round((st.local_share || 0) * 100) + '%'],
        ['needed AI', (h.llm || 0)], ['failed', (h.failed || 0)], ['reports', st.reports]].map(([k, v]) => `<span><b>${v}</b> ${k}</span>`).join('');
    } catch { /* offline */ }
  }
  $('logPick').onchange = async () => {
    const v = $('logPick').value;
    if (v === 'live') { viewEntries = null; renderLog(); return; }
    viewEntries = await (await fetch('/api/voicelogs/' + encodeURIComponent(v))).json();
    $('fullLog').innerHTML = buildTurns(viewEntries).map((x) => turnHtml(x, false)).join('');
  };
  $('logExport').onclick = () => {
    const data = viewEntries || entries;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'sakhi-voice-log.json'; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };

  // ------------------------------------------------------------------ follow-ups / stats
  async function loadFollowups() {
    try {
      const [items, st] = await Promise.all([(await fetch('/api/followups')).json(), (await fetch('/api/stats')).json()]);
      $('stats').innerHTML = [
        ['visits today', st.visits_today, 'green'], ['PHC referrals today', st.referrals_today, st.referrals_today ? 'red' : ''],
        ['urgent follow-ups', st.urgent_followups, st.urgent_followups ? 'red' : ''], ['vaccines due', st.vaccines_due, 'amber'],
        ['visits total', st.visits_total, ''], ['in register', st.register_size, ''],
      ].map(([k, v, c]) => `<div class="stat ${c}"><small>${k}</small><b>${v}</b></div>`).join('');
      const groups = { urgent: [], high: [], normal: [], check: [] };
      items.forEach((i) => (groups[i.priority] || groups.check).push(i));
      const today = new Date().toISOString().slice(0, 10);
      const ico = { urgent: '🚨', high: '💉', normal: '🏠', check: '🔎' };
      $('fuList').innerHTML = items.length ? Object.entries(groups).filter(([, v]) => v.length).map(([g, v]) => `<h3>${ico[g]} ${esc(t(g))} · ${v.length}</h3>` +
        v.map((i) => `<div class="fu ${g}"><div class="fu-body"><b>${esc(i.beneficiary_name || (hiUI() ? 'नया लाभार्थी' : 'New beneficiary'))}</b> <span class="muted small">ID ${esc(i.beneficiary_id || '—')}</span>
          <div class="fu-act">${esc(i.action)}</div></div><div class="fu-due ${i.due && i.due < today ? 'over' : ''}">${i.due ? esc(fmtDate(i.due)) : ''}</div></div>`).join('')).join('')
        : `<div class="empty-state">${esc(t('none'))}</div>`;
      const urgent = st.urgent_followups;
      $('fuCount').hidden = !urgent; $('fuCount').textContent = urgent;
    } catch { /* offline */ }
  }
  $('fuRefresh').onclick = loadFollowups;

  // ------------------------------------------------------------------ register
  let registry = [];
  async function loadRegister() {
    try { registry = await (await fetch('/api/beneficiaries')).json(); } catch { return; }
    renderRegister();
  }
  function renderRegister() {
    const q = $('regSearch').value.trim().toLowerCase();
    const list = registry.filter((b) => !q || [b.id, b.name, b.name_local, b.village, b.type].join(' ').toLowerCase().includes(q));
    $('regList').innerHTML = list.map((b) => {
      const c = b.context || {};
      const sub = [typeLabel(b.type), b.village, c.gestation_weeks != null ? `${c.gestation_weeks} wk` : null, c.age_days != null ? `${c.age_days} days old` : null].filter(Boolean).join(' · ');
      const due = (b.vaccinations_due || []).map((v) => `<span class="vchip">💉 ${esc(v)}</span>`).join('');
      return `<div class="reg"><div class="reg-top"><div class="avatar ${b.type}">${esc(b.name.replace(/^Baby of /, '').split(/\s+/).map((w) => w[0]).slice(0, 2).join(''))}</div>
        <div style="flex:1;min-width:0"><div class="reg-name">${esc(b.name)} <span class="muted small">${esc(b.name_local || '')}</span></div><div class="reg-sub">ID ${esc(b.id)} · ${esc(sub)}</div></div>
        ${b.flagged ? '<span class="tag" style="background:var(--danger-bg);color:var(--danger)">🚨 referred</span>' : ''}</div>
        <div class="reg-due">${due}${b.next_visit_suggested ? `<span class="tag">📅 ${esc(fmtDate(b.next_visit_suggested))}</span>` : ''}${b.last_visit ? `<span class="tag">last ${esc(b.last_visit.slice(0, 10))}</span>` : ''}</div>
        <button class="btn small" data-start="${esc(b.id)}">▶ Start visit · ${esc(b.visit_type || 'visit')}</button></div>`;
    }).join('') || '<div class="empty-state">No match</div>';
  }
  $('regSearch').oninput = renderRegister;
  $('regList').onclick = (e) => {
    const id = e.target.closest('[data-start]')?.dataset.start; if (!id) return;
    const b = registry.find((x) => x.id === id);
    send({ type: 'new_visit' }); prevVals = {};
    setTimeout(() => sayText(hiUI() ? `${b.name}, ID ${b.id}, ${b.visit_type || ''} visit` : `${b.visit_type || ''} visit for ${b.name}, ID ${b.id}`, 'typed'), 250);
    go('visit');
  };

  // ------------------------------------------------------------------ records
  async function loadRecords() {
    let recs = [];
    try { recs = await (await fetch('/api/records')).json(); } catch { return; }
    const F = [['visit_type', 'Visit'], ['visit_date', 'Date'], ['weight_kg', 'Weight kg'], ['bp', 'BP'], ['hb_g_dl', 'Hb g/dL'], ['temp', 'Temperature'],
      ['respiratory_rate', 'Breaths/min'], ['ifa_tablets_given', 'IFA'], ['vaccinations_given_today', 'Vaccines today'], ['vaccinations_due', 'Vaccines due'],
      ['next', 'Next visit'], ['notes', 'Notes'], ['asha_id', 'ASHA'], ['saved_at', 'Saved']];
    $('recList').innerHTML = recs.length ? recs.map((r) => {
      const v = { ...r, bp: r.bp_systolic ? `${r.bp_systolic}/${r.bp_diastolic ?? '?'}` : null,
        temp: r.temperature_c != null ? `${r.temperature_c} °C` : r.temperature_f != null ? `${r.temperature_f} °F` : null,
        next: r.next_visit_date || (r.next_visit_suggested ? r.next_visit_suggested + ' (suggested)' : null) };
      const rows = F.map(([k, l]) => [l, Array.isArray(v[k]) ? v[k].join(', ') : v[k]]).filter(([, x]) => x != null && x !== '');
      const ds = (r.danger_signs || []);
      return `<details class="rec ${r.referral_required ? 'flag' : ''}"><summary><div class="avatar ${r.beneficiary_type || ''}">${r.referral_required ? '🚨' : '✓'}</div>
        <div style="flex:1;min-width:0"><b>${esc(r.beneficiary_name || 'New beneficiary')}</b> <span class="muted small">ID ${esc(r.beneficiary_id || '—')}</span>
        <div class="muted small">${esc(r.visit_type || '')} · ${esc((r.saved_at || '').replace('T', ' ').slice(0, 16))}</div></div></summary>
        ${ds.length ? `<div class="warn" style="background:var(--danger-bg);color:var(--danger)">🚨 ${ds.map((d) => esc(signLabel(d))).join(' · ')}</div>` : ''}
        <dl>${rows.map(([l, x]) => `<dt>${esc(l)}</dt><dd>${esc(x)}</dd>`).join('')}</dl>
        <a class="btn" style="margin-top:8px;display:inline-block;text-decoration:none" href="/report/${encodeURIComponent(r.record_id)}?print=1" target="_blank" rel="noopener">📄 PDF report</a></details>`;
    }).join('') : '<div class="empty-state">No saved visits yet</div>';
  }

  // ------------------------------------------------------------------ demo runner
  let demoRunning = false;
  $('demoBtn').onclick = () => {
    if (demoRunning) { demoRunning = false; toast('Demo stopped'); return; }
    const list = DEMOS[hiUI() ? 'hi' : 'en'];
    $('demoList').innerHTML = list.map((d, i) => `<button class="demo-item" data-i="${i}"><b>${esc(d.title)}</b><small>${esc(d.sub)} · ${d.lines.length} lines</small></button>`).join('');
    $('demoSheet').hidden = false;
  };
  $('demoClose').onclick = () => { $('demoSheet').hidden = true; };
  $('demoSheet').onclick = (e) => { if (e.target === $('demoSheet')) $('demoSheet').hidden = true; };
  $('demoList').onclick = (e) => {
    const i = e.target.closest('[data-i]')?.dataset.i; if (i == null) return;
    $('demoSheet').hidden = true; runDemo(DEMOS[hiUI() ? 'hi' : 'en'][+i]);
  };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function waitIdle(afterTurn) {
    const t0 = Date.now();
    while (demoRunning && Date.now() - t0 < 30000) {
      const answered = lastAgentTurn >= afterTurn && lastUserTurn >= afterTurn;
      if (answered && !sources.length && $('alarm').hidden) { await sleep(900); if (!sources.length) return; }
      await sleep(150);
    }
  }
  async function runDemo(d) {
    demoRunning = true; go('visit');
    const banner = document.createElement('div'); banner.className = 'demo-banner'; banner.textContent = '▶ DEMO · ' + d.title;
    document.body.appendChild(banner);
    $('demoBtn').innerHTML = '<svg class="ic"><use href="#i-stop"/></svg> <span class="lbl">Stop</span>'; document.body.classList.add('demo-on');
    send({ type: 'new_visit' }); prevVals = {}; await sleep(400);
    for (const line of d.lines) {
      if (!demoRunning) break;
      const before = lastUserTurn;
      sayText(line, 'demo');
      const t0 = Date.now(); while (lastUserTurn === before && Date.now() - t0 < 4000) await sleep(50);
      await waitIdle(lastUserTurn);
    }
    demoRunning = false; banner.remove(); $('demoBtn').innerHTML = '<svg class="ic"><use href="#i-play"/></svg> <span class="lbl">Demo</span>'; document.body.classList.remove('demo-on');
  }

  // ------------------------------------------------------------------ navigation + controls
  function go(view) {
    document.querySelectorAll('.tab').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
    document.querySelectorAll('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + view));
    if (view === 'follow') loadFollowups();
    if (view === 'register') loadRegister();
    if (view === 'records') loadRecords();
    if (view === 'log') { loadSessions(); loadDsStats(); openReport = null; renderLog(); }
    window.scrollTo(0, 0);
  }
  document.querySelectorAll('.tab').forEach((b) => { b.onclick = () => go(b.dataset.view); });
  document.querySelectorAll('[data-goto]').forEach((b) => { b.onclick = () => go(b.dataset.goto); });

  $('doneBtn').onclick = () => {
    if (state === 'confirming') sayText(lang === 'en-IN' ? 'Yes, correct' : 'हाँ, सही है', 'typed');
    else if (useMic) send({ type: 'commit' });
    else { $('kbForm').hidden = false; $('kbInput').focus(); }
  };
  $('newBtn').onclick = () => { prevVals = {}; shownDanger = new Set(); send({ type: 'new_visit' }); toast(hiUI() ? 'नई विज़िट शुरू' : 'New visit started'); };
  $('kbBtn').onclick = () => { $('kbForm').hidden = !$('kbForm').hidden; if (!$('kbForm').hidden) $('kbInput').focus(); };
  $('kbForm').onsubmit = (e) => {
    e.preventDefault();
    const v = $('kbInput').value.trim(); if (!v) return;
    sayText(v, 'typed'); $('kbInput').value = '';
  };
  function toggleMute() {
    if (!useMic) return;
    muted = !muted; $('muteBtn').setAttribute('aria-pressed', String(muted));
    $('muteBtn').innerHTML = `<svg class="ic"><use href="#${muted ? 'i-micoff' : 'i-mic'}"/></svg>`;
    setOrb(sources.length ? 'speaking' : 'listening'); toast(muted ? t('muted') : t('listening'));
  }
  $('muteBtn').onclick = toggleMute;
  $('orb').onclick = toggleMute;
  document.addEventListener('visibilitychange', () => { if (!document.hidden && playCtx?.state === 'suspended' && !sources.length) playCtx.resume(); });

  // ------------------------------------------------------------------ tap-to-edit
  const EDIT = {
    visit_type: { f: 'visit_type', chips: ['ANC', 'PNC', 'HBNC', 'Immunisation', 'Other'], help: 'Visit type' },
    weight: { f: 'weight_kg', mode: 'decimal', help: 'Weight in kg, e.g. 2.8 or 56' },
    bp: { f: 'bp', mode: 'numeric', help: 'Top / bottom, e.g. 120/80', chips: ['110/70', '120/80', '140/90'] },
    hb: { f: 'hb_g_dl', mode: 'decimal', help: 'Haemoglobin in g/dL, e.g. 10.5' },
    temp: { f: 'temperature', mode: 'decimal', help: 'Above 50 is read as °F, otherwise °C', chips: ['98.6', '37'] },
    rr: { f: 'respiratory_rate', mode: 'numeric', help: 'Breaths per minute' },
    ifa: { f: 'ifa_tablets_given', mode: 'numeric', help: 'Number of IFA tablets given (0 if none)', chips: ['0', '30', '60', '100'] },
    vgiven: { f: 'vaccinations_given_today', help: 'Comma separated', chips: ['Td-1', 'Td-2', 'BCG', 'OPV-0', 'Hep-B', 'Penta-1', 'OPV-1', 'RVV-1', 'MR-1'] },
    next: { f: 'next_visit_date', type: 'date', help: 'Date of the next home visit' },
    notes: { f: 'notes', help: 'Short note' },
    id: { f: 'beneficiary_id', mode: 'numeric', help: 'Beneficiary ID from the register' },
  };
  let editKey = null;
  function openEdit(k, r) {
    if (state === 'saved') { toast(hiUI() ? 'सेव हो चुका है: नई विज़िट शुरू करें' : 'Already saved: start a new visit'); return; }
    const cfg = EDIT[k]; if (!cfg) return;
    editKey = k;
    const cur = {
      visit_type: r.visit_type, weight: r.weight_kg, bp: r.bp_systolic ? `${r.bp_systolic}/${r.bp_diastolic ?? ''}` : '',
      hb: r.hb_g_dl, temp: r.temperature_f ?? r.temperature_c, rr: r.respiratory_rate, ifa: r.ifa_tablets_given,
      vgiven: (r.vaccinations_given_today || []).join(', '), next: r.next_visit_date || r.next_visit_suggested || '', notes: r.notes, id: r.beneficiary_id,
    }[k];
    $('editTitle').textContent = '✎ ' + (k === 'id' ? 'ID' : t(k));
    $('editHelp').textContent = cfg.help + (state === 'confirming' ? ' · Sakhi will read the record back again.' : '');
    const inp = $('editInput');
    inp.type = cfg.type || 'text'; inp.inputMode = cfg.mode || 'text';
    inp.value = cur ?? '';
    $('editChips').innerHTML = (cfg.chips || []).map((c) => `<button type="button" class="chip-btn" data-v="${esc(c)}">${esc(c)}</button>`).join('');
    $('editSheet').hidden = false;
    setTimeout(() => { inp.focus(); inp.select?.(); }, 50);
  }
  $('editChips').onclick = (e) => {
    const v = e.target.closest('[data-v]')?.dataset.v; if (v == null) return;
    const inp = $('editInput');
    if (editKey === 'vgiven' && inp.value.trim()) inp.value = inp.value.replace(/[,\s]*$/, '') + ', ' + v; else inp.value = v;
    inp.focus();
  };
  function closeEdit() { $('editSheet').hidden = true; editKey = null; }
  $('editCancel').onclick = closeEdit;
  $('editClear').onclick = () => { if (editKey) send({ type: 'edit', field: EDIT[editKey].f, value: '' }); closeEdit(); };
  $('editForm').onsubmit = (e) => {
    e.preventDefault(); if (!editKey) return;
    if (send({ type: 'edit', field: EDIT[editKey].f, value: $('editInput').value.trim() })) toast('✓ ' + $('editTitle').textContent.replace('✎ ', ''));
    closeEdit();
  };
  $('editSheet').onclick = (e) => { if (e.target === $('editSheet')) closeEdit(); };
  document.querySelector('.rc-id').onclick = () => lastRecord && openEdit('id', lastRecord.record);
  document.querySelector('.rc-id').style.cursor = 'pointer';
  $('dangerBar').onclick = () => lastRecord && showAlarm(lastRecord.record.danger_signs || []);

  // ------------------------------------------------------------------ saved celebration
  function showSaved(m) {
    const r = m.record || {};
    $('savedTitle').textContent = t('saved_t');
    $('savedWho').textContent = [r.beneficiary_name || (hiUI() ? 'नया लाभार्थी' : 'New beneficiary'), r.beneficiary_id && 'ID ' + r.beneficiary_id, r.visit_type].filter(Boolean).join(' · ');
    const ico = { urgent: '🚨', high: '💉', normal: '📅', check: '🔎' };
    $('savedFu').innerHTML = (m.followups || []).map((f) => `<li class="${esc(f.priority)}">${ico[f.priority] || '•'} ${esc(f.action)}${f.due ? ` · ${esc(fmtDate(f.due))}` : ''}</li>`).join('')
      || `<li>${esc(t('none'))}</li>`;
    $('savedPdf').dataset.rid = r.record_id || '';
    $('savedLog').textContent = '🗒️ ' + t('tab_log');
    $('savedPdf').textContent = '📄 ' + (hiUI() ? 'PDF रिपोर्ट' : 'PDF report');
    $('savedNext').textContent = '＋ ' + t('next_visit_btn');
    $('savedSheet').hidden = false;
    if (!(r.danger_signs || []).length) confetti();
    clearTimeout(showSaved.tm); showSaved.tm = setTimeout(() => { $('savedSheet').hidden = true; }, 9000);
  }
  function confetti() {
    const colors = ['#b4462b', '#1d7a4c', '#e0b324', '#2b5fb4', '#d8673f'];
    for (let i = 0; i < 40; i++) {
      const c = document.createElement('i'); c.className = 'confetti';
      c.style.left = Math.random() * 100 + 'vw'; c.style.background = colors[i % colors.length];
      c.style.animationDuration = 1.6 + Math.random() * 1.4 + 's'; c.style.animationDelay = Math.random() * 0.4 + 's';
      document.body.appendChild(c); setTimeout(() => c.remove(), 3600);
    }
  }
  $('savedLog').onclick = () => { $('savedSheet').hidden = true; go('log'); };
  $('savedPdf').onclick = () => { const rid = $('savedPdf').dataset.rid; clearTimeout(showSaved.tm); if (rid) window.open('/report/' + encodeURIComponent(rid) + '?print=1', '_blank'); };
  $('savedNext').onclick = () => { $('savedSheet').hidden = true; $('newBtn').click(); };
  $('savedSheet').onclick = (e) => { if (e.target === $('savedSheet')) $('savedSheet').hidden = true; };

  // ------------------------------------------------------------------ help + keyboard
  $('helpBtn').onclick = () => { $('helpSheet').hidden = false; };
  $('helpClose').onclick = () => { $('helpSheet').hidden = true; };
  $('helpSheet').onclick = (e) => { if (e.target === $('helpSheet')) $('helpSheet').hidden = true; };
  document.addEventListener('keydown', (e) => {
    if ($('app').hidden || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'Escape') { ['editSheet', 'savedSheet', 'helpSheet', 'demoSheet', 'alarm'].forEach((id) => { $(id).hidden = true; }); return; }
    if (e.target.closest('input, select, textarea') || [...document.querySelectorAll('.sheet, .alarm')].some((x) => !x.hidden)) return;
    const views = ['visit', 'log', 'follow', 'register', 'records'];
    if (e.key === ' ') { e.preventDefault(); $('doneBtn').click(); }
    else if (e.key === 'm' || e.key === 'M') toggleMute();
    else if (e.key === 'n' || e.key === 'N') $('newBtn').click();
    else if (e.key === 'd' || e.key === 'D') $('demoBtn').click();
    else if (e.key === 't' || e.key === 'T') { e.preventDefault(); $('kbForm').hidden = false; $('kbInput').focus(); }
    else if (e.key === '?') $('helpSheet').hidden = false;
    else if (/^[1-5]$/.test(e.key)) go(views[+e.key - 1]);
  });

  // ------------------------------------------------------------------ utils
  function fmtDate(iso) {
    try { return new Date(iso + 'T00:00:00').toLocaleDateString(lang, { day: 'numeric', month: 'short', weekday: 'short' }); } catch { return iso; }
  }
  function esc(s) { return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function toast(msg) {
    const el = $('toast'); el.textContent = msg; el.hidden = false;
    clearTimeout(toast.tm); toast.tm = setTimeout(() => { el.hidden = true; }, 2200);
  }
})();
