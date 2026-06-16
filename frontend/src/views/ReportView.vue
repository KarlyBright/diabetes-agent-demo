<script setup>
import { computed, onMounted, ref } from 'vue'

import { summarizeGlucoseTrend } from '../api'
import GlucoseTrendChart from '../components/GlucoseTrendChart.vue'
import { useGlucoseStore } from '../stores/glucose'

const glucoseStore = useGlucoseStore()
const glucoseSummary = ref('')
const summaryLoading = ref(false)
const summaryError = ref('')
const reportLoading = ref(false)
const reportError = ref('')

const trendPoints = computed(() => glucoseStore.trend?.points || [])
const canSummarize = computed(() => trendPoints.value.length > 0 && !summaryLoading.value)

const formatMeasureType = (type) => {
  const labels = { fasting: '空腹', post_meal: '餐后', before_sleep: '睡前' }
  return labels[type] || type || '未标注'
}

const formatSource = (source) => {
  const labels = { manual: '手动', device: '设备', agent: '助手' }
  return labels[source] || source || '未知'
}

const getErrorMessage = (error, fallback) => {
  return error instanceof Error && error.message ? error.message : fallback
}

const loadReportData = async () => {
  reportLoading.value = true
  reportError.value = ''
  try {
    await Promise.all([glucoseStore.fetchRecent(1), glucoseStore.fetchTrend(1, 7)])
  } catch (error) {
    reportError.value = getErrorMessage(error, '加载健康报告数据失败')
  } finally {
    reportLoading.value = false
  }
}

const handleGenerateSummary = async () => {
  if (!canSummarize.value) {
    summaryError.value = '暂无近7日血糖趋势数据，无法生成总结'
    return
  }

  summaryLoading.value = true
  summaryError.value = ''
  glucoseSummary.value = ''

  try {
    const result = await summarizeGlucoseTrend(1, 7)
    glucoseSummary.value = result.data?.summary || '未返回有效总结'
  } catch (error) {
    summaryError.value = getErrorMessage(error, '生成血糖总结失败')
  } finally {
    summaryLoading.value = false
  }
}

onMounted(() => {
  void loadReportData()
})
</script>

<template>
  <main class="page">
    <section class="page-header">
      <h1>健康报告</h1>
      <p>查看近期血糖记录、7日趋势，并生成近况总结。</p>
    </section>

    <el-alert v-if="reportError" class="report-feedback" :title="reportError" type="error" show-icon :closable="false" />

    <div class="report-grid">
      <el-card shadow="never">
        <template #header>近期血糖</template>
        <el-skeleton v-if="reportLoading" :rows="4" animated />
        <el-empty v-else-if="glucoseStore.records.length === 0" description="暂无血糖记录" />
        <el-table v-else :data="glucoseStore.records" size="small">
          <el-table-column prop="value" label="血糖" width="90" />
          <el-table-column label="类型" width="120">
            <template #default="scope">{{ formatMeasureType(scope.row.measure_type) }}</template>
          </el-table-column>
          <el-table-column label="来源" width="100">
            <template #default="scope">{{ formatSource(scope.row.source) }}</template>
          </el-table-column>
          <el-table-column prop="measure_time" label="时间" />
        </el-table>
      </el-card>

      <el-card shadow="never">
        <template #header>7 日血糖趋势</template>
        <el-skeleton v-if="reportLoading" :rows="4" animated />
        <GlucoseTrendChart v-else :trend="glucoseStore.trend" />
      </el-card>
    </div>

    <el-card shadow="never" class="summary-card">
      <template #header>近况血糖总结</template>
      <div class="summary-actions">
        <el-button type="primary" :loading="summaryLoading" :disabled="!canSummarize" @click="handleGenerateSummary">
          {{ summaryLoading ? '生成中...' : '生成近况总结' }}
        </el-button>
        <span v-if="trendPoints.length === 0" class="summary-hint">暂无近7日血糖趋势数据</span>
      </div>
      <el-alert v-if="summaryError" class="summary-feedback" :title="summaryError" type="error" show-icon :closable="false" />
      <p v-if="glucoseSummary" class="summary-text">{{ glucoseSummary }}</p>
      <p v-else-if="!summaryError" class="summary-placeholder">点击按钮后，将基于近7日血糖趋势生成一段简短总结。</p>
    </el-card>
  </main>
</template>

<style scoped>
.page {
  max-width: 1280px;
  margin: 0 auto;
  padding: 24px;
}

.page-header {
  text-align: center;
  margin-bottom: 20px;
}

.page-header h1 {
  color: #1e293b;
  font-size: 24px;
  font-weight: 600;
  letter-spacing: 1px;
  margin: 0 0 6px;
}

.page-header p {
  color: #64748b;
  font-size: 13px;
  margin: 0;
}

.report-feedback {
  margin-bottom: 16px;
}

.report-grid {
  display: grid;
  grid-template-columns: 1fr 1.4fr;
  gap: 16px;
}

.summary-card {
  margin-top: 16px;
}

.summary-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.summary-hint,
.summary-placeholder {
  color: #94a3b8;
  font-size: 13px;
}

.summary-feedback {
  margin-top: 12px;
}

.summary-text {
  margin: 14px 0 0;
  color: #475569;
  line-height: 1.8;
  white-space: pre-wrap;
}

.summary-placeholder {
  margin: 14px 0 0;
}

@media (max-width: 900px) {
  .report-grid {
    grid-template-columns: 1fr;
  }
}
</style>
