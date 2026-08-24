#!/bin/bash

# NTRIP Caster 快速启动脚本
# 用于快速部署和管理 NTRIP Caster 服务

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 项目配置
PROJECT_NAME="ntrip-caster"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.example"

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${PURPLE}[STEP]${NC} $1"
}

# 显示横幅
show_banner() {
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                    NTRIP Caster 快速启动                    ║"
    echo "║                     Docker 容器化部署                       ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

# 检查依赖
check_dependencies() {
    log_step "检查系统依赖..."
    
    # 检查 Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker 未安装，请先安装 Docker"
        echo "安装指南: https://docs.docker.com/get-docker/"
        exit 1
    fi
    
    # 检查 Docker Compose
    if ! docker compose version &> /dev/null && ! command -v docker-compose &> /dev/null; then
        log_error "Docker Compose 未安装，请先安装 Docker Compose"
        echo "安装指南: https://docs.docker.com/compose/install/"
        exit 1
    fi
    
    # 检查 Docker 服务状态
    if ! docker info &> /dev/null; then
        log_error "Docker 服务未运行，请启动 Docker 服务"
        exit 1
    fi

    if ! command -v python3 &> /dev/null; then
        log_error "找不到 Python 3，無法安全建立本機 Docker 認證資料"
        exit 1
    fi
    
    log_success "系统依赖检查完成"
}

# 初始化环境
init_environment() {
    log_step "初始化环境配置..."
    
    # 创建必要目录
    log_info "创建必要目录..."
    ./docker-deploy.sh create_directories
    
    log_success "环境初始化完成"
}

# 选择部署模式
select_deployment_mode() {
    echo
    log_step "选择部署模式:"
    echo "1) 开发模式 (development) - 核心服务与本机监控"
    echo "2) 生产模式 (production) - 仅核心服务"
    echo "3) 完整模式 (full) - 包含所有服务和监控"
    echo "4) 最小模式 (minimal) - 仅 NTRIP Caster 核心服务"
    echo
    
    while true; do
        read -p "请选择部署模式 [1-4]: " choice
        case $choice in
            1)
                ENVIRONMENT="development"
                COMPOSE_PROFILES="monitoring"
                break
                ;;
            2)
                ENVIRONMENT="production"
                COMPOSE_PROFILES=""
                break
                ;;
            3)
                ENVIRONMENT="production"
                COMPOSE_PROFILES="nginx,monitoring,cache"
                break
                ;;
            4)
                ENVIRONMENT="production"
                COMPOSE_PROFILES=""
                break
                ;;
            *)
                log_warning "无效选择，请输入 1-4"
                ;;
        esac
    done
    
    local credential_args=(
        prepare-env
        --env-file "$ENV_FILE"
        --example "$ENV_EXAMPLE"
        --environment "$ENVIRONMENT"
        --profiles "$COMPOSE_PROFILES"
    )
    if [[ "$COMPOSE_PROFILES" == *"monitoring"* ]]; then
        credential_args+=(--monitoring)
    fi
    python3 "$SCRIPT_DIR/scripts/deployment_config.py" "${credential_args[@]}"
    chmod 600 "$ENV_FILE"

    log_success "已選擇 $ENVIRONMENT 模式；本機認證資料只儲存在 .env"
}

# 构建和启动服务
deploy_services() {
    log_step "构建和启动服务..."
    
    # 构建自定义镜像
    log_info "构建应用镜像..."
    ENVIRONMENT="$ENVIRONMENT" ./docker-deploy.sh build
    
    # 启动服务
    log_info "启动服务..."
    ENVIRONMENT="$ENVIRONMENT" ./docker-deploy.sh start
    
    # 等待服务启动
    log_info "等待服务启动..."
    sleep 10
    
    # 健康检查
    log_info "执行健康检查..."
    ENVIRONMENT="$ENVIRONMENT" ./docker-deploy.sh health
    
    log_success "服务部署完成"
}

# 显示服务信息
show_service_info() {
    log_step "服务信息:"
    
    # 显示服务状态
    ENVIRONMENT="$ENVIRONMENT" ./docker-deploy.sh status
    
    echo
    log_step "服务端点:"
    
    # 获取本机IP
    LOCAL_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "localhost")
    
    echo "📡 NTRIP Caster 服务:"
    echo "   - NTRIP 端口: ntrip://$LOCAL_IP:2101"
    echo "   - Web 管理界面: http://127.0.0.1:5757"
    
    if [[ "$COMPOSE_PROFILES" == *"monitoring"* ]]; then
        echo
        echo "📊 监控服务:"
        echo "   - Prometheus: http://127.0.0.1:9090"
        echo "   - Grafana: http://127.0.0.1:3000 (認證資料位於本機 .env，不會顯示)"
    fi
    
    echo
    log_success "部署完成！请使用上述端点访问服务"
}

# 显示管理命令
show_management_commands() {
    echo
    log_step "常用管理命令:"
    echo "查看日志:     ./docker-deploy.sh logs"
    echo "查看状态:     ./docker-deploy.sh status"
    echo "重启服务:     ./docker-deploy.sh restart"
    echo "停止服务:     ./docker-deploy.sh stop"
    echo "清理资源:     ./docker-deploy.sh clean"
    echo "健康检查:     ./docker-deploy.sh health"
    echo "备份数据:     ./docker-deploy.sh backup"
    echo "更新服务:     ./docker-deploy.sh update"
    echo
    echo "使用 Makefile (推荐):"
    echo "make up          # 启动服务"
    echo "make down        # 停止服务"
    echo "make logs        # 查看日志"
    echo "make status      # 查看状态"
    echo "make health      # 健康检查"
    echo "make clean       # 清理资源"
}

# 主函数
main() {
    show_banner
    
    # 检查是否在正确目录
    if [[ ! -f "docker-compose.yml" ]]; then
        log_error "请在 NTRIP Caster 项目根目录下运行此脚本"
        exit 1
    fi
    
    # 检查依赖
    check_dependencies
    
    # 初始化环境
    init_environment
    
    # 选择部署模式
    select_deployment_mode
    
    # 确认部署
    echo
    read -p "确认开始部署? [y/N]: " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        log_info "部署已取消"
        exit 0
    fi
    
    # 部署服务
    deploy_services
    
    # 显示服务信息
    show_service_info
    
    # 显示管理命令
    show_management_commands
    
    echo
    log_success "🎉 NTRIP Caster 快速启动完成！"
}

# 脚本入口
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
