import type { ReactNode } from 'react'

function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = []
  const pattern = /(`[^`]+`|\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*|\*[^*]+\*|_[^_]+_)/g
  let cursor = 0
  let match = pattern.exec(text)

  while (match) {
    if (match.index > cursor) {
      out.push(text.slice(cursor, match.index))
    }
    const token = match[0]
    if (token.startsWith('`') && token.endsWith('`')) {
      out.push(<code key={`${match.index}-code`}>{token.slice(1, -1)}</code>)
    } else if (token.startsWith('[') && token.includes('](') && token.endsWith(')')) {
      const splitAt = token.indexOf('](')
      const label = token.slice(1, splitAt)
      const href = token.slice(splitAt + 2, -1)
      out.push(
        <a key={`${match.index}-link`} href={href} target="_blank" rel="noreferrer">
          {label}
        </a>
      )
    } else if (token.startsWith('**') && token.endsWith('**')) {
      out.push(<strong key={`${match.index}-strong`}>{token.slice(2, -2)}</strong>)
    } else if (token.startsWith('*') && token.endsWith('*')) {
      out.push(<em key={`${match.index}-em`}>{token.slice(1, -1)}</em>)
    } else if (token.startsWith('_') && token.endsWith('_')) {
      out.push(<em key={`${match.index}-em-alt`}>{token.slice(1, -1)}</em>)
    } else {
      out.push(token)
    }
    cursor = match.index + token.length
    match = pattern.exec(text)
  }

  if (cursor < text.length) {
    out.push(text.slice(cursor))
  }
  return out
}

function isTableDivider(line: string): boolean {
  const trimmed = line.trim()
  if (!trimmed.includes('-') || !trimmed.includes('|')) return false
  return /^[:\-\s|]+$/.test(trimmed)
}

function parseTableRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  return trimmed.split('|').map((cell) => cell.trim())
}

function renderMarkdown(md: string): ReactNode[] {
  const rows = md.split('\n')
  const blocks: ReactNode[] = []

  let i = 0
  while (i < rows.length) {
    const raw = rows[i]
    const line = raw.trim()
    if (!line) {
      i += 1
      continue
    }

    if (line.startsWith('```')) {
      const lang = line.slice(3).trim()
      i += 1
      const codeRows: string[] = []
      while (i < rows.length && !rows[i].trim().startsWith('```')) {
        codeRows.push(rows[i])
        i += 1
      }
      if (i < rows.length && rows[i].trim().startsWith('```')) {
        i += 1
      }
      blocks.push(
        <pre key={`pre-${i}`}>
          <code className={lang ? `language-${lang}` : undefined}>{codeRows.join('\n')}</code>
        </pre>
      )
      continue
    }

    if (i + 1 < rows.length && line.includes('|') && isTableDivider(rows[i + 1])) {
      const headers = parseTableRow(rows[i])
      i += 2
      const bodyRows: string[][] = []
      while (i < rows.length && rows[i].includes('|') && rows[i].trim()) {
        bodyRows.push(parseTableRow(rows[i]))
        i += 1
      }

      blocks.push(
        <div className="md-table-wrap" key={`table-${i}`}>
          <table className="md-table">
            <thead>
              <tr>
                {headers.map((header, index) => (
                  <th key={`th-${i}-${index}`}>{renderInline(header)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bodyRows.map((cells, rowIndex) => (
                <tr key={`tr-${i}-${rowIndex}`}>
                  {headers.map((_, cellIndex) => (
                    <td key={`td-${i}-${rowIndex}-${cellIndex}`}>{renderInline(cells[cellIndex] || '')}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
      continue
    }

    if (line.startsWith('>')) {
      const quoteLines: string[] = []
      while (i < rows.length && rows[i].trim().startsWith('>')) {
        quoteLines.push(rows[i].trim().replace(/^>\s?/, ''))
        i += 1
      }
      blocks.push(<blockquote key={`quote-${i}`}>{renderInline(quoteLines.join(' '))}</blockquote>)
      continue
    }

    const heading = line.match(/^(#{1,4})\s+(.*)$/)
    if (heading) {
      const level = heading[1].length
      const content = heading[2]
      if (level === 1) blocks.push(<h1 key={`h-${i}`}>{renderInline(content)}</h1>)
      else if (level === 2) blocks.push(<h2 key={`h-${i}`}>{renderInline(content)}</h2>)
      else if (level === 3) blocks.push(<h3 key={`h-${i}`}>{renderInline(content)}</h3>)
      else blocks.push(<h4 key={`h-${i}`}>{renderInline(content)}</h4>)
      i += 1
      continue
    }

    if (/^[-*]\s+/.test(line)) {
      const items: ReactNode[] = []
      while (i < rows.length && /^[-*]\s+/.test(rows[i].trim())) {
        const itemText = rows[i].trim().replace(/^[-*]\s+/, '')
        items.push(<li key={`ul-${i}`}>{renderInline(itemText)}</li>)
        i += 1
      }
      blocks.push(<ul key={`ul-block-${i}`}>{items}</ul>)
      continue
    }

    if (/^\d+\.\s+/.test(line)) {
      const items: ReactNode[] = []
      while (i < rows.length && /^\d+\.\s+/.test(rows[i].trim())) {
        const itemText = rows[i].trim().replace(/^\d+\.\s+/, '')
        items.push(<li key={`ol-${i}`}>{renderInline(itemText)}</li>)
        i += 1
      }
      blocks.push(<ol key={`ol-block-${i}`}>{items}</ol>)
      continue
    }

    const paragraphParts: string[] = []
    while (i < rows.length) {
      const next = rows[i].trim()
      if (
        !next ||
        /^(#{1,4})\s+/.test(next) ||
        /^[-*]\s+/.test(next) ||
        /^\d+\.\s+/.test(next) ||
        next.startsWith('```') ||
        next.startsWith('>')
      ) {
        break
      }
      paragraphParts.push(next)
      i += 1
    }
    blocks.push(<p key={`p-${i}`}>{renderInline(paragraphParts.join(' '))}</p>)
  }

  return blocks
}

interface Props {
  markdown: string
}

export function RichMarkdown({ markdown }: Props) {
  return <>{renderMarkdown(markdown)}</>
}
