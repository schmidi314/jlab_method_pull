import importlib
import sys
import textwrap
from pathlib import Path

import pytest

from jlab_method_pull import (
    _all_module_level_names,
    _extract_file_imports,
    _find_func_lines_in_file,
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


def capture_cell(func, *args, **kwargs):
    """Call func(*args, **kwargs) with get_ipython patched; return the cell text."""
    import builtins
    captured = {}

    class FakeIP:
        def set_next_input(self, text, replace=False):
            captured["text"] = text

    builtins.get_ipython = lambda: FakeIP()
    try:
        func(*args, **kwargs)
    finally:
        del builtins.get_ipython
    return captured.get("text", "")


# ---------------------------------------------------------------------------
# _find_func_lines_in_file — class methods
# ---------------------------------------------------------------------------

class TestFindFuncLines:
    SOURCE = textwrap.dedent("""\
        class Foo:
            def bar(self):
                return 1

            def baz(self):
                x = 2
                return x

        def standalone():
            return 42
    """)

    def test_finds_first_method(self):
        start, end = _find_func_lines_in_file(self.SOURCE, "bar", "Foo")
        assert start == 2
        assert end == 3

    def test_finds_second_method(self):
        start, end = _find_func_lines_in_file(self.SOURCE, "baz", "Foo")
        assert start == 5
        assert end == 7

    def test_missing_method_returns_none(self):
        start, end = _find_func_lines_in_file(self.SOURCE, "nonexistent", "Foo")
        assert start is None and end is None

    def test_missing_class_returns_none(self):
        start, end = _find_func_lines_in_file(self.SOURCE, "bar", "Bar")
        assert start is None and end is None

    def test_finds_module_level_function(self):
        start, end = _find_func_lines_in_file(self.SOURCE, "standalone")
        assert start == 9
        assert end == 10

    def test_module_level_missing_returns_none(self):
        start, end = _find_func_lines_in_file(self.SOURCE, "nowhere")
        assert start is None and end is None

    def test_includes_decorator_in_start(self):
        source = textwrap.dedent("""\
            class Foo:
                @staticmethod
                def bar(x):
                    return x
        """)
        start, end = _find_func_lines_in_file(source, "bar", "Foo")
        assert start == 2  # @staticmethod line
        assert end == 4


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
# injectMethod — in-memory, class
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
# injectMethod — in-memory, module
# ---------------------------------------------------------------------------

class TestInjectMethodModuleInMemory:
    def test_replaces_module_function(self, tmp_path):
        write_module(tmp_path, "mmod_mem", """\
            def greet():
                return "hello"
        """)
        import mmod_mem

        def greet():
            return "hi"

        injectMethod(greet, mmod_mem, persistent=False)
        assert mmod_mem.greet() == "hi"


# ---------------------------------------------------------------------------
# injectMethod — persistent, class
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

        assert Calc().double(4) == 12
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

        def label(self):
            return "v2"

        injectMethod(label, Box, persistent=False)

        def label(self):  # noqa: F811
            return "v3"

        injectMethod(label, Box, persistent=True)

        assert Box().label() == "v3"
        new_source = mod.read_text()
        assert "v3" in new_source
        assert "v1" not in new_source


# ---------------------------------------------------------------------------
# injectMethod — persistent, module
# ---------------------------------------------------------------------------

class TestInjectMethodStaticMethod:
    def test_in_memory_preserves_static(self):
        class Target:
            @staticmethod
            def compute(x):
                return x + 1

        def compute(x):
            return x + 10

        injectMethod(compute, Target, persistent=False)
        assert Target.compute(5) == 15
        assert isinstance(Target.__dict__["compute"], staticmethod)

    def test_persistent_rewrites_file_with_decorator(self, tmp_path):
        mod = write_module(tmp_path, "smod", """\
            class Calc:
                @staticmethod
                def double(x):
                    return x * 2
        """)

        from smod import Calc  # noqa

        def double(x):
            return x * 3

        injectMethod(double, Calc, persistent=True)

        assert Calc.double(4) == 12
        assert isinstance(Calc.__dict__["double"], staticmethod)
        new_source = mod.read_text()
        assert "@staticmethod" in new_source
        assert "x * 3" in new_source
        assert "x * 2" not in new_source

    def test_pullMethodCode_strips_decorator(self, tmp_path):
        write_module(tmp_path, "smod2", """\
            class Tool:
                @staticmethod
                def run(x):
                    return x
        """)

        from smod2 import Tool  # noqa

        cell = capture_cell(pullMethodCode, Tool.run)
        assert "@staticmethod" not in cell
        assert "def run" in cell
        assert "injectMethod(run, Tool" in cell


class TestInjectMethodModulePersistent:
    def test_replaces_function_in_file(self, tmp_path):
        mod = write_module(tmp_path, "mmod", """\
            def compute(x):
                return x + 1
        """)
        import mmod

        def compute(x):
            return x + 10

        injectMethod(compute, mmod, persistent=True)

        assert mmod.compute(5) == 15
        new_source = mod.read_text()
        assert "x + 10" in new_source
        assert "x + 1\n" not in new_source

    def test_adds_new_function_to_file(self, tmp_path):
        mod = write_module(tmp_path, "mmod2", """\
            def existing():
                return 0
        """)
        import mmod2

        def new_func():
            return 99

        injectMethod(new_func, mmod2, persistent=True)

        assert mmod2.new_func() == 99
        assert "def new_func" in mod.read_text()


# ---------------------------------------------------------------------------
# pullMethodCode — cell content
# ---------------------------------------------------------------------------

class TestPullMethodCode:
    def test_method_cell_content(self, tmp_path):
        write_module(tmp_path, "cmod", """\
            import os

            class Thing:
                def name(self):
                    return os.getcwd()
        """)

        from cmod import Thing  # noqa

        cell = capture_cell(pullMethodCode, Thing.name)
        assert "import os" in cell
        assert "def name" in cell
        assert "injectMethod(name, Thing" in cell

    def test_module_function_cell_content(self, tmp_path):
        write_module(tmp_path, "cmod3", """\
            import os

            def get_path():
                return os.getcwd()
        """)
        import cmod3  # noqa

        cell = capture_cell(pullMethodCode, cmod3.get_path)
        assert "import os" in cell
        assert "import cmod3" in cell
        assert "def get_path" in cell
        assert "injectMethod(get_path, cmod3" in cell
