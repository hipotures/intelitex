import type { Section } from '../../api/schema'

/** Older Prepare snapshots can contain converter filenames instead of book headings. */
export function sectionTitles(sections: Pick<Section, 'id' | 'title' | 'fallback_excerpt'>[], creators: string[] = []): Map<string, string> {
  const titles = new Map<string, string>()
  let volume: string | null = null
  for (const section of sections) {
    const original = section.title?.trim()
    if (original && !/_split_\d+$/i.test(original)) {
      titles.set(section.id, original)
      continue
    }
    const excerpt = section.fallback_excerpt?.trim() ?? ''
    const contents = excerpt.match(/^\*\*([^*\n]{2,80})\nContents\*\*/i)
    if (contents) {
      volume = contents[1]!.trim()
      titles.set(section.id, `${volume} · Contents`)
      continue
    }
    if (/^This book is a work of fiction\b/i.test(excerpt) || /^.{0,70}\bCopyright\s*©/i.test(excerpt)) {
      titles.set(section.id, 'Copyright')
      continue
    }
    if (/^Contents(?:\s|$)/i.test(excerpt)) {
      titles.set(section.id, 'Contents')
      continue
    }
    const author = creators.find(name => name && excerpt.toLocaleLowerCase().includes(name.toLocaleLowerCase()))
    if (author) {
      const heading = excerpt.slice(0, excerpt.toLocaleLowerCase().indexOf(author.toLocaleLowerCase())).trim()
      if (/^[\p{Lu}\s'’:-]{6,80}$/u.test(heading)) {
        volume = heading
        titles.set(section.id, heading)
        continue
      }
    }
    const named = excerpt.match(/^\*\*(CAST OF CHARACTERS|PRINCIPAL CHARACTERS|PROLOGUE|EPILOGUE|TIMELINE)\*\*/i)
    if (named) {
      titles.set(section.id, named[1]!)
      continue
    }
    const part = excerpt.match(/^\*\*(Part\s+\d+\s*:[^*]{1,70})\*\*/i)
    const number = (part ? excerpt.slice(part[0].length).trimStart() : excerpt).match(/^(?:\*\*)?(\d{1,3})(?:\*\*)?(?=\s|$)/)
    if (number) {
      titles.set(section.id, `${volume ? `${volume} · ` : ''}Chapter ${number[1]}`)
      continue
    }
    if (part) {
      titles.set(section.id, part[1]!.trim())
      continue
    }
    titles.set(section.id, original || excerpt || 'Untitled section')
  }
  return titles
}
