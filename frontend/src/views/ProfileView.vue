<script setup>
import { onMounted } from 'vue'
import { ElMessage } from 'element-plus'

import ProfileForm from '../components/ProfileForm.vue'
import { useAdviceStore } from '../stores/advice'
import { useProfileStore } from '../stores/profile'

const profileStore = useProfileStore()
const adviceStore = useAdviceStore()

const handleSubmit = async (payload) => {
  await profileStore.saveProfile(payload)
  await adviceStore.fetchDaily(payload.user_id || 1).catch(() => null)
  ElMessage.success('个人资料已保存')
}

onMounted(() => {
  void profileStore.fetchProfile(1)
})
</script>

<template>
  <main class="page profile-page">
    <section class="page-header">
      <h1>个人资料</h1>
      <p>完善基础信息、目标血糖区间和用药情况，以获得更精准的建议。</p>
    </section>

    <el-alert
      v-if="profileStore.error"
      class="page-alert"
      :title="profileStore.error"
      type="error"
      show-icon
      :closable="false"
    />

    <ProfileForm
      v-loading="profileStore.loading"
      :profile="profileStore.profile"
      :saving="profileStore.saving"
      @submit="handleSubmit"
    />
  </main>
</template>

<style scoped>
.page {
  max-width: 1120px;
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

.page-alert {
  margin-bottom: 16px;
}
</style>
