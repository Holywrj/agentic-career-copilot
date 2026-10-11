export const USER_A = { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', status: 'active' }
export const USER_B = { id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', status: 'active' }
export const PASSWORD = '  A long private passphrase  '

export const PROFILE = {
  revision: 3,
  career_summary: '后端开发者，探索 AI 应用工程。',
  skills: ['Python', 'FastAPI'],
  target_roles: ['AI 工程师'],
  created_at: '2026-10-10T02:00:00Z',
  updated_at: '2026-10-10T02:15:00+00:00',
}

export const RESUME = {
  id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  original_filename: 'resume.pdf', media_type: 'application/pdf', size_bytes: 182734,
  status: 'uploaded', created_at: '2026-10-11T02:00:00.123456Z', updated_at: '2026-10-11T02:00:00+00:00',
}

export function resumeList(items: unknown[] = [RESUME]) { return { items, limit: 100, offset: 0 } }

export function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
}

export function token(access_token = 'token-a', expires_in = 1800) {
  return { access_token, token_type: 'bearer', expires_in }
}

export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail })
  return { promise, resolve, reject }
}
