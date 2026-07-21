import ElementPlus from 'element-plus'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ReleasesView from './ReleasesView.vue'

const apiMocks = vi.hoisted(() => ({
  getDatasetReleaseCoverage: vi.fn(),
  getDatasetReleases: vi.fn(),
  recoverReleaseGaps: vi.fn(),
}))

vi.mock('@/modules/operations/api', () => ({
  getDatasetReleaseCoverage: apiMocks.getDatasetReleaseCoverage,
  getDatasetReleases: apiMocks.getDatasetReleases,
  recoverReleaseGaps: apiMocks.recoverReleaseGaps,
}))

function localDate(value: Date) {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

describe('ReleasesView coverage recovery', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getDatasetReleases.mockResolvedValue({
      items: [
        {
          datasetName: 'stock_daily.core',
          datasetDisplayName: '股票核心日线',
          datasetDescription: '股票每日行情、复权因子和估值指标。',
          scopeType: 'DATE',
          scopeKey: '2026-07-21',
          businessDate: '2026-07-21',
          versionId: 'version-id',
          processId: 'process-id',
          processorVersion: '1',
          rowCount: 100,
          publishedAt: '2026-07-21T10:00:00Z',
        },
      ],
      page: 1,
      pageSize: 20,
      total: 1,
    })
    apiMocks.getDatasetReleaseCoverage.mockResolvedValue([
      {
        businessDate: '2026-07-18',
        expectedCount: 18,
        publishedCount: 17,
        coverageStatus: 'missing',
        missingDatasets: ['stock_top_list_daily'],
        missingDatasetDisplayNames: ['龙虎榜明细'],
      },
      {
        businessDate: '2026-07-17',
        expectedCount: 18,
        publishedCount: 18,
        coverageStatus: 'complete',
        missingDatasets: [],
        missingDatasetDisplayNames: [],
      },
      {
        businessDate: '2026-07-21',
        expectedCount: 18,
        publishedCount: 12,
        coverageStatus: 'pending',
        missingDatasets: ['stock_daily'],
        missingDatasetDisplayNames: ['日线行情'],
      },
    ])
    apiMocks.recoverReleaseGaps.mockResolvedValue({
      commandId: 'gap-command',
      result: {
        batchCount: 1,
        missingDateCount: 1,
        skippedActiveApiCount: 0,
      },
    })
  })

  function mountView() {
    return mount(ReleasesView, {
      global: {
        plugins: [ElementPlus],
        stubs: {
          AdminCommandDialog: {
            props: ['modelValue'],
            emits: ['submit'],
            template:
              '<button v-if="modelValue" class="confirm-gap" @click="$emit(\'submit\', { reason: \'补齐缺失\', idempotencyKey: \'gap-request-key\' })">确认</button>',
          },
          DataState: { template: '<div><slot /></div>' },
        },
      },
    })
  }

  it('applies convenient recent date ranges immediately', async () => {
    const wrapper = mountView()
    await flushPromises()

    const presetButton = wrapper.findAll('button').find((item) => item.text() === '近 7 天')
    expect(presetButton).toBeDefined()
    await presetButton!.trigger('click')
    await flushPromises()

    const end = new Date()
    const start = new Date(end)
    start.setDate(start.getDate() - 6)
    expect(apiMocks.getDatasetReleaseCoverage).toHaveBeenLastCalledWith({
      startDate: localDate(start),
      endDate: localDate(end),
    })
  })

  it('submits one command for all missing dates in the selected range', async () => {
    const wrapper = mountView()
    await flushPromises()

    const backfillButton = wrapper
      .findAll('button')
      .find((item) => item.text() === '回填全部缺失（1 日）')
    expect(backfillButton).toBeDefined()
    await backfillButton!.trigger('click')
    await wrapper.get('.confirm-gap').trigger('click')
    await flushPromises()

    const end = new Date()
    const start = new Date(end)
    start.setDate(start.getDate() - 29)
    expect(apiMocks.recoverReleaseGaps).toHaveBeenCalledWith(
      'backfill',
      {
        startDate: localDate(start),
        endDate: localDate(end),
        reason: '补齐缺失',
      },
      { idempotencyKey: 'gap-request-key' },
    )
  })

  it('filters the completeness table when a summary card is clicked', async () => {
    const wrapper = mountView()
    await flushPromises()

    const table = wrapper.get('[data-testid="coverage-table"]')
    expect(table.text()).toContain('2026-07-18')
    expect(table.text()).toContain('2026-07-17')
    expect(table.text()).toContain('2026-07-21')

    await wrapper.get('[data-testid="coverage-filter-complete"]').trigger('click')
    expect(table.text()).not.toContain('2026-07-18')
    expect(table.text()).toContain('2026-07-17')
    expect(table.text()).not.toContain('2026-07-21')
    expect(wrapper.get('[data-testid="coverage-filter-complete"]').attributes('aria-pressed')).toBe(
      'true',
    )

    await wrapper.get('[data-testid="coverage-filter-all"]').trigger('click')
    expect(table.text()).toContain('2026-07-18')
    expect(table.text()).toContain('2026-07-17')
    expect(table.text()).toContain('2026-07-21')
  })

  it('shows the dataset meaning before its technical name', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('股票核心日线')
    expect(wrapper.findAll('code').some((item) => item.text() === 'stock_daily.core')).toBe(true)
  })
})
