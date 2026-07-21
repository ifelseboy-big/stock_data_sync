import { beforeEach, describe, expect, it, vi } from 'vitest'

import { http } from '@/api/http'

import {
  createRepair,
  getDatasetReleaseCoverage,
  retryAllFailedCollectionTasks,
  retryAllFailedProcessingTasks,
  retryFailedCollectionTasks,
} from './api'

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

describe('release coverage query', () => {
  it('passes the selected date range to the API', async () => {
    vi.mocked(http.get).mockResolvedValueOnce({ data: [] })

    await getDatasetReleaseCoverage({
      startDate: '2026-07-01',
      endDate: '2026-07-19',
    })

    expect(http.get).toHaveBeenCalledWith('/operations/release-coverage', {
      params: {
        startDate: '2026-07-01',
        endDate: '2026-07-19',
      },
    })
  })
})

describe('bulk retry commands', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('uses dedicated collection and processing bulk endpoints', async () => {
    vi.mocked(http.post).mockResolvedValue({ data: { commandId: 'bulk-command' } })

    await retryFailedCollectionTasks(
      'batch-1',
      { reason: '批量重试采集' },
      { idempotencyKey: 'bulk-collection-request' },
    )
    await retryAllFailedCollectionTasks(
      { reason: '全局重试采集' },
      { idempotencyKey: 'global-collection-request' },
    )
    await retryAllFailedProcessingTasks(
      { reason: '批量重试加工' },
      { idempotencyKey: 'bulk-processing-request' },
    )

    expect(http.post).toHaveBeenNthCalledWith(
      1,
      '/operations/commands/acquisition-batches/batch-1/retry-failed-tasks',
      { reason: '批量重试采集' },
      expect.objectContaining({ headers: expect.any(Object) }),
    )
    expect(http.post).toHaveBeenNthCalledWith(
      2,
      '/operations/commands/collection-tasks/retry-all-failed',
      { reason: '全局重试采集' },
      expect.objectContaining({ headers: expect.any(Object) }),
    )
    expect(http.post).toHaveBeenNthCalledWith(
      3,
      '/operations/commands/processing-tasks/retry-all-failed',
      { reason: '批量重试加工' },
      expect.objectContaining({ headers: expect.any(Object) }),
    )
  })
})
