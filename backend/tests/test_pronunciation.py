"""PLS parsing, the lock file, and the upload/update flow (mock provider posing as live)."""

import json

import pytest

from relay import pronsync
from relay.pronunciation import (
    LOCK_NAME,
    PronunciationRule,
    parse_pls,
    pls_hash,
    read_locks,
)
from relay.providers.tts_mock import MockTTSProvider
from relay.sites import SITES_DIR, get_site, load_site

PLS = """<?xml version="1.0" encoding="UTF-8"?>
<lexicon version="1.0" xmlns="http://www.w3.org/2005/01/pronunciation-lexicon"
         alphabet="ipa" xml:lang="en-GB">
  <lexeme><grapheme>Holborn</grapheme><phoneme>ˈhəʊbən</phoneme></lexeme>
  <lexeme><grapheme>Marylebone</grapheme><grapheme>Marylebone Station</grapheme>
          <phoneme>ˈmɑːlɪbən</phoneme></lexeme>
  <lexeme><grapheme>ETA</grapheme><alias>estimated time of arrival</alias></lexeme>
</lexicon>
"""


def test_parse_pls_phonemes_aliases_and_multiple_graphemes():
    rules = parse_pls(PLS)
    assert rules == [
        PronunciationRule("Holborn", "ˈhəʊbən", "ipa"),
        PronunciationRule("Marylebone", "ˈmɑːlɪbən", "ipa"),
        PronunciationRule("Marylebone Station", "ˈmɑːlɪbən", "ipa"),
        PronunciationRule("ETA", alias="estimated time of arrival"),
    ]


@pytest.mark.parametrize(
    "lexeme",
    [
        "<lexeme><phoneme>x</phoneme></lexeme>",  # no grapheme
        "<lexeme><grapheme>A</grapheme></lexeme>",  # nothing to say
        "<lexeme><grapheme>A</grapheme><phoneme>x</phoneme><alias>y</alias></lexeme>",  # both
        "<lexeme><grapheme>A</grapheme><phoneme>x</phoneme></lexeme>"
        "<lexeme><grapheme>A</grapheme><phoneme>y</phoneme></lexeme>",  # duplicate
    ],
)
def test_parse_pls_rejects_bad_lexemes(lexeme):
    xml = f'<lexicon xmlns="http://www.w3.org/2005/01/pronunciation-lexicon">{lexeme}</lexicon>'
    with pytest.raises(ValueError):
        parse_pls(xml)


def test_shipped_pls_files_cover_the_hard_station_names():
    for site_id in ("tube_station", "bus_station"):
        site = get_site(site_id)
        _, rules = pronsync.load_rules(site)
        names = {r.grapheme for r in rules}
        assert {"Marylebone", "Southwark", "Holborn", "Greenwich", "Leicester Square"} <= names
        assert all(r.phoneme for r in rules)


def live_like() -> MockTTSProvider:
    tts = MockTTSProvider()
    tts.mode = "live"  # stands in for ElevenLabs; the mock keeps dictionaries in memory
    return tts


@pytest.fixture
def sites_dir(tmp_path):
    for name in ("tube_station.yaml", "tube_station.pls"):
        (tmp_path / name).write_text((SITES_DIR / name).read_text(encoding="utf-8"), "utf-8")
    return tmp_path


def test_sync_refuses_to_run_against_the_mock(sites_dir):
    with pytest.raises(RuntimeError, match="mock"):
        pronsync.sync_site(MockTTSProvider(), load_site(sites_dir / "tube_station.yaml"), sites_dir)
    assert not (sites_dir / LOCK_NAME).exists()  # a mock id must never reach the lock file


def test_first_sync_creates_then_update_changes_only_the_difference(sites_dir):
    tts = live_like()
    site = load_site(sites_dir / "tube_station.yaml")
    first = pronsync.sync_site(tts, site, sites_dir)
    assert first.action == "created" and len(first.added) == 5
    lock = read_locks(sites_dir)["tube_station"]
    assert (lock.dictionary_id, lock.version_id) == (first.dictionary_id, first.version_id)
    text = (sites_dir / "tube_station.pls").read_text(encoding="utf-8")
    assert lock.pls_sha256 == pls_hash(text)

    again = pronsync.sync_site(tts, site, sites_dir)  # nothing changed
    assert again.action == "unchanged" and again.version_id == first.version_id

    edited = text.replace("ˈsʌðək", "ˈsaʊθwɔːk").replace(
        "  <lexeme><grapheme>Holborn</grapheme><phoneme>ˈhəʊbən</phoneme></lexeme>\n", ""
    )
    (sites_dir / "tube_station.pls").write_text(edited, encoding="utf-8")
    updated = pronsync.sync_site(tts, site, sites_dir)
    assert updated.action == "updated" and updated.dictionary_id == first.dictionary_id
    assert updated.added == ["Southwark"] and updated.removed == ["Holborn"]
    assert updated.version_id != first.version_id
    assert read_locks(sites_dir)["tube_station"].version_id == updated.version_id


def test_site_loads_its_locked_dictionary(sites_dir):
    assert load_site(sites_dir / "tube_station.yaml").pronunciation is None
    (sites_dir / LOCK_NAME).write_text(
        json.dumps({"tube_station": {"dictionary_id": "d", "version_id": "v", "pls_sha256": "h"}})
    )
    lock = load_site(sites_dir / "tube_station.yaml").pronunciation
    assert (lock.dictionary_id, lock.version_id) == ("d", "v")


def test_site_without_a_pls_file_ignores_the_lock(sites_dir):
    (sites_dir / "airport.yaml").write_text(
        (SITES_DIR / "airport.yaml").read_text("utf-8"), "utf-8"
    )
    (sites_dir / LOCK_NAME).write_text(
        json.dumps({"airport": {"dictionary_id": "d", "version_id": "v", "pls_sha256": "h"}})
    )
    assert load_site(sites_dir / "airport.yaml").pronunciation is None
