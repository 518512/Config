#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LDMG - Lite Docker Manager Gram
基于 TG Bot 的 Docker Compose 升级助手
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

# 全局任务锁（延迟初始化，防止事件循环未启动引发 RuntimeError）
_EXEC_LOCK: Optional[asyncio.Lock] = None

def get_exec_lock() -> asyncio.Lock:
    """获取并发互斥锁（确保在活跃的 Event Loop 中创建）"""
    global _EXEC_LOCK
    if _EXEC_LOCK is None:
        _EXEC_LOCK = asyncio.Lock()
    return _EXEC_LOCK

# ==================== 日志与安全脱敏配置 ====================
# 1. 降低第三方 HTTP 库的日志级别，停止轮询 getUpdates 时的刷屏与 Token 输出
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# 2. 自定义日志脱敏过滤器（防止任何地方意外打出 Token）
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

# 基础日志格式配置
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# 为 Root Logger 挂载脱敏过滤器
if BOT_TOKEN:
    for handler in logging.getLogger().handlers:
        handler.addFilter(TokenMaskFilter(BOT_TOKEN))
    logging.getLogger().addFilter(TokenMaskFilter(BOT_TOKEN))


# ==================== 权限检查与审计辅助 ====================
def is_allowed(user_id: int) -> bool:
    """检查用户 ID 是否在白名单中"""
    if not ALLOWED_USER_IDS:
        return False
    return user_id in ALLOWED_USER_IDS


async def check_permission(update: Update) -> bool:
    """拦截器：校验 Telegram 发起者的操作权限"""
    user = update.effective_user
    if not user or not is_allowed(user.id):
        if update.message:
            await update.message.reply_text("❌ 无权限操作")
        elif update.callback_query:
            await update.callback_query.answer("❌ 无权限", show_alert=True)
        return False
    return True


def get_user_identifier(update: Update) -> str:
    """提取操作人的 ID 和 Username，便于审计日志追踪"""
    user = update.effective_user
    if not user:
        return "Unknown User"
    username = f"@{user.username}" if user.username else user.first_name
    return f"{user.id} ({username})"


# ==================== 核心：获取 Compose 项目 ====================
def _get_compose_projects_sync() -> List[Dict]:
    """底层同步函数：扫描系统中的所有 Compose 项目"""
    projects = []
    seen_dirs = set()

    # 方式 1：使用 docker compose ls -a 获取官方管理列表
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
                    containers = get_project_containers(name)
                    projects.append({
                        "name": name,
                        "dir": work_dir,
                        "status": status,
                        "containers": containers,
                    })
    except Exception as e:
        logger.warning(f"docker compose ls 扫描失败: {e}")

    # 方式 2：使用容器 Label 扫描进行补充，防止漏检
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
                    containers = get_project_containers(name)
                    projects.append({
                        "name": name,
                        "dir": work_dir,
                        "status": "from-label",
                        "containers": containers,
                    })
    except Exception as e:
        logger.warning(f"标签扫描失败: {e}")

    # 按照项目名称进行排序
    projects.sort(key=lambda x: x["name"])
    return projects


async def get_compose_projects() -> List[Dict]:
    """异步封装：将阻塞的 subprocess 放到单独线程中运行，避免卡死 Bot"""
    return await asyncio.to_thread(_get_compose_projects_sync)


def get_project_containers(project_name: str) -> str:
    """获取指定 Compose 项目对应的所有容器名称列表"""
    try:
        result = subprocess.run(
            [
                "docker", "ps", "-a",
                "--filter", f"label=com.docker.compose.project={project_name}",
                "--format", "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            names = [n.strip() for n in result.stdout.strip().splitlines() if n.strip()]
            return ",".join(names)
    except Exception:
        pass
    return ""


# ==================== 执行命令并实时反馈 ====================
async def run_command_with_feedback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cmd: List[str],
    cwd: Optional[str] = None,
    title: str = "执行中",
):
    """
    流式执行命令并将实时日志推送至 Telegram。
    包含 HTML 安全转义与 API 请求限频控制（防止 429 报错）。
    """
    message = update.effective_message
    safe_title = html.escape(title)
    safe_cmd = html.escape(' '.join(cmd))
    
    status_msg = await message.reply_text(
        f"⏳ {safe_title}\n<code>{safe_cmd}</code>", 
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
            # 限制更新频率：行数积攒或关键词触发，且距离上次更新 >= 1.5s
            if (len(output_lines) % 8 == 0 or is_key_line) and (now - last_update_time >= 1.5):
                preview = "\n".join(output_lines[-15:])
                safe_preview = html.escape(preview[-3500:])
                try:
                    await status_msg.edit_text(
                        f"⏳ {safe_title}\n<code>{safe_preview}</code>",
                        parse_mode="HTML",
                    )
                    last_update_time = now
                except Exception:
                    pass

        returncode = await process.wait()
        full_output = "\n".join(output_lines[-30:])
        safe_full_output = html.escape(full_output[-3500:])

        if returncode == 0:
            await status_msg.edit_text(
                f"✅ {safe_title} 完成\n<code>{safe_full_output}</code>",
                parse_mode="HTML",
            )
        else:
            await status_msg.edit_text(
                f"❌ {safe_title} 失败 (exit {returncode})\n<code>{safe_full_output}</code>",
                parse_mode="HTML",
            )
        return returncode == 0

    except Exception as e:
        safe_err = html.escape(str(e))
        await status_msg.edit_text(f"❌ 执行异常: {safe_err}")
        return False


# ==================== 命令与业务处理函数 ====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_permission(update):
        return
    await cmd_list(update, context)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示项目面板和交互菜单"""
    if not await check_permission(update):
        return

    projects = await get_compose_projects()
    text = "📋 <b>Docker Compose 升级助手 - TGBOT版</b>\n\n"
    text += "<b>00.</b> 清理未使用镜像\n"
    text += "────────────────\n"

    keyboard = [
        [InlineKeyboardButton("00. 清理未使用镜像", callback_data="prune")]
    ]

    if not projects:
        text += "\n暂无 Compose 项目"
    else:
        for i, p in enumerate(projects, 1):
            num = f"{i:02d}"
            name = p["name"]
            status = p["status"]
            status_icon = "🟢" if "running" in status else "🟡"
            
            safe_name = html.escape(name)
            safe_dir = html.escape(p["dir"])
            safe_containers = html.escape(p["containers"] or "-")

            text += f"<b>{num}.</b> {safe_name}  {status_icon} [{html.escape(status)}]\n"
            text += f"     路径: <code>{safe_dir}</code>\n"
            text += f"     容器: {safe_containers}\n\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"{num}. 升级 {name}",
                    callback_data=f"upgrade:{name}"
                )
            ])

    keyboard.append([InlineKeyboardButton("🔄 刷新列表", callback_data="refresh")])
    keyboard.append([InlineKeyboardButton("⬆️ 升级全部", callback_data="upgrade_all")])

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


async def cmd_prune(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """清理镜像请求：先扫描未使用的悬空镜像展示，等待确认"""
    if not await check_permission(update):
        return

    message = update.effective_message
    await message.reply_text("🔍 正在扫描未使用的镜像...")

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
            await message.reply_text("✨ 当前没有需要清理的无用 (dangling) 镜像！")
            return

    except Exception as e:
        dry_output = str(e)

    keyboard = [
        [
            InlineKeyboardButton("✅ 确认清理", callback_data="prune_confirm"),
            InlineKeyboardButton("❌ 取消", callback_data="cancel"),
        ]
    ]
    safe_dry_output = html.escape(dry_output[-3000:])
    await message.reply_text(
        f"<b>将清理以下无用镜像 (dangling images)：</b>\n<code>{safe_dry_output}</code>\n\n确认清理吗？",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def do_prune(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """真实执行镜像清理任务"""
    query = update.callback_query
    lock = get_exec_lock()
    user_str = get_user_identifier(update)

    if lock.locked():
        logger.warning(f"用户 [{user_str}] 尝试清理镜像，但已有任务在运行中")
        await query.answer("⚠️ 当前已有其他任务在运行中，请稍后再试", show_alert=True)
        return

    await query.answer()
    async with lock:
        logger.info(f"▶️ [操作审计] 用户 [{user_str}] 开始执行无用镜像清理...")
        await query.edit_message_text("🗑 正在删除未使用镜像...")

        success = await run_command_with_feedback(
            update, context,
            ["docker", "image", "prune", "-a", "-f"],
            title="清理未使用镜像",
        )
        
        if success:
            logger.info(f"✅ [操作审计] 用户 [{user_str}] 的镜像清理任务执行成功")
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "system", "df",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT
                )
                stdout, _ = await proc.communicate()
                safe_df = html.escape(stdout.decode('utf-8', errors='replace'))
                await update.effective_message.reply_text(
                    f"<b>当前磁盘使用情况：</b>\n<code>{safe_df}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        else:
            logger.error(f"❌ [操作审计] 用户 [{user_str}] 的镜像清理任务执行失败")


async def do_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE, project_name: str):
    """真实执行单个项目的升级流程"""
    lock = get_exec_lock()
    query = update.callback_query
    user_str = get_user_identifier(update)

    if lock.locked():
        logger.warning(f"用户 [{user_str}] 尝试升级项目 [{project_name}]，但已有任务在运行中")
        if query:
            await query.answer("⚠️ 当前已有其他任务在运行中，请稍后再试", show_alert=True)
        else:
            await update.effective_message.reply_text("⚠️ 当前已有其他任务在运行中，请稍后再试。")
        return

    if query:
        await query.answer()

    async with lock:
        projects = await get_compose_projects()
        target_p = next((p for p in projects if p["name"] == project_name), None)

        if not target_p:
            logger.error(f"❌ [操作审计] 用户 [{user_str}] 尝试升级不存在的项目: {project_name}")
            await update.effective_message.reply_text(f"❌ 未找到项目: <code>{html.escape(project_name)}</code>", parse_mode="HTML")
            return

        message = update.effective_message
        safe_name = html.escape(target_p['name'])
        safe_dir = html.escape(target_p['dir'])

        logger.info(f"🚀 [操作审计] 用户 [{user_str}] 开始升级项目 [{project_name}] (路径: {target_p['dir']})")

        await message.reply_text(
            f"🚀 开始升级项目 <b>{safe_name}</b>\n路径: <code>{safe_dir}</code>",
            parse_mode="HTML",
        )

        # 1. 执行 pull
        pull_ok = await run_command_with_feedback(
            update, context,
            ["docker", "compose", "pull"],
            cwd=target_p["dir"],
            title=f"拉取镜像 - {target_p['name']}",
        )
        if not pull_ok:
            logger.error(f"❌ [操作审计] 项目 [{project_name}] 镜像拉取 (pull) 失败，中断后续启动流程")
            return

        # 2. 执行 up -d
        up_ok = await run_command_with_feedback(
            update, context,
            ["docker", "compose", "up", "-d"],
            cwd=target_p["dir"],
            title=f"重建启动 - {target_p['name']}",
        )

        if up_ok:
            logger.info(f"✅ [操作审计] 项目 [{project_name}] 升级完成并成功重启")
            await message.reply_text(f"✅ 项目 <b>{safe_name}</b> 升级完成", parse_mode="HTML")
        else:
            logger.error(f"❌ [操作审计] 项目 [{project_name}] 重建启动 (up -d) 失败")


async def do_upgrade_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """真实执行所有项目的批量升级"""
    lock = get_exec_lock()
    query = update.callback_query
    user_str = get_user_identifier(update)

    if lock.locked():
        logger.warning(f"用户 [{user_str}] 尝试批量升级全部项目，但已有任务在运行中")
        if query:
            await query.answer("⚠️ 当前已有其他任务在运行中，请稍后再试", show_alert=True)
        else:
            await update.effective_message.reply_text("⚠️ 当前已有其他任务在运行中，请稍后再试。")
        return

    if query:
        await query.answer()

    async with lock:
        projects = await get_compose_projects()
        if not projects:
            logger.info(f"用户 [{user_str}] 触发升级全部，但未检测到可升级项目")
            await update.effective_message.reply_text("没有可升级的项目")
            return

        logger.info(f"🚀 [操作审计] 用户 [{user_str}] 开始批量升级全部 {len(projects)} 个项目...")
        message = update.effective_message
        await message.reply_text(f"🚀 开始升级全部 {len(projects)} 个项目...")

        for i, p in enumerate(projects, 1):
            p_name = p['name']
            safe_name = html.escape(p_name)
            
            logger.info(f"  └─ [{i}/{len(projects)}] 正在升级子项目: {p_name}")
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
            else:
                logger.error(f"  └─ 项目 [{p_name}] 拉取失败，跳过重启过程")

        logger.info(f"✅ [操作审计] 用户 [{user_str}] 的批量升级全部项目流程结束")
        await message.reply_text("✅ 全部项目升级完成")


# ==================== 回调查询与确认逻辑 ====================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理内嵌按钮的点击回调事件"""
    query = update.callback_query
    if not await check_permission(update):
        return

    data = query.data

    # 1. 刷新列表
    if data == "refresh":
        await cmd_list(update, context)

    # 2. 清理镜像（弹窗二次确认）
    elif data == "prune":
        await query.answer()
        await cmd_prune(update, context)

    # 3. 确认清理镜像
    elif data == "prune_confirm":
        await do_prune(update, context)

    # 4. 取消操作
    elif data == "cancel":
        await query.answer("已取消")
        await query.edit_message_text("❌ 已取消操作")

    # 5. 升级单个项目：触发二次确认面板
    elif data.startswith("upgrade:"):
        await query.answer()
        p_name = data.split(":", 1)[1]
        projects = await get_compose_projects()
        target_p = next((p for p in projects if p["name"] == p_name), None)

        if target_p:
            safe_name = html.escape(target_p['name'])
            safe_dir = html.escape(target_p['dir'])
            keyboard = [
                [
                    InlineKeyboardButton("✅ 确认升级", callback_data=f"upgrade_confirm:{p_name}"),
                    InlineKeyboardButton("❌ 取消", callback_data="cancel"),
                ]
            ]
            await query.edit_message_text(
                f"🚀 <b>确认升级项目 [{safe_name}]？</b>\n路径: <code>{safe_dir}</code>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text("❌ 该项目不存在或已被移除")

    # 6. 确认升级单个项目：真实开始
    elif data.startswith("upgrade_confirm:"):
        p_name = data.split(":", 1)[1]
        await do_upgrade(update, context, p_name)

    # 7. 升级全部项目：触发二次确认面板
    elif data == "upgrade_all":
        await query.answer()
        projects = await get_compose_projects()
        keyboard = [
            [
                InlineKeyboardButton("🚀 确认升级全部", callback_data="upgrade_all_confirm"),
                InlineKeyboardButton("❌ 取消", callback_data="cancel"),
            ]
        ]
        await query.edit_message_text(
            f"⚠️ <b>确认升级全部项目？</b>\n\n共检测到 <b>{len(projects)}</b> 个项目，升级过程中相关服务将会重启。",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

    # 8. 确认升级全部项目：真实开始
    elif data == "upgrade_all_confirm":
        await do_upgrade_all(update, context)


async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """支持命令行调用：/upgrade 01 或 /upgrade all"""
    if not await check_permission(update):
        return

    if not context.args:
        await update.message.reply_text("用法: /upgrade 01  或  /upgrade all")
        return

    arg = context.args[0].lower()

    if arg in ("all", "a"):
        keyboard = [
            [
                InlineKeyboardButton("🚀 确认升级全部", callback_data="upgrade_all_confirm"),
                InlineKeyboardButton("❌ 取消", callback_data="cancel"),
            ]
        ]
        await update.message.reply_text(
            "⚠️ <b>确认升级全部项目？</b>\n升级过程中相关服务将会重启。",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        return

    if arg in ("00", "0"):
        await cmd_prune(update, context)
        return

    try:
        num = int(arg)
        if num < 1:
            raise ValueError
        
        idx = num - 1
        projects = await get_compose_projects()
        if 0 <= idx < len(projects):
            p = projects[idx]
            safe_name = html.escape(p['name'])
            safe_dir = html.escape(p['dir'])
            keyboard = [
                [
                    InlineKeyboardButton("✅ 确认升级", callback_data=f"upgrade_confirm:{p['name']}"),
                    InlineKeyboardButton("❌ 取消", callback_data="cancel"),
                ]
            ]
            await update.message.reply_text(
                f"🚀 <b>确认升级项目 [{safe_name}]？</b>\n路径: <code>{safe_dir}</code>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("❌ 无效的项目序号")
    except ValueError:
        await update.message.reply_text("请输入正确的序号，例如 /upgrade 01")


# ==================== 主函数入口 ====================
def main():
    if not BOT_TOKEN:
        logger.error("请设置 BOT_TOKEN 环境变量")
        return
    if not ALLOWED_USER_IDS:
        logger.warning("警告：ALLOWED_USER_IDS 为空，将拒绝所有请求")

    app = Application.builder().token(BOT_TOKEN).build()

    # 注册指令监听
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("prune", cmd_prune))
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    
    # 注册按钮回调监听
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot 启动中...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
