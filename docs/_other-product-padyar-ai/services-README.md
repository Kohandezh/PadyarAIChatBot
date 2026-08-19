# Services Index

Third-party services used by Padyar AI.

## Infrastructure

| Service | Purpose | Config |
|---------|---------|--------|
| Supabase | Auth, database, pgvector | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` |
| Redis | Query caching | `REDIS_URL`, `REDIS_PASSWORD` |
| Cloudflare R2 | File storage (S3-compatible) | `STORAGE_ENDPOINT`, `STORAGE_BUCKET` |
| Vercel | Deployment, OG images | Auto-configured |

## AI Providers

| Provider | Models | Config |
|----------|--------|--------|
| OpenAI | GPT-5, GPT-4o, DALL-E, Whisper, Embeddings | `OPENAI_API_KEY` |
| Anthropic | Claude 4 Sonnet, Claude Opus 4 | `ANTHROPIC_API_KEY` |
| Google | Gemini 2.5 Pro/Flash | `GOOGLE_GENERATIVE_AI_API_KEY` |
| xAI | Grok 3, Grok 4 | `XAI_API_KEY` |
| Groq | Llama 4 Scout/Maverick | `GROQ_API_KEY` |
| DeepSeek | DeepSeek Chat | `DEEPSEEK_API_KEY` |
| Replicate | SDXL, Flux, image models | `REPLICATE_API_TOKEN` |
| ElevenLabs | Text-to-speech | `ELEVENLABS_API_TOKEN` |

## Payments

| Service | Purpose | Config |
|---------|---------|--------|
| Stripe | Payment processing | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` |
| LemonSqueezy | Payment processing | `LEMON_SQUEEZY_WEBHOOK_SECRET` |

## Communication

| Service | Purpose | Config |
|---------|---------|--------|
| Loops | Transactional email | `LOOPS_API_KEY` |
| Asanak | SMS gateway (`sms.asanak.ir` v2rest) | `ASANAK_USERNAME`, `ASANAK_PASSWORD` (stored as `enc:…`), `ASANAK_SOURCE`, `ASANAK_URL`, `ASANAK_STATUS_URL`, `ASANAK_CREDIT_URL`, `ASANAK_TRIM`, `ASANAK_SEND_TO_BLACKLIST` — written by the admin panel via `app/services/secure_store.py` |
| PostHog | Analytics | `NEXT_PUBLIC_POSTHOG_KEY` |

## MCP Tools

| Tool | Purpose | Config |
|------|---------|--------|
| filesystem | File read/write | Auto-configured |
| github | Repo, PRs, issues | `GITHUB_TOKEN` |
| tinyfish | Web crawling | Auto-configured |
| n8n | Workflow automation | `N8N_BASE_URL`, `N8N_API_KEY` |
