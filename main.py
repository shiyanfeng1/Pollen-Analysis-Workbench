"""Local launcher; packaged builds resolve imports through PyInstaller."""
import sys
import os
from pathlib import Path

if '--smoke-test' in sys.argv:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent / '.deps'))

from pollen.ui import main

if __name__ == '__main__':
    main()
