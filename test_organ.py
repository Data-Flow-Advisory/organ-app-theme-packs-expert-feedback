"""
Pytest test suite for the expert-feedback theme-pack organ.

Tests cover the domain-resolution decision logic:
  - Exact (canonical) domain match
  - Case/format-insensitive matching
  - Unknown-domain generic fallback
  - Missing / blank domain generic fallback
  - Empty catalogue handling
  - display_name passthrough from domain_configs
  - self_metric confidence and decision_path tracking
  - Fail-safe behaviour and determinism
"""

import json

import pytest

from organ import decide, _normalize


# A catalogue mirroring expert_feedback_config.json's keys.
CATALOGUE = [
    "sales",
    "marketing",
    "manufacturing",
    "gdpr",
    "ai_governance",
    "operations",
    "finance",
    "systems_it",
    "hr",
]

CONFIGS = {
    "sales": {"display_name": "Sales Discovery", "core_question_count": 4},
    "ai_governance": {"display_name": "AI Governance", "core_question_count": 4},
}


class TestExactMatch:
    def test_exact_canonical_match(self):
        result = decide(
            {"requested_domain": "sales", "available_domains": CATALOGUE,
             "domain_configs": CONFIGS}
        )
        assert result["output"]["resolved_domain"] == "sales"
        assert result["output"]["use_generic_fallback"] is False
        assert result["output"]["reason"] == "domain_matched"
        assert result["output"]["display_name"] == "Sales Discovery"
        assert result["self_metric"]["confidence"] == 1.0
        assert result["self_metric"]["decision_path"] == "exact_match"

    def test_exact_match_without_configs(self):
        """display_name is null when no domain_configs supplied."""
        result = decide(
            {"requested_domain": "finance", "available_domains": CATALOGUE}
        )
        assert result["output"]["resolved_domain"] == "finance"
        assert result["output"]["display_name"] is None
        assert result["output"]["use_generic_fallback"] is False


class TestNormalizedMatch:
    def test_case_insensitive(self):
        result = decide(
            {"requested_domain": "SALES", "available_domains": CATALOGUE}
        )
        assert result["output"]["resolved_domain"] == "sales"
        assert result["self_metric"]["decision_path"] == "normalized_match"

    def test_spaces_and_hyphens(self):
        result = decide(
            {"requested_domain": "AI Governance", "available_domains": CATALOGUE,
             "domain_configs": CONFIGS}
        )
        assert result["output"]["resolved_domain"] == "ai_governance"
        assert result["output"]["display_name"] == "AI Governance"
        assert result["output"]["reason"] == "domain_matched"

    def test_hyphenated(self):
        result = decide(
            {"requested_domain": "systems-it", "available_domains": CATALOGUE}
        )
        assert result["output"]["resolved_domain"] == "systems_it"
        assert result["output"]["use_generic_fallback"] is False

    def test_whitespace_padding(self):
        result = decide(
            {"requested_domain": "  marketing  ", "available_domains": CATALOGUE}
        )
        assert result["output"]["resolved_domain"] == "marketing"


class TestUnknownDomain:
    def test_unknown_domain_falls_back(self):
        result = decide(
            {"requested_domain": "astrology", "available_domains": CATALOGUE}
        )
        assert result["output"]["resolved_domain"] is None
        assert result["output"]["use_generic_fallback"] is True
        assert result["output"]["reason"] == "domain_not_found"
        assert result["self_metric"]["decision_path"] == "no_match"
        assert "astrology" in result["rationale"]

    def test_unknown_domain_confidence_high(self):
        """A definite 'not found' is a confident decision."""
        result = decide(
            {"requested_domain": "astrology", "available_domains": CATALOGUE}
        )
        assert result["self_metric"]["confidence"] == 1.0


class TestNoDomainSupplied:
    def test_none_domain(self):
        result = decide(
            {"requested_domain": None, "available_domains": CATALOGUE}
        )
        assert result["output"]["use_generic_fallback"] is True
        assert result["output"]["reason"] == "no_domain_supplied"
        assert result["self_metric"]["decision_path"] == "no_domain"

    def test_blank_domain(self):
        result = decide(
            {"requested_domain": "   ", "available_domains": CATALOGUE}
        )
        assert result["output"]["reason"] == "no_domain_supplied"

    def test_missing_key(self):
        result = decide({"available_domains": CATALOGUE})
        assert result["output"]["reason"] == "no_domain_supplied"


class TestEmptyCatalogue:
    def test_empty_available(self):
        result = decide(
            {"requested_domain": "sales", "available_domains": []}
        )
        assert result["output"]["use_generic_fallback"] is True
        assert result["output"]["reason"] == "no_domains_available"
        assert result["self_metric"]["decision_path"] == "empty_catalogue"
        # Lower confidence — caller probably failed to load the config.
        assert result["self_metric"]["confidence"] < 1.0

    def test_missing_available_key(self):
        result = decide({"requested_domain": "sales"})
        assert result["output"]["reason"] == "no_domains_available"

    def test_non_string_entries_filtered(self):
        """Junk entries in the catalogue are ignored."""
        result = decide(
            {"requested_domain": "sales", "available_domains": [None, 42, "", "sales"]}
        )
        assert result["output"]["resolved_domain"] == "sales"

    def test_all_junk_entries_is_empty(self):
        result = decide(
            {"requested_domain": "sales", "available_domains": [None, 42, "  "]}
        )
        assert result["output"]["reason"] == "no_domains_available"


class TestGateOrdering:
    def test_empty_catalogue_beats_missing_domain(self):
        """Empty catalogue is reported even if domain is also missing."""
        result = decide({"requested_domain": None, "available_domains": []})
        assert result["output"]["reason"] == "no_domains_available"

    def test_no_domain_beats_unknown(self):
        """Blank domain reports no_domain_supplied, not domain_not_found."""
        result = decide({"requested_domain": "", "available_domains": CATALOGUE})
        assert result["output"]["reason"] == "no_domain_supplied"


class TestContract:
    def test_output_shape(self):
        result = decide(
            {"requested_domain": "sales", "available_domains": CATALOGUE}
        )
        assert set(result.keys()) == {"output", "rationale", "self_metric"}
        assert set(result["output"].keys()) == {
            "resolved_domain", "display_name", "use_generic_fallback", "reason"
        }
        assert set(result["self_metric"].keys()) == {"confidence", "decision_path"}
        assert isinstance(result["rationale"], str) and result["rationale"]

    def test_reason_in_allowed_set(self):
        allowed = {
            "domain_matched", "domain_not_found",
            "no_domain_supplied", "no_domains_available",
        }
        for dom in ["sales", "astrology", None, ""]:
            for cat in [CATALOGUE, []]:
                r = decide({"requested_domain": dom, "available_domains": cat})
                assert r["output"]["reason"] in allowed

    def test_json_serialisable(self):
        result = decide(
            {"requested_domain": "sales", "available_domains": CATALOGUE}
        )
        # Round-trips cleanly through JSON.
        assert json.loads(json.dumps(result)) == result

    def test_deterministic(self):
        state = {"requested_domain": "AI Governance",
                 "available_domains": CATALOGUE, "domain_configs": CONFIGS}
        assert decide(state) == decide(state)

    def test_context_ignored(self):
        a = decide({"requested_domain": "sales", "available_domains": CATALOGUE})
        b = decide({"requested_domain": "sales", "available_domains": CATALOGUE},
                   context={"anything": "here"})
        assert a == b


class TestNormalizeHelper:
    def test_normalize_basic(self):
        assert _normalize("AI Governance") == "ai_governance"
        assert _normalize("systems-it") == "systems_it"
        assert _normalize("  Sales  ") == "sales"
        assert _normalize("a - b") == "a_b"
