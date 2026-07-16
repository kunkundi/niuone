let data = {records: [], platforms: [], chats: [], categories: {}};
let indicesData = {};
let sectorData = {};
let usSectorData = {items: []};
let usQuotesData = {items: {}, symbols: []};
let usQuotesLoadingKey = '';
let usProfilesData = {items: {}, symbols: []};
let usProfilesLoadingKey = '';
let usQuotesLoadScheduled = false;
let hotStocksData = {};
let moneyFlowData = {inflow: [], outflow: []};
let marketFlowData = {total_inflow_yi: null, total_outflow_yi: null, net_flow_yi: null};
let usMarketSummaryData = {loading: true};
let practiceCandidatesData = {items: [], count: 0};
let niuniuPracticeData = {positions: [], equity_history: [], trade_log: [], decision_log: [], cash: 1000000, total_equity: 1000000};
let practiceBenchmarksData = {items: []};
let benchmarkOverlay = {sh000001: true, sh000300: true, sz399006: true, sh000688: true};
const initialParams = new URLSearchParams(location.search);
const CATEGORY_ORDER = ['practice', 'indices', 'market_monitor', 'x_monitor', 'us_ratings'];
const CATEGORY_LABELS = {all:'全部', indices:'指数行情', practice:'模拟交易', us_ratings:'美股机构买入评级', x_monitor:'推特监控', market_monitor:'盘面监控', other:'其他'};
const CATEGORY_PATHS = {
  practice: '/practice',
  indices: '/indices',
  market_monitor: '/market-monitor',
  x_monitor: '/x-monitor',
  us_ratings: '/us-ratings'
};
const PATH_CATEGORIES = Object.fromEntries(Object.entries(CATEGORY_PATHS).map(([category, path]) => [path, category]));
const LEGACY_CATEGORY_ALIASES = {b1_screen:'practice'};
const US_FEATURE_CATEGORIES = new Set(['x_monitor', 'us_ratings']);
const MESSAGE_CATEGORIES = ['x_monitor', 'market_monitor', 'us_ratings'];
function categoryFromLocation(params = new URLSearchParams(location.search)) {
  const pathCategory = PATH_CATEGORIES[location.pathname];
  const queryCategory = params.get('category') || '';
  return pathCategory || LEGACY_CATEGORY_ALIASES[queryCategory] || queryCategory || 'practice';
}
let activeCategory = categoryFromLocation(initialParams);
let US_FEATURES_ENABLED = false;
let indicesViewMode = initialParams.get('panel') === 'market' ? 'market' : 'index';
let indicesMarketRegionOverride = '';
const INDICES_INDEX_PRIORITY_STATE_KEY = 'niuniu-dashboard-index-priority-v1';
let indicesIndexPriorityOverride = '';
try {
  const savedIndexPriority = sessionStorage.getItem(INDICES_INDEX_PRIORITY_STATE_KEY);
  if (['a_share', 'us'].includes(savedIndexPriority)) indicesIndexPriorityOverride = savedIndexPriority;
} catch (e) {}
const X_MONITOR_PAGE_SIZE = 10;
const X_PAGE_CACHE_TTL_MS = 5 * 60 * 1000;
const X_PAGE_CACHE_MAX_ENTRIES = 6;
const X_PAGE_STATE_KEY = 'niuniu-dashboard-x-pages-v1';
const MARKET_PAGE_CACHE_TTL_MS = 5 * 60 * 1000;
const MARKET_PAGE_STATE_KEY = 'niuniu-dashboard-market-page-v1';
const MARKET_AUX_REFRESH_MS = 5 * 60 * 1000;
let usRatingDayIndex = 0;
let ratingExpandedRowId = '';
let xExpandedRecordKey = '';
let xImageViewer = {url: '', label: '', zoom: 1};
let marketExpandedRecordKey = '';
let usMarketSummaryExpanded = false;
let marketDayIndex = Math.max(0, Number(initialParams.get('day') || 1) - 1);
let xPageOffset = Math.max(0, (Number(initialParams.get('page') || 1) - 1) * X_MONITOR_PAGE_SIZE);
let xLoadedOffset = -1;
let xPageCache = {};
let marketPageCache = null;
let practiceCurveMode = initialParams.get('curve') === 'daily' ? 'daily' : 'intraday';
window.practiceCurveMode = practiceCurveMode;
let practicePositionMode = initialParams.get('holdings') === 'sold' ? 'sold' : 'open';
window.practicePositionMode = practicePositionMode;
let practicePositionBriefMode = initialParams.get('brief') === '1';
window.practicePositionBriefMode = practicePositionBriefMode;
let practiceLogDetailKey = '';
let practiceRuleNoteOpen = false;
let practiceCalendarOpen = false;
let practiceCalendarMonth = '';
let practiceCalendarSelectedDate = '';
let practiceLoadSeq = 0;
let practiceFullSnapshotStatus = 'idle';
let practiceFullRequest = null;
let practiceManualCycleData = {running:false, stage:'idle', stage_label:'', error:''};
let practiceManualCyclePollTimer = null;
let practiceMarketSummaryData = {loading:true, available:false, scan_count:0};
let practiceMarketSummaryGenerating = false;
let practiceMarketSummaryExpanded = false;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const actionFetch = (url, options = {}) => fetch(url, {
  ...options,
  method: options.method || 'POST',
  headers: {'X-NiuOne-Action': '1', ...(options.headers || {})}
});
const fmtNumber = (v, d=2) => {
  const n = Number(v);
  return Number.isFinite(n) ? Number(n.toFixed(d)).toLocaleString('en') : '--';
};
const fmtAmount = v => {
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  return Math.abs(n) >= 10000 ? (n/10000).toFixed(2) + '万' : n.toFixed(2);
};
const fmtSignedAmount = v => {
  const n = Number(v);
  return Number.isFinite(n) ? `${n >= 0 ? '+' : ''}${fmtAmount(n)}` : '--';
};
const fmtSignedPct = (v, d=2) => {
  const n = Number(v);
  return Number.isFinite(n) ? `${n >= 0 ? '+' : ''}${fmtNumber(n, d)}%` : '--';
};
const fmtDurationSeconds = s => {
  const n = Number(s);
  if (!Number.isFinite(n)) return '--';
  return n >= 3600 ? (n/3600).toFixed(1)+'h' : n >= 60 ? (n/60).toFixed(0)+'m' : n.toFixed(0)+'s';
};
function compactText(value, limit=120) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  return text.length > limit ? `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}…` : text;
}
function practiceOperationLogDate(payload) {
  const generatedDate = String((payload || {}).generated_at || '').slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(generatedDate) ? generatedDate : localDateKey();
}
function practiceTradeLogEntry(trade, idx) {
  const action = String(trade.action || '').toUpperCase();
  const isBuy = action === 'BUY';
  const isSell = action === 'SELL';
  const actionLabel = isBuy ? '买入' : (isSell ? '卖出' : '成交');
  const codeName = [trade.code, trade.name].map(x => String(x || '').trim()).filter(Boolean).join(' ');
  const shares = trade.shares == null ? '' : `${trade.shares}股`;
  const price = Number(trade.price);
  const amount = Number(trade.amount);
  const pnl = Number(trade.pnl);
  const details = [
    Number.isFinite(price) ? `价 ${fmtNumber(price, 3)}` : '',
    shares,
    Number.isFinite(amount) ? `额 ${fmtAmount(amount)}` : '',
    isSell && Number.isFinite(pnl) ? `盈亏 ${pnl >= 0 ? '+' : ''}${fmtAmount(pnl)}` : '',
    compactText(trade.reason || trade.trade_reason || '', 100),
  ].filter(Boolean);
  return {
    key: `trade-${idx}`,
    time: String(trade.time || ''),
    kind: 'trade',
    raw: trade,
    badgeClass: isBuy ? 'buy' : (isSell ? 'sell' : 'trade'),
    badge: actionLabel,
    summary: `${actionLabel} ${codeName || '--'}${shares ? ` · ${shares}` : ''}`,
    detail: details.join('｜'),
    order: idx,
  };
}
function practiceExecutableActionCount(actions) {
  return actions.filter(action => {
    const act = String((action || {}).action || (action || {}).type || '').toUpperCase();
    return act === 'BUY' || act === 'SELL';
  }).length;
}
function practiceDecisionActionByCode(actions) {
  const byCode = new Map();
  actions.forEach(action => {
    const code = String((action || {}).code || '').trim();
    if (code) byCode.set(code, action || {});
  });
  return byCode;
}
function practiceBlockedReasons(decision, actions) {
  const raw = Array.isArray(decision.execution_blocked_reasons)
    ? decision.execution_blocked_reasons
    : (decision.execution_blocked_reason ? [decision.execution_blocked_reason] : []);
  const byCode = practiceDecisionActionByCode(actions);
  const seen = new Set();
  return raw.map(item => {
    const text = String(item || '').trim();
    if (!text || seen.has(text)) return '';
    seen.add(text);
    const match = text.match(/^(\d{6})[:：]\s*(.*)$/);
    if (!match) return text;
    const action = byCode.get(match[1]) || {};
    const name = String(action.name || '').trim();
    const subject = [match[1], name].filter(Boolean).join(' ');
    return `${subject}：${match[2] || '执行拦截'}`;
  }).filter(Boolean);
}
function practiceDecisionExecutionNote(entry, decisionTime) {
  const executed = Array.isArray(entry.executed) ? entry.executed : [];
  const times = executed.map(item => String((item || {}).time || '').slice(11, 19)).filter(Boolean);
  const uniqueTimes = [...new Set(times)];
  if (!uniqueTimes.length) return '';
  const first = uniqueTimes[0];
  const last = uniqueTimes[uniqueTimes.length - 1];
  const range = first === last ? first : `${first}-${last}`;
  return range && range !== String(decisionTime || '').slice(11, 19) ? `成交时间${range}` : '';
}
function practiceBuyRefinementText(decision) {
  const refinement = decision.buy_refinement || {};
  const dropped = Array.isArray(refinement.dropped) ? refinement.dropped : [];
  const kept = Array.isArray(refinement.kept_codes) ? refinement.kept_codes : [];
  if (!dropped.length && !kept.length) return '';
  const keptText = kept.length ? `保留${kept.join('、')}` : '未保留新仓';
  const droppedText = dropped.length
    ? `放弃${dropped.map(item => [item.code, item.name].filter(Boolean).join(' ')).filter(Boolean).join('、')}`
    : '';
  const summary = compactText(refinement.summary || refinement.reason || '', 90);
  return ['二次取舍', keptText, droppedText, summary].filter(Boolean).join('：');
}
function practiceDecisionLogEntry(entry, idx) {
  const decision = entry.decision || {};
  const actions = Array.isArray(decision.actions) ? decision.actions : [];
  const executed = Array.isArray(entry.executed) ? entry.executed : [];
  const suggestedCount = practiceExecutableActionCount(actions);
  const blockedReasons = practiceBlockedReasons(decision, actions);
  const statusParts = [
    suggestedCount ? `建议${suggestedCount}笔` : '',
    executed.length ? `执行${executed.length}笔` : '',
    blockedReasons.length ? `拦截${blockedReasons.length}笔` : '',
  ].filter(Boolean);
  const actionText = statusParts.length ? statusParts.join(' / ') : '无成交';
  const blockedText = blockedReasons.length ? `拦截：${compactText(blockedReasons.join('；'), 140)}` : '';
  const summary = compactText(decision.summary || entry.trade_reason || '模型决策', 120);
  const executionNote = practiceDecisionExecutionNote(entry, entry.time);
  const refinementText = practiceBuyRefinementText(decision);
  const details = [
    compactText(entry.trade_reason || '', 90),
    actionText,
    refinementText,
    blockedText,
    executionNote,
    decision.error ? compactText(decision.error, 90) : '',
  ].filter(Boolean);
  return {
    key: `decision-${idx}`,
    time: String(entry.time || ''),
    kind: 'decision',
    raw: entry,
    badgeClass: 'decision',
    badge: '决策',
    summary,
    detail: details.join('｜'),
    order: idx,
  };
}
function normalizePracticeOperationLogs(payload) {
  const p = payload || {};
  const date = practiceOperationLogDate(p);
  const entries = [];
  (p.trade_log || []).forEach((trade, idx) => {
    if (trade && String(trade.time || '').slice(0, 10) === date) {
      entries.push(practiceTradeLogEntry(trade, idx));
    }
  });
  (p.decision_log || []).forEach((entry, idx) => {
    if (entry && String(entry.time || '').slice(0, 10) === date) {
      entries.push(practiceDecisionLogEntry(entry, idx + 10000));
    }
  });
  return entries.sort((a, b) => String(b.time || '').localeCompare(String(a.time || '')) || a.order - b.order);
}
function renderPracticeOperationLog(payload) {
  const date = practiceOperationLogDate(payload);
  const entries = normalizePracticeOperationLogs(payload);
  const rows = entries.length ? entries.map(item => `
    <button type="button" class="practice-log-row" data-practice-log-key="${esc(item.key)}" title="查看完整日志" aria-label="查看完整日志：${esc(item.summary)}">
      <div class="practice-log-time">${esc(String(item.time || '').slice(11, 19) || '--')}</div>
      <div class="practice-log-badge ${esc(item.badgeClass)}">${esc(item.badge)}</div>
      <div class="practice-log-main">
        <div class="practice-log-summary">${esc(item.summary)}</div>
        ${item.detail ? `<div class="practice-log-detail">${esc(item.detail)}</div>` : ''}
      </div>
    </button>`).join('') : '<div class="empty" style="padding:18px;font-size:13px">当日暂无操作日志</div>';
  return `<div class="practice-log-panel">
    <div class="practice-log-head">
      <div class="practice-log-title">操作日志</div>
      <div class="practice-log-count">${esc(date)} · ${entries.length}条</div>
    </div>
    <div class="practice-log-scroll" tabindex="0" role="region" aria-label="当日所有操作日志">${rows}</div>
  </div>`;
}
function practiceLogTextValue(value) {
  if (value === null || value === undefined) return '';
  if (Array.isArray(value)) return value.map(practiceLogTextValue).filter(Boolean).join('；');
  if (typeof value === 'object') return practiceLogTextValue(value.summary || value.reason || value.detail || '');
  return String(value || '').trim();
}
function practiceLogRawText(item) {
  const raw = item && item.raw && typeof item.raw === 'object' ? item.raw : {};
  if (item.kind === 'trade') {
    return practiceLogTextValue(raw.reason || raw.trade_reason || item.detail || item.summary);
  }
  const decision = raw.decision && typeof raw.decision === 'object' ? raw.decision : {};
  const textParts = [
    practiceLogTextValue(decision.summary),
    practiceLogTextValue(raw.trade_reason),
    practiceLogTextValue(decision.execution_blocked_reasons || decision.execution_blocked_reason),
    practiceLogTextValue(decision.buy_refinement),
    practiceLogTextValue(decision.error),
  ].filter(Boolean);
  return [...new Set(textParts)].join('\n\n') || item.detail || item.summary || '无原文';
}
function renderPracticeLogDetailModal(payload) {
  if (!practiceLogDetailKey) return '';
  const item = normalizePracticeOperationLogs(payload).find(entry => entry.key === practiceLogDetailKey);
  if (!item) return '';
  const text = practiceLogRawText(item);
  return `<div class="practice-log-detail-backdrop" role="presentation">
    <div class="practice-log-detail-card" role="dialog" aria-modal="true" aria-label="完整操作日志">
      <div class="practice-log-detail-head">
        <div class="practice-log-detail-title">${esc(item.summary || '完整操作日志')}</div>
        <button type="button" class="practice-log-detail-close" data-practice-log-action="close" title="关闭" aria-label="关闭">x</button>
      </div>
      <div class="practice-log-detail-body">
        <div class="practice-log-detail-text">${esc(text)}</div>
      </div>
    </div>
  </div>`;
}
function practiceRuleFallbackNote() {
  return '100股整数倍、T+1；09:15-09:25只作开盘集合竞价观察，09:25-09:30不模拟成交。';
}
function renderPracticeRuleNoteModal(note) {
  if (!practiceRuleNoteOpen) return '';
  const text = String(note || practiceRuleFallbackNote()).trim();
  return `<div class="practice-rule-backdrop" role="presentation">
    <div class="practice-rule-card" role="dialog" aria-modal="true" aria-label="交易规则">
      <div class="practice-rule-head">
        <div class="practice-rule-title">交易规则</div>
        <button type="button" class="practice-rule-close" data-practice-rule-action="close" title="关闭" aria-label="关闭">x</button>
      </div>
      <div class="practice-rule-body">${esc(text)}</div>
    </div>
  </div>`;
}
const upCls = v => v > 0 ? 'up' : v < 0 ? 'down' : 'flat';
function categoryAvailable(category) {
  return !US_FEATURE_CATEGORIES.has(category) || US_FEATURES_ENABLED;
}
function visibleCategoryOrder() {
  return CATEGORY_ORDER.filter(categoryAvailable);
}
function normalizeActiveCategory(category) {
  const normalized = LEGACY_CATEGORY_ALIASES[category] || category;
  return visibleCategoryOrder().includes(normalized) ? normalized : 'practice';
}
const VIEW_STATE_KEY = 'niuniu-dashboard-view-state-v5';
const DATA_CACHE_TTL_MS = 30000;
const AUTO_REFRESH_TICK_MS = 15000;
const US_RATINGS_AUTO_REFRESH_MS = 10 * 60 * 1000;
let loadSeq = 0;
let pendingLoadController = null;
let loadingMoreHistory = false;
let xPageLoadInFlight = false;
let xPageNavigationSeq = 0;
const xPagePrefetches = new Map();
let marketAuxLoadPromise = null;
let lastMarketAuxRefreshAt = 0;
let lastAutoRefreshAt = 0;
function rememberXPage(offset, payload, savedAt = Date.now()) {
  const key = String(Math.max(0, Number(offset || 0)));
  xPageCache[key] = {data: payload, savedAt: Number(savedAt || Date.now())};
  const entries = Object.entries(xPageCache).sort((a, b) => Number(b[1]?.savedAt || 0) - Number(a[1]?.savedAt || 0));
  xPageCache = Object.fromEntries(entries.slice(0, X_PAGE_CACHE_MAX_ENTRIES));
}
function restoreXPageCache(cachedPages) {
  xPageCache = {};
  for (const [offset, entry] of Object.entries(cachedPages || {})) {
    if (!entry?.data || Date.now() - Number(entry.savedAt || 0) > X_PAGE_CACHE_TTL_MS) continue;
    rememberXPage(offset, entry.data, entry.savedAt);
  }
}
function cachedXPage(offset = xPageOffset) {
  const key = String(Math.max(0, Number(offset || 0)));
  const entry = xPageCache[key];
  if (!entry) return null;
  if (Date.now() - Number(entry.savedAt || 0) > X_PAGE_CACHE_TTL_MS) {
    delete xPageCache[key];
    return null;
  }
  return entry.data || null;
}
function applyCachedXPage(offset = xPageOffset) {
  const cachedPage = cachedXPage(offset);
  if (!cachedPage) return false;
  data = cachedPage;
  xLoadedOffset = Math.max(0, Number(offset || 0));
  return true;
}
function saveXPageState() {
  try {
    sessionStorage.setItem(X_PAGE_STATE_KEY, JSON.stringify({
      xPageCache,
      xPageOffset,
      savedAt: Date.now(),
    }));
  } catch (e) {}
}
function rememberMarketPage(payload, savedAt = Date.now()) {
  marketPageCache = {data: payload, savedAt: Number(savedAt || Date.now())};
}
function cachedMarketPage() {
  if (!marketPageCache) return null;
  if (Date.now() - Number(marketPageCache.savedAt || 0) > MARKET_PAGE_CACHE_TTL_MS) {
    marketPageCache = null;
    return null;
  }
  return marketPageCache.data || null;
}
function applyCachedMarketPage() {
  const cachedPage = cachedMarketPage();
  if (!cachedPage) return false;
  data = cachedPage;
  return true;
}
function saveMarketPageState() {
  try {
    sessionStorage.setItem(MARKET_PAGE_STATE_KEY, JSON.stringify({
      marketPageCache,
      usMarketSummaryData,
      lastMarketAuxRefreshAt,
      savedAt: Date.now(),
    }));
  } catch (e) {}
}
function saveViewState() {
  try {
    sessionStorage.setItem(VIEW_STATE_KEY, JSON.stringify({
      data, indicesData, sectorData, usSectorData, hotStocksData, moneyFlowData, marketFlowData,
      usMarketSummaryData, practiceCandidatesData, niuniuPracticeData, practiceBenchmarksData, usQuotesData, usProfilesData,
      xPageOffset, xLoadedOffset, practiceCurveMode, practicePositionMode, practicePositionBriefMode, indicesViewMode,
      savedAt: Date.now()
    }));
  } catch (e) {}
}
function restoreViewState() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(VIEW_STATE_KEY) || '{}');
    const cachedXPages = JSON.parse(sessionStorage.getItem(X_PAGE_STATE_KEY) || '{}');
    const cachedMarketPageState = JSON.parse(sessionStorage.getItem(MARKET_PAGE_STATE_KEY) || '{}');
    const cachedXPagesFresh = cachedXPages.savedAt && Date.now() - cachedXPages.savedAt <= X_PAGE_CACHE_TTL_MS;
    const cachedMarketPageFresh = cachedMarketPageState.savedAt
      && Date.now() - cachedMarketPageState.savedAt <= MARKET_PAGE_CACHE_TTL_MS;
    restoreXPageCache(cachedXPagesFresh ? cachedXPages.xPageCache : cached.xPageCache);
    if (cachedMarketPageFresh && cachedMarketPageState.marketPageCache?.data) {
      rememberMarketPage(
        cachedMarketPageState.marketPageCache.data,
        cachedMarketPageState.marketPageCache.savedAt,
      );
      usMarketSummaryData = cachedMarketPageState.usMarketSummaryData || usMarketSummaryData;
      lastMarketAuxRefreshAt = Number(cachedMarketPageState.lastMarketAuxRefreshAt || 0);
    }
    if (activeCategory === 'x_monitor' && !initialParams.has('page') && cachedXPagesFresh) {
      xPageOffset = Math.max(0, Number(cachedXPages.xPageOffset || 0));
    }
    if (!cached.savedAt || Date.now() - cached.savedAt > DATA_CACHE_TTL_MS) {
      if (activeCategory === 'x_monitor') applyCachedXPage(xPageOffset);
      if (activeCategory === 'market_monitor') applyCachedMarketPage();
      return;
    }
    data = cached.data || data;
    indicesData = cached.indicesData || indicesData;
    sectorData = cached.sectorData || sectorData;
    usSectorData = cached.usSectorData || usSectorData;
    hotStocksData = cached.hotStocksData || hotStocksData;
    moneyFlowData = cached.moneyFlowData || moneyFlowData;
    marketFlowData = cached.marketFlowData || marketFlowData;
    usMarketSummaryData = (cachedMarketPageFresh && cachedMarketPageState.usMarketSummaryData)
      || cached.usMarketSummaryData
      || usMarketSummaryData;
    practiceCandidatesData = cached.practiceCandidatesData || practiceCandidatesData;
    if (cached.niuniuPracticeData) {
      niuniuPracticeData = {...cached.niuniuPracticeData};
      // The portfolio can be warmed from sessionStorage, but model identity is
      // runtime configuration and must be confirmed by a fresh API response.
      delete niuniuPracticeData.decision_model;
      delete niuniuPracticeData.decision_provider;
    }
    practiceBenchmarksData = cached.practiceBenchmarksData || practiceBenchmarksData;
    usQuotesData = cached.usQuotesData || usQuotesData;
    usProfilesData = cached.usProfilesData || usProfilesData;
    if (!initialParams.has('curve') && ['intraday', 'daily'].includes(cached.practiceCurveMode)) {
      practiceCurveMode = cached.practiceCurveMode;
      window.practiceCurveMode = practiceCurveMode;
    }
    if (!initialParams.has('holdings') && ['open', 'sold'].includes(cached.practicePositionMode)) {
      practicePositionMode = cached.practicePositionMode;
      window.practicePositionMode = practicePositionMode;
    }
    if (!initialParams.has('brief') && typeof cached.practicePositionBriefMode === 'boolean') {
      practicePositionBriefMode = cached.practicePositionBriefMode;
      window.practicePositionBriefMode = practicePositionBriefMode;
    }
    if (!initialParams.has('panel') && ['index', 'market'].includes(cached.indicesViewMode)) {
      indicesViewMode = cached.indicesViewMode;
    }
    if (!initialParams.has('page')) xPageOffset = Math.max(0, Number(cached.xPageOffset || 0));
    xLoadedOffset = Number.isFinite(Number(cached.xLoadedOffset)) ? Number(cached.xLoadedOffset) : -1;
    if (activeCategory === 'x_monitor' && !applyCachedXPage(xPageOffset)) xLoadedOffset = -1;
    if (activeCategory === 'market_monitor' && !applyCachedMarketPage()
        && (data.records || []).some(record => record.category === 'market_monitor')) {
      rememberMarketPage(data, cached.savedAt);
    }
  } catch (e) {}
}
function hasWarmData(category) {
  if (category === 'indices') return Array.isArray(indicesData.items) && indicesData.items.length;
  if (category === 'practice') return (Array.isArray(practiceCandidatesData.items) && practiceCandidatesData.items.length) || Array.isArray(niuniuPracticeData.equity_history);
  if (category === 'x_monitor') return xLoadedOffset === xPageOffset && !!cachedXPage(xPageOffset) && Array.isArray(data.records);
  if (category === 'market_monitor') return !!cachedMarketPage() && (data.records || []).some(r => r.category === category);
  if (isMessageCategory(category)) return (data.records || []).some(r => r.category === category);
  return false;
}
function optionize(select, values, label) {
  const current = select.value;
  select.innerHTML = `<option value="">${label}</option>` + values.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
  select.value = values.includes(current) ? current : '';
}
function isMessageCategory(category = activeCategory) {
  return MESSAGE_CATEGORIES.includes(category);
}
function messagePageLimit(category = activeCategory) {
  if (category === 'us_ratings') return 120;
  if (category === 'x_monitor') return X_MONITOR_PAGE_SIZE;
  if (category === 'market_monitor') return 200;
  return 80;
}
function currentViewUrl() {
  const params = new URLSearchParams();
  if (activeCategory === 'x_monitor' && xPageOffset > 0) {
    params.set('page', String(Math.floor(xPageOffset / messagePageLimit('x_monitor')) + 1));
  }
  if (activeCategory === 'market_monitor' && marketDayIndex > 0) {
    params.set('day', String(marketDayIndex + 1));
  }
  if (activeCategory === 'indices' && indicesViewMode === 'market') {
    params.set('panel', 'market');
  }
  if (activeCategory === 'practice' && practicePositionMode === 'sold') {
    params.set('holdings', 'sold');
  }
  if (activeCategory === 'practice' && practiceCurveMode === 'daily') {
    params.set('curve', 'daily');
  }
  if (activeCategory === 'practice' && practicePositionBriefMode) {
    params.set('brief', '1');
  }
  const query = params.toString();
  return (CATEGORY_PATHS[activeCategory] || CATEGORY_PATHS.practice) + (query ? '?' + query : '');
}
function syncViewUrl({push = false} = {}) {
  history[push ? 'pushState' : 'replaceState'](null, '', currentViewUrl());
}
function messageOffset(category = activeCategory) {
  return category === 'x_monitor' ? xPageOffset : 0;
}
function recordKey(r) {
  return String(r.id || r.raw_path || r.external_id || `${r.category || ''}:${r.session_id || ''}:${r.timestamp || ''}:${(r.content || '').slice(0, 80)}`);
}
function mergeRecordLists(primary, secondary) {
  const seen = new Set();
  const merged = [];
  for (const r of [...(primary || []), ...(secondary || [])]) {
    const key = recordKey(r);
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(r);
  }
  return merged.sort((a, b) => {
    const at = Number(a.timestamp || Date.parse(a.time || 0) / 1000 || 0);
    const bt = Number(b.timestamp || Date.parse(b.time || 0) / 1000 || 0);
    return bt - at;
  });
}
function xPageRevision(payload) {
  const records = Array.isArray(payload?.records) ? payload.records : [];
  const total = Number(payload?.categories?.x_monitor?.count || 0);
  return JSON.stringify([
    total,
    records.map(record => [recordKey(record), record.timestamp, record.content_hash, record.metadata]),
  ]);
}
function marketPageRevision(payload) {
  const records = Array.isArray(payload?.records) ? payload.records : [];
  const total = Number(payload?.categories?.market_monitor?.count || 0);
  return JSON.stringify([
    total,
    records.map(record => [recordKey(record), record.timestamp, record.content_hash]),
  ]);
}
function activeCategoryTotal() {
  if (isMessageCategory()) return Number(data.categories?.[activeCategory]?.count || 0);
  return Number(data.total || 0);
}
function messagesUrl(offset = messageOffset(), limit = messagePageLimit(), category = activeCategory) {
  const msgCategory = isMessageCategory(category) ? category : '';
  return `/api/messages?limit=${limit}&offset=${offset}${msgCategory ? '&category=' + encodeURIComponent(msgCategory) : ''}`;
}
function prefetchXPage(offset, total) {
  const targetOffset = Math.max(0, Number(offset || 0));
  if (targetOffset >= Number(total || 0) || cachedXPage(targetOffset)) return null;
  const key = String(targetOffset);
  if (xPagePrefetches.has(key)) return xPagePrefetches.get(key);
  const request = fetch(messagesUrl(targetOffset, messagePageLimit('x_monitor'), 'x_monitor'))
    .then(response => {
      if (!response.ok) throw new Error(`x page prefetch failed: ${response.status}`);
      return response.json();
    })
    .then(payload => {
      rememberXPage(targetOffset, payload);
      return payload;
    })
    .catch(() => null)
    .finally(() => {
      if (xPagePrefetches.get(key) === request) xPagePrefetches.delete(key);
    });
  xPagePrefetches.set(key, request);
  return request;
}
function prefetchAdjacentXPages(offset, payload) {
  const total = Number(payload?.categories?.x_monitor?.count || payload?.matched_total || 0);
  if (!total) return;
  prefetchXPage(offset - X_MONITOR_PAGE_SIZE, total);
  prefetchXPage(offset + X_MONITOR_PAGE_SIZE, total);
}
function cancelXMediaRequests() {
  document.querySelectorAll('img[data-x-media-request]').forEach(img => {
    if (!img.complete) img.removeAttribute('src');
  });
}
function autoRefreshIntervalMs(category = activeCategory) {
  return category === 'us_ratings' ? US_RATINGS_AUTO_REFRESH_MS : AUTO_REFRESH_TICK_MS;
}
async function autoRefresh() {
  if (xPageLoadInFlight) return;
  if (Date.now() - lastAutoRefreshAt < autoRefreshIntervalMs()) return;
  await load({background:true});
}
function loadActiveCategoryData(category = activeCategory) {
  if (category === 'indices') return loadIndices();
  if (category === 'practice') return loadPracticePage();
  if (category === 'market_monitor') return loadMarketMonitorAuxData();
  return null;
}
async function load({background=false, updateTabs=true, waitFor=null} = {}) {
  const seq = ++loadSeq;
  const categoryAtStart = activeCategory;
  const offsetAtStart = messageOffset(categoryAtStart);
  const limitAtStart = messagePageLimit(categoryAtStart);
  if (pendingLoadController) pendingLoadController.abort();
  const controller = new AbortController();
  pendingLoadController = controller;
  const msgUrl = messagesUrl(isMessageCategory(categoryAtStart) ? offsetAtStart : 0, isMessageCategory(categoryAtStart) ? limitAtStart : 0, categoryAtStart);
  if (!background && !hasWarmData(categoryAtStart)) {
    $('feed').innerHTML = '<div class="loading">加载中…</div>';
  }
  // Put the message request into the browser's connection queue first. Market
  // auxiliary data is intentionally started only after the monitor list is
  // visible so a cold quote provider can never delay the primary content.
  const messageRequest = fetch(msgUrl, {signal: controller.signal});
  if (categoryAtStart !== 'market_monitor') loadActiveCategoryData(categoryAtStart);
  const res = await messageRequest;
  const nextData = await res.json();
  if (waitFor) await waitFor;
  if (seq !== loadSeq || activeCategory !== categoryAtStart) return;
  if (categoryAtStart === 'x_monitor' && xPageOffset !== offsetAtStart) return;
  const unchangedXPage = background
    && categoryAtStart === 'x_monitor'
    && xLoadedOffset === offsetAtStart
    && xPageRevision(data) === xPageRevision(nextData);
  const unchangedMarketPage = background
    && categoryAtStart === 'market_monitor'
    && marketPageRevision(data) === marketPageRevision(nextData);
  data = categoryAtStart === 'market_monitor'
    ? nextData
    : background && isMessageCategory(categoryAtStart) && categoryAtStart !== 'x_monitor'
    ? {...nextData, records: mergeRecordLists(nextData.records || [], data.records || [])}
    : nextData;
  if (categoryAtStart === 'x_monitor') {
    xLoadedOffset = offsetAtStart;
    rememberXPage(offsetAtStart, nextData);
    prefetchAdjacentXPages(offsetAtStart, nextData);
  }
  if (categoryAtStart === 'market_monitor') rememberMarketPage(nextData);
  $('updated').textContent = data.generated_at?.slice(11) || '--';
  if (updateTabs) renderTabs();
  if (seq !== loadSeq) return;
  if (!unchangedXPage && !unchangedMarketPage) render();
  if (categoryAtStart === 'market_monitor') loadActiveCategoryData(categoryAtStart);
  if (activeCategory === 'us_ratings') refreshVisibleUsQuotes();
  lastAutoRefreshAt = Date.now();
  if (categoryAtStart === 'x_monitor') saveXPageState();
  else if (categoryAtStart === 'market_monitor') saveMarketPageState();
  else saveViewState();
}
async function loadMoreMessages() {
  if (!isMessageCategory() || activeCategory === 'us_ratings' || activeCategory === 'x_monitor' || loadingMoreHistory) return;
  loadingMoreHistory = true;
  render();
  try {
    const offset = (data.records || []).length;
    const res = await fetch(messagesUrl(offset, messagePageLimit()));
    const nextData = await res.json();
    data = {
      ...nextData,
      records: mergeRecordLists(data.records || [], nextData.records || []),
      categories: nextData.categories || data.categories || {},
      platforms: nextData.platforms || data.platforms || [],
      chats: nextData.chats || data.chats || [],
      total: nextData.total || data.total,
      generated_at: nextData.generated_at || data.generated_at,
    };
    if (activeCategory === 'market_monitor') {
      rememberMarketPage(data);
      saveMarketPageState();
    } else {
      saveViewState();
    }
  } finally {
    loadingMoreHistory = false;
    render();
  }
}
async function loadXPage(nextOffset) {
  if (activeCategory !== 'x_monitor' || loadingMoreHistory) return;
  const limit = messagePageLimit('x_monitor');
  const total = activeCategoryTotal();
  const maxOffset = total ? Math.max(0, (Math.ceil(total / limit) - 1) * limit) : Math.max(0, Number(nextOffset || 0));
  const targetOffset = Math.max(0, Math.min(Number(nextOffset || 0), maxOffset));
  if (targetOffset === xPageOffset && xLoadedOffset === xPageOffset) return;
  const navigationSeq = ++xPageNavigationSeq;
  const previousOffset = xPageOffset;
  const previousLoadedOffset = xLoadedOffset;
  const previousData = data;
  cancelXMediaRequests();
  xPageOffset = targetOffset;
  xExpandedRecordKey = '';
  const hasCachedPage = applyCachedXPage(targetOffset);
  if (!hasCachedPage) xLoadedOffset = -1;
  syncViewUrl();
  xPageLoadInFlight = true;
  loadingMoreHistory = !hasCachedPage;
  if (hasCachedPage) render();
  else $('feed').innerHTML = '<div class="loading">加载中…</div>';
  try {
    await load({background:true});
  } catch (err) {
    if (navigationSeq !== xPageNavigationSeq || (err && err.name === 'AbortError')) return;
    if (!hasCachedPage) {
      xPageOffset = previousOffset;
      xLoadedOffset = previousLoadedOffset;
      data = previousData;
      syncViewUrl();
    }
    throw err;
  } finally {
    if (navigationSeq === xPageNavigationSeq) {
      xPageLoadInFlight = false;
      loadingMoreHistory = false;
      if (activeCategory === 'x_monitor' && !hasCachedPage) render();
    }
  }
}
function ratingSymbolsFromRecords(records) {
  const symbols = new Set();
  for (const r of records || []) {
    const parsed = parseRatingReport(r.content || '');
    if (!parsed) continue;
    for (const item of parsed.items) {
      const ticker = String((item.name || '').split('/')[0] || '').trim().toUpperCase();
      if (/^[A-Z][A-Z0-9.]{1,8}$/.test(ticker)) symbols.add(ticker);
    }
  }
  return [...symbols];
}
async function loadUsQuotes(records = currentUsRatingRecords()) {
  const symbols = ratingSymbolsFromRecords(records);
  const cachedItems = usQuotesData.items || {};
  const missing = symbols.filter(symbol => !cachedItems[symbol]);
  if (!missing.length) return false;
  const symList = missing.join(',');
  if (usQuotesLoadingKey === symList) return false;
  usQuotesLoadingKey = symList;
  try {
    const res = await fetch(`/api/us_quotes?symbols=${encodeURIComponent(symList)}`);
    const nextQuotes = await res.json();
    usQuotesData = {
      ...nextQuotes,
      items: {...(usQuotesData.items || {}), ...((nextQuotes && nextQuotes.items) || {})},
      symbols: [...new Set([...(usQuotesData.symbols || []), ...((nextQuotes && nextQuotes.symbols) || missing)])],
    };
    saveViewState();
    return true;
  } catch (e) {
    console.error('us quotes load error', e);
    return false;
  } finally {
    if (usQuotesLoadingKey === symList) usQuotesLoadingKey = '';
  }
}
async function loadUsProfiles(symbols) {
  const loadedSymbols = new Set(usProfilesData.symbols || []);
  const missing = [...new Set(symbols || [])].filter(symbol => symbol && !loadedSymbols.has(symbol));
  if (!missing.length) return false;
  const symList = missing.join(',');
  if (usProfilesLoadingKey === symList) return false;
  usProfilesLoadingKey = symList;
  try {
    const res = await fetch(`/api/us_profiles?symbols=${encodeURIComponent(symList)}`);
    const nextProfiles = await res.json();
    usProfilesData = {
      ...nextProfiles,
      items: {...(usProfilesData.items || {}), ...((nextProfiles && nextProfiles.items) || {})},
      symbols: [...new Set([...(usProfilesData.symbols || []), ...((nextProfiles && nextProfiles.symbols) || missing)])],
    };
    saveViewState();
    return true;
  } catch (e) {
    console.error('us profiles load error', e);
    return false;
  } finally {
    if (usProfilesLoadingKey === symList) usProfilesLoadingKey = '';
  }
}
function refreshVisibleUsQuotes() {
  if (activeCategory !== 'us_ratings' || usQuotesLoadScheduled) return;
  usQuotesLoadScheduled = true;
  const run = () => {
    usQuotesLoadScheduled = false;
    if (activeCategory !== 'us_ratings') return;
    const records = currentUsRatingRecords();
    loadUsQuotes(records).then(changed => {
      if (!changed || activeCategory !== 'us_ratings') return;
      render();
      restoreRatingDetail();
      saveViewState();
    }).catch(e => console.error('us quotes load error', e));
  };
  if (typeof window.requestIdleCallback === 'function') {
    window.requestIdleCallback(run, {timeout: 600});
  } else {
    window.setTimeout(run, 0);
  }
}
async function loadMarketMonitorAuxData() {
  if (marketAuxLoadPromise) return marketAuxLoadPromise;
  if (lastMarketAuxRefreshAt && Date.now() - lastMarketAuxRefreshAt < MARKET_AUX_REFRESH_MS) return null;
  if (!usMarketSummaryData.generated_at && !usMarketSummaryData.summary) {
    usMarketSummaryData = {...usMarketSummaryData, loading: true};
  }
  const request = fetch('/api/us_market_summary')
    .then(response => {
      if (!response.ok) throw new Error(`us market summary failed: ${response.status}`);
      return response.json();
    })
    .then(payload => {
      usMarketSummaryData = payload || {available:false};
      lastMarketAuxRefreshAt = Date.now();
      if (activeCategory === 'market_monitor') render();
      saveMarketPageState();
      return payload;
    })
    .catch(error => {
      console.error('us market summary load error', error);
      if (!usMarketSummaryData.generated_at && !usMarketSummaryData.summary) {
        usMarketSummaryData = {available:false, error:String(error), loading:false};
        if (activeCategory === 'market_monitor') render();
      }
      return null;
    })
    .finally(() => {
      if (marketAuxLoadPromise === request) marketAuxLoadPromise = null;
    });
  marketAuxLoadPromise = request;
  return request;
}
async function loadIndices() {
  try {
    const idxPromise = fetch('/api/indices').then(r => r.ok ? r.json() : {items: []});
    const secPromise = fetch('/api/sectors').then(r => r.ok ? r.json() : {sectors: []});
    const usSecPromise = fetch('/api/us_sectors').then(r => r.ok ? r.json() : {items: []});
    const hotPromise = fetch('/api/hot_stocks').then(r => r.ok ? r.json() : {items: []});
    const mfPromise = fetch('/api/money_flow').then(r => r.ok ? r.json() : {inflow: [], outflow: []});
    const mkfPromise = fetch('/api/market_flow').then(r => r.ok ? r.json() : {total_inflow_yi: null});
    const idx = await idxPromise;
    indicesData = idx || {items: []};
    if (activeCategory === 'indices') render();
    saveViewState();
    const [sec, usSec, hot, mf, mkf] = await Promise.all([secPromise, usSecPromise, hotPromise, mfPromise, mkfPromise]);
    sectorData = sec || sectorData || {sectors: []};
    usSectorData = usSec || usSectorData || {items: []};
    hotStocksData = hot || hotStocksData || {items: []};
    moneyFlowData = mf || moneyFlowData || {inflow: [], outflow: []};
    marketFlowData = mkf || marketFlowData || {total_inflow_yi: null};
    if (activeCategory === 'indices') render();
    saveViewState();
  } catch(e) {
    console.error('indices load error', e);
    indicesData = {items: [], error: String(e)};
    if (activeCategory === 'indices') render();
  }
}
function mergePracticeTimedRows(...sources) {
  const byTime = new Map();
  for (const source of sources) {
    for (const row of (Array.isArray(source) ? source : [])) {
      const time = String(row?.time || '');
      if (time) byTime.set(time, row);
    }
  }
  return [...byTime.values()].sort((a, b) => String(a?.time || '').localeCompare(String(b?.time || '')));
}
function practicePayloadModeRank(payload) {
  const mode = String(payload?.snapshot_mode || '');
  if (mode === 'full') return 2;
  if (mode === 'merged') return String(payload?.equity_history_scope || '') === 'retained_history' ? 2 : 1;
  return mode === 'fast' ? 1 : 0;
}
function practicePayloadFreshnessTuple(payload) {
  const meta = payload?.snapshot_meta || {};
  const latestEquityTime = mergePracticeTimedRows(payload?.equity_history || []).at(-1)?.time || '';
  const newest = values => values.map(value => String(value || '')).filter(Boolean).sort().at(-1) || '';
  const sourceUpdatedAt = newest([meta.source_updated_at, payload?.source_updated_at]);
  const sourceLastEquity = newest([meta.source_last_equity_time, payload?.source_last_equity_time, latestEquityTime]);
  const responseTime = newest([payload?.current_time, payload?.generated_at]);
  return [sourceUpdatedAt || sourceLastEquity || responseTime, sourceLastEquity, practicePayloadModeRank(payload), responseTime];
}
function comparePracticePayloadFreshness(left, right) {
  const a = practicePayloadFreshnessTuple(left);
  const b = practicePayloadFreshnessTuple(right);
  for (let idx = 0; idx < a.length; idx += 1) {
    if (a[idx] === b[idx]) continue;
    return a[idx] > b[idx] ? 1 : -1;
  }
  return 0;
}
function isUsablePracticePayload(payload) {
  if (!payload || typeof payload !== 'object' || String(payload.equity_history_scope || '') === 'unavailable') return false;
  const isLegacyErrorShell = !('equity_history_scope' in payload)
    && Boolean(payload.last_error)
    && (!Array.isArray(payload.positions) || payload.positions.length === 0)
    && (!Array.isArray(payload.equity_history) || payload.equity_history.length === 0)
    && ['cash', 'total_equity', 'initial_cash'].every(key => Number(payload[key] || 0) === 0);
  if (isLegacyErrorShell) return false;
  const hasFiniteField = key => payload[key] !== null && payload[key] !== '' && Number.isFinite(Number(payload[key]));
  return Boolean(
    hasFiniteField('total_equity')
    || hasFiniteField('cash')
    || (Array.isArray(payload.positions) && ('initial_cash' in payload || 'cash' in payload))
    || (Array.isArray(payload.equity_history) && payload.equity_history.length)
  );
}
function mergePracticeDailyRows(staleRows, liveRows) {
  const byDate = new Map();
  for (const source of [staleRows, liveRows]) {
    for (const row of (Array.isArray(source) ? source : [])) {
      const date = String(row?.time || '').slice(0, 10);
      if (date) byDate.set(date, row);
    }
  }
  return [...byDate.values()].sort((a, b) => String(a?.time || '').localeCompare(String(b?.time || '')));
}
function mergePracticeEquityRows(live, stale) {
  const liveRows = mergePracticeTimedRows(live?.equity_history || []);
  if (String(live?.equity_history_scope || '') === 'retained_history') return liveRows.slice(-2000);
  if (!liveRows.length) return [];
  const liveDate = String(liveRows.at(-1)?.time || '').slice(0, 10);
  const compactDates = new Set(Object.keys(live?.calendar_history?.days || {}));
  const olderRows = mergePracticeTimedRows(stale?.equity_history || []).filter(row => {
    const date = String(row?.time || '').slice(0, 10);
    return date && liveDate && date < liveDate && !compactDates.has(date);
  });
  return mergePracticeTimedRows(olderRows, liveRows).slice(-2000);
}
function mergePracticePayloadSnapshots(current, incoming) {
  if (!isUsablePracticePayload(current)) return {...(incoming || {})};
  if (!isUsablePracticePayload(incoming)) return {...(current || {})};
  const incomingIsFresher = comparePracticePayloadFreshness(incoming, current) >= 0;
  const live = incomingIsFresher ? incoming : current;
  const other = incomingIsFresher ? current : incoming;
  const merged = {...other, ...live};
  // Model identity describes the current server configuration, not the age of
  // the portfolio snapshot. Prefer freshly fetched metadata over a warmer but
  // stale sessionStorage snapshot even when the latter has fuller history.
  for (const key of ['decision_model', 'decision_provider']) {
    const incomingValue = String(incoming?.[key] || '').trim();
    if (incomingValue) merged[key] = incomingValue;
  }
  merged.equity_history = mergePracticeEquityRows(live, other);
  const liveDailyRows = Array.isArray(live.daily_equity_history) ? live.daily_equity_history : other.daily_equity_history;
  merged.daily_equity_history = mergePracticeDailyRows([], liveDailyRows).slice(-500);
  const modes = new Set([current.snapshot_mode, incoming.snapshot_mode].filter(Boolean));
  merged.snapshot_mode = modes.size > 1 || modes.has('merged') ? 'merged' : (live.snapshot_mode || '');
  merged.equity_history_scope = live.equity_history_scope || 'latest_day';
  merged.last_error = live.last_error || '';
  return merged;
}
async function loadPracticePage() {
  const seq = ++practiceLoadSeq;
  practiceFullSnapshotStatus = 'loading';
  const fetchJson = (url, options = {}) => fetch(url, options).then(response => {
    if (!response.ok) throw new Error(`${url} HTTP ${response.status}`);
    return response.json();
  });
  // Start every source together. The fast portfolio no longer waits for the
  // candidate scan, while the full snapshot hydrates richer historical data.
  const candidatesPromise = fetchJson('/api/practice_candidates');
  const manualCyclePromise = fetchJson('/api/niuniu_practice/manual-cycle', {cache: 'no-store'});
  const marketSummaryPromise = fetchJson('/api/niuniu_practice/market-summary', {cache: 'no-store'});
  const fastPracticePromise = fetchJson(
    '/api/niuniu_practice?fast=1&calendar_schema=1',
    {cache: 'no-cache'},
  );
  if (!practiceFullRequest) {
    practiceFullRequest = fetchJson('/api/niuniu_practice?snapshot_schema=2').then(
      payload => {
        practiceFullRequest = null;
        return payload;
      },
      error => {
        practiceFullRequest = null;
        throw error;
      },
    );
  }
  const fullPracticePromise = practiceFullRequest;
  const benchmarksPromise = fetchJson('/api/practice_benchmarks');
  const renderPracticeUpdate = () => {
    if (activeCategory === 'practice') render();
    saveViewState();
  };
  const tasks = [
    marketSummaryPromise.then(payload => {
      if (seq !== practiceLoadSeq) return;
      practiceMarketSummaryData = {...(payload || {}), loading:false};
      renderPracticeUpdate();
    }).catch(error => {
      if (seq !== practiceLoadSeq) return;
      console.error('practice market summary status error', error);
      practiceMarketSummaryData = {...practiceMarketSummaryData, loading:false, error:String(error)};
      renderPracticeUpdate();
    }),
    manualCyclePromise.then(payload => {
      if (seq !== practiceLoadSeq) return;
      practiceManualCycleData = payload || practiceManualCycleData;
      renderPracticeUpdate();
      if (practiceManualCycleData.running) schedulePracticeManualCyclePoll();
    }).catch(error => {
      if (seq === practiceLoadSeq) console.error('practice manual cycle status error', error);
    }),
    candidatesPromise.then(candidatePayload => {
      if (seq !== practiceLoadSeq) return;
      const candidateItems = candidatePayload.items || candidatePayload.candidates || [];
      practiceCandidatesData = {
        ...candidatePayload,
        items: candidateItems,
        count: candidatePayload.count || candidateItems.length,
      };
      renderPracticeUpdate();
    }).catch(error => {
      if (seq === practiceLoadSeq) console.error('practice candidates load error', error);
    }),
    fastPracticePromise.then(payload => {
      if (seq !== practiceLoadSeq || !isUsablePracticePayload(payload)) return;
      niuniuPracticeData = mergePracticePayloadSnapshots(niuniuPracticeData, payload);
      renderPracticeUpdate();
    }).catch(error => {
      if (seq === practiceLoadSeq) console.error('practice fast load error', error);
    }),
    fullPracticePromise.then(payload => {
      if (seq !== practiceLoadSeq) return;
      if (!isUsablePracticePayload(payload)) throw new Error('invalid full practice snapshot');
      practiceFullSnapshotStatus = 'loaded';
      niuniuPracticeData = mergePracticePayloadSnapshots(niuniuPracticeData, payload);
      renderPracticeUpdate();
    }).catch(error => {
      if (seq !== practiceLoadSeq) return;
      practiceFullSnapshotStatus = 'error';
      console.error('practice full load error', error);
      if (activeCategory === 'practice') render();
    }),
    benchmarksPromise.then(payload => {
      if (seq !== practiceLoadSeq) return;
      practiceBenchmarksData = payload || {items: []};
      renderPracticeUpdate();
    }).catch(error => {
      if (seq === practiceLoadSeq) console.error('practice benchmarks load error', error);
    }),
  ];
  await Promise.allSettled(tasks);
}
function renderTabs() {
  $('categoryTabs').innerHTML = visibleCategoryOrder().map(key => {
    const count = (key === 'indices' || key === 'practice') ? '' : ` · ${data.categories?.[key]?.count || 0}`;
    return `<a class="tab ${activeCategory === key ? 'active' : ''}" data-category="${key}" href="${CATEGORY_PATHS[key]}">${CATEGORY_LABELS[key]}${count}</a>`;
  }).join('');
  document.querySelectorAll('.tab[data-category]').forEach(tab => tab.onclick = (event) => {
    event.preventDefault();
    const nextCategory = tab.dataset.category;
    if (!nextCategory || !categoryAvailable(nextCategory) || nextCategory === activeCategory) return;
    if (activeCategory === 'x_monitor') cancelXMediaRequests();
    activeCategory = nextCategory;
    usRatingDayIndex = 0;
    ratingExpandedRowId = '';
    xExpandedRecordKey = '';
    marketExpandedRecordKey = '';
    if (activeCategory === 'market_monitor') {
      marketDayIndex = 0;
      applyCachedMarketPage();
    }
    if (activeCategory === 'x_monitor') {
      xPageOffset = 0;
      if (!applyCachedXPage(0)) xLoadedOffset = -1;
    }
    syncViewUrl({push:true});
    renderTabs();
    // Immediate optimistic switch: show cached/placeholder page in the same click frame,
    // then hydrate with fresh API data. This removes the perceived "button pressed but
    // nothing happens" delay when a heavy endpoint is cold.
    if (hasWarmData(activeCategory)) render();
    else $('feed').innerHTML = '<div class="loading">加载中…</div>';
    load({background:true}).catch(err => {
      if (err && err.name === 'AbortError') return;
      console.error(err);
    });
  });
}
function applyViewStateFromLocation() {
  const params = new URLSearchParams(location.search);
  activeCategory = normalizeActiveCategory(categoryFromLocation(params));
  indicesViewMode = params.get('panel') === 'market' ? 'market' : 'index';
  marketDayIndex = Math.max(0, Number(params.get('day') || 1) - 1);
  if (activeCategory === 'market_monitor') applyCachedMarketPage();
  xPageOffset = Math.max(0, (Number(params.get('page') || 1) - 1) * X_MONITOR_PAGE_SIZE);
  if (activeCategory !== 'x_monitor' || !applyCachedXPage(xPageOffset)) xLoadedOffset = -1;
  practicePositionMode = params.get('holdings') === 'sold' ? 'sold' : 'open';
  window.practicePositionMode = practicePositionMode;
  practiceCurveMode = params.get('curve') === 'daily' ? 'daily' : 'intraday';
  window.practiceCurveMode = practiceCurveMode;
  practicePositionBriefMode = params.get('brief') === '1';
  window.practicePositionBriefMode = practicePositionBriefMode;
  usRatingDayIndex = 0;
  ratingExpandedRowId = '';
  xExpandedRecordKey = '';
  marketExpandedRecordKey = '';
}
window.addEventListener('popstate', () => {
  const wasXMonitor = activeCategory === 'x_monitor';
  if (wasXMonitor) cancelXMediaRequests();
  applyViewStateFromLocation();
  if (location.pathname + location.search !== currentViewUrl()) syncViewUrl();
  renderTabs();
  if (hasWarmData(activeCategory)) render();
  else $('feed').innerHTML = '<div class="loading">加载中…</div>';
  load({background:true}).catch(err => {
    if (err && err.name === 'AbortError') return;
    console.error(err);
  });
});
function filtered() {
  return (data.records || []).filter(r => {
    if (activeCategory !== 'all' && r.category !== activeCategory) return false;
    return true;
  }).sort((a, b) => {
    const at = Number(a.timestamp || Date.parse(a.time || 0) / 1000 || 0);
    const bt = Number(b.timestamp || Date.parse(b.time || 0) / 1000 || 0);
    return bt - at;
  });
}
function clamp01(v) {
  return Math.max(0, Math.min(1, v));
}
function clockMinuteOfDay(timeText) {
  const m = String(timeText || '').match(/(\d{1,2}):(\d{2})/);
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}
function globalSessionElapsedMinute(clockMinute, sessionStartMinute) {
  if (clockMinute == null || sessionStartMinute == null) return null;
  let elapsed = clockMinute - sessionStartMinute;
  if (elapsed < 0) elapsed += 24 * 60;
  return elapsed;
}
function marketClockParts(timeZone) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone, hour12:false, weekday:'short', hour:'2-digit', minute:'2-digit'
  }).formatToParts(new Date());
  const pick = type => parts.find(p => p.type === type)?.value || '';
  let hour = Number(pick('hour'));
  const minute = Number(pick('minute'));
  if (hour === 24) hour = 0;
  return {weekday: pick('weekday'), minuteOfDay: hour * 60 + minute};
}
function isWeekdayClock(clock) {
  return ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'].includes(clock.weekday);
}
function isAShareOpenNow() {
  const c = marketClockParts('Asia/Shanghai');
  const m = c.minuteOfDay;
  return isWeekdayClock(c) && ((m >= 9 * 60 + 15 && m <= 11 * 60 + 30) || (m >= 13 * 60 && m <= 15 * 60));
}
function isAShareDaySessionNow() {
  const c = marketClockParts('Asia/Shanghai');
  const m = c.minuteOfDay;
  return isWeekdayClock(c) && m >= 9 * 60 + 15 && m <= 15 * 60;
}
function isUsOpenNow() {
  const c = marketClockParts('America/New_York');
  const m = c.minuteOfDay;
  return isWeekdayClock(c) && m >= 9 * 60 + 30 && m <= 16 * 60;
}
function indicesSwitchSession(aIndexItems = []) {
  const hasAIndexItems = !Array.isArray(aIndexItems) || aIndexItems.length > 0;
  if (isAShareOpenNow() || (isAShareDaySessionNow() && hasAIndexItems)) return 'a_share';
  if (isUsOpenNow()) return 'us_open';
  return 'global';
}
function compressedGlobalSessionProgresses(minuteLine, sessionStartMinute) {
  const rows = [];
  (minuteLine || []).forEach((p, idx) => {
    const clockMinute = clockMinuteOfDay(p.time);
    const elapsed = globalSessionElapsedMinute(clockMinute, sessionStartMinute);
    if (elapsed != null) rows.push({idx, elapsed});
  });
  if (rows.length < 2) return new Map();
  const gapThresholdMinutes = 30;
  const keptGapMinutes = 1;
  let removed = 0;
  let prevElapsed = rows[0].elapsed;
  const compressed = rows.map((row, i) => {
    if (i > 0) {
      const gap = row.elapsed - prevElapsed;
      if (gap > gapThresholdMinutes) {
        removed += Math.max(0, gap - keptGapMinutes);
      }
      prevElapsed = row.elapsed;
    }
    return {idx: row.idx, elapsed: row.elapsed - removed};
  });
  const denominator = Math.max(1, 24 * 60 - removed);
  return new Map(compressed.map(row => [row.idx, clamp01(row.elapsed / denominator)]));
}
function indexSparklineProgress(point, item, fallbackProgress, sessionStartMinute=null) {
  const marketType = String(item.market_type || '');
  if (marketType === 'a_index') {
    const minute = Number(point.minute);
    const tradeMinute = Number.isFinite(minute) ? minute : tradeMinuteOfDay(point.time);
    if (tradeMinute != null) return clamp01(tradeMinute / 240);
  }
  const clockMinute = clockMinuteOfDay(point.time);
  if (clockMinute != null) {
    if (marketType === 'us_index') return clamp01((clockMinute - (9 * 60 + 30)) / 390);
    const elapsed = globalSessionElapsedMinute(clockMinute, sessionStartMinute);
    if (elapsed != null) return clamp01(elapsed / (24 * 60));
  }
  return fallbackProgress;
}
function renderSparkline(vals, item={}) {
  const w=120, h=34, pad=4;
  const minuteLine = Array.isArray(item.minute_line) ? item.minute_line : [];
  let points = [];
  if (minuteLine.length >= 2) {
    const sessionStartMinute = (() => {
      for (const p of minuteLine) {
        const minute = clockMinuteOfDay(p.time);
        if (minute != null) return minute;
      }
      return null;
    })();
    const marketType = String(item.market_type || '');
    const compressedProgresses = marketType && marketType !== 'a_index' && marketType !== 'us_index'
      ? compressedGlobalSessionProgresses(minuteLine, sessionStartMinute)
      : new Map();
    points = minuteLine.map((p, i) => {
      const price = Number(p.price);
      if (!Number.isFinite(price) || price <= 0) return null;
      const fallbackProgress = i / Math.max(1, minuteLine.length - 1);
      const progress = compressedProgresses.has(i)
        ? compressedProgresses.get(i)
        : indexSparklineProgress(p, item, fallbackProgress, sessionStartMinute);
      return {price, x: clamp01(progress) * w};
    }).filter(Boolean);
  } else {
    const prices = (vals || []).map(v => Number(v)).filter(v => Number.isFinite(v) && v > 0);
    points = prices.map((price, i) => ({price, x: (i / Math.max(1, prices.length - 1)) * w}));
  }
  if (points.length < 2) return '';
  const currentPrice = Number(item.price);
  const currentChange = Number(item.change);
  const currentPct = Number(item.change_pct);
  let base = Number(item.prev_close ?? item.prevClose);
  if (!Number.isFinite(base) || base <= 0) {
    if (Number.isFinite(currentPrice) && Number.isFinite(currentChange) && Math.abs(currentPrice - currentChange) > 0) {
      base = currentPrice - currentChange;
    } else if (Number.isFinite(currentPrice) && Number.isFinite(currentPct) && currentPct > -99.9) {
      base = currentPrice / (1 + currentPct / 100);
    }
  }
  if (!Number.isFinite(base) || base <= 0) base = points[0].price;
  const pctVals = points.map(p => (p.price / base - 1) * 100);
  const minPct = Math.min(0, ...pctVals);
  const maxPct = Math.max(0, ...pctVals);
  const padPct = Math.max((maxPct - minPct) * 0.16, 0.05);
  const yMin = minPct - padPct;
  const yMax = maxPct + padPct;
  const span=(yMax-yMin)||1;
  const y = pct => h-pad-((pct-yMin)/span)*(h-pad*2);
  const pts=pctVals.map((v,i)=>[points[i].x,y(v)]);
  const line = pts.map((p,i)=>`${i?'L':'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  const zeroY = y(0);
  const firstX = pts[0][0];
  const lastX = pts[pts.length - 1][0];
  return `<svg class="sparkline" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <line class="sparkline-zero" x1="0" x2="${w}" y1="${zeroY.toFixed(1)}" y2="${zeroY.toFixed(1)}"><title>0% 基准线</title></line>
    <path class="sparkline-area" d="${line} L${lastX.toFixed(1)} ${zeroY.toFixed(1)} L${firstX.toFixed(1)} ${zeroY.toFixed(1)} Z"></path>
    <path class="sparkline-line" d="${line}"></path>
  </svg>`;
}
function tradeMinuteOfDay(timeText) {
  const m = String(timeText || '').match(/(\d{2}):(\d{2})/);
  if (!m) return null;
  const minutes = Number(m[1]) * 60 + Number(m[2]);
  const amStart = 9 * 60 + 30, amEnd = 11 * 60 + 30, pmStart = 13 * 60, pmEnd = 15 * 60;
  if (minutes < amStart || minutes > pmEnd || (minutes > amEnd && minutes < pmStart)) return null;
  if (minutes <= amEnd) return minutes - amStart;
  return 120 + (minutes - pmStart);
}
function toggleBenchmark(symbol) {
  benchmarkOverlay[symbol] = !benchmarkOverlay[symbol];
  render();
}
function setPracticeCurveMode(mode) {
  practiceCurveMode = mode === 'daily' ? 'daily' : 'intraday';
  window.practiceCurveMode = practiceCurveMode;
  syncViewUrl();
  if (activeCategory === 'practice') render();
  saveViewState();
}
function setPracticePositionMode(mode) {
  practicePositionMode = mode === 'sold' ? 'sold' : 'open';
  window.practicePositionMode = practicePositionMode;
  syncViewUrl();
  if (activeCategory === 'practice') render();
  saveViewState();
}
function setPracticePositionBriefMode(enabled) {
  practicePositionBriefMode = !!enabled;
  window.practicePositionBriefMode = practicePositionBriefMode;
  syncViewUrl();
  if (activeCategory === 'practice') render();
  saveViewState();
}
function renderPracticeHoverTooltip(item) {
  if (!item) return '';
  const rows = (item.rows || []).map(row => {
    const cls = row.cls ? ` class="${esc(row.cls)}"` : '';
    return `<span class="practice-hover-tooltip-row"><span>${esc(row.label)}</span><strong${cls}>${esc(row.value)}</strong></span>`;
  }).join('');
  return `<span class="practice-hover-tooltip-time">${esc(item.timeText || '--')}</span>${rows}`;
}
function practiceHoverLayerPoints(layer) {
  if (!layer) return [];
  if (Array.isArray(layer._practiceHoverPoints)) return layer._practiceHoverPoints;
  try {
    layer._practiceHoverPoints = JSON.parse(layer.dataset.practiceHoverPoints || '[]');
  } catch (err) {
    layer._practiceHoverPoints = [];
  }
  return layer._practiceHoverPoints;
}
function setPracticeHoverPoint(layer, point) {
  if (!layer || !point) return;
  layer.classList.add('active');
  layer.classList.toggle('place-left', Number(point.xPct || 0) > 66);
  layer.classList.toggle('place-bottom', Number(point.yPct || 0) < 34);
  layer.style.setProperty('--hover-x-pct', `${Number(point.xPct || 0).toFixed(2)}%`);
  layer.style.setProperty('--hover-y-pct', `${Number(point.yPct || 0).toFixed(2)}%`);
  if (point.ariaLabel) layer.setAttribute('aria-label', point.ariaLabel);
  const tooltip = layer.querySelector('.practice-hover-tooltip');
  if (tooltip) tooltip.innerHTML = renderPracticeHoverTooltip(point);
}
function practiceHoverNearestPoint(layer, clientX) {
  const points = practiceHoverLayerPoints(layer);
  if (!points.length) return null;
  const rect = layer.getBoundingClientRect();
  const xPct = rect.width > 0 ? clamp01((clientX - rect.left) / rect.width) * 100 : 0;
  let nearest = points[0];
  let bestDistance = Math.abs(Number(nearest.xPct || 0) - xPct);
  for (const point of points.slice(1)) {
    const distance = Math.abs(Number(point.xPct || 0) - xPct);
    if (distance < bestDistance) {
      nearest = point;
      bestDistance = distance;
    }
  }
  return nearest;
}
function practiceHoverMove(event, layer) {
  if (!layer) return;
  layer.dataset.practicePointerType = event.pointerType || 'mouse';
  if (event.type === 'pointerdown') {
    layer.dataset.practicePointerDown = '1';
    if (layer.setPointerCapture && event.pointerId != null) {
      try { layer.setPointerCapture(event.pointerId); } catch (err) {}
    }
  }
  const point = practiceHoverNearestPoint(layer, event.clientX);
  setPracticeHoverPoint(layer, point);
  if (event.cancelable && (event.pointerType === 'touch' || layer.dataset.practicePointerDown === '1')) {
    event.preventDefault();
  }
}
function practiceHoverRelease(event, layer) {
  if (!layer) return;
  layer.dataset.practicePointerDown = '0';
  if (layer.releasePointerCapture && event.pointerId != null) {
    try { layer.releasePointerCapture(event.pointerId); } catch (err) {}
  }
}
function practiceHoverLeave(layer) {
  if (!layer) return;
  if (layer.dataset.practicePointerDown === '1' || layer.dataset.practicePointerType === 'touch') return;
  layer.classList.remove('active');
}
function straightSvgPath(points) {
  if (!Array.isArray(points) || points.length === 0) return '';
  return points.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
}
function smoothSvgPath(points) {
  if (!Array.isArray(points) || points.length === 0) return '';
  if (points.length === 1) return `M${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`;
  if (points.length === 2) return points.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  const d = [`M${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`];
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[Math.max(0, i - 1)];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[Math.min(points.length - 1, i + 2)];
    const cp1x = Math.min(p2[0], Math.max(p1[0], p1[0] + (p2[0] - p0[0]) / 6));
    const cp1y = p1[1] + (p2[1] - p0[1]) / 6;
    const cp2x = Math.min(p2[0], Math.max(p1[0], p2[0] - (p3[0] - p1[0]) / 6));
    const cp2y = p2[1] - (p3[1] - p1[1]) / 6;
    d.push(`C${cp1x.toFixed(1)} ${cp1y.toFixed(1)} ${cp2x.toFixed(1)} ${cp2y.toFixed(1)} ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`);
  }
  return d.join(' ');
}
function dayMinuteOfDay(timeText) {
  const m = String(timeText || '').match(/(\d{2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return null;
  const hh = Number(m[1]), mm = Number(m[2]), ss = Number(m[3] || 0);
  if (!Number.isFinite(hh) || !Number.isFinite(mm) || !Number.isFinite(ss)) return null;
  return Math.max(0, Math.min(1439.999, hh * 60 + mm + ss / 60));
}
function tradingClockMinuteOfDay(timeText) {
  const minute = dayMinuteOfDay(timeText);
  if (minute == null) return null;
  const start = 9 * 60 + 30;
  const amEnd = 11 * 60 + 30;
  const pmStart = 13 * 60;
  const end = 15 * 60;

  if (minute < start || minute > end || (minute > amEnd && minute < pmStart)) return null;

  // 上午时间段
  if (minute <= amEnd) {
    return minute - start;
  }
  // 下午时间段：需要扣除中间休市的 90 分钟 (13:00 - 11:30)
  return (minute - start) - 90;
}
function clampedTradingClockMinuteOfDay(timeText) {
  const minute = dayMinuteOfDay(timeText);
  if (minute == null) return 0;
  const start = 9 * 60 + 30;
  const amEnd = 11 * 60 + 30;
  const pmStart = 13 * 60;
  const end = 15 * 60;
  if (minute <= start) return 0;
  if (minute <= amEnd) return minute - start;
  // 午间休市的净值心跳点应固定在上午收盘位置，而不是回到 09:30。
  if (minute < pmStart) return 120;
  if (minute <= end) return (minute - start) - 90;
  return 240;
}
function normalizePracticeEquityPoints(source) {
  return (source || [])
    .map(p => ({time: p.time || '', equity: Number(p.equity), pnlPct: Number(p.pnl_pct ?? p.pnlPct)}))
    .filter(p => Number.isFinite(p.equity) && p.time);
}
function compactPracticeCalendarHistoryPoints(payload) {
  const calendar = payload?.calendar_history;
  if (!calendar || Number(calendar.schema_version) !== 1 || !calendar.days || typeof calendar.days !== 'object') return [];
  const points = [];
  for (const [date, rows] of Object.entries(calendar.days)) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Array.isArray(rows)) continue;
    for (const row of rows) {
      const clock = String(row?.clock || '');
      if (!/^\d{2}:\d{2}(?::\d{2})?$/.test(clock)) continue;
      points.push({time:`${date} ${clock}`, equity:Number(row?.equity)});
    }
  }
  return points.filter(point => Number.isFinite(point.equity));
}
function practiceCalendarHistoryPoints(payload) {
  const rawPoints = normalizePracticeEquityPoints(payload?.equity_history || []);
  const rawDates = new Set(rawPoints.map(point => String(point.time || '').slice(0, 10)));
  const compactPoints = compactPracticeCalendarHistoryPoints(payload)
    .filter(point => !rawDates.has(String(point.time || '').slice(0, 10)));
  return normalizePracticeEquityPoints(mergePracticeTimedRows(compactPoints, rawPoints));
}
function practiceCalendarHistoryCoversDate(payload, date) {
  const calendar = payload?.calendar_history;
  if (!calendar || Number(calendar.schema_version) !== 1 || calendar.complete !== true || !calendar.days || typeof calendar.days !== 'object') return false;
  if (Object.prototype.hasOwnProperty.call(calendar.days, date)) return true;
  const start = String(calendar.coverage_start || '');
  const end = String(calendar.coverage_end || '');
  return Boolean(start && end && date >= start && date <= end);
}
function normalizePracticeTradeMarkers(source) {
  return (source || [])
    .map(trade => {
      const action = String(trade?.action || '').toUpperCase();
      const afterPct = trade?.position_after_trade_pct;
      return {
        time: String(trade?.time || ''),
        action,
        code: String(trade?.code || ''),
        name: String(trade?.name || ''),
        shares: Number(trade?.shares),
        price: Number(trade?.price),
        pnl: Number(trade?.pnl),
        pnlPct: Number(trade?.pnl_pct),
        isFullExit: trade?.is_full_exit === true || (action === 'SELL' && afterPct !== null && afterPct !== undefined && Number(afterPct) <= 0),
      };
    })
    .filter(trade => trade.time && (trade.action === 'BUY' || trade.action === 'SELL'))
    .sort((a, b) => a.time.localeCompare(b.time));
}
function practiceTradeMarkersForDate(date) {
  const payload = niuniuPracticeData || {};
  const source = Array.isArray(payload.trade_markers) && payload.trade_markers.length
    ? payload.trade_markers
    : (payload.trade_log || []);
  return normalizePracticeTradeMarkers(source).filter(trade => trade.time.slice(0, 10) === date);
}
function practiceTradeShareText(shares) {
  const value = Number(shares);
  if (!Number.isFinite(value)) return '--';
  return Number.isInteger(value) ? String(value) : fmtNumber(value, 2);
}
function practiceTradePriceText(price) {
  const value = Number(price);
  if (!Number.isFinite(value)) return '--';
  const cents = Math.round(value * 100) / 100;
  return value === cents ? value.toFixed(2) : value.toFixed(3);
}
function practiceTradeMarkerLine(trade) {
  const side = trade.action === 'BUY' ? '买' : '卖';
  const stockName = trade.name || trade.code || '--';
  let text = `${side} ${stockName} ${practiceTradeShareText(trade.shares)}股×${practiceTradePriceText(trade.price)}`;
  if (trade.action === 'SELL' && trade.isFullExit && Number.isFinite(trade.pnl)) {
    text += ` 盈亏${fmtSignedAmount(trade.pnl)}`;
    if (Number.isFinite(trade.pnlPct)) text += ` (${fmtSignedPct(trade.pnlPct)})`;
  }
  return text;
}
function renderPracticeTradeMarkerLine(trade) {
  const isBuy = trade.action === 'BUY';
  const side = isBuy ? '买' : '卖';
  const sideClass = isBuy ? 'buy' : 'sell';
  const stockName = trade.name || trade.code || '--';
  const fillText = `${practiceTradeShareText(trade.shares)}股×${practiceTradePriceText(trade.price)}`;
  const hasPnl = !isBuy && trade.isFullExit && Number.isFinite(trade.pnl);
  const pnlText = hasPnl
    ? `盈亏${fmtSignedAmount(trade.pnl)}${Number.isFinite(trade.pnlPct) ? ` (${fmtSignedPct(trade.pnlPct)})` : ''}`
    : '';
  const pnlClass = Number(trade.pnl) >= 0 ? 'up' : 'down';
  return `<span class="practice-trade-marker-line ${sideClass}">
    <span class="practice-trade-marker-side">${side}</span>
    <span class="practice-trade-marker-stock">${esc(stockName)}</span>
    <span class="practice-trade-marker-fill">${esc(fillText)}</span>
    ${pnlText ? `<span class="practice-trade-marker-pnl ${pnlClass}">${esc(pnlText)}</span>` : ''}
  </span>`;
}
function practiceInterpolatedYAtX(series, targetX) {
  const points = (series || [])
    .map(point => Array.isArray(point) ? {x:Number(point[0]), y:Number(point[1])} : {x:Number(point.x), y:Number(point.y)})
    .filter(point => Number.isFinite(point.x) && Number.isFinite(point.y))
    .sort((a, b) => a.x - b.x);
  if (!points.length || !Number.isFinite(Number(targetX))) return null;
  const x = Number(targetX);
  if (x <= points[0].x) return points[0].y;
  if (x >= points.at(-1).x) return points.at(-1).y;
  for (let idx = 1; idx < points.length; idx += 1) {
    const right = points[idx];
    if (right.x < x) continue;
    const left = points[idx - 1];
    const span = right.x - left.x;
    if (span <= 0) return right.y;
    return left.y + (right.y - left.y) * ((x - left.x) / span);
  }
  return points.at(-1).y;
}
function renderPracticeTradeMarkers(date, xFromTime, series, viewportWidth, viewportHeight) {
  const trades = practiceTradeMarkersForDate(date)
    .filter(trade => tradingClockMinuteOfDay(trade.time) != null && Number.isFinite(trade.shares) && trade.shares > 0);
  if (!trades.length) return '';
  const groups = new Map();
  for (const trade of trades) {
    const minuteKey = trade.time.slice(0, 16);
    if (!groups.has(minuteKey)) groups.set(minuteKey, []);
    groups.get(minuteKey).push(trade);
  }
  return [...groups.entries()].map(([minuteKey, groupTrades]) => {
    const xValues = groupTrades.map(trade => Number(xFromTime(trade.time))).filter(Number.isFinite);
    if (!xValues.length) return '';
    const x = xValues.reduce((sum, value) => sum + value, 0) / xValues.length;
    const y = practiceInterpolatedYAtX(series, x);
    if (!Number.isFinite(y)) return '';
    const xPct = Math.max(0, Math.min(100, x / viewportWidth * 100));
    const yPct = Math.max(0, Math.min(100, y / viewportHeight * 100));
    const actions = new Set(groupTrades.map(trade => trade.action));
    let sideClass = 'mixed';
    if (actions.size === 1 && actions.has('BUY')) {
      sideClass = 'buy';
    } else if (actions.size === 1 && actions.has('SELL')) {
      const fullExitCount = groupTrades.filter(trade => trade.isFullExit).length;
      sideClass = fullExitCount === groupTrades.length
        ? 'sell-full'
        : fullExitCount === 0 ? 'sell-partial' : 'sell-mixed';
    }
    const markerText = groupTrades.length > 1 ? String(groupTrades.length) : (actions.has('BUY') ? 'B' : 'S');
    const placement = [xPct > 72 ? 'place-left' : xPct < 28 ? 'place-right' : '', yPct < 34 ? 'place-bottom' : ''].filter(Boolean).join(' ');
    const lines = groupTrades.map(practiceTradeMarkerLine);
    const timeText = minuteKey.slice(11);
    const ariaLabel = `${timeText} ${lines.join('；')}`;
    return `<button type="button" class="practice-trade-marker ${sideClass} ${placement}" style="--marker-x:${xPct.toFixed(2)}%;top:${yPct.toFixed(2)}%" aria-label="${esc(ariaLabel)}">
      ${esc(markerText)}
      <span class="practice-trade-marker-tooltip" aria-hidden="true">
        <span class="practice-trade-marker-time">${esc(timeText)}</span>
        ${groupTrades.map(renderPracticeTradeMarkerLine).join('')}
      </span>
    </button>`;
  }).join('');
}
function practicePctAxisBounds(values) {
  const finite = (values || []).map(Number).filter(Number.isFinite);
  if (!finite.length) return {min: -0.01, max: 0.01, digits: 3};
  const dataMin = Math.min(...finite);
  const dataMax = Math.max(...finite);
  const dataRange = Math.max(0, dataMax - dataMin);
  const minSpan = Math.min(0.2, Math.max(0.02, dataRange * 1.4));
  const pad = Math.max(dataRange * 0.18, minSpan * 0.10);
  let min = dataMin - pad;
  let max = dataMax + pad;
  let span = max - min;
  if (span < minSpan) {
    const expand = (minSpan - span) / 2;
    min -= expand;
    max += expand;
    span = max - min;
  }
  const zeroNear = dataMin <= 0 && dataMax >= 0
    || Math.min(Math.abs(dataMin), Math.abs(dataMax)) <= Math.max(dataRange * 2, 0.04);
  if (zeroNear) {
    min = Math.min(min, -0.01);
    max = Math.max(max, 0.01);
    span = max - min;
  }
  return {min, max, digits: span < 0.05 ? 3 : 2};
}
function compactPracticeDailyPoints(points) {
  const byDate = new Map();
  for (const p of points || []) {
    const date = String(p.time || '').slice(0, 10);
    if (!date) continue;
    const prev = byDate.get(date);
    if (!prev || (new Date(p.time).getTime() || 0) >= (new Date(prev.time).getTime() || 0)) {
      byDate.set(date, p);
    }
  }
  return [...byDate.values()].sort((a, b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0));
}
function currentDateKey(date=new Date()) {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(date);
    const get = type => (parts.find(part => part.type === type) || {}).value || '';
    const year = get('year'), month = get('month'), day = get('day');
    if (year && month && day) return `${year}-${month}-${day}`;
  } catch (err) {}
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}
function practicePayloadDateKey() {
  const p = niuniuPracticeData || {};
  const tradingCalendar = p.trading_calendar || {};
  return String(p.current_date || tradingCalendar.date || currentDateKey()).slice(0, 10);
}
function buildPracticeCalendarRows(history, dailyHistory, initialCash=1000000) {
  const normalizedHistory = normalizePracticeEquityPoints(history);
  const normalizedDailyHistory = normalizePracticeEquityPoints(dailyHistory);
  const byDate = new Map();
  for (const p of [...compactPracticeDailyPoints(normalizedHistory), ...compactPracticeDailyPoints(normalizedDailyHistory)]) {
    const date = String(p.time || '').slice(0, 10);
    if (!date) continue;
    const prev = byDate.get(date);
    if (!prev || (new Date(p.time).getTime() || 0) >= (new Date(prev.time).getTime() || 0)) {
      byDate.set(date, p);
    }
  }
  const points = [...byDate.values()]
    .sort((a, b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0));
  let previousEquity = Number(initialCash);
  return points.map(p => {
    const date = String(p.time || '').slice(0, 10);
    const equity = Number(p.equity);
    const base = Number.isFinite(previousEquity) && previousEquity > 0 ? previousEquity : equity;
    const pnl = Number.isFinite(equity) && Number.isFinite(base) ? equity - base : 0;
    const pnlPct = base ? pnl / base * 100 : 0;
    previousEquity = equity;
    return {date, time:p.time, equity, pnl, pnlPct};
  }).filter(row => row.date);
}
function renderPracticeChartTitle(title, visibleDate='', measureDate=visibleDate) {
  const shownDate = String(visibleDate || '').slice(0, 10);
  const reservedDate = String(measureDate || shownDate || '0000-00-00').slice(0, 10);
  return `<div class="practice-chart-title">
    <span class="practice-chart-title-text">${esc(title)}${shownDate ? `（${esc(shownDate)}）` : ''}</span>
    <span class="practice-chart-title-measure" aria-hidden="true">今日收益曲线（${esc(reservedDate)}）</span>
  </div>`;
}
function renderPracticeCurve(history, dailyHistory, initialCash=1000000, benchmarks={items:[]}) {
  const isDailyMode = practiceCurveMode === 'daily';
  const normalizedHistory = normalizePracticeEquityPoints(history);
  const normalizedDailyHistory = normalizePracticeEquityPoints(dailyHistory);
  const tradingCalendar = (niuniuPracticeData && niuniuPracticeData.trading_calendar) || {};
  const isNonTradingCalendarDay = tradingCalendar.is_trading_day === false;
  const targetDate = practicePayloadDateKey();
  let rawPoints = [];
  let dailyCompactedPoints = [];
  let intradayBasePoint = null;
  if (isDailyMode) {
    const compactedFromDaily = compactPracticeDailyPoints(normalizedDailyHistory);
    const compactedFromIntraday = compactPracticeDailyPoints(normalizedHistory);
    const byDate = new Map();
    for (const p of [...compactedFromIntraday, ...compactedFromDaily]) {
      const date = String(p.time || '').slice(0, 10);
      if (!date) continue;
      const prev = byDate.get(date);
      if (!prev || (new Date(p.time).getTime() || 0) >= (new Date(prev.time).getTime() || 0)) {
        byDate.set(date, p);
      }
    }
    dailyCompactedPoints = [...byDate.values()]
      .sort((a, b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0));
    rawPoints = dailyCompactedPoints;
  } else {
    rawPoints = normalizedHistory;
  }
  rawPoints = [...rawPoints].sort((a, b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0));
  if (rawPoints.length < (isDailyMode ? 2 : 1)) return '<div class="empty" style="padding:18px">收益曲线等待更多净值点…</div>';
  const latestTradingClockPoint = rawPoints.filter(p => tradingClockMinuteOfDay(p.time) != null).at(-1);
  const latestDataDay = (latestTradingClockPoint || rawPoints[rawPoints.length - 1]).time.slice(0, 10);
  const latestDay = !isDailyMode && !isNonTradingCalendarDay && targetDate ? targetDate : latestDataDay;
  if (!isDailyMode) {
    const priorByDate = new Map();
    for (const p of [...compactPracticeDailyPoints(normalizedHistory), ...compactPracticeDailyPoints(normalizedDailyHistory)]) {
      const date = String(p.time || '').slice(0, 10);
      if (!date || date >= latestDay) continue;
      const prev = priorByDate.get(date);
      if (!prev || (new Date(p.time).getTime() || 0) >= (new Date(prev.time).getTime() || 0)) {
        priorByDate.set(date, p);
      }
    }
    intradayBasePoint = [...priorByDate.values()]
      .sort((a, b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0))
      .at(-1) || null;
  }
  const w = 720, h = 210, left = 12, right = 58, top = 18, bottom = 24;
  const innerW = w - left - right, innerH = h - top - bottom;
  const totalSessionMinutes = 4 * 60; // 4小时 = 240分钟
  let points = [];
  let timeTicks = [];
  let xFromTime;

  if (isDailyMode) {
    points = dailyCompactedPoints;
    if (points.length < 2) return '<div class="empty" style="padding:18px">累计收益等待更多交易日净值点…</div>';
    // 横轴按日期
    const totalDays = points.length;
    xFromTime = time => {
      const idx = points.findIndex(p => p.time === time);
      if (idx < 0) return left;
      return left + (idx / Math.max(1, totalDays - 1)) * innerW;
    };
    if (totalDays > 1) {
      if (totalDays <= 5) {
        timeTicks = points.map((p, idx) => ({
          label: p.time.slice(5, 10),
          x: left + (idx / Math.max(1, totalDays - 1)) * innerW,
        }));
      } else {
        timeTicks = [
          {label: points[0].time.slice(5,10), x: left},
          {label: points[totalDays-1].time.slice(5,10), x: left + innerW},
        ];
        timeTicks.splice(1, 0, {
          label: points[Math.floor((totalDays-1)/2)].time.slice(5,10),
          x: left + innerW * 0.5,
        });
      }
    }
  } else {
    const dayPoints = rawPoints.filter(p => p.time.slice(0, 10) === latestDay);
    const sessionPoints = dayPoints.filter(p => tradingClockMinuteOfDay(p.time) != null);
    if (sessionPoints.length >= 1) {
      points = sessionPoints;
    } else if (isNonTradingCalendarDay && dayPoints.length >= 2) {
      points = dayPoints;
    } else {
      points = [];
    }
    if (points.length < 1) {
      const modeButtons = `<div class="practice-mode-control" aria-label="收益曲线模式">
        <button class="practice-mode-btn active" type="button" onclick="setPracticeCurveMode('intraday')">当日收益</button>
        <button class="practice-mode-btn" type="button" onclick="setPracticeCurveMode('daily')">累计收益</button>
      </div>`;
      const calendarButton = `<button class="practice-calendar-open-btn" type="button" onclick="openPracticeCalendar(event)">交易日历</button>`;
      const latestHint = latestDataDay && latestDataDay !== latestDay ? ` · 最近已有分时点 ${esc(latestDataDay)}` : '';
      const emptyTitleHtml = renderPracticeChartTitle(
        '今日收益曲线',
        isNonTradingCalendarDay && latestDay ? latestDay : '',
        latestDay || targetDate,
      );
      const emptySub = isNonTradingCalendarDay
        ? `非交易日展示最近交易日 · ${latestHint.replace(/^ · /, '') || '等待交易日'}`
        : `北京时间 ${esc(latestDay || targetDate || '--')} · 等待今日盘中净值点${latestHint}`;
      return `<div class="practice-chart-card">
        <div class="practice-chart-head">
          <div>
            <div class="practice-chart-title-row">
              ${emptyTitleHtml}
              ${modeButtons}
              ${calendarButton}
            </div>
            <div class="practice-chart-sub">${emptySub}</div>
          </div>
        </div>
        <div class="empty" style="padding:18px">今日收益曲线等待北京时间 ${esc(latestDay || targetDate || '--')} 的盘中净值点…</div>
      </div>`;
    }

    // 按时间排序并去重
    points = [...points].sort((a,b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0));
    const seenTimes = new Set();
    points = points.filter(p => {
      const key = String(p.time || '');
      if (seenTimes.has(key)) return false;
      seenTimes.add(key);
      return true;
    });

    // 降采样：只保留盘中发生的点（避免盘后大量相同x坐标的点堆积导致贝塞尔曲线错乱）
    points = points.filter((p, i, arr) => {
      if (i === 0 || i === arr.length - 1) return true; // 保留首尾
      const m = dayMinuteOfDay(p.time);
      // 过滤掉盘前和盘后的密集点，只留一个
      if (m != null && (m < 9 * 60 + 30 || m > 15 * 60)) {
        const prev = dayMinuteOfDay(arr[i-1].time);
        if (prev != null && (prev < 9 * 60 + 30 || prev > 15 * 60)) return false;
      }
      return true;
    });

    xFromTime = time => {
      const clampedMinute = Math.max(0, Math.min(totalSessionMinutes, clampedTradingClockMinuteOfDay(time)));
      return left + (clampedMinute / totalSessionMinutes) * innerW;
    };
    timeTicks = [
      {label:'09:30', x:left},
      {label:'11:30', x:left + innerW * 0.5},
      {label:'15:00', x:left + innerW},
    ];
  }
  const vals = points.map(p => p.equity);
  const intradayBaseEquity = Number(intradayBasePoint?.equity);
  const hasIntradayOpenBase = !isDailyMode && Number.isFinite(intradayBaseEquity) && intradayBaseEquity > 0;
  const chartBase = isDailyMode ? initialCash : (hasIntradayOpenBase ? intradayBaseEquity : vals[0]);
  const chartPcts = vals.map(v => chartBase ? (v / chartBase - 1) * 100 : 0);
  const chartDeltas = vals.map(v => v - (chartBase || 0));
  const last = vals[vals.length - 1], prev = vals[Math.max(0, vals.length - 2)];
  // 收益曲线只展示牛牛账户本身，指数对照不再叠加，避免干扰账户收益率观察。
  const activeBenchmarks = [];
  const benchmarkSeries = activeBenchmarks.map((b, idx) => ({...b, color: b.symbol === 'sh000001' ? '#f59e0b' : b.symbol === 'sh000300' ? '#60a5fa' : b.symbol === 'sz399006' ? '#ec4899' : '#8b5cf6'}));

  // 上一交易日净值会作为 09:30 的 0% 起点，因此纵轴也必须包含 0%。
  const axisPcts = hasIntradayOpenBase ? [0, ...chartPcts] : chartPcts;
  const yAxis = practicePctAxisBounds(axisPcts);
  const yMinPct = yAxis.min;
  const yMaxPct = yAxis.max;

  const span = (yMaxPct - yMinPct) || 1;
  const y = pct => top + (yMaxPct - pct) / span * innerH;
  const clampPct = pct => Math.max(yMinPct, Math.min(yMaxPct, pct));
  const plottedPts = points.map((p, i) => [xFromTime(p.time), y(chartPcts[i])]);
  const pts = plottedPts.slice();

  // 当存在上一交易日净值时，将它作为 09:30 的 0% 起点连接到首个真实盘中点。
  // 没有上一交易日基准时，仍只在已有两点后沿用原来的左侧补平逻辑。
  let hasSyntheticOpenAnchor = false;
  if (hasIntradayOpenBase && pts.length > 0) {
    const openAnchor = [left, y(0)];
    const firstPoint = pts[0];
    if (Math.abs(firstPoint[0] - openAnchor[0]) > 0.1 || Math.abs(firstPoint[1] - openAnchor[1]) > 0.1) {
      pts.unshift(openAnchor);
      hasSyntheticOpenAnchor = true;
    }
  } else if (!isDailyMode && points.length > 1 && pts.length > 0 && pts[0][0] > left + 1) {
    pts.unshift([left, pts[0][1]]);
  }

  const benchmarkPaths = benchmarkSeries.map(b => {
    const bpts = b.points
      .filter(pt => Number.isFinite(Number(pt.pct)) && Number.isFinite(Number(pt.minute)))
      .map(pt => [left + (Number(pt.minute) / totalSessionMinutes) * innerW, y(clampPct(Number(pt.pct))) ]);
    const d = smoothSvgPath(bpts);
    const lastPct = b.points.length ? Number(b.points[b.points.length - 1].pct) : null;
    return {...b, d, lastPct};
  }).filter(b => b.d);
  const hasCurveSegment = pts.length > 1;
  const line = hasCurveSegment ? straightSvgPath(pts) : '';
  const zeroAxisInView = yMinPct <= 0 && yMaxPct >= 0;
  const areaBaseY = y(clampPct(0));
  const area = hasCurveSegment
    ? `${line} L${pts[pts.length-1][0].toFixed(1)} ${areaBaseY.toFixed(1)} L${pts[0][0].toFixed(1)} ${areaBaseY.toFixed(1)} Z`
    : '';
  const baseY = areaBaseY;
  const lastPt = pts[pts.length - 1];
  const totalPnl = last - initialCash;
  const totalPct = initialCash ? totalPnl / initialCash * 100 : 0;
  const latestDelta = chartDeltas[chartDeltas.length - 1] || 0;
  const latestDeltaPct = chartPcts[chartPcts.length - 1] || 0;
  const delta = latestDelta;
  const deltaPct = latestDeltaPct;
  const dayDelta = last - prev;
  const dayDeltaPct = prev ? (last / prev - 1) * 100 : 0;
  const maxDrawdown = (() => {
    const drawdownVals = hasIntradayOpenBase ? [chartBase, ...vals] : vals;
    let peak = drawdownVals[0], mdd = 0;
    for (const v of drawdownVals) { peak = Math.max(peak, v); mdd = Math.min(mdd, peak ? (v / peak - 1) * 100 : 0); }
    return mdd;
  })();
  const deltaCls = delta >= 0 ? 'up' : 'down';
  const midPct = (yMaxPct + yMinPct) / 2;
  const showMidAxisLabel = Math.abs(midPct) >= 0.08;
  const gridYs = [yMaxPct, midPct, yMinPct].map(y);
  const lastTime = points[points.length - 1].time ? (isDailyMode ? points[points.length - 1].time.slice(0, 10) : points[points.length - 1].time.slice(5,16)) : '';
  const markerLeftPct = (lastPt[0] / w) * 100;
  const markerTopPct = (lastPt[1] / h) * 100;
  const zeroAxisTopPct = (baseY / h) * 100;
  const isUp = latestDelta >= 0;
  const markerColor = isUp ? '#ff4d4f' : '#39d98a';
  const markerGlow = isUp ? 'rgba(255,77,79,.55)' : 'rgba(57,217,138,.55)';
  const timeTickHtml = timeTicks.map((t, idx) => {
    const cls = idx === 0 ? 'start' : (idx === timeTicks.length - 1 ? 'end' : 'mid');
    return `<span class="practice-time-label ${cls}" style="left:${((t.x / w) * 100).toFixed(2)}%">${esc(t.label)}</span>`;
  }).join('');
  const hoverSourcePoints = [
    ...(hasSyntheticOpenAnchor ? [{
      time: `${latestDay} 09:30:00`,
      equity: chartBase,
      x: left,
      y: y(0),
      delta: 0,
      pct: 0,
      dayDelta: 0,
      dayPct: 0,
    }] : []),
    ...points.map((p, i) => {
      const equity = Number(p.equity);
      const previousEquity = i > 0 ? Number(points[i - 1].equity) : Number(hasIntradayOpenBase ? chartBase : initialCash);
      const dayDeltaForPoint = Number.isFinite(equity) && Number.isFinite(previousEquity) ? equity - previousEquity : 0;
      const dayPctForPoint = previousEquity ? (equity / previousEquity - 1) * 100 : 0;
      return {
        time: String(p.time || ''),
        equity,
        x: plottedPts[i]?.[0] ?? xFromTime(p.time),
        y: plottedPts[i]?.[1] ?? y(chartPcts[i] || 0),
        delta: Number(chartDeltas[i] || 0),
        pct: Number(chartPcts[i] || 0),
        dayDelta: dayDeltaForPoint,
        dayPct: dayPctForPoint,
      };
    }),
  ].filter(point => point.time && Number.isFinite(point.equity) && Number.isFinite(point.x) && Number.isFinite(point.y));
  const hoverPoints = [];
  for (const point of hoverSourcePoints) {
    const lastHoverPoint = hoverPoints[hoverPoints.length - 1];
    if (lastHoverPoint && Math.abs(lastHoverPoint.x - point.x) < 0.5) {
      hoverPoints[hoverPoints.length - 1] = point;
    } else {
      hoverPoints.push(point);
    }
  }
  const hoverValueCls = value => Number(value) >= 0 ? 'up' : 'down';
  const hoverItems = hoverPoints.map(point => {
    const xPct = Math.max(0, Math.min(100, point.x / w * 100));
    const yPct = Math.max(0, Math.min(100, point.y / h * 100));
    const timeText = isDailyMode ? point.time.slice(0, 10) : point.time.slice(5, 16);
    const amountText = fmtSignedAmount(point.delta);
    const pctText = fmtSignedPct(point.pct);
    const dayAmountText = fmtSignedAmount(point.dayDelta);
    const dayPctText = fmtSignedPct(point.dayPct);
    const titleText = isDailyMode
      ? `${timeText} 累计金额 ${amountText}，累计收益率 ${pctText}，当日金额 ${dayAmountText}，当日收益率 ${dayPctText}`
      : `${timeText} 收益金额 ${amountText}，收益率 ${pctText}，账户净值 ${fmtAmount(point.equity)}`;
    const rows = isDailyMode
      ? [
        {label:'累计金额', value:amountText, cls:hoverValueCls(point.delta)},
        {label:'累计收益率', value:pctText, cls:hoverValueCls(point.delta)},
        {label:'当日金额', value:dayAmountText, cls:hoverValueCls(point.dayDelta)},
        {label:'当日收益率', value:dayPctText, cls:hoverValueCls(point.dayDelta)},
      ]
      : [
        {label:'收益金额', value:amountText, cls:hoverValueCls(point.delta)},
        {label:'收益率', value:pctText, cls:hoverValueCls(point.delta)},
        {label:'账户净值', value:fmtAmount(point.equity), cls:''},
      ];
    return {xPct, yPct, timeText, ariaLabel:titleText, rows};
  });
  const defaultHoverItem = hoverItems[hoverItems.length - 1] || null;
  const hoverLayerHtml = defaultHoverItem
    ? `<span class="practice-chart-hover-layer" data-practice-hover-points="${esc(JSON.stringify(hoverItems))}" style="--hover-x-pct:${defaultHoverItem.xPct.toFixed(2)}%;--hover-y-pct:${defaultHoverItem.yPct.toFixed(2)}%;--marker-color:${markerColor};--marker-glow:${markerGlow}" aria-label="${esc(defaultHoverItem.ariaLabel)}" onpointerenter="practiceHoverMove(event, this)" onpointermove="practiceHoverMove(event, this)" onpointerdown="practiceHoverMove(event, this)" onpointerup="practiceHoverRelease(event, this)" onpointercancel="practiceHoverRelease(event, this)" onpointerleave="practiceHoverLeave(this)">
      <span class="practice-hover-line"></span>
      <span class="practice-hover-marker"></span>
      <span class="practice-hover-tooltip">${renderPracticeHoverTooltip(defaultHoverItem)}</span>
    </span>`
    : '';
  const tradeMarkerHtml = isDailyMode ? '' : renderPracticeTradeMarkers(latestDay, xFromTime, plottedPts, w, h);
  const chartTitleHtml = renderPracticeChartTitle(
    isDailyMode ? '累积收益曲线' : '今日收益曲线',
    !isDailyMode && isNonTradingCalendarDay && latestDay ? latestDay : '',
    latestDay || targetDate,
  );
  const intradayBaseLabel = hasIntradayOpenBase
    ? `0轴为上一交易日净值(${esc(String(intradayBasePoint.time || '').slice(5, 16))})`
    : '0轴为今日首个净值';
  const chartSub = isDailyMode
    ? `按交易日最后净值计算 · 0轴为起始资金 · 最近点：${esc(lastTime)}`
    : `固定盘面时间轴 09:30-15:00 · ${intradayBaseLabel} · 最近点：${esc(lastTime)}`;
  const primaryKpiLabel = isDailyMode ? '最新总收益' : '当日收益';
  const secondaryKpiLabel = isDailyMode ? '较前日变化' : '累计收益';
  const secondaryKpiPnl = isDailyMode ? dayDelta : totalPnl;
  const secondaryKpiPct = isDailyMode ? dayDeltaPct : totalPct;
  const secondaryKpiCls = secondaryKpiPnl >= 0 ? 'up' : 'down';
  const modeButtons = `<div class="practice-mode-control" aria-label="收益曲线模式">
    <button class="practice-mode-btn ${!isDailyMode ? 'active' : ''}" type="button" onclick="setPracticeCurveMode('intraday')">当日收益</button>
    <button class="practice-mode-btn ${isDailyMode ? 'active' : ''}" type="button" onclick="setPracticeCurveMode('daily')">累计收益</button>
  </div>`;
  const calendarButton = `<button class="practice-calendar-open-btn" type="button" onclick="openPracticeCalendar(event)">交易日历</button>`;
  return `<div class="practice-chart-card">
    <div class="practice-chart-head">
      <div>
        <div class="practice-chart-title-row">
          ${chartTitleHtml}
          ${modeButtons}
          ${calendarButton}
        </div>
        <div class="practice-chart-sub">${chartSub}</div>
        <div class="benchmark-toggle-row"><button class="benchmark-toggle on" type="button" style="--dot:${markerColor}"><span class="benchmark-dot"></span>牛牛账户收益率</button></div>
      </div>
      <div class="practice-chart-kpis">
        <div class="practice-kpi"><div class="practice-kpi-label">${primaryKpiLabel}</div><div class="practice-kpi-value ${deltaCls}">${delta >= 0 ? '+' : ''}${fmtAmount(delta)} / ${deltaPct >= 0 ? '+' : ''}${fmtNumber(deltaPct)}%</div></div>
        <div class="practice-kpi"><div class="practice-kpi-label">${secondaryKpiLabel}</div><div class="practice-kpi-value ${secondaryKpiCls}">${secondaryKpiPnl >= 0 ? '+' : ''}${fmtAmount(secondaryKpiPnl)} / ${secondaryKpiPct >= 0 ? '+' : ''}${fmtNumber(secondaryKpiPct)}%</div></div>
        <div class="practice-kpi"><div class="practice-kpi-label">最大回撤</div><div class="practice-kpi-value down">${fmtNumber(maxDrawdown)}%</div></div>
      </div>
    </div>
    <div class="practice-chart-wrap">
      <span class="practice-axis-label top">${fmtNumber(yMaxPct, yAxis.digits)}%</span>
      ${showMidAxisLabel ? `<span class="practice-axis-label mid">${fmtNumber(midPct, yAxis.digits)}%</span>` : ''}
      <span class="practice-axis-label bot">${fmtNumber(yMinPct, yAxis.digits)}%</span>
      ${zeroAxisInView ? `<span class="practice-zero-axis-label" style="top:${zeroAxisTopPct.toFixed(2)}%">0%</span>` : ''}
      <svg class="practice-chart-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <defs>
          <linearGradient id="practiceFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="${markerColor}" stop-opacity="0.30"/>
            <stop offset="100%" stop-color="${markerColor}" stop-opacity="0.02"/>
          </linearGradient>
          <filter id="practiceGlow" x="-20%" y="-60%" width="140%" height="220%"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
        </defs>
        ${gridYs.map(gy => `<line x1="${left}" x2="${w-right}" y1="${gy.toFixed(1)}" y2="${gy.toFixed(1)}" stroke="rgba(255,255,255,.07)" stroke-dasharray="4 6"/>`).join('')}
        ${timeTicks.map(t => `<line x1="${t.x.toFixed(1)}" x2="${t.x.toFixed(1)}" y1="${top}" y2="${h-bottom}" stroke="rgba(255,255,255,.045)"/>`).join('')}
        ${zeroAxisInView ? `<line x1="${left}" x2="${w-right}" y1="${baseY.toFixed(1)}" y2="${baseY.toFixed(1)}" stroke="rgba(226,232,240,.46)" stroke-width="1.2" stroke-dasharray="7 5"/>` : ''}
        ${hasCurveSegment ? `<path d="${area}" fill="url(#practiceFill)"/>` : ''}
        ${benchmarkPaths.map(b => `<path d="${b.d}" fill="none" stroke="${b.color}" stroke-width="1.5" opacity=".58" vector-effect="non-scaling-stroke"><title>${b.name} ${Number.isFinite(b.lastPct) ? fmtNumber(b.lastPct) + '%' : ''}</title></path>`).join('')}
        ${hasCurveSegment ? `<path d="${line}" fill="none" stroke="${markerColor}" stroke-width="2.2" vector-effect="non-scaling-stroke" filter="url(#practiceGlow)"/>` : ''}
      </svg>
      ${hasCurveSegment ? `<span class="practice-current-line" style="left:${markerLeftPct.toFixed(2)}%"></span>` : ''}
      <span class="practice-current-marker" style="left:${markerLeftPct.toFixed(2)}%;top:${markerTopPct.toFixed(2)}%;--marker-color:${markerColor};--marker-glow:${markerGlow}" title="当前 ${fmtAmount(last)}"></span>
      ${hoverLayerHtml}
      ${tradeMarkerHtml}
      ${timeTickHtml}
    </div>
  </div>`;
}
function practiceCalendarRoot() {
  let root = document.getElementById('practiceCalendarRoot');
  if (!root) {
    root = document.createElement('div');
    root.id = 'practiceCalendarRoot';
    document.body.appendChild(root);
  }
  return root;
}
function monthKeyFromDate(value) {
  const text = String(value || '').slice(0, 10);
  return text.length >= 7 ? text.slice(0, 7) : '';
}
function localDateKey(date=new Date()) {
  return currentDateKey(date);
}
function shiftMonthKey(monthKey, delta) {
  const m = String(monthKey || '').match(/^(\d{4})-(\d{2})$/);
  const base = m ? new Date(Number(m[1]), Number(m[2]) - 1 + delta, 1) : new Date();
  return `${base.getFullYear()}-${String(base.getMonth() + 1).padStart(2, '0')}`;
}
function renderPracticeCalendarDayCurve(date) {
  if (!date) return '';
  const p = niuniuPracticeData || {};
  const initialCash = Number(p.initial_cash || 1000000);
  const history = practiceCalendarHistoryPoints(p);
  const dailyHistory = normalizePracticeEquityPoints(p.daily_equity_history || [])
    .sort((a, b) => (new Date(a.time).getTime() || 0) - (new Date(b.time).getTime() || 0));
  const allDayHistoryPoints = history
    .filter(point => String(point.time || '').slice(0, 10) === date)
    .filter((point, idx, arr) => idx === 0 || String(point.time || '') !== String(arr[idx - 1].time || ''));
  const dailyDayPoints = dailyHistory.filter(point => String(point.time || '').slice(0, 10) === date);
  const sessionDayPoints = allDayHistoryPoints.filter(point => tradingClockMinuteOfDay(point.time) != null);
  const dailyPoints = compactPracticeDailyPoints([...history, ...dailyHistory]);
  const prevPoint = dailyPoints.filter(point => String(point.time || '').slice(0, 10) < date).at(-1);
  const baseEquity = Number(prevPoint?.equity || initialCash);
  const row = buildPracticeCalendarRows(history, dailyHistory, initialCash).find(item => item.date === date);
  const latestEquity = Number(sessionDayPoints.at(-1)?.equity ?? allDayHistoryPoints.at(-1)?.equity ?? dailyDayPoints.at(-1)?.equity ?? row?.equity);
  const finalPnl = Number.isFinite(latestEquity) && Number.isFinite(baseEquity) ? latestEquity - baseEquity : Number(row?.pnl || 0);
  const finalPct = baseEquity ? finalPnl / baseEquity * 100 : Number(row?.pnlPct || 0);
  const valueCls = finalPnl > 0 ? 'up' : finalPnl < 0 ? 'down' : 'flat';
  const signedAmount = `${finalPnl >= 0 ? '+' : ''}${fmtAmount(finalPnl)}`;
  const signedPct = `${finalPct >= 0 ? '+' : ''}${fmtNumber(finalPct)}%`;
  const hasSessionCurve = sessionDayPoints.length >= 2;
  const isCurrentDate = date === String(p.current_date || '');
  const hasPartialHistory = String(p.equity_history_scope || '') !== 'retained_history';
  const needsFullHistory = isCurrentDate || (hasPartialHistory && !practiceCalendarHistoryCoversDate(p, date));
  const historyPending = !hasSessionCurve
    && needsFullHistory
    && practiceFullSnapshotStatus === 'loading';
  const historyLoadFailed = !hasSessionCurve
    && needsFullHistory
    && practiceFullSnapshotStatus === 'error';
  const curveSubPrefix = hasSessionCurve
    ? ''
    : historyPending
      ? '分时加载中 · '
      : historyLoadFailed
        ? '分时加载失败 · '
        : '仅有收盘点 · ';
  const closeBtn = '<button type="button" class="practice-calendar-day-curve-close" data-practice-calendar-action="clear-day" title="关闭曲线" aria-label="关闭曲线">x</button>';
  const head = `<div class="practice-calendar-day-curve-head">
    <div>
      <div class="practice-calendar-day-curve-title">${esc(date.slice(5))} 当日收益曲线</div>
      <div class="practice-calendar-day-curve-sub">${curveSubPrefix}0轴 ${prevPoint ? esc(String(prevPoint.time || '').slice(5, 16)) : '初始资金'}</div>
    </div>
    <div class="practice-calendar-day-curve-value ${valueCls}">${signedAmount} / ${signedPct}</div>
    ${closeBtn}
  </div>`;
  if (historyPending) {
    return `<div class="practice-calendar-day-curve" data-practice-calendar-curve>${head}<div class="practice-calendar-day-curve-empty" aria-live="polite">分时曲线加载中…</div></div>`;
  }
  if (historyLoadFailed) {
    return `<div class="practice-calendar-day-curve" data-practice-calendar-curve>${head}<div class="practice-calendar-day-curve-empty" role="status">分时曲线加载失败</div></div>`;
  }
  let curveSourcePoints = sessionDayPoints;
  if (!hasSessionCurve && Number.isFinite(baseEquity) && baseEquity > 0 && Number.isFinite(latestEquity)) {
    curveSourcePoints = [
      {time: `${date} 09:30:00`, equity: baseEquity, pnlPct: 0},
      {time: `${date} 15:00:00`, equity: latestEquity, pnlPct: finalPct},
    ];
  }
  if (curveSourcePoints.length < 2 || !Number.isFinite(baseEquity) || baseEquity <= 0) {
    return `<div class="practice-calendar-day-curve" data-practice-calendar-curve>${head}<div class="practice-calendar-day-curve-empty">等待当日分时点</div></div>`;
  }
  // Match the wide, fixed-height SVG viewport so preserveAspectRatio does not
  // letterbox the intraday x-axis with large empty gutters on both sides.
  const w = 464, h = 96, left = 8, right = 12, top = 8, bottom = 14;
  const innerW = w - left - right;
  const innerH = h - top - bottom;
  const curvePoints = curveSourcePoints.map(point => {
    const minute = clampedTradingClockMinuteOfDay(point.time);
    const pct = (Number(point.equity) - baseEquity) / baseEquity * 100;
    return {minute, pct};
  }).filter(point => Number.isFinite(point.minute) && Number.isFinite(point.pct));
  if (curvePoints.length < 2) {
    return `<div class="practice-calendar-day-curve" data-practice-calendar-curve>${head}<div class="practice-calendar-day-curve-empty">等待当日分时点</div></div>`;
  }
  const values = curvePoints.map(point => point.pct);
  let minV = Math.min(0, ...values);
  let maxV = Math.max(0, ...values);
  const pad = Math.max((maxV - minV) * 0.12, 0.08);
  minV -= pad;
  maxV += pad;
  const yFor = value => top + (maxV - value) / Math.max(0.0001, maxV - minV) * innerH;
  const xFor = minute => left + Math.max(0, Math.min(240, minute)) / 240 * innerW;
  const path = curvePoints.map((point, idx) => `${idx ? 'L' : 'M'}${xFor(point.minute).toFixed(1)},${yFor(point.pct).toFixed(1)}`).join(' ');
  const lastPoint = curvePoints.at(-1);
  const markerX = xFor(lastPoint.minute).toFixed(1);
  const markerY = yFor(lastPoint.pct).toFixed(1);
  const zeroY = yFor(0).toFixed(1);
  const stroke = finalPnl >= 0 ? '#ff4d4f' : '#39d98a';
  const fill = finalPnl >= 0 ? 'rgba(255,77,79,.13)' : 'rgba(57,217,138,.13)';
  const areaPath = `${path} L${markerX},${h - bottom} L${xFor(curvePoints[0].minute).toFixed(1)},${h - bottom} Z`;
  const plottedCurvePoints = curvePoints.map(point => [xFor(point.minute), yFor(point.pct)]);
  const tradeMarkerHtml = renderPracticeTradeMarkers(
    date,
    time => xFor(clampedTradingClockMinuteOfDay(time)),
    plottedCurvePoints,
    w,
    h,
  );
  return `<div class="practice-calendar-day-curve" data-practice-calendar-curve>${head}
    <div class="practice-calendar-day-curve-chart">
      <svg class="practice-calendar-day-curve-svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(date)} 当日收益曲线">
        <line x1="${left}" y1="${zeroY}" x2="${w - right}" y2="${zeroY}" stroke="rgba(203,213,225,.32)" stroke-width="1" stroke-dasharray="4 5"></line>
        <path d="${areaPath}" fill="${fill}"></path>
        <path d="${path}" fill="none" stroke="${stroke}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></path>
        <circle cx="${markerX}" cy="${markerY}" r="4" fill="#f8fafc" stroke="${stroke}" stroke-width="2"></circle>
        <text x="${left}" y="${h - 2}" fill="#7b8aa0" font-size="9">09:30</text>
        <text x="${left + innerW / 2}" y="${h - 2}" fill="#7b8aa0" font-size="9" text-anchor="middle">11:30</text>
        <text x="${w - right}" y="${h - 2}" fill="#7b8aa0" font-size="9" text-anchor="end">15:00</text>
      </svg>
      ${tradeMarkerHtml}
    </div>
  </div>`;
}
function renderPracticeCalendarModal() {
  const root = practiceCalendarRoot();
  if (!practiceCalendarOpen) {
    root.innerHTML = '';
    return;
  }
  const p = niuniuPracticeData || {};
  const rows = buildPracticeCalendarRows(practiceCalendarHistoryPoints(p), p.daily_equity_history || [], Number(p.initial_cash || 1000000));
  const latestMonth = monthKeyFromDate(rows.at(-1)?.date) || monthKeyFromDate(localDateKey());
  if (!practiceCalendarMonth) practiceCalendarMonth = latestMonth;
  const monthMatch = String(practiceCalendarMonth || latestMonth).match(/^(\d{4})-(\d{2})$/);
  const year = monthMatch ? Number(monthMatch[1]) : new Date().getFullYear();
  const month = monthMatch ? Number(monthMatch[2]) : new Date().getMonth() + 1;
  practiceCalendarMonth = `${year}-${String(month).padStart(2, '0')}`;
  const monthStart = new Date(year, month - 1, 1);
  const daysInMonth = new Date(year, month, 0).getDate();
  const firstWeekday = (monthStart.getDay() + 6) % 7;
  const rowByDate = new Map(rows.map(row => [row.date, row]));
  const monthRows = rows.filter(row => row.date.startsWith(practiceCalendarMonth));
  const monthPnl = monthRows.reduce((sum, row) => sum + (Number(row.pnl) || 0), 0);
  const monthBase = monthRows.length ? monthRows[0].equity - monthRows[0].pnl : Number(p.initial_cash || 0);
  const monthPct = monthBase ? monthPnl / monthBase * 100 : 0;
  const winDays = monthRows.filter(row => Number(row.pnl) > 0).length;
  const lossDays = monthRows.filter(row => Number(row.pnl) < 0).length;
  const flatDays = Math.max(0, monthRows.length - winDays - lossDays);
  const clsFor = value => Number(value) > 0 ? 'up' : Number(value) < 0 ? 'down' : 'flat';
  const signedPct = value => `${Number(value) >= 0 ? '+' : ''}${fmtNumber(value)}%`;
  const signedAmount = value => `${Number(value) >= 0 ? '+' : ''}${fmtAmount(value)}`;
  const signedCellPct = value => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '--';
    const digits = Math.abs(n) >= 1 ? 1 : 2;
    return `${n >= 0 ? '+' : ''}${fmtNumber(n, digits)}%`;
  };
  const signedCellAmount = value => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '--';
    const sign = n >= 0 ? '+' : '';
    const abs = Math.abs(n);
    if (abs >= 10000) return `${sign}${(n / 10000).toFixed(abs >= 100000 ? 1 : 2)}万`;
    if (abs >= 100) return `${sign}${Math.round(n)}`;
    return `${sign}${n.toFixed(1)}`;
  };
  const todayText = localDateKey();
  const cells = [];
  for (let i = 0; i < firstWeekday; i++) cells.push('<div class="practice-calendar-day blank" aria-hidden="true"></div>');
  for (let day = 1; day <= daysInMonth; day++) {
    const date = `${practiceCalendarMonth}-${String(day).padStart(2, '0')}`;
    const row = rowByDate.get(date);
    const valueCls = row ? clsFor(row.pnl) : '';
    const selectedCls = date === practiceCalendarSelectedDate ? 'selected' : '';
    const dateAttr = row ? `data-practice-calendar-date="${esc(date)}"` : '';
    const dayOfWeek = new Date(year, month - 1, day).getDay();
    const isWeekend = dayOfWeek === 0 || dayOfWeek === 6;
    const weekendCls = isWeekend && !row ? 'weekend' : '';
    const isToday = date === todayText;
    const weekendTodayMarker = isToday && isWeekend && !row ? '<span class="practice-calendar-today weekend-today">今</span>' : '';
    const inlineTodayMarker = isToday && !weekendTodayMarker ? '<span class="practice-calendar-today">今</span>' : '';
    const fullText = row ? `${date} ${signedPct(row.pnlPct)} / ${signedAmount(row.pnl)}` : `${date}${isWeekend ? ' 周末' : ''}`;
    cells.push(`<div class="practice-calendar-day ${weekendCls} ${selectedCls} ${row ? `has-result ${valueCls}` : ''}" ${dateAttr} title="${esc(fullText)}" aria-label="${esc(fullText)}">
      <div class="practice-calendar-date"><span>${day}</span>${inlineTodayMarker}</div>
      ${row ? `<div class="practice-calendar-values">
        <div class="practice-calendar-rate ${valueCls}">${signedCellPct(row.pnlPct)}</div>
        <div class="practice-calendar-amount ${valueCls}">${signedCellAmount(row.pnl)}</div>
      </div>` : '<div class="practice-calendar-no-data">--</div>'}
      ${weekendTodayMarker}
    </div>`);
  }
  const selectedCurve = practiceCalendarSelectedDate && practiceCalendarSelectedDate.startsWith(practiceCalendarMonth) && rowByDate.has(practiceCalendarSelectedDate)
    ? renderPracticeCalendarDayCurve(practiceCalendarSelectedDate)
    : '';
  root.innerHTML = `<div class="practice-calendar-popover">
    ${selectedCurve}
    <div class="practice-calendar-card" role="dialog" aria-label="交易日历">
      <div class="practice-calendar-head">
        <div>
          <div class="practice-calendar-title">交易日历 · ${year}年${String(month).padStart(2, '0')}月</div>
          <div class="practice-calendar-sub">${monthRows.length ? `有记录 ${monthRows.length} 天 · 最近 ${esc(monthRows.at(-1).date)}` : '本月暂无收益记录'}</div>
        </div>
        <div class="practice-calendar-actions">
          <button type="button" class="practice-calendar-icon-btn" data-practice-calendar-action="prev" title="上个月" aria-label="上个月">‹</button>
          <button type="button" class="practice-calendar-icon-btn" data-practice-calendar-action="next" title="下个月" aria-label="下个月">›</button>
          <button type="button" class="practice-calendar-icon-btn" data-practice-calendar-action="close" title="关闭" aria-label="关闭">x</button>
        </div>
      </div>
      <div class="practice-calendar-summary">
        <div class="practice-calendar-stat"><div class="practice-calendar-stat-label">本月收益</div><div class="practice-calendar-stat-value ${clsFor(monthPnl)}">${signedAmount(monthPnl)} / ${signedPct(monthPct)}</div></div>
        <div class="practice-calendar-stat"><div class="practice-calendar-stat-label">盈利天数</div><div class="practice-calendar-stat-value up">${winDays}</div></div>
        <div class="practice-calendar-stat"><div class="practice-calendar-stat-label">亏损/持平</div><div class="practice-calendar-stat-value">${lossDays} / ${flatDays}</div></div>
      </div>
      <div class="practice-calendar-grid-wrap">
        <div class="practice-calendar-weekdays">${['一','二','三','四','五','六','日'].map((day, idx) => `<div class="practice-calendar-weekday ${idx >= 5 ? 'weekend' : ''}">${day}</div>`).join('')}</div>
        <div class="practice-calendar-grid">${cells.join('')}</div>
      </div>
    </div>
  </div>`;
}
function openPracticeCalendar(event) {
  if (event && event.stopPropagation) event.stopPropagation();
  const p = niuniuPracticeData || {};
  const rows = buildPracticeCalendarRows(practiceCalendarHistoryPoints(p), p.daily_equity_history || [], Number(p.initial_cash || 1000000));
  practiceCalendarMonth = monthKeyFromDate(rows.at(-1)?.date) || monthKeyFromDate(localDateKey());
  practiceCalendarSelectedDate = '';
  practiceCalendarOpen = true;
  renderPracticeCalendarModal();
}
function closePracticeCalendar() {
  practiceCalendarOpen = false;
  practiceCalendarSelectedDate = '';
  renderPracticeCalendarModal();
}
function shiftPracticeCalendarMonth(delta) {
  practiceCalendarMonth = shiftMonthKey(practiceCalendarMonth, delta);
  practiceCalendarSelectedDate = '';
  renderPracticeCalendarModal();
}
function renderPracticeMarketSummary() {
  const d = practiceMarketSummaryData || {};
  const generating = !!practiceMarketSummaryGenerating;
  const scanCount = Math.max(0, Number(d.scan_count) || 0);
  const usSummaryCount = Math.max(0, Number(d.us_summary_count) || 0);
  const liveSnapshotCount = Math.max(0, Number(d.live_snapshot_count) || 0);
  const previousSummaryCount = Math.max(0, Number(d.previous_summary_count) || 0);
  const sourceParts = [`已有A股总结 ${scanCount} 次`];
  if (usSummaryCount) sourceParts.push(`前日美股 ${usSummaryCount} 份`);
  if (previousSummaryCount) sourceParts.push(`上一版 ${previousSummaryCount} 份`);
  if (liveSnapshotCount) sourceParts.push(`实时快照 ${liveSnapshotCount} 份`);
  const sourceCountText = sourceParts.join(' · ');
  const buttonText = generating ? '正在抓取实时盘面并对比…' : '生成今日盘面总结';
  const statusText = d.loading
    ? '正在读取今日盘面扫描'
    : (scanCount ? `复盘资料：${sourceCountText}` : '今日暂无A股盘面扫描');
  const action = `<div class="practice-market-summary-action">
    <button type="button" class="practice-market-summary-btn" onclick="triggerPracticeMarketSummary()" ${generating ? 'disabled aria-busy="true"' : ''}>${generating ? '⏳ ' : '✨ '}${esc(buttonText)}</button>
    <span>${esc(statusText)}${d.stale ? ' · 有新增扫描，建议重新生成' : ''}</span>
  </div>`;
  const error = d.error ? `<div class="practice-market-summary-error">${esc(d.error)}</div>` : '';
  if (!d.available || !d.summary) return `${action}${error}`;
  const comparisons = Array.isArray(d.comparison_lines) ? d.comparison_lines.filter(Boolean).slice(0, 5) : [];
  const trendLines = Array.isArray(d.trend_lines) ? d.trend_lines.filter(Boolean).slice(0, 5) : [];
  const structureLines = Array.isArray(d.structure_lines) ? d.structure_lines.filter(Boolean).slice(0, 5) : [];
  const risks = Array.isArray(d.risk_lines) ? d.risk_lines.filter(Boolean).slice(0, 4) : [];
  const renderList = (title, items, cls='') => items.length
    ? `<div class="practice-market-summary-section ${cls}"><b>${esc(title)}</b><ul>${items.map(item => `<li>${esc(item)}</li>`).join('')}</ul></div>`
    : '';
  const sourceMode = d.model_used ? '模型综合' : '本地规则汇总';
  const expanded = !!practiceMarketSummaryExpanded;
  return `${action}${error}<section class="practice-market-summary-card ${expanded ? 'open' : 'collapsed'} ${d.stale ? 'stale' : ''}">
    <button type="button" class="practice-market-summary-head" onclick="togglePracticeMarketSummary()" aria-expanded="${expanded ? 'true' : 'false'}">
      <span class="practice-market-summary-title">今日盘面总结 · ${esc(d.tone_label || '中性')}</span>
      <span class="practice-market-summary-compact-meta">${esc(sourceCountText)} · ${esc((d.generated_at || '').slice(5, 16))}</span>
      <span class="practice-market-summary-chevron" aria-hidden="true">›</span>
    </button>
    <div class="practice-market-summary-body"${expanded ? '' : ' hidden'}>
      <p>${esc(d.summary)}</p>
      ${renderList('实时对比结论', comparisons)}
      ${renderList('走势脉络', trendLines)}
      ${renderList('市场结构', structureLines)}
      ${renderList('风险变化', risks, 'risk')}
      <div class="practice-market-summary-meta">汇总 ${esc(sourceCountText)} · ${esc(sourceMode)}${d.live_snapshot_at ? ` · 实时抓取 ${esc(d.live_snapshot_at.slice(11, 19))}` : ''}${d.stale ? ' · 当前结果未包含最新资料' : ''}</div>
    </div>
  </section>`;
}

function togglePracticeMarketSummary() {
  practiceMarketSummaryExpanded = !practiceMarketSummaryExpanded;
  if (activeCategory === 'practice') renderPracticePage();
}

function renderPracticePanel() {
  const p = niuniuPracticeData || {};
  const positions = p.positions || [];
  const soldStocks = p.today_sold_stocks || [];
  const showSoldStocks = practicePositionMode === 'sold';
  const totalEquity = Number(p.total_equity);
  const pnl = Number(p.total_pnl || 0);
  const pnlCls = pnl >= 0 ? 'up' : 'down';
  const BUY_NAMES = {
    trend_pullback: '趋势回踩',
    breakout: '突破确认',
    shaofu_b1: '少妇B1', b2_confirm: 'B2确认',
    b3_accelerate: 'B3中继', super_b1: '超级B1',
    li_daxiao_bottom: '李大霄',
    tide_leader: '主线领航', tide_rotation: '轮动初升', tide_recovery: '冰点修复',
    mixed: '混合买入', unknown_buy: '未识别买入',
    auto_exit: '系统退出', unknown: '其他'
  };
  const EXIT_NAMES = {
    stop_loss: '止损', take_profit: '主动止盈', profit_protection: '回撤保护',
    top_escape: '逃顶/出货', technical_break: '技术破位', sell_score: '卖出评分',
    no_progress: '信号未兑现', position_adjust: '仓位调整', model_sell: '模型卖出',
    sector_retreat: '板块退潮', market_risk: '市场风险',
    other_exit: '其他卖出'
  };
  const dynamicStrategyMeta = (practiceCandidatesData && practiceCandidatesData.strategy_meta) || {};
  for (const [key, meta] of Object.entries(dynamicStrategyMeta)) {
    BUY_NAMES[key] = meta.label || BUY_NAMES[key] || key;
  }
  const splitTags = value => {
    if (Array.isArray(value)) return value.map(x => String(x || '').trim()).filter(Boolean);
    return String(value || '').split(/[，,]/).map(x => x.trim()).filter(Boolean);
  };
  const uniq = values => Array.from(new Set((values || []).filter(Boolean)));
  const inferExitRulesFromReason = reason => {
    const text = String(reason || '');
    const rules = [];
    const add = rule => { if (rule && !rules.includes(rule)) rules.push(rule); };
    if (/止损|破入场止损/.test(text)) add('stop_loss');
    if (/止盈清仓|第一批止盈|卤煮止盈|止盈/.test(text)) add('take_profit');
    if (/峰值回撤|ATR吊灯|移动止损保本|盈转亏/.test(text)) add('profit_protection');
    if (/S1|S2|S3|逃顶|出货五式/.test(text)) add('top_escape');
    if (/卖出评分|防卖飞评分/.test(text)) add('sell_score');
    if (/BBI|白线|死叉|低点跌破|趋势确认失效/.test(text)) add('technical_break');
    if (/未兑现|低效持仓|持仓到期|次日不涨|未延续/.test(text)) add('no_progress');
    return rules;
  };
  const badgeList = labels => labels.length
    ? `<span class="position-reason-badges">${labels.map(label => `<span class="position-reason-badge">${esc(label)}</span>`).join('')}</span>`
    : '';
  const reasonRow = (label, content) => content
    ? `<div class="position-reason-row"><span class="position-reason-label">${esc(label)}</span><span class="position-reason-text">${content}</span></div>`
    : '';
  const positionModeButtons = `<div class="practice-mode-control" aria-label="持仓视图">
    <button class="practice-mode-btn ${!showSoldStocks ? 'active' : ''}" type="button" onclick="setPracticePositionMode('open')">当前持仓${positions.length ? ` ${positions.length}` : ''}</button>
    <button class="practice-mode-btn ${showSoldStocks ? 'active' : ''}" type="button" onclick="setPracticePositionMode('sold')">今日卖出${soldStocks.length ? ` ${soldStocks.length}` : ''}</button>
  </div>`;
  const positionDisplayButtons = `<div class="practice-mode-control" aria-label="持仓显示模式">
    <button class="practice-mode-btn ${!practicePositionBriefMode ? 'active' : ''}" type="button" onclick="setPracticePositionBriefMode(false)">完整</button>
    <button class="practice-mode-btn ${practicePositionBriefMode ? 'active' : ''}" type="button" onclick="setPracticePositionBriefMode(true)">简要</button>
  </div>`;
  const posCards = positions.length ? positions.map(x => {
    const pnlValue = Number(x.pnl);
    const pnlPct = Number(x.pnl_pct);
    const c = !Number.isFinite(pnlValue) ? '#94a3b8' : (pnlValue >= 0 ? '#ff4d4f' : '#39d98a');
    const marketValue = Number(x.market_value);
    const positionPct = Number.isFinite(totalEquity) && totalEquity > 0 && Number.isFinite(marketValue) ? marketValue / totalEquity * 100 : null;
    const positionText = Number.isFinite(positionPct) ? `${fmtNumber(positionPct)}%` : '--';
    const pnlPctText = Number.isFinite(pnlPct) ? `${pnlPct >= 0 ? '+' : ''}${fmtNumber(pnlPct)}%` : '--';
    if (practicePositionBriefMode) {
      return `<div class="position-brief-card">
        <div class="position-brief-name">${esc(x.name || x.code || '--')}</div>
        <div class="position-brief-stats">
          <div class="position-brief-item"><span>仓位</span><b>${positionText}</b></div>
          <div class="position-brief-item"><span>盈亏</span><b style="color:${c}">${pnlPctText}</b></div>
        </div>
      </div>`;
    }
    const changePct = Number(x.change_pct);
    const todayPnl = Number(x.today_pnl);
    const todayPct = Number(x.today_pnl_pct ?? x.change_pct);
    const dayLowPct = Number(x.day_low_pct);
    const dayHighPct = Number(x.day_high_pct);
    const dayColor = Number.isFinite(todayPnl) ? (todayPnl >= 0 ? '#ff4d4f' : '#39d98a') : '#94a3b8';
    const changeColor = Number.isFinite(changePct) ? (changePct >= 0 ? '#ff4d4f' : '#39d98a') : '#94a3b8';
    const changeText = Number.isFinite(changePct) ? `${changePct >= 0 ? '+' : ''}${fmtNumber(changePct)}%` : '--';
    const lowColor = Number.isFinite(dayLowPct) ? (dayLowPct >= 0 ? '#ff4d4f' : '#39d98a') : '#94a3b8';
    const highColor = Number.isFinite(dayHighPct) ? (dayHighPct >= 0 ? '#ff4d4f' : '#39d98a') : '#94a3b8';
    const lowText = Number.isFinite(dayLowPct) ? `${dayLowPct >= 0 ? '+' : ''}${fmtNumber(dayLowPct)}%` : '--';
    const highText = Number.isFinite(dayHighPct) ? `${dayHighPct >= 0 ? '+' : ''}${fmtNumber(dayHighPct)}%` : '--';
    const todayText = Number.isFinite(todayPnl)
      ? `${todayPnl >= 0 ? '+' : ''}${fmtAmount(todayPnl)}${Number.isFinite(todayPct) ? ` / ${todayPct >= 0 ? '+' : ''}${fmtNumber(todayPct)}%` : ''}`
      : '--';
    const costPriceText = `${fmtNumber(x.avg_cost)} / ${fmtNumber(x.last_price)}`;
    const pnlText = Number.isFinite(pnlValue)
      ? `${pnlValue >= 0 ? '+' : ''}${fmtAmount(pnlValue)}${Number.isFinite(pnlPct) ? ` / ${pnlPct >= 0 ? '+' : ''}${fmtNumber(pnlPct)}%` : ''}`
      : '--';
    const availableHoldText = `${x.available_qty ?? 0} / ${x.qty ?? 0}`;
    const buyStrategyLabels = uniq(splitTags(x.buy_strategy).map(key => BUY_NAMES[key] || key));
    const buyReasonText = String(x.entry_reason || x.buy_reason || '').trim();
    const buyReasonBlock = x.bought_today && (buyStrategyLabels.length || buyReasonText)
      ? `<div class="position-reason-block">
          ${reasonRow('买入策略', badgeList(buyStrategyLabels))}
          ${reasonRow('买入理由', esc(buyReasonText))}
        </div>`
      : '';
    return `<div class="position-card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <span style="font-weight:700;font-size:16px;color:#f8fafc">${esc(x.code)} ${esc(x.name||'')}</span>
      </div>
      <div class="position-metrics">
        <div class="position-metric"><div class="position-label">成本/现价</div><div class="position-value combo">${costPriceText}</div></div>
        <div class="position-metric"><div class="position-label">盈亏</div><div class="position-value strong combo" style="color:${c}">${pnlText}</div></div>
        <div class="position-metric"><div class="position-label">实时涨幅</div><div class="position-value strong" style="color:${changeColor}">${changeText}</div></div>
        <div class="position-metric"><div class="position-label">最低/最高</div><div class="position-value strong combo"><span style="color:${lowColor}">${lowText}</span><span style="color:#64748b">/</span><span style="color:${highColor}">${highText}</span></div></div>
        <div class="position-metric"><div class="position-label">今日收益</div><div class="position-value strong" style="color:${dayColor}">${todayText}</div></div>
        <div class="position-metric"><div class="position-label">市值</div><div class="position-value">${fmtAmount(x.market_value)}</div></div>
        <div class="position-metric"><div class="position-label">仓位占比</div><div class="position-value">${positionText}</div></div>
        <div class="position-metric"><div class="position-label">可卖/持有</div><div class="position-value" style="color:#94a3b8">${availableHoldText}</div></div>
      </div>
      ${buyReasonBlock}
    </div>`;
  }).join('') : '<div class="empty" style="padding:18px;font-size:13px">暂无持仓，等待模型决策建仓</div>';
  const soldCards = soldStocks.length ? soldStocks.map(x => {
    const realized = Number(x.realized_pnl);
    const realizedPct = Number(x.realized_pnl_pct);
    const afterSellPnl = Number(x.after_sell_pnl);
    const afterSellPct = Number(x.change_after_sell_pct);
    const currentChangePct = Number(x.current_change_pct);
    const realizedColor = Number.isFinite(realized) ? (realized >= 0 ? '#ff4d4f' : '#39d98a') : '#94a3b8';
    const afterColor = Number.isFinite(afterSellPnl) ? (afterSellPnl > 0 ? '#f59e0b' : (afterSellPnl < 0 ? '#34d399' : '#94a3b8')) : '#94a3b8';
    const currentColor = Number.isFinite(currentChangePct) ? (currentChangePct >= 0 ? '#ff4d4f' : '#39d98a') : '#94a3b8';
    const realizedText = Number.isFinite(realized)
      ? `${realized >= 0 ? '+' : ''}${fmtAmount(realized)}${Number.isFinite(realizedPct) ? ` / ${realizedPct >= 0 ? '+' : ''}${fmtNumber(realizedPct)}%` : ''}`
      : '--';
    const afterText = Number.isFinite(afterSellPnl)
      ? `${afterSellPnl >= 0 ? '+' : ''}${fmtAmount(afterSellPnl)}${Number.isFinite(afterSellPct) ? ` / ${afterSellPct >= 0 ? '+' : ''}${fmtNumber(afterSellPct)}%` : ''}`
      : '--';
    const currentChangeText = Number.isFinite(currentChangePct) ? `${currentChangePct >= 0 ? '+' : ''}${fmtNumber(currentChangePct)}%` : '--';
    const observation = Number.isFinite(afterSellPnl)
      ? (afterSellPnl > 0 ? '卖出后上涨' : (afterSellPnl < 0 ? '卖出后回落' : '卖出后持平'))
      : '等待行情';
    const priceText = `${fmtNumber(x.avg_sell_price)} / ${x.current_price == null ? '--' : fmtNumber(x.current_price)}`;
    const sellReasonText = String(x.reason || '').trim();
    const rawExitRules = Array.isArray(x.exit_rules) && x.exit_rules.length ? x.exit_rules : x.exit_rule;
    const exitRuleKeys = splitTags(rawExitRules);
    const exitRuleLabels = uniq((exitRuleKeys.length ? exitRuleKeys : inferExitRulesFromReason(sellReasonText)).map(key => EXIT_NAMES[key] || key));
    const sellReasonBlock = (exitRuleLabels.length || sellReasonText)
      ? `<div class="position-reason-block">
          ${reasonRow('卖出归因', badgeList(exitRuleLabels))}
          ${reasonRow('卖出理由', esc(sellReasonText))}
        </div>`
      : '';
    return `<div class="position-card">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:8px">
        <span style="font-weight:700;font-size:16px;color:#f8fafc">${esc(x.code)} ${esc(x.name||'')}</span>
        <span style="font-size:13px;color:#94a3b8">${esc(x.shares)}股 · ${esc((x.last_sell_time||'').slice(11,16))}</span>
      </div>
      <div class="position-metrics">
        <div class="position-metric"><div class="position-label">卖出/现价</div><div class="position-value combo">${priceText}</div></div>
        <div class="position-metric"><div class="position-label">已实现盈亏</div><div class="position-value strong combo" style="color:${realizedColor}">${realizedText}</div></div>
        <div class="position-metric"><div class="position-label">卖后变化</div><div class="position-value strong combo" style="color:${afterColor}">${afterText}</div></div>
        <div class="position-metric"><div class="position-label">观察</div><div class="position-value strong" style="color:${afterColor}">${observation}</div></div>
        <div class="position-metric"><div class="position-label">实时涨幅</div><div class="position-value strong" style="color:${currentColor}">${currentChangeText}</div></div>
        <div class="position-metric"><div class="position-label">卖出金额</div><div class="position-value">${fmtAmount(x.sell_amount)}</div></div>
        <div class="position-metric"><div class="position-label">到账金额</div><div class="position-value">${fmtAmount(x.net_proceeds)}</div></div>
        <div class="position-metric"><div class="position-label">费用</div><div class="position-value" style="color:#94a3b8">${fmtAmount(x.fee)}</div></div>
      </div>
      ${sellReasonBlock}
    </div>`;
  }).join('') : '<div class="empty" style="padding:18px;font-size:13px">今日暂无卖出股票</div>';
  const stockCards = showSoldStocks ? soldCards : posCards;
  const stockCardsClass = !showSoldStocks && positions.length && practicePositionBriefMode ? 'position-brief-grid' : 'position-card-list';
  const operationLog = renderPracticeOperationLog(p);
  const logDetailModal = renderPracticeLogDetailModal(p);
  const quote = p.last_quote_refresh || {};
  const channels = quote.channel_counts || {};
  const ruleNote = p.trade_rule_note || practiceRuleFallbackNote();
  const ruleModal = renderPracticeRuleNoteModal(ruleNote);
  const channelText = quote.quote_time ? `腾讯${channels.tencent ?? 0}/东财${channels.eastmoney ?? 0}/Sina${channels.sina ?? 0}` : '';
  const singleRetryCount = Math.max(0, Math.trunc(Number(channels.single) || 0));
  const singleRetryText = singleRetryCount ? `，单股重试${singleRetryCount}只` : '';
  const quoteNote = quote.quote_time ? `行情：${esc(quote.quote_time)} 更新${quote.updated ?? 0}只 ${channelText}${singleRetryText}${quote.fallback ? `，回退${quote.fallback}只` : ''}` : '';
  const decisionModel = String(p.decision_model || '').trim();
  const missingModelLabel = practiceFullSnapshotStatus === 'error' ? '未知' : '加载中';
  const ruleMeta = [`模型：${esc(decisionModel || missingModelLabel)}`, quoteNote].filter(Boolean).join('｜');
  const manualCycle = practiceManualCycleData || {};
  const manualRunning = !!manualCycle.running;
  const manualButtonText = manualRunning ? (manualCycle.stage_label || '本轮执行中…') : '手动触发选股及买卖策略';
  const marketContext = p.market_decision_context || {};
  const marketGuidance = Array.isArray(marketContext.guidance_lines) ? marketContext.guidance_lines.slice(0, 2) : [];
  const marketEvaluation = marketContext.available || marketContext.tone_label
    ? `<div class="practice-market-evaluation"><span class="practice-market-evaluation-label">盘面评价 · ${esc(marketContext.tone_label || '中性')}</span><span>${esc(marketGuidance.join('；') || marketContext.source_title || '已更新')}</span><time>${esc((marketContext.source_time || marketContext.context_as_of || '').slice(5, 16))}</time></div>`
    : '';
  return `<section class="sector-cloud" style="margin-bottom:18px">
    <div class="practice-account-head">
      <h3>模拟账户</h3>
      <button type="button" class="practice-manual-cycle-btn" onclick="triggerPracticeManualCycle()" ${manualRunning ? 'disabled aria-busy="true"' : ''}>${manualRunning ? '⏳ ' : '▶ '}${esc(manualButtonText)}</button>
    </div>
    ${marketEvaluation}
    ${renderPracticeMarketSummary()}
    ${manualCycle.error ? `<div class="practice-manual-cycle-error">本轮执行失败：${esc(manualCycle.error)}</div>` : ''}
    ${p.trading_paused ? `<div style=\"background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.35);border-radius:12px;padding:10px 14px;margin:10px 0;display:flex;justify-content:space-between;align-items:center\">
      <span style=\"color:#fbbf24;font-size:13px\">⏸️ 交易已暂停：${esc(p.pause_reason||'风控触发')}（${esc((p.pause_since||'').slice(11,16))}起）</span>
      <button onclick=\"actionFetch('/api/niuniu_practice/resume').then(r=>r.json()).then(d=>{if(d.resumed)location.reload()})\" style=\"background:rgba(52,211,153,.18);color:#34d399;border:1px solid rgba(52,211,153,.35);border-radius:8px;padding:6px 12px;cursor:pointer;font-size:12px;font-weight:600\">🔄 强制恢复交易</button>
    </div>` : ''}
    <div class="practice-stats" style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:12px 0">
      <div class="inline-field"><div class="inline-label">初始资金</div><div class="inline-value">${fmtAmount(p.initial_cash)}</div></div>
      <div class="inline-field"><div class="inline-label">总权益</div><div class="inline-value">${fmtAmount(p.total_equity)}</div></div>
      <div class="inline-field"><div class="inline-label">现金</div><div class="inline-value">${fmtAmount(p.cash)}</div></div>
      <div class="inline-field"><div class="inline-label">累计收益</div><div class="inline-value ${pnlCls}">${fmtAmount(p.total_pnl)} / ${fmtNumber(p.total_pnl_pct)}%</div></div>
    </div>
    <div>${renderPracticeCurve(p.equity_history || [], p.daily_equity_history || [], Number(p.initial_cash || 1000000), practiceBenchmarksData || {items:[]})}</div>
    <div style="display:flex;align-items:center;justify-content:flex-start;gap:12px;flex-wrap:wrap;margin:12px 0 8px">
      ${positionModeButtons}
      ${!showSoldStocks ? positionDisplayButtons : ''}
    </div>
    <div class="${stockCardsClass}">${stockCards}</div>
    ${operationLog}
    ${logDetailModal}
    <div class="practice-rule-row">
      <button type="button" class="practice-rule-btn" data-practice-rule-action="open">交易规则</button>
      <span class="practice-rule-meta">${ruleMeta}</span>
    </div>
    ${ruleModal}
    ${p.last_error ? `<div class="empty" style="color:#f87171;margin-top:10px">模型/交易错误：${esc(p.last_error)}</div>` : ''}
  </section>`;
}

function setIndicesViewMode(mode) {
  indicesViewMode = mode === 'market' ? 'market' : 'index';
  syncViewUrl();
  if (activeCategory === 'indices') render();
  saveViewState();
}

function setIndicesMarketRegion(mode) {
  if (!['a_share', 'us'].includes(mode)) return;
  indicesMarketRegionOverride = mode;
  if (activeCategory === 'indices' && indicesViewMode === 'market') render();
}

function setIndicesIndexPriority(mode) {
  if (!['a_share', 'us'].includes(mode)) return;
  indicesIndexPriorityOverride = mode;
  try { sessionStorage.setItem(INDICES_INDEX_PRIORITY_STATE_KEY, mode); } catch (e) {}
  if (activeCategory === 'indices' && indicesViewMode === 'index') render();
}

function resolvedIndicesIndexPriority(aIndexItems = []) {
  if (indicesIndexPriorityOverride) return indicesIndexPriorityOverride;
  return indicesSwitchSession(aIndexItems) === 'a_share' ? 'a_share' : 'us';
}

function resolvedIndicesMarketRegion(aIndexItems = []) {
  if (indicesMarketRegionOverride) return indicesMarketRegionOverride;
  return indicesSwitchSession(aIndexItems) === 'a_share' ? 'a_share' : 'us';
}

function renderIndicesPanel() {
  const idx = indicesData;
  const items = idx.items || [];
  const hot = hotStocksData;
  const sec = sectorData;
  const sectors = sec.sectors || sec.items || [];
  const mf = moneyFlowData;
  const errorHtml = idx.error ? `<div class="empty" style="color:#f87171;margin-bottom:12px">指数接口错误：${esc(idx.error)}</div>` : '';
  if (!items.length && !idx.error) {
    return '<div class="loading">行情加载中...</div>';
  }
  function trendClass(item) {
    const c = Number(item.change_pct);
    return c > 0 ? 'index-up' : c < 0 ? 'index-down' : 'index-flat';
  }
  function fmtChange(item) {
    if (item.change_pct == null) return '';
    const c = Number(item.change_pct);
    const sign = c > 0 ? '+' : '';
    return `<div class="index-change ${trendClass(item)}">${sign}${fmtNumber(item.change_pct,2)}%</div>`;
  }
  function renderFlowBlock(title, list, isInflow) {
    return `<h3>${title}</h3><div class="sector-grid">${list.map(s => {
      const cls = isInflow ? 'up' : 'down';
      const sign = Number(s.pct) > 0 ? '+' : '';
      const flowSign = Number(s.net_flow_yi ?? 0) > 0 ? '+' : '';
      const flowCls = isInflow ? 'flow-in' : 'flow-out';
      const bg = isInflow ? 'rgba(127,29,29,.28)' : 'rgba(6,78,59,.28)';
      const border = isInflow ? 'rgba(248,113,113,.22)' : 'rgba(52,211,153,.22)';
      return `<div class="hot-item ${cls}" style="position:relative;background:${bg};border-color:${border}"><div class="sector-name">${esc(s.name)}</div><div class="hot-price">${fmtNumber(s.price)}</div><div class="sector-pct">${sign}${fmtNumber(s.pct)}% <span class="flow-val ${flowCls}">${flowSign}${fmtNumber(s.net_flow_yi,2)}亿</span></div></div>`;
    }).join('')}</div>`;
  }
  let mfHtml = '';
  if (mf.inflow && mf.inflow.length && mf.outflow && mf.outflow.length) {
    mfHtml = `<div class="sector-cloud"><h3 style="display:flex;align-items:center;gap:12px;flex-wrap:wrap"><span>主力资金流向</span></h3><div style="display:flex;gap:16px;flex-wrap:wrap"><div style="flex:1;min-width:260px">${renderFlowBlock('主力净流入前十', mf.inflow, true)}</div><div style="flex:1;min-width:260px">${renderFlowBlock('主力净流出前十', mf.outflow, false)}</div></div></div>`;
  }
  function renderMarketFlowBlock() {
    const mf = marketFlowData;
    if (mf.total_inflow_yi == null) return '';
    if (!Number(mf.total_inflow_yi) && !Number(mf.total_outflow_yi) && !Number(mf.net_flow_yi)) return '';
    const netCls = Number(mf.net_flow_yi) > 0 ? 'up' : Number(mf.net_flow_yi) < 0 ? 'down' : 'flat';
    const sign = Number(mf.net_flow_yi) > 0 ? '+' : '';
    return `<div class="sector-cloud"><h3>大盘资金流向</h3><div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:10px">
      <div style="flex:1;min-width:120px;text-align:center;padding:8px 12px;background:#111;border-radius:8px"><div style="font-size:12px;color:#999">总流入</div><div style="font-size:18px;color:#e74c3c;font-weight:bold">${fmtAmount(mf.total_inflow)}</div><div style="font-size:11px;color:#666">${fmtNumber(mf.total_inflow_yi, 0)}亿</div></div>
      <div style="flex:1;min-width:120px;text-align:center;padding:8px 12px;background:#111;border-radius:8px"><div style="font-size:12px;color:#999">总流出</div><div style="font-size:18px;color:#2ecc71;font-weight:bold">${fmtAmount(mf.total_outflow)}</div><div style="font-size:11px;color:#666">${fmtNumber(mf.total_outflow_yi, 0)}亿</div></div>
      <div style="flex:1;min-width:120px;text-align:center;padding:8px 12px;background:#111;border-radius:8px"><div style="font-size:12px;color:#999">净流入</div><div style="font-size:18px;color:${netCls === 'up' ? '#e74c3c' : '#2ecc71'};font-weight:bold">${sign}${fmtAmount(mf.net_flow)}</div><div style="font-size:11px;color:#666">${sign}${fmtNumber(mf.net_flow_yi, 0)}亿</div></div>
    </div></div>`;
  }
  function renderIndexGroup(title, list) {
    if (!list || !list.length) return '';
    return `<div style="margin-bottom:18px"><h3 style="margin:0 0 10px;color:#c7d2fe;font-size:15px">${title}</h3><section class="market-strip">${list.map(item => `
      <article class="index-card ${trendClass(item)}">
        <div class="index-name">${esc(item.name)}</div>
        <div class="index-price">${fmtNumber(item.price)}</div>
        ${fmtChange(item)}
        ${renderSparkline(item.sparkline, item)}
        <div class="index-time">${esc(item.time || '')}</div>
      </article>
    `).join('')}</section></div>`;
  }
  function legacyMarketType(item) {
    const key = String(item.key || '');
    const code = String(item.code || '');
    const name = String(item.name || '');
    if (item.market_type) return item.market_type;
    if (key === 'a50_fut' || code === 'hf_CHA50CFD' || /A50|富时中国/.test(name)) return 'a_futures';
    if (/_fut$/.test(key) || /期货/.test(name)) return 'us_futures';
    if (['dow', 'nas', 'spx'].includes(key) || /^us/.test(code)) return 'us_index';
    if (key === 'xau' || key === 'brent' || /黄金|伦敦金|原油/.test(name)) return 'commodity';
    if (item.group === 'domestic' || /^s[hz]/.test(code)) return 'a_index';
    return item.group || '';
  }
  function marketItems(type, fallbackGroup = '') {
    const grouped = idx.market_groups?.[type];
    if (Array.isArray(grouped) && grouped.length) return grouped;
    return items.filter(x => legacyMarketType(x) === type || (fallbackGroup && x.group === fallbackGroup && legacyMarketType(x) === type));
  }
  const aIndexItems = marketItems('a_index', 'domestic');
  const usIndexItems = marketItems('us_index', 'global');
  function renderSessionMarketGroups() {
    const session = indicesSwitchSession(aIndexItems);
    const indexPriority = resolvedIndicesIndexPriority(aIndexItems);
    const indexSections = indexPriority === 'a_share' ? [
      ['A股指数', aIndexItems],
      ['美股指数', usIndexItems],
    ] : [
      ['美股指数', usIndexItems],
      ['A股指数', aIndexItems],
    ];
    const supportingSections = session === 'us_open' ? [
      ['A股期货', marketItems('a_futures')],
      ['大宗商品', marketItems('commodity', 'commodity')],
    ] : [
      ['A股期货', marketItems('a_futures')],
      ['美股期货', marketItems('us_futures')],
      ['大宗商品', marketItems('commodity', 'commodity')],
    ];
    return [...indexSections, ...supportingSections].map(([title, list]) => renderIndexGroup(title, list)).join('');
  }
  function renderRankBlock(title, list, mode) {
    if (!list || !list.length) return '';
    return `<div style="flex:1;min-width:250px"><h3>${title}</h3><div class="sector-grid">${list.slice(0,10).map(s => {
      const cls = Number(s.pct) > 0 ? 'up' : Number(s.pct) < 0 ? 'down' : 'flat';
      const sign = Number(s.pct) > 0 ? '+' : '';
      const sub = mode === 'turnover' ? `换手 ${fmtNumber(s.turnover,2)}%` : mode === 'volume' ? `量 ${fmtNumber((s.volume_lot||0)/10000,1)}万手` : `额 ${fmtNumber(s.amount_yi,2)}亿`;
      return `<div class="hot-item ${cls}"><div class="sector-name">${esc(s.code)} ${esc(s.name||'')}</div><div class="hot-price">${fmtNumber(s.price)}</div><div class="sector-pct">${sign}${fmtNumber(s.pct)}% <span class="flow-val">${sub}</span></div></div>`;
    }).join('')}</div></div>`;
  }
  let hotHtml = '';
  if ((hot.amount_top && hot.amount_top.length) || (hot.turnover_top && hot.turnover_top.length) || (hot.volume_top && hot.volume_top.length)) {
    hotHtml = `<div class="sector-cloud"><h3>活跃股票榜</h3><div style="display:flex;gap:16px;flex-wrap:wrap">${renderRankBlock('成交额前十', hot.amount_top || hot.items || [], 'amount')}${renderRankBlock('换手率前十', hot.turnover_top || [], 'turnover')}${renderRankBlock('成交量前十', hot.volume_top || [], 'volume')}</div></div>`;
  } else if (hot.items && hot.items.length) {
    const items = hot.items.slice(0, 12);
    hotHtml = `<div class="sector-cloud"><h3>热搜股票</h3><div class="sector-grid">${items.map(s => {
      const cls = Number(s.pct) > 0 ? 'up' : Number(s.pct) < 0 ? 'down' : 'flat';
      const sign = Number(s.pct) > 0 ? '+' : '';
      return `<div class="hot-item ${cls}"><div class="sector-name">${esc(s.code)} ${esc(s.name||'')}</div><div class="hot-price">${fmtNumber(s.price)}</div><div class="sector-pct">${sign}${fmtNumber(s.pct)}%</div></div>`;
    }).join('')}</div></div>`;
  }
  let cloudHtml = '';
  const gainTop = sec.gain_top || sectors.slice(0, 10);
  const lossTop = sec.loss_top || [];
  function renderSectorCloudHeading(source) {
    const sourceMeta = source && source.generated_at ? `<span class="flow-val">更新 ${esc(source.generated_at)}</span>` : '';
    return `<h3>板块涨跌幅 ${sourceMeta}</h3>`;
  }
  function renderSectorMoveBlock(title, list, isGain) {
    if (!list || !list.length) return '';
    return `<h3>${title}</h3><div class="sector-grid">${list.slice(0,10).map(s => {
      const pct = Number(s.pct || 0);
      const sign = pct > 0 ? '+' : '';
      const cls = isGain ? 'up' : 'down';
      return `<div class="sector-item ${cls}"><div class="sector-name">${esc(s.name)}</div><div class="sector-pct">${sign}${fmtNumber(s.pct)}%</div></div>`;
    }).join('')}</div>`;
  }
  if (gainTop.length || lossTop.length) {
    cloudHtml = `<div class="sector-cloud">${renderSectorCloudHeading(sec)}<div style="display:flex;gap:16px;flex-wrap:wrap"><div style="flex:1;min-width:260px">${renderSectorMoveBlock('涨幅前十', gainTop, true)}</div><div style="flex:1;min-width:260px">${renderSectorMoveBlock('跌幅前十', lossTop, false)}</div></div></div>`;
  }
  function normalizedUsSectorRows() {
    return ((usSectorData && usSectorData.items) || []).map(row => {
      const pct = Number(row.change_pct);
      return {
        ...row,
        name: row.label || row.name || row.symbol || '',
        pct: Number.isFinite(pct) ? pct : null,
      };
    }).filter(row => row.name);
  }
  function renderUsSectorMoveBlock(title, list, fallbackTone) {
    if (!list || !list.length) {
      const emptyText = fallbackTone === 'up' ? '暂无上涨板块' : '暂无下跌板块';
      return `<h3>${title}</h3><div class="empty" style="padding:18px">${emptyText}</div>`;
    }
    return `<h3>${title}</h3><div class="sector-grid">${list.slice(0,10).map(s => {
      const pct = Number(s.pct);
      const cls = Number.isFinite(pct) ? (pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat') : fallbackTone;
      const sign = Number.isFinite(pct) && pct > 0 ? '+' : '';
      const mapping = Array.isArray(s.a_share_mapping) && s.a_share_mapping.length ? s.a_share_mapping.slice(0, 3).join('、') : (s.kind === 'theme' ? '主题ETF' : '行业ETF');
      const symbol = s.symbol ? `${s.symbol} · ` : '';
      const priceText = `${symbol}${Number.isFinite(Number(s.price)) ? fmtNumber(s.price) : '--'}`;
      const pctText = Number.isFinite(pct) ? `${sign}${fmtNumber(pct)}%` : '--';
      const titleText = `${s.name || ''} ${priceText} ${pctText} ${mapping}`.trim();
      return `<div class="hot-item us-sector-card ${cls}" title="${esc(titleText)}"><div class="sector-name">${esc(s.name)}</div><div class="hot-price">${esc(priceText)}</div><div class="sector-pct">${esc(pctText)}</div><div class="us-sector-map">${esc(mapping)}</div></div>`;
    }).join('')}</div>`;
  }
  function renderUsSectorMarketBlock() {
    const rows = normalizedUsSectorRows();
    if (!rows.length) {
      const text = usSectorData && usSectorData.error ? `美股板块行情暂不可用：${esc(usSectorData.error)}` : '美股板块行情加载中...';
      return `<div class="sector-cloud">${renderSectorCloudHeading(usSectorData)}<div class="empty" style="padding:18px">${text}</div></div>`;
    }
    const gainRows = rows.filter(row => Number.isFinite(row.pct) && row.pct > 0).sort((a, b) => b.pct - a.pct);
    const lossRows = rows.filter(row => Number.isFinite(row.pct) && row.pct < 0).sort((a, b) => a.pct - b.pct);
    return `<div class="sector-cloud us-sector-cloud">${renderSectorCloudHeading(usSectorData)}<div class="sector-columns"><div class="sector-column">${renderUsSectorMoveBlock('涨幅前十', gainRows, 'up')}</div><div class="sector-column">${renderUsSectorMoveBlock('跌幅前十', lossRows, 'down')}</div></div></div>`;
  }
  const indexHtml = renderSessionMarketGroups();
  const marketFlowHtml = renderMarketFlowBlock();
  const aShareMarketHtml = `${cloudHtml}${hotHtml}${marketFlowHtml}${mfHtml}`;
  const marketRegion = resolvedIndicesMarketRegion(aIndexItems);
  const marketUsesUsSectors = marketRegion === 'us';
  const marketHtml = marketUsesUsSectors ? renderUsSectorMarketBlock() : aShareMarketHtml;
  const usSectorCount = normalizedUsSectorRows().length;
  const marketModuleCount = marketUsesUsSectors ? usSectorCount : [cloudHtml, hotHtml, marketFlowHtml, mfHtml].filter(Boolean).length;
  const hasMarketPayload =
    marketUsesUsSectors ? usSectorCount > 0 : (
      ['gain_top', 'loss_top', 'sectors', 'items'].some(key => Array.isArray(sec[key])) ||
      ['amount_top', 'turnover_top', 'volume_top', 'items'].some(key => Array.isArray(hot[key])) ||
      ['inflow', 'outflow'].some(key => Array.isArray(mf[key]))
  );
  const activePanel = indicesViewMode === 'market' ? 'market' : 'index';
  const activeTitleHtml = activePanel === 'index' ? '<h2 class="indices-part-title">指数</h2>' : '';
  const activeMeta = activePanel === 'market' ? `${marketModuleCount || 0} ${marketUsesUsSectors ? '项' : '组'}` : `${items.length} 项`;
  const indexPriority = resolvedIndicesIndexPriority(aIndexItems);
  const indexPrioritySwitchHtml = activePanel === 'index' ? `
    <div class="market-region-switch index-priority-switch" role="group" aria-label="指数排序切换" title="${indicesIndexPriorityOverride ? '当前为手动排序' : '当前按交易时段自动排序'}">
      <button type="button" class="market-region-btn ${indexPriority === 'a_share' ? 'active' : ''}" data-index-priority="a_share" aria-pressed="${indexPriority === 'a_share' ? 'true' : 'false'}" onclick="setIndicesIndexPriority('a_share')">A股在上</button>
      <button type="button" class="market-region-btn ${indexPriority === 'us' ? 'active' : ''}" data-index-priority="us" aria-pressed="${indexPriority === 'us' ? 'true' : 'false'}" onclick="setIndicesIndexPriority('us')">美股在上</button>
    </div>` : '';
  const marketRegionSwitchHtml = activePanel === 'market' ? `
    <div class="market-region-switch" role="group" aria-label="行情市场切换" title="${indicesMarketRegionOverride ? '当前为手动选择' : '当前按交易时段自动选择'}">
      <button type="button" class="market-region-btn ${marketRegion === 'a_share' ? 'active' : ''}" data-market-region="a_share" aria-pressed="${marketRegion === 'a_share' ? 'true' : 'false'}" onclick="setIndicesMarketRegion('a_share')">A股</button>
      <button type="button" class="market-region-btn ${marketRegion === 'us' ? 'active' : ''}" data-market-region="us" aria-pressed="${marketRegion === 'us' ? 'true' : 'false'}" onclick="setIndicesMarketRegion('us')">美股</button>
    </div>` : '';
  const activeHtml = activePanel === 'market'
    ? (marketHtml || `<div class="empty" style="padding:18px">${hasMarketPayload ? '暂无行情数据' : '行情加载中...'}</div>`)
    : (indexHtml || '<div class="empty" style="padding:18px">暂无指数数据</div>');
  return `${errorHtml}<div class="indices-page">
    <div class="indices-switch" role="group" aria-label="指数行情切换">
      <button type="button" class="indices-switch-btn ${activePanel === 'index' ? 'active' : ''}" aria-pressed="${activePanel === 'index' ? 'true' : 'false'}" onclick="setIndicesViewMode('index')">指数</button>
      <button type="button" class="indices-switch-btn ${activePanel === 'market' ? 'active' : ''}" aria-pressed="${activePanel === 'market' ? 'true' : 'false'}" onclick="setIndicesViewMode('market')">行情</button>
    </div>
    <section class="indices-part" id="${activePanel === 'market' ? 'market-overview' : 'indices-overview'}">
      <div class="indices-part-head"><div class="indices-part-title-row">${activeTitleHtml}${indexPrioritySwitchHtml}${marketRegionSwitchHtml}</div><div class="indices-part-meta">${activeMeta}</div></div>
      <div class="${activePanel === 'market' ? 'indices-market-stack' : 'indices-index-stack'}">${activeHtml}</div>
    </section>
  </div>`;
}
function toggleHotStockSort(sort) {
  hotStockSortBy = sort;
  fetch('/api/hot_stocks?sort_by=' + sort)
    .then(r => r.ok ? r.json() : null)
    .then(d => { if (d) hotStocksData = d; })
    .then(() => render())
    .catch(() => {});
}
async function refreshPracticeCandidates() {
  const remaining = Number(practiceCandidatesData.cooldown_remaining_seconds || 0);
  if (practiceCandidatesData.running || remaining > 0) return;
  practiceCandidatesData = {...practiceCandidatesData, running:true, error:''};
  renderPracticePage();
  try {
    const res = await actionFetch('/api/practice_candidates/refresh');
    const d = await res.json();
    practiceCandidatesData = {...practiceCandidatesData, ...d};
    renderPracticePage();
    setTimeout(() => load().catch(console.error), 1200);
  } catch (err) {
    practiceCandidatesData = {...practiceCandidatesData, running:false, error:String(err)};
    renderPracticePage();
  }
}
function schedulePracticeManualCyclePoll() {
  if (practiceManualCyclePollTimer) return;
  practiceManualCyclePollTimer = setTimeout(async () => {
    practiceManualCyclePollTimer = null;
    try {
      const response = await fetch('/api/niuniu_practice/manual-cycle', {cache:'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const previousRunning = !!practiceManualCycleData.running;
      practiceManualCycleData = await response.json();
      if (activeCategory === 'practice') renderPracticePage();
      if (practiceManualCycleData.running) schedulePracticeManualCyclePoll();
      else if (previousRunning) await loadPracticePage();
    } catch (error) {
      console.error('practice manual cycle poll error', error);
      if (practiceManualCycleData.running) schedulePracticeManualCyclePoll();
    }
  }, 1500);
}
async function triggerPracticeManualCycle() {
  if (practiceManualCycleData.running) return;
  practiceManualCycleData = {...practiceManualCycleData, running:true, stage:'starting', stage_label:'正在启动', error:''};
  renderPracticePage();
  try {
    const response = await actionFetch('/api/niuniu_practice/manual-cycle');
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    practiceManualCycleData = payload;
    renderPracticePage();
    schedulePracticeManualCyclePoll();
  } catch (error) {
    practiceManualCycleData = {...practiceManualCycleData, running:false, stage:'error', stage_label:'启动失败', error:String(error)};
    renderPracticePage();
  }
}
async function triggerPracticeMarketSummary() {
  if (practiceMarketSummaryGenerating) return;
  practiceMarketSummaryGenerating = true;
  practiceMarketSummaryData = {...practiceMarketSummaryData, error:''};
  renderPracticePage();
  try {
    const response = await actionFetch('/api/niuniu_practice/market-summary');
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.error || `HTTP ${response.status}`);
    practiceMarketSummaryData = {...payload, loading:false, stale:false};
    practiceMarketSummaryExpanded = false;
  } catch (error) {
    practiceMarketSummaryData = {...practiceMarketSummaryData, loading:false, error:String(error).replace(/^Error:\s*/, '')};
  } finally {
    practiceMarketSummaryGenerating = false;
    if (activeCategory === 'practice') renderPracticePage();
  }
}
function renderPracticePage() {
  const d = practiceCandidatesData;
  const items = d.items || [];
  const err = d.error || '';
  const running = !!d.running;
  const cooldownRemaining = Number(d.cooldown_remaining_seconds || 0);
  const cooling = !running && cooldownRemaining > 0;
  const statusText = running ? `⏳ 计算中${d.started_at ? ' · 开始 ' + esc(d.started_at.slice(11)) : ''}` : `🕐 扫描时间: ${esc(d.generated_at || '--')} · 高流动性主板扫描 ${esc(d.count || items.length)} 只入选`;
  const header = `<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px;color:var(--muted);font-size:13px;flex-wrap:wrap">
    <span>${statusText}</span>
  </div>`;
  let html = renderPracticePanel() + header;
  if (running) {
    html += `<div class="empty" style="border-color:rgba(124,92,255,.35);color:#c4b5fd;background:rgba(124,92,255,.08)">⏳ 多战法正在计算中，完成后页面会自动刷新；当前下方仍显示上一版缓存结果。</div>`;
  }
  if (err) {
    html += `<div class="empty" style="color:#f87171">⚠️ ${esc(err)}</div>`;
  } else if (!items.length) {
    html += '<div class="empty">暂无多战法结果，请等待扫描完成…</div>';
  } else {
    const fallbackStrategyMeta = {
      trend_pullback: {label:'趋势回踩',  color:'#60a5fa'},
      breakout:       {label:'突破确认',  color:'#ec4899'},
      shaofu_b1:      {label:'少妇B1',    color:'#f97316'},
      b2_confirm:     {label:'B2确认',    color:'#22c55e'},
      b3_accelerate:  {label:'B3中继',    color:'#a78bfa'},
      super_b1:       {label:'超级B1',    color:'#fb7185'},
      tide_leader:    {label:'主线领航',  color:'#06b6d4'},
      tide_rotation:  {label:'轮动初升',  color:'#14b8a6'},
      tide_recovery:  {label:'冰点修复',  color:'#22d3ee'},
    };
    const STRATEGY_META = {...fallbackStrategyMeta, ...(d.strategy_meta || {})};
    const STOCK_BOARD_LABELS = {
      main_board: '主板',
      chi_next: '创业板',
      star_market: '科创板',
      st: 'ST',
    };
    const tierCounts = {high:0, mid:0, low:0};
    for (const item of items) {
      const s = item.best_score || item.score || 0;
      const threshold = Number(item.entry_threshold || 8);
      const hardBlockers = item.hard_blockers || [];
      const tradeReady = !!item.actionable && !hardBlockers.length && s >= threshold;
      if (tradeReady) tierCounts.high++;
      else if (s >= threshold - 1.5) tierCounts.mid++;
      else tierCounts.low++;
    }
    html += `<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px">
      <span style="padding:4px 10px;border-radius:999px;background:rgba(52,211,153,.15);color:#34d399;border:1px solid rgba(52,211,153,.3);font-size:12px">🥇 试仓 ${tierCounts.high}只</span>
      <span style="padding:4px 10px;border-radius:999px;background:rgba(251,191,36,.15);color:#fbbf24;border:1px solid rgba(251,191,36,.3);font-size:12px">🥈 等确认 ${tierCounts.mid}只</span>
      <span style="padding:4px 10px;border-radius:999px;background:rgba(148,163,184,.12);color:#94a3b8;border:1px solid rgba(148,163,184,.2);font-size:12px">👀 仅观察 ${tierCounts.low}只</span>
    </div>`;
    const distribution = d.strategy_distribution || {};
    const distHtml = Object.entries(distribution).filter(([_, count]) => Number(count) > 0).map(([name, count]) => {
      const sm = STRATEGY_META[name] || {label:name, color:'#94a3b8'};
      return `<span style="padding:4px 10px;border-radius:999px;background:${sm.color}18;color:${sm.color};border:1px solid ${sm.color}38;font-size:12px">${esc(sm.label)} ${Number(count)||0}</span>`;
    }).join('');
    if (distHtml) {
      html += `<div style="display:flex;flex-wrap:wrap;gap:8px;margin:-8px 0 18px">${distHtml}</div>`;
    }
    html += '<div style="display:grid;gap:12px">';
    for (const item of items) {
      const chg = item.change_pct != null ? (item.change_pct > 0 ? '+' : '') + item.change_pct.toFixed(2) + '%' : '--';
      const chgCls = item.change_pct > 0 ? 'up' : item.change_pct < 0 ? 'down' : 'flat';
      const distStr = item.distance_pct != null ? (item.distance_pct > 0 ? '+' : '') + item.distance_pct.toFixed(2) + '%' : '--';
      const bbiUp = item.bbi_upward ? '✅' : '❌';
      const aboveBbi = item.above_bbi ? '✅' : '❌';
      const jRec = item.j_recovering ? '📈回升' : item.j_oversold ? '📉续降' : '--';
      const jInfo = item.min_j_10d != null ? `J最低 ${item.min_j_10d.toFixed(1)} ${jRec}` : '';
      const riskFlags = (item.risk_flags || []).map(f => `<span style="color:#f87171;font-size:11px;margin-left:6px">⚠️${esc(f)}</span>`).join('');
      const hardBlockers = item.hard_blockers || [];
      const hardBlockerFlags = hardBlockers.map(f => `<span style="color:#fbbf24;font-size:11px;margin-left:6px">硬过滤:${esc(f)}</span>`).join('');
      const stratName = item.best_strategy || '';
      const isSectorTide = ['tide_leader', 'tide_rotation', 'tide_recovery'].includes(stratName);
      const tideStatusNames = {leading:'领先', improving:'改善', weakening:'转弱', lagging:'落后'};
      const sm = STRATEGY_META[stratName] || {label:stratName||'综合', color:'#94a3b8'};
      let groupBadge = '';
      const finalScore = item.best_score || item.score || 0;
      const entryThreshold = Number(item.entry_threshold || 8);
      const scoreBasis = item.score_basis || '';
      const tradeDiscipline = [item.position_hint, item.time_stop].filter(Boolean).join(' · ');
      const tradeReady = !!item.actionable && !hardBlockers.length && finalScore >= entryThreshold;
      const industryLabel = item.industry || item.sector || item.board_label || STOCK_BOARD_LABELS[item.board] || '';
      const groupBadgeBase = 'display:inline-flex;align-items:center;flex:0 0 auto;white-space:nowrap;line-height:1;background:rgba(52,211,153,.15);color:#34d399;padding:6px 10px;border-radius:999px;font-size:11px;font-weight:600';
      if (tradeReady) groupBadge = `<span style="${groupBadgeBase}">交易达标</span>`;
      else if (hardBlockers.length) groupBadge = `<span style="${groupBadgeBase};background:rgba(251,191,36,.15);color:#fbbf24">硬过滤</span>`;
      else if (finalScore >= entryThreshold - 1.5) groupBadge = `<span style="${groupBadgeBase};background:rgba(251,191,36,.15);color:#fbbf24">等确认</span>`;
      else groupBadge = `<span style="${groupBadgeBase};background:rgba(148,163,184,.12);color:#94a3b8">仅观察</span>`;
      html += `<div style="background:rgba(16,19,26,.86);border:1px solid var(--line);border-radius:18px;padding:16px;box-shadow:0 10px 36px rgba(0,0,0,.18)">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:10px">
          <div style="min-width:0;flex:1 1 auto">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0">
              <span style="font-weight:780;font-size:17px;color:#f8fafc">${esc(item.code)} ${esc(item.name)}</span>
              <span style="display:inline-flex;align-items:center;white-space:nowrap;padding:2px 8px;border-radius:999px;background:${sm.color}22;color:${sm.color};font-size:12px;border:1px solid ${sm.color}44">${esc(sm.label)}</span>
            </div>
            ${industryLabel ? `<div style="margin-top:8px"><span style="display:inline-flex;align-items:center;max-width:100%;white-space:nowrap;padding:2px 8px;border-radius:999px;background:rgba(124,92,255,.15);color:#c4b5fd;font-size:12px">${esc(industryLabel)}</span></div>` : ''}
          </div>
          ${groupBadge}
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px">
          <div style="background:rgba(2,6,23,.42);border:1px solid rgba(148,163,184,.10);border-radius:12px;padding:8px 10px;flex:1;min-width:100px">
            <div style="color:#8da0b8;font-size:11px">价格 / 涨跌</div>
            <div style="color:#eef2ff;font-size:14px;font-weight:600">${fmtNumber(item.price)} <span class="index-change ${chgCls}" style="font-size:13px">${esc(chg)}</span></div>
          </div>
          <div style="background:rgba(2,6,23,.42);border:1px solid rgba(148,163,184,.10);border-radius:12px;padding:8px 10px;flex:1;min-width:100px">
            <div style="color:#8da0b8;font-size:11px">${esc(sm.label)}评分</div>
            <div style="color:#eef2ff;font-size:14px;font-weight:600">${item.best_score||item.score}/${item.score_total||10} · 基准≥${entryThreshold}</div>
          </div>
          <div style="background:rgba(2,6,23,.42);border:1px solid rgba(148,163,184,.10);border-radius:12px;padding:8px 10px;flex:1;min-width:100px">
            <div style="color:#8da0b8;font-size:11px">${isSectorTide ? 'EMA20 / 距EMA20' : 'BBI / 距BBI'}</div>
            <div style="color:#eef2ff;font-size:14px;font-weight:600">${fmtNumber(isSectorTide ? item.ema20 : item.bbi)} / ${esc(distStr)}</div>
          </div>
          <div style="background:rgba(2,6,23,.42);border:1px solid rgba(148,163,184,.10);border-radius:12px;padding:8px 10px;flex:1;min-width:100px">
            <div style="color:#8da0b8;font-size:11px">成交额</div>
            <div style="color:#eef2ff;font-size:14px;font-weight:600">${item.amount_yi != null ? item.amount_yi + '亿' : '--'}</div>
          </div>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:6px;color:#94a3b8;font-size:12px">
          ${isSectorTide
            ? `<span>市场 ${esc(item.market_regime || '--')} ${fmtNumber(item.market_score)}</span><span>行业潮位 ${esc(tideStatusNames[item.sector_status] || item.sector_status || '--')} / ${fmtNumber(item.sector_score)}</span><span>板块内排名 ${fmtNumber(item.stock_sector_rank)}</span><span>结构止损 ${fmtNumber(item.stop_price)} (${fmtNumber(item.stop_distance_pct)}%)</span><span>跳空缓冲 ${fmtNumber(item.gap_buffer_pct)}%</span><span>有效损失 ${fmtNumber(item.effective_loss_distance_pct)}%</span><span>单笔预算 ${fmtNumber(item.per_trade_risk_budget_pct)}%</span><span>动态仓位上限 ${fmtNumber(item.max_position_pct_by_risk)}%</span>`
            : `<span>BBI上行 ${bbiUp}</span><span>站上BBI ${aboveBbi}</span><span>${esc(jInfo)}</span>`}
          ${scoreBasis ? `<span>${esc(scoreBasis)}</span>` : ''}
          ${tradeDiscipline ? `<span>${esc(tradeDiscipline)}</span>` : ''}
          ${hardBlockerFlags}
          ${riskFlags}
        </div>
      </div>`;
    }
    html += '</div>';
  }
  $('feed').innerHTML = html;
}
function ratingDateKey(r) {
  const t = String(r.time || '').trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(t)) return t.slice(0, 10);
  const ts = Number(r.timestamp || 0);
  if (Number.isFinite(ts) && ts > 0) {
    const d = new Date(ts * 1000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  return '未知日期';
}
function groupRatingRecordsByDay(records) {
  const groups = new Map();
  for (const r of records) {
    const key = ratingDateKey(r);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  return groups;
}
function currentUsRatingRecords(records = filtered()) {
  const groups = groupRatingRecordsByDay(records);
  const days = [...groups.keys()].sort().reverse();
  if (!days.length) return [];
  const day = days[usRatingDayIndex] || days[0];
  return groups.get(day) || [];
}
function shortRatingDate(day) {
  const s = String(day || '');
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
    return `${Number(s.slice(5, 7))}/${Number(s.slice(8, 10))}`;
  }
  return s || '--';
}
function ratingDayButtons(days, restoreDetail = false) {
  const olderDay = days[usRatingDayIndex + 1] || '';
  const newerDay = days[usRatingDayIndex - 1] || '';
  const restoreCall = restoreDetail ? 'restoreRatingDetail();' : '';
  return `
        <button title="查看更早的评级日报" onclick="usRatingDayIndex=Math.min(usRatingDayIndex+1,${days.length-1});render();${restoreCall}refreshVisibleUsQuotes()" ${olderDay ? '' : 'disabled'} style="padding:5px 10px;font-size:12px">‹ ${olderDay ? '更早 ' + esc(shortRatingDate(olderDay)) : '已是最早'}</button>
        <button title="回到更新的评级日报" onclick="usRatingDayIndex=Math.max(usRatingDayIndex-1,0);render();${restoreCall}refreshVisibleUsQuotes()" ${newerDay ? '' : 'disabled'} style="padding:5px 10px;font-size:12px">${newerDay ? '更新 ' + esc(shortRatingDate(newerDay)) : '已是最新'} ›</button>`;
}
function renderUsRatingDay(records) {
  const groups = groupRatingRecordsByDay(records);
  const days = [...groups.keys()].sort().reverse();
  if (!days.length) return '<div class="empty">暂无美股机构买入评级消息</div>';
  const day = days[usRatingDayIndex] || days[0];
  const dayRecords = groups.get(day) || [];
  return `<div class="sector-cloud" style="margin-bottom:14px">
    <div class="rating-day-pager" style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
      <span style="font-weight:700;color:#c7d2fe">${esc(day)}</span>
      <div class="rating-day-actions" style="display:flex;gap:8px">
${ratingDayButtons(days)}
      </div>
    </div>
  </div>
  <div style="display:grid;gap:14px">
    ${dayRecords.map(r => renderRatingCard(r)).join('')}
  </div>`;
}
function fmtUsd(v) { return '$' + (Number(v) || 0).toFixed(2); }
function pctClass(v) { const n = Number(v); return Number.isFinite(n) ? (n >= 0 ? 'pos' : 'neg') : ''; }
function renderMarkdown(s) { let html = esc(s); html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'); html = html.replace(/`([^`]+)`/g, '<code>$1</code>'); return html; }
function cleanRatingValue(v) { return String(v || '').replace(/^[-–—]\s*/, '').replace(/\*\*/g, '').replace(/\s+/g, ' ').trim(); }
function extractTargetPrice(text) {
  const s = String(text || '').replace(/,/g, '').split(/此前|原为|previously|from\s+\$?/i)[0];
  const arrowMatches = Array.from(s.matchAll(/(?:→|->|至|到|上调至|提高至)\s*\$?\s*([0-9]+(?:\.[0-9]+)?)/g));
  if (arrowMatches.length) { const n = Number(arrowMatches[arrowMatches.length - 1][1]); if (Number.isFinite(n)) return n; }
  const patterns = [/\$\s*([0-9]+(?:\.[0-9]+)?)/g, /([0-9]+(?:\.[0-9]+)?)\s*(?:美元|美金|usd)/gi, /(?:目标价)\s*([0-9]+(?:\.[0-9]+)?)/g];
  let best = null;
  for (const re of patterns) { for (const m of s.matchAll(re)) { const n = Number(m[1]); if (Number.isFinite(n)) best = n; } }
  return best;
}
function parseRatingReport(content) {
  const rawLines = String(content || '').split('\n');
  const lines = rawLines.map(line => line.replace(/\s+$/g, ''));
  const stockHeaderRe = /^(?:[-*]\s+|#{2,4}\s*\d+[）.)]?\s*|\d+[）.)]\s*)\*{0,2}[A-Z][A-Z0-9.]{0,8}\s*(?:\/|（|\()\s*[A-Z]/;
  const boldStockRe = /^\*{1,2}\s*(?:\d+[）.)]\s*)?[A-Z][A-Z0-9.]{2,8}\s*(?:[/(（]|\/\s*[A-Z])/;
  const firstStockIdx = lines.findIndex(line => stockHeaderRe.test(line.trim()) || boldStockRe.test(line.trim()));
  if (firstStockIdx < 0) return null;
  const intro = lines.slice(0, firstStockIdx).join('\n').replace(/^-{3,}\s*$/gm, '').trim();
  const title = (intro.split('\n').map(x => x.trim()).filter(Boolean)[0] || '机构买入评级').replace(/^标题[:：]\s*/, '');
  const summary = intro.split('\n').map(x => x.trim()).filter(Boolean).slice(1).join('\n\n');
  const items = []; let current = null;
  const fieldMap = [
    ['analyst', /^[-–—\s*]*(?:\*\*)?机构\/分析师(?:\*\*)?[:：](.*)$/],
    ['action', /^[-–—\s*]*(?:\*\*)?评级动作(?:\*\*)?[:：](.*)$/],
    ['target', /^[-–—\s*]*(?:\*\*)?目标价(?:\*\*)?[:：](.*)$/],
    ['reason', /^[-–—\s*]*(?:\*\*)?核心理由\/催化剂(?:\*\*)?[:：](.*)$/],
    ['risk', /^[-–—\s*]*(?:\*\*)?风险点(?:\*\*)?[:：](.*)$/],
    ['type', /^[-–—\s*]*(?:\*\*)?适合关注类型(?:\*\*)?[:：](.*)$/]
  ];
  let activeKey = '';
  function parseStockHeader(line) {
    let numberedBoldMatch = line.match(/^(?:[-*]\s+|#{2,4}\s*\d+[）.)]?\s*|\d+[）.)]\s*)\*{1,2}\s*([A-Z][A-Z0-9.]{1,8})\s*[（(]\s*([^)）]+?)\s*[)）]\s*\*{0,2}\s*(?:[—–-]\s*(.*))?$/);
    if (!numberedBoldMatch) { numberedBoldMatch = line.match(/^(?:[-*]\s+|#{2,4}\s*\d+[）.)]?\s*|\d+[）.)]\s*)\*{1,2}\s*([A-Z][A-Z0-9.]{1,8})\s*\/\s*([^*：:]+?)\s*\*{0,2}\s*(?:[—–-]\s*(.*))?$/); }
    if (numberedBoldMatch) { return {name: numberedBoldMatch[1].toUpperCase() + ' / ' + cleanRatingValue(numberedBoldMatch[2] || ''), inline: cleanRatingValue(numberedBoldMatch[3] || '')}; }
    const oldMatch = line.match(/^(?:[-*]\s+|#{2,4}\s*\d+[）.)]?\s*|\d+[）.)]\s*)\*{0,2}([A-Z][A-Z0-9.]{0,8}\s*\/\s*[A-Z][^：:]+?)(?:\*{0,2})\s*(?:[:：](.*))?$/);
    if (oldMatch) return {name: cleanRatingValue(oldMatch[1]), inline: cleanRatingValue(oldMatch[2] || '')};
    let boldMatch = line.match(/^\*{1,2}\s*(?:\d+[）.)]\s*)?([A-Z][A-Z0-9.]{2,8})\s*\/\s*([^：:]+?)(?:\s*\*{1,2}|\s*[:：]|$)/);
    if (!boldMatch) { boldMatch = line.match(/^\*{1,2}\s*(?:\d+[）.)]\s*)?([A-Z][A-Z0-9.]{2,8})\s*[（(]\s*([^)）]+?)\s*[)）]/); }
    if (boldMatch) {
      const ticker = boldMatch[1].toUpperCase(), company = cleanRatingValue(boldMatch[2] || '');
      const rest = line.slice(boldMatch[0].length).trim(), inlineMatch = rest.match(/^[\s\S]*?[:：]\s*(.*)/);
      return {name: ticker + (company ? ' / ' + company : ''), inline: cleanRatingValue(inlineMatch ? inlineMatch[1] : '')};
    }
    return null;
  }
  for (const raw of lines.slice(firstStockIdx)) {
    const line = raw.trim();
    if (!line || /^-{3,}$/.test(line)) continue;
    const parsed = parseStockHeader(line);
    if (parsed) {
      const candidateName = parsed.name;
      if (/报道|来源|链接|检索|摘要/.test(candidateName)) continue;
      if (current) items.push(current);
      current = {name: candidateName}; activeKey = '';
      const inline = parsed.inline;
      if (inline) {
        const sentences = inline.split(/[；;。]/).map(x => cleanRatingValue(x)).filter(Boolean);
        for (const sentence of sentences) {
          if (/目标价|\$\s*\d|\d+(?:\.\d+)?\s*(?:美元|美金)/i.test(sentence) && !current.target) current.target = sentence;
          else if (/机构|分析师|\/\s*[A-Z][A-Za-z .]+/.test(sentence) && !current.analyst) current.analyst = sentence;
          else if (/评级|上调|维持|新覆盖|Buy|Overweight|Outperform|Neutral|Underperform/i.test(sentence) && !current.action) current.action = sentence;
          else if (/风险/.test(sentence) && !current.risk) current.risk = sentence.replace(/^风险是?/, '');
          else if (/适合关注类型/.test(sentence) && !current.type) current.type = sentence.replace(/^适合关注类型[:：]?/, '');
          else if (!current.reason) current.reason = sentence;
          else current.reason = cleanRatingValue(current.reason + '；' + sentence);
        }
      }
      continue;
    }
    if (!current) continue;
    let matched = false;
    for (const [key, re] of fieldMap) { const m = line.match(re); if (m) { current[key] = cleanRatingValue(m[1]); activeKey = key; matched = true; break; } }
    if (!matched && activeKey) current[activeKey] = cleanRatingValue((current[activeKey] || '') + ' ' + line);
  }
  if (current) items.push(current);
  const validItems = items.filter(item => /^[A-Z][A-Z0-9.]{1,8}\s*\/?\s*/.test(item.name));
  if (!validItems.length) return null;
  return {title, summary, items: validItems};
}
function inlineField(label, value, className = '') {
  if (!value) return '';
  return `<div class="inline-field ${esc(className)}"><div class="inline-label">${esc(label)}</div><div class="inline-value">${renderMarkdown(value)}</div></div>`;
}
function ratingCompanyDetail(ticker, company, quote) {
  const lines = [`股票代码：${ticker}`];
  const companyName = cleanRatingValue(company);
  const sector = cleanRatingValue(quote && quote.sector);
  const industry = cleanRatingValue(quote && quote.industry);
  if (companyName) lines.push(`公司：${companyName}`);
  if (sector || industry) lines.push(`分类：${[sector, industry].filter(Boolean).join(' / ')}`);
  return lines.join('\n');
}
function ratingMetaDetail(item) {
  const lines = [];
  const analyst = cleanRatingValue(item && item.analyst);
  const type = cleanRatingValue(item && item.type);
  if (analyst) lines.push(`机构 / 分析师：${analyst}`);
  if (type) lines.push(`关注类型：${type}`);
  return lines.join('\n');
}
function safeDomIdPart(value) {
  return String(value || '').replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'row';
}
function ratingStableRowId(reportKey, ticker, idx) {
  return `rating-${safeDomIdPart(reportKey)}-${safeDomIdPart(ticker)}-${idx}`;
}
function renderRatingPriceTable(report, reportTime, reportKey) {
  const seen = new Set();
  const ratingItems = report.items.filter(item => {
    const ticker = String((item.name || '').split('/')[0] || '').trim().toUpperCase();
    if (!ticker || seen.has(ticker)) return false;
    seen.add(ticker); return true;
  });
  const rows = ratingItems.map((item, idx) => {
    const [tickerRaw, ...companyParts] = item.name.split('/').map(x => x.trim());
    const ticker = (tickerRaw || item.name || '').toUpperCase();
    const company = companyParts.join(' / ');
    const target = extractTargetPrice(item.target || item.action || '');
    const quote = {
      ...((usQuotesData.items || {})[ticker] || {}),
      ...((usProfilesData.items || {})[ticker] || {}),
    };
    const price = Number(quote.price);
    const upside = Number.isFinite(price) && price > 0 && Number.isFinite(target) ? ((target / price - 1) * 100) : null;
    const rowId = ratingStableRowId(reportKey || reportTime, ticker, idx);
    return `<tr id="rating-row-${rowId}" class="rating-data-row" onclick="toggleRatingDetail('${rowId}','${ticker}')" title="点击向下展开看多逻辑、机构/分析师和风险点">
      <td data-label="股票"><span class="ticker">${esc(ticker)}</span></td>
      <td data-label="当前股价"><span class="price">${Number.isFinite(price) ? fmtUsd(price) : '--'}</span></td>
      <td data-label="目标股价">${Number.isFinite(target) ? '<span class="target">' + fmtUsd(target) + '</span>' : (item.target ? renderMarkdown(item.target) : '--')}${item.action ? '<span class="rating-action-inline">' + renderMarkdown(item.action.replace(/，.*$/, '')) + '</span>' : ''}</td>
      <td data-label="目标空间">${Number.isFinite(upside) ? '<span class="upside ' + pctClass(upside) + '">' + (upside >= 0 ? '+' : '') + upside.toFixed(1) + '%</span>' : '<span class="muted">--</span>'}</td>
    </tr>
    <tr id="rating-detail-${rowId}" class="rating-detail-row"><td class="rating-detail-cell" colspan="4">
      <div class="rating-inline-detail">
        <div class="rating-inline-grid">
          ${inlineField('公司详情', ratingCompanyDetail(ticker, company, quote), 'rating-detail-company')}
          ${inlineField('评级信息', ratingMetaDetail(item), 'rating-detail-meta')}
          ${inlineField('看多逻辑 / 催化剂', item.reason, 'rating-detail-reason')}
          ${inlineField('风险点', item.risk, 'rating-detail-risk')}
        </div>
      </div>
    </td></tr>`;
  }).join('');
  return `<div class="rating-table-wrap">
    <div class="rating-table-title"><span>股票价格对照表</span><small>${reportTime ? esc(reportTime) : ''}</small></div>
    <table class="rating-table">
      <thead><tr><th>股票</th><th>当前股价</th><th>目标股价</th><th>目标空间</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>`;
}
function renderRatingCard(r) {
  const report = parseRatingReport(r.content);
  if (!report) {
    const lines = r.content.split('\n');
    return `<div class="card">${lines.slice(0, 30).map(l => esc(l)).join('<br>')}${lines.length > 30 ? '<br>...' : ''}</div>`;
  }
  const tableHtml = renderRatingPriceTable(report, r.time, recordKey(r));
  return `<article class="card rating-card">${tableHtml}</article>`;
}
function toggleRatingDetail(rowId, ticker = '') {
  const detailRow = document.getElementById('rating-detail-' + rowId);
  if (!detailRow) return;
  const dataRow = document.getElementById('rating-row-' + rowId);
  const wasOpen = detailRow.classList.contains('open');
  // Close all other open detail rows in the same table
  const table = dataRow ? dataRow.closest('table') : null;
  if (table) table.querySelectorAll('.rating-detail-row.open').forEach(el => el.classList.remove('open'));
  if (table) table.querySelectorAll('.rating-data-row.expanded').forEach(el => el.classList.remove('expanded'));
  if (!wasOpen) {
    detailRow.classList.add('open');
    if (dataRow) dataRow.classList.add('expanded');
    ratingExpandedRowId = rowId;
    loadUsProfiles([ticker]).then(changed => {
      if (!changed || activeCategory !== 'us_ratings' || ratingExpandedRowId !== rowId) return;
      render();
      restoreRatingDetail();
    }).catch(e => console.error('us profile detail load error', e));
  } else {
    ratingExpandedRowId = '';
  }
}
function restoreRatingDetail() {
  if (!ratingExpandedRowId) return;
  const detailRow = document.getElementById('rating-detail-' + ratingExpandedRowId);
  if (!detailRow) { ratingExpandedRowId = ''; return; }
  const dataRow = document.getElementById('rating-row-' + ratingExpandedRowId);
  detailRow.classList.add('open');
  if (dataRow) dataRow.classList.add('expanded');
}
function renderUsRatingDay(records) {
  const groups = groupRatingRecordsByDay(records);
  const days = [...groups.keys()].sort().reverse();
  if (!days.length) return '<div class="empty">暂无美股机构买入评级消息</div>';
  const day = days[usRatingDayIndex] || days[0];
  const dayRecords = groups.get(day) || [];
  return `<div class="sector-cloud" style="margin-bottom:14px">
    <div class="rating-day-pager" style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
      <span style="font-weight:700;color:#c7d2fe">${esc(day)}</span>
      <div class="rating-day-actions" style="display:flex;gap:8px">
${ratingDayButtons(days, true)}
      </div>
    </div>
  </div>
  <div style="display:grid;gap:14px">
    ${dayRecords.map(r => renderRatingCard(r)).join('')}
  </div>`;
}
function renderHistoryControls(records) {
  if (!isMessageCategory()) return '';
  if (activeCategory === 'x_monitor') {
    return renderXPager(records);
  }
  if (activeCategory === 'us_ratings') {
    return '';
  }
  const shown = records.length;
  const total = activeCategoryTotal();
  if (!total || shown >= total) {
    return `<div class="sector-cloud" style="margin-top:2px;padding:12px 14px;color:#94a3b8;font-size:13px">已显示全部历史：${shown} / ${total || shown}</div>`;
  }
  return `<div class="sector-cloud" style="margin-top:2px;padding:12px 14px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
    <span style="color:#94a3b8;font-size:13px">已显示 ${shown} / ${total} 条历史</span>
    <button onclick="loadMoreMessages()" ${loadingMoreHistory ? 'disabled' : ''} style="padding:8px 12px;font-size:13px">${loadingMoreHistory ? '加载中...' : '加载更多历史'}</button>
  </div>`;
}
function renderXPager(records) {
  const limit = messagePageLimit('x_monitor');
  const total = activeCategoryTotal();
  const totalPages = Math.max(1, Math.ceil((total || records.length || 1) / limit));
  const page = Math.min(totalPages, Math.floor(xPageOffset / limit) + 1);
  const first = total && records.length ? xPageOffset + 1 : (records.length ? 1 : 0);
  const last = total && records.length ? Math.min(xPageOffset + records.length, total) : records.length;
  const prevOffset = Math.max(0, xPageOffset - limit);
  const nextOffset = xPageOffset + limit;
  const lastOffset = Math.max(0, (totalPages - 1) * limit);
  const atFirst = xPageOffset <= 0;
  const atLast = total ? nextOffset >= total : records.length < limit;
  const disabled = loadingMoreHistory ? 'disabled' : '';
  return `<div class="sector-cloud x-pager">
    <div class="x-pager-status">第 ${page} / ${totalPages} 页 · ${first}-${last} / ${total || last} 条${loadingMoreHistory ? ' · 加载中...' : ''}</div>
    <div class="x-pager-actions">
      <button class="x-page-btn" onclick="loadXPage(0)" ${disabled || atFirst ? 'disabled' : ''}>首页</button>
      <button class="x-page-btn" onclick="loadXPage(${prevOffset})" ${disabled || atFirst ? 'disabled' : ''}>上一页</button>
      <button class="x-page-btn" onclick="loadXPage(${nextOffset})" ${disabled || atLast ? 'disabled' : ''}>下一页</button>
      <button class="x-page-btn" onclick="loadXPage(${lastOffset})" ${disabled || atLast ? 'disabled' : ''}>末页</button>
    </div>
  </div>`;
}
function shortHash(text) {
  let h = 2166136261;
  const s = String(text || '');
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(36);
}
function marketRecordKey(r) {
  return 'market-' + shortHash(recordKey(r));
}
function cleanMarketLine(line) {
  return String(line || '').replace(/\*\*/g, '').replace(/`/g, '').replace(/\s+/g, ' ').trim();
}
function marketReportType(record, content = '') {
  const identity = [
    record?.title,
    record?.chat_label,
    record?.metadata?.job_name,
    String(content || '').split('\n').slice(0, 3).join(' '),
  ].map(value => String(value || '').trim()).filter(Boolean).join(' ');
  const source = [
    record?.source_id,
    record?.external_id,
    record?.delivery?.job_id,
    record?.metadata?.run_key,
  ].map(value => String(value || '').trim()).filter(Boolean).join(' ');
  if (/98f0c8a12d3e/.test(source) || /隔夜美股|美股盘面/.test(identity)) return '美股';
  if (/192abba7eeb5/.test(source) || /午盘/.test(identity)) return '午盘';
  if (/67ac98149ead/.test(source) || /盘后|收盘/.test(identity)) return '盘后';
  if (/8453b3f28cd3/.test(source) || /竞价|盘前/.test(identity)) return '竞价';
  return '盘面';
}
function marketSectionLines(lines, headingText, limit = 3) {
  const start = lines.findIndex(line => cleanMarketLine(line).includes(headingText));
  if (start < 0) return [];
  const result = [];
  for (const raw of lines.slice(start + 1)) {
    const line = cleanMarketLine(raw);
    if (!line) continue;
    if (/\*\*.+\*\*/.test(raw) || /^[📊🔥💰⚡📈💡⚠️🌡️📌👀ℹ️]/u.test(line)) break;
    result.push(line);
    if (result.length >= limit) break;
  }
  return result;
}
function summarizeMarketRecord(r) {
  const raw = String(r.content || '');
  const lines = raw.split('\n').map(x => x.trim()).filter(Boolean);
  const cleanLines = lines.map(cleanMarketLine).filter(Boolean);
  const titleLine = cleanLines[0] || '盘面监控';
  const title = titleLine.replace(/^牛牛大王[，,]\s*/, '').replace(/来了[:：]?$/, '').trim() || '盘面监控';
  const timeLine = cleanLines.find(line => /\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}/.test(line)) || '';
  const timeMatch = timeLine.match(/\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}/);
  const mood = cleanLines.find(line => line.startsWith('💬')) || '';
  const overview = cleanLines.find(line => /^样本\s/.test(line) || /^涨停池\s/.test(line)) || '';
  const volume = cleanLines.find(line => /成交额\s/.test(line)) || '';
  const hotLines = marketSectionLines(lines, '热门板块', 3);
  const chips = [];
  for (const line of [overview, volume, ...hotLines]) {
    const text = truncateText(line.replace(/^💬\s*/, ''), 34);
    if (text) chips.push(text);
  }
  return {
    title,
    type: marketReportType(r, raw),
    time: timeMatch ? timeMatch[0] : (r.time || ''),
    preview: truncateText((mood || overview || titleLine).replace(/^💬\s*/, ''), 150),
    chips: chips.slice(0, 5)
  };
}
function marketLeadingIcon(text) {
  const m = String(text || '').match(/^(📊|🔥|💰|⚡|📈|💡|⚠️|⚠|🌡️|🌡|📌|👀|ℹ️|ℹ)\s*/u);
  return m ? {icon: m[1], rest: String(text || '').slice(m[0].length).trim()} : {icon: '', rest: String(text || '').trim()};
}
function marketSectionTone(title, icon) {
  const s = `${title || ''} ${icon || ''}`;
  if (/风险|⚠/.test(s)) return 'risk';
  if (/资金/.test(s)) return 'flow';
  if (/热门|强势|封单|热度|🔥|⚡|🌡|📌/.test(s)) return 'hot';
  if (/操作|提示|观察|💡|👀/.test(s)) return 'tip';
  if (/概况|情绪|📊/.test(s)) return 'overview';
  return '';
}
function marketHeadingInfo(raw) {
  const clean = cleanMarketLine(raw);
  if (!clean) return null;
  const leading = marketLeadingIcon(clean);
  const titleSource = leading.rest || clean;
  const titleParts = titleSource.split(/[·|]/).map(x => x.trim()).filter(Boolean);
  const title = (titleParts[0] || titleSource).replace(/[:：]$/, '').trim();
  const hasMarkdownHeading = /\*\*.+\*\*/.test(String(raw || ''));
  const knownHeading = /^(市场概况|竞价情绪|开盘价强弱|热门板块|竞价强势板块|资金流向|强势个股|成交活跃|竞价成交活跃|操作提示|风险|复合热度|涨停封单|封单|跌停风险|重点观察)/.test(title);
  if (!hasMarkdownHeading && !knownHeading) return null;
  return {
    title: title || '盘面小节',
    meta: titleParts.slice(1).join(' · '),
    icon: leading.icon || '•',
    tone: marketSectionTone(title, leading.icon)
  };
}
function parseMarketDetail(content) {
  const sections = [];
  const intro = [];
  let current = null;
  const pushCurrent = () => {
    if (current && (current.items.length || current.meta)) sections.push(current);
    current = null;
  };
  for (const raw of String(content || '').split('\n')) {
    if (!String(raw || '').trim()) continue;
    const heading = marketHeadingInfo(raw);
    if (heading) {
      pushCurrent();
      current = {...heading, items: []};
      continue;
    }
    const clean = cleanMarketLine(raw);
    if (!clean) continue;
    if (current) current.items.push(clean);
    else intro.push(clean);
  }
  pushCurrent();
  return {intro, sections};
}
function marketMoodLine(sections) {
  for (const section of sections) {
    for (const line of section.items || []) {
      const clean = cleanMarketLine(line);
      if (/^💬/.test(clean)) return clean.replace(/^💬\s*/, '').trim();
    }
  }
  return '';
}
function marketMetricTone(label, value) {
  const n = Number(String(value || '').replace(/[^\d.-]/g, ''));
  if (/上涨|涨停/.test(label)) return 'up';
  if (/下跌|跌停/.test(label)) return 'down';
  if (Number.isFinite(n) && n > 0 && /^\+/.test(String(value || '').trim())) return 'up';
  if (Number.isFinite(n) && n < 0) return 'down';
  return '';
}
function marketSummaryMetrics(sections) {
  const overview = sections.find(section => /市场概况|竞价情绪/.test(section.title)) || sections[0];
  if (!overview) return [];
  const metrics = [];
  const seen = new Set();
  for (const line of overview.items || []) {
    const clean = cleanMarketLine(line).replace(/^💬\s*/, '').trim();
    for (const part of clean.split(/[|·]/).map(x => x.trim()).filter(Boolean)) {
      const m = part.match(/^(涨停池|跌停池|竞价额|竞价量|成交额|样本|高开|平开|低开|强高开|深低开|上涨|下跌|平盘|涨停|跌停)\s*([+\-]?\d[\d,.]*(?:\.\d+)?\s*(?:只|亿手|万手|手|亿|万亿|万|%)?)/);
      if (!m || seen.has(m[1])) continue;
      seen.add(m[1]);
      metrics.push({label: m[1], value: m[2].replace(/\s+/g, ''), tone: marketMetricTone(m[1], m[2])});
      if (metrics.length >= 8) return metrics;
    }
  }
  return metrics;
}
function isMarketMetricLine(line) {
  const clean = cleanMarketLine(line).replace(/^💬\s*/, '').trim();
  return /(?:^|[|·]\s*)(涨停池|跌停池|竞价额|竞价量|成交额|样本|高开|平开|低开|强高开|深低开|上涨|下跌|平盘|涨停|跌停)\s*[+\-]?\d/.test(clean);
}
function renderMarketOverview(parsed) {
  const mood = marketMoodLine(parsed.sections);
  const metrics = marketSummaryMetrics(parsed.sections);
  if (!mood && !metrics.length) return '';
  const moodHtml = mood ? `<div class="market-mood-panel"><div class="market-mood-label">核心判断</div><div class="market-mood-text">${esc(mood)}</div></div>` : '';
  const metricHtml = metrics.length ? `<div class="market-metric-grid">${metrics.map(item => `
    <div class="market-metric-item">
      <div class="market-metric-label">${esc(item.label)}</div>
      <div class="market-metric-value ${esc(item.tone)}">${esc(item.value)}</div>
    </div>`).join('')}</div>` : '';
  return `<div class="market-detail-overview">${moodHtml}${metricHtml}</div>`;
}
function marketSectionDisplayItems(section) {
  const isOverview = /市场概况|竞价情绪/.test(section.title || '');
  return (section.items || []).filter(line => {
    const clean = cleanMarketLine(line);
    if (/^💬/.test(clean)) return false;
    if (isOverview && isMarketMetricLine(clean)) return false;
    return true;
  });
}
function renderMarketSignedText(text, options = {}) {
  const source = String(text || '');
  const colorUnsignedMoney = !!options.colorUnsignedMoney;
  const pattern = /((?:sh|sz|bj)?\d{6}\s+[*A-Za-z\u4e00-\u9fa5][*A-Za-z0-9\u4e00-\u9fa5·]{1,12})|([+\-]\d[\d,.]*(?:\.\d+)?\s*(?:%|万亿|亿手|万手|手|亿|万|元)?|\d[\d,.]*(?:\.\d+)?\s*(?:万亿|亿手|万手|手|亿))/gi;
  let html = '';
  let last = 0;
  for (const match of source.matchAll(pattern)) {
    const token = match[0];
    const start = match.index || 0;
    html += esc(source.slice(last, start));
    if (match[1]) {
      html += `<span class="market-symbol">${esc(token)}</span>`;
      last = start + token.length;
      continue;
    }
    const compact = token.replace(/\s+/g, '');
    const unsignedMoney = !/^[+\-]/.test(compact) && /(?:万亿|亿)$/.test(compact);
    const cls = compact.startsWith('-')
      ? 'down'
      : (compact.startsWith('+') || (colorUnsignedMoney && unsignedMoney) ? 'up' : '');
    html += cls ? `<span class="market-num ${cls}">${esc(token)}</span>` : esc(token);
    last = start + token.length;
  }
  html += esc(source.slice(last));
  return html;
}
function renderMarketDetailLine(text, sectionTone = '') {
  const clean = cleanMarketLine(text).replace(/^·\s*/, '').trim();
  if (!clean) return '';
  const flow = clean.match(/^(流入|流出)[:：]\s*(.+)$/);
  if (flow) {
    return `<div class="market-detail-line flow"><span class="market-flow-label">${esc(flow[1])}</span><span class="market-flow-value">${renderMarketSignedText(flow[2], {colorUnsignedMoney: true})}</span></div>`;
  }
  const cls = ['market-detail-line', 'item'];
  if (/^数据暂不可用|^数据为|^ℹ️|^ℹ/.test(clean)) cls.push('note');
  if (sectionTone === 'risk') cls.push('risk');
  if (sectionTone === 'tip') cls.push('tip');
  const colorUnsignedMoney = sectionTone === 'flow' || /净额/.test(clean);
  return `<div class="${cls.join(' ')}"><span>${renderMarketSignedText(clean, {colorUnsignedMoney})}</span></div>`;
}
function renderMarketSection(section) {
  const items = marketSectionDisplayItems(section);
  if (!items.length && /市场概况|竞价情绪/.test(section.title || '')) return '';
  if (!items.length && !section.meta) return '';
  const tone = section.tone || '';
  const wide = (section.wide || /热门板块|竞价强势板块|资金流向|竞价成交活跃/.test(section.title || '')) ? ' wide' : '';
  const count = items.length ? `<span class="market-section-count">${items.length} 条</span>` : '';
  const meta = section.meta ? `<span class="market-section-count">${esc(section.meta)}</span>` : count;
  const body = items.map(line => renderMarketDetailLine(line, tone)).filter(Boolean).join('');
  return `<section class="market-section ${esc(tone)}${wide}">
    <div class="market-section-head">
      <div class="market-section-title-wrap"><span class="market-section-icon">${esc(section.icon || '•')}</span><span class="market-section-title">${esc(section.title || '盘面小节')}</span></div>
      ${meta}
    </div>
    ${body ? `<div class="market-section-body">${body}</div>` : ''}
  </section>`;
}
function renderMarketDetail(content) {
  const parsed = parseMarketDetail(content);
  const overview = renderMarketOverview(parsed);
  const intro = parsed.intro.filter(line => !/^牛牛大王[，,]/.test(line)).map(line => renderMarketDetailLine(line)).filter(Boolean).join('');
  const sections = parsed.sections.map(renderMarketSection).filter(Boolean).join('');
  if (!overview && !intro && !sections) {
    const fallback = String(content || '').split('\n').map(line => renderMarketDetailLine(line)).filter(Boolean).join('');
    return `<div class="market-detail-box">${fallback}</div>`;
  }
  return `<div class="market-detail-box">${overview}${intro ? `<div class="market-section-list"><section class="market-section wide"><div class="market-section-head"><div class="market-section-title-wrap"><span class="market-section-icon">•</span><span class="market-section-title">摘要</span></div></div><div class="market-section-body">${intro}</div></section></div>` : ''}${sections ? `<div class="market-section-list">${sections}</div>` : ''}</div>`;
}
function renderMarketMonitorCard(r) {
  const key = marketRecordKey(r);
  const summary = summarizeMarketRecord(r);
  const open = marketExpandedRecordKey === key;
  const chips = summary.chips.map(text => {
    const cls = /\s-\d/.test(text) ? ' down' : /\s\+\d/.test(text) ? ' up' : '';
    return `<span class="market-chip${cls}">${esc(text)}</span>`;
  }).join('');
  return `<article class="market-monitor-card ${open ? 'open' : ''}" data-market-key="${esc(key)}" aria-expanded="${open ? 'true' : 'false'}">
    <div class="market-card-head">
      <div>
        <div class="market-card-title-row"><span class="market-card-title">${esc(summary.title)}</span>${summary.time ? `<span class="market-card-time">${esc(summary.time)}</span>` : ''}</div>
        <div class="market-card-preview">${esc(summary.preview || '等待盘面摘要')}</div>
        ${chips ? `<div class="market-chip-row">${chips}</div>` : ''}
      </div>
      <div class="market-card-side"><span class="market-type">${esc(summary.type)}</span><span class="market-chevron">›</span></div>
    </div>
    ${open ? `<div class="market-card-detail">${renderMarketDetail(r.content || '')}</div>` : ''}
  </article>`;
}
function usMarketToneClass(tone) {
  return ['offensive', 'balanced', 'cautious', 'defensive'].includes(tone) ? tone : 'neutral';
}
function renderUsMarketSummaryHead(subtitle, toneLabel, preview) {
  const actionLabel = usMarketSummaryExpanded ? '收起' : '展开';
  return `<button type="button" class="us-market-head" data-us-market-action="toggle" aria-controls="us-market-summary-body" aria-expanded="${usMarketSummaryExpanded ? 'true' : 'false'}" aria-label="${actionLabel}隔夜美股盘面总结">
    <span><span class="us-market-title">隔夜美股盘面总结</span><span class="us-market-sub">${esc(subtitle)}</span><span class="market-card-preview us-market-preview">${esc(preview)}</span></span>
    <span class="us-market-head-actions"><span class="us-market-tone">${esc(toneLabel)}</span><span class="market-chevron us-market-chevron" aria-hidden="true">›</span></span>
  </button>`;
}
function renderUsMarketSummaryMetric(metric) {
  const pct = Number(metric?.change_pct);
  const pctTone = Number.isFinite(pct) ? upCls(pct) : 'flat';
  return `<div class="market-metric-item">
    <div class="market-metric-label">${esc(metric?.label || '')}</div>
    <div class="market-metric-value us-market-metric-value"><span>${esc(metric?.value || '--')}</span><span class="market-num ${pctTone}">${esc(metric?.change_pct_text || '--')}</span></div>
  </div>`;
}
function renderUsMarketSummaryDetail(summaryData, summary) {
  const metrics = (summaryData?.metrics || []).slice(0, 8);
  const metricHtml = metrics.length ? `<div class="market-metric-grid">${metrics.map(renderUsMarketSummaryMetric).join('')}</div>` : '';
  const overview = `<div class="market-detail-overview us-market-overview${metrics.length ? '' : ' no-metrics'}">
    <div class="market-mood-panel"><div class="market-mood-label">核心判断</div><div class="market-mood-text">${esc(summary)}</div></div>
    ${metricHtml}
  </div>`;
  const mappingItems = (summaryData?.sector_mappings || []).slice(0, 5).map(mapping => {
    const mapText = Array.isArray(mapping.a_share_mapping)
      ? mapping.a_share_mapping.slice(0, 4).join(' / ')
      : (mapping.a_share_mapping || '相关板块');
    const sector = mapping.proxy ? `${mapping.us_sector || ''}(${mapping.proxy})` : (mapping.us_sector || '美股板块');
    const strategy = cleanMarketLine(mapping.strategy || '');
    if (strategy) return `${sector} ${mapping.change_pct_text || '--'} · ${strategy}`;
    const bias = cleanMarketLine(mapping.bias || '');
    return `${sector} ${mapping.change_pct_text || '--'} · A股映射：${mapText}${bias ? ` · ${bias}` : ''}`;
  });
  const summaryText = cleanMarketLine(summary);
  const guidanceSeen = new Set();
  const guidanceItems = (summaryData?.guidance_lines || []).slice(0, 7).filter(line => {
    const clean = cleanMarketLine(line);
    if (!clean || summaryText.includes(clean) || guidanceSeen.has(clean)) return false;
    guidanceSeen.add(clean);
    return true;
  });
  const sections = [
    mappingItems.length ? renderMarketSection({title: 'A股板块映射', icon: '🧭', tone: 'overview', wide: true, items: mappingItems}) : '',
    guidanceItems.length ? renderMarketSection({title: '今日执行', icon: '💡', tone: 'tip', wide: true, items: guidanceItems}) : '',
  ].filter(Boolean).join('');
  return `<div class="market-detail-box">${overview}${sections ? `<div class="market-section-list">${sections}</div>` : ''}</div>`;
}
function renderUsMarketSummaryCard() {
  const d = usMarketSummaryData || {};
  const expandedClass = usMarketSummaryExpanded ? ' open' : ' collapsed';
  if (d.loading && !d.generated_at) {
    const loadingSummary = '这条摘要会作为今日买卖选股的外盘背景，盘中仍以 A 股竞价、资金流和板块联动确认。';
    return `<section class="us-market-summary-card neutral${expandedClass}">
      ${renderUsMarketSummaryHead('正在加载昨晚美股盘面...', '加载中', loadingSummary)}
      <div id="us-market-summary-body" class="market-card-detail us-market-summary-body"${usMarketSummaryExpanded ? '' : ' hidden'}>${renderUsMarketSummaryDetail(d, loadingSummary)}</div>
    </section>`;
  }
  const tone = usMarketToneClass(String(d.tone || 'neutral'));
  const toneLabel = d.tone_label || '中性';
  const target = d.target_us_date || '--';
  const dateRule = d.date_rule || '周一显示上周五美股盘面；其他日期显示前一美股交易日。';
  const summary = d.summary || (d.error ? '隔夜美股盘面暂不可用，今日先按 A 股自身信号执行。' : '等待隔夜美股盘面总结。');
  return `<section class="us-market-summary-card ${tone}${expandedClass}">
    ${renderUsMarketSummaryHead(`目标美股交易日 ${target} · ${dateRule}`, toneLabel, summary)}
    <div id="us-market-summary-body" class="market-card-detail us-market-summary-body"${usMarketSummaryExpanded ? '' : ' hidden'}>${renderUsMarketSummaryDetail(d, summary)}</div>
  </section>`;
}
function isUsMarketSummaryRecord(record) {
  const title = String(record?.title || record?.chat_label || '').trim();
  const sourceId = String(record?.source_id || '').trim();
  const jobId = String(record?.delivery?.job_id || record?.metadata?.job_id || '').trim();
  return title === '隔夜美股盘面总结'
    || sourceId === 'cron_output_98f0c8a12d3e'
    || jobId === '98f0c8a12d3e';
}
function usMarketSummaryMatchesDay(day, summaryData = usMarketSummaryData) {
  const selectedDay = String(day || '').slice(0, 10);
  const targetDay = String(summaryData?.target_cn_date || '').slice(0, 10);
  return Boolean(selectedDay && targetDay && selectedDay === targetDay);
}
function marketDateKey(r) {
  const t = String(r.time || '').trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(t)) return t.slice(0, 10);
  const contentDate = String(r.content || '').match(/\d{4}-\d{2}-\d{2}/);
  if (contentDate) return contentDate[0];
  const ts = Number(r.timestamp || 0);
  if (Number.isFinite(ts) && ts > 0) {
    const d = new Date(ts * 1000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  return '未知日期';
}
function groupMarketRecordsByDay(records) {
  const groups = new Map();
  for (const r of records) {
    const key = marketDateKey(r);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  return groups;
}
function setMarketDay(index) {
  const records = filtered();
  const days = [...groupMarketRecordsByDay(records).keys()].sort().reverse();
  if (!days.length) return;
  marketDayIndex = Math.max(0, Math.min(Number(index || 0), days.length - 1));
  marketExpandedRecordKey = '';
  syncViewUrl();
  render();
  saveMarketPageState();
}
function renderMarketDayPager(allRecords, days, day, dayRecords) {
  const total = activeCategoryTotal();
  const atLatest = marketDayIndex <= 0;
  const atEarliest = marketDayIndex >= days.length - 1;
  const loadedText = total && allRecords.length < total ? `已载入最近 ${allRecords.length} / ${total} 条` : `共 ${days.length} 个日期`;
  return `<div class="sector-cloud market-day-pager">
    <div>
      <div class="market-day-title">${esc(day)} · ${dayRecords.length} 条盘面监控</div>
      <div class="market-day-sub">${loadedText}</div>
    </div>
    <div class="market-day-actions">
      <button class="market-day-btn" onclick="setMarketDay(0)" ${atLatest ? 'disabled' : ''}>最新</button>
      <button class="market-day-btn" onclick="setMarketDay(${marketDayIndex - 1})" ${atLatest ? 'disabled' : ''}>后一天</button>
      <button class="market-day-btn" onclick="setMarketDay(${marketDayIndex + 1})" ${atEarliest ? 'disabled' : ''}>前一天</button>
      <button class="market-day-btn" onclick="setMarketDay(${days.length - 1})" ${atEarliest ? 'disabled' : ''}>最早</button>
    </div>
  </div>`;
}
function renderMarketMonitor(records) {
  if (!records.length) return `<div class="empty">暂无盘面监控消息</div>${renderUsMarketSummaryCard()}`;
  const groups = groupMarketRecordsByDay(records);
  const days = [...groups.keys()].sort().reverse();
  if (!days.length) return `<div class="empty">暂无盘面监控消息</div>${renderUsMarketSummaryCard()}`;
  if (marketDayIndex >= days.length) marketDayIndex = 0;
  const day = days[marketDayIndex] || days[0];
  const dayRecords = groups.get(day) || [];
  const showLiveUsSummary = usMarketSummaryMatchesDay(day);
  const visibleDayRecords = showLiveUsSummary
    ? dayRecords.filter(record => !isUsMarketSummaryRecord(record))
    : dayRecords;
  const usSummaryHtml = showLiveUsSummary ? renderUsMarketSummaryCard() : '';
  return `<div class="market-monitor-grid">${visibleDayRecords.map(r => renderMarketMonitorCard(r)).join('')}</div>${usSummaryHtml}${renderMarketDayPager(records, days, day, dayRecords)}`;
}
function xRecordKey(r) {
  return 'x-' + shortHash(recordKey(r));
}
function cleanXLine(line) {
  return String(line || '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/^#{1,6}\s*/, '')
    .replace(/^[│┃┌└↳\-–—━\s]+/u, '')
    .replace(/\*\*/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}
function normalizeXMarker(line) {
  return cleanXLine(line).replace(/^【([^】]+)】/, '$1｜').trim();
}
function xLineRole(line) {
  const s = normalizeXMarker(line);
  if (/^(?:引用)?原[贴帖](?:\s*[|｜:：]|$)/.test(s)) return 'original';
  if (/^回复(?:\s*[|｜:：]|$)/.test(s)) return 'reply';
  return '';
}
function isXNoiseLine(line) {
  const s = cleanXLine(line);
  return !s || /^X Watchlist Dashboard Archive$/i.test(s) || /^Cron Job:/i.test(s) ||
    /^Job ID:/i.test(s) || /^Run Time:/i.test(s) || /^Mode:/i.test(s) ||
    /^Status:/i.test(s) || /^发现 X 账号新推文/.test(s) || /^X 新推文 \d+/.test(s);
}
function xParts(line) {
  return normalizeXMarker(line).split(/[｜|]/).map(x => x.trim()).filter(Boolean);
}
function xIsTimePart(part) {
  const s = String(part || '').trim();
  return /\d{4}-\d{2}-\d{2}/.test(s) || /^时间未知$/.test(s);
}
function xCleanAuthorPart(part) {
  return String(part || '')
    .replace(/^(?:引用)?原[贴帖]\s*[:：]?/, '')
    .replace(/^回复\s*[:：]?/, '')
    .replace(/^评论\/转述\s*[:：]?/, '')
    .trim();
}
function xLooksLikeRolePart(part) {
  const s = xCleanAuthorPart(part);
  return /^(?:回复|评论\/转述|转述|评论|引用|原[贴帖]|引用原[贴帖])$/.test(s) || !s;
}
function xHeaderAuthor(parts, role) {
  if (!parts.length) return '';
  if (parts.length >= 3 || role === 'reply' || role === 'original' || xLooksLikeRolePart(parts[0])) {
    const found = parts.find((p, i) => i > 0 && !xIsTimePart(p));
    return xCleanAuthorPart(found || parts[1] || parts[0]);
  }
  if (parts.length === 2 && xIsTimePart(parts[1])) {
    return xCleanAuthorPart(parts[0]);
  }
  return xCleanAuthorPart(parts.find(p => /^@/.test(p)) || parts[0]);
}
function xMetadataAuthor(r) {
  const post = xPostMeta(r);
  const direct = String(post.display_name || '').trim();
  if (direct) return direct;
  const sourceLabel = String((r && r.source_label) || '').trim();
  if (sourceLabel && sourceLabel !== '推特监控' && sourceLabel !== 'X 监控') return sourceLabel;
  const handle = String((r && r.metadata && r.metadata.handle) || post.handle || (r && r.source_id) || '').trim();
  return handle && !/^cron_/i.test(handle) ? '@' + handle.replace(/^@/, '') : '';
}
function truncateText(text, maxLen = 180) {
  const s = String(text || '').replace(/\s+/g, ' ').trim();
  return s.length > maxLen ? s.slice(0, maxLen - 1) + '…' : s;
}
function summarizeXRecord(r) {
  const raw = String(r.content || '');
  const lines = raw.split('\n').map(cleanXLine).filter(line => line && !isXNoiseLine(line));
  const replyIdx = lines.findIndex(line => xLineRole(line) === 'reply');
  const originalIdx = lines.findIndex(line => xLineRole(line) === 'original');
  const headerIdx = replyIdx >= 0 ? replyIdx : (originalIdx >= 0 ? originalIdx : lines.findIndex(line => line.includes('｜') || line.includes('|')));
  const headerLine = headerIdx >= 0 ? lines[headerIdx] : (lines[0] || '');
  const parts = xParts(headerLine);
  const role = xLineRole(headerLine);
  let author = xHeaderAuthor(parts, role);
  if (!author || xIsTimePart(author)) author = xMetadataAuthor(r);
  author = author || 'X';
  const timeFromHeader = parts.find(p => /\d{4}-\d{2}-\d{2}/.test(p));
  const bodyStart = headerIdx >= 0 ? headerIdx + 1 : 0;
  let bodyLines = lines.slice(bodyStart).filter(line => !xLineRole(line) && !isXNoiseLine(line) && !/^[-━└]+$/.test(line));
  if (!bodyLines.length) bodyLines = lines.filter(line => !xLineRole(line) && !isXNoiseLine(line));
  const preview = truncateText(bodyLines.join(' '), 190) || '暂无正文';
  const source = String(r.platform || r.chat_title || r.chat_name || r.session_id || '').trim();
  const label = role === 'reply' ? '回复' : (role === 'original' && headerLine.includes('引用') ? '引用' : '推文');
  const initialSource = author.replace(/^@/, '').trim();
  return {
    author,
    time: timeFromHeader || r.time || '',
    preview,
    source,
    label,
    threaded: originalIdx >= 0 && replyIdx >= 0,
    initial: (initialSource[0] || 'X').toUpperCase()
  };
}
function xPostMeta(r) {
  const meta = r && typeof r.metadata === 'object' && r.metadata ? r.metadata : {};
  return meta && typeof meta.post === 'object' && meta.post ? meta.post : {};
}
function cleanXMediaUrl(url) {
  let s = String(url || '').trim().replace(/\\\//g, '/');
  if (!/^https?:\/\//i.test(s)) return '';
  if (s.includes('pbs.twimg.com/media/') && !s.includes('?') && !/:(?:large|small|medium|orig)$/i.test(s) && /\.(?:jpg|jpeg|png|webp)$/i.test(s)) {
    s += ':large';
  }
  return s;
}
function isXPostMediaUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' && parsed.hostname === 'pbs.twimg.com' && /^\/(?:media|ext_tw_video_thumb|tweet_video_thumb)\//.test(parsed.pathname);
  } catch (_err) {
    return false;
  }
}
function xMediaItems(items) {
  if (!Array.isArray(items)) return [];
  const seen = new Set();
  const out = [];
  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    const url = cleanXMediaUrl(item.url || '');
    const type = String(item.type || '').trim() || 'image';
    if (!isXPostMediaUrl(url)) continue;
    const key = url;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({url, type});
  }
  return out.slice(0, 8);
}
function xMediaDisplayUrl(url) {
  return `/api/x_media?url=${encodeURIComponent(url)}`;
}
function clampXImageZoom(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 1;
  return Math.max(0.5, Math.min(3, Math.round(n * 100) / 100));
}
function xImageViewerRoot() {
  let root = document.getElementById('xImageViewerRoot');
  if (!root) {
    root = document.createElement('div');
    root.id = 'xImageViewerRoot';
    document.body.appendChild(root);
  }
  return root;
}
function renderXImageViewer() {
  const root = xImageViewerRoot();
  document.body.classList.toggle('x-image-viewer-open', !!xImageViewer.url);
  if (!xImageViewer.url) {
    root.innerHTML = '';
    return;
  }
  const zoom = clampXImageZoom(xImageViewer.zoom);
  xImageViewer.zoom = zoom;
  const src = xMediaDisplayUrl(xImageViewer.url);
  const label = xImageViewer.label || '推文图片';
  root.innerHTML = `<div class="x-image-viewer-backdrop">
    <div class="x-image-viewer-card" role="dialog" aria-modal="true" aria-label="${esc(label)}">
      <div class="x-image-viewer-head">
        <div class="x-image-viewer-title">${esc(label)} · ${Math.round(zoom * 100)}%</div>
        <div class="x-image-viewer-actions">
          <button type="button" class="x-image-viewer-btn" data-x-viewer-action="zoom-out" title="缩小" aria-label="缩小" ${zoom <= 0.5 ? 'disabled' : ''}>-</button>
          <button type="button" class="x-image-viewer-btn" data-x-viewer-action="zoom-in" title="放大" aria-label="放大" ${zoom >= 3 ? 'disabled' : ''}>+</button>
          <button type="button" class="x-image-viewer-btn" data-x-viewer-action="close" title="关闭" aria-label="关闭">x</button>
        </div>
      </div>
      <div class="x-image-viewer-stage">
        <img class="x-image-viewer-img" data-x-viewer-action="close" src="${esc(src)}" alt="${esc(label)}" style="--x-image-zoom:${zoom}" draggable="false">
      </div>
    </div>
  </div>`;
}
function openXImageViewer(url, label) {
  if (!isXPostMediaUrl(url)) return;
  xImageViewer = {url, label: label || '推文图片', zoom: 1};
  renderXImageViewer();
}
function closeXImageViewer() {
  xImageViewer = {url: '', label: '', zoom: 1};
  renderXImageViewer();
}
function zoomXImageViewer(delta) {
  if (!xImageViewer.url) return;
  xImageViewer.zoom = clampXImageZoom((xImageViewer.zoom || 1) + delta);
  renderXImageViewer();
}
function xMediaGroups(r) {
  const post = xPostMeta(r);
  return [
    {key:'reply_to_media', label:'原帖图片', items:xMediaItems(post.reply_to_media)},
    {key:'quoted_media', label:'引用图片', items:xMediaItems(post.quoted_media)},
    {key:'media', label:'推文图片', items:xMediaItems(post.media)}
  ].filter(group => group.items.length);
}
function xAllMediaItems(r) {
  return xMediaGroups(r).flatMap(group => group.items);
}
function renderXMediaStrip(r) {
  const media = xAllMediaItems(r).filter(item => item.url);
  if (!media.length) return '';
  const thumbs = media.slice(0, 1).map(item => `<span class="x-media-thumb"><img src="${esc(xMediaDisplayUrl(item.url))}" data-x-media-request="1" alt="推文图片" loading="lazy" fetchpriority="low" decoding="async"></span>`).join('');
  const more = media.length > 1 ? `<span class="x-media-more">+${media.length - 1}</span>` : '';
  return `<div class="x-media-strip">${thumbs}${more}</div>`;
}
function renderXMediaGallery(groups) {
  groups = (groups || []).filter(group => group.items && group.items.length);
  if (!groups.length) return '';
  return `<div class="x-media-gallery">${groups.map(group => {
    const tiles = group.items.map(item => {
      return `<button type="button" class="x-media-tile" data-x-image-url="${esc(item.url)}" data-x-image-label="${esc(group.label)}" title="查看图片">
        <span class="x-media-frame"><img src="${esc(xMediaDisplayUrl(item.url))}" data-x-media-request="1" alt="${esc(group.label)}" loading="lazy" fetchpriority="low" decoding="async"></span>
      </button>`;
    }).join('');
    return `<div class="x-media-group"><div class="x-media-label">${esc(group.label)}</div><div class="x-media-grid">${tiles}</div></div>`;
  }).join('')}</div>`;
}
function stripXCurrentPostHeader(text) {
  const lines = String(text || '').split('\n');
  if (!lines.length) return '';
  const firstLine = lines[0] || '';
  const isEmojiHeader = /^[\p{Emoji}\uFE0F\u200D]+\s*\*\*.+?\*\*/u.test(firstLine);
  if (xLineRole(firstLine) === 'reply' || firstLine.includes('｜') || firstLine.includes('|') || isEmojiHeader) {
    return lines.slice(1).join('\n').trim();
  }
  return String(text || '').trim();
}
function renderXDetail(r) {
  const thread = parseThread(r.content || '');
  const groups = xMediaGroups(r);
  const originalGroups = groups.filter(group => group.key === 'reply_to_media' || group.key === 'quoted_media');
  const mainGroups = groups.filter(group => group.key === 'media');
  if (thread.originalPost && thread.reply) {
    const replyBody = stripXCurrentPostHeader(thread.reply) || thread.reply;
    return `<div class="thread-card">
      <div class="thread-reply"><div class="thread-reply-content">${esc(replyBody)}</div>${renderXMediaGallery(mainGroups)}</div>
      <div class="thread-original"><div class="thread-original-content">${esc(thread.originalPost)}</div>${renderXMediaGallery(originalGroups)}</div>
    </div>`;
  }
  const lines = String(r.content || '').split('\n');
  const body = stripXCurrentPostHeader(lines.join('\n')) || '（无正文）';
  return `<div class="content">${esc(body)}</div>${renderXMediaGallery(groups)}`;
}
function renderXRow(r) {
  const key = xRecordKey(r);
  const s = summarizeXRecord(r);
  const open = xExpandedRecordKey === key;
  return `<article class="x-row ${open ? 'open' : ''}" data-x-key="${esc(key)}" aria-expanded="${open ? 'true' : 'false'}">
    <div class="x-avatar">${esc(s.initial)}</div>
    <div class="x-copy">
      <div class="x-line"><span class="x-author">${esc(s.author)}</span><span class="x-handle">${esc(s.label)}</span>${s.time ? `<span class="x-time">${esc(s.time)}</span>` : ''}</div>
      ${open ? '' : `<div class="x-preview">${esc(s.preview)}</div>${renderXMediaStrip(r)}`}
    </div>
    <div class="x-badges"><span class="x-chevron">›</span></div>
    ${open ? `<div class="x-detail">${renderXDetail(r)}</div>` : ''}
  </article>`;
}
function renderXMonitor(records) {
  if (!records.length) return '<div class="empty">暂无推特监控消息</div>';
  const total = activeCategoryTotal();
  const latest = records[0]?.time || '';
  const oldest = records[records.length - 1]?.time || '';
  const limit = messagePageLimit('x_monitor');
  const totalPages = Math.max(1, Math.ceil((total || records.length || 1) / limit));
  const page = Math.min(totalPages, Math.floor(xPageOffset / limit) + 1);
  return `<section class="sector-cloud x-monitor-panel">
    <div class="x-monitor-head">
      <div><div class="x-monitor-title">推特监控流</div><div class="x-monitor-sub">${latest ? '最新 ' + esc(latest) : '等待监控数据'}${oldest ? ' · 最早 ' + esc(oldest) : ''}</div></div>
      <div class="x-monitor-metrics"><span class="x-metric">第 ${page} / ${totalPages} 页</span><span class="x-metric">本页 ${records.length}</span></div>
    </div>
    <div class="x-list">${records.map(r => renderXRow(r)).join('')}</div>
  </section>${renderHistoryControls(records)}`;
}
function render() {
  if (activeCategory === 'indices') {
    $('feed').innerHTML = renderIndicesPanel();
    return;
  }
  if (activeCategory === 'practice') {
    renderPracticePage();
    renderPracticeCalendarModal();
    return;
  }
  const records = filtered();
  if (activeCategory === 'us_ratings') {
    $('feed').innerHTML = renderUsRatingDay(records) + renderHistoryControls(records);
    restoreRatingDetail();
    return;
  }
  if (activeCategory === 'x_monitor') {
    $('feed').innerHTML = renderXMonitor(records);
    return;
  }
  if (activeCategory === 'market_monitor') {
    $('feed').innerHTML = renderMarketMonitor(records);
    return;
  }
  $('feed').innerHTML = records.length
    ? records.map(r => renderCard(r)).join('') + renderHistoryControls(records)
    : '<div class="empty">暂无匹配消息</div>';
}
function parseThread(content) {
  const lines = content.split('\n');
  let originalPost = null, reply = null, inOriginal = false, inReply = false;
  const originalLines = [], replyLines = [];
  for (const line of lines) {
    const trimmed = line.trim();
    const marker = normalizeXMarker(trimmed);
    if (!marker || /^[-━└]+$/.test(marker)) continue;
    if (/^(?:引用)?原[贴帖](?:\s*[|｜:：]|$)/.test(marker)) {
      inOriginal = true; inReply = false;
      if (marker.includes('|') || marker.includes('｜') || marker.includes('：') || marker.includes(':')) originalLines.push(marker);
      continue;
    }
    if (/^回复(?:\s*[|｜:：]|$)/.test(marker)) {
      inOriginal = false; inReply = true;
      if (marker.includes('|') || marker.includes('｜') || marker.includes('：') || marker.includes(':')) replyLines.push(marker);
      continue;
    }
    const bodyLine = trimmed.replace(/^[│┃]\s?/u, '').trim();
    if (inOriginal && bodyLine) originalLines.push(bodyLine);
    else if (inReply && bodyLine) replyLines.push(bodyLine);
  }
  if (originalLines.length > 0 && replyLines.length > 0) {
    originalPost = originalLines.join('\n').trim();
    reply = replyLines.join('\n').trim();
  }
  return { originalPost, reply };
}
function renderCard(r) {
  const thread = parseThread(r.content);
  if (thread.originalPost && thread.reply) {
    return `<article class="card thread-card">
        <div class="mobile-head"><span>${esc(r.time)}</span></div>
        <div class="thread-original"><div class="thread-original-content">${esc(thread.originalPost)}</div></div>
        <div class="thread-reply"><div class="thread-reply-content">${esc(thread.reply)}</div></div>
      </article>`;
  }
  const lines = r.content.split('\n');
  let header = '', body = '';
  const firstLine = lines[0] || '';
  const isEmojiHeader = /^[\p{Emoji}\uFE0F\u200D]+\s*\*\*.+?\*\*/u.test(firstLine);
  if (lines.length > 0 && (firstLine.includes('｜') || firstLine.includes('|') || isEmojiHeader)) {
    header = firstLine; body = lines.slice(1).join('\n').trim();
  } else { body = r.content; }
  return `<article class="card${header ? ' has-header' : ''}">
      <div class="mobile-head"><span>${esc(r.time)}</span></div>
      ${header ? `<div class="post-header">${esc(header)}</div>` : ''}
      <div class="content">${esc(body)}</div>
    </article>`;
}
document.addEventListener('click', event => {
  const usMarketAction = event.target.closest('[data-us-market-action]');
  if (usMarketAction) {
    event.preventDefault();
    event.stopPropagation();
    if (usMarketAction.dataset.usMarketAction === 'toggle') {
      usMarketSummaryExpanded = !usMarketSummaryExpanded;
      const actionLabel = usMarketSummaryExpanded ? '收起' : '展开';
      const summaryCard = usMarketAction.closest('.us-market-summary-card');
      const summaryBody = summaryCard?.querySelector('.us-market-summary-body');
      summaryCard?.classList.toggle('collapsed', !usMarketSummaryExpanded);
      summaryCard?.classList.toggle('open', usMarketSummaryExpanded);
      if (summaryBody) summaryBody.hidden = !usMarketSummaryExpanded;
      usMarketAction.setAttribute('aria-expanded', usMarketSummaryExpanded ? 'true' : 'false');
      usMarketAction.setAttribute('aria-label', `${actionLabel}隔夜美股盘面总结`);
    }
    return;
  }
  const logAction = event.target.closest('[data-practice-log-action]');
  if (logAction) {
    event.preventDefault();
    event.stopPropagation();
    if (logAction.dataset.practiceLogAction === 'close') {
      practiceLogDetailKey = '';
      if (activeCategory === 'practice') render();
    }
    return;
  }
  if (practiceLogDetailKey && event.target.classList && event.target.classList.contains('practice-log-detail-backdrop')) {
    practiceLogDetailKey = '';
    if (activeCategory === 'practice') render();
    return;
  }
  const logTrigger = event.target.closest('[data-practice-log-key]');
  if (logTrigger) {
    event.preventDefault();
    event.stopPropagation();
    practiceLogDetailKey = logTrigger.dataset.practiceLogKey || '';
    if (activeCategory === 'practice') render();
    return;
  }
  const ruleAction = event.target.closest('[data-practice-rule-action]');
  if (ruleAction) {
    event.preventDefault();
    event.stopPropagation();
    practiceRuleNoteOpen = ruleAction.dataset.practiceRuleAction === 'open';
    if (activeCategory === 'practice') render();
    return;
  }
  if (practiceRuleNoteOpen && event.target.classList && event.target.classList.contains('practice-rule-backdrop')) {
    practiceRuleNoteOpen = false;
    if (activeCategory === 'practice') render();
    return;
  }
  const calendarAction = event.target.closest('[data-practice-calendar-action]');
  if (calendarAction) {
    event.preventDefault();
    event.stopPropagation();
    const action = calendarAction.dataset.practiceCalendarAction;
    if (action === 'close') closePracticeCalendar();
    else if (action === 'prev') shiftPracticeCalendarMonth(-1);
    else if (action === 'next') shiftPracticeCalendarMonth(1);
    else if (action === 'clear-day') {
      practiceCalendarSelectedDate = '';
      renderPracticeCalendarModal();
    }
    return;
  }
  const calendarDate = event.target.closest('[data-practice-calendar-date]');
  if (calendarDate) {
    event.preventDefault();
    event.stopPropagation();
    const nextDate = calendarDate.dataset.practiceCalendarDate || '';
    practiceCalendarSelectedDate = practiceCalendarSelectedDate === nextDate ? '' : nextDate;
    renderPracticeCalendarModal();
    return;
  }
  if (practiceCalendarOpen && !event.target.closest('.practice-calendar-card') && !event.target.closest('[data-practice-calendar-curve]') && !event.target.closest('.practice-calendar-open-btn')) {
    closePracticeCalendar();
    return;
  }
  const viewerAction = event.target.closest('[data-x-viewer-action]');
  if (viewerAction) {
    event.preventDefault();
    event.stopPropagation();
    const action = viewerAction.dataset.xViewerAction;
    if (action === 'close') closeXImageViewer();
    else if (action === 'zoom-in') zoomXImageViewer(0.25);
    else if (action === 'zoom-out') zoomXImageViewer(-0.25);
    return;
  }
  if (event.target.classList && event.target.classList.contains('x-image-viewer-backdrop')) {
    closeXImageViewer();
    return;
  }
  const imageTrigger = event.target.closest('[data-x-image-url]');
  if (imageTrigger) {
    event.preventDefault();
    event.stopPropagation();
    openXImageViewer(imageTrigger.dataset.xImageUrl || '', imageTrigger.dataset.xImageLabel || '推文图片');
    return;
  }
  if (activeCategory === 'x_monitor') {
    if (event.target.closest('.x-detail')) return;
    const row = event.target.closest('.x-row[data-x-key]');
    if (!row) return;
    xExpandedRecordKey = xExpandedRecordKey === row.dataset.xKey ? '' : row.dataset.xKey;
    render();
    return;
  }
  if (activeCategory === 'market_monitor') {
    if (event.target.closest('.market-card-detail')) return;
    const card = event.target.closest('.market-monitor-card[data-market-key]');
    if (!card) return;
    marketExpandedRecordKey = marketExpandedRecordKey === card.dataset.marketKey ? '' : card.dataset.marketKey;
    render();
  }
});
document.addEventListener('keydown', event => {
  if (practiceLogDetailKey && event.key === 'Escape') {
    event.preventDefault();
    practiceLogDetailKey = '';
    if (activeCategory === 'practice') render();
    return;
  }
  if (practiceRuleNoteOpen && event.key === 'Escape') {
    event.preventDefault();
    practiceRuleNoteOpen = false;
    if (activeCategory === 'practice') render();
    return;
  }
  if (practiceCalendarOpen && event.key === 'Escape') {
    event.preventDefault();
    closePracticeCalendar();
    return;
  }
  if (!xImageViewer.url) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeXImageViewer();
  } else if (event.key === '+' || event.key === '=') {
    event.preventDefault();
    zoomXImageViewer(0.25);
  } else if (event.key === '-') {
    event.preventDefault();
    zoomXImageViewer(-0.25);
  }
});
async function loadVersionStatus() {
  const status = $('versionStatus');
  const value = $('versionValue');
  if (!status || !value) return;
  try {
    const response = await fetch('/api/version', {
      credentials: 'same-origin',
      cache: 'no-store'
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const current = String(payload.current_version || 'dev');
    const latest = payload.latest_version ? String(payload.latest_version) : '';
    const currentLabel = current === 'dev' ? '开发版' : current;
    if (payload.check_ok !== true) {
      value.textContent = currentLabel;
      status.dataset.state = 'error';
      status.title = `当前版本 ${currentLabel}；Docker Hub 最新版本检查失败`;
    } else if (payload.update_available === true && latest) {
      value.textContent = `${currentLabel} → ${latest}`;
      status.dataset.state = 'update';
      status.title = `发现新版本 ${latest}，点击查看 Docker Hub`;
    } else if (payload.update_available === false) {
      value.textContent = currentLabel;
      status.dataset.state = 'current';
      status.title = `当前版本 ${currentLabel}，已是最新版本`;
    } else {
      value.textContent = latest ? `${currentLabel} · 最新 ${latest}` : currentLabel;
      status.dataset.state = 'checking';
      status.title = latest
        ? `当前为${currentLabel}，Docker Hub 最新版本为 ${latest}`
        : `当前版本 ${currentLabel}`;
    }
    status.setAttribute('aria-label', status.title);
  } catch (error) {
    status.dataset.state = 'error';
    status.title = '版本信息加载失败，点击查看 Docker Hub';
    status.setAttribute('aria-label', status.title);
    console.error('Version check failed', error);
  }
}
async function loadDashboardBootstrap() {
  try {
    const response = await fetch('/api/dashboard/bootstrap', {
      credentials: 'same-origin',
      cache: 'no-store'
    });
    if (!response.ok) return;
    const payload = await response.json();
    US_FEATURES_ENABLED = payload.us_features_enabled === true;
  } catch (error) {
    console.error('Dashboard bootstrap failed', error);
  }
}

async function startDashboard() {
  restoreViewState();
  loadVersionStatus();
  const categoryBeforeBootstrap = activeCategory;
  const needsFeatureCheck = US_FEATURE_CATEGORIES.has(categoryBeforeBootstrap);
  const bootstrapPromise = loadDashboardBootstrap();
  const reportLoadError = err => { if (!err || err.name !== 'AbortError') console.error(err); };
  if (!needsFeatureCheck && hasWarmData(activeCategory)) render();
  let initialLoadPromise = load({
    background: hasWarmData(activeCategory),
    updateTabs: true,
    waitFor: needsFeatureCheck ? bootstrapPromise : null,
  });
  initialLoadPromise.catch(reportLoadError);

  await bootstrapPromise;
  activeCategory = normalizeActiveCategory(activeCategory);
  const categoryChanged = activeCategory !== categoryBeforeBootstrap;
  if (categoryChanged) {
    if (pendingLoadController) pendingLoadController.abort();
    initialLoadPromise = null;
  }
  if (location.pathname + location.search !== currentViewUrl()) syncViewUrl();
  renderTabs();
  if (hasWarmData(activeCategory)) render();
  if (!initialLoadPromise) {
    initialLoadPromise = load();
    initialLoadPromise.catch(reportLoadError);
  }
  setInterval(() => autoRefresh().catch(err => { if (!err || err.name !== 'AbortError') console.error(err); }), AUTO_REFRESH_TICK_MS);
}

startDashboard();
