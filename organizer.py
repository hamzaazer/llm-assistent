from __future__ import annotations

from typing import Any, Dict, List
from pathlib import Path

from models import (
    Decision,
    Extractor,
    FileInsight,
    LLMClient,
    build_decision,
    guess_kind,
    human_size,
)


def run_organize_preview(self, cfg: Dict[str, Any]) -> None:
    root = Path(cfg["folder"])
    client = LLMClient(cfg["endpoint"], timeout=cfg["timeout"])
    files = self._iter_files(root, cfg["recursive"], cfg["max_files"], cfg["skip_hidden"], cfg["ignore_organized_folders"])
    total = len(files)

    if total == 0:
        self.app.emit("done", {"decisions": [], "errors": ["No files found."]}, op=self.operation_name)
        return

    decisions: List[Decision] = []
    errors: List[str] = []

    for idx, path in enumerate(files, start=1):
        if self.stop_event.is_set():
            errors.append("Operation stopped by user.")
            break

        self.app.emit("progress", {"current": idx - 1, "total": total, "file": path.name}, op=self.operation_name)

        try:
            if path.stat().st_size > cfg["max_file_size_mb"] * 1024 * 1024:
                insight = FileInsight(
                    path=path,
                    kind=guess_kind(path),
                    snippet=f"Large file skipped for deep reading. Size: {human_size(path.stat().st_size)}",
                    meta=Extractor.file_meta(path),
                    extraction_method="metadata_only",
                )
            else:
                insight = Extractor.extract(path, cfg["max_chars"])

            mode, selected_model = self.choose_model_for_file(insight, cfg)

            if mode == "vision":
                try:
                    image_mb = path.stat().st_size / (1024 * 1024)
                    if image_mb > cfg["max_image_mb"]:
                        raise RuntimeError(f"image too large ({image_mb:.1f} MB)")

                    parsed, raw_output = client.classify_image(insight, selected_model)
                    model_used = selected_model
                    insight.extraction_method = "vision_json"

                except Exception as vision_exc:
                    fallback_insight = FileInsight(
                        path=insight.path,
                        kind=insight.kind,
                        snippet=(
                            f"Filename: {insight.path.name}\n"
                            "Type: image\n"
                            "Vision failed, infer broad title and category from filename and metadata."
                        ),
                        meta=insight.meta,
                        extraction_method="image_text_fallback",
                        error=str(vision_exc),
                    )

                    parsed, raw_output = client.classify_text(fallback_insight, cfg["text_model"])
                    model_used = f"{selected_model} -> fallback:{cfg['text_model']}"
                    insight = fallback_insight

            else:
                parsed, raw_output = client.classify_text(insight, selected_model)
                model_used = selected_model

            decision = build_decision(
                insight=insight,
                parsed=parsed,
                raw_output=raw_output,
                root=root,
                rename_files=cfg["rename_files"],
                action=cfg["action"],
                model_used=model_used,
            )

            decisions.append(decision)
            self.app.emit("row", decision, op=self.operation_name)

        except Exception as exc:
            errors.append(f"{path.name}: {exc}")

        self.app.emit("progress", {"current": idx, "total": total, "file": path.name}, op=self.operation_name)

    self.app.emit("done", {"decisions": decisions, "errors": errors}, op=self.operation_name)
