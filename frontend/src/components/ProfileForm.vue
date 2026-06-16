<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({
  profile: {
    type: Object,
    default: null
  },
  saving: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['submit'])
const formRef = ref(null)

const form = reactive({
  name: '',
  age: null,
  gender: '',
  diabetes_type: '',
  disease_duration: null,
  height: null,
  weight: null,
  medications: '',
  complications: '',
  target_fasting_min: null,
  target_fasting_max: null,
  target_postmeal_min: null,
  target_postmeal_max: null
})

const rules = {
  age: [{ required: true, message: '请填写年龄', trigger: 'blur' }],
  diabetes_type: [{ required: true, message: '请选择糖尿病类型', trigger: 'change' }],
  height: [{ required: true, message: '请填写身高', trigger: 'blur' }],
  weight: [{ required: true, message: '请填写体重', trigger: 'blur' }]
}

const profileCompleted = computed(() => Boolean(props.profile?.profile_completed))

const parseList = (value) => {
  return String(value || '')
    .split(/[、,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

const nullableNumber = (value) => {
  return value === '' || value === null || typeof value === 'undefined' ? null : Number(value)
}

const applyProfile = (profile) => {
  form.name = profile?.name || ''
  form.age = profile?.age ?? null
  form.gender = profile?.gender || ''
  form.diabetes_type = profile?.diabetes_type || ''
  form.disease_duration = profile?.disease_duration ?? null
  form.height = profile?.height ?? null
  form.weight = profile?.weight ?? null
  form.medications = Array.isArray(profile?.medications) ? profile.medications.join('、') : ''
  form.complications = Array.isArray(profile?.complications) ? profile.complications.join('、') : ''
  form.target_fasting_min = profile?.target_fasting_min ?? null
  form.target_fasting_max = profile?.target_fasting_max ?? null
  form.target_postmeal_min = profile?.target_postmeal_min ?? null
  form.target_postmeal_max = profile?.target_postmeal_max ?? null
}

watch(() => props.profile, applyProfile, { immediate: true })

const buildPayload = () => ({
  user_id: props.profile?.user_id || 1,
  name: form.name.trim() || null,
  age: nullableNumber(form.age),
  gender: form.gender || null,
  diabetes_type: form.diabetes_type,
  disease_duration: nullableNumber(form.disease_duration),
  height: nullableNumber(form.height),
  weight: nullableNumber(form.weight),
  medications: parseList(form.medications),
  complications: parseList(form.complications),
  target_fasting_min: nullableNumber(form.target_fasting_min),
  target_fasting_max: nullableNumber(form.target_fasting_max),
  target_postmeal_min: nullableNumber(form.target_postmeal_min),
  target_postmeal_max: nullableNumber(form.target_postmeal_max)
})

const handleSubmit = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) {
    return
  }

  const payload = buildPayload()
  if (payload.target_fasting_min != null && payload.target_fasting_max != null && payload.target_fasting_min >= payload.target_fasting_max) {
    ElMessage.error('空腹目标下限必须小于上限')
    return
  }
  if (payload.target_postmeal_min != null && payload.target_postmeal_max != null && payload.target_postmeal_min >= payload.target_postmeal_max) {
    ElMessage.error('餐后目标下限必须小于上限')
    return
  }

  emit('submit', payload)
}
</script>

<template>
  <el-card class="profile-form-card" shadow="never">
    <template #header>
      <div class="form-header">
        <span>个人资料</span>
        <el-tag :type="profileCompleted ? 'success' : 'warning'">
          {{ profileCompleted ? '已完善' : '待完善' }}
        </el-tag>
      </div>
    </template>

    <el-form ref="formRef" :model="form" :rules="rules" label-width="120px" class="profile-form">
      <el-divider content-position="left">基本信息</el-divider>
      <el-row :gutter="16">
        <el-col :xs="24" :sm="12">
          <el-form-item label="姓名">
            <el-input v-model="form.name" maxlength="100" placeholder="例如：王三" />
          </el-form-item>
        </el-col>
        <el-col :xs="24" :sm="12">
          <el-form-item label="年龄" prop="age">
            <el-input-number v-model="form.age" :min="1" :max="120" controls-position="right" />
          </el-form-item>
        </el-col>
        <el-col :xs="24" :sm="12">
          <el-form-item label="性别">
            <el-select v-model="form.gender" placeholder="请选择" clearable>
              <el-option label="男" value="male" />
              <el-option label="女" value="female" />
              <el-option label="其他" value="other" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :xs="24" :sm="12">
          <el-form-item label="糖尿病类型" prop="diabetes_type">
            <el-select v-model="form.diabetes_type" placeholder="请选择">
              <el-option label="1 型糖尿病" value="T1" />
              <el-option label="2 型糖尿病" value="T2" />
              <el-option label="妊娠糖尿病" value="gestational" />
              <el-option label="其他" value="other" />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>

      <el-divider content-position="left">病程与身体指标</el-divider>
      <el-row :gutter="16">
        <el-col :xs="24" :sm="8">
          <el-form-item label="病程(年)">
            <el-input-number v-model="form.disease_duration" :min="0" :max="100" controls-position="right" />
          </el-form-item>
        </el-col>
        <el-col :xs="24" :sm="8">
          <el-form-item label="身高(cm)" prop="height">
            <el-input-number v-model="form.height" :min="1" :max="300" :precision="1" controls-position="right" />
          </el-form-item>
        </el-col>
        <el-col :xs="24" :sm="8">
          <el-form-item label="体重(kg)" prop="weight">
            <el-input-number v-model="form.weight" :min="1" :max="500" :precision="1" controls-position="right" />
          </el-form-item>
        </el-col>
      </el-row>

      <el-divider content-position="left">目标区间</el-divider>
      <el-row :gutter="16">
        <el-col :xs="24" :sm="12">
          <el-form-item label="空腹目标">
            <div class="range-inputs">
              <el-input-number v-model="form.target_fasting_min" :precision="1" :step="0.1" placeholder="下限" />
              <span>至</span>
              <el-input-number v-model="form.target_fasting_max" :precision="1" :step="0.1" placeholder="上限" />
            </div>
          </el-form-item>
        </el-col>
        <el-col :xs="24" :sm="12">
          <el-form-item label="餐后目标">
            <div class="range-inputs">
              <el-input-number v-model="form.target_postmeal_min" :precision="1" :step="0.1" placeholder="下限" />
              <span>至</span>
              <el-input-number v-model="form.target_postmeal_max" :precision="1" :step="0.1" placeholder="上限" />
            </div>
          </el-form-item>
        </el-col>
      </el-row>

      <el-divider content-position="left">用药与并发症</el-divider>
      <el-form-item label="当前用药">
        <el-input v-model="form.medications" type="textarea" :rows="2" placeholder="多个药物可用顿号、逗号或换行分隔" />
      </el-form-item>
      <el-form-item label="并发症">
        <el-input v-model="form.complications" type="textarea" :rows="2" placeholder="多个并发症可用顿号、逗号或换行分隔" />
      </el-form-item>

      <el-form-item>
        <el-button type="primary" :loading="saving" @click="handleSubmit">保存个人资料</el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<style scoped>
.profile-form-card {
  border-radius: 16px;
}

.form-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-weight: 700;
}

.range-inputs {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.range-inputs :deep(.el-input-number) {
  width: 130px;
}
</style>
