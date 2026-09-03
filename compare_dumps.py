#!/usr/bin/env python3
"""
compare_dumps.py - compare several .ds2502 clone dumps side by side.

Purpose: when a host (printer etc.) accepts every ORIGINAL chip although
their ROM IDs all differ, the usual reason is that the 128-byte data is
PERSONALIZED per chip - it embeds the chip's own ROM ID and/or a checksum
or signature computed from it.  Comparing several original dumps reveals
exactly which bytes vary and whether ROM-ID bytes appear inside the data.

Usage:
    python3 compare_dumps.py dump1.ds2502 dump2.ds2502 [more...]
"""

import json
import sys


def crc8(buf, seed=0):
    """Dallas/Maxim CRC8 (poly 0x8C reflected, LSB first)."""
    for b in buf:
        d = b
        for _ in range(8):
            mix = (seed ^ d) & 1
            seed >>= 1
            if mix:
                seed ^= 0x8C
            d >>= 1
    return seed


def load(path):
    raw = open(path, "rb").read()
    try:
        j = json.loads(raw.decode("utf-8"))
        return (bytes.fromhex(j.get("rom", "")),
                bytes.fromhex(j["data"]),
                bytes.fromhex(j.get("status", "FF" * 7 + "00")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        if len(raw) == 128:
            return b"", raw, bytes([0xFF] * 7 + [0x00])
        if len(raw) == 136:
            head, tail = raw[:8], raw[128:]
            if crc8(tail[:7]) == tail[7] and tail[0] != 0xFF:
                return tail, raw[:128], bytes([0xFF] * 7 + [0x00])
            if crc8(head[:7]) == head[7] and head[0] != 0xFF:
                return head, raw[8:], bytes([0xFF] * 7 + [0x00])
            return b"", raw[:128], raw[128:]
        raise ValueError(f"{path}: unsupported size {len(raw)}")


def main(paths):
    if len(paths) < 2:
        print(__doc__)
        sys.exit(1)
    dumps = []
    for p in paths:
        rom, data, stat = load(p)
        dumps.append((p, rom, data, stat))
        print(f"{p}")
        print(f"   ROM   : {rom.hex().upper() or '(none)'}")
        print(f"   used  : {sum(1 for b in data if b != 0xFF)}/128 bytes")
        print(f"   status: {' '.join(f'{b:02X}' for b in stat)}")
    print()

    # ---- which data offsets differ between the dumps? ----
    diff_offs = [i for i in range(128)
                 if len({d[2][i] for d in dumps}) > 1]
    same_used = [i for i in range(128)
                 if len({d[2][i] for d in dumps}) == 1
                 and dumps[0][2][i] != 0xFF]
    print(f"Data offsets identical in all dumps (and != FF): "
          f"{len(same_used)}  -> common/constant part")
    print(f"Data offsets that DIFFER between dumps: {len(diff_offs)}")
    if diff_offs:
        hdr = "offset | " + " | ".join(f"dump{i+1}" for i in range(len(dumps)))
        print("\n  " + hdr)
        print("  " + "-" * len(hdr))
        for off in diff_offs:
            row = " |  ".join(f"{d[2][off]:02X}" for d in dumps)
            print(f"   {off:02X}h  |  {row}")

    # ---- per-page CRC scheme (last byte of each 32 B page = CRC8) ----
    print()
    for p, rom, data, stat in dumps:
        oks = [crc8(data[a:a + 31]) == data[a + 31]
               for a in range(0, 128, 32)]
        print(f"{p}: page CRCs "
              + " ".join(f"pg{i}:{'OK' if ok else 'no'}"
                         for i, ok in enumerate(oks)))

    # ---- does the chip's own ROM ID appear inside its data? ----
    print()
    for p, rom, data, stat in dumps:
        if not rom:
            continue
        hits = []
        for ln in (8, 7, 6, 5, 4, 3):          # try longest match first
            for start in range(0, 9 - ln):
                pat = rom[start:start + ln]
                idx = data.find(pat)
                if idx >= 0:
                    hits.append(f"ROM[{start}:{start+ln}] "
                                f"({pat.hex().upper()}) at data offset "
                                f"{idx:02X}h")
            if hits:
                break
        rev = data.find(rom[1:7][::-1])
        if rev >= 0:
            hits.append(f"reversed ROM serial (bytes 1..6 of ROM) "
                        f"at offset {rev:02X}h")
        tag = "; ".join(hits) if hits else "no direct ROM-ID bytes found"
        print(f"{p}: {tag}")

    # ---- status registers ----
    print()
    if len({d[3] for d in dumps}) == 1:
        print("Status registers: IDENTICAL in all dumps")
    else:
        print("Status registers DIFFER:")
        for p, rom, data, stat in dumps:
            print(f"   {p}: {' '.join(f'{b:02X}' for b in stat)}")

    print("\nInterpretation aid:")
    print(" * differing bytes + ROM found in data  -> data is bound to the")
    print("   chip's ROM ID (clone on another chip WILL be rejected;")
    print("   use the emulator, or recompute the bound bytes)")
    print(" * differing bytes but NO ROM in data   -> per-cartridge serial/")
    print("   counters; binding may still exist via a checksum/signature")
    print(" * all data identical                   -> no binding; a rejected")
    print("   clone then points at the blank chip itself (family code?)")


if __name__ == "__main__":
    main(sys.argv[1:])
