const WEEKDAY_LABELS = {
  MON: '一',
  TUE: '二',
  WED: '三',
  THU: '四',
  FRI: '五',
  SAT: '六',
  SUN: '日'
}

export function formatFrequencyLabel(frequency) {
  const normalized = (frequency || 'daily').trim()
  const lowered = normalized.toLowerCase()

  if (!normalized || lowered === 'daily' || normalized === '每天' || normalized === '每日') {
    return '每日'
  }

  if (lowered.startsWith('weekly:')) {
    const dayText = normalized
      .split(':', 2)[1]
      .split(',')
      .map((day) => WEEKDAY_LABELS[day.trim().toUpperCase()])
      .filter(Boolean)
      .join('、')
    return dayText ? `每周${dayText}` : normalized
  }

  if (lowered.startsWith('interval:')) {
    const match = lowered.match(/^interval:(\d+)([hd])$/)
    if (!match) {
      return normalized
    }
    const unitLabel = match[2] === 'h' ? '小时' : '天'
    return `每 ${match[1]} ${unitLabel}`
  }

  if (lowered.startsWith('cron:')) {
    return `Cron：${normalized.split(':').slice(1).join(':').trim()}`
  }

  return normalized
}
