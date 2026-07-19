import { describe, expect, it } from 'vitest'

import { createIdempotencyKey } from './idempotency'

describe('idempotency key', () => {
  it('works without crypto.randomUUID and returns distinct UUID v4 values', () => {
    const first = createIdempotencyKey()
    const second = createIdempotencyKey()

    expect(first).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(second).not.toBe(first)
  })
})
