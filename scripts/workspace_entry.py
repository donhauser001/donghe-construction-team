"""Isolated interpreter bootstrap for the package-owned workspace reader."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from donghe_workspace import main

if __name__ == '__main__':
    main()
