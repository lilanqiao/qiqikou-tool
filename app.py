"""去气口工具 —— 跨平台界面（Windows / macOS），布局沿用旧版 PowerShell 界面。"""
import os
import sys
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import qiqikou_core as core

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except Exception:          # 拖放库加载失败时退回普通窗口，只是不能拖文件
    HAS_DND = False

IS_MAC = sys.platform == "darwin"
FONT = "PingFang SC" if IS_MAC else "Microsoft YaHei UI"
MONO = "Menlo" if IS_MAC else "Consolas"
FS = 13 if IS_MAC else 10          # macOS 的字号单位偏小，整体放大一点

DARK = "#1e1e32"
GOLD = "#ffd700"
BG = "#f5f5f8"
RED = "#b43c3c"
DISABLED = "#505064"
# 打包后资源在 sys._MEIPASS（PyInstaller 的 _internal 目录），开发时在本文件旁边
HERE = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))


class FlatButton(tk.Label):
    """用 Label 做的扁平按钮：macOS 的原生按钮不认背景色，这样两边看起来一致。"""

    def __init__(self, master, text, command, bg=DARK, fg="white", font=None, **kw):
        kw.setdefault("padx", 12); kw.setdefault("pady", 4)
        super().__init__(master, text=text, bg=bg, fg=fg, cursor="hand2",
                         font=font or (FONT, FS), **kw)
        self._bg, self._cmd, self._enabled = bg, command, True
        self.bind("<Button-1>", lambda e: self._enabled and self._cmd())
        self.bind("<Enter>", lambda e: self._enabled and self.config(bg=self._hover()))
        self.bind("<Leave>", lambda e: self._enabled and self.config(bg=self._bg))

    def _hover(self):
        r, g, b = (int(self._bg[i:i + 2], 16) for i in (1, 3, 5))
        return "#%02x%02x%02x" % (min(255, r + 30), min(255, g + 30), min(255, b + 30))

    def set_enabled(self, on):
        self._enabled = on
        self.config(bg=self._bg if on else DISABLED, cursor="hand2" if on else "arrow")


def _drop_path(data):
    """拖放进来的路径：带空格的会被 {} 包起来，多个文件只取第一个。"""
    data = data.strip()
    if data.startswith("{"):
        return data[1:data.index("}")]
    return data.split()[0] if data else ""


def open_folder(path):
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif IS_MAC:
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.worker = None
        self.last_output = None

        root.title("去气口工具")
        k = 1.0 if IS_MAC else max(1.0, root.winfo_fpixels("1i") / 96)   # Windows 缩放比例（125%/150%…）
        root.geometry(f"{int(680 * k)}x{int(640 * k)}")
        root.minsize(int(600 * k), int(560 * k))
        root.configure(bg=BG)
        try:
            if sys.platform == "win32":
                root.iconbitmap(os.path.join(HERE, "assets", "icon.ico"))
            else:
                root.iconphoto(True, tk.PhotoImage(file=os.path.join(HERE, "assets", "icon.png")))
        except Exception:
            pass

        title = tk.Frame(root, bg=DARK, height=56)
        title.pack(fill="x")
        tk.Label(title, text="去气口工具  —  口播配音自动剪停顿", bg=DARK, fg=GOLD,
                 font=(FONT, FS + 3, "bold")).pack(side="left", padx=20, pady=12)

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=(12, 16))

        # ── 音频文件 ──
        hint = "（WAV / MP3 / FLAC，可拖入）" if HAS_DND else "（WAV / MP3 / FLAC）"
        g1 = self._group(body, f"  音频文件{hint}  ")
        self.path = tk.StringVar()
        self.ent_path = self._entry(g1, self.path)
        FlatButton(g1, "选择文件", self.browse_file).pack(side="left", padx=(8, 0))
        FlatButton(g1, "清空", self.clear, bg=RED).pack(side="left", padx=(8, 0))

        # ── 保存位置 ──
        g2 = self._group(body, "  保存位置（留空 = 保存到原文件同目录）  ")
        self.out = tk.StringVar()
        self.ent_out = self._entry(g2, self.out)
        FlatButton(g2, "选择文件夹", self.browse_dir).pack(side="left", padx=(8, 0))

        # ── 最长保留停顿 ──
        g3 = tk.LabelFrame(body, text="  最长保留停顿  ", bg=BG, font=(FONT, FS), padx=10, pady=6)
        g3.pack(fill="x", pady=(0, 10))
        row = tk.Frame(g3, bg=BG); row.pack(fill="x")
        self.keep = tk.IntVar(value=450)
        self.lbl_ms = tk.Label(row, text="450 ms", bg=BG, fg="#3c3cc8", font=(FONT, FS, "bold"), width=8)
        ttk.Scale(row, from_=150, to=1200, variable=self.keep, command=self._on_scale).pack(
            side="left", fill="x", expand=True)
        self.lbl_ms.pack(side="left", padx=(8, 0))
        tip = tk.Label(g3, text="讲话部分语速不变；中间超过这个长度的停顿（比如隔一秒的换气）会被剪到这个长度。"
                                "数值越小越紧凑，越大越保留停顿。",
                       bg=BG, fg="#828296", font=(FONT, FS - 2), anchor="w", justify="left")
        tip.pack(fill="x", pady=(4, 0))
        tip.bind("<Configure>", lambda e: tip.config(wraplength=e.width - 4))

        # ── 开始 / 打开文件夹 ──
        btns = tk.Frame(body, bg=BG); btns.pack(fill="x", pady=(0, 10))
        self.btn_start = FlatButton(btns, "开  始  处  理", self.start, font=(FONT, FS + 3, "bold"), pady=10)
        self.btn_start.pack(side="left", fill="x", expand=True)
        self.btn_open = FlatButton(btns, "打开所在文件夹", self.open_result, bg="#2d6a4f", pady=10)

        # ── 日志 ──
        g4 = tk.LabelFrame(body, text="  日志  ", bg=BG, font=(FONT, FS), padx=6, pady=6)
        g4.pack(fill="both", expand=True)
        self.log = tk.Text(g4, bg="#141423", fg="#c8c8d2", font=(MONO, FS - 1), relief="flat",
                           insertbackground="#c8c8d2", wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)
        for tag, col in [("ok", "#50dc78"), ("err", "#ff6464"), ("info", "#50b4ff")]:
            self.log.tag_config(tag, foreground=col)

        if HAS_DND:
            for w, var in [(self.ent_path, self.path), (self.ent_out, self.out)]:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", lambda e, v=var: v.set(_drop_path(e.data)))

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self._poll)

    # ── 布局小工具 ──
    def _group(self, parent, text):
        g = tk.LabelFrame(parent, text=text, bg=BG, font=(FONT, FS), padx=10, pady=8)
        g.pack(fill="x", pady=(0, 10))
        return g

    def _entry(self, parent, var):
        e = tk.Entry(parent, textvariable=var, font=(FONT, FS), relief="solid", bd=1)
        e.pack(side="left", fill="x", expand=True, ipady=3)
        return e

    def _on_scale(self, _=None):
        v = int(round(self.keep.get() / 10) * 10)
        self.keep.set(v)
        self.lbl_ms.config(text=f"{v} ms")

    # ── 按钮动作 ──
    def browse_file(self):
        p = filedialog.askopenfilename(filetypes=[("音频", "*.wav *.mp3 *.flac"), ("全部", "*.*")])
        if p:
            self.path.set(p)

    def browse_dir(self):
        p = filedialog.askdirectory()
        if p:
            self.out.set(p)

    def clear(self):
        self.path.set(""); self.out.set("")
        self.log.config(state="normal"); self.log.delete("1.0", "end"); self.log.config(state="disabled")
        self.btn_open.pack_forget()

    def open_result(self):
        if self.last_output and os.path.exists(self.last_output):
            open_folder(self.last_output)

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        src = self.path.get().strip().strip('"')
        if not src:
            messagebox.showinfo("去气口工具", "请先选择或拖入音频文件！"); return
        self.clear_log()
        self.btn_open.pack_forget()
        self.btn_start.set_enabled(False)
        keep = self.keep.get()
        self.write(f"文件：{os.path.basename(src)}")
        self.write(f"最长保留停顿：{keep}ms")
        if self.out.get().strip():
            self.write(f"保存到：{self.out.get().strip()}")
        self.write("---")
        self.worker = threading.Thread(target=self._run, args=(src, keep, self.out.get()), daemon=True)
        self.worker.start()

    def _run(self, src, keep, out_dir):
        try:
            res = core.process(src, keep, out_dir, log=self.q.put)
        except Exception as e:     # 读不了的文件、没写权限等，都在日志里说清楚
            self.q.put(f"Error: 处理失败 —— {e}")
            res = None
        self.q.put(("__done__", res))

    # ── 日志 ──
    def clear_log(self):
        self.log.config(state="normal"); self.log.delete("1.0", "end"); self.log.config(state="disabled")

    def write(self, text):
        tag = None
        if any(k in text for k in ("完成", "已保存", "Done", "Saved")):
            tag = "ok"
        elif any(k in text for k in ("Error", "失败", "不存在", "不支持")):
            tag = "err"
        elif "->" in text or "检测到" in text:
            tag = "info"
        self.log.config(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def _poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                if isinstance(m, tuple) and m[0] == "__done__":
                    self.btn_start.set_enabled(True)
                    self.last_output = m[1]
                    if m[1]:
                        self.btn_open.pack(side="left", padx=(8, 0))
                else:
                    self.write(str(m))
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def on_close(self):
        self.root.destroy()       # 处理线程是 daemon，会随窗口一起结束


def selftest(src, out_dir):
    """打包后验证用：不开窗口，直接处理一个文件，结果写到 out_dir/selftest.log。"""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "selftest.log"), "w", encoding="utf-8") as f:
        try:
            res = core.process(src, 450, out_dir, log=lambda m: f.write(str(m) + "\n"))
            f.write(f"RESULT {'OK' if res and os.path.exists(res) else 'FAIL'}\n")
        except Exception as e:
            import traceback
            f.write(traceback.format_exc() + "RESULT FAIL\n")


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "--selftest":
        selftest(sys.argv[2], sys.argv[3]); return
    if sys.platform == "win32":
        try:                       # 高分屏下字不发糊（必须在建窗口之前）
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    App(root)
    root.lift(); root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    root.mainloop()


if __name__ == "__main__":
    main()
