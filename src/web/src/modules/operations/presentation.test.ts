import { describe, expect, it } from 'vitest'

import { formatDuration, formatPercent, formatPriority } from './presentation'

describe('operations presentation', () => {
  it('keeps missing ratios distinct from zero percent', () => {
    expect(formatPercent(null)).toBe('--')
    expect(formatPercent(0)).toBe('0.0%')
    expect(formatPercent(0.9876)).toBe('98.8%')
  })

  it('formats task and interface durations by scale', () => {
    expect(formatDuration(null)).toBe('--')
    expect(formatDuration(480)).toBe('480 ms')
    expect(formatDuration(12_450)).toBe('12.4 s')
    expect(formatDuration(125_000)).toBe('2m 5s')
  })

  it('uses the agreed processing priority labels', () => {
    expect(formatPriority('current_normal')).toBe('当期正常')
    expect(formatPriority('auto_supplement')).toBe('自动补采')
    expect(formatPriority('manual_rerun')).toBe('人工重跑')
    expect(formatPriority('historical')).toBe('历史补数')
  })
})
