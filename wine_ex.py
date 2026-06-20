import os
import zipfile
import shutil
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from tkinter import font as tkfont
import threading
import subprocess
import re
import time
import getpass

# ==========================================
# 共用工具函數 (Shared Utilities)
# ==========================================
def get_protontricks_cmd_base():
    local_pt = os.path.expanduser("~/.local/bin/protontricks")
    if shutil.which("protontricks"):
        return ["protontricks"]
    elif os.path.exists(local_pt):
        return [local_pt]

    flatpak_cmd = shutil.which("flatpak") or "/usr/bin/flatpak"
    if os.path.exists(flatpak_cmd):
        try:
            flatpak_list = subprocess.check_output([flatpak_cmd, "list"], text=True)
            if "com.github.Matoking.protontricks" in flatpak_list:
                return [flatpak_cmd, "run", "com.github.Matoking.protontricks"]
        except Exception:
            pass
    return None

def get_shared_appid_list():
    base_cmd = get_protontricks_cmd_base()
    if not base_cmd:
        return []
    
    cmd = base_cmd + ["--list"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except Exception as e:
        print(f"無法讀取 App 清單：{e}")
        return []

    apps = []
    for line in result.stdout.split('\n'):
        match = re.match(r"(.*) \((\d+)\)$", line.strip())
        if match:
            app_name, app_id = match.groups()
            apps.append((app_id, app_name))
    return apps

def get_pfx_dosdevices_path(appid):
    home = Path.home()
    paths = [
        home / ".steam/steam/steamapps/compatdata",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/compatdata"
    ]
    for base in paths:
        target = base / str(appid) / "pfx" / "dosdevices"
        if target.exists(): return target
    return None

def get_game_install_dir(appid):
    home = Path.home()
    steamapps_paths = [
        home / ".steam/steam/steamapps",
        home / ".local/share/Steam/steamapps",
        home / ".var/app/com.valvesoftware.Steam/data/Steam/steamapps"
    ]
    for steamapps in steamapps_paths:
        manifest_path = steamapps / f"appmanifest_{appid}.acf"
        if manifest_path.exists():
            try:
                content = manifest_path.read_text(encoding="utf-8", errors="ignore")
                match = re.search(r'"installdir"\s+"([^"]+)"', content)
                if match:
                    install_dir = match.group(1)
                    return steamapps / "common" / install_dir
            except Exception:
                pass
    return None

# ==========================================
# Tab 2 (原 Tab 1): 解壓縮與補丁安裝 (ZiPatch)
# ==========================================
class ExtractorTab:
    def __init__(self, parent, root, apps):
        self.parent = parent
        self.root = root
        self.apps = apps 
        self.last_y = 0

        username = getpass.getuser()
        self.downloads_path = Path(f"/home/{username}/Downloads")
        self.temp_path = self.downloads_path / "Temp"
        self.default_destination = Path(f"/home/{username}/.local/share/Steam/steamapps/common")
        
        self.current_display_path = self.downloads_path
        self.selected_archive = None
        self.selected_source_subfolder = None
        self.selected_destination = None
        self.is_selecting_source = False
        self.markzip = ""

        self.temp_path.mkdir(exist_ok=True)
        self.default_destination.mkdir(exist_ok=True, parents=True)

        self.create_widgets()
        self.populate_archive_list()

    def create_widgets(self):
        self.button_frame = ttk.Frame(self.parent)
        self.button_frame.pack(side="top", fill="x", padx=10, pady=10)

        self.back_button = ttk.Button(self.button_frame, text="⬆返回上一層", command=self.on_back_click)
        self.open_folder_button = ttk.Button(self.button_frame, text="📁打開資料夾", command=self.on_open_folder_click)
        self.find_game_button = ttk.Button(self.button_frame, text="🎮 自動定位遊戲目錄", command=self.show_find_game_dialog)
        self.confirm_button = ttk.Button(self.button_frame, text="✅確認", command=self.on_confirm_click)
        self.cancel_button = ttk.Button(self.button_frame, text="🔄重置/取消", command=self.on_cancel_click)

        self.reset_buttons()

        self.right_label = ttk.Label(self.button_frame, text="請選擇檔案 (支援exe 需要在桌面模式下安裝 且遊戲至少有啟動過一次)", font=("Arial", 12))
        self.right_label.pack(side="right", padx=5)

        style = ttk.Style()
        style.configure("Custom.Vertical.TScrollbar", width=20)

        self.canvas = tk.Canvas(self.parent)
        self.scrollbar = ttk.Scrollbar(self.parent, orient="vertical", command=self.canvas.yview, style="Custom.Vertical.TScrollbar")
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True, padx=(0, 10), pady=(0, 10))
        self.scrollbar.pack(side="right", fill="y", padx=(0, 10))

        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel)
        self.canvas.bind("<ButtonPress-1>", lambda e: setattr(self, "last_y", e.y))
        self.canvas.bind("<B1-Motion>", self._on_touch_drag)
        self.canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "last_y", 0))

    def populate_archive_list(self):
        archives = sorted([f for f in self.downloads_path.glob("*") if f.suffix.lower() in (".zip", ".rar", ".7z", ".exe")], key=lambda x: x.name.lower())
        for widget in self.scrollable_frame.winfo_children(): widget.destroy()

        if archives:
            for archive in archives:
                label = ttk.Label(self.scrollable_frame, text=archive.name, padding=10)
                label.pack(fill="x")
                label.bind("<Button-1>", lambda event, path=archive: self.on_item_click(path))
        else:
            ttk.Label(self.scrollable_frame, text="無壓縮檔或 EXE", padding=10).pack(fill="x")

    def populate_directory_structure(self, summary):
        for widget in self.scrollable_frame.winfo_children(): widget.destroy()
        try:
            for line in summary.split("\n"):
                if line.strip():
                    label = ttk.Label(self.scrollable_frame, text=line, padding=10)
                    label.pack(fill="x")
                    label.bind("<Button-1>", lambda event, text=line: self.on_item_click_structure(text))
        except Exception as e:
            self.right_label.configure(text="清單顯示失敗")

    def summarize_directory_unzip(self, directory):
        summary = f"📁 當前顯示路徑: {directory}\n"
        first_level_items = sorted(directory.iterdir(), key=lambda x: x.name.lower())
        item_count = 0
        max_items = 100
        for item in first_level_items:
            if item_count >= max_items:
                summary += "...更多項目未顯示\n"
                break
            if item.is_dir():
                summary += f"📂 {item.name}/\n"
                item_count += 1
                for sub_item in sorted(item.iterdir(), key=lambda x: x.name.lower()):
                    if item_count >= max_items: break
                    indent = '    '
                    relative_path = f"{item.name}/{sub_item.name}"
                    if sub_item.is_dir(): summary += f"{indent}📂 {relative_path}/\n"
                    else: summary += f"{indent}📄 {relative_path}\n"
                    item_count += 1
            else:
                summary += f"📄 {item.name}\n"
                item_count += 1
        return summary

    def summarize_directory(self, directory):
        summary = f"📁 當前目錄: {directory}\n"
        for item in sorted(directory.iterdir(), key=lambda x: x.name.lower()):
            if item.is_dir(): summary += f"📂 {item.name}/\n"
            else: summary += f"📄 {item.name}\n"
        return summary

    def update_summary(self, directory):
        self.current_display_path = directory
        summary = self.summarize_directory(directory) if not self.is_selecting_source else self.summarize_directory_unzip(directory)
        self.populate_directory_structure(summary)
        self.canvas.yview_moveto(0.0)
        self.canvas.xview_moveto(0.0)

    def hide_all_top_buttons(self):
        self.back_button.pack_forget()
        self.open_folder_button.pack_forget()
        self.find_game_button.pack_forget()
        self.confirm_button.pack_forget()
        self.cancel_button.pack_forget()

    def check_single_folder(self):
        items = list(self.temp_path.iterdir())
        folders = [i for i in items if i.is_dir()]
        files = [i for i in items if i.is_file()]
        self.hide_all_top_buttons()
        
        if len(items) == 1 and len(folders) == 1:
            self.right_label.configure(text=f"只有一個資料夾，是否刪除第一層資料夾? (點擊📄可執行exe選項)")
            self.confirm_button.configure(text="確定刪除", command=self.process_delyes)
            self.cancel_button.configure(text="不刪除並繼續", command=self.move_file)
            self.open_folder_button.configure(text="自選移動部份", command=self.select_source_folder)
        else:
            self.right_label.configure(text=f"發現 {len(folders)} 資料夾 / {len(files)} 檔案，請選擇 (點擊📄可執行exe選項)")
            self.confirm_button.configure(text="刪除第一層", command=self.process_delyes, state="disabled")
            self.cancel_button.configure(text="繼續", command=self.move_file)
            self.open_folder_button.configure(text="自選移動部份", command=self.select_source_folder)
            
        self.confirm_button.pack(side="left", padx=10)
        self.cancel_button.pack(side="left", padx=10)
        self.open_folder_button.pack(side="left", padx=10)

    def process_delyes(self):
        first = list(self.temp_path.iterdir())[0]
        for item in first.iterdir():
            shutil.move(str(item), str(self.temp_path))
        shutil.rmtree(first)
        summary = self.summarize_directory_unzip(self.temp_path)
        self.populate_directory_structure(summary)
        self.right_label.configure(text=f"{first.name} 已刪除並移出檔案")
        self.hide_all_top_buttons()
        self.root.after(2000, self.move_file)

    def move_file(self):
        self.selected_source_subfolder = self.temp_path
        self.is_selecting_source = False
        self.update_summary(self.default_destination)
        self.create_destination_selection_frame()
        self.right_label.configure(text=f"已選擇: {self.markzip}。請選擇目的地資料夾")

    def move_to_destination(self, destination):
        try:
            source_dir = self.selected_source_subfolder if self.selected_source_subfolder else self.current_display_path
            
            if source_dir and source_dir.exists():
                for item in list(source_dir.iterdir()):
                    dst = Path(destination) / item.name
                    if item.is_dir():
                        if dst.exists() and dst.is_dir():
                            for sub_item in item.iterdir():
                                sub_dst = dst / sub_item.name
                                if sub_item.is_dir():
                                    if sub_dst.exists():
                                        for inner_item in sub_item.rglob("*"):
                                            rel_path = inner_item.relative_to(sub_item)
                                            target_path = sub_dst / rel_path
                                            if inner_item.is_dir(): target_path.mkdir(parents=True, exist_ok=True)
                                            else:
                                                if target_path.exists(): os.remove(target_path)
                                                target_path.parent.mkdir(parents=True, exist_ok=True)
                                                shutil.move(str(inner_item), str(target_path))
                                        shutil.rmtree(sub_item)
                                    else:
                                        shutil.move(str(sub_item), str(sub_dst))
                                else:
                                    if sub_dst.exists(): os.remove(sub_dst)
                                    shutil.move(str(sub_item), str(sub_dst))
                        else:
                            shutil.move(str(item), str(dst), copy_function=shutil.copy2)
                    else:
                        if dst.exists(): os.remove(dst)
                        shutil.move(str(item), str(dst))

            self.clear_temp_path()
            self.right_label.configure(text=f"已移動到 {destination.name}，數秒後重置畫面。")
            self.root.after(2000, self.on_cancel_click)

        except Exception as e:
            self.right_label.configure(text=f"移動失敗: {str(e)}")

    def select_source_folder(self):
        self.is_selecting_source = True
        self.selected_source_subfolder = None
        self.current_display_path = self.temp_path
        self.update_summary(self.temp_path)
        self.right_label.configure(text="請選擇你要移動的部份")
        
        self.hide_all_top_buttons()
        self.open_folder_button.configure(text="打開資料夾", command=self.open_source_folder)
        self.confirm_button.configure(text="選擇並繼續", command=self.confirm_source_folder)
        self.cancel_button.configure(text="取消", command=self.on_cancel_click)
        
        self.back_button.pack(side="left", padx=10)
        self.open_folder_button.pack(side="left", padx=10)
        self.confirm_button.pack(side="left", padx=10)
        self.cancel_button.pack(side="left", padx=10)

    def open_source_folder(self):
        if self.selected_source_subfolder and self.selected_source_subfolder.exists():
            self.current_display_path = self.selected_source_subfolder
            self.update_summary(self.current_display_path)
            self.right_label.configure(text=f"資料夾: {self.current_display_path.name}")
            self.selected_source_subfolder = None
        else:
            self.right_label.configure(text="請先選擇一個資料夾")

    def confirm_source_folder(self):
        source = self.selected_source_subfolder if self.selected_source_subfolder and os.path.exists(self.selected_source_subfolder) else self.current_display_path
        if os.path.exists(source):
            self.selected_source_subfolder = source
            self.is_selecting_source = False
            self.update_summary(self.default_destination)
            self.right_label.configure(text=f"已選擇{os.path.basename(source)}裡面的內容")
            self.create_destination_selection_frame()
        else:
            self.right_label.configure(text="請選擇有效資料夾")

    def show_find_game_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("自動定位 Steam 遊戲目錄")
        dialog.geometry("500x420")
        dialog.transient(self.root)
        
        ttk.Label(dialog, text="請選擇遊戲，系統將自動跳轉到該遊戲目錄：(需啟動過一次)", padding=10).pack(fill=tk.X)
        
        list_frame = ttk.Frame(dialog, padding=10)
        list_frame.pack(fill=tk.BOTH, expand=True)

        entry_frame = ttk.Frame(list_frame)
        entry_frame.pack(fill=tk.X, side=tk.TOP, pady=(0, 5))
        ttk.Label(entry_frame, text="已選 AppID: ").pack(side=tk.LEFT)
        appid_entry = ttk.Entry(entry_frame, width=15, font=("Arial", 11))
        appid_entry.pack(side=tk.LEFT, padx=5)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        app_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, font=("Arial", 11), exportselection=False)
        app_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=app_listbox.yview)
        
        for appid, name in self.apps:
            app_listbox.insert(tk.END, f"{name} ({appid})")
            
        def on_list_select(event):
            selection = app_listbox.curselection()
            if selection:
                appid = self.apps[selection[0]][0]
                appid_entry.delete(0, tk.END)
                appid_entry.insert(0, appid)
        app_listbox.bind("<<ListboxSelect>>", on_list_select)
            
        def on_confirm():
            appid = appid_entry.get().strip()
            if not appid.isdigit():
                messagebox.showwarning("提示", "請輸入有效的 AppID", parent=dialog)
                return
            target_dir = get_game_install_dir(appid)
            if target_dir and target_dir.exists():
                self.current_display_path = target_dir
                self.update_summary(target_dir)
                self.right_label.configure(text=f"已成功跳轉至目錄: {target_dir.name}")
                dialog.destroy()
            else:
                messagebox.showerror("錯誤", "找不到該遊戲的安裝目錄", parent=dialog)
                
        ttk.Button(dialog, text="✅ 確定定位", command=on_confirm).pack(pady=15)
        
        dialog.update_idletasks()
        dialog.focus_force()
        dialog.grab_set()

    def create_destination_selection_frame(self):
        self.hide_all_top_buttons()
        self.back_button.pack(side="left", padx=10)
        self.open_folder_button.configure(text="📁打開資料夾", command=self.on_open_folder_click)
        self.open_folder_button.pack(side="left", padx=10)
        self.find_game_button.pack(side="left", padx=10)
        self.confirm_button.configure(text="✅確認移動", command=self.confirm_destination)
        self.cancel_button.configure(text="🔄取消/重置", command=self.on_cancel_click)
        self.confirm_button.pack(side="left", padx=10)
        self.cancel_button.pack(side="left", padx=10)

    def confirm_destination(self):
        destination = self.selected_destination if self.selected_destination and self.selected_destination.exists() else self.current_display_path
        if destination.exists():
            self.move_to_destination(destination)
        else:
            self.right_label.configure(text="請選擇有效資料夾")

    def on_item_click(self, path):
        self.selected_archive = path
        self.right_label.configure(text=f"已點選: {path.name}")
        self.markzip = path.name

    def on_item_click_structure(self, text):
        clean_text = text.strip()
        if "📂" in clean_text:
            self.right_label.configure(text=f"選擇: {clean_text}")
            folder_path = clean_text.replace("📂 ", "").rstrip("/")
            if "/" in folder_path:
                parent_folder, folder_name = folder_path.split("/", 1)
                if self.is_selecting_source: self.selected_source_subfolder = self.current_display_path / parent_folder / folder_name
                else: self.selected_destination = self.current_display_path / parent_folder / folder_name
            else:
                if self.is_selecting_source: self.selected_source_subfolder = self.current_display_path / folder_path
                else: self.selected_destination = self.current_display_path / folder_path
        elif "📄" in clean_text:
            file_path = clean_text.replace("📄 ", "")
            full_path = self.current_display_path / file_path
            if full_path.suffix.lower() == ".exe":
                self.show_exe_runner_dialog(full_path)
            else:
                self.right_label.configure(text=f"已選擇檔案: {full_path.name}")
                if self.is_selecting_source: self.selected_source_subfolder = None
                else: self.selected_destination = None

    def show_exe_runner_dialog(self, exe_path):
        dialog = tk.Toplevel(self.root)
        dialog.title("安裝環境設定")
        dialog.geometry("1120x700")
        dialog.transient(self.root)
        
        info_text = (
            f"選擇了安裝檔：\n{exe_path.name}\n\n"
            "💡 系統會自動尋找該遊戲的目錄，並掛載為 D槽。\n"
            "安裝路徑請直接選 D:\\ 即可完美覆蓋！"
        )
        ttk.Label(dialog, text=info_text, padding=10, wraplength=750).pack(fill=tk.X)

        content_frame = ttk.Frame(dialog)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        list_frame = ttk.LabelFrame(content_frame, text="請選擇對應的 Steam 遊戲 (AppID)：", padding=10)
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        entry_frame = ttk.Frame(list_frame)
        entry_frame.pack(fill=tk.X, side=tk.TOP, pady=(0, 5))
        ttk.Label(entry_frame, text="已選 AppID: ").pack(side=tk.LEFT)
        appid_entry = ttk.Entry(entry_frame, width=15, font=("Arial", 11))
        appid_entry.pack(side=tk.LEFT, padx=5)
        
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        app_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, font=("Arial", 11), exportselection=False)
        app_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=app_listbox.yview)

        for appid, name in self.apps:
            app_listbox.insert(tk.END, f"{name} ({appid})")

        def on_list_select(event):
            selection = app_listbox.curselection()
            if selection:
                appid = self.apps[selection[0]][0]
                appid_entry.delete(0, tk.END)
                appid_entry.insert(0, appid)
        app_listbox.bind("<<ListboxSelect>>", on_list_select)

        right_frame = ttk.LabelFrame(content_frame, text="操作選項", padding=10)
        right_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))

        def map_d_drive():
            appid = appid_entry.get().strip()
            if not appid.isdigit():
                messagebox.showwarning("提示", "請選擇 AppID！", parent=dialog)
                return False
            
            dosdevices_path = get_pfx_dosdevices_path(appid)
            if dosdevices_path:
                d_drive = dosdevices_path / "d:"
                try:
                    if d_drive.exists() or d_drive.is_symlink(): d_drive.unlink()
                    exact_game_path = get_game_install_dir(appid)
                    target_dir = exact_game_path if (exact_game_path and exact_game_path.exists()) else self.default_destination
                    os.symlink(str(target_dir), str(d_drive))
                    return True
                except Exception as e:
                    messagebox.showerror("錯誤", f"掛載 D 槽發生錯誤: {e}", parent=dialog)
                    return False
            else:
                messagebox.showerror("錯誤", "找不到 Proton 資料夾！", parent=dialog)
                return False

        def open_dolphin():
            if map_d_drive():
                try:
                    target_dir = str(self.temp_path)
                    dolphin_cmd = shutil.which("dolphin") or "/usr/bin/dolphin"
                    xdg_cmd = shutil.which("xdg-open") or "/usr/bin/xdg-open"
                    
                    if os.path.exists(dolphin_cmd): subprocess.Popen([dolphin_cmd, target_dir])
                    elif os.path.exists(xdg_cmd): subprocess.Popen([xdg_cmd, target_dir])
                    else: messagebox.showwarning("提示", "找不到檔案管理員", parent=dialog)
                except Exception as e:
                    messagebox.showerror("錯誤", f"無法開啟目錄: {e}", parent=dialog)

        def finish_and_reset():
            dialog.destroy()
            self.on_cancel_click()

        ttk.Button(right_frame, text="📂 1. 在 Dolphin 開啟補丁(Protontricks)", command=open_dolphin).pack(fill=tk.X, pady=5)
        ttk.Separator(right_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=15)
        ttk.Button(right_frame, text="✅ 2. 完成並回到首頁", command=finish_and_reset).pack(fill=tk.X, pady=5)

    def clear_temp_path(self):
        if not self.temp_path.exists(): return
        for item in self.temp_path.iterdir():
            if item.is_dir(): shutil.rmtree(item)
            else: os.remove(item)

    def extract_archive(self, archive_path, extract_to):
        try:
            self.clear_temp_path()
            if archive_path.endswith(".zip"):
                with zipfile.ZipFile(archive_path, "r") as archive_ref: archive_ref.extractall(extract_to)
            elif archive_path.endswith(".rar"):
                result = subprocess.run(["unrar", "x", "-y", archive_path, extract_to], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if result.returncode != 0: raise Exception(result.stderr)
            elif archive_path.endswith(".7z"):
                result = subprocess.run(["7z", "x", archive_path, f"-o{extract_to}", "-y"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if result.returncode != 0: raise Exception(result.stderr)
            elif archive_path.lower().endswith(".exe"):
                shutil.copy2(archive_path, extract_to)
            else:
                raise ValueError("不支援的檔案格式")

            if not any(Path(extract_to).iterdir()): raise Exception("處理後未找到檔案")
            summary = self.summarize_directory_unzip(self.temp_path)
            self.populate_directory_structure(summary)
            self.check_single_folder()

        except Exception as e: self.right_label.configure(text=f"處理失敗: {str(e)}")

    def on_back_click(self):
        if self.is_selecting_source:
            parent_path = self.current_display_path.parent
            if parent_path == self.downloads_path or parent_path == self.downloads_path.parent:
                self.is_selecting_source = False
                self.selected_source_subfolder = None
                self.current_display_path = self.downloads_path
                self.populate_archive_list()
                self.right_label.configure(text="未選擇檔案")
                self.reset_buttons()
            else:
                self.current_display_path = parent_path
                self.update_summary(self.current_display_path)
                self.right_label.configure(text=f"資料夾: {self.current_display_path.name}")
                self.selected_source_subfolder = None
        else:
            self.current_display_path = self.current_display_path.parent
            self.update_summary(self.current_display_path)
            self.right_label.configure(text=f"資料夾: {self.current_display_path.name}")
            self.selected_archive = None
            self.selected_destination = None
            self.reset_buttons()

    def on_open_folder_click(self):
        if self.selected_destination and self.selected_destination.exists():
            self.current_display_path = self.selected_destination
            self.update_summary(self.current_display_path)
            self.right_label.configure(text=f"資料夾: {self.current_display_path.name}")
            self.selected_archive = None
            self.selected_destination = None
            self.reset_buttons()

    def on_confirm_click(self):
        if self.selected_archive:
            self.right_label.configure(text="處理中...請稍候")
            self.root.update()
            try: self.extract_archive(str(self.selected_archive), str(self.temp_path))
            except Exception: pass 
        self.confirm_button.configure(state="normal")

    def on_cancel_click(self):
        self.clear_temp_path()
        self.is_selecting_source = False
        self.selected_archive = None
        self.selected_source_subfolder = None
        self.selected_destination = None
        self.current_display_path = self.downloads_path
        self.populate_archive_list()
        self.right_label.configure(text="已重置，請選擇檔案")
        self.reset_buttons()

    def reset_buttons(self):
        self.hide_all_top_buttons()
        self.confirm_button.configure(text="✅確認", command=self.on_confirm_click)
        self.cancel_button.configure(text="🔄重置/取消", command=self.on_cancel_click)
        self.confirm_button.pack(side="left", padx=10)
        self.cancel_button.pack(side="left", padx=10)

    def _on_mousewheel(self, event):
        if event.num == 5 or event.delta < 0: self.canvas.yview_scroll(1, "units")
        elif event.num == 4 or event.delta > 0: self.canvas.yview_scroll(-1, "units")

    def _on_touch_drag(self, event):
        current_y = event.y
        delta = current_y - self.last_y
        self.canvas.yview_scroll(int(-delta / 10), "units")
        self.last_y = current_y


# ==========================================
# Tab 1 (原 Tab 2): 三欄式環境修正與 DLL 安裝 (3-Column Layout)
# ==========================================
class UnifiedFixerTab:
    def __init__(self, parent, root, apps):
        self.parent = parent
        self.root = root
        self.apps = apps
        self.installing = False
        self.start_time = 0
        self.current_dll = ""
        self.base_cmd = get_protontricks_cmd_base()

        self.tool_var = tk.StringVar(value="auto_dll")
        self.option_var = tk.IntVar(value=1)
        self.close_after_install_var = tk.BooleanVar(value=True) 
        
        self.custom_options_list = ["wmp11", "wmp9", "directshow", "lavfilters", "quartz", "mf", "xaudio29", "xact"]
        self.custom_vars = {opt: tk.BooleanVar(value=False) for opt in self.custom_options_list}
        
        self.shell_options_list = ["wmp11", "quartz_dx", "quartz2", "mf", "xaudio29", "mciqtz32", "lavfilters"]
        self.shell_vars = {opt: tk.BooleanVar(value=False) for opt in self.shell_options_list}
        
        self.create_widgets()

    def create_widgets(self):
        # 採用 PanedWindow 製作 3 欄式佈局
        self.paned_window = ttk.PanedWindow(self.parent, orient=tk.HORIZONTAL)
        self.paned_window.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # --- 第一欄：選擇遊戲 (權重設為 2，佔約 40%) ---
        self.col1 = ttk.LabelFrame(self.paned_window, text="1. 選擇 Steam 遊戲", padding=10)
        self.paned_window.add(self.col1, weight=2)

        entry_frame = ttk.Frame(self.col1)
        entry_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(entry_frame, text="輸入 AppID:").pack(side=tk.LEFT)
        self.app_id_entry = ttk.Entry(entry_frame, width=10, font=("Arial", 11))
        self.app_id_entry.pack(side=tk.LEFT, padx=5)

        scrollbar = ttk.Scrollbar(self.col1)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        # 加入 width=10，打破 Listbox 預設的寬度限制
        self.app_listbox = tk.Listbox(self.col1, font=("Arial", 11), width=10, yscrollcommand=scrollbar.set, exportselection=False)
        self.app_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.app_listbox.yview)
        self.app_listbox.bind("<<ListboxSelect>>", self.on_app_selected)

        for appid, app_name in self.apps:
            self.app_listbox.insert(tk.END, f"{app_name} ({appid})")

        # --- 第二欄：選擇修正項目 (權重設為 1，佔約 20%) ---
        self.col2 = ttk.LabelFrame(self.paned_window, text="2. 選擇修正項目", padding=10)
        self.paned_window.add(self.col2, weight=1)

        tools = [
            ("⚙️ 安裝 Protontricks DLL", "auto_dll"),
            ("⌨️ 產生自訂 Shell 指令\n(針對特定修正檔案)", "shell_script"),
            ("🔧 修正字體啟動選項", "font"),
            ("🗑️ 刪除 Proton 遊戲數據", "reset_prefix")
        ]
        
        for text, val in tools:
            rb = ttk.Radiobutton(self.col2, text=text, value=val, variable=self.tool_var, command=self.update_col3)
            rb.pack(anchor=tk.W, pady=12)

        # --- 第三欄：執行項目 (動態變更區，權重設為 2，佔約 40%) ---
        self.col3 = ttk.LabelFrame(self.paned_window, text="3. 執行項目", padding=10)
        self.paned_window.add(self.col3, weight=2)

        # 啟動時先載入預設的第三欄介面
        self.update_col3()

    def on_app_selected(self, event):
        index = self.app_listbox.curselection()
        if not index: return
        appid = self.apps[index[0]][0]
        self.app_id_entry.delete(0, tk.END)
        self.app_id_entry.insert(0, appid)

    def find_compatdata_path(self, appid):
        home = Path.home()
        paths = [
            home / ".steam/steam/steamapps/compatdata",
            home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/compatdata"
        ]
        for base in paths:
            target = base / appid / "pfx/drive_c"
            if target.exists(): return target
        return None

    # ================= 第三欄動態生成邏輯 =================
    def update_col3(self):
        """根據第二欄的選擇，清空並重新建立第三欄的介面"""
        for widget in self.col3.winfo_children():
            widget.destroy()
            
        selection = self.tool_var.get()
        
        if selection == "auto_dll":
            self.col3.config(text="3. 執行項目 (DLL 與解碼器安裝)")
            self.build_auto_dll_ui()
        elif selection == "shell_script":
            self.col3.config(text="3. 執行項目 (自訂 DLL 修正檔選項)")
            self.build_shell_script_ui()
        elif selection == "font":
            self.col3.config(text="3. 執行項目 (字體修正)")
            self.build_font_ui()
        elif selection == "reset_prefix":
            self.col3.config(text="3. 執行項目 (重置與刪除)")
            self.build_reset_ui()

    # --- 介面 1: 自動安裝 DLL ---
    def build_auto_dll_ui(self):
        options_frame = ttk.Frame(self.col3)
        options_frame.pack(fill=tk.X, pady=(0, 10))

        # 列 1：預設組合
        ttk.Label(options_frame, text="推薦組合：", font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(0, 5))
        ttk.Radiobutton(options_frame, text="Wmp11 + DirectShow (萬用)", variable=self.option_var, value=1, command=self.clear_custom).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(options_frame, text="Wmp9 + DirectShow (穩定)", variable=self.option_var, value=2, command=self.clear_custom).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(options_frame, text="Lavfilters + Quartz (最快)", variable=self.option_var, value=3, command=self.clear_custom).pack(anchor=tk.W, pady=2)

        ttk.Separator(options_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        # 列 2：自訂組合
        ttk.Radiobutton(options_frame, text="自選組合：", variable=self.option_var, value=4).pack(anchor=tk.W, pady=(0, 5))
        
        custom_frame = ttk.Frame(options_frame)
        custom_frame.pack(fill=tk.X, padx=10)
        
        for i, opt in enumerate(self.custom_options_list):
            var = self.custom_vars[opt]
            cb = ttk.Checkbutton(custom_frame, text=opt, variable=var, command=lambda: self.option_var.set(4))
            cb.grid(row=i // 3, column=i % 3, sticky=tk.W, padx=10, pady=4)

        action_frame = ttk.Frame(self.col3)
        action_frame.pack(fill=tk.X, pady=10)
        
        ttk.Checkbutton(action_frame, text="安裝結束後會關閉程式", variable=self.close_after_install_var).pack(side=tk.LEFT, padx=5)
        self.install_button = ttk.Button(action_frame, text="▶️ 執行安裝", command=self.install_dlls)
        self.install_button.pack(side=tk.RIGHT, padx=5)

        console_frame = ttk.LabelFrame(self.col3, text="狀態控制台", padding=5)
        console_frame.pack(fill=tk.BOTH, expand=True)
        self.status_timer_label = ttk.Label(console_frame, text="等待開始 | 已用時間: 00:00", font=("Arial", 10, "bold"))
        self.status_timer_label.pack(anchor="w", pady=(0, 5))
        
        # 【關鍵修復】: 加入 width=10，強制打破文字框的預設 80 字元寬度限制
        self.result_text = scrolledtext.ScrolledText(console_frame, height=5, width=10, font=("Consolas", 10), bg="#1e1e1e", fg="#d4d4d4")
        self.result_text.pack(fill=tk.BOTH, expand=True)

    def clear_custom(self):
        for var in self.custom_vars.values():
            var.set(False)

    # --- 介面 2: 自訂 Shell 指令 (原 Gal Fix 輔助 + 複製 VN 修正檔案) ---
    def build_shell_script_ui(self):
        info_label = ttk.Label(self.col3, text="若是需要使用 vn_winestuff-main 自訂腳本修正，請依序執行下列操作：", padding=5)
        info_label.pack(anchor="w")
        
        cb_frame = ttk.Frame(self.col3)
        cb_frame.pack(fill=tk.X, pady=10)
        
        for i, opt in enumerate(self.shell_options_list):
            var = self.shell_vars[opt]
            cb = ttk.Checkbutton(cb_frame, text=opt, variable=var)
            cb.grid(row=i // 3, column=i % 3, sticky=tk.W, padx=10, pady=5)

        btn_frame = ttk.Frame(self.col3)
        btn_frame.pack(fill=tk.X, pady=15)
        
        ttk.Button(btn_frame, text="📁 1. 複製 VN 修正檔案", command=self.execute_copy_vn).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📋 2. 複製指令", command=self.copy_shell_command).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="💻 3. 開啟 Proton Shell", command=self.open_proton_shell).pack(side=tk.LEFT, padx=5)

    def execute_copy_vn(self):
        appid = self.app_id_entry.get().strip()
        if not appid.isdigit():
            messagebox.showwarning("格式錯誤", "請先選擇有效的 AppID")
            return
            
        steam_path = self.find_compatdata_path(appid)
        if not steam_path:
            messagebox.showerror("錯誤", "找不到該遊戲的 compatdata 資料夾。")
            return

        script_dir = Path(__file__).resolve().parent
        src_path = script_dir / "vn_winestuff-main"
        if not src_path.exists():
            messagebox.showerror("錯誤", f"找不到資料夾：\n{src_path}")
            return

        try:
            for item in src_path.iterdir():
                dest = steam_path / item.name
                if item.is_dir(): shutil.copytree(item, dest, dirs_exist_ok=True)
                else: shutil.copy2(item, dest)
            messagebox.showinfo("完成", f"修正檔案已成功複製到：\n{steam_path}")
        except Exception as e:
            messagebox.showerror("複製錯誤", str(e))

    def copy_shell_command(self):
        selected = [opt for opt, var in self.shell_vars.items() if var.get()]
        cmd_text = "sh codec.sh " + " ".join(selected)
        self.root.clipboard_clear()
        self.root.clipboard_append(cmd_text)
        self.root.update()
        messagebox.showinfo("已複製", f"已複製到剪貼簿：\n{cmd_text}")

    def open_proton_shell(self):
        appid = self.app_id_entry.get().strip()
        if not appid.isdigit():
            messagebox.showwarning("格式錯誤", "請先輸入有效的 AppID")
            return
            
        base_cmd = get_protontricks_cmd_base()
        if not base_cmd:
            messagebox.showerror("錯誤", "無法偵測 protontricks 或 flatpak")
            return
            
        cmd = base_cmd + [appid, "shell"]
        terminal_cmd = None
        # 針對 Arch Linux 優先呼叫 konsole
        if shutil.which("konsole"): terminal_cmd = ["konsole", "-e"] + cmd
        elif shutil.which("x-terminal-emulator"): terminal_cmd = ["x-terminal-emulator", "-e"] + cmd
        elif shutil.which("gnome-terminal"): terminal_cmd = ["gnome-terminal", "--"] + cmd
        elif shutil.which("xfce4-terminal"): terminal_cmd = ["xfce4-terminal", "-e", " ".join(cmd)]
        
        if terminal_cmd:
            subprocess.Popen(terminal_cmd)
            messagebox.showinfo("提示", "已開啟 Proton Shell。\n請在終端機貼上剛剛複製的指令，安裝完成後請手動關閉終端機。")
        else:
            messagebox.showerror("找不到終端機", "找不到可用的終端機程式，請手動執行：\n" + " ".join(cmd))

    # --- 介面 3: 字體修正 ---
    def build_font_ui(self):
        ttk.Label(self.col3, text="產生適用於遊戲的字體修正設定檔 (.fonts-wine.conf)", padding=10).pack(anchor="w")
        ttk.Button(self.col3, text="🔧 建立設定檔並複製啟動參數", command=self.execute_fontconfig).pack(pady=20, padx=10, anchor="w")

    def execute_fontconfig(self):
        script_dir = Path(__file__).resolve().parent
        fonts_dir = script_dir / "fonts"
        fonts_dir.mkdir(parents=True, exist_ok=True)
        conf_path = fonts_dir / ".fonts-wine.conf"
        conf_content = f"""<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig>\n  <dir>{str(fonts_dir)}</dir>\n  <config>\n    <rescan>0</rescan>\n  </config>\n</fontconfig>\n"""
        conf_path.write_text(conf_content, encoding="utf-8")
        
        cmd_text = f'FONTCONFIG_FILE="{conf_path}" %COMMAND%'
        self.root.clipboard_clear()
        self.root.clipboard_append(cmd_text)
        self.root.update()
        messagebox.showinfo("完成", "已建立 .fonts-wine.conf！\n啟動指令已複製到剪貼簿，請至 Steam 貼上至啟動選項中。")

    # --- 介面 4: 刪除 Proton 遊戲數據 ---
    def build_reset_ui(self):
        ttk.Label(self.col3, text="將會刪除該遊戲的所有 Proton 設定檔 (Prefix)。\n警告：如果存檔未支援雲端且存在於 Prefix 內將會遺失！", padding=10, foreground="red").pack(anchor="w")
        ttk.Button(self.col3, text="🗑️ 確認刪除資料", command=self.execute_delete_prefix).pack(pady=20, padx=10, anchor="w")

    def execute_delete_prefix(self):
        app_id = self.app_id_entry.get().strip()
        if not appid.isdigit():
            messagebox.showerror("錯誤", "App ID 必須是數字。")
            return

        confirm = messagebox.askyesno("警告", f"確定要刪除 App ID {app_id} 的資料夾？")
        if not confirm: return

        paths = [
            Path.home() / f".steam/steam/steamapps/compatdata/{app_id}",
            Path.home() / f".local/share/Steam/steamapps/compatdata/{app_id}",
            Path.home() / f".var/app/com.valvesoftware.Steam/data/Steam/steamapps/compatdata/{app_id}"
        ]
        
        for path in paths:
            if path.exists():
                shutil.rmtree(path)
                messagebox.showinfo("完成", f"已成功刪除：\n{path}")
                return
        messagebox.showwarning("注意", "找不到對應的 compatdata 資料夾。")

    # ================= DLL 安裝執行邏輯 =================
    def safe_update_text(self, text):
        """保證在多執行緒下安全寫入 UI 的文字更新函數"""
        def _append():
            if hasattr(self, 'result_text') and self.result_text.winfo_exists():
                self.result_text.insert(tk.END, text)
                self.result_text.see(tk.END)
        self.root.after(0, _append)

    def run_protontricks_command(self, app_id, dll):
        cmd_str = " ".join(self.base_cmd + [app_id, "--force", "-q", dll])
        try:
            process = subprocess.Popen(cmd_str, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
            for line in process.stdout: 
                self.safe_update_text(line)
            process.wait(timeout=300)
            return f"✔️ {dll} 執行完成\n"
        except subprocess.TimeoutExpired: 
            return f"❌ 安裝 {dll} 超時\n"
        except Exception as e: 
            return f"❌ 發生錯誤：{str(e)}\n"

    def update_timer_and_status(self):
        elapsed_time = time.time() - self.start_time
        minutes, seconds = divmod(int(elapsed_time), 60)
        status_text = f"⚙️ 正在安裝 : {self.current_dll}" if self.installing else "等待開始"
        if hasattr(self, 'status_timer_label') and self.status_timer_label.winfo_exists():
            self.status_timer_label.config(text=f"{status_text} | 已用時間: {minutes:02d}:{seconds:02d}")
        if self.installing:
            self.root.after(1000, self.update_timer_and_status)

    def install_dlls_thread(self):
        app_id = self.app_id_entry.get().strip()
        if not appid.isdigit():
            self.safe_update_text("錯誤：App ID 必須是一個數字。\n")
            self.root.after(0, lambda: self.install_button.config(state=tk.NORMAL, text="▶️ 執行安裝"))
            self.installing = False
            return

        def _clear_and_start():
            if hasattr(self, 'result_text') and self.result_text.winfo_exists():
                self.result_text.delete(1.0, tk.END)
                self.result_text.insert(tk.END, f"🚀 開始安裝環境...\n")
                self.result_text.see(tk.END)
        self.root.after(0, _clear_and_start)

        selected = self.option_var.get()
        
        if selected == 1: dlls = ["wmp11", "directshow"]
        elif selected == 2: dlls = ["wmp9", "directshow"]
        elif selected == 3: dlls = ["lavfilters", "quartz"]
        else:
            dlls = [dll for dll, var in self.custom_vars.items() if var.get()]
            if not dlls:
                self.safe_update_text("錯誤：請至少勾選一項 DLL。\n")
                self.root.after(0, lambda: self.install_button.config(state=tk.NORMAL))
                self.installing = False
                return

        for dll in dlls:
            self.current_dll = dll
            self.safe_update_text(f"\n[{dll}] 準備安裝...\n")
            
            output = self.run_protontricks_command(app_id, dll)
            self.safe_update_text(output)

        self.safe_update_text("\n✅ 所有選定環境安裝完成！\n")
        self.installing = False
        
        if self.close_after_install_var.get():
            self.safe_update_text("\n即將關閉程式...\n")
            self.root.after(1500, self.root.destroy)
        else:
            self.root.after(0, lambda: self.install_button.config(state=tk.NORMAL, text="▶️ 執行安裝"))

    def install_dlls(self):
        if not self.base_cmd:
            messagebox.showerror("錯誤", "未找到 protontricks！")
            return
            
        self.install_button.config(state=tk.DISABLED, text="⏳ 安裝中...")
        self.installing = True
        self.start_time = time.time()
        self.update_timer_and_status()
        threading.Thread(target=self.install_dlls_thread, daemon=True).start()

# ==========================================
# 主程式入口 (Main App)
# ==========================================
class UnifiedApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Galgame 工具箱")
        
        # 嘗試最大化 (適應 Gamescope 環境)
        try:
            self.root.state("zoomed")
        except Exception:
            w, h = root.winfo_screenwidth(), root.winfo_screenheight()
            self.root.geometry(f"{w}x{h}")

        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(size=11)
        self.root.option_add("*Font", default_font)
        
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        shared_apps = get_shared_appid_list()

        # 初始化兩個 Tab
        self.tab1 = ttk.Frame(self.notebook)
        self.tab2 = ttk.Frame(self.notebook)

        # 互換顯示標籤的順序：預設開啟「遊戲環境修正」
        self.notebook.add(self.tab1, text="🔧 遊戲環境修正與 DLL 安裝")
        self.notebook.add(self.tab2, text="📦 解壓縮與補丁安裝")

        # 把對應的功能綁到正確的 Tab 上
        self.unified_fixer_app = UnifiedFixerTab(self.tab1, self.root, shared_apps)
        self.extractor_app = ExtractorTab(self.tab2, self.root, shared_apps)

if __name__ == "__main__":
    root = tk.Tk()
    app = UnifiedApp(root)
    root.mainloop()