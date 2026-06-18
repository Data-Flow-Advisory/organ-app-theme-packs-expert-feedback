"""
Tests for the connection-standard ports manifest (ports.json) and its
validator (ports_validate.py).

These run inside the existing pytest step so a regression that breaks the
ports contract turns CI red (the conformance.yml ports step is the other
guard rail). Includes NEGATIVE cases proving the validator actually fails
when a type is unknown or a declared port name is not read/written.
"""

import json
import os

import pytest

import ports_validate as pv
from organ import decide


HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES_DIR = os.path.join(HERE, "samples")


# ---------------------------------------------------------------------------
# Positive: the real manifest is conformant.
# ---------------------------------------------------------------------------

def test_ports_json_parses():
    ports = pv.load_ports()
    assert isinstance(ports, dict)
    assert isinstance(ports.get("inputs"), list)
    assert isinstance(ports.get("outputs"), list)


def test_types_json_parses():
    types = pv.load_types()
    assert "types" in types and isinstance(types["types"], dict)


def test_manifest_shape_valid():
    assert pv.validate_shape(pv.load_ports()) == []


def test_every_port_type_in_vocabulary():
    ports, types = pv.load_ports(), pv.load_types()
    assert pv.validate_types_exist(ports, types) == []


def test_decide_reads_and_writes_declared_names():
    assert pv.validate_decide_io(pv.load_ports()) == []


def test_validate_overall_green():
    assert pv.validate() == []


def test_validator_main_exits_zero():
    assert pv.main() == 0


def test_declared_input_names_are_actually_read():
    """Each declared input name must be read by decide() on every sample."""
    ports = pv.load_ports()
    input_names = [p["name"] for p in ports["inputs"]]
    for fname in os.listdir(SAMPLES_DIR):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(SAMPLES_DIR, fname)) as fh:
            payload = json.load(fh)
        tracking = pv._TrackingDict(payload.get("state", {}))
        decide(tracking, payload.get("context"))
        for name in input_names:
            assert name in tracking.reads, f"{name} not read for {fname}"


def test_declared_output_names_are_written():
    ports = pv.load_ports()
    output_names = [p["name"] for p in ports["outputs"]]
    for fname in os.listdir(SAMPLES_DIR):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(SAMPLES_DIR, fname)) as fh:
            payload = json.load(fh)
        result = decide(payload["state"], payload.get("context"))
        for name in output_names:
            assert name in result["output"], f"{name} not written for {fname}"


def test_domain_resolution_mirrors_flat_fields():
    """The DomainResolution output port re-views the four flat fields."""
    for fname in os.listdir(SAMPLES_DIR):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(SAMPLES_DIR, fname)) as fh:
            payload = json.load(fh)
        out = decide(payload["state"], payload.get("context"))["output"]
        dr = out["domain_resolution"]
        for k in ("resolved_domain", "display_name",
                  "use_generic_fallback", "reason"):
            assert dr[k] == out[k], f"{k} diverges in {fname}"


# ---------------------------------------------------------------------------
# Negative: the validator MUST fail on a broken manifest.
# ---------------------------------------------------------------------------

def test_unknown_type_is_rejected():
    bad = {"inputs": [{"name": "available_domains", "type": "NoSuchType",
                       "required": True}],
           "outputs": [{"name": "domain_resolution", "type": "DomainResolution"}]}
    errors = pv.validate_types_exist(bad, pv.load_types())
    assert errors and any("NoSuchType" in e for e in errors)


def test_undeclared_output_name_is_rejected():
    bad = {"inputs": [{"name": "available_domains", "type": "ExpertDomainCatalogue",
                       "required": True}],
           "outputs": [{"name": "not_written_anywhere", "type": "DomainResolution"}]}
    errors = pv.validate_decide_io(bad)
    assert errors and any("not_written_anywhere" in e for e in errors)


def test_unread_input_name_is_rejected():
    bad = {"inputs": [{"name": "phantom_input", "type": "ExpertDomainCatalogue",
                       "required": True}],
           "outputs": [{"name": "domain_resolution", "type": "DomainResolution"}]}
    errors = pv.validate_decide_io(bad)
    assert errors and any("phantom_input" in e for e in errors)


def test_malformed_shape_is_rejected():
    assert pv.validate_shape({"inputs": "not-a-list"}) != []
    assert pv.validate_shape({"inputs": [{"name": "x"}], "outputs": []}) != []
