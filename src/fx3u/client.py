"""Single-owner programming-port session, proven Windows COM/FX3U profile."""
import ctypes
import hashlib
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .bytecode import REGION_SIZE, disassemble

BASE, BLOCK = 0x1805C, 254


def image_readback_lower_bound(size):
    """Wire minimum for response data only; never a STOP-duration estimate.

    Qualified transport is 9600 baud, 7E1: start + 7 data + parity + stop
    equals ten bits for each of the two ASCII HEX characters per image byte.
    Requests, framing, writes, adapter delays and recovery are excluded.
    """
    return {'image_bytes': size, 'ascii_hex_characters': size * 2,
            'baudrate': 9600, 'serial_format': '7E1', 'bits_per_character': 10,
            'response_data_min_seconds': size * 2 * 10 / 9600,
            'scope': 'response data only; excludes framing, requests, writes and delays',
            'hardware_stop_timing_qualified': False}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def frame(payload):
    body = payload.encode('ascii') + b'\x03'
    return b'\x02'+body+f'{sum(body)&255:02X}'.encode()


def payload(rx):
    if len(rx) < 5 or rx[:1] != b'\x02' or rx[-3:-2] != b'\x03':
        raise ValueError('Expected framed response')
    if rx[-2:] != f'{sum(rx[1:-2])&255:02X}'.encode():
        raise ValueError('Checksum mismatch')
    return rx[1:-3]


def data_bytes(rx, count):
    body = payload(rx)
    if len(body) != count*2:
        raise ValueError('Wrong response byte count')
    result = bytes.fromhex(body.decode('ascii'))
    if len(result) != count:
        raise ValueError('Invalid hex data')
    return result


class Session:
    """One lock per serial handle. Do not share an adapter with another process."""
    def __init__(self, port, log=None):
        self.port, self.log = port, log
        self.lock = threading.RLock()

    @classmethod
    def open(cls, name, log=None):
        if sys.platform != 'win32':
            raise RuntimeError('Live transport is qualified only with native Windows Python')
        import serial
        from serial import win32
        port = serial.Serial(name, baudrate=9600, bytesize=7, parity='E', stopbits=1,
                             timeout=.2, write_timeout=2)
        try:
            dcb = win32.DCB()
            dcb.DCBlength = ctypes.sizeof(dcb)
            if not win32.GetCommState(port._port_handle, ctypes.byref(dcb)):
                raise OSError('GetCommState failed')
            dcb.fOutxCtsFlow, dcb.fDtrControl, dcb.fRtsControl = 1, 0, 1
            if not win32.SetCommState(port._port_handle, ctypes.byref(dcb)):
                raise OSError('SetCommState failed')
            port.reset_input_buffer()  # Purge AFTER DCB, not before.
            session = cls(port, log)
            session.ack(b'\x05')
            if session.read(0x0E02, 2) != bytes.fromhex('f75e'):
                raise ValueError('CPU version differs from qualified 3.11 profile')
            return session
        except BaseException:
            port.close()
            raise

    def close(self):
        self.port.close()

    def exchange(self, tx):
        with self.lock:
            rx, error = bytearray(), None
            start = time.time_ns()
            deadline = time.monotonic()+3
            def exact(n):
                out = bytearray()
                while len(out) < n and time.monotonic() < deadline:
                    out.extend(self.port.read(n-len(out)))
                if len(out) != n:
                    raise TimeoutError('Incomplete response')
                return out
            try:
                self.port.dtr = True
                self.port.rts = True
                if self.port.write(tx) != len(tx):
                    raise OSError('Short write')
                rx.extend(exact(1))
                if rx == b'\x02':
                    while rx[-1] != 3 and len(rx) < 2048:
                        rx.extend(exact(1))
                    if rx[-1] != 3:
                        raise ValueError('Oversized response')
                    rx.extend(exact(2))
                    payload(rx)
                elif rx != b'\x06':
                    raise ValueError(f'NAK/unexpected reply: {rx.hex()}')
                return bytes(rx)
            except BaseException as exc:
                error = str(exc)
                raise
            finally:
                self.port.dtr = False
                self.port.rts = False
                if self.log:
                    self.log.write(json.dumps({'at_ns': start, 'finished_ns': time.time_ns(),
                                               'tx': tx.hex(), 'rx': rx.hex(), 'error': error})+'\n')
                    self.log.flush()

    def ack(self, tx):
        if self.exchange(tx) != b'\x06':
            raise ValueError('Expected ACK')

    def read(self, addr, count):
        if not 1 <= count <= BLOCK or not 0 <= addr <= 0xFFFF:
            raise ValueError('Legacy read range')
        return data_bytes(self.exchange(frame(f'0{addr:04X}{count:02X}')), count)

    @property
    def region_size(self):
        return getattr(self, '_program_size', REGION_SIZE)

    def qualify_full_region(self):
        from .layout import qualify_program_header
        with self.lock:
            header = data_bytes(self.exchange(frame('E0180005C')), 92)
            layout = qualify_program_header(header)
            if layout.base != BASE:
                raise ValueError('Qualified program base differs from this transport profile')
            self._program_size = layout.capacity
            self._program_header = header
            return layout

    def read_block(self, offset):
        if offset not in range(0, self.region_size, BLOCK):
            raise ValueError('Outside qualified program region')
        count = min(BLOCK, self.region_size-offset)
        return data_bytes(self.exchange(frame(f'E0{BASE+offset:05X}{count:02X}')), count)

    def image(self):
        with self.lock:
            return b''.join(self.read_block(i) for i in range(0, self.region_size, BLOCK))

    def write_block(self, offset, data):
        if offset not in range(0, self.region_size, BLOCK) or len(data) != min(BLOCK, self.region_size-offset):
            raise ValueError('Outside qualified program region')
        self.ack(frame(f'E1{BASE+offset:05X}{len(data):02X}{data.hex().upper()}'))

    def running(self):
        state = payload(self.exchange(frame('F50100060')))
        if state not in (b'0', b'1'):
            raise ValueError('Unknown CPU state')
        return state == b'1'

    def set_running(self, run):
        self.ack(frame('E82560' if run else 'E72560'))
        deadline = time.monotonic()+2
        while self.running() != run:
            if time.monotonic() >= deadline:
                diagnostics = self.read(0x0E78, 20).hex()
                raise ValueError(f'CPU state transition not confirmed; D8060..69 raw={diagnostics}')
            time.sleep(.05)

    def d(self, number):
        if not 0 <= number <= 127:
            raise ValueError('Outside qualified D range')
        return int.from_bytes(self.read(0x1000+2*number, 2), 'little')

    def diagnostics(self):
        """Special D word map from the FX programming-port memory layout.

        Meanings: Mitsubishi diagnostic devices manual (not protocol opcodes).
        D8004 is a relay number, not the error code itself.
        """
        with self.lock:
            registers = {}
            for first, count in [(8000, 18), (8060, 10)]:
                raw = self.read(0x0E00+2*(first-8000), count*2)
                registers.update({f'D{first+i}': int.from_bytes(raw[2*i:2*i+2], 'little')
                                  for i in range(count)})
            return {'special_registers_unsigned': registers,
                    'lowest_error_relay': registers['D8004'] or None,
                    'scan_ms': {key: registers[dev]/10 for key, dev in
                                [('current', 'D8010'), ('minimum', 'D8011'), ('maximum', 'D8012')]},
                    'run': self.running()}

    def snapshot(self):
        with self.lock:
            started = datetime.now(timezone.utc).isoformat()
            bits = {}
            for typ, addr, count in [('X', 0x80, 24), ('Y', 0xA0, 24),
                                     ('M', 0x100, 256), ('T', 0xC0, 49)]:
                raw = self.read(addr, (count+7)//8)
                for i in range(count):
                    name = typ+(f'{i:03o}' if typ in 'XY' else str(i))
                    bits[name] = bool(raw[i//8] & (1 << (i%8)))
            registers = {}
            for first, count in [(0, 24), (100, 28)]:
                raw = self.read(0x1000+2*first, count*2)
                registers.update({f'D{first+i}': int.from_bytes(raw[2*i:2*i+2], 'little')
                                  for i in range(count)})
            running = self.running()
            timer_values = self.read(0x800, 98).hex()
            counter = int.from_bytes(self.read(0xA00, 2), 'little')
            return {'started_utc': started, 'finished_utc': datetime.now(timezone.utc).isoformat(),
                    'atomic': False, 'run': running, 'bits': bits, 'registers': registers,
                    'timer_values_raw_hex': timer_values,
                    'counter_values_unsigned': {'C0': counter}}

    def deploy(self, candidate, expected_sha, out_dir, *, decoder=None, leave_stopped=False):
        """RAM only, same fixed region. Compare the full image in STOP before RUN.

        No automatic retry of an uncertain program write. On failure restore the
        fresh preflight image and compare its bytes before RUN. Failed recovery
        attempts STOP once more, then independently reports STOP/RUN/unknown;
        a broken connection cannot guarantee the final CPU mode.

        leave_stopped also issues and verifies STOP when the image is unchanged,
        and suppresses RUN during recovery. The CPU-maintained full-region sum
        remains deferred until RUN. Verified STOP does not establish machine safety.

        Timings cover host-observed STOP requests through confirmed RUN, including
        uncertain CPU time. Phase intervals can belong to one continuous stop;
        unresumed_stop_elapsed_seconds is an open observation, not total downtime.
        """
        decoder = disassemble if decoder is None else decoder
        if len(candidate) != self.region_size:
            raise ValueError('Wrong region size')
        decoder(candidate)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=False)
        with self.lock:
            if self.region_size != REGION_SIZE:
                self.qualify_full_region()
                (out_dir/'program-header.bin').write_bytes(self._program_header)
            before = self.image()
            (out_dir/'before.bin').write_bytes(before)
            if digest(before) != expected_sha:
                raise ValueError('Preflight differs from expected image; no write performed')
            decoder(before)
            if self.region_size != REGION_SIZE:
                from .layout import validate_program_checksum
                validate_program_checksum(self._program_header, before)
            if not self.running():
                raise ValueError('CPU must initially be RUN')
            dirty = [i for i in range(0, self.region_size, BLOCK)
                     if before[i:i+BLOCK] != candidate[i:i+BLOCK]]
            report = {'ok': False, 'before_sha256': digest(before),
                      'candidate_sha256': digest(candidate), 'dirty_offsets': dirty,
                      'ram_only': True, 'leave_stopped': leave_stopped,
                      'pre_run_image_verified': False,
                      'verification_policy': 'full_image_in_stop',
                      'full_image_readback': image_readback_lower_bound(self.region_size),
                      'stop_intervals': [], 'stop_request_to_run_seconds': [],
                      'stop_timing_scope': 'host-observed STOP request to confirmed RUN; '
                      'phase intervals may share one continuous STOP; uncertain CPU time is included'}
            if self.region_size != REGION_SIZE:
                report['checksum_status'] = 'not_verified'
            touched = False
            interval = None
            unresumed_stop_started = None

            def start_interval(phase):
                nonlocal interval, unresumed_stop_started
                interval = {'phase': phase, 'started': time.monotonic(),
                            'stop_confirmed': False}
                if unresumed_stop_started is None:
                    unresumed_stop_started = interval['started']

            def finish_interval(end):
                nonlocal interval, unresumed_stop_started
                finished = time.monotonic()
                seconds = finished - interval.pop('started')
                run_confirmed = end in ('run_confirmed', 'final_status_run')
                report['stop_intervals'].append({**interval, 'seconds': seconds,
                                                'end': end,
                                                'run_confirmed': run_confirmed})
                if run_confirmed:
                    report['stop_request_to_run_seconds'].append(finished - unresumed_stop_started)
                    unresumed_stop_started = None
                interval = None
                return seconds

            try:
                if dirty or leave_stopped:
                    touched = True
                    start_interval('deploy')
                    self.set_running(False)
                    if self.running():
                        raise ValueError('STOP verification failed before program access')
                    interval['stop_confirmed'] = True
                    for i in dirty:
                        self.write_block(i, candidate[i:i+BLOCK])
                        if self.read_block(i) != candidate[i:i+BLOCK]:
                            raise ValueError('Block readback differs')
                    stopped_image = self.image()
                    (out_dir/'stopped.bin').write_bytes(stopped_image)
                    if stopped_image != candidate:
                        raise ValueError('Stopped image differs')
                    report['pre_run_image_verified'] = True
                    if not leave_stopped:
                        self.set_running(True)
                        report['stop_seconds'] = finish_interval('run_confirmed')
                if self.region_size != REGION_SIZE:
                    self.qualify_full_region()
                # There has been no write or RUN since the complete stopped read.
                # Avoid another full-region transfer while deliberately in STOP.
                after = stopped_image if leave_stopped else self.image()
                if self.region_size != REGION_SIZE:
                    if leave_stopped:
                        report['checksum_status'] = 'deferred_until_run'
                    else:
                        validate_program_checksum(self._program_header, after)
                        report['checksum_status'] = 'verified_after_run'
                    (out_dir/'after-program-header.bin').write_bytes(self._program_header)
                (out_dir/'after.bin').write_bytes(after)
                expected_run = not leave_stopped
                if after != candidate or self.running() != expected_run:
                    mode = 'STOP' if leave_stopped else 'RUN'
                    raise ValueError(f'Final image/{mode} verification failed')
                if leave_stopped:
                    report['stop_verification_seconds'] = finish_interval('stopped_at_return')
                report['ok'] = True
                report['final_cpu_mode'] = 'STOP' if leave_stopped else 'RUN'
            except BaseException as exc:
                report['error'] = str(exc)
                if interval is not None:
                    report['stop_attempt_seconds'] = finish_interval('failure')
                if touched:
                    try:
                        start_interval('recovery')
                        self.set_running(False)
                        if self.running():
                            raise ValueError('Recovery STOP verification failed before program access')
                        interval['stop_confirmed'] = True
                        # Restore all possibly touched blocks, including uncertain writes.
                        for i in dirty:
                            self.write_block(i, before[i:i+BLOCK])
                        restored = self.image()
                        (out_dir/'recovery.bin').write_bytes(restored)
                        if restored != before:
                            raise ValueError('Recovery image differs')
                        if not leave_stopped:
                            self.set_running(True)
                            report['recovery_stop_seconds'] = finish_interval('run_confirmed')
                        if self.region_size != REGION_SIZE:
                            self.qualify_full_region()
                            if leave_stopped:
                                report['recovery_checksum_status'] = 'deferred_until_run'
                            else:
                                validate_program_checksum(self._program_header, restored)
                                report['recovery_checksum_status'] = 'verified_after_run'
                        if leave_stopped and self.running():
                            raise ValueError('Recovery STOP verification failed')
                        if leave_stopped:
                            report['recovery_stop_verification_seconds'] = finish_interval('stopped_at_return')
                        report['recovered'] = True
                    except BaseException as recovery:
                        report['recovery_error'] = str(recovery)
                        if interval is not None:
                            report['recovery_stop_attempt_seconds'] = finish_interval('failure')
                        report['recovery_stop_attempted'] = True
                        start_interval('final_stop')
                        try:
                            self.set_running(False)
                        except BaseException as stop:
                            report['recovery_stop_error'] = str(stop)
                # Do not infer a final mode from a command ACK or its failure.
                try:
                    report['final_cpu_mode'] = 'RUN' if self.running() else 'STOP'
                except BaseException as state:
                    report['final_cpu_mode'] = 'unknown'
                    report['final_cpu_mode_error'] = str(state)
                if interval is not None:
                    interval['stop_confirmed'] = report['final_cpu_mode'] == 'STOP'
                    finish_interval('final_status_' + report['final_cpu_mode'].lower())
                if leave_stopped and report['final_cpu_mode'] != 'STOP':
                    report.pop('recovered', None)
                raise
            finally:
                if unresumed_stop_started is not None:
                    # STOP deliberately continuing or CPU mode unknown: this is
                    # elapsed observation only, never a completed downtime.
                    report['unresumed_stop_elapsed_seconds'] = time.monotonic() - unresumed_stop_started
                (out_dir/'result.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
            return report
