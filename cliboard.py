from __future__ import annotations

import threading

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox

from models import APP_NAME, BACK_ICON, DEFAULT_TIMEOUT, TEXT_TOOLS_ICON, LLMClient

CLIPBOARD_HISTORY_LIMIT = 25


def build_text_tools_page(app) -> None:
    page = app.text_tools_page
    page.grid_columnconfigure(0, weight=1)
    page.grid_rowconfigure(0, weight=1)

    wrap = ctk.CTkFrame(page, corner_radius=0)
    wrap.grid(row=0, column=0, sticky="nsew")
    wrap.grid_columnconfigure(0, weight=1)
    wrap.grid_rowconfigure(2, weight=1)

    hero = ctk.CTkFrame(wrap)
    hero.grid(row=0, column=0, sticky="ew", padx=20, pady=20)
    hero.grid_columnconfigure(0, weight=1)

    ctk.CTkButton(hero, text=f"{BACK_ICON} Back", width=100, command=lambda: app.show_page("home")).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 8))
    ctk.CTkLabel(hero, text=f"{TEXT_TOOLS_ICON} Clipboard Manager", font=ctk.CTkFont(size=28, weight="bold")).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 4))
    ctk.CTkLabel(hero, text="Review clipboard text, choose how to load it, then run an AI operation.", text_color="gray75").grid(row=2, column=0, sticky="w", padx=14, pady=(0, 14))

    controls = ctk.CTkFrame(wrap)
    controls.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 16))
    controls.grid_columnconfigure(0, weight=2)
    controls.grid_columnconfigure(1, weight=2)
    controls.grid_columnconfigure((2, 3, 4, 5, 6), weight=1)

    operation_wrap = ctk.CTkFrame(controls, fg_color="transparent")
    operation_wrap.grid(row=0, column=0, sticky="ew", padx=(14, 8), pady=14)
    ctk.CTkLabel(operation_wrap, text="Operation").pack(anchor="w")
    app.text_tool_operation_menu = ctk.CTkOptionMenu(
        operation_wrap,
        values=["Translate", "Correct Grammar", "Summarize"],
        variable=app.text_tool_operation_var,
        command=app.on_text_tool_operation_change,
    )
    app.text_tool_operation_menu.pack(fill="x", pady=(6, 0))

    app.text_tool_language_frame = ctk.CTkFrame(controls, fg_color="transparent")
    app.text_tool_language_frame.grid(row=0, column=1, sticky="ew", padx=8, pady=14)
    ctk.CTkLabel(app.text_tool_language_frame, text="Translate to").pack(anchor="w")
    app.text_tool_language_menu = ctk.CTkOptionMenu(
        app.text_tool_language_frame,
        values=["Arabic", "English", "French"],
        variable=app.text_tool_language_var,
    )
    app.text_tool_language_menu.pack(fill="x", pady=(6, 0))

    app.text_tool_process_button = ctk.CTkButton(controls, text="Process Text", height=40, command=app.process_text_tools)
    app.text_tool_process_button.grid(row=0, column=2, sticky="ew", padx=8, pady=14)

    app.text_tool_copy_button = ctk.CTkButton(controls, text="Copy Result", height=40, fg_color="#059669", hover_color="#047857", command=app.copy_text_tool_result)
    app.text_tool_copy_button.grid(row=0, column=3, sticky="ew", padx=8, pady=14)

    app.text_tool_swap_button = ctk.CTkButton(controls, text="Swap Input/Output", height=40, fg_color="#D97706", hover_color="#B45309", command=app.swap_text_tools)
    app.text_tool_swap_button.grid(row=0, column=4, sticky="ew", padx=8, pady=14)

    app.text_tool_clear_button = ctk.CTkButton(controls, text="Clear All", height=40, fg_color="#7F1D1D", hover_color="#991B1B", command=app.clear_text_tools)
    app.text_tool_clear_button.grid(row=0, column=5, sticky="ew", padx=(8, 14), pady=14)

    app.text_tool_clipboard_window_button = ctk.CTkButton(
        controls,
        text="Clipboard Window",
        height=40,
        fg_color="#4F46E5",
        hover_color="#4338CA",
        command=app.open_text_tool_clipboard_window,
    )
    app.text_tool_clipboard_window_button.grid(row=0, column=6, sticky="ew", padx=(0, 14), pady=14)

    body = ctk.CTkFrame(wrap, fg_color="transparent")
    body.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 12))
    body.grid_columnconfigure((0, 1), weight=1)
    body.grid_rowconfigure(0, weight=1)

    input_card = ctk.CTkFrame(body)
    input_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=0)
    input_card.grid_columnconfigure(0, weight=1)
    input_card.grid_rowconfigure(1, weight=1)

    ctk.CTkLabel(input_card, text="Input Text", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 8))
    app.text_tool_input_box = ctk.CTkTextbox(
        input_card,
        wrap="word",
        fg_color=("#0B1220", "#0B1220"),
        text_color=("#F9FAFB", "#F9FAFB"),
        font=ctk.CTkFont(size=15),
    )
    app.text_tool_input_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

    output_card = ctk.CTkFrame(body)
    output_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0), pady=0)
    output_card.grid_columnconfigure(0, weight=1)
    output_card.grid_rowconfigure(1, weight=1)

    ctk.CTkLabel(output_card, text="Output Result", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 8))
    app.text_tool_output_box = ctk.CTkTextbox(
        output_card,
        wrap="word",
        fg_color=("#0B1220", "#0B1220"),
        text_color=("#F9FAFB", "#F9FAFB"),
        font=ctk.CTkFont(size=15),
    )
    app.text_tool_output_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

    app.text_tool_action_buttons = [
        app.text_tool_process_button,
        app.text_tool_copy_button,
        app.text_tool_swap_button,
        app.text_tool_clear_button,
        app.text_tool_clipboard_window_button,
    ]

    app._bind_mousewheel_to_widget(app.text_tool_input_box._textbox)
    app._bind_mousewheel_to_widget(app.text_tool_output_box._textbox)
    update_text_tool_operation_ui(app, app.text_tool_operation_var.get())
    refresh_clipboard_preview(app, update_status=False)

    status = ctk.CTkFrame(wrap)
    status.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 20))
    ctk.CTkLabel(status, textvariable=app.text_tool_status_var, anchor="w", text_color="#E5E7EB", font=ctk.CTkFont(size=14)).pack(fill="x", padx=14, pady=14)


def update_text_tool_operation_ui(app, _value: str) -> None:
    is_translate = app.text_tool_operation_var.get().strip() == "Translate"

    if hasattr(app, "text_tool_language_frame"):
        if is_translate:
            app.text_tool_language_frame.grid()
        else:
            app.text_tool_language_frame.grid_remove()

    if hasattr(app, "text_tool_language_menu"):
        state = "disabled" if app.text_tool_busy or not is_translate else "normal"
        app.text_tool_language_menu.configure(state=state)


def set_text_tool_busy(app, busy: bool) -> None:
    app.text_tool_busy = busy
    state = "disabled" if busy else "normal"

    if hasattr(app, "text_tool_operation_menu"):
        app.text_tool_operation_menu.configure(state=state)

    for button in getattr(app, "text_tool_action_buttons", []):
        button.configure(state=state)

    window = getattr(app, "text_tool_clipboard_window", None)
    if window is not None and window.winfo_exists():
        _sync_clipboard_window_controls(app)

    update_text_tool_operation_ui(app, app.text_tool_operation_var.get())


def process_text_tools_request(app) -> None:
    if app.worker is not None or app.text_tool_busy:
        messagebox.showwarning(APP_NAME, "Wait for the running job to finish first.")
        return

    if not app.ensure_models_for_operation("Text Tools"):
        return

    text = app._get_textbox_value(app.text_tool_input_box)
    if not text.strip():
        messagebox.showwarning(APP_NAME, "Please enter some text first.")
        return

    operation = app.text_tool_operation_var.get().strip() or "Translate"
    target_language = app.text_tool_language_var.get().strip()
    if operation == "Translate" and not target_language:
        messagebox.showwarning(APP_NAME, "Please choose a target language.")
        return

    endpoint = app.endpoint_var.get().strip()
    text_model = app.text_model_var.get().strip()
    timeout = int(app.timeout_var.get().strip() or DEFAULT_TIMEOUT)

    set_text_tool_busy(app, True)
    app._set_textbox_value(app.text_tool_output_box, "")
    app.text_tool_status_var.set(f"{operation} in progress...")

    threading.Thread(
        target=run_text_tool_request,
        args=(app, endpoint, text_model, timeout, text, operation, target_language),
        daemon=True,
    ).start()


def run_text_tool_request(app, endpoint: str, model: str, timeout: int, text: str, operation: str, target_language: str) -> None:
    try:
        client = LLMClient(endpoint, timeout=timeout)
        result = client.process_text_tool(text, model, operation, target_language)
        app.after(0, lambda: finish_text_tool_request(app, result, operation, ""))
    except Exception as exc:
        app.after(0, lambda: finish_text_tool_request(app, "", operation, str(exc)))


def finish_text_tool_request(app, result: str, operation: str, error: str) -> None:
    set_text_tool_busy(app, False)

    if error:
        app.text_tool_status_var.set("Processing failed.")
        messagebox.showerror(APP_NAME, f"Text Tools failed:\n{error}")
        return

    app._set_textbox_value(app.text_tool_output_box, result)
    if result.strip():
        app.text_tool_status_var.set(f"{operation} completed.")
    else:
        app.text_tool_status_var.set(f"{operation} completed, but no text was returned.")


def copy_text_tool_result(app) -> None:
    result = app._get_textbox_value(app.text_tool_output_box)
    if not result.strip():
        app.text_tool_status_var.set("Nothing to copy.")
        return

    app.clipboard_clear()
    app.clipboard_append(result)
    app.update_idletasks()
    refresh_clipboard_preview(app, update_status=False)
    app.text_tool_status_var.set("Result copied.")


def clear_text_tools(app) -> None:
    if app.text_tool_busy:
        return

    app._set_textbox_value(app.text_tool_input_box, "")
    app._set_textbox_value(app.text_tool_output_box, "")
    app.text_tool_status_var.set("Input and output cleared.")


def swap_text_tools(app) -> None:
    if app.text_tool_busy:
        return

    input_text = app._get_textbox_value(app.text_tool_input_box)
    output_text = app._get_textbox_value(app.text_tool_output_box)
    app._set_textbox_value(app.text_tool_input_box, output_text)
    app._set_textbox_value(app.text_tool_output_box, input_text)
    app.text_tool_status_var.set("Input and output swapped.")


def _get_system_clipboard_text(app) -> str:
    try:
        return app.clipboard_get()
    except tk.TclError:
        return ""


def _remember_clipboard_text(app, value: str) -> None:
    if not value or not value.strip():
        return

    history = [item for item in getattr(app, "text_tool_clipboard_history", []) if item != value]
    history.insert(0, value)
    app.text_tool_clipboard_history = history[:CLIPBOARD_HISTORY_LIMIT]

    selected_text = getattr(app, "text_tool_selected_clipboard_text", "")
    if not selected_text or selected_text not in app.text_tool_clipboard_history:
        app.text_tool_selected_clipboard_text = app.text_tool_clipboard_history[0]


def _summarize_clipboard_text(value: str, limit: int = 70) -> str:
    compact = " ".join((value or "").split())
    if len(compact) <= limit:
        return compact or "(empty)"
    return compact[: limit - 3] + "..."


def _set_clipboard_window_preview(app, value: str) -> None:
    preview_box = getattr(app, "text_tool_clipboard_preview_box", None)
    if preview_box is None:
        return

    preview_box.configure(state="normal")
    app._set_textbox_value(preview_box, value)
    preview_box.configure(state="disabled")


def _refresh_clipboard_window_list(app) -> None:
    window = getattr(app, "text_tool_clipboard_window", None)
    listbox = getattr(app, "text_tool_clipboard_listbox", None)
    if window is None or not window.winfo_exists() or listbox is None:
        return

    history = getattr(app, "text_tool_clipboard_history", [])
    selected_text = getattr(app, "text_tool_selected_clipboard_text", "")

    listbox.delete(0, "end")
    for index, item in enumerate(history):
        prefix = "Current" if index == 0 else f"Item {index + 1}"
        listbox.insert("end", f"{prefix}: {_summarize_clipboard_text(item)}")

    if history:
        if selected_text not in history:
            selected_text = history[0]
            app.text_tool_selected_clipboard_text = selected_text

        selected_index = history.index(selected_text)
        listbox.selection_clear(0, "end")
        listbox.selection_set(selected_index)
        listbox.activate(selected_index)
        listbox.see(selected_index)
        _set_clipboard_window_preview(app, selected_text)
    else:
        app.text_tool_selected_clipboard_text = ""
        _set_clipboard_window_preview(app, "Clipboard history is empty.")


def _sync_clipboard_window_controls(app) -> None:
    window = getattr(app, "text_tool_clipboard_window", None)
    if window is None or not window.winfo_exists():
        return

    state = "disabled" if app.text_tool_busy else "normal"

    mode_control = getattr(app, "text_tool_clipboard_popup_mode_control", None)
    if mode_control is not None:
        mode_control.configure(state=state)

    for attr in (
        "text_tool_clipboard_popup_refresh_button",
        "text_tool_clipboard_popup_use_button",
        "text_tool_clipboard_popup_close_button",
    ):
        button = getattr(app, attr, None)
        if button is not None:
            button.configure(state=state)


def _handle_clipboard_history_select(app, _event=None) -> None:
    listbox = getattr(app, "text_tool_clipboard_listbox", None)
    if listbox is None:
        return

    selection = listbox.curselection()
    history = getattr(app, "text_tool_clipboard_history", [])
    if not selection or not history:
        return

    selected_index = selection[0]
    if 0 <= selected_index < len(history):
        selected_text = history[selected_index]
        app.text_tool_selected_clipboard_text = selected_text
        _set_clipboard_window_preview(app, selected_text)


def _close_clipboard_window(app) -> None:
    window = getattr(app, "text_tool_clipboard_window", None)
    if window is not None and window.winfo_exists():
        window.destroy()

    app.text_tool_clipboard_window = None
    app.text_tool_clipboard_listbox = None
    app.text_tool_clipboard_preview_box = None
    app.text_tool_clipboard_popup_mode_control = None
    app.text_tool_clipboard_popup_refresh_button = None
    app.text_tool_clipboard_popup_use_button = None
    app.text_tool_clipboard_popup_close_button = None


def open_text_tool_clipboard_window(app) -> None:
    existing_window = getattr(app, "text_tool_clipboard_window", None)
    if existing_window is not None and existing_window.winfo_exists():
        existing_window.deiconify()
        existing_window.lift()
        existing_window.focus()
        refresh_clipboard_preview(app, update_status=False)
        _refresh_clipboard_window_list(app)
        return

    refresh_clipboard_preview(app, update_status=False)

    window = ctk.CTkToplevel(app)
    window.title("Clipboard Window")
    window.geometry("1040x700")
    window.minsize(860, 560)
    window.transient(app)
    window.protocol("WM_DELETE_WINDOW", lambda: _close_clipboard_window(app))
    window.grid_columnconfigure(0, weight=1)
    window.grid_rowconfigure(1, weight=1)

    app.text_tool_clipboard_window = window

    header = ctk.CTkFrame(window)
    header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 12))
    header.grid_columnconfigure(0, weight=1)

    ctk.CTkLabel(header, text="Clipboard Content and History", font=ctk.CTkFont(size=24, weight="bold")).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 4))
    ctk.CTkLabel(header, text="Select one clipboard item, preview it on the right, then choose how it should be used in the operation.", text_color="gray75", font=ctk.CTkFont(size=13)).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 16))

    content = ctk.CTkFrame(window, fg_color="transparent")
    content.grid(row=1, column=0, sticky="nsew", padx=18, pady=0)
    content.grid_columnconfigure(0, weight=1)
    content.grid_columnconfigure(1, weight=2)
    content.grid_rowconfigure(0, weight=1)

    history_frame = ctk.CTkFrame(content)
    history_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=0)
    history_frame.grid_rowconfigure(2, weight=1)
    history_frame.grid_columnconfigure(0, weight=1)

    ctk.CTkLabel(history_frame, text="History", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 4))
    ctk.CTkLabel(history_frame, text="Newest clipboard items appear first.", text_color="gray70", font=ctk.CTkFont(size=12)).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 8))

    list_wrap = ctk.CTkFrame(history_frame)
    list_wrap.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
    list_wrap.grid_rowconfigure(0, weight=1)
    list_wrap.grid_columnconfigure(0, weight=1)

    app.text_tool_clipboard_listbox = tk.Listbox(
        list_wrap,
        activestyle="none",
        bg="#0B1220",
        bd=0,
        exportselection=False,
        fg="#F9FAFB",
        font=("Segoe UI", 11),
        highlightthickness=0,
        relief="flat",
        selectbackground="#2563EB",
        selectborderwidth=0,
        selectforeground="#FFFFFF",
    )
    app.text_tool_clipboard_listbox.grid(row=0, column=0, sticky="nsew")

    list_scroll = ctk.CTkScrollbar(list_wrap, orientation="vertical", command=app.text_tool_clipboard_listbox.yview)
    list_scroll.grid(row=0, column=1, sticky="ns")
    app.text_tool_clipboard_listbox.configure(yscrollcommand=list_scroll.set)
    app.text_tool_clipboard_listbox.bind("<<ListboxSelect>>", lambda event: _handle_clipboard_history_select(app, event))
    app.text_tool_clipboard_listbox.bind("<Double-Button-1>", lambda _event: use_clipboard_for_text_tool(app))

    preview_frame = ctk.CTkFrame(content)
    preview_frame.grid(row=0, column=1, sticky="nsew", padx=(10, 0), pady=0)
    preview_frame.grid_rowconfigure(2, weight=1)
    preview_frame.grid_columnconfigure(0, weight=1)

    ctk.CTkLabel(preview_frame, text="Selected Content", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 4))
    ctk.CTkLabel(preview_frame, text="Preview the full clipboard text before inserting it into the input box.", text_color="gray70", font=ctk.CTkFont(size=12)).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 8))

    app.text_tool_clipboard_preview_box = ctk.CTkTextbox(
        preview_frame,
        wrap="word",
        fg_color=("#0B1220", "#0B1220"),
        text_color=("#F9FAFB", "#F9FAFB"),
        font=ctk.CTkFont(size=14),
        border_width=1,
        border_color="#1F2937",
    )
    app.text_tool_clipboard_preview_box.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))

    footer = ctk.CTkFrame(window)
    footer.grid(row=2, column=0, sticky="ew", padx=18, pady=(12, 18))
    footer.grid_columnconfigure((0, 1, 2), weight=1)

    app.text_tool_clipboard_popup_mode_control = ctk.CTkSegmentedButton(
        footer,
        values=["Replace Input", "Append to Input"],
        variable=app.text_tool_clipboard_mode_var,
    )
    app.text_tool_clipboard_popup_mode_control.grid(row=0, column=0, columnspan=3, sticky="ew", padx=14, pady=(14, 10))

    app.text_tool_clipboard_popup_refresh_button = ctk.CTkButton(
        footer,
        text="Refresh Clipboard",
        command=app.refresh_text_tool_clipboard,
    )
    app.text_tool_clipboard_popup_refresh_button.grid(row=1, column=0, sticky="ew", padx=(14, 7), pady=(0, 14))

    app.text_tool_clipboard_popup_use_button = ctk.CTkButton(
        footer,
        text="Use Selected",
        fg_color="#2563EB",
        hover_color="#1D4ED8",
        command=app.use_clipboard_for_text_tool,
    )
    app.text_tool_clipboard_popup_use_button.grid(row=1, column=1, sticky="ew", padx=7, pady=(0, 14))

    app.text_tool_clipboard_popup_close_button = ctk.CTkButton(
        footer,
        text="Close",
        fg_color="#4B5563",
        hover_color="#374151",
        command=lambda: _close_clipboard_window(app),
    )
    app.text_tool_clipboard_popup_close_button.grid(row=1, column=2, sticky="ew", padx=(7, 14), pady=(0, 14))

    app._bind_mousewheel_to_widget(app.text_tool_clipboard_preview_box._textbox)
    _refresh_clipboard_window_list(app)
    _sync_clipboard_window_controls(app)


def refresh_clipboard_preview(app, update_status: bool = True) -> str:
    clipboard_text = _get_system_clipboard_text(app)
    _remember_clipboard_text(app, clipboard_text)
    _refresh_clipboard_window_list(app)

    if update_status:
        if clipboard_text:
            app.text_tool_status_var.set("Clipboard refreshed and added to history.")
        else:
            app.text_tool_status_var.set("Clipboard is empty.")

    return clipboard_text


def _get_selected_clipboard_text(app) -> str:
    selected_text = getattr(app, "text_tool_selected_clipboard_text", "")
    history = getattr(app, "text_tool_clipboard_history", [])

    if selected_text and selected_text in history:
        return selected_text

    if history:
        app.text_tool_selected_clipboard_text = history[0]
        return history[0]

    return ""


def use_clipboard_for_text_tool(app) -> None:
    if app.text_tool_busy:
        return

    clipboard_text = _get_selected_clipboard_text(app)
    if not clipboard_text:
        clipboard_text = refresh_clipboard_preview(app, update_status=False)

    if not clipboard_text:
        messagebox.showwarning(APP_NAME, "Clipboard is empty or does not contain text.")
        app.text_tool_status_var.set("Clipboard is empty.")
        return

    mode = app.text_tool_clipboard_mode_var.get().strip()
    current_input = app._get_textbox_value(app.text_tool_input_box)

    if mode == "Append to Input" and current_input:
        separator = "\n\n" if current_input.strip() and clipboard_text.strip() else ""
        next_value = f"{current_input}{separator}{clipboard_text}"
        app.text_tool_status_var.set("Clipboard content appended to input.")
    else:
        next_value = clipboard_text
        app.text_tool_status_var.set("Clipboard content loaded into input.")

    app._set_textbox_value(app.text_tool_input_box, next_value)
