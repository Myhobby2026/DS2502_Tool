/*
 * ============================================================================
 *  DS2502 1-Wire EPROM Bridge  (ESP32)  -  split-pin driver-board edition
 * ============================================================================
 *  Matches the png2 driver schematic:
 *
 *    OW_TX  = GPIO25 - drive:  R2 1k -> Q1 2N7002 gate, Q1 drain on the bus.
 *                      ** INVERTED **  GPIO25 HIGH = bus pulled LOW.
 *    OW_RX  = GPIO26 - sense:  bus -> R3 10k -> GPIO26, BAT54S clamp
 *                      (pin3 = GPIO26-side node of R3, pin1 = GND, pin2 = 3V3)
 *                      so GPIO26 survives the 12 V VPP pulse.
 *    OW_VPP = GPIO27 - 12 V enable: R5 1k -> Q3 2N7002 -> pulls the gate node
 *                      (Q2.G + R4 1k pull-up + C1 470p) low -> Q2 AO3401A
 *                      P-FET switches VPP (+11.75 V from MT3608) onto the bus.
 *                      ** ACTIVE HIGH **  GPIO27 HIGH = 12 V on the bus.
 *
 *    Bus pull-up: R1 4.7k to 3V3.   DS2502 TO-92: 1=GND, 2=DATA, 3=NC.
 *
 *    !! Set the MT3608 boost to 11.75 V (measured under load) BEFORE
 *    !! connecting the DS2502. VPP must stay within 11.5 - 12.0 V.
 *
 *  Read-only quick build (no 12 V, no driver parts):
 *    set OW_USE_DRIVER 0 below -> single bidirectional pin (GPIO4) driven
 *    open-drain, bus + 4.7k pull-up to 3V3. Reads work, writes are refused.
 *
 *  DS2502 protocol implemented (per datasheet):
 *    Read ROM [33h], Read Memory [F0h], Read Status [AAh],
 *    Write Memory [0Fh], Write Status [55h]  - full write flow with per-byte
 *    CRC8 check, 480 us VPP pulse, read-back verify, auto-increment CRC rule.
 *
 * ----------------------------------------------------------------------------
 *  SERIAL PROTOCOL (115200 baud, line based, values in HEX):
 *    PING / DIAG / SEARCH / ROM
 *    RDATA <addr> <len>   RSTAT   WDATA <addr> <hex>   WSTAT <addr> <hex>
 *  Answers: OK ... / ERR ... ; writes print one BYTE line per byte.
 * ============================================================================
 */

#include <Arduino.h>

/* ------------------------------- user config ----------------------------- */
#define OW_USE_DRIVER   1     /* 1 = split-pin driver board (png2 schematic) */
                              /* 0 = read-only single-pin quick build        */

#if OW_USE_DRIVER
  #define OW_TX_PIN     25    /* drive  (inverting, via Q1 2N7002)           */
  #define OW_RX_PIN     26    /* sense  (via R3 10k + BAT54S clamp)          */
  #define OW_VPP_PIN    27    /* 12V enable (active HIGH, via Q3 + Q2)       */
#else
  #define OW_PIN        4     /* single bidirectional pin, open-drain        */
#endif

#define PROG_PULSE_US   500   /* datasheet tPROG min = 480 us                */
#define PROG_RECOVERY_US 200  /* bus settle time after the VPP pulse         */

/* ---------------------------- DS2502 constants --------------------------- */
#define DS2502_FAMILY       0x09
#define CMD_READ_MEMORY     0xF0
#define CMD_READ_STATUS     0xAA
#define CMD_WRITE_MEMORY    0x0F
#define CMD_WRITE_STATUS    0x55
#define DATA_SIZE           0x80
#define STATUS_SIZE         0x08

static char    lineBuf[600];
static size_t  lineLen = 0;

/* ========================================================================== */
/*  Low-level 1-Wire bit-bang layer                                           */
/* ========================================================================== */
#if OW_USE_DRIVER

/* Q1 is an inverting open-drain driver: TX HIGH -> bus LOW */
static inline void busDriveLow(void) { digitalWrite(OW_TX_PIN, HIGH); }
static inline void busRelease(void)  { digitalWrite(OW_TX_PIN, LOW);  }
static inline int  busLevel(void)    { return digitalRead(OW_RX_PIN); }
static inline void vppOff(void)      { digitalWrite(OW_VPP_PIN, LOW); }

static void owPinsInit(void)
{
  pinMode(OW_VPP_PIN, OUTPUT); vppOff();        /* 12V OFF, first thing      */
  pinMode(OW_TX_PIN,  OUTPUT); busRelease();
  pinMode(OW_RX_PIN,  INPUT);
}

#else  /* ------- read-only single-pin build: open-drain on OW_PIN --------- */

static inline void busDriveLow(void) { pinMode(OW_PIN, OUTPUT);
                                       digitalWrite(OW_PIN, LOW); }
static inline void busRelease(void)  { pinMode(OW_PIN, INPUT); }
static inline int  busLevel(void)    { return digitalRead(OW_PIN); }
static inline void vppOff(void)      { }

static void owPinsInit(void)
{
  busRelease();
}

#endif

/* Standard-speed 1-Wire timing (identical for both builds) */
static bool owReset(void)
{
  busRelease();
  delayMicroseconds(5);
  noInterrupts();
  busDriveLow();
  delayMicroseconds(480);
  busRelease();
  delayMicroseconds(70);
  bool presence = (busLevel() == LOW);
  interrupts();
  delayMicroseconds(410);
  return presence;
}

static void owWriteBit(uint8_t b)
{
  noInterrupts();
  if (b) { busDriveLow(); delayMicroseconds(6);  busRelease(); delayMicroseconds(64); }
  else   { busDriveLow(); delayMicroseconds(60); busRelease(); delayMicroseconds(10); }
  interrupts();
}

static uint8_t owReadBit(void)
{
  noInterrupts();
  busDriveLow();
  delayMicroseconds(6);
  busRelease();
  delayMicroseconds(9);                 /* sample ~15 us into the slot */
  uint8_t b = busLevel() ? 1 : 0;
  interrupts();
  delayMicroseconds(55);
  return b;
}

static void owWrite(uint8_t v)
{
  for (uint8_t i = 0; i < 8; i++) { owWriteBit(v & 0x01); v >>= 1; }
}

static uint8_t owRead(void)
{
  uint8_t v = 0;
  for (uint8_t i = 0; i < 8; i++) v |= (owReadBit() << i);
  return v;
}

static inline void owSkip(void) { owWrite(0xCC); }   /* Skip ROM */

/* -------- 12 V programming pulse (driver build only) ---------------------- */
#if OW_USE_DRIVER
static bool progPulse(void)
{
  busRelease();                        /* bus MUST idle high before VPP      */
  delayMicroseconds(5);
  noInterrupts();
  digitalWrite(OW_VPP_PIN, HIGH);      /* Q3 on -> Q2 gate low -> 12V on bus */
  delayMicroseconds(PROG_PULSE_US);
  digitalWrite(OW_VPP_PIN, LOW);
  interrupts();
  delayMicroseconds(PROG_RECOVERY_US); /* let bus fall back to 3V3 level    */
  return true;
}
#endif

/* ------------------------------ CRC8 (Dallas) ----------------------------- */
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

/* ------------------------------ small helpers ---------------------------- */
static void printHexByte(uint8_t b)
{
  if (b < 0x10) Serial.print('0');
  Serial.print(b, HEX);
}

static void printHexBuf(const uint8_t *buf, size_t len)
{
  for (size_t i = 0; i < len; i++) printHexByte(buf[i]);
}

/* ========================================================================== */
/*  1-Wire ROM search (standard Maxim algorithm, works in both builds)        */
/* ========================================================================== */
static uint8_t srchRom[8];
static int     srchLastDisc;
static bool    srchDone;

static void searchInit(void)
{
  srchLastDisc = 0;
  srchDone = false;
  memset(srchRom, 0, sizeof(srchRom));
}

static bool searchNext(void)
{
  if (srchDone) return false;
  if (!owReset()) { srchDone = true; return false; }
  owWrite(0xF0);                                   /* Search ROM */
  int lastZero = 0;
  for (int pos = 1; pos <= 64; pos++) {
    uint8_t idBit  = owReadBit();
    uint8_t cmpBit = owReadBit();
    uint8_t dir;
    if (idBit && cmpBit) { srchDone = true; return false; }
    if (idBit != cmpBit) {
      dir = idBit;
    } else {
      if (pos < srchLastDisc)
        dir = (srchRom[(pos - 1) >> 3] >> ((pos - 1) & 7)) & 1;
      else
        dir = (pos == srchLastDisc);
      if (!dir) lastZero = pos;
    }
    if (dir) srchRom[(pos - 1) >> 3] |=  (1 << ((pos - 1) & 7));
    else     srchRom[(pos - 1) >> 3] &= ~(1 << ((pos - 1) & 7));
    owWriteBit(dir);
  }
  srchLastDisc = lastZero;
  if (lastZero == 0) srchDone = true;
  return true;
}

/* ========================================================================== */
/*  DS2502 transactions                                                       */
/* ========================================================================== */
static bool readROM(uint8_t rom[8], const char **err)
{
  if (!owReset()) { *err = "NO_DEVICE (no presence pulse)"; return false; }
  owWrite(0x33);
  for (uint8_t i = 0; i < 8; i++) rom[i] = owRead();
  if (crc8_buf(0, rom, 7) != rom[7]) { *err = "ROM_CRC"; return false; }
  return true;
}

static bool readField(uint8_t cmd, uint16_t addr, uint16_t len,
                      uint16_t fieldSize, uint8_t *buf, const char **err)
{
  if (len == 0 || addr + len > fieldSize) { *err = "RANGE"; return false; }
  if (!owReset()) { *err = "NO_DEVICE (no presence pulse)"; return false; }
  owSkip();
  uint8_t hdr[3] = { cmd, (uint8_t)(addr & 0xFF), (uint8_t)(addr >> 8) };
  owWrite(hdr[0]); owWrite(hdr[1]); owWrite(hdr[2]);

  uint8_t crc = owRead();
  if (crc != crc8_buf(0, hdr, 3)) { owReset(); *err = "CMD_CRC"; return false; }

  for (uint16_t i = 0; i < len; i++) buf[i] = owRead();

  if (addr + len == fieldSize) {                 /* trailing data CRC */
    uint8_t dcrc = owRead();
    if (dcrc != crc8_buf(0, buf, len)) { owReset(); *err = "DATA_CRC"; return false; }
  }
  owReset();
  return true;
}

static bool writeField(uint8_t cmd, uint16_t addr,
                       const uint8_t *data, uint16_t len,
                       uint16_t fieldSize, const char **err)
{
#if !OW_USE_DRIVER
  (void)cmd; (void)addr; (void)data; (void)len; (void)fieldSize;
  *err = "READ_ONLY_BUILD (OW_USE_DRIVER=0: no 12V driver hardware)";
  return false;
#else
  static char msg[64];

  if (len == 0 || addr + len > fieldSize) { *err = "RANGE"; return false; }
  if (!owReset()) { *err = "NO_DEVICE (no presence pulse)"; return false; }
  owSkip();

  for (uint16_t i = 0; i < len; i++) {
    uint16_t a = addr + i;
    uint8_t expect;

    if (i == 0) {
      uint8_t hdr[4] = { cmd, (uint8_t)(a & 0xFF), (uint8_t)(a >> 8), data[i] };
      owWrite(hdr[0]); owWrite(hdr[1]); owWrite(hdr[2]); owWrite(hdr[3]);
      expect = crc8_buf(0, hdr, 4);
    } else {
      /* DS2502 auto-incremented: CRC generator LOADED with new addr LSB */
      owWrite(data[i]);
      expect = crc8_update((uint8_t)(a & 0xFF), data[i]);
    }

    uint8_t crc = owRead();
    if (crc != expect) {
      owReset();
      snprintf(msg, sizeof(msg), "WRITE_CRC at %04X (got %02X want %02X)",
               a, crc, expect);
      *err = msg;
      return false;
    }

    progPulse();                                  /* 12 V, 480+ us */

    uint8_t rb = owRead();                        /* programmed byte */
    if ((rb & (uint8_t)~data[i]) != 0) {          /* EPROM AND semantics */
      owReset();
      snprintf(msg, sizeof(msg), "VERIFY at %04X (wrote %02X read %02X)",
               a, data[i], rb);
      *err = msg;
      return false;
    }

    Serial.print(F("BYTE "));
    printHexByte((uint8_t)(a >> 8)); printHexByte((uint8_t)(a & 0xFF));
    Serial.print(F(" W=")); printHexByte(data[i]);
    Serial.print(F(" R=")); printHexByte(rb);
    Serial.println(rb == data[i] ? F(" OK") : F(" OK(AND)"));
  }

  owReset();
  return true;
#endif
}

/* ========================================================================== */
/*  command parsing                                                           */
/* ========================================================================== */
static bool parseHex(const char *s, uint32_t *out)
{
  if (!s || !*s) return false;
  char *end;
  *out = strtoul(s, &end, 16);
  return *end == '\0';
}

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
  char *save = NULL;
  char *cmd  = strtok_r(line, " \t", &save);
  if (!cmd) return;
  for (char *p = cmd; *p; p++) *p = toupper(*p);

  const char *err = "?";
  static uint8_t buf[DATA_SIZE];

  /* ---- PING ---- */
  if (!strcmp(cmd, "PING")) {
#if OW_USE_DRIVER
    Serial.println(F("OK PONG DS2502-BRIDGE v2.0 driver(TX25/RX26/VPP27)"));
#else
    Serial.println(F("OK PONG DS2502-BRIDGE v2.0 read-only(GPIO4)"));
#endif
    return;
  }

  /* ---- DIAG : bus health check ---- */
  if (!strcmp(cmd, "DIAG")) {
    busRelease();
    vppOff();
    delayMicroseconds(100);
    int idle = busLevel();
    Serial.print(F("DQ idle level: "));
    Serial.println(idle
      ? F("HIGH - good (R1 pull-up working)")
      : F("LOW  - BAD! short, missing R1 4.7k pull-up, or DS2502 GND/DATA swapped"));

    busDriveLow();
    delayMicroseconds(30);
    int drv = busLevel();
    busRelease();
    delayMicroseconds(100);
    int rel = busLevel();
    Serial.print(F("Drive test: "));
#if OW_USE_DRIVER
    Serial.println(!drv && rel
      ? F("TX pulls bus LOW, releases HIGH - good (Q1/R2/R3 path OK)")
      : (drv ? F("bus did NOT go low - BAD! check GPIO25->R2->Q1 gate, Q1 S to GND, Q1 D to bus")
             : F("bus stuck LOW after release - BAD! Q1 always on or short")));
#else
    Serial.println(!drv && rel ? F("OK") : F("FAILED - line stuck"));
#endif

    uint8_t hits = 0;
    for (uint8_t i = 0; i < 3; i++) { if (owReset()) hits++; delay(2); }
    Serial.print(F("Presence pulse: "));
    Serial.print(hits); Serial.println(F("/3 resets answered"));
    if (!hits) {
      Serial.println(F("-> no device: check DS2502 pinout (TO-92 flat face"));
      Serial.println(F("   front: 1=GND 2=DATA 3=NC) and that DATA is on the bus"));
    }

    Serial.print(F("OK DIAG IDLE=")); Serial.print(idle);
    Serial.print(F(" DRIVELOW="));    Serial.print(drv ? 0 : 1);
    Serial.print(F(" RELEASE="));     Serial.print(rel);
    Serial.print(F(" PRESENCE="));    Serial.println(hits);
    return;
  }

  /* ---- SEARCH ---- */
  if (!strcmp(cmd, "SEARCH")) {
    uint8_t n = 0;
    searchInit();
    while (searchNext()) {
      Serial.print(F("DEV "));
      printHexBuf(srchRom, 8);
      Serial.print(crc8_buf(0, srchRom, 7) == srchRom[7] ? F(" CRC=OK") : F(" CRC=BAD"));
      if (srchRom[0] == DS2502_FAMILY) Serial.print(F(" (DS2502)"));
      Serial.println();
      n++;
    }
    Serial.print(F("OK SEARCH ")); Serial.println(n);
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
    char *h = strtok_r(NULL, "",   &save);
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
  owPinsInit();                 /* VPP forced OFF before anything else */
  Serial.begin(115200);
  delay(200);
  Serial.println(F("# DS2502 bridge v2.0 ready (PING/DIAG/SEARCH/ROM/RDATA/RSTAT/WDATA/WSTAT)"));
#if OW_USE_DRIVER
  Serial.println(F("# driver build: OW_TX=GPIO25 OW_RX=GPIO26 OW_VPP=GPIO27"));
#else
  Serial.println(F("# read-only build: OW_PIN=GPIO4 (writes disabled)"));
#endif
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
      lineLen = 0;
      Serial.println(F("ERR LINE_TOO_LONG"));
    }
  }
}
