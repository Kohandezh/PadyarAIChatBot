# مالکیت فنی و وضعیت بازبینی

آخرین به‌روزرسانی: 2026-09-30، از روی کد commit پایهٔ `3a4a415`.

صداقت: ستون «بازبینی انسانی» وضعیت واقعی است. بازبینی‌ای که انجام نشده،
انجام‌شده اعلام نمی‌شود. امروز هیچ ردیفی بازبینی انسانی نشده است و همهٔ
ردیف‌ها `pending` هستند.

**ستون مالک:** فقط Sina (مالک محصول) این ستون را پر می‌کند. هیچ عامل AI نامی
در آن نمی‌نویسد. تا وقتی پر نشده، مالک همهٔ ردیف‌ها مالک محصول است.

**چطور وضعیت عوض می‌شود:** وقتی یک انسان یک زیرسیستم را طبق
`docs/engineering/REVIEW_PLAN.md` بازبینی کرد و نتیجه را در یک کامنت PR ثبت
کرد، همان انسان `pending` را با تاریخ و لینک آن کامنت عوض می‌کند. روش کامل:
`docs/engineering/REVIEW_PROCESS.md`.

**نگهداری:** `tests/test_code_ownership_doc.py` شکست می‌خورد اگر یک ماژول
رجیستری (`app/modules/registry.py`) در این جدول ردیف نداشته باشد، یا اگر یک
مسیر داخل backtick در این فایل وجود نداشته باشد. آن تست ستون مالک و ستون
بازبینی را بررسی نمی‌کند.

## ماژول‌های رجیستری

ترتیب همان ترتیب `MODULES` در `app/modules/registry.py` است. شش ماژول اول
هسته (core) هستند و همیشه روشن‌اند. بقیه اختیاری‌اند (`ENABLED_MODULES`).

| زیرسیستم | فایل‌های کلیدی | سند طراحی | تست‌ها | مالک | بازبینی انسانی |
|---|---|---|---|---|---|
| `chat` (هسته) | `app/routers/chat.py`، `app/services/answer.py`، `app/services/scope.py`، `app/services/conversational.py`، `app/services/suggestions.py` | `docs/features/grounded-selection/RESEARCH.md`، ADR-003/004/005/018 | `tests/test_grounded_selection.py`، `tests/test_answer_firewall.py`، `tests/test_chat_options_pick.py`، `tests/test_conversational.py` | (تعیین توسط Sina) | pending |
| `admin` (هسته) | `app/routers/admin.py`، `app/routers/public.py`، `templates/admin/`، `static/admin/js/` | `docs/engineering/SECURITY_MODEL.md`، ADR-014 | `tests/test_admin_lockout.py`، `tests/test_login_timing_equalization.py`، `tests/test_session_lifetime.py`، `tests/test_admin_navigation.py` | (تعیین توسط Sina) | pending |
| `search` (هسته) | `app/routers/synonyms.py`، `app/services/search.py`، `app/services/bm25.py`، `app/services/embeddings.py`، `app/services/rerank.py`، `app/services/intent.py`، `app/utils/normalizer.py`، `app/services/synonym_suggest.py` | `docs/engineering/ARCHITECTURE.md`، ADR-013 | `tests/test_embedding_search.py`، `tests/test_intent_routing.py`، `tests/test_rerank_tiebreak.py`، `tests/test_synonym_suggest.py` | (تعیین توسط Sina) | pending |
| `dataset` (هسته) | `app/routers/dataset.py`، `app/default_content.py`، `app/services/question_assist.py` | `docs/features/chat-training/RESEARCH.md`، ADR-006 | `tests/test_dataset_sync.py`، `tests/test_dataset_ordering.py`، `tests/test_default_seed.py`، `tests/test_question_assist.py` | (تعیین توسط Sina) | pending |
| `theme` (هسته) | `app/routers/themes.py`، `app/services/themes.py`، `app/services/branding.py`، `themes/`، `static/chat/core.js`، `static/chat/base.css` | `docs/features/brand-dynamic-shell/SPEC.md`، ADR-001/002/019 | `tests/test_public_ui.py`، `tests/test_branding.py`، `tests/test_chat_markdown_xss.py` | (تعیین توسط Sina) | pending |
| `conversations` (هسته) | `app/routers/conversations_admin.py`، `app/services/conversations.py` | ADR-020 | `tests/test_conversations_admin.py`، `tests/test_conversations_store.py`، `tests/test_kiosk_privacy.py` | (تعیین توسط Sina) | pending |
| `voice` | `app/routers/voice.py`، `app/services/ai/stt.py` | ندارد | `tests/test_security_hardening.py` (بخشی) | (تعیین توسط Sina) | pending |
| `video` | `app/routers/dataset.py` (endpointهای ویدیو)، `app/services/idle_video.py` | ندارد | `tests/test_idle_video.py`، `tests/test_booth_videos.py` | (تعیین توسط Sina) | pending |
| `infra` | `app/routers/dbadmin.py`، `app/services/dbadmin.py`، `app/services/pg_admin.py`، `app/services/storage.py` | `docs/engineering/DATABASE.md` | `tests/test_dbadmin.py`، `tests/test_storage.py` | (تعیین توسط Sina) | pending |
| `backups` | `app/routers/backups.py`، `app/services/backup_center.py`، `app/services/backup.py`، `app/services/pg_backup.py`، `app/services/backup_offsite.py`، `backup_db.py` | `docs/engineering/DEPLOYMENT_RUNBOOK.md` | `tests/test_backup_center.py`، `tests/test_backup_offsite.py`، `tests/test_backup_scheduler_dispatch.py` | (تعیین توسط Sina) | pending |
| `ops` | `app/routers/ops.py`، `app/services/service_control.py`، `app/services/health.py`، `app/services/resources.py`، `app/services/maintenance.py` | `docs/features/critical-watchdog/SPEC.md` | `tests/test_ops_control.py`، `tests/test_ops_resources.py`، `tests/test_health_surface.py`، `tests/test_maintenance_ui.py` | (تعیین توسط Sina) | pending |
| `logs` | `app/routers/logs.py`، `app/services/applog.py` | `docs/engineering/MONITORING.md` | `tests/test_applog.py` | (تعیین توسط Sina) | pending |
| `tts` | `app/routers/tts.py`، `app/services/tts_lexicon.py`، `deploy/tts/` | `docs/features/text-to-speech/RESEARCH.md`، ADR-008 تا ADR-012 | `tests/test_tts_admin.py`، `tests/test_tts_server.py` | (تعیین توسط Sina) | pending |
| `registration` | `app/routers/otp.py`، `app/services/otp.py`، `app/services/signup.py`، `app/services/sms.py`، `app/services/taxonomy.py`، `app/services/visit_plan.py` | `docs/features/otp-verification/RESEARCH.md` | `tests/test_otp.py`، `tests/test_signup_flow.py`، `tests/test_visit_plan.py`، `tests/test_sms_production_guard.py` | (تعیین توسط Sina) | pending |
| `leads` | `app/routers/leads.py`، `app/services/leads.py`، `app/services/campaigns.py`، `app/services/sms_outbox.py` | `docs/features/exhibition-lead-capture/SPEC.md`، `docs/features/exhibition-lead-capture/PRD.md` | `tests/test_leads_edit_session.py`، `tests/test_leads_visitor_rotation.py`، `tests/test_leads_campaigns.py`، `tests/test_leads_edit_fields.py` | (تعیین توسط Sina) | pending |

## زیرسیستم‌های مشترک

این‌ها ماژول رجیستری نیستند، اما چند ماژول به آن‌ها تکیه می‌کنند.

| زیرسیستم | فایل‌های کلیدی | سند طراحی | تست‌ها | مالک | بازبینی انسانی |
|---|---|---|---|---|---|
| احراز هویت و امنیت | `app/auth/security.py`، `app/auth/csrf.py`، `app/auth/visitor.py` | `docs/engineering/SECURITY_MODEL.md`، `docs/engineering/SECURITY.md`، ADR-014/015/016 | `tests/test_csrf.py`، `tests/test_security_hardening.py`، `tests/test_client_ip.py`، `tests/test_visitor_session.py` | (تعیین توسط Sina) | pending |
| دیتابیس و migrationها | `app/db/`، `migrations/`، `scripts/apply_migrations.py` | `docs/engineering/DATABASE.md`، `docs/engineering/POSTGRES_TESTING.md` | `tests/postgres/`، `tests/test_db_upgrade.py`، `tests/test_pg_layer.py` | (تعیین توسط Sina) | pending |
| لایهٔ کنترل providerهای AI | `app/services/ai/`، `app/routers/admin_ai.py`، `app/services/providers.py`، `app/services/openai.py` | `docs/engineering/ai-providers/`، ADR-007 | `tests/test_ai_engine.py`، `tests/test_ai_store.py`، `tests/test_ai_circuit.py`، `tests/test_ai_secret_redaction.py` | (تعیین توسط Sina) | pending |
| دادهٔ شرکت‌ها | `app/services/company_profiles.py`، `app/services/company_search.py`، `app/services/company_autofill.py` | `docs/features/companies-own-table/RESEARCH.md`، ADR-021 | `tests/test_company_profiles.py`، `tests/test_company_search.py`، `tests/test_company_autofill.py` | (تعیین توسط Sina) | pending |
| استقرار و CI | `deploy/`، `deploy/padyar-deploy.sh`، `.github/workflows/` | `docs/engineering/DEPLOYMENT_RUNBOOK.md`، `docs/features/release-process/SPEC.md` | `tests/test_secret_scan.py`، `tests/test_watchdog_logic.py`، `tests/test_handover_packaging.py` | (تعیین توسط Sina) | pending |
| متریک‌ها | `app/routers/metrics.py`، `app/services/metrics.py` | `docs/features/metrics-endpoint/SPEC.md`، `docs/engineering/MONITORING.md` | `tests/test_metrics.py` | (تعیین توسط Sina) | pending |
| ارزیابی بازیابی | `scripts/run_eval.py`، `data/eval/` | docstring خود اسکریپت | `tests/test_run_eval_conversations.py` | (تعیین توسط Sina) | pending |
| فرایند بازبینی PR | `.github/CODEOWNERS`، `.github/pull_request_template.md`، `.github/workflows/pr-governance.yml`، `scripts/check_pr_governance.py` | `docs/features/review-governance/SPEC.md`، ADR-022 | `tests/test_pr_governance_check.py`، `tests/test_code_ownership_doc.py` | (تعیین توسط Sina) | pending |
