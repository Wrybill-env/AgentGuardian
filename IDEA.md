# Strixora

## The Idea

Strixora is an open-source watchdog and recovery system for AI coding agents.

AI coding agents like Goose, OpenCode, Hermes, and others can randomly stop working because of temporary failures such as:

- API rate limits (429)
- provider overload
- 408 timeouts
- 5xx server errors
- connection resets
- DNS/network failures
- dropped streaming connections
- streams that silently stop producing tokens
- agent/backend crashes
- temporary provider outages

Right now, many agents treat these failures as a reason to stop the task.

Strixora's goal is to prevent temporary failures from killing long-running agent tasks.

Instead of:

Agent → error → task dies → user has to intervene

We want:

Agent → temporary failure → Guardian detects it → waits/backoffs → safely recovers → agent continues


## Core Concept

Strixora runs outside the AI coding agent as a supervisor/watchdog.

It monitors the agent's process, session, protocol, and streaming activity.

It should distinguish between three major categories of failures.

### Temporary / Recoverable Failures

Examples:

- 429 rate limit
- 408 timeout
- 500/502/503/504
- provider temporarily overloaded
- connection reset
- temporary network outage
- stream stopped producing chunks
- backend temporarily disconnected

For these, Guardian can wait, use exponential backoff with jitter, and attempt recovery when recovery is known to be safe.

### Permanent Failures

Examples:

- invalid API key
- invalid model
- permission denied
- invalid configuration
- unsupported operation
- user cancellation

These should not be retried forever.

### Dangerous / Ambiguous Failures

These are extremely important.

If the agent may have already:

- produced assistant output
- executed a tool
- modified files
- run shell commands
- made an external API request
- triggered an MCP tool
- performed another side effect

Guardian must assume that blindly replaying the request could duplicate actions.

When recovery cannot be proven safe, Guardian must fail closed.


## Stream Watchdog

One of the main features is detecting a stream that has silently stalled.

Example:

Agent sends request
→ model starts streaming
→ chunks arrive
→ no chunks for 60 seconds
→ Guardian detects possible stale stream
→ Guardian investigates / waits / reconnects if safe

The watchdog must distinguish a genuinely slow model from a dead stream.

An idle session that is not currently generating must NOT be considered a stale stream.

Example configuration:

stale_after = 60 seconds
hard_timeout = 240 seconds

These values should be configurable.


## Safety Principle

The most important rule of Strixora:

NEVER blindly replay an agent action when doing so could duplicate side effects.

Guardian should be conservative.

If it knows recovery is safe:

Failure
→ Classify
→ Safety check
→ Recover

If it does not know:

Failure
→ Uncertain
→ Stop
→ Tell the user

False negatives are preferable to accidentally executing a tool twice.


## Architecture

Strixora should have an agent-agnostic core with adapters for individual agents.

Strixora
├── Core Engine
│   ├── Failure Detection
│   ├── State Machine
│   ├── Backoff / Retry
│   ├── Stream Watchdog
│   ├── Safety Gates
│   └── Recovery Engine
│
└── Agent Adapters
    ├── Goose
    ├── Hermes
    └── OpenCode

The core must not depend on a specific agent.

Each adapter is responsible for understanding that agent's process, protocol, session, and recovery capabilities.


## Desired Features

### 1. Failure Detection

Detect transient and permanent failures across supported agents.

### 2. Stream Watchdog

Detect streams that stop producing output.

The watchdog should support configurable thresholds and should never treat an idle session as a stale stream.

### 3. Exponential Backoff

Use exponential backoff with jitter.

Example:

1s
2s
4s
8s
16s
...

Support:

- jitter
- maximum delay
- Retry-After when available
- potentially indefinite retries for genuinely transient failures

### 4. Recovery

Only perform recovery operations that are known to be safe for the specific agent and protocol.

### 5. Safety Gates

Before replaying or recovering, determine:

- Was a tool called?
- Was assistant output already produced?
- Did the agent execute anything?
- Could an external side effect have happened?
- Is the session state known?
- Is the recovery operation idempotent?

If uncertain:

DO NOT recover automatically.

### 6. Process / Backend Monitoring

Detect:

- agent process crashes
- backend crashes
- unexpected disconnects
- dead sessions

### 7. Network Monitoring

Recognize temporary network failures and wait for connectivity to return.

### 8. Session Awareness

Track:

- session ID
- run/turn ID
- active generation
- tool activity
- assistant output
- completion
- cancellation
- failure state

This information is necessary to prevent unsafe replay.

### 9. Logging / Diagnostics

Provide useful logs showing what Guardian is observing and why it made a decision.

Example:

[WATCH] active turn detected
[WATCH] no stream chunk for 60s
[DETECT] possible stale stream
[CLASSIFY] transient provider failure
[SAFETY] recovery considered safe
[RECOVER] waiting 8s
[RECOVER] reconnecting
[RESULT] stream resumed

Never expose API keys, tokens, or other secrets.

### 10. Multiple Agents

Initial adapters:

- Goose Desktop
- Hermes
- OpenCode

More agents can be added later without changing the core.


## Safety Modes

### OBSERVE_ONLY

Default mode.

Guardian watches and reports what it would do but performs no recovery actions.

### DRY_RUN

Guardian simulates recovery decisions without actually performing them.

### AUTO_RECOVER

Explicit opt-in.

Guardian performs only recovery operations that have been proven safe for the specific agent and failure scenario.


## Important Design Constraint

Strixora should NOT modify the source code of the agents it monitors.

It should operate externally wherever possible.

This makes it:

- safer
- easier to maintain
- usable across agent versions
- easier to open-source
- easier to support multiple agents


## Long-Term Goal

The long-term goal is for Strixora to become a general-purpose reliability layer for AI coding agents.

Instead of every agent independently implementing:

- retry logic
- stream watchdogs
- network recovery
- provider failure handling
- crash recovery
- session recovery

Strixora can provide that reliability externally.

The user should be able to start a long-running coding task without having to babysit the agent every time a provider temporarily dies.


## One Sentence

Strixora is a universal watchdog for AI coding agents that detects transient failures, waits and recovers when safe, and refuses to blindly replay actions when recovery could cause side effects.
