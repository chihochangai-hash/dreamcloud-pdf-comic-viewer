# Dreamcloud PDF Comic Viewer

Windows 上的 PDF 漫畫閱讀器。用 PyQt6 顯示頁面，PyMuPDF 渲染 PDF，Pillow 做畫質調整。

## 功能

- 拖拉或從命令列開啟 PDF，並記住閱讀進度
- 單頁 / 雙頁、日漫（右到左）/ 美漫（左到右）、奇數頁或偶數頁起讀
- 旋轉、放大鏡、亮度、對比、銳利度
- 自動色階、灰階、反相、去網紋、雙立方與高畫質

閱讀紀錄存在程式旁邊的 `comic_settings.json`，這個檔不會進版本庫。

## 執行

需要 Python 3.11 或以上。

```powershell
pip install -r requirements.txt
python PDF_comic_viewer.py
python PDF_comic_viewer.py "漫畫.pdf"
```

## 打包

```powershell
pip install pyinstaller
python -m PyInstaller --noconfirm --clean "Dreamcloud PDF Comic Viewer.spec"
```

輸出是 `dist\Dreamcloud PDF Comic Viewer.exe`。圖示使用同資料夾的 `PDF_icon.ico`。
