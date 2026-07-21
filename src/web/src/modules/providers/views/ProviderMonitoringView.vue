<script setup lang="ts">
import { computed } from 'vue'

import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceLabel from '@/components/ResourceLabel.vue'
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
    <PageHeader
      title="接口用量与质量"
      description="按单次 Tushare 请求查看调用量、成功率、耗时和本地安全上限；业务日期是否采集完成请查看“采集批次”。"
    >
      <template #actions><el-button :loading="loading" @click="load">刷新</el-button></template>
    </PageHeader>

    <DataState :loading="loading" :error="error" @retry="load">
      <el-card shadow="never" class="quota-card">
        <div v-if="data?.quota" class="quota-card__layout">
          <div>
            <span class="eyebrow">最近 60 秒请求量 / 本地安全上限</span>
            <div class="quota-card__number">
              <strong>{{ data.quota.usedInCurrentWindow }}</strong>
              <span>/ {{ data.quota.limitPerMinute }} 次</span>
            </div>
          </div>
          <div class="quota-card__progress">
            <el-progress :percentage="quotaPercent" :stroke-width="12" />
            <div class="quota-panel__meta">
              <span>本地预算余量 {{ data.quota.remainingInCurrentWindow }}</span>
              <span>平滑排队请求 {{ data.quota.delayedRequestCount }}（主动保护）</span>
              <span>采集时间 {{ formatDateTime(data.quota.capturedAt) }}</span>
            </div>
            <p class="quota-panel__hint">
              低于上限不等于采集线程空闲；任务选取、结果校验和原始数据落盘期间不会产生接口请求。
            </p>
          </div>
        </div>
        <el-empty v-else description="尚未上报本地请求预算" :image-size="72" />
      </el-card>

      <el-card shadow="never" class="panel-card panel-card--table">
        <template #header>
          <div class="panel-card__header">
            <div>
              <h3>接口明细</h3>
              <p>今日请求为累计值；耗时不包含限流器中的排队等待。</p>
            </div>
          </div>
        </template>
        <el-table :data="data?.endpoints ?? []" empty-text="暂无接口调用数据" scrollbar-always-on>
          <el-table-column label="接口" min-width="230" fixed="left">
            <template #default="{ row }">
              <ResourceLabel
                :display-name="row.endpointDisplayName"
                :identifier="row.endpoint"
                :title="row.endpointDescription"
              />
            </template>
          </el-table-column>
          <el-table-column prop="requestCountToday" label="今日请求" width="110" />
          <el-table-column label="请求成功率" width="120">
            <template #default="{ row }">{{ formatPercent(row.successRateToday) }}</template>
          </el-table-column>
          <el-table-column label="P50 耗时" width="120">
            <template #default="{ row }">{{ formatDuration(row.p50DurationMs) }}</template>
          </el-table-column>
          <el-table-column label="P95 耗时" width="120">
            <template #default="{ row }">{{ formatDuration(row.p95DurationMs) }}</template>
          </el-table-column>
          <el-table-column prop="throttledCountToday" label="平滑排队请求" width="130" />
          <el-table-column prop="emptyResponseCountToday" label="空响应" width="100" />
          <el-table-column label="最近调用" min-width="170">
            <template #default="{ row }">{{ formatDateTime(row.lastRequestedAt) }}</template>
          </el-table-column>
        </el-table>
      </el-card>
    </DataState>
  </section>
</template>
