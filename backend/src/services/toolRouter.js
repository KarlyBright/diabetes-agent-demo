import { addGlucoseRecordTool } from '../tools/glucoseTool.js'
import { analyzeMealTool } from '../tools/mealTool.js'
import { recordMedicationTakenTool } from '../tools/medicationTool.js'

export async function runTool(toolName, params, deps) {
  switch (toolName) {
    case 'add_glucose_record':
      return await addGlucoseRecordTool(params, deps)

    case 'analyze_meal':
      return await analyzeMealTool(params, deps)

    case 'record_medication_taken':
      return await recordMedicationTakenTool(params, deps)

    default:
      throw new Error(`未知 tool: ${toolName}`)
  }
}