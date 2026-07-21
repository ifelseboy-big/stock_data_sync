import ElementPlus from 'element-plus'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ProviderMonitoringView from './ProviderMonitoringView.vue'

const apiMocks = vi.hoisted(() => ({ getProviderMonitoring: vi.fn() }))

vi.mock('@/modules/operations/api', () => ({
  getProviderMonitoring: apiMocks.getProviderMonitoring,
}))

describe('ProviderMonitoringView', () => {
  beforeEach(() => {
    apiMocks.getProviderMonitoring.mockResolvedValue({
      generatedAt: '2026-07-21T10:00:00Z',
      quota: null,
      endpoints: [
        {
          endpoint: 'daily',
          endpointDisplayName: '股票日线行情',
          endpointDescription: '同步股票开高低收、成交量和成交额。',
          requestCountToday: 10,
          successRateToday: 1,
          p50DurationMs: 100,
          p95DurationMs: 200,
          throttledCountToday: 0,
          emptyResponseCountToday: 0,
          lastRequestedAt: '2026-07-21T10:00:00Z',
        },
      ],
    })
  })

  it('shows the endpoint meaning before its technical name', async () => {
    const wrapper = mount(ProviderMonitoringView, {
      global: {
        plugins: [ElementPlus],
        stubs: { DataState: { template: '<div><slot /></div>' } },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('股票日线行情')
    expect(wrapper.get('code').text()).toBe('daily')
  })
})
