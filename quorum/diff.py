"""Minimal unified-diff parser. Extracts added lines (with new-file line
numbers) per file, plus light classification (language / test / doc)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

EXT_LANG = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".java": "java", ".kt": "kotlin",
    ".go": "go", ".rb": "ruby", ".rs": "rust", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cs": "csharp", ".php": "php",
    ".swift": "swift", ".dart": "dart", ".sql": "sql", ".sh": "bash",
    ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".md": "markdown",
    ".html": "html", ".css": "css", ".scss": "css",
}
DOC_EXT = {".md", ".rst", ".txt", ".adoc"}
TEST_HINTS = ("test_", "_test.", "/tests/", "tests/", ".test.", ".spec.", "spec/")

_HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass
class FileDiff:
    path: str
    added: list = field(default_factory=list)  # list of (lineno, code)

    @property
    def ext(self) -> str:
        i = self.path.rfind(".")
        return self.path[i:].lower() if i >= 0 else ""

    @property
    def language(self) -> str:
        return EXT_LANG.get(self.ext, "text")

    @property
    def is_doc(self) -> bool:
        return self.ext in DOC_EXT

    @property
    def is_test(self) -> bool:
        p = self.path.lower()
        return any(h in p for h in TEST_HINTS)

    @property
    def added_text(self) -> str:
        return "\n".join(code for _, code in self.added)


def parse_diff(text: str) -> list[FileDiff]:
    files: dict[str, FileDiff] = {}
    current: FileDiff | None = None
    newline = 0
    for line in text.splitlines():
        if line.startswith("diff --git"):
            current = None
            continue
        if line.startswith("+++ "):
            path = line[4:].strip().split("\t")[0]
            if path.startswith("b/"):
                path = path[2:]
            if path == "/dev/null":
                current = None
                continue
            current = files.setdefault(path, FileDiff(path=path))
            continue
        if line.startswith("--- "):
            continue
        m = _HUNK_RE.match(line)
        if m:
            newline = int(m.group(1))
            continue
        if current is None:
            continue
        if line.startswith("+"):
            current.added.append((newline, line[1:]))
            newline += 1
        elif line.startswith("-"):
            continue  # removed line: does not advance the new-file counter
        else:
            newline += 1  # context line
    return [f for f in files.values() if f.added]
