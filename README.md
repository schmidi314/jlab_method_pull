# jlab_method_pull

Pull class methods into editable JupyterLab cells and inject modified versions back — optionally rewriting the source file for persistence.

## Install

```bash
pip install jlab_method_pull
```

To make `pullMethodCode` and `injectMethod` available automatically in every JupyterLab kernel, run once:

```python
from jlab_method_pull import install
install()
```

## Usage

### Pull a method into a new cell

```python
from jlab_method_pull import pullMethodCode, injectMethod
from mymodule import MyClass

pullMethodCode(MyClass.some_method)
```

A new cell appears below, pre-filled with the method's source, all imports from the source file, and a ready-to-run `injectMethod` call:

```python
import numpy as np
from mymodule import MyClass, OtherClass

def some_method(self, x):
    ...

injectMethod(some_method, MyClass, persistent=False)
```

### Inject a method back

```python
# In-memory only (survives the session, not reimports):
injectMethod(some_method, MyClass, persistent=False)

# Persistent (also rewrites mymodule.py on disk):
injectMethod(some_method, MyClass, persistent=True)
```

`persistent=True` uses AST to locate the exact lines of the old method in the source file and replaces them. It works correctly even if the method was previously monkey-patched in memory.

## How it compares

| Tool | Cell injection | Runtime patch | Rewrites source file |
|---|---|---|---|
| IPython `%load` | file-level only | — | — |
| gorilla | — | yes | — |
| **jlab_method_pull** | **method-level** | **yes** | **yes** |

## Requirements

- Python ≥ 3.9
- IPython (only required for the Jupyter cell injection and `install()`)
