<script setup>
import { computed, onMounted, onUnmounted } from 'vue'

import { useAdviceStore } from '../stores/advice'
import { useMedicationStore } from '../stores/medication'
import { eventBus } from '../utils/eventBus'
import { formatFrequencyLabel } from '../utils/medicationSchedule'

const medicationStore = useMedicationStore()
const adviceStore = useAdviceStore()

const today = computed(() => new Date().toISOString().slice(0, 10))

const getTodayStatus = (planId) => {
  const record = medicationStore.takenRecords.find((r) => r.plan_id === planId && r.created_at?.slice(0, 10) === today.value)
  return record?.status || 'pending'
}

const handleTake = async (plan) => {
  await medicationStore.take(plan, 1)
  eventBus.value.emit('medication:taken')
  await adviceStore.refresh(1)
}

const formatTime = (time) => (time ? time.slice(0, 5) : '')

const loadData = async () => {
  await medicationStore.refresh(1)
}

onMounted(loadData)

const handleMedicationConfirmed = () => loadData()
const handleMedicationTaken = () => loadData()

eventBus.value.on('medication:confirmed', handleMedicationConfirmed)
eventBus.value.on('medication:taken', handleMedicationTaken)

onUnmounted(() => {
  eventBus.value.off('medication:confirmed', handleMedicationConfirmed)
  eventBus.value.off('medication:taken', handleMedicationTaken)
})

defineExpose({ loadData })
</script>

<template>
  <div class="medication-card">
    <div class="card-header">
      <span class="icon">💊</span>
      <span class="title">用药提醒</span>
      <el-button size="small" :loading="medicationStore.loading" @click="loadData">刷新</el-button>
    </div>

    <div v-if="medicationStore.error" class="inline-error">{{ medicationStore.error }}</div>
    <div v-if="medicationStore.plans.length === 0" class="empty">
      暂无用药计划，请在对话中设置
    </div>

    <div v-else class="plans-list">
      <div v-for="plan in medicationStore.plans" :key="plan.plan_id" class="plan-item">
        <div class="plan-info">
          <span class="drug-name">{{ plan.drug_name }}</span>
          <span class="dosage">{{ plan.dosage }}</span>
        </div>
        <div class="plan-time">
          <span class="time-text">{{ plan.time_text }}</span>
          <span class="remind-time">{{ formatTime(plan.remind_time) }}</span>
          <span class="frequency">{{ formatFrequencyLabel(plan.frequency) }}</span>
        </div>
        <div class="plan-action">
          <span :class="['status', getTodayStatus(plan.plan_id)]">
            {{ getTodayStatus(plan.plan_id) === 'taken' ? '✓ 已服' : '待服药' }}
          </span>
          <el-button
            v-if="getTodayStatus(plan.plan_id) !== 'taken'"
            size="small"
            type="success"
            @click="handleTake(plan)"
          >
            服药
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.medication-card {
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

.empty,
.inline-error {
  color: #94a3b8;
  font-size: 12px;
  text-align: center;
  padding: 12px;
}

.inline-error {
  color: #dc2626;
}

.plans-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.plan-item {
  background: #f8fafc;
  border-radius: 8px;
  padding: 10px;
}

.plan-info {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.drug-name {
  color: #1e293b;
  font-size: 13px;
  font-weight: 600;
}

.dosage {
  color: #64748b;
  font-size: 12px;
}

.plan-time {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  color: #64748b;
  font-size: 11px;
  flex-wrap: wrap;
}

.time-text,
.frequency {
  background: #eff6ff;
  color: #3b82f6;
  padding: 2px 6px;
  border-radius: 4px;
}

.frequency {
  background: #f3e8ff;
  color: #7e22ce;
}

.plan-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.status {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
}

.status.pending {
  background: #fef3c7;
  color: #b45309;
}

.status.taken {
  background: #dcfce7;
  color: #15803d;
}
</style>
