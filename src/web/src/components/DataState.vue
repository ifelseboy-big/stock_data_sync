<script setup lang="ts">
withDefaults(
  defineProps<{
    loading: boolean
    error?: string
    empty?: boolean
    emptyTitle?: string
    emptyDescription?: string
  }>(),
  {
    error: '',
    empty: false,
    emptyTitle: '暂无数据',
    emptyDescription: '当前筛选条件下没有运行数据。',
  },
)

defineEmits<{ retry: [] }>()
</script>

<template>
  <div v-if="loading" class="data-state" aria-live="polite">
    <el-skeleton :rows="6" animated />
  </div>
  <el-result v-else-if="error" icon="error" title="数据加载失败" :sub-title="error">
    <template #extra>
      <el-button type="primary" @click="$emit('retry')">重新加载</el-button>
    </template>
  </el-result>
  <el-empty v-else-if="empty" :description="emptyTitle">
    <p class="data-state__description">{{ emptyDescription }}</p>
  </el-empty>
  <slot v-else />
</template>
