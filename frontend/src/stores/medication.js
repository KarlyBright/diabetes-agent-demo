import { defineStore } from 'pinia'

import { getMedicationPlans, getMedicationTaken, recordMedicationTaken } from '../api'

export const useMedicationStore = defineStore('medication', {
  state: () => ({
    plans: [],
    takenRecords: [],
    loading: false,
    error: ''
  }),
  actions: {
    async refresh(userId = 1) {
      this.loading = true
      this.error = ''
      try {
        const [plansRes, takenRes] = await Promise.all([getMedicationPlans(userId), getMedicationTaken(userId)])
        this.plans = plansRes.data || []
        this.takenRecords = takenRes.data || []
      } catch (error) {
        this.error = error instanceof Error ? error.message : '加载用药计划失败'
      } finally {
        this.loading = false
      }
    },
    async take(plan, userId = 1) {
      await recordMedicationTaken({ user_id: userId, plan_id: plan.plan_id, status: 'taken' })
      await this.refresh(userId)
    }
  }
})
