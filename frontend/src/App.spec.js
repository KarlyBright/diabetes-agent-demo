import { mount, RouterLinkStub } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import App from './App.vue'

describe('App navigation', () => {
  it('shows only active top-level pages after removing history and settings', () => {
    const wrapper = mount(App, {
      global: {
        stubs: {
          RouterLink: RouterLinkStub,
          RouterView: true
        }
      }
    })

    const labels = wrapper.findAll('.nav-link').map((link) => link.text())

    expect(labels).toEqual(['仪表盘', '个人资料', '健康报告'])
    expect(wrapper.text()).not.toContain('历史记录')
    expect(wrapper.text()).not.toContain('设置')
  })
})
