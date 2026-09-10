"""Tests for the delivery routing module."""

from gateway.config import Platform
from gateway.delivery import DeliveryTarget
from gateway.session import SessionSource


class TestParseTargetPlatformChat:
    def test_explicit_telegram_chat(self):
        target = DeliveryTarget.parse("telegram:12345")
        assert target.platform == Platform.TELEGRAM
        assert target.chat_id == "12345"
        assert target.is_explicit is True

    def test_platform_only_no_chat_id(self):
        target = DeliveryTarget.parse("discord")
        assert target.platform == Platform.DISCORD
        assert target.chat_id is None
        assert target.is_explicit is False

    def test_local_target(self):
        target = DeliveryTarget.parse("local")
        assert target.platform == Platform.LOCAL
        assert target.chat_id is None

    def test_origin_with_source(self):
        origin = SessionSource(platform=Platform.TELEGRAM, chat_id="789", thread_id="42")
        target = DeliveryTarget.parse("origin", origin=origin)
        assert target.platform == Platform.TELEGRAM
        assert target.chat_id == "789"
        assert target.thread_id == "42"
        assert target.is_origin is True

    def test_origin_without_source(self):
        target = DeliveryTarget.parse("origin")
        assert target.platform == Platform.LOCAL
        assert target.is_origin is True

    def test_unknown_platform(self):
        target = DeliveryTarget.parse("unknown_platform")
        assert target.platform == Platform.LOCAL


class TestTargetToStringRoundtrip:
    def test_origin_roundtrip(self):
        origin = SessionSource(platform=Platform.TELEGRAM, chat_id="111", thread_id="42")
        target = DeliveryTarget.parse("origin", origin=origin)
        assert target.to_string() == "origin"

    def test_local_roundtrip(self):
        target = DeliveryTarget.parse("local")
        assert target.to_string() == "local"

    def test_platform_only_roundtrip(self):
        target = DeliveryTarget.parse("discord")
        assert target.to_string() == "discord"

    def test_explicit_chat_roundtrip(self):
        target = DeliveryTarget.parse("telegram:999")
        s = target.to_string()
        assert s == "telegram:999"

        reparsed = DeliveryTarget.parse(s)
        assert reparsed.platform == Platform.TELEGRAM
        assert reparsed.chat_id == "999"





def _dynamic_platform(monkeypatch, name):
    """Resolve *name* as a runtime-registered plugin platform, as in production."""
    from gateway.platform_registry import platform_registry

    monkeypatch.setattr(platform_registry, "is_registered", lambda value: value == name)
    return Platform(name)


class TestPlatformDelivery:
    """Cron output is capped for messengers and A2UI only ships where it renders.

    Regression: a room report was cut mid-``application/a2ui+json`` fence, so
    rooms-api could not parse the block and the user saw raw JSON. The same job
    also delivers to Telegram, which cannot render A2UI at all.
    """

    def _router(self, tmp_path, monkeypatch):
        import gateway.delivery as delivery

        monkeypatch.setattr(delivery, "get_hermes_home", lambda: tmp_path)
        return delivery.DeliveryRouter(config=None, adapters={})

    def _deliver(self, router, platform_value, content, monkeypatch):
        import asyncio

        sent = {}

        class _Adapter:
            async def send(self, chat_id, text, metadata=None):
                sent["text"] = text
                return {"ok": True}

        platform = _dynamic_platform(monkeypatch, platform_value)
        router.adapters[platform] = _Adapter()
        target = DeliveryTarget(platform=platform, chat_id="c1")
        asyncio.run(router._deliver_to_platform(target, content, {"job_id": "j1"}))
        return sent["text"]

    def _report(self, filler):
        return (
            "Report header\n\n"
            "```application/a2ui+json\n"
            '[{"version":"v0.9","text":"' + filler + '"}]\n'
            "```\n"
        )

    def test_hub_chat_keeps_the_a2ui_block_untruncated(self, tmp_path, monkeypatch):
        router = self._router(tmp_path, monkeypatch)
        content = self._report("y" * 9000)
        assert self._deliver(router, "hub_chat", content, monkeypatch) == content

    def test_telegram_drops_the_a2ui_block(self, tmp_path, monkeypatch):
        router = self._router(tmp_path, monkeypatch)
        sent = self._deliver(router, "telegram", self._report("y" * 9000), monkeypatch)
        assert sent == "Report header"

    def test_telegram_never_gets_a_half_fence(self, tmp_path, monkeypatch):
        router = self._router(tmp_path, monkeypatch)
        sent = self._deliver(router, "telegram", self._report("y" * 9000), monkeypatch)
        assert "```" not in sent
        assert "truncated" not in sent

    def test_a2ui_only_message_leaves_a_note(self, tmp_path, monkeypatch):
        from gateway.delivery import A2UI_OMITTED_NOTE

        router = self._router(tmp_path, monkeypatch)
        content = "```application/a2ui+json\n[]\n```\n"
        assert self._deliver(router, "telegram", content, monkeypatch) == A2UI_OMITTED_NOTE

    def test_plain_text_still_truncated(self, tmp_path, monkeypatch):
        from gateway.delivery import TRUNCATED_VISIBLE

        router = self._router(tmp_path, monkeypatch)
        sent = self._deliver(router, "telegram", "x" * 9000, monkeypatch)
        assert sent.startswith("x" * TRUNCATED_VISIBLE)
        assert "[truncated, full output saved to" in sent


class TestStripA2uiBlocks:
    def test_unterminated_block_is_dropped_to_end(self):
        from gateway.delivery import strip_a2ui_blocks

        assert strip_a2ui_blocks("head\n\n```a2ui\n[{\"a\": 1") == "head"

    def test_other_code_fences_survive(self):
        from gateway.delivery import strip_a2ui_blocks

        content = "head\n\n```json\n{}\n```"
        assert strip_a2ui_blocks(content) == content
