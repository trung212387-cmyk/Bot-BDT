#!/usr/init/env python3
"""
CTDOTEAM - Discord Bot (Auto Quest + Token Checker + Setup Welcome, Birthday, Verify, Agree & Help)
Created by ph.huyy | Vinh Phuc, Viet Nam
"""

import requests
import time
import json
import random
import sys
import os
import re
import base64
import traceback
import string
from datetime import datetime, timezone
from typing import Optional

# Thư viện Discord.py
import discord
from discord.ext import commands, tasks
from discord import app_commands

# ── Config ─────────────────────────────────────────────────────────────────────
API_BASE = "https://discord.com/api/v9"
POLL_INTERVAL = 60          
HEARTBEAT_INTERVAL = 20     
AUTO_ACCEPT = True          
LOG_PROGRESS = True
DEBUG = True                

SUPPORTED_TASKS = [
    "WATCH_VIDEO",
    "PLAY_ON_DESKTOP",
    "STREAM_ON_DESKTOP",
    "PLAY_ACTIVITY",
    "WATCH_VIDEO_ON_MOBILE",
]


# ── Logging ────────────────────────────────────────────────────────────────────
class Colors:
    RESET  = "\033[0m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"


def log(msg: str, level: str = "info"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {
        "info":     f"{Colors.CYAN}[INFO]{Colors.RESET}",
        "ok":       f"{Colors.GREEN}[  OK]{Colors.RESET}",
        "warn":     f"{Colors.YELLOW}[WARN]{Colors.RESET}",
        "error":    f"{Colors.RED}[ ERR]{Colors.RESET}",
        "progress": f"{Colors.DIM}[PROG]{Colors.RESET}",
        "debug":    f"{Colors.DIM}[DBG ]{Colors.RESET}",
    }.get(level, f"[{level.upper()}]")

    if level == "debug" and not DEBUG:
        return
    if LOG_PROGRESS or level != "progress":
        print(f"{Colors.DIM}{ts}{Colors.RESET} {prefix} {msg}")


# ── Build number fetcher & Quest API Logic ──────────────────────────────────────
def fetch_latest_build_number() -> int:
    FALLBACK = 504649
    try:
        log("Đang lấy build number mới nhất từ Discord...", "info")
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        r = requests.get("https://discord.com/app", headers={"User-Agent": ua}, timeout=15)
        if r.status_code != 200:
            return FALLBACK
        scripts = re.findall(r'/assets/([a-f0-9]+)\.js', r.text)
        if not scripts:
            scripts_alt = re.findall(r'src="(/assets/[^"]+\.js)"', r.text)
            scripts = [s.split('/')[-1].replace('.js', '') for s in scripts_alt]
        if not scripts:
            return FALLBACK
        for asset_hash in scripts[-5:]:
            try:
                ar = requests.get(f"https://discord.com/assets/{asset_hash}.js", headers={"User-Agent": ua}, timeout=15)
                m = re.search(r'buildNumber["\s:]+["\s]*(\d{5,7})', ar.text)
                if m:
                    return int(m.group(1))
            except Exception:
                continue
        return FALLBACK
    except Exception:
        return FALLBACK


def make_super_properties(build_number: int) -> str:
    obj = {
        "os": "Windows", "browser": "Discord Client", "release_channel": "stable",
        "client_version": "1.0.9175", "os_version": "10.0.26100", "os_arch": "x64",
        "app_arch": "x64", "system_locale": "en-US",
        "browser_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36",
        "browser_version": "32.2.7", "client_build_number": build_number, "native_build_number": 59498,
        "client_event_source": None,
    }
    return base64.b64encode(json.dumps(obj).encode()).decode()


class DiscordAPI:
    def __init__(self, token: str, build_number: int):
        self.token = token
        self.session = requests.Session()
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
        sp = make_super_properties(build_number)
        self.session.headers.update({
            "Authorization": token, "Content-Type": "application/json", "Accept": "*/*",
            "User-Agent": ua, "X-Super-Properties": sp, "X-Discord-Locale": "en-US",
            "X-Discord-Timezone": "Asia/Ho_Chi_Minh", "Origin": "https://discord.com",
            "Referer": "https://discord.com/channels/@me",
        })

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.session.get(f"{API_BASE}{path}", **kwargs)

    def post(self, path: str, payload: Optional[dict] = None, **kwargs) -> requests.Response:
        return self.session.post(f"{API_BASE}{path}", json=payload, **kwargs)

    def validate_token(self) -> dict:
        """Kiểm tra token và trả về thông tin tài khoản nếu hợp lệ"""
        try:
            r = self.get("/users/@me")
            if r.status_code == 200:
                return r.json()
            return {}
        except Exception:
            return {}


# Helper functions cho Quest
def _get(d: Optional[dict], *keys):
    if d is None: return None
    for k in keys:
        if k in d: return d[k]
    return None

def get_task_config(quest: dict) -> Optional[dict]:
    return _get(quest.get("config", {}), "taskConfig", "task_config", "taskConfigV2", "task_config_v2")

def is_completable(quest: dict) -> bool:
    tc = get_task_config(quest)
    if not tc or "tasks" not in tc: return False
    return any(tc["tasks"].get(t) is not None for t in SUPPORTED_TASKS)

def is_enrolled(quest: dict) -> bool:
    us = _get(quest, "userStatus", "user_status")
    return bool(_get(us if isinstance(us, dict) else {}, "enrolledAt", "enrolled_at"))

def is_completed(quest: dict) -> bool:
    us = _get(quest, "userStatus", "user_status")
    return bool(_get(us if isinstance(us, dict) else {}, "completedAt", "completed_at"))

def get_task_type(quest: dict) -> Optional[str]:
    tc = get_task_config(quest)
    if not tc or "tasks" not in tc: return None
    for t in SUPPORTED_TASKS:
        if tc["tasks"].get(t) is not None: return t
    return None

def get_seconds_needed(quest: dict) -> int:
    tc = get_task_config(quest)
    tt = get_task_type(quest)
    return tc["tasks"][tt].get("target", 0) if tc and tt else 0

def get_seconds_done(quest: dict) -> float:
    tt = get_task_type(quest)
    us = _get(quest, "userStatus", "user_status")
    prog = (us.get("progress", {}) if isinstance(us, dict) else {}) or {}
    return prog.get(tt, {}).get("value", 0) if tt else 0


class QuestAutocompleter:
    def __init__(self, api: DiscordAPI):
        self.api = api
        self.completed_ids: set = set()

    def fetch_quests(self) -> list:
        try:
            r = self.api.get("/quests/@me")
            if r.status_code == 200:
                data = r.json()
                return data.get("quests", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            return []
        except Exception:
            return []

    def enroll_quest(self, quest: dict) -> bool:
        qid = quest["id"]
        try:
            r = self.api.post(f"/quests/{qid}/enroll", {
                "location": 11, "is_targeted": False, "metadata_raw": None, "metadata_sealed": None,
                "traffic_metadata_raw": quest.get("traffic_metadata_raw"),
                "traffic_metadata_sealed": quest.get("traffic_metadata_sealed"),
            })
            return r.status_code in (200, 201, 204)
        except Exception:
            return False

    def auto_accept(self, quests: list) -> list:
        if not AUTO_ACCEPT: return quests
        for q in [q for q in quests if not is_enrolled(q) and not is_completed(q) and is_completable(q)]:
            self.enroll_quest(q)
            time.sleep(2)
        return self.fetch_quests()

    def complete_video(self, quest: dict):
        qid = quest["id"]
        needed, done = get_seconds_needed(quest), get_seconds_done(quest)
        while done < needed:
            ts = done + 7
            try:
                r = self.api.post(f"/quests/{qid}/video-progress", {"timestamp": min(needed, ts)})
                if r.status_code == 200:
                    if r.json().get("completed_at"): break
                    done = min(needed, ts)
                elif r.status_code == 429:
                    time.sleep(5); continue
            except Exception:
                pass
            time.sleep(1)

    def complete_heartbeat(self, quest: dict):
        qid = quest["id"]
        tt, needed, done = get_task_type(quest), get_seconds_needed(quest), get_seconds_done(quest)
        pid = random.randint(1000, 30000)
        while done < needed:
            try:
                r = self.api.post(f"/quests/{qid}/heartbeat", {"stream_key": f"call:0:{pid}", "terminal": False})
                if r.status_code == 200:
                    body = r.json()
                    prog = body.get("progress", {})
                    if prog and tt in prog: done = prog[tt].get("value", done)
                    if body.get("completed_at") or done >= needed: break
                elif r.status_code == 429:
                    time.sleep(10); continue
            except Exception:
                pass
            time.sleep(HEARTBEAT_INTERVAL)
        try:
            self.api.post(f"/quests/{qid}/heartbeat", {"stream_key": f"call:0:{pid}", "terminal": True})
        except Exception:
            pass

    def process_quest(self, quest: dict):
        qid, tt = quest.get("id"), get_task_type(quest)
        if not tt or qid in self.completed_ids: return
        if tt in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
            self.complete_video(quest)
        elif tt in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP", "PLAY_ACTIVITY"):
            self.complete_heartbeat(quest)
        self.completed_ids.add(qid)


# Lưu trữ cấu hình database tạm thời trên RAM
WELCOME_CONFIGS = {}
VERIFY_ROLE_CONFIGS = {}
AGREE_ROLE_CONFIGS = {}        
BIRTHDAY_CHANNEL_CONFIGS = {}  
USER_BIRTHDAYS = {}            

# ── Discord Bot & UI Modals/Views ──────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Hàm hỗ trợ thả emoji tuỳ chỉnh theo ID khi người dùng bấm vào nút
async def try_react_custom_emoji(interaction: discord.Interaction):
    try:
        emoji_obj = bot.get_emoji(1503922700408586240)
        if emoji_obj and interaction.message:
            await interaction.message.add_reaction(emoji_obj)
    except Exception:
        pass


# 1. Modal Nhập Sinh Nhật Cực Đẹp
class BirthdayModal(discord.ui.Modal, title="🎂 Đăng ký thông tin Sinh Nhật"):
    day_input = discord.ui.TextInput(label="Ngày sinh (Day)", placeholder="Ví dụ: 25", min_length=1, max_length=2)
    month_input = discord.ui.TextInput(label="Tháng sinh (Month)", placeholder="Ví dụ: 12", min_length=1, max_length=2)
    year_input = discord.ui.TextInput(label="Năm sinh (Year)", placeholder="Ví dụ: 2008", min_length=4, max_length=4)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            d = int(self.day_input.value)
            m = int(self.month_input.value)
            y = int(self.year_input.value)
            
            birth_date = datetime(y, m, d)
            USER_BIRTHDAYS[interaction.user.id] = {"day": d, "month": m, "year": y}
            
            embed = discord.Embed(
                title="✨ ĐĂNG KÝ SINH NHẬT THÀNH CÔNG!",
                description=f"Hệ thống đã ghi nhận ngày sinh của bạn: **{d:02d}/{m:02d}/{y}** 🎈\n\n*Đến ngày sinh nhật của bạn, bot sẽ tự động gửi lời chúc mừng hoành tráng vào kênh chung!*",
                color=discord.Color.from_rgb(255, 105, 180)
            )
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.set_footer(text="Created by ph.huyy | Vĩnh Phúc, VN")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except ValueError:
            await interaction.response.send_message("❌ Ngày tháng năm sinh không hợp lệ! Vui lòng nhập lại chính xác bằng số.", ephemeral=True)

class BirthdayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎂 Đăng ký sinh nhật", style=discord.ButtonStyle.blurple, custom_id="persistent_birthday_button")
    async def birthday_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await try_react_custom_emoji(interaction)
        await interaction.response.send_modal(BirthdayModal())


# 2. Modal Xác thực Captcha ngẫu nhiên và cấp role
class VerifyModal(discord.ui.Modal):
    def __init__(self, correct_code: str):
        super().__init__(title="🔒 Xác thực Captcha")
        self.correct_code = correct_code
        
        self.code_input = discord.ui.TextInput(
            label=f"Nhập lại chính xác mã: {correct_code}",
            placeholder="Nhập mã ở trên vào đây...",
            min_length=len(correct_code),
            max_length=len(correct_code)
        )
        self.add_item(self.code_input)

    async def on_submit(self, interaction: discord.Interaction):
        user_val = self.code_input.value.strip()
        if user_val == self.correct_code:
            role_name = VERIFY_ROLE_CONFIGS.get(interaction.guild.id, "Member")
            
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            if not role:
                try:
                    role = await interaction.guild.create_role(name=role_name, reason="Tự động tạo role xác thực")
                except Exception:
                    pass
            
            if role and role not in interaction.user.roles:
                try:
                    await interaction.user.add_roles(role)
                    await interaction.response.send_message(f"🎉 Xác thực thành công! Bạn đã nhận được role **{role.name}**.", ephemeral=True)
                except Exception:
                    await interaction.response.send_message("⚠️ Xác thực thành công nhưng bot thiếu quyền để cấp role này! Vui lòng báo Admin.", ephemeral=True)
            else:
                await interaction.response.send_message("ℹ️ Bạn đã xác thực từ trước hoặc đã có role này rồi!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Sai mã captcha! Vui lòng bấm nút Xác thực lại và thử mã mới.", ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Xác thực ngay", style=discord.ButtonStyle.green, custom_id="persistent_verify_button")
    async def verify_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await try_react_custom_emoji(interaction)
        chars = string.ascii_uppercase + string.digits
        random_code = ''.join(random.choices(chars, k=5))
        await interaction.response.send_modal(VerifyModal(random_code))


# 3. View Đồng ý / Không đồng ý nội quy
class AgreeRulesView(discord.ui.View):
    def __init__(self, role_name: str):
        super().__init__(timeout=None)
        self.role_name = role_name

    @discord.ui.button(label="Đồng ý", style=discord.ButtonStyle.green, custom_id="agree_rules_yes", emoji="✅")
    async def agree_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await try_react_custom_emoji(interaction)
        guild = interaction.guild
        role = discord.utils.get(guild.roles, name=self.role_name)
        
        if not role:
            try:
                role = await guild.create_role(name=self.role_name, reason="Tự động tạo role qua nút Đồng ý")
            except Exception:
                pass
                
        if role and role not in interaction.user.roles:
            try:
                await interaction.user.add_roles(role)
                embed = discord.Embed(
                    title="✅ XÁC NHẬN ĐỒNG Ý THÀNH CÔNG!",
                    description=f"Cảm ơn bạn đã chấp nhận rủi ro và đồng ý với quy định của server **{guild.name}**!\n\nBạn đã nhận được role **{role.name}** <a:emoji_43:1541071774194733143>.",
                    color=discord.Color.green()
                )
                embed.set_footer(text="Created by ph.huyy | Vĩnh Phúc, VN")
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                await interaction.response.send_message("⚠️ Bot thiếu quyền để cấp role này cho bạn! Vui lòng liên hệ Admin.", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ Bạn đã có role **{self.role_name}** từ trước hoặc không tìm thấy role tương ứng!", ephemeral=True)

    @discord.ui.button(label="Không đồng ý", style=discord.ButtonStyle.red, custom_id="agree_rules_no", emoji="❌")
    async def disagree_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await try_react_custom_emoji(interaction)
        embed = discord.Embed(
            title="❌ ĐÃ TỪ CHỐI ĐIỀU KHOẢN",
            description=f"Bạn đã chọn **không đồng ý** hoặc không chấp nhận rủi ro tại server **{interaction.guild.name}**.\n\nNếu thay đổi suy nghĩ, bạn có thể bấm lại nút **Đồng ý** bất cứ lúc nào!",
            color=discord.Color.red()
        )
        embed.set_footer(text="Created by ph.huyy | Vĩnh Phúc, VN")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# 4. Modal Nhập Token Discord để xác định thông tin tài khoản
class TokenCheckModal(discord.ui.Modal, title="🔑 Xác thực Token Tài Khoản"):
    token_input = discord.ui.TextInput(
        label="Nhập Discord User Token của bạn",
        placeholder="Dán token của bạn vào đây...",
        style=discord.TextStyle.short,
        min_length=50,
        max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        token = self.token_input.value.strip()
        
        build_num = fetch_latest_build_number()
        api = DiscordAPI(token, build_num)
        user_data = api.validate_token()
        
        if not user_data or "id" not in user_data:
            await interaction.followup.send("❌ **Token không hợp lệ hoặc đã hết hạn!** Vui lòng kiểm tra lại token của bạn.", ephemeral=True)
            return
            
        username = user_data.get("username", "Unknown")
        global_name = user_data.get("global_name", username)
        user_id = user_data.get("id", "Unknown")
        email = user_data.get("email", "Không công khai / Không có")
        phone = user_data.get("phone", "Không có")
        mfa = "Có (Bảo mật 2 lớp)" if user_data.get("mfa_enabled") else "Không"
        
        avatar_hash = user_data.get("avatar")
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png" if avatar_hash else "https://cdn.discordapp.com/embed/avatars/0.png"
        
        embed = discord.Embed(
            title="✨ XÁC THỰC TOKEN THÀNH CÔNG!",
            description=f"Hệ thống đã kết nối thành công với tài khoản Discord của bạn <a:emoji_43:1541071774194733143>.",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=avatar_url)
        embed.add_field(name="📌 Tên hiển thị (Global Name)", value=f"**{global_name}**", inline=False)
        embed.add_field(name="👤 Tên tài khoản (Username)", value=f"`{username}`", inline=True)
        embed.add_field(name="🆔 ID Tài khoản", value=f"`{user_id}`", inline=True)
        embed.add_field(name="🔒 Xác thực 2 bước (MFA)", value=mfa, inline=True)
        embed.add_field(name="📧 Email", value=f"`{email}`", inline=True)
        embed.set_footer(text="Token Checker System | Created by ph.huyy (Vĩnh Phúc, VN)")
        
        await interaction.followup.send(embed=embed, ephemeral=True)

class TokenCheckView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔑 Nhập Token kiểm tra", style=discord.ButtonStyle.blurple, custom_id="persistent_token_button", emoji="🛡️")
    async def token_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await try_react_custom_emoji(interaction)
        await interaction.response.send_modal(TokenCheckModal())


# 5. Background Task kiểm tra và chúc mừng sinh nhật mỗi ngày vào lúc 00:00
@tasks.loop(hours=24)
async def check_birthdays_task():
    now = datetime.now()
    today_d = now.day
    today_m = now.month
    
    for guild in bot.guilds:
        channel_id = BIRTHDAY_CHANNEL_CONFIGS.get(guild.id)
        if not channel_id:
            continue
        channel = guild.get_channel(channel_id)
        if not channel:
            continue
            
        for user_id, b_info in USER_BIRTHDAYS.items():
            if b_info["day"] == today_d and b_info["month"] == today_m:
                member = guild.get_member(user_id)
                if member:
                    age_str = f" ({now.year - b_info['year']} tuổi)" if b_info.get("year") else ""
                    
                    embed = discord.Embed(
                        title="🎉 CHÚC MỪNG SINH NHẬT! 🥳",
                        description=f"Hôm nay là một ngày đặc biệt vô cùng ý nghĩa!\n\nHãy cùng toàn thể server gửi những lời chúc tốt đẹp nhất đến {member.mention}{age_str} nhé! 🎂🎁\n\n✨ *Chúc bạn tuổi mới luôn vui vẻ, hạnh phúc, học tập giỏi và đạt được mọi ước mơ!* ✨",
                        color=discord.Color.from_rgb(255, 215, 0)
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    embed.set_image(url="https://media1.giphy.com/media/26FPn4rR1damy0MQo/giphy.gif")
                    embed.set_footer(text=f"🎁 Gửi từ hệ thống tự động | Created by ph.huyy (Vĩnh Phúc, VN)")
                    
                    try:
                        await channel.send(content=f"🎊 **HAPPY BIRTHDAY TO {member.mention}!** 🎊", embed=embed)
                    except Exception:
                        pass


@bot.event
async def on_ready():
    bot.add_view(BirthdayView())
    bot.add_view(VerifyView())
    bot.add_view(AgreeRulesView("Member"))
    bot.add_view(TokenCheckView())
    
    if not check_birthdays_task.is_running():
        check_birthdays_task.start()
        
    log(f"Discord Bot đã sẵn sàng: {bot.user.name}", "ok")
    try:
        synced = await bot.tree.sync()
        log(f"Đã đồng bộ {len(synced)} lệnh slash.", "ok")
    except Exception as e:
        log(f"Lỗi đồng bộ lệnh: {e}", "error")


# ── Sự kiện Welcome ────────────────────────────────────────────────────────────
@bot.event
async def on_member_join(member: discord.Member):
    config = WELCOME_CONFIGS.get(member.guild.id)
    if not config:
        return
    
    channel = member.guild.get_channel(config["channel_id"])
    if not channel:
        return
    
    custom_msg = config["message"]
    custom_msg = custom_msg.replace("{user}", member.mention).replace("{server}", member.guild.name)

    embed = discord.Embed(
        title="👋 Chào mừng thành viên mới!",
        description=custom_msg,
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    
    if config["image_url"]:
        embed.set_image(url=config["image_url"])
        
    embed.set_footer(text=f"Thành viên thứ #{member.guild.member_count} | Created by ph.huyy (Vĩnh Phúc, VN)")
    await channel.send(embed=embed)


# ── Lệnh Slash: /auto ──────────────────────────────────────────────────────────
@bot.tree.command(name="auto", description="Tự động quét và hoàn thành Quest Discord bằng token cá nhân")
async def slash_auto(interaction: discord.Interaction, token: str):
    await interaction.response.defer(ephemeral=True)
    build_num = fetch_latest_build_number()
    api = DiscordAPI(token, build_num)
    
    user_data = api.validate_token()
    if not user_data or "id" not in user_data:
        await interaction.followup.send("❌ **Token không hợp lệ hoặc đã hết hạn!**", ephemeral=True)
        return
    
    completer = QuestAutocompleter(api)
    quests = completer.fetch_quests()
    
    if not quests:
        await interaction.followup.send("⚠️ Không tìm thấy Quest nào khả dụng.", ephemeral=True)
        return
        
    quests = completer.auto_accept(quests)
    actionable = [q for q in quests if is_enrolled(q) and not is_completed(q) and is_completable(q)]
    
    if not actionable:
        await interaction.followup.send("✅ Tất cả các Quest hiện tại đã hoàn thành hoặc chưa mở khóa!", ephemeral=True)
        return
        
    embed = discord.Embed(
        title="🚀 HỆ THỐNG AUTO QUEST ĐANG CHẠY",
        description=f"<a:emoji_43:1541071774194733143> Đang tiến hành chạy ngầm hoàn thành **{len(actionable)}** quest cho tài khoản của bạn. Vui lòng chờ trong giây lát...",
        color=discord.Color.from_rgb(0, 255, 127)
    )
    embed.set_footer(text="Auto Quest System | Created by ph.huyy (Vĩnh Phúc, VN)")
    
    await interaction.followup.send(embed=embed, ephemeral=True)
    for q in actionable:
        completer.process_quest(q)


# ── Lệnh Slash: /token (Hiển thị bảng nhập token để kiểm tra thông tin tài khoản) ─
@bot.tree.command(name="token", description="Hiển thị bảng nhập token để xác định thông tin tài khoản Discord của bạn")
async def slash_token(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔑 HỆ THỐNG KIỂM TRA THÔNG TIN TOKEN",
        description=(
            "Nhấn vào nút **Nhập Token kiểm tra** bên dưới để mở bảng nhập mã Token cá nhân.\n\n"
            "🛡️ *Hệ thống sẽ kết nối trực tiếp với Discord API để xác thực và hiển thị Tên, ID và ảnh đại diện của bạn một cách bảo mật.*"
        ),
        color=discord.Color.blurple()
    )
    embed.set_footer(text="Token Checker System | Created by ph.huyy (Vĩnh Phúc, VN)")
    
    view = TokenCheckView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ── Lệnh Slash: /agree (Gửi bảng nội quy & hỏi rủi ro kèm 2 nút Đồng ý / Không đồng ý) 
@bot.tree.command(name="agree", description="Gửi bảng nội quy và xác nhận rủi ro kèm hai nút Đồng ý/Không đồng ý")
@app_commands.describe(
    channel="Chọn kênh văn bản để gửi bảng xác nhận",
    role_name="Tên role trao thưởng khi người dùng bấm Đồng ý (Mặc định: Member)"
)
async def slash_agree(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None, role_name: Optional[str] = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Bạn cần quyền Quản trị viên (Administrator) để dùng tính năng này!", ephemeral=True)
        return

    target_channel = channel if channel else interaction.channel
    r_name = role_name if role_name else "Member"
    AGREE_ROLE_CONFIGS[interaction.guild.id] = r_name

    embed = discord.Embed(
        title="⚠️ XÁC NHẬN NỘI QUY & RỦI RO",
        description=(
            "Chào mừng bạn đến với server! Trước khi tham gia, vui lòng xác nhận rõ các điều khoản:\n\n"
            "1. Tôn trọng tất cả các thành viên khác trong cộng đồng.\n"
            "2. Không phát tán thông tin giả mạo hoặc link độc hại.\n\n"
            "🛡️ **Bạn có chấp nhận rủi ro khi tham gia server không?**\n\n"
            f"*Bấm nút **Đồng ý** bên dưới để chấp nhận điều khoản, rủi ro và nhận role **{r_name}** <a:emoji_43:1541071774194733143>.*"
        ),
        color=discord.Color.orange()
    )
    embed.set_footer(text="CTDOTEAM System | Created by ph.huyy (Vĩnh Phúc, VN)")

    view = AgreeRulesView(r_name)
    await target_channel.send(embed=embed, view=view)
    await interaction.response.send_message(f"✅ Đã gửi bảng nội quy & xác nhận rủi ro thành công vào {target_channel.mention}!", ephemeral=True)


# ── Lệnh Slash: /help (Hướng dẫn sử dụng bot) ──────────────────────────────────
@bot.tree.command(name="help", description="Hiển thị danh sách các lệnh và hướng dẫn sử dụng bot")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 TRUNG TÂM TRỢ GIÚP - CTDOTEAM BOT",
        description="Dưới đây là danh sách các lệnh Slash (`/`) và tính năng hiện có của hệ thống:",
        color=discord.Color.orange()
    )
    embed.add_field(
        name="🚀 `/auto`",
        value="Tự động quét và hoàn thành các Quest Discord (yêu cầu cung cấp token tài khoản cá nhân).",
        inline=False
    )
    embed.add_field(
        name="🔑 `/token`",
        value="Hiển thị bảng nhập token để kiểm tra và xác định tên tài khoản Discord của bạn.",
        inline=False
    )
    embed.add_field(
        name="📜 `/agree`",
        value="Gửi bảng nội quy & xác nhận rủi ro kèm hai nút bấm **Đồng ý** / **Không đồng ý**.",
        inline=False
    )
    embed.add_field(
        name="⚙️ `/setup`",
        value="Lệnh cài đặt hệ thống dành cho Quản trị viên (Yêu cầu quyền Administrator):\n"
              "• `welcome`: Cài đặt thông điệp chào mừng thành viên.\n"
              "• `birthday`: Gửi bảng đăng ký sinh nhật tương tác nút bấm vào kênh chọn.\n"
              "• `birthday_announcement`: Chọn kênh để bot tự động gửi thông báo chúc mừng sinh nhật.\n"
              "• `verify`: Gửi bảng xác thực Captcha kèm nút bấm và cấu hình role trao thưởng.\n"
              "• `channel`: Tự động khởi tạo danh mục và các kênh cơ bản.",
        inline=False
    )
    embed.add_field(
        name="❓ `/help`",
        value="Hiển thị bảng hướng dẫn sử dụng bot này.",
        inline=False
    )
    embed.set_footer(text="CTDOTEAM Discord Bot System | Created by ph.huyy (Vĩnh Phúc, VN)")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Lệnh Slash Gom Nhóm: /setup ────────────────────────────────────────────────
@bot.tree.command(name="setup", description="Cài đặt các tính năng của bot (Welcome, Birthday, Verify, Channel)")
@app_commands.choices(feature=[
    app_commands.Choice(name="Welcome (Chào mừng)", value="welcome"),
    app_commands.Choice(name="Birthday (Nút đăng ký sinh nhật)", value="birthday"),
    app_commands.Choice(name="Birthday Announcement (Kênh chúc mừng sinh nhật)", value="birthday_announcement"),
    app_commands.Choice(name="Verify (Xác thực)", value="verify"),
    app_commands.Choice(name="Channel (Tạo nhóm kênh cơ bản)", value="channel")
])
@app_commands.describe(
    feature="Chọn tính năng bạn muốn cài đặt",
    channel="Chọn kênh văn bản đích từ bảng danh sách kênh hiện có trong server",
    message="Nội dung thông điệp (Welcome) hoặc Tên Role trao thưởng (Verify)",
    image="Ảnh đính kèm (Dùng cho Welcome)"
)
async def setup(
    interaction: discord.Interaction, 
    feature: str, 
    channel: Optional[discord.TextChannel] = None,
    message: Optional[str] = None,
    image: Optional[discord.Attachment] = None
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Bạn cần quyền Quản trị viên (Administrator) để dùng tính năng này!", ephemeral=True)
        return

    if feature == "welcome":
        if not channel:
            await interaction.response.send_message("❌ Vui lòng chọn kênh (`channel`) từ bảng gợi ý để cài đặt Welcome!", ephemeral=True)
            return
            
        welcome_text = message if message else "Xin chào {user}! Chúc bạn có những phút giây vui vẻ tại **{server}**."
        image_url = image.url if image else None

        WELCOME_CONFIGS[interaction.guild.id] = {
            "channel_id": channel.id,
            "message": welcome_text,
            "image_url": image_url
        }

        embed = discord.Embed(
            title="⚙️ Đã thiết lập Welcome thành công!",
            description=f"• **Kênh:** {channel.mention}\n• **Văn bản:** {welcome_text}\n• **Ảnh đính kèm:** {'Có' if image_url else 'Không'}",
            color=discord.Color.green()
        )
        if image_url:
            embed.set_thumbnail(url=image_url)
        embed.set_footer(text="Created by ph.huyy | Vĩnh Phúc, VN")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    elif feature == "birthday":
        if not channel:
            await interaction.response.send_message("❌ Vui lòng chọn kênh (`channel`) từ bảng gợi ý để gửi bảng đăng ký sinh nhật!", ephemeral=True)
            return
            
        embed = discord.Embed(
            title="🎂 ĐĂNG KÝ SINH NHẬT THÀNH VIÊN",
            description="Nhấn vào nút **Đăng ký sinh nhật** bên dưới để mở bảng tương tác nhập ngày, tháng, năm sinh của bạn vào hệ thống.",
            color=discord.Color.gold()
        )
        embed.set_footer(text="Created by ph.huyy | Vĩnh Phúc, VN")
        view = BirthdayView()
        await channel.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ Đã gửi bảng đăng ký sinh nhật kèm nút bấm thành công vào {channel.mention}!", ephemeral=True)

    elif feature == "birthday_announcement":
        if not channel:
            await interaction.response.send_message("❌ Vui lòng chọn kênh (`channel`) từ bảng gợi ý để làm kênh chúc mừng sinh nhật!", ephemeral=True)
            return
            
        BIRTHDAY_CHANNEL_CONFIGS[interaction.guild.id] = channel.id
        
        embed = discord.Embed(
            title="✨ THIẾT LẬP KÊNH CHÚC MỪNG SINH NHẬT",
            description=f"Đã chọn {channel.mention} làm nơi bot tự động gửi lời chúc mừng sinh nhật hoành tráng đến các thành viên hàng ngày!",
            color=discord.Color.from_rgb(255, 105, 180)
        )
        embed.set_footer(text="Created by ph.huyy | Vĩnh Phúc, VN")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    elif feature == "verify":
        if not channel:
            await interaction.response.send_message("❌ Vui lòng chọn kênh (`channel`) từ bảng gợi ý để gửi bảng Verify!", ephemeral=True)
            return
            
        target_role_name = message if message else "Member"
        VERIFY_ROLE_CONFIGS[interaction.guild.id] = target_role_name
            
        embed = discord.Embed(
            title="🔒 XÁC THỰC TÀI KHOẢN CAPTCHA",
            description=f"Nhấn vào nút **Xác thực ngay** bên dưới để nhận mã ngẫu nhiên và nhận role **{target_role_name}**.",
            color=discord.Color.purple()
        )
        embed.set_footer(text="Created by ph.huyy | Vĩnh Phúc, VN")
        view = VerifyView()
        await channel.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ Đã gửi bảng xác thực vào {channel.mention}. Khi người dùng hoàn thành, bot sẽ tự cấp role **{target_role_name}**!", ephemeral=True)

    elif feature == "channel":
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)
        
        try:
            category = await guild.create_category("📌 THÔNG TIN & HỆ THỐNG")
            rules_chan = await guild.create_text_channel("📜・nội-quy", category=category)
            ann_chan = await guild.create_text_channel("📢・thông-báo", category=category)
            gen_chan = await guild.create_text_channel("💬・trò-chuyện", category=category)
            
            embed = discord.Embed(
                title="🛠️ Thiết lập kênh tự động thành công!",
                description=f"Đã khởi tạo danh mục **{category.name}** cùng các kênh:\n• {rules_chan.mention}\n• {ann_chan.mention}\n• {gen_chan.mention}",
                color=discord.Color.blue()
            )
            embed.set_footer(text="Created by ph.huyy | Vĩnh Phúc, VN")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Có lỗi xảy ra khi tạo kênh: {e}", ephemeral=True)


# ── Main Entry ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not DISCORD_BOT_TOKEN:
        print("Vui lòng thiết lập biến môi trường DISCORD_BOT_TOKEN trên hệ thống của bạn.")
        sys.exit(1)
    bot.run(DISCORD_BOT_TOKEN)
