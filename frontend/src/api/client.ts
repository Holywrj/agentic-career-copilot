export type ErrorKind =
  | 'validation_error'
  | 'invalid_credentials'
  | 'username_unavailable'
  | 'authentication_required'
  | 'authentication_unavailable'
  | 'career_profile_not_found'
  | 'career_profile_already_exists'
  | 'career_profile_conflict'
  | 'career_profile_unavailable'
  | 'resume_not_found'
  | 'resume_in_use'
  | 'unsupported_resume_type'
  | 'resume_storage_unavailable'
  | 'resume_unavailable'
  | 'network_failure'
  | 'unexpected_server_error'
  | 'invalid_response'
  | 'cancelled'
  | 'timeout'

export type BackendCode =
  | 'invalid_request'
  | 'request_too_large'
  | 'invalid_credentials'
  | 'username_unavailable'
  | 'authentication_required'
  | 'authentication_unavailable'
  | 'career_profile_not_found'
  | 'career_profile_already_exists'
  | 'career_profile_conflict'
  | 'career_profile_unavailable'
  | 'resume_not_found'
  | 'resume_in_use'
  | 'unsupported_resume_type'
  | 'resume_storage_unavailable'
  | 'resume_unavailable'

export class ApiError extends Error {
  readonly kind: ErrorKind
  readonly status: number | null
  readonly code: BackendCode | null

  constructor(kind: ErrorKind, status: number | null = null, code: BackendCode | null = null) {
    super(kind)
    this.name = 'ApiError'
    this.kind = kind
    this.status = status
    this.code = code
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  token?: string
  signal?: AbortSignal
  timeoutMs?: number
  expectedStatus?: number
}

export type AuthenticatedRequest = <T = unknown>(
  path: string,
  options?: Omit<RequestOptions, 'token'>,
) => Promise<T>

function internalPath(path: string): boolean {
  if (!path.startsWith('/') || path.startsWith('//') || /[\s\\#]/u.test(path)) return false
  try {
    return path.split('?')[0].split('/').every((part) => {
      const decoded = decodeURIComponent(part)
      return decoded !== '.' && decoded !== '..' && !/[\s\\/]/u.test(decoded)
    })
  } catch {
    return false
  }
}

function httpError(status: number, payload: unknown): ApiError {
  const detail = typeof payload === 'object' && payload !== null && 'detail' in payload
    ? payload.detail : null
  // Match both status and code. Unknown bodies never become user-facing text.
  if (status === 422 && detail === 'invalid_request') return new ApiError('validation_error', status, detail)
  if (status === 413 && detail === 'request_too_large') return new ApiError('validation_error', status, detail)
  if (status === 409 && detail === 'username_unavailable') return new ApiError('username_unavailable', status, detail)
  if (status === 401 && detail === 'invalid_credentials') return new ApiError('invalid_credentials', status, detail)
  if (status === 401 && detail === 'authentication_required') return new ApiError('authentication_required', status, detail)
  if (status === 503 && detail === 'authentication_unavailable') return new ApiError('authentication_unavailable', status, detail)
  if (status === 404 && detail === 'career_profile_not_found') return new ApiError('career_profile_not_found', status, detail)
  if (status === 409 && detail === 'career_profile_already_exists') return new ApiError('career_profile_already_exists', status, detail)
  if (status === 409 && detail === 'career_profile_conflict') return new ApiError('career_profile_conflict', status, detail)
  if (status === 503 && detail === 'career_profile_unavailable') return new ApiError('career_profile_unavailable', status, detail)
  if (status === 404 && detail === 'resume_not_found') return new ApiError('resume_not_found', status, detail)
  if (status === 409 && detail === 'resume_in_use') return new ApiError('resume_in_use', status, detail)
  if (status === 415 && detail === 'unsupported_resume_type') return new ApiError('unsupported_resume_type', status, detail)
  if (status === 503 && detail === 'resume_storage_unavailable') return new ApiError('resume_storage_unavailable', status, detail)
  if (status === 503 && detail === 'resume_unavailable') return new ApiError('resume_unavailable', status, detail)
  return new ApiError('unexpected_server_error', status)
}

export async function request<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  if (!internalPath(path)) throw new TypeError('API requests require an internal relative path')
  if (options.timeoutMs !== undefined && (!Number.isFinite(options.timeoutMs) || options.timeoutMs <= 0)) {
    throw new TypeError('timeoutMs must be positive')
  }
  const controller = new AbortController()
  const cancel = () => controller.abort()
  options.signal?.addEventListener('abort', cancel, { once: true })
  let timedOut = false
  const timer = options.timeoutMs === undefined ? undefined : setTimeout(() => {
    timedOut = true
    controller.abort()
  }, options.timeoutMs)
  try {
    if (options.signal?.aborted) throw new ApiError('cancelled')
    const headers: Record<string, string> = { Accept: 'application/json' }
    const multipart = options.body instanceof FormData
    if (options.body !== undefined && !multipart) headers['Content-Type'] = 'application/json'
    if (options.token) headers.Authorization = `Bearer ${options.token}`
    const response = await fetch(`/api${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: multipart ? options.body as FormData : options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: 'omit',
      signal: controller.signal,
    })
    if (controller.signal.aborted) throw new ApiError(timedOut ? 'timeout' : 'cancelled')
    if (response.ok && options.expectedStatus !== undefined && response.status !== options.expectedStatus) {
      throw new ApiError('invalid_response', response.status)
    }
    if (response.status === 204 && response.ok) return undefined as T
    const text = await response.text()
    let payload: unknown
    try {
      payload = JSON.parse(text)
    } catch {
      if (!response.ok) throw httpError(response.status, null)
      throw new ApiError('invalid_response', response.status)
    }
    if (controller.signal.aborted) throw new ApiError(timedOut ? 'timeout' : 'cancelled')
    if (!response.ok) throw httpError(response.status, payload)
    return payload as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (controller.signal.aborted) throw new ApiError(timedOut ? 'timeout' : 'cancelled')
    throw new ApiError('network_failure')
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', cancel)
  }
}

export function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return '暂时无法完成请求，请重试。'
  switch (error.kind) {
    case 'validation_error':
      return error.code === 'request_too_large'
        ? '输入内容过长，请缩短后重试。'
        : '请检查用户名和密码是否符合要求，然后重试。'
    case 'invalid_credentials': return '用户名或密码不正确。'
    case 'username_unavailable': return '该用户名不可用，请换一个。'
    case 'authentication_required': return '无法验证登录状态，请重新登录。'
    case 'authentication_unavailable': return '登录服务暂时不可用，请稍后重试。'
    case 'network_failure': return '无法连接服务，请检查网络后重试。'
    case 'timeout': return '请求超时，请重试。'
    case 'invalid_response': return '服务响应异常，请重试。'
    default: return '暂时无法完成请求，请重试。'
  }
}
