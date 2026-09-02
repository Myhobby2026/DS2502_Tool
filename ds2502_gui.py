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
        self.lbl_rom = ttk.Label(bar, text="ROM: --", font=("Consolas", 10))
        self.lbl_rom.pack(side="left", padx=4)

        # --- notebook --------------------------------------------------------
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=4)
        self.tab_data = ttk.Frame(nb)
        self.tab_stat = ttk.Frame(nb)
        nb.add(self.tab_data, text="  Data memory (128 B EPROM)  ")
        nb.add(self.tab_stat, text="  Status register (8 B)  ")
        self._build_data_tab()
        self._build_status_tab()

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
                self.log(f"ERROR: {exc}")
                self.after(0, lambda: messagebox.showerror("Error", str(exc)))
            finally:
                self.busy = False
        threading.Thread(target=worker, daemon=True).start()

    def _need_conn(self):
        if not self.bridge.connected:
            messagebox.showwarning("Not connected", "Connect to the bridge first.")
            return False
        return True

    # ------------------------------------------------------------- ROM / read
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

    def do_read_data(self):
        if not self._need_conn():
            return

        def job():
            self.log("Reading 128-byte data memory (Read Memory [F0h]) ...")
            ok, payload, _ = self.bridge.command("RDATA 00 80")
            if not ok:
                raise RuntimeError(payload)
            data = bytes.fromhex(payload.split()[1])
            self.last_dump = data

            def show():
                self.dump_txt.configure(state="normal")
                self.dump_txt.delete("1.0", "end")
                self.dump_txt.insert("end", hexdump(data))
                self.dump_txt.configure(state="disabled")
            self.after(0, show)
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
