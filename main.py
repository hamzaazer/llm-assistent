from __future__ import annotations

import json
import queue
import shutil
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from cliboard import (
    build_text_tools_page as clipboard_build_text_tools_page,
    clear_text_tools as clipboard_clear_text_tools,
    copy_text_tool_result as clipboard_copy_text_tool_result,
    finish_text_tool_request as clipboard_finish_text_tool_request,
    open_text_tool_clipboard_window as clipboard_open_text_tool_clipboard_window,
    process_text_tools_request,
    refresh_clipboard_preview,
    run_text_tool_request as clipboard_run_text_tool_request,
    set_text_tool_busy as clipboard_set_text_tool_busy,
    swap_text_tools as clipboard_swap_text_tools,
    use_clipboard_for_text_tool as clipboard_use_clipboard_for_text_tool,
    update_text_tool_operation_ui,
)
from models import *
from organizer import run_organize_preview
from translate import run_translate_preview
from speed import run_speed_preview


class Worker:
    def __init__(self, app: "LLMHelperApp", operation_name: Optional[str] = None):
        self.app = app
        self.stop_event = threading.Event()
        self.operation_name = operation_name or app.operation_var.get()

    def stop(self) -> None:
        self.stop_event.set()

    def _should_skip_path(self, root: Path, path: Path, skip_hidden: bool, ignore_organized: bool) -> bool:
        if not path.is_file():
            return True

        rel_parts = path.relative_to(root).parts if path != root else ()
        if skip_hidden and any(part.startswith(".") for part in rel_parts):
            return True

        if ignore_organized and rel_parts:
            first = rel_parts[0]
            if first in ROOT_ORGANIZED_FOLDERS:
                return True

        return False

    def _iter_files(self, root: Path, recursive: bool, max_files: int, skip_hidden: bool, ignore_organized: bool) -> List[Path]:
        out: List[Path] = []
        iterator = root.rglob("*") if recursive else root.glob("*")

        for p in iterator:
            if self.stop_event.is_set():
                break
            if self._should_skip_path(root, p, skip_hidden, ignore_organized):
                continue
            out.append(p)
            if len(out) >= max_files:
                break
        return out

    def _collect_process_candidates(self, limit: int) -> List[Dict[str, Any]]:
        if psutil is None:
            return []

        current_user = ""
        try:
            current_user = (psutil.Process().username() or "").strip().lower()
        except Exception:
            pass

        try:
            for proc in psutil.process_iter():
                try:
                    proc.cpu_percent(interval=None)
                except Exception:
                    pass
        except Exception:
            pass

        time.sleep(0.35)

        items: List[Dict[str, Any]] = []

        for proc in psutil.process_iter(["pid", "name", "username", "exe", "status", "memory_info"]):
            if self.stop_event.is_set():
                break

            try:
                info = proc.info
                pid = int(info.get("pid") or 0)
                name = (info.get("name") or "").strip()
                username = (info.get("username") or "").strip()
                exe = (info.get("exe") or "").strip()
                status = str(info.get("status") or "").strip()

                if pid <= 4 or not name:
                    continue
                if is_current_app_process(pid, name, exe):
                    continue
                if is_protected_process_name(name):
                    continue
                if is_system_username(username):
                    continue
                if is_system_process_path(exe):
                    continue
                if current_user and username and username.lower() != current_user:
                    continue

                rss = 0.0
                mem_info = info.get("memory_info")
                if mem_info is not None:
                    rss = round(mem_info.rss / (1024 * 1024), 1)

                cpu = 0.0
                try:
                    cpu = float(proc.cpu_percent(interval=None))
                except Exception:
                    cpu = 0.0

                if cpu <= 0.3 and rss < 80:
                    continue

                items.append({
                    "pid": pid,
                    "name": name,
                    "username": username,
                    "exe": exe,
                    "status": status,
                    "rss_mb": rss,
                    "cpu_percent": round(cpu, 1),
                })
            except Exception:
                continue

        items.sort(
            key=lambda x: (
                (x.get("cpu_percent", 0.0) * 3.0)
                + (x.get("rss_mb", 0.0) / 120.0)
            ),
            reverse=True,
        )
        return items[:limit]

    def choose_model_for_file(self, insight: FileInsight, cfg: Dict[str, Any]) -> Tuple[str, str]:
        if insight.kind == "image" and cfg["use_vision"] and cfg["vision_model"].strip():
            return "vision", cfg["vision_model"].strip()
        return "text", cfg["text_model"].strip()

    def preview(self) -> None:
        cfg = self.app.get_config()
        operation = cfg["operation"]

        if operation == "Organize Files":
            run_organize_preview(self, cfg)
        elif operation == "Translate Filenames":
            run_translate_preview(self, cfg)
        elif operation == "Clear Cpu":
            run_speed_preview(self, cfg)
        else:
            self.app.emit("done", {"decisions": [], "errors": [f"Unknown operation: {operation}"]}, op=self.operation_name)

    def apply(self, decisions: List[Decision]) -> None:
        total = len(decisions)
        processed = 0
        errors: List[str] = []

        for idx, dec in enumerate(decisions, start=1):
            if self.stop_event.is_set():
                errors.append("Apply operation stopped by user.")
                break

            self.app.emit("progress", {"current": idx - 1, "total": max(1, total), "file": dec.source.name}, op=self.operation_name)

            try:
                if dec.action == "terminate_process":
                    if psutil is None:
                        raise RuntimeError("psutil is not installed")

                    pid = int(dec.process_pid or 0)
                    if pid <= 0:
                        raise RuntimeError("Invalid process PID")

                    if is_current_app_process(pid):
                        raise PermissionError("Refusing to stop the current app process.")

                    try:
                        proc = psutil.Process(pid)
                    except psutil.NoSuchProcess:
                        self.app.emit("progress", {"current": idx, "total": max(1, total), "file": dec.source.name}, op=self.operation_name)
                        continue

                    live_name = ""
                    live_user = ""
                    live_exe = ""

                    try:
                        live_name = proc.name() or ""
                    except Exception:
                        pass
                    try:
                        live_user = proc.username() or ""
                    except Exception:
                        pass
                    try:
                        live_exe = proc.exe() or ""
                    except Exception:
                        pass

                    if is_current_app_process(pid, live_name, live_exe):
                        raise PermissionError(f"Refusing to stop the current app: {live_name or pid}")
                    if is_protected_process_name(live_name):
                        raise PermissionError(f"Protected system/application process: {live_name}")
                    if is_system_username(live_user):
                        raise PermissionError(f"System-owned process: {live_user}")
                    if is_system_process_path(live_exe):
                        raise PermissionError(f"Protected executable path: {live_exe}")

                    try:
                        proc.terminate()
                        proc.wait(timeout=4)
                    except Exception:
                        try:
                            proc.kill()
                            proc.wait(timeout=2)
                        except Exception as kill_exc:
                            raise RuntimeError(f"Could not stop process {live_name or pid}: {kill_exc}")

                    processed += 1
                    self.app.emit("progress", {"current": idx, "total": max(1, total), "file": dec.source.name}, op=self.operation_name)
                    continue

                if not dec.source.exists():
                    raise FileNotFoundError("Source item no longer exists")

                if dec.action == "rename":
                    target = unique_path(dec.target)
                    if target.resolve() != dec.source.resolve():
                        shutil.move(str(dec.source), str(target))
                        dec.target = target
                        processed += 1
                    self.app.emit("progress", {"current": idx, "total": max(1, total), "file": dec.source.name}, op=self.operation_name)
                    continue

                target = dec.target
                target.parent.mkdir(parents=True, exist_ok=True)

                if target.resolve() == dec.source.resolve():
                    self.app.emit("progress", {"current": idx, "total": max(1, total), "file": dec.source.name}, op=self.operation_name)
                    continue

                target = unique_path(target)

                if dec.action == "copy":
                    shutil.copy2(dec.source, target)
                else:
                    shutil.move(str(dec.source), str(target))

                dec.target = target
                processed += 1

            except Exception as exc:
                errors.append(f"{dec.source.name}: {exc}")

            self.app.emit("progress", {"current": idx, "total": max(1, total), "file": dec.source.name}, op=self.operation_name)

        self.app.emit("applied", {"count": processed, "errors": errors, "decisions": decisions}, op=self.operation_name)


class LLMHelperApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} ")
        self.geometry("1760x1020")
        self.minsize(1400, 860)

        # Set window icon - handle both PyInstaller bundled and source execution
        try:
            # When running as PyInstaller bundle, use sys._MEIPASS
            if getattr(sys, 'frozen', False):
                icon_path = Path(sys._MEIPASS) / 'op.ico'
            else:
                icon_path = Path(__file__).parent / 'op.ico'
            
            if icon_path.exists():
                self.iconbitmap(str(icon_path))
        except Exception:
            pass  # Icon file not found or not supported

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.queue: queue.Queue[Tuple[str, Any]] = queue.Queue()
        self.worker: Optional[Worker] = None

        self.active_operation: Optional[str] = None
        self.decisions_by_op: Dict[str, List[Decision]] = {
            "Organize Files": [],
            "Translate Filenames": [],
            "Clear Cpu": [],
        }
        self.detail_map_by_op: Dict[str, Dict[str, Decision]] = {
            "Organize Files": {},
            "Translate Filenames": {},
            "Clear Cpu": {},
        }
        self.page_ui: Dict[str, Dict[str, Any]] = {}

        self._build_vars()
        self._build_ui()
        self.after(120, self.process_queue)
        self.after(500, self._update_system_monitor)
        self.after(900, self._auto_refresh_text_tool_clipboard)
        self.show_page("home")
        self.after(300, self.startup_setup)

    def _build_vars(self) -> None:
        self.folder_var = tk.StringVar(value=str(Path.home()))
        self.endpoint_var = tk.StringVar(value=DEFAULT_ENDPOINT)
        self.text_model_var = tk.StringVar(value="")
        self.vision_model_var = tk.StringVar(value="")

        self.max_files_var = tk.StringVar(value=str(DEFAULT_MAX_FILES))
        self.max_chars_var = tk.StringVar(value=str(DEFAULT_MAX_CHARS))
        self.timeout_var = tk.StringVar(value=str(DEFAULT_TIMEOUT))
        self.max_file_size_var = tk.StringVar(value=str(DEFAULT_MAX_FILE_MB))
        self.max_image_mb_var = tk.StringVar(value=str(DEFAULT_MAX_IMAGE_MB))

        self.recursive_var = tk.BooleanVar(value=True)
        self.rename_files_var = tk.BooleanVar(value=True)
        self.use_vision_var = tk.BooleanVar(value=True)
        self.skip_hidden_var = tk.BooleanVar(value=True)
        self.ignore_organized_var = tk.BooleanVar(value=True)
        self.action_var = tk.StringVar(value="move")
        self.operation_var = tk.StringVar(value="Organize Files")
        self.target_language_var = tk.StringVar(value="French")
        self.text_tool_operation_var = tk.StringVar(value="Translate")
        self.text_tool_language_var = tk.StringVar(value="Arabic")
        self.text_tool_clipboard_mode_var = tk.StringVar(value="Replace Input")
        self.text_tool_status_var = tk.StringVar(value="Ready")
        self.text_tool_busy = False
        self.text_tool_clipboard_history: List[str] = []
        self.text_tool_selected_clipboard_text = ""
        self.text_tool_clipboard_last_value = ""
        self.text_tool_clipboard_window = None
        self.current_page_name = "home"

        self.cpu_live_var = tk.StringVar(value="CPU: -- %")
        self.ram_live_var = tk.StringVar(value="RAM: -- %")

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.container = ctk.CTkFrame(self, corner_radius=0)
        self.container.grid(row=0, column=0, sticky="nsew")
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.pages: Dict[str, ctk.CTkFrame] = {}
        self.home_page = ctk.CTkFrame(self.container, corner_radius=0)
        self.organize_page = ctk.CTkFrame(self.container, corner_radius=0)
        self.translate_page = ctk.CTkFrame(self.container, corner_radius=0)
        self.text_tools_page = ctk.CTkFrame(self.container, corner_radius=0)
        self.speed_page = ctk.CTkFrame(self.container, corner_radius=0)
        self.lmstudio_page = ctk.CTkFrame(self.container, corner_radius=0)

        for name, page in {
            "home": self.home_page,
            "organize": self.organize_page,
            "translate": self.translate_page,
            "text_tools": self.text_tools_page,
            "speed": self.speed_page,
            "lmstudio": self.lmstudio_page,
        }.items():
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = page

        self._build_home_page()
        self._build_organize_page()
        self._build_translate_page()
        self._build_text_tools_page()
        self._build_speed_page()
        self._build_lmstudio_page()

    def show_page(self, page_name: str) -> None:
        page = self.pages.get(page_name)
        if page:
            self.current_page_name = page_name
            page.tkraise()

    def open_organize_page(self) -> None:
        self.operation_var.set("Organize Files")
        self.show_page("organize")

    def open_translate_page(self) -> None:
        self.operation_var.set("Translate Filenames")
        self.show_page("translate")

    def open_text_tools_page(self) -> None:
        self.show_page("text_tools")
        self.refresh_text_tool_clipboard()

    def open_speed_page(self) -> None:
        self.operation_var.set("Clear Cpu")
        self.show_page("speed")

    def open_lmstudio_page(self) -> None:
        self.show_page("lmstudio")

    def _auto_refresh_text_tool_clipboard(self) -> None:
        window = getattr(self, "text_tool_clipboard_window", None)
        should_refresh = self.current_page_name == "text_tools" or (window is not None and window.winfo_exists())

        if should_refresh:
            try:
                clipboard_text = self.clipboard_get()
            except tk.TclError:
                clipboard_text = ""

            if clipboard_text != self.text_tool_clipboard_last_value:
                self.text_tool_clipboard_last_value = clipboard_text
                refresh_clipboard_preview(self, update_status=False)

        self.after(1000, self._auto_refresh_text_tool_clipboard)

    def _section_label(self, master, text: str):
        lbl = ctk.CTkLabel(master, text=text, font=ctk.CTkFont(size=18, weight="bold"))
        lbl.pack(anchor="w", padx=14, pady=(16, 8))
        return lbl

    def _labeled_entry(self, master, label: str, var: tk.Variable, placeholder: str = ""):
        wrap = ctk.CTkFrame(master, fg_color="transparent")
        wrap.pack(fill="x", padx=14, pady=6)
        ctk.CTkLabel(wrap, text=label).pack(anchor="w")
        entry = ctk.CTkEntry(wrap, textvariable=var, placeholder_text=placeholder, height=34)
        entry.pack(fill="x", pady=(6, 0))
        return entry

    def _grid_entry(self, master, row: int, col: int, label: str, var: tk.Variable):
        frame = ctk.CTkFrame(master, fg_color="transparent")
        frame.grid(row=row, column=col, sticky="ew", padx=8, pady=8)
        ctk.CTkLabel(frame, text=label).pack(anchor="w")
        entry = ctk.CTkEntry(frame, textvariable=var, height=34)
        entry.pack(fill="x", pady=(6, 0))
        return entry

    def _make_hidden_box(self, parent):
        textbox = ctk.CTkTextbox(parent, height=1, width=1)
        textbox.grid_forget()
        return textbox

    def _get_textbox_value(self, textbox) -> str:
        return textbox.get("1.0", "end-1c")

    def _set_textbox_value(self, textbox, value: str) -> None:
        textbox.delete("1.0", "end")
        if value:
            textbox.insert("1.0", value)

    def _bind_mousewheel_to_widget(self, widget):
        def _on_mousewheel(event):
            try:
                delta = getattr(event, "delta", 0)
                if delta == 0:
                    return "break"
                steps = int(abs(delta) / 120) if abs(delta) >= 120 else 1
                steps = max(1, steps) * MOUSE_SCROLL_UNITS
                if delta > 0:
                    widget.yview_scroll(-steps, "units")
                else:
                    widget.yview_scroll(steps, "units")
            except Exception:
                pass
            return "break"

        def _on_button4(_event):
            try:
                widget.yview_scroll(-MOUSE_SCROLL_UNITS, "units")
            except Exception:
                pass
            return "break"

        def _on_button5(_event):
            try:
                widget.yview_scroll(MOUSE_SCROLL_UNITS, "units")
            except Exception:
                pass
            return "break"

        try:
            widget.bind("<MouseWheel>", _on_mousewheel, add="+")
            widget.bind("<Shift-MouseWheel>", lambda _e: "break", add="+")
            widget.bind("<Button-4>", _on_button4, add="+")
            widget.bind("<Button-5>", _on_button5, add="+")
        except Exception:
            pass

    def _style_treeview(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("default")
        except Exception:
            pass

        bg = "#0b1220"
        cell = "#111827"
        fg = "#f9fafb"

        style.configure("Treeview", background=cell, foreground=fg, fieldbackground=bg, borderwidth=0, rowheight=36, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background="#1f2937", foreground="#f9fafb", relief="flat", font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#2563eb")], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", "#374151")])

    def _stat_card(self, master, col: int, title: str, value_var: tk.StringVar) -> None:
        colors = [
            ("#1D4ED8", "#1E3A8A"),
            ("#059669", "#065F46"),
            ("#7C3AED", "#5B21B6"),
        ]
        fg1, fg2 = colors[col % len(colors)]

        card = ctk.CTkFrame(master, fg_color=(fg1, fg2))
        card.grid(row=0, column=col, sticky="ew", padx=8, pady=4)

        ctk.CTkLabel(card, text=title, text_color="#DBEAFE").pack(anchor="w", padx=14, pady=(12, 4))
        ctk.CTkLabel(
            card,
            textvariable=value_var,
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color="white",
        ).pack(anchor="w", padx=14, pady=(0, 12))

    def _build_home_page(self) -> None:
        page = self.home_page
        page.grid_rowconfigure(0, weight=1)
        page.grid_columnconfigure(0, weight=1)

        bg = ctk.CTkFrame(page, corner_radius=0, fg_color=("#0b1220", "#0b1220"))
        bg.grid(row=0, column=0, sticky="nsew")
        bg.grid_rowconfigure(0, weight=1)
        bg.grid_columnconfigure(0, weight=1)

        wrap = ctk.CTkFrame(bg, corner_radius=28, fg_color=("#111827", "#111827"))
        wrap.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(wrap, text=HOME_ICON, font=ctk.CTkFont(size=46)).pack(padx=60, pady=(34, 6))
        
        ctk.CTkLabel(
            wrap,
            text="Choose your operation",
            text_color="#9CA3AF",
            font=ctk.CTkFont(size=16),
        ).pack(padx=60, pady=(0, 26))

        cards = ctk.CTkFrame(wrap, fg_color="transparent")
        cards.pack(fill="both", expand=True, padx=28, pady=(0, 28))

        def home_btn(parent, icon: str, title: str, subtitle: str, command, fg: str, hover: str):
            card = ctk.CTkButton(
                parent,
                text=f"{icon}  {title}\n{subtitle}",
                command=command,
                height=76,
                corner_radius=18,
                anchor="w",
                fg_color=fg,
                hover_color=hover,
                font=ctk.CTkFont(size=17, weight="bold"),
            )
            card.pack(fill="x", pady=8)
            return card

        home_btn(cards, ORGANIZE_ICON, "File Organizer", "Smart sorting by title and category", self.open_organize_page, "#2563EB", "#1D4ED8")
        home_btn(cards, TRANSLATE_ICON, "Filename Translator", "Rename filenames into another language", self.open_translate_page, "#059669", "#047857")
        home_btn(cards, TEXT_TOOLS_ICON, "Clipboard Manager", "Translate, correct grammar, or summarize text", self.open_text_tools_page, "#0EA5E9", "#0284C7")
        home_btn(cards, SPEED_ICON, "CPU Cleaner", "AI suggestions for safe process stopping", self.open_speed_page, "#D97706", "#B45309")
        home_btn(cards, SETTINGS_ICON, "LLM Settings", "Endpoint, text model, and vision model", self.open_lmstudio_page, "#7C3AED", "#6D28D9")

    def _build_results_panel(self, parent):
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkScrollableFrame(parent, width=390, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")

        main = ctk.CTkFrame(parent, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        return sidebar, main

    def _build_main_results_area(self, main, title_text: str, op_name: str) -> None:
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(main)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=18)
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text=title_text, font=ctk.CTkFont(size=26, weight="bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        ctk.CTkLabel(
            header,
            text="Preview before applying.",
            text_color="#D1D5DB",
            font=ctk.CTkFont(size=15),
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 14))

        stats = ctk.CTkFrame(main, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", padx=18)
        stats.grid_columnconfigure((0, 1, 2), weight=1)

        total_var = tk.StringVar(value="0")
        ready_var = tk.StringVar(value="0")
        errors_var = tk.StringVar(value="0")
        status_var = tk.StringVar(value="Ready")

        self._stat_card(stats, 0, "Planned files", total_var)
        self._stat_card(stats, 1, "Ready targets", ready_var)
        self._stat_card(stats, 2, "Errors", errors_var)

        center_scroll = ctk.CTkScrollableFrame(main, corner_radius=12, fg_color=("#0F172A", "#0F172A"))
        center_scroll.grid(row=2, column=0, sticky="nsew", padx=18, pady=18)
        center_scroll.grid_columnconfigure(0, weight=1)

        plan_card = ctk.CTkFrame(center_scroll, fg_color=("#111827", "#111827"))
        plan_card.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 12))
        plan_card.grid_columnconfigure(0, weight=1)
        plan_card.grid_rowconfigure(2 if op_name == "Clear Cpu" else 1, weight=1)

        toolbar = ctk.CTkFrame(plan_card, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 0))
        toolbar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(toolbar, text="Plan", font=ctk.CTkFont(size=18, weight="bold"), text_color="#F9FAFB").grid(row=0, column=0, sticky="w")

        filter_entry = ctk.CTkEntry(
            toolbar,
            placeholder_text="Filter by file, category, title, target...",
            height=38,
            width=360,
            fg_color=("#0B1220", "#0B1220"),
            text_color=("#F9FAFB", "#F9FAFB"),
            placeholder_text_color="#9CA3AF",
            font=ctk.CTkFont(size=14),
        )
        filter_entry.grid(row=0, column=1, sticky="e")
        filter_entry.bind("<KeyRelease>", lambda _e, op=op_name: self.refresh_tree(op))

        if op_name == "Clear Cpu":
            select_buttons = ctk.CTkFrame(plan_card, fg_color="transparent")
            select_buttons.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 4))
            ctk.CTkButton(
                select_buttons,
                text="Select all visible",
                height=34,
                fg_color="#374151",
                hover_color="#4B5563",
                command=lambda op=op_name: self.select_all_visible_rows(op),
            ).pack(side="left")
            ctk.CTkButton(
                select_buttons,
                text="Clear selection",
                height=34,
                fg_color="#1F2937",
                hover_color="#374151",
                command=lambda op=op_name: self.clear_tree_selection(op),
            ).pack(side="left", padx=(8, 0))
            ctk.CTkLabel(
                select_buttons,
                text="Select one or more processes before Apply.",
                text_color="#D1D5DB",
                font=ctk.CTkFont(size=13),
            ).pack(side="left", padx=(12, 0))

        tree_wrap = ctk.CTkFrame(plan_card, fg_color=("#0B1220", "#0B1220"))
        tree_row = 2 if op_name == "Clear Cpu" else 1
        tree_wrap.grid(row=tree_row, column=0, sticky="ew", padx=12, pady=12)
        tree_wrap.grid_rowconfigure(0, weight=1)
        tree_wrap.grid_columnconfigure(0, weight=1)

        columns = ("file", "category", "title", "kind", "action", "target")
        tree_selectmode = "extended" if op_name == "Clear Cpu" else "browse"
        tree = ttk.Treeview(tree_wrap, columns=columns, show="headings", selectmode=tree_selectmode, height=14)

        for name, width, anchor in [
            ("file", 230, "w"),
            ("category", 140, "w"),
            ("title", 260, "w"),
            ("kind", 100, "center"),
            ("action", 130, "center"),
            ("target", 700, "w"),
        ]:
            tree.heading(name, text=name.title())
            tree.column(name, width=width, anchor=anchor, stretch=True)

        tree.grid(row=0, column=0, sticky="nsew")
        tree.bind("<<TreeviewSelect>>", lambda _e, op=op_name: self.on_tree_select(op))

        yscroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")

        xscroll = ttk.Scrollbar(tree_wrap, orient="horizontal", command=tree.xview)
        xscroll.grid(row=1, column=0, sticky="ew")

        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self._style_treeview()
        self._bind_mousewheel_to_widget(tree)

        details_card = ctk.CTkFrame(center_scroll, fg_color=("#111827", "#111827"))
        details_card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 12))
        details_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(details_card, text="Details", font=ctk.CTkFont(size=18, weight="bold"), text_color="#F9FAFB").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 8))

        details_box = ctk.CTkTextbox(
            details_card,
            height=300,
            wrap="word",
            fg_color=("#0B1220", "#0B1220"),
            text_color=("#F9FAFB", "#F9FAFB"),
            font=ctk.CTkFont(size=15),
        )
        details_box.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))

        ctk.CTkLabel(
            details_card,
            text="Raw Model Output",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#E5E7EB",
        ).grid(row=2, column=0, sticky="w", padx=12, pady=(0, 8))

        raw_box = ctk.CTkTextbox(
            details_card,
            height=240,
            wrap="word",
            fg_color=("#0B1220", "#0B1220"),
            text_color=("#F9FAFB", "#F9FAFB"),
            font=ctk.CTkFont(size=15),
        )
        raw_box.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))

        self._bind_mousewheel_to_widget(details_box._textbox)
        self._bind_mousewheel_to_widget(raw_box._textbox)

        hidden_log_box = self._make_hidden_box(main)

        footer = ctk.CTkFrame(main)
        footer.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 18))
        footer.grid_columnconfigure(0, weight=1)

        progress = ctk.CTkProgressBar(footer)
        progress.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        progress.set(0)

        ctk.CTkLabel(
            footer,
            textvariable=status_var,
            anchor="w",
            text_color="#E5E7EB",
            font=ctk.CTkFont(size=14),
        ).grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 14))

        self.page_ui[op_name] = {
            "tree": tree,
            "details_box": details_box,
            "raw_box": raw_box,
            "log_box": hidden_log_box,
            "progress": progress,
            "tabs": None,
            "total_var": total_var,
            "ready_var": ready_var,
            "errors_var": errors_var,
            "status_var": status_var,
            "filter_entry": filter_entry,
            "center_scroll": center_scroll,
        }

    def _build_organize_page(self) -> None:
        sidebar, main = self._build_results_panel(self.organize_page)

        hero = ctk.CTkFrame(sidebar)
        hero.pack(fill="x", padx=12, pady=12)

        ctk.CTkButton(hero, text=f"{BACK_ICON} Back", width=100, command=lambda: self.show_page("home")).pack(anchor="w", padx=12, pady=(12, 8))
        ctk.CTkLabel(hero, text=f"{ORGANIZE_ICON} Organize Files", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", padx=12, pady=(0, 4))
        ctk.CTkLabel(hero, text="Sort files by title, category, and file type.", text_color="gray75").pack(anchor="w", padx=12, pady=(0, 12))

        self._section_label(sidebar, "Workspace")
        folder_wrap = ctk.CTkFrame(sidebar)
        folder_wrap.pack(fill="x", padx=14, pady=6)
        ctk.CTkLabel(folder_wrap, text="Folder").pack(anchor="w")
        row = ctk.CTkFrame(folder_wrap, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        ctk.CTkEntry(row, textvariable=self.folder_var, height=34).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Browse", width=90, command=self.pick_folder).pack(side="left", padx=(8, 0))

        self._section_label(sidebar, "Processing")
        grid = ctk.CTkFrame(sidebar)
        grid.pack(fill="x", padx=14, pady=6)
        grid.grid_columnconfigure((0, 1), weight=1)

        self._grid_entry(grid, 0, 0, "Max files", self.max_files_var)
        self._grid_entry(grid, 0, 1, "Max chars", self.max_chars_var)
        self._grid_entry(grid, 1, 0, "Timeout (s)", self.timeout_var)
        self._grid_entry(grid, 1, 1, "Max file MB", self.max_file_size_var)
        self._grid_entry(grid, 2, 0, "Max image MB", self.max_image_mb_var)

        ctk.CTkCheckBox(sidebar, text="Recursive scan", variable=self.recursive_var).pack(anchor="w", padx=18, pady=(10, 0))
        ctk.CTkCheckBox(sidebar, text="Rename files using extracted title", variable=self.rename_files_var).pack(anchor="w", padx=18, pady=(8, 0))
        ctk.CTkCheckBox(sidebar, text="Use vision model for images", variable=self.use_vision_var).pack(anchor="w", padx=18, pady=(8, 0))
        ctk.CTkCheckBox(sidebar, text="Skip hidden files/folders", variable=self.skip_hidden_var).pack(anchor="w", padx=18, pady=(8, 0))
        ctk.CTkCheckBox(sidebar, text="Ignore already organized folders", variable=self.ignore_organized_var).pack(anchor="w", padx=18, pady=(8, 0))

        action_wrap = ctk.CTkFrame(sidebar)
        action_wrap.pack(fill="x", padx=14, pady=(12, 6))
        ctk.CTkLabel(action_wrap, text="Action").pack(anchor="w", padx=10, pady=(10, 4))
        ctk.CTkSegmentedButton(action_wrap, values=["move", "copy"], variable=self.action_var).pack(fill="x", padx=10, pady=(0, 10))

        self._section_label(sidebar, "Run")
        run_wrap = ctk.CTkFrame(sidebar)
        run_wrap.pack(fill="x", padx=14, pady=(6, 18))
        ctk.CTkButton(run_wrap, text="Preview Organize", height=42, command=lambda: self.start_preview("Organize Files")).pack(fill="x", padx=10, pady=(10, 8))
        ctk.CTkButton(run_wrap, text="Apply", height=42, command=lambda: self.start_apply("Organize Files")).pack(fill="x", padx=10, pady=8)
        ctk.CTkButton(run_wrap, text="Stop", height=40, fg_color="#7f1d1d", hover_color="#991b1b", command=self.stop_worker).pack(fill="x", padx=10, pady=(8, 12))

        self._build_main_results_area(main, "Organize Files", "Organize Files")

    def _build_translate_page(self) -> None:
        sidebar, main = self._build_results_panel(self.translate_page)

        hero = ctk.CTkFrame(sidebar)
        hero.pack(fill="x", padx=12, pady=12)

        ctk.CTkButton(hero, text=f"{BACK_ICON} Back", width=100, command=lambda: self.show_page("home")).pack(anchor="w", padx=12, pady=(12, 8))
        ctk.CTkLabel(hero, text=f"{TRANSLATE_ICON} Translate Filenames", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", padx=12, pady=(0, 4))
        ctk.CTkLabel(hero, text="Translate file names only, without scanning content.", text_color="gray75").pack(anchor="w", padx=12, pady=(0, 12))

        self._section_label(sidebar, "Workspace")
        folder_wrap = ctk.CTkFrame(sidebar)
        folder_wrap.pack(fill="x", padx=14, pady=6)
        ctk.CTkLabel(folder_wrap, text="Folder").pack(anchor="w")
        row = ctk.CTkFrame(folder_wrap, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        ctk.CTkEntry(row, textvariable=self.folder_var, height=34).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Browse", width=90, command=self.pick_folder).pack(side="left", padx=(8, 0))

        self._section_label(sidebar, "Translate")
        language_wrap = ctk.CTkFrame(sidebar)
        language_wrap.pack(fill="x", padx=14, pady=6)
        ctk.CTkLabel(language_wrap, text="Target language").pack(anchor="w", padx=10, pady=(10, 4))
        lang_menu = ctk.CTkOptionMenu(
            language_wrap,
            values=["Arabic", "French", "English", "Spanish", "German", "Turkish", "Italian", "Custom"],
            variable=self.target_language_var,
            command=self.on_language_change,
        )
        lang_menu.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkEntry(language_wrap, textvariable=self.target_language_var, height=34, placeholder_text="Example: French").pack(fill="x", padx=10, pady=(0, 10))

        self._section_label(sidebar, "Processing")
        grid = ctk.CTkFrame(sidebar)
        grid.pack(fill="x", padx=14, pady=6)
        grid.grid_columnconfigure((0, 1), weight=1)
        self._grid_entry(grid, 0, 0, "Max files", self.max_files_var)
        self._grid_entry(grid, 0, 1, "Timeout (s)", self.timeout_var)

        ctk.CTkCheckBox(sidebar, text="Recursive scan", variable=self.recursive_var).pack(anchor="w", padx=18, pady=(10, 0))
        ctk.CTkCheckBox(sidebar, text="Skip hidden files/folders", variable=self.skip_hidden_var).pack(anchor="w", padx=18, pady=(8, 0))
        ctk.CTkCheckBox(sidebar, text="Ignore already organized folders", variable=self.ignore_organized_var).pack(anchor="w", padx=18, pady=(8, 0))

        self._section_label(sidebar, "Run")
        run_wrap = ctk.CTkFrame(sidebar)
        run_wrap.pack(fill="x", padx=14, pady=(6, 18))
        ctk.CTkButton(run_wrap, text="Preview Translate", height=42, command=lambda: self.start_preview("Translate Filenames")).pack(fill="x", padx=10, pady=(10, 8))
        ctk.CTkButton(run_wrap, text="Apply", height=42, command=lambda: self.start_apply("Translate Filenames")).pack(fill="x", padx=10, pady=8)
        ctk.CTkButton(run_wrap, text="Stop", height=40, fg_color="#7f1d1d", hover_color="#991b1b", command=self.stop_worker).pack(fill="x", padx=10, pady=(8, 12))

        self._build_main_results_area(main, "Translate Filenames", "Translate Filenames")

    def _build_speed_page(self) -> None:
        sidebar, main = self._build_results_panel(self.speed_page)

        hero = ctk.CTkFrame(sidebar, fg_color=("#1f6aa5", "#14375e"))
        hero.pack(fill="x", padx=12, pady=12)

        ctk.CTkButton(
            hero,
            text=f"{BACK_ICON} Back",
            width=100,
            fg_color=("#0f4c81", "#0f4c81"),
            hover_color=("#0c3d68", "#0c3d68"),
            command=lambda: self.show_page("home"),
        ).pack(anchor="w", padx=12, pady=(12, 8))

        ctk.CTkLabel(
            hero,
            text=f"{SPEED_ICON} Clear Cpu",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color="white",
        ).pack(anchor="w", padx=12, pady=(0, 4))

        ctk.CTkLabel(
            hero,
            text="Use AI to choose safe non-system processes to stop for better performance.",
            text_color="#E5F3FF",
        ).pack(anchor="w", padx=12, pady=(0, 12))

        monitor = ctk.CTkFrame(sidebar, fg_color=("#0f172a", "#111827"))
        monitor.pack(fill="x", padx=14, pady=8)

        ctk.CTkLabel(
            monitor,
            text="Live System Monitor",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=("#60A5FA", "#60A5FA"),
        ).pack(anchor="w", padx=12, pady=(12, 8))

        ctk.CTkLabel(monitor, textvariable=self.cpu_live_var).pack(anchor="w", padx=12)
        self.cpu_bar = ctk.CTkProgressBar(monitor, progress_color="#3B82F6")
        self.cpu_bar.pack(fill="x", padx=12, pady=(6, 10))
        self.cpu_bar.set(0)

        ctk.CTkLabel(monitor, textvariable=self.ram_live_var).pack(anchor="w", padx=12)
        self.ram_bar = ctk.CTkProgressBar(monitor, progress_color="#10B981")
        self.ram_bar.pack(fill="x", padx=12, pady=(6, 12))
        self.ram_bar.set(0)

        info = ctk.CTkFrame(sidebar, fg_color=("#1e293b", "#1f2937"))
        

    

        self._section_label(sidebar, "Settings")
        grid = ctk.CTkFrame(sidebar, fg_color=("#111827", "#111827"))
        grid.pack(fill="x", padx=14, pady=6)
        grid.grid_columnconfigure((0, 1), weight=1)

        self._grid_entry(grid, 0, 0, "Timeout (s)", self.timeout_var)

        model_info = ctk.CTkFrame(grid, fg_color="transparent")
        model_info.grid(row=0, column=1, sticky="ew", padx=8, pady=8)
        ctk.CTkLabel(model_info, text="Text model").pack(anchor="w")
        ctk.CTkLabel(
            model_info,
            textvariable=self.text_model_var,
            anchor="w",
            fg_color=("#1f2937", "#111827"),
            corner_radius=8,
            padx=10,
            pady=8,
        ).pack(fill="x", pady=(6, 0))

        self._section_label(sidebar, "Run")
        run_wrap = ctk.CTkFrame(sidebar, fg_color=("#111827", "#111827"))
        run_wrap.pack(fill="x", padx=14, pady=(6, 18))

        ctk.CTkButton(
            run_wrap,
            text="Preview Best Processes to Stop",
            height=44,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            command=lambda: self.start_preview("Clear Cpu"),
        ).pack(fill="x", padx=10, pady=(10, 8))

        ctk.CTkButton(
            run_wrap,
            text="Apply Selected",
            height=44,
            fg_color="#10B981",
            hover_color="#059669",
            command=lambda: self.start_apply("Clear Cpu"),
        ).pack(fill="x", padx=10, pady=8)

        
        ctk.CTkButton(
            run_wrap,
            text="Stop",
            height=40,
            fg_color="#DC2626",
            hover_color="#B91C1C",
            command=self.stop_worker,
        ).pack(fill="x", padx=10, pady=(8, 12))

        self._build_main_results_area(main, "Clear Cpu", "Clear Cpu")

    def _build_text_tools_page(self) -> None:
        clipboard_build_text_tools_page(self)

    def _build_lmstudio_page(self) -> None:
        page = self.lmstudio_page
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)

        wrap = ctk.CTkScrollableFrame(page, corner_radius=0)
        wrap.grid(row=0, column=0, sticky="nsew")
        wrap.grid_columnconfigure(0, weight=1)

        hero = ctk.CTkFrame(wrap)
        hero.grid(row=0, column=0, sticky="ew", padx=20, pady=20)
        hero.grid_columnconfigure(0, weight=1)

        ctk.CTkButton(hero, text=f"{BACK_ICON} Back", width=100, command=lambda: self.show_page("home")).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 8))
        ctk.CTkLabel(hero, text=f"{SETTINGS_ICON} LM Studio Settings", font=ctk.CTkFont(size=28, weight="bold")).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 4))
        ctk.CTkLabel(hero, text="Manage endpoint and models here.", text_color="gray75").grid(row=2, column=0, sticky="w", padx=14, pady=(0, 14))

        body = ctk.CTkFrame(wrap)
        body.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 20))
        body.grid_columnconfigure(0, weight=1)

        self._labeled_entry(body, "Endpoint", self.endpoint_var)
        self._labeled_entry(body, "Text model", self.text_model_var, "mistralai/ministral-3-3b")
        self._labeled_entry(body, "Vision model", self.vision_model_var, "moondream2-llamafile")

        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=10)
        ctk.CTkButton(btns, text="Load Models", command=self.load_models).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(btns, text="Auto Detect", command=self.auto_pick_models).pack(side="left", fill="x", expand=True, padx=(8, 0))
        ctk.CTkButton(btns, text="Test API", command=self.test_api).pack(side="left", fill="x", expand=True, padx=(8, 0))

    def on_language_change(self, value: str) -> None:
        if value != "Custom":
            self.target_language_var.set(value)

    def on_text_tool_operation_change(self, _value: str) -> None:
        update_text_tool_operation_ui(self, _value)

    def _set_text_tool_busy(self, busy: bool) -> None:
        clipboard_set_text_tool_busy(self, busy)

    def process_text_tools(self) -> None:
        process_text_tools_request(self)

    def _run_text_tool_request(self, endpoint: str, model: str, timeout: int, text: str, operation: str, target_language: str) -> None:
        clipboard_run_text_tool_request(self, endpoint, model, timeout, text, operation, target_language)

    def _finish_text_tool_request(self, result: str, operation: str, error: str) -> None:
        clipboard_finish_text_tool_request(self, result, operation, error)

    def copy_text_tool_result(self) -> None:
        clipboard_copy_text_tool_result(self)

    def clear_text_tools(self) -> None:
        clipboard_clear_text_tools(self)

    def swap_text_tools(self) -> None:
        clipboard_swap_text_tools(self)

    def refresh_text_tool_clipboard(self) -> None:
        self.text_tool_clipboard_last_value = refresh_clipboard_preview(self)

    def use_clipboard_for_text_tool(self) -> None:
        clipboard_use_clipboard_for_text_tool(self)

    def open_text_tool_clipboard_window(self) -> None:
        clipboard_open_text_tool_clipboard_window(self)

    def pick_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.home()))
        if folder:
            self.folder_var.set(folder)

    def emit(self, kind: str, data: Any, op: Optional[str] = None) -> None:
        target_op = op or self.active_operation or self.operation_var.get()
        self.queue.put((kind, {"op": target_op, "payload": data}))

    def _emit_thread_error(self, exc: Exception, op: Optional[str] = None) -> None:
        msg = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.emit("worker_error", msg, op=op)

    def _current_ui(self, op: Optional[str] = None) -> Dict[str, Any]:
        op_name = op or self.active_operation or self.operation_var.get()
        return self.page_ui[op_name]

    def reset_preview(self, op: str) -> None:
        self.decisions_by_op[op] = []
        self.detail_map_by_op[op] = {}

        ui = self._current_ui(op)
        for item in ui["tree"].get_children():
            ui["tree"].delete(item)

        ui["details_box"].delete("1.0", "end")
        ui["raw_box"].delete("1.0", "end")
        ui["log_box"].delete("1.0", "end")

        ui["progress"].set(0)
        ui["total_var"].set("0")
        ui["ready_var"].set("0")
        ui["errors_var"].set("0")
        ui["status_var"].set("Ready")

    def process_queue(self) -> None:
        try:
            while True:
                kind, packet = self.queue.get_nowait()
                op = packet.get("op") or self.operation_var.get()
                data = packet.get("payload")
                ui = self._current_ui(op)

                if kind == "log":
                    ui["log_box"].insert("end", str(data) + "\n")

                elif kind == "progress":
                    current = data["current"]
                    total = max(1, data["total"])
                    ui["progress"].set(current / total)
                    ui["status_var"].set(f"{current}/{total} • {data.get('file', '')}")

                elif kind == "row":
                    self.decisions_by_op[op].append(data)
                    self.refresh_tree(op)
                    ui["total_var"].set(str(len(self.decisions_by_op[op])))
                    ui["ready_var"].set(str(sum(1 for d in self.decisions_by_op[op] if d.target)))

                elif kind == "done":
                    returned_decisions = data.get("decisions", [])
                    errors = data.get("errors", [])

                    if returned_decisions and not self.decisions_by_op[op]:
                        self.decisions_by_op[op] = returned_decisions
                        self.refresh_tree(op)

                    ui["errors_var"].set(str(len(errors)))
                    ui["progress"].set(1 if self.decisions_by_op[op] or errors else 0)
                    ui["status_var"].set(f"Preview ready • {len(self.decisions_by_op[op])} item(s)")

                    if self.active_operation == op:
                        self.worker = None
                        self.active_operation = None

                    if errors:
                        messagebox.showwarning(APP_NAME, "Preview finished with some errors.")

                elif kind == "applied":
                    count = data.get("count", 0)
                    errors = data.get("errors", [])
                    ui["errors_var"].set(str(len(errors)))
                    ui["progress"].set(1)
                    ui["status_var"].set(f"Apply finished • {count} item(s) processed")

                    if self.active_operation == op:
                        self.worker = None
                        self.active_operation = None

                    if errors:
                        messagebox.showwarning(APP_NAME, f"Done. {count} item(s) processed.\nErrors: {len(errors)}")
                    else:
                        messagebox.showinfo(APP_NAME, f"Done. {count} item(s) processed.")

                elif kind == "worker_error":
                    ui["status_var"].set("Worker crashed")
                    try:
                        current_errors = int(ui["errors_var"].get() or "0")
                    except Exception:
                        current_errors = 0
                    ui["errors_var"].set(str(current_errors + 1))
                    ui["progress"].set(0)

                    if self.active_operation == op:
                        self.worker = None
                        self.active_operation = None

                    messagebox.showerror(APP_NAME, f"Worker error:\n\n{data}")

        except queue.Empty:
            pass

        self.after(120, self.process_queue)

    def _update_system_monitor(self) -> None:
        try:
            if psutil is None:
                self.cpu_live_var.set("CPU: psutil not installed")
                self.ram_live_var.set("RAM: psutil not installed")
                if hasattr(self, "cpu_bar"):
                    self.cpu_bar.set(0)
                if hasattr(self, "ram_bar"):
                    self.ram_bar.set(0)
            else:
                cpu = float(psutil.cpu_percent(interval=None))
                ram = float(psutil.virtual_memory().percent)

                self.cpu_live_var.set(f"CPU: {cpu:.1f} %")
                self.ram_live_var.set(f"RAM: {ram:.1f} %")

                if hasattr(self, "cpu_bar"):
                    self.cpu_bar.set(cpu / 100.0)
                if hasattr(self, "ram_bar"):
                    self.ram_bar.set(ram / 100.0)
        except Exception:
            pass

        self.after(1000, self._update_system_monitor)

    def get_config(self) -> Dict[str, Any]:
        folder = self.folder_var.get().strip() or str(Path.home())
        operation = self.operation_var.get().strip() or "Organize Files"
        endpoint = self.endpoint_var.get().strip()
        text_model = self.text_model_var.get().strip()

        if operation in {"Organize Files", "Translate Filenames", "Clear Cpu"}:
            if not endpoint:
                raise ValueError("Endpoint is required.")
            if not text_model:
                raise ValueError("Text model is required.")

        max_files_value = int(self.max_files_var.get().strip() or DEFAULT_MAX_FILES)

        if operation == "Clear Cpu":
            max_processes_value = SPEED_PROCESS_LIMIT
        else:
            max_processes_value = 30

        return {
            "folder": folder,
            "endpoint": endpoint,
            "text_model": text_model,
            "vision_model": self.vision_model_var.get().strip(),
            "timeout": int(self.timeout_var.get().strip() or DEFAULT_TIMEOUT),
            "max_files": max_files_value,
            "max_processes": max_processes_value,
            "max_chars": int(self.max_chars_var.get().strip() or DEFAULT_MAX_CHARS),
            "max_file_size_mb": int(self.max_file_size_var.get().strip() or DEFAULT_MAX_FILE_MB),
            "max_image_mb": int(self.max_image_mb_var.get().strip() or DEFAULT_MAX_IMAGE_MB),
            "recursive": bool(self.recursive_var.get()),
            "rename_files": bool(self.rename_files_var.get()),
            "use_vision": bool(self.use_vision_var.get()),
            "skip_hidden": bool(self.skip_hidden_var.get()),
            "ignore_organized_folders": bool(self.ignore_organized_var.get()),
            "action": self.action_var.get().strip() or "move",
            "operation": operation,
            "target_language": self.target_language_var.get().strip(),
        }

    def ensure_models_for_operation(self, operation: str) -> bool:
        endpoint = self.endpoint_var.get().strip()
        text_model = self.text_model_var.get().strip()
        vision_model = self.vision_model_var.get().strip()

        need_text = operation in {"Organize Files", "Translate Filenames", "Clear Cpu", "Text Tools"}
        need_vision = operation == "Organize Files" and bool(self.use_vision_var.get())

        missing = []

        if not endpoint:
            missing.append("Endpoint")
        if need_text and not text_model:
            missing.append("Text model")
        if need_vision and not vision_model:
            missing.append("Vision model")

        if missing:
            messagebox.showwarning(APP_NAME, "Please configure LM Studio first:\n\n- " + "\n- ".join(missing))
            self.show_page("lmstudio")
            return False

        return True

    def start_preview(self, op: str) -> None:
        self.operation_var.set(op)

        if self.text_tool_busy:
            messagebox.showwarning(APP_NAME, "Wait for the running Text Tools request to finish first.")
            return

        if not self.ensure_models_for_operation(op):
            return

        try:
            self.get_config()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            if "model" in str(exc).lower() or "endpoint" in str(exc).lower():
                self.show_page("lmstudio")
            return

        if self.worker is not None:
            messagebox.showwarning(APP_NAME, "Another job is already running.")
            return

        self.active_operation = op
        self.reset_preview(op)

        ui = self._current_ui(op)
        ui["status_var"].set("Scanning...")

        worker = Worker(self, operation_name=op)
        self.worker = worker
        threading.Thread(target=self._run_worker_safe, args=(worker.preview, op), daemon=True).start()

    def get_selected_decisions(self, op: str) -> List[Decision]:
        ui = self._current_ui(op)
        selected: List[Decision] = []
        for iid in ui["tree"].selection():
            dec = self.detail_map_by_op.get(op, {}).get(iid)
            if dec is not None:
                selected.append(dec)
        return selected

    def select_all_visible_rows(self, op: str) -> None:
        ui = self._current_ui(op)
        rows = ui["tree"].get_children()
        if not rows:
            return
        ui["tree"].selection_set(rows)
        ui["tree"].focus(rows[0])
        self.on_tree_select(op)

    def clear_tree_selection(self, op: str) -> None:
        ui = self._current_ui(op)
        for iid in ui["tree"].selection():
            ui["tree"].selection_remove(iid)
        ui["details_box"].delete("1.0", "end")
        ui["raw_box"].delete("1.0", "end")

    def start_apply(self, op: str) -> None:
        self.operation_var.set(op)

        if self.text_tool_busy:
            messagebox.showwarning(APP_NAME, "Wait for the running Text Tools request to finish first.")
            return

        if self.worker is not None:
            messagebox.showwarning(APP_NAME, "Wait for the running job to finish first.")
            return

        if not self.decisions_by_op[op]:
            messagebox.showwarning(APP_NAME, "Run Preview first.")
            return

        if op == "Clear Cpu":
            decisions = self.get_selected_decisions(op)
            if not decisions:
                messagebox.showwarning(APP_NAME, "Select one or more processes from the list first.")
                return
            confirm_text = (
                f"Apply '{op}' for {len(decisions)} selected process(es)?\n\n"
                "This will stop only the selected suggested non-system processes."
            )
        else:
            decisions = list(self.decisions_by_op[op])
            confirm_text = f"Apply '{op}' for {len(decisions)} item(s)?"

        if not messagebox.askyesno(APP_NAME, confirm_text):
            return

        self.active_operation = op
        ui = self._current_ui(op)
        ui["progress"].set(0)
        ui["status_var"].set("Applying changes...")

        worker = Worker(self, operation_name=op)
        self.worker = worker
        threading.Thread(
            target=self._run_worker_safe,
            args=(lambda w=worker, d=decisions: w.apply(d), op),
            daemon=True,
        ).start()

    def stop_worker(self) -> None:
        if self.worker:
            self.worker.stop()
        else:
            messagebox.showinfo(APP_NAME, "No running worker.")

    def _run_worker_safe(self, fn, op: Optional[str] = None) -> None:
        try:
            fn()
        except Exception as exc:
            self._emit_thread_error(exc, op=op)
            self.worker = None
            if self.active_operation == op:
                self.active_operation = None

    def refresh_tree(self, op: str) -> None:
        ui = self._current_ui(op)
        query = ui["filter_entry"].get().strip().lower()

        selected_old = ui["tree"].selection()
        selected_values = None
        if selected_old:
            try:
                selected_values = ui["tree"].item(selected_old[0], "values")
            except Exception:
                selected_values = None

        for item in ui["tree"].get_children():
            ui["tree"].delete(item)

        self.detail_map_by_op[op] = {}
        first_iid = None
        matched_iid = None

        for dec in self.decisions_by_op[op]:
            hay = " ".join([
                dec.source.name,
                dec.category,
                dec.title,
                str(dec.target),
                dec.kind,
                dec.summary,
                dec.action,
                dec.process_name,
                dec.process_username,
            ]).lower()

            if query and query not in hay:
                continue

            iid = ui["tree"].insert(
                "",
                "end",
                values=(
                    dec.source.name,
                    dec.category,
                    dec.title,
                    dec.kind,
                    dec.action,
                    str(dec.target),
                ),
            )
            self.detail_map_by_op[op][iid] = dec

            if first_iid is None:
                first_iid = iid

            if selected_values and tuple(selected_values) == (
                dec.source.name,
                dec.category,
                dec.title,
                dec.kind,
                dec.action,
                str(dec.target),
            ):
                matched_iid = iid

        target_iid = matched_iid or first_iid

        if target_iid:
            try:
                ui["tree"].selection_set(target_iid)
                ui["tree"].focus(target_iid)
                self.on_tree_select(op)
            except Exception:
                pass
        else:
            ui["details_box"].delete("1.0", "end")
            ui["raw_box"].delete("1.0", "end")

        ui["total_var"].set(str(len(self.decisions_by_op[op])))
        ui["ready_var"].set(str(sum(1 for d in self.decisions_by_op[op] if d.target)))



    def prefer_known_text_model(self, models: List[str]) -> None:
        preferred_text_models = [
            "mistralai/ministral-3-3b",
            "liquid/lfm2.5-1.2b",

        ]

        for preferred in preferred_text_models:
            found = next((m for m in models if m.strip().lower() == preferred.lower()), "")
            if found:
                self.text_model_var.set(found)
                return

    def load_models(self) -> None:
        endpoint = self.endpoint_var.get().strip()
        if not endpoint:
            messagebox.showerror(APP_NAME, "Endpoint is required.")
            return

        try:
            client = LLMClient(endpoint)
            models = client.list_models()
            if not models:
                raise RuntimeError("No models returned")
            self.prefer_known_text_model(models)
            self._show_model_picker(models)
            messagebox.showinfo(APP_NAME, f"Loaded {len(models)} model(s).")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not load models:\n{exc}")

    def auto_pick_models(self) -> None:
        endpoint = self.endpoint_var.get().strip()
        if not endpoint:
            messagebox.showerror(APP_NAME, "Endpoint is required.")
            return

        try:
            models = LLMClient(endpoint).list_models()
            if not models:
                raise RuntimeError("No models returned")

            preferred_text_models = [
                "mistralai/ministral-3-3b",
                "ministral-3-3b",
                "qwen2.5-14b-instruct-1m",
                "phi-3-mini-4k-instruct",
            ]

            text_candidate = ""
            for preferred in preferred_text_models:
                text_candidate = next((m for m in models if m.strip().lower() == preferred.lower()), "")
                if text_candidate:
                    break

            if not text_candidate:
                text_candidate = next((m for m in models if likely_text_model_name(m)), models[0])

            preferred_vision_models = [
                "moondream2-llamafile",
                "qwen2.5-vl",
                "qwen2-vl",
                "llava",
                "minicpm-v",
            ]

            vision_candidate = ""
            for preferred in preferred_vision_models:
                vision_candidate = next((m for m in models if preferred.lower() in m.lower()), "")
                if vision_candidate:
                    break

            if not vision_candidate:
                vision_candidate = next((m for m in models if likely_vision_model_name(m)), "")

            if text_candidate:
                self.text_model_var.set(text_candidate)
            if vision_candidate:
                self.vision_model_var.set(vision_candidate)

            msg = f"Text model: {text_candidate}\nVision model: {vision_candidate or '(none found)'}"
            messagebox.showinfo(APP_NAME, msg)

        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Auto detect failed:\n{exc}")

    def try_auto_setup_models_on_startup(self) -> None:
        endpoint = self.endpoint_var.get().strip()
        if not endpoint:
            return

        try:
            models = LLMClient(endpoint).list_models()
            if not models:
                return

            preferred_text_models = [
                "mistralai/ministral-3-3b",
                "ministral-3-3b",
                "qwen2.5-14b-instruct-1m",
                "phi-3-mini-4k-instruct",
            ]

            preferred_vision_models = [
                "moondream2-llamafile",
                "qwen2.5-vl",
                "qwen2-vl",
                "llava",
                "minicpm-v",
            ]

            text_candidate = ""
            for preferred in preferred_text_models:
                text_candidate = next((m for m in models if m.strip().lower() == preferred.lower()), "")
                if text_candidate:
                    break

            if not text_candidate:
                text_candidate = next((m for m in models if likely_text_model_name(m)), "")

            vision_candidate = ""
            for preferred in preferred_vision_models:
                vision_candidate = next((m for m in models if preferred.lower() in m.lower()), "")
                if vision_candidate:
                    break

            if not vision_candidate:
                vision_candidate = next((m for m in models if likely_vision_model_name(m)), "")

            if text_candidate:
                self.text_model_var.set(text_candidate)
            if vision_candidate:
                self.vision_model_var.set(vision_candidate)

        except Exception:
            pass

    def startup_setup(self) -> None:
        self.try_auto_setup_models_on_startup()
        if not self.endpoint_var.get().strip() or not self.text_model_var.get().strip():
            self.open_lmstudio_page()

    def _show_model_picker(self, models: List[str]) -> None:
        win = ctk.CTkToplevel(self)
        win.title("Pick models")
        win.geometry("760x520")
        win.transient(self)
        win.grab_set()

        ctk.CTkLabel(win, text="Available Models", font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w", padx=18, pady=(18, 8))
        ctk.CTkLabel(win, text="Double-click a model to place it in the text or vision field.", text_color="gray75").pack(anchor="w", padx=18, pady=(0, 12))

        top = ctk.CTkFrame(win)
        top.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        top.grid_columnconfigure((0, 1), weight=1)
        top.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(top, text="Text model target").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        ctk.CTkLabel(top, text="Vision model target").grid(row=0, column=1, sticky="w", padx=12, pady=(12, 6))

        left_frame = ctk.CTkFrame(top)
        left_frame.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 12))
        left_frame.grid_rowconfigure(0, weight=1)
        left_frame.grid_columnconfigure(0, weight=1)

        right_frame = ctk.CTkFrame(top)
        right_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 12))
        right_frame.grid_rowconfigure(0, weight=1)
        right_frame.grid_columnconfigure(0, weight=1)

        left_box = tk.Listbox(left_frame)
        left_box.grid(row=0, column=0, sticky="nsew")
        left_scroll = ttk.Scrollbar(left_frame, orient="vertical", command=left_box.yview)
        left_scroll.grid(row=0, column=1, sticky="ns")
        left_box.configure(yscrollcommand=left_scroll.set)

        right_box = tk.Listbox(right_frame)
        right_box.grid(row=0, column=0, sticky="nsew")
        right_scroll = ttk.Scrollbar(right_frame, orient="vertical", command=right_box.yview)
        right_scroll.grid(row=0, column=1, sticky="ns")
        right_box.configure(yscrollcommand=right_scroll.set)

        for m in models:
            left_box.insert("end", m)
            right_box.insert("end", m)

        def set_text(_e=None):
            sel = left_box.curselection()
            if sel:
                self.text_model_var.set(left_box.get(sel[0]))

        def set_vision(_e=None):
            sel = right_box.curselection()
            if sel:
                self.vision_model_var.set(right_box.get(sel[0]))

        left_box.bind("<Double-Button-1>", set_text)
        right_box.bind("<Double-Button-1>", set_vision)

        foot = ctk.CTkFrame(win, fg_color="transparent")
        foot.pack(fill="x", padx=18, pady=(0, 18))
        ctk.CTkButton(foot, text="Use selected as text model", command=set_text).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(foot, text="Use selected as vision model", command=set_vision).pack(side="left", fill="x", expand=True, padx=(8, 0))

    def test_api(self) -> None:
        endpoint = self.endpoint_var.get().strip()
        text_model = self.text_model_var.get().strip()

        if not endpoint or not text_model:
            messagebox.showwarning(APP_NAME, "Please set Endpoint and Text model first.")
            self.show_page("lmstudio")
            return

        try:
            client = LLMClient(endpoint, timeout=int(self.timeout_var.get().strip() or DEFAULT_TIMEOUT))
            insight = FileInsight(
                path=Path("sample.py"),
                kind="text",
                snippet="Python script that extracts images from PDF files and saves them into an output folder.",
                meta={"name": "sample.py", "size_human": "88 B"},
                extraction_method="manual_test",
            )
            parsed, raw = client.classify_text(insight, text_model)
            messagebox.showinfo(APP_NAME, f"API OK\n\nParsed:\n{json.dumps(parsed, ensure_ascii=False, indent=2)}\n\nRaw:\n{raw}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"API test failed:\n{exc}")


def main() -> None:
    app = LLMHelperApp()
    app.mainloop()


if __name__ == "__main__":
    main()
