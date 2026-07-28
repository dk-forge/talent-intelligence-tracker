"""Deterministic gate, run before the LLM ever sees a candidate.

Free filtering beats paid filtering (spec 4 rule 3). The first live run proved
why: a query containing the bare word "expansion" returned MLB expansion, World
of Warcraft expansion, Medicaid expansion, cattle herd expansion and war
escalation. Every one of those would have cost a classification call to reject.

The rule is simple: a talent signal is about **people at an employer**. If the
text contains no employment noun at all, no model needs to look at it.
"""

from __future__ import annotations

import re

# At least one of these must appear. They are the words that make a story about
# employment rather than about stadiums, herds or health policy.
_EMPLOYMENT_TERMS = (
    r"jobs?", r"hiring", r"hire[sd]?", r"roles?", r"headcount", r"staff",
    r"employees?", r"workers?", r"workforce", r"recruit\w*", r"vacanc\w+",
    r"appoint\w*", r"names? (?:its |a |new )?(?:chief|ceo|cfo|cto|president)",
    r"steps? down", r"resign\w*", r"succeeds?", r"chief \w+ officer",
    # Structural leadership news names no individual and no headcount, but is
    # squarely the leadership pillar. Kept narrow on purpose: bare "leadership"
    # matches an endless supply of AI-strategy think pieces.
    r"leadership (?:structure|team|reshuffle|shake-?up|transition)",
    r"management team", r"executive team", r"board appoint\w*",
    r"salar\w+", r"pay(?:rise|\srise)?", r"wages?", r"bonus\w*", r"compensation",
    r"remote work", r"hybrid work\w*", r"return to office", r"four-day week",
)
# Funding, in every language we query. This block is why the gate exists at all
# in its current form: without it, "who is raising money" was dead on arrival.
#
# The failure was silent and total. A funding headline says "Enigma Raises $71M
# in Seed Funding" and contains no employment word, so the employment gate threw
# away every single one. A live dry run on 2026-07-27 fetched 140 candidates and
# filtered 96; 78 of those 96 were funding stories. One of the four pillars had
# never produced a news record and nothing reported an error, because a free
# filter rejecting everything looks exactly like a quiet news day.
#
# A raise is a hiring signal: it is the money that pays for the roles. That is
# why it is a pillar, and why the page headline says "who is raising money".
_FUNDING_TERMS = (
    r"rais(?:e[sd]?|ing)", r"series [a-e]\b", r"seed (?:funding|round)",
    r"pre-?seed", r"funding round", r"secures? (?:\$|€|£|₹|us\$)?[\d.,]+",
    r"private placement", r"venture round", r"led the round",
    # German, French, Spanish, Portuguese, Italian, Dutch
    r"finanzierungsrunde", r"eingesammelt", r"kapitalrunde",
    r"lev\w*e de fonds", r"tour de table",
    r"ronda de (?:financiaci\w*n|inversi\w*n)", r"capta\w*",
    r"rodada de (?:investimento|financiamento)",
    r"round di finanziamento", r"raccoglie",
    r"financieringsronde",
)

# The same gate in the languages Google News is queried in. Without these, a
# German or Portuguese headline fails the free filter and is dropped before it
# ever reaches the model, so querying those editions would have produced
# exactly zero records while looking like it was working.
#
# Grouped by language for review. Each group is the vocabulary of the three
# intents its queries ask about: who is leading, who is hiring, who raised.
_EMPLOYMENT_TERMS_INTL = (
    # German
    r"stellen", r"arbeitspl\w+", r"mitarbeiter\w*", r"besch\w*ftigt\w*",
    r"vorstandsvorsitzend\w+", r"gesch\w*ftsf\w*hrer\w*", r"personalchef\w*",
    r"einstell\w+", r"tritt zur\w*ck", r"geh\w*lt\w*", r"l\w*hne",
    r"finanzierungsrunde", r"eingesammelt",
    # French
    r"emplois?", r"salari\w+", r"recrut\w+", r"embauch\w+", r"effectifs?",
    r"directeur g\w*n\w*ral", r"pdg", r"d\w*mission\w*", r"salaires?",
    r"lev\w*e de fonds",
    # Spanish
    r"empleos?", r"empleados?", r"contrata\w*", r"plantilla", r"puestos?",
    r"consejero delegado", r"director general", r"dimit\w+", r"salarios?",
    r"ronda de financiaci\w*n",
    # Portuguese
    r"empregos?", r"funcion\w*rios?", r"contrat\w+", r"vagas?", r"quadro de pessoal",
    r"presidente-executivo", r"diretor-?geral", r"demiss\w+", r"sal\w*rios?",
    r"rodada de investimento",
    # Italian
    r"posti di lavoro", r"dipendenti", r"assunzion\w+", r"assumer\w+", r"organico",
    r"amministratore delegato", r"dimission\w+", r"stipend\w+",
    r"round di finanziamento",
    # Dutch
    r"banen", r"medewerkers?", r"personeel", r"aannem\w+", r"vacatures?",
    r"topman", r"bestuursvoorzitter", r"stapt op", r"salaris\w*",
    r"financieringsronde",
    # Polish
    r"prezes\w*", r"zatrudni\w*", r"miejsc pracy", r"pracownik\w+",
    r"rezygnuje", r"wynagrodzeni\w+", r"runda finansowania",
    # Swedish
    r"\bvd\b", r"anställ\w+", r"jobb", r"medarbetare", r"personal",
    r"lämnar sin post", r"finansieringsrunda",
    # Turkish. The first pass had only eight terms and let 22% of real Turkish
    # hiring headlines through; the misses were all ordinary newsroom wording
    # ("personel alımı", "kadro", "atandı") rather than anything exotic.
    r"genel müdür\w*", r"istihdam", r"işe al\w+", r"çalışan\w*",
    r"görevinden ayrıl\w+", r"maaş\w*", r"yatırım turu", r"yatırım aldı",
    r"personel alım\w*", r"personel", r"kadro\w*", r"atand[ıi]", r"atama\w*",
    r"iş ilan\w*", r"eleman alım\w*", r"memur alım\w*", r"başkan\w* seçildi",
    r"yönetim kurulu", r"ceo['’]?su", r"görevine getirildi", r"istifa\w*", r"ceo['’]s[uü]",
    r"yeni ceo", r"ceo oldu", r"ceo olarak", r"ceo atand[ıi]",
    r"ücret\w*", r"zam", r"yeni ofis", r"fabrika açt\w+", r"tesis açt\w+",
    # Indonesian / Malay
    r"direktur utama", r"merekrut", r"lowongan", r"karyawan", r"pegawai",
    r"mengundurkan diri", r"pendanaan", r"putaran pendanaan",
    # Vietnamese
    r"tổng giám đốc", r"tuyển dụng", r"nhân viên", r"việc làm",
    r"từ chức", r"gọi vốn", r"huy động vốn",
)

# CJK and Arabic have no spaces between words the way \b expects, so these are
# matched as substrings. That is safe here because each string is long enough to
# be unambiguous — the risk \b guards against ("RIF" inside "tariff") does not
# arise for 社長に就任 or تعيين رئيس تنفيذي.
_EMPLOYMENT_TERMS_CJK = (
    # Japanese
    "社長", "就任", "退任", "採用", "求人", "従業員", "人員", "新拠点",
    "資金調達", "シリーズA", "シードラウンド", "賃上げ", "給与",
    # Korean
    "대표이사", "선임", "사임", "채용", "인력", "직원", "사무소",
    "투자 유치", "시리즈 A", "시드 투자", "임금",
    # Chinese
    "首席执行官", "总裁", "任命", "辞职", "招聘", "员工", "融资", "轮融资",
    # Arabic
    "الرئيس التنفيذي", "تعيين", "استقالة", "توظيف", "وظائف", "موظف",
    "جولة تمويل", "تمويل", "رواتب",
)

_CJK = re.compile("|".join(re.escape(t) for t in _EMPLOYMENT_TERMS_CJK))

_EMPLOYMENT = re.compile(
    r"\b(?:" + "|".join(_EMPLOYMENT_TERMS + _EMPLOYMENT_TERMS_INTL + _FUNDING_TERMS) + r")\b",
    re.I | re.UNICODE,
)

# Site-establishment terms. A company opening a capability centre IS a hiring
# event, even when the headline never says "jobs" — and this is precisely the
# phrasing the standalone euphemism queries exist to surface. The first version
# of this filter dropped every one of them, which would have made those queries
# dead on arrival exactly as the sibling's did.
#
# "GCC" is deliberately absent: it is also the Gulf Cooperation Council.
_SITE_TERMS = (
    r"capability cent(?:re|er)s?", r"cent(?:re|er)s? of excellence",
    r"delivery cent(?:re|er)s?", r"shared services", r"tech(?:nology)? cent(?:re|er)s?",
    r"engineering cent(?:re|er)s?", r"r&d cent(?:re|er)s?", r"innovation cent(?:re|er)s?",
    r"development cent(?:re|er)s?", r"opens? (?:a |its |new )?(?:office|hub|campus|site)",
    r"sets? up (?:a |its |new )?(?:office|hub|centre|center)",
    r"new (?:office|hub|campus|facility|plant|site)",
    # Bare "GCC" stays out (Gulf Cooperation Council), but a verb in front of
    # it is unambiguous: in Indian business press "opens a new GCC" is a Global
    # Capability Centre, which is exactly the category we want.
    r"(?:new|opens?|open|launch(?:es|ing)?|sets? up|establish(?:es|ing)?)\s+(?:a\s+|its\s+|the\s+)?(?:new\s+)?gcc\b",
)
_SITE = re.compile(r"\b(?:" + "|".join(_SITE_TERMS) + r")\b", re.I)

# Domains where "expansion", "hiring" and "roster" mean something else entirely.
# Cheap to check, and they were most of the noise in the first live run.
_OFF_TOPIC_TERMS = (
    r"nba", r"nfl", r"mlb", r"wnba", r"nhl", r"premier league", r"playoffs?",
    r"franchise", r"roster", r"draft pick", r"touchdown", r"season opener",
    r"medicaid", r"medicare", r"nuclear weapons?", r"ceasefire", r"airstrikes?",
    r"herd", r"cattle", r"livestock", r"acreage",
    r"world of warcraft", r"dlc", r"expansion pack", r"video game",
    # Government and civil-service exam notices. These are instructions to
    # applicants ("registration closes tomorrow", "admit card released"), not
    # intelligence about an employer's plans. A live run stored UPPSC PCS and
    # Indian Navy SSC notices before this existed.
    r"recruitment 20\d\d", r"admit card", r"answer key", r"exam date",
    r"registration closes", r"apply online", r"notification (?:out|released)",
    r"\d+\s+posts?\b", r"uppsc", r"upsc", r"ssc\s+(?:cgl|chsl|gd|mts|officer)",
    r"bharti", r"sarkari", r"vacanc(?:y|ies) notification",
    r"police constable", r"assistant teacher recruitment",
    # Football uses the hiring verb in several of these languages: a live test
    # of the Indonesian edition returned "Barcelona mencapai kesepakatan untuk
    # merekrut bintang Man City" as a hiring signal.
    r"barcelona", r"real madrid", r"man city", r"manchester united",
    r"liverpool", r"chelsea", r"arsenal", r"juventus", r"bayern",
    r"sepak bola", r"liga inggris", r"transfer pemain", r"bintang",
    r"bóng đá", r"câu lạc bộ", r"cầu thủ", r"chuyển nhượng",
    r"futbol", r"transfer sezonu", r"teknik direktör",
    r"fotboll", r"allsvenskan", r"piłkarz", r"transfer\w* piłkar\w*",
    # Naming clubs is whack-a-mole and it lost: "Barca va recruter le crack
    # ghaneen de 20 ans" and "Sparta Prague va recruter le cousin d'Erling
    # Haaland" both reached the live page tagged "Hiring up" because neither
    # club was on the list above (observed 2026-07-28). A signing IS a hire in
    # the literal sense, which is exactly why the employment gate passes it, so
    # the exclusion has to name the SPORT rather than the teams. Each term below
    # is football-specific in its own language and does not collide with
    # corporate hiring vocabulary.
    r"mercato", r"transfer window", r"footballer", r"football club",
    r"on loan from", r"free transfer", r"signs for",
    r"attaquant", r"milieu de terrain", r"gardien de but",
    r"delantero", r"centrocampista", r"portero", r"fichaje",
    r"st[üu]rmer", r"torwart", r"mittelfeldspieler",
    r"midfielder", r"goalkeeper", r"winger", r"centre-back",
    r"sparta prague", r"bar[çc]a\b", r"atl[ée]tico",
    # Public-sector recruitment notices, same reasoning as the Indian ones.
    r"tuyển dụng viên chức", r"tuyển dụng công chức", r"thi tuyển",
    r"penerimaan cpns", r"\bcpns\b", r"seleksi calon pegawai",
    r"memur alımı ilanı", r"kpss", r"bakanlığı personel alım\w*",
    r"bakanlık\w* personel", r"belediyesi personel", r"kamu personel alım\w*",
    r"i̇şkur", r"başvuru ekranı", r"başvuru şartları",
)
_OFF_TOPIC = re.compile(r"\b(?:" + "|".join(_OFF_TOPIC_TERMS) + r")\b", re.I)


# --- Scope boundary: displacement belongs to the sibling --------------------
#
# The page footer promises "Layoff and redundancy data is not collected here;
# see the AI Layoff Tracker", and it was not true: a Spanish-language Verizon
# story ("Verizon despedirá a 3,000 empleados") was live on the page. Two
# products, one boundary — the sibling owns workforce REDUCTION, this one owns
# everything else about the talent market.
#
# The vocabulary lives here rather than in validate.py because BOTH gates need
# exactly the same words. This one runs free, before any model is paid; the one
# in validate.py is the backstop for a story whose headline hid the cut.
#
# 49 Google News editions means the boundary has to hold in more than English.
# A rule that only recognises "layoffs" lets every non-English cut through,
# which is precisely how the Verizon row happened.
_REDUCTION_TERMS = (
    # English
    r"lay[ -]?offs?", r"laid off", r"lays? off", r"laying off",
    r"job cuts?", r"jobs? cut", r"job losses", r"role cuts?", r"staff cuts?",
    r"cut(?:s|ting)?\s+(?:up to\s+)?(?:about\s+)?(?:some\s+)?(?:[\d,.]+\s+)?"
    r"(?:more\s+)?(?:of its\s+)?(?:jobs|roles|positions|posts|staff|workers|"
    r"employees|workforce|headcount)",
    r"redundanc(?:y|ies)", r"made redundant",
    r"reduction in force", r"workforce reduction", r"headcount reduction",
    r"staff reductions?",
    r"reduc\w+\s+(?:its\s+)?(?:workforce|headcount|staff|staffing)",
    r"downsiz\w+", r"retrench\w+",
    r"shed(?:s|ding)?\s+(?:[\d,.]+\s+)?(?:jobs|roles|staff|workers|positions)",
    r"ax(?:e[sd]?|ing)\s+(?:[\d,.]+\s+)?(?:jobs|roles|staff|posts|positions)",
    r"slash(?:es|ing|ed)?\s+(?:[\d,.]+\s+)?(?:jobs|roles|staff|its workforce)",
    r"eliminat\w+\s+(?:[\d,.]+\s+)?(?:jobs|roles|positions)",
    r"mass firings?", r"pink slips?",
    r"terminat\w+\s+(?:[\d,.]+\s+)?(?:employees|workers|staff)",
    # Spanish
    r"desped\w+", r"despido\w*",
    r"recort(?:e|es|ar|a)\w*\s+(?:de\s+)?(?:empleos?|personal|plantilla|puestos)",
    r"reducci\w*n de (?:plantilla|personal|empleo)",
    r"suprim\w+\s+(?:[\d,.]+\s+)?(?:empleos|puestos)",
    # French. Narrowed to the noun and the verb forms: bare "licenci\\w+"
    # also matches the Spanish "licencia" (a permit) and the French
    # "licencié" (a degree holder, or a registered club player).
    r"licenciement\w*", r"licencie(?:r|nt|ra|ront|z)\b",
    r"suppressions? d[e'’]\s*(?:[\d\s.,]+\s*)?(?:postes|emplois)",
    r"supprime\w*\s+(?:[\d,.\s]+)?(?:postes|emplois)",
    r"plan social", r"plan de sauvegarde de l[e'’]emploi",
    r"r\w*duction d[e'’]effectifs",
    # German
    r"stellenabbau", r"arbeitsplatzabbau", r"personalabbau",
    r"stellenstreichung\w*", r"massenentlassung\w*", r"entlassung\w*",
    r"entl\w*sst", r"entlassen", r"streicht\s+[\d.]+\s+stellen",
    r"baut\s+[\d.]+\s+stellen ab",
    # Portuguese. Bare "demissão" is NOT here: in Portuguese business copy it is
    # as often one executive resigning as it is a workforce cut, and this
    # boundary must not swallow the leadership pillar.
    r"demiss(?:\w+)? em massa", r"demit(?:e|iu|ir)\w*\s+[\d.,]+",
    r"cort(?:a|am|ar|ou|es?)?\s+(?:de\s+)?(?:[\d.,]+\s+)?"
    r"(?:vagas|empregos?|pessoal|postos|funcion\w+)",
    r"redu\w+\s+(?:o\s+)?quadro de (?:pessoal|funcion\w+)",
    r"dispensa de funcion\w+", r"enxugamento",
    # Italian
    r"licenziament\w+", r"licenzia(?:re|no|ti|ta|to)\b", r"esuberi",
    r"tagli\w*\s+(?:ai posti|di posti|del personale|occupazionali|dipendenti)",
    r"riduzione (?:del personale|dell[e'’]organico)",
    # Dutch, Polish, Swedish, Turkish — cheap to add, same failure if absent.
    r"ontslag\w*", r"banenverlies", r"schrapt\s+[\d.]+\s+banen",
    r"zwolnieni\w+", r"redukcj\w+ etat\w+",
    r"varsl\w+", r"neddragning\w*",
    r"i̇?şten çıkar\w*", r"toplu i̇?şten",
)
_REDUCTION = re.compile(r"\b(?:" + "|".join(_REDUCTION_TERMS) + r")", re.I | re.UNICODE)

# "RIF" is only a reduction in force when it is written in capitals. Lowercase
# "rif" is a syllable in several of the languages above, and the sibling's own
# filter went inert for a day because "RIF" matched inside "tariff".
_RIF = re.compile(r"\bRIFs?\b")

# The events this product DOES own. A story is not a layoff story merely
# because it mentions a cut: "Klarna hires 1,000 after AI-driven job cuts" is a
# hiring story, and rejecting it would hand the sibling a story about growth.
_IN_SCOPE_SUBJECT_TERMS = (
    # Hiring and site growth
    r"hiring", r"hires?", r"to hire", r"hired", r"recruit\w*",
    r"creat\w+\s+(?:[\d,.]+\s+)?(?:new\s+)?(?:jobs|roles|posts|positions)",
    r"new jobs", r"add(?:s|ing)?\s+(?:[\d,.]+\s+)?(?:jobs|roles|staff)",
    r"opens? (?:a |its |new )", r"new (?:office|hub|campus|plant|facility|site)",
    r"capability cent(?:re|er)",
    # Leadership
    r"appoint\w*", r"names? (?:a |its |new )?(?:ceo|chief|cfo|cto|coo|president)",
    r"steps? down", r"resign\w*", r"succeeds?", r"promot\w+",
    r"new (?:ceo|chief executive|cfo|cto)",
    # Money
    r"rais(?:e[sd]?|ing)", r"funding round", r"series [a-e]\b",
    r"secures? (?:\$|€|£|₹)?[\d.,]+", r"invest(?:s|ment|ing)\b",
    # Pay
    r"pay ris\w+", r"pay increase", r"salar\w+ (?:increase|rise)",
    r"wage (?:increase|rise)", r"bonus\w*", r"minimum wage",
    # Non-English subjects, same three intents
    r"contrata\w*", r"embauch\w+", r"einstell\w+", r"assunzion\w+",
    r"nomm\w+", r"ernennt", r"nombra\w*", r"nomea\w*", r"nomina\w*",
    r"lev\w*e de fonds", r"finanzierungsrunde", r"ronda de financiaci\w*n",
    r"rodada de investimento", r"round di finanziamento",
)
_IN_SCOPE_SUBJECT = re.compile(
    r"\b(?:" + "|".join(_IN_SCOPE_SUBJECT_TERMS) + r")", re.I | re.UNICODE
)


def workforce_reduction_term(text: str) -> str | None:
    """The term that makes `text` a story the SIBLING owns, or None.

    The honest heuristic: a headline leads with its subject. So a reduction
    term counts only when no in-scope subject (hiring, funding, an
    appointment, a pay action, a location decision) appears EARLIER in the
    text. That keeps "Klarna hires 1,000 after AI job cuts" here and sends
    "Verizon despedirá a 3,000 empleados" to the sibling, without needing to
    parse a sentence.

    It is a heuristic and it has a known blind spot: "Amid layoffs, Acme hires
    500" reads as a reduction story. That is the direction to be wrong in — a
    layoff row on a page promising it collects none is a broken promise, while
    a missed hiring row is one row.
    """
    if not text:
        return None

    hit = _REDUCTION.search(text) or _RIF.search(text)
    if not hit:
        return None

    subject = _IN_SCOPE_SUBJECT.search(text)
    if subject and subject.start() < hit.start():
        return None
    return hit.group(0)


# --- Geography gate --------------------------------------------------------
#
# We claim eight markets. A signal in a place we do not cover gets rejected by
# validate.py anyway ("no geography"), so classifying it first is pure waste —
# the first successful live run paid for exactly that on Uzbekistan, Somalia,
# Ohio and Anglesey. Checking here costs nothing.
#
# Grows automatically as source_registry.MARKETS grows: nothing to hand-edit.

def _geography_terms() -> tuple[re.Pattern, re.Pattern]:
    from . import vocab

    long_terms, short_codes = set(), set()

    def add(term: str) -> None:
        (long_terms if len(term) >= 4 else short_codes).add(re.escape(term))

    for alias, (city, _region, _iso2) in vocab._CITY_ALIASES.items():
        add(alias)
        add(city)
    for name in vocab.COUNTRY_NAMES.values():
        add(name)
    for alias in vocab._COUNTRY_ALIASES:
        add(alias)
    # The two-letter codes of the editions we actually query. Deliberately NOT
    # all 198: uppercase "IT", "IN", "AT" and "NO" are ordinary words in a
    # headline, and this helper feeds a geography hint, not a gate, so a false
    # positive costs nothing while a false "IT department" would be noise.
    for code in ("US", "GB", "CA", "AU", "IE", "IN", "SG", "NZ", "ZA", "PH",
                 "NG", "DE", "FR", "NL", "ES", "MX", "AR", "CL", "CO", "BR",
                 "PT", "AT", "CH", "BE"):
        short_codes.add(re.escape(code))

    # Adjectival forms carry the geography just as well: "across German sites".
    long_terms.update({
        "irish", "german", "french", "dutch", "belgian", "british", "english",
        "scottish", "welsh", "spanish", "portuguese", "italian", "swedish",
        "danish", "norwegian", "finnish", "swiss", "polish", "czech",
        "romanian", "indian", "japanese", "australian", "american",
    })

    return (
        re.compile(r"\b(?:" + "|".join(sorted(long_terms)) + r")\b", re.I),
        # Short codes match case-sensitively on purpose: a lowercase "us" is
        # the pronoun, and "\bus\b" would let "join us" through as the USA.
        re.compile(r"\b(?:" + "|".join(sorted(c.upper() for c in short_codes)) + r")\b"),
    )


_GEO_LONG, _GEO_SHORT = _geography_terms()


def has_covered_geography(text: str) -> bool:
    return bool(_GEO_LONG.search(text) or _GEO_SHORT.search(text))


def passes(text: str) -> tuple[bool, str]:
    """Return (keep, reason). Reason is empty when kept."""
    if not text or not text.strip():
        return False, "empty text"

    # Word-boundary matching, not substring: the sibling's equivalent loop went
    # inert for a day because "RIF" matched inside "tariff".
    if _OFF_TOPIC.search(text):
        hit = _OFF_TOPIC.search(text).group(0)
        return False, f"off-topic domain ({hit})"

    if not (_EMPLOYMENT.search(text) or _SITE.search(text) or _CJK.search(text)):
        return False, "no employment or site-opening term"

    # Checked AFTER the employment gate on purpose: a layoff story always
    # passes that gate (it is full of "jobs" and "employees"), so this is the
    # cheapest possible place to hand it to the sibling — before a model is
    # paid to read a story we are not allowed to publish.
    cut = workforce_reduction_term(text)
    if cut:
        return False, f"workforce reduction belongs to the layoff tracker ({cut})"

    # NOTE: geography is deliberately NOT a gate here, though the helper above
    # exists and is tested. Gating on it looked like an easy saving — several
    # items were classified and then rejected for uncovered geography — but it
    # drops "Revolut CEO steps down" (no place in the headline at all), "Intel
    # opens new facility in Leixlip" and "BMS opens Mumbai capability centre".
    # A headline often carries no place while the body does, and the model can
    # infer it from the employer. Recall is the harder problem; validate.py
    # rejects on geography later with full context, for a fraction of a cent.
    return True, ""
