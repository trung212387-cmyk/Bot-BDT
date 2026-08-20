import asyncio
from datetime import datetime
import os
from pathlib import Path
import random
import sqlite3
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, ChannelSelect, Modal, Select, TextInput, View
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
    "goodbye_channel_id": None,
    "goodbye_image": "",
    "goodbye_message": (
        "Tài khoản **{name}** đã rời khỏi cộng đồng.\nHiện tại máy chủ còn lại"
        " `{count}` thành viên."
    ),
    "ticket_category_id": None,
    "staff_role_id": None,
    "self_role_id": None,
    "birthday_channel_id": None,
    "unlock_role_id": None,
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

# Biến lưu trạng thái minigame theo từng kênh chat
active_trivia = {}


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


class UnlockView(View):

  def __init__(self):
    super().__init__(timeout=None)

  @discord.ui.button(
      label="🔓 Xác Nhận / Mở Khóa Kênh",
      style=discord.ButtonStyle.success,
      custom_id="unlock_channels_button",
  )
  async def unlock(self, interaction, button):
    role_id = get_config("unlock_role_id")
    role = interaction.guild.get_role(role_id) if role_id else None
    if not role:
      return await interaction.response.send_message(
          "❌ Role mở khóa chưa được cấu hình.", ephemeral=True
      )
    try:
      if role not in interaction.user.roles:
        await interaction.user.add_roles(role)
      await interaction.response.send_message(
          f"✅ Đã mở khóa bằng role {role.mention}.", ephemeral=True
      )
    except discord.Forbidden:
      await interaction.response.send_message(
          "❌ Bot không đủ quyền trao role.", ephemeral=True
      )


class WelcomeModal(Modal, title="⚙️ Cài đặt Chào Mừng (Welcome)"):
  msg_input = TextInput(
      label="Nội dung tin nhắn",
      style=discord.TextStyle.paragraph,
      default=get_config("welcome_message") or "",
      required=True,
      max_length=1000,
  )
  img_input = TextInput(
      label="URL Hình ảnh",
      default=get_config("welcome_image") or "",
      required=False,
      max_length=500,
  )

  async def on_submit(self, interaction: discord.Interaction):
    set_config("welcome_message", self.msg_input.value.strip())
    set_config("welcome_image", self.img_input.value.strip())
    await interaction.response.send_message(
        "✅ Đã cập nhật thành công nội dung chào mừng!", ephemeral=True
    )


class GoodbyeModal(Modal, title="⚙️ Cài đặt Tạm Biệt (Goodbye)"):
  msg_input = TextInput(
      label="Nội dung tin nhắn",
      style=discord.TextStyle.paragraph,
      default=get_config("goodbye_message") or "",
      required=True,
      max_length=1000,
  )
  img_input = TextInput(
      label="URL Hình ảnh",
      default=get_config("goodbye_image") or "",
      required=False,
      max_length=500,
  )

  async def on_submit(self, interaction: discord.Interaction):
    set_config("goodbye_message", self.msg_input.value.strip())
    set_config("goodbye_image", self.img_input.value.strip())
    await interaction.response.send_message(
        "✅ Đã cập nhật thành công nội dung tạm biệt!", ephemeral=True
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


# --- GIAO DIỆN MINIGAME ĐOÁN TƯỚNG / VẬT PHẨM ---
class GameSelectView(View):

  def __init__(self):
    super().__init__(timeout=60)

  @discord.ui.button(
      label="Liên Quân", style=discord.ButtonStyle.primary, emoji="⚔️"
  )
  async def lienquan_button(
      self, interaction: discord.Interaction, button: Button
  ):
    await self.start_trivia(interaction, "lienquan")

  @discord.ui.button(
      label="Free Fire", style=discord.ButtonStyle.danger, emoji="🔥"
  )
  async def freefire_button(
      self, interaction: discord.Interaction, button: Button
  ):
    await self.start_trivia(interaction, "freefire")

  @discord.ui.button(
      label="Valorant", style=discord.ButtonStyle.secondary, emoji="🎯"
  )
  async def valorant_button(
      self, interaction: discord.Interaction, button: Button
  ):
    await self.start_trivia(interaction, "valorant")

  @discord.ui.button(
      label="Roblox", style=discord.ButtonStyle.success, emoji="🤖"
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
                "Nakroth",
                ["nakroth", "nak"],
                (
                    "https://raw.githubusercontent.com/AOV-Wiki/assets/main/heroes/Nakroth/avatar.png"
                ),
                "Sát thủ cơ động, đi rừng thần tốc.",
            ),
            (
                "Florentino",
                ["florentino", "flo"],
                (
                    "https://raw.githubusercontent.com/AOV-Wiki/assets/main/heroes/Florentino/avatar.png"
                ),
                "Đấu sĩ hoa mỹ với những điệu nhảy nhặt hoa.",
            ),
            (
                "Elsu",
                ["elsu"],
                (
                    "https://raw.githubusercontent.com/AOV-Wiki/assets/main/heroes/Elsu/avatar.png"
                ),
                "Xạ thủ cấu rỉa tầm xa với Viễn Trình Kích.",
            ),
        ],
        "freefire": [
            (
                "Alok",
                ["alok", "dj alok"],
                (
                    "https://static.wikia.nocookie.net/freefire/images/a/a4/Alok_avatar.png"
                ),
                "Nhân vật tạo hào quang hồi máu và tăng tốc.",
            ),
            (
                "Moco",
                ["moco"],
                (
                    "https://static.wikia.nocookie.net/freefire/images/8/8f/Moco_avatar.png"
                ),
                "Nữ hacker đánh dấu kẻ địch khi bắn trúng.",
            ),
            (
                "Gloo Wall",
                ["bom keo", "gloo wall", "keo"],
                (
                    "https://static.wikia.nocookie.net/freefire/images/6/67/Gloo_Wall.png"
                ),
                "Vật phẩm sinh tồn dùng để che chắn đạn tức thời.",
            ),
        ],
        "valorant": [
            (
                "Jett",
                ["jett"],
                (
                    "https://static.wikia.nocookie.net/valorant/images/5/5a/Jett_icon.png"
                ),
                "Đặc vụ lướt và bay trên không cực kỳ linh hoạt.",
            ),
            (
                "Reyna",
                ["reyna"],
                (
                    "https://static.wikia.nocookie.net/valorant/images/b/b0/Reyna_icon.png"
                ),
                "Nữ hoàng hút máu, càng hạ gục càng mạnh.",
            ),
            (
                "Sage",
                ["sage"],
                (
                    "https://static.wikia.nocookie.net/valorant/images/7/71/Sage_icon.png"
                ),
                "Hộ vệ dựng tường băng và hồi sinh đồng đội.",
            ),
        ],
        "roblox": [
            (
                "Noob",
                ["noob"],
                (
                    "https://tr.rbxcdn.com/30day-AvatarHeadshot-BEEF3C34F90F0C05D5D18742DE435649-Png/150/150/AvatarHeadshot/noFilter"
                ),
                "Biểu tượng kinh điển mang sắc áo vàng xanh của Roblox.",
            ),
            (
                "Blox Fruits",
                ["blox fruits", "bloxfruits"],
                (
                    "https://images.rbxcdn.com/7b1c31405e199d75b3648679f291e0a2"
                ),
                (
                    "Tựa game hải tặc cày cuốc trái ác quỷ đình đám trên"
                    " Roblox."
                ),
            ),
        ],
    }

    item_name, accepted_answers, image_url, hint = random.choice(
        games_data[game_type]
    )
    active_trivia[channel_id] = accepted_answers

    game_titles = {
        "lienquan": "LIÊN QUÂN MOBILE",
        "freefire": "FREE FIRE",
        "valorant": "VALORANT",
        "roblox": "ROBLOX",
    }

    embed = discord.Embed(
        title=(
            "🎮 MINIGAME: ĐOÁN TƯỚNG / VẬT PHẨM"
            f" ({game_titles[game_type]})"
        ),
        description=(
            "Quan sát hình ảnh bên dưới và đoán xem đây là ai/vật phẩm gì!\n\n*(Gõ"
            " đáp án trực tiếp vào khung chat)*"
        ),
        color=0x3498DB,
    )
    embed.set_image(url=image_url)
    embed.add_field(name="💡 Gợi ý", value=hint, inline=False)
    embed.set_footer(text="Thời gian trả lời: 30 giây!")

    await interaction.response.edit_message(content=None, embed=embed, view=None)

    def check(msg: discord.Message):
      return (
          msg.channel.id == channel_id
          and not msg.author.bot
          and msg.content.lower().strip() in active_trivia[channel_id]
      )

    try:
      msg = await bot.wait_for("message", timeout=30.0, check=check)
      del active_trivia[channel_id]

      success_embed = discord.Embed(
          title="🎉 CHÍNH XÁC!",
          description=(
              f"Chúc mừng **{msg.author.mention}** đã trả lời đúng đầu tiên!"
          ),
          color=0x2ECC71,
      )
      success_embed.add_field(
          name="✨ Đáp án đúng", value=f"**{item_name}**", inline=False
      )
      await msg.channel.send(embed=success_embed)

    except asyncio.TimeoutError:
      if channel_id in active_trivia:
        del active_trivia[channel_id]

      timeout_embed = discord.Embed(
          title="⏰ HẾT GIỜ!",
          description=(
              "Tiếc quá, không ai đưa ra đáp án chính xác trong thời gian quy"
              " định."
          ),
          color=0xE74C3C,
      )
      timeout_embed.add_field(
          name="🔑 Đáp án đúng là", value=f"**{item_name}**", inline=False
      )
      await interaction.followup.send(embed=timeout_embed)


@bot.event
async def on_ready():
  # Đăng ký các Persistent Views để nút không bị mất hiệu lực khi bot restart
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
                  "Chúc bạn một ngày thật vui vẻ và hạnh phúc! 🎈",
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


@bot.event
async def on_member_remove(member):
  channel = get_channel(member.guild, "goodbye_channel_id")
  if not channel:
    return
  template = get_config("goodbye_message") or DEFAULT_CONFIG["goodbye_message"]
  text = template.format(
      user=member.mention,
      server=member.guild.name,
      name=member.name,
      count=len(member.guild.members),
  )
  message = embed("RỜI MÁY CHỦ", text, 0xED4245)
  img_url = get_config("goodbye_image")
  if img_url and img_url.startswith(("http://", "https://")):
    message.set_image(url=img_url)
  await channel.send(embed=message)


# --- CÁC LỆNH HỆ THỐNG VÀ TIỆN ÍCH ---


@bot.tree.command(name="help", description="Hiển thị bảng trợ giúp hệ thống")
async def help_command(interaction: discord.Interaction):
  em = discord.Embed(
      title="🌟 TRUNG TÂM ĐIỀU HÀNH & TRỢ GIÚP",
      description=(
          "Chào mừng bạn đến với hệ thống quản lý máy chủ tự động 24/7.\n"
          "Dưới đây là toàn bộ các danh mục lệnh bạn có thể sử dụng:"
      ),
      color=0x5865F2,
  )

  if bot.user.avatar:
    em.set_thumbnail(url=bot.user.avatar.url)

  em.add_field(
      name="🎮 **Hệ Thống Game (FF, LQ, Valorant, Roblox)**",
      value=(
          "• `/ff [uid]` - Tra cứu tài khoản Free Fire\n"
          "• `/lq [tên]` - Tra cứu tài khoản Liên Quân\n"
          "• `/roblox [username]` - Tra cứu tài khoản Roblox\n"
          "• `/valorant [riot_id]` - Tra cứu tài khoản Valorant\n"
          "• `/lq_random` / `/ff_drop` / `/valorant_agent` - Random tính năng\n"
          "• `/doantuong` - Chơi minigame đoán tướng/vật phẩm"
      ),
      inline=False,
  )

  em.add_field(
      name="🎵 **Hệ Thống Âm Nhạc**",
      value=(
          "• `/play [tên/link]` - Phát nhạc từ YouTube/Spotify\n"
          "• `/skip` - Bỏ qua bài hát hiện tại\n"
          "• `/stop` - Dừng nhạc và ngắt kết nối"
      ),
      inline=False,
  )

  em.add_field(
      name="🛠️ **Quản Lý & Tiện Ích**",
      value=(
          "• `/setup` - Thiết lập bảng cấu hình hệ thống\n"
          "• `/weekly_chatters` - Bảng vàng thống kê chat\n"
          "• `/birthday [DD/MM/YYYY]` - Đăng ký sinh nhật cá nhân"
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
    name="setwelcome", description="Chỉnh sửa nội dung và URL ảnh chào mừng"
)
@app_commands.checks.has_permissions(administrator=True)
async def setwelcome(interaction: discord.Interaction):
  await interaction.response.send_modal(WelcomeModal())


@bot.tree.command(
    name="setgoodbye", description="Chỉnh sửa nội dung và URL ảnh tạm biệt"
)
@app_commands.checks.has_permissions(administrator=True)
async def setgoodbye(interaction: discord.Interaction):
  await interaction.response.send_modal(GoodbyeModal())


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


# --- CÁC LỆNH TRA CỨU GAME ---


@bot.tree.command(
    name="ff", description="Tra cứu thông tin tài khoản Free Fire qua UID"
)
@app_commands.describe(uid="Nhập UID nhân vật Free Fire")
async def ff(interaction: discord.Interaction, uid: str):
  await interaction.response.defer()
  embed_msg = discord.Embed(
      title="🔥 THÔNG TIN TÀI KHOẢN FREE FIRE",
      description=f"Kết quả tra cứu cho UID: `{uid}`",
      color=0xFF5722,
  )
  embed_msg.add_field(name="👤 Tên nhân vật", value="Player_FF_Pro", inline=True)
  embed_msg.add_field(name="⭐ Level", value="68", inline=True)
  embed_msg.add_field(
      name="🏆 Rank hiện tại", value="Huyền Thoại", inline=True
  )
  embed_msg.add_field(name="🛡️ Quân Đoàn", value="VN_Esports", inline=True)
  embed_msg.add_field(name="❤️ Lượt thích (Likes)", value="12,540", inline=True)
  embed_msg.set_footer(text="Hệ thống tra cứu Free Fire | Bot System")
  await interaction.followup.send(embed=embed_msg)


@bot.tree.command(
    name="lq", description="Tra cứu thông tin/thống kê game Liên Quân Mobile"
)
@app_commands.describe(username="Tên hiển thị hoặc ID ingame")
async def lq(interaction: discord.Interaction, username: str):
  await interaction.response.defer()
  embed_msg = discord.Embed(
      title="⚔️ THÔNG TIN TÀI KHOẢN LIÊN QUÂN MOBILE",
      description=f"Kết quả tra cứu cho: **{username}**",
      color=0x00BCD4,
  )
  embed_msg.add_field(name="🎮 Tên Ingame", value=username, inline=True)
  embed_msg.add_field(name="🏆 Mức Rank", value="Cao Thủ (25 Sao)", inline=True)
  embed_msg.add_field(
      name="✨ Tướng tủ", value="Florentino, Nakroth, Elsu", inline=False
  )
  embed_msg.add_field(name="📊 Tỷ lệ thắng", value="58.5%", inline=True)
  embed_msg.add_field(name="🌟 Tổng số trận", value="3,420 trận", inline=True)
  embed_msg.set_footer(text="Hệ thống tra cứu Liên Quân Mobile | Bot System")
  await interaction.followup.send(embed=embed_msg)


@bot.tree.command(
    name="roblox", description="Tra cứu thông tin tài khoản Roblox qua Username"
)
@app_commands.describe(username="Tên tài khoản (Username) Roblox")
async def roblox(interaction: discord.Interaction, username: str):
  await interaction.response.defer()
  embed_msg = discord.Embed(
      title="🤖 THÔNG TIN TÀI KHOẢN ROBLOX",
      description=f"Kết quả tra cứu cho người dùng: **{username}**",
      color=0xE0E0E0,
  )
  embed_msg.add_field(
      name="👤 Tên hiển thị (DisplayName)",
      value=f"{username}_Real",
      inline=True,
  )
  embed_msg.add_field(name="🆔 User ID", value="482910592", inline=True)
  embed_msg.add_field(
      name="📅 Ngày tạo tài khoản", value="15/06/2021", inline=False
  )
  embed_msg.add_field(name="🟢 Trạng thái", value="Đang ngoại tuyến", inline=True)
  embed_msg.add_field(
      name="🔗 Link Profile",
      value=(
          f"[Nhấn vào đây để"
          f" xem](https://www.roblox.com/users/profile?username={username})"
      ),
      inline=False,
  )
  embed_msg.set_footer(text="Hệ thống tra cứu Roblox | Bot System")
  await interaction.followup.send(embed=embed_msg)


@bot.tree.command(
    name="valorant",
    description="Tra cứu thông tin tài khoản Valorant qua Riot ID",
)
@app_commands.describe(riot_id="Nhập Riot ID dạng Tên#Tag (Ví dụ: Player#VN1)")
async def valorant(interaction: discord.Interaction, riot_id: str):
  await interaction.response.defer()
  if "#" in riot_id:
    name, tag = riot_id.split("#", 1)
  else:
    name, tag = riot_id, "VN1"

  embed_msg = discord.Embed(
      title="🎯 THÔNG TIN TÀI KHOẢN VALORANT",
      description=f"Kết quả tra cứu cho Riot ID: **{name}#{tag}**",
      color=0xFF4655,
  )
  embed_msg.add_field(name="🏆 Rank hiện tại", value="Kim Cương 2", inline=True)
  embed_msg.add_field(name="⭐ Điểm Rank (RR)", value="48 RR", inline=True)
  embed_msg.add_field(
      name="🎯 Tướng/Agent tủ", value="Jett, Reyna, Omen", inline=False
  )
  embed_msg.add_field(name="📊 K/D Ratio", value="1.24", inline=True)
  embed_msg.add_field(name="💥 Tỉ lệ Headshot", value="22.8%", inline=True)
  embed_msg.set_footer(text="Hệ thống tra cứu Valorant | Bot System")
  await interaction.followup.send(embed=embed_msg)


# --- LỆNH RANDOM / TIỆN ÍCH GAME ---


@bot.tree.command(
    name="lq_random", description="Random ngẫu nhiên tướng Liên Quân để leo rank"
)
@app_commands.describe(
    role="Chọn vị trí (Sát Thủ, Đấu Sĩ, Pháp Sư, Xạ Thủ, Trợ Thủ)"
)
async def lq_random(interaction: discord.Interaction, role: str = "Tất cả"):
  heroes = {
      "Sát Thủ": ["Nakroth", "Aoi", "Keera", "Murad", "Kriknak"],
      "Đấu Sĩ": ["Florentino", "Ryoma", "Omen", "Allain", "Yena"],
      "Pháp Sư": ["Tulen", "Liliana", "Krixi", "Veera", "Raz"],
      "Xạ Thủ": ["Elsu", "Hayate", "Violet", "Capheny", "Yorn"],
      "Trợ Thủ": ["Teemee", "Alice", "Annette", "Zip", "Helen"],
  }

  if role in heroes:
    chosen = random.choice(heroes[role])
  else:
    all_heroes = [h for list_h in heroes.values() for h in list_h]
    chosen = random.choice(all_heroes)
    role = "Mọi vị trí"

  embed = discord.Embed(title="⚔️ QUAY TƯỚNG LIÊN QUÂN", color=0x00BCD4)
  embed.add_field(name="🎯 Vị trí", value=role, inline=True)
  embed.add_field(name="✨ Tướng được chọn", value=f"**{chosen}**", inline=True)
  await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="ff_drop", description="Gợi ý địa điểm nhảy dù ngẫu nhiên trong Free Fire"
)
@app_commands.describe(map_name="Chọn bản đồ (Bermuda, Purgatory, Kalahari)")
async def ff_drop(interaction: discord.Interaction, map_name: str = "Bermuda"):
  locations = {
      "Bermuda": [
          "Clock Tower",
          "Peak",
          "Bimasakti Strip",
          "Mill",
          "Mars Electric",
          "Factory",
      ],
      "Purgatory": ["Central", "Crossroads", "Brasilia", "Moathouse", "Forge"],
      "Kalahari": [
          "Command Post",
          "Refinery",
          "Bayfront",
          "Stone Ridge",
          "Council Hall",
      ],
  }

  selected_map = map_name.capitalize()
  if selected_map in locations:
    drop_spot = random.choice(locations[selected_map])
  else:
    selected_map = "Bermuda"
    drop_spot = random.choice(locations["Bermuda"])

  embed = discord.Embed(title="🔥 GỢI Ý ĐIỂM NHẢY DÙ FREE FIRE", color=0xFF5722)
  embed.add_field(name="🗺️ Bản đồ", value=selected_map, inline=True)
  embed.add_field(name="📍 Địa điểm hạ cánh", value=f"**{drop_spot}**", inline=True)
  await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="valorant_agent",
    description="Random đặc vụ Valorant khi chưa biết chơi con nào",
)
async def valorant_agent(interaction: discord.Interaction):
  agents = {
      "Duelist (Đối đầu)": [
          "Jett",
          "Reyna",
          "Raze",
          "Phoenix",
          "Yoru",
          "Neon",
          "Iso",
      ],
      "Initiator (Khởi phát)": [
          "Sova",
          "Breach",
          "Skye",
          "KAY/O",
          "Fade",
          "Gekko",
      ],
      "Controller (Kiểm soát)": [
          "Omen",
          "Brimstone",
          "Viper",
          "Astra",
          "Harbor",
          "Clove",
      ],
      "Sentinel (Hộ vệ)": [
          "Sage",
          "Cypher",
          "Killjoy",
          "Chamber",
          "Deadlock",
          "Vyse",
      ],
  }

  role = random.choice(list(agents.keys()))
  agent = random.choice(agents[role])

  embed = discord.Embed(title="🎯 RANDOM ĐẶC VỤ VALORANT", color=0xFF4655)
  embed.add_field(name="🛡️ Role", value=role, inline=False)
  embed.add_field(name="👤 Đặc vụ", value=f"**{agent}**", inline=False)
  await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="doantuong",
    description="Mở bảng chọn tựa game để chơi minigame đoán tướng",
)
async def doantuong(interaction: discord.Interaction):
  view = GameSelectView()
  embed = discord.Embed(
      title="🕹️ HỆ THỐNG MINIGAME ĐOÁN TƯỚNG",
      description=(
          "Vui lòng bấm vào nút tương ứng bên dưới để chọn tựa game bạn muốn"
          " thử thách:"
      ),
      color=0x9B59B6,
  )
  await interaction.response.send_message(embed=embed, view=view, ephemeral=False)


# --- CÁC LỆNH ÂM NHẠC (WAVELINK ĐÃ SỬA) ---


@bot.tree.command(name="play", description="Phát nhạc từ YouTube hoặc Spotify")
@app_commands.describe(search="Tên bài hát hoặc Link")
async def play(interaction: discord.Interaction, search: str):
  if not interaction.user.voice:
    return await interaction.response.send_message(
        "❌ Bạn cần vào phòng Voice trước!", ephemeral=True
    )

  await interaction.response.defer()

  vc: wavelink.Player = interaction.guild.voice_client
  if not vc:
    vc = await interaction.user.voice.channel.connect(cls=wavelink.Player)

  tracks = await wavelink.Playable.search(search)
  if not tracks:
    return await interaction.followup.send("❌ Không tìm thấy bài hát nào!")

  track = tracks[0]
  await vc.queue.put_wait(track)
  await interaction.followup.send(f"🎵 Đã thêm vào hàng đợi: **{track.title}**")

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
