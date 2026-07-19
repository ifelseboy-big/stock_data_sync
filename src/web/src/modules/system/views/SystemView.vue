<script setup lang="ts">
import { Calendar, Connection, Monitor, SetUp } from '@element-plus/icons-vue'

import PageHeader from '@/components/PageHeader.vue'
import { useApiResource } from '@/composables/useApiResource'
import { getLiveness, getReadiness } from '@/modules/system/api'

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

function refresh() {
  void Promise.all([loadLiveness(), loadReadiness()])
}
</script>

<template>
  <section>
    <PageHeader title="系统运维" description="检查服务存活、数据库就绪和运行组件状态。">
      <template #actions>
        <el-button :loading="livenessLoading || readinessLoading" @click="refresh">刷新</el-button>
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
        <p>{{ livenessError || 'FastAPI 存活探针' }}</p>
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
        <p>{{ readinessError || '数据库就绪探针' }}</p>
      </el-card>
      <el-card shadow="never" class="health-card">
        <div class="health-card__topline">
          <span class="health-card__icon"
            ><el-icon><Calendar /></el-icon
          ></span>
          <el-tag type="info">未上报</el-tag>
        </div>
        <h3>采集调度器</h3>
        <p>等待运行状态接口接入</p>
      </el-card>
      <el-card shadow="never" class="health-card">
        <div class="health-card__topline">
          <span class="health-card__icon"
            ><el-icon><SetUp /></el-icon
          ></span>
          <el-tag type="info">未上报</el-tag>
        </div>
        <h3>加工执行器</h3>
        <p>等待全局执行槽位状态接入</p>
      </el-card>
    </div>

    <el-alert
      class="system-note"
      title="服务启停由安装目录中的 stock-sync 命令统一管理"
      description="浏览器后台只提供状态观测，避免绕过服务器权限、审计和进程管理机制。"
      type="info"
      :closable="false"
      show-icon
    />
  </section>
</template>
