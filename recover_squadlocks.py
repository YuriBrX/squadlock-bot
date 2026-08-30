from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, Iterable, Optional

import discord

from config import load_settings
from paths import RUNTIME_DIR

DATA_FILE = RUNTIME_DIR / "squadlocks.json"
HISTORY_LIMIT = 50
DEFAULT_WINDOW_HOURS = 48
ALERT_OFFSETS_MIN = [120, 15]
CUSTOM_IDS = {"squadlock:renew", "squadlock:setwin", "squadlock:close"}

TS_RE = re.compile(r"<t:(\d+):[A-Za-z]>")
TTL_RE = re.compile(r"TTL:\s*`(\d+)h`")


def _iter_dicts(obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_dicts(item)


def _collect_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _collect_strings(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _collect_strings(item)


def _components_to_dicts(message: discord.Message) -> list[dict]:
    out: list[dict] = []
    for comp in message.components or []:
        if hasattr(comp, "to_dict"):
            out.append(comp.to_dict())
    return out


def _has_squadlock_buttons(component_dicts: list[dict]) -> bool:
    for comp in _iter_dicts(component_dicts):
        custom_id = comp.get("custom_id")
        if custom_id in CUSTOM_IDS:
            return True
    return False


def _extract_thumbnail(component_dicts: list[dict]) -> Optional[str]:
    for comp in _iter_dicts(component_dicts):
        if comp.get("description") == "Vehicle thumbnail":
            url = comp.get("url")
            if isinstance(url, str):
                return url
    return None


def _extract_fields(component_dicts: list[dict]) -> tuple[Optional[str], Optional[int], Optional[int], Optional[int]]:
    strings = list(_collect_strings(component_dicts))
    vehicle_name: Optional[str] = None
    renewed_ts: Optional[int] = None
    expires_ts: Optional[int] = None
    window_hours: Optional[int] = None

    for text in strings:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("## "):
                candidate = line[3:].strip()
                if candidate and "squadlock" not in candidate.lower():
                    vehicle_name = candidate
                    break
        if vehicle_name:
            break

    for text in strings:
        for line in text.splitlines():
            if "Renewed" in line:
                match = TS_RE.search(line)
                if match:
                    renewed_ts = int(match.group(1))
            elif "Expires" in line:
                match = TS_RE.search(line)
                if match:
                    expires_ts = int(match.group(1))

    for text in strings:
        match = TTL_RE.search(text)
        if match:
            window_hours = int(match.group(1))
            break

    return vehicle_name, renewed_ts, expires_ts, window_hours


def _build_record(
    message: discord.Message,
    component_dicts: list[dict],
) -> Dict[str, Any]:
    now = int(time.time())
    vehicle_name, renewed_ts, expires_ts, window_hours = _extract_fields(component_dicts)

    if window_hours is None and renewed_ts and expires_ts:
        window_hours = max(1, int(round((expires_ts - renewed_ts) / 3600)))
    if window_hours is None:
        window_hours = DEFAULT_WINDOW_HOURS

    if renewed_ts is None:
        renewed_ts = expires_ts - window_hours * 3600 if expires_ts else now
    if expires_ts is None:
        expires_ts = renewed_ts + window_hours * 3600

    secs_left = expires_ts - now
    alerted_offsets = [off for off in ALERT_OFFSETS_MIN if secs_left <= off * 60]
    expired_announced = expires_ts <= now

    return {
        "guild_id": message.guild.id if message.guild else 0,
        "channel_id": message.channel.id,
        "message_id": message.id,
        "vehicle_name": vehicle_name or "Unknown Vehicle",
        "renewed_ts": renewed_ts,
        "expires_ts": expires_ts,
        "window_hours": window_hours,
        "alerted_offsets": alerted_offsets,
        "thumbnail_url": _extract_thumbnail(component_dicts),
        "expired_announced": expired_announced,
    }


async def _scan_channel(
    client: discord.Client,
    channel: discord.abc.Messageable,
    recovered: Dict[str, Dict[str, Any]],
) -> None:
    try:
        async for message in channel.history(limit=HISTORY_LIMIT):
            if message.author.id != client.user.id:
                continue
            component_dicts = _components_to_dicts(message)
            if not component_dicts or not _has_squadlock_buttons(component_dicts):
                continue
            record = _build_record(message, component_dicts)
            recovered[str(message.id)] = record
    except discord.Forbidden:
        logging.warning("No permission to read history in %s", getattr(channel, "name", "unknown"))
    except discord.HTTPException as exc:
        logging.warning("HTTP error reading history in %s: %s", getattr(channel, "name", "unknown"), exc)


async def _recover_all(client: discord.Client, guild_id: Optional[int]) -> Dict[str, Dict[str, Any]]:
    recovered: Dict[str, Dict[str, Any]] = {}
    for guild in client.guilds:
        if guild_id and guild.id != guild_id:
            continue
        for channel in guild.text_channels:
            await _scan_channel(client, channel, recovered)
        for thread in guild.threads:
            await _scan_channel(client, thread, recovered)
    return recovered


async def main() -> None:
    parser = argparse.ArgumentParser(description="Recover Squadlock entries from recent messages.")
    parser.add_argument("--guild-id", type=int, help="Only scan a specific guild ID.")
    args = parser.parse_args()

    settings = load_settings()
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        logging.info("Logged in as %s (%s)", client.user, client.user.id)
        recovered = await _recover_all(client, args.guild_id)
        DATA_FILE.write_text(
            json.dumps(recovered, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logging.info("Recovered %d squadlock entries into %s", len(recovered), DATA_FILE)
        await client.close()

    async with client:
        await client.start(settings.token)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    asyncio.run(main())
