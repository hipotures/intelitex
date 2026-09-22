// @vitest-environment jsdom
import { afterEach,it,expect } from 'vitest'
import { cleanup,render,screen } from '@testing-library/react'
import { Button,Cover,ErrorNote,initials } from './common'
afterEach(cleanup)
it('renders untrusted metadata as text and provides accessible disabled actions',()=>{
 render(<><Cover title={'<img src=x onerror=alert(1)>'} /><Button disabled>Publishing…</Button><ErrorNote error={new Error('<script>unsafe</script>')} /></>)
 expect(document.querySelector('img')).toBeNull();expect(document.querySelector('script')).toBeNull()
 expect(screen.getByRole('button').hasAttribute('disabled')).toBe(true)
 expect(screen.getByRole('alert').textContent).toContain('<script>unsafe</script>')
})
it('uses Unicode initials and ignores a leading English article',()=>{
 expect(initials('The Glass Meridian')).toBe('GM');expect(initials('Żółw i Łódź')).toBe('ŻIŁ');expect(initials('')).toBe('?')
})
