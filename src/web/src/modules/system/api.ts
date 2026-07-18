import { http } from '@/api/http'

export interface HealthResponse {
  status: string
  database?: string
}

export async function getLiveness(): Promise<HealthResponse> {
  const response = await http.get<HealthResponse>('/health/live')
  return response.data
}
