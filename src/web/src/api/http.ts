import axios, { AxiosError } from 'axios'

import { ApiError } from '@/types/http'

interface ValidationErrorDetail {
  msg?: unknown
}

export function apiErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item: ValidationErrorDetail) => (typeof item?.msg === 'string' ? item.msg : ''))
      .filter(Boolean)
    if (messages.length) return messages.join('；')
  }
  return fallback
}

export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api/v1',
  timeout: 15_000,
  headers: {
    'Content-Type': 'application/json',
  },
})

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ detail?: unknown }>) => {
    const message = apiErrorMessage(error.response?.data?.detail, error.message || '请求失败')
    return Promise.reject(
      new ApiError(message, error.response?.status, error.code, error.config?.url),
    )
  },
)
