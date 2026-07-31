"""Regression tests for the first-session home-channel onboarding prompt.

``hub_chat`` is the internal hub<->agent transport: replies deliver back to
the hub implicitly, and the platform has no home-target env var (it is in
neither ``cron.scheduler._HOME_TARGET_ENV_VARS`` nor
``_KNOWN_DELIVERY_PLATFORMS``), so ``/sethome`` there would write an env var
nothing reads. Before this fix every fresh hub_chat session triggered the
"No home channel is set" nag, which leaked to end users.
"""

from gateway.config import Platform
from gateway.run import _home_prompt_exempt


def _dynamic_platform(monkeypatch, name):
    """Resolve *name* as a runtime-registered plugin platform, as in production.

    hub_chat has no static ``Platform`` member -- the hub installs it as a
    plugin, so ``Platform("hub_chat")`` only resolves via ``_missing_`` once
    the platform registry knows about it.
    """
    from gateway.platform_registry import platform_registry

    monkeypatch.setattr(
        platform_registry, "is_registered", lambda value: value == name
    )
    return Platform(name)


def test_hub_chat_is_exempt_from_home_channel_prompt(monkeypatch):
    assert _home_prompt_exempt(_dynamic_platform(monkeypatch, "hub_chat")) is True


def test_local_and_webhook_remain_exempt():
    assert _home_prompt_exempt(Platform.LOCAL) is True
    assert _home_prompt_exempt(Platform.WEBHOOK) is True


def test_regular_platforms_still_get_the_prompt():
    assert _home_prompt_exempt(Platform.TELEGRAM) is False
    assert _home_prompt_exempt(Platform.DISCORD) is False
