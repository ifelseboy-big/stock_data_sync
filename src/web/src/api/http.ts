import axios, { AxiosError } from 'axios'

import { ApiError } from '@/types/http'

export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api/v1',
  timeout: 15_000,
  headers: {
    'Content-Type': 'application/json',
  },
})

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ detail?: string }>) => {
    const message = error.response?.data?.detail ?? error.message ?? '请求失败'
    return Promise.reject(
      new ApiError(message, error.response?.status, error.code, error.config?.url),
    )
  },
)
