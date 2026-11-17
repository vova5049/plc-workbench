"""Public, hand-written CSV boundary cases; no private PLC export fixtures."""
import csv
import io
import re

import pytest

from fx3u.bytecode import Instruction, assemble
from fx3u.dsl.gxworks_csv import (CSVParseError, parse, parse_lenient, parse_strict,
                                 validate_compiled_steps)
from fx3u.dsl.il_compiler import compile_program, write_utf16


HEADER = 'Step No.\tLine Statement\tInstruction\tI/O(Device)\tBlank\tPI Statement\tNote\n'
LD = '0\t\tLD\tM0\t\t\t\n'
END = '1\t\tEND\t\t\t\t\n'


def source(tmp_path, text):
    path = tmp_path / 'source.csv'
    path.write_text(text, encoding='utf-8')
    return path


def test_strict_import_counts_logical_records_and_continuations(tmp_path):
    path = source(tmp_path, 'MAIN\nPLC Information:\tFXCPU FX3U/FX3UC\n' + HEADER
                  + '0\t\tMOV\tD0\t\t\t\n'
                  + '\t\t\tD1\t\t\t\n'
                  + '5\t\tEND\t\t\t\t\n')
    meta, rows = parse_strict(path)
    assert meta == {'title': 'MAIN', 'plc': 'FXCPU FX3U/FX3UC',
                    'records_imported': 3, 'records_rejected': 0}
    assert rows == [{'step': '0', 'mnemonic': 'MOV', 'operands': ['D0', 'D1']},
                    {'step': '5', 'mnemonic': 'END', 'operands': []}]


@pytest.mark.parametrize('text,reason', [
    ('MAIN\nPLC Information:\tFXCPU FX3U/FX3UC\n' + LD + END, 'header'),
    (HEADER.replace('Note', 'Unknown') + LD, 'header'),
    (HEADER + '0\t\tLD\tM0\n', 'seven columns'),
    (HEADER + '0\t\tLD\tM0\t\t\t\textra\n', 'seven columns'),
    (HEADER + '\t\tLD\tM0\t\t\t\n', 'decimal step'),
    (HEADER + '-1\t\tLD\tM0\t\t\t\n', 'decimal step'),
    (HEADER + '1.0\t\tLD\tM0\t\t\t\n', 'decimal step'),
    (HEADER + '١\t\tLD\tM0\t\t\t\n', 'decimal step'),
    (HEADER + LD + '0\t\tEND\t\t\t\t\n', 'strictly increase'),
    (HEADER + '5\t\tLD\tM0\t\t\t\n' + END, 'strictly increase'),
    (HEADER + '\t\t\tD0\t\t\t\n', 'orphan'),
    (HEADER + '0\t\tMOV\t\t\t\t\n' + '\t\t\tD0\t\t\t\n', 'orphan'),
    (HEADER + '0\t\t\tD0\t\t\t\n', 'step without instruction'),
    (HEADER + '\tcomment\t\t\t\t\t\n', 'neither instruction'),
    (HEADER + '\t\t\t\t\t\t\n', 'neither instruction'),
    (HEADER + '\n', 'seven columns'),
    (HEADER + '0\t\tLD\tM0\topaque\t\t\n', 'Blank column'),
    (HEADER + '"0"oops\t\tLD\tM0\t\t\t\n', 'malformed CSV'),
    (HEADER + '"0\t\tLD\tM0\t\t\t\n', 'malformed CSV'),
    (HEADER, 'no instruction'),
    ('', 'header'),
])
def test_hand_written_malformed_exports_are_refused(tmp_path, text, reason):
    with pytest.raises(CSVParseError, match=reason) as caught:
        parse_strict(source(tmp_path, text))
    assert caught.value.records_rejected == 1
    assert caught.value.line_number >= 0


def test_failure_reports_prefix_counts_without_partial_result(tmp_path):
    path = source(tmp_path, HEADER + LD + '1\t\t\tM1\t\t\t\n' + END)
    with pytest.raises(CSVParseError) as caught:
        parse_strict(path)
    error = caught.value
    assert error.records_imported == 1
    assert error.records_rejected == 1
    assert error.line_number == 3
    assert 'records_imported=1, records_rejected=1' in str(error)


def test_lenient_parser_remains_explicitly_available_for_inspection(tmp_path):
    path = source(tmp_path, HEADER + '\t\t\tD999\t\t\t\n' + LD + END)
    assert parse(path) == parse_lenient(path)
    assert len(parse(path)[1]) == 2
    with pytest.raises(CSVParseError, match='orphan'):
        parse_strict(path)


@pytest.mark.parametrize('body', [
    '0\t\tUNSUPPORTED\tM0\t\t\t\n',
    '0\t\tLD\tM0\t\t\t\n\t\t\tM1\t\t\t\n',
    '0\t\tLD\t\t\t\t\n',
])
def test_encoder_owns_instruction_support_and_arity(tmp_path, body):
    _, rows = parse_strict(source(tmp_path, HEADER + body))
    with pytest.raises(ValueError):
        assemble([Instruction(row['mnemonic'], tuple(row['operands'])) for row in rows])


def test_writer_preserves_compatible_normal_format():
    program, comments = compile_program([('LD', 'M0'), ('END',)], {'M0': 'Condition'}, 'MAIN')
    assert program == ('"MAIN"\r\n"PLC Information:"\t"FXCPU FX3U/FX3UC"\r\n'
                       '"Step No."\t"Line Statement"\t"Instruction"\t"I/O(Device)"'
                       '\t"Blank"\t"PI Statement"\t"Note"\r\n'
                       '"0"\t""\t"LD"\t"M0"\t""\t""\t""\r\n'
                       '"1"\t""\t"END"\t""\t""\t""\t""\r\n')
    assert comments == '"MAIN"\r\n"Device Name"\t"Comment"\r\n"M0"\t"Condition"\r\n'


@pytest.mark.parametrize('comment', ['Quote "test"', 'Tab\ttext', 'Line\nnext',
                                    'Line\r\nnext', 'żółć "x"\t\nnext'])
def test_comments_quote_tab_and_newline_roundtrip(comment):
    _, text = compile_program([('END',)], {'M0': comment}, 'MAIN')
    rows = list(csv.reader(io.StringIO(text, newline=''), delimiter='\t', strict=True))
    assert rows == [['MAIN'], ['Device Name', 'Comment'], ['M0', comment]]


def test_metadata_escaping_and_utf16_strict_roundtrip(tmp_path):
    title, plc = 'A"\t\nB', 'FXCPU "FX3U"\t\r\nmetadata'
    program, _ = compile_program([('LD', 'M0'), ('END',)], {}, title, plc=plc)
    path = tmp_path / 'source.csv'
    write_utf16(path, program)
    assert path.read_bytes().startswith(b'\xff\xfe')
    meta, rows = parse_strict(path)
    assert meta['title'] == title
    assert meta['plc'] == plc
    assert meta['records_imported'] == 2
    assert [row['mnemonic'] for row in rows] == ['LD', 'END']


def test_bad_encoding_is_a_counted_refusal(tmp_path):
    path = tmp_path / 'invalid.csv'
    path.write_bytes(b'\xff\xfe\x00')
    with pytest.raises(CSVParseError, match='encoding') as caught:
        parse_strict(path)
    assert caught.value.records_imported == 0
    assert caught.value.records_rejected == 1


@pytest.mark.parametrize('body,reason', [
    (LD + '99\t\tEND\t\t\t\t\n', 'source step 99.*compiler step 1'),
    ('3\t\tLD\tM0\t\t\t\n4\t\tEND\t\t\t\t\n',
     'source step 3.*compiler step 0'),
    ('0\t\tMOV\tK1\t\t\t\n\t\t\tD1\t\t\t\n'
     '1\t\tEND\t\t\t\t\n', 'source step 1.*compiler step 5'),
    ('0\t\tMOV\tK1\t\t\t\n\t\t\tD1\t\t\t\n'
     '6\t\tEND\t\t\t\t\n', 'source step 6.*compiler step 5'),
])
@pytest.mark.parametrize('experimental', [False, True])
def test_compile_csv_refuses_step_mismatches_before_writing(tmp_path, body, reason,
                                                          experimental, monkeypatch, capsys):
    from fx3u.cli import main
    path = source(tmp_path, HEADER + body)
    output = tmp_path / 'compiled.bin'
    monkeypatch.setattr('sys.argv', ['fx3u', 'compile-csv', str(path), str(output)]
                        + (['--experimental'] if experimental else []))
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
    assert re.search(reason, capsys.readouterr().err)
    assert not any(output.with_suffix(suffix).exists()
                   for suffix in ('.bin', '.il', '.map.json'))


@pytest.mark.parametrize('experimental', [False, True])
def test_compiled_steps_accept_continuations_and_real_instruction_widths(tmp_path, experimental):
    # These offsets are hand-counted, not generated with the map under test.
    path = source(tmp_path, HEADER + LD
                  + '1\t\tMOV\tK1\t\t\t\n\t\t\tD1\t\t\t\n'
                  + '6\t\tOUT\tT0\t\t\t\n\t\t\tK10\t\t\t\n'
                  + '9\t\tEND\t\t\t\t\n')
    _, rows = parse_strict(path)
    encoder = assemble
    if experimental:
        from fx3u.experimental_codec import assemble as encoder
    _, mapping = encoder([Instruction(row['mnemonic'], tuple(row['operands'])) for row in rows])
    validate_compiled_steps(rows, mapping)
    assert [item['step'] for item in mapping] == [0, 1, 6, 9]


def test_extended_labels_and_explicit_nops_keep_their_steps(tmp_path):
    from fx3u.experimental_codec import assemble as extended_assemble
    # P256 occupies two steps, unlike P0; NOP is present explicitly in the CSV.
    path = source(tmp_path, HEADER
                  + '0\t\tP256\t\t\t\t\n'
                  + '2\t\tNOP\t\t\t\t\n'
                  + '3\t\tEND\t\t\t\t\n')
    _, rows = parse_strict(path)
    _, mapping = extended_assemble([Instruction(row['mnemonic'], tuple(row['operands']))
                                    for row in rows])
    validate_compiled_steps(rows, mapping)
    assert [item['step'] for item in mapping] == [0, 2, 3]


@pytest.mark.parametrize('dsl_export', [False, True])
def test_extended_pointer_uses_encoder_width_instead_of_dsl_size_table(
        tmp_path, dsl_export, monkeypatch, capsys):
    from fx3u.cli import main
    path = tmp_path / 'source.csv'
    if dsl_export:
        # The legacy DSL table assigns P256 one step; the encoder requires two.
        text, _ = compile_program([('P256',), ('END',)], {}, 'MAIN')
        write_utf16(path, text)
    else:
        path.write_text(HEADER + '0\t\tP256\t\t\t\t\n'
                        + '2\t\tEND\t\t\t\t\n', encoding='utf-8')
    output = tmp_path / 'compiled.bin'
    monkeypatch.setattr('sys.argv', ['fx3u', 'compile-csv', str(path), str(output),
                                    '--experimental'])
    if dsl_export:
        with pytest.raises(SystemExit) as caught:
            main()
        assert caught.value.code == 2
        assert 'source step 1 differs from compiler step 2' in capsys.readouterr().err
        assert not any(output.with_suffix(suffix).exists()
                       for suffix in ('.bin', '.il', '.map.json'))
    else:
        main()
        assert output.read_bytes() == bytes.fromhex('00b101b20f00')


def test_every_instruction_requires_a_matching_compiler_entry():
    rows = [{'step': '0', 'mnemonic': 'LD', 'operands': ['M0']},
            {'step': '1', 'mnemonic': 'END', 'operands': []}]
    with pytest.raises(ValueError, match='instruction count'):
        validate_compiled_steps(rows, [{'step': 0}])


def test_utf16_dsl_export_compiles_with_the_same_step_guard(tmp_path):
    program, _ = compile_program([('LD', 'M0'), ('MOV', 'K1', 'D1'),
                                  ('OUT', 'T0', 'K10'), ('END',)], {}, 'MAIN')
    path = tmp_path / 'source.csv'
    write_utf16(path, program)
    _, rows = parse_strict(path)
    _, mapping = assemble([Instruction(row['mnemonic'], tuple(row['operands'])) for row in rows])
    validate_compiled_steps(rows, mapping)
