import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ResourceLabel from './ResourceLabel.vue'

describe('ResourceLabel', () => {
  it('shows the business meaning first and the technical identifier as subtitle', () => {
    const wrapper = mount(ResourceLabel, {
      props: {
        displayName: '股票日线行情',
        identifier: 'daily',
        description: '同步股票开高低收、成交量和成交额。',
      },
    })

    expect(wrapper.get('strong').text()).toBe('股票日线行情')
    expect(wrapper.get('code').text()).toBe('daily')
    expect(wrapper.get('span').text()).toContain('同步股票开高低收')
  })
})
