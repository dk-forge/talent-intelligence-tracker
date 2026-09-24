"""'$' in a peso or real country is the local currency, not a US dollar.

Grupo Éxito (Colombia): '$292.000 millones' was stored as 292 BILLION US
dollars. It is 292,000 million Colombian PESOS (~US$70m). Colombian, Mexican,
Chilean, Argentine and other Latin American press write their own peso with a
bare '$', and Brazilian press writes the real as 'R$' (already vetoed) but also
bare '$' in some outlets. A bare '$' beside a Spanish/Portuguese scale word
("millones", "milhões") on a row placed in a non-USD country is refused.
"""

import pytest

from pipeline import vocab


@pytest.mark.parametrize("text,country", [
    ("$292.000 millones", "CO"),
    ("$1.500 millones", "MX"),
    ("$ 40.000 millones", "CL"),
    ("$3.000 milhões", "BR"),
    ("$10 mil millones", "AR"),
])
def test_bare_dollar_with_iberian_scale_in_peso_country_is_refused(text, country):
    assert vocab.funding_usd_for_country(text, country) is None


@pytest.mark.parametrize("text,country,expected", [
    ("US$ 51.000 millones", "AR", 51_000_000_000),   # stated US dollar: kept
    ("USD 20 millones", "CO", 20_000_000),
    ("$20 millones de dólares", "MX", 20_000_000),    # dollar written out
    ("$292.000 millones", "US", 292_000_000_000),     # US row: '$' is USD
    ("$292.000 millones", "EC", 292_000_000_000),     # Ecuador is dollarised
    ("$50 million", "CO", 50_000_000),
    ("$30 millones", "CR", 30_000_000),               # Costa Rica: colón is ₡                # English scale: unchanged
    ("$292.000 millones", None, 292_000_000_000),     # unplaced: unchanged
])
def test_what_the_guard_leaves_alone(text, country, expected):
    assert vocab.funding_usd_for_country(text, country) == expected


def test_the_correction_script_clears_the_grupo_exito_row():
    import correct_funding_amount as c
    row = {"funding_amount": "$292.000 millones", "country": "CO",
           "funding_amount_usd": 292_000_000_000}
    assert c.rederivation(row) == (292_000_000_000, None)
