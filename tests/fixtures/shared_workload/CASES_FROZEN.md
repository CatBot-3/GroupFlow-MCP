# Restart-under-load acceptance cases (frozen before implementation inspection)

Freeze date: 2026-10-18. Source: the shared-workload acceptance plan.

The owner of these cases may edit only this evidence directory and `tests/mcp/test_shared_restart.py`. Product fixes belong to the root agent. All people, groups, plans, records, credentials and external effects in this suite are synthetic. No model provider, external service, production database or deployment is used.

## Genuine process termination acceptance

Use one test-owned server subprocess and one persistent SQLite file for the run. Keep multiple authenticated clients active and overlap writes, repairs and human confirmation traffic. Abruptly terminate only the exact subprocess created by the test, restart against the same database, and verify through a newly initialized official MCP SDK client as well as synthetic human HTTP where applicable.

1. **Committed write, lost reply.** Hold a response after a successful durable domain mutation, while another client's request is in flight. Abrupt termination prevents delivery to the first client. After restart, replay its original request key. The saved successful result must be returned for its original actor/plan, and the intended mutation must exist exactly once. A different actor/plan must not retrieve that result by reusing the key.
2. **Queued repair interrupted.** Crash while a persisted repair request is queued behind slow running repair work. After restart, the queued request must reach an explicitly recoverable state (resume/requeue or a visible retryable failure), with no silent indefinite queued/running state and no unapproved plan mutation.
3. **Running repair interrupted.** Establish that the persisted repair job actually began execution before killing the process. After restart, it must resume safely or have a visible recoverable failure. Resubmission/retry may create a new repair draft but cannot apply an abandoned draft or execute external effects.
4. **Stale repair cannot overwrite newer edit.** Preserve a repair draft/job or preview based on an old plan version. Commit an independent newer edit while other workflows continue, then crash/restart. Any old preview/recipe or recovered old solve must be rejected or rendered non-applicable; the newer edit and unrelated plan changes survive.
5. **Pending human confirmations retain the current-authority boundary.** Crash with pending and partial human responses in flight. After restart, absent, stale, forged, cross-plan, withdrawn or no-longer-authorized answers cannot yield a valid committed approval. Current authorized human responses use their own login, CSRF and challenge route. A valid completion can commit once and must bind to the intended plan and scope.
6. **Execution replay is at most once.** Where the product exposes an execution boundary, crash after durable execution/receipt and before a reply. Replaying keys and pending confirmations after restart must not create a second execution or second synthetic external effect. An in-test external simulator may count effects durably; such a count is explicitly a simulator assertion and is not evidence of a real integration.

For each case, retain the first failing trace separately from corrected retests. Record the exact process IDs, database path, requested keys, barrier evidence, kill/restart ordering, relevant persisted states and post-restart SDK results. Transport failures caused intentionally by the crash are expected; internal/domain failures are classified separately.

## Persistent-state fixture supplement

Injected queued/running job records, interrupted receipts or confirmation states may supplement coverage and isolate recovery rules. Label all such checks **injected persistent state**, with separate results and no claim that they prove an actual crash during execution. They do not replace cases 1–6 above.

## Acceptance limits

Local bounded completion timings describe this test run only. A recoverable interrupted repair may require explicit caller retry if the product provides a clear terminal outcome and safe retry contract. A successful repair draft does not constitute a human approval, committed plan mutation or real external action. No assertions will be weakened to match observed implementation behavior; unresolved contract gaps will be reported explicitly.
