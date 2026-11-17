import io
import unittest
from pathlib import Path
from unittest.mock import patch

from fx3u.bytecode import Instruction, assemble, disassemble, parse_il
from fx3u.client import Session, data_bytes, frame, payload

class BytecodeTests(unittest.TestCase):
    def test_new_code_and_boundaries(self):
        ins = parse_il('LDI M239\nMOV K123 D120\nEND')
        self.assertEqual(ins, disassemble(assemble(ins)[0]))
        self.assertEqual(bytes.fromhex('ef3828007b800080f08600860f00'), assemble(ins, False)[0])
        for text in ['LD X008\nEND', 'LD M256\nEND', 'MOV K256 D0\nEND',
                     'MOV K1 D128\nEND', 'FOR K1\nEND', 'LD M0', 'END\nEND']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                assemble(parse_il(text))
        with self.assertRaises(ValueError):
            assemble(parse_il('LD M0\n'*3100+'END'))
        for data in [b'\x28\x00', b'\x12\x34', b'\x00', b'\x0f\x00\x00\x00',
                     bytes.fromhex('4000018600860f00')]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                disassemble(data)


class Port:
    def __init__(self, response):
        self.response = bytearray(response)
        self.dtr = self.rts = False
        self.signals = []
    def write(self, data):
        self.signals.append((self.dtr, self.rts))
        return len(data)
    def read(self, count):
        self.signals.append((self.dtr, self.rts))
        # Deliberately fragment even the checksum into one-byte reads.
        out = self.response[:1]
        del self.response[:1]
        return out


class TransportTests(unittest.TestCase):
    def test_fragmented_and_lines_held(self):
        port = Port(frame('0102'))
        session = Session(port, io.StringIO())
        self.assertEqual(b'\x01\x02', data_bytes(session.exchange(frame('0008002')), 2))
        self.assertTrue(all(a and b for a, b in port.signals))
        self.assertFalse(port.dtr or port.rts)

    def test_bad_checksum_nak_partial_timeout(self):
        for response in (b'\x15', frame('00')[:-1]+b'Z', b'\x02ab'):
            port = Port(response)
            with patch('fx3u.client.time.monotonic', side_effect=range(100)):
                with self.assertRaises((ValueError, TimeoutError)):
                    Session(port).exchange(b'\x05')
            self.assertFalse(port.dtr or port.rts)

    def test_state_not_hex(self):
        self.assertEqual(b'1', payload(frame('1')))
        with self.assertRaises(ValueError):
            data_bytes(frame('1'), 1)


class DeployTests(unittest.TestCase):
    def test_diff_only_and_uncertain_write_recovery(self):
        import tempfile
        import threading
        from fx3u.client import BLOCK, digest

        class MemorySession(Session):
            def __init__(self, image, fail=False):
                self.memory = bytearray(image)
                self.lock = threading.RLock()
                self.run = True
                self.writes = []
                self.fail = fail
            def image(self):
                return bytes(self.memory)
            def running(self):
                return self.run
            def set_running(self, run):
                self.run = run
            def write_block(self, offset, data):
                self.writes.append(offset)
                self.memory[offset:offset+BLOCK] = data
                if self.fail:
                    self.fail = False
                    raise TimeoutError('Write happened, ACK lost')
            def read_block(self, offset):
                return bytes(self.memory[offset:offset+BLOCK])
        original = assemble(parse_il('END'))[0]
        candidate = assemble(parse_il('LDI M239\nMOV K123 D120\nEND'))[0]
        for fail in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                s = MemorySession(original, fail)
                if fail:
                    with self.assertRaises(TimeoutError):
                        s.deploy(candidate, digest(original), Path(tmp)/'trial')
                    self.assertEqual(original, s.image())
                    self.assertEqual([0, 0], s.writes)
                else:
                    result = s.deploy(candidate, digest(original), Path(tmp)/'trial')
                    self.assertTrue(result['ok'])
                    self.assertEqual(candidate, s.image())
                    self.assertEqual([0], s.writes)
                self.assertTrue(s.run)
        with tempfile.TemporaryDirectory() as tmp:
            s = MemorySession(original)
            with self.assertRaises(ValueError):
                s.deploy(candidate, 'bad sha', Path(tmp)/'trial')
            self.assertEqual([], s.writes)


class StateTransitionTests(unittest.TestCase):
    def test_run_confirmation_waits_for_cpu(self):
        from unittest.mock import Mock
        session = Session(None)
        session.ack = Mock()
        session.running = Mock(side_effect=[False, False, True])
        with patch('fx3u.client.time.sleep') as sleep:
            session.set_running(True)
        self.assertEqual(2, sleep.call_count)
        session.ack.assert_called_once_with(frame('E82560'))


class SnapshotTests(unittest.TestCase):
    def test_device_boundaries_and_complete_time_window(self):
        import threading
        import time
        from datetime import datetime, timezone
        from fx3u.client import Session

        class SnapshotSession(Session):
            def __init__(self):
                self.lock = threading.RLock()
                self.counter_finished = None
            def running(self):
                return True
            def read(self, addr, count):
                data = bytearray(count)
                if addr == 0x80:
                    data[0] = 128  # X007
                    data[1] = 1    # X010
                    data[2] = 128  # X027
                if addr == 0x100:
                    data[239//8] |= 1 << (239%8)
                    data[249//8] |= 1 << (249%8)
                if addr == 0xC0:
                    data[6] = 1    # T48
                if addr == 0xA00:
                    data[:2] = b'\x0a\x00'
                    time.sleep(.001)
                    self.counter_finished = datetime.now(timezone.utc)
                return bytes(data)
        session = SnapshotSession()
        result = session.snapshot()
        for name in ['X007', 'X010', 'X027', 'M239', 'M249', 'T48']:
            self.assertTrue(result['bits'][name])
        for name in ['X006', 'X011', 'M238', 'M250', 'T47']:
            self.assertFalse(result['bits'][name])
        self.assertEqual(10, result['counter_values_unsigned']['C0'])
        self.assertGreaterEqual(datetime.fromisoformat(result['finished_utc']), session.counter_finished)
        self.assertFalse(result['atomic'])


class WriteGateGuardTests(unittest.TestCase):
    """Guards the write gate against refactors (review A7)."""

    def test_open_refuses_other_platforms_and_cpus(self):
        import sys as _sys
        from unittest.mock import MagicMock
        with patch.object(_sys, 'platform', 'linux'), self.assertRaises(RuntimeError):
            Session.open('COM1')
        # Windows path with a fake port whose CPU version reply is not the qualified one.
        fake_serial = MagicMock()
        port = fake_serial.Serial.return_value
        win32 = MagicMock()
        win32.GetCommState.return_value = win32.SetCommState.return_value = 1
        fake_serial.win32 = win32
        with patch.object(_sys, 'platform', 'win32'), \
             patch.dict(_sys.modules, {'serial': fake_serial, 'serial.win32': win32}), \
             patch('fx3u.client.ctypes.byref'), patch('fx3u.client.ctypes.sizeof', return_value=1), \
             patch.object(Session, 'ack'), patch.object(Session, 'read', return_value=bytes.fromhex('0000')):
            with self.assertRaises(ValueError):
                Session.open('COM1')
        port.close.assert_called_once()

    def test_backup_written_before_any_write(self):
        import tempfile
        import threading
        from fx3u.client import BLOCK, digest
        events = []

        class S(Session):
            def __init__(self, image):
                self.lock, self.memory, self.out = threading.RLock(), bytearray(image), None
                self.run = True
            def image(self):
                events.append(('read', (self.out/'before.bin').exists() if self.out else None))
                return bytes(self.memory)
            def running(self):
                return self.run
            def set_running(self, run):
                events.append(('stop' if not run else 'run', (self.out/'before.bin').exists()))
                self.run = run
            def write_block(self, offset, data):
                events.append(('write', (self.out/'before.bin').exists()))
                self.memory[offset:offset+BLOCK] = data
            def read_block(self, offset):
                return bytes(self.memory[offset:offset+BLOCK])
        original = assemble(parse_il('END'))[0]
        candidate = assemble(parse_il('LDI M239\nMOV K123 D120\nEND'))[0]
        with tempfile.TemporaryDirectory() as tmp:
            s = S(original)
            s.out = Path(tmp)/'trial'
            s.deploy(candidate, digest(original), s.out)
        writes = [e for e in events if e[0] in ('stop', 'write')]
        self.assertTrue(writes)
        self.assertTrue(all(backup for _, backup in writes))
