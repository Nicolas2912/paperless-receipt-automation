import os
import sys
import pytest

# Ensure the repository's src/ is importable
sys.path.insert(0, os.path.abspath("src"))

from paperless_automation.domain.merchant import resolve_tag_and_key


def test_exact_match_uses_original_key_casing():
    tag_map = {
        "REWE": "Supermarkt",
        "dm": "Drogerie",
    }
    tag, key = resolve_tag_and_key(tag_map, "rewe")
    assert tag == "Supermarkt"
    assert key == "REWE"  # original key preserved


def test_levenshtein_picks_nearest_key():
    tag_map = {
        "famila": "Verbrauchermarkt",
        "ikea": "Möbelhaus",
    }
    # Model extracted misspelling
    tag, key = resolve_tag_and_key(tag_map, "familia")
    assert tag == "Verbrauchermarkt"
    assert key == "famila"
