<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useNiuOneMainlineData } from '../composables/useNiuOneMainlineData.js'
import { authenticateAdmin } from '../utils/adminSession.js'
import { allowInfoPopoverClick } from '../utils/infoPopover.js'

const {
  state,
  activateNiuOneMainline,
  deactivateNiuOneMainline,
  refreshNiuOneMainline,
} = useNiuOneMainlineData()

const expandedTheme = ref('')
const expandedThemeUpward = ref(false)
const coveragePopoverOpen = ref(false)
const coverageInfoRoot = ref(null)
const coverageInfoTrigger = ref(null)
const adminAuth = reactive({ open: false, credential: '', error: '', submitting: false })
const adminCredentialInput = ref(null)
const payload = computed(() => state.payload || {})
const quoteGeneratedAt = computed(() => String(payload.value.quote_generated_at || payload.value.generated_at || ''))
const calculatedAt = computed(() => String(payload.value.generated_at || ''))
const market = computed(() => payload.value.market || {})
const mainline = computed(() => payload.value.mainline || {})
const themes = computed(() => Array.isArray(payload.value.themes) ? payload.value.themes : [])
const todayThemes = computed(() => Array.isArray(payload.value.today_themes) ? payload.value.today_themes : [])
const eastmoneySignal = computed(() => payload.value.eastmoney_concept_signal || {})
const coveragePct = computed(() => {
  const coverage = Number(payload.value.data_quality?.coverage)
  return Number.isFinite(coverage) ? Math.round(coverage * 1000) / 10 : null
})
const coverageReasons = computed(() => (
  Array.isArray(payload.value.data_quality?.uncovered_reasons)
    ? payload.value.data_quality.uncovered_reasons
    : []
))
const rankings = computed(() => {
  const sources = [
    {
      key: 'today',
      title: '今日排名',
      themes: todayThemes.value,
    },
    {
      key: 'structure',
      title: '结构排名',
      themes: themes.value,
    },
  ]
  const rowCount = Math.max(...sources.map((ranking) => ranking.themes.length), 0)
  return sources.map((ranking) => ({
    ...ranking,
    rows: Array.from({ length: rowCount }, (_, index) => (
      ranking.themes[index] || { placeholder: true, industry: `${ranking.key}-placeholder-${index}` }
    )),
  }))
})

function numeric(value, digits = 1) {
  if (value == null || value === '') return '--'
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(digits) : '--'
}

function signed(value, suffix = '') {
  if (value == null || value === '') return '--'
  const number = Number(value)
  if (!Number.isFinite(number)) return '--'
  return `${number > 0 ? '+' : ''}${number.toFixed(1)}${suffix}`
}

function percent(value) {
  if (value == null || value === '') return '--'
  const number = Number(value)
  if (!Number.isFinite(number)) return '--'
  return `${Math.round(number * 100)}%`
}

function effectiveBreadthTitle(theme) {
  const breadth = numeric(theme.effective_breadth_pct)
  const todayBreadth = numeric(theme.today_breadth_pct)
  const adjustedTodayBreadth = numeric(theme.today_adjusted_breadth_pct)
  const effective = numeric(theme.effective_strong_count)
  const attributed = numeric(theme.attributed_member_count)
  const members = Number(theme.member_count)
  const sample = Number.isFinite(members) && members > 0 ? `${members}只` : '待补充'
  return `结构有效广度 ${breadth}% · 等效强势股 ${effective}只 / 归因样本 ${attributed}（原始${sample}）；今日归因调整广度 ${adjustedTodayBreadth}%（原始 ${todayBreadth}%）`
}

function structuralLeaderStock(theme) {
  if (theme?.leader_stock && typeof theme.leader_stock === 'object') return theme.leader_stock
  return Array.isArray(theme?.strong_stocks) ? theme.strong_stocks[0] || null : null
}

function leaderStock(theme, rankingKey) {
  const structural = structuralLeaderStock(theme)
  if (rankingKey !== 'today' && structural) return structural
  if (theme?.today_leader_stock && typeof theme.today_leader_stock === 'object') return theme.today_leader_stock
  return Array.isArray(theme?.today_leaders) ? theme.today_leaders[0] || structural : structural
}

function leaderBadge(theme, rankingKey) {
  return rankingKey === 'today' || !structuralLeaderStock(theme) ? '领涨' : '龙头'
}

function themeStocks(theme, rankingKey) {
  const strongStocks = Array.isArray(theme?.strong_stocks) ? theme.strong_stocks : []
  if (rankingKey !== 'today' && strongStocks.length) return strongStocks
  return Array.isArray(theme?.today_leaders) ? theme.today_leaders : strongStocks
}

function displayedScore(theme, rankingKey) {
  return rankingKey === 'today' ? theme.today_strength_score : theme.score
}

function scoreDetail(theme, rankingKey) {
  return rankingKey === 'today'
    ? `结构 ${numeric(theme.score)}`
    : `今日 ${numeric(theme.today_strength_score)}`
}

function displayedCount(theme, rankingKey) {
  return rankingKey === 'today'
    ? theme.today_attributed_up_count ?? theme.today_up_count
    : theme.attributed_strong_stock_count ?? theme.strong_stock_count
}

function displayedBreadth(theme, rankingKey) {
  return rankingKey === 'today'
    ? theme.today_adjusted_breadth_pct ?? theme.today_breadth_pct
    : theme.effective_breadth_pct
}

function eastmoneyQuoteCount(theme) {
  const signal = theme?.eastmoney || {}
  return ['up_count', 'down_count', 'flat_count']
    .reduce((total, key) => total + (Number(signal[key]) || 0), 0)
}

function eastmoneyLeaderDetail(theme) {
  const signal = theme?.eastmoney || {}
  const leader = signal.leader || {}
  const quoteCount = eastmoneyQuoteCount(theme)
  const breadth = quoteCount > 0 ? `上涨 ${signal.up_count || 0}/${quoteCount}` : '广度待补充'
  if (!leader.name && !leader.code) return breadth
  return `${breadth} · 领涨 ${leader.name || leader.code} ${signed(leader.change_pct, '%')}`
}

function eastmoneyMissingDetail() {
  if (eastmoneySignal.value.available) return `涨幅榜前${eastmoneySignal.value.covered_count || 100}未匹配`
  if (eastmoneySignal.value.status === 'not_collected') return '等待下一次题材快照采集'
  return '即时概念榜暂不可用'
}

function stockChangeTone(value) {
  const number = Number(value)
  if (!Number.isFinite(number) || number === 0) return 'flat'
  return number > 0 ? 'up' : 'down'
}

function expandedThemeKey(theme, rankingKey) {
  return `${rankingKey}:${String(theme?.industry || '')}`
}

function themeStockPanelId(rankingKey, index) {
  return `${rankingKey}ThemeStocks${index}`
}

async function toggleThemeStocks(theme, rankingKey, event) {
  const key = expandedThemeKey(theme, rankingKey)
  if (expandedTheme.value === key) {
    expandedTheme.value = ''
    expandedThemeUpward.value = false
    return
  }
  const trigger = event?.currentTarget
  expandedThemeUpward.value = false
  expandedTheme.value = key
  await nextTick()
  const panel = trigger?.nextElementSibling
  if (!(panel instanceof HTMLElement)) return
  const triggerRect = trigger.getBoundingClientRect()
  const panelHeight = panel.getBoundingClientRect().height
  const viewport = window.visualViewport
  const viewportTop = viewport?.offsetTop || 0
  const viewportBottom = viewportTop + (viewport?.height || window.innerHeight)
  const spaceBelow = viewportBottom - triggerRect.bottom
  const spaceAbove = triggerRect.top - viewportTop
  expandedThemeUpward.value = spaceBelow < panelHeight + 8 && spaceAbove > spaceBelow
}

function handleThemeStocksPointerDown(event) {
  if (!expandedTheme.value) return
  const target = event.target
  if (target instanceof Element && target.closest('.theme-stock-list')) return
  expandedTheme.value = ''
  expandedThemeUpward.value = false
}

function toggleCoveragePopover(event) {
  if (!allowInfoPopoverClick(event)) {
    coveragePopoverOpen.value = false
    return
  }
  coveragePopoverOpen.value = !coveragePopoverOpen.value
}

function closeCoveragePopover({ restoreFocus = false } = {}) {
  if (!coveragePopoverOpen.value) return
  coveragePopoverOpen.value = false
  if (restoreFocus) nextTick(() => coverageInfoTrigger.value?.focus({ preventScroll: true }))
}

function handleCoveragePointerDown(event) {
  if (!coveragePopoverOpen.value || coverageInfoRoot.value?.contains(event.target)) return
  closeCoveragePopover()
}

function handleCoverageKeydown(event) {
  if (event.key !== 'Escape' || !coveragePopoverOpen.value) return
  event.preventDefault()
  closeCoveragePopover({ restoreFocus: true })
}

async function requestAdminAuthentication() {
  adminAuth.open = true
  adminAuth.error = ''
  adminAuth.credential = ''
  await nextTick()
  adminCredentialInput.value?.focus()
}

function cancelAdminAuthentication() {
  adminAuth.open = false
  adminAuth.credential = ''
  adminAuth.error = ''
}

async function refreshData() {
  const result = await refreshNiuOneMainline()
  if (result === 'admin_password_required') await requestAdminAuthentication()
}

async function submitAdminAuthentication() {
  if (adminAuth.submitting) return
  adminAuth.submitting = true
  adminAuth.error = ''
  try {
    await authenticateAdmin(adminAuth.credential)
    adminAuth.open = false
    adminAuth.credential = ''
    await refreshData()
  } catch (error) {
    adminAuth.error = error instanceof Error ? error.message : '管理员凭据错误'
    adminAuth.credential = ''
    await nextTick()
    adminCredentialInput.value?.focus()
  } finally {
    adminAuth.submitting = false
  }
}

function marketLabel(value) {
  return {
    offensive: '进攻', rotation: '轮动', recovery: '修复',
    balanced: '均衡', cautious: '谨慎', defensive: '防守',
  }[value] || value || '待评估'
}

function stateLabel(theme, rankingKey) {
  if (rankingKey === 'today') return `归因广度 ${numeric(theme.today_adjusted_breadth_pct ?? theme.today_breadth_pct)}%`
  if (theme.mainline_confirmed) return theme.niuone_lifecycle_label
    ? `${theme.niuone_lifecycle_label} · 已确认`
    : '已确认主线'
  if (theme.intraday_state === 'intraday_mainline' || theme.raw_state === 'intraday_mainline') return '日内观察'
  if (theme.niuone_lifecycle_label) return theme.niuone_lifecycle_label
  return {
    emerging: '酝酿中', diverging: '分歧', fading: '退潮', none: '未成线',
  }[theme.state] || theme.state || '未成线'
}

function themeTone(theme, rankingKey) {
  if (rankingKey === 'today') return 'intraday'
  if (theme.mainline_confirmed) return 'confirmed'
  if (theme.intraday_state === 'intraday_mainline' || theme.raw_state === 'intraday_mainline') return 'intraday'
  if (theme.state === 'emerging') return 'emerging'
  return 'neutral'
}

onMounted(() => {
  activateNiuOneMainline()
  document.addEventListener('pointerdown', handleThemeStocksPointerDown)
  document.addEventListener('pointerdown', handleCoveragePointerDown)
  document.addEventListener('keydown', handleCoverageKeydown)
})
onBeforeUnmount(() => {
  deactivateNiuOneMainline()
  document.removeEventListener('pointerdown', handleThemeStocksPointerDown)
  document.removeEventListener('pointerdown', handleCoveragePointerDown)
  document.removeEventListener('keydown', handleCoverageKeydown)
})
</script>

<template>
  <div class="mainline-page">
    <section class="mainline-hero">
      <div class="mainline-overview">
        <div class="mainline-heading">
          <div>
            <h2>题材强度雷达</h2>
            <p>用每 30 秒全市场最新报价聚类追踪题材延续性；覆盖范围独立于设置中的选股范围，仅用于题材研究。</p>
          </div>
          <div class="mainline-actions">
            <span class="mainline-time">
              {{ quoteGeneratedAt ? `行情 ${quoteGeneratedAt}` : '尚无行情时间' }}
              <template v-if="calculatedAt && calculatedAt !== quoteGeneratedAt"> · 计算 {{ calculatedAt }}</template>
            </span>
            <button type="button" :disabled="state.loading" @click="refreshData">
              {{ state.loading ? '读取中…' : '刷新数据' }}
            </button>
          </div>
        </div>

        <div v-if="state.error" class="mainline-notice error" role="alert">
          主线数据读取失败：{{ state.error }}
        </div>
        <div v-if="state.loading && !state.loaded" class="mainline-loading">正在读取题材强度快照…</div>
        <div v-else-if="!payload.available" class="mainline-empty">
          <strong>尚无题材强度扫描结果</strong>
          <span>运行一次牛牛战法选股后，此页会独立保留主线状态，不受当前所选策略切换影响。</span>
        </div>

        <template v-else>
          <div class="mainline-summary-grid">
            <article class="mainline-summary-card" :class="{ empty: !mainline.primary }">
              <span>跨日确认主线</span>
              <strong>{{ mainline.primary || '暂无' }}</strong>
              <small>{{ mainline.primary ? `强度 ${numeric(mainline.primary_score)} · 多只强势股跨日延续` : '等待多只强势股及核心股跨日延续' }}</small>
            </article>
            <article class="mainline-summary-card" :class="{ empty: !mainline.today_primary }">
              <span>今日领涨题材</span>
              <strong>{{ mainline.today_primary || '暂无' }}</strong>
              <small>{{ mainline.today_primary ? `今日强度 ${numeric(mainline.today_primary_score)} · 上涨广度 ${numeric(mainline.today_primary_breadth_pct)}%` : '当前没有达到今日观察阈值的题材' }}</small>
            </article>
            <article class="mainline-summary-card">
              <span>市场状态</span>
              <strong>{{ marketLabel(market.state) }} · {{ numeric(market.score, 0) }}分</strong>
              <small>涨停 {{ numeric(market.limit_up, 0) }} · 跌停 {{ numeric(market.limit_down, 0) }} · 中位涨幅 {{ signed(market.median_change_pct, '%') }}</small>
            </article>
            <article class="mainline-summary-card coverage-card">
              <div class="coverage-label-row">
                <span>题材有效覆盖</span>
                <div v-if="coverageReasons.length" ref="coverageInfoRoot" class="coverage-info">
                  <button
                    ref="coverageInfoTrigger"
                    class="dashboard-info-trigger"
                    type="button"
                    aria-label="查看未覆盖原因"
                    aria-controls="coverageReasonPopover"
                    :aria-expanded="coveragePopoverOpen"
                    @click="toggleCoveragePopover"
                  >
                    <svg viewBox="0 0 20 20" aria-hidden="true">
                      <circle cx="10" cy="10" r="8"></circle>
                      <path d="M10 9v5M10 6.2v.1"></path>
                    </svg>
                  </button>
                  <div
                    id="coverageReasonPopover"
                    class="coverage-popover"
                    :class="{ open: coveragePopoverOpen }"
                    role="dialog"
                    aria-label="未覆盖原因"
                  >
                    <strong class="coverage-popover-title">未覆盖原因</strong>
                    <div
                      v-for="reason in coverageReasons"
                      :key="reason.key || reason.label"
                      class="coverage-popover-row"
                    >
                      <span>{{ reason.label }}</span>
                      <b>{{ reason.count }}只</b>
                      <small v-if="reason.description">{{ reason.description }}</small>
                    </div>
                    <footer>各项按扫描阶段互斥归类，合计等于未覆盖股票数。</footer>
                  </div>
                </div>
              </div>
              <strong>{{ payload.data_quality?.mapped_stock_count || 0 }} / {{ payload.data_quality?.reference_pool_count || 0 }}</strong>
              <small>{{ coveragePct == null ? '覆盖率待计算' : `覆盖 ${coveragePct}% · 未覆盖 ${payload.data_quality?.unmapped_stock_count || 0} 只` }}</small>
            </article>
          </div>

        </template>
      </div>

      <template v-if="payload.available">
        <section class="mainline-section">
          <div class="theme-rankings">
            <section
              v-for="ranking in rankings"
              :key="ranking.key"
              class="theme-ranking-panel"
              :class="ranking.key"
              :aria-labelledby="`${ranking.key}RankingTitle`"
            >
              <header class="theme-ranking-head">
                <h4 :id="`${ranking.key}RankingTitle`">{{ ranking.title }}</h4>
                <span class="theme-ranking-count">{{ ranking.themes.length }} 个题材</span>
              </header>

              <div v-if="!ranking.rows.length" class="mainline-empty compact">当前暂无{{ ranking.title }}数据。</div>
              <ol v-else class="theme-ranking-list">
                <li
                  v-for="(theme, index) in ranking.rows"
                  :key="theme.industry"
                  class="theme-row"
                  :class="[
                    themeTone(theme, ranking.key),
                    {
                      expanded: expandedTheme === expandedThemeKey(theme, ranking.key),
                      placeholder: theme.placeholder,
                    },
                  ]"
                >
                  <div v-if="theme.placeholder" class="theme-row-placeholder" aria-label="暂无对应排名">—</div>
                  <template v-else>
                    <div class="theme-identity">
                      <span class="theme-rank">{{ String(index + 1).padStart(2, '0') }}</span>
                      <div>
                        <h5>{{ theme.industry }}</h5>
                        <div v-if="ranking.key !== 'today'" class="theme-state-line">
                          <span class="theme-state">{{ stateLabel(theme, ranking.key) }}</span>
                        </div>
                        <div class="theme-related-line">
                          <template v-if="theme.related_themes?.length">
                            <span class="theme-related-label">关联</span>
                            <span class="theme-related">{{ theme.related_themes.join('、') }}</span>
                          </template>
                          <span v-else class="theme-related placeholder" aria-hidden="true">—</span>
                        </div>
                      </div>
                    </div>
                    <div class="theme-score" :title="scoreDetail(theme, ranking.key)">
                      <span>
                        {{ ranking.key === 'today' ? '今日强度' : '结构分' }}
                        <small>{{ scoreDetail(theme, ranking.key) }}</small>
                      </span>
                      <strong>{{ numeric(displayedScore(theme, ranking.key)) }}</strong>
                    </div>
                    <div class="theme-metrics">
                      <span>
                        <small>{{ ranking.key === 'today' ? '等效上涨' : '归因强股' }}</small>
                        <strong>{{ numeric(displayedCount(theme, ranking.key)) }}只</strong>
                      </span>
                      <span :title="effectiveBreadthTitle(theme)" :aria-label="effectiveBreadthTitle(theme)">
                        <small>{{ ranking.key === 'today' ? '归因广度' : '结构广度' }}</small>
                        <strong>{{ numeric(displayedBreadth(theme, ranking.key)) }}%</strong>
                      </span>
                      <span>
                        <small>核心延续</small>
                        <strong>{{ numeric(theme.core_overlap_count, 0) }}只 · {{ percent(theme.core_overlap_ratio) }}</strong>
                      </span>
                    </div>
                    <div class="theme-stock-list">
                      <template v-if="leaderStock(theme, ranking.key)">
                        <button
                          type="button"
                          class="theme-leader-button"
                          :aria-expanded="expandedTheme === expandedThemeKey(theme, ranking.key)"
                          :aria-controls="themeStockPanelId(ranking.key, index)"
                          :aria-label="`${theme.industry}${leaderBadge(theme, ranking.key)}股 ${leaderStock(theme, ranking.key).name || leaderStock(theme, ranking.key).code}，${expandedTheme === expandedThemeKey(theme, ranking.key) ? '收起' : '展开'}代表股列表`"
                          @click="toggleThemeStocks(theme, ranking.key, $event)"
                        >
                          <span class="theme-leader-identity">
                            <span class="theme-leader-badge">{{ leaderBadge(theme, ranking.key) }}</span>
                            <strong>{{ leaderStock(theme, ranking.key).name || leaderStock(theme, ranking.key).code }}</strong>
                            <small>{{ leaderStock(theme, ranking.key).code }}<template v-if="leaderStock(theme, ranking.key).attribution_weight != null"> · 归因 {{ percent(leaderStock(theme, ranking.key).attribution_weight) }}</template></small>
                          </span>
                          <span class="theme-stock-change" :class="stockChangeTone(leaderStock(theme, ranking.key).change_pct)">{{ signed(leaderStock(theme, ranking.key).change_pct, '%') }}</span>
                          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4"></path></svg>
                        </button>
                        <div
                          v-if="expandedTheme === expandedThemeKey(theme, ranking.key)"
                          :id="themeStockPanelId(ranking.key, index)"
                          class="theme-stock-details"
                          :class="{ upward: expandedThemeUpward }"
                          role="region"
                          :aria-label="`${theme.industry}代表股列表`"
                        >
                          <div class="theme-stock-details-head">
                            <span class="theme-stock-detail-head-name">{{ ranking.key === 'today' ? '今日领涨股' : '结构代表股' }}</span>
                            <span class="theme-stock-detail-head-code">代码</span>
                            <span class="theme-stock-detail-head-attribution">归因</span>
                            <span class="theme-stock-detail-head-change">当前涨跌幅</span>
                          </div>
                          <div v-for="(stock, stockIndex) in themeStocks(theme, ranking.key)" :key="stock.code || stock.name" class="theme-stock-detail-row">
                            <span class="theme-stock-detail-name">
                              <b v-if="stockIndex === 0">{{ leaderBadge(theme, ranking.key) }}</b>
                              <strong>{{ stock.name || stock.code }}</strong>
                            </span>
                            <small class="theme-stock-detail-code"><span>代码</span>{{ stock.code || '—' }}</small>
                            <small class="theme-stock-detail-attribution"><span>归因</span>{{ stock.attribution_weight == null ? '—' : percent(stock.attribution_weight) }}</small>
                            <strong class="theme-stock-change" :class="stockChangeTone(stock.change_pct)">{{ signed(stock.change_pct, '%') }}</strong>
                          </div>
                        </div>
                      </template>
                      <span v-else class="theme-placeholder">—</span>
                    </div>
                    <div
                      class="theme-context"
                      :class="ranking.key"
                      :title="ranking.key === 'today' && theme.eastmoney ? eastmoneyLeaderDetail(theme) : ''"
                    >
                      <template v-if="ranking.key === 'today'">
                        <template v-if="theme.eastmoney">
                          <strong>东财 #{{ theme.eastmoney.rank }} · {{ signed(theme.eastmoney.change_pct, '%') }}</strong>
                          <small class="theme-context-detail">{{ eastmoneyLeaderDetail(theme) }}</small>
                          <small v-if="eastmoneySignal.stale" class="risk-text">陈旧快照 · {{ eastmoneySignal.quote_generated_at || eastmoneySignal.captured_at }}</small>
                        </template>
                        <template v-else>
                          <strong>{{ eastmoneySignal.available ? '东财未匹配' : '东财数据不可用' }}</strong>
                          <small>{{ eastmoneyMissingDetail() }}</small>
                        </template>
                      </template>
                      <template v-else>
                        <strong>{{ theme.cross_day_persistent ? '连续出现' : '尚未跨日' }}</strong>
                        <small v-if="theme.single_stock_dominated" class="risk-text">单股主导</small>
                        <small v-else-if="theme.flow_net_yi != null">主力净额 {{ signed(theme.flow_net_yi, '亿') }}</small>
                        <small v-else>资金数据待补充</small>
                      </template>
                    </div>
                  </template>
                </li>
              </ol>
            </section>
          </div>
        </section>
      </template>
    </section>
  </div>

  <Teleport to="body">
    <div
      v-if="adminAuth.open"
      class="dragon-tiger-admin-backdrop"
      role="presentation"
      @click.self="cancelAdminAuthentication"
    >
      <form
        class="dragon-tiger-admin-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mainlineRefreshAdminTitle"
        @submit.prevent="submitAdminAuthentication"
      >
        <h2 id="mainlineRefreshAdminTitle">刷新题材强度数据</h2>
        <p>手动刷新题材强度数据需要管理员身份验证。</p>
        <div v-if="adminAuth.error" class="dragon-tiger-admin-error">{{ adminAuth.error }}</div>
        <label for="mainlineRefreshAdminCredential">管理员密码</label>
        <input
          id="mainlineRefreshAdminCredential"
          ref="adminCredentialInput"
          v-model="adminAuth.credential"
          name="admin_password"
          type="password"
          autocomplete="current-password"
          required
          :disabled="adminAuth.submitting"
        >
        <div class="dragon-tiger-admin-actions">
          <button type="button" :disabled="adminAuth.submitting" @click="cancelAdminAuthentication">取消</button>
          <button type="submit" :disabled="adminAuth.submitting">
            {{ adminAuth.submitting ? '验证中…' : '验证并刷新' }}
          </button>
        </div>
      </form>
    </div>
  </Teleport>
</template>

<style scoped>
.mainline-page { --mainline-line:#bcc5d1; display:grid; width:100%; min-width:0; gap:16px; color:var(--text); }
:global(html[data-theme="dark"] .mainline-page) { --mainline-line:#46566c; }
.mainline-hero { min-width:0; border:1px solid var(--mainline-line); border-radius:12px; background:var(--panel); box-shadow:0 1px 2px rgba(16,24,40,.04); }
:global(html[data-theme="dark"] .mainline-hero),:global(html[data-theme="dark"] .theme-ranking-panel) { box-shadow:none; }
.mainline-overview { min-width:0; }
.mainline-section { min-width:0; margin-top:18px; }
.mainline-hero { padding:20px; }
.mainline-heading { display:flex; align-items:center; justify-content:space-between; gap:14px; }
.mainline-heading { align-items:flex-start; }
.mainline-heading h2 { margin:0 0 5px; font-size:26px; line-height:1.2; }
.mainline-heading p { margin:0; color:var(--muted); font-size:13px; line-height:1.65; }
.mainline-actions { display:flex; align-items:center; gap:10px; flex-shrink:0; }
.mainline-time { color:var(--muted); font-size:12px; white-space:nowrap; }
.mainline-actions button { border:1px solid var(--mainline-line); border-radius:10px; background:var(--panel2); color:var(--text); font:inherit; cursor:pointer; }
.mainline-actions button { min-height:36px; padding:0 13px; font-size:13px; font-weight:750; }
.mainline-actions button:hover { border-color:var(--accent-border); background:var(--accent-soft); }
.mainline-actions button:disabled { cursor:wait; opacity:.65; }
.mainline-summary-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin-top:18px; }
.mainline-summary-card { min-width:0; padding:15px; border:1px solid var(--mainline-line); border-radius:14px; background:var(--panel2); }
.mainline-summary-card > span { display:block; color:var(--muted); font-size:12px; }
.mainline-summary-card strong { display:block; overflow:hidden; margin:7px 0 4px; font-size:18px; line-height:1.25; text-overflow:ellipsis; white-space:nowrap; }
.mainline-summary-card small { display:block; color:var(--muted); font-size:11px; line-height:1.45; }
.mainline-summary-card.empty strong { color:var(--muted); }
.coverage-card { position:relative; }
.coverage-label-row { display:flex; align-items:center; gap:7px; color:var(--muted); font-size:12px; }
.coverage-info { display:inline-flex; align-items:center; }
.coverage-info > button { display:grid; width:20px; height:20px; place-items:center; padding:0; border:0; border-radius:999px; background:transparent; cursor:pointer; }
.coverage-info svg { width:19px; height:19px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; }
.coverage-popover { position:absolute; z-index:20; top:35px; right:10px; left:10px; display:none; padding:12px; border:1px solid var(--accent-border); border-radius:12px; background:var(--panel); color:var(--text); box-shadow:0 16px 34px rgba(15,23,42,.2); }
.coverage-popover.open { display:block; }
@media (hover:hover) and (pointer:fine) {
  .coverage-info:hover .coverage-popover { display:block; }
}
.coverage-popover-title { display:block; margin-bottom:8px; color:var(--text); font-size:12px; }
.coverage-popover-row { display:grid; grid-template-columns:1fr auto; gap:2px 10px; padding:7px 0; border-top:1px solid var(--mainline-line); }
.coverage-popover-row > span { color:var(--text); font-size:11px; }
.coverage-popover-row > b { color:var(--accent-text); font-size:11px; font-variant-numeric:tabular-nums; }
.coverage-popover-row > small { grid-column:1 / -1; color:var(--muted); font-size:9px; line-height:1.45; }
.coverage-popover footer { margin-top:6px; color:var(--muted); font-size:9px; line-height:1.45; }
.mainline-notice { display:flex; align-items:flex-start; gap:10px; margin-top:12px; padding:11px 13px; border:1px solid var(--accent-border); border-radius:12px; background:var(--accent-soft); color:var(--accent-text); font-size:12px; line-height:1.55; }
.mainline-notice.error { border-color:var(--red-border); background:var(--red-soft); color:var(--red-text); }
.mainline-loading,.mainline-empty { display:flex; min-height:150px; align-items:center; justify-content:center; flex-direction:column; gap:7px; color:var(--muted); text-align:center; }
.mainline-empty strong { color:var(--text); font-size:17px; }
.mainline-empty.compact { min-height:80px; }
.theme-rankings { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-items:stretch; gap:12px; }
.theme-ranking-panel { min-width:0; height:100%; overflow:visible; border:1px solid var(--mainline-line); border-radius:12px; background:var(--panel); background-clip:padding-box; box-shadow:0 1px 2px rgba(16,24,40,.04); container-type:inline-size; }
/* This nested header must not inherit the sticky site-header stacking layer. */
.theme-ranking-head { position:static; top:auto; z-index:auto; display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 12px; border-bottom:1px solid var(--mainline-line); border-radius:11px 11px 0 0; }
.theme-ranking-head h4 { margin:0; font-size:15px; line-height:1.2; }
.theme-ranking-count { flex-shrink:0; color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; white-space:nowrap; }
.theme-ranking-list { margin:0; padding:0; list-style:none; }
.theme-row { display:grid; height:80px; box-sizing:border-box; grid-template-columns:minmax(118px,1.15fr) 96px minmax(150px,1fr); grid-template-areas:"identity score stocks" "metrics metrics context"; align-items:center; gap:5px 14px; min-width:0; padding:8px 12px; border-bottom:1px solid color-mix(in srgb,var(--mainline-line) 72%,transparent); background:transparent; transition:background-color .16s ease; }
.theme-row:last-child { border-bottom:0; border-radius:0 0 11px 11px; }
.theme-row.expanded { position:relative; z-index:4; }
.theme-row:hover { background:var(--panel2); }
.theme-row.placeholder { grid-template-areas:none; }
.theme-row-placeholder { grid-column:1 / -1; color:color-mix(in srgb,var(--muted) 58%,transparent); font-size:12px; text-align:center; }
.theme-identity { display:flex; grid-area:identity; min-width:0; align-items:center; gap:8px; }
.theme-identity > div { width:100%; min-width:0; }
.theme-rank { width:22px; flex:0 0 22px; color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; }
.theme-identity h5 { overflow:hidden; margin:0 0 3px; font-size:14px; line-height:1.15; text-overflow:ellipsis; white-space:nowrap; }
.theme-state-line { display:flex; min-width:0; align-items:flex-start; }
.theme-state { display:flex; min-width:0; align-items:flex-start; gap:5px; color:var(--muted); font-size:10px; line-height:1.3; overflow-wrap:anywhere; }
.theme-related-line { display:grid; min-width:0; grid-template-columns:auto minmax(0,1fr); align-items:start; gap:4px; margin-top:2px; color:var(--muted); font-size:9px; line-height:1.3; }
.theme-related-label { white-space:nowrap; }
.theme-related { min-width:0; overflow-wrap:anywhere; }
.theme-related.placeholder { grid-column:1 / -1; }
.theme-related.placeholder,.theme-placeholder { color:color-mix(in srgb,var(--muted) 62%,transparent); }
.theme-state::before { content:""; width:6px; height:6px; flex:0 0 6px; border-radius:50%; background:var(--muted); opacity:.7; }
.theme-row.confirmed .theme-state { color:var(--red-text); }
.theme-row.confirmed .theme-state::before { background:var(--red); opacity:1; }
.theme-row.intraday .theme-state { color:var(--yellow-text); }
.theme-row.intraday .theme-state::before { background:var(--yellow); opacity:1; }
.theme-row.emerging .theme-state { color:var(--accent-text); }
.theme-row.emerging .theme-state::before { background:var(--accent); opacity:1; }
.theme-score,.theme-context { min-width:0; }
.theme-score { display:flex; grid-area:score; min-width:0; align-items:flex-end; justify-content:center; flex-direction:column; gap:2px; text-align:right; }
.theme-score > span { display:flex; overflow:hidden; max-width:100%; align-items:baseline; justify-content:flex-end; gap:4px; color:var(--muted); font-size:9px; line-height:1.2; text-overflow:ellipsis; white-space:nowrap; }
.theme-score strong { font-size:17px; line-height:1; font-variant-numeric:tabular-nums; }
.theme-score small,.theme-context small { display:block; overflow:hidden; margin:0; color:var(--muted); font-size:9px; line-height:1.2; text-overflow:ellipsis; white-space:nowrap; }
.theme-metrics { display:grid; grid-area:metrics; grid-template-columns:repeat(3,minmax(0,1fr)); column-gap:12px; margin-left:30px; }
.theme-metrics > span { display:flex; min-width:0; align-items:baseline; gap:4px; }
.theme-metrics > span + span { padding-left:0; border-left:0; }
.theme-metrics small { overflow:hidden; color:var(--muted); font-size:9px; line-height:1.3; text-overflow:ellipsis; white-space:nowrap; }
.theme-metrics strong { overflow:hidden; color:var(--text); font-size:11px; line-height:1.3; font-variant-numeric:tabular-nums; text-overflow:ellipsis; white-space:nowrap; }
.theme-stock-list { position:relative; grid-area:stocks; overflow:visible; min-width:0; color:var(--text); font-size:11px; line-height:1.55; }
.theme-leader-button { display:grid; width:100%; min-width:0; grid-template-columns:minmax(0,1fr) auto 12px; align-items:center; gap:6px; padding:3px 4px; border:0; border-radius:6px; background:transparent; color:var(--text); font:inherit; text-align:left; cursor:pointer; }
.theme-leader-button:hover { background:var(--panel2); }
.theme-leader-button:focus-visible { border-color:var(--accent-border); outline:2px solid var(--accent-border); outline-offset:1px; }
.theme-leader-identity { display:flex; min-width:0; align-items:baseline; gap:5px; overflow:hidden; }
.theme-leader-identity strong { overflow:hidden; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
.theme-leader-identity small { display:none; flex-shrink:0; color:var(--muted); font-size:9px; font-variant-numeric:tabular-nums; }
.theme-leader-badge,.theme-stock-detail-name b { flex-shrink:0; color:var(--accent-text); font-size:9px; font-weight:750; }
.theme-stock-change { color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; white-space:nowrap; }
.theme-stock-change.up { color:var(--red-text); }
.theme-stock-change.down { color:var(--green-text); }
.theme-leader-button svg { width:12px; height:12px; fill:none; stroke:currentColor; stroke-width:1.6; transition:transform .15s ease; }
.theme-leader-button[aria-expanded="true"] svg { transform:rotate(180deg); }
.theme-stock-details { position:absolute; z-index:10; top:calc(100% + 3px); right:0; left:auto; width:min(520px,calc(100cqw - 24px)); max-width:calc(100vw - 44px); max-height:min(360px,60vh); overflow-y:auto; padding:8px; border:1px solid var(--mainline-line); border-radius:10px; background:var(--panel); box-shadow:0 12px 28px rgba(15,23,42,.18); }
.theme-stock-details.upward { top:auto; bottom:calc(100% + 3px); }
.theme-stock-details-head,.theme-stock-detail-row { display:grid; grid-template-columns:minmax(150px,1fr) 76px 58px 82px; align-items:center; gap:10px; }
.theme-stock-details-head { padding:3px 10px 8px; color:var(--muted); font-size:9px; }
.theme-stock-detail-head-change { text-align:right; }
.theme-stock-detail-row { padding:10px; border-radius:7px; background:var(--panel2); }
.theme-stock-detail-row + .theme-stock-detail-row { margin-top:5px; }
.theme-stock-detail-name { display:flex; min-width:0; align-items:baseline; gap:5px; }
.theme-stock-detail-name strong { min-width:0; font-size:11px; line-height:1.35; overflow-wrap:anywhere; }
.theme-stock-detail-code,.theme-stock-detail-attribution { color:var(--muted); font-size:10px; font-variant-numeric:tabular-nums; white-space:nowrap; }
.theme-stock-detail-code > span,.theme-stock-detail-attribution > span { display:none; }
.theme-stock-detail-row > .theme-stock-change { justify-self:end; font-size:12px; font-weight:750; }
.theme-context { display:flex; overflow:hidden; grid-area:context; min-width:0; align-items:baseline; justify-content:flex-end; gap:6px; text-align:right; }
.theme-context strong,.theme-context small { display:block; overflow:hidden; min-width:0; text-overflow:ellipsis; white-space:nowrap; }
.theme-context strong { flex:0 0 auto; font-size:10px; font-weight:650; }
.theme-context small { text-align:right; }
.theme-context.today .theme-context-detail { display:none; }
.theme-context .risk-text { color:var(--red-text); }
@container (min-width:630px) {
  .theme-row { grid-template-columns:minmax(125px,1.05fr) 102px minmax(180px,1.35fr) minmax(150px,1.2fr); grid-template-areas:"identity score metrics stocks" "identity score metrics context"; column-gap:20px; }
  .theme-identity { align-self:center; }
  .theme-score { padding-left:0; border-left:0; }
  .theme-metrics { height:100%; align-items:stretch; column-gap:18px; margin-left:0; padding:0; border:0; }
  .theme-metrics > span { align-items:flex-start; justify-content:center; flex-direction:column; gap:2px; }
  .theme-metrics > span + span { padding-left:0; border-left:0; }
  .theme-metrics small { font-size:9px; }
  .theme-metrics strong { font-size:12px; }
  .theme-stock-list { align-self:end; }
  .theme-context { align-self:start; }
}
@media (max-width:1450px) {
  .mainline-summary-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
}
@media (max-width:840px) {
  .mainline-page { gap:10px; }
  .mainline-hero { padding:13px; border-radius:10px; box-shadow:0 1px 2px rgba(16,24,40,.04); }
  .mainline-heading { align-items:stretch; flex-direction:column; }
  .mainline-heading { gap:12px; }
  .mainline-heading h2 { margin-bottom:4px; font-size:22px; }
  .mainline-heading p { font-size:12px; line-height:1.55; }
  .mainline-actions { justify-content:space-between; }
  .mainline-actions button { min-width:0; min-height:34px; padding:0 11px; }
  .mainline-summary-grid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin-top:14px; }
  .mainline-summary-card { min-height:94px; padding:11px; border-radius:11px; }
  .mainline-summary-card > span,.coverage-label-row { font-size:10px; }
  .mainline-summary-card strong { display:-webkit-box; overflow:hidden; margin:7px 0 3px; font-size:16px; line-height:1.25; text-overflow:clip; white-space:normal; -webkit-box-orient:vertical; -webkit-line-clamp:2; }
  .mainline-summary-card small { font-size:9px; line-height:1.4; }
  .coverage-label-row { gap:4px; }
  .coverage-info > button { width:18px; height:18px; min-width:0; }
  .coverage-info svg { width:17px; height:17px; }
  .coverage-popover { top:31px; right:0; left:auto; width:calc(200% + 8px); max-width:calc(100vw - 44px); }
  .mainline-notice { gap:4px; margin-top:10px; padding:9px 10px; font-size:11px; }
  .mainline-section { margin-top:14px; }
  .theme-rankings { grid-template-columns:minmax(0,1fr); gap:10px; }
  .theme-ranking-panel { height:auto; border-radius:10px; }
  .theme-ranking-head { padding:9px 11px; }
  .theme-ranking-head { border-radius:9px 9px 0 0; }
  .theme-row { height:auto; min-height:80px; padding:8px 10px; }
  .theme-row:last-child { border-radius:0 0 9px 9px; }
  .theme-rank { width:20px; flex-basis:20px; }
  .theme-identity h5 { font-size:14px; }
  .theme-metrics { margin-left:30px; }
  .theme-leader-button:hover { background:var(--panel2); }
  .theme-leader-identity strong { font-size:11px; }
  .theme-stock-details-head { padding:3px 10px 8px; }
  .theme-stock-detail-row { padding:10px; }
}
@media (max-width:560px) {
  .theme-row { grid-template-columns:minmax(0,1.15fr) minmax(118px,.85fr); grid-template-areas:"identity score" "metrics metrics" "stocks context"; gap:5px 8px; padding:8px 10px; }
  .theme-metrics { margin-left:30px; }
  .theme-metrics { column-gap:6px; }
  .theme-metrics > span { gap:2px; }
  .theme-stock-list { margin-left:30px; }
  .theme-stock-details { right:auto; left:0; width:100%; max-width:100%; max-height:290px; padding:6px; }
  .theme-stock-details-head,.theme-stock-detail-row { grid-template-columns:minmax(0,1fr) auto; gap:5px; }
  .theme-stock-details-head { height:24px; grid-template-areas:"name change"; padding:2px 8px 6px; }
  .theme-stock-detail-head-name { grid-area:name; }
  .theme-stock-detail-head-change { grid-area:change; }
  .theme-stock-detail-head-code,.theme-stock-detail-head-attribution { display:none; }
  .theme-stock-detail-row { height:48px; grid-template-areas:"name change" "code attribution"; row-gap:5px; padding:7px 8px; }
  .theme-stock-detail-row + .theme-stock-detail-row { margin-top:3px; }
  .theme-stock-detail-name { grid-area:name; overflow:hidden; white-space:nowrap; }
  .theme-stock-detail-name strong { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; overflow-wrap:normal; }
  .theme-stock-detail-code { grid-area:code; }
  .theme-stock-detail-attribution { grid-area:attribution; justify-self:end; }
  .theme-stock-detail-row > .theme-stock-change { grid-area:change; }
  .theme-stock-detail-code,.theme-stock-detail-attribution { font-size:9px; }
  .theme-stock-detail-code > span { display:none; }
  .theme-stock-detail-attribution > span { display:inline; margin-right:2px; }
  .theme-context { display:grid; justify-items:end; gap:1px; }
  .theme-context strong,.theme-context small { width:100%; }
}
</style>
