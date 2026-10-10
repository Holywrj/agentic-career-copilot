# Agentic Career Copilot Product Baseline

## Product Vision

Agentic Career Copilot is an AI Career Intelligence Platform for job seekers and ongoing career development. It is not a general-purpose chatbot.

The product should build a durable understanding of each user's:

- Career Profile
- Resume versions
- Target jobs
- Historical analyses
- Later conversations and personal knowledge

Using that context, it should help users with:

- Job Fit analysis
- Skill Gap analysis
- Resume improvement
- Interview preparation
- Career action planning
- Later agent-driven tasks

The product should help a user move from understanding their background and goals to evaluating an opportunity, identifying gaps, improving application materials, preparing for interviews, and deciding what to do next.

## Core User Journey

```text
Register / Login
→ Create Career Profile
→ Upload Resume
→ Save Job Description
→ Analyze Job Fit
→ Identify Skill Gaps
→ Recommend Career Actions
→ Continue through Agent Conversation
```

This journey is the product direction. It does not mean every step is implemented today. Capabilities should be added incrementally while preserving a coherent path toward this experience.

## Product Principles

- Every feature should advance the final product instead of existing only to demonstrate a technology.
- AI output should distinguish among evidence-supported matches, evidence-supported gaps, and unknown or insufficient information.
- A skill missing from a resume is not proof that the user lacks that skill.
- The user-confirmed Career Profile is one authoritative source of career facts.
- LLMs and resume parsers may propose changes, but they must not silently overwrite user-confirmed information.
- Historical analyses must preserve enough context to explain which inputs produced a result.
- Resume and Job versions used by an analysis must be explicit rather than silently replaced with the latest data.
- Agent, RAG, and MCP capabilities should be introduced when they support real user workflows, not as speculative empty modules.
- Private career data must remain scoped to its owner throughout storage, analysis, retrieval, and execution.

## First Product Milestone

The first product milestone should allow a user to:

1. Log in securely to a personal workspace.
2. Create and maintain a Career Profile.
3. Store multiple Resume versions.
4. Save target Jobs and their job descriptions.
5. Generate a JobAnalysis from an explicitly selected Career Profile state, Resume, and Job.
6. Revisit historical analyses and understand the inputs used at the time.

Persistent Conversation, LangGraph Agent workflows, personal knowledge retrieval through RAG, and MCP integrations are later capabilities. They should build on the ownership, versioning, and traceability established in this milestone.
