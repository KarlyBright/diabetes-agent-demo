export async function addGlucoseRecordTool(params, deps) {
  const { glucoseService } = deps

  const payload = {
    user_id: params.user_id || 1,
    value: Number(params.value),
    measure_time: params.measure_time,
    measure_type: params.measure_type || 'fasting',
    source: params.source || 'agent'
  }

  if (!payload.value || !payload.measure_time) {
    throw new Error('缺少血糖记录必要参数')
  }

  const result = await glucoseService.addRecord(payload)

  return {
    tool: 'add_glucose_record',
    success: true,
    data: result,
    refresh: ['glucose', 'adherence', 'advice']
  }
}