from pathlib import Path

import pytest

from fx3u.analysis import operand_devices, query_usage
from fx3u.bytecode import Instruction, parse_il
from fx3u.dsl.gxworks_csv import devices, parse, sortkey


def usage(source, target):
    return query_usage(parse_il(source), target)


@pytest.mark.parametrize("instruction,target,start,end", [
    ("MOV K4M0 D20", "M15", "M0", "M15"),
    ("DMOV D10 D100", "D11", "D10", "D11"),
    ("DMOV D10 D100", "D101", "D100", "D101"),
    ("BMOV D10 D100 K3", "D12", "D10", "D12"),
    ("BMOV D10 D100 K3", "D102", "D100", "D102"),
    ("ZRST M10 M20", "M19", "M10", "M20"),
    ("MOV K4X7 D0", "X10", "X7", "X26"),
    ("ZRST Y7 Y12", "Y10", "Y7", "Y12"),
    ("SFTL M0 M100 K16 K2", "M115", "M100", "M115"),
])
def test_static_footprints(instruction, target, start, end):
    result = usage(instruction, target)
    assert result["complete"]
    assert [(hit["start"], hit["end"], hit["certainty"]) for hit in result["hits"]] == [(start, end, "definite")]


@pytest.mark.parametrize("instruction,target", [
    ("MOV K4M0 D20", "M16"),
    ("MOV D10 D100", "D11"),
    ("BMOV D10 D100 K3", "D103"),
    ("ZRST M10 M20", "M21"),
    ("SFTL M0 M100 K16 K2", "M2"),
])
def test_static_footprint_boundaries(instruction, target):
    result = usage(instruction, target)
    assert result["complete"]
    assert result["hits"] == []
    assert "free" not in result


def test_indexed_reference_is_potential_for_other_addresses_and_reads_index():
    result = usage("MOV D0Z0 D100", "D500")
    assert not result["complete"]
    assert result["hits"][0]["certainty"] == "potential"
    assert result["hits"][0]["start"] is None
    assert usage("MOV D0Z0 D100", "Z0")["hits"][0]["kind"] == "index-register"


@pytest.mark.parametrize("source", ["UNKNOWN D0", "BMOV D0 D100 D200", "MOV U0\\G0 D0", "BMOV K4M0 K4M100 K2",
                                   "ZRST M20 M10", "SFTL K4M0 M100 K16 K2", "BMOV C199 D0 K3"])
def test_unknown_footprints_never_claim_complete_analysis(source):
    result = usage(source, "D500")
    assert not result["complete"]
    assert result["uncertainties"]


def test_dword_counter_is_not_two_logical_counters():
    result = usage("DMOV C200 D0", "C201")
    assert not result["complete"]
    assert not result["hits"]


def test_octal_and_query_validation():
    assert operand_devices("K4X007Z0") == ["X7", "Z0"]
    assert operand_devices("K123") == []
    assert operand_devices('"M10"') == []
    with pytest.raises(ValueError, match="octal"):
        usage("MOV K0 D0", "Y8")
    with pytest.raises(ValueError, match="direct"):
        usage("MOV K0 D0", "K4M0")
    assert sorted(["X10", "X7"], key=sortkey) == ["X7", "X10"]


def test_csv_group_and_index_anchors_are_parsed_as_whole_operands(tmp_path):
    path = tmp_path / "group.csv"
    path.write_text('"MAIN"\n"PLC Information:"\t"FXCPU"\n"Step No."\t""\t"Instruction"\t"I/O(Device)"\n"0"\t""\t"MOV"\t"K4M0"\n""\t""\t""\t"D10Z0"\n', encoding="utf-8")
    _, rows = parse(path)
    assert devices(rows) == {"M0": 1, "D10": 1, "Z0": 1}
    assert query_usage(rows, "M15")["hits"][0]["kind"] == "group"


def test_lighting_source_is_completely_analyzed_without_rewriting_it():
    path = Path(__file__).parents[1] / "examples/lighting-case-study/program.il"
    result = query_usage(parse_il(path.read_text()), "Y0")
    assert result["complete"], result["uncertainties"][:3]
    assert any(hit["mnemonic"] == "OUT" for hit in result["hits"])


def test_instructions_and_csv_rows_share_analysis_contract():
    instructions = [Instruction("MOV", ("K4M0", "D0"))]
    rows = [{"step": "0", "mnemonic": "MOV", "operands": ["K4M0", "D0"]}]
    assert query_usage(instructions, "M15") == query_usage(rows, "M15")


def test_csv_xref_preserves_original_plc_step(tmp_path, monkeypatch, capsys):
    import json
    from fx3u.dsl.gxworks_csv import main
    path = tmp_path / 'p.csv'
    path.write_text('"MAIN"\n"PLC Information:"\t"FXCPU"\n"Step No."\t""\t"Instruction"\t"I/O(Device)"\n"17"\t""\t"MOV"\t"K4M0"\n""\t""\t""\t"D0"\n')
    monkeypatch.setattr('sys.argv', ['gxworks_csv', str(path), '--xref', 'M15'])
    main()
    report = json.loads(capsys.readouterr().out)
    assert report['complete']
    assert report['hits'][0]['step'] == '17'
    assert report['hits'][0]['kind'] == 'group'
