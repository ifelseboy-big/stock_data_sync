<script setup lang="ts">
import { Collection, Connection, SetUp, Warning } from '@element-plus/icons-vue'
import { computed } from 'vue'

import DataState from '@/components/DataState.vue'
import MetricCard from '@/components/MetricCard.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useApiResource } from '@/composables/useApiResource'
import { getOperationsOverview } from '@/modules/operations/api'
import { formatDateTime, formatDuration, formatPercent } from '@/modules/operations/presentation'

const { data, loading, error, load } = useApiResource(getOperationsOverview)

const quotaPercent = computed(() => {
  if (!data.value?.quota?.limitPerMinute) return 0
  return Math.min(
    100,
    (data.value.quota.usedInCurrentWindow / data.value.quota.limitPerMinute) * 100,
  )
})
</script>

<template>
  <section aria-labelledby="overview-heading">
    <PageHeader title="运行概览" description="聚合采集批次、全局串行加工、接口额度和异常信息。">
      <template #actions>
        <span class="last-updated">数据时间：{{ formatDateTime(data?.generatedAt) }}</span>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <DataState :loading="loading" :error="error" @retry="load">
      <div class="summary-grid">
        <MetricCard
          label="采集中批次"
          :value="data?.metrics.collectingBatchCount"
          note="尚未关闭的采集批次"
          :icon="Collection"
        />
        <MetricCard
          label="加工中任务"
          :value="data?.metrics.processingTaskCount"
          note="全局执行槽位最多 1 个"
          :icon="SetUp"
          tone="success"
        />
        <MetricCard
          label="阻塞任务"
          :value="data?.metrics.blockedTaskCount"
          note="必要依赖尚未就绪"
          :icon="Warning"
          tone="danger"
        />
        <MetricCard
          label="今日任务成功率"
          :value="formatPercent(data?.metrics.taskSuccessRateToday)"
          note="采集与加工终态任务"
          :icon="SetUp"
          tone="success"
        />
        <MetricCard
          label="今日接口请求成功率"
          :value="formatPercent(data?.metrics.providerSuccessRateToday)"
          :note="`仅统计接口请求 · P95 ${formatDuration(data?.metrics.providerP95DurationMs)}`"
          :icon="Connection"
          tone="warning"
        />
      </div>

      <div class="dashboard-grid">
        <el-card shadow="never" class="panel-card">
          <template #header>
            <div class="panel-card__header">
              <div>
                <h3>当前加工槽位</h3>
                <p>加工任务全局串行，同一时间仅运行一个。</p>
              </div>
              <StatusTag v-if="data?.currentProcessing" :status="data.currentProcessing.status" />
            </div>
          </template>
          <div v-if="data?.currentProcessing" class="current-task">
            <strong>{{ data.currentProcessing.taskName }}</strong>
            <dl class="definition-grid">
              <div>
                <dt>批次</dt>
                <dd>{{ data.currentProcessing.batchCode }}</dd>
              </div>
              <div>
                <dt>数据周期</dt>
                <dd>{{ data.currentProcessing.dataCycle }}</dd>
              </div>
              <div>
                <dt>依赖数</dt>
                <dd>{{ data.currentProcessing.dependencyCount }}</dd>
              </div>
              <div>
                <dt>开始时间</dt>
                <dd>{{ formatDateTime(data.currentProcessing.startedAt) }}</dd>
              </div>
            </dl>
          </div>
          <el-empty v-else description="当前没有加工任务占用执行槽位" :image-size="72" />
        </el-card>

        <el-card shadow="never" class="panel-card">
          <template #header>
            <div class="panel-card__header">
              <div>
                <h3>Tushare 请求额度</h3>
                <p>最近 60 秒窗口，额度由服务端配置提供。</p>
              </div>
            </div>
          </template>
          <div v-if="data?.quota" class="quota-panel">
            <div class="quota-panel__value">
              <strong>{{ data.quota.usedInCurrentWindow }}</strong>
              <span>/ {{ data.quota.limitPerMinute }} 次</span>
            </div>
            <el-progress :percentage="quotaPercent" :stroke-width="10" :show-text="false" />
            <div class="quota-panel__meta">
              <span>剩余 {{ data.quota.remainingInCurrentWindow }}</span>
              <span>发生限流等待的请求 {{ data.quota.delayedRequestCount }}</span>
            </div>
          </div>
          <el-empty v-else description="尚未上报接口额度" :image-size="72" />
        </el-card>
      </div>

      <el-card shadow="never" class="panel-card panel-card--table">
        <template #header>
          <div class="panel-card__header">
            <div>
              <h3>最近采集批次</h3>
              <p>批次完成后才会统一生成加工计划。</p>
            </div>
            <RouterLink to="/acquisition">查看全部</RouterLink>
          </div>
        </template>
        <el-table :data="data?.recentBatches ?? []" empty-text="暂无采集批次">
          <el-table-column prop="batchCode" label="批次" min-width="170" />
          <el-table-column prop="themeName" label="业务主题" min-width="140" />
          <el-table-column prop="dataCycle" label="数据周期" min-width="120" />
          <el-table-column label="进度" min-width="160">
            <template #default="{ row }">
              {{ row.succeededTaskCount }} / {{ row.taskCount }}
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }"><StatusTag :status="row.status" /></template>
          </el-table-column>
          <el-table-column label="关闭时间" min-width="170">
            <template #default="{ row }">{{ formatDateTime(row.closedAt) }}</template>
          </el-table-column>
        </el-table>
      </el-card>
    </DataState>
  </section>
</template>
