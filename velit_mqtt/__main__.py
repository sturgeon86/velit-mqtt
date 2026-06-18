"""Console entry point: ``python -m velit_mqtt``."""

from __future__ import annotations

import sys

from .service import main

if __name__ == "__main__":
    sys.exit(main())
