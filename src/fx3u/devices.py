"""Bounded device monitor and one-shot edits for the qualified CPU profile.

FORCE 7/8 changes one M bit; it is not a persistent override of ladder logic.
The initial wire format comes from fxplc's public Programming Communication
implementation; address ranges here are deliberately limited to this profile.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .client import frame


@dataclass(frozen=True)
class Device:
    name: str
    address: int
    size: int
    bit: int | None = None

    def decode(self, data):
        if len(data) != self.size:
            raise ValueError("Wrong device response length")
        return (
            bool(data[0] & (1 << self.bit))
            if self.bit is not None
            else int.from_bytes(data, "little")
        )


def device(label):
    label = label.upper()
    match = re.fullmatch(r"([XYMDTC])([0-9]+)", label)
    if not match:
        raise ValueError("Use a qualified X/Y/M/D/T/C device")
    kind, digits = match.groups()
    if kind in "XY":
        if any(c not in "01234567" for c in digits):
            raise ValueError("X/Y numbers are octal")
        number = int(digits, 8)
        if number >= 24:
            raise ValueError("Qualified X/Y range is 0..27 octal")
        return Device(
            f"{kind}{number:o}",
            (0x80 if kind == "X" else 0xA0) + number // 8,
            1,
            number % 8,
        )
    number = int(digits)
    limits = {"M": 255, "D": 127, "T": 48, "C": 0}
    if number > limits[kind]:
        raise ValueError(f"Outside qualified {kind} range 0..{limits[kind]}")
    if kind == "M":
        return Device(f"M{number}", 0x100 + number // 8, 1, number % 8)
    return Device(
        f"{kind}{number}", {"D": 0x1000, "T": 0x800, "C": 0xA00}[kind] + 2 * number, 2
    )


def monitor(session, labels):
    """Coalesce adjacent bytes; report explicitly non-atomic running snapshots."""
    devices = list(dict.fromkeys(device(label) for label in labels))
    if not devices:
        raise ValueError("Choose at least one device")
    needed = sorted(
        {addr for dev in devices for addr in range(dev.address, dev.address + dev.size)}
    )
    groups = []
    for addr in needed:
        if groups and addr == groups[-1][-1] + 1 and len(groups[-1]) < 254:
            groups[-1].append(addr)
        else:
            groups.append([addr])
    with session.lock:
        started = datetime.now(timezone.utc).isoformat()
        memory = {}
        for group in groups:
            raw = session.read(group[0], len(group))
            if len(raw) != len(group):
                raise ValueError("Incomplete monitor read")
            memory.update(zip(group, raw))
        values = {
            dev.name: dev.decode(
                bytes(memory[a] for a in range(dev.address, dev.address + dev.size))
            )
            for dev in devices
        }
        return {
            "started_utc": started,
            "finished_utc": datetime.now(timezone.utc).isoformat(),
            "atomic": False,
            "run": session.running(),
            "values": values,
        }


def edit_request(label, value):
    """Validate without opening a port; writes only internal M and D devices."""
    dev = device(label)
    if dev.name[0] not in "MD":
        raise ValueError("Edits are qualified only for M0..255 and D0..127")
    if type(value) is not int or not 0 <= value <= (
        1 if dev.bit is not None else 65535
    ):
        raise ValueError("Use 0/1 for M or an unsigned 16-bit D value")
    if dev.bit is not None:
        bit_address = 0x800 + int(dev.name[1:])
        request = ("7" if value else "8") + bit_address.to_bytes(
            2, "little"
        ).hex().upper()
    else:
        request = f"1{dev.address:04X}02" + value.to_bytes(2, "little").hex().upper()
    return dev, frame(request)


def edit(session, label, value, expected, out_dir):
    """STOP, back up the addressed device, compare, write once, read back, resume.

    On uncertain writes restore the original device value and verify in STOP.
    A failed recovery suppresses automatic RUN; communication loss can prevent
    confirmation of STOP. Ladder may replace an edit after RUN; that observation
    is reported separately from the successful stopped readback.
    """
    dev, request = edit_request(label, value)
    edit_request(label, expected)
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=False)
    report = {
        "ok": False,
        "device": dev.name,
        "requested": value,
        "expected": expected,
        "persistent_force": False,
        "program_modified": False,
    }
    with session.lock:
        initially_running = session.running()
        report["initial_run"] = initially_running
        touched = False
        resume = True
        original = None
        try:
            if initially_running:
                session.set_running(False)
            raw = session.read(dev.address, dev.size)
            (root / "before.bin").write_bytes(raw)
            original = dev.decode(raw)
            report["before"] = original
            # Flush the recovery description to disk before the first write.
            (root / "before.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            if original != expected:
                raise ValueError(
                    "Expected device value differs; no device write performed"
                )
            if original != value:
                touched = True
                session.ack(request)
            after_raw = session.read(dev.address, dev.size)
            (root / "after.bin").write_bytes(after_raw)
            report["stopped_readback"] = dev.decode(after_raw)
            if report["stopped_readback"] != value:
                raise ValueError("Device readback differs")
            # A bit edit must preserve its seven neighbours while CPU is STOP.
            if dev.bit is not None and (raw[0] ^ after_raw[0]) & ~(1 << dev.bit):
                raise ValueError("Neighbouring bits changed")
            report["ok"] = True
        except BaseException as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
            if touched:
                try:
                    session.ack(edit_request(label, int(original))[1])
                    restored = session.read(dev.address, dev.size)
                    (root / "recovery.bin").write_bytes(restored)
                    if restored != raw:
                        raise ValueError("Recovery differs; automatic resume suppressed")
                    report["recovered"] = True
                except BaseException as recovery:  # noqa: BLE001 -- interrupted recovery suppresses resume
                    resume = False
                    report["recovery_error"] = str(recovery)
            raise
        finally:
            try:
                if initially_running and resume:
                    session.set_running(True)
                report["final_run"] = session.running()
                if report["ok"]:
                    report["after_resume"] = dev.decode(
                        session.read(dev.address, dev.size)
                    )
                    report["overwritten_after_resume"] = report["after_resume"] != value
            except BaseException as exc:
                report["ok"] = False
                report["resume_error"] = str(exc)
                raise
            finally:
                (root / "result.json").write_text(
                    json.dumps(report, indent=2) + "\n", encoding="utf-8"
                )
    return report
