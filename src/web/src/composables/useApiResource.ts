import { onMounted, ref } from 'vue'

import { ApiError } from '@/types/http'

export function useApiResource<T>(loader: () => Promise<T>) {
  const data = ref<T>()
  const loading = ref(true)
  const error = ref('')

  async function load() {
    loading.value = true
    error.value = ''
    try {
      data.value = await loader()
    } catch (reason) {
      error.value = reason instanceof ApiError ? reason.message : '数据加载失败'
    } finally {
      loading.value = false
    }
  }

  onMounted(load)

  return { data, loading, error, load }
}
