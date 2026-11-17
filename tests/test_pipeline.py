from src.pipeline import canonical_link, item_id, pending_classification, digest_text


def test_canonical_link_removes_fragment():
    assert canonical_link("https://example.org/path#part") == "https://example.org/path"


def test_non_http_link_rejected():
    assert canonical_link("javascript:alert(1)") == ""


def test_id_deterministic():
    assert item_id("https://example.org") == item_id("https://example.org")


def test_pending_is_honest():
    c = pending_classification()
    assert c["review_required"] is True
    assert c["urgency"] == "da_verificare"


def test_digest_contains_original_source():
    item = {"title": "Test", "source": "EDPB", "link": "https://edpb.europa.eu/x", "classification": pending_classification()}
    assert item["link"] in digest_text([item])
