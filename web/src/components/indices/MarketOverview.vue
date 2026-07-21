<script setup>
import { computed } from 'vue'
import { formatAmount, formatNumber, numberValue, signedNumber, toneClass } from '../../utils/marketDisplay.js'

const props = defineProps({
  sectors: { type: Object, required: true },
  usSectors: { type: Object, required: true },
  hotStocks: { type: Object, required: true },
  moneyFlow: { type: Object, required: true },
  marketFlow: { type: Object, required: true },
  region: { type: String, required: true },
})

const sectorRows = computed(() => props.sectors.sectors || props.sectors.items || [])
const sectorGains = computed(() => (props.sectors.gain_top || sectorRows.value.slice(0, 10)).slice(0, 10))
const sectorLosses = computed(() => (props.sectors.loss_top || []).slice(0, 10))
const hasSectorMoves = computed(() => sectorGains.value.length || sectorLosses.value.length)

const hasHotRankings = computed(() => (
  props.hotStocks.amount_top?.length
  || props.hotStocks.turnover_top?.length
  || props.hotStocks.volume_top?.length
))
const hotSearchRows = computed(() => (props.hotStocks.items || []).slice(0, 12))
const hasHotStocks = computed(() => hasHotRankings.value || hotSearchRows.value.length)
const hasMoneyFlow = computed(() => props.moneyFlow.inflow?.length && props.moneyFlow.outflow?.length)
const hasMarketFlow = computed(() => {
  if (props.marketFlow.total_inflow_yi == null) return false
  return Boolean(
    Number(props.marketFlow.total_inflow_yi)
    || Number(props.marketFlow.total_outflow_yi)
    || Number(props.marketFlow.net_flow_yi)
  )
})

const usRows = computed(() => (props.usSectors.items || []).map(row => {
  const percentage = numberValue(row.change_pct)
  return {
    ...row,
    name: row.label || row.name || row.symbol || '',
    pct: percentage,
  }
}).filter(row => row.name))
const usGains = computed(() => usRows.value
  .filter(row => row.pct != null && row.pct > 0)
  .sort((left, right) => right.pct - left.pct)
  .slice(0, 10))
const usLosses = computed(() => usRows.value
  .filter(row => row.pct != null && row.pct < 0)
  .sort((left, right) => left.pct - right.pct)
  .slice(0, 10))

const moduleCount = computed(() => props.region === 'us'
  ? usRows.value.length
  : [hasSectorMoves.value, hasHotStocks.value, hasMarketFlow.value, hasMoneyFlow.value].filter(Boolean).length)

function flowCardStyle(inflow) {
  return {
    position: 'relative',
    background: inflow ? 'rgba(127,29,29,.28)' : 'rgba(6,78,59,.28)',
    borderColor: inflow ? 'rgba(248,113,113,.22)' : 'rgba(52,211,153,.22)',
  }
}

function hotSubLabel(item, mode) {
  if (mode === 'turnover') return `换手 ${formatNumber(item.turnover)}%`
  if (mode === 'volume') return `量 ${formatNumber((Number(item.volume_lot) || 0) / 10000, 1)}万手`
  return `额 ${formatNumber(item.amount_yi)}亿`
}

function usMapping(row) {
  if (Array.isArray(row.a_share_mapping) && row.a_share_mapping.length) {
    return row.a_share_mapping.slice(0, 3).join('、')
  }
  return row.kind === 'theme' ? '主题ETF' : '行业ETF'
}

function usPrice(row) {
  const symbol = row.symbol ? `${row.symbol} · ` : ''
  return `${symbol}${numberValue(row.price) == null ? '--' : formatNumber(row.price)}`
}

function usTitle(row) {
  return `${row.name} ${usPrice(row)} ${signedNumber(row.pct, '%')} ${usMapping(row)}`.trim()
}

defineExpose({ moduleCount })
</script>

<template>
  <template v-if="region === 'us'">
    <div v-if="usRows.length" class="sector-cloud us-sector-cloud">
      <h3>
        板块涨跌幅
        <span v-if="usSectors.generated_at" class="flow-val">更新 {{ usSectors.generated_at }}</span>
      </h3>
      <div class="sector-columns">
        <div class="sector-column">
          <h3>涨幅前十</h3>
          <div v-if="usGains.length" class="sector-grid">
            <div
              v-for="row in usGains"
              :key="row.symbol || row.name"
              class="hot-item us-sector-card"
              :class="toneClass(row.pct)"
              :title="usTitle(row)"
            >
              <div class="sector-name">{{ row.name }}</div>
              <div class="hot-price">{{ usPrice(row) }}</div>
              <div class="sector-pct">{{ signedNumber(row.pct, '%') }}</div>
              <div class="us-sector-map">{{ usMapping(row) }}</div>
            </div>
          </div>
          <div v-else class="empty" style="padding:18px">暂无上涨板块</div>
        </div>
        <div class="sector-column">
          <h3>跌幅前十</h3>
          <div v-if="usLosses.length" class="sector-grid">
            <div
              v-for="row in usLosses"
              :key="row.symbol || row.name"
              class="hot-item us-sector-card"
              :class="toneClass(row.pct)"
              :title="usTitle(row)"
            >
              <div class="sector-name">{{ row.name }}</div>
              <div class="hot-price">{{ usPrice(row) }}</div>
              <div class="sector-pct">{{ signedNumber(row.pct, '%') }}</div>
              <div class="us-sector-map">{{ usMapping(row) }}</div>
            </div>
          </div>
          <div v-else class="empty" style="padding:18px">暂无下跌板块</div>
        </div>
      </div>
    </div>
    <div v-else class="sector-cloud">
      <h3>
        板块涨跌幅
        <span v-if="usSectors.generated_at" class="flow-val">更新 {{ usSectors.generated_at }}</span>
      </h3>
      <div class="empty" style="padding:18px">
        {{ usSectors.error ? `美股板块行情暂不可用：${usSectors.error}` : '美股板块行情加载中...' }}
      </div>
    </div>
  </template>

  <template v-else>
    <div v-if="hasSectorMoves" class="sector-cloud">
      <h3>
        板块涨跌幅
        <span v-if="sectors.generated_at" class="flow-val">更新 {{ sectors.generated_at }}</span>
      </h3>
      <div style="display:flex;gap:16px;flex-wrap:wrap">
        <div style="flex:1;min-width:260px">
          <h3>涨幅前十</h3>
          <div class="sector-grid">
            <div v-for="row in sectorGains" :key="row.name" class="sector-item up">
              <div class="sector-name">{{ row.name }}</div>
              <div class="sector-pct">{{ signedNumber(row.pct, '%') }}</div>
            </div>
          </div>
        </div>
        <div style="flex:1;min-width:260px">
          <h3>跌幅前十</h3>
          <div class="sector-grid">
            <div v-for="row in sectorLosses" :key="row.name" class="sector-item down">
              <div class="sector-name">{{ row.name }}</div>
              <div class="sector-pct">{{ signedNumber(row.pct, '%') }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="hasHotStocks" class="sector-cloud">
      <h3>{{ hasHotRankings ? '活跃股票榜' : '热搜股票' }}</h3>
      <div v-if="hasHotRankings" style="display:flex;gap:16px;flex-wrap:wrap">
        <div
          v-for="ranking in [
            { title: '成交额前十', rows: hotStocks.amount_top || hotStocks.items || [], mode: 'amount' },
            { title: '换手率前十', rows: hotStocks.turnover_top || [], mode: 'turnover' },
            { title: '成交量前十', rows: hotStocks.volume_top || [], mode: 'volume' },
          ].filter(item => item.rows.length)"
          :key="ranking.mode"
          style="flex:1;min-width:250px"
        >
          <h3>{{ ranking.title }}</h3>
          <div class="sector-grid">
            <div
              v-for="row in ranking.rows.slice(0, 10)"
              :key="`${ranking.mode}-${row.code}`"
              class="hot-item"
              :class="toneClass(row.pct)"
            >
              <div class="sector-name">{{ row.code }} {{ row.name || '' }}</div>
              <div class="hot-price">{{ formatNumber(row.price) }}</div>
              <div class="sector-pct">
                {{ signedNumber(row.pct, '%') }}
                <span class="flow-val">{{ hotSubLabel(row, ranking.mode) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div v-else class="sector-grid">
        <div
          v-for="row in hotSearchRows"
          :key="row.code"
          class="hot-item"
          :class="toneClass(row.pct)"
        >
          <div class="sector-name">{{ row.code }} {{ row.name || '' }}</div>
          <div class="hot-price">{{ formatNumber(row.price) }}</div>
          <div class="sector-pct">{{ signedNumber(row.pct, '%') }}</div>
        </div>
      </div>
    </div>

    <div v-if="hasMarketFlow" class="sector-cloud">
      <h3>大盘资金流向</h3>
      <div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:10px">
        <div style="flex:1;min-width:120px;text-align:center;padding:8px 12px;background:var(--panel2);border:1px solid var(--line);border-radius:7px">
          <div style="font-size:12px;color:var(--muted)">总流入</div>
          <div style="font-size:18px;color:var(--red);font-weight:bold">{{ formatAmount(marketFlow.total_inflow) }}</div>
          <div style="font-size:11px;color:var(--muted)">{{ formatNumber(marketFlow.total_inflow_yi, 0) }}亿</div>
        </div>
        <div style="flex:1;min-width:120px;text-align:center;padding:8px 12px;background:var(--panel2);border:1px solid var(--line);border-radius:7px">
          <div style="font-size:12px;color:var(--muted)">总流出</div>
          <div style="font-size:18px;color:var(--green);font-weight:bold">{{ formatAmount(marketFlow.total_outflow) }}</div>
          <div style="font-size:11px;color:var(--muted)">{{ formatNumber(marketFlow.total_outflow_yi, 0) }}亿</div>
        </div>
        <div style="flex:1;min-width:120px;text-align:center;padding:8px 12px;background:var(--panel2);border:1px solid var(--line);border-radius:7px">
          <div style="font-size:12px;color:var(--muted)">净流入</div>
          <div :style="{ fontSize:'18px', color:Number(marketFlow.net_flow_yi) > 0 ? 'var(--red)' : 'var(--green)', fontWeight:'bold' }">{{ Number(marketFlow.net_flow_yi) > 0 ? '+' : '' }}{{ formatAmount(marketFlow.net_flow) }}</div>
          <div style="font-size:11px;color:var(--muted)">{{ signedNumber(marketFlow.net_flow_yi, '亿', 0) }}</div>
        </div>
      </div>
    </div>

    <div v-if="hasMoneyFlow" class="sector-cloud">
      <h3 style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <span>主力资金流向</span>
        <span class="flow-val">1分钟刷新{{ moneyFlow.generated_at ? ` · 更新 ${String(moneyFlow.generated_at).slice(11, 16)}` : '' }}</span>
      </h3>
      <div style="display:flex;gap:16px;flex-wrap:wrap">
        <div
          v-for="group in [
            { title: '主力净流入前十', rows: moneyFlow.inflow || [], inflow: true },
            { title: '主力净流出前十', rows: moneyFlow.outflow || [], inflow: false },
          ]"
          :key="group.title"
          style="flex:1;min-width:260px"
        >
          <h3>{{ group.title }}</h3>
          <div class="sector-grid">
            <div
              v-for="row in group.rows"
              :key="`${group.title}-${row.code || row.name}`"
              class="hot-item"
              :class="group.inflow ? 'up' : 'down'"
              :style="flowCardStyle(group.inflow)"
            >
              <div class="sector-name">{{ row.name }}</div>
              <div class="hot-price">{{ formatNumber(row.price) }}</div>
              <div class="sector-pct">
                {{ signedNumber(row.pct, '%') }}
                <span class="flow-val" :class="group.inflow ? 'flow-in' : 'flow-out'">{{ signedNumber(row.net_flow_yi, '亿') }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="!moduleCount" class="empty" style="padding:18px">
      {{ ['gain_top', 'loss_top', 'sectors', 'items'].some(key => Array.isArray(sectors[key])) ? '暂无行情数据' : '行情加载中...' }}
    </div>
  </template>
</template>
