import html
import re

def escape_html(text: str) -> str:
    """Escape text to prevent it from being parsed as Telegram HTML."""
    return html.escape(text, quote=True)

def format_telegram_message(text: str) -> str:
    """
    Convert Markdown to Telegram's HTML format safely.
    Telegram allows: <b>, <strong>, <i>, <em>, <u>, <ins>, <s>, <strike>, <del>, 
    <span class="tg-spoiler">, <tg-spoiler>, <a>, <code>, <pre>, <blockquote expandable>
    """
    code_blocks = []
    
    # Extract block code: ```language\ncode\n```
    def save_code_block(match):
        lang = match.group(1) or ""
        code = match.group(2)
        code = escape_html(code)
        idx = len(code_blocks)
        code_blocks.append(f'<pre><code class="language-{lang}">{code}</code></pre>')
        return f"__CODE_BLOCK_{idx}__"
        
    text = re.sub(r'```([a-zA-Z0-9_\-\+]*)\n(.*?)```', save_code_block, text, flags=re.DOTALL)
    
    # Extract inline code blocks: ```code```
    def save_inline_code_block(match):
        code = match.group(1)
        code = escape_html(code)
        idx = len(code_blocks)
        code_blocks.append(f'<pre><code>{code}</code></pre>')
        return f"__CODE_BLOCK_{idx}__"
        
    text = re.sub(r'```(.*?)```', save_inline_code_block, text, flags=re.DOTALL)

    inline_codes = []
    # Extract inline code: `code`
    def save_inline_code(match):
        code = match.group(1)
        code = escape_html(code)
        idx = len(inline_codes)
        inline_codes.append(f'<code>{code}</code>')
        return f"__INLINE_CODE_{idx}__"
    text = re.sub(r'`([^`]+)`', save_inline_code, text)

    # Escape HTML special chars for the rest of the text
    text = escape_html(text)

    # Headers (convert # to bold)
    text = re.sub(r'^#+\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)

    # Bold: **bold**
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)

    # Italic: *italic*
    text = re.sub(r'(?<!\w)\*(.*?)\*(?!\w)', r'<i>\1</i>', text)
    
    # Strikethrough: ~~strike~~
    text = re.sub(r'~~(.*?)~~', r'<s>\1</s>', text)

    # Links: [text](url)
    text = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', text)

    # Restore inline codes
    for i, code in enumerate(inline_codes):
        text = text.replace(f"__INLINE_CODE_{i}__", code)

    # Restore code blocks
    for i, block in enumerate(code_blocks):
        text = text.replace(f"__CODE_BLOCK_{i}__", block)

    return text

def chunk_telegram_message(text: str, max_len: int = 4000) -> list[str]:
    """
    Split a message into chunks within Telegram's character limit. 
    It doesn't perfectly align HTML tags (which is hard if splitting a huge <pre> fallback).
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    while len(text) > max_len:
        # Try finding a safe split point (double newline)
        split_idx = text.rfind('\n\n', 0, max_len)
        if split_idx == -1:
            split_idx = text.rfind('\n', 0, max_len)
        if split_idx == -1:
            split_idx = max_len

        if split_idx <= 0:
            split_idx = max_len
            
        chunks.append(text[:split_idx])
        text = text[split_idx:].lstrip()

    if text:
        chunks.append(text)
        
    return chunks
