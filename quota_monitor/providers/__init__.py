"""Registry of available provider implementations.

A config section ``[providers.<key>]`` uses the implementation named by its
``type`` setting, or by ``<key>`` itself when no type is given.
"""

from .antigravity import AntigravityProvider
from .base import Provider, ProviderError
from .claude import ClaudeProvider
from .codex import CodexProvider
from .commandcode import CommandCodeProvider
from .deepseek import DeepSeekProvider
from .opencode_go import OpenCodeGoProvider

PROVIDERS: dict[str, type[Provider]] = {
    cls.name: cls
    for cls in (AntigravityProvider, ClaudeProvider, CodexProvider, CommandCodeProvider, DeepSeekProvider,
                OpenCodeGoProvider)
}

# Providers that sign in through the browser: config key -> (menu label, login function).
from . import antigravity, claude, codex  # noqa: E402

LOGINS = {
    "claude": ("Sign in to Claude…", claude.login),
    "codex": ("Sign in to Codex…", codex.login),
    "antigravity": ("Sign in to Antigravity…", antigravity.login),
}

__all__ = ["LOGINS", "PROVIDERS", "Provider", "ProviderError"]
