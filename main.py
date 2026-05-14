import os
import re
import shutil
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, scrolledtext, ttk

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from chatglm_video.constants import (
    DEFAULT_DOWNLOAD_FOLDER,
    DEFAULT_IMAGE_FOLDER,
    DEFAULT_ROUND_INTERVAL_S_RANGE,
    DEFAULT_STEP_INTERVAL_S_RANGE,
    JITTER_AFTER_SEND_BEFORE_RECYCLE,
    JITTER_DOWNLOAD_POLL,
    JITTER_PAGE_RELOAD,
    JITTER_PAGE_RELOAD_LONG,
    MAX_TASKS,
    PROMPT,
    REFERENCE_IMAGE_ASPECT,
    ROUND_GENERATION_TIMEOUT_S,
    VIDEO_URL,
)
from chatglm_video.crop import confirm_reference_image_crop_modal
from chatglm_video.downloads_top import (
    click_text_download_finished_in_data_index_slots,
    click_toolbar_download_finished_in_data_index_slots,
    count_finished_in_data_index_slots,
    wait_data_index_slots_all_finished,
)
from chatglm_video.fs_utils import (
    default_chrome_profile_dir,
    get_sorted_images,
    move_uploaded_image_to_recycle_bin,
    send2trash,
)
from chatglm_video.humanize import sleep_jittered, sleep_uniform_range
from chatglm_video.login_state import video_studio_appears_logged_in
from chatglm_video.page_health import is_page_crashed
from chatglm_video.send import click_video_send_button
from chatglm_video.stealth import apply_stealth_to_context
from chatglm_video.toolbar import enter_image2video, wait_toolbar_shows_reference


# 字号（点）；字体族在运行时按系统从本机已安装字体中解析
_UI_FONT_SIZE = 10
_UI_FONT_SMALL = 9
_LOG_FONT_SIZE = 10


def _prepare_platform_before_tk() -> None:
    """创建 Tk 主窗口之前的系统级准备。

    Windows：开启每显示器 DPI 感知，减轻高分屏模糊。
    macOS：Retina 由 Tk/Cocoa 与系统处理，此处无需 ctypes。
    Linux：依赖桌面 DPI 与 Tk 配置。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                import ctypes

                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def _resolve_ui_font_family(root: tk.Tk) -> str:
    """按平台选择 UI 主字体族，中英混排尽量清晰。"""
    try:
        raw_families = tuple(tkfont.families(root=root))
    except TypeError:
        raw_families = tuple(tkfont.families())
    lower_to_canon = {f.lower(): f for f in raw_families}
    order: list[str]
    if sys.platform == "win32":
        order = ["Microsoft YaHei UI", "Segoe UI", "Tahoma"]
    elif sys.platform == "darwin":
        order = [
            ".SF NS Text",
            "SF Pro Text",
            "PingFang SC",
            "Heiti SC",
            "Helvetica Neue",
            "Arial Unicode MS",
        ]
    else:
        order = [
            "Noto Sans CJK SC",
            "Noto Sans CJK JP",
            "Noto Sans",
            "Source Han Sans SC",
            "DejaVu Sans",
        ]
    for name in order:
        canon = lower_to_canon.get(name.lower())
        if canon is not None:
            return canon
    try:
        return tkfont.nametofont("TkDefaultFont", root=root).actual()["family"]
    except TypeError:
        return tkfont.nametofont("TkDefaultFont").actual()["family"]


def _configure_tk_named_fonts(family: str) -> None:
    """统一 Tk 内置命名字体，使 ttk 之外的控件也使用清晰 UI 字体。"""
    sz = _UI_FONT_SIZE
    for name in (
        "TkDefaultFont",
        "TkTextFont",
        "TkHeadingFont",
        "TkCaptionFont",
        "TkMenuFont",
        "TkSmallCaptionFont",
        "TkIconFont",
    ):
        try:
            tkfont.nametofont(name).configure(family=family, size=sz)
        except tk.TclError:
            pass


class ChatGLMVideoApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self._ui_font_family = _resolve_ui_font_family(self)
        _configure_tk_named_fonts(self._ui_font_family)
        self.title("智谱清言 · 图生视频自动化")
        self.geometry("1160x1336")
        self.minsize(880, 1000)

        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._relogin_worker: threading.Thread | None = None
        self._login_win: tk.Toplevel | None = None
        self._orphan_playwright = None
        self._orphan_context = None

        self._apply_ui_styles()
        self._build_ui()

    def _apply_ui_styles(self) -> None:
        """浅色面板 + 略放大内边距的输入框/按钮（clam 主题便于统一配色）。"""
        bg = "#eceff1"
        fg = "#1e293b"
        muted = "#64748b"
        try:
            self.configure(bg=bg)
        except tk.TclError:
            pass
        try:
            st = ttk.Style()
            st.theme_use("clam")
            st.configure(".", background=bg)
            st.configure("TFrame", background=bg)
            ff = self._ui_font_family
            st.configure(
                "TLabel", background=bg, foreground=fg, font=(ff, _UI_FONT_SIZE)
            )
            st.configure(
                "Muted.TLabel",
                background=bg,
                foreground=muted,
                font=(ff, _UI_FONT_SMALL),
            )
            st.configure(
                "TEntry",
                fieldbackground="#ffffff",
                foreground=fg,
                insertwidth=1,
                insertcolor="#2563eb",
                padding=(9, 7),
                borderwidth=1,
                relief="solid",
            )
            st.configure("TButton", font=(ff, _UI_FONT_SIZE), padding=(12, 7))
            st.map(
                "TButton",
                background=[("active", "#dbeafe"), ("pressed", "#bfdbfe")],
                foreground=[("disabled", "#94a3b8")],
            )
            st.configure(
                "Primary.TButton",
                font=(ff, _UI_FONT_SIZE, "bold"),
                padding=(18, 8),
                background="#2563eb",
                foreground="#ffffff",
            )
            st.map(
                "Primary.TButton",
                background=[
                    ("active", "#1d4ed8"),
                    ("pressed", "#1e40af"),
                    ("disabled", "#94a3b8"),
                ],
                foreground=[("disabled", "#e2e8f0")],
            )
            st.configure("Browse.TButton", font=(ff, _UI_FONT_SMALL), padding=(10, 6))
            st.map("Browse.TButton", background=[("active", "#e2e8f0")])
            st.configure(
                "TRadiobutton",
                background=bg,
                foreground=fg,
                font=(ff, _UI_FONT_SIZE),
                indicatordiameter=14,
            )
            st.map("TRadiobutton", background=[("active", bg)])
            st.configure("TSeparator", background="#cbd5e1")
        except tk.TclError:
            pass

    def _build_ui(self):
        pad = {"padx": 6, "pady": 5}
        frm = ttk.Frame(self, padding=(14, 10, 14, 6))
        frm.pack(fill=tk.X)

        ttk.Label(frm, text="图片文件夹：").grid(row=0, column=0, sticky=tk.W, **pad)
        self.var_image = tk.StringVar(value=DEFAULT_IMAGE_FOLDER)
        ttk.Entry(frm, textvariable=self.var_image, width=36).grid(
            row=0, column=1, sticky=tk.EW, **pad
        )
        ttk.Button(
            frm, text="浏览…", command=self._browse_image, style="Browse.TButton"
        ).grid(row=0, column=2, **pad)

        ttk.Label(frm, text="视频保存到：").grid(row=1, column=0, sticky=tk.W, **pad)
        self.var_download = tk.StringVar(value=DEFAULT_DOWNLOAD_FOLDER)
        ttk.Entry(frm, textvariable=self.var_download, width=36).grid(
            row=1, column=1, sticky=tk.EW, **pad
        )
        ttk.Button(
            frm, text="浏览…", command=self._browse_download, style="Browse.TButton"
        ).grid(row=1, column=2, **pad)

        ttk.Label(frm, text="Chrome 配置目录：").grid(row=2, column=0, sticky=tk.W, **pad)
        self.var_profile = tk.StringVar(value=default_chrome_profile_dir())
        ttk.Entry(frm, textvariable=self.var_profile, width=36).grid(
            row=2, column=1, sticky=tk.EW, **pad
        )
        ttk.Button(
            frm, text="浏览…", command=self._browse_profile, style="Browse.TButton"
        ).grid(row=2, column=2, **pad)

        ttk.Label(frm, text="视频提示词：").grid(row=3, column=0, sticky=tk.NW, **pad)
        self.var_prompt = tk.StringVar(value=PROMPT)
        ttk.Entry(frm, textvariable=self.var_prompt).grid(
            row=3, column=1, columnspan=2, sticky=tk.EW, **pad
        )

        ttk.Label(frm, text="轮次间隔（秒）：").grid(
            row=4, column=0, sticky=tk.W, **pad
        )
        f_round = ttk.Frame(frm)
        f_round.grid(row=4, column=1, columnspan=2, sticky=tk.W, **pad)
        ttk.Label(f_round, text="最小").pack(side=tk.LEFT)
        self.var_round_lo = tk.StringVar(
            value=str(DEFAULT_ROUND_INTERVAL_S_RANGE[0])
        )
        ttk.Entry(f_round, textvariable=self.var_round_lo, width=8).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Label(f_round, text="最大").pack(side=tk.LEFT, padx=(10, 0))
        self.var_round_hi = tk.StringVar(
            value=str(DEFAULT_ROUND_INTERVAL_S_RANGE[1])
        )
        ttk.Entry(f_round, textvariable=self.var_round_hi, width=8).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Label(
            f_round,
            text="每轮之间均匀随机",
            style="Muted.TLabel",
        ).pack(side=tk.LEFT, padx=(12, 0))

        ttk.Label(frm, text="步骤间隔（秒）：").grid(
            row=5, column=0, sticky=tk.W, **pad
        )
        f_step = ttk.Frame(frm)
        f_step.grid(row=5, column=1, columnspan=2, sticky=tk.W, **pad)
        ttk.Label(f_step, text="最小").pack(side=tk.LEFT)
        self.var_step_lo = tk.StringVar(value=str(DEFAULT_STEP_INTERVAL_S_RANGE[0]))
        ttk.Entry(f_step, textvariable=self.var_step_lo, width=8).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Label(f_step, text="最大").pack(side=tk.LEFT, padx=(10, 0))
        self.var_step_hi = tk.StringVar(value=str(DEFAULT_STEP_INTERVAL_S_RANGE[1]))
        ttk.Entry(f_step, textvariable=self.var_step_hi, width=8).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Label(
            f_step,
            text="选图/裁切/填词/提交与重试、下载间隔；点生成后长等待为固定值",
            style="Muted.TLabel",
        ).pack(side=tk.LEFT, padx=(12, 0))

        ttk.Label(frm, text="参考图比例：").grid(row=6, column=0, sticky=tk.W, **pad)
        _ra = REFERENCE_IMAGE_ASPECT.strip()
        if _ra not in ("16:9", "9:16"):
            _ra = "16:9"
        self.var_aspect = tk.StringVar(value=_ra)
        f_asp = ttk.Frame(frm)
        f_asp.grid(row=6, column=1, columnspan=2, sticky=tk.W, **pad)
        ttk.Radiobutton(
            f_asp,
            text="16∶9（横屏）",
            value="16:9",
            variable=self.var_aspect,
        ).pack(side=tk.LEFT, padx=(0, 14))
        ttk.Radiobutton(
            f_asp,
            text="9∶16（竖屏）",
            value="9:16",
            variable=self.var_aspect,
        ).pack(side=tk.LEFT)

        frm.columnconfigure(1, weight=1)

        btn_frm = ttk.Frame(self, padding=(14, 6, 14, 6))
        btn_frm.pack(fill=tk.X)
        self.btn_start = ttk.Button(
            btn_frm, text="开始", command=self._on_start, style="Primary.TButton"
        )
        self.btn_start.pack(side=tk.LEFT, padx=(0, 10))
        self.btn_stop = ttk.Button(
            btn_frm, text="停止", command=self._on_stop, state=tk.DISABLED
        )
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_relogin = ttk.Button(
            btn_frm, text="重新登录", command=self._on_relogin
        )
        self.btn_relogin.pack(side=tk.LEFT, padx=(10, 0))

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=14, pady=(0, 0))

        log_hdr = ttk.Frame(self, padding=(14, 8, 14, 2))
        log_hdr.pack(fill=tk.X)
        ttk.Label(
            log_hdr,
            text="运行日志",
            font=(self._ui_font_family, _UI_FONT_SIZE, "bold"),
        ).pack(side=tk.LEFT)
        ttk.Label(log_hdr, text="（最近输出）", style="Muted.TLabel").pack(
            side=tk.LEFT, padx=(8, 0)
        )

        self.log = scrolledtext.ScrolledText(
            self,
            height=14,
            state=tk.DISABLED,
            wrap=tk.WORD,
            font=(self._ui_font_family, _LOG_FONT_SIZE),
            padx=10,
            pady=8,
            bg="#ffffff",
            fg="#111827",
            insertbackground="#111827",
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor="#2563eb",
            selectbackground="#dbeafe",
        )
        self.log.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12))
        self._log_max_lines = 520
        self._configure_log_tags()

    def _configure_log_tags(self) -> None:
        """日志行颜色（浅色背景上可读）。"""
        t = self.log
        t.tag_configure("log_muted", foreground="#6b7280")
        t.tag_configure("log_info", foreground="#1f2937")
        t.tag_configure("log_step", foreground="#1d4ed8")
        t.tag_configure("log_ok", foreground="#047857")
        t.tag_configure("log_warn", foreground="#b45309")
        t.tag_configure("log_err", foreground="#b91c1c")

    def _browse_image(self):
        d = filedialog.askdirectory(title="选择要上传的图片所在文件夹")
        if d:
            self.var_image.set(d)

    def _browse_download(self):
        d = filedialog.askdirectory(title="选择视频下载保存文件夹")
        if d:
            self.var_download.set(d)

    def _browse_profile(self):
        d = filedialog.askdirectory(title="选择 Chrome 用户数据目录（用于保留登录）")
        if d:
            self.var_profile.set(d)

    def _parse_interval_s(
        self, lo_s: str, hi_s: str, *, title: str, max_hi: float
    ) -> tuple[float, float] | None:
        """解析「最小 / 最大」秒数，返回 (lo, hi) 闭区间；无效时弹窗并返回 None。"""
        try:
            lo = float(str(lo_s).strip())
            hi = float(str(hi_s).strip())
        except ValueError:
            messagebox.showerror(
                "错误",
                f"「{title}」的最小、最大须为数字（秒）。",
                parent=self,
            )
            return None
        if lo < 0 or hi < 0:
            messagebox.showerror("错误", f"「{title}」不能为负数。", parent=self)
            return None
        if lo > hi:
            lo, hi = hi, lo
        hi = min(hi, max_hi)
        lo = min(max(0.05, lo), hi)
        hi = max(lo, hi)
        return (lo, hi)

    def _append_log(self, text: str, *, kind: str = "info") -> None:
        """kind: muted | info | step | ok | warn | err —— 前缀图标 + 颜色。"""

        def _do():
            tag_map = {
                "muted": "log_muted",
                "info": "log_info",
                "step": "log_step",
                "ok": "log_ok",
                "warn": "log_warn",
                "err": "log_err",
            }
            icon_map = {
                "muted": "·",
                "info": "•",
                "step": "›",
                "ok": "✓",
                "warn": "!",
                "err": "✗",
            }
            tag = tag_map.get(kind, "log_info")
            icon = icon_map.get(kind, "•")
            line = f"{icon}  {text}\n"

            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, line, (tag,))
            last = int(str(self.log.index("end-1c")).split(".")[0])
            if last > self._log_max_lines:
                cut = last - self._log_max_lines + 1
                self.log.delete("1.0", f"{cut}.0")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)

        self.after(0, _do)

    def _close_login_dialog(self):
        if self._login_win and self._login_win.winfo_exists():
            try:
                self._login_win.destroy()
            except tk.TclError:
                pass
        self._login_win = None

    def _show_login_dialog(self, login_event: threading.Event, *, relogin: bool = False):
        self._close_login_dialog()
        win = tk.Toplevel(self)
        self._login_win = win
        win.title("重新登录" if relogin else "登录")
        win.transient(self)
        win.grab_set()
        hint = "如登录已经完成，请点击继续"
        ttk.Label(
            win,
            text=hint,
            justify=tk.CENTER,
            wraplength=320,
        ).pack(padx=24, pady=(18, 12))

        def on_ok():
            login_event.set()
            win.destroy()
            self._login_win = None

        ttk.Button(win, text="继续", command=on_ok).pack(pady=(4, 18))
        win.protocol("WM_DELETE_WINDOW", on_ok)
        self._place_toplevel_on_parent_center(win, self)
        win.after(80, lambda w=win: self._place_toplevel_on_parent_center(w, self))

    def _place_toplevel_on_parent_center(self, win: tk.Toplevel, parent: tk.Tk) -> None:
        """将登录提示等 Toplevel 置于主窗口可视区域中央（避免出现在左上角）。"""
        try:
            if not win.winfo_exists():
                return
        except tk.TclError:
            return
        win.update_idletasks()
        win.resizable(False, False)
        req_w = int(win.winfo_reqwidth())
        req_h = int(win.winfo_reqheight())
        parent.update_idletasks()
        px = int(parent.winfo_rootx())
        py = int(parent.winfo_rooty())
        pw = max(int(parent.winfo_width()), 320)
        ph = max(int(parent.winfo_height()), 300)
        # 弹窗不超过主窗口，且上限约主窗一半宽度，避免过宽
        w = min(max(req_w, 260), min(380, pw - 24))
        h = max(req_h, 110)
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = max(8, min(x, sw - w - 16))
        y = max(8, min(y, sh - h - 48))
        win.geometry(f"{w}x{h}+{x}+{y}")

    def _wait_login_or_stop(self, login_event: threading.Event, *, relogin: bool = False) -> bool:
        self.after(0, lambda: self._show_login_dialog(login_event, relogin=relogin))
        while True:
            if self._stop.is_set():
                self.after(0, self._close_login_dialog)
                return False
            if login_event.wait(timeout=0.25):
                return True

    def _run_relogin_only(self, profile_dir: str, download_folder: str) -> None:
        """关闭旧实例 → 删除持久化 user_data 目录 → 新开浏览器与「开始」相同的登录确认。"""
        pw = None
        context = None
        leave_open = False
        login_event = threading.Event()
        abs_profile = os.path.abspath(profile_dir)
        try:
            self._shutdown_orphan_playwright()
            self._append_log("—— 重新登录：已关闭上次浏览器（如有）。", kind="step")

            if os.path.isfile(abs_profile):
                self._append_log(
                    "重新登录：配置路径是文件而非文件夹，无法删除。",
                    kind="err",
                )
                return
            if os.path.isdir(abs_profile):
                try:
                    shutil.rmtree(abs_profile)
                    self._append_log(f"已删除配置目录：{abs_profile}", kind="ok")
                except OSError as e:
                    self._append_log(
                        f"删除配置目录失败（请确认未被占用）：{e}",
                        kind="err",
                    )
                    return
            else:
                self._append_log(
                    f"配置目录不存在，将新建：{abs_profile}",
                    kind="muted",
                )
            os.makedirs(abs_profile, exist_ok=True)

            pw = sync_playwright().start()
            try:
                context = self._launch_context(pw, abs_profile, download_folder)
            except Exception as e:
                self._append_log(f"重新登录：启动浏览器失败：{e}", kind="err")
                return

            apply_stealth_to_context(context)
            self._append_log(
                f"重新登录：已就绪 · 下载目录 {os.path.abspath(download_folder)}",
                kind="muted",
            )

            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=120000)
            except Exception as e:
                self._append_log(f"重新登录：打开页面失败：{e}", kind="err")
                return

            self._append_log(
                "请在浏览器登录后点弹窗「继续」；主界面「停止」可中止。",
                kind="step",
            )
            if not self._wait_login_or_stop(login_event, relogin=True):
                self._append_log("重新登录已中止。", kind="warn")
                return
            leave_open = True
            self._append_log("重新登录完成；浏览器已保留。", kind="ok")
        except Exception as e:
            self._append_log(f"重新登录过程异常：{e}", kind="err")
        finally:
            keep = (
                pw is not None
                and context is not None
                and leave_open
                and not self._stop.is_set()
            )
            if keep:
                self._orphan_playwright = pw
                self._orphan_context = context
                self._append_log("下次「开始」会先关闭本次浏览器。", kind="muted")
            else:
                self._cleanup_playwright_session(pw, context)
                if self._stop.is_set():
                    self._append_log("已中止重新登录。", kind="warn")
            self._append_log("—— 重新登录结束 ——", kind="muted")

    def _relogin_finished(self) -> None:
        self.btn_start.configure(state=tk.NORMAL)
        self.btn_relogin.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)
        self._stop.clear()
        self._relogin_worker = None

    def _on_relogin(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("提示", "任务运行中，请先点「停止」再重新登录。")
            return
        if self._relogin_worker and self._relogin_worker.is_alive():
            messagebox.showinfo("提示", "正在重新登录，请稍候。")
            return

        profile_dir = self.var_profile.get().strip()
        download_folder = self.var_download.get().strip()
        if not profile_dir:
            messagebox.showerror("错误", "请设置 Chrome 配置目录。")
            return
        if not download_folder:
            messagebox.showerror("错误", "请设置视频保存路径。")
            return
        try:
            os.makedirs(download_folder, exist_ok=True)
        except OSError as e:
            messagebox.showerror("错误", f"无法创建下载目录：{e}")
            return

        abs_profile = os.path.abspath(profile_dir)
        if os.path.isfile(abs_profile):
            messagebox.showerror("错误", "Chrome 配置路径指向文件而非文件夹。")
            return
        if not messagebox.askyesno("重新登录", "确认是否重新登录？", parent=self):
            return

        self._stop.clear()
        self.btn_start.configure(state=tk.DISABLED)
        self.btn_relogin.configure(state=tk.DISABLED)
        self.btn_stop.configure(state=tk.NORMAL)

        def run():
            try:
                self._run_relogin_only(profile_dir, download_folder)
            finally:
                self.after(0, self._relogin_finished)

        self._relogin_worker = threading.Thread(target=run, daemon=True)
        self._relogin_worker.start()

    def _on_start(self):
        image_folder = self.var_image.get().strip()
        download_folder = self.var_download.get().strip()
        profile_dir = self.var_profile.get().strip()

        if not image_folder or not os.path.isdir(image_folder):
            try:
                os.makedirs(image_folder, exist_ok=True)
            except OSError as e:
                messagebox.showerror("错误", f"无法创建图片文件夹：{e}")
                return
        if not os.path.isdir(image_folder):
            messagebox.showerror("错误", "请选择有效的图片文件夹。")
            return
        if not download_folder:
            messagebox.showerror("错误", "请设置视频保存路径。")
            return
        if not profile_dir:
            messagebox.showerror("错误", "请设置 Chrome 配置目录。")
            return

        images = get_sorted_images(image_folder)
        if not images:
            messagebox.showerror("错误", "该文件夹内没有支持的图片（jpg / png / webp）。")
            return

        if self._worker and self._worker.is_alive():
            messagebox.showinfo("提示", "任务已在运行中。")
            return
        if self._relogin_worker and self._relogin_worker.is_alive():
            messagebox.showinfo("提示", "正在重新登录，请稍候完成或中止后再点开始。")
            return

        raw = self.var_prompt.get().strip()
        prompt_text = raw or PROMPT
        pv = prompt_text.replace("\n", " ").strip()
        if len(pv) > 120:
            pv = pv[:120] + "…"
        self._append_log(
            f"提示词：{'（界面）' if raw else '（默认）'}{pv}",
            kind="muted",
        )

        round_iv = self._parse_interval_s(
            self.var_round_lo.get(),
            self.var_round_hi.get(),
            title="轮次间隔",
            max_hi=600.0,
        )
        if round_iv is None:
            return
        step_iv = self._parse_interval_s(
            self.var_step_lo.get(),
            self.var_step_hi.get(),
            title="步骤间隔",
            max_hi=300.0,
        )
        if step_iv is None:
            return
        aspect = self.var_aspect.get().strip()
        if aspect not in ("16:9", "9:16"):
            messagebox.showerror("错误", "请选择 16:9 或 9:16。", parent=self)
            return

        self._stop.clear()
        self.btn_start.configure(state=tk.DISABLED)
        self.btn_stop.configure(state=tk.NORMAL)

        def run():
            try:
                self._run_automation(
                    image_folder,
                    download_folder,
                    profile_dir,
                    len(images),
                    prompt_text,
                    round_interval_s=round_iv,
                    step_interval_s=step_iv,
                    reference_aspect=aspect,
                )
            finally:
                self.after(0, self._automation_finished)

        self._worker = threading.Thread(target=run, daemon=True)
        self._worker.start()

    def _on_stop(self):
        self._append_log(
            "已请求停止（自动化中断，浏览器与本窗口保留）。",
            kind="warn",
        )
        self._stop.set()
        self.btn_stop.configure(state=tk.DISABLED)

    def _automation_finished(self):
        self.btn_start.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)
        self._stop.clear()
        self._worker = None

    def _shutdown_orphan_playwright(self) -> None:
        """下次任务开始前关闭上次「全部完成」时保留的浏览器，避免占用同一 user_data_dir。"""
        ctx = getattr(self, "_orphan_context", None)
        pw = getattr(self, "_orphan_playwright", None)
        self._orphan_context = None
        self._orphan_playwright = None
        self._cleanup_playwright_session(pw, ctx)

    def _cleanup_playwright_session(self, pw, context) -> None:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass

    def _launch_context(self, p, profile_dir: str, download_folder: str):
        os.makedirs(profile_dir, exist_ok=True)
        os.makedirs(download_folder, exist_ok=True)
        abs_dl = os.path.abspath(download_folder)
        # 尽量收敛 Playwright 默认带来的「自动化」痕迹（站点仍可能用 IP/行为等风控）
        chrome_args = [
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            "--exclude-switches=enable-automation",
            "--lang=zh-CN",
        ]
        common = dict(
            user_data_dir=os.path.abspath(profile_dir),
            headless=False,
            channel="chrome",
            args=chrome_args,
            downloads_path=abs_dl,
            accept_downloads=True,
            no_viewport=True,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            # 去掉自动化横幅；去掉强制禁用扩展，便于与日常 Chrome 配置更接近
            ignore_default_args=[
                "--enable-automation",
                "--disable-extensions",
            ],
        )
        try:
            return p.chromium.launch_persistent_context(**common)
        except PlaywrightError as e:
            self._append_log(f"使用系统 Chrome 失败（{e}），改用内置 Chromium…", kind="warn")
            kw = {k: v for k, v in common.items() if k != "channel"}
            return p.chromium.launch_persistent_context(**kw)

    def _run_automation(
        self,
        image_folder: str,
        download_folder: str,
        profile_dir: str,
        total: int,
        prompt_text: str,
        *,
        round_interval_s: tuple[float, float],
        step_interval_s: tuple[float, float],
        reference_aspect: str,
    ):
        self._shutdown_orphan_playwright()
        dl_abs = os.path.abspath(download_folder)
        prof_disp = profile_dir
        if len(prof_disp) > 52:
            prof_disp = "…" + prof_disp[-50:]
        self._append_log(
            f"开始 · 共 {total} 张 · 下载 {dl_abs}",
            kind="step",
        )
        self._append_log(f"Chrome 配置：{prof_disp}", kind="muted")
        login_event = threading.Event()
        completed = 0
        pending = get_sorted_images(image_folder)

        pw = None
        context = None
        leave_browser_open = False

        try:
            pw = sync_playwright().start()
            try:
                context = self._launch_context(pw, profile_dir, download_folder)
            except Exception as e:
                self._append_log(f"启动浏览器失败：{e}", kind="err")
                return

            apply_stealth_to_context(context)
            self._append_log(
                "防检测与随机间隔已启用；请适度使用并遵守平台规则。",
                kind="muted",
            )

            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=120000)
            except Exception as e:
                self._append_log(f"打开页面失败：{e}", kind="err")
                return

            if video_studio_appears_logged_in(page):
                self._append_log("已登录，跳过登录弹窗。", kind="ok")
            else:
                self._append_log("请在浏览器登录后，在弹窗点「继续」。", kind="step")
                if not self._wait_login_or_stop(login_event):
                    self._append_log("已中止（未进入任务）。", kind="warn")
                    return

            if enter_image2video(page) and wait_toolbar_shows_reference(page, 4):
                self._append_log("当前为「参考图生成」模式。", kind="ok")
            else:
                self._append_log(
                    "未能确认参考图模式；每张上传前会自动再切换。",
                    kind="warn",
                )

            self._append_log(
                f"批次模式：每轮最多 {MAX_TASKS} 张；只认 div[data-index] 为 0…{MAX_TASKS - 1} 的槽位；"
                f"每槽最多等 {ROUND_GENERATION_TIMEOUT_S:.0f} 秒，超时则下载已完成的并刷新页面后继续。",
                kind="muted",
            )
            self._append_log(
                f"节奏 · 轮次 {round_interval_s[0]:.2f}〜{round_interval_s[1]:.2f}s · "
                f"步骤 {step_interval_s[0]:.2f}〜{step_interval_s[1]:.2f}s · 参考图比例 {reference_aspect}",
                kind="muted",
            )

            logged_no_images = False
            warned_send2trash = False

            r_lo, r_hi = round_interval_s
            s_lo, s_hi = step_interval_s

            def sleep_round_gap() -> None:
                sleep_uniform_range(r_lo, r_hi)

            def sleep_step_gap() -> None:
                sleep_uniform_range(s_lo, s_hi)

            def reload_back_to_reference_mode() -> None:
                try:
                    self._append_log("正在刷新页面并回到参考图模式…", kind="step")
                    page.reload(wait_until="domcontentloaded", timeout=120000)
                    sleep_jittered(*JITTER_PAGE_RELOAD)
                    if not enter_image2video(page):
                        sleep_jittered(*JITTER_PAGE_RELOAD_LONG)
                    wait_toolbar_shows_reference(page, 8)
                except Exception as e:
                    self._append_log(f"刷新失败：{e}", kind="warn")

            def download_phase(need_rem: int, n_slot_scan: int) -> int:
                nonlocal completed
                rem = need_rem
                dl_deadline = time.time() + 1200.0
                while rem > 0 and not self._stop.is_set():
                    if time.time() > dl_deadline:
                        self._append_log(
                            f"下载重试超时，仍有 {rem} 个未完成，请人工检查。",
                            kind="warn",
                        )
                        break
                    k = click_toolbar_download_finished_in_data_index_slots(
                        page, n_slot_scan, rem
                    )
                    if k == 0:
                        k = click_text_download_finished_in_data_index_slots(
                            page, n_slot_scan, rem
                        )
                    if k == 0:
                        sleep_jittered(*JITTER_DOWNLOAD_POLL)
                        continue
                    for _ in range(k):
                        completed += 1
                        rem -= 1
                        self._append_log(
                            f"已下载 · {completed}/{total}"
                            + (f" · 待计数 {rem}" if rem else ""),
                            kind="ok",
                        )
                        sleep_step_gap()
                return rem

            try:
                while not self._stop.is_set():
                    if completed >= total and not pending:
                        leave_browser_open = True
                        self._append_log(
                            "全部完成；浏览器未关闭，请自行关窗口。",
                            kind="ok",
                        )
                        self.after(
                            0,
                            lambda: messagebox.showinfo(
                                "完成",
                                "全部图片已处理完成（含超时未生成项）。\n"
                                "浏览器未关闭，请自行关闭；下次「开始」前请先关掉该浏览器以免占用配置目录。",
                            ),
                        )
                        break

                    if is_page_crashed(page):
                        self._append_log("页面异常，正在刷新…", kind="warn")
                        try:
                            page.reload(wait_until="domcontentloaded", timeout=120000)
                            sleep_jittered(*JITTER_PAGE_RELOAD)
                            if not enter_image2video(page):
                                sleep_jittered(*JITTER_PAGE_RELOAD_LONG)
                            continue
                        except Exception as e:
                            self._append_log(f"刷新失败：{e}", kind="err")
                            break

                    if self._stop.is_set():
                        break

                    if not pending:
                        if completed >= total:
                            leave_browser_open = True
                            self._append_log(
                                "全部完成；浏览器未关闭，请自行关窗口。",
                                kind="ok",
                            )
                            self.after(
                                0,
                                lambda: messagebox.showinfo(
                                    "完成",
                                    "全部图片已处理完成（含超时未生成项）。\n"
                                    "浏览器未关闭，请自行关闭；下次「开始」前请先关掉该浏览器以免占用配置目录。",
                                ),
                            )
                            break
                        if not logged_no_images:
                            logged_no_images = True
                            self._append_log(
                                "本地已无待上传图片，正在收尾已提交任务…",
                                kind="step",
                            )
                            self.after(
                                0,
                                lambda: messagebox.showinfo(
                                    "没有图片了",
                                    "本地已无待上传的图片。\n"
                                    "正在按列表槽位 data-index 0…3 等待生成并下载（有超时则刷新后继续）。",
                                ),
                            )
                        outstanding = total - completed
                        if outstanding <= 0:
                            leave_browser_open = True
                            break
                        slots_fb = min(outstanding, MAX_TASKS)
                        self._append_log(
                            f"收尾：等待 data-index 0…{slots_fb - 1} 槽位全部完成（最长 "
                            f"{ROUND_GENERATION_TIMEOUT_S:.0f} 秒）…",
                            kind="step",
                        )
                        all_fin = wait_data_index_slots_all_finished(
                            page,
                            slots_fb,
                            self._stop,
                            timeout=ROUND_GENERATION_TIMEOUT_S,
                        )
                        if not all_fin and not self._stop.is_set():
                            self._append_log(
                                "收尾等待超时或未完成，将下载已就绪项并刷新页面。",
                                kind="warn",
                            )
                        finished_count = count_finished_in_data_index_slots(
                            page, slots_fb
                        )
                        self._append_log("收尾下载中…", kind="step")
                        need_rem = download_phase(finished_count, slots_fb)
                        downloaded_ok = finished_count - need_rem
                        completed += slots_fb - downloaded_ok
                        if self._stop.is_set():
                            break
                        if not all_fin and not self._stop.is_set():
                            reload_back_to_reference_mode()
                        if need_rem > 0 and not self._stop.is_set():
                            self._append_log(
                                f"收尾仍有 {need_rem} 个视频未点到下载，已计入进度并继续。",
                                kind="warn",
                            )
                        if completed >= total:
                            leave_browser_open = True
                            self._append_log(
                                "全部完成；浏览器未关闭，请自行关窗口。",
                                kind="ok",
                            )
                            self.after(
                                0,
                                lambda: messagebox.showinfo(
                                    "完成",
                                    "全部图片已处理完成（含超时未生成项）。\n"
                                    "浏览器未关闭，请自行关闭；下次「开始」前请先关掉该浏览器以免占用配置目录。",
                                ),
                            )
                            break
                        sleep_round_gap()
                        continue

                    batch_size = min(MAX_TASKS, len(pending))
                    batch = [pending.pop(0) for _ in range(batch_size)]
                    self._append_log(
                        f"本批 {batch_size} 张：依次提交；生成态只看 data-index 0…{batch_size - 1}。",
                        kind="step",
                    )

                    n_submitted = 0
                    batch_aborted = False
                    for bi, img_path in enumerate(batch):
                        if self._stop.is_set():
                            for r in reversed(batch[bi:]):
                                pending.insert(0, r)
                            batch_aborted = True
                            break
                        img_name = os.path.basename(img_path)
                        try:
                            if not enter_image2video(page):
                                self._append_log(
                                    "参考图模式切换失败，本张 2 秒后重试。",
                                    kind="warn",
                                )
                                pending.insert(0, img_path)
                                for r in reversed(batch[bi + 1 :]):
                                    pending.insert(0, r)
                                sleep_step_gap()
                                batch_aborted = True
                                break
                            if not wait_toolbar_shows_reference(page, 10):
                                self._append_log(
                                    "未确认底部「参考图生成」，本张放回队列。",
                                    kind="warn",
                                )
                                pending.insert(0, img_path)
                                for r in reversed(batch[bi + 1 :]):
                                    pending.insert(0, r)
                                sleep_step_gap()
                                batch_aborted = True
                                break

                            fin = page.locator("input[type='file']").first
                            fin.set_input_files(img_path)
                            sleep_step_gap()
                            confirm_reference_image_crop_modal(
                                page, aspect=reference_aspect
                            )
                            sleep_step_gap()

                            ta = page.get_by_placeholder(
                                re.compile(
                                    r"输入视频描述|上传图片|输入描述|创造你的视频"
                                )
                            )
                            if ta.count() == 0:
                                ta = page.locator(
                                    "[contenteditable='true'], textarea"
                                ).first
                            ta.click(timeout=15000)
                            ta.fill(prompt_text)
                            sleep_step_gap()

                            if not click_video_send_button(page, timeout=75.0):
                                gen_btn = page.get_by_role(
                                    "button",
                                    name=re.compile(r"生成视频|发送|创作"),
                                )
                                if gen_btn.count() > 0:
                                    gen_btn.first.click(timeout=15000)
                                else:
                                    page.locator(
                                        "button",
                                        has_text=re.compile(r"生成视频"),
                                    ).first.click(timeout=15000)
                            self._append_log(
                                f"已提交 {img_name} · 待上传队列 {len(pending)} 张",
                                kind="ok",
                            )
                            sleep_jittered(*JITTER_AFTER_SEND_BEFORE_RECYCLE)
                            if move_uploaded_image_to_recycle_bin(img_path):
                                pass
                            elif send2trash is None:
                                if not warned_send2trash:
                                    warned_send2trash = True
                                    self._append_log(
                                        "未安装 send2trash，原图不会进回收站（可 pip install）。",
                                        kind="warn",
                                    )
                            else:
                                self._append_log(
                                    f"原图未进回收站：{img_name}",
                                    kind="warn",
                                )
                            sleep_step_gap()
                            n_submitted += 1
                        except Exception as e:
                            self._append_log(
                                f"上传失败，已放回队列：{e}", kind="err"
                            )
                            pending.insert(0, img_path)
                            for r in reversed(batch[bi + 1 :]):
                                pending.insert(0, r)
                            sleep_step_gap()
                            batch_aborted = True
                            break

                    if batch_aborted:
                        continue

                    if n_submitted == 0:
                        sleep_step_gap()
                        continue

                    self._append_log(
                        f"本批 {n_submitted} 条已提交，等待 data-index 0…{n_submitted - 1} 全部完成（最长 "
                        f"{ROUND_GENERATION_TIMEOUT_S:.0f} 秒）…",
                        kind="step",
                    )
                    all_fin = wait_data_index_slots_all_finished(
                        page,
                        n_submitted,
                        self._stop,
                        timeout=ROUND_GENERATION_TIMEOUT_S,
                    )
                    if not all_fin and not self._stop.is_set():
                        self._append_log(
                            "本批等待超时：将下载已生成完成的，未完成的不再等待；随后刷新页面。",
                            kind="warn",
                        )
                    finished_count = count_finished_in_data_index_slots(
                        page, n_submitted
                    )

                    self._append_log("本批下载中…", kind="step")
                    need_rem = download_phase(finished_count, n_submitted)
                    downloaded_ok = finished_count - need_rem
                    completed += n_submitted - downloaded_ok

                    if not all_fin and not self._stop.is_set():
                        reload_back_to_reference_mode()
                    if need_rem > 0 and not self._stop.is_set():
                        self._append_log(
                            f"本批仍有 {need_rem} 个视频未点到下载，已计入进度。",
                            kind="warn",
                        )

                    if self._stop.is_set():
                        break

                    sleep_round_gap()

                if completed >= total and not self._stop.is_set():
                    leave_browser_open = True

            except Exception as e:
                self._append_log(f"运行异常：{e}", kind="err")
        except Exception as e:
            self._append_log(f"Playwright 启动或运行失败：{e}", kind="err")
        finally:
            keep_browser = leave_browser_open or self._stop.is_set()
            if keep_browser and pw is not None:
                self._orphan_playwright = pw
                self._orphan_context = context
                if self._stop.is_set() and not leave_browser_open:
                    self._append_log(
                        "已停止；浏览器未关闭。下次「开始」会先结束上次实例。",
                        kind="warn",
                    )
                else:
                    self._append_log(
                        "浏览器未关闭；下次「开始」会先尝试结束上次实例。",
                        kind="muted",
                    )
            else:
                self._cleanup_playwright_session(pw, context)
            self._append_log(f"结束 · 完成 {completed}/{total}", kind="info")


def main():
    _prepare_platform_before_tk()
    app = ChatGLMVideoApp()
    app.mainloop()


if __name__ == "__main__":
    main()