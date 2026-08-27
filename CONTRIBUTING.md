# Contributing

1. Do not commit API keys, CarSim binaries, commercial vehicle models, DBC/BLF files or generated outputs.
2. Add clear Chinese comments for non-obvious logic and keep functions focused.
3. Run `python -m pytest -q` before submitting changes.
4. New conditions must be disabled until signals, admission rules, simulator adapter, metrics and tests are complete.
5. Changes to metrics, data splits, parameter registry or control policy must update the strategy fingerprint and documentation.
