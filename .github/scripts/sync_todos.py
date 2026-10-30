import os
import re
import subprocess


def git(*args):
    return subprocess.check_output(["git", *args], text=True, stderr=subprocess.STDOUT)


# Only reconcile files changed between main and dev (main-to-dev changes).
merge_base = git("merge-base", "origin/main", "HEAD").strip()
changed = [
    f for f in git("diff", "--name-only", merge_base, "HEAD").splitlines()
    if f.endswith(".py")
]
print("Changed Python files between main and dev:", changed)

README = "README.md"
with open(README, encoding="utf-8") as fh:
    readme = fh.read()
lines = readme.split("\n")

bullet_re = re.compile(r"^- \[ \] \*\*(?P<path>[^*]+):(?P<line>\d+)\*\* - (?P<text>.*)$")
todo_re = re.compile(r"^\s*#\s*TODO\b(?P<rest>.*)$")


def extract_todos(path):
    found = []
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            m = todo_re.match(raw.rstrip("\n"))
            if not m:
                continue
            body = m.group("rest").strip()
            if body.startswith(":"):
                body = body[1:].lstrip()
            found.append((path, lineno, body))
    return found


# Locate the contiguous bullet block under "### 📝 Complete TODO List".
block_start = None
block_end = None
existing = []
for idx, line in enumerate(lines):
    m = bullet_re.match(line)
    if not m:
        continue
    if block_start is None:
        block_start = idx
    block_end = idx
    existing.append((m.group("path"), int(m.group("line")), m.group("text")))

changed_set = set(changed)
kept = [(p, l, t) for (p, l, t) in existing if p not in changed_set]

fresh = []
for path in changed:
    if os.path.exists(path):
        fresh.extend(extract_todos(path))

entries = kept + fresh
entries.sort(key=lambda r: (r[0], r[1]))
new_bullets = [f"- [ ] **{p}:{l}** - {t}" for (p, l, t) in entries]

new_lines = lines[:block_start] + new_bullets + lines[block_end + 1:]
new_readme = "\n".join(new_lines)

if new_readme != readme:
    with open(README, "w", encoding="utf-8") as fh:
        fh.write(new_readme)
    print("README.md updated; total bullets:", len(new_bullets))
else:
    print("README.md unchanged; total bullets:", len(new_bullets))
