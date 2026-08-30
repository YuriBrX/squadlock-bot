# Cogs/squadlock_cog.py
# discord.py 2.6+ (Components V2 / UI Kit) | Python 3.10+
from __future__ import annotations

import json
import time
import re
from dataclasses import dataclass, asdict, field
from typing import Dict, Optional, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

from paths import RUNTIME_DIR

# =========================
# ======= CONFIGS =========
# =========================
DATA_FILE = RUNTIME_DIR / "squadlocks.json"

# Confirmado: Container (UI Kit) usa Colour/Color como accent_color.
EMBED_COLOR = discord.Color(0x245682)  # Warden sRGB 245682FF (sem alfa)
DEFAULT_WINDOW_HOURS = 48

# Alerts (minutes before expiry)
ALERT_OFFSETS_MIN = [481, 121, 16]

# Mention strategy: "role" | "everyone" | "none"
MENTION_STRATEGY = "everyone"
ALERT_ROLE_ID: Optional[int] = None
MENTION_MODE_CHOICES = [
    app_commands.Choice(name="@everyone", value="everyone"),
    app_commands.Choice(name="Roles", value="role"),
    app_commands.Choice(name="None", value="none"),
]

# Optional channel for log of who clicked Renew
RENEW_LOG_CHANNEL_ID: Optional[int] = None

# Branding
BRAND_NAME = "Special Vehicle Information"
BADGE_LEFT = "Navy"
BADGE_STATE = "Available"

# Buttons (UI Kit)
RENEW_LABEL = "Renew"
SETWIN_LABEL = "Squadlock TTL"
CLOSE_LABEL = "Close"
WARNME_LABEL = "Warn me"

# =========================
# ======= MODELS ==========
# =========================
@dataclass
class SquadlockRecord:
    guild_id: int
    channel_id: int
    message_id: int
    vehicle_name: str
    renewed_ts: int
    expires_ts: int
    window_hours: int
    alerted_offsets: List[int]
    thumbnail_url: Optional[str] = None
    expired_announced: bool = False  # avoids expired spam
    mention_strategy: str = MENTION_STRATEGY
    mention_role_ids: List[int] = field(default_factory=list)
    subscribers: List[int] = field(default_factory=list)


def _load_all() -> Dict[str, SquadlockRecord]:
    if not DATA_FILE.exists():
        return {}
    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        out: Dict[str, SquadlockRecord] = {}
        for k, v in raw.items():
            if not isinstance(v, dict):
                continue
            data = dict(v)
            role_ids: List[int] = []
            if isinstance(data.get("mention_role_ids"), list):
                role_ids = [rid for rid in data["mention_role_ids"] if isinstance(rid, int)]
            elif isinstance(data.get("mention_role_ids"), int):
                role_ids = [data["mention_role_ids"]]
            legacy_role_id = data.pop("mention_role_id", None)
            if not role_ids and isinstance(legacy_role_id, int):
                role_ids = [legacy_role_id]
            data["mention_role_ids"] = role_ids
            if not isinstance(data.get("subscribers"), list):
                data["subscribers"] = []
            out[k] = SquadlockRecord(**data)
        return out
    except Exception:
        return {}


def _save_all(db: Dict[str, SquadlockRecord]) -> None:
    serial = {k: asdict(v) for k, v in db.items()}
    DATA_FILE.write_text(json.dumps(serial, ensure_ascii=False, indent=2), encoding="utf-8")


def _key(message_id: int) -> str:
    return str(message_id)


# =========================
# ======== MODAL ==========
# =========================
class SetWindowModal(discord.ui.Modal, title="Update TTL (hours)"):
    new_window = discord.ui.TextInput(
        label="TTL in hours",
        placeholder="e.g. 48",
        required=True,
        min_length=1,
        max_length=4,
    )

    def __init__(self, cog: "SquadlockCog", message: discord.Message):
        super().__init__(timeout=180)
        self.cog = cog
        self.message = message

    async def on_submit(self, interaction: discord.Interaction):
        rec = self.cog.db.get(_key(self.message.id))
        if not rec:
            return await interaction.response.send_message("Card not registered.", ephemeral=True)

        try:
            win = max(1, int(str(self.new_window)))
        except ValueError:
            return await interaction.response.send_message("Invalid number.", ephemeral=True)

        now = int(time.time())
        rec.window_hours = win
        rec.renewed_ts = now
        rec.expires_ts = now + win * 3600
        rec.alerted_offsets = []
        rec.expired_announced = False
        self.cog.db[_key(self.message.id)] = rec
        _save_all(self.cog.db)

        # Components V2: edita apenas o view. Também remove possíveis embeds legados.
        try:
            await self.message.edit(content=None, embeds=[], view=self.cog.build_card_view(rec))
        except discord.HTTPException:
            pass

        await interaction.response.send_message(f"TTL set to **{win}h**.", ephemeral=True)


# =========================
# ===== UI KIT (V2) =======
# =========================
class RolePickerView(discord.ui.LayoutView):
    header = discord.ui.TextDisplay("Select one or more roles to mention for this Squadlock.")
    row = discord.ui.ActionRow()

    def __init__(
        self,
        cog: "SquadlockCog",
        owner_id: int,
        channel_id: int,
        vehicle_name: str,
        window_hours: int,
        thumbnail_url: Optional[str],
    ):
        super().__init__(timeout=180)
        self.cog = cog
        self.owner_id = owner_id
        self.channel_id = channel_id
        self.vehicle_name = vehicle_name
        self.window_hours = window_hours
        self.thumbnail_url = thumbnail_url

    @row.select(
        cls=discord.ui.RoleSelect,
        placeholder="Select roles to mention... (can type here!)",
        min_values=1,
        max_values=5,
    )
    async def pick_roles(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message(
                "Only the creator can pick roles for this panel.",
                ephemeral=True,
            )

        if interaction.guild is None:
            return await interaction.response.send_message(
                "Role selection only works inside a server.",
                ephemeral=True,
            )

        channel = interaction.client.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await interaction.client.fetch_channel(self.channel_id)
            except discord.HTTPException:
                channel = None
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await interaction.response.send_message(
                "This command must be used in a server text channel.",
                ephemeral=True,
            )

        now = int(time.time())
        role_ids = [role.id for role in select.values]
        rec = SquadlockRecord(
            guild_id=interaction.guild.id,
            channel_id=channel.id,
            message_id=0,
            vehicle_name=self.vehicle_name,
            renewed_ts=now,
            expires_ts=now + self.window_hours * 3600,
            window_hours=self.window_hours,
            alerted_offsets=[],
            thumbnail_url=self.thumbnail_url,
            expired_announced=False,
            mention_strategy="role",
            mention_role_ids=list(dict.fromkeys(role_ids)),
        )

        try:
            msg = await channel.send(view=self.cog.build_card_view(rec))
        except discord.Forbidden:
            return await interaction.response.send_message(
                "I can't post in this channel. Please grant **View Channel** and **Send Messages** permissions.",
                ephemeral=True,
            )
        except discord.HTTPException:
            return await interaction.response.send_message(
                "I couldn't create the card due to a Discord error. Please try again.",
                ephemeral=True,
            )

        rec.message_id = msg.id
        self.cog.db[_key(msg.id)] = rec
        _save_all(self.cog.db)

        pretty = ", ".join(role.mention for role in select.values) if select.values else "(none)"
        await interaction.response.send_message(
            f"Squadlock card created and roles saved: {pretty}",
            ephemeral=True,
        )


class RenewButton(discord.ui.Button):
    def __init__(self, cog: "SquadlockCog"):
        super().__init__(
            label=RENEW_LABEL,
            emoji="🔄",
            style=discord.ButtonStyle.success,
            custom_id="squadlock:renew",
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        message = interaction.message
        if not message:
            return await interaction.response.send_message("Message not found.", ephemeral=True)

        rec = self.cog.db.get(_key(message.id))
        if not rec:
            return await interaction.response.send_message("Card not registered.", ephemeral=True)

        now = int(time.time())
        rec.renewed_ts = now
        rec.expires_ts = now + rec.window_hours * 3600
        rec.alerted_offsets = []
        rec.expired_announced = False
        self.cog.db[_key(message.id)] = rec
        _save_all(self.cog.db)

        # Atualiza o layout inteiro (container + textos + botões)
        try:
            await interaction.response.edit_message(content=None, embeds=[], view=self.cog.build_card_view(rec))
        except discord.HTTPException:
            # fallback: responde ephemeral se não conseguir editar
            try:
                await interaction.response.send_message("Squadlock renewed (could not edit card).", ephemeral=True)
            except discord.HTTPException:
                pass
            return

        await self.cog._log_renew(interaction.user, rec)


class SetWindowButton(discord.ui.Button):
    def __init__(self, cog: "SquadlockCog"):
        super().__init__(
            label=SETWIN_LABEL,
            emoji="⏳",
            style=discord.ButtonStyle.primary,
            custom_id="squadlock:setwin",
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        message = interaction.message
        if not message:
            return await interaction.response.send_message("Message not found.", ephemeral=True)

        if _key(message.id) not in self.cog.db:
            return await interaction.response.send_message("Card not registered.", ephemeral=True)

        await interaction.response.send_modal(SetWindowModal(self.cog, message))


class WarnMeButton(discord.ui.Button):
    def __init__(self, cog: "SquadlockCog"):
        super().__init__(
            label=WARNME_LABEL,
            emoji="🔔",
            style=discord.ButtonStyle.secondary,
            custom_id="squadlock:warnme",
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        message = interaction.message
        if not message:
            return await interaction.response.send_message("Message not found.", ephemeral=True)

        rec = self.cog.db.get(_key(message.id))
        if not rec:
            return await interaction.response.send_message("Card not registered.", ephemeral=True)

        user_id = interaction.user.id
        if user_id in rec.subscribers:
            rec.subscribers = [uid for uid in rec.subscribers if uid != user_id]
            status = "You will no longer be notified for this Squadlock."
        else:
            rec.subscribers.append(user_id)
            status = "You will be notified for this Squadlock."

        self.cog.db[_key(message.id)] = rec
        _save_all(self.cog.db)

        await interaction.response.send_message(status, ephemeral=True)


class CloseButton(discord.ui.Button):
    def __init__(self, cog: "SquadlockCog"):
        super().__init__(
            label=CLOSE_LABEL,
            emoji="⛔",
            style=discord.ButtonStyle.danger,
            custom_id="squadlock:close",
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        message = interaction.message
        if not message:
            return await interaction.response.send_message("Message not found.", ephemeral=True)

        # Permissão simples (ajuste como quiser):
        if isinstance(interaction.user, discord.Member):
            if not interaction.user.guild_permissions.manage_messages:
                return await interaction.response.send_message(
                    "You need **Manage Messages** to close a card.",
                    ephemeral=True,
                )

        if _key(message.id) in self.cog.db:
            self.cog.db.pop(_key(message.id), None)
            _save_all(self.cog.db)

        # Deleta a mensagem do card
        try:
            await message.delete()
        except discord.HTTPException:
            # Se não puder deletar, pelo menos desmonta e marca fechado
            try:
                await interaction.response.edit_message(
                    content=None,
                    embeds=[],
                    view=self.cog.build_closed_view(),
                )
            except discord.HTTPException:
                pass
            else:
                return

        # Se já deletou, só confirma ephemeral (se der tempo)
        try:
            await interaction.response.send_message("Card closed.", ephemeral=True)
        except discord.HTTPException:
            pass


class SquadlockCardView(discord.ui.LayoutView):
    """Layout bonito (Components V2) com Container + Section + Separators + ActionRow."""

    def __init__(self, cog: "SquadlockCog", rec: SquadlockRecord):
        super().__init__(timeout=None)
        self.cog = cog
        self._build(rec)

    def _build(self, rec: SquadlockRecord) -> None:
        renewed_f = f"<t:{rec.renewed_ts}:F>  |  <t:{rec.renewed_ts}:R>\n"
        expires_f = f"<t:{rec.expires_ts}:F>  |  <t:{rec.expires_ts}:R>\n"
        badges = f"⚓ **{BADGE_LEFT}**  |  🟢 **{BADGE_STATE}**"

        # --- Container principal (visual de "embed")
        box = discord.ui.Container(accent_color=EMBED_COLOR)

        # Header (Section com thumbnail opcional)
        title_block = discord.ui.TextDisplay(
            "\n".join(
                [
                    f"## {rec.vehicle_name}",
                    f"**📝 {BRAND_NAME}**",
                    badges,
                ]
            )
        )

        if rec.thumbnail_url:
            thumb = discord.ui.Thumbnail(rec.thumbnail_url, description="Vehicle thumbnail")
            box.add_item(discord.ui.Section(title_block, accessory=thumb))
        else:
            box.add_item(title_block)

        box.add_item(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))

        # Corpo (informacoes)
        info = discord.ui.TextDisplay(
            "\n".join(
                [
                    "### 🔐 Squadlock",
                    f"**🔒 Renewed:** {renewed_f}",
                    f"**🔓 Expires:** {expires_f}",
                    f"**⚙️ TTL:** `{rec.window_hours}h`  (default `{DEFAULT_WINDOW_HOURS}h`)",
                    "",
                    "⬇️ Use the buttons below to manage the card. ⬇️",
                ]
            )
        )
        box.add_item(info)

        box.add_item(discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.small))

        # Acao (botoes dentro do container)
        row = discord.ui.ActionRow(id=1001)
        row.add_item(RenewButton(self.cog))
        row.add_item(WarnMeButton(self.cog))
        row.add_item(SetWindowButton(self.cog))
        row.add_item(CloseButton(self.cog))
        box.add_item(row)

        # LayoutView recebe top-level components (o container e top-level)
        self.add_item(box)


class SquadlockClosedView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        box = discord.ui.Container(accent_color=discord.Color.dark_grey())
        box.add_item(discord.ui.TextDisplay("## 🔒 Squadlock\n**Status:** `Closed`"))
        self.add_item(box)


class SquadlockPersistentHandler(discord.ui.LayoutView):
    """
    View “registrada” no bot (persistente) só para o discord.py saber
    como rotear os custom_id após restart.
    """

    def __init__(self, cog: "SquadlockCog"):
        super().__init__(timeout=None)
        self.cog = cog

        # Não precisa ter o layout completo aqui — só precisa conter
        # os itens interativos com os mesmos custom_id.
        row = discord.ui.ActionRow(id=9999)
        row.add_item(RenewButton(cog))
        row.add_item(SetWindowButton(cog))
        row.add_item(WarnMeButton(cog))
        row.add_item(CloseButton(cog))
        self.add_item(row)


# =========================
# ========= COG ===========
# =========================
class SquadlockCog(commands.Cog):
    """Squadlock cards using Components V2 (UI Kit)."""

    LINK_RE = re.compile(r"https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Dict[str, SquadlockRecord] = _load_all()

        # Registra handler persistente (importantíssimo pro restart)
        self.bot.add_view(SquadlockPersistentHandler(self))

        self.watcher.start()

    def cog_unload(self):
        self.watcher.cancel()

    # ---------- UI builders ----------
    def build_card_view(self, rec: SquadlockRecord) -> discord.ui.LayoutView:
        return SquadlockCardView(self, rec)

    def build_closed_view(self) -> discord.ui.LayoutView:
        return SquadlockClosedView()

    # ---------- Alerts / Mentions ----------
    def _mention_payload(self, rec: SquadlockRecord) -> Tuple[str, discord.AllowedMentions]:
        strategy = (rec.mention_strategy or MENTION_STRATEGY).strip().lower()

        mention_parts: List[str] = []
        allow_everyone = False
        allow_roles = False
        allow_users = False

        if strategy == "everyone":
            mention_parts.append("@everyone")
            allow_everyone = True
        elif strategy == "role":
            role_ids = list(dict.fromkeys(rec.mention_role_ids))
            if role_ids:
                mention_parts.append(" ".join(f"<@&{rid}>" for rid in role_ids))
                allow_roles = True

        if rec.subscribers:
            mention_parts.append(" ".join(f"<@{uid}>" for uid in rec.subscribers))
            allow_users = True

        mention_text = " ".join(part for part in mention_parts if part).strip()
        allowed_mentions = discord.AllowedMentions(
            everyone=allow_everyone,
            roles=allow_roles,
            users=allow_users,
        )
        return mention_text, allowed_mentions

    async def _log_renew(self, user: discord.abc.User, rec: SquadlockRecord):
        if not RENEW_LOG_CHANNEL_ID:
            return
        ch = self.bot.get_channel(RENEW_LOG_CHANNEL_ID)
        if not isinstance(ch, discord.TextChannel):
            return
        try:
            await ch.send(
                f"Renewed **{rec.vehicle_name}** by {user.mention}. "
                f"New expiry: <t:{rec.expires_ts}:R> (<t:{rec.expires_ts}:F>)"
            )
        except discord.HTTPException:
            pass

    async def _resolve_message_from_url(self, message_url: str) -> Optional[discord.Message]:
        m = self.LINK_RE.fullmatch(message_url.strip())
        if not m:
            return None
        _, channel_id, message_id = map(int, m.groups())
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return None
        try:
            return await channel.fetch_message(message_id)
        except discord.HTTPException:
            return None

    async def _resolve_card_message(
        self,
        interaction: discord.Interaction,
        message_url: Optional[str],
    ) -> Optional[discord.Message]:
        if message_url:
            msg = await self._resolve_message_from_url(message_url)
            if msg:
                return msg
            await interaction.response.send_message("Invalid or inaccessible message link.", ephemeral=True)
            return None

        if interaction.channel:
            recs_here = [r for r in self.db.values() if r.channel_id == interaction.channel.id]
            if len(recs_here) == 1:
                ch = interaction.channel
                try:
                    return await ch.fetch_message(recs_here[0].message_id)  # type: ignore
                except Exception:
                    pass

        await interaction.response.send_message(
            "Provide the card **message link** or use the **Apps** menu on the card.",
            ephemeral=True,
        )
        return None

    # ---------- Slash Commands ----------
    squadlock = app_commands.Group(name="squadlock", description="Manage Squadlock cards")

    @squadlock.command(name="post", description="Create a Squadlock card here (Components V2).")
    @app_commands.describe(
        vehicle_name='Full vehicle name (ex.: GGMS "Bobby McFrigg" (Frigate))',
        window_hours=f"TTL until expiry (default: {DEFAULT_WINDOW_HOURS}h)",
        thumbnail_url="Optional thumbnail (map/camera/minimap image URL)",
        mention_mode="Who to mention on alerts (required).",
    )
    @app_commands.choices(mention_mode=MENTION_MODE_CHOICES)
    async def post(
        self,
        interaction: discord.Interaction,
        vehicle_name: str,
        mention_mode: str,
        window_hours: Optional[int] = None,
        thumbnail_url: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        ch = interaction.channel
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            return await interaction.followup.send("This command must be used in a server text channel.", ephemeral=True)

        win = max(1, window_hours or DEFAULT_WINDOW_HOURS)
        strategy = mention_mode.strip().lower()
        if strategy not in {"everyone", "role", "none"}:
            return await interaction.followup.send(
                "Invalid mention mode. Please pick one of the provided choices.",
                ephemeral=True,
            )

        if strategy == "role":
            view = RolePickerView(self, interaction.user.id, ch.id, vehicle_name, win, thumbnail_url)
            return await interaction.followup.send(view=view, ephemeral=True)

        now = int(time.time())
        rec = SquadlockRecord(
            guild_id=interaction.guild_id or 0,
            channel_id=ch.id,
            message_id=0,
            vehicle_name=vehicle_name,
            renewed_ts=now,
            expires_ts=now + win * 3600,
            window_hours=win,
            alerted_offsets=[],
            thumbnail_url=thumbnail_url,
            expired_announced=False,
            mention_strategy=strategy,
        )

        # Components V2: não envia embed/content; o "conteúdo" é TextDisplay no LayoutView.
        try:
            msg = await ch.send(view=self.build_card_view(rec))
        except discord.Forbidden:
            return await interaction.followup.send(
                "I can't post in this channel. Please grant **View Channel** and **Send Messages** permissions.",
                ephemeral=True,
            )
        except discord.HTTPException:
            return await interaction.followup.send(
                "I couldn't create the card due to a Discord error. Please try again.",
                ephemeral=True,
            )

        rec.message_id = msg.id
        self.db[_key(msg.id)] = rec
        _save_all(self.db)

        await interaction.followup.send(
            f"Squadlock card created for **{vehicle_name}** (TTL: **{win}h**).",
            ephemeral=True,
        )

    @squadlock.command(name="close", description="Stop tracking a Squadlock card.")
    @app_commands.describe(message_url="(Optional) Link to the card message")
    async def close(self, interaction: discord.Interaction, message_url: Optional[str] = None):
        msg = await self._resolve_card_message(interaction, message_url)
        if not msg:
            return

        if _key(msg.id) in self.db:
            self.db.pop(_key(msg.id))
            _save_all(self.db)
            try:
                await msg.delete()
            except discord.HTTPException:
                try:
                    await msg.edit(content=None, embeds=[], view=self.build_closed_view())
                except discord.HTTPException:
                    pass
            await interaction.response.send_message("Tracking removed and card closed.", ephemeral=True)
        else:
            await interaction.response.send_message("Card not registered.", ephemeral=True)

    @squadlock.command(name="setwindow", description="Set a new TTL (hours) for a Squadlock card.")
    @app_commands.describe(window_hours="New TTL in hours (e.g., 48)", message_url="(Optional) Link to the card message")
    async def setwindow(self, interaction: discord.Interaction, window_hours: int, message_url: Optional[str] = None):
        msg = await self._resolve_card_message(interaction, message_url)
        if not msg:
            return
        rec = self.db.get(_key(msg.id))
        if not rec:
            return await interaction.response.send_message("Card not registered.", ephemeral=True)

        now = int(time.time())
        rec.window_hours = max(1, window_hours)
        rec.renewed_ts = now
        rec.expires_ts = now + rec.window_hours * 3600
        rec.alerted_offsets = []
        rec.expired_announced = False
        self.db[_key(msg.id)] = rec
        _save_all(self.db)

        try:
            await msg.edit(content=None, embeds=[], view=self.build_card_view(rec))
        except discord.HTTPException:
            pass

        await interaction.response.send_message(f"TTL set to **{rec.window_hours}h**.", ephemeral=True)

    # ---------- Context Menus ----------
    async def cm_close(self, interaction: discord.Interaction, message: discord.Message):
        if _key(message.id) in self.db:
            self.db.pop(_key(message.id), None)
            _save_all(self.db)
            try:
                await message.delete()
            except discord.HTTPException:
                try:
                    await message.edit(content=None, embeds=[], view=self.build_closed_view())
                except discord.HTTPException:
                    pass
            await interaction.response.send_message("Card closed.", ephemeral=True)
        else:
            await interaction.response.send_message("Card not registered.", ephemeral=True)

    async def cm_setwindow(self, interaction: discord.Interaction, message: discord.Message):
        if _key(message.id) not in self.db:
            return await interaction.response.send_message("Card not registered.", ephemeral=True)
        await interaction.response.send_modal(SetWindowModal(self, message))

    # ---------- Watcher (alerts) ----------
    @tasks.loop(seconds=15)
    async def watcher(self):
        now = int(time.time())
        for rec in list(self.db.values()):
            try:
                secs_left = rec.expires_ts - now
                for off in sorted(set(ALERT_OFFSETS_MIN)):
                    if off not in rec.alerted_offsets and 0 < secs_left <= off * 60:
                        await self._send_alert(rec, max(secs_left // 60, 0))
                        rec.alerted_offsets.append(off)
                        self.db[_key(rec.message_id)] = rec
                        _save_all(self.db)

                if secs_left <= 0 and not rec.expired_announced:
                    await self._send_expired(rec)
                    rec.expired_announced = True
                    self.db[_key(rec.message_id)] = rec
                    _save_all(self.db)
            except Exception:
                continue

    @watcher.before_loop
    async def before_watcher(self):
        await self.bot.wait_until_ready()

    async def _send_alert(self, rec: SquadlockRecord, minutes_left: int):
        channel = self.bot.get_channel(rec.channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        mention, allowed = self._mention_payload(rec)
        try:
            await channel.send(
                f"{mention} **{rec.vehicle_name}** expires in **{minutes_left} min** "
                f"(<t:{rec.expires_ts}:R>). Use the button to renew.",
                allowed_mentions=allowed,
            )
        except discord.HTTPException:
            pass

    async def _send_expired(self, rec: SquadlockRecord):
        channel = self.bot.get_channel(rec.channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        mention, allowed = self._mention_payload(rec)
        try:
            await channel.send(
                f"{mention} **{rec.vehicle_name}** has **expired** (<t:{rec.expires_ts}:R>). "
                "Use the button to renew or recreate with `/squadlock post`.",
                allowed_mentions=allowed,
            )
        except discord.HTTPException:
            pass


# =============== SETUP ===============
async def setup(bot: commands.Bot):
    cog = SquadlockCog(bot)
    await bot.add_cog(cog)

    bot.tree.add_command(app_commands.ContextMenu(name="Squadlock Close", callback=cog.cm_close))
    bot.tree.add_command(app_commands.ContextMenu(name="Squadlock Set TTL", callback=cog.cm_setwindow))
