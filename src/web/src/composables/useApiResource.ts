import { onMounted, ref } from 'vue'

import { ApiError } from '@/types/http'

export function useApiResource<T>(loader: () => Promise<T>) {
  const data = ref<T>()
  const loading = ref(true)
  const error = ref('')
  let latestRequest = 0
  let visibleLoadCount = 0

  async function execute(silent: boolean) {
    const request = ++latestRequest
    if (!silent) {
      visibleLoadCount += 1
      loading.value = true
    }
    error.value = ''
    try {
      const result = await loader()
      if (request === latestRequest) {
        data.value = result
      }
    } catch (reason) {
      if (request === latestRequest) {
        error.value = reason instanceof ApiError ? reason.message : '数据加载失败'
      }
    } finally {
      if (!silent) {
        visibleLoadCount -= 1
        loading.value = visibleLoadCount > 0
      }
    }
  }

  function load() {
    return execute(false)
  }

  function refresh() {
    return execute(true)
  }

  onMounted(load)

  return { data, loading, error, load, refresh }
}
