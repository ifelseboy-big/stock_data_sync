<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'

import AdminCommandDialog from '@/components/AdminCommandDialog.vue'
import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceLabel from '@/components/ResourceLabel.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useApiResource } from '@/composables/useApiResource'
import { getRunRecords, runTaskCommand } from '@/modules/operations/api'
import type { ExecutionStatus, RunRecordItem, TaskTransition } from '@/modules/operations/contracts'
import { formatDateTime, formatDuration } from '@/modules/operations/presentation'

const runType = ref<'' | 'acquisition' | 'processing'>('')
const status = ref<ExecutionStatus | ''>('')
const page = ref(1)
const commandTarget = ref<{ row: RunRecordItem; transition: TaskTransition } | null>(null)
const commandLoading = ref(false)
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

const commandTitle = computed(() => {
  const target = commandTarget.value
  if (!target) return ''
  const verb = { retry: '重试', skip: '跳过', cancel: '取消' }[target.transition]
  return `${verb}${target.row.runType === 'acquisition' ? '采集' : '加工'}任务`
})

function canRetry(value: unknown) {
  const row = value as RunRecordItem
  return row.runType === 'acquisition'
    ? row.status === 'failed'
    : ['failed', 'blocked', 'waiting_retry'].includes(row.status)
}

function canStop(value: unknown) {
  const row = value as RunRecordItem
  return ['waiting_dependency', 'pending', 'waiting_retry', 'blocked'].includes(row.status)
}

function openCommand(value: unknown, transition: TaskTransition) {
  commandTarget.value = { row: value as RunRecordItem, transition }
}

async function submitCommand(value: { reason: string; idempotencyKey: string }) {
  const target = commandTarget.value
  if (!target) return
  commandLoading.value = true
  try {
    await runTaskCommand(
      target.row.runType,
      target.row.id,
      target.transition,
      { reason: value.reason },
      value,
    )
    ElMessage.success('人工命令已进入任务队列')
    commandTarget.value = null
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '人工命令提交失败')
  } finally {
    commandLoading.value = false
  }
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
            <el-option label="等待依赖" value="waiting_dependency" />
            <el-option label="待执行" value="pending" />
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
        <el-table :data="data?.items ?? []" scrollbar-always-on>
          <el-table-column label="类型" width="100">
            <template #default="{ row }">{{
              row.runType === 'acquisition' ? '采集' : '加工'
            }}</template>
          </el-table-column>
          <el-table-column label="任务" min-width="270">
            <template #default="{ row }">
              <ResourceLabel
                :display-name="row.taskDisplayName"
                :identifier="row.taskName"
                :description="row.taskDescription"
              />
            </template>
          </el-table-column>
          <el-table-column prop="scopeKey" label="任务范围" min-width="220" show-overflow-tooltip />
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
          <el-table-column label="人工操作" width="190" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="canRetry(row)"
                size="small"
                link
                type="primary"
                @click="openCommand(row, 'retry')"
              >
                重试
              </el-button>
              <el-button v-if="canStop(row)" size="small" link @click="openCommand(row, 'skip')">
                跳过
              </el-button>
              <el-button
                v-if="canStop(row)"
                size="small"
                link
                type="danger"
                @click="openCommand(row, 'cancel')"
              >
                取消
              </el-button>
              <span v-if="!canRetry(row) && !canStop(row)">--</span>
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

    <AdminCommandDialog
      :model-value="commandTarget !== null"
      :title="commandTitle"
      :description="`目标任务：${commandTarget?.row.taskName ?? ''}。命令只修改任务队列，不会在浏览器请求 Tushare 或直接写业务表。`"
      :loading="commandLoading"
      @update:model-value="!$event && (commandTarget = null)"
      @submit="submitCommand"
    />
  </section>
</template>
