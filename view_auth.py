import ast
with open("agent/modules/users/application/auth.py", "r", encoding="utf-8") as f:
    for line in f:
        if "get_current_admin" in line or "Redirect" in line:
            print(line.strip())
