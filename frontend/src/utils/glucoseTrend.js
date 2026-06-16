function formatAxisTime(value) {
  if (!value) {
    return ''
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return String(value).slice(5, 16).replace('T', ' ')
  }
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${month}-${day} ${hour}:${minute}`
}

const TYPE_LABELS = {
  fasting: '空腹',
  post_meal: '餐后',
  before_sleep: '睡前'
}

export function buildGlucoseTrendOption(payload = {}) {
  const target = payload.target || { min: 3.9, max: 10 }
  const points = [...(payload.points || [])].sort((left, right) => {
    return new Date(left.measure_time).getTime() - new Date(right.measure_time).getTime()
  })

  return {
    tooltip: {
      trigger: 'axis',
      formatter: (items) => {
        const item = Array.isArray(items) ? items[0] : items
        const raw = item?.data?.raw || {}
        const label = TYPE_LABELS[raw.measure_type] || raw.measure_type || '未知'
        return `${item?.axisValue || ''}<br/>${label}: ${raw.value ?? item?.value} mmol/L`
      }
    },
    grid: { left: 36, right: 16, top: 28, bottom: 32 },
    xAxis: {
      type: 'category',
      data: points.map((point) => formatAxisTime(point.measure_time))
    },
    yAxis: {
      type: 'value',
      min: 0,
      name: 'mmol/L'
    },
    series: [
      {
        name: '血糖',
        type: 'line',
        smooth: true,
        data: points.map((point) => ({
          value: point.value,
          raw: point,
          itemStyle: {
            color: point.value > target.max ? '#ef4444' : point.value < target.min ? '#f59e0b' : '#22c55e'
          }
        })),
        markArea: {
          silent: true,
          itemStyle: { color: 'rgba(34, 197, 94, 0.12)' },
          data: [[{ yAxis: target.min }, { yAxis: target.max }]]
        }
      }
    ]
  }
}
