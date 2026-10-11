import { beforeEach, expect, test, vi } from 'vitest'
import { ApiError, errorMessage, request } from './client'
import type { AuthenticatedRequest } from './client'
import { deleteResume, listResumes, MAX_RESUME_BYTES, resumeErrorMessage, RESUME_UPLOAD_TIMEOUT_MS, uncertainResumeWrite, uploadResume } from './resumes'
import { json, RESUME, resumeList } from '../test/fixtures'

let fetchMock = vi.fn<typeof fetch>()
const authenticated: AuthenticatedRequest = (path, options) => request(path, { ...options, token: 'test-token' })
beforeEach(() => { fetchMock = vi.fn<typeof fetch>(); vi.stubGlobal('fetch', fetchMock) })

test('lists the first 100 with bearer and accepts legacy/extra fields without exposing extras', async () => {
  fetchMock.mockResolvedValue(json(resumeList([{ ...RESUME, media_type: null, size_bytes: null, status: 'legacy', private_path: '/secret' }])))
  const result = await listResumes(authenticated)
  expect(result.items[0]).toEqual({ ...RESUME, media_type: null, size_bytes: null, status: 'legacy' })
  expect(fetchMock).toHaveBeenCalledWith('/api/resumes?limit=100&offset=0', expect.objectContaining({ headers: { Accept: 'application/json', Authorization: 'Bearer test-token' } }))
})

test.each([
  { id: '../private' }, { id: '' }, { original_filename: '' }, { media_type: 4 },
  { size_bytes: 0 }, { size_bytes: -1 }, { size_bytes: 1.5 }, { size_bytes: '100' }, { size_bytes: Number.MAX_SAFE_INTEGER + 1 },
  { status: null }, { created_at: 'not a date' }, { created_at: '2026-10-11T02:00:00' },
  { created_at: '2026-02-30T02:00:00Z' }, { updated_at: '2026-10-11T24:00:00Z' },
  { updated_at: '2026-10-11T02:00:00+24:00' }, { updated_at: undefined },
])('rejects malformed metadata %# safely', async (patch) => {
  fetchMock.mockResolvedValue(json(resumeList([{ ...RESUME, ...patch }])))
  await expect(listResumes(authenticated)).rejects.toMatchObject({ kind: 'invalid_response' })
})

test.each([null, [], {}, { items: {}, limit: 100, offset: 0 }, { ...resumeList(), limit: 20 },
  { ...resumeList(), offset: 1 }, resumeList([RESUME, RESUME]), resumeList(Array(101).fill(RESUME)), resumeList([null]),
])('rejects malformed list %#', async (body) => {
  fetchMock.mockResolvedValue(json(body))
  await expect(listResumes(authenticated)).rejects.toMatchObject({ kind: 'invalid_response' })
})

test('upload sends exactly one PDF part even with unknown browser MIME and returns canonical metadata', async () => {
  fetchMock.mockResolvedValue(json(RESUME, 201))
  const file = new File(['%PDF-1.7'], 'resume.v2.PDF')
  expect(await uploadResume(authenticated, file)).toEqual(RESUME)
  const init = fetchMock.mock.calls[0][1]!
  const form = init.body as FormData
  expect([...form.keys()]).toEqual(['file'])
  const part = form.get('file') as File
  expect(part.name).toBe(file.name)
  expect(part.size).toBe(file.size)
  expect(part.type).toBe('application/pdf')
  expect(init.headers).not.toHaveProperty('Content-Type')
  expect(init.headers).toHaveProperty('Authorization', 'Bearer test-token')
})

test.each([{ size_bytes: null }, { size_bytes: MAX_RESUME_BYTES + 1 }, { media_type: null }, { status: 'pending' }])('rejects upload responses violating new-upload guarantees %#', async (patch) => {
  fetchMock.mockResolvedValue(json({ ...RESUME, ...patch }, 201))
  await expect(uploadResume(authenticated, new File(['%PDF-'], 'x.pdf'))).rejects.toMatchObject({ kind: 'invalid_response' })
})

test.each([200, 202, 204])('wrong successful upload status %s stays uncertain even with otherwise valid data', async (status) => {
  fetchMock.mockResolvedValue(status === 204 ? new Response(null, { status }) : json(RESUME, status))
  await expect(uploadResume(authenticated, new File(['%PDF-'], 'x.pdf'))).rejects.toMatchObject({ kind: 'invalid_response', status })
})

test('DELETE requires empty success and validates id before dispatch', async () => {
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 })).mockResolvedValueOnce(json({ deleted: true }))
  await expect(deleteResume(authenticated, RESUME.id)).resolves.toBeUndefined()
  await expect(deleteResume(authenticated, RESUME.id)).rejects.toMatchObject({ kind: 'invalid_response' })
  await expect(deleteResume(authenticated, '../secret')).rejects.toMatchObject({ kind: 'invalid_response' })
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

test.each([
  [404, 'resume_not_found'], [409, 'resume_in_use'], [415, 'unsupported_resume_type'],
  [503, 'resume_storage_unavailable'], [503, 'resume_unavailable'],
])('allowlists Resume %s/%s and never retries', async (status, code) => {
  fetchMock.mockResolvedValue(json({ detail: code }, status))
  await expect(uploadResume(authenticated, new File(['%PDF-'], 'x.pdf'))).rejects.toMatchObject({ kind: code, code, status })
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

test.each([[500, 'resume_in_use'], [503, '/private/SQL failure'], [422, [{ input: 'private value' }]]])('untrusted errors remain safe (%s)', async (status, detail) => {
  fetchMock.mockResolvedValue(json({ detail }, status))
  try { await listResumes(authenticated); expect.fail('expected rejection') } catch (failure) {
    expect(failure).toMatchObject({ kind: 'unexpected_server_error', code: null })
    expect(resumeErrorMessage(failure)).not.toMatch(/private|SQL|input|用户名|密码/)
  }
})

test('Resume validation copy does not change auth wording', () => {
  const failure = new ApiError('validation_error', 422, 'invalid_request')
  expect(resumeErrorMessage(failure)).toContain('PDF')
  expect(errorMessage(failure)).toContain('用户名和密码')
})

test.each(['network_failure', 'timeout', 'invalid_response', 'unexpected_server_error', 'resume_storage_unavailable', 'resume_unavailable', 'cancelled'] as const)('write %s does not prove rejection', (kind) => {
  expect(uncertainResumeWrite(new ApiError(kind))).toBe(true)
})

test('upload uses its own 60 second timeout, propagates cancellation, and does not retry', async () => {
  vi.useFakeTimers()
  fetchMock.mockImplementation((_path, init) => new Promise((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
  }))
  const file = new File(['%PDF-'], 'x.pdf')
  const timed = expect(uploadResume(authenticated, file)).rejects.toMatchObject({ kind: 'timeout' })
  await vi.advanceTimersByTimeAsync(RESUME_UPLOAD_TIMEOUT_MS)
  await timed
  const controller = new AbortController()
  const cancelled = expect(uploadResume(authenticated, file, controller.signal)).rejects.toMatchObject({ kind: 'cancelled' })
  controller.abort()
  await cancelled
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(vi.getTimerCount()).toBe(0)
})
