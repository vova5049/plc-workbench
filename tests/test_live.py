"""Allow-listed FORCE / device writes in RUN (no STOP, no RUN command, no retries).

Everything here runs against a simulated transport: `FakePLC` subclasses the real `Session`
(so `running()` / `read()` use the real framing code) and replaces only `exchange()`. No test may
open a serial port. These tests provide host evidence, not hardware qualification.
"""

import json
import sys
from dataclasses import replace

import pytest

from fx3u import live
from fx3u.client import Session, frame, payload
from fx3u.live import AllowList, LiveWriteRefused, force_bit, read_y_block, write_units

ALLOW = AllowList(["M240-M255", "D24-D49"])
STATUS = frame("F50100060")


class FakePLC(Session):
    """Programming-port model: memory, RUN flag, scripted write replies; records every tx frame."""

    def __init__(self, *, run=True, reply="ack", consume=False, read_fail=False):
        super().__init__(port=None)
        self.memory = bytearray(0x2000)
        self.run = run
        self.reply = reply  # ack | nak | lost_ack | no_reply | garbage
        self.consume = consume  # ladder clears the written M bit before the read-back
        self.read_fail = read_fail
        self.sent = []

    def close(self):
        pass

    def set_running(self, run):  # pragma: no cover - must never be called
        raise AssertionError("set_running called: live writes must not issue STOP/RUN")

    def write_frames(self):
        return [tx for tx in self.sent if payload(tx)[:1] in (b"1", b"7", b"8")]

    def exchange(self, tx):
        self.sent.append(tx)
        body = payload(tx).decode()
        if body == "F50100060":
            return frame("1" if self.run else "0")
        if body[0] == "0":
            if self.read_fail and len(self.write_frames()) > 0:
                raise TimeoutError("Incomplete response")
            address, count = int(body[1:5], 16), int(body[5:7], 16)
            return frame(bytes(self.memory[address : address + count]).hex().upper())
        if body[0] in "178":
            if self.reply == "nak":
                raise ValueError("NAK/unexpected reply: 15")
            if self.reply == "garbage":
                raise ValueError("NAK/unexpected reply: 41")
            if self.reply == "no_reply":
                raise TimeoutError("Incomplete response")
            self.apply(body)
            if self.reply == "lost_ack":
                raise TimeoutError("Incomplete response")
            return b"\x06"
        raise AssertionError(f"unexpected frame {body}")

    def apply(self, body):
        if body[0] in "78":
            address = int.from_bytes(bytes.fromhex(body[1:]), "little")
            if address >= 0x800:  # M bit
                bit, base = address - 0x800, 0x100
            else:  # Y bit (0x0500 + index)
                assert 0x500 <= address < 0x518, hex(address)
                bit, base = address - 0x500, 0xA0
            byte, mask = base + bit // 8, 1 << (bit % 8)
            if body[0] == "7":
                self.memory[byte] |= mask
            else:
                self.memory[byte] &= ~mask
            if self.consume:
                self.memory[byte] &= ~mask
        else:
            address, count = int(body[1:5], 16), int(body[5:7], 16)
            self.memory[address : address + count] = bytes.fromhex(body[7:])


# --------------------------------------------------------------------------- wire format


def test_force_bit_on_frame_and_readback():
    plc = FakePLC()
    result = force_bit(plc, "M250", True, ALLOW)
    assert plc.sent[0] == STATUS  # RUN checked first
    assert plc.sent[1] == frame("7FA08")  # 0x0800 + 250 = 0x08FA, little endian
    assert plc.sent[2] == frame("0011F01")  # read-back of the M byte 0x0100 + 250 // 8
    assert len(plc.sent) == 3
    assert result.ok and result.status == "ok" and result.acked
    assert plc.memory[0x11F] == 0b0000_0100


def test_force_bit_off_frame():
    plc = FakePLC()
    plc.memory[0x11F] = 0xFF
    result = force_bit(plc, "M250", False, ALLOW)
    assert frame("8FA08") in plc.sent
    assert result.ok and plc.memory[0x11F] == 0b1111_1011


def test_write_word_frame_and_readback():
    plc = FakePLC()
    result = write_units(plc, "D44", [0xA5A5], ALLOW)
    assert plc.sent[0] == STATUS
    assert plc.sent[1] == frame("1105802A5A5")
    assert plc.sent[2] == frame("0105802")
    assert result.ok and result.status == "ok"
    assert bytes(plc.memory[0x1058:0x105A]) == b"\xa5\xa5"


def test_write_words_little_endian_and_multiple():
    plc = FakePLC()
    result = write_units(plc, "D30", [0x1234, 0x00FF], ALLOW)
    assert plc.write_frames() == [frame("1103C043412FF00")]
    assert result.ok and bytes(plc.memory[0x103C:0x1040]) == bytes.fromhex("3412FF00")


def test_write_m_byte_frame():
    plc = FakePLC()
    result = write_units(plc, "M248", [0xA5], ALLOW)
    assert plc.sent[1] == frame("1011F01A5")  # byte 0x0100 + 248 // 8
    assert plc.sent[2] == frame("0011F01")
    assert result.ok and plc.memory[0x11F] == 0xA5


def test_write_m_bytes_first_aligned_byte():
    plc = FakePLC()
    result = write_units(plc, "M240", [0x01, 0x80], ALLOW)
    assert plc.write_frames() == [frame("1011E0201" + "80")]
    assert result.ok


def test_result_is_json_serialisable_and_marks_qualification():
    result = force_bit(FakePLC(), "M250", True, ALLOW)
    data = json.loads(json.dumps(result.as_dict()))
    assert data["status"] == "ok" and data["retried"] is False
    assert data["qualified_on_hardware"] is False


def test_boundary_elements_allowed():
    plc = FakePLC()
    assert force_bit(plc, "M240", True, ALLOW).ok
    assert force_bit(plc, "M255", True, ALLOW).ok
    assert write_units(plc, "D24", [1], ALLOW).ok
    assert write_units(plc, "D49", [1], ALLOW).ok
    assert write_units(plc, "D48", [1, 2], ALLOW).ok


# --------------------------------------------------------------------------- refusals before TX


@pytest.mark.parametrize("field,value", [
    ("request", frame("70008")),  # FORCE M0 hidden behind an allowed M250 label.
    ("request", frame("E72560")),  # A raw CPU command must not become a live write.
    ("read_request", frame("0100002")),
    ("read_address", 0x1000),
    ("read_length", 2),
    ("device", "M251"),
    ("device", "M8000"),
    ("kind", "write"),
    ("kind", "raw"),
    ("unit", "byte"),
    ("bit", 0),
    ("expected", False),
])
def test_forged_plan_refused_before_even_status_read(field, value):
    plc = FakePLC()
    plan = replace(live.plan_force("M250", True, ALLOW), **{field: value})
    with pytest.raises(LiveWriteRefused):
        live.run_plan(plc, plan, ALLOW)
    assert plc.sent == []


@pytest.mark.parametrize("allow", [None, [], "M240-M255", {"allow": ["M240-M255"]}])
def test_execution_requires_a_valid_allowlist_before_io(allow):
    plc = FakePLC()
    plan = live.plan_force("M250", True, ALLOW)
    with pytest.raises(LiveWriteRefused):
        live.run_plan(plc, plan, allow)
    assert plc.sent == []


def test_execution_allowlist_argument_cannot_be_omitted():
    plc = FakePLC()
    with pytest.raises(TypeError):
        live.run_plan(plc, live.plan_force("M250", True, ALLOW))
    assert plc.sent == []


@pytest.mark.parametrize("plan,allow", [
    (live.plan_force("M250", True, ALLOW), AllowList(["M251"])),
    (live.plan_write("D48", [1, 2], ALLOW), AllowList(["D48"])),
    (live.plan_write("M240", [1, 2], ALLOW), AllowList(["M240-M254"])),
    (live.plan_force("Y10", True, AllowList(["Y0-Y27"])), AllowList(["Y0-Y7"])),
])
def test_execution_rechecks_current_policy_and_entire_span(plan, allow):
    plc = FakePLC()
    with pytest.raises(LiveWriteRefused, match="allow"):
        live.run_plan(plc, plan, allow)
    assert plc.sent == []


def test_mutated_write_values_without_matching_frame_are_refused():
    plc = FakePLC()
    plan = live.plan_write("D44", [7], ALLOW)
    plan.expected.append(8)
    with pytest.raises(LiveWriteRefused):
        live.run_plan(plc, plan, ALLOW)
    assert plc.sent == []


@pytest.mark.parametrize("plan", [None, {}, "raw frame"])
def test_execution_refuses_non_plan_data_before_io(plan):
    plc = FakePLC()
    with pytest.raises(LiveWriteRefused):
        live.run_plan(plc, plan, ALLOW)
    assert plc.sent == []


def test_execution_uses_rebuilt_snapshot_not_shared_mutable_expected():
    plc = FakePLC()
    plan = live.plan_write("D44", [7], ALLOW)
    original_exchange = plc.exchange

    def exchange(tx):
        if tx == STATUS:
            plan.expected[0] = 8
        return original_exchange(tx)

    plc.exchange = exchange
    result = live.run_plan(plc, plan, ALLOW)
    assert result.ok and result.expected == [7] and result.readback == [7]
    assert plan.expected == [8]
    assert plc.sent == [STATUS, frame("11058020700"), frame("0105802")]


def test_canonical_hand_built_plan_still_needs_execution_policy():
    plc = FakePLC()
    expected = live.plan_force("M250", True, ALLOW)
    copied = live.Plan(**expected.__dict__)
    result = live.run_plan(plc, copied, ALLOW)
    assert result.ok and result.qualified_on_hardware is False
    assert plc.sent == [STATUS, expected.request, expected.read_request]


REFUSED = [
    ("force", "M239", True),  # outside list
    ("force", "M8000", True),  # special M
    ("force", "M8002", True),
    ("force", "M256", True),  # beyond qualified M range
    ("force", "X0", True),
    ("force", "Y1", True),
    ("force", "T0", True),
    ("force", "C0", True),
    ("force", "S0", True),
    ("force", "D44", True),  # a word is not a bit
    ("force", "m250 ", True),  # not a clean label
    ("force", "M-1", True),
    ("force", "M", True),
    ("force", "M250", 2),  # not a bit value
    ("force", "M250", "1"),
    ("force", "M250", None),
    ("units", "D23", [1]),
    ("units", "D50", [1]),
    ("units", "D8000", [1]),
    ("units", "D8062", [1]),
    ("units", "D128", [1]),
    ("units", "X0", [1]),
    ("units", "Y0", [1]),
    ("units", "T0", [1]),
    ("units", "C0", [1]),
    ("units", "M8000", [1]),
    ("units", "M239", [1]),  # byte start outside list
    ("units", "M249", [1]),  # not aligned to 8
    ("units", "M250", [1]),
    ("units", "M255", [1]),
    ("units", "D44", []),  # nothing to write
    ("units", "D44", [65536]),
    ("units", "D44", [-1]),
    ("units", "D44", [True]),
    ("units", "D44", ["1"]),
    ("units", "D44", 0x1234),  # not a sequence
    ("units", "M248", [256]),
    ("units", "D24", list(range(33))),  # too long
]


@pytest.mark.parametrize("kind,label,value", REFUSED, ids=lambda v: repr(v))
def test_refused_before_any_transmission(kind, label, value):
    plc = FakePLC()
    with pytest.raises(LiveWriteRefused):
        if kind == "force":
            force_bit(plc, label, value, ALLOW)
        else:
            write_units(plc, label, value, ALLOW)
    assert plc.sent == []


def test_address_outside_allowlist_is_refused_before_transmission():
    plc = FakePLC()
    only_d = AllowList(["D24-D49"])
    with pytest.raises(LiveWriteRefused, match="allow"):
        force_bit(plc, "M250", True, only_d)
    only_m = AllowList(["M240-M255"])
    with pytest.raises(LiveWriteRefused, match="allow"):
        write_units(plc, "D44", [1], only_m)
    assert plc.sent == []


@pytest.mark.parametrize(
    "label,values",
    [
        ("D49", [1, 2]),  # D49, D50
        ("D48", [1, 2, 3]),
        ("D23", [1, 2]),  # D23, D24
        ("D0", [1] * 30),
    ],
)
def test_range_exceeding_allowlist_by_one_element_is_refused(label, values):
    plc = FakePLC()
    with pytest.raises(LiveWriteRefused, match="allow"):
        write_units(plc, label, values, ALLOW)
    assert plc.sent == []


def test_m_byte_span_must_be_fully_allowed():
    plc = FakePLC()
    partial = AllowList(["M248-M254"])  # M255 missing: the byte write would touch it
    with pytest.raises(LiveWriteRefused, match="allow"):
        write_units(plc, "M248", [0xFF], partial)
    assert plc.sent == []
    with pytest.raises(LiveWriteRefused, match="allow"):
        write_units(plc, "M248", [0x01, 0x02], ALLOW)  # second byte = M256..M263
    assert plc.sent == []


def test_gap_in_allowlist_inside_the_span_is_refused():
    plc = FakePLC()
    gap = AllowList(["D24-D30", "D32-D49"])  # D31 not allowed
    with pytest.raises(LiveWriteRefused, match="allow"):
        write_units(plc, "D30", [1, 2, 3], gap)
    assert plc.sent == []


@pytest.mark.parametrize("allow", [None, [], "M240-M255", {"allow": ["M240-M255"]}])
def test_missing_or_empty_allowlist_means_refusal(allow):
    plc = FakePLC()
    with pytest.raises(LiveWriteRefused):
        force_bit(plc, "M250", True, allow)
    with pytest.raises(LiveWriteRefused):
        write_units(plc, "D44", [1], allow)
    assert plc.sent == []


def test_empty_allowlist_object_is_refused():
    plc = FakePLC()
    with pytest.raises(LiveWriteRefused):
        force_bit(plc, "M250", True, AllowList([]))
    assert plc.sent == []


# --------------------------------------------------------------------------- allow-list file


def write_allow(tmp_path, data):
    path = tmp_path / "allow.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def test_allowlist_load_ok(tmp_path):
    allow = AllowList.load(write_allow(tmp_path, {"allow": ["M240-M255", "D24-D49", "m250", "d44"]}))
    assert allow.permits("M", 240, 16) and allow.permits("D", 24, 26)
    assert not allow.permits("M", 239, 1) and not allow.permits("D", 24, 27)
    assert not allow.permits("Y", 0, 1)


@pytest.mark.parametrize(
    "data",
    [
        {"allow": []},
        {},
        {"allow": "M240-M255"},
        {"allow": ["M240-M255"], "comment": "extra keys are not accepted"},
        {"allow": [""]},
        {"allow": [5]},
        {"allow": ["Y8"]},  # octal numbering: no digit 8
        {"allow": ["Y30"]},  # Y0..Y27 octal only
        {"allow": ["Y0-Y30"]},
        {"allow": ["Y27-Y0"]},
        {"allow": ["Y0-M5"]},
        {"allow": ["X0"]},
        {"allow": ["T0-T10"]},
        {"allow": ["D8000-D8010"]},
        {"allow": ["M8000"]},
        {"allow": ["M0-M256"]},
        {"allow": ["D0-D128"]},
        {"allow": ["M255-M240"]},
        {"allow": ["M240-D49"]},
        {"allow": ["M240..M255"]},
        {"allow": ["M"]},
        ["M240-M255"],
        "not json {",
        "",
    ],
    ids=repr,
)
def test_allowlist_load_rejects_bad_files(tmp_path, data):
    with pytest.raises(LiveWriteRefused):
        AllowList.load(write_allow(tmp_path, data))


def test_allowlist_missing_file(tmp_path):
    with pytest.raises(LiveWriteRefused):
        AllowList.load(tmp_path / "nope.json")


# --------------------------------------------------------------------------- Y outputs

YALLOW = AllowList(["Y0-Y7", "M240-M255"])


def test_force_y1_on_matches_documented_frame():
    plc = FakePLC()
    result = force_bit(plc, "Y1", True, YALLOW)
    assert plc.sent == [STATUS, frame("70105"), frame("000A001")]  # FORCE Y1 = 0x0501, image byte 0xA0
    assert result.ok and result.device == "Y1" and plc.memory[0xA0] == 0b10


def test_force_y_off_keeps_neighbours():
    plc = FakePLC()
    plc.memory[0xA0] = 0xFF
    result = force_bit(plc, "Y3", False, YALLOW)
    assert frame("80305") in plc.sent and result.ok and plc.memory[0xA0] == 0xF7


def test_force_y_octal_numbering_unqualified_addresses():
    allow = AllowList(["Y0-Y27"])
    plc = FakePLC()
    assert force_bit(plc, "Y10", True, allow).ok  # octal 10 = index 8 -> 0x0508, image byte 0xA1
    assert plc.sent[1] == frame("70805") and plc.sent[2] == frame("000A101") and plc.memory[0xA1] == 1
    assert force_bit(plc, "Y27", True, allow).ok  # index 23 -> 0x0517, byte 0xA2 bit 7
    assert frame("71705") in plc.sent and plc.memory[0xA2] == 0x80


@pytest.mark.parametrize("label", ["Y8", "Y9", "Y30", "Y31", "Y100", "X0", "X10", "Y01", "Y-1", "Y"])
def test_y_labels_outside_octal_range_refused_before_tx(label):
    plc = FakePLC()
    with pytest.raises(LiveWriteRefused):
        force_bit(plc, label, True, AllowList(["Y0-Y27"]))
    assert plc.sent == []


def test_y_outside_allowlist_refused_before_tx():
    plc = FakePLC()
    with pytest.raises(LiveWriteRefused, match="allow"):
        force_bit(plc, "Y10", True, YALLOW)  # only Y0..Y7 allowed
    with pytest.raises(LiveWriteRefused, match="allow"):
        force_bit(plc, "Y1", True, ALLOW)  # list without any Y
    assert plc.sent == []


def test_y_word_or_byte_write_is_never_possible():
    plc = FakePLC()
    with pytest.raises(LiveWriteRefused):
        write_units(plc, "Y0", [1], AllowList(["Y0-Y27"]))
    assert plc.sent == []


def test_y_allowlist_is_octal_and_listed_back():
    allow = AllowList(["y0-y7", "Y10"])
    assert allow.permits("Y", 0, 9) and not allow.permits("Y", 0, 10)
    assert allow.entries() == ["Y0-Y7", "Y10"]


def test_y_force_in_stop_refused_and_not_sent():
    plc = FakePLC(run=False)
    with pytest.raises(LiveWriteRefused, match="STOP"):
        force_bit(plc, "Y1", True, YALLOW)
    assert plc.sent == [STATUS] and plc.write_frames() == []


@pytest.mark.parametrize("reply", ["nak", "lost_ack", "no_reply", "garbage"])
def test_y_force_failures_not_retried(reply):
    plc = FakePLC(reply=reply)
    result = force_bit(plc, "Y1", True, YALLOW)
    assert result.status == ("nak" if reply == "nak" else "uncertain") and not result.ok
    assert len(plc.write_frames()) == 1 and plc.sent[-1] == plc.write_frames()[0]


def test_y_readback_mismatch_when_ladder_owns_the_output():
    plc = FakePLC(consume=True)  # an OUT coil immediately restores the logic state
    result = force_bit(plc, "Y1", True, YALLOW)
    assert result.status == "uncertain" and result.acked and result.readback is False


def test_read_y_block_decodes_octal_names_and_sends_one_read():
    plc = FakePLC()
    plc.memory[0xA0:0xA3] = bytes([0b0000_0010, 0b0000_0001, 0b1000_0000])
    block = read_y_block(plc)
    assert plc.sent == [frame("000A003")]  # one plain read, no status, no write
    assert block.raw == bytes([2, 1, 0x80]) and block.on == ["Y1", "Y10", "Y27"]
    assert block.as_dict() == {"raw_hex": "020180", "on": ["Y1", "Y10", "Y27"]}


def test_read_y_block_all_off_and_works_in_stop():
    plc = FakePLC(run=False)
    block = read_y_block(plc)
    assert block.on == [] and block.raw == b"\x00\x00\x00"
    assert plc.write_frames() == []


def test_read_y_block_propagates_transport_errors():
    plc = FakePLC()
    plc.exchange = lambda tx: (_ for _ in ()).throw(TimeoutError("Incomplete response"))
    with pytest.raises(TimeoutError):
        read_y_block(plc)


def test_cli_dry_run_force_y(tmp_path, monkeypatch, capsys, no_port):
    path = write_allow(tmp_path, {"allow": ["Y0-Y7"]})
    code, out, _ = run_cli(monkeypatch, capsys, "live-write", "Y1", "1", "--allow", str(path))
    data = json.loads(out)
    assert code == 0 and data["kind"] == "force" and data["device"] == "Y1"
    assert data["wire_request_hex"] == frame("70105").hex()
    code, _, err = run_cli(monkeypatch, capsys, "live-write", "Y10", "1", "--allow", str(path))
    assert code != 0 and "allow" in err


# --------------------------------------------------------------------------- STOP and run state


def test_cpu_in_stop_is_refused_without_any_write_or_run_command():
    plc = FakePLC(run=False)
    with pytest.raises(LiveWriteRefused, match="STOP"):
        force_bit(plc, "M250", True, ALLOW)
    with pytest.raises(LiveWriteRefused, match="STOP"):
        write_units(plc, "D44", [1], ALLOW)
    assert plc.sent == [STATUS, STATUS]  # only the RUN/STOP status read
    assert plc.write_frames() == []
    assert plc.memory[0x11F] == 0 and plc.memory[0x1058:0x105A] == b"\x00\x00"


def test_status_read_failure_is_refused_without_write():
    plc = FakePLC()
    plc.exchange = lambda tx: (_ for _ in ()).throw(TimeoutError("Incomplete response"))
    with pytest.raises(LiveWriteRefused, match="RUN"):
        force_bit(plc, "M250", True, ALLOW)


@pytest.mark.parametrize("op", ["force", "word", "byte", "off"])
def test_never_sends_stop_run_or_other_commands(op):
    plc = FakePLC()
    if op == "force":
        force_bit(plc, "M250", True, ALLOW)
    elif op == "off":
        force_bit(plc, "M250", False, ALLOW)
    elif op == "word":
        write_units(plc, "D44", [1], ALLOW)
    else:
        write_units(plc, "M248", [1], ALLOW)
    prefixes = {payload(tx)[:1] for tx in plc.sent}
    assert prefixes <= {b"F", b"0", b"1", b"7", b"8"}
    assert not any(payload(tx).startswith((b"E7", b"E8")) for tx in plc.sent)


# --------------------------------------------------------------------------- failures, no retry


@pytest.mark.parametrize("op", ["force", "word", "byte"])
def test_nak_is_certain_failure_and_not_retried(op):
    plc = FakePLC(reply="nak")
    result = run(plc, op)
    assert not result.ok and result.status == "nak" and not result.acked
    assert len(plc.write_frames()) == 1
    assert plc.sent[-1] == plc.write_frames()[0]  # nothing after the NAK, no retry


@pytest.mark.parametrize("reply", ["lost_ack", "no_reply", "garbage"])
@pytest.mark.parametrize("op", ["force", "word", "byte"])
def test_unconfirmed_write_is_uncertain_and_not_retried(reply, op):
    plc = FakePLC(reply=reply)
    result = run(plc, op)
    assert not result.ok and result.status == "uncertain" and not result.acked
    assert result.retried is False
    assert len(plc.write_frames()) == 1  # exactly one attempt
    assert plc.sent[-1] == plc.write_frames()[0]  # no automatic follow-up traffic at all


def test_readback_mismatch_after_ack_is_uncertain():
    plc = FakePLC(consume=True)  # ladder swallows the bit before the read-back
    result = force_bit(plc, "M250", True, ALLOW)
    assert not result.ok and result.status == "uncertain" and result.acked
    assert result.readback is False and result.expected is True
    assert len(plc.write_frames()) == 1


def test_readback_transport_failure_after_ack_is_uncertain():
    plc = FakePLC(read_fail=True)
    result = write_units(plc, "D44", [7], ALLOW)
    assert result.status == "uncertain" and result.acked and not result.ok
    assert len(plc.write_frames()) == 1


def test_word_readback_mismatch():
    plc = FakePLC()
    original_apply = plc.apply
    plc.apply = lambda body: original_apply(body.replace("A5A5", "A5A4"))
    result = write_units(plc, "D44", [0xA5A5], ALLOW)
    assert result.status == "uncertain" and result.acked and result.readback == [0xA4A5]


def run(plc, op):
    if op == "force":
        return force_bit(plc, "M250", True, ALLOW)
    if op == "word":
        return write_units(plc, "D44", [0xA5A5], ALLOW)
    return write_units(plc, "M248", [0xA5], ALLOW)


# --------------------------------------------------------------------------- module hygiene


def test_module_cannot_issue_stop_or_run():
    source = open(live.__file__, encoding="utf-8").read()
    assert "set_running" not in source
    assert "E72560" not in source and "E82560" not in source
    assert "import serial" not in source and "Session.open" not in source  # no direct port access


# --------------------------------------------------------------------------- CLI


def run_cli(monkeypatch, capsys, *argv):
    from fx3u import cli

    monkeypatch.setattr(sys, "argv", ["fx3u", *argv])
    code, extra = 0, ""
    try:
        cli.main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:  # uncaught CLI errors surface as a traceback on the real command line
        code, extra = 1, str(exc)
    captured = capsys.readouterr()
    return code, captured.out, captured.err + extra


@pytest.fixture
def no_port(monkeypatch):
    from fx3u import cli

    def forbidden(*a, **kw):
        raise AssertionError("CLI opened a port")

    monkeypatch.setattr(cli.Session, "open", forbidden)


def allow_file(tmp_path, entries=("M240-M255", "D24-D49")):
    return write_allow(tmp_path, {"allow": list(entries)})


def test_cli_dry_run_bit_prints_frame_and_never_opens_port(tmp_path, monkeypatch, capsys, no_port):
    code, out, _ = run_cli(monkeypatch, capsys, "live-write", "M250", "1", "--allow", str(allow_file(tmp_path)))
    data = json.loads(out)
    assert code == 0
    assert data["dry_run"] is True and data["kind"] == "force"
    assert data["wire_request_hex"] == frame("7FA08").hex()
    assert data["temporary_stop"] is False and data["qualified_on_hardware"] is False
    assert not (tmp_path / "wire.jsonl").exists()


def test_cli_dry_run_off_and_word(tmp_path, monkeypatch, capsys, no_port):
    allow = str(allow_file(tmp_path))
    code, out, _ = run_cli(monkeypatch, capsys, "live-write", "M250", "0", "--allow", allow)
    assert code == 0 and json.loads(out)["wire_request_hex"] == frame("8FA08").hex()
    code, out, _ = run_cli(monkeypatch, capsys, "live-write", "D44", "0xA5A5", "--allow", allow)
    data = json.loads(out)
    assert code == 0 and data["kind"] == "write" and data["wire_request_hex"] == frame("1105802A5A5").hex()


def test_cli_dry_run_refuses_outside_allowlist(tmp_path, monkeypatch, capsys, no_port):
    allow = str(allow_file(tmp_path, ["D24-D49"]))
    code, out, err = run_cli(monkeypatch, capsys, "live-write", "M250", "1", "--allow", allow)
    assert code != 0 and out == "" and "allow" in err
    code, _, err = run_cli(monkeypatch, capsys, "live-write", "D50", "1", "--allow", allow)
    assert code != 0 and "allow" in err


def test_cli_requires_allowlist(tmp_path, monkeypatch, capsys, no_port):
    code, _, err = run_cli(monkeypatch, capsys, "live-write", "M250", "1")
    assert code == 2 and "--allow" in err


def test_cli_empty_allowlist_refused(tmp_path, monkeypatch, capsys, no_port):
    code, out, err = run_cli(
        monkeypatch, capsys, "live-write", "M250", "1", "--allow", str(allow_file(tmp_path, []))
    )
    assert code != 0 and out == "" and "allow" in err.lower()


@pytest.mark.parametrize(
    "device,value",
    [("Y1", "1"), ("X0", "1"), ("D8062", "0"), ("M8000", "1"), ("M250", "2"), ("D44", "70000"), ("D44", "-1"),
     ("D44", "abc"), ("M239", "1")],
)
def test_cli_rejects_forbidden_targets_and_values(tmp_path, monkeypatch, capsys, no_port, device, value):
    code, out, _ = run_cli(monkeypatch, capsys, "live-write", device, value, "--allow", str(allow_file(tmp_path)))
    assert code != 0 and out == ""


def test_cli_execute_requires_port_and_wire_log(tmp_path, monkeypatch, capsys, no_port):
    allow = str(allow_file(tmp_path))
    code, _, err = run_cli(monkeypatch, capsys, "live-write", "M250", "1", "--allow", allow, "--execute")
    assert code != 0 and "--port" in err
    code, _, err = run_cli(
        monkeypatch, capsys, "live-write", "M250", "1", "--allow", allow, "--execute", "--port", "COM254"
    )
    assert code != 0 and "--wire-log" in err


def test_cli_execute_uses_session_and_reports(tmp_path, monkeypatch, capsys):
    from fx3u import cli

    plc = FakePLC()
    opened = []
    monkeypatch.setattr(cli.Session, "open", classmethod(lambda cls, name, log=None: opened.append(name) or plc))
    log = tmp_path / "wire.jsonl"
    code, out, _ = run_cli(
        monkeypatch, capsys, "live-write", "M250", "1", "--allow", str(allow_file(tmp_path)),
        "--port", "COM254", "--wire-log", str(log), "--execute",
    )
    data = json.loads(out)
    assert code == 0 and opened == ["COM254"] and log.exists()
    assert data["ok"] is True and data["status"] == "ok" and plc.write_frames() == [frame("7FA08")]


def test_cli_execute_exit_code_for_uncertain_write(tmp_path, monkeypatch, capsys):
    from fx3u import cli

    plc = FakePLC(reply="lost_ack")
    monkeypatch.setattr(cli.Session, "open", classmethod(lambda cls, name, log=None: plc))
    code, out, _ = run_cli(
        monkeypatch, capsys, "live-write", "D44", "5", "--allow", str(allow_file(tmp_path)),
        "--port", "COM254", "--wire-log", str(tmp_path / "w.jsonl"), "--execute",
    )
    assert code == 2 and json.loads(out)["status"] == "uncertain"
    assert len(plc.write_frames()) == 1


def test_cli_execute_stop_is_refused_nonzero(tmp_path, monkeypatch, capsys):
    from fx3u import cli

    plc = FakePLC(run=False)
    monkeypatch.setattr(cli.Session, "open", classmethod(lambda cls, name, log=None: plc))
    code, out, err = run_cli(
        monkeypatch, capsys, "live-write", "M250", "1", "--allow", str(allow_file(tmp_path)),
        "--port", "COM254", "--wire-log", str(tmp_path / "w.jsonl"), "--execute",
    )
    assert code != 0 and "STOP" in err and plc.write_frames() == []


@pytest.mark.skipif(sys.platform == "win32", reason="live transport is allowed on Windows")
def test_cli_execute_refuses_non_windows_without_touching_a_port(tmp_path, monkeypatch, capsys):
    code, _, err = run_cli(
        monkeypatch, capsys, "live-write", "M250", "1", "--allow", str(allow_file(tmp_path)),
        "--port", "COM254", "--wire-log", str(tmp_path / "w.jsonl"), "--execute",
    )
    assert code != 0 and "native Windows" in err
