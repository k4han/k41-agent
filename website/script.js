"use strict";

document.documentElement.classList.add("js-enabled");

const workflows = {
  build: {
    heading: "A fresh pair of eyes",
    prompt: "Help me find my way around this project.",
    response: "Let's turn this workspace into a clear starting point.",
    steps: [["Read workspace files", "files"], ["Map project entry points", "code"], ["Write a project overview", "write"]],
    result: "project-overview.md",
    detail: "Your next step, a little clearer.",
    icon: "file",
  },
  research: {
    heading: "A little clarity, on demand",
    prompt: "Summarize the notes in this workspace.",
    response: "I'll find the key themes and bring the useful pieces together.",
    steps: [["Find project notes", "files"], ["Read and compare sources", "read"], ["Save a concise summary", "write"]],
    result: "research-summary.md",
    detail: "The big picture, without the busywork.",
    icon: "search",
  },
  automate: {
    heading: "A routine, taken care of",
    prompt: "Set up a weekly project check-in.",
    response: "Let's make a little room in your week. Here's a recurring task.",
    steps: [["Review the project context", "files"], ["Prepare the check-in task", "task"], ["Schedule a weekly run", "schedule"]],
    result: "Weekly project check-in",
    detail: "A recurring job, ready for the next run.",
    icon: "clock",
  },
};

function connectTabs(tablist, onSelect) {
  const tabs = [...tablist.querySelectorAll('[role="tab"]')];
  function select(tab, focus = false) {
    for (const item of tabs) {
      const selected = item === tab;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
    }
    const panel = document.getElementById(tab.getAttribute("aria-controls"));
    panel.setAttribute("aria-labelledby", tab.id);
    onSelect(tab);
    if (focus) tab.focus();
  }
  for (const tab of tabs) {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (event) => {
      const current = tabs.indexOf(tab);
      let next;
      if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
      if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next !== undefined) {
        event.preventDefault();
        select(tabs[next], true);
      }
    });
  }
  return select;
}

connectTabs(document.querySelector(".demo-tabs"), (tab) => {
  const workflow = workflows[tab.dataset.demo];
  document.getElementById("demo-heading").textContent = workflow.heading;
  document.getElementById("demo-prompt").textContent = workflow.prompt;
  document.getElementById("demo-response").textContent = workflow.response;
  document.getElementById("demo-result-title").textContent = workflow.result;
  document.getElementById("demo-result-detail").textContent = workflow.detail;
  document.getElementById("demo-result-icon").setAttribute("href", `#icon-${workflow.icon}`);
  const steps = workflow.steps.map(([description, type]) => {
    const row = document.createElement("div");
    for (const [className, text] of [["step-check", "✓"], ["", description], ["step-type", type]]) {
      const span = document.createElement("span");
      span.className = className;
      span.textContent = text;
      row.append(span);
    }
    return row;
  });
  document.getElementById("demo-steps").replaceChildren(...steps);
});

const installPlatforms = {
  windows: {
    name: "Windows",
    shell: "PowerShell",
    command: "irm https://k4han.github.io/k41-agent/install.ps1 | iex",
    help: "Open PowerShell, paste the command, and let the installer take care of the rest.",
    installer: "./install.ps1",
  },
  macos: {
    name: "macOS",
    shell: "Terminal",
    command: "curl -fsSL https://k4han.github.io/k41-agent/install.sh | bash",
    help: "Open Terminal, paste the command, and let the installer take care of the rest.",
    installer: "./install.sh",
  },
  linux: {
    name: "Linux",
    shell: "Bash",
    command: "curl -fsSL https://k4han.github.io/k41-agent/install.sh | bash",
    help: "Open a Bash terminal and paste the command. The installer also sets up a systemd user service.",
    installer: "./install.sh",
  },
};

const commandElement = document.getElementById("install-command");
const copyButton = document.getElementById("copy-command");
const copyStatus = document.getElementById("copy-status");
let selectedPlatform = "windows";
let copyResetTimer;
function resetCopyButton() {
  clearTimeout(copyResetTimer);
  copyButton.querySelector("use").setAttribute("href", "#icon-copy");
  copyButton.querySelector("span").textContent = "Copy";
}
const selectPlatform = connectTabs(document.querySelector(".install-tabs"), (tab) => {
  selectedPlatform = tab.dataset.platform;
  const platform = installPlatforms[selectedPlatform];
  commandElement.textContent = platform.command;
  document.getElementById("install-shell").textContent = platform.shell;
  document.getElementById("install-help").textContent = platform.help;
  document.getElementById("installer-source").setAttribute("href", platform.installer);
  resetCopyButton();
  copyStatus.textContent = "";
});

const operatingSystem = navigator.userAgentData?.platform || navigator.platform || "";
if (/Mac|iPhone|iPad/i.test(operatingSystem)) selectPlatform(document.getElementById("install-macos"));
else if (/Linux/i.test(operatingSystem)) selectPlatform(document.getElementById("install-linux"));

copyButton.hidden = false;
copyButton.addEventListener("click", async () => {
  const platform = installPlatforms[selectedPlatform];
  resetCopyButton();
  copyButton.disabled = true;
  copyStatus.textContent = "";
  try {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
      await navigator.clipboard.writeText(platform.command);
    } catch {
      const input = document.createElement("textarea");
      input.value = platform.command;
      input.setAttribute("aria-label", "Installation command");
      input.style.cssText = "position:fixed;top:0;left:0;opacity:0;";
      document.body.append(input);
      try {
        input.select();
        if (!document.execCommand("copy")) throw new Error("Copy unavailable");
      } finally {
        input.remove();
      }
    }
    copyButton.querySelector("use").setAttribute("href", "#icon-check");
    copyButton.querySelector("span").textContent = "Copied";
    copyStatus.textContent = `${platform.name} installation command copied.`;
    copyResetTimer = setTimeout(resetCopyButton, 2200);
  } catch {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(commandElement);
    selection.removeAllRanges();
    selection.addRange(range);
    copyStatus.textContent = "Copy was unavailable. The command is selected so you can copy it manually.";
  } finally {
    copyButton.disabled = false;
    copyButton.focus({ preventScroll: true });
  }
});

const menuButton = document.querySelector(".menu-toggle");
const navigation = document.getElementById("navigation");
menuButton.hidden = false;
function setMenu(open) {
  navigation.classList.toggle("is-open", open);
  menuButton.setAttribute("aria-expanded", String(open));
  menuButton.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
  menuButton.querySelector("use").setAttribute("href", open ? "#icon-close" : "#icon-menu");
}
menuButton.addEventListener("click", () => setMenu(menuButton.getAttribute("aria-expanded") !== "true"));
navigation.addEventListener("click", (event) => {
  if (event.target.closest("a")) setMenu(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && menuButton.getAttribute("aria-expanded") === "true") {
    setMenu(false);
    menuButton.focus();
  }
});
document.addEventListener("click", (event) => {
  if (!navigation.contains(event.target) && !menuButton.contains(event.target)) setMenu(false);
});
navigation.addEventListener("focusout", () => {
  requestAnimationFrame(() => {
    if (!navigation.contains(document.activeElement) && document.activeElement !== menuButton) setMenu(false);
  });
});
window.matchMedia("(max-width: 600px)").addEventListener("change", () => setMenu(false));
