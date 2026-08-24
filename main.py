#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
import signal
import logging
import psutil
import threading
import argparse
import os
from pathlib import Path
from threading import Thread

# 解析命令行参数
parser = argparse.ArgumentParser(description='2RTK NTRIP Caster')
parser.add_argument('--config', type=str, help='設定檔路徑')
args = parser.parse_args()

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 如果指定了配置文件，设置环境变量
if args.config:
    os.environ['NTRIP_CONFIG_FILE'] = args.config

# 导入配置和核心模块
from src import config
from src import logger
from src import forwarder
from src.database import DatabaseManager
from src.web import create_web_manager
from src.ntrip import NTRIPCaster
from src.connection import get_connection_manager

def setup_logging():
    """设置日志系统"""
    # 初始化日志模块
    logger.init_logging()
    
    # 设置特定模块的日志级别
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('socketio').setLevel(logging.WARNING)
    logging.getLogger('engineio').setLevel(logging.WARNING)
    
    # 记录系统启动日志
    logger.log_system_event('日誌系統初始化完成')

def print_banner():
    """打印启动横幅"""
    banner = f"""

    ██████╗ ██████╗ ████████╗██╗  ██╗
    ╚════██╗██╔══██╗╚══██╔══╝██║ ██╔╝
     █████╔╝██████╗╔   ██║   █████╔╝ 
    ██╔═══╝ ██╔══██╗   ██║   ██║  ██╗
    ███████╗██║  ██║   ██║   ██║  ██╗
    ╚══════╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝
    2RTK Ntrip Caster {config.VERSION}

NTRIP 連接埠: {config.NTRIP_PORT:<8} Web 管理連接埠: {config.WEB_PORT:<8}
除錯模式: {str(config.DEBUG):<9} 最大連線數: {config.MAX_CONNECTIONS:<8}

    """
    print(banner)

def check_environment():
    """检查运行环境"""
    logger = logging.getLogger('main')
    
    # 检查Python版本
    if sys.version_info < (3, 7):
        logger.error("需要 Python 3.7 或更新版本")
        sys.exit(1)
    
    # 检查必要的目录
    required_dirs = [
        Path(config.DATABASE_PATH).parent,
        Path(config.LOG_DIR)
    ]
    
    for dir_path in required_dirs:
        if not dir_path.exists():
            logger.info(f"建立目錄：{dir_path}")
            dir_path.mkdir(parents=True, exist_ok=True)
    
    # 检查端口是否可用
    import socket
    
    def check_port(port, name):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
            return True
        except OSError:
            logger.error(f"{name} 連接埠 {port} 已被占用")
            return False
    
    ports_ok = True
    ports_ok &= check_port(config.NTRIP_PORT, "NTRIP")
    ports_ok &= check_port(config.WEB_PORT, "Web")
    
    if not ports_ok:
        logger.error("連接埠檢查失敗，請檢查占用狀況")
        sys.exit(1)
    
    logger.info("環境檢查通過")

class ServiceManager:
    """服务管理器 - 统一管理所有服务组件"""
    
    def __init__(self):
        self.db_manager = None
        self.web_manager = None
        self.ntrip_caster = None
        self.web_thread = None
        self.running = False
        self.stopping = False  # 添加停止标志位，防止重复调用
        self.start_time = None
        self.stats_thread = None
        self.stats_interval = 10  # 统计打印间隔（秒）
        self.last_network_stats = None
        self.print_stats = False  # 控制是否在控制台打印统计信息
        self.system_stats_cache = {}  # 缓存系统统计数据供Web API使用
        
    def start_all_services(self):
        """启动所有服务"""
        try:
            self.start_time = time.time()
            logger.log_system_event(f'啟動 2RTK NTRIP Caster v{config.VERSION}')
            
            # 1. 初始化数据库
            self.db_manager = DatabaseManager()
            self.db_manager.init_database()
            logger.log_system_event('資料庫初始化完成')
            
            # 2. 初始化并启动数据转发器
            forwarder.initialize()
            forwarder.start_forwarder()
            logger.log_system_event('資料轉送器初始化完成')
            
            # 3. RTCM解析现在集成在connection_manager中，无需单独启动
            logger.log_system_event('RTCM 解析器整合完成')
            
            # 4. 启动Web管理界面
            self._start_web_interface()
            
            # 5. 启动NTRIP服务器（在单独线程中）
            self.ntrip_caster = NTRIPCaster(self.db_manager)
            self.ntrip_thread = threading.Thread(target=self.ntrip_caster.start, daemon=True)
            self.ntrip_thread.start()
            time.sleep(1)  # 等待NTRIP服务器启动
            
            # 6. 注册信号处理
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
            
            self.running = True
            logger.log_system_event(f'所有服務已啟動 - NTRIP 連接埠：{config.NTRIP_PORT}，Web 連接埠：{config.WEB_PORT}')
            
            # 启动统计监控线程
            self._start_stats_monitor()
            
            # 主循环 - 保持服务运行
            self._main_loop()
            
        except Exception as e:
            logger.log_error(f"啟動服務失敗：{e}", exc_info=True)
            self.stop_all_services()
            raise
    
    def _start_web_interface(self):
        """启动Web管理界面"""
        from src.web import set_server_instance
        self.web_manager = create_web_manager(
            self.db_manager, 
            forwarder.get_forwarder(), 
            self.start_time
        )
        # 设置服务器实例供Web API使用
        set_server_instance(self)
        self.web_manager.start_rtcm_parsing()
        
        def run_web():
            self.web_manager.run(host=config.WEB_HOST, port=config.WEB_PORT, debug=False)
        
        self.web_thread = Thread(target=run_web, daemon=True)
        self.web_thread.start()
        
        # 显示所有可访问的Web管理界面地址
        web_urls = config.get_display_urls(config.WEB_PORT, "Web管理界面", config.WEB_HOST)
        if len(web_urls) == 1:
            logger.log_info(f'Web 管理介面已啟動，管理網址：{web_urls[0]}')
        else:
            logger.log_system_event('Web 管理介面已啟動，可透過下列網址存取：')
            for url in web_urls:
                logger.log_system_event(f'  - {url}')
    
    def _start_stats_monitor(self):
        """启动统计监控线程"""
        self.stats_thread = Thread(target=self._stats_monitor_worker, daemon=True)
        self.stats_thread.start()
 
    def _stats_monitor_worker(self):
        """统计监控工作线程"""
        while self.running:
            try:
                time.sleep(self.stats_interval)
                if self.running:
                    self._update_system_stats()
            except Exception as e:
                logger.log_error(f"統計監控發生例外：{e}", exc_info=True)
    
    def _update_system_stats(self):
        """更新系统统计信息到缓存"""
        try:
            # 获取系统性能数据
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            
            # 获取网络统计
            network_stats = psutil.net_io_counters()
            network_bandwidth = self._calculate_network_bandwidth(network_stats)
            
            # 获取NTRIP服务器统计
            ntrip_stats = self.ntrip_caster.get_performance_stats() if self.ntrip_caster else {}
            
            # 获取连接管理器统计
            conn_manager = get_connection_manager()
            conn_stats = conn_manager.get_statistics()
            
            # 计算运行时间
            uptime = time.time() - self.start_time if self.start_time else 0
            uptime_str = self._format_uptime(uptime)
            
            # 计算数据传输统计
            total_data_bytes = sum(mount['total_bytes'] for mount in conn_stats.get('mounts', []) if 'total_bytes' in mount)
            total_data_mb = total_data_bytes / (1024 * 1024)
            
            # 更新缓存
            self.system_stats_cache = {
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'uptime': uptime,  # 保存数字格式的运行时间
                'uptime_str': uptime_str,  # 保存格式化的运行时间字符串
                'cpu_percent': cpu_percent,
                'memory': memory,
                'network_bandwidth': network_bandwidth,
                'ntrip_stats': ntrip_stats,
                'conn_stats': conn_stats,
                'total_data_mb': total_data_mb
            }
            
        except Exception as e:
            logger.log_error(f"更新統計資訊失敗：{e}", exc_info=True)
    

    
    def get_system_stats(self):
        """获取系统统计数据供Web API使用"""
        try:
            stats = self.system_stats_cache.copy()
            if not stats:
                # 如果缓存为空，立即更新一次
                self._update_system_stats()
                stats = self.system_stats_cache.copy()
            
            # 格式化数据供前端使用
            if stats:
                memory_info = stats.get('memory')
                network_info = stats.get('network_bandwidth', {})
                
                return {
                    'timestamp': stats.get('timestamp'),
                    'uptime': stats.get('uptime', 0),  # 返回数字格式的运行时间
                    'cpu_percent': round(stats.get('cpu_percent', 0), 1),
                    'memory': {
                        'percent': round(getattr(memory_info, 'percent', 0), 1),
                        'used': getattr(memory_info, 'used', 0),
                        'total': getattr(memory_info, 'total', 0)
                    },
                    'network_bandwidth': {
                        'sent_rate': network_info.get('sent_rate', 0) if isinstance(network_info, dict) else 0,
                        'recv_rate': network_info.get('recv_rate', 0) if isinstance(network_info, dict) else 0
                    },
                    'connections': {
                        'active': stats.get('ntrip_stats', {}).get('active_connections', 0),
                        'total': stats.get('ntrip_stats', {}).get('total_connections', 0),
                        'rejected': stats.get('ntrip_stats', {}).get('rejected_connections', 0),
                        'max_concurrent': stats.get('ntrip_stats', {}).get('max_concurrent', 0)
                    },
                    'mounts': stats.get('conn_stats', {}).get('mounts', {}),
                    'users': stats.get('conn_stats', {}).get('users', {}),
                    'data_transfer': {
                        'total_bytes': stats.get('total_data_mb', 0) * 1024 * 1024
                    }
                }
            return {}
        except Exception as e:
            logger.log_error(f"取得系統統計資料失敗：{e}", exc_info=True)
            return {}
    
    def set_print_stats(self, enabled):
        """设置是否在控制台打印统计信息"""
        self.print_stats = enabled
        if enabled:
            logger.log_system_event('已啟用主控台統計資訊輸出')
        else:
            logger.log_system_event('已停用主控台統計資訊輸出')
    
    def _calculate_network_bandwidth(self, current_stats):
        """计算网络带宽"""
        if self.last_network_stats is None:
            self.last_network_stats = (current_stats, time.time())
            return "計算中..."
        
        last_stats, last_time = self.last_network_stats
        current_time = time.time()
        time_diff = current_time - last_time
        
        if time_diff <= 0:
            return "計算中..."
        
        bytes_sent_diff = current_stats.bytes_sent - last_stats.bytes_sent
        bytes_recv_diff = current_stats.bytes_recv - last_stats.bytes_recv
        
        upload_mbps = (bytes_sent_diff * 8) / (time_diff * 1024 * 1024)
        download_mbps = (bytes_recv_diff * 8) / (time_diff * 1024 * 1024)
        total_mbps = upload_mbps + download_mbps
        
        self.last_network_stats = (current_stats, current_time)
        
        return f"↑{upload_mbps:.2f} Mbps ↓{download_mbps:.2f} Mbps（合計：{total_mbps:.2f} Mbps）"
    
    def _format_uptime(self, seconds):
        """格式化运行时间"""
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        if days > 0:
            return f"{days}天 {hours}小時 {minutes}分鐘"
        elif hours > 0:
            return f"{hours}小時 {minutes}分鐘"
        else:
            return f"{minutes}分鐘 {secs}秒"

    def _main_loop(self):
        """主循环 - 监控服务状态"""
        while self.running:
            try:
                # 检查各服务状态
                if self.ntrip_caster and not self.ntrip_caster.running:
                    logger.log_error('NTRIP 伺服器意外停止')
                    break
                    
                if self.web_thread and not self.web_thread.is_alive():
                    logger.log_error('Web 服務意外停止')
                    break
                
                # 短暂休眠避免CPU占用过高
                time.sleep(1)
                
            except Exception as e:
                logger.log_error(f"主迴圈發生例外：{e}", exc_info=True)
                break
    
    def _signal_handler(self, signum, frame):
        """信号处理器"""
        if self.stopping:
            logger.log_system_event(f'收到訊號 {signum}，但服務正在關閉，忽略重複訊號')
            return
        logger.log_system_event(f'收到訊號 {signum}，開始關閉所有服務')
        self.stop_all_services()
    
    def stop_all_services(self):
        """停止所有服务"""
        if self.stopping:
            logger.log_system_event('服務正在關閉，略過重複呼叫')
            return
            
        self.stopping = True
        logger.log_system_event('正在關閉所有服務')
        
        try:
            self.running = False
            
            # 等待统计监控线程结束
            if self.stats_thread and self.stats_thread.is_alive():
                logger.log_system_event('正在停止統計監控執行緒')
                self.stats_thread.join(timeout=2)
            
            # 停止NTRIP服务器
            if self.ntrip_caster:
                try:
                    self.ntrip_caster.stop()
                except Exception as e:
                    logger.log_error(f'停止 NTRIP 伺服器時發生錯誤：{e}')
        
            # 停止数据转发器
            try:
                forwarder.stop_forwarder()
            except Exception as e:
                logger.log_error(f'停止資料轉送器時發生錯誤：{e}')
            
            # 停止Web管理器
            if self.web_manager:
                try:
                    self.web_manager.stop_rtcm_parsing()
                except Exception as e:
                    logger.log_error(f'停止 Web 管理器時發生錯誤：{e}')
            
            logger.log_system_event('所有服務已關閉')
            
        except Exception as e:
            logger.log_error(f'關閉服務時發生例外：{e}')
        finally:
            # 确保停止标志位被重置（虽然程序即将退出）
            self.stopping = False

# 全局服务器实例
server = None

def get_server_instance():
    """获取服务器实例"""
    return server

def main():
    """主函数"""
    global server
    try:
        # 设置日志
        setup_logging()
        main_logger = logger.get_logger('main')
        
        # 打印启动信息
        print_banner()
        
        # 检查环境
        check_environment()
        
        # 初始化配置
        config.init_config()
        logger.log_system_event('設定初始化完成')
        
        # 创建服务器实例并启动所有服务
        server = ServiceManager()
        globals()['server'] = server  # 设置全局变量
        server.start_all_services()
        
    except KeyboardInterrupt:
        logger.log_system_event('收到中斷訊號，正在關閉服務')
    except Exception as e:
        logger.log_error(f"啟動失敗：{e}", exc_info=True)
        sys.exit(1)
    finally:
        if server:
            server.stop_all_services()
        logger.log_system_event('程式已結束')
        logger.shutdown_logging()

if __name__ == '__main__':
    main()
