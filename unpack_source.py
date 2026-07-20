from __future__ import annotations

import base64
import shutil
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARTS = sorted((ROOT / ".upload").glob("source.zip.b64.part*"))

if not PARTS:
    raise SystemExit("소스 패키지를 찾지 못했습니다.")

encoded = "".join(part.read_text(encoding="utf-8").strip() for part in PARTS)
archive_bytes = base64.b64decode(encoded, validate=True)

with tempfile.TemporaryDirectory() as directory:
    temporary = Path(directory)
    archive = temporary / "source.zip"
    archive.write_bytes(archive_bytes)
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(temporary / "source")
    source_dirs = [path for path in (temporary / "source").iterdir() if path.is_dir()]
    if len(source_dirs) != 1:
        raise SystemExit("소스 폴더 구조를 확인할 수 없습니다.")
    for item in source_dirs[0].iterdir():
        destination = ROOT / item.name
        if item.is_dir():
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(item, destination)
        else:
            shutil.copy2(item, destination)

print("전체 소스를 저장소 최상단에 풀었습니다.")
