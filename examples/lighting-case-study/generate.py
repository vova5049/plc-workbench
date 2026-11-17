#!/usr/bin/env python3
"""Generate and verify the actual local lighting example; never opens a PLC."""
import argparse
import hashlib
import shutil
import tempfile
from pathlib import Path

from fx3u.bytecode import Instruction
from fx3u.dsl import write_utf16
from fx3u.dsl.gxworks_csv import parse_strict, validate_compiled_steps
from fx3u.experimental_codec import assemble, disassemble

import config
from build_station import build_station


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    reference = Path(__file__).with_name('program.il').read_text(encoding='utf-8')
    expected = [Instruction(row.split()[0], tuple(row.split()[1:]))
                for row in reference.splitlines() if row.strip()]
    reference_bytes, _ = assemble(expected)
    legacy = Path(__file__).with_name('legacy-reference.il').read_text(encoding='utf-8')
    legacy_instructions = [Instruction(row.split()[0], tuple(row.split()[1:]))
                           for row in legacy.splitlines() if row.strip()]
    legacy_bytes, _ = assemble(legacy_instructions)
    legacy_hash = hashlib.sha256(legacy_bytes).hexdigest()
    if (len(legacy_instructions) != 1585 or len(legacy_bytes) != 5954 or
            legacy_hash != 'ca59c2fafd692761234709d19ddf42faee3a26606e8e46f2bef4b17028b930b2'):
        raise ValueError('Historical PLC readback reference changed')
    print(f'legacy-reference: {len(legacy_instructions)} instructions, '
          f'{len(legacy_bytes)} bytes, sha256={legacy_hash}, '
          'historical PLC readback comparison only; contains known click-loss defect')
    messages = []
    with tempfile.TemporaryDirectory(prefix='lighting-generate-') as temporary:
        staged = Path(temporary)
        for station in config.STATIONS:
            program, _ = build_station(
                station, config.N_BUTTONS, config.WINDOW, config.LONG_PRESS,
                config.single_zones_for, config.double_zones_for,
                config.long_off_zones_for,
                auto_off_hours_for=config.auto_off_hours_for,
                enable_cmd_diag=config.ENABLE_CMD_DIAG,
                cmd_seq_reg=config.CMD_SEQ_REG, cmd_last_reg=config.CMD_LAST_REG)
            csv, comments = program.compile(station)
            csv_path = staged / f'{station}.csv'
            write_utf16(str(csv_path), csv)
            write_utf16(str(staged / f'{station}_COMMENT.csv'), comments)
            _, rows = parse_strict(str(csv_path))
            instructions = [Instruction(row['mnemonic'], tuple(row['operands']))
                            for row in rows]
            compiled, mapping = assemble(instructions)
            validate_compiled_steps(rows, mapping)
            decoded = disassemble(compiled)
            rebuilt, _ = assemble(decoded)
            if rebuilt != compiled:
                raise ValueError(f'{station}: decode/recompile mismatch')
            if compiled != reference_bytes:
                raise ValueError(f'{station}: differs from the recorded example; '
                                 'review intentional configuration changes')
            (staged / f'{station}.bin').write_bytes(compiled)
            (staged / f'{station}.il').write_text(
                '\n'.join(map(str, decoded)) + '\n', encoding='utf-8')
            messages.append(
                f'{station}: {len(instructions)} instructions, {len(compiled)} bytes, '
                f'sha256={hashlib.sha256(compiled).hexdigest()}, '
                'reference and round-trip PASS; offline only, not PLC-qualified')
        # Validation failures leave the destination untouched. Publishing several
        # files is not a filesystem transaction if copying itself fails.
        args.out.mkdir(parents=True, exist_ok=True)
        for path in sorted(staged.iterdir()):
            shutil.copyfile(path, args.out / path.name)
    for message in messages:
        print(message)


if __name__ == '__main__':
    main()
