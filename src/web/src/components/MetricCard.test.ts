import ElementPlus from 'element-plus'
import { Warning } from '@element-plus/icons-vue'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import MetricCard from './MetricCard.vue'

describe('MetricCard', () => {
  it('renders an actionable link when a destination is provided', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/dependencies', component: { template: '<div />' } },
      ],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(MetricCard, {
      props: {
        label: '阻塞任务',
        value: 3,
        note: '必要依赖尚未就绪',
        icon: Warning,
        tone: 'danger',
        to: { path: '/dependencies', query: { readiness: 'blocked' } },
        actionLabel: '查看阻塞原因',
      },
      global: { plugins: [ElementPlus, router] },
    })

    const link = wrapper.get('a')
    expect(link.attributes('href')).toBe('/dependencies?readiness=blocked')
    expect(link.attributes('aria-label')).toBe('阻塞任务，查看阻塞原因')
    expect(link.text()).toContain('查看阻塞原因')
  })
})
