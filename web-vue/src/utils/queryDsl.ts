/**
 * PaperVault query DSL — Web of Science compatible flavour.
 *
 * Inspired by Web of Science Core Collection search syntax
 * (https://webofscience.help.clarivate.com/en-us/Content/search-operators.html).
 * The grammar accepts both casual queries (`federated learning`) and
 * structured expressions
 * (`TS=(federated AND (privacy OR fairness)) NOT survey SO=ICLR,NeurIPS PY=2024-2026`).
 *
 * Field tags (case-insensitive). Both the WoS-style short tags and
 * developer-friendly long aliases are supported:
 *
 *   TS / topic             → title + abstract + author keywords (default)
 *   TI / title             → title only
 *   AB / abstract          → abstract only
 *   AU / author            → author name(s)
 *   SO / conf / venue      → conference / venue acronym (list-friendly)
 *   PY / year              → publication year (single or range)
 *   AK / keywords          → author keywords (reserved; mirrors TS for now)
 *
 * Operators (high → low precedence, mirroring WoS):
 *
 *   NEAR/x          words within x other words of each other (default 15)
 *   NOT  /  -       exclusion
 *   AND  /  <space> conjunction (implicit AND between adjacent tokens)
 *   OR              disjunction
 *
 * Phrase: "quoted text"      → exact phrase, lemmatisation disabled (we
 *                              just do a case-insensitive substring match)
 * Range : 2023..2026 / 2023-2026 (only valid for PY)
 * List  : SO=ICLR,NeurIPS    → matches any of the values for that field
 * Group : (a OR b) AND c     → parentheses override precedence
 *
 * The module exposes:
 *   - parseDsl       : text → AST
 *   - evaluateDsl    : (paper, AST) → boolean (used by SearchResultList)
 *   - splitForBackend: hoist top-level AND clauses into backend params and
 *                      keep the residual AST for client-side re-filtering
 *   - buildDsl       : programmatic builder used by the Advanced Search page
 */

export type AstNode =
  | { kind: 'and'; nodes: AstNode[] }
  | { kind: 'or'; nodes: AstNode[] }
  | { kind: 'not'; node: AstNode }
  | { kind: 'near'; left: AstNode; right: AstNode; distance: number }
  | { kind: 'term'; field: string | null; value: string; phrase: boolean }
  | { kind: 'list'; field: string; values: string[] }
  | { kind: 'range'; field: string; from: number; to: number }
  | { kind: 'empty' }

export interface DslPaper {
  title?: string | null
  abstract?: string | null
  authors?: string[] | null
  conf?: string | null
  year?: number | string | null
}

interface Token {
  type: 'word' | 'phrase' | 'lparen' | 'rparen' | 'op' | 'minus' | 'near'
  value: string
  /** Distance for NEAR/x; defaults to 15 (WoS default). */
  distance?: number
}

const BOOL_OPS = new Set(['AND', 'OR', 'NOT'])

/**
 * Canonical internal field names. Anything the parser sees through an alias
 * is rewritten to one of these before reaching the evaluator/splitter.
 */
export const FIELD_ALIASES: Record<string, string> = {
  ts: 'topic',
  topic: 'topic',
  ti: 'title',
  title: 'title',
  ab: 'abstract',
  abstract: 'abstract',
  au: 'author',
  author: 'author',
  so: 'conf',
  conf: 'conf',
  venue: 'conf',
  py: 'year',
  year: 'year',
  ak: 'keywords',
  keywords: 'keywords'
}

const canonicalField = (raw: string): string => {
  const k = raw.toLowerCase()
  return FIELD_ALIASES[k] ?? k
}

const tokenize = (input: string): Token[] => {
  const tokens: Token[] = []
  let i = 0
  const n = input.length
  while (i < n) {
    const c = input[i]
    if (c === ' ' || c === '\t' || c === '\n' || c === '\r') {
      i += 1
      continue
    }
    if (c === '(') {
      tokens.push({ type: 'lparen', value: '(' })
      i += 1
      continue
    }
    if (c === ')') {
      tokens.push({ type: 'rparen', value: ')' })
      i += 1
      continue
    }
    if (c === '-' && (i === 0 || /[\s(]/.test(input[i - 1] ?? ''))) {
      tokens.push({ type: 'minus', value: '-' })
      i += 1
      continue
    }
    if (c === '"') {
      let j = i + 1
      while (j < n && input[j] !== '"') j += 1
      const value = input.slice(i + 1, j)
      tokens.push({ type: 'phrase', value })
      i = j < n ? j + 1 : j
      continue
    }
    // Bare word: anything until whitespace / paren / quote
    let j = i
    while (j < n && !/[\s()"]/.test(input[j])) j += 1
    const word = input.slice(i, j)
    const upper = word.toUpperCase()
    // NEAR / NEAR/x — WoS proximity operator
    if (upper === 'NEAR') {
      tokens.push({ type: 'near', value: 'NEAR', distance: 15 })
    } else if (/^NEAR\/\d+$/i.test(word)) {
      const dist = Number(word.split('/')[1])
      tokens.push({ type: 'near', value: 'NEAR', distance: dist })
    } else if (BOOL_OPS.has(upper)) {
      tokens.push({ type: 'op', value: upper })
    } else {
      tokens.push({ type: 'word', value: word })
    }
    i = j
  }
  return tokens
}

/**
 * Push a field qualifier down onto every unqualified leaf inside ``node``.
 * Used to expand ``FIELD=(expr)`` into a sub-tree where each bare term carries
 * the matching field. Pre-existing field qualifiers, list/range nodes are
 * left untouched (the inner expression's intent wins, mirroring WoS).
 */
const applyFieldToAst = (node: AstNode, field: string): AstNode => {
  switch (node.kind) {
    case 'empty':
      return node
    case 'and':
      return {
        kind: 'and',
        nodes: node.nodes.map(n => applyFieldToAst(n, field))
      }
    case 'or':
      return {
        kind: 'or',
        nodes: node.nodes.map(n => applyFieldToAst(n, field))
      }
    case 'not':
      return { kind: 'not', node: applyFieldToAst(node.node, field) }
    case 'near':
      return {
        kind: 'near',
        left: applyFieldToAst(node.left, field),
        right: applyFieldToAst(node.right, field),
        distance: node.distance
      }
    case 'term':
      if (node.field) return node
      return { kind: 'term', field, value: node.value, phrase: node.phrase }
    case 'list':
    case 'range':
      return node
  }
}

/**
 * Recursive-descent parser. Precedence (low → high), matching WoS:
 *   parseOr   →   parseAnd ('OR' parseAnd)*
 *   parseAnd  →   parseNot (('AND' | <implicit>) parseNot)*
 *   parseNot  →   ('NOT' | '-')? parseNear
 *   parseNear →   parseAtom ('NEAR[/x]' parseAtom)*
 *   parseAtom →   '(' parseOr ')' | fieldTerm | phrase | bareWord
 */
class Parser {
  private pos = 0

  constructor(private readonly tokens: Token[]) {}

  parse(): AstNode {
    if (this.tokens.length === 0) return { kind: 'empty' }
    return this.parseOr()
  }

  private peek(): Token | null {
    return this.tokens[this.pos] ?? null
  }

  private consume(): Token | null {
    return this.tokens[this.pos++] ?? null
  }

  private parseOr(): AstNode {
    const nodes: AstNode[] = [this.parseAnd()]
    while (this.peek()?.type === 'op' && this.peek()?.value === 'OR') {
      this.consume()
      nodes.push(this.parseAnd())
    }
    if (nodes.length === 1) return nodes[0]
    return { kind: 'or', nodes }
  }

  private parseAnd(): AstNode {
    const nodes: AstNode[] = [this.parseNot()]
    for (;;) {
      const t = this.peek()
      if (!t) break
      if (t.type === 'rparen') break
      if (t.type === 'op' && t.value === 'OR') break
      if (t.type === 'op' && t.value === 'AND') {
        this.consume()
      }
      // implicit AND between adjacent tokens (mirrors WoS implicit AND)
      nodes.push(this.parseNot())
    }
    if (nodes.length === 1) return nodes[0]
    return { kind: 'and', nodes }
  }

  private parseNot(): AstNode {
    const t = this.peek()
    if (!t) return { kind: 'empty' }
    if (t.type === 'minus' || (t.type === 'op' && t.value === 'NOT')) {
      this.consume()
      return { kind: 'not', node: this.parseNear() }
    }
    return this.parseNear()
  }

  private parseNear(): AstNode {
    let left = this.parseAtom()
    while (this.peek()?.type === 'near') {
      const op = this.consume() as Token
      const right = this.parseAtom()
      left = {
        kind: 'near',
        left,
        right,
        distance: op.distance ?? 15
      }
    }
    return left
  }

  private parseAtom(): AstNode {
    const t = this.consume()
    if (!t) return { kind: 'empty' }
    if (t.type === 'lparen') {
      const inner = this.parseOr()
      if (this.peek()?.type === 'rparen') this.consume()
      return inner
    }
    if (t.type === 'phrase') {
      return { kind: 'term', field: null, value: t.value, phrase: true }
    }
    if (t.type === 'word') {
      // WoS allows `FIELD=(expr)` / `FIELD:(expr)` where the field tag is a
      // standalone token followed by a parenthesised sub-expression. The
      // tokeniser splits at `(`, so we may see a bare `TS=` token here. When
      // the prefix is a known field alias and the next token opens a group,
      // consume that sub-tree and push the field qualifier down onto every
      // unqualified term inside.
      const fieldOnly = this.matchFieldOnlyToken(t.value)
      if (fieldOnly && this.peek()?.type === 'lparen') {
        this.consume()
        const inner = this.parseOr()
        if (this.peek()?.type === 'rparen') this.consume()
        return applyFieldToAst(inner, fieldOnly)
      }
      // The tokeniser also breaks at `"`, so `AU="Sebastian U Stich"` arrives
      // as the bare ``AU=`` word followed by a phrase token. Without this
      // branch the field tag would be dropped (parsed as the free-text term
      // ``AU=`` plus an unrelated phrase), causing the click-an-author flow
      // (and every other quoted field-qualified search) to silently mismatch.
      if (fieldOnly && this.peek()?.type === 'phrase') {
        const phraseTok = this.consume()
        return {
          kind: 'term',
          field: fieldOnly,
          value: phraseTok?.value ?? '',
          phrase: true
        }
      }
      return this.parseWord(t.value)
    }
    return { kind: 'empty' }
  }

  /**
   * If ``word`` looks like a bare field tag (``TS=`` / ``so:`` / ``PY=``)
   * and the prefix resolves to a known field alias, return the canonical
   * field name; otherwise return null.
   */
  private matchFieldOnlyToken(word: string): string | null {
    const eq = word.indexOf('=')
    const colon = word.indexOf(':')
    const sep =
      eq > 0 && (colon < 0 || eq < colon) ? eq : colon > 0 ? colon : -1
    if (sep < 0) return null
    if (sep !== word.length - 1) return null
    const rawField = word.slice(0, sep)
    if (!FIELD_ALIASES[rawField.toLowerCase()]) return null
    return canonicalField(rawField)
  }

  /**
   * Resolve a bare word possibly carrying a field qualifier.
   * Both `field:value` and the WoS-style `FIELD=value` syntax are accepted.
   */
  private parseWord(word: string): AstNode {
    // Try `FIELD=...` first since WoS uses `=` (e.g. TS=cancer).
    const eq = word.indexOf('=')
    const colon = word.indexOf(':')
    const sep =
      eq > 0 && (colon < 0 || eq < colon)
        ? { idx: eq, char: '=' }
        : colon > 0
        ? { idx: colon, char: ':' }
        : null

    if (!sep || sep.idx === word.length - 1) {
      return { kind: 'term', field: null, value: word, phrase: false }
    }

    const rawField = word.slice(0, sep.idx)
    const rest = word.slice(sep.idx + 1)
    const field = canonicalField(rawField)
    // Only resolve to a field qualifier if it's one we actually know about.
    // Otherwise treat the whole token as a free-text term so URLs / DOIs with
    // colons do not break the query.
    if (!FIELD_ALIASES[rawField.toLowerCase()]) {
      return { kind: 'term', field: null, value: word, phrase: false }
    }

    // `field:(expr)` — handled by tokeniser as separate tokens; here we only
    // see the literal `field:` followed by a paren in the token stream. In
    // that uncommon case fall through to a single-value term.

    // Year range: PY=2023-2026 or PY=2023..2026
    const rangeMatch = rest.match(/^(\d{4})(?:\.\.|-)(\d{4})$/)
    if (field === 'year' && rangeMatch) {
      return {
        kind: 'range',
        field,
        from: Number(rangeMatch[1]),
        to: Number(rangeMatch[2])
      }
    }
    // Comma list, e.g. SO=ICLR,NeurIPS
    if (rest.includes(',')) {
      return {
        kind: 'list',
        field,
        values: rest
          .split(',')
          .map(s => s.trim())
          .filter(Boolean)
      }
    }
    return { kind: 'term', field, value: rest, phrase: false }
  }
}

/**
 * Normalise common CJK fullwidth punctuation into the ASCII equivalents the
 * parser expects. Most Chinese-keyboard users naturally type things like
 * `AU=（"Yang Liu"）` or `TS：federated，privacy`; converting them up front
 * lets the DSL "just work" without forcing users to memorise English-only
 * punctuation rules. The mapping is intentionally conservative so it never
 * touches CJK *letters*, only the symbol characters that have a direct
 * 1:1 ASCII counterpart used by the grammar.
 */
const CJK_PUNCT_MAP: Record<string, string> = {
  '（': '(',
  '）': ')',
  '【': '(',
  '】': ')',
  '〔': '(',
  '〕': ')',
  '［': '(',
  '］': ')',
  '〈': '(',
  '〉': ')',
  '《': '(',
  '》': ')',
  '“': '"',
  '”': '"',
  '„': '"',
  '〝': '"',
  '〞': '"',
  '＂': '"',
  '‘': '"',
  '’': '"',
  '＝': '=',
  '：': ':',
  '，': ',',
  '、': ',',
  '；': ' ',
  '。': ' ',
  '！': ' ',
  '？': ' ',
  '－': '-',
  '—': '-',
  '–': '-',
  '～': '-',
  '・': ' ',
  '／': '/',
  '＼': '\\',
  '　': ' '
}

export const normalizeQueryInput = (input: string): string => {
  if (!input) return ''
  let out = ''
  for (const ch of input) {
    out += CJK_PUNCT_MAP[ch] ?? ch
  }
  return out
}

export const parseDsl = (input: string): AstNode => {
  const text = normalizeQueryInput(input ?? '').trim()
  if (!text) return { kind: 'empty' }
  const tokens = tokenize(text)
  return new Parser(tokens).parse()
}

export interface BackendSplit {
  q: string | null
  author: string | null
  conf: string[] | null
  since: number | null
  until: number | null
  residual: AstNode
}

const flattenAnd = (node: AstNode): AstNode[] => {
  if (node.kind === 'and') return node.nodes.flatMap(flattenAnd)
  return [node]
}

/**
 * Walk the AST and hoist whatever the backend can natively narrow with.
 * Anything else (OR / NOT / NEAR / nested mixed fields) is kept in
 * ``residual`` so the client-side evaluator filters further on the loaded
 * payload.
 *
 * The optional second argument is kept for backwards compatibility with the
 * legacy "title vs author" toggle but is no longer used for routing.
 */
export const splitForBackend = (
  ast: AstNode,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _field: 'title' | 'author' | 'any' = 'any'
): BackendSplit => {
  const out: BackendSplit = {
    q: null,
    author: null,
    conf: null,
    since: null,
    until: null,
    residual: { kind: 'empty' }
  }
  if (ast.kind === 'empty') return out

  const clauses = flattenAnd(ast)
  const residual: AstNode[] = []
  const qParts: string[] = []

  for (const clause of clauses) {
    if (clause.kind === 'term' && clause.field === null) {
      // 分词器在到达这里之前已经把 ``clause.value`` 两端的引号剥掉
      // （例如 ``"time series"`` 变成 ``time series``）。后端在归一化
      // ``q`` 时不会剥除字面 ``"``，又是按整体子串匹配 title/abstract，
      // 因此若再用引号包裹，``q`` 里会带上 ``"`` 而 title 文本里没有
      // ``"``，结果必然失配。
      qParts.push(clause.value)
      continue
    }
    // Topic / Title / Abstract terms still need free-text matching upstream;
    // we forward them as plain q so the backend pre-narrows the corpus and
    // let the client-side AST re-filter to the right field.
    if (
      clause.kind === 'term' &&
      (clause.field === 'topic' ||
        clause.field === 'title' ||
        clause.field === 'abstract' ||
        clause.field === 'keywords')
    ) {
      qParts.push(clause.value)
      // Title/abstract/keywords still need residual evaluation so we keep
      // them in residual unless the field is the bag-of-words topic.
      if (clause.field !== 'topic') residual.push(clause)
      continue
    }
    if (clause.kind === 'term' && clause.field === 'author') {
      out.author = clause.value
      continue
    }
    if (clause.kind === 'term' && clause.field === 'conf') {
      out.conf = (out.conf ?? []).concat(clause.value)
      continue
    }
    if (clause.kind === 'list' && clause.field === 'conf') {
      out.conf = (out.conf ?? []).concat(clause.values)
      continue
    }
    if (clause.kind === 'term' && clause.field === 'year') {
      const y = Number(clause.value)
      if (!Number.isNaN(y)) {
        out.since = y
        out.until = y
      }
      continue
    }
    if (clause.kind === 'range' && clause.field === 'year') {
      out.since = Math.min(clause.from, clause.to)
      out.until = Math.max(clause.from, clause.to)
      continue
    }
    residual.push(clause)
  }

  if (qParts.length > 0) out.q = qParts.join(' ')
  if (residual.length === 1) out.residual = residual[0]
  else if (residual.length > 1) out.residual = { kind: 'and', nodes: residual }

  return out
}

const norm = (s: string | null | undefined): string =>
  (s ?? '').toString().toLowerCase()

const matchesTerm = (
  paper: DslPaper,
  field: string | null,
  value: string
): boolean => {
  const v = norm(value)
  if (!v) return true
  switch (field) {
    case 'title':
      return norm(paper.title).includes(v)
    case 'abstract':
      return norm(paper.abstract).includes(v)
    case 'keywords':
      // No dedicated keywords field on the local model; fall back to title +
      // abstract just like WoS Topic does.
      return norm(paper.title).includes(v) || norm(paper.abstract).includes(v)
    case 'author':
      return (paper.authors ?? []).some(a => norm(a).includes(v))
    case 'conf':
      return norm(paper.conf) === v || norm(paper.conf).includes(v)
    case 'year':
      return String(paper.year ?? '').toLowerCase() === v
    case 'topic':
    case null:
    default: {
      // Topic = title + abstract + author keywords + (we throw in venue/author
      // so the free-text behaviour stays useful on a small local corpus).
      const hay =
        norm(paper.title) +
        ' ' +
        norm(paper.abstract) +
        ' ' +
        (paper.authors ?? []).map(norm).join(' ') +
        ' ' +
        norm(paper.conf)
      return hay.includes(v)
    }
  }
}

/**
 * For NEAR/x we need word-level proximity, not just a substring hit. We
 * tokenise the haystack and check the minimal distance between any pair of
 * occurrences. Used by the evaluator only.
 */
const wordsOf = (s: string): string[] =>
  norm(s)
    .split(/[^a-z0-9]+/)
    .filter(Boolean)

const proximityOk = (
  paper: DslPaper,
  field: string | null,
  a: string,
  b: string,
  distance: number
): boolean => {
  const hay =
    field === 'title'
      ? norm(paper.title)
      : field === 'abstract'
      ? norm(paper.abstract)
      : norm(paper.title) + ' ' + norm(paper.abstract)
  const words = wordsOf(hay)
  const left = norm(a)
  const right = norm(b)
  if (!left || !right) return false
  const lefts: number[] = []
  const rights: number[] = []
  for (let i = 0; i < words.length; i += 1) {
    if (words[i] === left) lefts.push(i)
    if (words[i] === right) rights.push(i)
  }
  if (lefts.length === 0 || rights.length === 0) return false
  for (const li of lefts) {
    for (const ri of rights) {
      if (Math.abs(li - ri) - 1 <= distance) return true
    }
  }
  return false
}

const inferField = (node: AstNode): string | null => {
  if (node.kind === 'term') return node.field
  if (node.kind === 'list') return node.field
  return null
}

export const evaluateDsl = (paper: DslPaper, ast: AstNode): boolean => {
  switch (ast.kind) {
    case 'empty':
      return true
    case 'and':
      return ast.nodes.every(n => evaluateDsl(paper, n))
    case 'or':
      return ast.nodes.some(n => evaluateDsl(paper, n))
    case 'not':
      return !evaluateDsl(paper, ast.node)
    case 'term':
      return matchesTerm(paper, ast.field, ast.value)
    case 'list':
      return ast.values.some(v => matchesTerm(paper, ast.field, v))
    case 'range': {
      const y = Number(paper.year)
      if (Number.isNaN(y)) return false
      const lo = Math.min(ast.from, ast.to)
      const hi = Math.max(ast.from, ast.to)
      return y >= lo && y <= hi
    }
    case 'near': {
      // Only term-vs-term proximity is meaningful here; fall back to AND for
      // anything more complex.
      if (ast.left.kind !== 'term' || ast.right.kind !== 'term') {
        return evaluateDsl(paper, ast.left) && evaluateDsl(paper, ast.right)
      }
      const field = ast.left.field ?? ast.right.field ?? inferField(ast.left)
      return proximityOk(
        paper,
        field,
        ast.left.value,
        ast.right.value,
        ast.distance
      )
    }
    default:
      return true
  }
}

const quoteIfNeeded = (v: string): string => (/\s/.test(v) ? `"${v}"` : v)

/** ---------------------------------------------------------------------
 * buildDsl — programmatic builder used by the Advanced Search page.
 * Accepts either the legacy flat shape (single author/year/confs) or a
 * row-based shape (multiple field=value rows joined by AND/OR/NOT).
 * ------------------------------------------------------------------- */

export interface BuildDslInput {
  query?: string
  author?: string
  year?: string | number | null
  specificYear?: string | number | null
  confs?: string[]
}

export interface DslRow {
  /** Canonical field name (topic / title / abstract / author / conf / year). */
  field: string
  /** Free-text value the user typed for this row. */
  value: string
  /**
   * Boolean operator that joins this row to the PREVIOUS one. The first row
   * ignores this. Defaults to 'AND'.
   */
  op?: 'AND' | 'OR' | 'NOT'
}

const FIELD_TO_TAG: Record<string, string> = {
  topic: 'TS',
  title: 'TI',
  abstract: 'AB',
  author: 'AU',
  conf: 'SO',
  year: 'PY',
  keywords: 'AK'
}

const isRowArray = (x: unknown): x is DslRow[] =>
  Array.isArray(x) &&
  x.length > 0 &&
  typeof (x[0] as DslRow)?.field === 'string'

/**
 * Render one row (already filtered to non-empty) as a DSL fragment using the
 * short field tag (TS / TI / AB / AU / SO / PY).
 */
const renderRow = (row: DslRow): string => {
  const field = canonicalField(row.field)
  const tag = FIELD_TO_TAG[field] ?? 'TS'
  const value = row.value.trim()
  if (!value) return ''

  // Year supports range syntax 2023-2026 / 2023..2026 / single year.
  if (field === 'year') {
    return `${tag}=${value}`
  }
  // Venue lists keep their commas; everything else gets wrapped in parens so
  // multi-word values combine cleanly with surrounding boolean operators.
  if (field === 'conf' && value.includes(',')) {
    return `${tag}=${value
      .split(',')
      .map(s => s.trim())
      .filter(Boolean)
      .join(',')}`
  }
  if (/\s/.test(value)) {
    // If the user already wrote operators (or a leading-mid minus NOT like
    // ``-survey``), wrap in parens; otherwise quote the phrase so we keep
    // WoS-style exact phrase semantics.
    if (
      /\b(AND|OR|NOT|NEAR)\b/i.test(value) ||
      /[()]/.test(value) ||
      /(^|\s)-\S/.test(value)
    ) {
      return `${tag}=(${value})`
    }
    return `${tag}="${value}"`
  }
  return `${tag}=${value}`
}

export function buildDsl(input: BuildDslInput | DslRow[]): string {
  if (isRowArray(input)) {
    const parts: string[] = []
    for (let i = 0; i < input.length; i += 1) {
      const fragment = renderRow(input[i])
      if (!fragment) continue
      if (parts.length === 0) {
        parts.push(fragment)
      } else {
        const op = (input[i].op ?? 'AND').toUpperCase()
        parts.push(op, fragment)
      }
    }
    return parts.join(' ')
  }

  const state = input
  const parts: string[] = []
  if (state.query) parts.push(state.query.trim())
  if (state.author) parts.push(`AU=${quoteIfNeeded(state.author.trim())}`)
  if (state.specificYear) {
    parts.push(`PY=${state.specificYear}`)
  } else if (state.year) {
    const y = Number(state.year)
    if (!Number.isNaN(y)) {
      const now = new Date().getFullYear()
      parts.push(`PY=${y}-${now}`)
    }
  }
  if (state.confs && state.confs.length > 0) {
    parts.push(`SO=${state.confs.join(',')}`)
  }
  return parts.filter(Boolean).join(' ')
}

/** ---------------------------------------------------------------------
 * parseDslToRows — inverse of buildDsl, used by the Advanced Search page
 * to import a text DSL back into the visual row builder.
 *
 * The row model can only express a flat AND/OR/NOT chain of term/list/
 * range leaves, so round-tripping is faithful exactly for:
 *   - a single leaf,
 *   - an AND chain of leaves (NOT-wrapped leaves → op 'NOT'),
 *   - an OR chain whose legs are leaves or pure AND chains of leaves
 *     (matches the precedence the rebuilt text will parse back with).
 * Anything else (NEAR, parenthesised OR groups inside an AND, NOT around a
 * group, a leading NOT, OR-of-NOT) has no row representation. In that case
 * the function still succeeds but returns a single topic row holding the
 * original text plus ``supported: false`` — renderRow wraps it as
 * ``TS=(original)``, which preserves the original semantics exactly while
 * signalling to the UI that the rows are not individually editable.
 * ------------------------------------------------------------------- */

export interface ParseRowsResult {
  /** Builder rows; year rows use value ``from-to`` like the UI's year row. */
  rows: DslRow[]
  /** false when the AST contains structures the row model cannot express. */
  supported: boolean
}

interface RowLeaf {
  field: string
  value: string
  negated: boolean
}

/**
 * Reduce a node to a leaf (term / list / range), remembering a NOT wrapper.
 * Returns null for anything structured (and / or / near / not-of-group).
 */
const leafFromNode = (node: AstNode): RowLeaf | null => {
  let negated = false
  let n = node
  if (n.kind === 'not') {
    negated = true
    n = n.node
  }
  switch (n.kind) {
    case 'term': {
      // AK mirrors TS by design; fold it back so the builder's fixed field
      // list (which has no keywords option) can represent the row.
      const field =
        n.field === 'keywords' || n.field === null ? 'topic' : n.field
      return { field, value: n.value, negated }
    }
    case 'list':
      return {
        field: n.field === 'keywords' ? 'topic' : n.field,
        value: n.values.join(','),
        negated
      }
    case 'range':
      return {
        field: n.field,
        value: `${Math.min(n.from, n.to)}-${Math.max(n.from, n.to)}`,
        negated
      }
    default:
      return null
  }
}

/** Flatten same-kind nesting: `(a AND b) AND c` ≡ `a AND b AND c`. */
const flattenChain = (node: AstNode, kind: 'and' | 'or'): AstNode[] => {
  if (node.kind === kind) return node.nodes.flatMap(n => flattenChain(n, kind))
  return [node]
}

/** An OR leg: a leaf, or a pure AND chain of leaves. */
const andUnitToLeaves = (node: AstNode): RowLeaf[] | null => {
  const leaves = flattenChain(node, 'and').map(leafFromNode)
  if (leaves.some(l => l === null)) return null
  return leaves as RowLeaf[]
}

const toRow = (leaf: RowLeaf, op: 'AND' | 'OR' | 'NOT'): DslRow => ({
  field: leaf.field,
  value: leaf.value,
  op
})

const rowsFromAst = (ast: AstNode): DslRow[] | null => {
  if (ast.kind === 'empty') return []

  let rows: DslRow[]
  if (ast.kind === 'or') {
    rows = []
    for (const clause of flattenChain(ast, 'or')) {
      const leaves = andUnitToLeaves(clause)
      if (!leaves) return null
      // `a OR NOT b` has no row form: the rebuilt `a NOT b` would AND.
      if (leaves[0].negated) return null
      leaves.forEach((leaf, i) => {
        rows.push(toRow(leaf, i === 0 ? 'OR' : leaf.negated ? 'NOT' : 'AND'))
      })
    }
  } else {
    // Single leaf or a pure AND chain (flattenChain is the identity for
    // non-and nodes, so this also covers the lone-leaf case).
    const leaves = andUnitToLeaves(ast)
    if (!leaves) return null
    rows = leaves.map(leaf => toRow(leaf, leaf.negated ? 'NOT' : 'AND'))
  }

  if (rows.length === 0) return rows
  // The first row's op is ignored by buildDsl, so a leading NOT would be
  // silently dropped on rebuild — refuse to claim fidelity for that.
  if (rows[0].op === 'NOT') return null
  rows[0] = { ...rows[0], op: 'AND' }
  return rows
}

export const parseDslToRows = (input: string): ParseRowsResult => {
  const text = normalizeQueryInput(input ?? '').trim()
  if (!text) return { rows: [], supported: true }
  const rows = rowsFromAst(parseDsl(text))
  if (rows) return { rows, supported: true }
  return { rows: [{ field: 'topic', value: text }], supported: false }
}

/** ---------------------------------------------------------------------
 * DSL → coarse backend params / count plan
 *
 * The backend does NOT parse the DSL: ``q`` is matched as AND-ed substrings
 * over title/abstract, so forwarding raw DSL text (``TS=a AND PY=2024``)
 * would AND the literal tokens ``ts=a`` / ``and`` / ``py=2024`` and return
 * zero hits. These helpers mirror useHomeSearch's param building: hoist
 * AND-able qualifiers via splitForBackend, and when nothing could be
 * hoisted fall back to a cleaned free-text ``q`` so the backend does not
 * scan the whole corpus.
 *
 * ``dslToCoarseParams`` squeezes the whole expression into ONE param set;
 * for a top-level OR it ANDs all text terms into ``q`` — a lower bound that
 * can collapse to 0 for synonym merges. ``dslToCountPlan`` is the count-
 * oriented wrapper: it splits a top-level OR into one param set per leg so
 * the caller can count the legs individually and combine them.
 * ------------------------------------------------------------------- */

export interface CoarseBackendParams {
  q?: string
  author?: string
  conf?: string[]
  since?: number
  until?: number
}

/**
 * Collect the values of text-ish term leaves (topic / title / abstract /
 * keywords / unfielded) for the coarse-q fallback. NOT subtrees are skipped
 * — excluded terms must never enter an AND filter. Field-qualified non-text
 * leaves (author / conf / year) are skipped because their values (names,
 * venue acronyms, years) virtually never appear in title/abstract text, so
 * AND-ing them would zero out the count.
 */
const collectTextTerms = (node: AstNode, acc: string[]): void => {
  switch (node.kind) {
    case 'term':
      if (
        node.field === null ||
        node.field === 'topic' ||
        node.field === 'title' ||
        node.field === 'abstract' ||
        node.field === 'keywords'
      ) {
        acc.push(node.value)
      }
      return
    case 'and':
    case 'or':
      node.nodes.forEach(n => collectTextTerms(n, acc))
      return
    case 'near':
      collectTextTerms(node.left, acc)
      collectTextTerms(node.right, acc)
      return
    default:
      return
  }
}

/**
 * Coarse params for an already-parsed AST. ``rawFallback`` is forwarded as
 * ``q`` only when the AST is empty (malformed input); pass '' for OR legs,
 * where an unparseable leg should simply contribute nothing.
 */
const coarseParamsFromAst = (
  ast: AstNode,
  rawFallback: string
): CoarseBackendParams => {
  const split = splitForBackend(ast)
  const out: CoarseBackendParams = {}
  if (split.q) {
    out.q = split.q
  } else if (ast.kind === 'empty') {
    if (rawFallback) out.q = rawFallback
  } else if (
    !split.author &&
    !split.conf &&
    split.since == null &&
    split.until == null
  ) {
    // Nothing hoistable (nested OR / NOT / NEAR). Collect the actual text
    // terms from the AST rather than cleaning the raw string — the latter
    // would leave field tags (``TS=``) in ``q`` and AND them into a
    // guaranteed-zero substring filter. The backend still gets a coarse
    // pre-filter instead of scanning the whole corpus.
    const terms: string[] = []
    collectTextTerms(ast, terms)
    if (terms.length > 0) out.q = terms.join(' ')
  }
  if (split.author) out.author = split.author
  if (split.conf && split.conf.length > 0) out.conf = split.conf
  if (split.since != null) out.since = split.since
  if (split.until != null) out.until = split.until
  return out
}

export const dslToCoarseParams = (dsl: string): CoarseBackendParams => {
  const raw = normalizeQueryInput(dsl ?? '').trim()
  if (!raw) return {}
  return coarseParamsFromAst(parseDsl(raw), raw)
}

export type CountPlan =
  | { kind: 'none' }
  | { kind: 'single'; params: CoarseBackendParams }
  | { kind: 'or'; legs: CoarseBackendParams[] }

/**
 * Plan the backend queries needed to approximate a hit count for ``dsl``.
 *
 * A top-level OR becomes one param set per leg (`kind: 'or'`) so the caller
 * can count each disjunct separately — the backend has no text-OR, and
 * squeezing all legs into one ``q`` would AND them into a lower bound that
 * collapses to 0 for the synonym-merge queries this app produces most.
 * Field-qualified legs (``AU=… OR TS=…``) work too: each leg hoists its own
 * author/conf/year params. Legs that narrow nothing (e.g. a bare ``NOT``)
 * are dropped; if fewer than two legs survive, the whole expression falls
 * back to a single coarse param set.
 */
export const dslToCountPlan = (dsl: string): CountPlan => {
  const raw = normalizeQueryInput(dsl ?? '').trim()
  if (!raw) return { kind: 'none' }
  const ast = parseDsl(raw)
  if (ast.kind === 'or') {
    const legs = flattenChain(ast, 'or')
      .map(leg => coarseParamsFromAst(leg, ''))
      .filter(p => Object.keys(p).length > 0)
    if (legs.length >= 2) return { kind: 'or', legs }
  }
  return { kind: 'single', params: coarseParamsFromAst(ast, raw) }
}
