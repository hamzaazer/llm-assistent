from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Set

from models import Decision, LLMClient, make_process_source, psutil, safe_float


def _normalize_process_name(name: str) -> str:
    return (name or "").strip().lower()


def run_speed_preview(self, cfg: Dict[str, Any]) -> None:
    max_process_items = max(1, min(cfg["max_processes"], 50))

    decisions: List[Decision] = []
    errors: List[str] = []

    if psutil is None:
        errors.append("psutil is not installed. Process analysis and live monitor are unavailable.")
        self.app.emit("done", {"decisions": decisions, "errors": errors}, op=self.operation_name)
        return

    process_candidates = self._collect_process_candidates(max_process_items)

    if not process_candidates:
        self.app.emit("done", {"decisions": decisions, "errors": errors}, op=self.operation_name)
        return

    try:
        client = LLMClient(cfg["endpoint"], timeout=cfg["timeout"])
        llm_items, _raw = client.classify_processes(process_candidates, cfg["text_model"])
    except Exception as exc:
        self.app.emit("done", {"decisions": decisions, "errors": [f"LLM process analysis failed: {exc}"]}, op=self.operation_name)
        return

    processes_by_name: Dict[str, List[Dict[str, Any]]] = {}
    for proc in process_candidates:
        key = _normalize_process_name(proc.get("name", ""))
        if not key:
            continue
        processes_by_name.setdefault(key, []).append(proc)

    for items in processes_by_name.values():
        items.sort(
            key=lambda x: (
                float(x.get("cpu_percent", 0.0) or 0.0) * 3.0
                + float(x.get("rss_mb", 0.0) or 0.0) / 120.0
            ),
            reverse=True,
        )

    selected_pids: Set[int] = set()
    total_proc = max(1, len(llm_items))

    for idx, item in enumerate(llm_items, start=1):
        if self.stop_event.is_set():
            errors.append("Operation stopped by user.")
            break

        raw_name = str(item.get("process_name") or item.get("name") or "").strip()
        should_close = bool(item.get("close", item.get("stop", False)))
        confidence = safe_float(item.get("confidence", 0.0), 0.0)
        reason = str(item.get("reason") or "Selected by the LLM as a safe background process to close.")

        self.app.emit("progress", {"current": idx - 1, "total": total_proc, "file": raw_name or f"item {idx}"}, op=self.operation_name)

        if not raw_name:
            self.app.emit("progress", {"current": idx, "total": total_proc, "file": f"item {idx}"}, op=self.operation_name)
            continue
        if not should_close:
            self.app.emit("progress", {"current": idx, "total": total_proc, "file": raw_name}, op=self.operation_name)
            continue
        if confidence < 0.55:
            self.app.emit("progress", {"current": idx, "total": total_proc, "file": raw_name}, op=self.operation_name)
            continue

        matched = processes_by_name.get(_normalize_process_name(raw_name), [])
        if not matched:
            errors.append(f"LLM selected unknown process name: {raw_name}")
            self.app.emit("progress", {"current": idx, "total": total_proc, "file": raw_name}, op=self.operation_name)
            continue

        for proc in matched:
            pid = int(proc.get("pid") or 0)
            if pid <= 0 or pid in selected_pids:
                continue

            selected_pids.add(pid)
            summary = (
                f"{reason} | CPU {float(proc.get('cpu_percent', 0.0) or 0.0):.1f}% | "
                f"RAM {float(proc.get('rss_mb', 0.0) or 0.0):.1f} MB"
            )

            decision = Decision(
                source=make_process_source(proc["name"], pid),
                category="Process",
                title=proc["name"],
                summary=summary,
                confidence=confidence,
                model_used=cfg["text_model"],
                kind="process",
                extraction_method="llm_process_name_close_plan",
                raw_output=json.dumps(item, ensure_ascii=False, indent=2),
                snippet=json.dumps(proc, ensure_ascii=False, indent=2),
                target=Path(f"PID {pid}"),
                action="terminate_process",
                process_pid=pid,
                process_name=proc["name"],
                process_username=proc["username"],
            )
            decisions.append(decision)
            self.app.emit("row", decision, op=self.operation_name)

        self.app.emit("progress", {"current": idx, "total": total_proc, "file": raw_name}, op=self.operation_name)

    self.app.emit("done", {"decisions": decisions, "errors": errors}, op=self.operation_name)
