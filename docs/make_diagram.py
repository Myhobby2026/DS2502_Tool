#!/usr/bin/env python3
"""Render docs/wiring_diagram.png - DS2502 driver-board pin connection diagram.

Matches the png2 driver schematic and the defaults in ds2502_bridge.ino:
    OW_USE_DRIVER = 1,  OW_TX = GPIO25,  OW_RX = GPIO26,  OW_VPP = GPIO27
Regenerate with:  python3 docs/make_diagram.py
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyBboxPatch, Circle, Polygon, Arc

BLACK = "#1a1a1a"
RED = "#c62828"
GNDC = "#455a64"
BLUE = "#1565c0"
GREEN = "#2e7d32"
ORANGE = "#e65100"
GREY = "#666666"

fig, ax = plt.subplots(figsize=(15.5, 11.75), dpi=140)
ax.set_xlim(0, 124)
ax.set_ylim(0, 94)
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


def gnd_sym(x, y, color=GNDC):
    wire([(x, y), (x, y + 1.2)], color, 2)
    for i, w in enumerate((1.8, 1.2, 0.6)):
        yy = y + 1.2 + i * 0.7
        wire([(x - w, yy), (x + w, yy)], color, 2)


def arrow_up(x, y, label, color, side=False):
    wire([(x, y), (x, y - 2.2)], color, 2.4)
    ax.add_patch(Polygon([(x - 0.8, y - 2.2), (x + 0.8, y - 2.2),
                          (x, y - 3.6)], closed=True, fc=color, ec=color,
                         zorder=5))
    if side:
        txt(x + 1.3, y - 2.6, label, 9.5, "bold", color)
    else:
        txt(x, y - 4.8, label, 10, "bold", color, ha="center")


def resistor_v(x, y1, y2, name, lblside="left"):
    ym1, ym2 = y1 + (y2 - y1) * 0.22, y1 + (y2 - y1) * 0.78
    wire([(x, y1), (x, ym1)])
    wire([(x, ym2), (x, y2)])
    ax.add_patch(Rectangle((x - 1.1, min(ym1, ym2)), 2.2, abs(ym2 - ym1),
                           fc="white", ec=BLACK, lw=1.8, zorder=4))
    if lblside == "left":
        txt(x - 1.8, (y1 + y2) / 2, name, 10, "bold", ha="right")
    else:
        txt(x + 1.8, (y1 + y2) / 2, name, 10, "bold")


def resistor_h(x1, x2, y, name, above=True, color=BLACK):
    xm1, xm2 = x1 + (x2 - x1) * 0.2, x1 + (x2 - x1) * 0.8
    wire([(x1, y), (xm1, y)], color)
    wire([(xm2, y), (x2, y)], color)
    ax.add_patch(Rectangle((min(xm1, xm2), y - 1.1), abs(xm2 - xm1), 2.2,
                           fc="white", ec=BLACK, lw=1.8, zorder=4))
    txt((x1 + x2) / 2, y - 2.1 if above else y + 2.3, name, 10, "bold",
        ha="center")


def fet(cx, cy, name, part):
    ax.add_patch(Circle((cx, cy), 3.1, fc="white", ec=BLACK, lw=1.8, zorder=4))
    txt(cx, cy - 0.7, name, 11, "bold", ha="center")
    txt(cx, cy + 1.1, part, 7.5, ha="center")


def hop_v(x, y):
    """Vertical wire hops over a horizontal wire at (x, y)."""
    ax.add_patch(Arc((x, y), 1.8, 1.8, theta1=-90, theta2=90,
                     ec=BLACK, lw=2.2, zorder=3))


def diode(x, y1, y2, up):
    """Vertical diode from y1 to y2; up=True -> conducts from y1 (bottom
    anode) to y2 (top cathode)."""
    ym = (y1 + y2) / 2
    wire([(x, y1), (x, ym + (1.1 if up else -1.1))])
    if up:
        ax.add_patch(Polygon([(x - 1.1, ym + 1.1), (x + 1.1, ym + 1.1),
                              (x, ym - 1.1)], closed=True, fc="white",
                             ec=BLACK, lw=1.6, zorder=4))
        wire([(x - 1.1, ym - 1.1), (x + 1.1, ym - 1.1)], BLACK, 2.6)
        wire([(x, ym - 1.1), (x, y2)])
    else:
        ax.add_patch(Polygon([(x - 1.1, ym - 1.1), (x + 1.1, ym - 1.1),
                              (x, ym + 1.1)], closed=True, fc="white",
                             ec=BLACK, lw=1.6, zorder=4))
        wire([(x - 1.1, ym + 1.1), (x + 1.1, ym + 1.1)], BLACK, 2.6)
        wire([(x, ym + 1.1), (x, y2)])


# ------------------------------------------------------------------ title
txt(62, 2.5, "DS2502 Programmer \u2014 Driver-Board Pin Connections (ESP32)",
    17, "bold", ha="center")
txt(62, 5.6, "firmware defaults:  OW_USE_DRIVER = 1,  OW_TX = GPIO25,  "
             "OW_RX = GPIO26,  OW_VPP = GPIO27", 11, color=GREY, ha="center")

# ------------------------------------------------------------- ESP32 board
ax.add_patch(FancyBboxPatch((6, 10), 22, 56,
                            boxstyle="round,pad=0,rounding_size=1",
                            fc="#e3f2fd", ec="#37474f", lw=2, zorder=1))
txt(17, 13, "ESP32 DevKit", 12.5, "bold", ha="center")
txt(17, 15.6, "classic 30/38-pin", 9, color=GREY, ha="center")
txt(17, 18.2, "(S2/S3: GPIO25 absent \u2014", 8, color=GREY, ha="center")
txt(17, 19.9, "pick other pins)", 8, color=GREY, ha="center")

for y, name, role, col in ((16, "3V3", "", ORANGE),
                           (26, "GPIO25", "OW_TX  (drive)", GREEN),
                           (44, "GPIO26", "OW_RX  (sense)", GREEN),
                           (54, "GPIO27", "OW_VPP (12V enable)", GREEN),
                           (62, "GND", "", GNDC)):
    txt(27.2, y - (1.2 if role else 0), name, 11, "bold", "#0d47a1",
        ha="right")
    if role:
        txt(27.2, y + 1.3, role, 8.5, color=GREEN, ha="right")

# ESP GND + 3V3 stubs
wire([(28, 62), (33, 62)], GNDC, 2.4)
gnd_sym(33, 62)
wire([(28, 16), (33, 16)], ORANGE, 2.4)
arrow_up(33, 16, "+3V3", ORANGE)

# =================================================================== BUS
# vertical 1-Wire bus at x=88 from Q2 drain (y=15) to DS2502 (y=52)
wire([(88, 15), (88, 52)], BLUE, 3.4)
txt(89.2, 36, "1-Wire BUS (DATA)", 10, "bold", BLUE)

# ------------------------------------------------ TX driver: R2 + Q1 2N7002
resistor_h(28, 41, 26, "R2  1k", color=GREEN)
fet(48, 26, "Q1", "2N7002")
wire([(41, 26), (44.9, 26)], GREEN)
txt(45.6, 24.4, "G", 8, color=GREY, style="italic")
wire([(51.1, 26), (88, 26)], BLUE, 3.0)          # drain -> bus
dot(88, 26, BLUE)
txt(52.3, 24.4, "D", 8, color=GREY, style="italic")
wire([(48, 29.1), (48, 32)])                      # source -> GND
txt(49, 30.6, "S", 8, color=GREY, style="italic")
gnd_sym(48, 32)
txt(59, 28.8, "GPIO25 HIGH  \u21d2  bus LOW   (inverting!)", 9, color=GREY,
    style="italic")

# ------------------------------------------ VPP chain: R5 + Q3 + gate node
resistor_h(28, 50, 54, "R5  1k", above=False, color=GREEN)
fet(57, 54, "Q3", "2N7002")
wire([(50, 54), (53.9, 54)], GREEN)
txt(54.4, 52.4, "G", 8, color=GREY, style="italic")
wire([(57, 57.1), (57, 59.5)])
txt(58, 58.2, "S", 8, color=GREY, style="italic")
gnd_sym(57, 59.5)
# drain up to the gate node, hopping over the RX row (y=44) and TX row (y=26)
wire([(57, 50.9), (57, 44.9)])
hop_v(57, 44)
wire([(57, 43.1), (57, 26.9)])
hop_v(57, 26)
wire([(57, 25.1), (57, 15)])
txt(58, 48.5, "D", 8, color=GREY, style="italic")

# gate node (Q2.G + R4 + C1)
wire([(57, 15), (68.9, 15)])
dot(57, 15)
dot(62, 15)
dot(66, 15)
txt(56, 13.4, "gate node", 8.5, color=GREY, ha="right", style="italic")
resistor_v(62, 15, 9.5, "")
txt(60.6, 10.6, "R4 1k", 9, "bold", ha="right")
arrow_up(62, 9.5, "+12V", RED, side=True)
# C1 470p
wire([(66, 15), (66, 17.6)])
wire([(64.7, 17.6), (67.3, 17.6)], BLACK, 2.4)
wire([(64.7, 18.6), (67.3, 18.6)], BLACK, 2.4)
wire([(66, 18.6), (66, 20.2)])
gnd_sym(66, 20.2)
txt(68, 19.2, "C1 470p", 8.5)

# Q2 AO3401A P-FET high-side switch
fet(72, 15, "Q2", "AO3401A")
txt(69.6, 13.2, "G", 8, color=GREY, style="italic")
wire([(72, 11.9), (72, 9.5)], RED, 2.6)
txt(70.9, 10.8, "S", 8, color=GREY, style="italic")
arrow_up(72, 9.5, "+12V (VPP)", RED, side=True)
wire([(75.1, 15), (88, 15)], BLUE, 3.0)          # drain -> top of bus
txt(76.2, 13.4, "D", 8, color=GREY, style="italic")
txt(89.5, 25.3, "GPIO27 HIGH \u21d2 12 V pulse", 8.5, color=RED,
    style="italic")

# ------------------------------------------------- bus pull-up R1 to 3V3
dot(88, 20, BLUE)
resistor_h(88, 99, 20, "R1  4.7k", above=False)
arrow_up(101.5, 20, "+3V3", ORANGE, side=True)
wire([(99, 20), (101.5, 20)], ORANGE, 2.4)

# --------------------------------------------- RX sense: R3 + BAT54S clamp
dot(88, 44, BLUE)
resistor_h(88, 70, 44, "R3  10k", color=GREEN)
wire([(70, 44), (28, 44)], GREEN)
dot(40, 44, GREEN)
# BAT54S: pin2 (top diode cathode) -> 3V3 ; pin1 (bottom diode anode) -> GND
diode(40, 44, 39, up=True)                       # node -> 3V3 (clamps > 3.6V)
arrow_up(40, 39, "+3V3", ORANGE, side=True)
diode(40, 49, 44, up=True)                       # GND -> node (clamps < 0V)
gnd_sym(40, 49)
txt(38, 36.4, "D3  BAT54S", 9, "bold", ha="right")
txt(38, 38.3, "(clamp)", 8, color=GREY, ha="right")
txt(58, 46.6, "clamps GPIO26 to 0\u20143.6 V", 8.5,
    color=GREY, style="italic")

# ------------------------------------------------------------------ DS2502
ax.add_patch(FancyBboxPatch((92, 46), 21, 13,
                            boxstyle="round,pad=0,rounding_size=1",
                            fc="#eceff1", ec="#37474f", lw=2, zorder=1))
txt(102.5, 49, "DS2502 / DS1982", 11.5, "bold", ha="center")
txt(102.5, 51.3, "1 Kb Add-Only EPROM", 8.5, ha="center")
wire([(88, 52), (92, 52)], BLUE, 3.0)
txt(93, 53.4, "2  DATA", 9.5, "bold", "#0d47a1")
wire([(97, 59), (97, 61)], GNDC, 2.4)
txt(98.2, 60, "1  GND", 9.5, "bold", "#0d47a1")
gnd_sym(97, 61)
txt(102.5, 56.8, "3 = NC", 8.5, color=GREY, ha="center")
txt(102.5, 65.5, "TO-92 (flat face front):", 9, color=GREY, ha="center")
txt(102.5, 67.3, "1 = GND \u00b7 2 = DATA \u00b7 3 = NC", 9, color=GREY,
    ha="center")

# ------------------------------------------------------------ MT3608 note
ax.add_patch(FancyBboxPatch((95, 4), 26, 9,
                            boxstyle="round,pad=0,rounding_size=1",
                            fc="#fff3e0", ec="#e65100", lw=2, zorder=1))
txt(108, 6.3, "MT3608 BOOST", 10.5, "bold", ha="center")
txt(108, 8.4, "5 V (ESP32 VIN/USB) \u2192 11.75 V = VPP", 8.5, ha="center")
txt(108, 10.6, "set BEFORE connecting the DS2502!", 8.5, "bold", RED,
    ha="center")

# ------------------------------------------------------------------- notes
ax.add_patch(FancyBboxPatch((6, 71), 112, 21,
                            boxstyle="round,pad=0,rounding_size=1",
                            fc="#fffde7", ec="#f9a825", lw=2, zorder=1))
notes = [
    ("How it works", "bold", BLACK, 12),
    ("\u2022 READ:  GPIO25 (OW_TX) drives Q1 \u2014 HIGH pulls the bus LOW "
     "(inverting open-drain).  GPIO26 (OW_RX) senses via R3 10k; D3 BAT54S "
     "(pin3=node, pin2\u21923V3, pin1\u2192GND) clamps it to 0\u20133.6 V.",
     "normal", BLACK, 10.5),
    ("\u2022 WRITE:  GPIO27 (OW_VPP) HIGH \u2192 Q3 pulls the gate node low "
     "\u2192 Q2 switches VPP onto the bus for ~500 \u00b5s (tPROG \u2265 480 "
     "\u00b5s).  R4 keeps Q2 OFF otherwise; C1 tames the edges.",
     "normal", BLACK, 10.5),
    ("\u2022 Firmware:  OW_USE_DRIVER = 1, OW_TX=25 / OW_RX=26 / OW_VPP=27. "
     " Read-only quick build:  OW_USE_DRIVER = 0 \u2192 GPIO4 + 4.7k pull-up "
     "(writes refused).", "normal", BLACK, 10.5),
    ("\u26a0 VPP must be 11.5\u201312.0 V (12.0 V absolute max). Measure the "
     "MT3608 output UNDER LOAD before first connecting the DS2502.",
     "normal", RED, 10.5),
    ("\u26a0 No other (non-EPROM) 1-Wire devices on the bus \u2014 12 V "
     "destroys them.   \u26a0 DS2502 is EPROM: bits only go 1 \u2192 0, "
     "permanently.", "normal", RED, 10.5),
]
y = 73.6
for s, w, c, fs in notes:
    txt(8, y, s, fs, w, c)
    y += 3.3

fig.savefig("docs/wiring_diagram.png", facecolor="white")
print("wrote docs/wiring_diagram.png")
