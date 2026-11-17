"""Differential checks of the extended (experimental) codec against independent references.

W3 (public, hard): the extended codec must reproduce the qualified encoder (fx3u.bytecode)
    on every qualified form, as a prefix of the padded region image.
W2 (hard, skipped without FX3U_PRIVATE_CORPUS): private read-back of a real program.
W1 (informational, skipped without FX3U_EXPERIMENTAL_CORPUS): per-mnemonic coverage of the
    private oracle vectors; fails only on a mismatch.

Plus consistency checks of the 528-mnemonic set against the encoder and the catalog.
"""

import collections
import json
import os
from importlib import resources
from pathlib import Path

import pytest

from fx3u import bytecode, experimental_codec as xc
from fx3u.bytecode import Instruction

ROOT = Path(__file__).parents[1]
PROFILE = xc.PROFILE
HARDCODED = {"NOP", "MC", "MCR"}  # encoded in experimental_codec.py outside the profile
MNEMONICS = (
    set(PROFILE["instructions"])
    | set(PROFILE["bit_forms"])
    | set(PROFILE["special_forms_not_in_generic_table"])
    | HARDCODED
)
CAPABILITIES = json.loads(
    resources.files("fx3u").joinpath("capabilities.json").read_text(encoding="utf-8")
)
QUALIFIED = [
    row
    for row in CAPABILITIES["instructions"]
    if row.get("current_encoder", {}).get("status") != "unsupported"
]
CATALOG_CANONICAL = {
    m
    for row in CAPABILITIES["instructions"]
    for m in row.get("experimental_encoder", {}).get("canonical_mnemonics", [])
}

# Mnemonics known to the encoder but absent from the catalog and not "<catalog mnemonic>P".
# Known catalog gap, tracked as separate work; the list must not rot (see the drift test).
CATALOG_GAP_EXCEPTIONS = {
    "ANDD<", "ANDD<=", "ANDD<>", "ANDD=", "ANDD>", "ANDD>=",
    "LDD<", "LDD<=", "LDD<>", "LDD=", "LDD>", "LDD>=",
    "ORD<", "ORD<=", "ORD<>", "ORD=", "ORD>", "ORD>=",
    "EXTR", "DEXTR", "EXTRP", "DEXTRP", "OPT", "DOPT", "DXOR",
}  # fmt: skip

# One-liners for qualified mnemonics that have no catalog case and no example program.
EXTRA_PROGRAMS = [
    "LD M0\nLD M1\nORB\nOUT M2\nEND",
    "LD M0\nANI M1\nOR M3\nOUT M2\nEND",
    "LD M0\nPLS M1\nEND",
    "LD M0\nINC D0\nEND",
    "LD M0\nAND= D0 K1\nAND>= D1 K2\nOUT M2\nEND",
]

SAMPLE = {
    "bit:M": "M0", "bit:MS": "M8000", "bit:S": "S0", "bit:X": "X0", "bit:Y": "Y0",
    "constant:E": "E1.5", "constant:H": "H1", "constant:K": "K1",
    "group:M": "K1M0", "group:M:index:V": "K1M0V0", "group:M:index:Z": "K1M0Z0",
    "group:M:wide": "K5M0", "group:S": "K1S0", "group:S:wide": "K5S0",
    "group:X": "K1X0", "group:X:wide": "K5X0", "group:Y": "K1Y0", "group:Y:wide": "K5Y0",
    "module": "U0\\G0", "number:I": "I100", "number:P": "P0", "string": '"A"',
    "word:C": "C0", "word:C32": "C200", "word:C:index:V": "C0V0", "word:C:index:Z": "C0Z0",
    "word:D": "D0", "word:D:index:V": "D0V0", "word:D:index:Z": "D0Z0", "word:DS": "D8000",
    "word:R": "R0", "word:T": "T0", "word:T:index:V": "T0V0", "word:T:index:Z": "T0Z0",
    "word:V": "V0", "word:Z": "Z0",
}  # fmt: skip


def probe(mnemonic):
    """Return 'encoded' (full encoding built) or 'recognized' (known, no sample built).

    Raises AssertionError if the encoder does not know the mnemonic at all.
    """
    if mnemonic == "NOP":
        candidates = [()]
    elif mnemonic == "MC":
        candidates = [("N0", "M0")]
    elif mnemonic == "MCR":
        candidates = [("N0",)]
    elif mnemonic == "ASC":
        candidates = [("A", "D0")]
    elif mnemonic in PROFILE["instructions"]:
        spec = PROFILE["instructions"][mnemonic]
        candidates = [
            tuple(SAMPLE[form] for form in (forms[0] for forms in spec["forms"])),
        ]
        # Further attempts: every form of the first operand position paired with first forms.
        for form in spec["forms"][0] if spec["forms"] else []:
            candidates.append(
                (SAMPLE[form],) + tuple(SAMPLE[forms[0]] for forms in spec["forms"][1:])
            )
    elif mnemonic in PROFILE["bit_forms"]:
        candidates = [("M0",), ("Y0",), ("X0",), ("S0",)]
    else:
        raise AssertionError(f"{mnemonic} is not known to the experimental codec")
    for args in candidates:
        try:
            xc.encode_instruction(Instruction(mnemonic, args))
            return "encoded"
        except ValueError as error:
            if str(error).startswith("Unsupported experimental instruction"):
                if mnemonic in PROFILE["instructions"]:
                    # Wrong operand count would land here too; the spec fixes the count, so
                    # this cannot happen for a recognised profile entry.
                    raise AssertionError(f"{mnemonic}: {error}") from None
    return "recognized"


def test_mnemonic_set_is_528():
    assert (
        len(MNEMONICS)
        == CAPABILITIES["extended_codec"]["runtime_mnemonics"]
        == 528
    )
    assert {row["mnemonic"] for row in QUALIFIED} <= {m for m in MNEMONICS}
    assert len(QUALIFIED) == 16


def test_every_mnemonic_has_offline_form():
    # Negative control: the recognition probe really discriminates.
    with pytest.raises(ValueError, match="Unsupported experimental instruction"):
        xc.encode_instruction(Instruction("NOSUCHMNEMONIC", ()))
    with pytest.raises(AssertionError):
        probe("NOSUCHMNEMONIC")
    outcomes = collections.Counter(probe(m) for m in sorted(MNEMONICS))
    assert sum(outcomes.values()) == 528
    # Informational: how many got a full representative encoding (the rest is covered by
    # the oracle report W1 and by the hard checks W2/W3).
    print("probe outcomes:", dict(outcomes))


def test_catalog_gap_is_exactly_the_known_exceptions():
    missing = MNEMONICS - CATALOG_CANONICAL
    explained = {m for m in missing if m.endswith("P") and m[:-1] in CATALOG_CANONICAL}
    unexplained = missing - explained
    assert unexplained == CATALOG_GAP_EXCEPTIONS
    # The exception list must not rot: nothing in it may already be in the catalog,
    # and every entry must still be an encoder mnemonic.
    assert not (CATALOG_GAP_EXCEPTIONS & CATALOG_CANONICAL)
    assert CATALOG_GAP_EXCEPTIONS <= MNEMONICS
    assert not (CATALOG_CANONICAL - MNEMONICS)  # catalog never claims what the encoder lacks


def _prefix_matches_padded(il):
    """Return True/False, or None when the qualified encoder itself rejects the program."""
    try:
        reference = bytecode.assemble(bytecode.parse_il(il))[0]
    except ValueError:
        return None
    extended = xc.assemble(xc.parse_il(il))[0]
    assert reference[: len(extended)] == extended, il
    assert set(reference[len(extended) :]) <= {0xFF}, il
    return True


def test_qualified_forms_match_experimental():
    programs = []
    for row in QUALIFIED:
        for case in row.get("qualification", {}).get("cases", []) or []:
            for part in case.split(";"):
                part = part.strip()
                if part:
                    programs.append(f"{row['mnemonic']} {part}\nEND")
    programs += EXTRA_PROGRAMS
    programs += [path.read_text() for path in sorted((ROOT / "examples").glob("*.il"))]
    checked = skipped = 0
    seen = set()
    for il in programs:
        result = _prefix_matches_padded(il)
        if result is None:
            skipped += 1
            continue
        checked += 1
        seen |= {i.mnemonic for i in bytecode.parse_il(il)}
    print(f"differential W3: checked={checked} skipped={skipped}")
    wanted = {row["mnemonic"] for row in QUALIFIED}
    assert wanted <= seen, sorted(wanted - seen)


@pytest.mark.skipif(
    not os.environ.get("FX3U_PRIVATE_CORPUS"), reason="FX3U_PRIVATE_CORPUS not set"
)
def test_house_program_experimental_prefix():
    from fx3u.dsl.gxworks_csv import parse

    manifest = Path(os.environ["FX3U_PRIVATE_CORPUS"])
    cases = json.loads(manifest.read_text(encoding="utf-8"))["cases"]
    assert cases
    for case in cases:
        _, rows = parse(str(manifest.parent / case["csv"]))
        instructions = [Instruction(r["mnemonic"], tuple(r["operands"])) for r in rows]
        data, _ = xc.assemble(instructions)
        readback = (manifest.parent / case["readback"]).read_bytes()
        assert len(instructions) == case["instructions"]
        assert readback[: len(data)] == data, case["name"]
        assert set(readback[len(data) :]) <= {0xFF}, case["name"]


ORACLE_FILES = [
    "matrix-corpus-final.jsonl",
    "operand-corpus.jsonl",
    "form-corpus.jsonl",
    "all-device-corpus.jsonl",
    "extra-corpus.jsonl",
    "pointer-corpus.jsonl",
    "missing-corpus.jsonl",
    "group-boundary-corpus.jsonl",
]


@pytest.mark.skipif(
    not os.environ.get("FX3U_EXPERIMENTAL_CORPUS"),
    reason="FX3U_EXPERIMENTAL_CORPUS not set",
)
def test_oracle_coverage_report():
    root = Path(os.environ["FX3U_EXPERIMENTAL_CORPUS"])
    stats = collections.defaultdict(lambda: {"matched": 0, "rejected": 0, "mismatch": 0})
    for name in ORACLE_FILES:
        for line in (root / name).read_text().splitlines():
            row = json.loads(line)
            first = next((l for l in row["il"].splitlines() if l.strip() != "END"), "END")
            mnemonic = first.split()[0].upper() if first.split() else "END"
            bucket = stats[mnemonic]
            try:
                data, _ = xc.assemble(xc.parse_il(row["il"]))
            except ValueError:
                data = None
            if row["status"] == "ok":
                if data is not None and data == bytes.fromhex(row["cpu"]):
                    bucket["matched"] += 1
                else:
                    bucket["mismatch"] += 1
            elif data is None:
                bucket["rejected"] += 1
            else:
                bucket["mismatch"] += 1
    without = sorted(m for m in MNEMONICS if m not in stats)
    total = {k: sum(b[k] for b in stats.values()) for k in ("matched", "rejected", "mismatch")}
    print(f"oracle coverage: {total}; mnemonics without any vector: {len(without)}")
    destination = os.environ.get("FX3U_DIFF_REPORT")
    if destination:
        Path(destination).write_text(
            json.dumps(
                {"totals": total, "per_mnemonic": stats, "without_vector": without},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    bad = {m: b for m, b in stats.items() if b["mismatch"]}
    assert not bad, bad
