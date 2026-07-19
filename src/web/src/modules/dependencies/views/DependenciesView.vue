<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useApiResource } from '@/composables/useApiResource'
import { getDependencies } from '@/modules/operations/api'
import type {
  DependencyItem,
  DependencySourceSummary,
  ReadinessFilter,
  ReadinessStatus,
} from '@/modules/operations/contracts'

const route = useRoute()
const router = useRouter()
const keyword = ref(typeof route.query.query === 'string' ? route.query.query : '')
const readiness = ref<ReadinessFilter>(
  ['attention', 'waiting', 'blocked', 'ready', 'all'].includes(String(route.query.readiness))
    ? (route.query.readiness as ReadinessFilter)
    : 'attention',
)
const page = ref(1)
const { data, loading, error, load } = useApiResource(() =>
  getDependencies({
    query: keyword.value.trim() || undefined,
    readiness: readiness.value,
    page: page.value,
    pageSize: 20,
  }),
)

const currentPageCounts = computed(() => {
  const items = data.value?.items ?? []
  return {
    blocked: items.filter((item) => item.readinessStatus === 'blocked').length,
    waiting: items.filter((item) => item.readinessStatus === 'waiting').length,
    ready: items.filter((item) => item.readinessStatus === 'ready').length,
  }
})

const readinessMap: Record<
  ReadinessStatus,
  { label: string; type: 'success' | 'warning' | 'danger' }
> = {
  ready: { label: '全部就绪', type: 'success' },
  waiting: { label: '等待数据', type: 'warning' },
  blocked: { label: '存在缺失', type: 'danger' },
}

function search() {
  page.value = 1
  void load()
}

function openProcessing(value: unknown) {
  const item = value as DependencyItem
  void router.push({ path: '/processing', query: { datasetName: item.processingTaskName } })
}

function sourceCountText(value: unknown) {
  const source = value as DependencySourceSummary
  if (source.status === 'ready') return `${source.readyCount} / ${source.requiredCount} 已就绪`
  if (source.status === 'blocked') return `${source.blockedCount} 项缺失或失败`
  return `${source.waitingCount} 项仍在等待`
}
</script>

<template>
  <section>
    <PageHeader
      title="数据就绪检查"
      description="按加工任务汇总全部前置数据，直接查看哪些任务可以执行、哪些任务缺少数据。"
    >
      <template #actions><el-button :loading="loading" @click="load">刷新</el-button></template>
    </PageHeader>

    <div class="readiness-summary" aria-label="当前页数据就绪概况">
      <div class="readiness-summary__item readiness-summary__item--danger">
        <span>存在缺失</span><strong>{{ currentPageCounts.blocked }}</strong>
      </div>
      <div class="readiness-summary__item readiness-summary__item--warning">
        <span>等待数据</span><strong>{{ currentPageCounts.waiting }}</strong>
      </div>
      <div class="readiness-summary__item readiness-summary__item--success">
        <span>全部就绪</span><strong>{{ currentPageCounts.ready }}</strong>
      </div>
    </div>

    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" @submit.prevent="search">
        <el-form-item label="加工任务或前置数据">
          <el-input
            v-model="keyword"
            clearable
            placeholder="输入中文名称、任务代码或批次号"
            aria-label="搜索加工任务或前置数据"
            style="width: 320px"
          />
        </el-form-item>
        <el-form-item label="就绪情况">
          <el-select v-model="readiness" style="width: 170px">
            <el-option label="需要处理" value="attention" />
            <el-option label="存在缺失" value="blocked" />
            <el-option label="等待数据" value="waiting" />
            <el-option label="全部就绪" value="ready" />
            <el-option label="全部任务" value="all" />
          </el-select>
        </el-form-item>
        <el-form-item><el-button type="primary" native-type="submit">查询</el-button></el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="panel-card panel-card--table">
      <template #header>
        <div class="panel-card__header">
          <div>
            <h3>加工任务前置数据</h3>
            <p>默认只展示尚未全部就绪的任务；展开任务可查看各类前置数据汇总。</p>
          </div>
          <el-tag type="info">{{ data?.total ?? 0 }} 个任务</el-tag>
        </div>
      </template>
      <DataState
        :loading="loading"
        :error="error"
        :empty="data?.items.length === 0"
        empty-title="当前没有需要处理的数据依赖"
        empty-description="可以切换为“全部任务”查看已经就绪的加工任务。"
        @retry="load"
      >
        <el-table :data="data?.items ?? []" row-key="id" scrollbar-always-on>
          <el-table-column type="expand" width="48">
            <template #default="{ row }">
              <div class="source-detail">
                <div class="source-detail__header">
                  <strong>前置数据明细</strong>
                  <span>相同接口或数据集的多个采集范围已经合并统计。</span>
                </div>
                <el-table :data="row.sources" size="small">
                  <el-table-column label="前置数据" min-width="230">
                    <template #default="{ row: source }">
                      <div class="source-name">
                        <strong>{{ source.sourceDisplayName }}</strong>
                        <code>{{ source.sourceName }}</code>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="来源类型" width="120">
                    <template #default="{ row: source }">
                      {{ source.sourceType === 'raw_asset' ? '原始采集数据' : '已发布数据' }}
                    </template>
                  </el-table-column>
                  <el-table-column label="数量" width="170">
                    <template #default="{ row: source }">{{ sourceCountText(source) }}</template>
                  </el-table-column>
                  <el-table-column label="状态" width="110">
                    <template #default="{ row: source }">
                      <el-tag :type="readinessMap[source.status as ReadinessStatus].type">
                        {{ readinessMap[source.status as ReadinessStatus].label }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column
                    prop="reason"
                    label="未就绪原因"
                    min-width="240"
                    show-overflow-tooltip
                  />
                </el-table>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="加工任务" min-width="280" fixed="left">
            <template #default="{ row }">
              <div class="task-name">
                <strong>{{ row.processingTaskDisplayName }}</strong>
                <span>{{ row.processingTaskDescription }}</span>
                <code>{{ row.processingTaskName }}</code>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="dataCycle" label="数据周期" width="120" />
          <el-table-column label="前置数据进度" min-width="210">
            <template #default="{ row }">
              <strong class="numeric">
                {{ row.readyDependencyCount }} / {{ row.dependencyCount }} 已就绪
              </strong>
              <div v-if="row.blockedDependencyCount" class="dependency-hint text-danger">
                {{ row.blockedDependencyCount }} 项缺失或失败
              </div>
              <div v-else-if="row.waitingDependencyCount" class="dependency-hint">
                {{ row.waitingDependencyCount }} 项仍在等待
              </div>
            </template>
          </el-table-column>
          <el-table-column label="就绪情况" width="120">
            <template #default="{ row }">
              <el-tag :type="readinessMap[row.readinessStatus as ReadinessStatus].type">
                {{ readinessMap[row.readinessStatus as ReadinessStatus].label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="加工状态" width="110">
            <template #default="{ row }"><StatusTag :status="row.processingStatus" /></template>
          </el-table-column>
          <el-table-column prop="reason" label="阻塞原因" min-width="220" show-overflow-tooltip />
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button size="small" link type="primary" @click="openProcessing(row)">
                查看加工任务
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
  </section>
</template>

<style scoped>
.readiness-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.readiness-summary__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 64px;
  padding: 12px 16px;
  border: 1px solid var(--el-border-color-light);
  border-left-width: 4px;
  border-radius: var(--el-border-radius-base);
  background: var(--el-bg-color);
}

.readiness-summary__item strong {
  font-size: 24px;
  font-variant-numeric: tabular-nums;
}

.readiness-summary__item--danger {
  border-left-color: var(--el-color-danger);
}

.readiness-summary__item--warning {
  border-left-color: var(--el-color-warning);
}

.readiness-summary__item--success {
  border-left-color: var(--el-color-success);
}

.task-name,
.source-name {
  display: grid;
  gap: 3px;
}

.task-name span,
.source-detail__header span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.45;
}

.task-name code,
.source-name code {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.dependency-hint {
  margin-top: 4px;
  color: var(--el-color-warning-dark-2);
  font-size: 12px;
}

.source-detail {
  padding: 12px 20px 20px 48px;
  background: var(--el-fill-color-lighter);
}

.source-detail__header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 10px;
}

@media (max-width: 768px) {
  .readiness-summary {
    grid-template-columns: 1fr;
  }
}
</style>
