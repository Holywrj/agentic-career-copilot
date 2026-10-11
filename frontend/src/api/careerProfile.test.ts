import { beforeEach, expect, test, vi } from 'vitest'
import { ApiError, errorMessage, request } from './client'
import {
  careerProfileErrorMessage, createCareerProfile, getCareerProfile, PROFILE_TIMEOUT_MS, uncertainWrite, updateCareerProfile,
} from './careerProfile'
import type { AuthenticatedRequest } from './client'
import { json, PROFILE } from '../test/fixtures'

let fetchMock = vi.fn<typeof fetch>()
const authenticated: AuthenticatedRequest = (path, options) => request(path, { ...options, token: 'test-token' })
beforeEach(() => { fetchMock = vi.fn<typeof fetch>(); vi.stubGlobal('fetch', fetchMock) })

test('GET parses the server profile through the authenticated boundary and copies arrays', async () => {
  const boundary = { request: authenticated }
  const requester = vi.spyOn(boundary, 'request').mockResolvedValue(PROFILE)
  const controller = new AbortController()
  const result = await getCareerProfile(boundary.request, controller.signal)
  expect(requester).toHaveBeenCalledWith('/career-profile', { signal: controller.signal, timeoutMs: PROFILE_TIMEOUT_MS })
  expect(result).toEqual(PROFILE)
  expect(result.skills).not.toBe(PROFILE.skills)
  expect(result.target_roles).not.toBe(PROFILE.target_roles)
})

test.each([
  null, [], {}, { ...PROFILE, revision: 0 }, { ...PROFILE, revision: -1 },
  { ...PROFILE, revision: 1.2 }, { ...PROFILE, revision: '3' }, { ...PROFILE, revision: true },
  { ...PROFILE, revision: Number.MAX_SAFE_INTEGER + 1 }, { ...PROFILE, career_summary: 5 },
  { ...PROFILE, career_summary: undefined }, { ...PROFILE, skills: null },
  { ...PROFILE, skills: [4] }, { ...PROFILE, target_roles: 'AI' }, { ...PROFILE, target_roles: [null] },
  { ...PROFILE, created_at: undefined }, { ...PROFILE, updated_at: 4 },
])('rejects malformed success %#', async (body) => {
  fetchMock.mockResolvedValue(json(body))
  await expect(getCareerProfile(authenticated)).rejects.toMatchObject({ kind: 'invalid_response' })
})

test('POST accepts explicit empty fields or defaults without client-owned revision', async () => {
  fetchMock.mockImplementation(async () => json({ ...PROFILE, revision: 1, career_summary: null, skills: [], target_roles: [] }, 201))
  const data = { career_summary: null, skills: [], target_roles: [] }
  const created = await createCareerProfile(authenticated, data)
  expect(created.revision).toBe(1)
  expect(fetchMock).toHaveBeenCalledWith('/api/career-profile', expect.objectContaining({
    method: 'POST', body: JSON.stringify(data), credentials: 'omit',
    headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
  }))
  await createCareerProfile(authenticated, {})
  expect(fetchMock.mock.calls[1][1]?.body).toBe('{}')
})

test('PUT includes loaded expected_revision and every replacement field; uses server revision', async () => {
  fetchMock.mockResolvedValue(json({ ...PROFILE, revision: 17 }))
  const data = { expected_revision: PROFILE.revision, career_summary: null, skills: [], target_roles: [] }
  expect((await updateCareerProfile(authenticated, data)).revision).toBe(17)
  expect(fetchMock).toHaveBeenCalledWith('/api/career-profile', expect.objectContaining({ method: 'PUT', body: JSON.stringify(data) }))
})

test.each([
  [401, 'authentication_required', 'authentication_required'],
  [404, 'career_profile_not_found', 'career_profile_not_found'],
  [409, 'career_profile_already_exists', 'career_profile_already_exists'],
  [409, 'career_profile_conflict', 'career_profile_conflict'],
  [413, 'request_too_large', 'validation_error'],
  [422, 'invalid_request', 'validation_error'],
  [503, 'career_profile_unavailable', 'career_profile_unavailable'],
])('maps Profile %s %s safely', async (status, code, kind) => {
  fetchMock.mockResolvedValue(json({ detail: code }, status))
  await expect(getCareerProfile(authenticated)).rejects.toMatchObject({ status, code, kind })
})

test.each([
  [401, 'career_profile_conflict'], [404, 'career_profile_conflict'],
  [409, 'career_profile_unavailable'], [500, 'career_profile_not_found'],
  [503, 'private database connection string'], [422, [{ input: 'private draft' }]],
])('does not trust mismatched or raw errors (%s)', async (status, detail) => {
  fetchMock.mockResolvedValue(json({ detail }, status))
  try { await getCareerProfile(authenticated); expect.fail('expected rejection') }
  catch (error) {
    expect(error).toMatchObject({ kind: 'unexpected_server_error', code: null })
    expect(careerProfileErrorMessage(error)).not.toMatch(/private|database|input|用户名|密码/)
  }
})

test('HTML errors and malformed success stay safe and writes require reconciliation', async () => {
  fetchMock.mockResolvedValueOnce(new Response('<html>private proxy error</html>', { status: 502 }))
  try { await createCareerProfile(authenticated, {}); expect.fail('expected rejection') }
  catch (error) { expect(uncertainWrite(error)).toBe(true); expect(careerProfileErrorMessage(error)).not.toContain('private') }
  fetchMock.mockResolvedValueOnce(json({ saved: true }))
  await expect(updateCareerProfile(authenticated, { expected_revision: 3, career_summary: null, skills: [], target_roles: [] })).rejects.toMatchObject({ kind: 'invalid_response' })
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

test('Profile validation copy is separate from unchanged authentication copy', () => {
  const error = new ApiError('validation_error', 422, 'invalid_request')
  expect(careerProfileErrorMessage(error)).toContain('职业概述、技能和目标岗位')
  expect(errorMessage(error)).toContain('用户名和密码')
})

test.each(['network_failure', 'timeout', 'invalid_response', 'unexpected_server_error', 'career_profile_unavailable', 'authentication_unavailable'] as const)('uncertain %s write is not a safe automatic retry', (kind) => {
  expect(uncertainWrite(new ApiError(kind))).toBe(true)
})

test.each(['validation_error', 'authentication_required', 'cancelled', 'career_profile_conflict', 'career_profile_already_exists', 'career_profile_not_found'] as const)('recognized %s rejection is not an uncertain write', (kind) => {
  expect(uncertainWrite(new ApiError(kind))).toBe(false)
})
