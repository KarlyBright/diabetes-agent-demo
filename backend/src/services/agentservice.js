import { runTool } from './toolRouter.js'

function parseIntent(message) {
  const text = message.trim()

  if (text.includes('血糖')) {
    const valueMatch = text.match(/(\d+(\.\d+)?)/)
    if (valueMatch) {
      return {
        action: 'tool',
        toolName: 'add_glucose_record',
        params: {
          user_id: 1,
          value: valueMatch[1],
          measure_time: new Date().toISOString(),
          measure_type: 'fasting',
          source: 'agent'
        }
      }
    }
  }

  if (text.includes('吃了') || text.includes('早餐') || text.includes('午餐') || text.includes('晚餐')) {
    return {
      action: 'tool',
      toolName: 'analyze_meal',
      params: {
        user_id: 1,
        meal_text: text
      }
    }
  }

  if (text.includes('服药') || text.includes('吃药')) {
    const planIdMatch = text.match(/(\d+)/)
    if (planIdMatch) {
      return {
        action: 'tool',
        toolName: 'record_medication_taken',
        params: {
          user_id: 1,
          plan_id: Number(planIdMatch[1])
        }
      }
    }
  }

  return {
    action: 'reply',
    reply: '我可以帮你记录血糖、分析饮食、登记服药。你可以直接告诉我，例如：“我刚测血糖 7.2” 或 “早餐吃了两个馒头一杯豆浆”。'
  }
}

export async function handleAgentChat(message, deps) {
  const intent = parseIntent(message)

  if (intent.action === 'reply') {
    return {
      role: 'assistant',
      content: intent.reply,
      refresh: []
    }
  }

  const toolResult = await runTool(intent.toolName, intent.params, deps)

  let content = '操作已完成。'

  if (toolResult.tool === 'add_glucose_record') {
    content = `已帮你记录血糖 ${intent.params.value} mmol/L。`
  }

  if (toolResult.tool === 'analyze_meal') {
    const risk = toolResult.data?.risk_level || 'low'
    const score = toolResult.data?.score ?? '-'
    content = `我已经帮你分析这餐。风险等级：${risk}，评分：${score}。你也可以查看下方饮食分析卡片。`
  }

  if (toolResult.tool === 'record_medication_taken') {
    content = '已帮你登记本次服药。'
  }

  return {
    role: 'assistant',
    content,
    refresh: toolResult.refresh || []
  }
}