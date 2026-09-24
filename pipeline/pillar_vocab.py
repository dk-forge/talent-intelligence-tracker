"""The two thin pillars and three thin event types, as ONE vocabulary.

WHY THIS EXISTS (coverage audit 2026-09-24)
-------------------------------------------
The Google News packs in `source_registry.GOOGLE_NEWS_VOCAB` carry three
intents per language: leadership, hiring/office, funding. Two of the tracker's
four public pillars -- Rewards & compensation and How we work -- had no query
term in ANY non-English language, and the English anchor had one line each.
Over the 30 days to 2026-09-22 the database held 2 `rto_mandate` rows and
~180 M&A rows against 2,739 leadership rows. A pillar nobody searches for is
a pillar that looks like a quiet news month.

WHY ONE MODULE FEEDS BOTH THE QUERY AND THE GATE
------------------------------------------------
A query term the free prefilter does not recognise is a query that pays for
the RSS fetch and then throws every result away before the classifier: the
2026-07-27 funding incident (78 of 96 filtered items were funding stories) was
exactly that. So `source_registry.pillar_queries` builds the Google News OR
groups from these phrases and `pipeline.prefilter` compiles the SAME phrases
into its gate. They cannot drift apart because there is only one list, and
`tests/test_pillar_vocabulary.py` asserts every phrase survives the gate.

THE SHAPE
---------
    PILLAR_VOCAB[lang][intent] -> tuple of phrases

Intents (every language carries all five, pinned by a test):

    work_mode      return to office, hybrid, office mandate, four-day week
    pay_benefits   pay rise / freeze, wage agreement, bonus and benefit cuts
    mna            acquires, takeover, merger
    hiring_freeze  hiring freeze / pause
    expansion      new headquarters, new plant, R&D centre, investment in a site

Discipline carried over from the packs: phrases a sub-editor writes, never a
bare high-frequency token (the Czech `investice` lesson). Where a phrase is a
single word ("Übernahme", "買収") it is one that means the event itself.

No phrase may contain an ASCII double quote: it would terminate the query's
phrase quoting (the Hebrew gershayim trap, source_registry 2026-07-29).
"""

from __future__ import annotations

INTENTS = ("work_mode", "pay_benefits", "mna", "hiring_freeze", "expansion")

PILLAR_VOCAB: dict[str, dict[str, tuple[str, ...]]] = {
    "en": {
        "work_mode": ("return to office", "office mandate", "days a week in the office",
                      "hybrid work policy", "four-day week", "remote work policy"),
        "pay_benefits": ("pay rise", "pay freeze", "salary increase", "wage agreement",
                         "pay deal", "bonus cut", "benefits cut", "pension changes"),
        "mna": ("to acquire", "agrees to buy", "takeover bid", "merger agreement",
                "completes acquisition"),
        "hiring_freeze": ("hiring freeze", "pauses hiring", "freezes hiring"),
        "expansion": ("new headquarters", "new campus", "R&D centre", "R&D center",
                      "innovation hub", "new plant"),
    },
    "de": {
        "work_mode": ("Rückkehr ins Büro", "Präsenzpflicht", "Homeoffice-Regelung",
                      "Vier-Tage-Woche", "hybrides Arbeiten"),
        "pay_benefits": ("Lohnerhöhung", "Gehaltserhöhung", "Tarifabschluss",
                         "Nullrunde", "Bonus gestrichen", "Betriebsrente"),
        "mna": ("Übernahme", "übernimmt", "Fusion mit", "Kaufangebot"),
        "hiring_freeze": ("Einstellungsstopp", "stoppt Neueinstellungen"),
        "expansion": ("neue Zentrale", "neues Werk", "Forschungszentrum", "Ansiedlung"),
    },
    "fr": {
        "work_mode": ("retour au bureau", "fin du télétravail", "accord de télétravail",
                      "semaine de quatre jours", "travail hybride"),
        "pay_benefits": ("augmentation de salaire", "augmentations salariales",
                         "gel des salaires", "NAO salaires", "prime supprimée", "mutuelle d'entreprise"),
        "mna": ("rachète", "rachat de", "fusion avec", "acquisition de", "OPA sur"),
        "hiring_freeze": ("gel des embauches", "suspend les recrutements"),
        "expansion": ("nouveau siège", "nouvelle usine", "centre de R&D", "implantation"),
    },
    "es": {
        "work_mode": ("vuelta a la oficina", "regreso a la oficina", "fin del teletrabajo",
                      "semana de cuatro días", "trabajo híbrido"),
        "pay_benefits": ("subida salarial", "aumento salarial", "congelación salarial",
                         "convenio colectivo", "recorte de bonus"),
        "mna": ("adquiere", "adquisición de", "fusión con", "opa sobre"),
        "hiring_freeze": ("congelación de contrataciones", "congela las contrataciones"),
        "expansion": ("nueva sede", "nueva planta", "centro de I+D", "centro de innovación"),
    },
    "pt": {
        "work_mode": ("volta ao escritório", "retorno ao escritório", "fim do home office",
                      "semana de quatro dias", "trabalho híbrido"),
        "pay_benefits": ("reajuste salarial", "aumento salarial", "congelamento salarial",
                         "acordo coletivo", "corte de bônus"),
        "mna": ("adquire", "aquisição de", "fusão com"),
        "hiring_freeze": ("congela contratações", "suspende contratações"),
        "expansion": ("nova sede", "nova fábrica", "centro de P&D", "centro de inovação"),
    },
    "it": {
        "work_mode": ("rientro in ufficio", "fine dello smart working", "settimana corta",
                      "lavoro ibrido"),
        "pay_benefits": ("aumento di stipendio", "aumenti salariali", "rinnovo contrattuale",
                         "blocco degli stipendi", "premio di produzione"),
        "mna": ("acquisisce", "acquisizione di", "fusione con", "opa su"),
        "hiring_freeze": ("blocco delle assunzioni", "stop alle assunzioni"),
        "expansion": ("nuova sede", "nuovo stabilimento", "centro di ricerca",
                      "polo di innovazione"),
    },
    "nl": {
        "work_mode": ("terug naar kantoor", "thuiswerkbeleid", "vierdaagse werkweek",
                      "hybride werken"),
        "pay_benefits": ("loonsverhoging", "cao-akkoord", "loonbevriezing", "bonus geschrapt"),
        "mna": ("neemt over", "overname van", "fusie met"),
        "hiring_freeze": ("vacaturestop", "personeelsstop"),
        "expansion": ("nieuw hoofdkantoor", "nieuwe fabriek", "innovatiecentrum"),
    },
    "pl": {
        "work_mode": ("powrót do biura", "praca hybrydowa", "czterodniowy tydzień pracy"),
        "pay_benefits": ("podwyżki płac", "podwyżka wynagrodzeń", "zamrożenie płac",
                         "układ zbiorowy"),
        "mna": ("przejmuje", "przejęcie spółki", "fuzja z"),
        "hiring_freeze": ("wstrzymanie rekrutacji", "zamrożenie zatrudnienia"),
        "expansion": ("nowa siedziba", "nowa fabryka", "centrum badawczo-rozwojowe"),
    },
    "sv": {
        "work_mode": ("tillbaka till kontoret", "distansarbete", "fyradagarsvecka",
                      "hybridarbete"),
        "pay_benefits": ("löneökning", "löneavtal", "lönefrysning"),
        "mna": ("förvärvar", "köper upp", "uppköpserbjudande", "går samman med"),
        "hiring_freeze": ("anställningsstopp",),
        "expansion": ("nytt huvudkontor", "ny fabrik", "utvecklingscenter"),
    },
    "tr": {
        "work_mode": ("ofise dönüş", "hibrit çalışma", "uzaktan çalışma politikası",
                      "dört günlük çalışma"),
        "pay_benefits": ("maaş zammı", "ücret artışı", "toplu sözleşme"),
        "mna": ("satın aldı", "satın alacak", "birleşme"),
        "hiring_freeze": ("işe alım dondurma", "işe alımları durdurdu"),
        "expansion": ("yeni genel merkez", "yeni fabrika", "Ar-Ge merkezi"),
    },
    "id": {
        "work_mode": ("kembali ke kantor", "kerja hibrida", "work from office"),
        "pay_benefits": ("kenaikan gaji", "kenaikan upah", "pembekuan gaji", "bonus karyawan"),
        "mna": ("mengakuisisi", "akuisisi", "merger"),
        "hiring_freeze": ("membekukan rekrutmen", "menghentikan rekrutmen"),
        "expansion": ("kantor pusat baru", "pabrik baru", "pusat riset"),
    },
    "vi": {
        "work_mode": ("quay lại văn phòng", "làm việc từ xa", "làm việc kết hợp"),
        "pay_benefits": ("tăng lương", "đóng băng lương", "thưởng Tết"),
        "mna": ("mua lại công ty", "thâu tóm", "sáp nhập"),
        "hiring_freeze": ("ngừng tuyển dụng", "đóng băng tuyển dụng"),
        "expansion": ("trụ sở mới", "nhà máy mới", "trung tâm R&D"),
    },
    "ja": {
        "work_mode": ("出社回帰", "出社義務", "週4日勤務", "リモートワーク廃止"),
        "pay_benefits": ("賃上げ", "ベースアップ", "賃金凍結", "賞与削減"),
        "mna": ("買収", "経営統合", "TOB"),
        "hiring_freeze": ("採用凍結", "採用を停止"),
        "expansion": ("新本社", "新工場", "研究開発拠点"),
    },
    "ko": {
        "work_mode": ("사무실 복귀", "재택근무 폐지", "주4일제", "하이브리드 근무"),
        "pay_benefits": ("임금 인상", "임금 동결", "연봉 인상", "성과급 삭감", "임금협상"),
        "mna": ("인수합병", "인수 추진", "합병 계약"),
        "hiring_freeze": ("채용 동결", "채용 중단"),
        "expansion": ("신사옥", "신규 공장", "연구개발센터"),
    },
    "ar": {
        "work_mode": ("العودة إلى المكتب", "العمل عن بعد", "العمل الهجين"),
        "pay_benefits": ("زيادة الرواتب", "تجميد الرواتب", "زيادة الأجور"),
        "mna": ("استحواذ", "تستحوذ على", "اندماج"),
        "hiring_freeze": ("تجميد التوظيف", "وقف التوظيف"),
        "expansion": ("مقر جديد", "مصنع جديد", "مركز أبحاث"),
    },
    "he": {
        "work_mode": ("חזרה למשרד", "עבודה היברידית", "עבודה מהבית"),
        "pay_benefits": ("העלאת שכר", "הקפאת שכר", "הסכם קיבוצי"),
        "mna": ("רוכשת את", "השלימה את רכישת", "הסכם מיזוג"),
        "hiring_freeze": ("הקפאת גיוס", "הקפאת גיוסים"),
        "expansion": ("מטה חדש", "מפעל חדש", "מרכז מחקר ופיתוח"),
    },
}


def all_phrases() -> tuple[str, ...]:
    """Every phrase in every language, de-duplicated, for the free gate."""
    seen: list[str] = []
    for intents in PILLAR_VOCAB.values():
        for phrases in intents.values():
            for p in phrases:
                if p not in seen:
                    seen.append(p)
    return tuple(seen)
