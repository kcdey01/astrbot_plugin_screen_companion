# -*- coding: utf-8 -*-
"""Remote Screen Companion Client.

Capture screenshots locally and push them to the plugin via WebSocket.

Usage:
    python remote_client.py --server ws://your-server:6315 --token your-token
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import logging
import platform
import sys
import time
from typing import Any

try:
    import pyautogui
except ImportError:
    print("ERROR: pip install pyautogui Pillow")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("ERROR: pip install websockets")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("screen_client")


def get_active_window_title() -> str:
    """Get the active window title when the platform supports it."""
    try:
        if sys.platform == "win32":
            import pygetwindow

            win = pygetwindow.getActiveWindow()
            return str(win.title or "").strip() if win else ""
        if sys.platform == "darwin":
            import subprocess

            script = (
                "tell application \"System Events\" to get name of first application "
                "process whose frontmost is true"
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip()

        import subprocess

        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception as e:
        log.debug(f"Failed to get window title: {e}")
        return ""


def get_system_stats() -> dict[str, Any]:
    stats: dict[str, Any] = {}
    try:
        import psutil

        stats["cpu_percent"] = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        stats["memory_percent"] = mem.percent
        stats["memory_used_mb"] = mem.used // (1024 * 1024)
        battery = psutil.sensors_battery()
        if battery:
            stats["battery_percent"] = battery.percent
            stats["battery_plugged"] = battery.power_plugged
    except ImportError:
        pass
    return stats


def capture_screenshot(image_quality: int = 70) -> bytes:
    screenshot = pyautogui.screenshot()
    if screenshot.mode != "RGB":
        screenshot = screenshot.convert("RGB")
    buf = io.BytesIO()
    screenshot.save(buf, format="JPEG", quality=image_quality)
    return buf.getvalue()


async def run_client(
    server_url: str,
    token: str,
    interval: float,
    image_quality: int,
    client_id: str,
) -> None:
    while True:
        try:
            log.info(f"Connecting to {server_url} ...")
            async with websockets.connect(server_url) as ws:
                if token:
                    await ws.send(json.dumps({"token": token}))
                    resp = json.loads(await ws.recv())
                    if resp.get("status") not in {"authenticated", "ready"}:
                        log.error(f"Auth failed: {resp}")
                        await asyncio.sleep(5)
                        continue
                    log.info(f"Server status: {resp.get('status')}")
                else:
                    resp = json.loads(await ws.recv())
                    log.info(f"Server status: {resp.get('status')}")

                while True:
                    try:
                        jpeg_bytes = await asyncio.to_thread(
                            capture_screenshot,
                            image_quality,
                        )
                        window_title = await asyncio.to_thread(get_active_window_title)
                        system_stats = await asyncio.to_thread(get_system_stats)

                        bundle = {
                            "type": "screenshot_bundle",
                            "image": base64.b64encode(jpeg_bytes).decode("ascii"),
                            "window_title": window_title,
                            "system_stats": system_stats,
                            "timestamp": time.time(),
                            "client_id": client_id,
                        }
                        await ws.send(json.dumps(bundle))

                        ack = await asyncio.wait_for(ws.recv(), timeout=10.0)
                        ack_data = json.loads(ack)
                        log.debug(f"Ack: {ack_data.get('status')}")
                        log.info(
                            f"Sent screenshot: {len(jpeg_bytes)} bytes, "
                            f"window='{window_title}'"
                        )
                    except asyncio.TimeoutError:
                        log.warning("Ack timeout, continuing...")
                    except Exception as e:
                        log.error(f"Send error: {e}")
                        break

                    await asyncio.sleep(interval)

        except websockets.exceptions.ConnectionClosed as e:
            log.warning(f"Connection closed: {e}, reconnecting in 5s...")
            await asyncio.sleep(5)
        except ConnectionRefusedError:
            log.warning("Connection refused, retrying in 10s...")
            await asyncio.sleep(10)
        except Exception as e:
            log.error(f"Unexpected error: {e}, reconnecting in 10s...")
            await asyncio.sleep(10)


def main():
    parser = argparse.ArgumentParser(description="Remote Screen Companion Client")
    parser.add_argument(
        "--server",
        "-s",
        required=True,
        help="WebSocket server URL, e.g. ws://your-server:6315",
    )
    parser.add_argument(
        "--token",
        "-t",
        default="",
        help="Authentication token. Leave empty only if the server also allows no auth.",
    )
    parser.add_argument(
        "--interval",
        "-i",
        type=float,
        default=10.0,
        help="Screenshot interval in seconds (default: 10)",
    )
    parser.add_argument(
        "--quality",
        "-q",
        type=int,
        default=70,
        help="JPEG quality 1-100 (default: 70)",
    )
    parser.add_argument(
        "--client-id",
        default=f"client_{platform.node()}",
        help="Client identifier",
    )
    args = parser.parse_args()

    log.info("Starting remote screen client")
    log.info(f"  Server: {args.server}")
    log.info(f"  Interval: {args.interval}s")
    log.info(f"  Quality: {args.quality}")
    log.info(f"  Client ID: {args.client_id}")

    try:
        asyncio.run(
            run_client(
                server_url=args.server,
                token=args.token,
                interval=args.interval,
                image_quality=args.quality,
                client_id=args.client_id,
            )
        )
    except KeyboardInterrupt:
        log.info("Client stopped")


if __name__ == "__main__":
    main()
