---
name: audit-log
description: Record human decisions in an append-only, hash-chained audit log. Verify, read and anchor the record.
---

# audit-log

Every human decision that matters gets one line here. The log is
append-only and hash-chained. Nothing rewrites an entry, and any edit,
deletion, reorder or full rebuild breaks verification at a named entry.

## When to append

Call `audit::append` the moment a decision lands. One call per decision.

- `kind`: the decision's name in the project's vocabulary, for example
  `approval.resolved` or `publish.granted`. The call needs this field.
  Declare the vocabulary in `AUDIT_LOG_KINDS` so typos get flagged.
- `actor`: who decided. Name the person when a person decided. This field is
  the accountability chain.
- `subject`: what the decision was about, such as a PR number or a job id.
- `detail`: everything else worth keeping: the chosen option, the reason, a
  link.

The call returns `seq` and `hash`. Keep them when a caller needs to prove
later that it recorded the decision. `known_kind: false` means the kind is
not declared. The log still records the entry.

## With approval-gate

approval-gate holds function calls for a human and announces each
resolution. It keeps no decision record. That gap is this worker's job.
Observe the approval-gate resolution event from a small adapter that calls
`audit::append` once per resolution. Use `kind: approval.resolved`, set
`actor` to the human who answered, and set `subject` to the held call. See
the approval-gate page on the registry for its function ids.

audit-log also stands alone. Any caller that decides anything can append
without approval-gate installed.

## Reading and proving

- `audit::verify`: is the chain intact, and from which entry is it not.
- `audit::head`: the seq and hash of the last entry. An anchor stores this
  pair outside the log directory. After any tampering, including a full
  rebuild of the directory, the stored pair stops matching.
- `audit::read`, `audit::tally`, `audit::summary`: entries and counts.
- `audit::recorded`: observe it from any worker to hear about every sealed
  entry, head included. Anchors bind here.

On a machine without an engine, the same answers come from the files:

```bash
python src/main.py check   # chain plus counts, non-zero on a break
python src/main.py head
python src/main.py read kind=approval.resolved
```

## Rules

Never append from more than one place at once. The worker is the one writer.
A script that appends beside it produces a chain that fails its own
verification.
