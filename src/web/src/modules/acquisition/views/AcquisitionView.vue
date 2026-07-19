<script setup lang="ts">
import { ref } from 'vue'

import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useApiResource } from '@/composables/useApiResource'
import { getAcquisitionBatches } from '@/modules/operations/api'
import type { ExecutionStatus } from '@/modules/operations/contracts'
import { formatDateTime, formatDuration } from '@/modules/operations/presentation'

const status = ref<ExecutionStatus | ''>('')
const dataCycle = ref('')
const page = ref(1)

const { data, loading, error, load } = useApiResource(() =>
  getAcquisitionBatches({
    status: status.value || undefined,
    dataCycle: dataCycle.value || undefined,
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
    <PageHeader title="采集运行" description="按批次观察原始数据采集、失败重试和批次关闭结果。">
      <template #actions><el-button :loading="loading" @click="load">刷新</el-button></template>
    </PageHeader>

    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" @submit.prevent="search">
        <el-form-item label="状态">
          <el-select v-model="status" placeholder="全部状态" clearable style="width: 150px">
            <el-option label="运行中" value="running" />
            <el-option label="等待重试" value="waiting_retry" />
            <el-option label="成功" value="succeeded" />
            <el-option label="部分失败" value="partial_failed" />
            <el-option label="失败" value="failed" />
            <el-option label="已关闭" value="closed" />
          </el-select>
        </el-form-item>
        <el-form-item label="数据周期">
          <el-input v-model.trim="dataCycle" placeholder="如 2026-07-18" clearable />
        </el-form-item>
        <el-form-item><el-button type="primary" native-type="submit">查询</el-button></el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="panel-card panel-card--table">
      <DataState
        :loading="loading"
        :error="error"
        :empty="data?.items.length === 0"
        empty-title="暂无采集批次"
        empty-description="采集调度产生批次后会显示在这里。"
        @retry="load"
      >
        <el-table :data="data?.items ?? []">
          <el-table-column prop="batchCode" label="批次" min-width="180" fixed="left" />
          <el-table-column prop="themeName" label="业务主题" min-width="150" />
          <el-table-column prop="dataCycle" label="数据周期" min-width="120" />
          <el-table-column label="类型" width="110">
            <template #default="{ row }">
              {{
                row.batchType === 'auto_supplement'
                  ? '自动补采'
                  : row.batchType === 'manual'
                    ? '人工触发'
                    : '正常调度'
              }}
            </template>
          </el-table-column>
          <el-table-column label="任务进度" min-width="170">
            <template #default="{ row }">
              <span class="numeric">{{ row.succeededTaskCount }} / {{ row.taskCount }}</span>
              <span v-if="row.failedTaskCount" class="text-danger">
                · 失败 {{ row.failedTaskCount }}</span
              >
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }"><StatusTag :status="row.status" /></template>
          </el-table-column>
          <el-table-column label="耗时" width="110">
            <template #default="{ row }">{{ formatDuration(row.durationMs) }}</template>
          </el-table-column>
          <el-table-column label="关闭时间" min-width="170">
            <template #default="{ row }">{{ formatDateTime(row.closedAt) }}</template>
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
