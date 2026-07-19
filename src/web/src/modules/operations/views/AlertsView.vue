<script setup lang="ts">
import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import { useApiResource } from '@/composables/useApiResource'
import { getAlerts } from '@/modules/operations/api'
import type { AlertLevel } from '@/modules/operations/contracts'
import { formatDateTime } from '@/modules/operations/presentation'

const { data, loading, error, load } = useApiResource(getAlerts)
const levelMap: Record<AlertLevel, { label: string; type: 'danger' | 'warning' | 'info' }> = {
  critical: { label: '严重', type: 'danger' },
  warning: { label: '警告', type: 'warning' },
  info: { label: '提示', type: 'info' },
}
</script>

<template>
  <section>
    <PageHeader title="告警中心" description="集中查看任务、依赖、接口和系统异常。">
      <template #actions><el-button :loading="loading" @click="load">刷新</el-button></template>
    </PageHeader>
    <el-card shadow="never" class="panel-card panel-card--table">
      <DataState
        :loading="loading"
        :error="error"
        :empty="data?.length === 0"
        empty-title="当前没有告警"
        empty-description="新的异常会按发生时间显示。"
        @retry="load"
      >
        <el-table :data="data ?? []">
          <el-table-column label="级别" width="90">
            <template #default="{ row }">
              <el-tag :type="levelMap[row.level as AlertLevel].type">
                {{ levelMap[row.level as AlertLevel].label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="source" label="来源" min-width="130" />
          <el-table-column prop="title" label="告警" min-width="190" />
          <el-table-column prop="detail" label="详情" min-width="280" show-overflow-tooltip />
          <el-table-column label="发生时间" min-width="170">
            <template #default="{ row }">{{ formatDateTime(row.occurredAt) }}</template>
          </el-table-column>
          <el-table-column label="确认状态" width="110">
            <template #default="{ row }">
              <el-tag :type="row.acknowledgedAt ? 'success' : 'warning'">
                {{ row.acknowledgedAt ? '已确认' : '未确认' }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </DataState>
    </el-card>
  </section>
</template>
