import os
import re

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
# lockfiles and build output: lots of tokens, nothing worth reviewing
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", "uv.lock", "Cargo.lock", "go.sum"}
SKIP_ENDINGS = (".min.js", ".min.css", ".map")
MAX_DIFF_CHARS = 200_000  # about 50k tokens


def number_patch(patch):
    # put the new-file line number in front of each line, so claude copies numbers instead of counting.
    # also collect the lines github will accept a comment on (added and unchanged ones), with their code
    rows = patch.split("\n")  # not splitlines(), which also splits on characters that can appear inside code
    if rows and rows[-1] == "":
        rows.pop()
    out, lines, n = [], {}, None
    for raw in rows:
        m = HUNK.match(raw)
        if m:
            n = int(m.group(1))
            out.append(raw)
        elif n is None or raw.startswith("\\"):
            continue  # before the first hunk, or "\ No newline at end of file"
        elif raw.startswith("-"):
            out.append(f"{'':>5} - {raw[1:]}")
        else:
            mark = "+" if raw.startswith("+") else " "
            out.append(f"{n:>5} {mark} {raw[1:]}")
            lines[n] = raw[1:]
            n += 1
    return "\n".join(out), lines


def build(files):
    # returns (numbered diff for claude, {path: {line: code} we can comment on}, [paths left out])
    parts, commentable, skipped, used = [], {}, [], 0
    for f in files:
        name = f.filename
        if f.status == "removed" or not f.patch or os.path.basename(name) in SKIP_NAMES or name.endswith(SKIP_ENDINGS):
            skipped.append(name)
            continue
        text, lines = number_patch(f.patch)
        block = f"### {name} ({f.status})\n{text}\n"
        if used + len(block) > MAX_DIFF_CHARS:
            skipped.append(name)  # keep going, a smaller file later might still fit
            continue
        parts.append(block)
        commentable[name] = lines
        used += len(block)
    return "\n".join(parts), commentable, skipped


def split_findings(findings, commentable):
    # github rejects comments on lines outside the diff, so check every finding first.
    # the ones that can't go on a line still get mentioned in the summary comment
    inline, general = [], []
    for f in findings:
        lines = commentable.get(f.path, {})
        if f.line not in lines:
            general.append(f)
            continue
        if f.start_line > f.line or any(n not in lines for n in range(f.start_line, f.line + 1)):
            # the range runs outside the diff. comment on the last line only and drop the
            # suggestion, because it was written to replace the whole range
            f.start_line = f.line
            f.suggestion = ""
            f.also_needed = ""
        elif f.suggestion:
            widen(f, lines)
        inline.append(f)
    return inline, general


def widen(f, lines):
    # claude sometimes starts or ends a suggestion with an unchanged line from just outside the range.
    # stretch the range over it, otherwise that line shows up twice after "Commit suggestion"
    sug = [s.strip() for s in f.suggestion.rstrip("\n").split("\n")]
    if len(sug) > f.line - f.start_line + 1 and sug[0] and sug[0] == lines.get(f.start_line - 1, "").strip():
        f.start_line -= 1
    if len(sug) > f.line - f.start_line + 1 and sug[-1] and sug[-1] == lines.get(f.line + 1, "").strip():
        f.line += 1
