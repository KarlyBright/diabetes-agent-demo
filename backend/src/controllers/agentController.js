import { handleAgentChat } from '../services/agentService.js'

// 这里的 deps 先手动注入
import * as glucoseService from '../services/glucoseService.js'
import * as mealService from '../services/mealService.js'
import * as medicationService from '../services/medicationService.js'

export async function chatWithAgent(req, res) {
  try {
    const { message } = req.body

    if (!message || !message.trim()) {
      return res.status(400).json({ message: 'message 不能为空' })
    }

    const deps = {
      glucoseService,
      mealService,
      medicationService
    }

    const result = await handleAgentChat(message, deps)

    res.json({
      code: 0,
      data: result
    })
  } catch (error) {
    console.error('agent chat error:', error)
    res.status(500).json({
      code: 500,
      message: error.message || 'agent 调用失败'
    })
  }
}