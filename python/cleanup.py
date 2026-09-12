"""Release only descendants of the extension's known worker PIDs. No generated code."""
import sys
from lifecycle import kill_windows_descendants

if sys.platform == "win32":
    kill_windows_descendants([int(value) for value in sys.argv[1:]])
