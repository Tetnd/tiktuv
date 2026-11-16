"""PyInstaller entry point for the Tiktuv backend.

``tiktuv/__main__.py`` uses relative imports, which only resolve when the module
is executed as ``python -m tiktuv``. PyInstaller runs its entry script as a
top-level ``__main__`` where those relative imports fail. This tiny launcher
imports the package absolutely instead, so the frozen backend starts exactly the
same way the ``-m tiktuv`` invocation does.
"""

from __future__ import annotations

import sys

from tiktuv.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
