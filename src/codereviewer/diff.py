from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChangedLine:
    path: str
    old_line: int | None
    new_line: int | None
    side: str
    content: str


@dataclass
class FileDiff:
    old_path: str
    new_path: str
    patch: str = ""
    changed_lines: list[ChangedLine] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path if self.new_path != "/dev/null" else self.old_path

    @property
    def new_changed_lines(self) -> set[int]:
        return {line.new_line for line in self.changed_lines if line.new_line is not None}


@dataclass
class PullRequestDiff:
    files: list[FileDiff]
    raw: str

    @property
    def changed_line_count(self) -> int:
        return sum(len(file.changed_lines) for file in self.files)

    def file(self, path: str) -> FileDiff | None:
        return next((item for item in self.files if item.path == path), None)

    def inline_location(self, path: str, line: int) -> tuple[int, str] | None:
        file = self.file(path)
        if not file:
            return None
        # Model findings use new-file line numbers. Prefer RIGHT when an old and
        # new line share the same number inside a replacement hunk.
        for changed in file.changed_lines:
            if changed.side == "RIGHT" and changed.new_line == line:
                return line, "RIGHT"
        for changed in file.changed_lines:
            if changed.side == "LEFT" and changed.old_line == line:
                return line, "LEFT"
        return None


def _strip_git_prefix(path: str) -> str:
    path = path.strip()
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _parse_hunk_header(header: str) -> tuple[int, int]:
    # @@ -old_start,old_count +new_start,new_count @@
    parts = header.split()
    old_start = int(parts[1].split(",")[0][1:])
    new_start = int(parts[2].split(",")[0][1:])
    return old_start, new_start


def parse_unified_diff(raw: str) -> PullRequestDiff:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    current_patch: list[str] = []
    old_line = 0
    new_line = 0
    in_hunk = False

    def finish_current() -> None:
        nonlocal current, current_patch
        if current is not None:
            current.patch = "\n".join(current_patch)
            files.append(current)
        current = None
        current_patch = []

    for line in raw.splitlines():
        if line.startswith("diff --git "):
            finish_current()
            parts = line.split(" ", 3)
            current = FileDiff(
                old_path=_strip_git_prefix(parts[2]),
                new_path=_strip_git_prefix(parts[3]),
            )
            current_patch.append(line)
            in_hunk = False
            continue

        if current is None:
            continue

        current_patch.append(line)
        if line.startswith("--- "):
            current.old_path = _strip_git_prefix(line[4:].split("\t", 1)[0])
            continue
        if line.startswith("+++ "):
            current.new_path = _strip_git_prefix(line[4:].split("\t", 1)[0])
            continue
        if line.startswith("@@ "):
            old_line, new_line = _parse_hunk_header(line)
            in_hunk = True
            continue
        if not in_hunk or line.startswith("\\ No newline"):
            continue

        if line.startswith("+"):
            current.changed_lines.append(
                ChangedLine(current.path, None, new_line, "RIGHT", line[1:])
            )
            new_line += 1
        elif line.startswith("-"):
            current.changed_lines.append(
                ChangedLine(current.path, old_line, None, "LEFT", line[1:])
            )
            old_line += 1
        else:
            old_line += 1
            new_line += 1

    finish_current()
    return PullRequestDiff(files=files, raw=raw)
