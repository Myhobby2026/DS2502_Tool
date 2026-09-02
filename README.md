# DS2502_Tool

Read **and write** tool for the **DS2502 1Kb Add-Only Memory** (1-Wire EPROM,
family code `09h`), e.g. as used in Apple MagSafe / Dell / HP power-adapter
identification.

| Component | File | Role |
|---|---|---|
| Bridge firmware | [`ds2502_bridge/ds2502_bridge.ino`](ds2502_bridge/ds2502_bridge.ino) | ESP32 / ESP8266, drives the 1-Wire bus + 12V program pulse |
| Desktop GUI | [`ds2502_gui.py`](ds2502_gui.py) | Python/Tkinter app talking to the bridge over USB serial |

## Features

* Read 64-bit ROM ID (Read ROM `33h`, CRC verified)
* Read full 128-byte data memory (Read Memory `F0h`, CRCs verified)
* **Write data memory** (Write Memory `0Fh`) — full datasheet flow:
  per-byte CRC8 check → 12V/480µs program pulse → read-back verification →
  auto-increment CRC rule for subsequent bytes
* Read status register (Read Status `AAh`) with full decoding
* **Write status register** (Write Status `55h`):
  * raw byte writes
  * one-click **write-protect page 0–3** (status byte `00h`, bits WP0–WP3)
  * **page redirection** helper (bytes `01h`–`04h`, one's complement of the
    new page number, `FFh` = no redirection)
* Save memory dump to `.bin`, program a `.bin` file into the chip

> ⚠️ **The DS2502 is EPROM — bits can only be programmed 1 → 0 and can never
> be erased.** All writes are permanent. The GUI asks for confirmation before
> every write.

## Status register map (DS2502 datasheet)

| Addr | Meaning |
|---|---|
| `00h` | Write-protect bits: bit0–bit3 = WP for data pages 0–3 (0 = protected) |
| `01h`–`04h` | Page redirection bytes for pages 0–3 (one's complement of new page, `FFh` = none) |
| `05h`–`06h` | Reserved (`FFh`) |
| `07h` | Factory programmed `00h` |

## Pin connections

![Wiring diagram](docs/wiring_diagram.png)

*(diagram sources: [`docs/wiring_diagram.svg`](docs/wiring_diagram.svg), regenerable PNG via `python3 docs/make_diagram.py` — adapt the `#define`s at the top of the sketch if your pins differ)*

```
 ESP32 / ESP8266 (3.3V!)                                 DS2502
 ─────────────────────────                              ────────
                                     3V3
                                      │
                                     ┌┴┐
                                     │ │ 4.7kΩ  (1-Wire pullup)
                                     └┬┘
 GPIO4 (NodeMCU D2) ──[470Ω]──●──────┴──────────●────── DQ (pin 2)
   1-Wire DQ                  │                 │
                        BAT54 ▼ (clamp          │
                        to 3V3, protects        │
                        GPIO from 12V)          │
                                                │
 GPIO5 (NodeMCU D1) ───► 12V pulse switch ──────┘
   PROG control          (PNP/P-MOSFET high-side
                          switch from +12V rail,
                          e.g. NPN level shifter
                          + P-MOSFET; ~480µs pulse)

 GND ───────────────────────────────────────────●────── GND (pin 1)
                                                │
 +12V supply GND ───────────────────────────────┘
```

Notes:

* The DS2502 is parasite-powered — only **DQ** and **GND** are connected.
* **Programming needs a clean 11.5–12V pulse (480 µs)** on DQ. The bridge
  raises `PROG_PIN` for 500 µs; your external transistor/MOSFET circuit
  switches +12V onto the DQ line during that time.
* The ESP GPIO must survive the 12V pulse — the series resistor + Schottky
  clamp above (or an equivalent from your circuit) is **mandatory** on a
  3.3V MCU.
* Set `PROG_ACTIVE_HIGH` in the sketch to match your switch polarity
  (default: pin HIGH = 12V on). The pin is driven to its idle level in
  `setup()` so 12V is never applied accidentally.
* **Never** put other (non-EPROM) 1-Wire devices on the bus while
  programming — 12V will damage them.

## Bridge — build & flash

1. Arduino IDE (or `arduino-cli`) with the ESP32/ESP8266 core installed
2. Install the **OneWire** library (Paul Stoffregen) via Library Manager
3. Open `ds2502_bridge/ds2502_bridge.ino`, check `OW_PIN`, `PROG_PIN`,
   `PROG_ACTIVE_HIGH`, then flash. Serial monitor: **115200 baud**.

### Serial protocol (usable manually from any terminal)

```
PING                     -> OK PONG DS2502-BRIDGE v1.0
ROM                      -> OK ROM 09A1B2C3D4E5F607
RDATA <addr> <len>       -> OK DATA 55AA...        (hex, addr/len in hex)
RSTAT                    -> OK STAT FFFFFFFFFFFFFF00
WDATA <addr> <hexbytes>  -> BYTE 0000 W=55 R=55 OK ... OK WDATA <n>
WSTAT <addr> <hexbytes>  -> BYTE 0000 W=FE R=FE OK ... OK WSTAT <n>
```

Errors answer `ERR <reason>` (`NO_DEVICE`, `CMD_CRC`, `WRITE_CRC at ...`,
`VERIFY at ...`, `RANGE`, ...). A write aborts on the first failed byte.

## GUI — run

```bash
pip install -r requirements.txt
python ds2502_gui.py
```

Select the bridge's COM port → **Connect** → use the *Data memory* and
*Status register* tabs.
