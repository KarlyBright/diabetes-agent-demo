<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'

import { acknowledgeChatReminders, buildReminderStreamUrl, pollChatReminders, sendChatMessage } from '../api'
import { useAdviceStore } from '../stores/advice'
import { useGlucoseStore } from '../stores/glucose'
import { useMedicationStore } from '../stores/medication'
import { useRemindersStore } from '../stores/reminders'
import { eventBus } from '../utils/eventBus'
import { appendUniqueReminderMessage } from '../utils/reminderMessages'

const REMINDER_POLL_INTERVAL_MS = 15000
const DEMO_USER_ID = 1
const MAX_CHAT_IMAGE_COUNT = 3
const MAX_CHAT_IMAGE_BYTES = 5 * 1024 * 1024
const MAX_CHAT_MESSAGE_LENGTH = 500
const ALLOWED_IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])

const adviceStore = useAdviceStore()
const glucoseStore = useGlucoseStore()
const medicationStore = useMedicationStore()
const remindersStore = useRemindersStore()

const inputMessage = ref('')
const selectedImages = ref([])
const fileInputRef = ref(null)
const isSending = ref(false)
const composerError = ref('')
const messages = ref([
  {
    role: 'assistant',
    content: '你好！我是你的糖尿病管理助手。你可以告诉我血糖、饮食或用药情况，我会帮你分析和建议。',
    images: []
  }
])
const messagesContainer = ref(null)
const seenReminderIds = new Set()
const displayedReminderIds = new Set()
const pendingReminderIds = new Set()
let reminderPollTimer = null
let reminderAbortController = null
let chatAbortController = null
let reminderEventSource = null
let isReminderPollInFlight = false
let isChatPanelMounted = false
let isPollingFallbackEnabled = false

const canSend = computed(() => {
  return !isSending.value && (inputMessage.value.trim().length > 0 || selectedImages.value.length > 0)
})

const buildImageId = () => `${Date.now()}-${Math.random().toString(16).slice(2)}`

const buildPreviewImage = (file, previewUrl = URL.createObjectURL(file)) => ({
  id: buildImageId(),
  file,
  name: file.name,
  previewUrl
})

const revokeImages = (images = []) => {
  images.forEach((image) => {
    if (image?.previewUrl) {
      URL.revokeObjectURL(image.previewUrl)
    }
  })
}

const cloneImagesForMessage = (images = []) => {
  return images.map((image) => ({
    id: buildImageId(),
    name: image.name,
    previewUrl: URL.createObjectURL(image.file)
  }))
}

const resetSelectedImages = () => {
  revokeImages(selectedImages.value)
  selectedImages.value = []
  if (fileInputRef.value) {
    fileInputRef.value.value = ''
  }
}

const clearComposerError = () => {
  composerError.value = ''
}

const scrollToBottom = () => {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

const emitRefreshEvents = (refreshList, payload = null) => {
  if (!Array.isArray(refreshList)) {
    return
  }

  if (refreshList.includes('glucose')) {
    void glucoseStore.refresh(DEMO_USER_ID)
    eventBus.value.emit('glucose:added')
  }
  if (refreshList.includes('meal')) {
    eventBus.value.emit('meal:analyzed', payload?.meal_analysis ?? null)
  }
  if (refreshList.includes('medication')) {
    void medicationStore.refresh(DEMO_USER_ID)
  }
  if (refreshList.includes('adherence')) {
    void adviceStore.fetchAdherence(DEMO_USER_ID).catch(() => null)
    eventBus.value.emit('adherence:refresh')
  }
  if (refreshList.includes('advice')) {
    void adviceStore.fetchDaily(DEMO_USER_ID).catch(() => null)
    eventBus.value.emit('advice:refresh')
  }
}

const appendMessage = ({ role = 'assistant', content = '', images = [] }) => {
  if (!isChatPanelMounted) {
    return
  }

  const hasImages = Array.isArray(images) && images.length > 0
  const normalizedContent = typeof content === 'string' ? content : ''
  const fallbackContent = role === 'assistant' && !hasImages ? '收到' : ''

  messages.value.push({
    role,
    content: normalizedContent || fallbackContent,
    images: hasImages ? images : []
  })
  scrollToBottom()
}

const scheduleNextReminderPoll = () => {
  if (!isChatPanelMounted) {
    return
  }

  reminderPollTimer = window.setTimeout(() => {
    void pollReminders()
  }, REMINDER_POLL_INTERVAL_MS)
}

const processReminder = async (reminder) => {
  const reminderId = reminder?.reminder_id
  if (!reminderId || pendingReminderIds.has(reminderId) || seenReminderIds.has(reminderId)) {
    return
  }

  pendingReminderIds.add(reminderId)
  try {
    if (!displayedReminderIds.has(reminderId)) {
      const nextMessages = appendUniqueReminderMessage({
        messages: messages.value,
        seenReminderIds,
        reminder
      })

      if (nextMessages !== messages.value) {
        messages.value = nextMessages
        displayedReminderIds.add(reminderId)
        scrollToBottom()
      }
    }

    const ackResult = await acknowledgeChatReminders([reminderId], DEMO_USER_ID)
    ;(ackResult.acknowledged_ids || []).forEach((acknowledgedId) => {
      seenReminderIds.add(acknowledgedId)
      remindersStore.markSeen(acknowledgedId)
    })
    emitRefreshEvents(reminder.refresh)
  } catch (error) {
    remindersStore.markError(getErrorMessage(error))
    if (!isPollingFallbackEnabled) {
      startReminderPolling()
    }
  } finally {
    pendingReminderIds.delete(reminderId)
  }
}

const pollReminders = async () => {
  if (!isChatPanelMounted || isReminderPollInFlight) {
    return
  }

  isReminderPollInFlight = true
  reminderAbortController = new AbortController()

  try {
    const reminders = await pollChatReminders(DEMO_USER_ID, reminderAbortController.signal)
    const unseenReminders = reminders.filter((reminder) => !seenReminderIds.has(reminder.reminder_id))

    if (unseenReminders.length > 0 && isChatPanelMounted) {
      const ackResult = await acknowledgeChatReminders(
        unseenReminders.map((reminder) => reminder.reminder_id),
        DEMO_USER_ID,
        reminderAbortController.signal
      )
      ;(ackResult.acknowledged_ids || []).forEach((reminderId) => {
        seenReminderIds.add(reminderId)
      })

      unseenReminders.forEach((reminder) => {
        const reminderId = reminder.reminder_id
        if (!seenReminderIds.has(reminderId)) {
          return
        }

        if (!displayedReminderIds.has(reminderId)) {
          appendMessage({
            role: reminder.role || 'assistant',
            content: reminder.content || '收到新的用药提醒'
          })
          displayedReminderIds.add(reminderId)
        }
        remindersStore.markSeen(reminderId)
        emitRefreshEvents(reminder.refresh)
      })
    }
  } catch (error) {
    if (error.name !== 'AbortError') {
      remindersStore.markError(error instanceof Error ? error.message : '轮询用药提醒失败')
    }
  } finally {
    isReminderPollInFlight = false
    reminderAbortController = null
    scheduleNextReminderPoll()
  }
}

const startReminderPolling = () => {
  isPollingFallbackEnabled = true
  void pollReminders()
}

const startReminderStream = () => {
  if (typeof EventSource === 'undefined') {
    startReminderPolling()
    return
  }

  reminderEventSource = new EventSource(buildReminderStreamUrl(DEMO_USER_ID))
  reminderEventSource.onopen = () => {
    remindersStore.markConnected(true)
  }
  reminderEventSource.onmessage = (event) => {
    try {
      const reminder = JSON.parse(event.data)
      void processReminder(reminder)
    } catch (error) {
      remindersStore.markError(error instanceof Error ? error.message : '提醒消息解析失败')
    }
  }
  reminderEventSource.onerror = () => {
    remindersStore.markError('提醒推送连接失败，已切换为轮询兜底')
    reminderEventSource?.close()
    reminderEventSource = null
    if (!isPollingFallbackEnabled) {
      startReminderPolling()
    }
  }
}

const stopReminderPolling = () => {
  isPollingFallbackEnabled = false
  if (reminderEventSource) {
    reminderEventSource.close()
    reminderEventSource = null
  }
  remindersStore.markConnected(false)
  if (reminderPollTimer !== null) {
    window.clearTimeout(reminderPollTimer)
    reminderPollTimer = null
  }
  if (reminderAbortController) {
    reminderAbortController.abort()
    reminderAbortController = null
  }
  if (chatAbortController) {
    chatAbortController.abort()
    chatAbortController = null
  }
}

const triggerImagePicker = () => {
  clearComposerError()
  fileInputRef.value?.click()
}

const removeSelectedImage = (imageId) => {
  const targetImage = selectedImages.value.find((image) => image.id === imageId)
  if (!targetImage) {
    return
  }

  revokeImages([targetImage])
  selectedImages.value = selectedImages.value.filter((image) => image.id !== imageId)
  if (fileInputRef.value && selectedImages.value.length === 0) {
    fileInputRef.value.value = ''
  }
}

const handleImageSelection = (event) => {
  clearComposerError()
  const files = Array.from(event.target.files || [])
  if (files.length === 0) {
    return
  }

  const remainingSlots = MAX_CHAT_IMAGE_COUNT - selectedImages.value.length
  if (remainingSlots <= 0) {
    composerError.value = `最多只能上传${MAX_CHAT_IMAGE_COUNT}张图片`
    event.target.value = ''
    return
  }

  const nextImages = []
  files.slice(0, remainingSlots).forEach((file) => {
    if (!ALLOWED_IMAGE_TYPES.has(file.type)) {
      composerError.value = '仅支持 JPG、PNG、WEBP 图片'
      return
    }
    if (file.size > MAX_CHAT_IMAGE_BYTES) {
      composerError.value = '图片大小不能超过5MB'
      return
    }

    nextImages.push(buildPreviewImage(file))
  })

  if (files.length > remainingSlots && !composerError.value) {
    composerError.value = `最多只能上传${MAX_CHAT_IMAGE_COUNT}张图片`
  }

  selectedImages.value = [...selectedImages.value, ...nextImages]
  event.target.value = ''
}

const getErrorMessage = (error) => {
  if (error instanceof Error) {
    return error.message || '请求失败'
  }

  if (typeof error === 'string' && error.trim()) {
    return error
  }

  if (error && typeof error === 'object') {
    if (typeof error.message === 'string' && error.message.trim()) {
      return error.message
    }
    if (typeof error.detail === 'string' && error.detail.trim()) {
      return error.detail
    }
    if (error.data && typeof error.data === 'object') {
      return getErrorMessage(error.data)
    }
    if (error.originalError) {
      return getErrorMessage(error.originalError)
    }
  }

  return '请求失败'
}

const handleSend = async () => {
  if (!canSend.value) {
    return
  }

  clearComposerError()
  const userText = inputMessage.value.trim()
  if (userText.length > MAX_CHAT_MESSAGE_LENGTH) {
    composerError.value = `消息不能超过${MAX_CHAT_MESSAGE_LENGTH}个字符`
    return
  }

  isSending.value = true
  const imagesToSend = selectedImages.value.map((image) => image.file)
  const messageImages = cloneImagesForMessage(selectedImages.value)

  appendMessage({ role: 'user', content: userText, images: messageImages })
  inputMessage.value = ''
  resetSelectedImages()
  chatAbortController = new AbortController()

  try {
    const reply = await sendChatMessage({
      message: userText,
      images: imagesToSend,
      signal: chatAbortController.signal
    })

    appendMessage({
      role: reply.role || 'assistant',
      content: reply.content || '收到'
    })
    emitRefreshEvents(reply.refresh, reply)
  } catch (error) {
    if (error?.name !== 'AbortError') {
      appendMessage({
        role: 'assistant',
        content: `出错了：${getErrorMessage(error)}`
      })
    }
  } finally {
    chatAbortController = null
    isSending.value = false
  }
}

const handleInputKeyup = (event) => {
  if (event.isComposing) {
    return
  }

  if (event.key === 'Enter') {
    void handleSend()
  }
}

const handleDeviceSync = (payload) => {
  appendMessage({
    role: 'assistant',
    content: payload?.content || '已从 BLE GATT + GLP 演示血糖仪同步记录'
  })
  emitRefreshEvents(payload?.refresh || ['glucose', 'adherence', 'advice'])
}

onMounted(() => {
  isChatPanelMounted = true
  startReminderStream()
  scrollToBottom()
  eventBus.value.on('device:sync', handleDeviceSync)
})

onUnmounted(() => {
  isChatPanelMounted = false
  stopReminderPolling()
  eventBus.value.off('device:sync', handleDeviceSync)
  revokeImages(selectedImages.value)
  messages.value.forEach((message) => revokeImages(message.images))
})
</script>

<template>
  <div class="chat-panel">
    <div class="chat-header">
      <span class="avatar">🤖</span>
      <div class="header-text">
        <h3>智能助手</h3>
        <span class="status">在线</span>
      </div>
    </div>

    <div class="chat-messages" ref="messagesContainer">
      <div
        v-for="(msg, index) in messages"
        :key="index"
        :class="['message', msg.role]"
      >
        <div class="message-avatar">{{ msg.role === 'user' ? '👤' : '🤖' }}</div>
        <div class="message-content">
          <div v-if="msg.images?.length" class="message-images">
            <img
              v-for="image in msg.images"
              :key="image.id || image.previewUrl"
              :src="image.previewUrl"
              :alt="image.name || '已发送图片'"
              class="message-image"
            />
          </div>
          <div v-if="msg.content" class="message-text">{{ msg.content }}</div>
        </div>
      </div>
    </div>

    <div class="chat-composer">
      <div v-if="composerError" class="composer-error">{{ composerError }}</div>

      <div v-if="selectedImages.length" class="composer-preview-list">
        <div
          v-for="image in selectedImages"
          :key="image.id"
          class="composer-preview-item"
        >
          <img :src="image.previewUrl" :alt="image.name" class="composer-preview-image" />
          <button class="remove-image-btn" type="button" @click="removeSelectedImage(image.id)">×</button>
        </div>
      </div>

      <div class="chat-input">
        <input
          ref="fileInputRef"
          class="hidden-file-input"
          type="file"
          accept="image/png,image/jpeg,image/webp"
          multiple
          @change="handleImageSelection"
        />
        <button class="image-btn" type="button" @click="triggerImagePicker">图片</button>
        <input
          v-model="inputMessage"
          placeholder="输入消息..."
          :disabled="isSending"
          @input="clearComposerError"
          @keyup="handleInputKeyup"
        />
        <button class="send-btn" :disabled="!canSend" @click="handleSend">
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="22" y1="2" x2="11" y2="13"></line>
            <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-panel {
  height: 100%;
  min-height: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  padding: 0;
  background: #fff;
  overflow: hidden;
}

.chat-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid #e5e7eb;
  flex-shrink: 0;
}

.avatar {
  font-size: 28px;
}

.header-text h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #111;
}

.status {
  font-size: 11px;
  color: #22c55e;
}

.chat-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
  background: #f9fafb;
}

.message {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  align-items: flex-start;
}

.message.user {
  flex-direction: row-reverse;
}

.message-avatar {
  font-size: 20px;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff;
  border-radius: 50%;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
  flex-shrink: 0;
}

.message-content {
  max-width: 70%;
}

.message-images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.message-image {
  width: 156px;
  max-width: 100%;
  border-radius: 14px;
  border: 1px solid #dbe3f0;
  display: block;
}

.message-text {
  padding: 10px 14px;
  border-radius: 18px;
  font-size: 14px;
  line-height: 1.5;
  word-wrap: break-word;
  white-space: pre-wrap;
}

.message.user .message-text {
  background: #3b82f6;
  color: #fff;
  border-bottom-right-radius: 4px;
}

.message.assistant .message-text {
  background: #fff;
  color: #111;
  border: 1px solid #e5e7eb;
  border-bottom-left-radius: 4px;
}

.chat-composer {
  padding: 12px 16px;
  background: #fff;
  border-top: 1px solid #e5e7eb;
  flex-shrink: 0;
}

.composer-error {
  margin-bottom: 8px;
  color: #dc2626;
  font-size: 13px;
}

.composer-preview-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.composer-preview-item {
  position: relative;
}

.composer-preview-image {
  width: 72px;
  height: 72px;
  object-fit: cover;
  border-radius: 12px;
  border: 1px solid #dbe3f0;
}

.remove-image-btn {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 50%;
  background: rgba(17, 24, 39, 0.8);
  color: #fff;
  cursor: pointer;
  line-height: 1;
}

.chat-input {
  display: flex;
  align-items: center;
  gap: 10px;
}

.hidden-file-input {
  display: none;
}

.image-btn {
  padding: 0 14px;
  height: 38px;
  border: 1px solid #d1d5db;
  border-radius: 19px;
  background: #fff;
  color: #111827;
  cursor: pointer;
  flex-shrink: 0;
}

.chat-input input:not(.hidden-file-input) {
  flex: 1;
  padding: 10px 14px;
  border: 1px solid #d1d5db;
  border-radius: 20px;
  font-size: 14px;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
  background: #f9fafb;
  color: #111827;
  caret-color: #111827;
  color-scheme: light;
}

.chat-input input:not(.hidden-file-input)::placeholder {
  color: #6b7280;
}

.chat-input input:not(.hidden-file-input):focus {
  border-color: #3b82f6;
  background: #fff;
}

.send-btn {
  width: 38px;
  height: 38px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #3b82f6;
  color: #fff;
  border: none;
  border-radius: 50%;
  cursor: pointer;
  transition: background 0.2s, transform 0.2s;
}

.send-btn:hover:not(:disabled) {
  background: #2563eb;
  transform: scale(1.05);
}

.send-btn:disabled,
.image-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
