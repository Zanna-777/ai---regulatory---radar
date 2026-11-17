from src.pipeline import canonical_link, item_id, identify_acts, identify_areas, is_relevant, digest_text


def test_canonical_link_removes_fragment():
    assert canonical_link("https://example.org/path#part") == "https://example.org/path"


def test_non_http_link_rejected():
    assert canonical_link("javascript:alert(1)") == ""


def test_id_deterministic():
    assert item_id("https://example.org") == item_id("https://example.org")


def test_keyword_topics():
    assert identify_acts({"title": "AI Act and GDPR update", "summary": ""}) == ["AI Act", "GDPR"]
    assert identify_acts({"title": "General announcement", "summary": ""}) == []


def test_two_areas_and_legislative_proposals():
    item = {"title": "AI Act and GDPR update", "summary": ""}
    assert identify_areas(item) == ["Intelligenza artificiale", "Altre normative digitali"]
    assert identify_areas({"title": "Digital Markets Act proposal", "summary": ""}) == ["Altre normative digitali"]
    assert is_relevant("Cybersecurity and NIS2 rules")
    assert is_relevant("EU digital legislative proposal on platform rules")
    assert not is_relevant("Inviti ai partecipanti del convegno")


def test_digest_contains_original_source():
    item = {"title": "Test", "source": "EDPB", "link": "https://edpb.europa.eu/x", "acts": [], "published": "2026-11-17"}
    assert item["link"] in digest_text([item])
