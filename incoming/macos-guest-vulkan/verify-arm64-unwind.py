#!/usr/bin/env python3
"""Independently compare decoded CFI with actual AArch64 stack-pair instructions."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def frames(data):
    start, size = 0x188278, 441924
    pos = 20
    result = {}
    while pos < size:
        length = struct.unpack_from("<I", data, start + pos)[0]
        if not length:
            assert pos + 4 == size
            break
        raw = data[start + pos:start + pos + length + 4]
        assert struct.unpack_from("<I", raw, 4)[0] == pos + 4 and raw[16] == 0
        pc = start + pos + 8 + struct.unpack_from("<i", raw, 8)[0]
        count = struct.unpack_from("<I", raw, 12)[0]
        assert pc not in result and 0x200000 <= pc < 0x3f8ff0
        result[pc] = {"fde": start + pos, "range": count, "cfi": raw[17:]}
        pos += length + 4
    return result


def cfi_rows(pc, code):
    # CIE: CFA=SP, data alignment -4, default register rules restored by 0xc0.
    offset, registers, saved, position = 0, {}, [], 0
    rows = {pc: (offset, dict(registers))}
    def uleb():
        nonlocal position
        value = shift = 0
        while True:
            byte = code[position]
            position += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
            shift += 7
    while position < len(code):
        opcode = code[position]
        position += 1
        primary = opcode & 0xc0
        if primary == 0x40:
            pc += opcode & 63
        elif primary == 0x80:
            registers[opcode & 63] = -4 * uleb()
        elif primary == 0xc0:
            registers.pop(opcode & 63, None)
        elif opcode == 0x00:
            pass
        elif opcode == 0x02:
            pc += code[position]
            position += 1
        elif opcode == 0x03:
            pc += struct.unpack_from("<H", code, position)[0]
            position += 2
        elif opcode == 0x0e:
            offset = uleb()
        elif opcode == 0x0a:
            saved.append((offset, dict(registers)))
        elif opcode == 0x0b:
            offset, registers = saved.pop()
        else:
            raise ValueError("unexpected CFI opcode " + hex(opcode))
        rows[pc] = (offset, dict(registers))
    return rows


def stack_instruction(word, sp, registers):
    # Decode 64-bit STP/LDP with SP base, including signed pre/post writeback.
    if word & 0x3a000000 != 0x28000000 or (word >> 5) & 31 != 31:
        return sp, registers
    assert (word >> 30) & 3 == 2
    load, mode = (word >> 22) & 1, (word >> 23) & 3
    immediate = (word >> 15) & 127
    immediate = (immediate - 128 if immediate >= 64 else immediate) * 8
    rt, rt2 = word & 31, (word >> 10) & 31
    if mode == 3:
        sp += immediate
    address = sp + immediate if mode == 2 else sp
    for index, register in enumerate((rt, rt2)):
        if load:
            assert registers[register] == address + index * 8
            registers.pop(register)
        else:
            registers[register] = address + index * 8
    if mode == 1:
        sp += immediate
    return sp, registers


def check_range(data, frame, start, stop, reset_at=None):
    rows = cfi_rows(start, frame["cfi"])
    keys = sorted(rows)
    sp, registers, snapshot = 0, {}, None
    checked = 0
    for pc in range(start, stop, 4):
        if reset_at and pc == reset_at:
            sp, saved = snapshot
            registers = dict(saved)
        expected = rows[max(key for key in keys if key <= pc)]
        assert expected == (-sp, registers), (hex(pc), expected, (-sp, registers))
        instruction = struct.unpack_from("<I", data, pc)[0]
        sp, registers = stack_instruction(instruction, sp, registers)
        if reset_at and pc + 4 == 0x213f50:
            snapshot = (sp, dict(registers))
        checked += 1
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    original, candidate = args.original.read_bytes(), args.candidate.read_bytes()
    old, new = frames(original), frames(candidate)
    assert old.keys() == new.keys() and len(new) == 9563 and len(original) == len(candidate)
    targets = {0x213f48, 0x3c86c8, 0x3ebd10}
    for pc in old.keys() - targets:
        assert old[pc]["range"] == new[pc]["range"] and old[pc]["cfi"] == new[pc]["cfi"]
    assert new[0x3ebd10]["range"] == 4 and set(new[0x3ebd10]["cfi"]) <= {0}
    hdr, count = 0x175794, 9563
    assert candidate[hdr:hdr + 4] == b"\x01\x1b\x03\x3b"
    assert hdr + 4 + struct.unpack_from("<i", candidate, hdr + 4)[0] == 0x188278
    assert struct.unpack_from("<I", candidate, hdr + 8)[0] == count
    previous = 0
    for i in range(count):
        pc_delta, fde_delta = struct.unpack_from("<ii", candidate, hdr + 12 + i * 8)
        pc, fde = hdr + pc_delta, hdr + fde_delta
        assert pc > previous and pc in new and new[pc]["fde"] == fde
        previous = pc
    checked = check_range(candidate, new[0x3c86c8], 0x3c86c8, 0x3c87f0)
    checked += check_range(candidate, new[0x213f48], 0x213f48, 0x214148, reset_at=0x214138)
    # The original CFI must fail against the candidate's changed stack program.
    try:
        check_range(candidate, old[0x3c86c8], 0x3c86c8, 0x3c87f0)
    except (AssertionError, ValueError):
        negative = True
    else:
        raise AssertionError("original stale CFI unexpectedly passed")
    report = {"ok": True, "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
              "fde_count": count, "all_header_entries_verified": True,
              "other_9560_fdes_cfi_unchanged": True, "instruction_pcs_checked": checked,
              "stale_cfi_negative_control": negative,
              "scope": "DWARF CFI versus actual stack-pair instruction semantics, including both bind2 exits"}
    with args.output.open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
