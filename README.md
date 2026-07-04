# imperal-ext-automations

Event-driven automation rules — subscribe to platform events (email arrivals, schedule ticks, custom signals) and run multi-step actions across other extensions when conditions match.

Imperal-owned extension for [Webbee 🐝](https://docs.imperal.io), the agent of [Imperal Cloud](https://imperal.io) — the world's first AI Cloud OS.

| Field | Value |
|---|---|
| **App ID** | `automations` |
| **Current version** | v1.9.1 |
| **Status** | Production |
| **License** | Proprietary (Imperal, Inc.) |
| **SDK** | `imperal-sdk >= 4.1.4` |

## UI model

The app is intentionally split into two coordinated surfaces:

- **Left sidebar — AI Agents**
  - browse all existing rules
  - quick status control: pause / resume
  - quick notification mode switch: all / failures / off
  - open the safe editor for a specific rule
  - delete a rule
  - auto-refresh on create / pause / resume / delete / update events
- **Center Workshop**
  - create a new rule
  - edit an existing rule **in place** with `update_automation`
  - review execution outcomes and failure patterns
  - stay in sync with sidebar actions through the same rule lifecycle events

This design reuses the extension's existing rule/update primitives instead of inventing a parallel UI-only mutation path, which keeps behavior stable and reduces regression risk.

## Deploy flow

This git repo is the **source of truth**. The deployed copy on `whm-ai-worker:/opt/extensions/automations/` is downstream of Dev Portal uploads — do not edit the deployed copy directly.

1. Edit code locally in this folder.
2. Commit + push to `main`.
3. Open <https://panel.imperal.io/developer> and upload a tarball of the current commit.
4. Dev Portal validates against the federal extension contract (V14–V22 + V24) and rolls out to production workers.

## Federal contract

Must satisfy V14–V22 + V24 to publish via Dev Portal. See <https://docs.imperal.io/en/sdk/validators-reference/>.
