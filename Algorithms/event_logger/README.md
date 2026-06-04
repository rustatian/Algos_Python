# Event Logger — batching, fsync, group commit

Append durable log records fast. The bottleneck is `fsync` — forcing the
OS to flush to stable storage costs milliseconds — so one `fsync` per event
caps throughput at the device's IOPS. The fix is **group commit**: many
concurrent appenders share ONE `fsync`. This is exactly how Postgres and
MySQL/InnoDB commit their write-ahead logs.

Modeled on the classic "durable event logger" question. Related LeetCode
references: #635 (Design Log Storage System), #1352 (Product of the Last K
Numbers — append-only), #355 (Design Twitter — timeline append).

## Problem

`append(event)` must persist an event durably. The naive implementation —
`write` then `fsync` per event — is correct but slow: if a flush is ~10 ms,
that is ~100 events/sec regardless of CPU. Batching many events into one
`fsync` lifts throughput to 10K+/sec, but a buffer that is not yet flushed
is **not durable** — a crash loses it. The art is getting *both* throughput
and durability.

The durable destination is modeled as a tiny `Sink` (`write` stages bytes
in the page cache; `fsync` is the durability barrier), so tests use an
in-memory sink that counts flushes and reports exactly what would survive a
crash.

```python
sink = InMemorySink()
log = BatchedLogger(sink, batch_size=3)
log.append("a"); log.append("b"); log.append("c")  # batch full -> flush
sink.fsync_count        # -> 1   (three events, one fsync)
```

## Tiers

| Tier | Class | Strategy | The lesson |
|------|-------|----------|------------|
| 1 | `SimpleLogger` | `fsync` per event | the baseline — durable but throughput-bound by the flush rate |
| 2 | `BatchedLogger` | buffer + one `fsync` per batch | throughput via amortization — but a crash loses the unflushed buffer |
| 3 | `GroupCommitLogger` | concurrent appenders block until a shared `fsync` | durable **and** fast — the real WAL commit path |
| 4 | `DistributedLog` | replicated WAL | the system-design follow-up — Kafka/Raft, quorum durability |

Each tier answers the previous one's weak spot. Tier 1 fsyncs every event,
so N events cost N flushes — the disk barrier, not the CPU, is the ceiling.
Tier 2 buffers and flushes a whole batch with one fsync (N/batch_size
flushes), but events sitting in the buffer are not durable, so a crash
loses them — it traded durability for throughput. Tier 3 recovers both:
concurrent appenders each enqueue and **block until durable**, while a
single background flusher coalesces whatever accumulated into one fsync and
then wakes exactly the appenders whose events landed. A monotonic sequence
number plus a single `durable_seq` watermark (an LSN, in WAL terms) lets
each appender tell when *its* event is on stable storage. Tier 4 replicates
the log across machines.

### Why a brief "batch window" is the trick in Tier 3

If the flusher fsynced the first event the instant it arrived, it would
degenerate back to Tier 1 — one flush per event under low concurrency. By
lingering a few milliseconds before flushing, a burst of appenders joins
one group, so a single fsync covers them all. It trades a little latency
for a large throughput gain — the defining move of group commit. The fsync
rate is then bounded by ~1/window regardless of how fast events arrive.

### Why the sequence-number watermark

A flush happening is not enough for an appender — it must know *its own*
event is durable. Assigning each event a monotonic `seq` and advancing one
`durable_seq` after each fsync lets every waiter check `durable_seq > my_seq`.
That single integer is the in-process analogue of a WAL's log sequence
number; replication (Tier 4) extends it to "durable on a quorum."

## Tier 4 — the system-design follow-up (distributed, replicated log)

The follow-up: *a durable, ordered, highly-available log across machines —
the backbone of replication, event sourcing, and stream processing
(Kafka, a Raft log, Bookkeeper).*

**Opener questions.** Durability bar — fsync on one node, or a quorum of
replicas? Ordering — total order, or per-partition order? Throughput vs
latency target? Retention (forever, or time/size-bounded)? Single writer
per partition, or many?

**Design sketch.**

```
   producers ─► partition leader (appends to local WAL, group-commits)
                      │  replicate batch
                      ▼
            follower replicas (apply to their WAL)  ──ack──► leader
                      │  acked by a quorum -> "committed" watermark advances
                      ▼
                consumers read up to the committed offset
```

- **Partitioned for throughput.** The log is split into partitions, each an
  independent ordered sequence with one leader; total throughput scales
  with partition count. Order is guaranteed *within* a partition (the
  single-writer leader assigns offsets) — the same role `seq` plays here.
- **Group commit, lifted to replication.** The leader still batches local
  appends with one fsync (Tier 3), and *also* ships each batch to followers;
  an offset is "committed" once a quorum has it durably. The `durable_seq`
  watermark becomes the **committed offset** — consumers may read only up to
  it, exactly as Tier 3's appenders wait for `durable_seq`.
- **Retention & replay.** The log is the source of truth; consumers track
  their own offset and can replay. Old segments are truncated by time/size
  or compacted by key.

**Failures.** Leader crash → a follower with the highest committed offset is
elected (Raft/ISR); uncommitted tail entries are dropped. Slow follower →
removed from the in-sync set so it cannot stall commits. Producer retry
after an ambiguous ack → an idempotent producer id + sequence dedupes, so a
retried append is not double-logged.

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| `Sink.fsync` | a flush to disk on each replica |
| `GroupCommitLogger` batch | the leader's local group commit + replication batch |
| `durable_seq` watermark | the committed offset (durable on a quorum) |
| `append` blocking until durable | a producer awaiting `acks=all` |
| single process | partitioned, leader-replicated log (Kafka / Raft) |

## Running the tests

```sh
uv run pytest Algorithms/event_logger/tests/ -q
```
