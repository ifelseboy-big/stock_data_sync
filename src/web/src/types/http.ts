export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly code?: string,
    readonly endpoint?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}
