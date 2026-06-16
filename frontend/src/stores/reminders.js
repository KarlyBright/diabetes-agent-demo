import { defineStore } from 'pinia'

export const useRemindersStore = defineStore('reminders', {
  state: () => ({
    connected: false,
    lastError: '',
    seenReminderIds: []
  }),
  actions: {
    markConnected(value) {
      this.connected = value
      if (value) {
        this.lastError = ''
      }
    },
    markError(message) {
      this.connected = false
      this.lastError = message || '提醒推送连接失败'
    },
    hasSeen(reminderId) {
      return this.seenReminderIds.includes(reminderId)
    },
    markSeen(reminderId) {
      if (!this.hasSeen(reminderId)) {
        this.seenReminderIds = [...this.seenReminderIds, reminderId]
      }
    }
  }
})
