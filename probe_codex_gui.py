from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import messagebox, ttk

from probe_tools import probe_gui_support as support


APP_TITLE = "Codex 供应商探测"
DEFAULT_ATTEMPTS = 1
DEFAULT_TIMEOUT_SECONDS = 90


def format_attempt_progress(
    provider: str,
    model: str,
    current_attempt: int,
    total_attempts: int,
    elapsed_seconds: int,
    timeout_seconds: int,
) -> str:
    prefix = (
        f"{provider} | {model} | 第 {current_attempt}/{total_attempts} 次尝试"
    )
    remaining = max(0, timeout_seconds - elapsed_seconds)
    if remaining == 0:
        return f"{prefix} | 已达到 {timeout_seconds}s 超时，正在终止子进程..."
    return f"{prefix} | 已用 {elapsed_seconds}s，剩余 {remaining}s"


def compact_result_detail(detail: str, limit: int = 48) -> str:
    normalized = " ".join(detail.split())
    summary = normalized.split("；", 1)[0]
    if len(summary) <= limit:
        return summary
    return summary[: limit - 1].rstrip() + "…"


class ScrollableCheckList(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        on_change: Callable[[], None] | None = None,
        height: int = 220,
    ) -> None:
        super().__init__(master)
        self.on_change = on_change
        self.variables: dict[str, tk.BooleanVar] = {}
        self.labels: dict[str, str] = {}

        self.canvas = tk.Canvas(self, highlightthickness=0, height=height)
        self.scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
        )
        self.inner = ttk.Frame(self.canvas)
        self.window_id = self.canvas.create_window(
            (0, 0),
            window=self.inner,
            anchor="nw",
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.inner.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_inner)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.inner.bind("<MouseWheel>", self._on_mouse_wheel)

    def _update_scroll_region(self, _event: tk.Event[Any]) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.after_idle(self._update_scrollbar_visibility)

    def _resize_inner(self, event: tk.Event[Any]) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)
        self._update_scrollbar_visibility()

    def _update_scrollbar_visibility(self) -> None:
        content_height = self.inner.winfo_reqheight()
        viewport_height = self.canvas.winfo_height()
        if content_height > viewport_height:
            self.scrollbar.grid()
        else:
            self.canvas.yview_moveto(0)
            self.scrollbar.grid_remove()

    def _notify_change(self) -> None:
        if self.on_change:
            self.on_change()

    def _on_mouse_wheel(self, event: tk.Event[Any]) -> str:
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            self.canvas.yview_moveto(0)
            return "break"
        if event.delta == 0:
            return "break"
        direction = -1 if event.delta > 0 else 1
        steps = max(1, abs(event.delta) // 120)
        self.canvas.yview_scroll(direction * steps, "units")
        return "break"

    def set_items(
        self,
        items: list[tuple[str, str]],
        selected: set[str] | None = None,
    ) -> None:
        selected = selected or set()
        for child in self.inner.winfo_children():
            child.destroy()
        self.variables.clear()
        self.labels = dict(items)
        for row, (key, label) in enumerate(items):
            variable = tk.BooleanVar(master=self, value=key in selected)
            checkbox = ttk.Checkbutton(
                self.inner,
                text=label,
                variable=variable,
                command=self._notify_change,
            )
            checkbox.grid(row=row, column=0, sticky="w", padx=8, pady=3)
            checkbox.bind("<MouseWheel>", self._on_mouse_wheel)
            self.variables[key] = variable
        self.inner.columnconfigure(0, weight=1)
        self.after_idle(self._update_scrollbar_visibility)
        self._notify_change()

    def selected_keys(self) -> list[str]:
        return [key for key, variable in self.variables.items() if variable.get()]

    def set_selected(self, keys: set[str]) -> None:
        for key, variable in self.variables.items():
            variable.set(key in keys)
        self._notify_change()

    def set_all(self, value: bool) -> None:
        for variable in self.variables.values():
            variable.set(value)
        self._notify_change()


class ProbeApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.withdraw()
        self.root.title(APP_TITLE)
        self.root.geometry("1120x800")
        self.root.minsize(960, 700)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.running = False
        self.cancel_requested = False
        self.active_attempt: dict[str, Any] | None = None
        self.attempt_timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        self.last_report: Path | None = None
        self.providers = support.load_api_providers()
        self.provider_by_id = {
            provider.provider_id: provider for provider in self.providers
        }
        self.settings = support.load_settings()
        self.custom_models = list(self.settings["custom_models"])
        self.codex_binary = support.resolve_codex_binary()
        self.row_ids: dict[tuple[str, str], str] = {}
        self.result_details: dict[str, str] = {}
        self.result_tooltip: tk.Toplevel | None = None
        self.result_tooltip_item = ""
        self.result_tooltip_after: str | None = None

        default_provider_ids, default_models = support.default_selection(self.providers)
        saved_provider_ids = [
            provider_id
            for provider_id in self.settings["selected_provider_ids"]
            if provider_id in self.provider_by_id
        ]
        self.initial_provider_ids = set(saved_provider_ids or default_provider_ids)

        catalog = support.build_model_catalog(
            self.providers,
            self.custom_models + list(self.settings["selected_models"]),
        )
        self.model_catalog = catalog
        saved_models = [
            model for model in self.settings["selected_models"] if model in catalog
        ]
        self.initial_models = set(saved_models or default_models)

        self.attempts_var = tk.StringVar(value=str(DEFAULT_ATTEMPTS))
        self.timeout_var = tk.StringVar(value=str(DEFAULT_TIMEOUT_SECONDS))
        self.reasoning_var = tk.StringVar(value="high")
        self.sandbox_var = tk.StringVar(value="read-only")
        self.custom_model_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")
        self.selection_var = tk.StringVar()

        self._configure_style()
        self._build_ui()
        self._populate_providers(self.initial_provider_ids)
        self._populate_models(self.initial_models)
        self._update_selection_summary()
        self._center_window()
        self.root.deiconify()
        self.root.after(100, self._drain_events)

    def _center_window(self) -> None:
        self.root.update_idletasks()
        self.root.tk.call("tk::PlaceWindow", self.root._w, "center")

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        available = style.theme_names()
        if "vista" in available:
            style.theme_use("vista")
        elif "clam" in available:
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
        style.configure("Subtitle.TLabel", foreground="#666666")
        style.configure("Treeview", rowheight=23, font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=14)
        container.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(3, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
        )
        codex_text = (
            self.codex_binary.name
            if self.codex_binary
            else "未找到可直接启动的 Codex CLI"
        )
        ttk.Label(
            header,
            text=f"CC Switch provider 实际调用探测  ·  Codex: {codex_text}",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Button(header, text="刷新供应商", command=self.refresh_providers).grid(
            row=0,
            column=1,
            rowspan=2,
            sticky="e",
            padx=(12, 0),
        )

        selection = ttk.Panedwindow(container, orient="horizontal")
        selection.grid(row=1, column=0, sticky="nsew")

        provider_frame = ttk.LabelFrame(selection, text="供应商", padding=8)
        model_frame = ttk.LabelFrame(selection, text="模型", padding=8)
        selection.add(provider_frame, weight=1)
        selection.add(model_frame, weight=1)

        provider_buttons = ttk.Frame(provider_frame)
        provider_buttons.pack(fill="x", pady=(0, 6))
        ttk.Button(
            provider_buttons,
            text="全选",
            command=lambda: self.provider_list.set_all(True),
        ).pack(side="left")
        ttk.Button(
            provider_buttons,
            text="全不选",
            command=lambda: self.provider_list.set_all(False),
        ).pack(side="left", padx=5)
        ttk.Button(
            provider_buttons,
            text="只选当前",
            command=self.select_current_provider,
        ).pack(side="left")
        self.provider_list = ScrollableCheckList(
            provider_frame,
            on_change=self._update_selection_summary,
            height=220,
        )
        self.provider_list.pack(fill="both", expand=True)

        model_buttons = ttk.Frame(model_frame)
        model_buttons.pack(fill="x", pady=(0, 6))
        ttk.Button(
            model_buttons,
            text="全选",
            command=lambda: self.model_list.set_all(True),
        ).pack(side="left")
        ttk.Button(
            model_buttons,
            text="全不选",
            command=lambda: self.model_list.set_all(False),
        ).pack(side="left", padx=5)
        ttk.Button(
            model_buttons,
            text="删除已选自定义",
            command=self.remove_selected_custom_models,
        ).pack(side="left")
        self.model_list = ScrollableCheckList(
            model_frame,
            on_change=self._update_selection_summary,
            height=180,
        )
        self.model_list.pack(fill="both", expand=True)

        custom_row = ttk.Frame(model_frame)
        custom_row.pack(fill="x", pady=(7, 0))
        custom_entry = ttk.Entry(custom_row, textvariable=self.custom_model_var)
        custom_entry.pack(side="left", fill="x", expand=True)
        custom_entry.bind("<Return>", lambda _event: self.add_custom_model())
        ttk.Button(custom_row, text="添加模型", command=self.add_custom_model).pack(
            side="left",
            padx=(6, 0),
        )

        options = ttk.LabelFrame(container, text="探测参数（默认）", padding=8)
        options.grid(row=2, column=0, sticky="ew", pady=10)
        for column in range(10):
            options.columnconfigure(column, weight=0)
        options.columnconfigure(9, weight=1)
        ttk.Label(options, text="尝试次数").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(
            options,
            from_=1,
            to=10,
            width=6,
            textvariable=self.attempts_var,
        ).grid(row=0, column=1, padx=(5, 16))
        ttk.Label(options, text="单次超时(s)").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(
            options,
            from_=10,
            to=3600,
            increment=10,
            width=8,
            textvariable=self.timeout_var,
        ).grid(row=0, column=3, padx=(5, 16))
        ttk.Label(options, text="推理强度").grid(row=0, column=4, sticky="w")
        ttk.Combobox(
            options,
            values=("low", "medium", "high", "xhigh"),
            state="readonly",
            width=9,
            textvariable=self.reasoning_var,
        ).grid(row=0, column=5, padx=(5, 16))
        ttk.Label(options, text="沙箱").grid(row=0, column=6, sticky="w")
        ttk.Combobox(
            options,
            values=("read-only", "workspace-write", "danger-full-access"),
            state="readonly",
            width=18,
            textvariable=self.sandbox_var,
        ).grid(row=0, column=7, padx=(5, 16))
        ttk.Label(options, textvariable=self.selection_var).grid(
            row=0,
            column=9,
            sticky="e",
        )

        output_panes = ttk.Panedwindow(container, orient="horizontal")
        output_panes.grid(row=3, column=0, sticky="nsew")

        result_frame = ttk.LabelFrame(output_panes, text="探测结果", padding=6)
        log_frame = ttk.LabelFrame(output_panes, text="实时日志", padding=6)
        output_panes.add(result_frame, weight=1)
        output_panes.add(log_frame, weight=1)

        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)
        columns = ("provider", "model", "status", "elapsed", "detail")
        self.result_tree = ttk.Treeview(
            result_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=6,
        )
        headings = {
            "provider": "供应商",
            "model": "模型",
            "status": "状态",
            "elapsed": "耗时",
            "detail": "关键信息",
        }
        widths = {
            "provider": 145,
            "model": 120,
            "status": 80,
            "elapsed": 65,
            "detail": 320,
        }
        for column in columns:
            self.result_tree.heading(column, text=headings[column])
            self.result_tree.column(
                column,
                width=widths[column],
                minwidth=60,
                stretch=column == "detail",
            )
        result_scrollbar = ttk.Scrollbar(
            result_frame,
            orient="vertical",
            command=self.result_tree.yview,
        )
        result_horizontal_scrollbar = ttk.Scrollbar(
            result_frame,
            orient="horizontal",
            command=self.result_tree.xview,
        )
        self.result_tree.configure(
            yscrollcommand=result_scrollbar.set,
            xscrollcommand=result_horizontal_scrollbar.set,
        )
        self.result_tree.grid(row=0, column=0, sticky="nsew")
        result_scrollbar.grid(row=0, column=1, sticky="ns")
        result_horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        self.result_tree.tag_configure("healthy", foreground="#16803c")
        self.result_tree.tag_configure("warning", foreground="#9a6700")
        self.result_tree.tag_configure("error", foreground="#c62828")
        self.result_tree.bind("<Double-1>", self.show_selected_result_detail)
        self.result_tree.bind("<Motion>", self._on_result_motion)
        self.result_tree.bind("<Leave>", self._hide_result_tooltip)
        self.result_tree.bind("<ButtonPress>", self._hide_result_tooltip)
        self.result_tree.bind("<MouseWheel>", self._hide_result_tooltip)

        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            height=15,
            wrap="none",
            font=("Microsoft YaHei UI", 9),
            state="disabled",
        )
        log_scrollbar = ttk.Scrollbar(
            log_frame,
            orient="vertical",
            command=self.log_text.yview,
        )
        log_horizontal_scrollbar = ttk.Scrollbar(
            log_frame,
            orient="horizontal",
            command=self.log_text.xview,
        )
        self.log_text.configure(
            yscrollcommand=log_scrollbar.set,
            xscrollcommand=log_horizontal_scrollbar.set,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scrollbar.grid(row=0, column=1, sticky="ns")
        log_horizontal_scrollbar.grid(row=1, column=0, sticky="ew")

        footer = ttk.Frame(container)
        footer.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(5, weight=1)
        self.start_button = ttk.Button(
            footer,
            text="开始探测",
            command=self.start_probe,
        )
        self.start_button.grid(row=0, column=0)
        self.cancel_button = ttk.Button(
            footer,
            text="取消",
            command=self.cancel_probe,
            state="disabled",
        )
        self.cancel_button.grid(row=0, column=1, padx=6)
        self.open_report_button = ttk.Button(
            footer,
            text="打开报告",
            command=self.open_report,
            state="disabled",
        )
        self.open_report_button.grid(row=0, column=2)
        ttk.Button(footer, text="打开报告目录", command=self.open_report_directory).grid(
            row=0,
            column=3,
            padx=6,
        )
        self.progress = ttk.Progressbar(
            footer,
            mode="determinate",
            maximum=100,
            value=0,
            length=150,
        )
        self.progress.grid(row=0, column=4, padx=(8, 10))
        ttk.Label(footer, textvariable=self.status_var).grid(
            row=0,
            column=5,
            sticky="e",
        )

    def _provider_items(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        for provider in self.providers:
            markers: list[str] = []
            if provider.is_current:
                markers.append("当前")
            model = support.configured_model(provider)
            if model:
                markers.append(f"配置模型: {model}")
            suffix = f"  [{' · '.join(markers)}]" if markers else ""
            items.append((provider.provider_id, provider.name + suffix))
        return items

    def _populate_providers(self, selected: set[str]) -> None:
        self.provider_list.set_items(self._provider_items(), selected)

    def _populate_models(self, selected: set[str]) -> None:
        items = [(model, model) for model in self.model_catalog]
        self.model_list.set_items(items, selected)

    def _update_selection_summary(self) -> None:
        if not hasattr(self, "provider_list") or not hasattr(self, "model_list"):
            return
        provider_count = len(self.provider_list.selected_keys())
        model_count = len(self.model_list.selected_keys())
        self.selection_var.set(
            f"已选 {provider_count} 个供应商 × {model_count} 个模型 = {provider_count * model_count} 组"
        )

    def select_current_provider(self) -> None:
        current_ids = {
            provider.provider_id for provider in self.providers if provider.is_current
        }
        self.provider_list.set_selected(current_ids)

    def refresh_providers(self) -> None:
        if self.running:
            return
        selected = set(self.provider_list.selected_keys())
        selected_models = set(self.model_list.selected_keys())
        try:
            self.providers = support.load_api_providers()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"刷新供应商失败：{exc}", parent=self.root)
            return
        self.provider_by_id = {
            provider.provider_id: provider for provider in self.providers
        }
        valid_selected = selected.intersection(self.provider_by_id)
        if not valid_selected:
            valid_selected = set(support.default_selection(self.providers)[0])
        self._populate_providers(valid_selected)
        self.model_catalog = support.build_model_catalog(
            self.providers,
            self.custom_models + list(selected_models),
        )
        self._populate_models(selected_models)
        self.status_var.set(f"已刷新，共 {len(self.providers)} 个 API 供应商")

    def add_custom_model(self) -> None:
        model = self.custom_model_var.get().strip()
        if not model:
            return
        selected = set(self.model_list.selected_keys())
        if model not in self.model_catalog:
            known_without_custom = support.build_model_catalog(self.providers, [])
            if model not in known_without_custom and model not in self.custom_models:
                self.custom_models.append(model)
            self.model_catalog = support.build_model_catalog(
                self.providers,
                self.custom_models,
            )
        selected.add(model)
        self._populate_models(selected)
        self.custom_model_var.set("")

    def remove_selected_custom_models(self) -> None:
        selected = set(self.model_list.selected_keys())
        removable = selected.intersection(self.custom_models)
        if not removable:
            return
        self.custom_models = [
            model for model in self.custom_models if model not in removable
        ]
        self.model_catalog = support.build_model_catalog(
            self.providers,
            self.custom_models,
        )
        self._populate_models(selected - removable)

    def _current_settings(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "selected_provider_ids": self.provider_list.selected_keys(),
            "selected_models": self.model_list.selected_keys(),
            "custom_models": self.custom_models,
        }

    def _save_settings(self) -> None:
        try:
            support.save_settings(self._current_settings())
        except OSError as exc:
            self._append_log(f"保存选择失败：{exc}")

    def _parse_positive_int(self, value: str, label: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{label}必须是整数") from exc
        if parsed < 1:
            raise ValueError(f"{label}必须大于等于 1")
        return parsed

    def start_probe(self) -> None:
        if self.running:
            return
        provider_ids = self.provider_list.selected_keys()
        models = self.model_list.selected_keys()
        if not provider_ids:
            messagebox.showwarning(APP_TITLE, "请至少选择一个供应商。", parent=self.root)
            return
        if not models:
            messagebox.showwarning(APP_TITLE, "请至少选择一个模型。", parent=self.root)
            return
        try:
            attempts = self._parse_positive_int(self.attempts_var.get(), "尝试次数")
            timeout = self._parse_positive_int(self.timeout_var.get(), "超时秒数")
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc), parent=self.root)
            return

        if self.codex_binary is None:
            self.codex_binary = support.resolve_codex_binary()
        if self.codex_binary is None:
            messagebox.showerror(
                APP_TITLE,
                "没有找到可由 Python 直接启动的 Codex 原生可执行文件。",
                parent=self.root,
            )
            return

        support.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = support.REPORT_DIR / f"probe-{timestamp}.json"
        command = support.build_probe_command(
            python_executable=support.resolve_python_console(),
            backend_script=support.BACKEND_SCRIPT,
            provider_ids=provider_ids,
            models=models,
            codex_binary=self.codex_binary,
            output_path=output_path,
            attempts=attempts,
            timeout=timeout,
            reasoning_effort=self.reasoning_var.get(),
            sandbox=self.sandbox_var.get(),
        )

        self._save_settings()
        self._prepare_result_rows(provider_ids, models)
        self._clear_log()
        self._append_log(
            f"开始探测：{len(provider_ids)} 个供应商 × {len(models)} 个模型；报告：{output_path}"
        )
        self.running = True
        self.cancel_requested = False
        self.active_attempt = None
        self.attempt_timeout_seconds = timeout
        self.last_report = None
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.open_report_button.configure(state="disabled")
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)
        self.status_var.set("正在启动探测进程...")

        worker = threading.Thread(
            target=self._run_probe,
            args=(command, output_path),
            daemon=True,
        )
        worker.start()

    def _prepare_result_rows(self, provider_ids: list[str], models: list[str]) -> None:
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        self.row_ids.clear()
        self.result_details.clear()
        for provider_id in provider_ids:
            provider = self.provider_by_id.get(provider_id)
            provider_name = provider.name if provider else provider_id
            for model in models:
                item_id = self.result_tree.insert(
                    "",
                    "end",
                    values=(provider_name, model, "等待", "", ""),
                )
                self.row_ids[(provider_name, model)] = item_id

    def _run_probe(self, command: list[str], output_path: Path) -> None:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        creation_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        child_env = os.environ.copy()
        child_env["PYTHONUTF8"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        try:
            process = subprocess.Popen(
                command,
                cwd=support.PROJECT_ROOT,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
            )
            self.process = process
            if process.stdout is not None:
                for line in process.stdout:
                    self.event_queue.put(("log", line.rstrip("\r\n")))
            return_code = process.wait()
            report: dict[str, Any] | None = None
            if output_path.is_file():
                try:
                    report = json.loads(output_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    self.event_queue.put(("log", f"读取报告失败：{exc}"))
            self.event_queue.put(
                (
                    "complete",
                    {
                        "return_code": return_code,
                        "output_path": output_path,
                        "report": report,
                        "canceled": self.cancel_requested,
                    },
                )
            )
        except Exception as exc:
            self.event_queue.put(("error", str(exc)))
        finally:
            self.process = None

    def _drain_events(self) -> None:
        try:
            while True:
                event_type, payload = self.event_queue.get_nowait()
                if event_type == "log":
                    self._append_log(str(payload))
                    self._update_progress_from_log(str(payload))
                elif event_type == "complete":
                    self._finish_probe(payload)
                elif event_type == "error":
                    self._finish_probe_with_error(str(payload))
        except queue.Empty:
            pass
        self._refresh_attempt_progress()
        if self.root.winfo_exists():
            self.root.after(100, self._drain_events)

    def _refresh_attempt_progress(self) -> None:
        attempt = self.active_attempt
        if not self.running or attempt is None:
            return
        elapsed_seconds = int(time.monotonic() - attempt["started_at"])
        self.progress.configure(
            mode="determinate",
            maximum=self.attempt_timeout_seconds,
            value=min(elapsed_seconds, self.attempt_timeout_seconds),
        )
        self.status_var.set(
            format_attempt_progress(
                attempt["provider"],
                attempt["model"],
                attempt["current"],
                attempt["total"],
                elapsed_seconds,
                self.attempt_timeout_seconds,
            )
        )

    def _update_progress_from_log(self, line: str) -> None:
        message = re.sub(r"^\[\d{2}:\d{2}:\d{2}\]\s*", "", line).strip()
        if message:
            self.status_var.set(message)

        attempt_started = re.match(
            r"^(.*?) \| (\S+) \| 第 (\d+)/(\d+) 次尝试 \| 题目 (.+)$",
            message,
        )
        if attempt_started:
            self.progress.stop()
            self.progress.configure(
                mode="determinate",
                maximum=self.attempt_timeout_seconds,
                value=0,
            )
            self.active_attempt = {
                "provider": attempt_started.group(1),
                "model": attempt_started.group(2),
                "current": int(attempt_started.group(3)),
                "total": int(attempt_started.group(4)),
                "started_at": time.monotonic(),
            }
            self._set_result_row(
                attempt_started.group(1),
                attempt_started.group(2),
                "运行中",
                "0s",
                "",
            )
            self._refresh_attempt_progress()
            return

        started = re.match(r"^(.*?) \| 开始模型 (\S+)$", message)
        if started:
            self._set_result_row(started.group(1), started.group(2), "运行中", "", "")
            return

        completed = re.match(
            r"^(.*?) \| (\S+) \| 第 \d+/\d+ 次尝试完成 \| 状态 ([^|]+) \| rc=-?\d+ \| elapsed=([0-9.]+)s(?: \| 关键信息 (.*))?$",
            message,
        )
        if completed:
            self.active_attempt = None
            self.progress.configure(mode="determinate", value=0)
            self._set_result_row(
                completed.group(1),
                completed.group(2),
                completed.group(3).strip(),
                completed.group(4) + "s",
                completed.group(5) or "",
            )

    def _set_result_row(
        self,
        provider: str,
        model: str,
        status: str,
        elapsed: str,
        detail: str,
        raw_status: str = "",
    ) -> None:
        item_id = self.row_ids.get((provider, model))
        if not item_id:
            return
        lowered = raw_status or status
        if lowered == "healthy" or status == "正常":
            tag = "healthy"
        elif lowered in {"timeout", "rate_limited", "network_error"} or status in {
            "超时",
            "限流",
            "连接异常",
        }:
            tag = "warning"
        elif status in {"等待", "运行中"}:
            tag = ""
        else:
            tag = "error"
        full_detail = " ".join(detail.split())
        self.result_details[item_id] = full_detail
        self.result_tree.item(
            item_id,
            values=(
                provider,
                model,
                status,
                elapsed,
                compact_result_detail(full_detail),
            ),
            tags=(tag,) if tag else (),
        )

    def _finish_probe(self, payload: dict[str, Any]) -> None:
        self.running = False
        self.active_attempt = None
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        output_path = Path(payload["output_path"])
        report = payload.get("report")
        if report:
            self.last_report = output_path
            self.open_report_button.configure(state="normal")
            for row in support.report_rows(report):
                self._set_result_row(
                    row["provider"],
                    row["model"],
                    row["status_label"],
                    row["elapsed"],
                    row["detail"],
                    row["status"],
                )

        if payload.get("canceled"):
            self.status_var.set("探测已取消")
            self._append_log("探测已由用户取消。")
        elif payload.get("return_code") == 0:
            self.status_var.set("探测完成，所有选中组合均正常")
        elif report:
            self.status_var.set("探测完成，部分组合存在异常")
        else:
            self.status_var.set(
                f"探测进程退出，但没有生成报告（rc={payload.get('return_code')}）"
            )

    def _finish_probe_with_error(self, message: str) -> None:
        self.running = False
        self.active_attempt = None
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.status_var.set("探测启动失败")
        self._append_log("探测启动失败：" + message)
        messagebox.showerror(APP_TITLE, "探测启动失败：" + message, parent=self.root)

    def cancel_probe(self) -> None:
        process = self.process
        if not self.running or process is None or process.poll() is not None:
            return
        self.cancel_requested = True
        self.active_attempt = None
        self.cancel_button.configure(state="disabled")
        self.status_var.set("正在取消探测...")

        def terminate_tree() -> None:
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    creationflags=creation_flags,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                self.event_queue.put(("log", f"取消进程失败：{exc}"))

        threading.Thread(target=terminate_tree, daemon=True).start()

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _on_result_motion(self, event: tk.Event[Any]) -> None:
        item_id = self.result_tree.identify_row(event.y)
        column = self.result_tree.identify_column(event.x)
        detail = self.result_details.get(item_id, "")
        if column != "#5" or not detail:
            self._hide_result_tooltip()
            return
        if item_id == self.result_tooltip_item:
            return
        self._hide_result_tooltip()
        self.result_tooltip_item = item_id
        x = event.x_root + 14
        y = event.y_root + 16
        self.result_tooltip_after = self.root.after(
            350,
            lambda: self._show_result_tooltip(item_id, detail, x, y),
        )

    def _show_result_tooltip(
        self,
        item_id: str,
        detail: str,
        x: int,
        y: int,
    ) -> None:
        self.result_tooltip_after = None
        if item_id != self.result_tooltip_item:
            return
        tooltip = tk.Toplevel(self.root)
        tooltip.overrideredirect(True)
        tooltip.attributes("-topmost", True)
        label = tk.Label(
            tooltip,
            text=detail,
            justify="left",
            wraplength=520,
            background="#fffbe6",
            foreground="#202124",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=6,
            font=("Microsoft YaHei UI", 9),
        )
        label.pack()
        tooltip.update_idletasks()
        x = min(x, self.root.winfo_screenwidth() - tooltip.winfo_reqwidth() - 8)
        y = min(y, self.root.winfo_screenheight() - tooltip.winfo_reqheight() - 8)
        tooltip.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.result_tooltip = tooltip

    def _hide_result_tooltip(self, _event: tk.Event[Any] | None = None) -> None:
        if self.result_tooltip_after is not None:
            self.root.after_cancel(self.result_tooltip_after)
            self.result_tooltip_after = None
        if self.result_tooltip is not None:
            self.result_tooltip.destroy()
            self.result_tooltip = None
        self.result_tooltip_item = ""

    def show_selected_result_detail(self, _event: tk.Event[Any]) -> None:
        selection = self.result_tree.selection()
        if not selection:
            return
        values = self.result_tree.item(selection[0], "values")
        if not values:
            return
        detail = self.result_details.get(selection[0], "")
        messagebox.showinfo(
            f"{values[0]} · {values[1]}",
            f"状态：{values[2]}\n耗时：{values[3]}\n\n{detail or '无额外信息'}",
            parent=self.root,
        )

    def open_report(self) -> None:
        if self.last_report and self.last_report.is_file():
            os.startfile(self.last_report)  # type: ignore[attr-defined]

    def open_report_directory(self) -> None:
        support.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(support.REPORT_DIR)  # type: ignore[attr-defined]

    def on_close(self) -> None:
        if self.running:
            confirmed = messagebox.askyesno(
                APP_TITLE,
                "探测仍在运行，关闭窗口将终止当前探测。是否继续？",
                parent=self.root,
            )
            if not confirmed:
                return
            self.cancel_probe()
        self._save_settings()
        self.root.after(300, self.root.destroy)


def smoke_test() -> int:
    providers = support.load_api_providers()
    settings = support.load_settings()
    models = support.build_model_catalog(providers, settings["custom_models"])
    codex_binary = support.resolve_codex_binary()
    payload = {
        "title": APP_TITLE,
        "provider_count": len(providers),
        "model_count": len(models),
        "codex_binary_found": codex_binary is not None,
        "codex_binary": str(codex_binary) if codex_binary else "",
        "settings_path": str(support.settings_path()),
    }
    print(json.dumps(payload, ensure_ascii=True))
    return 0 if providers and codex_binary else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="检查 GUI 依赖并输出 JSON，不打开窗口。",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.smoke_test:
        return smoke_test()
    root = tk.Tk()
    ProbeApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
