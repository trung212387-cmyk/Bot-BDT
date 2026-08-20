import asyncio
import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, Modal, Select, TextInput, View
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
BIRTHDAY_FILE = BASE_DIR / "birthdays.json"
CHAT_ACTIVITY_FILE = BASE_DIR / "chat_activity.json"
TARGET_GUILD_ID = 1503922700408586240

# Cơ chế an toàn: Load .env nếu ở máy cục bộ, dùng biến môi trường nếu ở trên Cloud
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(env_path)
    print("✅ Đã load file .env từ máy cục bộ.")
else:
    print("ℹ️ Không tìm thấy file .env, bot sẽ sử dụng biến môi trường (Environment Variables) từ hệ thống Cloud.")

DEFAULT_CONFIG = {
    "welcome_channel_id": None, "welcome_image": "",
    "welcome_message": "Chào mừng {user} đã gia nhập **{server}**!\n\n▫️ **Tài khoản:** {name}\n▫️ **Thành viên thứ:** `{count}`",
    "goodbye_channel_id": None, "goodbye_image": "",
    "goodbye_message": "Tài khoản **{name}** đã rời khỏi cộng đồng.\nHiện tại máy chủ còn lại `{count}` thành viên.",
    "ticket_category_id": None, "staff_role_id": None, "self_role_id": None,
    "birthday_channel_id": None, "unlock_role_id": None,
}


def load_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, type(default)) else default.copy()
    except (OSError, json.JSONDecodeError):
        return default.copy()


def save_json(path, data):
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)


config = DEFAULT_CONFIG.copy()
config.update(load_json(CONFIG_FILE, {}))
birthdays_data = load_json(BIRTHDAY_FILE, {})
chat_activity = load_json(CHAT_ACTIVITY_FILE, {})

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Thiếu DISCORD_TOKEN trong biến môi trường hoặc file .env")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
views_registered = False


def embed(title, description, color=0x2B2D31):
    result = discord.Embed(title=title, description=description, color=color)
    result.set_footer(text="by ph.huyy")
    return result


def get_channel(guild, key):
    value = config.get(key)
    return guild.get_channel(value) if value else None


def record_chat_activity(message):
    week_info = datetime.now().isocalendar()
    week_key = f"{week_info.year}-W{week_info.week:02d}"
    guild_data = chat_activity.setdefault(str(message.guild.id), {})
    week_data = guild_data.setdefault(week_key, {})
    user_key = str(message.author.id)
    user_data = week_data.setdefault(user_key, {"name": message.author.display_name, "messages": 0})
    user_data["name"] = message.author.display_name
    user_data["messages"] += 1
    user_data["last_message"] = datetime.now().astimezone().isoformat()
    save_json(CHAT_ACTIVITY_FILE, chat_activity)


class CloseTicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Đóng Ticket", style=discord.ButtonStyle.secondary, custom_id="close_ticket")
    async def close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(embed=embed("⚠️ XÁC NHẬN ĐÓNG TICKET", "Kênh sẽ tự động xóa sau **5 giây**...", 0xFEE75C))
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason="Ticket closed")
        except discord.HTTPException:
            pass


class TicketPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    async def create_ticket(self, interaction, ticket_type, prefix):
        guild = interaction.guild
        user = interaction.user
        category = guild.get_channel(config.get("ticket_category_id")) if config.get("ticket_category_id") else None
        staff = guild.get_role(config.get("staff_role_id")) if config.get("staff_role_id") else None
        name = f"ticket-{prefix}-{user.name}".lower()[:100]
        existing = discord.utils.get(guild.text_channels, name=name)
        if existing:
            return await interaction.response.send_message(f"❌ Ticket đang mở: {existing.mention}", ephemeral=True)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
        }
        if staff:
            overwrites[staff] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True)
        try:
            channel = await guild.create_text_channel(name=name, category=category, overwrites=overwrites)
            await channel.send(content=f"{user.mention} {staff.mention if staff else ''}", embed=embed(f"📩 TICKET: {ticket_type.upper()}", "Đội ngũ hỗ trợ sẽ phản hồi bạn sớm nhất!", 0x5865F2), view=CloseTicketView())
            await interaction.response.send_message(f"✅ Đã tạo ticket: {channel.mention}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot thiếu quyền tạo kênh.", ephemeral=True)

    @discord.ui.button(label="🛠️ Hỗ Trợ Kỹ Thuật", style=discord.ButtonStyle.primary, custom_id="ticket_support")
    async def support(self, interaction, button):
        await self.create_ticket(interaction, "Hỗ Trợ Kỹ Thuật", "ky-thuat")

    @discord.ui.button(label="📢 Tố Cáo / Góp Ý", style=discord.ButtonStyle.danger, custom_id="ticket_report")
    async def report(self, interaction, button):
        await self.create_ticket(interaction, "Tố Cáo / Góp Ý", "to-cao")


class RoleView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✨ Nhận / Hủy Role", style=discord.ButtonStyle.success, custom_id="toggle_self_role")
    async def toggle(self, interaction, button):
        role = interaction.guild.get_role(config.get("self_role_id")) if config.get("self_role_id") else None
        if not role:
            return await interaction.response.send_message("❌ Self-role chưa được cấu hình.", ephemeral=True)
        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role)
                message = f"Đã gỡ {role.mention}."
            else:
                await interaction.user.add_roles(role)
                message = f"Đã nhận {role.mention}."
            await interaction.response.send_message(message, ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot không đủ quyền quản lý role này.", ephemeral=True)


class UnlockView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔓 Xác Nhận / Mở Khóa Kênh", style=discord.ButtonStyle.success, custom_id="unlock_channels_button")
    async def unlock(self, interaction, button):
        role = interaction.guild.get_role(config.get("unlock_role_id")) if config.get("unlock_role_id") else None
        if not role:
            return await interaction.response.send_message("❌ Role mở khóa chưa được cấu hình.", ephemeral=True)
        try:
            if role not in interaction.user.roles:
                await interaction.user.add_roles(role)
            await interaction.response.send_message(f"✅ Đã mở khóa bằng role {role.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot không đủ quyền trao role.", ephemeral=True)


class OnboardingModal(Modal, title="📝 Khảo Sát Thành Viên Mới"):
    fullname = TextInput(label="Họ và tên", max_length=50)
    hobby = TextInput(label="Sở thích / Mục đích tham gia", style=discord.TextStyle.paragraph, max_length=300)

    async def on_submit(self, interaction):
        await interaction.response.send_message(embed=embed("🎉 HOÀN TẤT ĐĂNG KÝ", f"Cảm ơn bạn **{self.fullname.value}**!\n\n📌 **Thông tin:** {self.hobby.value}", 0x57F287), ephemeral=True)


class OnboardingView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🚀 Bắt đầu khai báo thông tin", style=discord.ButtonStyle.success, custom_id="start_onboarding_modal")
    async def open_modal(self, interaction, button):
        await interaction.response.send_modal(OnboardingModal())


class ConfigModal(Modal):
    def __init__(self, key):
        super().__init__(title=f"⚙️ Cài đặt {key}")
        self.key = key
        self.value = TextInput(label="Nhập ID", default=str(config.get(key) or ""), required=False, max_length=20)
        self.add_item(self.value)

    async def on_submit(self, interaction):
        raw = self.value.value.strip()
        if raw and not raw.isdigit():
            return await interaction.response.send_message("❌ ID phải là số.", ephemeral=True)
        config[self.key] = int(raw) if raw else None
        save_json(CONFIG_FILE, config)
        await interaction.response.send_message("✅ Đã lưu cấu hình.", ephemeral=True)


class SetupSelect(Select):
    def __init__(self):
        keys = [key for key in DEFAULT_CONFIG if key.endswith("_id")]
        super().__init__(placeholder="⚙️ Chọn mục muốn cài đặt...", custom_id="setup_config_select", options=[discord.SelectOption(label=key, value=key) for key in keys])

    async def callback(self, interaction):
        await interaction.response.send_modal(ConfigModal(self.values[0]))


class SetupView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SetupSelect())


@bot.event
async def on_ready():
    global views_registered
    if not views_registered:
        for persistent_view in (TicketPanelView(), CloseTicketView(), RoleView(), OnboardingView(), UnlockView(), SetupView()):
            bot.add_view(persistent_view)
        views_registered = True
    if not check_birthdays.is_running():
        check_birthdays.start()
    synced = await bot.tree.sync()
    print(f"✅ Bot sẵn sàng: {bot.user} | Đồng bộ {len(synced)} lệnh")


@tasks.loop(hours=24)
async def check_birthdays():
    day = datetime.now().strftime("%d/%m")
    for guild in bot.guilds:
        channel = get_channel(guild, "birthday_channel_id")
        if not channel:
            continue
        for user_id, birthday in birthdays_data.items():
            if birthday.startswith(day):
                member = guild.get_member(int(user_id))
                if member:
                    await channel.send(content=f"🎂 Chúc mừng sinh nhật {member.mention}!", embed=embed("🎉 CHÚC MỪNG SINH NHẬT!", "Chúc bạn một ngày thật vui vẻ và hạnh phúc! 🎈", 0xFF73FA))


@check_birthdays.before_loop
async def before_birthdays():
    await bot.wait_until_ready()


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.guild:
        record_chat_activity(message)
    if bot.user in message.mentions:
        try:
            guild = bot.get_guild(TARGET_GUILD_ID)
            if guild:
                emojis = list(guild.emojis)
                if not emojis:
                    emojis = await guild.fetch_emojis()
                if emojis:
                    await message.add_reaction(random.choice(emojis))
        except (discord.HTTPException, discord.Forbidden):
            pass
    await bot.process_commands(message)


@bot.event
async def on_member_join(member):
    channel = get_channel(member.guild, "welcome_channel_id")
    if not channel:
        return
    text = config["welcome_message"].format(user=member.mention, server=member.guild.name, name=member.name, count=len(member.guild.members))
    message = embed("✨ CHÀO MỪNG THÀNH VIÊN MỚI", text)
    if config.get("welcome_image", "").startswith(("http://", "https://")):
        message.set_image(url=config["welcome_image"])
    await channel.send(embed=message)


@bot.event
async def on_member_remove(member):
    channel = get_channel(member.guild, "goodbye_channel_id")
    if not channel:
        return
    text = config["goodbye_message"].format(user=member.mention, server=member.guild.name, name=member.name, count=len(member.guild.members))
    message = embed("🚪 RỜI MÁY CHỦ", text)
    if config.get("goodbye_image", "").startswith(("http://", "https://")):
        message.set_image(url=config["goodbye_image"])
    await channel.send(embed=message)


@bot.tree.command(name="setup", description="Bảng cài đặt cấu hình Bot")
@app_commands.checks.has_permissions(administrator=True)
async def setup_command(interaction):
    values = "\n".join(f"`{key}`: `{config.get(key) or 'Chưa cài'}`" for key in DEFAULT_CONFIG if key.endswith("_id"))
    await interaction.response.send_message(embed=embed("⚙️ BẢNG CẤU HÌNH BOT", values), view=SetupView(), ephemeral=True)


@bot.tree.command(name="setup_birthday", description="Cài kênh gửi thông báo sinh nhật")
@app_commands.describe(channel="Kênh sẽ nhận thông báo sinh nhật")
@app_commands.checks.has_permissions(administrator=True)
async def setup_birthday(interaction: discord.Interaction, channel: discord.TextChannel):
    config["birthday_channel_id"] = channel.id
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(
        f"✅ Đã cài kênh sinh nhật: {channel.mention}",
        ephemeral=True,
    )


@bot.tree.command(name="weekly_chatters", description="Xem những người chat nhiều nhất trong tuần")
@app_commands.describe(limit="Số người muốn hiển thị, từ 1 đến 20")
async def weekly_chatters(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 20] = 10):
    if not interaction.guild:
        return await interaction.response.send_message("❌ Lệnh này chỉ dùng trong server.", ephemeral=True)

    week_info = datetime.now().isocalendar()
    week_key = f"{week_info.year}-W{week_info.week:02d}"
    week_data = chat_activity.get(str(interaction.guild.id), {}).get(week_key, {})
    ranked = sorted(week_data.items(), key=lambda item: item[1].get("messages", 0), reverse=True)[:limit]

    if not ranked:
        return await interaction.response.send_message("📊 Chưa có dữ liệu chat trong tuần này.", ephemeral=True)

    lines = [
        f"**{index}.** <@{user_id}> — `{data.get('messages', 0):,}` tin nhắn"
        for index, (user_id, data) in enumerate(ranked, start=1)
    ]
    message = f"📊 **NHỮNG NGƯỜI CHAT NHIỀU TRONG TUẦN ({week_key})**\n\n" + "\n".join(lines)
    await interaction.response.send_message(embed=embed("📊 THỐNG KÊ CHAT TUẦN", message, 0x5865F2))


@bot.tree.command(name="kick_inactive", description="Kick thành viên không chat trong số ngày")
@app_commands.describe(days="Số ngày không chat, mặc định 7 ngày")
@app_commands.checks.has_permissions(kick_members=True)
async def kick_inactive(interaction: discord.Interaction, days: app_commands.Range[int, 1, 365] = 7):
    if not interaction.guild:
        return await interaction.response.send_message("❌ Lệnh này chỉ dùng trong server.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    guild_data = chat_activity.get(str(interaction.guild.id), {})
    candidates = []
    skipped = 0

    for member in interaction.guild.members:
        if member.bot or member == interaction.guild.owner or member.guild_permissions.administrator:
            continue
        if member.joined_at and member.joined_at > cutoff:
            continue

        last_message = None
        for week_data in guild_data.values():
            activity = week_data.get(str(member.id))
            if activity and activity.get("last_message"):
                try:
                    timestamp = datetime.fromisoformat(activity["last_message"])
                    if last_message is None or timestamp > last_message:
                        last_message = timestamp
                except ValueError:
                    continue

        if last_message is None or last_message < cutoff:
            candidates.append(member)

    kicked = []
    for member in candidates:
        if member.top_role >= interaction.guild.me.top_role:
            skipped += 1
            continue
        try:
            await member.kick(reason=f"Không hoạt động trong {days} ngày")
            kicked.append(member.display_name)
        except discord.Forbidden:
            skipped += 1

    summary = f"✅ Đã kick **{len(kicked)}** thành viên không chat trong **{days} ngày**."
    if skipped:
        summary += f"\n⚠️ Bỏ qua **{skipped}** thành viên do thiếu quyền hoặc role cao hơn bot."
    if kicked:
        summary += "\n\n" + "\n".join(f"• {name}" for name in kicked[:20])
        if len(kicked) > 20:
            summary += f"\n• ... và {len(kicked) - 20} người khác"
    await interaction.followup.send(embed=embed("👢 KICK THÀNH VIÊN KHÔNG HOẠT ĐỘNG", summary, 0xED4245), ephemeral=True)


@bot.tree.command(name="send_ticket_panel", description="Gửi bảng Ticket")
@app_commands.checks.has_permissions(administrator=True)
async def send_ticket_panel(interaction):
    await interaction.channel.send(embed=embed("🎫 TRUNG TÂM HỖ TRỢ", "Bấm nút bên dưới để mở kênh liên hệ riêng."), view=TicketPanelView())
    await interaction.response.send_message("✅ Đã gửi bảng Ticket.", ephemeral=True)


@bot.tree.command(name="send_role_panel", description="Gửi bảng Self-Role")
@app_commands.checks.has_permissions(administrator=True)
async def send_role_panel(interaction):
    await interaction.channel.send(embed=embed("🎭 TỰ NHẬN VAI TRÒ", "Bấm nút để nhận hoặc hủy role."), view=RoleView())
    await interaction.response.send_message("✅ Đã gửi bảng Role.", ephemeral=True)


@bot.tree.command(name="send_onboarding", description="Gửi bảng Onboarding")
@app_commands.checks.has_permissions(administrator=True)
async def send_onboarding(interaction):
    await interaction.channel.send(embed=embed("🌟 KHẢO SÁT THÀNH VIÊN", "Bấm nút bên dưới để điền thông tin."), view=OnboardingView())
    await interaction.response.send_message("✅ Đã gửi bảng Onboarding.", ephemeral=True)


@bot.tree.command(name="send_unlock_panel", description="Gửi bảng mở khóa kênh")
@app_commands.checks.has_permissions(administrator=True)
async def send_unlock_panel(interaction):
    await interaction.channel.send(embed=embed("📜 MỞ KHÓA KÊNH HỆ THỐNG", "Bấm nút để xác nhận nội quy."), view=UnlockView())
    await interaction.response.send_message("✅ Đã gửi bảng mở khóa.", ephemeral=True)


@bot.tree.command(name="birthday", description="Đăng ký sinh nhật")
@app_commands.describe(date="Định dạng DD/MM/YYYY")
async def birthday(interaction, date: str):
    try:
        parsed = datetime.strptime(date.strip(), "%d/%m/%Y")
    except ValueError:
        return await interaction.response.send_message("❌ Dùng đúng định dạng DD/MM/YYYY.", ephemeral=True)
    birthdays_data[str(interaction.user.id)] = parsed.strftime("%d/%m/%Y")
    save_json(BIRTHDAY_FILE, birthdays_data)
    await interaction.response.send_message(f"✅ Đã lưu sinh nhật: **{parsed.strftime('%d/%m/%Y')}**", ephemeral=True)


@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="Không có lý do"):
    await member.ban(reason=reason)
    await ctx.send(embed=embed("🔨 ĐÃ BAN THÀNH VIÊN", f"Đã cấm **{member}**.\n📌 **Lý do:** {reason}", 0xED4245))


@bot.command(name="mute")
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, minutes: int, *, reason="Không có lý do"):
    if minutes <= 0:
        return await ctx.send("❌ Số phút phải lớn hơn 0.")
    await member.timeout(datetime.now().astimezone() + timedelta(minutes=minutes), reason=reason)
    await ctx.send(embed=embed("🔇 ĐÃ KHÓA CHAT", f"Đã timeout **{member}** trong **{minutes} phút**.\n📌 **Lý do:** {reason}", 0xFEE75C))


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Bạn không có đủ quyền.", delete_after=5)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Thiếu thông tin bắt buộc.", delete_after=5)
    elif not isinstance(error, commands.CommandNotFound):
        await ctx.send(f"❌ Lỗi: {error}", delete_after=5)


bot.run(TOKEN)