import ast
import inspect
import shutil
import textwrap
import types
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


def pullMethodCode(func) -> str:
    """Open a new cell with the function/method source, ready to edit and re-inject."""
    # Dedent so the def is at column 0
    source = textwrap.dedent(inspect.getsource(func))

    qualname_parts = func.__qualname__.split(".")
    is_method = len(qualname_parts) >= 2 and "<locals>" not in qualname_parts
    class_name = qualname_parts[-2] if is_method else None
    func_name = func.__name__
    module = inspect.getmodule(func)
    module_name = module.__name__ if module else None

    # Detect static method and strip @staticmethod decorator from cell source
    # so the user edits a plain function (injectMethod re-applies the wrapper).
    cls = getattr(module, class_name, None) if (module and class_name) else None
    is_static = cls is not None and isinstance(cls.__dict__.get(func_name), staticmethod)
    if is_static:
        source = _strip_staticmethod_decorator(source)

    source_file = inspect.getfile(func)

    # Collect top-level imports from the source file
    file_imports = _extract_file_imports(source_file)

    # All public names defined in the source file (for from-import line)
    all_module_names = _all_module_level_names(source_file)

    # Build cell content
    lines = []
    if file_imports:
        lines.append(file_imports)
    if module_name:
        if is_method and all_module_names:
            names_str = ", ".join(sorted(all_module_names))
            lines.append(f"from {module_name} import {names_str}")
        else:
            # Module-level function: import the module itself so injectMethod can target it
            lines.append(f"import {module_name}")
            if all_module_names:
                names_str = ", ".join(sorted(all_module_names))
                lines.append(f"from {module_name} import {names_str}")
    lines.append("")
    lines.append(source.rstrip())
    lines.append("")
    target = class_name if is_method else module_name
    lines.append(f"injectMethod({func_name}, {target}, persistent=False)")

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


def injectMethod(new_implementation, target, persistent=True):
    """Inject a function as a method into a class, or replace a function in a module.

    Parameters
    ----------
    new_implementation : callable
        The function to inject. Its __name__ determines what is replaced or added.
    target : type or module
        The class or module to inject into.
    persistent : bool
        If True, also rewrites the target's source file so the change
        survives the next import. If False, only patches in memory.
    """
    func_name = new_implementation.__name__

    is_static = (
        not isinstance(target, types.ModuleType)
        and isinstance(target.__dict__.get(func_name), staticmethod)
    )

    if persistent:
        _persist_method(new_implementation, target, func_name, is_static=is_static)

    value = staticmethod(new_implementation) if is_static else new_implementation
    setattr(target, func_name, value)
    location = "persistent" if persistent else "in-memory only"
    target_name = target.__name__ if hasattr(target, "__name__") else str(target)
    print(f"Injected '{func_name}' into {target_name} ({location})")


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


def _find_func_lines_in_file(source: str, func_name: str, class_name: str | None = None):
    """Return (start_lineno, end_lineno) of a function/method using AST.

    If class_name is given, searches inside that class; otherwise searches at
    module level. start_lineno includes any leading decorators. Both line
    numbers are 1-indexed; end_lineno is inclusive.
    Returns (None, None) if not found.
    """
    tree = ast.parse(source)

    def _lines(item):
        start = item.decorator_list[0].lineno if item.decorator_list else item.lineno
        return start, item.end_lineno

    if class_name is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in node.body:
                    if (
                        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name == func_name
                    ):
                        return _lines(item)
    else:
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == func_name
            ):
                return _lines(node)
    return None, None


def _strip_staticmethod_decorator(source: str) -> str:
    """Remove the @staticmethod decorator line from a dedented function source."""
    lines = source.splitlines(keepends=True)
    return "".join(
        line for line in lines
        if line.strip() not in ("@staticmethod",)
    )


def _persist_method(new_func, target, func_name: str, is_static: bool = False):
    source_file = inspect.getfile(target)

    with open(source_file, "r") as fh:
        content = fh.read()
    file_lines = content.splitlines(keepends=True)

    is_module = isinstance(target, types.ModuleType)
    class_name = None if is_module else target.__name__

    if is_module:
        # Module-level function: no extra indentation
        raw = textwrap.dedent(inspect.getsource(new_func))
        if not raw.endswith("\n"):
            raw += "\n"
        new_lines = raw.splitlines(keepends=True)
    else:
        class_indent = _class_body_indent(target, file_lines)
        new_lines = _get_indented_source(new_func, class_indent)
        if is_static:
            new_lines = [f"{class_indent}@staticmethod\n"] + new_lines

    start_lineno, end_lineno = _find_func_lines_in_file(content, func_name, class_name)

    if start_lineno is not None:
        updated = file_lines[: start_lineno - 1] + new_lines + file_lines[end_lineno:]
    elif is_module:
        # New module-level function — append at end of file
        updated = file_lines + ["\n"] + new_lines
    else:
        updated = _insert_into_class(file_lines, class_name, new_lines, class_indent)

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
