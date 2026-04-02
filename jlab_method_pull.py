import ast
import difflib
import inspect
import shutil
import textwrap
import types
from pathlib import Path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def install():
    """Copy jlab_method_pull.py to the IPython startup directory.

    Files in that directory run automatically at kernel start, making
    pullMethodCode and injectMethod available without any explicit import.
    Offers to delete or disable any existing conflicting startup files.
    """
    try:
        from IPython.paths import get_ipython_dir
        startup_dir = Path(get_ipython_dir()) / "profile_default" / "startup"
    except ImportError:
        startup_dir = Path.home() / ".ipython" / "profile_default" / "startup"

    startup_dir.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve()
    dst = startup_dir / src.name
    _resolve_conflicts(startup_dir, skip=dst)
    shutil.copy2(src, dst)
    print(f"Installed to {dst}")
    print("pullMethodCode and injectMethod will be available in every new JupyterLab kernel.")


def pullMethodCode(func):
    """Open a new Jupyter cell with func's source, ready to edit and re-inject.

    The generated cell contains the file's imports, the function source
    (dedented, @staticmethod stripped), and a ready-to-run injectMethod() call.
    """
    qualname_parts = func.__qualname__.split(".")
    is_method = len(qualname_parts) >= 2 and "<locals>" not in qualname_parts
    func_name  = func.__name__
    class_name = qualname_parts[-2] if is_method else None
    module     = inspect.getmodule(func)
    module_name = module.__name__ if module else None
    source_file = inspect.getfile(func)

    cls = getattr(module, class_name, None) if (module and class_name) else None
    is_static = cls is not None and isinstance(cls.__dict__.get(func_name), staticmethod)

    source = textwrap.dedent(inspect.getsource(func))
    if is_static:
        source = "\n".join(
            line for line in source.splitlines() if line.strip() != "@staticmethod"
        ) + "\n"

    file_tree  = ast.parse(Path(source_file).read_text())
    imports    = "\n".join(ast.unparse(n) for n in file_tree.body if isinstance(n, (ast.Import, ast.ImportFrom)))
    pub_names  = ", ".join(sorted(_public_names(file_tree)))
    target     = class_name if is_method else module_name

    cell_lines = []
    if imports:
        cell_lines.append(imports)
    if module_name and pub_names:
        if not is_method:
            cell_lines.append(f"import {module_name}")
        cell_lines.append(f"from {module_name} import {pub_names}")
    cell_lines += ["", source.rstrip(), "", f"injectMethod({func_name}, {target}, persistent=False)"]

    cell_content = "\n".join(cell_lines)
    try:
        get_ipython().set_next_input(cell_content, replace=False)  # noqa: F821
    except NameError:
        pass


def injectMethod(new_func, target, persistent=True):
    """Inject new_func into target (a class or module).

    Parameters
    ----------
    new_func : callable
        Function to inject; its __name__ determines what is replaced or added.
    target : type or module
        Class or module to inject into.
    persistent : bool
        If True, also rewrites the target's source file on disk.
    """
    func_name = new_func.__name__
    is_static = (
        not isinstance(target, types.ModuleType)
        and isinstance(target.__dict__.get(func_name), staticmethod)
    )

    if persistent:
        _persist(new_func, target, func_name, is_static)

    setattr(target, func_name, staticmethod(new_func) if is_static else new_func)
    location = "persistent" if persistent else "in-memory only"
    print(f"Injected '{func_name}' into {target.__name__} ({location})")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _public_names(tree: ast.Module) -> list[str]:
    """All public names defined at module level (classes, functions, assignments)."""
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
    return names


def _find_func_lines(source: str, func_name: str, class_name: str | None = None):
    """Return (start, end) line numbers (1-indexed, inclusive) of a function.

    Searches inside class_name if given, otherwise at module level.
    start includes any decorator lines. Returns (None, None) if not found.
    """
    tree = ast.parse(source)

    def span(node):
        start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
        return start, node.end_lineno

    if class_name is not None:
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == func_name:
                        return span(item)
    else:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                return span(node)
    return None, None


def _class_indent(target_class, source: str) -> str:
    """Detect the indentation string used for methods inside target_class."""
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == target_class.__name__:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    line = lines[item.lineno - 1]
                    return line[: len(line) - len(line.lstrip())]
    return "    "


def _persist(new_func, target, func_name: str, is_static: bool):
    source_file = inspect.getfile(target)
    content = Path(source_file).read_text()
    file_lines = content.splitlines(keepends=True)

    is_module = isinstance(target, types.ModuleType)
    class_name = None if is_module else target.__name__

    if is_module:
        raw = textwrap.dedent(inspect.getsource(new_func))
        new_lines = (raw if raw.endswith("\n") else raw + "\n").splitlines(keepends=True)
    else:
        indent = _class_indent(target, content)
        indented = textwrap.indent(textwrap.dedent(inspect.getsource(new_func)), indent)
        if not indented.endswith("\n"):
            indented += "\n"
        new_lines = ([f"{indent}@staticmethod\n"] if is_static else []) + indented.splitlines(keepends=True)

    start, end = _find_func_lines(content, func_name, class_name)

    if start is not None:
        updated = file_lines[: start - 1] + new_lines + file_lines[end:]
    elif is_module:
        updated = file_lines + ["\n"] + new_lines
    else:
        updated = _insert_into_class(file_lines, class_name, new_lines, indent)

    if not _confirm_diff(source_file, file_lines, updated):
        print("Aborted — file not modified.")
        return
    Path(source_file).write_text("".join(updated))


def _confirm_diff(source_file: str, original: list[str], updated: list[str]) -> bool:
    """Print a colored unified diff and ask the user to confirm the write."""
    RED, GREEN, CYAN, RESET = "\033[31m", "\033[32m", "\033[36m", "\033[0m"

    diff = list(difflib.unified_diff(original, updated, fromfile=source_file, tofile=source_file))
    if not diff:
        return True  # nothing changed, no need to ask

    for line in diff:
        if line.startswith("---") or line.startswith("+++"):
            print(f"{CYAN}{line}{RESET}", end="")
        elif line.startswith("-"):
            print(f"{RED}{line}{RESET}", end="")
        elif line.startswith("+"):
            print(f"{GREEN}{line}{RESET}", end="")
        else:
            print(line, end="")

    return input("\nWrite changes? [y/N] ").strip().lower() == "y"


def _insert_into_class(file_lines: list[str], class_name: str, new_lines: list[str], indent: str) -> list[str]:
    """Append new_lines at the end of the named class body."""
    insert_at = len(file_lines)
    in_class = False
    for i, line in enumerate(file_lines):
        if line.lstrip().startswith(f"class {class_name}"):
            in_class = True
            continue
        if in_class and line.strip() and not line.startswith(indent):
            insert_at = i
            break
    return file_lines[:insert_at] + ["\n"] + new_lines + file_lines[insert_at:]


def _resolve_conflicts(startup_dir: Path, skip: Path):
    """Offer to delete or disable startup files that define pullMethodCode/injectMethod."""
    OUR_FUNCS = ("pullMethodCode", "injectMethod")

    def conflicting(path):
        try:
            names = {n.name for n in ast.walk(ast.parse(path.read_text()))
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            return [f for f in OUR_FUNCS if f in names]
        except SyntaxError:
            return []

    conflicts = {p: found for p in sorted(startup_dir.glob("*.py"))
                 if p != skip and (found := conflicting(p))}
    if not conflicts:
        return

    print("\nThe following startup files already define conflicting functions:")
    for path, names in conflicts.items():
        print(f"  {path.name}  ({', '.join(names)})")
    print("\nFor each file, choose an action:")
    print("  d = delete   x = disable (.py.disabled)   s = skip")

    for path in conflicts:
        while True:
            choice = input(f"\n  [{path.name}] d / x / s? ").strip().lower()
            if choice == "d":
                path.unlink();  print(f"  Deleted {path.name}"); break
            elif choice == "x":
                dst = path.with_suffix(".py.disabled")
                path.rename(dst);  print(f"  Disabled → {dst.name}"); break
            elif choice == "s":
                print(f"  Kept {path.name}"); break
            else:
                print("  Please enter d, x, or s.")
