import 'vue-router'

export {}

declare module 'vue-router' {
  interface RouteMeta {
    title: string
    requiresAuth?: boolean
    permission?: string
  }
}
