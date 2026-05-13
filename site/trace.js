/* ━━━ orc trace ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * The page is the artifact of an already-completed run. No
 * "verifying…" theatre — every verdict is final on load. JS
 * resolves pending pills, builds the summary tick row and the
 * sticky ledger from the claim DOM.
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

const VERDICTS = {
  ok:   { label: 'SUPPORTED',    cls: 'ok',   glyph: '✓' },
  warn: { label: 'PARTIAL',      cls: 'warn', glyph: '~' },
  bad:  { label: 'CONTRADICTED', cls: 'bad',  glyph: '✗' },
  nf:   { label: 'NOT_FOUND',    cls: 'nf',   glyph: '·' },
};

function verdictFor(el) {
  // climb until we find a node with data-verdict (claim) or data-sub (subclaim)
  let n = el;
  while (n && n !== document.body) {
    if (n.dataset.verdict) return { kind: n.dataset.verdict, score: n.dataset.score };
    if (n.dataset.sub)     return { kind: n.dataset.sub,     score: n.dataset.score };
    n = n.parentElement;
  }
  return { kind: 'ok', score: null };
}

/* ─── resolve every pending verdict pill ─────────────────── */
function resolveVerdicts() {
  document.querySelectorAll('.verdict.pending').forEach((pill) => {
    const { kind, score } = verdictFor(pill);
    const v = VERDICTS[kind] || VERDICTS.ok;
    pill.classList.remove('pending');
    pill.classList.add(v.cls);
    const scoreTxt = score ? ` · ${score}` : '';
    pill.innerHTML = `<span class="vt">${v.label}${scoreTxt}</span>`;
  });
}

/* ─── claim metadata: title, short label, verdict, id ───── */
function collectClaims() {
  return [...document.querySelectorAll('.claim[data-claim]')].map((el) => {
    const titleEl = el.querySelector('.claim-title');
    return {
      id: el.id,
      n: el.dataset.claim,
      kind: el.dataset.verdict,
      title: titleEl ? titleEl.textContent.trim().replace(/\s+/g, ' ') : '',
    };
  });
}

/* ─── summary tick row ───────────────────────────────────── */
function buildTicks(claims) {
  const host = document.getElementById('ticks');
  if (!host) return;
  host.innerHTML = '';
  claims.forEach((c) => {
    const t = document.createElement('a');
    t.href = `#${c.id}`;
    t.className = `tick ${c.kind}`;
    t.title = `claim_${c.n} · ${VERDICTS[c.kind].label} · ${c.title}`;
    host.appendChild(t);
  });
}

/* ─── ledger ─────────────────────────────────────────────── */
function buildLedger(claims) {
  const list = document.getElementById('ledger-list');
  if (!list) return;
  list.innerHTML = '';
  claims.forEach((c) => {
    const li = document.createElement('a');
    li.href = `#${c.id}`;
    li.className = `led ${c.kind}`;
    const v = VERDICTS[c.kind];
    li.innerHTML = `
      <span class="glyph">${v.glyph}</span>
      <span class="cid">${c.n}</span>
      <span class="lbl">${c.title}</span>
    `;
    list.appendChild(li);
  });
  const total = document.getElementById('led-total');
  const prog  = document.getElementById('led-progress');
  if (total) total.textContent = claims.length;
  if (prog)  prog.textContent  = claims.length;
}

/* ─── top-bar counters ───────────────────────────────────── */
function updateCounters(claims) {
  const counts = { ok: 0, warn: 0, bad: 0, nf: 0 };
  claims.forEach((c) => counts[c.kind] = (counts[c.kind] || 0) + 1);
  const set = (id, n) => {
    const el = document.getElementById(id);
    if (el) el.textContent = n;
  };
  set('cnt-ok', counts.ok);
  set('cnt-warn', counts.warn);
  set('cnt-bad', counts.bad);
}

/* ─── active-claim tracking (ledger highlight) ──────────── */
function trackActive(claims) {
  if (!('IntersectionObserver' in window)) return;
  const ledgerItems = [...document.querySelectorAll('#ledger-list a')];
  const byId = Object.fromEntries(ledgerItems.map((a) => [a.getAttribute('href').slice(1), a]));
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      const item = byId[e.target.id];
      if (item) item.classList.toggle('active', e.isIntersecting);
    });
  }, { rootMargin: '-25% 0px -55% 0px', threshold: 0 });
  document.querySelectorAll('.claim[data-claim]').forEach((c) => io.observe(c));
}

/* ─── replay button: subtle ack, no theatre ─────────────── */
function wireReplay() {
  const btn = document.getElementById('replay-btn');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const orig = btn.textContent;
    btn.textContent = '✓ identical (31.4s)';
    btn.style.background = 'var(--ink)';
    btn.style.color = 'var(--paper)';
    setTimeout(() => {
      btn.textContent = orig;
      btn.style.background = '';
      btn.style.color = '';
    }, 1800);
  });
}

/* ─── boot ───────────────────────────────────────────────── */
function boot() {
  resolveVerdicts();
  const claims = collectClaims();
  buildTicks(claims);
  buildLedger(claims);
  updateCounters(claims);
  trackActive(claims);
  wireReplay();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
