import { http } from '@/api/http'

export interface HealthResponse {
  status: string
  database?: string
}

export interface SystemResources {
  generatedAt: string
  database: {
    status: string
    version: string
    sizeBytes: number
    sharedBuffersBytes: number
    activeConnectionCount: number
    longTransactionCount: number
  }
  scheduler: {
    status: string
    singletonLockHeld: boolean
    processingRunningCount: number
    processingMaxWorkers: number
  }
  storage: {
    level: 'NORMAL' | 'WARNING' | 'PROTECT'
    path: string
    totalBytes: number
    usedBytes: number
    freeBytes: number
    usedPercent: number
  }
  process: {
    appVersion: string
    cpuCount: number
    loadAverageOneMinute: number | null
    memoryHighWaterBytes: number
  }
}

export async function getLiveness(): Promise<HealthResponse> {
  const response = await http.get<HealthResponse>('/health/live')
  return response.data
}

export async function getReadiness(): Promise<HealthResponse> {
  const response = await http.get<HealthResponse>('/health/ready')
  return response.data
}

export async function getSystemResources(): Promise<SystemResources> {
  const response = await http.get<SystemResources>('/system/resources')
  return response.data
}
