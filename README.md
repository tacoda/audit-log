<!-- voice-register: informational -->

# audit-log

**An append-only, tamper-evident record, as an [iii](https://iii.dev) worker.**
One writer, fsynced before it answers, and every entry chained to the one before
it. Nothing here decides anything: callers append, this worker remembers, and
whoever cares observes. It needs iii 0.26.0 or newer, because it speaks
observers.

```bash
iii call compose::add worker=audit-log
```

## The idea

Session transcripts are not an audit log. They are prunable, they are scoped to a
session, and they record what was *said* rather than what was *decided*. An
auditor asking "who allowed this to ship, and on what evidence" cannot be
answered from a store a retention policy is allowed to empty.

Three properties, and the third is the one people skip:

1. **Append-only.** Callers add entries. Nothing rewrites them. Rotation seals a
   file and starts another; it does not delete one.
2. **Durable.** This worker flushes and fsyncs each line before it answers. An
   entry lost in a page cache during a crash is an entry that never existed, and
   the caller has already acted on the decision it records.
3. **Chained.** Each entry carries the hash of the one before it. Without the
   chain the honest claim is "nothing here has been changed as far as we know",
   which is not a claim an auditor accepts. With it, any edit, reorder or
   deletion breaks verification at a nameable line.

**An append-only file you can edit is not an audit log.** That sentence is the
whole reason this exists as its own worker rather than as a table somewhere.

## Why the chain and the store are one thing

The obvious refactor is to split them: a worker that sequences and hashes, and
somebody else's store underneath. It does not work, and both failures are
structural rather than a matter of taste.

**Sequencing needs an authoritative tail.** The next entry chains onto the last
one, and this worker re-reads that one from disk inside the lock. A worker
shipping entries elsewhere would have to keep its tail in memory, which is the
bug below.

**You cannot delegate durability.** Property 2 is an fsync *before* the answer.
Fan-out has to be fire-and-forget: an observer that is down must not turn a
recorded decision into a failed call. Nothing fire-and-forget can promise an
entry is durable.

So this worker owns its files. What it can do is tell everybody afterward.

## It announces what it recorded

`audit::recorded` is a private, observable function. After the fsync seals an
entry, this worker calls it once. The engine then delivers the entry to every
worker that observes the function. The event carries the whole entry,
including the sequence number and hash.

```python
worker.observe("audit::recorded", "my-worker::on-decision")
```

Bind an observability worker and every decision shows up in your traces.
Bind an alerting worker to watch for one kind. Bind an anchoring worker and
it stores each head somewhere the log directory cannot reach.

None of them can weaken the record. The function is private, so only this
worker can fire it. It fires after the entry is durable. The engine delivers
the news off the critical path. An observer is told, never asked.

Your target receives the Observed payload. The entry sits under `input`, and
under `output` too. An event source returns what it is called with. Narrow
with `capture` or `filters` when you want less.

That split is deliberate. A record that lives inside the thing it records is
not independent of it. The deciding belongs to the callers. The remembering
belongs here.

## Functions

| Function | What it does |
|---|---|
| `audit::append` | record one decision. The only way anything gets in |
| `audit::verify` | is the chain intact, and from which entry is it not |
| `audit::summary` | whether it can be trusted, and what it counts |
| `audit::read` | entries, narrowed by `kind` or `subject` |
| `audit::tally` | count by any field, narrowed to one kind |
| `audit::head` | the chain head: the pair an anchor stores |
| `audit::recorded` | the sealed-entry event source. Observe it; only this worker fires it |

`append` returns the sequence number and hash, so a caller that wants to prove it
recorded something has both to name.

## Reading it without an engine

Verifying a chain needs no worker, and the person most likely to want to is an
auditor, who is the person least likely to have an engine running. Asking the
writer whether its own writing is intact is also the wrong shape.

```bash
make check                        # is it intact, and what does it hold
make verify                       # the same, quiet. Non-zero on a broken chain
make read KIND=ladder.refused     # entries, narrowed
```

These dispatch to the same handlers the bus calls, so the two answers cannot
drift. **`append` is deliberately absent from the command line.** One writer is
the entire guarantee, and a shell that appends beside a running worker is a
second one.

## Declaring your vocabulary

The kinds you record are yours. This worker ships no list, because a fixed one
would be somebody else's event taxonomy in your log.

```bash
AUDIT_LOG_KINDS=turn.started,turn.completed,ladder.refused,published
```

Declaring it turns on one check: `verify` notes any entry whose kind is not on
the list. That catches the typo which would otherwise split your counts in two.
It is never fatal. An unknown kind is a newer writer rather than a tampered
entry, and a log that refused to verify because it mentions an event this reader
has not heard of would be a format nobody could upgrade.

Leaving it unset checks nothing, which is the honest answer rather than a
permissive one: nobody said what counts as known here, so there is nothing to
report.

## Configuration

| Variable | Default | What it is |
|---|---|---|
| `AUDIT_LOG_DIR` | `./audit` | the directory of sealed JSONL files |
| `AUDIT_LOG_KINDS` | unset | the kinds you record, comma-separated |
| `III_URL` | `ws://localhost:49134` | the engine |

Rotation seals a file at 64 MB and starts another. I picked 64 MB so a file stays
greppable and still loads into memory for verification, not for any storage
reason. The chain continues across the boundary, so a sealed file plus its
successors verify as one history.

## What this deliberately does not do

- **It does not decide anything.** No policy, no gate, no verdict. Callers decide
  and append; a record that judges is not a record.
- **It does not delete.** There is no retention policy, because a retention
  policy the audited operator can change is the thing this exists to avoid. When
  you need old entries gone, move the sealed files yourself and know the chain
  no longer verifies from the beginning.
- **It does not replicate.** One directory, one machine. Observe the log from
  another worker and ship the entries onward if you need a second copy.
- **It does not encrypt or sign.** The chain proves nothing was changed after the
  fact. It does not prove who wrote it, and a determined operator with write
  access to the whole directory can rebuild a consistent chain from scratch. If
  you need that, the next property is an external notary, and this does not have
  one.

## The failure that shaped it

The first version held a thread lock and an in-memory tail, which is correct for
one process and wrong for two. Two workers appended from separate processes, each
chained onto the tail it remembered, and the first real run produced a log that
failed its own verification with nothing having tampered with it. I could not
tell that apart from an attack, which is the worst way for an audit log to fail:
it cried wolf about itself.

One worker owns the chain now. The cross-process `flock` stayed anyway, because a
worker makes a second writer unlikely rather than impossible, and a design should
enforce the invariant it depends on rather than assume it.

## Status

Early. 40 tests cover the chain, the store, the handlers the engine calls and
the concurrent-writer case. None of them needs an engine. A fresh Pyright run
over the wiring reports no problems. The live path ran on an 0.26 engine:
registration, append over `iii call`, head, and observer delivery of the
sealed entry all verified.

## License

MIT.
