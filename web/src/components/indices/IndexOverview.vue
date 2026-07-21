<script setup>
import { computed } from 'vue'
import { formatNumber, indicesSwitchSession, marketItems, toneClass } from '../../utils/marketDisplay.js'
import IndexSparkline from './IndexSparkline.vue'

const props = defineProps({
  payload: { type: Object, required: true },
  priority: { type: String, required: true },
})

const groups = computed(() => {
  const aShare = marketItems(props.payload, 'a_index', 'domestic')
  const us = marketItems(props.payload, 'us_index', 'global')
  const primary = props.priority === 'a_share'
    ? [['A股指数', aShare], ['美股指数', us]]
    : [['美股指数', us], ['A股指数', aShare]]
  const supporting = indicesSwitchSession(aShare) === 'us_open'
    ? [
        ['A股期货', marketItems(props.payload, 'a_futures')],
        ['大宗商品', marketItems(props.payload, 'commodity', 'commodity')],
      ]
    : [
        ['A股期货', marketItems(props.payload, 'a_futures')],
        ['美股期货', marketItems(props.payload, 'us_futures')],
        ['大宗商品', marketItems(props.payload, 'commodity', 'commodity')],
      ]
  return [...primary, ...supporting]
    .filter(([, items]) => items.length)
    .map(([title, items]) => ({ title, items }))
})
</script>

<template>
  <template v-if="groups.length">
    <div v-for="group in groups" :key="group.title" style="margin-bottom:18px">
      <h3 style="margin:0 0 10px;color:var(--text);font-size:15px">{{ group.title }}</h3>
      <section class="market-strip">
        <article
          v-for="item in group.items"
          :key="item.key || item.code || item.name"
          class="index-card"
          :class="toneClass(item.change_pct, 'index')"
        >
          <div class="index-name">{{ item.name }}</div>
          <div class="index-price">{{ formatNumber(item.price) }}</div>
          <div
            v-if="item.change_pct != null"
            class="index-change"
            :class="toneClass(item.change_pct, 'index')"
          >{{ Number(item.change_pct) > 0 ? '+' : '' }}{{ formatNumber(item.change_pct) }}%</div>
          <IndexSparkline :item="item" />
          <div class="index-time">{{ item.time || '' }}</div>
        </article>
      </section>
    </div>
  </template>
  <div v-else class="empty" style="padding:18px">暂无指数数据</div>
</template>
