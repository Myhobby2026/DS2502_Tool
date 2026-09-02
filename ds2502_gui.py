#!/usr/bin/env python3
"""
============================================================================
 DS2502 1-Wire EPROM Tool - GUI
============================================================================
 Talks to the ESP32/ESP8266 bridge (ds2502_bridge.ino) over a serial port.

 Features
 --------
   * Read 64-bit ROM ID (family 0x09)
   * Read the full 128-byte EPROM data memory (hex + ASCII dump)
   * WRITE data memory   (Write Memory [0Fh], 12V pulse done by the bridge)
   * Read the 8-byte status register, decoded:
        byte 0      : write-protect bits WP0..WP3 for pages 0..3
        bytes 1..4  : page redirection bytes (one's complement, FF = none)
        bytes 5..6  : reserved
        byte 7      : factory programmed 00h
   * WRITE status register (Write Status [55h]) - raw bytes or via helpers
     ("write-protect page n", "redirect page a -> b")
   * Save dump to .bin / load .bin and program it

 !! DS2502 is EPROM: bits can only go 1 -> 0 and can NEVER be erased !!
    Every write action pops a confirmation dialog for this reason.

 Requirements:  pip install pyserial
============================================================================
"""

import json
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # pragma: no cover
    raise SystemExit("pyserial is required:  pip install pyserial")

BAUD = 115200
DATA_SIZE = 0x80          # 128 bytes
STATUS_SIZE = 0x08        # 8 bytes
PAGE_SIZE = 0x20          # 32 bytes / page

EPROM_WARNING = (
    "The DS2502 is an ADD-ONLY EPROM.\n\n"
    "Bits can only be programmed from 1 to 0 and can NEVER be erased "
    "or changed back.\n\nThis operation is IRREVERSIBLE. Continue?"
)


# ---------------------------------------------------------------------------
# Serial link to the bridge
# ---------------------------------------------------------------------------
class Bridge:
    def __init__(self):
        self.ser = None
        self.lock = threading.Lock()

    @property
    def connected(self):
        return self.ser is not None and self.ser.is_open

    def open(self, port):
        self.close()
        self.ser = serial.Serial(port, BAUD, timeout=0.2)
        time.sleep(2.0)                       # ESP resets on port open
        self.ser.reset_input_buffer()

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def command(self, cmd, timeout=30.0, progress=None):
        """Send one command, return (ok, payload, progress_lines)."""
        with self.lock:
            if not self.connected:
                raise RuntimeError("Not connected")
            self.ser.reset_input_buffer()
            self.ser.write((cmd + "\n").encode("ascii"))
            self.ser.flush()

            deadline = time.time() + timeout
            lines = []
            buf = b""
            while time.time() < deadline:
                chunk = self.ser.read(256)
                if chunk:
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        line = raw.decode("ascii", "replace").strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.startswith("OK"):
                            return True, line[2:].strip(), lines
                        if line.startswith("ERR"):
                            return False, line[3:].strip(), lines
                        lines.append(line)
                        if progress:
                            progress(line)
            raise TimeoutError(f"No answer to '{cmd}' within {timeout:.0f}s")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_hex_bytes(text):
    """'A0 FF,01' / 'A0FF01' -> bytes. Raises ValueError."""
    clean = text.replace(",", " ").replace(":", " ").split()
    joined = "".join(clean) if clean else ""
    if not joined:
        raise ValueError("no data")
    if len(joined) % 2:
        raise ValueError("odd number of hex digits")
    return bytes.fromhex(joined)


def hexdump(data, base=0):
    out = []
    for off in range(0, len(data), 16):
        row = data[off:off + 16]
        hx = " ".join(f"{b:02X}" for b in row)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        out.append(f"{base + off:04X}:  {hx:<47}  |{asc}|")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DS2502 EPROM Tool  -  read / write data + status")
        self.geometry("980x760")
        self.bridge = Bridge()
        self.busy = False
        self.last_dump = None

        self._build_ui()
        self.refresh_ports()

    # ------------------------------------------------------------- UI build
    def _build_ui(self):
        # --- connection bar -------------------------------------------------
        bar = ttk.LabelFrame(self, text="Connection")
        bar.pack(fill="x", padx=8, pady=(8, 4))

        self.port_var = tk.StringVar()
        self.port_cb = ttk.Combobox(bar, textvariable=self.port_var, width=28,
                                    state="readonly")
        self.port_cb.pack(side="left", padx=6, pady=6)
        ttk.Button(bar, text="Refresh", command=self.refresh_ports)\
            .pack(side="left", padx=2)
        self.btn_conn = ttk.Button(bar, text="Connect", command=self.toggle_conn)
        self.btn_conn.pack(side="left", padx=6)
        self.lbl_status = ttk.Label(bar, text="Disconnected", foreground="red")
        self.lbl_status.pack(side="left", padx=10)

        ttk.Button(bar, text="Read ROM ID", command=self.do_read_rom)\
            .pack(side="left", padx=12)
        ttk.Button(bar, text="Diagnose bus", command=self.do_diag)\
            .pack(side="left", padx=2)
        self.lbl_rom = ttk.Label(bar, text="ROM: --", font=("Consolas", 10))
        self.lbl_rom.pack(side="left", padx=4)

        # --- notebook --------------------------------------------------------
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=4)
        self.tab_data = ttk.Frame(nb)
        self.tab_stat = ttk.Frame(nb)
        self.tab_clone = ttk.Frame(nb)
        nb.add(self.tab_data, text="  Data memory (128 B EPROM)  ")
        nb.add(self.tab_stat, text="  Status register (8 B)  ")
        nb.add(self.tab_clone, text="  Cloning  ")
        self._build_data_tab()
        self._build_status_tab()
        self._build_clone_tab()

        # --- log --------------------------------------------------------------
        logf = ttk.LabelFrame(self, text="Log")
        logf.pack(fill="both", padx=8, pady=(4, 8))
        self.log_txt = scrolledtext.ScrolledText(logf, height=8,
                                                 font=("Consolas", 9),
                                                 state="disabled")
        self.log_txt.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_data_tab(self):
        t = self.tab_data

        rd = ttk.Frame(t)
        rd.pack(fill="x", padx=6, pady=6)
        ttk.Button(rd, text="Read all 128 bytes", command=self.do_read_data)\
            .pack(side="left")
        ttk.Button(rd, text="Save dump as .bin", command=self.do_save_dump)\
            .pack(side="left", padx=8)

        self.dump_txt = scrolledtext.ScrolledText(t, height=12,
                                                  font=("Consolas", 10))
        self.dump_txt.pack(fill="both", expand=True, padx=6, pady=4)
        self.dump_txt.insert("end", "(no data read yet)")
        self.dump_txt.configure(state="disabled")

        wf = ttk.LabelFrame(t, text="Write data memory  (Write Memory [0Fh], "
                                    "12V program pulse per byte)")
        wf.pack(fill="x", padx=6, pady=6)

        row = ttk.Frame(wf)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Label(row, text="Start address (hex, 00-7F):").pack(side="left")
        self.w_addr = ttk.Entry(row, width=6, font=("Consolas", 10))
        self.w_addr.insert(0, "00")
        self.w_addr.pack(side="left", padx=6)
        ttk.Label(row, text="Data bytes (hex):").pack(side="left", padx=(12, 0))
        self.w_data = ttk.Entry(row, width=60, font=("Consolas", 10))
        self.w_data.pack(side="left", padx=6, fill="x", expand=True)

        row2 = ttk.Frame(wf)
        row2.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(row2, text="WRITE bytes", command=self.do_write_data)\
            .pack(side="left")
        ttk.Button(row2, text="Write .bin file ...",
                   command=self.do_write_file).pack(side="left", padx=8)
        ttk.Label(row2, foreground="#a00000",
                  text="EPROM: bits only go 1 \u2192 0, writes are permanent!")\
            .pack(side="left", padx=12)

    def _build_status_tab(self):
        t = self.tab_stat

        top = ttk.Frame(t)
        top.pack(fill="x", padx=6, pady=6)
        ttk.Button(top, text="Read status register", command=self.do_read_status)\
            .pack(side="left")
        self.lbl_stat_raw = ttk.Label(top, text="raw: -- -- -- -- -- -- -- --",
                                      font=("Consolas", 10))
        self.lbl_stat_raw.pack(side="left", padx=12)

        # decoded view
        dec = ttk.LabelFrame(t, text="Decoded (per DS2502 datasheet)")
        dec.pack(fill="x", padx=6, pady=4)
        self.stat_tree = ttk.Treeview(
            dec, columns=("addr", "val", "meaning"), show="headings", height=8)
        for col, txt, w in (("addr", "Addr", 60), ("val", "Value", 70),
                            ("meaning", "Meaning", 640)):
            self.stat_tree.heading(col, text=txt)
            self.stat_tree.column(col, width=w, anchor="w")
        self.stat_tree.pack(fill="x", padx=6, pady=6)

        # helpers: write protect
        wp = ttk.LabelFrame(t, text="Write-protect a data page "
                                    "(programs a 0 bit in status byte 0 - permanent!)")
        wp.pack(fill="x", padx=6, pady=4)
        row = ttk.Frame(wp)
        row.pack(fill="x", padx=6, pady=6)
        for p in range(4):
            ttk.Button(row, text=f"Protect page {p}",
                       command=lambda p=p: self.do_protect_page(p))\
                .pack(side="left", padx=4)

        # helpers: redirection
        rd = ttk.LabelFrame(t, text="Redirect a page (writes one's complement of "
                                    "new page into redirection byte 1-4)")
        rd.pack(fill="x", padx=6, pady=4)
        row = ttk.Frame(rd)
        row.pack(fill="x", padx=6, pady=6)
        ttk.Label(row, text="Redirect page").pack(side="left")
        self.rd_from = ttk.Combobox(row, values=[0, 1, 2, 3], width=3,
                                    state="readonly")
        self.rd_from.set(0)
        self.rd_from.pack(side="left", padx=4)
        ttk.Label(row, text="to page").pack(side="left")
        self.rd_to = ttk.Combobox(row, values=[0, 1, 2, 3], width=3,
                                  state="readonly")
        self.rd_to.set(1)
        self.rd_to.pack(side="left", padx=4)
        ttk.Button(row, text="Write redirection",
                   command=self.do_redirect).pack(side="left", padx=10)

        # raw write
        raw = ttk.LabelFrame(t, text="Raw status write  (Write Status [55h])")
        raw.pack(fill="x", padx=6, pady=4)
        row = ttk.Frame(raw)
        row.pack(fill="x", padx=6, pady=6)
        ttk.Label(row, text="Address (hex, 00-07):").pack(side="left")
        self.ws_addr = ttk.Entry(row, width=6, font=("Consolas", 10))
        self.ws_addr.insert(0, "00")
        self.ws_addr.pack(side="left", padx=6)
        ttk.Label(row, text="Data bytes (hex):").pack(side="left", padx=(12, 0))
        self.ws_data = ttk.Entry(row, width=30, font=("Consolas", 10))
        self.ws_data.pack(side="left", padx=6)
        ttk.Button(row, text="WRITE status", command=self.do_write_status)\
            .pack(side="left", padx=10)

    def _build_clone_tab(self):
        t = self.tab_clone

        g = ttk.LabelFrame(t, text="Guided clone: original chip \u2192 new chip "
                                   "(read \u2192 swap \u2192 write \u2192 verify)")
        g.pack(fill="x", padx=6, pady=8)
        row = ttk.Frame(g)
        row.pack(fill="x", padx=6, pady=6)
        ttk.Button(row, text="Clone chip (guided) \u2026",
                   command=self.do_clone).pack(side="left")
        ttk.Label(row, text="reads ROM + data + status, prompts for chip "
                            "swap, writes and verifies everything")\
            .pack(side="left", padx=10)

        f = ttk.LabelFrame(t, text="Clone dump file  (.ds2502 = data + status "
                                   "+ ROM in one file)")
        f.pack(fill="x", padx=6, pady=8)
        row = ttk.Frame(f)
        row.pack(fill="x", padx=6, pady=6)
        ttk.Button(row, text="Read chip \u2192 Save clone dump \u2026",
                   command=self.do_clone_save).pack(side="left")
        ttk.Button(row, text="Load clone dump \u2192 Write to chip \u2026",
                   command=self.do_clone_load).pack(side="left", padx=8)
        row2 = ttk.Frame(f)
        row2.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(row2, text="Save: archive the original once, clone as many "
                             "chips as you like later \u2014 without the "
                             "original present.").pack(side="left")

        s = ttk.LabelFrame(t, text="Current clone source in memory")
        s.pack(fill="x", padx=6, pady=8)
        self.lbl_clone_src = ttk.Label(
            s, text="(none - read a chip or load a dump)",
            font=("Consolas", 10))
        self.lbl_clone_src.pack(anchor="w", padx=8, pady=6)

        ttk.Label(t, foreground="#a00000",
                  text="\u26a0  The 64-bit ROM ID is factory-lasered and can "
                       "never be cloned \u2014 only data + status are copied.")\
            .pack(anchor="w", padx=10, pady=4)

    def _set_clone_src(self, src, origin):
        """Remember the clone source and show it in every view."""
        self.clone_src = src
        used = sum(1 for b in src["data"] if b != 0xFF)
        stat_hex = " ".join(f"{b:02X}" for b in src["stat"])
        self.lbl_clone_src.config(
            text=f"{origin}\nROM    : {src.get('rom') or 'unknown'}\n"
                 f"Data   : 128 bytes ({used} bytes != FF)\n"
                 f"Status : {stat_hex}")
        # update the Data memory and Status register tabs too
        self._show_dump(src["data"])
        self._show_status(src["stat"])

    # --------------------------------------------------------------- logging
    def log(self, msg):
        def _do():
            self.log_txt.configure(state="normal")
            self.log_txt.insert("end", time.strftime("[%H:%M:%S] ") + msg + "\n")
            self.log_txt.see("end")
            self.log_txt.configure(state="disabled")
        self.after(0, _do)

    # ------------------------------------------------------------ connection
    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_cb["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def toggle_conn(self):
        if self.bridge.connected:
            self.bridge.close()
            self.lbl_status.config(text="Disconnected", foreground="red")
            self.btn_conn.config(text="Connect")
            self.log("Disconnected")
            return
        port = self.port_var.get()
        if not port:
            messagebox.showwarning("No port", "Select a serial port first.")
            return
        self._run(lambda: self._connect(port))

    def _connect(self, port):
        self.log(f"Opening {port} @ {BAUD} ...")
        self.bridge.open(port)
        ok, payload, _ = self.bridge.command("PING", timeout=5)
        if not ok:
            raise RuntimeError(f"Bridge error: {payload}")
        self.after(0, lambda: (
            self.lbl_status.config(text=f"Connected ({port})",
                                   foreground="green"),
            self.btn_conn.config(text="Disconnect")))
        self.log(f"Bridge says: {payload}")

    # ------------------------------------------------------- worker plumbing
    def _run(self, fn):
        if self.busy:
            messagebox.showinfo("Busy", "An operation is already running.")
            return
        if fn.__name__ != "<lambda>" and not self.bridge.connected:
            messagebox.showwarning("Not connected", "Connect to the bridge first.")
            return

        def worker():
            self.busy = True
            try:
                fn()
            except Exception as exc:
                msg = str(exc) or exc.__class__.__name__
                self.log(f"ERROR: {msg}")
                self.after(0, lambda m=msg: messagebox.showerror("Error", m))
            finally:
                self.busy = False
        threading.Thread(target=worker, daemon=True).start()

    def _need_conn(self):
        if not self.bridge.connected:
            messagebox.showwarning("Not connected", "Connect to the bridge first.")
            return False
        return True

    # ------------------------------------------------------------- cloning
    def _read_chip_full(self):
        """Read ROM + full data + status of the connected chip."""
        ok, p, _ = self.bridge.command("ROM")
        if not ok:
            raise RuntimeError(f"read ROM: {p}")
        rom = p.split()[1]
        ok, p, _ = self.bridge.command("RDATA 00 80")
        if not ok:
            raise RuntimeError(f"read data: {p}")
        data = bytes.fromhex(p.split()[1])
        ok, p, _ = self.bridge.command("RSTAT")
        if not ok:
            raise RuntimeError(f"read status: {p}")
        stat = bytes.fromhex(p.split()[1])
        return rom, data, stat

    def do_clone(self):
        """Guided 1:1 clone: original chip -> blank chip (data + status)."""
        if not self._need_conn():
            return

        def job():
            self.log("=== CLONE step 1/3: reading ORIGINAL chip ===")
            rom, data, stat = self._read_chip_full()
            src = {"rom": rom, "data": data, "stat": stat}
            self.log(f"Original ROM   : {rom}")
            self.log(f"Original data  : 128 bytes read "
                     f"({sum(1 for b in data if b != 0xFF)} bytes != FF)")
            self.log("Original status: " + " ".join(f"{b:02X}" for b in stat))
            self.after(0, lambda: (
                self._set_clone_src(src, "source: chip read (guided clone)"),
                self._clone_swap_prompt()))
        self._run(job)

    def do_clone_save(self):
        """Read the connected chip and archive it as a .ds2502 clone dump."""
        if not self._need_conn():
            return

        def job():
            self.log("Reading chip for clone dump ...")
            rom, data, stat = self._read_chip_full()
            src = {"rom": rom, "data": data, "stat": stat}
            self.log(f"Read OK - ROM {rom}, 128 B data, status "
                     + " ".join(f"{b:02X}" for b in stat))
            self.after(0, lambda: (
                self._set_clone_src(src, "source: chip read (saved to dump)"),
                self._clone_save_dialog()))
        self._run(job)

    def _clone_save_dialog(self):
        src = self.clone_src
        path = filedialog.asksaveasfilename(
            defaultextension=".ds2502",
            filetypes=[("DS2502 clone dump", "*.ds2502"),
                       ("JSON", "*.json"), ("All files", "*.*")],
            initialfile=f"ds2502_{src['rom'][:16]}.ds2502")
        if not path:
            self.log("Clone dump save cancelled.")
            return
        with open(path, "w") as f:
            json.dump({
                "tool": "DS2502_Tool clone dump",
                "version": 1,
                "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
                "rom": src["rom"],
                "data": src["data"].hex().upper(),
                "status": src["stat"].hex().upper(),
            }, f, indent=2)
        self.log(f"Clone dump saved to {path}")
        messagebox.showinfo("Clone dump saved",
                            f"Saved:\n{path}\n\nROM {src['rom']}\n"
                            "128 B data + 8 B status archived.\nYou can now "
                            "clone chips from this file any time - without "
                            "the original.")

    def do_clone_load(self):
        """Load a .ds2502 clone dump (or raw 128-byte .bin) and write it."""
        if not self._need_conn():
            return
        path = filedialog.askopenfilename(
            filetypes=[("DS2502 clone dump", "*.ds2502"),
                       ("JSON", "*.json"), ("Raw 128-byte bin", "*.bin"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            raw = open(path, "rb").read()
            try:                                   # .ds2502 / .json
                j = json.loads(raw.decode("utf-8"))
                data = bytes.fromhex(j["data"])
                stat = bytes.fromhex(j.get("status", "FF" * 7 + "00"))
                rom = j.get("rom", "")
            except (UnicodeDecodeError, json.JSONDecodeError):
                if len(raw) == 128:                # plain data-only .bin
                    data, stat, rom = raw, bytes([0xFF] * 7 + [0x00]), ""
                else:
                    raise ValueError(f"not a clone dump and not a 128-byte "
                                     f"bin (size {len(raw)})")
            if len(data) != 128 or len(stat) != 8:
                raise ValueError(f"bad lengths: data {len(data)} B "
                                 f"(need 128), status {len(stat)} B (need 8)")
        except Exception as e:
            messagebox.showerror("Bad clone dump", f"{path}\n\n{e}")
            return

        src = {"rom": rom, "data": data, "stat": stat}
        self._set_clone_src(src, f"source: file  {path}")
        self.log(f"Clone dump loaded: {path}")
        used = sum(1 for b in data if b != 0xFF)
        stat_hex = " ".join(f"{b:02X}" for b in stat)
        if not messagebox.askokcancel(
                "Write clone dump to chip",
                f"Loaded clone dump:\n\n  ROM of source: {rom or 'unknown'}\n"
                f"  Data  : 128 bytes ({used} != FF)\n"
                f"  Status: {stat_hex}\n\n"
                "Make sure the NEW (blank) chip is connected, then click OK "
                "to burn it.\n\nWriting is PERMANENT (EPROM, bits only go "
                "1\u21920).", icon="warning"):
            self.log("Clone dump write cancelled.")
            return
        self._run(lambda: self._clone_write_from(src))

    def _clone_swap_prompt(self):
        src = self.clone_src
        stat_hex = " ".join(f"{b:02X}" for b in src["stat"])
        msg = (
            "ORIGINAL chip read successfully:\n\n"
            f"  ROM ID : {src['rom']}\n"
            f"  Data   : 128 bytes\n"
            f"  Status : {stat_hex}\n\n"
            "Now:\n"
            "  1. REMOVE the original chip from the socket\n"
            "  2. INSERT the new (blank) chip\n"
            "  3. Click OK to burn the clone\n\n"
            "Writing is PERMANENT (EPROM, bits only go 1\u21920).\n"
            "Note: the 64-bit ROM ID is factory-lasered and CANNOT be "
            "cloned - only data + status are copied."
        )
        if messagebox.askokcancel("Clone - swap chips now", msg,
                                  icon="warning"):
            self._run(lambda: self._clone_write_from(src))
        else:
            self.log("Clone cancelled by user.")

    def _clone_write_from(self, src):
        s_data, s_stat = src["data"], src["stat"]

        self.log("=== CLONE step 2/3: checking TARGET chip ===")
        ok, p, _ = self.bridge.command("ROM")
        if not ok:
            raise RuntimeError(f"target ROM: {p} - is the new chip inserted?")
        rom_t = p.split()[1]
        if src.get("rom") and rom_t == src["rom"]:
            raise RuntimeError("Same ROM ID as the clone source - the "
                               "ORIGINAL chip is still connected! Swap the "
                               "chips.")
        self.log(f"Target ROM     : {rom_t}")

        ok, p, _ = self.bridge.command("RDATA 00 80")
        if not ok:
            raise RuntimeError(f"target data read: {p}")
        t_data = bytes.fromhex(p.split()[1])
        ok, p, _ = self.bridge.command("RSTAT")
        if not ok:
            raise RuntimeError(f"target status read: {p}")
        t_stat = bytes.fromhex(p.split()[1])

        # EPROM compatibility: target must not have 0-bits where source has 1
        bad = [i for i in range(128)
               if (t_data[i] & s_data[i]) != s_data[i]]
        bad_s = [a for a in range(7)
                 if (t_stat[a] & s_stat[a]) != s_stat[a]]
        if bad or bad_s:
            where = ", ".join(f"data 0x{i:02X}" for i in bad[:8])
            if bad_s:
                where += (", " if where else "") + \
                         ", ".join(f"status 0x{a:02X}" for a in bad_s)
            raise RuntimeError(
                f"Target is NOT blank enough - it already has 0-bits where "
                f"the original has 1-bits at: {where}"
                f"{' ...' if len(bad) > 8 else ''}. A perfect clone is "
                f"impossible on this chip; use a fresh one.")
        if all(b == 0xFF for b in t_data):
            self.log("Target data    : blank (all FF) - good")
        else:
            self.log("Target data    : partially programmed but compatible")

        self.log("=== CLONE step 3/3: writing DATA, then STATUS ===")

        # ---- data: write only contiguous runs that differ, 32 B chunks ----
        runs, i = [], 0
        while i < 128:
            if t_data[i] != s_data[i]:
                j = i
                while j < 128 and t_data[j] != s_data[j]:
                    j += 1
                runs.append((i, j))
                i = j
            else:
                i += 1
        total = sum(b - a for a, b in runs)
        self.log(f"Data bytes to program: {total} in {len(runs)} block(s)")
        for a, b in runs:
            for c in range(a, b, 32):
                d = min(c + 32, b)
                chunk = s_data[c:d]
                ok, p, _ = self.bridge.command(
                    f"WDATA {c:02X} {chunk.hex().upper()}",
                    timeout=10 + len(chunk),
                    progress=lambda ln: self.log("  " + ln))
                if not ok:
                    raise RuntimeError(f"data write failed: {p}")

        # ---- verify data ----
        ok, p, _ = self.bridge.command("RDATA 00 80")
        if not ok:
            raise RuntimeError(f"data verify read: {p}")
        v_data = bytes.fromhex(p.split()[1])
        diff = [i for i in range(128) if v_data[i] != s_data[i]]
        if diff:
            raise RuntimeError(
                "DATA VERIFY FAILED at " +
                ", ".join(f"0x{i:02X}" for i in diff[:8]) +
                (" ..." if len(diff) > 8 else ""))
        self.log("Data verify    : 128/128 bytes identical - OK")

        # ---- status: redirection/reserved bytes 1..6 first, WP byte 0 LAST
        #      (locking pages first would make the data unwritable!) ----
        wrote = 0
        for a in (1, 2, 3, 4, 5, 6, 0):
            if s_stat[a] != 0xFF and t_stat[a] != s_stat[a]:
                ok, p, _ = self.bridge.command(
                    f"WSTAT {a:02X} {s_stat[a]:02X}", timeout=15,
                    progress=lambda ln: self.log("  " + ln))
                if not ok:
                    raise RuntimeError(f"status write @{a:02X}: {p}")
                wrote += 1
        self.log(f"Status bytes programmed: {wrote}")

        # ---- verify status ----
        ok, p, _ = self.bridge.command("RSTAT")
        if not ok:
            raise RuntimeError(f"status verify read: {p}")
        v_stat = bytes.fromhex(p.split()[1])
        sdiff = [a for a in range(7) if v_stat[a] != s_stat[a]]
        if sdiff:
            raise RuntimeError("STATUS VERIFY FAILED at byte(s) " +
                               ", ".join(f"0x{a:02X}" for a in sdiff))
        note7 = ""
        if v_stat[7] != s_stat[7]:
            note7 = (f"\n\nNote: factory byte 07h differs "
                     f"(original {s_stat[7]:02X}, clone {v_stat[7]:02X}) - "
                     f"it is factory-programmed and cannot be changed.")
        self.log("Status verify  : bytes 00-06 identical - OK")
        self.log("=== CLONE COMPLETE ===")

        self.after(0, lambda: (
            self._show_dump(v_data),
            self._show_status(v_stat),
            messagebox.showinfo(
                "Clone complete",
                "Perfect clone written and verified!\n\n"
                f"  Source ROM : {src.get('rom') or '(from file)'}\n"
                f"  Clone ROM  : {rom_t}\n"
                f"  Data       : 128/128 bytes identical\n"
                f"  Status     : bytes 00h-06h identical\n\n"
                "Remember: the ROM ID itself is unique per chip and can "
                "never be copied." + note7)))

    # ------------------------------------------------------------- ROM / read
    def do_diag(self):
        if not self._need_conn():
            return

        def job():
            self.log("Running 1-Wire bus diagnostics ...")
            ok, payload, lines = self.bridge.command("DIAG", timeout=10)
            for ln in lines:
                self.log("  " + ln)
            if not ok:
                raise RuntimeError(payload)
            self.log(f"DIAG result: {payload}")
            report = "\n".join(lines) or payload
            self.after(0, lambda r=report: messagebox.showinfo(
                "1-Wire bus diagnostics", r))
            # also list every device found on the bus
            ok, payload, lines = self.bridge.command("SEARCH", timeout=10)
            for ln in lines:
                self.log("  " + ln)
            if ok:
                self.log(f"Devices on bus: {payload.split()[-1]}")
        self._run(job)

    def do_read_rom(self):
        if not self._need_conn():
            return

        def job():
            ok, payload, _ = self.bridge.command("ROM")
            if not ok:
                raise RuntimeError(payload)
            rom = payload.split()[1]
            fam = rom[0:2]
            note = "" if fam.upper() == "09" else "  (family != 09 !)"
            self.after(0, lambda: self.lbl_rom.config(text=f"ROM: {rom}{note}"))
            self.log(f"ROM ID: {rom}{note}")
        self._run(job)

    def _show_dump(self, data):
        """Update the Data memory tab's hex view + last_dump."""
        self.last_dump = bytes(data)
        self.dump_txt.configure(state="normal")
        self.dump_txt.delete("1.0", "end")
        self.dump_txt.insert("end", hexdump(data))
        self.dump_txt.configure(state="disabled")

    def do_read_data(self):
        if not self._need_conn():
            return

        def job():
            self.log("Reading 128-byte data memory (Read Memory [F0h]) ...")
            ok, payload, _ = self.bridge.command("RDATA 00 80")
            if not ok:
                raise RuntimeError(payload)
            data = bytes.fromhex(payload.split()[1])
            self.after(0, lambda: self._show_dump(data))
            self.log(f"Read {len(data)} bytes OK (CRC verified by bridge).")
        self._run(job)

    def do_save_dump(self):
        if self.last_dump is None:
            messagebox.showinfo("No data", "Read the data memory first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".bin",
                                            filetypes=[("Binary", "*.bin")])
        if path:
            with open(path, "wb") as f:
                f.write(self.last_dump)
            self.log(f"Dump saved to {path}")

    # ------------------------------------------------------------ data write
    def _do_write(self, cmd, addr, data, field, limit):
        """Common write path with confirmation + progress."""
        end = addr + len(data)
        if end > limit:
            raise RuntimeError(f"Range 0x{addr:02X}..0x{end - 1:02X} exceeds "
                               f"{field} size (0x{limit:02X})")
        preview = " ".join(f"{b:02X}" for b in data[:16])
        if len(data) > 16:
            preview += " ..."
        msg = (f"Write {len(data)} byte(s) to {field} at address "
               f"0x{addr:02X}:\n\n{preview}\n\n{EPROM_WARNING}")
        if not messagebox.askokcancel("Confirm permanent write", msg,
                                      icon="warning"):
            self.log("Write cancelled by user.")
            return

        def job():
            self.log(f"Writing {len(data)} byte(s) to {field} @ 0x{addr:02X} ...")
            ok, payload, _ = self.bridge.command(
                f"{cmd} {addr:02X} {data.hex().upper()}",
                timeout=10 + len(data) * 0.5,
                progress=lambda ln: self.log("  " + ln))
            if not ok:
                raise RuntimeError(payload)
            self.log(f"Write finished: {payload}")
            # auto re-read to show the result
            if cmd == "WDATA":
                self.do_read_data()
            else:
                self.do_read_status()
        self._run(job)

    def do_write_data(self):
        if not self._need_conn():
            return
        try:
            addr = int(self.w_addr.get().strip() or "0", 16)
            data = parse_hex_bytes(self.w_data.get())
        except ValueError as e:
            messagebox.showerror("Bad input", f"Invalid hex input: {e}")
            return
        self._do_write("WDATA", addr, data, "data memory", DATA_SIZE)

    def do_write_file(self):
        if not self._need_conn():
            return
        path = filedialog.askopenfilename(filetypes=[("Binary", "*.bin"),
                                                     ("All files", "*.*")])
        if not path:
            return
        with open(path, "rb") as f:
            data = f.read()
        if not data or len(data) > DATA_SIZE:
            messagebox.showerror("Bad file",
                                 f"File must be 1..{DATA_SIZE} bytes "
                                 f"(got {len(data)}).")
            return
        self._do_write("WDATA", 0, data, "data memory", DATA_SIZE)

    # ---------------------------------------------------------------- status
    def do_read_status(self):
        if not self._need_conn():
            return

        def job():
            self.log("Reading status register (Read Status [AAh]) ...")
            ok, payload, _ = self.bridge.command("RSTAT")
            if not ok:
                raise RuntimeError(payload)
            st = bytes.fromhex(payload.split()[1])
            self.after(0, lambda: self._show_status(st))
            self.log("Status: " + " ".join(f"{b:02X}" for b in st))
        self._run(job)

    def _show_status(self, st):
        self.lbl_stat_raw.config(
            text="raw: " + " ".join(f"{b:02X}" for b in st))
        for i in self.stat_tree.get_children():
            self.stat_tree.delete(i)

        # byte 0 : write protection
        wp = st[0]
        prot = [p for p in range(4) if not (wp >> p) & 1]
        mean = ("write-protect bits WP0..WP3 - protected pages: "
                + (", ".join(str(p) for p in prot) if prot else "none"))
        self.stat_tree.insert("", "end", values=("00", f"{wp:02X}", mean))

        # bytes 1..4 : redirection
        for p in range(4):
            v = st[1 + p]
            if v == 0xFF:
                mean = f"page {p} redirection: none (FFh)"
            else:
                tgt = (~v) & 0xFF
                valid = " (INVALID target!)" if tgt > 3 else ""
                mean = (f"page {p} redirected to page {tgt}"
                        f" (one's complement of {v:02X}){valid}")
            self.stat_tree.insert("", "end",
                                  values=(f"{1 + p:02X}", f"{v:02X}", mean))

        # bytes 5..6 : reserved
        for a in (5, 6):
            self.stat_tree.insert("", "end",
                                  values=(f"{a:02X}", f"{st[a]:02X}",
                                          "reserved (factory FFh)"))
        # byte 7
        note = "factory programmed 00h" + \
               ("" if st[7] == 0x00 else "  (expected 00h!)")
        self.stat_tree.insert("", "end", values=("07", f"{st[7]:02X}", note))

    def do_write_status(self):
        if not self._need_conn():
            return
        try:
            addr = int(self.ws_addr.get().strip() or "0", 16)
            data = parse_hex_bytes(self.ws_data.get())
        except ValueError as e:
            messagebox.showerror("Bad input", f"Invalid hex input: {e}")
            return
        if addr + len(data) > 7 and not messagebox.askokcancel(
                "Byte 7 is factory programmed",
                "Status byte 07h is factory programmed to 00h and normally "
                "not written. Continue anyway?", icon="warning"):
            return
        self._do_write("WSTAT", addr, data, "status register", STATUS_SIZE)

    def do_protect_page(self, page):
        if not self._need_conn():
            return
        # program only the selected WP bit to 0, all other bits stay 1
        val = (~(1 << page)) & 0xFF
        if not messagebox.askokcancel(
                "Confirm write protection",
                f"Permanently WRITE-PROTECT data page {page}?\n\n"
                f"Status byte 00h will be programmed with {val:02X}h "
                f"(bit {page} -> 0).\n\n{EPROM_WARNING}", icon="warning"):
            return
        self._do_write("WSTAT", 0x00, bytes([val]), "status register",
                       STATUS_SIZE)

    def do_redirect(self):
        if not self._need_conn():
            return
        src = int(self.rd_from.get())
        dst = int(self.rd_to.get())
        if src == dst:
            messagebox.showwarning("Invalid", "Source and target page are equal.")
            return
        val = (~dst) & 0xFF          # one's complement of new page address
        if not messagebox.askokcancel(
                "Confirm redirection",
                f"Redirect page {src} to page {dst}?\n\n"
                f"Redirection byte 0{src + 1:X}h will be programmed with "
                f"{val:02X}h (one's complement of {dst:02X}h).\n\n"
                f"{EPROM_WARNING}", icon="warning"):
            return
        self._do_write("WSTAT", 0x01 + src, bytes([val]), "status register",
                       STATUS_SIZE)


if __name__ == "__main__":
    App().mainloop()
