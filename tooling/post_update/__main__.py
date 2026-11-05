import logging
import sys

from .driver import main

# --tab-json output is parsed by the UI (stderr is merged into it): keep log lines out of it
logging.basicConfig(level=logging.WARNING if ("--tab-json" in sys.argv or "--json" in sys.argv) else logging.INFO, format="  %(message)s")  # shows which read-only tool was used
sys.exit(main(sys.argv[1:]))
