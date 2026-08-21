import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path
import random
import string
import sqlite3
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, Modal, RoleSelect, Select, TextInput, View
from flask import Flask

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "bot_database.db"
TARGET_GUILD_ID = 1503922700408586240

# Lấy Token từ biến môi trường trên Render
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Thiếu DISCORD_TOKEN trong biến môi trường của Render!")


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
        CREATE TABLE IF NOT EXISTS user_birthdays (
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
    conn.commit()
    conn.close()


init_db()

DEFAULT_CONFIG = {
    "welcome_channel_id": None,
    "welcome_image": (
        "https://images.unsplash.com/photo-1578632767115-351597cf2477?q=80&w=1000&auto=format&fit=crop"
    ),
    "welcome_message": (
        "Chào mừng {user} đã gia nhập **{server}**!\n\n▫️ **Tài khoản:** {name}\n▫️ **Thành viên thứ:** `{count}`"
    ),
    "ticket_category_id": None,
    "staff_role_id": None,
    "self_role_id": None,
    "birthday_channel_id": None,
    "unlock_role_id": None,
    "verify_channel_id": None,
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
    cursor.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
        (key, str(value) if value is not None else ""),
    )
    conn.commit()
    conn.close()


def get_user_birthday(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT birthday FROM user_birthdays WHERE user_id = ?", (str(user_id),))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


# --- HỆ THỐNG XÁC THỰC BẢO MẬT ---

def generate_random_code(length=6):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


temp_verify_codes = {}


class UnifiedVerifyModal(Modal):
    def __init__(self, needs_birthday: bool, code: str):
        super().__init__(title="🔐 Xác Thực & Đăng Ký Bảo Mật")
        self.needs_birthday = needs_birthday
        self.code = code

        if self.needs_birthday:
            self.date_input = TextInput(
                label="1. Ngày tháng năm sinh của bạn",
                placeholder="Định dạng: DD/MM/YYYY (Ví dụ: 15/08/2008)",
                style=discord.TextStyle.short,
                required=True,
                max_length=15,
            )
            self.add_item(self.date_input)

        self.code_input = TextInput(
            label=f"Mã xác thực của bạn là: [{code}]",
            placeholder="Nhập lại chính xác mã phía trên vào đây...",
            style=discord.TextStyle.short,
            required=True,
            max_length=10,
        )
        self.add_item(self.code_input)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        entered_code = self.code_input.value.strip().upper()
        correct_code = temp_verify_codes.get(user_id)

        if not correct_code or entered_code != correct_code:
            return await interaction.response.send_message(
                "❌ Mã xác thực không chính xác hoặc đã hết hạn! Vui lòng bấm lại nút Verify để nhận mã mới.",
                ephemeral=True,
            )

        # Nếu người dùng chưa có ngày sinh thì xử lý lưu ngày sinh luôn tại đây
        if self.needs_birthday:
            date_str = self.date_input.value.strip()
            try:
                parsed = datetime.strptime(date_str, "%d/%m/%Y")
            except ValueError:
                return await interaction.response.send_message(
                    "❌ Sai định dạng ngày sinh! Vui lòng nhập đúng mẫu: `DD/MM/YYYY` (Ví dụ: 25/12/2008).",
                    ephemeral=True,
                )
            
            formatted_date = parsed.strftime("%d/%m/%Y")
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO user_birthdays (user_id, birthday) VALUES (?, ?)",
                (str(user_id), formatted_date),
            )
            conn.commit()
            conn.close()

        # Kiểm tra và cấp role mở khóa
        role_id = get_config("unlock_role_id")
        role = interaction.guild.get_role(role_id) if role_id else None
        if not role:
            return await interaction.response.send_message(
                "❌ Role mở khóa chưa được cấu hình trên hệ thống bởi Admin.",
                ephemeral=True,
            )

        try:
            if role not in interaction.user.roles:
                await interaction.user.add_roles(role)
            
            await interaction.response.send_message(
                f"✅ Xác thực thành công! Đã cấp role {role.mention} và mở khóa toàn bộ kênh cho bạn.",
                ephemeral=True,
            )
            temp_verify_codes.pop(user_id, None)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Bot không đủ quyền trao role này.", ephemeral=True
            )


class BirthdayModal(Modal, title="🎂 Đăng Ký Ngày Sinh Bảo Mật"):
    date_input = TextInput(
        label="Nhập ngày tháng năm sinh của bạn",
        placeholder="Định dạng: DD/MM/YYYY (Ví dụ: 15/08/2008)",
        style=discord.TextStyle.short,
        required=True,
        max_length=15,
    )

    async def on_submit(self, interaction: discord.Interaction):
        date_str = self.date_input.value.strip()
        try:
            parsed = datetime.strptime(date_str, "%d/%m/%Y")
        except ValueError:
            return await interaction.response.send_message(
                "❌ Sai định dạng! Vui lòng nhập đúng mẫu: `DD/MM/YYYY` (Ví dụ: 25/12/2008).",
                ephemeral=True,
            )

        user_id = str(interaction.user.id)
        formatted_date = parsed.strftime("%d/%m/%Y")

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO user_birthdays (user_id, birthday) VALUES (?, ?)",
            (user_id, formatted_date),
        )
        conn.commit()
        conn.close()

        await interaction.response.send_message(
            "✅ Đã lưu ngày sinh thành công!", ephemeral=True
        )


class VerifyButtonView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔐 Xác Thực Ngay",
        style=discord.ButtonStyle.success,
        custom_id="persistent_verify_button"
    )
    async def verify_button_callback(self, interaction: discord.Interaction, button: Button):
        role_id = get_config("unlock_role_id")
        role = interaction.guild.get_role(role_id) if role_id else None
        
        if role and role in interaction.user.roles:
            return await interaction.response.send_message(
                "❌ Bạn đã được xác thực từ trước rồi!", ephemeral=True
            )

        saved_birthday = get_user_birthday(interaction.user.id)
        new_code = generate_random_code()
        temp_verify_codes[interaction.user.id] = new_code

        # Nếu chưa có ngày sinh, hiển thị modal tích hợp cả 2 ô (Ngày sinh + Mã xác thực)
        if not saved_birthday:
            return await interaction.response.send_modal(UnifiedVerifyModal(needs_birthday=True, code=new_code))
        
        # Nếu đã có ngày sinh từ trước, chỉ hiển thị modal nhập mã xác thực
        return await interaction.response.send_modal(UnifiedVerifyModal(needs_birthday=False, code=new_code))


class SetupVerifyRoleSelect(RoleSelect):
    def __init__(self):
        super().__init__(
            placeholder="📂 Chọn role cấp khi Verify...",
            min_values=1,
            max_values=1,
            custom_id="setup_verify_role_select",
        )

    async def callback(self, interaction: discord.Interaction):
        selected_role = self.values[0]
        set_config("unlock_role_id", selected_role.id)
        await interaction.response.edit_message(
            content=f"✅ Đã thiết lập thành công role xác thực là: {selected_role.mention}!",
            view=None,
            embed=None,
        )


class SetupVerifyView(View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(SetupVerifyRoleSelect())


# --- KHỞI TẠO BOT & TIỆN ÍCH CHUNG ---

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def embed(title, description, color=0x5865F2):
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
    cursor.execute(
        """
        INSERT INTO chat_activity (guild_id, week_key, user_id, name, messages)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(guild_id, week_key, user_id) 
        DO UPDATE SET messages = messages + 1, name = ?
    """,
        (guild_id, week_key, user_id, name, name),
    )
    conn.commit()
    conn.close()


# --- TICKET & ROLE VIEWS ---

class CloseTicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔒 Đóng Ticket",
        style=discord.ButtonStyle.secondary,
        custom_id="close_ticket",
    )
    async def close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            embed=embed("XÁC NHẬN ĐÓNG TICKET", "Kênh sẽ tự động xóa sau **5 giây**...", 0xFEE75C)
        )
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
            return await interaction.response.send_message(
                f"❌ Ticket đang mở: {existing.mention}", ephemeral=True
            )
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
        }
        if staff:
            overwrites[staff] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True)
        try:
            channel = await guild.create_text_channel(
                name=name, category=category, overwrites=overwrites
            )
            await channel.send(
                content=f"{user.mention} {staff.mention if staff else ''}",
                embed=embed(f"TICKET: {ticket_type.upper()}", "Đội ngũ hỗ trợ sẽ phản hồi bạn sớm nhất!", 0x5865F2),
                view=CloseTicketView(),
            )
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


# --- CÀI ĐẶT HỆ THỐNG (SETUP) ---

class WelcomeModal(Modal, title="⚙️ Cài đặt Chào Mừng (Welcome)"):
    msg_input = TextInput(
        label="Nội dung tin nhắn",
        style=discord.TextStyle.paragraph,
        default=get_config("welcome_message") or "",
        required=True,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        set_config("welcome_message", self.msg_input.value.strip())
        await interaction.response.send_message("✅ Đã cập nhật nội dung chào mừng!", ephemeral=True)


class ConfigModal(Modal):
    def __init__(self, key):
        super().__init__(title=f"⚙️ Cài đặt {key}")
        self.key = key
        is_channel = "channel" in key or "category" in key
        self.value = TextInput(
            label="Nhập ID (hoặc nhập số ID kênh/role)" if is_channel else "Nhập ID",
            default=str(get_config(key) or ""),
            required=False,
            max_length=20,
        )
        self.add_item(self.value)

    async def on_submit(self, interaction):
        raw = self.value.value.strip()
        if raw and not raw.isdigit():
            return await interaction.response.send_message("❌ ID phải là dạng số.", ephemeral=True)
        
        val_int = int(raw) if raw else None
        set_config(self.key, val_int)

        if self.key == "verify_channel_id" and val_int:
            channel = interaction.guild.get_channel(val_int)
            if channel:
                embed_msg = discord.Embed(
                    title="『 XÁC THỰC MỞ KHÓA MÁY CHỦ 』",
                    description=(
                        "Chào mừng bạn đến với máy chủ!\n\n"
                        "⚠️ **Hướng dẫn:** Bấm vào nút bên dưới để tiến hành xác thực tài khoản một cách bảo mật."
                    ),
                    color=0x57F287
                )
                embed_msg.set_footer(text="Hệ thống bảo mật tự động")
                try:
                    await channel.send(embed=embed_msg, view=VerifyButtonView())
                except discord.Forbidden:
                    pass

        await interaction.response.send_message("✅ Đã lưu cấu hình thành công.", ephemeral=True)


class SetupSelect(Select):
    def __init__(self):
        keys = list(DEFAULT_CONFIG.keys())
        options = [discord.SelectOption(label=key, value=key, description=f"Cài đặt cho {key}") for key in keys]
        super().__init__(
            placeholder="⚙️ Chọn mục cần cấu hình ngay...",
            custom_id="setup_config_select",
            options=options,
        )

    async def callback(self, interaction):
        selected_key = self.values[0]
        await interaction.response.send_modal(ConfigModal(selected_key))


class SetupView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SetupSelect())


# --- SỰ KIỆN BOT ---

@bot.event
async def on_ready():
    bot.add_view(TicketPanelView())
    bot.add_view(CloseTicketView())
    bot.add_view(RoleView())
    bot.add_view(SetupView())
    bot.add_view(VerifyButtonView())

    if not check_birthdays.is_running():
        check_birthdays.start()

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
    cursor.execute("SELECT user_id, birthday FROM user_birthdays")
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
                    await channel.send(
                        content=f"🎂 Chúc mừng sinh nhật {member.mention}!",
                        embed=embed("CHÚC MỪNG SINH NHẬT!", "Chúc bạn một ngày mới thật vui vẻ và hạnh phúc! 🎈", 0xFF73FA),
                    )


@check_birthdays.before_loop
async def before_birthdays():
    await bot.wait_until_ready()


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.content.startswith("!"):
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
    text = template.format(
        user=member.mention,
        server=member.guild.name,
        name=member.name,
        count=len(member.guild.members),
    )
    message = embed("CHÀO MỪNG THÀNH VIÊN MỚI", text, 0x57F287)
    img_url = get_config("welcome_image")
    if img_url and img_url.startswith(("http://", "https://")):
        message.set_image(url=img_url)
    if member.display_avatar:
        message.set_thumbnail(url=member.display_avatar.url)
    await channel.send(embed=message)


# --- CÁC LỆNH SLASH COMMANDS ---

@bot.tree.command(name="help", description="Hiển thị bảng trợ giúp hệ thống")
async def help_command(interaction: discord.Interaction):
    em = discord.Embed(
        title="🌟 TRUNG TÂM ĐIỀU HÀNH & TRỢ GIÚP",
        description="Chào mừng bạn đến với hệ thống quản lý máy chủ tự động 24/7.\nDưới đây là toàn bộ các danh mục lệnh hiện có:",
        color=0x5865F2,
    )

    if bot.user.avatar:
        em.set_thumbnail(url=bot.user.avatar.url)

    em.add_field(
        name="🛡️ **Quản Trị (Moderation) & Xác Thực**",
        value=(
            "• `/ban [thành viên] [lý do]` - Khóa vĩnh viễn thành viên\n"
            "• `/mute [thành viên] [phút] [lý do]` - Cấm chat thành viên tạm thời\n"
            "• `/setup_verify` - Cài đặt Role trao khi người dùng Verify\n"
            "• `/verify` hoặc `/verify_panel` - Mở bảng xác thực hoặc gửi bảng nút bấm\n"
            "• `/set_birthday` - Mở bảng điền ngày sinh bảo mật (riêng tư)"
        ),
        inline=False,
    )

    em.add_field(
        name="🛠️ **Quản Lý Hệ Thống**",
        value=(
            "• `/setup` - Thiết lập bảng cấu hình hệ thống\n"
            "• `/weekly_chatters` - Bảng vàng thống kê chat\n"
            "• `/setwelcome` - Cài đặt tin nhắn và ảnh chào mừng"
        ),
        inline=False,
    )

    em.set_footer(
        text=f"Yêu cầu bởi {interaction.user.name} • Hoạt động ổn định",
        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
    )
    em.timestamp = datetime.now()

    await interaction.response.send_message(embed=em, ephemeral=True)


@bot.tree.command(name="verify", description="Mở bảng xác thực bảo mật riêng tư cho bạn")
async def verify_command(interaction: discord.Interaction):
    role_id = get_config("unlock_role_id")
    role = interaction.guild.get_role(role_id) if role_id else None
    
    if role and role in interaction.user.roles:
        return await interaction.response.send_message(
            "❌ Bạn đã được xác thực từ trước rồi!", ephemeral=True
        )

    saved_birthday = get_user_birthday(interaction.user.id)
    new_code = generate_random_code()
    temp_verify_codes[interaction.user.id] = new_code

    if not saved_birthday:
        return await interaction.response.send_modal(UnifiedVerifyModal(needs_birthday=True, code=new_code))
    
    return await interaction.response.send_modal(UnifiedVerifyModal(needs_birthday=False, code=new_code))


@bot.tree.command(name="verify_panel", description="Gửi khung bảng nút bấm xác thực vào kênh hiện tại")
@app_commands.checks.has_permissions(administrator=True)
async def verify_panel(interaction: discord.Interaction):
    embed_msg = discord.Embed(
        title="『 XÁC THỰC MỞ KHÓA MÁY CHỦ 』",
        description=(
            "Chào mừng bạn đến với máy chủ!\n\n"
            "⚠️ **Hướng dẫn:**\n"
            "• Bấm vào nút **🔐 Xác Thực Ngay** bên dưới.\n"
            "• Điền thông tin vào bảng bảo mật riêng tư hiện lên để xác thực hoàn tất."
        ),
        color=0x57F287
    )
    embed_msg.set_footer(text="Hệ thống bảo mật tự động")
    
    await interaction.channel.send(embed=embed_msg, view=VerifyButtonView())
    await interaction.response.send_message("✅ Đã gửi bảng Verify thành công vào kênh này!", ephemeral=True)


@bot.tree.command(name="setup", description="Bảng cài đặt cấu hình Bot")
@app_commands.checks.has_permissions(administrator=True)
async def setup_command(interaction):
    values = "\n".join(
        f"`{key}`: `{get_config(key) or 'Chưa cài'}`"
        for key in DEFAULT_CONFIG
    )
    await interaction.response.send_message(
        embed=embed("BẢNG CẤU HÌNH BOT", values), view=SetupView(), ephemeral=True
    )


@bot.tree.command(name="setup_verify", description="Cài đặt Role sẽ được trao khi người dùng Verify")
@app_commands.checks.has_permissions(administrator=True)
async def setup_verify(interaction: discord.Interaction):
    view = SetupVerifyView()
    await interaction.response.send_message(
        "⚙️ Vui lòng chọn **Role** từ danh sách bên dưới để cài đặt cho lệnh Verify:",
        view=view,
        ephemeral=True,
    )


@bot.tree.command(name="ban", description="Khóa vĩnh viễn (ban) một thành viên khỏi máy chủ")
@app_commands.describe(member="Thành viên cần ban", reason="Lý do ban khỏi máy chủ")
@app_commands.checks.has_permissions(ban_members=True)
async def ban_command(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "Không có lý do",
):
    if member.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message(
            "❌ Bạn không thể ban thành viên có quyền cao hơn hoặc ngang bằng bạn.",
            ephemeral=True,
        )
    try:
        await member.ban(reason=reason)
        await interaction.response.send_message(
            embed=embed(
                "ĐÃ BAN THÀNH VIÊN",
                f"👤 **Thành viên:** {member.mention} (`{member.id}`)\n🛡️ **Người thực hiện:** {interaction.user.mention}\n📝 **Lý do:** {reason}",
                0xED4245,
            )
        )
    except discord.Forbidden:
        await interaction.response.send_message("❌ Bot thiếu quyền để ban thành viên này.", ephemeral=True)


@bot.tree.command(name="mute", description="Cấm chat (Timeout) một thành viên trong khoảng thời gian")
@app_commands.describe(member="Thành viên cần mute", minutes="Số phút muốn cấm chat", reason="Lý do mute")
@app_commands.checks.has_permissions(moderate_members=True)
async def mute_command(
    interaction: discord.Interaction,
    member: discord.Member,
    minutes: app_commands.Range[int, 1, 10080],
    reason: str = "Không có lý do",
):
    if member.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message(
            "❌ Bạn không thể mute thành viên có quyền cao hơn hoặc ngang bằng bạn.",
            ephemeral=True,
        )
    try:
        duration = timedelta(minutes=minutes)
        await member.timeout(duration, reason=reason)
        await interaction.response.send_message(
            embed=embed(
                "ĐÃ MUTE THÀNH VIÊN",
                f"👤 **Thành viên:** {member.mention}\n⏱️ **Thời gian:** `{minutes} phút`\n🛡️ **Người thực hiện:** {interaction.user.mention}\n📝 **Lý do:** {reason}",
                0xFEE75C,
            )
        )
    except discord.Forbidden:
        await interaction.response.send_message("❌ Bot thiếu quyền để timeout thành viên này.", ephemeral=True)


@bot.tree.command(name="setwelcome", description="Cập nhật hình nền và tùy chỉnh lời chào mừng")
@app_commands.describe(image="Chọn file ảnh từ thiết bị của bạn (tùy chọn)")
@app_commands.checks.has_permissions(administrator=True)
async def setwelcome(interaction: discord.Interaction, image: discord.Attachment = None):
    if image:
        set_config("welcome_image", image.url)
    await interaction.response.send_modal(WelcomeModal())


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
    cursor.execute(
        """
        SELECT user_id, messages FROM chat_activity 
        WHERE guild_id = ? AND week_key = ? 
        ORDER BY messages DESC LIMIT ?
    """,
        (guild_id, week_key, limit),
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return await interaction.response.send_message("📊 Chưa có dữ liệu chat trong tuần này.", ephemeral=True)

    lines = [f"**{index}.** <@{user_id}> — `{msgs:,}` tin nhắn" for index, (user_id, msgs) in enumerate(rows, start=1)]
    message = f"📊 **THỐNG KÊ TUẦN ({week_key})**\n\n" + "\n".join(lines)
    await interaction.response.send_message(embed=embed("THỐNG KÊ CHAT TUẦN", message, 0x5865F2))


@bot.tree.command(name="set_birthday", description="Mở bảng bảo mật để đăng ký ngày tháng năm sinh cá nhân")
async def set_birthday(interaction: discord.Interaction):
    await interaction.response.send_modal(BirthdayModal())


# --- WEB SERVER (FLASK) ---
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot Discord System is running 24/7 smoothly!"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    t = Thread(target=run_web, daemon=True)
    t.start()
    bot.run(TOKEN)
