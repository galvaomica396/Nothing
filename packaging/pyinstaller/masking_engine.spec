# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files


repo_root = Path(SPECPATH).parents[1]
datas = [
    (str(repo_root / "data" / "kr_regions.seed.json"), "data"),
]
datas += collect_data_files("ko_pii")
active_regions = repo_root / "data" / "kr_regions.json"
if active_regions.exists():
    datas.append((str(active_regions), "data"))


a = Analysis(
    [str(repo_root / "scripts" / "masking_engine_entry.py")],
    pathex=[str(repo_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "document_masker_ocr_gui",
        "ko_pii_detector",
        "masking_context",
        "pdf_redaction_rendering",
        "masking_rules",
        "masking_extraction",
        "masking_redaction",
        "masking_reporting",
        "privacy_false_positive",
        "privacy_spans",
        "privacy_transformers",
        "fitz",
        "pymupdf",
        "pypdf",
        "pymupdf4llm",
        "ko_pii",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="masking_engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
