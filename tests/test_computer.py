from __future__ import annotations

import pytest

from dafeiyu.computer import ComputerController, ComputerError, IOREG


def make_controller() -> ComputerController:
    controller = ComputerController()
    controller.is_macos = True
    return controller


async def test_status_parses_app_window_and_idle() -> None:
    controller = make_controller()
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        calls.append(args)
        if args[0] == IOREG:
            return '"HIDIdleTime" = 3000000000'
        return "Safari\n某个网页"

    controller._run = fake_run  # type: ignore[method-assign]
    status = await controller.get_status()
    assert status["app"] == "Safari"
    assert status["window"] == "某个网页"
    assert status["idle_seconds"] == 3
    assert len(calls) == 2


async def test_open_url_only_http() -> None:
    controller = make_controller()
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        calls.append(args)
        return ""

    controller._run = fake_run  # type: ignore[method-assign]
    await controller.open_url("https://example.com/a")
    assert calls[-1] == ("/usr/bin/open", "https://example.com/a")

    for bad in ("file:///etc/passwd", "javascript:alert(1)", "ftp://x", "不是链接"):
        with pytest.raises(ComputerError):
            await controller.open_url(bad)


async def test_open_app_validation_and_allowlist() -> None:
    controller = make_controller()
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        calls.append(args)
        return ""

    controller._run = fake_run  # type: ignore[method-assign]
    await controller.open_app("Safari")
    assert calls[-1] == ("/usr/bin/open", "-a", "Safari")

    for bad in ("", "-Terminal", "a\nb", "x" * 101):
        with pytest.raises(ComputerError):
            await controller.open_app(bad)

    limited = ComputerController(allowed_apps=("Safari",))
    limited.is_macos = True
    with pytest.raises(ComputerError, match="允许列表"):
        await limited.open_app("微信")


async def test_volume_is_clamped_and_validated() -> None:
    controller = make_controller()
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        calls.append(args)
        return ""

    controller._run = fake_run  # type: ignore[method-assign]
    result = await controller.set_volume(150)
    assert result["level"] == 100
    assert calls[-1][-1] == "100"

    await controller.set_volume(-5)
    assert calls[-1][-1] == "0"

    with pytest.raises(ComputerError):
        await controller.set_volume("很大")


async def test_notify_and_media_validation() -> None:
    controller = make_controller()
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        calls.append(args)
        return ""

    controller._run = fake_run  # type: ignore[method-assign]
    await controller.notify("大肥鱼", "该喝水了")
    assert calls[-1][-2:] == ("大肥鱼", "该喝水了")

    with pytest.raises(ComputerError):
        await controller.notify("大肥鱼", "")

    await controller.media("next")
    assert "next track" in calls[-1][2]
    with pytest.raises(ComputerError):
        await controller.media("爆炸")


async def test_unknown_action() -> None:
    controller = make_controller()
    with pytest.raises(ComputerError, match="不支持"):
        await controller.execute("run_shell", command="rm -rf /")
