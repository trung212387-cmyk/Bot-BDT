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
from flask import Flask

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "bot_database.db"
TARGET_GUILD_ID = 1503922700408586240

# Lấy Token từ biến môi trường trên Render
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Thiếu DISCORD_TOKEN trong biến môi trường của Render!")


def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_coins (
            user_id TEXT PRIMARY KEY,
            coins INTEGER DEFAULT 0
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
    conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
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
    conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
        (key, str(value) if value is not None else ""),
    )
    conn.commit()
    conn.close()


def get_user_birthday(user_id):
    conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT birthday FROM user_birthdays WHERE user_id = ?", (str(user_id),))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def add_user_coins(user_id, amount):
    conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO user_coins (user_id, coins) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET coins = coins + ?
    """,
        (str(user_id), amount, amount),
    )
    conn.commit()
    conn.close()


def get_user_coins(user_id):
    conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT coins FROM user_coins WHERE user_id = ?", (str(user_id),))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0


# --- HỆ THỐNG XÁC THỰC & SINH NHẬT ---

def generate_random_code(length=6):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


temp_verify_codes = {}


class BirthdayOnlyModal(discord.ui.Modal, title="🎂 Đăng Ký Ngày Sinh Bảo Mật"):
    def __init__(self):
        super().__init__()
        self.date_input = discord.ui.TextInput(
            label="Nhập ngày tháng năm sinh của bạn",
            placeholder="Định dạng: DD/MM/YYYY (Ví dụ: 15/08/2008)",
            style=discord.TextStyle.short,
            required=True,
            max_length=15,
        )
        self.add_item(self.date_input)

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

        conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO user_birthdays (user_id, birthday) VALUES (?, ?)",
            (user_id, formatted_date),
        )
        conn.commit()
        conn.close()

        await interaction.response.send_message(
            f"✅ Đã lưu thành công ngày sinh `{formatted_date}` của bạn! Bây giờ bạn đã có thể bấm nút **Verify** để xác thực tài khoản.",
            ephemeral=True,
        )


class BirthdayButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🎂 Đăng Ký Ngày Sinh Ngay",
        style=discord.ButtonStyle.primary,
        custom_id="persistent_birthday_button",
    )
    async def birthday_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BirthdayOnlyModal())


class VerifyCodeModal(discord.ui.Modal, title="🔐 Xác Thực Mã Bảo Mật"):
    def __init__(self, code: str):
        super().__init__()
        self.code = code
        self.code_input = discord.ui.TextInput(
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


class VerifyButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔐 Xác Thực Ngay",
        style=discord.ButtonStyle.success,
        custom_id="persistent_verify_button",
    )
    async def verify_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_id = get_config("unlock_role_id")
        role = interaction.guild.get_role(role_id) if role_id else None

        if role and role in interaction.user.roles:
            return await interaction.response.send_message(
                "❌ Bạn đã được xác thực từ trước rồi!", ephemeral=True
            )

        saved_birthday = get_user_birthday(interaction.user.id)
        if not saved_birthday:
            return await interaction.response.send_message(
                "❌ **Bạn chưa đăng ký ngày sinh!** Vui lòng bấm nút **🎂 Đăng Ký Ngày Sinh Ngay** ở kênh sinh nhật trước khi tiến hành xác thực.",
                ephemeral=True,
            )

        new_code = generate_random_code()
        temp_verify_codes[interaction.user.id] = new_code
        return await interaction.response.send_modal(VerifyCodeModal(code=new_code))


class SetupVerifyRoleSelect(discord.ui.RoleSelect):
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


class SetupVerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(SetupVerifyRoleSelect())


# --- KHỞI TẠO BOT & TIỆN ÍCH CHUNG ---

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Lưu trữ các phòng game đang hoạt động theo kênh chat: {channel_id: BomBomKissGame}
active_games = {}


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

    conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
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

class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔒 Đóng Ticket",
        style=discord.ButtonStyle.secondary,
        custom_id="close_ticket",
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=embed("XÁC NHẬN ĐÓNG TICKET", "Kênh sẽ tự động xóa sau **5 giây**...", 0xFEE75C)
        )
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason="Ticket closed")
        except discord.HTTPException:
            pass


class TicketPanelView(discord.ui.View):
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


class RoleView(discord.ui.View):
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


# --- PHÒNG CHỜ VÀ MINIGAME: 9 Ô BẤM NÚT ---

class BomBomKissGame:
    def __init__(self, host: discord.User, target: discord.User, channel: discord.TextChannel):
        self.host = host
        self.target = target
        self.channel = channel
        self.players = [host, target]
        
        # Random người chơi đi đầu tiên
        self.current_turn_index = random.randint(0, 1)
        
        self.boom_index = random.randint(1, 9)
        self.score = 0
        self.opened_boxes = set()
        self.is_over = False

    def get_current_player(self):
        return self.players[self.current_turn_index]

    def switch_turn(self):
        self.current_turn_index = 1 - self.current_turn_index


class GameBoardView(discord.ui.View):
    def __init__(self, game: BomBomKissGame):
        super().__init__(timeout=180)
        self.game = game
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        for i in range(1, 10):
            if i in self.game.opened_boxes:
                if i == self.game.boom_index:
                    btn = discord.ui.Button(
                        label="💣 BOM",
                        style=discord.ButtonStyle.danger,
                        disabled=True,
                        row=(i - 1) // 3
                    )
                else:
                    btn = discord.ui.Button(
                        label=f"💎 Ô {i}",
                        style=discord.ButtonStyle.success,
                        disabled=True,
                        row=(i - 1) // 3
                    )
            else:
                if self.game.is_over:
                    btn = discord.ui.Button(
                        label=f"Ô {i}",
                        style=discord.ButtonStyle.secondary,
                        disabled=True,
                        row=(i - 1) // 3
                    )
                else:
                    btn = discord.ui.Button(
                        label=f"Ô {i}",
                        style=discord.ButtonStyle.primary,
                        row=(i - 1) // 3
                    )
                    btn.callback = self.create_callback(i)
            self.add_item(btn)

    def create_callback(self, box_num: int):
        async def button_callback(interaction: discord.Interaction):
            if self.game.is_over:
                return await interaction.response.send_message("❌ Trận đấu đã kết thúc!", ephemeral=True)

            current_player = self.game.get_current_player()
            if interaction.user != current_player:
                return await interaction.response.send_message(
                    f"❌ Chưa tới lượt của bạn! Hiện tại là lượt của {current_player.mention}.",
                    ephemeral=True
                )

            if box_num in self.game.opened_boxes:
                return await interaction.response.send_message("❌ Ô này đã được mở rồi!", ephemeral=True)

            self.game.opened_boxes.add(box_num)

            # --- KHI BẤM TRÚNG BOM ---
            if box_num == self.game.boom_index:
                self.game.is_over = True
                active_games.pop(self.game.channel.id, None)

                self.update_buttons()

                embed_msg = embed(
                    "💥💣 BOM BOM KISS - BÙM NỔ KẾT THÚC!",
                    f"💥 Ôi không! {interaction.user.mention} đã bấm trúng **Ô số {box_num}** chứa quả **💣 Bom** định mệnh!\n\n"
                    f"💀 **Trò chơi chính thức kết thúc!** {interaction.user.mention} đã thua cuộc trong ván đấu này. 🔥",
                    0xED4245
                )
                return await interaction.response.edit_message(embed=embed_msg, view=self)

            # --- KHI BẤM TRÚNG KIM CƯƠNG (AN TOÀN) ---
            else:
                self.game.score += 50

                # Nếu mở hết 8 ô an toàn -> Chiến thắng hoàn hảo
                if len(self.game.opened_boxes) == 8:
                    reward_coins = 200
                    add_user_coins(interaction.user.id, reward_coins)
                    total_balance = get_user_coins(interaction.user.id)
                    self.game.is_over = True
                    active_games.pop(self.game.channel.id, None)

                    self.update_buttons()

                    embed_msg = embed(
                        "🎉💋 BOM BOM KISS - CHIẾN THẮNG HOÀN HẢO!",
                        f"🏆 Tuyệt vời! {interaction.user.mention} đã né sạch bom và tìm thấy toàn bộ **💎 Kim Cương** ở 8 ô an toàn!\n"
                        f"🎁 Phần thưởng chiến thắng: **`+{reward_coins} Coin`** 🪙\n"
                        f"💰 Tổng số Coin hiện tại của bạn: **`{total_balance} Coin`** ✨",
                        0x57F287
                    )
                    return await interaction.response.edit_message(embed=embed_msg, view=self)
                else:
                    self.game.switch_turn()
                    next_player = self.game.get_current_player()
                    self.update_buttons()

                    embed_msg = embed(
                        "💥 KỊCH TÍNH: BOM BOM KISS 💋",
                        f"✨ {interaction.user.mention} vừa mở trúng **💎 Kim Cương** ở Ô số {box_num} (+50 điểm)!\n\n"
                        f"👉 Lượt tiếp theo thuộc về: **{next_player.mention}**. Hãy bấm chọn ô tiếp theo trên bàn cờ!",
                        0xFF73FA
                    )
                    return await interaction.response.edit_message(embed=embed_msg, view=self)

        return button_callback

    async def on_timeout(self):
        self.game.is_over = True
        active_games.pop(self.game.channel.id, None)
        for child in self.children:
            child.disabled = True
        try:
            await self.game.channel.send("⏱️ Trận đấu Bom Bom Kiss đã bị hủy do hết thời gian phản hồi (3 phút)!")
        except:
            pass


class RoomLobbyView(discord.ui.View):
    def __init__(self, host: discord.User, channel: discord.TextChannel):
        super().__init__(timeout=60)
        self.host = host
        self.channel = channel
        self.message = None

    @discord.ui.button(label="🎮 Tham Gia Trận Đấu", style=discord.ButtonStyle.success, custom_id="join_room_game")
    async def join_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user == self.host:
            return await interaction.response.send_message("❌ Bạn là chủ phòng, không thể tự tham gia phòng của mình!", ephemeral=True)
        if interaction.user.bot:
            return await interaction.response.send_message("❌ Bot không thể tham gia chơi!", ephemeral=True)

        game = BomBomKissGame(self.host, interaction.user, self.channel)
        active_games[self.channel.id] = game

        current_player = game.get_current_player()
        game_view = GameBoardView(game)

        msg_embed = embed(
            "💥 KỊCH TÍNH: 9 Ô BOM BOM KISS 💋",
            f"🏠 Phòng đấu giữa {self.host.mention} và {interaction.user.mention} đã bắt đầu!\n\n"
            f"🎲 **Bot đã random và lượt đi đầu tiên thuộc về:** {current_player.mention}!\n\n"
            f"👉 {current_player.mention}, hãy bấm vào các nút bên dưới để chọn ô!",
            0xFF73FA
        )
        await interaction.response.edit_message(content="🚀 Trận đấu chính thức bắt đầu!", embed=msg_embed, view=game_view)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            if self.message:
                await self.message.edit(content="⏱️ Phòng chờ Bom Bom Kiss đã hết hạn do không có ai tham gia!", view=None)
        except:
            pass


# --- CÀI ĐẶT HỆ THỐNG (SETUP) ---

class WelcomeModal(discord.ui.Modal, title="⚙️ Cài đặt Chào Mừng (Welcome)"):
    def __init__(self):
        super().__init__()
        self.msg_input = discord.ui.TextInput(
            label="Nội dung tin nhắn",
            style=discord.TextStyle.paragraph,
            default=get_config("welcome_message") or "",
            required=True,
            max_length=1000,
        )
        self.add_item(self.msg_input)

    async def on_submit(self, interaction: discord.Interaction):
        set_config("welcome_message", self.msg_input.value.strip())
        await interaction.response.send_message("✅ Đã cập nhật nội dung chào mừng!", ephemeral=True)


class ConfigModal(discord.ui.Modal):
    def __init__(self, key):
        super().__init__(title=f"⚙️ Cài đặt {key}")
        self.key = key
        is_channel = "channel" in key or "category" in key
        self.value = discord.ui.TextInput(
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
                    title="🔥 XÁC THỰC MỞ KHÓA MÁY CHỦ",
                    description=(
                        "Chào mừng bạn đến với máy chủ!\n\n"
                        "⚠️ **Hướng dẫn:** Bấm vào nút bên dưới để tiến hành xác thực tài khoản một cách bảo mật."
                    ),
                    color=0x57F287,
                )
                embed_msg.set_footer(text="Hệ thống bảo mật tự động")
                try:
                    await channel.send(embed=embed_msg, view=VerifyButtonView())
                except discord.Forbidden:
                    pass

        if self.key == "birthday_channel_id" and val_int:
            channel = interaction.guild.get_channel(val_int)
            if channel:
                embed_msg = discord.Embed(
                    title="🎂 ĐĂNG KÝ NGÀY SINH NHẬT",
                    description=(
                        "Chào mừng bạn đến với kênh thông báo sinh nhật!\n\n"
                        "🎁 **Hướng dẫn:** Bấm vào nút **🎂 Đăng Ký Ngày Sinh Ngay** bên dưới để hệ thống ghi nhớ và chúc mừng sinh nhật bạn vào mỗi năm."
                    ),
                    color=0xFF73FA,
                )
                embed_msg.set_footer(text="Hệ thống sinh nhật tự động")
                try:
                    await channel.send(embed=embed_msg, view=BirthdayButtonView())
                except discord.Forbidden:
                    pass

        await interaction.response.send_message("✅ Đã lưu cấu hình thành công.", ephemeral=True)


class SetupSelect(discord.ui.Select):
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


class SetupView(discord.ui.View):
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
    bot.add_view(BirthdayButtonView())

    if not check_birthdays.is_running():
        check_birthdays.start()

    try:
        synced = await bot.tree.sync()
        print(f"✅ Bot sẵn sàng: {bot.user} | Đồng bộ {len(synced)} lệnh Slash Commands")
    except Exception as e:
        print(f"⚠️ Lỗi đồng bộ lệnh: {e}")


@tasks.loop(hours=24)
async def check_birthdays():
    day = datetime.now().strftime("%d/%m")
    conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
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
                    try:
                        await channel.send(
                            content=f"🎂 Chúc mừng sinh nhật {member.mention}!",
                            embed=embed("CHÚC MỪNG SINH NHẬT!", "Chúc bạn một ngày mới thật vui vẻ và hạnh phúc! 🎈", 0xFF73FA),
                        )
                    except discord.HTTPException:
                        pass


@check_birthdays.before_loop
async def before_birthdays():
    await bot.wait_until_ready()


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # --- LỆNH NHẮN TIN TRUYỀN THỐNG: !phong ---
    if message.content.strip().lower() == "!phong":
        if message.channel.id in active_games:
            return await message.channel.send("❌ Kênh này đang có một ván đấu diễn ra! Hãy hoàn thành ván trước đó.", delete_after=5)
        
        view = RoomLobbyView(message.author, message.channel)
        msg_embed = embed(
            "🏠 PHÒNG CHỜ BOM BOM KISS 💋",
            f"{message.author.mention} vừa mở phòng chờ minigame **9 Ô May Mắn**!\n\n"
            "✨ Bấm nút **🎮 Tham Gia Trận Đấu** bên dưới để nhảy vào phòng và cùng chơi ngay lập tức!",
            0xFF73FA
        )
        sent_msg = await message.channel.send(embed=msg_embed, view=view)
        view.message = sent_msg
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
    try:
        await channel.send(embed=message)
    except discord.HTTPException:
        pass


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
            "• `/birthday_panel` - Gửi bảng nút bấm đăng ký sinh nhật ra kênh"
        ),
        inline=False,
    )

    em.add_field(
        name="🎮 **Minigame & Kinh Tế (Coin)**",
        value=(
            "• Gõ `!phong` - Tạo phòng chờ minigame (Random lượt đi, bấm nút chọn ô)\n"
            "• `/balance` - Kiểm tra số Coin hiện có của bạn\n"
            "• `/weekly_chatters` - Bảng vàng thống kê chat tuần"
        ),
        inline=False,
    )

    em.add_field(
        name="🛠️ **Quản Lý Hệ Thống**",
        value=(
            "• `/setup` - Thiết lập bảng cấu hình hệ thống\n"
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


@bot.tree.command(name="balance", description="Kiểm tra số xu (Coin) hiện có của bạn")
@app_commands.describe(member="Thành viên cần xem (để trống nếu muốn xem của chính mình)")
async def balance_command(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    coins = get_user_coins(target.id)
    await interaction.response.send_message(
        embed=embed("VÍ TIỀN / COIN", f"👤 **Thành viên:** {target.mention}\n💰 **Số dư hiện tại:** `{coins:,} Coin`", 0xFEE75C),
        ephemeral=True,
    )


@bot.tree.command(name="verify", description="Mở bảng xác thực bảo mật riêng tư cho bạn")
async def verify_command(interaction: discord.Interaction):
    role_id = get_config("unlock_role_id")
    role = interaction.guild.get_role(role_id) if role_id else None

    if role and role in interaction.user.roles:
        return await interaction.response.send_message(
            "❌ Bạn đã được xác thực từ trước rồi!", ephemeral=True
        )

    saved_birthday = get_user_birthday(interaction.user.id)
    if not saved_birthday:
        return await interaction.response.send_message(
            "❌ **Bạn chưa đăng ký ngày sinh!** Vui lòng sang kênh sinh nhật để đăng ký ngày sinh trước khi tiến hành xác thực.",
            ephemeral=True,
        )

    new_code = generate_random_code()
    temp_verify_codes[interaction.user.id] = new_code
    return await interaction.response.send_modal(VerifyCodeModal(code=new_code))


@bot.tree.command(name="verify_panel", description="Gửi khung bảng nút bấm xác thực vào kênh hiện tại")
@app_commands.checks.has_permissions(administrator=True)
async def verify_panel(interaction: discord.Interaction):
    embed_msg = discord.Embed(
        title="🔥 XÁC THỰC MỞ KHÓA MÁY CHỦ",
        description=(
            "Chào mừng bạn đến với máy chủ!\n\n"
            "⚠️ **Hướng dẫn:**\n"
            "• Đảm bảo bạn đã đăng ký ngày sinh.\n"
            "• Bấm vào nút **🔐 Xác Thực Ngay** bên dưới.\n"
            "• Điền mã xác thực vào bảng bảo mật riêng tư hiện lên để hoàn tất."
        ),
        color=0x57F287,
    )
    embed_msg.set_footer(text="Hệ thống bảo mật tự động")

    await interaction.channel.send(embed=embed_msg, view=VerifyButtonView())
    await interaction.response.send_message("✅ Đã gửi bảng Verify thành công vào kênh này!", ephemeral=True)


@bot.tree.command(name="birthday_panel", description="Gửi khung bảng nút bấm đăng ký ngày sinh vào kênh hiện tại")
@app_commands.checks.has_permissions(administrator=True)
async def birthday_panel(interaction: discord.Interaction):
    embed_msg = discord.Embed(
        title="🎂 ĐĂNG KÝ NGÀY SINH NHẬT",
        description=(
            "Chào mừng bạn đến với kênh thông báo sinh nhật!\n\n"
            "🎁 **Hướng dẫn:** Bấm vào nút **🎂 Đăng Ký Ngày Sinh Ngay** bên dưới để hệ thống ghi nhớ và chúc mừng sinh nhật bạn vào mỗi năm."
        ),
        color=0xFF73FA,
    )
    embed_msg.set_footer(text="Hệ thống sinh nhật tự động")

    await interaction.channel.send(embed=embed_msg, view=BirthdayButtonView())
    await interaction.response.send_message("✅ Đã gửi bảng Đăng Ký Sinh Nhật thành công vào kênh này!", ephemeral=True)


@bot.tree.command(name="setup", description="Bảng cài đặt cấu hình Bot")
@app_commands.checks.has_permissions(administrator=True)
async def setup_command(interaction: discord.Interaction):
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

    conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
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
    await interaction.response.send_modal(BirthdayOnlyModal())


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
