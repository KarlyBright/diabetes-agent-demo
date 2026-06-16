<script setup>
import { reactive, ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'

import GlucoseTrendChart from './GlucoseTrendChart.vue'
import { useAdviceStore } from '../stores/advice'
import { useGlucoseStore } from '../stores/glucose'
import { eventBus } from '../utils/eventBus'

const glucoseStore = useGlucoseStore()
const adviceStore = useAdviceStore()
const showForm = ref(false)
const formRef = ref(null)
const formData = reactive({
  user_id: 1,
  value: null,
  measure_time: '',
  measure_type: 'fasting',
  source: 'manual'
})

const rules = {
  value: [{ required: true, message: '请填写血糖值', trigger: 'blur' }],
  measure_time: [{ required: true, message: '请选择测量时间', trigger: 'change' }]
}

const getTypeLabel = (type) => {
  const map = { fasting: '空腹', post_meal: '餐后', before_sleep: '睡前' }
  return map[type] || type
}

const getSourceLabel = (source) => {
  const map = { manual: '手动', device: '设备', agent: '助手' }
  return map[source] || source
}

const getDeviceStateLabel = (state) => {
  const map = { disconnected: '未连接', connected: '已连接', synced: '已同步' }
  return map[state] || state
}

const resetForm = () => {
  formData.value = null
  formData.measure_time = ''
  formData.measure_type = 'fasting'
  formData.source = 'manual'
}

const refreshDependents = async () => {
  eventBus.value.emit('glucose:added')
  await adviceStore.refresh(1)
}

const handleSubmit = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) {
    return
  }

  await glucoseStore.addRecord({ ...formData })
  showForm.value = false
  resetForm()
  ElMessage.success('血糖记录已保存')
  await refreshDependents()
}

const handleConnectDevice = async () => {
  try {
    if (glucoseStore.deviceStatus.connected) {
      await glucoseStore.disconnect()
      ElMessage.success('已断开演示血糖仪')
    } else {
      await glucoseStore.connect()
      ElMessage.success(`已连接 ${glucoseStore.deviceStatus.device_name}`)
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '设备操作失败')
  }
}

const handleMockSync = async () => {
  try {
    const data = await glucoseStore.mockSync()
    eventBus.value.emit('device:sync', {
      content: data.message || '已完成 GLP 演示同步',
      refresh: data.refresh || ['glucose', 'adherence', 'advice']
    })
    await refreshDependents()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : 'GLP 演示同步失败，请先连接血糖仪')
    await glucoseStore.fetchDeviceStatus()
  }
}

const loadData = async () => {
  await glucoseStore.refresh(1)
}

onMounted(loadData)

const handleGlucoseAdded = () => {
  void loadData()
}

eventBus.value.on('glucose:added', handleGlucoseAdded)

onUnmounted(() => {
  eventBus.value.off('glucose:added', handleGlucoseAdded)
})

defineExpose({ loadData })
</script>

<template>
  <div class="glucose-card">
    <div class="card-header">
      <span class="icon">📊</span>
      <span class="title">血糖记录</span>
      <span class="device-state" :data-state="glucoseStore.deviceStatus.state">
        {{ getDeviceStateLabel(glucoseStore.deviceStatus.state) }}
      </span>
      <el-button size="small" :loading="glucoseStore.deviceLoading" @click="handleConnectDevice">
        {{ glucoseStore.deviceStatus.connected ? '断开设备' : '连接血糖仪' }}
      </el-button>
      <el-button size="small" type="success" :disabled="!glucoseStore.deviceStatus.connected" @click="handleMockSync">
        GLP 同步
      </el-button>
      <el-button size="small" type="primary" @click="showForm = true">录入</el-button>
    </div>

    <div class="device-panel">
      <div class="device-panel-row">
        <span class="device-name">{{ glucoseStore.deviceStatus.device_name }}</span>
        <span class="protocol-badge">{{ glucoseStore.deviceStatus.protocol?.profile }} · {{ glucoseStore.deviceStatus.protocol?.service_uuid }}</span>
      </div>
      <div class="device-panel-row muted">
        <span>最近同步：{{ glucoseStore.deviceStatus.last_sync_at ? glucoseStore.deviceStatus.last_sync_at.replace('T', ' ') : '尚未同步' }}</span>
        <span>新增记录：{{ glucoseStore.deviceStatus.last_sync_count || 0 }}</span>
      </div>
      <div v-if="glucoseStore.error || glucoseStore.deviceStatus.last_error" class="device-panel-row feedback">
        {{ glucoseStore.error || glucoseStore.deviceStatus.last_error }}
      </div>
    </div>

    <GlucoseTrendChart :trend="glucoseStore.trend" />

    <div class="records-list">
      <div v-if="glucoseStore.records.length === 0" class="empty">暂无记录</div>
      <div v-for="r in glucoseStore.records" :key="r.id" class="record-item">
        <span class="value">{{ r.value }}</span>
        <span class="unit">mmol/L</span>
        <span class="type">{{ getTypeLabel(r.measure_type) }}</span>
        <span class="source" :data-source="r.source">{{ getSourceLabel(r.source) }}</span>
        <span class="time">{{ r.measure_time?.slice(0, 16) }}</span>
      </div>
    </div>

    <el-dialog v-model="showForm" title="录入血糖" width="420px">
      <el-form ref="formRef" :model="formData" :rules="rules" label-width="90px">
        <el-form-item label="血糖值" prop="value">
          <el-input-number v-model="formData.value" :precision="1" :step="0.1" :min="0" controls-position="right" />
        </el-form-item>
        <el-form-item label="测量类型">
          <el-select v-model="formData.measure_type">
            <el-option label="空腹" value="fasting" />
            <el-option label="餐后" value="post_meal" />
            <el-option label="睡前" value="before_sleep" />
          </el-select>
        </el-form-item>
        <el-form-item label="测量时间" prop="measure_time">
          <el-date-picker v-model="formData.measure_time" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.glucose-card {
  padding: 10px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.icon {
  font-size: 16px;
}

.title {
  font-weight: 600;
  font-size: 14px;
  color: #1e293b;
  flex: 1;
}

.device-state {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #e2e8f0;
  color: #334155;
}

.device-state[data-state='connected'] {
  background: #dbeafe;
  color: #1d4ed8;
}

.device-state[data-state='synced'] {
  background: #dcfce7;
  color: #15803d;
}

.device-panel {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px;
  margin-bottom: 10px;
}

.device-panel-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: #1e293b;
  margin-bottom: 6px;
  flex-wrap: wrap;
}

.device-panel-row:last-child {
  margin-bottom: 0;
}

.device-panel-row.muted {
  color: #64748b;
}

.device-panel-row.feedback {
  color: #0f766e;
  font-weight: 500;
}

.device-name {
  font-weight: 600;
}

.protocol-badge {
  background: #eff6ff;
  color: #2563eb;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
}

.records-list {
  max-height: 160px;
  overflow-y: auto;
  margin-top: 10px;
}

.empty {
  color: #94a3b8;
  font-size: 12px;
  text-align: center;
  padding: 12px;
}

.record-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 0;
  border-bottom: 1px solid #f1f5f9;
  font-size: 12px;
}

.record-item .value {
  font-weight: 600;
  font-size: 15px;
  color: #ef4444;
}

.record-item .unit {
  color: #64748b;
}

.record-item .type,
.source {
  background: #eff6ff;
  color: #3b82f6;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
}

.source[data-source='device'] {
  background: #dcfce7;
  color: #15803d;
}

.source[data-source='agent'] {
  background: #f3e8ff;
  color: #7e22ce;
}

.source[data-source='manual'] {
  background: #fef3c7;
  color: #b45309;
}

.record-item .time {
  color: #94a3b8;
  margin-left: auto;
  font-size: 11px;
}
</style>
