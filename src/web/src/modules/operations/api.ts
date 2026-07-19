import { http } from '@/api/http'

import type {
  AcquisitionBatchItem,
  AcquisitionBatchQuery,
  AlertQuery,
  ManualCommandOptions,
  AdminCommandOptions,
  AlertItem,
  DependencyItem,
  DependencyQuery,
  DatasetReleaseItem,
  DatasetReleaseQuery,
  CreateBackfillCommand,
  CreateRepairCommand,
  OperationsOverview,
  OperationCommandResult,
  PageResult,
  ProcessingQueueItem,
  ProcessingQueueQuery,
  ProviderMonitoring,
  RunRecordItem,
  RunRecordQuery,
  TaskCommand,
  TaskTransition,
} from './contracts'

function commandHeaders(options: AdminCommandOptions) {
  return {
    Authorization: `Bearer ${options.adminToken}`,
    'Idempotency-Key': options.idempotencyKey,
  }
}

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

export async function getProcessingQueue(
  query: ProcessingQueueQuery = {},
): Promise<PageResult<ProcessingQueueItem>> {
  const response = await http.get<PageResult<ProcessingQueueItem>>('/operations/processing-queue', {
    params: query,
  })
  return response.data
}

export async function getDependencies(
  query: DependencyQuery = {},
): Promise<PageResult<DependencyItem>> {
  const response = await http.get<PageResult<DependencyItem>>('/operations/dependencies', {
    params: query,
  })
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

export async function getAlerts(query: AlertQuery = {}): Promise<PageResult<AlertItem>> {
  const response = await http.get<PageResult<AlertItem>>('/operations/alerts', { params: query })
  return response.data
}

export async function getDatasetReleases(
  query: DatasetReleaseQuery = {},
): Promise<PageResult<DatasetReleaseItem>> {
  const response = await http.get<PageResult<DatasetReleaseItem>>('/operations/releases', {
    params: query,
  })
  return response.data
}

export async function createBackfill(
  payload: CreateBackfillCommand,
  options: AdminCommandOptions,
): Promise<OperationCommandResult> {
  const response = await http.post<OperationCommandResult>(
    '/operations/commands/backfills',
    payload,
    {
      headers: commandHeaders(options),
    },
  )
  return response.data
}

export async function createRepair(
  payload: CreateRepairCommand,
  options: AdminCommandOptions,
): Promise<OperationCommandResult> {
  const response = await http.post<OperationCommandResult>(
    '/operations/commands/repairs',
    payload,
    {
      headers: commandHeaders(options),
    },
  )
  return response.data
}

export async function runTaskCommand(
  runType: 'acquisition' | 'processing',
  taskId: string,
  transition: TaskTransition,
  payload: TaskCommand,
  options: AdminCommandOptions,
): Promise<OperationCommandResult> {
  const taskType = runType === 'acquisition' ? 'collection-tasks' : 'processing-tasks'
  const response = await http.post<OperationCommandResult>(
    `/operations/commands/${taskType}/${taskId}/${transition}`,
    payload,
    { headers: commandHeaders(options) },
  )
  return response.data
}

export async function cancelAcquisitionBatch(
  batchId: string,
  payload: TaskCommand,
  options: AdminCommandOptions,
): Promise<OperationCommandResult> {
  const response = await http.post<OperationCommandResult>(
    `/operations/commands/acquisition-batches/${batchId}/cancel`,
    payload,
    { headers: commandHeaders(options) },
  )
  return response.data
}

export async function getManualCommandOptions(): Promise<ManualCommandOptions> {
  const response = await http.get<ManualCommandOptions>('/operations/command-options')
  return response.data
}
