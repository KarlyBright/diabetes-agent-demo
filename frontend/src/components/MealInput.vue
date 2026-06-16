<script setup>
import { reactive, ref, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'

import { analyzeMeal } from '../api'
import { eventBus } from '../utils/eventBus'
const MEAL_TEXT_MAX_LENGTH = 500

const formRef = ref(null)
const form = reactive({ meal_text: '' })
const result = ref(null)
const loading = ref(false)
const showResult = ref(false)
const errorMessage = ref('')

const rules = {
  meal_text: [{ required: true, message: '请输入饮食内容', trigger: 'blur' }]
}

const loadData = async () => {}

const getErrorMessage = (error) => {
  if (error instanceof Error && error.message) {
    return error.message
  }
  return '饮食分析失败，请稍后重试'
}

const handleChatMealAnalyzed = (mealAnalysis) => {
  if (!mealAnalysis || typeof mealAnalysis !== 'object') {
    return
  }

  errorMessage.value = ''
  result.value = mealAnalysis
  showResult.value = true
}

const handleAnalyze = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) {
    return
  }

  loading.value = true
  errorMessage.value = ''
  try {
    const res = await analyzeMeal({ user_id: 1, meal_text: form.meal_text })
    result.value = res.data
    showResult.value = true
    eventBus.value.emit('meal:analyzed', res.data)
    ElMessage.success('饮食分析完成')
  } catch (error) {
    errorMessage.value = getErrorMessage(error)
    result.value = null
    showResult.value = false
  } finally {
    loading.value = false
  }
}

const getRiskClass = (level) => level || 'low'

const getRiskLabel = (level) => {
  const map = { high: '高风险', medium: '中等', low: '低风险' }
  return map[level] || level
}

const getFoodDisplayName = (food) => food?.name || food?.food || '未知食物'

const formatConfidence = (value) => {
  const numericValue = Number(value)
  if (!Number.isFinite(numericValue)) {
    return '未知'
  }
  return `${Math.round(numericValue * 100)}%`
}

const formatPortionSource = (source) => {
  const map = {
    explicit_gram: '明确克重',
    explicit_ml: '明确毫升',
    unit_bowl: '按碗估算',
    unit_cup: '按杯估算',
    unit_piece: '按个估算',
    unit_serving: '按份估算',
    unit_mapping: '按份量词估算',
    unit_mapping_scaled: '按份量词估算',
    fuzzy_estimated: '模糊份量估算',
    default: '默认份量'
  }
  return map[source] || source || '未知来源'
}

eventBus.value.on('meal:analyzed', handleChatMealAnalyzed)

onUnmounted(() => {
  eventBus.value.off('meal:analyzed', handleChatMealAnalyzed)
})

defineExpose({ loadData })
</script>

<template>
  <div class="meal-input">
    <div class="card-header">
      <span class="icon">🍽️</span>
      <span class="title">饮食记录与分析</span>
    </div>

    <el-form ref="formRef" :model="form" :rules="rules" class="input-area">
      <el-form-item prop="meal_text">
        <el-input
          v-model="form.meal_text"
          type="textarea"
          :rows="3"
          :maxlength="MEAL_TEXT_MAX_LENGTH"
          show-word-limit
          placeholder="请输入您吃了什么，例如：早餐吃了两个馒头、一杯豆浆"
        />
      </el-form-item>
      <el-button type="primary" :loading="loading" @click="handleAnalyze">
        {{ loading ? '分析中...' : '分析' }}
      </el-button>
      <div v-if="errorMessage" class="error-message">{{ errorMessage }}</div>
    </el-form>

    <div v-if="showResult && result" class="result-area">
      <div class="result-header">
        <span :class="['risk-level', getRiskClass(result.risk_level)]">
          {{ getRiskLabel(result.risk_level) }}
        </span>
        <span class="gl-value">GL: {{ result.total_gl || 0 }} ({{ result.gl_level || '未知' }})</span>
      </div>

      <div v-if="result.confidence !== undefined || result.calculation_version" class="gl-phase1-details">
        <div v-if="result.confidence !== undefined" class="detail-row">
          <span>估算置信度</span>
          <strong>{{ formatConfidence(result.confidence) }}</strong>
        </div>
        <div v-if="result.calculation_version" class="detail-row muted">
          <span>计算版本</span>
          <span>{{ result.calculation_version }}</span>
        </div>
      </div>

      <div v-if="result.detected_foods?.length" class="foods">
        <div class="foods-title">检测到:</div>
        <div class="food-tags">
          <span
            v-for="(food, idx) in result.detected_foods"
            :key="`${getFoodDisplayName(food)}-${idx}`"
            :class="['food-tag', food.category]"
          >
            {{ getFoodDisplayName(food) }}
          </span>
        </div>
        <div class="calculation-list">
          <div
            v-for="(food, idx) in result.detected_foods"
            :key="`calc-${getFoodDisplayName(food)}-${idx}`"
            class="calculation-item"
          >
            <div class="calculation-title">
              {{ getFoodDisplayName(food) }} · {{ food.portion_g || 0 }}g
              <span class="portion-source">{{ formatPortionSource(food.portion_source) }}</span>
            </div>
            <div class="calculation-meta">
              碳水 {{ food.available_carbs_g ?? food.carbs_g ?? 0 }}g · GI {{ food.gi ?? 0 }} · GL {{ food.gl ?? 0 }} · 置信度 {{ formatConfidence(food.confidence) }}
            </div>
            <div v-if="food.warnings?.length" class="inline-warning">
              {{ food.warnings.join('；') }}
            </div>
          </div>
        </div>
      </div>

      <div v-if="result.warnings?.length" class="phase1-section warning-section">
        <div class="section-title">估算提示:</div>
        <div v-for="(warning, idx) in result.warnings" :key="`warning-${idx}`" class="phase1-item">
          {{ warning }}
        </div>
      </div>

      <div v-if="result.unrecognized_items?.length" class="phase1-section">
        <div class="section-title">未计入 GL:</div>
        <div v-for="(item, idx) in result.unrecognized_items" :key="`unknown-${idx}`" class="phase1-item">
          {{ item.raw_text }}：{{ item.reason }}
        </div>
      </div>

      <div v-if="result.ignored_foods?.length" class="phase1-section">
        <div class="section-title">已忽略:</div>
        <div v-for="(item, idx) in result.ignored_foods" :key="`ignored-${idx}`" class="phase1-item">
          {{ item.food_name || item.raw_text }}：{{ item.reason }}
        </div>
      </div>

      <div v-if="result.suggestion?.length" class="suggestions">
        <div class="suggestions-title">建议:</div>
        <div v-for="(s, idx) in result.suggestion" :key="idx" class="suggestion-item">
          {{ s }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.meal-input {
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
  color: #1e293b;
  font-size: 14px;
  font-weight: 600;
}

.input-area {
  margin-bottom: 10px;
}

.error-message {
  margin-top: 8px;
  color: #dc2626;
  font-size: 12px;
}

.result-area {
  background: #f8fafc;
  border-radius: 8px;
  padding: 10px;
}

.result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.risk-level {
  padding: 3px 10px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 500;
}

.risk-level.high {
  background: #fee2e2;
  color: #dc2626;
}

.risk-level.medium {
  background: #fef3c7;
  color: #b45309;
}

.risk-level.low {
  background: #dcfce7;
  color: #16a34a;
}

.gl-value,
.foods-title,
.suggestions-title {
  color: #64748b;
  font-size: 12px;
}

.food-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin: 6px 0;
}

.food-tag {
  background: #eff6ff;
  color: #2563eb;
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 11px;
}

.food-tag.high_risk {
  background: #fee2e2;
  color: #dc2626;
}

.food-tag.medium_risk {
  background: #fef3c7;
  color: #b45309;
}

.food-tag.protective {
  background: #dcfce7;
  color: #16a34a;
}

.gl-phase1-details,
.calculation-list,
.phase1-section {
  margin-top: 8px;
}

.detail-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #475569;
  font-size: 12px;
  line-height: 1.6;
}

.detail-row.muted {
  color: #94a3b8;
  font-size: 11px;
}

.calculation-item {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  margin-top: 6px;
  padding: 7px;
}

.calculation-title {
  color: #334155;
  font-size: 12px;
  font-weight: 600;
}

.portion-source {
  background: #f1f5f9;
  border-radius: 999px;
  color: #64748b;
  display: inline-block;
  font-size: 10px;
  font-weight: 400;
  margin-left: 6px;
  padding: 1px 6px;
}

.calculation-meta,
.inline-warning,
.phase1-item {
  color: #64748b;
  font-size: 11px;
  line-height: 1.5;
  margin-top: 3px;
}

.inline-warning,
.warning-section .phase1-item {
  color: #b45309;
}

.section-title {
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 3px;
}

.suggestion-item {
  color: #475569;
  font-size: 12px;
  line-height: 1.5;
  margin-top: 4px;
}
</style>
