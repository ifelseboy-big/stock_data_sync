import { describe, expect, it } from 'vitest'

import { apiErrorMessage } from './http'

describe('apiErrorMessage', () => {
  it('returns a normal API error string', () => {
    expect(apiErrorMessage('结束日期不能早于开始日期', '请求失败')).toBe('结束日期不能早于开始日期')
  })

  it('extracts FastAPI validation messages from detail arrays', () => {
    expect(
      apiErrorMessage(
        [{ msg: 'String should have at least 3 characters' }, { msg: 'Field required' }],
        '请求失败',
      ),
    ).toBe('String should have at least 3 characters；Field required')
  })

  it('falls back when the response has no usable detail', () => {
    expect(apiErrorMessage(undefined, 'Network Error')).toBe('Network Error')
  })
})
