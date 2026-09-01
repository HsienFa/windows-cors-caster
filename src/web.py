#!/usr/bin/env python3
"""
web.py - Web管理模块
功能：提供前端接口，展示挂载点的实时信息，支持查看和查询挂载点解析数据
"""

import threading
import time
import json
import logging
import math
import psutil
import re
from datetime import datetime
from functools import wraps
from threading import Thread

from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, send_from_directory
# from flask_cors import CORS  # 已移除，不需要CORS功能
import os
from flask_socketio import SocketIO, emit, join_room
from werkzeug.serving import make_server

from .database import DatabaseManager
from . import config
from . import logger
from .logger import log_debug, log_info, log_warning, log_error, log_critical, log_web_request, log_system_event
from . import connection
from . import forwarder
from .rtcm2_manager import parser_manager as rtcm_manager

# 全局服务器实例引用
server_instance = None

ROVER_API_FIELDS = (
    'username',
    'connection_id',
    'mount_name',
    'ip_address',
    'user_agent',
    'connect_datetime',
    'latitude',
    'longitude',
    'gga_fix_quality',
    'satellites',
    'hdop',
    'altitude',
    'last_gga_time',
    'has_valid_position',
    'position_fresh',
    'gga_age_seconds',
)


def _valid_coordinate(value, minimum, maximum):
    try:
        coordinate = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(coordinate) or not minimum <= coordinate <= maximum:
        return None
    return coordinate


def _get_base_coordinates(mount_info):
    """Return trusted in-memory base coordinates without reading persistence."""
    if not isinstance(mount_info, dict):
        return None, None

    latitude = _valid_coordinate(mount_info.get('lat'), -90, 90)
    longitude = _valid_coordinate(mount_info.get('lon'), -180, 180)
    if latitude is not None and longitude is not None:
        return latitude, longitude

    if not mount_info.get('final_str_generated'):
        return None, None
    str_data = mount_info.get('str_data')
    if not isinstance(str_data, str):
        return None, None
    fields = str_data.split(';')
    if len(fields) < 11 or fields[0] != 'STR':
        return None, None
    latitude = _valid_coordinate(fields[9], -90, 90)
    longitude = _valid_coordinate(fields[10], -180, 180)
    return latitude, longitude


def _distance_km(latitude, longitude, base_latitude, base_longitude):
    """Calculate great-circle distance using the mean Earth radius."""
    coordinates = (
        _valid_coordinate(latitude, -90, 90),
        _valid_coordinate(longitude, -180, 180),
        _valid_coordinate(base_latitude, -90, 90),
        _valid_coordinate(base_longitude, -180, 180),
    )
    if any(value is None for value in coordinates):
        return None

    rover_latitude, rover_longitude, base_latitude, base_longitude = map(
        math.radians,
        coordinates,
    )
    latitude_delta = base_latitude - rover_latitude
    longitude_delta = base_longitude - rover_longitude
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(rover_latitude)
        * math.cos(base_latitude)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(haversine)))


def _public_online_user_summary(online_users):
    """Return counts only; never expose Rover connection identity or position."""
    if not isinstance(online_users, dict):
        return {'online_user_count': 0, 'connection_count': 0}
    return {
        'online_user_count': len(online_users),
        'connection_count': sum(
            len(connections)
            for connections in online_users.values()
            if isinstance(connections, (list, tuple))
        ),
    }


def _public_system_stats(stats):
    """Remove per-user identities from system statistics used by public UI."""
    if not isinstance(stats, dict):
        return {}
    public_stats = dict(stats)
    user_details = public_stats.pop('users', None)
    public_stats['user_count'] = (
        len(user_details)
        if isinstance(user_details, (list, tuple, dict))
        else 0
    )
    return public_stats

def set_server_instance(server):
    """设置服务器实例"""
    global server_instance
    server_instance = server

def get_server_instance():
    """获取服务器实例"""
    return server_instance

# 获取日志记录器
# web_logger = logger.get_logger('main')  # 已改用直接的log_函数

class WebManager:
    """Web管理器"""
    
    def __init__(self, db_manager, data_forwarder, start_time):
        self.db_manager = db_manager
        self.data_forwarder = data_forwarder
        self.start_time = start_time
        
        # 创建连接管理器实例
        global rtcm
        rtcm = connection.ConnectionManager()
        
        # 模板目录和静态文件目录
        self.template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')
        self.static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
        
        # 创建Flask应用
        self.app = Flask(__name__, static_folder=self.static_dir, static_url_path='/static')
        self.app.secret_key = config.FLASK_SECRET_KEY
        self.app.json.ensure_ascii = False
        
        # 配置CORS - 已移除，项目为同域部署，不需要CORS功能
        # CORS(self.app, origins="*" if config.DEBUG else config.WEBSOCKET_CONFIG['cors_allowed_origins'])
        
        # 创建SocketIO实例
        # 在Windows上明确使用threading模式，避免eventlet兼容性问题
        # 移除CORS配置，项目为同域部署不需要跨域支持
        self.socketio = SocketIO(
            self.app, 
            async_mode='threading',  # 明确指定threading模式
            # cors_allowed_origins="*" if config.DEBUG else config.WEBSOCKET_CONFIG['cors_allowed_origins'],  # 已移除CORS
            ping_timeout=config.WEBSOCKET_CONFIG['ping_timeout'],
            ping_interval=config.WEBSOCKET_CONFIG['ping_interval']
        )
        
        # 注册路由
        self._register_routes()
        self._register_socketio_events()
        
        # 实时数据推送线程
        self.push_thread = None
        self.push_running = False
        self._push_stop_event = threading.Event()
        self._server_lock = threading.Lock()
        self._http_server = None
        self._web_stop_requested = False
        
        # 设置logger的web实例引用，用于实时日志推送
        logger.set_web_instance(self)
    
    def _format_uptime_simple(self, uptime_seconds):
        """格式化运行时间（简单版本）"""
        try:
            days = int(uptime_seconds // 86400)
            hours = int((uptime_seconds % 86400) // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            
            if days > 0:
                return f"{days}天{hours}小時{minutes}分鐘"
            elif hours > 0:
                return f"{hours}小時{minutes}分鐘"
            else:
                return f"{minutes}分鐘"
        except:
            return "0分鐘"
    
    def _validate_alphanumeric(self, value, field_name):
        """验证输入是否只包含英文字母、数字、下划线和中横线"""
        if not value:
            return False, f"{field_name}不得為空白"
        
        # 允许英文字母、数字、下划线和中横线
        if not re.match(r'^[a-zA-Z0-9_-]+$', value):
            return False, f"{field_name}僅能包含英文字母、數字、底線與連字號"
        
        return True, ""
    
    def _load_template(self, template_name, **kwargs):
        """加载外部模板文件"""
        template_path = os.path.join(self.template_dir, template_name)
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                template_content = f.read()
            template_context = dict(kwargs)
            template_context.update({
                'app_name': config.APP_NAME,
                'app_version': config.APP_VERSION,
                'app_author': config.APP_AUTHOR,
                'app_contact': config.APP_CONTACT,
                'app_website': config.APP_WEBSITE,
                'current_year': datetime.now().year,
            })
            return render_template_string(template_content, **template_context)
        except FileNotFoundError:
            log_error(f"找不到範本檔案：{template_path}")
            return f"<h1>找不到範本檔案：{template_name}</h1>"
        except Exception as e:
            log_error(f"載入範本檔案失敗：{e}")
            return f"<h1>載入範本失敗：{str(e)}</h1>"
    
    def _register_routes(self):
        """注册Flask路由"""
        
        @self.app.route('/static/<path:filename>')
        def static_files(filename):
            """静态文件服务"""
            static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
            return send_from_directory(static_dir, filename)

        @self.app.route('/terms')
        def terms_of_use():
            """公開唯讀的使用條款初稿。"""
            return self._load_template('terms.html')

        @self.app.route('/privacy')
        def privacy_policy():
            """公開唯讀的隱私權政策初稿。"""
            return self._load_template('privacy.html')
        
        @self.app.route('/')
        def index():
            """主页 - SPA应用"""
            if (
                request.args.get('page') == 'monitor'
                and not session.get('admin_logged_in')
            ):
                return redirect('/login?redirect=monitor')
            map_config = config.get_public_map_config()
            
            return self._load_template('spa.html', 
                                     map_provider=map_config['provider'],
                                     google_maps_enabled=map_config['google_enabled'],
                                     map_default_latitude=map_config['default_latitude'],
                                     map_default_longitude=map_config['default_longitude'],
                                     map_default_zoom=map_config['default_zoom'],
                                     google_maps_script_url=config.get_google_maps_script_url())
        
        @self.app.route('/classic')
        @self.require_login
        def classic_index():
            """经典主页 - 系统状态和挂载点信息"""
            # 获取系统信息
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            uptime = time.time() - self.start_time
            
            # 获取运行中的挂载点
            running_mounts = self.db_manager.get_running_mounts()
            
            # 获取在线用户
            online_users = connection.get_connection_manager().get_online_users()
            
            # 获取RTCM解析数据
            parsed_data = connection.get_statistics().get('mounts', {})
            
            return self._load_template('index.html', 
                                        cpu_percent=cpu_percent,
                                        memory_percent=memory.percent,
                                        memory_used=memory.used // (1024*1024),
                                        memory_total=memory.total // (1024*1024),
                                        uptime=self._format_uptime(uptime),
                                        running_mounts=running_mounts,
                                        online_users=online_users,
                                        parsed_data=parsed_data)
        
        @self.app.route('/login', methods=['GET', 'POST'])
        def login():
            """登录页面"""
            if request.method == 'POST':
                # 表单验证
                username = request.form.get('username', '').strip()
                password = request.form.get('password', '').strip()
                
                # 防止空白提交
                if not username or not password:
                    return self._load_template('login.html', error="使用者名稱與密碼不得為空白")
                
                # 长度验证
                if len(username) < 2 or len(username) > 50:
                    return self._load_template('login.html', error="使用者名稱長度必須介於 2 至 50 個字元")
                
                if len(password) < 6 or len(password) > 100:
                    return self._load_template('login.html', error="密碼長度必須介於 6 至 100 個字元")
                
                # 验证用户名字符
                username_valid, username_error = self._validate_alphanumeric(username, "使用者名稱")
                if not username_valid:
                    return self._load_template('login.html', error=username_error)
                
                # 验证密码字符
                password_valid, password_error = self._validate_alphanumeric(password, "密碼")
                if not password_valid:
                    return self._load_template('login.html', error=password_error)
                
                if self.db_manager.verify_admin(username, password):
                    session['admin_logged_in'] = True
                    session['admin_username'] = username
                    
                    # 检查重定向参数
                    redirect_page = request.args.get('redirect')
                    if redirect_page and redirect_page in ['users', 'mounts', 'monitor', 'settings']:
                        return redirect(f'/?page={redirect_page}')
                    
                    return redirect(url_for('index'))
                else:
                    return self._load_template('login.html', error="使用者名稱或密碼錯誤")
            
            return self._load_template('login.html')
        
        @self.app.route('/logout', methods=['GET', 'POST'])
        def logout():
            """登出"""
            session.clear()
            if request.method == 'POST':
                return jsonify({'success': True})
            return redirect(url_for('login'))
        
        @self.app.route('/api/login', methods=['POST'])
        def api_login():
            """API登录"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({'error': '請求資料格式錯誤'}), 400
                
                username = data.get('username', '').strip()
                password = data.get('password', '').strip()
                
                # 防止空白提交
                if not username or not password:
                    return jsonify({'error': '使用者名稱與密碼不得為空白'}), 400
                
                # 长度验证
                if len(username) < 2 or len(username) > 50:
                    return jsonify({'error': '使用者名稱長度必須介於 2 至 50 個字元'}), 400
                
                if len(password) < 6 or len(password) > 100:
                    return jsonify({'error': '密碼長度必須介於 6 至 100 個字元'}), 400
                
                # 防止SQL注入的基本字符检查
                if any(char in username for char in ["'", '"', ';', '--', '/*', '*/', 'xp_']):
                    return jsonify({'error': '使用者名稱含有不允許的字元'}), 400
                
                if self.db_manager.verify_admin(username, password):
                    session['admin_logged_in'] = True
                    session['admin_username'] = username
                    return jsonify({
                        'success': True,
                        'message': '登入成功',
                        'token': 'session_based'  # 使用session而不是JWT
                    })
                else:
                    return jsonify({'error': '使用者名稱或密碼錯誤'}), 401
            except Exception as e:
                    log_error(f"API 登入失敗：{e}")
                    return jsonify({'error': '登入失敗'}), 500

        
        @self.app.route('/api/mount_info/<mount>')
        @self.require_login
        def mount_info(mount):
            """获取指定挂载点的解析信息并返回给前端"""
            parsed_data = rtcm_manager.get_parsed_mount_data(mount)
            statistics = rtcm_manager.get_mount_statistics(mount)
            
            if parsed_data:
                return jsonify({
                    'success': True,
                    'data': parsed_data,
                    'statistics': statistics
                })
            else:
                return jsonify({
                    'success': False,
                    'message': '掛載點資料不存在或尚未解析'
                })
        

        
        @self.app.route('/api/system/restart', methods=['POST'])
        @self.require_login
        def restart_system():
            """要求服務安全關機；由外部服務管理器決定是否重新啟動。"""
            try:
                service_manager = get_server_instance()
                if service_manager is None:
                    return jsonify({
                        'success': False,
                        'message': '伺服器執行個體無法使用'
                    }), 503

                log_info("管理員要求安全關閉程式")
                shutdown_thread = threading.Thread(
                    target=service_manager.stop_all_services,
                    name='Web-Graceful-Shutdown',
                    daemon=True,
                )
                shutdown_thread.start()
                
                return jsonify({
                    'success': True,
                    'message': '安全關機指令已送出；如需重新啟動，請由服務管理器執行'
                })
                
            except Exception as e:
                log_error(f"要求安全關機失敗：{e}")
                return jsonify({
                    'success': False,
                    'error': str(e)
                }), 500
        

        
        @self.app.route('/api/mount/<mount_name>/realtime')
        @self.require_login
        def api_get_mount_realtime(mount_name):
            """获取指定挂载点的实时解析数据"""
            try:
                realtime_data = rtcm_manager.get_parsed_mount_data(mount_name, limit=10)
                if realtime_data is None:
                    return jsonify({'error': '找不到掛載點'}), 404
                return jsonify(realtime_data)
            except Exception as e:
                    log_error(f"取得掛載點 {mount_name} 即時資料失敗：{e}")
                    return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/mount/initialize', methods=['POST'])
        @self.require_login
        def api_initialize_mount():
            """初始化挂载点"""
            try:
                data = request.get_json()
                mount_name = data.get('mount_name')
                if not mount_name:
                    return jsonify({'error': '必須提供掛載點名稱'}), 400
                
                connection.get_connection_manager().add_mount_connection(mount_name, '127.0.0.1', '網頁介面')
                log_system_event(f"掛載點 {mount_name} 初始化成功")
                return jsonify({'success': True, 'message': f'掛載點 {mount_name} 已初始化'})
            except Exception as e:
                log_error(f"初始化掛載點失敗：{e}")
                return jsonify({'error': str(e)}), 500
        

        

        
        @self.app.route('/api/bypass/stop-all', methods=['POST'])
        @self.require_login
        def api_stop_all_bypass_parsing():
            """停止所有挂载点的旁路解析"""
            try:
                rtcm_manager.stop_realtime_parsing()
                log_system_event("已停止所有掛載點的旁路解析")
                return jsonify({'success': True, 'message': '已停止所有旁路解析'})
            except Exception as e:
                log_error(f"停止所有旁路解析失敗：{e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/mount/<mount_name>/simulate', methods=['POST'])
        @self.require_login
        def api_simulate_mount_data(mount_name):
            """为挂载点模拟数据"""
            try:
                # 模拟数据功能暂时不可用
                log_system_event(f"掛載點 {mount_name} 資料模擬請求（功能目前無法使用）")
                log_system_event(f"掛載點 {mount_name} 資料模擬啟動成功")
                return jsonify({'success': True, 'message': f'已啟動掛載點 {mount_name} 的資料模擬'})
            except Exception as e:
                log_error(f"模擬掛載點資料失敗：{e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/mount/<mount_name>/rtcm-parse/start', methods=['POST'])
        @self.require_login
        def api_start_rtcm_parsing(mount_name):
            """启动指定挂载点的实时RTCM解析"""
            try:
                # print(f"[后端API] 收到启动RTCM解析请求 - 挂载点: {mount_name}")
                
                # 注意：不再手动调用stop_realtime_parsing()，
                # 因为新的start_realtime_parsing方法已经内置了智能清理逻辑
                # print(f"[后端API] 准备启动解析任务，内置清理逻辑将自动处理前一个解析线程")
                
                # 定义推送回调：接收rtcm.py解析的数据并推送到前端
                def push_callback(parsed_data):
                    mount_name = parsed_data.get("mount_name", "N/A")
                    data_type = parsed_data.get("data_type", "N/A")
                    timestamp = parsed_data.get("timestamp", "N/A")
                    data_keys = list(parsed_data.keys()) if isinstance(parsed_data, dict) else "N/A"
                    
                    # print(f"\n[后端推送] 准备推送数据到前端:")
        # print(f"   挂载点: {mount_name}")
        # print(f"   数据类型: {data_type}")
        # print(f"   时间戳: {timestamp}")
        # print(f"   数据键: {data_keys}")
                    
                    # 详细打印不同类型的数据
                    if data_type == 'msm_satellite':
                        # MSM卫星数据调试信息已注释，避免刷屏
                        # print(f"   MSM卫星数据详情:")
                        # print(f"      GNSS类型: {parsed_data.get('gnss', 'N/A')}")
                        # print(f"      消息类型: {parsed_data.get('msg_type', 'N/A')}")
                        # print(f"      MSM等级: {parsed_data.get('msm_level', 'N/A')}")
                        # print(f"      卫星数量: {parsed_data.get('total_sats', 'N/A')}")
                        # if 'sats' in parsed_data and isinstance(parsed_data['sats'], list):
                        #     print(f"      前3个卫星数据:")
                        #     for i, sat in enumerate(parsed_data['sats'][:3]):
                        #         print(f"        卫星{i+1}: PRN={sat.get('id', 'N/A')}, SNR={sat.get('snr', 'N/A')}, 信号={sat.get('signal_type', 'N/A')}")
                        #     if len(parsed_data['sats']) > 3:
                        #         print(f"        ... 还有 {len(parsed_data['sats']) - 3} 个卫星")
                        pass
                    elif data_type == 'geography':
                        # print(f"   地理位置数据详情:")
                        # print(f"      基准站ID: {parsed_data.get('station_id', 'N/A')}")
                        # print(f"      纬度: {parsed_data.get('lat', 'N/A')}")
                        # print(f"      经度: {parsed_data.get('lon', 'N/A')}")
                        # print(f"      高度: {parsed_data.get('height', 'N/A')}")
                        # print(f"      国家: {parsed_data.get('country', 'N/A')}")
                        # print(f"      城市: {parsed_data.get('city', 'N/A')}")
                        pass
                    elif data_type == 'device_info':
                        # print(f"   设备信息数据详情:")
                        # print(f"      接收机: {parsed_data.get('receiver', 'N/A')}")
                        # print(f"      固件版本: {parsed_data.get('firmware', 'N/A')}")
                        # print(f"      天线: {parsed_data.get('antenna', 'N/A')}")
                        # print(f"      天线固件: {parsed_data.get('antenna_firmware', 'N/A')}")
                        pass
                    elif data_type == 'message_stats':
                        # print(f"   消息统计数据详情:")
                        # print(f"      消息类型: {parsed_data.get('message_types', 'N/A')}")
                        # print(f"      GNSS系统: {parsed_data.get('gnss', 'N/A')}")
                        # print(f"      载波频段: {parsed_data.get('carriers', 'N/A')}")
                        pass
                    
                    # 打印完整数据（截断显示）- 对MSM数据不打印以避免刷屏
                    if data_type != 'msm_satellite':
                        data_str = str(parsed_data)
                        # print(f"   完整数据: {data_str[:500]}{'...' if len(data_str) > 500 else ''}")
                    
                    # 确保数据包含mount_name
                    if 'mount_name' not in parsed_data:
                        # print(f"[后端推送] 推送数据缺少mount_name字段")
                        log_warning("推送資料缺少 mount_name 欄位")
                        return
                        
                    # 通过SocketIO推送给前端，事件名为'rtcm_realtime_data'
                    if data_type != 'msm_satellite':
                        # print(f"[后端推送] 通过SocketIO推送数据到前端 - 事件: rtcm_realtime_data")
                        pass
                    self.socketio.emit(
                        'rtcm_realtime_data',
                        parsed_data
                    )
                    if data_type != 'msm_satellite':
                        # print(f"[后端推送] 数据推送完成\n")
                        pass
                
                # 启动新的解析任务，传入回调
                # print(f"[后端API] 启动新的解析任务 - 挂载点: {mount_name}")
                success = rtcm_manager.start_realtime_parsing(
                    mount_name=mount_name,
                    push_callback=push_callback  # 替换原有的self.socketio参数
                )
                if success:
                    # print(f" [后端API] 解析启动成功 - 挂载点: {mount_name}")
                    log_system_event(f"掛載點 {mount_name} 的即時 RTCM 解析已啟動")
                    return jsonify({'success': True, 'message': f'已啟動掛載點 {mount_name} 的即時 RTCM 解析'})
                else:
                    # print(f"[后端API] 解析启动失败 - 挂载点: {mount_name} (可能离线)")
                    return jsonify({'error': '無法啟動解析，掛載點可能已離線'}), 400
            except Exception as e:
                # print(f"[后端API] 启动RTCM解析异常: {e}")
                log_error(f"啟動即時 RTCM 解析失敗：{e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/mount/rtcm-parse/stop', methods=['POST'])
        @self.require_login
        def api_stop_rtcm_parsing():
            """停止所有实时RTCM解析"""
            try:
                rtcm_manager.stop_realtime_parsing()
                log_system_event("已停止所有即時 RTCM 解析")
                return jsonify({'success': True, 'message': '已停止即時 RTCM 解析'})
            except Exception as e:
                log_error(f"停止即時 RTCM 解析失敗：{e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/mount/rtcm-parse/status', methods=['GET'])
        @self.require_login
        def api_get_rtcm_parsing_status():
            """获取RTCM解析器状态信息"""
            try:
                status = rtcm_manager.get_parser_status()
                return jsonify({
                    'success': True, 
                    'status': status,
                    'message': '已取得解析器狀態'
                })
            except Exception as e:
                log_error(f"取得解析器狀態失敗：{e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/mount/rtcm-parse/heartbeat', methods=['POST'])
        @self.require_login
        def api_rtcm_parsing_heartbeat():
            """实时RTCM解析心跳维持"""
            try:
                data = request.get_json()
                mount_name = data.get('mount_name') if data else None
                
                if mount_name:
                    # 更新心跳时间戳
                    rtcm_manager.update_parsing_heartbeat(mount_name)
                    return jsonify({'success': True, 'message': '心跳時間已更新'})
                else:
                    return jsonify({'error': '必須提供掛載點名稱'}), 400
            except Exception as e:
                log_error(f"更新解析心跳失敗：{e}")
                return jsonify({'error': str(e)}), 500
        


        
        @self.app.route('/alipay_qr')
        def alipay_qr():
            """支付宝二维码"""
            return redirect(config.ALIPAY_QR_URL)
        
        @self.app.route('/wechat_qr')
        def wechat_qr():
            """微信二维码"""
            return redirect(config.WECHAT_QR_URL)
        

        @self.app.route('/api/app_info')
        def api_app_info():
            """获取应用信息"""
            try:
                return jsonify({
                    'name': config.APP_NAME,
                    'version': config.APP_VERSION,
                    'description': config.APP_DESCRIPTION,
                    'author': config.APP_AUTHOR,
                    'contact': config.APP_CONTACT,
                    'website': config.APP_WEBSITE
                })
            except Exception as e:
                log_error(f"取得應用程式資訊失敗：{e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/users', methods=['GET', 'POST'])
        @self.require_login
        def api_users():
            """用户管理API"""
            if request.method == 'GET':
                # 获取用户列表
                try:
                    users = self.db_manager.get_all_users()
                    
                    # 获取在线用户信息
                    try:
                        online_users = connection.get_connection_manager().get_online_users()
                        online_usernames = list(online_users.keys())
                    except Exception as e:
                        log_error(f"取得線上使用者失敗：{e}")
                        online_usernames = []
                    
                    # 将tuple转换为字典格式并添加在线状态和连接数
                    user_list = []
                    for user in users:
                        username = user[1]
                        connection_count = connection.get_connection_manager().get_user_connection_count(username)
                        connect_time = connection.get_connection_manager().get_user_connect_time(username)
                        user_dict = {
                            'id': user[0],
                            'username': username,
                            'online': username in online_usernames,
                            'connection_count': connection_count,
                            'connect_time': connect_time or '-'  # 接入时间
                        }
                        user_list.append(user_dict)
                    
                    return jsonify(user_list)
                except Exception as e:
                    log_error(f"取得使用者清單失敗：{e}")
                    return jsonify({'error': str(e)}), 500
            
            elif request.method == 'POST':
                # 添加用户
                try:
                    data = request.get_json()
                    if not data:
                        return jsonify({'error': '請求資料格式錯誤'}), 400
                    
                    username = data.get('username', '').strip()
                    password = data.get('password', '').strip()
                    
                    # 表单验证
                    if not username or not password:
                        return jsonify({'error': '使用者名稱與密碼不得為空白'}), 400
                    
                    # 验证用户名字符
                    username_valid, username_error = self._validate_alphanumeric(username, "使用者名稱")
                    if not username_valid:
                        return jsonify({'error': username_error}), 400
                    
                    # 验证密码字符
                    password_valid, password_error = self._validate_alphanumeric(password, "密碼")
                    if not password_valid:
                        return jsonify({'error': password_error}), 400
                    
                    elif len(username) < 2 or len(username) > 50:
                        return jsonify({'error': '使用者名稱長度必須介於 2 至 50 個字元'}), 400
                    elif len(password) < 6 or len(password) > 100:
                        return jsonify({'error': '密碼長度必須介於 6 至 100 個字元'}), 400
                    
                    # 检查用户是否已存在
                    existing_users = [u[1] for u in self.db_manager.get_all_users()]
                    if username in existing_users:
                        return jsonify({'error': '使用者名稱已存在'}), 400
                    
                    success, message = self.db_manager.add_user(username, password)
                    if success:
                        return jsonify({'message': message}), 201
                    else:
                        return jsonify({'error': message}), 400
                    
                except Exception as e:
                    log_error(f"新增使用者失敗：{e}")
                    return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/users/<username>', methods=['PUT', 'DELETE'])
        @self.require_login
        def api_user_detail(username):
            """用户详情管理API"""
            if request.method == 'PUT':
                # 更新用户信息（密码或用户名）
                try:
                    data = request.get_json()
                    if not data:
                        return jsonify({'error': '請求資料格式錯誤'}), 400
                    
                    new_password = data.get('password', '').strip()
                    new_username = data.get('username', '').strip()
                    
                    # 检查是否是管理员账户
                    if username == config.DEFAULT_ADMIN['username']:
                        # 管理员只能修改密码，不能修改用户名
                        if new_username:
                            return jsonify({'error': '管理員的使用者名稱無法修改'}), 400
                        
                        if not new_password:
                            return jsonify({'error': '新密碼不得為空白'}), 400
                        
                        # 验证密码字符
                        password_valid, password_error = self._validate_alphanumeric(new_password, "新密碼")
                        if not password_valid:
                            return jsonify({'error': password_error}), 400
                        
                        elif len(new_password) < 6 or len(new_password) > 100:
                            return jsonify({'error': '新密碼長度必須介於 6 至 100 個字元'}), 400
                        
                        # 管理员密码更新
                        success = self.db_manager.update_admin_password(username, new_password)
                        if success:
                            return jsonify({'message': f'管理員 {username} 的密碼更新成功'})
                        else:
                            return jsonify({'error': '管理員密碼更新失敗'}), 500
                    else:
                        # 普通用户可以修改密码和用户名
                        if new_username:
                            # 修改用户名
                            # 验证用户名字符
                            username_valid, username_error = self._validate_alphanumeric(new_username, "使用者名稱")
                            if not username_valid:
                                return jsonify({'error': username_error}), 400
                            
                            if len(new_username) < 2 or len(new_username) > 50:
                                return jsonify({'error': '使用者名稱長度必須介於 2 至 50 個字元'}), 400
                            
                            # 检查新用户名是否已存在
                            existing_users = [u[1] for u in self.db_manager.get_all_users()]
                            if new_username in existing_users and new_username != username:
                                return jsonify({'error': '使用者名稱已存在'}), 400
                            
                            # 强制下线用户
                            forwarder.force_disconnect_user(username)
                            
                            # 获取用户ID和当前密码
                            users = self.db_manager.get_all_users()
                            user_id = None
                            current_password = None
                            for user in users:
                                if user[1] == username:
                                    user_id = user[0]
                                    current_password = user[2]  # 获取当前密码哈希
                                    break
                            
                            if user_id is None:
                                return jsonify({'error': '使用者不存在'}), 400
                            
                            # 更新用户名（保持原密码）
                            success, message = self.db_manager.update_user(user_id, new_username, current_password)
                            if success:
                                return jsonify({'message': f'使用者名稱已從 {username} 更新為 {new_username}'})
                            else:
                                return jsonify({'error': message}), 400
                        
                        elif new_password:
                            # 修改密码
                            if len(new_password) < 6 or len(new_password) > 100:
                                return jsonify({'error': '新密碼長度必須介於 6 至 100 個字元'}), 400
                            
                            # 强制下线用户
                            forwarder.force_disconnect_user(username)
                            success, message = self.db_manager.update_user_password(username, new_password)
                            if success:
                                return jsonify({'message': f'使用者 {username} 的密碼更新成功'})
                            else:
                                return jsonify({'error': message}), 400
                        else:
                            return jsonify({'error': '請提供要更新的密碼或使用者名稱'}), 400
                    
                except Exception as e:
                    log_error(f"更新使用者失敗：{e}")
                    return jsonify({'error': str(e)}), 500
            
            elif request.method == 'DELETE':
                # 删除用户
                try:
                    # 强制下线用户
                    forwarder.force_disconnect_user(username)
                    success, result = self.db_manager.delete_user(username)
                    if success:
                        return jsonify({'message': f'使用者 {result} 刪除成功'})
                    else:
                        return jsonify({'error': result}), 400
                    
                except Exception as e:
                    log_error(f"刪除使用者失敗：{e}")
                    return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/mounts', methods=['GET', 'POST'])
        @self.require_login
        def api_mounts():
            """挂载点管理API"""
            if request.method == 'GET':
                # 获取挂载点列表
                try:
                    mounts = self.db_manager.get_all_mounts()
                    online_mounts = connection.get_connection_manager().get_online_mounts()
                    
                    # 将tuple转换为字典格式并添加运行状态和连接信息
                    mount_list = []
                    for mount in mounts:
                        mount_name = mount[1]
                        is_online = mount_name in online_mounts
                        # 获取实际的数据率
                        data_rate_str = '0 B/s'
                        if is_online:
                            mount_info = connection.get_connection_manager().get_mount_info(mount_name)
                            if mount_info and 'data_rate' in mount_info:
                                data_rate_bps = mount_info['data_rate']
                                if data_rate_bps >= 1024:
                                    data_rate_str = f'{data_rate_bps/1024:.2f} KB/s'
                                else:
                                    data_rate_str = f'{data_rate_bps:.2f} B/s'
                        
                        mount_dict = {
                            'id': mount[0],
                            'mount': mount_name,
                            'password': mount[2],
                            'username': mount[4] if len(mount) > 4 else None,  # 用户名
                            'lat': mount[5] if len(mount) > 5 and mount[5] is not None else 0,
                            'lon': mount[6] if len(mount) > 6 and mount[6] is not None else 0,
                            'active': is_online,
                            'connections': connection.get_connection_manager().get_mount_connection_count(mount_name) if is_online else 0,
                            'data_rate': data_rate_str
                        }
                        mount_list.append(mount_dict)
                    
                    return jsonify(mount_list)
                except Exception as e:
                    log_error(f"取得掛載點清單失敗：{e}")
                    return jsonify({'error': str(e)}), 500
            
            elif request.method == 'POST':
                # 添加挂载点
                try:
                    data = request.get_json()
                    if not data:
                        return jsonify({'error': '請求資料格式錯誤'}), 400
                    
                    mount = data.get('mount', '').strip()
                    password = data.get('password', '').strip()
                    user_id = data.get('user_id')  # 可选的用户ID参数
                    
                    # 表单验证
                    if not mount or not password:
                        return jsonify({'error': '掛載點名稱與密碼不得為空白'}), 400
                    
                    # 验证挂载点名称字符
                    mount_valid, mount_error = self._validate_alphanumeric(mount, "掛載點名稱")
                    if not mount_valid:
                        return jsonify({'error': mount_error}), 400
                    
                    # 验证密码字符
                    password_valid, password_error = self._validate_alphanumeric(password, "密碼")
                    if not password_valid:
                        return jsonify({'error': password_error}), 400
                    
                    elif len(mount) < 2 or len(mount) > 50:
                        return jsonify({'error': '掛載點名稱長度必須介於 2 至 50 個字元'}), 400
                    elif len(password) < 6 or len(password) > 100:
                        return jsonify({'error': '密碼長度必須介於 6 至 100 個字元'}), 400
                    
                    # 如果指定了user_id，验证用户是否存在
                    if user_id is not None:
                        try:
                            user_id = int(user_id)
                            users = self.db_manager.get_all_users()
                            user_ids = [u[0] for u in users]  # u[0] 是用户ID
                            if user_id not in user_ids:
                                return jsonify({'error': '指定的使用者不存在'}), 400
                        except (ValueError, TypeError):
                            return jsonify({'error': '使用者 ID 格式錯誤'}), 400
                    
                    # 检查挂载点是否已存在
                    existing_mounts = [m[1] for m in self.db_manager.get_all_mounts()]
                    if mount in existing_mounts:
                        return jsonify({'error': '掛載點已存在'}), 400
                    
                    success, message = self.db_manager.add_mount(mount, password, user_id)
                    if success:
                        return jsonify({'message': message}), 201
                    else:
                        return jsonify({'error': message}), 400
                    
                except Exception as e:
                    log_error(f"新增掛載點失敗：{e}")
                    return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/mounts/<mount_name>', methods=['PUT', 'DELETE'])
        @self.require_login
        def api_mount_detail(mount_name):
            """挂载点详情管理API"""
            if request.method == 'PUT':
                # 更新挂载点
                try:
                    data = request.get_json()
                    if not data:
                        return jsonify({'error': '請求資料格式錯誤'}), 400
                    
                    new_password = data.get('password', '').strip()
                    new_mount_name = data.get('mount_name', '').strip()
                    new_user_id = data.get('user_id')
                    username = data.get('username')
                    
                    # 验证新挂载点名称
                    if new_mount_name:
                        # 验证挂载点名称字符
                        mount_valid, mount_error = self._validate_alphanumeric(new_mount_name, "掛載點名稱")
                        if not mount_valid:
                            return jsonify({'error': mount_error}), 400
                        
                        if len(new_mount_name) < 2 or len(new_mount_name) > 50:
                            return jsonify({'error': '掛載點名稱長度必須介於 2 至 50 個字元'}), 400
                        
                        # 检查新挂载点名称是否已存在
                        existing_mounts = [m[1] for m in self.db_manager.get_all_mounts()]
                        if new_mount_name in existing_mounts and new_mount_name != mount_name:
                            return jsonify({'error': '掛載點名稱已存在'}), 400
                    
                    # 处理用户绑定（支持用户名和用户ID两种方式）
                    if username is not None:
                        if username == "" or (isinstance(username, str) and username.lower() == "null"):
                            new_user_id = None  # 空字符串或"null"表示解除绑定
                        else:
                            # 验证用户名字符
                            username_valid, username_error = self._validate_alphanumeric(username, "使用者名稱")
                            if not username_valid:
                                return jsonify({'error': username_error}), 400
                            
                            # 通过用户名查找用户ID
                            users = self.db_manager.get_all_users()
                            user_found = False
                            for user in users:
                                if user[1] == username:  # user[1] 是用户名
                                    new_user_id = user[0]  # user[0] 是用户ID
                                    user_found = True
                                    break
                            if not user_found:
                                return jsonify({'error': f'使用者「{username}」不存在'}), 400
                    elif new_user_id is not None:
                        # 兼容原有的用户ID方式
                        if new_user_id == "" or (isinstance(new_user_id, str) and new_user_id.lower() == "null"):
                            new_user_id = None  # 空字符串或"null"转换为None
                        elif new_user_id is not None:
                            try:
                                new_user_id = int(new_user_id)
                                # 检查用户是否存在
                                users = self.db_manager.get_all_users()
                                user_exists = any(user[0] == new_user_id for user in users)
                                if not user_exists:
                                    return jsonify({'error': '指定的使用者不存在'}), 400
                            except (ValueError, TypeError):
                                return jsonify({'error': '使用者 ID 格式錯誤'}), 400
                    
                    if new_password:
                        # 验证密码字符
                        password_valid, password_error = self._validate_alphanumeric(new_password, "密碼")
                        if not password_valid:
                            return jsonify({'error': password_error}), 400
                        
                        if len(new_password) < 6 or len(new_password) > 100:
                            return jsonify({'error': '新密碼長度必須介於 6 至 100 個字元'}), 400
                    
                    # 强制下线挂载点
                    forwarder.force_disconnect_mount(mount_name)
                    
                    # 获取挂载点ID
                    mounts = self.db_manager.get_all_mounts()
                    mount_id = None
                    for mount in mounts:
                        if mount[1] == mount_name:  # mount[1] 是挂载点名称
                            mount_id = mount[0]  # mount[0] 是ID
                            break
                    
                    if mount_id is None:
                        return jsonify({'error': '掛載點不存在'}), 400
                    
                    # 使用update_mount函数更新挂载点信息
                    success, result = self.db_manager.update_mount(
                        mount_id, 
                        new_mount_name if new_mount_name else None,
                        new_password if new_password else None,
                        new_user_id
                    )
                    if success:
                        # 构建返回消息
                        messages = []
                        if new_mount_name:
                            messages.append(f'掛載點名稱已從 {mount_name} 更新為 {new_mount_name}')
                        if new_password:
                            messages.append('掛載點密碼已更新')
                        if 'username' in data or new_user_id is not None:
                            if new_user_id is None:
                                messages.append('已清除掛載點所屬使用者')
                            else:
                                if username and username != "":
                                    messages.append(f'掛載點所屬使用者已更新為 {username}')
                                else:
                                    messages.append(f'掛載點所屬使用者已更新為使用者 ID {new_user_id}')
                        
                        if not messages:
                            messages.append('掛載點資訊更新成功')
                        
                        return jsonify({'message': '; '.join(messages)})
                    else:
                        return jsonify({'error': result}), 400
                    
                except Exception as e:
                    log_error(f"更新掛載點失敗：{e}")
                    return jsonify({'error': str(e)}), 500
            
            elif request.method == 'DELETE':
                # 删除挂载点
                try:
                    # 获取挂载点ID
                    mounts = self.db_manager.get_all_mounts()
                    mount_id = None
                    for mount in mounts:
                        if mount[1] == mount_name:  # mount[1] 是挂载点名称
                            mount_id = mount[0]  # mount[0] 是ID
                            break
                    
                    if mount_id is None:
                        return jsonify({'error': '掛載點不存在'}), 400
                    
                    # 强制下线挂载点
                    forwarder.force_disconnect_mount(mount_name)
                    success, result = self.db_manager.delete_mount(mount_name)
                    if success:
                        # 清理挂载点连接数据
                        connection.get_connection_manager().remove_mount_connection(mount_name)
                        return jsonify({'message': f'掛載點 {result} 刪除成功'})
                    else:
                        return jsonify({'error': result}), 400
                    
                except Exception as e:
                    log_error(f"刪除掛載點失敗：{e}")
                    return jsonify({'error': str(e)}), 500

        

        

        
        @self.app.route('/api/mount/<mount_name>/online')
        @self.require_login
        def api_mount_online_status(mount_name):
            """检查挂载点是否在线"""
            try:
                is_online = connection.is_mount_online(mount_name)
                mount_info = None
                if is_online:
                    mount_info = connection.get_connection_manager().get_mount_info(mount_name)
                
                return jsonify({
                    'mount_name': mount_name,
                    'online': is_online,
                    'mount_info': mount_info
                })
            except Exception as e:
                log_error(f"檢查掛載點線上狀態失敗：{e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/api/rovers', methods=['GET'])
        @self.require_login
        def api_rovers():
            """Return an authenticated, explicitly whitelisted rover snapshot."""
            try:
                manager = connection.get_connection_manager()
                rover_snapshot = manager.get_rover_status()
                online_mounts = manager.get_online_mounts()
                rovers = []

                for rover in rover_snapshot:
                    item = {
                        field_name: rover.get(field_name)
                        for field_name in ROVER_API_FIELDS
                    }
                    base_latitude, base_longitude = _get_base_coordinates(
                        online_mounts.get(item['mount_name'])
                    )
                    item['base_latitude'] = base_latitude
                    item['base_longitude'] = base_longitude
                    item['distance_to_base_km'] = None
                    if item['has_valid_position']:
                        item['distance_to_base_km'] = _distance_km(
                            item['latitude'],
                            item['longitude'],
                            base_latitude,
                            base_longitude,
                        )
                    rovers.append(item)

                return jsonify({
                    'success': True,
                    'rovers': rovers,
                    'total_count': len(rovers),
                    'freshness_threshold_seconds': (
                        connection.ROVER_GGA_FRESHNESS_SECONDS
                    ),
                    'timestamp': time.time(),
                })
            except Exception:
                log_error("取得 Rover 狀態失敗", exc_info=True)
                return jsonify({'error': '無法取得 Rover 狀態'}), 500
        
        @self.app.route('/api/system/stats')
        def api_system_stats():
            """获取系统统计数据"""
            try:
                # 获取服务器实例
                server = get_server_instance()
                if server and hasattr(server, 'get_system_stats'):
                    stats = server.get_system_stats()
                
                    return jsonify(_public_system_stats(stats))
                else:
                    log_error("API 錯誤：無法取得伺服器執行個體或 get_system_stats 方法")
                    return jsonify({'error': '無法取得系統統計資料'}), 500
            except Exception as e:
                log_error(f"API 例外：取得系統統計資料失敗：{e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/str-table', methods=['GET'])
        def api_str_table():
            """获取实时STR表数据"""
            try:
                # 获取所有在线挂载点的STR数据
                cm = connection.get_connection_manager()
                str_data = cm.get_all_str_data()
                
                # 生成完整的挂载点列表（包括STR表）
                mount_list = cm.generate_mount_list()
                
                return jsonify({
                    'success': True,
                    'str_data': str_data,
                    'mount_list': mount_list,
                    'timestamp': time.time()
                })
            except Exception as e:
                log_error(f"取得 STR 資料表失敗：{e}")
                return jsonify({
                    'success': False,
                    'error': str(e)
                }), 500
        
        @self.app.route('/api/mounts/online', methods=['GET'])
        def api_online_mounts_detailed():
            """获取详细的在线挂载点信息"""
            try:
                cm = connection.get_connection_manager()
                online_mounts = cm.get_online_mounts()
                
                # 为每个挂载点添加详细信息
                detailed_mounts = {}
                for mount_name, mount_info in online_mounts.items():
                    detailed_mounts[mount_name] = {
                        'basic_info': mount_info,
                        'str_data': cm.get_mount_str_data(mount_name),
                        'statistics': cm.get_mount_statistics(mount_name),
                        'connection_count': cm.get_mount_connection_count(mount_name)
                    }
                
                return jsonify({
                    'success': True,
                    'online_mounts': detailed_mounts,
                    'total_count': len(detailed_mounts),
                    'timestamp': time.time()
                })
            except Exception as e:
                log_error(f"取得線上掛載點詳細資料失敗：{e}")
                return jsonify({
                    'success': False,
                    'error': str(e)
                }), 500
        
        @self.app.route('/api/mount/<mount_name>/rtcm-parse/history', methods=['GET'])
        @self.require_login
        def api_get_rtcm_history(mount_name):
            """获取指定挂载点的历史解析数据"""
            try:
                # 获取解析结果
                parsed_data = rtcm_manager.get_parsed_mount_data(mount_name)
                if parsed_data:
                    return jsonify({
                        'success': True,
                        'data': parsed_data
                    })
                else:
                    return jsonify({
                        'success': False,
                        'error': '此掛載點目前沒有資料'
                    }), 404
            except Exception as e:
                log_error(f"取得掛載點 {mount_name} 的歷史資料失敗：{e}")
                return jsonify({'error': str(e)}), 500

    
    def _ensure_forwarder_started(self):
        """确保forwarder已启动（已在main.py中启动，此方法保留用于兼容性）"""
        # forwarder已经在main.py中启动，这里不需要重复启动
        pass
    
    def _register_socketio_events(self):
        """注册SocketIO事件"""
        
        @self.socketio.on('connect')
        def handle_connect():
            """客户端连接"""
            from flask import session
            client_id = session.get('sid', 'unknown')
            log_web_request('websocket', 'connect', client_id, 'WebSocket 用戶端連線')
            # 公開 room 只接收非敏感摘要；管理員 room 可接收管理日誌。
            join_room('data_push')
            if session.get('admin_logged_in'):
                join_room('admin_data')
            if config.LOG_FREQUENT_STATUS:
                log_info(f"用戶端 {client_id} 已加入 data_push 房間")
            emit('status', {'message': '連線成功'})
        
        @self.socketio.on('disconnect')
        def handle_disconnect():
            """客户端断开连接"""
            from flask import session
            client_id = session.get('sid', 'unknown')
            log_web_request('websocket', 'disconnect', client_id, 'WebSocket 用戶端中斷連線')
            # Web parser 是全域管理服務，不屬於單一瀏覽器連線。
            # 其生命週期僅由登入保護的 start/stop API 與正式關機流程管理。
            log_debug("WebSocket 用戶端已中斷；保留全域 Web 解析執行緒")
        
        @self.socketio.on('request_mount_data')
        def handle_request_mount_data(data):
            """请求挂载点数据"""
            mount = data.get('mount')
            if mount:
                parsed_data = rtcm_manager.get_parsed_mount_data(mount)
                statistics = rtcm_manager.get_mount_statistics(mount)
                emit('mount_data', {
                    'mount': mount,
                    'data': parsed_data,
                    'statistics': statistics
                })
        
        @self.socketio.on('request_recent_data')
        def handle_request_recent_data(data):
            """前端请求挂载点最近解析的数据"""
            mount_name = data.get('mount_name')
            if mount_name:
                recent_data = rtcm_manager.get_parsed_mount_data(mount_name)
                emit('recent_data_response', {
                    'mount_name': mount_name,
                    'data': recent_data
                })
        
        @self.socketio.on('request_system_stats')
        def handle_request_system_stats():
            """请求系统统计数据"""
            try:
                server = get_server_instance()
                if server and hasattr(server, 'get_system_stats'):
                    stats = server.get_system_stats()
                    if stats:
                        emit('system_stats_update', {
                            'stats': _public_system_stats(stats),
                            'timestamp': time.time()
                        })
                    else:
                        emit('error', {'message': '無法取得系統統計資料'})
                else:
                    emit('error', {'message': '伺服器執行個體無法使用'})
            except Exception as e:
                log_error(f"處理系統統計資料請求失敗：{e}")
                emit('error', {'message': str(e)})
    
    def require_login(self, f):
        """登录装饰器"""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('admin_logged_in'):
                # 检查是否是API请求
                if request.path.startswith('/api/'):
                    return jsonify({'error': '尚未登入或登入狀態已過期'}), 401
                else:
                    return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function
    
    def start_rtcm_parsing(self):
        """启动RTCM解析进程，持续解析数据并推送到前端"""
        # 现在RTCM解析集成在connection_manager中，无需单独启动
        
        # 启动实时数据推送
        if not self.push_running:
            self.push_running = True
            self._push_stop_event.clear()
            self.push_thread = Thread(target=self._push_data_loop, daemon=True)
            self.push_thread.start()
            log_system_event('Web 即時資料推送已啟動')
    
    def stop_rtcm_parsing(self):
        """停止RTCM解析"""
        # 现在RTCM解析集成在connection_manager中，无需单独停止
        
        # 停止实时数据推送
        if self.push_running:
            self.push_running = False
            self._push_stop_event.set()
            if self.push_thread:
                self.push_thread.join(timeout=5)
            log_system_event('Web 即時資料推送已停止')
    
    def _push_data_loop(self):
        """实时数据推送循环"""
        log_info("資料推送迴圈已啟動")
        while self.push_running:
            try:
                # 推送系统统计数据
                server = get_server_instance()
                if server and hasattr(server, 'get_system_stats'):
                    stats = server.get_system_stats()
                    if stats:
                        self.socketio.emit('system_stats_update', {
                            'stats': _public_system_stats(stats),
                            'timestamp': time.time()
                        }, to='data_push')
                        # 移除调试日志输出
                pass
                
                # 推送在线用户列表
                online_users = connection.get_connection_manager().get_online_users()
                online_user_summary = _public_online_user_summary(online_users)
                self.socketio.emit(
                    'online_users_update',
                    {
                        **online_user_summary,
                        'timestamp': time.time(),
                    },
                    to='data_push',
                )
                # 移除调试日志输出
                pass
                
                # 推送在线挂载点列表
                online_mounts = connection.get_connection_manager().get_online_mounts()
                self.socketio.emit('online_mounts_update', {
                    'mounts': online_mounts,
                    'timestamp': time.time()
                }, to='data_push')
                # 移除调试日志输出
                pass
                
                # 推送STR表数据
                str_data = connection.get_connection_manager().get_all_str_data()
                self.socketio.emit('str_data_update', {
                    'str_data': str_data,
                    'timestamp': time.time()
                }, to='data_push')
                # 移除调试日志输出
                pass
                
                self._push_stop_event.wait(config.REALTIME_PUSH_INTERVAL)
            except Exception as e:
                log_error(f"資料推送發生例外：{e}", exc_info=True)
                self._push_stop_event.wait(1)
    
    def push_log_message(self, message, log_type='info'):
        """推送日志消息到前端"""
        try:
            self.socketio.emit('log_message', {
                'message': message,
                'type': log_type,
                'timestamp': time.time()
            }, to='admin_data')
        except Exception as e:
            log_error(f"推送日誌訊息失敗：{e}")
    
    def _format_uptime(self, uptime_seconds):
        """格式化运行时间"""
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        seconds = int(uptime_seconds % 60)
        
        if days > 0:
            return f"{days}天 {hours}小時 {minutes}分鐘"
        elif hours > 0:
            return f"{hours}小時 {minutes}分鐘"
        else:
            return f"{minutes}分鐘 {seconds}秒"
    

    
    def run(self, host=None, port=None, debug=None):
        """启动Web服务器"""
        host = host or config.WEB_HOST
        port = port or config.WEB_PORT
        debug = debug if debug is not None else config.DEBUG
        http_server = make_server(host, port, self.app, threaded=True)

        with self._server_lock:
            if self._web_stop_requested:
                http_server.server_close()
                return
            self._http_server = http_server

        try:
            http_server.serve_forever()
        finally:
            http_server.server_close()
            with self._server_lock:
                if self._http_server is http_server:
                    self._http_server = None

    def stop_web_server(self):
        """停止可控的 Werkzeug WSGI server；重複呼叫不會重複關閉。"""
        with self._server_lock:
            self._web_stop_requested = True
            http_server = self._http_server
            self._http_server = None

        if http_server is not None:
            http_server.shutdown()

    def stop(self):
        """停止 Web listener 與其背景資料推送。"""
        self.stop_web_server()
        self.stop_rtcm_parsing()
    

# 便捷函数
def create_web_manager(db_manager, data_forwarder, start_time):
    """创建Web管理器实例"""
    return WebManager(db_manager, data_forwarder, start_time)
