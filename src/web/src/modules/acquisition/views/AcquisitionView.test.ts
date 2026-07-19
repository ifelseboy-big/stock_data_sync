import ElementPlus from 'element-plus'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AcquisitionView from './AcquisitionView.vue'

const apiMocks = vi.hoisted(() => ({
  createBackfill: vi.fn(),
  getAcquisitionBatches: vi.fn(),
  getManualCommandOptions: vi.fn(),
}))

vi.mock('@/modules/operations/api', () => ({
  cancelAcquisitionBatch: vi.fn(),
  createBackfill: apiMocks.createBackfill,
  createRepair: vi.fn(),
  getAcquisitionBatches: apiMocks.getAcquisitionBatches,
  getManualCommandOptions: apiMocks.getManualCommandOptions,
  getRunRecords: vi.fn(),
  runTaskCommand: vi.fn(),
}))

describe('AcquisitionView manual backfill', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getAcquisitionBatches.mockResolvedValue({ items: [], page: 1, pageSize: 20, total: 0 })
    apiMocks.getManualCommandOptions.mockResolvedValue({
      acquisitionApis: [
        {
          apiName: 'daily',
          displayName: '日线行情',
          description: '股票日线行情',
          scheduleGroup: 'DAILY',
        },
      ],
      maxBackfillDays: 3660,
    })
    apiMocks.createBackfill.mockResolvedValue({ commandId: 'command-1', result: {} })
  })

  it('submits a valid backfill after clicking 创建命令', async () => {
    const wrapper = mount(AcquisitionView, {
      global: {
        plugins: [ElementPlus],
        stubs: {
          AdminCommandDialog: true,
          DataState: { template: '<div><slot /></div>' },
          StatusTag: true,
        },
      },
    })
    await flushPromises()

    const historyButton = wrapper.findAll('button').find((item) => item.text() === '历史回填')
    expect(historyButton).toBeDefined()
    await historyButton!.trigger('click')

    const view = wrapper.vm as unknown as {
      commandForm: {
        startDate: string
        endDate: string
        apiNames: string[]
        reason: string
      }
    }
    view.commandForm.startDate = '2026-07-01'
    view.commandForm.endDate = '2026-07-02'
    view.commandForm.apiNames = ['daily']
    view.commandForm.reason = '回填'
    await nextTick()

    const createButton = wrapper.findAll('button').find((item) => item.text() === '创建命令')
    expect(createButton).toBeDefined()
    await createButton!.trigger('click')
    await flushPromises()

    expect(apiMocks.createBackfill).toHaveBeenCalledWith(
      {
        startDate: '2026-07-01',
        endDate: '2026-07-02',
        apiNames: ['daily'],
        reason: '回填',
      },
      { idempotencyKey: expect.any(String) },
    )
  })
})
