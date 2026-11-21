/*
 * HTTP status → user-facing message, resolved through the i18n layer so
 * the toast language follows the current UI language. The status → key
 * mapping lives here; the actual strings live in ``utils/i18n.ts``
 * under ``error.http.<code>`` (fallback ``error.http.other``).
 */
export const ERROR_CODE_TYPE = (
  code: string,
  t: (key: string, vars?: Record<string, string | number>) => string
): string => {
  const known = [
    '400',
    '401',
    '403',
    '404',
    '405',
    '408',
    '500',
    '501',
    '502',
    '503',
    '504',
    '505'
  ]
  const key = known.includes(code) ? code : 'other'
  return t('error.http.format', { code, msg: t(`error.http.${key}`) })
}
