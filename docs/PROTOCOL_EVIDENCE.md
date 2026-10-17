# Protocol evidence

Captured 2026-10-17T06:24:59Z by `python -m groupflow evidence` (Python 3.13.16, MCP Python SDK 2.3.0, Linux).

GroupFlow is a remote MCP server: Streamable HTTP on `/mcp`, JSON-RPC 2.0, protocol version **2025-11-25**, bearer-token identity. Everything below is the real traffic of one run against a throwaway database; only the tokens are redacted.

## 1. Handshake, discovery and a tool call (Alice, who has three plans in two groups)

### initialize

The client offers 2025-11-25; the server answers with the same version, its capabilities and its instructions.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-11-25",
    "capabilities": {},
    "clientInfo": {
      "name": "evidence-client",
      "version": "1.0"
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 439899d69aef405cbefd0968af070c7c

{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "capabilities": {
      "prompts": {
        "listChanged": false
      },
      "resources": {
        "listChanged": false,
        "subscribe": false
      },
      "tools": {
        "listChanged": false
      }
    },
    "instructions": "GroupFlow keeps shared, permission-checked plans. The connection fixes the speaker. Start each turn with get_context(detail='summary'); People talk casually (slang, typos, jokes, venting, side chatter): act only on what they ask or state about a plan, let the last version of a self-correction win, and change nothing for chatter. read one plan in full when needed. Discover templates in the short directory or list_templates, then get_template for one specification. Record statements with propose_change; hedged ideas are tentative. Owner items wait for their owner, open items allow any member, and activities need each person's own page confirmation followed by organizer commit. You can never confirm, decline or withdraw for anyone; reported group agreement changes no approvals. Report tool outcomes exactly. The server filters visibility and private conditions. create_group starts a group and invite_to_group makes a code; people join only on their own page. When get_context lists shared_with, those people also read the reply and only plans all of them can see are included. To tell others about a decision use notify_people; it reaches only people who can see the plan. If a result has suggested_followup, offer it at the end of your reply and pass their answer to answer_followup. Never guess a vague time, amount or plan: ask with ask_followup (concrete options, each with the exact call), then send their reply to answer_followup. get_context lists open_questions. set_reminder schedules a reminder for a stated time; review_plan says what a plan waits on and sets automatic check-ins.",
    "protocolVersion": "2025-11-25",
    "serverInfo": {
      "name": "groupflow",
      "title": "GroupFlow",
      "version": "0.7.0"
    }
  }
}
```

### notifications/initialized

A notification: no id, answered with 202 and no body.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: 439899d69aef405cbefd0968af070c7c
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "method": "notifications/initialized"
}
```

Response:

```http
HTTP/1.1 202 Accepted
content-type: application/json
mcp-session-id: 439899d69aef405cbefd0968af070c7c

"(empty body)"
```

### tools/list

Everyone gets the same 26 generic tools (14 core, 2 template discovery, 5 structured-project, 2 group tools, 3 for telling people and asking first); none can approve an activity, accept an invite or link an account. Each has an input schema, an output schema and annotations.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: 439899d69aef405cbefd0968af070c7c
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/list"
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 439899d69aef405cbefd0968af070c7c

{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools (names)": [
      "get_context",
      "search_plan",
      "create_plan",
      "propose_change",
      "decide_change",
      "link_items",
      "set_my_conditions",
      "report_observation",
      "request_confirmations",
      "find_alternatives",
      "get_alternatives",
      "commit_plan",
      "explain",
      "get_history",
      "list_templates",
      "get_template",
      "get_project",
      "configure_project",
      "upsert_work",
      "manage_recurrence",
      "repair_project",
      "create_group",
      "invite_to_group",
      "notify_people",
      "ask_followup",
      "answer_followup",
      "set_reminder",
      "review_plan"
    ],
    "first tool in full": {
      "annotations": {
        "destructiveHint": false,
        "idempotentHint": false,
        "openWorldHint": false,
        "readOnlyHint": false
      },
      "description": "Read fresh state. summary includes a paged plan/template index, key item ids, the speaker's open tasks and updates across visible plans, plus details of the selected plan. Focus does not hide other plans' new work. Use returned ids; read another plan before guessing a venue or value. your_conditions is ONLY the speaker's: an empty list never means nobody else has private limits. Returned updates with mark_read=true are marked heard.",
      "inputSchema": {
        "properties": {
          "plan": {
            "anyOf": [
              {
                "type": "string"
              },
              {
                "type": "null"
              }
            ],
            "default": null,
            "description": "Only this plan (id or title). Default: every plan the speaker can see.",
            "title": "Plan"
          },
          "mark_read": {
            "anyOf": [
              {
                "type": "boolean"
              },
              {
                "type": "null"
              }
            ],
            "default": null,
            "description": "Mark returned updates as heard. Default true.",
            "title": "Mark Read"
          },
          "detail": {
            "default": "full",
            "description": "summary: short index plus the selected plan; full: detailed plan views.",
            "enum": [
              "summary",
              "full"
            ],
            "title": "Detail",
            "type": "string"
          },
          "group": {
            "anyOf": [
              {
                "type": "string"
              },
              {
                "type": "null"
              }
            ],
            "default": null,
            "description": "Optional group id or name; visibility is still checked.",
            "title": "Group"
          },
          "limit": {
            "default": 12,
            "description": "Page size for the summary index and updates.",
            "maximum": 50,
            "minimum": 1,
            "title": "Limit",
            "type": "integer"
          },
          "offset": {
            "default": 0,
            "description": "Summary index offset.",
            "minimum": 0,
            "title": "Offset",
            "type": "integer"
          }
        },
        "type": "object",
        "title": "get_contextArguments"
      },
      "name": "get_context",
      "outputSchema": "(omitted here; present on every tool)",
      "title": "Everything the speaker needs to know"
    }
  }
}
```

### tools/call get_context

Results come back as structuredContent (validated against the output schema) plus a text copy for older clients. Alice sees the surprise party, the family's Home plan and the Friday dinner.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: 439899d69aef405cbefd0968af070c7c
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "get_context",
    "arguments": {
      "mark_read": false
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 439899d69aef405cbefd0968af070c7c

{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "structuredContent (excerpt)": {
      "you": "Alice",
      "headline": "4 items to confirm on your page.",
      "plans": [
        {
          "title": "Friday dinner",
          "kind": "Evening out",
          "visible_to": [
            "Alice",
            "Bob",
            "Chen",
            "Dana"
          ],
          "status": "AWAITING_CONFIRMATIONS",
          "next_step": "Waiting for Alice, Bob, Chen and Dana to confirm P1 on their own GroupFlow page. Nobody can confirm for someone else."
        },
        {
          "title": "Linda's surprise party",
          "kind": "Surprise party",
          "visible_to": [
            "Alice",
            "Bob",
            "Charlie"
          ],
          "status": "ACTIVE",
          "next_step": "Nothing is waiting. Owners decide their own details; tasks belong to whoever does them."
        },
        {
          "title": "Home",
          "kind": "Home, chores and shopping",
          "visible_to": [
            "Alice",
            "Bob",
            "Charlie",
            "Linda"
          ],
          "status": "ACTIVE",
          "next_step": "Nothing is waiting. Owners decide their own details; tasks belong to whoever does them."
        }
      ],
      "your_conditions": [
        "Latest time to be home: 21:00",
        "Budget for the evening: at most CAD 45.00 in total",
        "Everyone attends Dinner"
      ]
    }
  }
}
```

## 2. The same endpoint, the guest of honour (Linda)

### tools/call get_context as Linda

Linda's view comes from the same tool: the party plan lives in a space without her, so it is simply not there.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <linda's token>
mcp-session-id: f02e1d9667ed41c2821fd74d331f1017
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "get_context",
    "arguments": {
      "mark_read": false
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: f02e1d9667ed41c2821fd74d331f1017

{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "structuredContent (excerpt)": {
      "you": "Linda",
      "headline": "Nothing new.",
      "plans": [
        {
          "title": "Home",
          "kind": "Home, chores and shopping",
          "visible_to": [
            "Alice",
            "Bob",
            "Charlie",
            "Linda"
          ],
          "status": "ACTIVE",
          "next_step": "Nothing is waiting. Owners decide their own details; tasks belong to whoever does them."
        }
      ],
      "your_conditions": []
    }
  }
}
```

### tools/call get_context for the hidden plan

Asking for it by id is refused like any plan that does not exist for her.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <linda's token>
mcp-session-id: f02e1d9667ed41c2821fd74d331f1017
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "get_context",
    "arguments": {
      "plan": "party"
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: f02e1d9667ed41c2821fd74d331f1017

{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "content": [
      {
        "text": "{\"error\": {\"code\": \"NOT_FOUND\", \"message\": \"No such plan is available to you. Your plans: Home (id home).\", \"details\": {}}}",
        "type": "text"
      }
    ],
    "isError": true
  }
}
```

## 3. A shared conversation: the host declares who else will read the reply

### initialize with X-GroupFlow-Listeners

A group chat host (or a shared kitchen device) opens Alice's session declaring the family group as listeners. The header can only narrow what Alice's connection reads, never widen it.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
x-groupflow-listeners: group:family

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-11-25",
    "capabilities": {},
    "clientInfo": {
      "name": "evidence-client",
      "version": "1.0"
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: d7d5178ca32143eb89cdc752577a9753

{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "capabilities": {
      "prompts": {
        "listChanged": false
      },
      "resources": {
        "listChanged": false,
        "subscribe": false
      },
      "tools": {
        "listChanged": false
      }
    },
    "instructions": "GroupFlow keeps shared, permission-checked plans. The connection fixes the speaker. Start each turn with get_context(detail='summary'); People talk casually (slang, typos, jokes, venting, side chatter): act only on what they ask or state about a plan, let the last version of a self-correction win, and change nothing for chatter. read one plan in full when needed. Discover templates in the short directory or list_templates, then get_template for one specification. Record statements with propose_change; hedged ideas are tentative. Owner items wait for their owner, open items allow any member, and activities need each person's own page confirmation followed by organizer commit. You can never confirm, decline or withdraw for anyone; reported group agreement changes no approvals. Report tool outcomes exactly. The server filters visibility and private conditions. create_group starts a group and invite_to_group makes a code; people join only on their own page. When get_context lists shared_with, those people also read the reply and only plans all of them can see are included. To tell others about a decision use notify_people; it reaches only people who can see the plan. If a result has suggested_followup, offer it at the end of your reply and pass their answer to answer_followup. Never guess a vague time, amount or plan: ask with ask_followup (concrete options, each with the exact call), then send their reply to answer_followup. get_context lists open_questions. set_reminder schedules a reminder for a stated time; review_plan says what a plan waits on and sets automatic check-ins.",
    "protocolVersion": "2025-11-25",
    "serverInfo": {
      "name": "groupflow",
      "title": "GroupFlow",
      "version": "0.7.0"
    }
  }
}
```

### tools/call get_context in the family chat

Same tool, same speaker: Linda is listening, so the party is not in this conversation at all, and Alice's own private conditions are left out too.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
x-groupflow-listeners: group:family
mcp-session-id: d7d5178ca32143eb89cdc752577a9753
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "tools/call",
  "params": {
    "name": "get_context",
    "arguments": {
      "mark_read": false
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: d7d5178ca32143eb89cdc752577a9753

{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "structuredContent (excerpt)": {
      "you": "Alice",
      "headline": "Nothing new.",
      "plans": [
        {
          "title": "Home",
          "kind": "Home, chores and shopping",
          "visible_to": [
            "Alice",
            "Bob",
            "Charlie",
            "Linda"
          ],
          "status": "ACTIVE",
          "next_step": "Nothing is waiting. Owners decide their own details; tasks belong to whoever does them."
        }
      ],
      "your_conditions": [],
      "shared_with": [
        "Bob",
        "Charlie",
        "Linda"
      ],
      "shared_note": "Shared conversation: Bob, Charlie and Linda will also read this reply. Only plans all of them can see are included; the speaker's private conditions, private plans and plans hidden from any of them are left out, not missing. For those, the speaker can message the assistant privately."
    }
  }
}
```

### tools/call create_plan private in the family chat

A plan some listener could not see cannot be started where they are listening.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
x-groupflow-listeners: group:family
mcp-session-id: d7d5178ca32143eb89cdc752577a9753
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 6,
  "method": "tools/call",
  "params": {
    "name": "create_plan",
    "arguments": {
      "template": "general_event",
      "title": "Gift ideas",
      "private": true
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: d7d5178ca32143eb89cdc752577a9753

{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {
    "content": [
      {
        "text": "{\"error\": {\"code\": \"SHARED_CONVERSATION\", \"message\": \"Other people are reading this conversation, so a plan only you can see cannot be made here. Message the assistant privately to keep it to yourself. No plan was created.\", \"details\": {}}}",
        "type": "text"
      }
    ],
    "isError": true
  }
}
```

## 4. A person who signed up links an assistant (simulated account linking)

Maya signs up and, on her own page (`POST /human/links` with her session and CSRF token), lets the chat host act for her. The host receives a revocable token once; it is an ordinary bearer token for Maya on `/mcp`.

### initialize with a linked token

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <maya (linked)'s token>

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-11-25",
    "capabilities": {},
    "clientInfo": {
      "name": "evidence-client",
      "version": "1.0"
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 0c2166ae420343e9839f89bc72389ed3

{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "capabilities": {
      "prompts": {
        "listChanged": false
      },
      "resources": {
        "listChanged": false,
        "subscribe": false
      },
      "tools": {
        "listChanged": false
      }
    },
    "instructions": "GroupFlow keeps shared, permission-checked plans. The connection fixes the speaker. Start each turn with get_context(detail='summary'); People talk casually (slang, typos, jokes, venting, side chatter): act only on what they ask or state about a plan, let the last version of a self-correction win, and change nothing for chatter. read one plan in full when needed. Discover templates in the short directory or list_templates, then get_template for one specification. Record statements with propose_change; hedged ideas are tentative. Owner items wait for their owner, open items allow any member, and activities need each person's own page confirmation followed by organizer commit. You can never confirm, decline or withdraw for anyone; reported group agreement changes no approvals. Report tool outcomes exactly. The server filters visibility and private conditions. create_group starts a group and invite_to_group makes a code; people join only on their own page. When get_context lists shared_with, those people also read the reply and only plans all of them can see are included. To tell others about a decision use notify_people; it reaches only people who can see the plan. If a result has suggested_followup, offer it at the end of your reply and pass their answer to answer_followup. Never guess a vague time, amount or plan: ask with ask_followup (concrete options, each with the exact call), then send their reply to answer_followup. get_context lists open_questions. set_reminder schedules a reminder for a stated time; review_plan says what a plan waits on and sets automatic check-ins.",
    "protocolVersion": "2025-11-25",
    "serverInfo": {
      "name": "groupflow",
      "title": "GroupFlow",
      "version": "0.7.0"
    }
  }
}
```

### tools/call create_group

Starting a group adds nobody else.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <maya (linked)'s token>
mcp-session-id: 0c2166ae420343e9839f89bc72389ed3
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "create_group",
    "arguments": {
      "name": "Flatmates"
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 0c2166ae420343e9839f89bc72389ed3

{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "content": [
      {
        "text": "{\n  \"outcome\": \"created\",\n  \"group\": {\n    \"id\": \"flatmates\",\n    \"name\": \"Flatmates\",\n    \"timezone\": \"America/Toronto\",\n    \"currency\": \"CAD\",\n    \"description\": null,\n    \"members\": [\n      {\n        \"id\": \"maya\",\n        \"name\": \"Maya\",\n        \"role\": \"organizer\",\n        \"label\": null,\n        \"color\": \"#6B46C1\"\n      }\n    ]\n  },\n  \"message\": \"Started Flatmates with you as organizer. Invite people with invite_to_group; each person joins on their own GroupFlow page.\"\n}",
        "type": "text"
      }
    ],
    "isError": false,
    "structuredContent": {
      "outcome": "created",
      "group": {
        "id": "flatmates",
        "name": "Flatmates",
        "timezone": "America/Toronto",
        "currency": "CAD",
        "description": null,
        "members": [
          {
            "id": "maya",
            "name": "Maya",
            "role": "organizer",
            "label": null,
            "color": "#6B46C1"
          }
        ]
      },
      "message": "Started Flatmates with you as organizer. Invite people with invite_to_group; each person joins on their own GroupFlow page."
    }
  }
}
```

### tools/call invite_to_group

The assistant can make an invite code; only the invited person can accept it, on their own page.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <maya (linked)'s token>
mcp-session-id: 0c2166ae420343e9839f89bc72389ed3
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 8,
  "method": "tools/call",
  "params": {
    "name": "invite_to_group",
    "arguments": {}
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 0c2166ae420343e9839f89bc72389ed3

{
  "jsonrpc": "2.0",
  "id": 8,
  "result": {
    "content": [
      {
        "text": "{\n  \"outcome\": \"created\",\n  \"group\": {\n    \"id\": \"flatmates\",\n    \"name\": \"Flatmates\"\n  },\n  \"code\": \"WJGB-8A7P\",\n  \"expires_at\": \"2026-10-24T06:24:58.722Z\",\n  \"message\": \"Invite code for Flatmates: WJGB-8A7P. Share it; each person opens it and joins on their own GroupFlow page. Nobody can be added for them.\",\n  \"join_url\": \"http://localhost:8788/chat?invite=WJGB-8A7P\"\n}",
        "type": "text"
      }
    ],
    "isError": false,
    "structuredContent": {
      "outcome": "created",
      "group": {
        "id": "flatmates",
        "name": "Flatmates"
      },
      "code": "WJGB-8A7P",
      "expires_at": "2026-10-24T06:24:58.722Z",
      "message": "Invite code for Flatmates: WJGB-8A7P. Share it; each person opens it and joins on their own GroupFlow page. Nobody can be added for them.",
      "join_url": "http://localhost:8788/chat?invite=WJGB-8A7P"
    }
  }
}
```

## 5. Telling people and asking first

### tools/call propose_change (a decision, and the server's offer)

Alice owns the venue, so the change takes effect. The result carries the server's offer to tell the people who can see the plan and did not hear about it; Linda is not among them.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: 439899d69aef405cbefd0968af070c7c
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 11,
  "method": "tools/call",
  "params": {
    "name": "propose_change",
    "arguments": {
      "item": "party.venue",
      "value": "Lucky Star karaoke",
      "quote": "let's just do karaoke at lucky star"
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 439899d69aef405cbefd0968af070c7c

{
  "jsonrpc": "2.0",
  "id": 11,
  "result": {
    "structuredContent": {
      "outcome": "confirmed",
      "message": "Venue is now \"Lucky Star karaoke\" (was \"Rosa's Trattoria, back room\").",
      "plan": "Linda's surprise party",
      "item": "party.venue",
      "request": null,
      "impacts": {
        "reopened": [
          "Book Rosa's back room"
        ],
        "flagged": [
          "Send invitations"
        ],
        "downstream": [
          "Decorate the back room",
          "Drive Linda to the party"
        ],
        "notified": [
          "Charlie"
        ],
        "unblocked": []
      },
      "plan_status": null,
      "problems": [],
      "suggested_followup": {
        "id": "fu_2d804e67",
        "question": "Want me to let Bob know that Venue is now \"Lucky Star karaoke\"?",
        "options": [
          "yes",
          "no"
        ]
      }
    }
  }
}
```

### tools/call answer_followup

Alice says yes. The server matches her words to the offer and runs exactly the notify_people call behind it, as Alice. Each notice is written by the server from the plan's state.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: 439899d69aef405cbefd0968af070c7c
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 12,
  "method": "tools/call",
  "params": {
    "name": "answer_followup",
    "arguments": {
      "choice": "yeah go for it"
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 439899d69aef405cbefd0968af070c7c

{
  "jsonrpc": "2.0",
  "id": 12,
  "result": {
    "structuredContent": {
      "outcome": "answered",
      "id": "fu_2d804e67",
      "question": "Want me to let Bob know that Venue is now \"Lucky Star karaoke\"?",
      "choice": "yes",
      "action": {
        "tool": "notify_people",
        "ok": true,
        "result": {
          "outcome": "sent",
          "plan": "Linda's surprise party",
          "about": [
            "Venue"
          ],
          "recipients": [
            {
              "id": "bob",
              "name": "Bob",
              "role": "fyi",
              "text": "Alice asked Alexa to let you know about Linda's surprise party: Venue is \"Lucky Star karaoke\".",
              "notice_id": "n_b9c82162"
            }
          ],
          "skipped": [],
          "message": "Told Bob. They hear it next time they talk to Alexa or open GroupFlow. Nothing in the plan changed, and nobody's agreement was given."
        }
      },
      "message": "Ran notify_people as they chose; report its outcome exactly."
    }
  }
}
```

### tools/call notify_people with a note that names the surprise

The family's Home plan reaches Linda, and the note names a plan kept from her, so nobody is told.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: 439899d69aef405cbefd0968af070c7c
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 13,
  "method": "tools/call",
  "params": {
    "name": "notify_people",
    "arguments": {
      "plan": "home",
      "note": "the surprise party is at Lucky Star karaoke now"
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 439899d69aef405cbefd0968af070c7c

{
  "jsonrpc": "2.0",
  "id": 13,
  "result": {
    "isError": true,
    "content (parsed)": {
      "error": {
        "code": "PRIVATE_NOTE",
        "message": "Nobody was told: the note mentions something Linda cannot see. Say it without that part, or tell only people who can see it.",
        "details": {}
      }
    }
  }
}
```

### tools/call propose_change from vague words

The words name no time, so the server refuses to record one the model would have to invent.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: 439899d69aef405cbefd0968af070c7c
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 14,
  "method": "tools/call",
  "params": {
    "name": "propose_change",
    "arguments": {
      "item": "party.date",
      "value": "Saturday, Oct 24, 7:00 pm",
      "quote": "can we push it back a bit"
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 439899d69aef405cbefd0968af070c7c

{
  "jsonrpc": "2.0",
  "id": 14,
  "result": {
    "isError": true,
    "content (parsed)": {
      "error": {
        "code": "VAGUE_TIME",
        "message": "The speaker did not say exactly when (\"push it back\"), so nothing changed. Ask which time they mean before changing anything: ask_followup with two or three concrete times as options (each option's action is this same change with that time), or ask in your reply and record the time they give.",
        "details": {
          "said": "push it back",
          "current": "Saturday, Oct 24, 6:00 pm"
        }
      }
    }
  }
}
```

### tools/call ask_followup

The server keeps the question and the exact call behind each option. This server sends JSON responses, so it cannot ask through the host (elicitation needs GROUPFLOW_MCP_RESPONSES=sse); the question comes back for the assistant to ask.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: 439899d69aef405cbefd0968af070c7c
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 15,
  "method": "tools/call",
  "params": {
    "name": "ask_followup",
    "arguments": {
      "question": "Push it to 6:30 or 7?",
      "options": [
        {
          "label": "6:30",
          "action": {
            "tool": "propose_change",
            "arguments": {
              "item": "party.date",
              "value": "Saturday, Oct 24, 6:30 pm",
              "quote": "can we push it back a bit"
            }
          }
        },
        {
          "label": "7:00",
          "action": {
            "tool": "propose_change",
            "arguments": {
              "item": "party.date",
              "value": "Saturday, Oct 24, 7:00 pm",
              "quote": "can we push it back a bit"
            }
          }
        }
      ]
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 439899d69aef405cbefd0968af070c7c

{
  "jsonrpc": "2.0",
  "id": 15,
  "result": {
    "structuredContent": {
      "outcome": "pending",
      "id": "fu_f93325d5",
      "question": "Push it to 6:30 or 7?",
      "options": [
        "6:30",
        "7:00"
      ],
      "message": "Ask this in your reply, with the options. When they answer, call answer_followup with their words; the server runs what they chose."
    }
  }
}
```

### tools/call answer_followup with a bare time

"7" picks the 7:00 option and the change runs as Alice, with every check. Bob and Charlie own work that watches the date, so the change itself flags it for them, and there is nobody left to offer to tell.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: 439899d69aef405cbefd0968af070c7c
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 16,
  "method": "tools/call",
  "params": {
    "name": "answer_followup",
    "arguments": {
      "choice": "7"
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 439899d69aef405cbefd0968af070c7c

{
  "jsonrpc": "2.0",
  "id": 16,
  "result": {
    "structuredContent": {
      "outcome": "answered",
      "id": "fu_f93325d5",
      "question": "Push it to 6:30 or 7?",
      "choice": "7:00",
      "action": {
        "tool": "propose_change",
        "ok": true,
        "result": {
          "outcome": "confirmed",
          "message": "Party date & time is now \"Saturday, Oct 24, 7:00 pm\" (was \"Saturday, Oct 24, 6:00 pm\").",
          "plan": "Linda's surprise party",
          "item": "party.date",
          "request": null,
          "impacts": {
            "reopened": [
              "Order the cake"
            ],
            "flagged": [
              "Weekend family plan",
              "Book Rosa's back room",
              "Drive Linda to the party",
              "Send invitations"
            ],
            "downstream": [
              "Decorate the back room",
              "Pick up the cake"
            ],
            "notified": [
              "Bob",
              "Charlie"
            ],
            "unblocked": []
          },
          "plan_status": null,
          "problems": [],
          "suggested_followup": null
        }
      },
      "message": "Ran propose_change as they chose; report its outcome exactly."
    }
  }
}
```

## 6. Reminders and checking progress

### tools/call set_reminder with a vague time

"Later" names no time, so nothing is scheduled. The server keeps a question with concrete times, each carrying the same reminder at that time.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: 439899d69aef405cbefd0968af070c7c
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 17,
  "method": "tools/call",
  "params": {
    "name": "set_reminder",
    "arguments": {
      "when": "later",
      "about": "cake.order"
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 439899d69aef405cbefd0968af070c7c

{
  "jsonrpc": "2.0",
  "id": 17,
  "result": {
    "isError": true,
    "content (parsed)": {
      "error": {
        "code": "WHEN_UNCLEAR",
        "message": "Say exactly when: a time (\"tomorrow at 9\"), a delay (\"in 2 hours\") or relative to the plan (\"1 day before\"). Ask the speaker rather than guessing.",
        "details": {
          "said": "later",
          "plan": "party",
          "followup": {
            "id": "fu_fe647518",
            "question": "When should the reminder go off: this evening at 7, tomorrow at 9am, the day before at 9am or the morning of?",
            "options": [
              "This evening at 7",
              "Tomorrow at 9am",
              "The day before at 9am",
              "The morning of"
            ]
          }
        }
      }
    }
  }
}
```

### tools/call answer_followup picks a time

The reminder is set for the time Alice picked. Who receives a reminder addressed by role is worked out when it goes off, and its facts are read from the plan then.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: 439899d69aef405cbefd0968af070c7c
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 18,
  "method": "tools/call",
  "params": {
    "name": "answer_followup",
    "arguments": {
      "choice": "tomorrow at 9am"
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 439899d69aef405cbefd0968af070c7c

{
  "jsonrpc": "2.0",
  "id": 18,
  "result": {
    "structuredContent": {
      "outcome": "answered",
      "id": "fu_fe647518",
      "question": "When should the reminder go off: this evening at 7, tomorrow at 9am, the day before at 9am or the morning of?",
      "choice": "Tomorrow at 9am",
      "action": {
        "tool": "set_reminder",
        "ok": true,
        "result": {
          "outcome": "scheduled",
          "id": "rm_15298bd9",
          "plan": "Linda's surprise party",
          "when": "Sun, Oct 18, 9:00 am",
          "to": "you",
          "about": [
            "Cake"
          ],
          "message": "Reminder set for Sun, Oct 18, 9:00 am for you. At that time it shows up in GroupFlow, and Alexa mentions it on your next turn."
        }
      },
      "message": "Ran set_reminder as they chose; report its outcome exactly."
    }
  }
}
```

### tools/call review_plan

What the plan is waiting on, from whom and for how long: the same report the server's automatic check-ins use.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: 439899d69aef405cbefd0968af070c7c
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 19,
  "method": "tools/call",
  "params": {
    "name": "review_plan",
    "arguments": {
      "plan": "friday_dinner"
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 439899d69aef405cbefd0968af070c7c

{
  "jsonrpc": "2.0",
  "id": 19,
  "result": {
    "structuredContent": {
      "plan": "Friday dinner",
      "status": "waiting for answers",
      "quiet_for": "less than an hour",
      "event": "Fri, Oct 23, 6:00 pm",
      "checkins": "after 24 quiet hours",
      "stuck": false,
      "waiting": [
        {
          "what": "answers on the sent plan (P1)",
          "waiting_on": [
            "you",
            "Bob",
            "Chen",
            "Dana"
          ],
          "for": "less than an hour",
          "next_step": "each of them confirms or declines on their own GroupFlow page"
        }
      ],
      "message": "Friday dinner has been quiet for less than an hour. Waiting on: answers on the sent plan (P1) from you, Bob, Chen and Dana."
    }
  }
}
```

## 7. Refusals

### No bearer token

Identity comes only from the token. Without one the server answers 401 with a WWW-Authenticate challenge.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-11-25",
    "capabilities": {},
    "clientInfo": {
      "name": "evidence-client",
      "version": "1.0"
    }
  }
}
```

Response:

```http
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer realm="groupflow"
content-type: application/json

{
  "jsonrpc": "2.0",
  "error": {
    "code": -32001,
    "message": "Missing or invalid bearer token."
  },
  "id": null
}
```

### Browser request from an unknown origin

Origin validation (DNS-rebinding and cross-site protection).

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
origin: https://evil.example

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-11-25",
    "capabilities": {},
    "clientInfo": {
      "name": "evidence-client",
      "version": "1.0"
    }
  }
}
```

Response:

```http
HTTP/1.1 403 Forbidden
content-type: application/json

{
  "jsonrpc": "2.0",
  "error": {
    "code": -32000,
    "message": "Origin https://evil.example is not allowed."
  },
  "id": null
}
```

### Another member reusing Alice's session

Sessions are bound to the member who opened them.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <bob's token>
mcp-session-id: 439899d69aef405cbefd0968af070c7c
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 9,
  "method": "tools/list"
}
```

Response:

```http
HTTP/1.1 403 Forbidden
content-type: application/json

{
  "jsonrpc": "2.0",
  "error": {
    "code": -32003,
    "message": "This session belongs to a different member."
  },
  "id": null
}
```

### A tool that does not exist

There is no tool that approves, declines or withdraws for anyone.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: 439899d69aef405cbefd0968af070c7c
mcp-protocol-version: 2025-11-25

{
  "jsonrpc": "2.0",
  "id": 10,
  "method": "tools/call",
  "params": {
    "name": "approve_plan",
    "arguments": {
      "member": "dana"
    }
  }
}
```

Response:

```http
HTTP/1.1 200 OK
content-type: application/json
mcp-session-id: 439899d69aef405cbefd0968af070c7c

{
  "jsonrpc": "2.0",
  "id": 10,
  "result": {
    "content": [
      {
        "text": "Unknown tool: approve_plan",
        "type": "text"
      }
    ],
    "isError": true
  }
}
```

## 8. Version negotiation with the official SDK client

The Python MCP SDK client (`mcp` 2.3.0) connects in both of its modes. In `auto` mode it first tries the newer per-request envelope; the server does not offer it, so the client falls back to the handshake.

| SDK client mode | Negotiated protocol version |
| --- | --- |
| `legacy` | `2025-11-25` |
| `auto` | `2025-11-25` |

Tools listed in the last session: 28.

