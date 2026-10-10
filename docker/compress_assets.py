#!/usr/bin/env python3
"""docker.compress_assets: Minifies HTML, CSS, and JS assets for production container images."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    import minify_html
except ImportError:
    minify_html = None

try:
    import rcssmin
except ImportError:
    rcssmin = None

try:
    import rjsmin
except ImportError:
    rjsmin = None


def compress_directory(target_dir: Path | str) -> dict[str, tuple[int, int]]:
    """Recursively minifies all HTML, CSS, and JS files within target_dir."""
    target_path = Path(target_dir).resolve()
    stats: dict[str, tuple[int, int]] = {}
    if not target_path.exists():
        print(f"Target directory {target_path} does not exist.", file=sys.stderr)
        return stats

    html_count = 0
    css_count = 0
    js_count = 0
    orig_total = 0
    min_total = 0

    for root, _, files in os.walk(target_path):
        for f in files:
            p = Path(root) / f
            ext = p.suffix.lower()

            if ext == ".html":
                try:
                    raw = p.read_text(encoding="utf-8")
                    orig_bytes = len(raw.encode("utf-8"))
                    if minify_html:
                        minified = minify_html.minify(
                            raw,
                            keep_closing_tags=True,
                            keep_html_and_head_opening_tags=True,
                            minify_css=True,
                            minify_js=True,
                        )
                    else:
                        minified = raw
                    min_bytes = len(minified.encode("utf-8"))
                    p.write_text(minified, encoding="utf-8")
                    html_count += 1
                    orig_total += orig_bytes
                    min_total += min_bytes
                    stats[str(p.relative_to(target_path))] = (orig_bytes, min_bytes)
                    saved = orig_bytes - min_bytes
                    pct = (saved / orig_bytes * 100) if orig_bytes > 0 else 0
                    print(f"⚡ Minified HTML: {p.relative_to(target_path)} ({orig_bytes} -> {min_bytes} bytes, -{pct:.1f}%)")
                except Exception as exc:  # noqa: BLE001
                    print(f"⚠️ Warning: Failed minifying {p}: {exc}", file=sys.stderr)

            elif ext == ".css":
                try:
                    raw = p.read_text(encoding="utf-8")
                    orig_bytes = len(raw.encode("utf-8"))
                    minified = rcssmin.cssmin(raw) if rcssmin else raw
                    min_bytes = len(minified.encode("utf-8"))
                    p.write_text(minified, encoding="utf-8")
                    css_count += 1
                    orig_total += orig_bytes
                    min_total += min_bytes
                    stats[str(p.relative_to(target_path))] = (orig_bytes, min_bytes)
                    saved = orig_bytes - min_bytes
                    pct = (saved / orig_bytes * 100) if orig_bytes > 0 else 0
                    print(f"⚡ Minified CSS: {p.relative_to(target_path)} ({orig_bytes} -> {min_bytes} bytes, -{pct:.1f}%)")
                except Exception as exc:  # noqa: BLE001
                    print(f"⚠️ Warning: Failed minifying {p}: {exc}", file=sys.stderr)

            elif ext == ".js" and not f.endswith(".min.js"):
                try:
                    raw = p.read_text(encoding="utf-8")
                    orig_bytes = len(raw.encode("utf-8"))
                    minified = rjsmin.jsmin(raw) if rjsmin else raw
                    min_bytes = len(minified.encode("utf-8"))
                    p.write_text(minified, encoding="utf-8")
                    js_count += 1
                    orig_total += orig_bytes
                    min_total += min_bytes
                    stats[str(p.relative_to(target_path))] = (orig_bytes, min_bytes)
                    saved = orig_bytes - min_bytes
                    pct = (saved / orig_bytes * 100) if orig_bytes > 0 else 0
                    print(f"⚡ Minified JS: {p.relative_to(target_path)} ({orig_bytes} -> {min_bytes} bytes, -{pct:.1f}%)")
                except Exception as exc:  # noqa: BLE001
                    print(f"⚠️ Warning: Failed minifying {p}: {exc}", file=sys.stderr)

    saved_total = orig_total - min_total
    pct_total = (saved_total / orig_total * 100) if orig_total > 0 else 0
    print(
        f"✅ Compressed {html_count} HTML, {css_count} CSS, and {js_count} JS files: "
        f"{orig_total / 1024:.1f} KB -> {min_total / 1024:.1f} KB (-{saved_total / 1024:.1f} KB / -{pct_total:.1f}%)."
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Compress HTML, CSS, and JS assets for container images.")
    parser.add_argument("target", nargs="?", default="bot", help="Target directory to compress (default: bot)")
    args = parser.parse_args()
    compress_directory(args.target)


if __name__ == "__main__":
    main()
