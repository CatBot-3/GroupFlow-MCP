# Protocol evidence

Captured 2026-10-16T21:00:51Z by `python -m groupflow evidence` (Python 3.13.16, MCP Python SDK 2.3.0, Linux).

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
mcp-session-id: a1c569e28c7849d49ef13f16cfd403a1

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
    "instructions": "GroupFlow keeps shared, permission-checked plans. The connection fixes the speaker. Start each turn with get_context(detail='summary'); read one plan in full when needed. Discover templates in the short directory or list_templates, then get_template for one specification. Record statements with propose_change; hedged ideas are tentative. Owner items wait for their owner, open items allow any member, and activities need each person's own page confirmation followed by organizer commit. You can never confirm, decline or withdraw for anyone; reported group agreement changes no approvals. Report tool outcomes exactly. The server filters visibility and private conditions. create_group starts a group and invite_to_group makes a code; people join only on their own page. When get_context lists shared_with, those people also read the reply and only plans all of them can see are included.",
    "protocolVersion": "2025-11-25",
    "serverInfo": {
      "name": "groupflow",
      "title": "GroupFlow",
      "version": "0.6.0"
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
mcp-session-id: a1c569e28c7849d49ef13f16cfd403a1
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
mcp-session-id: a1c569e28c7849d49ef13f16cfd403a1

"(empty body)"
```

### tools/list

Everyone gets the same 23 generic tools (14 core, 2 template discovery, 5 structured-project, 2 group tools); none can approve an activity, accept an invite or link an account. Each has an input schema, an output schema and annotations.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: a1c569e28c7849d49ef13f16cfd403a1
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
mcp-session-id: a1c569e28c7849d49ef13f16cfd403a1

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
      "invite_to_group"
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
mcp-session-id: a1c569e28c7849d49ef13f16cfd403a1
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
mcp-session-id: a1c569e28c7849d49ef13f16cfd403a1

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
mcp-session-id: 796177741872487c9c3edce72dbb1bdb
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
mcp-session-id: 796177741872487c9c3edce72dbb1bdb

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
mcp-session-id: 796177741872487c9c3edce72dbb1bdb
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
mcp-session-id: 796177741872487c9c3edce72dbb1bdb

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
mcp-session-id: 22a15b8f88be42fc8b0e280be867eaaf

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
    "instructions": "GroupFlow keeps shared, permission-checked plans. The connection fixes the speaker. Start each turn with get_context(detail='summary'); read one plan in full when needed. Discover templates in the short directory or list_templates, then get_template for one specification. Record statements with propose_change; hedged ideas are tentative. Owner items wait for their owner, open items allow any member, and activities need each person's own page confirmation followed by organizer commit. You can never confirm, decline or withdraw for anyone; reported group agreement changes no approvals. Report tool outcomes exactly. The server filters visibility and private conditions. create_group starts a group and invite_to_group makes a code; people join only on their own page. When get_context lists shared_with, those people also read the reply and only plans all of them can see are included.",
    "protocolVersion": "2025-11-25",
    "serverInfo": {
      "name": "groupflow",
      "title": "GroupFlow",
      "version": "0.6.0"
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
mcp-session-id: 22a15b8f88be42fc8b0e280be867eaaf
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
mcp-session-id: 22a15b8f88be42fc8b0e280be867eaaf

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
mcp-session-id: 22a15b8f88be42fc8b0e280be867eaaf
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
mcp-session-id: 22a15b8f88be42fc8b0e280be867eaaf

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
mcp-session-id: 81fe580dcb854039aabb71efcdd24cd8

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
    "instructions": "GroupFlow keeps shared, permission-checked plans. The connection fixes the speaker. Start each turn with get_context(detail='summary'); read one plan in full when needed. Discover templates in the short directory or list_templates, then get_template for one specification. Record statements with propose_change; hedged ideas are tentative. Owner items wait for their owner, open items allow any member, and activities need each person's own page confirmation followed by organizer commit. You can never confirm, decline or withdraw for anyone; reported group agreement changes no approvals. Report tool outcomes exactly. The server filters visibility and private conditions. create_group starts a group and invite_to_group makes a code; people join only on their own page. When get_context lists shared_with, those people also read the reply and only plans all of them can see are included.",
    "protocolVersion": "2025-11-25",
    "serverInfo": {
      "name": "groupflow",
      "title": "GroupFlow",
      "version": "0.6.0"
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
mcp-session-id: 81fe580dcb854039aabb71efcdd24cd8
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
mcp-session-id: 81fe580dcb854039aabb71efcdd24cd8

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
mcp-session-id: 81fe580dcb854039aabb71efcdd24cd8
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
mcp-session-id: 81fe580dcb854039aabb71efcdd24cd8

{
  "jsonrpc": "2.0",
  "id": 8,
  "result": {
    "content": [
      {
        "text": "{\n  \"outcome\": \"created\",\n  \"group\": {\n    \"id\": \"flatmates\",\n    \"name\": \"Flatmates\"\n  },\n  \"code\": \"QM65-RJ3R\",\n  \"expires_at\": \"2026-10-23T21:00:50.810Z\",\n  \"message\": \"Invite code for Flatmates: QM65-RJ3R. Share it; each person opens it and joins on their own GroupFlow page. Nobody can be added for them.\",\n  \"join_url\": \"http://localhost:8788/chat?invite=QM65-RJ3R\"\n}",
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
      "code": "QM65-RJ3R",
      "expires_at": "2026-10-23T21:00:50.810Z",
      "message": "Invite code for Flatmates: QM65-RJ3R. Share it; each person opens it and joins on their own GroupFlow page. Nobody can be added for them.",
      "join_url": "http://localhost:8788/chat?invite=QM65-RJ3R"
    }
  }
}
```

## 5. Refusals

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
mcp-session-id: a1c569e28c7849d49ef13f16cfd403a1
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
mcp-session-id: a1c569e28c7849d49ef13f16cfd403a1
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
mcp-session-id: a1c569e28c7849d49ef13f16cfd403a1

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

## 6. Version negotiation with the official SDK client

The Python MCP SDK client (`mcp` 2.3.0) connects in both of its modes. In `auto` mode it first tries the newer per-request envelope; the server does not offer it, so the client falls back to the handshake.

| SDK client mode | Negotiated protocol version |
| --- | --- |
| `legacy` | `2025-11-25` |
| `auto` | `2025-11-25` |

Tools listed in the last session: 23.

