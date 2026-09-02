/*
 * ============================================================================
 *  DS2502 1-Wire EPROM Bridge  (ESP32 / ESP8266)
 * ============================================================================
 *  Full READ + WRITE support for the DS2502 1Kb Add-Only Memory:
 *
 *    Data memory   : 128 bytes (4 pages x 32 bytes),  0x00 .. 0x7F
 *    Status memory : 8 bytes,                         0x00 .. 0x07
 *
 *  Implemented 1-Wire memory-function commands (per DS2502 datasheet):
 *    Read Memory   [F0h]   - read data EPROM, CRC8 of cmd+address verified
 *    Read Status   [AAh]   - read status EPROM, CRC8 verified
 *    Write Memory  [0Fh]   - program data EPROM  (needs 12V program pulse)
 *    Write Status  [55h]   - program status EPROM(needs 12V program pulse)
 *    Read ROM      [33h]   - 64-bit ROM ID (family 0x09), CRC8 verified
 *
 *  WRITE PROTOCOL (exactly as in the datasheet flow chart):
 *    1st byte : master TX  cmd, TA1, TA2, data
 *               master RX  CRC8(cmd, TA1, TA2, data)      -> must match
 *               master applies 12V / 480us program pulse on DQ
 *               master RX  programmed byte                -> verify (EPROM AND)
 *    next byte: DS2502 auto-increments its address counter
 *               master TX  data
 *               master RX  CRC8 seeded with LSB of NEW address, then data
 *               program pulse, read-back verify ... and so on.
 *
 *  NOTE: EPROM technology - bits can only be changed 1 -> 0, never back!
 *
 * ----------------------------------------------------------------------------
 *  SERIAL PROTOCOL (115200 baud, line based, values in HEX):
 *
 *    PING                       -> OK PONG DS2502-BRIDGE v1.0
 *    ROM                        -> OK ROM 09xxxxxxxxxxxxCC
 *    RDATA <addr> <len>         -> OK DATA <hex bytes>
 *    RSTAT                      -> OK STAT <16 hex chars>          (8 bytes)
 *    WDATA <addr> <hexbytes>    -> BYTE ... lines, then OK WDATA <n>
 *    WSTAT <addr> <hexbytes>    -> BYTE ... lines, then OK WSTAT <n>
 *
 *    Any failure answers:  ERR <reason>
 *
 * ----------------------------------------------------------------------------
 *  PIN CONNECTIONS (see README.md / your png2.pdf 12V pulse circuit):
 *
 *    OW_PIN    GPIO4  (ESP8266 NodeMCU: D2) - 1-Wire DQ, 4.7k pullup to 3V3
 *    PROG_PIN  GPIO5  (ESP8266 NodeMCU: D1) - drives the 12V pulse switch
 *
 *    The PROG_PIN switches +12V onto the DQ line through your external
 *    transistor/MOSFET circuit. Set PROG_ACTIVE_HIGH below to match the
 *    polarity of that circuit (1 = pin HIGH turns 12V on).
 *
 *    !! The ESP32/ESP8266 is a 3.3V device. The DQ GPIO *must* be protected
 *    !! from the 12V pulse (series resistor + Schottky clamp to 3V3, or an
 *    !! equivalent arrangement) - see README.md.
 * ============================================================================
 */

#include <OneWire.h>

/* ------------------------------- user config ----------------------------- */
#define OW_PIN            4      // 1-Wire DQ line (ESP8266 NodeMCU: D2)
#define PROG_PIN          5      // 12V program-pulse control (NodeMCU: D1)
#define PROG_ACTIVE_HIGH  1      // 1: HIGH switches 12V on, 0: LOW switches on
#define PROG_PULSE_US     500    // datasheet tPROG min = 480 us
#define PROG_RECOVERY_US  100    // recovery after pulse before read-back

/* ---------------------------- DS2502 constants --------------------------- */
#define DS2502_FAMILY       0x09
#define CMD_READ_MEMORY     0xF0
#define CMD_READ_STATUS     0xAA
#define CMD_WRITE_MEMORY    0x0F
#define CMD_WRITE_STATUS    0x55
#define DATA_SIZE           0x80   // 128 bytes
#define STATUS_SIZE         0x08   // 8 bytes

OneWire ow(OW_PIN);

static char    lineBuf[600];
static size_t  lineLen = 0;

/* ------------------------------ small helpers ---------------------------- */

// Dallas/Maxim CRC8 (poly X^8+X^5+X^4+1, LSB first), with explicit seed.
// Needed because subsequent write passes seed the CRC with the address LSB.
static uint8_t crc8_update(uint8_t crc, uint8_t data)
{
  for (uint8_t i = 0; i < 8; i++) {
    uint8_t mix = (crc ^ data) & 0x01;
    crc >>= 1;
    if (mix) crc ^= 0x8C;
    data >>= 1;
  }
  return crc;
}

static uint8_t crc8_buf(uint8_t crc, const uint8_t *buf, size_t len)
{
  while (len--) crc = crc8_update(crc, *buf++);
  return crc;
}

static void progPinIdle(void)
{
  digitalWrite(PROG_PIN, PROG_ACTIVE_HIGH ? LOW : HIGH);
}

/* Apply the 12V programming pulse on DQ via the external switch circuit.
 * The OneWire library leaves DQ released (input) after the last time slot,
 * so the line is idling high through the pullup when we hit it with 12V.  */
static void programPulse(void)
{
  noInterrupts();
  digitalWrite(PROG_PIN, PROG_ACTIVE_HIGH ? HIGH : LOW);
  delayMicroseconds(PROG_PULSE_US);
  progPinIdle();
  interrupts();
  delayMicroseconds(PROG_RECOVERY_US);
}

static void printHexByte(uint8_t b)
{
  if (b < 0x10) Serial.print('0');
  Serial.print(b, HEX);
}

static void printHexBuf(const uint8_t *buf, size_t len)
{
  for (size_t i = 0; i < len; i++) printHexByte(buf[i]);
}

/* -------------------------- 1-Wire transactions -------------------------- */

static bool busReset(void)
{
  return ow.reset() == 1;
}

/* Read ROM [33h] - single device on the bus assumed. */
static bool readROM(uint8_t rom[8], const char **err)
{
  if (!busReset()) { *err = "NO_DEVICE (no presence pulse)"; return false; }
  ow.write(0x33);
  for (uint8_t i = 0; i < 8; i++) rom[i] = ow.read();
  if (OneWire::crc8(rom, 7) != rom[7]) { *err = "ROM_CRC"; return false; }
  return true;
}

/* Generic field read: cmd = F0h (data) or AAh (status).
 * Verifies the CRC8 of (cmd, TA1, TA2) that the DS2502 sends back.
 * If the read runs to the end of the field, the trailing CRC8 that the
 * DS2502 generates over all transferred data bytes is verified too.      */
static bool readField(uint8_t cmd, uint16_t addr, uint16_t len,
                      uint16_t fieldSize, uint8_t *buf, const char **err)
{
  if (len == 0 || addr + len > fieldSize) { *err = "RANGE"; return false; }
  if (!busReset()) { *err = "NO_DEVICE (no presence pulse)"; return false; }
  ow.skip();                                    // Skip ROM [CCh]
  uint8_t hdr[3] = { cmd, (uint8_t)(addr & 0xFF), (uint8_t)(addr >> 8) };
  ow.write(hdr[0]); ow.write(hdr[1]); ow.write(hdr[2]);

  uint8_t crc = ow.read();
  if (crc != crc8_buf(0, hdr, 3)) { busReset(); *err = "CMD_CRC"; return false; }

  for (uint16_t i = 0; i < len; i++) buf[i] = ow.read();

  if (addr + len == fieldSize) {                // read the trailing data CRC
    uint8_t dcrc = ow.read();
    if (dcrc != crc8_buf(0, buf, len)) { busReset(); *err = "DATA_CRC"; return false; }
  }
  busReset();
  return true;
}

/* Generic EPROM write: cmd = 0Fh (data) or 55h (status).
 * Full datasheet flow with per-byte CRC check, 12V pulse and read-back.
 * Prints one "BYTE aaaa W=xx R=yy OK/PARTIAL" progress line per byte.     */
static bool writeField(uint8_t cmd, uint16_t addr,
                       const uint8_t *data, uint16_t len,
                       uint16_t fieldSize, const char **err)
{
  static char msg[64];

  if (len == 0 || addr + len > fieldSize) { *err = "RANGE"; return false; }
  if (!busReset()) { *err = "NO_DEVICE (no presence pulse)"; return false; }
  ow.skip();                                    // Skip ROM [CCh]

  for (uint16_t i = 0; i < len; i++) {
    uint16_t a = addr + i;
    uint8_t expect;

    if (i == 0) {
      /* 1st pass: TX cmd, TA1, TA2, data - RX CRC8 of all four bytes */
      uint8_t hdr[4] = { cmd, (uint8_t)(a & 0xFF), (uint8_t)(a >> 8), data[i] };
      ow.write(hdr[0]); ow.write(hdr[1]); ow.write(hdr[2]); ow.write(hdr[3]);
      expect = crc8_buf(0, hdr, 4);
    } else {
      /* subsequent passes: DS2502 has auto-incremented its address counter.
       * TX data - RX CRC8 with the CRC generator *loaded* with the LSB of
       * the new address, then the data byte shifted in.                   */
      ow.write(data[i]);
      expect = crc8_update((uint8_t)(a & 0xFF), data[i]);
    }

    uint8_t crc = ow.read();
    if (crc != expect) {
      busReset();
      snprintf(msg, sizeof(msg), "WRITE_CRC at %04X (got %02X want %02X)",
               a, crc, expect);
      *err = msg;
      return false;
    }

    programPulse();                             // 12V, 480 us

    uint8_t rb = ow.read();                     // read-back of programmed byte

    /* EPROM semantics: byte is the logical AND of everything ever written.
     * Success = every 0-bit we asked for is now 0.                        */
    if ((rb & (uint8_t)~data[i]) != 0) {
      busReset();
      snprintf(msg, sizeof(msg), "VERIFY at %04X (wrote %02X read %02X)",
               a, data[i], rb);
      *err = msg;
      return false;
    }

    Serial.print(F("BYTE "));
    printHexByte((uint8_t)(a >> 8)); printHexByte((uint8_t)(a & 0xFF));
    Serial.print(F(" W=")); printHexByte(data[i]);
    Serial.print(F(" R=")); printHexByte(rb);
    /* rb may have extra 0 bits from earlier programming - flag it */
    Serial.println(rb == data[i] ? F(" OK") : F(" OK(AND)"));
  }

  busReset();
  return true;
}

/* ------------------------------ cmd handlers ----------------------------- */

static bool parseHex(const char *s, uint32_t *out)
{
  if (!s || !*s) return false;
  char *end;
  *out = strtoul(s, &end, 16);
  return *end == '\0';
}

/* Parse a hex string ("A0FF01" or "A0 FF 01") into bytes. */
static int parseHexBytes(char *s, uint8_t *buf, int maxLen)
{
  int n = 0;
  uint8_t nib = 0;
  bool have = false;
  for (; *s; s++) {
    char c = *s;
    int v;
    if (c == ' ' || c == ',' || c == ':') { if (have) return -1; continue; }
    if (c >= '0' && c <= '9')      v = c - '0';
    else if (c >= 'a' && c <= 'f') v = c - 'a' + 10;
    else if (c >= 'A' && c <= 'F') v = c - 'A' + 10;
    else return -1;
    if (!have) { nib = v; have = true; }
    else {
      if (n >= maxLen) return -1;
      buf[n++] = (nib << 4) | v;
      have = false;
    }
  }
  return have ? -1 : n;
}

static void handleLine(char *line)
{
  /* tokenize */
  char *save = NULL;
  char *cmd  = strtok_r(line, " \t", &save);
  if (!cmd) return;
  for (char *p = cmd; *p; p++) *p = toupper(*p);

  const char *err = "?";
  static uint8_t buf[DATA_SIZE];

  /* ---- PING ---- */
  if (!strcmp(cmd, "PING")) {
    Serial.println(F("OK PONG DS2502-BRIDGE v1.0"));
    return;
  }

  /* ---- ROM ---- */
  if (!strcmp(cmd, "ROM")) {
    uint8_t rom[8];
    if (!readROM(rom, &err)) { Serial.print(F("ERR ")); Serial.println(err); return; }
    Serial.print(F("OK ROM "));
    printHexBuf(rom, 8);
    if (rom[0] != DS2502_FAMILY) Serial.print(F(" WARN_FAMILY"));
    Serial.println();
    return;
  }

  /* ---- RDATA <addr> <len> ---- */
  if (!strcmp(cmd, "RDATA")) {
    uint32_t addr = 0, len = DATA_SIZE;
    char *a = strtok_r(NULL, " \t", &save);
    char *l = strtok_r(NULL, " \t", &save);
    if (a && !parseHex(a, &addr)) { Serial.println(F("ERR ARG")); return; }
    if (l && !parseHex(l, &len))  { Serial.println(F("ERR ARG")); return; }
    if (!a) { addr = 0; len = DATA_SIZE; }
    else if (!l) { len = DATA_SIZE - addr; }
    if (!readField(CMD_READ_MEMORY, addr, len, DATA_SIZE, buf, &err)) {
      Serial.print(F("ERR ")); Serial.println(err); return;
    }
    Serial.print(F("OK DATA "));
    printHexBuf(buf, len);
    Serial.println();
    return;
  }

  /* ---- RSTAT ---- */
  if (!strcmp(cmd, "RSTAT")) {
    if (!readField(CMD_READ_STATUS, 0, STATUS_SIZE, STATUS_SIZE, buf, &err)) {
      Serial.print(F("ERR ")); Serial.println(err); return;
    }
    Serial.print(F("OK STAT "));
    printHexBuf(buf, STATUS_SIZE);
    Serial.println();
    return;
  }

  /* ---- WDATA / WSTAT <addr> <hexbytes> ---- */
  bool wdata = !strcmp(cmd, "WDATA");
  bool wstat = !strcmp(cmd, "WSTAT");
  if (wdata || wstat) {
    uint32_t addr;
    char *a = strtok_r(NULL, " \t", &save);
    char *h = strtok_r(NULL, "",   &save);     // rest of line = hex payload
    if (!a || !parseHex(a, &addr) || !h) { Serial.println(F("ERR ARG")); return; }
    int n = parseHexBytes(h, buf, sizeof(buf));
    if (n <= 0) { Serial.println(F("ERR HEX")); return; }

    uint8_t  wcmd  = wdata ? CMD_WRITE_MEMORY : CMD_WRITE_STATUS;
    uint16_t fsize = wdata ? DATA_SIZE        : STATUS_SIZE;

    if (!writeField(wcmd, addr, buf, n, fsize, &err)) {
      Serial.print(F("ERR ")); Serial.println(err); return;
    }
    Serial.print(F("OK ")); Serial.print(wdata ? F("WDATA ") : F("WSTAT "));
    Serial.println(n);
    return;
  }

  Serial.println(F("ERR UNKNOWN_CMD"));
}

/* --------------------------------- setup --------------------------------- */

void setup()
{
  pinMode(PROG_PIN, OUTPUT);
  progPinIdle();                 // make absolutely sure 12V is OFF
  Serial.begin(115200);
  delay(200);
  Serial.println(F("# DS2502 bridge ready (PING/ROM/RDATA/RSTAT/WDATA/WSTAT)"));
}

void loop()
{
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen) {
        lineBuf[lineLen] = '\0';
        handleLine(lineBuf);
        lineLen = 0;
      }
    } else if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    } else {
      lineLen = 0;               // overflow - drop the line
      Serial.println(F("ERR LINE_TOO_LONG"));
    }
  }
}
