from __future__ import annotations

import json
from typing import Dict, List
from pathlib import Path

from models import (
    Decision,
    Extractor,
    FileInsight,
    LLMClient,
    filename_needs_translation,
    guess_kind,
    sanitize_component,
)


def run_translate_preview(self, cfg: Dict[str, Any]) -> None:
    root = Path(cfg["folder"])
    client = LLMClient(cfg["endpoint"], timeout=cfg["timeout"])
    files = self._iter_files(root, cfg["recursive"], cfg["max_files"], cfg["skip_hidden"], cfg["ignore_organized_folders"])
    total = len(files)
    target_language = cfg["target_language"].strip()

    if total == 0:
        self.app.emit("done", {"decisions": [], "errors": ["No files found."]}, op=self.operation_name)
        return

    if not target_language:
        self.app.emit("done", {"decisions": [], "errors": ["Target language is required."]}, op=self.operation_name)
        return

    decisions: List[Decision] = []
    errors: List[str] = []

    for idx, path in enumerate(files, start=1):
        if self.stop_event.is_set():
            errors.append("Operation stopped by user.")
            break

        self.app.emit("progress", {"current": idx - 1, "total": total, "file": path.name}, op=self.operation_name)

        try:
            insight = FileInsight(
                path=path,
                kind=guess_kind(path),
                snippet=f"Filename only: {path.stem}",
                meta=Extractor.file_meta(path),
                extraction_method="filename_only_translate",
            )

            if filename_needs_translation(path.stem):
                translated_title, raw_output = client.translate_filename(insight, cfg["text_model"], target_language)
            else:
                translated_title = path.stem
                raw_output = json.dumps({
                    "title": path.stem,
                    "summary": "Skipped translation because filename is numeric or not meaningful.",
                    "confidence": 1.0,
                }, ensure_ascii=False)

            target = insight.path.with_name(
                sanitize_component(translated_title, fallback=insight.path.stem, max_len=90) + insight.path.suffix
            )

            decision = Decision(
                source=insight.path,
                category="Translation",
                title=translated_title,
                summary=f"Translate filename to {target_language}",
                confidence=0.95,
                model_used=cfg["text_model"],
                kind=insight.kind,
                extraction_method="filename_only_translate",
                raw_output=raw_output,
                snippet=f"Old name: {path.stem}\nNew name: {translated_title}",
                target=target,
                action="rename",
                error="",
            )
            decisions.append(decision)
            self.app.emit("row", decision, op=self.operation_name)

        except Exception as exc:
            errors.append(f"{path.name}: {exc}")

        self.app.emit("progress", {"current": idx, "total": total, "file": path.name}, op=self.operation_name)

    self.app.emit("done", {"decisions": decisions, "errors": errors}, op=self.operation_name)
