<script setup lang="ts">
import { ref } from 'vue'

import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useApiResource } from '@/composables/useApiResource'
import { getRunRecords } from '@/modules/operations/api'
import type { ExecutionStatus } from '@/modules/operations/contracts'
import { formatDateTime, formatDuration } from '@/modules/operations/presentation'

const runType = ref<'' | 'acquisition' | 'processing'>('')
const status = ref<ExecutionStatus | ''>('')
const page = ref(1)
const { data, loading, error, load } = useApiResource(() =>
  getRunRecords({
    runType: runType.value || undefined,
    status: status.value || undefined,
    page: page.value,
    pageSize: 20,
  }),
)

function search() {
  page.value = 1
  void load()
}
</script>

<template>
  <section>
    <PageHeader title="运行记录" description="查询采集与加工任务每次执行的状态、重试和耗时。">
      <template #actions><el-button :loading="loading" @click="load">刷新</el-button></template>
    </PageHeader>
    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" @submit.prevent="search">
        <el-form-item label="任务类型">
          <el-select v-model="runType" placeholder="全部类型" clearable style="width: 140px">
            <el-option label="采集任务" value="acquisition" />
            <el-option label="加工任务" value="processing" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="status" placeholder="全部状态" clearable style="width: 140px">
            <el-option label="运行中" value="running" />
            <el-option label="等待重试" value="waiting_retry" />
            <el-option label="成功" value="succeeded" />
            <el-option label="失败" value="failed" />
            <el-option label="阻塞" value="blocked" />
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
        empty-title="暂无运行记录"
        empty-description="任务产生执行实例后会显示在这里。"
        @retry="load"
      >
        <el-table :data="data?.items ?? []">
          <el-table-column label="类型" width="100">
            <template #default="{ row }">{{
              row.runType === 'acquisition' ? '采集' : '加工'
            }}</template>
          </el-table-column>
          <el-table-column prop="taskName" label="任务" min-width="180" />
          <el-table-column prop="batchCode" label="批次" min-width="170" />
          <el-table-column prop="dataCycle" label="数据周期" min-width="120" />
          <el-table-column label="状态" width="110">
            <template #default="{ row }"><StatusTag :status="row.status" /></template>
          </el-table-column>
          <el-table-column prop="attempt" label="尝试次数" width="100" />
          <el-table-column label="耗时" width="110">
            <template #default="{ row }">{{ formatDuration(row.durationMs) }}</template>
          </el-table-column>
          <el-table-column label="开始时间" min-width="170">
            <template #default="{ row }">{{ formatDateTime(row.startedAt) }}</template>
          </el-table-column>
          <el-table-column
            prop="errorSummary"
            label="错误摘要"
            min-width="220"
            show-overflow-tooltip
          />
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
