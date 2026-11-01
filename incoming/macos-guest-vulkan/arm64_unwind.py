"""Retain the ELF's FDE count and section sizes while updating changed CFI.

The original dynCheck FDE now covers its branch stub and both adjacent wrappers.
The retired static KHR body needs only a leaf FDE for its four-byte branch.
Its freed CFI space offsets the larger wrapper and corrected bind2 records.
All other CFI is preserved, with relative FDE/CIE and header references relocated.
"""
import hashlib
import struct


class CFI:
    def __init__(self, pc):
        self.pc = pc
        self.code = bytearray()

    def advance(self, pc):
        delta = pc - self.pc
        if not 0 <= delta <= 0xFFFF:
            raise ValueError("CFI PC sequence/range")
        if delta <= 63:
            self.code.append(0x40 + delta)
        elif delta <= 255:
            self.code.extend((0x02, delta))
        else:
            self.code.extend(b"\x03" + struct.pack("<H", delta))
        self.pc = pc

    def cfa(self, size):
        assert size < 128
        self.code.extend((0x0E, size))

    def save(self, register, cfa_offset):
        # The exact original CIE has data alignment -4, not -8.
        assert cfa_offset < 0 and cfa_offset % 4 == 0 and -cfa_offset // 4 < 128
        self.code.extend((0x80 + register, -cfa_offset // 4))

    def restore(self, *registers):
        self.code.extend(0xC0 + register for register in registers)


def wrapper_cfi():
    c = CFI(0x3C86C8)
    for base, size in ((0x3C86CC, 144), (0x3C875C, 132)):
        for step, registers in enumerate(((19, 20), (21, 22), (23, 24), (25, 30)), 1):
            c.advance(base + step * 4)
            if step == 1:
                c.cfa(64)
            c.save(registers[0], -64 + (step - 1) * 16)
            c.save(registers[1], -56 + (step - 1) * 16)
        epilogue = base + size - 20
        for step, registers in enumerate(((25, 30), (23, 24), (21, 22), (19, 20)), 1):
            c.advance(epilogue + step * 4)
            c.restore(*registers)
            if step == 4:
                c.cfa(0)
    return bytes(c.code)


def bind2_cfi():
    c = CFI(0x213F48)
    c.advance(0x213F4C)
    c.cfa(32)
    c.save(22, -32)
    c.save(21, -24)
    c.advance(0x213F50)
    c.save(20, -16)
    c.save(19, -8)
    c.code.append(0x0A)  # remember_state: error branch retains this frame
    c.advance(0x21411C)
    c.restore(19, 20)
    c.advance(0x214124)
    c.cfa(0)
    c.restore(21, 22)
    c.advance(0x214138)
    c.code.append(0x0B)  # restore_state
    c.advance(0x21413C)
    c.restore(19, 20)
    c.advance(0x214144)
    c.cfa(0)
    c.restore(21, 22)
    return bytes(c.code)


def rebuild_unwind(original, candidate):
    shoff = struct.unpack_from("<Q", original, 40)[0]
    entsize, count, strings_index = struct.unpack_from("<HHH", original, 58)
    sections = [struct.unpack_from("<IIQQQQIIQQ", original, shoff + i * entsize)
                for i in range(count)]
    strings = sections[strings_index]
    names = original[strings[4]:strings[4] + strings[5]]
    sections = {names[s[0]:].split(b"\0")[0].decode(): s for s in sections}
    eh = sections[".eh_frame"]
    hdr = sections[".eh_frame_hdr"]
    if eh[3] != eh[4] or hdr[3] != hdr[4]:
        raise ValueError("unexpected ARM ELF virtual/file delta")
    source = original[eh[4]:eh[4] + eh[5]]
    # Validate the exact shared CIE: zR, code alignment 1, data alignment -4,
    # return register 30, PC-relative signed four-byte FDE addresses.
    if source[:20] != bytes.fromhex("1000000000000000017a5200017c1e011b0c1f00"):
        raise ValueError("unsupported CIE")
    output = bytearray(source[:20])
    relocated = {}
    modified = []
    old = 20
    while old < len(source):
        length = struct.unpack_from("<I", source, old)[0]
        if length == 0:
            if old + 4 != len(source):
                raise ValueError("unexpected .eh_frame terminator")
            output.extend(b"\0" * 4)
            break
        raw = bytearray(source[old:old + length + 4])
        if len(raw) != length + 4 or struct.unpack_from("<I", raw, 4)[0] != old + 4:
            raise ValueError("unsupported CIE reference")
        pc = eh[3] + old + 8 + struct.unpack_from("<i", raw, 8)[0]
        if raw[16] != 0:
            raise ValueError("unexpected FDE augmentation")
        cfi = {0x3C86C8: wrapper_cfi(), 0x213F48: bind2_cfi(), 0x3EBD10: b""}.get(pc)
        if cfi is not None:
            raw = raw[:17] + cfi
            raw.extend(b"\0" * (-len(raw) % 4))
            struct.pack_into("<I", raw, 0, len(raw) - 4)
            if pc == 0x3EBD10:
                struct.pack_into("<I", raw, 12, 4)
            modified.append({"pc": hex(pc), "old_record_bytes": length + 4,
                             "new_record_bytes": len(raw), "cfi_hex": cfi.hex()})
        new = len(output)
        relocated[eh[3] + old] = eh[3] + new
        struct.pack_into("<I", raw, 4, new + 4)
        struct.pack_into("<i", raw, 8, pc - (eh[3] + new + 8))
        output.extend(raw)
        old += length + 4
    if len(output) != len(source) or len(modified) != 3:
        raise ValueError(f"unwind section size/count changed: {len(source)} -> {len(output)}")
    candidate[eh[4]:eh[4] + eh[5]] = output
    # Header: version 1, pcrel sdata4 eh_frame pointer, udata4 count,
    # datarel sdata4 binary-search table. The key PCs and FDE count stay fixed.
    header = bytearray(original[hdr[4]:hdr[4] + hdr[5]])
    if header[:4] != b"\x01\x1b\x03\x3b":
        raise ValueError("unsupported .eh_frame_hdr encodings")
    fde_count = struct.unpack_from("<I", header, 8)[0]
    if len(header) != 12 + 8 * fde_count or fde_count != len(relocated):
        raise ValueError("unwind header count mismatch")
    for i in range(fde_count):
        offset = 16 + i * 8
        previous = hdr[3] + struct.unpack_from("<i", header, offset)[0]
        if previous not in relocated:
            raise ValueError("header references an unknown FDE")
        struct.pack_into("<i", header, offset, relocated[previous] - hdr[3])
    candidate[hdr[4]:hdr[4] + hdr[5]] = header
    return {"fde_count": fde_count, "eh_frame_bytes": len(output),
            "eh_frame_hdr_bytes": len(header), "section_sizes_unchanged": True,
            "modified_fdes": modified,
            "eh_frame_sha256": hashlib.sha256(output).hexdigest(),
            "eh_frame_hdr_sha256": hashlib.sha256(header).hexdigest()}
