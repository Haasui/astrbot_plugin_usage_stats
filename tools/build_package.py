"""Build an AstrBot installation archive from runtime sources and user documentation."""
from __future__ import annotations
import ast
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "__init__.py", "main.py", "attribute.py", "patch.py", "stats.py", "store.py", "event_store.py", "price_catalog.py", "log_tail.py",
    "_conf_schema.json", "metadata.yaml", "requirements.txt", "LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", "CHANGELOG.md",
    ".astrbot-plugin/i18n/en-US.json", "views/usage/index.html", "views/usage/style.css", "views/usage/dashboard.js", "views/usage/app.js",
    "docs/README.md", "docs/USAGE.md", "docs/CONFIGURATION.md", "docs/ARCHITECTURE.md",
]
def build():
    metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
    version = re.search(r"^version:\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$", metadata, re.M).group(1)
    if f'VERSION = "{version}"' not in (ROOT / "main.py").read_text(encoding="utf-8"):
        raise ValueError("source and metadata versions differ")
    if "GNU AFFERO GENERAL PUBLIC LICENSE" not in (ROOT / "LICENSE").read_text(encoding="utf-8"):
        raise ValueError("unexpected project license")
    payloads = [(name, (ROOT / name).read_bytes()) for name in FILES]
    for name, data in payloads:
        if name.endswith(".py"):
            ast.parse(data, filename=name)
    output = ROOT / "_dist" / f"astrbot_plugin_usage_stats-v{version}-install.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in payloads:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("archive CRC failure")
    print(f"Created {output} ({len(FILES)} files, {output.stat().st_size} bytes)")

if __name__ == "__main__":
    build()
