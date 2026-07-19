<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'

import AdminCommandDialog from '@/components/AdminCommandDialog.vue'
import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import { useApiResource } from '@/composables/useApiResource'
import {
  getScheduledJobExecutions,
  getScheduledJobs,
  runScheduledJobCommand,
} from '@/modules/operations/api'
import type {
  ScheduledJobAction,
  ScheduledJobItem,
  ScheduledJobStatus,
} from '@/modules/operations/contracts'
import { formatDateTime, formatDuration } from '@/modules/operations/presentation'

const selectedJobId = ref('')
const selectedStatus = ref<ScheduledJobStatus | ''>('')
const page = ref(1)
const commandTarget = ref<{ job: ScheduledJobItem; action: ScheduledJobAction } | null>(null)
const commandLoading = ref(false)

const {
  data: jobs,
  loading: jobsLoading,
  error: jobsError,
  load: loadJobs,
} = useApiResource(getScheduledJobs)
const {
  data: executions,
  loading: executionsLoading,
  error: executionsError,
  load: loadExecutions,
} = useApiResource(() =>
  getScheduledJobExecutions({
    jobId: selectedJobId.value || undefined,
    status: selectedStatus.value || undefined,
    page: page.value,
    pageSize: 20,
  }),
)

const commandTitle = computed(() => {
  const target = commandTarget.value
  if (!target) return ''
  const verb = { run: '立即执行', enable: '启用', disable: '停用' }[target.action]
  return `${verb}定时任务`
})

const commandDescription = computed(() => {
  const target = commandTarget.value
  if (!target) return ''
  if (target.action === 'run') {
    return `任务：${target.job.name}。命令进入 PostgreSQL 队列，由独立调度器执行。`
  }
  return `任务：${target.job.name}。${target.action === 'enable' ? '启用后恢复定时触发。' : '停用后跳过定时和启动补偿，仍允许人工执行。'}`
})

const categoryMap: Record<string, string> = {
  runtime: '运行协调',
  master: '主数据',
  daily: '日频采集',
  maintenance: '系统维护',
}
const executionStatusMap: Record<
  ScheduledJobStatus,
  { label: string; type: 'info' | 'primary' | 'success' | 'danger' }
> = {
  pending: { label: '待执行', type: 'info' },
  running: { label: '运行中', type: 'primary' },
  success: { label: '成功', type: 'success' },
  failed: { label: '失败', type: 'danger' },
}
const triggerMap = { scheduled: '定时', manual: '人工', startup_catchup: '启动补偿' } as const

function statusMeta(status: unknown) {
  return executionStatusMap[status as ScheduledJobStatus] ?? executionStatusMap.pending
}

function openCommand(row: unknown, action: ScheduledJobAction) {
  commandTarget.value = { job: row as ScheduledJobItem, action }
}

function toggleCommand(row: unknown) {
  const job = row as ScheduledJobItem
  openCommand(job, job.enabled ? 'disable' : 'enable')
}

function searchExecutions() {
  page.value = 1
  void loadExecutions()
}

function refresh() {
  void Promise.all([loadJobs(), loadExecutions()])
}

async function submitCommand(value: {
  reason: string
  adminToken: string
  idempotencyKey: string
}) {
  const target = commandTarget.value
  if (!target) return
  commandLoading.value = true
  try {
    await runScheduledJobCommand(target.job.jobId, target.action, { reason: value.reason }, value)
    ElMessage.success(target.action === 'run' ? '人工执行请求已进入调度队列' : '任务状态已更新')
    commandTarget.value = null
    await Promise.all([loadJobs(), loadExecutions()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '定时任务命令提交失败')
  } finally {
    commandLoading.value = false
  }
}
</script>

<template>
  <section>
    <PageHeader title="任务调度" description="查看任务计划、下次执行时间和实际执行结果。">
      <template #actions>
        <el-button :loading="jobsLoading || executionsLoading" @click="refresh">刷新</el-button>
      </template>
    </PageHeader>

    <el-card shadow="never" class="panel-card panel-card--table">
      <template #header>
        <div class="panel-card__header">
          <div>
            <h3>定时任务</h3>
            <p>停用只影响定时触发和启动补偿；人工执行仍需管理 Token 和操作原因。</p>
          </div>
        </div>
      </template>
      <DataState
        :loading="jobsLoading"
        :error="jobsError"
        :empty="jobs?.length === 0"
        empty-title="暂无定时任务"
        @retry="loadJobs"
      >
        <el-table :data="jobs ?? []" scrollbar-always-on>
          <el-table-column prop="name" label="任务" min-width="210" fixed="left" />
          <el-table-column label="分类" width="110">
            <template #default="{ row }">{{ categoryMap[row.category] ?? row.category }}</template>
          </el-table-column>
          <el-table-column prop="schedule" label="计划" min-width="170" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.enabled ? 'success' : 'info'">
                {{ row.enabled ? '已启用' : '已停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="下次执行" min-width="170">
            <template #default="{ row }">{{ formatDateTime(row.nextRunAt) }}</template>
          </el-table-column>
          <el-table-column label="最近结果" width="100">
            <template #default="{ row }">
              <el-tag v-if="row.lastStatus" :type="statusMeta(row.lastStatus).type">
                {{ statusMeta(row.lastStatus).label }}
              </el-tag>
              <span v-else>--</span>
            </template>
          </el-table-column>
          <el-table-column label="最近耗时" width="110">
            <template #default="{ row }">{{ formatDuration(row.lastDurationMs) }}</template>
          </el-table-column>
          <el-table-column
            prop="lastError"
            label="最近错误"
            min-width="220"
            show-overflow-tooltip
          />
          <el-table-column label="操作" width="190" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="row.manualAllowed"
                link
                type="primary"
                @click="openCommand(row, 'run')"
              >
                立即执行
              </el-button>
              <el-button
                link
                :type="row.enabled ? 'danger' : 'success'"
                @click="toggleCommand(row)"
              >
                {{ row.enabled ? '停用' : '启用' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </DataState>
    </el-card>

    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" @submit.prevent="searchExecutions">
        <el-form-item label="任务">
          <el-select
            v-model="selectedJobId"
            filterable
            clearable
            placeholder="全部任务"
            style="width: 260px"
          >
            <el-option
              v-for="job in jobs ?? []"
              :key="job.jobId"
              :label="job.name"
              :value="job.jobId"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="结果">
          <el-select v-model="selectedStatus" clearable placeholder="全部结果" style="width: 140px">
            <el-option label="待执行" value="pending" />
            <el-option label="运行中" value="running" />
            <el-option label="成功" value="success" />
            <el-option label="失败" value="failed" />
          </el-select>
        </el-form-item>
        <el-form-item><el-button type="primary" native-type="submit">查询</el-button></el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="panel-card panel-card--table">
      <template #header><h3>执行记录</h3></template>
      <DataState
        :loading="executionsLoading"
        :error="executionsError"
        :empty="executions?.items.length === 0"
        empty-title="暂无调度执行记录"
        empty-description="调度器开始运行或提交人工执行后会显示记录。"
        @retry="loadExecutions"
      >
        <el-table :data="executions?.items ?? []" scrollbar-always-on>
          <el-table-column prop="jobId" label="任务 ID" min-width="210" fixed="left" />
          <el-table-column label="触发方式" width="110">
            <template #default="{ row }">{{
              triggerMap[row.triggerType as keyof typeof triggerMap]
            }}</template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="statusMeta(row.status).type">
                {{ statusMeta(row.status).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="110">
            <template #default="{ row }">{{ formatDuration(row.durationMs) }}</template>
          </el-table-column>
          <el-table-column label="开始时间" min-width="170">
            <template #default="{ row }">{{ formatDateTime(row.startedAt) }}</template>
          </el-table-column>
          <el-table-column label="结束时间" min-width="170">
            <template #default="{ row }">{{ formatDateTime(row.finishedAt) }}</template>
          </el-table-column>
          <el-table-column prop="requestedBy" label="操作人" width="120" />
          <el-table-column prop="reason" label="操作原因" min-width="180" show-overflow-tooltip />
          <el-table-column prop="errorMessage" label="错误" min-width="220" show-overflow-tooltip />
        </el-table>
        <div class="pagination-row">
          <el-pagination
            v-model:current-page="page"
            background
            layout="total, prev, pager, next"
            :total="executions?.total ?? 0"
            :page-size="20"
            @current-change="loadExecutions"
          />
        </div>
      </DataState>
    </el-card>

    <AdminCommandDialog
      :model-value="commandTarget !== null"
      :title="commandTitle"
      :description="commandDescription"
      :loading="commandLoading"
      @update:model-value="!$event && (commandTarget = null)"
      @submit="submitCommand"
    />
  </section>
</template>
