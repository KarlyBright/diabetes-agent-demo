import { defineStore } from 'pinia'

import { getAdherenceAnalysis, getDailyAdvice } from '../api'

export const useAdviceStore = defineStore('advice', {
  state: () => ({
    daily: null,
    adherence: null,
    loadingDaily: false,
    loadingAdherence: false,
    dailyError: '',
    adherenceError: ''
  }),
  actions: {
    async fetchDaily(userId = 1) {
      this.loadingDaily = true
      this.dailyError = ''
      try {
        const res = await getDailyAdvice(userId)
        this.daily = res.data
        return this.daily
      } catch (error) {
        this.dailyError = error instanceof Error ? error.message : '加载建议失败'
        this.daily = null
        throw error
      } finally {
        this.loadingDaily = false
      }
    },
    async fetchAdherence(userId = 1) {
      this.loadingAdherence = true
      this.adherenceError = ''
      try {
        const res = await getAdherenceAnalysis(userId)
        this.adherence = res.data
        return this.adherence
      } catch (error) {
        this.adherenceError = error instanceof Error ? error.message : '加载待办失败'
        this.adherence = null
        throw error
      } finally {
        this.loadingAdherence = false
      }
    },
    async refresh(userId = 1) {
      await Promise.allSettled([this.fetchDaily(userId), this.fetchAdherence(userId)])
    }
  }
})
