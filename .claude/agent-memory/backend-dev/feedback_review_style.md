---
name: Code review output style
description: User asked for thorough code review with file/line/severity/financial-loss for each finding
type: feedback
---

User asked for code review in a specific format: file + line number, description, severity (critical/high/medium/low), suggested fix, and whether it could cause financial loss. Ended with a summary table and "top 3 to fix immediately".

**Why:** This is a financial trading system so thoroughness and prioritization by financial risk is valued over brevity.
**How to apply:** When asked to review code, use this format. Always call out financial loss risk explicitly for each issue. End with a summary table and prioritized top-N.
