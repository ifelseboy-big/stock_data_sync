import { describe, expect, it } from 'vitest'

import { formatDateTime } from './date'

describe('formatDateTime', () => {
  it('formats valid timestamps', () => {
    expect(formatDateTime('2026-07-18T09:30:00')).toBe('2026-07-18 09:30:00')
  })

  it('returns a placeholder for missing values', () => {
    expect(formatDateTime(null)).toBe('--')
  })
})
