import { beforeEach, describe, expect, it, vi } from 'vitest'

import { http } from '@/api/http'

import { createRepair } from './api'

vi.mock('@/api/http', () => ({
  http: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

describe('operations command authentication', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('loads the configured admin token and sends it automatically', async () => {
    vi.mocked(http.get).mockResolvedValueOnce({
      data: { admin_api_token: 'configured-admin-token' },
    })
    vi.mocked(http.post).mockResolvedValueOnce({ data: { commandId: 'command-1' } })

    await createRepair(
      {
        businessDate: '2026-07-19',
        apiNames: ['daily'],
        reason: '验证自动鉴权',
      },
      { idempotencyKey: 'request-1' },
    )

    expect(http.get).toHaveBeenCalledWith('/system/admin-config')
    expect(http.post).toHaveBeenCalledWith('/operations/commands/repairs', expect.any(Object), {
      headers: {
        Authorization: 'Bearer configured-admin-token',
        'Idempotency-Key': 'request-1',
      },
    })
  })
})
