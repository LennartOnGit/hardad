# Hardad — Swedish Tutor

A conversational Swedish-language tutor powered by Anthropic's Claude. Part of a larger Docker/Kubernetes demo project. Type Swedish (or English when you need help), and the tutor responds with immersion-heavy conversation tailored to your level.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installed
- [Docker](https://www.docker.com/) installed (for Postgres)

## Setup

1. Install dependencies:

   ```bash
   uv sync
   ```

2. Copy and fill in environment variables:

   ```bash
   cp .env.example .env
   ```

   Edit `.env` — set `ANTHROPIC_API_KEY` to your key and `DEMO_ACCESS_TOKEN` to a random string:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

3. Start Postgres:

   ```bash
   docker run -d --name hardad-pg -e POSTGRES_USER=tutor -e POSTGRES_PASSWORD=dev -e POSTGRES_DB=tutor -p 5432:5432 postgres:16
   ```

4. Run the app:

   ```bash
   uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8800
   ```

5. Visit `http://localhost:8800/docker_demo/?token=<your DEMO_ACCESS_TOKEN>`

## What's next

This is Step 3 of the project. Upcoming steps add Docker packaging, Kubernetes deployment, OIDC authentication, voice input/output, and more.
