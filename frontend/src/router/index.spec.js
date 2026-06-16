import { describe, expect, it } from 'vitest'

import { router } from './index'

describe('router', () => {
  it('does not expose removed history and settings pages', () => {
    const paths = router.getRoutes().map((route) => route.path)

    expect(paths).toEqual(expect.arrayContaining(['/', '/profile', '/report']))
    expect(paths).not.toContain('/history')
    expect(paths).not.toContain('/settings')
  })
})
