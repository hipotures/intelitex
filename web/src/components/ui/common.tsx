import { useEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { cva } from 'class-variance-authority'
import { ArrowLeft, X } from 'lucide-react'
import { Link } from '@tanstack/react-router'
const button = cva('', { variants: { variant: { primary: 'primary-btn', secondary: 'secondary-btn', ghost: 'ghost-btn', danger: 'danger-btn', icon: 'icon-btn' } }, defaultVariants: { variant: 'secondary' } })
export function Button({ variant, className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary'|'secondary'|'ghost'|'danger'|'icon' }) {
  return <button type="button" className={button({ variant, className })} {...props} />
}
export function ErrorNote({ error, retry }: { error: unknown; retry?: () => void }) {
  if (!error) return null
  return <div className="notice error" role="alert"><span>{error instanceof Error ? error.message : 'Unable to load this view.'}</span>{retry && <Button onClick={retry}>Reload current state</Button>}</div>
}
export function Empty({ children }: { children: ReactNode }) { return <div className="empty-note">{children}</div> }
export function Back({ id }: { id?: string }) {
  return <div className="back-row"><Link className="back-link" to={id ? '/work/workspaces/$workspaceId' : '/work'} params={id ? { workspaceId: id } : {}}><ArrowLeft size={14} />{id ? 'Workspace' : 'Library'}</Link></div>
}
export function initials(title: string) {
  const words = title.trim().split(/\s+/)
  if (words.length > 1 && /^(a|an|the)$/i.test(words[0]!)) words.shift()
  return words.map(word => word.match(/[\p{L}\p{N}]/u)?.[0] ?? '').filter(Boolean).slice(0, 3).join('').toUpperCase() || '?'
}
export function Cover({ title, large = false }: { title: string; large?: boolean }) {
  return <div className={large ? 'cover' : 'mini-cover'} title={title}><span className={large ? 'cover-initials' : undefined}>{initials(title)}</span></div>
}
export function Overlay({ title, eyebrow, children, close, drawer = false, compact = false }: { title: string; eyebrow?: string; children: ReactNode; close: () => void; drawer?: boolean; compact?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const trigger = document.activeElement as HTMLElement | null
    const dialog = ref.current!; dialog.showModal()
    const overflow = document.body.style.overflow; document.body.style.overflow = 'hidden'
    return () => { dialog.close(); document.body.style.overflow = overflow; trigger?.focus() }
  }, [])
  return createPortal(<dialog ref={ref} className={drawer ? 'drawer open overlay-dialog' : `modal-card overlay-dialog ${compact ? 'compact-modal' : ''}`} onCancel={e => { e.preventDefault(); close() }} onClick={e => { if (e.target === e.currentTarget && (e.clientX < e.currentTarget.getBoundingClientRect().left || e.clientX > e.currentTarget.getBoundingClientRect().right || e.clientY < e.currentTarget.getBoundingClientRect().top || e.clientY > e.currentTarget.getBoundingClientRect().bottom)) close() }} aria-label={title}>
    <div className={drawer ? 'drawer-head' : 'modal-head'}><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h2>{title}</h2></div><Button variant="icon" onClick={close} aria-label="Close"><X size={18} /></Button></div>{children}
  </dialog>, document.body)
}
export function Panel({ title, children }: { title: string; children: ReactNode }) { return <section className="phase-detail-card"><div className="phase-detail-card-head"><h2>{title}</h2></div><div className="phase-detail-card-body">{children}</div></section> }

export function ProfileSwatch({ index, name }: { index: number | null | undefined; name: string }) { return <span className="profile-swatch" data-palette={index == null ? undefined : index % 18} title={name} aria-label={name} /> }
