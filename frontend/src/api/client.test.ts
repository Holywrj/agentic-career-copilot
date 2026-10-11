import { beforeEach, expect, test, vi } from 'vitest'
import { ApiError, errorMessage, request } from './client'
import { json } from '../test/fixtures'

let fetchMock = vi.fn<typeof fetch>()
beforeEach(() => { fetchMock = vi.fn<typeof fetch>(); vi.stubGlobal('fetch', fetchMock) })

test('uses relative /api, JSON, explicit bearer, and omits cookies', async () => {
  fetchMock.mockResolvedValue(json({ ok: true }))
  expect(await request('/auth/login', { method: 'POST', body: { username: 'alice', password: 'private' }, token: 'temporary' })).toEqual({ ok: true })
  expect(fetchMock).toHaveBeenCalledWith('/api/auth/login', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ username: 'alice', password: 'private' }), credentials: 'omit',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json', Authorization: 'Bearer temporary' },
  }))
})

test('sends neither bearer nor content type without explicit token/body', async () => {
  fetchMock.mockImplementation(async () => json({}))
  await request('/health')
  expect(fetchMock.mock.calls[0][1]?.headers).toEqual({ Accept: 'application/json' })
  await request('/health', { token: '' })
  expect(fetchMock.mock.calls[1][1]?.headers).not.toHaveProperty('Authorization')
})

test('passes FormData directly with bearer and leaves multipart boundary to the browser', async () => {
  fetchMock.mockResolvedValue(json({ ok: true }, 201))
  const form = new FormData()
  form.append('file', new File(['%PDF-'], 'resume.pdf', { type: 'application/pdf' }))
  await request('/resumes', { method: 'POST', body: form, token: 'temporary' })
  const init = fetchMock.mock.calls[0][1]!
  expect(init.body).toBe(form)
  expect(init.headers).toEqual({ Accept: 'application/json', Authorization: 'Bearer temporary' })
})

test('handles empty 204 without parsing JSON', async () => {
  fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
  expect(await request('/auth/logout', { method: 'POST', token: 'temporary' })).toBeUndefined()
})

test.each([
  [422, 'invalid_request', 'validation_error'],
  [413, 'request_too_large', 'validation_error'],
  [409, 'username_unavailable', 'username_unavailable'],
  [401, 'invalid_credentials', 'invalid_credentials'],
  [401, 'authentication_required', 'authentication_required'],
  [503, 'authentication_unavailable', 'authentication_unavailable'],
])('normalizes %s %s safely', async (status, code, kind) => {
  fetchMock.mockResolvedValue(json({ detail: code }, status))
  await expect(request('/auth/me')).rejects.toMatchObject({ kind, code, status })
})

test.each([
  [401, { detail: 'secret decoder information' }],
  [401, { detail: 'authentication_unavailable' }],
  [500, { detail: { input: 'private password' } }],
])('does not trust unknown or mismatched error details (%s)', async (status, body) => {
  fetchMock.mockResolvedValue(json(body, status))
  try { await request('/auth/me'); expect.fail('expected error') } catch (error) {
    expect(error).toMatchObject({ kind: 'unexpected_server_error', status, code: null })
    expect(errorMessage(error)).not.toMatch(/secret|decoder|private password|incorrect|用户名或密码不正确/)
    expect(JSON.stringify(error)).not.toMatch(/secret|decoder|private password/)
  }
})

test('handles non-JSON proxy errors without exposing HTML', async () => {
  fetchMock.mockResolvedValue(new Response('<html>upstream private details</html>', { status: 502 }))
  await expect(request('/auth/me')).rejects.toMatchObject({ kind: 'unexpected_server_error', status: 502, code: null })
})

test.each(['not JSON', ''])('rejects malformed successful JSON (%s)', async (body) => {
  fetchMock.mockResolvedValue(new Response(body))
  await expect(request('/auth/me')).rejects.toMatchObject({ kind: 'invalid_response', status: 200 })
})

test('distinguishes network failure', async () => {
  fetchMock.mockRejectedValue(new TypeError('untrusted networking detail'))
  await expect(request('/auth/me')).rejects.toMatchObject({ kind: 'network_failure', status: null })
})

function abortableFetch(_input: unknown, init?: RequestInit): Promise<Response> {
  return new Promise((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
  })
}

test('forwards AbortSignal and distinguishes cancellation', async () => {
  fetchMock.mockImplementation(abortableFetch)
  const controller = new AbortController()
  const result = request('/auth/me', { signal: controller.signal })
  const assertion = expect(result).rejects.toMatchObject({ kind: 'cancelled' })
  controller.abort()
  await assertion
})

test('does not start fetch with an already aborted signal', async () => {
  const controller = new AbortController()
  controller.abort()
  await expect(request('/auth/me', { signal: controller.signal })).rejects.toMatchObject({ kind: 'cancelled' })
  expect(fetchMock).not.toHaveBeenCalled()
})

test('timeout is per request, distinct from cancellation, with no default timer', async () => {
  vi.useFakeTimers()
  fetchMock.mockImplementation(abortableFetch)
  const controller = new AbortController()
  const longRequest = request('/future-task', { signal: controller.signal })
  const cancelled = expect(longRequest).rejects.toMatchObject({ kind: 'cancelled' })
  expect(vi.getTimerCount()).toBe(0)
  const timed = expect(request('/auth/logout', { timeoutMs: 100 })).rejects.toMatchObject({ kind: 'timeout' })
  await vi.advanceTimersByTimeAsync(100)
  await timed
  controller.abort()
  await cancelled
  expect(vi.getTimerCount()).toBe(0)
})

test.each(['https://example.com/auth', '//example.com/auth', 'auth/me', '/auth/../health', '/auth/%2e%2e/health', '/auth/%2f/me', '/auth\\me', '/auth/me#token', '/auth/..\t/health'])('rejects unsafe path %s before fetch', async (path) => {
  await expect(request(path, { token: 'private' })).rejects.toThrow(TypeError)
  expect(fetchMock).not.toHaveBeenCalled()
})

test('does not retry failed POST requests', async () => {
  fetchMock.mockResolvedValue(json({ detail: 'authentication_unavailable' }, 503))
  await expect(request('/auth/login', { method: 'POST', body: {} })).rejects.toBeInstanceOf(ApiError)
  expect(fetchMock).toHaveBeenCalledTimes(1)
})
