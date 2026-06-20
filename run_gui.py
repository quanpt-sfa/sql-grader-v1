import sys
from pathlib import Path

# Add src/ to python path
src_dir = Path(__file__).parent / "src"
sys.path.insert(0, str(src_dir))

from dbcheck.gui.app import main

if __name__ == "__main__":
    main()
