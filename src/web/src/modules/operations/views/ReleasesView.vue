<script setup lang="ts">
import { ref } from 'vue'

import DataState from '@/components/DataState.vue'
import PageHeader from '@/components/PageHeader.vue'
import { useApiResource } from '@/composables/useApiResource'
import { getDatasetReleases } from '@/modules/operations/api'
import { formatDateTime } from '@/modules/operations/presentation'

const datasetName = ref('')
const page = ref(1)

const { data, loading, error, load } = useApiResource(() =>
  getDatasetReleases({
    datasetName: datasetName.value || undefined,
    page: page.value,
    pageSize: 20,
  }),
)

function search() {
  page.value = 1
  void load()
}
</script>

<template>
  <section>
    <PageHeader title="数据发布" description="查看各数据集当前可消费范围、处理版本和发布行数。">
      <template #actions><el-button :loading="loading" @click="load">刷新</el-button></template>
    </PageHeader>

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
        <el-table :data="data?.items ?? []">
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
