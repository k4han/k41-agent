import os
for path in ['agent/delivery/http/dashboard/templates/index.html', 
             'agent/delivery/http/dashboard/templates/channels.html', 
             'agent/delivery/http/dashboard/templates/config.html']:
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # The actual replacements
    content = content.replace('href="/"', 'href="/dashboard/"')
    content = content.replace('href="/channels"', 'href="/dashboard/channels"')
    content = content.replace('href="/config"', 'href="/dashboard/config"')
    content = content.replace('href="/change-password"', 'href="/dashboard/change-password"')
    content = content.replace('href="/logout"', 'href="/dashboard/logout"')

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
