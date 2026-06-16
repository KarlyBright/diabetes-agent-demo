import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { reactive } from 'vue'

import ReportView from './ReportView.vue'
import { summarizeGlucoseTrend } from '../api'

const glucoseStore = reactive({
  records: [],
  trend: { target: { min: 3.9, max: 10 }, points: [] },
  fetchRecent: vi.fn(),
  fetchTrend: vi.fn()
})

vi.mock('../stores/glucose', () => ({
  useGlucoseStore: () => glucoseStore
}))

vi.mock('../api', () => ({
  summarizeGlucoseTrend: vi.fn()
}))

const mountReportView = () =>
  mount(ReportView, {
    global: {
      stubs: {
        GlucoseTrendChart: {
          props: ['trend'],
          template: '<div class="trend-chart-stub">趋势点：{{ trend.points.length }}</div>'
        },
        ElCard: {
          template: '<section class="el-card"><header><slot name="header" /></header><div><slot /></div></section>'
        },
        ElEmpty: {
          props: ['description'],
          template: '<div class="empty-stub">{{ description }}</div>'
        },
        ElSkeleton: {
          template: '<div class="skeleton-stub">加载中</div>'
        },
        ElTable: {
          props: ['data'],
          template: '<table><tbody><tr v-for="(row, index) in data" :key="index"><slot :row="row" /></tr></tbody></table>'
        },
        ElTableColumn: true,
        ElButton: {
          props: ['loading', 'disabled'],
          emits: ['click'],
          template: '<button :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>'
        },
        ElAlert: {
          props: ['title'],
          template: '<div class="alert-stub">{{ title }}</div>'
        }
      }
    }
  })

describe('ReportView', () => {
  beforeEach(() => {
    glucoseStore.records = [
      {
        id: 1,
        value: 6.2,
        measure_type: 'fasting',
        source: 'manual',
        measure_time: '2026-05-20T08:00:00'
      }
    ]
    glucoseStore.trend = {
      target: { min: 3.9, max: 10 },
      points: [
        { measure_time: '2026-05-20T08:00:00', value: 6.2, measure_type: 'fasting' }
      ]
    }
    glucoseStore.fetchRecent.mockResolvedValue(glucoseStore.records)
    glucoseStore.fetchTrend.mockResolvedValue(glucoseStore.trend)
    summarizeGlucoseTrend.mockReset()
  })

  it('shows recent glucose and 7-day trend without adherence summary', async () => {
    const wrapper = mountReportView()
    await flushPromises()

    expect(wrapper.text()).toContain('近期血糖')
    expect(wrapper.text()).toContain('7 日血糖趋势')
    expect(wrapper.text()).toContain('近况血糖总结')
    expect(wrapper.text()).not.toContain('依从性摘要')
    expect(wrapper.text()).not.toContain('风险等级')
  })

  it('loads recent glucose and trend on mount', () => {
    mountReportView()

    expect(glucoseStore.fetchRecent).toHaveBeenCalledWith(1)
    expect(glucoseStore.fetchTrend).toHaveBeenCalledWith(1, 7)
  })

  it('generates a read-only glucose summary from the dedicated endpoint', async () => {
    summarizeGlucoseTrend.mockResolvedValue({ data: { summary: '近7日血糖整体平稳。' } })
    const wrapper = mountReportView()
    await flushPromises()

    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(summarizeGlucoseTrend).toHaveBeenCalledWith(1, 7)
    expect(wrapper.text()).toContain('近7日血糖整体平稳。')
  })

  it('shows summary generation errors', async () => {
    summarizeGlucoseTrend.mockRejectedValue(new Error('总结接口不可用'))
    const wrapper = mountReportView()
    await flushPromises()

    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('总结接口不可用')
  })

  it('shows report load errors without unhandled initial fetch failures', async () => {
    glucoseStore.fetchRecent.mockRejectedValue(new Error('血糖接口不可用'))
    const wrapper = mountReportView()
    await flushPromises()

    expect(wrapper.text()).toContain('血糖接口不可用')
  })

  it('disables summary generation when trend data is empty', async () => {
    glucoseStore.trend = { target: { min: 3.9, max: 10 }, points: [] }
    const wrapper = mountReportView()
    await flushPromises()

    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.find('button').attributes('disabled')).toBeDefined()
    expect(summarizeGlucoseTrend).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('暂无近7日血糖趋势数据')
  })
})
