<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useDashboardTabs } from '../composables/useDashboardTabs.js'
import { authenticateAdmin } from '../utils/adminSession.js'
import { startVisiblePolling } from '../utils/visiblePolling.js'

const REFRESH_INTERVAL_MS = 60 * 1000
const SORT_FIELDS = new Set(['name', 'sector', 'change_pct', 'net_amount_yuan'])
const TEXT_SORT_FIELDS = new Set(['name', 'sector'])
const initialDate = new URLSearchParams(window.location.search).get('date') || ''
const selectedDate = ref(/^\d{4}-\d{2}-\d{2}$/.test(initialDate) ? initialDate : '')
const dateInput = ref(selectedDate.value)
const payload = ref({ loading: true, loaded: false, available: false, items: [] })
const sort = reactive({ key: 'net_amount_yuan', direction: 'desc' })
const adminAuth = reactive({ open: false, credential: '', error: '', submitting: false })
const adminCredentialInput = ref(null)
const { setCategoryCount } = useDashboardTabs()
let requestController = null
let loadSequence = 0
let stopRefreshPolling = null
let pendingLoadOptions = null

function numberValue(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function formatNumber(value, digits = 2) {
  const number = numberValue(value)
  return number === null ? '--' : Number(number.toFixed(digits)).toLocaleString('en')
}

function formatAmount(value, { signed = false } = {}) {
  if (value === null || value === undefined || value === '') return '--'
  const number = Number(value)
  if (!Number.isFinite(number)) return '--'
  const absolute = Math.abs(number)
  const formatted = absolute >= 100000000
    ? `${(absolute / 100000000).toFixed(2)}亿`
    : absolute >= 10000
      ? `${(absolute / 10000).toFixed(2)}万`
      : `${absolute.toFixed(0)}元`
  if (!signed || number === 0) return formatted
  return `${number > 0 ? '+' : '-'}${formatted}`
}

function formatPct(value) {
  if (value === null || value === undefined || value === '') return '--'
  const number = Number(value)
  return Number.isFinite(number) ? `${number >= 0 ? '+' : ''}${formatNumber(number)}%` : '--'
}

function valueClass(value) {
  if (value === null || value === undefined || value === '') return ''
  const number = Number(value)
  return number > 0 ? 'up' : number < 0 ? 'down' : 'flat'
}

function defaultSortDirection(key) {
  return TEXT_SORT_FIELDS.has(key) ? 'asc' : 'desc'
}

function sortLabel(key, label) {
  const active = sort.key === key
  const direction = active ? sort.direction : defaultSortDirection(key)
  return active
    ? `按${label}排序，当前${direction === 'asc' ? '升序' : '降序'}`
    : `按${label}排序，首次点击为${direction === 'asc' ? '升序' : '降序'}`
}

function sortIndicator(key) {
  if (sort.key !== key) return '↕'
  return sort.direction === 'asc' ? '↑' : '↓'
}

function setSort(key) {
  if (!SORT_FIELDS.has(key)) return
  if (sort.key === key) sort.direction = sort.direction === 'asc' ? 'desc' : 'asc'
  else {
    sort.key = key
    sort.direction = defaultSortDirection(key)
  }
}

function sortValue(item, key) {
  if (TEXT_SORT_FIELDS.has(key)) return String(item?.[key] ?? '').trim()
  const raw = item?.[key]
  if (raw === null || raw === undefined || raw === '') return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

const items = computed(() => {
  const source = Array.isArray(payload.value.items) ? payload.value.items : []
  const key = SORT_FIELDS.has(sort.key) ? sort.key : 'net_amount_yuan'
  const direction = sort.direction === 'asc' ? 1 : -1
  const compareText = (left, right) => String(left).localeCompare(
    String(right),
    'zh-CN',
    { numeric: true, sensitivity: 'base' },
  )
  return source.map((item, index) => ({ item, index })).sort((left, right) => {
    const leftValue = sortValue(left.item, key)
    const rightValue = sortValue(right.item, key)
    const leftMissing = leftValue === null || leftValue === ''
    const rightMissing = rightValue === null || rightValue === ''
    if (leftMissing !== rightMissing) return leftMissing ? 1 : -1
    if (!leftMissing) {
      const primary = TEXT_SORT_FIELDS.has(key)
        ? compareText(leftValue, rightValue)
        : leftValue - rightValue
      if (primary !== 0) return primary * direction
    }
    const nameOrder = compareText(left.item.name || '', right.item.name || '')
    if (nameOrder !== 0) return nameOrder
    const codeOrder = compareText(left.item.code || '', right.item.code || '')
    return codeOrder || left.index - right.index
  }).map(entry => entry.item)
})

function detailsFor(item) {
  return Array.isArray(item?.details) && item.details.length ? item.details : [item]
}

function reasonsFor(item) {
  const reasons = []
  const seen = new Set()
  detailsFor(item).forEach(detail => {
    const reason = String(detail?.reason ?? '').replace(/\s+/g, ' ').trim()
    if (!reason || seen.has(reason)) return
    seen.add(reason)
    reasons.push(reason)
  })
  return reasons
}

function streak(item) {
  const up = Math.max(0, Math.trunc(Number(item?.limit_up_streak) || 0))
  if (up > 0) return { label: up === 1 ? '首板' : `${up}连板`, className: 'limit-up', title: `连续涨停 ${up} 个交易日` }
  const down = Math.max(0, Math.trunc(Number(item?.limit_down_streak) || 0))
  if (down > 0) return { label: down === 1 ? '首跌停' : `${down}连跌停`, className: 'limit-down', title: `连续跌停 ${down} 个交易日` }
  return null
}

function seatRank(record, side) {
  const sideRank = Math.max(0, Math.trunc(Number(record?.[`${side}_rank`]) || 0))
  if (sideRank) return sideRank
  const recordSide = String(record?.side || '').trim().toLowerCase()
  return recordSide === side || recordSide === 'both'
    ? Math.max(0, Math.trunc(Number(record?.rank) || 0))
    : 0
}

function seatCategory(record) {
  if (record?.seat_category === 'institution') return { label: '机构', className: 'institution' }
  if (record?.seat_category === 'quant') return { label: '量化', className: 'quant' }
  if (record?.seat_category === 'hot_money') return { label: '游资', className: 'hot-money' }
  return { label: '营业部', className: 'brokerage' }
}

function seatGroups(item) {
  const groups = []
  const byKey = new Map()
  const records = Array.isArray(item?.seats) ? item.seats : []
  records.forEach(record => {
    const listType = String(record?.list_type || '').trim()
    const reason = String(record?.reason || '').trim()
    const key = `${listType}\u0000${reason}`
    if (!byKey.has(key)) {
      const group = { listType, reason, records: [] }
      byKey.set(key, group)
      groups.push(group)
    }
    byKey.get(key).records.push(record)
  })
  return groups
}

function sideRecords(group, side) {
  return (group?.records || [])
    .filter(record => seatRank(record, side) > 0)
    .sort((left, right) => seatRank(left, side) - seatRank(right, side)
      || String(left?.seat_name || '').localeCompare(String(right?.seat_name || ''), 'zh-CN'))
}

function seatSourceFailed(item) {
  return Boolean(payload.value.seat_error || payload.value.institution_error)
    && !(Array.isArray(item?.seats) && item.seats.length)
}

function errorText(error) {
  if (error === 'iwencai_disabled') return '问财数据源尚未启用。'
  if (error === 'iwencai_not_configured') return '问财 API Key 尚未配置。'
  if (error === 'dashboard_request_failed') return 'Dashboard 暂时无法读取龙虎榜数据。'
  return '龙虎榜数据暂时不可用，请稍后重试。'
}

const refreshTime = computed(() => payload.value.scheduled_refresh_time || '18:00')
const statusText = computed(() => {
  if (payload.value.loading) return '实时回源查询中…'
  if (payload.value.stale) return `当前展示最近成功快照 · 原计划 ${payload.value.requested_date || ''}`
  if (payload.value.snapshot) return `${refreshTime.value} 定时快照`
  return '实时回源数据'
})

function updateUrl() {
  const nextUrl = new URL(window.location.href)
  if (selectedDate.value) nextUrl.searchParams.set('date', selectedDate.value)
  else nextUrl.searchParams.delete('date')
  window.history.replaceState({}, '', `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`)
}

function publishStatus(nextPayload) {
  const count = Number(nextPayload.unique_count ?? (nextPayload.items || []).length)
  setCategoryCount('dragon_tiger', nextPayload.loaded ? ` · ${count}` : '')
  window.dispatchEvent(new CustomEvent('niuone:last-updated', {
    detail: { value: String(nextPayload.generated_at || '').slice(11, 19) || '--' },
  }))
}

async function requestAdminAuthentication(options) {
  pendingLoadOptions = options
  adminAuth.open = true
  adminAuth.error = ''
  adminAuth.credential = ''
  await nextTick()
  adminCredentialInput.value?.focus()
}

function cancelAdminAuthentication() {
  pendingLoadOptions = null
  adminAuth.open = false
  adminAuth.credential = ''
  adminAuth.error = ''
  loadLatest()
}

async function submitAdminAuthentication() {
  if (adminAuth.submitting) return
  adminAuth.submitting = true
  adminAuth.error = ''
  try {
    await authenticateAdmin(adminAuth.credential)
    const retryOptions = pendingLoadOptions || {}
    pendingLoadOptions = null
    adminAuth.open = false
    adminAuth.credential = ''
    await load(retryOptions)
  } catch (error) {
    adminAuth.error = error instanceof Error ? error.message : '管理员凭据错误'
    adminAuth.credential = ''
    await nextTick()
    adminCredentialInput.value?.focus()
  } finally {
    adminAuth.submitting = false
  }
}

async function load({ background = false } = {}) {
  const sequence = ++loadSequence
  requestController?.abort()
  const controller = new AbortController()
  requestController = controller
  if (!background) payload.value = { ...payload.value, loading: true }
  const query = selectedDate.value ? `?date=${encodeURIComponent(selectedDate.value)}` : ''
  try {
    const response = await fetch(`/api/iwencai/dragon-tiger${query}`, {
      signal: controller.signal,
      credentials: 'same-origin',
      cache: 'no-store',
    })
    const responsePayload = await response.json().catch(() => null)
    if (response.status === 403 && responsePayload?.error === 'admin_password_required') {
      if (sequence !== loadSequence) return
      payload.value = { ...payload.value, loading: false }
      await requestAdminAuthentication({ background })
      return
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const nextPayload = { ...responsePayload, loading: false, loaded: true }
    if (sequence !== loadSequence) return
    payload.value = nextPayload
    if (!selectedDate.value) dateInput.value = String(nextPayload.date || '')
    publishStatus(nextPayload)
  } catch (error) {
    if (error?.name === 'AbortError' || sequence !== loadSequence) return
    const nextPayload = {
      loading: false,
      loaded: true,
      available: false,
      items: [],
      error: 'dashboard_request_failed',
    }
    payload.value = nextPayload
    publishStatus(nextPayload)
  } finally {
    if (requestController === controller) requestController = null
  }
}

function loadSelectedDate() {
  const value = String(dateInput.value || '').trim()
  if (value && !/^\d{4}-\d{2}-\d{2}$/.test(value)) return
  selectedDate.value = value
  updateUrl()
  load()
}

function loadLatest() {
  selectedDate.value = ''
  updateUrl()
  load()
}

onMounted(() => {
  load()
  stopRefreshPolling = startVisiblePolling(() => {
    if (!selectedDate.value) load({ background: true })
  }, REFRESH_INTERVAL_MS)
})
onBeforeUnmount(() => {
  loadSequence += 1
  requestController?.abort()
  stopRefreshPolling?.()
  stopRefreshPolling = null
  pendingLoadOptions = null
})
</script>

<template>
  <div v-if="payload.loading && !payload.loaded" class="loading">加载龙虎榜数据…</div>
  <section v-else class="sector-cloud dragon-tiger-panel">
    <div class="dragon-tiger-head">
      <div>
        <h2>每日龙虎榜</h2>
        <div v-if="payload.available" class="dragon-tiger-sub">
          数据日期 {{ payload.date || '--' }} · {{ payload.source || '同花顺问财' }} · 每日北京时间 {{ refreshTime }} 更新
        </div>
        <div v-else class="dragon-tiger-sub">同花顺问财 · 每日北京时间 {{ refreshTime }} 更新</div>
      </div>
      <div class="dragon-tiger-head-actions">
        <div class="dragon-tiger-date-controls">
          <input v-model="dateInput" type="date" aria-label="龙虎榜交易日" :disabled="payload.loading">
          <button type="button" :disabled="payload.loading" @click.stop="loadSelectedDate">
            {{ payload.loading ? '查询中…' : '查看' }}
          </button>
          <button type="button" :disabled="payload.loading" @click.stop="loadLatest">最新</button>
        </div>
        <div
          v-if="payload.available || payload.loading"
          class="dragon-tiger-status"
          :class="{stale: payload.stale && !payload.loading, querying: payload.loading}"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          {{ statusText }}
        </div>
      </div>
    </div>

    <div v-if="!payload.available" class="dragon-tiger-unavailable">
      <b>{{ errorText(payload.error) }}</b>
      <span>启用并保存配置后，定时任务会自动生成每日快照。</span>
      <a href="/admin/settings/iwencai">前往问财配置</a>
    </div>

    <template v-else>
      <div v-if="items.length" class="dragon-tiger-list">
        <div class="dragon-tiger-list-head">
          <button
            v-for="column in [
              {key: 'name', label: '名称'},
              {key: 'sector', label: '板块'},
              {key: 'change_pct', label: '涨幅'},
              {key: 'net_amount_yuan', label: '净买入'},
            ]"
            :key="column.key"
            type="button"
            class="dragon-tiger-sort-btn"
            :class="{active: sort.key === column.key}"
            :aria-label="sortLabel(column.key, column.label)"
            :title="sortLabel(column.key, column.label)"
            @click.stop="setSort(column.key)"
          >
            <span>{{ column.label }}</span>
            <span class="dragon-tiger-sort-indicator" aria-hidden="true">{{ sortIndicator(column.key) }}</span>
          </button>
        </div>

        <details v-for="item in items" :key="`${item.code || ''}-${item.name || ''}`" class="dragon-tiger-item">
          <summary>
            <span class="dragon-tiger-list-name">
              <span>{{ item.name || '--' }}</span>
              <small
                v-if="streak(item)"
                class="dragon-tiger-streak"
                :class="streak(item).className"
                :title="streak(item).title"
              >{{ streak(item).label }}</small>
            </span>
            <span class="dragon-tiger-list-sector" :title="item.sector_path || item.sector || '--'">
              {{ item.sector || '--' }}
            </span>
            <span class="dragon-tiger-list-number" :class="valueClass(item.change_pct)">
              {{ formatPct(item.change_pct) }}
            </span>
            <span class="dragon-tiger-list-number" :class="valueClass(item.net_amount_yuan)">
              {{ formatAmount(item.net_amount_yuan, {signed: true}) }}
            </span>
          </summary>

          <div class="dragon-tiger-detail">
            <div class="dragon-tiger-detail-meta">
              <span><small>股票代码</small><b>{{ item.code || '--' }}</b></span>
              <span><small>最新价</small><b>{{ formatNumber(item.price) }}</b></span>
              <span><small>所属行业</small><b :title="item.sector_path || item.sector || '--'">{{ item.sector_path || item.sector || '--' }}</b></span>
              <span><small>上榜明细</small><b>{{ detailsFor(item).length }} 条</b></span>
            </div>

            <section class="dragon-tiger-reasons" aria-label="上榜理由">
              <div class="dragon-tiger-reasons-head">
                <b>上榜理由</b><span>{{ reasonsFor(item).length ? `${reasonsFor(item).length} 条` : '暂无' }}</span>
              </div>
              <ol v-if="reasonsFor(item).length" class="dragon-tiger-reason-list">
                <li v-for="(reason, index) in reasonsFor(item)" :key="reason">
                  <b>{{ index + 1 }}</b><span>{{ reason }}</span>
                </li>
              </ol>
              <div v-else class="dragon-tiger-reason-empty">暂无上榜理由</div>
            </section>

            <section class="dragon-tiger-funds" aria-label="榜单资金">
              <div class="dragon-tiger-funds-head"><b>榜单资金</b><span>{{ detailsFor(item).length }} 条</span></div>
              <div class="dragon-tiger-detail-records">
                <article v-for="(detail, index) in detailsFor(item)" :key="index" class="dragon-tiger-detail-record">
                  <div class="dragon-tiger-detail-record-head">
                    <b>{{ detail.list_type || '龙虎榜' }}</b><span>{{ detail.list_date || '--' }}</span>
                  </div>
                  <p v-if="detailsFor(item).length > 1">{{ detail.reason || '暂无上榜原因' }}</p>
                  <div class="dragon-tiger-detail-values">
                    <span><small>买入额</small><b>{{ formatAmount(detail.buy_amount_yuan) }}</b></span>
                    <span><small>卖出额</small><b>{{ formatAmount(detail.sell_amount_yuan) }}</b></span>
                    <span><small>净买入</small><b :class="valueClass(detail.net_amount_yuan)">{{ formatAmount(detail.net_amount_yuan, {signed: true}) }}</b></span>
                    <span><small>净买入占比</small><b>{{ formatPct(detail.net_ratio_pct) }}</b></span>
                  </div>
                </article>
              </div>
            </section>

            <section class="dragon-tiger-seats" aria-label="席位明细">
              <div class="dragon-tiger-seats-head">
                <b>席位明细</b><span>{{ (item.seats || []).length ? `${item.seats.length} 条` : '暂无' }}</span>
              </div>
              <div v-if="payload.seat_preserved_from_previous || payload.institution_preserved_from_previous" class="dragon-tiger-seat-notice">
                本次席位明细更新失败，当前显示同一交易日已归档记录。
              </div>
              <div v-if="(item.seats || []).length && payload.seat_data_complete === false" class="dragon-tiger-seat-notice">
                当前快照仅含已保留的部分席位记录。
              </div>
              <div v-if="seatSourceFailed(item)" class="dragon-tiger-seat-empty">
                本次买卖席位明细获取失败；已有归档记录会继续保留。
              </div>
              <div v-else-if="(item.seats || []).length" class="dragon-tiger-seat-groups">
                <section
                  v-for="(group, groupIndex) in seatGroups(item)"
                  :key="`${group.listType}-${group.reason}`"
                  class="dragon-tiger-seat-group"
                >
                  <div v-if="seatGroups(item).length > 1" class="dragon-tiger-seat-group-head">
                    <b>{{ group.listType || `榜单 ${groupIndex + 1}` }}</b>
                  </div>
                  <p v-if="seatGroups(item).length > 1">{{ group.reason || '未标注上榜原因' }}</p>
                  <div class="dragon-tiger-seat-sides">
                    <section v-for="side in ['buy', 'sell']" :key="side" class="dragon-tiger-seat-side" :class="side" :aria-label="side === 'buy' ? '买方前五' : '卖方前五'">
                      <div class="dragon-tiger-seat-side-head">
                        <b>{{ side === 'buy' ? '买方前五' : '卖方前五' }}</b>
                        <span>{{ sideRecords(group, side).length }} 条</span>
                      </div>
                      <div v-if="sideRecords(group, side).length" class="dragon-tiger-seat-records">
                        <article v-for="record in sideRecords(group, side)" :key="`${side}-${seatRank(record, side)}-${record.seat_name}`" class="dragon-tiger-seat-record">
                          <div class="dragon-tiger-seat-record-head">
                            <span class="dragon-tiger-seat-rank" :class="side">
                              {{ seatRank(record, side) ? `${side === 'buy' ? '买' : '卖'}${seatRank(record, side)}` : `${side === 'buy' ? '买' : '卖'}方` }}
                            </span>
                            <b>{{ record.seat_name || '未标注营业部' }}</b>
                            <em class="dragon-tiger-seat-category" :class="seatCategory(record).className">{{ seatCategory(record).label }}</em>
                          </div>
                          <div class="dragon-tiger-seat-values">
                            <span><small>{{ side === 'buy' ? '买入' : '卖出' }}</small><b>{{ formatAmount(side === 'buy' ? record.buy_amount_yuan : record.sell_amount_yuan) }}</b></span>
                            <span><small>{{ side === 'buy' ? '卖出' : '买入' }}</small><b>{{ formatAmount(side === 'buy' ? record.sell_amount_yuan : record.buy_amount_yuan) }}</b></span>
                            <span><small>净额</small><b :class="valueClass(record.net_amount_yuan)">{{ formatAmount(record.net_amount_yuan, {signed: true}) }}</b></span>
                          </div>
                        </article>
                      </div>
                      <div v-else class="dragon-tiger-seat-side-empty">暂无记录</div>
                    </section>
                  </div>
                </section>
              </div>
              <div v-else class="dragon-tiger-seat-empty">
                {{ payload.seat_available == null && !payload.seat_query ? '当前快照暂无完整买卖席位数据。' : '当日未披露买卖前五席位记录。' }}
              </div>
            </section>
          </div>
        </details>
      </div>
      <div v-else class="empty">当日暂无龙虎榜记录</div>
      <div class="dragon-tiger-foot">
        列表按股票去重，净买入优先采用单日榜；点击股票可查看上榜理由、买卖前五机构及营业部席位与榜单明细。最近一次成功查询会保留至下次成功更新。数据仅用于研究和信息展示，不构成投资建议。
      </div>
    </template>
  </section>

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
        aria-labelledby="dragonTigerAdminTitle"
        @submit.prevent="submitAdminAuthentication"
      >
        <h2 id="dragonTigerAdminTitle">查看历史龙虎榜</h2>
        <p>当日及当前保留的最近数据无需密码；更早日期需要管理员密码。</p>
        <div v-if="adminAuth.error" class="dragon-tiger-admin-error">{{ adminAuth.error }}</div>
        <label for="dragonTigerAdminCredential">管理员密码</label>
        <input
          id="dragonTigerAdminCredential"
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
            {{ adminAuth.submitting ? '验证中…' : '验证并查询' }}
          </button>
        </div>
      </form>
    </div>
  </Teleport>
</template>
