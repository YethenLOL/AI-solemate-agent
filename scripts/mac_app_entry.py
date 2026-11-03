import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_HOME = Path.home() / "Library/Application Support/Dafeiyu"
os.environ.setdefault("DAFEIYU_HOME", str(APP_HOME))

from dafeiyu.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
