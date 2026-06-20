#!/bin/bash

# 設置 Python 路徑（如果需要）
export PATH="/usr/bin:$PATH"

# 切換到腳本所在目錄
cd "/path/to/your/script/directory"

# 運行 Python 腳本
python3 wine_ex.py

# 運行遊戲（假設 %command% 是 Steam 的遊戲啟動命令）
eval $@