"""Build .env from the fenced ```env block in _env.md.  Usage: python scripts/make_env.py [--force]"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src, dst = ROOT / "_env.md", ROOT / ".env"


def extract(md: str) -> str:
    m = re.search(r"```env\n(.*?)```", md, flags=re.S)
    if not m:
        raise SystemExit("no ```env block found in _env.md")
    return m.group(1)


def main() -> None:
    if dst.exists() and "--force" not in sys.argv:
        raise SystemExit(f"{dst.name} already exists. Use --force to overwrite (your keys will be lost).")
    dst.write_text(extract(src.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"wrote {dst}")


if __name__ == "__main__":
    main()
