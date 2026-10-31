/**
 * Field / category metadata mirrored from `maintain.py` (CATEGORY_MAP /
 * CATEGORY_MAP_EN). Kept in sync manually because the backend does not yet
 * expose this mapping over the API.
 *
 * The venue acronym carried by each paper (`PaperItem.conf`) is uppercase
 * and does NOT include the year (see `papervault/services/papers.py`
 * `_TRAILING_YEAR_RE`). We therefore key everything by uppercase acronyms.
 */

export type Lang = 'zh' | 'en'

interface FieldDef {
  zh: string
  en: string
  venues: string[]
}

const FIELD_DEFS: FieldDef[] = [
  {
    zh: '计算机体系结构/高性能计算/存储系统',
    en: 'Computer Architecture / HPC / Storage',
    venues: [
      'TOCS',
      'TOS',
      'TCAD',
      'TC',
      'TPDS',
      'TACO',
      'PPOPP',
      'FAST',
      'DAC',
      'HPCA',
      'MICRO',
      'SC',
      'ASPLOS',
      'ISCA',
      'ATC',
      'EUROSYS',
      'HPDC'
    ]
  },
  {
    zh: '计算机网络',
    en: 'Computer Networks',
    venues: ['JSAC', 'TMC', 'TON', 'SIGCOMM', 'MOBICOM', 'INFOCOM', 'NSDI']
  },
  {
    zh: '网络与信息安全',
    en: 'Network & Information Security',
    venues: [
      'TDSC',
      'TIFS',
      'JOC',
      'CCS',
      'EUROCRYPT',
      'SP',
      'CRYPTO',
      'USS',
      'NDSS'
    ]
  },
  {
    zh: '软件工程/系统软件/程序设计语言',
    en: 'Software Engineering / Systems / PL',
    venues: [
      'TOPLAS',
      'TOSEM',
      'TSE',
      'TSC',
      'PLDI',
      'POPL',
      'FSE',
      'SOSP',
      'OOPSLA',
      'ASE',
      'ICSE',
      'ISSTA',
      'OSDI',
      'FM'
    ]
  },
  {
    zh: '数据库/数据挖掘/内容检索',
    en: 'Database / Data Mining / IR',
    venues: [
      'TODS',
      'TOIS',
      'TKDE',
      'VLDBJ',
      'SIGMOD',
      'KDD',
      'ICDE',
      'SIGIR',
      'VLDB',
      'CIKM',
      'WSDM',
      'WWW',
      'ECIR',
      'ICDM',
      'RECSYS'
    ]
  },
  {
    zh: '计算机科学理论',
    en: 'Theoretical Computer Science',
    venues: [
      'TIT',
      'IANDC',
      'SICOMP',
      'STOC',
      'SODA',
      'CAV',
      'FOCS',
      'LICS',
      'COLT',
      'ALT'
    ]
  },
  {
    zh: '计算机图形学与多媒体',
    en: 'Computer Graphics & Multimedia',
    venues: [
      'TOG',
      'TIP',
      'TVCG',
      'TMM',
      'MM',
      'SIGGRAPH',
      'VR',
      'IEEEVIS',
      'BMVC',
      'MICCAI',
      'ICME'
    ]
  },
  {
    zh: '人工智能',
    en: 'Artificial Intelligence',
    venues: [
      'AI',
      'TPAMI',
      'IJCV',
      'JMLR',
      'AAAI',
      'NIPS',
      'ACL',
      'CVPR',
      'ICCV',
      'ICML',
      'ICLR',
      'AISTATS',
      'UAI',
      'TNNLS',
      'MLJ',
      'IJCAI',
      'COLING',
      'EACL',
      'EMNLP',
      'NAACL',
      'ECCV',
      'WACV',
      'MLSYS'
    ]
  },
  {
    zh: '人机交互与普适计算',
    en: 'Human-Computer Interaction & Ubicomp',
    venues: ['TOCHI', 'IJHCS', 'CSCW', 'CHI', 'UBICOMP', 'UIST']
  },
  {
    zh: '语音',
    en: 'Speech',
    venues: ['ICASSP', 'INTERSPEECH', 'TASLP']
  },
  {
    zh: '交叉/综合/新兴',
    en: 'Interdisciplinary / Comprehensive / Emerging',
    venues: ['JACM', 'PROCIEEE', 'SCIS', 'BIOINFORMATICS', 'RTSS', 'ISWC']
  }
]

/**
 * Normalise a venue acronym to the form used in `FIELD_DEFS`:
 *   - upper-case
 *   - strip trailing year (e.g. "CVPR2024" -> "CVPR")
 *   - resolve common aliases (NEURIPS -> NIPS)
 */
export const normalizeConf = (raw: string | null | undefined): string => {
  if (!raw) return ''
  let s = String(raw).trim().toUpperCase()
  s = s.replace(/\s+/g, '')
  s = s.replace(/\d{4,}$/, '')
  if (s === 'NEURIPS') s = 'NIPS'
  return s
}

export interface FieldOption {
  /** Stable key used in v-model / filtering (always the zh label) */
  key: string
  zh: string
  en: string
  venues: string[]
}

export const FIELDS: FieldOption[] = FIELD_DEFS.map(f => ({
  key: f.zh,
  zh: f.zh,
  en: f.en,
  venues: f.venues
}))

const VENUE_TO_FIELD_KEY = new Map<string, string>()
for (const f of FIELDS) {
  for (const v of f.venues) {
    VENUE_TO_FIELD_KEY.set(v.toUpperCase(), f.key)
  }
}

export const getFieldKeyForConf = (conf: string | null | undefined): string => {
  const norm = normalizeConf(conf)
  return VENUE_TO_FIELD_KEY.get(norm) ?? ''
}

export const labelOfField = (key: string, lang: Lang): string => {
  const f = FIELDS.find(x => x.key === key)
  if (!f) return key
  return lang === 'en' ? f.en : f.zh
}

/** Reserved key used in counts to represent venues outside the static map. */
export const OTHER_FIELD_KEY = '__other__'

export const labelOfOther = (lang: Lang): string =>
  lang === 'en' ? 'Other' : '其他'
