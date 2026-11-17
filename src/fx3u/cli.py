"""fx3u --help. Offline compile/disassemble/catalog; live subcommands are explicit and read-only
except `deploy --execute`, internal `device-write --execute` (both STOP the CPU) and
`live-write --execute` (allow-listed write in RUN, no STOP; selected bench cases
have evidence, other forms and CPUs remain unqualified). See SAFETY.md."""
import argparse
import json
import time
from importlib import resources
from pathlib import Path

from . import fleet
from .bytecode import Instruction, assemble, disassemble, parse_il
from .client import REGION_SIZE, Session, digest


def _int_auto(text):
    return int(text, 0)  # decimal, 0x.. hex; ValueError -> argparse usage error


def _full_region_verification(session, data):
    """Report image/header integrity separately from static layout qualification."""
    from .layout import validate_program_checksum
    header = session._program_header
    info = {
        'layout_valid': True,  # qualify_full_region already succeeded before read.
        'checksum_expected_sum16': int.from_bytes(header[2:4], 'little'),
        'checksum_actual_sum16': sum(int.from_bytes(data[i:i+2], 'little')
                                     for i in range(0, len(data), 2)) & 0xFFFF,
        'checksum_valid': False,
    }
    try:
        validate_program_checksum(header, data)
    except ValueError as exc:
        info['checksum_error'] = str(exc)
    else:
        info['checksum_valid'] = True
    return info


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    catalog = sub.add_parser('catalog', help='Offline instruction atlas and qualification status')
    catalog.add_argument('--mnemonic')
    catalog.add_argument('--family')
    catalog.add_argument('--supported', action='store_true')
    catalog.add_argument('--experimental', action='store_true', help='Show independently calibrated extended encoding forms')
    c = sub.add_parser('compile', help='IL -> fixed-region RAM image and source map')
    c.add_argument('source', type=Path)
    c.add_argument('output', type=Path)
    d = sub.add_parser('disassemble', help='RAM region -> IL (reject unknown encodings)')
    d.add_argument('image', type=Path)
    d.add_argument('output', type=Path)
    for name in ('compile-experimental', 'disassemble-experimental'):
        experimental = sub.add_parser(name, help='Offline experimental oracle-calibrated codec; no PLC access')
        experimental.add_argument('source', type=Path)
        experimental.add_argument('output', type=Path)
    x = sub.add_parser('xref', help='Find device uses and PLC step numbers')
    x.add_argument('image', type=Path)
    x.add_argument('device')
    x.add_argument('--experimental', action='store_true', help='Use the extended decoder for this image')
    cc = sub.add_parser('compile-csv', help='GX Works2 IL CSV export (or fx3u.dsl output) -> RAM image')
    cc.add_argument('source', type=Path)
    cc.add_argument('output', type=Path)
    cc.add_argument('--experimental',action='store_true',help='Compile DSL/CSV using the extended independent encoder')
    sim = sub.add_parser('simulate', help='Offline scan semantics subset; unsupported operations fail explicitly')
    sim.add_argument('source',type=Path)
    sim.add_argument('--scans',type=int)
    sim.add_argument('--dt-ms',type=float,default=10)
    sim.add_argument('--trace',type=Path,help='JSON list of inputs/dt_ms scan frames')
    sim.add_argument('--watch',nargs='+',default=['D0','D1','C0','T0','M0','Y0'])
    lw = sub.add_parser('live-write', help='Allow-listed FORCE of one M/Y bit or write of one D word in RUN; '
                                           'no STOP/RUN; dry run unless --execute; selected bench cases have '
                                           'evidence, other forms and CPUs remain unqualified')
    lw.add_argument('device', help='M<n> or Y<octal> = FORCE bit (value 0/1) or D<n> = word write (value 0..65535 or 0x..)')
    lw.add_argument('value', type=_int_auto)
    lw.add_argument('--allow', type=Path, required=True, help='JSON {"allow": ["M240-M255", "D24-D49"]}; Y octal; mandatory')
    lw.add_argument('--port', help='COM port; needed (with --wire-log) only for --execute')
    lw.add_argument('--wire-log', type=Path, help='New JSONL file, exclusive creation; needed for --execute')
    lw.add_argument('--execute', action='store_true', help='Send the write (RUN required, one attempt, one read-back)')
    fleet.add_arguments(sub.add_parser('fleet-watch', help='Read-only polling of several named COM ports'))
    for name in ('status', 'diagnostics', 'watch', 'monitor', 'device-write', 'dump', 'compare', 'deploy'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--port', required=True)
        cmd.add_argument('--wire-log', type=Path, required=True, help='New JSONL file, exclusive creation')
        if name in ('dump', 'deploy'):
            cmd.add_argument('--out-dir', type=Path, required=True)
        if name in ('compare', 'deploy'):
            cmd.add_argument('image', type=Path)
        if name in ('watch', 'monitor'):
            cmd.add_argument('--count', type=int, default=10)
            cmd.add_argument('--interval', type=float, default=1)
        if name == 'monitor':
            cmd.add_argument('devices', nargs='+', help='Qualified X/Y/M/D/T/C labels; X/Y are octal')
        if name == 'device-write':
            cmd.add_argument('device', help='Internal M0..255 or D0..127')
            cmd.add_argument('value', type=int)
            cmd.add_argument('--expected-value', type=int, required=True)
            cmd.add_argument('--out-dir', type=Path, required=True)
            cmd.add_argument('--execute', action='store_true', help='STOP, backup, compare, one-shot write, verify, resume')
        if name in ('dump','compare','deploy'):
            cmd.add_argument('--experimental', action='store_true', help='Use the independently calibrated extended codec')
            cmd.add_argument('--full-region', action='store_true', help='Require the qualified 32,000-byte program layout; implies extended codec')
        if name == 'deploy':
            cmd.add_argument('--expected-sha256', required=True, help='SHA256 of live image expected before writes')
            cmd.add_argument('--execute', action='store_true', help='Without this flag only validate candidate offline')
            cmd.add_argument('--leave-stopped', action='store_true', help='Verify the image and leave CPU in STOP, including recovery; no automatic RUN')
            cmd.add_argument('--pad-to-region', action='store_true', help='Explicitly replace the rest of the selected region with 0xFF for a short extended image')
    args = p.parse_args()
    if args.command in ('watch','monitor'):
        import math
        if not 1 <= args.count <= 10000 or not math.isfinite(args.interval) or args.interval < 0:
            p.error('Use count 1..10000 and a finite nonnegative interval')
    if args.command == 'monitor':
        from .devices import device
        for label in args.devices:
            device(label)
    if args.command == 'device-write':
        from .devices import edit_request
        dev, request = edit_request(args.device,args.value)
        edit_request(args.device,args.expected_value)
        if not args.execute:
            print(json.dumps({'dry_run':True,'device':dev.name,'value':args.value,
                              'expected_value':args.expected_value,'wire_request_hex':request.hex(),
                              'temporary_stop':True,'persistent_force':False}))
            return
    if args.command == 'live-write':
        from . import live
        try:
            allow = live.AllowList.load(args.allow)
            plan = live.plan_label(args.device, args.value, allow)
        except live.LiveWriteRefused as exc:
            p.error(str(exc))
        if not args.execute:
            print(json.dumps({'dry_run': True, 'kind': plan.kind, 'device': plan.device, 'value': args.value,
                              'wire_request_hex': plan.request.hex(), 'readback_request_hex': plan.read_request.hex(),
                              'temporary_stop': False, 'persistent_force': False, 'qualified_on_hardware': False}))
            return
        if not args.port:
            p.error('--execute needs --port')
        if not args.wire_log:
            p.error('--execute needs --wire-log')
        args.wire_log.parent.mkdir(parents=True, exist_ok=True)
        with args.wire_log.open('x', encoding='utf-8', buffering=1) as log:
            session = Session.open(args.port, log)
            try:
                result = live.run_plan(session, plan, allow)
            except live.LiveWriteRefused as exc:
                p.error(str(exc))
            finally:
                session.close()
        print(json.dumps(result.as_dict()))
        if not result.ok:
            raise SystemExit(2)
        return
    if args.command == 'simulate':
        from .experimental_codec import parse_il as parse_sim_il
        from .sim import ScanSimulator
        model=ScanSimulator(parse_sim_il(args.source.read_text(encoding='utf-8')))
        if args.trace:
            frames=json.loads(args.trace.read_text(encoding='utf-8'))
            if not isinstance(frames,list) or (args.scans is not None and args.scans!=len(frames)):
                p.error('Trace must be a list matching --scans if specified')
        else:
            count=1 if args.scans is None else args.scans
            if not 1<=count<=100000:p.error('Use 1..100000 scans')
            frames=[{} for _ in range(count)]
        if not 1<=len(frames)<=100000:p.error('Use 1..100000 scan frames')
        for frame_row in frames:
            model.step(frame_row.get('inputs',{}),frame_row.get('dt_ms',args.dt_ms))
            values={}
            for name in args.watch:
                name=name.upper()
                values[name]=model.bit(name) if name[0] in 'XYMS' else model.read(name)
            print(json.dumps({'scan':model.scans,'time_ms':model.time_ms,'values':values}))
        return
    if args.command in ('compile-experimental','disassemble-experimental'):
        from . import experimental_codec as codec
        if args.command == 'compile-experimental':
            data,mapping=codec.assemble(codec.parse_il(args.source.read_text(encoding='utf-8')))
            args.output.write_bytes(data)
            args.output.with_suffix('.map.json').write_text(json.dumps(mapping,indent=2)+'\n',encoding='utf-8')
            print(json.dumps({'bytes':len(data),'sha256':digest(data),'evidence_level':'offline_oracle_only'}))
        else:
            instructions=codec.disassemble(args.source.read_bytes())
            args.output.write_text('\n'.join(map(str,instructions))+'\n',encoding='utf-8')
        return
    if args.command == 'catalog':
        text = resources.files('fx3u').joinpath('capabilities.json').read_text(encoding='utf-8')
        rows = json.loads(text)['instructions']
        if args.mnemonic:
            rows = [r for r in rows if r['mnemonic'] == args.mnemonic.upper()]
        if args.family:
            rows = [r for r in rows if args.family.casefold() in r['family'].casefold()]
        if args.supported:
            field = 'experimental_encoder' if args.experimental else 'current_encoder'
            rows = [r for r in rows if r.get(field,{}).get('status','unsupported') != 'unsupported']
        if args.experimental:
            from .experimental_codec import PROFILE
            for row in rows:
                row['encoding_profile'] = {m: PROFILE['instructions'].get(m, {'bit_forms':PROFILE['bit_forms'].get(m,[]),'special_form':True})
                    for m in row.get('experimental_encoder',{}).get('canonical_mnemonics',[])}
        print(json.dumps(rows, indent=2, ensure_ascii=True))
        if not rows:
            raise SystemExit(2)
        return
    if args.command == 'compile':
        data, source_map = assemble(parse_il(args.source.read_text(encoding='utf-8')))
        args.output.write_bytes(data)
        args.output.with_suffix('.map.json').write_text(json.dumps(source_map, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'sha256': digest(data), 'instructions': len(source_map),
                          'steps': source_map[-1]['step']+1}))
        return
    if args.command == 'compile-csv':
        from .dsl.gxworks_csv import parse_strict, validate_compiled_steps
        try:
            metadata, rows = parse_strict(str(args.source))
            instructions = [Instruction(r['mnemonic'], tuple(r['operands'])) for r in rows]
            if args.experimental:
                from .experimental_codec import assemble as csv_assembler
            else:
                csv_assembler = assemble
            data, mapping = csv_assembler(instructions)
            validate_compiled_steps(rows, mapping)
        except ValueError as exc:
            p.error(str(exc))
        args.output.write_bytes(data)
        args.output.with_suffix('.il').write_text('\n'.join(map(str, instructions))+'\n', encoding='utf-8')
        args.output.with_suffix('.map.json').write_text(json.dumps(mapping, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'source_sha256': digest(args.source.read_bytes()),
                          'image_sha256': digest(data), 'instructions': len(mapping),
                          'records_imported': metadata['records_imported'],
                          'records_rejected': metadata['records_rejected']}))
        return
    if args.command == 'fleet-watch':
        fleet.run(args, p)
        return
    if args.command in ('disassemble', 'xref'):
        decoder, encoder = disassemble, assemble
        if args.command == 'xref' and args.experimental:
            from .experimental_codec import disassemble as decoder, assemble as encoder
        ins = decoder(args.image.read_bytes())
        if args.command == 'disassemble':
            args.output.write_text('\n'.join(map(str, ins))+'\n', encoding='utf-8')
        else:
            from .analysis import query_usage
            try:
                report = query_usage(ins, args.device)
            except ValueError as exc:
                p.error(str(exc))
            _, mapping = encoder(ins)
            for row in report['hits'] + report['uncertainties']:
                location = mapping[row['instruction_index']]
                row.update(step=location['step'], line=location['line'])
            print(json.dumps(report, indent=2))
        return
    candidate = None
    use_extended_codec = getattr(args,'experimental',False) or getattr(args,'full_region',False)
    target_size = 32000 if getattr(args,'full_region',False) else REGION_SIZE
    if args.command == 'deploy':
        source_image = args.image.read_bytes()
        candidate = source_image
        candidate_decoder = disassemble
        if args.pad_to_region and not use_extended_codec:
            p.error('--pad-to-region requires --experimental or --full-region')
        if use_extended_codec:
            from .experimental_codec import disassemble as candidate_decoder
            if len(candidate) < target_size:
                if not args.pad_to_region:
                    p.error(f'Short image ({len(candidate)} bytes): use --pad-to-region to explicitly replace the remaining {target_size-len(candidate)} bytes with 0xFF')
                candidate = candidate.ljust(target_size, b'\xff')
        instructions = candidate_decoder(candidate)
        if len(candidate) != target_size:
            p.error(f'Deployment needs a complete {target_size}-byte region')
        from .client import image_readback_lower_bound
        candidate_info = {'source_bytes': len(source_image), 'candidate_bytes': len(candidate),
                          'source_sha256': digest(source_image), 'candidate_sha256': digest(candidate),
                          'padded': len(candidate) != len(source_image),
                          'leave_stopped': args.leave_stopped,
                          'verification_policy': 'full_image_in_stop',
                          'full_image_readback': image_readback_lower_bound(target_size)}
        if not args.execute:
            print(json.dumps({'dry_run': True, **candidate_info,
                              'instructions': len(instructions)}))
            return
        from sys import stderr
        minimum = candidate_info['full_image_readback']['response_data_min_seconds']
        print(f'Full-image verification in STOP: response data alone needs at least '
              f'{minimum:.2f} s at 9600 baud 7E1. This excludes writes, requests and '
              'delays; actual STOP duration is not hardware-qualified.', file=stderr, flush=True)
    args.wire_log.parent.mkdir(parents=True, exist_ok=True)
    with args.wire_log.open('x', encoding='utf-8', buffering=1) as log:
        if args.command == 'deploy':
            # Preserve input identity even if connection, deployment or recovery fails.
            input_report = args.wire_log.with_name(args.wire_log.name + '.input.json')
            with input_report.open('x', encoding='utf-8') as details:
                json.dump(candidate_info, details, indent=2)
                details.write('\n')
        session = Session.open(args.port, log)
        try:
            if getattr(args,'full_region',False):
                session.qualify_full_region()
            if args.command in ('status', 'watch'):
                count = args.count if args.command == 'watch' else 1
                for i in range(count):
                    print(json.dumps(session.snapshot()), flush=True)
                    if i+1 < count:
                        time.sleep(max(.1, args.interval))
            elif args.command == 'diagnostics':
                print(json.dumps(session.diagnostics()))
            elif args.command == 'monitor':
                from .devices import monitor
                for i in range(args.count):
                    print(json.dumps(monitor(session,args.devices)),flush=True)
                    if i+1 < args.count:
                        time.sleep(max(.1,args.interval))
            elif args.command == 'device-write':
                from .devices import edit
                print(json.dumps(edit(session,args.device,args.value,args.expected_value,args.out_dir)))
            elif args.command == 'dump':
                args.out_dir.mkdir(parents=True, exist_ok=False)
                data = session.image()
                (args.out_dir/'program.bin').write_bytes(data)
                # Raw backup survives even if this decoder cannot understand the code.
                info = {'sha256': digest(data), 'bytes': len(data), 'run': session.running(),
                        'scope': 'Qualified program region only; not parameters/retentive data/full PLC backup'}
                if getattr(args,'full_region',False):
                    (args.out_dir/'program-header.bin').write_bytes(session._program_header)
                    info.update(_full_region_verification(session, data))
                    info['verified'] = info['checksum_valid']
                (args.out_dir/'manifest.json').write_text(json.dumps(info, indent=2)+'\n', encoding='utf-8')
                if getattr(args,'full_region',False) and not info['verified']:
                    print(json.dumps(info))
                    raise SystemExit(2)
                if use_extended_codec:
                    from .experimental_codec import disassemble as dump_decoder
                else:
                    dump_decoder = disassemble
                ins = dump_decoder(data)
                (args.out_dir/'program.il').write_text('\n'.join(map(str, ins))+'\n', encoding='utf-8')
                print(json.dumps(info))
            elif args.command == 'compare':
                data = session.image()
                same = data == args.image.read_bytes()
                info = {'match': same, 'live_sha256': digest(data), 'run': session.running()}
                if getattr(args,'full_region',False):
                    info.update(_full_region_verification(session, data))
                    info['success'] = same and info['checksum_valid']
                print(json.dumps(info))
                if not info.get('success', same):
                    raise SystemExit(2)
            elif args.command == 'deploy':
                kwargs = {'leave_stopped': args.leave_stopped}
                if use_extended_codec:
                    kwargs['decoder'] = candidate_decoder
                result = session.deploy(candidate, args.expected_sha256, args.out_dir, **kwargs)
                result.update(candidate_info)
                (args.out_dir/'result.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
                print(json.dumps(result))
        finally:
            session.close()


if __name__ == '__main__':
    main()
