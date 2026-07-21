<script setup lang="ts">
import { ArrowRight } from '@element-plus/icons-vue'
import type { Component } from 'vue'
import { RouterLink, type RouteLocationRaw } from 'vue-router'

withDefaults(
  defineProps<{
    label: string
    value?: string | number
    note?: string
    icon: Component
    tone?: 'primary' | 'success' | 'warning' | 'danger'
    to?: RouteLocationRaw
    actionLabel?: string
  }>(),
  { value: undefined, note: '', tone: 'primary', to: undefined, actionLabel: '查看详情' },
)
</script>

<template>
  <component
    :is="to ? RouterLink : 'div'"
    v-bind="to ? { to } : {}"
    class="summary-card-shell"
    :class="{ 'summary-card-link': to }"
    :aria-label="to ? `${label}，${actionLabel}` : undefined"
  >
    <el-card shadow="never" class="summary-card">
      <div class="summary-card__topline">
        <span>{{ label }}</span>
        <span class="summary-card__icon" :class="`summary-card__icon--${tone}`">
          <el-icon :size="18"><component :is="icon" /></el-icon>
        </span>
      </div>
      <strong>{{ value ?? '--' }}</strong>
      <p v-if="note">{{ note }}</p>
      <span v-if="to" class="summary-card__action">
        {{ actionLabel }}
        <el-icon><ArrowRight /></el-icon>
      </span>
    </el-card>
  </component>
</template>
