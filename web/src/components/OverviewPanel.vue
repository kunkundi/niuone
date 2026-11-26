<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useIndicesData } from '../composables/useIndicesData.js'
import { useNiuOneMainlineData } from '../composables/useNiuOneMainlineData.js'
import { usePracticeCandidatesData } from '../composables/usePracticeCandidatesData.js'
import { usePracticeData } from '../composables/usePracticeData.js'
import { useRealtimeNewsData } from '../composables/useRealtimeNewsData.js'
import {
  finiteNumber,
  formatOverviewAmount,
  formatOverviewNumber,
  formatOverviewPercent,
  formatOverviewYi,
  freshnessText,
  overviewAccount,
  overviewBreadth,
  overviewCandidatePeriod,
  overviewCandidates,
  overviewFlowRowLimit,
  overviewIndices,
  overviewMainlinePanelMode,
  overviewMarketState,
  overviewMoneyFlow,
  overviewMoneyFlowNet,
  overviewPracticeMarketSummary,
  overviewSectorMoves,
  overviewThemes,
  overviewViewportMode,
  valueTone,
} from '../utils/homeOverviewDisplay.js'
import {
  filterRealtimeNews,
  realtimeNewsClock,
} from '../utils/realtimeNewsDisplay.js'
import IndexSparkline from './indices/IndexSparkline.vue'
import MarketBreadthChart from './indices/MarketBreadthChart.vue'

const {
  state: indicesState,
  activateIndices,
  deactivateIndices,
} = useIndicesData()
const {
  state: mainlineState,
  activateNiuOneMainline,
  deactivateNiuOneMainline,
} = useNiuOneMainlineData()
const {
  state: candidateState,
  activatePracticeCandidates,
  deactivatePracticeCandidates,
} = usePracticeCandidatesData()
const {
  state: practiceState,
  activatePractice,
  deactivatePractice,
} = usePracticeData()
const {
  state: realtimeNewsState,
  activateRealtimeNews,
  deactivateRealtimeNews,
} = useRealtimeNewsData()

const overviewRoot = ref(null)
const mainlinePanel = ref(null)
const overviewViewportHeight = ref(0)
const overviewViewportWidth = ref(0)
const mainlinePanelHeight = ref(0)
const expandedThemeIndex = ref(-1)
const expandedThemeRanking = ref('')
const themeStockPopover = ref(null)
const themeStockPopoverStyle = ref({})
let headerResizeObserver = null
let mainlineResizeObserver = null
let themeStockAnchor = null

const mainlinePayload = computed(() => mainlineState.payload || {})
const marketState = computed(() => overviewMarketState(mainlinePayload.value.market))
const breadth = computed(() => overviewBreadth(indicesState.marketBreadth, {
  ...(mainlinePayload.value.market || {}),
  generated_at: mainlinePayload.value.generated_at,
}))
const account = computed(() => overviewAccount(practiceState.practice))
const accountReady = computed(() => practiceState.loaded && account.value.available)
const indices = computed(() => overviewIndices(indicesState.indices))
const candidatePeriod = computed(() => overviewCandidatePeriod(
  candidateState.generatedAt,
  practiceState.practice?.trading_calendar,
))
const candidates = computed(() => overviewCandidates(
  candidateState.items,
  candidateState.strategyMeta,
))
const overviewNewsItems = computed(() => filterRealtimeNews(
  realtimeNewsState.items,
  'all',
  realtimeNewsState.overviewImportantOnly,
).slice(0, 10))
const overviewNewsModeLabel = computed(() => (
  realtimeNewsState.overviewImportantOnly ? '仅重要' : '全部快讯'
))
const overviewNewsEmptyText = computed(() => {
  if (!realtimeNewsState.enabled) return '财经快讯已停用'
  if (realtimeNewsState.overviewImportantOnly && realtimeNewsState.items.length) {
    return '当前暂无重要快讯'
  }
  return realtimeNewsState.error ? '财经快讯暂不可用' : '暂无财经快讯'
})
const themeRankings = computed(() => [
  {
    key: 'today',
    title: '今日排名',
    themes: overviewThemes(mainlinePayload.value, 5, 'today'),
  },
  {
    key: 'structure',
    title: '结构排名',
    themes: overviewThemes(mainlinePayload.value, 5, 'structure'),
  },
])
const expandedTheme = computed(() => themeRankings.value
  .find(ranking => ranking.key === expandedThemeRanking.value)
  ?.themes[expandedThemeIndex.value] || null)
const flowRowLimit = computed(() => overviewFlowRowLimit(overviewViewportHeight.value))
const sectorGains = computed(() => overviewSectorMoves(indicesState.sectors, 'gain', flowRowLimit.value))
const sectorLosses = computed(() => overviewSectorMoves(indicesState.sectors, 'loss', flowRowLimit.value))
const moneyInflows = computed(() => overviewMoneyFlow(indicesState.moneyFlow, 'inflow', flowRowLimit.value))
const moneyOutflows = computed(() => overviewMoneyFlow(indicesState.moneyFlow, 'outflow', flowRowLimit.value))
const overviewPreviousTradingDayLabel = computed(() => {
  const calendar = practiceState.practice?.trading_calendar || {}
  const hasCalendarState = typeof calendar.is_trading_day === 'boolean'
  const hasPreviousDayFallback = Boolean(
    breadth.value.previousTradingDay
    || candidatePeriod.value.previousTradingDay
    || indicesState.moneyFlow?.displaying_previous_trading_day,
  )
  if (hasCalendarState ? calendar.is_trading_day : !hasPreviousDayFallback) return ''
  const date = String(
    calendar.previous_trading_day
    || (breadth.value.previousTradingDay ? breadth.value.displayDate : '')
    || (candidatePeriod.value.previousTradingDay ? candidatePeriod.value.generatedDate : '')
    || indicesState.moneyFlow?.display_date
    || '',
  ).slice(5, 10)
  return date ? `数据基准：上一交易日 ${date}` : '数据基准：上一交易日'
})
const industryNetFlow = computed(() => overviewMoneyFlowNet(indicesState.moneyFlow))
const marketSummary = computed(() => overviewPracticeMarketSummary(
  practiceState.marketSummary,
  practiceState.marketSummaryGenerating,
))
const latestUpdate = computed(() => (
  indicesState.indices?.generated_at
  || breadth.value.generatedAt
  || mainlinePayload.value.generated_at
  || account.value.generatedAt
  || ''
))
const exposureTone = computed(() => {
  if (!accountReady.value) return 'neutral'
  const exposure = account.value.exposurePct
  if (exposure == null) return 'neutral'
  if (exposure >= 80) return 'negative'
  if (exposure >= 55) return 'warning'
  return 'positive'
})
const mainlineAvailable = computed(() => mainlinePayload.value.available === true)
const flowDataAvailable = computed(() => (
  sectorGains.value.length
  || sectorLosses.value.length
  || moneyInflows.value.length
  || moneyOutflows.value.length
))
const accountStatus = computed(() => {
  if (account.value.paused) return account.value.pauseReason || '新开仓已暂停'
  if (practiceState.dataReadiness?.ready === true) return '交易数据就绪'
  return practiceState.dataReadiness?.status_label || '正在检查交易数据'
})
const viewportMode = computed(() => overviewViewportMode(
  overviewViewportWidth.value,
  overviewViewportHeight.value,
))
const mainlinePanelMode = computed(() => overviewMainlinePanelMode(mainlinePanelHeight.value))

function themeLeader(theme) {
  const leader = theme?.leader
  const fallback = `${theme?.leaderBadge || '龙头'}待确认`
  if (!leader || typeof leader !== 'object') return fallback
  return [leader.code, leader.name].filter(Boolean).join(' ') || fallback
}

function syncThemeStockPopover() {
  if (expandedThemeIndex.value < 0 || !themeStockAnchor) return
  const anchorRect = themeStockAnchor.getBoundingClientRect()
  const visualViewport = window.visualViewport
  const viewportLeft = visualViewport?.offsetLeft || 0
  const viewportTop = visualViewport?.offsetTop || 0
  const viewportWidth = visualViewport?.width || window.innerWidth
  const viewportHeight = visualViewport?.height || window.innerHeight
  const width = Math.max(196, Math.min(208, viewportWidth - 16))
  const popoverHeight = themeStockPopover.value?.getBoundingClientRect().height
    || 54 + (expandedTheme.value?.coreStocks.length || 0) * 25
  const left = Math.max(
    viewportLeft + 8,
    Math.min(anchorRect.left, viewportLeft + viewportWidth - width - 8),
  )
  const belowTop = anchorRect.bottom + 6
  const aboveTop = anchorRect.top - popoverHeight - 6
  const top = belowTop + popoverHeight <= viewportTop + viewportHeight - 8
    ? belowTop
    : Math.max(viewportTop + 8, aboveTop)
  themeStockPopoverStyle.value = {
    left: `${Math.round(left)}px`,
    top: `${Math.round(top)}px`,
    width: `${Math.round(width)}px`,
  }
}

function closeThemeStocks(restoreFocus = false) {
  const anchor = themeStockAnchor
  expandedThemeIndex.value = -1
  expandedThemeRanking.value = ''
  themeStockPopoverStyle.value = {}
  themeStockAnchor = null
  if (restoreFocus && anchor) nextTick(() => anchor.focus())
}

function isThemeExpanded(rankingKey, index) {
  return expandedThemeRanking.value === rankingKey && expandedThemeIndex.value === index
}

function themeStockPanelId(rankingKey, index) {
  return `overviewThemeStocks-${rankingKey}-${index}`
}

function toggleThemeStocks(rankingKey, index, event) {
  if (isThemeExpanded(rankingKey, index)) {
    closeThemeStocks()
    return
  }
  expandedThemeRanking.value = rankingKey
  expandedThemeIndex.value = index
  themeStockAnchor = event?.currentTarget || null
  nextTick(syncThemeStockPopover)
}

function handleThemeStockPointerDown(event) {
  if (expandedThemeIndex.value < 0) return
  if (themeStockAnchor?.contains(event.target) || themeStockPopover.value?.contains(event.target)) return
  closeThemeStocks()
}

function handleThemeStockKeydown(event) {
  if (event.key === 'Escape' && expandedThemeIndex.value >= 0) closeThemeStocks(true)
}

function candidateIdentity(candidate) {
  return [candidate.code, candidate.name].filter(Boolean).join(' ') || '未命名标的'
}

function sectorMove(row) {
  return finiteNumber(row?.move ?? row?.pct ?? row?.change_pct)
}

function flowIdentity(row) {
  return [row?.code, row?.name].filter(Boolean).join(' ') || '未命名标的'
}

function syncOverviewViewport() {
  const bounds = overviewRoot.value?.getBoundingClientRect()
  const visualViewport = window.visualViewport
  // Keep the JavaScript layout mode on the same viewport basis as CSS media
  // queries. visualViewport.width excludes the scrollbar in Chromium, which
  // otherwise makes the 721px boundary enter mobile data mode while desktop
  // CSS is still active.
  const viewportWidth = window.innerWidth
    || document.documentElement.clientWidth
    || visualViewport?.width
    || 0
  const viewportBottom = visualViewport
    ? visualViewport.height + visualViewport.offsetTop
    : window.innerHeight || document.documentElement.clientHeight || 0
  const available = Math.floor(viewportBottom - (bounds?.top || 0) - 6)
  if (viewportWidth > 0) overviewViewportWidth.value = Math.floor(viewportWidth)
  if (available > 0) overviewViewportHeight.value = available
  syncThemeStockPopover()
}

function syncMainlinePanelHeight() {
  const height = mainlinePanel.value?.getBoundingClientRect().height || 0
  if (height > 0) mainlinePanelHeight.value = Math.round(height * 100) / 100
}

onMounted(() => {
  document.body.classList.add('overview-terminal-open')
  activateIndices()
  activateNiuOneMainline()
  activatePracticeCandidates()
  activatePractice()
  activateRealtimeNews()
  window.addEventListener('resize', syncOverviewViewport, { passive: true })
  window.visualViewport?.addEventListener('resize', syncOverviewViewport, { passive: true })
  window.addEventListener('scroll', syncThemeStockPopover, true)
  document.addEventListener('pointerdown', handleThemeStockPointerDown, true)
  document.addEventListener('keydown', handleThemeStockKeydown, true)
  nextTick(() => {
    if (!overviewRoot.value) return
    syncOverviewViewport()
    const header = document.querySelector('header')
    if (header && typeof ResizeObserver !== 'undefined') {
      headerResizeObserver = new ResizeObserver(syncOverviewViewport)
      headerResizeObserver.observe(header)
    }
    syncMainlinePanelHeight()
    if (mainlinePanel.value && typeof ResizeObserver !== 'undefined') {
      mainlineResizeObserver = new ResizeObserver(syncMainlinePanelHeight)
      mainlineResizeObserver.observe(mainlinePanel.value)
    }
  })
})

onBeforeUnmount(() => {
  document.body.classList.remove('overview-terminal-open')
  deactivateIndices()
  deactivateNiuOneMainline()
  deactivatePracticeCandidates()
  deactivatePractice()
  deactivateRealtimeNews()
  window.removeEventListener('resize', syncOverviewViewport)
  window.visualViewport?.removeEventListener('resize', syncOverviewViewport)
  window.removeEventListener('scroll', syncThemeStockPopover, true)
  document.removeEventListener('pointerdown', handleThemeStockPointerDown, true)
  document.removeEventListener('keydown', handleThemeStockKeydown, true)
  headerResizeObserver?.disconnect()
  headerResizeObserver = null
  mainlineResizeObserver?.disconnect()
  mainlineResizeObserver = null
})
</script>

<template>
  <div
    ref="overviewRoot"
    class="overview-page"
    :data-layout="viewportMode.layout"
    :data-density="viewportMode.density"
    :style="{
      '--overview-viewport-height': overviewViewportHeight ? `${overviewViewportHeight}px` : undefined,
      '--overview-mainline-panel-height': mainlinePanelHeight ? `${mainlinePanelHeight}px` : undefined,
    }"
  >
    <section class="overview-command-head" aria-labelledby="overviewTitle">
      <div>
        <h2 id="overviewTitle">盘面监测总览</h2>
        <p class="overview-market-summary" :title="marketSummary" aria-label="模拟交易盘面总结">{{ marketSummary }}</p>
      </div>
      <div class="overview-command-meta" aria-label="数据更新时间">
        <span class="overview-live-dot" aria-hidden="true"></span>
        <span v-if="overviewPreviousTradingDayLabel" class="overview-stale-badge">{{ overviewPreviousTradingDayLabel }}</span>
        <span>{{ freshnessText(latestUpdate) }}</span>
        <span v-if="indicesState.indices?.stale_cache && !overviewPreviousTradingDayLabel" class="overview-stale-badge">缓存行情</span>
      </div>
    </section>

    <div v-if="account.paused" class="overview-banner negative" role="status">
      <strong>交易风控</strong>
      <span>{{ accountStatus }}；卖出风控继续运行，账户操作请前往模拟交易页。</span>
      <RouterLink to="/practice">查看账户</RouterLink>
    </div>
    <div
      v-else-if="practiceState.dataReadiness?.blockers?.length"
      class="overview-banner warning"
      role="status"
    >
      <strong>数据准备未完成</strong>
      <span>{{ accountStatus }}，首页继续展示最近一份有效数据。</span>
      <RouterLink to="/practice">查看详情</RouterLink>
    </div>

    <section class="overview-kpi-strip" role="list" aria-label="核心决策指标">
      <article class="overview-kpi" :class="marketState.tone" role="listitem">
        <div class="overview-kpi-label">市场状态</div>
        <strong>{{ marketState.available ? marketState.label : '--' }}</strong>
        <span>{{ marketState.score == null ? '评分待补充' : `${formatOverviewNumber(marketState.score, 0)}分 · ${marketState.allowNewBuys ? '允许观察新机会' : '暂停新开仓'}` }}</span>
      </article>
      <article class="overview-kpi neutral" role="listitem">
        <div class="overview-kpi-label">市场宽度</div>
        <strong>{{ breadth.advancingPct == null ? '--' : formatOverviewPercent(breadth.advancingPct, 0) }}</strong>
        <span v-if="breadth.countsAvailable">红盘 {{ formatOverviewNumber(breadth.advancing, 0) }} · 绿盘 {{ formatOverviewNumber(breadth.declining, 0) }}</span>
        <span v-else-if="breadth.medianChangePct != null">全市场中位涨跌 {{ formatOverviewPercent(breadth.medianChangePct, 2, true) }}</span>
        <span v-else>涨跌分布待补充</span>
      </article>
      <article class="overview-kpi neutral" role="listitem">
        <div class="overview-kpi-label">涨跌停</div>
        <strong><b class="up">↑ {{ formatOverviewNumber(breadth.limitUp, 0) }}</b><i>/</i><b class="down">↓ {{ formatOverviewNumber(breadth.limitDown, 0) }}</b></strong>
        <span>炸板 {{ formatOverviewNumber(breadth.brokenLimit, 0) }}只</span>
      </article>
      <article class="overview-kpi" :class="valueTone(industryNetFlow)" role="listitem">
        <div class="overview-kpi-label">主要行业主力净额</div>
        <strong>{{ formatOverviewYi(industryNetFlow, true) }}</strong>
        <span>{{ freshnessText(indicesState.moneyFlow?.generated_at) }}</span>
      </article>
      <article class="overview-kpi" :class="exposureTone" role="listitem">
        <div class="overview-kpi-label">账户仓位</div>
        <strong>{{ accountReady ? formatOverviewPercent(account.exposurePct, 1) : '--' }}</strong>
        <span
          v-if="accountReady"
          :title="account.dailyPnl == null || account.dailyPnlPct == null ? '当日收益数据待补充' : `当日收益 ${formatOverviewAmount(account.dailyPnl, true)}，收益率 ${formatOverviewPercent(account.dailyPnlPct, 2, true)}`"
        >
          <b v-if="account.dailyPnl != null && account.dailyPnlPct != null" :class="valueTone(account.dailyPnl)">当日 {{ formatOverviewAmount(account.dailyPnl, true) }} · {{ formatOverviewPercent(account.dailyPnlPct, 2, true) }}</b>
          <b v-else>当日收益待补充</b>
          <i> · {{ account.positionCount }}只</i>
        </span>
        <span v-else>账户快照加载中</span>
      </article>
    </section>

    <div class="overview-terminal-grid">
    <div class="overview-primary-grid">
      <section class="overview-panel overview-market-panel" aria-labelledby="overviewMarketTitle">
        <div class="overview-panel-head">
          <div>
            <h3 id="overviewMarketTitle">市场情绪</h3>
          </div>
          <div class="overview-panel-actions">
            <span class="overview-update-time">{{ freshnessText(breadth.generatedAt) }}</span>
            <RouterLink to="/indices">查看详情 <span aria-hidden="true">›</span></RouterLink>
          </div>
        </div>

        <div class="overview-chart-wrap">
          <MarketBreadthChart v-if="indicesState.marketBreadth?.timeline?.length" :payload="indicesState.marketBreadth" terminal />
          <div
            v-else-if="breadth.advancingPct != null"
            class="overview-breadth-unavailable"
            :style="{ '--breadth-reference-position': `${100 - Math.max(0, Math.min(100, breadth.advancingPct))}%` }"
            role="status"
          >
            <div class="overview-breadth-reference-line" aria-hidden="true"><span></span></div>
            <div class="overview-breadth-unavailable-copy">
              <strong>日内曲线暂无完整采样</strong>
              <span>收盘市场宽度 {{ formatOverviewPercent(breadth.advancingPct, 0) }}，已用于上方指标</span>
            </div>
          </div>
          <div v-else class="overview-empty compact">等待交易时段首个有效采样</div>
        </div>
      </section>

      <section
        ref="mainlinePanel"
        class="overview-panel overview-mainline-panel"
        :data-mainline-layout="viewportMode.layout !== 'wide' || viewportMode.density === 'comfortable' ? 'full' : mainlinePanelMode"
        aria-labelledby="overviewMainlineTitle"
      >
        <div class="overview-panel-head compact">
          <div>
            <h3 id="overviewMainlineTitle">主线机会</h3>
          </div>
          <RouterLink to="/niuone-mainline" class="overview-detail-link" aria-label="查看完整主线机会">详情 <span aria-hidden="true">›</span></RouterLink>
        </div>

        <div v-if="mainlineState.error && mainlineAvailable" class="overview-inline-notice warning" role="status">
          题材更新暂时失败，继续展示缓存：{{ mainlineState.error }}
        </div>

        <template v-if="mainlineAvailable">
          <div
            v-if="themeRankings.some(ranking => ranking.themes.length)"
            class="overview-theme-rankings"
            aria-label="主线题材排名"
          >
            <section
              v-for="ranking in themeRankings"
              :key="ranking.key"
              class="overview-theme-ranking"
              :aria-labelledby="`overview-${ranking.key}-ranking-title`"
            >
              <h4 :id="`overview-${ranking.key}-ranking-title`"><span>{{ ranking.title }}</span></h4>
              <div
                v-if="ranking.themes.length"
                class="overview-theme-list"
                role="table"
                :aria-label="ranking.title"
              >
                <div class="overview-theme-table-head" role="row">
                  <span role="columnheader">题材 / 周期</span>
                  <span role="columnheader">核心股 / 梯队</span>
                </div>
                <div v-for="(theme, index) in ranking.themes" :key="`${ranking.key}-${theme.displayName}`" class="overview-theme-row" role="row">
                  <div class="overview-theme-copy" role="cell">
                    <div class="overview-theme-title">
                      <strong>{{ theme.displayName }}</strong>
                      <small class="overview-theme-lifecycle">{{ theme.lifecycle }}</small>
                    </div>
                    <small class="overview-theme-meta">
                      <template v-if="ranking.key === 'today'">上涨 {{ formatOverviewNumber(theme.strongStockCount, 0) }}只 · 结构 {{ formatOverviewNumber(theme.comparisonScore, 1) }}</template>
                      <template v-else>强股 {{ formatOverviewNumber(theme.strongStockCount, 0) }}只 · 确认 {{ formatOverviewNumber(theme.confirmationCount, 0) }}次</template>
                    </small>
                    <button
                      class="overview-theme-mobile-toggle"
                      type="button"
                      :aria-expanded="isThemeExpanded(ranking.key, index)"
                      :aria-controls="themeStockPanelId(ranking.key, index)"
                      @click="toggleThemeStocks(ranking.key, index, $event)"
                    >
                      <span>{{ themeLeader(theme) }}</span>
                      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4"></path></svg>
                    </button>
                  </div>
                  <div class="overview-theme-leader" role="cell">
                    <button
                      class="overview-theme-leader-toggle"
                      type="button"
                      :aria-expanded="isThemeExpanded(ranking.key, index)"
                      :aria-controls="themeStockPanelId(ranking.key, index)"
                      :aria-label="`${isThemeExpanded(ranking.key, index) ? '收起' : '展开'}${theme.displayName}全部核心股`"
                      @click="toggleThemeStocks(ranking.key, index, $event)"
                    >
                      <span>{{ themeLeader(theme) }}</span>
                      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4"></path></svg>
                    </button>
                  </div>
                </div>
              </div>
              <div v-else class="overview-mini-empty">当前暂无{{ ranking.title }}数据</div>
            </section>
          </div>
          <div v-else class="overview-empty">尚无题材排名数据</div>
        </template>
        <div v-else-if="mainlineState.loading && !mainlineState.loaded" class="overview-loading">正在读取主线题材…</div>
        <div v-else class="overview-empty">{{ mainlineState.error ? `题材数据暂不可用：${mainlineState.error}` : '尚无主线题材扫描结果' }}</div>
      </section>
      <Teleport to="body">
        <div
          v-if="expandedTheme"
          :id="themeStockPanelId(expandedThemeRanking, expandedThemeIndex)"
          ref="themeStockPopover"
          class="overview-theme-stock-popover"
          :style="themeStockPopoverStyle"
          role="region"
          :aria-label="`${expandedTheme.displayName}全部核心股`"
        >
          <div class="overview-theme-stock-popover-head">
            <div>
              <strong>{{ expandedTheme.displayName }}核心股</strong>
              <span>{{ expandedTheme.coreStocks.length }}只</span>
            </div>
            <button type="button" @click="closeThemeStocks(true)">关闭</button>
          </div>
          <div class="overview-theme-stock-list-head" aria-hidden="true">
            <span>{{ expandedTheme.stockListLabel }}</span>
            <span>代码</span>
            <span>涨跌幅</span>
          </div>
          <div class="overview-theme-stock-list">
            <span v-for="(stock, stockIndex) in expandedTheme.coreStocks" :key="stock.code || stock.name" class="overview-theme-stock">
              <span class="overview-theme-stock-name">
                <strong>{{ stock.name || '名称待补充' }}</strong>
                <b v-if="stockIndex === 0">{{ expandedTheme.leaderBadge }}</b>
              </span>
              <small>{{ stock.code || '--' }}</small>
              <em :class="valueTone(stock.changePct)">{{ formatOverviewPercent(stock.changePct, 2, true) }}</em>
            </span>
          </div>
        </div>
      </Teleport>
    </div>

    <div class="overview-secondary-grid">
      <section class="overview-panel overview-indices-panel" aria-labelledby="overviewIndicesTitle">
        <div class="overview-panel-head">
          <div>
            <h3 id="overviewIndicesTitle">指数</h3>
          </div>
          <div class="overview-panel-actions">
            <span class="overview-update-time">{{ freshnessText(indicesState.indices?.generated_at) }}</span>
            <RouterLink to="/indices">查看行情 <span aria-hidden="true">›</span></RouterLink>
          </div>
        </div>
        <div v-if="indicesState.indices?.error && indices.length && !overviewPreviousTradingDayLabel" class="overview-inline-notice warning" role="status">
          实时更新暂时失败，继续展示最近缓存：{{ indicesState.indices.error }}
        </div>
        <div v-if="indices.length" class="overview-index-grid" role="list" aria-label="A股核心指数">
          <article
            v-for="item in indices"
            :key="item.key || item.code || item.name"
            class="overview-index-tile"
            :class="valueTone(item.change_pct)"
            role="listitem"
          >
            <div class="overview-index-meta"><span>{{ item.name }}</span><time>{{ String(item.time || '').slice(11, 16) }}</time></div>
            <div class="overview-index-quote">
              <strong>{{ formatOverviewNumber(item.price, 2) }}</strong>
              <b>{{ finiteNumber(item.change_pct) > 0 ? '↑ ' : finiteNumber(item.change_pct) < 0 ? '↓ ' : '' }}{{ formatOverviewPercent(item.change_pct, 2, true) }}</b>
            </div>
            <IndexSparkline :item="item" />
          </article>
        </div>
        <div v-else-if="indicesState.loading" class="overview-loading">正在读取 A 股核心指数…</div>
        <div v-else class="overview-empty">{{ indicesState.indices?.error ? `指数数据暂不可用：${indicesState.indices.error}` : '尚无 A 股指数数据' }}</div>
      </section>

      <div class="overview-right-bottom">
      <section class="overview-panel overview-candidate-panel" aria-labelledby="overviewCandidatesTitle">
        <div class="overview-panel-head">
          <div>
            <h3 id="overviewCandidatesTitle">候选池</h3>
          </div>
          <div class="overview-panel-actions">
            <span class="overview-update-time">{{ candidateState.count || candidateState.items.length }}只 · {{ freshnessText(candidateState.generatedAt) }}</span>
            <RouterLink to="/practice">查看全部 <span aria-hidden="true">›</span></RouterLink>
          </div>
        </div>
        <div v-if="candidateState.strategyCacheStale" class="overview-inline-notice warning" role="status">
          {{ candidateState.statusMessage || '策略已切换，等待重新扫描；旧候选不参与首页排序。' }}
        </div>
        <div v-else-if="candidateState.running" class="overview-inline-notice" role="status">
          新一轮候选正在计算，当前继续展示上一版有效结果。
        </div>
        <div v-if="candidateState.error && candidates.length" class="overview-inline-notice warning" role="status">
          候选更新暂时失败，继续展示缓存：{{ candidateState.error }}
        </div>
        <div
          v-if="candidates.length && !candidateState.strategyCacheStale"
          class="overview-candidate-table"
          role="table"
          :aria-label="candidatePeriod.historical ? '历史候选股' : '优先候选股'"
        >
          <div class="overview-candidate-table-head" role="row">
            <span role="columnheader">标的</span>
            <span role="columnheader" class="overview-candidate-wide-only">涨跌幅</span>
            <span role="columnheader">题材 / 行业</span>
            <span role="columnheader">策略</span>
          </div>
          <article v-for="candidate in candidates" :key="`${candidate.code}-${candidate.best_strategy}`" class="overview-candidate-row" role="row">
            <div role="cell">
              <strong>{{ candidateIdentity(candidate) }}</strong>
              <span class="overview-candidate-compact-only" :class="valueTone(candidate.change_pct)">{{ formatOverviewPercent(candidate.change_pct, 2, true) }}</span>
            </div>
            <span role="cell" class="overview-candidate-change overview-candidate-wide-only" :class="valueTone(candidate.change_pct)">{{ formatOverviewPercent(candidate.change_pct, 2, true) }}</span>
            <span role="cell" class="overview-candidate-theme">{{ candidate.themeLabel || candidate.industryLabel || '题材待归因' }}</span>
            <span role="cell" class="overview-candidate-strategy" :title="candidate.strategyLabel">{{ candidate.strategyDisplayLabel }}</span>
          </article>
        </div>
        <div v-else-if="candidateState.loading && !candidateState.loaded" class="overview-loading">正在读取候选池…</div>
        <div v-else class="overview-empty">{{ candidateState.error ? `候选池暂不可用：${candidateState.error}` : '当前没有可展示的候选标的' }}</div>
      </section>

      <section class="overview-panel overview-news-panel" aria-labelledby="overviewNewsTitle">
        <div class="overview-panel-head compact">
          <div class="overview-news-heading">
            <h3 id="overviewNewsTitle">财经快讯</h3>
            <span class="overview-news-mode">{{ overviewNewsModeLabel }}</span>
            <span v-if="realtimeNewsState.stale" class="overview-news-cache">缓存</span>
          </div>
          <div class="overview-panel-actions">
            <span class="overview-update-time">{{ freshnessText(realtimeNewsState.generatedAt) }}</span>
            <RouterLink to="/realtime-news">查看全部 <span aria-hidden="true">›</span></RouterLink>
          </div>
        </div>
        <ol v-if="overviewNewsItems.length" class="overview-news-list">
          <li
            v-for="item in overviewNewsItems"
            :key="item.id"
            class="overview-news-item"
            :class="{ important: item.important }"
          >
            <time :datetime="item.published_at || undefined">{{ realtimeNewsClock(item) }}</time>
            <span class="overview-news-source" :title="item.source_name || item.source_id">{{ item.source_name || item.source_id }}</span>
            <a
              v-if="item.url"
              class="overview-news-title"
              :href="item.url"
              :title="item.title"
              target="_blank"
              rel="noopener noreferrer"
            >{{ item.title }}</a>
            <span v-else class="overview-news-title" :title="item.title">{{ item.title }}</span>
          </li>
        </ol>
        <div v-else-if="realtimeNewsState.loading && !realtimeNewsState.loaded" class="overview-news-empty">正在读取财经快讯…</div>
        <div v-else class="overview-news-empty">{{ overviewNewsEmptyText }}</div>
      </section>
      </div>

      <section class="overview-panel overview-flow-panel" aria-labelledby="overviewFlowTitle">
        <div class="overview-panel-head">
          <div>
            <h3 id="overviewFlowTitle">板块与资金</h3>
          </div>
          <div class="overview-panel-actions">
            <span class="overview-update-time">{{ freshnessText(indicesState.moneyFlow?.generated_at || indicesState.sectors?.generated_at) }}</span>
            <RouterLink to="/indices?panel=market">查看详情 <span aria-hidden="true">›</span></RouterLink>
          </div>
        </div>
        <template v-if="flowDataAvailable">
          <div class="overview-flow-columns">
            <div>
              <h4>领涨板块</h4>
              <div v-for="row in sectorGains" :key="`gain-${row.name}`" class="overview-flow-row up">
                <span>{{ row.name }}</span><b>↑ {{ formatOverviewPercent(sectorMove(row), 2, true) }}</b>
              </div>
              <div v-if="!sectorGains.length" class="overview-mini-empty">暂无领涨板块</div>
            </div>
            <div>
              <h4>领跌板块</h4>
              <div v-for="row in sectorLosses" :key="`loss-${row.name}`" class="overview-flow-row down">
                <span>{{ row.name }}</span><b>↓ {{ formatOverviewPercent(sectorMove(row), 2, true) }}</b>
              </div>
              <div v-if="!sectorLosses.length" class="overview-mini-empty">暂无领跌板块</div>
            </div>
          </div>
          <div class="overview-flow-divider"></div>
          <div class="overview-flow-columns">
            <div>
              <h4>主力净流入</h4>
              <div v-for="row in moneyInflows" :key="`in-${row.code || row.name}`" class="overview-flow-row up">
                <span>{{ flowIdentity(row) }}</span><b>{{ formatOverviewYi(row.netFlowYi, true) }}</b>
              </div>
              <div v-if="!moneyInflows.length" class="overview-mini-empty">暂无净流入排行</div>
            </div>
            <div>
              <h4>主力净流出</h4>
              <div v-for="row in moneyOutflows" :key="`out-${row.code || row.name}`" class="overview-flow-row down">
                <span>{{ flowIdentity(row) }}</span><b>{{ formatOverviewYi(row.netFlowYi, true) }}</b>
              </div>
              <div v-if="!moneyOutflows.length" class="overview-mini-empty">暂无净流出排行</div>
            </div>
          </div>
        </template>
        <div v-else-if="indicesState.loading" class="overview-loading">正在读取板块与资金数据…</div>
        <div v-else class="overview-empty">板块与资金数据暂不可用</div>
      </section>
    </div>
    </div>
  </div>
</template>

<style scoped>
.overview-page {
  --overview-surface: #ffffff;
  --overview-surface-raised: #f3f5f7;
  --overview-surface-strong: #e9edf1;
  --overview-border: #cfd6dd;
  --overview-border-strong: #aeb9c4;
  --overview-text: #1d2731;
  --overview-muted: #66727e;
  --overview-faint: #66727e;
  --overview-accent: #536b82;
  --overview-accent-soft: #eef2f5;
  --overview-up: #b6534e;
  --overview-up-soft: rgba(182, 83, 78, .07);
  --overview-down: #34745d;
  --overview-down-soft: rgba(52, 116, 93, .07);
  --overview-warning: #7b5e30;
  --overview-warning-soft: rgba(140, 107, 53, .08);
  --overview-mainline-accent: #46627c;
  --overview-mainline-secondary: #4f5d69;
  color: var(--overview-text);
  display: grid;
  gap: 14px;
  font-variant-numeric: tabular-nums;
  min-width: 0;
  width: 100%;
}

:global(html[data-theme="dark"] .overview-page) {
  --overview-surface: #0d1218;
  --overview-surface-raised: #121920;
  --overview-surface-strong: #18212a;
  --overview-border: #303c47;
  --overview-border-strong: #43515e;
  --overview-text: #e2e7ec;
  --overview-muted: #8b97a3;
  --overview-faint: #7d8995;
  --overview-accent: #7b8fa3;
  --overview-accent-soft: rgba(111, 130, 149, .12);
  --overview-up: #d06e68;
  --overview-up-soft: rgba(208, 110, 104, .07);
  --overview-down: #559a7f;
  --overview-down-soft: rgba(85, 154, 127, .07);
  --overview-warning: #b18b52;
  --overview-warning-soft: rgba(177, 139, 82, .08);
  --overview-mainline-accent: #9bb1c5;
  --overview-mainline-secondary: #aab5bf;
}

.overview-command-head,
.overview-panel,
.overview-kpi,
.overview-banner {
  border: 1px solid var(--overview-border);
  background: var(--overview-surface);
  box-shadow: none;
}

.overview-command-head,
.overview-panel { border-color: var(--overview-border-strong); }

.overview-command-head {
  align-items: flex-end;
  background: var(--overview-surface-raised);
  border-radius: 14px;
  display: flex;
  gap: 20px;
  justify-content: space-between;
  overflow: hidden;
  padding: 18px 20px;
  position: relative;
}

.overview-command-head > div:first-child {
  align-items: baseline;
  display: flex;
  flex: 1 1 auto;
  gap: 12px;
  min-width: 0;
}

.overview-command-head h2 {
  color: var(--overview-text);
  font-size: clamp(22px, 2.2vw, 30px);
  letter-spacing: -.03em;
  line-height: 1.15;
  margin: 0;
  white-space: nowrap;
}

.overview-market-summary {
  color: var(--overview-mainline-secondary);
  font-size: 12px;
  font-weight: 500;
  line-height: 1.35;
  margin: 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.overview-command-meta {
  align-items: center;
  color: var(--overview-muted);
  display: flex;
  flex: 0 0 auto;
  font-size: 11px;
  gap: 7px;
  white-space: nowrap;
}

.overview-live-dot {
  background: var(--overview-down);
  border-radius: 50%;
  box-shadow: 0 0 0 4px var(--overview-down-soft);
  height: 6px;
  width: 6px;
}

.overview-stale-badge {
  background: var(--overview-warning-soft);
  border: 1px solid color-mix(in srgb, var(--overview-warning) 40%, transparent);
  border-radius: 999px;
  color: var(--overview-warning);
  padding: 2px 7px;
}

.overview-banner {
  align-items: center;
  border-radius: 10px;
  display: grid;
  font-size: 12px;
  gap: 10px;
  grid-template-columns: auto minmax(0, 1fr) auto;
  padding: 10px 13px;
}

.overview-banner.warning { background: var(--overview-warning-soft); color: var(--overview-warning); }
.overview-banner.negative { background: var(--overview-up-soft); color: var(--overview-up); }
.overview-banner a { color: inherit; font-weight: 800; }

.overview-kpi-strip {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(5, minmax(0, 1fr));
}

.overview-kpi {
  border-radius: 11px;
  min-width: 0;
  overflow: hidden;
  padding: 12px 13px;
  position: relative;
}

.overview-kpi-label {
  color: var(--overview-muted);
  font-size: 10px;
  font-weight: 750;
  letter-spacing: .04em;
}

.overview-kpi strong {
  align-items: baseline;
  color: var(--overview-text);
  display: flex;
  font-size: 22px;
  gap: 7px;
  line-height: 1.2;
  margin-top: 5px;
  min-height: 26px;
  white-space: nowrap;
}

.overview-kpi strong b { font-size: 17px; }
.overview-kpi strong i { color: var(--overview-muted); font-size: 13px; font-style: normal; }
.overview-kpi > span { color: var(--overview-muted); display: block; font-size: 10px; margin-top: 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overview-kpi > span b { font-weight: 800; }
.overview-kpi > span i { font-style: normal; }
.overview-kpi.up strong,
.overview-kpi .up { color: var(--overview-up); }
.overview-kpi.down strong,
.overview-kpi .down { color: var(--overview-down); }

.overview-primary-grid {
  align-items: start;
  display: grid;
  gap: 14px;
  grid-template-columns: minmax(0, 1.85fr) minmax(330px, .85fr);
}

.overview-terminal-grid { display: grid; gap: 14px; min-width: 0; }
.overview-panel { border-radius: 13px; min-width: 0; overflow: hidden; padding: 15px; }
.overview-candidate-panel,
.overview-mainline-panel { container-type: inline-size; }

.overview-panel-head {
  align-items: center;
  display: flex;
  gap: 12px;
  justify-content: space-between;
  margin-bottom: 12px;
  min-width: 0;
}

.overview-panel-head.compact { margin-bottom: 10px; }
.overview-panel-head h3 { color: var(--overview-text); font-size: 15px; letter-spacing: -.01em; margin: 0; }
.overview-panel-actions { align-items: center; color: var(--overview-faint); display: flex; font-size: 10px; gap: 10px; }
.overview-panel a { color: var(--overview-accent); font-size: 11px; font-weight: 800; text-decoration: none; white-space: nowrap; }
.overview-panel a:hover { text-decoration: underline; }
.overview-panel a:focus-visible { outline: 2px solid var(--overview-accent); outline-offset: 3px; border-radius: 2px; }
.overview-detail-link { align-self: flex-start; }

.overview-news-heading { align-items: center; display: flex; gap: 7px; min-width: 0; }
.overview-news-mode,
.overview-news-cache {
  border: 1px solid var(--overview-border-strong);
  border-radius: 3px;
  color: var(--overview-muted);
  flex: 0 0 auto;
  font-size: 9px;
  font-weight: 750;
  line-height: 1;
  padding: 3px 5px;
}
.overview-news-mode { background: var(--overview-surface-raised); }
.overview-news-cache { background: var(--overview-warning-soft); border-color: color-mix(in srgb, var(--overview-warning) 34%, var(--overview-border)); color: var(--overview-warning); }
.overview-news-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); list-style: none; margin: 0; min-width: 0; padding: 0; }
.overview-news-item {
  align-items: center;
  border-right: 1px solid var(--overview-border);
  display: grid;
  gap: 7px;
  grid-template-columns: 36px minmax(72px, 100px) minmax(0, 1fr);
  min-width: 0;
  padding: 5px 10px;
}
.overview-news-item:last-child { border-right: 0; }
.overview-news-item time { color: var(--overview-faint); font-size: 10px; font-weight: 800; white-space: nowrap; }
.overview-news-source { color: var(--overview-accent); font-size: 9px; font-weight: 750; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overview-news-item .overview-news-title { color: var(--overview-text); font-size: 10px; font-weight: 650; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overview-news-item.important .overview-news-title { color: var(--overview-up); }
.overview-news-empty { color: var(--overview-faint); font-size: 10px; padding: 5px 1px; }

.overview-inline-notice {
  background: var(--overview-accent-soft);
  border: 1px solid color-mix(in srgb, var(--overview-accent) 24%, transparent);
  border-radius: 8px;
  color: var(--overview-accent);
  font-size: 10px;
  margin-bottom: 10px;
  padding: 7px 9px;
}

.overview-inline-notice.warning { background: var(--overview-warning-soft); border-color: color-mix(in srgb, var(--overview-warning) 30%, transparent); color: var(--overview-warning); }

.overview-index-grid {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  grid-template-rows: repeat(2, minmax(0, 1fr));
  min-height: 0;
}

.overview-index-tile {
  background: var(--overview-surface-raised);
  border: 1px solid var(--overview-border);
  border-radius: 9px;
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 10px;
}

.overview-index-tile > div { align-items: center; display: flex; gap: 6px; justify-content: space-between; }
.overview-index-tile > .overview-index-quote { align-items: baseline; margin-top: 5px; }
.overview-index-tile span { color: var(--overview-muted); font-size: 10px; font-weight: 750; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overview-index-tile time { color: var(--overview-faint); font-size: 9px; }
.overview-index-tile strong { color: var(--overview-text); display: block; font-size: 17px; }
.overview-index-tile b { display: block; font-size: 11px; margin-left: auto; text-align: right; }
.overview-index-tile.up b { color: var(--overview-up); }
.overview-index-tile.down b { color: var(--overview-down); }
.overview-index-tile.flat b { color: var(--overview-muted); }
.overview-index-tile :deep(.sparkline) {
  display: block;
  flex: 1 1 34px;
  height: auto;
  margin-top: 5px;
  min-height: 26px;
  width: 100%;
}
.overview-index-tile.up :deep(.sparkline) { color: var(--overview-up); }
.overview-index-tile.down :deep(.sparkline) { color: var(--overview-down); }
.overview-index-tile.flat :deep(.sparkline) { color: var(--overview-muted); }

.overview-chart-wrap {
  --market-breadth-limit-down: var(--overview-down);
  --market-breadth-limit-up: var(--overview-up);
  --market-breadth-broken-limit: var(--overview-warning);
  --market-breadth-red: var(--overview-up);
  --market-breadth-green: var(--overview-down);
  --market-breadth-estimated-turnover: var(--overview-warning);
  --market-breadth-actual-turnover: var(--overview-accent);
  --market-breadth-previous-turnover: var(--overview-faint);
  --market-breadth-turnover-increment: var(--overview-down);
  --market-breadth-same-time-delta: var(--overview-muted);
  background: var(--overview-surface-raised);
  border: 1px solid var(--overview-border);
  border-radius: 10px;
  margin-top: 10px;
  min-height: 260px;
  overflow: hidden;
  padding: 10px;
}

.overview-chart-wrap :deep(.market-breadth-card) { border: 0; box-shadow: none; padding: 0; }
.overview-chart-wrap :deep(.market-breadth-chart-wrap) { min-height: 220px; }

.overview-breadth-unavailable {
  --breadth-reference-position: 50%;
  align-items: center;
  background-image:
    linear-gradient(to right, color-mix(in srgb, var(--overview-border) 48%, transparent) 1px, transparent 1px),
    linear-gradient(to bottom, color-mix(in srgb, var(--overview-border) 48%, transparent) 1px, transparent 1px);
  background-size: 12.5% 25%;
  display: flex;
  justify-content: center;
  min-height: 238px;
  overflow: hidden;
  position: relative;
}
.overview-breadth-unavailable::before {
  border-bottom: 1px solid var(--overview-border-strong);
  border-left: 1px solid var(--overview-border-strong);
  bottom: 18px;
  content: '';
  left: 28px;
  pointer-events: none;
  position: absolute;
  right: 18px;
  top: 16px;
}
.overview-breadth-reference-line {
  border-top: 1px dashed color-mix(in srgb, var(--overview-accent) 55%, transparent);
  left: 28px;
  pointer-events: none;
  position: absolute;
  right: 18px;
  top: var(--breadth-reference-position);
}
.overview-breadth-reference-line span {
  background: var(--overview-accent);
  border: 3px solid var(--overview-surface-raised);
  border-radius: 50%;
  height: 9px;
  position: absolute;
  right: -1px;
  top: -5px;
  width: 9px;
}
.overview-breadth-unavailable-copy {
  background: color-mix(in srgb, var(--overview-surface-raised) 90%, transparent);
  border: 1px solid var(--overview-border);
  border-radius: 8px;
  display: grid;
  gap: 4px;
  padding: 9px 12px;
  position: relative;
  text-align: center;
}
.overview-breadth-unavailable-copy strong { color: var(--overview-text); font-size: 11px; }
.overview-breadth-unavailable-copy span { color: var(--overview-muted); font-size: 9px; }

.overview-loading,
.overview-empty {
  align-items: center;
  color: var(--overview-muted);
  display: flex;
  font-size: 11px;
  justify-content: center;
  min-height: 118px;
  padding: 18px;
  text-align: center;
}
.overview-empty.compact { min-height: 238px; }

.overview-theme-rankings { display: grid; gap: 8px; grid-template-columns: repeat(2, minmax(0, 1fr)); min-width: 0; }
.overview-theme-ranking { border: 1px solid var(--overview-border); border-radius: 7px; min-width: 0; overflow: hidden; }
.overview-theme-ranking > h4 { background: var(--overview-surface-raised); border-bottom: 1px solid var(--overview-border-strong); color: var(--overview-text); font-size: 11px; margin: 0; padding: 6px 8px; }
.overview-theme-list { display: grid; }
.overview-theme-table-head,
.overview-theme-row { align-items: center; display: grid; gap: 14px; grid-template-columns: minmax(164px, 1fr) minmax(180px, 1.08fr); }
.overview-theme-table-head { background: var(--overview-surface-raised); border-bottom: 1px solid var(--overview-border-strong); color: var(--overview-muted); font-size: 9px; padding: 0 2px 5px; }
.overview-theme-row { border-bottom: 1px solid var(--overview-border); padding: 7px 2px; }
.overview-theme-row:last-child { border-bottom: 0; }
.overview-theme-row div { min-width: 0; }
.overview-theme-row strong { display: block; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overview-theme-copy { display: grid; gap: 5px; }
.overview-theme-title { align-items: center; display: flex; gap: 7px; min-width: 0; }
.overview-theme-lifecycle {
  border-left: 1px solid var(--overview-border-strong);
  color: var(--overview-mainline-accent);
  flex: 0 0 auto;
  font-size: 9px;
  font-weight: 650;
  line-height: 1;
  padding-left: 7px;
  white-space: nowrap;
}
.overview-theme-meta {
  color: var(--overview-mainline-secondary);
  display: block;
  font-size: 9px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.overview-theme-leader { color: var(--overview-mainline-secondary); display: grid; gap: 5px; min-width: 0; }
.overview-theme-leader-toggle,
.overview-theme-mobile-toggle {
  color: inherit;
  cursor: pointer;
  font: inherit;
  margin: 0;
  padding: 0;
  text-align: left;
}
.overview-theme-leader-toggle {
  align-items: center;
  background: var(--overview-surface-raised);
  border: 1px solid var(--overview-border-strong);
  border-radius: 4px;
  box-sizing: border-box;
  color: var(--overview-text);
  display: flex;
  font-size: 10px;
  font-weight: 650;
  gap: 4px;
  justify-content: space-between;
  justify-self: stretch;
  max-width: 100%;
  min-width: 0;
  padding: 3px 7px;
  width: 100%;
}
.overview-theme-leader-toggle span { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overview-theme-leader-toggle svg { color: var(--overview-mainline-accent); }
.overview-theme-leader-toggle svg,
.overview-theme-mobile-toggle svg { fill: none; flex: 0 0 auto; height: 10px; stroke: currentColor; stroke-width: 1.7; width: 10px; }
.overview-theme-leader-toggle[aria-expanded="true"] svg,
.overview-theme-mobile-toggle[aria-expanded="true"] svg { transform: rotate(180deg); }
.overview-theme-leader-toggle:hover { border-color: var(--overview-border-strong); color: var(--overview-text); }
.overview-theme-leader-toggle:focus-visible,
.overview-theme-mobile-toggle:focus-visible { border-radius: 3px; outline: 2px solid var(--overview-accent); outline-offset: 2px; }
.overview-theme-mobile-toggle { align-items: center; background: var(--overview-surface-raised); border: 1px solid var(--overview-border); border-radius: 4px; color: var(--overview-muted); display: none; font-size: 10px; font-weight: 650; gap: 3px; justify-self: start; padding: 3px 6px; }
.overview-theme-stock-popover {
  --overview-surface: #ffffff;
  --overview-surface-raised: #f3f5f7;
  --overview-border: #cfd6dd;
  --overview-border-strong: #aeb9c4;
  --overview-text: #1d2731;
  --overview-muted: #66727e;
  --overview-faint: #66727e;
  --overview-accent: #536b82;
  --overview-up: #b6534e;
  --overview-down: #34745d;
  background: var(--overview-surface);
  border: 1px solid var(--overview-border-strong);
  border-radius: 7px;
  box-shadow: 0 14px 34px rgba(15, 23, 42, .2);
  color: var(--overview-text);
  font-variant-numeric: tabular-nums;
  max-height: min(260px, 60vh);
  overflow-y: auto;
  padding: 5px;
  position: fixed;
  z-index: 2000;
}
.overview-theme-stock-popover-head { align-items: center; display: flex; justify-content: space-between; padding: 1px 1px 5px; }
.overview-theme-stock-popover-head > div { align-items: baseline; display: flex; gap: 7px; }
.overview-theme-stock-popover-head strong { font-size: 11px; }
.overview-theme-stock-popover-head span { color: var(--overview-muted); font-size: 9px; }
.overview-theme-stock-popover-head button { background: transparent; border: 0; color: var(--overview-muted); cursor: pointer; font-size: 9px; padding: 2px 0 2px 8px; }
.overview-theme-stock-popover-head button:focus-visible { border-radius: 2px; outline: 2px solid var(--overview-accent); outline-offset: 2px; }
.overview-theme-stock-list-head,
.overview-theme-stock {
  align-items: center;
  display: grid;
  gap: 3px;
  grid-template-columns: minmax(0, 1fr) 42px 46px;
}
.overview-theme-stock-list-head { color: var(--overview-faint); font-size: 9px; padding: 2px 4px 3px; }
.overview-theme-stock-list-head span:last-child { text-align: left; }
.overview-theme-stock-list { display: grid; }
.overview-theme-stock {
  background: var(--overview-surface-raised);
  border-radius: 4px;
  font-size: 9px;
  min-width: 0;
  padding: 4px;
}
.overview-theme-stock + .overview-theme-stock { margin-top: 2px; }
.overview-theme-stock-name { align-items: baseline; display: flex; gap: 4px; min-width: 0; }
.overview-theme-stock-name b { color: var(--overview-muted); flex: 0 0 auto; font-size: 9px; }
.overview-theme-stock-name strong { color: var(--overview-text); font-size: 9px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overview-theme-stock > small { color: var(--overview-muted); font-size: 9px; white-space: nowrap; }
.overview-theme-stock > em { color: var(--overview-muted); font-style: normal; white-space: nowrap; }
.overview-theme-stock > em.up { color: var(--overview-up); }
.overview-theme-stock > em.down { color: var(--overview-down); }
:global(html[data-theme="dark"] .overview-theme-stock-popover) {
  --overview-surface: #0d1218;
  --overview-surface-raised: #121920;
  --overview-border: #303c47;
  --overview-border-strong: #43515e;
  --overview-text: #e2e7ec;
  --overview-muted: #8b97a3;
  --overview-faint: #7d8995;
  --overview-accent: #7b8fa3;
  --overview-up: #d06e68;
  --overview-down: #559a7f;
  box-shadow: 0 16px 38px rgba(0, 0, 0, .48);
}
.overview-secondary-grid { display: grid; gap: 14px; grid-template-columns: minmax(0, 1.35fr) minmax(360px, .85fr); }
.overview-candidate-table { display: grid; }
.overview-candidate-table-head,
.overview-candidate-row { align-items: center; display: grid; gap: 12px; grid-template-columns: minmax(150px, 1fr) minmax(180px, 1.35fr) minmax(100px, .7fr); }
.overview-candidate-table-head { background: var(--overview-surface-raised); border-bottom: 1px solid var(--overview-border-strong); color: var(--overview-muted); font-size: 9px; padding: 0 8px 7px; }
.overview-candidate-row { border-bottom: 1px solid var(--overview-border); min-height: 50px; padding: 7px 8px; }
.overview-candidate-row:last-child { border-bottom: 0; }
.overview-candidate-row > div { min-width: 0; }
.overview-candidate-row strong { display: block; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overview-candidate-row div span { color: var(--overview-muted); display: block; font-size: 9px; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overview-candidate-row div span.up { color: var(--overview-up); }
.overview-candidate-row div span.down { color: var(--overview-down); }
.overview-candidate-wide-only { display: none; }
.overview-candidate-change { font-size: 10px; font-weight: 800; white-space: nowrap; }
.overview-candidate-change.up { color: var(--overview-up); }
.overview-candidate-change.down { color: var(--overview-down); }
.overview-candidate-strategy { color: var(--overview-text); font-size: 10px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overview-candidate-theme { color: var(--overview-muted); font-size: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overview-flow-columns { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.overview-flow-columns h4 { color: var(--overview-muted); font-size: 9px; letter-spacing: .04em; margin: 0 0 5px; }
.overview-flow-row { align-items: center; border-bottom: 1px solid var(--overview-border); display: flex; font-size: 10px; gap: 8px; justify-content: space-between; min-height: 30px; }
.overview-flow-row span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overview-flow-row b { flex: 0 0 auto; }
.overview-flow-row.up b { color: var(--overview-up); }
.overview-flow-row.down b { color: var(--overview-down); }
.overview-flow-divider { border-top: 1px solid var(--overview-border-strong); margin: 10px 0; }
.overview-mini-empty { color: var(--overview-faint); font-size: 9px; padding: 8px 0; }

.overview-mainline-panel[data-mainline-layout="compact"] .overview-theme-list {
  grid-template-rows: auto repeat(3, minmax(0, 1fr));
}

.overview-mainline-panel[data-mainline-layout="summary"] .overview-theme-list {
  grid-template-rows: auto repeat(2, minmax(0, 1fr));
}

.overview-mainline-panel[data-mainline-layout="compact"] .overview-theme-row,
.overview-mainline-panel[data-mainline-layout="summary"] .overview-theme-row {
  min-height: 0;
  padding-block: 1px;
}

.overview-mainline-panel[data-mainline-layout="compact"] .overview-theme-row:nth-child(n + 4),
.overview-mainline-panel[data-mainline-layout="compact"] .overview-theme-row small,
.overview-mainline-panel[data-mainline-layout="summary"] .overview-theme-row:nth-child(n + 3),
.overview-mainline-panel[data-mainline-layout="summary"] .overview-theme-row small { display: none; }

@media (max-width: 1120px) {
  .overview-kpi-strip { grid-template-columns: repeat(5, minmax(180px, 1fr)); overflow-x: auto; padding-bottom: 3px; scrollbar-width: thin; }
  .overview-primary-grid { grid-template-columns: minmax(0, 1.5fr) minmax(300px, .9fr); }
  .overview-index-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .overview-secondary-grid { grid-template-columns: 1fr; }
}

@media (max-width: 900px) {
  .overview-primary-grid { grid-template-columns: 1fr; }
}

@media (max-width: 720px) {
  .overview-page { gap: 10px; }
  .overview-terminal-grid,
  .overview-primary-grid,
  .overview-secondary-grid,
  .overview-right-bottom { display: contents; }
  .overview-market-panel { order: 1; }
  .overview-indices-panel { order: 2; }
  .overview-flow-panel { order: 3; }
  .overview-mainline-panel { order: 4; }
  .overview-candidate-panel { order: 5; }
  .overview-news-panel { order: 6; }
  .overview-command-head { align-items: flex-start; display: grid; padding: 14px; }
  .overview-command-meta { justify-self: start; }
  .overview-banner { align-items: start; grid-template-columns: 1fr; }
  .overview-kpi-strip { grid-template-columns: repeat(5, minmax(156px, 1fr)); margin-inline: -1px; }
  .overview-kpi { padding: 10px 11px; }
  .overview-kpi strong { font-size: 19px; }
  .overview-panel { padding: 12px; }
  .overview-panel-head { align-items: flex-start; }
  .overview-panel-actions { align-items: flex-end; display: grid; justify-items: end; }
  .overview-news-list { grid-template-columns: 1fr; }
  .overview-news-item { border-bottom: 1px solid var(--overview-border); border-right: 0; grid-template-columns: 38px minmax(72px, 105px) minmax(0, 1fr); }
  .overview-news-item:last-child { border-bottom: 0; }
  .overview-chart-wrap { min-height: 230px; padding: 6px; }
  .overview-theme-rankings { grid-template-columns: 1fr; }
  .overview-theme-table-head { display: none; }
  .overview-theme-row { grid-template-columns: minmax(0, 1fr); }
  .overview-theme-leader { display: none; }
  .overview-theme-mobile-toggle { display: inline-flex; }
  .overview-theme-stock-popover { padding: 7px; }
  .overview-candidate-table-head { display: none; }
  .overview-candidate-row { gap: 7px; grid-template-columns: minmax(96px, 1fr) minmax(0, 1fr) minmax(54px, .55fr); }
}

@media (max-width: 440px) {
  .overview-news-item { gap: 6px; grid-template-columns: 36px 76px minmax(0, 1fr); padding-inline: 4px; }
  .overview-index-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .overview-index-tile time { display: none; }
  .overview-flow-columns { grid-template-columns: 1fr; }
  .overview-flow-divider { margin: 4px 0; }
}

@media (prefers-reduced-motion: reduce) {
  .overview-page *,
  .overview-page *::before,
  .overview-page *::after { scroll-behavior: auto !important; transition: none !important; }
}

@media (min-width: 721px) {
  :global(html:not([data-theme="tongdaxin"]) body.overview-terminal-open main) {
    padding: 6px max(clamp(9px, 1.8vw, 24px), calc((100vw - 1440px) / 2));
  }

  .overview-page {
    --overview-terminal-left: minmax(0, 1.55fr);
    --overview-terminal-right: minmax(340px, .9fr);
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-height: var(--overview-viewport-height, calc(100dvh - 148px));
  }

  .overview-command-head {
    align-items: center;
    border-radius: 7px;
    flex: 0 0 38px;
    min-height: 38px;
    padding: 5px 10px;
  }

  .overview-command-head > div:first-child {
    align-items: center;
    display: flex;
    gap: 10px;
    min-width: 0;
  }

  .overview-command-head h2 { font-size: 16px; margin: 0; white-space: nowrap; }
  .overview-market-summary { font-size: 10px; }
  .overview-command-meta { font-size: 10px; }

  .overview-banner {
    flex: 0 0 auto;
    min-height: 28px;
    padding: 5px 9px;
  }

  .overview-kpi-strip {
    flex: 0 0 58px;
    gap: 5px;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    min-height: 58px;
    overflow: hidden;
    padding: 0;
  }

  .overview-kpi {
    border-radius: 6px;
    padding: 6px 9px;
  }

  .overview-kpi-label { font-size: 9px; }
  .overview-kpi strong { font-size: 16px; line-height: 1.1; margin-top: 2px; min-height: 18px; }
  .overview-kpi strong b { font-size: 13px; }
  .overview-kpi > span { font-size: 9px; margin-top: 2px; }

  .overview-terminal-grid {
    display: grid;
    flex: 1 1 auto;
    gap: 6px;
    grid-template-areas:
      "market side"
      "mainline side";
    grid-template-columns: var(--overview-terminal-left) var(--overview-terminal-right);
    grid-template-rows: minmax(0, .9fr) minmax(0, 1.1fr);
    min-height: 0;
  }

  .overview-primary-grid { display: contents; }
  .overview-secondary-grid {
    display: grid;
    gap: 6px;
    grid-area: side;
    grid-template-areas:
      "indices"
      "flow"
      "candidate";
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(92px, .65fr) minmax(92px, 1fr) minmax(118px, 1.1fr);
    min-height: 0;
    min-width: 0;
    overflow: hidden;
  }

  .overview-market-panel { grid-area: market; }
  .overview-indices-panel { grid-area: indices; }
  .overview-flow-panel { grid-area: flow; }
  .overview-mainline-panel { grid-area: mainline; }
  .overview-right-bottom {
    display: grid;
    gap: 6px;
    grid-area: candidate;
    grid-template-rows: minmax(0, 1fr) 118px;
    min-height: 0;
    min-width: 0;
    overflow: hidden;
  }

  .overview-panel {
    border-radius: 7px;
    height: 100%;
    min-height: 0;
    padding: 7px 8px;
  }

  .overview-panel-head,
  .overview-panel-head.compact { margin-bottom: 5px; min-height: 23px; }
  .overview-panel-head h3 { font-size: 12px; margin-top: 0; }
  .overview-panel-actions { font-size: 9px; gap: 7px; }
  .overview-panel a { font-size: 10px; }
  .overview-inline-notice { font-size: 9px; margin-bottom: 4px; padding: 3px 6px; }
  .overview-right-bottom > .overview-news-panel { display: flex; flex-direction: column; padding: 4px 7px; }
  .overview-right-bottom .overview-news-panel .overview-panel-head,
  .overview-right-bottom .overview-news-panel .overview-panel-head.compact { margin-bottom: 1px; min-height: 18px; }
  .overview-right-bottom .overview-news-mode,
  .overview-right-bottom .overview-news-cache { padding: 2px 4px; }
  .overview-right-bottom .overview-news-list { flex: 1 1 auto; grid-auto-rows: minmax(0, 1fr); grid-template-columns: 1fr; min-height: 0; overflow: hidden; }
  .overview-right-bottom .overview-news-item {
    border-bottom: 1px solid var(--overview-border);
    border-right: 0;
    gap: 5px;
    grid-template-columns: 30px 100px minmax(0, 1fr);
    min-height: 0;
    padding: 1px 4px;
  }
  .overview-right-bottom .overview-news-item:last-child { border-bottom: 0; }
  .overview-right-bottom .overview-news-item time,
  .overview-right-bottom .overview-news-source,
  .overview-right-bottom .overview-news-item .overview-news-title { font-size: 9px; }
  .overview-right-bottom .overview-news-empty { padding: 2px 1px; }

  .overview-market-panel,
  .overview-indices-panel {
    display: flex;
    flex-direction: column;
    min-height: 0;
  }

  .overview-index-grid {
    flex: 1 1 auto;
    gap: 4px;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    grid-template-rows: repeat(2, minmax(0, 1fr));
  }
  .overview-index-tile { border-radius: 5px; padding: 4px 6px; }
  .overview-index-tile span { font-size: 9px; }
  .overview-index-tile time { font-size: 9px; }
  .overview-index-tile > .overview-index-quote { margin-top: 1px; }
  .overview-index-tile strong { font-size: 13px; }
  .overview-index-tile b { font-size: 11px; }
  .overview-index-tile :deep(.sparkline) { margin-top: 2px; min-height: 18px; }

  .overview-chart-wrap {
    display: flex;
    flex: 1 1 auto;
    margin-top: 4px;
    min-height: 0;
    padding: 3px 5px;
  }

  .overview-chart-wrap :deep(.market-breadth-card.terminal) {
    display: flex;
    flex: 1 1 auto;
    flex-direction: column;
    height: 100%;
    margin: 0;
    min-height: 0;
  }

  .overview-chart-wrap :deep(.market-breadth-card.terminal .market-breadth-head) {
    flex: 0 0 auto;
    gap: 5px;
    grid-template-areas: "controls meta";
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .overview-chart-wrap :deep(.market-breadth-card.terminal .market-breadth-heading) { display: none; }
  .overview-chart-wrap :deep(.market-breadth-card.terminal .market-breadth-controls) { gap: 4px; justify-content: flex-start; }
  .overview-chart-wrap :deep(.market-breadth-card.terminal .market-breadth-toggle) { min-height: 22px; padding: 3px 6px; }
  .overview-chart-wrap :deep(.market-breadth-card.terminal .market-breadth-toggle small) { display: none; }
  .overview-chart-wrap :deep(.market-breadth-card.terminal .market-breadth-head-meta) {
    box-sizing: border-box;
    min-width: 68px;
    overflow: hidden;
    padding: 0 6px 0 2px;
    font-size: 9px;
  }
  .overview-chart-wrap :deep(.market-breadth-card.terminal .market-breadth-time) {
    display: block;
    max-width: 100%;
    overflow: hidden;
    line-height: 1;
    text-align: right;
    white-space: nowrap;
  }
  .overview-chart-wrap :deep(.market-breadth-card.terminal .market-breadth-chart-wrap) { flex: 1 1 auto; margin-top: 1px; min-height: 0; padding: 0; }
  .overview-chart-wrap :deep(.market-breadth-card.terminal .market-breadth-chart) { height: 100%; min-height: 0; width: 100%; }
  .overview-chart-wrap :deep(.market-breadth-card.terminal .market-breadth-compact-tooltip) { font-size: 9px; }
  .overview-breadth-unavailable,
  .overview-empty.compact { flex: 1 1 auto; min-height: 0; width: 100%; }

  .overview-mainline-panel,
  .overview-indices-panel,
  .overview-candidate-panel,
  .overview-flow-panel { display: flex; flex-direction: column; min-height: 0; }

  .overview-theme-rankings { align-items: start; flex: 0 0 auto; min-height: 0; overflow: hidden; }
  .overview-theme-ranking { border-inline: 0; border-radius: 0; display: flex; flex-direction: column; min-height: 0; }
  .overview-theme-ranking > h4 { align-items: center; display: flex; flex: 0 0 21px; font-size: 10px; line-height: 1; padding: 0 6px; }
  .overview-theme-ranking > h4 > span { transform: translateY(.75px); }
  .overview-theme-list { align-content: start; flex: 0 0 auto; min-height: 0; overflow: hidden; }
  .overview-theme-table-head,
  .overview-theme-row { gap: 5px; grid-template-columns: minmax(88px, 1fr) minmax(82px, 1fr); }
  .overview-theme-table-head { font-size: 9px; height: 17px; line-height: 1; padding: 0 5px; }
  .overview-theme-row { min-height: 25px; padding: 3px 5px; }
  .overview-theme-row strong { font-size: 10px; }
  .overview-theme-copy { gap: 5px; }
  .overview-theme-leader { gap: 3px; }
  .overview-theme-title { gap: 4px; }
  .overview-theme-lifecycle { font-size: 9px; padding-left: 4px; }
  .overview-theme-leader { font-size: 9px; }
  .overview-theme-meta { font-size: 9px; }
  .overview-theme-leader-toggle { font-size: 10px; padding: 2px 6px; }
  .overview-mainline-panel[data-mainline-layout="full"] .overview-theme-list {
    grid-template-rows: auto repeat(5, 38px);
  }

  .overview-candidate-table {
    flex: 1 1 auto;
    grid-auto-rows: minmax(0, 1fr);
    grid-template-rows: auto;
    min-height: 0;
    overflow: hidden;
  }
  .overview-candidate-table-head,
  .overview-candidate-row {
    gap: 7px;
    grid-template-columns: minmax(140px, 1fr) 72px minmax(180px, 1.5fr) minmax(90px, .7fr);
  }
  .overview-candidate-wide-only { display: block; }
  .overview-candidate-compact-only { display: none !important; }
  .overview-candidate-table-head { box-shadow: inset 0 1px 0 var(--overview-border-strong); font-size: 9px; height: 17px; line-height: 1; padding: 0 5px; }
  .overview-theme-table-head > span,
  .overview-candidate-table-head > span { transform: translateY(.75px); }
  .overview-candidate-row { min-height: 0; padding: 3px 5px; }
  .overview-candidate-row strong { font-size: 10px; }
  .overview-candidate-row div span { font-size: 9px; margin-top: 0; }

  .overview-flow-columns { flex: 1 1 0; gap: 8px; min-height: 0; overflow: hidden; }
  .overview-flow-columns > div { display: flex; flex-direction: column; min-height: 0; }
  .overview-flow-columns h4 { font-size: 9px; margin-bottom: 2px; }
  .overview-flow-row { box-sizing: border-box; flex: 1 1 0; font-size: 9px; line-height: 1.25; min-height: 0; overflow: hidden; }
  .overview-flow-divider { margin: 4px 0; }
  .overview-mini-empty { font-size: 9px; padding: 3px 0; }
  .overview-loading,
  .overview-empty { flex: 1 1 auto; min-height: 0; padding: 8px; }

  .overview-page[data-layout="wide"] .overview-terminal-grid {
    grid-template-areas:
      "market market market market market market market market market market market market market market market market market market market market indices indices indices indices indices indices indices indices indices indices indices indices flow flow flow flow flow flow flow flow"
      "mainline mainline mainline mainline mainline mainline mainline mainline mainline mainline mainline mainline mainline mainline mainline candidate candidate candidate candidate candidate candidate candidate candidate candidate news news news news news news news news news news news news news news news news";
    grid-template-columns: repeat(40, minmax(0, 1fr));
  }
  .overview-page[data-layout="wide"] .overview-secondary-grid,
  .overview-page[data-layout="wide"] .overview-right-bottom { display: contents; }
  .overview-page[data-layout="wide"] .overview-indices-panel { grid-area: indices; }
  .overview-page[data-layout="wide"] .overview-flow-panel { grid-area: flow; }
  .overview-page[data-layout="wide"] .overview-candidate-panel { grid-area: candidate; }
  .overview-page[data-layout="wide"] .overview-news-panel { grid-area: news; }
  .overview-page[data-layout="wide"] .overview-news-list {
    align-content: start;
    flex: 1 1 auto;
    grid-auto-rows: 25px;
  }
  .overview-page[data-layout="wide"] .overview-candidate-table-head,
  .overview-page[data-layout="wide"] .overview-candidate-row {
    gap: 4px;
    grid-template-columns: minmax(90px, 1fr) minmax(46px, 1fr) minmax(0, 1fr) minmax(42px, 1fr);
  }
  .overview-page[data-layout="wide"] .overview-candidate-wide-only { display: block; }
  .overview-page[data-layout="wide"] .overview-candidate-compact-only { display: none !important; }
  .overview-page[data-layout="wide"] .overview-candidate-table-head .overview-candidate-wide-only,
  .overview-page[data-layout="wide"] .overview-candidate-change { text-align: left; }
  .overview-page[data-layout="wide"] .overview-candidate-table-head > :last-child,
  .overview-page[data-layout="wide"] .overview-candidate-strategy { text-align: right; }

  .overview-page[data-layout="wide"][data-density="comfortable"] {
    --overview-terminal-left: minmax(0, 1.55fr);
    --overview-terminal-right: minmax(380px, .9fr);
  }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-panel { padding: 9px 10px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-panel-head,
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-panel-head.compact {
    margin-bottom: 7px;
    min-height: 25px;
  }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-panel-head h3 { font-size: 14px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-panel-actions { font-size: 9px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-mainline-panel { align-self: start; height: auto; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-news-panel,
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-candidate-panel {
    align-self: start;
    height: var(--overview-mainline-panel-height, auto);
    max-height: var(--overview-mainline-panel-height, none);
  }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-right-bottom { grid-template-rows: minmax(0, 1fr) 126px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-right-bottom > .overview-news-panel { padding: 4px 8px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-right-bottom .overview-news-panel .overview-panel-head,
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-right-bottom .overview-news-panel .overview-panel-head.compact { margin-bottom: 1px; min-height: 19px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-index-grid { flex-basis: 54px; gap: 5px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-index-tile { padding: 5px 8px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-index-tile span { font-size: 9px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-index-tile strong { font-size: 14px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-index-tile b { font-size: 12px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-theme-row strong { font-size: 10px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-theme-lifecycle { font-size: 9px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-candidate-table-head { font-size: 9px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-candidate-row strong { font-size: 10px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-flow-columns h4 { font-size: 9px; }
  .overview-page[data-layout="wide"][data-density="comfortable"] .overview-flow-row { font-size: 9px; }

  .overview-page[data-layout="compact"] {
    --overview-terminal-left: minmax(0, 1.35fr);
    --overview-terminal-right: minmax(290px, .92fr);
  }
  .overview-page[data-layout="compact"] .overview-panel-actions > .overview-update-time { display: none; }
  .overview-page[data-layout="compact"] .overview-index-tile time { display: none; }
  .overview-page[data-layout="compact"] .overview-theme-table-head,
  .overview-page[data-layout="compact"] .overview-theme-row {
    grid-template-columns: minmax(88px, 1fr) minmax(82px, 1fr);
  }
  .overview-page[data-layout="compact"] .overview-candidate-table-head,
  .overview-page[data-layout="compact"] .overview-candidate-row {
    gap: 5px;
    grid-template-columns: minmax(92px, 1fr) minmax(104px, 1.15fr) minmax(64px, .7fr);
  }
  .overview-page[data-layout="compact"] .overview-candidate-wide-only { display: none; }
  .overview-page[data-layout="compact"] .overview-candidate-compact-only { display: block !important; }

  .overview-page[data-density="compact"] .overview-command-head {
    flex-basis: 32px;
    min-height: 32px;
  }
  .overview-page[data-density="compact"] .overview-kpi-strip {
    flex-basis: 50px;
    min-height: 50px;
  }
  .overview-page[data-density="compact"] .overview-kpi { padding-block: 4px; }
  .overview-page[data-density="compact"] .overview-kpi strong { font-size: 14px; min-height: 16px; }
  .overview-page[data-density="compact"] .overview-kpi strong b { font-size: 11px; }
  .overview-page[data-density="compact"] .overview-panel-head,
  .overview-page[data-density="compact"] .overview-panel-head.compact { min-height: 20px; }
  .overview-page[data-density="compact"] .overview-right-bottom { grid-template-rows: minmax(0, 1fr) 108px; }
  .overview-page[data-density="compact"] .overview-right-bottom > .overview-news-panel { padding: 3px 6px; }
  .overview-page[data-density="compact"] .overview-right-bottom .overview-news-panel .overview-panel-head,
  .overview-page[data-density="compact"] .overview-right-bottom .overview-news-panel .overview-panel-head.compact { margin-bottom: 0; min-height: 16px; }
  .overview-page[data-density="compact"] .overview-index-grid { flex-basis: 42px; }
  .overview-page[data-density="compact"] .overview-chart-wrap :deep(.market-breadth-card.terminal .market-breadth-toggle) {
    min-height: 18px;
    padding-block: 1px;
  }
  .overview-page[data-density="compact"] .overview-theme-row { min-height: 21px; padding-block: 2px; }
  .overview-page[data-layout="wide"][data-density="compact"] .overview-news-list { grid-auto-rows: 22px; }
  .overview-page[data-density="compact"] .overview-candidate-row:nth-child(n + 9) { display: none; }
  .overview-page[data-density="compact"] .overview-flow-row { min-height: 17px; }

  .overview-page[data-density="ultra-compact"] { gap: 4px; }
  .overview-page[data-density="ultra-compact"] .overview-command-head {
    flex-basis: 26px;
    min-height: 26px;
    padding-block: 2px;
  }
  .overview-page[data-density="ultra-compact"] .overview-command-head h2 { font-size: 13px; }
  .overview-page[data-density="ultra-compact"] .overview-market-summary { font-size: 9px; }
  .overview-page[data-density="ultra-compact"] .overview-kpi-strip {
    flex-basis: 52px;
    min-height: 52px;
  }
  .overview-page[data-density="ultra-compact"] .overview-kpi { padding: 3px 7px; }
  .overview-page[data-density="ultra-compact"] .overview-kpi strong { font-size: 12px; min-height: 14px; }
  .overview-page[data-density="ultra-compact"] .overview-kpi strong b { font-size: 10px; }
  .overview-page[data-density="ultra-compact"] .overview-kpi > span,
  .overview-page[data-density="ultra-compact"] .overview-kpi-label { font-size: 9px; }
  .overview-page[data-density="ultra-compact"] .overview-terminal-grid {
    gap: 4px;
  }
  .overview-page[data-density="ultra-compact"] .overview-primary-grid,
  .overview-page[data-density="ultra-compact"] .overview-secondary-grid { gap: 4px; }
  .overview-page[data-density="ultra-compact"] .overview-panel { padding: 4px 6px; }
  .overview-page[data-density="ultra-compact"] .overview-panel-head,
  .overview-page[data-density="ultra-compact"] .overview-panel-head.compact {
    margin-bottom: 2px;
    min-height: 17px;
  }
  .overview-page[data-density="ultra-compact"] .overview-panel-head h3 { font-size: 10px; }
  .overview-page[data-density="ultra-compact"] .overview-right-bottom { gap: 4px; grid-template-rows: minmax(0, 1fr) 94px; }
  .overview-page[data-density="ultra-compact"] .overview-right-bottom > .overview-news-panel { padding: 2px 5px; }
  .overview-page[data-density="ultra-compact"] .overview-right-bottom .overview-news-panel .overview-panel-head,
  .overview-page[data-density="ultra-compact"] .overview-right-bottom .overview-news-panel .overview-panel-head.compact { margin-bottom: 0; min-height: 14px; }
  .overview-page[data-density="ultra-compact"] .overview-news-mode,
  .overview-page[data-density="ultra-compact"] .overview-news-cache { padding-block: 2px; }
  .overview-page[data-density="ultra-compact"] .overview-news-item { padding-block: 0; }
  .overview-page[data-layout="wide"][data-density="ultra-compact"] .overview-news-list { grid-auto-rows: 20px; }
  .overview-page[data-layout="wide"][data-density="ultra-compact"] .overview-news-item:nth-child(n + 5) { display: none; }
  .overview-page[data-density="ultra-compact"] .overview-index-grid { flex-basis: 36px; }
  .overview-page[data-density="ultra-compact"] .overview-index-tile { padding-block: 2px; }
  .overview-page[data-density="ultra-compact"] .overview-index-tile strong { font-size: 10px; }
  .overview-page[data-density="ultra-compact"] .overview-index-tile b { font-size: 9px; }
  .overview-page[data-density="ultra-compact"] .overview-index-tile :deep(.sparkline) { min-height: 12px; }
  .overview-page[data-density="ultra-compact"] .overview-theme-row { min-height: 17px; padding-block: 1px; }
  .overview-page[data-density="ultra-compact"] .overview-candidate-table-head { display: none; }
  .overview-page[data-density="ultra-compact"] .overview-candidate-row { padding-block: 1px; }
  .overview-page[data-density="ultra-compact"] .overview-candidate-row:nth-child(n + 7) { display: none; }
  .overview-page[data-density="ultra-compact"] .overview-flow-row { min-height: 14px; }
  .overview-page[data-density="ultra-compact"] .overview-flow-divider { margin-block: 2px; }
}

@container (min-width: 420px) {
  .overview-page[data-layout="wide"] .overview-candidate-table {
    grid-template-columns: repeat(3, minmax(0, 1fr)) max-content;
  }
  .overview-page[data-layout="wide"] .overview-candidate-table-head,
  .overview-page[data-layout="wide"] .overview-candidate-row {
    gap: 0;
    grid-column: 1 / -1;
    grid-template-columns: subgrid;
  }
}

@container (max-width: 400px) {
  .overview-theme-rankings { grid-template-columns: 1fr; }
}

@media (min-width: 721px) {
  .overview-page[data-layout="compact"] .overview-terminal-grid {
    align-items: stretch;
    grid-template-areas:
      "market indices"
      "market flow"
      "mainline mainline"
      "bottom bottom";
    grid-template-columns: minmax(0, 1.35fr) minmax(280px, .92fr);
    grid-template-rows: repeat(4, auto);
    overflow: visible;
  }

  .overview-page[data-layout="compact"] .overview-primary-grid,
  .overview-page[data-layout="compact"] .overview-secondary-grid { display: contents; }

  .overview-page[data-layout="compact"] .overview-right-bottom {
    align-items: stretch;
    display: grid;
    gap: 6px;
    grid-area: bottom;
    grid-template-columns: minmax(288px, .72fr) minmax(0, 1.45fr);
    grid-template-rows: auto;
    overflow: visible;
  }

  .overview-page[data-layout="compact"] .overview-market-panel { grid-area: market; }
  .overview-page[data-layout="compact"] .overview-indices-panel { grid-area: indices; }
  .overview-page[data-layout="compact"] .overview-flow-panel { grid-area: flow; }
  .overview-page[data-layout="compact"] .overview-mainline-panel { grid-area: mainline; }
  .overview-page[data-layout="compact"] .overview-candidate-panel,
  .overview-page[data-layout="compact"] .overview-news-panel { grid-area: auto; }

  .overview-page[data-layout="compact"] .overview-panel {
    height: auto;
    padding: 7px 8px;
  }

  .overview-page[data-layout="compact"] .overview-indices-panel,
  .overview-page[data-layout="compact"] .overview-flow-panel {
    align-self: start;
    height: max-content;
    width: 100%;
  }

  .overview-page[data-layout="compact"] .overview-candidate-panel,
  .overview-page[data-layout="compact"] .overview-news-panel {
    align-self: stretch;
    height: auto;
    width: 100%;
  }

  .overview-page[data-layout="compact"] .overview-command-head {
    flex-basis: 38px;
    min-height: 38px;
  }

  .overview-page[data-layout="compact"] .overview-kpi-strip {
    flex-basis: 58px;
    min-height: 58px;
  }

  .overview-page[data-layout="compact"] .overview-kpi { padding: 6px 9px; }
  .overview-page[data-layout="compact"] .overview-kpi strong { font-size: 16px; min-height: 18px; }
  .overview-page[data-layout="compact"] .overview-kpi strong b { font-size: 13px; }
  .overview-page[data-layout="compact"] .overview-panel-head,
  .overview-page[data-layout="compact"] .overview-panel-head.compact { margin-bottom: 5px; min-height: 23px; }
  .overview-page[data-layout="compact"] .overview-panel-head h3 { font-size: 12px; }

  .overview-page[data-layout="compact"] .overview-candidate-table {
    flex: none;
    grid-auto-rows: minmax(28px, auto);
    overflow: visible;
  }

  .overview-page[data-layout="compact"] .overview-candidate-table-head,
  .overview-page[data-layout="compact"] .overview-candidate-row {
    box-sizing: border-box;
    gap: 4px;
    grid-template-columns: minmax(94px, 1.05fr) minmax(82px, .9fr) minmax(52px, .65fr);
    min-width: 0;
    width: 100%;
  }

  .overview-page[data-layout="compact"] .overview-index-grid {
    flex: none;
    grid-template-rows: repeat(2, minmax(64px, auto));
  }

  .overview-page[data-layout="compact"] .overview-candidate-row { min-height: 28px; padding-block: 4px; }
  .overview-page[data-layout="compact"] .overview-candidate-row:nth-child(n + 7),
  .overview-page[data-layout="compact"] .overview-candidate-row:nth-child(n + 9) { display: grid; }

  .overview-page[data-layout="compact"] .overview-news-list {
    flex: 1 1 auto;
    grid-auto-rows: minmax(24px, 1fr);
    overflow: visible;
  }

  .overview-page[data-layout="compact"] .overview-right-bottom .overview-news-item {
    min-height: 24px;
    padding-block: 3px;
  }

  .overview-page[data-layout="compact"] .overview-flow-columns { flex: none; }
  .overview-page[data-layout="compact"] .overview-flow-row { flex: none; min-height: 22px; }
}

@media (max-width: 720px) {
  .overview-command-head > div:first-child {
    align-items: start;
    display: grid;
    gap: 6px;
  }

  .overview-market-summary {
    display: -webkit-box;
    line-height: 1.45;
    overflow: hidden;
    white-space: normal;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 3;
  }

  .overview-kpi-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    margin-inline: 0;
    overflow: visible;
    padding-bottom: 0;
  }

  .overview-chart-wrap :deep(.market-breadth-card.terminal .market-breadth-chart-wrap) {
    height: clamp(280px, 78vw, 420px);
  }

  .overview-chart-wrap :deep(.market-breadth-card.terminal .market-breadth-chart) { height: 100%; }
}

@media (min-width: 520px) and (max-width: 720px) {
  .overview-kpi-strip { grid-template-columns: repeat(6, minmax(0, 1fr)); }
  .overview-kpi { grid-column: span 2; }
  .overview-kpi:nth-last-child(-n + 2) { grid-column: span 3; }
}

@media (max-width: 519px) {
  .overview-kpi:last-child { grid-column: 1 / -1; }
}

/* 直角主题采用金融终端式边框；状态圆点和数据进度轨道保留原有几何语义。 */
:global(html[data-corners="square"]) .overview-command-head,
:global(html[data-corners="square"]) .overview-stale-badge,
:global(html[data-corners="square"]) .overview-news-mode,
:global(html[data-corners="square"]) .overview-news-cache,
:global(html[data-corners="square"]) .overview-banner,
:global(html[data-corners="square"]) .overview-kpi,
:global(html[data-corners="square"]) .overview-panel,
:global(html[data-corners="square"]) .overview-inline-notice,
:global(html[data-corners="square"]) .overview-index-tile,
:global(html[data-corners="square"]) .overview-chart-wrap,
:global(html[data-corners="square"]) .overview-breadth-unavailable-copy,
:global(html[data-corners="square"]) .overview-theme-ranking,
:global(html[data-corners="square"]) .overview-theme-leader-toggle,
:global(html[data-corners="square"]) .overview-theme-mobile-toggle,
:global(html[data-corners="square"]) .overview-theme-stock-popover,
:global(html[data-corners="square"]) .overview-theme-stock,
:global(html[data-corners="square"]) .overview-panel a:focus-visible,
:global(html[data-corners="square"]) .overview-theme-leader-toggle:focus-visible,
:global(html[data-corners="square"]) .overview-theme-mobile-toggle:focus-visible,
:global(html[data-corners="square"]) .overview-theme-stock-popover-head button:focus-visible {
  border-radius: 0;
}

:global(html[data-corners="square"]) .overview-chart-wrap :deep(.market-breadth-card),
:global(html[data-corners="square"]) .overview-chart-wrap :deep(.market-breadth-info-popover),
:global(html[data-corners="square"]) .overview-chart-wrap :deep(.market-breadth-notice),
:global(html[data-corners="square"]) .overview-chart-wrap :deep(.market-breadth-controls),
:global(html[data-corners="square"]) .overview-chart-wrap :deep(.market-breadth-toggle),
:global(html[data-corners="square"]) .overview-chart-wrap :deep(.market-breadth-desktop-tooltip),
:global(html[data-corners="square"]) .overview-chart-wrap :deep(.market-breadth-compact-tooltip) {
  border-radius: 0 !important;
}

:global(html[data-corners="square"]) .overview-chart-wrap :deep(.market-breadth-toggle)::before {
  border-radius: 0 !important;
}

</style>
