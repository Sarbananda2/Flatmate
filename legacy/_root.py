"""Put the project root on the import path.

The modules in this directory are thin compatibility paths onto the ``aem``
package, which lives one level up. Importing this first lets them be run
directly (``python legacy/ledger_entry_cli.py``) from anywhere.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
