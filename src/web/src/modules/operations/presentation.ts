import dayjs from 'dayjs'

import type { PriorityLevel } from './contracts'

export const priorityLabels: Record<PriorityLevel, string> = {
  current_normal: '当期正常',
  auto_supplement: '自动补采',
  manual_rerun: '人工重跑',
  historical: '历史补数',
}

export function formatPriority(value: PriorityLevel): string {
  return priorityLabels[value]
}

export function formatPercent(value: number | null | undefined): string {
  return value == null ? '--' : `${(value * 100).toFixed(1)}%`
}

export function formatDuration(value: number | null | undefined): string {
  if (value == null) return '--'
  if (value < 1_000) return `${value} ms`
  if (value < 60_000) return `${(value / 1_000).toFixed(1)} s`
  return `${Math.floor(value / 60_000)}m ${Math.round((value % 60_000) / 1_000)}s`
}

export function formatDateTime(value: string | null | undefined): string {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '--'
}
