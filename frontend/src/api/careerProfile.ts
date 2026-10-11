import { ApiError } from './client'
import type { AuthenticatedRequest } from './client'

export interface CareerProfile {
  revision: number
  career_summary: string | null
  skills: string[]
  target_roles: string[]
  created_at: string
  updated_at: string
}

export interface ProfileFields {
  career_summary: string | null
  skills: string[]
  target_roles: string[]
}
export type CareerProfileCreate = Partial<ProfileFields>
export interface CareerProfileUpdate extends ProfileFields { expected_revision: number }

export const PROFILE_TIMEOUT_MS = 15_000

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function profile(value: unknown): CareerProfile {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new ApiError('invalid_response')
  const data = value as Record<string, unknown>
  if (typeof data.revision !== 'number' || !Number.isSafeInteger(data.revision) || data.revision <= 0
    || !(data.career_summary === null || typeof data.career_summary === 'string')
    || !strings(data.skills) || !strings(data.target_roles)
    || typeof data.created_at !== 'string' || typeof data.updated_at !== 'string') {
    throw new ApiError('invalid_response')
  }
  return {
    revision: data.revision, career_summary: data.career_summary,
    skills: [...data.skills], target_roles: [...data.target_roles],
    created_at: data.created_at, updated_at: data.updated_at,
  }
}

export async function getCareerProfile(request: AuthenticatedRequest, signal?: AbortSignal): Promise<CareerProfile> {
  return profile(await request('/career-profile', { signal, timeoutMs: PROFILE_TIMEOUT_MS }))
}

export async function createCareerProfile(request: AuthenticatedRequest, data: CareerProfileCreate, signal?: AbortSignal): Promise<CareerProfile> {
  return profile(await request('/career-profile', { method: 'POST', body: data, signal, timeoutMs: PROFILE_TIMEOUT_MS }))
}

export async function updateCareerProfile(request: AuthenticatedRequest, data: CareerProfileUpdate, signal?: AbortSignal): Promise<CareerProfile> {
  return profile(await request('/career-profile', { method: 'PUT', body: data, signal, timeoutMs: PROFILE_TIMEOUT_MS }))
}

// Feature copy stays here; auth forms retain their existing validation wording.
export function careerProfileErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return '暂时无法读取职业档案，请稍后重新加载。'
  switch (error.kind) {
    case 'validation_error': return error.code === 'request_too_large'
      ? '档案内容过大，请缩短职业概述或减少列表内容。'
      : '请检查职业概述、技能和目标岗位是否符合要求。'
    case 'career_profile_unavailable': return '职业档案服务暂时不可用，请稍后重新加载。'
    case 'authentication_unavailable': return '登录验证服务暂时不可用，请稍后重新加载。'
    case 'authentication_required': return '登录已失效，请重新登录。'
    case 'network_failure': return '无法连接服务，请检查网络后重新加载。'
    case 'timeout': return '请求超时，请重新加载服务器档案。'
    case 'invalid_response': return '职业档案响应异常，请重新加载。'
    case 'career_profile_not_found': return '服务器上尚无职业档案。'
    case 'career_profile_already_exists': return '档案已在其他位置创建，请比较服务器版本与当前草稿。'
    case 'career_profile_conflict': return '档案已在其他位置修改，请比较服务器版本与当前草稿。'
    default: return '暂时无法读取职业档案，请稍后重新加载。'
  }
}

// Only recognized rejections establish that a write did not succeed. Unknown
// errors, malformed successes and availability failures require reconciliation.
export function uncertainWrite(error: unknown): boolean {
  return !(error instanceof ApiError) || ![
    'validation_error', 'authentication_required', 'cancelled',
    'career_profile_conflict', 'career_profile_already_exists', 'career_profile_not_found',
  ].includes(error.kind)
}
