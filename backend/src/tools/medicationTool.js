export async function recordMedicationTakenTool(params, deps) {
  const { medicationService } = deps

  const payload = {
    user_id: params.user_id || 1,
    plan_id: params.plan_id,
    status: 'taken'
  }

  if (!payload.plan_id) {
    throw new Error('缺少 plan_id')
  }

  const result = await medicationService.recordTaken(payload)

  return {
    tool: 'record_medication_taken',
    success: true,
    data: result,
    refresh: ['medication', 'adherence', 'advice']
  }
}