"""Entry script for stamper-cli.exe (console): always the command line."""

import sys

from stamper.cli import main

if __name__ == "__main__":
    sys.exit(main())
