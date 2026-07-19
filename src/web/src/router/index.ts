import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      component: () => import('@/layouts/AdminLayout.vue'),
      children: [
        {
          path: '',
          name: 'dashboard',
          component: () => import('@/modules/dashboard/views/DashboardView.vue'),
          meta: { title: '运行概览' },
        },
        {
          path: 'acquisition',
          name: 'acquisition',
          component: () => import('@/modules/acquisition/views/AcquisitionView.vue'),
          meta: { title: '采集运行' },
        },
        {
          path: 'processing',
          name: 'processing',
          component: () => import('@/modules/processing/views/ProcessingView.vue'),
          meta: { title: '加工队列' },
        },
        {
          path: 'dependencies',
          name: 'dependencies',
          component: () => import('@/modules/dependencies/views/DependenciesView.vue'),
          meta: { title: '依赖观测' },
        },
        {
          path: 'releases',
          name: 'releases',
          component: () => import('@/modules/operations/views/ReleasesView.vue'),
          meta: { title: '数据发布' },
        },
        {
          path: 'schedules',
          name: 'schedules',
          component: () => import('@/modules/operations/views/ScheduledJobsView.vue'),
          meta: { title: '任务调度' },
        },
        {
          path: 'providers',
          name: 'providers',
          component: () => import('@/modules/providers/views/ProviderMonitoringView.vue'),
          meta: { title: '接口监控' },
        },
        {
          path: 'runs',
          name: 'runs',
          component: () => import('@/modules/operations/views/RunRecordsView.vue'),
          meta: { title: '运行记录' },
        },
        {
          path: 'alerts',
          name: 'alerts',
          component: () => import('@/modules/operations/views/AlertsView.vue'),
          meta: { title: '告警中心' },
        },
        {
          path: 'system',
          name: 'system',
          component: () => import('@/modules/system/views/SystemView.vue'),
          meta: { title: '系统运维' },
        },
      ],
    },
  ],
})

router.afterEach((to) => {
  document.title = `${String(to.meta.title ?? '管理后台')} · Stock Data Sync`
})

export default router
