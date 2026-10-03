#!/usr/bin/env python3
"""Generates embedded payload C++ source and header files for cmdiqo-installer."""

from __future__ import annotations

import argparse
import io
from pathlib import Path
import zipfile


def generate_embedded_payload(
    binary_path: Path | None,
    version: str,
    output_header: Path,
    output_source: Path,
    use_zip: bool = True,
) -> None:
    output_header.parent.mkdir(parents=True, exist_ok=True)
    output_source.parent.mkdir(parents=True, exist_ok=True)

    header_content = """#pragma once

#include <cstddef>

namespace cmdiqo::installer::resources {

extern const unsigned char embedded_payload_data[];
extern const std::size_t embedded_payload_size;
extern const bool embedded_payload_is_zip;

}  // namespace cmdiqo::installer::resources
"""
    output_header.write_text(header_content, encoding="utf-8")

    payload_bytes = b""
    is_zip = False

    if binary_path is not None and binary_path.is_file():
        binary_data = binary_path.read_bytes()
        if use_zip:
            is_zip = True
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                arcname = binary_path.name
                if binary_path.suffix.lower() == ".exe" or "windows" in str(binary_path).lower():
                    arcname = "cmdiqo-agent.exe"
                else:
                    arcname = "cmdiqo-agent"
                zf.writestr(arcname, binary_data)
                zf.writestr("version.txt", (version.strip() + "\n").encode("utf-8"))
            payload_bytes = buf.getvalue()
        else:
            payload_bytes = binary_data

    lines = [
        '#include "embedded_payload.hpp"',
        "",
        "namespace cmdiqo::installer::resources {",
        "",
        f"const bool embedded_payload_is_zip = {'true' if is_zip else 'false'};",
        f"const std::size_t embedded_payload_size = {len(payload_bytes)};",
        "",
    ]

    if payload_bytes:
        lines.append("const unsigned char embedded_payload_data[] = {")
        chunk_size = 32
        for i in range(0, len(payload_bytes), chunk_size):
            chunk = payload_bytes[i : i + chunk_size]
            hex_str = ", ".join(f"0x{b:02x}" for b in chunk)
            lines.append(f"    {hex_str},")
        lines.append("};")
    else:
        lines.append("const unsigned char embedded_payload_data[] = { 0 };")

    lines.append("")
    lines.append("}  // namespace cmdiqo::installer::resources")
    lines.append("")

    output_source.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed agent binary into installer")
    parser.add_argument("--binary", type=Path, default=None, help="Path to agent binary")
    parser.add_argument("--version", type=str, default="unknown", help="Build version")
    parser.add_argument("--output-header", type=Path, required=True, help="Output header path")
    parser.add_argument("--output-source", type=Path, required=True, help="Output source path")
    parser.add_argument("--zip", action="store_true", default=True, help="Compress payload as zip")
    args = parser.parse_args()

    binary_path = args.binary
    if binary_path is not None and not binary_path.is_file():
        binary_path = None

    generate_embedded_payload(
        binary_path=binary_path,
        version=args.version,
        output_header=args.output_header,
        output_source=args.output_source,
        use_zip=args.zip,
    )


if __name__ == "__main__":
    main()
