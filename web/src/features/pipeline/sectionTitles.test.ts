import { describe, expect, it } from 'vitest'
import { sectionTitles } from './sectionTitles'

describe('sectionTitles', () => {
  it('recovers chapter and volume labels from saved split-file excerpts', () => {
    const titles = sectionTitles([
      { id: 'one', title: 'Writer - Trilogy_split_001', fallback_excerpt: 'Contents The River Book' },
      { id: 'two', title: 'Writer - Trilogy_split_002', fallback_excerpt: 'THE RIVER BOOK ADA WRITER' },
      { id: 'three', title: 'Writer - Trilogy_split_003', fallback_excerpt: '**The River Book\nContents** Chapter 1 Chapter 2' },
      { id: 'four', title: 'Writer - Trilogy_split_004', fallback_excerpt: '**Part 1: Dawn** **1** The ship arrived.' },
      { id: 'five', title: 'Writer - Trilogy_split_005', fallback_excerpt: '**2** The voyage continued.' },
      { id: 'six', title: 'Writer - Trilogy_split_006', fallback_excerpt: '**The Second Book\nContents** Chapter 1' },
      { id: 'seven', title: 'Writer - Trilogy_split_007', fallback_excerpt: '1 A new story begins.' },
      { id: 'eight', title: 'Writer - Trilogy_split_008', fallback_excerpt: '**EPILOGUE** A final letter.' },
    ], ['Ada Writer'])
    expect([...titles.values()]).toEqual([
      'Contents', 'THE RIVER BOOK', 'The River Book · Contents',
      'The River Book · Chapter 1', 'The River Book · Chapter 2',
      'The Second Book · Contents', 'The Second Book · Chapter 1', 'EPILOGUE',
    ])
  })

  it('preserves real titles and leaves unrecognised filenames visible', () => {
    const titles = sectionTitles([
      { id: 'named', title: 'A Real Chapter', fallback_excerpt: '1 Opening.' },
      { id: 'unknown', title: 'book_split_009', fallback_excerpt: 'The rain crossed the valley.' },
    ])
    expect([...titles.values()]).toEqual(['A Real Chapter', 'book_split_009'])
  })
})
