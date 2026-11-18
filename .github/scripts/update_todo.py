#!/usr/bin/env python3
"""Update the README "Complete TODO List" from Python TODO markers.

Only the TODO additions/removals/line updates introduced by the changes between
the base branch (main) and the current HEAD (dev) are applied. Python files that
did not change in that diff keep their README entries verbatim, so the update is
strictly scoped to the main->dev changes.
"""
import os
import re
import sys
import subprocess
import hashlib
import traceback

REPO_ROOT = os.getcwd()
README = "README.md"
# "### 📝 Complete TODO List" (written with an escape to keep the source ASCII-safe)
TITLE = "### \U0001f4dd Complete TODO List"

EXCLUDE_DIRS = {
    ".git", ".github", "__pycache__", "node_modules", ".venv", "venv",
    ".playwright_browsers", ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
}

ENTRY_RE = re.compile(r'^- \[ \] \*\*(?P<path>.+?):(?P<line>\d+)\*\* - (?P<msg>.*)$')
TODO_RE = re.compile(r'\bTODO\b', re.IGNORECASE)
# Text after the leading '#' (and the TODO token). Mirrors the existing README format:
#   '# TODO: x' -> 'x' ; '# TODO(sgm): x' -> '(sgm): x' ; '# TODO (sgm): x' -> '(sgm): x'
TODO_PREFIX_RE = re.compile(r'^\s*TODO\s*:?\s*(.*?)\s*$', re.IGNORECASE)


def run(cmd, check=True):
    p = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(
            "cmd failed ({}): {}\nSTDOUT:\n{}\nSTDERR:\n{}".format(
                p.returncode, " ".join(cmd), p.stdout, p.stderr))
    return p


def is_relevant_py(path):
    if not path.endswith(".py"):
        return False
    top = path.split("/", 1)[0]
    if top in (".github", ".git"):
        return False
    return True


def extract_todos(path):
    with open(path, "rb") as f:
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    out = []
    for i, line in enumerate(text.split("\n"), 1):
        idx = line.find("#")
        if idx == -1:
            continue
        comment = line[idx + 1:]
        if TODO_RE.search(comment):
            m = TODO_PREFIX_RE.match(comment)
            msg = m.group(1) if m else comment.strip()
            out.append((i, msg))
    return out


def scan_all_py():
    scanned = {}
    for dp, dn, fn in os.walk(REPO_ROOT):
        dn[:] = [d for d in dn if d not in EXCLUDE_DIRS]
        for name in fn:
            if not name.endswith(".py"):
                continue
            full = os.path.join(dp, name)
            rel = os.path.relpath(full, REPO_ROOT).replace(os.sep, "/")
            todos = extract_todos(full)
            if todos:
                scanned[rel] = sorted(todos, key=lambda t: t[0])
    return scanned


def resolve_base():
    # Prefer origin/main, fall back to local refs, then the immediate parent (main is an ancestor).
    candidates = ["origin/main", "main", "refs/heads/main", "HEAD~1"]
    for ref in candidates:
        p = run(["git", "rev-parse", "--verify", ref], check=False)
        if p.returncode == 0:
            return ref
    raise RuntimeError("could not resolve a base branch (main)")


def get_changed_py(base):
    """Return (changed, deleted) sets of relevant .py paths between base and HEAD."""
    p = run(["git", "diff", "--name-status", base, "HEAD"])
    changed, deleted = set(), set()
    for raw in p.stdout.splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        status = parts[0][0]
        if status in ("A", "M"):
            if is_relevant_py(parts[1]):
                changed.add(parts[1])
        elif status == "D":
            if is_relevant_py(parts[1]):
                deleted.add(parts[1])
        elif status in ("R", "C"):
            old, new = parts[1], parts[2]
            if is_relevant_py(old):
                deleted.add(old)
            if is_relevant_py(new):
                changed.add(new)
    return changed, deleted


def parse_entries(text):
    entries = []
    for i, line in enumerate(text.split("\n")):
        m = ENTRY_RE.match(line)
        if m:
            entries.append({"idx": i, "path": m.group("path"),
                            "line": int(m.group("line")), "msg": m.group("msg")})
    return entries


def build_new_readme(readme_text, scanned, changed, deleted):
    lines = readme_text.splitlines(keepends=True)
    title_idx = None
    for i, l in enumerate(lines):
        if l.rstrip("\r\n") == TITLE:
            title_idx = i
            break
    if title_idx is None:
        raise AssertionError("Title not found in README: " + TITLE)
    if sum(1 for l in lines if l.rstrip("\r\n") == TITLE) != 1:
        raise AssertionError("Title must appear exactly once")

    # next level-2 heading (e.g. "## 🤝 Contributing") terminates the section
    next_h = len(lines)
    for j in range(title_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            next_h = j
            break

    entry_idxs = [k for k in range(title_idx + 1, next_h) if ENTRY_RE.match(lines[k])]
    if not entry_idxs:
        raise AssertionError("No TODO list entries found under the title")
    s, e = entry_idxs[0], entry_idxs[-1]
    # The list is a contiguous block of entries (no blank/other lines inside).
    for k in range(s, e + 1):
        if not ENTRY_RE.match(lines[k]):
            raise AssertionError("Unexpected non-entry line inside TODO list at line {}".format(k + 1))

    baseline = parse_entries(readme_text)
    if baseline[0]["idx"] != s or baseline[-1]["idx"] != e:
        raise AssertionError("Entry indices mismatch after parsing")
    keys = [(x["path"], x["line"]) for x in baseline]
    if keys != sorted(keys):
        raise AssertionError("Baseline README TODO list is not sorted by (path, line)")

    groups = {}
    for en in baseline:
        groups.setdefault(en["path"], []).append(en)

    final = {}
    # 1) keep unchanged path groups verbatim
    for path, ens in groups.items():
        if path in changed or path in deleted:
            continue
        final[path] = [(x["line"], x["msg"]) for x in ens]
    # 2) refresh added/modified paths from the working tree (sorted by line)
    for path in changed:
        if path in deleted:
            continue
        if path not in scanned:
            raise AssertionError("Changed file not found on disk / scanned: {}".format(path))
        final[path] = list(scanned[path])
    # 3) deleted paths are simply omitted

    # render with lexicographic path ordering, ascending line numbers within a file
    rendered = []
    for path in sorted(final.keys()):
        for ln, msg in sorted(final[path], key=lambda x: x[0]):
            rendered.append("- [ ] **{}:{}** - {}\n".format(path, ln, msg))
    block = "".join(rendered)

    new_text = "".join(lines[:s]) + block + "".join(lines[e + 1:])

    # ---- validations ----
    if new_text.count(TITLE) != 1:
        raise AssertionError("Title changed count after edit")
    res = parse_entries(new_text)
    rkeys = [(x["path"], x["line"]) for x in res]
    if rkeys != sorted(rkeys):
        raise AssertionError("Resulting TODO list is not sorted")
    # unchanged file groups must be byte-identical to baseline
    for path, ens in groups.items():
        if path in changed or path in deleted:
            continue
        base_lines = ["- [ ] **{}:{}** - {}".format(path, x["line"], x["msg"]) for x in ens]
        new_lines = ["- [ ] **{}:{}** - {}".format(path, x[0], x[1]) for x in final[path]]
        if base_lines != new_lines:
            raise AssertionError("Unchanged file {} was modified unexpectedly".format(path))
    return new_text, baseline, res


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def report_changed_files(base, scanned, changed, deleted, baseline):
    by_path = {}
    for en in baseline:
        by_path.setdefault(en["path"], []).append(en)
    print("Base ref:", base, " HEAD:", run(["git", "rev-parse", "HEAD"]).stdout.strip())
    print("Changed .py (A/M/R):", sorted(changed))
    print("Deleted .py (D/R):  ", sorted(deleted))
    for path in sorted(set(list(changed) + list(deleted))):
        old = by_path.get(path, [])
        new = scanned.get(path, [])
        print("\n### {}".format(path))
        print("  baseline count: {}  scanned count: {}".format(len(old), len(new)))
        old_by_msg = {}
        for o in old:
            old_by_msg.setdefault(o["msg"], []).append(o["line"])
        new_by_msg = {}
        for ln, msg in new:
            new_by_msg.setdefault(msg, []).append(ln)
        for msg in sorted(set(old_by_msg) | set(new_by_msg)):
            ol = old_by_msg.get(msg)
            nl = new_by_msg.get(msg)
            if ol and nl:
                tag = "LINE-UPDATE" if ol != nl else "SAME"
                print("   [{:11}] {} : {} -> {} | {}".format(tag, path, ol, nl, msg[:90]))
            elif ol:
                print("   [{:11}] {} : {} (removed) | {}".format("REMOVED", path, ol, msg[:90]))
            else:
                print("   [{:11}] {} : {} (added)   | {}".format("ADDED", path, nl, msg[:90]))


def main():
    dry_run = os.environ.get("INPUT_DRY_RUN", "false").strip().lower() in ("1", "true", "yes")
    print("dry_run =", dry_run)

    # Make sure the base branch (main) is available for diffing.
    run(["git", "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*", "--no-tags"], check=False)
    base = resolve_base()

    changed, deleted = get_changed_py(base)
    scanned = scan_all_py()

    with open(os.path.join(REPO_ROOT, README), "r", encoding="utf-8") as f:
        readme_text = f.read()

    report_changed_files(base, scanned, changed, deleted, parse_entries(readme_text))

    new_text, baseline, res = build_new_readme(readme_text, scanned, changed, deleted)

    print("\nTODO entries: {} (baseline) -> {} (result)".format(len(baseline), len(res)))
    data = new_text.encode("utf-8")
    print("README blob sha:", blob_sha(data))

    if new_text == readme_text:
        print("No README changes needed.")
        return 0

    # Show the exact README diff for audit (computed in-memory so a dry run leaves the tree clean).
    import difflib
    diff = difflib.unified_diff(
        readme_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile="a/" + README, tofile="b/" + README, n=3)
    print("\n----- diff for {} -----".format(README))
    print("".join(diff))
    print("---------------------------\n")

    if dry_run:
        print("DRY RUN: not writing, committing, or pushing.")
        return 0

    with open(os.path.join(REPO_ROOT, README), "w", encoding="utf-8", newline="") as f:
        f.write(new_text)

    # Commit and push (uses GITHUB_TOKEN configured by actions/checkout).
    run(["git", "add", README])
    msg = ("docs: update Complete TODO List in README [skip ci]\n\n"
           "Scanned .py files for TODO markers and applied only the additions,\n"
           "removals and line-number updates introduced by main->dev changes.\n"
           "Title '### \\U0001f4dd Complete TODO List' preserved.")
    run(["git", "commit", "-m", msg])
    branch = os.environ.get("GITHUB_REF_NAME") or run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    print("Pushing to branch:", branch)
    run(["git", "push", "origin", "HEAD:refs/heads/{}".format(branch)])
    head = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    print("Committed:", head)
    print("Post-push README blob sha:", blob_sha(data))
    print(run(["git", "--no-pager", "show", "--stat", "HEAD"], check=False).stdout)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
