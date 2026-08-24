#!/bin/bash
# NTRIP Caster 保护版本构建和部署脚本
# 用于构建源码保护的Docker镜像并推送到仓库

set -euo pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 配置变量
IMAGE_NAME="ntripcaster"
IMAGE_TAG="2.2.0"
REGISTRY_URL="2rtk"  # Docker Hub用户名/组织名
REGISTRY_NAMESPACE=""  # Docker Hub不需要额外命名空间
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 函数定义
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

log_success() {
    echo -e "${CYAN}[SUCCESS]${NC} $1"
}

# 显示横幅
show_banner() {
    echo -e "${CYAN}"
    cat << 'EOF'
    ███╗   ██╗████████╗██████╗ ██╗██████╗ 
    ████╗  ██║╚══██╔══╝██╔══██╗██║██╔══██╗
    ██╔██╗ ██║   ██║   ██████╔╝██║██████╔╝
    ██║╚██╗██║   ██║   ██╔══██╗██║██╔═══╝ 
    ██║ ╚████║   ██║   ██║  ██║██║██║     
    ╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝     
EOF
    echo -e "${NC}"
    echo -e "${GREEN}    NTRIP Caster 保护版本构建部署工具${NC}"
    echo -e "${BLUE}    版本: ${IMAGE_TAG}${NC}"
    echo
}

# 检查依赖
check_dependencies() {
    log_step "检查构建依赖..."
    
    local deps=("python3" "docker" "git")
    for dep in "${deps[@]}"; do
        if ! command -v "$dep" &> /dev/null; then
            log_error "缺少依赖: $dep"
            exit 1
        fi
    done
    
    # 检查Docker是否运行
    if ! docker info &> /dev/null; then
        log_error "Docker未运行或无权限访问"
        exit 1
    fi
    
    log_info "依赖检查通过"
}

# 构建保护版本的二进制文件
build_protected_binary() {
    log_step "构建源码保护的二进制文件..."
    
    cd "$SCRIPT_DIR"
    
    # 运行保护构建脚本
    if [ -f "build_protected.py" ]; then
        python3 build_protected.py
    else
        log_error "找不到build_protected.py脚本"
        exit 1
    fi
    
    # 检查构建结果
    if [ ! -d "dist_protected/ntrip-caster" ]; then
        log_error "二进制文件构建失败"
        exit 1
    fi
    
    log_success "二进制文件构建完成"
}

# 构建Docker镜像
build_docker_image() {
    log_step "构建Docker镜像..."
    
    cd "$SCRIPT_DIR"
    
    # 构建镜像
    local full_image_name="${IMAGE_NAME}:${IMAGE_TAG}"
    
    docker build \
        -f Dockerfile \
        -t "$full_image_name" \
        -t "${IMAGE_NAME}:latest" \
        --build-arg BUILD_DATE="$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
        --build-arg VCS_REF="$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')" \
        .
    
    log_success "Docker镜像构建完成: $full_image_name"
}

# 测试镜像
test_image() {
    log_step "测试Docker镜像..."
    
    local test_container="ntrip-test-$(date +%s)"
    local test_admin_password
    test_admin_password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
    
    # 启动测试容器
    docker run -d \
        --name "$test_container" \
        -p 127.0.0.1:12101:2101 \
        -p 127.0.0.1:15757:5757 \
        -e NTRIP_ADMIN_PASSWORD="$test_admin_password" \
        "${IMAGE_NAME}:${IMAGE_TAG}"
    unset test_admin_password
    
    # 等待容器启动
    sleep 10
    
    # 检查容器状态
    if docker ps | grep -q "$test_container"; then
        log_info "容器启动成功，进行健康检查..."
        
        # 等待健康检查
        local max_attempts=30
        local attempt=0
        
        while [ $attempt -lt $max_attempts ]; do
            if docker exec "$test_container" python3 /app/healthcheck.py &>/dev/null; then
                log_success "健康检查通过"
                break
            fi
            
            attempt=$((attempt + 1))
            sleep 2
        done
        
        if [ $attempt -eq $max_attempts ]; then
            log_warn "健康检查超时，但容器仍在运行"
        fi
    else
        log_error "容器启动失败"
        docker logs "$test_container"
        docker rm -f "$test_container" 2>/dev/null || true
        exit 1
    fi
    
    # 清理测试容器
    docker rm -f "$test_container" 2>/dev/null || true
    log_success "镜像测试完成"
}

# 推送到仓库
push_to_registry() {
    if [ -z "$REGISTRY_URL" ]; then
        log_warn "未设置仓库地址，跳过推送步骤"
        log_info "如需推送，请设置REGISTRY_URL和REGISTRY_NAMESPACE变量"
        return
    fi
    
    log_step "推送镜像到仓库..."
    
    local registry_image
    if [ -n "$REGISTRY_NAMESPACE" ]; then
        registry_image="${REGISTRY_URL}/${REGISTRY_NAMESPACE}/${IMAGE_NAME}"
    else
        registry_image="${REGISTRY_URL}/${IMAGE_NAME}"
    fi
    
    # 标记镜像
    docker tag "${IMAGE_NAME}:${IMAGE_TAG}" "${registry_image}:${IMAGE_TAG}"
    docker tag "${IMAGE_NAME}:latest" "${registry_image}:latest"
    
    # 推送镜像
    docker push "${registry_image}:${IMAGE_TAG}"
    docker push "${registry_image}:latest"
    
    log_success "镜像推送完成: ${registry_image}:${IMAGE_TAG}"
}

# 生成部署文档
generate_deployment_docs() {
    log_step "生成部署文档..."
    
    local docs_dir="deployment_docs"
    mkdir -p "$docs_dir"
    
    # 生成docker-compose.yml
    cat > "${docs_dir}/docker-compose.yml" << EOF
# NTRIP Caster 保护版本部署配置
# 使用方法: docker-compose up -d

version: '3.8'

services:
  ntrip-caster:
    image: ${REGISTRY_URL:+${REGISTRY_URL}/}${REGISTRY_NAMESPACE:+${REGISTRY_NAMESPACE}/}${IMAGE_NAME}:${IMAGE_TAG}
    container_name: ntrip-caster
    hostname: ntrip-caster
    restart: unless-stopped
    ports:
      - "\${NTRIP_PUBLISH_HOST:-0.0.0.0}:2101:2101"
      - "\${WEB_PUBLISH_HOST:-127.0.0.1}:5757:5757"
    volumes:
      - ntrip-data:/app/data          # 数据持久化
      - ntrip-logs:/app/logs          # 日志持久化
      - ntrip-config:/app/config      # 配置文件
      - /etc/localtime:/etc/localtime:ro  # 时区同步
    environment:
      - TZ=\${TZ:-Asia/Taipei}
      - NTRIP_CONFIG_FILE=/app/config/config.ini
      - NTRIP_LISTEN_HOST=0.0.0.0
      - WEB_LISTEN_HOST=0.0.0.0
      - NTRIP_ADMIN_USERNAME=\${NTRIP_ADMIN_USERNAME:-admin}
      - NTRIP_ADMIN_PASSWORD=\${NTRIP_ADMIN_PASSWORD:-}
      - MAP_PROVIDER=\${MAP_PROVIDER:-osm}
      - GOOGLE_MAPS_API_KEY=\${GOOGLE_MAPS_API_KEY:-}
    networks:
      - ntrip-network
    healthcheck:
      test: ["CMD", "python", "/app/healthcheck.py"]
      interval: 30s
      timeout: 15s
      retries: 3
      start_period: 90s
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "5"
        compress: "true"
    security_opt:
      - no-new-privileges:true
    ulimits:
      nofile:
        soft: 65536
        hard: 65536

volumes:
  ntrip-data:
    driver: local
  ntrip-logs:
    driver: local
  ntrip-config:
    driver: local

networks:
  ntrip-network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16
EOF

    cp .env.example "${docs_dir}/.env.example"
    cp scripts/deployment_config.py "${docs_dir}/deployment_config.py"

    # 生成部署脚本
    cat > "${docs_dir}/deploy.sh" << 'EOF'
#!/bin/bash
# NTRIP Caster 一键部署脚本

set -e

echo "开始部署NTRIP Caster..."

# 检查 Docker、Compose 与 Python
if ! command -v docker &> /dev/null; then
    echo "错误: 未安装Docker"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo "错误: 未安装 Docker Compose 插件"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo "错误: 未安装 Python 3，无法安全建立本机认证资料"
    exit 1
fi

python3 deployment_config.py prepare-env \
    --env-file .env \
    --example .env.example \
    --environment production \
    --profiles ""
chmod 600 .env

docker compose config --quiet

# 拉取最新镜像
echo "拉取最新镜像..."
docker compose pull

# 启动服务
echo "启动服务..."
docker compose up -d

# 等待服务启动
echo "等待服务启动..."
sleep 30

# 检查服务状态
echo "检查服务状态..."
docker compose ps

echo "部署完成!"
echo "NTRIP服务地址: ntrip://localhost:2101"
echo "Web管理界面: http://127.0.0.1:5757"
echo "管理员认证资料仅储存在本机 .env，不会显示"
echo ""
echo "常用命令:"
echo "  查看日志: docker compose logs -f"
echo "  停止服务: docker compose down"
echo "  重启服务: docker compose restart"
EOF

    chmod +x "${docs_dir}/deploy.sh"
    
    # 生成README
    cat > "${docs_dir}/README.md" << EOF
# NTRIP Caster 部署指南

## 快速部署

1. 确保已安装 Docker、Docker Compose 插件与 Python 3
2. 运行部署脚本:
   \`\`\`bash
   ./deploy.sh
   \`\`\`

## 手动部署

1. 拉取镜像:
   \`\`\`bash
   python3 deployment_config.py prepare-env --env-file .env --example .env.example --environment production --profiles ""
   docker compose pull
   \`\`\`

2. 启动服务:
   \`\`\`bash
   docker compose up -d
   \`\`\`

## 服务访问

- NTRIP 服务：TCP 2101，預設對外發布，必須設定防火牆
- Web 管理介面：\`http://127.0.0.1:5757\`
- 管理員認證資料：僅儲存在被 Git 忽略的 \`.env\`，不會輸出

## 配置说明

首次啟動會在 \`ntrip-config\` 卷內建立小寫 schema 的安全設定；不會複製公開範例。

## 数据持久化

- 数据目录: \`ntrip-data\` 卷
- 日志目录: \`ntrip-logs\` 卷  
- 配置目录: \`ntrip-config\` 卷

## 常用命令

\`\`\`bash
# 查看服务状态
docker compose ps

# 查看日志
docker compose logs -f

# 重启服务
docker compose restart

# 停止服务
docker compose down

# 更新服务
docker compose pull && docker compose up -d
\`\`\`

## 故障排除

1. 检查端口是否被占用
2. 检查Docker服务是否正常运行
3. 查看容器日志排查问题

EOF

    log_success "部署文档生成完成: $docs_dir/"
}

# 清理构建文件
cleanup_build_files() {
    log_step "清理构建文件..."
    
    # 可选择性清理，保留重要文件
    if [ -d "build_protected" ]; then
        rm -rf build_protected/work build_protected/obfuscated
    fi
    
    log_info "构建文件清理完成"
}

# 显示使用帮助
show_help() {
    cat << EOF
NTRIP Caster 保护版本构建部署工具

用法: $0 [选项]

选项:
  --registry-url URL        设置Docker仓库地址
  --registry-namespace NS   设置仓库命名空间
  --skip-test              跳过镜像测试
  --skip-push              跳过推送到仓库
  --cleanup                构建完成后清理临时文件
  --help, -h               显示此帮助信息

示例:
  $0 --registry-url registry.example.com --registry-namespace mycompany
  $0 --skip-test --skip-push

EOF
}

# 解析命令行参数
parse_args() {
    SKIP_TEST=false
    SKIP_PUSH=false
    CLEANUP=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --registry-url)
                REGISTRY_URL="$2"
                shift 2
                ;;
            --registry-namespace)
                REGISTRY_NAMESPACE="$2"
                shift 2
                ;;
            --skip-test)
                SKIP_TEST=true
                shift
                ;;
            --skip-push)
                SKIP_PUSH=true
                shift
                ;;
            --cleanup)
                CLEANUP=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "未知参数: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

# 主函数
main() {
    # 解析参数
    parse_args "$@"
    
    # 显示横幅
    show_banner
    
    # 显示配置信息
    log_info "构建配置:"
    echo "  镜像名称: ${IMAGE_NAME}:${IMAGE_TAG}"
    echo "  仓库地址: ${REGISTRY_URL:-'未设置'}"
    echo "  命名空间: ${REGISTRY_NAMESPACE:-'未设置'}"
    echo "  跳过测试: ${SKIP_TEST}"
    echo "  跳过推送: ${SKIP_PUSH}"
    echo
    
    try {
        # 1. 检查依赖
        check_dependencies
        
        # 2. 构建保护版本的二进制文件
        build_protected_binary
        
        # 3. 构建Docker镜像
        build_docker_image
        
        # 4. 测试镜像（可选）
        if [ "$SKIP_TEST" = false ]; then
            test_image
        fi
        
        # 5. 推送到仓库（可选）
        if [ "$SKIP_PUSH" = false ]; then
            push_to_registry
        fi
        
        # 6. 生成部署文档
        generate_deployment_docs
        
        # 7. 清理构建文件（可选）
        if [ "$CLEANUP" = true ]; then
            cleanup_build_files
        fi
        
        echo
        log_success "构建部署完成!"
        echo
        log_info "下一步:"
        echo "  1. 查看部署文档: deployment_docs/README.md"
        echo "  2. 使用部署脚本: cd deployment_docs && ./deploy.sh"
        if [ -n "$REGISTRY_URL" ]; then
            echo "  3. 分发镜像: ${REGISTRY_URL}/${REGISTRY_NAMESPACE:+${REGISTRY_NAMESPACE}/}${IMAGE_NAME}:${IMAGE_TAG}"
        fi
        echo
        
    } catch {
        log_error "构建失败: $1"
        exit 1
    }
}

# Bash错误处理函数
try() {
    "$@"
}

catch() {
    case $? in
        0) ;; # 成功，什么都不做
        *) "$@" ;; # 失败，执行catch块
    esac
}

# 脚本入口
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
