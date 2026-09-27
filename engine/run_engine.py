"""PyInstaller entry point. Freezing barnaby_engine/__main__.py directly would
run it as a top-level script, breaking its package-relative imports."""
from barnaby_engine.__main__ import main

if __name__ == "__main__":
    main()
