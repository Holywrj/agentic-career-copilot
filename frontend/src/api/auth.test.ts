import { beforeEach, expect, test, vi } from 'vitest'
import { login, logout, me, register } from './auth'
import { json, PASSWORD, token, USER_A } from '../test/fixtures'

let fetchMock = vi.fn<typeof fetch>()
beforeEach(() => { fetchMock = vi.fn<typeof fetch>(); vi.stubGlobal('fetch', fetchMock) })

test.each([
  { ...token(), access_token: '' },
  { ...token(), access_token: 'with whitespace' },
  { ...token(), token_type: 'cookie' },
  { ...token(), expires_in: 0 },
  { ...token(), expires_in: -1 },
  { ...token(), expires_in: 0.5 },
  { ...token(), expires_in: 3601 },
  { ...token(), expires_in: '1800' },
  null,
])('rejects invalid login response %#', async (body) => {
  fetchMock.mockResolvedValue(json(body))
  await expect(login({ username: 'alice', password: PASSWORD })).rejects.toMatchObject({ kind: 'invalid_response' })
})

test.each([{ ...USER_A, status: 'disabled' }, { ...USER_A, id: 'wrong' }, { id: USER_A.id }, null])('rejects invalid current user %#', async (body) => {
  fetchMock.mockResolvedValue(json(body))
  await expect(me('temporary')).rejects.toMatchObject({ kind: 'invalid_response' })
})

test('validates registration fields without creating a Session', async () => {
  fetchMock.mockResolvedValueOnce(json({ ...USER_A, username: 'alice' }, 201))
  expect(await register({ username: '  ALICE  ', password: PASSWORD })).toEqual({ ...USER_A, username: 'alice' })
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(fetchMock.mock.calls[0][1]?.body).toBe(JSON.stringify({ username: '  ALICE  ', password: PASSWORD }))
  fetchMock.mockResolvedValueOnce(json(USER_A, 201))
  await expect(register({ username: 'alice', password: PASSWORD })).rejects.toMatchObject({ kind: 'invalid_response' })
})

test('logout expects an empty response', async () => {
  fetchMock.mockResolvedValue(json({ revoked: true }))
  await expect(logout('temporary')).rejects.toMatchObject({ kind: 'invalid_response' })
})
