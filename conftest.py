"""Minimal pytest setup (Step 2).

Puts the repo root on sys.path so `import app...` resolves when pytest is run
from anywhere. Adds no application code and changes no existing behaviour.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
