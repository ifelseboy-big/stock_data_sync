<script setup lang="ts">
import { ref } from 'vue'

import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceLabel from '@/components/ResourceLabel.vue'
import { useApiResource } from '@/composables/useApiResource'
import { getAlerts } from '@/modules/operations/api'
import type { AlertCategory, AlertLevel } from '@/modules/operations/contracts'
import { formatDateTime } from '@/modules/operations/presentation'

const page = ref(1)
const source = ref<'' | 'acquisition' | 'processing' | 'scheduler' | 'storage'>('')
const category = ref<AlertCategory | 'all'>('action_required')
const { data, loading, error, load } = useApiResource(() =>
  getAlerts({
    category: category.value,
    source: source.value || undefined,
    page: page.value,
    pageSize: 20,
  }),
)

function search() {
  page.value = 1
  void load()
}
const levelMap: Record<AlertLevel, { label: string; type: 'danger' | 'warning' | 'info' }> = {
  critical: { label: '严重', type: 'danger' },
  warning: { label: '警告', type: 'warning' },
  info: { label: '提示', type: 'info' },
}
const sourceMap = {
  acquisition: '采集',
  processing: '加工',
  scheduler: '调度',
  storage: '存储',
} as const
const categoryMap: Record<AlertCategory, { label: string; type: 'danger' | 'warning' | 'info' }> = {
  action_required: { label: '待处理', type: 'danger' },
  data_gap: { label: '数据缺口', type: 'warning' },
  quality: { label: '质量记录', type: 'info' },
}
</script>

<template>
  <section>
    <PageHeader
      title="告警中心"
      description="默认只显示需处理异常；已接受的数据缺口和自动隔离质量问题可切换查看。"
    >
      <template #actions><el-button :loading="loading" @click="load">刷新</el-button></template>
    </PageHeader>
    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" @submit.prevent="search">
        <el-form-item label="类型">
          <el-select v-model="category" style="width: 160px">
            <el-option label="待处理" value="action_required" />
            <el-option label="数据缺口" value="data_gap" />
            <el-option label="质量记录" value="quality" />
            <el-option label="全部" value="all" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源">
          <el-select v-model="source" clearable placeholder="全部" style="width: 160px">
            <el-option label="采集" value="acquisition" />
            <el-option label="加工" value="processing" />
            <el-option label="调度" value="scheduler" />
            <el-option label="存储" value="storage" />
          </el-select>
        </el-form-item>
        <el-form-item><el-button type="primary" native-type="submit">查询</el-button></el-form-item>
      </el-form>
    </el-card>
    <el-card shadow="never" class="panel-card panel-card--table">
      <DataState
        :loading="loading"
        :error="error"
        :empty="data?.items.length === 0"
        empty-title="当前没有告警"
        empty-description="新的异常会按发生时间显示。"
        @retry="load"
      >
        <el-table :data="data?.items ?? []" scrollbar-always-on>
          <el-table-column label="类型" width="105">
            <template #default="{ row }">
              <el-tag :type="categoryMap[row.category as AlertCategory].type">
                {{ categoryMap[row.category as AlertCategory].label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="级别" width="90">
            <template #default="{ row }">
              <el-tag :type="levelMap[row.level as AlertLevel].type">
                {{ levelMap[row.level as AlertLevel].label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="来源" min-width="130">
            <template #default="{ row }">{{
              sourceMap[row.source as keyof typeof sourceMap] ?? row.source
            }}</template>
          </el-table-column>
          <el-table-column label="对象" min-width="230">
            <template #default="{ row }">
              <ResourceLabel :display-name="row.taskDisplayName" :identifier="row.taskName" />
            </template>
          </el-table-column>
          <el-table-column prop="title" label="告警" min-width="140" />
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
        <div class="pagination-row">
          <el-pagination
            v-model:current-page="page"
            background
            layout="total, prev, pager, next"
            :total="data?.total ?? 0"
            :page-size="20"
            @current-change="load"
          />
        </div>
      </DataState>
    </el-card>
  </section>
</template>
