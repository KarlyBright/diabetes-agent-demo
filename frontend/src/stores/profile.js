import { defineStore } from 'pinia'

import { getUserProfile, saveUserProfile } from '../api'

export const useProfileStore = defineStore('profile', {
  state: () => ({
    profile: null,
    loading: false,
    saving: false,
    error: ''
  }),
  getters: {
    profileCompleted: (state) => Boolean(state.profile?.profile_completed)
  },
  actions: {
    async fetchProfile(userId = 1) {
      this.loading = true
      this.error = ''
      try {
        const res = await getUserProfile(userId)
        this.profile = res.data
        return this.profile
      } catch (error) {
        this.error = error instanceof Error ? error.message : '加载个人资料失败'
        throw error
      } finally {
        this.loading = false
      }
    },
    async saveProfile(payload) {
      this.saving = true
      this.error = ''
      try {
        const res = await saveUserProfile(payload)
        this.profile = res.data
        return this.profile
      } catch (error) {
        this.error = error instanceof Error ? error.message : '保存个人资料失败'
        throw error
      } finally {
        this.saving = false
      }
    }
  }
})
