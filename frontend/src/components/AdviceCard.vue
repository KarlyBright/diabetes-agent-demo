<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'

import { useAdviceStore } from '../stores/advice'
import { useProfileStore } from '../stores/profile'
import { eventBus } from '../utils/eventBus'

const adviceStore = useAdviceStore()
const profileStore = useProfileStore()
const router = useRouter()

const data = computed(() => adviceStore.daily)
const loading = computed(() => adviceStore.loadingDaily)
const errorMessage = computed(() => adviceStore.dailyError)

const loadData = async () => {
  await Promise.allSettled([adviceStore.fetchDaily(1), profileStore.fetchProfile(1)])
}

const getRiskClass = (level) => level || 'low'

const getRiskLabel = (level) => {
  const map = { high: '高', medium: '中', low: '低' }
  return map[level] || level || '低'
}

const goProfile = () => {
  void router.push('/profile')
}

onMounted(loadData)

const handleRefresh = () => {
  void loadData()
}

eventBus.value.on('glucose:added', handleRefresh)
eventBus.value.on('meal:analyzed', handleRefresh)
eventBus.value.on('medication:confirmed', handleRefresh)
eventBus.value.on('medication:taken', handleRefresh)
eventBus.value.on('advice:refresh', handleRefresh)

onUnmounted(() => {
  eventBus.value.off('glucose:added', handleRefresh)
  eventBus.value.off('meal:analyzed', handleRefresh)
  eventBus.value.off('medication:confirmed', handleRefresh)
  eventBus.value.off('medication:taken', handleRefresh)
  eventBus.value.off('advice:refresh', handleRefresh)
})

defineExpose({ loadData })
</script>

<template>
  <div class="advice-card">
    <div class="card-header">
      <span class="icon">📝</span>
      <span class="title">今日建议</span>
      <el-button size="small" :loading="loading" @click="loadData">刷新</el-button>
    </div>

    <div v-if="loading" class="loading">加载中...</div>
    <div v-else-if="errorMessage && !data" class="empty">{{ errorMessage }}</div>
    <div v-else-if="!data" class="empty">暂无建议</div>
    <div v-else class="content">
      <el-alert v-if="errorMessage" class="inline-alert" :title="errorMessage" type="error" show-icon :closable="false" />

      <div v-if="data.profile_summary" class="profile">{{ data.profile_summary }}</div>
      <div v-else class="profile profile-empty">
        <span>请先完善个人资料以获得更精准建议</span>
        <el-button type="primary" link @click="goProfile">去完善</el-button>
      </div>

      <div class="section">
        <div class="section-title">血糖</div>
        <div class="section-content">{{ data.glucose_summary }}</div>
      </div>

      <div class="section">
        <div class="section-title">饮食</div>
        <div class="section-content">{{ data.meal_summary }}</div>
      </div>

      <div :class="['risk-badge', getRiskClass(data.risk_level)]">
        风险等级：{{ getRiskLabel(data.risk_level) }}
      </div>

      <div v-if="data.daily_advice?.length" class="advice-list">
        <div class="advice-title">建议</div>
        <div v-for="(item, idx) in data.daily_advice" :key="idx" class="advice-item">
          {{ item }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.advice-card {
  padding: 10px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
}

.icon {
  font-size: 16px;
}

.title {
  flex: 1;
  color: #1e293b;
  font-size: 14px;
  font-weight: 600;
}

.loading,
.empty {
  color: #94a3b8;
  font-size: 12px;
  text-align: center;
  padding: 12px;
}

.inline-alert {
  margin-bottom: 10px;
}

.profile {
  background: #eff6ff;
  color: #2563eb;
  padding: 8px;
  border-radius: 8px;
  font-size: 12px;
  margin-bottom: 10px;
}

.profile-empty {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  background: #fff7ed;
  color: #c2410c;
}

.section {
  margin-bottom: 10px;
}

.section-title,
.advice-title {
  font-weight: 600;
  color: #1e293b;
  font-size: 12px;
  margin-bottom: 4px;
}

.section-content {
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

.risk-badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 12px;
  margin-bottom: 10px;
}

.risk-badge.high {
  background: #fee2e2;
  color: #dc2626;
}

.risk-badge.medium {
  background: #fef3c7;
  color: #b45309;
}

.risk-badge.low {
  background: #dcfce7;
  color: #15803d;
}

.advice-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.advice-item {
  background: #f8fafc;
  border-left: 3px solid #3b82f6;
  padding: 8px;
  border-radius: 4px;
  font-size: 12px;
  color: #475569;
  line-height: 1.5;
}
</style>
