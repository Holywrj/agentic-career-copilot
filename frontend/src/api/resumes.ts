import { ApiError } from './client'
import type { AuthenticatedRequest } from './client'

export interface Resume {
  id: string
  original_filename: string
  media_type: string | null
  size_bytes: number | null
  status: string
  created_at: string
  updated_at: string
}
export interface ResumeList { items: Resume[]; limit: number; offset: number }

export const MAX_RESUME_BYTES = 5 * 1024 * 1024
export const RESUME_LIST_LIMIT = 100
export const RESUME_READ_TIMEOUT_MS = 15_000
export const RESUME_UPLOAD_TIMEOUT_MS = 60_000
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function nonempty(value: unknown): value is string { return typeof value === 'string' && value.trim().length > 0 }

// Require an actual timezone-aware ISO calendar date. Normalize microseconds to
// milliseconds before browser parsing (ECMAScript's standard date precision).
export function resumeTimestamp(value: string): number {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/.exec(value)
  if (!match) throw new ApiError('invalid_response')
  const [, y, m, d, h, min, s, fraction, zone] = match
  const year = Number(y), month = Number(m), day = Number(d)
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate()
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days
    || Number(h) > 23 || Number(min) > 59 || Number(s) > 59
    || (zone !== 'Z' && (Number(zone.slice(1, 3)) > 23 || Number(zone.slice(4)) > 59))) {
    throw new ApiError('invalid_response')
  }
  const normalized = `${y}-${m}-${d}T${h}:${min}:${s}${fraction ? `.${fraction.slice(0, 3).padEnd(3, '0')}` : ''}${zone}`
  const timestamp = Date.parse(normalized)
  if (!Number.isFinite(timestamp)) throw new ApiError('invalid_response')
  return timestamp
}

function resume(value: unknown): Resume {
  if (!record(value) || typeof value.id !== 'string' || !uuid.test(value.id)
    || !nonempty(value.original_filename) || !(value.media_type === null || nonempty(value.media_type))
    || !(value.size_bytes === null || (typeof value.size_bytes === 'number' && Number.isSafeInteger(value.size_bytes) && value.size_bytes > 0))
    || !nonempty(value.status) || typeof value.created_at !== 'string' || typeof value.updated_at !== 'string') {
    throw new ApiError('invalid_response')
  }
  resumeTimestamp(value.created_at)
  resumeTimestamp(value.updated_at)
  // Copy only contract fields; extra backend fields never enter presentation.
  return {
    id: value.id.toLowerCase(), original_filename: value.original_filename,
    media_type: value.media_type, size_bytes: value.size_bytes, status: value.status,
    created_at: value.created_at, updated_at: value.updated_at,
  }
}

export async function listResumes(request: AuthenticatedRequest, signal?: AbortSignal): Promise<ResumeList> {
  const data = await request(`/resumes?limit=${RESUME_LIST_LIMIT}&offset=0`, { signal, timeoutMs: RESUME_READ_TIMEOUT_MS, expectedStatus: 200 })
  if (!record(data) || !Array.isArray(data.items) || data.limit !== RESUME_LIST_LIMIT || data.offset !== 0
    || data.items.length > RESUME_LIST_LIMIT) throw new ApiError('invalid_response')
  const items = data.items.map(resume)
  if (new Set(items.map((item) => item.id)).size !== items.length) throw new ApiError('invalid_response')
  return { items, limit: data.limit, offset: data.offset }
}

export async function uploadResume(request: AuthenticatedRequest, file: File, signal?: AbortSignal): Promise<Resume> {
  const form = new FormData()
  // This PDF-only feature declares the part MIME consistently, even when the
  // browser cannot infer File.type. The backend still checks extension/content.
  form.append('file', new Blob([file], { type: 'application/pdf' }), file.name)
  const uploaded = resume(await request('/resumes', { method: 'POST', body: form, signal, timeoutMs: RESUME_UPLOAD_TIMEOUT_MS, expectedStatus: 201 }))
  if (uploaded.media_type !== 'application/pdf' || uploaded.size_bytes === null
    || uploaded.size_bytes > MAX_RESUME_BYTES || uploaded.status !== 'uploaded') throw new ApiError('invalid_response')
  return uploaded
}

export async function deleteResume(request: AuthenticatedRequest, id: string, signal?: AbortSignal): Promise<void> {
  if (!uuid.test(id)) throw new ApiError('invalid_response')
  const data = await request(`/resumes/${id}`, { method: 'DELETE', signal, timeoutMs: RESUME_READ_TIMEOUT_MS, expectedStatus: 204 })
  if (data !== undefined) throw new ApiError('invalid_response')
}

export function resumeErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return '暂时无法完成简历操作，请稍后重试。'
  switch (error.kind) {
    case 'validation_error': return error.code === 'request_too_large' ? '文件不能超过 5 MiB。' : '无法处理这份简历，请确认选择的是有效的 PDF。'
    case 'unsupported_resume_type': return '只支持 PDF 文件。'
    case 'resume_in_use': return '这份简历正在被其他记录使用，暂时不能删除。'
    case 'resume_not_found': return '这份简历当前已不可见，请刷新列表。'
    case 'resume_storage_unavailable':
    case 'resume_unavailable': return '简历服务暂时不可用，请稍后重试。'
    case 'authentication_required': return '登录已失效，请重新登录。'
    case 'network_failure': return '无法读取简历列表，请检查网络后重试。'
    case 'timeout': return '读取简历列表超时，请重试。'
    case 'invalid_response': return '简历列表响应异常，请重新加载。'
    default: return '暂时无法完成简历操作，请稍后重试。'
  }
}

export function uncertainResumeWrite(error: unknown): boolean {
  return !(error instanceof ApiError) || ![
    'validation_error', 'authentication_required', 'unsupported_resume_type', 'resume_in_use', 'resume_not_found',
  ].includes(error.kind)
}
