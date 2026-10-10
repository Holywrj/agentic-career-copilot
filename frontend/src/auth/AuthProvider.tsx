import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import * as authApi from '../api/auth'
import { ApiError, request } from '../api/client'
import type { RequestOptions } from '../api/client'
import { AuthContext } from './AuthContext'
import type { AuthNotice, AuthState, Session } from './AuthContext'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'anonymous', session: null })
  const [loginPending, setLoginPending] = useState(false)
  const [notice, setNotice] = useState<AuthNotice>(null)
  const session = useRef<Session | null>(null)
  const generation = useRef(0)
  const mounted = useRef(true)
  const loginController = useRef<AbortController | null>(null)
  const privateControllers = useRef(new Set<AbortController>())
  const logoutControllers = useRef(new Set<AbortController>())

  const endSession = useCallback((reason: AuthNotice = null) => {
    generation.current += 1
    loginController.current?.abort()
    loginController.current = null
    privateControllers.current.forEach((controller) => controller.abort())
    privateControllers.current.clear()
    session.current = null
    setState({ status: 'anonymous', session: null })
    setLoginPending(false)
    setNotice(reason)
  }, [])

  useEffect(() => {
    mounted.current = true
    const requests = privateControllers.current
    const logouts = logoutControllers.current
    return () => {
      mounted.current = false
      generation.current += 1
      loginController.current?.abort()
      loginController.current = null
      requests.forEach((controller) => controller.abort())
      logouts.forEach((controller) => controller.abort())
      session.current = null
    }
  }, [])

  useEffect(() => {
    if (!state.session) return
    const active = state.session
    const checkDeadline = () => {
      if (session.current === active && Date.now() >= active.expiresAt) endSession('session-ended')
    }
    const timer = setTimeout(checkDeadline, Math.max(0, active.expiresAt - Date.now()))
    const onVisible = () => { if (document.visibilityState === 'visible') checkDeadline() }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('pageshow', checkDeadline)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('pageshow', checkDeadline)
    }
  }, [state, endSession])

  const login = useCallback(async (credentials: authApi.Credentials) => {
    if (loginController.current) throw new ApiError('cancelled')
    endSession()
    const operation = generation.current
    const controller = new AbortController()
    loginController.current = controller
    setLoginPending(true)
    const assertCurrent = () => {
      if (!mounted.current || generation.current !== operation || controller.signal.aborted) {
        throw new ApiError('cancelled')
      }
    }
    try {
      const token = await authApi.login(credentials, controller.signal)
      assertCurrent()
      // A UX estimate only; authorization always belongs to the backend.
      const expiresAt = Date.now() + token.expires_in * 1000
      const user = await authApi.me(token.access_token, controller.signal)
      assertCurrent()
      if (Date.now() >= expiresAt) throw new ApiError('authentication_required', 401, 'authentication_required')
      const established = { accessToken: token.access_token, user, expiresAt }
      session.current = established
      setState({ status: 'authenticated', session: established })
    } finally {
      if (generation.current === operation && mounted.current) {
        loginController.current = null
        setLoginPending(false)
      }
    }
  }, [endSession])

  const cancelLogin = useCallback(() => {
    if (loginController.current) endSession()
  }, [endSession])

  const logout = useCallback(async () => {
    const previous = session.current
    endSession('signed-out')
    const operation = generation.current
    if (!previous) return
    const controller = new AbortController()
    logoutControllers.current.add(controller)
    try {
      await authApi.logout(previous.accessToken, controller.signal)
    } catch (error) {
      const alreadyInvalid = error instanceof ApiError && error.status === 401 && error.code === 'authentication_required'
      if (!alreadyInvalid && mounted.current && generation.current === operation) setNotice('logout-unconfirmed')
    } finally {
      logoutControllers.current.delete(controller)
    }
  }, [endSession])

  const requestAuthenticated = useCallback(async <T,>(path: string, options: Omit<RequestOptions, 'token'> = {}): Promise<T> => {
    const active = session.current
    const operation = generation.current
    if (!active || Date.now() >= active.expiresAt) {
      if (active) endSession('session-ended')
      throw new ApiError('authentication_required', 401, 'authentication_required')
    }
    const controller = new AbortController()
    const cancel = () => controller.abort()
    if (options.signal?.aborted) controller.abort()
    options.signal?.addEventListener('abort', cancel, { once: true })
    privateControllers.current.add(controller)
    try {
      const data = await request<T>(path, { ...options, token: active.accessToken, signal: controller.signal })
      if (!mounted.current || generation.current !== operation) throw new ApiError('cancelled')
      return data
    } catch (error) {
      if (error instanceof ApiError && error.status === 401 && error.code === 'authentication_required'
        && mounted.current && generation.current === operation) endSession('session-ended')
      throw error
    } finally {
      privateControllers.current.delete(controller)
      options.signal?.removeEventListener('abort', cancel)
    }
  }, [endSession])

  const value = useMemo(() => ({
    status: state.status,
    currentUser: state.session?.user ?? null,
    loginPending, notice, login, cancelLogin, logout, requestAuthenticated,
  }), [state, loginPending, notice, login, cancelLogin, logout, requestAuthenticated])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
