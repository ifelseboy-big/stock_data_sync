import ElementPlus from 'element-plus'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ProcessingView from './ProcessingView.vue'

const apiMocks = vi.hoisted(() => ({
  getProcessingQueue: vi.fn(),
  getRunRecords: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('@/modules/operations/api', () => ({
  getProcessingQueue: apiMocks.getProcessingQueue,
  getRunRecords: apiMocks.getRunRecords,
  retryAllFailedProcessingTasks: vi.fn(),
  runTaskCommand: vi.fn(),
}))

describe('ProcessingView live queue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getRunRecords.mockResolvedValue({
      generatedAt: '2026-07-23T14:00:00Z',
      items: [],
      page: 1,
      pageSize: 20,
      total: 0,
    })
    apiMocks.getProcessingQueue.mockImplementation(
      (query: { status?: string; pageSize?: number }) => {
        if (query.status === 'running') {
          return Promise.resolve({
            generatedAt: '2026-07-23T14:00:00Z',
            items: [
              {
                id: 'process-1',
                taskName: 'stock_daily.core',
                taskDisplayName: '股票日行情核心数据',
                taskDescription: '清洗并发布股票日行情。',
                batchCode: 'batch-1',
                dataCycle: '2026-07-23',
                priority: 'normal',
                queuePosition: 0,
                status: 'running',
                dependencyCount: 2,
                waitingSince: null,
                startedAt: '2026-07-23T13:59:50Z',
                durationMs: 10_000,
                blockedReason: null,
              },
            ],
            page: 1,
            pageSize: 8,
            total: 1,
          })
        }
        return Promise.resolve({
          generatedAt: '2026-07-23T14:00:00Z',
          items: [],
          page: 1,
          pageSize: 50,
          total: 12,
        })
      },
    )
  })

  it('loads running tasks from a dedicated live query', async () => {
    const wrapper = mount(ProcessingView, {
      global: {
        plugins: [ElementPlus],
        stubs: {
          AdminCommandDialog: true,
          DataState: { template: '<div><slot /></div>' },
          ResourceLabel: {
            props: ['displayName', 'identifier'],
            template: '<div>{{ displayName }}<code>{{ identifier }}</code></div>',
          },
          StatusTag: true,
        },
      },
    })
    await flushPromises()

    expect(apiMocks.getProcessingQueue).toHaveBeenCalledWith({
      status: 'running',
      page: 1,
      pageSize: 8,
    })
    expect(wrapper.text()).toContain('当前运行任务 1 个')
    expect(wrapper.text()).toContain('股票日行情核心数据')
    expect(wrapper.text()).toContain('自动刷新')
    expect(wrapper.text()).not.toContain('空闲，等待可执行任务进入队列')

    wrapper.unmount()
  })
})
