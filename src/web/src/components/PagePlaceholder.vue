<script setup lang="ts">
import type { Component } from 'vue'

defineProps<{
  title: string
  description: string
  icon: Component
}>()
</script>

<template>
  <section class="placeholder-card" :aria-labelledby="`${title}-title`">
    <el-icon class="placeholder-card__icon" :size="36"><component :is="icon" /></el-icon>
    <h2 :id="`${title}-title`">{{ title }}</h2>
    <p>{{ description }}</p>
    <el-tag type="info" effect="plain">等待业务设计</el-tag>
  </section>
</template>
