<script setup lang="ts">
import { computed } from 'vue'

import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useApiResource } from '@/composables/useApiResource'
import { getProcessingQueue } from '@/modules/operations/api'
import { formatDateTime, formatDuration, formatPriority } from '@/modules/operations/presentation'

const { data, loading, error, load } = useApiResource(getProcessingQueue)
const currentTask = computed(() => data.value?.find((item) => item.status === 'running'))
const waitingTasks = computed(() => data.value?.filter((item) => item.status !== 'running') ?? [])
</script>

<template>
  <section>
    <PageHeader title="加工队列" description="加工任务全局串行；重试退避不占用唯一执行槽位。">
      <template #actions><el-button :loading="loading" @click="load">刷新</el-button></template>
    </PageHeader>

    <el-card shadow="never" class="execution-slot">
      <div class="execution-slot__label">全局执行槽位</div>
      <div v-if="currentTask" class="execution-slot__content">
        <div>
          <strong>{{ currentTask.taskName }}</strong>
          <p>{{ currentTask.batchCode }} · {{ currentTask.dataCycle }}</p>
        </div>
        <div class="execution-slot__meta">
          <StatusTag :status="currentTask.status" />
          <span>开始于 {{ formatDateTime(currentTask.startedAt) }}</span>
          <span>已运行 {{ formatDuration(currentTask.durationMs) }}</span>
        </div>
      </div>
      <div v-else class="execution-slot__empty">空闲，等待可执行任务进入队列</div>
    </el-card>

    <el-card shadow="never" class="panel-card panel-card--table">
      <template #header>
        <div class="panel-card__header">
          <div>
            <h3>等待队列</h3>
            <p>优先级相同时按计划生成时间排序。</p>
          </div>
          <el-tag type="info">{{ waitingTasks.length }} 个任务</el-tag>
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
        <el-table :data="waitingTasks">
          <el-table-column prop="queuePosition" label="#" width="60" />
          <el-table-column prop="taskName" label="加工任务" min-width="180" />
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
        </el-table>
      </DataState>
    </el-card>
  </section>
</template>
