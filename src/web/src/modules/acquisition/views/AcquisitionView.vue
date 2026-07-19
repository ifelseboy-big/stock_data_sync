<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'

import AdminCommandDialog from '@/components/AdminCommandDialog.vue'
import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useApiResource } from '@/composables/useApiResource'
import {
  cancelAcquisitionBatch,
  createBackfill,
  createRepair,
  getAcquisitionBatches,
  getManualCommandOptions,
  getRunRecords,
  runTaskCommand,
} from '@/modules/operations/api'
import type {
  AcquisitionBatchItem,
  ExecutionStatus,
  ManualCommandOptions,
  PageResult,
  RunRecordItem,
} from '@/modules/operations/contracts'
import { formatDateTime, formatDuration } from '@/modules/operations/presentation'
import { createIdempotencyKey } from '@/utils/idempotency'

const status = ref<ExecutionStatus | ''>('')
const dataCycle = ref('')
const page = ref(1)
const manualMode = ref<'backfill' | 'repair' | null>(null)
const manualLoading = ref(false)
const commandOptions = ref<ManualCommandOptions | null>(null)
const cancelTarget = ref<AcquisitionBatchItem | null>(null)
const cancelLoading = ref(false)
const commandFormRef = ref()
const taskBatch = ref<AcquisitionBatchItem | null>(null)
const taskStatus = ref<ExecutionStatus | ''>('')
const taskPage = ref(1)
const taskData = ref<PageResult<RunRecordItem>>()
const taskLoading = ref(false)
const taskError = ref('')
const retryTarget = ref<RunRecordItem | null>(null)
const retryLoading = ref(false)
const commandForm = reactive({
  startDate: '',
  endDate: '',
  businessDate: '',
  apiNames: [] as string[],
  reason: '',
})
let idempotencyKey = ''

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

const backfillScheduleGroups = new Set(['DAILY', 'DELAYED', 'HOT'])
const availableApis = computed(() =>
  (commandOptions.value?.acquisitionApis ?? []).filter(
    (item) => manualMode.value !== 'backfill' || backfillScheduleGroups.has(item.scheduleGroup),
  ),
)
const selectedAvailableCount = computed(
  () => availableApis.value.filter((item) => commandForm.apiNames.includes(item.apiName)).length,
)
const allAvailableApisSelected = computed(
  () =>
    availableApis.value.length > 0 && selectedAvailableCount.value === availableApis.value.length,
)
const someAvailableApisSelected = computed(
  () => selectedAvailableCount.value > 0 && !allAvailableApisSelected.value,
)
const scheduleGroupMap: Record<string, string> = {
  MASTER: '主数据',
  DAILY: '日频数据',
  DELAYED: '延迟数据',
  HOT: '热榜数据',
}

onMounted(async () => {
  try {
    commandOptions.value = await getManualCommandOptions()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '读取人工操作选项失败')
  }
})

function openManualCommand(mode: 'backfill' | 'repair') {
  manualMode.value = mode
  commandForm.startDate = ''
  commandForm.endDate = ''
  commandForm.businessDate = ''
  commandForm.apiNames = []
  commandForm.reason = ''
  idempotencyKey = createIdempotencyKey()
}

function toggleAllApis(value: boolean | string | number) {
  commandForm.apiNames = value ? availableApis.value.map((item) => item.apiName) : []
}

async function submitManualCommand() {
  await commandFormRef.value?.validate()
  if (!commandForm.apiNames.length) {
    ElMessage.warning('至少选择一个采集接口')
    return
  }
  manualLoading.value = true
  try {
    const options = { idempotencyKey }
    if (manualMode.value === 'backfill') {
      await createBackfill(
        {
          startDate: commandForm.startDate,
          endDate: commandForm.endDate,
          apiNames: commandForm.apiNames,
          reason: commandForm.reason.trim(),
        },
        options,
      )
    } else {
      await createRepair(
        {
          businessDate: commandForm.businessDate || null,
          apiNames: commandForm.apiNames,
          reason: commandForm.reason.trim(),
        },
        options,
      )
    }
    ElMessage.success('采集命令已创建，调度器将异步执行')
    manualMode.value = null
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '采集命令提交失败')
  } finally {
    manualLoading.value = false
  }
}

async function submitCancel(value: { reason: string; idempotencyKey: string }) {
  if (!cancelTarget.value) return
  cancelLoading.value = true
  try {
    await cancelAcquisitionBatch(cancelTarget.value.id, { reason: value.reason }, value)
    ElMessage.success('批次取消命令已执行')
    cancelTarget.value = null
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '批次取消失败')
  } finally {
    cancelLoading.value = false
  }
}

function openCancel(value: unknown) {
  cancelTarget.value = value as AcquisitionBatchItem
}

async function loadBatchTasks() {
  if (!taskBatch.value) return
  taskLoading.value = true
  taskError.value = ''
  try {
    taskData.value = await getRunRecords({
      runType: 'acquisition',
      batchId: taskBatch.value.id,
      status: taskStatus.value || undefined,
      page: taskPage.value,
      pageSize: 50,
    })
  } catch (error) {
    taskError.value = error instanceof Error ? error.message : '批次任务加载失败'
  } finally {
    taskLoading.value = false
  }
}

function openBatchTasks(value: unknown) {
  taskBatch.value = value as AcquisitionBatchItem
  taskStatus.value = ''
  taskPage.value = 1
  void loadBatchTasks()
}

function searchBatchTasks() {
  taskPage.value = 1
  void loadBatchTasks()
}

function openTaskRetry(value: unknown) {
  retryTarget.value = value as RunRecordItem
}

async function submitTaskRetry(value: { reason: string; idempotencyKey: string }) {
  if (!retryTarget.value) return
  retryLoading.value = true
  try {
    await runTaskCommand(
      'acquisition',
      retryTarget.value.id,
      'retry',
      { reason: value.reason },
      value,
    )
    ElMessage.success('已创建单任务修复批次，调度器将自动执行')
    retryTarget.value = null
    await Promise.all([loadBatchTasks(), load()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '任务重试失败')
  } finally {
    retryLoading.value = false
  }
}
</script>

<template>
  <section>
    <PageHeader title="采集运行" description="按批次观察原始数据采集、失败重试和最终结果。">
      <template #actions>
        <el-button @click="openManualCommand('repair')">创建修复</el-button>
        <el-button type="primary" @click="openManualCommand('backfill')">历史回填</el-button>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
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
        <el-table :data="data?.items ?? []" scrollbar-always-on>
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
          <el-table-column label="完成时间" min-width="170">
            <template #default="{ row }">{{ formatDateTime(row.closedAt) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="170" fixed="right">
            <template #default="{ row }">
              <el-button size="small" link type="primary" @click="openBatchTasks(row)">
                查看任务
              </el-button>
              <el-button
                v-if="['pending', 'running', 'waiting_retry'].includes(row.status)"
                size="small"
                link
                type="danger"
                @click="openCancel(row)"
              >
                取消批次
              </el-button>
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

    <el-dialog
      :model-value="taskBatch !== null"
      :title="`批次任务 · ${taskBatch?.themeName ?? ''} · ${taskBatch?.dataCycle ?? ''}`"
      width="min(1180px, 94vw)"
      :close-on-click-modal="false"
      @update:model-value="!$event && (taskBatch = null)"
    >
      <el-alert
        title="采集失败会先自动重试；达到最大次数后，可在下方对具体失败任务执行人工重试。人工重试会自动创建单任务修复批次。"
        type="info"
        :closable="false"
        show-icon
      />
      <el-form :inline="true" class="batch-task-filter" @submit.prevent="searchBatchTasks">
        <el-form-item label="任务状态">
          <el-select v-model="taskStatus" clearable placeholder="全部状态" style="width: 150px">
            <el-option label="等待重试" value="waiting_retry" />
            <el-option label="失败" value="failed" />
            <el-option label="成功" value="succeeded" />
          </el-select>
        </el-form-item>
        <el-form-item><el-button type="primary" native-type="submit">查询</el-button></el-form-item>
      </el-form>
      <DataState
        :loading="taskLoading"
        :error="taskError"
        :empty="taskData?.items.length === 0"
        empty-title="该批次没有符合条件的任务"
        empty-description="可以切换任务状态重新查询。"
        @retry="loadBatchTasks"
      >
        <el-table :data="taskData?.items ?? []" scrollbar-always-on max-height="520">
          <el-table-column label="采集数据" min-width="270" fixed="left">
            <template #default="{ row }">
              <div class="batch-task-name">
                <strong>{{ row.taskDisplayName }}</strong>
                <span>{{ row.taskDescription }}</span>
                <code>{{ row.taskName }}</code>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="scopeKey" label="采集范围" min-width="220" show-overflow-tooltip />
          <el-table-column label="状态" width="110">
            <template #default="{ row }"><StatusTag :status="row.status" /></template>
          </el-table-column>
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
              <el-button
                v-if="row.status === 'failed'"
                size="small"
                link
                type="primary"
                @click="openTaskRetry(row)"
              >
                重试
              </el-button>
              <span v-else>--</span>
            </template>
          </el-table-column>
        </el-table>
        <div class="pagination-row">
          <el-pagination
            v-model:current-page="taskPage"
            background
            layout="total, prev, pager, next"
            :total="taskData?.total ?? 0"
            :page-size="50"
            @current-change="loadBatchTasks"
          />
        </div>
      </DataState>
    </el-dialog>

    <el-dialog
      :model-value="manualMode !== null"
      :title="manualMode === 'backfill' ? '创建历史回填' : '创建修复采集'"
      width="720px"
      :close-on-click-modal="false"
      :show-close="!manualLoading"
      @update:model-value="!$event && (manualMode = null)"
    >
      <el-alert
        :title="
          manualMode === 'backfill'
            ? `只为交易日生成批次，单次最多 ${commandOptions?.maxBackfillDays ?? '--'} 天。`
            : '修复批次只重新采集所选原始接口，后续加工仍由全局串行调度。'
        "
        type="warning"
        :closable="false"
        show-icon
      />
      <el-form ref="commandFormRef" :model="commandForm" label-position="top" class="command-form">
        <div v-if="manualMode === 'backfill'" class="command-date-grid">
          <el-form-item
            label="开始日期"
            prop="startDate"
            :rules="[{ required: true, message: '请选择开始日期', trigger: 'change' }]"
          >
            <el-date-picker
              v-model="commandForm.startDate"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="开始日期"
              style="width: 100%"
            />
          </el-form-item>
          <el-form-item
            label="结束日期"
            prop="endDate"
            :rules="[{ required: true, message: '请选择结束日期', trigger: 'change' }]"
          >
            <el-date-picker
              v-model="commandForm.endDate"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="结束日期"
              style="width: 100%"
            />
          </el-form-item>
        </div>
        <el-form-item v-else label="业务日期（主数据修复可留空）">
          <el-date-picker
            v-model="commandForm.businessDate"
            type="date"
            value-format="YYYY-MM-DD"
            clearable
            placeholder="选择业务日期"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item
          :label="manualMode === 'backfill' ? '需要回填的数据' : '需要修复的数据'"
          required
        >
          <div class="api-selector-toolbar">
            <el-checkbox
              :model-value="allAvailableApisSelected"
              :indeterminate="someAvailableApisSelected"
              @change="toggleAllApis"
            >
              全选当前可用接口
            </el-checkbox>
            <span>已选 {{ selectedAvailableCount }} / {{ availableApis.length }} 项</span>
          </div>
          <el-select
            v-model="commandForm.apiNames"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            :max-collapse-tags="2"
            placeholder="可按中文名称或接口名搜索"
            style="width: 100%"
          >
            <el-option
              v-for="item in availableApis"
              :key="item.apiName"
              :label="`${item.displayName} ${item.apiName}`"
              :value="item.apiName"
              class="api-option"
            >
              <div class="api-option__title">
                <span>{{ item.displayName }}</span>
                <code>{{ item.apiName }}</code>
                <el-tag size="small" type="info">
                  {{ scheduleGroupMap[item.scheduleGroup] ?? item.scheduleGroup }}
                </el-tag>
              </div>
              <div class="api-option__description">{{ item.description }}</div>
            </el-option>
          </el-select>
          <div class="form-helper">
            历史回填只显示可按交易日期采集的数据；主数据请使用“创建修复”。
          </div>
        </el-form-item>
        <el-form-item
          label="操作原因"
          prop="reason"
          :rules="[
            { required: true, message: '请输入操作原因', trigger: 'blur' },
            { min: 3, max: 500, message: '原因长度为 3 到 500 个字符', trigger: 'blur' },
          ]"
        >
          <el-input v-model="commandForm.reason" type="textarea" :rows="3" maxlength="500" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="manualLoading" @click="manualMode = null">取消</el-button>
        <el-button type="primary" :loading="manualLoading" @click="submitManualCommand">
          创建命令
        </el-button>
      </template>
    </el-dialog>

    <AdminCommandDialog
      :model-value="cancelTarget !== null"
      title="取消采集批次"
      :description="`批次：${cancelTarget?.batchCode ?? ''}。存在正在执行的采集任务时，服务端会拒绝取消。`"
      confirm-text="确认取消"
      :loading="cancelLoading"
      @update:model-value="!$event && (cancelTarget = null)"
      @submit="submitCancel"
    />

    <AdminCommandDialog
      :model-value="retryTarget !== null"
      title="重试采集任务"
      :description="`系统会复制“${retryTarget?.taskDisplayName ?? ''}”的原接口和采集范围，自动创建单任务修复批次，不会重新打开原批次。`"
      confirm-text="确认重试"
      :loading="retryLoading"
      @update:model-value="!$event && (retryTarget = null)"
      @submit="submitTaskRetry"
    />
  </section>
</template>

<style scoped>
.command-form {
  margin-top: 20px;
}

.batch-task-filter {
  margin-top: 16px;
}

.batch-task-name {
  display: grid;
  gap: 3px;
}

.batch-task-name span,
.batch-task-name code {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.4;
}

.command-date-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.api-selector-toolbar {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  color: var(--el-text-color-secondary);
}

.api-option {
  height: auto;
  min-height: 58px;
  padding-top: 7px;
  padding-bottom: 7px;
  line-height: 1.4;
}

.api-option__title {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-right: 24px;
  color: var(--el-text-color-primary);
  font-weight: 600;
}

.api-option__title code {
  color: var(--el-text-color-secondary);
  font-weight: 400;
}

.api-option__description,
.form-helper {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.api-option__description {
  margin-top: 3px;
  padding-right: 24px;
}

.form-helper {
  margin-top: 6px;
}
</style>
