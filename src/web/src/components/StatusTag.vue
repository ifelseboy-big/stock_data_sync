<script setup lang="ts">
import { computed } from 'vue'

import type { ExecutionStatus } from '@/modules/operations/contracts'

const props = defineProps<{ status: ExecutionStatus }>()

const statusMap: Record<
  ExecutionStatus,
  { label: string; type: 'info' | 'primary' | 'success' | 'warning' | 'danger' }
> = {
  pending: { label: '待执行', type: 'info' },
  running: { label: '运行中', type: 'primary' },
  waiting_retry: { label: '等待重试', type: 'warning' },
  succeeded: { label: '成功', type: 'success' },
  partial_failed: { label: '部分失败', type: 'warning' },
  failed: { label: '失败', type: 'danger' },
  blocked: { label: '已阻塞', type: 'danger' },
  closed: { label: '已关闭', type: 'info' },
}

const display = computed(() => statusMap[props.status])
</script>

<template>
  <el-tag :type="display.type" effect="light">{{ display.label }}</el-tag>
</template>
