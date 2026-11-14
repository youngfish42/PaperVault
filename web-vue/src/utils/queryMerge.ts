/**
 * Query OR-merge helpers for P3 AI suggestions.
 *
 * The user can pick multiple AI-suggested keywords and have them appended
 * to the current search box with OR semantics. That single transformation
 * is shared by:
 *
 *   - the hero "AI search" dialog
 *   - the right-sidebar suggestion panel (post-search)
 *
 * Keeping it as a pure utility (no Vue / no DOM) means the same logic is
 * trivially testable in isolation and the components above just emit the
 * raw picked strings; the caller decides when / where to apply the merge.
 */

/**
 * Wrap a keyword in double quotes if it isn't already a phrase / field tag
 * / pure ASCII safe word. The Web of Science-style DSL splitter inside the
 * search backend will chop unquoted multi-word strings into AND tokens,
 * which silently destroys the relevance of any keyword containing spaces,
 * punctuation, or CJK. Quoting defensively around the merge keeps the
 * LLM-suggested phrase intact without losing readability for plain words
 * like "federated" or "privacy".
 */
export const quoteIfNeeded = (s: string): string => {
  const t = s.trim()
  if (!t) return ''
  // Already a quoted phrase.
  if (/^".+"$/.test(t)) return t
  // Already a structured field tag (TS=, AU=, ...).
  if (/^(TS|TI|AB|AU|SO|PY|AK)=/i.test(t)) return t
  // Pure ASCII safe words: no quoting needed.
  if (/^[A-Za-z0-9_.-]+$/.test(t)) return t
  // Anything else (spaces, CJK, embedded punctuation) gets wrapped, with
  // any inner double-quote escaped to keep the parse valid.
  return `"${t.replace(/"/g, '\\"')}"`
}

/**
 * Append ``picked`` keywords to ``current`` with OR semantics:
 *
 *   current=""               → "(k1 OR k2 OR ...)"
 *   current="x"              → "x OR (k1 OR k2 OR ...)"
 *   current='k1' (length=1) → "k1 OR k2"      (no parens, preserves OR precedence)
 *
 * The decision to drop the wrapping parens for a single picked keyword is
 * deliberate: the existing query might already contain field qualifiers
 * like ``AU="X"`` whose top-level ANDing is unaffected by a single right-
 * hand OR, but a single bare word in parens would be visually noisy.
 *
 * ``cap`` (optional, default ``Infinity``) limits how many picked keywords
 * actually get merged. Excess picks are silently truncated to the first N.
 * The dialog uses a cap of 3 to defend against an LLM returning a long
 * tail of loosely related keywords — each OR'd keyword widens the result
 * set multiplicatively, and a single broad keyword like "offline
 * reinforcement learning" alone can already saturate MAX_FETCH.
 */
export const buildOrMerge = (
  current: string,
  picked: string[],
  cap: number = Infinity
): string => {
  const cleaned = picked.map(quoteIfNeeded).filter(Boolean)
  // ``cap`` semantics:
  //   Infinity (default) / negative / NaN → no truncation (keep all)
  //   0                                   → truncate to empty (zero picks)
  //   n > 0 (finite)                      → keep first n picks
  // Number.isFinite() is the cheapest way to filter out Infinity and NaN
  // without accidentally treating 0 as "no cap".
  const trimmed =
    Number.isFinite(cap) && cap >= 0 ? cleaned.slice(0, cap) : cleaned
  if (!trimmed.length) return current.trim()
  const addition =
    trimmed.length === 1 ? trimmed[0] : `(${trimmed.join(' OR ')})`
  const base = current.trim()
  return base ? `${base} OR ${addition}` : addition
}
