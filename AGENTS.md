# Project Guidance

This is a public, production-oriented educational portfolio project for building a full-stack AI career copilot. It will progressively cover Python, FastAPI, React, TypeScript, LangChain, LangGraph, tool calling, MCP, RAG, hybrid retrieval, reranking, Reciprocal Rank Fusion (RRF), streaming, retry and resilience patterns, evaluation, and observability.

## Working Principles

- Prefer production-oriented architecture, simple explicit implementations, and avoid overengineering early phases.
- Inspect existing code and documentation before making architectural assumptions; keep changes scoped to the requested task.
- Add dependencies only when the task justifies them.
- Never commit secrets, API keys, tokens, credentials, `.env` files, or other sensitive configuration. Use `.env.example` when introducing environment configuration.
- Add or update tests when behavior changes, and run relevant validation commands when they exist. Never bypass failing tests, linting, typing, or CI merely to make a change pass.
- Do not implement features directly on `main` or force-push shared branches.
- Use Conventional Commit-style messages and prefer small, reviewable pull requests.
- Update documentation when architecture, setup, public APIs, or developer workflows change. Document non-obvious architectural decisions and their reasoning.
- More specific `AGENTS.md` files may be added in subdirectories as those areas are introduced.
