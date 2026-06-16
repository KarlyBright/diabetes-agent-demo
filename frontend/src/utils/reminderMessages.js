export function appendUniqueReminderMessage({ messages, seenReminderIds, reminder }) {
  const reminderId = reminder?.reminder_id
  if (!reminderId || seenReminderIds.has(reminderId)) {
    return messages
  }

  return [
    ...messages,
    {
      role: reminder.role || 'assistant',
      content: reminder.content || '收到新的用药提醒',
      images: []
    }
  ]
}
