<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { LineChart } from 'echarts/charts'
import { GridComponent, MarkAreaComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

import { buildGlucoseTrendOption } from '../utils/glucoseTrend'

use([LineChart, GridComponent, MarkAreaComponent, TooltipComponent, CanvasRenderer])

const props = defineProps({
  trend: {
    type: Object,
    default: () => ({ target: { min: 3.9, max: 10 }, points: [] })
  }
})

const chartEl = ref(null)
const hasTrendPoints = computed(() => Boolean(props.trend?.points?.length))
let chart = null

const disposeChart = () => {
  chart?.dispose()
  chart = null
}

const renderChart = async () => {
  await nextTick()
  if (!chartEl.value || !hasTrendPoints.value) {
    disposeChart()
    return
  }
  if (!chart) {
    chart = init(chartEl.value)
  }
  chart.setOption(buildGlucoseTrendOption(props.trend), true)
}

const resizeChart = () => {
  chart?.resize()
}

watch(() => props.trend, renderChart, { deep: true })

onMounted(() => {
  void renderChart()
  window.addEventListener('resize', resizeChart)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeChart)
  disposeChart()
})
</script>

<template>
  <div class="trend-card">
    <div v-if="!hasTrendPoints" class="empty-trend">暂无趋势数据</div>
    <div ref="chartEl" v-show="hasTrendPoints" class="chart"></div>
  </div>
</template>

<style scoped>
.trend-card {
  min-height: 220px;
}

.chart {
  width: 100%;
  height: 220px;
}

.empty-trend {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 220px;
  color: #94a3b8;
  font-size: 13px;
  background: #f8fafc;
  border: 1px dashed #cbd5e1;
  border-radius: 10px;
}
</style>
