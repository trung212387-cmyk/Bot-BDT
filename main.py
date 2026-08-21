import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path
import random
import sqlite3
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, ChannelSelect, Modal, RoleSelect, Select, TextInput, View
from flask import Flask
import wavelink

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
  conn.commit()
  conn.close()


init_db()

DEFAULT_CONFIG = {
    "welcome_channel_id": None,
    "welcome_image": "",
    "welcome_message": (
        "Chào mừng {user} đã gia nhập **{server}**!\n\n▫️ **Tài khoản:**"
        " {name}\n▫️ **Thành viên thứ:** `{count}`"
    ),
    "ticket_category_id": None,
    "staff_role_id": None,
    "self_role_id": None,
    "birthday_channel_id": None,
    "unlock_role_id": None,
    "verify_code": "123456",
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


intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

active_trivia = {}
active_drop_ff = {}


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


async def connect_nodes():
  await bot.wait_until_ready()
  try:
    node = wavelink.Node(
        uri="http://lavalink.darrennathanael.com:80", password="youshallnotpass"
    )
    await wavelink.Pool.connect(nodes=[node], client=bot)
  except Exception as e:
    print(f"⚠️ Không thể kết nối Wavelink Node: {e}")


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
        embed=embed(
            "XÁC NHẬN ĐÓNG TICKET",
            "Kênh sẽ tự động xóa sau **5 giây**...",
            0xFEE75C,
        )
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
        user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, attach_files=True
        ),
    }
    if staff:
      overwrites[staff] = discord.PermissionOverwrite(
          view_channel=True, send_messages=True, attach_files=True
      )
    try:
      channel = await guild.create_text_channel(
          name=name, category=category, overwrites=overwrites
      )
      await channel.send(
          content=f"{user.mention} {staff.mention if staff else ''}",
          embed=embed(
              f"TICKET: {ticket_type.upper()}",
              "Đội ngũ hỗ trợ sẽ phản hồi bạn sớm nhất!",
              0x5865F2,
          ),
          view=CloseTicketView(),
      )
      await interaction.response.send_message(
          f"✅ Đã tạo ticket: {channel.mention}", ephemeral=True
      )
    except discord.Forbidden:
      await interaction.response.send_message(
          "❌ Bot thiếu quyền tạo kênh.", ephemeral=True
      )

  @discord.ui.button(
      label="🛠️ Hỗ Trợ Kỹ Thuật",
      style=discord.ButtonStyle.primary,
      custom_id="ticket_support",
  )
  async def support(self, interaction, button):
    await self.create_ticket(interaction, "Hỗ Trợ Kỹ Thuật", "ky-thuat")

  @discord.ui.button(
      label="📢 Tố Cáo / Góp Ý",
      style=discord.ButtonStyle.danger,
      custom_id="ticket_report",
  )
  async def report(self, interaction, button):
    await self.create_ticket(interaction, "Tố Cáo / Góp Ý", "to-cao")


class RoleView(View):

  def __init__(self):
    super().__init__(timeout=None)

  @discord.ui.button(
      label="✨ Nhận / Hủy Role",
      style=discord.ButtonStyle.success,
      custom_id="toggle_self_role",
  )
  async def toggle(self, interaction, button):
    role_id = get_config("self_role_id")
    role = interaction.guild.get_role(role_id) if role_id else None
    if not role:
      return await interaction.response.send_message(
          "❌ Self-role chưa được cấu hình.", ephemeral=True
      )
    try:
      if role in interaction.user.roles:
        await interaction.user.remove_roles(role)
        message = f"Đã gỡ {role.mention}."
      else:
        await interaction.user.add_roles(role)
        message = f"Đã nhận {role.mention}."
      await interaction.response.send_message(message, ephemeral=True)
    except discord.Forbidden:
      await interaction.response.send_message(
          "❌ Bot không đủ quyền quản lý role này.", ephemeral=True
      )


class VerifyModal(Modal, title="🔐 Nhập Mã Xác Thực Server"):
  code_input = TextInput(
      label="Mã xác thực",
      placeholder="Nhập mã xác thực của bạn vào đây...",
      style=discord.TextStyle.short,
      required=True,
      max_length=50,
  )

  async def on_submit(self, interaction: discord.Interaction):
    entered_code = self.code_input.value.strip()
    correct_code = str(get_config("verify_code") or "123456")

    if entered_code != correct_code:
      return await interaction.response.send_message(
          "❌ Mã xác thực không chính xác! Vui lòng kiểm tra lại.",
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
          f"✅ Xác thực thành công! Đã cấp role {role.mention}.", ephemeral=True
      )
    except discord.Forbidden:
      await interaction.response.send_message(
          "❌ Bot không đủ quyền trao role này.", ephemeral=True
      )


class UnlockView(View):

  def __init__(self):
    super().__init__(timeout=None)

  @discord.ui.button(
      label="🔓 Xác Nhận / Mở Khóa Kênh",
      style=discord.ButtonStyle.success,
      custom_id="unlock_channels_button",
  )
  async def unlock(self, interaction: discord.Interaction, button: Button):
    await interaction.response.send_modal(VerifyModal())


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
        content=(
            f"✅ Đã thiết lập thành công role xác thực cho lệnh Verify là:"
            f" {selected_role.mention}!"
        ),
        view=None,
        embed=None,
    )


class SetupVerifyView(View):

  def __init__(self):
    super().__init__(timeout=60)
    self.add_item(SetupVerifyRoleSelect())


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
    await interaction.response.send_message(
        "✅ Đã cập nhật nội dung chào mừng! (Hãy dùng lệnh `/setwelcome` kèm"
        " đính kèm ảnh nếu muốn đổi hình ảnh)",
        ephemeral=True,
    )


class SetupChannelSelect(ChannelSelect):

  def __init__(self, key):
    self.key = key
    super().__init__(
        placeholder=f"📂 Chọn kênh cho {key}...",
        min_values=1,
        max_values=1,
        channel_types=[discord.ChannelType.text],
        custom_id=f"select_channel_{key}",
    )

  async def callback(self, interaction):
    selected_channel = self.values[0]
    set_config(self.key, selected_channel.id)
    await interaction.response.edit_message(
        content=(
            f"✅ Đã cài đặt **`{self.key}`** vào kênh"
            f" {selected_channel.mention}!"
        ),
        view=None,
        embed=None,
    )


class SetupChannelView(View):

  def __init__(self, key):
    super().__init__(timeout=60)
    self.add_item(SetupChannelSelect(key))


class SetupSelect(Select):

  def __init__(self):
    keys = [key for key in DEFAULT_CONFIG if key.endswith("_id")]
    options = [
        discord.SelectOption(
            label=key, value=key, description=f"Cài đặt cho {key}"
        )
        for key in keys
    ]
    super().__init__(
        placeholder="⚙️ Chọn mục cần cấu hình ngay...",
        custom_id="setup_config_select",
        options=options,
    )

  async def callback(self, interaction):
    selected_key = self.values[0]
    if "channel" in selected_key:
      view = SetupChannelView(selected_key)
      await interaction.response.edit_message(
          content=f"📂 Vui lòng chọn kênh cho **`{selected_key}`**:",
          embed=None,
          view=view,
      )
    else:
      await interaction.response.send_modal(ConfigModal(selected_key))


class ConfigModal(Modal):

  def __init__(self, key):
    super().__init__(title=f"⚙️ Cài đặt {key}")
    self.key = key
    self.value = TextInput(
        label="Nhập ID",
        default=str(get_config(key) or ""),
        required=False,
        max_length=20,
    )
    self.add_item(self.value)

  async def on_submit(self, interaction):
    raw = self.value.value.strip()
    if raw and not raw.isdigit():
      return await interaction.response.send_message(
          "❌ ID phải là số.", ephemeral=True
      )
    set_config(self.key, int(raw) if raw else None)
    await interaction.response.send_message(
        "✅ Đã lưu cấu hình thành công.", ephemeral=True
    )


class SetupView(View):

  def __init__(self):
    super().__init__(timeout=None)
    self.add_item(SetupSelect())


class GameSelectView(View):

  def __init__(self):
    super().__init__(timeout=60)

  @discord.ui.button(
      label="Liên Quân Mobile",
      style=discord.ButtonStyle.primary,
      emoji="⚔️",
      custom_id="game_lienquan",
  )
  async def lienquan_button(
      self, interaction: discord.Interaction, button: Button
  ):
    await self.start_trivia(interaction, "lienquan")

  @discord.ui.button(
      label="Valorant",
      style=discord.ButtonStyle.secondary,
      emoji="🎯",
      custom_id="game_valorant",
  )
  async def valorant_button(
      self, interaction: discord.Interaction, button: Button
  ):
    await self.start_trivia(interaction, "valorant")

  @discord.ui.button(
      label="Roblox",
      style=discord.ButtonStyle.success,
      emoji="🤖",
      custom_id="game_roblox",
  )
  async def roblox_button(
      self, interaction: discord.Interaction, button: Button
  ):
    await self.start_trivia(interaction, "roblox")

  async def start_trivia(self, interaction: discord.Interaction, game_type: str):
    channel_id = interaction.channel.id

    if channel_id in active_trivia:
      await interaction.response.send_message(
          "⚠️ Đã có câu đố đang diễn ra ở kênh này rồi!", ephemeral=True
      )
      return

    games_data = {
        "lienquan": [
            (
                "Nakroth (Sát Thủ Đi Rừng)",
                ["nakroth", "nak", "nạc rốt"],
                (
                    "https://raw.githubusercontent.com/AOV-Wiki/assets/main/heroes/Nakroth/avatar.png"
                ),
                "Sát thủ cơ động, đi rừng thần tốc.",
            ),
            (
                "Florentino (Đấu Sĩ Hoa Mỹ)",
                ["florentino", "flo", "phloren"],
                (
                    "https://raw.githubusercontent.com/AOV-Wiki/assets/main/heroes/Florentino/avatar.png"
                ),
                "Đấu sĩ hoa mỹ với những điệu nhảy nhặt hoa.",
            ),
            (
                "Elsu (Xạ Thủ Tầm Xa)",
                ["elsu", "eo su"],
                (
                    "https://raw.githubusercontent.com/AOV-Wiki/assets/main/heroes/Elsu/avatar.png"
                ),
                "Xạ thủ cấu rỉa tầm xa với Viễn Trình Kích.",
            ),
            (
                "Tulen (Pháp Sư Lôi Điện)",
                ["tulen", "tu len"],
                (
                    "https://raw.githubusercontent.com/AOV-Wiki/assets/main/heroes/Tulen/avatar.png"
                ),
                "Pháp sư dồn sát thương cực mạnh với lôi điện.",
            ),
            (
                "Violet (Xạ Thủ Chí Mạng)",
                ["violet", "vịt", "vân"],
                (
                    "https://raw.githubusercontent.com/AOV-Wiki/assets/main/heroes/Violet/avatar.png"
                ),
                "Xạ thủ lăn lộn bắn chí mạng biểu tượng của Liên Quân.",
            ),
            (
                "Raz (Quyền Vương)",
                ["raz", "rát"],
                (
                    "https://raw.githubusercontent.com/AOV-Wiki/assets/main/heroes/Raz/avatar.png"
                ),
                "Đấu sĩ/Sát thủ quyền vương đẩy lùi kẻ địch mạnh mẽ.",
            ),
            (
                "Hayate (Ninja Sát Thương Chuẩn)",
                ["hayate", "hải", "haya"],
                (
                    "https://raw.githubusercontent.com/AOV-Wiki/assets/main/heroes/Hayate/avatar.png"
                ),
                "Xạ thủ sát thương chuẩn cơ động bậc nhất.",
            ),
            (
                "Maloch (Ma Vương Quỷ Kiếm)",
                ["maloch", "mã lộc"],
                (
                    "https://raw.githubusercontent.com/AOV-Wiki/assets/main/heroes/Maloch/avatar.png"
                ),
                "Đại tướng quỷ chém ra sát thương chuẩn diện rộng.",
            ),
            (
                "Lauriel (Đại Thiên Sứ)",
                ["lauriel", "lơ ri el"],
                (
                    "https://raw.githubusercontent.com/AOV-Wiki/assets/main/heroes/Lauriel/avatar.png"
                ),
                "Đại thiên sứ múa trong vòng tròn giảm hồi chiêu.",
            ),
            (
                "Ryoma (Kiếm Sĩ Đâm Xa)",
                ["ryoma", "rô ma"],
                (
                    "https://raw.githubusercontent.com/AOV-Wiki/assets/main/heroes/Ryoma/avatar.png"
                ),
                "Kiếm khách đâm kiếm tầm xa cấu rỉa cực khó chịu.",
            ),
        ],
        "valorant": [
            (
                "Jett (Đặc Vụ Gió Lướt)",
                ["jett", "vét", "dét"],
                (
                    "https://static.wikia.nocookie.net/valorant/images/5/5a/Jett_icon.png"
                ),
                "Đặc vụ lướt và bay trên không cực kỳ linh hoạt.",
            ),
            (
                "Reyna (Nữ Hoàng Hút Máu)",
                ["reyna", "rây na", "rayna"],
                (
                    "https://static.wikia.nocookie.net/valorant/images/b/b0/Reyna_icon.png"
                ),
                "Nữ hoàng hút máu, càng hạ gục càng mạnh mẽ.",
            ),
            (
                "Sage (Hộ Vệ Tường Băng)",
                ["sage", "sếch", "sây gi"],
                (
                    "https://static.wikia.nocookie.net/valorant/images/7/71/Sage_icon.png"
                ),
                "Hộ vệ dựng tường băng bảo vệ và hồi sinh đồng đội.",
            ),
            (
                "Omen (Bóng Ma Đen Tối)",
                ["omen", "ô mên"],
                (
                    "https://static.wikia.nocookie.net/valorant/images/b/b2/Omen_icon.png"
                ),
                "Bóng ma dịch chuyển không tiếng động và tạo khói tối.",
            ),
            (
                "Viper (Chuyên Gia Độc Dược)",
                ["viper", "vai pơ"],
                (
                    "https://static.wikia.nocookie.net/valorant/images/3/33/Viper_icon.png"
                ),
                "Chuyên gia độc dược tạo tường khí độc kiểm soát bản đồ.",
            ),
            (
                "Phoenix (Đặc Vụ Lửa)",
                ["phoenix", "phê lích", "phượng hoàng"],
                (
                    "https://static.wikia.nocookie.net/valorant/images/7/77/Phoenix_icon.png"
                ),
                "Sử dụng sức mạnh lửa để hồi máu và tự hồi sinh.",
            ),
            (
                "Chamber (Nhà Quý Tôn Súng Ngắm)",
                ["chamber", "chêm bơ", "ông chú súng ngắm"],
                (
                    "https://static.wikia.nocookie.net/valorant/images/2/27/Chamber_icon.png"
                ),
                "Nhà chế tạo vũ khí hào hoa với súng ngắm tối thượng.",
            ),
            (
                "Cypher (Trinh Sát Camera)",
                ["cypher", "sai phơ", "ciph"],
                (
                    "https://static.wikia.nocookie.net/valorant/images/9/98/Cypher_icon.png"
                ),
                "Trinh sát giăng camera và bẫy dây theo dõi kẻ địch.",
            ),
        ],
        "roblox": [
            (
                "Noob (Biểu Tượng Vàng Xanh)",
                ["noob", "núp", "núp lùm"],
                (
                    "https://tr.rbxcdn.com/30day-AvatarHeadshot-BEEF3C34F90F0C05D5D18742DE435649-Png/150/150/AvatarHeadshot/noFilter"
                ),
                "Biểu tượng kinh điển mang sắc áo vàng xanh của Roblox.",
            ),
            (
                "Trái Ác Quỷ Blox Fruits",
                ["blox fruits", "bloxfruits", "trái ác quỷ", "blox fruit"],
                (
                    "https://images.rbxcdn.com/7b1c31405e199d75b3648679f291e0a2"
                ),
                "Tựa game hải tặc cày cuốc trái ác quỷ đình đám trên Roblox.",
            ),
            (
                "Guest (Khách Vãng Lai)",
                ["guest", "gét", "khách vãng lai"],
                (
                    "https://tr.rbxcdn.com/30day-AvatarHeadshot-7362C04B4582E51D0C44053B5D3457D4-Png/150/150/AvatarHeadshot/noFilter"
                ),
                "Hình mẫu khách vãng lai huyền thoại trong lịch sử Roblox.",
            ),
            (
                "Robux (Tiền Tệ Roblox)",
                ["robux", "rbx", "rô bักซ", "tiền robux"],
                (
                    "https://images.rbxcdn.com/978000490b4d45cbe273f5a2e9b97a22"
                ),
                "Đơn vị tiền tệ chính thức và cao cấp trong thế giới Roblox.",
            ),
            (
                "Builderman (Nhà Sáng Lập)",
                ["builderman", "đại diện roblox", "nhà sáng lập"],
                (
                    "https://tr.rbxcdn.com/30day-AvatarHeadshot-95383AE11BD693B8933273D1E9DE80D9-Png/150/150/AvatarHeadshot/noFilter"
                ),
                "Nhà sáng lập và gương mặt đại diện quen thuộc của Roblox.",
            ),
        ],
    }

    item_name, accepted_answers, image_url, hint = random.choice(
        games_data[game_type]
    )
    active_trivia[channel_id] = accepted_answers

    game_titles = {
        "lienquan": "LIÊN QUÂN MOBILE",
        "valorant": "VALORANT",
        "roblox": "ROBLOX",
    }

    embed_msg = discord.Embed(
        title=(
            "🎮 MINIGAME: ĐOÁN NHÂN VẬT / VẬT PHẨM"
            f" ({game_titles[game_type]})"
        ),
        description=(
            "Quan sát hình ảnh bên dưới và đoán xem đây là nhân vật hoặc vật"
            " phẩm gì!\n\n*(Gõ đáp án tiếng Việt trực tiếp vào khung chat)*"
        ),
        color=0x3498DB,
    )
    embed_msg.set_image(url=image_url)
    embed_msg.add_field(name="💡 Gợi ý hệ thống", value=hint, inline=False)
    embed_msg.set_footer(text="Thời gian trả lời: 30 giây!")

    await interaction.response.edit_message(
        content=None, embed=embed_msg, view=None
    )

    def check(msg: discord.Message):
      return (
          msg.channel.id == channel_id
          and not msg.author.bot
          and msg.content.lower().strip() in active_trivia[channel_id]
      )

    try:
      msg = await bot.wait_for("message", timeout=30.0, check=check)
      if channel_id in active_trivia:
        del active_trivia[channel_id]

      success_embed = discord.Embed(
          title="🎉 CHÍNH XÁC!",
          description=(
              f"Chúc mừng **{msg.author.mention}** đã trả lời đúng nhanh nhất!"
              f"\n🎁 **Phần thưởng:** Được cộng thêm **5 lần đoán** cho các"
              f" vòng tiếp theo!"
          ),
          color=0x2ECC71,
      )
      success_embed.add_field(
          name="✨ Tên chính xác", value=f"**{item_name}**", inline=False
      )
      success_embed.set_image(url=image_url)
      await msg.channel.send(embed=success_embed)

    except asyncio.TimeoutError:
      if channel_id in active_trivia:
        del active_trivia[channel_id]

      timeout_embed = discord.Embed(
          title="⏰ HẾT GIỜ!",
          description=(
              "Tiếc quá, không có ai đưa ra đáp án chính xác trong thời gian"
              " quy định."
          ),
          color=0xE74C3C,
      )
      timeout_embed.add_field(
          name="🔑 Đáp án đúng là", value=f"**{item_name}**", inline=False
      )
      timeout_embed.set_image(url=image_url)
      await interaction.followup.send(embed=timeout_embed)


@bot.event
async def on_ready():
  bot.add_view(TicketPanelView())
  bot.add_view(CloseTicketView())
  bot.add_view(RoleView())
  bot.add_view(UnlockView())
  bot.add_view(SetupView())

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
          await channel.send(
              content=f"🎂 Chúc mừng sinh nhật {member.mention}!",
              embed=embed(
                  "CHÚC MỪNG SINH NHẬT!",
                  "Chúc bạn một ngày mới thật vui vẻ và hạnh phúc! 🎈",
                  0xFF73FA,
              ),
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


# --- CÁC LỆNH HỆ THỐNG VÀ TIỆN ÍCH ---


@bot.tree.command(name="help", description="Hiển thị bảng trợ giúp hệ thống")
async def help_command(interaction: discord.Interaction):
  em = discord.Embed(
      title="🌟 TRUNG TÂM ĐIỀU HÀNH & TRỢ GIÚP",
      description=(
          "Chào mừng bạn đến với hệ thống quản lý máy chủ tự động 24/7.\n"
          "Dưới đây là toàn bộ các danh mục lệnh hiện có:"
      ),
      color=0x5865F2,
  )

  if bot.user.avatar:
    em.set_thumbnail(url=bot.user.avatar.url)

  em.add_field(
      name="🕹️ **Minigame & Xác Thực**",
      value=(
          "• `/doantuong` - Chơi minigame đoán nhân vật game\n"
          "• `/drop_ff` - Minigame quay drop phần thưởng/nhân vật Free Fire\n"
          "• `/verify` - Gửi khung nút bấm xác nhận kênh\n"
          "• `/setup_verify` - Cài đặt Role trao khi người dùng Verify\n"
          "• `/setup_verify_code` - Đặt mã code để người dùng nhập khi verify"
      ),
      inline=False,
  )

  em.add_field(
      name="🛡️ **Quản Trị (Moderation)**",
      value=(
          "• `/ban [thành viên] [lý do]` - Khóa vĩnh viễn thành viên\n"
          "• `/mute [thành viên] [phút] [lý do]` - Cấm chat thành viên tạm thời"
      ),
      inline=False,
  )

  em.add_field(
      name="🎵 **Hệ Thống Âm Nhạc (Tốc độ cao)**",
      value=(
          "• `/play [tên/link]` - Phát nhạc lập tức\n"
          "• `/skip` - Bỏ qua bài hát\n"
          "• `/stop` - Dừng nhạc và thoát Voice"
      ),
      inline=False,
  )

  em.add_field(
      name="🛠️ **Quản Lý Hệ Thống**",
      value=(
          "• `/setup` - Thiết lập bảng cấu hình hệ thống\n"
          "• `/weekly_chatters` - Bảng vàng thống kê chat\n"
          "• `/birthday [DD/MM/YYYY]` - Đăng ký sinh nhật\n"
          "• `/setwelcome` - Cài đặt tin nhắn và ảnh chào mừng"
      ),
      inline=False,
  )

  em.set_footer(
      text=f"Yêu cầu bởi {interaction.user.name} • Hoạt động ổn định",
      icon_url=(
          interaction.user.display_avatar.url
          if interaction.user.display_avatar
          else None
      ),
  )
  em.timestamp = datetime.now()

  await interaction.response.send_message(embed=em, ephemeral=True)


@bot.tree.command(name="setup", description="Bảng cài đặt cấu hình Bot")
@app_commands.checks.has_permissions(administrator=True)
async def setup_command(interaction):
  values = "\n".join(
      f"`{key}`: `{get_config(key) or 'Chưa cài'}`"
      for key in DEFAULT_CONFIG
      if key.endswith("_id")
  )
  await interaction.response.send_message(
      embed=embed("BẢNG CẤU HÌNH BOT", values), view=SetupView(), ephemeral=True
  )


@bot.tree.command(
    name="setup_verify", description="Cài đặt Role sẽ được trao khi người dùng Verify"
)
@app_commands.checks.has_permissions(administrator=True)
async def setup_verify(interaction: discord.Interaction):
  view = SetupVerifyView()
  await interaction.response.send_message(
      "⚙️ Vui lòng chọn **Role** từ danh sách bên dưới để cài đặt cho lệnh"
      " Verify:",
      view=view,
      ephemeral=True,
  )


@bot.tree.command(
    name="setup_verify_code", description="Thay đổi mã code yêu cầu khi Verify"
)
@app_commands.describe(code="Mã code mới (ví dụ: KimNgoc2026)")
@app_commands.checks.has_permissions(administrator=True)
async def setup_verify_code(interaction: discord.Interaction, code: str):
  set_config("verify_code", code.strip())
  await interaction.response.send_message(
      f"✅ Đã cập nhật mã xác thực mới thành công: **`{code.strip()}`**",
      ephemeral=True,
  )


@bot.tree.command(
    name="verify", description="Gửi khung nút bấm xác nhận/mở khóa kênh"
)
@app_commands.checks.has_permissions(administrator=True)
async def verify_command(interaction: discord.Interaction):
  em = discord.Embed(
      title="🔐 XÁC NHẬN MỞ KHÓA MÁY CHỦ",
      description=(
          "Nhấn nút **🔓 Xác Nhận / Mở Khóa Kênh** bên dưới, sau đó nhập mã"
          " xác thực để nhận role mở khóa toàn bộ các kênh!"
      ),
      color=0x5865F2,
  )
  await interaction.channel.send(embed=em, view=UnlockView())
  await interaction.response.send_message(
      "✅ Đã gửi bảng Verify thành công!", ephemeral=True
  )


@bot.tree.command(
    name="ban", description="Khóa vĩnh viễn (ban) một thành viên khỏi máy chủ"
)
@app_commands.describe(
    member="Thành viên cần ban", reason="Lý do ban khỏi máy chủ"
)
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
            (
                f"👤 **Thành viên:** {member.mention} (`{member.id}`)\n🛡️"
                f" **Người thực hiện:** {interaction.user.mention}\n📝"
                f" **Lý do:** {reason}"
            ),
            0xED4245,
        )
    )
  except discord.Forbidden:
    await interaction.response.send_message(
        "❌ Bot thiếu quyền để ban thành viên này.", ephemeral=True
    )


@bot.tree.command(
    name="mute", description="Cấm chat (Timeout) một thành viên trong khoảng thời gian"
)
@app_commands.describe(
    member="Thành viên cần mute",
    minutes="Số phút muốn cấm chat",
    reason="Lý do mute",
)
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
            (
                f"👤 **Thành viên:** {member.mention}\n⏱️ **Thời gian:**"
                f" `{minutes} phút`\n🛡️ **Người thực hiện:**"
                f" {interaction.user.mention}\n📝 **Lý do:** {reason}"
            ),
            0xFEE75C,
        )
    )
  except discord.Forbidden:
    await interaction.response.send_message(
        "❌ Bot thiếu quyền để timeout thành viên này.", ephemeral=True
    )


@bot.tree.command(
    name="setwelcome",
    description=(
        "Chỉnh sửa lời chào mừng và tải ảnh trực tiếp từ thiết bị của bạn"
    ),
)
@app_commands.describe(image="Chọn file ảnh từ thiết bị của bạn (tùy chọn)")
@app_commands.checks.has_permissions(administrator=True)
async def setwelcome(
    interaction: discord.Interaction, image: discord.Attachment = None
):
  if image:
    set_config("welcome_image", image.url)

  await interaction.response.send_modal(WelcomeModal())


@bot.tree.command(
    name="weekly_chatters", description="Xem những người chat nhiều nhất trong tuần"
)
@app_commands.describe(limit="Số người muốn hiển thị, từ 1 đến 20")
async def weekly_chatters(
    interaction: discord.Interaction,
    limit: app_commands.Range[int, 1, 20] = 10,
):
  if not interaction.guild:
    return await interaction.response.send_message(
        "❌ Lệnh này chỉ dùng trong server.", ephemeral=True
    )

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
    return await interaction.response.send_message(
        "📊 Chưa có dữ liệu chat trong tuần này.", ephemeral=True
    )

  lines = [
      f"**{index}.** <@{user_id}> — `{msgs:,}` tin nhắn"
      for index, (user_id, msgs) in enumerate(rows, start=1)
  ]
  message = f"📊 **THỐNG KÊ TUẦN ({week_key})**\n\n" + "\n".join(lines)
  await interaction.response.send_message(
      embed=embed("THỐNG KÊ CHAT TUẦN", message, 0x5865F2)
  )


@bot.tree.command(name="birthday", description="Đăng ký sinh nhật")
@app_commands.describe(date="Định dạng DD/MM/YYYY")
async def birthday(interaction, date: str):
  try:
    parsed = datetime.strptime(date.strip(), "%d/%m/%Y")
  except ValueError:
    return await interaction.response.send_message(
        "❌ Dùng đúng định dạng DD/MM/YYYY.", ephemeral=True
    )

  user_id = str(interaction.user.id)
  formatted_date = parsed.strftime("%d/%m/%Y")

  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute(
      "INSERT OR REPLACE INTO birthdays (user_id, birthday) VALUES (?, ?)",
      (user_id, formatted_date),
  )
  conn.commit()
  conn.close()

  await interaction.response.send_message(
      f"✅ Đã lưu sinh nhật: **{formatted_date}**", ephemeral=True
  )


@bot.tree.command(
    name="doantuong",
    description="Mở bảng chọn tựa game để chơi minigame đoán nhân vật",
)
async def doantuong(interaction: discord.Interaction):
  view = GameSelectView()
  embed_msg = discord.Embed(
      title="🕹️ HỆ THỐNG MINIGAME ĐOÁN NHÂN VẬT",
      description=(
          "Vui lòng bấm vào nút tương ứng bên dưới để chọn tựa game bạn muốn"
          " tham gia thử thách:"
      ),
      color=0x9B59B6,
  )
  await interaction.response.send_message(
      embed=embed_msg, view=view, ephemeral=False
  )


@bot.tree.command(
    name="drop_ff",
    description="Minigame đoán nhân vật và vật phẩm Free Fire cực vui",
)
async def drop_ff(interaction: discord.Interaction):
  channel_id = interaction.channel.id

  if channel_id in active_drop_ff:
    return await interaction.response.send_message(
        "⚠️ Đã có minigame Free Fire đang diễn ra ở kênh này rồi!",
        ephemeral=True,
    )

  freefire_items = [
      (
          "Alok (Drop the Beat)",
          ["alok", "a lốc", "dj alok"],
          (
              "https://static.wikia.nocookie.net/freefire/images/a/a4/Alok_avatar.png"
          ),
          "Nhân vật tạo hào quang hồi máu và tăng tốc độ di chuyển.",
      ),
      (
          "Moco (Hacker's Eye)",
          ["moco", "mô cô"],
          (
              "https://static.wikia.nocookie.net/freefire/images/8/8f/Moco_avatar.png"
          ),
          "Nữ hacker thông thái đánh dấu vị trí kẻ địch khi bắn trúng.",
      ),
      (
          "Gloo Wall",
          ["bom keo", "gloo wall", "keo", "tường keo"],
          (
              "https://static.wikia.nocookie.net/freefire/images/6/67/Gloo_Wall.png"
          ),
          "Vật phẩm sinh tồn dùng để che chắn đạn tức thời.",
      ),
      (
          "Chrono (Time Turner)",
          ["chrono", "cr7", "cờ rốt nô"],
          (
              "https://static.wikia.nocookie.net/freefire/images/7/7b/Chrono_avatar.png"
          ),
          "Tạo ra khiên năng lượng bất tử chặn sát thương.",
      ),
      (
          "Kelly (Dash)",
          ["kelly", "nữ vận động viên", "kê li"],
          (
              "https://static.wikia.nocookie.net/freefire/images/3/36/Kelly_avatar.png"
          ),
          "Nữ vận động viên điền kinh có tốc độ chạy nước rút đỉnh cao.",
      ),
      (
          "Hayato (Bushido)",
          ["hayato", "ha ya to"],
          (
              "https://static.wikia.nocookie.net/freefire/images/c/c5/Hayato_avatar.png"
          ),
          "Samurai huyền thoại tăng xuyên giáp khi máu thấp.",
      ),
      (
          "K (Master of All)",
          ["k", "captain k", "giáo sư k"],
          (
              "https://static.wikia.nocookie.net/freefire/images/4/4c/K_avatar.png"
          ),
          "Giáo sư tâm lý học tự động hồi máu và EP cho đồng đội.",
      ),
      (
          "Skyler (Riptide Rhythm)",
          ["skyler", "sky ler"],
          (
              "https://static.wikia.nocookie.net/freefire/images/2/22/Skyler_avatar.png"
          ),
          "Phóng sóng âm phá hủy bom keo của kẻ địch nhanh chóng.",
      ),
  ]

  item_name, accepted_answers, image_url, hint = random.choice(freefire_items)
  active_drop_ff[channel_id] = accepted_answers

  embed_msg = discord.Embed(
      title="🔥 MINIGAME DROP FREE FIRE",
      description=(
          "Quan sát hình ảnh item/nhân vật Free Fire bên dưới và đoán tên!\n\n*(Gõ"
          " đáp án tiếng Việt trực tiếp vào khung chat)*"
      ),
      color=0xE67E22,
  )
  embed_msg.set_image(url=image_url)
  embed_msg.add_field(name="💡 Gợi ý hệ thống", value=hint, inline=False)
  embed_msg.set_footer(text="Thời gian trả lời: 30 giây!")

  await interaction.response.send_message(embed=embed_msg)

  def check(msg: discord.Message):
    return (
        msg.channel.id == channel_id
        and not msg.author.bot
        and msg.content.lower().strip() in active_drop_ff[channel_id]
    )

  try:
    msg = await bot.wait_for("message", timeout=30.0, check=check)
    if channel_id in active_drop_ff:
      del active_drop_ff[channel_id]

    success_embed = discord.Embed(
        title="🎉 CHÍNH XÁC!",
        description=(
            f"Chúc mừng **{msg.author.mention}** đã trả lời đúng nhanh nhất!"
        ),
        color=0x2ECC71,
    )
    success_embed.add_field(
        name="✨ Tên chính xác", value=f"**{item_name}**", inline=False
    )
    success_embed.set_image(url=image_url)
    await msg.channel.send(embed=success_embed)

  except asyncio.TimeoutError:
    if channel_id in active_drop_ff:
      del active_drop_ff[channel_id]

    timeout_embed = discord.Embed(
        title="⏰ HẾT GIỜ!",
        description=(
            "Tiếc quá, không có ai đưa ra đáp án chính xác trong thời gian"
            " quy định."
        ),
        color=0xE74C3C,
    )
    timeout_embed.add_field(
        name="🔑 Đáp án đúng là", value=f"**{item_name}**", inline=False
    )
    timeout_embed.set_image(url=image_url)
    await interaction.followup.send(embed=timeout_embed)


# --- CÁC LỆNH ÂM NHẠC (TỐC ĐỘ CAO VỚI WAVELINK) ---


@bot.tree.command(
    name="play", description="Phát nhạc cực nhanh từ YouTube hoặc Spotify"
)
@app_commands.describe(search="Tên bài hát hoặc Link")
async def play(interaction: discord.Interaction, search: str):
  if not interaction.user.voice:
    return await interaction.response.send_message(
        "❌ Bạn cần vào phòng Voice trước!", ephemeral=True
    )

  await interaction.response.defer()

  vc: wavelink.Player = interaction.guild.voice_client
  if not vc:
    vc = await interaction.user.voice.channel.connect(
        cls=wavelink.Player, self_deaf=True
    )

  tracks = await wavelink.Playable.search(search)
  if not tracks:
    return await interaction.followup.send("❌ Không tìm thấy bài hát nào!")

  track = tracks[0]
  await vc.queue.put_wait(track)
  await interaction.followup.send(f"🎵 Đã phát/thêm vào hàng đợi: **{track.title}**")

  if not vc.playing:
    await vc.play(vc.queue.get())


@bot.tree.command(name="skip", description="Bỏ qua bài hát hiện tại")
async def skip(interaction: discord.Interaction):
  vc: wavelink.Player = interaction.guild.voice_client
  if not vc or not vc.playing:
    return await interaction.response.send_message(
        "❌ Không có bài hát nào đang phát.", ephemeral=True
    )
  await vc.stop()
  await interaction.response.send_message("⏭️ Đã bỏ qua bài hát hiện tại!")


@bot.tree.command(name="stop", description="Dừng nhạc và thoát Voice")
async def stop(interaction: discord.Interaction):
  vc: wavelink.Player = interaction.guild.voice_client
  if not vc:
    return await interaction.response.send_message(
        "❌ Bot không ở trong phòng Voice.", ephemeral=True
    )
  await vc.disconnect()
  await interaction.response.send_message("⏹️ Đã dừng nhạc và ngắt kết nối!")


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
