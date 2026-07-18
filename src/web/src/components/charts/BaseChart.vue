<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import VChart from 'vue-echarts'

import '@/plugins/echarts'

withDefaults(
  defineProps<{
    option: EChartsOption
    ariaLabel: string
    summary: string
    height?: string
  }>(),
  { height: '320px' },
)
</script>

<template>
  <figure class="chart-figure">
    <VChart
      class="chart-figure__canvas"
      :option="option"
      :style="{ height }"
      :aria-label="ariaLabel"
      role="img"
      autoresize
    />
    <figcaption class="visually-hidden">{{ summary }}</figcaption>
  </figure>
</template>
