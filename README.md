# Expert-Feedback Theme-Pack Organ

A pure decider for **domain resolution** in the expert-feedback theme pack,
extracted from discovery-engine's `app/theme_packs/expert_feedback.py`.

## What it does

The expert-feedback theme pack runs domain-specific expert interviews (sales,
GDPR, AI governance, …). Each domain has its own core questions and system
prompt, loaded from `expert_feedback_config.json`. Before an interview starts,
the system must resolve the expert's declared domain to one of the configured
domains — or fall back to the generic prompt if the domain is unknown or
absent.

In the service, `get_domain_config(domain)` raises `KeyError` on an unknown
domain and the caller silently falls back to the generic `SYSTEM_PROMPT`
(before the 2026-05-13 audit it fell all the way back to the `ops` pack). This
organ makes that resolution **explicit, fail-safe, and testable**:

1. **Exact match** — the requested domain is already a canonical key.
2. **Normalized match** — case/format-insensitive: `"AI Governance"`,
   `"ai-governance"` and `"ai_governance"` all resolve to `ai_governance`.
3. **Generic fallback** — unknown domain, blank domain, or empty catalogue
   returns `use_generic_fallback: true` with a reason, never raising.

## Input contract

```json
{
  "state": {
    "requested_domain": "AI Governance",
    "available_domains": ["sales", "marketing", "gdpr", "ai_governance"],
    "domain_configs": {
      "ai_governance": {"display_name": "AI Governance", "core_question_count": 4}
    }
  }
}
```

### Fields

- **requested_domain** (str | null): The expert's domain from interview
  metadata. May be null/blank.
- **available_domains** (list[str]): The configured domain keys — the caller
  pre-loads these from `expert_feedback_config.json` (the organ does **no**
  file I/O). Canonical (lower_snake_case) keys expected; non-string/blank
  entries are ignored.
- **domain_configs** (dict | null, optional): Light per-domain metadata. When
  supplied, the matched domain's `display_name` is echoed in the output. The
  organ never needs the full config — it stays pure.

## Output contract

```json
{
  "output": {
    "resolved_domain": "ai_governance",
    "display_name": "AI Governance",
    "use_generic_fallback": false,
    "reason": "domain_matched"
  },
  "rationale": "Resolved expert domain to 'ai_governance' (AI Governance) via normalized_match; ...",
  "self_metric": {
    "confidence": 1.0,
    "decision_path": "normalized_match"
  }
}
```

### Fields

- **output.resolved_domain** (str | null): Canonical key to load, or `null` on
  fallback.
- **output.display_name** (str | null): From `domain_configs` when supplied.
- **output.use_generic_fallback** (bool): Whether to use the generic prompt.
- **output.reason** (str): One of:
  - `"domain_matched"` — resolved to a catalogue entry
  - `"domain_not_found"` — a domain was requested but no entry matches
  - `"no_domain_supplied"` — `requested_domain` was null/blank
  - `"no_domains_available"` — the catalogue itself was empty
- **rationale** (str): Human-readable explanation, derivable from state alone.
- **self_metric.confidence** (float): `1.0` when fully determined; `0.5` for an
  empty catalogue (caller likely failed to load the config); `0.0` on the
  defensive error path.
- **self_metric.decision_path** (str): `exact_match` | `normalized_match` |
  `no_match` | `no_domain` | `empty_catalogue` | `error_fallback`.

## Usage

### Run on a sample

```bash
ORGAN_INPUT=samples/domain_normalized_match.json python organ.py
```

### Run tests

```bash
pip install pytest
pytest -v
```

### Integration into discovery-engine

```python
import json
import subprocess

state = {
    "requested_domain": interview.metadata_json.get("expert_domain"),
    "available_domains": list(_CONFIG.keys()),  # from expert_feedback_config.json
    "domain_configs": {
        k: {"display_name": v["display_name"],
            "core_question_count": len(v["core_questions"])}
        for k, v in _CONFIG.items()
    },
}

result = subprocess.run(
    ["python", "organ.py"],
    input=json.dumps({"state": state}),
    capture_output=True, text=True,
)
out = json.loads(result.stdout)["output"]
if out["use_generic_fallback"]:
    prompt = SYSTEM_PROMPT                      # generic
else:
    prompt = get_system_prompt(out["resolved_domain"])
```

## Design principles

- **Pure**: all inputs via JSON; no DB / file / network calls; only computed
  advice.
- **Fail-safe**: any error or missing fact falls back to the generic prompt —
  never raises into the interview loop.
- **Deterministic**: same input always produces the same output.
- **Stdlib-only**: no external dependencies — runs anywhere Python 3.6+ is.

## Samples

| Sample | Scenario |
|--------|----------|
| `domain_exact_match.json` | Canonical key (`sales`) matches directly |
| `domain_normalized_match.json` | `"AI Governance"` → `ai_governance` |
| `domain_unknown_fallback.json` | Unknown domain → generic fallback |
