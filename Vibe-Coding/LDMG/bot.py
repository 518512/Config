#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LDMG - Lite Docker Manager Gram
基于 TG Bot 的 Docker Compose 升级助手 (全量修复与美化增强版)
"""

import os
import json
import time
import html
import uuid
import asyncio
import subprocess
import logging
from collections import deque
from typing import List, Dict, Optional, Any

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# 加载 .env 环境变量
load_dotenv()

# ==================== 配置 ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ALLOWED_USER_IDS = {
    int(uid.strip())
    for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if uid.strip().isdigit()
}

COMMAND_TIMEOUT = 300  # Docker 单个命令超时时间（秒）
PAGE_SIZE = 6          # 主面板每页展示的项目数量

# 全局任务状态管理
_EXEC_LOCK: Optional[asyncio.Lock] = None
_CURRENT_PROCESS: Optional[asyncio.subprocess.Process] = None
_CANCEL_REQUESTED: bool = False

def get_exec_lock() -> asyncio.Lock:
    """获取并发互斥锁"""
    global _EXEC_LOCK
    if _EXEC_LOCK is None:
        _EXEC_LOCK = asyncio.Lock()
    return _EXEC_LOCK


# ==================== Callback Data 内存映射系统 ====================
# 彻底解决 Telegram 64 字节 callback_data 限制
_CALLBACK_STORE: Dict[str, Dict[str, Any]] = {}

def create_cb_data(action: str, payload: Optional[Dict[str, Any]] = None) -> str:
    """生成短 ID 并存储真实载荷，规避 Telegram 限制"""
    if payload is None:
        payload = {}
    short_id = uuid.uuid4().hex[:8]
    key = f"{action}:{short_id}"
    _CALLBACK_STORE[key] = payload
    
    # 自动清理过期的映射，防止内存膨胀
    if len(_CALLBACK_STORE) > 800:
        for k in list(_CALLBACK_STORE.keys())[:200]:
            _CALLBACK_STORE.pop(k, None)
            
    return key

def get_cb_payload(key: str) -> Optional[Dict[str, Any]]:
    """根据 short key 获取回调数据"""
    return _CALLBACK_STORE.get(key)


# ==================== 日志与安全脱敏配置 ====================
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

class TokenMaskFilter(logging.Filter):
    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.token:
            return True
        if isinstance(record.msg, str) and self.token in record.msg:
            record.msg = record.msg.replace(self.token, "[REDACTED_BOT_TOKEN]")
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    arg.replace(self.token, "[REDACTED_BOT_TOKEN]") if isinstance(arg, str) else arg 
                    for arg in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (v.replace(self.token, "[REDACTED_BOT_TOKEN]") if isinstance(v, str) else v) 
                    for k, v in record.args.items()
                }
        return True

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

if BOT_TOKEN:
    mask_filter = TokenMaskFilter(BOT_TOKEN)
    for handler in logging.getLogger().handlers:
        handler.addFilter(mask_filter)


# ==================== 权限与辅助函数 ====================
def is_allowed(user_id: int) -> bool:
    return bool(ALLOWED_USER_IDS) and user_id in ALLOWED_USER_IDS

async def check_permission(update: Update) -> bool:
    user = update.effective_user
    if not user or not is_allowed(user.id):
        if update.message:
            await update.message.reply_text("❌ 无权限操作")
        elif update.callback_query:
            await update.callback_query.answer("❌ 无权限", show_alert=True)
        return False
    return True

def get_user_identifier(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "Unknown User"
    username = f"@{user.username}" if user.username else user.first_name
    return f"{user.id} ({username})"

def render_progress_bar(percentage: int, width: int = 8) -> str:
    """生成自定义进度条图标"""
    filled = int(round(width * percentage / 100))
    return "▰" * filled + "▱" * (width - filled)

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    from telegram.error import NetworkError, TimedOut, BadRequest
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.warning(f"🌐 遇到临时网络波动: {context.error}")
        return
    if isinstance(context.error, BadRequest) and "Message is not modified" in str(context.error):
        return
    logger.error("❌ 未捕获的系统异常:", exc_info=True)


# ==================== Docker Compose 扫描与状态 ====================
def get_project_services(work_dir: str) -> List[str]:
    """获取项目的服务定义"""
    try:
        result = subprocess.run(
            ["docker", "compose", "config", "--services"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return [s.strip() for s in result.stdout.strip().splitlines() if s.strip()]
    except Exception as e:
        logger.warning(f"获取 {work_dir} 服务列表失败: {e}")
    return []

def _get_compose_projects_sync() -> List[Dict]:
    projects = []
    seen_keys = set()

    try:
        result = subprocess.run(
            ["docker", "compose", "ls", "-a", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                data = json.loads(result.stdout)
                if isinstance(data, dict):
                    data = [data]
            except json.JSONDecodeError:
                data = []
                for line in result.stdout.strip().splitlines():
                    if line.strip():
                        try:
                            data.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

            for item in data:
                name = item.get("Name", "")
                status = item.get("Status", "")
                config_files = item.get("ConfigFiles", "")
                if not name:
                    continue
                first_file = config_files.split(",")[0].strip() if config_files else ""
                work_dir = os.path.dirname(first_file) if first_file else ""
                
                unique_key = f"{name}:{work_dir}"
                if work_dir and os.path.isdir(work_dir) and unique_key not in seen_keys:
                    seen_keys.add(unique_key)
                    services = get_project_services(work_dir)
                    projects.append({
                        "name": name,
                        "dir": work_dir,
                        "status": status,
                        "services": services,
                    })
    except Exception as e:
        logger.warning(f"docker compose ls 扫描失败: {e}")

    projects.sort(key=lambda x: x["name"])
    return projects

async def get_compose_projects() -> List[Dict]:
    return await asyncio.to_thread(_get_compose_projects_sync)


# ==================== 可视化进度条 & 日志执行引擎 ====================
async def run_command_with_feedback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cmd: List[str],
    cwd: Optional[str] = None,
    title: str = "执行中",
    progress_pct: int = 50,
) -> bool:
    global _CURRENT_PROCESS, _CANCEL_REQUESTED
    message = update.effective_message
    
    start_time = time.time()
    safe_title = html.escape(title)
    safe_cmd = html.escape(' '.join(cmd))
    
    cancel_cb = create_cb_data("task_cancel")
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 中断执行", callback_data=cancel_cb)]])

    p_bar = render_progress_bar(progress_pct)
    status_msg = await message.reply_text(
        f"⚙️ <b>{safe_title}</b> [{p_bar}]\n"
        f"⏱ <b>已用时：</b>0.0s\n"
        f"<code>{safe_cmd}</code>",
        reply_markup=cancel_markup,
        parse_mode="HTML"
    )

    output_lines = deque(maxlen=100)
    _CURRENT_PROCESS = None
    _CANCEL_REQUESTED = False

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        _CURRENT_PROCESS = process

        last_update_time = time.time()

        async def read_stream():
            nonlocal last_update_time
            while True:
                if _CANCEL_REQUESTED:
                    process.kill()
                    break

                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    break
                
                line = line_bytes.decode('utf-8', errors='replace').rstrip()
                output_lines.append(line)

                now = time.time()
                is_key_line = any(
                    k in line.lower() for k in ["pulling", "downloaded", "created", "started", "error", "done"]
                )
                if (len(output_lines) % 8 == 0 or is_key_line) and (now - last_update_time >= 1.2):
                    elapsed = round(now - start_time, 1)
                    preview = "\n".join(list(output_lines)[-12:])
                    safe_preview = html.escape(preview[-3500:])
                    try:
                        await status_msg.edit_text(
                            f"⚙️ <b>{safe_title}</b> [{p_bar}]\n"
                            f"⏱ <b>已用时：</b>{elapsed}s\n"
                            f"<code>{safe_preview}</code>",
                            reply_markup=cancel_markup,
                            parse_mode="HTML",
                        )
                        last_update_time = now
                    except Exception:
                        pass

        await asyncio.wait_for(read_stream(), timeout=COMMAND_TIMEOUT)
        returncode = await process.wait()

        elapsed = round(time.time() - start_time, 1)
        full_output = "\n".join(list(output_lines)[-25:])
        safe_full_output = html.escape(full_output[-3500:])

        if _CANCEL_REQUESTED:
            await status_msg.edit_text(
                f"🛑 <b>{safe_title} 已取消</b>\n"
                f"⏱ <b>已用时：</b>{elapsed}s\n"
                f"<code>{safe_full_output}</code>",
                parse_mode="HTML"
            )
            return False

        if returncode == 0:
            p_done = render_progress_bar(100)
            await status_msg.edit_text(
                f"✅ <b>{safe_title} 完成</b> [{p_done}]\n"
                f"⏱ <b>总耗时：</b>{elapsed}s\n"
                f"<code>{safe_full_output}</code>",
                parse_mode="HTML",
            )
            return True
        else:
            await status_msg.edit_text(
                f"❌ <b>{safe_title} 失败 (Code {returncode})</b>\n"
                f"⏱ <b>耗时：</b>{elapsed}s\n"
                f"<code>{safe_full_output}</code>",
                parse_mode="HTML",
            )
            return False

    except asyncio.TimeoutError:
        if process:
            try: process.kill()
            except Exception: pass
        await status_msg.edit_text(
            f"⏰ <b>{safe_title} 超时中断</b>\n"
            f"单条指令耗时超过 {COMMAND_TIMEOUT} 秒，已强行终止。",
            parse_mode="HTML"
        )
        return False

    except Exception as e:
        if process:
            try: process.kill()
            except Exception: pass
        await status_msg.edit_text(f"❌ 执行发生异常: {html.escape(str(e))}", parse_mode="HTML")
        return False
    finally:
        _CURRENT_PROCESS = None


# ==================== 核心面板与分页菜单 ====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_permission(update):
        return
    await cmd_list(update, context)

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """主面板列表（支持分页、统计与分组）"""
    if not await check_permission(update):
        return

    user_str = get_user_identifier(update)
    logger.info(f"▶️ [操作审计] 用户 [{user_str}] 查看项目列表 (页码: {page})")

    projects = await get_compose_projects()
    total_projects = len(projects)
    running_cnt = sum(1 for p in projects if "running" in p["status"].lower())
    
    # 分页计算
    total_pages = max(1, (total_projects + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * PAGE_SIZE
    page_projects = projects[start_idx:start_idx + PAGE_SIZE]

    text = "🐳 <b>Docker Compose 管理面板</b>\n"
    text += f"📊 <b>统计：</b>共 {total_projects} 个项目 | 🟢 {running_cnt} 运行中 | 🟡 {total_projects - running_cnt} 停止\n"
    text += f"📖 <b>页码：</b>{page} / {total_pages}\n"
    text += "───────────────────────────\n\n"

    keyboard = []

    if not projects:
        text += "⚠️ 暂未检测到任何 Docker Compose 项目"
    else:
        for i, p in enumerate(page_projects, start=start_idx + 1):
            num = f"{i:02d}"
            name = p["name"]
            status = p["status"]
            status_icon = "🟢" if "running" in status.lower() else "🟡"
            
            disp_name = name[:26] + ".." if len(name) > 28 else name
            safe_name = html.escape(name)
            safe_dir = html.escape(p["dir"])
            services_str = ", ".join(p["services"]) if p["services"] else "-"
            safe_services = html.escape(services_str)

            text += f"<b>{num}.</b> {safe_name} {status_icon} <code>[{html.escape(status)}]</code>\n"
            text += f"     📂 <code>{safe_dir}</code>\n"
            text += f"     📦 服务({len(p['services'])}): {safe_services}\n\n"
            
            if len(p["services"]) > 1:
                cb_data = create_cb_data("p_sel", {"name": name, "page": page})
                keyboard.append([InlineKeyboardButton(f"⚙️ {num}. {disp_name} (多服务)", callback_data=cb_data)])
            else:
                cb_data = create_cb_data("up_s_ask", {"name": name, "page": page})
                keyboard.append([InlineKeyboardButton(f"🚀 {num}. {disp_name}", callback_data=cb_data)])

    # 分页翻页控制行
    page_nav = []
    if page > 1:
        cb_prev = create_cb_data("page_turn", {"page": page - 1})
        page_nav.append(InlineKeyboardButton("◀ 上一页", callback_data=cb_prev))
    page_nav.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        cb_next = create_cb_data("page_turn", {"page": page + 1})
        page_nav.append(InlineKeyboardButton("下一页 ▶", callback_data=cb_next))
    
    keyboard.append(page_nav)

    # 快捷功能行
    cb_prune_menu = create_cb_data("prune_menu")
    keyboard.append([
        InlineKeyboardButton("🧹 镜像清理菜单", callback_data=cb_prune_menu),
        InlineKeyboardButton("⬆️ 升级全部项目", callback_data="upgrade_all")
    ])
    keyboard.append([InlineKeyboardButton("🔄 刷新状态", callback_data=create_cb_data("page_turn", {"page": page}))])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
        await update.callback_query.answer()
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")


async def show_project_detail(update: Update, project_name: str, back_page: int = 1):
    """二级菜单：项目详情控制页"""
    query = update.callback_query
    projects = await get_compose_projects()
    target_p = next((p for p in projects if p["name"] == project_name), None)

    if not target_p:
        await query.answer("❌ 未找到该项目", show_alert=True)
        return

    safe_name = html.escape(target_p['name'])
    safe_dir = html.escape(target_p['dir'])

    text = f"📦 <b>项目卡片：{safe_name}</b>\n\n"
    text += f"📂 <b>路径：</b><code>{safe_dir}</code>\n"
    text += f"🟢 <b>状态：</b>{target_p['status']}\n\n"
    text += "⚙️ <b>请选择操作控制范围：</b>\n"

    cb_all = create_cb_data("up_s_ask", {"name": project_name, "page": back_page})
    keyboard = [[InlineKeyboardButton("⚡ 升级全部服务容器", callback_data=cb_all)]]

    if target_p["services"]:
        for svc in target_p["services"]:
            cb_svc = create_cb_data("up_svc_ask", {"name": project_name, "svc": svc, "page": back_page})
            keyboard.append([InlineKeyboardButton(f"🔹 仅升级服务: {svc}", callback_data=cb_svc)])

    cb_back = create_cb_data("page_turn", {"page": back_page})
    keyboard.append([InlineKeyboardButton("🔙 返回列表", callback_data=cb_back)])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    await query.answer()


# ==================== 确认弹窗函数 ====================
async def ask_single_upgrade(update: Update, project_name: str, back_page: int = 1):
    query = update.callback_query
    projects = await get_compose_projects()
    target_p = next((p for p in projects if p["name"] == project_name), None)

    safe_name = html.escape(project_name)
    safe_dir = html.escape(target_p["dir"]) if target_p else "未知路径"

    cb_confirm = create_cb_data("up_p_do", {"name": project_name})
    cb_back = create_cb_data("page_turn", {"page": back_page})
    
    keyboard = [
        [
            InlineKeyboardButton("✅ 确认升级", callback_data=cb_confirm),
            InlineKeyboardButton("🔙 取消返回", callback_data=cb_back),
        ]
    ]

    text = (
        f"🚀 <b>升级确认 - [{safe_name}]</b>\n\n"
        f"📂 <b>工作目录：</b><code>{safe_dir}</code>\n"
        f"🛠 <b>执行步骤：</b>\n"
        f"  1. <code>docker compose pull</code>\n"
        f"  2. <code>docker compose up -d</code>\n"
    )

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    await query.answer()


async def ask_svc_upgrade(update: Update, project_name: str, service_name: str, back_page: int = 1):
    query = update.callback_query
    projects = await get_compose_projects()
    target_p = next((p for p in projects if p["name"] == project_name), None)

    safe_p = html.escape(project_name)
    safe_s = html.escape(service_name)
    safe_dir = html.escape(target_p["dir"]) if target_p else "未知路径"

    cb_confirm = create_cb_data("up_svc_do", {"name": project_name, "svc": service_name})
    cb_back = create_cb_data("p_sel", {"name": project_name, "page": back_page})

    keyboard = [
        [
            InlineKeyboardButton("✅ 确认升级单一服务", callback_data=cb_confirm),
            InlineKeyboardButton("🔙 返回", callback_data=cb_back),
        ]
    ]

    text = (
        f"🚀 <b>服务升级确认 - [{safe_s}]</b>\n\n"
        f"📦 <b>所属项目：</b>{safe_p}\n"
        f"📂 <b>工作路径：</b><code>{safe_dir}</code>\n\n"
        f"💡 仅重建并升级 <code>{safe_s}</code>，项目内其他容器不受影响。"
    )

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    await query.answer()


# ==================== 执行逻辑底座 ====================
async def do_upgrade_project(update: Update, context: ContextTypes.DEFAULT_TYPE, project_name: str):
    lock = get_exec_lock()
    query = update.callback_query
    user_str = get_user_identifier(update)

    if lock.locked():
        if query: await query.answer("⚠️ 当前已有正在运行的部署/清理任务", show_alert=True)
        return

    if query: await query.answer()

    async with lock:
        projects = await get_compose_projects()
        target_p = next((p for p in projects if p["name"] == project_name), None)

        if not target_p:
            await update.effective_message.reply_text(f"❌ 找不到对应项目: <code>{html.escape(project_name)}</code>", parse_mode="HTML")
            return

        message = update.effective_message
        safe_name = html.escape(target_p['name'])
        logger.info(f"🚀 [操作审计] 用户 [{user_str}] 升级完整项目 [{project_name}]")

        await message.reply_text(f"🚀 <b>开始升级项目 [{safe_name}]</b>", parse_mode
