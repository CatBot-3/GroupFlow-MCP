# One story: Friday with friends (target 170 seconds)

The product demonstration uses one everyday situation. Other groups remain in **Test lab** to test the MCP structure and model behavior; do not splice them into the main story. This is a recording plan, not a claim that a video has already been produced.

Start with a separate synthetic demo database:

```powershell
python -m groupflow demo --db data/friends-demo.db
```

Open the default Friends demo. If replaying an already completed story, use **Reset this group** to restore the seeded agreement. Do not reset a database containing work you want to keep. The nine guided steps use the real model, real MCP sessions and real human-only HTTP; the venue event, participants and bookings are simulated.

| Time | Action | Point to demonstrate |
| --- | --- | --- |
| 0–15 s | Show the agreed Friday timeline | Four friends agreed on meet-up, dinner, dessert and a ride. A plan change should not restart every agreement. |
| 15–28 s | Play step 1: report Restaurant A unavailable | This explicitly labelled demo feed stands in for a trusted venue source; the agreement is marked invalid. |
| 28–48 s | Play steps 2–3: Alice asks what happened, then asks for a fix | The assistant reads persisted state and calls the repair tool. Only Dinner changes, to Restaurant B; time and other activities are preserved. |
| 48–68 s | Play step 4: Bob asks why not Restaurant C | The server explains a constraint kind without naming its owner. The assistant does not need everyone's private calendars in its context. |
| 68–90 s | Play step 5: Alice requests confirmations | The top row shows one changed activity, twelve confirmations kept and four answers needed. The same-candidate full-reconfirmation policy would ask sixteen. |
| 90–107 s | Play step 6: Bob asks to approve for everyone | The model cannot produce human receipts. The four pending answers remain pending. |
| 107–130 s | Play step 7: show the own-page answers | Each demo identity signs in separately and answers the changed scope. The runner explicitly simulates participants; this is not a real user study. |
| 130–150 s | Play steps 8–9: Alice commits, Dana asks for the final plan | The server checks the current version, conditions and human answers before recording the simulated booking. Counts remain visible after commit. |
| 150–170 s | Open **See how AI uses memory**, then the selected speaker's last turn | Show authorized records and an actual tool result. Distinguish the fresh snapshot from prior model input. End with “repair what changed, preserve what still holds, never invent someone's consent.” |

Keep raw protocol details collapsed unless asked. A technical follow-up can show the exact `request_confirmations` result with `carried_forward: 12`, the committed `agreement_summary`, or the real `initialize` / `tools/list` / `tools/call` traffic. Do not present the semantic memory graph as model chain-of-thought.

The N key advances a step outside text fields. Model wording and latency vary, so rehearse with the configured provider. A failed model request or human answer must be retried; the UI does not silently advance that step.

The corporate world remains available separately with `python -m groupflow demo --world demo/enterprise.yaml --db data/enterprise.db`. It supports structural coverage, not a second main demo narrative.
