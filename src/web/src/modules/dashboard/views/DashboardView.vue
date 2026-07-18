<script setup lang="ts">
import { Connection, DataLine, Timer } from '@element-plus/icons-vue'
import { onMounted, ref } from 'vue'

import { getLiveness } from '@/modules/system/api'

const checking = ref(true)
const serverOnline = ref(false)

onMounted(async () => {
  try {
    await getLiveness()
    serverOnline.value = true
  } catch {
    serverOnline.value = false
  } finally {
    checking.value = false
  }
})
</script>

<template>
  <section aria-labelledby="overview-heading">
    <div class="section-heading">
      <div>
        <h2 id="overview-heading">工程状态</h2>
        <p>当前页面用于确认前后端脚手架已正确连接。</p>
      </div>
      <el-tag v-if="checking" type="info">检测中</el-tag>
      <el-tag v-else :type="serverOnline ? 'success' : 'danger'">
        {{ serverOnline ? '后端在线' : '后端未连接' }}
      </el-tag>
    </div>

    <div class="metric-grid">
      <el-card shadow="never">
        <div class="metric-card">
          <el-icon :size="24"><DataLine /></el-icon>
          <div><span>数据同步任务</span><strong>待设计</strong></div>
        </div>
      </el-card>
      <el-card shadow="never">
        <div class="metric-card">
          <el-icon :size="24"><Timer /></el-icon>
          <div><span>调度执行引擎</span><strong>APScheduler</strong></div>
        </div>
      </el-card>
      <el-card shadow="never">
        <div class="metric-card">
          <el-icon :size="24"><Connection /></el-icon>
          <div><span>运维数据存储</span><strong>PostgreSQL</strong></div>
        </div>
      </el-card>
    </div>
  </section>
</template>
