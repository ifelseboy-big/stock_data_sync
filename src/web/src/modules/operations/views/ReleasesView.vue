<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'

import AdminCommandDialog from '@/components/AdminCommandDialog.vue'
import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceLabel from '@/components/ResourceLabel.vue'
import { useApiResource } from '@/composables/useApiResource'
import {
  getDatasetReleaseCoverage,
  getDatasetReleases,
  recoverReleaseGaps,
} from '@/modules/operations/api'
import type { DatasetReleaseCoverageItem } from '@/modules/operations/contracts'
import { formatDateTime } from '@/modules/operations/presentation'

const datasetName = ref('')
const page = ref(1)
const coverageFilter = ref<DatasetReleaseCoverageItem['coverageStatus'] | 'all'>('all')
const gapTarget = ref<{
  action: 'backfill' | 'repair'
  startDate: string
  endDate: string
  missingDateCount: number
} | null>(null)
const gapLoading = ref(false)

function localDate(value: Date) {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const today = new Date()
const defaultStart = new Date(today)
defaultStart.setDate(defaultStart.getDate() - 29)
const coverageRange = ref<[string, string] | null>([localDate(defaultStart), localDate(today)])
const coveragePresets = [
  { label: '近 7 天', days: 7 },
  { label: '近 30 天', days: 30 },
  { label: '近 90 天', days: 90 },
  { label: '近半年', days: 180 },
  { label: '近一年', days: 365 },
] as const

const { data, loading, error, load } = useApiResource(() =>
  getDatasetReleases({
    datasetName: datasetName.value || undefined,
    page: page.value,
    pageSize: 20,
  }),
)
const {
  data: coverage,
  loading: coverageLoading,
  error: coverageError,
  load: loadCoverage,
} = useApiResource(() =>
  getDatasetReleaseCoverage({
    startDate: coverageRange.value?.[0],
    endDate: coverageRange.value?.[1],
  }),
)

const coverageSummary = computed(() => {
  const items = coverage.value ?? []
  return {
    tradingDayCount: items.length,
    completeCount: items.filter((item) => item.coverageStatus === 'complete').length,
    missingCount: items.filter((item) => item.coverageStatus === 'missing').length,
    pendingCount: items.filter((item) => item.coverageStatus === 'pending').length,
  }
})
const missingRows = computed(() =>
  (coverage.value ?? []).filter((item) => item.coverageStatus === 'missing'),
)
const filteredCoverageRows = computed(() =>
  coverageFilter.value === 'all'
    ? (coverage.value ?? [])
    : (coverage.value ?? []).filter((item) => item.coverageStatus === coverageFilter.value),
)
const gapDialogTitle = computed(() =>
  gapTarget.value?.action === 'repair' ? '修复当日缺失数据' : '回填范围内缺失数据',
)
const gapDialogDescription = computed(() => {
  const target = gapTarget.value
  if (!target) return ''
  const range =
    target.startDate === target.endDate
      ? target.startDate
      : `${target.startDate} 至 ${target.endDate}`
  return `系统将重新核对 ${range} 的发布完整性，只为 ${target.missingDateCount} 个缺失交易日创建必要的采集任务；完整日期和已有活动任务不会重复创建。`
})

const coverageStatusMap = {
  complete: { label: '完整', type: 'success' },
  missing: { label: '有缺失', type: 'danger' },
  pending: { label: '今日进行中', type: 'warning' },
} as const

function coverageStatusMeta(value: unknown) {
  return coverageStatusMap[value as DatasetReleaseCoverageItem['coverageStatus']]
}

function refresh() {
  void Promise.all([load(), loadCoverage()])
}

function search() {
  page.value = 1
  void load()
}

function searchCoverage() {
  void loadCoverage()
}

function applyCoveragePreset(days: number) {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - days + 1)
  coverageRange.value = [localDate(start), localDate(end)]
  void loadCoverage()
}

function applyCurrentYear() {
  const end = new Date()
  coverageRange.value = [localDate(new Date(end.getFullYear(), 0, 1)), localDate(end)]
  void loadCoverage()
}

function applyCoverageFilter(value: DatasetReleaseCoverageItem['coverageStatus'] | 'all') {
  coverageFilter.value = value
}

function openRangeGapBackfill() {
  if (!coverageRange.value || !missingRows.value.length) return
  gapTarget.value = {
    action: 'backfill',
    startDate: coverageRange.value[0],
    endDate: coverageRange.value[1],
    missingDateCount: missingRows.value.length,
  }
}

function openDateGapRepair(value: unknown) {
  const row = value as DatasetReleaseCoverageItem
  gapTarget.value = {
    action: 'repair',
    startDate: row.businessDate,
    endDate: row.businessDate,
    missingDateCount: 1,
  }
}

async function submitGapRecovery(value: { reason: string; idempotencyKey: string }) {
  const target = gapTarget.value
  if (!target) return
  gapLoading.value = true
  try {
    const command = await recoverReleaseGaps(
      target.action,
      {
        startDate: target.startDate,
        endDate: target.endDate,
        reason: value.reason,
      },
      { idempotencyKey: value.idempotencyKey },
    )
    const batches = Number(command.result.batchCount ?? 0)
    const missingDates = Number(command.result.missingDateCount ?? 0)
    const skippedActive = Number(command.result.skippedActiveApiCount ?? 0)
    ElMessage.success(
      `已为 ${missingDates} 个缺失交易日创建 ${batches} 个任务批次${skippedActive ? `，跳过 ${skippedActive} 个正在处理的接口` : ''}`,
    )
    gapTarget.value = null
    await Promise.all([loadCoverage(), load()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '缺失数据恢复失败')
  } finally {
    gapLoading.value = false
  }
}

function missingDatasetText(value: unknown) {
  const row = value as DatasetReleaseCoverageItem
  return row.missingDatasetDisplayNames
    .map((name, index) => `${name}（${row.missingDatasets[index]}）`)
    .join('、')
}
</script>

<template>
  <section>
    <PageHeader title="数据发布" description="查看各数据集当前可消费范围、处理版本和发布行数。">
      <template #actions>
        <el-button :loading="loading || coverageLoading" @click="refresh">刷新</el-button>
      </template>
    </PageHeader>

    <el-card shadow="never" class="panel-card panel-card--table">
      <template #header>
        <div class="panel-card__header">
          <div>
            <h3>时间范围数据完整性</h3>
            <p>仅检查交易日；今天尚未完成的发布标记为“进行中”，不会误报为历史缺失。</p>
          </div>
          <el-button
            type="danger"
            plain
            :disabled="coverageLoading || !missingRows.length"
            :loading="gapLoading"
            @click="openRangeGapBackfill"
          >
            回填全部缺失{{ missingRows.length ? `（${missingRows.length} 日）` : '' }}
          </el-button>
        </div>
      </template>

      <el-form :inline="true" class="coverage-filter" @submit.prevent="searchCoverage">
        <el-form-item label="检查范围">
          <el-date-picker
            v-model="coverageRange"
            type="daterange"
            unlink-panels
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            value-format="YYYY-MM-DD"
            :clearable="false"
            @change="searchCoverage"
          />
        </el-form-item>
        <el-form-item label="快捷范围">
          <el-button-group>
            <el-button
              v-for="preset in coveragePresets"
              :key="preset.label"
              @click="applyCoveragePreset(preset.days)"
            >
              {{ preset.label }}
            </el-button>
            <el-button @click="applyCurrentYear">今年</el-button>
          </el-button-group>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" native-type="submit" :loading="coverageLoading">
            检查完整性
          </el-button>
        </el-form-item>
      </el-form>

      <div class="coverage-summary" aria-label="数据完整性筛选">
        <button
          type="button"
          class="coverage-summary__item"
          :class="{ 'is-active': coverageFilter === 'all' }"
          :aria-pressed="coverageFilter === 'all'"
          data-testid="coverage-filter-all"
          @click="applyCoverageFilter('all')"
        >
          <span>全部交易日</span><strong>{{ coverageSummary.tradingDayCount }}</strong>
          <small>点击查看全部</small>
        </button>
        <button
          type="button"
          class="coverage-summary__item coverage-summary__item--success"
          :class="{ 'is-active': coverageFilter === 'complete' }"
          :aria-pressed="coverageFilter === 'complete'"
          :disabled="coverageSummary.completeCount === 0"
          data-testid="coverage-filter-complete"
          @click="applyCoverageFilter('complete')"
        >
          <span>完整</span><strong>{{ coverageSummary.completeCount }}</strong>
          <small>点击只看完整日期</small>
        </button>
        <button
          type="button"
          class="coverage-summary__item coverage-summary__item--danger"
          :class="{ 'is-active': coverageFilter === 'missing' }"
          :aria-pressed="coverageFilter === 'missing'"
          :disabled="coverageSummary.missingCount === 0"
          data-testid="coverage-filter-missing"
          @click="applyCoverageFilter('missing')"
        >
          <span>存在缺失</span><strong>{{ coverageSummary.missingCount }}</strong>
          <small>点击只看缺失日期</small>
        </button>
        <button
          type="button"
          class="coverage-summary__item coverage-summary__item--warning"
          :class="{ 'is-active': coverageFilter === 'pending' }"
          :aria-pressed="coverageFilter === 'pending'"
          :disabled="coverageSummary.pendingCount === 0"
          data-testid="coverage-filter-pending"
          @click="applyCoverageFilter('pending')"
        >
          <span>今日进行中</span><strong>{{ coverageSummary.pendingCount }}</strong>
          <small>点击查看今日进度</small>
        </button>
      </div>

      <DataState
        :loading="coverageLoading"
        :error="coverageError"
        :empty="filteredCoverageRows.length === 0"
        :empty-title="coverage?.length ? '当前筛选下没有交易日' : '所选范围内没有交易日'"
        empty-description="可以切换汇总筛选或调整日期范围。"
        @retry="loadCoverage"
      >
        <el-table :data="filteredCoverageRows" data-testid="coverage-table">
          <el-table-column prop="businessDate" label="交易日" width="130" />
          <el-table-column label="完整度" width="130">
            <template #default="{ row }"
              >{{ row.publishedCount }} / {{ row.expectedCount }}</template
            >
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="coverageStatusMeta(row.coverageStatus).type">
                {{ coverageStatusMeta(row.coverageStatus).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="缺失数据集" min-width="320">
            <template #default="{ row }">
              <div
                v-if="row.missingDatasets.length"
                class="missing-datasets"
                :title="missingDatasetText(row)"
              >
                <ResourceLabel
                  v-for="(identifier, index) in row.missingDatasets"
                  :key="identifier"
                  :display-name="row.missingDatasetDisplayNames[index] ?? identifier"
                  :identifier="identifier"
                />
              </div>
              <span v-else>--</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="row.coverageStatus === 'missing'"
                type="danger"
                link
                :disabled="gapLoading"
                @click="openDateGapRepair(row)"
              >
                修复当日
              </el-button>
              <span v-else>--</span>
            </template>
          </el-table-column>
        </el-table>
      </DataState>
    </el-card>

    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" @submit.prevent="search">
        <el-form-item label="数据集">
          <el-input
            v-model.trim="datasetName"
            placeholder="如 stock_daily.core"
            clearable
            style="width: 240px"
          />
        </el-form-item>
        <el-form-item><el-button type="primary" native-type="submit">查询</el-button></el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="panel-card panel-card--table">
      <DataState
        :loading="loading"
        :error="error"
        :empty="data?.items.length === 0"
        empty-title="暂无发布记录"
        empty-description="加工任务完成原子发布后会显示在这里。"
        @retry="load"
      >
        <el-table :data="data?.items ?? []" scrollbar-always-on>
          <el-table-column label="数据集" min-width="230" fixed="left">
            <template #default="{ row }">
              <ResourceLabel
                :display-name="row.datasetDisplayName"
                :identifier="row.datasetName"
                :title="row.datasetDescription"
              />
            </template>
          </el-table-column>
          <el-table-column prop="scopeType" label="范围类型" width="110" />
          <el-table-column prop="scopeKey" label="发布范围" min-width="140" />
          <el-table-column prop="rowCount" label="发布行数" width="120" />
          <el-table-column prop="processorVersion" label="处理器版本" width="120" />
          <el-table-column
            prop="processId"
            label="加工任务"
            min-width="220"
            show-overflow-tooltip
          />
          <el-table-column label="发布时间" min-width="180">
            <template #default="{ row }">{{ formatDateTime(row.publishedAt) }}</template>
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
      :model-value="gapTarget !== null"
      :title="gapDialogTitle"
      :description="gapDialogDescription"
      :confirm-text="gapTarget?.action === 'repair' ? '确认修复' : '确认回填'"
      :loading="gapLoading"
      @update:model-value="!$event && (gapTarget = null)"
      @submit="submitGapRecovery"
    />
  </section>
</template>

<style scoped>
.coverage-filter {
  margin-bottom: 8px;
}

.coverage-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.coverage-summary__item {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  min-height: 64px;
  padding: 12px 16px;
  border: 1px solid var(--el-border-color-light);
  border-left: 4px solid var(--el-color-info);
  border-radius: var(--el-border-radius-base);
  color: inherit;
  font: inherit;
  text-align: left;
  background: var(--el-fill-color-blank);
  cursor: pointer;
  transition:
    border-color 0.2s ease,
    box-shadow 0.2s ease,
    transform 0.2s ease;
}

.coverage-summary__item:hover:not(:disabled) {
  border-color: var(--el-color-primary-light-5);
  transform: translateY(-1px);
}

.coverage-summary__item:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}

.coverage-summary__item.is-active {
  border-color: var(--el-color-primary-light-3);
  box-shadow: 0 0 0 2px var(--el-color-primary-light-8);
}

.coverage-summary__item:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.coverage-summary__item strong {
  grid-row: 1 / span 2;
  grid-column: 2;
  font-size: 24px;
  font-variant-numeric: tabular-nums;
}

.coverage-summary__item small {
  margin-top: 4px;
  color: var(--app-text-secondary);
  font-size: 11px;
}

.coverage-summary__item--success {
  border-left-color: var(--el-color-success);
}

.coverage-summary__item--danger {
  border-left-color: var(--el-color-danger);
}

.coverage-summary__item--warning {
  border-left-color: var(--el-color-warning);
}

.missing-datasets {
  display: grid;
  gap: 8px;
}

@media (max-width: 900px) {
  .coverage-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .coverage-summary {
    grid-template-columns: 1fr;
  }
}
</style>
