export async function analyzeMealTool(params, deps) {
  const { mealService } = deps

  const payload = {
    user_id: params.user_id || 1,
    meal_text: params.meal_text || ''
  }

  if (!payload.meal_text.trim()) {
    throw new Error('meal_text 不能为空')
  }

  const result = await mealService.analyzeMeal(payload)

  return {
    tool: 'analyze_meal',
    success: true,
    data: result,
    refresh: ['meal', 'adherence', 'advice']
  }
}