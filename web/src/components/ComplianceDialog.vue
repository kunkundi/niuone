<script setup>
import { onBeforeMount, onBeforeUnmount, ref } from 'vue'

const visible = ref(true)

function closeDialog() {
  if (!visible.value) return
  visible.value = false
  document.body.classList.remove('compliance-dialog-open')
}

function handleKeydown(event) {
  if (event.key === 'Escape') closeDialog()
}

onBeforeMount(() => {
  document.body.classList.add('compliance-dialog-open')
  document.addEventListener('keydown', handleKeydown)
})

onBeforeUnmount(() => {
  document.body.classList.remove('compliance-dialog-open')
  document.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <div
    v-show="visible"
    id="complianceDialog"
    class="compliance-dialog-backdrop"
    role="presentation"
  >
    <section
      class="compliance-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="complianceDialogTitle"
      aria-describedby="complianceDialogContent"
    >
      <div class="compliance-dialog-head">
        <h2 id="complianceDialogTitle">使用与风险提示</h2>
      </div>
      <div id="complianceDialogContent" class="compliance-dialog-body">
        <div class="compliance-row"><span class="compliance-badge">非荐股提示</span><span class="compliance-text">本页面仅用于个人研究、模拟交易和信息展示，不构成证券、期货投资咨询、投资建议、荐股服务或任何买卖依据；不承诺收益，不代客理财，不收取荐股费用。</span></div>
        <div class="compliance-row"><span class="compliance-badge risk">入市风险提示</span><span class="compliance-text">证券、期货等投资存在本金损失风险，市场有涨有跌；请通过正规持牌机构独立判断、自主决策、风险自担。投资有风险，入市需谨慎。</span></div>
      </div>
      <div class="compliance-dialog-actions">
        <button id="complianceDialogClose" type="button" class="compliance-dialog-close" autofocus @click="closeDialog">关闭</button>
      </div>
    </section>
  </div>
</template>
