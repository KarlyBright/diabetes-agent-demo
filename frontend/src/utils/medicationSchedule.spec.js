import { describe, expect, it } from 'vitest'

import { formatFrequencyLabel } from './medicationSchedule'

describe('formatFrequencyLabel', () => {
  it('formats supported medication frequencies for display', () => {
    expect(formatFrequencyLabel('daily')).toBe('每日')
    expect(formatFrequencyLabel('每天')).toBe('每日')
    expect(formatFrequencyLabel('weekly:MON,WED,FRI')).toBe('每周一、三、五')
    expect(formatFrequencyLabel('interval:8h')).toBe('每 8 小时')
    expect(formatFrequencyLabel('cron:0 8,20 * * *')).toBe('Cron：0 8,20 * * *')
  })

  it('falls back to raw text for unknown frequencies', () => {
    expect(formatFrequencyLabel('custom')).toBe('custom')
    expect(formatFrequencyLabel('')).toBe('每日')
  })
})
