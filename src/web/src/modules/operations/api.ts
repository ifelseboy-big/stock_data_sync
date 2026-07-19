import { http } from '@/api/http'

import type {
  AcquisitionBatchItem,
  AcquisitionBatchQuery,
  AlertItem,
  DependencyItem,
  OperationsOverview,
  PageResult,
  ProcessingQueueItem,
  ProviderMonitoring,
  RunRecordItem,
  RunRecordQuery,
} from './contracts'

export async function getOperationsOverview(): Promise<OperationsOverview> {
  const response = await http.get<OperationsOverview>('/operations/overview')
  return response.data
}

export async function getAcquisitionBatches(
  query: AcquisitionBatchQuery = {},
): Promise<PageResult<AcquisitionBatchItem>> {
  const response = await http.get<PageResult<AcquisitionBatchItem>>(
    '/operations/acquisition-batches',
    {
      params: query,
    },
  )
  return response.data
}

export async function getProcessingQueue(): Promise<ProcessingQueueItem[]> {
  const response = await http.get<ProcessingQueueItem[]>('/operations/processing-queue')
  return response.data
}

export async function getDependencies(): Promise<DependencyItem[]> {
  const response = await http.get<DependencyItem[]>('/operations/dependencies')
  return response.data
}

export async function getProviderMonitoring(): Promise<ProviderMonitoring> {
  const response = await http.get<ProviderMonitoring>('/operations/providers/tushare')
  return response.data
}

export async function getRunRecords(
  query: RunRecordQuery = {},
): Promise<PageResult<RunRecordItem>> {
  const response = await http.get<PageResult<RunRecordItem>>('/operations/runs', { params: query })
  return response.data
}

export async function getAlerts(): Promise<AlertItem[]> {
  const response = await http.get<AlertItem[]>('/operations/alerts')
  return response.data
}
