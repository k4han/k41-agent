"""Prepare detailed builtin permissions before any batch side effect."""


async def prepare_builtin(runtime, name, arguments):
    if name.startswith("browser_"):
        from agent.modules.tools.builtin import browser
        browser.identity(runtime)
        if name == "browser_snapshot":
            arguments = {"include_iframes": arguments.get("include_iframes", True),
                         **({"full": True} if arguments.get("full") else {})}
        elif name == "browser_run":
            arguments = ({"code": arguments["code"]} if arguments.get("code") is not None
                         else {"actions": arguments.get("actions")})
        browser.authorize(runtime, name, arguments)
        return
    if name.startswith("computer_"):
        from agent.modules.tools.builtin import computer
        from agent.modules.tools.runtime.targets import resolve_target
        target = await resolve_target(runtime, arguments.get("target"))
        if name == "computer_action":
            for step in computer._parse_steps(arguments["actions"]):
                computer._authorize_step(runtime, target, step)
        if name == "computer_screenshot" or arguments.get("screenshot", True):
            computer._authorize(runtime, "computer_screenshot", target.locator)
        return
    if name.startswith("instabox_"):
        from agent.modules.tools.builtin import instabox as box
        values = {}
        if name in {"instabox_machine_list", "instabox_snapshot_list"}:
            resource = "*"
        elif name == "instabox_machine_create":
            resource = arguments.get("name") or "*"
            values = {key: arguments.get(key) for key in ("name", "template", "snapshot_id", "purpose")}
        elif name in {"instabox_snapshot_get", "instabox_snapshot_delete"}:
            resource = arguments["snapshot_id"]
            if name.endswith("delete"):
                values = {"confirm": arguments["confirm"]}
        else:
            target = await box._target(runtime, arguments.get("machine_id"))
            resource = target.locator
            if name == "instabox_machine_fork":
                values = {key: arguments.get(key) for key in ("name", "purpose")}
            elif name == "instabox_machine_delete":
                values = {"confirm": arguments["confirm"]}
            elif name == "instabox_snapshot_create":
                values = {"name": arguments["name"]}
            elif name == "instabox_snapshot_restore":
                resource += "<-" + arguments["snapshot_id"]
            elif name.startswith("instabox_port_") and "port" in arguments:
                resource += ":" + str(arguments["port"])
                if name in {"instabox_port_publish", "instabox_port_set_access"}:
                    access = arguments.get("access", "private")
                    box._authorize_access(runtime, name, resource, access)
                    return
        box._authorize(runtime, name, resource, values)
        return
    if name == "copy_files":
        # Import the module explicitly; builtin exports may expose the tool instead.
        from importlib import import_module
        tool_module = import_module("agent.modules.tools.builtin.copy_files")
        from agent.modules.tools.runtime.targets import resolve_target
        source = tool_module._Side(await resolve_target(runtime, arguments.get("source_target")),
                                   tool_module._relative(arguments["source_path"], "source_path"), runtime)
        destination = tool_module._Side(await resolve_target(runtime, arguments.get("destination_target")),
                                        tool_module._relative(arguments["destination_path"], "destination_path"), runtime)
        tool_module._authorize(runtime, source, destination)
        kind, data = await tool_module._read(source)
        members = tool_module._members(data) if kind == "directory" else None
        await tool_module._authorize_members(source, destination, [entry[0] for entry in members] if members is not None else [""])
