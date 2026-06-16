import { describe, expect, it } from 'vitest'

import { buildGlucoseTrendOption } from './glucoseTrend'

describe('buildGlucoseTrendOption', () => {
  it('builds an ECharts option with target range and sorted points', () => {
    const option = buildGlucoseTrendOption({
      target: { min: 3.9, max: 10 },
      points: [
        { measure_time: '2026-05-12T08:00:00', value: 7.2, measure_type: 'post_meal' },
        { measure_time: '2026-05-11T07:00:00', value: 5.8, measure_type: 'fasting' }
      ]
    })

    expect(option.xAxis.data).toEqual(['05-11 07:00', '05-12 08:00'])
    expect(option.series[0].data.map((item) => item.value)).toEqual([5.8, 7.2])
    expect(option.series[0].markArea.data[0]).toEqual([{ yAxis: 3.9 }, { yAxis: 10 }])
  })
})
