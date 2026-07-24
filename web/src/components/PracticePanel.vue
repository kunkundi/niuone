<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { usePracticeCandidatesData } from '../composables/usePracticeCandidatesData.js'
import { usePracticeData } from '../composables/usePracticeData.js'
import { authenticateAdmin } from '../utils/adminSession.js'
import PracticeCandidatesPanel from './PracticeCandidatesPanel.vue'
import PracticeAccountOverview from './practice/PracticeAccountOverview.vue'
import PracticeCalendar from './practice/PracticeCalendar.vue'
import PracticeEquityChart from './practice/PracticeEquityChart.vue'
import PracticeOperationLog from './practice/PracticeOperationLog.vue'
import PracticeRule from './practice/PracticeRule.vue'

const calendarOpen = ref(false)
const adminAuth = reactive({ open: false, credential: '', error: '', submitting: false })
const adminCredentialInput = ref(null)
const pendingAdminAction = ref('')
const { state: candidateState } = usePracticeCandidatesData()
const {
  state,
  activatePractice,
  deactivatePractice,
  ensureFullSnapshot,
  resumeTrading,
  triggerManualCycle,
  triggerMarketSummary,
} = usePracticeData()

const strategyMeta = computed(() => candidateState.strategyMeta || {})
const adminDialog = computed(() => pendingAdminAction.value === 'manual-cycle'
  ? {
      title: '手动运行选股与交易策略',
      description: '手动运行选股与交易策略需要管理员身份验证。',
      submitLabel: '验证并运行',
    }
  : {
      title: '生成今日盘面总结',
      description: '生成盘面总结需要管理员身份验证。',
      submitLabel: '验证并生成',
    })

async function requestAdminAuthentication(action) {
  pendingAdminAction.value = action
  adminAuth.open = true
  adminAuth.error = ''
  adminAuth.credential = ''
  await nextTick()
  adminCredentialInput.value?.focus()
}

function cancelAdminAuthentication() {
  pendingAdminAction.value = ''
  adminAuth.open = false
  adminAuth.credential = ''
  adminAuth.error = ''
}

async function generateMarketSummary() {
  const result = await triggerMarketSummary()
  if (result === 'admin_password_required') await requestAdminAuthentication('market-summary')
}

async function runManualCycle() {
  const result = await triggerManualCycle()
  if (result === 'admin_password_required') await requestAdminAuthentication('manual-cycle')
}

async function submitAdminAuthentication() {
  if (adminAuth.submitting) return
  adminAuth.submitting = true
  adminAuth.error = ''
  try {
    await authenticateAdmin(adminAuth.credential)
    const retryAction = pendingAdminAction.value
    pendingAdminAction.value = ''
    adminAuth.open = false
    adminAuth.credential = ''
    if (retryAction === 'manual-cycle') await runManualCycle()
    else if (retryAction === 'market-summary') await generateMarketSummary()
  } catch (error) {
    adminAuth.error = error instanceof Error ? error.message : '管理员凭据错误'
    adminAuth.credential = ''
    await nextTick()
    adminCredentialInput.value?.focus()
  } finally {
    adminAuth.submitting = false
  }
}

onMounted(activatePractice)
onBeforeUnmount(deactivatePractice)
</script>

<template>
  <div v-if="state.loading && !state.loaded" class="loading">模拟账户加载中...</div>
  <div v-else-if="state.error && !state.loaded" class="empty" style="color:#f87171">⚠️ {{ state.error }}</div>
  <template v-else>
    <PracticeAccountOverview
      :practice="state.practice"
      :manual-cycle="state.manualCycle"
      :market-summary="state.marketSummary"
      :market-summary-generating="state.marketSummaryGenerating"
      :strategy-meta="strategyMeta"
      :error="state.error"
      @manual-cycle="runManualCycle"
      @market-summary="generateMarketSummary"
      @resume="resumeTrading"
    >
      <template #chart>
        <PracticeEquityChart
          :practice="state.practice"
          @open-calendar="calendarOpen = true"
        />
      </template>
      <template #activity>
        <PracticeOperationLog :practice="state.practice" />
      </template>
      <template #rule>
        <PracticeRule
          :practice="state.practice"
          :full-snapshot-status="state.fullSnapshotStatus"
        />
      </template>
    </PracticeAccountOverview>
    <PracticeCandidatesPanel />
    <PracticeCalendar
      :open="calendarOpen"
      :practice="state.practice"
      :full-snapshot-status="state.fullSnapshotStatus"
      @close="calendarOpen = false"
      @ensure-full="ensureFullSnapshot"
    />
  </template>

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
        aria-labelledby="practiceActionAdminTitle"
        @submit.prevent="submitAdminAuthentication"
      >
        <h2 id="practiceActionAdminTitle">{{ adminDialog.title }}</h2>
        <p>{{ adminDialog.description }}</p>
        <div v-if="adminAuth.error" class="dragon-tiger-admin-error">{{ adminAuth.error }}</div>
        <label for="practiceActionAdminCredential">管理员密码</label>
        <input
          id="practiceActionAdminCredential"
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
            {{ adminAuth.submitting ? '验证中…' : adminDialog.submitLabel }}
          </button>
        </div>
      </form>
    </div>
  </Teleport>
</template>
