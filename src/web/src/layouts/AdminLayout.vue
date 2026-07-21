<script setup lang="ts">
import {
  Bell,
  Collection,
  Connection,
  DataAnalysis,
  DataBoard,
  Document,
  Fold,
  Link,
  Menu as MenuIcon,
  Monitor,
  SetUp,
  Timer,
} from '@element-plus/icons-vue'
import { breakpointsTailwind, useBreakpoints } from '@vueuse/core'
import { storeToRefs } from 'pinia'
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'

import { useAppStore } from '@/stores/app'

const route = useRoute()
const appStore = useAppStore()
const { sidebarCollapsed } = storeToRefs(appStore)
const activeMenu = computed(() => route.path)
const isMobile = useBreakpoints(breakpointsTailwind).smaller('md')

watch(isMobile, (mobile) => appStore.setSidebarCollapsed(mobile), { immediate: true })
</script>

<template>
  <el-container class="admin-shell">
    <el-aside :width="sidebarCollapsed ? '72px' : '224px'" class="sidebar">
      <div class="brand" :class="{ 'brand--collapsed': sidebarCollapsed }">
        <span class="brand__mark" aria-hidden="true">S</span>
        <span v-show="!sidebarCollapsed" class="brand__name">Stock Sync</span>
      </div>

      <nav aria-label="主导航">
        <el-menu
          :default-active="activeMenu"
          :default-openeds="['runtime', 'observability']"
          :collapse="sidebarCollapsed"
          :collapse-transition="false"
          router
        >
          <el-sub-menu index="runtime">
            <template #title>
              <el-icon><DataAnalysis /></el-icon>
              <span>运行中心</span>
            </template>
            <el-menu-item index="/">运行概览</el-menu-item>
            <el-menu-item index="/acquisition">
              <el-icon><Collection /></el-icon>
              <template #title>采集批次</template>
            </el-menu-item>
            <el-menu-item index="/processing">
              <el-icon><SetUp /></el-icon>
              <template #title>加工队列</template>
            </el-menu-item>
            <el-menu-item index="/dependencies">
              <el-icon><Link /></el-icon>
              <template #title>数据就绪检查</template>
            </el-menu-item>
            <el-menu-item index="/releases">
              <el-icon><DataBoard /></el-icon>
              <template #title>数据发布</template>
            </el-menu-item>
          </el-sub-menu>
          <el-sub-menu index="observability">
            <template #title>
              <el-icon><Monitor /></el-icon>
              <span>观测中心</span>
            </template>
            <el-menu-item index="/schedules">
              <el-icon><Timer /></el-icon>
              <template #title>任务调度</template>
            </el-menu-item>
            <el-menu-item index="/providers">
              <el-icon><Connection /></el-icon>
              <template #title>接口用量与质量</template>
            </el-menu-item>
            <el-menu-item index="/runs">
              <el-icon><Document /></el-icon>
              <template #title>运行记录</template>
            </el-menu-item>
            <el-menu-item index="/alerts">
              <el-icon><Bell /></el-icon>
              <template #title>告警中心</template>
            </el-menu-item>
            <el-menu-item index="/system">
              <el-icon><Monitor /></el-icon>
              <template #title>系统运维</template>
            </el-menu-item>
          </el-sub-menu>
        </el-menu>
      </nav>
    </el-aside>

    <el-container class="content-shell">
      <el-header class="topbar">
        <el-button
          text
          class="topbar__toggle"
          :aria-label="sidebarCollapsed ? '展开导航' : '收起导航'"
          :aria-expanded="!sidebarCollapsed"
          @click="appStore.toggleSidebar"
        >
          <el-icon :size="20">
            <MenuIcon v-if="sidebarCollapsed" />
            <Fold v-else />
          </el-icon>
        </el-button>
        <div>
          <h1 class="topbar__title">{{ route.meta.title }}</h1>
          <p class="topbar__subtitle">股票数据同步运维管理平台</p>
        </div>
      </el-header>

      <el-main id="main-content" class="main-content" tabindex="-1">
        <RouterView />
      </el-main>
    </el-container>
  </el-container>
</template>
