<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AdminLogin from './AdminLogin.vue'
import AdminPageTitle from './AdminPageTitle.vue'
import ThemeToggle from './ThemeToggle.vue'
import { useAdminConfig } from '../composables/useAdminConfig.js'

document.title = '牛牛1号 · 策略回测'

const NIUONE_BACKTEST_PROTOCOL_VERSION = 'niuone-backtest-v32'

const route = useRoute()
const { state, errorMessage, refresh, authenticate } = useAdminConfig()
const strategyId = computed(() => String(route.params.strategyId || ''))
const options = ref(null)
const optionsError = ref('')
const starting = ref(false)
const cancelling = ref(false)
const taskError = ref('')
const job = ref(null)
const form = reactive({
  startDate: '', endDate: '', adjustment: 'qfq', source: 'auto',
})
let pollTimer = 0

const strategy = computed(() => (
  (options.value?.strategies || []).find(item => item.id === strategyId.value) || null
))
const limits = computed(() => options.value?.limits || {})
const expectedProtocolVersion = computed(() => (
  strategy.value?.id === 'niuone'
    ? String(strategy.value?.backtest_protocol_version || NIUONE_BACKTEST_PROTOCOL_VERSION)
    : String(strategy.value?.backtest_protocol_version || '')
))
const staleResult = computed(() => (
  job.value?.status === 'succeeded'
  && Boolean(expectedProtocolVersion.value)
  && String(job.value?.result?.protocol?.version || '') !== expectedProtocolVersion.value
))
const result = computed(() => (staleResult.value ? null : (job.value?.result || null)))
const statistics = computed(() => result.value?.selection?.statistics || {})
const diagnostics = computed(() => result.value?.selection?.diagnostics || {})
const universe = computed(() => result.value?.universe || {})
const signals = computed(() => result.value?.selection?.signals || [])
const trades = computed(() => result.value?.selection?.trades || [])
const portfolio = computed(() => result.value?.selection?.portfolio || {})
const entryRejectionRows = computed(() => Object.entries(
  statistics.value.entry_rejection_counts || {},
).map(([reason, count]) => ({ reason, count })))
const isStrategyPortfolio = computed(() => statistics.value.evaluation_mode === 'strategy_portfolio')
const isTradeLifecycle = computed(() => ['trade_lifecycle', 'strategy_portfolio'].includes(statistics.value.evaluation_mode))
const horizonRows = computed(() => Object.entries(statistics.value.by_horizon || {}).map(
  ([holding, item]) => ({ holding, ...item }),
))
const strategyRows = computed(() => Object.entries(statistics.value.by_strategy || {}).map(
  ([id, item]) => ({ id, ...item }),
))
const diagnosticRows = computed(() => Object.entries(diagnostics.value.by_strategy || {}).map(
  ([id, item]) => ({ id, ...item }),
))
const diagnosticPeriodRows = computed(() => Object.entries(
  diagnostics.value.periods || {},
).flatMap(([period, payload]) => Object.entries(payload?.by_strategy || {}).map(
  ([id, item]) => ({ period, id, ...item }),
)))
const qualityWarnings = computed(() => {
  const warnings = (result.value?.warnings || []).map(value => String(value || ''))
  const coverage = warnings.find(value => value.includes('historical universe coverage'))
  const partial = warnings.find(value => value.includes('partial universe fetched'))
  const rows = warnings
    .filter(value => value !== coverage && value !== partial)
    .map((value, index) => ({
      key: `warning-${index}-${value}`,
      label: warningLabel(value),
      text: warningText(value),
    }))
  if (coverage || partial) {
    const coverageText = coverage
      ? `历史行情覆盖率：${coverage.split(':').slice(1).join(':').trim()}`
      : '历史行情未完全覆盖自动候选范围'
    const missingCount = warningSymbolCount(partial)
    rows.unshift({
      key: 'historical-coverage',
      label: '行情覆盖',
      text: missingCount ? `${coverageText}；未获取：${missingCount} 只` : coverageText,
    })
  }
  return rows
})
const qualitySummary = computed(() => {
  const coverage = qualityWarnings.value.find(item => item.key === 'historical-coverage')
  const coverageText = coverage?.text?.split('；')[0] || '回测数据完整性说明'
  const biasCount = qualityWarnings.value.length - (coverage ? 1 : 0)
  return biasCount > 0 ? `${coverageText} · ${biasCount} 项偏差提示` : coverageText
})
const isActive = computed(() => ['queued', 'running'].includes(job.value?.status))
const canStart = computed(() => (
  state.value === 'ready'
  && strategy.value?.supported
  && form.startDate
  && form.endDate
  && !starting.value
  && !isActive.value
))
const canCancel = computed(() => isActive.value && !cancelling.value)
const strategyLabels = computed(() => {
  const ids = strategy.value?.strategy_ids || []
  const labels = strategy.value?.strategy_labels || []
  return new Map(ids.map((id, index) => [id, labels[index] || id]))
})

const phaseLabels = {
  queued: '等待执行', universe: '构建候选范围', preparing: '准备数据', fetching: '获取历史行情',
  annotating: '补充行业信息', normalizing: '整理历史行情', precomputing: '预计算技术指标', replay_cache: '校验选股回放缓存',
  rebuilding_context: '重建题材截面', scoring: '执行策略评分', evaluating: '回放选股信号', replaying_exits: '回放持仓退出',
  completed: '回测完成', failed: '回测失败', cancelled: '回测已终止',
}
const statusLabels = {
  queued: '排队中', running: '运行中', succeeded: '已完成', failed: '失败', cancelled: '已终止',
}
const signalStatusLabels = { evaluated: '已评估', skipped: '已跳过', rejected: '不可评估' }
const signalStatusReasonLabels = {
  unknown: '未记录具体原因',
  cooldown: '冷却期内重复信号',
  unknown_symbol: '候选股票不在历史行情范围',
  no_next_session: '回测区间内没有下一交易日',
  missing_next_session_bar: '缺少下一交易日行情',
  suspended_or_zero_volume: '下一交易日停牌或成交量为零',
  insufficient_forward_data: '缺少完整的后续收益区间',
  open_at_limit_up: '次日开盘涨停，无法按规则成交',
  position_open: '已有持仓，不重复买入',
  entry_pending: '已有待执行买入信号',
  holding_upgrade_missing_position: '阶段升级信号缺少对应持仓',
  markup_upgrade_same_day_add: '主升升级当日不重复加仓',
  markup_upgrade_early_done: '启动阶段升级加仓已完成',
  markup_upgrade_confirmed_done: '主升阶段升级加仓已完成',
  markup_upgrade_rule: '主升阶段升级加仓条件未满足',
  markup_rebalance_rule: '主升回补条件未满足',
  reversal_same_day_add: '牛牛试仓当日不重复加仓',
  reversal_upgrade_unconfirmed: '试仓尚未满足启动/主线升级条件',
  emerging_upgrade_unconfirmed: '启动观察仓尚未确认升级为主线',
  mixed_strategy_add: '不允许混合不同阶段的加仓路径',
  unsupported_strategy: '策略类型不支持组合定仓',
  markup_momentum_identity_block: '主升动量试仓不符合策略身份条件',
  missing_signal_close: '缺少信号日收盘价，无法校验次日执行',
  reversal_execution_gap: '试仓次日开盘跳空超过执行上限',
  markup_momentum_execution_gap: '主升动量试仓次日跳空超过执行上限',
  max_open_positions: '已达到当前风险档位的持仓数量上限',
  max_new_positions: '当日新仓数量已达当前风险档位上限',
  max_industry_positions: '同一主题持仓数量已达当前风险档位上限',
  market_risk_block: '市场状态禁止买入',
  missing_industry: '缺少主题/行业归属',
  structure_risk_block: '次日开盘后的结构止损距离不符合风控',
  missing_gap_buffer: '缺少跳空风险缓冲',
  risk_budget_unavailable: '动态风险预算不可用',
  target_position_reached: '当前持仓已达到该阶段风险仓位上限',
  below_board_lot: '风险预算不足 1 手，未成交',
  insufficient_cash: '现金不足或低于策略现金储备',
  entry_risk_rejected: '买入未通过风险定仓规则',
}
const diagnosticFamilyLabels = {
  risk_structure: '结构风险', daily_v_structure: '日线 V 型结构', price_structure: '价格结构',
  lifecycle_route: '生命周期路由', market_regime: '市场状态', leadership_quality: '龙头质量',
  theme_quality: '题材质量', other: '其他',
}
const lifecycleStageLabels = {
  brewing: '酝酿', markup: '主升', climax: '高潮', divergence: '分歧', fade: '退幕', unknown: '未识别',
}

function setTitle(value) {
  window.dispatchEvent(new CustomEvent('niuone:admin-title', { detail: { title: value } }))
}

function responseError(payload, fallback) {
  return new Error(String(payload?.error || fallback))
}

function applyDefaults() {
  const defaults = options.value?.defaults || {}
  if (!form.startDate) form.startDate = String(defaults.start_date || '')
  if (!form.endDate) form.endDate = String(defaults.end_date || '')
  if (!form.adjustment) form.adjustment = String(defaults.adjustment || 'qfq')
}

async function loadOptions() {
  optionsError.value = ''
  try {
    const response = await fetch('/api/admin/backtests/options', {
      credentials: 'same-origin', cache: 'no-store',
    })
    if (response.status === 403) {
      await refresh()
      return
    }
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload || !Array.isArray(payload.strategies)) {
      throw responseError(payload, '回测配置加载失败')
    }
    options.value = payload
    applyDefaults()
    setTitle(strategy.value ? `${strategy.value.label}回测` : '策略回测')
    if (strategy.value) await restoreLatestJob()
  } catch (error) {
    optionsError.value = error instanceof Error ? error.message : '回测配置加载失败'
  }
}

function stopPolling() {
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = 0
}

async function loadServerJob(expectedStrategyId = strategyId.value) {
  try {
    const response = await fetch(
      `/api/admin/backtests/latest/${encodeURIComponent(expectedStrategyId)}`,
      { credentials: 'same-origin', cache: 'no-store' },
    )
    if (response.status === 403) {
      await refresh()
      return
    }
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload) throw responseError(payload, '回测状态加载失败')
    if (expectedStrategyId !== strategyId.value) return
    taskError.value = ''
    job.value = payload.job || null
    if (['queued', 'running'].includes(payload.job?.status)) {
      pollTimer = window.setTimeout(() => loadServerJob(expectedStrategyId), 1200)
    }
  } catch (error) {
    if (expectedStrategyId !== strategyId.value) return
    taskError.value = error instanceof Error ? error.message : '回测状态加载失败'
    stopPolling()
  }
}

async function restoreLatestJob() {
  const requestedStrategy = strategyId.value
  stopPolling()
  taskError.value = ''
  await loadServerJob(requestedStrategy)
}

async function startBacktest() {
  if (!canStart.value) return
  const requestedStrategy = strategyId.value
  starting.value = true
  taskError.value = ''
  stopPolling()
  try {
    const body = new URLSearchParams({
      strategy_id: requestedStrategy,
      start_date: form.startDate,
      end_date: form.endDate,
      adjustment: form.adjustment,
      source: form.source,
    })
    const response = await fetch('/api/admin/backtests', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
        'X-NiuOne-Action': '1',
      },
      body: body.toString(),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.id) throw responseError(payload, '回测任务创建失败')
    if (requestedStrategy !== strategyId.value) return
    job.value = payload
    await loadServerJob(requestedStrategy)
  } catch (error) {
    taskError.value = error instanceof Error ? error.message : '回测任务创建失败'
  } finally {
    starting.value = false
  }
}

async function cancelBacktest() {
  const currentJobId = String(job.value?.id || '')
  const requestedStrategy = strategyId.value
  if (!canCancel.value || !currentJobId) return
  cancelling.value = true
  taskError.value = ''
  stopPolling()
  try {
    const response = await fetch(
      `/api/admin/backtests/${encodeURIComponent(currentJobId)}/cancel`,
      {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-NiuOne-Action': '1' },
      },
    )
    if (response.status === 403) {
      await refresh()
      return
    }
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.id) throw responseError(payload, '终止回测失败')
    if (requestedStrategy !== strategyId.value) return
    job.value = payload
  } catch (error) {
    taskError.value = error instanceof Error ? error.message : '终止回测失败'
    if (isActive.value) {
      pollTimer = window.setTimeout(() => loadServerJob(requestedStrategy), 1200)
    }
  } finally {
    cancelling.value = false
  }
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function formatUnsignedPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return `${Number(value).toFixed(2)}%`
}

function formatScore(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return Number(value).toFixed(1)
}

function formatPrice(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return Number(value).toFixed(2)
}

function formatMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return `¥${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatRatio(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return Number(value).toFixed(2)
}

function compactDateTime(value) {
  const text = String(value || '').trim()
  return text ? text.slice(0, 19).replace('T', ' ') : '—'
}

function formatDuration(value) {
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} 秒`
  const minutes = Math.floor(seconds / 60)
  const remaining = Math.round(seconds % 60)
  return `${minutes} 分 ${remaining} 秒`
}

function stockCode(value) {
  const matched = String(value || '').match(/\d{6}/)
  return matched ? matched[0] : '—'
}

function stockName(item) {
  const direct = String(item?.name || item?.stock_name || '').trim()
  if (direct) return direct
  const symbol = String(item?.symbol || '')
  const series = result.value?.data?.series || {}
  return String(series[symbol]?.name || series[stockCode(symbol)]?.name || '').trim() || '—'
}

function leaderBranchRows(values) {
  const rows = (Array.isArray(values) ? values : []).map((item, index) => {
    const symbol = String(item?.best_symbol || '')
    const name = stockName({
      name: item?.best_name || item?.best_stock_name,
      symbol,
    })
    const code = stockCode(symbol)
    const stockGroupKey = name === '—' ? code : name
    const bestDate = String(item?.best_date || '').slice(0, 10)
    return {
      ...item,
      stock_name: name,
      stock_code: code,
      stock_group_key: stockGroupKey,
      sort_date: /^\d{4}-\d{2}-\d{2}$/.test(bestDate) ? bestDate : '9999-12-31',
      source_order: index,
    }
  }).sort((left, right) => (
    left.stock_group_key.localeCompare(right.stock_group_key, 'zh-CN')
    || left.sort_date.localeCompare(right.sort_date)
    || String(left.industry || '').localeCompare(String(right.industry || ''), 'zh-CN')
    || left.source_order - right.source_order
  ))
  const groupSizes = rows.reduce((counts, item) => {
    counts.set(item.stock_group_key, (counts.get(item.stock_group_key) || 0) + 1)
    return counts
  }, new Map())
  return rows.map((item, index) => ({
    ...item,
    stock_group_start: index === 0
      || rows[index - 1].stock_group_key !== item.stock_group_key,
    stock_group_size: groupSizes.get(item.stock_group_key) || 1,
    row_key: [
      item.stock_group_key,
      item.sort_date,
      String(item.industry || ''),
      item.source_order,
    ].join('|'),
  }))
}

function percentClass(value) {
  if (value === null || value === undefined) return ''
  return Number(value) > 0 ? 'is-positive' : (Number(value) < 0 ? 'is-negative' : '')
}

function signalReturn(signal, holding) {
  return signal?.forward_returns?.[holding]?.net_return_pct
}

function strategyLabel(id) {
  return strategyLabels.value.get(id) || id || '未归类'
}

function strategyPathLabel(trade) {
  const path = Array.isArray(trade?.strategy_path) && trade.strategy_path.length
    ? trade.strategy_path
    : [trade?.strategy_id]
  return path.map(strategyLabel).join(' → ')
}

function diagnosticFamilyLabel(value) {
  const key = String(value || '')
  return diagnosticFamilyLabels[key] || key || '未分类'
}

function signedOffset(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return `${number > 0 ? '+' : ''}${number}`
}

function diagnosticThresholdLabel(values, fallback = null) {
  const thresholds = (Array.isArray(values) ? values : [])
    .map(item => Number(item?.threshold ?? item))
    .filter(Number.isFinite)
  if (!thresholds.length && fallback !== null && fallback !== undefined) {
    thresholds.push(Number(fallback))
  }
  const labels = [...new Set(thresholds)].sort((a, b) => a - b).map(formatScore)
  return labels.length > 1 ? `条件化 ${labels.join(' / ')}` : (labels[0] || '—')
}

function sensitivityThresholdLabel(item) {
  const values = Array.isArray(item?.applicable_thresholds)
    ? item.applicable_thresholds
    : [item?.threshold]
  return diagnosticThresholdLabel(values)
}

function compactDiagnosticCounts(values, labels = {}) {
  return Object.entries(values || {}).map(
    ([key, count]) => `${labels[key] || key} ${count}`,
  ).join(' · ') || '—'
}

function compactDiagnosticReasons(values, actionable = false) {
  const reasons = Array.isArray(values) ? values.filter(Boolean) : []
  return reasons.join(' · ') || (actionable ? '无（可执行）' : '—')
}

function signalStatusReason(reason) {
  const value = String(reason || '')
  if (!value) return '未记录具体原因'
  return signalStatusReasonLabels[value]
    || (/^[a-z][a-z0-9_]*$/.test(value) ? '其他策略限制' : value)
}

function warningSymbolCount(value) {
  const symbols = String(value || '').match(/(?:sh|sz|bj)\d{6}/gi) || []
  return new Set(symbols.map(symbol => symbol.toLowerCase())).size
}

function warningText(value) {
  const text = String(value || '')
  if (text.includes('look-ahead bias')) return '历史行业分类暂使用当前行业标签，结果可能存在前视偏差。'
  if (text.includes('fallback source')) {
    const count = warningSymbolCount(text)
    return count ? `部分标的发生行情源降级：${count} 只` : '部分标的发生行情源降级。'
  }
  if (text.includes('partial universe fetched because')) return '部分标的历史行情获取失败，本次回测仅使用成功获取的标的。'
  if (text.includes('current classification fallback used: iwencai_current_industry_concept')) return '当前行业/概念分类已改用问财备用源。'
  if (text.includes('stale current classification snapshot used')) {
    const snapshotDate = text.split(':').slice(1).join(':').trim()
    return snapshotDate && snapshotDate !== 'unknown date'
      ? `当前行业/概念分类使用了过期快照：${snapshotDate}。`
      : '当前行业/概念分类使用了日期未知的过期快照。'
  }
  if (text.includes('survivorship bias')) return '自动候选范围使用当前上市状态，无法补回已退市股票或精确还原历史上市成员，结果可能存在幸存者偏差。'
  if (text.includes('NiuOne structural stops use the completed daily low')) return '牛牛结构止损使用已完成日 K 的最低价判断触发，并以止损价或开盘价作为成交参考；其他退出使用收盘价。日 K 无法还原盘中精确触发时点与排队优先级。'
  if (text.includes('NiuOne entries use 100% of the deterministic maximum risk-permitted')) return '牛牛回测按风控允许的确定性最大整手数量下单；模拟交易使用模型指定股数，超出上限时会拒单而非自动缩量。因此本回测的组合收益和回撤反映最大定仓情景。'
  if (text.includes('NiuOne aggressive backtest profile increases account-risk')) return '牛牛进取回测参数提高账户风险、组合/题材敞口与持仓数量预算，但不会放宽价格形态、结构止损、涨停或 T+1 规则。'
  if (text.includes('completed daily bars at the close')) return '卖出规则使用每日收盘后可见的日 K 数据回放，触发时按当日收盘价估算成交；日 K 无法还原盘中精确触发时点与排队次序。'
  if (text.includes('historical universe coverage')) return `历史行情覆盖率：${text.split(':').slice(1).join(':').trim()}`
  if (text.includes('selection replay cache could not be persisted')) return '选股回放缓存未能持久化；本次回测已使用内存中的回放数据正常完成。'
  return text
}

function warningLabel(value) {
  const text = String(value || '')
  if (text.includes('look-ahead bias')) return '前视偏差'
  if (text.includes('survivorship bias')) return '幸存者偏差'
  if (text.includes('fallback source')) return '行情源降级'
  if (text.includes('partial universe fetched because')) return '行情缺失'
  if (text.includes('current classification fallback used: iwencai_current_industry_concept')) return '分类源降级'
  if (text.includes('stale current classification snapshot used')) return '分类快照过期'
  if (text.includes('NiuOne structural stops use the completed daily low')) return '结构止损假设'
  if (text.includes('NiuOne entries use 100% of the deterministic maximum risk-permitted')) return '定仓差异'
  if (text.includes('NiuOne aggressive backtest profile increases account-risk')) return '进取参数'
  if (text.includes('completed daily bars at the close')) return '卖出成交假设'
  if (text.includes('selection replay cache could not be persisted')) return '缓存降级'
  return '其他提示'
}

watch(state, current => {
  if (current === 'ready') loadOptions()
  else if (current === 'login') setTitle('回测验证')
  else if (current === 'error') setTitle('回测加载失败')
})

watch(strategyId, async () => {
  stopPolling()
  job.value = null
  taskError.value = ''
  if (options.value) {
    setTitle(strategy.value ? `${strategy.value.label}回测` : '策略回测')
    if (strategy.value) await restoreLatestJob()
  }
})

watch(() => form.adjustment, value => {
  if (value !== 'none' && form.source === 'sina') form.source = 'auto'
})

onMounted(refresh)
onBeforeUnmount(stopPolling)
</script>

<template>
  <header class="admin-header">
    <div class="admin-header-inner">
      <div><div class="eyebrow">牛牛1号 · 策略历史回测</div><AdminPageTitle /></div>
      <div class="admin-header-actions">
        <ThemeToggle button-id="backtestThemeToggle" button-class="admin-theme-toggle" />
        <RouterLink class="toplink" to="/admin/settings/stock-strategy">返回策略设置</RouterLink>
        <a class="toplink" href="/">返回首页</a>
      </div>
    </div>
  </header>

  <main class="admin-main backtest-page" aria-live="polite">
    <div v-if="state === 'loading'" class="admin-loading">回测页面加载中…</div>
    <AdminLogin v-else-if="state === 'login'" :authenticate="authenticate" />
    <div v-else-if="state === 'error'" class="errmsg">{{ errorMessage || '回测页面加载失败' }}</div>

    <template v-else-if="state === 'ready'">
      <div v-if="optionsError" class="errmsg">{{ optionsError }}</div>
      <div v-else-if="!options" class="admin-loading">回测配置加载中…</div>
      <div v-else-if="!strategy" class="errmsg">未找到策略“{{ strategyId }}”。请从策略设置卡片进入回测。</div>

      <template v-else>
        <section class="backtest-hero" :style="{'--strategy-color': strategy.color || '#60a5fa'}">
          <div class="backtest-hero-copy">
            <span class="backtest-strategy-dot" />
            <div><h2>{{ strategy.label }}</h2><p>{{ strategy.desc }}</p></div>
          </div>
          <div v-if="strategy.strategy_labels?.length" class="backtest-tags">
            <span v-for="item in strategy.strategy_labels" :key="item">{{ item }}</span>
          </div>
        </section>

        <div v-if="!strategy.supported" class="backtest-notice is-warning">
          {{ strategy.unsupported_reason || '该策略暂不支持历史回测。' }}
        </div>

        <template v-else>
          <form class="backtest-form" @submit.prevent="startBacktest">
            <div class="backtest-form-head">
              <div><h2>回测参数</h2><p v-if="strategy.id === 'niuone'">无需输入股票，系统按历史行情自主选股；牛牛战法固定使用进取风险参数和 100 万元独立初始资金，严格回放风险定仓、阶段升级加仓、T+1、持仓/主题/总仓约束及策略卖出。回测与模拟账户完全隔离，历史日 K 实时获取且不使用本地缓存。</p><p v-else>无需输入股票，系统按历史行情自主选股；收盘信号于次日开盘买入，与模拟账户及持仓完全隔离。历史日 K 按所选区间实时获取，不使用本地日 K 缓存。</p></div>
              <span>最长 {{ limits.max_range_days || 366 }} 天</span>
            </div>
            <div class="backtest-fields">
              <label><span>开始日期</span><input v-model="form.startDate" type="date" required></label>
              <label><span>结束日期</span><input v-model="form.endDate" type="date" required></label>
              <label>
                <span>复权方式</span>
                <select v-model="form.adjustment">
                  <option value="qfq">前复权</option><option value="hfq">后复权</option><option value="none">不复权</option>
                </select>
              </label>
              <label>
                <span>行情来源</span>
                <select v-model="form.source">
                  <option value="auto">自动（东方财富 → 腾讯 → 新浪）</option><option value="eastmoney">东方财富</option><option value="tencent">腾讯</option>
                  <option value="sina" :disabled="form.adjustment !== 'none'">新浪（仅不复权）</option>
                </select>
              </label>
            </div>
            <div class="backtest-actions">
              <button v-if="isActive" class="backtest-cancel" type="button" :disabled="!canCancel" @click="cancelBacktest">
                {{ cancelling ? '正在终止…' : '终止回测' }}
              </button>
              <button v-else class="backtest-start" type="submit" :disabled="!canStart">
                {{ starting ? '正在创建…' : '开始回测' }}
              </button>
            </div>
          </form>

          <div v-if="taskError" class="errmsg">{{ taskError }}</div>

          <section v-if="job && isActive" class="backtest-progress-card">
            <div class="backtest-progress-head">
              <div>
                <span :class="['backtest-status', `is-${job.status}`]">{{ statusLabels[job.status] || job.status }}</span>
                <h2>{{ phaseLabels[job.phase] || job.phase || '准备回测' }}</h2>
                <p>{{ job.message || '正在处理' }}</p>
              </div>
              <strong>{{ Math.max(0, Math.min(100, Number(job.progress) || 0)) }}%</strong>
            </div>
            <div class="backtest-progress-track" role="progressbar" aria-label="回测进度" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="Number(job.progress) || 0">
              <span :style="{width: `${Math.max(0, Math.min(100, Number(job.progress) || 0))}%`}" />
            </div>
            <div class="backtest-timestamps">
              <span>开始于 {{ compactDateTime(job.started_at || job.created_at) }}</span>
              <span v-if="job.trading_date">交易日 {{ job.trading_date }}</span>
              <span v-if="job.day_elapsed_seconds !== null && job.day_elapsed_seconds !== undefined">本日耗时 {{ formatDuration(job.day_elapsed_seconds) }}</span>
              <span v-if="job.eta_seconds !== null && job.eta_seconds !== undefined">预计剩余 {{ formatDuration(job.eta_seconds) }}</span>
            </div>
            <div v-if="job.error" class="errmsg">{{ job.error }}</div>
          </section>
          <section v-else-if="job" :class="['backtest-job-summary', `is-${job.status}`]">
            <span :class="['backtest-status', `is-${job.status}`]">{{ statusLabels[job.status] || job.status }}</span>
            <strong>{{ staleResult ? '旧版结果已失效' : (job.status === 'succeeded' ? '结果已加载' : (job.message || phaseLabels[job.phase] || '任务已结束')) }}</strong>
            <small>{{ job.finished_at ? `完成于 ${compactDateTime(job.finished_at)}` : compactDateTime(job.created_at) }}</small>
            <div v-if="job.error" class="errmsg">{{ job.error }}</div>
          </section>

          <div v-if="staleResult" class="backtest-notice is-warning">
            当前结果由旧版牛牛回测协议生成，已停止展示，避免把阶段错配结果误认为当前策略。请重启 Dashboard 后重新运行回测。
          </div>

          <template v-if="result">
            <details v-if="qualityWarnings.length" class="backtest-quality">
              <summary>
                <span class="backtest-quality-icon">!</span>
                <span class="backtest-quality-copy"><strong>数据质量与偏差说明</strong><small>{{ qualitySummary }}</small></span>
                <span class="backtest-quality-count">{{ qualityWarnings.length }} 项</span>
              </summary>
              <div class="backtest-quality-details">
                <div v-for="item in qualityWarnings" :key="item.key">
                  <strong>{{ item.label }}</strong><span>{{ item.text }}</span>
                </div>
              </div>
            </details>

            <section class="backtest-overview">
              <div class="backtest-scope-summary">
                <span>候选范围</span><strong>{{ universe.configured_scope_label || '当前设置' }}</strong>
                <small>{{ universe.reference_symbol_count || 0 }} 只参考 · {{ universe.eligible_symbol_count || 0 }} 只可产生信号</small>
              </div>
              <div><span>选股信号</span><strong>{{ statistics.signal_count || 0 }}</strong></div>
              <template v-if="isStrategyPortfolio">
                <div><span>组合净收益</span><strong :class="percentClass(portfolio.total_return_pct)">{{ formatPercent(portfolio.total_return_pct) }}</strong></div>
                <div><span>最大回撤</span><strong :class="percentClass(portfolio.max_drawdown_pct)">{{ formatPercent(portfolio.max_drawdown_pct) }}</strong></div>
                <div><span>期末持仓</span><strong>{{ portfolio.open_position_count || 0 }}</strong></div>
              </template>
              <template v-else-if="isTradeLifecycle">
                <div><span>实际买入</span><strong>{{ statistics.evaluated_signal_count || 0 }}</strong></div>
                <div><span>完整交易</span><strong>{{ statistics.completed_trade_count || 0 }}</strong></div>
                <div><span>期末持仓</span><strong>{{ statistics.open_trade_count || 0 }}</strong></div>
              </template>
              <template v-else>
                <div><span>可评估信号</span><strong>{{ statistics.evaluated_signal_count || 0 }}</strong></div>
                <div><span>冷却期跳过</span><strong>{{ statistics.duplicate_signal_count || 0 }}</strong></div>
                <div><span>无法成交/评估</span><strong>{{ statistics.rejected_signal_count || 0 }}</strong></div>
              </template>
            </section>

            <section v-if="isStrategyPortfolio" class="backtest-result-card">
              <div class="backtest-result-head"><div><h2>策略组合</h2><p>按实际资金、风险仓位和交易批次计算；加仓、减仓、费用及期末持仓均计入组合净值。</p></div></div>
              <div class="backtest-table-wrap"><table>
                <thead><tr><th>初始资金</th><th>期末权益</th><th>期末现金</th><th>持仓市值</th><th>组合净收益</th><th>最大回撤</th><th>开仓</th><th>加仓</th><th>卖出</th></tr></thead>
                <tbody><tr>
                  <td>{{ formatMoney(portfolio.initial_cash) }}</td><td>{{ formatMoney(portfolio.final_equity) }}</td><td>{{ formatMoney(portfolio.final_cash) }}</td><td>{{ formatMoney(portfolio.final_market_value) }}</td>
                  <td :class="percentClass(portfolio.total_return_pct)">{{ formatPercent(portfolio.total_return_pct) }}</td><td :class="percentClass(portfolio.max_drawdown_pct)">{{ formatPercent(portfolio.max_drawdown_pct) }}</td>
                  <td>{{ portfolio.open_order_count || 0 }}</td><td>{{ portfolio.add_order_count || 0 }}</td><td>{{ portfolio.sell_order_count || 0 }}</td>
                </tr></tbody>
              </table></div>
              <div class="backtest-result-head"><div><h2>风险与资金效率</h2><p>年化指标按实际回测交易日折算，夏普/索提诺未扣无风险利率；换手率为买卖成交额均值占期间平均权益的比例。</p></div></div>
              <div class="backtest-table-wrap"><table>
                <thead><tr><th>交易日</th><th>年化收益</th><th>年化波动</th><th>夏普</th><th>索提诺</th><th>卡玛</th><th>平均仓位</th><th>最高仓位</th><th>换手率</th></tr></thead>
                <tbody><tr>
                  <td>{{ portfolio.trading_session_count || 0 }}</td>
                  <td :class="percentClass(portfolio.annualized_return_pct)">{{ formatPercent(portfolio.annualized_return_pct) }}</td>
                  <td>{{ formatUnsignedPercent(portfolio.annualized_volatility_pct) }}</td>
                  <td>{{ formatRatio(portfolio.sharpe_ratio) }}</td><td>{{ formatRatio(portfolio.sortino_ratio) }}</td><td>{{ formatRatio(portfolio.calmar_ratio) }}</td>
                  <td>{{ formatUnsignedPercent(portfolio.average_exposure_pct) }}</td><td>{{ formatUnsignedPercent(portfolio.max_exposure_pct) }}</td><td>{{ formatUnsignedPercent(portfolio.turnover_pct) }}</td>
                </tr></tbody>
              </table></div>
            </section>

            <section v-if="isStrategyPortfolio && entryRejectionRows.length" class="backtest-result-card">
              <div class="backtest-result-head"><div><h2>买入未成交归因</h2><p>汇总信号产生后，因涨停、仓位、风险预算、现金或组合约束而未能实际买入的原因。</p></div></div>
              <div class="backtest-table-wrap"><table>
                <thead><tr><th>原因</th><th>信号数</th></tr></thead>
                <tbody><tr v-for="row in entryRejectionRows" :key="row.reason">
                  <td>{{ signalStatusReason(row.reason) }}</td><td>{{ row.count }}</td>
                </tr></tbody>
              </table></div>
            </section>

            <section class="backtest-result-card backtest-diagnostics-card">
              <div class="backtest-result-head">
                <div><h2>未入选原因</h2><p>只做诊断展示，不放宽策略评分、买点或风控条件。“通过全部条件”为{{ isTradeLifecycle ? '子策略信号上限与同股持仓去重' : '子策略信号上限、同股去重及冷却期' }}处理前的候选数。</p></div>
              </div>
              <div v-if="!diagnosticRows.length" class="backtest-empty">该结果生成于未入选诊断启用前，请重新执行回测。</div>
              <template v-else>
                <div class="backtest-table-wrap"><table>
                  <thead><tr><th>子策略</th><th>股票日评分</th><th>达到评分门槛</th><th>通过全部条件</th><th>最高分 / 门槛</th></tr></thead>
                  <tbody><tr v-for="row in diagnosticRows" :key="row.id">
                    <td>{{ strategyLabel(row.id) }}</td><td>{{ row.scored_count || 0 }}</td><td>{{ row.threshold_met_count || 0 }}</td><td>{{ row.actionable_candidate_count || 0 }}</td>
                    <td>{{ formatScore(row.maximum_score) }} / {{ diagnosticThresholdLabel(row.entry_thresholds, row.entry_threshold) }}</td>
                  </tr></tbody>
                </table></div>
                <div class="backtest-diagnostic-groups">
                  <article v-for="row in diagnosticRows" :key="`diagnostic-${row.id}`">
                    <h3>{{ strategyLabel(row.id) }}</h3>
                    <div v-if="row.blockers?.length" class="backtest-diagnostic-section">
                      <strong>达到门槛后的主要拦截</strong>
                      <ul class="backtest-blockers">
                        <li v-for="item in row.blockers.slice(0, 5)" :key="item.reason"><span>{{ item.reason }}</span><b>{{ item.count }} 次</b></li>
                      </ul>
                    </div>
                    <div v-if="row.near_misses?.length" class="backtest-diagnostic-section">
                      <strong>最接近入选</strong>
                      <ol class="backtest-near-misses">
                        <li v-for="(item, index) in row.near_misses.slice(0, 3)" :key="`${item.date}-${item.symbol}-${index}`">
                          <span>{{ item.date }} · {{ stockName(item) }} · {{ stockCode(item.symbol) }} · {{ formatScore(item.score) }} 分</span>
                          <small>{{ (item.reasons || []).join('、') || '未提供拦截原因' }}</small>
                        </li>
                      </ol>
                    </div>
                    <p v-if="!row.blockers?.length && !row.near_misses?.length" class="backtest-diagnostic-empty">本次没有可展示的近似候选。</p>
                  </article>
                </div>
              </template>
            </section>

            <section v-if="strategy.id === 'niuone' && diagnosticPeriodRows.length" class="backtest-result-card">
              <div class="backtest-result-head">
                <div><h2>月度门槛敏感性与硬门槛消融</h2><p>只读诊断：展示调整评分门槛或单独移除某类硬门槛后可增加的候选数，不会自动改写策略。</p></div>
              </div>
              <div class="backtest-monthly-diagnostics">
                <details v-for="row in diagnosticPeriodRows" :key="`${row.period}-${row.id}`" class="backtest-month-diagnostic" :open="diagnosticPeriodRows.length <= 4">
                  <summary><strong>{{ row.period }} · {{ strategyLabel(row.id) }}</strong><span>通过全部条件 {{ row.actionable_candidate_count || 0 }} 个</span></summary>
                  <div class="backtest-month-diagnostic-body">
                    <div class="backtest-table-wrap">
                      <table>
                        <thead><tr><th>评分门槛偏移</th><th>实际门槛</th><th>无其他硬阻断候选</th></tr></thead>
                        <tbody><tr v-for="item in row.score_sensitivity || []" :key="item.threshold_offset">
                          <td>{{ signedOffset(item.threshold_offset) }}</td><td>{{ sensitivityThresholdLabel(item) }}</td><td>{{ item.candidate_count || 0 }}</td>
                        </tr></tbody>
                      </table>
                    </div>
                    <div class="backtest-table-wrap">
                      <table>
                        <thead><tr><th>硬门槛族</th><th>被该族阻断</th><th>单独移除可挽救</th></tr></thead>
                        <tbody><tr v-for="item in row.hard_gate_family_ablation || []" :key="item.family">
                          <td>{{ diagnosticFamilyLabel(item.family) }}</td><td>{{ item.blocked_candidate_count || 0 }}</td><td>{{ item.rescued_at_production_threshold || 0 }}</td>
                        </tr><tr v-if="!row.hard_gate_family_ablation?.length"><td colspan="3">没有可消融的单一门槛族</td></tr></tbody>
                      </table>
                    </div>
                    <div class="backtest-table-wrap backtest-branch-table">
                      <table>
                        <thead><tr><th>最佳标的</th><th>最佳评分日</th><th>概念/行业龙头分支</th><th>股票日评分</th><th>达门槛</th><th>可执行</th><th>月度阶段</th><th>最佳标的当日阻断</th><th>月度累计阻断族</th></tr></thead>
                        <tbody><tr
                          v-for="item in leaderBranchRows(row.leader_branch_coverage)"
                          :key="item.row_key"
                          :class="{'is-stock-group-start': item.stock_group_start}"
                        >
                          <td v-if="item.stock_group_start" :rowspan="item.stock_group_size" class="backtest-stock-group">
                            <strong>{{ item.stock_name }}</strong><br><small>{{ item.stock_code }}</small>
                          </td>
                          <td>{{ item.best_date || '—' }}<br><small>{{ formatScore(item.maximum_score) }} 分</small></td>
                          <td>{{ item.industry }}</td><td>{{ item.evaluated_count || 0 }}</td><td>{{ item.threshold_met_count || 0 }}</td><td>{{ item.actionable_candidate_count || 0 }}</td>
                          <td>{{ compactDiagnosticCounts(item.lifecycle_stages, lifecycleStageLabels) }}</td>
                          <td>{{ compactDiagnosticReasons(item.best_reasons, item.best_actionable) }}</td>
                          <td>{{ compactDiagnosticCounts(item.monthly_blocker_family_counts || item.blocker_family_counts, diagnosticFamilyLabels) }}</td>
                        </tr><tr v-if="!row.leader_branch_coverage?.length"><td colspan="9">本月没有可展示的龙头分支</td></tr></tbody>
                      </table>
                    </div>
                  </div>
                </details>
              </div>
            </section>

            <section v-if="isTradeLifecycle" class="backtest-result-card">
              <div class="backtest-result-head"><div><h2>买卖收益</h2><p>仅统计已触发卖出并完成离场的持仓周期，净收益已计入全部买卖批次、滑点、佣金、过户费与卖出印花税。</p></div></div>
              <div class="backtest-table-wrap"><table>
                <thead><tr><th>完整交易</th><th>平均净收益</th><th>中位净收益</th><th>胜率</th><th>最好</th><th>最差</th><th>平均持有</th></tr></thead>
                <tbody><tr>
                  <td>{{ statistics.completed_trade_count || 0 }}</td>
                  <td :class="percentClass(statistics.average_net_return_pct)">{{ formatPercent(statistics.average_net_return_pct) }}</td>
                  <td :class="percentClass(statistics.median_net_return_pct)">{{ formatPercent(statistics.median_net_return_pct) }}</td>
                  <td>{{ formatPercent(statistics.win_rate_pct) }}</td>
                  <td :class="percentClass(statistics.best_net_return_pct)">{{ formatPercent(statistics.best_net_return_pct) }}</td>
                  <td :class="percentClass(statistics.worst_net_return_pct)">{{ formatPercent(statistics.worst_net_return_pct) }}</td>
                  <td>{{ statistics.average_holding_sessions == null ? '—' : `${statistics.average_holding_sessions} 个交易日` }}</td>
                </tr></tbody>
              </table></div>
            </section>

            <section v-else class="backtest-result-card">
              <div class="backtest-result-head"><div><h2>整体收益</h2><p>净收益已计入滑点、佣金、过户费与卖出印花税。</p></div></div>
              <div class="backtest-table-wrap"><table>
                <thead><tr><th>持有日</th><th>样本</th><th>平均净收益</th><th>中位净收益</th><th>胜率</th><th>最好</th><th>最差</th></tr></thead>
                <tbody><tr v-for="row in horizonRows" :key="row.holding">
                  <td>{{ row.holding }} 日</td><td>{{ row.sample_count }}</td>
                  <td :class="percentClass(row.average_net_return_pct)">{{ formatPercent(row.average_net_return_pct) }}</td>
                  <td :class="percentClass(row.median_net_return_pct)">{{ formatPercent(row.median_net_return_pct) }}</td>
                  <td>{{ formatPercent(row.win_rate_pct) }}</td>
                  <td :class="percentClass(row.best_net_return_pct)">{{ formatPercent(row.best_net_return_pct) }}</td>
                  <td :class="percentClass(row.worst_net_return_pct)">{{ formatPercent(row.worst_net_return_pct) }}</td>
                </tr></tbody>
              </table></div>
            </section>

            <section v-if="strategyRows.length" class="backtest-result-card">
              <div class="backtest-result-head"><div><h2>{{ isTradeLifecycle ? '子策略交易' : '子策略信号' }}</h2><p>{{ isTradeLifecycle ? '对比各入场路径的实际买入与卖出表现。' : '用于确认组合中实际触发信号的规则。' }}</p></div></div>
              <div v-if="isTradeLifecycle" class="backtest-table-wrap"><table>
                <thead><tr><th>子策略</th><th>信号数</th><th>实际买入</th><th>完整交易</th><th>期末持仓</th><th>平均净收益</th><th>胜率</th><th>平均持有</th></tr></thead>
                <tbody><tr v-for="row in strategyRows" :key="row.id">
                  <td>{{ strategyLabel(row.id) }}</td><td>{{ row.signal_count }}</td><td>{{ row.evaluated_signal_count }}</td><td>{{ row.completed_trade_count }}</td><td>{{ row.open_trade_count }}</td>
                  <td :class="percentClass(row.average_net_return_pct)">{{ formatPercent(row.average_net_return_pct) }}</td><td>{{ formatPercent(row.win_rate_pct) }}</td>
                  <td>{{ row.average_holding_sessions == null ? '—' : `${row.average_holding_sessions} 日` }}</td>
                </tr></tbody>
              </table></div>
              <div v-else class="backtest-table-wrap"><table>
                <thead><tr><th>子策略</th><th>信号数</th><th>可评估</th><th>5 日平均净收益</th><th>10 日平均净收益</th><th>20 日平均净收益</th></tr></thead>
                <tbody><tr v-for="row in strategyRows" :key="row.id">
                  <td>{{ strategyLabel(row.id) }}</td><td>{{ row.signal_count }}</td><td>{{ row.evaluated_signal_count }}</td>
                  <td :class="percentClass(row.by_horizon?.['5']?.average_net_return_pct)">{{ formatPercent(row.by_horizon?.['5']?.average_net_return_pct) }}</td>
                  <td :class="percentClass(row.by_horizon?.['10']?.average_net_return_pct)">{{ formatPercent(row.by_horizon?.['10']?.average_net_return_pct) }}</td>
                  <td :class="percentClass(row.by_horizon?.['20']?.average_net_return_pct)">{{ formatPercent(row.by_horizon?.['20']?.average_net_return_pct) }}</td>
                </tr></tbody>
              </table></div>
            </section>

            <section v-if="isTradeLifecycle" class="backtest-result-card">
              <div class="backtest-result-head"><div><h2>交易明细</h2><p>{{ trades.length }} 个持仓周期；阶段升级会记录为同一周期内的加仓，全部卖出后再次入选才生成新周期。</p></div></div>
              <div v-if="!trades.length" class="backtest-empty">策略在该历史区间内没有可成交的买入信号。</div>
              <div v-else class="backtest-table-wrap"><table>
                <thead><tr><th>信号日</th><th>买入日</th><th>卖出日</th><th>股票</th><th>代码</th><th>策略路径</th><th>买/卖批次</th><th>状态</th><th>持有</th><th>买入均价</th><th>卖出均价</th><th>实际净收益</th><th>期末浮动</th><th>卖出原因</th></tr></thead>
                <tbody><tr v-for="trade in trades" :key="trade.id">
                  <td>{{ trade.signal_date }}</td><td>{{ trade.entry_date }}</td><td>{{ trade.exit_date || '—' }}</td><td>{{ stockName(trade) }}</td><td>{{ stockCode(trade.symbol) }}</td><td>{{ strategyPathLabel(trade) }}</td><td>{{ trade.entry_legs?.length || 1 }} / {{ trade.exit_legs?.length || 0 }}</td>
                  <td>{{ trade.status === 'completed' ? '已卖出' : '期末持仓' }}</td><td>{{ trade.holding_sessions == null ? '—' : `${trade.holding_sessions} 日` }}</td>
                  <td>{{ formatPrice(trade.entry_price) }}</td><td>{{ formatPrice(trade.exit_price) }}</td>
                  <td :class="percentClass(trade.net_return_pct)">{{ formatPercent(trade.net_return_pct) }}</td><td :class="percentClass(trade.mark_net_return_pct)">{{ formatPercent(trade.mark_net_return_pct) }}</td>
                  <td><small>{{ trade.exit_reason || '尚未触发卖出规则' }}</small></td>
                </tr></tbody>
              </table></div>
            </section>

            <section v-else class="backtest-result-card">
              <div class="backtest-result-head"><div><h2>信号明细</h2><p>{{ signals.length }} 条收盘后选股信号，收益从下一交易日开盘起算。</p></div></div>
              <div v-if="!signals.length" class="backtest-empty">策略在该历史区间内没有自主选出符合条件的股票。</div>
              <div v-else class="backtest-table-wrap"><table>
                <thead><tr><th>信号日</th><th>股票名称</th><th>代码</th><th>子策略</th><th>状态</th><th>入场日</th><th>1日</th><th>3日</th><th>5日</th><th>10日</th><th>20日</th></tr></thead>
                <tbody><tr v-for="(signal, index) in signals" :key="`${signal.signal_date}-${signal.symbol}-${signal.strategy_id}-${index}`">
                  <td>{{ signal.signal_date }}</td><td>{{ stockName(signal) }}</td><td>{{ stockCode(signal.symbol) }}</td><td>{{ strategyLabel(signal.strategy_id) }}</td>
                  <td>{{ signalStatusLabels[signal.status] || signal.status }}<small v-if="signal.status_reason"> · {{ signalStatusReason(signal.status_reason) }}</small></td>
                  <td>{{ signal.entry_date || '—' }}</td>
                  <td v-for="holding in ['1', '3', '5', '10', '20']" :key="holding" :class="percentClass(signalReturn(signal, holding))">{{ formatPercent(signalReturn(signal, holding)) }}</td>
                </tr></tbody>
              </table></div>
            </section>
          </template>
        </template>
      </template>
    </template>
  </main>
</template>

<style src="../../../frontend/admin.css"></style>
<style scoped>
.backtest-page{gap:12px}.backtest-hero,.backtest-form,.backtest-progress-card,.backtest-job-summary,.backtest-result-card{border:1px solid var(--line);border-radius:10px;background:var(--surface);box-shadow:var(--page-shadow)}
.backtest-hero{display:flex;justify-content:space-between;align-items:center;gap:18px;padding:15px 18px}.backtest-hero-copy{display:flex;align-items:flex-start;gap:12px;min-width:0}.backtest-hero-copy h2,.backtest-form h2,.backtest-progress-card h2,.backtest-result-card h2{margin:0;color:var(--text);font-size:18px}.backtest-hero-copy p,.backtest-form-head p,.backtest-progress-card p,.backtest-result-head p{margin-top:5px;color:var(--muted);font-size:13px;line-height:1.5}.backtest-strategy-dot{width:12px;height:12px;margin-top:5px;border-radius:4px;background:var(--strategy-color);flex:0 0 auto}.backtest-tags{display:flex;justify-content:flex-end;flex-wrap:wrap;gap:6px}.backtest-tags span{padding:5px 8px;border:1px solid var(--line);border-radius:999px;background:var(--surface2);color:var(--soft);font-size:11px;font-weight:800}
.backtest-notice{padding:11px 13px;border:1px solid var(--accent-border);border-radius:8px;background:var(--accent-soft);color:var(--accent-text);font-size:13px;line-height:1.55}.backtest-notice.is-warning{border-color:var(--yellow-border);background:var(--yellow-soft);color:var(--yellow-text)}
.backtest-quality{overflow:hidden;border:1px solid var(--yellow-border);border-radius:9px;background:var(--yellow-soft);color:var(--yellow-text)}.backtest-quality summary{display:flex;align-items:center;gap:10px;padding:12px 14px;cursor:pointer;list-style:none}.backtest-quality summary::-webkit-details-marker{display:none}.backtest-quality summary::after{content:'展开';flex:0 0 auto;color:var(--yellow-text);font-size:11px;font-weight:800}.backtest-quality[open] summary::after{content:'收起'}.backtest-quality-icon{display:grid;width:22px;height:22px;place-items:center;border:1px solid var(--yellow-border);border-radius:999px;background:var(--surface);font-size:12px;font-weight:950}.backtest-quality-copy{display:grid;min-width:0;gap:2px;flex:1}.backtest-quality-copy strong{font-size:13px}.backtest-quality-copy small{overflow:hidden;color:var(--yellow-text);font-size:11px;opacity:.86;text-overflow:ellipsis;white-space:nowrap}.backtest-quality-count{flex:0 0 auto;padding:3px 7px;border:1px solid var(--yellow-border);border-radius:999px;background:var(--surface);font-size:10px;font-weight:850}.backtest-quality-details{display:grid;gap:0;border-top:1px solid var(--yellow-border);background:color-mix(in srgb,var(--surface) 72%,var(--yellow-soft))}.backtest-quality-details>div{display:grid;grid-template-columns:90px minmax(0,1fr);gap:12px;padding:10px 14px;border-bottom:1px solid var(--yellow-border)}.backtest-quality-details>div:last-child{border-bottom:0}.backtest-quality-details strong{font-size:11px}.backtest-quality-details span{color:var(--soft);font-size:11px;line-height:1.5;overflow-wrap:anywhere}
.backtest-form{padding:16px}.backtest-form-head,.backtest-result-head,.backtest-progress-head{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}.backtest-form-head>span{color:var(--muted);font-size:12px;font-weight:800;white-space:nowrap}.backtest-fields{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:14px}.backtest-fields label{display:grid;gap:7px;color:var(--text);font-size:13px;font-weight:850}.backtest-fields input,.backtest-fields select{width:100%}.backtest-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:14px}.backtest-start,.backtest-cancel{display:inline-flex;min-width:150px;min-height:44px;align-items:center;justify-content:center;gap:8px;margin:0}.backtest-start{background:var(--primary);color:var(--primary-text);box-shadow:none}.backtest-cancel{border:1px solid var(--danger-button-border);background:var(--danger-button-bg);color:var(--danger-button-text);box-shadow:0 5px 14px rgba(127,29,29,.28),0 1px 0 rgba(255,255,255,.18) inset;transition:background .12s ease,border-color .12s ease,box-shadow .12s ease,transform .12s ease}.backtest-cancel::before{content:'■';font-size:9px;line-height:1}.backtest-cancel:hover:not(:disabled){background:var(--danger-button-hover);box-shadow:0 7px 18px rgba(127,29,29,.36),0 1px 0 rgba(255,255,255,.20) inset;transform:translateY(-1px)}.backtest-cancel:focus-visible{outline:3px solid var(--red);outline-offset:3px}.backtest-cancel:active:not(:disabled){box-shadow:0 2px 7px rgba(127,29,29,.34) inset;transform:translateY(1px)}.backtest-start:disabled,.backtest-cancel:disabled{cursor:not-allowed;opacity:.65}
.backtest-progress-card{padding:18px}.backtest-progress-head strong{color:var(--accent);font-size:28px}.backtest-status{display:inline-flex;margin-bottom:7px;padding:3px 7px;border:1px solid var(--accent-border);border-radius:999px;background:var(--accent-soft);color:var(--accent-text);font-size:11px;font-weight:900}.backtest-status.is-succeeded{border-color:var(--green-border);background:var(--green-soft);color:var(--green-text)}.backtest-status.is-failed,.backtest-status.is-cancelled{border-color:var(--red-border);background:var(--red-soft);color:var(--red-text)}.backtest-progress-track{height:10px;margin-top:16px;overflow:hidden;border-radius:999px;background:var(--surface2);border:1px solid var(--line)}.backtest-progress-track span{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,var(--primary),var(--green));transition:width .25s ease}.backtest-timestamps{display:flex;flex-wrap:wrap;gap:8px 18px;margin-top:10px;color:var(--muted);font-size:11px}
.backtest-job-summary{display:flex;align-items:center;flex-wrap:wrap;gap:10px;padding:9px 12px}.backtest-job-summary .backtest-status{margin:0}.backtest-job-summary>strong{color:var(--text);font-size:12px}.backtest-job-summary>small{margin-left:auto;color:var(--muted);font-size:11px}.backtest-job-summary>.errmsg{flex-basis:100%;margin:0}.backtest-overview{display:grid;grid-template-columns:minmax(220px,1.35fr) repeat(4,minmax(120px,1fr));gap:10px}.backtest-overview>div{display:grid;gap:4px;padding:12px 14px;border:1px solid var(--line);border-radius:9px;background:var(--surface)}.backtest-overview span{color:var(--muted);font-size:11px}.backtest-overview strong{color:var(--text);font-size:22px}.backtest-scope-summary strong{font-size:15px}.backtest-scope-summary small{color:var(--soft);font-size:10px}.backtest-result-card{overflow:hidden}.backtest-result-head{padding:15px 17px;border-bottom:1px solid var(--line);background:var(--surface2)}.backtest-table-wrap{max-width:100%;overflow:auto}.backtest-table-wrap table{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}.backtest-table-wrap th,.backtest-table-wrap td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:right}.backtest-table-wrap th{position:sticky;top:0;background:var(--surface2);color:var(--muted);font-size:11px}.backtest-table-wrap th:first-child,.backtest-table-wrap td:first-child{text-align:left}.backtest-table-wrap td{color:var(--soft)}.backtest-table-wrap td small{color:var(--muted)}.backtest-table-wrap .is-positive{color:var(--green-text);font-weight:800}.backtest-table-wrap .is-negative{color:var(--red-text);font-weight:800}.backtest-empty{padding:28px;color:var(--muted);text-align:center;font-size:13px}
.backtest-diagnostic-groups{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;padding:15px}.backtest-diagnostic-groups article{min-width:0;padding:14px;border:1px solid var(--line);border-radius:9px;background:var(--surface2)}.backtest-diagnostic-groups h3{margin:0;color:var(--text);font-size:14px}.backtest-diagnostic-section{display:grid;gap:8px;margin-top:13px}.backtest-diagnostic-section>strong{color:var(--muted);font-size:11px}.backtest-blockers,.backtest-near-misses{display:grid;gap:7px;margin:0;padding:0;list-style:none}.backtest-blockers li{display:flex;justify-content:space-between;gap:8px;padding:7px 8px;border-radius:7px;background:var(--surface)}.backtest-blockers span{min-width:0;color:var(--soft);font-size:11px;line-height:1.4}.backtest-blockers b{flex:0 0 auto;color:var(--yellow-text);font-size:10px}.backtest-near-misses li{display:grid;gap:3px;padding-left:9px;border-left:2px solid var(--accent-border)}.backtest-near-misses span{color:var(--text);font-size:11px;font-weight:800}.backtest-near-misses small{color:var(--muted);font-size:10px;line-height:1.45;overflow-wrap:anywhere}.backtest-diagnostic-empty{margin:13px 0 0;color:var(--muted);font-size:11px}
.backtest-monthly-diagnostics{display:grid;gap:10px;padding:15px}.backtest-month-diagnostic{overflow:hidden;border:1px solid var(--line);border-radius:9px;background:var(--surface2)}.backtest-month-diagnostic>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 13px;cursor:pointer;list-style:none}.backtest-month-diagnostic>summary::-webkit-details-marker{display:none}.backtest-month-diagnostic>summary strong{color:var(--text);font-size:12px}.backtest-month-diagnostic>summary span{color:var(--muted);font-size:11px}.backtest-month-diagnostic-body{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:0 12px 12px;border-top:1px solid var(--line)}.backtest-month-diagnostic-body>.backtest-table-wrap{margin-top:12px;border:1px solid var(--line);border-radius:7px;background:var(--surface)}.backtest-month-diagnostic-body>.backtest-branch-table{grid-column:1/-1}.backtest-month-diagnostic-body td{white-space:normal}.backtest-month-diagnostic-body td:first-child{font-weight:750}.backtest-month-diagnostic-body td small{font-size:10px}
.backtest-branch-table tr.is-stock-group-start:not(:first-child)>td{border-top:2px solid var(--line)}.backtest-branch-table .backtest-stock-group{min-width:112px;vertical-align:top;background:var(--surface2)}.backtest-stock-group strong{color:var(--text);font-size:12px}.backtest-stock-group small{display:inline-block;margin-top:3px}
@media(max-width:980px){.backtest-fields{grid-template-columns:repeat(2,minmax(0,1fr))}.backtest-overview{grid-template-columns:repeat(2,minmax(0,1fr))}.backtest-scope-summary{grid-column:1/-1}}
@media(max-width:980px){.backtest-diagnostic-groups{grid-template-columns:1fr}}
@media(max-width:980px){.backtest-month-diagnostic-body{grid-template-columns:1fr}.backtest-month-diagnostic-body>.backtest-branch-table{grid-column:auto}}
@media(max-width:620px){.backtest-hero,.backtest-form-head,.backtest-progress-head{align-items:stretch;flex-direction:column}.backtest-tags{justify-content:flex-start}.backtest-fields,.backtest-overview{grid-template-columns:1fr}.backtest-actions{flex-direction:column}.backtest-start,.backtest-cancel{width:100%}.backtest-job-summary{align-items:flex-start;flex-wrap:wrap}.backtest-job-summary>small{width:100%;margin-left:0}.backtest-quality-count{display:none}.backtest-quality-details>div{grid-template-columns:1fr;gap:4px}}
</style>
