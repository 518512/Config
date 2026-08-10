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

    page_nav = []
    if page > 1:
        cb_prev = create_cb_data("page_turn", {"page": page - 1})
        page_nav.append(InlineKeyboardButton("◀ 上一页", callback_data=cb_prev))
    page_nav.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        cb_next = create_cb_data("page_turn", {"page": page + 1})
        page_nav.append(InlineKeyboardButton("下一页 ▶", callback_data=cb_next))
    
    keyboard.append(page_nav)

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

        # 此处修补语法错误: 补充完整的 parse_mode="HTML" 和右括号 )
        await message.reply_text(f"🚀 <b>开始升级项目 [{safe_name}]</b>", parse_mode="HTML")

        # 阶段 1：Pull
        pull_ok = await run_command_with_feedback(
            update, context,
            ["docker", "compose", "pull"],
            cwd=target_p["dir"],
            title=f"拉取新镜像 - {target_p['name']}",
            progress_pct=30,
        )
        if not pull_ok: return

        # 阶段 2：Up
        up_ok = await run_command_with_feedback(
            update, context,
            ["docker", "compose", "up", "-d"],
            cwd=target_p["dir"],
            title=f"重建与启动 - {target_p['name']}",
            progress_pct=80,
        )

        if up_ok:
            logger.info(f"✅ 项目 [{project_name}] 升级完成")
            await message.reply_text(f"🎉 项目 <b>{safe_name}</b> 整体升级成功！", parse_mode="HTML")


async def do_upgrade_service(update: Update, context: ContextTypes.DEFAULT_TYPE, project_name: str, service_name: str):
    lock = get_exec_lock()
    query = update.callback_query
    user_str = get_user_identifier(update)

    if lock.locked():
        if query: await query.answer("⚠️ 当前已有正在运行的任务", show_alert=True)
        return

    if query: await query.answer()

    async with lock:
        projects = await get_compose_projects()
        target_p = next((p for p in projects if p["name"] == project_name), None)

        if not target_p:
            await update.effective_message.reply_text(f"❌ 未找到项目: <code>{html.escape(project_name)}</code>", parse_mode="HTML")
            return

        message = update.effective_message
        safe_name = html.escape(target_p['name'])
        safe_svc = html.escape(service_name)

        logger.info(f"🚀 [操作审计] 用户 [{user_str}] 升级单服务 [{project_name} -> {service_name}]")

        await message.reply_text(f"🚀 <b>升级服务 [{safe_svc}] ({safe_name})</b>", parse_mode="HTML")

        pull_ok = await run_command_with_feedback(
            update, context,
            ["docker", "compose", "pull", service_name],
            cwd=target_p["dir"],
            title=f"拉取服务镜像 - {service_name}",
            progress_pct=30,
        )
        if not pull_ok: return

        up_ok = await run_command_with_feedback(
            update, context,
            ["docker", "compose", "up", "-d", service_name],
            cwd=target_p["dir"],
            title=f"重建启动服务 - {service_name}",
            progress_pct=80,
        )

        if up_ok:
            logger.info(f"✅ 服务 [{service_name}] 升级完成")
            await message.reply_text(f"🎉 服务 <code>{safe_svc}</code> 升级成功！", parse_mode="HTML")


async def do_upgrade_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lock = get_exec_lock()
    query = update.callback_query
    user_str = get_user_identifier(update)

    if lock.locked():
        if query: await query.answer("⚠️ 当前已有其他任务在运行", show_alert=True)
        return

    if query: await query.answer()

    async with lock:
        projects = await get_compose_projects()
        if not projects:
            await update.effective_message.reply_text("未检测到可升级的项目")
            return

        logger.info(f"🚀 [操作审计] 用户 [{user_str}] 触发批量升级共 {len(projects)} 个项目")
        message = update.effective_message
        await message.reply_text(f"🚀 <b>开始批量升级全部 {len(projects)} 个项目...</b>", parse_mode="HTML")

        success_list = []
        fail_list = []

        for i, p in enumerate(projects, 1):
            p_name = p['name']
            safe_name = html.escape(p_name)
            pct = int((i / len(projects)) * 100)
            
            await message.reply_text(f"📦 <b>[{i}/{len(projects)}] 处理项目: {safe_name}</b>", parse_mode="HTML")
            
            pull_ok = await run_command_with_feedback(
                update, context,
                ["docker", "compose", "pull"],
                cwd=p["dir"],
                title=f"批量拉取 - {p_name}",
                progress_pct=pct,
            )
            
            up_ok = False
            if pull_ok:
                up_ok = await run_command_with_feedback(
                    update, context,
                    ["docker", "compose", "up", "-d"],
                    cwd=p["dir"],
                    title=f"批量启动 - {p_name}",
                    progress_pct=pct,
                )
            
            if pull_ok and up_ok:
                success_list.append(p_name)
            else:
                fail_list.append(p_name)

        summary = f"🏁 <b>批量升级任务完成</b>\n"
        summary += f"───────────────────────────\n"
        summary += f"✅ <b>成功 ({len(success_list)})：</b> {', '.join(success_list) if success_list else '无'}\n"
        summary += f"❌ <b>失败 ({len(fail_list)})：</b> {', '.join(fail_list) if fail_list else '无'}"
        await message.reply_text(summary, parse_mode="HTML")


# ==================== 镜像清理与分类处理 ====================
async def show_prune_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()

    text = "🧹 <b>Docker 镜像清理中心</b>\n\n"
    text += "请选择清理类型：\n"
    text += "• <b>悬空镜像 (Dangling)</b>：无标签且未被使用的临时镜像层（安全推荐）\n"
    text += "• <b>所有未使用镜像 (All Unused)</b>：没有任何容器正在使用的全部旧镜像（深度清理）"

    cb_dangling = create_cb_data("prune_req", {"all": False})
    cb_all = create_cb_data("prune_req", {"all": True})

    keyboard = [
        [InlineKeyboardButton("🍂 仅清理悬空镜像 (Dangling)", callback_data=cb_dangling)],
        [InlineKeyboardButton("🗑 清理所有未使用镜像 (All Unused)", callback_data=cb_all)],
        [InlineKeyboardButton("🔙 返回主菜单", callback_data=create_cb_data("page_turn", {"page": 1}))]
    ]

    message = update.effective_message
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def cmd_prune(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_permission(update): return
    await show_prune_menu(update, context)


async def ask_prune_confirm(update: Update, prune_all: bool):
    query = update.callback_query
    message = update.effective_message
    
    label = "所有未使用" if prune_all else "悬空 (dangling)"
    await query.edit_message_text(f"🔍 正在扫描系统中的 <b>{label}</b> 镜像...", parse_mode="HTML")

    cmd = ["docker", "images"]
    if not prune_all:
        cmd.extend(["-f", "dangling=true"])
    cmd.extend(["--format", "table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}"])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        stdout, _ = await proc.communicate()
        
        if proc.returncode != 0:
            await message.reply_text(f"❌ 扫描镜像异常: <code>{html.escape(stdout.decode())}</code>", parse_mode="HTML")
            return

        dry_output = stdout.decode('utf-8', errors='replace').strip()

        if not dry_output or len(dry_output.splitlines()) <= 1:
            await message.reply_text(f"✨ 系统内未检测到可清理的 <b>{label}</b> 镜像！", parse_mode="HTML")
            return
    except Exception as e:
        await message.reply_text(f"❌ 执行扫描出错: {html.escape(str(e))}", parse_mode="HTML")
        return

    cb_confirm = create_cb_data("prune_do", {"all": prune_all})
    cb_cancel = create_cb_data("page_turn", {"page": 1})

    keyboard = [
        [
            InlineKeyboardButton("✅ 确认清理", callback_data=cb_confirm),
            InlineKeyboardButton("❌ 取消", callback_data=cb_cancel),
        ]
    ]
    
    safe_dry = html.escape(dry_output[-3000:])
    await message.reply_text(
        f"🧹 <b>待清理镜像预判 (范围: {label})：</b>\n<code>{safe_dry}</code>\n\n确认执行清理吗？",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def do_prune(update: Update, context: ContextTypes.DEFAULT_TYPE, prune_all: bool):
    query = update.callback_query
    lock = get_exec_lock()
    if lock.locked():
        await query.answer("⚠️ 当前已有任务在运行", show_alert=True)
        return
    await query.answer()

    async with lock:
        await query.edit_message_text("🗑 正在执行镜像清理，请稍候...")
        
        cmd = ["docker", "image", "prune", "-f"]
        if prune_all:
            cmd.append("-a")

        await run_command_with_feedback(
            update, context, cmd,
            title="清理系统镜像",
            progress_pct=90
        )


# ==================== 状态速览 (/status) ====================
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_permission(update): return

    message = update.effective_message
    status_msg = await message.reply_text("🔍 正在拉取 Docker 容器状态速览...")

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "-a",
            "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode('utf-8', errors='replace').strip()

        if not output:
            await status_msg.edit_text("⚠️ 未找到正在运行或已停止的 Docker 容器。")
            return

        safe_output = html.escape(output[-3800:])
        await status_msg.edit_text(
            f"📊 <b>Docker 容器实时状态速览</b>\n\n<code>{safe_output}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ 获取状态失败: {html.escape(str(e))}")


# ==================== 命令行指令解析 ====================
async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_permission(update): return

    user_str = get_user_identifier(update)

    if not context.args:
        await update.message.reply_text(
            "💡 <b>/upgrade 命令行升级指南：</b>\n\n"
            "• <code>/upgrade 01</code> : 升级列表中第 01 个项目\n"
            "• <code>/upgrade 01 emby</code> : 仅升级第 01 个项目中的 emby 容器\n"
            "• <code>/upgrade all</code> : 升级所有检测到的项目",
            parse_mode="HTML"
        )
        return

    arg = context.args[0].lower()
    service_name = context.args[1] if len(context.args) > 1 else None
    logger.info(f"▶️ [操作审计] 用户 [{user_str}] 执行命令: /upgrade {' '.join(context.args)}")

    if arg in ("all", "a"):
        keyboard = [[
            InlineKeyboardButton("🚀 确认升级全部", callback_data="upgrade_all_confirm"),
            InlineKeyboardButton("❌ 取消", callback_data=create_cb_data("page_turn", {"page": 1})),
        ]]
        await update.message.reply_text("⚠️ <b>确认批量升级全部项目的全部容器？</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    try:
        num = int(arg)
        if num < 1: raise ValueError
        idx = num - 1
        
        projects = await get_compose_projects()
        if 0 <= idx < len(projects):
            target_p = projects[idx]
            p_name = target_p['name']
            safe_p_name = html.escape(p_name)
            safe_dir = html.escape(target_p['dir'])
            
            if service_name:
                safe_svc = html.escape(service_name)
                cb_confirm = create_cb_data("up_svc_do", {"name": p_name, "svc": service_name})
                keyboard = [[
                    InlineKeyboardButton("✅ 确认升级指定服务", callback_data=cb_confirm),
                    InlineKeyboardButton("❌ 取消", callback_data=create_cb_data("page_turn", {"page": 1})),
                ]]
                await update.message.reply_text(
                    f"🚀 确认升级 <b>{safe_p_name}</b> 中的服务 <code>{safe_svc}</code>？\n📂 路径：<code>{safe_dir}</code>",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML"
                )
            else:
                cb_confirm = create_cb_data("up_p_do", {"name": p_name})
                keyboard = [[
                    InlineKeyboardButton("✅ 确认升级整个项目", callback_data=cb_confirm),
                    InlineKeyboardButton("❌ 取消", callback_data=create_cb_data("page_turn", {"page": 1})),
                ]]
                await update.message.reply_text(
                    f"🚀 确认升级项目 <b>{safe_p_name}</b>？\n📂 路径：<code>{safe_dir}</code>",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML"
                )
        else:
            await update.message.reply_text("❌ 无效的项目序号")
    except ValueError:
        await update.message.reply_text("❌ 格式不正确。示例: <code>/upgrade 01</code> 或 <code>/upgrade 01 emby</code>", parse_mode="HTML")


# ==================== Callback 路由 Handler ====================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _CANCEL_REQUESTED
    query = update.callback_query
    if not await check_permission(update):
        return

    data = query.data

    if data == "noop":
        await query.answer()
        return

    if data == "upgrade_all":
        await query.answer()
        projects = await get_compose_projects()
        keyboard = [[
            InlineKeyboardButton("🚀 确认升级全部", callback_data="upgrade_all_confirm"),
            InlineKeyboardButton("❌ 取消", callback_data=create_cb_data("page_turn", {"page": 1})),
        ]]
        await query.edit_message_text(
            f"⚠️ <b>确认批量升级全部项目？</b>\n共有 {len(projects)} 个 Compose 项目。",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    if data == "upgrade_all_confirm":
        await do_upgrade_all(update, context)
        return

    if ":" in data:
        action, _ = data.split(":", 1)
        payload = get_cb_payload(data)

        if action == "task_cancel":
            _CANCEL_REQUESTED = True
            if _CURRENT_PROCESS:
                try: _CURRENT_PROCESS.kill()
                except Exception: pass
            await query.answer("🛑 已发送中断信号，正在停止任务...", show_alert=True)
            return

        if payload is None and action not in ("task_cancel",):
            await query.answer("⚠️ 菜单响应超时，请重新输入 /list 打开", show_alert=True)
            return

        if action == "page_turn":
            page = payload.get("page", 1)
            await cmd_list(update, context, page=page)

        elif action == "p_sel":
            await show_project_detail(update, payload["name"], back_page=payload.get("page", 1))

        elif action == "up_s_ask":
            await ask_single_upgrade(update, payload["name"], back_page=payload.get("page", 1))

        elif action == "up_svc_ask":
            await ask_svc_upgrade(update, payload["name"], payload["svc"], back_page=payload.get("page", 1))

        elif action == "up_p_do":
            await do_upgrade_project(update, context, payload["name"])

        elif action == "up_svc_do":
            await do_upgrade_service(update, context, payload["name"], payload["svc"])

        elif action == "prune_menu":
            await show_prune_menu(update, context)

        elif action == "prune_req":
            await ask_prune_confirm(update, prune_all=payload["all"])

        elif action == "prune_do":
            await do_prune(update, context, prune_all=payload["all"])


# ==================== 主程序入口 ====================
def main():
    if not BOT_TOKEN:
        logger.error("请设置 BOT_TOKEN 环境变量")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("prune", cmd_prune))
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(global_error_handler)

    logger.info("Bot 成功启动，所有优化与 UI 美化处理完成！")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
