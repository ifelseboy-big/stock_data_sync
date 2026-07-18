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
          path: 'tasks',
          name: 'tasks',
          component: () => import('@/modules/tasking/views/TaskManagementView.vue'),
          meta: { title: '任务管理' },
        },
        {
          path: 'operations',
          name: 'operations',
          component: () => import('@/modules/operations/views/OperationsView.vue'),
          meta: { title: '运行记录' },
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
