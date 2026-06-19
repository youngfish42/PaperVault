type PaperLike = {
  title?: string
  url?: string
  authors?: string[] | string
  abstract?: string
  code?: string
  conf?: string
  year?: string | number
}

const CSV_HEADER = ['title', 'url', 'authors', 'abstract', 'code', 'conf', 'year'] as const

function escapeCsvCell(value: unknown): string {
  if (Array.isArray(value)) {
    return `"${value.join(',').replace(/"/g, '""')}"`
  }
  const str = value == null ? '' : String(value)
  return `"${str.replace(/"/g, '""')}"`
}

function downloadBlob(blob: Blob, fileName: string): void {
  const link = document.createElement('a')
  const url = URL.createObjectURL(blob)
  link.href = url
  link.download = fileName
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

export default {
  exportCSV(jsonData: PaperLike[], fileName = 'exportCSV.csv'): void {
    if (!jsonData || jsonData.length === 0) return
    const lines: string[] = [CSV_HEADER.join(',')]
    for (const row of jsonData) {
      lines.push(CSV_HEADER.map(key => escapeCsvCell((row as any)[key])).join(','))
    }
    const text = '\ufeff' + lines.join('\n')
    downloadBlob(new Blob([text], { type: 'text/csv;charset=utf-8' }), fileName)
  },

  exportTxt(jsonData: PaperLike[], fileName = 'exportTXT.txt'): void {
    if (!jsonData || jsonData.length === 0) return
    const text = jsonData
      .map(v => `[${(v.conf ?? '') + (v.year ?? '')}]\t${v.title ?? ''}`)
      .join('\r\n')
    downloadBlob(new Blob([text], { type: 'text/plain;charset=utf-8' }), fileName)
  }
}
