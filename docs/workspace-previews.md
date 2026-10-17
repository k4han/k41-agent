# Workspace file previews

Open a file from the workspace tree to view it in a tab. Preview support is shared
by local, Daytona, and Modal workspaces.

| Format | Viewer |
| --- | --- |
| PNG, JPEG, GIF, WebP, AVIF, BMP, ICO, SVG | Image preview |
| PDF | Browser PDF viewer |
| MP3, WAV, OGG, M4A, FLAC, AAC | Browser audio controls |
| MP4, WebM, OGV, MOV | Browser video controls |
| Markdown | Formatted document, with a Source toggle |
| HTML, HTM | Sandboxed document, with a Source toggle |
| CSV, TSV | Table, with a Source toggle |
| Other text and source files | Existing syntax-highlighted source viewer |
| Other binary formats, including Office documents and archives | Download |

Every file tab offers Download for the complete original file. Browser and codec
support determine whether a particular image, audio, video, or PDF can be shown.

Text previews retain the existing 300,000-byte limit. Table previews show at most
200 rows and 100 columns, including any header row. Native binary previews use a
separate authenticated endpoint and are not truncated by the text preview limit.
Local binary responses support byte ranges; remote backends read the original
bytes through the workspace file I/O capability.

HTML previews block scripts, forms, and external resources. SVG responses block
scripts and external resources. Neither viewer runs workspace code in the
dashboard's origin. The inline endpoint accepts only the supported image, PDF,
audio, and video extensions; HTML is rendered in a sandboxed frame from the
bounded text payload.

Markdown previews remove embedded styles and inline style attributes. Native
previews use the file response's modification time and size to refresh even when
the containing folder is collapsed, while unchanged files keep their preview URL.
