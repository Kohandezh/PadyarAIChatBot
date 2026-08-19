# Legal — Padyar AI Legal Documents & Compliance

This directory tracks all legal documents, compliance requirements, and regulatory obligations for the Padyar AI platform.

## Why This Matters

Padyar AI processes user data, handles payments, routes prompts through third-party AI models (OpenAI, Anthropic, Google, Meta), stores conversation history, and uses vector embeddings. Each of these activities triggers specific legal obligations. Missing or incomplete legal documents can result in:

- Regulatory fines (GDPR: up to 4% of global revenue, CCPA: up to $7,500 per violation)
- App store / marketplace rejection
- Loss of user trust
- Inability to sign B2B contracts

---

## Required Legal Documents

### 1. Terms of Service (ToS)

**Status:** Not created

The master agreement between Padyar AI and its users. Covers:

- Account registration and eligibility (age requirements, account termination)
- Description of services (12+ AI applications, multi-model routing, credit system)
- User responsibilities and acceptable behavior
- Intellectual property rights (who owns AI-generated outputs, user prompts)
- Payment terms, credits, refund policy, pricing changes
- Service availability disclaimer (no uptime guarantee unless SLA applies)
- Limitation of liability and indemnification
- Dispute resolution (governing law, jurisdiction, arbitration vs. court)
- Modifications to terms (how users are notified of changes)

**Must address for AI specifically:**
- AI output accuracy disclaimer ("outputs may be inaccurate, not professional advice")
- No guarantee of output ownership or copyrightability
- User responsibility to review AI outputs before use
- Prohibited uses (generating illegal content, impersonation, automated disinformation)

---

### 2. Privacy Policy

**Status:** Not created

Required by GDPR, CCPA/CPRA, and most privacy laws worldwide. Must include:

- **Categories of data collected:** name, email, billing info, IP address, device info, conversation history, uploaded files (PDFs, images), usage analytics
- **How data is collected:** registration forms, payment processing, cookies/analytics, AI model interactions, file uploads
- **Purpose of processing:** providing services, processing payments, improving AI responses, fraud prevention, communication
- **Legal basis for processing:** consent, contractual necessity, legitimate interests (GDPR requirement)
- **Third-party data sharing:** AI providers (OpenAI, Anthropic, Google, Meta), payment processor (Stripe), hosting (Vercel/Supabase), analytics, Redis/R2 storage
- **Data retention periods:** how long each data category is kept, deletion criteria
- **User rights:** access, correction, deletion, portability, objection, restriction of processing
- **Security measures:** encryption, access controls, RLS (Row Level Security)
- **Contact information:** privacy email, Data Protection Officer (if applicable)
- **International data transfers:** how data moves between EU and non-EU countries (Supabase, Vercel, AI providers)
- **Embeddings and vector data:** how conversation embeddings are stored and can be deleted

**Applicable regulations:** GDPR (EU users), CCPA/CPRA (California users), PIPA (Iran — if serving Iranian users), UK GDPR

---

### 3. Cookie Policy

**Status:** Not created

Discloses all cookies and tracking technologies used on the platform:

- Essential cookies (session, authentication, CSRF protection)
- Analytics cookies (if using Google Analytics, PostHog, etc.)
- Preference cookies (theme, language selection — Persian/English)
- Third-party cookies (AI provider scripts, payment widgets)
- Cookie duration and purpose for each category

**Requires:** Cookie consent banner (GDPR requires opt-in consent before non-essential cookies load)

---

### 4. Acceptable Use Policy (AUP)

**Status:** Not created

Defines what users cannot do on the platform:

- Generate illegal content (CSAM, terrorism, fraud instructions)
- Impersonate real individuals
- Create deepfakes or misleading media
- Automated scraping or abuse of rate limits
- Reverse-engineering the platform
- Sharing accounts or reselling credits
- Using AI outputs to harm, harass, or defame others
- Violating third-party IP rights with AI-generated content

This is separate from ToS because it's often referenced by AI providers' own usage policies — we must enforce their rules too.

---

### 5. Refund and Credit Policy

**Status:** Not created

Specific to Padyar AI's credit-based payment model:

- Credit pricing and packages
- Credit expiration rules (do credits expire?)
- Refund eligibility (unused credits, service outages, billing errors)
- No-refund situations (used credits, user-caused issues)
- How to request a refund
- Processing timeline for refunds
- Free tier limitations (which models are free: gpt-4o-mini, claude-3-5-haiku, llama-4-scout, gemini-2.0-flash)

---

### 6. Data Processing Agreement (DPA)

**Status:** Not created

Required for B2B customers and GDPR compliance. Covers:

- Data controller vs. data processor roles
- Types of personal data processed
- Processing instructions and restrictions
- Sub-processor list (AI providers, hosting, analytics)
- Data breach notification timeline (GDPR: 72 hours)
- Data deletion and return upon contract termination
- Audit rights
- International transfer safeguards (Standard Contractual Clauses)

**When needed:** Any enterprise/B2B customer asks for it, or when processing data on behalf of another business.

---

### 7. Service Level Agreement (SLA)

**Status:** Not created

Optional but required for paid/B2B tiers:

- Uptime commitment (e.g., 99.9%)
- What counts as downtime (planned maintenance exclusions)
- Response time for support tickets
- Compensation for SLA breaches (credit refunds)
- Exclusions (force majeure, third-party AI provider outages)

---

### 8. End-User License Agreement (EULA)

**Status:** Not created

Needed if distributing any downloadable software, mobile apps, or desktop clients:

- License grant and scope
- Restrictions on modification/redistribution
- Open-source component disclosures
- Warranty disclaimers
- Termination conditions

**When needed:** Before launching any mobile app or downloadable client.

---

### 9. AI-Specific Disclosures

**Status:** Not created

Required under the EU AI Act and emerging AI regulations:

- Transparency notice: "This service uses artificial intelligence"
- AI model disclosure: which models power which features
- Output labeling: AI-generated content is clearly marked
- Bias and fairness statement
- Human oversight description (how content moderation works)
- Right to human review of AI decisions
- Log retention for AI interactions

**EU AI Act classification:** Padyar AI likely falls under "General Purpose AI (GPAI)" — must comply with transparency obligations by August 2025.

---

### 10. Copyright and IP Policy

**Status:** Not created

Addresses ownership of AI-generated content:

- Who owns AI-generated outputs (likely the user, with platform license)
- Platform's license to use prompts for improvement (opt-in vs. opt-out)
- DMCA takedown procedure
- Handling of copyrighted material in prompts/uploads
- Indemnification for IP claims

---

## Regulatory Compliance Map

| Regulation | Jurisdiction | Applies If | Key Requirements | Deadline |
|-----------|-------------|-----------|-----------------|----------|
| GDPR | EU/EEA | Any EU users | Privacy Policy, DPA, cookie consent, data rights, 72h breach notification | Active now |
| CCPA/CPRA | California, US | Any CA users | Privacy Policy, opt-out of data sale, right to delete | Active now |
| EU AI Act | EU | GPAI providers | Transparency, risk assessment, technical documentation | Aug 2025 (transparency), Aug 2026 (full) |
| UK GDPR | UK | Any UK users | Same as GDPR, separate ICO jurisdiction | Active now |
| COPPA | US | Users under 13 | Parental consent, age gating | If targeting minors |
| PIPA | Iran | Iranian users | Data localization, consent requirements | If serving Iranian users |
| SOPIPA | US (varies) | Students/education | Student data protection | If education features |

---

## Priority Timeline

### Before Public Launch (Blockers)

These must exist before any user can sign up:

1. **Terms of Service** — no account creation without it
2. **Privacy Policy** — legally required before collecting any personal data
3. **Cookie Policy + Consent Banner** — GDPR/CCPA requirement before any cookies load
4. **Acceptable Use Policy** — required by AI provider agreements

### Before Monetization (Pre-Payment)

5. **Refund and Credit Policy** — required before charging users
6. **Copyright and IP Policy** — needed when users generate and save AI content

### Before B2B / Enterprise

7. **Data Processing Agreement (DPA)** — enterprise customers will ask for it
8. **Service Level Agreement (SLA)** — paid tier commitment

### Before Mobile App / Desktop Client

9. **EULA** — required by app stores (Apple, Google Play)

### Ongoing Compliance

10. **AI-Specific Disclosures** — EU AI Act transparency obligations

---

## Directory Structure

```
legal/
  README.md              # This file — overview and tracking
  templates/             # Draft templates for each document
  policies/              # Final, published versions
  compliance/            # Audit trails, DPIA records, compliance checklists
  archive/               # Previous versions with effective dates
```

---

## Implementation Notes

### Legal Document Hosting

- All policies should be accessible at `/legal/{policy-slug}` (e.g., `/legal/terms`, `/legal/privacy`)
- Include "Last Updated" date on every document
- Include "Effective Date" on every document
- Store version history — keep previous versions accessible
- Users must accept ToS at signup (checkbox + timestamp in database)
- Notify users of material changes via email + in-app banner

### Consent Management

- Cookie consent banner on first visit (GDPR opt-in)
- ToS acceptance at account creation
- Separate consent for marketing emails
- Consent records stored with timestamp in Supabase

### AI Provider Compliance

Padyar routes prompts through multiple AI providers. Each has usage policies we must enforce:

- **OpenAI:** Prohibits CSAM, illegal content, impersonation. Requires disclosure of AI use.
- **Anthropic:** Prohibits harmful content, requires output labeling in some contexts.
- **Google (Gemini):** Prohibits generation of harmful or misleading content.
- **Meta (Llama):** Acceptable use policy prohibits misuse for harm.

Our Acceptable Use Policy must be at least as restrictive as all providers combined.

### Supabase-Specific Considerations

- RLS (Row Level Security) must be documented as a security measure
- pgvector embeddings qualify as personal data if tied to user conversations
- Data residency: know which Supabase region stores data (affects GDPR compliance)
- Backup and deletion procedures must be documented

---

## Maintenance Checklist

- [ ] Review all policies quarterly
- [ ] Update Privacy Policy whenever new third-party tools are added
- [ ] Update ToS when pricing or features change
- [ ] Re-evaluate AI Act compliance as new guidance is published
- [ ] Run annual DPIA (Data Protection Impact Assessment) for AI processing
- [ ] Verify AI provider policies haven't changed
- [ ] Audit cookie list against Cookie Policy
- [ ] Test user rights workflows (data access, deletion requests) quarterly

---

## Legal Counsel

This directory documents requirements and tracks status. It does not replace legal advice.

**Before launch:** Have a qualified attorney review all documents, especially:
- Terms of Service
- Privacy Policy
- Any documents governing user data or payments

**Jurisdictions to cover:** Iran (primary), EU (GDPR), US (CCPA), UK (UK GDPR)
