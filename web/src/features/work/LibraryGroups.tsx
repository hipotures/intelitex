import type { CSSProperties } from 'react'
import type { LibraryPage, Workspace } from '../../api/schema'
import { Cover } from '../../components/ui/common'
import { debugTag } from '../../debug/regions'

type Source = LibraryPage['sources'][number]
const colors = ['#789bc5', '#ac8abd', '#80ad95', '#c5a375', '#af8698', '#7badaf']
function groupColor(id: string) {
  let value = 0
  for (const character of id) value = (value * 31 + character.charCodeAt(0)) >>> 0
  return colors[value % colors.length]
}

export function LibraryGroups({ sources, workspaces, open }: {
  sources: Source[]; workspaces: Workspace[]; open: (source: Source) => void
}) {
  return sources.map(source => {
    const linked = workspaces.filter(w => w.source_id === source.source_id).length
    const parent = source.groups[0]
    const path = source.groups.map(group => `${group.kind ? `${group.kind}: ` : ''}${group.name}`).join(' › ')
    return <button className="book-card" data-source-id={source.source_id} data-library-group={parent?.group_id}
      style={parent ? { '--library-group-color': groupColor(parent.group_id) } as CSSProperties : undefined}
      {...debugTag('BKC', source.source_id)} key={source.source_id} onClick={() => open(source)} aria-label={`Open ${source.title}`}>
      <Cover title={source.title} large /><div className="book-meta"><div className="book-name" title={source.title}>{source.title}</div>
        <div className="book-detail"><span>{source.creators.join(', ') || 'Unknown author'}</span>{source.language && source.language.toLowerCase() !== 'und' && <span className="library-language">{source.language}</span>}
          {linked > 0 && <span className="workspace-chip">{linked} active {linked === 1 ? 'workspace' : 'workspaces'}</span>}</div>
        {parent && <div className="library-group-path" title={path}>{source.groups.map((group, i) => <span key={group.group_id}>
          {i > 0 && <span aria-hidden="true"> › </span>}{group.kind && <span className="library-kind">{group.kind}</span>} {group.name}
        </span>)}</div>}
        {source.groups.some(group => group.warning) && <span className="library-group-warning">Group configuration needs attention</span>}
      </div>
    </button>
  })
}
