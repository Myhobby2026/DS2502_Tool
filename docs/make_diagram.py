#!/usr/bin/env python3
"""Render docs/wiring_diagram.png - DS2502 programmer pin connection diagram.

Pure-matplotlib schematic so the repo can regenerate the image anywhere:
    python3 docs/make_diagram.py
Layout matches docs/wiring_diagram.svg and the defaults in ds2502_bridge.ino
(OW_PIN = GPIO4, PROG_PIN = GPIO5, PROG_ACTIVE_HIGH = 1).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyBboxPatch, Circle, Polygon, Arc

BLACK = "#1a1a1a"
RED = "#c62828"
GNDC = "#455a64"
BLUE = "#0d47a1"
GREY = "#666666"

fig, ax = plt.subplots(figsize=(15.5, 11.25), dpi=140)
ax.set_xlim(0, 124)
ax.set_ylim(0, 90)
ax.invert_yaxis()
ax.axis("off")
fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)


def wire(pts, color=BLACK, lw=2.2):
    xs, ys = zip(*pts)
    ax.plot(xs, ys, color=color, lw=lw, solid_capstyle="round", zorder=2)


def dot(x, y, color=BLACK):
    ax.add_patch(Circle((x, y), 0.45, color=color, zorder=5))


def txt(x, y, s, size=11, weight="normal", color=BLACK, ha="left",
        style="normal"):
    ax.text(x, y, s, fontsize=size, fontweight=weight, color=color, ha=ha,
            va="center", fontstyle=style, zorder=6)


def resistor_v(x, y1, y2, name, value):
    ym1, ym2 = y1 + (y2 - y1) * 0.25, y1 + (y2 - y1) * 0.75
    wire([(x, y1), (x, ym1)])
    wire([(x, ym2), (x, y2)])
    ax.add_patch(Rectangle((x - 1.2, ym1), 2.4, ym2 - ym1, fc="white",
                           ec=BLACK, lw=1.8, zorder=4))
    txt(x - 2, (y1 + y2) / 2 - 1, name, 11, "bold", ha="right")
    txt(x - 2, (y1 + y2) / 2 + 1, value, 10.5, ha="right")


def resistor_h(x1, x2, y, name, label_below=False):
    xm1, xm2 = x1 + (x2 - x1) * 0.2, x1 + (x2 - x1) * 0.8
    wire([(x1, y), (xm1, y)])
    wire([(xm2, y), (x2, y)])
    ax.add_patch(Rectangle((xm1, y - 1.2), xm2 - xm1, 2.4, fc="white",
                           ec=BLACK, lw=1.8, zorder=4))
    txt((x1 + x2) / 2, y + 2.4 if label_below else y - 2.2, name, 11, "bold",
        ha="center")


def box(x, y, w, h, r=1.0):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle=f"round,pad=0,rounding_size={r}",
                                fc="#eceff1", ec="#37474f", lw=2, zorder=1))


# ------------------------------------------------------------------ title
txt(62, 3, "DS2502 Programmer \u2014 Pin Connection Diagram (ESP32 / ESP8266 bridge)",
    17, "bold", ha="center")
txt(62, 6, "matches ds2502_bridge.ino defaults:   OW_PIN = GPIO4,   "
           "PROG_PIN = GPIO5,   PROG_ACTIVE_HIGH = 1", 11, color=GREY,
    ha="center")

# ------------------------------------------------------------- ESP32 board
box(6, 15, 22, 49)
txt(17, 18.5, "ESP32 DevKit /", 12.5, "bold", ha="center")
txt(17, 21, "ESP8266 NodeMCU", 12.5, "bold", ha="center")
txt(17, 23.5, "(3.3 V logic!)", 10.5, color=RED, ha="center")

pins = [(26, "3V3", "", ""), (33, "GPIO4", "(NodeMCU D2)", "1-Wire DQ"),
        (47, "GPIO5", "(NodeMCU D1)", "PROG pulse ctrl"), (60, "GND", "", "")]
for y, name, alias, role in pins:
    wire([(25.5, y), (28, y)], GNDC if name == "GND" else BLACK, 2.5)
    txt(24.8, y - (1.1 if alias else 0), name, 11.5, "bold", BLUE, ha="right")
    if alias:
        txt(24.8, y + 1.2, alias, 9.5, color=GREY, ha="right")
    if role:
        txt(15, y + 3.4, role, 9.5, color=GREY, ha="center", style="italic")

# --------------------------------------------------------------- 3V3 rail
wire([(28, 26), (33, 26), (33, 21), (56, 21)])
txt(44.5, 19.5, "3V3 rail", 11.5, "bold", ha="center")
dot(38, 21)

# clamp diode D1 (anode on GPIO4 net, cathode to 3V3)
wire([(38, 33), (38, 29.4)])
ax.add_patch(Polygon([(36.8, 29.4), (39.2, 29.4), (38, 26.8)], closed=True,
                     fc="white", ec=BLACK, lw=1.8, zorder=4))
wire([(36.8, 26.8), (39.2, 26.8)], lw=3)
wire([(38, 26.8), (38, 21)])
txt(40, 27.4, "D1  BAT54", 11, "bold")
txt(40, 29.4, "Schottky clamp \u2014 protects", 9.5)
txt(40, 31.1, "GPIO4 during the 12 V pulse", 9.5)

# pull-up R1
resistor_v(56, 21, 33, "R1", "4.7 k\u03a9")
txt(57.8, 27.5, "pull-up", 9.5, color=GREY, style="italic")

# --------------------------------------------------------- GPIO4 / DQ net
wire([(28, 33), (41.5, 33)])
dot(38, 33)
resistor_h(41.5, 48.5, 33, "R2  470 \u03a9", label_below=True)
wire([(48.5, 33), (88, 33)])
dot(56, 33)
dot(70, 33)
txt(79, 31.6, "1-Wire DQ bus", 11.5, "bold", BLUE, ha="center")

# ------------------------------------------------- +12V rail, pulse switch
wire([(60, 12), (113, 12)], RED, 2.8)
txt(113.5, 12, "+12 V", 12.5, "bold", RED)
txt(86.5, 10.2, "programming supply 11.5 \u2013 12 V (only during 480 \u00b5s pulse)",
    9.5, color=RED, ha="center")
dot(62, 12, RED)
dot(70, 12, RED)

# R4 gate pull-up
resistor_v(62, 12, 21.5, "R4", "10 k\u03a9")

# Q2 P-MOSFET
ax.add_patch(Circle((70, 18.5), 3.1, fc="white", ec=BLACK, lw=1.8, zorder=4))
txt(70, 17.8, "Q2", 11, "bold", ha="center")
txt(70, 19.6, "P-MOSFET", 7.5, ha="center")
txt(74, 16.3, "AO3401 /", 9.5)
txt(74, 17.9, "IRLML6402", 9.5)
wire([(70, 12), (70, 15.4)], RED, 2.8)
txt(70.9, 14, "S", 9, color=GREY, style="italic")
wire([(66.9, 18.5), (64.5, 18.5), (64.5, 21.5), (62, 21.5)])
txt(65.4, 17.6, "G", 9, color=GREY, style="italic")
dot(62, 21.5)
wire([(70, 21.6), (70, 33)], RED, 2.8)
txt(70.9, 24, "D", 9, color=GREY, style="italic")
txt(71.2, 29, "12 V pulse onto DQ", 9.5, color=RED)

# Q1 NPN driver (collector line hops OVER the DQ bus - no connection there)
wire([(62, 21.5), (62, 32.1)])
ax.add_patch(Arc((62, 33), 1.8, 1.8, theta1=-90, theta2=90,
                 ec=BLACK, lw=2.2, zorder=3))
wire([(62, 33.9), (62, 44)])
ax.add_patch(Circle((62, 47), 3.1, fc="white", ec=BLACK, lw=1.8, zorder=4))
txt(62, 46.3, "Q1", 11, "bold", ha="center")
txt(62, 48.1, "2N3904", 7.5, ha="center")
txt(62.9, 43, "C", 9, color=GREY, style="italic")
wire([(58.9, 47), (56.5, 47)])
txt(59.5, 45.7, "B", 9, color=GREY, style="italic")
wire([(62, 50.1), (62, 60)])
txt(62.9, 52.5, "E", 9, color=GREY, style="italic")

# R3 base resistor
wire([(28, 47), (49.5, 47)])
resistor_h(49.5, 56.5, 47, "R3  1 k\u03a9")

# ------------------------------------------------------------------ DS2502
box(88, 26, 23, 18)
txt(99.5, 29, "DS2502", 13.5, "bold", ha="center")
txt(96, 31.3, "1 Kb Add-Only EPROM", 9.5, ha="center")
wire([(88, 33), (90.5, 33)])
txt(91.2, 33, "2  DQ (DATA)", 10.5, "bold", BLUE)
wire([(88, 39.5), (90.5, 39.5)], GNDC, 2.5)
txt(91.2, 39.5, "1  GND", 10.5, "bold", BLUE)
txt(91.2, 42, "3  NC \u2014 no connect", 9.5, color=GREY)

# TO-92 front view
ax.add_patch(
    plt.matplotlib.patches.Wedge((107.5, 34.2), 2.8, 180, 360, fc="white",
                                 ec=BLACK, lw=1.6, zorder=4))
ax.add_patch(Rectangle((104.7, 34.2), 5.6, 1.6, fc="white", ec=BLACK,
                       lw=1.6, zorder=4))
for i, xleg in enumerate((105.7, 107.5, 109.3)):
    wire([(xleg, 35.8), (xleg, 37.6)], lw=1.6)
    txt(xleg, 38.6, str(i + 1), 8, ha="center")
txt(107.5, 40.6, "TO-92, flat face", 8, color=GREY, ha="center", style="italic")
txt(107.5, 42.2, "toward you", 8, color=GREY, ha="center", style="italic")

# DS2502 GND route
wire([(88, 39.5), (84.5, 39.5), (84.5, 60)], GNDC, 2.5)

# --------------------------------------------------------------- GND rail
wire([(28, 60), (113, 60)], GNDC, 2.8)
txt(113.5, 60, "GND", 12.5, "bold", GNDC)
dot(62, 60)
dot(84.5, 60)
txt(96, 58.6, "common ground \u2014 ESP32, DS2502 and the +12 V supply GND "
              "all tie here", 9, color=GREY, ha="center", style="italic")

# ------------------------------------------------------------------- notes
ax.add_patch(FancyBboxPatch((6, 65), 112, 23,
                            boxstyle="round,pad=0,rounding_size=1",
                            fc="#fffde7", ec="#f9a825", lw=2, zorder=1))
notes = [
    ("How it works / notes", "bold", BLACK),
    ("\u2022 READ / normal 1-Wire traffic:  GPIO4 \u21c4 R2 (470 \u03a9) \u21c4 DQ bus, "
     "idling high through R1 (4.7 k\u03a9 to 3V3).  DS2502 is parasite-powered \u2014 only DQ and GND are wired.",
     "normal", BLACK),
    ("\u2022 WRITE (program) pulse:  bridge drives GPIO5 HIGH for ~500 \u00b5s \u2192 Q1 on \u2192 "
     "Q2 gate pulled low \u2192 Q2 switches +12 V onto DQ (tPROG \u2265 480 \u00b5s per datasheet).",
     "normal", BLACK),
    ("   R4 keeps Q2 firmly OFF at all other times, and the sketch parks GPIO5 LOW in setup(), "
     "so 12 V is never applied accidentally.", "normal", BLACK),
    ("\u2022 Protection (mandatory on a 3.3 V MCU):  during the pulse R2 limits the current and D1 clamps "
     "the GPIO4 node to \u22483.6 V. Never wire DQ directly to the ESP.", "normal", BLACK),
    ("\u2022 Polarity:  this circuit is active-HIGH \u2192 keep PROG_ACTIVE_HIGH = 1 in ds2502_bridge.ino "
     "(set 0 if your switch inverts).", "normal", BLACK),
    ("\u26a0 Remove ALL other (non-EPROM) 1-Wire devices from the bus before programming \u2014 "
     "the 12 V pulse will destroy them.", "normal", RED),
    ("\u26a0 DS2502 is EPROM: bits only go 1 \u2192 0, permanently. Double-check data before writing.",
     "normal", RED),
]
y = 67.4
for s, w, c in notes:
    txt(8, y, s, 10.5 if w == "normal" else 12, w, c)
    y += 2.65

fig.savefig("docs/wiring_diagram.png", facecolor="white")
print("wrote docs/wiring_diagram.png")
