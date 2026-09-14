"""Compatibility CLI: R16-R1 uses the original production Python oracle."""
from r16_production_oracle import main

if __name__ == "__main__":
    raise SystemExit(main())
