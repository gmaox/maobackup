import base64
import time
import os, threading, zipfile, json, platform
from datetime import datetime
import tkinter as tk
from tkinter import Listbox, simpledialog, messagebox
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import urllib3
import requests
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# 全局变量
selected_path = None
config = {}  # 存储 WebDAV 配置

class MyHandler(FileSystemEventHandler):
    """文件系统事件处理器，将变化目录添加到列表"""
    def __init__(self, listbox, path_set):
        self.listbox = listbox
        self.directories = path_set

    def add_directory(self, directory):
        if directory not in self.directories:
            self.directories.add(directory)
            self.listbox.insert(tk.END, directory)

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

def handle_selected_path():
    """双击路径后弹窗选择子路径并设置 selected_path"""
    global selected_path
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
    tk.Label(dialog, text="请选择路径分段：").pack(padx=10, pady=5)
    def on_seg(idx):
        global selected_path
        chosen = segments[idx]
        root.clipboard_clear()
        root.clipboard_append(chosen)
        selected_path = chosen
        selected_path_var.set(chosen)
        dialog.destroy()
    for idx, seg in enumerate(segments):
        btn = tk.Button(dialog, text=f"{idx+1}: {seg}", anchor="w", width=60,
                        command=lambda i=idx: on_seg(i))
        btn.pack(fill="x", padx=10, pady=2)

def backup():
    """点击备份按钮后，询问游戏名和备注，然后启动备份线程"""
    global selected_path
    selected_path = (r"C:\Users\86150\Desktop\3242\1")
    if not selected_path:
        messagebox.showerror("错误", "请先选择一个路径！")
        return
    print(f"开始备份路径: {selected_path}")
    #game_name = simpledialog.askstring("游戏名称", "请输入游戏名称：", parent=root)
    game_name = "game3"  # 测试用
    if not game_name:
        return
    #remark = simpledialog.askstring("备注", "请输入备注（可选）：", parent=root)
    remark = None
    # 路径不加 dav/ 前缀
    backup_path = f"python-upload/{game_name}"
    threading.Thread(target=perform_backup, args=(selected_path, game_name, remark, backup_path)).start()

def ensure_webdav_dir(remote_dir):
    # 递归创建目录
    url, user, pwd = get_webdav_config()
    parts = remote_dir.strip("/").split("/")
    for i in range(1, len(parts)+1):
        subdir = "/".join(parts[:i])
        full_url = f"{url}/{subdir}"
        r = requests.request("MKCOL", full_url, auth=(user, pwd), verify=False, proxies={"http": None, "https": None})
        if r.status_code in (201, 405):
            continue
        elif r.status_code == 409:
            continue  # 父目录不存在，继续递归
        else:
            print(f"创建目录失败: {full_url}, 状态码: {r.status_code}")
            break

def upload_webdav_file(remote_path, local_path):
    url, user, pwd = get_webdav_config()
    ensure_webdav_dir(os.path.dirname(remote_path))
    full_url = f"{url}/{remote_path}"
    with open(local_path, "rb") as f:
        data = f.read()
    r = requests.put(full_url, data=data, auth=(user, pwd), verify=False, proxies={"http": None, "https": None})
    if r.status_code in (200, 201, 204):
        return True
    else:
        print("上传失败，状态码：", r.status_code)
        return False

def get_webdav_config():
    with open("webdav_config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)
    url = cfg["hostname"].rstrip("/")
    user = base64.b64decode(cfg["username"]).decode()
    pwd = base64.b64decode(cfg["password"]).decode()
    return url, user, pwd

def list_webdav_zip_files(remote_dir):
    """
    递归列出 remote_dir 下所有 zip 文件（含子目录），返回相对路径，兼容坚果云
    """
    url, user, pwd = get_webdav_config()
    full_url = f"{url}/{remote_dir.strip('/')}"
    headers = {"Depth": "infinity"}
    body = '''<?xml version="1.0" encoding="utf-8" ?>\n<propfind xmlns="DAV:"><propname/></propfind>'''
    r = requests.request(
        "PROPFIND", full_url,
        data=body.encode("utf-8"),
        headers={**headers, "Content-Type": "application/xml"},
        auth=(user, pwd), verify=False, proxies={"http": None, "https": None}
    )
    if r.status_code not in (207, 200):
        print("列目录失败，状态码：", r.status_code)
        return []
    from xml.etree import ElementTree as ET
    tree = ET.fromstring(r.content)
    ns = {'d': 'DAV:'}
    files = []
    for resp in tree.findall('d:response', ns):
        href = resp.find('d:href', ns).text
        # 只收集zip文件
        if href.lower().endswith('.zip'):
            # 去掉/dav/前缀
            if href.startswith('/dav/'):
                rel_path = href[len('/dav/'):]
            else:
                rel_path = href.lstrip('/')
            # 去掉 remote_dir 前缀
            base = remote_dir.strip('/') + '/'
            if rel_path.startswith(base):
                rel_path = rel_path[len(base):]
            # 只保留 python-upload/ 下的相对路径
            if rel_path.startswith('python-upload/'):
                rel_path = rel_path[len('python-upload/'):]
            files.append(rel_path)
    print("DEBUG 最终files:", files)
    return files

def download_webdav_file(remote_path, local_path):
    url, user, pwd = get_webdav_config()
    full_url = f"{url}/{remote_path}"
    r = requests.get(full_url, auth=(user, pwd), verify=False, proxies={"http": None, "https": None})
    if r.status_code == 200:
        with open(local_path, "wb") as f:
            f.write(r.content)
        return True
    else:
        print("下载失败，状态码：", r.status_code)
        return False

def perform_backup(path, game_name, remark, backup_path):
    """执行备份：保留父目录，记录完整路径，打包并上传到 WebDAV (requests)"""
    try:
        timestamp = datetime.now().strftime("%Y %m%d %H%M%S")
        system = "Windows11"
        if remark:
            backup_name = f"{remark}{game_name}-{timestamp}-{system}.zip"
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
        }
        def replace_with_env_vars(p):
            for var, val in sorted(env_map.items(), key=lambda x: -len(str(x[1]))):
                if val and p.startswith(val):
                    return p.replace(val, var, 1)
            return p

        path_for_backup = replace_with_env_vars(path)
        with open(backup_path_file, "w", encoding="utf-8") as f:
            f.write(path_for_backup)
        # 3. 打包 backup_path.txt 和存档目录（并列在 zip 根目录）
        with zipfile.ZipFile(local_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.join(dir_name, os.path.relpath(file_path, path))
                    zipf.write(file_path, arcname)
            zipf.write(backup_path_file, "backup_path.txt")
        os.remove(backup_path_file)

        print(f"正在上传备份文件: {remote_path}")
        success = upload_webdav_file(remote_path, local_zip)
        if success:
            print("备份完成")
            os.remove(local_zip)
        else:
            print("备份失败：上传失败")
    except Exception as e:
        print(f"备份失败：{e}")
        return

def dir_exists(client, path):
    """用 list() 判断目录是否存在，兼容坚果云"""
    try:
        parent = os.path.dirname(path) or '/'
        items = client.list(parent)
        folder_name = os.path.basename(path.rstrip('/')) + '/'
        return folder_name in items
    except Exception as e:
        print(f"检查目录 {path} 是否存在时出错: {e}")
        return False

def configure_webdav():
    """弹窗集中输入WebDAV参数，账号密码简单加密保存本地"""
    global config
    dialog = tk.Toplevel(root)
    dialog.title("WebDAV 配置")
    dialog.grab_set()
    tk.Label(dialog, text="WebDAV 主机 URL:").grid(row=0, column=0, sticky="e", padx=5, pady=5)
    tk.Label(dialog, text="用户名:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
    tk.Label(dialog, text="密码:").grid(row=2, column=0, sticky="e", padx=5, pady=5)
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
            messagebox.showerror("错误", "WebDAV 主机和用户名不能为空！")
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
        messagebox.showinfo("配置", "WebDAV 配置已保存。")
        dialog.destroy()

    tk.Button(dialog, text="保存", command=save).grid(row=3, column=0, columnspan=2, pady=10)

def list_backups():
    """递归获取 python-upload/ 下所有 ZIP 文件，并显示在远程列表框（requests实现）"""
    def walk_webdav_zip_files(remote_dir, files):
        url, user, pwd = get_webdav_config()
        # remote_dir 不能有多余的斜杠
        remote_dir = remote_dir.strip("/")
        full_url = f"{url}/{remote_dir}" if remote_dir else url
        headers = {"Depth": "1"}
        body = '''<?xml version="1.0" encoding="utf-8" ?>
        <propfind xmlns="DAV:"><prop><resourcetype/></prop></propfind>'''
        r = requests.request(
            "PROPFIND", full_url,
            data=body.encode("utf-8"),
            headers={**headers, "Content-Type": "application/xml"},
            auth=(user, pwd), verify=False, proxies={"http": None, "https": None}
        )
        if r.status_code not in (207, 200):
            print(f"列目录失败，状态码：{r.status_code}，路径：{full_url}")
            return
        
        from xml.etree import ElementTree as ET
        import urllib.parse
        tree = ET.fromstring(r.content)
        ns = {'d': 'DAV:'}
        
        for resp in tree.findall('d:response', ns):
            href = resp.find('d:href', ns).text
            # 处理URL编码的路径
            href = urllib.parse.unquote(href)
            # 标准化路径格式
            if href.startswith('/'):
                href = href[1:]
            # 跳过自身目录
            if href.rstrip('/') == remote_dir.rstrip('/'):
                continue
            # 判断是否为目录
            is_dir = resp.find('d:propstat/d:prop/d:resourcetype/d:collection', ns) is not None
            
            if is_dir:
                # 递归处理子目录
                walk_webdav_zip_files(href, files)
            elif href.lower().endswith('.zip'):
                # 处理ZIP文件路径
                base = "python-upload/"
                if href.startswith(base):
                    rel_file = href[len(base):]
                    files.append(rel_file)
                    print(f"发现zip文件: {href}")

    try:
        files = []
        walk_webdav_zip_files("python-upload", files)
        print("最终files:", files)
        listbox_remote.delete(0, tk.END)
        for f in files:
            listbox_remote.insert(tk.END, f)
    except Exception as e:
        messagebox.showerror("错误", e)
        print(f"获取备份列表失败: {e}")
        
def restore_selected():
    """下载选中的备份 ZIP，读取 backup_path.txt 并恢复文件"""
    sel = listbox_remote.curselection()
    if not sel:
        return
    entry = listbox_remote.get(sel[0])
    game, zipname = entry.split('/', 1)
    remote_path = f"python-upload/{entry}"
    local_zip = os.path.join(os.getcwd(), zipname)
    success = download_webdav_file(remote_path, local_zip)
    if not success:
        messagebox.showerror("错误", f"下载失败: {remote_path}")
        return
    with zipfile.ZipFile(local_zip, 'r') as z:
        # 读取 backup_path.txt
        path_txt = z.read("backup_path.txt").decode("utf-8").strip()
        restored_path = os.path.expandvars(path_txt)
        # 找到存档目录名
        all_names = z.namelist()
        dir_names = [n.split('/')[0] for n in all_names if '/' in n and not n.startswith('__MACOSX')]
        if not dir_names:
            messagebox.showerror("错误", "备份包中未找到存档目录")
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
        # 获取zip文件修改时间
        zip_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(local_zip)))
        # 弹窗确认
        msg = (
            f"存档目录名: {archive_dir}\n"
            f"文件数: {file_count}\n"
            f"总大小: {total_size/1024:.2f} KB\n"
            f"备份时间: {zip_time}\n"
            f"原路径: {restored_path}\n\n"
            "是否确认还原？"
        )
        if not messagebox.askokcancel("还原确认", msg):
            return
        save_dir = os.path.join(os.path.dirname(restored_path), archive_dir)
        # 解压存档目录到目标路径
        for member in all_names:
            if member.startswith(archive_dir + "/"):
                z.extract(member, os.path.dirname(restored_path))
        messagebox.showinfo("还原完成", f"存档已还原到: {restored_path}")

# ----------- Tkinter 界面布局 -----------
root = tk.Tk()
root.title("游戏存档备份工具")

# 本地路径列表
listbox = Listbox(root, width=80, height=10)
listbox.pack()
# 远程备份列表
listbox_remote = Listbox(root, width=80, height=5)
listbox_remote.pack()

# 当前选择路径显示
selected_path_var = tk.StringVar()
tk.Label(root, text="当前选择的路径:").pack()
tk.Label(root, textvariable=selected_path_var, wraplength=600).pack()

# 按钮区域
frame = tk.Frame(root)
frame.pack(fill="x", padx=10, pady=5)
tk.Button(frame, text="选择路径分段", command=handle_selected_path).pack(side="left", padx=5)
tk.Button(frame, text="备份到WebDAV", command=backup).pack(side="left", padx=5)
tk.Button(frame, text="刷新备份列表", command=list_backups).pack(side="left", padx=5)
tk.Button(frame, text="还原选定备份", command=restore_selected).pack(side="left", padx=5)
tk.Button(frame, text="配置WebDAV", command=configure_webdav).pack(side="left", padx=5)

# 启动文件系统监视器
path_set = set()
from psutil import disk_partitions
partitions = [p.device for p in disk_partitions()]
observers = []
for path in partitions:
    if "Temp" in path:
        continue
    handler = MyHandler(listbox, path_set)
    observer = Observer()
    observer.schedule(handler, path, recursive=True)
    observer.start()
    observers.append(observer)

listbox.bind("<Double-Button-1>", lambda e: handle_selected_path())

# 主循环
try:
    root.mainloop()
finally:
    for o in observers:
        o.stop()
        o.join()