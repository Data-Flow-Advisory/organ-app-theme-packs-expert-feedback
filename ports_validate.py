#!/usr/bin/env python3
"""
Ports conformance validator for the connection standard (CONNECTORS.md).

Asserts the three port-level invariants the connection standard requires of
every organ (see CONNECTORS.md § "Conformance gains a port check"):

  1. ``ports.json`` parses and has the ``{inputs:[{name,type,required}],
     outputs:[{name,type}]}`` shape.
  2. Every port ``type`` exists in the shared type vocabulary (``types.json``).
     We validate against the VENDORED ``types.json`` in this repo so the
     self-hosted CI runner needs no cross-repo auth (see the file's _README).
  3. ``decide`` actually reads each declared input ``name`` (under ``state``)
     and writes each declared output ``name`` (under ``output``), sampled
     against the organ's own ``samples/``.

This is the standalone, CI-invokable mirror of the assertions in
``test_ports.py``; it is also importable so the tests can exercise the same
functions (including negative cases). Exit 0 = conformant, exit 1 = not.
"""

from __future__ import annotations

import json
import os
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
PORTS_PATH = os.path.join(HERE, "ports.json")
TYPES_PATH = os.path.join(HERE, "types.json")
SAMPLES_DIR = os.path.join(HERE, "samples")


class _TrackingDict(dict):
    """A dict that records which keys were read via ``.get`` / ``[]``.

    The organ reads its inputs with ``state.get("...")``; wrapping a sample's
    ``state`` in this lets us prove ``decide`` genuinely reads each declared
    input port name rather than trusting the manifest.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reads: set = set()

    def get(self, key, default=None):
        self.reads.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.reads.add(key)
        return super().__getitem__(key)


def load_ports(path: str = PORTS_PATH) -> dict:
    with open(path) as fh:
        return json.load(fh)


def load_types(path: str = TYPES_PATH) -> dict:
    with open(path) as fh:
        return json.load(fh)


def validate_shape(ports: dict) -> list:
    """ports.json has inputs/outputs lists of well-formed port dicts."""
    errors = []
    if not isinstance(ports, dict):
        return ["ports.json is not a JSON object"]
    for side, required_keys in (("inputs", {"name", "type"}),
                                ("outputs", {"name", "type"})):
        seq = ports.get(side)
        if not isinstance(seq, list):
            errors.append(f"ports.json '{side}' must be a list")
            continue
        for i, port in enumerate(seq):
            if not isinstance(port, dict):
                errors.append(f"{side}[{i}] is not an object")
                continue
            missing = required_keys - set(port)
            if missing:
                errors.append(f"{side}[{i}] missing keys: {sorted(missing)}")
            if "name" in port and not isinstance(port["name"], str):
                errors.append(f"{side}[{i}].name must be a string")
            if "type" in port and not isinstance(port["type"], str):
                errors.append(f"{side}[{i}].type must be a string")
    return errors


def validate_types_exist(ports: dict, types: dict) -> list:
    """Every declared port type is a key in the vocabulary's `types` map."""
    vocab = set((types or {}).get("types", {}))
    errors = []
    for side in ("inputs", "outputs"):
        for port in ports.get(side, []) or []:
            t = port.get("type")
            if t not in vocab:
                errors.append(
                    f"{side} port '{port.get('name')}' has type '{t}' "
                    f"which is not in the type vocabulary (types.json)"
                )
    return errors


def validate_decide_io(ports: dict, samples_dir: str = SAMPLES_DIR) -> list:
    """decide reads each input name and writes each output name (on samples)."""
    # Imported lazily so a syntax error in organ.py surfaces as a clear failure
    # rather than an import-time crash of the whole validator.
    from organ import decide

    errors = []
    input_names = [p["name"] for p in ports.get("inputs", []) or []]
    output_names = [p["name"] for p in ports.get("outputs", []) or []]

    sample_files = sorted(
        f for f in os.listdir(samples_dir) if f.endswith(".json")
    ) if os.path.isdir(samples_dir) else []
    if not sample_files:
        errors.append("no samples/*.json to validate decide() I/O against")
        return errors

    for fname in sample_files:
        with open(os.path.join(samples_dir, fname)) as fh:
            payload = json.load(fh)
        tracking = _TrackingDict(payload.get("state", {}))
        result = decide(tracking, payload.get("context"))

        # Inputs: every declared input name must have been read from state.
        for name in input_names:
            if name not in tracking.reads:
                errors.append(
                    f"{fname}: declared input port '{name}' is never read by "
                    f"decide() (read: {sorted(tracking.reads)})"
                )

        # Outputs: every declared output name must appear under result.output.
        out = (result or {}).get("output", {})
        if not isinstance(out, dict):
            errors.append(f"{fname}: decide() result has no 'output' object")
            continue
        for name in output_names:
            if name not in out:
                errors.append(
                    f"{fname}: declared output port '{name}' is not written "
                    f"by decide() (wrote: {sorted(out)})"
                )
    return errors


def validate(ports_path: str = PORTS_PATH,
             types_path: str = TYPES_PATH,
             samples_dir: str = SAMPLES_DIR) -> list:
    """Run all checks; return a (possibly empty) list of error strings."""
    try:
        ports = load_ports(ports_path)
    except Exception as e:  # parse failure is itself a conformance failure
        return [f"ports.json failed to parse: {e}"]
    try:
        types = load_types(types_path)
    except Exception as e:
        return [f"types.json failed to parse: {e}"]

    errors = validate_shape(ports)
    if errors:
        # Shape errors make the later checks meaningless; report and stop.
        return errors
    errors += validate_types_exist(ports, types)
    errors += validate_decide_io(ports, samples_dir)
    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("ports conformance: FAIL", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("ports conformance: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
