"""
Module Registry — defines available modules, their metadata,
and loads routers conditionally based on ENABLED_MODULES config.
"""

import importlib
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("PadyarAssistant")


@dataclass
class ModuleDef:
    """Definition of an installable module."""

    name: str
    description: str
    is_core: bool = False
    router_module: str = ""  # e.g. "app.routers.voice"
    router_var: str = "router"  # variable name in the router module


# ── Module catalogue ────────────────────────────────────────────────
MODULES: dict[str, ModuleDef] = {
    # Core modules — always enabled
    "chat": ModuleDef(
        name="chat",
        description="Chatbot engine (TF-IDF + GPT fallback)",
        is_core=True,
        router_module="app.routers.chat",
    ),
    "admin": ModuleDef(
        name="admin",
        description="Admin dashboard and API",
        is_core=True,
        router_module="app.routers.admin",
    ),
    "search": ModuleDef(
        name="search",
        description="Synonym management API",
        is_core=True,
        router_module="app.routers.synonyms",
    ),
    "dataset": ModuleDef(
        name="dataset",
        description="Dataset and questions CRUD",
        is_core=True,
        router_module="app.routers.dataset",
    ),
    "theme": ModuleDef(
        name="theme",
        description="Theme management and switching",
        is_core=True,
        router_module="app.routers.themes",
    ),
    # Core, not optional. It is the admin's only view of the visitors,
    # conversations and messages the CORE chat module always writes: an
    # install able to switch it off would still collect names, phone numbers
    # and everything people typed, with nobody able to read, check or export
    # any of it. The rule that a new feature is optional protects installs
    # from paying for what they did not order; this is the reading end of
    # something every install already records.
    "conversations": ModuleDef(
        name="conversations",
        description="Visitors, conversation transcripts and the wrong-answer queue",
        is_core=True,
        router_module="app.routers.conversations_admin",
    ),
    # Optional modules — enabled via ENABLED_MODULES
    "voice": ModuleDef(
        name="voice",
        description="Voice input via Whisper API",
        is_core=False,
        router_module="app.routers.voice",
    ),
    "video": ModuleDef(
        name="video",
        description="Video upload and serving",
        is_core=False,
        router_module="app.routers.dataset",  # video endpoints live in dataset router
    ),
    "infra": ModuleDef(
        name="infra",
        description="Infrastructure centre: database diagnostics, storage, backups",
        is_core=False,
        router_module="app.routers.dbadmin",
    ),
    "backups": ModuleDef(
        name="backups",
        description="Backup and restore centre",
        is_core=False,
        router_module="app.routers.backups",
    ),
    "ops": ModuleDef(
        name="ops",
        description="Admin operations centre: dashboard, service health and control",
        is_core=False,
        router_module="app.routers.ops",
    ),
    "logs": ModuleDef(
        name="logs",
        description="Central logging, audit trail and security events",
        is_core=False,
        router_module="app.routers.logs",
    ),
    "tts": ModuleDef(
        name="tts",
        description="Persian text-to-speech control panel (Chatterbox) + voice cloning",
        is_core=False,
        router_module="app.routers.tts",
    ),
    "registration": ModuleDef(
        name="registration",
        description="Visitor registration + SMS verification (/verify, /api/auth/otp/*)",
        is_core=False,
        router_module="app.routers.otp",
    ),
    "leads": ModuleDef(
        name="leads",
        description="Exhibition lead capture: field visitors, company contacts, "
                    "one-time edit invites and the edit review queue",
        is_core=False,
        router_module="app.routers.leads",
    ),
    "pwa_api": ModuleDef(
        name="pwa_api",
        description="Bearer-token API for the InotexPWA app: public companies "
                    "directory, chat-token mint, visitor settings and QR contact "
                    "exchange",
        is_core=False,
        router_module="app.routers.pwa_api",
    ),
}

CORE_MODULE_NAMES = [m.name for m in MODULES.values() if m.is_core]
OPTIONAL_MODULE_NAMES = [m.name for m in MODULES.values() if not m.is_core]


def resolve_enabled_modules(enabled_modules_str: str) -> list[str]:
    """
    Resolve the final list of enabled module names.

    - Core modules are always included.
    - If enabled_modules_str is empty/blank → all optional modules are enabled (backward compat).
    - Otherwise only the listed optional modules are included.
    """
    enabled = list(CORE_MODULE_NAMES)

    if not enabled_modules_str or not enabled_modules_str.strip():
        # No config → enable everything (backward compatible)
        enabled.extend(OPTIONAL_MODULE_NAMES)
    else:
        for name in enabled_modules_str.split(","):
            name = name.strip().lower()
            if name in OPTIONAL_MODULE_NAMES:
                enabled.append(name)
            elif name not in CORE_MODULE_NAMES:
                logger.warning(f"Unknown module '{name}' in ENABLED_MODULES, ignoring")

    return enabled


def module_enabled(module_name: str, enabled_modules: list[str]) -> bool:
    """Check if a specific module is enabled."""
    return module_name in enabled_modules


def load_module_routers(enabled_modules: list[str]):
    """
    Import and return (module_name, router) pairs for all enabled modules.
    Skips modules that fail to import with a warning.
    """
    routers = []

    for module_name in enabled_modules:
        mod_def = MODULES.get(module_name)
        if not mod_def or not mod_def.router_module:
            continue

        try:
            module = importlib.import_module(mod_def.router_module)
            router = getattr(module, mod_def.router_var)
            routers.append((module_name, router))
            logger.info(f"Module loaded: {module_name}")
        except ImportError as e:
            logger.error(f"Failed to load module '{module_name}': {e}")
        except AttributeError as e:
            logger.error(f"Module '{module_name}' has no '{mod_def.router_var}': {e}")

    return routers
