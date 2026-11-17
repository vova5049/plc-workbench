"""Conservative device-reference analysis, independent of bytecode codecs.

This is an inspection aid, not a free-device allocator or an IL validator.
Only the explicitly listed instruction footprints are resolved completely.
"""
import re


_DEVICE = re.compile(r"(?:(?:K([1-8]))?([XYMSTDCRVZ])(\d+))(?:([VZ])([0-7]))?$")
_CONSTANT = re.compile(r"(?:K-?\d+|H[0-9A-F]+|E[-+0-9.E]+|[PIN]\d+)$")
_BITS = frozenset("XYMS")
_CONTACTS = {"LD", "LDI", "LDP", "LDF", "AND", "ANI", "ANDP", "ANDF", "OR", "ORI", "ORP", "ORF"}
_COILS = {"SET", "RST", "PLS", "PLF"}
_EMPTY = {"END", "FEND", "NOP", "ORB", "ANB", "MPS", "MRD", "MPP", "RET"}


def _name(family, number):
    return family + (format(number, "o") if family in "XY" else str(number))


def parse_device(text):
    """Parse a whole operand; X/Y numbers are octal, all other devices decimal.

    Return None for constants/unsupported forms. Invalid octal raises ValueError.
    A grouped operand's width is the explicitly designated number of bits.
    """
    match = _DEVICE.fullmatch(text.strip().upper())
    if not match:
        return None
    group, family, digits, index_family, index_digits = match.groups()
    if group and family not in _BITS:
        return None
    try:
        number = int(digits, 8 if family in "XY" else 10)
    except ValueError:
        raise ValueError(f"Invalid octal device: {text}") from None
    return {"family": family, "number": number, "width": int(group) * 4 if group else 1,
            "grouped": bool(group), "index": index_family + index_digits if index_family else None}


def operand_devices(text):
    """Return explicit device anchors (including index registers), not occupancy."""
    device = parse_device(text)
    if device is None:
        return []
    result = [_name(device["family"], device["number"])]
    if device["index"]:
        result.append(device["index"])
    return result


def _constant(text):
    if re.fullmatch(r"K\d+", text):
        return int(text[1:])
    if re.fullmatch(r"H[0-9A-F]+", text):
        return int(text[1:], 16)
    return None


def _footprint(mnemonic, operands, parsed):
    """Return per-operand widths and unresolved semantic reasons."""
    widths = [d["width"] if d else 1 for d in parsed]
    issues = []
    m = mnemonic[:-1] if mnemonic.endswith("P") and mnemonic not in _CONTACTS | _COILS | _EMPTY else mnemonic
    if m in _EMPTY and not operands:
        return widths, issues
    if m in _CONTACTS | _COILS and len(operands) == 1:
        return widths, issues
    if m == "OUT" and len(operands) in (1, 2):
        if len(operands) == 2 and (not parsed[0] or parsed[0]["family"] not in "TC"):
            issues.append("Unsupported OUT operand form")
        return widths, issues
    word_ops = {"MOV": 2, "INC": 1, "DEC": 1, "ROL": 2, "ROR": 2}
    if m in word_ops and len(operands) == word_ops[m]:
        return widths, issues
    if m == "DMOV" and len(operands) == 2:
        for i, d in enumerate(parsed):
            if d and not d["grouped"]:
                if d["family"] in "DR":
                    widths[i] = 2
                else:
                    issues.append("DMOV footprint for this device family is unresolved")
        return widths, issues
    if m.startswith(("AND", "OR", "LD")) and m.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ") in {"=", "<>", ">", "<", ">=", "<="} and len(operands) == 2:
        prefix = m[:len(m) - len(m.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))]
        if prefix in {"AND", "OR", "LD"}:
            return widths, issues
    if m == "BMOV" and len(operands) == 3:
        count = _constant(operands[2])
        if count is None or count <= 0:
            issues.append("BMOV count is not a positive constant; extent unresolved")
            return widths, issues
        for i in (0, 1):
            if parsed[i] and parsed[i]["family"] in "DR":
                widths[i] = count
            else:
                issues.append("BMOV footprint for this device family is unresolved")
        return widths, issues
    if m == "ZRST" and len(operands) == 2:
        first, last = parsed
        if (first and last and first["family"] == last["family"]
                and last["number"] >= first["number"]
                and not any(d["index"] or d["grouped"] for d in parsed)):
            widths[0] = last["number"] - first["number"] + 1
        else:
            issues.append("ZRST endpoints cannot be resolved as a static same-family range")
        return widths, issues
    if m == "SFTL" and len(operands) == 4:
        length, shift = _constant(operands[2]), _constant(operands[3])
        if (length is not None and shift is not None and 1 <= shift <= length
                and all(d and d["family"] in _BITS and not d["grouped"] for d in parsed[:2])):
            widths[:2] = [shift, length]
        else:
            issues.append("SFTL extent is not a valid static constant range")
        return widths, issues
    issues.append("Instruction footprint is outside the supported analysis subset")
    return widths, issues


def query_usage(instructions, device):
    """Find definite/potential references, always reporting analysis uncertainty.

    Accept bytecode.Instruction objects or GX CSV parser dictionaries. Indices
    are zero based. complete means all footprints were resolved by this subset;
    an empty hits list never authorizes use of an address on a physical PLC.
    """
    target = parse_device(device)
    if target is None or target["grouped"] or target["index"]:
        raise ValueError("Query requires one direct device address")
    result = {"device": _name(target["family"], target["number"]), "hits": [], "uncertainties": [], "complete": True}
    for index, instruction in enumerate(instructions):
        if isinstance(instruction, dict):
            mnemonic, operands = instruction["mnemonic"], instruction["operands"]
        else:
            mnemonic, operands = instruction.mnemonic, instruction.operands
        mnemonic, operands = mnemonic.upper(), [str(op).strip().upper() for op in operands]
        context = {"instruction_index": index, "mnemonic": mnemonic, "operands": operands}
        parsed = []
        issues = []
        for op in operands:
            try:
                parsed.append(parse_device(op))
            except ValueError:
                parsed.append(None)
                issues.append("Invalid device address")
            if parsed[-1] is None and not _CONSTANT.fullmatch(op):
                issues.append("Unsupported operand form")
        widths, footprint_issues = _footprint(mnemonic, operands, parsed)
        issues.extend(footprint_issues)
        for operand_index, (op, width) in enumerate(zip(parsed, widths)):
            if op is None:
                continue
            if op["family"] == "C" and op["number"] >= 200:
                issues.append("High-speed counter width/semantics is unresolved")
                # A logical 32-bit counter is not two consecutive C devices.
                width = 1
            if op["index"]:
                issues.append("Indexed address has an unknown runtime offset")
                if target["family"] == op["family"]:
                    result["hits"].append({**context, "operand_index": operand_index,
                                           "kind": "indexed", "certainty": "potential", "start": None, "end": None})
                if result["device"] == op["index"]:
                    result["hits"].append({**context, "operand_index": operand_index,
                                           "kind": "index-register", "certainty": "definite", "start": op["index"], "end": op["index"]})
                continue
            if op["family"] == target["family"] and op["number"] <= target["number"] < op["number"] + width:
                result["hits"].append({**context, "operand_index": operand_index,
                                       "kind": "group" if op["grouped"] else "range" if width > 1 else "direct",
                                       "certainty": "definite", "start": _name(op["family"], op["number"]),
                                       "end": _name(op["family"], op["number"] + width - 1)})
        for reason in dict.fromkeys(issues):
            result["uncertainties"].append({**context, "reason": reason})
    result["complete"] = not result["uncertainties"]
    return result
