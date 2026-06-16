import { API_BASE } from './config'

function formatErrorDetail(detail) {
  if (typeof detail === 'string' && detail.trim()) {
    return detail
  }

  if (Array.isArray(detail)) {
    return detail
      .map((item) => formatErrorDetail(item))
      .filter(Boolean)
      .join('；') || '请求失败'
  }

  if (detail && typeof detail === 'object') {
    if (typeof detail.message === 'string' && detail.message.trim()) {
      return detail.message
    }
    if (typeof detail.msg === 'string' && detail.msg.trim()) {
      return detail.msg
    }
    if (detail.data && typeof detail.data === 'object') {
      return formatErrorDetail(detail.data)
    }
  }

  return '请求失败'
}

export async function sendChatMessage({ message = '', images = [], signal } = {}) {
  const hasImages = images.length > 0
  const requestInit = hasImages
    ? (() => {
        const formData = new FormData()
        formData.append('message', message)
        images.forEach((image) => {
          formData.append('images', image)
        })
        return {
          method: 'POST',
          body: formData,
          signal
        }
      })()
    : {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ message }),
        signal
      }

  const res = await fetch(`${API_BASE}/api/agent/chat`, requestInit)
  const rawText = await res.text()
  let json = null

  try {
    json = rawText ? JSON.parse(rawText) : null
  } catch {
    json = null
  }

  if (!res.ok || json?.code !== 0 || !json?.data) {
    throw new Error(formatErrorDetail(json?.detail || json?.message || rawText))
  }

  return json.data
}
