import { useLayoutEffect, useRef } from 'react'

export function usePreviewPosition(enabled = true) {
  const ref = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const stack = ref.current, grid = stack?.parentElement
    if (!enabled || !stack || !grid) return
    const update = () => grid.style.setProperty('--translate-preview-top', `${stack.getBoundingClientRect().top + window.scrollY}px`)
    const observer = new ResizeObserver(update)
    const main = stack.closest('main')
    if (main) observer.observe(main)
    window.addEventListener('resize', update); update()
    return () => { observer.disconnect(); window.removeEventListener('resize', update) }
  }, [enabled])
  return ref
}
