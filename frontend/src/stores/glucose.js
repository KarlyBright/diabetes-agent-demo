import { defineStore } from 'pinia'

import {
  addGlucoseRecord,
  connectDevice,
  disconnectDevice,
  getDeviceStatus,
  getGlucoseTrend,
  getRecentGlucose,
  mockSyncDevice
} from '../api'

export const useGlucoseStore = defineStore('glucose', {
  state: () => ({
    records: [],
    trend: { target: { min: 3.9, max: 10 }, points: [] },
    deviceStatus: {
      connected: false,
      state: 'disconnected',
      device_name: 'GLP 演示血糖仪',
      protocol: { profile: 'GLP', service_uuid: '0x1808', characteristics: [] },
      features: [],
      last_sync_at: null,
      last_sync_count: 0,
      last_error: null
    },
    loading: false,
    deviceLoading: false,
    error: ''
  }),
  actions: {
    async fetchRecent(userId = 1) {
      const res = await getRecentGlucose(userId)
      this.records = res.data || []
      return this.records
    },
    async fetchTrend(userId = 1, days = 7) {
      const res = await getGlucoseTrend(userId, days)
      this.trend = res.data || { target: { min: 3.9, max: 10 }, points: [] }
      return this.trend
    },
    async fetchDeviceStatus() {
      const res = await getDeviceStatus()
      this.deviceStatus = res.data || this.deviceStatus
      return this.deviceStatus
    },
    async refresh(userId = 1) {
      this.loading = true
      this.error = ''
      try {
        await Promise.all([this.fetchRecent(userId), this.fetchTrend(userId), this.fetchDeviceStatus()])
      } catch (error) {
        this.error = error instanceof Error ? error.message : '加载血糖数据失败'
      } finally {
        this.loading = false
      }
    },
    async addRecord(payload) {
      await addGlucoseRecord(payload)
      await this.refresh(payload.user_id || 1)
    },
    async connect() {
      this.deviceLoading = true
      try {
        const res = await connectDevice()
        this.deviceStatus = res.data || this.deviceStatus
      } finally {
        this.deviceLoading = false
      }
    },
    async disconnect() {
      this.deviceLoading = true
      try {
        const res = await disconnectDevice()
        this.deviceStatus = res.data || this.deviceStatus
      } finally {
        this.deviceLoading = false
      }
    },
    async mockSync() {
      const res = await mockSyncDevice()
      await this.refresh(1)
      return res.data || {}
    }
  }
})
