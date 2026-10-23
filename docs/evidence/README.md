# Evaluation reports

The raw reports behind the numbers in [EVALUATION.md](../EVALUATION.md), exactly as `python -m groupflow eval --out` wrote them.

## Current (2026-10-22)

DeepSeek `deepseek-flash`, the 57 realistic scenarios and the 5 planted-instruction scenarios, three runs each, run after the changes listed in EVALUATION.md.

| File | Client | Suites |
| --- | --- | --- |
| [minimal-host.md](minimal-host.md) | Alexa+-like (`--host minimal`): a one-line prompt plus GroupFlow's server instructions, the 13 voice tools, nobody declared as listening | realistic and planted instructions |
| [lab-host.md](lab-host.md) | The simulator's client (`--host lab`), with its long prompt, state read and re-check | realistic and planted instructions |
| [hardest-minimal-host-v4-pro.md](hardest-minimal-host-v4-pro.md) | Alexa+-like, with DeepSeek `deepseek-v4-pro` instead of flash, on the 18 hardest scenarios only, before the last two fixes | realistic (18 scenarios) |

The first run's reports (2026-10-20), before the fixes, are in the repository's history, in this folder as of commit 6ec60e0.
