# Protocol evidence

Captured 2026-10-16T02:37:16Z by `python -m groupflow evidence` (Python 3.12.4, MCP Python SDK 2.3.0, Windows).

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
mcp-session-id: 045601b22c94428eb81fa83cf1bf3ed1

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
    "instructions": "GroupFlow keeps shared, permission-checked plans. The connection fixes the speaker. Start each turn with get_context(detail='summary'); read one plan in full when needed. Discover templates in the short directory or list_templates, then get_template for one specification. Record statements with propose_change; hedged ideas are tentative. Owner items wait for their owner, open items allow any member, and activities need each person's own page confirmation followed by organizer commit. You can never confirm, decline or withdraw for anyone; reported group agreement changes no approvals. Report tool outcomes exactly. The server filters visibility and private conditions.",
    "protocolVersion": "2025-11-25",
    "serverInfo": {
      "name": "groupflow",
      "title": "GroupFlow",
      "version": "0.3.0"
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
mcp-session-id: 045601b22c94428eb81fa83cf1bf3ed1
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
mcp-session-id: 045601b22c94428eb81fa83cf1bf3ed1

"(empty body)"
```

### tools/list

Everyone gets the same generic tools (14 core tools plus 2 template discovery tools); none can approve an activity. Each has an input schema, an output schema and annotations.

Request:

```http
POST /mcp HTTP/1.1
content-type: application/json
accept: application/json, text/event-stream
authorization: Bearer <alice's token>
mcp-session-id: 045601b22c94428eb81fa83cf1bf3ed1
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
mcp-session-id: 045601b22c94428eb81fa83cf1bf3ed1

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
      "repair_project"
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
mcp-session-id: 045601b22c94428eb81fa83cf1bf3ed1
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
mcp-session-id: 045601b22c94428eb81fa83cf1bf3ed1

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
mcp-session-id: 1b391879e83143d9b4228a9a838ff679
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
mcp-session-id: 1b391879e83143d9b4228a9a838ff679

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
mcp-session-id: 1b391879e83143d9b4228a9a838ff679
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
mcp-session-id: 1b391879e83143d9b4228a9a838ff679

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

## 3. Refusals

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
mcp-session-id: 045601b22c94428eb81fa83cf1bf3ed1
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
mcp-session-id: 045601b22c94428eb81fa83cf1bf3ed1
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
mcp-session-id: 045601b22c94428eb81fa83cf1bf3ed1

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

## 4. Version negotiation with the official SDK client

The Python MCP SDK client (`mcp` 2.3.0) connects in both of its modes. In `auto` mode it first tries the newer per-request envelope; the server does not offer it, so the client falls back to the handshake.

| SDK client mode | Negotiated protocol version |
| --- | --- |
| `legacy` | `2025-11-25` |
| `auto` | `2025-11-25` |

Tools listed in the last session: 21.

