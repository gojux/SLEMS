"""Table of contents of the user guides in docs/guide/.

A guide gets a table of contents between the markers ``<!-- toc -->`` and
``<!-- /toc -->`` (the longer guides have them, below the navigation line). It
lists the headings of level 2 and 3 with GitHub's anchors.

    python3 dev/guide_toc.py          # update the tables of contents
    python3 dev/guide_toc.py --check  # exit 1 if one is out of date
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

GUIDE = Path(__file__).resolve().parent.parent / "docs" / "guide"
START, END = "<!-- toc -->", "<!-- /toc -->"


def slug(heading: str) -> str:
    """GitHub's anchor of a heading."""
    text = re.sub(r"`|\*\*|\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def toc(markdown: str) -> str:
    lines = []
    seen: dict[str, int] = {}
    in_code = False
    for line in markdown.split("\n"):
        if line.startswith("```"):
            in_code = not in_code
            continue
        match = None if in_code else re.match(r"^(#{1,6}) (.+?)\s*$", line)
        if not match:
            continue
        anchor = slug(match.group(2))
        count = seen.get(anchor, 0)
        seen[anchor] = count + 1
        if count:
            anchor = f"{anchor}-{count}"
        level = len(match.group(1))
        if level in (2, 3):
            lines.append(f"{'  ' * (level - 2)}- [{match.group(2)}](#{anchor})")
    return "\n".join(lines)


def updated(markdown: str) -> str | None:
    """The guide with a fresh table of contents (None: it has no markers)."""
    if START not in markdown or END not in markdown:
        return None
    head, rest = markdown.split(START, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{START}\n\n{toc(markdown)}\n\n{END}{tail}"


def main() -> int:
    check = "--check" in sys.argv[1:]
    stale = []
    for path in sorted(GUIDE.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        new = updated(text)
        if new is None or new == text:
            continue
        stale.append(path.name)
        if not check:
            path.write_text(new, encoding="utf-8")
    if stale:
        print(("Out of date: " if check else "Updated: ") + ", ".join(stale))
    return 1 if check and stale else 0


if __name__ == "__main__":
    sys.exit(main())
