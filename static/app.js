/* Backtest Pro — Zero-Flicker Quant Workbench SPA */

const state = {
  init: null,
  mode: 'strategy',          // 'strategy' | 'regime' | 'allocation'
  tickers: ['069500'],
  allocWeights: { SPY: 30, TLT: 40, IEF: 15, GLD: 15 },
  entryNumbers: [],
  exitNumbers: [],
  linkedSlots: [],
  activeFormulaInput: null,  // HTMLTextAreaElement
  acSelectedIdx: 0,
  acMatches: [],
  lastResult: null,
  lastCompare: null,
  lastOptimize: null,
  chartUnit: 'pct',          // 'pct' | 'krw'
  equityZoom: 'ALL',         // '6M' | '1Y' | '3Y' | 'ALL'
  candleTf: 'auto',          // 'auto' | 'D' | 'W' | 'M'
  candleZoom: 'AUTO',        // '3M' | '6M' | '1Y' | 'AUTO' | 'ALL'
  tradeFilter: 'all',        // 'all' | 'win' | 'loss'
  hmMetric: 'sharpe',        // 'sharpe' | 'ret'
  mcHorizonDays: 252,
  snippetTarget: 'entry',
  snippetJoin: 'AND',
  snippetTf: 'M',
  uiMode: 'simple',          // 'simple' | 'pro'
  equityOverlayIndexSymbol: '',
  equityOverlayIndexData: null,
  nationalIndexSymbol: '^KS11',
  nationalIndexUnit: 'price', // 'price' | 'pct'
  nationalIndexFilter: 'all', // 'all' | 'kr' | 'macro' | 'shock'
  nationalIndexData: null,
  indexEventsCache: {},
  guruData: null,
  guruFilter: 'all',
  experiments: [],
  baselineExpId: null,
  expSeq: 1,
  formulaHistory: [],
  formulaHistoryIdx: -1,
  applyingHistory: false,
  isDraggingTuner: false,
  tunerSeq: 0,
  debounceBacktestTimer: null,
  debounceInspectTimer: null,
  abortBacktest: null,
};

function toBase64Utf8(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = '';
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin);
}

function fromBase64Utf8(b64) {
  const bin = atob(b64);
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

function encodeShareHash() {
  const obj = {
    t: state.tickers,
    e: $('#entry-input')?.value || '',
    x: $('#exit-input')?.value || '',
    s: $('#start-date')?.value || '',
    end: $('#end-date')?.value || '',
    sl: Number($('#stop-loss')?.value || 0),
    tp: Number($('#take-profit')?.value || 0),
    tr: Number($('#trailing-stop')?.value || 0),
    mtf: $('#mtf-close-only')?.checked !== false,
    ctf: state.candleTf || 'auto',
  };
  return 'f=' + toBase64Utf8(JSON.stringify(obj));
}

function restoreFromUrlHash() {
  const hash = window.location.hash || '';
  if (!hash.startsWith('#f=')) return false;
  try {
    const obj = JSON.parse(fromBase64Utf8(hash.slice(3)));
    if (!obj || typeof obj !== 'object') return false;
    if (Array.isArray(obj.t) && obj.t.length) {
      state.tickers = obj.t.map((x) => String(x));
      renderSelectedTickers();
    }
    setMode('strategy');
    $('#strategy-select').value = 'custom';
    onStrategySelectChange(false);
    if (typeof obj.e === 'string') $('#entry-input').value = obj.e;
    if (typeof obj.x === 'string') $('#exit-input').value = obj.x;
    if (obj.s) $('#start-date').value = obj.s;
    if (obj.end) $('#end-date').value = obj.end;
    if (obj.sl !== undefined) $('#stop-loss').value = obj.sl;
    if (obj.tp !== undefined) $('#take-profit').value = obj.tp;
    if (obj.tr !== undefined) $('#trailing-stop').value = obj.tr;
    if (obj.mtf !== undefined) $('#mtf-close-only').checked = !!obj.mtf;
    if (obj.ctf) {
      state.candleTf = obj.ctf;
      $$('#candle-tf-group .seg-btn').forEach((b) => b.classList.toggle('active', b.dataset.ctf === obj.ctf));
      $$('#equity-tf-group .seg-btn').forEach((b) => b.classList.toggle('active', b.dataset.etf === obj.ctf));
    }
    setStatus('🔗 공유 링크에서 수식을 복원했습니다', 'ok');
    return true;
  } catch (e) {
    return false;
  }
}

function updateHistoryButtons() {
  const u = $('#btn-formula-undo');
  const r = $('#btn-formula-redo');
  if (u) u.disabled = state.formulaHistoryIdx <= 0;
  if (r) r.disabled = state.formulaHistoryIdx < 0 || state.formulaHistoryIdx >= state.formulaHistory.length - 1;
}

function pushFormulaHistory() {
  if (state.applyingHistory) return;
  const en = ($('#entry-input')?.value ?? '');
  const ex = ($('#exit-input')?.value ?? '');
  const cur = state.formulaHistory[state.formulaHistoryIdx];
  if (cur && cur.en === en && cur.ex === ex) {
    updateHistoryButtons();
    return;
  }
  state.formulaHistory = state.formulaHistory.slice(0, state.formulaHistoryIdx + 1);
  state.formulaHistory.push({ en, ex });
  if (state.formulaHistory.length > 40) {
    state.formulaHistory.shift();
  }
  state.formulaHistoryIdx = state.formulaHistory.length - 1;
  updateHistoryButtons();
}

async function restoreFormulaHistoryIdx(idx) {
  if (idx < 0 || idx >= state.formulaHistory.length) return;
  const item = state.formulaHistory[idx];
  state.applyingHistory = true;
  try {
    setMode('strategy');
    $('#strategy-select').value = 'custom';
    onStrategySelectChange(false);
    $('#entry-input').value = item.en;
    $('#exit-input').value = item.ex;
    state.formulaHistoryIdx = idx;
    updateHistoryButtons();
    await inspectFormula(false);
    scheduleAutoRun(120);
  } finally {
    state.applyingHistory = false;
  }
}

function undoFormula() {
  if (state.formulaHistoryIdx > 0) restoreFormulaHistoryIdx(state.formulaHistoryIdx - 1);
}

function redoFormula() {
  if (state.formulaHistoryIdx < state.formulaHistory.length - 1) restoreFormulaHistoryIdx(state.formulaHistoryIdx + 1);
}

function findFormulaNumberSpans(expr) {
  const spans = [];
  const re = /\d+(?:\.\d+)?/g;
  let m;
  while ((m = re.exec(expr)) !== null) {
    const s = m.index;
    const e = s + m[0].length;
    const prev = s > 0 ? expr[s - 1] : '';
    const next = e < expr.length ? expr[e] : '';
    if (/[A-Za-z0-9_.]/.test(prev) || /[A-Za-z0-9_]/.test(next)) continue;
    spans.push({ start: s, end: e, raw: m[0] });
  }
  return spans;
}

function replaceFormulaNumbersClient(expr, updates, fallbackNums = []) {
  const entries = Object.entries(updates || {});
  if (!entries.length) return expr;
  const spans = findFormulaNumberSpans(expr);
  const useSpans = spans.length === fallbackNums.length ? spans : fallbackNums;
  const edits = [];
  entries.forEach(([idxStr, newStr]) => {
    const idx = Number(idxStr);
    const sp = useSpans[idx];
    if (sp && sp.start !== undefined && sp.end !== undefined) {
      edits.push({ start: sp.start, end: sp.end, val: String(newStr) });
    }
  });
  edits.sort((a, b) => b.start - a.start);
  let out = expr;
  edits.forEach((ed) => {
    out = out.slice(0, ed.start) + ed.val + out.slice(ed.end);
  });
  return out;
}

function populateOptSlots(slots) {
  const s1 = $('#opt-slot1');
  const s2 = $('#opt-slot2');
  const bar = $('#opt-slots-bar');
  if (!s1 || !s2) return;
  const list = Array.isArray(slots) ? slots : [];
  const prev1 = s1.value;
  const prev2 = s2.value;
  s1.innerHTML = '';
  s2.innerHTML = '';
  if (!list.length) {
    s1.innerHTML = '<option value="">변수 없음</option>';
    s2.innerHTML = '<option value="">(해당 없음)</option>';
    if (bar) bar.classList.add('hidden');
    return;
  }
  if (bar) bar.classList.remove('hidden');
  list.forEach((sl) => {
    const o1 = document.createElement('option');
    o1.value = String(sl.index);
    o1.textContent = `#${sl.index + 1} ${sl.label}`;
    s1.appendChild(o1);

    const o2 = document.createElement('option');
    o2.value = String(sl.index);
    o2.textContent = `#${sl.index + 1} ${sl.label}`;
    s2.appendChild(o2);
  });
  const noneOpt = document.createElement('option');
  noneOpt.value = '';
  noneOpt.textContent = '1차원 단일 탐색 (사용 안함)';
  s2.appendChild(noneOpt);

  if (prev1 && list.some((sl) => String(sl.index) === prev1)) s1.value = prev1;
  else s1.value = String(list[0].index);

  if (prev2 !== '' && list.some((sl) => String(sl.index) === prev2) && prev2 !== s1.value) s2.value = prev2;
  else if (list.length > 1) s2.value = String(list[1].index);
  else s2.value = '';
}

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const PLOT_LAYOUT_BASE = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(9,14,26,0.55)',
  font: { family: "'Noto Sans KR', 'Inter', sans-serif", color: '#CBD5E1', size: 11.5 },
  margin: { l: 58, r: 22, t: 24, b: 36 },
  hovermode: 'x unified',
  legend: { orientation: 'h', y: 1.08, x: 0, bgcolor: 'rgba(0,0,0,0)' },
  xaxis: { gridcolor: 'rgba(148,163,184,0.09)', zerolinecolor: 'rgba(148,163,184,0.18)' },
  yaxis: { gridcolor: 'rgba(148,163,184,0.09)', zerolinecolor: 'rgba(148,163,184,0.18)' },
};
const PLOT_CFG = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ['lasso2d', 'select2d'] };

function isMobileView() {
  return window.innerWidth <= 900;
}

function buildPlotLayout(layout) {
  if (!isMobileView()) return layout;
  const out = {
    ...layout,
    margin: { l: 40, r: 8, t: 22, b: 26 },
    dragmode: 'pan',
    font: { ...(layout.font || PLOT_LAYOUT_BASE.font), size: 10 },
    legend: {
      ...(layout.legend || {}),
      orientation: 'h',
      y: 1.08,
      x: 0,
      font: { size: 9.5 },
      bgcolor: 'rgba(0,0,0,0)',
    },
  };
  ['yaxis', 'yaxis2', 'yaxis3'].forEach((yk) => {
    if (out[yk]) {
      out[yk] = { ...out[yk], title: '', tickfont: { size: 9.5 }, automargin: true };
    }
  });
  if (out.xaxis) {
    out.xaxis = { ...out.xaxis, tickfont: { size: 9.5 }, nticks: 5 };
  }
  return out;
}

function buildPlotCfg() {
  return { ...PLOT_CFG, displayModeBar: !isMobileView() };
}

function toggleChartFullscreen(cardSelector, chartId, btnSelector) {
  const card = $(cardSelector);
  const btn = $(btnSelector);
  const el = document.getElementById(chartId);
  if (!card) return;
  const on = card.classList.toggle('chart-fullscreen-card');
  document.body.style.overflow = on ? 'hidden' : '';
  if (btn) btn.textContent = on ? '✕ 닫기' : '⛶ 크게';
  setTimeout(() => {
    if (el && el.data) Plotly.Plots.resize(el);
  }, 60);
}

function shiftDateDays(dateStr, days) {
  const d = new Date(dateStr + 'T00:00:00Z');
  if (Number.isNaN(d.getTime())) return dateStr;
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function computeVisibleOhlcRange(dates, highs, lows, zoomMode, tf = 'D') {
  const nBars = Array.isArray(dates) ? dates.length : 0;
  if (!nBars) return null;
  let startIdx = 0;
  if (zoomMode === 'LAST_140') {
    if (nBars > 160) startIdx = nBars - 140;
  } else if (zoomMode === 'AUTO') {
    const cap = isMobileView() ? 75 : 150;
    if (nBars > cap) startIdx = nBars - cap;
  } else if (zoomMode && zoomMode !== 'ALL' && nBars > 10) {
    const daysBack = zoomMode === '3M' ? 92 : (zoomMode === '6M' ? 184 : (zoomMode === '1Y' ? 366 : 1096));
    const cutoff = shiftDateDays(dates[nBars - 1], -daysBack);
    const found = dates.findIndex((d) => d >= cutoff);
    if (found > 0) startIdx = found;
  }
  if (startIdx > 0 && nBars - startIdx < 12) {
    startIdx = Math.max(0, nBars - 12);
  }
  if (startIdx <= 0 || startIdx >= nBars) return null;
  const padDays = tf === 'M' ? 20 : (tf === 'W' ? 7 : 2);
  const xRange = [dates[startIdx], shiftDateDays(dates[nBars - 1], padDays)];
  let yLo = Infinity;
  let yHi = -Infinity;
  for (let i = startIdx; i < nBars; i += 1) {
    const lo = Number(lows[i]);
    const hi = Number(highs[i]);
    if (Number.isFinite(lo) && lo < yLo) yLo = lo;
    if (Number.isFinite(hi) && hi > yHi) yHi = hi;
  }
  let yRange = null;
  if (Number.isFinite(yLo) && Number.isFinite(yHi) && yHi >= yLo) {
    const pad = Math.max((yHi - yLo) * 0.1, yHi * 0.015);
    yRange = [yLo - pad, yHi + pad];
  }
  return { xRange, yRange };
}

// ═══════════════════════════════════════════════════════════════
// 포맷터 & 유틸
// ═══════════════════════════════════════════════════════════════

function fmtKRW(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '-';
  const num = Math.round(Number(v));
  return num.toLocaleString('ko-KR') + '원';
}

function fmtCompactKRW(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '-';
  const num = Number(v);
  const abs = Math.abs(num);
  if (abs >= 1e8) return (num / 1e8).toFixed(2) + '억원';
  if (abs >= 1e4) return Math.round(num / 1e4).toLocaleString('ko-KR') + '만원';
  return Math.round(num).toLocaleString('ko-KR') + '원';
}

function fmtPct(v, signed = true) {
  if (v === null || v === undefined || Number.isNaN(v)) return '-';
  const n = Number(v);
  return (signed && n > 0 ? '+' : '') + n.toFixed(2) + '%';
}

function setStatus(text, type = 'ok') {
  const el = $('#status-pill');
  el.textContent = text;
  el.className = 'status-pill' + (type === 'loading' ? ' loading' : type === 'error' ? ' error' : '');
}

function showError(msg) {
  const b = $('#error-banner');
  if (!msg) {
    b.classList.add('hidden');
    b.textContent = '';
  } else {
    b.textContent = '⚠️ ' + msg;
    b.classList.remove('hidden');
  }
}

async function apiJson(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || data.error || `HTTP ${res.status}`);
  }
  return data;
}

// ═══════════════════════════════════════════════════════════════
// 초기화
// ═══════════════════════════════════════════════════════════════

function setMobilePane(pane, tabId = null) {
  const p = pane === 'right' ? 'right' : 'left';
  const wasRight = document.body.classList.contains('mobile-view-right');
  document.body.classList.toggle('mobile-view-left', p === 'left');
  document.body.classList.toggle('mobile-view-right', p === 'right');

  if (p === 'right' && tabId) {
    $$('.dash-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tabId));
    $$('.tab-pane').forEach((el) => el.classList.toggle('active', el.id === `tab-${tabId}`));
  }

  const activeTabId = $$('.dash-tab').find((b) => b.classList.contains('active'))?.dataset.tab || 'summary';
  $$('#mobile-bottom-nav .mobile-nav-btn[data-mpane]').forEach((b) => {
    if (b.dataset.mpane === 'left') {
      b.classList.toggle('active', p === 'left');
    } else {
      const btnTab = b.dataset.mtab || 'summary';
      b.classList.toggle('active', p === 'right' && activeTabId === btnTab);
    }
  });

  if (p !== 'right' || !wasRight || tabId) {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  if (p === 'right') {
    setTimeout(() => {
      $$('.tab-pane.active .plotly-box').forEach((el) => {
        if (el.data) Plotly.Plots.resize(el);
      });
    }, 60);
  }
}

async function copyAccessText(btn, text) {
  if (!text) return;
  let copied = false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      copied = true;
    }
  } catch (e) {}
  if (!copied) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try { copied = document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(ta);
  }
  if (btn) {
    const orig = btn.textContent;
    btn.textContent = copied ? '✅ 복사됨' : '복사 실패';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  }
}

function renderAccessInfo(info) {
  if (!info) return;
  const lanInp = $('#lan-url-input');
  const lanQr = $('#lan-qr-img');
  if (lanInp) lanInp.value = info.lan_url || info.local_url || '';
  if (lanQr && (info.lan_url || info.local_url)) {
    const targetLan = info.lan_url || info.local_url;
    lanQr.src = `https://api.qrserver.com/v1/create-qr-code/?size=160x160&margin=6&data=${encodeURIComponent(targetLan)}`;
  }

  const badge = $('#tunnel-status-badge');
  const btn = $('#btn-toggle-tunnel');
  const box = $('#public-url-box');
  const errEl = $('#tunnel-error-msg');
  const pubInp = $('#public-url-input');
  const pubQr = $('#public-qr-img');

  const running = info.tunnel_status === 'running' && !!info.public_url;
  if (badge) {
    badge.className = 'live-badge ' + (running ? 'ok' : (info.tunnel_status === 'error' ? 'err' : ''));
    badge.textContent = running ? '켜짐 (HTTPS 접속 가능)' : (info.tunnel_status === 'error' ? '오류' : '꺼짐');
  }
  if (btn) {
    btn.disabled = false;
    btn.textContent = running ? '🛑 외부 접속 끄기' : '🚀 외부 HTTPS 링크 1클릭 켜기';
    btn.classList.toggle('btn-primary', !running);
    btn.classList.toggle('btn-outline', running);
  }
  if (errEl) {
    if (info.tunnel_error && !running) {
      errEl.textContent = info.tunnel_error;
      errEl.classList.remove('hidden');
    } else {
      errEl.classList.add('hidden');
    }
  }
  if (box) box.classList.toggle('hidden', !running);
  if (running) {
    if (pubInp) pubInp.value = info.public_url;
    if (pubQr) pubQr.src = `https://api.qrserver.com/v1/create-qr-code/?size=160x160&margin=6&data=${encodeURIComponent(info.public_url)}`;
  }
}

async function refreshAccessInfo() {
  try {
    const info = await apiJson('/api/access/info');
    renderAccessInfo(info);
    return info;
  } catch (e) {
    return null;
  }
}

function isLocalAdminClient() {
  const h = (window.location.hostname || '').toLowerCase();
  const isLocalHost = h === 'localhost' || h === '127.0.0.1' || h === '::1' || h === '[::1]';
  if (!isLocalHost) return false;
  return Boolean(state.init && state.init.is_local_admin === true);
}

function updateAccessButtonVisibility() {
  const allow = isLocalAdminClient();
  const btn = $('#btn-open-access');
  if (btn) btn.classList.toggle('hidden', !allow);
  if (!allow) $('#access-modal')?.classList.add('hidden');
}

async function openAccessModal() {
  if (!isLocalAdminClient()) return;
  $('#access-modal').classList.remove('hidden');
  await Promise.all([refreshAccessInfo(), refreshCloudConfig()]);
}

function renderCloudConfig(cfg) {
  if (!cfg) return;
  const badge = $('#cloud-sync-badge');
  const tokInp = $('#cloud-gh-token');
  const repoInp = $('#cloud-gh-repo');
  const autoChk = $('#cloud-auto-sync');
  const urlBox = $('#cloud-url-box');
  const urlInp = $('#cloud-url-input');
  const repoLink = $('#cloud-repo-link');
  const qrImg = $('#cloud-qr-img');
  const msgEl = $('#cloud-status-msg');
  const renderInp = $('#cloud-render-url');
  const renderBtn = $('#btn-open-render-deploy');

  const renderUrl = (cfg.render_url || cfg.cloud_url || '').trim();
  const connected = !!renderUrl;
  const hasGithub = !!(cfg.github_repo_url || cfg.has_token);
  if (badge) {
    badge.className = 'live-badge ' + (connected ? 'ok' : (hasGithub ? 'neu' : ''));
    badge.textContent = cfg.in_cloud
      ? '☁️ Render 클라우드에서 실행 중'
      : (connected
        ? '🟢 Render 24시 서버 · 자동동기화 켜짐'
        : (hasGithub ? 'GitHub 연결됨 · Render 주소 대기' : '미연결'));
  }
  if (tokInp && cfg.has_token && !tokInp.value) {
    tokInp.placeholder = `저장된 토큰 (${cfg.token_preview || '설정됨'}) — 변경할 때만 새로 입력`;
  }
  if (repoInp && cfg.github_repo && !repoInp.value) {
    const parts = cfg.github_repo.split('/');
    repoInp.value = parts[parts.length - 1] || 'backtest-pro';
  }
  if (renderInp && renderUrl && !renderInp.value) {
    renderInp.value = renderUrl;
  }
  if (renderBtn && cfg.github_repo_url) {
    renderBtn.href = `https://render.com/deploy?repo=${encodeURIComponent(cfg.github_repo_url)}`;
    renderBtn.classList.remove('hidden');
  }
  if (autoChk) autoChk.checked = cfg.auto_sync_strategies !== false;
  if (urlBox) urlBox.classList.toggle('hidden', !connected);
  if (connected) {
    if (urlInp) urlInp.value = renderUrl;
    if (repoLink) repoLink.href = cfg.github_repo_url || '#';
    if (qrImg) qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=160x160&margin=6&data=${encodeURIComponent(renderUrl)}`;
  }
  if (msgEl && cfg.last_sync_note) {
    msgEl.textContent = `ℹ️ ${cfg.last_sync_note}${cfg.last_sync_at ? ' (' + cfg.last_sync_at + ')' : ''}`;
    msgEl.style.color = '#94A3B8';
    msgEl.classList.remove('hidden');
  }
}

async function refreshCloudConfig() {
  try {
    const cfg = await apiJson('/api/cloud/config');
    renderCloudConfig(cfg);
    return cfg;
  } catch (e) {
    return null;
  }
}

async function deployToGithubCloud() {
  const btn = $('#btn-github-deploy');
  const msgEl = $('#cloud-status-msg');
  const ghToken = ($('#cloud-gh-token')?.value || '').trim();
  const repoName = ($('#cloud-gh-repo')?.value || 'backtest-pro').trim() || 'backtest-pro';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Git 없이 GitHub 업로드 중...';
  }
  if (msgEl) {
    msgEl.textContent = '⏳ 소스 파일 전체를 GitHub 저장소로 올리는 중입니다...';
    msgEl.style.color = '#38BDF8';
    msgEl.classList.remove('hidden');
  }
  try {
    const res = await apiJson('/api/cloud/github-deploy', {
      method: 'POST',
      body: JSON.stringify({ gh_token: ghToken, repo_name: repoName }),
    });
    if (res.cloud) renderCloudConfig(res.cloud);
    const renderBtn = $('#btn-open-render-deploy');
    if (renderBtn && res.render_deploy_url) {
      renderBtn.href = res.render_deploy_url;
      renderBtn.classList.remove('hidden');
    }
    if (msgEl) {
      msgEl.textContent = `✅ GitHub(${res.repo}, ${res.files_uploaded}개 파일) 업로드 완료! 오른쪽 2단계 버튼으로 Render에 배포한 뒤 3단계에 주소를 연결하세요.`;
      msgEl.style.color = '#34D399';
      msgEl.classList.remove('hidden');
    }
  } catch (err) {
    if (msgEl) {
      msgEl.textContent = `⚠️ ${err.message}`;
      msgEl.style.color = '#FB7185';
      msgEl.classList.remove('hidden');
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '🐙 1단계: Git 없이 GitHub 1클릭 업로드';
    }
  }
}

async function saveRenderCloudUrl() {
  const msgEl = $('#cloud-status-msg');
  const rUrl = ($('#cloud-render-url')?.value || '').trim();
  if (!rUrl) return;
  try {
    const res = await apiJson('/api/cloud/config', {
      method: 'POST',
      body: JSON.stringify({ render_url: rUrl }),
    });
    renderCloudConfig(res);
    if (msgEl) {
      msgEl.textContent = `✅ Render 24시간 서버(${res.render_url}) 연결 및 전략 자동 동기화 연동 완료!`;
      msgEl.style.color = '#34D399';
      msgEl.classList.remove('hidden');
    }
  } catch (err) {
    if (msgEl) {
      msgEl.textContent = `⚠️ ${err.message}`;
      msgEl.style.color = '#FB7185';
      msgEl.classList.remove('hidden');
    }
  }
}

async function syncCloudStrategiesNow() {
  const btn = $('#btn-cloud-sync-now');
  const msgEl = $('#cloud-status-msg');
  const ghToken = ($('#cloud-gh-token')?.value || '').trim();
  const repoName = ($('#cloud-gh-repo')?.value || '').trim();
  const autoSync = $('#cloud-auto-sync')?.checked !== false;

  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ 동기화 중...';
  }
  try {
    if (ghToken || repoName) {
      await apiJson('/api/cloud/config', {
        method: 'POST',
        body: JSON.stringify({ gh_token: ghToken, github_repo: repoName || undefined, auto_sync_strategies: autoSync }),
      });
    }
    const res = await apiJson('/api/cloud/sync-now', { method: 'POST' });
    if (res.strategies) renderSavedStrategies(res.strategies);
    if (res.cloud) renderCloudConfig(res.cloud);
    if (msgEl) {
      msgEl.textContent = `✅ ${res.note || '동기화 완료'}`;
      msgEl.style.color = '#34D399';
      msgEl.classList.remove('hidden');
    }
  } catch (err) {
    if (msgEl) {
      msgEl.textContent = `⚠️ 동기화 실패: ${err.message}`;
      msgEl.style.color = '#FB7185';
      msgEl.classList.remove('hidden');
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '🔄 전략 양방향 동기화';
    }
  }
}

async function togglePublicTunnel() {
  const btn = $('#btn-toggle-tunnel');
  const curInfo = await refreshAccessInfo();
  const isRunning = curInfo && curInfo.tunnel_status === 'running' && !!curInfo.public_url;
  const action = isRunning ? 'stop' : 'start';
  if (btn) {
    btn.disabled = true;
    btn.textContent = action === 'start' ? '⏳ 외부 HTTPS 터널 생성 중 (최초 1회 약 5~15초)...' : '⏳ 터널 종료 중...';
  }
  try {
    const res = await apiJson('/api/access/tunnel', {
      method: 'POST',
      body: JSON.stringify({ action }),
    });
    renderAccessInfo(res);
  } catch (err) {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '🚀 외부 HTTPS 링크 1클릭 켜기';
    }
    const errEl = $('#tunnel-error-msg');
    if (errEl) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  }
}

async function initApp() {
  try {
    updateAccessButtonVisibility();
    setMobilePane(isMobileView() ? 'right' : 'left', 'summary');
    setStatus('⏳ 데이터 준비 중...', 'loading');
    const data = await apiJson('/api/init');
    state.init = data;
    updateAccessButtonVisibility();

    const endEl = $('#end-date');
    if (endEl && !endEl.value) endEl.value = data.today;

    renderRecipes(data.recipes);
    renderQuickPicks(data.quick_picks);
    renderSelectedTickers();
    renderStrategyDropdown(data.strategies);
    renderSavedStrategies(data.saved_strategies);
    renderSnippets();
    renderAllocWeights();
    renderDictionary('');

    bindEvents();
    setUiMode(state.uiMode);

    const restoredFromUrl = restoreFromUrlHash();
    // 첫 시작: 공유 링크가 없으면 KODEX200 월봉 눌림목 레시피 적용 및 자동 실행
    if (!restoredFromUrl && data.recipes && data.recipes.length > 0) {
      await applyRecipe(data.recipes[0], false);
    }
    pushFormulaHistory();
    await inspectFormula(false);
    loadUsGurus();
    await runBacktest();
  } catch (err) {
    setStatus('🔴 초기화 실패', 'error');
    showError(err.message);
  }
}

// ═══════════════════════════════════════════════════════════════
// 왼쪽 패널 렌더링
// ═══════════════════════════════════════════════════════════════

function renderRecipes(recipes) {
  const box = $('#recipe-bar');
  box.innerHTML = '';
  (recipes || []).forEach((r) => {
    const btn = document.createElement('button');
    btn.className = 'recipe-pill';
    btn.dataset.recipeId = r.id;
    btn.title = r.desc;
    btn.textContent = r.title;
    btn.onclick = () => applyRecipe(r, true);
    box.appendChild(btn);
  });
}

async function applyRecipe(r, shouldRun = true) {
  $$('.recipe-pill').forEach((el) => el.classList.toggle('active', el.dataset.recipeId === r.id));
  if (r.start_date) {
    $('#start-date').value = r.start_date;
    $$('#period-chips .mini-chip').forEach((c) => c.classList.remove('active'));
  }
  if (r.tickers && r.tickers.length) {
    state.tickers = [...r.tickers];
    renderSelectedTickers();
  }
  setMode(r.mode || 'strategy');

  if (r.mode === 'regime' && r.regime) {
    $('#reg-signal-ticker').value = r.regime.signal_ticker || 'QQQ';
    $('#reg-t1-ticker').value = r.regime.t1_ticker || 'TQQQ';
    $('#reg-t2-ticker').value = r.regime.t2_ticker || '';
    $('#reg-expr').value = r.regime.entry_expr || 'CLOSE > MA(200)';
    $('#reg-freq').value = r.regime.eval_freq || 'daily';
    updateRegimeKo();
  } else if (r.mode === 'allocation' && r.allocation) {
    state.allocWeights = { ...(r.allocation.weights || {}) };
    state.tickers = Object.keys(state.allocWeights);
    renderSelectedTickers();
    renderAllocWeights();
    $('#alloc-rebal').value = r.allocation.rebalance_freq || 'quarterly';
    $('#alloc-dca').value = r.allocation.monthly_contribution || 0;
  } else {
    $('#strategy-select').value = r.strategy_id || 'custom';
    onStrategySelectChange(false);
    if (r.entry_expr !== undefined) $('#entry-input').value = r.entry_expr;
    if (r.exit_expr !== undefined) $('#exit-input').value = r.exit_expr;
    pushFormulaHistory();
    await inspectFormula(false);
  }

  if (shouldRun) {
    runBacktest(true);
  }
}

function renderQuickPicks(picks) {
  const box = $('#quick-picks');
  box.innerHTML = '';
  (picks || []).forEach((qp) => {
    const btn = document.createElement('button');
    btn.className = 'qp-chip';
    btn.title = qp.desc;
    btn.textContent = qp.label;
    btn.onclick = () => {
      state.tickers = [...qp.tickers];
      qp.tickers.forEach((t) => {
        if (!(t in state.allocWeights)) state.allocWeights[t] = Math.floor(100 / qp.tickers.length);
      });
      renderSelectedTickers();
      renderAllocWeights();
      if (qp.tickers.length > 1 && Number($('#max-positions').value) < qp.tickers.length) {
        $('#max-positions').value = qp.tickers.length;
      }
      scheduleAutoRun();
    };
    box.appendChild(btn);
  });
}

function findUniverseName(code) {
  if (!state.init || !state.init.universe) return code;
  const hit = state.init.universe.find((u) => u.code.toUpperCase() === code.toUpperCase());
  return hit ? `${hit.name} (${hit.code})` : code;
}

function renderSelectedTickers() {
  const box = $('#selected-tickers');
  box.innerHTML = '';
  if (!state.tickers.length) {
    box.innerHTML = '<span class="badge-muted">종목을 검색해 추가하세요</span>';
    return;
  }
  state.tickers.forEach((t) => {
    const tag = document.createElement('span');
    tag.className = 'ticker-tag';
    tag.innerHTML = `<span>${findUniverseName(t)}</span>`;
    const x = document.createElement('button');
    x.type = 'button';
    x.textContent = '×';
    x.onclick = () => {
      state.tickers = state.tickers.filter((c) => c !== t);
      renderSelectedTickers();
      renderAllocWeights();
      scheduleAutoRun();
    };
    tag.appendChild(x);
    box.appendChild(tag);
  });
}

function addTicker(code) {
  const clean = String(code || '').trim();
  if (!clean) return;
  if (!state.tickers.includes(clean)) {
    state.tickers.push(clean);
    if (!(clean in state.allocWeights)) state.allocWeights[clean] = 20;
  }
  $('#ticker-search').value = '';
  $('#ticker-dropdown').classList.add('hidden');
  renderSelectedTickers();
  renderAllocWeights();
  scheduleAutoRun();
}

function renderStrategyDropdown(strategies) {
  const sel = $('#strategy-select');
  sel.innerHTML = '<option value="custom">✍️ 직접 수식 작성 / 수정 (Custom)</option>';
  const cats = state.init.category_order || ['장기투자', '추세추종', '역추세', '돌파'];
  cats.forEach((cat) => {
    const grp = document.createElement('optgroup');
    grp.label = cat;
    (strategies || []).filter((s) => s.category === cat).forEach((s) => {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = `${s.label} [${s.level}]`;
      grp.appendChild(opt);
    });
    sel.appendChild(grp);
  });
}

function onStrategySelectChange(shouldInspectAndRun = true) {
  const sid = $('#strategy-select').value;
  const descBox = $('#strategy-desc-box');
  const vboBox = $('#vbo-box');
  const fwInner = $('#formula-workbench-inner');

  if (sid === 'custom') {
    descBox.classList.add('hidden');
    vboBox.classList.add('hidden');
    fwInner.classList.remove('hidden');
    return;
  }
  const s = (state.init.strategies || []).find((x) => x.id === sid);
  if (!s) return;

  descBox.innerHTML = `<strong>${s.label}</strong> — ${s.summary}<br><span style="color:#94A3B8">👍 강점: ${s.good_when} | ⚠️ 주의: ${s.bad_when}</span>`;
  descBox.classList.remove('hidden');

  if (s.kind === 'vbo') {
    vboBox.classList.remove('hidden');
    fwInner.classList.add('hidden');
  } else {
    vboBox.classList.add('hidden');
    fwInner.classList.remove('hidden');
    $('#entry-input').value = s.default_entry;
    $('#exit-input').value = s.default_exit;
    if (shouldInspectAndRun) {
      pushFormulaHistory();
      inspectFormula(true).then(() => scheduleAutoRun());
    }
  }
}

function renderSavedStrategies(list) {
  const sel = $('#saved-strategy-select');
  sel.innerHTML = '<option value="">💾 내 전략 불러오기 (' + (list ? list.length : 0) + ')</option>';
  (list || []).forEach((s) => {
    const opt = document.createElement('option');
    opt.value = s.filename;
    opt.textContent = `${s.name} (${(s.tickers || []).join(',')})`;
    sel.appendChild(opt);
  });
}

function renderSnippets() {
  if (!state.init || !state.init.snippets) return;
  const palette = $('#snippet-palette');
  const groups = state.init.snippets[state.snippetTf] || {};
  palette.innerHTML = '';
  Object.entries(groups).forEach(([cat, items]) => {
    const sec = document.createElement('div');
    sec.innerHTML = `<div class="snip-cat-title">${cat}</div>`;
    const row = document.createElement('div');
    row.className = 'snip-chips';
    items.forEach(([label, formula, tip]) => {
      const b = document.createElement('button');
      b.className = 'snip-chip';
      b.title = `${formula}\n${tip}`;
      b.textContent = label;
      b.onclick = () => insertSnippetBlock(formula);
      row.appendChild(b);
    });
    sec.appendChild(row);
    palette.appendChild(sec);
  });
}

async function insertSnippetBlock(block, targetOverride = null, joinOverride = null) {
  const res = await apiJson('/api/formula/transform', {
    method: 'POST',
    body: JSON.stringify({
      action: 'append_block',
      target: targetOverride || state.snippetTarget,
      block,
      join_op: joinOverride || state.snippetJoin,
      entry_expr: $('#entry-input').value,
      exit_expr: $('#exit-input').value,
    }),
  });
  $('#strategy-select').value = 'custom';
  onStrategySelectChange(false);
  $('#entry-input').value = res.entry_expr;
  $('#exit-input').value = res.exit_expr;
  pushFormulaHistory();
  applyInspectPayload(res);
  scheduleAutoRun();
}

function renderAllocWeights() {
  const box = $('#alloc-weights-list');
  box.innerHTML = '';
  const list = state.tickers.length ? state.tickers : Object.keys(state.allocWeights);
  let sum = 0;
  list.forEach((t) => {
    const val = Number(state.allocWeights[t] ?? 0);
    sum += val;
    const row = document.createElement('div');
    row.className = 'alloc-row';
    row.innerHTML = `<span>${findUniverseName(t)}</span>`;
    const inp = document.createElement('input');
    inp.type = 'number';
    inp.min = 0;
    inp.max = 100;
    inp.step = 5;
    inp.value = val;
    inp.oninput = () => {
      state.allocWeights[t] = Number(inp.value || 0);
      updateAllocTotal();
    };
    inp.onchange = () => scheduleAutoRun();
    row.appendChild(inp);
    box.appendChild(row);
  });
  updateAllocTotal();
}

function updateAllocTotal() {
  const list = state.tickers.length ? state.tickers : Object.keys(state.allocWeights);
  const sum = list.reduce((acc, t) => acc + Number(state.allocWeights[t] || 0), 0);
  const bar = $('#alloc-total-bar');
  if (sum < 100) {
    bar.textContent = `주식 합계: ${sum.toFixed(0)}% (나머지 ${(100 - sum).toFixed(0)}% 현금 보유)`;
  } else if (sum > 100) {
    bar.textContent = `합계: ${sum.toFixed(0)}% (실행 시 100% 비율로 자동 정규화됨)`;
  } else {
    bar.textContent = `목표 비중 합계: 100% (완벽)`;
  }
}

function setMode(mode) {
  state.mode = mode;
  $$('.mode-btn').forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
  $('#panel-mode-strategy').classList.toggle('hidden', mode !== 'strategy');
  $('#panel-mode-regime').classList.toggle('hidden', mode !== 'regime');
  $('#panel-mode-allocation').classList.toggle('hidden', mode !== 'allocation');
}

// ═══════════════════════════════════════════════════════════════
// 실시간 수식 검증 · 한국어 번역 · 숫자 슬라이더
// ═══════════════════════════════════════════════════════════════

async function inspectFormula(loadData = false) {
  if (state.mode !== 'strategy') return;
  const entryExpr = $('#entry-input').value;
  const exitExpr = $('#exit-input').value;
  const ticker = state.tickers[0] || '';
  try {
    const res = await apiJson('/api/formula/inspect', {
      method: 'POST',
      body: JSON.stringify({
        entry_expr: entryExpr,
        exit_expr: exitExpr,
        ticker,
        start_date: $('#start-date').value,
        end_date: $('#end-date').value,
        mtf_close_only: $('#mtf-close-only').checked,
        load_data: loadData,
      }),
    });
    applyInspectPayload(res);
  } catch (e) {
    // ignore transient syntax error while typing
  }
}

function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function renderClauseXray(live) {
  const box = $('#clause-xray-box');
  if (!box) return;
  if (!live || !live.valid || (!live.entry_xray && !live.exit_xray)) {
    box.classList.add('hidden');
    return;
  }
  const renderSide = (title, xr) => {
    if (!xr || !xr.clauses || !xr.clauses.length) return '';
    const opBadge = xr.op === 'AND' ? '전체 동시충족 (AND)' : (xr.op === 'OR' ? '하나라도 충족 (OR)' : '단일 조건');
    const rows = xr.clauses.map((c) => {
      const cls = c.count === 0 ? 'zero' : (c.pct < 3.0 ? 'tight' : 'ok');
      const todayTag = c.today ? '🟢오늘 ' : '';
      return `
        <div class="xray-row" title="${escHtml(c.ko || '')}">
          <span class="xray-expr">${escHtml(c.expr)}</span>
          <span class="xray-cnt ${cls}">${todayTag}${c.count}봉 (${c.pct}%)</span>
        </div>
      `;
    }).join('');
    return `
      <div class="xray-group">
        <div class="xray-title">
          <span>${title} · <span style="color:#64748B;font-weight:500;">${opBadge}</span></span>
          <span>최종 ${xr.combined_count} / ${xr.total_bars}봉</span>
        </div>
        ${rows}
      </div>
    `;
  };
  const html = renderSide('🟢 매수 조건별 병목', live.entry_xray) + renderSide('🔴 매도 조건별 병목', live.exit_xray);
  if (!html.trim()) {
    box.classList.add('hidden');
    return;
  }
  box.innerHTML = html;
  box.classList.remove('hidden');
}

function applyInspectPayload(res) {
  const enKo = $('#entry-ko');
  const exKo = $('#exit-ko');
  const enBadge = $('#entry-live-badge');
  const exBadge = $('#exit-live-badge');

  enKo.textContent = '💬 ' + (res.entry_ko || '');
  enKo.classList.toggle('err', !res.entry_valid);
  exKo.textContent = '💬 ' + (res.exit_ko || '');
  exKo.classList.toggle('err', !res.exit_valid);

  enBadge.className = 'live-badge ' + (res.entry_valid ? 'ok' : 'err');
  enBadge.textContent = res.entry_valid ? '문법 정상' : '문법 오류';

  exBadge.className = 'live-badge ' + (res.exit_valid ? 'ok' : 'err');
  exBadge.textContent = res.exit_valid ? '문법 정상' : '문법 오류';

  if (res.live && res.live.valid) {
    const l = res.live;
    const ec = l.period_entry_count !== undefined ? l.period_entry_count : l.entry_count;
    const xc = l.period_exit_count !== undefined ? l.period_exit_count : l.exit_count;
    enBadge.textContent = `기간 내 신호 ${ec}회 · 최근 ${l.last_entry_date}`;
    exBadge.textContent = `기간 내 신호 ${xc}회 · 최근 ${l.last_exit_date}`;
    renderLiveIndicatorStrip(l);
    renderClauseXray(l);
  } else {
    renderClauseXray(null);
  }

  state.entryNumbers = res.entry_numbers || [];
  state.exitNumbers = res.exit_numbers || [];
  if (Array.isArray(res.linked_slots)) {
    state.linkedSlots = res.linked_slots;
    populateOptSlots(res.linked_slots);
  }
  if (!state.isDraggingTuner) {
    renderNumberTuners();
  }
}

function renderLiveIndicatorStrip(live) {
  const strip = $('#live-indicator-strip');
  if (!live || !live.indicator_values) {
    strip.classList.add('hidden');
    return;
  }
  const entries = Object.entries(live.indicator_values);
  if (!entries.length) {
    strip.classList.add('hidden');
    return;
  }
  strip.innerHTML = `<span class="ind-chip">📅 최근일 <strong>${live.last_date}</strong></span>` +
    entries.map(([k, v]) => {
      const fmtV = Math.abs(v) >= 1000 ? Math.round(v).toLocaleString('ko-KR') : Number(v).toFixed(2);
      return `<span class="ind-chip">${k}: <strong>${fmtV}</strong></span>`;
    }).join('');
  strip.classList.remove('hidden');
}

function renderNumberTuners() {
  const box = $('#number-tuners');
  const useSync = $('#sync-linked-nums')?.checked && Array.isArray(state.linkedSlots) && state.linkedSlots.length > 0;
  const items = [];

  if (useSync) {
    state.linkedSlots.forEach((sl) => {
      const targets = sl.targets || [];
      if (!targets.length) return;
      const [pSide, pIdx] = targets[0];
      const tok = pSide === 'entry' ? state.entryNumbers[pIdx] : state.exitNumbers[pIdx];
      if (!tok) return;
      const sides = new Set(targets.map((t) => t[0]));
      items.push({
        tok,
        targets,
        displayLabel: sl.label || tok.label,
        sideBadge: sides.size > 1 ? '연동' : (pSide === 'entry' ? '매수' : '매도'),
        sideClass: sides.size > 1 ? 'entry' : pSide,
      });
    });
  } else {
    state.entryNumbers.forEach((tok) => {
      items.push({ tok, targets: [['entry', tok.index]], displayLabel: tok.label, sideBadge: '매수', sideClass: 'entry' });
    });
    state.exitNumbers.forEach((tok) => {
      items.push({ tok, targets: [['exit', tok.index]], displayLabel: tok.label, sideBadge: '매도', sideClass: 'exit' });
    });
  }

  if (!items.length) {
    box.innerHTML = '<div style="font-size:11px;color:#64748B;padding:4px 2px;">수식에 조절할 숫자가 없습니다 (예: MA(20), RSI(14), * 0.99 입력 시 슬라이더 자동 생성)</div>';
    return;
  }

  box.innerHTML = '';
  items.forEach((item) => {
    const tok = item.tok;
    const row = document.createElement('div');
    row.className = 'tuner-row';

    const top = document.createElement('div');
    top.className = 'tuner-top';
    const linkNote = item.targets.length > 1 ? ` <small style="color:#60A5FA;">(${item.targets.length}곳 동시)</small>` : '';
    top.innerHTML = `
      <span class="tuner-label">
        <span class="tuner-side ${item.sideClass}">${item.sideBadge}</span>
        ${item.displayLabel}${linkNote}
      </span>
      <span style="font-size:10.5px;color:#94A3B8;font-family:var(--font-mono);">${tok.min} ~ ${tok.max}</span>
    `;

    const ctrl = document.createElement('div');
    ctrl.className = 'tuner-ctrl';

    const minusBtn = document.createElement('button');
    minusBtn.type = 'button';
    minusBtn.className = 'step-btn';
    minusBtn.textContent = '−';

    const slider = document.createElement('input');
    slider.type = 'range';
    slider.min = tok.min;
    slider.max = Math.max(tok.max, tok.value);
    slider.step = tok.step;
    slider.value = tok.value;

    const plusBtn = document.createElement('button');
    plusBtn.type = 'button';
    plusBtn.className = 'step-btn';
    plusBtn.textContent = '+';

    const numInp = document.createElement('input');
    numInp.type = 'number';
    numInp.className = 'tuner-num-input';
    numInp.min = tok.min;
    numInp.max = Math.max(tok.max * 2, tok.value * 2);
    numInp.step = tok.step;
    numInp.value = tok.value;

    const applyNewVal = (newV, isFinal = false) => {
      const clamped = Number(tok.is_float ? Number(newV).toFixed(4) : Math.round(Number(newV)));
      if (Number.isNaN(clamped)) return;
      slider.value = clamped;
      numInp.value = clamped;

      const enUp = {};
      const exUp = {};
      item.targets.forEach(([s, idx]) => {
        if (s === 'entry' && state.entryNumbers[idx]) {
          state.entryNumbers[idx].value = clamped;
          enUp[idx] = String(clamped);
        } else if (s === 'exit' && state.exitNumbers[idx]) {
          state.exitNumbers[idx].value = clamped;
          exUp[idx] = String(clamped);
        }
      });

      // 0ms 클라이언트 즉시 치환
      $('#strategy-select').value = 'custom';
      if (Object.keys(enUp).length) {
        $('#entry-input').value = replaceFormulaNumbersClient($('#entry-input').value, enUp, state.entryNumbers);
      }
      if (Object.keys(exUp).length) {
        $('#exit-input').value = replaceFormulaNumbersClient($('#exit-input').value, exUp, state.exitNumbers);
      }

      clearTimeout(state.debounceInspectTimer);
      if (isFinal) {
        pushFormulaHistory();
        inspectFormula(false);
      } else {
        state.debounceInspectTimer = setTimeout(() => inspectFormula(false), 90);
      }
      scheduleAutoRun(isFinal ? 120 : 180);
    };

    slider.addEventListener('pointerdown', () => { state.isDraggingTuner = true; });

    slider.oninput = () => applyNewVal(slider.value, false);
    slider.onchange = () => {
      state.isDraggingTuner = false;
      applyNewVal(slider.value, true);
    };
    numInp.onchange = () => applyNewVal(numInp.value, true);
    minusBtn.onclick = () => applyNewVal(Number(numInp.value) - Number(tok.step), true);
    plusBtn.onclick = () => applyNewVal(Number(numInp.value) + Number(tok.step), true);

    ctrl.append(minusBtn, slider, plusBtn, numInp);
    row.append(top, ctrl);
    box.appendChild(row);
  });
}

// ═══════════════════════════════════════════════════════════════
// IntelliSense 수식 자동완성 팝업
// ═══════════════════════════════════════════════════════════════

function getCaretWord(ta) {
  const pos = ta.selectionStart || 0;
  const before = ta.value.slice(0, pos);
  const m = before.match(/([A-Za-z_][A-Za-z0-9_]*)$/);
  if (!m) return { word: '', start: pos, end: pos };
  return { word: m[1].toUpperCase(), start: pos - m[1].length, end: pos };
}

function showAutocomplete(ta) {
  const popup = $('#autocomplete-popup');
  if (!state.init || !state.init.autocomplete) return;
  const { word } = getCaretWord(ta);
  if (!word || word.length < 1) {
    popup.classList.add('hidden');
    state.acMatches = [];
    return;
  }

  const matches = state.init.autocomplete
    .filter((item) => item.token.toUpperCase().includes(word) || item.desc.toUpperCase().includes(word))
    .slice(0, 10);

  if (!matches.length) {
    popup.classList.add('hidden');
    state.acMatches = [];
    return;
  }

  // 팝업을 현재 포커스된 textarea의 .editor-wrapper 안으로 이동
  ta.parentElement.appendChild(popup);
  state.activeFormulaInput = ta;
  state.acMatches = matches;
  state.acSelectedIdx = 0;

  renderAcPopup();
  popup.classList.remove('hidden');
}

function renderAcPopup() {
  const popup = $('#autocomplete-popup');
  popup.innerHTML = '';
  state.acMatches.forEach((m, idx) => {
    const row = document.createElement('div');
    row.className = 'ac-item' + (idx === state.acSelectedIdx ? ' selected' : '');
    row.innerHTML = `
      <div class="ac-left">
        <span class="ac-token">${m.token}</span>
        <span class="ac-cat">${m.cat}</span>
      </div>
      <span class="ac-desc">${m.desc}</span>
    `;
    row.onmousedown = (e) => {
      e.preventDefault();
      acceptAutocomplete(idx);
    };
    popup.appendChild(row);
  });
}

function acceptAutocomplete(idx) {
  const ta = state.activeFormulaInput;
  const item = state.acMatches[idx];
  const popup = $('#autocomplete-popup');
  if (!ta || !item) {
    popup.classList.add('hidden');
    return;
  }
  const { start, end } = getCaretWord(ta);
  const before = ta.value.slice(0, start);
  const after = ta.value.slice(end);
  ta.value = before + item.insert + after;
  const newCaret = start + item.insert.length;
  ta.focus();
  ta.setSelectionRange(newCaret, newCaret);
  popup.classList.add('hidden');
  state.acMatches = [];
  inspectFormula(false);
  scheduleAutoRun(350);
}

// ═══════════════════════════════════════════════════════════════
// 백테스트 실행 & 자동 실험 기록
// ═══════════════════════════════════════════════════════════════

function scheduleAutoRun(delayMs = 220) {
  if (!$('#autorun-checkbox').checked) return;
  clearTimeout(state.debounceBacktestTimer);
  state.debounceBacktestTimer = setTimeout(() => runBacktest(), delayMs);
}

function collectBacktestPayload() {
  return {
    mode: state.mode,
    tickers: state.tickers,
    benchmark_ticker: $('#benchmark-select').value,
    start_date: $('#start-date').value,
    end_date: $('#end-date').value,
    initial_capital: Number($('#initial-capital').value || 10000000),
    strategy_id: $('#strategy-select').value,
    strategy_params: {
      k: Number($('#vbo-k').value || 0.5),
      ma: Number($('#vbo-ma').value || 5),
    },
    entry_expr: $('#entry-input').value,
    exit_expr: $('#exit-input').value,
    max_positions: Number($('#max-positions').value || 1),
    execution: $('#execution-mode').value,
    stop_loss_pct: Number($('#stop-loss').value || 0),
    take_profit_pct: Number($('#take-profit').value || 0),
    trailing_stop_pct: Number($('#trailing-stop').value || 0),
    atr_stop: Number($('#atr-stop').value || 0),
    max_hold_days: Number($('#max-hold-days').value || 0),
    mtf_close_only: $('#mtf-close-only').checked,
    market_filter: $('#market-filter').value,
    slippage_pct: Number($('#slippage-pct').value || 0),
    tax_exempt: $('#tax-exempt').checked,
    oos_ratio: Number($('#oos-ratio').value || 0),
    mc_horizon_days: state.mcHorizonDays,
    candle_tf: state.candleTf || 'auto',
    regime: {
      signal_ticker: $('#reg-signal-ticker').value,
      t1_ticker: $('#reg-t1-ticker').value,
      t2_ticker: $('#reg-t2-ticker').value,
      entry_expr: $('#reg-expr').value,
      eval_freq: $('#reg-freq').value,
    },
    allocation: {
      weights: state.allocWeights,
      rebalance_freq: $('#alloc-rebal').value,
      monthly_contribution: Number($('#alloc-dca').value || 0),
    },
  };
}

async function runBacktest(switchMobile = false) {
  if (state.abortBacktest) state.abortBacktest.abort();
  state.abortBacktest = new AbortController();

  const payload = collectBacktestPayload();
  showError('');
  setStatus('⏳ 시뮬레이션 실행 중...', 'loading');
  updateSafetySummaryTag();

  try {
    const data = await apiJson('/api/backtest', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal: state.abortBacktest.signal,
    });
    state.lastResult = data;

    if (data.mode === 'strategy' && data.live_signal && data.live_signal.signal_diag) {
      renderLiveIndicatorStrip(data.live_signal.signal_diag);
      renderClauseXray(data.live_signal.signal_diag);
      const sd = data.live_signal.signal_diag;
      if (sd.valid) {
        $('#entry-live-badge').textContent = `신호 ${sd.entry_count}회 · 최근 ${sd.last_entry_date}`;
        $('#exit-live-badge').textContent = `신호 ${sd.exit_count}회 · 최근 ${sd.last_exit_date}`;
      }
    }

    logExperiment(data);
    renderDashboard(data);
    setStatus('🟢 분석 완료', 'ok');
    if (switchMobile && window.innerWidth <= 900) {
      setMobilePane('right');
    }
  } catch (err) {
    if (err.name === 'AbortError') return;
    setStatus('🔴 오류 발생', 'error');
    showError(err.message);
  }
}

function updateSafetySummaryTag() {
  const sl = Number($('#stop-loss').value || 0);
  const tp = Number($('#take-profit').value || 0);
  const tr = Number($('#trailing-stop').value || 0);
  const ex = $('#execution-mode').value === 'next_open' ? '익일시가' : '당일종가';
  const parts = [];
  if (sl > 0) parts.push(`손절 -${sl}%`);
  if (tp > 0) parts.push(`익절 +${tp}%`);
  if (tr > 0) parts.push(`추적 -${tr}%`);
  if (!parts.length) parts.push('안전손절 0%');
  parts.push(ex);
  $('#safety-summary-tag').textContent = parts.join(' · ');
}

function logExperiment(data) {
  if (data.mode !== 'strategy') return;
  const sigKey = `${(data.tickers || []).join(',')}|${data.entry_expr}|${data.exit_expr}`;
  const existing = state.experiments.find((e) => e.sigKey === sigKey);
  if (existing) {
    existing.metrics = data.metrics;
    existing.grade = data.grade;
    existing.dates = data.series.dates;
    existing.equity_pct = data.series.equity_pct;
    return;
  }
  const exp = {
    id: state.expSeq++,
    sigKey,
    pinned: false,
    tickers: [...(data.tickers || [])],
    entry_expr: data.entry_expr,
    exit_expr: data.exit_expr,
    metrics: data.metrics,
    grade: data.grade,
    dates: data.series.dates,
    equity_pct: data.series.equity_pct,
  };
  state.experiments.unshift(exp);
  if (!state.baselineExpId) {
    state.baselineExpId = exp.id;
  }
  if (state.experiments.length > 18) {
    // 고정되지 않은 가장 오래된 항목 제거
    const unpinnedIdx = [...state.experiments].reverse().findIndex((x) => !x.pinned && x.id !== state.baselineExpId);
    if (unpinnedIdx >= 0) {
      const realIdx = state.experiments.length - 1 - unpinnedIdx;
      state.experiments.splice(realIdx, 1);
    }
  }
  $('#exp-count').textContent = state.experiments.length;
}

// ═══════════════════════════════════════════════════════════════
// 대시보드 & 차트 렌더링
// ═══════════════════════════════════════════════════════════════

function _extEsc(s) {
  return String(s ?? '').replace(/[&<>\x22\x27]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _fmtFlowShares(v) {
  const n = Number(v || 0);
  const abs = Math.abs(n);
  const sign = n > 0 ? '+' : n < 0 ? '-' : '';
  if (abs >= 10000) return `${sign}${(abs / 10000).toFixed(1)}만주`;
  return `${sign}${Math.round(abs).toLocaleString('ko-KR')}주`;
}

function renderExternalIntelBar(data) {
  const box = $('#external-intel-bar');
  if (!box) return;
  const ext = data && data.external_intel;
  if (!ext || (!ext.fundamentals && !ext.macro)) {
    box.classList.add('hidden');
    return;
  }

  const f = ext.fundamentals || {};
  const m = ext.macro || {};
  const flow = f.flow_summary || null;
  const autoEvs = ext.auto_events || [];
  const latestNews = ext.latest_news || [];
  const researches = f.researches || [];

  const chips = [];
  if (flow) {
    chips.push({ lbl: '외인+기관 5일', val: _fmtFlowShares(flow.smart_net_5d), cls: flow.smart_net_5d >= 0 ? 'pos' : 'neg', block: 'SMART_NET(5) > 0' });
    chips.push({ lbl: '외국인 5일', val: _fmtFlowShares(flow.frg_net_5d), cls: flow.frg_net_5d >= 0 ? 'pos' : 'neg', block: 'FRG_NET(5) > 0' });
    chips.push({ lbl: '기관 5일', val: _fmtFlowShares(flow.inst_net_5d), cls: flow.inst_net_5d >= 0 ? 'pos' : 'neg', block: 'INST_NET(5) > 0' });
  }
  if (f.frg_ratio != null) chips.push({ lbl: '외인지분율', val: `${Number(f.frg_ratio).toFixed(2)}%`, block: 'FRG_RATIO > REF(FRG_RATIO, 20)' });
  if (f.inst_hold_pct != null) chips.push({ lbl: '기관보유(US)', val: `${f.inst_hold_pct}%` });
  if (f.short_float_pct != null) chips.push({ lbl: '공매도비율', val: `${f.short_float_pct}%` });
  if (f.fw_per != null) chips.push({ lbl: '선행 PER', val: `${Number(f.fw_per).toFixed(2)}배`, block: 'FW_PER > 0 AND FW_PER <= 15' });
  if (f.per != null) chips.push({ lbl: 'PER', val: `${Number(f.per).toFixed(2)}배`, block: 'PER > 0 AND PER <= 20' });
  if (f.pbr != null) chips.push({ lbl: 'PBR', val: `${Number(f.pbr).toFixed(2)}배`, block: 'PBR > 0 AND PBR <= 2' });
  if (f.target_upside_pct != null) {
    const up = Number(f.target_upside_pct);
    chips.push({ lbl: '목표가 괴리', val: `${up >= 0 ? '+' : ''}${up.toFixed(1)}%`, cls: up >= 0 ? 'pos' : 'neg', block: 'TARGET_UPSIDE >= 15' });
  }
  if (m.vix != null) chips.push({ lbl: 'VIX', val: `${m.vix} (${_extEsc(m.vix_regime)})`, block: 'VIX < 25' });
  if (m.us10y != null) chips.push({ lbl: '미10년금리', val: `${m.us10y}%`, block: 'US10Y < 4.5' });
  if (m.usdkrw != null) chips.push({ lbl: '원/달러', val: `${Number(m.usdkrw).toLocaleString('ko-KR')}원`, block: 'USDKRW < 1400' });

  const chipsHtml = chips.map((c) => {
    const clickAttr = c.block ? ` data-block="${_extEsc(c.block)}" title="클릭하면 수식(${_extEsc(c.block)}) 추가"` : '';
    const clickCls = c.block ? ' clickable' : '';
    return `<div class="ext-chip${clickCls}"${clickAttr}><span class="ext-chip-lbl">${_extEsc(c.lbl)}</span><span class="ext-chip-val ${c.cls || ''}">${_extEsc(c.val)}</span></div>`;
  }).join('');

  const renderNewsRows = (rows, emptyMsg) => {
    if (!rows.length) return `<div class="ext-news-item" style="color:#64748B;">${emptyMsg}</div>`;
    return rows.map((it) => {
      const txt = _extEsc(it.full_title || it.title || '');
      const src = it.source ? ` <span style="color:#64748B;">(${_extEsc(it.source)})</span>` : '';
      const titleHtml = it.url
        ? `<a href="${_extEsc(it.url)}" target="_blank" rel="noopener noreferrer">${txt}</a>${src}`
        : `${txt}${src}`;
      return `<div class="ext-news-item"><span class="ext-news-date">${_extEsc(it.date || '')}</span><span>${titleHtml}</span></div>`;
    }).join('');
  };

  const resHtml = researches.length ? `
    <div class="ext-news-box">
      <div class="ext-news-box-title">📑 증권사 최신 리포트</div>
      <div class="ext-news-list">${researches.slice(0, 4).map((r) => `<div class="ext-news-item"><span class="ext-news-date">${_extEsc(r.date || '')}</span><span><strong style="color:#CBD5E1;">[${_extEsc(r.broker || '')}]</strong> ${_extEsc(r.title || '')}</span></div>`).join('')}</div>
    </div>` : '';

  // 🏦 전문가 매수(스마트머니) 핵심 판정 배너
  let smartRowHtml = '';
  if (flow) {
    const sm5 = Number(flow.smart_net_5d || 0);
    const sm20 = Number(flow.smart_net_20d || 0);
    const frg5 = Number(flow.frg_net_5d || 0);
    const inst5 = Number(flow.inst_net_5d || 0);
    let badgeCls = 'neu';
    let badgeText = `🟡 외인(${_fmtFlowShares(frg5)}) · 기관(${_fmtFlowShares(inst5)}) 수급 혼조`;
    if (frg5 > 0 && inst5 > 0) {
      badgeCls = 'pos';
      badgeText = `🟢 외인·기관 쌍끌이 매수 중 (5일 합산 ${_fmtFlowShares(sm5)})`;
    } else if (sm5 > 0) {
      badgeCls = 'pos';
      badgeText = `🟢 스마트머니 순유입 (5일 ${_fmtFlowShares(sm5)})`;
    } else if (frg5 < 0 && inst5 < 0) {
      badgeCls = 'neg';
      badgeText = `🔴 외인·기관 동반 순매도 (5일 ${_fmtFlowShares(sm5)})`;
    }
    smartRowHtml = `
      <div class="ext-smart-row">
        <div class="ext-smart-banner">
          <span style="font-weight:700;color:#E2E8F0;">🏦 전문가 수급(스마트머니)</span>
          <span class="ext-smart-pill ${badgeCls}">${badgeText}</span>
          <span style="font-size:0.74rem;color:#94A3B8;">20일 누적 ${_fmtFlowShares(sm20)}${f.frg_ratio != null ? ` · 외인지분 ${Number(f.frg_ratio).toFixed(2)}%` : ''}</span>
        </div>
        <button type="button" class="sm-chip sm-chip-hot ext-smart-apply" data-block="SMART_NET(5) > 0">+ 매수식에 스마트머니 적용</button>
      </div>`;
  } else if (f.inst_hold_pct != null || f.target_upside_pct != null || f.fw_per != null) {
    const up = f.target_upside_pct != null ? Number(f.target_upside_pct) : null;
    const parts = [];
    if (f.inst_hold_pct != null) parts.push(`🏛️ 기관보유 ${f.inst_hold_pct}%`);
    if (f.fw_per != null) parts.push(`💎 선행PER ${Number(f.fw_per).toFixed(1)}배`);
    if (up != null) parts.push(`🎯 목표가 괴리 ${up >= 0 ? '+' : ''}${up.toFixed(1)}%`);
    const pillCls = (up != null && up >= 10) ? 'pos' : (up != null && up < 0 ? 'neg' : 'neu');
    smartRowHtml = `
      <div class="ext-smart-row">
        <div class="ext-smart-banner">
          <span style="font-weight:700;color:#E2E8F0;">🏦 스마트머니 · 밸류에이션 핵심</span>
          <span class="ext-smart-pill ${pillCls}">${_extEsc(parts.join(' · '))}</span>
        </div>
        ${f.fw_per != null ? `<button type="button" class="sm-chip sm-chip-hot ext-smart-apply" data-block="FW_PER > 0 AND FW_PER <= 18">+ 선행PER 저평가 결합</button>` : ''}
      </div>`;
  }

  // ⚡ 시점별 급변동 주요사건 요약 칩 (항상 노출)
  const inlineEventsHtml = autoEvs.length ? `
    <div class="ext-event-inline-pills">
      ${autoEvs.slice(0, 3).map((ev) => {
        const titleTxt = _extEsc(ev.full_title || ev.title || ev.label || '');
        const dtTxt = _extEsc(ev.date || '');
        if (ev.url) {
          return `<a class="ext-ev-pill" href="${_extEsc(ev.url)}" target="_blank" rel="noopener noreferrer"><span class="ev-dt">⚡ ${dtTxt}</span><span>${titleTxt}</span></a>`;
        }
        return `<span class="ext-ev-pill"><span class="ev-dt">⚡ ${dtTxt}</span><span>${titleTxt}</span></span>`;
      }).join('')}
    </div>` : '';

  box.innerHTML = `
    <div class="ext-intel-head">
      <div class="ext-intel-title">🌐 실시간 외부 인텔리전스 · ${_extEsc(f.name || ext.ticker || '')} <span style="font-weight:400;color:#94A3B8;font-size:0.76rem;">(${_extEsc(f.market_cap_str || f.source || '')})</span></div>
      <div class="ext-intel-meta">💡 칩 클릭 시 매수 조건 결합 · 기준 ${_extEsc(ext.fetched_at || '-')}</div>
    </div>
    ${smartRowHtml}
    <div class="ext-chips-row">${chipsHtml || '<div class="ext-chip">외부 데이터 로딩 대기</div>'}</div>
    ${inlineEventsHtml}
    <details class="ext-news-details">
      <summary class="ext-news-summary">📰 급변동 시점 뉴스(${autoEvs.length}건) · 최신 뉴스(${latestNews.length}건)${researches.length ? ` · 리포트(${researches.length}건)` : ''} 전체 보기</summary>
      <div class="ext-news-strip">
        <div class="ext-news-box">
          <div class="ext-news-box-title">⚡ 차트 급변동 시점별 자동 뉴스 (Top ${autoEvs.length})</div>
          <div class="ext-news-list">${renderNewsRows(autoEvs, '기간 내 ±3.5% 이상 급변동 사건 없음')}</div>
        </div>
        <div class="ext-news-box">
          <div class="ext-news-box-title">📰 최신 종목 뉴스 (Google News RSS)</div>
          <div class="ext-news-list">${renderNewsRows(latestNews.slice(0, 4), '최신 뉴스를 가져오지 못했습니다.')}</div>
        </div>
        ${resHtml}
      </div>
    </details>
  `;
  box.classList.remove('hidden');

  $$('.ext-chip.clickable[data-block], .ext-smart-apply[data-block]', box).forEach((el) => {
    el.onclick = async () => {
      if (state.mode !== 'strategy') setMode('strategy');
      await insertSnippetBlock(el.dataset.block, 'entry', 'AND');
    };
  });
}

function renderDashboard(data) {
  renderLiveSignalBanner(data.live_signal);
  renderVerdictAndKpis(data);
  renderExternalIntelBar(data);
  renderEquityChart(data);
  if (state.equityOverlayIndexSymbol) {
    ensureEquityOverlayAndRender(data);
  }
  loadAndRenderNationalIndex();
  renderRadarChart(data);
  renderCoach(data);
  renderMetricsGrid(data.metrics);
  renderExperimentsBoard();
  renderCandleAndTrades(data);
  renderFutureTab(data);
}

function renderLiveSignalBanner(sig) {
  const b = $('#live-signal-banner');
  if (!sig || !sig.status) {
    b.classList.add('hidden');
    return;
  }
  b.classList.remove('hidden');
  b.style.borderLeft = `4px solid ${sig.color || '#3B82F6'}`;

  let holdTxt = '';
  if (sig.holding) {
    const h = sig.holding;
    const parts = [`진입가 ${Math.round(h.entry_price).toLocaleString('ko-KR')}`, `현재가 ${Math.round(h.current_price).toLocaleString('ko-KR')}`];
    if (h.stop_loss_price) parts.push(`손절가 ${Math.round(h.stop_loss_price).toLocaleString('ko-KR')} (${h.stop_loss_gap_pct.toFixed(1)}%)`);
    if (h.take_profit_price) parts.push(`목표가 ${Math.round(h.take_profit_price).toLocaleString('ko-KR')} (+${h.take_profit_gap_pct.toFixed(1)}%)`);
    holdTxt = parts.join(' · ');
  } else {
    holdTxt = `기준일: ${sig.last_date} · 최근 매수신호: ${sig.signal_diag?.last_entry_date || '없음'}`;
  }

  b.innerHTML = `
    <div class="sig-left">
      <span class="sig-badge" style="background:${sig.color}22;color:${sig.color};border:1px solid ${sig.color}66;">${sig.badge}</span>
      <span class="sig-summary">${sig.summary}</span>
    </div>
    <div class="sig-meta">${holdTxt}</div>
  `;
}

function renderVerdictAndKpis(data) {
  const m = data.metrics || {};
  const g = data.grade || {};

  const gb = $('#grade-badge');
  gb.textContent = g.grade || '-';
  gb.className = `grade-badge grade-${g.grade || 'B'}`;

  $('#verdict-score').textContent = `AI 종합 ${g.score ?? '-'}점 / 100`;
  $('#verdict-tickers').textContent = `${(data.tickers || []).join(', ')} · ${data.series?.dates?.[0] || ''} ~ ${data.series?.dates?.slice(-1)[0] || ''}`;
  $('#verdict-headline').textContent = g.headline || '-';
  $('#verdict-formula').textContent = `매수: ${data.entry_expr || '-'}  |  매도: ${data.exit_expr || '-'}`;

  // Baseline 대비 Delta 계산
  const baseExp = state.experiments.find((e) => e.id === state.baselineExpId);
  let expDeltaHtml = '';
  if (baseExp && data.mode === 'strategy' && baseExp.entry_expr + '|' + baseExp.exit_expr !== data.entry_expr + '|' + data.exit_expr) {
    const dRet = Number(m['Total Return (%)'] || 0) - Number(baseExp.metrics['Total Return (%)'] || 0);
    expDeltaHtml = ` · <span class="${dRet >= 0 ? 'pos' : 'neg'}">기준대비 ${dRet >= 0 ? '+' : ''}${dRet.toFixed(1)}%p</span>`;
  }

  $('#kpi-final').textContent = fmtCompactKRW(m['Final Capital']);
  $('#kpi-profit').textContent = `순수익 ${Number(m['Net Profit'] || 0) >= 0 ? '+' : ''}${fmtCompactKRW(m['Net Profit'])}`;

  const totRet = Number(m['Total Return (%)'] || 0);
  const retEl = $('#kpi-return');
  retEl.textContent = fmtPct(totRet);
  retEl.className = 'kpi-value ' + (totRet >= 0 ? 'pos' : 'neg');
  $('#kpi-return-delta').innerHTML = `보유(${fmtPct(m['Benchmark Return (%)'])}) 대비 ${fmtPct(m['Alpha (%)'])}p${expDeltaHtml}`;

  const cagr = Number(m['CAGR (%)'] || 0);
  const cagrEl = $('#kpi-cagr');
  cagrEl.textContent = fmtPct(cagr);
  cagrEl.className = 'kpi-value ' + (cagr >= 0 ? 'pos' : 'neg');
  $('#kpi-cagr-bench').textContent = `벤치마크 CAGR ${fmtPct(m['Benchmark CAGR (%)'])}`;

  const mdd = Number(m['MDD (%)'] || 0);
  const bmdd = Number(m['Benchmark MDD (%)'] || 0);
  $('#kpi-mdd').textContent = fmtPct(mdd, false);
  $('#kpi-mdd').className = 'kpi-value neg';
  $('#kpi-mdd-bench').textContent = `보유 MDD ${fmtPct(bmdd, false)} · 회복 ${m['MDD Duration (days)'] || 0}일`;

  const sh = Number(m['Sharpe Ratio'] || 0);
  $('#kpi-sharpe').textContent = sh.toFixed(2);
  $('#kpi-sharpe').className = 'kpi-value ' + (sh >= 1 ? 'pos' : '');
  $('#kpi-sortino').textContent = `소르티노 ${Number(m['Sortino Ratio'] || 0).toFixed(2)} · 칼마 ${Number(m['Calmar Ratio'] || 0).toFixed(2)}`;

  $('#kpi-winrate').textContent = `${Number(m['Win Rate (%)'] || 0).toFixed(1)}% · PF ${m['Profit Factor'] ?? 0}`;
  $('#kpi-trades').textContent = `완료 ${m['Total Trades'] || 0}회 · 보유비중 ${m['Exposure (%)'] || 0}%`;
}

function renderEquityChart(data) {
  const s = data.series;
  if (!s || !s.dates || !s.dates.length) return;
  const c = data.candle;
  const hasCandle = !!(c && Array.isArray(c.dates) && c.dates.length);
  const isCandle = state.chartUnit === 'candle' && hasCandle;
  const isPct = state.chartUnit === 'pct';
  const useLog = $('#chart-log-scale').checked;

  $('#equity-tf-group')?.classList.toggle('hidden', state.chartUnit !== 'candle');

  let traces = [];
  if (isCandle) {
    const tfName = c.tf === 'M' ? '월봉' : (c.tf === 'W' ? '주봉' : '일봉');
    traces = [
      {
        x: c.dates, open: c.open, high: c.high, low: c.low, close: c.close,
        type: 'candlestick', name: `${c.ticker} (${tfName})`,
        increasing: { line: { color: '#00D084' }, fillcolor: 'rgba(0,208,132,0.85)' },
        decreasing: { line: { color: '#FF4D4F' }, fillcolor: 'rgba(255,77,79,0.85)' },
        xaxis: 'x', yaxis: 'y',
      },
      { x: c.dates, y: c.ma20, name: 'MA(20)', type: 'scatter', mode: 'lines', line: { color: 'rgba(245,176,65,0.6)', width: 1.2 }, xaxis: 'x', yaxis: 'y' },
      { x: c.dates, y: c.ma60, name: 'MA(60)', type: 'scatter', mode: 'lines', line: { color: 'rgba(139,92,246,0.6)', width: 1.2 }, xaxis: 'x', yaxis: 'y' },
    ];
    const ovColors = ['#38BDF8', '#F472B6', '#FACC15', '#34D399', '#FB923C'];
    (c.custom_overlays || []).forEach((ov, idx) => {
      traces.push({
        x: c.dates, y: ov.values, name: `📐 ${ov.name}`,
        type: 'scatter', mode: 'lines',
        line: { color: ovColors[idx % ovColors.length], width: 2.0 },
        xaxis: 'x', yaxis: 'y',
      });
    });
    if (c.buys && c.buys.length) {
      traces.push({
        x: c.buys.map((b) => b.date),
        y: c.buys.map((b) => b.price),
        mode: 'markers', name: '매수 ▲',
        marker: { symbol: 'triangle-up', size: 11, color: '#00E676', line: { color: '#052E16', width: 1 } },
        text: c.buys.map((b) => `매수 ${Math.round(b.price).toLocaleString()}원 (${b.shares}주 · ${b.raw_date || b.date})`),
        xaxis: 'x', yaxis: 'y',
      });
    }
    if (c.sells && c.sells.length) {
      traces.push({
        x: c.sells.map((sl) => sl.date),
        y: c.sells.map((sl) => sl.price),
        mode: 'markers', name: '매도 ▼',
        marker: { symbol: 'triangle-down', size: 11, color: '#FF4D4F', line: { color: '#450A0A', width: 1 } },
        text: c.sells.map((sl) => `매도 ${Math.round(sl.price).toLocaleString()}원 (${sl.return_pct.toFixed(1)}% · ${sl.reason} · ${sl.raw_date || sl.date})`),
        xaxis: 'x', yaxis: 'y',
      });
    }
    traces.push({
      x: s.dates, y: s.drawdown, name: '전략 낙폭(MDD)',
      type: 'scatter', mode: 'lines',
      line: { color: '#FF4D4F', width: 1.5 },
      fill: 'tozeroy', fillcolor: 'rgba(255,77,79,0.16)',
      xaxis: 'x', yaxis: 'y2',
    });
  } else {
    const yStrat = isPct ? s.equity_pct : s.equity;
    const yBench = isPct ? s.benchmark_pct : s.benchmark;

    traces = [
      {
        x: s.dates, y: yBench, name: s.benchmark_name || '그냥 보유',
        type: 'scatter', mode: 'lines',
        line: { color: '#64748B', width: 1.7, dash: 'dot' },
        xaxis: 'x', yaxis: 'y',
      },
      {
        x: s.dates, y: yStrat, name: '내 전략',
        type: 'scatter', mode: 'lines',
        line: { color: '#00D084', width: 2.5 },
        fill: 'tozeroy', fillcolor: 'rgba(0,208,132,0.08)',
        xaxis: 'x', yaxis: 'y',
      },
      {
        x: s.dates, y: s.drawdown, name: '전략 낙폭(MDD)',
        type: 'scatter', mode: 'lines',
        line: { color: '#FF4D4F', width: 1.5 },
        fill: 'tozeroy', fillcolor: 'rgba(255,77,79,0.16)',
        xaxis: 'x', yaxis: 'y2',
      },
    ];
    if (s.bench_dd && s.bench_dd.length) {
      traces.push({
        x: s.dates, y: s.bench_dd, name: '보유 낙폭',
        type: 'scatter', mode: 'lines',
        line: { color: '#94A3B8', width: 1, dash: 'dot' },
        xaxis: 'x', yaxis: 'y2',
      });
    }

    const ov = state.equityOverlayIndexData;
    if (state.equityOverlayIndexSymbol && ov && ov.series && Array.isArray(ov.series.dates) && ov.series.dates.length) {
      const initCap = Number($('#initial-capital')?.value || 10000000);
      const yOv = isPct
        ? ov.series.pct
        : ov.series.pct.map((p) => Math.round(initCap * (1 + Number(p || 0) / 100)));
      traces.push({
        x: ov.series.dates,
        y: yOv,
        name: `${ov.flag || '🌍'} ${ov.short || ov.symbol} 국가지수`,
        type: 'scatter',
        mode: 'lines',
        line: { color: '#F59E0B', width: 2.0 },
        xaxis: 'x',
        yaxis: 'y',
      });
      if (Array.isArray(ov.series.drawdown) && ov.series.drawdown.length) {
        traces.push({
          x: ov.series.dates,
          y: ov.series.drawdown,
          name: `${ov.short || ov.symbol} 낙폭`,
          type: 'scatter',
          mode: 'lines',
          line: { color: '#F59E0B', width: 1.1, dash: 'dot' },
          xaxis: 'x',
          yaxis: 'y2',
        });
      }
    }
  }

  const layout = {
    ...PLOT_LAYOUT_BASE,
    grid: { rows: 2, columns: 1, pattern: 'coupled', roworder: 'top to bottom' },
    yaxis: {
      ...PLOT_LAYOUT_BASE.yaxis,
      domain: [0.26, 1.0],
      title: isCandle ? '주가 (봉차트)' : (isPct ? '수익률 (%)' : '평가자산 (원)'),
      type: ((!isPct && !isCandle && useLog) || (isCandle && useLog)) ? 'log' : 'linear',
    },
    yaxis2: {
      ...PLOT_LAYOUT_BASE.yaxis,
      domain: [0.0, 0.20],
      title: '낙폭 (%)',
    },
    xaxis: { ...PLOT_LAYOUT_BASE.xaxis, anchor: 'y2', ...(isCandle ? { rangeslider: { visible: false } } : {}) },
    shapes: [],
    annotations: [],
  };

  const eqEl = $('#chart-equity');
  if (eqEl && !$('#equity-card')?.classList.contains('chart-fullscreen-card')) {
    eqEl.style.height = isMobileView() ? '350px' : '580px';
  }

  if ($('#chart-show-events')?.checked) {
    const minD = s.dates[0];
    const maxD = s.dates[s.dates.length - 1];
    const filterMode = $('#chart-event-filter')?.value || 'all';
    const macroEvents = (state.init && Array.isArray(state.init.historical_events))
      ? state.init.historical_events.map((e) => ({ ...e, category: e.category || 'macro' }))
      : [];
    const stockEvents = (data.external_intel && Array.isArray(data.external_intel.auto_events))
      ? data.external_intel.auto_events.map((e) => ({ ...e, category: 'stock' }))
      : [];

    let combined = [];
    if (filterMode === 'stock') combined = stockEvents;
    else if (filterMode === 'macro') combined = macroEvents;
    else combined = [...stockEvents, ...macroEvents];

    const evColors = {
      crisis: 'rgba(255, 77, 79, 0.65)',
      shock: 'rgba(245, 176, 65, 0.65)',
      bull: 'rgba(0, 208, 132, 0.65)',
      policy: 'rgba(56, 189, 248, 0.65)',
      stock_up: 'rgba(34, 197, 94, 0.85)',
      stock_down: 'rgba(249, 115, 22, 0.85)',
    };

    let visEvents = combined
      .filter((ev) => ev && ev.date >= minD && ev.date <= maxD)
      .sort((a, b) => (a.date > b.date ? 1 : -1));
    if (isMobileView() && visEvents.length > 4) {
      const stk = visEvents.filter((e) => e.category === 'stock');
      const mac = visEvents.filter((e) => e.category !== 'stock');
      visEvents = [...stk.slice(-3), ...mac.slice(-(4 - Math.min(3, stk.length)))]
        .sort((a, b) => (a.date > b.date ? 1 : -1));
    }
    visEvents.forEach((ev, idx) => {
      const col = evColors[ev.type] || 'rgba(148, 163, 184, 0.55)';
      const isStock = ev.category === 'stock';
      layout.shapes.push({
        type: 'line',
        xref: 'x',
        yref: 'paper',
        x0: ev.date,
        x1: ev.date,
        y0: 0.26,
        y1: 1.0,
        line: { color: col, width: isStock ? 1.5 : 1.1, dash: isStock ? 'dashdot' : 'dot' },
      });
      const hoverInfo = ev.full_title
        ? `${ev.full_title}${ev.source ? ' (' + ev.source + ')' : ''}`
        : `${ev.date} · ${ev.label} (${ev.market || 'MACRO'})`;
      layout.annotations.push({
        x: ev.date,
        xref: 'x',
        y: isStock ? (0.99 - (idx % 2) * 0.085) : (0.82 - (idx % 3) * 0.085),
        yref: 'paper',
        text: ev.label,
        hovertext: hoverInfo,
        hoverlabel: { bgcolor: '#0F172A', bordercolor: col, font: { color: '#F8FAFC', size: 11 } },
        showarrow: false,
        xanchor: 'left',
        font: { size: 9.5, color: isStock ? '#FDE68A' : '#E2E8F0' },
        bgcolor: isStock ? 'rgba(30, 41, 59, 0.90)' : 'rgba(15, 23, 42, 0.78)',
        bordercolor: col,
        borderwidth: 1,
        borderpad: 2,
      });
    });
  }

  if (data.is_oos && data.is_oos['OOS Start']) {
    layout.shapes.push({
      type: 'line',
      x0: data.is_oos['OOS Start'], x1: data.is_oos['OOS Start'],
      y0: 0, y1: 1, yref: 'paper',
      line: { color: '#8B5CF6', width: 2, dash: 'dash' },
    });
  }

  const zEq = state.equityZoom || 'ALL';
  if (isCandle) {
    const candleMode = zEq === 'ALL' ? 'LAST_140' : zEq;
    const rng = computeVisibleOhlcRange(c.dates, c.high, c.low, candleMode, c.tf || 'D');
    if (rng) {
      layout.xaxis.range = rng.xRange;
      if (rng.yRange && !useLog) layout.yaxis.range = rng.yRange;
    }
  } else if (zEq !== 'ALL' && s.dates.length > 5) {
    const eqDays = zEq === '6M' ? 184 : zEq === '1Y' ? 366 : 1096;
    const eqCut = shiftDateDays(s.dates[s.dates.length - 1], -eqDays);
    if (eqCut > s.dates[0]) {
      layout.xaxis.range = [eqCut, s.dates[s.dates.length - 1]];
    }
  }

  Plotly.react('chart-equity', traces, buildPlotLayout(layout), buildPlotCfg());
}

function setUiMode(mode) {
  const m = mode === 'pro' ? 'pro' : 'simple';
  state.uiMode = m;
  document.body.classList.toggle('simple-mode', m === 'simple');
  $$('#ui-mode-group .seg-btn').forEach((b) => {
    b.classList.toggle('active', b.dataset.uimode === m);
  });
  const deep = $('#summary-deep-details');
  if (deep) {
    deep.open = m === 'pro';
  }
  if (m === 'simple') {
    const activeTabBtn = $('#dash-tabs .dash-tab.active');
    if (activeTabBtn && activeTabBtn.classList.contains('simple-hide')) {
      const sumTab = $('#dash-tabs .dash-tab[data-tab="summary"]');
      if (sumTab) sumTab.click();
    }
    if (state.mode !== 'strategy') setMode('strategy');
  }
  setTimeout(() => {
    ['chart-equity', 'chart-national-index', 'chart-radar'].forEach((cid) => {
      const el = document.getElementById(cid);
      if (el && el.data) {
        try { Plotly.Plots.resize(el); } catch (e) {}
      }
    });
  }, 60);
}

async function fetchIndexEventsData(symbol, startDate, endDate) {
  const sym = symbol || '^KS11';
  const sDate = startDate || $('#start-date')?.value || '2018-01-01';
  const eDate = endDate || $('#end-date')?.value || '';
  const key = `${sym}|${sDate}|${eDate}`;
  if (state.indexEventsCache[key]) return state.indexEventsCache[key];
  const qs = new URLSearchParams({ symbol: sym, start_date: sDate, end_date: eDate });
  const data = await apiJson(`/api/index-events?${qs.toString()}`);
  state.indexEventsCache[key] = data;
  return data;
}

async function ensureEquityOverlayAndRender(btData) {
  const sym = state.equityOverlayIndexSymbol;
  if (!sym) {
    state.equityOverlayIndexData = null;
    if (btData) renderEquityChart(btData);
    return;
  }
  const s = btData?.series;
  const sDate = s?.dates?.[0] || $('#start-date')?.value || '2018-01-01';
  const eDate = s?.dates?.[s.dates.length - 1] || $('#end-date')?.value || '';
  try {
    const ov = await fetchIndexEventsData(sym, sDate, eDate);
    if (state.equityOverlayIndexSymbol === sym) {
      state.equityOverlayIndexData = ov;
      if (state.lastResult) renderEquityChart(state.lastResult);
    }
  } catch (e) {
    // 무시하고 기본 차트 유지
  }
}

async function loadAndRenderNationalIndex(symbolOverride = null) {
  if (symbolOverride) state.nationalIndexSymbol = symbolOverride;
  const sym = state.nationalIndexSymbol || '^KS11';
  const sDate = state.lastResult?.series?.dates?.[0] || $('#start-date')?.value || '2018-01-01';
  const eDate = state.lastResult?.series?.dates?.[state.lastResult.series.dates.length - 1] || $('#end-date')?.value || '';

  $$('#ni-chip-group .seg-btn').forEach((b) => {
    b.classList.toggle('active', b.dataset.ni === sym);
  });

  const strip = $('#ni-summary-strip');
  if (strip && !state.indexEventsCache[`${sym}|${sDate}|${eDate}`]) {
    strip.innerHTML = `<span class="ni-stat-muted">🌍 ${sym} 지수 및 주요사건 매핑 불러오는 중...</span>`;
  }

  try {
    const data = await fetchIndexEventsData(sym, sDate, eDate);
    if (state.nationalIndexSymbol !== sym) return;
    state.nationalIndexData = data;
    renderNationalIndexSection();
  } catch (err) {
    if (strip) strip.innerHTML = `<span class="ni-stat-muted">⚠️ 지수 데이터 로드 실패 (${_extEsc(err.message || err)})</span>`;
  }
}

function getFilteredNationalIndexEvents(data) {
  const allEvs = Array.isArray(data?.combined_events) ? data.combined_events : [];
  const f = state.nationalIndexFilter || 'all';
  if (f === 'kr') return allEvs.filter((e) => e.market === 'KR' || e.category === 'shock');
  if (f === 'macro') return allEvs.filter((e) => e.category === 'macro');
  if (f === 'shock') return allEvs.filter((e) => e.category === 'shock');
  return allEvs;
}

function zoomNationalIndexToEvent(dateStr) {
  const el = document.getElementById('chart-national-index');
  const s = state.nationalIndexData?.series;
  if (!el || !s || !Array.isArray(s.dates) || !s.dates.length || !dateStr) return;
  const minD = s.dates[0];
  const maxD = s.dates[s.dates.length - 1];
  const left = shiftDateDays(dateStr, -180);
  const right = shiftDateDays(dateStr, 180);
  const r0 = left < minD ? minD : left;
  const r1 = right > maxD ? maxD : right;
  Plotly.relayout(el, { 'xaxis.range': [r0, r1] });
  $('#national-index-card')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function renderNationalIndexSection() {
  const data = state.nationalIndexData;
  if (!data || !data.series || !Array.isArray(data.series.dates) || !data.series.dates.length) return;

  const s = data.series;
  const sum = data.summary || {};
  const isPct = state.nationalIndexUnit === 'pct';
  const isCandle = state.nationalIndexUnit === 'candle' && Array.isArray(s.opens) && s.opens.length > 0;
  const filteredEvs = getFilteredNationalIndexEvents(data);

  const strip = $('#ni-summary-strip');
  if (strip) {
    const ret = Number(sum.period_return_pct || 0);
    const mdd = Number(sum.mdd_pct || 0);
    strip.innerHTML = `
      <span class="ni-stat-chip"><span class="lbl">${_extEsc(data.flag || '🌍')} ${_extEsc(data.name || data.symbol)}</span><span class="val">${Number(sum.end_close || 0).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}</span></span>
      <span class="ni-stat-chip"><span class="lbl">기간 수익률</span><span class="val ${ret >= 0 ? 'pos' : 'neg'}">${ret >= 0 ? '+' : ''}${ret.toFixed(1)}%</span></span>
      <span class="ni-stat-chip"><span class="lbl">최대 낙폭</span><span class="val neg">${mdd.toFixed(1)}%</span></span>
      <span class="ni-stat-chip"><span class="lbl">거시 주요사건</span><span class="val">${sum.historical_count || 0}건</span></span>
      <span class="ni-stat-chip"><span class="lbl">지수 급변동 뉴스</span><span class="val">${sum.shock_count || 0}건</span></span>
      <button type="button" class="ni-zoom-btn" id="btn-ni-reset-zoom" title="전체 기간으로 차트 복원">↺ 전체 구간</button>
    `;
    const rstBtn = $('#btn-ni-reset-zoom');
    if (rstBtn) {
      rstBtn.onclick = () => {
        const el = document.getElementById('chart-national-index');
        if (el) Plotly.relayout(el, { 'xaxis.autorange': true, 'yaxis.autorange': true });
      };
    }
  }

  const yVals = isPct ? s.pct : s.closes;
  const traces = [
    isCandle
      ? {
          x: s.dates,
          open: s.opens,
          high: s.highs,
          low: s.lows,
          close: s.closes,
          name: `${data.short || data.symbol} (봉)`,
          type: 'candlestick',
          increasing: { line: { color: '#00D084', width: 1 }, fillcolor: '#00D084' },
          decreasing: { line: { color: '#FF4D4F', width: 1 }, fillcolor: '#FF4D4F' },
          xaxis: 'x',
          yaxis: 'y',
        }
      : {
          x: s.dates,
          y: yVals,
          name: `${data.short || data.symbol}`,
          type: 'scatter',
          mode: 'lines',
          line: { color: '#38BDF8', width: 2.2 },
          fill: 'tozeroy',
          fillcolor: 'rgba(56, 189, 248, 0.08)',
          xaxis: 'x',
          yaxis: 'y',
        },
    {
      x: s.dates,
      y: s.drawdown,
      name: '지수 낙폭(%)',
      type: 'scatter',
      mode: 'lines',
      line: { color: '#FB7185', width: 1.4 },
      fill: 'tozeroy',
      fillcolor: 'rgba(251, 113, 133, 0.15)',
      xaxis: 'x',
      yaxis: 'y2',
    },
  ];

  // 사건 마커 포인트 트레이스 (호버 시 +1M/+3M 임팩트 즉시 확인)
  if (filteredEvs.length) {
    traces.push({
      x: filteredEvs.map((e) => e.matched_date || e.date),
      y: filteredEvs.map((e) => (isPct ? e.index_pct : e.index_close)),
      name: '매핑 주요사건',
      type: 'scatter',
      mode: 'markers',
      marker: {
        size: 8,
        color: filteredEvs.map((e) => (e.type === 'bull' ? '#10B981' : (e.type === 'policy' ? '#38BDF8' : (e.type === 'shock' ? '#F59E0B' : '#F43F5E')))),
        line: { color: '#0F172A', width: 1.5 },
      },
      text: filteredEvs.map((e) => {
        const r1 = e.ret_1m != null ? `${e.ret_1m >= 0 ? '+' : ''}${e.ret_1m}%` : '-';
        const r3 = e.ret_3m != null ? `${e.ret_3m >= 0 ? '+' : ''}${e.ret_3m}%` : '-';
        return `${e.date} · ${e.full_title || e.label}<br>지수: ${e.index_close} · +1개월: ${r1} · +3개월: ${r3}`;
      }),
      hoverinfo: 'text',
      xaxis: 'x',
      yaxis: 'y',
    });
  }

  const evColors = {
    crisis: 'rgba(244, 63, 94, 0.72)',
    shock: 'rgba(245, 158, 11, 0.72)',
    bull: 'rgba(16, 185, 129, 0.72)',
    policy: 'rgba(56, 189, 248, 0.72)',
  };

  const layout = {
    ...PLOT_LAYOUT_BASE,
    grid: { rows: 2, columns: 1, pattern: 'coupled', roworder: 'top to bottom' },
    yaxis: {
      ...PLOT_LAYOUT_BASE.yaxis,
      domain: [0.25, 1.0],
      title: isPct ? '누적 수익률 (%)' : '지수 포인트',
    },
    yaxis2: {
      ...PLOT_LAYOUT_BASE.yaxis,
      domain: [0.0, 0.19],
      title: '낙폭 (%)',
    },
    xaxis: {
      ...PLOT_LAYOUT_BASE.xaxis,
      anchor: 'y2',
      ...(isCandle ? { rangeslider: { visible: false } } : {}),
    },
    shapes: [],
    annotations: [],
  };

  const visForChart = isMobileView() ? filteredEvs.slice(-8) : filteredEvs;
  visForChart.forEach((ev, idx) => {
    const dt = ev.matched_date || ev.date;
    const col = evColors[ev.type] || 'rgba(148, 163, 184, 0.6)';
    const isShock = ev.category === 'shock';
    layout.shapes.push({
      type: 'line',
      xref: 'x',
      yref: 'paper',
      x0: dt,
      x1: dt,
      y0: 0.25,
      y1: 1.0,
      line: { color: col, width: isShock ? 1.4 : 1.1, dash: isShock ? 'dashdot' : 'dot' },
    });
    const r1 = ev.ret_1m != null ? ` (+1M ${ev.ret_1m >= 0 ? '+' : ''}${ev.ret_1m}%)` : '';
    layout.annotations.push({
      x: dt,
      xref: 'x',
      y: isShock ? (0.98 - (idx % 2) * 0.085) : (0.83 - (idx % 4) * 0.075),
      yref: 'paper',
      text: ev.label,
      hovertext: `${ev.date} · ${ev.full_title || ev.label}${r1}`,
      hoverlabel: { bgcolor: '#0F172A', bordercolor: col, font: { color: '#F8FAFC', size: 11 } },
      showarrow: false,
      xanchor: 'left',
      font: { size: 9.5, color: isShock ? '#FDE68A' : '#E2E8F0' },
      bgcolor: isShock ? 'rgba(30, 41, 59, 0.90)' : 'rgba(15, 23, 42, 0.78)',
      bordercolor: col,
      borderwidth: 1,
      borderpad: 2,
    });
  });

  if (isCandle) {
    const rng = computeVisibleOhlcRange(s.dates, s.highs, s.lows, 'LAST_140', 'D');
    if (rng) {
      layout.xaxis.range = rng.xRange;
      if (rng.yRange) layout.yaxis.range = rng.yRange;
    }
  }
  const niEl = document.getElementById('chart-national-index');
  if (niEl && !$('#national-index-card')?.classList.contains('chart-fullscreen-card')) {
    niEl.style.height = isMobileView() ? '320px' : '500px';
  }
  Plotly.react('chart-national-index', traces, buildPlotLayout(layout), buildPlotCfg());

  // 사건 임팩트 테이블 렌더링
  const tblBox = $('#ni-events-table');
  if (!tblBox) return;
  if (!filteredEvs.length) {
    tblBox.innerHTML = '<div style="padding:12px;color:#94A3B8;font-size:0.8rem;">선택한 기간/필터에 해당하는 주요사건이 없습니다.</div>';
    return;
  }

  const fmtRet = (v) => {
    if (v == null || Number.isNaN(Number(v))) return '<span style="color:#64748B;">-</span>';
    const num = Number(v);
    return `<span class="ni-ret-pill ${num >= 0 ? 'pos' : 'neg'}">${num >= 0 ? '+' : ''}${num.toFixed(1)}%</span>`;
  };
  const catNames = { crisis: '위기', shock: '충격/급변', bull: '상승랠리', policy: '정책변화' };

  const rowsHtml = [...filteredEvs].reverse().map((ev) => {
    const dt = ev.matched_date || ev.date;
    const catCls = ev.type || 'shock';
    const catTxt = ev.category === 'shock' ? '지수뉴스' : (catNames[catCls] || '거시사건');
    const titleRaw = ev.full_title || ev.label || '';
    const titleHtml = ev.url
      ? `<a href="${_extEsc(ev.url)}" target="_blank" rel="noopener noreferrer" style="color:#F8FAFC;text-decoration:none;">${_extEsc(titleRaw)} ↗</a>`
      : _extEsc(titleRaw);
    return `
      <tr>
        <td style="font-variant-numeric:tabular-nums;white-space:nowrap;color:#CBD5E1;">${_extEsc(ev.date)}</td>
        <td><span class="ni-cat-badge ${catCls}">${_extEsc(catTxt)}</span></td>
        <td>${titleHtml}${ev.source ? ` <span style="color:#64748B;font-size:0.71rem;">(${_extEsc(ev.source)})</span>` : ''}</td>
        <td style="font-variant-numeric:tabular-nums;white-space:nowrap;">${Number(ev.index_close || 0).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}</td>
        <td>${fmtRet(ev.ret_1m)}</td>
        <td>${fmtRet(ev.ret_3m)}</td>
        <td><button type="button" class="ni-zoom-btn" data-zoom-date="${_extEsc(dt)}">🔍 확대</button></td>
      </tr>`;
  }).join('');

  tblBox.innerHTML = `
    <table class="ni-table">
      <thead>
        <tr>
          <th>날짜</th>
          <th>구분</th>
          <th>주요사건 · 당시 뉴스</th>
          <th>당일 지수</th>
          <th>+1개월 후</th>
          <th>+3개월 후</th>
          <th>차트</th>
        </tr>
      </thead>
      <tbody>${rowsHtml}</tbody>
    </table>
  `;

  $$('.ni-zoom-btn[data-zoom-date]', tblBox).forEach((btn) => {
    btn.onclick = () => zoomNationalIndexToEvent(btn.dataset.zoomDate);
  });
}

async function loadUsGurus() {
  const strip = $('#guru-summary-strip');
  try {
    const data = await apiJson('/api/us-gurus');
    state.guruData = data;
    renderUsGurusSection();
  } catch (err) {
    if (strip) {
      strip.innerHTML = `<div class="sub-empty">미국 유명 투자자 매수 데이터를 불러오지 못했습니다 (${_extEsc(err.message)}).</div>`;
    }
  }
}

function _guruSignedPct(v) {
  if (v == null || Number.isNaN(Number(v))) return '-';
  const n = Number(v);
  return `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`;
}

function renderUsGurusSection() {
  const data = state.guruData;
  const strip = $('#guru-summary-strip');
  const grid = $('#guru-picks-grid');
  if (!strip || !grid || !data) return;

  const esc = (v) => _extEsc(v == null ? '' : String(v));
  const gurus = data.gurus || [];
  const allPicks = data.picks || [];
  const activeFilter = state.guruFilter || 'all';

  $$('#guru-chip-group .seg-btn').forEach((b) => {
    b.classList.toggle('active', (b.dataset.guru || 'all') === activeFilter);
  });

  const filteredPicks = activeFilter === 'all'
    ? allPicks
    : allPicks.filter((p) => p.guru_id === activeFilter);

  const activeGuru = gurus.find((g) => g.id === activeFilter);
  if (activeGuru) {
    const avgCls = activeGuru.avg_ret_pct == null ? '' : (activeGuru.avg_ret_pct >= 0 ? 'style="color:#34d399"' : 'style="color:#fb7185"');
    strip.innerHTML = `
      <span>${esc(activeGuru.badge)} <strong>${esc(activeGuru.name)}</strong> (${esc(activeGuru.org)}) — ${esc(activeGuru.style_ko)}</span>
      <span>추적 ${activeGuru.pick_count}종목 · 평균 매수 대비 수익률 <strong ${avgCls}>${_guruSignedPct(activeGuru.avg_ret_pct)}</strong> · 기준 ${esc(data.updated_at || '-')}</span>
    `;
  } else {
    const validRets = filteredPicks.map((p) => p.ret_since_buy_pct).filter((v) => v != null && !Number.isNaN(Number(v)));
    const avgAll = validRets.length ? (validRets.reduce((a, b) => a + Number(b), 0) / validRets.length) : null;
    const avgCls = avgAll == null ? '' : (avgAll >= 0 ? 'style="color:#34d399"' : 'style="color:#fb7185"');
    strip.innerHTML = `
      <span>🦅 추적 구루 <strong>${gurus.length}명</strong> · 공시 픽 <strong>${filteredPicks.length}종목</strong> · 매수 대비 평균 <strong ${avgCls}>${_guruSignedPct(avgAll)}</strong></span>
      <span>${esc(data.note || '')} (갱신: ${esc(data.updated_at || '-')})</span>
    `;
  }

  if (!filteredPicks.length) {
    grid.innerHTML = '<div class="sub-empty">선택한 투자자의 최근 매수 공시 항목이 없습니다.</div>';
    return;
  }

  grid.innerHTML = filteredPicks.map((p) => {
    const actCls = p.action_type === 'add' ? 'add' : (p.action_type === 'call' ? 'call' : 'new');
    const buyCls = p.ret_since_buy_pct == null ? '' : (Number(p.ret_since_buy_pct) >= 0 ? 'pos' : 'neg');
    const m3Cls = p.ret_3m_pct == null ? '' : (Number(p.ret_3m_pct) >= 0 ? 'pos' : 'neg');
    const ddCls = p.dd_from_high_pct == null ? '' : (Number(p.dd_from_high_pct) <= -20 ? 'neg' : '');
    const estTxt = p.est_buy_price != null ? ` · 추정 매수가 $${Number(p.est_buy_price).toFixed(2)}` : '';
    return `
      <div class="guru-pick-card">
        <div class="guru-card-top">
          <div class="guru-stock-head">
            <div class="guru-ticker-line">
              <span class="guru-ticker-code">${esc(p.ticker)}</span>
              <span class="guru-stock-name">${esc(p.name_ko)}</span>
            </div>
            <span class="guru-sector-tag">${esc(p.sector_ko)}</span>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
            <span class="guru-badge">${esc(p.guru_badge)} ${esc(p.guru_name)}</span>
            <span class="guru-action-pill ${actCls}">${esc(p.action_label)}</span>
          </div>
        </div>

        <div class="guru-metrics-row">
          <div class="guru-metric">
            <span>현재가 (${esc(p.last_date || '-')})</span>
            <strong>${p.current_price != null ? '$' + Number(p.current_price).toFixed(2) : '-'}</strong>
          </div>
          <div class="guru-metric">
            <span>매수 대비</span>
            <strong class="${buyCls}">${_guruSignedPct(p.ret_since_buy_pct)}</strong>
          </div>
          <div class="guru-metric">
            <span>3개월 수익</span>
            <strong class="${m3Cls}">${_guruSignedPct(p.ret_3m_pct)}</strong>
          </div>
          <div class="guru-metric">
            <span>52주고점비</span>
            <strong class="${ddCls}">${p.dd_from_high_pct != null ? Number(p.dd_from_high_pct).toFixed(1) + '%' : '-'}</strong>
          </div>
        </div>

        <div class="guru-detail-box">
          <div class="guru-scale">${esc(p.filing_source)} · ${esc(p.scale_info)}${esc(estTxt)}</div>
          <div>${esc(p.detail_ko)}</div>
        </div>

        <div class="guru-thesis-text">💡 ${esc(p.thesis_ko)}</div>

        <div class="guru-card-actions">
          <button class="ghost-btn sm-btn" data-guru-run="${esc(p.ticker)}">🚀 바로 백테스트</button>
          <button class="ghost-btn sm-btn" data-guru-add="${esc(p.ticker)}">➕ 종목 추가</button>
          <span class="guru-meta-date">매수 ${esc(p.buy_date || '-')} · 공시 ${esc(p.filing_date || '-')}</span>
        </div>
      </div>
    `;
  }).join('');

  $$('[data-guru-run]', grid).forEach((btn) => {
    btn.onclick = () => {
      const ticker = btn.dataset.guruRun;
      if (ticker) _runGuruBacktest([ticker], `🦅 구루 픽 ${ticker} 단일 종목 백테스트를 실행합니다.`);
    };
  });

  $$('[data-guru-add]', grid).forEach((btn) => {
    btn.onclick = () => {
      const ticker = btn.dataset.guruAdd;
      if (!ticker) return;
      addTicker(ticker);
      const prev = btn.textContent;
      btn.textContent = '✅ 추가됨';
      setTimeout(() => { btn.textContent = prev; }, 1400);
    };
  });
}

function _runGuruBacktest(tickers, statusMsg) {
  const uniq = [...new Set((tickers || []).filter(Boolean))];
  if (!uniq.length) return;
  setMode('strategy');
  state.tickers = uniq;
  const w = Math.round((100 / uniq.length) * 10) / 10;
  state.allocWeights = Object.fromEntries(uniq.map((t) => [t, w]));
  renderSelectedTickers();
  renderAllocWeights();
  setStatus(statusMsg, 'info');
  runBacktest(true);
  const topAnchor = $('#equity-card') || $('#tab-summary');
  if (topAnchor && !isMobileView()) topAnchor.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function runGuruFilteredBasket() {
  const picks = state.guruData?.picks || [];
  const filtered = state.guruFilter === 'all'
    ? picks
    : picks.filter((p) => p.guru_id === state.guruFilter);
  const uniq = [...new Set(filtered.map((p) => p.ticker).filter(Boolean))];
  const label = state.guruFilter === 'all'
    ? '전체 구루'
    : ((state.guruData?.gurus || []).find((g) => g.id === state.guruFilter)?.name || state.guruFilter);
  _runGuruBacktest(uniq, `🦅 ${label} 매수 바스켓(${uniq.length}종목) 백테스트를 실행합니다.`);
}

function renderRadarChart(data) {
  const radar = data.grade?.radar || {};
  const cats = Object.keys(radar);
  const vals = Object.values(radar);
  if (!cats.length) return;

  const traces = [{
    type: 'scatterpolar',
    r: [...vals, vals[0]],
    theta: [...cats, cats[0]],
    fill: 'toself',
    fillcolor: 'rgba(59, 130, 246, 0.24)',
    line: { color: '#60A5FA', width: 2 },
    name: '전략 균형',
  }];
  const layout = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    font: PLOT_LAYOUT_BASE.font,
    margin: { l: 42, r: 42, t: 24, b: 24 },
    polar: {
      bgcolor: 'rgba(9,14,26,0.5)',
      radialaxis: { visible: true, range: [0, 100], gridcolor: 'rgba(148,163,184,0.14)', tickfont: { size: 9 } },
      angularaxis: { gridcolor: 'rgba(148,163,184,0.14)' },
    },
    showlegend: false,
  };
  Plotly.react('chart-radar', traces, layout, PLOT_CFG);
}

function renderCoach(data) {
  const g = data.grade || {};
  const box = $('#coach-grid');
  const mkList = (arr, emptyMsg) => arr && arr.length
    ? `<ul>${arr.map((x) => `<li>${x}</li>`).join('')}</ul>`
    : `<div style="font-size:11.5px;color:#64748B;">${emptyMsg}</div>`;

  box.innerHTML = `
    <div class="coach-col"><h4 style="color:#34D399;">✅ 잘한 점</h4>${mkList(g.strengths, '특별히 두드러진 강점 지표 없음')}</div>
    <div class="coach-col"><h4 style="color:#FCA5A5;">⚠️ 주의할 점</h4>${mkList(g.warnings, '치명적인 위험 신호가 발견되지 않았습니다')}</div>
    <div class="coach-col"><h4 style="color:#60A5FA;">💡 개선 아이디어</h4>${mkList(g.tips, '현재 설정이 균형 잡혀 있습니다')}</div>
  `;

  const oos = $('#oos-box');
  if (data.is_oos && data.is_oos['OOS Start']) {
    const o = data.is_oos;
    oos.innerHTML = `<strong>🔍 과적합 검증 분할 (${o['OOS Start']} 이후 검증 구간)</strong> — ` +
      `학습구간(IS): 수익률 <strong>${fmtPct(o['IS Return (%)'])}</strong> · MDD ${o['IS MDD (%)']}% · 샤프 ${o['IS Sharpe']} &nbsp;|&nbsp; ` +
      `실전검증(OOS): 수익률 <strong>${fmtPct(o['OOS Return (%)'])}</strong> · MDD ${o['OOS MDD (%)']}% · 샤프 ${o['OOS Sharpe']}`;
    oos.classList.remove('hidden');
  } else {
    oos.classList.add('hidden');
  }
}

function renderMetricsGrid(metrics) {
  const box = $('#metrics-detail-grid');
  const help = state.init?.metric_help || {};
  const keys = Object.keys(help);
  box.innerHTML = keys.map((k) => {
    const [title, desc] = help[k];
    let val = metrics[k];
    if (k === 'Total Fees' || k === 'Net Profit') val = fmtKRW(val);
    else if (k.includes('(%)')) val = val !== undefined ? `${val}%` : '-';
    return `
      <div class="m-item">
        <div class="m-name">${title}</div>
        <div class="m-val">${val ?? '-'}</div>
        <div class="m-desc">${desc}</div>
      </div>
    `;
  }).join('');
}

// ─── TAB 2: 수식 실험 비교 (A/B 보드) ───
function renderExperimentsBoard() {
  const tbody = $('#experiments-table tbody');
  if (!state.experiments.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="empty-td">아직 기록된 수식 실험이 없습니다.</td></tr>';
    return;
  }

  const baseExp = state.experiments.find((e) => e.id === state.baselineExpId) || state.experiments[state.experiments.length - 1];
  const baseRet = Number(baseExp?.metrics?.['Total Return (%)'] || 0);

  // 비교 차트
  const colors = ['#00D084', '#3B82F6', '#F5B041', '#EC4899', '#8B5CF6', '#06B6D4', '#F97316'];
  const traces = state.experiments.slice(0, 7).map((exp, idx) => ({
    x: exp.dates,
    y: exp.equity_pct,
    type: 'scatter',
    mode: 'lines',
    name: `#${exp.id} ${exp.entry_expr.slice(0, 26)}`,
    line: {
      color: colors[idx % colors.length],
      width: exp.id === state.baselineExpId ? 3 : 1.8,
      dash: exp.id === state.baselineExpId ? 'solid' : 'solid',
    },
  }));
  Plotly.react('chart-experiments', traces, {
    ...PLOT_LAYOUT_BASE,
    yaxis: { ...PLOT_LAYOUT_BASE.yaxis, title: '누적 수익률 (%)' },
  }, PLOT_CFG);

  tbody.innerHTML = '';
  state.experiments.forEach((exp) => {
    const m = exp.metrics || {};
    const ret = Number(m['Total Return (%)'] || 0);
    const delta = ret - baseRet;
    const isBase = exp.id === state.baselineExpId;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>#${exp.id}</strong> ${isBase ? '<span class="badge-muted" style="color:#FBBF24;">기준</span>' : ''}</td>
      <td>${exp.tickers.join(',')}</td>
      <td class="mono-cell" title="${exp.entry_expr}">${exp.entry_expr}</td>
      <td class="mono-cell" title="${exp.exit_expr}">${exp.exit_expr || '-'}</td>
      <td class="${ret >= 0 ? 'pos' : 'neg'}"><strong>${fmtPct(ret)}</strong></td>
      <td class="${delta >= 0 ? 'pos' : 'neg'}">${isBase ? '기준' : (delta >= 0 ? '+' : '') + delta.toFixed(1) + '%p'}</td>
      <td>${fmtPct(m['CAGR (%)'])}</td>
      <td class="neg">${fmtPct(m['MDD (%)'], false)}</td>
      <td>${Number(m['Sharpe Ratio'] || 0).toFixed(2)}</td>
      <td>${m['Win Rate (%)']}% (${m['Total Trades']}회)</td>
      <td style="display:flex;gap:4px;"></td>
    `;
    const actTd = tr.lastElementChild;

    const restoreBtn = document.createElement('button');
    restoreBtn.className = 'btn btn-outline btn-xs';
    restoreBtn.textContent = '↩ 복원';
    restoreBtn.onclick = async () => {
      setMode('strategy');
      state.tickers = [...exp.tickers];
      renderSelectedTickers();
      $('#strategy-select').value = 'custom';
      onStrategySelectChange(false);
      $('#entry-input').value = exp.entry_expr;
      $('#exit-input').value = exp.exit_expr;
      await inspectFormula(false);
      runBacktest();
    };

    const pinBtn = document.createElement('button');
    pinBtn.className = 'btn btn-ghost btn-xs';
    pinBtn.textContent = exp.pinned ? '📌 고정됨' : '고정';
    pinBtn.onclick = () => {
      exp.pinned = !exp.pinned;
      renderExperimentsBoard();
    };

    const baseBtn = document.createElement('button');
    baseBtn.className = 'btn btn-ghost btn-xs';
    baseBtn.textContent = '기준';
    baseBtn.title = '이 실험을 기준(Baseline)으로 삼아 수익률 차이를 비교합니다';
    baseBtn.onclick = () => {
      state.baselineExpId = exp.id;
      renderExperimentsBoard();
    };

    actTd.append(restoreBtn, pinBtn, baseBtn);
    tbody.appendChild(tr);
  });
}

// ─── TAB 3: 매매 타점 캔들 & 거래내역 ───
function renderCandleAndTrades(data) {
  const c = data.candle;
  if (c && c.dates && c.dates.length) {
    const tfName = c.tf === 'M' ? '월봉' : (c.tf === 'W' ? '주봉' : '일봉');
    $('#candle-title').textContent = `🕯️ ${findUniverseName(c.ticker)} (${tfName}) — 캔들 · 수식 지표 · 매수(▲)/매도(▼) 타점`;
    const hasOsc = Array.isArray(c.oscillators) && c.oscillators.length > 0;
    const candleEl = $('#chart-candle');
    if (candleEl && !$('#candle-card')?.classList.contains('chart-fullscreen-card')) {
      candleEl.style.height = isMobileView() ? (hasOsc ? '420px' : '370px') : (hasOsc ? '700px' : '640px');
    }

    const volAxisY = hasOsc ? 'y3' : 'y2';
    const traces = [
      {
        x: c.dates, open: c.open, high: c.high, low: c.low, close: c.close,
        type: 'candlestick', name: `${c.ticker} (${tfName})`,
        increasing: { line: { color: '#00D084' }, fillcolor: 'rgba(0,208,132,0.85)' },
        decreasing: { line: { color: '#FF4D4F' }, fillcolor: 'rgba(255,77,79,0.85)' },
        xaxis: 'x', yaxis: 'y',
      },
      { x: c.dates, y: c.ma20, name: 'MA(20)', type: 'scatter', mode: 'lines', line: { color: 'rgba(245,176,65,0.55)', width: 1.1 }, xaxis: 'x', yaxis: 'y' },
      { x: c.dates, y: c.ma60, name: 'MA(60)', type: 'scatter', mode: 'lines', line: { color: 'rgba(139,92,246,0.55)', width: 1.1 }, xaxis: 'x', yaxis: 'y' },
      { x: c.dates, y: c.bb_upper, name: 'BB 상단', type: 'scatter', mode: 'lines', line: { color: 'rgba(148,163,184,0.3)', width: 1, dash: 'dot' }, xaxis: 'x', yaxis: 'y' },
      { x: c.dates, y: c.bb_lower, name: 'BB 하단', type: 'scatter', mode: 'lines', line: { color: 'rgba(148,163,184,0.3)', width: 1, dash: 'dot' }, xaxis: 'x', yaxis: 'y' },
    ];

    const ovColors = ['#38BDF8', '#F472B6', '#FACC15', '#34D399', '#FB923C'];
    (c.custom_overlays || []).forEach((ov, idx) => {
      traces.push({
        x: c.dates,
        y: ov.values,
        name: `📐 ${ov.name}`,
        type: 'scatter',
        mode: 'lines',
        line: { color: ovColors[idx % ovColors.length], width: 2.2 },
        xaxis: 'x',
        yaxis: 'y',
      });
    });

    if (hasOsc) {
      const oscColors = ['#F59E0B', '#EC4899', '#22D3EE'];
      c.oscillators.forEach((osc, idx) => {
        traces.push({
          x: c.dates,
          y: osc.values,
          name: `📊 ${osc.name}`,
          type: 'scatter',
          mode: 'lines',
          line: { color: oscColors[idx % oscColors.length], width: 1.8 },
          xaxis: 'x',
          yaxis: 'y2',
        });
      });
    }

    traces.push({
      x: c.dates, y: c.volume, name: '거래량', type: 'bar',
      marker: { color: c.close.map((cl, i) => cl >= c.open[i] ? 'rgba(0,208,132,0.45)' : 'rgba(255,77,79,0.45)') },
      xaxis: 'x', yaxis: volAxisY, showlegend: false,
    });

    if (c.buys && c.buys.length) {
      traces.push({
        x: c.buys.map((b) => b.date),
        y: c.buys.map((b) => b.price),
        mode: 'markers',
        name: '매수 ▲',
        marker: { symbol: 'triangle-up', size: 12, color: '#00E676', line: { color: '#052E16', width: 1 } },
        text: c.buys.map((b) => `매수 ${Math.round(b.price).toLocaleString()}원 (${b.shares}주 · ${b.raw_date || b.date})`),
        xaxis: 'x', yaxis: 'y',
      });
    }
    if (c.sells && c.sells.length) {
      traces.push({
        x: c.sells.map((s) => s.date),
        y: c.sells.map((s) => s.price),
        mode: 'markers',
        name: '매도 ▼',
        marker: { symbol: 'triangle-down', size: 12, color: '#FF4D4F', line: { color: '#450A0A', width: 1 } },
        text: c.sells.map((s) => `매도 ${Math.round(s.price).toLocaleString()}원 (${s.return_pct.toFixed(1)}% · ${s.reason} · ${s.raw_date || s.date})`),
        xaxis: 'x', yaxis: 'y',
      });
    }

    const candleLayout = hasOsc ? {
      ...PLOT_LAYOUT_BASE,
      grid: { rows: 3, columns: 1, pattern: 'coupled', roworder: 'top to bottom' },
      xaxis: { ...PLOT_LAYOUT_BASE.xaxis, rangeslider: { visible: false }, anchor: 'y3' },
      yaxis: { ...PLOT_LAYOUT_BASE.yaxis, domain: [0.38, 1.0], title: '주가' },
      yaxis2: { ...PLOT_LAYOUT_BASE.yaxis, domain: [0.17, 0.34], title: '보조지표' },
      yaxis3: { ...PLOT_LAYOUT_BASE.yaxis, domain: [0.0, 0.13], title: '거래량' },
    } : {
      ...PLOT_LAYOUT_BASE,
      grid: { rows: 2, columns: 1, pattern: 'coupled', roworder: 'top to bottom' },
      xaxis: { ...PLOT_LAYOUT_BASE.xaxis, rangeslider: { visible: false }, anchor: 'y2' },
      yaxis: { ...PLOT_LAYOUT_BASE.yaxis, domain: [0.24, 1.0], title: '주가' },
      yaxis2: { ...PLOT_LAYOUT_BASE.yaxis, domain: [0.0, 0.18], title: '거래량' },
    };

    const rng = computeVisibleOhlcRange(c.dates, c.high, c.low, state.candleZoom || 'AUTO', c.tf || 'D');
    if (rng) {
      candleLayout.xaxis.range = rng.xRange;
      if (rng.yRange) candleLayout.yaxis.range = rng.yRange;
    }

    Plotly.react('chart-candle', traces, buildPlotLayout(candleLayout), buildPlotCfg());
  }

  // 거래 수익률 분포 & MAE/MFE
  const completed = (data.trades || []).filter((t) => !t.is_open);
  if (completed.length) {
    const rets = completed.map((t) => Number(t.return_pct || 0));
    Plotly.react('chart-trade-dist', [{
      x: rets,
      type: 'histogram',
      nbinsx: 24,
      marker: { color: '#3B82F6', line: { color: '#93C5FD', width: 0.7 } },
    }], {
      ...PLOT_LAYOUT_BASE,
      xaxis: { ...PLOT_LAYOUT_BASE.xaxis, title: '거래 수익률 (%)' },
      yaxis: { ...PLOT_LAYOUT_BASE.yaxis, title: '거래 수' },
    }, PLOT_CFG);

    Plotly.react('chart-mae-mfe', [{
      x: completed.map((t) => Number(t.mae_pct || 0)),
      y: completed.map((t) => Number(t.mfe_pct || 0)),
      mode: 'markers',
      type: 'scatter',
      text: completed.map((t) => `${t.ticker} (${t.entry_date}) 수익 ${Number(t.return_pct).toFixed(1)}%`),
      marker: {
        size: 8,
        color: completed.map((t) => Number(t.return_pct) > 0 ? '#00D084' : '#FF4D4F'),
        opacity: 0.85,
      },
    }], {
      ...PLOT_LAYOUT_BASE,
      hovermode: 'closest',
      xaxis: { ...PLOT_LAYOUT_BASE.xaxis, title: '진입 후 장중 최대역행 MAE (%)' },
      yaxis: { ...PLOT_LAYOUT_BASE.yaxis, title: '진입 후 장중 최대수익 MFE (%)' },
    }, PLOT_CFG);
  } else {
    Plotly.react('chart-trade-dist', [], PLOT_LAYOUT_BASE, PLOT_CFG);
    Plotly.react('chart-mae-mfe', [], PLOT_LAYOUT_BASE, PLOT_CFG);
  }

  renderTradesTable();
}

function renderTradesTable() {
  const trades = state.lastResult?.trades || [];
  const tbody = $('#trades-table tbody');
  const filtered = trades.filter((t) => {
    if (state.tradeFilter === 'win') return Number(t.return_pct || 0) > 0;
    if (state.tradeFilter === 'loss') return Number(t.return_pct || 0) <= 0;
    return true;
  });

  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty-td">조건에 해당하는 거래 내역이 없습니다.</td></tr>';
    return;
  }

  tbody.innerHTML = filtered.slice().reverse().map((t) => {
    const r = Number(t.return_pct || 0);
    const pnl = Number(t.pnl || 0);
    return `
      <tr>
        <td><strong>${t.ticker}</strong></td>
        <td>${t.entry_date}</td>
        <td>${t.is_open ? '<span style="color:#60A5FA;">보유 중</span>' : t.exit_date}</td>
        <td>${Number(t.entry_price).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}</td>
        <td>${Number(t.exit_price).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}</td>
        <td>${Number(t.shares).toLocaleString('ko-KR')}</td>
        <td class="${pnl >= 0 ? 'pos' : 'neg'}">${pnl >= 0 ? '+' : ''}${fmtKRW(pnl)}</td>
        <td class="${r >= 0 ? 'pos' : 'neg'}"><strong>${fmtPct(r)}</strong></td>
        <td>${t.hold_days}일</td>
        <td>${t.reason}</td>
      </tr>
    `;
  }).join('');
}

// ─── TAB 5: 미래 시뮬 & 월별·위기 ───
function renderFutureTab(data) {
  const mc = data.monte_carlo;
  const kpiBox = $('#mc-kpi-row');
  if (mc && mc.dates) {
    kpiBox.innerHTML = `
      <div class="kpi-card"><div class="kpi-label">원금 플러스 확률</div><div class="kpi-value pos">${mc.prob_profit.toFixed(1)}%</div></div>
      <div class="kpi-card"><div class="kpi-label">+20% 이상 확률</div><div class="kpi-value" style="color:#60A5FA;">${mc.prob_plus20.toFixed(1)}%</div></div>
      <div class="kpi-card"><div class="kpi-label">하위 10% (비관)</div><div class="kpi-value ${mc.p10_ret >= 0 ? 'pos' : 'neg'}">${fmtCompactKRW(mc.p10_final)}</div><div class="kpi-sub">${fmtPct(mc.p10_ret)}</div></div>
      <div class="kpi-card"><div class="kpi-label">중앙값 50% (기본)</div><div class="kpi-value pos">${fmtCompactKRW(mc.p50_final)}</div><div class="kpi-sub">${fmtPct(mc.p50_ret)}</div></div>
      <div class="kpi-card"><div class="kpi-label">상위 10% (낙관)</div><div class="kpi-value pos">${fmtCompactKRW(mc.p90_final)}</div><div class="kpi-sub">${fmtPct(mc.p90_ret)}</div></div>
    `;
    const mcTraces = [
      { x: mc.dates, y: mc.p90, name: '상위 10%', type: 'scatter', mode: 'lines', line: { color: 'rgba(0,208,132,0.55)', dash: 'dot', width: 1.5 } },
      { x: mc.dates, y: mc.p10, name: '하위 10%', type: 'scatter', mode: 'lines', fill: 'tonexty', fillcolor: 'rgba(59,130,246,0.1)', line: { color: 'rgba(255,77,79,0.6)', dash: 'dot', width: 1.5 } },
      { x: mc.dates, y: mc.p75, name: '상위 25%', type: 'scatter', mode: 'lines', line: { width: 0 }, showlegend: false },
      { x: mc.dates, y: mc.p25, name: '25~75% 구간', type: 'scatter', mode: 'lines', fill: 'tonexty', fillcolor: 'rgba(59,130,246,0.22)', line: { width: 0 } },
      { x: mc.dates, y: mc.p50, name: '중앙값 (50%)', type: 'scatter', mode: 'lines', line: { color: '#60A5FA', width: 2.8 } },
    ];
    Plotly.react('chart-mc', mcTraces, {
      ...PLOT_LAYOUT_BASE,
      yaxis: { ...PLOT_LAYOUT_BASE.yaxis, title: '예상 자산 (원)' },
    }, PLOT_CFG);
  }

  // 연도별 바 차트
  if (data.yearly && data.yearly.length) {
    const yrs = data.yearly.map((y) => String(y['연도']));
    const tracesY = [{
      x: yrs, y: data.yearly.map((y) => y['전략 수익률(%)']),
      name: '내 전략', type: 'bar',
      marker: { color: data.yearly.map((y) => y['전략 수익률(%)'] >= 0 ? '#00D084' : '#FF4D4F') },
    }];
    if (data.yearly[0]['벤치마크(%)'] !== undefined) {
      tracesY.push({
        x: yrs, y: data.yearly.map((y) => y['벤치마크(%)']),
        name: '벤치마크', type: 'bar', marker: { color: 'rgba(148,163,184,0.55)' },
      });
    }
    Plotly.react('chart-yearly', tracesY, {
      ...PLOT_LAYOUT_BASE,
      barmode: 'group',
      yaxis: { ...PLOT_LAYOUT_BASE.yaxis, title: '수익률 (%)' },
    }, PLOT_CFG);
  }

  // 6개월 롤링 수익률
  if (data.rolling_6m && data.rolling_6m.dates.length) {
    Plotly.react('chart-rolling', [{
      x: data.rolling_6m.dates,
      y: data.rolling_6m.values,
      type: 'scatter', mode: 'lines',
      line: { color: '#38BDF8', width: 2 },
      fill: 'tozeroy', fillcolor: 'rgba(56,189,248,0.12)',
      name: '6M 연환산(%)',
    }], {
      ...PLOT_LAYOUT_BASE,
      yaxis: { ...PLOT_LAYOUT_BASE.yaxis, title: '연환산 수익률 (%)' },
    }, PLOT_CFG);
  }

  // 월별 히트맵 테이블
  const mBox = $('#monthly-heatmap-table');
  const mt = data.monthly_table;
  if (mt && mt.years && mt.years.length) {
    const colorCell = (val) => {
      if (val === null || val === undefined) return '<td>-</td>';
      const intensity = Math.min(0.45, Math.abs(val) / 22);
      const bg = val >= 0 ? `rgba(0, 208, 132, ${0.08 + intensity})` : `rgba(255, 77, 79, ${0.08 + intensity})`;
      return `<td class="month-cell" style="background:${bg};color:${val >= 0 ? '#D1FAE5' : '#FEE2E2'};">${val.toFixed(1)}%</td>`;
    };
    mBox.innerHTML = `
      <table class="data-table">
        <thead><tr><th>연도</th>${mt.columns.map((c) => `<th style="text-align:center;">${c}</th>`).join('')}</tr></thead>
        <tbody>
          ${mt.years.map((yr, rIdx) => `<tr><td><strong>${yr}</strong></td>${mt.values[rIdx].map(colorCell).join('')}</tr>`).join('')}
        </tbody>
      </table>
    `;
  } else {
    mBox.innerHTML = '<div class="empty-td">월별 데이터가 부족합니다.</div>';
  }

  // 최악의 하락 구간 TOP 5
  const uBody = $('#underwater-table tbody');
  if (data.underwater && data.underwater.length) {
    uBody.innerHTML = data.underwater.map((u) => `
      <tr>
        <td><strong>#${u['순위']}</strong></td>
        <td>${u['고점일']}</td>
        <td>${u['저점일']}</td>
        <td>${u['회복일']}</td>
        <td class="neg"><strong>${u['최대낙폭(%)']}%</strong></td>
        <td>${u['하락기간(일)']}일</td>
        <td>${u['회복까지(일)']}일</td>
      </tr>
    `).join('');
  } else {
    uBody.innerHTML = '<tr><td colspan="7" class="empty-td">유의미한 하락 구간이 없습니다.</td></tr>';
  }
}

// ═══════════════════════════════════════════════════════════════
// 최적화 (2D 히트맵) & 13개 전략 대결
// ═══════════════════════════════════════════════════════════════

async function runOptimize() {
  switchTab('optimize');
  setStatus('🔬 최적 숫자 조합 탐색 중...', 'loading');
  showError('');
  try {
    const payload = collectBacktestPayload();
    payload.opt_mode = $('#opt-mode-select').value;
    payload.sort_by = $('#opt-sort-select').value;
    payload.sync_linked = $('#sync-linked-nums') ? $('#sync-linked-nums').checked : true;
    const s1 = $('#opt-slot1')?.value;
    const s2 = $('#opt-slot2')?.value;
    const slotIndices = [];
    if (s1 !== '' && s1 !== undefined) slotIndices.push(Number(s1));
    if (s2 !== '' && s2 !== undefined && s2 !== s1) slotIndices.push(Number(s2));
    if (slotIndices.length) payload.slot_indices = slotIndices;

    const res = await apiJson('/api/optimize', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    state.lastOptimize = res;
    renderOptimizeResult(res);
    setStatus('🟢 최적화 완료', 'ok');
  } catch (err) {
    setStatus('🔴 최적화 오류', 'error');
    showError(err.message);
  }
}

function renderOptimizeResult(res) {
  const wb = $('#opt-winner-banner');
  const best = res.best || {};
  const availSlots = res.available_slots || best.available_slots;
  if (Array.isArray(availSlots) && availSlots.length) {
    populateOptSlots(availSlots);
  }
  if (best.entry_expr) {
    wb.innerHTML = `
      <div>
        <strong>🏆 1등 조합 발견! (${best.summary || ''})</strong><br>
        <span style="font-family:var(--font-mono);font-size:11.5px;color:#D1FAE5;">매수: ${best.entry_expr} | 매도: ${best.exit_expr || '-'}</span>
      </div>
      <button class="btn btn-primary btn-sm" id="btn-apply-opt-best">✨ 1등 수식 즉시 적용 &amp; 실행</button>
    `;
    wb.classList.remove('hidden');
    $('#btn-apply-opt-best').onclick = () => applyOptimizedFormula(best.entry_expr, best.exit_expr);
  } else {
    wb.classList.add('hidden');
  }

  renderOptimizeHeatmap();

  const tbody = $('#opt-table tbody');
  const rows = res.rows || [];
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-td">탐색 결과가 없습니다.</td></tr>';
    return;
  }
  tbody.innerHTML = '';
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    const ret = Number(r['누적수익률(%)'] || 0);
    tr.innerHTML = `
      <td><strong>#${r['순위']}</strong></td>
      <td>${r['파라미터']}</td>
      <td class="${ret >= 0 ? 'pos' : 'neg'}"><strong>${fmtPct(ret)}</strong></td>
      <td>${fmtPct(r['연평균CAGR(%)'])}</td>
      <td class="neg">${fmtPct(r['최대낙폭MDD(%)'], false)}</td>
      <td><strong>${Number(r['샤프지수'] || 0).toFixed(2)}</strong></td>
      <td>${r['승률(%)']}%</td>
      <td>${r['거래횟수']}</td>
      <td></td>
    `;
    if (r._entry) {
      const btn = document.createElement('button');
      btn.className = 'btn btn-outline btn-xs';
      btn.textContent = '적용';
      btn.onclick = () => applyOptimizedFormula(r._entry, r._exit || '');
      tr.lastElementChild.appendChild(btn);
    }
    tbody.appendChild(tr);
  });
}

function renderOptimizeHeatmap() {
  const hm = state.lastOptimize?.heatmap;
  const wrap = $('#opt-heatmap-wrap');
  if (!hm || !hm.z_sharpe) {
    wrap.classList.add('hidden');
    return;
  }
  wrap.classList.remove('hidden');
  const z = (state.hmMetric === 'ret' && hm.z_ret) ? hm.z_ret : hm.z_sharpe;
  Plotly.react('chart-heatmap', [{
    z, x: hm.x, y: hm.y,
    type: 'heatmap',
    colorscale: 'RdYlGn',
    hoverongaps: false,
  }], {
    ...PLOT_LAYOUT_BASE,
    hovermode: 'closest',
    xaxis: { ...PLOT_LAYOUT_BASE.xaxis, title: hm.x_label, type: 'category' },
    yaxis: { ...PLOT_LAYOUT_BASE.yaxis, title: hm.y_label, type: 'category' },
  }, PLOT_CFG);
}

async function applyOptimizedFormula(en, ex) {
  setMode('strategy');
  $('#strategy-select').value = 'custom';
  onStrategySelectChange(false);
  $('#entry-input').value = en;
  $('#exit-input').value = ex || '';
  pushFormulaHistory();
  await inspectFormula(false);
  runBacktest();
}

async function runScanner() {
  switchTab('battle');
  const tbody = $('#scanner-table tbody');
  if (tbody) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty-td">⏳ 여러 종목 데이터를 불러와 수식을 일괄 시뮬레이션하고 있습니다...</td></tr>';
  }
  setStatus('📡 전 종목 신호 및 궁합 스캔 중...', 'loading');
  showError('');
  try {
    const payload = collectBacktestPayload();
    payload.basket = $('#scanner-basket-select')?.value || 'core10';
    const res = await apiJson('/api/scanner', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    renderScannerResult(res);
    setStatus(`🟢 스캔 완료 (${res.scanned || 0}종목)`, 'ok');
  } catch (err) {
    setStatus('🔴 스캔 오류', 'error');
    showError(err.message);
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="10" class="empty-td">오류: ${err.message}</td></tr>`;
    }
  }
}

function renderScannerResult(res) {
  const tbody = $('#scanner-table tbody');
  if (!tbody) return;
  const rows = res.rows || [];
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty-td">스캔된 종목이 없습니다.</td></tr>';
    return;
  }
  tbody.innerHTML = '';
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    const st = String(r.signal_status || 'WATCHING').toUpperCase();
    const isBuy = st.includes('BUY');
    const isSell = st.includes('SELL') || st.includes('EXIT');
    const isHold = st.includes('HOLD');
    const badgeColor = isBuy ? '#00D084' : (isSell ? '#FF4D4F' : (isHold ? '#60A5FA' : '#94A3B8'));
    const badgeBg = isBuy ? 'rgba(0,208,132,0.16)' : (isSell ? 'rgba(255,77,79,0.16)' : (isHold ? 'rgba(59,130,246,0.16)' : 'rgba(148,163,184,0.12)'));
    const ret = Number(r.total_return || 0);
    const alpha = Number(r.alpha || 0);
    tr.innerHTML = `
      <td><span style="display:inline-block;padding:2px 7px;border-radius:999px;font-weight:700;font-size:11px;background:${badgeBg};color:${badgeColor};">${r.signal_badge || st}</span></td>
      <td><strong>${r.name || r.ticker}</strong> <span style="color:#64748B;font-size:11px;">${r.ticker}</span></td>
      <td>${r.last_entry_date || '-'}</td>
      <td><span class="grade-badge">${r.grade || '-'}</span></td>
      <td class="${ret >= 0 ? 'pos' : 'neg'}"><strong>${fmtPct(ret)}</strong></td>
      <td class="${alpha >= 0 ? 'pos' : 'neg'}">${fmtPct(alpha)}</td>
      <td class="neg">${fmtPct(r.mdd || 0, false)}</td>
      <td><strong>${Number(r.sharpe || 0).toFixed(2)}</strong></td>
      <td>${Number(r.win_rate || 0).toFixed(0)}% (${r.trades || 0}회)</td>
      <td></td>
    `;
    const useBtn = document.createElement('button');
    useBtn.className = 'btn btn-outline btn-xs';
    useBtn.textContent = '이 종목 쓰기';
    useBtn.onclick = async () => {
      state.tickers = [r.ticker];
      renderSelectedTickers();
      switchTab('dashboard');
      await inspectFormula(true);
      runBacktest();
    };
    tr.lastElementChild.appendChild(useBtn);
    tbody.appendChild(tr);
  });
}

async function runCompare() {
  switchTab('battle');
  setStatus('🏆 13개 전략 대결 중...', 'loading');
  showError('');
  try {
    const res = await apiJson('/api/compare', {
      method: 'POST',
      body: JSON.stringify(collectBacktestPayload()),
    });
    state.lastCompare = res;
    renderCompareResult(res);
    setStatus('🟢 전략 대결 완료', 'ok');
  } catch (err) {
    setStatus('🔴 비교 오류', 'error');
    showError(err.message);
  }
}

function renderCompareResult(res) {
  const curves = res.curves;
  if (curves && curves.dates && curves.dates.length) {
    const traces = (curves.series || []).slice(0, 8).map((s) => ({
      x: curves.dates,
      y: s.pct,
      type: 'scatter',
      mode: 'lines',
      name: s.name,
      line: { width: 2 },
    }));
    Plotly.react('chart-compare', traces, {
      ...PLOT_LAYOUT_BASE,
      yaxis: { ...PLOT_LAYOUT_BASE.yaxis, title: '누적 수익률 (%)' },
    }, PLOT_CFG);
  }

  const tbody = $('#compare-table tbody');
  const rows = res.ranking || [];
  tbody.innerHTML = '';
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    const ret = Number(r['누적수익률(%)'] || 0);
    tr.innerHTML = `
      <td><strong>#${r['순위']}</strong></td>
      <td><span class="badge-muted">${r['등급']} (${r['종합점수']}점)</span></td>
      <td><strong>${r['전략명']}</strong></td>
      <td>${r['카테고리']}</td>
      <td class="${ret >= 0 ? 'pos' : 'neg'}"><strong>${fmtPct(ret)}</strong></td>
      <td>${fmtPct(r['연평균CAGR(%)'])}</td>
      <td class="neg">${fmtPct(r['최대낙폭MDD(%)'], false)}</td>
      <td>${Number(r['샤프지수'] || 0).toFixed(2)}</td>
      <td>${r['승률(%)']}%</td>
      <td>${r['거래횟수']}회</td>
      <td></td>
    `;
    const btn = document.createElement('button');
    btn.className = 'btn btn-outline btn-xs';
    btn.textContent = '이 전략 쓰기';
    btn.onclick = async () => {
      setMode('strategy');
      $('#strategy-select').value = r.strategy_id;
      onStrategySelectChange(false);
      await inspectFormula(false);
      runBacktest();
    };
    tr.lastElementChild.appendChild(btn);
    tbody.appendChild(tr);
  });
}

// ─── 사전 ───
function renderDictionary(filterText) {
  const box = $('#dict-content');
  if (!state.init) return;
  const q = (filterText || '').trim().toLowerCase();
  const cards = [];

  (state.init.indicator_reference || []).forEach((it) => {
    const txt = `${it.category} ${it.syntax} ${it.desc}`.toLowerCase();
    if (!q || txt.includes(q)) {
      cards.push(`<div class="dict-card"><div class="dict-cat">${it.category}</div><div class="dict-syn">${it.syntax}</div><div class="dict-desc">${it.desc}</div></div>`);
    }
  });
  Object.entries(state.init.metric_help || {}).forEach(([k, [title, desc]]) => {
    const txt = `${k} ${title} ${desc}`.toLowerCase();
    if (!q || txt.includes(q)) {
      cards.push(`<div class="dict-card"><div class="dict-cat">성과 지표 · ${k}</div><div class="dict-syn">${title}</div><div class="dict-desc">${desc}</div></div>`);
    }
  });
  box.innerHTML = cards.join('') || '<div class="empty-td">검색 결과가 없습니다.</div>';
}

// ═══════════════════════════════════════════════════════════════
// 이벤트 바인딩
// ═══════════════════════════════════════════════════════════════

function switchTab(tabId) {
  $$('.dash-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tabId));
  $$('.tab-pane').forEach((p) => p.classList.toggle('active', p.id === `tab-${tabId}`));
  if (window.innerWidth <= 900) {
    setMobilePane('right');
  }
  // 숨겨진 탭이 보일 때 Plotly 차트 너비 재계산
  setTimeout(() => {
    $$(`#tab-${tabId} .plotly-box`).forEach((el) => {
      if (el.data) Plotly.Plots.resize(el);
    });
  }, 40);
}

function updateRegimeKo() {
  apiJson('/api/formula/inspect', {
    method: 'POST',
    body: JSON.stringify({ entry_expr: $('#reg-expr').value, exit_expr: '' }),
  }).then((res) => {
    const el = $('#reg-expr-ko');
    el.textContent = '💬 ' + (res.entry_ko || '');
    el.classList.toggle('err', !res.entry_valid);
  }).catch(() => {});
}

function bindEvents() {
  // 글로벌 실행 & 상단 액션
  $('#btn-run-backtest').onclick = () => runBacktest(true);
  $('#btn-mobile-run').onclick = () => runBacktest(true);
  $$('#mobile-bottom-nav .mobile-nav-btn[data-mpane]').forEach((b) => {
    b.onclick = () => setMobilePane(b.dataset.mpane, b.dataset.mtab || null);
  });
  $('#btn-open-access').onclick = () => openAccessModal();
  $('#btn-close-access').onclick = () => $('#access-modal').classList.add('hidden');
  $('#access-modal').onclick = (e) => {
    if (e.target === $('#access-modal')) $('#access-modal').classList.add('hidden');
  };
  $('#btn-toggle-tunnel').onclick = () => togglePublicTunnel();
  $('#btn-copy-lan-url').onclick = () => copyAccessText($('#btn-copy-lan-url'), $('#lan-url-input').value);
  $('#btn-copy-public-url').onclick = () => copyAccessText($('#btn-copy-public-url'), $('#public-url-input').value);
  $('#btn-copy-public-formula-url').onclick = () => {
    const base = ($('#public-url-input').value || '').trim();
    if (base) copyAccessText($('#btn-copy-public-formula-url'), `${base}#${encodeShareHash()}`);
  };
  if ($('#btn-cloud-sync-now')) {
    $('#btn-cloud-sync-now').onclick = () => syncCloudStrategiesNow();
  }
  if ($('#btn-copy-cloud-url')) {
    $('#btn-copy-cloud-url').onclick = () => copyAccessText($('#btn-copy-cloud-url'), $('#cloud-url-input')?.value || '');
  }
  if ($('#cloud-auto-sync')) {
    $('#cloud-auto-sync').onchange = async () => {
      try {
        await apiJson('/api/cloud/config', {
          method: 'POST',
          body: JSON.stringify({ auto_sync_strategies: $('#cloud-auto-sync').checked }),
        });
        await refreshCloudConfig();
      } catch (e) {}
    };
  }
  if ($('#btn-github-deploy')) {
    $('#btn-github-deploy').onclick = () => deployToGithubCloud();
  }
  if ($('#btn-save-render-url')) {
    $('#btn-save-render-url').onclick = () => saveRenderCloudUrl();
  }
  if ($('#cloud-render-url')) {
    $('#cloud-render-url').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') saveRenderCloudUrl();
    });
  }
  $('#btn-top-compare').onclick = () => runCompare();
  $('#btn-top-optimize').onclick = () => runOptimize();
  $('#btn-run-compare').onclick = () => runCompare();
  $('#btn-run-optimize').onclick = () => runOptimize();
  $('#btn-run-scanner').onclick = () => runScanner();
  window.addEventListener('pointerup', () => { state.isDraggingTuner = false; });

  // Ctrl+Enter 실행, 수식 외 포커스 시 Ctrl+Z / Ctrl+Y 실행 취소·다시 실행
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'Enter') {
      e.preventDefault();
      runBacktest();
      return;
    }
    const tag = (e.target && e.target.tagName) ? e.target.tagName.toUpperCase() : '';
    const inEditor = tag === 'TEXTAREA' || tag === 'INPUT';
    if (!inEditor && e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === 'z') {
      e.preventDefault();
      undoFormula();
    } else if (!inEditor && e.ctrlKey && (e.key.toLowerCase() === 'y' || (e.shiftKey && e.key.toLowerCase() === 'z'))) {
      e.preventDefault();
      redoFormula();
    }
  });

  // 종목 검색 자동완성
  const ts = $('#ticker-search');
  const td = $('#ticker-dropdown');
  ts.addEventListener('input', () => {
    const q = ts.value.trim().toLowerCase();
    if (!q || !state.init) {
      td.classList.add('hidden');
      return;
    }
    const hits = state.init.universe.filter((u) =>
      u.code.toLowerCase().includes(q) || u.name.toLowerCase().includes(q)
    ).slice(0, 12);
    if (!hits.length) {
      td.classList.add('hidden');
      return;
    }
    td.innerHTML = '';
    hits.forEach((h) => {
      const row = document.createElement('div');
      row.className = 'ticker-option';
      row.innerHTML = `<span>${h.label}</span><span style="color:#64748B;">${h.market}</span>`;
      row.onmousedown = (e) => {
        e.preventDefault();
        addTicker(h.code);
      };
      td.appendChild(row);
    });
    td.classList.remove('hidden');
  });
  ts.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && ts.value.trim()) {
      e.preventDefault();
      addTicker(ts.value.trim().toUpperCase());
    }
  });
  ts.addEventListener('blur', () => setTimeout(() => td.classList.add('hidden'), 150));
  $('#btn-add-custom-ticker').onclick = () => {
    if (ts.value.trim()) addTicker(ts.value.trim().toUpperCase());
  };

  // 전체 상장종목 새로고침
  $('#btn-refresh-universe').onclick = async () => {
    setStatus('⏳ 한국·미국 전체 종목 불러오는 중...', 'loading');
    try {
      const res = await apiJson('/api/universe/refresh', { method: 'POST' });
      state.init.universe = res.universe;
      setStatus(`🟢 전체 ${res.count.toLocaleString()}종목 로드됨`, 'ok');
    } catch (e) {
      setStatus('🔴 로드 실패', 'error');
    }
  };

  // 기간 빠른 선택
  $$('#period-chips .mini-chip').forEach((btn) => {
    btn.onclick = () => {
      $$('#period-chips .mini-chip').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      const p = btn.dataset.period;
      const now = new Date();
      let startStr = '2010-01-01';
      if (p.endsWith('y')) {
        const yrs = parseInt(p, 10);
        const d = new Date(now.getFullYear() - yrs, now.getMonth(), now.getDate());
        startStr = d.toISOString().slice(0, 10);
      } else {
        startStr = `${p}-01-01`;
      }
      $('#start-date').value = startStr;
      $('#end-date').value = state.init?.today || now.toISOString().slice(0, 10);
      scheduleAutoRun();
    };
  });
  $('#start-date').onchange = () => scheduleAutoRun();
  $('#end-date').onchange = () => scheduleAutoRun();

  // 자본금 빠른 선택
  const updateCapHuman = () => {
    $('#capital-human').textContent = fmtCompactKRW($('#initial-capital').value);
  };
  $$('.cap-chip').forEach((btn) => {
    btn.onclick = () => {
      $$('.cap-chip').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      $('#initial-capital').value = btn.dataset.cap;
      updateCapHuman();
      scheduleAutoRun();
    };
  });
  $('#initial-capital').oninput = updateCapHuman;
  $('#initial-capital').onchange = () => scheduleAutoRun();
  $('#benchmark-select').onchange = () => scheduleAutoRun();

  // 모드 전환
  $$('#mode-switcher .mode-btn').forEach((btn) => {
    btn.onclick = () => {
      setMode(btn.dataset.mode);
      scheduleAutoRun();
    };
  });

  // 전략 프리셋 & VBO 슬라이더
  $('#strategy-select').onchange = () => onStrategySelectChange(true);
  $('#vbo-k').oninput = () => {
    $('#vbo-k-val').textContent = $('#vbo-k').value;
    scheduleAutoRun(200);
  };
  $('#vbo-ma').oninput = () => {
    $('#vbo-ma-val').textContent = $('#vbo-ma').value;
    scheduleAutoRun(200);
  };

  // 원클릭 타임프레임 변환
  $$('.btn-tf[data-tf]').forEach((btn) => {
    btn.onclick = async () => {
      const res = await apiJson('/api/formula/transform', {
        method: 'POST',
        body: JSON.stringify({
          action: 'timeframe',
          tf: btn.dataset.tf,
          target: 'both',
          entry_expr: $('#entry-input').value,
          exit_expr: $('#exit-input').value,
        }),
      });
      $('#strategy-select').value = 'custom';
      onStrategySelectChange(false);
      $('#entry-input').value = res.entry_expr;
      $('#exit-input').value = res.exit_expr;
      pushFormulaHistory();
      applyInspectPayload(res);
      scheduleAutoRun();
    };
  });

  $('#btn-swap-formulas').onclick = async () => {
    const res = await apiJson('/api/formula/transform', {
      method: 'POST',
      body: JSON.stringify({
        action: 'swap',
        entry_expr: $('#entry-input').value,
        exit_expr: $('#exit-input').value,
      }),
    });
    $('#strategy-select').value = 'custom';
    $('#entry-input').value = res.entry_expr;
    $('#exit-input').value = res.exit_expr;
    pushFormulaHistory();
    applyInspectPayload(res);
    scheduleAutoRun();
  };

  $('#btn-formula-undo').onclick = () => undoFormula();
  $('#btn-formula-redo').onclick = () => redoFormula();

  $('#btn-copy-formula').onclick = async () => {
    const hash = encodeShareHash();
    try {
      window.history.replaceState(null, '', '#' + hash);
    } catch (e) {}
    const shareUrl = `${window.location.origin}${window.location.pathname}#${hash}`;
    const en = $('#entry-input').value.trim();
    const ex = $('#exit-input').value.trim();
    const copyText = `[매수] ${en}\n[매도] ${ex || '(없음)'}\n🔗 ${shareUrl}`;
    let copied = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(copyText);
        copied = true;
      }
    } catch (e) {}
    if (!copied) {
      const ta = document.createElement('textarea');
      ta.value = copyText;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      try { copied = document.execCommand('copy'); } catch (e) {}
      document.body.removeChild(ta);
    }
    const btn = $('#btn-copy-formula');
    const orig = btn.textContent;
    btn.textContent = copied ? '✅ 복사됨' : '🔗 링크갱신';
    setStatus('🟢 수식과 1클릭 복원 링크가 복사되었습니다 (주소창 링크 공유 가능)', 'ok');
    setTimeout(() => { btn.textContent = orig; }, 1600);
  };

  $('#sync-linked-nums').onchange = () => renderNumberTuners();

  $('#btn-inline-live-check').onclick = () => inspectFormula(true);

  // 매수/매도 수식 입력 & 자동완성 이벤트
  ['#entry-input', '#exit-input'].forEach((sel) => {
    const ta = $(sel);
    ta.addEventListener('input', () => {
      $('#strategy-select').value = 'custom';
      showAutocomplete(ta);
      clearTimeout(state.debounceInspectTimer);
      state.debounceInspectTimer = setTimeout(() => {
        pushFormulaHistory();
        inspectFormula(false);
      }, 160);
      scheduleAutoRun(650);
    });

    ta.addEventListener('keydown', (e) => {
      const popup = $('#autocomplete-popup');
      if (popup.classList.contains('hidden') || !state.acMatches.length) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        state.acSelectedIdx = (state.acSelectedIdx + 1) % state.acMatches.length;
        renderAcPopup();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        state.acSelectedIdx = (state.acSelectedIdx - 1 + state.acMatches.length) % state.acMatches.length;
        renderAcPopup();
      } else if (e.key === 'Tab' || (e.key === 'Enter' && !e.ctrlKey)) {
        e.preventDefault();
        acceptAutocomplete(state.acSelectedIdx);
      } else if (e.key === 'Escape') {
        popup.classList.add('hidden');
      }
    });

    ta.addEventListener('blur', () => {
      setTimeout(() => $('#autocomplete-popup').classList.add('hidden'), 150);
    });
  });

  // 조건 블록 컨트롤
  $$('#snippet-target-group .seg-btn').forEach((b) => {
    b.onclick = () => {
      $$('#snippet-target-group .seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.snippetTarget = b.dataset.target;
    };
  });
  $$('#snippet-join-group .seg-btn').forEach((b) => {
    b.onclick = () => {
      $$('#snippet-join-group .seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.snippetJoin = b.dataset.join;
    };
  });
  $$('#snippet-tf-group .seg-btn').forEach((b) => {
    b.onclick = () => {
      $$('#snippet-tf-group .seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.snippetTf = b.dataset.stf;
      renderSnippets();
    };
  });

  // 초보자 노코드 문장 조립기
  $('#nc-right').onchange = () => {
    const isConst = $('#nc-right').value === 'CONST';
    $('#nc-const').classList.toggle('hidden', !isConst);
    $('#nc-mult-wrap').classList.toggle('hidden', isConst);
  };
  $('#btn-nc-insert').onclick = () => {
    const p = $('#nc-tf').value;
    const prefix = (name) => `${p}${name}`;
    const left = prefix($('#nc-left').value);
    const op = $('#nc-op').value;
    let right = $('#nc-right').value === 'CONST' ? $('#nc-const').value : prefix($('#nc-right').value);
    const mult = Number($('#nc-mult').value || 1);
    if ($('#nc-right').value !== 'CONST' && Math.abs(mult - 1.0) > 0.001) {
      right = `${right} * ${mult.toFixed(2)}`;
    }
    let expr = '';
    if (op === 'CROSSUP' || op === 'CROSSDOWN') {
      expr = `${op}(${left}, ${right})`;
    } else {
      expr = `${left} ${op} ${right}`;
    }
    insertSnippetBlock(expr);
  };

  // 레짐 모드 컨트롤
  $$('.reg-preset').forEach((b) => {
    b.onclick = () => {
      $('#reg-expr').value = b.dataset.expr;
      updateRegimeKo();
      scheduleAutoRun();
    };
  });
  $('#reg-expr').oninput = () => updateRegimeKo();
  ['#reg-signal-ticker', '#reg-t1-ticker', '#reg-t2-ticker', '#reg-freq'].forEach((s) => {
    $(s).onchange = () => scheduleAutoRun();
  });

  // 자산배분 프리셋
  $$('.alloc-preset').forEach((b) => {
    b.onclick = () => {
      const p = b.dataset.preset;
      if (p === 'allweather') state.allocWeights = { SPY: 30, TLT: 40, IEF: 15, GLD: 15 };
      else if (p === '6040') state.allocWeights = { SPY: 60, IEF: 40 };
      else if (p === 'permanent') state.allocWeights = { SPY: 25, TLT: 25, GLD: 25, SHY: 25 };
      else {
        const w = {};
        const list = state.tickers.length ? state.tickers : ['SPY'];
        const each = Math.round(100 / list.length);
        list.forEach((t) => (w[t] = each));
        state.allocWeights = w;
      }
      state.tickers = Object.keys(state.allocWeights);
      renderSelectedTickers();
      renderAllocWeights();
      scheduleAutoRun();
    };
  });
  $('#alloc-rebal').onchange = () => scheduleAutoRun();
  $('#alloc-dca').onchange = () => scheduleAutoRun();

  // 안전장치 변경 시 자동 실행
  ['#stop-loss', '#take-profit', '#trailing-stop', '#max-hold-days', '#execution-mode',
   '#max-positions', '#market-filter', '#oos-ratio', '#slippage-pct', '#atr-stop',
   '#mtf-close-only', '#tax-exempt'].forEach((s) => {
    $(s).onchange = () => scheduleAutoRun();
  });

  // 대시보드 탭 전환
  $$('#dash-tabs .dash-tab[data-tab]').forEach((btn) => {
    btn.onclick = () => switchTab(btn.dataset.tab);
  });
  if ($('#btn-expand-pro-tabs')) {
    $('#btn-expand-pro-tabs').onclick = () => setUiMode('pro');
  }

  // PC UI 간편모드 / 상세모드 전환
  $$('#ui-mode-group .seg-btn').forEach((b) => {
    b.onclick = () => setUiMode(b.dataset.uimode || 'simple');
  });

  // 전문가 매수(스마트머니) 빠른 바
  $$('#smart-money-quick-bar .sm-chip[data-sm-block]').forEach((b) => {
    b.onclick = async () => {
      if (state.mode !== 'strategy') setMode('strategy');
      await insertSnippetBlock(b.dataset.smBlock, 'entry', 'AND');
    };
  });

  // 접이식 AI 상세 진단 열릴 때 레이더 차트 리사이즈
  if ($('#summary-deep-details')) {
    $('#summary-deep-details').addEventListener('toggle', () => {
      if ($('#summary-deep-details').open && state.lastResult) {
        setTimeout(() => {
          renderRadarChart(state.lastResult);
          const el = document.getElementById('chart-radar');
          if (el) try { Plotly.Plots.resize(el); } catch (e) {}
        }, 50);
      }
    });
  }

  // 차트 단위/로그 스케일/국가지수 오버레이 전환
  if ($('#equity-index-overlay')) {
    $('#equity-index-overlay').onchange = async () => {
      state.equityOverlayIndexSymbol = $('#equity-index-overlay').value || '';
      if (!state.equityOverlayIndexSymbol) {
        state.equityOverlayIndexData = null;
        if (state.lastResult) renderEquityChart(state.lastResult);
      } else {
        await ensureEquityOverlayAndRender(state.lastResult);
      }
    };
  }
  $$('#chart-unit-group .seg-btn').forEach((b) => {
    b.onclick = () => {
      $$('#chart-unit-group .seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.chartUnit = b.dataset.unit;
      if (state.lastResult) renderEquityChart(state.lastResult);
    };
  });
  $('#chart-log-scale').onchange = () => {
    if (state.lastResult) renderEquityChart(state.lastResult);
  };
  $('#chart-show-events').onchange = () => {
    if (state.lastResult) renderEquityChart(state.lastResult);
  };
  if ($('#chart-event-filter')) {
    $('#chart-event-filter').onchange = () => {
      if (state.lastResult) renderEquityChart(state.lastResult);
    };
  }
  const syncTfButtons = (tf) => {
    $$('#candle-tf-group .seg-btn').forEach((x) => x.classList.toggle('active', x.dataset.ctf === tf));
    $$('#equity-tf-group .seg-btn').forEach((x) => x.classList.toggle('active', x.dataset.etf === tf));
  };
  $$('#candle-tf-group .seg-btn').forEach((b) => {
    b.onclick = () => {
      const tf = b.dataset.ctf || 'auto';
      syncTfButtons(tf);
      state.candleTf = tf;
      runBacktest();
    };
  });
  $$('#equity-tf-group .seg-btn').forEach((b) => {
    b.onclick = () => {
      const tf = b.dataset.etf || 'auto';
      syncTfButtons(tf);
      state.candleTf = tf;
      runBacktest();
    };
  });
  $$('#equity-zoom-group .seg-btn').forEach((b) => {
    b.onclick = () => {
      $$('#equity-zoom-group .seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.equityZoom = b.dataset.ezoom || 'ALL';
      if (state.lastResult) renderEquityChart(state.lastResult);
    };
  });
  $$('#candle-zoom-group .seg-btn').forEach((b) => {
    b.onclick = () => {
      $$('#candle-zoom-group .seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.candleZoom = b.dataset.czoom || 'AUTO';
      if (state.lastResult) renderCandleAndTrades(state.lastResult);
    };
  });
  if ($('#btn-fullscreen-equity')) {
    $('#btn-fullscreen-equity').onclick = () => toggleChartFullscreen('#equity-card', 'chart-equity', '#btn-fullscreen-equity');
  }
  if ($('#btn-fullscreen-candle')) {
    $('#btn-fullscreen-candle').onclick = () => toggleChartFullscreen('#candle-card', 'chart-candle', '#btn-fullscreen-candle');
  }
  if ($('#btn-fullscreen-ni')) {
    $('#btn-fullscreen-ni').onclick = () => toggleChartFullscreen('#national-index-card', 'chart-national-index', '#btn-fullscreen-ni');
  }

  // 국가지수 & 주요사건 타임라인 컨트롤
  $$('#ni-chip-group .seg-btn').forEach((b) => {
    b.onclick = () => loadAndRenderNationalIndex(b.dataset.ni);
  });
  $$('#ni-unit-group .seg-btn').forEach((b) => {
    b.onclick = () => {
      $$('#ni-unit-group .seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.nationalIndexUnit = b.dataset.niunit || 'price';
      renderNationalIndexSection();
    };
  });
  if ($('#ni-event-filter')) {
    $('#ni-event-filter').onchange = () => {
      state.nationalIndexFilter = $('#ni-event-filter').value || 'all';
      renderNationalIndexSection();
    };
  }

  // 미국 유명인 · 구루 매수 트래커 컨트롤
  $$('#guru-chip-group .seg-btn').forEach((b) => {
    b.onclick = () => {
      state.guruFilter = b.dataset.guru || 'all';
      renderUsGurusSection();
    };
  });
  if ($('#btn-guru-run-basket')) {
    $('#btn-guru-run-basket').onclick = () => runGuruFilteredBasket();
  }

  // 실험 보드 컨트롤
  $('#btn-set-baseline').onclick = () => {
    if (state.experiments.length) {
      state.baselineExpId = state.experiments[0].id;
      renderExperimentsBoard();
      if (state.lastResult) renderVerdictAndKpis(state.lastResult);
    }
  };
  $('#btn-clear-experiments').onclick = () => {
    state.experiments = state.experiments.filter((e) => e.pinned);
    $('#exp-count').textContent = state.experiments.length;
    renderExperimentsBoard();
  };

  // 거래 필터 & CSV 다운로드
  $$('#trade-filter-group .seg-btn').forEach((b) => {
    b.onclick = () => {
      $$('#trade-filter-group .seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.tradeFilter = b.dataset.tfilter;
      renderTradesTable();
    };
  });
  $('#btn-export-csv').onclick = () => {
    const trades = state.lastResult?.trades || [];
    if (!trades.length) return;
    const cols = Object.keys(trades[0]);
    const csv = '\uFEFF' + [cols.join(','), ...trades.map((r) => cols.map((c) => JSON.stringify(r[c] ?? '')).join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `backtest_trades_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
  };

  // 히트맵 지표 전환
  $$('#hm-metric-group .seg-btn').forEach((b) => {
    b.onclick = () => {
      $$('#hm-metric-group .seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.hmMetric = b.dataset.hm;
      renderOptimizeHeatmap();
    };
  });

  // 몬테카를로 기간 전환
  $$('#mc-horizon-group .seg-btn').forEach((b) => {
    b.onclick = () => {
      $$('#mc-horizon-group .seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.mcHorizonDays = Number(b.dataset.days);
      runBacktest();
    };
  });

  // 용어사전 검색
  $('#dict-search').oninput = () => renderDictionary($('#dict-search').value);

  // 전략 저장/불러오기/삭제
  $('#btn-save-strategy').onclick = async () => {
    const name = window.prompt('저장할 전략 이름을 입력하세요:', `${state.tickers.join('+')} 수식 전략`);
    if (!name) return;
    const res = await apiJson('/api/strategies', {
      method: 'POST',
      body: JSON.stringify(collectBacktestPayload() ? { ...collectBacktestPayload(), name } : { name }),
    });
    renderSavedStrategies(res.strategies);
    setStatus(`🟢 '${name}' 저장 완료`, 'ok');
  };
  $('#saved-strategy-select').onchange = async () => {
    const fn = $('#saved-strategy-select').value;
    if (!fn || !state.init) return;
    const res = await apiJson('/api/strategies');
    const item = (res.strategies || []).find((s) => s.filename === fn);
    if (!item) return;
    if (item.tickers && item.tickers.length) {
      state.tickers = [...item.tickers];
      renderSelectedTickers();
    }
    if (item.start_date) $('#start-date').value = item.start_date;
    setMode(item.mode || 'strategy');
    $('#strategy-select').value = item.strategy_id || 'custom';
    onStrategySelectChange(false);
    $('#entry-input').value = item.entry_expr || '';
    $('#exit-input').value = item.exit_expr || '';
    $('#stop-loss').value = item.stop_loss_pct || 0;
    $('#take-profit').value = item.take_profit_pct || 0;
    $('#trailing-stop').value = item.trailing_stop_pct || 0;
    pushFormulaHistory();
    await inspectFormula(false);
    runBacktest();
  };
  $('#btn-delete-strategy').onclick = async () => {
    const fn = $('#saved-strategy-select').value;
    if (!fn) return;
    if (!window.confirm('선택한 저장 전략을 삭제하시겠습니까?')) return;
    const res = await apiJson(`/api/strategies/${encodeURIComponent(fn)}`, { method: 'DELETE' });
    renderSavedStrategies(res.strategies);
  };
}

document.addEventListener('DOMContentLoaded', initApp);
