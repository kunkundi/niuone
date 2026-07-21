<script setup>
import { computed, ref } from 'vue'
import {
  extractTargetPrice,
  parseRatingReport,
  ratingCompanyDetail,
  ratingMetaDetail,
  ratingRecordKey,
  ratingStableRowId,
  ratingTicker,
} from '../../utils/usRatingDisplay.js'
import RatingText from './RatingText.vue'

const props = defineProps({
  record: { type: Object, required: true },
  quotes: { type: Object, default: () => ({}) },
  profiles: { type: Object, default: () => ({}) },
  loadProfile: { type: Function, required: true },
})

const expandedRowId = ref('')
const report = computed(() => parseRatingReport(props.record?.content || ''))
const fallbackLines = computed(() => String(props.record?.content || '').split('\n').slice(0, 30))
const fallbackTruncated = computed(() => String(props.record?.content || '').split('\n').length > 30)
const rows = computed(() => {
  const seen = new Set()
  return (report.value?.items || []).flatMap((item, index) => {
    const ticker = ratingTicker(item)
    if (!ticker || seen.has(ticker)) return []
    seen.add(ticker)
    const company = item.name.split('/').map(value => value.trim()).slice(1).join(' / ')
    const quote = { ...(props.quotes[ticker] || {}), ...(props.profiles[ticker] || {}) }
    const target = extractTargetPrice(item.target || item.action || '')
    const price = Number(quote.price)
    const upside = Number.isFinite(price) && price > 0 && Number.isFinite(target)
      ? (target / price - 1) * 100
      : null
    return [{
      item,
      ticker,
      company,
      quote,
      target,
      price,
      upside,
      id: ratingStableRowId(ratingRecordKey(props.record) || props.record?.time, ticker, index),
      companyDetail: ratingCompanyDetail(ticker, company, quote),
      metaDetail: ratingMetaDetail(item),
    }]
  })
})

function formatUsd(value) {
  return `$${Number(value).toFixed(2)}`
}

function toggleRow(row) {
  const opening = expandedRowId.value !== row.id
  expandedRowId.value = opening ? row.id : ''
  if (opening) props.loadProfile(row.ticker)
}

function handleKey(event, row) {
  if (!['Enter', ' '].includes(event.key)) return
  event.preventDefault()
  toggleRow(row)
}
</script>

<template>
  <div v-if="!report" class="card">
    <template v-for="(line, index) in fallbackLines" :key="index">{{ line }}<br></template>
    <template v-if="fallbackTruncated">...</template>
  </div>
  <article v-else class="card rating-card">
    <div class="rating-table-wrap">
      <div class="rating-table-title">
        <span>股票价格对照表</span>
        <small>{{ record.time || '' }}</small>
      </div>
      <table class="rating-table">
        <thead>
          <tr><th>股票</th><th>当前股价</th><th>目标股价</th><th>目标空间</th></tr>
        </thead>
        <tbody>
          <template v-for="row in rows" :key="row.id">
            <tr
              :id="`rating-row-${row.id}`"
              class="rating-data-row"
              :class="{ expanded: expandedRowId === row.id }"
              title="点击向下展开看多逻辑、机构/分析师和风险点"
              role="button"
              tabindex="0"
              :aria-expanded="expandedRowId === row.id"
              @click="toggleRow(row)"
              @keydown="handleKey($event, row)"
            >
              <td data-label="股票"><span class="ticker">{{ row.ticker }}</span></td>
              <td data-label="当前股价"><span class="price">{{ Number.isFinite(row.price) ? formatUsd(row.price) : '--' }}</span></td>
              <td data-label="目标股价">
                <span v-if="Number.isFinite(row.target)" class="target">{{ formatUsd(row.target) }}</span>
                <RatingText v-else-if="row.item.target" :value="row.item.target" />
                <template v-else>--</template>
                <span v-if="row.item.action" class="rating-action-inline"><RatingText :value="row.item.action.replace(/，.*$/, '')" /></span>
              </td>
              <td data-label="目标空间">
                <span v-if="Number.isFinite(row.upside)" class="upside" :class="row.upside >= 0 ? 'pos' : 'neg'">
                  {{ row.upside >= 0 ? '+' : '' }}{{ row.upside.toFixed(1) }}%
                </span>
                <span v-else class="muted">--</span>
              </td>
            </tr>
            <tr :id="`rating-detail-${row.id}`" class="rating-detail-row" :class="{ open: expandedRowId === row.id }">
              <td class="rating-detail-cell" colspan="4">
                <div class="rating-inline-detail">
                  <div class="rating-inline-grid">
                    <div v-if="row.companyDetail" class="inline-field rating-detail-company">
                      <div class="inline-label">公司详情</div>
                      <div class="inline-value"><RatingText :value="row.companyDetail" /></div>
                    </div>
                    <div v-if="row.metaDetail" class="inline-field rating-detail-meta">
                      <div class="inline-label">评级信息</div>
                      <div class="inline-value"><RatingText :value="row.metaDetail" /></div>
                    </div>
                    <div v-if="row.item.reason" class="inline-field rating-detail-reason">
                      <div class="inline-label">看多逻辑 / 催化剂</div>
                      <div class="inline-value"><RatingText :value="row.item.reason" /></div>
                    </div>
                    <div v-if="row.item.risk" class="inline-field rating-detail-risk">
                      <div class="inline-label">风险点</div>
                      <div class="inline-value"><RatingText :value="row.item.risk" /></div>
                    </div>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </article>
</template>
