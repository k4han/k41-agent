# Website, documentation, and GitHub Pages

The public K41 Agent website combines a static landing page in `website/` and
VitePress documentation sourced from `docs/guide/`. Both are independent of the
application dashboard. Node.js and pnpm build the documentation; the published
site requires no Python runtime, API keys, or backend services.

## Local development

With Node.js 22 or newer and pnpm available, install dependencies and run from
the repository root:

```sh
pnpm install
pnpm site:dev
```

Open `http://127.0.0.1:4173/` for the landing page, or
`http://127.0.0.1:4173/docs/` for documentation. This server previews the
complete production build under the same paths used by GitHub Pages. Changes to
the landing page, documentation, theme, shared sources, or installers rebuild the
output; refresh the browser after the build completes. Set `PORT` to use a
different port.

For documentation-only editing with VitePress hot reload:

```sh
pnpm docs:dev
```

Open `http://127.0.0.1:5174/docs/`. This server only serves the docs;
use `pnpm site:dev` to preview navigation between the docs and landing page.

Validate and build without starting a server:

```sh
pnpm site:check
pnpm site:build
```

`site:check` validates the landing page's asset paths, source documentation
links, anchors, accessibility references, and JavaScript syntax. `site:build`
also builds all VitePress pages, checks their internal page links, and packages
the combined output in `dist/site/`, which is ignored by Git.

The documentation-only build is available separately:

```sh
pnpm docs:build
pnpm docs:preview
```

Its output is `dist/docs/`; preview it at
`http://127.0.0.1:4174/docs/`.

The combined build copies the documentation into `dist/site/docs/`, along with a
root `404.html` for GitHub Pages. It also copies the
original root `install.ps1`, `install.sh`, `uninstall.ps1`, and `CNAME` into the output.
This publishes the installer endpoints on the custom domain and keeps published
scripts in sync with the repository. Do not maintain separate installer copies in `website/`.

## Deployment

The repository publishes the combined website through GitHub Actions.
Configure Pages before the first deployment:

1. Open the repository's **Settings > Pages**.
2. Under **Build and deployment > Source**, select **GitHub Actions**.
3. Push the website changes to `main`, or run **Deploy website and docs** from
   the **Actions** tab after the workflow exists on `main`.
4. Set the custom domain to `k41.bot.cd` and enable **Enforce HTTPS**.
5. Wait for both the `build` and `deploy` jobs to succeed.

The production URL is:

```text
https://k41.bot.cd/
https://k41.bot.cd/docs/
```

`.github/workflows/pages.yml` installs dependencies with the frozen pnpm lockfile
and builds relevant pull requests without publishing them. Relevant pushes to
`main` and manual workflow runs build and publish only `dist/site/`. The workflow
needs no repository secrets. GitHub may
require a deployment approval if protection rules exist on the `github-pages`
environment.

The public installer endpoints are:

```text
https://k41.bot.cd/install.ps1
https://k41.bot.cd/install.sh
https://k41.bot.cd/uninstall.ps1
```

This deployment follows GitHub's
[custom Pages workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Editing

- `website/index.html`: page content, metadata, illustrative workspace, and FAQ.
- `website/styles.css`: colors, typography, layouts, and responsive behavior.
- `website/script.js`: workflow examples, operating-system tabs, clipboard, and navigation.
- `website/assets/favicon.svg`: the landing page brand icon.
- `website/assets/fonts/`: self-hosted fonts and their SIL Open Font Licenses.
- `docs/guide/`: public documentation pages in Markdown.
- `docs/.vitepress/config.mts`: routes, navigation, search, metadata, and build settings.
- `docs/.vitepress/theme/`: the documentation theme, using the landing page's fonts.
- `scripts/site.mjs`: source checks, combined builds, packaging, and local preview.

Only `docs/guide/` is published as documentation. Existing design notes and other
Markdown files outside it are not automatically published. The file-preview and
model-pool guides include the existing `docs/workspace-previews.md` and
`docs/model-pools.md` sources so their content stays synchronized.

Add new public pages under `docs/guide/` and register them in the VitePress
sidebar. Keep internal links relative and leave `ignoreDeadLinks` disabled.
The documentation uses explicit `.html` URLs, so direct navigation and page
reloads work on GitHub Pages without server rewrites. Local search runs in the
browser and requires no external search account.

Page copy is English, following the repository's source-language convention.
The workspace preview is illustrative; it does not connect to a live agent or
execute tasks. Installation commands are only copied, never executed by the site.
The FAQ and installer commands remain usable without JavaScript.
All page assets are self-hosted; no third-party font service or CDN is required.

Use relative asset paths for the landing page and `/docs/` for documentation. When forking
or changing the public URL, also update the repository links, canonical URL,
Open Graph URL, installer commands, the local server's base path, and VitePress's
`base` and sitemap settings. VitePress deployment details are described in its
[official guide](https://vitepress.dev/guide/deploy).
