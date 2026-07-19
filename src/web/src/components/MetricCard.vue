<script setup lang="ts">
import type { Component } from 'vue'

withDefaults(
  defineProps<{
    label: string
    value?: string | number
    note?: string
    icon: Component
    tone?: 'primary' | 'success' | 'warning' | 'danger'
  }>(),
  { value: undefined, note: '', tone: 'primary' },
)
</script>

<template>
  <el-card shadow="never" class="summary-card">
    <div class="summary-card__topline">
      <span>{{ label }}</span>
      <span class="summary-card__icon" :class="`summary-card__icon--${tone}`">
        <el-icon :size="18"><component :is="icon" /></el-icon>
      </span>
    </div>
    <strong>{{ value ?? '--' }}</strong>
    <p v-if="note">{{ note }}</p>
  </el-card>
</template>
