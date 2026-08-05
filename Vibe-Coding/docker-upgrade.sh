#!/bin/bash
# Docker Compose 升级助手（支持 00 清理镜像）

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}    Docker Compose 升级助手${NC}"
echo -e "${BLUE}========================================${NC}"
echo

if ! command -v docker &>/dev/null || ! docker compose version &>/dev/null; then
    echo -e "${RED}错误：docker 或 docker compose 不可用${NC}"
    exit 1
fi

echo -e "${CYAN}正在扫描 Compose 项目...${NC}"
echo

declare -A SEEN_DIRS
declare -a PROJECT_NAMES
declare -a PROJECT_DIRS
declare -a PROJECT_STATUS
declare -a PROJECT_CONTAINERS

# 解析 docker compose ls -a
while IFS= read -r line; do
    [[ "$line" =~ ^NAME ]] && continue
    [[ -z "$line" ]] && continue

    name=$(echo "$line" | awk '{print $1}')
    status=$(echo "$line" | awk '{print $2}')
    config_files=$(echo "$line" | awk '{$1=$2=""; print $0}' | sed 's/^ *//')
    first_file=$(echo "$config_files" | cut -d',' -f1 | xargs)

    if [ -n "$first_file" ] && [ -f "$first_file" ]; then
        work_dir=$(dirname "$first_file")
    else
        continue
    fi

    if [ -d "$work_dir" ] && [ -z "${SEEN_DIRS[$work_dir]+x}" ]; then
        SEEN_DIRS[$work_dir]=1
        PROJECT_NAMES+=("$name")
        PROJECT_DIRS+=("$work_dir")
        PROJECT_STATUS+=("$status")

        containers=$(docker ps -a --filter "label=com.docker.compose.project=$name" --format '{{.Names}}' | tr '\n' ',' | sed 's/,$//')
        PROJECT_CONTAINERS+=("$containers")
    fi
done < <(docker compose ls -a 2>/dev/null || true)

# 补充标签扫描
while IFS=$'\t' read -r project work_dir; do
    [ -z "$project" ] && continue
    if [ -n "$work_dir" ] && [ -d "$work_dir" ] && [ -z "${SEEN_DIRS[$work_dir]+x}" ]; then
        SEEN_DIRS[$work_dir]=1
        PROJECT_NAMES+=("$project")
        PROJECT_DIRS+=("$work_dir")
        PROJECT_STATUS+=("from-label")
        containers=$(docker ps -a --filter "label=com.docker.compose.project=$project" --format '{{.Names}}' | tr '\n' ',' | sed 's/,$//')
        PROJECT_CONTAINERS+=("$containers")
    fi
done < <(docker ps -a --filter "label=com.docker.compose.project" \
    --format '{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null | sort -u)

# 显示列表
echo -e "${GREEN}可操作列表：${NC}"
echo "----------------------------------------"
echo -e "${CYAN}00.${NC} 清理未使用镜像"
echo "----------------------------------------"

if [ ${#PROJECT_NAMES[@]} -eq 0 ]; then
    echo -e "${YELLOW}未发现任何 Compose 项目${NC}"
else
    for i in "${!PROJECT_NAMES[@]}"; do
        num=$(printf "%02d" $((i+1)))
        status_color="$CYAN"
        [[ "${PROJECT_STATUS[$i]}" == *running* ]] && status_color="$GREEN"
        [[ "${PROJECT_STATUS[$i]}" == *exited* || "${PROJECT_STATUS[$i]}" == *stopped* ]] && status_color="$YELLOW"

        echo -e "${CYAN}${num}.${NC} ${PROJECT_NAMES[$i]}  ${status_color}[${PROJECT_STATUS[$i]}]${NC}"
        echo -e "     路径  : ${PROJECT_DIRS[$i]}"
        echo -e "     容器  : ${PROJECT_CONTAINERS[$i]}"
    done
fi
echo "----------------------------------------"
echo

# 选择
while true; do
    read -rp "请输入序号 (q 退出 / a 升级全部项目): " choice

    if [[ "$choice" == "q" || "$choice" == "Q" ]]; then
        echo "已退出"
        exit 0
    fi

    # 00 = 清理镜像
    if [[ "$choice" == "00" || "$choice" == "0" ]]; then
        echo
        echo -e "${BLUE}========================================${NC}"
        echo -e "${BLUE}    清理未使用的旧镜像${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo
        echo -e "${YELLOW}以下镜像将被删除（如果确认）：${NC}"
        echo "----------------------------------------"
        docker image prune -a --dry-run 2>/dev/null || true
        echo "----------------------------------------"
        echo
        read -rp "确认删除未使用的旧镜像？(y/N): " prune_confirm
        if [[ "$prune_confirm" =~ ^[Yy]$ ]]; then
            docker image prune -a -f
            echo -e "${GREEN}✓ 清理完成${NC}"
            echo
            docker system df
        else
            echo -e "${YELLOW}已取消清理${NC}"
        fi
        exit 0
    fi

    # 升级全部
    if [[ "$choice" == "a" || "$choice" == "A" ]]; then
        if [ ${#PROJECT_NAMES[@]} -eq 0 ]; then
            echo -e "${YELLOW}没有可升级的项目${NC}"
            exit 0
        fi
        read -rp "确认升级全部项目？请输入 yes: " confirm_all
        [[ "$confirm_all" != "yes" ]] && { echo "已取消"; exit 0; }

        for i in "${!PROJECT_NAMES[@]}"; do
            echo
            echo -e "${BLUE}>>> 升级项目: ${PROJECT_NAMES[$i]}${NC}"
            cd "${PROJECT_DIRS[$i]}"
            docker compose pull
            docker compose up -d
            echo -e "${GREEN}✓ ${PROJECT_NAMES[$i]} 完成${NC}"
        done

        # 全部升级后询问是否清理
        echo
        read -rp "是否清理未使用的旧镜像？(y/N): " prune_confirm
        if [[ "$prune_confirm" =~ ^[Yy]$ ]]; then
            docker image prune -a -f
            echo -e "${GREEN}✓ 清理完成${NC}"
            docker system df
        fi
        exit 0
    fi

    # 单个项目
    choice_num=$((10#$choice)) 2>/dev/null || true
    if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice_num" -ge 1 ] && [ "$choice_num" -le ${#PROJECT_NAMES[@]} ]; then
        idx=$((choice_num-1))
        selected_name="${PROJECT_NAMES[$idx]}"
        selected_dir="${PROJECT_DIRS[$idx]}"
        break
    else
        echo -e "${RED}无效序号，请重新输入${NC}"
    fi
done

# 执行单个升级
echo
echo -e "${YELLOW}准备升级: ${selected_name}${NC}"
echo -e "路径: ${selected_dir}"
read -rp "确认？(y/N): " confirm
[[ ! "$confirm" =~ ^[Yy]$ ]] && { echo "已取消"; exit 0; }

cd "$selected_dir"
echo -e "${CYAN}>>> docker compose pull${NC}"
docker compose pull
echo -e "${CYAN}>>> docker compose up -d${NC}"
docker compose up -d
echo -e "${GREEN}✓ ${selected_name} 升级完成${NC}"

# 升级后询问是否清理
echo
read -rp "是否清理未使用的旧镜像？(y/N): " prune_confirm
if [[ "$prune_confirm" =~ ^[Yy]$ ]]; then
    docker image prune -a -f
    echo -e "${GREEN}✓ 清理完成${NC}"
    docker system df
else
    echo -e "${YELLOW}已跳过清理${NC}"
fi

echo
echo -e "${GREEN}全部操作完成${NC}"
