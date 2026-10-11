import { StrictMode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, expect, test, vi } from 'vitest'
import { AuthContext } from '../auth/AuthContext'
import type { AuthContextValue } from '../auth/AuthContext'
import { request } from '../api/client'
import type { AuthenticatedRequest } from '../api/client'
import { MAX_RESUME_BYTES, RESUME_UPLOAD_TIMEOUT_MS } from '../api/resumes'
import { deferred, json, RESUME, resumeList, USER_A } from '../test/fixtures'
import { ResumesPage } from './ResumesPage'

let fetchMock = vi.fn<typeof fetch>()
const authenticated: AuthenticatedRequest = (path, options) => request(path, { ...options, token: 'test-token' })
beforeEach(() => {
  fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => json(resumeList()))
  vi.stubGlobal('fetch', fetchMock)
})

function renderPage(requestAuthenticated = authenticated) {
  const auth: AuthContextValue = {
    status: 'authenticated', currentUser: USER_A as AuthContextValue['currentUser'],
    loginPending: false, notice: null, login: vi.fn(), cancelLogin: vi.fn(), logout: vi.fn(), requestAuthenticated,
  }
  return render(<StrictMode><MemoryRouter initialEntries={['/app/resumes']}><AuthContext.Provider value={auth}>
    <Routes><Route path="/app/resumes" element={<ResumesPage />} /><Route path="/app" element={<h1>工作台</h1>} /></Routes>
  </AuthContext.Provider></MemoryRouter></StrictMode>)
}
async function loaded() { await waitFor(() => expect(screen.getByRole('button', { name: '刷新列表' }).hasAttribute('disabled')).toBe(false)) }
async function selectFile(file = new File(['%PDF-1.7'], 'local.v2.PDF')) {
  await userEvent.setup({ applyAccept: false }).upload(screen.getByLabelText('选择 PDF'), file)
  return file
}
function calls(method: string) { return fetchMock.mock.calls.filter(([, init]) => init?.method === method) }
function submit() { fireEvent.submit(screen.getByRole('form', { name: '上传简历' })) }
const confirmed = { ...RESUME, id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd', original_filename: 'canonical.pdf' }

test('StrictMode loading/stale GET cannot overwrite the current list; latest read is aborted on unmount', async () => {
  const old = deferred<Response>(), latest = deferred<Response>()
  fetchMock.mockReturnValueOnce(old.promise).mockReturnValueOnce(latest.promise)
  const view = renderPage()
  expect(screen.getByText('正在加载简历列表…')).toBeTruthy()
  expect(screen.getByRole('button', { name: '上传简历' }).hasAttribute('disabled')).toBe(true)
  expect(calls('GET')[0][1]?.signal?.aborted).toBe(true)
  await act(async () => { latest.resolve(json(resumeList())) }); await loaded()
  await act(async () => { old.resolve(json(resumeList([confirmed]))) })
  expect(screen.queryByRole('heading', { name: 'canonical.pdf' })).toBeNull()
  const pending = deferred<Response>()
  fetchMock.mockReturnValueOnce(pending.promise)
  fireEvent.click(screen.getByRole('button', { name: '刷新列表' }))
  const signal = calls('GET').at(-1)?.[1]?.signal
  view.unmount()
  expect(signal?.aborted).toBe(true)
  await act(async () => { pending.resolve(json(resumeList())) })
})

test('empty state, load failure, GET-only retry and legacy rendering', async () => {
  fetchMock.mockImplementation(async () => json({ detail: 'resume_unavailable' }, 503))
  renderPage()
  await screen.findByText('简历服务暂时不可用，请稍后重试。')
  fetchMock.mockResolvedValueOnce(json(resumeList([])))
  await userEvent.setup().click(screen.getByRole('button', { name: '重新读取列表' }))
  await screen.findByText(/还没有简历/)
  fetchMock.mockResolvedValueOnce(json(resumeList([{ ...RESUME, size_bytes: null, media_type: null, status: 'private parser code' }])))
  await userEvent.setup().click(screen.getByRole('button', { name: '刷新列表' }))
  await screen.findByText('状态暂不可识别')
  expect(screen.getByText('—')).toBeTruthy()
  expect(screen.getByText('未记录')).toBeTruthy()
  expect(screen.queryByText('private parser code')).toBeNull()
  expect(calls('POST')).toHaveLength(0)
})

test('page identity guards stale success even when the authenticated requester ignores abort', async () => {
  const older = deferred<unknown>(), newer = deferred<unknown>()
  let count = 0
  const requester: AuthenticatedRequest = async <T,>() => await (++count === 1 ? older.promise : newer.promise) as T
  renderPage(requester)
  await act(async () => { newer.resolve(resumeList()) }); await loaded()
  await act(async () => { older.resolve(resumeList([confirmed])) })
  expect(screen.getByRole('heading', { name: 'resume.pdf' })).toBeTruthy()
  expect(screen.queryByRole('heading', { name: 'canonical.pdf' })).toBeNull()
})

test.each([
  [new File(['x'], 'x.docx'), '只支持 PDF 文件。'],
  [new File([], 'x.pdf'), '文件为空，请选择有效的 PDF。'],
  [new File([new Uint8Array(MAX_RESUME_BYTES + 1)], 'x.pdf'), '文件不能超过 5 MiB。'],
])('preflight prevents invalid upload %#', async (file, copy) => {
  renderPage(); await loaded(); await selectFile(file)
  expect(screen.getByRole('alert').textContent).toBe(copy)
  expect(screen.getByRole('button', { name: '上传简历' }).hasAttribute('disabled')).toBe(true)
  submit(); expect(calls('POST')).toHaveLength(0)
})

test('confirmed upload merges immediately, resets input, prevents duplicate submit and survives refresh failure', async () => {
  renderPage(); await loaded(); await selectFile()
  expect(screen.getByText('local.v2.PDF')).toBeTruthy()
  const post = deferred<Response>(), refresh = deferred<Response>()
  fetchMock.mockReturnValueOnce(post.promise).mockReturnValueOnce(refresh.promise)
  act(() => { submit(); submit() })
  expect(calls('POST')).toHaveLength(1)
  expect(screen.getByRole('button', { name: '正在上传…' }).hasAttribute('disabled')).toBe(true)
  await act(async () => { post.resolve(json(confirmed, 201)) })
  expect(screen.getByRole('heading', { name: 'canonical.pdf' })).toBeTruthy()
  expect(screen.getByText('简历已上传：canonical.pdf。')).toBeTruthy()
  expect((screen.getByLabelText('选择 PDF') as HTMLInputElement).value).toBe('')
  await act(async () => { refresh.resolve(json({ detail: 'resume_unavailable' }, 503)) })
  expect(screen.getByRole('alert').textContent).toContain('上传已确认，但列表刷新失败')
  expect(screen.getByRole('heading', { name: 'canonical.pdf' })).toBeTruthy()
  expect(screen.queryByText('操作结果待确认')).toBeNull()
  expect(calls('POST')).toHaveLength(1)
  fetchMock.mockResolvedValueOnce(json(resumeList([confirmed, RESUME])))
  await userEvent.setup().click(screen.getByRole('button', { name: '重新读取列表' }))
  await loaded()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByText('简历已上传：canonical.pdf。')).toBeTruthy()
})

test.each([[413, 'request_too_large', '文件不能超过 5 MiB。'], [415, 'unsupported_resume_type', '只支持 PDF 文件。'], [422, 'invalid_request', '无法上传这份文件，请确认选择的是有效的 PDF。']])('known upload %s rejection retains file and does not reconcile', async (status, detail, copy) => {
  renderPage(); await loaded(); await selectFile()
  const before = calls('GET').length
  fetchMock.mockResolvedValueOnce(json({ detail }, status))
  submit()
  await screen.findByText(copy)
  expect(screen.getByText('local.v2.PDF')).toBeTruthy()
  expect(calls('GET')).toHaveLength(before)
  expect(calls('POST')).toHaveLength(1)
})

test.each(['network', 'malformed', 'storage-503', 'db-503', 'unknown-502'])('uncertain upload %s reconciles current state without inferring success or retrying POST', async (failure) => {
  renderPage(); await loaded(); await selectFile()
  if (failure === 'network') fetchMock.mockRejectedValueOnce(new TypeError('/private/path SQL'))
  else if (failure === 'malformed') fetchMock.mockResolvedValueOnce(json({ saved: true }, 201))
  else fetchMock.mockResolvedValueOnce(failure === 'unknown-502' ? new Response('private proxy detail', { status: 502 }) : json({ detail: failure === 'storage-503' ? 'resume_storage_unavailable' : 'resume_unavailable' }, 503))
  // Same name/size is deliberately not a POST receipt.
  fetchMock.mockResolvedValueOnce(json(resumeList([{ ...RESUME, original_filename: 'local.v2.PDF', size_bytes: 8 }])))
  submit()
  await screen.findByText(/已刷新服务器当前列表；这不是/)
  expect(screen.getByRole('heading', { name: '操作结果待确认' })).toBeTruthy()
  expect(screen.queryByText(/简历已上传：/)).toBeNull()
  expect(screen.getByRole('button', { name: '再次上传此文件' }).hasAttribute('disabled')).toBe(false)
  expect(calls('POST')).toHaveLength(1)
  expect(document.body.textContent).not.toMatch(/private|SQL|用户名|密码/)
})

test('failed upload reconciliation blocks writes, GET-only retry restores state, explicit re-upload needs confirmation', async () => {
  renderPage(); await loaded(); await selectFile()
  fetchMock.mockRejectedValueOnce(new TypeError('offline')).mockRejectedValueOnce(new TypeError('offline'))
  submit()
  await screen.findByText(/暂时无法刷新服务器当前列表，请先/)
  expect(screen.getByRole('button', { name: '再次上传此文件' }).hasAttribute('disabled')).toBe(true)
  expect(screen.getByRole('button', { name: '删除简历 resume.pdf' }).hasAttribute('disabled')).toBe(true)
  submit(); expect(calls('POST')).toHaveLength(1)
  fetchMock.mockResolvedValueOnce(json(resumeList()))
  await userEvent.setup().click(screen.getByRole('button', { name: '重新读取列表' }))
  await screen.findByText(/已刷新服务器当前列表；这不是/)
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  submit(); expect(calls('POST')).toHaveLength(1)
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('可能产生重复简历'))
  confirm.mockReturnValue(true)
  fetchMock.mockResolvedValueOnce(json(confirmed, 201)).mockResolvedValueOnce(json(resumeList([confirmed, RESUME])))
  submit()
  await screen.findByText('简历已上传：canonical.pdf。')
  expect(calls('POST')).toHaveLength(2)
})

test('upload timeout reconciles with GET only', async () => {
  renderPage(); await loaded(); await selectFile()
  vi.useFakeTimers()
  fetchMock.mockImplementationOnce((_path, init) => new Promise((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
  })).mockResolvedValueOnce(json(resumeList()))
  submit()
  await act(async () => { await vi.advanceTimersByTimeAsync(RESUME_UPLOAD_TIMEOUT_MS) })
  expect(screen.getByText(/已刷新服务器当前列表；这不是/)).toBeTruthy()
  expect(calls('POST')).toHaveLength(1)
})

test('delete confirmation cancel sends nothing; 204 removes immediately and refresh failure preserves success', async () => {
  renderPage(); await loaded()
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  const button = screen.getByRole('button', { name: '删除简历 resume.pdf' })
  fireEvent.click(button); expect(calls('DELETE')).toHaveLength(0)
  confirm.mockReturnValue(true)
  const deletion = deferred<Response>(), refresh = deferred<Response>()
  fetchMock.mockReturnValueOnce(deletion.promise).mockReturnValueOnce(refresh.promise)
  act(() => { fireEvent.click(button); fireEvent.click(button) })
  expect(calls('DELETE')).toHaveLength(1)
  await act(async () => { deletion.resolve(new Response(null, { status: 204 })) })
  expect(screen.queryByRole('heading', { name: 'resume.pdf' })).toBeNull()
  expect(screen.getByText('简历已删除：resume.pdf。')).toBeTruthy()
  await act(async () => { refresh.reject(new TypeError('offline')) })
  expect(screen.getByRole('alert').textContent).toContain('删除已确认，但列表刷新失败')
  expect(screen.queryByRole('heading', { name: 'resume.pdf' })).toBeNull()
})

test('409 in-use keeps row, uses safe copy and does not refresh', async () => {
  renderPage(); await loaded(); vi.spyOn(window, 'confirm').mockReturnValue(true)
  const reads = calls('GET').length
  fetchMock.mockResolvedValueOnce(json({ detail: 'resume_in_use' }, 409))
  fireEvent.click(screen.getByRole('button', { name: '删除简历 resume.pdf' }))
  await screen.findByText('这份简历正在被其他记录使用，暂时不能删除。')
  expect(screen.getByRole('heading', { name: 'resume.pdf' })).toBeTruthy()
  expect(calls('GET')).toHaveLength(reads)
})

test.each(['404', 'network', 'invalid', 'storage-503', 'db-503', 'timeout'])('delete %s refreshes server state with no blind retry', async (failure) => {
  renderPage(); await loaded(); vi.spyOn(window, 'confirm').mockReturnValue(true)
  if (failure === 'network') fetchMock.mockRejectedValueOnce(new TypeError('offline'))
  else if (failure === 'invalid') fetchMock.mockResolvedValueOnce(json({ deleted: true }))
  else if (failure === 'timeout') {
    vi.useFakeTimers()
    fetchMock.mockImplementationOnce((_path, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    }))
  } else fetchMock.mockResolvedValueOnce(json({ detail: failure === '404' ? 'resume_not_found' : failure === 'storage-503' ? 'resume_storage_unavailable' : 'resume_unavailable' }, failure === '404' ? 404 : 503))
  fetchMock.mockResolvedValueOnce(json(resumeList([])))
  fireEvent.click(screen.getByRole('button', { name: '删除简历 resume.pdf' }))
  if (failure === 'timeout') await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })
  else await screen.findByText(/已刷新服务器当前列表；这不是/)
  expect(screen.queryByRole('heading', { name: 'resume.pdf' })).toBeNull()
  expect(screen.queryByText(/简历已删除：/)).toBeNull()
  expect(calls('DELETE')).toHaveLength(1)
})

test.each(['upload', 'delete', 'recovery'])('unmount during %s aborts operation and late results cause no updates/follow-ups', async (stage) => {
  const view = renderPage(); await loaded()
  const pending = deferred<Response>()
  if (stage === 'delete') {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    fetchMock.mockReturnValueOnce(pending.promise)
    fireEvent.click(screen.getByRole('button', { name: '删除简历 resume.pdf' }))
  } else {
    await selectFile()
    if (stage === 'recovery') fetchMock.mockRejectedValueOnce(new TypeError('offline'))
    fetchMock.mockReturnValueOnce(pending.promise)
    submit()
    if (stage === 'recovery') await screen.findByText(/正在刷新服务器当前列表/)
  }
  const before = fetchMock.mock.calls.length, signal = fetchMock.mock.calls.at(-1)?.[1]?.signal
  view.unmount(); expect(signal?.aborted).toBe(true)
  await act(async () => { pending.resolve(stage === 'upload' ? json(confirmed, 201) : stage === 'delete' ? new Response(null, { status: 204 }) : json(resumeList())) })
  expect(fetchMock).toHaveBeenCalledTimes(before)
})
