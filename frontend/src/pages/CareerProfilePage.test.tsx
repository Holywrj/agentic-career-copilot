import { StrictMode } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, expect, test, vi } from 'vitest'
import { AuthContext } from '../auth/AuthContext'
import type { AuthContextValue } from '../auth/AuthContext'
import { request } from '../api/client'
import type { AuthenticatedRequest } from '../api/client'
import { PROFILE_TIMEOUT_MS } from '../api/careerProfile'
import { CareerProfilePage } from './CareerProfilePage'
import { deferred, json, PROFILE, USER_A } from '../test/fixtures'

let fetchMock = vi.fn<typeof fetch>()
let logout = vi.fn<() => Promise<void>>()
const authenticated: AuthenticatedRequest = (path, options) => request(path, { ...options, token: 'test-token' })

beforeEach(() => {
  fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => json(PROFILE))
  logout = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
  vi.stubGlobal('fetch', fetchMock)
})

function renderPage(requestAuthenticated = authenticated) {
  const auth: AuthContextValue = {
    status: 'authenticated', currentUser: USER_A as AuthContextValue['currentUser'],
    loginPending: false, notice: null, login: vi.fn(), cancelLogin: vi.fn(), logout, requestAuthenticated,
  }
  return render(<StrictMode><MemoryRouter initialEntries={['/app/profile']}><AuthContext.Provider value={auth}>
    <Routes><Route path="/app/profile" element={<CareerProfilePage />} />
      <Route path="/app" element={<h1>工作台</h1>} /><Route path="/" element={<h1>首页</h1>} />
    </Routes>
  </AuthContext.Provider></MemoryRouter></StrictMode>)
}

async function loaded() {
  await screen.findByRole('form', { name: '编辑职业档案' })
  await waitFor(() => expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe(PROFILE.career_summary))
}

function changeSummary(value = '我正在编辑的草稿') {
  fireEvent.change(screen.getByLabelText('职业概述'), { target: { value } })
}

function writeCalls(method: string) { return fetchMock.mock.calls.filter(([, init]) => init?.method === method) }
function recoveryPanel() { return screen.getByRole('region', { name: /需要确认服务器状态|写入结果待确认/ }) }
async function expectLatest(revision: number) { await screen.findByRole('heading', { name: `最新服务器状态 · revision ${revision}` }) }

test('initial GET uses authenticated transport, has loading state, and loads server values', async () => {
  const pending = deferred<Response>()
  fetchMock.mockReturnValue(pending.promise)
  renderPage()
  expect(screen.getByRole('status').textContent).toBe('正在加载职业档案…')
  expect(screen.queryByRole('form')).toBeNull()
  expect(fetchMock.mock.calls.every(([path, init]) => path === '/api/career-profile' && init?.method === 'GET')).toBe(true)
  await act(async () => { pending.resolve(json(PROFILE)) })
  await loaded()
  expect(screen.getByText('当前编辑基线：revision 3')).toBeTruthy()
  expect(screen.getByRole('button', { name: '保存档案' }).hasAttribute('disabled')).toBe(true)
  expect(screen.getByRole('button', { name: '删除技能 Python' })).toBeTruthy()
})

test('404 onboarding allows explicit empty creation; server response establishes canonical state', async () => {
  fetchMock.mockImplementation(async () => json({ detail: 'career_profile_not_found' }, 404))
  renderPage()
  const create = await screen.findByRole('button', { name: '创建档案' })
  expect(create.hasAttribute('disabled')).toBe(false)
  const empty = { ...PROFILE, revision: 1, career_summary: null, skills: [], target_roles: [] }
  fetchMock.mockImplementationOnce(async () => json(empty, 201))
  await userEvent.setup().click(create)
  expect(await screen.findByText('职业档案已保存，当前显示服务器确认的数据。')).toBeTruthy()
  expect(writeCalls('POST')).toHaveLength(1)
  expect(JSON.parse(String(writeCalls('POST')[0][1]?.body))).toEqual({ career_summary: null, skills: [], target_roles: [] })
  expect(screen.getByText('当前编辑基线：revision 1')).toBeTruthy()
  expect(screen.getByRole('button', { name: '保存档案' }).hasAttribute('disabled')).toBe(true)
})

test('create populated profile and replace draft with normalized server fields', async () => {
  fetchMock.mockImplementation(async () => json({ detail: 'career_profile_not_found' }, 404))
  renderPage()
  await screen.findByRole('button', { name: '创建档案' })
  changeSummary('  新的职业方向  ')
  const server = { ...PROFILE, revision: 1, career_summary: '新的职业方向', skills: [], target_roles: [] }
  fetchMock.mockImplementationOnce(async () => json(server, 201))
  fireEvent.submit(screen.getByRole('form'))
  await screen.findByText('职业档案已保存，当前显示服务器确认的数据。')
  expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe(server.career_summary)
  expect(JSON.parse(String(writeCalls('POST')[0][1]?.body)).career_summary).toBe('  新的职业方向  ')
})

test('PUT uses loaded revision, replaces all business fields, then uses returned revision for next save', async () => {
  renderPage(); await loaded()
  changeSummary('  新方向  ')
  const server = { ...PROFILE, revision: 19, career_summary: '新方向', skills: ['PYTHON'], target_roles: [] }
  fetchMock.mockImplementationOnce(async () => json(server))
  fireEvent.submit(screen.getByRole('form'))
  await screen.findByText('职业档案已保存，当前显示服务器确认的数据。')
  expect(JSON.parse(String(writeCalls('PUT')[0][1]?.body))).toEqual({
    expected_revision: 3, career_summary: '  新方向  ', skills: PROFILE.skills, target_roles: PROFILE.target_roles,
  })
  expect(screen.getByText('当前编辑基线：revision 19')).toBeTruthy()
  expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe('新方向')
  expect(screen.queryByRole('button', { name: '删除技能 FastAPI' })).toBeNull()
  expect(screen.getByRole('button', { name: '保存档案' }).hasAttribute('disabled')).toBe(true)
  changeSummary('继续编辑')
  fetchMock.mockImplementationOnce(async () => json({ ...server, revision: 23, career_summary: '继续编辑' }))
  fireEvent.submit(screen.getByRole('form'))
  await screen.findByText('当前编辑基线：revision 23')
  expect(JSON.parse(String(writeCalls('PUT')[1][1]?.body))).toEqual({ expected_revision: 19, career_summary: '继续编辑', skills: ['PYTHON'], target_roles: [] })
})

test('keyboard adds and removes skills and target roles without submitting the profile', async () => {
  const user = userEvent.setup()
  renderPage(); await loaded()
  for (const [label, value] of [['技能', 'TypeScript'], ['目标岗位', '全栈工程师']]) {
    const input = screen.getByLabelText(`添加${label}`)
    await user.type(input, value)
    await user.keyboard('{Enter}')
    expect(document.activeElement).toBe(input)
    const remove = screen.getByRole('button', { name: `删除${label} ${value}` })
    remove.focus(); await user.keyboard('{Enter}')
    expect(screen.queryByRole('button', { name: `删除${label} ${value}` })).toBeNull()
    expect(document.activeElement).toBe(input)
  }
  expect(writeCalls('POST')).toHaveLength(0)
  expect(writeCalls('PUT')).toHaveLength(0)
  expect(screen.getByRole('button', { name: '保存档案' }).hasAttribute('disabled')).toBe(true)
})

test.each(['技能', '目标岗位'])('IME composition Enter does not add %s or submit Profile; Enter after composition adds once', async (label) => {
  renderPage(); await loaded()
  const input = screen.getByLabelText(`添加${label}`)
  const list = screen.getByRole('list', { name: `已添加的${label}` })
  const originalCount = within(list).getAllByRole('listitem').length
  input.focus()
  fireEvent.compositionStart(input)
  fireEvent.change(input, { target: { value: '中文候选' } })
  fireEvent.compositionUpdate(input, { data: '中文候选' })
  fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', isComposing: true })
  fireEvent.keyUp(input, { key: 'Enter', code: 'Enter', isComposing: true })
  expect(within(list).getAllByRole('listitem')).toHaveLength(originalCount)
  expect((input as HTMLInputElement).value).toBe('中文候选')
  expect(writeCalls('POST')).toHaveLength(0)
  expect(writeCalls('PUT')).toHaveLength(0)

  fireEvent.compositionEnd(input, { data: '中文候选' })
  await userEvent.setup().keyboard('{Enter}')
  expect(within(list).getAllByRole('listitem')).toHaveLength(originalCount + 1)
  expect(screen.getByRole('button', { name: `删除${label} 中文候选` })).toBeTruthy()
  expect((input as HTMLInputElement).value).toBe('')
  expect(writeCalls('POST')).toHaveLength(0)
  expect(writeCalls('PUT')).toHaveLength(0)
})

test('PUT explicitly clears summary and both lists', async () => {
  const user = userEvent.setup()
  renderPage(); await loaded()
  changeSummary('')
  for (const label of ['删除技能 Python', '删除技能 FastAPI', '删除目标岗位 AI 工程师']) await user.click(screen.getByRole('button', { name: label }))
  fetchMock.mockImplementationOnce(async () => json({ ...PROFILE, revision: 4, career_summary: null, skills: [], target_roles: [] }))
  fireEvent.submit(screen.getByRole('form'))
  await screen.findByText('当前编辑基线：revision 4')
  expect(JSON.parse(String(writeCalls('PUT')[0][1]?.body))).toEqual({ expected_revision: 3, career_summary: null, skills: [], target_roles: [] })
})

test('unadded list input is retained and prevents accidental save', async () => {
  renderPage(); await loaded()
  changeSummary()
  fireEvent.change(screen.getByLabelText('添加技能'), { target: { value: '未添加技能' } })
  fireEvent.submit(screen.getByRole('form'))
  expect(await screen.findByText('列表输入框还有未添加的内容，请先添加或清空，再保存档案。')).toBeTruthy()
  expect(writeCalls('PUT')).toHaveLength(0)
})

test('Unicode summary uses normalized code points rather than HTML maxLength', async () => {
  renderPage(); await loaded()
  expect(screen.getByLabelText('职业概述').hasAttribute('maxlength')).toBe(false)
  changeSummary('😀'.repeat(2000))
  expect(screen.getByText('规范化后字符数：2000 / 2000')).toBeTruthy()
  fetchMock.mockImplementationOnce(async () => json({ ...PROFILE, revision: 4, career_summary: '😀'.repeat(2000) }))
  fireEvent.submit(screen.getByRole('form'))
  await screen.findByText('当前编辑基线：revision 4')
  changeSummary('😀'.repeat(2001))
  fireEvent.submit(screen.getByRole('form'))
  expect(await screen.findByText('职业概述最多 2000 个 Unicode 字符，请缩短后保存。')).toBeTruthy()
  expect(writeCalls('PUT')).toHaveLength(1)
})

test('list limits support supplementary Unicode characters and reject blank/too-long names', async () => {
  const user = userEvent.setup()
  renderPage(); await loaded()
  for (const [label, length] of [['技能', 80], ['目标岗位', 120]] as const) {
    const input = screen.getByLabelText(`添加${label}`)
    expect(input.hasAttribute('maxlength')).toBe(false)
    fireEvent.change(input, { target: { value: '😀'.repeat(length) } })
    await user.click(screen.getByRole('button', { name: `添加${label}` }))
    expect(screen.getByRole('button', { name: `删除${label} ${'😀'.repeat(length)}` })).toBeTruthy()
    fireEvent.change(input, { target: { value: '😀'.repeat(length + 1) } })
    await user.click(screen.getByRole('button', { name: `添加${label}` }))
    expect(await screen.findByText(`请输入单行${label}，每项 1–${length} 个 Unicode 字符，不能包含控制字符。`)).toBeTruthy()
  }
})

test('raw list count limits prevent extra additions even when the extra name is a duplicate', async () => {
  const full = { ...PROFILE, skills: Array.from({ length: 100 }, (_, i) => `Skill ${i}`), target_roles: Array.from({ length: 10 }, (_, i) => `Role ${i}`) }
  fetchMock.mockImplementation(async () => json(full))
  renderPage(); await loaded()
  for (const [label, value, count] of [['技能', 'Skill 0', 100], ['目标岗位', 'Role 0', 10]] as const) {
    fireEvent.change(screen.getByLabelText(`添加${label}`), { target: { value } })
    await userEvent.setup().click(screen.getByRole('button', { name: `添加${label}` }))
    expect(await screen.findByText(`${label}最多添加 ${count} 项。`)).toBeTruthy()
    expect(within(screen.getByRole('list', { name: `已添加的${label}` })).getAllByRole('listitem')).toHaveLength(count)
  }
})

test('server deduplication replaces raw UI list after successful save', async () => {
  const user = userEvent.setup()
  renderPage(); await loaded()
  await user.type(screen.getByLabelText('添加技能'), 'python')
  await user.keyboard('{Enter}')
  fetchMock.mockImplementationOnce(async () => json({ ...PROFILE, revision: 4 }))
  fireEvent.submit(screen.getByRole('form'))
  await screen.findByText('当前编辑基线：revision 4')
  expect(JSON.parse(String(writeCalls('PUT')[0][1]?.body)).skills).toEqual(['Python', 'FastAPI', 'python'])
  expect(screen.queryByRole('button', { name: '删除技能 python' })).toBeNull()
  expect(screen.getByRole('button', { name: '保存档案' }).hasAttribute('disabled')).toBe(true)
})

test.each([
  [422, 'invalid_request', '请检查职业概述、技能和目标岗位是否符合要求。'],
  [413, 'request_too_large', '档案内容过大，请缩短职业概述或减少列表内容。'],
])('recognized %s validation rejection keeps draft without reconciliation', async (status, detail, message) => {
  renderPage(); await loaded(); changeSummary()
  const before = fetchMock.mock.calls.length
  fetchMock.mockImplementationOnce(async () => json({ detail }, status))
  fireEvent.submit(screen.getByRole('form'))
  expect(await screen.findByText(message)).toBeTruthy()
  expect(screen.getByRole('form').getAttribute('aria-describedby')).toBe('profile-error')
  expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe('我正在编辑的草稿')
  expect(fetchMock).toHaveBeenCalledTimes(before + 1)
  expect(screen.queryByText(/用户名和密码/)).toBeNull()
})

test.each([
  ['503', () => json({ detail: 'career_profile_unavailable' }, 503), /职业档案服务暂时不可用/],
  ['network', () => { throw new TypeError('private network error') }, /无法连接服务/],
  ['HTML', () => new Response('<html>private error</html>', { status: 502 }), /暂时无法读取职业档案/],
  ['unknown 404', () => json({ detail: 'unknown' }, 404), /暂时无法读取职业档案/],
  ['malformed', () => json({ revision: 3 }), /职业档案响应异常/],
])('initial %s failure has safe retry and does not become onboarding', async (_name, response, message) => {
  fetchMock.mockImplementation(async () => response())
  renderPage()
  expect(await screen.findByText(message)).toBeTruthy()
  expect(screen.queryByRole('form')).toBeNull()
  expect(screen.queryByText(/private/)).toBeNull()
  fetchMock.mockImplementation(async () => json(PROFILE))
  await userEvent.setup().click(screen.getByRole('button', { name: '重新加载档案' }))
  await loaded()
})

test('synchronous double submit sends one PUT and disables editing while saving', async () => {
  renderPage(); await loaded(); changeSummary()
  const pending = deferred<Response>()
  fetchMock.mockReturnValueOnce(pending.promise)
  const form = screen.getByRole('form')
  act(() => { fireEvent.submit(form); fireEvent.submit(form) })
  expect(writeCalls('PUT')).toHaveLength(1)
  expect(form.getAttribute('aria-busy')).toBe('true')
  expect(screen.getByRole('button', { name: '正在处理…' }).hasAttribute('disabled')).toBe(true)
  expect(screen.getByLabelText('职业概述').hasAttribute('disabled')).toBe(true)
  await act(async () => { pending.resolve(json({ ...PROFILE, revision: 4 })) })
  await screen.findByText('当前编辑基线：revision 4')
})

test('conflict preserves draft, shows latest profile, blocks writes until explicit baseline choice', async () => {
  const user = userEvent.setup()
  renderPage(); await loaded(); changeSummary()
  const latest = { ...PROFILE, revision: 9, career_summary: '另一个页面确认的内容', skills: ['Rust'], target_roles: ['平台工程师'] }
  fetchMock.mockImplementationOnce(async () => json({ detail: 'career_profile_conflict' }, 409))
    .mockImplementationOnce(async () => json(latest))
  fireEvent.submit(screen.getByRole('form'))
  await expectLatest(9)
  expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe('我正在编辑的草稿')
  expect(within(recoveryPanel()).getByText(latest.career_summary)).toBeTruthy()
  expect(within(recoveryPanel()).getByText('Rust')).toBeTruthy()
  expect(screen.getByRole('button', { name: '保存档案' }).hasAttribute('disabled')).toBe(true)
  fireEvent.submit(screen.getByRole('form'))
  expect(writeCalls('PUT')).toHaveLength(1)
  expect(JSON.parse(String(writeCalls('PUT')[0][1]?.body)).expected_revision).toBe(3)
  expect(screen.getByText(/这不是自动合并.*完整替换职业概述、技能和目标岗位/)).toBeTruthy()
  await user.click(screen.getByRole('button', { name: '保留草稿，以服务器状态继续编辑' }))
  expect(writeCalls('PUT')).toHaveLength(1)
  expect(screen.getByText(/当前编辑基线：revision 9/)).toBeTruthy()
  fetchMock.mockImplementationOnce(async () => json({ ...PROFILE, revision: 10, career_summary: '我正在编辑的草稿' }))
  await user.click(screen.getByRole('button', { name: '保存档案' }))
  await screen.findByText('当前编辑基线：revision 10')
  expect(JSON.parse(String(writeCalls('PUT')[1][1]?.body))).toEqual({ expected_revision: 9, career_summary: '我正在编辑的草稿', skills: PROFILE.skills, target_roles: PROFILE.target_roles })
})

test('server reset is explicit, cancellable, clears entries, and disables unchanged save', async () => {
  const user = userEvent.setup()
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  renderPage(); await loaded(); changeSummary()
  fetchMock.mockImplementationOnce(async () => json({ detail: 'career_profile_conflict' }, 409))
    .mockImplementationOnce(async () => json({ ...PROFILE, revision: 7, career_summary: '最新职业方向' }))
  fireEvent.submit(screen.getByRole('form')); await expectLatest(7)
  fireEvent.change(screen.getByLabelText('添加技能'), { target: { value: '未添加内容' } })
  const reset = screen.getByRole('button', { name: '使用服务器状态重新编辑' })
  await user.click(reset)
  expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe('我正在编辑的草稿')
  confirm.mockReturnValue(true); await user.click(reset)
  expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe('最新职业方向')
  expect((screen.getByLabelText('添加技能') as HTMLInputElement).value).toBe('')
  expect(screen.getByRole('button', { name: '保存档案' }).hasAttribute('disabled')).toBe(true)
  expect(writeCalls('PUT')).toHaveLength(1)
})

test.each(['conflict', 'uncertain'])('server reset clears both list editors local errors after %s recovery', async (reason) => {
  const user = userEvent.setup()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  renderPage(); await loaded(); changeSummary()
  if (reason === 'conflict') fetchMock.mockImplementationOnce(async () => json({ detail: 'career_profile_conflict' }, 409))
  else fetchMock.mockRejectedValueOnce(new TypeError('lost write response'))
  fetchMock.mockImplementationOnce(async () => json({ ...PROFILE, revision: 7 }))
  fireEvent.submit(screen.getByRole('form')); await expectLatest(7)
  for (const [label, limit] of [['技能', 80], ['目标岗位', 120]] as const) {
    const input = screen.getByLabelText(`添加${label}`)
    fireEvent.change(input, { target: { value: '字'.repeat(limit + 1) } })
    await user.click(screen.getByRole('button', { name: `添加${label}` }))
    expect(screen.getByText(`请输入单行${label}，每项 1–${limit} 个 Unicode 字符，不能包含控制字符。`)).toBeTruthy()
    expect(input.getAttribute('aria-invalid')).toBe('true')
  }
  await user.click(screen.getByRole('button', { name: '使用服务器状态重新编辑' }))
  for (const label of ['技能', '目标岗位']) {
    const input = screen.getByLabelText(`添加${label}`)
    expect((input as HTMLInputElement).value).toBe('')
    expect(input.getAttribute('aria-invalid')).toBe('false')
    expect(input.getAttribute('aria-describedby')).not.toContain('-error')
  }
  expect(screen.queryByText(/请输入单行/)).toBeNull()
  expect(writeCalls('PUT')).toHaveLength(1)
})

test('POST already-exists refreshes latest but never silently replaces the draft or repeats POST', async () => {
  fetchMock.mockImplementation(async () => json({ detail: 'career_profile_not_found' }, 404))
  renderPage(); await screen.findByRole('button', { name: '创建档案' }); changeSummary()
  fetchMock.mockImplementationOnce(async () => json({ detail: 'career_profile_already_exists' }, 409))
    .mockImplementationOnce(async () => json(PROFILE))
  fireEvent.submit(screen.getByRole('form')); await expectLatest(3)
  expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe('我正在编辑的草稿')
  expect(writeCalls('POST')).toHaveLength(1)
  expect(writeCalls('PUT')).toHaveLength(0)
  await userEvent.setup().click(screen.getByRole('button', { name: '保留草稿，以服务器状态继续编辑' }))
  expect(screen.getByRole('button', { name: '保存档案' })).toBeTruthy()
  expect(writeCalls('POST')).toHaveLength(1)
})

test.each([
  ['network', () => Promise.reject(new TypeError('connection lost'))],
  ['503', async () => json({ detail: 'career_profile_unavailable' }, 503)],
  ['malformed success', async () => json({ saved: true })],
  ['proxy failure', async () => new Response('<html>private gateway data</html>', { status: 502 })],
])('uncertain PUT %s preserves draft, GETs latest, makes no success claim or automatic second PUT', async (_name, response) => {
  renderPage(); await loaded(); changeSummary()
  const before = fetchMock.mock.calls.length
  fetchMock.mockImplementationOnce(response).mockImplementationOnce(async () => json({ ...PROFILE, revision: 8, career_summary: '其他请求的内容' }))
  fireEvent.submit(screen.getByRole('form')); await expectLatest(8)
  expect(document.getElementById('profile-error')).toBeNull()
  expect(screen.getByRole('form').getAttribute('aria-describedby')).toBeNull()
  expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe('我正在编辑的草稿')
  expect(screen.getByText(/写入结果不确定.*不能证明刚才保存成功/)).toBeTruthy()
  expect(screen.queryByText('职业档案已保存，当前显示服务器确认的数据。')).toBeNull()
  expect(fetchMock.mock.calls.slice(before).map(([, init]) => init?.method)).toEqual(['PUT', 'GET'])
  expect(writeCalls('PUT')).toHaveLength(1)
  fireEvent.submit(screen.getByRole('form'))
  expect(writeCalls('PUT')).toHaveLength(1)
})

test('uncertain PUT timeout reconciles with GET while retaining draft and never repeats PUT', async () => {
  renderPage(); await loaded(); changeSummary()
  vi.useFakeTimers()
  fetchMock.mockImplementationOnce((_path, init) => new Promise((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
  })).mockImplementationOnce(async () => json({ ...PROFILE, revision: 12, career_summary: '服务器状态' }))
  fireEvent.submit(screen.getByRole('form'))
  await act(async () => { await vi.advanceTimersByTimeAsync(PROFILE_TIMEOUT_MS) })
  expect(screen.getByRole('heading', { name: '最新服务器状态 · revision 12' })).toBeTruthy()
  expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe('我正在编辑的草稿')
  expect(screen.getByText(/写入结果不确定/)).toBeTruthy()
  expect(writeCalls('PUT')).toHaveLength(1)
  expect(fetchMock.mock.calls.at(-1)?.[1]?.method).toBe('GET')
})

test('uncertain POST discovers an existing profile, retains draft, and waits for human choice', async () => {
  fetchMock.mockImplementation(async () => json({ detail: 'career_profile_not_found' }, 404))
  renderPage(); await screen.findByRole('button', { name: '创建档案' }); changeSummary()
  fetchMock.mockRejectedValueOnce(new TypeError('lost create response')).mockImplementationOnce(async () => json({ ...PROFILE, revision: 1 }))
  fireEvent.submit(screen.getByRole('form')); await expectLatest(1)
  expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe('我正在编辑的草稿')
  expect(writeCalls('POST')).toHaveLength(1)
  expect(screen.getByRole('button', { name: '创建档案' }).hasAttribute('disabled')).toBe(true)
  expect(screen.queryByText('职业档案已保存，当前显示服务器确认的数据。')).toBeNull()
  await userEvent.setup().click(screen.getByRole('button', { name: '保留草稿，以服务器状态继续编辑' }))
  expect(screen.getByRole('button', { name: '保存档案' })).toBeTruthy()
  expect(writeCalls('POST')).toHaveLength(1)
  expect(writeCalls('PUT')).toHaveLength(0)
})

test('uncertain POST with GET 404 requires explicit decision before another create', async () => {
  fetchMock.mockImplementation(async () => json({ detail: 'career_profile_not_found' }, 404))
  renderPage(); await screen.findByRole('button', { name: '创建档案' }); changeSummary()
  fetchMock.mockRejectedValueOnce(new TypeError('lost response'))
  fireEvent.submit(screen.getByRole('form'))
  await screen.findByText('当前服务器尚无职业档案。这不保证先前请求不会稍后完成。')
  expect(writeCalls('POST')).toHaveLength(1)
  await userEvent.setup().click(screen.getByRole('button', { name: '保留草稿，以服务器状态继续编辑' }))
  expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe('我正在编辑的草稿')
  expect(screen.getByRole('button', { name: '创建档案' }).hasAttribute('disabled')).toBe(false)
  expect(writeCalls('POST')).toHaveLength(1)
})

test('failed conflict reconciliation keeps draft and blocks save; retry is GET-only', async () => {
  renderPage(); await loaded(); changeSummary()
  fetchMock.mockImplementationOnce(async () => json({ detail: 'career_profile_conflict' }, 409))
    .mockRejectedValueOnce(new TypeError('offline'))
  fireEvent.submit(screen.getByRole('form'))
  const retry = await screen.findByRole('button', { name: '重新读取服务器档案' })
  expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe('我正在编辑的草稿')
  expect(screen.getByRole('button', { name: '保存档案' }).hasAttribute('disabled')).toBe(true)
  fetchMock.mockImplementationOnce(async () => json({ ...PROFILE, revision: 6 }))
  await userEvent.setup().click(retry); await expectLatest(6)
  expect(writeCalls('PUT')).toHaveLength(1)
})

test('uncertain write with failed reconciliation remains blocked and retry never resends write', async () => {
  renderPage(); await loaded(); changeSummary()
  fetchMock.mockRejectedValueOnce(new TypeError('lost write response'))
    .mockImplementationOnce(async () => json({ detail: 'career_profile_unavailable' }, 503))
  fireEvent.submit(screen.getByRole('form'))
  const retry = await screen.findByRole('button', { name: '重新读取服务器档案' })
  expect(within(recoveryPanel()).getByRole('alert').textContent).toContain('职业档案服务暂时不可用')
  expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe('我正在编辑的草稿')
  expect(screen.getByRole('button', { name: '保存档案' }).hasAttribute('disabled')).toBe(true)
  const before = fetchMock.mock.calls.length
  fetchMock.mockImplementationOnce(async () => json({ ...PROFILE, revision: 4 }))
  await userEvent.setup().click(retry); await expectLatest(4)
  expect(document.getElementById('profile-error')).toBeNull()
  expect(within(recoveryPanel()).queryByRole('alert')).toBeNull()
  expect(screen.getByText(/写入结果不确定.*不能证明刚才保存成功/)).toBeTruthy()
  expect(fetchMock.mock.calls.slice(before).map(([, init]) => init?.method)).toEqual(['GET'])
  expect(writeCalls('PUT')).toHaveLength(1)
  expect(screen.queryByText('职业档案已保存，当前显示服务器确认的数据。')).toBeNull()
})

test('PUT not-found preserves draft and never silently converts update into create', async () => {
  renderPage(); await loaded(); changeSummary()
  fetchMock.mockImplementation(async () => json({ detail: 'career_profile_not_found' }, 404))
  fireEvent.submit(screen.getByRole('form'))
  await screen.findByText('当前服务器尚无职业档案。这不保证先前请求不会稍后完成。')
  expect(writeCalls('PUT')).toHaveLength(1)
  expect(writeCalls('POST')).toHaveLength(0)
})

test('StrictMode aborts stale GET, unmount aborts current GET, and late response cannot replace data', async () => {
  const first = deferred<Response>()
  const second = deferred<Response>()
  fetchMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
  const view = renderPage()
  expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true)
  await act(async () => { second.resolve(json(PROFILE)) }); await loaded()
  await act(async () => { first.resolve(json({ ...PROFILE, revision: 1, career_summary: '过期响应' })) })
  expect((screen.getByLabelText('职业概述') as HTMLTextAreaElement).value).toBe(PROFILE.career_summary)
  view.unmount()
  const pending = deferred<Response>()
  fetchMock.mockReturnValue(pending.promise)
  const next = renderPage()
  const signal = fetchMock.mock.calls.at(-1)?.[1]?.signal
  next.unmount(); expect(signal?.aborted).toBe(true)
  await act(async () => { pending.resolve(json(PROFILE)) })
  expect(screen.queryByRole('form')).toBeNull()
})

test('unmount during save aborts request and a late successful write triggers no reconciliation', async () => {
  const view = renderPage(); await loaded(); changeSummary()
  const pending = deferred<Response>()
  fetchMock.mockReturnValueOnce(pending.promise)
  fireEvent.submit(screen.getByRole('form'))
  const before = fetchMock.mock.calls.length
  const signal = writeCalls('PUT')[0][1]?.signal
  view.unmount(); expect(signal?.aborted).toBe(true)
  await act(async () => { pending.resolve(json({ ...PROFILE, revision: 4 })) })
  expect(fetchMock).toHaveBeenCalledTimes(before)
  expect(screen.queryByText(/响应异常|请求超时|无法连接/)).toBeNull()
})

test('unmount during reconciliation aborts GET and late result produces no follow-up write', async () => {
  const view = renderPage(); await loaded(); changeSummary()
  const pending = deferred<Response>()
  fetchMock.mockImplementationOnce(async () => json({ detail: 'career_profile_conflict' }, 409)).mockReturnValueOnce(pending.promise)
  fireEvent.submit(screen.getByRole('form'))
  await screen.findByText('正在读取最新服务器档案…')
  const signal = fetchMock.mock.calls.at(-1)?.[1]?.signal
  const before = fetchMock.mock.calls.length
  view.unmount(); expect(signal?.aborted).toBe(true)
  await act(async () => { pending.resolve(json({ ...PROFILE, revision: 8 })) })
  expect(fetchMock).toHaveBeenCalledTimes(before)
  expect(writeCalls('PUT')).toHaveLength(1)
})

test('dirty navigation can be cancelled, and refresh protection is removed after acknowledged save', async () => {
  const user = userEvent.setup()
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  renderPage(); await loaded(); changeSummary()
  const before = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(before); expect(before.defaultPrevented).toBe(true)
  await user.click(screen.getByRole('link', { name: '返回工作台' }))
  expect(screen.getByRole('form')).toBeTruthy()
  await user.click(screen.getByRole('link', { name: 'Career Copilot 首页' }))
  expect(screen.getByRole('form')).toBeTruthy()
  await user.click(screen.getByRole('button', { name: '退出登录' }))
  expect(logout).not.toHaveBeenCalled()
  fetchMock.mockImplementationOnce(async () => json({ ...PROFILE, revision: 4, career_summary: '我正在编辑的草稿' }))
  fireEvent.submit(screen.getByRole('form')); await screen.findByText('当前编辑基线：revision 4')
  const after = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(after); expect(after.defaultPrevented).toBe(false)
  await user.click(screen.getByRole('link', { name: '返回工作台' }))
  expect(screen.getByRole('heading', { name: '工作台' })).toBeTruthy()
  expect(confirm).toHaveBeenCalledTimes(3)
})
