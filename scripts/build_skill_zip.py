"""Build the downloadable PaperVault MCP Skill bundle."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills" / "papervault-search"
TARGET = ROOT / "web-vue" / "public" / "downloads" / "papervault-search-skill.zip"


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in SOURCE.iterdir() if path.is_file())
    with ZipFile(TARGET, "w", ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f"papervault-search/{path.name}")
    print(f"Wrote {TARGET} ({TARGET.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
