// Sakhi mascot: an original SVG character — a community health worker in a mint uniform with a voice headset.
// Usage: <div data-mascot></div> is filled automatically; Mascot.set(el, state); Mascot.flag(el, mood, ms); Mascot.mouth(el, 0..1).
// Pipeline states (replace each other): sleep | listening | hearing | thinking | speaking | muted
// Moods (overlays with their own timers): waking | waving | worried | happy
(() => {
  const SVG = (n) => `
<svg class="mascot" viewBox="0 0 160 160" role="img" aria-label="Sakhi">
  <defs>
    <linearGradient id="mTop${n}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--m-top, #7fd3bb)"/><stop offset="1" stop-color="var(--m-top2, #3fae90)"/></linearGradient>
    <radialGradient id="mSkin${n}" cx=".42" cy=".38" r=".75"><stop offset="0" stop-color="#e2a77e"/><stop offset=".7" stop-color="#c98a62"/><stop offset="1" stop-color="#b27652"/></radialGradient>
    <linearGradient id="mHair${n}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3b2a22"/><stop offset="1" stop-color="#1f1612"/></linearGradient>
    <clipPath id="mMouthClip${n}"><path class="m-mouth-shape-clip" d="M71 104 Q80 102 89 104 Q80 108 71 104 Z"/></clipPath>
  </defs>
  <g class="m-all">
    <path d="M22 162 C26 128 50 114 80 114 C110 114 134 128 138 162 Z" fill="url(#mTop${n})"/>
    <path d="M64 114 L80 134 L96 114" fill="#2f8f75" opacity=".55"/>
    <path d="M64 114 L80 134 L96 114" fill="none" stroke="#2a7f68" stroke-width="2" stroke-linejoin="round"/>
    <rect x="100" y="130" width="18" height="12" rx="3" fill="#fff" opacity=".95"/>
    <path d="M109 132.5 v7 M105.5 136 h7" stroke="#e0475b" stroke-width="2.4" stroke-linecap="round"/>
    <path d="M70 102 h20 v14 c0 5-20 5-20 0z" fill="#b27652"/>
    <g class="m-head">
      <path d="M40 78 C36 42 56 26 80 26 C106 26 124 42 120 80 C119 96 113 106 106 110 L54 110 C47 106 41 96 40 78 Z" fill="url(#mHair${n})"/>
      <ellipse cx="45" cy="84" rx="6" ry="8" fill="#c98a62"/><ellipse cx="115" cy="84" rx="6" ry="8" fill="#c98a62"/>
      <path d="M46 78 C46 52 60 40 80 40 C100 40 114 52 114 78 C114 100 99 116 80 116 C61 116 46 100 46 78 Z" fill="url(#mSkin${n})"/>
      <path d="M44 74 C42 46 60 30 82 31 C104 32 118 46 116 70 C110 56 100 48 88 47 C80 54 66 60 50 66 C47 68 45 71 44 74 Z" fill="url(#mHair${n})"/>
      <path d="M88 47 C96 50 106 56 112 66" stroke="#4a362b" stroke-width="1.6" fill="none" stroke-linecap="round" opacity=".7"/>
      <path class="m-brow m-brow-l" d="M57 68 Q64 63 71 66" stroke="#2a1d17" stroke-width="3" fill="none" stroke-linecap="round"/>
      <path class="m-brow m-brow-r" d="M89 66 Q96 63 103 68" stroke="#2a1d17" stroke-width="3" fill="none" stroke-linecap="round"/>
      <g class="m-eyes">
        <g class="m-eye m-eye-l"><ellipse cx="64" cy="79" rx="7" ry="7.6" fill="#fff"/><g class="m-iris"><circle cx="64" cy="79.6" r="4.7" fill="#5a3826"/><circle cx="64" cy="79.6" r="2.5" fill="#140d0a"/><circle cx="65.8" cy="77.6" r="1.5" fill="#fff"/></g>
          <path d="M56.8 76 Q64 70.5 71.2 76" stroke="#2a1d17" stroke-width="1.8" fill="none" stroke-linecap="round"/></g>
        <g class="m-eye m-eye-r"><ellipse cx="96" cy="79" rx="7" ry="7.6" fill="#fff"/><g class="m-iris"><circle cx="96" cy="79.6" r="4.7" fill="#5a3826"/><circle cx="96" cy="79.6" r="2.5" fill="#140d0a"/><circle cx="97.8" cy="77.6" r="1.5" fill="#fff"/></g>
          <path d="M88.8 76 Q96 70.5 103.2 76" stroke="#2a1d17" stroke-width="1.8" fill="none" stroke-linecap="round"/></g>
      </g>
      <g class="m-eyes-happy" fill="none" stroke="#2a1d17" stroke-width="3" stroke-linecap="round"><path d="M57 81 Q64 73 71 81"/><path d="M89 81 Q96 73 103 81"/></g>
      <g class="m-eyes-closed" fill="none" stroke="#2a1d17" stroke-width="2.6" stroke-linecap="round"><path d="M57 80 Q64 85 71 80"/><path d="M89 80 Q96 85 103 80"/></g>
      <path d="M80 84 Q76.5 93 79.5 95.5 Q82 96.5 84.5 94.5" stroke="#9c6343" stroke-width="1.8" fill="none" stroke-linecap="round"/>
      <ellipse class="m-cheek" cx="56" cy="93" rx="6.5" ry="4" fill="#ef8a7a" opacity=".35"/><ellipse class="m-cheek" cx="104" cy="93" rx="6.5" ry="4" fill="#ef8a7a" opacity=".35"/>
      <g class="m-mouth-g">
        <path class="m-mouth" d="M71 104 Q80 102 89 104 Q80 108 71 104 Z" fill="#5a1f24" stroke="#5a1f24" stroke-width="1.4" stroke-linejoin="round"/>
        <g clip-path="url(#mMouthClip${n})"><rect class="m-teeth" x="66" y="99" width="28" height="5.2" fill="#fff" opacity="0"/><ellipse class="m-tongue" cx="80" cy="112" rx="6" ry="4" fill="#e46a73" opacity="0"/></g>
      </g>
      <path class="m-frown" d="M71 108 Q80 101 89 108" stroke="#5a1f24" stroke-width="2.8" fill="none" stroke-linecap="round"/>
      <path class="m-grin" d="M68 101 Q80 100 92 101 Q90 116 80 116 Q70 116 68 101 Z" fill="#5a1f24"/>
      <path class="m-grin" d="M70 101.5 Q80 101 90 101.5 L89 105 Q80 106 71 105 Z" fill="#fff"/>
      <ellipse class="m-o" cx="80" cy="106" rx="4.6" ry="5.4" fill="#5a1f24"/>
      <path d="M44 80 C40 40 120 40 116 80" fill="none" stroke="#2b3236" stroke-width="5" stroke-linecap="round"/>
      <rect x="108" y="74" width="13" height="18" rx="6" fill="#2b3236"/>
      <path d="M112 90 C112 104 104 108 94 108" fill="none" stroke="#2b3236" stroke-width="3" stroke-linecap="round"/>
      <circle class="m-mic" cx="92.5" cy="108" r="4" fill="#86d9c2"/>
      <path class="m-sweat" d="M115 58 Q119 66 115 69 Q111 66 115 58 Z" fill="#9fd4ff" stroke="#6fb3e8" stroke-width="1"/>
    </g>
    <g class="m-chin"><path d="M44 162 C46 140 54 128 64 120" stroke="url(#mTop${n})" stroke-width="13" stroke-linecap="round" fill="none"/><ellipse cx="68" cy="116" rx="8" ry="7" fill="#c98a62"/></g>
    <g class="m-hand"><path d="M128 160 L140 112" stroke="url(#mTop${n})" stroke-width="13" stroke-linecap="round"/><g transform="translate(141 104)"><ellipse rx="9" ry="10" fill="#c98a62"/><path d="M-6 -8 v-6 M-2 -9 v-8 M2 -9 v-8 M6 -8 v-6" stroke="#c98a62" stroke-width="4" stroke-linecap="round"/></g></g>
    <g class="m-dots"><circle cx="112" cy="40" r="4"/><circle cx="121" cy="32" r="4"/><circle cx="129" cy="25" r="4"/></g>
    <g class="m-spark" fill="#ffd36e"><path d="M30 44 l3 7 7 3 -7 3 -3 7 -3 -7 -7 -3 7 -3z"/><path d="M128 40 l2 5 5 2 -5 2 -2 5 -2 -5 -5 -2 5 -2z"/></g>
    <g class="m-zz" fill="var(--ink-2, #b9b5ae)" font-family="system-ui, sans-serif" font-weight="800"><text x="116" y="44" font-size="14">z</text><text x="126" y="32" font-size="11">z</text></g>
    <g class="m-micoff"><circle cx="40" cy="132" r="12" fill="var(--danger, #f26d82)"/><rect x="37" y="124" width="6" height="10" rx="3" fill="#fff"/><path d="M33 140 L47 124" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/></g>
  </g>
</svg>`;

  let n = 0;
  function mount(el) {
    if (el.dataset.mascotReady) return;
    el.innerHTML = SVG(n++);
    el.dataset.mascotReady = '1';
    el.classList.add('mascot-host', 'm-listening');
    lookAround(el);
  }
  function lookAround(el) {  // idle saccades: glance around every few seconds like a real person
    const iris = el.querySelectorAll('.m-iris');
    const tick = () => {
      if (!el.isConnected) return;
      const calm = el.classList.contains('m-listening') && !el.classList.contains('m-worried');
      if (calm) {
        const r = Math.random();
        const [x, y] = r < 0.25 ? [-2, 0] : r < 0.5 ? [2, 0] : r < 0.65 ? [0, -1.5] : [0, 0];
        iris.forEach((i) => { i.style.transform = `translate(${x}px, ${y}px)`; });
      } else iris.forEach((i) => { i.style.transform = ''; });
      setTimeout(tick, 1600 + Math.random() * 2400);
    };
    setTimeout(tick, 1200);
  }
  const STATES = ['sleep', 'listening', 'hearing', 'thinking', 'speaking', 'muted', 'typed', 'idle'];
  const Mascot = {
    mountAll() { document.querySelectorAll('[data-mascot]').forEach(mount); },
    set(el, state) {
      if (!el) return;
      STATES.forEach((s) => el.classList.remove('m-' + s));
      el.classList.add('m-' + state);
      if (state !== 'speaking') Mascot.mouth(el, 0);
    },
    flag(el, cls, ms) {
      if (!el) return;
      el.classList.add('m-' + cls);
      clearTimeout(el['_t' + cls]);
      if (ms) el['_t' + cls] = setTimeout(() => el.classList.remove('m-' + cls), ms);
    },
    unflag(el, cls) { el && el.classList.remove('m-' + cls); },
    mouth(el, o) {  // o: 0 (closed smile) .. 1 (wide open), set from the real voice level
      if (!el) return;
      const open = Math.max(0, Math.min(1, o));
      const w = 9 + open * 1.5, top = 104 - open * 1.5, low = 107 + open * 15;
      const d = `M${80 - w} 104 Q80 ${top - 2} ${80 + w} 104 Q80 ${low} ${80 - w} 104 Z`;
      el.querySelectorAll('.m-mouth, .m-mouth-shape-clip').forEach((p) => p.setAttribute('d', d));
      const teeth = el.querySelector('.m-teeth'), tongue = el.querySelector('.m-tongue');
      if (teeth) teeth.setAttribute('opacity', open > 0.18 ? '1' : '0');
      if (tongue) { tongue.setAttribute('cy', String(104 + open * 13)); tongue.setAttribute('opacity', open > 0.35 ? '1' : '0'); }
    },
  };
  window.Mascot = Mascot;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => Mascot.mountAll());
  else Mascot.mountAll();
})();
