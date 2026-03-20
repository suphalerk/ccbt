# Team Orchestrator

Coordinate the agent team to execute complex tasks. The PM receives a task from the user, breaks it down, and delegates to specialists who work in parallel.

## When to Use

When the user gives a complex task that requires multiple specialists:
- "ทำ feature X" → PM plans → SA designs → backend-dev implements → tests
- "วิจัย strategy ใหม่" → PM scopes → trader-expert researches → backend-dev implements
- "review code" → SA + backend-dev + trader-expert review in parallel
- "deploy to production" → SA plans → devops executes → backend-dev validates

## Team Structure

```
User → PM (team lead, sonnet)
         ├→ SA (architect, opus) — designs, reviews architecture
         ├→ Trader Expert (opus) — strategy, risk, market analysis
         ├→ Crypto Expert (opus) — domain knowledge, market context
         ├→ Backend Dev (sonnet) — implements features, fixes bugs
         ├→ Frontend Dev (sonnet) — dashboard, UI
         └→ DevOps (sonnet) — deploy, infra, monitoring
```

## How to Orchestrate

### Step 1: PM receives task
Spawn PM agent to analyze the task and create a plan:
```
Agent(subagent_type="pm", prompt="User wants X. Break into subtasks, assign to team.")
```

### Step 2: PM delegates in parallel
Based on PM's plan, spawn specialists simultaneously:
```
Agent(subagent_type="sa", prompt="...", run_in_background=True)
Agent(subagent_type="backend-dev", prompt="...", run_in_background=True)
Agent(subagent_type="trader-expert", prompt="...", run_in_background=True)
```

### Step 3: Multi-instance when needed
For heavy tasks, spawn multiple instances of the same specialist:
```
Agent(subagent_type="backend-dev", name="backend-1", prompt="implement module A")
Agent(subagent_type="backend-dev", name="backend-2", prompt="implement module B")
```

### Step 4: PM reviews results
Spawn PM again to review all results and report to user.

## Orchestration Patterns

### Pattern A: Research (parallel analysis)
```
PM → [trader-expert, crypto-expert, sa] in parallel → PM synthesizes
```

### Pattern B: Build (sequential with parallel steps)
```
PM → SA (design) → [backend-dev × 2, frontend-dev] in parallel → SA (review) → PM (report)
```

### Pattern C: Review (all reviewers parallel)
```
PM → [sa, backend-dev, trader-expert] all review same code → PM (compile findings)
```

### Pattern D: Deploy (sequential)
```
PM → backend-dev (tests) → sa (architecture check) → devops (deploy) → PM (verify)
```

## Rules

- PM ALWAYS receives the task first and creates the plan
- Spawn specialists in PARALLEL when they don't depend on each other
- Use `run_in_background=True` for parallel work
- Use `name` parameter to spawn multiple instances (e.g., "backend-1", "backend-2")
- PM reviews ALL results before reporting to user
- Each specialist only does work within their expertise
- If a specialist needs info from another, PM coordinates (not direct agent-to-agent)

## Invocation

User says `/team` or gives a complex multi-step task:
- `/team "implement Ichimoku strategy for gold"` → full team orchestration
- Any task that needs 2+ specialists → auto-invoke team pattern
