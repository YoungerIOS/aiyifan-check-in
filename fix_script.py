with open("main.py", "r") as f: lines = f.readlines()
lines[1832] = "        except Exception as e:\n"
lines[1833] = "            print(f\"❌ 执行过程中发生严重错误: {str(e)}\")\n"
with open("main.py.fixed", "w") as f: f.writelines(lines)
