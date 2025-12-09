import sys
import os
import json
import subprocess
import webbrowser
from functools import partial
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMenu, QPushButton, QVBoxLayout,
    QHBoxLayout, QLabel, QScrollArea, QFrame, QSizePolicy, QLineEdit, QGridLayout, QGraphicsOpacityEffect,
    QDialog, QListWidget, QListWidgetItem
)
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QInputDialog
from PyQt6.QtCore import Qt, QPoint, QEvent, QSize, QTimer, QMimeData, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QRect, QSequentialAnimationGroup
from PyQt6.QtGui import QPainter, QColor, QBrush, QIcon, QPixmap, QDrag, QPen
from PyQt6.QtCore import pyqtSignal, QThread
from PyQt6.QtWidgets import QFileIconProvider
from PyQt6.QtCore import QFileInfo
import ctypes
from ctypes import wintypes
import math
import urllib.parse
import hashlib
import urllib.request
import ssl
from PyQt6.QtGui import QFontMetrics

# Windows Shell constants
SHGFI_ICON = 0x000000100
SHGFI_SMALLICON = 0x000000001
SHGFI_LARGEICON = 0x000000000


class SHFILEINFO(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


def extract_qicon_from_file(path):
    """尝试返回一个 QIcon：先用 QIcon(path)，失败时使用 Windows Shell 提取 HICON -> QPixmap -> QIcon。

    仅在 Windows 环境有效；若无法提取返回一个空的 QIcon().
    """
    if not path or not os.path.exists(path):
        return QIcon()

    icon = QIcon(path)
    if not icon.isNull():
        return icon

    # 尝试使用 QFileIconProvider（Qt 提供的文件图标提供者，通常能返回系统图标）
    try:
        provider = QFileIconProvider()
        qfi = QFileInfo(path)
        sys_icon = provider.icon(qfi)
        if not sys_icon.isNull():
            return sys_icon
    except Exception:
        pass

    # 尝试用 Windows API 提取图标并通过 QtWin 转换为 QPixmap（仅在 QtWin 可用时）
    try:
        # 延迟导入 QtWinExtras，以避免在无此模块时导入错误
        from PyQt6 import Qt6
        try:
            from PyQt6.QtWinExtras import QtWin
        except Exception:
            QtWin = None
        if QtWin is not None:
            shfi = SHFILEINFO()
            res = ctypes.windll.shell32.SHGetFileInfoW(path, 0, ctypes.byref(shfi), ctypes.sizeof(shfi), SHGFI_ICON | SHGFI_LARGEICON)
            if res:
                hIcon = shfi.hIcon
                if hIcon:
                    pix = QtWin.fromHICON(hIcon)
                    ctypes.windll.user32.DestroyIcon(hIcon)
                    if not pix.isNull():
                        return QIcon(pix)
    except Exception:
        pass

    return QIcon()

def resolve_windows_shortcut(path):
    """解析 .lnk 快捷方式，返回 (target_path, icon_path)。失败则返回 (None, None)。

    为避免复杂的 COM 封装，这里调用 PowerShell 读取 TargetPath 与 IconLocation，更稳定。
    """
    try:
        if not path.lower().endswith('.lnk'):
            return None, None
        if not os.path.exists(path):
            return None, None
        ps_path = path.replace("'", "''")
        cmd = [
            "powershell", "-NoLogo", "-NoProfile", "-Command",
            f"$s=New-Object -ComObject WScript.Shell; $lnk=$s.CreateShortcut('{ps_path}'); "
            "Write-Output $lnk.TargetPath; Write-Output $lnk.IconLocation;"
        ]
        out = subprocess.check_output(cmd, text=True, encoding='utf-8', errors='ignore')
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        target = lines[0] if lines else None
        icon_loc = lines[1] if len(lines) > 1 else None
        if icon_loc:
            icon_path = icon_loc.split(',', 1)[0].strip()
        else:
            icon_path = target
        return (target or None), (icon_path or None)
    except Exception:
        return None, None


def fetch_favicon_bytes(url, timeout=6):
    """尝试获取 favicon 字节数据：先访问 /favicon.ico，再尝试解析页面寻找 <link rel="icon">。"""
    try:
        parsed = urllib.parse.urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return None

    ctx = ssl.create_default_context()
    # 尝试 root /favicon.ico
    try:
        fav_url = urllib.parse.urljoin(base, '/favicon.ico')
        with urllib.request.urlopen(fav_url, timeout=timeout, context=ctx) as resp:
            data = resp.read()
            if data:
                return data
    except Exception:
        pass

    # 尝试解析主页寻找 link rel
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
    except Exception:
        html = ''

    # 简单解析 link rel=icon / shortcut icon
    import re
    m = re.search(r'<link[^>]+rel=["\']?(?:shortcut icon|icon)["\']?[^>]*>', html, re.IGNORECASE)
    if m:
        tag = m.group(0)
        href_m = re.search(r'href=["\']([^"\']+)["\']', tag)
        if href_m:
            href = href_m.group(1)
            fav_url = urllib.parse.urljoin(base, href)
            try:
                with urllib.request.urlopen(fav_url, timeout=timeout, context=ctx) as resp:
                    data = resp.read()
                    if data:
                        return data
            except Exception:
                pass

    return None


def save_icon_bytes_to_cache(key, data):
    try:
        cache_dir = os.path.join(os.path.dirname(__file__), 'icon_cache')
        os.makedirs(cache_dir, exist_ok=True)
        h = hashlib.sha1(key.encode('utf-8')).hexdigest()
        # try to guess extension from header bytes
        ext = '.ico'
        if data[:8].startswith(b'\x89PNG'):
            ext = '.png'
        elif data[:2] == b'BM':
            ext = '.bmp'
        elif data[:3] == b'GIF':
            ext = '.gif'
        fname = h + ext
        fpath = os.path.join(cache_dir, fname)
        with open(fpath, 'wb') as f:
            f.write(data)
        return fpath
    except Exception:
        return None


def _get_pixmap_for_icon_key(icon_key, btn_size):
    """根据 icon_key（可能是文件路径或 URL 或特殊 combo key）返回 QPixmap。
    如果找不到图标，返回一个带首字母的占位 pixmap。
    """
    try:
        # 如果已经存在于全局或 Launcher 的 icon_cache，会在调用处优先使用
        # 这里回退到尝试从文件提取或生成占位
        if os.path.exists(icon_key):
            icon = extract_qicon_from_file(icon_key)
            if not icon.isNull():
                pix = icon.pixmap(int(btn_size*0.8), int(btn_size*0.8))
                return pix
    except Exception:
        pass

    # 无法提取时创建占位
    try:
        pix = QPixmap(btn_size, btn_size)
        pix.fill(QColor(255, 255, 255, 0))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 背景圆角矩形
        brush = QBrush(QColor(240, 240, 240))
        painter.setBrush(brush)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = pix.rect().adjusted(4, 4, -4, -4)
        painter.drawRoundedRect(rect, 10, 10)
        # 首字母
        text = os.path.basename(icon_key)[:1].upper() if icon_key else '?'
        painter.setPen(QColor(120, 120, 120))
        fm = QFontMetrics(painter.font())
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        return pix
    except Exception:
        return QPixmap()


def generate_combo_icon(icon_items, size=112):
    """根据 icon_items 生成一个拼贴组合图标，返回 QIcon。
    优化：增加背景容器，并强制图标在格子里居中显示。
    """
    try:
        count = max(1, min(len(icon_items), 9))
        
        # 定义网格布局 (行数, 列数)
        if count == 1:
            grid = (1, 1)
        elif count == 2:
            # 2个图标：左右两列 (1行, 2列)
            grid = (1, 2)
        elif count <= 4:
            # 3-4个图标：2x2 网格
            grid = (2, 2)
        else:
            # 5个以上：3x3 网格
            grid = (3, 3)

        rows, cols = grid
        # 增加一点内边距，让图标不要贴着边框
        pad = int(size * 0.1) 
        
        # 计算每个格子的最大可用宽高
        # 总宽度减去所有间隙，除以列数
        cell_w = (size - pad * (cols + 1)) // cols
        cell_h = (size - pad * (rows + 1)) // rows

        out = QPixmap(size, size)
        out.fill(QColor(0, 0, 0, 0)) # 透明背景
        
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        
        # --- 1. 绘制文件夹背景 (容器) ---
        # 透明磨砂效果背景
        bg_color = QColor(240, 240, 240, 200)  # 半透明白色背景
        border_color = QColor(200, 200, 200, 150)  # 半透明边框
        
        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(border_color, 2)) 
        # 绘制圆角矩形背景
        rect = out.rect().adjusted(2, 2, -2, -2)
        painter.drawRoundedRect(rect, 18, 18)
        
        # 添加文件夹顶部标签效果
        folder_tab_color = QColor(220, 220, 220, 180)
        painter.setBrush(QBrush(folder_tab_color))
        painter.setPen(QPen(border_color, 1))
        tab_rect = QRect(rect.x() + rect.width() * 0.3, rect.y() - 8, rect.width() * 0.4, 16)
        painter.drawRoundedRect(tab_rect, 8, 8)

        # --- 2. 绘制每个子图标 ---
        for idx in range(count):
            r = idx // cols
            c = idx % cols
            
            # 计算当前格子的左上角坐标
            x_cell = pad + c * (cell_w + pad)
            y_cell = pad + r * (cell_h + pad)
            
            item = icon_items[idx]
            pix = None
            
            # --- 获取图片逻辑 (保持原逻辑不变) ---
            try:
                if isinstance(item, QPixmap):
                    pix = item
                elif isinstance(item, QIcon):
                    pix = item.pixmap(cell_w, cell_h)
                elif isinstance(item, str):
                    key = item
                    if os.path.exists(key):
                        pix = QPixmap(key)
                    else:
                        # 尝试从缓存加载
                        cache_dir = os.path.join(os.path.dirname(__file__), 'icon_cache')
                        if os.path.isdir(cache_dir):
                            h = hashlib.sha1(key.encode('utf-8')).hexdigest()
                            for fn in os.listdir(cache_dir):
                                if fn.startswith(h):
                                    pix = QPixmap(os.path.join(cache_dir, fn))
                                    break
            except Exception:
                pix = None

            # 如果没找到图，生成首字母占位
            if pix is None or pix.isNull():
                pix = _get_pixmap_for_icon_key(str(item), max(cell_w, cell_h))

            # --- 绘制逻辑 (核心优化) ---
            if pix and not pix.isNull():
                # 1. 按比例缩放到适合格子的大小
                scaled_pix = pix.scaled(cell_w, cell_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                
                # 2. 计算居中偏移量 (重要步骤，防止图标飘在左上角)
                off_x = (cell_w - scaled_pix.width()) // 2
                off_y = (cell_h - scaled_pix.height()) // 2
                
                # 3. 绘制
                target_x = x_cell + off_x
                target_y = y_cell + off_y
                painter.drawPixmap(target_x, target_y, scaled_pix)

        painter.end()
        return QIcon(out)
    except Exception as e:
        print(f"Combo icon error: {e}")
        return QIcon()

DEBUG = False

def dbg(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)

class FloatingBall(QWidget):
    def __init__(self):
        super().__init__()
        # apps 列表会在 init_ui 之前通过 load_config 加载
        self.apps = []
        self.load_config()
        self.init_ui()
        # icon cache shared across launcher windows
        self._global_icon_cache = {}

    def init_ui(self):
        # 1. 设置窗口大小
        self.setFixedSize(60, 60)

        # 2. 去掉标题栏和边框 (Frameless)
        # 这里的 WindowStaysOnTopHint 让它永远置顶
        # Tool 属性 Lets it not appear in task bar (Optional)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | 
                            Qt.WindowType.WindowStaysOnTopHint | 
                            Qt.WindowType.Tool)

        # 3. 设置背景透明
        # 如果不设这个，你的圆球外面会有一个黑色的矩形框
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 用于记录鼠标拖拽的偏移量
        self.drag_pos = QPoint()

        # 显示窗口
        self.show()

    # --- 绘制部分 (类似 HTML5 Canvas) ---
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing) # 抗锯齿，让圆滑一点

        # 设置画刷颜色 (这里用的是半透明的蓝色)
        # QColor(R, G, B, Alpha) -> Alpha 200 代表 80% 不透明
        painter.setBrush(QBrush(QColor(66, 135, 245, 200))) 
        
        # 去掉边线
        painter.setPen(Qt.PenStyle.NoPen)
        
        # 画圆 (在 0,0 位置，宽60，高60)
        painter.drawEllipse(0, 0, 60, 60)

    # --- 鼠标事件处理 (核心交互逻辑) ---
    def mousePressEvent(self, event):
        # 区分左键与右键：
        if event.button() == Qt.MouseButton.LeftButton:
            # 左键按下：记录用于拖拽的偏差，同时记录按下位置以便判断是拖拽还是单击
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            # 记录按下的全局位置与按键，用于在释放时判定是否为点击
            self._press_pos = event.globalPosition().toPoint()
            self._press_button = event.button()
            self._moved = False
            event.accept()

        elif event.button() == Qt.MouseButton.RightButton:
            # 右键：弹出带样式的菜单，菜单项为“退出程序”
            menu = QMenu(self)
            # 增大菜单最小宽度以便显示更长的文字
            menu.setStyleSheet(
                "QMenu { min-width: 220px; background-color: white; color: black; border: 1px solid #ccc; }"
                "QMenu::item:selected { background-color: #e6e6e6; }"
            )
            menu.addAction('退出程序', lambda: QApplication.instance().quit())
            # 在鼠标的全局位置显示菜单
            menu.exec(event.globalPosition().toPoint())
            event.accept()

    def mouseReleaseEvent(self, event):
        # 左键释放：如果没有移动（判定为点击），弹出 Launcher 菜单
        if event.button() == Qt.MouseButton.LeftButton:
            # 如果在移动过程中已标记为移动，则不弹出菜单
            # 另外要求：必须是同一次按下/释放（按键一致），且释放位置与按下位置距离在系统阈值内
            try:
                is_same_button = getattr(self, '_press_button', None) == event.button()
                press_pos = getattr(self, '_press_pos', None)
                moved_flag = getattr(self, '_moved', False)
                within_click_distance = True
                if press_pos is not None:
                    delta = event.globalPosition().toPoint() - press_pos
                    within_click_distance = delta.manhattanLength() <= QApplication.startDragDistance()

                if (not moved_flag) and is_same_button and within_click_distance:
                    launcher = LauncherWindow(self.apps, launcher_callback=self.launch_app)
                    launcher.show()
                    # 延迟居中与首次布局，等待 Qt 完成初始布局计算
                    def center_and_layout():
                        try:
                            launcher.rebuild_app_grid()
                        except Exception:
                            pass
                        try:
                            screen_geom = QApplication.primaryScreen().availableGeometry()
                            x = screen_geom.x() + (screen_geom.width() - launcher.width()) // 2
                            y = screen_geom.y() + (screen_geom.height() - launcher.height()) // 2
                            launcher.move(x, y)
                        except Exception as e:
                            print(f"居中启动器失败: {e}")

                    QTimer.singleShot(0, center_and_layout)
            except Exception as e:
                print(f"打开启动器窗口失败: {e}")
            finally:
                # 清理按下标记
                self._press_pos = None
                self._press_button = None
            event.accept()

    def mouseMoveEvent(self, event):
        # 当鼠标按住并移动时
        if event.buttons() & Qt.MouseButton.LeftButton:
            # 如果移动距离较大，判定为拖拽并移动窗口
            if hasattr(self, '_press_pos') and self._press_pos is not None:
                delta = event.globalPosition().toPoint() - self._press_pos
                if delta.manhattanLength() > QApplication.startDragDistance():
                    self._moved = True
            # 移动窗口：新的屏幕坐标 - 之前的偏移量
            self.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()
            
    def launch_app(self, path):
        """非阻塞启动外部程序（Windows 可执行文件）。

        使用 subprocess.Popen 启动，捕获异常并打印错误信息。
        """
        try:
            # 如果是 URL，则使用默认浏览器打开
            if isinstance(path, str) and path.lower().startswith(('http://', 'https://')):
                webbrowser.open(path)
                return

            # 如果是目录，则使用系统文件管理器打开
            if os.path.isdir(path):
                try:
                    # 在 Windows 上，os.startfile 更直观
                    os.startfile(path)
                except Exception:
                    subprocess.Popen(['explorer', path])
                return

            # 否则尝试作为可执行文件或文档打开
            if os.path.exists(path):
                subprocess.Popen([path])
            else:
                print(f"启动失败：路径不存在 - {path}")
        except FileNotFoundError:
            print(f"启动失败：找不到可执行文件 - {path}")
        except Exception as e:
            print(f"启动程序时发生错误: {e}")

    def load_config(self):
        """从当前脚本目录加载 apps.json 配置文件，填充 self.apps 列表。

        配置示例格式：
        [
            {"name": "计算器", "path": "C:\\Windows\\System32\\calc.exe"},
            {"name": "记事本", "path": "C:\\Windows\\System32\\notepad.exe"}
        ]
        """
        config_path = os.path.join(os.path.dirname(__file__), 'apps.json')
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.apps = data
                    else:
                        print(f"apps.json 内容不是列表，忽略: {config_path}")
            else:
                # 不报错，仅告知用户可以创建该文件
                print(f"未找到配置文件，使用内置默认菜单。可创建 {config_path} 来自定义应用列表。")
        except json.JSONDecodeError as e:
            print(f"解析 apps.json 失败: {e}")
        except Exception as e:
            print(f"读取 apps.json 时发生错误: {e}")


class DragButton(QPushButton):
    """支持拖拽启动的按钮，拖动时会把关联的 path 作为 MIME 文本传出。"""
    def __init__(self, drag_data='', *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._drag_start_pos = None
        self._drag_data = drag_data

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()

    def mouseMoveEvent(self, event):
        # 仅保留鼠标移动基础行为；AppCell 负责发起拖放以便拖动整个单元
        super().mouseMoveEvent(event)


class AppCell(QWidget):
    """单个应用单元：包含可拖动的按钮与名称标签，支持作为 drop 目标。"""
    def __init__(self, app, parent_window, btn_size, parent=None):
        super().__init__(parent)
        self.app = app
        self.parent_window = parent_window
        self.setAcceptDrops(True)
        # 用于检测整体单元拖动
        self._drag_start_pos = None
        self._is_dragging = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(6)

        self.btn = DragButton(drag_data=app.get('path',''))
        self.btn.setFixedSize(btn_size, btn_size)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setStyleSheet('QPushButton{border-radius:12px;border:1px solid #ddd;background:#fff;} QPushButton:hover{background:#f5f5f5;}')
        layout.addWidget(self.btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        lbl = QLabel()
        fm = QFontMetrics(lbl.font())
        elided = fm.elidedText(app.get('name','Unnamed'), Qt.TextElideMode.ElideRight, btn_size + 8)
        lbl.setText(elided)
        lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lbl.setFixedHeight(fm.height() + 2)
        lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(lbl)

        # 把按钮的事件转交给本单元处理，以便整体拖动（但保持按钮的点击可用）
        self.btn.installEventFilter(self)

    def eventFilter(self, source, event):
        # 仅处理来自子控件（主要是按钮）的鼠标按下/移动/释放，用以触发整体拖动
        if source is self.btn:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                # 记录按下全局位置与当前组件位置
                self._drag_start_pos = event.globalPosition().toPoint()
                self._is_dragging = False
                return False

            if event.type() == QEvent.Type.MouseMove and self._drag_start_pos is not None:
                # 如果超过系统阈值，则进入拖拽模式（父窗口负责移动与重排）
                delta = event.globalPosition().toPoint() - self._drag_start_pos
                if not self._is_dragging and delta.manhattanLength() >= QApplication.startDragDistance():
                    self._is_dragging = True
                    # 告知父窗口开始拖动
                    self.parent_window.start_drag(self, self._drag_start_pos)
                if self._is_dragging:
                    # 实时更新父窗口中被拖动单元的位置
                    self.parent_window.update_drag(self, event.globalPosition().toPoint())
                    return True
                return False

            if event.type() == QEvent.Type.MouseButtonRelease:
                if self._is_dragging:
                    # 结束拖动
                    self.parent_window.end_drag(self, event.globalPosition().toPoint())
                    # 已处理拖拽释放事件——不要交给按钮触发 clicked
                    self._drag_start_pos = None
                    self._is_dragging = False
                    return True
                # 非拖拽的正常释放，让按钮继续处理（返回 False）
                self._drag_start_pos = None
                self._is_dragging = False
                return False

        return super().eventFilter(source, event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasText():
            source_path = event.mimeData().text()
            target_path = self.app.get('path')
            try:
                self.parent_window.reorder_apps(source_path, target_path)
            except Exception:
                pass
            event.acceptProposedAction()
        else:
            event.ignore()
    
class LauncherWindow(QWidget):
    """自定义圆角启动器窗口，居中显示，右上角有最小化/最大化/关闭按钮。"""
    def __init__(self, apps, launcher_callback=None):
        super().__init__(None)
        self.apps = apps or []
        self.launcher_callback = launcher_callback
        self._maximized = False
        self._prev_geometry = None
        # 可配置的图标按钮尺寸（像素），修改此值可改变网格中图标大小
        self.btn_size = 112
        # icon cache: path -> QIcon
        self.icon_cache = {}
        # path -> list of QPushButton instances to update
        self.path_buttons = {}
        # set of paths currently loading
        self.loading_set = set()
        # keep threads references
        self._threads = []
        self.init_ui()

    def init_ui(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # 初始尺寸与最小/最大限制（放大启动器窗口）
        self.resize(700, 480)
        self.setMinimumSize(480, 300)
        screen_geom = QApplication.primaryScreen().availableGeometry()
        self.setMaximumSize(int(screen_geom.width() * 0.9), int(screen_geom.height() * 0.9))

        # 主容器，使用样式化圆角白色背景，通过布局自适应内容
        self.main_frame = QFrame(self)
        self.main_frame.setObjectName('main_frame')
        self.main_frame.setStyleSheet('#main_frame { background: white; border-radius: 12px; }')

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.main_frame)

        frame_layout = QVBoxLayout(self.main_frame)
        frame_layout.setContentsMargins(12, 12, 12, 12)
        frame_layout.setSpacing(8)

        # 顶部栏（用于拖动和放置窗口按钮）
        title = QLabel('Launcher')
        title.setStyleSheet('font-weight:600;')

        # 三个窗口控制按钮
        btn_min = QPushButton('-')
        btn_max = QPushButton('□')
        btn_close = QPushButton('✕')
        for b in (btn_min, btn_max, btn_close):
            b.setFixedSize(26, 22)
            b.setFlat(True)
            b.setStyleSheet('QPushButton{border:none;background:transparent;} QPushButton:hover{background:#e6e6e6;border-radius:4px;}')

        btn_min.clicked.connect(self.showMinimized)
        btn_max.clicked.connect(self.toggle_maximize)
        btn_close.clicked.connect(self.close)

        # 布局：标题左侧，按钮放右侧
        top_container = QWidget(self.main_frame)
        top_layout = QHBoxLayout(top_container)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(title)
        top_layout.addStretch()
        top_layout.addWidget(btn_min)
        top_layout.addWidget(btn_max)
        top_layout.addWidget(btn_close)
        frame_layout.addWidget(top_container)
        # 仅在顶栏生效的拖拽，通过事件过滤器实现
        top_container.installEventFilter(self)

        # 搜索框（用于动态过滤）
        self.search = QLineEdit(self.main_frame)
        self.search.setPlaceholderText('搜索应用...')
        self.search.textChanged.connect(self.on_search_text_changed)
        frame_layout.addWidget(self.search)

        # 内容区：放入 QScrollArea 以便当应用过多时出现滚动条
        scroll = QScrollArea(self.main_frame)
        scroll.setWidgetResizable(True)
        content_widget = QWidget()
        # 使用绝对定位的内容区（不使用 QGridLayout），实现自定义流式布局与动画重排
        self.content_widget = content_widget
        self.content_widget.setAcceptDrops(True)
        # spacing / margin 设置
        self.grid_spacing = 22  # 稍微加大间距，减少误吸附
        self.grid_margin = 12
        scroll.setWidget(content_widget)
        frame_layout.addWidget(scroll)

        # 保存引用以便重建
        self._content_widget = content_widget
        self._scroll = scroll
        self._drag_pos = None
        # cells 对应当前 self.apps 的可视单元（顺序即显示顺序）
        self.cells = []
        # 记录每次布局计算出的格子位置 (list of QPoint)
        self.grid_positions = []
        # 正在拖拽的单元
        self._dragging_cell = None
        self._dragging_offset = QPoint(0,0)
        # 磁吸目标与其原始 geometry（用于动画恢复）
        self._magnet_target = None
        self._magnet_orig_geom = None
        self._magnet_offset = QPoint(0, 0)
        self._magnet_threshold = 26  # 磁吸触发距离（像素）稍放宽以增强吸附感
        self._magnet_delay_ms = 320  # 停留时间阈值（毫秒）加快确认
        self._magnet_candidate = None
        self._magnet_candidate_snap = None
        self._magnet_timer = QTimer(self)
        self._magnet_timer.setSingleShot(True)
        self._magnet_timer.timeout.connect(self._confirm_magnet_candidate)
        # 动画引用池，防止被回收
        self._anims = []
        # 支持窗口级拖拽添加
        self.setAcceptDrops(True)

        # 初次填充应用网格
        # 延迟首次填充，使控件完成 show/layout 后再计算尺寸
        QTimer.singleShot(0, lambda: self.rebuild_app_grid())

        # 根据内容自适应并限制到屏幕可视区域
        # 默认窗口更大一点以适配更大图标网格
        default_w = min(int(screen_geom.width() * 0.7), 820)
        default_h = min(int(screen_geom.height() * 0.7), 640)
        self.resize(default_w, default_h)

    def resizeEvent(self, event):
        # 保证 main_frame 和子容器随窗口大小更新
        self.main_frame.setGeometry(0, 0, self.width(), self.height())
        return super().resizeEvent(event)

    def toggle_maximize(self):
        screen_geom = QApplication.primaryScreen().availableGeometry()
        if not self._maximized:
            self._prev_geometry = self.geometry()
            self.setGeometry(screen_geom)
            self._maximized = True
        else:
            if self._prev_geometry:
                self.setGeometry(self._prev_geometry)
            self._maximized = False

    def _on_launch(self, path):
        try:
            if self.launcher_callback:
                self.launcher_callback(path)
            else:
                subprocess.Popen([path])
        except Exception as e:
            print(f"启动应用失败: {e}")
        # 启动后关闭启动器窗口
        self.close()

    def _apply_magnet_style(self, widget, enable, strong=False):
        """为磁吸预览/锁定添加视觉效果。"""
        try:
            if not widget:
                return
            if enable:
                color = '#5b8cff' if strong else '#8fb3ff'
                width = 3 if strong else 2
                widget.setStyleSheet(f'QWidget{{border:{width}px {"solid" if strong else "dashed"} {color}; border-radius:14px;}}')
            else:
                widget.setStyleSheet('')
        except Exception:
            pass

    def _clear_magnet_style_on_all(self):
        """清除当前所有单元上的磁吸高亮。"""
        try:
            for c in self.cells:
                self._apply_magnet_style(c, False)
        except Exception:
            pass

    def _pulse_widget(self, widget, factor=1.08, duration=150):
        """小幅脉冲动画，模拟吸附的“弹”一下。"""
        try:
            if not widget:
                return
            g = widget.geometry()
            cx, cy = g.center().x(), g.center().y()
            new_w = int(g.width() * factor)
            new_h = int(g.height() * factor)
            new_x = cx - new_w // 2
            new_y = cy - new_h // 2
            expanded = QRect(new_x, new_y, new_w, new_h)

            anim_up = QPropertyAnimation(widget, b'geometry', self)
            anim_up.setDuration(duration)
            anim_up.setEasingCurve(QEasingCurve.Type.OutBack)
            anim_up.setStartValue(g)
            anim_up.setEndValue(expanded)

            anim_down = QPropertyAnimation(widget, b'geometry', self)
            anim_down.setDuration(duration)
            anim_down.setEasingCurve(QEasingCurve.Type.OutBack)
            anim_down.setStartValue(expanded)
            anim_down.setEndValue(g)

            group = QSequentialAnimationGroup(self)
            group.addAnimation(anim_up)
            group.addAnimation(anim_down)
            group.start()
            self._anims.append(group)
        except Exception:
            pass

    def rebuild_app_grid(self, filter_text=''):
        """根据 self.apps 和 filter_text 重新生成图标网格（多列）。

        使用绝对定位与自定义网格位置，生成 self.cells 列表与 grid_positions。
        """
        # 清理之前的 path_buttons，避免旧的按钮引用残留
        self.path_buttons = {}
        btn_size = getattr(self, 'btn_size', 72)

        # 清除内容区所有子控件
        for ch in list(self._content_widget.children()):
            if isinstance(ch, QWidget):
                ch.setParent(None)
                ch.deleteLater()

        self.cells = []
        self.grid_positions = []

        apps = self.apps
        if filter_text:
            ft = filter_text.lower()
            apps = [a for a in apps if ft in (a.get('name','').lower())]

        if not apps:
            lbl = QLabel('未找到匹配的应用。', self._content_widget)
            lbl.move(self.grid_margin, self.grid_margin)
            lbl.show()
            return

        # 计算列数（基于可见宽度）
        try:
            avail_w = max(200, self._scroll.viewport().width())
        except Exception:
            avail_w = max(200, self.width())
        spacing = getattr(self, 'grid_spacing', 16)
        margin = getattr(self, 'grid_margin', 12)
        cols = max(1, avail_w // (btn_size + spacing))

        n = len(apps)
        rows = math.ceil(max(1, n + 1) / cols)  # 预留“添加”按钮一格

        # 预计算每个格子的位置
        cell_h = btn_size + (QFontMetrics(QLabel().font()).height() + 2)
        positions = []
        for idx in range(n):
            r = idx // cols
            c = idx % cols
            x = margin + c * (btn_size + spacing)
            y = margin + r * (cell_h + spacing)
            positions.append(QPoint(x, y))

        # 更新内容 widget 最小高度以支持滚动
        total_h = margin + rows * (cell_h + spacing)
        self._content_widget.setMinimumHeight(total_h + margin)

        # 创建单元并绝对定位
        for idx, app in enumerate(apps):
            pos = positions[idx]
            cell = AppCell(app, self, btn_size, parent=self._content_widget)
            # connect launch/click behavior on inner button
            if app.get('combo'):
                # 组合图标：点击启动组合内所有应用
                cell.btn.clicked.connect(partial(self._on_launch_combo, app))
            elif app.get('path'):
                cell.btn.clicked.connect(partial(self._on_launch, app.get('path')))
            else:
                cell.btn.setEnabled(False)
            # context menu on inner button
            try:
                cell.btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                cell.btn.customContextMenuRequested.connect(lambda pos, a=app, b=cell.btn: self.on_app_context_menu(a, b, pos))
            except Exception:
                pass

            # 注册 tooltip 与初始图标显示（从缓存取或显示首字母占位）
            try:
                display_name = app.get('name', '') or ''
                cell.btn.setToolTip(display_name)
                # 选择用于图标加载的 key（优先 app['icon']，回退到 path）
                icon_key = app.get('icon') or app.get('path') or ''
                if icon_key in self.icon_cache:
                    icon = self.icon_cache.get(icon_key)
                    if not icon.isNull():
                        cell.btn.setIcon(icon)
                        cell.btn.setIconSize(QSize(int(cell.btn.width()*0.6), int(cell.btn.height()*0.6)))
                        cell.btn.setText('')
                    else:
                        # 使用首字母作为文本占位
                        if display_name:
                            cell.btn.setText(display_name[0])
                else:
                    if display_name:
                        cell.btn.setText(display_name[0])
                # 把按钮注册到 path_buttons 映射，供 IconLoader 回调更新
                if icon_key:
                    # 对于组合图标，我们生成图标并缓存到 special key
                    if app.get('combo'):
                        # icon_keys 为组合成员的 icon 或 path
                        comp_keys = []
                        for member in app.get('combo', []):
                            # member 可能是 dict (保存 name/path/icon)
                            if isinstance(member, dict):
                                comp_keys.append(member.get('icon') or member.get('path') or '')
                            else:
                                comp_keys.append(str(member))
                        combo_key = 'combo:' + hashlib.sha1(','.join(comp_keys).encode('utf-8')).hexdigest()
                        # 立刻生成图标并缓存
                        try:
                            icon = generate_combo_icon(comp_keys, size=btn_size)
                            if not icon.isNull():
                                self.icon_cache[combo_key] = icon
                                cell.btn.setIcon(icon)
                                cell.btn.setIconSize(QSize(int(cell.btn.width()*0.6), int(cell.btn.height()*0.6)))
                                cell.btn.setText('')
                                # register under combo_key so future updates may address it
                                self.path_buttons.setdefault(combo_key, []).append(cell.btn)
                        except Exception:
                            pass
                    else:
                        self.path_buttons.setdefault(icon_key, []).append(cell.btn)
            except Exception:
                pass

            cell.setFixedSize(btn_size, cell_h)
            cell.move(pos)
            cell.show()
            self.cells.append(cell)
            self.grid_positions.append(pos)

        # 添加“添加应用”按钮作为最后一个单元
        add_btn = QPushButton(self._content_widget)
        add_btn.setFixedSize(btn_size, btn_size)
        add_btn.setToolTip('添加应用')
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setText('+')
        add_btn.setStyleSheet('QPushButton{border-radius:12px;border:1px dashed #bbb;background:#fff;font-size:24px;} QPushButton:hover{background:#f5f5f5;}')
        add_btn.clicked.connect(self.add_app_via_dialog)
        # label 下方
        add_cell = QWidget(self._content_widget)
        layout_inner = QVBoxLayout(add_cell)
        layout_inner.setContentsMargins(0,0,0,0)
        layout_inner.setSpacing(6)
        layout_inner.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        lbl = QLabel('添加')
        fm = QFontMetrics(lbl.font())
        lbl.setText(fm.elidedText('添加', Qt.TextElideMode.ElideRight, btn_size + 8))
        lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lbl.setFixedHeight(fm.height() + 2)
        layout_inner.addWidget(lbl)
        add_idx = n  # 紧跟应用后
        add_r = add_idx // cols
        add_c = add_idx % cols
        add_pos = QPoint(margin + add_c * (btn_size + spacing),
                         margin + add_r * (cell_h + spacing))
        add_cell.setFixedSize(btn_size, cell_h)
        add_cell.move(add_pos)
        add_cell.show()
        # not part of reorderable cells
        # 注册 icon 加载同样逻辑（使用 app['icon'] if present）
        for i, app in enumerate(apps):
            icon_path = app.get('icon')
            if icon_path and icon_path not in self.icon_cache and icon_path not in self.loading_set:
                loader = IconLoader(icon_path)
                loader.icon_loaded.connect(self._on_icon_loaded)
                self._threads.append(loader)
                self.loading_set.add(icon_path)
                loader.start()

    def resizeEvent(self, event):
        # 窗口大小变化时重新布局网格，并保持 main_frame 大小同步
        try:
            self.main_frame.setGeometry(0, 0, self.width(), self.height())
        except Exception:
            pass
        try:
            super().resizeEvent(event)
        finally:
            # 延迟重建以确保布局组件尺寸已更新
            try:
                self.rebuild_app_grid(self.search.text() if hasattr(self, 'search') else '')
            except Exception:
                pass

    def on_search_text_changed(self, text):
        self.rebuild_app_grid(text)

    # --- 外部拖放添加应用 ---
    def dragEnterEvent(self, event):
        try:
            if event.mimeData().hasUrls() or event.mimeData().hasText():
                event.acceptProposedAction()
            else:
                event.ignore()
        except Exception:
            event.ignore()

    def dropEvent(self, event):
        handled = False
        try:
            if event.mimeData().hasUrls():
                urls = event.mimeData().urls()
                if urls:
                    handled = self._handle_drop_urls(urls)
            elif event.mimeData().hasText():
                text = event.mimeData().text().strip()
                handled = self._handle_drop_text(text)
        finally:
            if handled:
                event.acceptProposedAction()
            else:
                event.ignore()

    def _handle_drop_urls(self, urls):
        """处理从文件管理器/浏览器拖入的 url 列表。仅取第一个。"""
        try:
            if not urls:
                return False
            url = urls[0]
            if url.isLocalFile():
                path = url.toLocalFile()
                if not path or not os.path.exists(path):
                    return False
                abs_path = os.path.abspath(path)
                # 若是快捷方式，解析目标与图标路径，尽量使用目标 exe/dir 的图标
                icon_path = abs_path
                if abs_path.lower().endswith('.lnk'):
                    tgt, ico = resolve_windows_shortcut(abs_path)
                    if tgt:
                        abs_path = tgt
                        icon_path = ico or tgt
                if os.path.isdir(abs_path):
                    name = os.path.basename(os.path.normpath(abs_path)) or abs_path
                else:
                    name = os.path.splitext(os.path.basename(abs_path))[0] or abs_path
                # 与手动添加一致：path/icon 使用实际路径，去重用绝对路径
                return self._add_app_entry(name=name, path=abs_path, icon=icon_path)
            else:
                # 非本地文件，按文本 URL 处理
                return self._handle_drop_text(url.toString())
        except Exception as e:
            print(f"处理拖入文件失败: {e}")
            return False

    def _handle_drop_text(self, text):
        """处理纯文本拖入（主要是 URL）。"""
        try:
            if not text:
                return False
            t = text.strip()
            # 只接受 http/https；与手动添加一致，缺 scheme 自动补全 http://
            if not t.lower().startswith(('http://', 'https://')):
                t = 'http://' + t
            # 规范化尾部斜杠
            t = t.rstrip('/')
            try:
                parsed = urllib.parse.urlparse(t)
                name = parsed.netloc or t
            except Exception:
                name = t
            return self._add_app_entry(name=name, path=t, icon=t)
        except Exception as e:
            print(f"处理拖入文本失败: {e}")
            return False

    def _add_app_entry(self, name, path, icon):
        """去重后添加应用并刷新。"""
        if not path:
            return False
        key = os.path.abspath(path) if os.path.exists(path) else path
        for a in self.apps:
            existing = a.get('path') or ''
            if existing:
                if os.path.exists(existing):
                    try:
                        if os.path.abspath(existing) == key:
                            print("拖入的应用已存在，忽略。")
                            return False
                    except Exception:
                        pass
                if existing == key:
                    print("拖入的应用已存在，忽略。")
                    return False
        new_app = {"name": name, "path": key, "icon": icon}
        self.apps.append(new_app)
        try:
            self.save_config()
        except Exception as e:
            print(f"保存拖入应用失败: {e}")
        self.rebuild_app_grid(self.search.text() if hasattr(self, 'search') else '')
        return True

    def on_app_context_menu(self, app, btn, pos):
        menu = QMenu(self)
        menu.addAction('重命名', lambda: self.rename_app(app))
        menu.addAction('删除', lambda: self.delete_app(app))
        
        # 如果是组合应用，提供解散选项
        if app.get('combo'):
            menu.addSeparator()
            menu.addAction('解散组合', lambda: self.dissolve_combo(app))
        
        global_pos = btn.mapToGlobal(pos)
        menu.exec(global_pos)

    def _on_launch_combo(self, app):
        """同时启动组合中的所有成员（按顺序）。"""
        try:
            members = self._flatten_combo_apps(app)
            for m in members:
                path = None
                if isinstance(m, dict):
                    path = m.get('path')
                else:
                    path = str(m)
                if path:
                    try:
                        if self.launcher_callback:
                            self.launcher_callback(path)
                        else:
                            subprocess.Popen([path])
                    except Exception:
                        pass
            # 组合启动后关闭启动器窗口
            self.close()
        except Exception:
            pass

    def _flatten_combo_apps(self, app):
        """将组合展开成成员列表，普通应用返回自身列表。"""
        if isinstance(app, dict) and app.get('combo'):
            result = []
            for m in app.get('combo') or []:
                result.extend(self._flatten_combo_apps(m))
            return result
        return [app]

    def dissolve_combo(self, app):
        """对给定的组合应用执行消散动画并在动画结束后从 apps 列表中移除。"""
        try:
            # 找到在 apps 中的索引，以及对应的 cell
            idx = None
            for i, a in enumerate(self.apps):
                if a is app:
                    idx = i
                    break
            if idx is None:
                return
            if idx < len(self.cells):
                cell = self.cells[idx]
            else:
                cell = None

            # 如果有对应的 cell，做并行动画：放大 + 透明度变为 0
            if cell is not None:
                try:
                    effect = QGraphicsOpacityEffect(cell)
                    cell.setGraphicsEffect(effect)
                    anim_op = QPropertyAnimation(effect, b'opacity', self)
                    anim_op.setDuration(420)
                    anim_op.setStartValue(1.0)
                    anim_op.setEndValue(0.0)

                    anim_geo = QPropertyAnimation(cell, b'geometry', self)
                    anim_geo.setDuration(420)
                    anim_geo.setStartValue(cell.geometry())
                    # 放大到 140% 并保持中心位置
                    g = cell.geometry()
                    new_w = int(g.width() * 1.4)
                    new_h = int(g.height() * 1.4)
                    new_x = g.x() - (new_w - g.width()) // 2
                    new_y = g.y() - (new_h - g.height()) // 2
                    anim_geo.setEndValue(QRect(new_x, new_y, new_w, new_h))

                    group = QParallelAnimationGroup(self)
                    group.addAnimation(anim_op)
                    group.addAnimation(anim_geo)

                    def on_finished():
                        try:
                            # 移除组合数据并保存
                            for j, a in enumerate(list(self.apps)):
                                if a is app:
                                    del self.apps[j]
                                    break
                            try:
                                self.save_config()
                            except Exception:
                                pass
                            # 重建网格
                            self.rebuild_app_grid(self.search.text() if hasattr(self, 'search') else '')
                        except Exception:
                            pass

                    group.finished.connect(on_finished)
                    group.start()
                    self._anims.append(group)
                    return
                except Exception:
                    pass

            # 如果没有 cell（不可见），直接移除并保存
            for j, a in enumerate(list(self.apps)):
                if a is app:
                    del self.apps[j]
                    break
            try:
                self.save_config()
            except Exception:
                pass
            self.rebuild_app_grid(self.search.text() if hasattr(self, 'search') else '')
        except Exception:
            pass

    def rename_app(self, app):
        old_name = app.get('name','')
        new_name, ok = QInputDialog.getText(self, '重命名应用', '新的显示名称：', text=old_name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.information(self, '提示', '名称不能为空。')
            return
        app['name'] = new_name
        try:
            self.save_config()
        except Exception as e:
            QMessageBox.warning(self, '保存失败', f'无法保存配置: {e}')
        self.rebuild_app_grid(self.search.text() if hasattr(self, 'search') else '')

    def delete_app(self, app):
        reply = QMessageBox.question(self, '删除应用', f"确认要删除 '{app.get('name','')}' 吗？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            # remove by matching path/name
            for i, a in enumerate(self.apps):
                if a.get('path') == app.get('path') and a.get('name') == app.get('name'):
                    del self.apps[i]
                    break
            self.save_config()
        except Exception as e:
            QMessageBox.warning(self, '删除失败', f'无法删除应用: {e}')
        self.rebuild_app_grid(self.search.text() if hasattr(self, 'search') else '')

    def eventFilter(self, source, event):
        # 仅响应顶栏区域的拖动事件来自定义拖动窗口
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if source and isinstance(source, QWidget):
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
        elif event.type() == QEvent.Type.MouseMove and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            return True
        elif event.type() == QEvent.Type.MouseButtonRelease:
            self._drag_pos = None
            return True
        return super().eventFilter(source, event)

    def add_app_via_dialog(self):
        """支持添加三种类型：可执行文件、文件夹、网页 URL。"""
        # 先让用户选择类型
        dlg = QMessageBox(self)
        dlg.setWindowTitle('添加应用')
        dlg.setText('选择要添加的类型：')
        btn_exec = dlg.addButton('可执行文件 (.exe)', QMessageBox.ButtonRole.ActionRole)
        btn_folder = dlg.addButton('文件夹', QMessageBox.ButtonRole.ActionRole)
        btn_url = dlg.addButton('网页 (URL)', QMessageBox.ButtonRole.ActionRole)
        btn_cancel = dlg.addButton(QMessageBox.StandardButton.Cancel)
        dlg.exec()

        clicked = dlg.clickedButton()
        if clicked == btn_cancel or clicked is None:
            return

        if clicked == btn_exec:
            start_dir = os.getenv('ProgramFiles', os.path.expanduser('~'))
            path, _ = QFileDialog.getOpenFileName(self, '选择可执行文件', start_dir, '可执行文件 (*.exe);;所有文件 (*)')
            if not path:
                return
            name = os.path.splitext(os.path.basename(path))[0]
            icon_val = path
            key = os.path.abspath(path)

        elif clicked == btn_folder:
            start_dir = os.path.expanduser('~')
            path = QFileDialog.getExistingDirectory(self, '选择文件夹', start_dir)
            if not path:
                return
            name = os.path.basename(os.path.normpath(path)) or path
            icon_val = path
            key = os.path.abspath(path)

        else:  # 网页
            url, ok = QInputDialog.getText(self, '添加网页', '请输入网页地址 (以 http:// 或 https:// 开头)：')
            if not ok or not url:
                return
            url = url.strip()
            # 自动补全 scheme
            if not urllib.parse.urlparse(url).scheme:
                url = 'http://' + url
            name_input, ok2 = QInputDialog.getText(self, '网页名称', '为该网页输入显示名称（可留空）:')
            if ok2 and name_input:
                name = name_input.strip()
            else:
                # 从域名生成默认名称
                try:
                    parsed = urllib.parse.urlparse(url)
                    name = parsed.netloc or url
                except Exception:
                    name = url
            icon_val = key = url.rstrip('/')

        # 检查重复（对文件/文件夹使用绝对路径，对 URL 使用规范化 URL）
        for a in self.apps:
            existing = a.get('path') or ''
            if existing:
                if existing == key or os.path.abspath(existing) == key:
                    QMessageBox.information(self, '提示', '该应用已在列表中。')
                    return

        new_app = {"name": name, "path": path if clicked != btn_url else key, "icon": icon_val}
        self.apps.append(new_app)
        try:
            self.save_config()
        except Exception as e:
            QMessageBox.warning(self, '保存失败', f'无法保存配置: {e}')
        self.rebuild_app_grid(self.search.text() if hasattr(self, 'search') else '')

    def save_config(self):
        """将当前 self.apps 写回 apps.json（覆盖）。"""
        config_path = os.path.join(os.path.dirname(__file__), 'apps.json')
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self.apps, f, ensure_ascii=False, indent=4)
        except Exception:
            raise

    def reorder_apps(self, source_path, target_path=None):
        """把 source_path 对应的 app 移动到 target_path 所在位置之前；如果 target_path 为 None 则移到末尾。"""
        try:
            if not source_path:
                return
            src_idx = None
            for i, a in enumerate(self.apps):
                if a.get('path') == source_path:
                    src_idx = i
                    break
            if src_idx is None:
                return
            # 找目标索引
            dst_idx = None
            if target_path:
                for j, a in enumerate(self.apps):
                    if a.get('path') == target_path:
                        dst_idx = j
                        break
            # 如果目标未找到则移动到末尾
            if dst_idx is None:
                dst_idx = len(self.apps) - 1
            # 当源在目标之后且我们要插入在目标之前，需要先移除源再插入
            app_obj = self.apps.pop(src_idx)
            # 如果源在目标之前且我们 pop 了前面的元素，目标索引会减一
            if src_idx < dst_idx:
                dst_idx -= 1
            # 插入到目标位置之前（即在 dst_idx 位置插入）
            self.apps.insert(dst_idx, app_obj)
            # 保存并刷新界面
            try:
                self.save_config()
            except Exception:
                pass
            self.rebuild_app_grid(self.search.text() if hasattr(self, 'search') else '')
        except Exception:
            pass

    # --- Drag / Reorder helpers for realtime drag-and-animate behavior ---
    def start_drag(self, cell, press_global_pos):
        """开始拖拽：记录初始状态，将当前单元置顶。"""
        try:
            self._dragging_cell = cell
            cell.raise_() # 让被拖拽的物体浮在最上层
            # 重置磁吸状态
            self._magnet_target = None
            self._magnet_offset = QPoint(0, 0)
            self._magnet_orig_geom = None
            self._magnet_candidate = None
            self._magnet_candidate_snap = None
            self._magnet_timer.stop()
            
            # 计算鼠标点击位置相对于 Cell 左上角的偏移，防止拖拽时图标瞬移
            content_pos = self._content_widget.mapFromGlobal(press_global_pos)
            self._dragging_offset = content_pos - cell.pos()
            
            # 记录当前被拖拽物体在列表中的索引，用于检测位置变化
            if cell in self.cells:
                self._drag_current_idx = self.cells.index(cell)
            else:
                self._drag_current_idx = -1
                
        except Exception as e:
            print(f"Start drag error: {e}")
            self._dragging_cell = None

    def update_drag(self, cell, global_pos):
        """拖拽中：核心物理引擎逻辑。
        实现“实体碰撞”效果：当拖拽物入侵其他物体领地时，其他物体自动弹开。
        """
        try:
            if self._dragging_cell is not cell or not self.cells:
                return

            # 1. 移动被拖拽的图标跟随鼠标
            content_pos = self._content_widget.mapFromGlobal(global_pos)
            new_top_left = content_pos - self._dragging_offset
            
            # 限制拖拽范围不跑出容器太多
            cw = max(-20, min(new_top_left.x(), self._content_widget.width() - cell.width() + 20))
            ch = max(-20, min(new_top_left.y(), self._content_widget.height() - cell.height() + 20))
            cell.move(QPoint(cw, ch))

            # 已有磁吸目标：直接带动目标一起移动，暂不触发布局重排
            if self._magnet_target:
                follow_pos = QPoint(cw, ch) + self._magnet_offset
                self._magnet_target.move(follow_pos)
                return
            
            # 磁吸预检测：靠近后开启计时，达到延时才真正吸附
            g1 = cell.geometry()
            nearest = None
            nearest_snap = None
            for other in self.cells:
                if other is cell:
                    continue
                g2 = other.geometry()
                overlap_y = not (g1.bottom() < g2.top() or g2.bottom() < g1.top())
                dx = min(abs(g1.right() - g2.left()), abs(g2.right() - g1.left()))

                snap_pos = None
                # 仅在左右边缘接近且垂直方向有重叠时才允许磁吸，避免上下误吸附
                if overlap_y and dx <= self._magnet_threshold:
                    snap_x = g2.left() - g1.width() if g1.center().x() < g2.center().x() else g2.right()
                    snap_pos = QPoint(snap_x, g1.y())

                if snap_pos is not None:
                    nearest = other
                    nearest_snap = snap_pos
                    break

            if nearest is None:
                # 离开吸附范围，取消候选
                self._magnet_candidate = None
                self._magnet_candidate_snap = None
                self._magnet_timer.stop()
                self._clear_magnet_style_on_all()
            else:
                # 在范围内但需要停留一段时间才吸附
                if self._magnet_candidate is not nearest or self._magnet_candidate_snap != nearest_snap:
                    self._magnet_candidate = nearest
                    self._magnet_candidate_snap = nearest_snap
                    self._magnet_timer.start(self._magnet_delay_ms)
                    # 预览样式：当前拖拽物与目标高亮
                    self._clear_magnet_style_on_all()
                    self._apply_magnet_style(cell, True, strong=False)
                    self._apply_magnet_style(nearest, True, strong=False)
                else:
                    # 若正在等待，保持计时
                    if not self._magnet_timer.isActive():
                        self._magnet_timer.start(self._magnet_delay_ms)

            # 2. 计算当前拖拽物中心点所在的“网格索引” (Grid Index)
            # 这模拟了物理世界的占位逻辑
            center_x = cw + cell.width() // 2
            center_y = ch + cell.height() // 2
            
            margin = getattr(self, 'grid_margin', 12)
            spacing = getattr(self, 'grid_spacing', 16)
            btn_size = getattr(self, 'btn_size', 112)
            # 计算行高 (按钮高度 + 文字高度)
            # 简单估算：btn_size + 30 (文字预留)
            # 更精确的做法是获取 cell 的实际高度，这里取 cell.height() 近似
            grid_h = cell.height() + spacing
            grid_w = btn_size + spacing

            # 逆向计算行列
            col = max(0, int((center_x - margin) / grid_w))
            row = max(0, int((center_y - margin) / grid_h))
            
            # 计算当前布局有多少列
            avail_w = max(200, self._content_widget.width())
            cols_count = max(1, avail_w // grid_w)
            
            # 算出目标索引 (Target Index)
            target_idx = row * cols_count + col
            
            # 索引边界限制
            target_idx = max(0, min(target_idx, len(self.cells) - 1))

            # 3. 碰撞检测与重排 (Collision & Reorder)
            # 如果计算出的目标位置不是当前位置，说明发生了“碰撞/挤压”
            if target_idx != self._drag_current_idx:
                
                # 在内存列表中移动元素：把拖拽物从旧位置拔出来，插到新位置
                # 这就像挤公交车，一个人挤进去，后面所有人往后挪
                cell_obj = self.cells.pop(self._drag_current_idx)
                self.cells.insert(target_idx, cell_obj)
                
                # 更新当前索引
                self._drag_current_idx = target_idx
                
                # 4. 触发物理动画：让除了被拖拽物之外的所有图标归位
                for i, c in enumerate(self.cells):
                    if c is self._dragging_cell:
                        continue # 被拖拽物由鼠标控制，不参与自动归位
                    
                    # 获取该索引原本应该在的物理坐标
                    if i < len(self.grid_positions):
                        target_pos = self.grid_positions[i]
                    else:
                        continue

                    # 只有位置不对时才通过动画移动
                    if c.pos() != target_pos:
                        # 创建动画
                        anim = QPropertyAnimation(c, b'pos', self)
                        anim.setDuration(300) # 300ms 动画时长
                        
                        # 使用 OutBack 曲线模拟磁铁斥力/弹簧效果
                        # 这种曲线会先冲过头一点点再弹回来，非常有物理感
                        anim.setEasingCurve(QEasingCurve.Type.OutBack) 
                        
                        anim.setStartValue(c.pos())
                        anim.setEndValue(target_pos)
                        anim.start()
                        
                        # 存入动画池防止被垃圾回收
                        self._anims.append(anim)
                
                # 清理已完成的动画
                self._anims = [a for a in self._anims if a.state() == QPropertyAnimation.Running]

        except Exception as e:
            # print(e) 
            pass

    def _confirm_magnet_candidate(self):
        """计时结束后确认磁吸，避免误触发。"""
        try:
            cell = self._dragging_cell
            other = self._magnet_candidate
            snap_pos = self._magnet_candidate_snap
            if not cell or not other or snap_pos is None:
                return

            # 再次验证仍在阈值内
            g1 = cell.geometry()
            g2 = other.geometry()
            overlap_y = not (g1.bottom() < g2.top() or g2.bottom() < g1.top())
            dx = min(abs(g1.right() - g2.left()), abs(g2.right() - g1.left()))
            if not (overlap_y and dx <= self._magnet_threshold):
                return

            # 执行吸附
            cell.move(snap_pos)
            self._magnet_target = other
            self._magnet_orig_geom = other.geometry()
            self._magnet_offset = other.pos() - cell.pos()
            other.move(cell.pos() + self._magnet_offset)
            # 确认后加重高亮，提示已吸附（无需脉冲避免抖动）
            self._apply_magnet_style(cell, True, strong=True)
            self._apply_magnet_style(other, True, strong=True)
        finally:
            self._magnet_candidate = None
            self._magnet_candidate_snap = None
            self._magnet_timer.stop()

    def end_drag(self, cell, global_pos):
        """结束拖拽：吸附归位并保存数据。"""
        try:
            if self._dragging_cell is not cell:
                return
            
            # 如果有磁吸目标，则把两者作为一组一起归位并更新顺序
            if self._magnet_target:
                # 生成组合应用 C
                app_a = cell.app
                app_b = self._magnet_target.app
                members = self._flatten_combo_apps(app_a) + self._flatten_combo_apps(app_b)
                # 组合名称：前两个名称 + “等N项”
                names = []
                for m in members[:2]:
                    if isinstance(m, dict):
                        names.append(m.get('name',''))
                    else:
                        names.append(str(m))
                if len(members) > 2:
                    combo_name = f"{' & '.join(names)} 等{len(members)}项"
                else:
                    combo_name = ' & '.join(names) if names else '组合'

                combo_app = {
                    'name': combo_name or '组合',
                    'combo': members,
                    'icon': 'combo'
                }

                # 按落点索引插入新组合
                margin = getattr(self, 'grid_margin', 12)
                spacing = getattr(self, 'grid_spacing', 16)
                btn_size = getattr(self, 'btn_size', 112)
                grid_h = cell.height() + spacing
                grid_w = btn_size + spacing
                center_x = cell.x() + cell.width() // 2
                center_y = cell.y() + cell.height() // 2
                col = max(0, int((center_x - margin) / grid_w))
                row = max(0, int((center_y - margin) / grid_h))
                avail_w = max(200, self._content_widget.width())
                cols_count = max(1, avail_w // grid_w)
                target_idx = row * cols_count + col
                target_idx = max(0, min(target_idx, len(self.cells) - 1))

                # 重建 apps：保留 A/B 原有按钮，再额外插入组合
                new_apps = [c.app for c in self.cells]
                target_idx = min(target_idx, len(new_apps))
                new_apps.insert(target_idx, combo_app)
                self.apps = new_apps
                self.save_config()

                # 重置磁吸状态并重建网格以生成组合按钮
                self._magnet_target = None
                self._magnet_offset = QPoint(0, 0)
                self._magnet_orig_geom = None
                self._magnet_candidate = None
                self._magnet_candidate_snap = None
                self._magnet_timer.stop()
                self.rebuild_app_grid(self.search.text() if hasattr(self, 'search') else '')
                return
            
            # 1. 最终吸附动画 (Snap to Grid)
            # 找到当前它在列表中的位置对应的物理坐标
            final_idx = -1
            if cell in self.cells:
                final_idx = self.cells.index(cell)
            
            if final_idx != -1 and final_idx < len(self.grid_positions):
                dest = self.grid_positions[final_idx]
                
                anim = QPropertyAnimation(cell, b'pos', self)
                anim.setDuration(400)
                # 结束时也用回弹效果，像磁铁吸附
                anim.setEasingCurve(QEasingCurve.Type.OutBack)
                anim.setStartValue(cell.pos())
                anim.setEndValue(dest)
                anim.start()
                self._anims.append(anim)
            
            # 2. 同步数据结构 (self.apps) 并保存到文件
            # 因为 self.cells 的顺序已经变了，我们需要根据 cell.app 更新 self.apps
            new_apps_list = [c.app for c in self.cells]
            self.apps = new_apps_list
            self.save_config()
            
        except Exception as e:
            print(f"End drag error: {e}")
        finally:
            self._dragging_cell = None
            self._drag_current_idx = -1
            self._magnet_candidate = None
            self._magnet_candidate_snap = None
            self._magnet_timer.stop()

    def _on_icon_loaded(self, path, icon):
        # 缓存并更新已注册的按钮
        try:
            self.icon_cache[path] = icon
            btns = self.path_buttons.get(path, [])
            for btn in btns:
                if not icon.isNull():
                    btn.setIcon(icon)
                    btn.setIconSize(QSize(int(btn.width()*0.6), int(btn.height()*0.6)))
                    btn.setText('')
                else:
                    # 如果仍为空，显示首字母占位
                    if btn.toolTip():
                        btn.setText(btn.toolTip()[0])
            # 清理加载集合
            if path in self.loading_set:
                self.loading_set.remove(path)
        except Exception:
            pass


class IconLoader(QThread):
    icon_loaded = pyqtSignal(str, QIcon)

    def __init__(self, path):
        super().__init__()
        self.path = path

    def run(self):
        try:
            if isinstance(self.path, str) and self.path.lower().startswith(('http://', 'https://')):
                # URL -> 尝试从磁盘缓存加载
                cache_dir = os.path.join(os.path.dirname(__file__), 'icon_cache')
                h = hashlib.sha1(self.path.encode('utf-8')).hexdigest()
                # 查找已有文件
                found = None
                if os.path.isdir(cache_dir):
                    for fn in os.listdir(cache_dir):
                        if fn.startswith(h):
                            found = os.path.join(cache_dir, fn)
                            break
                if found and os.path.exists(found):
                    pix = QPixmap(found)
                    if not pix.isNull():
                        icon = QIcon(pix)
                    else:
                        icon = QIcon()
                else:
                    data = fetch_favicon_bytes(self.path)
                    if data:
                        fpath = save_icon_bytes_to_cache(self.path, data)
                        if fpath:
                            pix = QPixmap()
                            pix.load(fpath)
                            if not pix.isNull():
                                icon = QIcon(pix)
                            else:
                                icon = QIcon()
                        else:
                            # try load from bytes directly
                            pix = QPixmap()
                            if pix.loadFromData(data):
                                icon = QIcon(pix)
                            else:
                                icon = QIcon()
                    else:
                        icon = QIcon()
            else:
                icon = extract_qicon_from_file(self.path)
        except Exception:
            icon = QIcon()

        # emit even if null to allow fallback handling
        try:
            self.icon_loaded.emit(self.path, icon)
        except Exception:
            pass


if __name__ == '__main__':
    # C语言里的 main 函数入口
    app = QApplication(sys.argv)
    ball = FloatingBall()
    sys.exit(app.exec())