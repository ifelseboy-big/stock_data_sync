import dayjs from 'dayjs'

export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return '--'

  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm:ss') : '--'
}
