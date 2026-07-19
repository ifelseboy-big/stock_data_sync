<script setup lang="ts">
import { Calendar, Connection, Monitor, SetUp } from '@element-plus/icons-vue'

import PageHeader from '@/components/PageHeader.vue'
import { useApiResource } from '@/composables/useApiResource'
import { getLiveness, getReadiness, getSystemResources } from '@/modules/system/api'

const {
  data: livenessData,
  loading: livenessLoading,
  error: livenessError,
  load: loadLiveness,
} = useApiResource(getLiveness)
const {
  data: readinessData,
  loading: readinessLoading,
  error: readinessError,
  load: loadReadiness,
} = useApiResource(getReadiness)
const {
  data: resources,
  loading: resourcesLoading,
  error: resourcesError,
  load: loadResources,
} = useApiResource(getSystemResources)

function refresh() {
  void Promise.all([loadLiveness(), loadReadiness(), loadResources()])
}

function formatBytes(value: number | undefined): string {
  if (value == null) return '--'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let amount = value
  let unit = 0
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024
    unit += 1
  }
  return `${amount.toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`
}
</script>

<template>
  <section>
    <PageHeader title="系统运维" description="检查服务存活、数据库、调度器和目录容量。">
      <template #actions>
        <el-button
          :loading="livenessLoading || readinessLoading || resourcesLoading"
          @click="refresh"
        >
          刷新
        </el-button>
      </template>
    </PageHeader>

    <div class="health-grid">
      <el-card shadow="never" class="health-card">
        <div class="health-card__topline">
          <span class="health-card__icon"
            ><el-icon><Monitor /></el-icon
          ></span>
          <el-tag :type="livenessData?.status === 'ok' ? 'success' : 'danger'">
            {{ livenessLoading ? '检查中' : livenessData?.status === 'ok' ? '正常' : '异常' }}
          </el-tag>
        </div>
        <h3>后端服务</h3>
        <p>{{ livenessError || `版本 ${resources?.process.appVersion ?? '--'}` }}</p>
      </el-card>

      <el-card shadow="never" class="health-card">
        <div class="health-card__topline">
          <span class="health-card__icon"
            ><el-icon><Connection /></el-icon
          ></span>
          <el-tag :type="readinessData?.database === 'postgresql' ? 'success' : 'danger'">
            {{
              readinessLoading
                ? '检查中'
                : readinessData?.database === 'postgresql'
                  ? '正常'
                  : '异常'
            }}
          </el-tag>
        </div>
        <h3>PostgreSQL</h3>
        <p>{{ readinessError || `${resources?.database.activeConnectionCount ?? '--'} 个连接` }}</p>
      </el-card>

      <el-card shadow="never" class="health-card">
        <div class="health-card__topline">
          <span class="health-card__icon"
            ><el-icon><Calendar /></el-icon
          ></span>
          <el-tag :type="resources?.scheduler.status === 'running' ? 'success' : 'danger'">
            {{
              resourcesLoading
                ? '检查中'
                : resources?.scheduler.status === 'running'
                  ? '运行中'
                  : '未运行'
            }}
          </el-tag>
        </div>
        <h3>采集调度器</h3>
        <p>
          {{
            resourcesError ||
            (resources?.scheduler.singletonLockHeld
              ? 'PostgreSQL 单例锁已持有'
              : '未检测到调度单例锁')
          }}
        </p>
      </el-card>

      <el-card shadow="never" class="health-card">
        <div class="health-card__topline">
          <span class="health-card__icon"
            ><el-icon><SetUp /></el-icon
          ></span>
          <el-tag
            :type="
              (resources?.scheduler.processingRunningCount ?? 0) <=
              (resources?.scheduler.processingMaxWorkers ?? 0)
                ? 'success'
                : 'danger'
            "
          >
            {{
              resourcesLoading
                ? '检查中'
                : `${resources?.scheduler.processingRunningCount ?? 0} / ${resources?.scheduler.processingMaxWorkers ?? '--'} 运行中`
            }}
          </el-tag>
        </div>
        <h3>加工执行器</h3>
        <p>受控并发执行；同一数据集保持串行发布</p>
      </el-card>
    </div>

    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="panel-card__header">
          <div>
            <h3>资源状态</h3>
            <p>数据库运行配置、API 进程资源和原始数据目录容量。</p>
          </div>
        </div>
      </template>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="数据库大小">
          {{ formatBytes(resources?.database.sizeBytes) }}
        </el-descriptions-item>
        <el-descriptions-item label="PostgreSQL 共享缓冲区（运行值）">
          {{ formatBytes(resources?.database.sharedBuffersBytes) }}
        </el-descriptions-item>
        <el-descriptions-item label="活动连接">
          {{ resources?.database.activeConnectionCount ?? '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="长事务">
          {{ resources?.database.longTransactionCount ?? '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="CPU 核数">
          {{ resources?.process.cpuCount ?? '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="API 进程峰值内存">
          {{ formatBytes(resources?.process.memoryHighWaterBytes) }}
        </el-descriptions-item>
        <el-descriptions-item label="1 分钟系统负载">
          {{ resources?.process.loadAverageOneMinute?.toFixed(2) ?? '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="原始目录">
          {{ resources?.storage.path ?? '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="磁盘使用率">
          {{ resources ? `${resources.storage.usedPercent.toFixed(1)}%` : '--' }}
        </el-descriptions-item>
        <el-descriptions-item label="剩余空间">
          {{ formatBytes(resources?.storage.freeBytes) }}
        </el-descriptions-item>
        <el-descriptions-item label="容量级别">
          {{ resources?.storage.level ?? '--' }}
        </el-descriptions-item>
      </el-descriptions>
      <p class="resource-explanation">
        PostgreSQL 共享缓冲区从当前数据库实例实时读取；API
        进程峰值内存是服务启动以来的最大常驻内存，不代表当前占用。
      </p>
    </el-card>

    <el-alert
      class="system-note"
      title="服务启停由安装目录中的 stock-data-sync 命令统一管理"
      description="浏览器后台只提供状态观测，避免绕过服务器权限、审计和进程管理机制。"
      type="info"
      :closable="false"
      show-icon
    />
  </section>
</template>

<style scoped>
.resource-explanation {
  margin: 12px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}
</style>
