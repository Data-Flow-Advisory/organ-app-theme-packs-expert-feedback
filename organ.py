#!/usr/bin/env python3
"""
Expert-Feedback Theme-Pack Organ — extracted decision logic from
discovery-engine.

A pure decider for *domain resolution* in the expert-feedback theme pack.
Given an expert's declared domain and the catalogue of domains the
configuration actually defines, this organ decides which domain config to
use — or whether to fall back to the generic prompt — and explains why.

This is the pure core of ``app/theme_packs/expert_feedback.py``'s
``get_domain_config(domain)`` / ``get_system_prompt(domain)`` resolution.
In the service that logic loads ``expert_feedback_config.json`` from disk and
raises ``KeyError`` on an unknown domain; the caller historically swallowed
that and silently fell back to the generic SYSTEM_PROMPT (or, before the
2026-05-13 audit, all the way to the ops pack). This organ makes that
resolution explicit, fail-safe, and testable:

  - The caller pre-loads the config and passes the available domain keys
    (and, optionally, light metadata per domain) — the organ does NO file
    or network I/O.
  - Resolution is case/format-insensitive: ``"AI Governance"`` resolves to
    the canonical key ``"ai_governance"``.
  - An unknown / missing domain returns a generic-fallback decision instead
    of raising, matching the service's observed behaviour.

Contract:
  INPUT state: {
    "requested_domain": str | null,      # expert's domain from interview meta
    "available_domains": [str, ...],     # pre-loaded config keys (canonical)
    "domain_configs": {                  # OPTIONAL light metadata per domain
      "<key>": {
        "display_name": str,
        "core_question_count": int
      }
    } | null
  }

  OUTPUT: {
    "output": {
      "resolved_domain": str | null,     # canonical key, or null on fallback
      "display_name": str | null,        # from domain_configs when supplied
      "use_generic_fallback": bool,
      "reason": str                      # see _REASONS below
    },
    "rationale": "...",
    "self_metric": {
      "confidence": float,               # 1.0 when fully determined
      "decision_path": str               # which branch decided
    }
  }

Reasons:
  "domain_matched"      — requested domain resolved to a catalogue entry
  "domain_not_found"    — a domain was requested but no entry matches
  "no_domain_supplied"  — requested_domain was null/blank
  "no_domains_available"— the catalogue itself was empty

The organ is pure:
  - Takes all inputs via JSON
  - Makes no DB / file / network calls
  - Returns only computed advice
  - Never raises on bad input (fail-safe → generic fallback)
"""

from __future__ import annotations

import json
import os
import sys


_REASONS = (
    "domain_matched",
    "domain_not_found",
    "no_domain_supplied",
    "no_domains_available",
)


def _normalize(value: str) -> str:
    """Canonicalise a domain label for matching.

    Lower-cases, trims, and collapses spaces/hyphens to underscores so that
    ``"AI Governance"``, ``"ai-governance"`` and ``"ai_governance"`` all
    compare equal.
    """
    out = value.strip().lower()
    for ch in (" ", "-"):
        out = out.replace(ch, "_")
    # collapse repeated underscores produced by mixed separators
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


def _fallback(reason: str, rationale: str, decision_path: str, confidence: float) -> dict:
    return {
        "output": {
            "resolved_domain": None,
            "display_name": None,
            "use_generic_fallback": True,
            "reason": reason,
        },
        "rationale": rationale,
        "self_metric": {
            "confidence": confidence,
            "decision_path": decision_path,
        },
    }


def decide(state: dict, context: dict | None = None) -> dict:
    """Resolve an expert's domain to a theme-pack domain config.

    Args:
        state: {"requested_domain": ..., "available_domains": [...], ...}
        context: unused, present for orchestrator compatibility

    Returns:
        {"output": {...}, "rationale": "...", "self_metric": {...}}
    """
    context = context or {}

    try:
        requested = state.get("requested_domain")
        available = state.get("available_domains") or []
        domain_configs = state.get("domain_configs") or {}

        # Defensive: keep only string entries in the catalogue.
        available = [d for d in available if isinstance(d, str) and d.strip()]

        # 1. Empty catalogue — nothing to resolve against.
        if not available:
            return _fallback(
                "no_domains_available",
                "No domains are configured (available_domains is empty); "
                "use the generic expert-feedback system prompt.",
                "empty_catalogue",
                # Lower confidence: caller likely failed to load the config.
                0.5,
            )

        # 2. No domain supplied — generic prompt by design.
        if not isinstance(requested, str) or not requested.strip():
            return _fallback(
                "no_domain_supplied",
                "No expert domain was supplied; use the generic "
                "expert-feedback system prompt.",
                "no_domain",
                1.0,
            )

        # 3a. Exact (already-canonical) match.
        if requested in available:
            return _resolved(requested, domain_configs, "exact_match")

        # 3b. Case/format-insensitive match against the catalogue.
        target = _normalize(requested)
        norm_index = {_normalize(d): d for d in available}
        if target in norm_index:
            canonical = norm_index[target]
            return _resolved(canonical, domain_configs, "normalized_match")

        # 4. Requested but unknown — fall back, name what was asked for.
        return _fallback(
            "domain_not_found",
            f"Requested domain '{requested}' is not in the configured "
            f"catalogue ({', '.join(sorted(available))}); use the generic "
            f"expert-feedback system prompt.",
            "no_match",
            1.0,
        )

    except Exception as e:  # pragma: no cover - defensive
        # Fail-safe: on any error, fall back to the generic prompt rather
        # than raising into the interview loop.
        return _fallback(
            "no_domain_supplied",
            f"Decision logic error (fail-safe to generic): {e}",
            "error_fallback",
            0.0,
        )


def _resolved(canonical: str, domain_configs: dict, decision_path: str) -> dict:
    cfg = domain_configs.get(canonical) if isinstance(domain_configs, dict) else None
    display_name = None
    if isinstance(cfg, dict):
        dn = cfg.get("display_name")
        if isinstance(dn, str) and dn.strip():
            display_name = dn
    return {
        "output": {
            "resolved_domain": canonical,
            "display_name": display_name,
            "use_generic_fallback": False,
            "reason": "domain_matched",
        },
        "rationale": (
            f"Resolved expert domain to '{canonical}'"
            + (f" ({display_name})" if display_name else "")
            + f" via {decision_path}; load its domain-specific questions and "
            f"system prompt."
        ),
        "self_metric": {
            "confidence": 1.0,
            "decision_path": decision_path,
        },
    }


def main() -> int:
    path = os.environ.get("ORGAN_INPUT")
    raw = open(path).read() if path else sys.stdin.read()
    try:
        payload = json.loads(raw)
        state = payload["state"]
    except Exception as e:
        print(json.dumps({"error": f"invalid input: {e}"}), file=sys.stderr)
        return 1
    print(json.dumps(decide(state, payload.get("context")), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
