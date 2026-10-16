---
description: Inspect and control learned memory (list, show, forget, demote, pin, rerun --full)
argument-hint: list | show <id> | forget <id> | demote <id> | pin <id> | rerun --full
allowed-tools: Bash(python:*)
---
<!-- learned-memory: managed -->

Run the learned-memory controls and show the output as-is:

```
python -P -m learned.cli $ARGUMENTS
```

If no arguments were given, run `python -P -m learned.cli list`.
