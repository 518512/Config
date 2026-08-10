#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LDMG - Lite Docker Manager Gram
基于 TG Bot 的 Docker Compose 升级助手 (完整优化修复版)
"""

import os
import re
import json
import time
import html
import asyncio
import subprocess
import logging
from typing import List, Dict, Optional

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

# 全局任务锁
_EXEC_LOCK: Optional[asyncio.Lock] = None

def get_exec_lock() -> asyncio.Lock:
    """获取并发互斥锁"""
    global _EXEC_LOCK
    if _EXEC_LOCK is None:
        _EXEC_LOCK = asyncio.Lock()
    return _EXEC_LOCK

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
    for handler in logging.getLogger().handlers:
        handler.addFilter(TokenMaskFilter(BOT_TOKEN))
    logging.getLogger().addFilter(TokenMaskFilter(BOT_TOKEN))


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

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    from telegram.error import NetworkError, TimedOut, BadRequest
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.warning(f"🌐 遇到临时网络波动: {context.error}")
        return
    if isinstance(context.error, BadRequest) and "Message is not modified" in str(context.error):
        return
    logger.error("❌ 未捕获的系统异常:", exc_info=context.error)


# ==================== Docker Compose 扫描与服务获取 ====================
def get_project_services(work_dir: str) -> List[str]:
    """通过 docker compose config --services 获取项目里定义的准确 service 名称"""
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
    seen_dirs = set()

    try:
        result = subprocess.run(
            ["docker", "compose", "ls", "-a", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                name = item.get("Name", "")
                status = item.get("Status", "")
                config_files = item.get("ConfigFiles", "")
                if not name:
                    continue
                first_file = config_files.split(",")[0].strip() if config_files else ""
                work_dir = os.path.dirname(first_file) if first_file else ""
                if work_dir and os.path.isdir(work_dir) and work_dir not in seen_dirs:
                    seen_dirs.add(work_dir)
                    services = get_project_services(work_dir)
                    projects.append({
                        "name": name,
                        "dir": work_dir,
                        "status": status,
                        "services": services,
                    })
    except Exception as e:
        logger.warning(f"docker compose ls 扫描失败: {e}")

    try:
        result = subprocess.run(
            [
                "docker", "ps", "-a",
                "--filter", "label=com.docker.compose.project",
                "--format",
                '{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.project.working_dir"}}',
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                name, work_dir = parts[0].strip(), parts[1].strip()
                if work_dir and os.path.isdir(work_dir) and work_dir not in seen_dirs:
                    seen_dirs.add(work_dir)
                    services = get_project_services(work_dir)
                    projects.append({
                        "name": name,
                        "dir": work_dir,
                        "status": "running",
                        "services": services,
                    })
    except Exception as e:
        logger.warning(f"标签扫描失败: {e}")

    projects.sort(key=lambda x: x["name"])
    return projects

async def get_compose_projects() -> List[Dict]:
    return await asyncio.to_thread(_get_compose_projects_sync)


# ==================== 执行命令并流式推送日志 ====================
async def run_command_with_feedback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cmd: List[str],
    cwd: Optional[str] = None,
    title: str = "执行中",
):
    message = update.effective_message
    safe_title = html.escape(title)
    safe_cmd = html.escape(' '.join(cmd))
    
    status_msg = await message.reply_text(
        f"⏳ <b>{safe_title}</b>\n<code>{safe_cmd}</code>", 
        parse_mode="HTML"
    )

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        output_lines = []
        last_update_time = time.time()

        while True:
            line_bytes = await process.stdout.readline()
            if not line_bytes:
                break
            
            line = line_bytes.decode('utf-8', errors='replace').rstrip()
            output_lines.append(line)

            now = time.time()
            is_key_line = any(
                k in line.lower() for k in ["pulling", "downloaded", "created", "started", "error", "done"]
            )
            if (len(output_lines) % 8 == 0 or is_key_line) and (now - last_update_time >= 1.5):
                preview = "\n".join(output_lines[-15:])
                safe_preview = html.escape(preview[-3500:])
                try:
                    await status_msg.edit_text(
                        f"⏳ <b>{safe_title}</b>\n<code>{safe_preview}</code>",
                        parse_mode="HTML",
                    )
                    last_update_time = now
                except Exception:
                    pass

        returncode = await process.wait()
        full_output = "\n".join(output_lines[-30:])
        safe_full_output = html.escape(full_output[-3500:])

        try:
            if returncode == 0:
                await status_msg.edit_text(
                    f"✅ <b>{safe_title} 完成</b>\n<code>{safe_full_output}</code>",
                    parse_mode="HTML",
                )
            else:
                await status_msg.edit_text(
                    f"❌ <b>{safe_title} 失败 (exit {returncode})</b>\n<code>{safe_full_output}</code>",
                    parse_mode="HTML",
                )
        except Exception:
            pass  # 忽略内容未变引起的 Message is not modified 异常

        return returncode == 0

    except Exception as e:
        safe_err = html.escape(str(e))
        try:
            await status_msg.edit_text(f"❌ 执行异常: {safe_err}")
        except Exception:
            pass
        return False


# ==================== 核心面板与菜单交互 ====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_permission(update):
        return
    await cmd_list(update, context)

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """主面板列表"""
    if not await check_permission(update):
        return

    user_str = get_user_identifier(update)
    logger.info(f"▶️ [操作审计] 用户 [{user_str}] 查看主项目列表")

    projects = await get_compose_projects()
    
    running_cnt = sum(1 for p in projects if "running" in p["status"].lower())
    
    text = "🐳 <b>Docker Compose 管理面板</b>\n"
    text += f"📊 <b>项目总数：</b>{len(projects)}  |  🟢 <b>运行中：</b>{running_cnt}\n"
    text += "────────────────\n"

    keyboard = [
        [InlineKeyboardButton("🧹 00. 清理未使用镜像", callback_data="prune")]
    ]

    if not projects:
        text += "\n⚠️ 暂未检测到 Docker Compose 项目"
    else:
        for i, p in enumerate(projects, 1):
            num = f"{i:02d}"
            name = p["name"]
            status = p["status"]
            status_icon = "🟢" if "running" in status.lower() else "🟡"
            
            safe_name = html.escape(name)
            safe_dir = html.escape(p["dir"])
            services_str = ", ".join(p["services"]) if p["services"] else "-"
            safe_services = html.escape(services_str)

            text += f"<b>{num}.</b> {safe_name}  {status_icon} [{html.escape(status)}]\n"
            text += f"     📂 <code>{safe_dir}</code>\n"
            text += f"     📦 {safe_services}\n\n"
            
            # 使用精简的 callback_data 前缀，确保不破 64 字节限制
            if len(p["services"]) > 1:
                keyboard.append([
                    InlineKeyboardButton(
                        f"{num}. {name} (多服务)",
                        callback_data=f"p_sel:{name}"
                    )
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton(
                        f"{num}. {name}",
                        callback_data=f"up_s_ask:{name}"
                    )
                ])

    keyboard.append([
        InlineKeyboardButton("🔄 刷新列表", callback_data="refresh"),
        InlineKeyboardButton("⬆️ 升级全部项目", callback_data="upgrade_all")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=reply_markup, parse_mode="HTML"
        )
        await update.callback_query.answer()
    else:
        await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode="HTML"
        )


async def show_project_detail(update: Update, project_name: str):
    """二级菜单：针对包含多个服务容器的项目"""
    query = update.callback_query
    projects = await get_compose_projects()
    target_p = next((p for p in projects if p["name"] == project_name), None)

    if not target_p:
        await query.answer("❌ 未找到该项目", show_alert=True)
        return

    safe_name = html.escape(target_p['name'])
    safe_dir = html.escape(target_p['dir'])

    text = f"📦 <b>项目详情：{safe_name}</b>\n\n"
    text += f"📂 <b>配置路径：</b>\n<code>{safe_dir}</code>\n\n"
    text += "⚙️ <b>请选择升级范围：</b>\n"

    keyboard = [
        [InlineKeyboardButton("⚡ 升级整个项目 (所有容器)", callback_data=f"up_c_ask:{project_name}")]
    ]

    if target_p["services"]:
        for svc in target_p["services"]:
            keyboard.append([
                InlineKeyboardButton(
                    f"🔹 仅升级服务: {svc}",
                    callback_data=f"up_svc_ask:{project_name}:{svc}"
                )
            ])

    keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="refresh")])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    await query.answer()


# ==================== 确认弹窗函数 ====================
async def ask_single_upgrade(update: Update, project_name: str):
    """项目升级确认界面"""
    query = update.callback_query
    projects = await get_compose_projects()
    target_p = next((p for p in projects if p["name"] == project_name), None)

    safe_name = html.escape(project_name)
    safe_dir = html.escape(target_p["dir"]) if target_p else "未知路径"

    keyboard = [
        [
            InlineKeyboardButton("✅ 确认升级", callback_data=f"up_p_do:{project_name}"),
            InlineKeyboardButton("🔙 返回", callback_data="refresh"),
        ]
    ]

    text = (
        f"🚀 <b>确认升级项目 [{safe_name}]？</b>\n\n"
        f"📂 <b>路径：</b><code>{safe_dir}</code>\n\n"
        f"💡 将会自动执行 <code>pull</code> 拉取新镜像并 <code>up -d</code> 重建启动。"
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    await query.answer()


async def ask_svc_upgrade(update: Update, project_name: str, service_name: str):
    """指定服务升级确认界面"""
    query = update.callback_query
    projects = await get_compose_projects()
    target_p = next((p for p in projects if p["name"] == project_name), None)

    safe_p = html.escape(project_name)
    safe_s = html.escape(service_name)
    safe_dir = html.escape(target_p["dir"]) if target_p else "未知路径"

    keyboard = [
        [
            InlineKeyboardButton("✅ 确认升级", callback_data=f"up_svc_do:{project_name}:{service_name}"),
            InlineKeyboardButton("🔙 返回", callback_data=f"p_sel:{project_name}"),
        ]
    ]

    text = (
        f"🚀 <b>确认升级服务 [{safe_s}]？</b>\n\n"
        f"📦 <b>所属项目：</b>{safe_p}\n"
        f"📂 <b>路径：</b><code>{safe_dir}</code>\n\n"
        f"💡 仅更新此单一服务容器，不会影响项目内其他服务。"
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    await query.answer()


# ==================== 执行底层逻辑 ====================
async def do_upgrade_project(update: Update, context: ContextTypes.DEFAULT_TYPE, project_name: str):
    """升级整个 Compose 项目"""
    lock = get_exec_lock()
    query = update.callback_query
    user_str = get_user_identifier(update)

    if lock.locked():
        if query: await query.answer("⚠️ 当前已有其他任务在运行中", show_alert=True)
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
        logger.info(f"🚀 [操作审计] 用户 [{user_str}] 升级完整项目 [{project_name}]")

        await message.reply_text(f"🚀 开始升级项目 <b>{safe_name}</b>...", parse_mode="HTML")

        pull_ok = await run_command_with_feedback(
            update, context,
            ["docker", "compose", "pull"],
            cwd=target_p["dir"],
            title=f"拉取镜像 - {target_p['name']}",
        )
        if not pull_ok: return

        up_ok = await run_command_with_feedback(
            update, context,
            ["docker", "compose", "up", "-d"],
            cwd=target_p["dir"],
            title=f"重建启动 - {target_p['name']}",
        )

        if up_ok:
            logger.info(f"✅ 项目 [{project_name}] 升级完成")
            await message.reply_text(f"🎉 项目 <b>{safe_name}</b> 升级完成！", parse_mode="HTML")


async def do_upgrade_service(update: Update, context: ContextTypes.DEFAULT_TYPE, project_name: str, service_name: str):
    """仅升级项目下的某一个服务容器"""
    lock = get_exec_lock()
    query = update.callback_query
    user_str = get_user_identifier(update)

    if lock.locked():
        if query: await query.answer("⚠️ 当前已有其他任务在运行中", show_alert=True)
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

        logger.info(f"🚀 [操作审计] 用户 [{user_str}] 升级项目 [{project_name}] 中的单服务 [{service_name}]")

        await message.reply_text(f"🚀 开始升级 <b>{safe_name}</b> 下的服务 <code>{safe_svc}</code>...", parse_mode="HTML")

        pull_ok = await run_command_with_feedback(
            update, context,
            ["docker", "compose", "pull", service_name],
            cwd=target_p["dir"],
            title=f"拉取 - {service_name}",
        )
        if not pull_ok: return

        up_ok = await run_command_with_feedback(
            update, context,
            ["docker", "compose", "up", "-d", service_name],
            cwd=target_p["dir"],
            title=f"重建 - {service_name}",
        )

        if up_ok:
            logger.info(f"✅ 服务 [{service_name}] 升级完成")
            await message.reply_text(f"🎉 服务 <code>{safe_svc}</code> 升级完成！", parse_mode="HTML")


async def do_upgrade_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """升级全部项目"""
    lock = get_exec_lock()
    query = update.callback_query
    user_str = get_user_identifier(update)

    if lock.locked():
        if query: await query.answer("⚠️ 当前已有其他任务在运行中", show_alert=True)
        return

    if query: await query.answer()

    async with lock:
        projects = await get_compose_projects()
        if not projects:
            await update.effective_message.reply_text("未检测到可升级的项目")
            return

        logger.info(f"🚀 [操作审计] 用户 [{user_str}] 开始批量升级全部 {len(projects)} 个项目...")
        message = update.effective_message
        await message.reply_text(f"🚀 开始升级全部 {len(projects)} 个项目...")

        for i, p in enumerate(projects, 1):
            p_name = p['name']
            safe_name = html.escape(p_name)
            
            await message.reply_text(f"[{i}/{len(projects)}] 升级 <b>{safe_name}</b>...", parse_mode="HTML")
            
            pull_ok = await run_command_with_feedback(
                update, context,
                ["docker", "compose", "pull"],
                cwd=p["dir"],
                title=f"拉取 - {p_name}",
            )
            
            if pull_ok:
                await run_command_with_feedback(
                    update, context,
                    ["docker", "compose", "up", "-d"],
                    cwd=p["dir"],
                    title=f"启动 - {p_name}",
                )

        await message.reply_text("🎉 全部项目批量升级完成！")


async def cmd_prune(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_permission(update): return
    
    # 若从按钮触发，回应 answer 以停止转圈动画
    if update.callback_query:
        await update.callback_query.answer()

    message = update.effective_message
    await message.reply_text("🔍 正在扫描未使用的无头 (dangling) 镜像...")
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "images", "-f", "dangling=true",
            "--format", "table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        stdout, _ = await proc.communicate()
        dry_output = stdout.decode('utf-8', errors='replace').strip()

        if not dry_output or len(dry_output.splitlines()) <= 1:
            await message.reply_text("✨ 当前系统内没有需要清理的无用镜像！")
            return
    except Exception as e:
        dry_output = str(e)

    keyboard = [
        [
            InlineKeyboardButton("✅ 确认清理", callback_data="prune_confirm"),
            InlineKeyboardButton("❌ 取消", callback_data="cancel"),
        ]
    ]
    safe_dry = html.escape(dry_output[-3000:])
    await message.reply_text(
        f"🧹 <b>将清理以下悬空镜像：</b>\n<code>{safe_dry}</code>\n\n确认清理吗？",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )

async def do_prune(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lock = get_exec_lock()
    if lock.locked():
        await query.answer("⚠️ 当前已有其他任务在运行中", show_alert=True)
        return
    await query.answer()
    async with lock:
        await query.edit_message_text("🗑 正在清理无用镜像，请稍等...")
        await run_command_with_feedback(
            update, context,
            ["docker", "image", "prune", "-a", "-f"],
            title="清理未使用镜像",
        )


# ==================== 命令行指令解析 ====================
async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_permission(update):
        return

    user_str = get_user_identifier(update)

    if not context.args:
        await update.message.reply_text(
            "💡 <b>命令行升级说明：</b>\n"
            "• <code>/upgrade 01</code> : 升级第 01 个项目的所有容器\n"
            "• <code>/upgrade 01 emby</code> : 仅升级第 01 项目中的 emby 容器\n"
            "• <code>/upgrade all</code> : 批量升级所有项目",
            parse_mode="HTML"
        )
        return

    arg = context.args[0].lower()
    service_name = context.args[1] if len(context.args) > 1 else None
    logger.info(f"▶️ [操作审计] 用户 [{user_str}] 执行命令: /upgrade {' '.join(context.args)}")

    if arg in ("all", "a"):
        keyboard = [[
            InlineKeyboardButton("🚀 确认升级全部", callback_data="upgrade_all_confirm"),
            InlineKeyboardButton("❌ 取消", callback_data="cancel"),
        ]]
        await update.message.reply_text("⚠️ <b>确认升级全部项目的全部容器？</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
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
                keyboard = [[
                    InlineKeyboardButton("✅ 确认升级指定服务", callback_data=f"up_svc_do:{p_name}:{service_name}"),
                    InlineKeyboardButton("❌ 取消", callback_data="cancel"),
                ]]
                await update.message.reply_text(
                    f"🚀 确认升级 <b>{safe_p_name}</b> 中的服务 <code>{safe_svc}</code>？\n📂 路径：<code>{safe_dir}</code>",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML"
                )
            else:
                keyboard = [[
                    InlineKeyboardButton("✅ 确认升级整个项目", callback_data=f"up_p_do:{p_name}"),
                    InlineKeyboardButton("❌ 取消", callback_data="cancel"),
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


# ==================== 按钮回调分发 Handler (短名路由) ====================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await check_permission(update):
        return

    data = query.data

    if data == "refresh":
        await cmd_list(update, context)

    elif data == "prune":
        await cmd_prune(update, context)

    elif data == "prune_confirm":
        await do_prune(update, context)

    elif data == "cancel":
        await query.answer("已取消")
        await query.edit_message_text("❌ 已取消操作")

    elif data.startswith("p_sel:"):
        p_name = data.split(":", 1)[1]
        await show_project_detail(update, p_name)

    # 询问 confirmation 拦截器 (使用精简 callback 前缀)
    elif data.startswith("up_s_ask:") or data.startswith("up_c_ask:"):
        p_name = data.split(":", 1)[1]
        await ask_single_upgrade(update, p_name)

    elif data.startswith("up_svc_ask:"):
        _, p_name, svc_name = data.split(":", 2)
        await ask_svc_upgrade(update, p_name, svc_name)

    # 实际底层执行逻辑触发点
    elif data.startswith("up_p_do:"):
        p_name = data.split(":", 1)[1]
        await do_upgrade_project(update, context, p_name)

    elif data.startswith("up_svc_do:"):
        _, p_name, svc_name = data.split(":", 2)
        await do_upgrade_service(update, context, p_name, svc_name)

    elif data == "upgrade_all":
        await query.answer()
        projects = await get_compose_projects()
        keyboard = [[
            InlineKeyboardButton("🚀 确认升级全部", callback_data="upgrade_all_confirm"),
            InlineKeyboardButton("❌ 取消", callback_data="cancel"),
        ]]
        await query.edit_message_text(
            f"⚠️ <b>确认批量升级全部项目？</b>\n共包含 {len(projects)} 个 Compose 项目。",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif data == "upgrade_all_confirm":
        await do_upgrade_all(update, context)


# ==================== 主入口 ====================
def main():
    if not BOT_TOKEN:
        logger.error("请设置 BOT_TOKEN 环境变量")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("prune", cmd_prune))
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(global_error_handler)

    logger.info("Bot 成功启动！")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
