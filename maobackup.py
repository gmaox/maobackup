import base64
import ctypes
import subprocess
import sys
import time
import webbrowser
import os, threading, zipfile, json, platform
from datetime import datetime
import tkinter as tk
from tkinter import Listbox, simpledialog, messagebox
import requests
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import urllib3
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
from win10toast import ToastNotifier
toaster = ToastNotifier()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
#C:\Users\86150\AppData\Local\Programs\Python\Python38\python.exe -m PyInstaller --add-data "icon.ico;." -i icon.ico maobackup.py --noconsole
# 全局变量
selected_path = None
config = {}  # 存储 WebDAV 配置
path_set = set()
addgame_mode = False
addgame_name = ""
if len(sys.argv) > 2 and sys.argv[1] == "-addgame":
    addgame_mode = True
    addgame_name = sys.argv[2]
    print(f"addgame_mode: {addgame_mode}, addgame_name: {addgame_name}")
class WebDAVClient:
    """基于requests的WebDAV客户端，替换opendal功能"""
    def __init__(self, hostname, username, password):
        self.hostname = hostname.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.verify = False
        self.session.proxies = {"http": None, "https": None}
    
    def list(self, path):
        """列出目录内容，返回类似opendal的Entry对象列表。自动创建不存在的目录。"""
        url = urljoin(self.hostname + "/", path)
        # 构建PROPFIND请求的XML体
        propfind_xml = '''<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
    <D:prop>
        <D:resourcetype/>
        <D:getlastmodified/>
        <D:getcontentlength/>
    </D:prop>
</D:propfind>'''
        def try_propfind():
            response = self.session.request("PROPFIND", url, 
                headers={
                    "Depth": "1",
                    "Content-Type": "application/xml"
                },
                data=propfind_xml.encode('utf-8')
            )
            response.raise_for_status()
            return response
        try:
            try:
                response = try_propfind()
            except requests.exceptions.HTTPError as e:
                # 409 Conflict: 目录不存在，自动创建
                if hasattr(e.response, 'status_code') and e.response.status_code == 409:
                    # 递归创建父目录
                    parent = os.path.dirname(path.rstrip('/'))
                    if parent and parent != path:
                        self._ensure_dir(parent)
                    # 创建当前目录
                    mkcol_resp = self.session.request("MKCOL", url)
                    if mkcol_resp.status_code not in (201, 405):
                        # 201 Created, 405 Method Not Allowed(已存在)
                        raise Exception(f"MKCOL失败: {mkcol_resp.status_code}")
                    # 创建后重试
                    response = try_propfind()
                else:
                    raise
            # 解析XML响应
            root = ET.fromstring(response.content)
            entries = []
            for response_elem in root.findall(".//{DAV:}response"):
                href_elem = response_elem.find(".//{DAV:}href")
                if href_elem is not None:
                    href = href_elem.text
                    # 移除URL前缀，只保留相对路径，并进行URL解码
                    if href.startswith(self.hostname):
                        href = href[len(self.hostname):]
                    if href.startswith("/"):
                        href = href[1:]
                    from urllib.parse import unquote
                    href = unquote(href)
                    # 检查是否为目录
                    is_dir = False
                    propstat = response_elem.find(".//{DAV:}propstat")
                    if propstat is not None:
                        prop = propstat.find(".//{DAV:}prop")
                        if prop is not None:
                            resourcetype = prop.find(".//{DAV:}resourcetype")
                            if resourcetype is not None:
                                collection = resourcetype.find(".//{DAV:}collection")
                                is_dir = collection is not None
                    entry = type('Entry', (), {
                        'path': href,
                        'is_dir': is_dir
                    })()
                    entries.append(entry)
            return entries
        except Exception as e:
            print(f"WebDAV list失败: {e}")
            print(f"请求URL: {url}")
            print(f"请求头: {response.headers if 'response' in locals() else 'N/A'}")
            print(f"响应状态码: {response.status_code if 'response' in locals() else 'N/A'}")
            if 'response' in locals() and response.content:
                print(f"响应内容: {response.content[:500]}...")
            return []

    def _ensure_dir(self, path):
        """递归创建目录（仅用于list自动修复）"""
        url = urljoin(self.hostname + "/", path)
        parent = os.path.dirname(path.rstrip('/'))
        if parent and parent != path:
            self._ensure_dir(parent)
        mkcol_resp = self.session.request("MKCOL", url)
        # 201 Created, 405 Method Not Allowed(已存在)
        if mkcol_resp.status_code not in (201, 405):
            raise Exception(f"MKCOL失败: {mkcol_resp.status_code}")
    
    def stat(self, path):
        """获取文件信息，返回类似opendal的Stat对象"""
        url = urljoin(self.hostname + "/", path)
        
        # 构建PROPFIND请求的XML体
        propfind_xml = '''<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
    <D:prop>
        <D:resourcetype/>
        <D:getlastmodified/>
        <D:getcontentlength/>
    </D:prop>
</D:propfind>'''
        
        try:
            response = self.session.request("PROPFIND", url, 
                headers={
                    "Depth": "0",
                    "Content-Type": "application/xml"
                },
                data=propfind_xml.encode('utf-8')
            )
            response.raise_for_status()
            
            # 解析XML响应
            root = ET.fromstring(response.content)
            
            # 查找lastmodified
            last_modified = None
            for response_elem in root.findall(".//{DAV:}response"):
                propstat = response_elem.find(".//{DAV:}propstat")
                if propstat is not None:
                    prop = propstat.find(".//{DAV:}prop")
                    if prop is not None:
                        lastmodified_elem = prop.find(".//{DAV:}getlastmodified")
                        if lastmodified_elem is not None:
                            last_modified_str = lastmodified_elem.text
                            # 解析时间格式 "Wed, 09 Jun 2021 10:18:14 GMT"
                            try:
                                from email.utils import parsedate_to_datetime
                                last_modified = parsedate_to_datetime(last_modified_str)
                            except:
                                pass
            
            # 创建类似opendal.Stat的对象
            stat_obj = type('Stat', (), {
                'last_modified': last_modified
            })()
            return stat_obj
        except Exception as e:
            print(f"WebDAV stat失败: {e}")
            print(f"请求URL: {url}")
            print(f"请求头: {response.headers if 'response' in locals() else 'N/A'}")
            print(f"响应状态码: {response.status_code if 'response' in locals() else 'N/A'}")
            if 'response' in locals() and response.content:
                print(f"响应内容: {response.content[:500]}...")
            return None
    
    def write(self, path, data):
        """上传文件"""
        url = urljoin(self.hostname + "/", path)
        try:
            response = self.session.put(url, data=data)
            response.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            # 409 Conflict: 目录不存在，自动创建
            if hasattr(e.response, 'status_code') and e.response.status_code == 409:
                # 递归创建父目录
                parent = os.path.dirname(path.rstrip('/'))
                if parent and parent != path:
                    self._ensure_dir(parent)
                try:
                    # 创建后重试
                    response = self.session.put(url, data=data)
                    response.raise_for_status()
                    return True
                except Exception as e:
                    print(f"WebDAV write失败（重试后）：{e}")
                    print(f"请求URL: {url}")
                    print(f"请求头: {response.headers if 'response' in locals() else 'N/A'}")
                    print(f"响应状态码: {response.status_code if 'response' in locals() else 'N/A'}")
                    if 'response' in locals() and response.content:
                        print(f"响应内容: {response.content[:500]}...")
                    return False
            else:
                raise
        except Exception as e:
            print(f"WebDAV write失败: {e}")
            print(f"请求URL: {url}")
            print(f"请求头: {response.headers if 'response' in locals() else 'N/A'}")
            print(f"响应状态码: {response.status_code if 'response' in locals() else 'N/A'}")
            if 'response' in locals() and response.content:
                print(f"响应内容: {response.content[:500]}...")
            return False
    
    def read(self, path):
        """下载文件"""
        url = urljoin(self.hostname + "/", path)
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.content
        except Exception as e:
            print(f"WebDAV read失败: {e}")
            print(f"请求URL: {url}")
            print(f"请求头: {response.headers if 'response' in locals() else 'N/A'}")
            print(f"响应状态码: {response.status_code if 'response' in locals() else 'N/A'}")
            if 'response' in locals() and response.content:
                print(f"响应内容: {response.content[:500]}...")
            return None

if datetime.now() > datetime(2025, 12, 3):
    messagebox.showerror("测试结束", "为保证安全性，该版本已停止使用，请联系作者获取最新版本。")
    sys.exit(0)
class MyHandler(FileSystemEventHandler):
    """文件系统事件处理器，将变化目录添加到列表"""
    def __init__(self, listbox, path_set):
        self.listbox = listbox
        self.directories = path_set

    def add_directory(self, directory):
        if directory not in self.directories:
            self.directories.add(directory)
            self.listbox.insert(tk.END, directory)
            self.listbox.see(tk.END)

    def on_created(self, event):
        directory = os.path.dirname(event.src_path)
        self.add_directory(directory)
    def on_deleted(self, event):
        directory = os.path.dirname(event.src_path)
        self.add_directory(directory)
    def on_modified(self, event):
        directory = os.path.dirname(event.src_path)
        self.add_directory(directory)
    def on_moved(self, event):
        directory = os.path.dirname(event.dest_path)
        self.add_directory(directory)
class TextRedirector(object):
    def __init__(self, text_widget):
        self.text_widget = text_widget
    def write(self, s):
        self.text_widget.configure(state='normal')
        self.text_widget.insert('end', s)
        self.text_widget.see('end')
        self.text_widget.configure(state='disabled')
    def flush(self):
        pass

# ----------- 状态窗口与print重定向 -----------
class StatusWindow:
    def __init__(self, root=None, title="备份/还原状态"):
        if root is None:
            self.root = tk.Tk()
        else:
            self.root = root
            # 清空原有控件
            for widget in self.root.winfo_children():
                widget.destroy()
        self.root.title(title)
        self.text = tk.Text(self.root, width=80, height=30, wrap="word")
        self.text.pack(fill="both", expand=True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # 保存原始stdout和stderr
        self._orig_stdout = sys.__stdout__
        self._orig_stderr = sys.__stderr__
        # 重定向stdout和stderr
        self._redirector = TextRedirector(self.text)
        sys.stdout = self._redirector
        sys.stderr = self._redirector
    def restore_redirect(self):
        sys.stdout = self._redirector
        sys.stderr = self._redirector
    def restore_orig(self):
        sys.stdout = self._orig_stdout
        sys.stderr = self._orig_stderr
    def mainloop(self):
        self.root.mainloop()
    def on_close(self):
        self.root.destroy()
        sys.exit(0)
def handle_selected_path():
    """双击路径后弹窗选择子路径并设置 selected_path，并输入游戏名保存到 webdav_config.json"""
    global selected_path, game_name
    selection = listbox.curselection()
    if not selection:
        return
    full_path = listbox.get(selection[0])
    parts = full_path.split("\\")
    segments = []
    for i in range(2, len(parts)+1):
        segment = "\\".join(parts[:i])
        segments.append(segment)
    dialog = tk.Toplevel(root)
    dialog.title("选择路径分段复制到剪贴板")
    dialog.attributes('-topmost', True)
    tk.Label(dialog, text="请选择路径分段：").pack(padx=10, pady=5)
    def on_seg(idx):
        global selected_path, game_name
        chosen = segments[idx]
        root.clipboard_clear()
        root.clipboard_append(chosen)
        selected_path = chosen
        selected_path_var.set(chosen)
        dialog.destroy()
        # 统计文件大小
        total_size = 0
        file_count = 0
        if os.path.exists(chosen):
            for root_, dirs_, files_ in os.walk(chosen):
                for file_ in files_:
                    try:
                        total_size += os.path.getsize(os.path.join(root_, file_))
                        file_count += 1
                    except Exception:
                        pass
        if addgame_mode:
            name = addgame_name
            if not show_message("confirm", "添加游戏", f"已添加游戏：{name}，路径：{chosen}\n文件数: {file_count}总大小: {total_size/1024:.2f} KB\n请仔细确认备份信息"):
                return
        else:
            # 获取默认游戏名
            default_name = os.path.basename(chosen.rstrip("\\/"))
            # 弹窗输入游戏名称，默认值为目录最后一级
            name = simpledialog.askstring(
                "请仔细确认备份信息",
                f"当前路径: {chosen}\n文件数: {file_count}\n总大小: {total_size/1024:.2f} KB\n\n请输入游戏名称：",
                initialvalue=default_name,
                parent=root
            )
            if not name:
                return
        game_name = name
        game_name_var.set(name)
        update_selected_info()
        # 保存到 webdav_config.json
        try:
            with open("webdav_config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        games = cfg.get("games", [])
        # 检查是否已存在同名游戏，存在则更新路径
        found = False
        for g in games:
            if g.get("name") == name:
                g["path"] = chosen
                found = True
                break
        if not found:
            games.append({"name": name, "path": chosen})
        cfg["games"] = games
        # 保存 last_selected
        cfg["last_selected"] = {"name": name, "path": chosen}
        with open("webdav_config.json", "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        if addgame_mode:
            sys.exit(0)  # 退出程序
    for idx, seg in enumerate(segments):
        btn = tk.Button(dialog, text=f"{idx+1}: {seg}", anchor="w", width=60,
                        command=lambda i=idx: on_seg(i))
        btn.pack(fill="x", padx=10, pady=2)

def backup():
    """点击备份按钮后，自动填充游戏名和路径，未选择时提示"""
    global selected_path, game_name
    if not selected_path or not game_name:
        show_message("error", "错误", "请先选择一个游戏或路径！")
        return
    print(f"开始备份路径: {selected_path}, 游戏名: {game_name}")
    #remark = simpledialog.askstring("备注", "请输入备注（可选）：", parent=root)
    remark = None
    backup_path = f"maobackup/{game_name}"
    threading.Thread(target=perform_backup, args=(selected_path, game_name, remark, backup_path)).start()

def perform_backup(path, game_name, remark, backup_path):
    """执行备份：保留父目录，记录完整路径，打包并上传到 WebDAV"""
    try:
        operator = get_opendal_operator()
        if operator is None:
            print("WebDAV 客户端初始化失败")
            return
        timestamp = datetime.now().strftime("%Y %m%d %H%M%S")
        system = platform.node() # 获取本机电脑名
        if remark:
            backup_name = f"({remark}){game_name}-{timestamp}-{system}.zip"
        else:
            backup_name = f"{game_name}-{timestamp}-{system}.zip"
        remote_path = f"{backup_path}/{backup_name}".replace("\\", "/")
        local_zip = "temp_backup.zip"

        # 1. 获取父目录和目录名
        parent_dir = os.path.dirname(path)
        dir_name = os.path.basename(path)
        backup_path_file = os.path.join(parent_dir, "backup_path.txt")
        # 2. 写入完整路径到 backup_path.txt（优先用环境变量）
        env_map = {
            "%CommonProgramFiles%": os.environ.get("CommonProgramFiles", r"C:\Program Files\Common Files"),
            "%COMMONPROGRAMFILES(x86)%": os.environ.get("CommonProgramFiles(x86)", r"C:\Program Files (x86)\Common Files"),
            "%HOMEPATH%": os.environ.get("HOMEPATH", ""),
            "%USERPROFILE%": os.environ.get("USERPROFILE", ""),
            "%APPDATA%": os.environ.get("APPDATA", ""),
            "%ALLUSERSPROFILE%": os.environ.get("ALLUSERSPROFILE", ""),
            "%TEMP%": os.environ.get("TEMP", ""),
            "%LOCALAPPDATA%": os.environ.get("LOCALAPPDATA", ""),
            "%PROGRAMDATA%": os.environ.get("PROGRAMDATA", ""),
            "%PUBLIC%": os.environ.get("PUBLIC", r"C:\Users\Public"),
            # 特殊目录
            #"%STARTMENU%": os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu"),
            #"%STARTUP%": os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup"),
        }
        def replace_with_env_vars(p):
            # 优先最长路径匹配
            for var, val in sorted(env_map.items(), key=lambda x: -len(str(x[1]))):
                if val and p.startswith(val):
                    return p.replace(val, var, 1)
            return p

        path_for_backup = replace_with_env_vars(path)
        with open(backup_path_file, "w", encoding="utf-8") as f:
            f.write(path_for_backup)
        # 3. 打包 backup_path.txt 和存档目录（并列在 zip 根目录）
        with zipfile.ZipFile(local_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # 打包存档目录
            for root, dirs, files in os.walk(path):
                for file in files:
                    file_path = os.path.join(root, file)
                    # zip 内部路径：存档目录名/子路径
                    arcname = os.path.join(dir_name, os.path.relpath(file_path, path))
                    zipf.write(file_path, arcname)
            # 打包 backup_path.txt 到 zip 根目录
            zipf.write(backup_path_file, "backup_path.txt")
        # 删除临时 backup_path.txt
        os.remove(backup_path_file)

        print(f"正在上传备份文件: {remote_path}")
        with open(local_zip, "rb") as f:
            data = f.read()
        if operator.write(remote_path, data):
            print("备份完成")
            # 若工作目录中存在 DeskGamix.exe，且为快速操作，则弹出托盘通知，否则弹窗
            if '--quick-dgaction' in sys.argv:
                toaster.show_toast("备份完成", f"备份已上传到远程: {remote_path}", icon_path='', duration=1)
            else:
                show_message("info", "备份完成", f"备份已上传到远程: {remote_path}")
        else:
            print("备份失败：上传失败")
            show_message("error", "错误", "备份失败：上传失败")
        os.remove(local_zip)
    except Exception as e:
        print(f"备份失败：{e}")
        show_message("error", "错误", f"备份失败: {e}")
        return

def dir_exists(client, path):
    """用 list() 判断目录是否存在"""
    try:
        parent = os.path.dirname(path) or '/'
        items = client.list(parent)
        folder_name = os.path.basename(path.rstrip('/'))
        for item in items:
            if item.path == folder_name and item.is_dir:
                return True
        return False
    except Exception as e:
        print(f"检查目录 {path} 是否存在时出错: {e}")
        return False

def get_opendal_operator():
    """根据配置创建 WebDAV 客户端"""
    global config
    if not config:
        try:
            with open("webdav_config.json", "r", encoding="utf-8") as f:
                saved = json.load(f)
                config["hostname"] = saved["hostname"]
                config["username"] = base64.b64decode(saved["username"]).decode()
                config["password"] = base64.b64decode(saved["password"]).decode()
        except Exception:
            return None
    try:
        operator = WebDAVClient(
            hostname=config["hostname"],
            username=config["username"],
            password=config["password"]
        )
        return operator
    except Exception as e:
        print(f"WebDAV 客户端初始化失败: {e}")
        return None

def configure_webdav():
    """弹窗集中输入WebDAV参数，账号密码简单加密保存本地"""
    global config
    dialog = tk.Toplevel(root)
    dialog.title("WebDAV 配置")
    dialog.attributes('-topmost', True)
    dialog.grab_set()
    tk.Label(dialog, text="WebDAV 主机 URL:").grid(row=0, column=0, sticky="e", padx=5, pady=5)
    tk.Label(dialog, text="用户名:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
    tk.Label(dialog, text="密码:").grid(row=2, column=0, sticky="e", padx=5, pady=5)
    tk.Label(dialog, text="（当尝试还原备份时，程序会将本机原存档压缩在/extra_backup目录中）\n(因此建议定期清理extra_backup下的压缩文件)\n(该版本为测试版本，12月3日之后软件将无法使用，届时请前往下载更新版本)", fg="gray").grid(row=3, column=0, columnspan=2, padx=5, pady=2)
    entry_host = tk.Entry(dialog, width=40)
    entry_user = tk.Entry(dialog, width=40)
    entry_pass = tk.Entry(dialog, width=40, show="*")
    entry_host.grid(row=0, column=1, padx=5, pady=5)
    entry_user.grid(row=1, column=1, padx=5, pady=5)
    entry_pass.grid(row=2, column=1, padx=5, pady=5)

    # 尝试读取本地配置
    try:
        with open("webdav_config.json", "r", encoding="utf-8") as f:
            saved = json.load(f)
            entry_host.insert(0, saved.get("hostname", ""))
            entry_user.insert(0, base64.b64decode(saved.get("username", "")).decode())
            entry_pass.insert(0, base64.b64decode(saved.get("password", "")).decode())
    except Exception:
        pass

    def save():
        host = entry_host.get().strip()
        user = entry_user.get().strip()
        pwd = entry_pass.get().strip()
        if not host or not user:
            show_message("error", "错误", "WebDAV 主机和用户名不能为空！")
            return
        # 简单加密
        cfg = {
            "hostname": host,
            "username": base64.b64encode(user.encode()).decode(),
            "password": base64.b64encode(pwd.encode()).decode()
        }
        with open("webdav_config.json", "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        # 解密后赋值到全局
        config["hostname"] = host
        config["username"] = user
        config["password"] = pwd
        show_message("info", "配置", "WebDAV 配置已保存。")
        dialog.destroy()
    # 添加坚果云和GitHub按钮
    def open_jianguoyun():
        webbrowser.open("https://www.jianguoyun.com/")
    def open_github():
        webbrowser.open("https://github.com/gmaox/maobackup")
    btn_frame = tk.Frame(dialog)
    btn_frame.grid(row=5, column=0, columnspan=2, pady=2)
    tk.Button(btn_frame, text="调试模式", command=lambda: (ctypes.windll.kernel32.AllocConsole(), setattr(sys, 'stdout', open("CONOUT$", "w")))).pack(side="left", padx=5)
    tk.Button(btn_frame, text="坚果云网盘", command=open_jianguoyun).pack(side="left", padx=5)
    tk.Button(btn_frame, text="GitHub地址", command=open_github).pack(side="left", padx=5)
    tk.Button(btn_frame, text="保存WebDAV 配置", command=save).pack(side="left", padx=5)

def list_backups():
    """递归获取 maobackup/ 下所有 ZIP 文件，并显示在远程列表框，暂停本地监听。若已选择游戏，只显示该游戏存档。"""
    stop_monitor()
    client = get_opendal_operator()
    if not client:
        show_message("error", "错误", "WebDAV 未配置")
        return
    def walk_dir(path, files, dirs):
        # 确保 path 以 / 结尾
        if not path.endswith('/'):
            path = path + '/'
        # 检查末尾目录是否重复，例如 maobackup/test1/test1/
        parts = path.strip('/').split('/')
        if len(parts) >= 2 and parts[-1] == parts[-2]:
            # 末尾目录重复，跳出递归
            return
        for entry in client.list(path):
            # 跳过自身目录（有些 WebDAV 返回 "" 或 "." 作为当前目录）
            if not entry.path or entry.path in ('.', './'):
                continue
            entry_name = entry.path.rstrip('/').split('/')[-1]
            if not entry_name:
                continue
            next_path = path + entry_name
            if entry.is_dir:
                if path == "maobackup/":
                    # 只收集一级目录名（即游戏名），排除自身
                    if entry_name != "maobackup":
                        dirs.append(entry_name)
                else:
                    walk_dir(next_path, files, dirs)
            elif next_path.endswith('.zip'):
                rel_path = next_path[len('maobackup/'):]
                files.append(rel_path)
    try:
        files = []
        dirs = []
        # 若已选择游戏，只拉取该游戏的存档
        if game_name_var.get():
            game = game_name_var.get()
            walk_dir(f"maobackup/{game}/", files, dirs)
            show_all_btn.pack(side="left", padx=5)
            listbox_remote.delete(0, tk.END)
            for f in reversed(files):
                listbox_remote.insert(tk.END, f)
            listbox_remote.pack()  # 确保显示
            # 还原点击事件
            def on_backup_select(event=None):
                sel = listbox_remote.curselection()
                if not sel:
                    return
                entry = listbox_remote.get(sel[0])
                # 直接还原选中的备份
                restore_selected(entry)
            listbox_remote.unbind("<Double-Button-1>")
            listbox_remote.bind("<Double-Button-1>", on_backup_select)
        else:
            walk_dir("maobackup/", files, dirs)
            show_all_btn.pack_forget()
            # 只显示游戏列表
            listbox_remote.delete(0, tk.END)
            for d in dirs:
                listbox_remote.insert(tk.END, d)
            listbox_remote.pack()
            # 绑定点击事件：点击后自动选择该游戏并拉取存档
            def on_game_select(event=None):
                sel = listbox_remote.curselection()
                if not sel:
                    return
                game = listbox_remote.get(sel[0])
                # 优先查找本地webdav_config.json是否有该游戏路径
                restored_path = None
                try:
                    with open("webdav_config.json", "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    games = cfg.get("games", [])
                    for g in games:
                        if g.get("name") == game and g.get("path"):
                            restored_path = g["path"]
                            break
                except Exception:
                    pass
                # 如果本地没有路径，则下载zip获取路径
                if not restored_path:
                    temp_files = []
                    walk_dir(f"maobackup/{game}/", temp_files, [])
                    if temp_files:
                        remote_path = f"maobackup/{temp_files[0]}"
                        local_zip = os.path.join(os.getcwd(), os.path.basename(temp_files[0]))
                        if download_webdav_file(remote_path, local_zip):
                            try:
                                with zipfile.ZipFile(local_zip, 'r') as z:
                                    path_txt = z.read("backup_path.txt").decode("utf-8").strip()
                                    restored_path = os.path.expandvars(path_txt)
                            except Exception:
                                pass
                            finally:
                                try:
                                    os.remove(local_zip)
                                except Exception:
                                    pass
                # 设置全局变量并刷新
                global selected_path, game_name
                selected_path = restored_path if restored_path else ""
                game_name = game
                selected_path_var.set(selected_path)
                game_name_var.set(game_name)
                update_selected_info()
                # 保存到 webdav_config.json（如果本地没有则补充）
                if restored_path:
                    try:
                        with open("webdav_config.json", "r", encoding="utf-8") as f:
                            cfg = json.load(f)
                    except Exception:
                        cfg = {}
                    games = cfg.get("games", [])
                    found = False
                    for g in games:
                        if g.get("name") == game:
                            if not g.get("path"):
                                g["path"] = restored_path
                            found = True
                            break
                    if not found:
                        games.append({"name": game, "path": restored_path})
                    cfg["games"] = games
                    cfg["last_selected"] = {"name": game, "path": restored_path}
                    with open("webdav_config.json", "w", encoding="utf-8") as f:
                        json.dump(cfg, f, ensure_ascii=False, indent=2)
                # 重新拉取该游戏的存档
                list_backups()
            # 只绑定一次
            listbox_remote.unbind("<Double-Button-1>")
            listbox_remote.bind("<Double-Button-1>", on_game_select)
    except Exception as e:
        show_message("error", "错误", e)
        print(f"获取备份列表失败: {e}")

def show_all_remote_backups():
    """清除游戏选择状态并拉取全部远程存档"""
    global selected_path, game_name
    selected_path = None
    game_name = None
    selected_path_var.set("")
    game_name_var.set("")
    update_selected_info()
    list_backups()

def download_webdav_file(remote_path, local_path):
    """使用WebDAV客户端下载文件"""
    client = get_opendal_operator()
    if not client:
        print("WebDAV 未配置")
        return False
    
    try:
        data = client.read(remote_path)
        if data is not None:
            with open(local_path, "wb") as f:
                f.write(data)
            return True
        else:
            print("下载失败：无法读取文件")
            return False
    except Exception as e:
        print(f"下载失败：{e}")
        return False
def restore_selected(entry=None):
    """下载选中的备份 ZIP，读取 backup_path.txt 并恢复文件，自动保存新游戏名和路径到本地配置
    entry: 可选，远程zip路径（如 maobackup/xxx/xxx.zip），如有则直接还原该文件，否则按listbox选中"""
    if entry is None:
        sel = listbox_remote.curselection()
        if not sel:
            return
        entry = listbox_remote.get(sel[0])
    # 支持多级目录，entry 形如 test1/xxx.zip 或 test1/子目录/xxx.zip
    parts = entry.split('/')
    if len(parts) < 2:
        show_message("error", "错误", "无效的备份文件路径")
        return
    game = parts[0]
    zipname = '/'.join(parts[1:])
    remote_path = f"maobackup/{entry}" if not entry.startswith("maobackup/") else entry
    client = get_opendal_operator()
    if not client:
        show_message("error", "错误", "WebDAV 未配置")
        return
    # 修复：本地 zip 路径只用文件名，避免多级目录不存在
    local_zip = os.path.join(os.getcwd(), os.path.basename(zipname))
    success = download_webdav_file(remote_path, local_zip)
    if not success:
        show_message("error", "错误", f"下载失败: {remote_path}")
        return
    try:
        with zipfile.ZipFile(local_zip, 'r') as z:
            # 读取 backup_path.txt
            path_txt = z.read("backup_path.txt").decode("utf-8").strip()
            restored_path = os.path.expandvars(path_txt)
            # 找到存档目录名
            all_names = z.namelist()
            dir_names = [n.split('/')[0] for n in all_names if '/' in n and not n.startswith('__MACOSX')]
            if not dir_names:
                show_message("error", "错误", "备份包中未找到存档目录")
                return
            archive_dir = os.path.basename(restored_path)
            # 统计存档目录下文件总大小
            total_size = 0
            file_count = 0
            for member in all_names:
                if member.startswith(archive_dir + "/") and not member.endswith("/"):
                    info = z.getinfo(member)
                    total_size += info.file_size
                    file_count += 1
            # 获取zip内backup_path.txt的修改时间作为备份时间
            try:
                info = z.getinfo("backup_path.txt")
                # info.date_time: (year, month, day, hour, minute, second)
                zip_time = time.strftime('%Y-%m-%d %H:%M:%S', time.struct_time((info.date_time[0], info.date_time[1], info.date_time[2], info.date_time[3], info.date_time[4], info.date_time[5], 0, 0, -1)))
            except Exception:
                zip_time = "N/A"
            # 自动保存到webdav_config.json
            saved_to_local = False
            try:
                with open("webdav_config.json", "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}
                saved_to_local = True
            games = cfg.get("games", [])
            found = False
            for g in games:
                if g.get("name") == game and g.get("path") == restored_path:
                    found = True
                    break
            if not found and game != "maobackup":
                games.append({"name": game, "path": restored_path})
                cfg["games"] = games
                with open("webdav_config.json", "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                saved_to_local = True
            # 弹窗确认
            msg = (
                f"存档目录名: {archive_dir}\n"
                f"文件数: {file_count}\n"
                f"总大小: {total_size/1024:.2f} KB\n"
                f"备份时间: {zip_time}\n"
                f"原路径: {restored_path}\n"
            )
            if saved_to_local:
                msg += "游戏路径信息已保存本地供下次备份使用。\n"
            msg += "\n是否确认还原？"
            if not show_message("confirm", "还原确认", msg):
                return
            save_dir = os.path.join(os.path.dirname(restored_path), archive_dir)
            # ----------- 新增：先备份当前存档目录到/extra_backup -------------
            import shutil
            backup_dir = os.path.join(os.getcwd(), "extra_backup")
            if not os.path.exists(backup_dir):
                os.makedirs(backup_dir)
            # 备份文件名：存档目录名+时间戳
            backup_time = time.strftime('%Y%m%d_%H%M%S')
            backup_zip_path = os.path.join(backup_dir, f"{archive_dir}_{backup_time}.zip")
            if os.path.exists(restored_path) and os.path.isdir(restored_path):
                # 先写入backup_path.txt到临时文件
                backup_path_txt = os.path.join(os.path.dirname(restored_path), "backup_path.txt")
                try:
                    with open(backup_path_txt, "w", encoding="utf-8") as f:
                        f.write(restored_path)
                    with zipfile.ZipFile(backup_zip_path, 'w', zipfile.ZIP_DEFLATED) as backup_zip:
                        # 打包存档目录
                        for root_, dirs_, files_ in os.walk(restored_path):
                            for file_ in files_:
                                file_path_ = os.path.join(root_, file_)
                                arcname_ = os.path.relpath(file_path_, os.path.dirname(restored_path))
                                backup_zip.write(file_path_, arcname_)
                        # 打包backup_path.txt到zip根目录
                        backup_zip.write(backup_path_txt, "backup_path.txt")
                except Exception as e:
                    show_message("warning", "备份警告", f"备份原存档目录失败: {e}")
                    return
                finally:
                    try:
                        os.remove(backup_path_txt)
                    except Exception:
                        pass
            # ----------- 新增：清空目标目录 -------------
            if os.path.exists(restored_path) and os.path.isdir(restored_path):
                try:
                    for root_, dirs_, files_ in os.walk(restored_path, topdown=False):
                        for file_ in files_:
                            try:
                                os.remove(os.path.join(root_, file_))
                            except Exception:
                                pass
                        for dir_ in dirs_:
                            try:
                                shutil.rmtree(os.path.join(root_, dir_))
                            except Exception:
                                pass
                except Exception as e:
                    return
            # ----------- 解压存档目录到目标路径 -------------
            for member in all_names:
                if member.startswith(archive_dir + "/"):
                    z.extract(member, os.path.dirname(restored_path))
        show_message("info", "还原完成", f"存档已还原到: {restored_path}")
    finally:
        try:
            os.remove(local_zip)
        except Exception:
            print(f"删除临时文件失败: {local_zip}")

def delete_selected_game():
    sel = saved_listbox.curselection()
    if not sel:
        show_message("info", "提示", "请先选择要删除的游戏。")
        return
    idx = sel[0]
    try:
        with open("webdav_config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        games = cfg.get("games", [])
    except Exception:
        games = []
    if idx >= len(games):
        return
    g = games[idx]
    if not show_message("confirm", "确认", f"确定要删除游戏：{g['name']} ?"):
        return
    del games[idx]
    cfg["games"] = games
    # 检查last_selected是否还存在于games
    last = cfg.get("last_selected", {})
    last_name = last.get("name", None)
    last_path = last.get("path", None)
    found = False
    for gg in games:
        if gg.get("name") == last_name and gg.get("path") == last_path:
            found = True
            break
    if not found:
        # 清除全局选择
        global selected_path, game_name
        selected_path = None
        game_name = None
        selected_path_var.set("")
        game_name_var.set("")
        update_selected_info()
        # 清除配置文件中的last_selected
        cfg["last_selected"] = {}
    with open("webdav_config.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    show_saved_games()

def add_desktop_shortcut():
    import sys
    import os
    sel = saved_listbox.curselection()
    if not sel:
        show_message("info", "提示", "请先选择要添加快捷方式的游戏。")
        return
    idx = sel[0]
    try:
        with open("webdav_config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        games = cfg.get("games", [])
    except Exception:
        games = []
    if idx >= len(games):
        return
    g = games[idx]
    shortcut_name = f"{g['name']}_快速同步.bat"
    # 多种方式获取桌面路径
    desktop = os.path.join(os.environ.get("USERPROFILE", r"C:\\Users\\Public"), "Desktop")
    if not os.path.exists(desktop):
        try:
            import ctypes
            CSIDL_DESKTOP = 0
            SHGFP_TYPE_CURRENT = 0
            buf = ctypes.create_unicode_buffer(260)
            ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOP, None, SHGFP_TYPE_CURRENT, buf)
            desktop = buf.value
        except Exception:
            desktop = os.path.expanduser("~\\Desktop")
    if not os.path.exists(desktop):
        try:
            desktop = os.path.join(os.path.expanduser("~"), "桌面")
        except Exception:
            pass
    if not os.path.exists(desktop):
        show_message("error", "桌面路径错误", f"无法定位桌面路径，请手动创建快捷方式。\n尝试的路径: {desktop}")
        return
    exe_path = os.path.abspath(sys.argv[0])
    shortcut_path = os.path.join(desktop, shortcut_name)
    # 生成bat内容
    # 用引号包裹路径，防止空格和中文问题
    bat_content = f'''@echo off
cd /d "{os.path.dirname(exe_path)}"
"{exe_path}" --quick-action "{g["name"]}"
exit
'''
    try:
        with open(shortcut_path, "w", encoding="utf-8") as f:
            f.write(bat_content)
        show_message(
            "info",
            "快捷方式",
            f"桌面批处理文件已创建：{shortcut_name}，双击可一键备份/还原该游戏。\n同步逻辑如下：\n双击后，程序会自动检测本地游戏存档时间和远程对比\n若本地存档较旧，会将远程较新的存档覆盖本地存档。\n（覆盖前会将存档zip备份到程序运行目录下的/extra_backup中）\n若本地存档较新，则会将本地存档打包上传到远程。"
        )
    except Exception as e:
        show_message("error", "快捷方式创建失败", f"创建批处理文件时发生错误：{e}\n请检查桌面路径和写入权限。\n目标: {shortcut_path}")

import tkinter.filedialog

def manual_select_path():
    global selected_path, game_name
    path = tkinter.filedialog.askdirectory(title="请选择游戏存档目录")
    if not path:
        return
    # 路径统一为反斜杠风格
    path = os.path.normpath(path)
    # 统计文件大小
    total_size = 0
    file_count = 0
    if os.path.exists(path):
        for root_, dirs_, files_ in os.walk(path):
            for file_ in files_:
                try:
                    total_size += os.path.getsize(os.path.join(root_, file_))
                    file_count += 1
                except Exception:
                    pass
    if addgame_mode:
        name = addgame_name
    else:
        default_name = os.path.basename(path.rstrip("\\/"))
        name = simpledialog.askstring(
            "请仔细确认备份信息",
            f"当前路径: {path}\n文件数: {file_count}\n总大小: {total_size/1024:.2f} KB\n\n请输入游戏名称：",
            initialvalue=default_name,
            parent=root
        )
        if not name:
            return
    selected_path = path
    game_name = name
    selected_path_var.set(path)
    game_name_var.set(name)
    update_selected_info()
    # 保存到 webdav_config.json
    try:
        with open("webdav_config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    games = cfg.get("games", [])
    found = False
    for g in games:
        if g.get("name") == name:
            g["path"] = path
            found = True
            break
    if not found:
        games.append({"name": name, "path": path})
    cfg["games"] = games
    cfg["last_selected"] = {"name": name, "path": path}
    with open("webdav_config.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    if addgame_mode:
        show_message("info", "添加游戏", f"已添加游戏：{name}，路径：{path}")
        sys.exit(0)  # 退出程序
def quick_action(game_name):
    # 读取本地配置，找到游戏路径（如果本地没有，后面会用zip内路径）
    try:
        with open("webdav_config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        games = cfg.get("games", [])
    except Exception:
        games = []
    game = next((g for g in games if g["name"] == game_name), None)
    local_path = game["path"] if game else None

    client = get_opendal_operator()
    if not client:
        show_message("error","错误","WebDAV 未配置")
        return
    # 1. 列出所有远程zip
    files = []
    def walk_dir(path, files):
        # path: 远程当前目录
        for entry in client.list(path):
            # 跳过自身目录（有些 WebDAV 返回 "" 或 "." 作为当前目录）
            if not entry.path or entry.path in ('.', './'):
                continue
            entry_name = entry.path.rstrip('/').split('/')[-1]
            if not entry_name:
                continue
            next_path = path.rstrip('/') + '/' + entry_name
            if entry.is_dir:
                walk_dir(next_path, files)
            elif next_path.endswith('.zip'):
                rel_path = next_path[len('maobackup/'):]
                files.append(rel_path)
    walk_dir(f"maobackup/{game_name}/", files)
    if not files:
        # 没有远程备份，直接用本地路径备份
        if local_path:
            print("无远程备份，自动执行备份...")
            do_backup(game_name, local_path)
        else:
            print("无远程备份，且本地未找到路径，无法备份。")
            subprocess.Popen(["maobackup.exe", "-addgame", game_name])
        return
    # 2. 找到最新zip
    files.sort(reverse=True)
    latest_zip = files[0]
    # latest_zip 已经是相对 maobackup/ 的路径
    remote_path = f"maobackup/{latest_zip}"
    # 3. 下载zip到本地临时文件
    import tempfile
    tmp_zip = tempfile.mktemp(suffix=".zip")
    success = download_webdav_file(remote_path, tmp_zip)
    if not success:
        print(f"下载远程备份失败: {remote_path}")
        return
    try:
        with zipfile.ZipFile(tmp_zip, 'r') as z:
            # 4. 读取 backup_path.txt 得到原始路径
            try:
                path_txt = z.read("backup_path.txt").decode("utf-8").strip()
                restored_path = os.path.expandvars(path_txt)
            except Exception:
                print("zip包中未找到 backup_path.txt，无法自动还原")
                restored_path = None
            # 5. 统计本地该路径下所有文件的最新修改时间
            local_latest_mtime = 0
            if restored_path and os.path.exists(restored_path):
                for root_, dirs_, files_ in os.walk(restored_path):
                    for file_ in files_:
                        try:
                            mtime = os.path.getmtime(os.path.join(root_, file_))
                            if mtime > local_latest_mtime:
                                local_latest_mtime = mtime
                        except Exception:
                            pass
            elif local_path and os.path.exists(local_path):
                for root_, dirs_, files_ in os.walk(local_path):
                    for file_ in files_:
                        try:
                            mtime = os.path.getmtime(os.path.join(root_, file_))
                            if mtime > local_latest_mtime:
                                local_latest_mtime = mtime
                        except Exception:
                            pass
            # 6. 直接用zip内backup_path.txt的修改时间作为远程备份时间
            try:
                info = z.getinfo("backup_path.txt")
                remote_time = time.mktime((info.date_time[0], info.date_time[1], info.date_time[2], info.date_time[3], info.date_time[4], info.date_time[5], 0, 0, -1))
                zip_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.struct_time((info.date_time[0], info.date_time[1], info.date_time[2], info.date_time[3], info.date_time[4], info.date_time[5], 0, 0, -1)))
            except Exception:
                remote_time = 0
                zip_time_str = "N/A"
            print(f"本地最新修改时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(local_latest_mtime)) if local_latest_mtime else '无'}")
            print(f"远程备份时间: {zip_time_str}")
            # 7. 比较时间，决定备份还是还原
            if local_latest_mtime > remote_time:
                # 优先用 zip 里的路径
                if restored_path:
                    print("本地较新，执行备份...")
                    do_backup(game_name, restored_path)
                elif local_path:
                    print("本地较新，执行备份...")
                    do_backup(game_name, local_path)
                else:
                    print("未找到本地路径，无法备份")
            else:
                print("远程较新，执行还原...")
                restore_selected(latest_zip)
    finally:
        try:
            os.remove(tmp_zip)
        except Exception:
            pass

def do_backup(game_name, path):
    print(f"自动备份: {game_name} {path}")
    backup_path = f"maobackup/{game_name}"
    remark = None
    perform_backup(path, game_name, remark, backup_path)

# def do_restore(local_zip):
#     print(f"自动还原: {local_zip}")
#     with zipfile.ZipFile(local_zip, 'r') as z:
#         path_txt = z.read("backup_path.txt").decode("utf-8").strip()
#         restored_path = os.path.expandvars(path_txt)
#         all_names = z.namelist()
#         archive_dir = os.path.basename(restored_path)
#         for member in all_names:
#             if member.startswith(archive_dir + "/"):
#                 z.extract(member, os.path.dirname(restored_path))
#     print(f"还原完成: {restored_path}")

# ----------- Tkinter 界面布局 -----------
root = tk.Tk()
root.title("游戏存档备份工具 v0.12")
root.attributes('-topmost', True)
try:
    icon_path = "./_internal/icon.ico"
    root.iconbitmap(icon_path)
except Exception:
    pass

def show_message(type_, title, message):
    if '--quick-dgaction' in sys.argv or '--quick-dgrestore' in sys.argv:
        # 临时恢复原stdout/stderr（如果有StatusWindow实例）
        status_win = None
        for v in globals().values():
            if isinstance(v, StatusWindow):
                status_win = v
                break
        if status_win is not None:
            status_win.restore_orig()
        try:
            print(json.dumps({"type": type_, "title": title, "message": message}), flush=True)
        finally:
            if status_win is not None:
                status_win.restore_redirect()
        if type_ == "confirm":
            resp = sys.stdin.readline()
            return resp.strip().lower() in ("yes", "true", "1")
        return None
    else:
        from tkinter import messagebox
        if type_ == "error":
            return messagebox.showerror(title, message)
        elif type_ == "info":
            return messagebox.showinfo(title, message)
        elif type_ == "warning":
            return messagebox.showwarning(title, message)
        elif type_ == "confirm":
            return messagebox.askokcancel(title, message)
        else:
            return None

# 已保存游戏选择区域（含说明、列表和按钮）
saved_frame = tk.Frame(root)
saved_label = tk.Label(saved_frame, text="选择已保存游戏（双击可快速选择）")
saved_label.pack()
saved_listbox = Listbox(saved_frame, width=65, height=8)
saved_listbox.pack()
btn_frame = tk.Frame(saved_frame)
btn_frame.pack(pady=3)
tk.Button(btn_frame, text="删除游戏", command=delete_selected_game).pack(side="left", padx=5)
tk.Button(btn_frame, text="添加桌面快捷方式", command=add_desktop_shortcut).pack(side="left", padx=5)
tk.Button(btn_frame, text="选择游戏", command=lambda: select_saved_game_action()).pack(side="left", padx=5)
saved_frame.pack_forget()  # 默认隐藏

# 本地路径区域（含说明、列表和按钮）
local_frame = tk.Frame(root)
local_label = tk.Label(local_frame, text="正在监听变化的目录（双击可选择）")
local_label.pack()
listbox = Listbox(local_frame, width=65, height=10)
listbox.pack()
listbox.bind("<Double-Button-1>", lambda e: handle_selected_path())
local_btn_frame = tk.Frame(local_frame)
local_btn_frame.pack(pady=3)
tk.Button(local_btn_frame, text="选择路径分段", command=handle_selected_path).pack(side="left", padx=5)
tk.Button(local_btn_frame, text="手动选择路径", command=manual_select_path).pack(side="left", padx=5)
local_frame.pack_forget()  # 默认隐藏

# 远程备份区域（含说明、列表和按钮）
remote_frame = tk.Frame(root)
remote_label = tk.Label(remote_frame, text="可还原的远程备份（双击可还原）")
remote_label.pack()
listbox_remote = Listbox(remote_frame, width=65, height=10)
listbox_remote.pack()
remote_btn_frame = tk.Frame(remote_frame)
remote_btn_frame.pack(pady=3)

extra_backup_frame = tk.Frame(root)
extra_backup_label = tk.Label(extra_backup_frame, text="本地还原时产生的额外备份列表（双击可还原）")
extra_backup_label.pack()
extra_backup_listbox = Listbox(extra_backup_frame, width=65, height=10)
extra_backup_listbox.pack()
extra_backup_btn_frame = tk.Frame(extra_backup_frame)
extra_backup_btn_frame.pack(pady=3)
tk.Button(extra_backup_btn_frame, text="返回", command=lambda: extra_backup_frame.pack_forget()).pack(side="left", padx=5)
extra_backup_frame.pack_forget()
extra_backup_listbox.bind("<Double-Button-1>", lambda e: restore_extra_backup())
def show_extra_backup_list():
    local_frame.pack_forget()
    saved_frame.pack_forget()
    remote_frame.pack_forget()
    extra_backup_frame.pack()
    extra_backup_listbox.delete(0, tk.END)
    backup_dir = os.path.join(os.getcwd(), "extra_backup")
    if not os.path.exists(backup_dir):
        show_message("info", "提示", "没有找到extra_backup目录。")
        return
    files = [f for f in os.listdir(backup_dir) if f.lower().endswith('.zip')]
    if not files:
        show_message("info", "提示", "extra_backup目录下没有备份文件。")
        return
    for f in sorted(files, reverse=True):
        extra_backup_listbox.insert(tk.END, f)

def restore_extra_backup():
    sel = extra_backup_listbox.curselection()
    if not sel:
        return
    filename = extra_backup_listbox.get(sel[0])
    backup_dir = os.path.join(os.getcwd(), "extra_backup")
    local_zip = os.path.join(backup_dir, filename)
    try:
        with zipfile.ZipFile(local_zip, 'r') as z:
            path_txt = z.read("backup_path.txt").decode("utf-8").strip()
            restored_path = os.path.expandvars(path_txt)
            all_names = z.namelist()
            dir_names = [n.split('/')[0] for n in all_names if '/' in n and not n.startswith('__MACOSX')]
            if not dir_names:
                show_message("error", "错误", "备份包中未找到存档目录")
                return
            archive_dir = os.path.basename(restored_path)
            total_size = 0
            file_count = 0
            for member in all_names:
                if member.startswith(archive_dir + "/") and not member.endswith("/"):
                    info = z.getinfo(member)
                    total_size += info.file_size
                    file_count += 1
            try:
                info = z.getinfo("backup_path.txt")
                zip_time = time.strftime('%Y-%m-%d %H:%M:%S', time.struct_time((info.date_time[0], info.date_time[1], info.date_time[2], info.date_time[3], info.date_time[4], info.date_time[5], 0, 0, -1)))
            except Exception:
                zip_time = "N/A"
            msg = (
                f"存档目录名: {archive_dir}\n"
                f"文件数: {file_count}\n"
                f"总大小: {total_size/1024:.2f} KB\n"
                f"备份时间: {zip_time}\n"
                f"原路径: {restored_path}\n"
            )
            msg += "\n是否确认还原？"
            if not show_message("confirm", "还原确认", msg):
                return
            import shutil
            if os.path.exists(restored_path) and os.path.isdir(restored_path):
                try:
                    for root_, dirs_, files_ in os.walk(restored_path, topdown=False):
                        for file_ in files_:
                            try:
                                os.remove(os.path.join(root_, file_))
                            except Exception:
                                pass
                        for dir_ in dirs_:
                            try:
                                shutil.rmtree(os.path.join(root_, dir_))
                            except Exception:
                                pass
                except Exception as e:
                    show_message("warning", "清空目录失败", f"清空目标目录失败: {e}")
                    return
            for member in all_names:
                if member.startswith(archive_dir + "/"):
                    z.extract(member, os.path.dirname(restored_path))
        show_message("info", "还原完成", f"存档已还原到: {restored_path}")
    except Exception as e:
        show_message("error", "错误", f"还原失败: {e}")
tk.Button(remote_btn_frame, text="额外备份列表", command=show_extra_backup_list).pack(side="left", padx=5)
tk.Button(remote_btn_frame, text="还原选定备份", command=restore_selected).pack(side="left", padx=5)
show_all_btn = tk.Button(remote_btn_frame, text="远程游戏列表", command=show_all_remote_backups)
# 默认不pack show_all_btn
remote_frame.pack_forget()  # 默认隐藏
listbox_remote.bind("<Double-Button-1>", lambda e: restore_selected())

# 当前选择路径和游戏名显示
selected_path_var = tk.StringVar()
game_name_var = tk.StringVar()
def update_selected_info():
    info = f"路径: {selected_path_var.get()}    游戏名: {game_name_var.get()}"
    if addgame_mode:
        info = f"当前游戏无路径，请先添加存档路径\n（下面文本框会列出有文件变化的路径，请进入游戏进行存档然后返回该程序进行路径选择）\n即将添加的游戏: {addgame_name}"
    selected_info_var.set(info)
selected_info_var = tk.StringVar()
update_selected_info() # 初始化显示
tk.Label(root, textvariable=selected_info_var, wraplength=600).pack()

def show_saved_games():
    saved_listbox.delete(0, tk.END)
    try:
        with open("webdav_config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        games = cfg.get("games", [])
    except Exception:
        games = []
    if not games:
        show_message("info", "提示", "没有已保存的游戏，请先选择路径并保存。")
        return
    for g in games:
        saved_listbox.insert(tk.END, f"{g['name']}  |  {g['path']}")
    saved_frame.pack()
    local_frame.pack_forget()
    remote_frame.pack_forget()
# 按钮区域
frame = tk.Frame(root)
frame.pack(fill="x", padx=10, pady=5)
tk.Button(frame, text="添加新游戏", command=lambda: [remote_frame.pack_forget(), saved_frame.pack_forget(), local_frame.pack(), listbox.delete(0, tk.END), start_monitor()]).pack(side="left", padx=0)
tk.Button(frame, text=" 本地游戏列表 ", command=show_saved_games).pack(side="left", padx=0)
tk.Button(frame, text="备份到WebDAV", command=backup).pack(side="left", padx=0)
tk.Button(frame, text=" 远程备份列表 ", command=lambda: [local_frame.pack_forget(), saved_frame.pack_forget(), remote_frame.pack(), list_backups()]).pack(side="left", padx=0)
tk.Button(frame, text="配置WebDAV", command=configure_webdav).pack(side="left", padx=0)

# 监听相关全局变量
observers = []
monitoring = False

def start_monitor():
    global observers, monitoring, path_set
    if monitoring:
        return
    path_set.clear()
    from psutil import disk_partitions
    partitions = [p.device for p in disk_partitions()]
    for path in partitions:
        if "Temp" in path:
            continue
        handler = MyHandler(listbox, path_set)
        observer = Observer()
        observer.schedule(handler, path, recursive=True)
        observer.start()
        observers.append(observer)
    monitoring = True
if addgame_mode:
    # 隐藏 frame 区域的所有按钮
    for child in frame.winfo_children():
        child.pack_forget()
    # 自动执行"添加新游戏"逻辑
    remote_frame.pack_forget()
    saved_frame.pack_forget()
    local_frame.pack()
    listbox.delete(0, tk.END)
    start_monitor()
def stop_monitor():
    global observers, monitoring
    for o in observers:
        o.stop()
        o.join()
    observers.clear()
    monitoring = False


def select_saved_game_action(event=None):
    sel = saved_listbox.curselection()
    if not sel:
        return
    idx = sel[0]
    # 重新读取，防止期间有变动
    try:
        with open("webdav_config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        games = cfg.get("games", [])
    except Exception:
        games = []
    if idx >= len(games):
        return
    g = games[idx]
    global selected_path, game_name
    selected_path = g["path"]
    game_name = g["name"]
    selected_path_var.set(selected_path)
    game_name_var.set(game_name)
    update_selected_info()
    saved_frame.pack_forget()
    # 保存 last_selected
    try:
        with open("webdav_config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg["last_selected"] = {"name": game_name, "path": selected_path}
    with open("webdav_config.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
saved_listbox.bind("<Double-Button-1>", select_saved_game_action)
# 启动时自动读取 last_selected
try:
    with open("webdav_config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)
    last = cfg.get("last_selected")
    if last:
        selected_path = last.get("path", "")
        game_name = last.get("name", "")
        selected_path_var.set(selected_path)
        game_name_var.set(game_name)
        update_selected_info()
except Exception:
    pass
# 如果命令行参数为 --quick-action/--quick-dgaction/--quick-restore/--quick-dgrestore，则执行对应操作
def quick_restore(game_name):
    try:
        client = get_opendal_operator()
        if not client:
            show_message("error", "错误", "WebDAV 未配置")
            return
        files = []
        def walk_dir(path, files):
            for entry in client.list(path):
                if not entry.path or entry.path in ('.', './'):
                    continue
                entry_name = entry.path.rstrip('/').split('/')[-1]
                if not entry_name:
                    continue
                next_path = path.rstrip('/') + '/' + entry_name
                if entry.is_dir:
                    walk_dir(next_path, files)
                elif next_path.endswith('.zip'):
                    rel_path = next_path[len('maobackup/'):]
                    files.append(rel_path)
        walk_dir(f"maobackup/{game_name}/", files)
        if not files:
            show_message("error", "错误", "无远程备份，无法还原。")
            return
        files.sort(reverse=True)
        latest_zip = files[0]
        print(f"自动还原: {latest_zip}")
        restore_selected(latest_zip)
    except Exception as e:
        print(f"自动还原失败: {e}")

if (len(sys.argv) > 2 and sys.argv[1] == "--quick-action") or ('--quick-dgaction' in sys.argv and len(sys.argv) > 2):
    try:
        for widget in root.winfo_children():
            widget.destroy()
    except Exception:
        pass
    status_win = StatusWindow(root)
    if '--quick-dgaction' in sys.argv:
        root.withdraw()
    def run_quick():
        try:
            quick_action(sys.argv[2])
            sys.exit(0)
        except Exception as e:
            print(f"发生异常: {e}")
            root.mainloop() 
        finally:
            print("\n操作已完成，可关闭窗口。")
    status_win.root.after(100, run_quick)
    status_win.mainloop()
    sys.exit(0)
elif (len(sys.argv) > 2 and sys.argv[1] == "--quick-restore") or ('--quick-dgrestore' in sys.argv and len(sys.argv) > 2):
    try:
        for widget in root.winfo_children():
            widget.destroy()
    except Exception:
        pass
    status_win = StatusWindow(root)
    if '--quick-dgrestore' in sys.argv:
        root.withdraw()
    def run_restore():
        try:
            quick_restore(sys.argv[2])
            sys.exit(0)
        except Exception as e:
            print(f"发生异常: {e}")
            root.mainloop()
        finally:
            print("\n操作已完成，可关闭窗口。")
    status_win.root.after(100, run_restore)
    status_win.mainloop()
    sys.exit(0)
# ===== 新增 -backuplist 参数处理 =====
elif (len(sys.argv) > 2 and sys.argv[1] == "-backuplist"):
    game_name = sys.argv[2]
    # 检查本地配置文件中是否有该游戏
    try:
        with open("webdav_config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        games = cfg.get("games", [])
    except Exception:
        games = []
    found = False
    for g in games:
        if g.get("name") == game_name:
            found = True
            selected_path = g.get("path", "")
            break
    if not found:
        # 没有该游戏，调用添加流程
        subprocess.Popen(["maobackup.exe", "-addgame", game_name])
        sys.exit(0)
    # 有该游戏，设置变量并切换界面
    game_name_var.set(game_name)
    selected_path_var.set(selected_path)
    update_selected_info()
    # 切换到远程备份界面并显示该游戏的备份
    local_frame.pack_forget()
    saved_frame.pack_forget()
    remote_frame.pack()
    list_backups()
    # 进入主循环
    root.mainloop()
    sys.exit(0)
else:
    # 主循环
    try:
        root.mainloop()
    finally:
        stop_monitor()