"""管理介面台灣繁體中文的靜態回歸測試。"""

import ast
import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

FRONTEND_FILES = (
    PROJECT_ROOT / 'templates' / 'login.html',
    PROJECT_ROOT / 'templates' / 'spa.html',
    PROJECT_ROOT / 'static' / 'app.js',
)

BACKEND_MESSAGE_FILES = (
    PROJECT_ROOT / 'main.py',
    PROJECT_ROOT / 'src' / 'config.py',
    PROJECT_ROOT / 'src' / 'web.py',
    PROJECT_ROOT / 'src' / 'database.py',
    PROJECT_ROOT / 'src' / 'connection.py',
    PROJECT_ROOT / 'src' / 'forwarder.py',
    PROJECT_ROOT / 'src' / 'logger.py',
    PROJECT_ROOT / 'src' / 'ntrip.py',
    PROJECT_ROOT / 'src' / 'rtcm2.py',
    PROJECT_ROOT / 'src' / 'rtcm2_manager.py',
)

FORBIDDEN_SIMPLIFIED_TERMS = (
    '用户',
    '登录',
    '管理员',
    '连接',
    '挂载点',
    '数据',
    '日志',
    '配置',
    '保存',
    '删除',
    '添加',
    '页面',
    '网页',
    '后端',
    '实时',
    '信息',
    '监控',
    '统计',
    '获取',
    '发送',
    '线程',
    '关闭',
    '处理',
    '启动',
    '请求',
    '响应',
    '服务',
    '服务器',
    '端口',
    '程序',
    '状态',
    '错误',
    '失败',
    '加载',
    '在线',
    '离线',
    '密码',
    '用户名',
    '网络',
    '内存',
    '时间',
    '国家',
    '设备',
    '卫星',
    '地图',
    '选择',
    '输入',
    '编辑',
    '确认',
    '运行',
    '当前',
    '总连接',
    '队列',
    '数据库',
    '认证',
    '异常',
    '检测',
    '创建',
    '默认',
    '无法',
    '过期',
    '重复',
    '绑定',
    '说明',
    '传输',
    '活动',
    '访问',
    '显示',
    '频率',
    '映射',
    '模块',
    '隐藏',
    '结构',
    '存储',
    '发生',
    '系统',
    '概览',
    '仪表盘',
    '基础',
    '调试',
    '消息',
    '类型',
    '内容',
    '无效',
    '优先',
    '名称',
    '模拟',
    '动画',
    '全局',
    '样式',
    '客户端',
)

EXPECTED_TAIWAN_TERMS = (
    '使用者',
    '登入',
    '管理員',
    '連線',
    '掛載點',
    '資料',
    '日誌',
    '設定',
    '儲存',
    '刪除',
    '新增',
    '基站',
)

MESSAGE_CALLS = {
    'jsonify',
    'emit',
    'log_info',
    'log_warning',
    'log_error',
    'log_system_event',
    'log_database_operation',
    'print',
    'append',
}

def strip_non_visible_comments(source):
    """移除 HTML、CSS、JavaScript 註解，僅檢查可能顯示或執行的內容。"""
    source = re.sub(r'<!--.*?-->', '', source, flags=re.DOTALL)
    source = re.sub(r'/\*.*?\*/', '', source, flags=re.DOTALL)
    source = re.sub(r'^\s*//.*$', '', source, flags=re.MULTILINE)
    return source


def call_name(call):
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def string_constants(node):
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.lineno, child.value


class TraditionalChineseFrontendTests(unittest.TestCase):
    def test_pages_declare_taiwan_traditional_chinese_and_utf8(self):
        for relative_path in ('templates/login.html', 'templates/spa.html'):
            with self.subTest(path=relative_path):
                source = (PROJECT_ROOT / relative_path).read_text(encoding='utf-8')
                self.assertIn('<html lang="zh-Hant-TW">', source)
                self.assertRegex(source, r'<meta\s+charset=["\']UTF-8["\']\s*/?>')

    def test_visible_frontend_uses_required_taiwan_terms(self):
        visible_source = '\n'.join(
            strip_non_visible_comments(path.read_text(encoding='utf-8'))
            for path in FRONTEND_FILES
        )

        for term in EXPECTED_TAIWAN_TERMS:
            with self.subTest(term=term):
                self.assertIn(term, visible_source)

        for term in FORBIDDEN_SIMPLIFIED_TERMS:
            with self.subTest(term=term):
                self.assertNotIn(term, visible_source)

    def test_old_english_interface_labels_are_removed(self):
        visible_source = '\n'.join(
            strip_non_visible_comments(path.read_text(encoding='utf-8'))
            for path in FRONTEND_FILES
        )
        old_labels = (
            'Administrator Login',
            'User Management',
            'Mount Point Management',
            'Base Station Monitoring',
            'System Settings',
            'System Logs',
            'Add User',
            'Add Mount Point',
            'Change Administrator Password',
            'Restart Program',
        )

        for label in old_labels:
            with self.subTest(label=label):
                self.assertNotIn(label, visible_source)


class TraditionalChineseBackendMessageTests(unittest.TestCase):
    def test_web_messages_do_not_use_required_simplified_terms(self):
        failures = []

        for path in BACKEND_MESSAGE_FILES:
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            for node in ast.walk(tree):
                candidates = []
                if isinstance(node, ast.Call) and call_name(node) in MESSAGE_CALLS:
                    candidates.extend(string_constants(node))
                elif isinstance(node, ast.Return):
                    candidates.extend(string_constants(node))

                for lineno, message in candidates:
                    for term in FORBIDDEN_SIMPLIFIED_TERMS:
                        if term in message:
                            failures.append(
                                f'{path.relative_to(PROJECT_ROOT)}:{lineno}: {term} in {message!r}'
                            )

        self.assertEqual(failures, [], '\n'.join(failures))

    def test_flask_json_keeps_traditional_chinese_readable(self):
        source = (PROJECT_ROOT / 'src' / 'web.py').read_text(encoding='utf-8')
        self.assertIn('self.app.json.ensure_ascii = False', source)


class LocalizationSafetyTests(unittest.TestCase):
    def test_protocol_text_and_api_identifiers_are_preserved(self):
        ntrip_source = (PROJECT_ROOT / 'src' / 'ntrip.py').read_text(encoding='utf-8')
        web_source = (PROJECT_ROOT / 'src' / 'web.py').read_text(encoding='utf-8')
        app_source = (PROJECT_ROOT / 'static' / 'app.js').read_text(encoding='utf-8')

        self.assertIn('SOURCETABLE 200 OK', ntrip_source)
        self.assertIn('ICY 200 OK', ntrip_source)
        self.assertIn("data.get('mount_name')", web_source)
        self.assertIn("'rtcm_realtime_data'", web_source)
        self.assertIn("socket.on('rtcm_realtime_data'", app_source)
        self.assertIn('data.mount_name', app_source)

    def test_localized_sources_are_utf8_without_mojibake(self):
        mojibake_markers = ('\ufffd', 'Ã', 'Â', '锟斤拷', '馃', '鈥', '鈻')

        for path in FRONTEND_FILES + BACKEND_MESSAGE_FILES:
            with self.subTest(path=path.relative_to(PROJECT_ROOT)):
                source = path.read_bytes().decode('utf-8')
                for marker in mojibake_markers:
                    self.assertNotIn(marker, source)


if __name__ == '__main__':
    unittest.main()
