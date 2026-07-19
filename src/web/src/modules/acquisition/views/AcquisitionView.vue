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
} from '@/modules/operations/api'
import type {
  AcquisitionBatchItem,
  ExecutionStatus,
  ManualCommandOptions,
} from '@/modules/operations/contracts'
import { formatDateTime, formatDuration } from '@/modules/operations/presentation'

const status = ref<ExecutionStatus | ''>('')
const dataCycle = ref('')
const page = ref(1)
const manualMode = ref<'backfill' | 'repair' | null>(null)
const manualLoading = ref(false)
const commandOptions = ref<ManualCommandOptions | null>(null)
const cancelTarget = ref<AcquisitionBatchItem | null>(null)
const cancelLoading = ref(false)
const commandFormRef = ref()
const commandForm = reactive({
  startDate: '',
  endDate: '',
  businessDate: '',
  apiNames: [] as string[],
  reason: '',
  adminToken: '',
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

const availableApis = computed(() =>
  (commandOptions.value?.acquisitionApis ?? []).filter(
    (item) => manualMode.value !== 'backfill' || item.scheduleGroup === 'DAILY',
  ),
)

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
  idempotencyKey = crypto.randomUUID()
}

async function submitManualCommand() {
  await commandFormRef.value?.validate()
  if (!commandForm.apiNames.length) {
    ElMessage.warning('至少选择一个采集接口')
    return
  }
  manualLoading.value = true
  try {
    const options = { adminToken: commandForm.adminToken, idempotencyKey }
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

async function submitCancel(value: { reason: string; adminToken: string; idempotencyKey: string }) {
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
</script>

<template>
  <section>
    <PageHeader title="采集运行" description="按批次观察原始数据采集、失败重试和批次关闭结果。">
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
          <el-table-column label="人工操作" width="100" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="['pending', 'running', 'waiting_retry'].includes(row.status)"
                size="small"
                link
                type="danger"
                @click="openCancel(row)"
              >
                取消批次
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
            :page-size="20"
            @current-change="load"
          />
        </div>
      </DataState>
    </el-card>

    <el-dialog
      :model-value="manualMode !== null"
      :title="manualMode === 'backfill' ? '创建历史回填' : '创建修复采集'"
      width="580px"
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
        <el-form-item label="采集接口" required>
          <el-select
            v-model="commandForm.apiNames"
            multiple
            filterable
            placeholder="选择需要采集的接口"
            style="width: 100%"
          >
            <el-option
              v-for="item in availableApis"
              :key="item.apiName"
              :label="`${item.apiName} · ${item.scheduleGroup}`"
              :value="item.apiName"
            />
          </el-select>
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
        <el-form-item
          label="管理 Token"
          prop="adminToken"
          :rules="[{ required: true, message: '请输入管理 Token', trigger: 'blur' }]"
        >
          <el-input
            v-model="commandForm.adminToken"
            type="password"
            show-password
            autocomplete="off"
            placeholder="ADMIN_API_TOKEN"
          />
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
  </section>
</template>

<style scoped>
.command-form {
  margin-top: 20px;
}

.command-date-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
</style>
