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
import hmac
import mimetypes
from pathlib import Path
from datetime import datetime
from urllib.error import URLError
from urllib.request import Request, urlopen

# Windows 控制台 UTF-8 支持
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import qrcode
import psutil
from flask import Flask, request, jsonify, send_file, render_template, abort

# ─── 路径与配置 ───────────────────────────────────────────────────────────────
RUNTIME_DIR  = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).parent.resolve()
BASE_DIR     = Path(__file__).parent.resolve()
RECEIVED_DIR = BASE_DIR / "received"
SHARED_DIR   = BASE_DIR / "shared"
STATIC_DIR   = BASE_DIR / "static"
CONFIG_FILE  = BASE_DIR / "config.json"
PID_FILE     = RUNTIME_DIR / "FileBeam_Lite.pid"


def get_license_file():
    if sys.platform == "win32":
        data_root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        data_root = Path.home() / "Library" / "Application Support"
    else:
        data_root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_root / "FileBeam" / "license.dat"


LICENSE_FILE = get_license_file()

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


LICENSE_SIGNING_KEY = bytes.fromhex(
    "8a12a9f5b5cf6d97388b2d4e93a4c882a3fc1c97df3e0f5f18e278624b95fc21"
)


def _terminate_stale_filebeam_processes():
    """Stop older FileBeam Lite instances before binding the service port."""
    current_pid = os.getpid()
    candidates = set()
    try:
        if PID_FILE.exists():
            candidates.add(int(PID_FILE.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        pass

    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        if proc.info["pid"] == current_pid:
            continue
        name = str(proc.info.get("name") or "").lower()
        exe = str(proc.info.get("exe") or "").lower()
        cmdline = " ".join(proc.info.get("cmdline") or []).lower()
        if (
            name == "filebeam_lite.exe"
            or Path(exe).name == "filebeam_lite.exe"
            or ("server.py" in cmdline and "filebeam_lite" in cmdline)
        ):
            candidates.add(int(proc.info["pid"]))

    for pid in candidates:
        if pid == current_pid:
            continue
        try:
            process = psutil.Process(pid)
            process.terminate()
            try:
                process.wait(timeout=3)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            print(f"[Process] 已结束旧版 FileBeam Lite 进程 PID={pid}")
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, OSError):
            continue


def _ensure_port_available():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("0.0.0.0", PORT))
    except OSError as exc:
        raise RuntimeError(
            f"端口 {PORT} 仍被其他程序占用。请关闭占用程序或在 config.json 修改端口。"
        ) from exc
    finally:
        probe.close()


def prepare_runtime():
    """Kill stale instances, verify the port, and register the current PID."""
    if os.environ.get("AIBAG_SMOKE_TEST") == "1":
        return
    _terminate_stale_filebeam_processes()
    _ensure_port_available()
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    import atexit

    def _remove_pid_file():
        try:
            if PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    atexit.register(_remove_pid_file)


def _license_signature(payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(LICENSE_SIGNING_KEY, body, hashlib.sha256).hexdigest()


def load_license_state():
    if not LICENSE_FILE.exists():
        return {"activated": False}
    try:
        data = json.loads(LICENSE_FILE.read_text(encoding="utf-8"))
        payload = data.get("payload") or {}
        signature = str(data.get("signature", ""))
        activated = (
            payload.get("product") == "FileBeam Pro"
            and len(str(payload.get("activation_hash", ""))) == 64
            and secrets.compare_digest(signature, _license_signature(payload))
        )
        return {
            "activated": activated,
            "activated_at": payload.get("activated_at") if activated else None,
            "license_type": payload.get("license_type") if activated else None,
            "activation_hash": payload.get("activation_hash") if activated else None,
        }
    except Exception:
        return {"activated": False}


def save_license_state(activation_code: str, remote: dict):
    activated_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "product": "FileBeam Pro",
        "license_type": "personal-multi-device-lifetime",
        "activation_hash": hashlib.sha256(activation_code.encode("utf-8")).hexdigest(),
        "activated_at": activated_at,
        "use_count": int(remote.get("use_count", 0) or 0),
        "max_uses": int(remote.get("max_uses", 10) or 10),
    }
    credential = {"payload": payload, "signature": _license_signature(payload)}
    LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LICENSE_FILE.write_text(
        json.dumps(credential, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        LICENSE_FILE.chmod(0o600)
    except OSError:
        pass
    return load_license_state()


def verify_activation_code(activation_code: str):
    """Verify an activation code through the Cloudflare Worker."""
    api_base = str(CONFIG.get("activation_api_url", ACTIVATION_API_DEFAULT)).rstrip("/")
    request = Request(
        f"{api_base}/api/activate",
        data=json.dumps({"activation_code": activation_code}).encode("utf-8"),
        method="POST",
        headers={
            "User-Agent": "FileBeam-Lite/1.0.2",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read(256 * 1024).decode("utf-8"))
    except (URLError, TimeoutError, ValueError, OSError):
        return False, "unavailable", "暂时无法连接授权服务，请稍后重试。", {}

    if payload.get("valid"):
        remaining = payload.get("remaining_uses")
        message = "激活成功，永久授权凭证已写入本地用户目录。"
        if remaining is not None:
            message += f" 该激活码还可联网验证 {remaining} 次。"
        return True, "activated", message, payload
    return False, "invalid", str(payload.get("message") or "激活码无效或使用次数已达上限。"), payload

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
    response = send_file(STATIC_DIR / "index.html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.after_request
def disable_stale_cache(response):
    if request.path == "/" or request.path.startswith(("/api/", "/static/")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

@app.route("/api/info", methods=["GET"])
def get_info():
    ip = get_local_ip()
    url = f"http://{ip}:{PORT}"
    license_state = load_license_state()
    return jsonify({
        "edition": "FileBeam Lite (免费开源版)",
        "version": "1.0.4",
        "ip": ip,
        "port": PORT,
        "url": url,
        "max_size_mb": 10,
        "max_size_bytes": MAX_FILE_SIZE,
        "pro_upgrade_url": CONFIG.get("pro_upgrade_url", PRO_UPGRADE_URL_DEFAULT),
        "activated": license_state["activated"],
        "license_type": license_state.get("license_type"),
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
        "activated": state["activated"],
        "activated_at": state.get("activated_at"),
        "license_type": state.get("license_type"),
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

    valid, status, message, remote = verify_activation_code(activation_code)
    if not valid:
        return jsonify({
            "success": False,
            "status": status,
            "message": message,
        }), 200

    state = save_license_state(activation_code, remote)
    return jsonify({
        "success": True,
        "status": "activated",
        "message": message,
        "activated": True,
        "activated_at": state.get("activated_at"),
        "license_type": state.get("license_type"),
        "remaining_uses": remote.get("remaining_uses"),
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
    prepare_runtime()
    local_ip = get_local_ip()
    print_banner(local_ip, PORT)
    try:
        app.run(host="0.0.0.0", port=PORT, threaded=True)
    except KeyboardInterrupt:
        print("\n[INFO] 服务已停止。")
