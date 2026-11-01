// This file is staged outside Manager for review. It can be copied unchanged to
// internal/vkfix/arm64.go after the isolated Android regression passes.
// It needs no compiler at runtime and never distributes a patched guest library.
package vkfix

import (
	"bytes"
	"encoding/binary"
	"fmt"

	"modavd/internal/errs"
)

const (
	ARM64OrigSHA256 = "d6188011ed2ed58a1d69adfb04246abaa2595c0f15565a22da83f7751a3d6828"
	ARM64FixSHA256  = "73ddfe0935826feef1f1a78d5a70af3ee7fa83584d2efaf5bad6ee91f36eb613"
	ARM64CacheName  = "libvulkan_enc.arm64-khrfix-bindlock2.so"
)

type arm64CodeEdit struct {
	offset      int
	size        int
	originalSHA string
	replacement string
}

var arm64CodeEdits = []arm64CodeEdit{
	// KHR entry to core entry
	{0x3ebd10, 4, "9578e722f9ab0a18626e241133188c8f9463e9e1cb7297fec82fd6f65e1a90cf", "24faff17"},
	// KHR dynCheck to core entry
	{0x3c86c8, 4, "63b368cdb6ab9ee53a7cd531602553504c8573b654a5d4e620add844a006e9f4", "b6870014"},
	// bind wrappers in retired dynCheck
	{0x3c86cc, 292, "9ae7f5989678fbf36a36fe545bec25a3463add4c714b2b5a2a2f7cb615d96ab9", "f353bca9f55b01a9f76302a9f97b03a9130440f9f40301aaf50303aaf60304aaf70305aaf80306aa6082199186c70094e00313aae10314aae30315aae40316aae50317aae60318aa972df997f903002a6082199188c70094e003192a00010035e00314aae10315aae20316aae30317aae40318aa25008052efc90094f97b43a9f76342a9f55b41a9f353c4a8c0035fd6f353bca9f55b01a9f76302a9f97b03a9130440f9f40301aaf50303aaf603042af70305aa6082199163c70094e00313aae10314aae30315aae403162ae50317aaeb2df997f903002a6082199166c70094e003192ae0000035e00314aae10315aae203162ae30317aa24008052d6c90094f97b43a9f76342a9f55b41a9f353c4a8c0035fd61f2003d51f2003d51f2003d51f2003d5"},
	// bind validator success, no host call while locked
	{0x213f24, 28, "ac0ad703c43eb8adba3462f24dbe357acae21a181a0162e24dd887605dad7855", "00008052c0035fd61f2003d51f2003d51f2003d51f2003d51f2003d5"},
	// bind2 validator success, restored original stack
	{0x214118, 32, "2d260099f0a02c8efd1d2afacf41bf2b8057c4b8de6e7c2d9b1a946cb478da75", "f44f41a900008052f657c2a8c0035fd61f2003d51f2003d51f2003d51f2003d5"},
	// bind forwarder
	{0x213d68, 8, "81ce198ce27828ba00b5a91d96416a993297afcc914c4fa49a8fbcaece0580ad", "59d206141f2003d5"},
	// bind2 forwarder
	{0x213f40, 8, "81ce198ce27828ba00b5a91d96416a993297afcc914c4fa49a8fbcaece0580ad", "07d206141f2003d5"},
	// bind2KHR forwarder
	{0x214148, 8, "9b5b042b161a43e7a414e5ad5253b563ed0f3dc88635e08c215ecab328c9f0d7", "85d106141f2003d5"},
}

// PatchARM64 supports only the exact API34 Google APIs arm64-v8a revision14
// library. The wrappers validate under ResourceTracker::Impl::mLock and release
// it before the original encoder call. Fixed input/output hashes guard code,
// metadata and ELF layout; an unexpected version is never patched.
func PatchARM64(original []byte) ([]byte, error) {
	if actual := SHA256Hex(original); actual != ARM64OrigSHA256 {
		return nil, errs.Newf(errs.Unsupported, "ARM64 客体驱动库 sha256 %s 不是预期版本", actual)
	}
	data := bytes.Clone(original)
	for _, edit := range arm64CodeEdits {
		end := edit.offset + edit.size
		if edit.offset < 0 || end > len(data) || SHA256Hex(original[edit.offset:end]) != edit.originalSHA {
			return nil, errs.Newf(errs.Unsupported, "ARM64 客体补丁位置 0x%x 原字节不符", edit.offset)
		}
		blob := mustHex(edit.replacement)
		if len(blob) != edit.size {
			return nil, errs.Newf(errs.Unsupported, "ARM64 客体补丁位置 0x%x 长度不符", edit.offset)
		}
		copy(data[edit.offset:end], blob)
	}
	if err := arm64RebuildUnwind(original, data); err != nil {
		return nil, errs.Newf(errs.Unsupported, "ARM64 客体 unwind 元数据不符: %v", err)
	}
	if actual := SHA256Hex(data); actual != ARM64FixSHA256 {
		return nil, errs.Newf(errs.Unsupported, "ARM64 客体补丁结果 sha256 %s 不符", actual)
	}
	return data, nil
}

func arm64RebuildUnwind(original, data []byte) error {
	const ehStart, ehSize = 0x188278, 441924
	const headerStart, headerSize = 0x175794, 76516
	source := original[ehStart : ehStart+ehSize]
	if !bytes.Equal(source[:20], mustHex("1000000000000000017a5200017c1e011b0c1f00")) {
		return fmt.Errorf("unsupported shared CIE")
	}
	// Data alignment is -4. The dynCheck FDE describes both adjacent wrappers,
	// including partial prologues/epilogues. The retired KHR entry is a leaf branch.
	programs := map[int][]byte{
		0x213f48: mustHex("440e209608950644940493020a03cc01d3d4480e00d5d6540b44d3d4480e00d5d6"),
		0x3c86c8: mustHex("480e409310940e44950c960a44970898064499049e020270d9de44d7d844d5d644d3d40e00480e409310940e44950c960a44970898064499049e020264d9de44d7d844d5d644d3d40e00"),
		0x3ebd10: {},
	}
	out := append(make([]byte, 0, ehSize), source[:20]...)
	relocated := make(map[int]int)
	modified := 0
	terminated := false
	for old := 20; old+4 <= len(source); {
		length := int(binary.LittleEndian.Uint32(source[old:]))
		if length == 0 {
			if old+4 != len(source) {
				return fmt.Errorf("unexpected terminator")
			}
			out = append(out, 0, 0, 0, 0)
			terminated = true
			break
		}
		if length < 16 || old+length+4 > len(source) {
			return fmt.Errorf("FDE bounds")
		}
		raw := bytes.Clone(source[old : old+length+4])
		if binary.LittleEndian.Uint32(raw[4:]) != uint32(old+4) || raw[16] != 0 {
			return fmt.Errorf("FDE CIE/augmentation")
		}
		pc := ehStart + old + 8 + int(int32(binary.LittleEndian.Uint32(raw[8:])))
		if cfi, ok := programs[pc]; ok {
			raw = append(raw[:17], cfi...)
			for len(raw)%4 != 0 {
				raw = append(raw, 0)
			}
			binary.LittleEndian.PutUint32(raw, uint32(len(raw)-4))
			if pc == 0x3ebd10 {
				binary.LittleEndian.PutUint32(raw[12:], 4)
			}
			modified++
		}
		newOffset := len(out)
		relocated[ehStart+old] = ehStart + newOffset
		binary.LittleEndian.PutUint32(raw[4:], uint32(newOffset+4))
		binary.LittleEndian.PutUint32(raw[8:], uint32(int32(pc-(ehStart+newOffset+8))))
		out = append(out, raw...)
		old += length + 4
	}
	if !terminated || len(out) != ehSize || modified != 3 {
		return fmt.Errorf("FDE size/count mismatch")
	}
	copy(data[ehStart:ehStart+ehSize], out)
	header := data[headerStart : headerStart+headerSize]
	if !bytes.Equal(header[:4], []byte{1, 0x1b, 3, 0x3b}) {
		return fmt.Errorf("unwind header encoding")
	}
	count := int(binary.LittleEndian.Uint32(header[8:]))
	if headerSize != 12+8*count || count != len(relocated) || count != 9563 {
		return fmt.Errorf("unwind header FDE count")
	}
	for i := 0; i < count; i++ {
		offset := 16 + i*8
		previous := headerStart + int(int32(binary.LittleEndian.Uint32(header[offset:])))
		next, ok := relocated[previous]
		if !ok {
			return fmt.Errorf("unwind header FDE reference")
		}
		binary.LittleEndian.PutUint32(header[offset:], uint32(int32(next-headerStart)))
	}
	return nil
}
