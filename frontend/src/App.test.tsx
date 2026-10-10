import { StrictMode, useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'
import { beforeEach, expect, test, vi } from 'vitest'
import App from './App'
import { AuthProvider } from './auth/AuthProvider'
import { useAuth } from './auth/AuthContext'
import { deferred, json, PASSWORD, token, USER_A, USER_B } from './test/fixtures'

let fetchMock = vi.fn<typeof fetch>()
beforeEach(() => { fetchMock = vi.fn<typeof fetch>(); vi.stubGlobal('fetch', fetchMock) })

function Controls() {
  const auth = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [result, setResult] = useState('idle')
  const startLogin = (username: string) => { void auth.login({ username, password: PASSWORD }).then(() => setResult('login complete'), () => setResult('login failed')) }
  return <aside aria-label="Test controls">
    <output data-testid="auth-state">{auth.status}:{auth.currentUser?.id ?? 'none'}</output>
    <output data-testid="notice">{auth.notice ?? 'none'}</output>
    <output data-testid="location">{location.pathname}:{JSON.stringify(location.state)}</output>
    <output data-testid="result">{result}</output>
    <button onClick={() => startLogin('alice')}>Begin A</button>
    <button onClick={() => startLogin('bob')}>Begin B</button>
    <button onClick={() => { void auth.logout().then(() => setResult('logout settled')) }}>End session</button>
    <button onClick={() => { void auth.requestAuthenticated('/auth/me').then(() => setResult('request complete'), () => setResult('request failed')) }}>Private request</button>
    <button onClick={() => navigate('/login')}>Go login</button>
    <button onClick={() => navigate('/register')}>Go register</button>
  </aside>
}

function renderApp(path = '/login', controls = false) {
  return render(<StrictMode><MemoryRouter initialEntries={[path]}><AuthProvider>{controls && <Controls />}<App /></AuthProvider></MemoryRouter></StrictMode>)
}

async function fillLogin(user: ReturnType<typeof userEvent.setup>, password = PASSWORD) {
  await user.type(screen.getByLabelText('用户名'), 'Alice')
  await user.type(screen.getByLabelText('密码'), password)
}

function queueLogin(id = USER_A, name = 'token-a', expires = 1800) {
  fetchMock.mockResolvedValueOnce(json(token(name, expires))).mockResolvedValueOnce(json(id))
}

async function establish() {
  queueLogin()
  fireEvent.click(screen.getByRole('button', { name: 'Begin A' }))
  await screen.findByRole('heading', { name: '在这里，开启下一程。' })
}

test('renders a semantic accessible login form with route title and focus', () => {
  renderApp()
  const heading = screen.getByRole('heading', { name: '欢迎回来' })
  expect(screen.getByRole('form', { name: '登录' })).toBeTruthy()
  expect(screen.getByLabelText('用户名').getAttribute('autocomplete')).toBe('username')
  expect(screen.getByLabelText('密码').getAttribute('autocomplete')).toBe('current-password')
  expect(document.title).toBe('登录 · Career Copilot')
  expect(document.activeElement).toBe(heading)
})

test('login uses temporary bearer and becomes authenticated only after /me; Enter submits', async () => {
  const user = userEvent.setup()
  const meResponse = deferred<Response>()
  fetchMock.mockResolvedValueOnce(json(token())).mockReturnValueOnce(meResponse.promise)
  renderApp('/login', true)
  await fillLogin(user)
  await user.keyboard('{Enter}')
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  expect(fetchMock.mock.calls[0][0]).toBe('/api/auth/login')
  expect(fetchMock.mock.calls[0][1]?.body).toBe(JSON.stringify({ username: 'Alice', password: PASSWORD }))
  expect(fetchMock.mock.calls[0][1]?.headers).not.toHaveProperty('Authorization')
  expect(fetchMock.mock.calls[1][0]).toBe('/api/auth/me')
  expect(fetchMock.mock.calls[1][1]?.headers).toHaveProperty('Authorization', 'Bearer token-a')
  expect(screen.getByTestId('auth-state').textContent).toBe('anonymous:none')
  expect(screen.getByRole('button', { name: '正在登录…' }).hasAttribute('disabled')).toBe(true)
  fireEvent.submit(screen.getByRole('form', { name: '登录' }))
  expect(fetchMock).toHaveBeenCalledTimes(2)
  await act(async () => { meResponse.resolve(json(USER_A)) })
  expect(await screen.findByRole('heading', { name: '在这里，开启下一程。' })).toBeTruthy()
  expect(screen.getByTestId('auth-state').textContent).toBe(`authenticated:${USER_A.id}`)
  expect(screen.getByTestId('location').textContent).toBe('/app:null')
  expect(document.title).toBe('工作台 · Career Copilot')
})

test.each([
  ['credentials', () => json({ detail: 'invalid_credentials' }, 401), /用户名或密码不正确/],
  ['unavailable', () => json({ detail: 'authentication_unavailable' }, 503), /暂时不可用/],
  ['network', () => { throw new TypeError('offline') }, /无法连接服务/],
])('login %s remains anonymous with a safe error', async (_name, response, message) => {
  fetchMock.mockImplementationOnce(async () => response())
  renderApp('/login', true)
  const user = userEvent.setup()
  await fillLogin(user, 'wrong')
  await user.click(screen.getByRole('button', { name: '登录' }))
  expect(await screen.findByText(message)).toBeTruthy()
  expect(screen.getByTestId('auth-state').textContent).toBe('anonymous:none')
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('form', { name: '登录' }).getAttribute('aria-describedby')).toBe('login-error')
  expect(screen.getByRole('button', { name: '登录' }).hasAttribute('disabled')).toBe(false)
})

test.each([
  ['401', () => json({ detail: 'authentication_required' }, 401), /无法验证登录状态/],
  ['503', () => json({ detail: 'authentication_unavailable' }, 503), /暂时不可用/],
  ['network', () => { throw new TypeError('offline') }, /无法连接服务/],
  ['invalid response', () => json({ id: USER_A.id, status: 'disabled' }), /服务响应异常/],
])('/me %s discards temporary credentials and does not imply a password error', async (_name, response, message) => {
  fetchMock.mockResolvedValueOnce(json(token())).mockImplementationOnce(async () => response())
  renderApp('/login', true)
  const user = userEvent.setup()
  await fillLogin(user)
  await user.click(screen.getByRole('button', { name: '登录' }))
  expect(await screen.findByText(message)).toBeTruthy()
  expect(screen.getByTestId('auth-state').textContent).toBe('anonymous:none')
  expect(screen.queryByText(/用户名或密码不正确/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Private request' }))
  await waitFor(() => expect(screen.getByTestId('result').textContent).toBe('request failed'))
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

test('synchronous duplicate submits start only one login request', async () => {
  const pending = deferred<Response>()
  fetchMock.mockReturnValueOnce(pending.promise)
  renderApp()
  const user = userEvent.setup()
  await fillLogin(user)
  const form = screen.getByRole('form', { name: '登录' })
  act(() => { fireEvent.submit(form); fireEvent.submit(form) })
  expect(fetchMock).toHaveBeenCalledTimes(1)
  await act(async () => { pending.resolve(json({ detail: 'invalid_credentials' }, 401)) })
  expect(await screen.findByText(/用户名或密码不正确/)).toBeTruthy()
})

test('registration redirects to login with success and never logs in or calls /me', async () => {
  fetchMock.mockResolvedValueOnce(json({ ...USER_A, username: 'alice' }, 201))
  renderApp('/register', true)
  const user = userEvent.setup()
  expect(screen.getByLabelText('密码').getAttribute('autocomplete')).toBe('new-password')
  await user.type(screen.getByLabelText('用户名'), 'Alice')
  await user.type(screen.getByLabelText('密码'), PASSWORD)
  await user.click(screen.getByRole('button', { name: '创建账号' }))
  expect(await screen.findByText('账号创建成功，请登录以继续。')).toBeTruthy()
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(fetchMock.mock.calls[0][0]).toBe('/api/auth/register')
  expect(screen.getByTestId('auth-state').textContent).toBe('anonymous:none')
  expect(screen.getByTestId('location').textContent).toBe('/login:{"registered":true}')
})

test.each([
  [409, 'username_unavailable', /用户名不可用/],
  [422, 'invalid_request', /检查用户名和密码/],
  [413, 'request_too_large', /输入内容过长/],
  [503, 'authentication_unavailable', /暂时不可用/],
])('registration %s reports safely without authentication', async (status, detail, message) => {
  fetchMock.mockResolvedValueOnce(json({ detail }, status))
  renderApp('/register', true)
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('用户名'), 'alice')
  await user.type(screen.getByLabelText('密码'), PASSWORD)
  await user.click(screen.getByRole('button', { name: '创建账号' }))
  expect(await screen.findByText(message)).toBeTruthy()
  expect(screen.getByTestId('auth-state').textContent).toBe('anonymous:none')
  expect(screen.getByRole('form', { name: '创建账号' }).getAttribute('aria-describedby')).toBe('register-error')
})

test('registration network failure is retryable', async () => {
  fetchMock.mockRejectedValueOnce(new TypeError('offline'))
  renderApp('/register')
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('用户名'), 'alice')
  await user.type(screen.getByLabelText('密码'), PASSWORD)
  await user.click(screen.getByRole('button', { name: '创建账号' }))
  expect(await screen.findByText(/无法连接服务/)).toBeTruthy()
  expect(screen.getByRole('button', { name: '创建账号' }).hasAttribute('disabled')).toBe(false)
})

test.each(['short', '               '])('registration gives password guidance before sending (%s)', async (password) => {
  renderApp('/register')
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('用户名'), 'alice')
  await user.type(screen.getByLabelText('密码'), password)
  await user.click(screen.getByRole('button', { name: '创建账号' }))
  expect(await screen.findByText(/密码需包含 15–128 个字符/)).toBeTruthy()
  expect(fetchMock).not.toHaveBeenCalled()
})

test('does not reject Unicode passwords using UTF-16 maxLength or transform the submitted password', async () => {
  const password = '😀'.repeat(128)
  fetchMock.mockResolvedValueOnce(json({ ...USER_A, username: 'alice' }, 201))
  renderApp('/register')
  const input = screen.getByLabelText('密码')
  expect(input.hasAttribute('maxlength')).toBe(false)
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'alice' } })
  fireEvent.change(input, { target: { value: password } })
  fireEvent.submit(screen.getByRole('form', { name: '创建账号' }))
  await screen.findByText('账号创建成功，请登录以继续。')
  expect(fetchMock.mock.calls[0][1]?.body).toBe(JSON.stringify({ username: 'alice', password }))
})

test.each(['/', '/app'])('anonymous %s redirects to login without fetch', async (path) => {
  renderApp(path, true)
  expect(await screen.findByRole('form', { name: '登录' })).toBeTruthy()
  expect(screen.getByTestId('location').textContent).toBe('/login:null')
  expect(fetchMock).not.toHaveBeenCalled()
})

test('authenticated public routes redirect to workspace', async () => {
  renderApp('/login', true)
  await establish()
  for (const name of ['Go login', 'Go register']) {
    fireEvent.click(screen.getByRole('button', { name }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/app:null'))
    expect(screen.queryByRole('form')).toBeNull()
  }
})

test('unknown URL renders not found with a safe return link', () => {
  renderApp('/unknown')
  expect(screen.getByRole('heading', { name: '这个页面不存在' })).toBeTruthy()
  expect(screen.getByRole('link', { name: '返回登录' }).getAttribute('href')).toBe('/login')
  expect(document.title).toBe('页面未找到 · Career Copilot')
})

test('keeps credentials in memory only; a fresh Provider starts anonymous without restoring', async () => {
  const local = vi.spyOn(localStorage, 'setItem')
  const session = vi.spyOn(sessionStorage, 'setItem')
  const open = vi.fn()
  vi.stubGlobal('indexedDB', { open })
  const cookie = vi.spyOn(document, 'cookie', 'set')
  const first = renderApp('/login', true)
  await establish()
  expect(local).not.toHaveBeenCalled()
  expect(session).not.toHaveBeenCalled()
  expect(open).not.toHaveBeenCalled()
  expect(cookie).not.toHaveBeenCalled()
  expect(screen.getByTestId('location').textContent).not.toContain('token-a')
  for (const [, init] of fetchMock.mock.calls) expect(init?.credentials).toBe('omit')
  first.unmount()
  renderApp('/app', true)
  expect(await screen.findByRole('form', { name: '登录' })).toBeTruthy()
  expect(screen.getByTestId('auth-state').textContent).toBe('anonymous:none')
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

test('current private 401 clears auth and protected route returns to login', async () => {
  renderApp('/login', true)
  await establish()
  fetchMock.mockResolvedValueOnce(json({ detail: 'authentication_required' }, 401))
  fireEvent.click(screen.getByRole('button', { name: 'Private request' }))
  expect(await screen.findByRole('form', { name: '登录' })).toBeTruthy()
  expect(screen.getByTestId('auth-state').textContent).toBe('anonymous:none')
  expect(screen.getByTestId('notice').textContent).toBe('session-ended')
})

test.each([
  ['503', () => json({ detail: 'authentication_unavailable' }, 503)],
  ['network', () => { throw new TypeError('offline') }],
  ['unknown 401', () => json({ detail: 'untrusted detail' }, 401)],
])('private %s preserves the current Session', async (_name, response) => {
  renderApp('/login', true)
  await establish()
  fetchMock.mockImplementationOnce(async () => response())
  fireEvent.click(screen.getByRole('button', { name: 'Private request' }))
  await waitFor(() => expect(screen.getByTestId('result').textContent).toBe('request failed'))
  expect(screen.getByTestId('auth-state').textContent).toBe(`authenticated:${USER_A.id}`)
})

test('late Session A private 401 cannot clear Session B', async () => {
  renderApp('/login', true)
  await establish()
  const old = deferred<Response>()
  fetchMock.mockReturnValueOnce(old.promise)
  fireEvent.click(screen.getByRole('button', { name: 'Private request' }))
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
  fireEvent.click(screen.getByRole('button', { name: 'End session' }))
  queueLogin(USER_B, 'token-b')
  fireEvent.click(screen.getByRole('button', { name: 'Begin B' }))
  await waitFor(() => expect(screen.getByTestId('auth-state').textContent).toBe(`authenticated:${USER_B.id}`))
  await act(async () => { old.resolve(json({ detail: 'authentication_required' }, 401)) })
  expect(screen.getByTestId('auth-state').textContent).toBe(`authenticated:${USER_B.id}`)
  expect(screen.getByTestId('notice').textContent).toBe('none')
})

test('superseded login /me result cannot restore old Session', async () => {
  const oldMe = deferred<Response>()
  fetchMock.mockResolvedValueOnce(json(token())).mockReturnValueOnce(oldMe.promise)
  renderApp('/login', true)
  fireEvent.click(screen.getByRole('button', { name: 'Begin A' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  fireEvent.click(screen.getByRole('button', { name: 'End session' }))
  queueLogin(USER_B, 'token-b')
  fireEvent.click(screen.getByRole('button', { name: 'Begin B' }))
  await waitFor(() => expect(screen.getByTestId('auth-state').textContent).toBe(`authenticated:${USER_B.id}`))
  await act(async () => { oldMe.resolve(json(USER_A)) })
  expect(screen.getByTestId('auth-state').textContent).toBe(`authenticated:${USER_B.id}`)
})

test('leaving login page cancels pending identity establishment', async () => {
  const oldMe = deferred<Response>()
  fetchMock.mockResolvedValueOnce(json(token())).mockReturnValueOnce(oldMe.promise)
  renderApp('/login', true)
  fireEvent.click(screen.getByRole('button', { name: 'Begin A' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  fireEvent.click(screen.getByRole('button', { name: 'Go register' }))
  await screen.findByRole('form', { name: '创建账号' })
  await act(async () => { oldMe.resolve(json(USER_A)) })
  expect(screen.getByTestId('auth-state').textContent).toBe('anonymous:none')
})

test.each([
  ['204', () => new Response(null, { status: 204 }), 'signed-out'],
  ['401', () => json({ detail: 'authentication_required' }, 401), 'signed-out'],
  ['503', () => json({ detail: 'authentication_unavailable' }, 503), 'logout-unconfirmed'],
  ['network', () => { throw new TypeError('offline') }, 'logout-unconfirmed'],
  ['unexpected', () => new Response('bad gateway', { status: 502 }), 'logout-unconfirmed'],
])('logout %s clears locally and reports revocation accurately', async (_name, response, expectedNotice) => {
  renderApp('/login', true)
  await establish()
  fetchMock.mockImplementationOnce(async () => response())
  fireEvent.click(screen.getByRole('button', { name: '退出登录' }))
  expect(await screen.findByRole('form', { name: '登录' })).toBeTruthy()
  await waitFor(() => expect(screen.getByTestId('notice').textContent).toBe(expectedNotice))
  expect(screen.getByTestId('auth-state').textContent).toBe('anonymous:none')
  expect(fetchMock.mock.calls[2][0]).toBe('/api/auth/logout')
  expect(fetchMock.mock.calls[2][1]?.headers).toHaveProperty('Authorization', 'Bearer token-a')
  if (expectedNotice === 'logout-unconfirmed') expect(screen.getByText(/无法确认服务器上的登录会话是否已撤销/)).toBeTruthy()
})

test.each(['success', 'failure'])('old logout %s cannot affect new Session or its notice', async (outcome) => {
  renderApp('/login', true)
  await establish()
  const old = deferred<Response>()
  fetchMock.mockReturnValueOnce(old.promise)
  fireEvent.click(screen.getByRole('button', { name: 'End session' }))
  expect(screen.getByTestId('auth-state').textContent).toBe('anonymous:none')
  queueLogin(USER_B, 'token-b')
  fireEvent.click(screen.getByRole('button', { name: 'Begin B' }))
  await waitFor(() => expect(screen.getByTestId('auth-state').textContent).toBe(`authenticated:${USER_B.id}`))
  await act(async () => { old.resolve(outcome === 'success' ? new Response(null, { status: 204 }) : json({ detail: 'authentication_unavailable' }, 503)) })
  expect(screen.getByTestId('auth-state').textContent).toBe(`authenticated:${USER_B.id}`)
  expect(screen.getByTestId('notice').textContent).toBe('none')
})

test('local expiry timer clears auth using expires_in without decoding the token', async () => {
  vi.useFakeTimers()
  queueLogin(USER_A, 'opaque-credential', 5)
  renderApp('/login', true)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Begin A' })) })
  expect(screen.getByTestId('auth-state').textContent).toBe(`authenticated:${USER_A.id}`)
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(screen.getByTestId('auth-state').textContent).toBe('anonymous:none')
  expect(screen.getByRole('form', { name: '登录' })).toBeTruthy()
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

test('visibility/resume rechecks deadline even if a background timer did not run', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-10T00:00:00Z'))
  queueLogin(USER_A, 'opaque-credential', 5)
  renderApp('/login', true)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Begin A' })) })
  vi.setSystemTime(new Date('2026-10-10T00:00:06Z'))
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  act(() => { document.dispatchEvent(new Event('visibilitychange')) })
  expect(screen.getByTestId('auth-state').textContent).toBe('anonymous:none')
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

test('logout timeout still leaves browser anonymous with unconfirmed revocation', async () => {
  vi.useFakeTimers()
  renderApp('/login', true)
  queueLogin()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Begin A' })) })
  fetchMock.mockImplementationOnce((_input, init) => new Promise((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
  }))
  act(() => { fireEvent.click(screen.getByRole('button', { name: '退出登录' })) })
  expect(screen.getByTestId('auth-state').textContent).toBe('anonymous:none')
  await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })
  expect(screen.getByTestId('notice').textContent).toBe('logout-unconfirmed')
})

test('keyboard tab order reaches labeled inputs and submit', async () => {
  const user = userEvent.setup()
  renderApp()
  await user.tab()
  expect(document.activeElement).toBe(screen.getByLabelText('用户名'))
  await user.tab()
  expect(document.activeElement).toBe(screen.getByLabelText('密码'))
  await user.tab()
  expect(document.activeElement).toBe(screen.getByRole('button', { name: '登录' }))
})
