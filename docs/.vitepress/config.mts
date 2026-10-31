import { fileURLToPath } from "node:url";
import { defineConfig } from "vitepress";

const base = "/docs/";

export default defineConfig({
  title: "K41 Agent",
  titleTemplate: ":title | K41 Agent Docs",
  description: "A practical guide to your local AI workspace: installation, models, projects, integrations, and everyday commands.",
  lang: "en-US",
  base,
  srcDir: "guide",
  outDir: fileURLToPath(new URL("../../dist/docs", import.meta.url)),
  cacheDir: fileURLToPath(new URL("./cache", import.meta.url)),
  cleanUrls: false,
  appearance: true,
  head: [
    ["link", { rel: "icon", type: "image/svg+xml", href: `${base}favicon.svg` }],
    ["meta", { name: "theme-color", content: "#f7f8f2" }],
  ],
  sitemap: { hostname: "https://k41.bot.cd/docs/" },
  transformHead({ pageData }) {
    const page = pageData.relativePath.replace(/(?:^|\/)index\.md$/, "/").replace(/\.md$/, ".html");
    const url = `https://k41.bot.cd${base}${page === "/" ? "" : page}`;
    return [
      ["link", { rel: "canonical", href: url }],
      ["meta", { property: "og:type", content: "website" }],
      ["meta", { property: "og:title", content: `${pageData.title} | K41 Agent Docs` }],
      ["meta", { property: "og:description", content: pageData.description || "Practical documentation for your local AI workspace." }],
      ["meta", { property: "og:url", content: url }],
    ];
  },
  markdown: { lineNumbers: true, theme: { light: "github-light", dark: "github-dark" } },
  vite: {
    publicDir: fileURLToPath(new URL("../../website/assets", import.meta.url)),
  },
  themeConfig: {
    logo: { src: "/favicon.svg", alt: "" },
    siteTitle: "k41 agent",
    nav: [
      { text: "Guide", link: "/getting-started", activeMatch: "^/(?!cli|maintenance|troubleshooting)" },
      { text: "CLI reference", link: "/cli", activeMatch: "^/cli" },
      { text: "Website", link: "../", target: "_self", noIcon: true },
    ],
    sidebar: [
      {
        text: "Start here",
        items: [
          { text: "Welcome", link: "/" },
          { text: "Installation", link: "/installation" },
          { text: "Your first conversation", link: "/getting-started" },
        ],
      },
      {
        text: "Your workspace",
        items: [
          { text: "Projects and files", link: "/workspaces" },
          { text: "File previews", link: "/file-previews" },
          { text: "Model providers", link: "/providers" },
          { text: "Model pools", link: "/model-pools" },
          { text: "Agents", link: "/agents" },
          { text: "Skills", link: "/skills" },
        ],
      },
      {
        text: "Connect and automate",
        items: [
          { text: "Connections and channels", link: "/connections" },
          { text: "Browser automation", link: "/browser" },
          { text: "Tasks and schedules", link: "/automation" },
        ],
      },
      {
        text: "Reference",
        items: [
          { text: "Command line", link: "/cli" },
          { text: "Updates and maintenance", link: "/maintenance" },
          { text: "Troubleshooting", link: "/troubleshooting" },
        ],
      },
    ],
    outline: { level: [2, 3], label: "On this page" },
    socialLinks: [{ icon: "github", link: "https://github.com/k4han/k41-agent" }],
    search: { provider: "local" },
    editLink: { pattern: "https://github.com/k4han/k41-agent/edit/main/docs/guide/:path", text: "Improve this page on GitHub" },
    footer: { message: "Your machine. Your models. Your workspace.", copyright: "K41 Agent · MIT licensed" },
    docFooter: { prev: "Previous", next: "Up next" },
  },
});
