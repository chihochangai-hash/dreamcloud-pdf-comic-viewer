# 變更紀錄:
# 2026-10-27 - 應用程式更名為 Dreamcloud PDF Comic Viewer。
# 2026-10-18 - 介面：底部懸浮工具列與調整面板，游標離開後自動隱藏，
#              移到視窗底部再叫出。閱讀區改為全幅暖色底。
# 2026-10-18 - 效能與畫質：依視窗實際像素渲染（不再固定 2.5/3 倍）、HiDPI 對齊、
#              高畫質/雙立方改為超取樣後高品質縮小、去網紋改為盒式縮小、
#              預快取不再卡住介面、放大鏡改為對原始頁面局部重繪。
# 2026-03-20 14:19 - 程式碼審查優化：修復全螢幕工具列 Bug、merge_images 改 RGB 省記憶體、
#                    預快取遵守 LRU 上限、方向切換不再清快取、裸 except 改 except Exception。
# 2026-03-07 14:17 - 效率及圖像品質優化：防抖渲染、LRU 快取淘汰、提升預設 DPI、
#                    LANCZOS 縮放、改進自動色階與摩爾紋處理、減少不必要的格式轉換。

import sys
import os
import json
import shutil
from collections import OrderedDict
import fitz  # PyMuPDF
from PIL import Image, ImageEnhance, ImageOps, ImageFilter

from PyQt6.QtWidgets import (QApplication, QMainWindow, QLabel, QFileDialog,
                             QScrollArea, QSlider, QWidget,
                             QHBoxLayout, QPushButton, QMessageBox,
                             QVBoxLayout, QGridLayout)
from PyQt6.QtGui import (QPixmap, QImage, QColor, QPainter, QShortcut,
                         QKeySequence, QIcon, QFont, QPen, QPainterPath, QCursor)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRect, QRectF, QEvent, QTimer

APP_NAME = "Dreamcloud PDF Comic Viewer"


# ==========================================
# 路徑與設定檔處理 (v32: 強化容錯版)
# ==========================================
def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


CONFIG_FILE = os.path.join(get_base_path(), "comic_settings.json")


def load_settings():
    # 1. 定義標準預設值
    default_settings = {
        "history": {},
        "rtl_mode": True,
        "high_dpi": False,
        "auto_contrast": True,  # 預設開啟自動色階體驗較好
        "grayscale": False,
        "invert": False,
        "moire_reduction": False,
        "bicubic": False,
        "sharpness": 1.0,
        "contrast": 1.0,
        "brightness": 1.0,
        "magnifier_factor": 2.5,
        "window_w": 1300,
        "window_h": 850,
        "is_maximized": False,
        "single_page": False,
        "rotation": 0
    }

    if not os.path.exists(CONFIG_FILE):
        return default_settings

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)

            # 2. 嚴格驗證每一個數值，防止參數異常導致崩潰
            # 如果讀到的數值型態不對，就保持預設值
            for key, val in default_settings.items():
                if key in saved:
                    # 型態檢查
                    if isinstance(val, bool) and isinstance(saved[key], bool):
                        default_settings[key] = saved[key]
                    elif isinstance(val, (int, float)) and isinstance(saved[key], (int, float)):
                        default_settings[key] = saved[key]
                    elif isinstance(val, dict) and isinstance(saved[key], dict):
                        default_settings[key] = saved[key]

            # 3. 特殊邏輯檢查
            # 視窗太小或太大都重置
            w = default_settings["window_w"]
            h = default_settings["window_h"]
            if w < 400 or h < 300 or w > 10000 or h > 10000:
                default_settings["window_w"] = 1300
                default_settings["window_h"] = 850
                default_settings["is_maximized"] = False

            # 歷史紀錄過多清理
            history = default_settings.get("history", {})
            if isinstance(history, dict) and len(history) > 50:
                default_settings["history"] = dict(list(history.items())[-50:]) # type: ignore

    except Exception as e:
        # 4. 如果 JSON 格式損壞 (例如斷尾)，自動重置
        print(f"設定檔損壞，正在重置: {e}")
        try:
            # 備份壞掉的檔以便查驗 (可選)
            shutil.copy(CONFIG_FILE, CONFIG_FILE + ".bak")
            os.remove(CONFIG_FILE)
        except Exception:
            pass
        return default_settings  # 回傳預設值讓程式繼續執行

    return default_settings


def save_settings(data):
    try:
        # 寫入暫存檔再更名，防止寫入中斷導致檔案損毀
        temp_file = CONFIG_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        if os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)
        os.rename(temp_file, CONFIG_FILE)
    except Exception:
        pass


# ==========================================
# 圖示載入器
# ==========================================
def load_app_icon():
    """載入 PDF_icon.ico 作為應用程式圖示"""
    icon_path = os.path.join(get_base_path(), "PDF_icon.ico")
    if os.path.exists(icon_path):
        return QIcon(icon_path)
    # Fallback: 嘗試同目錄下的 .png
    png_path = os.path.join(get_base_path(), "PDF_icon.png")
    if os.path.exists(png_path):
        return QIcon(png_path)
    # 最後 fallback: 使用系統預設圖示
    return QIcon()


# ==========================================
# 樣式表
# ==========================================
# ==========================================
# 樣式表 (Modern Rounded)
# ==========================================
MODERN_STYLE = """
QMainWindow { background-color: #100e0c; }
QWidget#Canvas { background-color: #14110e; }
QWidget#Dock { background: transparent; }
QWidget#PagePill {
    background-color: #1c1712;
    border: 1px solid #3d3428;
    border-radius: 24px;
}
QWidget#AdjustCard {
    background-color: #1c1712;
    border: 1px solid #3d3428;
    border-radius: 16px;
}
QMainWindow QLabel {
    color: #f3ead8;
    font-family: "Microsoft JhengHei UI", "Microsoft YaHei UI", "Segoe UI";
    font-size: 13px;
}
QLabel#PageMain {
    color: #f3ead8;
    font-size: 15px;
    font-weight: 700;
}
QLabel#PageSub { color: #b7aa96; font-size: 11px; font-weight: 500; }
QLabel#CardTitle { color: #e7c27a; font-size: 13px; font-weight: 700; }
QLabel#CardSection { color: #b7aa96; font-size: 11px; font-weight: 700; }
QLabel#SliderName { color: #d9ccb8; font-size: 12px; }
QLabel#ValLabel {
    color: #e7c27a;
    font-weight: 700;
    font-family: Consolas, "Microsoft JhengHei UI", monospace;
    font-size: 12px;
}
QLabel#PageView { color: #8a7d6b; font-size: 22px; background: transparent; }
QPushButton#DockBtn, QPushButton#NavBtn, QPushButton#Chip, QPushButton#GhostBtn {
    font-family: "Microsoft JhengHei UI", "Microsoft YaHei UI", "Segoe UI";
    font-weight: 600;
}
QPushButton#DockBtn {
    background-color: #1c1712;
    color: #f3ead8;
    border: 1px solid #3d3428;
    border-radius: 18px;
    padding: 0 14px;
    min-height: 36px;
    font-size: 13px;
}
QPushButton#DockBtn:hover { background-color: #2a241c; }
QPushButton#DockBtn:checked, QPushButton#DockBtn[active="true"] {
    background-color: #e7c27a;
    color: #2a2114;
    border-color: #e7c27a;
}
QPushButton#DockBtn:checked:hover, QPushButton#DockBtn[active="true"]:hover { background-color: #f0d08a; }
QPushButton#NavBtn {
    background-color: #2a241c;
    color: #f3ead8;
    border: none;
    border-radius: 18px;
    font-size: 18px;
    min-width: 36px;
    max-width: 36px;
    min-height: 36px;
    max-height: 36px;
}
QPushButton#NavBtn:hover { background-color: #3a3228; }
QPushButton#Chip {
    background-color: #2a241c;
    color: #f3ead8;
    border: none;
    border-radius: 8px;
    padding: 6px 8px;
    min-height: 30px;
    font-size: 12px;
}
QPushButton#Chip:hover { background-color: #3a3228; }
QPushButton#Chip:checked { background-color: #e7c27a; color: #2a2114; }
QPushButton#Chip:checked:hover { background-color: #f0d08a; }
QPushButton#Chip:disabled { color: #6d6458; background-color: #17130f; }
QPushButton#GhostBtn {
    background-color: transparent;
    color: #f3ead8;
    border: 1px solid #3d3428;
    border-radius: 10px;
    padding: 7px 10px;
    font-size: 13px;
}
QPushButton#GhostBtn:hover { background-color: #2a241c; }
QMainWindow QSlider::groove:horizontal { height: 4px; background: #3a3228; border-radius: 2px; }
QMainWindow QSlider::handle:horizontal {
    background: #fff8ea;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QMainWindow QSlider::sub-page:horizontal { background: #e7c27a; border-radius: 2px; }
QScrollArea { border: none; background-color: #14110e; }
"""


# ==========================================
# 放大鏡 & Label
# ==========================================
class MagnifierOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent) # type: ignore
        self.lens_size = 375
        self.setFixedSize(self.lens_size, self.lens_size)
        self.pixmap = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def update_content(self, pixmap):
        self.pixmap = pixmap;
        self.update()

    def paintEvent(self, event):
        if not self.pixmap: return
        painter = QPainter(self);
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        path = QPainterPath();
        path.addEllipse(0, 0, self.lens_size, self.lens_size)
        painter.setClipPath(path)
        target_rect = QRect(0, 0, self.lens_size, self.lens_size)
        painter.drawPixmap(target_rect, self.pixmap)
        painter.setClipping(False)
        pen = QPen(QColor("#e7c27a"));
        pen.setWidth(4);
        painter.setPen(pen)
        painter.drawEllipse(2, 2, self.lens_size - 4, self.lens_size - 4)


class InteractiveImageLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent) # type: ignore
        self.main_window = None
        self.magnifier = MagnifierOverlay(self)
        self.high_res_pixmap = None
        self.factor = 2.5
        self._lens_key = None
        self._lens_pix = None

    def set_high_res_pixmap(self, px):
        self.high_res_pixmap = px

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.update_magnifier(event.pos());
            self.magnifier.show()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.magnifier.isVisible(): self.update_magnifier(event.pos())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.magnifier.hide();
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.main_window and hasattr(self.main_window, 'toggle_fullscreen'): self.main_window.toggle_fullscreen() # type: ignore

    def update_magnifier(self, mouse_pos):
        hr = self.high_res_pixmap
        pm = self.pixmap()
        if not hr or not pm: return
        disp_w = pm.width()
        disp_h = pm.height()
        offset_x = (self.width() - disp_w) / 2
        offset_y = (self.height() - disp_h) / 2
        rel_x = mouse_pos.x() - offset_x
        rel_y = mouse_pos.y() - offset_y
        if rel_x < 0 or rel_x > disp_w or rel_y < 0 or rel_y > disp_h:
            self.magnifier.hide()
            return
        src_w = hr.width()
        src_h = hr.height()
        src_cx = rel_x * (src_w / disp_w)
        src_cy = rel_y * (src_h / disp_h)
        crop_w = self.magnifier.lens_size / self.factor
        crop_h = self.magnifier.lens_size / self.factor
        content = self._lens_content(src_cx, src_cy, crop_w, crop_h, hr)
        self.magnifier.update_content(content)
        self.magnifier.move(int(mouse_pos.x() - self.magnifier.lens_size // 2),
                            int(mouse_pos.y() - self.magnifier.lens_size // 2))

    def _lens_content(self, src_cx, src_cy, crop_w, crop_h, hr):
        key = self._lens_key
        if (
            self._lens_pix is not None
            and key is not None
            and abs(src_cx - key[0]) < 3.0
            and abs(src_cy - key[1]) < 3.0
            and abs(crop_w - key[2]) < 0.1
            and abs(self.factor - key[3]) < 0.01
        ):
            return self._lens_pix
        sharp = None
        if self.main_window is not None and hasattr(self.main_window, "render_lens"):
            sharp = self.main_window.render_lens(src_cx, src_cy, crop_w, crop_h, self.magnifier.lens_size)
        if sharp is None:
            src_rect = QRect(int(src_cx - crop_w / 2), int(src_cy - crop_h / 2), int(crop_w), int(crop_h))
            sharp = hr.copy(src_rect)
        self._lens_key = (src_cx, src_cy, crop_w, self.factor)
        self._lens_pix = sharp
        return sharp


# ==========================================
# 影像處理
# 快取圖的長邊對齊視窗實際像素，而不是固定把 PDF 放大 2.5/3 倍。
# 掃描檔若整頁只有一張圖，渲染不超過原圖像素，避免先放大再縮小。
# ==========================================
MAX_RENDER_LONG = 5120


def _raster_native_long(page):
    """整頁只有一張貼滿的點陣圖時，回傳它的長邊像素。否則回傳 None。"""
    try:
        infos = page.get_image_info()
    except Exception:
        return None
    if len(infos) != 1:
        return None
    info = infos[0]
    bbox = info.get("bbox")
    if not bbox:
        return None
    image_rect = fitz.Rect(bbox)
    bases = [page.rect]
    if page.rotation:
        bases.append(page.cropbox)
    covered = False
    for base in bases:
        area = base.width * base.height
        if area > 0 and image_rect.width * image_rect.height >= area * 0.82:
            covered = True
            break
    if not covered:
        return None
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    if width < 2 or height < 2:
        return None
    return max(width, height)


def _quality_plan(settings):
    """回傳 (超取樣倍率, 縮小濾鏡, 是否為去網紋)。"""
    if settings.get("moire_reduction"):
        return 2.0, Image.Resampling.BOX, True
    if settings.get("bicubic"):
        return 2.0, Image.Resampling.LANCZOS, False
    if settings.get("high_dpi"):
        return 1.5, Image.Resampling.LANCZOS, False
    return 1.0, None, False


def _needs_adjust(settings):
    if settings.get("grayscale") or settings.get("invert") or settings.get("auto_contrast"):
        return True
    if settings.get("moire_reduction"):
        return True
    for key in ("brightness", "contrast", "sharpness"):
        if abs(float(settings.get(key, 1.0)) - 1.0) > 1e-3:
            return True
    return False


def _visual_page_size(page, rotation):
    width, height = float(page.rect.width), float(page.rect.height)
    if int(rotation) % 180 == 90:
        return height, width
    return width, height


def _qimage_from_pixmap(pix):
    if pix.n != 3 or pix.alpha:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    qimg = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
    return qimg.copy()


def _pil_from_pixmap(pix):
    if pix.n != 3 or pix.alpha:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    raw = bytes(pix.samples)
    if pix.stride == pix.width * 3:
        return Image.frombytes("RGB", (pix.width, pix.height), raw)
    return Image.frombytes("RGB", (pix.width, pix.height), raw, "raw", "RGB", pix.stride, 1)


def _downscale(img, target_long, resample):
    long_edge = max(img.width, img.height)
    if long_edge <= target_long * 1.03:
        return img
    ratio = target_long / float(long_edge)
    size = (max(1, int(round(img.width * ratio))), max(1, int(round(img.height * ratio))))
    return img.resize(size, resample=resample)


def _apply_adjustments(img, settings):
    if settings.get("invert"):
        img = ImageOps.invert(img)
    if settings.get("grayscale"):
        img = ImageOps.grayscale(img).convert("RGB")
    if settings.get("auto_contrast"):
        img = ImageOps.autocontrast(img, cutoff=0.5)
    brightness = float(settings.get("brightness", 1.0))
    contrast = float(settings.get("contrast", 1.0))
    sharpness = float(settings.get("sharpness", 1.0))
    if abs(brightness - 1.0) > 1e-3:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if abs(contrast - 1.0) > 1e-3:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if abs(sharpness - 1.0) > 1e-3:
        img = ImageEnhance.Sharpness(img).enhance(sharpness)
    return img


def _qimage_from_pil(img):
    if img.mode != "RGB":
        img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
    return qimg.copy()


def pixels_to_page_clip(page, user_rotation, render_scale, x0, y0, x1, y1, dst_w, dst_h):
    """把最終快取圖上的像素矩形轉回 page.rect 座標。

    render_scale 是 PyMuPDF 產生點陣圖時用的倍率。若之後有縮小，用 dst 尺寸把像素
    線性對回那張點陣圖的整數邊界。
    """
    matrix = fitz.Matrix(float(render_scale), float(render_scale)).prerotate(int(user_rotation) % 360)
    bounds = (page.rect * matrix).irect
    if bounds.width < 1 or bounds.height < 1 or dst_w < 1 or dst_h < 1:
        return fitz.Rect()
    inverse = ~matrix
    points = []
    for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        px = float(x) * bounds.width / float(dst_w)
        py = float(y) * bounds.height / float(dst_h)
        points.append(fitz.Point(bounds.x0 + px, bounds.y0 + py) * inverse)
    clip = fitz.Rect(
        min(point.x for point in points),
        min(point.y for point in points),
        max(point.x for point in points),
        max(point.y for point in points),
    )
    return clip & page.rect


def process_image_static(doc, page_idx, settings):
    """回傳 (QImage, 渲染倍率) 或 None。渲染倍率是交給 PyMuPDF 的倍率，縮小前的那一次。"""
    if page_idx < 0 or page_idx >= len(doc):
        return None
    try:
        page = doc.load_page(page_idx)
        rotation = int(settings.get("rotation", 0) or 0) % 360
        target = int(settings.get("target_long_px") or 1600)
        target = max(640, min(4096, target))
        supersample, down_filter, moire = _quality_plan(settings)
        native_long = _raster_native_long(page)
        vis_long = max(float(page.rect.width), float(page.rect.height)) or 1.0
        render_long = float(target) * supersample
        if native_long:
            render_long = min(render_long, float(native_long))
        render_long = min(MAX_RENDER_LONG, max(1.0, render_long))
        scale = render_long / vis_long
        matrix = fitz.Matrix(scale, scale).prerotate(rotation)
        pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False, annots=False)
        if pix.width < 1 or pix.height < 1:
            return None

        long_edge = max(pix.width, pix.height)
        needs_down = long_edge > target * 1.03
        if not _needs_adjust(settings) and not needs_down and not moire:
            qimg = _qimage_from_pixmap(pix)
            if qimg is None or qimg.isNull():
                return None
            return qimg, scale

        img = _pil_from_pixmap(pix)
        if needs_down:
            img = _downscale(img, target, down_filter or Image.Resampling.LANCZOS)
        elif moire:
            img = img.filter(ImageFilter.BoxBlur(0.8))
        img = _apply_adjustments(img, settings)
        qimg = _qimage_from_pil(img)
        if qimg is None or qimg.isNull():
            return None
        return qimg, scale
    except Exception:
        return None


class CacheWorker(QThread):
    """用自己的 Document 預渲染，避免和介面執行緒共用同一份 MuPDF 文件。"""

    image_ready = pyqtSignal(int, int, QImage, float)

    def __init__(self, path, queue_indices, settings, gen):
        super().__init__()
        self.path = path
        self.queue = list(queue_indices)
        self.settings = dict(settings)
        self.gen = int(gen)
        self.is_running = True

    def run(self):
        doc = None
        try:
            doc = fitz.open(self.path)
            for idx in self.queue:
                if not self.is_running:
                    break
                try:
                    rendered = process_image_static(doc, idx, self.settings)
                    if rendered and self.is_running:
                        qimg, scale = rendered
                        self.image_ready.emit(self.gen, idx, qimg, scale)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    def stop(self):
        self.is_running = False


# ==========================================
# 主視窗
# ==========================================
class ComicReader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.app_settings = load_settings()  # 這裡讀取設定，如果有錯會自動重置

        self.setWindowTitle(APP_NAME)
        w = self.app_settings.get("window_w", 1300)
        h = self.app_settings.get("window_h", 850)
        self.resize(w, h)

        # 注意：這裡不直接呼叫 showMaximized()，避免衝突

        self.setAcceptDrops(True)
        self.setWindowIcon(load_app_icon())

        self.doc = None;
        self.current_file_path = None;
        self.total_pages = 0
        self.current_base_index = 0;
        self.page_offset = 0;
        self.is_fullscreen = False
        self.page_cache = OrderedDict()  # LRU 快取
        self.page_scale = {}
        self._cache_bytes_n = 0
        self.cache_max_pages = 8
        self.cache_max_bytes = 320 * 1024 * 1024
        self._pinned = set()
        self._native_long = {}
        self.cache_thread = None
        self._precache_gen = 0
        self._precache_again = False
        self.current_final_pixmap = None
        self.spread_map = []
        self._render_bucket = 0
        self._applied_signature = None
        self._compose_epoch = 0
        self._compose_key = None
        self._compose_pix = None
        self._compose_spread = []
        self._present_key = None
        self._present_pix = None

        # 防抖計時器 (設定變更)
        self._setting_timer = QTimer()
        self._setting_timer.setSingleShot(True)
        self._setting_timer.setInterval(300)
        self._setting_timer.timeout.connect(self._apply_setting_change)

        # 防抖計時器 (視窗 resize)
        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(150)
        self._resize_timer.timeout.connect(self._apply_resize)

        self.is_rtl = self.app_settings["rtl_mode"]
        self.high_dpi_mode = self.app_settings["high_dpi"]
        self.auto_contrast_mode = self.app_settings["auto_contrast"]
        self.grayscale_mode = self.app_settings["grayscale"]
        self.invert_mode = self.app_settings["invert"]
        self.moire_mode = self.app_settings["moire_reduction"]
        self.bicubic_mode = self.app_settings["bicubic"]
        self.sharpness_val = self.app_settings["sharpness"]
        self.contrast_val = self.app_settings["contrast"]
        self.brightness_val = self.app_settings["brightness"]
        self.magnifier_factor = self.app_settings.get("magnifier_factor", 2.5)
        self.is_single_page = self.app_settings.get("single_page", False)
        self.rotation = self.app_settings.get("rotation", 0)
        self._adjust_open = False
        self._chrome_introduced = False
        self._page_main = "尚未開啟"
        self._page_sub = "拖入 PDF"
        self._chrome_timer = QTimer(self)
        self._chrome_timer.setSingleShot(True)
        self._chrome_timer.timeout.connect(self._hide_chrome)
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.timeout.connect(self._restore_page_sub)

        self.init_ui()
        self.init_shortcuts()
        self.setMouseTracking(True)
        self.canvas.setMouseTracking(True)
        self.scroll_area.setMouseTracking(True)
        self.scroll_area.viewport().setMouseTracking(True)
        self.image_label.setMouseTracking(True)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._applied_signature = self._settings_signature()

    def _dock_button(self, text, tip, checkable=False):
        btn = QPushButton(text)
        btn.setObjectName("DockBtn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setToolTip(tip)
        btn.setCheckable(checkable)
        return btn

    def _chip_button(self, text, checked, tip):
        btn = QPushButton(text)
        btn.setObjectName("Chip")
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.setToolTip(tip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.toggled.connect(self.on_setting_change)
        return btn

    def _mark_active(self, btn, active):
        if bool(btn.property("active")) == bool(active):
            return
        btn.setProperty("active", bool(active))
        btn.style().unpolish(btn)
        btn.style().polish(btn)
        btn.update()

    def init_ui(self):
        self.setFont(QFont("Microsoft JhengHei UI", 10))
        self.statusBar().hide()

        self.canvas = QWidget()
        self.canvas.setObjectName("Canvas")
        self.canvas.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root = QVBoxLayout(self.canvas)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.viewport().setStyleSheet("background-color: #14110e;")
        self.image_label = InteractiveImageLabel()
        self.image_label.main_window = self
        self.image_label.setObjectName("PageView")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setText("請拖拉 PDF 檔案至此")
        self.scroll_area.setWidget(self.image_label)
        root.addWidget(self.scroll_area)
        self.setCentralWidget(self.canvas)

        self.dock = QWidget(self.canvas)
        self.dock.setObjectName("Dock")
        dock_lay = QHBoxLayout(self.dock)
        dock_lay.setContentsMargins(0, 0, 0, 0)
        dock_lay.setSpacing(8)
        dock_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.btn_open = self._dock_button("開啟", "開啟 PDF")
        self.btn_open.clicked.connect(self.open_file_dialog)
        dock_lay.addWidget(self.btn_open)

        pill = QWidget()
        pill.setObjectName("PagePill")
        pill.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pill_lay = QHBoxLayout(pill)
        pill_lay.setContentsMargins(6, 6, 6, 6)
        pill_lay.setSpacing(4)
        self.btn_prev = QPushButton("‹")
        self.btn_next = QPushButton("›")
        for btn, tip, slot in (
            (self.btn_prev, "上一頁", self.prev_page),
            (self.btn_next, "下一頁", self.next_page),
        ):
            btn.setObjectName("NavBtn")
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(slot)
        text_box = QWidget()
        text_lay = QVBoxLayout(text_box)
        text_lay.setContentsMargins(8, 0, 8, 0)
        text_lay.setSpacing(0)
        self.page_main = QLabel(self._page_main)
        self.page_main.setObjectName("PageMain")
        self.page_main.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_main.setMinimumWidth(96)
        self.page_sub = QLabel(self._page_sub)
        self.page_sub.setObjectName("PageSub")
        self.page_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_lay.addWidget(self.page_main)
        text_lay.addWidget(self.page_sub)
        pill_lay.addWidget(self.btn_prev)
        pill_lay.addWidget(text_box)
        pill_lay.addWidget(self.btn_next)
        dock_lay.addWidget(pill)

        self.btn_spread = self._dock_button("雙頁", "切換單頁 / 雙頁")
        self.btn_spread.clicked.connect(self.toggle_single_page)
        self.btn_dir = self._dock_button("日漫", "日漫由右往左，美漫由左往右")
        self.btn_dir.clicked.connect(self.toggle_direction)
        self.btn_full = self._dock_button("全螢幕", "全螢幕 (F11)，再按一次或 Esc 離開", checkable=True)
        self.btn_full.clicked.connect(self.toggle_fullscreen)
        self.btn_adjust = self._dock_button(
            "調整",
            "畫面調整。游標離開後，工具列和調整面板會自動隱藏；移到視窗底部可再叫出。",
            checkable=True,
        )
        self.btn_adjust.clicked.connect(self._toggle_adjust_panel)
        for btn in (self.btn_spread, self.btn_dir, self.btn_full, self.btn_adjust):
            dock_lay.addWidget(btn)

        self.adjust_card = QWidget(self.canvas)
        self.adjust_card.setObjectName("AdjustCard")
        self.adjust_card.setFixedWidth(292)
        self.adjust_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card = QVBoxLayout(self.adjust_card)
        card.setContentsMargins(14, 12, 14, 12)
        card.setSpacing(8)
        title = QLabel("畫面")
        title.setObjectName("CardTitle")
        card.addWidget(title)

        sec_layout = QLabel("排版")
        sec_layout.setObjectName("CardSection")
        card.addWidget(sec_layout)
        layout_row = QHBoxLayout()
        layout_row.setSpacing(6)
        self.btn_offset = QPushButton("偶數起")
        self.btn_offset.setObjectName("Chip")
        self.btn_offset.setToolTip("雙頁時，封面要單獨一頁還是跟下一頁並排")
        self.btn_offset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_offset.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_offset.clicked.connect(self.toggle_offset)
        self.btn_rot_l = QPushButton("左旋")
        self.btn_rot_r = QPushButton("右旋")
        self.btn_rot_l.setObjectName("Chip")
        self.btn_rot_r.setObjectName("Chip")
        self.btn_rot_l.setToolTip("向左旋轉 90 度 (L)")
        self.btn_rot_r.setToolTip("向右旋轉 90 度 (R)")
        self.btn_rot_l.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_rot_r.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_rot_l.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rot_r.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rot_l.clicked.connect(self.rotate_left)
        self.btn_rot_r.clicked.connect(self.rotate_right)
        layout_row.addWidget(self.btn_offset)
        layout_row.addWidget(self.btn_rot_l)
        layout_row.addWidget(self.btn_rot_r)
        card.addLayout(layout_row)

        sec_q = QLabel("畫質")
        sec_q.setObjectName("CardSection")
        card.addWidget(sec_q)
        self.chk_moire = self._chip_button("去網紋", self.moire_mode, "以兩倍取樣後盒式縮小打散網點，比中值濾波快，也比較不會糊掉線條")
        self.chk_bicubic = self._chip_button("雙立方", self.bicubic_mode, "兩倍超取樣後用 Lanczos 縮小，線條更平滑 (速度較慢)")
        self.chk_high_dpi = self._chip_button("高畫質", self.high_dpi_mode, "提高渲染解析度，並用 Lanczos 縮小，文字與網點更清楚 (速度較慢)")
        self.chk_auto = self._chip_button("自動", self.auto_contrast_mode, "自動分析圖片並調整對比度，增強閱讀體驗")
        self.chk_gray = self._chip_button("黑白", self.grayscale_mode, "將圖片轉換為灰階模式")
        self.chk_night = self._chip_button("夜間", self.invert_mode, "反轉顏色，適合夜間閱讀")
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, chk in enumerate((self.chk_moire, self.chk_bicubic, self.chk_high_dpi, self.chk_auto, self.chk_gray, self.chk_night)):
            grid.addWidget(chk, i // 3, i % 3)
        card.addLayout(grid)

        sec_color = QLabel("色彩")
        sec_color.setObjectName("CardSection")
        card.addWidget(sec_color)

        def create_slider(label, tip, max_v, init_val, factor=10.0, is_zoom=False):
            w = QWidget()
            l = QHBoxLayout()
            l.setContentsMargins(0, 0, 0, 0)
            l.setSpacing(6)
            lbl_n = QLabel(label)
            lbl_n.setObjectName("SliderName")
            lbl_n.setFixedWidth(48)
            lbl_n.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            val_text = f"{init_val:.1f}x" if is_zoom else f"{init_val:.1f}"
            lbl_v = QLabel(val_text)
            lbl_v.setObjectName("ValLabel")
            lbl_v.setFixedWidth(36)
            lbl_v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(int(1.0 * factor) if is_zoom else 0, max_v)
            sl.setValue(int(init_val * factor))
            sl.setToolTip(tip)
            sl.valueChanged.connect(lambda v: lbl_v.setText(f"{v / factor:.1f}x" if is_zoom else f"{v / factor:.1f}"))
            sl.valueChanged.connect(self.on_setting_change)
            l.addWidget(lbl_n)
            l.addWidget(sl, 1)
            l.addWidget(lbl_v)
            w.setLayout(l)
            return sl, w, lbl_v

        self.slider_zoom, w_z, self.lbl_z = create_slider("放大鏡", "按住左鍵時的放大倍率", 50, self.magnifier_factor, factor=10.0, is_zoom=True)
        self.slider_sharp, w_s, self.lbl_s = create_slider("銳利度", "銳利度", 40, self.sharpness_val)
        self.slider_cont, w_c, self.lbl_c = create_slider("對比度", "對比度", 30, self.contrast_val)
        self.slider_brit, w_b, self.lbl_b = create_slider("亮度", "亮度", 30, self.brightness_val)
        card.addWidget(w_z)
        card.addWidget(w_s)
        card.addWidget(w_c)
        card.addWidget(w_b)
        btn_reset = QPushButton("重置")
        btn_reset.setObjectName("GhostBtn")
        btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_reset.clicked.connect(self.reset_adjustments)
        card.addWidget(btn_reset)

        self.adjust_card.hide()
        self._sync_mode_buttons()
        self._place_chrome()

    def load_pdf(self, path):
        try:
            if self.current_file_path: 
                hist = self.app_settings.get("history", {})
                if isinstance(hist, dict):
                    hist[self.current_file_path] = self.current_base_index
                    self.app_settings["history"] = hist
            new_doc = fitz.open(path)
            self._stop_precache(wait=False)
            old_doc = self.doc
            self.doc = new_doc
            if old_doc is not None and old_doc is not new_doc:
                try:
                    old_doc.close()
                except Exception:
                    pass
            self._native_long.clear()
            self.current_file_path = os.path.abspath(path);
            self.total_pages = len(self.doc)
            hist = self.app_settings.get("history", {})
            saved_page = hist.get(self.current_file_path, 0) if isinstance(hist, dict) else 0
            if saved_page >= self.total_pages: saved_page = 0
            self.current_base_index = saved_page;
            self.clear_cache()
            self.setWindowTitle(f"{APP_NAME} - {os.path.basename(path)}")
            self.update_view()
            self._show_chrome(linger_ms=1800)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _settings_signature(self):
        """會改變像素的設定。放大鏡倍率不在這裡，調整它不必重繪整頁。"""
        return (
            self.chk_high_dpi.isChecked(),
            self.chk_auto.isChecked(),
            self.chk_gray.isChecked(),
            self.chk_night.isChecked(),
            self.chk_moire.isChecked(),
            self.chk_bicubic.isChecked(),
            self.slider_sharp.value(),
            self.slider_cont.value(),
            self.slider_brit.value(),
            int(self.rotation) % 360,
        )

    def get_current_settings(self):
        return {
            'high_dpi': self.chk_high_dpi.isChecked(), 'grayscale': self.chk_gray.isChecked(),
            'auto_contrast': self.chk_auto.isChecked(),
            'invert': self.chk_night.isChecked(), 'moire_reduction': self.chk_moire.isChecked(),
            'bicubic': self.chk_bicubic.isChecked(),
            'sharpness': self.slider_sharp.value() / 10.0, 'contrast': self.slider_cont.value() / 10.0,
            'brightness': self.slider_brit.value() / 10.0,
            'rotation': self.rotation,
            'target_long_px': int(self._render_bucket or 1600),
        }

    def on_setting_change(self):
        # 只更新放大鏡倍率 (即時生效，不需重繪)
        self.magnifier_factor = self.slider_zoom.value() / 10.0
        self.image_label.factor = self.magnifier_factor
        self.image_label._lens_key = None
        # 其餘設定透過防抖計時器延遲套用
        self._setting_timer.start()

    def _apply_setting_change(self):
        """防抖後真正套用設定變更"""
        signature = self._settings_signature()
        if signature == self._applied_signature:
            return
        self._applied_signature = signature
        self.high_dpi_mode = self.chk_high_dpi.isChecked()
        self.auto_contrast_mode = self.chk_auto.isChecked()
        self.grayscale_mode = self.chk_gray.isChecked()
        self.invert_mode = self.chk_night.isChecked()
        self.moire_mode = self.chk_moire.isChecked()
        self.bicubic_mode = self.chk_bicubic.isChecked()
        self.sharpness_val = self.slider_sharp.value() / 10.0
        self.contrast_val = self.slider_cont.value() / 10.0
        self.brightness_val = self.slider_brit.value() / 10.0
        self.clear_cache()
        self.update_view()

    def closeEvent(self, event):
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._stop_precache(wait=True)
        self.app_settings["rtl_mode"] = self.is_rtl;
        self.app_settings["high_dpi"] = self.chk_high_dpi.isChecked()
        self.app_settings["auto_contrast"] = self.chk_auto.isChecked();
        self.app_settings["grayscale"] = self.chk_gray.isChecked()
        self.app_settings["invert"] = self.chk_night.isChecked();
        self.app_settings["moire_reduction"] = self.chk_moire.isChecked()
        self.app_settings["bicubic"] = self.chk_bicubic.isChecked()
        self.app_settings["sharpness"] = self.slider_sharp.value() / 10.0;
        self.app_settings["contrast"] = self.slider_cont.value() / 10.0
        self.app_settings["brightness"] = self.slider_brit.value() / 10.0;
        self.app_settings["magnifier_factor"] = self.slider_zoom.value() / 10.0
        self.app_settings["is_maximized"] = self.isMaximized()
        self.app_settings["single_page"] = self.is_single_page
        self.app_settings["rotation"] = self.rotation
        if not self.isMaximized() and not self.isMinimized():
            self.app_settings["window_w"] = self.width();
            self.app_settings["window_h"] = self.height()
        if self.current_file_path: 
            hist = self.app_settings.get("history", {})
            if isinstance(hist, dict):
                hist[self.current_file_path] = self.current_base_index
                self.app_settings["history"] = hist
        save_settings(self.app_settings)
        super().closeEvent(event)

    def _viewport_long_px(self):
        viewport = self.scroll_area.viewport()
        dpr = float(self.devicePixelRatioF() or 1.0)
        width = max(1, int(round(viewport.width() * dpr)))
        height = max(1, int(round(viewport.height() * dpr)))
        if self.is_single_page:
            return max(width, height)
        return max(height, width // 2)

    def _compute_bucket(self):
        need = self._viewport_long_px()
        if self.chk_high_dpi.isChecked():
            need = int(round(need * 1.5))
            floor, cap = 1792, 4096
        else:
            need = int(round(need * 1.15))
            floor, cap = 1280, 3072
        need = max(floor, min(cap, need))
        return int(((need + 255) // 256) * 256)

    def _sync_render_bucket(self):
        bucket = self._compute_bucket()
        if bucket != self._render_bucket:
            self._render_bucket = bucket
            self.clear_cache()

    def update_view(self):
        if not self.doc:
            return
        self._sync_render_bucket()
        idx = self.current_base_index
        if self.is_single_page:
            self._pinned = {idx}
            self._set_page_label(f"{idx + 1}", f"{self.total_pages} 頁")
            img = self.get_qimage(idx)
            key = ("s", idx, self.rotation, self._compose_epoch, self._render_bucket)
            if key == self._compose_key and self._compose_pix is not None:
                self.spread_map = list(self._compose_spread)
                master = self._compose_pix
            else:
                if img:
                    self.spread_map = [self._spread_part(idx, 0, img)]
                    master = QPixmap.fromImage(img)
                else:
                    self.spread_map = []
                    master = None
                self._store_compose(key, master)
            self._present(master)
            self.trigger_precache()
            return

        lower = idx
        if self.page_offset == 0 and idx == 0:
            lower, higher = 0, -1
        else:
            if self.page_offset == 0:
                if lower % 2 != 0:
                    lower -= 1
            else:
                if lower % 2 == 0 and lower > 0:
                    lower -= 1
            higher = lower + 1
        self._pinned = {i for i in (lower, higher) if 0 <= i < self.total_pages}
        img_a = self.get_qimage(lower)
        img_b = self.get_qimage(higher)
        if self.is_rtl:
            img_right, img_left, page_r, page_l = img_a, img_b, lower, higher
        else:
            img_left, img_right, page_l, page_r = img_a, img_b, lower, higher
        if img_left and img_right:
            main = f"{page_l + 1} ⇦ {page_r + 1}" if self.is_rtl else f"{page_l + 1} ⇨ {page_r + 1}"
        elif img_right:
            main = f"{page_r + 1}"
        elif img_left:
            main = f"{page_l + 1}"
        else:
            main = "—"
        self._set_page_label(main, f"{self.total_pages} 頁")
        key = ("d", lower, higher, self.is_rtl, self.page_offset, self.rotation, self._compose_epoch, self._render_bucket)
        if key == self._compose_key and self._compose_pix is not None:
            self.spread_map = list(self._compose_spread)
            master = self._compose_pix
        else:
            master = self.merge_images(img_left, img_right, page_l, page_r)
            self._store_compose(key, master)
        self._present(master)
        self.trigger_precache()

    def _store_compose(self, key, master):
        self._compose_key = key
        self._compose_pix = master
        self._compose_spread = list(self.spread_map)
        self._present_key = None
        self._present_pix = None

    def _spread_part(self, idx, x, img):
        return {"idx": int(idx), "x": int(x), "y": 0, "w": int(img.width()), "h": int(img.height())}

    def _present(self, master):
        if master is None or master.isNull():
            self.current_final_pixmap = None
            self.image_label.clear()
            self.image_label.set_high_res_pixmap(None)
            self.image_label._lens_key = None
            return
        self.current_final_pixmap = master
        self.image_label.set_high_res_pixmap(master)
        viewport = self.scroll_area.viewport()
        view_w = max(1, viewport.width() - 5)
        view_h = max(1, viewport.height() - 5)
        dpr = float(self.devicePixelRatioF() or 1.0)
        if dpr <= 0:
            dpr = 1.0
        key = (master.cacheKey(), view_w, view_h, round(dpr, 2))
        if key != self._present_key or self._present_pix is None:
            target_w = max(1, int(round(view_w * dpr)))
            target_h = max(1, int(round(view_h * dpr)))
            scaled = master.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            self._present_pix = scaled
            self._present_key = key
        self.image_label.setPixmap(self._present_pix)

    def merge_images(self, left, right, idx_left=-1, idx_right=-1):
        self.spread_map = []
        if not left and not right:
            return None
        if left and not right:
            self.spread_map = [self._spread_part(idx_left, 0, left)]
            return QPixmap.fromImage(left)
        if right and not left:
            self.spread_map = [self._spread_part(idx_right, 0, right)]
            return QPixmap.fromImage(right)
        width = left.width() + right.width()
        height = max(left.height(), right.height())
        merged = QImage(width, height, QImage.Format.Format_RGB888)
        merged.fill(QColor("#1e1e1e"))
        painter = QPainter(merged)
        painter.drawImage(0, 0, left)
        painter.drawImage(left.width(), 0, right)
        painter.end()
        self.spread_map = [
            self._spread_part(idx_left, 0, left),
            self._spread_part(idx_right, left.width(), right),
        ]
        return QPixmap.fromImage(merged)

    def _native_long_cached(self, page, idx):
        cached = self._native_long.get(idx)
        if cached is not None:
            return cached or None
        value = _raster_native_long(page) or 0
        self._native_long[idx] = value
        return value or None

    def render_lens(self, master_x, master_y, crop_w, crop_h, lens_px):
        """依放大鏡底下的頁面範圍，向 PDF 要一塊較高解析度的圖。"""
        if not self.doc or not self.spread_map or crop_w < 1 or crop_h < 1 or lens_px < 8:
            return None
        painter = None
        try:
            dpr = float(self.devicePixelRatioF() or 1.0)
            if dpr <= 0:
                dpr = 1.0
            out_px = max(1, int(round(lens_px * dpr)))
            out = QImage(out_px, out_px, QImage.Format.Format_RGB888)
            out.fill(QColor("#1e1e1e"))
            painter = QPainter(out)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            crop_x = float(master_x) - float(crop_w) / 2.0
            crop_y = float(master_y) - float(crop_h) / 2.0
            zoom = out_px / float(crop_w)
            drawn = False
            for part in self.spread_map:
                inter_x0 = max(crop_x, part["x"])
                inter_y0 = max(crop_y, part["y"])
                inter_x1 = min(crop_x + crop_w, part["x"] + part["w"])
                inter_y1 = min(crop_y + crop_h, part["y"] + part["h"])
                if inter_x1 - inter_x0 < 0.5 or inter_y1 - inter_y0 < 0.5:
                    continue
                idx = part["idx"]
                if idx < 0 or idx >= self.total_pages:
                    continue
                page = self.doc.load_page(idx)
                rotation = int(self.rotation) % 360
                render_scale = self.page_scale.get(idx)
                if not render_scale or part["w"] < 1 or part["h"] < 1:
                    continue
                local_x0 = inter_x0 - part["x"]
                local_y0 = inter_y0 - part["y"]
                local_x1 = inter_x1 - part["x"]
                local_y1 = inter_y1 - part["y"]
                clip = pixels_to_page_clip(
                    page, rotation, render_scale,
                    local_x0, local_y0, local_x1, local_y1,
                    part["w"], part["h"],
                )
                if clip.is_empty or clip.width < 0.2 or clip.height < 0.2:
                    continue
                source_bounds = (page.rect * fitz.Matrix(render_scale, render_scale).prerotate(rotation)).irect
                source_per_final = source_bounds.width / float(part["w"]) if part["w"] else 1.0
                zscale = render_scale * zoom / source_per_final if source_per_final else render_scale * zoom
                native_long = self._native_long_cached(page, idx)
                vis_w, vis_h = _visual_page_size(page, rotation)
                vis_long = max(vis_w, vis_h)
                if native_long and vis_long > 0:
                    zscale = min(zscale, float(native_long) / vis_long)
                zscale = min(max(zscale, 0.05), 16.0)
                matrix = fitz.Matrix(zscale, zscale).prerotate(rotation)
                pix = page.get_pixmap(
                    matrix=matrix,
                    clip=clip,
                    colorspace=fitz.csRGB,
                    alpha=False,
                    annots=False,
                )
                if pix.width < 1 or pix.height < 1:
                    continue
                tile = _qimage_from_pixmap(pix)
                rx = (inter_x0 - crop_x) / crop_w * out_px
                ry = (inter_y0 - crop_y) / crop_h * out_px
                rw = (inter_x1 - inter_x0) / crop_w * out_px
                rh = (inter_y1 - inter_y0) / crop_h * out_px
                painter.drawImage(QRectF(rx, ry, rw, rh), tile)
                drawn = True
            if painter.isActive():
                painter.end()
            if not drawn:
                return None
            pixmap = QPixmap.fromImage(out)
            pixmap.setDevicePixelRatio(dpr)
            return pixmap
        except Exception:
            return None
        finally:
            if painter is not None and painter.isActive():
                painter.end()

    def _sync_mode_buttons(self):
        self.btn_spread.setText("單頁" if self.is_single_page else "雙頁")
        self._mark_active(self.btn_spread, True)
        self.btn_dir.setText("日漫" if self.is_rtl else "美漫")
        self._mark_active(self.btn_dir, True)
        self.btn_offset.setText("偶數起" if self.page_offset == 0 else "奇數起")
        self.btn_offset.setEnabled(not self.is_single_page)
        if self.btn_full.isChecked() != self.is_fullscreen:
            self.btn_full.setChecked(self.is_fullscreen)

    def _set_page_label(self, main, sub):
        self._page_main = main
        self._page_sub = sub
        self.page_main.setText(main)
        if not self._note_timer.isActive():
            self.page_sub.setText(sub)
        self._place_chrome()

    def _restore_page_sub(self):
        self.page_sub.setText(self._page_sub)
        self._place_chrome()

    def _flash_note(self, text):
        self.page_sub.setText(text)
        self._note_timer.start(2000)
        self._show_chrome(linger_ms=2200)
        self._place_chrome()

    def _toggle_adjust_panel(self):
        self._adjust_open = self.btn_adjust.isChecked()
        self._show_chrome(linger_ms=2600)

    def _is_descendant(self, widget, ancestor):
        while widget is not None:
            if widget is ancestor:
                return True
            widget = widget.parentWidget()
        return False

    def _pointer_over_widgets(self):
        pos = QCursor.pos()
        for widget in (self.dock, self.adjust_card):
            if not widget.isVisible():
                continue
            rect = widget.rect().adjusted(-16, -16, 16, 16)
            if rect.contains(widget.mapFromGlobal(pos)):
                return True
        return False

    def _pointer_in_bottom_strip(self):
        if not hasattr(self, "canvas"):
            return False
        pos = self.canvas.mapFromGlobal(QCursor.pos())
        width = self.canvas.width()
        height = self.canvas.height()
        return 0 <= pos.x() <= width and height - 72 <= pos.y() <= height

    def _chrome_interacting(self):
        if QApplication.mouseButtons() == Qt.MouseButton.NoButton:
            return False
        focus = QApplication.focusWidget()
        if focus is None:
            return False
        return self._is_descendant(focus, self.dock) or self._is_descendant(focus, self.adjust_card)

    def _pointer_in_reveal_zone(self):
        over = self._pointer_over_widgets()
        if QApplication.mouseButtons() & Qt.MouseButton.LeftButton and not over and not self._chrome_interacting():
            return False
        return over or self._pointer_in_bottom_strip() or self._chrome_interacting()

    def _show_chrome(self, linger_ms=None):
        self._chrome_timer.stop()
        dock_was = self.dock.isVisible()
        card_should = self._adjust_open
        card_was = self.adjust_card.isVisible()
        if not dock_was:
            self.dock.show()
        if card_should and not card_was:
            self.adjust_card.show()
        elif not card_should and card_was:
            self.adjust_card.hide()
        if self.btn_adjust.isChecked() != self._adjust_open:
            self.btn_adjust.setChecked(self._adjust_open)
        if not dock_was or card_should != card_was:
            self._place_chrome()
        if linger_ms:
            self._chrome_timer.start(linger_ms)

    def _schedule_chrome_hide(self, delay=900):
        if self._pointer_in_reveal_zone() or self._chrome_interacting():
            return
        remaining = self._chrome_timer.remainingTime()
        if remaining > delay:
            return
        self._chrome_timer.start(delay)

    def _hide_chrome(self):
        if self._pointer_in_reveal_zone() or self._chrome_interacting():
            self._chrome_timer.start(900)
            return
        self.dock.hide()
        self.adjust_card.hide()

    def _place_chrome(self):
        canvas = getattr(self, "canvas", None)
        if canvas is None or canvas.width() < 10 or not hasattr(self, "dock"):
            return
        self.dock.adjustSize()
        dock_w = self.dock.sizeHint().width()
        dock_h = max(self.dock.sizeHint().height(), 48)
        if dock_w > canvas.width() - 24:
            dock_w = canvas.width() - 24
        x = max(12, (canvas.width() - dock_w) // 2)
        y = max(8, canvas.height() - dock_h - 18)
        self.dock.setGeometry(x, y, dock_w, dock_h)
        self.adjust_card.adjustSize()
        # setFixedWidth 會讓 sizeHint 比實際寬度窄，用實際寬度才不會把右側按鈕推出視窗。
        card_w = max(self.adjust_card.width(), self.adjust_card.sizeHint().width())
        card_h = max(self.adjust_card.height(), self.adjust_card.sizeHint().height())
        card_w = min(card_w, max(120, canvas.width() - 24))
        cx = max(12, canvas.width() - card_w - 22)
        cy = y - card_h - 12
        if cy < 8:
            cy = 8
        self.adjust_card.setGeometry(cx, cy, card_w, card_h)
        overflow = self.adjust_card.geometry().right() - (canvas.width() - 22)
        if overflow > 0:
            self.adjust_card.move(max(12, self.adjust_card.x() - overflow), self.adjust_card.y())
        if self.dock.isVisible():
            self.dock.raise_()
        if self.adjust_card.isVisible():
            self.adjust_card.raise_()

    def _owns_widget(self, source):
        widget = source
        while widget is not None:
            if widget is self:
                return True
            parent = widget.parentWidget() if hasattr(widget, "parentWidget") else None
            widget = parent
        return False

    def toggle_direction(self):
        self.is_rtl = not self.is_rtl
        self._sync_mode_buttons()
        self.update_view()
        self._flash_note("日漫 · 右到左" if self.is_rtl else "美漫 · 左到右")

    def toggle_offset(self):
        if self.is_single_page:
            return
        self.page_offset = 1 if self.page_offset == 0 else 0
        self._sync_mode_buttons()
        self.update_view()
        msg = "偶數起 · 封面通常單獨一頁" if self.page_offset == 0 else "奇數起 · 首頁與下一頁並排"
        self._flash_note(msg)

    def toggle_single_page(self):
        self.is_single_page = not self.is_single_page
        self._sync_mode_buttons()
        self.update_view()
        msg = "單頁" if self.is_single_page else "雙頁"
        self._flash_note(msg)

    def reset_adjustments(self):
        self.blockSignals(True);
        self.slider_zoom.setValue(25);
        self.lbl_z.setText("2.5x");
        self.slider_sharp.setValue(10);
        self.lbl_s.setText("1.0")
        self.slider_cont.setValue(10);
        self.lbl_c.setText("1.0");
        self.slider_brit.setValue(10);
        self.lbl_b.setText("1.0")
        self.chk_high_dpi.setChecked(False);
        self.chk_auto.setChecked(False);
        self.chk_gray.setChecked(False)
        self.chk_night.setChecked(False);
        self.chk_moire.setChecked(False);
        self.chk_bicubic.setChecked(False)
        self.blockSignals(False);
        self.on_setting_change()

    def get_qimage(self, idx):
        if idx < 0 or idx >= self.total_pages:
            return None
        if idx in self.page_cache:
            self.page_cache.move_to_end(idx)
            return self.page_cache[idx]
        rendered = process_image_static(self.doc, idx, self.get_current_settings())
        if not rendered:
            return None
        img, scale = rendered
        self._cache_put(idx, img, scale)
        return self.page_cache.get(idx, img)

    def _cache_put(self, idx, img, scale):
        if idx in self.page_cache:
            self._drop_cached(idx)
        self.page_cache[idx] = img
        self.page_scale[idx] = float(scale)
        self._cache_bytes_n += img.sizeInBytes()
        self._evict_cache()

    def _drop_cached(self, idx):
        img = self.page_cache.pop(idx, None)
        self.page_scale.pop(idx, None)
        if img is not None:
            self._cache_bytes_n -= img.sizeInBytes()
            if self._cache_bytes_n < 0:
                self._cache_bytes_n = 0

    def _over_budget(self):
        return len(self.page_cache) > self.cache_max_pages or self._cache_bytes_n > self.cache_max_bytes

    def _evict_cache(self):
        while self._over_budget():
            victim = None
            for key in self.page_cache:
                if key not in self._pinned:
                    victim = key
                    break
            if victim is None:
                break
            self._drop_cached(victim)

    def clear_cache(self):
        self.page_cache.clear()
        self.page_scale.clear()
        self._cache_bytes_n = 0
        self._compose_epoch += 1
        self._compose_key = None
        self._compose_pix = None
        self._compose_spread = []
        self._present_key = None
        self._present_pix = None

    def _precache_indices(self):
        if not self.total_pages:
            return []
        base = self.current_base_index
        order = []
        seen = set()
        for delta in (1, 2, 3, 4, -1, -2):
            idx = base + delta
            if idx < 0 or idx >= self.total_pages or idx in self.page_cache or idx in seen:
                continue
            seen.add(idx)
            order.append(idx)
        return order

    def _stop_precache(self, wait=False):
        self._precache_gen += 1
        self._precache_again = False
        worker = self.cache_thread
        if worker is not None and worker.isRunning():
            worker.stop()
            if wait:
                worker.wait(2000)

    def trigger_precache(self):
        if not self.doc or not self.current_file_path:
            return
        self._precache_gen += 1
        worker = self.cache_thread
        if worker is not None and worker.isRunning():
            worker.stop()
            self._precache_again = True
            return
        self._start_precache_worker()

    def _start_precache_worker(self):
        need = self._precache_indices()
        if not need or not self.current_file_path:
            self._precache_again = False
            return
        old = self.cache_thread
        worker = CacheWorker(self.current_file_path, need, self.get_current_settings(), self._precache_gen)
        worker.image_ready.connect(self._on_precache_ready)
        worker.finished.connect(self._on_precache_finished)
        self.cache_thread = worker
        worker.start()
        if old is not None and old is not worker:
            old.deleteLater()

    def _on_precache_ready(self, gen, idx, qimg, scale):
        """預快取結果回呼。過期世代與正在看的頁面都不會把快取撐爆。"""
        if gen != self._precache_gen or qimg is None or qimg.isNull():
            return
        self._cache_put(idx, qimg, scale)

    def _on_precache_finished(self):
        if self._precache_again:
            self._precache_again = False
            self._start_precache_worker()

    def next_page(self):
        if not self.doc: return
        if self.is_single_page:
            step = 1
        else:
            step = 1 if (self.page_offset == 0 and self.current_base_index == 0) else 2
        if self.current_base_index + step < self.total_pages: self.current_base_index += step; self.update_view()

    def prev_page(self):
        if not self.doc: return
        if self.is_single_page:
            step = 1
        else:
            step = 1 if (self.page_offset == 0 and self.current_base_index == 1) else 2
        if self.current_base_index - step >= 0:
            self.current_base_index -= step; self.update_view()
        elif self.current_base_index > 0:
            self.current_base_index = 0; self.update_view()
            
    def rotate_left(self):
        self.rotation = (self.rotation - 90) % 360
        self._applied_signature = self._settings_signature()
        self.clear_cache()
        self.update_view()
        self._flash_note(f"向左旋轉 · {self.rotation}°")

    def rotate_right(self):
        self.rotation = (self.rotation + 90) % 360
        self._applied_signature = self._settings_signature()
        self.clear_cache()
        self.update_view()
        self._flash_note(f"向右旋轉 · {self.rotation}°")

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if path: self.load_pdf(path)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.exit_fullscreen()
        else:
            self.is_fullscreen = True
            self.showFullScreen()
            self._sync_mode_buttons()
            self._show_chrome(linger_ms=1800)
            self.update_view()

    def exit_fullscreen(self):
        if self.isFullScreen():
            self.is_fullscreen = False
            self.showNormal()
            self._sync_mode_buttons()
            self._show_chrome(linger_ms=1800)
            self.update_view()

    def showEvent(self, event):
        super().showEvent(event)
        self._place_chrome()
        if not self._chrome_introduced:
            self._chrome_introduced = True
            self._show_chrome(linger_ms=2600)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "dock"):
            self._place_chrome()
        # 防抖：resize 結束後才重繪，避免連續高頻渲染
        timer = getattr(self, "_resize_timer", None)
        if timer is not None:
            timer.start()

    def _apply_resize(self):
        """防抖後真正重繪"""
        self.update_view()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            timer = getattr(self, "_resize_timer", None)
            if timer is not None:
                timer.start()
        super().changeEvent(event)

    def eventFilter(self, source, event):
        et = event.type()
        if et == QEvent.Type.Resize and source is getattr(self, "canvas", None):
            self._place_chrome()
        elif et == QEvent.Type.MouseMove and self._owns_widget(source):
            self._on_pointer_move()
        elif et == QEvent.Type.Leave and self._owns_widget(source):
            self._schedule_chrome_hide(900)
        return super().eventFilter(source, event)

    def _on_pointer_move(self):
        if self._pointer_in_reveal_zone():
            self._show_chrome()
        elif self.dock.isVisible():
            self._schedule_chrome_hide(900)

    def init_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self.next_page)
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, self.next_page)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self.prev_page)
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, self.prev_page)
        QShortcut(QKeySequence(Qt.Key.Key_Home), self, self.go_to_first_page)
        QShortcut(QKeySequence(Qt.Key.Key_End), self, self.go_to_last_page)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, self.toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.exit_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_L), self, self.rotate_left)
        QShortcut(QKeySequence(Qt.Key.Key_R), self, self.rotate_right)

    def wheelEvent(self, event):
        if not self.doc: return
        delta = event.angleDelta().y()
        if delta < 0:
            self.next_page()
        elif delta > 0:
            self.prev_page()
        event.accept()

    def go_to_first_page(self):
        if not self.doc: return
        self.current_base_index = 0;
        self.update_view()

    def go_to_last_page(self):
        if not self.doc: return
        self.current_base_index = max(0, self.total_pages - 1);
        self.update_view()

    def dragEnterEvent(self, e):
        e.accept() if e.mimeData().hasUrls() else e.ignore()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls()]
        if files and files[0].lower().endswith('.pdf'): self.load_pdf(files[0])


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setStyleSheet(MODERN_STYLE)
    window = ComicReader()
    window.show()
    # 延遲應用最大化狀態，避免初始化 crash
    if window.app_settings.get("is_maximized", False):
        QTimer.singleShot(100, window.showMaximized)

    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".pdf"):
        window.load_pdf(sys.argv[1])
    sys.exit(app.exec())