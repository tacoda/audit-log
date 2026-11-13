"""The published shape of every function and trigger type.

The registry's API reference is read from a running engine, and a function
registered without a typed schema shows up as *unknown* on the worker page.
These dicts are handed to the SDK at registration time so the page can state
the contract instead of shrugging at it.

Plain JSON Schema, because that is what the SDK sends and what the engine
stores. Kept apart from the handlers so the contract can be read without
knowing any of the wiring.
"""

ENTRY = {
    "type": "object",
    "description": (
        "One sealed audit entry. `hash` is SHA-256 over every other field, "
        "including `prev`, which is what makes the log a chain."),
    "properties": {
        "v": {"type": "integer", "description": "Entry format version."},
        "seq": {"type": "integer",
                "description": "Position in the chain, starting at 0."},
        "prev": {"type": "string",
                 "description": "Hash of the entry before; 64 zeros at the start."},
        "kind": {"type": "string",
                 "description": "What kind of decision, in the deployment's vocabulary."},
        "at": {"type": "integer",
               "description": "When the entry was sealed, epoch milliseconds."},
        "actor": {"type": "string",
                  "description": "Who or what decided: a person, a worker, a rule id."},
        "subject": {"type": "string",
                    "description": "What the decision was about."},
        "detail": {"type": "object", "additionalProperties": True,
                   "description": "Whatever else the decision carries."},
        "hash": {"type": "string",
                 "description": "SHA-256 over every field above."},
    },
    "required": ["v", "seq", "prev", "kind", "at", "actor", "subject",
                 "detail", "hash"],
}

FUNCTIONS = {
    "audit::append": {
        "request": {
            "type": "object",
            "properties": {
                "kind": {"type": "string",
                         "description": "The kind of decision. Required."},
                "actor": {"type": "string",
                          "description": "Who decided. Name the person where there was one."},
                "subject": {"type": "string",
                            "description": "What the decision was about."},
                "detail": {"type": "object", "additionalProperties": True},
            },
            "required": ["kind"],
        },
        "response": {
            "type": "object",
            "properties": {
                "seq": {"type": "integer"},
                "hash": {"type": "string",
                         "description": "The proof the caller keeps: seq plus hash names the entry."},
                "kind": {"type": "string"},
                "known_kind": {"type": "boolean",
                               "description": ("False when the kind is not on AUDIT_LOG_KINDS. "
                                               "Recorded anyway.")},
                "error": {"type": "string",
                          "description": "Set only when nothing was recorded."},
            },
        },
    },
    "audit::verify": {
        "request": {"type": "object", "properties": {}},
        "response": {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "entries": {"type": "integer"},
                "verified_through": {"type": "integer",
                                     "description": ("Last seq that verified. "
                                                     "Everything after it is unproven.")},
                "problems": {"type": "array", "items": {"type": "string"}},
                "folder": {"type": "string"},
            },
        },
    },
    "audit::summary": {
        "request": {
            "type": "object",
            "properties": {
                "by": {"type": "array", "items": {"type": "string"},
                       "description": ("Fields to count by: top-level or detail. "
                                       "Defaults to kind and actor.")},
            },
        },
        "response": {
            "type": "object",
            "properties": {
                "entries": {"type": "integer"},
                "files": {"type": "array", "items": {"type": "string"}},
                "verified": {"type": "boolean"},
                "verified_through": {"type": "integer"},
                "problems": {"type": "array", "items": {"type": "string"}},
                "first_at": {"type": "integer"},
                "last_at": {"type": "integer"},
                "by": {"type": "object", "additionalProperties": True,
                       "description": "One counts object per requested field."},
            },
        },
    },
    "audit::read": {
        "request": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "subject": {"type": "string"},
                "limit": {"type": "integer",
                          "description": "Newest N entries. Default 200."},
            },
        },
        "response": {
            "type": "object",
            "properties": {
                "entries": {"type": "array", "items": ENTRY},
                "total": {"type": "integer",
                          "description": "Matching entries, before the limit cut."},
                "problems": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "audit::tally": {
        "request": {
            "type": "object",
            "properties": {
                "by": {"type": "string",
                       "description": "The field to count. Default kind."},
                "kind": {"type": "string",
                         "description": "Narrow to one kind before counting."},
            },
        },
        "response": {
            "type": "object",
            "properties": {
                "counts": {"type": "object", "additionalProperties": True},
                "total": {"type": "integer"},
            },
        },
    },
    "audit::head": {
        "request": {"type": "object", "properties": {}},
        "response": {
            "type": "object",
            "properties": {
                "seq": {"type": "integer",
                        "description": "-1 when the log is empty."},
                "hash": {"type": "string",
                         "description": ("The fact an anchor stores. "
                                         "64 zeros when the log is empty.")},
                "at": {"type": "integer"},
            },
        },
    },
}

# What a bind to `audit::recorded` is configured with, and what the bound
# function receives: the whole sealed entry, which includes the head.
TRIGGER_BIND = {
    "type": "object",
    "properties": {
        "function_id": {"type": "string",
                        "description": "The function told when an entry lands."},
    },
    "required": ["function_id"],
}
