<script setup lang="ts">
withDefaults(
  defineProps<{
    displayName: string
    identifier: string
    description?: string
  }>(),
  { description: '' },
)
</script>

<template>
  <div class="resource-label">
    <strong>{{ displayName || identifier }}</strong>
    <code v-if="identifier">{{ identifier }}</code>
    <span v-if="description">{{ description }}</span>
  </div>
</template>

<style scoped>
.resource-label {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.resource-label strong,
.resource-label code,
.resource-label span {
  overflow: hidden;
  text-overflow: ellipsis;
}

.resource-label strong {
  color: var(--el-text-color-primary);
  line-height: 1.4;
  white-space: normal;
}

.resource-label.resource-label--prominent strong {
  font-size: 18px;
}

.resource-label code {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.4;
  white-space: nowrap;
}

.resource-label span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.45;
  white-space: normal;
}
</style>
