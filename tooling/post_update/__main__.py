import logging
import sys

from .driver import main

logging.basicConfig(level=logging.INFO, format="  %(message)s")  # shows which read-only tool was used
sys.exit(main(sys.argv[1:]))
