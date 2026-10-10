"""The registry-write boundary, checked from source (PUBLISHING §22).

Two structural checks back the run-time RegistryGate:

- `scan_repo`: every construct in dogear/ and tools/ (at any depth) that could
  write storage, reach the gate's raw store, reflect, rebind or subclass the
  protected surface, or import a module not reviewed before. Each finding is
  one occurrence, and the set must equal the reviewed list exactly.
- `surface`: a fingerprint of each region of the critical surface (the gate,
  the guarded registry write and its guards, the firmware capability, the drill
  writer, and this scanner itself). A change there needs an explicit review
  (a new fingerprint) even when it uses a reflection trick nothing here knows.

Both read local files only, and are deterministic across Python 3.9 to 3.14
(a canonical form of the syntax tree, not ast.dump or ast.unparse)."""

from __future__ import annotations

import ast
import collections
import copy
import hashlib
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# The scanned trees, at any depth. tests/ is not scanned: its probes are the bypasses.
SCANNED = ("dogear", "tools")

WRITE_METHODS = frozenset({"put", "allow_once", "_request"})
# Names that reach the gate's raw store or its authorisation.
PRIVATE_NAMES = frozenset({"_store", "_allowed", "_RegistryGate__store", "_RegistryGate__allowed", "__store",
                           "__allowed"})
# Every dunder is reflection (__get__, __dict__, __class__, __closure__, __traceback__, ...)
# except these, which name a module or its documentation.
BENIGN_DUNDERS = frozenset({"__name__", "__main__", "__file__", "__future__", "__all__", "__doc__", "__version__"})
# Builtins that look up, set or run something by a computed name. Flagged on any
# reference, called or not, bare or qualified (builtins.getattr, alias = vars).
REFLECTIVE_BUILTINS = frozenset({"getattr", "setattr", "delattr", "hasattr", "vars", "dir", "eval", "exec",
                                 "compile", "globals", "locals", "breakpoint"})
# ... except as an attribute of something else, where these two are ordinary (re.compile).
ORDINARY_ATTRS = frozenset({"compile", "dir"})
# Other routes to private state or to frames (whose locals hold the raw store),
# by name wherever they appear: a module's re-export (dataclasses.builtins), a
# formatter that looks up computed fields, the gc graph, tracing hooks, frames.
REFLECTIVE_NAMES = frozenset({
    "builtins", "Formatter", "get_field", "attrgetter", "methodcaller",
    "get_referents", "get_referrers", "get_objects", "getattr_static", "getmembers", "currentframe",
    "_getframe", "settrace", "setprofile", "monitoring", "exc_info", "last_traceback", "last_value", "last_exc",
    "_current_frames", "modules", "f_locals", "f_back", "f_globals", "f_builtins", "tb_frame", "tb_next",
    "gi_frame", "cr_frame", "ag_frame", "cell_contents", "executable",
    "system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp", "execv", "execve", "execvp", "execvpe",
    "execl", "execle", "execlp", "execlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl", "spawnle",
    "spawnlp", "spawnlpe"})
# Modules whose every import is a finding: reflection, serialisation of live
# objects, code execution, other processes, the network.
SENSITIVE_MODULES = frozenset({
    "gc", "inspect", "ctypes", "operator", "importlib", "functools", "copyreg", "pickle", "marshal", "builtins",
    "string", "traceback", "types", "dis", "code", "codeop", "runpy", "pdb", "bdb", "trace", "shelve",
    "multiprocessing", "concurrent", "threading", "_thread", "weakref", "unittest", "sys",
    "subprocess", "pty", "socket", "ssl", "urllib", "http", "ftplib", "smtplib"})
# Every other module imported anywhere in the scanned trees; an import of any
# module outside this list (or the package itself) is a finding.
KNOWN_MODULES = frozenset({"__future__", "argparse", "collections", "copy", "dataclasses", "datetime", "dogear",
                           "hashlib", "hmac", "html", "json", "os", "pathlib", "posixpath", "re", "secrets",
                           "shutil", "tempfile", "typing", "unicodedata", "zoneinfo"})
# The critical surface: (file, region). A region is a module-level class,
# function or assigned name, "Class.method", "<imports>" (every module-level
# import of that file), "<bindings>" (every binding of a name the file's
# protected code uses, however nested: see binding_table) or "" (the whole
# module). Its fingerprint is in write_boundary_review.PROTECTED, and every
# package name a region uses is itself on the surface or reviewed as not
# critical (write_boundary_review.NOT_CRITICAL; see dependencies).
SURFACE = (
    ("dogear/publish/publisher.py", "<imports>"),
    ("dogear/publish/publisher.py", "<bindings>"),
    ("dogear/publish/publisher.py", "UnguardedRegistryWrite"),
    ("dogear/publish/publisher.py", "is_registry_key"),
    ("dogear/publish/publisher.py", "_GATE_API"),
    ("dogear/publish/publisher.py", "RegistryGate"),
    ("dogear/publish/publisher.py", "Publisher.__init__"),
    ("dogear/publish/publisher.py", "Publisher._quiet_capability"),
    ("dogear/publish/publisher.py", "Publisher._revision_is_quiet"),
    ("dogear/publish/publisher.py", "Publisher._quiet_presentation_guard"),
    ("dogear/publish/publisher.py", "Publisher._quiet_shown_without_capability"),
    ("dogear/publish/publisher.py", "Publisher._all"),
    ("dogear/publish/publisher.py", "Publisher._upload"),
    ("dogear/publish/publisher.py", "Publisher._transact"),
    ("dogear/publish/publisher.py", "Publisher._control"),
    # what a write makes visible, and the guards every write re-runs (eligibility,
    # separation, the Manila week, visible revisions), and the operations that
    # choose which guards a write gets
    ("dogear/publish/publisher.py", "shown_revisions"),
    ("dogear/publish/publisher.py", "Publisher._read_registry"),
    ("dogear/publish/publisher.py", "Publisher._dataset_text"),
    ("dogear/publish/publisher.py", "Publisher._week_guard"),
    ("dogear/publish/publisher.py", "Publisher._separation_problem"),
    ("dogear/publish/publisher.py", "Publisher._eligibility_guard"),
    ("dogear/publish/publisher.py", "Publisher._visible_revisions_guard"),
    ("dogear/publish/publisher.py", "Publisher.promote"),
    ("dogear/publish/publisher.py", "Publisher.launch"),
    ("dogear/publish/publisher.py", "Publisher.correct"),
    ("dogear/publish/publisher.py", "Publisher.rollback"),
    ("dogear/publish/publisher.py", "Publisher.restore"),
    ("dogear/publish/publisher.py", "Publisher.resume"),
    # production readiness: refused while the policy is unset, and quiet-week
    # refused while the firmware cannot present quiet issues
    ("dogear/publish/publisher.py", "Publisher._ready"),
    ("dogear/publish/publisher.py", "NotReady"),
    ("dogear/publish/publisher.py", "Aborted"),
    ("dogear/publish/publisher.py", "NeedsHuman"),
    ("dogear/publish/policy.py", ""),
    ("dogear/publish/publisher.py", "rollover_week"),
    ("dogear/publish/publisher.py", "ROLLOVER"),
    # the registry model (which revision is active, which week current), eligibility,
    # the week arithmetic, and the checks the guards call in other modules
    ("dogear/publish/registry.py", ""),
    ("dogear/publish/eligibility.py", ""),
    ("dogear/week.py", ""),
    ("dogear/dataset.py", ""),  # records and their approvals, which eligibility compares
    ("dogear/affinity.py", "<bindings>"),
    ("dogear/affinity.py", "normalize"),
    ("dogear/publish/edition.py", ""),
    ("dogear/synthetic.py", ""),
    ("dogear/quiet.py", ""),
    ("dogear/select.py", "<bindings>"),
    ("dogear/select.py", "MAX_ENTRIES"),
    ("dogear/select.py", "MIN_SIGNIFICANCE"),
    ("dogear/select.py", "CTA_LABELS"),
    # the quiet source (pool, full-item and subject exclusions) from complete,
    # validated history, and the pre-promotion verification of a staged week
    ("dogear/publish/publisher.py", "Publisher._quiet_source"),
    ("dogear/publish/quiet_history.py", ""),
    ("dogear/publish/pubdata.py", ""),
    ("dogear/publish/staging.py", "<bindings>"),
    ("dogear/publish/staging.py", "verify_staged"),
    ("dogear/publish/staging.py", "Staged"),
    ("dogear/publish/staging.py", "StageInputs"),
    ("dogear/publish/staging.py", "Frozen"),
    ("dogear/publish/staging.py", "StageRefused"),
    ("dogear/publish/staging.py", "WeekHeld"),
    ("dogear/publish/staging.py", "_raw_records"),
    ("dogear/publish/staging.py", "artifact_problems"),
    ("dogear/publish/staging.py", "device_limit_problems"),
    ("dogear/publish/staging.py", "ALLOWED_CTA"),
    ("dogear/publish/staging.py", "MAX_ENTRIES"),
    ("dogear/publish/staging.py", "provenance_bytes"),
    ("dogear/publish/staging.py", "Checks"),  # production needs its link and fit checkers
    ("dogear/publish/firmware.py", ""),
    # where a write goes: the storage clients, the drill wrapper, and the CLI's
    # target -> (store, host, mode, policy, dataset, separation) -> Publisher handoff
    ("dogear/publish/r2.py", ""),
    ("dogear/publish/store.py", ""),
    ("dogear/publish/drills.py", ""),
    ("dogear/publish/cli.py", "<bindings>"),
    ("dogear/publish/cli.py", "ROOT"),
    ("dogear/publish/cli.py", "PRODUCTION_HOST"),
    ("dogear/publish/cli.py", "TARGETS"),
    ("dogear/publish/cli.py", "_generator"),
    ("dogear/publish/cli.py", "_store"),
    ("dogear/publish/cli.py", "_apply_target"),
    ("dogear/publish/cli.py", "_pairing_problem"),
    ("dogear/publish/cli.py", "_configure"),
    ("tests/write_boundary.py", ""),
    ("tests/test_firmware_capability.py", "RegistryWriteBoundaryTests"),
    ("tests/test_firmware_capability.py", "SourceReviewGuardTests"),
    ("tests/test_firmware_capability.py", "BindingAndSurfaceTests"),
    ("tests/test_firmware_capability.py", "NESTED_REBINDING"),
    ("tests/test_firmware_capability.py", "LAMBDA_REBINDING"),
    ("tests/test_firmware_capability.py", "CliHandoffTests"),
    ("tests/test_firmware_capability.py", "QuietVerificationSurfaceTests"),
    ("tests/test_firmware_capability.py", "BYPASS"),
    ("tests/test_firmware_capability.py", "QUALIFIED_BYPASS"),
    ("tests/test_firmware_capability.py", "BYPASS_VARIANTS"),
    ("tests/test_firmware_capability.py", "NESTED_WRITER"),
)
# What reaches storage or a publisher: every reference (import, call, alias) is a
# finding, so the CLI's checked handoff (_configure) stays the only route to them.
CONSTRUCTORS = frozenset({"Publisher", "R2Store"})
# Classes whose behaviour the surface fixes: a subclass anywhere could override it.
FINAL_CLASSES = frozenset({"Publisher", "RegistryGate", "DrillStore"})
# Attributes that, rebound or deleted from anywhere (module.name = ..., Class.method = ...,
# obj.store = ...), would change the surface without changing its source.
EXTRA_PROTECTED = frozenset({"store", "_presentation_checked", "firmware", "regmod", "registry", "posixpath"})

SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
BLOCKS = ("body", "orelse", "finalbody", "handlers", "cases")
Finding = collections.namedtuple("Finding", "file scope kind detail at statement n")
# statement id -> its text, for messages and for writing the reviewed list
STATEMENTS = {}


def canon(node) -> str:
    """A canonical spelling of a syntax tree: node types and field values, with
    no positions and no empty or absent fields, so the same code gives the same
    text on every Python this runs on (3.9 to 3.14); comments and formatting
    are not part of it."""
    if isinstance(node, ast.AST):
        fields = [f"{name}={canon(value)}" for name, value in ast.iter_fields(node)
                  if value is not None and value != []]
        return f"{type(node).__name__}({', '.join(fields)})"
    if isinstance(node, list):
        return "[" + ", ".join(canon(item) for item in node) + "]"
    return repr(node)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _header(stmt):
    """A statement without its nested blocks (their statements are their own)."""
    head = copy.copy(stmt)
    for name in BLOCKS:
        if getattr(head, name, None):
            setattr(head, name, [])
    return head


def _statement_id(stmt) -> str:
    head = _header(stmt)
    sid = digest(canon(head))[:16]
    STATEMENTS.setdefault(sid, " ".join(ast.unparse(head).split()))
    return sid


def _bound_name(stmt):
    if isinstance(stmt, SCOPES):
        return stmt.name
    targets = stmt.targets if isinstance(stmt, ast.Assign) else \
        [stmt.target] if isinstance(stmt, ast.AnnAssign) else []
    if len(targets) == 1 and isinstance(targets[0], ast.Name):
        return targets[0].id
    return None


def _step(parent, field: str, index: int, stmt) -> str:
    """One step of a finding's position. In a function, the statement's index in
    its block: order there is reviewed (a write stays after its guards). At the
    top of a module or class, where order is declarative, the name the statement
    binds (or "-", leaving its statement and n to identify it), so adding an
    unrelated import or constant above does not move it."""
    if not isinstance(parent, (ast.Module, ast.ClassDef)):
        return f"{field}[{index}]"
    return _bound_name(stmt) or "-"


def _protected_names() -> frozenset:
    names = set(EXTRA_PROTECTED)
    for rel, region in SURFACE:
        if rel.startswith("tests/"):
            continue
        if region == "":
            names.update(regions((REPO / rel).read_text(encoding="utf-8"))[1])
        elif not region.startswith("<"):
            last = region.rsplit(".", 1)[-1]
            if not last.startswith("__"):
                names.add(last)
    return frozenset(names)


def _identifiers(node):
    """(role, text) for every identifier-like string a node carries."""
    if isinstance(node, ast.Attribute):
        yield "attr", node.attr
    elif isinstance(node, ast.Name):
        yield "name", node.id
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield "string", node.value
    elif isinstance(node, ast.keyword) and node.arg:
        yield "keyword", node.arg
    elif isinstance(node, ast.alias):
        yield "import-name", node.name
        if node.asname:
            yield "import-name", node.asname
    elif type(node).__name__ == "MatchClass":  # 3.10+: case C(attr=...) looks attributes up
        for attr in node.kwd_attrs:
            yield "pattern", attr


def scan_source(rel: str, source: str, protected: frozenset = None) -> set:
    """Every construct in one module that could write storage, reach the gate's
    raw store, or change the protected surface from outside, read from the
    syntax tree (not its spelling). Kinds:
      write-call / write-ref  a write method, called directly or only referenced
                              (`writer = store.put`; partial(store.put))
      write-name, private-name, private-attr
                              those names anywhere: strings, keywords, imports, patterns
      dynamic                 getattr/setattr/vars/eval/...: any reference, bare or qualified
                              (builtins.getattr(...), alias = vars), and type() with 3 arguments
      reflection              any dunder (__get__, __dict__, __class__, ...) and the names
                              in REFLECTIVE_NAMES (frames, tracing, Formatter, attrgetter, ...)
      import                  any import of a sensitive module
      module                  an import of a module nobody imported before
      rebind                  assigning or deleting a protected name as an attribute
                              (publisher.is_registry_key = ..., Publisher._transact = ...)
      subclass                a subclass of Publisher, RegistryGate or DrillStore
      construct               any reference to Publisher or R2Store (the routes to a publisher
                              and to production storage)
      rebinding               in a module on the surface, a second binding of a name its
                              protected code depends on, in any form and however deeply
                              nested in its execution flow (see rebindings)

    Each finding is one occurrence, identified structurally: file, enclosing
    class/function, kind, detail, `at` (its statement's position inside that
    scope, see _step: unrelated code moving elsewhere does not move it, but
    reordering the scope's own statements does), the statement (a digest of its
    canonical form) and `n` (which identical occurrence at that position). A
    second occurrence anywhere, even inside an approved scope or statement, is
    a new finding: nothing collapses into an existing approval."""
    protected = _protected_names() if protected is None else protected
    raw = []

    def note(scope, at, stmt, kind, detail):
        raw.append((rel, ".".join(scope), kind, detail, "/".join(at), _statement_id(stmt)))

    def walk(node, scope, at, stmt):
        for field, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            for index, child in enumerate(children):
                if not isinstance(child, ast.AST):
                    continue
                if isinstance(child, ast.stmt):
                    child_at = at + [_step(node, field, index, child)]
                    if isinstance(child, SCOPES):  # a new scope: positions restart inside it
                        visit(child, node, scope, child_at, child)
                        walk(child, scope + [child.name], [], child)
                        continue
                    visit(child, node, scope, child_at, child)
                    walk(child, scope, child_at, child)
                else:
                    visit(child, node, scope, at, stmt)
                    walk(child, scope, at, stmt)

    def visit(child, parent, scope, at, stmt):
        called = isinstance(parent, ast.Call) and parent.func is child
        for role, text in _identifiers(child):
            shown = ast.unparse(parent) if called else (ast.unparse(child) if role in ("attr", "name") else text)
            if text in WRITE_METHODS:
                if role == "attr":
                    note(scope, at, stmt, "write-call" if called else "write-ref", ast.unparse(child))
                elif role != "name":
                    note(scope, at, stmt, "write-name", text)
            if text in PRIVATE_NAMES or "_RegistryGate" in text:
                note(scope, at, stmt, "private-attr" if role == "attr" else "private-name", shown)
            if (text.startswith("__") and text.endswith("__") and len(text) > 4 and text not in BENIGN_DUNDERS) \
                    or text in REFLECTIVE_NAMES:
                note(scope, at, stmt, "reflection", shown)
            if text in REFLECTIVE_BUILTINS and not (role == "attr" and text in ORDINARY_ATTRS) \
                    and role != "keyword":
                note(scope, at, stmt, "dynamic", shown)
            if text in CONSTRUCTORS and role in ("attr", "name", "import-name", "string"):
                note(scope, at, stmt, "construct", shown)
            if role == "attr" and text in protected and isinstance(child.ctx, (ast.Store, ast.Del)):
                note(scope, at, stmt, "rebind", ast.unparse(child))
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "type" \
                and len(child.args) + len(child.keywords) > 1:
            note(scope, at, stmt, "dynamic", ast.unparse(child))  # a class made at run time
        if isinstance(child, ast.ClassDef):
            for base in child.bases + [k.value for k in child.keywords]:
                name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", None)
                if name in FINAL_CLASSES or not isinstance(base, (ast.Name, ast.Attribute)):
                    note(scope, at, stmt, "subclass", ast.unparse(base))
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            if isinstance(child, ast.ImportFrom) and child.level:
                return  # the package itself
            names = [a.name for a in child.names] if isinstance(child, ast.Import) else [child.module or ""]
            for name in names:
                top = name.split(".")[0]
                if top in SENSITIVE_MODULES:
                    note(scope, at, stmt, "import", name)
                elif top not in KNOWN_MODULES:
                    note(scope, at, stmt, "module", name)

    tree = ast.parse(source)
    walk(tree, [], [], None)
    for scope, name, kind, at, stmt in rebindings(rel, tree):
        raw.append((rel, scope, "rebinding", f"{name} ({kind})", at, _statement_id(stmt)))
    seen = collections.Counter()
    found = set()
    for key in raw:
        seen[key] += 1
        found.add(Finding(*key, seen[key]))
    return found


def discover(root: Path = REPO) -> dict:
    """{path: source} for every Python module beneath dogear/ and tools/, however
    deeply nested: each *.py file, and each extensionless script whose shebang
    names python. Fails closed on a symlink anywhere, the roots included (what
    it points at is not reviewed here), and on a missing root."""
    sources = {}
    for top in SCANNED:
        base = root / top
        if os.path.islink(base):
            raise AssertionError(f"{top} is a symlink; the scan does not follow it")
        if not base.is_dir():
            raise AssertionError(f"{top} is missing; nothing to scan")
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            for name in dirnames + filenames:
                if os.path.islink(os.path.join(dirpath, name)):
                    raise AssertionError(f"{Path(dirpath, name).relative_to(root)} is a symlink; the scan does not "
                                         "follow it")
            for name in sorted(filenames):
                path = Path(dirpath, name)
                if path.suffix == ".py" or (not path.suffix and _python_script(path)):
                    sources[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return sources


def _python_script(path: Path) -> bool:
    with path.open("rb") as f:
        first = f.readline()
    return first.startswith(b"#!") and b"python" in first


def scan_repo(extra: dict = None, root: Path = REPO) -> set:
    """scan_source over every discovered module (plus `extra`: {path: source},
    for proving what a new or changed module would be flagged for)."""
    sources = discover(root)
    sources.update(extra or {})
    protected = _protected_names()
    return set().union(*(scan_source(rel, src, protected) for rel, src in sources.items()))


# ---- the source-change review guard ------------------------------------------

def regions(source: str) -> tuple:
    """({region: canonical text}, module-level names) for one module. A name
    defined twice is refused: a region must be one unambiguous thing."""
    tree = ast.parse(source)
    out, names = {"": canon(tree)}, []
    imports = [s for s in tree.body if isinstance(s, (ast.Import, ast.ImportFrom))]
    out["<imports>"] = canon(imports)

    def add(name, node):
        if name in out:
            raise AssertionError(f"{name} is defined more than once")
        out[name] = canon(node)

    for stmt in tree.body:
        name = _bound_name(stmt)
        if name is None:
            continue
        names.append(name)
        add(name, stmt)
        if isinstance(stmt, ast.ClassDef):
            for inner in stmt.body:
                inner_name = _bound_name(inner)
                if inner_name is not None:
                    add(f"{name}.{inner_name}", inner)
    return out, names


def surface(overrides: dict = None, root: Path = REPO) -> dict:
    """{(file, region): fingerprint} for the critical surface, plus the
    fingerprint of the surface's own extent ("<surface>"), read from local files
    (or `overrides`: {path: source}). A missing file or region fails closed."""
    overrides = overrides or {}
    cache, out = {}, {}
    for rel, region in SURFACE:
        if rel not in cache:
            source = overrides[rel] if rel in overrides else (root / rel).read_text(encoding="utf-8")
            cache[rel] = regions(source)[0]
        if region == "<bindings>":
            source = overrides[rel] if rel in overrides else (root / rel).read_text(encoding="utf-8")
            out[(rel, region)] = digest(binding_table(rel, source))
            continue
        if region not in cache[rel]:
            raise AssertionError(f"{rel}: {region} is gone; the critical surface must be reviewed again")
        out[(rel, region)] = digest(cache[rel][region])
    out[("<surface>", "")] = digest(repr(SURFACE))
    return out


def unreviewed(found: set, reviewed: set) -> str:
    """'' when the scan matches the reviewed list; otherwise what differs."""
    def line(tag, f):
        return f"  {tag} {tuple(f)}\n        statement: {STATEMENTS.get(f.statement, '?')}\n"
    new, gone = sorted(found - reviewed), sorted(reviewed - found)
    return "".join(line("new: ", f) for f in new) + "".join(line("gone:", f) for f in gone)


def changed_regions(current: dict, protected: dict) -> str:
    """'' when every region matches its reviewed fingerprint; otherwise which
    changed, with the fingerprint to record once the change is reviewed."""
    lines = []
    for key in sorted(set(current) | set(protected)):
        if current.get(key) != protected.get(key):
            lines.append(f"  {key}: reviewed {protected.get(key)}, now {current.get(key)}\n")
    return "".join(lines)


# ---- bindings: a protected name rebound anywhere in a module's execution flow --

SIMPLE_BINDINGS = frozenset({"def", "class", "assign", "destructure", "annassign", "import"})


def _target_names(node):
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, (ast.Tuple, ast.List)):
        for element in node.elts:
            yield from _target_names(element)
    elif isinstance(node, ast.Starred):
        yield from _target_names(node.value)


def _walrus_names(node):
    """Names an expression binds with :=, comprehensions included (they bind in
    the enclosing scope). A lambda's body is its own scope, but its defaults
    (positional and keyword-only) run in the enclosing one, so they are walked."""
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Lambda):
            stack.extend(current.args.defaults)
            stack.extend(d for d in current.args.kw_defaults if d is not None)
            continue
        if isinstance(current, ast.NamedExpr):
            yield from _target_names(current.target)
        stack.extend(ast.iter_child_nodes(current))


def _pattern_names(pattern):
    for node in ast.walk(pattern):
        kind = type(node).__name__
        if kind in ("MatchAs", "MatchStar") and node.name:
            yield node.name
        elif kind == "MatchMapping" and node.rest:
            yield node.rest


def flow_bindings(body: list, top=None) -> list:
    """Every binding in one scope's execution flow (a module or a class body):
    [(name, kind, nested, at, stmt)], however deeply it sits inside if / try /
    with / for / while / match blocks. Kinds: def, class, assign, destructure,
    augassign, annassign, for, with, except, match, import, star-import, del,
    walrus, type. A def or class body is its own scope and is not entered (its
    decorators, defaults and bases are)."""
    out = []

    def bind(name, kind, nested, steps, stmt):
        out.append((name, kind, nested, "/".join(steps), stmt))

    def header_walrus(node, nested, steps, stmt):
        for field, value in ast.iter_fields(node):
            if field in BLOCKS:
                continue
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, ast.AST):
                    for name in _walrus_names(item):
                        bind(name, "walrus", nested, steps, stmt)

    def visit(stmt, steps, nested):
        if isinstance(stmt, SCOPES):
            bind(stmt.name, "class" if isinstance(stmt, ast.ClassDef) else "def", nested, steps, stmt)
            for expr in stmt.decorator_list + getattr(stmt, "bases", []) + \
                    [k.value for k in getattr(stmt, "keywords", [])]:
                for name in _walrus_names(expr):
                    bind(name, "walrus", nested, steps, stmt)
            if not isinstance(stmt, ast.ClassDef):
                for expr in stmt.args.defaults + [d for d in stmt.args.kw_defaults if d is not None]:
                    for name in _walrus_names(expr):
                        bind(name, "walrus", nested, steps, stmt)
            return
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                kind = "assign" if isinstance(target, ast.Name) else "destructure"
                for name in _target_names(target):
                    bind(name, kind, nested, steps, stmt)
        elif isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
            for name in _target_names(stmt.target):
                bind(name, "augassign" if isinstance(stmt, ast.AugAssign) else "annassign", nested, steps, stmt)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            for name in _target_names(stmt.target):
                bind(name, "for", nested, steps, stmt)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                if item.optional_vars is not None:
                    for name in _target_names(item.optional_vars):
                        bind(name, "with", nested, steps, stmt)
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                bind(alias.asname or alias.name.split(".")[0], "import", nested, steps, stmt)
        elif isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                if alias.name == "*":
                    bind("*", "star-import", nested, steps, stmt)
                else:
                    bind(alias.asname or alias.name, "import", nested, steps, stmt)
        elif isinstance(stmt, ast.Delete):
            for target in stmt.targets:
                for name in _target_names(target):
                    bind(name, "del", nested, steps, stmt)
        elif type(stmt).__name__ == "TypeAlias":  # 3.12+
            bind(stmt.name.id, "type", nested, steps, stmt)
        header_walrus(stmt, nested, steps, stmt)
        for field in BLOCKS:
            for index, child in enumerate(getattr(stmt, field, None) or []):
                here = steps + [f"{field}[{index}]"]
                if isinstance(child, ast.ExceptHandler):
                    if child.name:
                        bind(child.name, "except", True, here, stmt)
                    if child.type is not None:
                        for name in _walrus_names(child.type):
                            bind(name, "walrus", True, here, stmt)
                    for j, inner in enumerate(child.body):
                        visit(inner, here + [f"body[{j}]"], True)
                elif type(child).__name__ == "match_case":
                    for name in _pattern_names(child.pattern):
                        bind(name, "match", True, here, stmt)
                    if child.guard is not None:
                        for name in _walrus_names(child.guard):
                            bind(name, "walrus", True, here, stmt)
                    for j, inner in enumerate(child.body):
                        visit(inner, here + [f"body[{j}]"], True)
                else:
                    visit(child, here, True)

    for index, stmt in enumerate(body):
        visit(stmt, [_step(top, "body", index, stmt) if top is not None else f"body[{index}]"], False)
    return out


def _classes_in_flow(body: list) -> list:
    """Every class defined in a module's execution flow, nested blocks included."""
    found = []

    def walk(stmts):
        for stmt in stmts:
            if isinstance(stmt, ast.ClassDef):
                found.append(stmt)
            elif not isinstance(stmt, SCOPES):
                for field in BLOCKS:
                    for child in getattr(stmt, field, None) or []:
                        walk([child] if isinstance(child, ast.stmt) else child.body)
    walk(body)
    return found


def _region_nodes(tree) -> dict:
    """{region name: node} for module-level definitions and class members."""
    out = {}
    for stmt in tree.body:
        name = _bound_name(stmt)
        if name is None:
            continue
        out.setdefault(name, stmt)
        if isinstance(stmt, ast.ClassDef):
            for inner in stmt.body:
                inner_name = _bound_name(inner)
                if inner_name is not None:
                    out.setdefault(f"{name}.{inner_name}", inner)
    return out


def protected_in(rel: str) -> list:
    return [region for path, region in SURFACE if path == rel]


def guarded_names(rel: str, tree) -> dict:
    """{scope: names} that must be bound once, directly, in each execution flow
    of a protected module: in the module, every name its protected regions
    define or mention (builtins included: rebinding `object` would change the
    gate); in a class, its protected members and every self.X they use."""
    regions_here = protected_in(rel)
    nodes = _region_nodes(tree)
    whole = "" in regions_here
    module, classes = set(), collections.defaultdict(set)
    for region in regions_here:
        if region.startswith("<"):
            if region == "<imports>":
                for stmt in tree.body:
                    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                        module.update(a.asname or a.name.split(".")[0] for a in stmt.names)
            continue
        node = tree if region == "" else nodes.get(region)
        if node is None:
            continue
        top = region.split(".")[0]
        if top:
            module.add(top)
        if "." in region:
            classes[top].add(region.split(".", 1)[1])
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name):
                module.add(inner.id)
            elif "." in region and isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name) \
                    and inner.value.id == "self":
                classes[top].add(inner.attr)
    if whole:
        for cls in _classes_in_flow(tree.body):
            classes[cls.name].update(name for name, *_ in flow_bindings(cls.body, cls))
    out = {"": module}
    out.update(classes)
    return out


def rebindings(rel: str, tree) -> list:
    """[(scope, name, kind, at, stmt)] for every binding of a guarded name other
    than its one original, direct, simple binding (the first), in the module's
    flow and in each class body's flow; and every `global` naming one."""
    if rel.startswith("tests/") or not protected_in(rel):
        return []
    guarded = guarded_names(rel, tree)
    out = []

    def check(scope, bindings, names):
        original = set()
        for name, kind, nested, at, stmt in bindings:
            if name == "*":
                out.append((scope, name, kind, at, stmt))
            elif name in names:
                if not nested and kind in SIMPLE_BINDINGS and name not in original:
                    original.add(name)
                else:
                    out.append((scope, name, kind, at, stmt))

    check("", flow_bindings(tree.body, tree), guarded[""])
    for cls in _classes_in_flow(tree.body):
        if cls.name in guarded:
            check(cls.name, flow_bindings(cls.body, cls), guarded[cls.name])
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                if name in guarded[""]:
                    out.append(("", name, "global", "-", node))
    return out


def binding_table(rel: str, source: str) -> str:
    """The guarded names' bindings, canonically: the "<bindings>" region."""
    tree = ast.parse(source)
    guarded = guarded_names(rel, tree)
    rows = []
    for name, kind, nested, at, stmt in flow_bindings(tree.body, tree):
        if name in guarded[""] or name == "*":
            rows.append(("", name, kind, nested, at, canon(_header(stmt))))
    for cls in _classes_in_flow(tree.body):
        for name, kind, nested, at, stmt in flow_bindings(cls.body, cls):
            if name in guarded.get(cls.name, ()):
                rows.append((cls.name, name, kind, nested, at, canon(_header(stmt))))
    rows += [("", "global", ",".join(n.names), True, "-", "") for n in ast.walk(tree)
             if isinstance(n, (ast.Global, ast.Nonlocal))]
    return repr(sorted(rows))


# ---- the surface's dependencies: every one protected or reviewed as not critical

def _resolve(rel: str, level: int, module, root: Path):
    """The package file a (relative) import names, or None outside the package."""
    if level:
        base = Path(rel).parent
        for _ in range(level - 1):
            base = base.parent
    else:
        base = Path()
    target = base / module.replace(".", "/") if module else base
    for candidate in (target.with_suffix(".py"), target / "__init__.py"):
        if (root / candidate).is_file():
            return candidate.as_posix()
    return None


def _imports(rel: str, tree, root: Path) -> dict:
    """{local name: (package file, name in it or None for a module alias)}."""
    out = {}
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.ImportFrom):
            source = _resolve(rel, stmt.level, stmt.module, root)
            for alias in stmt.names:
                local = alias.asname or alias.name
                sub = _resolve(rel, stmt.level, f"{stmt.module}.{alias.name}" if stmt.module else alias.name, root)
                if sub and (source is None or source.endswith("__init__.py")):
                    out[local] = (sub, None)
                elif source:
                    out[local] = (source, alias.name)
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                target = _resolve(rel, 0, alias.name, root)
                if target and alias.asname:
                    out[alias.asname] = (target, None)
    return out


def dependencies(root: Path = REPO, overrides: dict = None) -> dict:
    """{(file, region): {protected regions using it}}: each package-level name a
    protected region uses: a module-level definition of its own file, a
    method it calls as self.X, a name imported from another package module or
    an attribute of an imported package module. A whole-module region counts
    its imports from the package."""
    overrides = overrides or {}
    out = collections.defaultdict(set)
    trees = {}

    def tree_of(rel):
        if rel not in trees:
            trees[rel] = ast.parse(overrides[rel] if rel in overrides else (root / rel).read_text(encoding="utf-8"))
        return trees[rel]

    for rel, region in SURFACE:
        if rel.startswith("tests/") or region.startswith("<"):
            continue
        tree = tree_of(rel)
        nodes, imported = _region_nodes(tree), _imports(rel, tree, root)
        node = tree if region == "" else nodes[region]
        cls = region.split(".")[0] if "." in region else None
        for inner in ast.walk(node):
            dep = None
            if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load):
                if inner.id in imported and imported[inner.id][1] is not None:
                    dep = imported[inner.id]
                elif region != "" and inner.id in nodes and inner.id != (cls or region):
                    dep = (rel, inner.id)
            elif isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name):
                base = inner.value.id
                if base == "self" and cls and f"{cls}.{inner.attr}" in nodes:
                    dep = (rel, f"{cls}.{inner.attr}")
                elif base in imported and imported[base][1] is None:
                    target = imported[base][0]
                    if inner.attr in _region_nodes(tree_of(target)):
                        dep = (target, inner.attr)
            if dep is not None and dep != (rel, region):
                out[dep].add((rel, region))
    return dict(out)


def covered(dep: tuple) -> bool:
    """On the surface: itself, its whole module or class, or (for a class used to
    construct one) its __init__, the class name itself being pinned by "<bindings>"."""
    rel, region = dep
    return dep in SURFACE or (rel, "") in SURFACE or ("." in region and (rel, region.split(".")[0]) in SURFACE) \
        or ("." not in region and (rel, f"{region}.__init__") in SURFACE)
