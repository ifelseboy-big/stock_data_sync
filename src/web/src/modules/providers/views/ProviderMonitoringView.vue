<script setup lang="ts">
import { computed } from 'vue'

import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import { useApiResource } from '@/composables/useApiResource'
import { getProviderMonitoring } from '@/modules/operations/api'
import { formatDateTime, formatDuration, formatPercent } from '@/modules/operations/presentation'

const { data, loading, error, load } = useApiResource(getProviderMonitoring)
const quotaPercent = computed(() => {
  if (!data.value?.quota?.limitPerMinute) return 0
  return Math.min(
    100,
    (data.value.quota.usedInCurrentWindow / data.value.quota.limitPerMinute) * 100,
  )
})
</script>

<template>
  <section>
    <PageHeader title="接口监控" description="观察 Tushare 请求额度、限流等待、成功率与响应耗时。">
      <template #actions><el-button :loading="loading" @click="load">刷新</el-button></template>
    </PageHeader>

    <DataState :loading="loading" :error="error" @retry="load">
      <el-card shadow="never" class="quota-card">
        <div v-if="data?.quota" class="quota-card__layout">
          <div>
            <span class="eyebrow">最近 60 秒请求窗口</span>
            <div class="quota-card__number">
              <strong>{{ data.quota.usedInCurrentWindow }}</strong>
              <span>/ {{ data.quota.limitPerMinute }} 次</span>
            </div>
          </div>
          <div class="quota-card__progress">
            <el-progress :percentage="quotaPercent" :stroke-width="12" />
            <div class="quota-panel__meta">
              <span>剩余额度 {{ data.quota.remainingInCurrentWindow }}</span>
              <span>限流等待 {{ data.quota.delayedRequestCount }}</span>
              <span>采集时间 {{ formatDateTime(data.quota.capturedAt) }}</span>
            </div>
          </div>
        </div>
        <el-empty v-else description="尚未上报请求额度" :image-size="72" />
      </el-card>

      <el-card shadow="never" class="panel-card panel-card--table">
        <template #header>
          <div class="panel-card__header">
            <div>
              <h3>接口明细</h3>
              <p>耗时不包含限流器中的排队等待。</p>
            </div>
          </div>
        </template>
        <el-table :data="data?.endpoints ?? []" empty-text="暂无接口调用数据">
          <el-table-column prop="endpoint" label="接口" min-width="170" fixed="left" />
          <el-table-column prop="requestCountToday" label="今日请求" width="110" />
          <el-table-column label="成功率" width="110">
            <template #default="{ row }">{{ formatPercent(row.successRateToday) }}</template>
          </el-table-column>
          <el-table-column label="P50 耗时" width="120">
            <template #default="{ row }">{{ formatDuration(row.p50DurationMs) }}</template>
          </el-table-column>
          <el-table-column label="P95 耗时" width="120">
            <template #default="{ row }">{{ formatDuration(row.p95DurationMs) }}</template>
          </el-table-column>
          <el-table-column prop="throttledCountToday" label="限流等待" width="110" />
          <el-table-column prop="emptyResponseCountToday" label="空响应" width="100" />
          <el-table-column label="最近调用" min-width="170">
            <template #default="{ row }">{{ formatDateTime(row.lastRequestedAt) }}</template>
          </el-table-column>
        </el-table>
      </el-card>
    </DataState>
  </section>
</template>
