/* Dashboard client. Two data modes, chosen by config.js:
 *   server mode  - talks to the local FastAPI backend (/api/*, /ws)
 *   static mode  - GitHub Pages: results/history are JSON files, and the browser itself
 *                  polls Upstox's public candle API for live prices (CORS is allowed).
 */
(() => {
  const CFG = window.APP_CONFIG || { static: false };
  const STATIC = !!CFG.static;
  const $ = (id) => document.getElementById(id);
  const fmt = (n, d = 2) => (n == null || isNaN(n)) ? '—' : Number(n).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
  const pct = (n, d = 1, sign = true) => (n == null || isNaN(n)) ? '—' : `${sign && n > 0 ? '+' : ''}${Number(n).toFixed(d)}%`;
  const cls = (n) => n > 0 ? 'up' : n < 0 ? 'down' : '';
  const fmtVol = (v) => v == null ? '—' : v >= 1e7 ? (v / 1e7).toFixed(2) + ' Cr' : v >= 1e5 ? (v / 1e5).toFixed(2) + ' L' : String(v);

  const state = {
    results: null, quotes: {}, tab: 'breakouts', sort: { key: 'days_since_breakout', dir: 1 },
    selected: null, tf: 'W', chart: null, series: null, status: null, indices: CFG.indices || {},
    lastQuoteTs: null, polling: false, nextPollAt: null, pollError: null,
  };

  // ---------------------------------------------------------------- IST clock / market phase (client side)
  function istNow() {
    const p = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', weekday: 'short' }).formatToParts(new Date());
    const g = (t) => p.find(x => x.type === t).value;
    return { date: `${g('year')}-${g('month')}-${g('day')}`, hm: `${g('hour') % 24}`.padStart(2, '0') + ':' + g('minute'), mins: (+g('hour') % 24) * 60 + +g('minute'), weekday: g('weekday') };
  }
  function marketPhase() {
    const n = istNow();
    if (n.weekday === 'Sat' || n.weekday === 'Sun') return 'closed';
    if (n.mins >= 540 && n.mins < 555) return 'pre_open';
    if (n.mins >= 555 && n.mins < 930) return 'open';
    return 'closed';
  }

  // ---------------------------------------------------------------- theme
  const applyTheme = (t) => { document.documentElement.dataset.theme = t; try { localStorage.setItem('theme', t); } catch {} ; if (state.chart) styleChart(); };
  (() => { let t = null; try { t = localStorage.getItem('theme'); } catch {} ; applyTheme(t || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')); })();
  $('theme-btn').onclick = () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');

  // ---------------------------------------------------------------- columns
  const COLS = {
    breakouts: [
      { key: 'symbol', label: 'Stock', render: r => `${r.symbol}<span class="nm">${r.name}</span>` },
      { key: 'pattern', label: 'Pattern', render: r => `<span class="badge ${r.pattern}">${r.pattern}</span>` },
      { key: 'ltp', label: 'LTP', num: true, render: r => { const q = state.quotes[r.token]; return q ? fmt(q.ltp) : fmt(r.last_close); } },
      { key: 'change_pct', label: 'Day %', num: true, render: r => { const q = state.quotes[r.token]; return q && q.change_pct != null ? `<span class="${cls(q.change_pct)}">${pct(q.change_pct, 2)}</span>` : '—'; } },
      { key: 'rim_price', label: 'Prior ATH', num: true, render: r => fmt(r.base.rim_price), get: r => r.base.rim_price },
      { key: 'pct_above_rim', label: 'Above ATH', num: true, render: r => `<span class="${cls(liveAboveRim(r))}">${pct(liveAboveRim(r))}</span>`, get: r => liveAboveRim(r) },
      { key: 'depth_pct', label: 'Base depth', num: true, render: r => pct(r.base.depth_pct, 1, false), get: r => r.base.depth_pct },
      { key: 'weeks', label: 'Base weeks', num: true, render: r => r.base.weeks, get: r => r.base.weeks },
      { key: 'breakout_date', label: 'Breakout', render: r => r.base.breakout_date, get: r => r.base.breakout_date },
      { key: 'days_since_breakout', label: 'Days ago', num: true, render: r => r.days_since_breakout },
      { key: 'breakout_vol_ratio', label: 'Vol × base', num: true, render: r => r.base.breakout_vol_ratio == null ? '—' : r.base.breakout_vol_ratio.toFixed(1) + '×', get: r => r.base.breakout_vol_ratio ?? -1 },
      { key: 'listing_date', label: 'Listed', render: r => r.listing_date },
    ],
    approaching: [
      { key: 'symbol', label: 'Stock', render: r => `${r.symbol}<span class="nm">${r.name}</span>` },
      { key: 'ltp', label: 'LTP', num: true, render: r => { const q = state.quotes[r.token]; return q ? fmt(q.ltp) : fmt(r.last_close); } },
      { key: 'change_pct', label: 'Day %', num: true, render: r => { const q = state.quotes[r.token]; return q && q.change_pct != null ? `<span class="${cls(q.change_pct)}">${pct(q.change_pct, 2)}</span>` : '—'; } },
      { key: 'rim_price', label: 'All-time high', num: true, render: r => fmt(r.base.rim_price), get: r => r.base.rim_price },
      { key: 'pct_below_rim', label: 'Below ATH', num: true, render: r => pct(-liveBelowRim(r)), get: r => liveBelowRim(r) },
      { key: 'depth_pct', label: 'Base depth', num: true, render: r => pct(r.base.depth_pct, 1, false), get: r => r.base.depth_pct },
      { key: 'weeks', label: 'Weeks in base', num: true, render: r => r.base.weeks, get: r => r.base.weeks },
      { key: 'rim_date', label: 'ATH date', render: r => r.base.rim_date, get: r => r.base.rim_date },
      { key: 'listing_date', label: 'Listed', render: r => r.listing_date },
    ],
  };
  const livePx = (r) => { const q = state.quotes[r.token]; return q ? q.ltp : r.last_close; };
  const liveAboveRim = (r) => (livePx(r) - r.base.rim_price) / r.base.rim_price * 100;
  const liveBelowRim = (r) => (r.base.rim_price - livePx(r)) / r.base.rim_price * 100;
  const allRows = () => state.results ? [...state.results.breakouts, ...state.results.approaching] : [];

  // ---------------------------------------------------------------- data: results & history
  async function loadResults() {
    const res = await fetch(STATIC ? 'data/results.json' : '/api/results', { cache: 'no-cache' });
    state.results = await res.json();
    const p = state.results.params || {};
    if ($('f-depth').value === '') $('f-depth').value = p.min_depth_pct ?? 15;
    if ($('f-weeks').value === '') $('f-weeks').value = p.min_weeks ?? 7;
    $('scan-meta').textContent = state.results.generated_at
      ? `Scanned ${state.results.scanned} of ${state.results.universe_size} stocks · ${new Date(state.results.generated_at).toLocaleString('en-IN')}`
      : 'No scan results yet';
    render();
  }

  async function loadHistory(symbol, tf) {
    if (STATIC) {
      const j = await (await fetch(`data/history/${symbol}.json`)).json();
      return (j[tf] || []).map(c => ({ time: c[0], open: c[1], high: c[2], low: c[3], close: c[4] }));
    }
    const j = await (await fetch(`/api/history/${symbol}?tf=${tf}`)).json();
    return j.candles.map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close }));
  }

  // ---------------------------------------------------------------- status pill
  function paintStatus(s) {
    const m = $('market-pill'); m.className = 'pill ' + ({ open: 'open', pre_open: 'pre', closed: 'closed' }[s.market.phase] || 'closed');
    m.textContent = { open: 'Market open', pre_open: 'Pre-open', closed: 'Market closed' }[s.market.phase] + ' · ' + s.market.ist.slice(11, 16) + ' IST';
    const f = $('feed-pill');
    const stale = s.feed.seconds_since_tick;
    const ts = s.feed.last_quote_ts;
    const asOf = ts ? ` · as of ${ts.slice(0, 10) === s.market.ist.slice(0, 10) ? '' : ts.slice(5, 10) + ' '}${ts.slice(11, 16)} IST` : '';
    if (s.feed.mode === 'simulated') { f.className = 'pill sim'; f.textContent = 'Simulated feed'; }
    else if (s.feed.mode === 'upstox') { f.className = 'pill delayed'; f.textContent = `Upstox 1-min candles · ${s.feed.subscribed} symbols${asOf}`; }
    else if (s.feed.mode === 'browser') { f.className = 'pill delayed'; f.textContent = (state.polling ? 'Refreshing… · ' : '') + `Upstox 1-min candles · ${s.feed.subscribed} symbols${asOf}`; }
    else if (s.feed.mode === 'live' && s.feed.connected) { f.className = 'pill live'; f.textContent = `Live · ${s.feed.subscribed} symbols` + (stale != null && stale > 60 && s.market.phase === 'open' ? ` · stale ${Math.round(stale)}s` : ''); }
    else { f.className = 'pill off'; f.textContent = s.has_credentials ? 'Feed disconnected' : 'No feed'; }
    $('sim-banner').classList.toggle('hidden', s.feed.mode !== 'simulated');
    $('upstox-banner').classList.toggle('hidden', !(s.feed.mode === 'upstox' || s.feed.mode === 'browser'));
    $('poll-secs').textContent = s.feed.poll_seconds ?? 60;
    $('err-banner').classList.toggle('hidden', !s.feed.error);
    if (s.feed.error) $('err-banner').textContent = s.feed.error;
  }

  async function loadStatus() {
    if (STATIC) {
      const n = istNow();
      paintStatus({ market: { phase: marketPhase(), ist: `${n.date}T${n.hm}` },
        feed: { mode: 'browser', subscribed: allRows().length + Object.keys(state.indices).length, last_quote_ts: state.lastQuoteTs, poll_seconds: CFG.poll_seconds || 60, error: state.pollError } });
      return;
    }
    try {
      const s = await (await fetch('/api/status')).json(); state.status = s;
      paintStatus(s);
      $('rescan-btn').disabled = s.rescan_running; $('rescan-btn').textContent = s.rescan_running ? 'Scanning…' : 'Re-scan';
      if (!s.rescan_running && state._wasScanning) { state._wasScanning = false; loadResults(); }
      if (s.rescan_running) state._wasScanning = true;
      state.indices = s.indices || {}; renderIndices();
    } catch {}
  }

  // ---------------------------------------------------------------- live prices: server WebSocket
  function connectWS() {
    const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      applyQuotes(msg.quotes, msg.type === 'snapshot');
    };
    ws.onclose = () => setTimeout(connectWS, 2000);
  }

  function applyQuotes(quotes, full) {
    const changed = new Set();
    for (const q of quotes) { state.quotes[q.token] = q; changed.add(q.token); if (q.ts && (!state.lastQuoteTs || q.ts > state.lastQuoteTs)) state.lastQuoteTs = q.ts; }
    if (full) render(); else patchRows(changed);
    renderIndices(); if (state.selected && changed.has(state.selected.token)) renderDetailPrice();
  }

  // ---------------------------------------------------------------- live prices: browser polls Upstox directly (static mode)
  const UPSTOX = 'https://api.upstox.com/v3/historical-candle/';
  const bust = () => `t=${Math.floor(Date.now() / 30000)}`;
  async function getCandles(url) {
    const r = await fetch(url, { headers: { Accept: 'application/json' } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    return ((j.data || {}).candles || []).slice().reverse(); // oldest first
  }
  async function fetchUpstoxQuote(key) {
    const k = encodeURIComponent(key);
    const c = await getCandles(`${UPSTOX}intraday/${k}/minutes/1?${bust()}`);
    if (c.length) {
      const last = c[c.length - 1];
      return { ltp: last[4], open: c[0][1], high: Math.max(...c.map(x => x[2])), low: Math.min(...c.map(x => x[3])), volume: Math.max(...c.map(x => x[5])), ts: last[0], day: last[0].slice(0, 10) };
    }
    // No session today yet (overnight, weekend, holiday): fall back to the last daily candle.
    const today = istNow().date, from = new Date(Date.now() - 10 * 864e5).toISOString().slice(0, 10);
    const d = await getCandles(`${UPSTOX}${k}/days/1/${today}/${from}?${bust()}`);
    if (!d.length) return null;
    const last = d[d.length - 1];
    return { ltp: last[4], open: last[1], high: last[2], low: last[3], volume: last[5], ts: last[0].slice(0, 10) + 'T15:30:00+05:30', day: last[0].slice(0, 10) };
  }

  async function pollUpstox() {
    if (state.polling || !state.results) return;
    try { await pollUpstoxInner(); }
    catch (e) { state.pollError = 'Live refresh failed: ' + (e && e.message || e); console.error(e); }
    finally { state.polling = false; loadStatus(); }
  }

  async function pollUpstoxInner() {
    state.polling = true; loadStatus();
    const items = [];
    for (const [tok, key] of Object.entries(CFG.index_keys || {})) items.push({ tok, key });
    for (const r of allRows()) if (r.isin) items.push({ tok: r.token, key: `NSE_EQ|${r.isin}`, r });
    let failures = 0;
    for (let i = 0; i < items.length; i += 4) {                       // a few at a time, never hundreds at once
      const batch = items.slice(i, i + 4);
      const got = await Promise.all(batch.map(async (it) => {
        try {
          const q = await fetchUpstoxQuote(it.key); if (!q) return null;
          const r = it.r; let prev = null;
          if (r) prev = (r.prev_close && q.day === r.last_date) ? r.prev_close : r.last_close;
          return { token: it.tok, ltp: q.ltp, open: q.open, high: q.high, low: q.low, volume: q.volume, ts: q.ts,
                   prev_close: prev, change_pct: prev ? +((q.ltp - prev) / prev * 100).toFixed(2) : null };
        } catch (e) { failures++; return null; }
      }));
      applyQuotes(got.filter(Boolean), false);
      if (i + 4 < items.length) await new Promise(res => setTimeout(res, 250));
    }
    state.pollError = failures === items.length && items.length ? 'Could not reach api.upstox.com from this browser (offline, or an ad-blocker/network blocking it).' : null;
  }
  window.__pollUpstox = pollUpstox; // handy for debugging from the console

  function schedulePolling() {
    const tick = async () => {
      if (document.visibilityState === 'visible') await pollUpstox();
      const phase = marketPhase();
      const wait = (phase === 'open' || phase === 'pre_open') ? (CFG.poll_seconds || 60) : (CFG.idle_poll_seconds || 600);
      state.nextPollAt = Date.now() + wait * 1000;
      setTimeout(tick, wait * 1000);
    };
    tick();
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && state.nextPollAt && Date.now() > state.nextPollAt) pollUpstox(); });
  }

  // ---------------------------------------------------------------- rendering
  function currentRows() {
    const r = state.results; if (!r) return [];
    const rows = (state.tab === 'breakouts' ? r.breakouts : r.approaching) || [];
    const pat = $('f-pattern').value, age = +$('f-age').value, depth = +$('f-depth').value || 0, weeks = +$('f-weeks').value || 0;
    const q = $('f-search').value.trim().toLowerCase();
    return rows.filter(x =>
      (state.tab !== 'breakouts' || pat === 'all' || x.pattern === pat) &&
      (state.tab !== 'breakouts' || !age || x.days_since_breakout <= age) &&
      x.base.depth_pct >= depth && x.base.weeks >= weeks &&
      (!q || x.symbol.toLowerCase().includes(q) || x.name.toLowerCase().includes(q)));
  }

  function sorted(rows) {
    const cols = COLS[state.tab]; const col = cols.find(c => c.key === state.sort.key) || cols[0];
    const get = col.get || ((r) => (r[col.key] ?? (state.quotes[r.token] || {})[col.key]));
    return rows.slice().sort((a, b) => { const x = get(a), y = get(b); return (x > y ? 1 : x < y ? -1 : 0) * state.sort.dir; });
  }

  function render() {
    const r = state.results; if (!r) return;
    $('n-breakouts').textContent = r.breakouts.length; $('n-approaching').textContent = r.approaching.length;
    renderTiles(); renderIndices();
    const cols = COLS[state.tab];
    $('grid-head').innerHTML = '<tr>' + cols.map(c => `<th class="${c.num ? 'num' : ''}" data-key="${c.key}">${c.label}${state.sort.key === c.key ? `<span class="arrow">${state.sort.dir > 0 ? '▲' : '▼'}</span>` : ''}</th>`).join('') + '</tr>';
    $('grid-head').querySelectorAll('th').forEach(th => th.onclick = () => { const k = th.dataset.key; state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : 1 }; render(); });
    const rows = sorted(currentRows());
    $('empty').classList.toggle('hidden', rows.length > 0);
    $('grid-body').innerHTML = rows.map(x => `<tr data-token="${x.token}" class="${state.selected && state.selected.symbol === x.symbol ? 'sel' : ''}">` +
      cols.map(c => `<td class="${c.num ? 'num' : ''}" data-col="${c.key}">${c.render(x)}</td>`).join('') + '</tr>').join('');
    $('grid-body').querySelectorAll('tr').forEach(tr => tr.onclick = () => select(rows.find(x => x.token === tr.dataset.token)));
  }

  function patchRows(changed) {
    const rows = currentRows(); const byTok = Object.fromEntries(rows.map(r => [r.token, r]));
    const cols = COLS[state.tab]; const liveCols = new Set(['ltp', 'change_pct', 'pct_above_rim', 'pct_below_rim']);
    $('grid-body').querySelectorAll('tr').forEach(tr => {
      const r = byTok[tr.dataset.token]; if (!r || !changed.has(r.token)) return;
      tr.querySelectorAll('td').forEach(td => { const c = cols.find(k => k.key === td.dataset.col); if (c && liveCols.has(c.key)) td.innerHTML = c.render(r); });
      tr.classList.remove('flash'); void tr.offsetWidth; tr.classList.add('flash');
    });
    renderTiles();
  }

  function renderTiles() {
    const r = state.results; if (!r) return;
    const b = r.breakouts, fresh = b.filter(x => x.days_since_breakout <= 30).length;
    const u = b.filter(x => x.pattern === 'U').length, w = b.length - u;
    const upToday = b.filter(x => (state.quotes[x.token] || {}).change_pct > 0).length;
    const withQuotes = b.filter(x => state.quotes[x.token]).length;
    $('tiles').innerHTML = [
      ['Above prior ATH', b.length, `${u} cup · ${w} double-bottom`],
      ['Broke out ≤ 30 days', fresh, 'fresh breakouts'],
      ['Approaching ATH', r.approaching.length, `within ${r.params?.near_breakout_pct ?? 5}% below the rim`],
      ['Up today', withQuotes ? `${upToday}/${withQuotes}` : '—', 'of breakout stocks with a quote'],
      ['Universe', r.universe_size, `NSE EQ listed in last 10 years · ${r.scanned} with history`],
    ].map(([k, v, s]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join('');
  }

  function renderIndices() {
    $('indices').innerHTML = Object.entries(state.indices).map(([tok, name]) => {
      const q = state.quotes[tok]; return `<div class="idx"><span class="n">${name}</span><span class="v">${q ? fmt(q.ltp) : '—'} <span class="${q ? cls(q.change_pct) : ''}">${q && q.change_pct != null ? pct(q.change_pct, 2) : ''}</span></span></div>`;
    }).join('');
  }

  // ---------------------------------------------------------------- detail + chart
  function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function styleChart() {
    if (!state.chart) return;
    state.chart.applyOptions({
      layout: { background: { color: cssVar('--surface') }, textColor: cssVar('--text-2'), fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif' },
      grid: { vertLines: { color: cssVar('--grid') }, horzLines: { color: cssVar('--grid') } },
      rightPriceScale: { borderColor: cssVar('--axis') }, timeScale: { borderColor: cssVar('--axis') },
      crosshair: { vertLine: { color: cssVar('--muted') }, horzLine: { color: cssVar('--muted') } },
    });
    if (state.series) state.series.applyOptions({ upColor: cssVar('--candle-up'), downColor: cssVar('--candle-down'), wickUpColor: cssVar('--candle-up'), wickDownColor: cssVar('--candle-down'), borderVisible: false });
  }

  async function select(r) {
    if (!r) return; state.selected = r;
    $('grid-body').querySelectorAll('tr').forEach(tr => tr.classList.toggle('sel', tr.dataset.token === r.token));
    $('detail-empty').classList.add('hidden'); $('detail-body').classList.remove('hidden');
    $('d-symbol').textContent = r.symbol; $('d-name').textContent = `${r.name} · listed ${r.listing_date}`;
    const pb = $('d-pattern'); pb.textContent = r.pattern ? (r.pattern === 'U' ? 'U · cup' : 'W · double bottom') : 'in base'; pb.className = 'badge ' + (r.pattern || '');
    renderDetailPrice(); renderFacts(r); renderBases(r);
    await drawChart(r);
  }

  function renderDetailPrice() {
    const r = state.selected; const q = state.quotes[r.token];
    $('d-ltp').textContent = '₹' + fmt(q ? q.ltp : r.last_close);
    const c = q ? q.change_pct : null;
    const when = q && q.ts ? ` · ${q.ts.slice(5, 10)} ${q.ts.slice(11, 16)}` : '';
    $('d-chg').innerHTML = q ? `<span class="${cls(c)}">${pct(c, 2)}</span> vs prev close ${fmt(q.prev_close)}${when}<br>O ${fmt(q.open)} H ${fmt(q.high)} L ${fmt(q.low)} · Vol ${fmtVol(q.volume)}` : `last close ${r.last_date}`;
  }

  function renderFacts(r) {
    const b = r.base; const items = r.status === 'breakout' ? [
      ['Prior all-time high (rim)', `₹${fmt(b.rim_price)} · ${b.rim_date}`],
      ['Trough', `₹${fmt(b.trough_price)} · ${b.trough_date}`],
      ...(b.pattern === 'W' ? [['Middle peak', `₹${fmt(b.mid_peak_price)} · ${b.mid_peak_date}`], ['Second trough', `₹${fmt(b.second_trough_price)} · ${b.second_trough_date}`]] : []),
      ['Base depth / length', `${b.depth_pct}% · ${b.weeks} weeks`],
      ['Breakout (weekly close)', `₹${fmt(b.breakout_close)} · ${b.breakout_date}`],
      ['Breakout volume vs base avg', b.breakout_vol_ratio == null ? '—' : `${b.breakout_vol_ratio.toFixed(1)}×`],
      ['Now vs rim', pct(liveAboveRim(r))],
      ['Post-breakout high', `₹${fmt(r.ath_close)} · ${r.ath_date}`],
      ['Pullback from that high', `${r.drawdown_since_ath_pct}% max · ${pct(r.pct_from_ath)} now`],
      ['Time near the bottom', `${b.time_in_lower_half_pct}% of base weeks (low = V-shape)`],
      ['52-week range', `₹${fmt(r.lo_52w)} – ₹${fmt(r.hi_52w)}`],
      ['Avg volume (20d)', fmtVol(r.avg_vol_20d)],
      ['Completed bases', r.n_bases],
    ] : [
      ['All-time high (rim)', `₹${fmt(b.rim_price)} · ${b.rim_date}`],
      ['Trough so far', `₹${fmt(b.trough_price)} · ${b.trough_date}`],
      ['Base depth / length', `${b.depth_pct}% · ${b.weeks} weeks`],
      ['Distance below rim', pct(-liveBelowRim(r))],
      ['52-week range', `₹${fmt(r.lo_52w)} – ₹${fmt(r.hi_52w)}`],
      ['Avg volume (20d)', fmtVol(r.avg_vol_20d)],
      ['Completed bases before this', r.n_bases],
    ];
    $('facts').innerHTML = items.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('');
  }

  function renderBases(r) {
    if (!r.bases || !r.bases.length) { $('bases').innerHTML = '<p class="muted">None.</p>'; return; }
    $('bases').innerHTML = '<table><thead><tr><th>#</th><th>Type</th><th>Rim</th><th>Trough</th><th>Depth</th><th>Weeks</th><th>Breakout</th></tr></thead><tbody>' +
      r.bases.map((b, i) => `<tr><td>${i + 1}</td><td><span class="badge ${b.pattern}">${b.pattern}</span></td><td>₹${fmt(b.rim_price)}<br><span class="muted">${b.rim_date}</span></td><td>₹${fmt(b.trough_price)}<br><span class="muted">${b.trough_date}</span></td><td>${b.depth_pct}%</td><td>${b.weeks}</td><td>₹${fmt(b.breakout_close)}<br><span class="muted">${b.breakout_date}</span></td></tr>`).join('') + '</tbody></table>';
  }

  async function drawChart(r) {
    let candles;
    try { candles = await loadHistory(r.symbol, state.tf); } catch { candles = []; }
    const el = $('chart');
    if (!state.chart) {
      state.chart = LightweightCharts.createChart(el, { height: 320, autoSize: true, timeScale: { timeVisible: false }, handleScroll: true, handleScale: true });
      state.series = state.chart.addCandlestickSeries({ priceFormat: { type: 'price', precision: 2, minMove: 0.05 } });
      styleChart();
    }
    state.series.setData(candles);
    for (const l of (state._lines || [])) state.series.removePriceLine(l);
    const b = r.base; state._lines = [];
    if (!candles.length) { state.series.setMarkers([]); return; }
    const addLine = (price, color, title) => state._lines.push(state.series.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title }));
    addLine(b.rim_price, cssVar('--series-2'), 'prior ATH');
    addLine(b.trough_price, cssVar('--series-7'), 'trough');
    const nearest = (d) => { let best = candles[0]; for (const c of candles) { if (c.time <= d) best = c; else break; } return best ? best.time : d; };
    const markers = [{ time: nearest(b.rim_date), position: 'aboveBar', color: cssVar('--series-2'), shape: 'arrowDown', text: 'rim' },
                     { time: nearest(b.trough_date), position: 'belowBar', color: cssVar('--series-7'), shape: 'arrowUp', text: 'trough' }];
    if (b.second_trough_date) markers.push({ time: nearest(b.second_trough_date), position: 'belowBar', color: cssVar('--series-7'), shape: 'arrowUp', text: 'trough 2' });
    if (b.mid_peak_date) markers.push({ time: nearest(b.mid_peak_date), position: 'aboveBar', color: cssVar('--series-7'), shape: 'circle', text: 'peak' });
    if (b.breakout_date) markers.push({ time: nearest(b.breakout_date), position: 'belowBar', color: cssVar('--series-3'), shape: 'arrowUp', text: 'breakout' });
    markers.sort((a, c) => a.time < c.time ? -1 : 1);
    state.series.setMarkers(markers);
    const from = candles.findIndex(c => c.time >= b.rim_date);
    state.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, from - 8), to: candles.length + 2 });
  }

  // ---------------------------------------------------------------- wiring
  document.querySelectorAll('.tab').forEach(t => t.onclick = () => { document.querySelectorAll('.tab').forEach(x => x.classList.remove('active')); t.classList.add('active'); state.tab = t.dataset.tab; state.sort = state.tab === 'breakouts' ? { key: 'days_since_breakout', dir: 1 } : { key: 'pct_below_rim', dir: 1 }; render(); });
  document.querySelectorAll('.seg').forEach(s => s.onclick = () => { document.querySelectorAll('.seg').forEach(x => x.classList.remove('active')); s.classList.add('active'); state.tf = s.dataset.tf; if (state.selected) drawChart(state.selected); });
  ['f-pattern', 'f-age', 'f-depth', 'f-weeks'].forEach(id => $(id).onchange = render);
  $('f-search').oninput = render;
  $('rescan-btn').onclick = async () => {
    const qs = new URLSearchParams({ min_depth_pct: $('f-depth').value, min_weeks: $('f-weeks').value, max_breakout_age_days: $('f-age').value });
    $('rescan-btn').disabled = true; $('rescan-btn').textContent = 'Scanning…'; state._wasScanning = true;
    await fetch('/api/rescan?' + qs, { method: 'POST' });
  };
  $('refresh-btn').onclick = () => STATIC ? pollUpstox() : loadStatus();

  if (STATIC) {
    $('rescan-btn').classList.add('hidden');
    $('static-note').classList.remove('hidden');
    if (CFG.built_at) $('built-at').textContent = new Date(CFG.built_at).toLocaleString('en-IN');
    loadResults().then(() => { loadStatus(); schedulePolling(); });
    setInterval(loadStatus, 5000);
  } else {
    $('refresh-btn').classList.add('hidden');
    loadResults(); loadStatus(); connectWS(); setInterval(loadStatus, 5000);
  }
})();
