export type ExecutionStatus =
  | 'pending'
  | 'running'
  | 'waiting_retry'
  | 'succeeded'
  | 'partial_failed'
  | 'failed'
  | 'blocked'
  | 'closed'

export type PriorityLevel = 'current_normal' | 'auto_supplement' | 'manual_rerun' | 'historical'
export type AlertLevel = 'critical' | 'warning' | 'info'

export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  pageSize: number
}

export interface OverviewMetrics {
  collectingBatchCount: number
  processingTaskCount: number
  blockedTaskCount: number
  taskSuccessRateToday: number | null
  providerSuccessRateToday: number | null
  providerP95DurationMs: number | null
}

export interface QuotaSnapshot {
  provider: string
  limitPerMinute: number
  usedInCurrentWindow: number
  remainingInCurrentWindow: number
  delayedRequestCount: number
  capturedAt: string
}

export interface ProcessingQueueItem {
  id: string
  taskName: string
  batchCode: string
  dataCycle: string
  priority: PriorityLevel
  queuePosition: number
  status: ExecutionStatus
  dependencyCount: number
  waitingSince: string | null
  startedAt: string | null
  durationMs: number | null
  blockedReason: string | null
}

export interface AcquisitionBatchItem {
  id: string
  batchCode: string
  themeName: string
  dataCycle: string
  batchType: 'normal' | 'auto_supplement' | 'manual'
  status: ExecutionStatus
  taskCount: number
  succeededTaskCount: number
  failedTaskCount: number
  startedAt: string | null
  closedAt: string | null
  durationMs: number | null
}

export interface DependencyItem {
  id: string
  processingTaskName: string
  batchCode: string
  sourceEndpoint: string
  sourceCycle: string
  sourcePolicy: 'current_cycle' | 'latest_valid'
  sourceReady: boolean
  status: ExecutionStatus
  reason: string | null
}

export interface ProviderEndpointMetric {
  endpoint: string
  requestCountToday: number
  successRateToday: number | null
  p50DurationMs: number | null
  p95DurationMs: number | null
  throttledCountToday: number
  emptyResponseCountToday: number
  lastRequestedAt: string | null
}

export interface AlertItem {
  id: string
  level: AlertLevel
  source: string
  title: string
  detail: string
  occurredAt: string
  acknowledgedAt: string | null
}

export interface RunRecordItem {
  id: string
  runType: 'acquisition' | 'processing'
  taskName: string
  batchCode: string
  dataCycle: string
  status: ExecutionStatus
  attempt: number
  startedAt: string | null
  finishedAt: string | null
  durationMs: number | null
  errorSummary: string | null
}

export interface OperationsOverview {
  generatedAt: string
  metrics: OverviewMetrics
  quota: QuotaSnapshot | null
  currentProcessing: ProcessingQueueItem | null
  recentBatches: AcquisitionBatchItem[]
  recentAlerts: AlertItem[]
}

export interface AcquisitionBatchQuery {
  status?: ExecutionStatus
  dataCycle?: string
  page?: number
  pageSize?: number
}

export interface RunRecordQuery {
  runType?: 'acquisition' | 'processing'
  status?: ExecutionStatus
  page?: number
  pageSize?: number
}

export interface ProviderMonitoring {
  quota: QuotaSnapshot | null
  endpoints: ProviderEndpointMetric[]
}
