import { API_BASE } from './config'

const DEFAULT_HEADERS = { 'Content-Type': 'application/json' }

async function parseJsonResponse(res) {
  const json = await res.json()
  if (!res.ok || (typeof json.code !== 'undefined' && json.code !== 0)) {
    throw new Error(json.detail || json.message || '请求失败')
  }
  return json
}

export async function getUserProfile(userId = 1) {
  const res = await fetch(`${API_BASE}/api/user/profile?user_id=${userId}`)
  return parseJsonResponse(res)
}

export async function saveUserProfile(data) {
  const res = await fetch(`${API_BASE}/api/user/profile`, {
    method: 'POST',
    headers: DEFAULT_HEADERS,
    body: JSON.stringify(data)
  })
  return parseJsonResponse(res)
}

export async function getRecentGlucose(userId = 1) {
  const res = await fetch(`${API_BASE}/api/glucose/recent?user_id=${userId}`)
  return parseJsonResponse(res)
}

export async function getGlucoseTrend(userId = 1, days = 7) {
  const res = await fetch(`${API_BASE}/api/glucose/trend?user_id=${userId}&days=${days}`)
  return parseJsonResponse(res)
}

export async function summarizeGlucoseTrend(userId = 1, days = 7) {
  const res = await fetch(`${API_BASE}/api/glucose/summary?user_id=${userId}&days=${days}`)
  return parseJsonResponse(res)
}

export async function addGlucoseRecord(data) {
  const res = await fetch(`${API_BASE}/api/glucose`, {
    method: 'POST',
    headers: DEFAULT_HEADERS,
    body: JSON.stringify(data)
  })
  return parseJsonResponse(res)
}

export async function getDeviceStatus() {
  const res = await fetch(`${API_BASE}/api/device/status`)
  return parseJsonResponse(res)
}

export async function connectDevice() {
  const res = await fetch(`${API_BASE}/api/device/connect`, {
    method: 'POST',
    headers: DEFAULT_HEADERS
  })
  return parseJsonResponse(res)
}

export async function disconnectDevice() {
  const res = await fetch(`${API_BASE}/api/device/disconnect`, {
    method: 'POST',
    headers: DEFAULT_HEADERS
  })
  return parseJsonResponse(res)
}

export async function mockSyncDevice() {
  const res = await fetch(`${API_BASE}/api/device/mock-sync`, {
    method: 'POST',
    headers: DEFAULT_HEADERS
  })
  return parseJsonResponse(res)
}

export async function getMedicationPlans(userId = 1) {
  const res = await fetch(`${API_BASE}/api/medication/plans?user_id=${userId}`)
  return res.json()
}

export async function getMedicationTaken(userId = 1) {
  const res = await fetch(`${API_BASE}/api/medication/taken?user_id=${userId}`)
  return res.json()
}

export async function recordMedicationTaken(data) {
  const res = await fetch(`${API_BASE}/api/medication/take`, {
    method: 'POST',
    headers: DEFAULT_HEADERS,
    body: JSON.stringify(data)
  })
  return res.json()
}

export async function getAdherenceAnalysis(userId = 1) {
  const res = await fetch(`${API_BASE}/api/adherence/analyze?user_id=${userId}`)
  return res.json()
}

export async function getDailyAdvice(userId = 1) {
  const res = await fetch(`${API_BASE}/api/advice/daily?user_id=${userId}`)
  return parseJsonResponse(res)
}

export async function analyzeMeal(data) {
  const res = await fetch(`${API_BASE}/api/meal/analyze`, {
    method: 'POST',
    headers: DEFAULT_HEADERS,
    body: JSON.stringify(data)
  })
  return parseJsonResponse(res)
}

export async function pollChatReminders(userId = 1, signal) {
  const res = await fetch(`${API_BASE}/api/agent/reminders?user_id=${userId}`, { signal })
  const json = await res.json()

  if (!res.ok || json.code !== 0) {
    throw new Error(json.detail || json.message || '请求失败')
  }

  return json.data || []
}

export function buildReminderStreamUrl(userId = 1) {
  return `${API_BASE}/api/agent/reminders/stream?user_id=${userId}`
}

export async function acknowledgeChatReminders(reminderIds, userId = 1, signal) {
  const res = await fetch(`${API_BASE}/api/agent/reminders/ack?user_id=${userId}`, {
    method: 'POST',
    headers: DEFAULT_HEADERS,
    body: JSON.stringify({ reminder_ids: reminderIds }),
    signal
  })
  const json = await res.json()

  if (!res.ok || json.code !== 0) {
    throw new Error(json.detail || json.message || '请求失败')
  }

  return json.data
}

export { sendChatMessage } from './chat'
