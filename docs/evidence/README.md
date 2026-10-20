# Evaluation reports

The raw reports behind the numbers in [EVALUATION.md](../EVALUATION.md), exactly as `python -m groupflow eval --out` wrote them on 2026-10-20 (DeepSeek `deepseek-flash`, three runs of each scenario).

| File | Host | Suites |
| --- | --- | --- |
| [minimal-host.md](minimal-host.md) | Minimal (Alexa+-like): one-line prompt plus server instructions, voice tools, no listener declaration | realistic and adversarial |
| [lab-host.md](lab-host.md) | The simulator's lab host | realistic and adversarial |
| [injection-minimal-host.md](injection-minimal-host.md) | Minimal | adversarial only, run again after its fixtures were fixed |
| [injection-lab-host.md](injection-lab-host.md) | Lab | adversarial only, run again after its fixtures were fixed |

In the two combined reports, INJ04 and INJ05 fail in every run with a setup error from the test fixtures (a bring-list entry over the length limit, an expense recorded without naming its plan), not with anything the model did. Their headline rows therefore include those six errors; EVALUATION.md reports the realistic suites from the per-scenario tables and the injection suite from the separate runs.
