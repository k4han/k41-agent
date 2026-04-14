import os
for path in ['agent/delivery/http/dashboard/templates/index.html', 
             'agent/delivery/http/dashboard/templates/channels.html', 
             'agent/delivery/http/dashboard/templates/config.html']:
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Normalize legacy prefixed links back to root-based dashboard routes.
    content = content.replace('href="/dashboard/"', 'href="/"')
    content = content.replace('href="/dashboard/channels"', 'href="/channels"')
    content = content.replace('href="/dashboard/config"', 'href="/config"')
    content = content.replace('href="/dashboard/change-password"', 'href="/change-password"')
    content = content.replace('href="/dashboard/logout"', 'href="/logout"')
    content = content.replace('href="/dashboard/scheduler"', 'href="/scheduler"')

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
