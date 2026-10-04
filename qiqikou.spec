# PyInstaller 打包配置（Windows / macOS 共用）
#   Windows: pyinstaller qiqikou.spec  → dist/去气口工具/去气口工具.exe
#   macOS:   pyinstaller qiqikou.spec  → dist/去气口工具.app
import sys
from PyInstaller.utils.hooks import collect_data_files

IS_MAC = sys.platform == "darwin"
NAME = "去气口工具"

a = Analysis(
    ["app.py"],
    datas=[
        ("silero_vad.onnx", "."),
        ("assets/icon.ico", "assets"),
        ("assets/icon.png", "assets"),
    ] + collect_data_files("tkinterdnd2"),
    hiddenimports=["tkinterdnd2"],
    excludes=["pydub", "matplotlib", "scipy", "pandas", "PIL", "IPython", "pytest", "setuptools"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    icon="assets/icon.icns" if IS_MAC else "assets/icon.ico",
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)

if IS_MAC:
    app = BUNDLE(
        coll,
        name=NAME + ".app",
        icon="assets/icon.icns",
        bundle_identifier="com.qiaozhanggui.qiqikou",
        info_plist={
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": "2.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
