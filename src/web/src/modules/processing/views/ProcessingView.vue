<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AdminCommandDialog from '@/components/AdminCommandDialog.vue'
import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useApiResource } from '@/composables/useApiResource'
import {
  getProcessingQueue,
  getRunRecords,
  retryAllFailedProcessingTasks,
  runTaskCommand,
} from '@/modules/operations/api'
import type { ExecutionStatus, RunRecordItem } from '@/modules/operations/contracts'
import { formatDateTime, formatDuration, formatPriority } from '@/modules/operations/presentation'

const route = useRoute()
const router = useRouter()
const page = ref(1)
const status = ref<ExecutionStatus | ''>('')
const datasetName = ref(typeof route.query.datasetName === 'string' ? route.query.datasetName : '')
const failedPage = ref(1)
const retryTarget = ref<RunRecordItem | null>(null)
const retryLoading = ref(false)
const retryAllOpen = ref(false)
const retryAllLoading = ref(false)
const { data, loading, error, load } = useApiResource(() =>
  getProcessingQueue({
    status: status.value || undefined,
    datasetName: datasetName.value.trim() || undefined,
    page: page.value,
    pageSize: 50,
  }),
)
const currentTasks = computed(
  () => data.value?.items.filter((item) => item.status === 'running') ?? [],
)
const waitingTasks = computed(
  () => data.value?.items.filter((item) => item.status !== 'running') ?? [],
)
const {
  data: failedData,
  loading: failedLoading,
  error: failedError,
  load: loadFailed,
} = useApiResource(() =>
  getRunRecords({
    runType: 'processing',
    status: 'failed',
    unresolvedOnly: true,
    page: failedPage.value,
    pageSize: 20,
  }),
)

function search() {
  page.value = 1
  void load()
}

function openReadiness(taskName: string) {
  void router.push({ path: '/dependencies', query: { query: taskName, readiness: 'all' } })
}

function openRetry(value: unknown) {
  retryTarget.value = value as RunRecordItem
}

async function submitRetry(value: { reason: string; idempotencyKey: string }) {
  if (!retryTarget.value) return
  retryLoading.value = true
  try {
    await runTaskCommand(
      'processing',
      retryTarget.value.id,
      'retry',
      { reason: value.reason },
      value,
    )
    ElMessage.success('加工任务已重新进入受控并发队列')
    retryTarget.value = null
    await Promise.all([load(), loadFailed()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加工任务重试失败')
  } finally {
    retryLoading.value = false
  }
}

async function submitRetryAll(value: { reason: string; idempotencyKey: string }) {
  retryAllLoading.value = true
  try {
    const command = await retryAllFailedProcessingTasks({ reason: value.reason }, value)
    const retried = Number(command.result.retryCount ?? 0)
    const skipped = Number(command.result.skippedDependencyCount ?? 0)
    const deduplicated = Number(command.result.deduplicatedCount ?? 0)
    const skippedActive = Number(command.result.skippedActiveCount ?? 0)
    const notes: string[] = []
    if (skipped) notes.push(`${skipped} 个因依赖未就绪跳过`)
    if (deduplicated) notes.push(`去除 ${deduplicated} 个重复失败`)
    if (skippedActive) notes.push(`跳过 ${skippedActive} 个已有活动任务的范围`)
    ElMessage.success(
      `已将 ${retried} 个逻辑任务加入加工队列${notes.length ? `；${notes.join('，')}` : ''}`,
    )
    retryAllOpen.value = false
    await Promise.all([load(), loadFailed()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '全部失败任务重试失败')
  } finally {
    retryAllLoading.value = false
  }
}
</script>

<template>
  <section>
    <PageHeader title="加工队列" description="按配置受控并发执行；同一数据集保持串行发布。">
      <template #actions><el-button :loading="loading" @click="load">刷新</el-button></template>
    </PageHeader>

    <el-card shadow="never" class="execution-slot">
      <div class="execution-slot__label">当前运行任务 {{ currentTasks.length }} 个</div>
      <div v-for="task in currentTasks" :key="task.id" class="execution-slot__content">
        <div>
          <strong>{{ task.taskName }}</strong>
          <p>{{ task.batchCode }} · {{ task.dataCycle }}</p>
        </div>
        <div class="execution-slot__meta">
          <StatusTag :status="task.status" />
          <span>开始于 {{ formatDateTime(task.startedAt) }}</span>
          <span>已运行 {{ formatDuration(task.durationMs) }}</span>
        </div>
      </div>
      <div v-if="!currentTasks.length" class="execution-slot__empty">
        空闲，等待可执行任务进入队列
      </div>
    </el-card>

    <el-card shadow="never" class="panel-card panel-card--table failed-panel">
      <template #header>
        <div class="panel-card__header">
          <div>
            <h3>需要人工处理的失败任务</h3>
            <p>自动重试次数已经用完；重试只读取现有原始数据，不会再次请求 Tushare。</p>
          </div>
          <div class="failed-panel__actions">
            <el-button
              type="warning"
              plain
              :disabled="!failedData?.total"
              :loading="retryAllLoading"
              @click="retryAllOpen = true"
            >
              全部重试
            </el-button>
            <el-tag :type="failedData?.total ? 'danger' : 'success'">
              {{ failedData?.total ?? 0 }} 个失败任务
            </el-tag>
          </div>
        </div>
      </template>
      <DataState
        :loading="failedLoading"
        :error="failedError"
        :empty="failedData?.items.length === 0"
        empty-title="当前没有需要人工处理的加工任务"
        empty-description="可恢复错误会先按重试策略自动执行。"
        @retry="loadFailed"
      >
        <el-table :data="failedData?.items ?? []" scrollbar-always-on>
          <el-table-column label="加工任务" min-width="280" fixed="left">
            <template #default="{ row }">
              <div class="processing-task-name">
                <strong>{{ row.taskDisplayName }}</strong>
                <span>{{ row.taskDescription }}</span>
                <code>{{ row.taskName }}</code>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="dataCycle" label="数据周期" width="120" />
          <el-table-column prop="batchCode" label="来源批次" min-width="180" />
          <el-table-column prop="attempt" label="尝试次数" width="100" />
          <el-table-column label="耗时" width="110">
            <template #default="{ row }">{{ formatDuration(row.durationMs) }}</template>
          </el-table-column>
          <el-table-column
            prop="errorSummary"
            label="失败原因"
            min-width="260"
            show-overflow-tooltip
          />
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button size="small" link type="primary" @click="openRetry(row)"> 重试 </el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pagination-row">
          <el-pagination
            v-model:current-page="failedPage"
            background
            layout="total, prev, pager, next"
            :total="failedData?.total ?? 0"
            :page-size="20"
            @current-change="loadFailed"
          />
        </div>
      </DataState>
    </el-card>

    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" @submit.prevent="search">
        <el-form-item label="数据集">
          <el-input
            v-model="datasetName"
            clearable
            placeholder="如 stock_daily.core"
            style="width: 240px"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="status" clearable placeholder="全部状态" style="width: 150px">
            <el-option label="等待依赖" value="waiting_dependency" />
            <el-option label="待执行" value="pending" />
            <el-option label="运行中" value="running" />
            <el-option label="等待重试" value="waiting_retry" />
            <el-option label="阻塞" value="blocked" />
          </el-select>
        </el-form-item>
        <el-form-item><el-button type="primary" native-type="submit">查询</el-button></el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="panel-card panel-card--table">
      <template #header>
        <div class="panel-card__header">
          <div>
            <h3>等待队列</h3>
            <p>优先级相同时按计划生成时间排序。</p>
          </div>
          <el-tag type="info">{{ data?.total ?? 0 }} 个任务</el-tag>
        </div>
      </template>
      <DataState
        :loading="loading"
        :error="error"
        :empty="waitingTasks.length === 0"
        empty-title="加工队列为空"
        empty-description="批次关闭并完成依赖判断后，任务会进入此队列。"
        @retry="load"
      >
        <el-table :data="waitingTasks" scrollbar-always-on>
          <el-table-column prop="queuePosition" label="#" width="60" />
          <el-table-column label="加工任务" min-width="280">
            <template #default="{ row }">
              <div class="processing-task-name">
                <strong>{{ row.taskDisplayName }}</strong>
                <span>{{ row.taskDescription }}</span>
                <code>{{ row.taskName }}</code>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="batchCode" label="批次" min-width="170" />
          <el-table-column prop="dataCycle" label="数据周期" min-width="120" />
          <el-table-column label="优先级" width="120">
            <template #default="{ row }">{{ formatPriority(row.priority) }}</template>
          </el-table-column>
          <el-table-column prop="dependencyCount" label="必要依赖" width="100" />
          <el-table-column label="状态" width="110">
            <template #default="{ row }"><StatusTag :status="row.status" /></template>
          </el-table-column>
          <el-table-column label="等待起点" min-width="170">
            <template #default="{ row }">{{ formatDateTime(row.waitingSince) }}</template>
          </el-table-column>
          <el-table-column
            prop="blockedReason"
            label="阻塞原因"
            min-width="220"
            show-overflow-tooltip
          />
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="['waiting_dependency', 'blocked'].includes(row.status)"
                size="small"
                link
                type="primary"
                @click="openReadiness(row.taskName)"
              >
                查看前置数据
              </el-button>
              <span v-else>--</span>
            </template>
          </el-table-column>
        </el-table>
        <div class="pagination-row">
          <el-pagination
            v-model:current-page="page"
            background
            layout="total, prev, pager, next"
            :total="data?.total ?? 0"
            :page-size="50"
            @current-change="load"
          />
        </div>
      </DataState>
    </el-card>

    <AdminCommandDialog
      :model-value="retryTarget !== null"
      title="重试加工任务"
      :description="`任务：${retryTarget?.taskDisplayName ?? ''}。系统只重新读取已经封存的原始数据，并重新进入受控并发加工队列。`"
      confirm-text="确认重试"
      :loading="retryLoading"
      @update:model-value="!$event && (retryTarget = null)"
      @submit="submitRetry"
    />
    <AdminCommandDialog
      v-model="retryAllOpen"
      title="重试全部失败加工任务"
      :description="`将重试近 30 天仍未恢复的 ${failedData?.total ?? 0} 个失败记录。系统会按数据集和业务日期去重；依赖未就绪或已有活动任务的范围会跳过并提示。`"
      confirm-text="确认全部重试"
      :loading="retryAllLoading"
      @submit="submitRetryAll"
    />
  </section>
</template>

<style scoped>
.failed-panel {
  margin-bottom: 16px;
}

.failed-panel__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.processing-task-name {
  display: grid;
  gap: 3px;
}

.processing-task-name span,
.processing-task-name code {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.4;
}
</style>
