<script setup lang="ts">
import { computed, ref } from 'vue'

import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import { useApiResource } from '@/composables/useApiResource'
import { getDatasetReleaseCoverage, getDatasetReleases } from '@/modules/operations/api'
import type { DatasetReleaseCoverageItem } from '@/modules/operations/contracts'
import { formatDateTime } from '@/modules/operations/presentation'

const datasetName = ref('')
const page = ref(1)

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
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" native-type="submit" :loading="coverageLoading">
            检查完整性
          </el-button>
        </el-form-item>
      </el-form>

      <div class="coverage-summary" aria-label="数据完整性汇总">
        <div class="coverage-summary__item">
          <span>交易日</span><strong>{{ coverageSummary.tradingDayCount }}</strong>
        </div>
        <div class="coverage-summary__item coverage-summary__item--success">
          <span>完整</span><strong>{{ coverageSummary.completeCount }}</strong>
        </div>
        <div class="coverage-summary__item coverage-summary__item--danger">
          <span>存在缺失</span><strong>{{ coverageSummary.missingCount }}</strong>
        </div>
        <div class="coverage-summary__item coverage-summary__item--warning">
          <span>今日进行中</span><strong>{{ coverageSummary.pendingCount }}</strong>
        </div>
      </div>

      <DataState
        :loading="coverageLoading"
        :error="coverageError"
        :empty="coverage?.length === 0"
        empty-title="所选范围内没有交易日"
        empty-description="请调整日期范围后重新检查。"
        @retry="loadCoverage"
      >
        <el-table :data="coverage ?? []">
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
              <span
                v-if="row.missingDatasets.length"
                class="missing-datasets"
                :title="missingDatasetText(row)"
              >
                {{ row.missingDatasetDisplayNames.join('、') }}
              </span>
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
          <el-table-column prop="datasetName" label="数据集" min-width="190" fixed="left" />
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
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 64px;
  padding: 12px 16px;
  border: 1px solid var(--el-border-color-light);
  border-left: 4px solid var(--el-color-info);
  border-radius: var(--el-border-radius-base);
}

.coverage-summary__item strong {
  font-size: 24px;
  font-variant-numeric: tabular-nums;
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
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
