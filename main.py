import asyncio
import os
import random
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, ChannelSelect, Modal, Select, TextInput, View
from dotenv import load_dotenv
from flask import Flask
from threading import Thread
import wavelink

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "bot_database.db"
TARGET_GUILD_ID = 1503922700408586240

env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(env_path)
    print("✅ Đã load file .env từ máy cục bộ.")
else:
    print("ℹ️ Không tìm thấy file .env, bot sẽ sử dụng biến môi trường từ hệ thống Cloud.")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS birthdays (
            user_id TEXT PRIMARY KEY,
            birthday TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_activity (
            guild_id TEXT,
            week_key TEXT,
            user_id TEXT,
            name TEXT,
            messages INTEGER,
            PRIMARY KEY (guild_id, week_key, user_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mc_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command TEXT,
            args TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mc_config (
            user_id TEXT PRIMARY KEY,
            host TEXT,
            port INTEGER,
            username TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

DEFAULT_CONFIG = {
    "welcome_channel_id": None, 
    "welcome_image": "",
    "welcome_message": "Chào mừng {user} đã gia nhập **{server}**!\n\n▫️ **Tài khoản:** {name}\n▫️ **Thành viên thứ:** `{count}`",
    "goodbye_channel_id": None, 
    "goodbye_image": "",
    "goodbye_message": "Tài khoản **{name}** đã rời khỏi cộng đồng.\nHiện tại máy chủ còn lại `{count}` thành viên.",
    "ticket_category_id": None, "staff_role_id": None, "self_role_id": None,
    "birthday_channel_id": None, "unlock_role_id": None,
}

def get_config(key):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM config WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return DEFAULT_CONFIG.get(key)
    val = row[0]
    if val == "None" or val == "" or val is None:
        return None
    if key.endswith("_id") and str(val).isdigit():
        return int(val)
    return val

def set_config(key, value):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value) if value is not None else ""))
    conn.commit()
    conn.close()

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Thiếu DISCORD_TOKEN trong biến môi trường hoặc file .env")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
views_registered = False

def embed(title, description, color=0x2B2D31):
    result = discord.Embed(title=f"『 {title} 』", description=description, color=color)
    result.set_footer(text="Hệ thống quản lý Bot | ph.huyy")
    result.timestamp = datetime.now()
    return result

def get_channel(guild, key):
    value = get_config(key)
    return guild.get_channel(value) if value else None

def record_chat_activity(message):
    week_info = datetime.now().isocalendar()
    week_key = f"{week_info.year}-W{week_info.week:02d}"
    guild_id = str(message.guild.id)
    user_id = str(message.author.id)
    name = message.author.display_name

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO chat_activity (guild_id, week_key, user_id, name, messages)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(guild_id, week_key, user_id) 
        DO UPDATE SET messages = messages + 1, name = ?
    """, (guild_id, week_key, user_id, name, name))
    conn.commit()
    conn.close()

async def connect_nodes():
    await bot.wait_until_ready()
    try:
        node = wavelink.Node(uri="http://lavalink.darrennathanael.com:80", password="youshallnotpass")
        await wavelink.Pool.connect(nodes=[node], client=bot)
    except Exception as e:
        print(f"⚠️ Không thể kết nối Wavelink Node: {e}")

class CloseTicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Đóng Ticket", style=discord.ButtonStyle.secondary, custom_id="close_ticket")
    async def close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(embed=embed("XÁC NHẬN ĐÓNG TICKET", "Kênh sẽ tự động xóa sau **5 giây**...", 0xFEE75C))
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
        cat_id = get_config("ticket_category_id")
        staff_id = get_config("staff_role_id")
        
        category = guild.get_channel(cat_id) if cat_id else None
        staff = guild.get_role(staff_id) if staff_id else None
        
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
            await channel.send(content=f"{user.mention} {staff.mention if staff else ''}", embed=embed(f"TICKET: {ticket_type.upper()}", "Đội ngũ hỗ trợ sẽ phản hồi bạn sớm nhất!", 0x5865F2), view=CloseTicketView())
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
        role_id = get_config("self_role_id")
        role = interaction.guild.get_role(role_id) if role_id else None
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
        role_id = get_config("unlock_role_id")
        role = interaction.guild.get_role(role_id) if role_id else None
        if not role:
            return await interaction.response.send_message("❌ Role mở khóa chưa được cấu hình.", ephemeral=True)
        try:
            if role not in interaction.user.roles:
                await interaction.user.add_roles(role)
            await interaction.response.send_message(f"✅ Đã mở khóa bằng role {role.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot không đủ quyền trao role.", ephemeral=True)

class WelcomeModal(Modal, title="⚙️ Cài đặt Chào Mừng (Welcome)"):
    msg_input = TextInput(label="Nội dung tin nhắn", style=discord.TextStyle.paragraph, default=get_config("welcome_message") or "", required=True, max_length=1000)
    img_input = TextInput(label="URL Hình ảnh", default=get_config("welcome_image") or "", required=False, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        set_config("welcome_message", self.msg_input.value.strip())
        set_config("welcome_image", self.img_input.value.strip())
        await interaction.response.send_message("✅ Đã cập nhật thành công nội dung chào mừng!", ephemeral=True)

class GoodbyeModal(Modal, title="⚙️ Cài đặt Tạm Biệt (Goodbye)"):
    msg_input = TextInput(label="Nội dung tin nhắn", style=discord.TextStyle.paragraph, default=get_config("goodbye_message") or "", required=True, max_length=1000)
    img_input = TextInput(label="URL Hình ảnh", default=get_config("goodbye_image") or "", required=False, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        set_config("goodbye_message", self.msg_input.value.strip())
        set_config("goodbye_image", self.img_input.value.strip())
        await interaction.response.send_message("✅ Đã cập nhật thành công nội dung tạm biệt!", ephemeral=True)

class MinecraftConfigModal(Modal, title="⚙️ Cài đặt Bot Minecraft"):
    host_input = TextInput(label="IP Server Minecraft", placeholder="Ví dụ: play.myserver.com hoặc IP", required=True, max_length=100)
    port_input = TextInput(label="Port Server", placeholder="Mặc định là 25565", default="25565", required=True, max_length=5)
    name_input = TextInput(label="Tên nhân vật (Username) của Bot", placeholder="Ví dụ: MyMinecraftBot", required=True, max_length=16)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        host = self.host_input.value.strip()
        port_str = self.port_input.value.strip()
        username = self.name_input.value.strip()
        port = int(port_str) if port_str.isdigit() else 25565

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO mc_config (user_id, host, port, username) 
            VALUES (?, ?, ?, ?)
        """, (user_id, host, port, username))
        conn.commit()
        conn.close()

        await interaction.response.send_message(
            f"✅ Đã lưu cấu hình server!\n- **IP:** `{host}:{port}`\n- **Tên Bot:** `{username}`", 
            ephemeral=True
        )

class SetupChannelSelect(ChannelSelect):
    def __init__(self, key):
        self.key = key
        super().__init__(placeholder=f"📂 Chọn kênh cho {key}...", min_values=1, max_values=1, channel_types=[discord.ChannelType.text], custom_id=f"select_channel_{key}")

    async def callback(self, interaction):
        selected_channel = self.values[0]
        set_config(self.key, selected_channel.id)
        await interaction.response.edit_message(content=f"✅ Đã cài đặt **`{self.key}`** vào kênh {selected_channel.mention}!", view=None, embed=None)

class SetupChannelView(View):
    def __init__(self, key):
        super().__init__(timeout=60)
        self.add_item(SetupChannelSelect(key))

class SetupSelect(Select):
    def __init__(self):
        keys = [key for key in DEFAULT_CONFIG if key.endswith("_id")]
        options = [discord.SelectOption(label=key, value=key, description=f"Cài đặt cho {key}") for key in keys]
        super().__init__(placeholder="⚙️ Chọn mục cần cấu hình ngay...", custom_id="setup_config_select", options=options)

    async def callback(self, interaction):
        selected_key = self.values[0]
        if "channel" in selected_key:
            view = SetupChannelView(selected_key)
            await interaction.response.edit_message(content=f"📂 Vui lòng chọn kênh cho **`{selected_key}`**:", embed=None, view=view)
        else:
            await interaction.response.send_modal(ConfigModal(selected_key))

class ConfigModal(Modal):
    def __init__(self, key):
        super().__init__(title=f"⚙️ Cài đặt {key}")
        self.key = key
        self.value = TextInput(label="Nhập ID", default=str(get_config(key) or ""), required=False, max_length=20)
        self.add_item(self.value)

    async def on_submit(self, interaction):
        raw = self.value.value.strip()
        if raw and not raw.isdigit():
            return await interaction.response.send_message("❌ ID phải là số.", ephemeral=True)
        set_config(self.key, int(raw) if raw else None)
        await interaction.response.send_message("✅ Đã lưu cấu hình thành công.", ephemeral=True)

class SetupView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SetupSelect())

@bot.event
async def on_ready():
    global views_registered
    if not views_registered:
        for persistent_view in (TicketPanelView(), CloseTicketView(), RoleView(), UnlockView(), SetupView()):
            bot.add_view(persistent_view)
        views_registered = True
        
    if not check_birthdays.is_running():
        check_birthdays.start()
    
    bot.loop.create_task(connect_nodes())
    
    try:
        synced = await bot.tree.sync()
        print(f"✅ Bot sẵn sàng: {bot.user} | Đồng bộ {len(synced)} lệnh")
    except Exception as e:
        print(f"⚠️ Lỗi đồng bộ lệnh: {e}")

@tasks.loop(hours=24)
async def check_birthdays():
    day = datetime.now().strftime("%d/%m")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, birthday FROM birthdays")
    rows = cursor.fetchall()
    conn.close()

    for guild in bot.guilds:
        channel = get_channel(guild, "birthday_channel_id")
        if not channel:
            continue
        for user_id, birthday in rows:
            if birthday.startswith(day):
                member = guild.get_member(int(user_id))
                if member:
                    await channel.send(content=f"🎂 Chúc mừng sinh nhật {member.mention}!", embed=embed("CHÚC MỪNG SINH NHẬT!", "Chúc bạn một ngày thật vui vẻ và hạnh phúc! 🎈", 0xFF73FA))

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
    template = get_config("welcome_message") or DEFAULT_CONFIG["welcome_message"]
    text = template.format(user=member.mention, server=member.guild.name, name=member.name, count=len(member.guild.members))
    message = embed("CHÀO MỪNG THÀNH VIÊN MỚI", text, 0x57F287)
    img_url = get_config("welcome_image")
    if img_url and img_url.startswith(("http://", "https://")):
        message.set_image(url=img_url)
    if member.display_avatar:
        message.set_thumbnail(url=member.display_avatar.url)
    await channel.send(embed=message)

@bot.event
async def on_member_remove(member):
    channel = get_channel(member.guild, "goodbye_channel_id")
    if not channel:
        return
    template = get_config("goodbye_message") or DEFAULT_CONFIG["goodbye_message"]
    text = template.format(user=member.mention, server=member.guild.name, name=member.name, count=len(member.guild.members))
    message = embed("RỜI MÁY CHỦ", text, 0xED4245)
    img_url = get_config("goodbye_image")
    if img_url and img_url.startswith(("http://", "https://")):
        message.set_image(url=img_url)
    await channel.send(embed=message)

@bot.tree.command(name="setup", description="Bảng cài đặt cấu hình Bot")
@app_commands.checks.has_permissions(administrator=True)
async def setup_command(interaction):
    values = "\n".join(f"`{key}`: `{get_config(key) or 'Chưa cài'}`" for key in DEFAULT_CONFIG if key.endswith("_id"))
    await interaction.response.send_message(embed=embed("BẢNG CẤU HÌNH BOT", values), view=SetupView(), ephemeral=True)

@bot.tree.command(name="setwelcome", description="Chỉnh sửa nội dung và URL ảnh chào mừng")
@app_commands.checks.has_permissions(administrator=True)
async def setwelcome(interaction: discord.Interaction):
    await interaction.response.send_modal(WelcomeModal())

@bot.tree.command(name="setgoodbye", description="Chỉnh sửa nội dung và URL ảnh tạm biệt")
@app_commands.checks.has_permissions(administrator=True)
async def setgoodbye(interaction: discord.Interaction):
    await interaction.response.send_modal(GoodbyeModal())

@bot.tree.command(name="setmc", description="Cài đặt thông tin server Minecraft của bạn cho bot")
async def setmc(interaction: discord.Interaction):
    await interaction.response.send_modal(MinecraftConfigModal())

@bot.tree.command(name="weekly_chatters", description="Xem những người chat nhiều nhất trong tuần")
@app_commands.describe(limit="Số người muốn hiển thị, từ 1 đến 20")
async def weekly_chatters(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 20] = 10):
    if not interaction.guild:
        return await interaction.response.send_message("❌ Lệnh này chỉ dùng trong server.", ephemeral=True)

    week_info = datetime.now().isocalendar()
    week_key = f"{week_info.year}-W{week_info.week:02d}"
    guild_id = str(interaction.guild.id)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, messages FROM chat_activity 
        WHERE guild_id = ? AND week_key = ? 
        ORDER BY messages DESC LIMIT ?
    """, (guild_id, week_key, limit))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return await interaction.response.send_message("📊 Chưa có dữ liệu chat trong tuần này.", ephemeral=True)

    lines = [f"**{index}.** <@{user_id}> — `{msgs:,}` tin nhắn" for index, (user_id, msgs) in enumerate(rows, start=1)]
    message = f"📊 **THỐNG KÊ TUẦN ({week_key})**\n\n" + "\n".join(lines)
    await interaction.response.send_message(embed=embed("THỐNG KÊ CHAT TUẦN", message, 0x5865F2))

@bot.tree.command(name="birthday", description="Đăng ký sinh nhật")
@app_commands.describe(date="Định dạng DD/MM/YYYY")
async def birthday(interaction, date: str):
    try:
        parsed = datetime.strptime(date.strip(), "%d/%m/%Y")
    except ValueError:
        return await interaction.response.send_message("❌ Dùng đúng định dạng DD/MM/YYYY.", ephemeral=True)
    
    user_id = str(interaction.user.id)
    formatted_date = parsed.strftime("%d/%m/%Y")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO birthdays (user_id, birthday) VALUES (?, ?)", (user_id, formatted_date))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ Đã lưu sinh nhật: **{formatted_date}**", ephemeral=True)

@bot.tree.command(name="mc", description="Điều khiển bot Minecraft từ Discord")
@app_commands.describe(action="Hành động", message="Nội dung chat (nếu có)")
@app_commands.choices(action=[
    app_commands.Choice(name="chat", value="chat"),
    app_commands.Choice(name="come", value="come"),
    app_commands.Choice(name="stop", value="stop"),
    app_commands.Choice(name="status", value="status"),
    app_commands.Choice(name="eat", value="eat")
])
async def mc_command(interaction: discord.Interaction, action: str, message: str = ""):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO mc_commands (command, args) VALUES (?, ?)", (action, message))
    conn.commit()
    conn.close()
    
    await interaction.response.send_message(f"✅ Đã gửi lệnh `!{action} {message}` xuống bot Minecraft!", ephemeral=True)

@bot.tree.command(name="play", description="Phát nhạc từ YouTube hoặc Spotify")
@app_commands.describe(search="Tên bài hát hoặc Link")
async def play(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        return await interaction.response.send_message("❌ Bạn cần vào phòng Voice trước!", ephemeral=True)

    await interaction.response.defer()
    vc: wavelink.Player = interaction.guild.voice_client
    if not vc:
        try:
            vc = await interaction.user.voice.channel.connect(cls=wavelink.Player)
        except discord.ClientException:
            return await interaction.followup.send("❌ Bot không thể kết nối vào phòng Voice.")

    tracks = await wavelink.Playable.search(search)
    if not tracks:
        return await interaction.followup.send("❌ Không tìm thấy bài hát nào!")

    if isinstance(tracks, wavelink.Playlist):
        added = await vc.queue.put_wait(tracks)
        await interaction.followup.send(f"🎵 Đã thêm playlist **{tracks.name}** (`{added}` bài) vào hàng đợi.")
    else:
        track = tracks[0]
        await vc.queue.put_wait(track)
        await interaction.followup.send(f"🎵 Đã thêm vào hàng đợi: **{track.title}** (`{track.author}`)")

    if not vc.playing:
        await vc.play(vc.queue.get())

@bot.tree.command(name="skip", description="Bỏ qua bài hát hiện tại")
async def skip(interaction: discord.Interaction):
    vc: wavelink.Player = interaction.guild.voice_client
    if not vc or not vc.playing:
        return await interaction.response.send_message("❌ Không có bài hát nào đang phát.", ephemeral=True)
    await vc.stop()
    await interaction.response.send_message("⏭️ Đã bỏ qua bài hát hiện tại!")

@bot.tree.command(name="stop", description="Dừng nhạc và thoát Voice")
async def stop(interaction: discord.Interaction):
    vc: wavelink.Player = interaction.guild.voice_client
    if not vc:
        return await interaction.response.send_message("❌ Bot không ở trong phòng Voice.", ephemeral=True)
    await vc.disconnect()
    await interaction.response.send_message("⏹️ Đã dừng nhạc và ngắt kết nối!")

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot System is running 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def run_minecraft_bot():
    if os.path.exists("bot.js"):
        try:
            print("⛏️ Đang khởi động bot Minecraft (Node.js)...")
            subprocess.Popen(["node", "bot.js"])
        except Exception as e:
            print(f"⚠️ Không thể khởi chạy tiến trình Node.js: {e}")
    else:
        print("⚠️ Không tìm thấy file bot.js, bỏ qua khởi động Minecraft bot.")

if __name__ == "__main__":
    t = Thread(target=run_web, daemon=True)
    t.start()
    
    run_minecraft_bot()

    bot.run(TOKEN)
