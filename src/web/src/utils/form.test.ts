import { describe, expect, it, vi } from 'vitest'

import { validateForm } from './form'

describe('validateForm', () => {
  it('returns true when the form is valid', async () => {
    await expect(validateForm({ validate: vi.fn().mockResolvedValue(true) })).resolves.toBe(true)
  })

  it('turns Element Plus validation rejection into a normal false result', async () => {
    await expect(
      validateForm({ validate: vi.fn().mockRejectedValue({ reason: ['required'] }) }),
    ).resolves.toBe(false)
  })

  it('returns false when the form instance is unavailable', async () => {
    await expect(validateForm(undefined)).resolves.toBe(false)
  })
})
