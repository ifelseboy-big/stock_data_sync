<script setup lang="ts">
import { computed, ref } from 'vue'

import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useApiResource } from '@/composables/useApiResource'
import { getDependencies } from '@/modules/operations/api'

const keyword = ref('')
const { data, loading, error, load } = useApiResource(getDependencies)

const filteredItems = computed(() => {
  const value = keyword.value.trim().toLowerCase()
  if (!value) return data.value ?? []
  return (data.value ?? []).filter((item) =>
    [item.processingTaskName, item.batchCode, item.sourceEndpoint].some((field) =>
      field.toLowerCase().includes(value),
    ),
  )
})
</script>

<template>
  <section>
    <PageHeader title="依赖观测" description="检查每个加工任务的全部必要依赖及其原始数据就绪状态。">
      <template #actions><el-button :loading="loading" @click="load">刷新</el-button></template>
    </PageHeader>

    <el-card shadow="never" class="filter-card">
      <el-input
        v-model="keyword"
        clearable
        placeholder="搜索加工任务、批次或来源接口"
        aria-label="搜索依赖"
        style="max-width: 380px"
      />
    </el-card>

    <el-card shadow="never" class="panel-card panel-card--table">
      <DataState
        :loading="loading"
        :error="error"
        :empty="filteredItems.length === 0"
        empty-title="暂无依赖记录"
        empty-description="加工计划生成后会建立必要依赖检查记录。"
        @retry="load"
      >
        <el-table :data="filteredItems" row-key="id">
          <el-table-column
            prop="processingTaskName"
            label="加工任务"
            min-width="180"
            fixed="left"
          />
          <el-table-column prop="batchCode" label="批次" min-width="170" />
          <el-table-column prop="sourceEndpoint" label="原始数据接口" min-width="150" />
          <el-table-column prop="sourceCycle" label="来源周期" min-width="120" />
          <el-table-column label="取数策略" width="130">
            <template #default="{ row }">
              {{ row.sourcePolicy === 'current_cycle' ? '当前周期' : '最新有效数据' }}
            </template>
          </el-table-column>
          <el-table-column label="来源就绪" width="100">
            <template #default="{ row }">
              <el-tag :type="row.sourceReady ? 'success' : 'danger'">
                {{ row.sourceReady ? '已就绪' : '未就绪' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="任务状态" width="110">
            <template #default="{ row }"><StatusTag :status="row.status" /></template>
          </el-table-column>
          <el-table-column prop="reason" label="未就绪原因" min-width="220" show-overflow-tooltip />
        </el-table>
      </DataState>
    </el-card>
  </section>
</template>
