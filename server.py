#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FileBeam Lite (免费开源版) - 局域网极速跨设备文件互传工具
电脑与手机免安装 APP，同一局域网扫码即传。
单文件最大限制: 10MB (如需无限制大文件/断点续传请升级 FileBeam Pro)
"""

import os
import sys
import io
import json
import time
import socket
import secrets
import hashlib
import uuid
import getpass
import mimetypes
from pathlib import Path
from datetime import datetime
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Windows 控制台 UTF-8 支持
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import qrcode
import psutil
from flask import Flask, request, jsonify, send_file, render_template, abort

# ─── 路径与配置 ───────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent.resolve()
RECEIVED_DIR = BASE_DIR / "received"
SHARED_DIR   = BASE_DIR / "shared"
STATIC_DIR   = BASE_DIR / "static"
CONFIG_FILE  = BASE_DIR / "config.json"
LICENSE_FILE = BASE_DIR / "license.json"

RECEIVED_DIR.mkdir(parents=True, exist_ok=True)
SHARED_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PORT = 5000
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB 免费版限制
PRO_UPGRADE_URL_DEFAULT = "https://wojingfang.cn/tools/filebeam.html"
ACTIVATION_API_DEFAULT = "https://api.wojingfang.cn"

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            changed = False
            if not cfg.get("pro_upgrade_url") or "your-repo" in cfg.get("pro_upgrade_url", ""):
                cfg["pro_upgrade_url"] = PRO_UPGRADE_URL_DEFAULT
                changed = True
            if not cfg.get("activation_api_url"):
                cfg["activation_api_url"] = ACTIVATION_API_DEFAULT
                changed = True
            if changed:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
            return cfg
        except Exception:
            pass
    cfg = {
        "device_name": socket.gethostname(),
        "port": DEFAULT_PORT,
        "max_size_mb": 10,
        "pro_upgrade_url": PRO_UPGRADE_URL_DEFAULT,
        "activation_api_url": ACTIVATION_API_DEFAULT,
        "received_dir": str(RECEIVED_DIR),
        "shared_dir": str(SHARED_DIR)
    }
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg

CONFIG = load_config()
PORT = CONFIG.get("port", DEFAULT_PORT)


def get_machine_code():
    """Create a stable, non-reversible 12-character device identifier."""
    # Never expose the raw MAC address, hostname, or Windows account name.
    fingerprint = "|".join((
        "FileBeam-Pro",
        socket.gethostname(),
        f"{uuid.getnode():012x}",
        getpass.getuser(),
    ))
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:12].upper()


MACHINE_CODE = get_machine_code()


def load_license_state():
    if not LICENSE_FILE.exists():
        return {"activated": False, "machine_code": MACHINE_CODE}
    try:
        data = json.loads(LICENSE_FILE.read_text(encoding="utf-8"))
        activated = (
            data.get("machine_code") == MACHINE_CODE
            and bool(str(data.get("activation_code", "")).strip())
        )
        return {
            "activated": activated,
            "machine_code": MACHINE_CODE,
            "activated_at": data.get("activated_at") if activated else None,
        }
    except Exception:
        return {"activated": False, "machine_code": MACHINE_CODE}


def verify_activation_code(activation_code: str):
    """Verify a code against the same machine-code record used by the website."""
    api_base = str(CONFIG.get("activation_api_url", ACTIVATION_API_DEFAULT)).rstrip("/")
    query = urlencode({"code": MACHINE_CODE})
    request = Request(
        f"{api_base}/query?{query}",
        headers={"User-Agent": "FileBeam-Lite/1.0.1", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read(256 * 1024).decode("utf-8"))
    except (URLError, TimeoutError, ValueError, OSError):
        return False, "unavailable", "暂时无法连接授权服务，请稍后重试。"

    expected = str(payload.get("activation") or "").strip().upper()
    submitted = activation_code.strip().upper()
    if payload.get("success") and expected and secrets.compare_digest(expected, submitted):
        return True, "activated", "激活码验证成功，授权已保存到本机。"
    if payload.get("status") == "pending" or not payload.get("success"):
        return False, "pending", str(payload.get("msg") or "授权仍在处理中，请确认付款后稍后重试。")
    return False, "invalid", "激活码与本机机器码不匹配，请核对后重新输入。"

# ─── 获取本机局域网 IP（智能过滤虚拟网卡）──────────────────────────────────────
def get_local_ip():
    virtual_keywords = ["vmware", "virtual", "vbox", "wsl", "hyper-v", "vethernet", "loopback", "docker", "tap", "tun"]
    candidates = []
    try:
        for iface, addrs in psutil.net_if_addrs().items():
            iface_lower = iface.lower()
            is_virtual = any(kw in iface_lower for kw in virtual_keywords)
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    ip = addr.address
                    if is_virtual or ip.startswith("169.254."):
                        candidates.append((2, ip))
                    elif ip.startswith("192.168.") or ip.startswith("10.") or ip.startswith("172."):
                        candidates.append((0, ip))
                    else:
                        candidates.append((1, ip))
        if candidates:
            candidates.sort(key=lambda x: x[0])
            return candidates[0][1]
    except Exception:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"

# ─── Flask 实例初始化 ─────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="static", template_folder="static")
app.config["SECRET_KEY"] = secrets.token_hex(16)
# 允许稍微超过10MB以便给前端返回自定义 413 错误信息
app.config["MAX_CONTENT_LENGTH"] = (MAX_FILE_SIZE + 1024 * 1024)

# ─── 辅助函数 ─────────────────────────────────────────────────────────────────
def format_size(bytes_num):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} TB"

def get_safe_filename(target_dir: Path, original_name: str) -> str:
    name = Path(original_name).name
    # 过滤路径穿越符号
    name = name.replace("/", "").replace("\\", "").replace("..", "").strip()
    if not name:
        name = "unnamed_file"
    target_path = target_dir / name
    if not target_path.exists():
        return name
    stem = Path(name).stem
    suffix = Path(name).suffix
    counter = 1
    while (target_dir / f"{stem}({counter}){suffix}").exists():
        counter += 1
    return f"{stem}({counter}){suffix}"

def list_folder_files(folder_path: Path):
    if not folder_path.exists():
        return []
    items = []
    try:
        for p in folder_path.iterdir():
            if p.is_file() and not p.name.startswith("."):
                try:
                    stat = p.stat()
                    items.append({
                        "name": p.name,
                        "size": stat.st_size,
                        "size_str": format_size(stat.st_size),
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    })
                except Exception:
                    continue
        # 按修改时间降序排序
        items.sort(key=lambda x: x["modified"], reverse=True)
    except Exception as e:
        print(f"[ERROR] 读取目录失败 {folder_path}: {e}")
    return items

# ─── 路由定义 ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file(STATIC_DIR / "index.html")

@app.route("/api/info", methods=["GET"])
def get_info():
    ip = get_local_ip()
    url = f"http://{ip}:{PORT}"
    license_state = load_license_state()
    return jsonify({
        "edition": "FileBeam Lite (免费开源版)",
        "version": "1.0.1",
        "ip": ip,
        "port": PORT,
        "url": url,
        "max_size_mb": 10,
        "max_size_bytes": MAX_FILE_SIZE,
        "pro_upgrade_url": CONFIG.get("pro_upgrade_url", PRO_UPGRADE_URL_DEFAULT),
        "machine_code": MACHINE_CODE,
        "activated": license_state["activated"],
        "received_dir": str(RECEIVED_DIR),
        "shared_dir": str(SHARED_DIR),
        "received_count": len(list_folder_files(RECEIVED_DIR)),
        "shared_count": len(list_folder_files(SHARED_DIR))
    })


@app.route("/api/activation-status", methods=["GET"])
def activation_status():
    state = load_license_state()
    return jsonify({
        "success": True,
        "machine_code": MACHINE_CODE,
        "activated": state["activated"],
        "activated_at": state.get("activated_at"),
        "pro_upgrade_url": CONFIG.get("pro_upgrade_url", PRO_UPGRADE_URL_DEFAULT),
    })


@app.route("/api/activate", methods=["POST"])
def activate_license():
    data = request.get_json(silent=True) or {}
    activation_code = str(data.get("activation_code", "")).strip().upper()
    if not activation_code:
        return jsonify({"success": False, "error": "EMPTY_CODE", "message": "请输入激活码。"}), 400
    if len(activation_code) > 80:
        return jsonify({"success": False, "error": "INVALID_CODE", "message": "激活码格式不正确。"}), 400

    valid, status, message = verify_activation_code(activation_code)
    if not valid:
        return jsonify({
            "success": False,
            "status": status,
            "message": message,
            "machine_code": MACHINE_CODE,
        }), 200

    activated_at = datetime.now().isoformat(timespec="seconds")
    LICENSE_FILE.write_text(
        json.dumps({
            "product": "FileBeam Pro",
            "machine_code": MACHINE_CODE,
            "activation_code": activation_code,
            "activated_at": activated_at,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return jsonify({
        "success": True,
        "status": "activated",
        "message": message,
        "machine_code": MACHINE_CODE,
        "activated": True,
        "activated_at": activated_at,
    })

@app.route("/api/files", methods=["GET"])
def get_files():
    return jsonify({
        "shared": list_folder_files(SHARED_DIR),
        "received": list_folder_files(RECEIVED_DIR)
    })

@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "没有收到文件"}), 400
    
    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"success": False, "error": "文件名为空"}), 400

    # 检查单文件大小
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)

    if file_size > MAX_FILE_SIZE:
        return jsonify({
            "success": False,
            "error": "FILE_TOO_LARGE",
            "message": f"免费版单文件最大限制为 10MB（当前文件 {format_size(file_size)}），如需传输大文件请升级到专业版。",
            "pro_url": CONFIG.get("pro_upgrade_url", PRO_UPGRADE_URL_DEFAULT)
        }), 413

    safe_name = get_safe_filename(RECEIVED_DIR, file.filename)
    dest_path = RECEIVED_DIR / safe_name

    try:
        file.save(dest_path)
        return jsonify({
            "success": True,
            "filename": safe_name,
            "size": file_size,
            "size_str": format_size(file_size),
            "message": "文件上传成功！"
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"保存文件失败: {str(e)}"}), 500

@app.route("/api/download/<folder>/<path:filename>", methods=["GET"])
def download_file(folder, filename):
    if folder == "shared":
        base = SHARED_DIR
    elif folder == "received":
        base = RECEIVED_DIR
    else:
        return abort(404)

    file_path = (base / filename).resolve()
    # 严格防止目录穿越漏洞
    if not str(file_path).startswith(str(base.resolve())):
        return abort(403)
    if not file_path.is_file():
        return abort(404)

    mime, _ = mimetypes.guess_type(str(file_path))
    return send_file(
        file_path,
        mimetype=mime or "application/octet-stream",
        as_attachment=True,
        download_name=file_path.name
    )

@app.route("/api/qrcode", methods=["GET"])
def get_qrcode():
    ip = get_local_ip()
    url = f"http://{ip}:{PORT}"
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1e293b", back_color="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")

@app.errorhandler(413)
def entity_too_large(e):
    return jsonify({
        "success": False,
        "error": "FILE_TOO_LARGE",
        "message": "免费版单文件最大限制为 10MB。如需传输数GB大文件请升级到专业版。",
        "pro_url": CONFIG.get("pro_upgrade_url", PRO_UPGRADE_URL_DEFAULT)
    }), 413

# ─── 控制台启动与引导 ─────────────────────────────────────────────────────────
def print_banner(ip, port):
    url = f"http://{ip}:{port}"
    print("=" * 64)
    print("  🚀 FileBeam Lite (免费开源版) - 局域网文件互传服务已启动")
    print("=" * 64)
    print(f"  💻 电脑端访问:  {url}")
    print(f"  📱 手机端访问:  同一 WiFi 下打开手机浏览器访问上述网址")
    print(f"                 或扫描电脑屏幕上的二维码直接打开")
    print(f"  📂 接收保存目录: {RECEIVED_DIR}")
    print(f"  📂 共享文件目录: {SHARED_DIR}")
    print("-" * 64)
    print("  💡 免费版特性:  单文件限额 10MB (日常照片/办公文档免安装畅传)")
    print("  💎 升级专业版:  支持多GB大文件、断点续传、手机常亮、一键打包")
    print("=" * 64)
    print("  按 Ctrl + C 可随时退出服务\n")

if __name__ == "__main__":
    local_ip = get_local_ip()
    print_banner(local_ip, PORT)
    try:
        app.run(host="0.0.0.0", port=PORT, threaded=True)
    except KeyboardInterrupt:
        print("\n[INFO] 服务已停止。")
