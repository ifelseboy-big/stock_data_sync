<script setup lang="ts">
import { Operation } from '@element-plus/icons-vue'

import PagePlaceholder from '@/components/PagePlaceholder.vue'
</script>

<template>
  <PagePlaceholder
    title="运行记录"
    description="任务运行、接口调用和耗时明细将在需求确认后实现。"
    :icon="Operation"
  />
</template>
