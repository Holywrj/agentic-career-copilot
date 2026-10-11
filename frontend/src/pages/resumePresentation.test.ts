import { expect, test, vi } from 'vitest'
import { MAX_RESUME_BYTES } from '../api/resumes'
import { RESUME } from '../test/fixtures'
import { filePreflight, formatResumeDate, formatResumeSize, mergeConfirmedResume, resumeStatus, uploadFileIdentity } from './resumePresentation'

test('upload identity matches separate File objects by name, size and lastModified', () => {
  const original = new File(['%PDF-1.7'], 'A.pdf', { lastModified: 1000 })
  const reselected = new File(['%PDF-1.7'], 'A.pdf', { lastModified: 1000, type: 'application/pdf' })
  expect(reselected).not.toBe(original)
  expect(uploadFileIdentity(reselected)).toBe(uploadFileIdentity(original))
  for (const different of [
    new File(['%PDF-1.7'], 'B.pdf', { lastModified: 1000 }),
    new File(['%PDF-1.7 extra'], 'A.pdf', { lastModified: 1000 }),
    new File(['%PDF-1.7'], 'A.pdf', { lastModified: 2000 }),
  ]) expect(uploadFileIdentity(different)).not.toBe(uploadFileIdentity(original))
})

test.each([[null, '—'], [0, '0 B'], [1023, '1023 B'], [1024, '1 KB'], [182734, '178 KB'], [MAX_RESUME_BYTES, '5 MB']])('formats %s deterministically', (bytes, expected) => {
  expect(formatResumeSize(bytes as number | null)).toBe(expected)
})

test('PDF preflight handles size bounds and uppercase dots without trusting File.type', () => {
  expect(filePreflight(new File(['x'], 'My.Resume.PDF', { type: 'text/plain' }))).toBeNull()
  expect(filePreflight(new File(['x'], 'x.docx'))).toBe('只支持 PDF 文件。')
  expect(filePreflight(new File([], 'x.pdf'))).toContain('文件为空')
  expect(filePreflight(new File([new Uint8Array(MAX_RESUME_BYTES)], 'x.pdf'))).toBeNull()
  expect(filePreflight(new File([new Uint8Array(MAX_RESUME_BYTES + 1)], 'x.pdf'))).toContain('5 MiB')
})

test('formats offset/microsecond dates in browser timezone and hides unknown status', () => {
  const format = vi.fn().mockReturnValue('本地时间')
  vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(function (_locale, options) {
    expect(options).not.toHaveProperty('timeZone')
    return { format } as unknown as Intl.DateTimeFormat
  })
  expect(formatResumeDate('2026-10-11T10:00:00.123456+08:00')).toBe('本地时间')
  expect(format).toHaveBeenCalledWith(Date.UTC(2026, 9, 11, 2, 0, 0, 123))
  expect(resumeStatus('uploaded')).toBe('已上传')
  expect(resumeStatus('private parser status')).toBe('状态暂不可识别')
})

test('merges confirmed resources by id and caps the visible list at 100', () => {
  const existing = Array.from({ length: 100 }, (_, index) => ({ ...RESUME, id: `item-${index}` }))
  const confirmed = { ...existing[50], original_filename: 'confirmed.pdf' }
  const result = mergeConfirmedResume(existing, confirmed)
  expect(result).toHaveLength(100)
  expect(result[0]).toBe(confirmed)
  expect(result.filter((item) => item.id === confirmed.id)).toHaveLength(1)
  expect(mergeConfirmedResume(existing, RESUME)).toHaveLength(100)
})
