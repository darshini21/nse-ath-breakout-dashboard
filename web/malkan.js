/* Malkan systems dashboard. Works against the local server (/api/*, /ws) or as a static
 * GitHub Pages copy (data/*.json + the browser polling Upstox for live prices). */
(() => {
  const CFG = window.APP_CONFIG || { static: false };
  const STATIC = !!CFG.static;
  const $ = (id) => document.getElementById(id);
  const fmt = (n, d = 2) => (n == null || isNaN(n)) ? '—' : Number(n).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
  const inr = (n) => (n == null || isNaN(n)) ? '—' : '₹' + fmt(n, n >= 1000 ? 0 : 2);
  const pct = (n, d = 1, sign = true) => (n == null || isNaN(n)) ? '—' : `${sign && n > 0 ? '+' : ''}${Number(n).toFixed(d)}%`;
  const cls = (n) => n > 0 ? 'up' : n < 0 ? 'down' : '';
  const today = () => istNow().date;

  const state = {
    data: null, quotes: {}, tab: 's1', chip: 'all', selected: null, tf: 'W', chart: null, rsiChart: null,
    series: {}, journal: [], indices: CFG.indices || {}, lastQuoteTs: null, polling: false, pollError: null,
    settings: { capital: 1000000, risk: 1 }, editing: null, sort: null,
  };
  try { const s = JSON.parse(localStorage.getItem('malkan.settings') || 'null'); if (s) state.settings = s; } catch {}

  // ---------------------------------------------------------------- IST / market phase
  function istNow() {
    const p = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', weekday: 'short' }).formatToParts(new Date());
    const g = (t) => p.find(x => x.type === t).value;
    return { date: `${g('year')}-${g('month')}-${g('day')}`, hm: `${g('hour') % 24}`.padStart(2, '0') + ':' + g('minute'), mins: (+g('hour') % 24) * 60 + +g('minute'), weekday: g('weekday') };
  }
  function marketPhase() { const n = istNow(); if (n.weekday === 'Sat' || n.weekday === 'Sun') return 'closed'; if (n.mins >= 540 && n.mins < 555) return 'pre_open'; if (n.mins >= 555 && n.mins < 930) return 'open'; return 'closed'; }

  // ---------------------------------------------------------------- theme
  const applyTheme = (t) => { document.documentElement.dataset.theme = t; try { localStorage.setItem('theme', t); } catch {} ; styleCharts(); };
  (() => { let t = null; try { t = localStorage.getItem('theme'); } catch {} ; applyTheme(t || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')); })();
  $('theme-btn').onclick = () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');

  // ---------------------------------------------------------------- settings (capital / risk)
  const rpt = () => state.settings.capital * state.settings.risk / 100;
  function renderSettings() {
    $('s-capital').value = state.settings.capital; $('s-risk').value = state.settings.risk;
    $('s-rpt').textContent = inr(rpt()) + ' per trade';
  }
  ['s-capital', 's-risk'].forEach(id => $(id).onchange = () => {
    state.settings = { capital: +$('s-capital').value || 1000000, risk: +$('s-risk').value || 1 };
    try { localStorage.setItem('malkan.settings', JSON.stringify(state.settings)); } catch {}
    renderSettings(); if (state.selected) renderPlan(state.selected); if (state.tab === 'journal') render();
  });

  // ---------------------------------------------------------------- live prices
  const livePx = (r) => { const q = state.quotes[r.token]; return q ? q.ltp : r.last_close; };
  function applyQuotes(quotes, full) {
    const changed = new Set();
    for (const q of quotes) { state.quotes[q.token] = q; changed.add(q.token); if (q.ts && (!state.lastQuoteTs || q.ts > state.lastQuoteTs)) state.lastQuoteTs = q.ts; }
    if (full) render(); else patchRows(changed);
    renderIndices(); if (state.selected && changed.has(state.selected.token)) { renderDetailPrice(); renderPlan(state.selected); }
  }
  function connectWS() {
    const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); applyQuotes(m.quotes, m.type === 'snapshot'); };
    ws.onclose = () => setTimeout(connectWS, 2000);
  }
  const UPSTOX = 'https://api.upstox.com/v3/historical-candle/';
  const bust = () => `t=${Math.floor(Date.now() / 30000)}`;
  async function getCandles(url) { const r = await fetch(url, { headers: { Accept: 'application/json' } }); if (!r.ok) throw new Error(`HTTP ${r.status}`); const j = await r.json(); return ((j.data || {}).candles || []).slice().reverse(); }
  async function fetchUpstoxQuote(key) {
    const k = encodeURIComponent(key);
    const c = await getCandles(`${UPSTOX}intraday/${k}/minutes/1?${bust()}`);
    if (c.length) { const last = c[c.length - 1]; return { ltp: last[4], open: c[0][1], high: Math.max(...c.map(x => x[2])), low: Math.min(...c.map(x => x[3])), volume: Math.max(...c.map(x => x[5])), ts: last[0], day: last[0].slice(0, 10) }; }
    const from = new Date(Date.now() - 10 * 864e5).toISOString().slice(0, 10);
    const d = await getCandles(`${UPSTOX}${k}/days/1/${today()}/${from}?${bust()}`);
    if (!d.length) return null;
    const last = d[d.length - 1];
    return { ltp: last[4], open: last[1], high: last[2], low: last[3], volume: last[5], ts: last[0].slice(0, 10) + 'T15:30:00+05:30', day: last[0].slice(0, 10) };
  }
  function pollTargets() {
    // visible rows first, then open journal trades, capped so a visitor never hammers the API
    const items = [];
    for (const [tok, key] of Object.entries(CFG.index_keys || {})) items.push({ tok, key });
    const seen = new Set();
    const add = (r) => { if (r && r.isin && !seen.has(r.isin)) { seen.add(r.isin); items.push({ tok: r.token || r.isin, key: `NSE_EQ|${r.isin}`, r }); } };
    currentRows().forEach(add);
    state.journal.filter(t => t.status !== 'closed').forEach(t => add({ isin: t.isin, token: t.isin, last_close: t.entry, prev_close: null, last_date: null }));
    return items.slice(0, 220);
  }
  async function pollUpstox() {
    if (state.polling || !state.data) return;
    state.polling = true; loadStatus();
    try {
      const items = pollTargets(); let failures = 0;
      for (let i = 0; i < items.length; i += 4) {
        const got = await Promise.all(items.slice(i, i + 4).map(async (it) => {
          try {
            const q = await fetchUpstoxQuote(it.key); if (!q) return null;
            const r = it.r; let prev = null;
            if (r) prev = (r.prev_close && q.day === r.last_date) ? r.prev_close : r.last_close;
            return { token: it.tok, ltp: q.ltp, open: q.open, high: q.high, low: q.low, volume: q.volume, ts: q.ts, prev_close: prev, change_pct: prev ? +((q.ltp - prev) / prev * 100).toFixed(2) : null };
          } catch { failures++; return null; }
        }));
        applyQuotes(got.filter(Boolean), false);
        if (i + 4 < items.length) await new Promise(res => setTimeout(res, 250));
      }
      state.pollError = failures === items.length && items.length ? 'Could not reach api.upstox.com from this browser.' : null;
    } catch (e) { state.pollError = 'Live refresh failed: ' + (e && e.message || e); }
    finally { state.polling = false; loadStatus(); }
  }
  function schedulePolling() {
    const tick = async () => {
      if (document.visibilityState === 'visible') await pollUpstox();
      const ph = marketPhase(); const wait = (ph === 'open' || ph === 'pre_open') ? (CFG.poll_seconds || 60) : (CFG.idle_poll_seconds || 600);
      setTimeout(tick, wait * 1000);
    };
    tick();
  }

  // ---------------------------------------------------------------- status
  function paintStatus(s) {
    const m = $('market-pill'); m.className = 'pill ' + ({ open: 'open', pre_open: 'pre', closed: 'closed' }[s.market.phase] || 'closed');
    m.textContent = { open: 'Market open', pre_open: 'Pre-open', closed: 'Market closed' }[s.market.phase] + ' · ' + s.market.ist.slice(11, 16) + ' IST';
    const f = $('feed-pill'); const ts = s.feed.last_quote_ts;
    const asOf = ts ? ` · as of ${ts.slice(0, 10) === s.market.ist.slice(0, 10) ? '' : ts.slice(5, 10) + ' '}${ts.slice(11, 16)} IST` : '';
    if (s.feed.mode === 'upstox' || s.feed.mode === 'browser') { f.className = 'pill delayed'; f.textContent = (state.polling ? 'Refreshing… · ' : '') + `Upstox 1-min candles${asOf}`; }
    else if (s.feed.mode === 'live' && s.feed.connected) { f.className = 'pill live'; f.textContent = 'Live · Angel One'; }
    else if (s.feed.mode === 'simulated') { f.className = 'pill sim'; f.textContent = 'Simulated feed'; }
    else { f.className = 'pill off'; f.textContent = 'No feed'; }
    $('upstox-banner').classList.toggle('hidden', !(s.feed.mode === 'upstox' || s.feed.mode === 'browser'));
    $('poll-secs').textContent = s.feed.poll_seconds ?? 60;
    $('err-banner').classList.toggle('hidden', !s.feed.error); if (s.feed.error) $('err-banner').textContent = s.feed.error;
  }
  async function loadStatus() {
    if (STATIC) { const n = istNow(); paintStatus({ market: { phase: marketPhase(), ist: `${n.date}T${n.hm}` }, feed: { mode: 'browser', last_quote_ts: state.lastQuoteTs, poll_seconds: CFG.poll_seconds || 60, error: state.pollError } }); return; }
    try {
      const s = await (await fetch('/api/status')).json(); paintStatus(s); state.indices = s.indices || {};
      $('rescan-btn').disabled = s.rescan_running; $('rescan-btn').textContent = s.rescan_running ? 'Scanning…' : 'Re-scan';
      if (!s.rescan_running && state._wasScanning) { state._wasScanning = false; loadData(); }
      if (s.rescan_running) state._wasScanning = true;
    } catch {}
  }

  // ---------------------------------------------------------------- data
  async function loadData() {
    state.data = await (await fetch(STATIC ? 'data/malkan.json' : '/api/malkan', { cache: 'no-cache' })).json();
    $('scan-meta').textContent = state.data.generated_at ? `Scanned ${state.data.scanned} of ${state.data.universe_size} Nifty 500 stocks · ${new Date(state.data.generated_at).toLocaleString('en-IN')}` : 'No scan yet — run: run.cmd malkan';
    const sectors = (state.data.sectors || []).map(s => s.industry).sort();
    $('f-sector').innerHTML = '<option value="">all sectors</option>' + sectors.map(s => `<option>${s}</option>`).join('');
    $('n-s1').textContent = state.data.system1.length; $('n-s2').textContent = state.data.system2.length; $('n-s3').textContent = state.data.system3.length;
    await loadJournal(); render();
  }
  async function loadJournal() {
    if (STATIC) { try { state.journal = JSON.parse(localStorage.getItem('malkan.journal') || '[]'); } catch { state.journal = []; } }
    else state.journal = await (await fetch('/api/journal')).json();
    $('n-journal').textContent = state.journal.filter(t => t.status !== 'closed').length;
  }
  async function saveJournal(trade) {
    if (STATIC) {
      const now = new Date().toISOString();
      if (trade.id) { const i = state.journal.findIndex(t => t.id === trade.id); state.journal[i] = { ...state.journal[i], ...trade, updated_at: now }; }
      else state.journal.push({ id: Math.random().toString(36).slice(2, 12), created_at: now, updated_at: now, status: 'open', ...trade, rps: +(trade.entry - trade.sl).toFixed(2) });
      localStorage.setItem('malkan.journal', JSON.stringify(state.journal));
    } else await fetch('/api/journal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(trade) });
    await loadJournal();
  }
  async function deleteJournal(id) {
    if (STATIC) { state.journal = state.journal.filter(t => t.id !== id); localStorage.setItem('malkan.journal', JSON.stringify(state.journal)); }
    else await fetch('/api/journal/' + id, { method: 'DELETE' });
    await loadJournal();
  }
  async function loadHistory(symbol) {
    if (STATIC) return (await fetch(`data/mhist/${symbol}.json`)).json();
    return (await fetch(`/api/malkan/history/${symbol}`)).json();
  }

  // ---------------------------------------------------------------- tab definitions
  const RULES = {
    s1: '<b>System 1 · Trend following (weekly, large caps, wealth creation).</b> Buy when the weekly RSI(14) closes above 60. Stop loss = previous week\'s 20-week SMA. Exit on a weekly close below the 20 SMA. Add (pyramid) when the rising SMA reaches your entry — never average down. Filters: strong sector, near all-time high, cup formation.',
    s2: '<b>System 2 · Cup strategy (scan monthly, trade daily, any stock).</b> The cup must be at least <b>5 years wide</b> — the longer the cup, the stronger the move. Resolution point (RP) = cup neckline. Buy at RP + 4 %, SL at RP − 2 %. Level 1: sell a third at +10 / +20 / +30 % and trail the SL to the previous level. Level 2: keep the last third with a trailing SL. Max target = cup depth above RP. Expect 1:3 in 4–8 weeks.',
    s3: '<b>System 3 · Positional (monthly RSI near 40).</b> Monthly RSI(14) between 40 and 50 — never below 40. Buy above the high of the first green weekly candle after a dip. SL = weekly swing low. Target 1 = 1:3; target 2 = the monthly swing high. Look for asymmetric risk (1:10).',
    journal: '<b>Trade journal.</b> Entry date · entry · SL · risk per share · risk per trade · qty · exit price · exit date · P&L. Open trades show live P&L and rule alerts: stop hit, target reached (move the SL up), System-1 exit (weekly close below 20 SMA) and pyramid (SMA has reached your entry).',
    sectors: '<b>Sector strength (top-down filter).</b> Median 13-week and 4-week return of each Nifty 500 industry. System 1 says to start from the strongest sectors.',
  };
  const CHIPS = {
    s1: [['all', 'All'], ['buy', 'Fresh buys'], ['trend', 'In trend'], ['exit', 'Exits']],
    s2: [['all', 'All'], ['triggered', 'Triggered'], ['breakout', 'Above RP'], ['approaching', 'Approaching'], ['extended', 'Resolved earlier']],
    s3: [['all', 'All'], ['triggered', 'Triggered'], ['setup', 'Setup'], ['extended', 'Extended']],
    journal: [['all', 'All'], ['open', 'Open'], ['closed', 'Closed']],
    sectors: [],
  };
  const stockCell = (r) => `${r.symbol}<span class="nm">${r.name}</span>`;
  const ltpCell = (r) => { const q = state.quotes[r.token]; return fmt(q ? q.ltp : r.last_close); };
  const chgCell = (r) => { const q = state.quotes[r.token]; return q && q.change_pct != null ? `<span class="${cls(q.change_pct)}">${pct(q.change_pct, 2)}</span>` : '—'; };
  const sectorCell = (r) => `${r.industry}<span class="nm">rank ${r.sector_rank ?? '—'} of ${(state.data.sectors || []).length}</span>`;
  const COLS = {
    s1: [
      { key: 'symbol', label: 'Stock', render: stockCell },
      { key: 'state', label: 'State', render: r => `<span class="badge ${r.state}">${{ buy: 'Fresh buy', trend: 'In trend', exit: 'Exit' }[r.state]}</span>${r.week_complete ? '' : ' <span class="badge prov" title="Current week not finished">wk open</span>'}` },
      { key: 'ltp', label: 'LTP', num: true, render: ltpCell, get: livePx },
      { key: 'chg', label: 'Day %', num: true, render: chgCell, get: r => (state.quotes[r.token] || {}).change_pct ?? -999 },
      { key: 'rsi_w', label: 'Weekly RSI', num: true, render: r => `${fmt(r.rsi_w, 1)}<span class="nm">now ${fmt(r.rsi_w_now, 1)}</span>` },
      { key: 'sma20_w_prev', label: 'SL (prev-wk 20 SMA)', num: true, render: r => fmt(r.sma20_w_prev) },
      { key: 'above_sma', label: 'Above SMA', num: true, render: r => pct((livePx(r) - r.sma20_w) / r.sma20_w * 100), get: r => (livePx(r) - r.sma20_w) / r.sma20_w * 100 },
      { key: 'cross_date', label: 'RSI > 60 since', render: r => r.cross_date ? `${r.cross_date}<span class="nm">${r.weeks_since_cross} wk ago</span>` : '—' },
      { key: 'pct_from_ath', label: 'From ATH', num: true, render: r => pct(r.pct_from_ath) },
      { key: 'sector_rank', label: 'Sector', render: sectorCell },
    ],
    s2: [
      { key: 'symbol', label: 'Stock', render: stockCell },
      { key: 'status', label: 'Status', render: r => `<span class="badge ${r.status}">${{ triggered: 'Triggered', breakout: 'Above RP', approaching: 'Approaching', extended: 'Resolved earlier' }[r.status]}</span>${r.cup_completed ? '' : ' <span class="badge prov" title="Cup not yet resolved on the monthly chart">forming</span>'}${r.rim_at_data_start ? ' <span class="badge prov" title="The neckline is the first month of our 10-year history, so the cup may be even older">≥10 yrs?</span>' : ''}` },
      { key: 'ltp', label: 'LTP', num: true, render: ltpCell, get: livePx },
      { key: 'chg', label: 'Day %', num: true, render: chgCell, get: r => (state.quotes[r.token] || {}).change_pct ?? -999 },
      { key: 'rp', label: 'RP (neckline)', num: true, render: r => `${fmt(r.rp)}<span class="nm">${r.rp_date}</span>` },
      { key: 'vs_rp', label: 'vs RP', num: true, render: r => `<span class="${cls(livePx(r) - r.rp)}">${pct((livePx(r) - r.rp) / r.rp * 100)}</span>`, get: r => (livePx(r) - r.rp) / r.rp * 100 },
      { key: 'entry', label: 'Entry (+4%)', num: true, render: r => fmt(r.plan.entry), get: r => r.plan.entry },
      { key: 'sl', label: 'SL (−2%)', num: true, render: r => fmt(r.plan.sl), get: r => r.plan.sl },
      { key: 'years', label: 'Width / depth', num: true, render: r => `${r.years} yrs<span class="nm">${r.depth_pct}% deep</span>`, get: r => r.years },
      { key: 'trigger_date', label: 'Triggered', render: r => r.trigger_date ? `${r.trigger_date}<span class="nm">${r.days_since_trigger} d · ${pct(r.gain_pct)}</span>` : '—' },
      { key: 'sector_rank', label: 'Sector', render: sectorCell },
    ],
    s3: [
      { key: 'symbol', label: 'Stock', render: stockCell },
      { key: 'status', label: 'Status', render: r => `<span class="badge ${r.status}">${{ triggered: 'Triggered', setup: 'Setup', extended: 'Extended' }[r.status]}</span>` },
      { key: 'ltp', label: 'LTP', num: true, render: ltpCell, get: livePx },
      { key: 'chg', label: 'Day %', num: true, render: chgCell, get: r => (state.quotes[r.token] || {}).change_pct ?? -999 },
      { key: 'rsi_m', label: 'Monthly RSI', num: true, render: r => `${fmt(r.rsi_m, 1)}<span class="nm">now ${fmt(r.rsi_m_now, 1)}</span>` },
      { key: 'signal_date', label: 'Signal week', render: r => r.signal_date },
      { key: 'entry', label: 'Entry (candle high)', num: true, render: r => fmt(r.plan.entry), get: r => r.plan.entry },
      { key: 'to_entry', label: 'To entry', num: true, render: r => pct((r.plan.entry - livePx(r)) / livePx(r) * 100), get: r => (r.plan.entry - livePx(r)) / livePx(r) * 100 },
      { key: 'sl', label: 'SL (swing low)', num: true, render: r => fmt(r.plan.sl), get: r => r.plan.sl },
      { key: 't1', label: 'T1 (1:3)', num: true, render: r => fmt(r.plan.target1), get: r => r.plan.target1 },
      { key: 't2', label: 'T2 (monthly high)', num: true, render: r => `${fmt(r.plan.target2)}<span class="nm">${r.plan.rr_target2}:1</span>`, get: r => r.plan.rr_target2 },
      { key: 'sector_rank', label: 'Sector', render: sectorCell },
    ],
    journal: [
      { key: 'symbol', label: 'Stock', render: t => `${t.symbol}<span class="nm">System ${t.system}</span>` },
      { key: 'status', label: 'Status', render: t => `<span class="badge ${t.status}">${t.status}</span>` },
      { key: 'entry_date', label: 'Entry date', render: t => t.entry_date },
      { key: 'entry', label: 'Entry', num: true, render: t => fmt(t.entry) },
      { key: 'sl', label: 'SL', num: true, render: t => fmt(t.sl) },
      { key: 'rps', label: 'RPS', num: true, render: t => fmt(t.entry - t.sl) },
      { key: 'rpt', label: 'RPT', num: true, render: t => fmt((t.entry - t.sl) * t.qty, 0) },
      { key: 'qty', label: 'Qty', num: true, render: t => t.qty },
      { key: 'ltp', label: t => 'LTP / Exit', num: true, render: t => t.status === 'closed' ? fmt(t.exit_price) : fmt(livePxJ(t)) },
      { key: 'pnl', label: 'P&L ₹', num: true, render: t => { const v = pnl(t); return `<span class="${cls(v)}">${fmt(v, 0)}</span>`; }, get: t => pnl(t) },
      { key: 'pnlp', label: 'P&L %', num: true, render: t => { const v = pnl(t) / (t.entry * t.qty) * 100; return `<span class="${cls(v)}">${pct(v)}</span>`; } },
      { key: 'alerts', label: 'Alerts', render: t => alerts(t).map(a => `<span class="alert ${a.kind}">${a.text}</span>`).join('') || '—' },
      { key: 'exit_date', label: 'Exit date', render: t => t.exit_date || '—' },
      { key: 'act', label: '', render: t => `<button class="mini" data-edit="${t.id}">Edit</button>` },
    ],
    sectors: [
      { key: 'rank', label: '#', num: true, render: s => s.rank },
      { key: 'industry', label: 'Industry', render: s => s.industry },
      { key: 'stocks', label: 'Stocks', num: true, render: s => s.stocks },
      { key: 'ret_13w', label: '13-week median return', num: true, render: s => `<span class="sector-bar ${s.ret_13w < 0 ? 'neg' : ''}" style="width:${Math.min(120, Math.abs(s.ret_13w) * 6)}px"></span> <span class="${cls(s.ret_13w)}">${pct(s.ret_13w)}</span>` },
      { key: 'ret_4w', label: '4-week median return', num: true, render: s => `<span class="${cls(s.ret_4w)}">${pct(s.ret_4w)}</span>` },
    ],
  };
  const livePxJ = (t) => { const q = state.quotes[t.isin]; return q ? q.ltp : t.entry; };
  const pnl = (t) => ((t.status === 'closed' ? t.exit_price : livePxJ(t)) - t.entry) * t.qty;
  function alerts(t) {
    if (t.status === 'closed') return [];
    const out = [], px = livePxJ(t);
    if (px <= t.sl) out.push({ kind: 'bad', text: 'SL hit' });
    const targets = (t.targets || []).map(Number).filter(Boolean);
    targets.forEach((tg, i) => { if (px >= tg) out.push({ kind: 'good', text: `T${i + 1} ${fmt(tg, 0)} reached` }); });
    if (String(t.system) === '1') {
      const r = (state.data.system1 || []).find(x => x.symbol === t.symbol);
      if (r) { if (r.state === 'exit') out.push({ kind: 'bad', text: 'Weekly close below 20 SMA — exit' }); if (r.sma20_w >= t.entry) out.push({ kind: 'good', text: `20 SMA ${fmt(r.sma20_w, 0)} ≥ entry — pyramid` }); }
    }
    return out;
  }

  // ---------------------------------------------------------------- rendering
  function rowsForTab() {
    const d = state.data; if (!d) return [];
    if (state.tab === 'journal') return state.journal;
    if (state.tab === 'sectors') return d.sectors || [];
    return d[{ s1: 'system1', s2: 'system2', s3: 'system3' }[state.tab]] || [];
  }
  function currentRows() {
    let rows = rowsForTab();
    const q = $('f-search').value.trim().toLowerCase(), sec = $('f-sector').value;
    if (state.tab === 'journal') return rows.filter(t => (state.chip === 'all' || t.status === state.chip) && (!q || t.symbol.toLowerCase().includes(q)));
    if (state.tab === 'sectors') return rows;
    const key = state.tab === 's1' ? 'state' : 'status';
    return rows.filter(r => (state.chip === 'all' || r[key] === state.chip) && (!sec || r.industry === sec) && (!q || r.symbol.toLowerCase().includes(q) || r.name.toLowerCase().includes(q)));
  }
  function sorted(rows) {
    if (!state.sort) return rows;
    const col = COLS[state.tab].find(c => c.key === state.sort.key); if (!col) return rows;
    const get = col.get || ((r) => r[col.key]);
    return rows.slice().sort((a, b) => { const x = get(a), y = get(b); return (x > y ? 1 : x < y ? -1 : 0) * state.sort.dir; });
  }
  function render() {
    if (!state.data) return;
    $('rules').innerHTML = RULES[state.tab];
    const counts = {}; rowsForTab().forEach(r => { const k = state.tab === 'journal' ? r.status : state.tab === 's1' ? r.state : r.status; counts[k] = (counts[k] || 0) + 1; });
    $('chips').innerHTML = CHIPS[state.tab].map(([k, l]) => `<span class="chip ${state.chip === k ? 'active' : ''}" data-chip="${k}">${l}<span class="n">${k === 'all' ? rowsForTab().length : (counts[k] || 0)}</span></span>`).join('');
    $('chips').querySelectorAll('.chip').forEach(c => c.onclick = () => { state.chip = c.dataset.chip; render(); });
    $('f-sector').parentElement.classList.toggle('hidden', state.tab === 'journal' || state.tab === 'sectors');
    renderTiles();
    const cols = COLS[state.tab];
    $('grid-head').innerHTML = '<tr>' + cols.map(c => `<th class="${c.num ? 'num' : ''}" data-key="${c.key}">${typeof c.label === 'function' ? c.label() : c.label}${state.sort && state.sort.key === c.key ? `<span class="arrow">${state.sort.dir > 0 ? '▲' : '▼'}</span>` : ''}</th>`).join('') + '</tr>';
    $('grid-head').querySelectorAll('th').forEach(th => th.onclick = () => { const k = th.dataset.key; state.sort = { key: k, dir: state.sort && state.sort.key === k ? -state.sort.dir : 1 }; render(); });
    const rows = sorted(currentRows());
    $('empty').classList.toggle('hidden', rows.length > 0);
    const id = (r) => r.id || r.symbol || r.industry;
    $('grid-body').innerHTML = rows.map(r => `<tr data-id="${id(r)}" class="${state.selected && state.selected.symbol === r.symbol && state.tab !== 'sectors' ? 'sel' : ''}">` + cols.map(c => `<td class="${c.num ? 'num' : ''}" data-col="${c.key}">${c.render(r)}</td>`).join('') + '</tr>').join('');
    $('grid-body').querySelectorAll('tr').forEach(tr => tr.onclick = (ev) => {
      if (ev.target.dataset.edit) { editTrade(state.journal.find(t => t.id === ev.target.dataset.edit)); return; }
      const r = rows.find(x => id(x) === tr.dataset.id); if (!r || state.tab === 'sectors') return;
      if (state.tab === 'journal') { const sig = findSignal(r.symbol); select(sig || { symbol: r.symbol, name: r.name || r.symbol, token: r.isin, isin: r.isin, last_close: r.entry, _journal: r, plan: { entry: r.entry, sl: r.sl } }); }
      else select(r);
    });
  }
  function findSignal(symbol) { for (const k of ['system1', 'system2', 'system3']) { const r = (state.data[k] || []).find(x => x.symbol === symbol); if (r) return r; } return null; }
  function patchRows(changed) {
    const rows = currentRows(); const cols = COLS[state.tab]; if (state.tab === 'sectors') return;
    const byId = {}; rows.forEach(r => byId[r.id || r.symbol] = r);
    const live = new Set(['ltp', 'chg', 'above_sma', 'vs_rp', 'to_entry', 'pnl', 'pnlp', 'alerts']);
    $('grid-body').querySelectorAll('tr').forEach(tr => {
      const r = byId[tr.dataset.id]; if (!r || !changed.has(r.token || r.isin)) return;
      tr.querySelectorAll('td').forEach(td => { const c = cols.find(k => k.key === td.dataset.col); if (c && live.has(c.key)) td.innerHTML = c.render(r); });
      tr.classList.remove('flash'); void tr.offsetWidth; tr.classList.add('flash');
    });
    if (state.tab === 'journal') renderTiles();
  }
  function renderTiles() {
    const d = state.data; let tiles = [];
    if (state.tab === 's1') { const s = d.system1; tiles = [['Fresh buys', s.filter(r => r.state === 'buy').length, 'RSI crossed 60 in the last 4 weeks'], ['In trend', s.filter(r => r.state === 'trend').length, 'RSI > 60 and above the 20 SMA'], ['Exits', s.filter(r => r.state === 'exit').length, 'closed below the 20 SMA this week'], ['Near ATH', s.filter(r => r.pct_from_ath > -5).length, 'within 5% of all-time high']]; }
    else if (state.tab === 's2') { const s = d.system2; tiles = [['Triggered', s.filter(r => r.status === 'triggered').length, 'crossed RP + 4% within 8 weeks'], ['Above RP', s.filter(r => r.status === 'breakout').length, 'waiting for the +4% filter'], ['Approaching', s.filter(r => r.status === 'approaching').length, 'within 20% below a 5-year-old neckline'], ['Resolved earlier', s.filter(r => r.status === 'extended').length, 'broke out more than 8 weeks ago — shows what the system produces']]; }
    else if (state.tab === 's3') { const s = d.system3; tiles = [['Triggered', s.filter(r => r.status === 'triggered').length, 'price crossed the signal high'], ['Setups', s.filter(r => r.status === 'setup').length, 'signal candle formed, waiting'], ['Asymmetric', s.filter(r => r.plan.rr_target2 >= 10).length, '≥ 1:10 to the monthly swing high'], ['Monthly RSI 40–50', s.length, 'of ' + d.scanned + ' scanned']]; }
    else if (state.tab === 'journal') { const o = state.journal.filter(t => t.status !== 'closed'), c = state.journal.filter(t => t.status === 'closed'); const open = o.reduce((a, t) => a + pnl(t), 0), realised = c.reduce((a, t) => a + pnl(t), 0); const risk = o.reduce((a, t) => a + (t.entry - t.sl) * t.qty, 0); tiles = [['Open trades', o.length, `${inr(o.reduce((a, t) => a + t.entry * t.qty, 0))} invested`], ['Open P&L', `<span class="${cls(open)}">${inr(open)}</span>`, 'at the latest price'], ['Realised P&L', `<span class="${cls(realised)}">${inr(realised)}</span>`, `${c.length} closed`], ['Risk on table', inr(risk), `${(risk / state.settings.capital * 100).toFixed(1)}% of capital`]]; }
    else { tiles = [['Strongest sector', (d.sectors[0] || {}).industry || '—', pct((d.sectors[0] || {}).ret_13w) + ' over 13 weeks'], ['Weakest sector', (d.sectors[d.sectors.length - 1] || {}).industry || '—', pct((d.sectors[d.sectors.length - 1] || {}).ret_13w) + ' over 13 weeks']]; }
    $('tiles').innerHTML = tiles.map(([k, v, s]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join('');
  }
  function renderIndices() {
    $('indices').innerHTML = Object.entries(state.indices).map(([tok, name]) => { const q = state.quotes[tok]; return `<div class="idx"><span class="n">${name}</span><span class="v">${q ? fmt(q.ltp) : '—'} <span class="${q ? cls(q.change_pct) : ''}">${q && q.change_pct != null ? pct(q.change_pct, 2) : ''}</span></span></div>`; }).join('');
  }

  // ---------------------------------------------------------------- detail: chart + plan
  function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function styleCharts() {
    const opts = { layout: { background: { color: cssVar('--surface') }, textColor: cssVar('--text-2'), fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif' }, grid: { vertLines: { color: cssVar('--grid') }, horzLines: { color: cssVar('--grid') } }, rightPriceScale: { borderColor: cssVar('--axis') }, timeScale: { borderColor: cssVar('--axis') }, crosshair: { vertLine: { color: cssVar('--muted') }, horzLine: { color: cssVar('--muted') } } };
    if (state.chart) state.chart.applyOptions(opts); if (state.rsiChart) state.rsiChart.applyOptions(opts);
    if (state.series.candles) state.series.candles.applyOptions({ upColor: cssVar('--candle-up'), downColor: cssVar('--candle-down'), wickUpColor: cssVar('--candle-up'), wickDownColor: cssVar('--candle-down'), borderVisible: false });
    if (state.series.sma) state.series.sma.applyOptions({ color: cssVar('--series-2') });
    if (state.series.rsi) state.series.rsi.applyOptions({ color: cssVar('--series-1') });
  }
  async function select(r) {
    state.selected = r;
    $('grid-body').querySelectorAll('tr').forEach(tr => tr.classList.toggle('sel', tr.dataset.id === r.symbol));
    $('detail-empty').classList.add('hidden'); $('detail-body').classList.remove('hidden');
    $('d-symbol').textContent = r.symbol; $('d-name').textContent = `${r.name}${r.industry ? ' · ' + r.industry : ''}`;
    const st = r.state || r.status || (r._journal ? 'open' : ''); const b = $('d-status'); b.textContent = st; b.className = 'badge ' + st;
    renderDetailPrice(); renderPlan(r); await drawChart(r);
  }
  function renderDetailPrice() {
    const r = state.selected; const q = state.quotes[r.token];
    $('d-ltp').textContent = '₹' + fmt(q ? q.ltp : r.last_close);
    const when = q && q.ts ? ` · ${q.ts.slice(5, 10)} ${q.ts.slice(11, 16)}` : '';
    $('d-chg').innerHTML = q ? `<span class="${cls(q.change_pct)}">${pct(q.change_pct, 2)}</span> vs prev close ${fmt(q.prev_close)}${when}<br>O ${fmt(q.open)} H ${fmt(q.high)} L ${fmt(q.low)}` : `last close ${r.last_date || ''}`;
  }
  function sysOf(r) { return r.state ? 1 : r.rp ? 2 : r.signal_date ? 3 : (r._journal ? +r._journal.system : 1); }
  function renderPlan(r) {
    const sys = sysOf(r); const p = r.plan || {}; const px = livePx(r);
    const entry = sys === 1 ? px : p.entry; const sl = p.sl; const rps = entry - sl; const R = rpt();
    const qty = rps > 0 ? Math.floor(R / rps) : 0; const invest = qty * entry;
    const kv = (k, v) => `<div><dt>${k}</dt><dd>${v}</dd></div>`;
    let html = `<h4>Trade plan · System ${sys}</h4><div class="kv">` +
      kv(sys === 1 ? 'Entry (current price)' : 'Entry', inr(entry)) + kv('Stop loss', inr(sl)) + kv('Risk per share', fmt(rps)) +
      kv('Risk per trade', inr(R)) + kv('Qty', qty ? qty.toLocaleString('en-IN') : '—') + kv('Investment', `${inr(invest)}<span class="nm">${(invest / state.settings.capital * 100).toFixed(1)}% of capital</span>`) + '</div>';
    if (rps <= 0 || px < sl) html += `<div class="warn">Price is at or below the stop loss — do not buy.</div>`;
    let targets = [];
    if (sys === 1) {
      html += `<div class="note">Exit on a weekly close below the 20-week SMA (${fmt(r.sma20_w)}). Pyramid: add a position when the 20 SMA rises to ${inr(entry)}${r.sma20_w >= entry ? ' — <b>it has</b>' : ''}. ${r.pct_from_ath > -5 ? 'Near its all-time high ✓' : `${pct(r.pct_from_ath)} from its all-time high`}. Sector rank ${r.sector_rank ?? '—'}.</div>`;
    } else if (sys === 2) {
      const third = Math.floor(qty / 3);
      const rows = p.level1.map((t, i) => `<tr><td>Level-1 target ${i + 1} (+${(i + 1) * 10}%)</td><td class="num">${fmt(t)}</td><td class="num">sell ${third}</td><td>${i === 0 ? `move SL to entry ${fmt(entry)}` : `move SL to ${fmt(p.level1[i - 1])}`}</td></tr>`).join('');
      html += `<table><thead><tr><th>Level</th><th class="num">Price</th><th class="num">Action</th><th>Then</th></tr></thead><tbody>${rows}<tr><td>Level 2 — keep last third</td><td class="num">${fmt(p.max_target)}</td><td class="num">max target</td><td>trail: at +40% SL→+30%, +50%→+40%, +60%→+50%…</td></tr></tbody></table>`;
      html += `<div class="note">Cup ${r.years} years wide and ${r.depth_pct}% deep, neckline ${fmt(r.rp)} (${r.rp_date})${r.rim_at_data_start ? ' — at the start of our data, so possibly older' : ''}. Level-3 variant: +20 / +40 / +60% (${p.level3.map(x => fmt(x)).join(' / ')}). Reward at level-1 target 3: ${((p.level1[2] - entry) / rps).toFixed(1)}:1.</div>`;
      targets = p.level1;
    } else {
      html += `<table><thead><tr><th>Target</th><th class="num">Price</th><th class="num">Reward</th></tr></thead><tbody><tr><td>Target 1 (1:3)</td><td class="num">${fmt(p.target1)}</td><td class="num">3:1</td></tr><tr><td>Target 2 (monthly swing high)</td><td class="num">${fmt(p.target2)}</td><td class="num">${p.rr_target2}:1</td></tr></tbody></table>`;
      html += `<div class="note">Signal candle week ${r.signal_date} (high ${fmt(r.signal_high)}, low ${fmt(r.signal_low)}); monthly RSI ${r.rsi_m}. ${p.rr_target2 >= 10 ? 'Asymmetric risk (≥ 1:10) ✓' : ''}</div>`;
      targets = [p.target1, p.target2];
    }
    if (!r._journal) html += `<button id="plan-add">Add to journal</button>`;
    $('plan').innerHTML = html;
    const btn = $('plan-add'); if (btn) btn.onclick = () => openForm({ symbol: r.symbol, name: r.name, isin: r.isin, system: sys, entry: +entry.toFixed(2), sl: +sl.toFixed(2), qty, rpt: R, targets, entry_date: today() });
  }
  async function drawChart(r) {
    let h; try { h = await loadHistory(r.symbol); } catch { h = null; }
    if (!state.chart) {
      state.chart = LightweightCharts.createChart($('chart'), { height: 300, autoSize: true, timeScale: { timeVisible: false }, handleScroll: true, handleScale: true });
      state.series.candles = state.chart.addCandlestickSeries({ priceFormat: { type: 'price', precision: 2, minMove: 0.05 } });
      state.series.sma = state.chart.addLineSeries({ lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      state.rsiChart = LightweightCharts.createChart($('rsi-chart'), { height: 120, autoSize: true, timeScale: { visible: false }, handleScroll: false, handleScale: false, rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 } } });
      state.series.rsi = state.rsiChart.addLineSeries({ lineWidth: 2, priceLineVisible: false, lastValueVisible: true });
      state.series.rsi.createPriceLine({ price: 60, color: cssVar('--up'), lineWidth: 1, lineStyle: 2, title: '60' });
      state.series.rsi.createPriceLine({ price: 40, color: cssVar('--critical'), lineWidth: 1, lineStyle: 2, title: '40' });
      state.chart.timeScale().subscribeVisibleLogicalRangeChange(rg => { if (rg) state.rsiChart.timeScale().setVisibleLogicalRange(rg); });
      styleCharts();
    }
    const bars = (h && h[state.tf]) || [];
    state.series.candles.setData(bars.map(c => ({ time: c[0], open: c[1], high: c[2], low: c[3], close: c[4] })));
    state.series.sma.setData(state.tf === 'D' ? [] : bars.filter(c => c[7] != null).map(c => ({ time: c[0], value: c[7] })));
    state.series.rsi.setData(state.tf === 'D' ? [] : bars.filter(c => c[6] != null).map(c => ({ time: c[0], value: c[6] })));
    $('rsi-chart').style.display = state.tf === 'D' ? 'none' : '';
    for (const l of (state._lines || [])) state.series.candles.removePriceLine(l); state._lines = [];
    const add = (price, title) => price && state._lines.push(state.series.candles.createPriceLine({ price, color: cssVar('--series-7'), lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title }));
    const p = r.plan || {}; const sys = sysOf(r);
    if (sys === 1) { add(p.sl, 'SL'); } else if (sys === 2) { add(r.rp, 'RP'); add(p.entry, 'entry'); add(p.sl, 'SL'); (p.level1 || []).forEach((t, i) => add(t, `T${i + 1}`)); } else { add(p.entry, 'entry'); add(p.sl, 'SL'); add(p.target1, 'T1'); add(p.target2, 'T2'); }
    if (r._journal) { add(r._journal.entry, 'entry'); add(r._journal.sl, 'SL'); }
    const n = bars.length; const span = state.tf === 'M' ? 60 : state.tf === 'W' ? 80 : 180;
    state.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - span), to: n + 2 });
  }

  // ---------------------------------------------------------------- journal form
  function openForm(t) {
    state.editing = t.id || null;
    $('jf-title').textContent = t.id ? `Edit trade · ${t.symbol}` : `Add trade · ${t.symbol}`;
    $('jf-symbol').value = t.symbol; $('jf-system').value = String(t.system || 1); $('jf-date').value = t.entry_date || today();
    $('jf-entry').value = t.entry ?? ''; $('jf-sl').value = t.sl ?? ''; $('jf-qty').value = t.qty ?? '';
    $('jf-targets').value = (t.targets || []).map(x => fmt(x)).join(', ').replace(/,(\d)/g, '$1'); $('jf-notes').value = t.notes || '';
    $('jf-exit-date').value = t.exit_date || ''; $('jf-exit').value = t.exit_price ?? '';
    $('jf-delete').classList.toggle('hidden', !t.id); $('journal-form').dataset.meta = JSON.stringify({ name: t.name, isin: t.isin });
    $('journal-form').classList.remove('hidden'); $('journal-form').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  function editTrade(t) { if (t) openForm(t); }
  $('jf-cancel').onclick = () => $('journal-form').classList.add('hidden');
  $('jf-save').onclick = async () => {
    const meta = JSON.parse($('journal-form').dataset.meta || '{}');
    const exitPrice = $('jf-exit').value ? +$('jf-exit').value : null;
    const trade = { id: state.editing || undefined, symbol: $('jf-symbol').value, name: meta.name, isin: meta.isin, system: +$('jf-system').value,
      entry_date: $('jf-date').value, entry: +$('jf-entry').value, sl: +$('jf-sl').value, qty: +$('jf-qty').value, rpt: rpt(),
      targets: $('jf-targets').value.split(',').map(s => +s.replace(/[^\d.]/g, '')).filter(Boolean), notes: $('jf-notes').value,
      exit_date: $('jf-exit-date').value || null, exit_price: exitPrice, status: exitPrice ? 'closed' : 'open' };
    if (!trade.entry || !trade.sl || !trade.qty) { alert('Entry, stop loss and qty are required.'); return; }
    await saveJournal(trade); $('journal-form').classList.add('hidden');
    state.tab = 'journal'; state.chip = 'all'; setTab(); render(); if (STATIC) pollUpstox();
  };
  $('jf-delete').onclick = async () => { if (state.editing && confirm('Delete this trade?')) { await deleteJournal(state.editing); $('journal-form').classList.add('hidden'); render(); } };

  // ---------------------------------------------------------------- wiring
  function setTab() { document.querySelectorAll('#tabs .tab').forEach(x => x.classList.toggle('active', x.dataset.tab === state.tab)); }
  document.querySelectorAll('#tabs .tab').forEach(t => t.onclick = () => { state.tab = t.dataset.tab; state.chip = 'all'; state.sort = null; setTab(); render(); if (STATIC) pollUpstox(); });
  document.querySelectorAll('.seg').forEach(s => s.onclick = () => { document.querySelectorAll('.seg').forEach(x => x.classList.remove('active')); s.classList.add('active'); state.tf = s.dataset.tf; if (state.selected) drawChart(state.selected); });
  $('f-search').oninput = render; $('f-sector').onchange = render;
  $('refresh-btn').onclick = () => STATIC ? pollUpstox() : loadStatus();
  $('rescan-btn').onclick = async () => { $('rescan-btn').disabled = true; $('rescan-btn').textContent = 'Scanning…'; state._wasScanning = true; await fetch('/api/malkan/rescan', { method: 'POST' }); };

  renderSettings();
  if (STATIC) {
    $('rescan-btn').classList.add('hidden'); $('static-note').classList.remove('hidden');
    if (CFG.built_at) $('built-at').textContent = new Date(CFG.built_at).toLocaleString('en-IN');
    loadData().then(() => { loadStatus(); schedulePolling(); }); setInterval(loadStatus, 5000);
  } else {
    $('refresh-btn').classList.add('hidden');
    loadData().then(() => { connectWS(); }); loadStatus(); setInterval(loadStatus, 5000);
  }
})();
