/**
 * Zero-dependency regression tests for ``src/utils/queryDsl.ts``.
 *
 * Why this exists
 * ----------------
 * The DSL parser/splitter/evaluator is the heart of the WoS-style search
 * overhaul and has already produced one user-visible regression (the
 * ``AU="Xiaowen Jiang"`` → empty-result bug fixed in commit 4c94a7e by
 * tightening ``HomeView.buildBaseQuery``'s ``q`` fallback). To prevent that
 * class of bug from creeping back in, this file pins down the contract of
 * the three exported entry points the rest of the front-end relies on:
 *
 *   - ``parseDsl``       – text → AST, including CJK punctuation handling
 *   - ``splitForBackend``– AST → backend params + residual AST (the exact
 *                          surface area that decides what gets sent as
 *                          ``q`` / ``author`` / ``conf`` / ``since`` /
 *                          ``until`` and what stays for client-side filtering)
 *   - ``evaluateDsl``    – AST applied to a paper record (the residual
 *                          filter run inside ``SearchResultList``)
 *
 * Running it
 * ----------
 *     cd web-vue
 *     node src/utils/__tests__/queryDsl.test.mjs
 *
 * The script transpiles ``queryDsl.ts`` on the fly with the project's own
 * ``typescript`` devDependency so we don't have to add Vitest/Jest just to
 * cover one file. It deliberately writes the compiled output to a
 * gitignored ``.tmp`` sibling so the workspace stays clean.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createRequire } from 'node:module'

const here = dirname(fileURLToPath(import.meta.url))
const srcPath = resolve(here, '..', 'queryDsl.ts')
const tmpDir = resolve(here, '.tmp')
const outPath = resolve(tmpDir, 'queryDsl.mjs')

const require = createRequire(import.meta.url)
const ts = require('typescript')

mkdirSync(tmpDir, { recursive: true })
const source = readFileSync(srcPath, 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2020,
    module: ts.ModuleKind.ESNext,
    esModuleInterop: true
  },
  fileName: 'queryDsl.ts'
})
writeFileSync(outPath, outputText)
const mod = await import(pathToFileURL(outPath).href)
// Clean up the on-disk artefact once import has resolved.
try {
  rmSync(tmpDir, { recursive: true, force: true })
} catch {
  /* best-effort cleanup */
}

const { parseDsl, splitForBackend, evaluateDsl, normalizeQueryInput } = mod

// ---------------------------------------------------------------------------
// parseDsl + normalizeQueryInput
// ---------------------------------------------------------------------------

test('parseDsl: empty / whitespace / null input → empty AST', () => {
  assert.equal(parseDsl('').kind, 'empty')
  assert.equal(parseDsl('   ').kind, 'empty')
  assert.equal(parseDsl(null).kind, 'empty')
  assert.equal(parseDsl(undefined).kind, 'empty')
})

test('parseDsl: bare keywords → topic-bound terms joined by implicit AND', () => {
  const ast = parseDsl('federated learning')
  // Implicit AND between two unfielded words.
  assert.equal(ast.kind, 'and')
  assert.equal(ast.nodes.length, 2)
  assert.equal(ast.nodes[0].kind, 'term')
  assert.equal(ast.nodes[0].field, null)
  assert.equal(ast.nodes[0].value, 'federated')
})

test('parseDsl: AU="Xiaowen Jiang" preserves multi-word author phrase', () => {
  // This is the exact input that triggered the production bug. The phrase
  // MUST survive parsing as a single author term, not be split on the
  // closing quote.
  const ast = parseDsl('AU="Xiaowen Jiang"')
  assert.equal(ast.kind, 'term')
  assert.equal(ast.field, 'author')
  assert.equal(ast.value, 'Xiaowen Jiang')
  assert.equal(ast.phrase, true)
})

test('normalizeQueryInput: CJK fullwidth punctuation → ASCII', () => {
  // Users typing on a Chinese IME naturally produce these symbols.
  assert.equal(normalizeQueryInput('AU＝（"Yang Liu"）'), 'AU=("Yang Liu")')
  assert.equal(normalizeQueryInput('TS：a，b'), 'TS:a,b')
  assert.equal(normalizeQueryInput('“federated”'), '"federated"')
})

test('parseDsl: CJK-punctuated input still parses to the right AST', () => {
  const ast = parseDsl('AU=（"Yang Liu"）')
  assert.equal(ast.kind, 'term')
  assert.equal(ast.field, 'author')
  assert.equal(ast.value, 'Yang Liu')
})

test('parseDsl: OR / NOT / parens respect WoS precedence', () => {
  const ast = parseDsl('(a OR b) NOT c')
  // Top-level AND has the parenthesised OR and the NOT clause.
  assert.equal(ast.kind, 'and')
  const kinds = ast.nodes.map(n => n.kind).sort()
  assert.deepEqual(kinds, ['not', 'or'])
})

test('parseDsl: NEAR/x parses with explicit distance', () => {
  const ast = parseDsl('privacy NEAR/5 utility')
  assert.equal(ast.kind, 'near')
  assert.equal(ast.distance, 5)
  assert.equal(ast.left.value, 'privacy')
  assert.equal(ast.right.value, 'utility')
})

test('parseDsl: SO list and PY range', () => {
  const list = parseDsl('SO=ICLR,NeurIPS')
  assert.equal(list.kind, 'list')
  assert.equal(list.field, 'conf')
  assert.deepEqual(list.values, ['ICLR', 'NeurIPS'])

  const range = parseDsl('PY=2023-2026')
  assert.equal(range.kind, 'range')
  assert.equal(range.field, 'year')
  assert.equal(range.from, 2023)
  assert.equal(range.to, 2026)
})

// ---------------------------------------------------------------------------
// splitForBackend — the regression hotspot
// ---------------------------------------------------------------------------

test('splitForBackend: AU-only query hoists author and leaves q empty', () => {
  // The regression bug: ``q`` MUST stay null here, otherwise HomeView used
  // to forward the raw DSL text as ``q`` and AND-it with the (correct)
  // ``author`` param, producing zero results.
  const split = splitForBackend(parseDsl('AU="Xiaowen Jiang"'))
  assert.equal(split.q, null)
  assert.equal(split.author, 'Xiaowen Jiang')
  assert.equal(split.conf, null)
  assert.equal(split.since, null)
  assert.equal(split.until, null)
  assert.equal(split.residual.kind, 'empty')
})

test('splitForBackend: mixed AU + SO + PY hoists all three backend params', () => {
  const split = splitForBackend(
    parseDsl('AU="Yang Liu" SO=ICLR,NeurIPS PY=2024-2026')
  )
  assert.equal(split.author, 'Yang Liu')
  assert.deepEqual(split.conf, ['ICLR', 'NeurIPS'])
  assert.equal(split.since, 2024)
  assert.equal(split.until, 2026)
  assert.equal(split.q, null)
})

test('splitForBackend: topic + author keeps topic in q AND author param', () => {
  const split = splitForBackend(parseDsl('federated AU="Yang Liu"'))
  assert.equal(split.q, 'federated')
  assert.equal(split.author, 'Yang Liu')
})

test('splitForBackend: TI=… is sent as q AND kept in residual for refining', () => {
  // Title-bound terms still need a corpus narrowing pass on the backend,
  // but the residual MUST survive so the client-side evaluator can enforce
  // the field constraint (otherwise TI would silently degrade to TS).
  const split = splitForBackend(parseDsl('TI=diffusion'))
  assert.equal(split.q, 'diffusion')
  assert.notEqual(split.residual.kind, 'empty')
  assert.equal(split.residual.kind, 'term')
  assert.equal(split.residual.field, 'title')
})

test('splitForBackend: top-level OR cannot be hoisted → stays in residual', () => {
  const split = splitForBackend(parseDsl('AU="X" OR SO=ICLR'))
  // Neither leg can be promoted independently because the OR semantics
  // would be lost.
  assert.equal(split.author, null)
  assert.equal(split.conf, null)
  assert.equal(split.residual.kind, 'or')
})

test('splitForBackend: AI-merged "seed OR (kw1 OR kw2 OR kw3)" keeps q=null AND residual=whole OR', () => {
  // P3-B's ``buildOrMerge`` emits a parenthesized OR group after a bare
  // seed, e.g. ``time series llm OR ("time-series forecasting" OR
  // "foundation models for TS" OR "time-series LLM")``. The splitter
  // must NOT promote individual OR legs (that would silently degrade the
  // query to its AND-over-OR-leg-tokens prefilter), so it keeps the whole
  // OR tree in ``residual`` and leaves ``q`` null. ``HomeView`` then
  // detects "no field-qualified clauses" and falls back to ``originalTopic``
  // (``"time series llm"``) as a coarse ``q`` so the backend doesn't
  // fan out across the whole corpus (~621k papers).
  const split = splitForBackend(
    parseDsl(
      'time series llm OR ("time-series forecasting" OR "foundation models for TS" OR "time-series LLM")'
    )
  )
  assert.equal(split.q, null)
  assert.equal(split.author, null)
  assert.equal(split.conf, null)
  assert.equal(split.residual.kind, 'or')
  // ``residual`` must contain exactly one OR node whose children are the
  // seed term plus the parenthesised OR group — i.e. we did NOT flatten
  // it, otherwise the frontend evaluator would re-AND the legs.
  assert.equal(split.residual.nodes.length, 2)
})

test('splitForBackend: unqualified phrase forwards q WITHOUT literal quotes', () => {
  // Regression: a previous version of splitForBackend wrapped phrase clauses
  // back in literal ``"…"`` before forwarding them as ``q``. The backend
  // does not strip those quotes and matches ``q`` as a single substring
  // against the normalised title, so the literal ``"`` made every quoted
  // phrase silently miss. The contract is: ``q`` is the bare phrase value.
  const split = splitForBackend(parseDsl('"time series"'))
  assert.equal(split.q, 'time series')
  assert.equal(split.residual.kind, 'empty')
})

test('splitForBackend: TS="…" phrase forwards q WITHOUT literal quotes', () => {
  // Same contract for the explicitly-tagged topic phrase, which is the
  // canonical entry point users type in the search bar.
  const split = splitForBackend(parseDsl('TS="time series"'))
  assert.equal(split.q, 'time series')
})

// ---------------------------------------------------------------------------
// evaluateDsl — covers the residual filter contract
// ---------------------------------------------------------------------------

const samplePaper = {
  title: 'Federated Learning with Differential Privacy',
  abstract:
    'We study privacy and utility trade-offs in federated optimisation.',
  authors: ['Xiaowen Jiang', 'Alice Example'],
  conf: 'ICLR',
  year: 2025
}

test('evaluateDsl: AU substring matches normalised author', () => {
  assert.equal(evaluateDsl(samplePaper, parseDsl('AU="Xiaowen Jiang"')), true)
  assert.equal(evaluateDsl(samplePaper, parseDsl('AU=Jiang')), true)
  assert.equal(evaluateDsl(samplePaper, parseDsl('AU="Nobody"')), false)
})

test('evaluateDsl: TI restricts to title only', () => {
  assert.equal(evaluateDsl(samplePaper, parseDsl('TI=federated')), true)
  // "utility" lives in the abstract, not the title.
  assert.equal(evaluateDsl(samplePaper, parseDsl('TI=utility')), false)
})

test('evaluateDsl: PY range inclusive bounds', () => {
  assert.equal(evaluateDsl(samplePaper, parseDsl('PY=2024-2026')), true)
  assert.equal(evaluateDsl(samplePaper, parseDsl('PY=2026-2030')), false)
})

test('evaluateDsl: NEAR/x respects word distance', () => {
  // "privacy" and "utility" are within a handful of words in the abstract.
  assert.equal(
    evaluateDsl(samplePaper, parseDsl('privacy NEAR/5 utility')),
    true
  )
  // Two unrelated words far apart should not match a tiny window.
  assert.equal(
    evaluateDsl(samplePaper, parseDsl('federated NEAR/0 utility')),
    false
  )
})

test('evaluateDsl: NOT excludes matching papers', () => {
  assert.equal(
    evaluateDsl(samplePaper, parseDsl('federated NOT privacy')),
    false
  )
  assert.equal(evaluateDsl(samplePaper, parseDsl('federated NOT survey')), true)
})

test('evaluateDsl: empty AST is permissive (matches everything)', () => {
  assert.equal(evaluateDsl(samplePaper, parseDsl('')), true)
})

// ---------------------------------------------------------------------------
// parseDslToRows — text DSL → Advanced Search builder rows
// ---------------------------------------------------------------------------

const { parseDslToRows, buildDsl } = mod

/** Round-trip helper: rows → DSL text → rows again must be faithful. */
const roundTrip = rows => {
  const dsl = buildDsl(rows)
  const back = parseDslToRows(dsl)
  return { dsl, back }
}

test('parseDslToRows: empty / whitespace input → no rows, supported', () => {
  assert.deepEqual(parseDslToRows(''), { rows: [], supported: true })
  assert.deepEqual(parseDslToRows('   '), { rows: [], supported: true })
})

test('parseDslToRows: buildDsl → parseDslToRows round-trips multi-field rows', () => {
  const rows = [
    { field: 'topic', value: 'federated learning' },
    { field: 'author', value: 'Yang Liu', op: 'AND' },
    { field: 'conf', value: 'ICLR,NeurIPS', op: 'AND' },
    { field: 'year', value: '2024-2026', op: 'AND' }
  ]
  const { dsl, back } = roundTrip(rows)
  assert.equal(back.supported, true)
  assert.deepEqual(
    back.rows.map(r => ({ field: r.field, value: r.value, op: r.op })),
    rows.map((r, i) => ({ ...r, op: i === 0 ? 'AND' : r.op }))
  )
  // And the re-imported rows must compile to the same text again.
  assert.equal(buildDsl(back.rows), dsl)
})

test('parseDslToRows: OR and NOT joins survive the round-trip', () => {
  const rows = [
    { field: 'topic', value: 'diffusion' },
    { field: 'topic', value: 'flow matching', op: 'OR' },
    { field: 'abstract', value: 'survey', op: 'NOT' }
  ]
  const { dsl, back } = roundTrip(rows)
  assert.equal(back.supported, true)
  assert.equal(buildDsl(back.rows), dsl)
  // Semantics must be identical, not just the text: both ASTs evaluate the
  // same way on a probe paper.
  const paper = {
    title: 'Diffusion Models',
    abstract: 'A survey of flows.',
    authors: [],
    conf: 'ICLR',
    year: 2025
  }
  assert.equal(
    evaluateDsl(paper, parseDsl(dsl)),
    evaluateDsl(paper, parseDsl(buildDsl(back.rows)))
  )
})

test('parseDslToRows: precedence-legal mixed AND/OR stays supported', () => {
  // `a AND b OR c` parses as `(a AND b) OR c`; the rebuilt text from rows
  // parses back to the very same tree, so this IS faithfully representable.
  const dsl = 'TS=diffusion AND TI=score OR AU="Yang Liu"'
  const back = parseDslToRows(dsl)
  assert.equal(back.supported, true)
  assert.equal(buildDsl(back.rows), dsl)
})

test('parseDslToRows: parenthesised OR inside AND → degrade keeps original text', () => {
  const dsl = 'TS=(federated OR privacy) AND PY=2020-2026'
  const back = parseDslToRows(dsl)
  assert.equal(back.supported, false)
  // Degrade path: a single topic row holding the original expression; the
  // rebuilt DSL must preserve the original semantics exactly.
  assert.equal(back.rows.length, 1)
  assert.equal(back.rows[0].field, 'topic')
  const paper = {
    title: 'Federated Learning',
    abstract: '',
    authors: [],
    conf: 'ICLR',
    year: 2023
  }
  assert.equal(
    evaluateDsl(paper, parseDsl(buildDsl(back.rows))),
    evaluateDsl(paper, parseDsl(dsl))
  )
})

test('parseDslToRows: NEAR degrades but preserves semantics', () => {
  const dsl = 'privacy NEAR/5 utility'
  const back = parseDslToRows(dsl)
  assert.equal(back.supported, false)
  assert.equal(back.rows.length, 1)
  assert.equal(evaluateDsl(samplePaper, parseDsl(buildDsl(back.rows))), true)
})

test('parseDslToRows: leading NOT degrades instead of silently dropping it', () => {
  // The first row's op is ignored by buildDsl, so claiming support here
  // would turn `NOT survey` into `survey` on rebuild.
  const back = parseDslToRows('NOT TS=survey')
  assert.equal(back.supported, false)
  const paper = {
    title: 'A survey',
    abstract: '',
    authors: [],
    conf: '',
    year: 2024
  }
  assert.equal(evaluateDsl(paper, parseDsl(buildDsl(back.rows))), false)
})

test('parseDslToRows: operator-free degrade (-a b) still preserves NOT semantics', () => {
  // `-a b` parses as and{not{a}, b}; the leading NOT has no row form, so it
  // degrades. The rebuilt DSL must keep matching "b-but-not-a", not become
  // the phrase substring "-a b".
  const back = parseDslToRows('-a b')
  assert.equal(back.supported, false)
  const rebuilt = buildDsl(back.rows)
  const onlyB = {
    title: 'has b here',
    abstract: '',
    authors: [],
    conf: '',
    year: 2024
  }
  const both = {
    title: 'has a and b',
    abstract: '',
    authors: [],
    conf: '',
    year: 2024
  }
  assert.equal(
    evaluateDsl(onlyB, parseDsl(rebuilt)),
    evaluateDsl(onlyB, parseDsl('-a b'))
  )
  assert.equal(
    evaluateDsl(both, parseDsl(rebuilt)),
    evaluateDsl(both, parseDsl('-a b'))
  )
  assert.equal(evaluateDsl(both, parseDsl(rebuilt)), false)
})

test('parseDslToRows: AK list folds to topic rows', () => {
  const back = parseDslToRows('AK=transformer,diffusion')
  assert.equal(back.supported, true)
  assert.equal(back.rows[0].field, 'topic')
  assert.equal(back.rows[0].value, 'transformer,diffusion')
})

test('parseDslToRows: AK folds back to topic (builder has no keywords field)', () => {
  const back = parseDslToRows('AK=transformer')
  assert.equal(back.supported, true)
  assert.equal(back.rows[0].field, 'topic')
  assert.equal(back.rows[0].value, 'transformer')
})

test('parseDslToRows: CJK fullwidth input imports cleanly', () => {
  const back = parseDslToRows('AU＝"Yang Liu" AND PY＝2024')
  assert.equal(back.supported, true)
  assert.equal(back.rows[0].field, 'author')
  assert.equal(back.rows[0].value, 'Yang Liu')
  assert.equal(back.rows[1].field, 'year')
  assert.equal(back.rows[1].value, '2024')
})

// ---------------------------------------------------------------------------
// dslToCoarseParams — DSL → coarse backend filter params (result counts).
// The backend treats ``q`` as AND-ed substrings and never parses the DSL,
// so raw DSL text must never be forwarded as ``q``.
// ---------------------------------------------------------------------------

const { dslToCoarseParams } = mod

test('dslToCoarseParams: hoists topic/year instead of forwarding raw DSL as q', () => {
  // Regression guard for the saved-query count bug: raw DSL
  // (``TS=federated AND PY=2024-2026``) as ``q`` would AND the literal
  // tokens ``ts=federated``/``and``/``py=2024-2026`` and always return 0.
  const p = dslToCoarseParams('TS=federated AND PY=2024-2026')
  assert.equal(p.q, 'federated')
  assert.equal(p.since, 2024)
  assert.equal(p.until, 2026)
})

test('dslToCoarseParams: AU-only query sends author and NO q', () => {
  const p = dslToCoarseParams('AU="Xiaowen Jiang"')
  assert.equal(p.q, undefined)
  assert.equal(p.author, 'Xiaowen Jiang')
})

test('dslToCoarseParams: SO list and author hoist together', () => {
  const p = dslToCoarseParams('AU="Yang Liu" SO=ICLR,NeurIPS PY=2024-2026')
  assert.equal(p.q, undefined)
  assert.equal(p.author, 'Yang Liu')
  assert.deepEqual(p.conf, ['ICLR', 'NeurIPS'])
  assert.equal(p.since, 2024)
  assert.equal(p.until, 2026)
})

test('dslToCoarseParams: top-level OR falls back to a cleaned coarse q', () => {
  // Nothing is hoisted for an OR tree; without a coarse q the backend would
  // scan the whole corpus and return ~621k as the "count".
  const p = dslToCoarseParams('TS=federated OR TS="transfer learning"')
  assert.equal(p.q, 'federated transfer learning')
  assert.equal(p.author, undefined)
  assert.equal(p.conf, undefined)
})

test('dslToCoarseParams: NOT-excluded terms never enter the coarse q', () => {
  // and{or{a,b}, not{survey}} — "survey" must not be AND-ed into q.
  const p = dslToCoarseParams('TS=(a OR b) NOT survey')
  assert.equal(p.q, 'a b')
})

test('dslToCoarseParams: non-text field values stay out of the coarse q', () => {
  // Author names / venue acronyms / years virtually never appear in
  // title/abstract text, so including them would zero out the count.
  const p = dslToCoarseParams('AU="Yang Liu" OR SO=ICLR')
  assert.equal(p.q, undefined)
  assert.equal(p.author, undefined)
  assert.equal(p.conf, undefined)
})

test('dslToCoarseParams: empty input → no params', () => {
  assert.deepEqual(dslToCoarseParams(''), {})
  assert.deepEqual(dslToCoarseParams('   '), {})
})

// ---------------------------------------------------------------------------
// dslToCountPlan — count-oriented query planning (top-level OR → per-leg)
// ---------------------------------------------------------------------------

const { dslToCountPlan } = mod

test('dslToCountPlan: empty input → none', () => {
  assert.deepEqual(dslToCountPlan(''), { kind: 'none' })
})

test('dslToCountPlan: AND query stays a single hoisted param set', () => {
  const plan = dslToCountPlan('TS=federated AND PY=2024-2026')
  assert.equal(plan.kind, 'single')
  assert.deepEqual(plan.params, { q: 'federated', since: 2024, until: 2026 })
})

test('dslToCountPlan: top-level OR of text terms splits into per-leg queries', () => {
  // Regression guard for the review finding: AND-ing all OR terms into one
  // q ("federated transfer learning") is a lower bound that collapses to 0
  // for synonym merges — legs must be counted separately.
  const plan = dslToCountPlan('TS=federated OR TS="transfer learning"')
  assert.equal(plan.kind, 'or')
  assert.deepEqual(plan.legs, [{ q: 'federated' }, { q: 'transfer learning' }])
})

test('dslToCountPlan: field-qualified OR legs hoist their own params', () => {
  const plan = dslToCountPlan('AU="Yang Liu" OR TS=llm OR SO=ICLR')
  assert.equal(plan.kind, 'or')
  assert.deepEqual(plan.legs, [
    { author: 'Yang Liu' },
    { q: 'llm' },
    { conf: ['ICLR'] }
  ])
})

test('dslToCountPlan: OR legs that narrow nothing are dropped', () => {
  // The bare NOT leg contributes no params; with only one usable leg left,
  // the plan falls back to a single coarse query for the whole expression
  // (whose collectTextTerms skips the NOT subtree).
  const plan = dslToCountPlan('NOT survey OR TS=a')
  assert.equal(plan.kind, 'single')
  assert.deepEqual(plan.params, { q: 'a' })
})
