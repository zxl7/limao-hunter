import sys
from pathlib import Path

WEALTH_CODE = Path(__file__).resolve().parents[1]
if str(WEALTH_CODE) not in sys.path:
    sys.path.insert(0, str(WEALTH_CODE))
