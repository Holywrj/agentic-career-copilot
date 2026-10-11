import { useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'

interface Props {
  id: string
  label: string
  values: string[]
  entry: string
  onEntryChange: (value: string) => void
  onChange: (values: string[]) => void
  maxItems: number
  maxLength: number
  disabled: boolean
}

export function StringListEditor({ id, label, values, entry, onEntryChange, onChange, maxItems, maxLength, disabled }: Props) {
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState('')
  const input = useRef<HTMLInputElement>(null)

  function add() {
    const name = entry.normalize('NFC').trim()
    if (!name || Array.from(name).length > maxLength || /[\p{Cc}\p{Cs}\p{Zl}\p{Zp}]/u.test(entry)) {
      setError(`请输入单行${label}，每项 1–${maxLength} 个 Unicode 字符，不能包含控制字符。`)
      input.current?.focus()
      return
    }
    if (values.length >= maxItems) { setError(`${label}最多添加 ${maxItems} 项。`); return }
    // The backend owns final normalization and Unicode casefold deduplication.
    onChange([...values, name])
    onEntryChange('')
    setError(null)
    setStatus(`已添加${label}：${name}`)
    input.current?.focus()
  }

  function remove(index: number) {
    onChange(values.filter((_, item) => item !== index))
    setError(null)
    setStatus(`已删除${label}：${values[index]}`)
    input.current?.focus()
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); if (!disabled) add() }
  }

  return <section className="profile-list" aria-labelledby={`${id}-title`}>
    <h2 id={`${id}-title`}>{label} <span className="field-help">{values.length} / {maxItems} 项</span></h2>
    <label htmlFor={id}>添加{label}</label>
    <div className="list-entry">
      <input id={id} ref={input} value={entry} disabled={disabled}
        onChange={(event) => { onEntryChange(event.target.value); setError(null) }} onKeyDown={onKeyDown}
        aria-describedby={`${id}-help${error ? ` ${id}-error` : ''}`} aria-invalid={!!error} />
      <button type="button" className="button secondary" disabled={disabled} onClick={add}>添加{label}</button>
    </div>
    <p className="field-help" id={`${id}-help`}>每项 1–{maxLength} 个 Unicode 字符，最多 {maxItems} 项。输入后按 Enter 或点击添加。</p>
    <ul className="profile-tags" aria-label={`已添加的${label}`}>
      {values.map((name, index) => <li key={`${index}-${name}`}><span>{name}</span>
        <button type="button" disabled={disabled} onClick={() => remove(index)} aria-label={`删除${label} ${name}`}>×</button>
      </li>)}
    </ul>
    <div aria-live="assertive" aria-atomic="true">{error && <p className="notice error" id={`${id}-error`}>{error}</p>}</div>
    <span className="sr-only" role="status">{status}</span>
  </section>
}
