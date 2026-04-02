import ast
import inspect
import shutil
import textwrap
from pathlib import Path


def install():
    """Copy jlab_method_pull.py to the IPython startup directory.

    Files in that directory are executed automatically at the start of every
    IPython/JupyterLab kernel, making pullMethodCode and injectMethod
    available without any explicit import.

    If other startup files already define pullMethodCode or injectMethod,
    the user is offered the choice to delete or disable each one.
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


def _defines_our_functions(path: Path) -> list[str]:
    """Return which of pullMethodCode / injectMethod are defined in path."""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return []
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return [n for n in ("pullMethodCode", "injectMethod") if n in names]


def _resolve_conflicts(startup_dir: Path, skip: Path):
    """Check every .py file in startup_dir for conflicting definitions."""
    conflicts = {
        p: found
        for p in sorted(startup_dir.glob("*.py"))
        if p != skip and (found := _defines_our_functions(p))
    }

    if not conflicts:
        return

    print("\nThe following startup files already define conflicting functions:")
    for path, names in conflicts.items():
        print(f"  {path.name}  ({', '.join(names)})")

    print("\nFor each file, choose an action:")
    print("  d = delete the file")
    print("  x = disable it (rename to .py.disabled)")
    print("  s = skip (keep as-is)")

    for path, names in conflicts.items():
        while True:
            choice = input(f"\n  [{path.name}] d / x / s? ").strip().lower()
            if choice == "d":
                path.unlink()
                print(f"  Deleted {path.name}")
                break
            elif choice == "x":
                disabled = path.with_suffix(".py.disabled")
                path.rename(disabled)
                print(f"  Disabled → {disabled.name}")
                break
            elif choice == "s":
                print(f"  Kept {path.name}")
                break
            else:
                print("  Please enter d, x, or s.")


def pullMethodCode(class_method) -> str:
    """Open a new cell with the method's source, ready to edit and re-inject."""
    # Dedent so the def is at column 0
    source = textwrap.dedent(inspect.getsource(class_method))

    # Derive class name and module from the method
    qualname_parts = class_method.__qualname__.split(".")
    class_name = qualname_parts[-2] if len(qualname_parts) >= 2 else None
    method_name = class_method.__name__
    module = inspect.getmodule(class_method)
    module_name = module.__name__ if module else None

    source_file = inspect.getfile(class_method)

    # Collect top-level imports from the source file
    file_imports = _extract_file_imports(source_file)

    # Import every public name defined at module level in the source file
    all_module_names = _all_module_level_names(source_file)

    # Build cell content
    lines = []
    if file_imports:
        lines.append(file_imports)
    if module_name and all_module_names:
        names_str = ", ".join(sorted(all_module_names))
        lines.append(f"from {module_name} import {names_str}")
    lines.append("")
    lines.append(source.rstrip())
    lines.append("")
    lines.append(f"injectMethod({method_name}, {class_name}, persistent=False)")

    cell_content = "\n".join(lines)

    try:
        ip = get_ipython()  # noqa: F821 — available in Jupyter kernels
        ip.set_next_input(cell_content, replace=False)
    except NameError:
        pass  # not running in a Jupyter kernel


def _extract_file_imports(source_file: str) -> str:
    """Return all top-level import statements from a source file as a string."""
    with open(source_file, "r") as fh:
        tree = ast.parse(fh.read())
    lines = [
        ast.unparse(node)
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    return "\n".join(lines)


def _all_module_level_names(source_file: str) -> list[str]:
    """Return every public name defined at module level in a source file.

    Respects __all__ if present; otherwise returns all names that don't
    start with an underscore (classes, functions, top-level assignments).
    """
    with open(source_file, "r") as fh:
        tree = ast.parse(fh.read())

    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
    return names


def injectMethod(new_method_implementation, target_class, persistent=True):
    """Inject a function as a method into target_class.

    Parameters
    ----------
    new_method_implementation : callable
        The function to inject. Its __name__ determines which method is
        replaced or added.
    target_class : type
        The class to receive the method.
    persistent : bool
        If True, also rewrites the class's source file so the change
        survives the next import. If False, only monkey-patches in memory.
    """
    method_name = new_method_implementation.__name__

    if persistent:
        _persist_method(new_method_implementation, target_class, method_name)

    setattr(target_class, method_name, new_method_implementation)
    location = "persistent" if persistent else "in-memory only"
    print(f"Injected '{method_name}' into {target_class.__name__} ({location})")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_indented_source(new_func, class_indent: str) -> list[str]:
    """Return new_func's source dedented and re-indented for the class body."""
    raw = inspect.getsource(new_func)
    dedented = textwrap.dedent(raw)
    indented = textwrap.indent(dedented, class_indent)
    if not indented.endswith("\n"):
        indented += "\n"
    return indented.splitlines(keepends=True)


def _class_body_indent(target_class, file_lines: list[str]) -> str:
    """Detect the indentation used for method definitions inside target_class."""
    class_name = target_class.__name__
    inside = False
    for line in file_lines:
        stripped = line.lstrip()
        if stripped.startswith(f"class {class_name}"):
            inside = True
            continue
        if inside and (stripped.startswith("def ") or stripped.startswith("async def ")):
            indent = line[: len(line) - len(stripped)]
            if indent:
                return indent
    return "    "  # fall back to 4 spaces


def _find_method_lines_in_file(source: str, class_name: str, method_name: str):
    """Return (start_lineno, end_lineno) of a method inside a class using AST.

    Both values are 1-indexed; end_lineno is inclusive.
    Returns (None, None) if not found.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == method_name
                ):
                    return item.lineno, item.end_lineno
    return None, None


def _persist_method(new_func, target_class, method_name: str):
    source_file = inspect.getfile(target_class)

    with open(source_file, "r") as fh:
        content = fh.read()
    file_lines = content.splitlines(keepends=True)

    class_indent = _class_body_indent(target_class, file_lines)
    new_lines = _get_indented_source(new_func, class_indent)

    start_lineno, end_lineno = _find_method_lines_in_file(
        content, target_class.__name__, method_name
    )

    if start_lineno is not None:
        # Replace exactly the lines the AST says belong to the old method
        updated = file_lines[: start_lineno - 1] + new_lines + file_lines[end_lineno:]
    else:
        # New method — append inside the class body before it ends.
        updated = _insert_into_class(file_lines, target_class.__name__, new_lines, class_indent)

    with open(source_file, "w") as fh:
        fh.writelines(updated)


def _insert_into_class(
    file_lines: list[str], class_name: str, new_lines: list[str], class_indent: str
) -> list[str]:
    """Insert new_lines at the end of the named class body."""
    # Walk backwards from end of file to find the last line that belongs to
    # the class (i.e. has at least class_indent indentation, or is blank).
    in_class = False
    class_start = None
    for i, line in enumerate(file_lines):
        stripped = line.lstrip()
        if stripped.startswith(f"class {class_name}"):
            in_class = True
            class_start = i
            continue
        if in_class and line.strip() == "" :
            continue
        if in_class and not line.startswith(class_indent) and line.strip():
            # First non-blank line after class body that is not indented
            insert_at = i
            break
    else:
        insert_at = len(file_lines)

    # Add a blank separator line before the new method for readability.
    return file_lines[:insert_at] + ["\n"] + new_lines + file_lines[insert_at:]
