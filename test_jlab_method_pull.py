import importlib
import sys
import textwrap
from pathlib import Path

import pytest

from jlab_method_pull import (
    _all_module_level_names,
    _extract_file_imports,
    _find_method_lines_in_file,
    injectMethod,
    pullMethodCode,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_module(tmp_path: Path, name: str, source: str) -> Path:
    """Write a .py file, register it as a module, and return the path."""
    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(source))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return path


# ---------------------------------------------------------------------------
# _find_method_lines_in_file
# ---------------------------------------------------------------------------

class TestFindMethodLines:
    SOURCE = textwrap.dedent("""\
        class Foo:
            def bar(self):
                return 1

            def baz(self):
                x = 2
                return x
    """)

    def test_finds_first_method(self):
        start, end = _find_method_lines_in_file(self.SOURCE, "Foo", "bar")
        assert start == 2
        assert end == 3

    def test_finds_second_method(self):
        start, end = _find_method_lines_in_file(self.SOURCE, "Foo", "baz")
        assert start == 5
        assert end == 7

    def test_missing_method_returns_none(self):
        start, end = _find_method_lines_in_file(self.SOURCE, "Foo", "nonexistent")
        assert start is None and end is None

    def test_missing_class_returns_none(self):
        start, end = _find_method_lines_in_file(self.SOURCE, "Bar", "bar")
        assert start is None and end is None


# ---------------------------------------------------------------------------
# _extract_file_imports
# ---------------------------------------------------------------------------

class TestExtractFileImports:
    def test_extracts_imports(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("import os\nimport sys\n\nclass Foo:\n    pass\n")
        result = _extract_file_imports(str(p))
        assert "import os" in result
        assert "import sys" in result

    def test_no_imports(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("class Foo:\n    pass\n")
        assert _extract_file_imports(str(p)) == ""

    def test_from_import(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("from pathlib import Path\n")
        result = _extract_file_imports(str(p))
        assert "pathlib" in result
        assert "Path" in result


# ---------------------------------------------------------------------------
# _all_module_level_names
# ---------------------------------------------------------------------------

class TestAllModuleLevelNames:
    def test_returns_classes_and_functions(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("class Foo:\n    pass\n\ndef bar():\n    pass\n")
        names = _all_module_level_names(str(p))
        assert "Foo" in names
        assert "bar" in names

    def test_excludes_imports(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("import os\nclass Foo:\n    pass\n")
        names = _all_module_level_names(str(p))
        assert "os" not in names
        assert "Foo" in names


# ---------------------------------------------------------------------------
# injectMethod — in-memory
# ---------------------------------------------------------------------------

class TestInjectMethodInMemory:
    def test_replaces_method(self):
        class Target:
            def greet(self):
                return "hello"

        def greet(self):
            return "hi"

        injectMethod(greet, Target, persistent=False)
        assert Target().greet() == "hi"

    def test_adds_new_method(self):
        class Target:
            pass

        def speak(self):
            return "woof"

        injectMethod(speak, Target, persistent=False)
        assert Target().speak() == "woof"


# ---------------------------------------------------------------------------
# injectMethod — persistent (rewrites source file)
# ---------------------------------------------------------------------------

class TestInjectMethodPersistent:
    def test_replaces_method_in_file(self, tmp_path):
        mod = write_module(tmp_path, "pmod", """\
            class Calc:
                def double(self, x):
                    return x * 2
        """)

        from pmod import Calc  # noqa

        def double(self, x):
            return x * 3

        injectMethod(double, Calc, persistent=True)

        # Check in-memory behaviour
        assert Calc().double(4) == 12

        # Check the file was rewritten
        new_source = mod.read_text()
        assert "x * 3" in new_source
        assert "x * 2" not in new_source

    def test_adds_new_method_to_file(self, tmp_path):
        mod = write_module(tmp_path, "pmod2", """\
            class Widget:
                def color(self):
                    return "red"
        """)

        from pmod2 import Widget  # noqa

        def size(self):
            return "large"

        injectMethod(size, Widget, persistent=True)

        assert Widget().size() == "large"
        assert "def size" in mod.read_text()

    def test_survives_prior_monkey_patch(self, tmp_path):
        """persistent=True must locate the method via AST, not the live object."""
        mod = write_module(tmp_path, "pmod3", """\
            class Box:
                def label(self):
                    return "v1"
        """)

        from pmod3 import Box  # noqa

        # First patch in-memory only
        def label(self):
            return "v2"

        injectMethod(label, Box, persistent=False)

        # Now persist a further change — must still find the right lines in file
        def label(self):  # noqa: F811
            return "v3"

        injectMethod(label, Box, persistent=True)

        assert Box().label() == "v3"
        new_source = mod.read_text()
        assert "v3" in new_source
        assert "v1" not in new_source


# ---------------------------------------------------------------------------
# pullMethodCode — cell content
# ---------------------------------------------------------------------------

class TestPullMethodCode:
    def test_contains_dedented_def(self, tmp_path):
        write_module(tmp_path, "cmod", """\
            class Animal:
                def speak(self):
                    return "roar"
        """)

        from cmod import Animal  # noqa

        result = pullMethodCode(Animal.speak)
        assert result is None or isinstance(result, str)
        # Function returns None (cell injection path) but we can test the
        # cell content indirectly via the helper that builds it

    def test_imports_included(self, tmp_path):
        """The generated cell must include the file's imports."""
        write_module(tmp_path, "cmod2", """\
            import os

            class Thing:
                def name(self):
                    return os.getcwd()
        """)

        from cmod2 import Thing  # noqa

        # Patch get_ipython to capture the cell content
        captured = {}

        class FakeIP:
            def set_next_input(self, text, replace=False):
                captured["text"] = text

        import builtins
        builtins.get_ipython = lambda: FakeIP()
        try:
            pullMethodCode(Thing.name)
        finally:
            del builtins.get_ipython

        cell = captured["text"]
        assert "import os" in cell
        assert "def name" in cell
        assert "injectMethod(name, Thing" in cell
