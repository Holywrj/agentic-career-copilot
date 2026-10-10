import { ApiError, request } from './client'

export interface Credentials { username: string; password: string }
export interface CurrentUser { id: string; status: 'active' }
export interface RegisteredUser extends CurrentUser { username: string }
export interface LoginResponse { access_token: string; token_type: 'bearer'; expires_in: number }

const AUTH_TIMEOUT_MS = 15_000
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function currentUser(value: unknown): CurrentUser {
  if (!record(value) || typeof value.id !== 'string' || !uuid.test(value.id) || value.status !== 'active') {
    throw new ApiError('invalid_response')
  }
  return { id: value.id, status: value.status }
}

export async function register(credentials: Credentials, signal?: AbortSignal): Promise<RegisteredUser> {
  const data = await request('/auth/register', { method: 'POST', body: credentials, signal, timeoutMs: AUTH_TIMEOUT_MS })
  const user = currentUser(data)
  if (!record(data) || typeof data.username !== 'string' || !/^[a-z0-9][a-z0-9_.-]{2,31}$/.test(data.username)) {
    throw new ApiError('invalid_response')
  }
  return { ...user, username: data.username }
}

export async function login(credentials: Credentials, signal?: AbortSignal): Promise<LoginResponse> {
  const data = await request('/auth/login', { method: 'POST', body: credentials, signal, timeoutMs: AUTH_TIMEOUT_MS })
  if (!record(data) || typeof data.access_token !== 'string' || !data.access_token || /\s/.test(data.access_token)
    || data.token_type !== 'bearer' || typeof data.expires_in !== 'number'
    || !Number.isInteger(data.expires_in) || data.expires_in <= 0 || data.expires_in > 3600) {
    throw new ApiError('invalid_response')
  }
  return { access_token: data.access_token, token_type: data.token_type, expires_in: data.expires_in }
}

export async function me(token: string, signal?: AbortSignal): Promise<CurrentUser> {
  return currentUser(await request('/auth/me', { token, signal, timeoutMs: AUTH_TIMEOUT_MS }))
}

export async function logout(token: string, signal?: AbortSignal): Promise<void> {
  const data = await request('/auth/logout', { method: 'POST', token, signal, timeoutMs: AUTH_TIMEOUT_MS })
  if (data !== undefined) throw new ApiError('invalid_response')
}
